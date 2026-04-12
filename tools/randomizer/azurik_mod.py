"""
Azurik: Rise of Perathia — Mod Tool
====================================
Applies mod definitions to a game ISO, producing a patched ISO for xemu.

Primary usage (all-in-one):
    python azurik_mod.py patch --iso Azurik.iso --mod mods/mod.json --output Azurik_modded.iso

Utility commands:
    python azurik_mod.py list  --sections
    python azurik_mod.py list  --entities critters_walking
    python azurik_mod.py dump  --iso Azurik.iso --section settings_foo --entity air
    python azurik_mod.py diff  --iso Azurik.iso --mod mods/mod.json

Requires:
  - config_registry.json in claude_output/ (offset database)
  - xdvdfs on PATH or in ./tools/ (Xbox ISO tool)
    Download from: https://github.com/antangelo/xdvdfs/releases

Mod file formats:

  Grouped (recommended):
    {
      "name": "My Mod",
      "format": "grouped",
      "sections": {
        "critters_walking": {
          "air": {
            "enemies": {
              "air_elemental": {
                "provoke_distance": 30.0,
                "max_distance": 5000
              }
            }
          }
        },
        "settings_foo": {
          "air": { "initial_fuel": 5.0 }
        }
      }
    }

  Legacy flat (still supported):
    {
      "name": "My Mod",
      "patches": [
        {"section": "critters_walking", "entity": "air_elemental",
         "property": "provoke_distance", "value": 30.0}
      ]
    }

  Level entity patches (move/rename entities in level XBR files):
    {
      "name": "My Level Mod",
      "level": "w2",
      "level_patches": [
        {"entity": "diamond", "action": "move", "x": -90.0, "y": -35.0, "z": -7.0},
        {"entity": "power_water_a3", "action": "rename", "new_name": "power_fire_a3"}
      ]
    }
"""

import argparse
import json
import random
import shutil
import struct
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = SCRIPT_DIR / "claude_output" / "config_registry.json"
XDVDFS_DOWNLOAD = "https://github.com/antangelo/xdvdfs/releases"

# Relative path within the game folder where config.xbr lives
CONFIG_XBR_REL = Path("gamedata") / "config.xbr"
# Level XBR files are flat inside gamedata/
GAMEDATA_REL = Path("gamedata")


# ---------------------------------------------------------------------------
# xdvdfs helpers
# ---------------------------------------------------------------------------

def find_xdvdfs() -> str | None:
    found = shutil.which("xdvdfs")
    if found:
        return found
    for name in ("xdvdfs", "xdvdfs.exe"):
        local = SCRIPT_DIR / "tools" / name
        if local.exists():
            return str(local)
    return None


def require_xdvdfs() -> str:
    path = find_xdvdfs()
    if path:
        return path
    print("ERROR: xdvdfs not found.")
    print(f"  Download from: {XDVDFS_DOWNLOAD}")
    print(f"  Place in: {SCRIPT_DIR / 'tools' / 'xdvdfs.exe'}")
    print("  Or: cargo install xdvdfs-cli")
    sys.exit(1)


def run_xdvdfs(xdvdfs: str, args: list[str]):
    cmd = [xdvdfs] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  xdvdfs error: {result.stderr.strip()}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Registry + binary helpers
# ---------------------------------------------------------------------------

_registry_cache = None

def load_registry() -> dict:
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache
    if not REGISTRY_PATH.exists():
        print(f"ERROR: Registry not found at {REGISTRY_PATH}")
        sys.exit(1)
    with open(REGISTRY_PATH, "r") as f:
        _registry_cache = json.load(f)
    return _registry_cache


def read_value(data: bytes, offset: int, type_flag: int):
    """Read a config value from config.xbr at the given offset.

    Config values are stored as 8-byte IEEE 754 doubles (little-endian).
    The offset should point to the start of the double (record_base + 4).
    """
    if type_flag == 2:
        # Integer values are doubles that represent whole numbers
        return int(struct.unpack_from("<d", data, offset)[0])
    return struct.unpack_from("<d", data, offset)[0]


def write_value(data: bytearray, offset: int, value, type_flag: int):
    """Write a config value to config.xbr at the given offset.

    Writes an 8-byte IEEE 754 double (little-endian).
    """
    if type_flag == 2:
        struct.pack_into("<d", data, offset, float(int(value)))
    else:
        struct.pack_into("<d", data, offset, float(value))


def format_value(val, type_flag: int) -> str:
    if type_flag == 2:
        return str(int(val)) if isinstance(val, (int, float)) else str(val)
    if isinstance(val, float):
        if val == int(val):
            return f"{val:.1f}"
        return f"{val:.4f}"
    return str(val)


def resolve_prop(registry: dict, section: str, entity: str, prop: str) -> dict | None:
    sec = registry.get("sections", {}).get(section)
    if not sec:
        return None
    ent = sec.get("entities", {}).get(entity)
    if not ent:
        return None
    p = ent.get("properties", {}).get(prop)
    if p and p.get("type_flag", 0) > 2:
        # type_flag > 2 means this offset lands in the string table — invalid
        return None
    return p


def load_mod(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: Mod file not found: {p}")
        sys.exit(1)
    with open(p, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in {p.name}")
            print(f"  Line {e.lineno}, column {e.colno}: {e.msg}")
            lines = e.doc.splitlines()
            if 0 < e.lineno <= len(lines):
                print(f"  > {lines[e.lineno - 1]}")
            print(f"\n  Hint: Check for trailing commas, missing quotes, or unmatched brackets.")
            sys.exit(1)


def extract_config_from_iso(iso_path: Path) -> bytearray:
    """Extract config.xbr from an ISO into memory via temp dir."""
    xdvdfs = require_xdvdfs()
    with tempfile.TemporaryDirectory(prefix="azurik_read_") as tmpdir:
        tmp = Path(tmpdir)
        # Extract just config.xbr using copy-out
        out_file = tmp / "config.xbr"
        # Use forward slashes for the in-image path — xdvdfs requires
        # POSIX separators for Xbox filesystem entries.
        run_xdvdfs(xdvdfs, ["copy-out", str(iso_path),
                             CONFIG_XBR_REL.as_posix(), str(out_file)])
        if not out_file.exists():
            print(f"ERROR: Could not extract {CONFIG_XBR_REL} from {iso_path}")
            sys.exit(1)
        data = bytearray(out_file.read_bytes())

    if data[:4] != b"xobx":
        print(f"ERROR: Extracted config.xbr has bad magic: {data[:4]!r}")
        sys.exit(1)
    return data


def read_config_data(args) -> bytearray:
    """Read config.xbr from either --iso or --input (raw .xbr file)."""
    if hasattr(args, 'iso') and args.iso:
        iso_path = Path(args.iso)
        if not iso_path.exists():
            print(f"ERROR: ISO not found: {iso_path}")
            sys.exit(1)
        print(f"  Extracting config.xbr from {iso_path}...")
        return extract_config_from_iso(iso_path)
    elif hasattr(args, 'input') and args.input:
        p = Path(args.input)
        if not p.exists():
            print(f"ERROR: File not found: {p}")
            sys.exit(1)
        data = bytearray(p.read_bytes())
        if data[:4] != b"xobx":
            print(f"ERROR: {p} is not a valid XBR file")
            sys.exit(1)
        return data
    else:
        print("ERROR: Specify --iso (game ISO) or --input (raw config.xbr)")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Mod format handling — grouped and legacy flat
# ---------------------------------------------------------------------------

def flatten_mod(mod: dict, registry: dict) -> list[dict]:
    """Convert any mod format into a flat list of patch dicts.

    Supports:
      - Legacy flat: mod has "patches" key with list of {section, entity, property, value}
      - Grouped: mod has "sections" key with nested element/role/entity/property structure
    """
    # Legacy flat format
    if "patches" in mod:
        return mod["patches"]

    # Grouped format: walk the nested tree to find entity names
    if "sections" not in mod:
        return []

    patches = []
    reg_sections = registry.get("sections", {})

    for sec_name, sec_content in mod["sections"].items():
        if not isinstance(sec_content, dict):
            continue

        # Build set of known entity names for this section
        reg_sec = reg_sections.get(sec_name, {})
        known_entities = set(reg_sec.get("entities", {}).keys())

        def _walk(obj, path=""):
            """Recursively walk until we hit a known entity name,
            then treat its children as property: value pairs."""
            for key, val in obj.items():
                if key.startswith("_"):
                    continue
                if not isinstance(val, dict):
                    continue
                if key in known_entities:
                    # Check if children are property values (numbers) or
                    # nested groups (dicts) — handles name collisions like
                    # "debug" being both a role name and an entity name
                    has_dicts = any(isinstance(v, dict) for v in val.values())
                    if has_dicts:
                        # Children are dicts — this is a group, recurse
                        _walk(val, f"{path}/{key}")
                    else:
                        # Children are values — this is an entity
                        for prop_name, prop_val in val.items():
                            if isinstance(prop_val, (int, float)):
                                patches.append({
                                    "section": sec_name,
                                    "entity": key,
                                    "property": prop_name,
                                    "value": prop_val,
                                })
                else:
                    # Group or role name — recurse deeper
                    _walk(val, f"{path}/{key}")

        _walk(sec_content)

    return patches


# ---------------------------------------------------------------------------
# Level entity patching (uses level_editor.py discovery format)
# ---------------------------------------------------------------------------

def find_level_entities(data: bytes) -> dict[str, dict]:
    """Find named entities in a level XBR using the 1.0f+name coordinate pattern.

    Returns dict keyed by entity name with coord_offset, x, y, z, name_offset, name_len.
    Tries multiple coordinate offsets: -96 (standard), -116 (typed nodes), -144, -140.
    """
    COORD_OFFSETS = [-96, -116, -144, -140]
    entities = {}
    i = 0
    while i < len(data) - 8:
        if (data[i] == 0x00 and data[i+1] == 0x00
                and data[i+2] == 0x80 and data[i+3] == 0x3F
                and i + 4 < len(data) and 33 <= data[i+4] < 127):
            s_start = i + 4
            s_end = s_start
            while s_end < len(data) and data[s_end] != 0 and s_end - s_start < 200:
                if data[s_end] < 32 or data[s_end] >= 127:
                    break
                s_end += 1
            if s_end < len(data) and data[s_end] == 0 and s_end - s_start >= 2:
                name = data[s_start:s_end].decode("ascii")
                for co in COORD_OFFSETS:
                    cp = s_start + co
                    if cp >= 0 and cp + 16 <= len(data):
                        x, y, z, w = struct.unpack_from("<4f", data, cp)
                        if (w == 0.0
                                and all(abs(v) < 50000 for v in [x, y, z] if v == v)):
                            entities[name] = {
                                "name_offset": s_start,
                                "name_len": s_end - s_start,
                                "coord_offset": cp,
                                "x": x, "y": y, "z": z,
                            }
                            break
                i = s_end + 1
                continue
        i += 1
    return entities


def find_null_terminated_string(data: bytes, name: str) -> list[dict]:
    """Find all occurrences of a null-terminated ASCII string in the file.

    Returns list of {offset, length} for each match where the string is
    preceded by a null byte (or is at position 0) and followed by a null byte.
    """
    needle = name.encode("ascii")
    results = []
    pos = 0
    while True:
        pos = data.find(needle, pos)
        if pos == -1:
            break
        end = pos + len(needle)
        # Must be null-terminated
        if end < len(data) and data[end] == 0:
            # Must be preceded by null or start of file (not mid-string)
            if pos == 0 or data[pos - 1] == 0 or data[pos - 1] < 32:
                # Verify the full string matches exactly (no trailing chars before null)
                results.append({"offset": pos, "length": len(needle)})
        pos += 1
    return results


def apply_level_patches(data: bytearray, patches: list[dict]) -> tuple[int, int]:
    """Apply move/rename patches to a level XBR. Returns (applied, errors)."""
    entities = find_level_entities(data)
    applied = errors = 0

    for p in patches:
        ent_name = p.get("entity", "")
        action = p.get("action", "move")

        if action == "move":
            ent = entities.get(ent_name)
            if not ent:
                print(f"    ERROR: Entity '{ent_name}' not found (1.0f+name pattern)")
                errors += 1
                continue
            nx, ny, nz = float(p["x"]), float(p["y"]), float(p["z"])
            old = f"({ent['x']:.2f}, {ent['y']:.2f}, {ent['z']:.2f})"
            new = f"({nx:.2f}, {ny:.2f}, {nz:.2f})"
            struct.pack_into("<3f", data, ent["coord_offset"], nx, ny, nz)
            print(f"    {ent_name}: {old} -> {new}")
            applied += 1

        elif action == "rename":
            new_name = p["new_name"]

            # First try: 1.0f+name pattern entities
            ent = entities.get(ent_name)
            if ent:
                old_len = ent["name_len"]
                if len(new_name) > old_len:
                    print(f"    ERROR: '{new_name}' ({len(new_name)} chars) > "
                          f"'{ent_name}' ({old_len} chars)")
                    errors += 1
                    continue
                offset = ent["name_offset"]
                new_bytes = new_name.encode("ascii") + b"\x00" * (old_len - len(new_name))
                data[offset:offset + old_len] = new_bytes
                print(f"    rename: '{ent_name}' -> '{new_name}' @0x{offset:08X}")
                applied += 1
                continue

            # Fallback: search for null-terminated string anywhere in file
            matches = find_null_terminated_string(data, ent_name)
            if not matches:
                print(f"    ERROR: Entity '{ent_name}' not found anywhere in file")
                errors += 1
                continue

            if len(new_name) > len(ent_name):
                print(f"    ERROR: '{new_name}' ({len(new_name)} chars) > "
                      f"'{ent_name}' ({len(ent_name)} chars) — would corrupt adjacent data")
                errors += 1
                continue

            # Rename all occurrences
            for match in matches:
                offset = match["offset"]
                old_len = match["length"]
                new_bytes = new_name.encode("ascii") + b"\x00" * (old_len - len(new_name))
                data[offset:offset + old_len] = new_bytes
                print(f"    rename: '{ent_name}' -> '{new_name}' @0x{offset:08X}")
            applied += 1

        elif action == "raw_patch":
            # Direct binary patch at a specific file offset
            file_offset = p.get("file_offset")
            if file_offset is None:
                print(f"    ERROR: raw_patch requires 'file_offset'")
                errors += 1
                continue
            if isinstance(file_offset, str):
                file_offset = int(file_offset, 0)  # supports "0x..." hex

            value = p.get("value")
            value_type = p.get("value_type", "uint16")

            if value_type == "uint16":
                if file_offset + 2 > len(data):
                    print(f"    ERROR: offset 0x{file_offset:08X} out of range")
                    errors += 1
                    continue
                old_val = struct.unpack_from("<H", data, file_offset)[0]
                struct.pack_into("<H", data, file_offset, int(value))
                print(f"    raw_patch @0x{file_offset:08X}: uint16 {old_val} -> {int(value)}"
                      f"  ({ent_name})")
                applied += 1
            elif value_type == "uint32":
                if file_offset + 4 > len(data):
                    print(f"    ERROR: offset 0x{file_offset:08X} out of range")
                    errors += 1
                    continue
                old_val = struct.unpack_from("<I", data, file_offset)[0]
                struct.pack_into("<I", data, file_offset, int(value))
                print(f"    raw_patch @0x{file_offset:08X}: uint32 {old_val} -> {int(value)}"
                      f"  ({ent_name})")
                applied += 1
            elif value_type == "float":
                if file_offset + 4 > len(data):
                    print(f"    ERROR: offset 0x{file_offset:08X} out of range")
                    errors += 1
                    continue
                old_val = struct.unpack_from("<f", data, file_offset)[0]
                struct.pack_into("<f", data, file_offset, float(value))
                print(f"    raw_patch @0x{file_offset:08X}: float {old_val:.4f} -> {float(value):.4f}"
                      f"  ({ent_name})")
                applied += 1
            else:
                print(f"    ERROR: Unknown value_type '{value_type}'")
                errors += 1

        else:
            print(f"    ERROR: Unknown action '{action}' for {ent_name}")
            errors += 1

    return applied, errors


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(args):
    registry = load_registry()
    sections = registry.get("sections", {})

    if args.sections:
        print("Available sections:\n")
        for name, sec in sorted(sections.items()):
            n = len(sec.get("entities", {}))
            conf = sec.get("confidence", "?")
            desc = sec.get("description", "")[:70]
            print(f"  {name:<25} {n:>4} entities  [{conf}]  {desc}")

    elif args.entities:
        section = sections.get(args.entities)
        if not section:
            print(f"ERROR: Section '{args.entities}' not found.")
            print(f"  Available: {', '.join(sorted(sections.keys()))}")
            sys.exit(1)
        entities = section.get("entities", {})
        print(f"Entities in {args.entities} ({len(entities)} total):\n")
        for name in sorted(entities.keys()):
            n = len(entities[name].get("properties", {}))
            print(f"  {name:<35} {n} properties")
    else:
        print("Use --sections or --entities <section_name>")


def cmd_dump(args):
    registry = load_registry()
    data = read_config_data(args)
    sections = registry.get("sections", {})
    section = sections.get(args.section)
    if not section:
        print(f"ERROR: Section '{args.section}' not found.")
        sys.exit(1)

    entities = section.get("entities", {})
    names = [args.entity] if args.entity else sorted(entities.keys())

    for ent_name in names:
        ent = entities.get(ent_name)
        if not ent:
            print(f"  WARNING: Entity '{ent_name}' not found")
            continue
        print(f"\n  [{args.section}] {ent_name}")
        for prop_name, prop in sorted(ent["properties"].items(),
                                       key=lambda x: x[1].get("prop_index", 0)):
            offset = int(prop["value_file_offset"], 16)
            tf = prop.get("type_flag", 0)
            val = read_value(data, offset, tf) if offset + 4 <= len(data) else "?"
            ts = {0: "unset", 1: "float", 2: "int"}.get(tf, f"?{tf}")
            print(f"    {prop_name:<30} = {format_value(val, tf):<15} [{ts}]  @0x{offset:06X}")


def cmd_diff(args):
    registry = load_registry()
    data = read_config_data(args)

    all_patches = []
    for mod_path in args.mod:
        mod = load_mod(mod_path)
        print(f"Mod: {mod.get('name', mod_path)}")
        if mod.get("description"):
            print(f"  {mod['description']}")
        all_patches.extend(flatten_mod(mod, registry))

    print()
    changes = errors = 0
    for i, p in enumerate(all_patches):
        prop = resolve_prop(registry, p["section"], p["entity"], p["property"])
        if not prop:
            print(f"  [{i+1}] ERROR: {p['section']}/{p['entity']}/{p['property']} not found")
            errors += 1
            continue
        offset = int(prop["value_file_offset"], 16)
        tf = prop.get("type_flag", 0)
        cur = read_value(data, offset, tf) if offset + 4 <= len(data) else "?"
        cur_s = format_value(cur, tf)
        new_s = format_value(p["value"], tf)
        marker = "~" if cur_s != new_s else "="
        print(f"  [{i+1}] {marker} {p['section']}/{p['entity']}/{p['property']}: {cur_s} -> {new_s}")
        if cur_s != new_s:
            changes += 1

    print(f"\n  {changes} changed, {errors} errors, "
          f"{len(all_patches) - changes - errors} unchanged")


def cmd_patch(args):
    """Main command: ISO in + mod(s) -> patched ISO out."""
    xdvdfs = require_xdvdfs()
    registry = load_registry()
    iso_path = Path(args.iso)
    out_path = Path(args.output)

    if not iso_path.exists():
        print(f"ERROR: ISO not found: {iso_path}")
        sys.exit(1)
    if iso_path.resolve() == out_path.resolve():
        print("ERROR: --output must differ from --iso")
        sys.exit(1)

    # Collect all patches from all mods, separating config vs level patches
    all_config_patches = []
    all_level_mods = []  # list of (level_name, level_patches_list)
    for mod_path in args.mod:
        mod = load_mod(mod_path)
        print(f"  Mod: {mod.get('name', mod_path)}")
        all_config_patches.extend(flatten_mod(mod, registry))
        if "level_patches" in mod and "level" in mod:
            all_level_mods.append((mod["level"], mod["level_patches"]))

    has_config = len(all_config_patches) > 0
    has_level = len(all_level_mods) > 0
    total_steps = 1 + int(has_config) + int(has_level) + 1  # extract + patches + repack

    with tempfile.TemporaryDirectory(prefix="azurik_mod_") as tmpdir:
        extract_dir = Path(tmpdir) / "game"

        # Step 1: Extract full game
        step = 1
        print(f"\n[{step}/{total_steps}] Extracting {iso_path.name}...")
        run_xdvdfs(xdvdfs, ["unpack", str(iso_path), str(extract_dir)])

        # Validate
        if not (extract_dir / "default.xbe").exists():
            print("  ERROR: Extracted folder missing default.xbe — not a valid game ISO")
            sys.exit(1)

        # Patch config.xbr if there are config patches
        if has_config:
            step += 1
            config_xbr = extract_dir / CONFIG_XBR_REL
            if not config_xbr.exists():
                print(f"  ERROR: {CONFIG_XBR_REL} not found in extracted game")
                sys.exit(1)

            print(f"[{step}/{total_steps}] Patching config.xbr ({len(all_config_patches)} changes)...")
            data = bytearray(config_xbr.read_bytes())
            if data[:4] != b"xobx":
                print("  ERROR: config.xbr has bad magic")
                sys.exit(1)

            applied = errors = 0
            for p in all_config_patches:
                path = f"{p['section']}/{p['entity']}/{p['property']}"
                prop = resolve_prop(registry, p["section"], p["entity"], p["property"])
                if not prop:
                    print(f"    ERROR: {path} not found in registry")
                    errors += 1
                    continue

                offset = int(prop["value_file_offset"], 16)
                tf = prop.get("type_flag", 0)
                if offset + 4 > len(data):
                    print(f"    ERROR: offset 0x{offset:06X} out of range for {path}")
                    errors += 1
                    continue

                old = read_value(data, offset, tf)
                write_value(data, offset, p["value"], tf)
                new = read_value(data, offset, tf)
                print(f"    {path}: "
                      f"{format_value(old, tf)} -> {format_value(new, tf)}")
                applied += 1

            config_xbr.write_bytes(data)
            print(f"  {applied} applied, {errors} errors")

        # Patch level XBR files if there are level patches
        if has_level:
            step += 1
            print(f"[{step}/{total_steps}] Patching level files...")
            for level_name, level_patches in all_level_mods:
                level_file = extract_dir / GAMEDATA_REL / f"{level_name}.xbr"
                if not level_file.exists():
                    print(f"  ERROR: {level_name}.xbr not found in extracted game")
                    continue

                print(f"  Patching {level_name}.xbr ({len(level_patches)} changes)...")
                ldata = bytearray(level_file.read_bytes())
                if ldata[:4] != b"xobx":
                    print(f"  ERROR: {level_name}.xbr has bad magic")
                    continue

                la, le = apply_level_patches(ldata, level_patches)
                level_file.write_bytes(ldata)
                print(f"  {la} applied, {le} errors")

        # Repack full game into new ISO
        step += 1
        print(f"[{step}/{total_steps}] Building {out_path.name}...")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        run_xdvdfs(xdvdfs, ["pack", str(extract_dir), str(out_path)])

        if out_path.exists():
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"\n  Done! {out_path} ({size_mb:.1f} MB)")
            print(f"  Load in xemu to play.")
        else:
            print("\n  ERROR: ISO creation failed")
            sys.exit(1)


# ---------------------------------------------------------------------------
# Gem randomizer (ISO pipeline)
# ---------------------------------------------------------------------------

# Level files that contain playable areas with gems.
# Excludes non-level XBRs (config, characters, english, fx, etc.) and
# special/cutscene levels that have no gem spawns.
LEVEL_XBRS = [
    "a1", "a3", "a5", "a6",
    "w1", "w2", "w3", "w4",
    "f1", "f2", "f3", "f4", "f6",
    "e2", "e5", "e6", "e7",
    "d1", "d2",
    "town", "life", "training_room",
]

GEM_TYPES = ["diamond", "emerald", "sapphire", "obsidian", "ruby"]
NAME_FIELD_SIZE = 20  # fixed-width name field in critterGenerator entries


def _gem_base_type(name: str):
    """Return the base gem type if name is a gem entity, else None."""
    lower = name.lower()
    for gem in GEM_TYPES:
        if lower == gem or lower.startswith(gem + "_"):
            return gem
    return None


def _find_level_gem_entities(data: bytes) -> list[dict]:
    """Find gem entities in level data using the 1.0f+name pattern."""
    entities = find_level_entities(data)  # reuse existing function
    gems = []
    for name, info in entities.items():
        base = _gem_base_type(name)
        if base is not None:
            suffix = name[len(base):]
            gems.append({
                "name": name,
                "name_offset": info["name_offset"],
                "name_len": info["name_len"],
                "coord_offset": info["coord_offset"],
                "x": info["x"], "y": info["y"], "z": info["z"],
                "gem_base": base,
                "gem_suffix": suffix,
            })
    return gems


def cmd_randomize_gems(args):
    """Randomize gem types across all level files in an ISO."""
    xdvdfs = require_xdvdfs()
    iso_path = Path(args.iso)
    out_path = Path(args.output)

    if not iso_path.exists():
        print(f"ERROR: ISO not found: {iso_path}")
        sys.exit(1)
    if iso_path.resolve() == out_path.resolve():
        print("ERROR: --output must differ from --iso")
        sys.exit(1)

    seed = args.seed
    levels = args.levels if args.levels else LEVEL_XBRS

    print(f"Azurik Gem Randomizer")
    print(f"  Seed: {seed}")
    print(f"  Levels: {len(levels)}")

    with tempfile.TemporaryDirectory(prefix="azurik_rand_") as tmpdir:
        extract_dir = Path(tmpdir) / "game"

        # Step 1: Extract
        print(f"\n[1/3] Extracting {iso_path.name}...")
        run_xdvdfs(xdvdfs, ["unpack", str(iso_path), str(extract_dir)])

        if not (extract_dir / "default.xbe").exists():
            print("  ERROR: Extracted folder missing default.xbe — not a valid game ISO")
            sys.exit(1)

        # Step 2: Randomize gems in each level
        print(f"\n[2/3] Randomizing gems...")
        total_gems = 0
        total_changes = 0
        levels_with_gems = 0
        global_before = Counter()
        global_after = Counter()

        # Use a seeded RNG for the whole run.  Each level gets a
        # deterministic sub-seed derived from the master so the result
        # is stable regardless of which levels are included.
        master_rng = random.Random(seed)

        for level_name in sorted(levels):
            level_file = extract_dir / GAMEDATA_REL / f"{level_name}.xbr"
            if not level_file.exists():
                print(f"  {level_name}.xbr — not found, skipping")
                continue

            data = bytearray(level_file.read_bytes())
            if data[:4] != b"xobx":
                print(f"  {level_name}.xbr — bad magic, skipping")
                continue

            gems = _find_level_gem_entities(data)
            if len(gems) < 2:
                if gems:
                    print(f"  {level_name}.xbr — {len(gems)} gem (need 2+, skipping)")
                continue

            # Per-level deterministic seed
            level_seed = master_rng.randint(0, 2**31)
            level_rng = random.Random(level_seed)

            base_types = [g["gem_base"] for g in gems]
            before = Counter(base_types)
            level_rng.shuffle(base_types)
            after = Counter(base_types)

            changes = 0
            for g, new_base in zip(gems, base_types):
                new_name = new_base + g["gem_suffix"]
                if len(new_name) > NAME_FIELD_SIZE:
                    continue
                if g["name"] != new_name:
                    changes += 1
                offset = g["name_offset"]
                new_bytes = new_name.encode("ascii").ljust(NAME_FIELD_SIZE, b"\x00")
                data[offset:offset + NAME_FIELD_SIZE] = new_bytes

            level_file.write_bytes(data)

            global_before += before
            global_after += after
            total_gems += len(gems)
            total_changes += changes
            levels_with_gems += 1

            dist_str = ", ".join(f"{t}:{before.get(t, 0)}->{after.get(t, 0)}"
                                 for t in GEM_TYPES if before.get(t, 0) or after.get(t, 0))
            print(f"  {level_name}.xbr — {len(gems)} gems, {changes} changed  [{dist_str}]")

        print(f"\n  Summary: {total_gems} gems across {levels_with_gems} levels, "
              f"{total_changes} changed")
        print(f"  Global distribution: {dict(global_before)} -> {dict(global_after)}")

        # Step 3: Repack
        print(f"\n[3/3] Building {out_path.name}...")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        run_xdvdfs(xdvdfs, ["pack", str(extract_dir), str(out_path)])

        if out_path.exists():
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"\n  Done! {out_path} ({size_mb:.1f} MB)")
            print(f"  Seed: {seed}  (use same seed to reproduce)")
            print(f"  Load in xemu to play.")
        else:
            print("\n  ERROR: ISO creation failed")
            sys.exit(1)


# ---------------------------------------------------------------------------
# Unified randomizer (gems + fragments + power-ups)
# ---------------------------------------------------------------------------

# Known power-up element types (mapped from entity names)
POWER_ELEMENTS = ["water", "air", "earth", "fire", "ammo", "life", "staff"]

# Town temple power-up names (behind obsidian gates, not keys)
TOWN_POWERS = ["power_life", "power_ammo", "power_staff1", "power_staff2"]

# Key realm assignments for within-realm shuffling
KEY_REALMS = {
    "air":   [("a6", "key_blue"), ("a6", "key_green"), ("a6", "key_red")],
    "water": [("w1", "key_life1"), ("life", "key_life1")],
    "fire":  [("f1", "key_fire1"), ("f3", "key_fire1")],
    "earth": [("e2", "key_circuitboard"), ("e2", "key_gear"),
              ("e5", "key_fuse"), ("e7", "key_battery")],
    "death": [("d1", "key_diamondbattery"), ("d2", "key_lens")],
    "town":  [("town", "key_obsidian1"), ("town", "key_obsidian2")],
}

# Barrier fourcc offsets per level — (level_name, offset, barrier_type)
# barrier_type: "firewall" or "iceblock"
BARRIER_OFFSETS = {
    # FireWall element offsets
    "firewall": {
        "a5": 0x01934870, "a6": 0x01395FFC, "w1": 0x025C756C,
        "w2": 0x01D936D4, "f1": 0x0131BFC8, "f4": 0x01398560,
        "e2": 0x04C1DEA8, "e6": 0x01FE2DF0, "e7": 0x00D8D568,
        "town": 0x0383A018,
    },
    # IceBlock element offsets
    "iceblock": {
        "a5": 0x0191F788, "a6": 0x0139C35C, "w1": 0x025C830C,
        "w2": 0x01D8CB6C, "w3": 0x01A277FC, "f6": 0x0120F094,
        "e2": 0x04C1FA2C, "e7": 0x00D8EA30, "d1": 0x01B0F410,
    },
}

# Valid barrier fourccs — each represents an element COMBINATION the player must have active
# Single-element: watr (Water), fire (Earth+Fire), smsh (Earth+Air+Fire), wind (Earth+Air)
# Multi-element:  stem (Fire+Water), acid (Earth+Water), ice (Earth+Air+Water), litn (Air+Fire+Water)
# Using only single-element fourccs keeps barriers accessible early; combo fourccs require more powers
BARRIER_FOURCCS = [b"watr", b"fire", b"smsh", b"wind"]
BARRIER_FOURCCS_HARD = [b"watr", b"fire", b"smsh", b"wind", b"stem", b"acid", b"ice\x00", b"litn"]

# XBE QoL patch offsets
# Gem popup string file offsets (null first byte to disable)
GEM_POPUP_OFFSETS = [0x197858, 0x19783C, 0x197820, 0x197800, 0x1977D8]
# Disable all pickup celebration animations (file 0x0313A2, VA 0x0413A2).
# Forces the pickup handler to always skip the stop-pose-delay animation
# by replacing a conditional JNZ with an unconditional JMP.
PICKUP_ANIM_OFFSET = 0x0313A2
PICKUP_ANIM_ORIGINAL = bytes([0x0F, 0x85, 0xCB, 0x00, 0x00, 0x00])  # JNZ +0xCB
PICKUP_ANIM_PATCH = bytes([0xE9, 0xCC, 0x00, 0x00, 0x00, 0x90])     # JMP +0xCC; NOP

# Player character swap: replace "garret4" with another character model
# At file offset 0x1976C8, "garret4\0d:\" = 12 bytes, can fit any name up to 11 chars
PLAYER_CHAR_OFFSET = 0x1976C8
PLAYER_CHAR_ORIGINAL = bytes([0x67,0x61,0x72,0x72,0x65,0x74,0x34,0x00,
                               0x64,0x3a,0x5c,0x00])  # "garret4\0d:\\0"
PLAYER_CHAR_MAX_LEN = 11  # max chars (12 bytes with null)

# Obsidian lock threshold tables in town.xbr
# Two identical tables of 10 entries (48 bytes each), threshold float at +0
OBSIDIAN_LOCK_TABLE_A = 0x37DBDC4
OBSIDIAN_LOCK_TABLE_B = 0x37DC0E4
OBSIDIAN_LOCK_ENTRY_SIZE = 48
OBSIDIAN_LOCK_COUNT = 10
OBSIDIAN_LOCK_DEFAULTS = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]

# Town barrier item scale: items placed behind obsidian locks that aren't
# native to town get scaled down so they don't protrude through the force field.
# Scale is applied to the 3x3 rotation/scale matrix diagonal at name-56, name-36, name-20.
# Items that are behind obsidian lock barriers in town
TOWN_BARRIER_ITEMS = {
    "power_life", "power_ammo", "power_staff1", "power_staff2",
    "key_obsidian1", "key_obsidian2",
}
TOWN_BARRIER_SCALE = 0.5
# Offsets of scale floats relative to the entity name offset
SCALE_OFFSETS = [-56, -36, -20]


# ---------------------------------------------------------------------------
# Level connection randomization
# ---------------------------------------------------------------------------

# Level paths as they appear in XBR transition data
LEVEL_PATHS = {
    "town": "levels/town",          # 11 chars
    "life": "levels/life",          # 11 chars
    "a1": "levels/air/a1",          # 13 chars
    "a3": "levels/air/a3",          # 13 chars
    "a5": "levels/air/a5",          # 13 chars
    "a6": "levels/air/a6",          # 13 chars
    "f1": "levels/fire/f1",         # 14 chars
    "f2": "levels/fire/f2",         # 14 chars
    "f3": "levels/fire/f3",         # 14 chars
    "f4": "levels/fire/f4",         # 14 chars
    "f6": "levels/fire/f6",         # 14 chars
    "w1": "levels/water/w1",        # 15 chars
    "w2": "levels/water/w2",        # 15 chars
    "w3": "levels/water/w3",        # 15 chars
    "w4": "levels/water/w4",        # 15 chars
    "e2": "levels/earth/e2",        # 15 chars
    "e5": "levels/earth/e5",        # 15 chars
    "e6": "levels/earth/e6",        # 15 chars
    "e7": "levels/earth/e7",        # 15 chars
    "d1": "levels/death/d1",        # 15 chars
    "d2": "levels/death/d2",        # 15 chars
    "airship": "levels/air/airship", # 18 chars
}

# Valid destination levels for randomization (exclude cut levels)
VALID_DEST_LEVELS = set(LEVEL_PATHS.keys()) - {"airship"}  # airship is one-way, special

# Transitions to exclude from randomization
EXCLUDE_TRANSITIONS = {
    ("f1", "f7"),     # cut level
    ("e2", "e2"),     # self-reference (bink movie)
}


def _find_level_transitions(data: bytes, level_name: str) -> list[dict]:
    """Scan a level's XBR data for all levelSwitch transition entries.

    Returns list of dicts with:
      offset: file offset of destination path string
      dest_path: full path string (e.g. "levels/water/w1")
      dest_level: short name (e.g. "w1")
      path_len: length of path string (not including null)
      spot: start spot name in destination level
      spot_offset: file offset of spot string (0 if no spot)
      movie: movie/bink path before the transition (empty if none)
    """
    valid_levels = set(LEVEL_PATHS.keys()) | {"f7"}  # include f7 for detection
    transitions = []
    pos = 0
    search = b"levels/"

    while True:
        pos = data.find(search, pos)
        if pos == -1:
            break

        end = data.find(b"\x00", pos)
        dest_path = data[pos:end].decode("ascii", errors="replace")
        dest_level = dest_path.split("/")[-1]

        # Skip non-level destinations (fx_, etc.)
        if dest_level not in valid_levels:
            pos += 1
            continue

        # Read start spot: next non-null string after path
        spot_start = end + 1
        while spot_start < len(data) and data[spot_start] == 0:
            spot_start += 1
        spot_end = data.find(b"\x00", spot_start)
        spot = ""
        if spot_end > spot_start and spot_end - spot_start < 40:
            candidate = data[spot_start:spot_end]
            if all(32 <= b < 127 for b in candidate):
                spot = candidate.decode("ascii")

        # Check for movie/bink path immediately before
        movie = ""
        pre = pos - 1
        while pre > 0 and data[pre] == 0:
            pre -= 1
        if pre > 0:
            ps = pre
            while ps > 0 and data[ps - 1] != 0:
                ps -= 1
            pre_str = data[ps:pre + 1].decode("ascii", errors="replace")
            if pre_str.startswith("bink:") or pre_str.startswith("movies/"):
                movie = pre_str

        # Skip self-references at file end (index entries, not transitions)
        if dest_level == level_name and not spot and pos > len(data) - 2000:
            pos += 1
            continue

        transitions.append({
            "offset": pos,
            "dest_path": dest_path,
            "dest_level": dest_level,
            "path_len": len(dest_path),
            "spot": spot,
            "spot_offset": spot_start if spot else 0,
            "movie": movie,
            "level": level_name,
        })
        pos += 1

    return transitions


def _power_element(name: str) -> str | None:
    """Extract the element type from a power-up entity name.

    power_water_a3 -> water, power_fire -> fire, power_ammo -> ammo
    """
    if not name.startswith("power_"):
        return None
    rest = name[6:]  # strip "power_"
    for elem in POWER_ELEMENTS:
        if rest == elem or rest.startswith(elem + "_") or rest.startswith(elem) and rest[len(elem):].isdigit():
            return elem
    return None


def _frag_parts(name: str) -> tuple[str, str] | None:
    """Extract (element, number) from a fragment entity name.

    frag_air_1 -> ("air", "1"), frag_earth_3 -> ("earth", "3")
    """
    if not name.startswith("frag_"):
        return None
    rest = name[5:]  # strip "frag_"
    for elem in ["water", "air", "earth", "fire", "life"]:
        if rest.startswith(elem + "_"):
            num = rest[len(elem) + 1:]
            if num.isdigit():
                return (elem, num)
    return None


def _find_cross_level_entities(extract_dir: Path, levels: list[str]):
    """Scan all level files and collect fragment + power-up entities.

    Returns:
        fragments: list of {level, name, name_offset, name_len, element, number, file_path}
        powerups:  list of {level, name, name_offset, name_len, element, file_path}
        level_data: dict of {level_name: bytearray} for modified levels
    """
    fragments = []
    powerups = []
    level_data = {}  # level_name -> bytearray (loaded on demand)

    for level_name in sorted(levels):
        level_file = extract_dir / GAMEDATA_REL / f"{level_name}.xbr"
        if not level_file.exists():
            continue

        data = bytearray(level_file.read_bytes())
        if data[:4] != b"xobx":
            continue

        entities = find_level_entities(data)
        has_targets = False

        for name, info in entities.items():
            parts = _frag_parts(name)
            if parts is not None:
                fragments.append({
                    "level": level_name,
                    "name": name,
                    "name_offset": info["name_offset"],
                    "name_len": info["name_len"],
                    "element": parts[0],
                    "number": parts[1],
                    "file_path": level_file,
                })
                has_targets = True

            elem = _power_element(name)
            if elem is not None:
                powerups.append({
                    "level": level_name,
                    "name": name,
                    "name_offset": info["name_offset"],
                    "name_len": info["name_len"],
                    "element": elem,
                    "file_path": level_file,
                })
                has_targets = True

        if has_targets:
            level_data[level_name] = data

    return fragments, powerups, level_data


def cmd_randomize(args):
    """Randomize gems, fragments, and power-ups across all levels."""
    xdvdfs = require_xdvdfs()
    iso_path = Path(args.iso)
    out_path = Path(args.output)

    if not iso_path.exists():
        print(f"ERROR: ISO not found: {iso_path}")
        sys.exit(1)
    if iso_path.resolve() == out_path.resolve():
        print("ERROR: --output must differ from --iso")
        sys.exit(1)

    seed = args.seed
    levels = args.levels if args.levels else LEVEL_XBRS
    do_gems = not args.no_gems
    do_frags = not args.no_fragments
    do_powers = not args.no_powers

    print(f"Azurik Collectible Randomizer")
    print(f"  Seed: {seed}")
    print(f"  Levels: {len(levels)}")
    print(f"  Randomize: {', '.join(t for t, f in [('gems', do_gems), ('fragments', do_frags), ('powers', do_powers)] if f)}")

    with tempfile.TemporaryDirectory(prefix="azurik_rand_") as tmpdir:
        extract_dir = Path(tmpdir) / "game"

        # Step 1: Extract
        print(f"\n[1/4] Extracting {iso_path.name}...")
        run_xdvdfs(xdvdfs, ["unpack", str(iso_path), str(extract_dir)])

        if not (extract_dir / "default.xbe").exists():
            print("  ERROR: Not a valid game ISO")
            sys.exit(1)

        # Track which level files are modified (need writing back)
        modified_levels = {}  # level_name -> bytearray

        # Step 2: Randomize gems (per-level, same as before)
        master_rng = random.Random(seed)

        if do_gems:
            print(f"\n[2/4] Randomizing gems...")
            total_gems = 0
            total_gem_changes = 0
            gem_levels = 0

            for level_name in sorted(levels):
                level_file = extract_dir / GAMEDATA_REL / f"{level_name}.xbr"
                if not level_file.exists():
                    continue

                if level_name in modified_levels:
                    data = modified_levels[level_name]
                else:
                    data = bytearray(level_file.read_bytes())
                    if data[:4] != b"xobx":
                        continue

                gems = _find_level_gem_entities(data)
                if len(gems) < 2:
                    continue

                level_seed = master_rng.randint(0, 2**31)
                level_rng = random.Random(level_seed)

                base_types = [g["gem_base"] for g in gems]
                before = Counter(base_types)
                level_rng.shuffle(base_types)
                after = Counter(base_types)

                changes = 0
                for g, new_base in zip(gems, base_types):
                    new_name = new_base + g["gem_suffix"]
                    if len(new_name) > NAME_FIELD_SIZE:
                        continue
                    if g["name"] != new_name:
                        changes += 1
                    offset = g["name_offset"]
                    new_bytes = new_name.encode("ascii").ljust(NAME_FIELD_SIZE, b"\x00")
                    data[offset:offset + NAME_FIELD_SIZE] = new_bytes

                modified_levels[level_name] = data
                total_gems += len(gems)
                total_gem_changes += changes
                gem_levels += 1

                dist_str = ", ".join(f"{t}:{before.get(t, 0)}->{after.get(t, 0)}"
                                     for t in GEM_TYPES if before.get(t, 0) or after.get(t, 0))
                print(f"  {level_name}.xbr — {len(gems)} gems, {changes} changed  [{dist_str}]")

            print(f"  Total: {total_gems} gems across {gem_levels} levels, {total_gem_changes} changed")
        else:
            print(f"\n[2/4] Gems — skipped")

        # Step 3: Randomize fragments + power-ups (cross-level)
        if do_frags or do_powers:
            print(f"\n[3/4] Scanning for fragments and power-ups...")
            fragments, powerups, scan_data = _find_cross_level_entities(extract_dir, levels)

            # Merge scan_data into modified_levels (prefer already-modified data)
            for lname, ldata in scan_data.items():
                if lname not in modified_levels:
                    modified_levels[lname] = ldata

            # Use dedicated sub-seeds so fragment/power shuffles are independent
            frag_rng = random.Random(master_rng.randint(0, 2**31))
            power_rng = random.Random(master_rng.randint(0, 2**31))

            if do_frags and fragments:
                print(f"\n  Randomizing {len(fragments)} fragments across {len(set(f['level'] for f in fragments))} levels...")

                # Full permutation: shuffle the exact names across all locations
                frag_names = [f["name"] for f in fragments]
                frag_rng.shuffle(frag_names)

                frag_changes = 0
                for frag, new_name in zip(fragments, frag_names):
                    data = modified_levels[frag["level"]]
                    old_name = frag["name"]

                    if len(new_name) > NAME_FIELD_SIZE:
                        print(f"    WARNING: '{new_name}' exceeds {NAME_FIELD_SIZE} bytes, skipping")
                        continue

                    changed = old_name != new_name
                    if changed:
                        frag_changes += 1
                    marker = "~" if changed else "="

                    print(f"    {marker} {frag['level']:>5}: {old_name:<15} -> {new_name:<15}")

                    offset = frag["name_offset"]
                    new_bytes = new_name.encode("ascii").ljust(NAME_FIELD_SIZE, b"\x00")
                    data[offset:offset + NAME_FIELD_SIZE] = new_bytes

                print(f"  Fragments: {frag_changes}/{len(fragments)} changed")
            elif do_frags:
                print(f"\n  No fragments found in scanned levels")

            if do_powers and powerups:
                print(f"\n  Randomizing {len(powerups)} power-ups across {len(set(p['level'] for p in powerups))} levels...")

                # Shuffle element types with logic solver validation.
                # Retry with new sub-seeds until we find a solvable placement.
                elements = [p["element"] for p in powerups]
                MAX_ATTEMPTS = 100

                try:
                    from solver import Solver
                    solver = Solver()
                    has_solver = True
                except Exception as e:
                    print(f"  WARNING: Logic solver unavailable ({e}), skipping solvability check")
                    has_solver = False

                solved = False
                for attempt in range(MAX_ATTEMPTS):
                    trial_elements = list(elements)
                    power_rng.shuffle(trial_elements)

                    if has_solver:
                        # Build shuffle mapping for solver: (level, orig_power_name, new_power_name)
                        power_mapping = []
                        for pu, new_elem in zip(powerups, trial_elements):
                            # Map to canonical solver names (power_water, not power_water_a3)
                            orig_canonical = f"power_{pu['element']}"
                            new_canonical = f"power_{new_elem}"
                            power_mapping.append((pu["level"], orig_canonical, new_canonical))

                        if solver.check_power_placement(power_mapping):
                            if attempt > 0:
                                print(f"  Found solvable placement after {attempt + 1} attempts")
                            else:
                                print(f"  Placement verified solvable")
                            elements = trial_elements
                            solved = True
                            break
                        else:
                            # Advance the RNG for next attempt
                            power_rng = random.Random(power_rng.randint(0, 2**31))
                    else:
                        elements = trial_elements
                        solved = True
                        break

                if not solved:
                    print(f"  ERROR: Could not find solvable placement in {MAX_ATTEMPTS} attempts")
                    print(f"  Try a different seed, or use --no-powers")
                    sys.exit(1)

                power_changes = 0
                for pu, new_elem in zip(powerups, elements):
                    data = modified_levels[pu["level"]]
                    old_name = pu["name"]
                    new_name = f"power_{new_elem}"

                    if len(new_name) > NAME_FIELD_SIZE:
                        print(f"    WARNING: '{new_name}' exceeds {NAME_FIELD_SIZE} bytes, skipping")
                        continue

                    changed = old_name != new_name
                    if changed:
                        power_changes += 1
                    marker = "~" if changed else "="

                    print(f"    {marker} {pu['level']:>5}: {old_name:<20} -> {new_name:<20}")

                    offset = pu["name_offset"]
                    new_bytes = new_name.encode("ascii").ljust(NAME_FIELD_SIZE, b"\x00")
                    data[offset:offset + NAME_FIELD_SIZE] = new_bytes

                print(f"  Power-ups: {power_changes}/{len(powerups)} changed")
            elif do_powers:
                print(f"\n  No power-ups found in scanned levels")
        else:
            print(f"\n[3/4] Fragments/powers — skipped")

        # Write all modified level files back
        for level_name, data in modified_levels.items():
            level_file = extract_dir / GAMEDATA_REL / f"{level_name}.xbr"
            level_file.write_bytes(data)

        # Step 4: Repack
        print(f"\n[4/4] Building {out_path.name}...")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        run_xdvdfs(xdvdfs, ["pack", str(extract_dir), str(out_path)])

        if out_path.exists():
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"\n  Done! {out_path} ({size_mb:.1f} MB)")
            print(f"  Seed: {seed}  (use same seed to reproduce)")
            print(f"  Load in xemu to play.")
        else:
            print("\n  ERROR: ISO creation failed")
            sys.exit(1)


DIRECT_SEARCH_NAMES = [
    # Powers (some lack 1.0f marker or have non-zero w in coords)
    b"power_water", b"power_water_a3",
    b"power_air", b"power_earth", b"power_fire",
    b"power_staff1", b"power_staff2", b"power_life", b"power_ammo",
    # Fragments
    b"frag_air_1", b"frag_air_2", b"frag_air_3",
    b"frag_water_1", b"frag_water_2", b"frag_water_3",
    b"frag_fire_1", b"frag_fire_2", b"frag_fire_3",
    b"frag_earth_1", b"frag_earth_2", b"frag_earth_3",
    b"frag_life_1", b"frag_life_2", b"frag_life_3",
    b"frag_water_4", b"frag_fire_4", b"frag_earth_4", b"frag_life_4",
    # Keys
    b"key_air1", b"key_air2", b"key_air3",
    b"key_fire1", b"key_fire2", b"key_fire3",
    b"key_water1", b"key_water2",
    b"key_battery", b"key_circuitboard", b"key_diamondbattery",
    b"key_fuse", b"key_gear", b"key_lens", b"key_life1",
    b"key_obsidian1", b"key_obsidian2",
    b"key_blue", b"key_green", b"key_red",
]


def _find_all_entities_in_level(data: bytes, level_name: str):
    """Find all relevant entities in a level, categorized into pools.

    Returns dict with keys: gems, obsidians, fragments, powers, town_powers, keys
    Each value is a list of {name, name_offset, name_len, level, field_size, ...}
    """
    entities = find_level_entities(data)

    # Direct search for entities that may lack the 1.0f marker
    for needle in DIRECT_SEARCH_NAMES:
        name = needle.decode("ascii")
        if name in entities:
            continue
        # Search for the name as a null-terminated string anywhere in the file
        pos = 0
        while True:
            pos = data.find(needle, pos)
            if pos == -1:
                break
            end = pos + len(needle)
            # Must be null-terminated and not mid-string
            if end < len(data) and data[end] == 0:
                if name not in entities:
                    for co in [-96, -116, -144, -140]:
                        cp = pos + co
                        if cp < 0 or cp + 16 > len(data):
                            continue
                        x, y, z, w = struct.unpack_from("<4f", data, cp)
                        if w == 0.0 and all(abs(v) < 50000 for v in [x, y, z] if v == v):
                            entities[name] = {
                                "name_offset": pos,
                                "name_len": len(needle),
                                "coord_offset": cp,
                                "x": x, "y": y, "z": z,
                            }
                            break
            pos += 1

    result = {"gems": [], "obsidians": [], "fragments": [], "powers": [],
              "town_powers": [], "keys": []}

    for name, info in entities.items():
        entry = {
            "name": name,
            "name_offset": info["name_offset"],
            "name_len": info["name_len"],
            "level": level_name,
            "x": info["x"], "y": info["y"], "z": info["z"],
        }
        # Measure actual field size (count null bytes after name)
        end = info["name_offset"] + info["name_len"]
        field_size = info["name_len"]
        while end + (field_size - info["name_len"]) < len(data) and data[end + (field_size - info["name_len"])] == 0:
            field_size += 1
            if field_size >= 32:
                break
        entry["field_size"] = min(field_size, 32)

        # Categorize
        if name.startswith("key_"):
            result["keys"].append(entry)
        elif name.startswith("frag_") and _frag_parts(name) is not None:
            entry["element"], entry["number"] = _frag_parts(name)
            result["fragments"].append(entry)
        elif name.startswith("power_"):
            elem = _power_element(name)
            if elem is not None:
                entry["element"] = elem
                if name in TOWN_POWERS and level_name == "town":
                    result["town_powers"].append(entry)
                else:
                    result["powers"].append(entry)
        elif _gem_base_type(name) is not None:
            base = _gem_base_type(name)
            entry["gem_base"] = base
            entry["gem_suffix"] = name[len(base):]
            if base == "obsidian":
                result["obsidians"].append(entry)
            else:
                result["gems"].append(entry)

    return result


def _write_name(data: bytearray, offset: int, new_name: str, field_size: int):
    """Write a new entity name at offset, null-padded to field_size."""
    new_bytes = new_name.encode("ascii")
    if len(new_bytes) >= field_size:
        new_bytes = new_bytes[:field_size - 1]
    padded = new_bytes + b"\x00" * (field_size - len(new_bytes))
    data[offset:offset + field_size] = padded


def _rename_all_refs(data: bytearray, old_name: str, new_name: str, primary_offset: int):
    """Rename all null-terminated occurrences of old_name in data.

    Some entities have both an inline name and an NDBG debug name.
    Both must be renamed for the engine to use the new type.
    The primary_offset is renamed via _write_name; additional occurrences
    are renamed in-place (must be same length or shorter, null-padded).
    """
    old_bytes = old_name.encode("ascii")
    pos = 0
    while True:
        pos = data.find(old_bytes, pos)
        if pos == -1:
            break
        end = pos + len(old_bytes)
        if end < len(data) and data[end] == 0 and pos != primary_offset:
            # Found an additional reference — rename it in-place
            new_bytes = new_name.encode("ascii")
            if len(new_bytes) <= len(old_bytes):
                data[pos:pos + len(old_bytes)] = new_bytes + b"\x00" * (len(old_bytes) - len(new_bytes))
            # If new name is longer, we can't safely expand — skip
        pos += 1


def cmd_randomize_full(args):
    """Full game randomizer: major items, keys, gems, barriers + QoL patches."""
    xdvdfs = require_xdvdfs()
    iso_path = Path(args.iso)
    out_path = Path(args.output)

    if not iso_path.exists():
        print(f"ERROR: ISO not found: {iso_path}")
        sys.exit(1)
    if iso_path.resolve() == out_path.resolve():
        print("ERROR: --output must differ from --iso")
        sys.exit(1)

    seed = args.seed
    do_major = not args.no_major
    do_keys = not args.no_keys
    do_gems = not args.no_gems
    do_barriers = not args.no_barriers
    hard_barriers = getattr(args, 'hard_barriers', False)
    do_qol = not args.no_qol
    do_connections = not getattr(args, 'no_connections', False)
    force_unsolvable = getattr(args, 'force', False)
    obsidian_cost = getattr(args, 'obsidian_cost', None)

    # Parse custom item pool if provided
    custom_pool = None
    item_pool_arg = getattr(args, 'item_pool', None)
    if item_pool_arg:
        try:
            pool_path = Path(item_pool_arg)
            if pool_path.exists():
                with open(pool_path) as f:
                    custom_pool = json.load(f)
            else:
                custom_pool = json.loads(item_pool_arg)
        except (json.JSONDecodeError, Exception) as e:
            print(f"ERROR: Could not parse --item-pool: {e}")
            sys.exit(1)
        print(f"  Custom item pool: {custom_pool}")

    categories = [t for t, f in [("major items", do_major), ("keys", do_keys),
                                  ("gems", do_gems), ("barriers", do_barriers),
                                  ("connections", do_connections),
                                  ("QoL patches", do_qol)] if f]
    print(f"Azurik Full Randomizer")
    print(f"  Seed: {seed}")
    print(f"  Categories: {', '.join(categories)}")
    if force_unsolvable:
        print(f"  Force: building even if unsolvable")

    with tempfile.TemporaryDirectory(prefix="azurik_full_") as tmpdir:
        extract_dir = Path(tmpdir) / "game"

        # Step 1: Extract
        print(f"\n[1/7] Extracting {iso_path.name}...")
        run_xdvdfs(xdvdfs, ["unpack", str(iso_path), str(extract_dir)])

        if not (extract_dir / "default.xbe").exists():
            print("  ERROR: Not a valid game ISO")
            sys.exit(1)

        # Load all level data
        modified_levels = {}  # level_name -> bytearray
        all_entities = {}     # level_name -> categorized entities

        for level_name in LEVEL_XBRS:
            level_file = extract_dir / GAMEDATA_REL / f"{level_name}.xbr"
            if not level_file.exists():
                continue
            data = bytearray(level_file.read_bytes())
            if data[:4] != b"xobx":
                continue
            modified_levels[level_name] = data
            all_entities[level_name] = _find_all_entities_in_level(data, level_name)

        master_rng = random.Random(seed)

        # Step 2: Major items (fragments + powers + town powers)
        # Uses solver forward-fill to guarantee completability.
        if do_major:
            print(f"\n[2/7] Randomizing major items (forward-fill)...")
            major_rng = random.Random(master_rng.randint(0, 2**31))

            # Collect all major item slots from binary scan
            # NOTE: obsidian spawn points are excluded because their NDBG
            # parent group ("obsidians") uses a different collection handler.
            major_items = []
            for level_name, ents in all_entities.items():
                major_items.extend(ents["fragments"])
                major_items.extend(ents["powers"])
                major_items.extend(ents["town_powers"])

            if len(major_items) >= 2:
                # Check all names fit in all slots
                names = [item["name"] for item in major_items]
                max_name_len = max(len(n) for n in names)
                min_field = min(item["field_size"] for item in major_items)
                if max_name_len >= min_field:
                    print(f"  WARNING: Longest name ({max_name_len}) may not fit smallest field ({min_field})")

                # Build lookup: (level, original_item_name) -> binary entity info
                binary_lookup = {}
                for item in major_items:
                    key = (item["level"], item["name"])
                    binary_lookup[key] = item

                # Use solver forward-fill for completable placement
                try:
                    from solver import Solver
                    solver = Solver()
                    has_solver = True
                except Exception as e:
                    print(f"  WARNING: Logic solver unavailable ({e}), falling back to blind shuffle")
                    has_solver = False

                if has_solver:
                    # Build custom groups if user provided an item pool
                    custom_groups = None
                    if custom_pool:
                        # Filter out gem types — those are handled in step 4
                        custom_items = []
                        for item_name, count in custom_pool.items():
                            if item_name in GEM_TYPES:
                                continue  # gem weights handled separately
                            custom_items.extend([item_name] * int(count))
                        if custom_items:
                            custom_groups = {"progression": custom_items}
                            print(f"  Custom pool: {len(custom_items)} items ({len(set(custom_items))} unique types)")

                    # Try forward-fill with increasing seeds until solvable
                    MAX_ATTEMPTS = 100
                    solved = False
                    last_placement = None
                    for attempt in range(MAX_ATTEMPTS):
                        attempt_rng = random.Random(major_rng.randint(0, 2**31))
                        placement, step_log = solver.forward_fill(
                            rng=attempt_rng,
                            groups=custom_groups,
                        )
                        last_placement = placement

                        # Validate the placement is completable
                        # forward_fill returns index-based placement which
                        # _build_pickup_map handles natively
                        ok, _ = solver.validate_placement(placement)
                        if ok:
                            if attempt > 0:
                                print(f"  Found solvable placement after {attempt + 1} attempts")
                            else:
                                print(f"  Placement verified solvable")
                            solved = True
                            break

                    if not solved:
                        if force_unsolvable:
                            print(f"  WARNING: No solvable placement found in {MAX_ATTEMPTS} attempts")
                            print(f"  --force: building with last attempted placement (NOT completable)")
                            placement = last_placement
                        else:
                            print(f"  ERROR: Could not find solvable placement in {MAX_ATTEMPTS} attempts")
                            print(f"  Try a different seed, use --no-major, or use --force to build anyway")
                            sys.exit(1)

                    # Convert solver placement to binary rename operations
                    # placement = {node_id: {pickup_idx: new_item_name}}
                    rename_ops = []  # list of (binary_item, new_name)
                    for node_id, idx_map in placement.items():
                        node_data = solver.nodes[node_id]
                        level = node_data.get("level", "")
                        vanilla_pickups = node_data.get("pickups", [])
                        for idx, new_item in idx_map.items():
                            if idx < len(vanilla_pickups):
                                orig_item = vanilla_pickups[idx]
                                key = (level, orig_item)
                                if key in binary_lookup:
                                    rename_ops.append((binary_lookup[key], new_item))
                                else:
                                    print(f"    WARNING: {level}/{orig_item} not found in binary scan")

                    # Also handle items NOT in the solver DB (stay in place)
                    solver_items = set()
                    for node_id, node_data in solver.nodes.items():
                        level = node_data.get("level", "")
                        for pickup in node_data.get("pickups", []):
                            solver_items.add((level, pickup))

                    for item in major_items:
                        key = (item["level"], item["name"])
                        if key not in solver_items:
                            rename_ops.append((item, item["name"]))  # no change
                else:
                    # Fallback: blind shuffle (no solver)
                    major_rng.shuffle(names)
                    rename_ops = list(zip(major_items, names))

                changes = 0
                levels_touched = set()
                print(f"  Placing {len(rename_ops)} items across levels:")
                for item, new_name in rename_ops:
                    data = modified_levels[item["level"]]
                    old_name = item["name"]
                    changed = old_name != new_name
                    if changed:
                        changes += 1
                        levels_touched.add(item["level"])
                    marker = "~" if changed else "="
                    print(f"    {marker} {item['level']:>5}: {old_name:<20} -> {new_name:<20}")
                    _write_name(data, item["name_offset"], new_name, item["field_size"])
                    if changed:
                        _rename_all_refs(data, old_name, new_name, item["name_offset"])
                        # Scale down non-native items placed behind town barriers
                        if (item["level"] == "town"
                                and new_name not in TOWN_BARRIER_ITEMS
                                and old_name in TOWN_BARRIER_ITEMS):
                            name_off = item["name_offset"]
                            # Verify the scale offsets contain 1.0f before patching
                            if all(data[name_off + so : name_off + so + 4] == b"\x00\x00\x80\x3f"
                                   for so in SCALE_OFFSETS):
                                for so in SCALE_OFFSETS:
                                    struct.pack_into("<f", data, name_off + so, TOWN_BARRIER_SCALE)
                                print(f"      (scaled to {TOWN_BARRIER_SCALE}x for barrier fit)")

                print(f"  Major items: {changes}/{len(rename_ops)} changed across {len(levels_touched)} levels")
            else:
                print(f"  Only {len(major_items)} major items found, skipping")
        else:
            print(f"\n[2/7] Major items — skipped")

        # Step 3: Keys (within-realm shuffle)
        if do_keys:
            print(f"\n[3/7] Randomizing keys (within realm)...")
            key_rng = random.Random(master_rng.randint(0, 2**31))
            total_key_changes = 0

            for realm, realm_keys in KEY_REALMS.items():
                # Find actual key entities in the loaded data
                realm_items = []
                for level_name, key_name in realm_keys:
                    if level_name not in all_entities:
                        continue
                    for k in all_entities[level_name]["keys"]:
                        if k["name"] == key_name:
                            realm_items.append(k)
                            break

                if len(realm_items) < 2:
                    if realm_items:
                        print(f"  {realm}: 1 key, no shuffle needed")
                    continue

                names = [k["name"] for k in realm_items]
                key_rng.shuffle(names)

                realm_changes = 0
                for item, new_name in zip(realm_items, names):
                    data = modified_levels[item["level"]]
                    old_name = item["name"]
                    changed = old_name != new_name
                    if changed:
                        realm_changes += 1
                    marker = "~" if changed else "="
                    print(f"    {marker} {realm:>6} {item['level']:>5}: {old_name:<20} -> {new_name:<20}")
                    _write_name(data, item["name_offset"], new_name, item["field_size"])

                total_key_changes += realm_changes
                print(f"  {realm}: {realm_changes}/{len(realm_items)} changed")

            print(f"  Total key changes: {total_key_changes}")
        else:
            print(f"\n[3/7] Keys — skipped")

        # Step 4: Gems (per-level shuffle, including obsidians)
        if do_gems:
            print(f"\n[4/7] Randomizing gems...")
            gem_rng = random.Random(master_rng.randint(0, 2**31))
            total_gems = 0
            total_gem_changes = 0

            # Check for custom gem weights in the item pool
            gem_weights = None
            if custom_pool:
                gw = {g: custom_pool[g] for g in GEM_TYPES if g in custom_pool}
                if gw:
                    gem_weights = gw
                    print(f"  Custom gem weights: {gem_weights}")

            for level_name in sorted(all_entities.keys()):
                # Combine regular gems + obsidians into one pool
                gems = all_entities[level_name]["gems"] + all_entities[level_name]["obsidians"]
                if len(gems) < 2:
                    continue

                level_rng = random.Random(gem_rng.randint(0, 2**31))

                if gem_weights:
                    # Weighted random: each gem slot independently drawn
                    # from the custom distribution
                    weight_types = list(gem_weights.keys())
                    weight_vals = list(gem_weights.values())
                    base_types = level_rng.choices(
                        weight_types, weights=weight_vals, k=len(gems))
                else:
                    # Default: shuffle existing types (preserves counts)
                    base_types = [g["gem_base"] for g in gems]
                    level_rng.shuffle(base_types)

                changes = 0
                for g, new_base in zip(gems, base_types):
                    new_name = new_base + g["gem_suffix"]
                    data = modified_levels[level_name]
                    if g["name"] != new_name:
                        changes += 1
                    _write_name(data, g["name_offset"], new_name, g["field_size"])

                total_gems += len(gems)
                total_gem_changes += changes
                print(f"  {level_name}: {len(gems)} gems, {changes} changed")

            print(f"  Total: {total_gems} gems, {total_gem_changes} changed")
        else:
            print(f"\n[4/7] Gems — skipped")

        # Step 5: Barriers (randomize element vulnerability)
        if do_barriers:
            print(f"\n[5/7] Randomizing barriers...")
            barrier_rng = random.Random(master_rng.randint(0, 2**31))
            barrier_changes = 0
            fourcc_pool = BARRIER_FOURCCS_HARD if hard_barriers else BARRIER_FOURCCS

            for barrier_type, level_offsets in BARRIER_OFFSETS.items():
                for level_name, offset in level_offsets.items():
                    if level_name not in modified_levels:
                        continue
                    data = modified_levels[level_name]
                    if offset + 4 > len(data):
                        print(f"    WARNING: {level_name} {barrier_type} offset 0x{offset:X} out of range")
                        continue
                    old_fourcc = bytes(data[offset:offset + 4])
                    new_fourcc = barrier_rng.choice(fourcc_pool)
                    changed = old_fourcc != new_fourcc
                    if changed:
                        barrier_changes += 1
                    data[offset:offset + 4] = new_fourcc
                    marker = "~" if changed else "="
                    print(f"    {marker} {level_name:>5} {barrier_type:>9}: {old_fourcc.decode('ascii', errors='replace'):4} -> {new_fourcc.decode('ascii'):4}")

            print(f"  Barriers: {barrier_changes} changed")
        else:
            print(f"\n[5/7] Barriers — skipped")

        # Step 6: Level connections (randomize exits between levels)
        if do_connections:
            print(f"\n[6/7] Randomizing level connections...")
            conn_rng = random.Random(master_rng.randint(0, 2**31))

            # Scan all loaded levels for transitions
            all_transitions = []
            for level_name, data in modified_levels.items():
                transitions = _find_level_transitions(bytes(data), level_name)
                for t in transitions:
                    pair = (t["level"], t["dest_level"])
                    if pair in EXCLUDE_TRANSITIONS:
                        continue
                    # Only include transitions to valid randomizable levels
                    if t["dest_level"] in VALID_DEST_LEVELS:
                        all_transitions.append(t)

            # Group transitions by path length (can only swap within same length or shorter)
            by_length: dict[int, list[dict]] = {}
            for t in all_transitions:
                by_length.setdefault(t["path_len"], []).append(t)

            print(f"  Found {len(all_transitions)} transitions in {len(by_length)} length groups:")
            for length in sorted(by_length.keys()):
                group = by_length[length]
                dests = [t["dest_level"] for t in group]
                print(f"    {length} chars: {len(group)} exits -> {sorted(set(dests))}")

            # Shuffle destinations within each length group
            conn_changes = 0
            for length, group in by_length.items():
                # Collect the destination paths and shuffle them
                dest_paths = [t["dest_path"] for t in group]
                shuffled = list(dest_paths)
                conn_rng.shuffle(shuffled)

                for t, new_dest_path in zip(group, shuffled):
                    data = modified_levels[t["level"]]
                    old_dest = t["dest_path"]
                    changed = old_dest != new_dest_path

                    if changed:
                        conn_changes += 1

                    # Write the new destination path (null-pad if shorter)
                    new_bytes = new_dest_path.encode("ascii")
                    old_len = len(old_dest)
                    padded = new_bytes + b"\x00" * (old_len - len(new_bytes) + 1)
                    data[t["offset"]:t["offset"] + len(padded)] = padded

                    # Clear the start spot name (set to empty string)
                    # This makes the player spawn at the level's default origin
                    # which is safer than leaving a mismatched spot name
                    if changed and t["spot_offset"]:
                        spot_len = len(t["spot"])
                        data[t["spot_offset"]:t["spot_offset"] + spot_len] = b"\x00" * spot_len

                    new_level = new_dest_path.split("/")[-1]
                    marker = "~" if changed else "="
                    print(f"    {marker} {t['level']:>5} -> {t['dest_level']:>8} now -> {new_level:<8}")

            print(f"  Connections: {conn_changes}/{len(all_transitions)} changed")
        else:
            print(f"\n[6/7] Level connections — skipped")

        # Step 7: QoL XBE patches
        if do_qol:
            print(f"\n[7/7] Applying QoL patches to default.xbe...")
            xbe_path = extract_dir / "default.xbe"
            xbe_data = bytearray(xbe_path.read_bytes())

            # Disable gem first-pickup popups
            for off in GEM_POPUP_OFFSETS:
                if off < len(xbe_data):
                    xbe_data[off] = 0x00
            print(f"  Disabled 5 gem first-pickup popups")

            # Disable all pickup celebration animations
            if PICKUP_ANIM_OFFSET + 6 <= len(xbe_data):
                current = bytes(xbe_data[PICKUP_ANIM_OFFSET:PICKUP_ANIM_OFFSET + 6])
                if current == PICKUP_ANIM_ORIGINAL:
                    xbe_data[PICKUP_ANIM_OFFSET:PICKUP_ANIM_OFFSET + 6] = PICKUP_ANIM_PATCH
                    print(f"  Disabled pickup celebration animation")
                else:
                    print(f"  WARNING: XBE bytes at 0x{PICKUP_ANIM_OFFSET:X} don't match expected "
                          f"(got {current.hex()}, expected {PICKUP_ANIM_ORIGINAL.hex()})")

            # Player character swap (experimental)
            player_char = getattr(args, 'player_character', None)
            if player_char:
                if len(player_char) > PLAYER_CHAR_MAX_LEN:
                    print(f"  WARNING: Player character name '{player_char}' too long "
                          f"(max {PLAYER_CHAR_MAX_LEN} chars), skipping")
                elif PLAYER_CHAR_OFFSET + 12 <= len(xbe_data):
                    current = bytes(xbe_data[PLAYER_CHAR_OFFSET:PLAYER_CHAR_OFFSET + 12])
                    if current == PLAYER_CHAR_ORIGINAL:
                        new_bytes = player_char.encode("ascii") + b"\x00"
                        new_bytes = new_bytes + b"\x00" * (12 - len(new_bytes))
                        xbe_data[PLAYER_CHAR_OFFSET:PLAYER_CHAR_OFFSET + 12] = new_bytes
                        print(f"  Player character: garret4 -> {player_char} (EXPERIMENTAL)")
                    else:
                        print(f"  WARNING: Player char bytes don't match expected, skipping")

            xbe_path.write_bytes(xbe_data)
        else:
            print(f"\n[7/7] QoL patches — skipped")

        # Config.xbr patches (from --config-mod)
        config_mod_arg = getattr(args, 'config_mod', None)
        if config_mod_arg:
            config_xbr = extract_dir / CONFIG_XBR_REL
            if config_xbr.exists():
                print(f"\n  Applying config patches...")
                registry = load_registry()
                # Load the mod JSON (file path or inline JSON)
                try:
                    mod_path = Path(config_mod_arg)
                    if mod_path.exists():
                        mod = load_mod(str(mod_path))
                    else:
                        mod = json.loads(config_mod_arg)
                except Exception as e:
                    print(f"  WARNING: Could not parse --config-mod: {e}")
                    mod = None

                if mod:
                    config_data = bytearray(config_xbr.read_bytes())
                    applied = 0

                    # Variant-record patches (via config_registry)
                    patches = flatten_mod(mod, registry)
                    for p in patches:
                        prop = resolve_prop(registry, p["section"], p["entity"], p["property"])
                        if not prop:
                            continue
                        offset = int(prop["value_file_offset"], 16)
                        tf = prop.get("type_flag", 0)
                        if offset + 8 > len(config_data):
                            continue
                        write_value(config_data, offset, p["value"], tf)
                        applied += 1

                    # Keyed-table patches (direct cell offset doubles)
                    keyed = mod.get("_keyed_patches", {})
                    if keyed:
                        # Import keyed table parser
                        parser_path = SCRIPT_DIR / "claude_output" / "keyed_table_parser.py"
                        if parser_path.exists():
                            import importlib.util
                            spec = importlib.util.spec_from_file_location(
                                "keyed_table_parser", str(parser_path))
                            ktp = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(ktp)
                            tables = ktp.load_all_tables(str(config_xbr))
                            for section_key, entities in keyed.items():
                                if section_key not in tables:
                                    print(f"    WARNING: keyed section '{section_key}' not found")
                                    continue
                                table = tables[section_key]
                                for entity_name, props in entities.items():
                                    for prop_name, value in props.items():
                                        cell = table.get_value(entity_name, prop_name)
                                        if cell and cell[0] == "double":
                                            cell_off = cell[2]
                                            # Write double at cell_offset + 8
                                            struct.pack_into("<d", config_data,
                                                             cell_off + 8, float(value))
                                            applied += 1

                    config_xbr.write_bytes(config_data)
                    print(f"  Config patches: {applied} applied")

        # Obsidian lock thresholds (patched in town.xbr)
        if obsidian_cost is not None and "town" in modified_levels:
            print(f"\n  Patching obsidian lock thresholds (cost={obsidian_cost} per lock)...")
            town_data = modified_levels["town"]
            thresholds = [obsidian_cost * (i + 1) for i in range(OBSIDIAN_LOCK_COUNT)]
            for table_base in [OBSIDIAN_LOCK_TABLE_A, OBSIDIAN_LOCK_TABLE_B]:
                for i, thresh in enumerate(thresholds):
                    off = table_base + i * OBSIDIAN_LOCK_ENTRY_SIZE
                    if off + 4 <= len(town_data):
                        old_val = struct.unpack_from("<f", town_data, off)[0]
                        struct.pack_into("<f", town_data, off, float(thresh))
            print(f"  Thresholds: {thresholds}")

        # Write all modified level files back
        for level_name, data in modified_levels.items():
            level_file = extract_dir / GAMEDATA_REL / f"{level_name}.xbr"
            level_file.write_bytes(data)

        # Repack
        print(f"\nBuilding {out_path.name}...")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        run_xdvdfs(xdvdfs, ["pack", str(extract_dir), str(out_path)])

        if out_path.exists():
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"\n  Done! {out_path} ({size_mb:.1f} MB)")
            print(f"  Seed: {seed}  (use same seed to reproduce)")
        else:
            print("\n  ERROR: ISO creation failed")
            sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Azurik mod tool — patch game values and build xemu-ready ISOs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=
            "Primary workflow:\n"
            "  %(prog)s patch --iso Azurik.iso --mod mod.json -o Azurik_modded.iso\n"
            "\n"
            "Full randomizer (gems + fragments + powers):\n"
            "  %(prog)s randomize --iso Azurik.iso --seed 42 -o Azurik_rand.iso\n"
            "  %(prog)s randomize --iso Azurik.iso --seed 42 --no-gems -o powers_only.iso\n"
            "\n"
            "Gem-only randomizer (legacy):\n"
            "  %(prog)s randomize-gems --iso Azurik.iso --seed 42 -o Azurik_rand.iso\n"
            "\n"
            "Browse values:\n"
            "  %(prog)s list  --sections\n"
            "  %(prog)s list  --entities critters_walking\n"
            "  %(prog)s dump  --iso Azurik.iso -s settings_foo -e air\n"
            "  %(prog)s dump  --input config.xbr -s critters_walking -e garret4\n"
            "  %(prog)s diff  --iso Azurik.iso --mod mod.json\n"
    )

    sub = parser.add_subparsers(dest="command")

    # patch (primary)
    p_patch = sub.add_parser("patch",
        help="Apply mod(s) to a game ISO, producing a patched ISO for xemu")
    p_patch.add_argument("--iso", required=True, help="Original game .iso")
    p_patch.add_argument("--mod", "-m", action="append", required=True,
                         help="Mod JSON file (repeat for multiple mods)")
    p_patch.add_argument("--output", "-o", required=True, help="Output .iso path")

    # list
    p_list = sub.add_parser("list", help="List sections or entities in the registry")
    p_list.add_argument("--sections", action="store_true")
    p_list.add_argument("--entities", metavar="SECTION")

    # dump
    p_dump = sub.add_parser("dump", help="Show current values from a game ISO or config.xbr")
    source = p_dump.add_mutually_exclusive_group(required=True)
    source.add_argument("--iso", help="Read config.xbr from a game ISO")
    source.add_argument("--input", "-i", help="Read a raw config.xbr file directly")
    p_dump.add_argument("--section", "-s", required=True)
    p_dump.add_argument("--entity", "-e")

    # diff
    p_diff = sub.add_parser("diff", help="Preview what a mod would change")
    source2 = p_diff.add_mutually_exclusive_group(required=True)
    source2.add_argument("--iso", help="Read config.xbr from a game ISO")
    source2.add_argument("--input", "-i", help="Read a raw config.xbr file directly")
    p_diff.add_argument("--mod", "-m", action="append", required=True)

    # randomize-gems (legacy, gems only)
    p_rand = sub.add_parser("randomize-gems",
        help="Randomize gem types across all levels and build a new ISO")
    p_rand.add_argument("--iso", required=True, help="Original game .iso")
    p_rand.add_argument("--seed", "-s", type=int, default=42,
                         help="Random seed for reproducibility (default: 42)")
    p_rand.add_argument("--output", "-o", required=True, help="Output .iso path")
    p_rand.add_argument("--levels", "-l", nargs="+", metavar="LEVEL",
                         help="Only randomize these levels (e.g. a3 w2 f1). "
                              "Default: all playable levels")

    # randomize (unified: gems + fragments + powers)
    p_rall = sub.add_parser("randomize",
        help="Randomize gems, fragments, and power-ups across all levels",
        description=(
            "Unified collectible randomizer. Shuffles gem types per-level,\n"
            "fragment names cross-level, and power-up elements cross-level.\n"
            "Use --no-gems, --no-fragments, --no-powers to disable categories."
        ))
    p_rall.add_argument("--iso", required=True, help="Original game .iso")
    p_rall.add_argument("--seed", "-s", type=int, default=42,
                         help="Random seed for reproducibility (default: 42)")
    p_rall.add_argument("--output", "-o", required=True, help="Output .iso path")
    p_rall.add_argument("--levels", "-l", nargs="+", metavar="LEVEL",
                         help="Only process these levels (default: all playable)")
    p_rall.add_argument("--no-gems", action="store_true",
                         help="Skip gem randomization")
    p_rall.add_argument("--no-fragments", action="store_true",
                         help="Skip fragment randomization")
    p_rall.add_argument("--no-powers", action="store_true",
                         help="Skip power-up randomization")

    # randomize-full (full game randomizer)
    p_full = sub.add_parser("randomize-full",
        help="Full game randomizer: major items, keys, gems, barriers + QoL",
        description=(
            "Full game randomizer with 4 shuffle pools:\n"
            "  1. Major items: fragments + powers + town powers + obsidians (cross-level)\n"
            "  2. Keys: shuffled within elemental realm\n"
            "  3. Gems: diamond/emerald/sapphire/ruby shuffled per-level\n"
            "  4. Barriers: element vulnerability randomized per-level\n"
            "\n"
            "Also applies QoL patches: disable gem popups + pickup animations.\n"
            "Use --no-major, --no-keys, --no-gems, --no-barriers, --no-qol to skip."
        ))
    p_full.add_argument("--iso", required=True, help="Original game .iso")
    p_full.add_argument("--seed", "-s", type=int, default=42,
                         help="Random seed for reproducibility (default: 42)")
    p_full.add_argument("--output", "-o", required=True, help="Output .iso path")
    p_full.add_argument("--no-major", action="store_true",
                         help="Skip major item randomization (fragments/powers/obsidians)")
    p_full.add_argument("--no-keys", action="store_true",
                         help="Skip key randomization")
    p_full.add_argument("--no-gems", action="store_true",
                         help="Skip gem randomization")
    p_full.add_argument("--no-barriers", action="store_true",
                         help="Skip barrier randomization")
    p_full.add_argument("--hard-barriers", action="store_true",
                         help="Include multi-element combo fourccs (stem/acid/ice/litn) in barrier pool")
    p_full.add_argument("--no-connections", action="store_true",
                         help="Skip level connection randomization")
    p_full.add_argument("--no-qol", action="store_true",
                         help="Skip QoL patches (gem popups, pickup animations)")
    p_full.add_argument("--obsidian-cost", type=int, metavar="N",
                         help="Obsidian cost per temple lock (default: 10 = locks at 10,20,...100)")
    p_full.add_argument("--item-pool",
                         help='Custom item pool as JSON (inline or file path). '
                              'Format: {"power_water": 5, "frag_air_1": 2, ...}. '
                              'Overrides the default item counts for the solver.')
    p_full.add_argument("--force", action="store_true",
                         help="Build the ISO even if no solvable placement is found")
    p_full.add_argument("--player-character",
                         help="Replace player model (e.g. evil_noreht, overlord, flicken). "
                              "EXPERIMENTAL: animations may break. Max 11 chars.")
    p_full.add_argument("--config-mod",
                         help="Config mod JSON to apply (file path or inline JSON). "
                              "Patches config.xbr values (entity stats, damage, etc.)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    {"list": cmd_list, "dump": cmd_dump, "diff": cmd_diff, "patch": cmd_patch,
     "randomize-gems": cmd_randomize_gems,
     "randomize": cmd_randomize,
     "randomize-full": cmd_randomize_full}[args.command](args)


if __name__ == "__main__":
    main()
