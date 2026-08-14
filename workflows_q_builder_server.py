"""Serveur local de Workflows Q-builder.

Successeur de ltx_builder_server.py : meme port, memes endpoints d'upload, mais
sert workflows_q_builder.html a la racine. L'ancienne interface LTX reste
accessible sur /ltx_prompt_queue_builder_v2.html tant qu'elle est dans le
dossier — rien n'est casse pendant la bascule.

Ajout par rapport a l'ancien serveur : /api/workflow-read, que l'interface
appelait deja pour detecter un workflow au format UI charge par erreur (il
renvoyait 404 silencieusement, donc la detection ne marchait jamais).
"""

import argparse
import json
import mimetypes
import socket
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "ltx_builder_uploads"
WORKFLOW_UPLOAD_DIR = ROOT / "ltx_builder_workflows"
APP_HTML = "workflows_q_builder.html"
LEGACY_HTML = "ltx_prompt_queue_builder_v2.html"
HOST = "127.0.0.1"
PORT = 8765


def guess_type(path):
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def port_taken(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((HOST, port)) == 0


class Handler(BaseHTTPRequestHandler):
    server_version = "WorkflowsQBuilder/1.0"

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
        if parsed.path in {"/", "/" + APP_HTML}:
            self.send_file(ROOT / APP_HTML)
            return
        if parsed.path == "/" + LEGACY_HTML:
            self.send_file(ROOT / LEGACY_HTML)
            return
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if parsed.path.startswith("/uploads/"):
            name = unquote(parsed.path.removeprefix("/uploads/"))
            self.send_file(UPLOAD_DIR / Path(name).name)
            return
        if parsed.path == "/api/env":
            # L'interface s'en sert pour remplir le champ Dossier : sans ca, le
            # chemin reste celui de la machine ou l'app a ete ecrite et la
            # commande PowerShell echoue sur un `cd` vers un dossier inexistant.
            # sys.executable plutot que "python" : sur une machine ou Python
            # n'est pas dans le PATH (ou ou l'app tourne avec le Python
            # embarque de ComfyUI), la commande generee doit designer le meme
            # interpreteur que celui qui fait tourner ce serveur.
            self.send_json({"root": str(ROOT), "app": APP_HTML, "python": sys.executable})
            return
        if parsed.path == "/api/workflow-read":
            self.handle_workflow_read(parse_qs(parsed.query))
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
        # On parcourt TOUTES les parties avant de conclure : l'ancien serveur
        # sortait de la boucle des qu'il tenait le fichier, donc filename_hint
        # (envoye apres le fichier par l'interface) n'etait jamais lu.
        for part in parts:
            if b"Content-Disposition:" not in part:
                continue
            header_blob, _, body = part.partition(b"\r\n\r\n")
            if not body:
                continue
            body = body.rstrip(b"\r\n-")
            # Le nom de fichier se lit sur la seule ligne Content-Disposition :
            # decouper tout le bloc d'en-tetes sur ';' collait le Content-Type
            # suivant dans la valeur, et l'extension finissait a la poubelle.
            disposition = ""
            for line in header_blob.decode("utf-8", errors="ignore").splitlines():
                if line.lower().startswith("content-disposition:"):
                    disposition = line
                    break
            params = {}
            for token in disposition.split(";")[1:]:
                key, _, value = token.strip().partition("=")
                params[key.strip().lower()] = value.strip().strip('"')

            if params.get("name") == "filename_hint":
                filename_hint = body.decode("utf-8", errors="ignore").strip()
                continue
            if params.get("name") != expected_field:
                continue
            filename = params.get("filename")
            data = body

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
        if not isinstance(workflow, dict):
            self.send_json({"error": "Workflow JSON is invalid"}, 400)
            return
        # Un template H3 Director en format UI est legitime ici (il se depose
        # dans ComfyUI, il ne part pas sur /prompt), donc on accepte les deux
        # formats et on laisse queue_h3_multishot.py trancher selon le mode.
        is_ui = isinstance(workflow.get("nodes"), list) and isinstance(workflow.get("links"), list)
        is_api = any(
            isinstance(node, dict) and "class_type" in node
            for node in workflow.values()
        )
        if not (is_ui or is_api):
            self.send_json({"error": "This does not look like a ComfyUI workflow (API export or UI graph)."}, 400)
            return

        WORKFLOW_UPLOAD_DIR.mkdir(exist_ok=True)
        target = WORKFLOW_UPLOAD_DIR / f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{safe_name}"
        target.write_bytes(data)
        self.send_json({
            "filename": target.name,
            "path": str(target),
            "format": "ui" if is_ui else "api",
        })

    def handle_workflow_read(self, query):
        name = (query.get("name") or [""])[0]
        if not name:
            self.send_json({"error": "Missing name"}, 400)
            return
        target = WORKFLOW_UPLOAD_DIR / Path(unquote(name)).name
        if not target.exists():
            target = ROOT / Path(unquote(name)).name
        if not target.exists() or target.suffix.lower() != ".json":
            self.send_json({"error": "Workflow not found"}, 404)
            return
        try:
            workflow = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            self.send_json({"error": "Workflow JSON is invalid"}, 400)
            return
        is_ui = isinstance(workflow.get("nodes"), list) and isinstance(workflow.get("links"), list)
        # On ne renvoie pas le graphe entier : l'interface ne veut savoir que si
        # c'est un export UI ou API, et ces fichiers pesent jusqu'a 100 Ko.
        self.send_json({
            "name": target.name,
            "format": "ui" if is_ui else "api",
            "nodes": [] if is_ui else None,
            "links": [] if is_ui else None,
        })


def main():
    # --port : sous Windows, deux serveurs peuvent tenir le meme port sans
    # erreur (allow_reuse_address), et c'est le plus ancien qui repond. Sans
    # option de port, une ancienne instance de ltx_builder_server.py restee
    # ouverte sert silencieusement l'ancienne interface a la place de celle-ci.
    parser = argparse.ArgumentParser(description="Serveur local de Workflows Q-builder.")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    # Windows laisse deux serveurs se poser sur le meme port sans lever
    # d'erreur (allow_reuse_address), et c'est le premier arrive qui repond :
    # un ancien ltx_builder_server.py oublie servirait l'ancienne interface
    # pendant que celle-ci tourne pour rien. On refuse plutot que de mentir.
    if port_taken(args.port):
        print(f"Le port {args.port} est deja pris par un autre serveur (probablement "
              f"ltx_builder_server.py laisse ouvert).")
        print("Ferme sa fenetre, puis relance, ou lance celui-ci sur un autre port:")
        print(f"    python .\\{Path(__file__).name} --port {args.port + 1}")
        raise SystemExit(1)

    UPLOAD_DIR.mkdir(exist_ok=True)
    WORKFLOW_UPLOAD_DIR.mkdir(exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, args.port), Handler)
    url = f"http://{HOST}:{args.port}/"
    print(f"Workflows Q-builder running at {url}")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    httpd.serve_forever()


if __name__ == "__main__":
    main()
