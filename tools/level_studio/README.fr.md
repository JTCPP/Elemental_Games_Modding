# Azurik Level Studio 2.1.1

**Version 2.1.1** de l’éditeur de niveaux d’**Azurik: Rise of Perathia**, pour votre propre copie du jeu Xbox.

[Télécharger pour Windows](https://github.com/Azurik-Modding-Hub/Azurik-Level-Editor/releases/tag/v2.1.1) · [English guide](README.en.md) · [Niveaux et construction ISO](docs/LEVELS_V21.md#français) · [Illustrations](docs/SCREENSHOTS.md) · [Feuille de route](ROADMAP.md)

![Menus de l’éditeur version 2.0.0](docs/screenshots/v2-editor-menus.jpg)

## Correctif 2.1.1

- **Navigation séparée :** l’explorateur possède deux sections **Niveaux** et **Cinématiques**. Le dump européen étudié contient **24 niveaux et 9 cinématiques**, avec leurs propres sélecteurs et compteurs. Chaque section conserve sa dernière sélection ; l’ouverture ou la restauration d’une entrée sélectionne sa section. La gestion des niveaux et le choix du modèle source affichent les niveaux jouables.
- **Éclairage de D2 :** les lumières directionnelles utilisent désormais la conversion native de matrice en quaternion, y compris sous des transformations à échelle non uniforme. Les surfaces incorrectement assombries sont corrigées. Le quaternion calculé n’est pas normalisé ; seule la direction finale de la lumière l’est, comme dans le shader du jeu.
- **Vue intérieure de D2 :** la vue initiale s’ouvre dans l’enveloppe mauve de la cavité. D2 ne déclare pas de passe de ciel dédiée : ce ciel apparent fait partie du décor. L’enveloppe cubique extérieure verte d’origine est conservée et peut apparaître dans les vues extérieures.
- **Changement de langue :** les noms des niveaux personnalisés restent inchangés dans le sélecteur et l’en-tête de la vue.

![Vue intérieure de D2 et catalogue séparé dans la 2.1.1](docs/screenshots/d2-2.1.1.jpg)

Cette capture montre le rendu des données source et la séparation du catalogue dans l’éditeur. Elle ne constitue pas une validation du jeu dans Xemu. [Légendes des illustrations](docs/SCREENSHOTS.md#version-211--d2-and-separate-catalogue).

## Nouveautés 2.1.0

- **Menu Niveaux :** création d’un niveau natif depuis un modèle source propre, suppression d’un niveau du mod et restauration d’un niveau supprimé. La copie reprend géométrie, collisions et scripts d’origine ; elle ne reprend pas les modifications actuelles du projet. Ce n’est pas un compilateur de niveaux vides.
- **Entrées compatibles :** remplacement d’un niveau d’origine par un clone du même modèle, pour conserver les identifiants des points d’entrée. Le sélecteur et la salle d’entraînement sont protégés.
- **Historique global :** Ctrl Z et Ctrl Maj Z annulent/rétablissent une action dans l’ordre, y compris créations, suppressions, transformations et opérations d’assets. L’historique enregistré reste disponible après réouverture.
- **Export natif :** nouveaux XBR, mise à jour du registre `gamedata/index/index.xbr` et plan `iso-plan.json` pour décrire ajouts, remplacements et suppressions.
- **Construire une ISO du mod :** choix du dossier exporté, de l’ISO source correspondante et d’un nouveau chemin. Le logiciel met à jour les dépendances de préchargement `prefetch-lists.txt`, reconstruit les répertoires du disque et vérifie ses fichiers. L’ISO source et l’exécutable Xbox restent intacts.

**État de validation :** contrôles structurels, tests sur archives/disques synthétiques et vérifications de fichiers isolées. Le chargement des nouveaux niveaux, des portails et des états de quête dans **Xemu reste à tester en jeu**. Scripts, collisions et identifiants natifs sont hérités du modèle. [Utilisation et limites](docs/LEVELS_V21.md#français).

## Correctif 2.0.1

Les calculs d’éclairage, de réflexion et de limites des objets transparents sont réutilisés quand ils restent identiques, sans réduire le détail ni les textures. Sur un test local de mise à jour des matières d’A5, le temps processeur passe de **4,19 à 1,16 ms par image** (environ 72 % de moins) ; cette mesure ne représente pas les FPS du GPU. La cadence des textures dans une vue immobile est désormais distinguée des FPS pendant le déplacement. Les projets et les données du jeu sont conservés.

## Nouveautés 2.0.0

- Caméra libre par défaut, commandes et focus de la vue corrigés, regard sur place au clic droit ou avec les flèches.
- Menus visibles : fichier, édition, importation, affichage et aide aux raccourcis.
- **Restaurer le niveau** revient à l’état source du niveau actif en une opération qui peut elle-même être annulée.
- **Ctrl Z / Ctrl Maj Z** pour annuler/rétablir transformations, imports, remplacements, duplication et restauration.
- Import et remplacement de modèles statiques **OBJ, JSON de géométrie, glTF/GLB**, et duplication indépendante.
- Import de textures **PNG**, affectation aux modèles importés et remplacement des surfaces natives compatibles.
- Réduction des transferts répétés et du travail inutile dans la vue ; le décodage des gros niveaux prend encore du temps.
- Votre image **Perathia Modding Hub** devient l’icône de l’exécutable et de la fenêtre 2.0.0.

Les remplacements compatibles sont exportables dans les archives du jeu. Les nouveaux modèles, duplications et textures sans cible native restent des **aperçus du projet**, enregistrés et exportés avec leurs fichiers. [Règles précises](docs/EDITING_V2.md).

## Tester les modifications en jeu

EDIT : You need to clear the cache for the changes you make to take effect ! Don’t worry, this won’t affect your save files in any way!

Il s’agit **uniquement du cache Xbox** : **Clear Cache** dans xemu-dashboard ou **Flush Cache Partitions** dans LithiumX, puis redémarrage sur l’ISO modifiée. Conservez le disque dur virtuel et la partition E contenant les sauvegardes. [Instructions et références Xemu](docs/GAME_CACHE.md).

## Ouvrir le logiciel

1. Téléchargez **Azurik-Level-Studio-2.1.1-Windows-x64.zip** et extrayez-le.
2. Double-cliquez sur **Azurik Level Studio.exe**. L’exécutable inclut Python et les dépendances : aucune installation de Python n’est nécessaire.
3. Cliquez sur **Importer un ISO**, choisissez votre ISO/XISO Xbox d’Azurik ou saisissez son chemin. Attendez l’import, puis choisissez **Niveaux** ou **Cinématiques** dans l’explorateur et sélectionnez une entrée.
4. Sélectionnez un élément, déverrouillez-le si nécessaire, puis déplacez-le, tournez-le ou changez son échelle avec les outils ou les champs précis.
5. **Enregistrer** conserve le projet. **Exporter le mod** crée les nouveaux XBR, le registre si nécessaire, un plan ISO et un rapport. **Niveaux → Construire une ISO du mod**, également proposé après l’export, crée un disque de test séparé depuis cet export et votre ISO source correspondante.

Dans les sources, l’exécutable est dans `windows/Azurik Level Studio.exe`. **Ouvrir Azurik Level Studio.bat** permet aussi de le lancer ; les lanceurs `.cmd` et PowerShell sont conservés. Le logiciel ouvre sa propre fenêtre. Windows x64, .NET Framework et le [runtime WebView2](https://developer.microsoft.com/microsoft-edge/webview2/) sont requis ; ce dernier est généralement installé.

La version compilée conserve projets, imports, cache, exports et journaux dans **`%LOCALAPPDATA%\AzurikLevelStudio`**. Elle réutilise un serveur de même version ; un serveur plus ancien reste ouvert et la nouvelle version utilise un port distinct. En mode source, les données restent dans le dossier du code.

Sauvegardez une copie de votre projet avant une mise à jour. Les opérations d’assets utilisent le format de projet 2 ; la gestion des niveaux utilise le format 3. Les projets existants de format 1 et 2 peuvent être ouverts. Fermez l’ancien éditeur avant de modifier le même projet.

## Fonctionnalités

- Décors, modèles placés, textures, matières et références en 3D ; montagnes et structures statiques ; ciel jour/nuit.
- Catalogues distincts : 24 niveaux et 9 cinématiques dans le dump européen étudié ; la disponibilité et les compteurs suivent votre disque et votre projet.
- Interface français/anglais sans rechargement du niveau.
- Caméra orbitale ou libre, vues perspective/dessus/face/côté, cadrage et regard à 360° sur place.
- Déplacement, rotation, échelle, champs précis, annuler/rétablir et restauration du placement source.
- Verrouillage individuel ou du niveau, enregistré et contrôlé par le serveur.
- Bibliothèque d’assets avec recherche, séquences et cubemaps ; export trié PNG/glTF avec l’outil fourni.
- Import ISO local, progression, validation et projets séparés.
- Gestion native des niveaux et construction d’ISO : clones propres, entrées compatibles, suppressions réversibles et nouveau disque vérifié séparément.
- Captures PNG de la vue enregistrées dans le projet.
- Véritable exécutable Windows, icône, version et empreinte SHA-256.

## Commandes

| Action | Commande |
| --- | --- |
| Orbiter / zoomer | Clic gauche + glisser / molette |
| Cadrer la sélection / le niveau | F / Home |
| Avancer / gauche / reculer / droite | **Z / Q / S / D** en français ; **W / Q / S / D** en anglais |
| Regarder sans se déplacer | Clic droit + glisser ou flèches en caméra libre |
| Monter / descendre | Espace / Ctrl |
| Vitesse | Lent / Normal / Rapide ; Maj accélère, Alt ralentit |
| Déplacer / tourner / redimensionner | W / E / R hors touches consommées par la caméra |
| Annuler / rétablir / enregistrer | Ctrl Z / Ctrl Maj Z (ou Ctrl Y) / Ctrl S |

Cliquez dans la vue avant de naviguer. Les lettres suivent le clavier, y compris AZERTY. Les champs de texte et dialogues suspendent les déplacements. La langue traduit l’interface ; les noms des ressources restent conservés.

## Export et limites

Le dump et l’ISO d’origine sont lus sans modification. Les placements vérifiés sont exportés dans de **nouveaux XBR** avec empreintes et rapport. Les autres transformations sont signalées **aperçu seulement**, enregistrées séparément dans `scene-overrides.json` et ne sont pas appliquées au jeu.

Les collisions restent à leur position originale quand un décor est déplacé : vérifiez le résultat en jeu. La 2.1.0 peut construire une nouvelle ISO depuis le mod exporté et l’image d’origine correspondante. Le rapport vérifie la structure et les fichiers du disque, puis indique explicitement `imageTestedInGame: false`. Une construction réussie ne certifie pas le fonctionnement du jeu.

Un nouveau niveau conserve géométrie, collisions, scripts, identifiants de portails et de départ, ainsi que les liens aux états de quête du modèle. Sa famille affichée est un classement dans le catalogue. La création n’ajoute pas automatiquement un portail ou une entrée de campagne. Pour le tester par une entrée existante, supprimez son niveau d’origine du mod en choisissant un clone de ce même modèle comme remplacement. Le remappage entre modèles différents et la création arbitraire de niveaux vides ne sont pas disponibles. [Guide des niveaux](docs/LEVELS_V21.md#français).

Le rendu utilise les données du jeu sans exécuter tout le moteur Xbox. Personnages en pose de liaison, scripts, animations de squelette, particules, éclairages ponctuels et certains effets restent partiels. Ciel et chemin vérifié du brouillard/reflet à deux couches d’A5 sont pris en charge. Un rendu identique pixel par pixel n’est pas annoncé.

## Sources et prochaine version

Pour la fenêtre native : Python 3.10+, `python -m pip install -r requirements-desktop.txt`, puis `python desktop.py`. Utilisez `--source` pour un dump extrait. Le navigateur reste disponible avec `python server.py --open` après installation de `requirements.txt`.

Pour compiler : Windows x64, Python 3.11+ et **Build Windows.ps1**. Tests : `python -m pytest -q` ; Node.js est requis uniquement pour les tests de l’interface. Les fichiers du jeu ne sont pas inclus.

Validation de la 2.1.1 le 10 octobre 2026 : **766 tests réussis, 3 ignorés** en 70,69 secondes. Ces contrôles automatisés ne certifient pas le fonctionnement du jeu dans Xemu.

L’import, le remplacement et la duplication sont disponibles dans la 2.0.0 avec les limites d’export décrites dans [le guide V2](docs/EDITING_V2.md). Les ajouts arbitraires dans le jeu et les collisions restent dans [ROADMAP.md](ROADMAP.md).

[Dépôt dédié](https://github.com/Azurik-Modding-Hub/Azurik-Level-Editor) · [Expensions](https://github.com/Azurik-Modding-Hub/Elemental_Games_Modding---Expensions) · [Pull request en anglais](https://github.com/JTCPP/Elemental_Games_Modding/pull/2)
