# Elemental Games Modding

A community-driven reverse engineering and modding project for classic Xbox titles built on the Elemental engine. This repository contains research, tools, documentation, and code reconstructions produced through static analysis — no original game assets or proprietary binaries are included or distributed.

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
├── tools/              # Utilities for binary analysis and content manipulation
├── reconstructed/      # Reconstructed C++ headers and source files
├── scripts/            # Automation scripts (Ghidra, Python, etc.)
└── README.md
```

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
