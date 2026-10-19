# Images communes ou jeux independants

Les IDs ci-dessous illustrent le contrat v3 ; remplacer les assets par les
vraies images avant de generer une page avec qbuilder_page.py.

Une image commune a trois prompts :
```json
[
  {"name":"01_a","character_refs":["alice"]},
  {"name":"02_b","character_refs":["alice"]},
  {"name":"03_c","character_refs":["alice"]}
]
```
Chaque scene utilise `<Subject 1>` pour Alice.

Trois jeux de trois images :
```json
[
  {"name":"01_a","character_refs":["alice","bruno","chloe"]},
  {"name":"02_b","character_refs":["david","emma","farid"]},
  {"name":"03_c","character_refs":["gisele","hugo","ines"]}
]
```
Dans le deuxieme prompt, David est `<Subject 1>` et Emma `<Subject 2>`.
Ne pas continuer la numerotation globale avec `<Subject 4>` et `<Subject 5>`.
Un decor distinct utilise `background_ref` et ne devient pas un personnage.

Dans Q-Builder : charger l'image commune dans le catalogue et laisser les
prompts sur « Images communes ». Pour l'autre cas, passer chaque prompt sur
« Choisir les images de ce prompt », puis importer/cocher ses trois images.
La generation de texte conserve les jeux d'images deja choisis.
