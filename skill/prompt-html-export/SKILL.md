---
name: prompt-html-export
description: Produire une page HTML de prompts video (LTX, MiniMax H3, ou tout autre modele) qui se charge sans perte dans Workflows Q-builder via son bouton "Load prompts HTML". Declencher des que David demande une serie de prompts presentee en page HTML, une planche de prompts, un HTML de prompts a charger dans l'app, ou qu'il parle de "load prompts HTML", "page de prompts", "8 prompts en HTML". Declencher aussi pour corriger une page existante dont les prompts ne remplissent pas les champs de l'app.
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
`character_refs` ou `background_ref` existe dans `assets`. Un prompt present a
l'ecran mais absent du JSON ne sera jamais charge, et c'est invisible a la
lecture de la page.
