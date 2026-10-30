# Préparer un HTML pour les fonctions récentes de Q-Builder

Ce guide complète `prompt-html-export/SKILL.md`. Il concerne MiniMax H3 >
Director. Pour Orbital, lire le skill `h3-orbital-html` du dépôt ; ne pas
mélanger `qbuilder-html/v3` et `qbuilder-html/orbital-v1`.

## Contrat Director

Le HTML autonome contient un seul bloc
`<script type="application/json" data-prompts>` avec un objet
`{"schema":"qbuilder-html/v3","model":"h3-director", "assets":{...},
"scenes":[...]}`. Q-Builder et l'API locale acceptent au plus 20 scènes.
Chaque asset est une image JPEG, PNG ou WebP embarquée en `data_url` base64
(25 Mo maximum par image) ; un chemin vers un fichier voisin ou sur le NAS ne
suffit pas. L'ID d'asset est unique ; deux images de même nom doivent avoir
des IDs et des données distincts.

Dans chaque scène, `character_refs` liste les IDs des personnages dans
l'ordre des `<Subject 1..N>`. `background_ref` est l'ID facultatif du décor.
Une scène peut sélectionner zéro référence explicitement ; ne pas lui
réinjecter les images communes. La limite est de neuf références par scène,
pas neuf pour tout le catalogue. Le workflow et l'upload n'utilisent que les
assets sélectionnés par cette scène. Chaque CUT porte `seconds`, `text` et
`speakers` ; les numéros des locuteurs sont locaux à la scène. Un CUT contenant
une réplique entre guillemets doit déclarer ses locuteurs. Maximum 15 s par
scène H3 Director sur ce parc.

`output_name` est facultatif, mais indispensable pour conserver un nom de
sortie précis ou créer une suite `_B`. Exemple :

```json
{
  "name": "01_le_garage_de_nuit",
  "output_name": "h3_queue_01_01_le_garage_de_nuit_B",
  "character_refs": ["gardien"],
  "background_ref": "garage",
  "cuts": [{"seconds": 5, "text": "<Subject 1> enters the garage.", "speakers": []}]
}
```

Q-Builder produit alors une base `Muse/Video/h3_queue_01_01_le_garage_de_nuit_B`
et le film habituel `h3_queue_01_01_le_garage_de_nuit_B_00001-audio.mp4`.
Ne pas inclure `_00001-audio.mp4` dans `output_name`. La casse, `_`, `-` et
`.` sont conservés ; les autres caractères sont normalisés en `_`. Vérifier
que les noms restent uniques **après** normalisation : l'API refuse un doublon
avec HTTP 422. Sans champ, le nom automatique reste lié au lot.

## Générer et contrôler

Préparer une spec JSON puis utiliser `scripts/qbuilder_page.py` : `--check`
valide sans écrire, l'appel sans option produit le HTML indiqué par `out`.
Le générateur embarque les images et recopie `output_name` par scène. Ne pas
annoncer la page prête si les `data_url` ne décodent pas ou si les références
du JSON ne correspondent pas aux images visibles.

Dans Q-Builder, utiliser « Load prompts HTML », choisir MiniMax H3 > Director,
vérifier le casting et le décor de chaque scène, puis générer la commande.
Pour l'API locale, appeler `/api/batch/validate` sur le chemin HTML, puis
`POST /api/batch` avec `dry_run:true` et vérifier les `output_name` renvoyés
avant toute soumission. Le chemin HTML et la destination de copie doivent
être sous les `api_allowed_roots` du poste de commande. Ne jamais convertir
un refus 403 en contournement de chemin.

`copy_results` doit être explicite si une copie est attendue : fournir un
dossier local ou NAS autorisé. `copy_results:""` désactive la copie. La copie
ne supprime pas les originaux des workers ; confirmer le rapatriement par le
statut du batch et les chemins locaux renvoyés, pas par la seule acceptation
du batch. La répartition automatique dépend des workers disponibles et n'est
pas une garantie d'une scène par PC. Ne jamais lancer des rendus sans demande
explicite ; validation et dry-run ne lancent aucun calcul.

## Frontière vidéo/audio

Le JSON HTML v3 ne définit actuellement que des **assets image**. Ne pas
mettre un MP4/WAV en `assets.data_url` ni promettre son import par « Load
prompts HTML ». Les `ref_videos` et `ref_audios` sont pris en charge dans les
briefs H3 Director `.json` du lanceur `queue_h3_multishot.py --queue` : le
lanceur téléverse ces fichiers au worker choisi et réécrit `file`/`fileName`
du timeline. Une reprise refait ce transfert. Si un lot exige ces médias,
préparer un brief Director pour ce lanceur et demander à David avant un vrai
envoi réseau. Le dossier de retour est passé séparément par `--copy-results`.
