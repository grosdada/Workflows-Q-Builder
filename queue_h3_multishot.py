"""queue_h3_multishot.py — une file de scenes MiniMax H3 -> N workflows ComfyUI.

Pendant H3 de queue_ltx_multishot.py, pour Workflows Q-builder.

Deux modes, parce que H3 et LTX ne se pilotent pas pareil :

  1. EMIT (defaut) : chaque scene du fichier de briefs produit un fichier
     .json au format UI, patche depuis h3_template_scout_v1.json via
     h3_director_build.build(). David glisse les fichiers dans ComfyUI
     (chaque depot ouvre un nouvel onglet) et appuie sur Run : la file de
     ComfyUI fait le reste. C'est la voie documentee par le skill.

  2. QUEUE (--queue) : demande un template au format API
     (ComfyUI > Workflow > Export (API)). Les valeurs du noeud
     MuseMinimaxDirector sont patchees puis POSTees sur /prompt, comme le
     fait queue_ltx_multishot.py pour LTX. L'export API fige les bypass :
     il doit etre fait avec la chaine RTX + Refine deja bypassee.

Le fichier de briefs est un tableau JSON d'objets brief au schema du skill
muse-h3-director (python h3_director_build.py --schema).

Rappel dur, repris du skill : un rendu H3 = 15 s maximum. Au-dela, le
second chunk fait OOM sur la machine de David. h3_director_build refuse de
construire au-dela, ce script ne contourne rien.
"""

import argparse
import copy
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# Le Python embarque de ComfyUI est livre avec un fichier python311._pth qui
# fixe les chemins de recherche et, contrairement a un Python normal, n'ajoute
# PAS le dossier du script. Sans cette ligne, l'import ci-dessous echoue en
# ModuleNotFoundError alors que le fichier est juste a cote — vecu sur un poste
# sans Python installe, qui utilise donc l'interpreteur de ComfyUI.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h3_director_build as director  # noqa: E402


DIRECTOR_CLASS = "MuseMinimaxDirector"


def resolve_path(root, raw_path):
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else root / path


def is_ui_workflow(data):
    return isinstance(data, dict) and isinstance(data.get("nodes"), list) and isinstance(data.get("links"), list)


def is_api_workflow(data):
    return isinstance(data, dict) and any(
        isinstance(node, dict) and "class_type" in node for node in data.values()
    )


def safe_slug(value, fallback):
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip()).strip("_")
    return slug or fallback


def patch_api_workflow(workflow, settings, tdata, prefix):
    """Patche un workflow H3 au format API (Export (API)) : le noeud Director
    y expose ses widgets comme des cles d'inputs nommees, pas une liste
    positionnelle. Renvoie la liste des avertissements."""
    warn = []
    directors = [
        (node_id, node)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and node.get("class_type") == DIRECTOR_CLASS
    ]
    if not directors:
        raise SystemExit(
            f"Aucun noeud {DIRECTOR_CLASS} dans le workflow API. "
            "Refais un Export (API) depuis le workflow H3 qui tourne."
        )
    if len(directors) > 1:
        warn.append(f"{len(directors)} noeuds {DIRECTOR_CLASS} trouves : tous patches a l'identique.")

    mode_value = director.MODE_REFERENCE if settings["mode"] == "reference" else director.MODE_FIRST_LAST
    scalars = {
        "mode": mode_value,
        "aspect_ratio": settings["aspect_ratio"],
        "multiple": settings["multiple"],
        "resize_method": settings["resize_method"],
        "ref_image_size": settings["ref_image_size"],
        "hybrid_continuation": settings["hybrid_continuation"],
        "seed": settings["seed"],
        # Pas de seed_control ici : c'est le widget UI control_after_generate,
        # absent de l'Export (API) (verifie sur h3_director_api.json).
        "seed_hunt": settings["seed_hunt"],
        "steps": settings["steps"],
        "sampler_name": settings["sampler_name"],
        "scheduler": settings["scheduler"],
        "megapixels": float(settings["megapixels"]),
        "duration_seconds": float(settings["duration_seconds"]),
        "chunk_duration_seconds": float(settings["chunk_duration_seconds"]),
        "shift_video": float(settings["shift_video"]),
        "shift_audio": float(settings["shift_audio"]),
        "timeline_data": json.dumps(tdata, ensure_ascii=False),
    }

    for _node_id, node in directors:
        inputs = node.setdefault("inputs", {})
        # Constate l'etat AVANT de patcher : apres, toutes les cles existent
        # forcement puisqu'on les ecrit.
        unknown = [key for key in scalars if key not in inputs]
        if unknown:
            warn.append(f"Cles absentes du noeud {DIRECTOR_CLASS} ({', '.join(unknown)}) : "
                        "version du noeud differente, refaire l'Export (API).")
        for key, value in scalars.items():
            # Une entree branchee sur un autre noeud est une liste [node_id, slot] :
            # l'ecraser casserait le graphe.
            if key in inputs and isinstance(inputs[key], list):
                warn.append(f"'{key}' est cable a un autre noeud dans l'export API : laisse tel quel.")
                continue
            inputs[key] = value

    if prefix:
        patched = 0
        for node in workflow.values():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                continue
            if node.get("class_type") in ("VHS_VideoCombine", "SaveVideo", "SaveAnimatedWEBP"):
                if not isinstance(inputs.get("filename_prefix"), list):
                    inputs["filename_prefix"] = prefix
                    patched += 1
        if patched == 0:
            warn.append("Aucun noeud de sortie patche : les rendus garderont le prefixe du template.")
    return warn


def comfy_input_dir():
    """Dossier input de ComfyUI. Le skill le fige sur la machine de David ;
    ici un fichier comfy_input.txt pose a cote du script prend le dessus, pour
    que le dossier distribuable marche sur un autre poste."""
    here = Path(__file__).resolve().parent
    override = here / "comfy_input.txt"
    if override.exists():
        value = override.read_text(encoding="utf-8-sig").strip()
        if value:
            return value
    settings = here / "local_settings.json"
    if settings.exists():
        try:
            value = json.loads(settings.read_text(encoding="utf-8-sig")).get("comfy_input")
        except (OSError, json.JSONDecodeError):
            value = None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return director.COMFY_INPUT


def local_sync_command(settings):
    """Copie des refs du projet vers ComfyUI/input/musedirector/<projet>.

    Le sync_command du skill vise le bac a sable d'une session Claude
    (/sessions/*/mnt/...) : inutilisable ici, puisque l'app tourne deja sur la
    machine ou sont les deux dossiers. On sort donc la commande PowerShell
    equivalente."""
    link = director.project_link(settings)
    if not link or settings.get("link_mode") != "copy_project" or not settings.get("project_dir"):
        return ""
    source = Path(settings["project_dir"]) / (settings.get("refs_subdir") or "refs")
    # Le champ Projet accepte un simple nom depuis que l'app depose elle-meme
    # les images dans ComfyUI. Sans dossier source reel, il n'y a rien a
    # synchroniser et afficher la commande n'aurait aucun sens.
    if not source.is_dir():
        return ""
    target = Path(comfy_input_dir()) / "musedirector" / link
    return (
        f'New-Item -ItemType Directory -Force "{target}" | Out-Null\n'
        f'Copy-Item "{source}\\*" "{target}" -Force'
    )


def describe_comfy_rejection(exc):
    """Rend lisible un refus de ComfyUI (HTTP 400 sur /prompt).

    ComfyUI renvoie dans le corps de la reponse la raison exacte : noeud
    manquant, modele absent, valeur hors bornes. La jeter pour n'afficher
    qu'un code HTTP laissait sans piste — et le message parlait de serveur
    injoignable alors que le serveur avait parfaitement repondu.
    """
    raw = ""
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        pass

    lines = [f"ComfyUI a refuse le workflow (HTTP {exc.code})."]
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        if raw.strip():
            lines.append(raw.strip()[:800])
        return "\n".join(lines)

    message = data.get("error")
    if isinstance(message, dict):
        detail = message.get("message") or message.get("type") or ""
        extra = message.get("details") or ""
        lines.append(f"  {detail} {extra}".rstrip())
    elif message:
        lines.append(f"  {message}")

    for node_id, node in (data.get("node_errors") or {}).items():
        node_type = node.get("class_type") or node.get("type") or ""
        lines.append(f"  noeud {node_id} ({node_type}) :")
        for error in node.get("errors") or []:
            detail = error.get("message", "")
            extra = error.get("details", "")
            lines.append(f"    - {detail} {extra}".rstrip())

    if len(lines) == 1 and raw.strip():
        lines.append(raw.strip()[:800])
    lines.append("Verifie que ComfyUI a bien les noeuds et les modeles de ce workflow, "
                 "et qu'il tourne la version attendue.")
    return "\n".join(lines)


def queue_prompt(server, workflow, client_id):
    payload = json.dumps({"client_id": client_id, "prompt": workflow}).encode("utf-8")
    request = urllib.request.Request(
        f"{server.rstrip('/')}/prompt",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Build (and optionally queue) MiniMax H3 ComfyUI workflows.")
    parser.add_argument("--briefs", default="h3_custom_queue.json")
    parser.add_argument("--template", default="h3_template_scout_v1.json")
    parser.add_argument("--out-dir", default="h3_workflows")
    parser.add_argument("--server", default="http://127.0.0.1:8188")
    parser.add_argument("--queue", action="store_true",
                        help="POST les workflows sur ComfyUI. Demande un template au format Export (API).")
    parser.add_argument("--preview", action="store_true", help="Affiche le prompt six sections compile.")
    parser.add_argument("--dry-run", action="store_true", help="Valide et affiche, n'ecrit rien, n'envoie rien.")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    briefs_path = resolve_path(root, args.briefs)
    template_path = resolve_path(root, args.template)
    if not briefs_path.exists():
        raise SystemExit(f"Fichier de briefs introuvable: {briefs_path}")
    if not template_path.exists():
        raise SystemExit(f"Template introuvable: {template_path}")

    briefs = json.loads(briefs_path.read_text(encoding="utf-8-sig"))
    if isinstance(briefs, dict):
        briefs = [briefs]
    if not isinstance(briefs, list) or not briefs:
        raise SystemExit("Le fichier de briefs doit etre un tableau JSON non vide.")

    template_data = json.loads(template_path.read_text(encoding="utf-8-sig"))
    template_is_api = is_api_workflow(template_data) and not is_ui_workflow(template_data)
    if args.queue and not template_is_api:
        raise SystemExit(
            f"--queue demande un template au format API, or {template_path.name} est au format UI.\n"
            "Dans ComfyUI: ouvre le workflow H3 (chaine RTX + Refine deja bypassee), "
            "Workflow > Export (API), puis relance avec --template <ce fichier>.\n"
            "Sans export API, retire --queue: les .json produits se glissent dans ComfyUI."
        )
    if not args.queue and template_is_api:
        print("! Template au format API mais --queue absent: les fichiers produits seront au format API, "
              "non deposables dans l'UI ComfyUI.")

    out_dir = resolve_path(root, args.out_dir)
    if not args.dry_run and not args.queue:
        out_dir.mkdir(parents=True, exist_ok=True)

    client_id = str(uuid.uuid4())
    queued = []
    written = []
    failures = 0

    for index, brief in enumerate(briefs, start=1):
        name = safe_slug(brief.get("name") or (brief.get("settings") or {}).get("output_name"), f"scene_{index:02d}")
        label = f"{index:02d} {name}"
        try:
            if template_is_api:
                settings, tdata, warn = director.normalise(brief)
                workflow = copy.deepcopy(template_data)
                prefix = (brief.get("settings") or {}).get("output_name") or ""
                if not prefix and director.project_link(settings):
                    prefix = f"Muse/Video/{director.project_link(settings)}"
                warn = warn + patch_api_workflow(workflow, settings, tdata, director._norm(prefix) if prefix else "")
            else:
                workflow, settings, tdata, warn = director.build(brief, str(template_path))
        except director.BriefError as exc:
            failures += 1
            print(f"ERREUR {label}: {exc}")
            continue

        cuts = len(tdata["segments"])
        refs = sum(1 for c in tdata["characters"] if c and c["file"])
        print(f"{label}: {settings['duration_seconds']:g}s, {cuts} CUT, {refs} image(s) de ref")
        for message in warn:
            print(f"  ! {message}")

        if args.preview:
            print(director.preview(settings, tdata))
            print()

        if args.dry_run:
            continue

        if args.queue:
            try:
                result = queue_prompt(args.server, workflow, client_id)
            except urllib.error.HTTPError as exc:
                # Une reponse HTTP n'est pas une panne de connexion : le serveur
                # a repondu, et il explique pourquoi il refuse.
                raise SystemExit(f"{label} : {describe_comfy_rejection(exc)}") from exc
            except urllib.error.URLError as exc:
                raise SystemExit(
                    f"ComfyUI injoignable sur {args.server}: {exc}\n"
                    "Verifie qu'il tourne et que l'adresse est la bonne."
                ) from exc
            prompt_id = result.get("prompt_id", "unknown")
            queued.append((name, prompt_id))
            print(f"  -> file ComfyUI: {prompt_id}")
            time.sleep(0.2)
        else:
            target = out_dir / f"{index:02d}_{name}.json"
            target.write_text(json.dumps(workflow, ensure_ascii=False, indent=1), encoding="utf-8")
            written.append(target)
            print(f"  -> {target}")

    sync = local_sync_command(dict(director.DEFAULTS, **(briefs[0].get("settings") or {})))
    if sync:
        print("\nSi les refs ne sont pas encore sous input/musedirector, passe ceci une fois:")
        for line in sync.splitlines():
            print("  " + line)

    if written:
        print(f"\n{len(written)} workflow(s) ecrits dans {out_dir}")
        if template_is_api:
            print("Format API : ces fichiers ne se glissent PAS dans ComfyUI. Relance avec --queue pour "
                  "les envoyer dans la file, ou avec un template UI pour produire des fichiers deposables.")
        else:
            print("Glisse-les dans ComfyUI (chaque depot ouvre un onglet), puis Run (Ctrl+Entree) sur chacun.")
        print("Lance UN SEUL rendu en test avant d'empiler le lot: un job qui OOM ne bloque pas la file, "
              "les suivants partent et echouent pareil.")
    if queued:
        print("\nScenes en file:")
        for name, prompt_id in queued:
            print(f"- {name}: {prompt_id}")
    if failures:
        raise SystemExit(f"\n{failures} scene(s) rejetee(s), rien n'a ete produit pour celles-la.")


if __name__ == "__main__":
    main()
