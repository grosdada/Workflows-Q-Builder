"""Reconstruit le dossier distribuable a partir du dossier de developpement.

    python build_release.py

Copie l'app et ses workflows dans ../Workflows Q-builder, sans les fichiers de
travail (files en cours, uploads, sorties) ni l'ancienne app LTX. Le dossier
produit est celui qu'on copie sur un poste neuf ; ensuite, c'est le bouton
Update de l'app qui prend le relais.

Chemins relatifs a dessein : le script doit marcher depuis n'importe quelle
machine et n'importe quel emplacement.
"""

import pathlib
import shutil

HERE = pathlib.Path(__file__).resolve().parent
DIST = HERE.parent / "Workflows Q-builder"

FILES = [
    "workflows_q_builder.html",
    "workflows_q_builder_server.py",
    "queue_ltx_multishot.py",
    "queue_h3_multishot.py",
    "h3_director_build.py",
    "Lancer_Workflows_Q_Builder.bat",
    "README.md",
    "CONTEXTE.md",
    # MiniMax H3
    "h3_director_api.json",
    "h3_t2v_api.json",
    "h3_template_scout_v1.json",
    # LTX 2.5
    "ltx25_t2v_api.json",
    "ltx25_i2v_api.json",
    "ltx25_flf2v_api.json",
    # LTX 2.3
    "ltx2_t2v.json",
    "ltx19b_i2v_fast_api.json",
    "ltx19b_i2v_quality_upscale_api.json",
    # presets secondaires, chargeables via Parcourir
    "ltx19b_i2v_quality_api.json",
    "ltx19b_i2v_image_lock_api.json",
    "ltx_i2v_proto_api.json",
    "ltx2_i2v_audio_onepass_api.json",
    "ltx2_i2v_audio_quality_api.json",
    "ltx2_i2v_audio_t2v_conditioning_api.json",
    "local_settings.example.json",
]

# Presents s'ils existent : reglages et version de cette machine. Utiles quand
# on deploie sur ses propres postes, inutiles pour quelqu'un d'autre.
OPTIONAL = ["local_settings.json", "VERSION.json"]

WORK_DIRS = ["ltx_builder_uploads", "ltx_builder_workflows", "h3_workflows"]


def main():
    missing = [name for name in FILES if not (HERE / name).exists()]
    if missing:
        raise SystemExit("fichiers absents du dossier de dev : " + ", ".join(missing))

    if DIST.exists():
        # Un serveur laisse ouvert garde le dossier : on le dit au lieu de
        # planter sur une arborescence a moitie supprimee.
        try:
            shutil.rmtree(DIST)
        except PermissionError as exc:
            raise SystemExit(
                f"Impossible de vider {DIST} : {exc}\n"
                "Ferme la fenetre du serveur ou l'explorateur ouvert dessus, puis relance."
            ) from exc
    DIST.mkdir(parents=True)

    copied = 0
    for name in FILES + OPTIONAL:
        source = HERE / name
        if source.exists():
            shutil.copy2(source, DIST / name)
            copied += 1

    for folder in WORK_DIRS:
        (DIST / folder).mkdir(exist_ok=True)
        (DIST / folder / ".gitkeep").write_text("", encoding="utf-8")

    total = sum(item.stat().st_size for item in DIST.rglob("*") if item.is_file())
    print(f"{copied} fichiers copies vers {DIST} ({total / 1024:.0f} Ko)")


if __name__ == "__main__":
    main()
