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
import shutil
import socket
import ssl
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
import zipfile
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


def local_settings():
    """Reglages propres a cette machine, jamais versionnes.

    local_settings.json est optionnel ; absent, l'app garde ses valeurs par
    defaut. Il evite d'ecrire un chemin de NAS ou un dossier ComfyUI dans un
    depot public.
    """
    path = ROOT / "local_settings.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        print("local_settings.json illisible, ignore")
        return {}
    allowed = ("workflow_browse_path", "comfy_server", "comfy_input")
    return {key: data[key] for key in allowed if isinstance(data.get(key), str) and data[key]}


# Depot public de reference. Surchargeable par "update_repo" dans
# local_settings.json, pour pointer un fork sans toucher au code.
DEFAULT_REPO = "grosdada/Workflows-Q-Builder"
DEFAULT_BRANCH = "main"

# Jamais ecrases par une mise a jour : ce sont les fichiers de cette machine.
UPDATE_SKIP = {
    "local_settings.json", "comfy_input.txt", "python_path.txt",
    "ltx_custom_queue.json", "h3_custom_queue.json", "h3_t2v_queue.json",
}
UPDATE_SKIP_DIRS = {
    "ltx_builder_uploads", "ltx_builder_workflows", "h3_workflows",
    "__pycache__", ".git", "_backup_previous",
}


def update_repo():
    return local_settings().get("update_repo") or DEFAULT_REPO


def read_version():
    path = ROOT / "VERSION.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def explain_network_error(exc):
    """Message lisible pour les pannes reseau qui reviennent.

    « CERTIFICATE_VERIFY_FAILED » ne dit rien a qui l'attrape : sans un mot
    d'explication, on cherche du cote du depot ou du pare-feu alors que le
    probleme est le magasin de racines de Windows. Voir ssl_context().
    """
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return (f"{text} — le magasin de certificats de Windows n'a pas la racine "
                "qui signe github.com. Ouvrir https://github.com dans un navigateur "
                "une fois (Windows telecharge alors la racine manquante), puis "
                "relancer le serveur et reessayer.")
    return text


def ssl_context():
    """Contexte TLS pour joindre GitHub, avec un magasin de secours.

    Sous Windows, Python lit le magasin de certificats de la machine tel qu'il
    est, alors que les navigateurs (et PowerShell) font telecharger a Windows la
    racine manquante au moment ou ils en ont besoin. Sur un poste qui n'a encore
    jamais visite de site signe par cette racine, le magasin ne la contient pas
    et Python echoue en CERTIFICATE_VERIFY_FAILED / unable to get local issuer
    certificate — pendant que le navigateur ouvre github.com sans broncher.

    Vu en vrai : github.com sert un certificat ECDSA signe par « Sectigo Public
    Server Authentication CA DV E36 », dont la racine « USERTrust ECC
    Certification Authority » etait absente du magasin de la machine.

    On prefere donc le paquet de racines de certifi quand il est installe (c'est
    le cas du Python embarque de ComfyUI, qui l'a via requests). Sinon on repart
    sur le magasin systeme, qui suffit des que Windows a fait sa mise a jour.
    """
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def github_json(url):
    request = urllib.request.Request(url, headers={
        "User-Agent": "WorkflowsQBuilder",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(request, timeout=20, context=ssl_context()) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_commit():
    repo = update_repo()
    data = github_json(f"https://api.github.com/repos/{repo}/commits/{DEFAULT_BRANCH}")
    commit = data.get("commit", {})
    return {
        "sha": (data.get("sha") or "")[:7],
        "date": (commit.get("committer") or {}).get("date", ""),
        "message": (commit.get("message") or "").splitlines()[0] if commit.get("message") else "",
    }


def force_crlf(data):
    """Fins de ligne Windows pour les .bat.

    L'archive zip de GitHub ignore l'attribut `eol=crlf` du .gitattributes
    (verifie sur le depot) : un lanceur installe par mise a jour arriverait en
    LF, ou `goto` peut deraper. On corrige donc a l'ecriture, sans dependre du
    comportement de git ni de GitHub.
    """
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def same_content(local, remote):
    """Compare en ignorant les fins de ligne.

    Le depot stocke du LF, la copie de travail sous Windows a souvent du CRLF :
    sans cette normalisation, chaque mise a jour reecrirait tous les fichiers
    texte pour rien, et signalerait un redemarrage a chaque fois.
    """
    if local == remote:
        return True
    if b"\x00" in local or b"\x00" in remote:
        return False
    return local.replace(b"\r\n", b"\n") == remote.replace(b"\r\n", b"\n")


def install_update():
    """Telecharge l'archive de la branche et remplace les fichiers de l'app.

    On ne supprime jamais rien : un fichier disparu du depot reste en place.
    Le remplacement est precede d'une sauvegarde dans _backup_previous, pour
    pouvoir revenir en arriere sans reseau si une version casse quelque chose.
    """
    repo = update_repo()
    url = f"https://codeload.github.com/{repo}/zip/refs/heads/{DEFAULT_BRANCH}"
    request = urllib.request.Request(url, headers={"User-Agent": "WorkflowsQBuilder"})
    with urllib.request.urlopen(request, timeout=120, context=ssl_context()) as response:
        payload = response.read()

    backup = ROOT / "_backup_previous"
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    backup.mkdir(exist_ok=True)

    updated = []
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "update.zip"
        archive.write_bytes(payload)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(Path(tmp) / "extracted")
        roots = [item for item in (Path(tmp) / "extracted").iterdir() if item.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("archive inattendue : pas de dossier racine unique")
        source = roots[0]

        for item in source.rglob("*"):
            if not item.is_file():
                continue
            relative = item.relative_to(source)
            if relative.name in UPDATE_SKIP:
                continue
            if set(relative.parts) & UPDATE_SKIP_DIRS:
                continue
            target = ROOT / relative
            new_bytes = item.read_bytes()
            # Pour les .bat on compare octet pour octet, apres avoir impose les
            # fins de ligne Windows : une installation faite depuis le zip de
            # GitHub arrive en LF, et la comparaison tolerante la laisserait
            # ainsi indefiniment. La premiere mise a jour la repare.
            is_batch = relative.suffix.lower() == ".bat"
            if is_batch:
                new_bytes = force_crlf(new_bytes)
            if target.exists():
                current = target.read_bytes()
                identical = (current == new_bytes) if is_batch else same_content(current, new_bytes)
                if identical:
                    continue
            if target.exists():
                saved = backup / relative
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(new_bytes)
            updated.append(str(relative))

    version = latest_commit()
    version["installed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (ROOT / "VERSION.json").write_text(json.dumps(version, indent=2), encoding="utf-8")
    # Un .py mis a jour ne prend effet qu'au prochain demarrage : le serveur qui
    # repond ici tourne encore sur l'ancien code.
    needs_restart = any(name.endswith(".py") for name in updated)
    return {"updated": updated, "version": version, "needs_restart": needs_restart}


def port_taken(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((HOST, port)) == 0


class Handler(BaseHTTPRequestHandler):
    server_version = "WorkflowsQBuilder/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_no_cache(self):
        """Interdit la mise en cache.

        Sans ca, une mise a jour remplace bien les fichiers sur le disque mais
        le navigateur continue de servir l'ancienne page depuis son cache : on
        clique Update, tout se passe bien, et rien ne change a l'ecran. Vecu
        sur un poste secondaire.
        """
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_no_cache()
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
        self.send_no_cache()
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
            payload = {"root": str(ROOT), "app": APP_HTML, "python": sys.executable,
                       "version": read_version().get("sha", "")}
            payload.update(local_settings())
            self.send_json(payload)
            return
        if parsed.path == "/api/ref-image":
            self.handle_ref_image(parse_qs(parsed.query))
            return
        if parsed.path == "/api/workflow-read":
            self.handle_workflow_read(parse_qs(parsed.query))
            return
        if parsed.path == "/api/update-check":
            self.handle_update_check()
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
        if parsed.path == "/api/update":
            self.handle_update()
            return
        if parsed.path == "/api/h3-ref-upload":
            self.handle_h3_ref_upload()
            return
        self.send_error(404)

    def handle_h3_ref_upload(self):
        """Depose une image de reference la ou ComfyUI ira la chercher.

        Le noeud Director resout `musedirector/<projet>/<fichier>` sous le
        dossier input de ComfyUI. Copier l'image ailleurs obligerait a refaire
        la synchro a la main avant chaque RUN — c'est justement l'etape qu'on
        veut supprimer.
        """
        try:
            filename, data = self.read_multipart_file("image")
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)
            return

        project = ""
        try:
            project = self.last_fields.get("project", "")
        except AttributeError:
            project = ""
        project = Path(project.strip()).name if project else ""

        safe_name = Path(filename).name
        comfy_input = local_settings().get("comfy_input", "")
        if comfy_input and Path(comfy_input).is_dir():
            target_dir = Path(comfy_input) / "musedirector"
            if project:
                target_dir = target_dir / project
            placed_in_comfy = True
        else:
            # Sans dossier input connu, on garde l'image a portee plutot que de
            # refuser : elle sera juste a copier a la main avant de lancer.
            target_dir = UPLOAD_DIR
            placed_in_comfy = False

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / safe_name
            target.write_bytes(data)
        except OSError as exc:
            self.send_json({"error": f"Copie impossible vers {target_dir}: {exc}"}, 500)
            return

        self.send_json({
            "filename": safe_name,
            "path": str(target),
            "in_comfy": placed_in_comfy,
            "subfolder": f"musedirector/{project}" if project else "musedirector",
        })

    def handle_update_check(self):
        current = read_version()
        try:
            latest = latest_commit()
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError) as exc:
            self.send_json({"error": f"Depot injoignable: {explain_network_error(exc)}",
                            "repo": update_repo()}, 502)
            return
        self.send_json({
            "repo": update_repo(),
            "current": current,
            "latest": latest,
            # Sans VERSION.json (installation copiee a la main), on ne peut pas
            # savoir ou on en est : on propose la mise a jour plutot que de
            # pretendre que tout est a jour.
            "behind": current.get("sha") != latest.get("sha"),
        })

    def handle_update(self):
        try:
            result = install_update()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
            self.send_json({"error": f"Mise a jour impossible: {explain_network_error(exc)}"}, 502)
            return
        self.send_json(result)

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
        # Champs texte de la requete (project, filename_hint...), disponibles
        # pour l'appelant apres le parsing.
        self.last_fields = {}
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

            name = params.get("name")
            if name and "filename" not in params:
                # Partie sans nom de fichier : c'est un champ texte.
                self.last_fields[name] = body.decode("utf-8", errors="ignore").strip()
            if name == "filename_hint":
                filename_hint = self.last_fields.get("filename_hint", "")
                continue
            if name != expected_field:
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

    def handle_ref_image(self, query):
        """Sert l'image de reference telle que ComfyUI la verra.

        L'apercu au survol montre le fichier reellement present dans le dossier
        input, pas celui qu'on vient de choisir dans le navigateur : c'est la
        seule facon de verifier que le workflow aura bien la bonne image.
        """
        name = Path(unquote((query.get("name") or [""])[0])).name
        project = Path(unquote((query.get("project") or [""])[0])).name
        if not name:
            self.send_error(404)
            return

        candidates = []
        comfy_input = local_settings().get("comfy_input", "")
        if comfy_input:
            base = Path(comfy_input) / "musedirector"
            if project:
                candidates.append(base / project / name)
            candidates.append(base / name)
            candidates.append(Path(comfy_input) / name)
        candidates.append(UPLOAD_DIR / name)

        for candidate in candidates:
            if candidate.is_file():
                self.send_file(candidate)
                return
        self.send_error(404)

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
        print(f"Le port {args.port} est deja pris par un autre serveur : le plus souvent une "
              f"instance de celui-ci restee ouverte dans une autre fenetre, sinon l'ancien "
              f"ltx_builder_server.py.")
        print(f"Ouvre http://{HOST}:{args.port}/ pour voir laquelle repond, ferme sa fenetre "
              f"noire, puis relance. Ou lance celui-ci sur un autre port:")
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
