import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socket import socket
from urllib.parse import quote, urlsplit

ROOT_DIR: Path = Path(__file__).resolve().parent
PICTURES_DIR: Path = ROOT_DIR / "pictures"
FEATURED_PICTURE: str = "portrait-01.jpg"
PORT: int = int(os.environ.get("PORT", "8000"))
SUPPORTED_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".gif", ".jpeg", ".jpg", ".png", ".webp"}
)


class SiteHandler(SimpleHTTPRequestHandler):
    def __init__(
        self,
        request: socket,
        client_address: tuple[str, int],
        server: ThreadingHTTPServer,
    ) -> None:
        super().__init__(request, client_address, server, directory=str(ROOT_DIR))

    def do_GET(self) -> None:
        if urlsplit(self.path).path == "/api/picture":
            self.send_featured_picture()
            return

        super().do_GET()

    def send_featured_picture(self) -> None:
        filename = Path(FEATURED_PICTURE)
        if (
            filename.name != FEATURED_PICTURE
            or filename.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS
        ):
            self.send_error(500, "FEATURED_PICTURE must be a supported image filename.")
            return

        picture_path = PICTURES_DIR / filename
        if not picture_path.is_file():
            self.send_error(404, "The configured picture was not found in pictures/.")
            return

        payload = json.dumps(
            {"url": f"/pictures/{quote(FEATURED_PICTURE, safe='')}"}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    with ThreadingHTTPServer(("127.0.0.1", PORT), SiteHandler) as server:
        print(f"The Fav Club is running at http://127.0.0.1:{PORT}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")


if __name__ == "__main__":
    main()
