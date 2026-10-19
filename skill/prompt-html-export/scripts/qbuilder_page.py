"""Fabrique une page HTML qbuilder-html/v3 (MiniMax H3 > Director) a partir d'une spec JSON, avec toutes les verifications.

    python qbuilder_page.py spec.json            -> ecrit la page indiquee par spec["out"]
    python qbuilder_page.py spec.json --check    -> verifie seulement, n'ecrit rien

Spec (voir assets/exemple_spec.json) :
{
  "out": "H3-Director_batch52_ma-serie-ep01-10.html",   (relatif a la spec, ou absolu)
  "title": "Ma serie 01-10", "intro": "texte libre affiche en tete de page",
  "aspect_ratio": "4:3", "duration": 15,
  "style_line": "...", "overall_soundscape": "...", "non_diegetic_music": "", "dialogue_language": "none",
  "assets": {
    "m091": {"path": "planches/091.jpg", "description": "a puffy-faced man ...", "retention": "fully_preserved"},
    "decor_bus": {"path": "decors/bus.jpg", "description": "the inside of a city bus ... the set of every shot"}
  },
  "scenes": [
    {"name": "52-01_l-arret-de-bus", "seed": 7061, "character_refs": ["m091"], "background_ref": "decor_bus",
     "cuts": [{"seconds": 3, "text": "Camera A, ... <Subject 1> ...", "speakers": []}, ...]}
  ]
}
Un asset accepte "path" (image locale) ou "data_url" deja faite. Les personnages ont "retention", le decor non.
"background_ref" est facultatif. Chaque image est mise au format de sortie SANS rognage (bandes de la couleur du coin
haut-gauche), reduite a 1600 px de large, en JPEG : le noeud Muse recadre ("crop") toute reference au format de sortie.

Necessite Pillow (pip install pillow).
"""
import base64, html, io, json, os, re, sys

from PIL import Image

INTERDITS = r"\b(costume|dolly|zoom|cinematic|film grain|35mm|operator|cameraman)\b"
NEGATIONS = r"\b(never|no one|nobody|nothing|without|does not|doesn't|don't|not|no)\b"


def au_format(im, ratio):
    w, h = im.size
    if abs(w / h - ratio) < 0.01:
        return im
    fond = im.getpixel((5, 5))
    if w / h > ratio:
        nh = round(w / ratio); c = Image.new("RGB", (w, nh), fond); c.paste(im, (0, (nh - h) // 2))
    else:
        nw = round(h * ratio); c = Image.new("RGB", (nw, h), fond); c.paste(im, ((nw - w) // 2, 0))
    return c


def data_url(path, ratio, max_w=1600):
    im = au_format(Image.open(path).convert("RGB"), ratio)
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=86)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def verifier(spec):
    """Renvoie la liste des erreurs (vide si tout va bien)."""
    err = []
    assets, scenes, duree = spec["assets"], spec["scenes"], spec.get("duration", 15)
    if not 1 <= len(scenes) <= 20:
        err.append(f"{len(scenes)} scenes : Q-builder en accepte 1 a 20")
    noms = [s["name"] for s in scenes]
    if len(set(noms)) != len(noms):
        err.append("noms de scenes en double")
    for sc in scenes:
        n, refs = sc["name"], sc.get("character_refs", [])
        if not re.fullmatch(r"[a-z0-9_-]+", n):
            err.append(f"{n} : nom a garder en minuscules, chiffres, - et _")
        tot = sum(c["seconds"] for c in sc["cuts"])
        if abs(tot - duree) > 1e-6:
            err.append(f"{n} : {tot:g} s au lieu de {duree} s (au-dela de 15 s le noeud enchaine un 2e chunk qui plante)")
        for r in refs + ([sc["background_ref"]] if sc.get("background_ref") else []):
            if r not in assets:
                err.append(f"{n} : asset inconnu {r}")
        if len(refs) + bool(sc.get("background_ref")) > 9:
            err.append(f"{n} : plus de 9 references")
        for k in range(1, len(refs) + 1):
            if not any(f"<Subject {k}>" in c["text"] for c in sc["cuts"]):
                err.append(f"{n} : <Subject {k}> jamais cite")
        for i, c in enumerate(sc["cuts"], 1):
            t, sp = c["text"], c.get("speakers", [])
            for m in re.findall(r"<Subject (\d+)>", t):
                if not 1 <= int(m) <= len(refs):
                    err.append(f"{n} CUT {i} : <Subject {m}> hors casting ({len(refs)} personnages)")
            if ('"' in t) != bool(sp):
                err.append(f"{n} CUT {i} : dialogue entre guillemets et speakers doivent aller ensemble")
            if any(not 1 <= s <= len(refs) for s in sp):
                err.append(f"{n} CUT {i} : speaker hors casting")
            if spec.get("dialogue_language", "none") == "none" and '"' in t:
                err.append(f"{n} CUT {i} : guillemets dans une page muette")
            for pat, quoi in ((INTERDITS, "mot banni"), (NEGATIONS, "negation (H3 ajoute ce qu'on nie)")):
                m = re.search(pat, t.lower())
                if m:
                    err.append(f"{n} CUT {i} : {quoi} « {m.group(0)} »")
    return err


def construire(spec, base):
    ar = spec.get("aspect_ratio", "4:3")
    a, b = (int(x) for x in ar.split(":"))
    utiles = {r for sc in spec["scenes"] for r in sc.get("character_refs", []) + [sc.get("background_ref")] if r}
    assets = {}
    for k, v in spec["assets"].items():
        if k not in utiles:
            continue
        du = v.get("data_url") or data_url(os.path.join(base, v["path"]), a / b)
        e = {"filename": v.get("filename", k + ".jpg"), "data_url": du, "description": v["description"]}
        if "retention" in v:
            e["retention"] = v["retention"]
        assets[k] = e
    scenes = []
    for sc in spec["scenes"]:
        s = {"name": sc["name"], "character_refs": sc.get("character_refs", []),
             "cuts": [{"seconds": c["seconds"], "text": c["text"], "speakers": c.get("speakers", [])} for c in sc["cuts"]]}
        if "seed" in sc:
            s["seed"] = sc["seed"]
        if sc.get("background_ref"):
            s["background_ref"] = sc["background_ref"]
        scenes.append(s)
    pack = {"schema": "qbuilder-html/v3", "model": "h3-director", "aspect_ratio": ar, "duration": spec.get("duration", 15),
            "style_line": spec.get("style_line", ""), "dialogue_language": spec.get("dialogue_language", "none"),
            "overall_soundscape": spec.get("overall_soundscape", ""), "non_diegetic_music": spec.get("non_diegetic_music", ""),
            "assets": assets, "scenes": scenes}
    js = json.dumps(pack, ensure_ascii=False, indent=1).replace("</", "<\\/")
    e = html.escape
    corps = []
    for sc in scenes:
        txt = "\n".join(f"CUT {i} · {c['seconds']:g} s\n{c['text']}" for i, c in enumerate(sc["cuts"], 1))
        decor = f" · décor <code>{e(sc['background_ref'])}</code>" if sc.get("background_ref") else ""
        corps.append(f"<h2 class=story>{e(sc['name'])}</h2><p class=mut>persos {e(', '.join(sc['character_refs']))}{decor}</p>"
                     f"<section><h3>{e(sc['name'])} <small>seed {sc.get('seed', '-')} · {len(sc['cuts'])} coupes · "
                     f"{pack['duration']} s · {ar}</small></h3><pre><code>{e(txt)}</code></pre></section>")
    titre = e(spec.get("title", "H3 Director"))
    page = f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{titre}</title>
<script type="application/json" data-prompts>
{js}
</script>
<style>
:root{{--bg:#f6f3ee;--fg:#1d1b18;--mut:#6b655c;--card:#fff;--line:#ddd6cb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16150f;--fg:#ece7de;--mut:#a39c90;--card:#201e18;--line:#3a362e}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}} .mut{{color:var(--mut)}} h2.story{{font-size:16px;margin:28px 0 2px;font-family:ui-monospace,Consolas,monospace}}
section{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:12px 0}}
h3{{font-size:15px;margin:0 0 8px}} small{{color:var(--mut);font-weight:400}} code{{overflow-wrap:anywhere}}
pre{{white-space:pre-wrap;font:13px/1.5 ui-monospace,Consolas,monospace;margin:0}}
</style></head><body>
<h1>{titre}</h1>
<p class=mut>{e(spec.get("intro", ""))} Charger avec « Load prompts HTML » en MiniMax H3 &gt; Director.</p>
<section><h3>Style</h3><p>{e(pack["style_line"])}</p></section>
<section><h3>Ambiance sonore</h3><p>{e(pack["overall_soundscape"])}</p></section>
{''.join(corps)}
</body></html>
"""
    assert page.count("<pre>") == len(scenes)
    return page, len(assets)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    chemin = os.path.abspath(sys.argv[1])
    base = os.path.dirname(chemin)
    spec = json.load(open(chemin, encoding="utf-8"))
    err = verifier(spec)
    if err:
        print("\n".join(err)); sys.exit(f"{len(err)} erreur(s), page non ecrite")
    if "--check" in sys.argv:
        print("OK :", len(spec["scenes"]), "scenes"); return
    page, n = construire(spec, base)
    out = os.path.join(base, spec["out"])
    try:  # une page ouverte dans Q-builder est verrouillee
        with open(out, "r+b" if os.path.exists(out) else "wb") as f:
            f.write(page.encode("utf-8")); f.truncate()
    except PermissionError:
        sys.exit("PAGE OUVERTE DANS Q-BUILDER, non reecrite : " + out)
    print(out, round(os.path.getsize(out) / 1e6, 1), "Mo,", len(spec["scenes"]), "scenes,", n, "images")


if __name__ == "__main__":
    main()
