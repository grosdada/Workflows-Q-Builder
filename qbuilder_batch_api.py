"""Local, allowlisted H3 Director batch control for Q-Builder and its travel wrapper.

The HTML v3 packet is compiled to the same brief format used by the browser's
H3 Director button. Rendering uses queue_h3_multishot's compiler, uploader and
ComfyUI submission functions; this module only adds HTTP-oriented persistence.
"""

import base64
import binascii
import copy
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode

import h3_director_build as director
import queue_h3_multishot as h3
from network_cluster import initial_loads, select_server
from qbuilder_job_ledger import default_path, probe as probe_job
from qbuilder_job_ledger import record as record_job, update as update_job
from queue_ltx_multishot import copy_prompt_outputs, wait_prompt_result


ROOT = Path(__file__).resolve().parent
STORE = ROOT / "qbuilder_api_batches.json"
STAGING = ROOT / "ltx_builder_uploads" / "api_batches"
TEMPLATE = ROOT / "h3_director_api.json"
LOCK = threading.RLock()
WATCHING = set()
PROCESS_TOKEN = str(uuid.uuid4())
ALLOWED_OVERRIDES = {
    "megapixels", "steps", "aspect_ratio", "sampler_name", "scheduler",
    "ref_image_size", "seed_hunt", "resize_method", "multiple",
    "style_preset", "style_prompt",
}
INA_16MM_PROMPT = ("Use a 16 mm INA documentary look with archival French institutional film texture, "
                   "restrained observational camera work, practical location sound, modest grain, "
                   "natural color response, and sober public-broadcast documentary pacing.")
IMAGE_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


class BatchError(Exception):
    def __init__(self, message, status=400, details=None):
        super().__init__(message)
        self.status = status
        self.details = details or []


class _PacketParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.packets = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attrs = dict(attrs)
            self.active = attrs.get("type") == "application/json" or "data-prompts" in attrs
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.packets.append("".join(self.parts))
            self.active = False


def _store():
    try:
        data = json.loads(STORE.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STORE)


def _roots(settings):
    roots = settings.get("api_allowed_roots") or []
    if not isinstance(roots, list):
        return []
    return [Path(value).resolve() for value in roots if isinstance(value, str) and value.strip()]


def allowed_path(raw, settings, must_exist=False):
    if not isinstance(raw, str) or not raw.strip():
        raise BatchError("Chemin de fichier ou dossier manquant.")
    roots = _roots(settings)
    if not roots:
        raise BatchError("Configure api_allowed_roots dans local_settings.json.", 403)
    path = Path(raw).expanduser().resolve()
    permitted = False
    for root in roots:
        try:
            permitted = os.path.commonpath([os.path.normcase(str(path)), os.path.normcase(str(root))]) == os.path.normcase(str(root))
        except ValueError:  # chemins situes sur des lecteurs Windows differents
            continue
        if permitted:
            break
    if not path.is_absolute() or not permitted:
        raise BatchError("Chemin hors des racines autorisees.", 403)
    if must_exist and not path.is_file():
        raise BatchError(f"Fichier introuvable : {path}", 404)
    return path


def _packet(request, settings):
    if "prompts" in request:
        packet = request["prompts"]
        source = ""
    else:
        source = str(allowed_path(request.get("html_path"), settings, True))
        if Path(source).suffix.lower() not in {".html", ".htm"}:
            raise BatchError("html_path doit pointer vers un fichier HTML.")
        if Path(source).stat().st_size > 64 * 1024 * 1024:
            raise BatchError("HTML trop volumineux (64 Mo maximum).", 413)
        parser = _PacketParser()
        parser.feed(Path(source).read_text(encoding="utf-8-sig"))
        packet = None
        for text in parser.packets:
            try:
                candidate = json.loads(text.strip())
            except ValueError:
                continue
            if isinstance(candidate, dict) and isinstance(candidate.get("scenes"), list):
                packet = candidate
                break
    if not isinstance(packet, dict) or packet.get("schema") != "qbuilder-html/v3" or packet.get("model") != "h3-director":
        raise BatchError("Cette API attend un paquet HTML qbuilder-html/v3 pour h3-director.")
    if not isinstance(packet.get("scenes"), list) or not packet["scenes"]:
        raise BatchError("Aucune scene H3 dans le paquet.")
    if len(packet["scenes"]) > 20:
        raise BatchError("20 scenes maximum par batch.")
    return packet, source


def _assets(packet):
    assets = packet.get("assets") or {}
    if isinstance(assets, list):
        assets = {str(item.get("id")): item for item in assets if isinstance(item, dict) and item.get("id")}
    if not isinstance(assets, dict):
        raise BatchError("assets doit etre un objet ou une liste.")
    return assets


def _image_bytes(asset):
    if not isinstance(asset, dict):
        raise BatchError("Asset image invalide.")
    url = asset.get("data_url") or asset.get("dataUrl") or asset.get("image") or asset.get("source")
    if not isinstance(url, str):
        raise BatchError("Asset sans data_url image.")
    match = re.fullmatch(r"data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\s]+)", url)
    if not match:
        raise BatchError("data_url image invalide (JPEG, PNG ou WebP base64 requis).")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except binascii.Error as exc:
        raise BatchError("Image base64 illisible.") from exc
    if len(raw) > 25 * 1024 * 1024:
        raise BatchError("Image superieure a 25 Mo.", 422)
    return raw, IMAGE_MIME[match.group(1)]


def _scene_output_name(scene, index):
    if "output_name" in scene:
        raw = scene["output_name"]
        if not isinstance(raw, str) or not raw.strip():
            raise BatchError("output_name doit etre une chaine non vide.", 422)
        name = h3.safe_slug(raw, "")
        if not name or name in {".", ".."}:
            raise BatchError("output_name invalide apres normalisation.", 422)
        return name
    name = str(scene.get("name") or f"scene_{index:02d}")
    return f"api_{index:02d}_{h3.safe_slug(name, 'scene')}"


def _scene_brief(packet, scene, assets, paths, overrides, index, style_prefix=""):
    ids = scene.get("character_refs") or []
    if not isinstance(ids, list):
        raise BatchError("character_refs doit etre une liste.")
    def ref(asset_id):
        asset = assets[str(asset_id)]
        return {"file": paths.get(str(asset_id), ""), "description": asset.get("description") or "",
                "retention": asset.get("retention") or "fully_preserved"}
    name = str(scene.get("name") or f"scene_{index:02d}")
    settings = {
        "mode": "reference", "aspect_ratio": packet.get("aspect_ratio") or "4:3",
        "megapixels": 0.8, "steps": 20, "sampler_name": "res_multistep",
        "scheduler": "simple", "ref_image_size": "match", "link_mode": "copy_project",
        "seed": scene.get("seed") or 100,
        "output_name": f"Muse/Video/{_scene_output_name(scene, index)}",
    }
    settings.update({key: value for key, value in overrides.items() if key not in {"style_preset", "style_prompt"}})
    scene_style = scene.get("custom_style") or scene.get("style_line") or ""
    return {"name": name, "settings": settings,
            "style_line": " ".join(filter(None, [style_prefix, packet.get("style_line") or "", scene_style])),
            "dialogue_language": packet.get("dialogue_language") or "English",
            "overall_soundscape": packet.get("overall_soundscape") or "",
            "non_diegetic_music": packet.get("non_diegetic_music") or "",
            "characters": [ref(value) for value in ids],
            "background": ref(scene["background_ref"]) if scene.get("background_ref") else None,
            "cuts": scene.get("cuts") or []}


def validate(request, settings):
    packet, source = _packet(request, settings)
    assets = _assets(packet)
    errors, warnings, asset_rows, scenes = [], [], [], []
    for asset_id, asset in assets.items():
        try:
            raw, _ = _image_bytes(asset)
            size = len(raw)
        except BatchError as exc:
            errors.append(f"Asset {asset_id}: {exc}")
            size = 0
        asset_rows.append({"id": asset_id, "filename": asset.get("filename", "") if isinstance(asset, dict) else "",
                           "size": size, "has_retention": isinstance(asset, dict) and "retention" in asset})
    names = set()
    output_names = set()
    overrides = request.get("settings_override") or {}
    if not isinstance(overrides, dict) or set(overrides) - ALLOWED_OVERRIDES:
        raise BatchError("settings_override contient un parametre interdit.")
    for index, scene in enumerate(packet["scenes"], 1):
        if not isinstance(scene, dict):
            errors.append(f"Scene {index}: objet invalide")
            continue
        name = str(scene.get("name") or f"scene_{index:02d}")
        if name in names:
            errors.append(f"Nom de scene duplique : {name}")
        names.add(name)
        output_name = None
        try:
            output_name = _scene_output_name(scene, index)
            if output_name in output_names:
                errors.append(f"output_name duplique : {output_name}")
            output_names.add(output_name)
        except BatchError as exc:
            errors.append(f"{name}: {exc}")
        ids = scene.get("character_refs") or []
        if not isinstance(ids, list):
            ids = []
            errors.append(f"{name}: character_refs invalide")
        background = scene.get("background_ref")
        if len(ids) + bool(background) > 9:
            errors.append(f"{name}: plus de 9 references")
        for asset_id in ids + ([background] if background else []):
            if str(asset_id) not in assets:
                errors.append(f"{name}: asset inconnu {asset_id}")
        cuts = scene.get("cuts") or []
        try:
            total = sum(float(cut.get("seconds", 0)) for cut in cuts)
        except (TypeError, ValueError, AttributeError):
            total = 0
            errors.append(f"{name}: duree des CUT invalide")
        if total > 15:
            errors.append(f"{name}: {total:g}s > 15s (risque OOM)")
        if not cuts or total <= 0:
            errors.append(f"{name}: aucun CUT valide")
        for cut_index, cut in enumerate(cuts, 1):
            if not isinstance(cut, dict):
                errors.append(f"{name} CUT {cut_index}: objet invalide")
                continue
            line = str(cut.get("text") or "")
            speakers = cut.get("speakers") or []
            if not isinstance(speakers, list):
                errors.append(f"{name} CUT {cut_index}: speakers doit etre une liste")
                speakers = []
            for number in re.findall(r"<Subject (\d+)>", line):
                if int(number) > len(ids) or int(number) < 1:
                    errors.append(f"{name} CUT {cut_index}: <Subject {number}> hors casting")
            if '"' in line and not speakers:
                errors.append(f"{name} CUT {cut_index}: dialogue sans locuteur")
            if any(not isinstance(n, int) or n < 1 or n > len(ids) for n in speakers):
                errors.append(f"{name} CUT {cut_index}: speaker hors casting")
        if output_name and not any(str(asset_id) not in assets for asset_id in ids + ([background] if background else [])):
            try:
                brief = _scene_brief(packet, scene, assets, {key: "placeholder.png" for key in assets}, overrides, index)
                _, _, note = director.normalise(brief)
                warnings.extend(f"{name}: {message}" for message in note)
            except (director.BriefError, ValueError, TypeError, KeyError) as exc:
                errors.append(f"{name}: {exc}")
        scenes.append({"name": name, "seed": scene.get("seed"), "cuts": len(cuts),
                       "duration": total, "character_refs": ids, "background_ref": background,
                       "output_name": output_name})
    return {"ok": not errors, "schema": packet["schema"], "model": packet["model"],
            "aspect_ratio": packet.get("aspect_ratio"), "duration": packet.get("duration"),
            "dialogue_language": packet.get("dialogue_language"), "html_path": source,
            "scenes": scenes, "assets": asset_rows, "errors": errors, "warnings": warnings}, packet


def _selection(value, scenes):
    if value in (None, "all"):
        return list(range(len(scenes)))
    if isinstance(value, list):
        names = {str(item) for item in value}
        indexes = [i for i, scene in enumerate(scenes) if scene.get("name") in names]
        if len(indexes) != len(names):
            raise BatchError("Une scene demandee est inconnue.")
        return indexes
    if isinstance(value, str) and re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", value.replace(" ", "")):
        selected = set()
        for part in value.replace(" ", "").split(","):
            bounds = part.split("-")
            start, end = int(bounds[0]), int(bounds[-1])
            if start < 1 or end > len(scenes) or start > end:
                raise BatchError("Selection de scenes hors plage.")
            selected.update(range(start - 1, end))
        return sorted(selected)
    raise BatchError("scenes doit valoir all, 01, 01,03-05 ou une liste de noms.")


def _workers(value, settings):
    nodes = settings.get("comfy_nodes") or []
    if value in (None, "auto"):
        selected = [node for node in nodes if node.get("auto")]
    elif isinstance(value, list):
        names = {str(name).upper() for name in value}
        selected = [node for node in nodes if node["name"].upper() in names]
        if len(selected) != len(names):
            raise BatchError("Worker inconnu dans la selection.")
    else:
        raise BatchError("workers doit valoir auto ou une liste de noms.")
    if not selected:
        raise BatchError("Aucun worker actif.", 503)
    try:
        loads = initial_loads([node["url"].rstrip("/") for node in selected])
    except RuntimeError as exc:
        raise BatchError(str(exc), 503) from exc
    return selected, loads


def _stage_assets(batch_id, assets):
    folder = STAGING / batch_id
    folder.mkdir(parents=True, exist_ok=False)
    paths = {}
    for asset_id, asset in assets.items():
        raw, ext = _image_bytes(asset)
        digest = hashlib.sha256(str(asset_id).encode("utf-8")).hexdigest()[:10]
        name = h3.safe_slug(str(asset_id), "image")[:45] + "_" + digest + ext
        target = folder / name
        target.write_bytes(raw)
        paths[str(asset_id)] = str(target)
    return paths


def _ledger(job, **changes):
    if job.get("ledger_id"):
        if changes.get("state") == "done":
            changes["state"] = "finished"
        update_job(default_path(ROOT), job["ledger_id"], **changes)


def _change(batch_id, job_id, **changes):
    with LOCK:
        data = _store()
        batch = data.get(batch_id)
        if not batch:
            return None
        job = next((entry for entry in batch["jobs"] if entry["job_id"] == job_id), None)
        if not job:
            return None
        job.update(changes)
        _save(data)
        _ledger(job, **{key: value for key, value in changes.items() if key in {
            "state", "prompt_id", "server", "workflow", "references", "retries", "detail",
            "previous_server", "previous_prompt_id"}})
        return copy.deepcopy(job)


def _snapshot(batch_id):
    with LOCK:
        batch = _store().get(batch_id)
        if not batch:
            raise BatchError("Batch inconnu.", 404)
        return copy.deepcopy(batch)


def _outputs(entry):
    result = []
    seen = set()
    for node in (entry.get("outputs") or {}).values():
        if not isinstance(node, dict):
            continue
        for key in ("images", "gifs", "videos", "audio"):
            for item in node.get(key) or []:
                if isinstance(item, dict) and item.get("filename"):
                    source_key = (item["filename"], item.get("subfolder", ""), item.get("type", "output"))
                    if source_key in seen:
                        continue
                    seen.add(source_key)
                    result.append({"filename": item["filename"], "subfolder": item.get("subfolder", ""),
                                   "type": item.get("type", "output"), "local": ""})
    return result


def _submit(batch_id, job_id, worker=None):
    batch = _snapshot(batch_id)
    job = next(item for item in batch["jobs"] if item["job_id"] == job_id)
    if job["state"] == "cancelled":
        return
    server = worker or job["server"]
    try:
        name = batch.get("worker_names", {}).get(server, job["worker"])
        _change(batch_id, job_id, state="uploading", server=server, worker=name,
                detail="upload des references")
        settings, timeline, warnings = director.normalise(job["brief"])
        references = []
        h3.upload_h3_images(server, timeline, settings, {}, references)
        if _snapshot(batch_id)["jobs"][job["index"]]["state"] == "cancelled":
            return
        workflow = json.loads(TEMPLATE.read_text(encoding="utf-8-sig"))
        h3.patch_api_workflow(workflow, settings, timeline, settings["output_name"])
        available = h3.available_node_types(server)
        if available:
            _, blocking = h3.prune_unusable_nodes(workflow, available)
            if blocking:
                raise BatchError("Noeuds ComfyUI manquants : " + ", ".join(str(item) for item in blocking), 502)
        response = h3.queue_prompt(server, workflow, str(uuid.uuid4()))
        prompt_id = response.get("prompt_id")
        if not prompt_id:
            raise BatchError("ComfyUI n'a renvoye aucun prompt_id.", 502)
        if next(item for item in _snapshot(batch_id)["jobs"] if item["job_id"] == job_id)["state"] == "cancelled":
            req = urllib.request.Request(server + "/queue", json.dumps({"delete": [prompt_id]}).encode(),
                                         {"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=10).close()
            return
        with LOCK:
            ledger = record_job(default_path(ROOT), {
                "kind": "h3", "name": job["scene"], "batch_id": batch_id,
                "prompt_id": prompt_id, "server": server, "workflow": workflow,
                "references": references, "state": "queued", "retries": job.get("retries", 0),
            })
            _change(batch_id, job_id, state="queued", prompt_id=prompt_id,
                    workflow=workflow, references=references, ledger_id=ledger["id"],
                    detail="en file ComfyUI", warnings=warnings)
        _watch(batch_id, job_id)
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError):
            detail = h3.describe_comfy_rejection(exc)
        else:
            detail = str(exc)
        _change(batch_id, job_id, state="failed", detail=detail[:1200], finished_at=int(time.time()))


def _monitor(batch_id, job_id):
    job = {}
    try:
        job = next(item for item in _snapshot(batch_id)["jobs"] if item["job_id"] == job_id)
        if not job.get("prompt_id") or job["state"] == "cancelled":
            return
        ok, entry, reason = wait_prompt_result(job["server"], job["prompt_id"])
        latest = next(item for item in _snapshot(batch_id)["jobs"] if item["job_id"] == job_id)
        if latest["state"] == "cancelled" or latest.get("prompt_id") != job["prompt_id"]:
            return
        if not ok:
            observed, observed_detail = probe_job(job)
            if observed == "unreachable":
                _change(batch_id, job_id, state="running",
                        detail=f"Worker injoignable, reprise suspendue : {observed_detail}"[:1200])
                return
            if observed in {"queued", "finished"}:
                _change(batch_id, job_id, state="queued", detail="Job retrouve sur le worker ; surveillance reprise")
                return
            if entry:
                for message in (entry.get("status") or {}).get("messages") or []:
                    if isinstance(message, (list, tuple)) and len(message) > 1 and message[0] == "execution_error":
                        info = message[1] if isinstance(message[1], dict) else {}
                        reason = f"{info.get('exception_type', 'ComfyUI')}: {info.get('exception_message', reason)}"[:1200]
                        break
            if latest.get("retries", 0) < 1:
                settings = _snapshot(batch_id)["worker_urls"]
                candidates = [url for url in settings if url != job["server"]]
                try:
                    loads = initial_loads(candidates) if candidates else {}
                except RuntimeError:
                    loads = {}
                if loads:
                    target = select_server(loads)
                    _change(batch_id, job_id, state="retried", retries=1,
                            previous_server=job["server"], previous_prompt_id=job["prompt_id"],
                            prompt_id=None, detail=f"{reason}; relance sur {target}")
                    _submit(batch_id, job_id, target)
                    return
            _change(batch_id, job_id, state="failed", detail=reason, finished_at=int(time.time()))
            return
        outputs = _outputs(entry or {})
        copy_path = _snapshot(batch_id).get("copy_results") or ""
        copy_error = ""
        if copy_path:
            try:
                saved = copy_prompt_outputs(job["server"], job["prompt_id"], copy_path)
                for item, path in zip(outputs, saved):
                    item["local"] = str(path)
            except Exception as exc:
                copy_error = f"Rendu termine, copie impossible : {exc}"
        _change(batch_id, job_id, state="done", outputs=outputs,
                finished_at=int(time.time()), detail=copy_error or "termine")
    except Exception as exc:
        _change(batch_id, job_id, state="failed", detail=str(exc)[:1200], finished_at=int(time.time()))
    finally:
        with LOCK:
            WATCHING.discard((batch_id, job_id, job.get("prompt_id")))


def _watch(batch_id, job_id):
    with LOCK:
        current = next((item for item in _store()[batch_id]["jobs"] if item["job_id"] == job_id), None)
        key = (batch_id, job_id, current.get("prompt_id") if current else None)
        if key in WATCHING:
            return
        WATCHING.add(key)
    threading.Thread(target=_monitor, args=(batch_id, job_id), daemon=True).start()


def _resume():
    for batch_id, batch in _store().items():
        for job in batch.get("jobs", []):
            if job.get("state") == "uploading" and not job.get("prompt_id") and job.get("session") != PROCESS_TOKEN:
                _change(batch_id, job["job_id"], state="failed",
                        detail="Serveur redemarre pendant l'envoi ; verifier la file avant une relance.")
            if job.get("prompt_id") and job.get("state") in {"queued", "running", "retried"}:
                _watch(batch_id, job["job_id"])


def _public(batch):
    jobs = []
    for job in batch["jobs"]:
        jobs.append({key: job.get(key) for key in (
            "scene", "output_name", "job_id", "worker", "prompt_id", "state", "queue_position",
            "started_at", "finished_at", "detail", "outputs", "retries")})
        if not jobs[-1]["output_name"]:
            prefix = (job.get("brief") or {}).get("settings", {}).get("output_name") or ""
            jobs[-1]["output_name"] = prefix.rsplit("/", 1)[-1] or None
        jobs[-1]["status"] = jobs[-1].pop("state")
    counts = {state: sum(job["status"] == state for job in jobs) for state in
              ("queued", "uploading", "running", "done", "failed", "retried", "cancelled")}
    return {"ok": True, "batch_id": batch["batch_id"], "label": batch.get("label", ""),
            "created_at": batch["created_at"], "html_path": batch.get("html_path", ""),
            "jobs": jobs, "summary": counts}


def start(request, settings):
    inherited = settings.get("h3_api_defaults") or {}
    if not isinstance(inherited, dict):
        inherited = {}
    merged_request = dict(request, settings_override={**inherited, **(request.get("settings_override") or {})})
    report, packet = validate(merged_request, settings)
    if report["errors"]:
        raise BatchError("Batch invalide : aucun job envoye.", 422, report["errors"])
    indices = _selection(request.get("scenes"), packet["scenes"])
    selected, loads = _workers(request.get("workers"), settings)
    urls = list(loads)
    default_copy = settings.get("results_copy_path", "") if settings.get("api_copy_results_enabled") else ""
    copy_setting = request.get("copy_results", default_copy)
    if not isinstance(copy_setting, str):
        raise BatchError("copy_results doit etre un chemin ou une chaine vide.")
    copy_path = str(allowed_path(copy_setting, settings)) if copy_setting else ""
    batch_id = str(uuid.uuid4())
    overrides = dict(merged_request["settings_override"])
    overrides = {key: value for key, value in overrides.items() if key in ALLOWED_OVERRIDES}
    style_preset = overrides.get("style_preset", settings.get("h3_style_preset", ""))
    style_prefix = overrides.get("style_prompt") or ""
    if not style_prefix and style_preset == settings.get("h3_style_preset"):
        style_prefix = settings.get("h3_style_prompt") or ""
    if not style_prefix and style_preset == "ina_16mm":
        style_prefix = INA_16MM_PROMPT
    if style_preset not in {"", "none", "ina_16mm", settings.get("h3_style_preset", "")} and not style_prefix:
        raise BatchError("Preset style inconnu : fournis aussi settings_override.style_prompt.")
    if not packet.get("style_line") and settings.get("h3_style_line"):
        style_prefix = " ".join(filter(None, [style_prefix, settings["h3_style_line"]]))
    elif style_prefix and style_prefix in (packet.get("style_line") or ""):
        style_prefix = ""
    assets = _assets(packet)
    paths = {key: "placeholder.png" for key in assets}
    if not request.get("dry_run"):
        paths = _stage_assets(batch_id, assets)
    jobs = []
    for index in indices:
        scene = packet["scenes"][index]
        server = select_server(loads)
        brief = _scene_brief(packet, scene, assets, paths, overrides, index + 1, style_prefix)
        effective, _, _ = director.normalise(brief)
        worker = next(node["name"] for node in selected if node["url"].rstrip("/") == server)
        jobs.append({"index": len(jobs), "scene": brief["name"], "job_id": str(uuid.uuid4()),
                     "worker": worker, "server": server, "prompt_id": None,
                     "output_name": _scene_output_name(scene, index + 1),
                     "state": "dry_run" if request.get("dry_run") else "uploading",
                     "brief": brief, "settings": {key: effective[key] for key in
                         ("megapixels", "steps", "aspect_ratio", "sampler_name", "scheduler", "ref_image_size")},
                     "started_at": int(time.time()), "finished_at": None, "session": PROCESS_TOKEN,
                     "outputs": [], "retries": 0})
    if request.get("dry_run"):
        return {"ok": True, "dry_run": True, "batch_id": batch_id,
                "jobs": [{"scene": job["scene"], "job_id": job["job_id"],
                          "worker": job["worker"], "prompt_id": None, "status": "dry_run",
                          "output_name": job["output_name"],
                          "settings": job["settings"], "style_preset": style_preset,
                          "style_line": job["brief"]["style_line"]}
                         for job in jobs]}
    batch = {"batch_id": batch_id, "label": str(request.get("label") or ""),
             "html_path": report["html_path"], "created_at": int(time.time()),
             "worker_urls": urls,
             "worker_names": {node["url"].rstrip("/"): node["name"] for node in selected},
             "copy_results": copy_path, "jobs": jobs}
    with LOCK:
        data = _store()
        data[batch_id] = batch
        _save(data)
    for job in jobs:
        threading.Thread(target=_submit, args=(batch_id, job["job_id"]), daemon=True).start()
    return _public(batch)


def detail(batch_id):
    _resume()
    batch = _snapshot(batch_id)
    for job in batch["jobs"]:
        if not job.get("prompt_id") or job["state"] in {"done", "failed", "cancelled"}:
            continue
        try:
            with urllib.request.urlopen(job["server"] + "/queue", timeout=4) as response:
                queue = json.load(response)
            for state, key in (("running", "queue_running"), ("queued", "queue_pending")):
                for position, item in enumerate(queue.get(key) or [], 1):
                    if isinstance(item, list) and len(item) > 1 and item[1] == job["prompt_id"]:
                        job["state"] = state
                        job["queue_position"] = position
        except (OSError, ValueError):
            pass
    return _public(batch)


def batches(limit=20):
    _resume()
    records = sorted(_store().values(), key=lambda item: item.get("created_at", 0), reverse=True)
    return {"ok": True, "batches": [{key: _public(item)[key] for key in
              ("batch_id", "label", "created_at", "html_path", "summary")}
              for item in records[:max(1, min(int(limit), 100))]]}


def _selected_jobs(batch, value):
    names = _selection(value, [{"name": job["scene"]} for job in batch["jobs"]])
    return [batch["jobs"][index] for index in names]


def cancel(batch_id, request):
    batch = _snapshot(batch_id)
    for job in _selected_jobs(batch, request.get("scenes")):
        if job["state"] in {"done", "cancelled", "failed"}:
            continue
        prompt_id = job.get("prompt_id")
        if prompt_id:
            with urllib.request.urlopen(job["server"] + "/queue", timeout=6) as response:
                queue = json.load(response)
            pending = any(isinstance(item, list) and len(item) > 1 and item[1] == prompt_id
                          for item in queue.get("queue_pending") or [])
            running = any(isinstance(item, list) and len(item) > 1 and item[1] == prompt_id
                          for item in queue.get("queue_running") or [])
            if pending:
                body = json.dumps({"delete": [prompt_id]}).encode()
                req = urllib.request.Request(job["server"] + "/queue", body,
                                             {"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=10).close()
            elif running:
                # /interrupt targets the active prompt only; verify its ID first.
                req = urllib.request.Request(job["server"] + "/interrupt", b"{}",
                                             {"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=10).close()
            else:
                raise BatchError(f"{job['scene']}: prompt absent de la file, annulation non confirmee.", 409)
        _change(batch_id, job["job_id"], state="cancelled", finished_at=int(time.time()), detail="annule")
    return detail(batch_id)


def retry(batch_id, request, settings):
    batch = _snapshot(batch_id)
    selected, loads = _workers(request.get("workers"), settings)
    for job in _selected_jobs(batch, request.get("scenes")):
        if job["state"] != "failed":
            raise BatchError(f"{job['scene']}: seule une scene echouee peut etre relancee.", 409)
        server = select_server(loads)
        worker = next(node["name"] for node in selected if node["url"].rstrip("/") == server)
        _change(batch_id, job["job_id"], state="retried", server=server, worker=worker,
                previous_server=job["server"], previous_prompt_id=job["prompt_id"],
                prompt_id=None, retries=job.get("retries", 0) + 1, finished_at=None)
        threading.Thread(target=_submit, args=(batch_id, job["job_id"], server), daemon=True).start()
    return detail(batch_id)


def effective(settings, probe):
    defaults = {key: director.DEFAULTS[key] for key in
                ("megapixels", "steps", "aspect_ratio", "sampler_name", "scheduler", "ref_image_size")}
    defaults.update({key: value for key, value in (settings.get("h3_api_defaults") or {}).items()
                     if key in defaults})
    return {"ok": True, "h3_director": defaults, "style_preset": settings.get("h3_style_preset", ""),
            "style_prompt": settings.get("h3_style_prompt", ""),
            "style_line": settings.get("h3_style_line", ""),
            "copy_results": {"enabled": settings.get("api_copy_results_enabled", False),
                             "path": settings.get("results_copy_path", "")},
            "workers": [probe(node) for node in settings.get("comfy_nodes", [])]}
