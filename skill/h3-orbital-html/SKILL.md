---
name: h3-orbital-html
description: Générer des batchs HTML portables pour le mode MiniMax H3 > Orbital de Workflows Q-Builder, avec images start, character sheets, decor sheets et trajectoires par scène.
---

# HTML H3-Orbital pour Q-Builder

Utiliser ce skill quand David demande une page HTML de prompts H3-Orbital, une série de mouvements de caméra sur des images, ou un batch à charger avec « Load prompts HTML » dans Q-Builder. Le livrable est un fichier `.html` autonome. Ne pas modifier ni démarrer les workers ComfyUI : Claude prépare le batch, Q-Builder s'occupe des uploads et de la queue.

## Contrat exact avec Q-Builder

Le HTML contient un unique `<script type="application/json" data-prompts>` avec un objet JSON :

```json
{
  "schema": "qbuilder-html/orbital-v1",
  "engine": "orbital",
  "orbit_defaults": {"length": 73, "megapixels": 0.58, "steps": 28, "output_prefix": "H3-Orbital/Projet"},
  "assets": {
    "depart_01": {"filename": "depart_01.png", "data_url": "data:image/png;base64,..."},
    "personnage_face": {"filename": "face.jpg", "data_url": "data:image/jpeg;base64,..."},
    "decor_planche": {"filename": "decor.jpg", "data_url": "data:image/jpeg;base64,..."}
  },
  "scenes": [
    {
      "name": "01_orbite_droite",
      "seed": 904231,
      "orbit": {
        "motion": "orbit360",
        "length": 73,
        "start_image": "depart_01",
        "character_refs": ["personnage_face"],
        "set_refs": ["decor_planche"],
        "prompt": "Smooth constant camera motion, no acceleration or deceleration."
      }
    }
  ]
}
```

L'asset `start_image` est obligatoire **dans chaque scène**. `character_refs` et `set_refs` sont facultatifs, chacun avec 0 à 4 IDs. Pour une seule image start, les deux tableaux sont vides ou absents : ne pas inventer de sheets. Les IDs sont des clefs de `assets`, jamais des noms de fichiers ComfyUI. Une scène n'envoie au worker que les images qu'elle cite. `name` doit être unique, court, ordonné (`01_`, `02_`) et compatible avec un nom de fichier. Limite : 20 scènes.

Chaque asset embarque réellement son image dans `data_url` (`data:image/png;base64,...`, `data:image/jpeg;base64,...` ou `data:image/webp;base64,...`). Ne jamais mettre seulement un chemin voisin du HTML, une URL NAS, un lien `file://` ou un nom de fichier : le navigateur ne peut pas les importer silencieusement. Chaque image doit être sous 25 Mo. Ne pas réencoder une image si l'original peut être embarqué tel quel. Ne pas prétendre qu'un HTML est prêt si les base64 sont encore des points de suspension.

## Presets `motion`

| Valeur | Effet demandé | Particularité |
|---|---|---|
| `orbit360` | tour complet en boucle | mode Orbit, première et dernière image rapprochées |
| `orbit360_reverse` | tour complet sens inverse | mode Orbit + miroir |
| `half-right` / `half-left` | demi-cercle autour du sujet | mode Path, s'arrête derrière |
| `quarter-push-right` / `quarter-push-left` | quart de cercle en avançant | mode Path |
| `pan360` | rotation sur l'axe, sans translation | expérimental : le modèle ne garantit pas le 360 exact |
| `custom` | trajectoire libre | décrire exactement le mouvement dans `prompt` |

Pour `half-*`, gauche/droite se rapportent au personnage face caméra. Ne pas appeler `pan360` une orbite. Si David exige une rotation sur trépied, choisir `pan360` et signaler son caractère expérimental. Les trajectoires génératives ne sont pas des déplacements géométriquement garantis.

`length` est un nombre d'images à 24 images/s, pas une durée en secondes : `73` ≈ 3 s, `124` ≈ 5 s, `175` ≈ 7 s, `243` ≈ 10 s, `345` ≈ 14 s. Laisser `73` pour les premiers tests ; `345` peut faire monter fortement la RAM. Seules ces cinq valeurs sont exposées dans Q-Builder. `megapixels` doit être entre 0 et 0,985 ; préférer 0,58 au départ. `steps` vaut 28 par défaut. Une scène peut remplacer `orbit_defaults.length` par son propre `orbit.length`.

Le champ `prompt` décrit le sujet et la cinématique sans inventer des zones du décor absentes des références. Pour une animation linéaire : « constant angular speed, no acceleration or deceleration ». Les sheets servent d'ancrage visuel, pas de plusieurs premières images. Ne pas y mettre toute la grille 3x3 comme `start_image` si un cadre précis est demandé : extraire d'abord la case de départ et l'embarquer comme asset distinct.

## Présentation et contrôle

Autour du JSON invisible, rendre la page lisible avec un titre, une fiche par scène, le nom, le preset, le nombre d'images, la liste des IDs utilisés et le prompt. Échapper `&` et `<` dans la partie visible. Dans le JSON, remplacer tout `</script>` par `<\/script>` pour éviter de fermer la balise.

Avant livraison : parser le JSON embarqué ; vérifier que `engine` vaut `orbital`, que chaque scène a une start, que tous les IDs référencés existent, que les base64 décodent et correspondent à un fichier image, que les listes ne dépassent pas quatre, que les noms sont uniques, et que la page contient autant de fiches visibles que de scènes. Charger le HTML dans Q-Builder, vérifier que le moteur bascule sur Orbital, que les scènes affichent leurs images/IDs, puis utiliser « Générer la commande » pour confirmer qu'aucune image n'est signalée comme manquante. Ne pas lancer de rendu réel sans demande explicite.

Un exemple minimal se trouve dans `assets/exemple.html`. Ses images minuscules ne servent qu'à valider le format, pas à générer une vidéo.
