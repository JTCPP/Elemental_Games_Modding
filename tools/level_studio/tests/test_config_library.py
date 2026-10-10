import hashlib
from pathlib import Path
import struct

import pytest

from config_library import CritterLibrary, read_engine_table
from renderer_parser import read_sections


def fixture_config():
    rows = ["name", "body", "scale", "ai", "realm"]
    columns = [["small_health", "large_health", 0.9, "item", None],
               ["town_female_3", "townspeople/female_3", 1.15, "town", "water"]]
    base = 256
    cells = base + 20 + len(rows) * 8
    data = bytearray(cells + len(rows) * len(columns) * 16)
    data[:4] = b"xobx"
    struct.pack_into("<I", data, 4, 4)
    struct.pack_into("<II", data, 12, 1, base)
    resource_name = b"config/critters_engine\0"
    struct.pack_into("<4I", data, 44, 1, 80, 88, len(resource_name))
    struct.pack_into("<II", data, 80, 0, 0)
    data[88:88 + len(resource_name)] = resource_name
    struct.pack_into("<5I", data, base, len(rows), 16, len(columns), len(rows) * len(columns), cells - base - 16)
    def string(field, value):
        target = len(data)
        data.extend(value.encode("ascii") + b"\0")
        struct.pack_into("<i", data, field, target - field)
        return len(value)
    for index, name in enumerate(rows):
        row = base + 20 + index * 8
        length = string(row + 4, name)
        struct.pack_into("<I", data, row, length)
    for column, values in enumerate(columns):
        for row_index, value in enumerate(values):
            cell = cells + (column * len(rows) + row_index) * 16
            if isinstance(value, str):
                length = string(cell + 12, value)
                struct.pack_into("<I", data, cell, 2)
                struct.pack_into("<I", data, cell + 8, length)
            elif value is not None:
                struct.pack_into("<I", data, cell, 1)
                struct.pack_into("<d", data, cell + 8, value)
    size = len(data) - base
    struct.pack_into("<I4sII", data, 64, size, b"tabl", 0, size)
    return data, cells


def install(tmp_path, data):
    gamedata = tmp_path / "gamedata"
    gamedata.mkdir()
    path = gamedata / "config.xbr"
    path.write_bytes(data)
    return path


def test_exact_alias_and_npc_subfolder_are_loaded_from_real_table(tmp_path):
    data, cells = fixture_config()
    path = install(tmp_path, data)
    original = hashlib.sha256(path.read_bytes()).hexdigest()
    library = CritterLibrary(tmp_path)
    pickup = library.resolve("small_health")
    assert pickup["body"] == "large_health"
    assert pickup["scale"] == 0.9
    assert pickup["bodyOffset"] == cells + 16
    assert pickup["scaleOffset"] == cells + 32
    assert pickup["sourceOffset"] == cells
    assert pickup["sourceArchive"] == "config.xbr"
    assert pickup["sourceHash"] == original
    npc = library.resolve("town_female_3")
    assert npc["body"] == "townspeople/female_3"
    assert npc["bodyResource"] == "characters/townspeople/female_3"
    assert npc["ai"] == "town"
    assert npc["scale"] == 1.15
    assert library.resolve("female_3") is None
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original


def test_generator_colon_splits_first_separator_and_preserves_path(tmp_path):
    data, _ = fixture_config()
    install(tmp_path, data)
    mapping = CritterLibrary(tmp_path).resolve("town_female_3:town_path:branch")
    assert mapping["matchedName"] == "town_female_3"
    assert mapping["path"] == "town_path:branch"
    assert mapping["matchKind"] == "path"
    assert mapping["body"] == "townspeople/female_3"
    assert CritterLibrary(tmp_path).resolve("unknown:town_female_3") is None


def test_empty_scale_uses_proven_engine_default_and_catalog_is_isolated(tmp_path):
    data, cells = fixture_config()
    struct.pack_into("<I", data, cells + 32, 0)
    install(tmp_path, data)
    library = CritterLibrary(tmp_path / "gamedata")
    mapping = library.resolve("small_health")
    assert mapping["scale"] == 1.0
    assert mapping["scaleSource"] == "default"
    mapping["body"] = "wrong"
    catalog = library.catalog()
    assert catalog["entryCount"] == 2
    catalog["entries"][0]["body"] = "wrong"
    assert library.resolve("small_health")["body"] == "large_health"
    result = library.coverage(["small_health", "town_female_3:path", "missing"])
    assert result["resolvedCount"] == 2
    assert result["missing"] == ["missing"]


def test_body_resource_uses_proven_ascii_lowercase_without_changing_source_name(tmp_path):
    data, cells = fixture_config()
    field = cells + 16 + 12
    target = field + struct.unpack_from("<i", data, field)[0]
    data[target:target + len("large_health")] = b"LARGE_health"
    install(tmp_path, data)
    mapping = CritterLibrary(tmp_path).resolve("small_health")
    assert mapping["body"] == "LARGE_health"
    assert mapping["bodyResource"] == "characters/large_health"


@pytest.mark.parametrize("damage", ["escaped_string", "escaped_cells", "length", "nan", "unknown_type", "wrong_count"])
def test_table_rejects_unbounded_pointers_and_invalid_values(damage):
    data, cells = fixture_config()
    section = read_sections(data)[0]
    if damage == "escaped_string":
        field = cells + 12
        struct.pack_into("<i", data, field, len(data) - field)
        data.extend(b"small_health\0")
    elif damage == "escaped_cells":
        struct.pack_into("<I", data, 256 + 16, section.size)
    elif damage == "length":
        struct.pack_into("<I", data, cells + 8, 1)
    elif damage == "nan":
        struct.pack_into("<d", data, cells + 40, float("nan"))
    elif damage == "unknown_type":
        struct.pack_into("<I", data, cells, 9)
    elif damage == "wrong_count":
        struct.pack_into("<I", data, 256 + 12, 99)
    with pytest.raises(ValueError):
        read_engine_table(bytes(data), section)


def test_cache_reloads_a_source_change_without_writing_source(tmp_path):
    data, cells = fixture_config()
    path = install(tmp_path, data)
    library = CritterLibrary(tmp_path)
    assert library.resolve("small_health")["scale"] == 0.9
    struct.pack_into("<d", data, cells + 40, 0.8)
    path.write_bytes(data)
    assert library.resolve("small_health")["scale"] == 0.8
    assert path.read_bytes() == bytes(data)


def test_missing_archive_returns_unavailable_and_invalid_queries_fail(tmp_path):
    library = CritterLibrary(tmp_path)
    assert library.resolve("small_health") is None
    assert library.catalog()["available"] is False
    for name in ("", "name\0tail", None, "é"):
        with pytest.raises(ValueError):
            library.resolve(name)
