"""Registre local et reprise prudente des jobs envoyes a un cluster ComfyUI."""

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


DEFAULT_LEDGER = "qbuilder_jobs.json"


def default_path(root):
    return Path(root) / DEFAULT_LEDGER


def load(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    data.setdefault("jobs", [])
    return data


def save(path, data):
    path = Path(path)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record(path, job):
    data = load(path)
    item = dict(job)
    item.setdefault("id", str(uuid.uuid4()))
    item.setdefault("submitted_at", int(time.time()))
    item.setdefault("state", "queued")
    item.setdefault("retries", 0)
    data["jobs"].append(item)
    # Le registre sert a reprendre des jobs recents, pas a archiver toute la
    # vie du poste de commande.
    data["jobs"] = data["jobs"][-500:]
    save(path, data)
    return item


def update(path, job_id, **changes):
    data = load(path)
    for job in data["jobs"]:
        if job.get("id") == job_id:
            job.update(changes)
            job["updated_at"] = int(time.time())
            save(path, data)
            return job
    return None


def _json(url, timeout=15):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def probe(job):
    """Retourne finished, failed, queued, missing ou unreachable."""
    server = str(job.get("server") or "").rstrip("/")
    prompt_id = job.get("prompt_id") or ""
    if not server or not prompt_id:
        return "invalid", "registre incomplet"
    try:
        entry = _json(f"{server}/history/{prompt_id}").get(prompt_id)
        if entry:
            status = entry.get("status") or {}
            messages = status.get("messages") or []
            if status.get("status_str") == "error" or any(
                isinstance(msg, (list, tuple)) and msg and msg[0] == "execution_error" for msg in messages
            ):
                return "failed", "erreur d'execution ComfyUI"
            if status.get("completed") or status.get("status_str") == "success" or entry.get("outputs"):
                return "finished", "termine"
        queue = _json(f"{server}/queue")
        for key in ("queue_running", "queue_pending"):
            for item in queue.get(key) or []:
                if isinstance(item, list) and len(item) > 1 and item[1] == prompt_id:
                    return "queued", "en file ComfyUI"
        return "missing", "absent de la file et de l'historique"
    except (OSError, ValueError, urllib.error.URLError, TimeoutError) as exc:
        return "unreachable", str(exc)


def queue_prompt(server, workflow, client_id):
    payload = json.dumps({"client_id": client_id, "prompt": workflow}).encode("utf-8")
    request = urllib.request.Request(
        f"{server.rstrip('/')}/prompt", data=payload,
        headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def reachable_loads(servers):
    loads = {}
    for server in servers:
        try:
            queue = _json(f"{server.rstrip('/')}/queue", timeout=6)
            loads[server.rstrip("/")] = len(queue.get("queue_running") or []) + len(queue.get("queue_pending") or [])
        except (OSError, ValueError, urllib.error.URLError, TimeoutError):
            pass
    return loads


def recover(path, servers, retry=False):
    """Inspecte le registre, et reprend UNE fois les jobs perdus/en erreur.

    Un worker injoignable n'est jamais suppose avoir perdu son job : le risque
    de lancer un doublon pendant une panne reseau serait pire que l'attente.
    """
    path = Path(path)
    data = load(path)
    loads = reachable_loads(servers)
    report = []
    for job in data["jobs"]:
        if job.get("state") == "finished":
            continue
        state, detail = probe(job)
        job["state"], job["detail"], job["checked_at"] = state, detail, int(time.time())
        action = ""
        if retry and state in {"missing", "failed"} and job.get("retries", 0) < 1 and job.get("workflow"):
            candidates = [server for server in loads if server != job.get("server")]
            if candidates:
                target = min(candidates, key=loads.get)
                try:
                    # Les references sont re-uploadees avec le meme nom distant
                    # que celui deja inscrit dans le workflow sauvegarde.
                    from queue_ltx_multishot import upload_image_path
                    for ref in job.get("references") or []:
                        source = Path(ref.get("source") or "")
                        if source.is_file():
                            upload_image_path(target, source, subfolder=ref.get("subfolder") or "ltx_queue",
                                              overwrite="true", filename=ref.get("filename") or source.name)
                    response = queue_prompt(target, job["workflow"], str(uuid.uuid4()))
                    job.update({
                        "previous_server": job.get("server"), "previous_prompt_id": job.get("prompt_id"),
                        "server": target, "prompt_id": response.get("prompt_id", "unknown"),
                        "state": "queued", "detail": "reprise unique envoyee", "retries": job.get("retries", 0) + 1,
                        "retried_at": int(time.time()),
                    })
                    loads[target] += 1
                    action = f" -> relance sur {target}"
                except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                    job["detail"] = f"reprise impossible: {exc}"
        report.append({"name": job.get("name") or job.get("id"), "state": job.get("state"),
                       "detail": job.get("detail", ""), "action": action})
    save(path, data)
    return report
