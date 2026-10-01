import hashlib
import hmac
import json
import os
import secrets
import time
from email import policy
from email.parser import BytesParser
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socket import socket
from threading import Lock
from urllib.parse import quote, unquote, urlsplit

ROOT_DIR: Path = Path(__file__).resolve().parent
PICTURES_DIR: Path = ROOT_DIR / "pictures"
SELECTION_FILE: Path = ROOT_DIR / "featured-picture.json"
SHARED_PHOTOS_FILE: Path = ROOT_DIR / "shared-photos.json"
DEFAULT_FEATURED_PICTURE: str = "portrait-01.jpg"
PORT: int = int(os.environ.get("PORT", "8000"))
ADMIN_USERNAME: str = os.environ.get("FAV_ADMIN_USERNAME", "nrobins")
PASSWORD_SALT: bytes = bytes.fromhex("6fb9d30fa2c105e46228c399727711a8")
PASSWORD_HASH: bytes = bytes.fromhex(
    "e205b2999a180c575416163c8baebfcd2a846a2e0431a7efaac38bc9c62a0f2d"
)
PASSWORD_ITERATIONS: int = 600_000
SESSION_COOKIE: str = "fav_admin_session"
SESSION_LIFETIME_SECONDS: int = 43_200
MAX_JSON_BYTES: int = 16_384
MAX_UPLOAD_BYTES: int = 8 * 1024 * 1024
SUPPORTED_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".gif", ".jpeg", ".jpg", ".png", ".webp"}
)
ACTIVE_SESSIONS: dict[str, float] = {}
SESSION_LOCK: Lock = Lock()
SELECTION_LOCK: Lock = Lock()
SHARED_PHOTOS_LOCK: Lock = Lock()


def is_supported_filename(filename: str) -> bool:
    return (
        bool(filename)
        and Path(filename).name == filename
        and "/" not in filename
        and "\\" not in filename
        and Path(filename).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    )


def get_featured_picture() -> str:
    with SELECTION_LOCK:
        try:
            selection = json.loads(SELECTION_FILE.read_text(encoding="utf-8"))
            filename = selection.get("filename")
        except (AttributeError, OSError, TypeError, json.JSONDecodeError):
            filename = DEFAULT_FEATURED_PICTURE

        if (
            not isinstance(filename, str)
            or not is_supported_filename(filename)
            or not (PICTURES_DIR / filename).is_file()
        ):
            return DEFAULT_FEATURED_PICTURE
        return filename


def set_featured_picture(filename: str) -> None:
    temporary_file = ROOT_DIR / f".featured-{secrets.token_hex(8)}.tmp"
    with SELECTION_LOCK:
        try:
            temporary_file.write_text(
                json.dumps({"filename": filename}), encoding="utf-8"
            )
            os.replace(temporary_file, SELECTION_FILE)
        finally:
            if temporary_file.exists():
                temporary_file.unlink()


def is_valid_share_token(token: str) -> bool:
    return 24 <= len(token) <= 64 and all(
        character.isalnum() or character in "_-" for character in token
    )


def get_shared_filename(token: str) -> str | None:
    if not is_valid_share_token(token):
        return None
    with SHARED_PHOTOS_LOCK:
        try:
            shares = json.loads(SHARED_PHOTOS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(shares, dict):
            return None
        filename = shares.get(token)
        if (
            not isinstance(filename, str)
            or not is_supported_filename(filename)
            or not (PICTURES_DIR / filename).is_file()
        ):
            return None
        return filename


def create_shared_photo(filename: str) -> str:
    token = secrets.token_urlsafe(24)
    temporary_file = ROOT_DIR / f".shares-{secrets.token_hex(8)}.tmp"
    with SHARED_PHOTOS_LOCK:
        try:
            try:
                shares = json.loads(SHARED_PHOTOS_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                shares = {}
            if not isinstance(shares, dict):
                shares = {}
            shares[token] = filename
            temporary_file.write_text(json.dumps(shares), encoding="utf-8")
            os.replace(temporary_file, SHARED_PHOTOS_FILE)
        finally:
            if temporary_file.exists():
                temporary_file.unlink()
    return token


def has_valid_image_signature(extension: str, content: bytes) -> bool:
    extension = extension.lower()
    if extension in {".jpg", ".jpeg"}:
        return content.startswith(b"\xff\xd8\xff")
    if extension == ".png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if extension == ".webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False


class SiteHandler(SimpleHTTPRequestHandler):
    def __init__(
        self,
        request: socket,
        client_address: tuple[str, int],
        server: ThreadingHTTPServer,
    ) -> None:
        super().__init__(request, client_address, server, directory=str(ROOT_DIR))

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in {"/admin", "/admin/"}:
            self.path = "/admin.html"
            super().do_GET()
            return
        if path == "/admin.html" or path in {
            "/app.py",
            "/featured-picture.json",
            "/shared-photos.json",
        }:
            self.send_json(404, {"error": "Not found."})
            return
        if path == "/api/picture":
            self.send_featured_picture()
            return
        if path.startswith("/api/shared/"):
            self.send_shared_photo(path.removeprefix("/api/shared/"))
            return
        if path.startswith("/shared-photo/"):
            self.serve_shared_photo(path.removeprefix("/shared-photo/"))
            return
        if path.startswith("/share/"):
            token = path.removeprefix("/share/")
            if is_valid_share_token(token):
                self.path = "/share.html"
                super().do_GET()
            else:
                self.send_json(404, {"error": "This share link is not valid."})
            return
        if path == "/api/admin/session":
            self.send_json(200, {"authenticated": self.is_authenticated()})
            return
        if path == "/api/admin/pictures":
            if self.require_admin():
                self.send_picture_list()
            return
        if path.startswith("/pictures/"):
            self.serve_picture(path)
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/admin/login":
            self.login()
            return
        if path == "/api/admin/logout":
            if self.require_admin():
                self.logout()
            return
        if path == "/api/admin/select":
            if self.require_admin():
                self.select_picture()
            return
        if path == "/api/admin/upload":
            if self.require_admin():
                self.upload_picture()
            return
        if path == "/api/pass-it-on":
            self.create_share_link()
            return
        self.send_json(404, {"error": "Not found."})

    def send_json(
        self,
        status: int,
        payload: dict[str, object],
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> dict[str, object] | None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "Invalid request size."})
            return None

        if content_length <= 0 or content_length > MAX_JSON_BYTES:
            self.send_json(413, {"error": "Request is empty or too large."})
            return None

        try:
            payload = json.loads(self.rfile.read(content_length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "Invalid JSON request."})
            return None

        if not isinstance(payload, dict):
            self.send_json(400, {"error": "Expected a JSON object."})
            return None
        return payload

    def is_authenticated(self) -> bool:
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            cookie = cookies.get(SESSION_COOKIE)
        except Exception:
            return False
        if cookie is None:
            return False

        now = time.time()
        with SESSION_LOCK:
            expires_at = ACTIVE_SESSIONS.get(cookie.value)
            if expires_at is None:
                return False
            if expires_at <= now:
                ACTIVE_SESSIONS.pop(cookie.value, None)
                return False
        return True

    def require_admin(self) -> bool:
        if self.is_authenticated():
            return True
        self.send_json(401, {"error": "Please sign in to manage pictures."})
        return False

    def login(self) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        username = payload.get("username")
        password = payload.get("password")
        if (
            not isinstance(username, str)
            or not isinstance(password, str)
            or len(username) > 128
            or len(password) > 256
        ):
            self.send_json(400, {"error": "Enter a valid username and password."})
            return

        try:
            password_bytes = password.encode("utf-8")
        except UnicodeEncodeError:
            self.send_json(400, {"error": "Enter a valid username and password."})
            return

        submitted_hash = hashlib.pbkdf2_hmac(
            "sha256", password_bytes, PASSWORD_SALT, PASSWORD_ITERATIONS
        )
        username_matches = hmac.compare_digest(username, ADMIN_USERNAME)
        password_matches = hmac.compare_digest(submitted_hash, PASSWORD_HASH)
        if not (username_matches and password_matches):
            self.send_json(401, {"error": "That username or password did not match."})
            return

        session_id = secrets.token_urlsafe(32)
        with SESSION_LOCK:
            ACTIVE_SESSIONS[session_id] = time.time() + SESSION_LIFETIME_SECONDS
        cookie = (
            f"{SESSION_COOKIE}={session_id}; HttpOnly; SameSite=Strict; "
            f"Path=/; Max-Age={SESSION_LIFETIME_SECONDS}"
        )
        self.send_json(
            200,
            {"authenticated": True},
            (("Set-Cookie", cookie),),
        )

    def logout(self) -> None:
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            cookie = cookies.get(SESSION_COOKIE)
        except Exception:
            cookie = None
        if cookie is not None:
            with SESSION_LOCK:
                ACTIVE_SESSIONS.pop(cookie.value, None)
        self.send_json(
            200,
            {"authenticated": False},
            (("Set-Cookie", f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"),),
        )

    def send_featured_picture(self) -> None:
        filename = get_featured_picture()
        if not is_supported_filename(filename) or not (PICTURES_DIR / filename).is_file():
            self.send_json(404, {"error": "The selected picture was not found."})
            return
        self.send_json(
            200,
            {"url": f"/pictures/{quote(filename, safe='')}"},
        )

    def send_shared_photo(self, token: str) -> None:
        if get_shared_filename(token) is None:
            self.send_json(404, {"error": "This share link is not valid."})
            return
        self.send_json(200, {"url": f"/shared-photo/{quote(token, safe='')}"})

    def serve_shared_photo(self, token: str) -> None:
        filename = get_shared_filename(token)
        if filename is None:
            self.send_json(404, {"error": "This share link is not valid."})
            return
        self.path = f"/pictures/{quote(filename, safe='')}"
        super().do_GET()

    def send_picture_list(self) -> None:
        pictures = sorted(
            (
                path.name
                for path in PICTURES_DIR.iterdir()
                if path.is_file()
                and is_supported_filename(path.name)
            ),
            key=str.casefold,
        )
        self.send_json(
            200,
            {"pictures": pictures, "selected": get_featured_picture()},
        )

    def serve_picture(self, path: str) -> None:
        filename = unquote(path[len("/pictures/"):])
        if not is_supported_filename(filename):
            self.send_json(404, {"error": "Picture not found."})
            return
        picture_path = PICTURES_DIR / filename
        if not picture_path.is_file():
            self.send_json(404, {"error": "Picture not found."})
            return
        if filename != get_featured_picture() and not self.is_authenticated():
            self.send_json(404, {"error": "Picture not found."})
            return
        super().do_GET()

    def select_picture(self) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        filename = payload.get("filename")
        if (
            not isinstance(filename, str)
            or not is_supported_filename(filename)
            or not (PICTURES_DIR / filename).is_file()
        ):
            self.send_json(400, {"error": "Choose a picture from the collection."})
            return
        set_featured_picture(filename)
        self.send_json(200, {"selected": filename})

    def upload_picture(self) -> None:
        filename = self.save_uploaded_image("upload")
        if filename is None:
            return
        self.send_json(201, {"filename": filename})

    def create_share_link(self) -> None:
        filename = self.save_uploaded_image("pass")
        if filename is None:
            return
        token = create_shared_photo(filename)
        self.send_json(201, {"url": f"/share/{quote(token, safe='')}"})

    def save_uploaded_image(self, prefix: str) -> str | None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json(400, {"error": "Invalid upload size."})
            return None
        if content_length <= 0 or content_length > MAX_UPLOAD_BYTES + 64 * 1024:
            self.send_json(413, {"error": "Choose a picture smaller than 8 MB."})
            return None

        content_type = self.headers.get("Content-Type", "")
        boundary = self.headers.get_boundary()
        if (
            self.headers.get_content_type() != "multipart/form-data"
            or not boundary
            or "\r" in content_type
            or "\n" in content_type
        ):
            self.send_json(400, {"error": "Choose a picture file to upload."})
            return None

        message_bytes = (
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii")
            + self.rfile.read(content_length)
        )
        message = BytesParser(policy=policy.default).parsebytes(message_bytes)
        if not message.is_multipart():
            self.send_json(400, {"error": "Choose a picture file to upload."})
            return None

        upload_name: str | None = None
        upload_content: bytes | None = None
        for part in message.iter_parts():
            if part.get_param("name", header="content-disposition") != "picture":
                continue
            upload_name = part.get_filename()
            upload_content = part.get_payload(decode=True)
            break

        if not upload_name or not isinstance(upload_content, bytes):
            self.send_json(400, {"error": "Choose a picture file to upload."})
            return None
        if len(upload_content) > MAX_UPLOAD_BYTES:
            self.send_json(413, {"error": "Choose a picture smaller than 8 MB."})
            return None
        extension = Path(upload_name).suffix.lower()
        if extension not in SUPPORTED_IMAGE_EXTENSIONS or not has_valid_image_signature(
            extension, upload_content
        ):
            self.send_json(400, {"error": "Use a valid JPG, PNG, WEBP, or GIF image."})
            return None

        filename = f"{prefix}-{secrets.token_hex(12)}{extension}"
        (PICTURES_DIR / filename).write_bytes(upload_content)
        return filename


def main() -> None:
    with ThreadingHTTPServer(("127.0.0.1", PORT), SiteHandler) as server:
        print(f"The Fav Club is running at http://127.0.0.1:{PORT}")
        print(f"Picture desk: http://127.0.0.1:{PORT}/admin")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")


if __name__ == "__main__":
    main()
