from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

import boto3
from botocore.exceptions import ClientError

BUCKET_NAME = os.environ["PICTURES_BUCKET"]
TABLE_NAME = os.environ["METADATA_TABLE"]
USER_POOL_CLIENT_ID = os.environ["USER_POOL_CLIENT_ID"]
DEFAULT_PICTURE_KEY = os.environ.get(
    "DEFAULT_PICTURE_KEY", "library/portrait-01.jpg"
)
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(TABLE_NAME)
cognito = boto3.client("cognito-idp")


def response(status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(payload),
    }


def json_body(event: dict[str, Any]) -> dict[str, Any] | None:
    try:
        body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body, validate=True)
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def event_headers(event: dict[str, Any]) -> dict[str, str]:
    return {key.lower(): value for key, value in event.get("headers", {}).items()}


def get_access_token(event: dict[str, Any]) -> str | None:
    authorization = event_headers(event).get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if separator and scheme.lower() == "bearer" and token:
        return token
    return None


def is_admin(event: dict[str, Any]) -> bool:
    token = get_access_token(event)
    if not token:
        return False
    try:
        cognito.get_user(AccessToken=token)
    except ClientError:
        return False
    return True


def require_admin(event: dict[str, Any]) -> dict[str, Any] | None:
    if is_admin(event):
        return None
    return response(401, {"error": "Please sign in to manage pictures."})


def image_extension(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).suffix.lower()


def has_valid_image_signature(extension: str, content: bytes) -> bool:
    if extension in {".jpg", ".jpeg"}:
        return content.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if extension == ".webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False


def start_picture_upload(event: dict[str, Any], kind: str) -> dict[str, Any]:
    payload = json_body(event)
    if payload is None:
        return response(400, {"error": "Choose a picture to upload."})

    filename = payload.get("filename")
    content_type = payload.get("contentType")
    size = payload.get("size")
    if not isinstance(filename, str) or len(filename) > 255:
        return response(400, {"error": "Choose a supported image."})
    extension = image_extension(filename)
    expected_type = mimetypes.guess_type(f"picture{extension}")[0]
    if (
        extension not in SUPPORTED_EXTENSIONS
        or content_type != expected_type
        or not isinstance(size, int)
        or isinstance(size, bool)
        or not 0 < size <= MAX_UPLOAD_BYTES
    ):
        return response(400, {"error": "Use a valid JPG, PNG, WEBP, or GIF under 8 MB."})

    upload_id = secrets.token_urlsafe(24)
    key = f"pending/{uuid.uuid4().hex}{extension}"
    table.put_item(
        Item={
            "pk": f"UPLOAD#{upload_id}",
            "sk": "PENDING",
            "objectKey": key,
            "kind": kind,
            "expiresAt": int(datetime.now(timezone.utc).timestamp()) + 3600,
        }
    )

    post = s3.generate_presigned_post(
        Bucket=BUCKET_NAME,
        Key=key,
        Fields={
            "Content-Type": content_type,
            "x-amz-meta-upload-id": upload_id,
        },
        Conditions=[
            {"Content-Type": content_type},
            {"x-amz-meta-upload-id": upload_id},
            ["content-length-range", 1, MAX_UPLOAD_BYTES],
        ],
        ExpiresIn=900,
    )
    return response(
        200,
        {
            "uploadId": upload_id,
            "filename": PurePosixPath(filename).name,
            "url": post["url"],
            "fields": post["fields"],
        },
    )


def finish_picture_upload(event: dict[str, Any], expected_kind: str) -> dict[str, Any]:
    payload = json_body(event)
    upload_id = payload.get("uploadId") if payload else None
    if not isinstance(upload_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", upload_id):
        return response(400, {"error": "This upload is no longer available."})

    result = table.get_item(Key={"pk": f"UPLOAD#{upload_id}", "sk": "PENDING"})
    pending = result.get("Item")
    if not pending or pending.get("kind") != expected_kind:
        return response(404, {"error": "This upload is no longer available."})

    key = pending.get("objectKey")
    if not isinstance(key, str):
        return response(400, {"error": "This upload is invalid."})
    try:
        metadata = s3.head_object(Bucket=BUCKET_NAME, Key=key)
        if (
            metadata.get("Metadata", {}).get("upload-id") != upload_id
            or metadata.get("ContentLength", 0) > MAX_UPLOAD_BYTES
        ):
            return response(400, {"error": "This upload is invalid."})
        image = s3.get_object(Bucket=BUCKET_NAME, Key=key, Range="bytes=0-11")
        signature = image["Body"].read(12)
        image["Body"].close()
    except ClientError:
        return response(400, {"error": "Upload the selected picture before continuing."})

    extension = image_extension(key)
    if not has_valid_image_signature(extension, signature):
        s3.delete_object(Bucket=BUCKET_NAME, Key=key)
        table.delete_item(Key={"pk": f"UPLOAD#{upload_id}", "sk": "PENDING"})
        return response(400, {"error": "The uploaded file is not a valid supported image."})

    destination_prefix = "shares" if expected_kind == "share" else "uploads"
    destination_key = f"{destination_prefix}/{uuid.uuid4().hex}{extension}"
    s3.copy_object(
        Bucket=BUCKET_NAME,
        Key=destination_key,
        CopySource={"Bucket": BUCKET_NAME, "Key": key},
        ContentType=metadata.get("ContentType", "application/octet-stream"),
        MetadataDirective="REPLACE",
        ServerSideEncryption="AES256",
    )
    s3.delete_object(Bucket=BUCKET_NAME, Key=key)
    table.delete_item(Key={"pk": f"UPLOAD#{upload_id}", "sk": "PENDING"})
    if expected_kind == "share":
        token = secrets.token_urlsafe(24)
        table.put_item(
            Item={
                "pk": f"SHARE#{token}",
                "sk": "PHOTO",
                "objectKey": destination_key,
                "createdAt": datetime.now(timezone.utc).isoformat(),
            }
        )
        return response(201, {"url": f"/share.html?token={token}"})
    return response(
        201,
        {"key": destination_key, "filename": PurePosixPath(destination_key).name},
    )


def get_featured_key() -> str:
    result = table.get_item(Key={"pk": "SITE", "sk": "FEATURED"})
    item = result.get("Item", {})
    return item.get("objectKey", DEFAULT_PICTURE_KEY)


def signed_picture_url(key: str) -> str:
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET_NAME, "Key": key},
        ExpiresIn=900,
    )


def list_library_pictures() -> list[dict[str, str]]:
    paginator = s3.get_paginator("list_objects_v2")
    objects: list[dict[str, str]] = []
    for prefix in ("library/", "uploads/"):
        for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=prefix):
            for item in page.get("Contents", []):
                key = item["Key"]
                objects.append(
                    {
                        "key": key,
                        "filename": PurePosixPath(key).name,
                        "url": signed_picture_url(key),
                    }
                )
    return sorted(objects, key=lambda item: item["filename"].casefold())


def login(event: dict[str, Any]) -> dict[str, Any]:
    payload = json_body(event)
    if payload is None:
        return response(400, {"error": "Enter a valid username and password."})
    username = payload.get("username")
    password = payload.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        return response(400, {"error": "Enter a valid username and password."})

    try:
        result = cognito.initiate_auth(
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
            ClientId=USER_POOL_CLIENT_ID,
        )
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code in {
            "NotAuthorizedException",
            "UserNotFoundException",
            "UserNotConfirmedException",
            "PasswordResetRequiredException",
        }:
            return response(401, {"error": "That username or password did not match."})
        return response(503, {"error": "Sign-in is temporarily unavailable."})

    auth_result = result.get("AuthenticationResult", {})
    access_token = auth_result.get("AccessToken")
    if not access_token:
        return response(403, {"error": "Complete the required password reset before signing in."})
    return response(200, {"authenticated": True, "accessToken": access_token})


def admin_route(event: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    if path == "/api/admin/login" and method == "POST":
        return login(event)
    if path == "/api/admin/session" and method == "GET":
        return response(200, {"authenticated": is_admin(event)})

    auth_error = require_admin(event)
    if auth_error is not None:
        return auth_error

    if path == "/api/admin/logout" and method == "POST":
        token = get_access_token(event)
        if token:
            try:
                cognito.global_sign_out(AccessToken=token)
            except ClientError:
                pass
        return response(200, {"authenticated": False})

    if path == "/api/admin/pictures" and method == "GET":
        pictures = list_library_pictures()
        selected = get_featured_key()
        return response(200, {"pictures": pictures, "selected": selected})

    if path == "/api/admin/select" and method == "POST":
        payload = json_body(event)
        key = payload.get("key") if payload else None
        if not isinstance(key, str) or not key.startswith(("library/", "uploads/")):
            return response(400, {"error": "Choose a picture from the collection."})
        try:
            s3.head_object(Bucket=BUCKET_NAME, Key=key)
        except ClientError:
            return response(404, {"error": "That picture is no longer available."})
        table.put_item(
            Item={"pk": "SITE", "sk": "FEATURED", "objectKey": key}
        )
        return response(200, {"selected": key})

    if path == "/api/admin/upload-url" and method == "POST":
        return start_picture_upload(event, "admin")

    if path == "/api/admin/upload-complete" and method == "POST":
        return finish_picture_upload(event, "admin")

    return response(404, {"error": "Not found."})


def shared_photo_route(token: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32}", token):
        return response(404, {"error": "This reveal link is not available."})
    result = table.get_item(Key={"pk": f"SHARE#{token}", "sk": "PHOTO"})
    item = result.get("Item")
    if not item or not str(item.get("objectKey", "")).startswith("shares/"):
        return response(404, {"error": "This reveal link is not available."})
    return response(200, {"url": signed_picture_url(item["objectKey"])})


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")

    if method == "OPTIONS":
        return {"statusCode": 204, "headers": {}, "body": ""}

    if path == "/api/picture" and method == "GET":
        try:
            return response(200, {"url": signed_picture_url(get_featured_key())})
        except ClientError:
            return response(404, {"error": "The selected picture was not found."})

    if path.startswith("/api/shared/") and method == "GET":
        return shared_photo_route(path.removeprefix("/api/shared/"))

    if path.startswith("/api/admin/"):
        return admin_route(event, method, path)

    if path == "/api/pass-it-on/start" and method == "POST":
        return start_picture_upload(event, "share")

    if path == "/api/pass-it-on/complete" and method == "POST":
        return finish_picture_upload(event, "share")

    return response(404, {"error": "Not found."})
