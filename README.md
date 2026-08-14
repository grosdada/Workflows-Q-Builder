# Workflows Q-builder

Interface locale pour préparer des files multi-shots ComfyUI : **LTX 2.5**, **LTX 2.3**
et **MiniMax H3**. Elle écrit un fichier de file et la commande PowerShell qui envoie
les jobs à ComfyUI.

Tout tourne en local : un petit serveur Python, une page HTML, aucun compte requis
pour l'usage de base.

## Installation

1. Copier le dossier où l'on veut.
2. Double-cliquer `Lancer_Workflows_Q_Builder.bat`.
3. Le navigateur s'ouvre sur `http://127.0.0.1:8765/`.

Python 3 doit être présent. Si le lanceur ne le trouve pas, il explique quoi faire :
installer Python, ou pointer un `python.exe` existant (celui de ComfyUI portable fait
l'affaire) via un fichier `python_path.txt` posé à côté du `.bat`.

ComfyUI doit tourner sur `http://127.0.0.1:8188/` avec les modèles voulus.

## Les trois moteurs

| Moteur | Ce qu'on remplit | Ce qui part |
|---|---|---|
| **LTX** 2.5 / 2.3 | un prompt par plan | `queue_ltx_multishot.py` → file ComfyUI |
| **MiniMax H3 — Director** | CUTs + images de référence | `queue_h3_multishot.py` → file ou fichiers `.json` |
| **MiniMax H3 — T2V** | un prompt par scène | `queue_ltx_multishot.py` → file ComfyUI |

## Ce que fait l'app

- **Génération assistée** des prompts via OpenAI, Anthropic (Claude) ou Google (Gemini).
  Une clé par fournisseur, stockée dans le navigateur, jamais dans les fichiers. La liste
  des modèles se rafraîchit depuis l'API du fournisseur, donc elle ne périme pas.
- **Presets caméra / style** par époque, du film noir des années 1930 à l'iPhone, avec
  surcharge possible plan par plan.
- **Multishot H3 T2V** : impose de vraies coupes `[Shot n]` horodatées à l'intérieur d'une
  génération, et vérifie ensuite que le modèle les a bien écrites.
- **Interface FR / EN** et thème clair ou sombre, mémorisés.
- **Bouton Update** : récupère la dernière version depuis ce dépôt sans rien recopier à la
  main. Les réglages de la machine et les files en cours ne sont jamais écrasés, et la
  version précédente est sauvegardée dans `_backup_previous`.

## Réglages propres à la machine

Copier `local_settings.example.json` en `local_settings.json` et renseigner ce qui
diffère d'un poste à l'autre :

```json
{
  "workflow_browse_path": "",
  "comfy_server": "http://127.0.0.1:8188",
  "comfy_input": "C:\\ComfyUI\\input"
}
```

Ce fichier n'est pas versionné et survit aux mises à jour.

## Règles MiniMax H3 à ne pas contourner

Elles viennent de plantages réels, pas de prudence théorique.

- **15 secondes maximum par rendu.** Au-delà, le nœud enchaîne un second chunk, recharge
  tous les modèles sans rien libérer, et meurt en `OutOfMemoryError` avant le premier
  step — après avoir calculé le premier chunk pour rien. Découper en scènes `_a` / `_b`.
- **Écrire `<Subject 1>`, `<Subject 2>`… en toutes lettres** dans le texte des CUTs. Aucune
  résolution sémantique n'est faite : « the scavenger » ne sera jamais relié à la Ref 1.
- **Ancrer le lieu dans chaque CUT**, même en gros plan, sinon H3 reprend le décor de la
  planche de référence du personnage.
- **L'ordre des personnages fixe la numérotation des Subject.** Un slot sans image est
  ignoré à la compilation et décale tous les suivants.
- **Lancer un seul rendu en test avant d'empiler un lot** : un job qui sature la VRAM ne
  bloque pas la file, les suivants partent et échouent pareil.

## Crédits

La construction des workflows H3 Director reprend le skill `muse-h3-director`
(`h3_director_build.py`), qui patche un template ComfyUI existant plutôt que de
régénérer le graphe.
