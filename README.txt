Workflows Q-builder (ex LTX Prompt Builder)
===========================================

Double-clique Lancer_Workflows_Q_Builder.bat pour lancer l'app locale.
L'ancien lanceur (Lancer_LTX_Prompt_Queue_Builder.bat) ouvre toujours
l'ancienne interface LTX seule, sur le meme port.

Adresse locale:
http://127.0.0.1:8765/

ComfyUI doit tourner sur:
http://127.0.0.1:8188/

Modeles
-------
LTX (2.5 / 2.3)              prompts multishot -> queue_ltx_multishot.py
MiniMax H3 - Director        CUTs + images de reference -> queue_h3_multishot.py
MiniMax H3 - T2V simple      un prompt par scene -> queue_ltx_multishot.py

Contenu
-------
- workflows_q_builder.html      : interface
- workflows_q_builder_server.py : serveur local de l'app
- queue_ltx_multishot.py        : envoie les jobs LTX et H3 T2V a ComfyUI
- queue_h3_multishot.py         : construit / envoie les workflows H3 Director
- h3_director_build.py          : brief H3 -> workflow (copie du skill muse-h3-director)
- h3_director_api.json          : workflow H3 Director au format Export (API), pour la file
- h3_template_scout_v1.json     : template H3 Director au format UI, pour les .json a glisser
- h3_t2v_api.json               : workflow MiniMax H3 T2V natif (format API)
- ltx*.json                     : presets workflows LTX (format API)
- ltx_builder_uploads           : images copiees par l'app
- ltx_builder_workflows         : workflows charges via Parcourir
- h3_workflows                  : workflows H3 produits en mode fichiers

Regles H3 a ne pas contourner
-----------------------------
- Un rendu H3 = 15 secondes maximum. Au-dela, le second chunk fait OOM
  (32 Go de VRAM, reproduit plusieurs fois). Decoupe en deux scenes _a / _b.
- Les tags <Subject 1>, <Subject 2>... s'ecrivent en toutes lettres dans le
  texte des CUTs. Aucune resolution semantique n'est faite.
- Ancre le lieu dans chaque CUT, meme en gros plan, sinon H3 reprend le decor
  de la planche de reference du personnage.
- L'ordre des personnages fixe la numerotation des Subject. Un slot sans image
  est ignore a la compilation et decale tous les suivants.
- Lance un seul rendu en test avant d'empiler un lot: un job qui OOM ne bloque
  pas la file, les suivants partent et echouent pareil.

Pour les workflows charges manuellement, utilise ComfyUI Export (API).
En mode H3 Director "fichiers .json", c'est au contraire un workflow UI qu'il
faut, puisque les fichiers produits se deposent dans ComfyUI.
