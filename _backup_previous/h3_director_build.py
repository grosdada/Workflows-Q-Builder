#!/usr/bin/env python3
"""build.py — brief.json -> workflow ComfyUI prêt à RUN (Muse MiniMax H3 Director).

Usage:
    python3 build.py brief.json -o sortie.json          # construit le workflow
    python3 build.py brief.json -o sortie.json --preview # + aperçu du prompt compilé
    python3 build.py --schema                            # rappelle le schéma du brief

Avec settings.project_dir renseigné, characters[].file accepte un simple nom de
fichier : le script le préfixe selon link_mode et affiche la commande mklink /J à
passer une fois pour le projet.

Le script patche UNIQUEMENT les widgets du nœud MuseMinimaxDirector du template.
Le reste du graphe (loaders, seed hunt, upscale, VideoCombine) reste intact.
"""

import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Copie locale du template du skill muse-h3-director (assets/template_scout_v1.json).
# Le reste du fichier est repris tel quel du skill : on patche, on ne reecrit pas.
DEFAULT_TEMPLATE = os.path.join(HERE, "h3_template_scout_v1.json")

MODE_REFERENCE = "Reference (Omni) — up to 9 images, 3 videos, 3 audio"
MODE_FIRST_LAST = "First/Last Frame — zero, one, or two frame images"

# Index positionnels des widgets du nœud MuseMinimaxDirector (version seed-hunt).
W = {
    "mode": 0, "aspect_ratio": 1, "megapixels": 2, "multiple": 3, "resize_method": 4,
    "duration_seconds": 5, "chunk_duration_seconds": 6, "ref_image_size": 7,
    "hybrid_continuation": 8, "seed": 9, "seed_control": 10, "seed_hunt": 11,
    "steps": 12, "sampler_name": 13, "scheduler": 14, "shift_video": 15,
    "shift_audio": 16, "timeline_data": 17,
}

SAMPLERS = ["res_multistep", "euler", "euler_ancestral", "dpmpp_2m"]
SCHEDULERS = ["simple", "normal", "beta", "sgm_uniform"]
RESIZE = ["crop", "pad", "stretch"]
REF_SIZE = ["match", "max"]

# Valeurs EXACTES attendues par le widget COMBO aspect_ratio du nœud (comfy_extras/
# nodes_resolution.py AspectRatio enum) — une chaîne mal formée ("9:16" au lieu de
# "9:16 (Portrait Widescreen)") n'est détectée qu'au RUN, sous forme d'un ValueError
# Python dans _resolve_resolution : un cycle de génération complet gâché pour une
# faute de frappe. On valide ici, avec des alias courts en secours.
ASPECT_RATIOS = [
    "1:1 (Square)", "2:3 (Portrait Photo)", "3:2 (Photo)",
    "3:4 (Portrait Standard)", "4:3 (Standard)",
    "9:16 (Portrait Widescreen)", "16:9 (Widescreen)", "21:9 (Ultrawide)",
]
ASPECT_RATIO_ALIASES = {
    "1:1": "1:1 (Square)", "2:3": "2:3 (Portrait Photo)", "3:2": "3:2 (Photo)",
    "3:4": "3:4 (Portrait Standard)", "4:3": "4:3 (Standard)",
    "9:16": "9:16 (Portrait Widescreen)", "16:9": "16:9 (Widescreen)",
    "21:9": "21:9 (Ultrawide)",
}
# --- résolution des images de référence depuis un dossier projet -------------
# ComfyUI résout `file` avec _resolve_path() : basename cherché dans input/, puis
# input/musedirector/, puis input/muse/, et en dernier recours os.path.join(input, file).
# Sous Windows, ntpath.join("C:/ComfyUI/input", "Y:/projet/x.png") renvoie le chemin
# absolu tel quel — donc un chemin absolu FONCTIONNE au RUN. Mais la vignette de l'UI
# passe par /view?filename=<base>&subfolder=<dir>&type=input, qui refuse tout ce qui
# sort du dossier input : slots gris, aucune image visible. D'où le mode 'junction'
# par défaut, seul mode où le RUN et les vignettes marchent en même temps.
LINK_MODES = ["copy_project", "junction", "absolute", "copy"]
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

VISUAL_RETENTION = ["fully_preserved", "partially_preserved", "attribute_transfer", "weak_reference"]
AUDIO_RETENTION = ["reference", "fully_copy", "partially_copy", "weak_reference"]
VIDEO_ROLE = ["reference", "editing_source", "continuation_source"]
MAX_CHARACTERS = 9

DEFAULTS = {
    "mode": "reference",
    "aspect_ratio": "16:9 (Widescreen)",
    # 0.8 MP = 1216x672 en 16:9. Valeur demandée par David le 2026-08-09, après
    # 0.4 puis 0.6 : la passe de refine qui remontait la sortie à 0.9 MP étant
    # désactivée, c'est cette valeur seule qui détermine la qualité finale.
    # Compter ~1 min/step à 0.6 MP sur sa machine, donc ~1,3 min/step à 0.8 —
    # environ 25-30 min pour 20 steps. Descendre à 0.4 pour du repérage rapide.
    "megapixels": 0.8,
    "multiple": 32,
    "resize_method": "crop",
    "duration_seconds": None,          # None -> somme des durées de CUT
    "chunk_duration_seconds": 15.0,
    "ref_image_size": "match",
    "hybrid_continuation": False,
    "seed": 100,
    "seed_control": "fixed",
    # Désactivé par défaut (était True) : avec seed hunt ON, le Director remplit
    # les 4 slots candidate_1..4, mais le node MuseMinimaxRefine en aval doit
    # alors pointer manuellement sur le bon numéro de candidat — source réelle
    # d'un bug vécu (Refine laissé sur candidate=3 alors qu'un seul candidat
    # existait, plantage direct). Avec seed_hunt OFF, seul candidate_1 est
    # rempli et `build()` verrouille le Refine sur candidate=1 automatiquement
    # (voir plus bas) : plus de désaccord possible entre les deux nœuds.
    "seed_hunt": False,
    # Chaîne de post-traitement (RTX Video Super Resolution + MuseMinimaxRefine)
    # DÉSACTIVÉE par défaut. Deux bugs vécus en conditions réelles :
    #   - MuseMinimaxRefine plante en `Windows fatal exception: access violation`
    #     (dans encode_token_weights, puis dans pack_latents) parce qu'il invoque
    #     manuellement des node classes ComfyUI hors du graphe (_execute_comfy_node).
    #     Reproduit à 0.9 MP ET à 0.6 MP : ce n'est pas un problème de résolution.
    #   - RTXVideoSuperResolution en amont du Refine reste actif même quand le
    #     Refine est bypassé (le bypass fait juste passer sa sortie au VideoCombine),
    #     et tente alors un upscale sur toute la séquence : `DefaultCPUAllocator:
    #     not enough memory: you tried to allocate 36831559680 bytes` sur 362 frames.
    # Sans cette chaîne, la sortie du Director part directement au VideoCombine
    # « Full » : c'est la résolution de `megapixels` qui fait la qualité finale.
    "refine": False,
    "steps": 20,
    "sampler_name": "res_multistep",
    "scheduler": "simple",
    "shift_video": 12.0,
    "shift_audio": 3.0,
    # --- dossier projet ------------------------------------------------------
    # project_dir = racine du projet, TOUJOURS fournie par David dans la demande.
    # Il change de projet en permanence : ne jamais reprendre celui d'une session
    # précédente, ne jamais le déduire du sujet du film, ne jamais l'inventer. Sans
    # chemin donné, demander avant de construire. Les images de ref sont attendues
    # dans <project_dir>/<refs_subdir>/ ; renseigné, project_dir permet d'écrire
    # juste le nom de fichier dans characters[].file, le script préfixe tout seul.
    "project_dir": None,
    # Préfixe de nom de fichier des VHS_VideoCombine. Indispensable dès qu'on
    # empile plusieurs workflows dans la file de ComfyUI : sans ça les 5 sorties
    # s'appellent toutes MCvideo_00001, MCvideo_00002… et plus rien ne dit quel
    # scénario a produit quoi. None -> "Muse/Video/<projet>" si project_dir est
    # renseigné, sinon le préfixe du template est laissé intact.
    "output_name": None,
    "project_name": None,        # None -> basename(project_dir)
    "refs_subdir": "refs",
    "link_mode": "copy_project", # copy_project | junction | absolute | copy
}

# Dossier `input` de ComfyUI — destination des copies en mode copy_project.
# Defaut neutre : le vrai chemin se met dans local_settings.json ou
# comfy_input.txt, qui ne sont pas versionnes (voir comfy_input_dir()
# dans queue_h3_multishot.py).
COMFY_INPUT = r"C:\ComfyUI\input"

SCHEMA_DOC = """
brief.json
{
  "settings": { ... voir DEFAULTS, tout est optionnel ...
                "project_dir": "<chemin donne par David>",  // jamais devine
                "refs_subdir": "refs",                      // images de ref dedans
                "link_mode": "copy_project"                 // copy_project|junction|absolute|copy
              },
  "style_line": "une ou deux phrases : photographie, lumière, palette, énergie caméra",
  "dialogue_language": "English",
  "overall_soundscape": "sons diégétiques / ambiance",   ("" ou "N/A" si rien)
  "non_diegetic_music": "score de fond",                  ("" ou "N/A" si rien)
  "characters": [                       # max 9, l'ordre = <Subject 1..N>
    {"file": "musedirector/x.png",      # "" = slot vide, à glisser dans l'UI
     "description": "the rugged man, with tousled dirty-blond hair, wearing ...",
     "retention": "fully_preserved"}
  ],
  "background": {"file": "...", "description": "...", "retention": "fully_preserved"},
  "ref_videos": [{"file": "...", "description": "", "role": "reference",
                  "retention": "weak_reference", "includeAudio": false,
                  "trimStartSec": 0, "trimEndSec": null}],
  "ref_audios": [{"file": "...", "description": "Subject 1's voice",
                  "retention": "reference", "trimStartSec": 0, "trimEndSec": null}],
  "cuts": [
    {"seconds": 2.7,
     "text": "<Subject 1> steps through a torn bulkhead, ... He says, \\"Hold back.\\"",
     "speakers": [1]}                   # numéros de Subject (1-based), [] si muet
  ]
}
"""


# ---------------------------------------------------------------- validation

class BriefError(Exception):
    pass


def _check(cond, msg):
    if not cond:
        raise BriefError(msg)


def _norm(p):
    """Chemin en slashes avant, sans slash final — ComfyUI accepte les deux sous
    Windows et les backslashes doivent sinon être échappés dans le JSON."""
    return (p or "").replace("\\", "/").rstrip("/")


def project_link(s):
    """Nom du dossier tel qu'il apparaîtra sous input/musedirector/, ou ''."""
    if not s.get("project_dir"):
        return ""
    return s.get("project_name") or os.path.basename(_norm(s["project_dir"]))


def resolve_ref(name, s):
    """Un nom de fichier nu -> le chemin que ComfyUI saura résoudre.

    Laisse passer tel quel : chaîne vide, chemin déjà absolu (lettre de lecteur ou
    slash initial), et tout chemin qui contient déjà un dossier (musedirector/x.png)
    — pour ne pas casser les briefs écrits avant l'arrivée de project_dir.
    """
    name = _norm(name)
    if not name:
        return ""
    if re.match(r"^[A-Za-z]:/", name) or name.startswith("/") or "/" in name:
        return name
    link = project_link(s)
    if not link:
        return name
    mode = s.get("link_mode", "copy_project")
    if mode == "absolute":
        return f"{_norm(s['project_dir'])}/{_norm(s.get('refs_subdir') or 'refs')}/{name}"
    if mode == "copy":
        return f"musedirector/{name}"
    # copy_project et junction produisent le même chemin ; seule diffère la façon
    # dont le dossier arrive sous input/ (copie via device_bash, ou lien NTFS).
    return f"musedirector/{link}/{name}"


def sync_command(s):
    """Le script de synchro refs -> input/musedirector/<projet>, à exécuter via
    device_bash SUR la machine de David (les deux dossiers doivent être connectés).
    Idempotent : un fichier déjà présent à la même taille n'est pas recopié."""
    link = project_link(s)
    if not link or s.get("link_mode") != "copy_project":
        return ""
    return (
        'S=$(ls -d /sessions/*/mnt/{refs_mount}/{sub} | head -1)\n'
        'D=$(ls -d /sessions/*/mnt/input | head -1)/musedirector/{link}\n'
        'mkdir -p "$D"\n'
        'for f in "$S"/*; do b=$(basename "$f");\n'
        '  if [ -f "$D/$b" ] && [ "$(stat -c%s "$f")" = "$(stat -c%s "$D/$b")" ];\n'
        '    then echo "SKIP $b"; else cp -p "$f" "$D/$b" && echo "COPIE $b"; fi\n'
        'done'
    ).format(refs_mount=link, sub=_norm(s.get("refs_subdir") or "refs"), link=link)


def setup_command(s):
    """La commande à passer une fois par projet, ou '' si rien à faire."""
    link = project_link(s)
    if not link or s.get("link_mode") != "junction":
        return ""
    target = _norm(s["project_dir"]) + "/" + _norm(s.get("refs_subdir") or "refs")
    return ('mklink /J "%COMFYUI%\\input\\musedirector\\{link}" "{target}"'
            .format(link=link, target=target.replace("/", "\\")))


def normalise(brief):
    """Valide le brief et renvoie (settings, timeline_data, warnings)."""
    warn = []
    s = dict(DEFAULTS)
    s.update(brief.get("settings") or {})

    _check(s["mode"] in ("reference", "flf"), "settings.mode doit être 'reference' ou 'flf'")
    s["aspect_ratio"] = ASPECT_RATIO_ALIASES.get(s["aspect_ratio"], s["aspect_ratio"])
    _check(s["aspect_ratio"] in ASPECT_RATIOS,
           f"aspect_ratio doit être parmi {ASPECT_RATIOS} (ou un alias court {sorted(ASPECT_RATIO_ALIASES)})")
    _check(s["sampler_name"] in SAMPLERS, f"sampler_name doit être parmi {SAMPLERS}")
    _check(s["scheduler"] in SCHEDULERS, f"scheduler doit être parmi {SCHEDULERS}")
    _check(s["resize_method"] in RESIZE, f"resize_method doit être parmi {RESIZE}")
    _check(s["ref_image_size"] in REF_SIZE, f"ref_image_size doit être parmi {REF_SIZE}")
    _check(s["link_mode"] in LINK_MODES, f"link_mode doit être parmi {LINK_MODES}")
    if s["project_dir"] and s["link_mode"] == "absolute":
        warn.append("link_mode='absolute' : le RUN fonctionnera mais les vignettes des slots "
                    "Ref resteront vides dans l'UI (/view refuse les chemins hors du dossier "
                    "input). Préférer 'junction' si David veut voir ses refs.")
    _check(0.1 <= float(s["megapixels"]) <= 4.0, "megapixels hors bornes (0.1 - 4.0)")
    _check(1 <= int(s["steps"]) <= 100, "steps hors bornes (1 - 100)")
    _check(3.0 <= float(s["chunk_duration_seconds"]) <= 15.0,
           "chunk_duration_seconds hors bornes (3 - 15)")

    cuts = brief.get("cuts") or []
    _check(cuts, "brief.cuts est vide — il faut au moins un CUT")
    for i, c in enumerate(cuts, 1):
        _check((c.get("text") or "").strip(), f"CUT {i} : texte vide")
        _check(float(c.get("seconds", 0)) > 0, f"CUT {i} : 'seconds' doit être > 0")

    total_cuts = round(sum(float(c["seconds"]) for c in cuts), 3)
    duration = s["duration_seconds"] or total_cuts
    duration = round(float(duration), 3)
    _check(1.0 <= duration <= 120.0, "duration_seconds hors bornes (1 - 120)")
    # PLAFOND DUR : au-delà d'un chunk, le second chunk OOM systématiquement sur la
    # machine de David (RTX 5090 32 Go). Le nœud recharge les deux VAE + le text
    # encoder + le modèle de diffusion sans rien décharger ("0 models unloaded"),
    # atteint ~30.1 GiB sur 31.84 et meurt avant le premier step. Reproduit 4 fois
    # le 2026-08-12, ~22 min perdues à chaque coup. Le widget accepte 120 s, la
    # machine non. Découper en plusieurs workflows de <= 15 s.
    _check(duration <= float(s["chunk_duration_seconds"]),
           f"duration_seconds ({duration:g}s) > chunk_duration_seconds "
           f"({float(s['chunk_duration_seconds']):g}s) : le multi-chunk fait OOM sur la "
           f"machine de David. Un rendu H3 = 15 s MAXIMUM. Decoupe en plusieurs "
           f"workflows (_a / _b ...). Voir SKILL.md > Plafond de duree.")
    if abs(duration - total_cuts) > 0.05:
        warn.append(f"duration_seconds ({duration}s) != somme des CUTs ({total_cuts}s) — "
                    "les durées affichées seront re-proportionnées.")
    s["duration_seconds"] = duration

    chars = brief.get("characters") or []
    _check(len(chars) <= MAX_CHARACTERS, f"max {MAX_CHARACTERS} personnages")
    any_missing_file = False
    for i, ch in enumerate(chars, 1):
        r = ch.get("retention") or "fully_preserved"
        _check(r in VISUAL_RETENTION, f"personnage {i} : retention '{r}' invalide")
        if not (ch.get("file") or "").strip():
            any_missing_file = True
            warn.append(f"Ref {i} sans image : glisse le PNG dans le slot Ref {i} avant de RUN "
                        "(sinon <Subject %d> n'existera pas et la numérotation glisse)." % i)
        elif any_missing_file:
            # BUG réel trouvé ici : le nœud n'assigne un <Subject N> qu'aux
            # personnages qui ONT un `file` (_build_character_subjects fait
            # `continue` sur les slots vides) — donc un Ref SANS image suivi
            # plus loin d'un Ref AVEC image décale silencieusement la
            # numérotation des Subject de tous ceux qui suivent, jusqu'au RUN.
            # Cas normal (documenté) : aucune image connue encore, on écrit le
            # brief avec toutes les descriptions et David glisse les PNG dans
            # l'ordre avant de lancer — dans ce cas il n'y a pas de "trou" (pas
            # d'image DU TOUT), donc pas de risque, juste le warning ci-dessus.
            # Cas dangereux : un Ref plus loin DANS CE BRIEF a déjà un fichier
            # alors qu'un Ref plus tôt n'en a pas — signalé explicitement ici.
            warn.append(f"Ref {i} a une image mais un Ref antérieur n'en a pas : si ce Ref "
                        f"antérieur n'est pas rempli avant de lancer, Ref {i} ne sera PAS "
                        f"<Subject {i}> mais un numéro plus petit (la numérotation décale).")

    # Cohérence des tags <Subject N> et des speakers. On valide contre le compte
    # BRUT (`len(chars)`) : au moment d'écrire le brief on ne sait souvent pas
    # encore quelles images seront réellement posées (voir SKILL.md, workflow
    # normal "on ne connaît pas les fichiers"). Le vrai garde-fou est le warning
    # de trou ci-dessus, pas un blocage ici — sinon on ne pourrait jamais écrire
    # de CUTs référençant des personnages avant d'avoir les fichiers.
    max_subj = len(chars)
    for i, c in enumerate(cuts, 1):
        for n in {int(m) for m in re.findall(r"<Subject (\d+)>", c["text"])}:
            if n > max_subj:
                raise BriefError(f"CUT {i} cite <Subject {n}> mais seuls {max_subj} personnages "
                                 "sont définis.")
        if '"' in c["text"] and not c.get("speakers"):
            warn.append(f"CUT {i} contient du dialogue mais aucun 'speakers' — la voix ne sera "
                        "attribuée à personne.")
        for sp in c.get("speakers") or []:
            if not (1 <= int(sp) <= max_subj):
                raise BriefError(f"CUT {i} : speaker {sp} hors des {max_subj} personnages.")

    if s["hybrid_continuation"] and duration <= s["chunk_duration_seconds"]:
        warn.append("hybrid_continuation activé sur un seul chunk : sans effet.")

    # -------------------------------------------------- timeline_data
    def img_entry(e, default_ret="fully_preserved"):
        if not e:
            return None
        f = resolve_ref(e.get("file"), s)
        return {
            "file": f,
            "fileName": os.path.basename(f.replace("\\", "/")),
            "description": (e.get("description") or "").strip(),
            "retention": e.get("retention") or default_ret,
        }

    segments = []
    for c in cuts:
        secs = float(c["seconds"])
        hint = round(secs / total_cuts * duration, 1) if total_cuts else secs
        segments.append({
            "prompt": c["text"].strip(),
            "weight": secs,
            # toFixed(1) côté JS (widget-only, jamais lu par le compilateur Python,
            # mais on garde le même format pour que le libellé affiché dans l'UI du
            # timeline soit cohérent, ex. "2.5" pas "2.5000000000000004").
            "duration_hint": f"{hint:.1f}",
            # `speakerCharIdxs` attend l'index BRUT (0-based) dans `characters[]`.
            # Correct tant que les Ref sont remplies dans l'ordre du brief, sans
            # trou (voir le warning "Ref i a une image mais un Ref antérieur
            # n'en a pas" plus haut, qui signale explicitement quand ce n'est
            # pas garanti).
            "speakerCharIdxs": [int(x) - 1 for x in (c.get("speakers") or [])],
        })

    ref_videos = []
    for v in (brief.get("ref_videos") or [])[:3]:
        e = img_entry(v, "weak_reference")
        e.update({
            "role": v.get("role") or "reference",
            "includeAudio": bool(v.get("includeAudio")),
            "trimStartSec": v.get("trimStartSec", 0),
            "trimEndSec": v.get("trimEndSec"),
            "sourceDurationSec": None,
        })
        _check(e["role"] in VIDEO_ROLE, f"ref_video role '{e['role']}' invalide")
        ref_videos.append(e)

    ref_audios = []
    for a in (brief.get("ref_audios") or [])[:3]:
        e = img_entry(a, "reference")
        e["retention"] = a.get("retention") or "reference"
        _check(e["retention"] in AUDIO_RETENTION, f"ref_audio retention invalide")
        e.update({"trimStartSec": a.get("trimStartSec", 0),
                  "trimEndSec": a.get("trimEndSec"), "sourceDurationSec": None})
        ref_audios.append(e)

    tdata = {
        "characters": [img_entry(c) for c in chars],
        "segments": segments,
        "style_line": (brief.get("style_line") or "").strip(),
        "background": img_entry(brief.get("background")),
        "refVideos": ref_videos + [None] * (3 - len(ref_videos)),
        "refAudios": ref_audios + [None] * (3 - len(ref_audios)),
        "overall_soundscape": (brief.get("overall_soundscape") or "").strip(),
        "non_diegetic_music": (brief.get("non_diegetic_music") or "").strip(),
        "dialogue_language": brief.get("dialogue_language") or "English",
        "analyze_provider": brief.get("analyze_provider") or "gemini",
    }
    return s, tdata, warn


# ---------------------------------------------------------------- build

def build(brief, template_path):
    s, tdata, warn = normalise(brief)
    with open(template_path, encoding="utf-8") as fh:
        wf = json.load(fh)

    node = next((n for n in wf["nodes"] if n["type"] == "MuseMinimaxDirector"), None)
    _check(node is not None, "Aucun nœud MuseMinimaxDirector dans le template.")
    wv = node["widgets_values"]
    _check(len(wv) > W["timeline_data"],
           "Le template a moins de widgets que prévu — la version du nœud a changé, "
           "revérifier la table W avant de patcher.")

    wv[W["mode"]] = MODE_REFERENCE if s["mode"] == "reference" else MODE_FIRST_LAST
    for key in ("aspect_ratio", "multiple", "resize_method", "ref_image_size",
                "hybrid_continuation", "seed", "seed_control", "seed_hunt",
                "steps", "sampler_name", "scheduler"):
        wv[W[key]] = s[key]
    for key in ("megapixels", "duration_seconds", "chunk_duration_seconds",
                "shift_video", "shift_audio"):
        wv[W[key]] = float(s[key])
    wv[W["timeline_data"]] = json.dumps(tdata, ensure_ascii=False)

    # Verrouille le node MuseMinimaxRefine en aval sur candidate=1. Avec
    # seed_hunt=False (le nouveau défaut), seul candidate_1_images/audio est
    # jamais rempli par le Director — si le Refine reste réglé sur un autre
    # numéro (résidu d'un essai seed-hunt précédent), il reçoit un slot vide et
    # plante (`torch.cat(): expected a non-empty list of Tensors`, vécu en
    # conditions réelles). Effet inverse si settings.seed_hunt=True est demandé
    # explicitement : on laisse le Refine tel quel dans le template, David doit
    # alors choisir lui-même le meilleur des 4 candidats après coup.
    if not s["seed_hunt"]:
        refine = next((n for n in wf["nodes"] if n["type"] == "MuseMinimaxRefine"), None)
        if refine is not None and len(refine["widgets_values"]) > 1:
            refine["widgets_values"][1] = 1
        elif refine is not None:
            warn.append("MuseMinimaxRefine trouvé mais widgets_values trop court pour verrouiller "
                        "candidate=1 — vérifier le node à la main avant de RUN.")

    # --- nom des fichiers de sortie -----------------------------------------
    # Appliqué aux 6 VHS_VideoCombine (Full + les 4 candidats seed hunt), pour
    # que 5 workflows empilés dans la file ComfyUI ne produisent pas 5 fichiers
    # indiscernables.
    prefix = s.get("output_name")
    if not prefix and project_link(s):
        prefix = f"Muse/Video/{project_link(s)}"
    if prefix:
        prefix = _norm(prefix)
        if "/" not in prefix:
            prefix = f"Muse/Video/{prefix}"
        n_out = 0
        for n in wf["nodes"]:
            if n["type"] == "VHS_VideoCombine" and isinstance(n.get("widgets_values"), dict):
                n["widgets_values"]["filename_prefix"] = prefix
                n_out += 1
        if n_out == 0:
            warn.append("Aucun VHS_VideoCombine patché — les sorties garderont le préfixe "
                        "du template, illisible en lot.")

    # --- chaîne de post-traitement (voir DEFAULTS["refine"]) -----------------
    # Le template livre déjà 179/180/187/188/189 en mode 4, mais on force l'état
    # explicitement : un template réenregistré depuis l'UI de David pourrait les
    # avoir réactivés sans qu'on s'en rende compte, et le bug reviendrait en
    # silence. Les deux nœuds DOIVENT être bypassés ensemble : bypasser le seul
    # Refine laisse le RTX en amont s'exécuter quand même (vécu : OOM CPU 36 Go).
    by_id = {n["id"]: n for n in wf["nodes"]}
    main_chain = [179, 180]                 # RTX Super Resolution 1 + MuseMinimaxRefine
    hunt_chain = [187, 188, 189]            # RTX Super Resolution 2/3/4 (candidats seed hunt)
    refine_mode = 0 if s["refine"] else 4
    hunt_mode = 0 if (s["refine"] and s["seed_hunt"]) else 4
    for nid, mode in [(i, refine_mode) for i in main_chain] + [(i, hunt_mode) for i in hunt_chain]:
        n = by_id.get(nid)
        if n is None or n["type"] not in ("RTXVideoSuperResolution", "MuseMinimaxRefine"):
            warn.append(f"Nœud {nid} absent ou d'un type inattendu — chaîne de refine non "
                        f"normalisée, vérifier les bypass à la main avant de RUN.")
            continue
        n["mode"] = mode
    if s["refine"]:
        warn.append("settings.refine=True : MuseMinimaxRefine est réactivé. Ce nœud a planté en "
                    "`access violation` sur cette machine à 0.9 MP comme à 0.6 MP — ne l'activer "
                    "que si le bug a été corrigé en amont, et prévenir David explicitement.")

    return wf, s, tdata, warn


# ---------------------------------------------------------------- preview

_DIALOGUE_RE = re.compile(r'"([^"]*)"')


def _wrap_dialogue(text, lang):
    def sub(m):
        inner = re.sub(r"[~]{2,}|[*_#]+", "", m.group(1)).strip().rstrip(",").strip()
        if inner and inner[-1] not in ".!?":
            inner += "."
        return f"<d>[{lang}] {inner}</d>"
    return _DIALOGUE_RE.sub(sub, text)


def _ts(sec):
    m = int(sec // 60)
    return f"{m:02d}:{sec - m * 60:06.3f}"


def preview(s, tdata):
    """Réplique allégée du compilateur six sections (mode Reference, sans chunking
    fin ni carry-over) — sert à relire le prompt AVANT de lancer ComfyUI."""
    lang = tdata["dialogue_language"]
    subs, ret = [], []
    idx_to_subj = {}
    n = 0
    for i, ch in enumerate(tdata["characters"]):
        if not ch or not ch["file"]:
            continue
        n += 1
        idx_to_subj[i] = n
        d = ch["description"]
        subs.append(f"<Subject {n}> is {d} (from `<Picture {n}>`)." if d
                    else f"<Subject {n}> is the subject shown in `<Picture {n}>`.")
        ret.append((n, n, ch["retention"]))
    bg = tdata.get("background")
    if bg and bg["file"]:
        n += 1
        subs.append(f"<Subject {n}> is {bg['description'] or 'the setting'} (from `<Picture {n}>`).")
        ret.append((n, n, bg["retention"]))

    total_w = sum(float(x["weight"]) for x in tdata["segments"]) or 1.0
    dur = s["duration_seconds"]
    shots, cursor = [], 0.0
    for k, seg in enumerate(tdata["segments"], 1):
        text = seg["prompt"]
        sp = seg["speakerCharIdxs"]
        tagged = False
        for j, ci in enumerate(sp, 1):
            tag = f"<Subject {idx_to_subj.get(ci, '?')}>"
            if tag in text:
                text = text.replace(tag, f"{tag} (S{j})")
                tagged = True
        if sp and not tagged and len(sp) == 1:
            sn = idx_to_subj.get(sp[0])
            text = f"<Subject {sn}> (S1) continues: {text}"
        text = _wrap_dialogue(text, lang)
        shots.append(f"[Shot 1] {text}" if k == 1 else f"[Shot {k}] At {_ts(cursor)}, {text}")
        cursor += float(seg["weight"]) / total_w * dur

    tags = [f"<Subject {i}>" for i in range(1, n + 1)]
    summary = ("The target video shows " + (", ".join(tags[:-1]) + " and " + tags[-1]
               if len(tags) > 1 else tags[0]) + ".") if tags else \
              "The target video follows the shot description below."
    # Présence par sujet : le nœud scanne les tags <Subject N> LITTÉRAUX de chaque
    # CUT et n'écrit "(present throughout)" que si le tag apparaît dans tous les
    # shots. Répliqué ici à l'identique — sinon l'aperçu annonce un personnage
    # présent partout alors que le prompt réel dira "(appears in [Shot 3])", et on
    # relit un texte qui n'est pas celui qui sera envoyé.
    total_shots = sum(1 for seg in tdata["segments"] if (seg.get("prompt") or "").strip())
    appearances = {}
    k = 0
    for seg in tdata["segments"]:
        txt = (seg.get("prompt") or "").strip()
        if not txt:
            continue
        k += 1
        for m in set(re.findall(r"<Subject (\d+)>", txt)):
            appearances.setdefault(int(m), []).append(k)

    def presence(n):
        shots_n = appearances.get(n, [])
        if not shots_n:
            return "(referenced in this shot)"
        if total_shots and len(shots_n) >= total_shots:
            return "(present throughout)"
        return "(appears in " + ", ".join(f"[Shot {x}]" for x in shots_n) + ")"

    ret_lines = [f"<Subject {a}> {presence(a)}: {r} - matches `<Picture {b}>`."
                 for a, b, r in ret]
    parts = []
    if subs:
        parts.append("subject_definitions:\n" + "\n".join(subs))
    parts.append("summary:\n[reference generation] " + summary)
    if ret_lines:
        parts.append("retention_analysis:\n" + "\n".join(ret_lines))
    desc = ([tdata["style_line"]] if tdata["style_line"] else []) + shots
    parts.append("detailed_description:\n" + "\n".join(desc))
    parts.append("overall_soundscape:\n" + (tdata["overall_soundscape"] or "N/A"))
    parts.append("non_diegetic_music:\n" + (tdata["non_diegetic_music"] or "N/A"))
    nb = max(1, math.ceil(dur / s["chunk_duration_seconds"]))
    head = f"--- Aperçu ({dur:g}s, ~{nb} chunk(s) de {s['chunk_duration_seconds']:g}s) ---"
    return head + "\n" + "\n\n".join(parts)


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("brief", nargs="?")
    ap.add_argument("-o", "--out")
    ap.add_argument("-t", "--template", default=DEFAULT_TEMPLATE)
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--schema", action="store_true")
    a = ap.parse_args()

    if a.schema or not a.brief:
        print(SCHEMA_DOC)
        return 0
    with open(a.brief, encoding="utf-8") as fh:
        brief = json.load(fh)
    try:
        wf, s, tdata, warn = build(brief, a.template)
    except BriefError as e:
        print(f"ERREUR brief : {e}", file=sys.stderr)
        return 1

    out = a.out or os.path.splitext(a.brief)[0] + "_workflow.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(wf, fh, ensure_ascii=False, indent=1)
    print(f"OK -> {out}  ({s['duration_seconds']:g}s, {len(tdata['segments'])} CUT, "
          f"{sum(1 for c in tdata['characters'] if c and c['file'])} image(s) de ref)")
    for w in warn:
        print("  ! " + w)
    cmd = setup_command(s)
    if cmd:
        print("\n  Jonction à créer une fois pour ce projet (invite de commandes admin) :\n"
              "  " + cmd)
    sync = sync_command(s)
    if sync:
        print("\n  Synchro à passer via device_bash AVANT de livrer le workflow :\n"
              + "\n".join("  " + l for l in sync.splitlines()))
    if cmd or sync:
        for c in tdata["characters"] + [tdata.get("background")]:
            if c and c["file"]:
                print("    ref -> " + c["file"])
    if a.preview:
        print("\n" + preview(s, tdata))
    return 0


if __name__ == "__main__":
    sys.exit(main())
