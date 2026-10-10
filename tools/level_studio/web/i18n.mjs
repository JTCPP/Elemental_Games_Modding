// UI translation only. Identifiers, source names, file paths and game data
// remain verbatim; switching language never changes the imported game files.
import { levelMessages } from './level-messages.mjs';
export const languageStorageKey = 'azurik-studio-language';
const supported = new Set(['fr', 'en']);
let language = 'fr';
let activeDocument = null;
let observer = null;
const listeners = new Set();
const textSources = new WeakMap();
const attributeSources = new WeakMap();

// French is the existing UI's canonical language. Exact entries keep source
// names safe: we never use unrestricted word substitutions on arbitrary data.
export const messages = Object.freeze({
  ...levelMessages,
  'Coordonnées du modèle': 'Model coordinates',
  'Coordonnées locales du modèle': 'Local model coordinates',
  'Coordonnées du niveau (monde)': 'Level coordinates (world)',
  'Menus de l’éditeur': 'Editor menus',
  'Fichier': 'File',
  'Édition': 'Edit',
  'Importation': 'Import',
  'Aide et raccourcis': 'Help and shortcuts',
  'Caméra libre / orbitale · Tab': 'Free / orbit camera · Tab',
  'Caméra libre / orbitale': 'Free / orbit camera',
  'Tab · vue 3D active': 'Tab · 3D view focused',
  'Édition → Restaurer le niveau d’origine': 'Edit → Restore original level',
  'Importer / remplacer modèles et textures': 'Import / replace models and textures',
  'Menu Importation': 'Import menu',
  'Cliquez dans la vue 3D pour activer les déplacements. Les raccourcis de navigation sont suspendus dans les champs de texte et les fenêtres de dialogue.': 'Click in the 3D view to move. Navigation shortcuts are suspended in text fields and dialogs.',
  'Caméra libre · déplacement et regard': 'Free camera · move and look',
  'Caméra orbitale · clic gauche + glisser': 'Orbit camera · left mouse drag',
  'Déplacer · W (caméra orbitale)': 'Move · W (orbit camera)',
  'W / E / R · caméra orbitale / repère monde / local': 'W / E / R · orbit camera / world / local space',
  'Rétablir · Ctrl Maj Z': 'Redo · Ctrl Shift Z',
  'Ctrl Z / Ctrl Maj Z': 'Ctrl Z / Ctrl Shift Z',
  'Ctrl Maj Z': 'Ctrl Shift Z',
  'Ctrl Maj E': 'Ctrl Shift E',
  'Suppr': 'Delete',
  'Dupliquer la sélection': 'Duplicate selection',
  'Supprimer l’ajout sélectionné': 'Delete selected addition',
  'Restaurer le niveau d’origine': 'Restore original level',
  'Restaurer ce niveau': 'Restore this level',
  'Importer un modèle': 'Import a model',
  'Remplacer le modèle sélectionné': 'Replace selected model',
  'Importer une texture PNG': 'Import a PNG texture',
  'Remplacer une texture du niveau': 'Replace a level texture',
  'Remplacer cette texture': 'Replace this texture',
  'Cadrer la sélection': 'Frame selection',
  'Fichier à importer': 'File to import',
  'Nom de l’asset': 'Asset name',
  'Orientation du modèle': 'Model orientation',
  'Z vers le haut · Azurik': 'Z up · Azurik',
  'Y vers le haut · glTF standard': 'Y up · standard glTF',
  'Texture du modèle': 'Model texture',
  'Texture à remplacer': 'Texture to replace',
  'Conserver la texture · ou sans texture': 'Keep texture · or no texture',
  'Lecture de l’asset…': 'Reading asset…',
  'Importer': 'Import',
  'Remplacer': 'Replace',
  'sommets': 'vertices',
  'Repos': 'Idle',
  'Revenir au niveau': 'Return to level',
  'Asset importé': 'Asset imported',
  'Asset remplacé': 'Asset replaced',
  'Modèle dupliqué': 'Model duplicated',
  'Modèle remplacé': 'Model replaced',
  'Export dans le jeu pris en charge': 'Game export supported',
  'Ajout du projet · aperçu': 'Project addition · preview',
  'Texture du projet · aperçu et export d’assets uniquement.': 'Project texture · preview and asset export only.',
  'Modification compatible avec l’export dans le jeu.': 'Change compatible with game export.',
  'Ajout conservé dans le projet et l’export d’assets. Aperçu uniquement dans le niveau ; non intégré au jeu.': 'Addition saved in the project and asset export. Level preview only; not included in the game.',
  'Ctrl Z annule cette opération ; Ctrl Maj Z la rétablit.': 'Ctrl Z undoes this operation; Ctrl Shift Z redoes it.',
  'Ajout supprimé · Ctrl Z pour le restaurer.': 'Addition deleted · Ctrl Z to restore it.',
  'Niveau d’origine restauré · Ctrl Z pour annuler.': 'Original level restored · Ctrl Z to undo.',
  'Bloc d’aperçu sauvegardé dans le projet et l’export d’assets. Ce bloc n’est pas intégré dans les fichiers du jeu.': 'Preview object saved in the project and asset export. This object is not included in the game files.',
  'Rétablir les positions, transformations, modèles et textures du niveau actif depuis le dump source, puis supprimer les ajouts du niveau. Les autres niveaux sont conservés. Ctrl Z permet de récupérer vos modifications.': 'Restore positions, transforms, models and textures in the active level from the source dump, then remove level additions. Other levels are preserved. Ctrl Z recovers your changes.',
  'OBJ, glTF 2.0, GLB ou JSON · 12 Mo maximum. Pour un glTF, sélectionnez aussi son fichier .bin. La géométrie est assemblée ; choisissez la texture ci-dessous. Les ajouts et duplications restent des aperçus. Un remplacement de même topologie peut être exporté dans le jeu et affecter ses autres instances.': 'OBJ, glTF 2.0, GLB or JSON · 12 MB maximum. For glTF, also select its .bin file. Geometry is assembled; choose the texture below. Additions and duplicates remain previews. A same-topology replacement can be exported to the game and affect its other instances.',
  'PNG · 8 Mo maximum. Un remplacement compatible conserve les dimensions et le format Xbox du jeu. Une texture partagée est remplacée dans toutes ses utilisations. Les autres imports restent dans le projet et l’export d’assets.': 'PNG · 8 MB maximum. A compatible replacement preserves the game dimensions and Xbox format. A shared texture is replaced in all its uses. Other imports remain in the project and asset export.',
  'Les modèles importés ou dupliqués et les nouvelles textures sont conservés dans le projet et dans le dossier assets de l’export. L’allocation de nouvelles ressources dans le jeu n’est pas encore prise en charge.': 'Imported or duplicated models and new textures are saved in the project and the export assets folder. Allocating new game resources is not yet supported.',
  'Remplacement natif : les sommets de la réserve partagée sont modifiés dans toutes ses instances ; les collisions et les matériaux d’origine sont conservés.': 'Native replacement: shared vertex pool vertices change in all instances; original collisions and materials are preserved.',
  'Remplacement natif : toutes les utilisations de cette surface et tous ses niveaux de détail utilisent la nouvelle image.': 'Native replacement: every use and mip level of this surface uses the new image.',
  'Les triangles dégénérés ne sont pas acceptés.': 'Degenerate triangles are not accepted.',
  'Choisissez un modèle OBJ ou JSON.': 'Choose an OBJ or JSON model.',
  'Choisissez un modèle et ses tampons locaux, de 12 Mo maximum.': 'Choose a model and its local buffers, up to 12 MB.',
  'Choisissez une texture PNG de 8 Mo maximum.': 'Choose a PNG texture up to 8 MB.',
  'La texture est illisible.': 'The texture cannot be read.',
  'Ce fichier n’est pas un PNG valide.': 'This file is not a valid PNG.',
  'Le modèle glTF doit utiliser uniquement des fichiers locaux sélectionnés.': 'The glTF model must use selected local files only.',
  'Exportez le modèle glTF sans compression Draco ou Meshopt.': 'Export the glTF model without Draco or Meshopt compression.',
  'Le fichier JSON est illisible.': 'The JSON file cannot be read.',
  'Indice OBJ hors limites.': 'OBJ index out of bounds.',
  'Coordonnée OBJ invalide.': 'Invalid OBJ coordinate.',
  'Un triangle référence un sommet absent.': 'A triangle refers to a missing vertex.',
  'Une coordonnée UV est requise par sommet.': 'Each vertex needs a UV coordinate.',
  'Une normale est requise par sommet.': 'Each vertex needs a normal.',
  'Langue de l’interface': 'Interface language',
  'Importer un ISO': 'Import an ISO',
  'Importer un fichier ISO Xbox': 'Import an Xbox ISO file',
  'Lent · précision': 'Slow · precision',
  'Normal': 'Normal',
  'Rapide · exploration': 'Fast · exploration',
  'Vitesse de déplacement': 'Movement speed',
  'Vitesse': 'Speed',
  'Verrouiller': 'Lock',
  'Déverrouiller': 'Unlock',
  'Tout verrouiller': 'Lock all',
  'Tout déverrouiller': 'Unlock all',
  'Verrouiller la sélection': 'Lock selection',
  'Déverrouiller la sélection': 'Unlock selection',
  'Empêcher les déplacements accidentels': 'Prevent accidental changes',
  'Verrouiller tous les éléments du niveau': 'Lock all level elements',
  'Déverrouiller tous les éléments du niveau': 'Unlock all level elements',
  'Verrouillé': 'Locked',
  'Déverrouillé': 'Unlocked',
  'Protection du placement': 'Placement protection',
  'Sélection verrouillée': 'Selection locked',
  'Sélection déverrouillée': 'Selection unlocked',
  'Tous les éléments du niveau sont verrouillés.': 'All level elements are locked.',
  'Tous les éléments du niveau sont déverrouillés.': 'All level elements are unlocked.',
  'Cet élément est verrouillé. Déverrouillez-le pour le modifier.': 'This element is locked. Unlock it to edit it.',
  'Cet élément ou une partie liée est verrouillé.': 'This element or a linked part is locked.',
  'Chargement du projet…': 'Loading project…',
  'Commandes et raccourcis': 'Controls and shortcuts',
  'Enregistrer': 'Save',
  'Exporter le mod': 'Export mod',
  'EXPLORATEUR': 'EXPLORER',
  'Niveau actif': 'Active level',
  'Lecture du dump…': 'Reading game dump…',
  'Données originales du jeu': 'Original game data',
  'Scène': 'Scene',
  'Rechercher dans la scène': 'Search scene',
  'Tout': 'All',
  'Géométrie': 'Geometry',
  'Entités': 'Entities',
  'Filtrer par groupe du niveau': 'Filter by level group',
  'Tous les groupes du niveau': 'All level groups',
  'Isoler': 'Isolate',
  'Tout afficher': 'Show all',
  'Le contenu du niveau apparaîtra ici.': 'Level content will appear here.',
  'Dump source protégé': 'Source dump protected',
  'Les changements sont enregistrés dans le projet.': 'Changes are saved in the project.',
  'Déplacer · W': 'Move · W',
  'Déplacer': 'Move',
  'Rotation locale · E': 'Local rotation · E',
  'Rotation locale': 'Local rotation',
  'Échelle locale · R': 'Local scale · R',
  'Échelle locale': 'Local scale',
  'Annuler · Ctrl Z': 'Undo · Ctrl Z',
  'Annuler': 'Undo',
  'Rétablir · Ctrl Y': 'Redo · Ctrl Y',
  'Rétablir': 'Redo',
  'Monde': 'World',
  'Local': 'Local',
  'MONDE': 'WORLD',
  'SOURCE': 'SOURCE',
  'Cadrer la sélection · F / tout le niveau · Home': 'Frame selection · F / full level · Home',
  'Cadrer': 'Frame',
  'Caméra libre : clic droit + ZQSD / WASD': 'Free camera: right mouse + WQDS / WASD',
  'Caméra libre : ZQSD · clic droit pour regarder': 'Free camera: WQDS · right mouse to look',
  'Caméra libre': 'Free camera',
  'Grille': 'Grid',
  'Textures': 'Textures',
  'Fil de fer': 'Wireframe',
  'Collisions': 'Collisions',
  'Repères d’entités': 'Entity markers',
  'Point de vue': 'Viewpoint',
  'Perspective': 'Perspective',
  'Depuis le départ': 'From start point',
  'Dessus · Z': 'Top · Z',
  'Face · Y': 'Front · Y',
  'Côté · X': 'Side · X',
  'Zoom du viewport': 'Viewport zoom',
  'Modèles d’entités en pose de liaison': 'Entity models in bind pose',
  'Modèles': 'Models',
  'Animer les textures · cadence d’aperçu': 'Animate textures · preview frame rate',
  'Lire les animations de textures': 'Play texture animations',
  'Cadence d’aperçu des textures': 'Texture preview frame rate',
  'ips': 'fps',
  'Capturer la vue en PNG': 'Capture view as PNG',
  'Capturer en PNG': 'Capture as PNG',
  'Réglages de visualisation': 'View settings',
  'Détail du décor': 'Scenery detail',
  'Décor · détail maximal': 'Scenery · maximum detail',
  'Décor · état de chargement': 'Scenery · initial state',
  'Ciel du niveau': 'Level sky',
  'Lecture du ciel…': 'Reading sky…',
  'Vue 3D du niveau': '3D level view',
  'Données du jeu': 'Game data',
  'Ouverture du projet': 'Opening project',
  'Lecture du catalogue des niveaux…': 'Reading level catalogue…',
  'Réessayer': 'Retry',
  'Coordonnées originales : Z vers le haut': 'Original coordinates: Z up',
  'Souris': 'Mouse',
  'orbiter': 'orbit',
  'Molette': 'Scroll wheel',
  'zoomer': 'zoom',
  'cadrer': 'frame',
  'Clic droit': 'Right mouse',
  'regarder': 'look',
  'avancer': 'move',
  'Maj': 'Shift',
  'accélérer': 'speed up',
  'Alt': 'Alt',
  'ralentir': 'slow down',
  'INSPECTEUR': 'INSPECTOR',
  'PROPRIÉTÉS': 'PROPERTIES',
  'Explorez le niveau': 'Explore the level',
  'Sélectionnez un élément pour inspecter sa source et modifier son placement, sa rotation ou son échelle.': 'Select an element to inspect its source and edit its placement, rotation or scale.',
  'Cadrer le niveau': 'Frame level',
  'GÉOMÉTRIE': 'GEOMETRY',
  'Transformation': 'Transform',
  'Position': 'Position',
  'degrés': 'degrees',
  'Restaurer la position d’origine': 'Restore original position',
  'Restaurer rotation et échelle': 'Restore rotation and scale',
  'Données source': 'Source data',
  'Prise en charge du niveau': 'Level support',
  'LECTURE RÉELLE': 'SOURCE DATA',
  'Lecture en cours': 'Reading data',
  'Matières': 'Materials',
  'Références': 'References',
  'Filtrer les assets…': 'Filter assets…',
  'Rechercher dans les assets': 'Search assets',
  'Réduire le navigateur d’assets': 'Collapse asset browser',
  'Réduire les assets': 'Collapse assets',
  'Déployer le navigateur d’assets': 'Expand asset browser',
  'Portée de la bibliothèque': 'Library scope',
  'Bibliothèque du jeu': 'Game library',
  'Archive de la bibliothèque': 'Library archive',
  'Lecture des archives…': 'Reading archives…',
  'Assets du niveau actif': 'Active level assets',
  'Lecture seule': 'Read only',
  'Assets du niveau': 'Level assets',
  'Les textures décodées apparaîtront ici.': 'Decoded textures will appear here.',
  'Page précédente': 'Previous page',
  'Page suivante': 'Next page',
  'Connexion au projet local…': 'Connecting to local project…',
  'Aucune modification': 'No changes',
  'Commandes': 'Controls',
  'Fermer': 'Close',
  'Rendu 3D des données Azurik': '3D rendering of Azurik data',
  'Domaine de l’Air': 'Air Domain',
  'Domaine de la Terre': 'Earth Domain',
  'Domaine du Feu': 'Fire Domain',
  'Domaine de l’Eau': 'Water Domain',
  'Domaine de la Mort': 'Death Domain',
  'Cinématiques': 'Cinematics',
  'NIVEAUX': 'LEVELS',
  'CINÉMATIQUES': 'CINEMATICS',
  'Type de scène': 'Scene type',
  'Niveau actif': 'Active level',
  'Cinématique active': 'Active cinematic',
  'Aucun niveau disponible.': 'No levels available.',
  'Aucune cinématique disponible.': 'No cinematics available.',
  'Niveaux personnalisés': 'Custom levels',
  'Autres niveaux': 'Other levels',
  'Niveaux du jeu': 'Game levels',
  'Créer une copie des fichiers modifiés': 'Create a copy of modified files',
  'Aucune modification à exporter': 'No changes to export',
  'Une texture n’a pas pu être chargée ; géométrie disponible.': 'A texture could not be loaded; geometry is available.',
  'Aucun niveau compatible n’a été trouvé dans le dump. Vérifiez le chemin du jeu.': 'No supported level was found in the dump. Check the game path.',
  'Projet indisponible': 'Project unavailable',
  'Décodage de la géométrie, des collisions et des assets du jeu…': 'Decoding game geometry, collisions and assets…',
  'Catégories du graphe': 'Graph categories',
  'Groupes de placement': 'Placement groups',
  'Ciel · masqué': 'Sky · hidden',
  'Ciel · Jour': 'Sky · Day',
  'Ciel · Nuit': 'Sky · Night',
  'Ciel · Tous': 'Sky · All',
  'Ciel · Toutes les variantes': 'Sky · All variants',
  'Ciel non décodé': 'Sky not decoded',
  'Ciel absent de ce niveau': 'No sky in this level',
  'Parties placées dans le graphe du niveau ; les groupes de détail alternatifs sont conservés et restent consultables.': 'Parts placed in the level graph; alternative detail groups are preserved and can be inspected.',
  'Ce bloc ne possède pas ce canal de transformation modifiable.': 'This block has no editable channel for this transform.',
  'Pivot ou parent complexe : utilisez les champs locaux. La géométrie sera reconstruite après validation.': 'Complex pivot or parent: use the local fields. Geometry will be rebuilt after applying the change.',
  'Géométrie du niveau': 'Level geometry',
  'Primitives de modèles': 'Model primitives',
  'Position modifiée': 'Position changed',
  'Masquer dans la vue': 'Hide in view',
  'Afficher dans la vue': 'Show in view',
  'Aucun résultat pour cette recherche.': 'No results for this search.',
  'Aucun élément de ce type.': 'No elements of this type.',
  'GÉOMÉTRIE DU NIVEAU': 'LEVEL GEOMETRY',
  'PRIMITIVE DE MODÈLE': 'MODEL PRIMITIVE',
  'REPÈRE D’ENTITÉ': 'ENTITY MARKER',
  'Mesh du jeu': 'Game mesh',
  'Entité du jeu': 'Game entity',
  'Ce bloc est consultable. Son format ne permet pas encore un déplacement fiable ; la modification est désactivée.': 'This block can be inspected. Its format does not yet allow reliable movement; editing is disabled.',
  'Le déplacement modifie le placement du décor et ses parties liées. Les collisions restent à leur position d’origine : vérifiez-les dans le jeu.': 'Moving changes the placement of scenery and its linked parts. Collisions stay at their original position: check them in the game.',
  'Cette primitive est affichée dans ses coordonnées source. Modifier ses sommets affecte le modèle utilisé par le jeu ; ses placements et les collisions demandent une vérification dans le jeu.': 'This primitive uses its source coordinates. Editing its vertices affects the game model; check its placements and collisions in the game.',
  'Modèle original en pose de liaison, sans animation de squelette. Le placement est celui du graphe du jeu ; les comportements ne sont pas exécutés.': 'Original model in bind pose, without skeletal animation. Placement comes from the game graph; behaviours are not simulated.',
  'Ce repère représente une position du graphe du jeu. Son modèle et ses comportements ne sont pas exécutés dans cette vue.': 'This marker represents a position in the game graph. Its model and behaviours are not simulated in this view.',
  'Ce repère affiche les coordonnées enregistrées dans le fichier. Leur rattachement à un parent n’est pas encore résolu : la position mondiale reste à confirmer. Le modèle animé n’est pas décodé.': 'This marker shows coordinates stored in the file. Parent attachment is unresolved: world position remains unverified. The animated model is not decoded.',
  'Type': 'Type',
  'Instance de scène': 'Scene instance',
  'Géométrie statique': 'Static geometry',
  'Primitive de modèle': 'Model primitive',
  'Entité': 'Entity',
  'Adresse du bloc': 'Block address',
  'Coordonnées': 'Coordinates',
  'Monde original · Z ↑': 'Original world · Z ↑',
  'Source locale · Z ↑': 'Local source · Z ↑',
  'Déplacement': 'Movement',
  'Pris en charge': 'Supported',
  'Placement sérialisé': 'Serialized placement',
  'Parties liées': 'Linked parts',
  'Nœud de transformation': 'Transform node',
  'Groupe du niveau': 'Level group',
  'Modèle original': 'Original model',
  'Pose affichée': 'Displayed pose',
  'Liaison · non animée': 'Bind pose · not animated',
  'Échelle de base du modèle': 'Base model scale',
  'Correspondance du jeu': 'Game mapping',
  'Bibliothèque du modèle': 'Model library',
  'Sommets': 'Vertices',
  'Triangles': 'Triangles',
  'Sous-parties': 'Subparts',
  'Matériaux texturés': 'Textured materials',
  'Éclairage du décor': 'Scenery lighting',
  'Coordonnées des reflets': 'Reflection coordinates',
  'Calcul original du jeu · texture 2D': 'Original game calculation · 2D texture',
  'Éclairage partiel': 'Partial lighting',
  'État initial ; lumières ponctuelles, projecteurs et changements pendant la partie non reproduits.': 'Initial state; point lights, spotlights and changes during gameplay are not reproduced.',
  'Opacité source de la plateforme': 'Source platform opacity',
  'Facteur d’opacité de l’aperçu': 'Preview opacity factor',
  'Biais de distance du rendu': 'Render distance bias',
  'État dynamique': 'Dynamic state',
  'Conservé visible pour inspection · scripts non simulés': 'Kept visible for inspection · scripts not simulated',
  'Rendu partiel': 'Partial rendering',
  'Origine du bloc': 'Block origin',
  'Géométries': 'Geometries',
  'Entités repérées': 'Located entities',
  'Triangles visuels': 'Visual triangles',
  'Faces de collision': 'Collision faces',
  'Ciel original · caméra dédiée': 'Original sky · dedicated camera',
  'Aucun ciel déclaré dans ce niveau': 'No sky declared in this level',
  'Géométrie de scène placée': 'Placed scene geometry',
  'Modèles statiques décodés': 'Decoded static models',
  'Textures : données non décodées': 'Textures: data not decoded',
  'Collision du niveau consultable': 'Level collision available to inspect',
  'Collision indisponible': 'Collision unavailable',
  'Modèles originaux · pose de liaison': 'Original models · bind pose',
  'Entités : repères de position': 'Entities: position markers',
  'Rotation et échelle locales': 'Local rotation and scale',
  'Transformations : selon le bloc source': 'Transforms: depend on the source block',
  'Textures fixes': 'Static textures',
  'Rendu des fichiers du dump : les effets, scripts et animations du moteur ne sont pas exécutés.': 'Rendering dump files: engine effects, scripts and animations are not simulated.',
  'Disponible pour le niveau actif': 'Available for the active level',
  'Lecture de l’archive…': 'Reading archive…',
  'Lecture du catalogue du jeu…': 'Reading game catalogue…',
  'Aucune archive du jeu n’est disponible dans la bibliothèque.': 'No game archive is available in the library.',
  'Bibliothèque indisponible': 'Library unavailable',
  'Lecture des ressources originales': 'Reading original resources',
  'Le catalogue et les aperçus sont préparés depuis l’archive sélectionnée.': 'The catalogue and previews are prepared from the selected archive.',
  'Archive indisponible': 'Archive unavailable',
  'Aucun asset correspondant': 'No matching asset',
  'Aucune ressource décodée dans cette catégorie': 'No decoded resources in this category',
  'Essayez un nom, un identifiant ou un format différent.': 'Try a different name, identifier or format.',
  'Les ressources identifiées dans les fichiers originaux apparaîtront ici.': 'Resources identified in the original files will appear here.',
  'Référence': 'Reference',
  'Pose de liaison': 'Bind pose',
  'Modèle statique': 'Static model',
  'niveau actif': 'active level',
  'MATIÈRE ORIGINALE': 'ORIGINAL MATERIAL',
  'Ressource': 'Resource',
  'Technique': 'Technique',
  'Opacité': 'Opacity',
  'Comparaison alpha': 'Alpha comparison',
  'Automatique · supérieur': 'Automatic · greater than',
  'Seuil alpha': 'Alpha threshold',
  'Mode de mélange': 'Blend mode',
  'Écriture profondeur': 'Depth write',
  'Géométries liées': 'Linked geometries',
  'sans texture': 'no texture',
  'Valeurs lues dans le jeu. Les combinaisons à plusieurs couches et les coordonnées générées ne sont pas encore entièrement reproduites.': 'Values read from the game. Multilayer combinations and generated coordinates are not yet fully reproduced.',
  'Sélectionner une géométrie liée': 'Select linked geometry',
  'TEXTURE DU DUMP': 'DUMP TEXTURE',
  'Texture du jeu': 'Game texture',
  'Texture Xbox': 'Xbox texture',
  'Pause / lecture de cet aperçu': 'Pause / play this preview',
  'Choisir une image de l’animation': 'Choose an animation frame',
  'Séquence d’images originale. La cadence est un réglage d’aperçu ; le rythme et les effets du moteur ne sont pas simulés.': 'Original image sequence. The frame rate is a preview setting; engine timing and effects are not simulated.',
  'Six faces originales. Leur orientation dans le moteur reste à vérifier.': 'Six original faces. Their orientation in the engine remains unverified.',
  'MODÈLE ORIGINAL': 'ORIGINAL MODEL',
  'Lecture du modèle original…': 'Reading original model…',
  'Réserve de géométrie du niveau. Sélectionnez une instance dans la hiérarchie ou ouvrez un assemblage statique depuis la bibliothèque du jeu.': 'Level geometry pool. Select an instance in the hierarchy or open a static assembly from the game library.',
  'Cette ressource ne possède pas encore un assemblage de triangles validé. Les sommets ne sont pas triangulés automatiquement.': 'This resource has no verified triangle assembly yet. Vertices are not triangulated automatically.',
  'Assemblage statique original. Les matières complexes sont approximées avec leur couleur principale. Les animations du squelette, les effets et l’éclairage du moteur ne sont pas exécutés dans cet aperçu.': 'Original static assembly. Complex materials are approximated using their main colour. Skeletal animations, effects and engine lighting are not simulated in this preview.',
  'Modèle original en pose de liaison. Les matières complexes sont approximées avec leur couleur principale. Les animations du squelette, les effets et l’éclairage du moteur ne sont pas exécutés dans cet aperçu.': 'Original model in bind pose. Complex materials are approximated using their main colour. Skeletal animations, effects and engine lighting are not simulated in this preview.',
  'Vue de l’ensemble du niveau': 'Full level view',
  'Entrez des coordonnées numériques valides.': 'Enter valid numeric coordinates.',
  'Vue d’éditeur derrière le départ du niveau': 'Editor view behind the level start',
  'Ce niveau ne déclare pas de point de départ.': 'This level has no declared start point.',
  'CAPTURE DU VIEWPORT': 'VIEWPORT CAPTURE',
  'Capture de la vue': 'View capture',
  'Télécharger le PNG': 'Download PNG',
  'Capture PNG enregistrée dans le dossier du projet.': 'PNG capture saved in the project folder.',
  'Fond du viewport': 'Viewport background',
  'Luminosité d’aperçu': 'Preview brightness',
  'Studio bleu': 'Blue studio',
  'Nuit': 'Night',
  'Gris neutre': 'Neutral grey',
  'Noir': 'Black',
  'Restaurer les valeurs de lecture': 'Restore source values',
  'Ces réglages ne modifient aucun fichier du jeu. La luminosité à 1× conserve les couleurs lues. L’aperçu ne reproduit pas l’ensemble des effets et de l’éclairage du moteur Xbox.': 'These settings do not modify game files. Brightness at 1× preserves the source colours. The preview does not reproduce all Xbox engine effects and lighting.',
  'Entrez une rotation valide et une échelle non nulle.': 'Enter a valid rotation and a nonzero scale.',
  'Dernière modification annulée': 'Last change undone',
  'Modification rétablie': 'Change redone',
  'Projet enregistré. Les fichiers d’origine restent intacts.': 'Project saved. Original files remain intact.',
  'Projet enregistré': 'Project saved',
  'Export en cours…': 'Export in progress…',
  'EXPORT DU MOD': 'MOD EXPORT',
  'Mod exporté': 'Mod exported',
  'Consultez le dossier exports du projet.': 'Check the project exports folder.',
  'Les fichiers d’origine du dump sont conservés. Testez le niveau exporté dans le jeu : les collisions, scripts et modèles animés ne sont pas simulés par cette vue.': 'Original dump files are preserved. Test the exported level in the game: this view does not simulate collisions, scripts or animated models.',
  'Copier le chemin': 'Copy path',
  'Chemin copié': 'Path copied',
  'Le navigateur n’autorise pas la copie automatique.': 'The browser does not allow automatic copying.',
  'Dossier exports': 'Exports folder',
  'Caméra orbitale activée': 'Orbit camera enabled',
  'Caméra libre activée · maintenez le clic droit + ZQSD / WASD. Espace monte, Ctrl descend.': 'Free camera enabled · hold right mouse + WQDS / WASD. Space moves up, Ctrl moves down.',
  'Sélection et parties liées isolées dans la vue.': 'Selection and linked parts isolated in the view.',
  'Décor affiché avec une seule version de chaque groupe de distance.': 'Scenery displayed with one version of each distance group.',
  'Visibilité enregistrée au chargement du jeu': 'Visibility stored at game loading',
  'Décor en détail maximal · groupes de distance résolus': 'Maximum scenery detail · distance groups resolved',
  'Orbiter autour du niveau': 'Orbit around the level',
  'Clic gauche + glisser': 'Left mouse + drag',
  'Déplacer la caméra': 'Move camera',
  'Clic droit + glisser': 'Right mouse + drag',
  'Zoomer': 'Zoom',
  'Déplacer / tourner / redimensionner': 'Move / rotate / scale',
  'W / E / R · repère monde / local': 'W / E / R · world / local space',
  'Rotation et échelle précises': 'Precise rotation and scale',
  'Champs locaux de l’inspecteur · degrés': 'Local inspector fields · degrees',
  'Sélectionner un élément': 'Select an element',
  'Clic sur la vue ou la hiérarchie': 'Click the view or hierarchy',
  'Cadrer la sélection / tout le niveau': 'Frame selection / full level',
  'Bouton avion · clic droit + ZQSD / WASD': 'Plane button · right mouse + WQDS / WASD',
  'Monter / descendre en caméra libre': 'Move up / down in free camera',
  'Espace / Ctrl · Maj accélère': 'Space / Ctrl · Shift speeds up',
  'Annuler / rétablir': 'Undo / redo',
  'Enregistrer le projet': 'Save project',
  'Désélectionner': 'Deselect',
  'Échap': 'Esc',
  'Les axes et les positions suivent les coordonnées originales du jeu : Z vers le haut. La vue affiche la géométrie et les matériaux décodés ; elle ne reproduit pas les scripts, les effets ou les animations du moteur Xbox.': 'Axes and positions use the original game coordinates: Z up. The view displays decoded geometry and materials; it does not simulate Xbox engine scripts, effects or animations.',
  'Effet de matière partiellement reproduit': 'Material effect partially reproduced',
  'Fusion de terrain partiellement reproduite': 'Terrain blending partially reproduced',
  'Matière à coordonnées générées partiellement reproduite': 'Material with generated coordinates partially reproduced',
  'MODÈLE SOURCE · ASSEMBLAGE STATIQUE': 'SOURCE MODEL · STATIC ASSEMBLY',
  'MODÈLE SOURCE · POSE DE LIAISON': 'SOURCE MODEL · BIND POSE',
  'Capture enregistrée': 'Capture saved',
  'CAPTURE DU NIVEAU': 'LEVEL CAPTURE',
  'Visualisation du niveau': 'Level view settings',
  'RÉGLAGES D’APERÇU': 'PREVIEW SETTINGS',
  'Fond de la vue': 'View background',
  'Votre mod est exporté': 'Your mod has been exported',
  'EXPORT DU JEU': 'GAME EXPORT',
  'Ciel · Tous les éléments': 'Sky · All elements',
  'Transformer ce décor modifie son nœud de placement, ses parties liées et ses descendants. Les collisions restent à leur position d’origine.': 'Transforming this scenery changes its placement node, linked parts and descendants. Collisions stay at their original position.',
  'Le décor est extrait des fichiers du jeu. Les animations, effets et objets dynamiques ne sont pas encore reproduits intégralement.': 'Scenery is extracted from the game files. Animations, effects and dynamic objects are not yet fully reproduced.',
  'Transformation conservée dans le projet de l’éditeur ; ce placement n’est pas encore exportable dans le jeu. Les collisions restent inchangées.': 'Transform saved in the editor project; this placement cannot yet be exported to the game. Collisions remain unchanged.',
  "L'aperçu n'exécute pas les scripts, les animations des personnages ni l'ensemble des effets et shaders Xbox.": 'The preview does not run scripts, character animations or all Xbox effects and shaders.',
  'Les collisions sont une visualisation indicative de suites de quadrilatères planaires SDSR ; leur format complet reste à confirmer et elles ne sont pas modifiables.': 'Collisions are an indicative view of planar SDSR quadrilateral sequences; their full format remains unverified and they cannot be edited.',
  'Les transformations utilisent les valeurs de départ du graphe ; les animations et changements d’état pendant la partie ne sont pas simulés.': 'Transforms use the initial graph values; animations and state changes during gameplay are not simulated.',
  'Les décors statiques LEVL sont affichés dans toutes leurs cellules, avec leur détail maximal ; les portails et distances de visibilité du jeu ne sont pas simulés.': 'Static LEVL scenery is displayed in every cell at maximum detail; game portals and visibility distances are not simulated.',
  'Aperçu des ressources originales. Les matières complexes, particules, scripts et animations du moteur ne sont pas simulés.': 'Preview of original resources. Complex materials, particles, scripts and engine animations are not simulated.',
  'Objet verrouillé · déverrouillez-le pour le modifier.': 'Object locked · unlock it to edit it.',
  'Placement d’aperçu sauvegardé dans le projet. Ce bloc ne peut pas encore être déplacé dans les fichiers du jeu.': 'Preview placement saved in the project. This block cannot yet be moved in the game files.',
  'Protection': 'Protection',
  'Export dans le jeu': 'Export to game',
  'Aperçu uniquement': 'Preview only',
  'Placement source': 'Source placement',
  'Clic droit : regarder à 360°': 'Right mouse: look around 360°',
  'Maj : rapide · Alt : lent': 'Shift: fast · Alt: slow',
  'Souris : orbiter · Molette : zoomer · F : cadrer': 'Mouse: orbit · Scroll wheel: zoom · F: frame',
  'Caméra libre activée · clic droit pour regarder sur place. Espace monte, Ctrl descend. Maj accélère, Alt ralentit.': 'Free camera enabled · right mouse to look from your position. Space moves up, Ctrl moves down. Shift speeds up, Alt slows down.',
  'Tous les éléments sont verrouillés.': 'All elements are locked.',
  'Tous les éléments sont déverrouillés.': 'All elements are unlocked.',
  'Objet verrouillé.': 'Object locked.',
  'Objet déverrouillé.': 'Object unlocked.',
  'Importez votre jeu': 'Import your game',
  'Choisissez un ISO Xbox d’Azurik ou un dump compatible pour commencer.': 'Choose an Azurik Xbox ISO or a compatible dump to get started.',
  'Aucune source ouverte · import ISO disponible': 'No source opened · ISO import available',
  'Lecture de l’ISO…': 'Reading ISO…',
  'Source ouverte · le projet précédent est conservé.': 'Source opened · the previous project is preserved.',
  'Extraction locale': 'Local extraction',
  'Import ISO impossible.': 'ISO import failed.',
  'Import terminé · ouverture du jeu…': 'Import complete · opening game…',
  'ISO importée · projet séparé ouvert.': 'ISO imported · separate project opened.',
  'Choisissez un fichier .iso.': 'Choose an .iso file.',
  'La taille de l’ISO doit être comprise entre 1 octet et 16 Go.': 'The ISO size must be between 1 byte and 16 GB.',
  'Copie locale de l’ISO': 'Local ISO copy',
  'Le serveur local ne répond pas.': 'The local server is not responding.',
  'Import interrompu.': 'Import interrupted.',
  'Réponse d’import illisible.': 'Unreadable import response.',
  'Ouvrir le jeu': 'Open game',
  'IMPORT ISO · PROJETS': 'ISO IMPORT · PROJECTS',
  'Importez votre ISO Xbox d’Azurik. Le jeu est extrait localement et chaque import dispose de son propre projet.': 'Import your Azurik Xbox ISO. The game is extracted locally and each import has its own project.',
  'Choisir un fichier ISO': 'Choose an ISO file',
  'Ou saisir le chemin local de l’ISO': 'Or enter the local ISO path',
  'Chemin complet vers votre fichier .iso': 'Full path to your .iso file',
  'Importer ce chemin': 'Import this path',
  'Import en cours…': 'Import in progress…',
  'Votre dump et vos projets existants sont conservés.': 'Your dump and existing projects are preserved.',
  'Revenir au dump d’origine': 'Return to original dump',
  'Ouvrir': 'Open',
  'Regarder à 360° sur place': 'Look around 360° from your position',
  'Caméra libre · clic droit + glisser': 'Free camera · right mouse + drag',
  'Avancer / gauche / reculer / droite': 'Forward / left / backward / right',
  'Menu Vitesse · Maj accélère · Alt ralentit': 'Speed menu · Shift speeds up · Alt slows down',
  'Bouton Verrouiller dans l’inspecteur': 'Lock button in the inspector',
  'Changements enregistrés dans le projet local': 'Changes saved in the local project',
  'Coordonnées ou couches du terrain non décodées': 'Terrain coordinates or layers not decoded',
  'Couleur de sommet absente : éclairage du terrain non reproduit': 'Missing vertex colour: terrain lighting not reproduced',
  'Modèle original en pose de liaison.': 'Original model in bind pose.',
  'Assemblage statique original.': 'Original static assembly.',
  'Objet verrouillé': 'Object locked',
  'Verrouillé par un parent · déverrouillez le parent ou le niveau.': 'Locked by a parent · unlock the parent or the level.',
  'Flèches : regarder sur place': 'Arrow keys: look from your position',
  'Position de la caméra': 'Camera position',
  'Vue': 'View',
  'UV1 absent : mélange du terrain non reproduit': 'Missing UV1: terrain blending not reproduced',
  'modifications d’aperçu': 'preview changes',
  'La position doit contenir trois nombres X, Y, Z.': 'Position must contain three numbers X, Y, Z.',
  'Les coordonnées doivent être des nombres.': 'Coordinates must be numbers.',
  'Les coordonnées doivent être finies et comprises dans les limites du jeu.': 'Coordinates must be finite and within the game limits.',
  'La rotation locale doit être exprimée en radians finis (±720 maximum).': 'Local rotation must be expressed in finite radians (±720 maximum).',
  'L’échelle locale doit être finie et non nulle (valeur absolue de 0,0001 à 1000).': 'Local scale must be finite and nonzero (absolute value from 0.0001 to 1000).',
  'Le projet et les exports doivent être séparés du dump source.': 'The project and exports must be separate from the source dump.',
  'Ce projet appartient à un autre dump source.': 'This project belongs to a different source dump.',
  'Projet invalide.': 'Invalid project.',
  'État de niveau invalide dans le projet.': 'Invalid level state in the project.',
  'Verrouillages ou transformations de scène invalides.': 'Invalid locks or scene transforms.',
  'Identifiant de niveau invalide.': 'Invalid level identifier.',
  'Projet incompatible : offset de modification non validé.': 'Incompatible project: edit offset is unverified.',
  'Format de transformation inconnu.': 'Unknown transform format.',
  'Projet incompatible : octets originaux non valides.': 'Incompatible project: invalid original bytes.',
  'Projet V1 incompatible : canaux de transformation non validés.': 'Incompatible V1 project: transform channels are unverified.',
  'Projet incompatible : position locale non validée.': 'Incompatible project: local position is unverified.',
  'Les octets originaux des paramètres ne correspondent pas à la source.': 'Original parameter bytes do not match the source.',
  'Projet incompatible : plusieurs modifications du même nœud.': 'Incompatible project: multiple edits of the same node.',
  'Cycle dans les transformations.': 'Cycle in transforms.',
  'Identifiant d’objet invalide.': 'Invalid object identifier.',
  'Objet introuvable dans ce niveau.': 'Object not found in this level.',
  'Cet objet est verrouillé. Déverrouillez-le avant de le déplacer.': 'This object is locked. Unlock it before moving it.',
  'Ce déplacement modifierait un objet lié verrouillé. Déverrouillez-le d’abord.': 'This move would change a locked linked object. Unlock it first.',
  'Le verrouillage doit être vrai ou faux.': 'Lock state must be true or false.',
  'Transformation de scène incompatible avec cet objet.': 'Scene transform is incompatible with this object.',
  'Transformation de scène invalide.': 'Invalid scene transform.',
  'La position de cet objet n’est pas disponible.': 'This object’s position is unavailable.',
  'Cet objet n’a pas de position sérialisée validée.': 'This object has no verified serialized position.',
  'Le nœud de placement n’est pas validé pour l’édition.': 'The placement node is not verified for editing.',
  'Entrée d’historique invalide.': 'Invalid history entry.',
  'Snapshot de transformations de scène invalide.': 'Invalid scene transform snapshot.',
  'Snapshot d’historique invalide.': 'Invalid history snapshot.',
  'Export annulé : octets originaux inattendus.': 'Export cancelled: unexpected original bytes.',
  'Export annulé : modifications superposées.': 'Export cancelled: overlapping edits.',
  'Export annulé : modification en dehors des paramètres autorisés.': 'Export cancelled: edit outside allowed parameters.',
  'Aucune modification à exporter.': 'No changes to export.',
  'Le dump source ne peut pas être une destination d’export.': 'The source dump cannot be an export destination.',
  // New controls may use named templates without constructing French strings.
  'import.selected': 'Selected ISO: {name}',
  'import.progress': 'Importing ISO · {percent}%',
  'import.ready': 'ISO imported · {count} levels available',
  'lock.count': '{count} elements locked',
  'unlock.count': '{count} elements unlocked',
});

const frenchKeys = Object.freeze({
  'import.selected': 'ISO sélectionné : {name}',
  'import.progress': 'Import de l’ISO · {percent} %',
  'import.ready': 'ISO importé · {count} niveaux disponibles',
  'lock.count': '{count} éléments verrouillés',
  'unlock.count': '{count} éléments déverrouillés',
});

// Full messages or narrowly delimited UI patterns. Captured source names and
// paths pass through unchanged. The reverse patterns restore French on switch.
const templates = [
  ['{1} NIVEAUX', '{1} LEVELS'],
  ['{1} CINÉMATIQUES', '{1} CINEMATICS'],
  ['Sélectionnez aussi le tampon du modèle : {1}', 'Also select the model buffer: {1}'],
  ['{1} : données invalides ou trop volumineuses.', '{1}: invalid or oversized data.'],
  ['{1} modifications du jeu · {2} modifications d’aperçu', '{1} game changes · {2} preview changes'],
  ['{1} modification du jeu · {2} modifications d’aperçu', '{1} game change · {2} preview changes'],
  ['Aucune modification · {1} modifications d’aperçu', 'No changes · {1} preview changes'],
  ['Ouverture · {1}', 'Opening · {1}'],
  ['Lecture du niveau {1}…', 'Reading level {1}…'],
  ['Impossible d’ouvrir {1}', 'Unable to open {1}'],
  ['{1} ouvert · {2} géométries · {3} repères d’entités', '{1} open · {2} geometries · {3} entity markers'],
  ['{1} modification du jeu', '{1} game change'],
  ['{1} modifications du jeu', '{1} game changes'],
  ['{1} modification d’aperçu', '{1} preview change'],
  ['{1} modifications d’aperçu', '{1} preview changes'],
  ['{1} triangles visibles', '{1} visible triangles'],
  ['{1} parties du décor · {2} rétablies', '{1} scenery parts · {2} restored'],
  ['{1} parties du décor', '{1} scenery parts'],
  ['{1} textures Xbox décodées', '{1} decoded Xbox textures'],
  ['{1} animations de texture · aperçu', '{1} texture animations · preview'],
  ['{1} informations sur le rendu et les modifications', '{1} rendering and editing notes'],
  ['{1} lumières directionnelles du jeu · couleurs d’origine', '{1} game directional lights · original colours'],
  ['{1} os · pose de liaison', '{1} bones · bind pose'],
  ['{1} triangles · {2} parties', '{1} triangles · {2} parts'],
  ['{1} triangles · {2} parties · {3} os · {4}', '{1} triangles · {2} parts · {3} bones · {4}'],
  ['{1} triangles · {2} parties · {3}', '{1} triangles · {2} parts · {3}'],
  ['{1} sommets · {2} triangles · ressource du niveau', '{1} vertices · {2} triangles · level resource'],
  ['Azurik · pose de liaison', 'Azurik · bind pose'],
  ['Décodage · {1}', 'Decoding · {1}'],
  ['{1} ressources · {2} archives', '{1} resources · {2} archives'],
  ['{1} ressources', '{1} resources'],
  ['Aperçu de {1}', 'Preview of {1}'],
  ['{1} IMAGES', '{1} FRAMES'],
  ['Technique {1} · {2} couche(s) · {3} géométrie(s)', 'Technique {1} · {2} layer(s) · {3} geometry item(s)'],
  ['Pose de liaison · {1} triangles · {2}', 'Bind pose · {1} triangles · {2}'],
  ['Modèle statique · {1} triangles · {2}', 'Static model · {1} triangles · {2}'],
  ['Couche {1} · sans texture · flags {2}', 'Layer {1} · no texture · flags {2}'],
  ['Couche {1} · {2} · flags {3}', 'Layer {1} · {2} · flags {3}'],
  ['Face {1}', 'Face {1}'],
  ['{1} information(s) sur cette ressource', '{1} note(s) about this resource'],
  ['Modèle indisponible : {1}', 'Model unavailable: {1}'],
  ['Sélection cadrée · {1}', 'Selection framed · {1}'],
  ['Position modifiée · {1}', 'Position changed · {1}'],
  ['Déplacement refusé : {1}', 'Movement refused: {1}'],
  ['Capture impossible : {1}', 'Capture failed: {1}'],
  ['Transformation locale modifiée · {1}', 'Local transform changed · {1}'],
  ['Transformation refusée : {1}', 'Transform refused: {1}'],
  ['Projet enregistré · {1}', 'Project saved · {1}'],
  ['Enregistrement impossible : {1}', 'Save failed: {1}'],
  ['{1} fichier exporté · {2} modification.', '{1} file exported · {2} change.'],
  ['{1} fichiers exportés · {2} modifications.', '{1} files exported · {2} changes.'],
  ['{1} fichier exporté · {2} modifications.', '{1} file exported · {2} changes.'],
  ['{1} fichiers exportés · {2} modification.', '{1} files exported · {2} change.'],
  ['Mod exporté · {1}', 'Mod exported · {1}'],
  ['Export impossible : {1}', 'Export failed: {1}'],
  ['Cadence d’aperçu des textures : {1} images / seconde', 'Texture preview frame rate: {1} frames / second'],
  ['Le serveur a renvoyé une réponse illisible ({1}).', 'The server returned an unreadable response ({1}).'],
  ['Erreur {1}', 'Error {1}'],
  ['Position {1}', 'Position {1}'],
  ['Rotation locale {1} en degrés', 'Local rotation {1} in degrees'],
  ['Échelle locale {1}', 'Local scale {1}'],
  ["{1} séquences de textures sont extraites dans leur ordre d'origine ; la cadence de prévisualisation est réglable et ne représente pas une cadence du jeu vérifiée.", '{1} texture sequences are extracted in their original order; the adjustable preview frame rate is not a verified game frame rate.'],
  ['{1} images de séquences animées ne peuvent pas être décodées ; leurs emplacements dans la séquence sont conservés.', '{1} animated sequence frames cannot be decoded; their positions in the sequence are preserved.'],
  ['{1} textures cubiques : les six faces sont extraites ; leur orientation dans le jeu et les reflets Xbox ne sont pas reproduits.', '{1} cube textures: all six faces are extracted; their game orientation and Xbox reflections are not reproduced.'],
  ['{1} surfaces utilisent un format non décodé.', '{1} surfaces use an undecoded format.'],
  ['{1} modèles non décodés : {2}', '{1} undecoded models: {2}'],
  ['{1} modèles utilisent plusieurs couches de texture ; les combinaisons vérifiées sont composées, les autres restent en couche principale.', '{1} models use multiple texture layers; verified combinations are composed, others use their main layer.'],
  ["{1} modèles ne déclarent aucune couche de texture dans leur matériau d'origine.", '{1} models declare no texture layer in their original material.'],
  ["{1} modèles affichent leurs couleurs de sommets car leur matériau ou leur surface principale n'a pas pu être décodé.", '{1} models display vertex colours because their material or main surface could not be decoded.'],
  ['Instances non affichées : {1}', 'Instances not displayed: {1}'],
  ["{1} groupes LOD affichent leur branche de détail maximal dans l'éditeur ; la sélection du jeu selon la distance caméra n'est pas simulée.", '{1} LOD groups display their maximum detail branch in the editor; game selection by camera distance is not simulated.'],
  ['Décors statiques non affichés : {1}', 'Static scenery not displayed: {1}'],
  ['{1} décors statiques déclarent des liaisons d’éclairage conservées comme métadonnées ; ces lumières du moteur Xbox ne sont pas exécutées.', '{1} static scenery items declare lighting links preserved as metadata; these Xbox engine lights are not simulated.'],
  ['Cellules statiques de {1} non résolues : {2}', 'Unresolved static cells in {1}: {2}'],
  ['Cellules de {1} non résolues : {2}', 'Unresolved cells in {1}: {2}'],
  ['Ciel non résolu : {1}', 'Unresolved sky: {1}'],
  ['Dossier gamedata introuvable : {1}', 'Gamedata folder not found: {1}'],
  ['Le fichier source {1}.xbr a changé ; les modifications ne peuvent pas être appliquées.', 'Source file {1}.xbr has changed; edits cannot be applied.'],
  ['Projet incompatible : canal {1} incomplet.', 'Incompatible project: incomplete {1} channel.'],
  ['Canal {1} sans offset sérialisé validé.', 'Channel {1} has no verified serialized offset.'],
  ['Projet incompatible : offset {1} non validé.', 'Incompatible project: {1} offset is unverified.'],
  ['Projet incompatible : octets originaux {1} non validés.', 'Incompatible project: original {1} bytes are unverified.'],
  ['Le canal {1} de ce nœud n’est pas validé pour l’édition.', 'This node’s {1} channel is not verified for editing.'],
  ['Export annulé : le SHA-256 de {1}.xbr a changé.', 'Export cancelled: the SHA-256 of {1}.xbr has changed.'],
  ['Export annulé : {1}.xbr diffère de la source analysée.', 'Export cancelled: {1}.xbr differs from the analysed source.'],
];

const escapeRegex = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const fill = (value, params) => value.replace(/\{([\w]+)\}/g, (token, key) => params[key] ?? token);
function compileTemplate(source, output) {
  const keys = [];
  const parts = source.split(/(\{\d+\})/);
  const regex = new RegExp(`^${parts.map(part => {
    const key = /^\{(\d+)\}$/.exec(part);
    if (!key) return escapeRegex(part);
    keys.push(key[1]); return '(.+?)';
  }).join('')}$`, 'u');
  const nestedError = /^(Déplacement refusé|Capture impossible|Transformation refusée|Enregistrement impossible|Export impossible|Modèle indisponible|Ciel non résolu) : \{1\}$/u.test(source) || /^(Déplacement refusé|Capture impossible|Transformation refusée|Enregistrement impossible|Export impossible|Modèle indisponible|Ciel non résolu) : \{1\}$/u.test(output);
  return { regex, output, keys, nestedError };
}
const forwardTemplates = templates.map(([fr, en]) => compileTemplate(fr, en));
const reverseTemplates = templates.filter(([fr, en]) => fr !== en).map(([fr, en]) => compileTemplate(en, fr));
const reverseMessages = new Map(Object.entries(messages).filter(([key]) => !key.includes('.')).map(([fr, en]) => [en, fr]));

function templateTranslation(value, compiled, depth = 0) {
  for (const { regex, output, keys, nestedError } of compiled) {
    const match = regex.exec(value);
    if (match) {
      const params = Object.fromEntries(keys.map((key, i) => [key, match[i + 1]]));
      if (nestedError && depth < 3) {
        const nested = params['1'];
        params['1'] = compiled === forwardTemplates
          ? (messages[nested] ?? templateTranslation(nested, compiled, depth + 1))
          : (reverseMessages.get(nested) ?? templateTranslation(nested, compiled, depth + 1));
      }
      return fill(output, params);
    }
  }
  return value;
}

export function getLanguage() { return language; }
export function getLocale() { return language === 'en' ? 'en-US' : 'fr-FR'; }
const levelAliases = Object.freeze({
  'Perathia — Ville': 'Perathia — Town',
  'Royaume de la Vie': 'Realm of Life',
  'Vaisseau aérien': 'Airship',
  'Salle d’entraînement': 'Training Room',
  'Sélecteur de niveaux': 'Level Selector',
  'Cinématique — Accostage du vaisseau': 'Cinematic — Airship Docking',
  'Cinématique — Accostage sur l’eau': 'Cinematic — Water Docking',
  'Cinématique — Voyage du vaisseau': 'Cinematic — Airship Journey',
});
export function translateLevelName(label) {
  const value = String(label ?? '');
  if (language === 'fr') return value;
  if (Object.hasOwn(levelAliases, value)) return levelAliases[value];
  const match = /^(Eau|Terre|Feu|Mort) — ([WEFD]\d+)$/u.exec(value);
  return match ? `${({ Eau: 'Water', Terre: 'Earth', Feu: 'Fire', Mort: 'Death' })[match[1]]} — ${match[2]}` : value;
}
export function formatNumber(value, options = {}) {
  return new Intl.NumberFormat(getLocale(), options).format(Number(value) || 0);
}
export function formatSize(bytes) {
  const amount = Number(bytes) || 0;
  const unit = amount > 1048576 ? (language === 'en' ? 'MB' : 'Mo') : (language === 'en' ? 'KB' : 'Ko');
  const number = amount > 1048576 ? formatNumber(amount / 1048576, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : formatNumber(Math.round(amount / 1024));
  return `${number} ${unit}`;
}

export function translate(message, params = {}) {
  const value = String(message ?? '');
  if (Object.hasOwn(frenchKeys, value)) return fill(language === 'en' ? messages[value] : frenchKeys[value], params);
  if (language === 'fr') return fill(value, params);
  const result = Object.hasOwn(messages, value) ? messages[value] : templateTranslation(value, forwardTemplates);
  return fill(result, params);
}

// Canonicalise already-translated strings written by the application itself.
// Unknown text is returned unchanged in both directions.
function toFrench(value) {
  return reverseMessages.get(value) ?? templateTranslation(value, reverseTemplates);
}

const ignoreSelector = [
  '[data-i18n-ignore]', '[translate="no"]', 'script', 'style', 'textarea', 'code', 'pre',
  '.tree-name', '#selectedName', '#selectedId', '#sourcePath', '.export-path',
  '.texture-info strong', '.reference-card strong', '#selectionLabel span',
  '#headerLevel', '#viewportLevel', '#levelSelect option', '#archiveSelect option',
  '#nodeFilter option[value^="node:"]', '#nodeFilter option[value^="type:"]',
].join(',');
const sourceDetailLabels = new Set([
  'Nœud de transformation', 'Groupe du niveau', 'Modèle original',
  'Correspondance du jeu', 'Bibliothèque du modèle',
]);
function isProtected(element) {
  if (!element || element.closest(ignoreSelector)) return true;
  const value = element.closest('dd');
  return !!value && sourceDetailLabels.has(toFrench(value.previousElementSibling?.textContent || ''));
}

function renderText(node) {
  if (isProtected(node.parentElement) || !node.data.trim()) return;
  let record = textSources.get(node);
  if (!record || node.data !== record.rendered) record = { source: toFrench(node.data), rendered: node.data };
  const leading = record.source.match(/^\s*/u)[0];
  const trailing = record.source.match(/\s*$/u)[0];
  const rendered = `${leading}${translate(record.source.trim())}${trailing}`;
  record.rendered = rendered; textSources.set(node, record);
  if (node.data !== rendered) node.data = rendered;
}

const attributes = ['title', 'aria-label', 'placeholder', 'label'];
function renderAttributes(element) {
  if (isProtected(element)) return;
  let records = attributeSources.get(element);
  if (!records) { records = new Map(); attributeSources.set(element, records); }
  for (const name of attributes) {
    if (!element.hasAttribute(name)) continue;
    const current = element.getAttribute(name);
    let record = records.get(name);
    if (!record || current !== record.rendered) record = { source: toFrench(current), rendered: current };
    const rendered = translate(record.source);
    record.rendered = rendered; records.set(name, record);
    if (current !== rendered) element.setAttribute(name, rendered);
  }
}

function renderSubtree(root) {
  if (!root) return;
  if (root.nodeType === 3) { renderText(root); return; }
  if (root.nodeType === 1) renderAttributes(root);
  const walker = (root.ownerDocument || activeDocument).createTreeWalker(root, 5); // elements + text
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node.nodeType === 3) renderText(node);
    else renderAttributes(node);
  }
}

export function applyLanguage(root = activeDocument?.body) {
  if (!activeDocument) return;
  activeDocument.documentElement.lang = language;
  const select = activeDocument.getElementById('languageSelect');
  if (select) select.value = language;
  renderSubtree(root);
}

export function onLanguageChange(callback) {
  listeners.add(callback);
  return () => listeners.delete(callback);
}

export function setLanguage(next) {
  if (!supported.has(next)) return language;
  const changed = language !== next;
  language = next;
  try { globalThis.localStorage?.setItem(languageStorageKey, language); } catch { /* private browsing */ }
  applyLanguage();
  if (changed) listeners.forEach(callback => callback(language));
  return language;
}

export function initLanguage(document = globalThis.document) {
  if (!document) return language;
  if (activeDocument === document && observer) { applyLanguage(); return language; }
  observer?.disconnect();
  activeDocument = document;
  try {
    const saved = globalThis.localStorage?.getItem(languageStorageKey);
    if (supported.has(saved)) language = saved;
  } catch { /* UI still works when persistent storage is unavailable */ }
  const select = document.getElementById('languageSelect');
  if (select && !select.dataset.languageBound) {
    select.addEventListener('change', event => setLanguage(event.target.value));
    select.dataset.languageBound = 'true';
  }
  applyLanguage();
  if (typeof MutationObserver !== 'undefined') {
    observer = new MutationObserver(records => {
      for (const record of records) {
        if (record.type === 'characterData') renderText(record.target);
        else if (record.type === 'attributes') renderAttributes(record.target);
        else record.addedNodes.forEach(renderSubtree);
      }
    });
    observer.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: attributes });
  }
  return language;
}
