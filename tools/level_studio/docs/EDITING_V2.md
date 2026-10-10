# Model and texture editing in 2.0.0

Use the **Import** menu for a new model or PNG texture. Select a scene model to replace or duplicate it. Open a level texture's details to replace that texture. The dialogs and results distinguish game-exportable replacements from project previews.

## Models

Static triangle geometry can be imported from OBJ, geometry JSON or glTF/GLB. glTF sidecar files must be explicitly selected alongside the main file; remote resources are not fetched. Materials, rigs and animations are not imported as retail Xbox shader or skeletal records. Choose a level/imported texture for the resulting model.

New models and duplicated instances are **project previews**. They can be moved, rotated, scaled, saved, undone and redone independently. Export includes their model geometry, textures and placements in a separate preview package. They are not new native scene records and will not appear in an exported game disc.

Replacement of a supported existing native mesh can be written to the game when the imported geometry keeps the validated vertex/triangle layout and representable coordinate/UV ranges. This updates the existing resource instead of allocating a new one. A shared vertex pool may affect other parts or instances, so check the reported scope. Unsupported topology or attributes remain a clearly labelled preview.

Native shaders, collision meshes, scripts, skeletons and game-state visibility links remain unchanged. A visual replacement does not make its collision shape follow it.

The replacement dialog requires a coordinate choice: **Local model coordinates** for the native asset geometry (including its original pool origin), or **Level coordinates (world)** for an exported placed instance. World coordinates are converted through the selected instance's inverse placement before writing the native resource, including its normals. Ambiguous requests without an explicit choice remain previews. Restore an existing preview replacement before using its world placement for a native replacement.

Static geometry must also remain within the original serialized LEVL bounds of every descriptor using the vertex pool, including LOD alternatives. Validation uses the encoded/quantized candidate positions. Larger changes that exceed these bounds remain previews; cell/portal bounds are not rebuilt.

## Textures

Import a local PNG to add a texture to the project's asset browser. New textures can be assigned to imported preview models without modifying the source archive.

Compatible replacement of an existing level surface is game-exportable:

- A verified two-dimensional **BGRA8, DXT1 or DXT3** native resource.
- The same width and height as the original surface.
- At most **1024 × 1024** pixels for a native replacement; imported preview textures can be up to **4096 × 4096**.
- A complete mip chain encoded in the original resource's available storage.
- The original source resource and byte ranges still pass validation.

DXT encoding is lossy. Cubemaps, unsupported formats and resources without a verified native binding remain previews. Replacing a shared texture changes every model/material using that texture; it does not create a texture unique to the selected object.

## History, locks and restoration

**Ctrl Z** undoes one operation; **Ctrl Shift Z** redoes it. **Ctrl Y** is also supported. Editing a text field keeps that field's ordinary text-editing shortcuts.

**Restore level** clears the active level's visual edits and asset changes back to the source as one history operation. You can undo this restoration. Other levels remain edited. Protection locks are preserved. Imported files needed by undo remain available within the project.

Save persists the project and history. The source dump and ISO are always read-only. The export report lists game changes and preview changes separately; it also includes checksums, validated starting offsets and the number of changed bytes for written native resources.

## Français

Le menu **Importation** permet d’ajouter modèles et textures PNG. Sélectionnez un modèle pour le remplacer ou le dupliquer ; ouvrez les détails d’une texture du niveau pour la remplacer.

Les **nouveaux modèles et duplications** sont des éléments d’aperçu du projet : ils sont enregistrés, modifiables, annulables et exportés avec leurs fichiers, mais ne sont pas ajoutés automatiquement au jeu Xbox.

Les **remplacements compatibles** utilisent les ressources natives existantes : géométrie validée sans changement de structure, ou texture 2D BGRA8/DXT1/DXT3 aux dimensions originales. Les ressources partagées peuvent modifier plusieurs objets. Les collisions et scripts restent inchangés.

Pour remplacer un modèle, choisissez ses **coordonnées locales** (avec l’origine de la réserve native) ou les **coordonnées du niveau** d’une instance exportée. La conversion vers les coordonnées natives évite d’appliquer deux fois le placement du modèle.

La géométrie statique doit rester dans les limites LEVL originales de toutes les utilisations de sa réserve, y compris les autres niveaux de détail. Un agrandissement hors de ces limites reste un aperçu ; les limites des cellules et portails ne sont pas reconstruites.

**Ctrl Z** annule ; **Ctrl Maj Z** rétablit. **Restaurer le niveau** remet le niveau actif à son état source en une seule étape réversible. Les autres niveaux et les verrouillages sont conservés. Consultez le rapport pour distinguer ce qui est appliqué au jeu de ce qui reste un aperçu.
