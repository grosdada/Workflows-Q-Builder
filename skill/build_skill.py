"""Empaquete le skill en fichier .skill importable dans Claude.

    python skill/build_skill.py

Un .skill est une archive zip contenant un dossier unique au nom du skill,
avec SKILL.md a sa racine. Meme forme que les skills fournis par Anthropic.
"""

import pathlib
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
NAME = "prompt-html-export"
SOURCE = HERE / NAME
TARGET = HERE.parent / f"{NAME}.skill"


def main():
    if not (SOURCE / "SKILL.md").exists():
        raise SystemExit(f"SKILL.md introuvable dans {SOURCE}")

    files = sorted(item for item in SOURCE.rglob("*") if item.is_file())
    if TARGET.exists():
        TARGET.unlink()

    with zipfile.ZipFile(TARGET, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in files:
            # Chemin dans l'archive : <nom-du-skill>/... , comme attendu a l'import.
            archive.write(item, str(pathlib.Path(NAME) / item.relative_to(SOURCE)))

    size = TARGET.stat().st_size
    print(f"{TARGET.name} : {len(files)} fichier(s), {size / 1024:.1f} Ko")
    for item in files:
        print("  " + str(item.relative_to(SOURCE)))


if __name__ == "__main__":
    main()
