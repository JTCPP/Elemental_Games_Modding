# Local Xbox ISO import

The editor imports an original Xbox Azurik disc from a local `.iso` or `.xiso` file. No image or game asset is sent to an external service. The input disc image is opened read-only and is never patched or executed.

Use **Import ISO** in the editor, either select a file or enter its local path. A selected browser file is streamed to the local server, with upload progress. A local path avoids copying the entire image. Extraction runs in a background job with file and byte progress, and opens the imported game when ready. The same dialog reopens prior imports or returns to the original dump.

Each import receives a separate `imports/<id>/source`, project, export directory, and texture cache. The original dump, its project, and existing exports are preserved. Imported files are exclusively `default.xbe` and the `gamedata` subtree; videos and unrelated disc content are omitted.

The XDVDFS reader checks the opening and closing volume signature, directory record bounds, file extents, branch cycles, directory cycles, duplicate names, and safe Windows filenames before extraction. It rejects path traversal, device names, separators, alternate stream names, truncated archives, excessive directory sizes, and oversized images. The Xbox executable certificate must identify Azurik (`0x4D530007`) and the game archives must contain valid version 4 XBR headers. Missing game archives or a different game are rejected.

The supported volume bases are `0`, `0x18300000`, `0x0FD90000`, and `0x02080000`. These constants and the 2048-byte sector / binary directory record layout follow the public format reader in [XboxDev/extract-xiso](https://github.com/XboxDev/extract-xiso/blob/master/extract-xiso.c). This is an independent Python reader; it does not invoke that utility. Compressed CHD/CCI/CSO files, encrypted data, and arbitrary ISO-9660 discs are not supported. Maximum image size is 16 GiB, with disk space checked before upload and extraction.

## API

- `POST /api/import-iso` with JSON `{ "path": "C:\\games\\Azurik.iso" }`: start a background import, returning a job and HTTP 202.
- `POST /api/import-iso-upload` with a binary body, `Content-Type: application/octet-stream`, a content length, and a URL-encoded `X-File-Name`: receive the image locally and start extraction, returning HTTP 202.
- `GET /api/import-iso?id=<job-id>`: poll `status`, `progress`, byte/file counts, and an error or ready source directory.
- `GET /api/sources`: current source, available imports, and original-source availability.
- `POST /api/open-import` or `/api/open-source` with JSON `{ "id": "<job-id>" }`: atomically select an imported source and its independent project.
- `POST /api/open-source` with JSON `{ "id": "original" }`: return to the existing original project.

An installation without a configured dump starts normally, returns `needsImport: true`, and lets the user import a disc. `AZURIK_SOURCE` overrides the default source; otherwise a saved default project's source is reused, then `source/` beside the editor is tried. `AZURIK_TOOLKIT` overrides the optional toolkit location.

## Locks and editor-only placements

`POST /api/lock` with `{ "level": "a5", "id": "<object-id>", "locked": true }` persists an object lock. `{ "level": "a5", "all": true, "locked": false }` unlocks a level. Parts sharing a serialized placement share a lock. Locked ancestors protect descendants, and a transform which would indirectly move a locked descendant is rejected. Move, rotation, scale, reset, undo, and redo obey locks on the server.

All decoded objects with a finite position can be transformed in the editor. Objects without a verified serialized XBR placement use a persistent preview override (`previewOnly: true`), with a relative Euler ZYX rotation and scale applied to the source geometry. These transformations remain in the project, support undo/redo, and are counted separately from game edits. An export writes their `scene-overrides.json` with `gameExportable: false`; it does not invent XBR byte offsets or silently present them as game modifications. Collision geometry remains unchanged.

The tests use small synthetic XDVDFS fixtures and temporary projects. They cover all partition offsets, exact extracted bytes, malicious and truncated images, streamed uploads, job status, isolated source switching, persistent locks, shared placements, descendants, and preview transformation history. No commercial disc assets are required to run them.

The European disc's exact 60-byte empty `gamedata/loc.xbr` metadata placeholder is preserved during import. This narrow exception does not accept truncated level archives. Frozen builds store imports under `%LOCALAPPDATA%\AzurikLevelStudio`; source runs use the source tree.
