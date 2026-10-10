# Changelog

## 2.1.1 — 2026-10-10

- Separate the Explorer into Levels and Cinematics, with independent selectors, counts and remembered selections. The inspected European dump contains 24 levels and 9 cinematics; counts follow the opened source and project.
- Keep cinematics out of the level-management list and source-template picker while retaining the complete catalogue for identifier validation and history. Preserve custom level names when switching language.
- Correct native directional-light orientation under non-uniform scene transforms using the game's matrix-to-quaternion conversion. Keep the quaternion unnormalised and normalise only the final light direction in the shader. This repairs the incorrectly dark lighting in D2.
- Open D2 inside its mauve cavity shell. D2 has no dedicated sky pass; the apparent sky belongs to the level scenery. Preserve the source green outer cube enclosure visible in exterior views.
- Include bilingual guidance and an editor screenshot; these rendering corrections do not modify the source game archives.
- Automated validation: 766 passed, 3 skipped in 70.69 seconds. Xemu gameplay validation remains separate.

## 2.1.0 — 2026-10-10

- Add a bilingual Levels menu: clone a clean native source template, remove a level from the mod, and restore removed levels.
- Redirect existing level entries only to a retained clone of the same template; protect the selector and training room. Geometry, collisions, scripts and native IDs remain inherited.
- Preserve global Ctrl Z / Ctrl Shift Z chronology across catalog, transform and asset operations, including saved history after restarting and rollback on failed catalog saves.
- Export native XBR registration changes, compatible level aliases and an explicit `iso-plan.json` with checksums and removals.
- Build a separate mod ISO with native prefetch dependencies, rebuilt directories and file readback verification; preserve the source ISO and Xbox executable.
- Document structural/isolated validation separately from Xemu gameplay testing, which remains pending for new levels and inherited quest-state behavior. Empty-level compilation and cross-template spawn remapping are not included.

## 2.0.1 — 2026-10-10

- Cache unchanged instance lighting, reflection transforms and transparent bounds without reducing scene detail or textures.
- A local A5 benchmark reduces material-update CPU time from 4.19 to 1.16 ms per frame (about 72%); this does not measure GPU FPS.
- Distinguish stationary texture-preview cadence from FPS during camera movement.

## 2.0.0 — 2026-10-09

- Free camera by default, reliable viewport focus and fixed-position looking.
- File/edit/import/view/help menus and visible shortcut hints.
- Ctrl Z / Ctrl Shift Z history and reversible whole-level restoration.
- Static model import/replacement/duplication and PNG texture import/replacement.
- Guarded compatible native replacements and separate preview-asset export.
- Static transfer caching and reduced redundant viewport work.
- Version-aware desktop startup, updated EXE and owner-supplied Perathia Modding Hub icon.
- Cache guidance in the presentation, releases and English upstream pull request.

## 1.0.0 — 2026-10-09

First official release of Azurik Level Studio.

- Native desktop window, bundled Windows executable, launchers, icon and version metadata.
- Writable application-data directory for frozen builds; source projects remain compatible.
- French/English interface, ZQSD/WQSD controls, fixed-position looking and speed modifiers.
- Static scenery, placed models, sky variants, source textures/normals/materials, verified lighting and A5 fog/reflection preview.
- Transform tools, precise fields, saved projects, undo/redo and persistent locks.
- Guarded XBR exports and separately labelled preview-only overrides.
- Local ISO import, including the exact empty European `loc.xbr` placeholder.
- HTTP/1.1 responses to prevent large bundled module transfers resetting in WebView2.
- Owner-provided gallery, bilingual instructions, build script and checksums.
- Next-version model and texture import/replacement roadmap.
