"""Queue Q-Builder H3-Orbital scenes on remote ComfyUI workers.

All reference images travel through /upload/image. No local ComfyUI path is used.
The ledger keeps the exact workflow and upload names for later recovery.
"""

import argparse
import json
import time
import urllib.error
import uuid
from pathlib import Path

import h3_orbital_build as orbital
from network_cluster import initial_loads, parse_servers, select_server
from qbuilder_job_ledger import default_path, record, update
from queue_h3_multishot import queue_prompt
from queue_ltx_multishot import copy_prompt_outputs, upload_image_path, wait_prompt_result


def scene_paths(scene):
    return [scene["start_image"], *(scene.get("character_refs") or []),
            *(scene.get("set_refs") or []), *([scene["plan"]] if scene.get("plan") else [])]


def prepare(scene, server, cache):
    names = {}
    references = []
    for raw in dict.fromkeys(scene_paths(scene)):
        source = Path(raw)
        if not source.is_file():
            raise ValueError(f"Image Orbit introuvable sur le poste de commande: {source}")
        # Stable name permits recovery to upload exactly what the saved graph expects.
        filename = f"{uuid.uuid5(uuid.NAMESPACE_URL, str(source.resolve())).hex[:12]}_{source.name}"
        subfolder = "h3orbital/qbuilder"
        key = (server, str(source.resolve()))
        if key not in cache:
            cache[key] = upload_image_path(server, source, subfolder=subfolder,
                                           overwrite="true", filename=filename)
            print(f"  ref uploadee vers {server}: {source.name} -> {cache[key]}")
        names[raw] = cache[key]
        references.append({"source": str(source.resolve()), "subfolder": subfolder,
                           "filename": filename})
    return orbital.build_workflow(scene, names), references


def main():
    parser = argparse.ArgumentParser(description="Q-Builder MiniMax H3-Orbital remote queue")
    parser.add_argument("--scenes", default="h3_orbital_queue.json")
    parser.add_argument("--server", default="http://127.0.0.1:8188")
    parser.add_argument("--servers", default="")
    parser.add_argument("--copy-results", default="")
    parser.add_argument("--job-ledger", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    path = Path(args.scenes)
    if not path.is_absolute():
        path = root / path
    scenes = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(scenes, list) or not scenes:
        raise SystemExit("Le fichier Orbit doit contenir un tableau de scenes non vide.")
    for index, scene in enumerate(scenes, 1):
        errors = orbital.validate_scene(scene)
        for raw in scene_paths(scene):
            if not Path(raw).is_file():
                errors.append(f"image introuvable: {raw}")
        if errors:
            raise SystemExit(f"Scene {index}: {'; '.join(errors)}")
    if args.dry_run:
        for scene in scenes:
            wf = orbital.build_workflow(scene)
            print(f"{scene['id']}: {scene.get('mode', 'orbit')}, {scene.get('length', 73)} images, "
                  f"{len(scene.get('character_refs') or [])} refs personnage, "
                  f"{len(scene.get('set_refs') or [])} refs decor, {len(wf)} noeuds")
        return

    loads = initial_loads(parse_servers(args.server, args.servers))
    for server in list(loads):
        missing = orbital.preflight(server)
        if missing:
            print(f"  ! {server} exclu de la file Orbit: {', '.join(missing)}")
            del loads[server]
    if not loads:
        raise SystemExit("Aucun worker du cluster ne dispose de H3-Orbital et de ses modeles.")
    print("ComfyUI target(s): " + ", ".join(loads))
    cache = {}
    queued = []
    ledger_path = Path(args.job_ledger) if args.job_ledger else default_path(root)
    client_id = str(uuid.uuid4())
    for scene in scenes:
        server = select_server(loads)
        workflow, references = prepare(scene, server, cache)
        try:
            response = queue_prompt(server, workflow, client_id)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"{scene['id']}: worker {server} refuse le workflow: HTTP {exc.code}, "
                             f"{exc.read().decode('utf-8', errors='replace')[:1500]}") from exc
        prompt_id = response["prompt_id"]
        entry = record(ledger_path, {"kind": "h3_orbital", "name": scene["id"],
                                     "server": server, "prompt_id": prompt_id,
                                     "workflow": workflow, "references": references})
        queued.append({"scene": scene, "server": server, "prompt_id": prompt_id,
                       "ledger_id": entry["id"]})
        print(f"  -> {scene['id']} sur {server}: {prompt_id}")
        time.sleep(0.2)

    failures = 0
    for job in queued:
        ok, unused_entry, reason = wait_prompt_result(job["server"], job["prompt_id"])
        if not ok:
            alternatives = [server for server in loads if server != job["server"]]
            if alternatives:
                target = min(alternatives, key=loads.get)
                print(f"  ! {job['scene']['id']}: {reason}; reprise unique sur {target}")
                try:
                    workflow, references = prepare(job["scene"], target, cache)
                    response = queue_prompt(target, workflow, client_id)
                    update(ledger_path, job["ledger_id"], previous_server=job["server"],
                           previous_prompt_id=job["prompt_id"], server=target,
                           prompt_id=response["prompt_id"], workflow=workflow,
                           references=references, retries=1, state="queued")
                    job.update(server=target, prompt_id=response["prompt_id"])
                    loads[target] += 1
                    ok, unused_entry, reason = wait_prompt_result(target, job["prompt_id"])
                except (OSError, ValueError, urllib.error.URLError) as exc:
                    reason = str(exc)
        if not ok:
            failures += 1
            update(ledger_path, job["ledger_id"], state="failed", detail=reason)
            print(f"  ERREUR DEFINITIVE {job['scene']['id']}: {reason}")
            continue
        update(ledger_path, job["ledger_id"], state="finished", detail="termine")
        print(f"  OK {job['scene']['id']} sur {job['server']}")
        if args.copy_results:
            copy_prompt_outputs(job["server"], job["prompt_id"], args.copy_results)
    if failures:
        raise SystemExit(f"{failures} scene(s) Orbit en echec definitif.")


if __name__ == "__main__":
    main()
