---
name: prompt-html-export
description: Produire une page HTML de prompts video (LTX, MiniMax H3, ou tout autre modele) qui se charge sans perte dans Workflows Q-builder via son bouton "Load prompts HTML". Declencher des que David demande une serie de prompts presentee en page HTML, une planche de prompts, un HTML de prompts a charger dans l'app, ou qu'il parle de "load prompts HTML", "page de prompts", "8 prompts en HTML". Declencher aussi pour corriger une page existante dont les prompts ne remplissent pas les champs de l'app, et pour tout lot H3 Director (noeud Muse) ou chaque scene a son propre casting de personnages, a partir d'images fournies par David (planches de personnages, decors).
---

# Page HTML de prompts, chargeable sans perte

Workflows Q-builder sait charger une page HTML de prompts et les repartir dans
ses champs. Ce skill dit comment ecrire la page pour que ca marche a tous les
coups, au lieu de laisser l'app deviner.

## La regle qui evite le probleme

**Toujours embarquer un bloc JSON dans la page.** L'app le cherche en premier et
s'y fie entierement : aucune heuristique, aucune perte, aucun ordre a deviner.

```html
<script type="application/json" data-prompts>
[
  {"name": "01_salt_flat", "positive": "Multi-shot air-to-air sequence, six cuts..."},
  {"name": "02_canyon",    "positive": "Multi-shot air-to-air sequence, six cuts..."}
]
</script>
```

La page reste lisible par ailleurs : titres, blocs `<pre>`, boutons copier, ce
qu'on veut. Le bloc JSON est invisible a l'ecran et ne sert qu'a l'app.

## Pourquoi ce bloc n'est pas facultatif

Sans lui, l'app lit les blocs `<pre>` et deduit le reste. Ca marche souvent, mais
une serie de prompts partage typiquement un long bloc commun en tete — meme
sujet, meme decor, meme style — et seules les dernieres lignes different. Toute
heuristique de dedoublonnage devient alors un pari.

Cas vecu le 2026-08-14 : huit prompts H3 partageant le meme bloc SUBJECT. L'app
dedoublonnait sur les 300 premiers caracteres et n'a rempli qu'un champ sur huit.
Le dedoublonnage compare desormais le texte entier, mais le bloc JSON reste la
seule facon de ne dependre d'aucune heuristique.

## Synchronisation Q-Builder / IA integree — 2026-10-19

Ce paquet conserve le generateur HTML original et documente le contrat commun
avec l'IA integree. Lire aussi `assets/exemple_ia_integree.md`.

### Deux modes de references, meme contrat de rendu

- **Images communes** : une image (ou un jeu commun) est reutilisee par chaque
  scene. Trois prompts peuvent donc pointer vers exactement la meme image.
- **Images propres a chaque prompt** : chaque scene choisit son propre jeu,
  par exemple trois prompts avec trois images chacun. Les jeux peuvent aussi
  se recouper. Une selection vide explicite signifie zero reference, jamais
  un retour automatique aux images communes.
- Dans l'interface Director, le choix est affiche sous chaque prompt :
  `Utiliser les images communes` ou `Choisir les images de ce prompt`.
  Les images peuvent etre ajoutees sur place ou cochees dans la bibliotheque.
  La liste affiche l'ordre local des `<Subject N>`.
- Le HTML v3 emploie toujours `assets`, `character_refs` et `background_ref`.
  Ne pas changer ce schema. L'ordre de `character_refs` est celui des Subjects
  et des locuteurs de CETTE scene. La numerotation repart a 1 a chaque scene.
- Le catalogue peut depasser neuf images ; le plafond de neuf est applique
  a chaque scene. Trois images par scene sont prises en charge. Les fichiers
  deja presents sur un autre worker ne constituent jamais une selection.
- Ne jamais reutiliser un meme nom de staging pour deux fichiers differents :
  `image.png` du prompt 1 et `image.png` du prompt 2 doivent rester distincts.

### Regles de l'IA integree

L'IA integree recoit les associations ordonnees pour chacune des scenes a
produire. Elle genere le JSON des textes, pas une page HTML : l'application
possede deja les fichiers et les associations. Elle conserve ces selections
lorsqu'elle remplace les textes et ne renvoie ni chemin invente, ni asset
inexistant, ni URL blob. Sans references, aucun tag `<Subject N>`.

Les textes restent en anglais ; chaque CUT ancre le lieu et decrit une action
visible. Duree maximale : 15 secondes par scene. Par defaut, pas de parole
intelligible ; une demande explicite de dialogue peut l'activer. Tout dialogue
entre guillemets a ses locuteurs Subject dans `speakers`, et un CUT sans
parole garde `speakers: []`. Un figurant sans reference ne devient pas un
locuteur par substitution a un autre personnage.

La recette Mode-Cringe/VHS ci-dessous est une direction artistique pour ce
projet. Ne pas la confondre avec le contrat technique, ni l'imposer a un autre
style explicitement demande par David. Ses interdictions lexicales et son
univers de mode ne doivent pas etre appliques globalement a tous les projets.

### Verification et limites

Avant de remplacer les slots, Q-Builder controle le JSON complet, le nombre
de scenes, les durees, les numeros de Subjects/locuteurs et leur coherence
avec les images selectionnees. Une reponse invalide laisse les slots et les
associations precedentes en place ; elle reste recuperable sans second appel.
Pendant un import d'images, le lancement reste bloque jusqu'a la fin de la
copie. Ne jamais recharger une page contenant des prompts non sauvegardes
pour installer une mise a jour ; ouvrir une nouvelle page.

Les regles n'impliquent pas que l'IA texte a vu les pixels. Une description
vide ne permet pas de deduire l'apparence d'un personnage a partir du nom du
fichier. Le rendu ComfyUI recoit les images ; pour une interpretation visuelle
dans l'IA, fournir des descriptions ou une analyse d'image explicite.

## H3 Director : references differentes par scene

Pour un batch H3 ou chaque scene emploie un autre casting, utiliser
`"schema": "qbuilder-html/v3"`. Declarer chaque image une seule fois dans
`assets`, puis selectionner ses identifiants dans chaque scene. Ne pas mettre
les images dans les scenes elles-memes.

```json
{
  "schema": "qbuilder-html/v3",
  "model": "h3-director",
  "assets": {
    "alice": {"filename": "alice.jpg", "data_url": "data:image/jpeg;base64,...", "description": "...", "retention": "fully_preserved"},
    "bruno": {"filename": "bruno.jpg", "data_url": "data:image/jpeg;base64,...", "description": "...", "retention": "fully_preserved"},
    "decor_bureau": {"filename": "bureau.jpg", "data_url": "data:image/jpeg;base64,...", "description": "..."}
  },
  "scenes": [{
    "name": "01_alice_bruno",
    "character_refs": ["alice", "bruno"],
    "background_ref": "decor_bureau",
    "cuts": [{"seconds": 5, "text": "<Subject 1> speaks to <Subject 2>.", "speakers": [1]}]
  }]
}
```

`character_refs` fixe les personnages de **cette scene uniquement**, dans cet
ordre. Ils sont renumerotes localement : les deux IDs selectionnes deviennent
`<Subject 1>` et `<Subject 2>`, quels que soient leurs rangs dans les autres
scenes. Les numeros de `speakers` suivent cette meme numerotation locale.

Q-builder construit alors un workflow H3 par scene et n'upload vers son worker
que les deux images et le decor selectionnes. Les images deja presentes dans le
`input` d'un worker ne sont pas supprimees, mais elles ne sont pas chargees par
le workflow d'une scene qui ne les selectionne pas.

Chaque asset doit avoir une `data_url` (`data:image/...;base64,...`), un ID
unique, un `filename` court et une image sous 25 Mo. `background_ref` est
optionnel. La limite de 9 references s'applique a une scene, pas a `assets`.

Le schema v2 (`characters` et `background` a la racine) reste adapte lorsqu'un
batch entier partage le meme casting ; il reste compatible.

## H3 Director : fabriquer la page avec le script (methode a suivre)

Ne pas ecrire la page a la main : ecrire une **spec JSON** puis lancer
`scripts/qbuilder_page.py`. Le script embarque les images, construit le bloc v3,
la partie lisible, et refuse la page si une regle est violee.

```
python scripts/qbuilder_page.py ma_spec.json --check   # verifie seulement
python scripts/qbuilder_page.py ma_spec.json           # ecrit la page (spec["out"])
```

Modele complet : `assets/exemple_spec.json` (deux scenes, castings differents,
une avec decor, l'autre sans). Necessite Pillow (`pip install pillow`).

- **Images** : chaque asset a `path` (image locale, relative a la spec ou
  absolue) ou `data_url`. N'importe quelles images conviennent : celles que David
  donne dans la conversation, copiees dans un dossier. Le script les met au
  format de sortie **sans les rogner** (bandes de la couleur du coin haut-gauche),
  les reduit a 1600 px et les passe en JPEG. Indispensable : le noeud Muse recadre
  toute reference au format de sortie, une planche 16:9 envoyee telle quelle perd
  ses bords en 4:3.
- **Personnages** : `retention: "fully_preserved"` et une `description` en
  anglais (age, visage, cheveux, vetement precis) ; ils deviennent
  `<Subject 1..N>` dans l'ordre de `character_refs` de chaque scene. 4 au plus
  par scene en pratique. Les figurants sans image sont decrits dans le texte.
- **Decor** : asset sans `retention`, cite par `background_ref` (facultatif ; sans
  decor, decrire le lieu dans la premiere coupe).
- Seuls les assets cites par au moins une scene sont embarques.

## H3 Director : reglages de page et ecriture (recette de David)

Champs de page : `aspect_ratio` "4:3", `duration` 15, `style_line`,
`overall_soundscape`, `non_diegetic_music` "" , `dialogue_language` "none".

- **15 s maximum par scene** : au-dela le noeud enchaine un second chunk qui
  plante en manque de memoire. Decoupage habituel : 6 coupes
  `[3, 2.5, 2.5, 2.5, 2.5, 2]`. 10 scenes par page (20 au plus). Seeds
  numerotees a la suite des lots precedents.
- **Muet par defaut** (voix H3 faibles) : aucun guillemet, `speakers` vide ; un
  murmure s'ecrit « murmurs something too low to hear ».
- **style_line** : commencer par un appareil grand public + date + genre, puis
  decrire le comportement de la camera, pas des mots de cinema. Gabarit :
  « Footage from a VHS tape recorded at home in 1987: a French television
  documentary following ..., filmed on location with a video camcorder and taped
  off the television on a home video recorder. Handheld at shoulder height from
  one spot at a time: the frame drifts and corrects itself a moment late, the
  autofocus hunts when someone moves, the auto-exposure pumps when the camera
  turns toward a window, windows blow out to white, flat available light,
  everything in focus from front to back, colours a little washed out with the
  reds bleeding, light tape noise. The camera only pans and tilts. Mostly medium
  shots and close-ups, full figures only briefly. [qui sont les personnages et
  comment ils se comportent] [comment les gens normaux reagissent] Deadpan slice
  of life, slow, long silences, no punchline. Every face is real human skin with
  visible pores, fine lines and small imperfections, moving naturally. »
- **Coupes** : la premiere decrit le lieu fixe (ou est chaque chose, d'ou
  filment les cameras, toutes du meme cote de l'axe). Chaque coupe nomme sa
  camera : « Camera A, from ...: », « Close-up of <Subject 2>: ». Chaque Subject
  declare apparait au moins une fois.
- **Aucune negation dans les coupes** : H3 est distille, sans prompt negatif ;
  « never, no, not, without, nobody... » ajoutent ce qu'ils nient. Ecrire a
  l'affirmative (« his eyes stay on the newspaper »).
- **Mots bannis** : costume, dolly, zoom, cinematic, film grain, 35mm, operator,
  cameraman. Ne jamais decrire qui filme. Panoramiques et bascules seulement.
- Plans moyens et gros plans ; le plan en pied reste bref (visages degrades en
  plan large).
- Univers Mode-Cringe : mode, pas carnaval (ni fruit, ni animal, ni deguisement),
  jamais le mot « costume » ; tranches de vie a la Rohmer, malaise, rien ne se
  conclut, pas de replique a chute.

Le script verifie tout cela (duree, Subjects, guillemets/speakers, negations,
mots bannis) et n'ecrit rien tant qu'il reste une erreur.

Charger la page dans Workflows Q-builder : « Load prompts HTML », MiniMax H3 >
Director. Une page ouverte dans Q-builder est verrouillee : relancer plus tard.

## H3 Director : chaque replique doit avoir un locuteur Subject

Q-builder refuse une commande des qu'un CUT contient du dialogue (texte entre
guillemets) avec `speakers` vide : « CUT n a du dialogue sans locuteur : la voix
ne sera attribuee a personne ». Regles :

- **Tout CUT qui cite une replique entre guillemets doit lister ses locuteurs**
  dans `speakers`, dans l'ordre ou ils parlent, en numerotation locale a la
  scene : `{"seconds": 6, "text": "<Subject 1> says, \"...\" <Subject 2> replies, \"...\"", "speakers": [1, 2]}`.
- **Seuls les `<Subject N>` peuvent parler.** Un figurant (serveuse, gardien,
  voix off, voix au telephone) n'a pas de numero : ne jamais lui donner une
  replique entre guillemets, et ne jamais l'attribuer a un Subject pour faire
  passer la verification — la voix sortirait de la bouche du mauvais personnage.
  Decrire plutot son geste ou le sens de ce qu'il dit, sans guillemets
  (« the waitress puts the bill between their cups », « the porter raises a hand
  toward the door »), ou faire de lui un Subject de la scene s'il doit parler.
- **Inversement**, un CUT sans guillemets garde `speakers: []`.

Verification a ajouter avant de livrer : pour chaque CUT, `('"' in text) == bool(speakers)`.

## Schema

Un tableau d'objets, 20 elements maximum (l'app a 20 emplacements).

| Champ | Obligatoire | Pour quoi |
|---|---|---|
| `name` | oui | nom du plan, sert au nom de fichier de sortie |
| `positive` | oui, sauf en mode Director | le prompt complet, tel qu'il sera envoye |
| `seed` | non | entier ; absent, l'app met le sien |
| `cuts` | mode H3 Director seulement | `[{"seconds": 3.5, "text": "...", "speakers": [1]}]` |
| `style` | non | valeur d'un preset camera, ou `"custom"` |
| `custom_style` | non | texte du style si `style` vaut `"custom"` |
| `character_refs` | H3 v3 | IDs des assets, dans l'ordre local des `<Subject N>` |
| `background_ref` | H3 v3 | ID de l'asset decor pour cette scene |

`name` doit etre court et sans caractere exotique : minuscules, chiffres,
tirets bas. Numeroter dans l'ordre de lecture (`01_`, `02_`…) — c'est ce qui
distingue les sorties dans ComfyUI.

## Deux pieges d'ecriture

**`</script>` dans un prompt** ferme le bloc JSON et casse la page. Si un prompt
doit contenir cette suite de caracteres, l'ecrire `<\/script>`.

**Les chevrons des tags H3** (`<Subject 1>`) s'ecrivent normalement dans le JSON,
sans echappement HTML. Dans la partie visible de la page, en revanche, ils
doivent etre echappes en `&lt;Subject 1&gt;` — sinon le navigateur les prend pour
des balises et les fait disparaitre a l'ecran.

## Si la page doit rester lisible sans le bloc JSON

L'app sait retomber sur la structure visible. Pour que ce filet fonctionne :

- **un prompt par bloc `<pre><code>`**, jamais deux prompts dans le meme bloc ;
- **un titre juste avant chaque bloc** (`<h2>`, ou une ligne `class="sub"`)
  contenant un slug numerote du type `01_salt-flat` : l'app en fait le nom du
  plan. Sans slug, elle prend le titre litteraire ; sans titre, elle fabrique un
  nom a partir des premiers mots du prompt — donc identique pour toute la serie.

## Modele de page

`assets/exemple.html` est une page complete et minimale : deux prompts, le bloc
JSON, les titres numerotes et les blocs `<pre>` echappes. La lire avant d'ecrire
une page pour la premiere fois evite de reinventer la structure.

## Verification avant de livrer

Compter les elements du bloc JSON et les blocs `<pre>` visibles : les deux
nombres doivent etre egaux. En v3, verifier aussi que chaque ID cite par
`character_refs` ou `background_ref` existe dans `assets`, et en H3 Director que
chaque CUT a guillemets a des `speakers` (et qu'aucun figurant ne parle). Un prompt present a
l'ecran mais absent du JSON ne sera jamais charge, et c'est invisible a la
lecture de la page.
