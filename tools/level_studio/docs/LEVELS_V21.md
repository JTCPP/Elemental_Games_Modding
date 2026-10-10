# Level management and mod ISO construction in 2.1.x

[English editor guide](../README.md) · [Guide français](../README.fr.md)

## Levels and cinematics

Since 2.1.1, choose **Levels** or **Cinematics** in the Explorer before selecting an entry. Their selectors, counts and last selections are separate. The inspected European dump has 24 levels and 9 cinematics; your source and custom project entries determine the displayed counts. Level management and source-template selection show gameplay levels. Custom gameplay clones remain in Levels even when their chosen display family is cinematic.

## What a new level contains

**Levels → Create a level from a template** creates a separate registered native level from your own source dump. Choose a source level, a unique ID such as `my_water`, a name and a family. IDs use 1–40 lowercase letters, digits or underscores and begin with a letter; names contain at most 80 characters. Reserved names and existing IDs are rejected.

The template is copied from its original source archive. Current transforms and asset edits in the project are not copied. A clone of another custom level retains the same original template ancestry. The selected family groups the level in the editor; it does not change its game logic.

This copies the template's geometry, collisions, scripts, portal/spawn IDs and quest-state links. It does not compile an empty map, invent new entry points or give the clone independent quest IDs. Subsequent supported edits can be applied to the clone. The existing [model/texture export limits](EDITING_V2.md) still apply.

## Reach a clone through an existing game entry

Creating a registered level alone does not add a portal or campaign-menu entry. To use an existing level entrance, select **Remove a level from the mod** and choose a retained replacement with the **same original template**. For example:

1. Create `my_water` from **W1**.
2. Make supported edits in `my_water` and save the project.
3. Remove **W1** from the mod and choose `my_water` as its replacement.
4. Export the mod and build a new ISO as described below.

The export redirects W1's native level keys to the clone. Its inherited entry-point identities remain compatible. A clone of A5 cannot replace W1 through this workflow. If no compatible destination exists, create a clone of the level first. Chains of replacements resolve to a retained destination; cycles and missing destinations are rejected.

The selector (`selector`) and training room (`training_room`) are protected from removal. Removing a level changes the project/mod catalogue, not the original dump. An original XBR is omitted from the rebuilt disc only when its other registered resources are not still needed. Custom levels removed before export are not written as additions.

## Restore, undo and save

**Levels → Manage levels** lists retained and removed levels. **Restore removed level** returns a removed level to the mod catalogue. This differs from **Edit → Restore original level**, which resets the active level's scene/asset changes to its source state.

**Ctrl Z** undoes one action; **Ctrl Shift Z** redoes it. The order spans level creation/removal/restoration, scene transforms and asset edits, even when the action affected another level. Text fields keep their ordinary text-editing shortcuts. A new edit clears the previous redo branch.

Save retains the project and its history. Format 3 stores level management; existing format 1 and 2 projects remain readable. Custom archives and data required by undo stay in the project so a reopened session can replay its history. Close older editor versions before editing that same project.

## Export and construct a new ISO

1. Choose **Export mod**. Keep the complete export folder together.
2. Choose **Levels → Build a mod ISO**, or the same button in the export result.
3. Enter that project's exported folder and the original ISO/XISO corresponding to its source dump.
4. Choose a **new** `.iso` or `.xiso` path in an existing destination folder, outside the source dump. Existing ISO/report files are not overwritten.
5. Start construction and wait for file verification. The job runs locally. Closing the dialog stops its progress polling; the construction continues and its dialog can be reopened to resume tracking.

The export folder includes the changed/custom XBRs, the updated `gamedata/index/index.xbr` when level management changes registrations, `iso-plan.json`, and the ordinary export report. The explicit plan lists changed files and their hashes, required removals, and level operations. It is not a patch to the original Xbox executable.

During ISO construction, the builder applies the plan to the matching original disc and updates the native `prefetch-lists.txt` dependencies for cloned levels and retained replacement aliases. It rebuilds the XDVDFS directories, keeps unrelated assets, then reopens the new disc and verifies the files. Source mismatches, missing dependencies or unsafe paths stop the build instead of silently omitting a required change. The source dump, source ISO and saves remain intact.

A report beside the output ISO records checksums and verification results. Read its warnings as well as the export report. Game-exportable changes and preview-only model/texture additions remain distinct: previews do not become native game assets merely because an ISO is built.

## Validation and the first Xemu test

Automated checks cover synthetic native archives/indexes, aliases, persisted history, prefetch commands, protected output paths, directory reconstruction and file readback. These are structural and isolated checks. They do not run the commercial game engine.

**Xemu runtime validation of new levels remains to be performed.** After selecting the new ISO, check ordinary level loading, the original entrance/spawn, return portals, inherited quest state, collisions and supported edits. Existing scripts and IDs are shared with the template; visual model movement still does not move its collision mesh. A structurally verified disc may expose a gameplay limitation that these checks cannot simulate. The ISO report deliberately records `imageTestedInGame: false`.

EDIT : You need to clear the cache for the changes you make to take effect ! Don’t worry, this won’t affect your save files in any way!

Clear only the Xbox game cache partitions, keep the virtual hard drive and E partition containing saves, then restart with the modified ISO and load an ordinary game save. An older emulator snapshot can retain the previously loaded world. [Cache instructions](GAME_CACHE.md).

## Français

### Niveaux et cinématiques

Depuis la 2.1.1, choisissez **Niveaux** ou **Cinématiques** dans l’explorateur avant de sélectionner une entrée. Sélecteurs, compteurs et dernières sélections sont séparés. Le dump européen étudié comporte 24 niveaux et 9 cinématiques ; les fichiers source et les niveaux personnalisés du projet déterminent les compteurs affichés. La gestion des niveaux et le choix du modèle source affichent les niveaux jouables. Un clone jouable reste dans Niveaux, même si sa famille d’affichage choisie est cinématique.

### Créer un niveau depuis un modèle

**Niveaux → Créer un niveau depuis un modèle** crée un niveau natif distinct depuis votre dump source. Choisissez le modèle, un identifiant unique comme `mon_eau`, un nom et une famille. L’identifiant utilise 1 à 40 lettres minuscules, chiffres ou `_`, avec une lettre au début ; le nom contient au maximum 80 caractères. Les noms réservés et identifiants déjà utilisés sont refusés.

La copie reprend l’archive source propre, sans les transformations ou modifications d’assets actuelles du projet. Un clone de niveau personnalisé conserve l’origine du même modèle. La famille sert au classement dans l’éditeur, sans modifier la logique du jeu.

Géométrie, collisions, scripts, identifiants de départ/portail et liens aux états de quête sont hérités. Cette fonction ne compile pas une carte vide, n’invente pas d’entrées et n’attribue pas de nouveaux identifiants de quête indépendants. Vous pouvez ensuite appliquer les modifications compatibles au clone. Les [limites d’export des modèles/textures](EDITING_V2.md#français) restent applicables.

### Utiliser une entrée existante

La création seule n’ajoute pas de portail ou d’entrée au menu du jeu. Pour tester un clone par une entrée existante :

1. Créez `mon_eau` depuis **W1**.
2. Modifiez `mon_eau` avec les outils compatibles, puis enregistrez.
3. Choisissez **Supprimer un niveau du mod** pour **W1**, avec `mon_eau` comme remplacement.
4. Exportez le mod et construisez une nouvelle ISO.

Les clés natives de W1 sont redirigées vers le clone, dont les identifiants de points d’entrée restent compatibles. Le remplacement doit venir du **même modèle d’origine** : un clone d’A5 ne peut pas remplacer W1 par cette fonction. Sans destination compatible, créez d’abord un clone du niveau. Les chaînes de remplacement aboutissent à un niveau conservé ; les cycles et destinations absentes sont refusés.

Le sélecteur (`selector`) et la salle d’entraînement (`training_room`) sont protégés. La suppression agit sur le projet/mod, sans effacer le dump source. Une archive d’origine n’est retirée du nouveau disque que si ses autres ressources enregistrées ne sont plus nécessaires. Un niveau personnalisé supprimé avant l’export n’est pas ajouté au disque.

### Restaurer et reprendre l’historique

**Niveaux → Gérer les niveaux** permet de restaurer un niveau supprimé. Cette action diffère de **Édition → Restaurer le niveau d’origine**, qui remet les modifications visuelles et d’assets du niveau actif à leur état source.

**Ctrl Z** annule une action ; **Ctrl Maj Z** la rétablit. L’ordre est global entre gestion des niveaux, transformations et opérations d’assets, même pour un autre niveau. Les champs conservent leurs raccourcis d’édition de texte. Une nouvelle modification efface la branche de rétablissement précédente.

Le projet enregistré conserve son historique et les archives nécessaires à sa reprise après réouverture. La gestion des niveaux utilise le format 3 ; les projets de format 1 et 2 restent lisibles. Fermez les anciennes versions avant de modifier le même projet.

### Construire le disque du mod

1. Utilisez **Exporter le mod** et conservez son dossier complet.
2. Choisissez **Niveaux → Construire une ISO du mod**, ou le bouton du résultat d’export.
3. Indiquez le dossier exporté par ce projet et l’ISO/XISO d’origine correspondant au dump.
4. Choisissez un **nouveau** chemin `.iso` ou `.xiso`, dans un dossier existant, hors du dump source. Les ISO et rapports existants ne sont pas écrasés.
5. Attendez la construction et la vérification des fichiers. Fermer le dialogue arrête son suivi, sans arrêter le travail ; le rouvrir permet de reprendre le suivi.

L’export contient les XBR modifiés/personnalisés, le registre `gamedata/index/index.xbr` mis à jour si nécessaire, `iso-plan.json` et le rapport. Le plan décrit fichiers, empreintes, suppressions et opérations sur les niveaux. La construction met à jour le préchargement natif `prefetch-lists.txt`, reconstruit les répertoires XDVDFS et relit les fichiers du nouveau disque. Les sources incompatibles, dépendances absentes et chemins non sûrs sont refusés. L’exécutable Xbox n’est pas modifié.

Le rapport placé à côté de l’ISO donne les résultats et empreintes. Consultez aussi ses avertissements. Les ajouts de modèles/textures marqués **aperçu seulement** restent des aperçus, même après construction d’une ISO. Dump, ISO source et sauvegardes sont conservés.

### Ce qui reste à tester dans Xemu

Les tests automatisés valident des archives/registres synthétiques, les alias, l’historique enregistré, le préchargement, les chemins de sortie et la relecture des fichiers. Ils restent des contrôles structurels et isolés.

**La validation des nouveaux niveaux dans Xemu reste à effectuer en jeu.** Vérifiez chargement normal, arrivée et départ du joueur, portails de retour, états de quête hérités, collisions et modifications compatibles. Les scripts et identifiants du modèle restent partagés ; déplacer un modèle visuel ne déplace toujours pas ses collisions. Le rapport indique volontairement `imageTestedInGame: false`.

EDIT : You need to clear the cache for the changes you make to take effect ! Don’t worry, this won’t affect your save files in any way!

Videz uniquement les partitions de cache Xbox, conservez le disque dur virtuel et la partition E des sauvegardes, redémarrez sur la nouvelle ISO, puis chargez une sauvegarde normale du jeu. Un ancien état instantané de l’émulateur peut garder le monde précédent. [Instructions de cache](GAME_CACHE.md#français).
