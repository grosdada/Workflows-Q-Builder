import json
import mimetypes
import shutil
import threading
import time
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "ltx_builder_uploads"
WORKFLOW_UPLOAD_DIR = ROOT / "ltx_builder_workflows"
HOST = "127.0.0.1"
PORT = 8765


def guess_type(path):
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    server_version = "LTXBuilder/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", guess_type(path))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/ltx_prompt_queue_builder_v2.html"}:
            self.send_file(ROOT / "ltx_prompt_queue_builder_v2.html")
            return
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if parsed.path.startswith("/uploads/"):
            name = unquote(parsed.path.removeprefix("/uploads/"))
            self.send_file(UPLOAD_DIR / name)
            return
        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/upload":
            self.handle_upload()
            return
        if parsed.path == "/api/workflow-upload":
            self.handle_workflow_upload()
            return
        self.send_error(404)

    def read_multipart_file(self, expected_field):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type or "boundary=" not in content_type:
            raise ValueError("Expected multipart/form-data")

        boundary = content_type.split("boundary=", 1)[1].strip().strip('"')
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        marker = ("--" + boundary).encode("utf-8")
        parts = raw.split(marker)

        filename = None
        filename_hint = None
        data = None
        for part in parts:
            if b"Content-Disposition:" not in part:
                continue
            header_blob, _, body = part.partition(b"\r\n\r\n")
            if not body:
                continue
            body = body.rstrip(b"\r\n-")
            headers = header_blob.decode("utf-8", errors="ignore")
            if b'name="filename_hint"' in part:
                filename_hint = body.decode("utf-8", errors="ignore").strip()
                continue
            expected = f'name="{expected_field}"'.encode("utf-8")
            if expected not in part:
                continue
            for token in headers.split(";"):
                token = token.strip()
                if token.startswith("filename="):
                    filename = token.split("=", 1)[1].strip().strip('"')
            data = body
            break

        if not filename or data is None:
            raise ValueError(f"No {expected_field} file found")
        return filename_hint or filename, data

    def handle_upload(self):
        try:
            filename, data = self.read_multipart_file("image")
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
            return

        UPLOAD_DIR.mkdir(exist_ok=True)
        safe_name = Path(filename).name
        target = UPLOAD_DIR / f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{safe_name}"
        target.write_bytes(data)
        self.send_json({
            "filename": target.name,
            "path": str(target),
            "url": f"/uploads/{target.name}",
        })

    def handle_workflow_upload(self):
        try:
            filename, data = self.read_multipart_file("workflow")
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
            return

        safe_name = Path(filename).name
        if not safe_name.lower().endswith(".json"):
            self.send_json({"error": "Workflow must be a .json file"}, 400)
            return
        try:
            workflow = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json({"error": "Workflow JSON is invalid"}, 400)
            return
        if not isinstance(workflow, dict) or not any(
            isinstance(node, dict) and "class_type" in node
            for node in workflow.values()
        ):
            self.send_json({"error": "This does not look like a ComfyUI API workflow. Use Export (API)."}, 400)
            return

        WORKFLOW_UPLOAD_DIR.mkdir(exist_ok=True)
        target = WORKFLOW_UPLOAD_DIR / f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{safe_name}"
        target.write_bytes(data)
        self.send_json({
            "filename": target.name,
            "path": str(target),
        })


def main():
    UPLOAD_DIR.mkdir(exist_ok=True)
    WORKFLOW_UPLOAD_DIR.mkdir(exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"LTX Prompt Queue Builder running at {url}")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    httpd.serve_forever()


if __name__ == "__main__":
    main()

