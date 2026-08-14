# Contexte technique — Workflows Q-builder

Document de reprise, a lire par tout assistant qui doit depanner ou faire evoluer
cette app. Il rassemble les decisions, les pieges deja rencontres et la facon de
verifier qu'on n'a rien casse. Ecrit le 2026-08-14.

## Ce que c'est

Une interface locale qui prepare des files de generation ComfyUI et produit la
commande PowerShell qui les envoie. Pas de build, pas de dependance : un serveur
Python de la bibliotheque standard et une page HTML autonome.

L'app ne genere pas de video elle-meme. Elle remplit des workflows ComfyUI et les
empile dans la file de ComfyUI, qui tourne a part sur `http://127.0.0.1:8188/`.

## Les trois moteurs

| Moteur | Entree | Script | Sortie |
|---|---|---|---|
| LTX 2.5 / 2.3 | un prompt par plan | `queue_ltx_multishot.py` | POST sur `/prompt` |
| H3 Director | CUTs + images de reference | `queue_h3_multishot.py` | file API, ou fichiers `.json` a glisser |
| H3 T2V | un prompt par scene | `queue_ltx_multishot.py` | POST sur `/prompt` |

H3 T2V passe par le script LTX parce que son workflow natif se pilote exactement
pareil : un prompt, une seed, un prefixe de sortie. Seules la duree (widget
`PrimitiveFloat` nomme duration) et la taille (`ResolutionSelector`, en ratio +
megapixels) demandent des options dediees, ajoutees a ce script.

## Carte des fichiers

```
workflows_q_builder.html        toute l'interface (HTML + CSS + JS, un seul fichier)
workflows_q_builder_server.py   serveur local : sert la page, uploads, /api/env, mise a jour
queue_ltx_multishot.py          patche un workflow API et l'envoie a ComfyUI (LTX + H3 T2V)
queue_h3_multishot.py           boucle sur des briefs H3, construit ou envoie les workflows
h3_director_build.py            brief -> workflow H3 (copie du skill muse-h3-director)
h3_director_api.json            workflow H3 Director au format Export (API) : pour la file
h3_template_scout_v1.json       le meme au format UI : pour produire des .json deposables
h3_t2v_api.json                 workflow MiniMax H3 T2V natif (format API)
ltx*.json                       presets LTX (format API)
local_settings.json             reglages de CETTE machine, non versionne
VERSION.json                    version installee, ecrite par la mise a jour, non versionne
_backup_previous/               version precedente, ecrite par la mise a jour, non versionne
```

## Comment ca tourne

`Lancer_Workflows_Q_Builder.bat` cherche un Python (PATH, puis le lanceur `py`,
puis un chemin ecrit dans `python_path.txt`), lance le serveur sur le port 8765 et
ouvre le navigateur.

Le serveur refuse de demarrer si le port est deja pris. C'est voulu : sous Windows
deux serveurs peuvent se poser sur le meme port sans erreur, et c'est le plus
ancien qui repond — on servait l'ancienne interface en croyant lancer la nouvelle.
Option `--port` pour en lancer un second volontairement.

Endpoints : `/api/env` (dossier, interpreteur Python, reglages locaux),
`/api/upload`, `/api/workflow-upload`, `/api/workflow-read`, `/api/update-check`,
`/api/update`.

## Rien de code en dur

L'app doit tourner depuis n'importe quel dossier, sur n'importe quelle machine.

- Le champ **Dossier** est rempli par `/api/env` au chargement, jamais ecrit dans le
  HTML. Ouverte sans serveur (double-clic sur le `.html`), l'app le dit desormais
  au lieu de laisser le champ vide sans explication.
- La commande generee designe **l'interpreteur exact** qui fait tourner le serveur
  (`& 'C:\...\python.exe'`), pas le mot `python` : sur une machine ou Python n'est
  pas dans le PATH, `python` echouerait.
- Chemin du NAS, serveur ComfyUI, dossier `input` de ComfyUI : dans
  `local_settings.json`, hors depot.

## Contraintes MiniMax H3, verifiees en conditions reelles

- **15 secondes maximum par rendu.** Au-dela, le noeud enchaine un second chunk,
  recharge les deux VAE + le text encoder + le modele sans rien liberer, atteint
  ~30 Go sur 32 et meurt en `OutOfMemoryError` avant le premier step — apres avoir
  calcule le premier chunk. `h3_director_build.py` refuse de construire au-dela.
- **Tags `<Subject N>` litteraux** dans le texte des CUTs. Aucune resolution
  semantique : « the scavenger » ne sera jamais relie a la Ref 1.
- **Le lieu dans chaque CUT**, meme en gros plan, sinon H3 reprend le decor de la
  planche de reference du personnage.
- **Un slot de reference sans image est ignore** a la compilation et decale la
  numerotation de tous les suivants.
- **Chaine de post-traitement bypassee** (`RTXVideoSuperResolution` + `MuseMinimaxRefine`) :
  le Refine plante en access violation, et le RTX en amont sature la RAM si on ne
  bypasse que le Refine. Les deux ensemble, toujours.
- **Multishot T2V** : le modele ne coupe que si on lui impose des segments
  `[Shot n]` horodates. Une consigne « 5 plans » en langage libre donne un plan
  continu. Le champ « Plans par scene » genere la consigne, et la ligne d'etat
  compte ensuite les coupes reellement ecrites.

## Contraintes LTX

- `--frame-count` n'est pas injecte tel quel : pour LTX 2.5 il est converti en
  duree (secondes) car le graphe pilote la longueur par un widget duration.
  241 frames a 24 fps donnent 10 secondes.
- Le prompt enhancer LTX 2.5 est **desactive** dans la boucle
  (`disable_prompt_enhance`) : actif en lot, il fait saturer la VRAM.
- LTX 2.5 veut un frame count en 8n+1 et une taille divisible par 32. L'interface
  refuse de generer sinon.

## Pieges deja rencontres, ne pas les reintroduire

- **Un `<label>` transmet le clic a son premier controle.** Un bouton dans un label
  se declenchait en cliquant le libelle ou le vide autour : la fenetre de saisie de
  cle s'ouvrait toute seule, « Rafraichir » lancait un appel API, « Clear » effacait
  la description. Les champs concernes utilisent `.field` + un `<label for=...>`
  pointant le vrai champ.
- **L'archive zip de GitHub ignore `.gitattributes`.** Un `.bat` livre par mise a
  jour arrivait en LF. La mise a jour force elle-meme les fins de ligne Windows
  pour les `.bat`, et compare octet pour octet ceux-la (comparaison tolerante pour
  le reste, sinon chaque mise a jour reecrivait tous les fichiers texte).
- **Un acces direct a `localStorage` peut lever une exception** (page hors serveur,
  profil restreint). Comme le premier acces etait en tete de script, toute l'app
  mourait sans afficher un mot. Tout passe par le helper `store`.
- **Le parseur multipart** decoupait le bloc d'en-tetes entier sur `;`, ce qui
  collait le `Content-Type` dans le nom de fichier et mangeait l'extension. Il ne
  lit plus que la ligne `Content-Disposition`.
- **`seed_control` n'existe pas** dans l'Export (API) du noeud Director : c'est le
  widget UI `control_after_generate`. Ne pas le patcher.

## Mise a jour

`/api/update` telecharge l'archive de la branche `main` du depot
`grosdada/Workflows-Q-Builder`, remplace les fichiers, ecrit `VERSION.json` et
sauvegarde les precedents dans `_backup_previous`.

Jamais ecrases : `local_settings.json`, `comfy_input.txt`, `python_path.txt`, les
fichiers de file (`*_queue.json`), et les dossiers `ltx_builder_uploads`,
`ltx_builder_workflows`, `h3_workflows`, `_backup_previous`, `.git`.

Rien n'est supprime : un fichier disparu du depot reste en place.

## Comment verifier qu'on n'a rien casse

Il n'y a pas de suite de tests automatisee ; voici ce qui a servi jusqu'ici.

```bash
# 1. les scripts compilent
python -m py_compile workflows_q_builder_server.py queue_ltx_multishot.py queue_h3_multishot.py h3_director_build.py

# 2. LTX part bien, sans rien envoyer
python queue_ltx_multishot.py --workflow ltx25_t2v_api.json --prompts ltx_custom_queue.json --dry-run

# 3. H3 : validation, apercu du prompt six sections, aucun fichier ecrit
python queue_h3_multishot.py --briefs h3_custom_queue.json --template h3_director_api.json --dry-run --preview
```

Pour l'interface : ouvrir la page, verifier qu'il n'y a **aucune erreur console**,
basculer les trois moteurs et confirmer que la derniere ligne de la commande
generee designe le bon script. Basculer FR/EN et le theme, verifier qu'aucune zone
ne reste dans l'autre langue.

Le remplacement de fichiers de la mise a jour se teste hors ligne, sur un faux
dossier d'app et une fausse archive : c'est la partie qui peut detruire du travail,
elle merite un test avant chaque modification.

## Facon de travailler

Le dossier de developpement est sur le PC principal ; c'est la que vit le depot
git. Apres modification : commit + push, puis les autres postes cliquent Update.

Le dossier `Workflows Q-builder` (a cote du dossier de dev) est la copie propre
pour le premier deploiement d'un poste neuf. Elle doit rester identique au dev.

Ne jamais remettre de chemin personnel dans un fichier versionne : le depot est
public.
