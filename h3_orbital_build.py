"""H3-Orbital pour Workflows Q-builder : scene JSON -> workflow API ComfyUI, upload, envoi, copie.

Bibliotheque standard seulement. Utilisable comme module (build_workflow, run_scene) ou en ligne
de commande :

    python h3_orbital_build.py scene.json --dry-run                 # affiche le workflow construit
    python h3_orbital_build.py scene.json --server http://127.0.0.1:8188 --copy-results out/
    python h3_orbital_build.py a.json b.json c.json --servers http://worker-a.local:8188,http://worker-b.local:8188

Schema d'une scene : voir SCENE_KEYS ci-dessous et INTEGRATION.html.
"""

import argparse
import copy
import json
import mimetypes
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "h3_orbital_api.json"

# Noeud -> role. Ces IDs sont le contrat avec Q-builder : ne pas les renumeroter.
NODE_START = "1"
NODE_SCALE = "2"
NODE_LORA = "5"
NODE_COND = "8"
NODE_NOISE = "9"
NODE_SCHED = "11"
NODE_SAVE = "16"
CHARACTER_NODES = ["21", "22", "23", "24"]      # -> H3OrbitalConditioning.character_1..4
SET_NODES = ["31", "32", "33", "34"]            # -> H3OrbitalConditioning.set_1..4
PLAN_NODE = "40"                                # -> H3OrbitalConditioning.plan

VALID_LENGTHS = [17 * k + 5 for k in range(1, 21)]   # 22 ... 345 : 15 s max par rendu (OOM au-dela)
REQUIRED_NODES = ["H3OrbitalConditioning", "ImageFlip", "LoraLoaderModelOnly", "UNETLoader", "CLIPLoader", "VAELoader",
                  "ImageScaleToTotalPixels", "SamplerCustomAdvanced", "BasicGuider", "BasicScheduler",
                  "KSamplerSelect", "RandomNoise", "VAEDecode", "CreateVideo", "SaveVideo", "LoadImage"]
REQUIRED_FILES = {
    "UNETLoader": ("unet_name", "minimax_h3_fl2va_pruned_int8_convrot.safetensors"),
    "LoraLoaderModelOnly": ("lora_name", "minimax_h3_flf2v_lora_v1.safetensors"),
    "CLIPLoader": ("clip_name", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"),
    "VAELoader": ("vae_name", "minimax_h3_video_vae_fp16.safetensors"),
}

# Trajectoires en texte (mode path). « droite / gauche » = cote du personnage, qui regarde la camera
# au depart : son cote droit est a GAUCHE de l'image de depart.
PATH_PRESETS = {
    "half-right": ("The camera starts in front of the character and moves smoothly in a half circle around "
                   "the character's right side, always aimed at the character, ending directly behind, so the "
                   "last shot shows the character's back and the scenery beyond. The camera stops there and "
                   "does not come back."),
    "half-left": ("The camera starts in front of the character and moves smoothly in a half circle around "
                  "the character's left side, always aimed at the character, ending directly behind, so the "
                  "last shot shows the character's back and the scenery beyond. The camera stops there and "
                  "does not come back."),
    "quarter-push-right": ("The camera starts in front of the character and moves smoothly a quarter circle "
                           "around the character's right side while getting closer, always aimed at the "
                           "character, ending on a closer side view of the character's profile. The camera "
                           "stops there."),
    "quarter-push-left": ("The camera starts in front of the character and moves smoothly a quarter circle "
                          "around the character's left side while getting closer, always aimed at the "
                          "character, ending on a closer side view of the character's profile. The camera "
                          "stops there."),
}

SCENE_KEYS = {
    "id": "identifiant court, sert au nom de sortie (obligatoire)",
    "mode": "'orbit' (boucle 360, 1re = derniere image) ou 'path' (trajectoire libre, 1re image seule)",
    "start_image": "chemin local de la case de depart (obligatoire)",
    "character_refs": "liste de 0 a 4 chemins : vues du personnage (face, profil, dos, visage)",
    "set_refs": "liste de 0 a 4 chemins : vues du decor",
    "plan": "chemin du plan vu de dessus avec la trajectoire (mode path, optionnel)",
    "path_preset": "mode path : une cle de PATH_PRESETS (half-right, half-left, quarter-push-right...)",
    "prompt": "texte ajoute au prompt compose par le noeud (trajectoire libre, precisions)",
    "seed": "entier, defaut 904231",
    "length": "images a 24 i/s, 17k+5 : 73 (3 s) ou 124 (5,2 s) ; defaut 73",
    "megapixels": "taille de generation, en Mpx x 1024^2 : 0.58 (defaut) ; 0.985 = 1344x768 max H3",
    "lora_strength": "defaut 1.0 en orbit, 0.0 en path",
    "mirror": "true = trajectoire inversee (gauche/droite) : entrees retournees puis video remise a l'endroit",
    "steps": "defaut 28",
    "output_prefix": "defaut H3-Orbital/<id>",
}


def load_template():
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


def slug(value):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_") or "scene"


def validate_scene(scene):
    errors = []
    if not scene.get("id"):
        errors.append("id manquant")
    if not scene.get("start_image"):
        errors.append("start_image manquant")
    mode = scene.get("mode", "orbit")
    if mode not in ("orbit", "path"):
        errors.append("mode doit valoir 'orbit' ou 'path'")
    if len(scene.get("character_refs") or []) > 4:
        errors.append("4 vues du personnage au maximum")
    if len(scene.get("set_refs") or []) > 4:
        errors.append("4 vues du decor au maximum")
    if scene.get("plan") and mode != "path":
        errors.append("plan seulement en mode 'path'")
    if scene.get("path_preset") and scene["path_preset"] not in PATH_PRESETS:
        errors.append("path_preset inconnu : %s" % ", ".join(PATH_PRESETS))
    if scene.get("path_preset") and mode != "path":
        errors.append("path_preset seulement en mode 'path'")
    length = int(scene.get("length", 73))
    if length not in VALID_LENGTHS:
        errors.append("length doit valoir 17k+5 entre 22 et 345 (73 = 3 s, 124 = 5,2 s) ; recu %d" % length)
    return errors


def build_workflow(scene, remote_names=None):
    """Construit le workflow API d'une scene.

    remote_names : dict {chemin_local: nom_retourne_par_/upload/image}. Sans lui, les chemins
    de la scene sont mis tels quels (utile pour --dry-run ou un ComfyUI local).
    """
    errors = validate_scene(scene)
    if errors:
        raise ValueError("; ".join(errors))
    remote_names = remote_names or {}
    name = lambda p: remote_names.get(str(p), str(p)).replace("\\", "/")
    wf = copy.deepcopy(load_template())
    mode = scene.get("mode", "orbit")
    cond = wf[NODE_COND]["inputs"]

    wf[NODE_START]["inputs"]["image"] = name(scene["start_image"])
    wf[NODE_SCALE]["inputs"]["megapixels"] = float(scene.get("megapixels", 0.58))
    wf[NODE_LORA]["inputs"]["strength_model"] = float(scene.get("lora_strength", 1.0 if mode == "orbit" else 0.0))
    wf[NODE_NOISE]["inputs"]["noise_seed"] = int(scene.get("seed", 904231))
    wf[NODE_SCHED]["inputs"]["steps"] = int(scene.get("steps", 28))
    wf[NODE_SAVE]["inputs"]["filename_prefix"] = scene.get("output_prefix") or "H3-Orbital/" + slug(scene["id"])
    cond["mode"] = mode
    preset = PATH_PRESETS.get(scene.get("path_preset", ""), "")
    cond["prompt"] = " ".join(t for t in (preset, scene.get("prompt", "").strip()) if t)
    cond["length"] = int(scene.get("length", 73))
    if scene.get("mirror"):
        cond["mirror"] = True
        wf["17"] = {"class_type": "ImageFlip", "_meta": {"title": "Remise a l'endroit (miroir)"},
                    "inputs": {"image": ["14", 0], "flip_method": "y-axis: horizontally"}}
        wf["15"]["inputs"]["images"] = ["17", 0]

    def fill(nodes, key, paths):
        paths = list(paths or [])
        for i, node in enumerate(nodes):
            slot = "%s_%d" % (key, i + 1)
            if i < len(paths):
                wf[node]["inputs"]["image"] = name(paths[i])
                cond[slot] = [node, 0]
            else:                       # emplacement vide : on retire le LoadImage et l'entree
                del wf[node]
                cond.pop(slot, None)

    fill(CHARACTER_NODES, "character", scene.get("character_refs"))
    fill(SET_NODES, "set", scene.get("set_refs"))
    if scene.get("plan"):
        wf[PLAN_NODE]["inputs"]["image"] = name(scene["plan"])
        cond["plan"] = [PLAN_NODE, 0]
    else:
        del wf[PLAN_NODE]
        cond.pop("plan", None)
    return wf


def scene_images(scene):
    return [p for p in [scene["start_image"], *(scene.get("character_refs") or []), *(scene.get("set_refs") or []),
                        scene.get("plan")] if p]


# --- HTTP ----------------------------------------------------------------------------------------

def http_json(url, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def upload_image(server, path, subfolder):
    """POST /upload/image ; renvoie 'sous-dossier/nom' a mettre tel quel dans LoadImage.image."""
    path = Path(path)
    boundary = "----h3orbital-" + uuid.uuid4().hex
    mime = mimetypes.guess_type(path.name)[0] or "image/png"

    def part(name, value, ctype=None, filename=None):
        head = '--%s\r\nContent-Disposition: form-data; name="%s"' % (boundary, name)
        if filename:
            head += '; filename="%s"' % filename
        if ctype:
            head += "\r\nContent-Type: %s" % ctype
        body = value if isinstance(value, bytes) else str(value).encode()
        return head.encode() + b"\r\n\r\n" + body + b"\r\n"

    body = b"".join([part("image", path.read_bytes(), mime, path.name), part("type", "input"),
                     part("subfolder", subfolder), part("overwrite", "true"),
                     ("--%s--\r\n" % boundary).encode()])
    req = urllib.request.Request(server.rstrip("/") + "/upload/image", data=body, method="POST",
                                 headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read().decode("utf-8"))
    return "%s/%s" % (res["subfolder"], res["name"]) if res.get("subfolder") else res["name"]


def preflight(server):
    """Liste ce qui manque sur un worker pour ce workflow (vide = pret)."""
    missing = []
    try:
        version = http_json(server.rstrip("/") + "/system_stats", timeout=10)["system"].get("comfyui_version", "0")
    except (OSError, ValueError, KeyError) as exc:
        return ["worker injoignable (%s)" % exc]
    if tuple(int(x) for x in re.findall(r"\d+", version)[:3]) < (0, 33, 0):
        missing.append("ComfyUI %s < 0.33.0" % version)
    for node in REQUIRED_NODES:
        try:
            info = http_json("%s/object_info/%s" % (server.rstrip("/"), node), timeout=15)
        except (OSError, ValueError) as exc:
            return ["worker injoignable (%s)" % exc]
        if node not in info:
            missing.append("noeud %s" % node)
            continue
        if node in REQUIRED_FILES:
            field, fname = REQUIRED_FILES[node]
            options = info[node]["input"]["required"][field][0]
            if fname not in options:
                missing.append("fichier %s (%s)" % (fname, node))
    return missing


def wait_result(server, prompt_id, timeout=7200):
    deadline = time.time() + timeout
    while time.time() < deadline:
        entry = http_json("%s/history/%s" % (server.rstrip("/"), prompt_id)).get(prompt_id)
        if entry:
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                raise RuntimeError("rendu en erreur : %s" % json.dumps(msgs)[:1500])
            if status.get("completed"):
                return entry
        time.sleep(4)
    raise TimeoutError(prompt_id)


def outputs(entry):
    for node_output in (entry.get("outputs") or {}).values():
        for key in ("images", "videos", "gifs"):
            for item in node_output.get(key) or []:
                if item.get("filename"):
                    yield item


def run_scene(scene, server, subfolder="h3orbital/qbuilder", copy_to=None, log=print):
    """Upload des images, envoi, attente, copie optionnelle. Renvoie un dict de compte rendu."""
    t0 = time.time()
    remote = {}
    for p in scene_images(scene):
        if str(p) not in remote:
            remote[str(p)] = upload_image(server, p, subfolder)
            log("  upload %s -> %s" % (Path(p).name, remote[str(p)]))
    wf = build_workflow(scene, remote)
    resp = http_json(server.rstrip("/") + "/prompt", {"prompt": wf, "client_id": str(uuid.uuid4())})
    pid = resp["prompt_id"]
    log("  prompt_id %s sur %s" % (pid, server))
    entry = wait_result(server, pid)
    items = list(outputs(entry))
    saved = []
    if copy_to:
        dest = Path(copy_to)
        dest.mkdir(parents=True, exist_ok=True)
        for it in items:
            q = urllib.parse.urlencode({"filename": it["filename"], "subfolder": it.get("subfolder", ""),
                                        "type": it.get("type", "output")})
            target = dest / it["filename"]
            with urllib.request.urlopen("%s/view?%s" % (server.rstrip("/"), q), timeout=300) as r:
                target.write_bytes(r.read())
            saved.append(str(target))
            log("  copie -> %s" % target)
    return {"id": scene["id"], "server": server, "prompt_id": pid, "seconds": round(time.time() - t0, 1),
            "outputs": [("%s/%s" % (i.get("subfolder"), i["filename"])).lstrip("/") for i in items],
            "copied": saved}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenes", nargs="+", help="fichiers scene .json")
    ap.add_argument("--server", default="http://127.0.0.1:8188")
    ap.add_argument("--servers", default="", help="liste separee par des virgules : repartition des scenes")
    ap.add_argument("--subfolder", default="h3orbital/qbuilder", help="sous-dossier de ComfyUI/input")
    ap.add_argument("--copy-results", default="", help="dossier local ou copier les sorties")
    ap.add_argument("--dry-run", action="store_true", help="affiche le workflow sans rien envoyer")
    ap.add_argument("--preflight", action="store_true", help="verifie les workers et s'arrete")
    args = ap.parse_args()

    servers = [s.strip() for s in args.servers.split(",") if s.strip()] or [args.server]
    if args.preflight:
        for s in servers:
            miss = preflight(s)
            print(s, "PRET" if not miss else "MANQUE : " + ", ".join(miss))
        return
    scenes = []
    for f in args.scenes:
        sc = json.loads(Path(f).read_text(encoding="utf-8"))
        base = Path(f).resolve().parent     # chemins relatifs = relatifs au fichier scene
        for k in ("start_image", "plan"):
            if sc.get(k):
                sc[k] = str((base / sc[k]).resolve())
        for k in ("character_refs", "set_refs"):
            sc[k] = [str((base / p).resolve()) for p in sc.get(k) or []]
        scenes.append(sc)
    if args.dry_run:
        # noms tels que /upload/image les renverra (overwrite=true garde le nom du fichier)
        for sc in scenes:
            remote = {p: "%s/%s" % (args.subfolder, Path(p).name) for p in scene_images(sc)}
            print(json.dumps(build_workflow(sc, remote), indent=1, ensure_ascii=False))
        return
    ready = [s for s in servers if not preflight(s)]
    if not ready:
        sys.exit("aucun worker pret : lancer --preflight")
    report = []
    for i, sc in enumerate(scenes):
        server = ready[i % len(ready)]
        print("scene %s -> %s" % (sc["id"], server))
        report.append(run_scene(sc, server, args.subfolder, args.copy_results or None))
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
