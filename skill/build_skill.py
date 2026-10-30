"""Empaquete le skill en fichier .skill importable dans Claude.

    python skill/build_skill.py

Un .skill est une archive zip contenant un dossier unique au nom du skill,
avec SKILL.md a sa racine. Meme forme que les skills fournis par Anthropic.
"""

import argparse
import pathlib
import zipfile

HERE = pathlib.Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Package a repository skill for Claude.")
    parser.add_argument("name", nargs="?", default="prompt-html-export",
                        choices=("prompt-html-export", "h3-orbital-html"))
    name = parser.parse_args().name
    source = HERE / name
    target = HERE.parent / f"{name}.skill"
    if not (source / "SKILL.md").exists():
        raise SystemExit(f"SKILL.md introuvable dans {source}")

    files = sorted(item for item in source.rglob("*") if item.is_file())
    if target.exists():
        target.unlink()

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in files:
            # Chemin dans l'archive : <nom-du-skill>/... , comme attendu a l'import.
            archive.write(item, str(pathlib.Path(name) / item.relative_to(source)))

    size = target.stat().st_size
    print(f"{target.name} : {len(files)} fichier(s), {size / 1024:.1f} Ko")
    for item in files:
        print("  " + str(item.relative_to(source)))


if __name__ == "__main__":
    main()
