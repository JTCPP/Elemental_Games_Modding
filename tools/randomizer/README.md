# Azurik: Rise of Perathia — Randomizer

A full-game randomizer for **Azurik: Rise of Perathia** (Xbox, 2001). Shuffles collectibles, powers, fragments, keys, gems, and barriers to create a unique experience each playthrough. Includes a logic solver to guarantee completability.

## Features

- **Major Items**: Disc fragments and elemental powers shuffled cross-level with forward-fill logic solver
- **Keys**: Shuffled within their elemental realm
- **Gems**: Diamond, emerald, sapphire, ruby distribution randomized per-level (custom weights supported)
- **Barriers**: Element vulnerability randomized
- **Custom Item Pool**: Choose exactly how many of each item type to include
- **QoL Patches**: Disables gem first-pickup popups and all pickup celebration animations
- **Seed-based**: Reproducible results — share a seed to share a run
- **GUI and CLI**: Graphical interface or command-line for advanced users

## Requirements

- **Python 3.10+** with tkinter (included in most Python installs)
- **xdvdfs** — Xbox DVD filesystem tool ([download here](https://github.com/antangelo/xdvdfs/releases))
- **A legitimate copy** of Azurik: Rise of Perathia as an ISO disc image
- **xemu** ([xemu.app](https://xemu.app)) — Original Xbox emulator for playing the randomized ISO

## Setup

1. Clone or download this folder
2. Place `xdvdfs.exe` (or `xdvdfs`) into a `tools/` subfolder, or install it on your PATH
3. Place your game ISO in the `iso/` folder (or browse to it in the GUI)

```
randomizer/
├── azurik_mod.py          # CLI tool
├── azurik_gui_launcher.py # GUI launcher
├── solver.py              # Logic solver
├── logic_db.json          # World graph for solver
├── level_editor.py        # Level entity utilities
├── parse_level_toc.py     # XBR table-of-contents parser
├── azurik_gui/            # GUI package
├── claude_output/         # Generated data files
│   ├── config_registry.json   # Offset database (required)
│   └── property_schema.json   # Config schema (reference)
├── iso/                   # Place your game ISO here
│   └── .gitkeep
└── tools/                 # Place xdvdfs here
    └── (xdvdfs.exe)
```

## Usage

### GUI (Recommended)

```bash
python azurik_gui_launcher.py
```

The GUI will auto-detect your ISO from the `iso/` folder. Select your randomization categories, set a seed, and click **Build Randomized ISO**.

### CLI

```bash
# Full randomizer (all categories)
python azurik_mod.py randomize-full --iso "iso/Azurik - Rise of Perathia.iso" --seed 42 --output iso/Azurik_randomized.iso

# Skip specific categories
python azurik_mod.py randomize-full --iso game.iso --seed 42 --output out.iso --no-keys --no-barriers

# Custom item pool (e.g. 5 water powers, all obsidian gems)
python azurik_mod.py randomize-full --iso game.iso --seed 42 --output out.iso \
  --item-pool '{"power_water": 5, "power_air": 3, "frag_air_1": 2, "obsidian": 200, "diamond": 1}'

# Force build even if unsolvable
python azurik_mod.py randomize-full --iso game.iso --seed 42 --output out.iso --force
```

### Item Pool Format

The `--item-pool` argument accepts a JSON object with item counts and gem weights:

**Major items** (counts — placed into the 27 available slots):
| Key | Description | Vanilla Count |
|-----|-------------|---------------|
| `power_water` | Water Power | 2 |
| `power_water_a3` | Water Power (A3 variant) | 1 |
| `power_air` | Air Power | 3 |
| `power_earth` | Earth Power | 3 |
| `power_fire` | Fire Power | 3 |
| `frag_air_1` .. `frag_air_3` | Air Fragments | 1 each |
| `frag_water_1` .. `frag_water_3` | Water Fragments | 1 each |
| `frag_fire_1` .. `frag_fire_3` | Fire Fragments | 1 each |
| `frag_earth_1` .. `frag_earth_3` | Earth Fragments | 1 each |
| `frag_life_1` .. `frag_life_3` | Life Fragments | 1 each |

**Gem types** (relative weights — higher = more frequent):
| Key | Description |
|-----|-------------|
| `diamond` | Diamond gems |
| `emerald` | Emerald gems |
| `sapphire` | Sapphire gems |
| `ruby` | Ruby gems |
| `obsidian` | Obsidian gems |

## Playing on xemu

1. Build your randomized ISO
2. Open xemu and load the output ISO
3. **Important**: Disable "Cache Shaders" in xemu settings, or clear the HDD cache between runs — stale caches can cause visual glitches with randomized content

## Solvability

When **Major Items** randomization is enabled, the tool uses a forward-fill logic solver to guarantee the game is completable. The solver models the full world graph including power requirements, barriers, and progression gates.

If a custom item pool makes solvability impossible (e.g., removing all required powers), the tool will report this. You can use `--force` (CLI) or click "Build Anyway" (GUI) to create the ISO regardless.

**Keys** and **Barriers** randomization may occasionally produce unsolvable combinations depending on the seed — the GUI marks these with a warning.

## License

MIT License — see repository root.
