# Elemental Games Modding

A community-driven reverse engineering and modding project for classic Xbox titles built on the Elemental engine. This repository contains research, tools, documentation, and code reconstructions produced through static analysis — no original game assets or proprietary binaries are included or distributed.

**Azurik Level Studio 2.1.1** is available as a [native Windows level editor](tools/level_studio/README.md), with a bundled executable, French/English controls, global undo/redo, model/texture tools and local ISO import. The new **Levels** menu creates registered native levels from clean templates, removes/restores levels in the mod and redirects existing entries to compatible clones of the same template. It exports registry changes and can build a separate mod ISO with updated native prefetch dependencies and file verification. [Download 2.1.1 for Windows](https://github.com/Azurik-Modding-Hub/Azurik-Level-Editor/releases/tag/v2.1.1) · [Screenshots](tools/level_studio/docs/SCREENSHOTS.md) · [Tools catalogue](tools/README.md) · [Dedicated editor repository](https://github.com/Azurik-Modding-Hub/Azurik-Level-Editor).

Created levels inherit their template's geometry, collisions, scripts, spawn/portal IDs and quest state. Empty-map compilation, arbitrary native model allocation and cross-template entry remapping are not included. Structural and isolated ISO checks pass; new level loading and quest behavior still need **Xemu runtime testing**. [Level workflow and limits](tools/level_studio/docs/LEVELS_V21.md).

The renderer improvements from patch 2.0.1 retain full scene detail and textures while caching unchanged updates. A local A5 material-update benchmark fell from **4.19 to 1.16 ms per frame** (about 72% less CPU time); this does not measure GPU FPS.

Patch **2.1.1** separates **Levels** and **Cinematics** browsing (24 levels and 9 cinematics in the inspected European dump). It corrects D2 directional lighting using the native matrix-to-quaternion conversion under non-uniform transforms and opens the view inside the mauve cavity shell. D2 has no dedicated sky pass; its source green outer cube enclosure remains preserved in exterior views. [D2 illustration](tools/level_studio/docs/screenshots/d2-2.1.1.jpg).

EDIT : You need to clear the cache for the changes you make to take effect ! Don’t worry, this won’t affect your save files in any way!

Clear only the Xbox game cache partitions; keep your virtual HDD and saves. [Cache guide](tools/level_studio/docs/GAME_CACHE.md).

---

## ⚠️ Legal Notice

This project is a **clean-room reverse engineering effort** conducted for preservation, research, and interoperability purposes. It contains only:

- Original tooling and scripts written by contributors
- Documentation and research notes derived from binary analysis
- Reconstructed code structures based on observed behavior

This project does **not** contain, distribute, or reproduce any copyrighted game assets, executables, ROMs, or disc images. You must own a legitimate copy of any game you wish to use with these tools.

---

## About

The Elemental engine powered several original Xbox titles. This project aims to document the engine's architecture, reconstruct its systems from compiled binaries using modern reverse engineering tooling, and provide a foundation for community modding.

Goals include:

- Documenting engine subsystems (rendering, AI, physics, animation, audio)
- Reconstructing C++ class hierarchies and data structures
- Building tooling to inspect, modify, and re-package game content
- Enabling the community to create mods, patches, and ports

---

## Repository Structure

```
Elemental_Games_Modding/
├── docs/               # Research notes, struct definitions, system documentation
├── tools/
│   └── randomizer/     # Full-game randomizer for Azurik (GUI + CLI)
├── reconstructed/      # Reconstructed C++ headers and source files
├── scripts/            # Automation scripts (Ghidra, Python, etc.)
└── README.md
```

### Azurik Randomizer

The headline tool is a **full-game randomizer** for Azurik: Rise of Perathia with a graphical interface and logic solver. It shuffles powers, fragments, keys, gems, and barriers while guaranteeing the game remains completable.

See [`tools/randomizer/README.md`](tools/randomizer/README.md) for setup and usage instructions.

---

## Tools & Methodology

This project uses the following open-source toolchain:

| Tool | Purpose |
|------|---------|
| [Ghidra](https://ghidra-sre.org) | Primary disassembler and decompiler |
| [GhydraMCP](https://github.com/starsong-consulting/GhydraMCP) | AI-assisted reverse engineering via MCP |
| [Claude Code](https://claude.ai/code) | Agentic code analysis and reconstruction |
| [xemu](https://xemu.app) | Original Xbox emulator for runtime verification |
| [xdvdfs](https://github.com/antangelo/xdvdfs) | Xbox disc image extraction |
| Python 3 | Scripting and binary analysis utilities |

The general workflow is:

1. Import XBE executable into Ghidra and run auto-analysis
2. Use GhydraMCP to give Claude Code a live bidirectional connection to Ghidra
3. Iteratively decompile, analyze, and reconstruct functions
4. Write inferred names, types, and struct definitions back into Ghidra
5. Validate behavior against xemu at runtime

---

## Getting Started

### Prerequisites

- Ghidra 11.x or later
- Python 3.10+
- A legitimate copy of the target game (disc image not provided)
- xemu (for runtime testing)

### Setup

```bash
git clone https://github.com/JTCPP/Elemental_Games_Modding.git
cd Elemental_Games_Modding
pip install -r tools/requirements.txt
```

See [`docs/setup.md`](docs/setup.md) for full environment setup instructions including Ghidra configuration and GhydraMCP integration.

---

## Contributing

Contributions are welcome. If you have:

- Identified a function or struct not yet documented
- Improved an existing reconstruction
- Built a tool that helps with analysis or modding
- Found a bug in the tooling

Please open an issue or pull request. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for guidelines.

All contributed code must be original work. Do not submit decompiled output verbatim — reconstructed code should be rewritten into clean, human-readable form with proper attribution of the analysis methodology.

---

## Acknowledgements

- [LaurieWired](https://github.com/LaurieWired) — GhidraMCP original
- [starsong-consulting](https://github.com/starsong-consulting/GhydraMCP) — GhydraMCP multi-instance fork
- The xemu development team
- The broader Xbox preservation and modding community

---

## License

Original tooling and documentation in this repository is released under the [MIT License](LICENSE).

Reconstructed code files represent the contributors' own original expression of observed binary behavior and are similarly MIT licensed. They are not copies or derivatives of any copyrighted source code.

### Azurik Level Studio

[Azurik Level Studio](tools/level_studio/README.md) is a local 3D level inspection and editing tool with French/English UI, free camera navigation, persistent object locks, source-preserving transform exports, preview-only placement overrides and local Xbox ISO import. It loads game data from the user’s own disc or extracted dump and does not include game assets. Rendering and export limitations are documented in the tool’s README.
