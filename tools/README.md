# Tools and plugins

Each module has its own folder with source, launch files and documentation.

| Module | Folder | Purpose |
| --- | --- | --- |
| **Azurik Level Studio 2.1.1** | [level_studio](level_studio/README.md) | Native Windows 3D editor with French/English controls, global history, clean native level clones, compatible removal/restoration, registry export and separate verified mod ISO construction, alongside model/texture tools and asset inspection. |
| **Azurik Randomizer** | [randomizer](randomizer/README.md) | Full-game randomizer, solver and existing GUI/CLI tools. |

The renderer improvements from patch 2.0.1 retain full scene detail and textures while caching unchanged updates. A local A5 material-update benchmark fell from **4.19 to 1.16 ms per frame** (about 72% less CPU time); this does not measure GPU FPS.

Patch **2.1.1** separates **Levels** and **Cinematics** browsing (24 levels and 9 cinematics in the inspected European dump). It corrects D2 directional lighting using the native matrix-to-quaternion conversion under non-uniform transforms and opens the view inside the mauve cavity shell. D2 has no dedicated sky pass; its source green outer cube enclosure remains preserved in exterior views. [D2 illustration](level_studio/docs/screenshots/d2-2.1.1.jpg).

Level creation inherits the template's scripts, collision and native IDs. Original entrances can be redirected only to a retained clone of the same template. ISO structure and file checks do not certify gameplay; loading, portals and inherited quest state still need Xemu testing. [2.1.1 level and ISO guide](level_studio/docs/LEVELS_V21.md).

## Open the level editor

Double-click `level_studio/windows/Azurik Level Studio.exe` or `level_studio/Ouvrir Azurik Level Studio.bat`. The bundled executable needs no Python installation. Use **Import ISO** for your own Azurik Xbox ISO/XISO. See the [guide](level_studio/README.md), [screenshots](level_studio/docs/SCREENSHOTS.md) and [official Windows release](https://github.com/Azurik-Modding-Hub/Azurik-Level-Editor/releases/tag/v2.1.1).

Source use: install `requirements-desktop.txt`, then run `python desktop.py` inside `tools/level_studio`. WebView2 is required on Windows. Browser mode remains available with `requirements.txt` and `python server.py --open`.

## Add a module

Create a separate `tools/<module_name>/` folder and add source, dependencies, launchers and a README. Add it to this catalogue. Generated game data, user projects, imports and exports stay outside version control.
