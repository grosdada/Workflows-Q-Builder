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

## Les moteurs

| Moteur | Ce qu'on remplit | Ce qui part |
|---|---|---|
| **LTX** 2.5 / 2.3 | un prompt par plan | `queue_ltx_multishot.py` → file ComfyUI |
| **MiniMax H3 — Director** | CUTs + images de référence | `queue_h3_multishot.py` → file ou fichiers `.json` |
| **MiniMax H3 — T2V** | un prompt par scène | `queue_ltx_multishot.py` → file ComfyUI |
| **MiniMax H3 — Orbital** | image de départ, références et trajectoire | `queue_h3_orbital.py` → file ComfyUI |

## Ce que fait l'app

- **Génération assistée** des prompts via OpenAI, Anthropic (Claude), Google (Gemini) ou DeepSeek.
  Une clé par fournisseur, conservée en mémoire ou dans le navigateur si demandé, jamais dans les fichiers. La liste
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

## Pilotage réseau

`local_settings.json` peut contenir une liste `comfy_nodes`. L'interface affiche leur état et permet soit de viser une machine, soit de répartir automatiquement la file sur les nœuds joignables les moins chargés.

```json
"comfy_nodes": [
  {"name": "PC-1", "url": "http://192.168.1.10:8188"},
  {"name": "PC-2", "url": "http://192.168.1.11:8188"}
]
```

Chaque ComfyUI distant doit être lancé avec `--listen 0.0.0.0` et rester limité au réseau privé. Les scripts acceptent toujours `--server` pour un seul PC, et désormais `--servers` avec une liste séparée par des virgules. Les images LTX/I2V sont envoyées au nœud choisi via son API avant le workflow.

Q-builder peut tourner sur un poste de commande sans ComfyUI. Les références H3 sont alors conservées par l’app puis envoyées au worker choisi. Le drapeau `auto` active ou exclut chaque nœud de la répartition automatique. Une scène H3 qui échoue ou disparaît est réessayée une seule fois sur un autre worker. L’option **Copy results** attend les rendus et télécharge une copie des sorties dans le dossier choisi, sans supprimer les originaux des workers.

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

## Références par prompt et génération intégrée

En mode H3 Director, chaque prompt peut utiliser les **images communes** ou un
**jeu propre à ce prompt**. Une seule image peut servir à trois prompts ; trois
prompts peuvent aussi utiliser chacun trois images différentes. Le choix se fait
sous chaque prompt, par import de fichiers ou sélection des images déjà chargées.
Les fichiers portant le même nom restent distincts lorsqu’ils sont importés par prompt.

L’ordre choisi définit `<Subject 1>`, `<Subject 2>`, etc., à nouveau pour chaque
scène. La génération assistée conserve ces associations et reçoit les références
propres à chaque scène. Elle contrôle la réponse complète avant de remplacer les
textes ; une réponse invalide reste disponible pour réapplication sans nouvel
appel payant. L’IA texte reçoit les descriptions, pas automatiquement les pixels.

Les catalogues OpenAI et Anthropic affichent les cinq modèles texte les plus
récents retournés par leur API. Les modèles sont récupérés avec leur pagination.
DeepSeek utilise sa propre clé et le catalogue de son API. Les erreurs et réponses
tronquées conservent les prompts existants.

Le skill synchronisé est dans [skill/prompt-html-export](skill/prompt-html-export/SKILL.md)
et dans l’archive importable [prompt-html-export.skill](prompt-html-export.skill).
Il couvre le contrat HTML v3, les images partagées ou propres à une scène, les
Subjects et les locuteurs. La recette artistique Mode-Cringe/VHS reste distincte
des règles techniques.

Pour préparer les lots avec les fonctions récentes, voir le
[guide HTML Director](skill/prompt-html-export/references/fonctions-recentes-qbuilder.md).
Le mode Orbital a son [skill séparé](skill/h3-orbital-html/SKILL.md) et son
propre schéma HTML ; les deux formats ne sont pas interchangeables.

Les versions récentes de `MuseMinimaxDirector` requièrent `candidate_2`,
`candidate_3` et `candidate_4`. Q-Builder complète les anciens exports avec les
valeurs désactivées, en conservant les valeurs ou branchements explicites.

Dans un brief Director envoyé avec `queue_h3_multishot.py --queue`, les médias
optionnels se déclarent à la racine, à côté de `characters` et `background` :

```json
{
  "ref_videos": [{"file": "plan_source.mp4", "role": "reference", "includeAudio": false}],
  "ref_audios": [{"file": "voix.wav", "retention": "reference"}]
}
```

Chaque liste accepte jusqu'à trois références. `file` désigne un fichier lisible
depuis le poste de commande (chemin absolu, fichier préparé dans
`ltx_builder_uploads`, ou nom résolu dans le dossier de références du projet).
Les images, vidéos et audios sont copiés par HTTP vers le worker choisi sous un
nom court déterministe ; le timeline du workflow API reçoit le chemin distant
dans `file` et son nom dans `fileName`. Une reprise sur un autre worker refait
ces uploads. Les originaux ne sont pas supprimés. `--copy-results` choisit seul
le dossier de rapatriement ; aucun chemin de sortie par défaut n'est changé.

Vérification : références communes et trois jeux de trois images exercés dans
l’interface avec des fournisseurs simulés ; aucune génération payante ni rendu
GPU n’est nécessaire pour ces contrôles. Les tests Orbital peuvent être lancés
avec `python -m unittest test_h3_orbital_qbuilder.py`.

## Pilotage par API (H3 Director)

Le serveur local Q-Builder expose ces routes sur `127.0.0.1:8765`. Le portail
de voyage, après redémarrage, les expose aussi sur `127.0.0.1:8790`. Les POST
sont JSON, limités à la même origine, et ne lancent aucune commande shell.

Configurer `api_allowed_roots` dans le `local_settings.json` local :

```json
"api_allowed_roots": ["Y:\\project", "E:\\Codex-Proj", "K:\\Bibliotheque-Assets-IA", "\\\\192.168.1.44\\nas"]
```

Les chemins HTML et les destinations de copie hors de ces racines sont refusés.
Le JSON attendu est `qbuilder-html/v3` / `h3-director`, avec images `data_url`.
Chaque scène peut définir `"output_name": "h3_queue_01_01_le_garage_de_nuit_B"`.
Ce nom remplace le préfixe automatique du lot dans `Muse/Video/` ; la casse et
les tirets sont conservés (seuls les caractères interdits deviennent `_`).
Deux scènes ne peuvent pas produire le même `output_name` : validation `422`.
`validate`, le dry-run et le suivi du batch renvoient le nom effectif.
L'API utilise `h3_director_build.py`, `h3_director_api.json`, l'upload HTTP
`/upload/image`, le répartiteur et `qbuilder_jobs.json`. Les originaux restent
dans ComfyUI/output ; `copy_results` ne fait qu'en télécharger une copie.

```js
const base = {html_path: 'Y:\\project\\mon-film\\H3-Director_batch.html'};
const post = (url, body) => fetch(url, {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(body)
}).then(r => r.json());
await post('/api/batch/validate', base);
await fetch('/api/settings/effective').then(r => r.json());
await post('/api/batch', {...base, scenes: '01,03-05', dry_run: true});
const started = await post('/api/batch', {...base, scenes: ['01_intro'],
  workers: ['FORD'], copy_results: 'Y:\\project\\mon-film\\_test-api'});
await fetch(`/api/batch/${started.batch_id}`).then(r => r.json());
await fetch('/api/batches?limit=20').then(r => r.json());
// Exemples independants : annuler une scene en attente, OU relancer une scene en echec.
await post(`/api/batch/${started.batch_id}/cancel`, {scenes: '01'});
await post(`/api/batch/${started.batch_id}/retry`, {scenes: ['01_intro'], workers: 'auto'});
```

`POST /api/batch` répond immédiatement ; le `prompt_id` apparaît ensuite dans
`GET /api/batch/<id>`. `copy_results` absent reprend la case de l'interface ;
`""` désactive la copie. Le dry-run n'envoie rien. Une reprise automatique
unique peut basculer sur un autre worker. Les scènes API sont enregistrées dans
le même registre que celles lancées en ligne de commande.

Codes : `400` JSON/sélection invalide, `403` origine ou chemin refusé, `404`
HTML/batch absent, `409` action incompatible avec l'état, `413` requête/image
trop volumineuse, `422` prompts invalides, `503` aucun worker disponible.
Les erreurs ont la forme `{"ok":false,"error":"...","details":[...]}`.

## Suivi des calculs et copies

Le menu « Calculs » sous les commandes de langue affiche, par PC, les jobs en
cours et en attente ; il se rafraîchit automatiquement. Les machines hors ligne
restent signalées comme telles. Dans le panneau réseau, « Batchs API » permet
de consulter les scènes, workers, `prompt_id` et chemins de résultats des lots
pilotés par l'API. Un batch API possède sa propre surveillance et sa reprise
unique : l'outil de récupération général l'ignore pour éviter un doublon.

Quand « Copy results » est activé, Q-Builder télécharge les sorties via
`/view` sans toucher aux originaux ComfyUI. Une même sortie signalée plusieurs
fois par `/history` n'est copiée qu'une fois ; si un fichier identique existe
déjà dans le dossier choisi, il est réutilisé plutôt que de créer un suffixe
`_2`. Un fichier différent portant le même nom garde une copie distincte.
