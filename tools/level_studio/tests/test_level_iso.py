"""Independent synthetic XDVDFS fixtures for the create-new level ISO writer."""
import hashlib
import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

import level_iso
from xbox_iso import XboxISO, SECTOR, MAGIC


def sha(data):
    return hashlib.sha256(data).hexdigest()


def xbr(tag=b"synthetic"):
    data = bytearray(64) + tag
    data[:4] = b"xobx"
    struct.pack_into("<I", data, 4, 4)
    struct.pack_into("<II", data, 12, 0, 64)
    return bytes(data)


def directory(rows):
    # Independent fixture encoding: a simple right-linked tree, not the writer's
    # balanced-tree layout. Constant 64-byte slots are sufficient for fixtures.
    rows = sorted(rows, key=lambda row: row[0].lower())
    data = bytearray(b"\xff" * SECTOR)
    for index, (name, sector, size, flags) in enumerate(rows):
        encoded = name.encode("ascii")
        at = index * 64
        struct.pack_into("<HHIIBB", data, at, 0, (index + 1) * 16 if index + 1 < len(rows) else 0,
                         sector, size, flags, len(encoded))
        data[at + 14:at + 14 + len(encoded)] = encoded
    return data


def fixture_iso(path, *, partition=0, index=True, empty=False, parent_file=False,
                prefetch=None, prefetch_name="prefetch-lists.txt"):
    executable = bytearray(512)
    executable[:4] = b"XBEH"
    struct.pack_into("<I", executable, 0x104, 0x10000)
    struct.pack_into("<I", executable, 0x118, 0x10178)
    struct.pack_into("<I", executable, 0x180, 0x4D530007)
    title = "Azurik - Rise of Perathia".encode("utf-16-le")
    executable[0x184:0x184 + len(title)] = title
    files = {"default.xbe": bytes(executable), "GameData/town.xbr": xbr(b"town-original"),
             "GameData/w1.xbr": xbr(b"water-original"), "intro.bik": b"synthetic unchanged movie"}
    if index:
        files["GameData/Index/index.xbr"] = xbr(b"index-original")
    if parent_file:
        files["GameData/Index"] = b"this is a file, not a directory"
    if prefetch is not None:
        assert len(prefetch) <= SECTOR
        files[prefetch_name] = prefetch
    disc = bytearray(partition + 43 * SECTOR + 17)
    volume = bytearray(SECTOR)
    volume[:20] = volume[-20:] = MAGIC
    struct.pack_into("<II", volume, 20, 34, SECTOR)
    disc[partition + 32 * SECTOR:partition + 33 * SECTOR] = volume
    root = [("default.xbe", 37, len(executable), 0x21), ("GameData", 35, SECTOR, 0x10),
            ("intro.bik", 41, len(files["intro.bik"]), 0x22)]
    if empty:
        root.append(("Empty", 0, 0, 0x10))
    if prefetch is not None:
        root.append((prefetch_name, 42, len(prefetch), 0x21))
    gamedata = [("town.xbr", 38, len(files["GameData/town.xbr"]), 0x21),
                ("w1.xbr", 39, len(files["GameData/w1.xbr"]), 0x20)]
    if index:
        gamedata.append(("Index", 36, SECTOR, 0x10))
    if parent_file:
        gamedata.append(("Index", 40, len(files["GameData/Index"]), 0x20))
    tables = [(34, directory(root)), (35, directory(gamedata))]
    if index:
        tables.append((36, directory([("index.xbr", 40, len(files["GameData/Index/index.xbr"]), 0x20)])))
    for sector, table in tables:
        disc[partition + sector * SECTOR:partition + (sector + 1) * SECTOR] = table
    sectors = {"default.xbe": 37, "GameData/town.xbr": 38, "GameData/w1.xbr": 39,
               "GameData/Index/index.xbr": 40, "GameData/Index": 40, "intro.bik": 41}
    if prefetch is not None:
        sectors[prefetch_name] = 42
    for name, data in files.items():
        at = partition + sectors[name] * SECTOR
        disc[at:at + len(data)] = data
    disc[-17:] = b"preserve-old-tail!"
    path.write_bytes(disc)
    XboxISO(path)  # The existing reader must independently accept the fixture.
    return {"path": path, "bytes": bytes(disc), "files": files, "sectors": sectors, "partition": partition}


def export_plan(root, files=(), removed=(), operations=None):
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for path, data, original in files:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        rows.append({"path": path, "sha256": sha(data),
                     "sourceSha256": sha(original) if original is not None else None})
    plan = {"format": level_iso.PLAN_FORMAT, "version": 1, "gameFiles": rows,
            "removedFiles": [{"path": path, "sourceSha256": sha(original)} for path, original in removed]}
    if operations is not None:
        plan["levelOperations"] = operations
    (root / level_iso.PLAN_FILENAME).write_text(json.dumps(plan), "utf-8")
    return plan


def read_file(path, image, entry):
    with path.open("rb") as stream:
        stream.seek(image.partition + entry["sector"] * SECTOR)
        return stream.read(entry["size"])


def attribute_map(path, image):
    """Independent decoding of only the attribute bytes in reachable records."""
    with path.open("rb") as stream:
        stream.seek(image.partition + 0x10000 + 20)
        sector, size = struct.unpack("<II", stream.read(8))
        pending, result = [("", sector, size)], {}
        while pending:
            prefix, sector, size = pending.pop()
            stream.seek(image.partition + sector * SECTOR)
            table = stream.read(size)
            offsets = [0]
            while offsets:
                at = offsets.pop()
                if table[at:at + 2] == b"\xff\xff":
                    continue
                left, right, start, length, flags, count = struct.unpack_from("<HHIIBB", table, at)
                name = table[at + 14:at + 14 + count].decode("ascii")
                full = prefix + name
                result[full.casefold()] = flags
                if flags & 16 and length:
                    pending.append((full + "/", start, length))
                offsets.extend(child * 4 for child in (left, right) if child)
        return result


@pytest.fixture
def source(tmp_path):
    return fixture_iso(tmp_path / "source.iso")


@pytest.mark.parametrize("partition", [0, 4 * SECTOR])
def test_add_replace_delete_and_index_preserve_all_other_assets_and_prefix(tmp_path, monkeypatch, partition):
    monkeypatch.setattr("xbox_iso.PARTITION_OFFSETS", (0, 4 * SECTOR))
    source = fixture_iso(tmp_path / "source.iso", partition=partition, empty=True)
    export = tmp_path / "export"
    new, town, index = xbr(b"my-new-level"), xbr(b"town-changed"), xbr(b"index-changed")
    export_plan(export, [("gamedata/custom.xbr", new, None),
                         ("gamedata/town.xbr", town, source["files"]["GameData/town.xbr"]),
                         ("gamedata/index/index.xbr", index, source["files"]["GameData/Index/index.xbr"])],
                [("gamedata/w1.xbr", source["files"]["GameData/w1.xbr"])])
    destination, progress = tmp_path / "new.iso", []
    report = level_iso.build_iso(source["path"], export, destination, lambda value, stage: progress.append(value))
    assert source["path"].read_bytes() == source["bytes"]
    actual = XboxISO(destination)
    entries = {e["path"].casefold(): e for e in actual.entries}
    assert "gamedata/w1.xbr" not in entries
    for name, data in {"gamedata/custom.xbr": new, "gamedata/town.xbr": town,
                       "gamedata/index/index.xbr": index}.items():
        assert read_file(destination, actual, entries[name]) == data
        assert actual.partition + entries[name]["sector"] * SECTOR >= len(source["bytes"])
    for name in ("default.xbe", "intro.bik"):
        entry = entries[name]
        assert entry["sector"] == source["sectors"][name]
        assert read_file(destination, actual, entry) == source["files"][name]
    assert entries["empty"]["directory"]
    attributes = attribute_map(destination, actual)
    assert attributes["default.xbe"] == 0x21
    assert attributes["intro.bik"] == 0x22
    assert attributes["gamedata/town.xbr"] == 0x21
    assert attributes["gamedata/custom.xbr"] == 0x20
    data = destination.read_bytes()
    expected = bytearray(source["bytes"])
    descriptor = partition + 0x10000 + 20
    expected[descriptor:descriptor + 8] = data[descriptor:descriptor + 8]
    assert data[:len(expected)] == expected
    assert report["inputSha256"] == sha(source["bytes"])
    assert report["isoSha256"] == sha(data)
    assert report["sourceUnchanged"] and report["executableUnchanged"]
    assert report["unchangedAssetsRetainExtents"] and not report["imageTestedInGame"]
    assert {row["path"].casefold() for row in report["files"]} == {key for key, entry in entries.items() if not entry["directory"]}
    assert progress[-1] == 100 and all(a <= b for a, b in zip(progress, progress[1:]))
    assert (export / "gamedata/custom.xbr").read_bytes() == new


def test_create_missing_index_subdirectory(tmp_path):
    source = fixture_iso(tmp_path / "source.iso", index=False)
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    payload = xbr(b"new-index")
    export_plan(export, [("gamedata/index/index.xbr", payload, None)])
    level_iso.build_iso(source["path"], export, destination)
    actual = XboxISO(destination)
    entries = {e["path"].casefold(): e for e in actual.entries}
    assert entries["gamedata/index"]["directory"]
    assert read_file(destination, actual, entries["gamedata/index/index.xbr"]) == payload


def test_parent_file_conflict_creates_nothing(tmp_path):
    source = fixture_iso(tmp_path / "source.iso", index=False, parent_file=True)
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/index/index.xbr", xbr(), None)])
    with pytest.raises(ValueError, match="dossier"):
        level_iso.build_iso(source["path"], export, destination)
    assert not destination.exists() and source["path"].read_bytes() == source["bytes"]


@pytest.mark.parametrize("size", [7, 1024 * 1024])
def test_descriptor_rewrite_across_copy_chunk_boundaries(source, tmp_path, monkeypatch, size):
    monkeypatch.setattr(level_iso, "CHUNK", size)
    export = tmp_path / "export"
    export_plan(export, [("gamedata/new.xbr", xbr(), None)])
    destination = tmp_path / "new.iso"
    report = level_iso.build_iso(source["path"], export, destination)
    assert report["isoSha256"] == sha(destination.read_bytes())
    assert source["path"].read_bytes() == source["bytes"]


@pytest.mark.parametrize("which", ["replace", "remove"])
def test_source_sha_mismatch_fails_before_output(source, tmp_path, which):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    if which == "replace":
        export_plan(export, [("gamedata/town.xbr", xbr(b"new"), xbr(b"wrong"))])
    else:
        export_plan(export, [], [("gamedata/w1.xbr", xbr(b"wrong"))])
    with pytest.raises(ValueError, match="SHA-256"):
        level_iso.build_iso(source["path"], export, destination)
    assert not destination.exists() and source["path"].read_bytes() == source["bytes"]


@pytest.mark.parametrize("damage", ["empty", "format", "bool_version", "traversal", "backslash", "absolute",
                                    "executable", "non_xbr", "unsupported_subdir", "duplicate", "conflict",
                                    "missing_source_sha", "bad_sha", "addition_exists", "replacement_absent",
                                    "removal_absent", "missing_payload", "changed_payload", "bad_xbr"])
def test_bad_plan_never_creates_output(source, tmp_path, damage):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    plan = export_plan(export, [("gamedata/new.xbr", xbr(), None)])
    if damage == "empty":
        plan["gameFiles"] = []
    elif damage == "format":
        plan["format"] = "other"
    elif damage == "bool_version":
        plan["version"] = True
    elif damage in ("traversal", "backslash", "absolute", "executable", "non_xbr", "unsupported_subdir"):
        plan["gameFiles"][0]["path"] = {"traversal": "gamedata/../town.xbr", "backslash": "gamedata\\new.xbr",
            "absolute": "C:/new.xbr", "executable": "default.xbe", "non_xbr": "gamedata/test.png",
            "unsupported_subdir": "gamedata/other/new.xbr"}[damage]
    elif damage == "duplicate":
        plan["gameFiles"].append(dict(plan["gameFiles"][0], path="GAMEDATA/NEW.XBR"))
    elif damage == "conflict":
        plan["removedFiles"] = [{"path": "gamedata/new.xbr", "sourceSha256": "0" * 64}]
    elif damage == "missing_source_sha":
        del plan["gameFiles"][0]["sourceSha256"]
    elif damage == "bad_sha":
        plan["gameFiles"][0]["sha256"] = "bad"
    elif damage == "addition_exists":
        plan = export_plan(export, [("gamedata/town.xbr", xbr(), None)])
    elif damage == "replacement_absent":
        plan["gameFiles"][0]["sourceSha256"] = "0" * 64
    elif damage == "removal_absent":
        plan["removedFiles"] = [{"path": "gamedata/missing.xbr", "sourceSha256": "0" * 64}]
    elif damage == "missing_payload":
        (export / "gamedata/new.xbr").unlink()
    elif damage == "changed_payload":
        (export / "gamedata/new.xbr").write_bytes(xbr(b"unexpected"))
    elif damage == "bad_xbr":
        body = b"bad!" + xbr()[4:]
        (export / "gamedata/new.xbr").write_bytes(body)
        plan["gameFiles"][0]["sha256"] = sha(body)
    (export / level_iso.PLAN_FILENAME).write_text(json.dumps(plan), "utf-8")
    with pytest.raises((ValueError, OSError)):
        level_iso.build_iso(source["path"], export, destination)
    assert not destination.exists() and source["path"].read_bytes() == source["bytes"]


@pytest.mark.parametrize("raw", ['{"format":"azurik-level-iso","format":"azurik-level-iso"}', '{"version":NaN}', "[]"])
def test_duplicate_json_keys_and_nonfinite_json_rejected(source, tmp_path, raw):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export.mkdir()
    (export / level_iso.PLAN_FILENAME).write_text(raw, "utf-8")
    with pytest.raises(ValueError):
        level_iso.build_iso(source["path"], export, destination)
    assert not destination.exists()


@pytest.mark.parametrize("which", ["input", "existing_output"])
def test_existing_files_are_preserved(source, tmp_path, which):
    export = tmp_path / "export"
    export_plan(export, [("gamedata/new.xbr", xbr(), None)])
    destination = source["path"] if which == "input" else tmp_path / "new.iso"
    if which == "existing_output":
        destination.write_bytes(b"keep this user's file")
    original = destination.read_bytes()
    with pytest.raises(ValueError, match="nouvelle ISO"):
        level_iso.build_iso(source["path"], export, destination)
    assert destination.read_bytes() == original


def test_exclusive_create_race_keeps_external_file(source, tmp_path):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/new.xbr", xbr(), None)])
    sentinel = b"another process created this file"
    def progress(value, stage):
        if value >= 20 and not destination.exists():
            destination.write_bytes(sentinel)
    with pytest.raises(FileExistsError):
        level_iso.build_iso(source["path"], export, destination, progress)
    assert destination.read_bytes() == sentinel
    assert source["path"].read_bytes() == source["bytes"]


def test_callback_failure_removes_only_owned_output(source, tmp_path):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/new.xbr", xbr(), None)])
    def progress(value, stage):
        if value > 25:
            raise RuntimeError("synthetic callback failure")
    with pytest.raises(RuntimeError, match="callback"):
        level_iso.build_iso(source["path"], export, destination, progress)
    assert not destination.exists() and source["path"].read_bytes() == source["bytes"]


def test_low_disk_space_creates_nothing(source, tmp_path, monkeypatch):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/new.xbr", xbr(), None)])
    monkeypatch.setattr(level_iso.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(ValueError, match="disque"):
        level_iso.build_iso(source["path"], export, destination)
    assert not destination.exists()


def test_concurrent_source_change_aborts_without_reverting_external_change(source, tmp_path):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/new.xbr", xbr(), None)])
    changed = bytearray(source["bytes"])
    changed[41 * SECTOR + 2] ^= 1
    done = False
    def progress(value, stage):
        nonlocal done
        if value >= 20 and not done:
            source["path"].write_bytes(changed)
            done = True
    with pytest.raises(ValueError, match="source.*changé"):
        level_iso.build_iso(source["path"], export, destination, progress)
    assert done and not destination.exists()
    assert source["path"].read_bytes() == changed


def test_concurrent_export_change_removes_only_owned_new_iso(source, tmp_path):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/new.xbr", xbr(b"first"), None)])
    done = False
    def progress(value, stage):
        nonlocal done
        if value >= 60 and not done:
            (export / "gamedata/new.xbr").write_bytes(xbr(b"later"))
            done = True
    with pytest.raises(ValueError, match="exporté.*changé"):
        level_iso.build_iso(source["path"], export, destination, progress)
    assert done and not destination.exists()
    assert source["path"].read_bytes() == source["bytes"]


def test_output_replaced_by_other_process_is_not_deleted(source, tmp_path):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/new.xbr", xbr(), None)])
    sentinel, done = b"foreign file created during readback", False
    def progress(value, stage):
        nonlocal done
        if value >= 65 and not done:
            destination.unlink()
            destination.write_bytes(sentinel)
            done = True
    with pytest.raises(ValueError):
        level_iso.build_iso(source["path"], export, destination, progress)
    assert done and destination.read_bytes() == sentinel
    assert source["path"].read_bytes() == source["bytes"]


@pytest.mark.parametrize("damage", ["oversized_plan", "too_many_files", "bad_count", "bad_payload_offset"])
def test_plan_and_xbr_resource_bounds_before_create(source, tmp_path, monkeypatch, damage):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    plan = export_plan(export, [("gamedata/new.xbr", xbr(), None)])
    if damage == "oversized_plan":
        monkeypatch.setattr(level_iso, "MAX_PLAN_BYTES", 8)
    elif damage == "too_many_files":
        monkeypatch.setattr(level_iso, "MAX_PLAN_FILES", 0)
    else:
        data = bytearray(xbr())
        struct.pack_into("<I", data, 12 if damage == "bad_count" else 16, 10000000)
        (export / "gamedata/new.xbr").write_bytes(data)
        plan["gameFiles"][0]["sha256"] = sha(data)
        (export / level_iso.PLAN_FILENAME).write_text(json.dumps(plan), "utf-8")
    with pytest.raises(ValueError):
        level_iso.build_iso(source["path"], export, destination)
    assert not destination.exists()


@pytest.mark.parametrize("damage", ["old_asset", "removed_payload", "new_payload", "directory_padding", "extra_tail"])
def test_full_readback_detects_corruption_and_removes_owned_output(source, tmp_path, monkeypatch, damage):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    payload = xbr(b"new-payload")
    export_plan(export, [("gamedata/new.xbr", payload, None)],
                [("gamedata/w1.xbr", source["files"]["GameData/w1.xbr"])])
    reader = level_iso.XboxISO
    def corrupt_then_read(path):
        if Path(path) == destination:
            data = bytearray(destination.read_bytes())
            appended = level_iso._align(len(source["bytes"]))
            at = {"old_asset": 41 * SECTOR + 3, "removed_payload": 39 * SECTOR + 65,
                  "new_payload": appended + 65,
                  "directory_padding": level_iso._align(appended + len(payload)) + SECTOR - 1}.get(damage)
            if at is None:
                data.append(1)
            else:
                data[at] ^= 1
            destination.write_bytes(data)
        return reader(path)
    monkeypatch.setattr(level_iso, "XboxISO", corrupt_then_read)
    with pytest.raises(ValueError, match="relecture|taille|diffère|remplissage|vérification"):
        level_iso.build_iso(source["path"], export, destination)
    assert not destination.exists() and source["path"].read_bytes() == source["bytes"]


def test_export_symlink_rejected_before_create(source, tmp_path):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/new.xbr", xbr(), None)])
    external = tmp_path / "outside.xbr"
    external.write_bytes(xbr())
    target = export / "gamedata/new.xbr"
    target.unlink()
    try:
        target.symlink_to(external)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links")
    with pytest.raises(ValueError, match="jonction|lien"):
        level_iso.build_iso(source["path"], export, destination)
    assert not destination.exists() and external.read_bytes() == xbr()


def test_windows_reparse_attribute_is_rejected(tmp_path, monkeypatch):
    target = tmp_path / "payload.xbr"
    target.write_bytes(xbr())
    info = target.lstat()
    monkeypatch.setattr(Path, "lstat", lambda self: SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400))
    with pytest.raises(ValueError, match="jonction|lien"):
        level_iso._reject_reparse(target)


def test_directory_tree_sector_boundaries_and_sorted_binary_lookup(source, tmp_path):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    additions = [("gamedata/" + str(i).zfill(3) + "x" * 240 + ".xbr", xbr(str(i).encode()), None)
                 for i in range(35)]
    export_plan(export, additions)
    level_iso.build_iso(source["path"], export, destination)
    actual = XboxISO(destination)
    entry = next(e for e in actual.entries if e["path"].casefold() == "gamedata")
    table = read_file(destination, actual, entry)
    def visit(at, lo=None, hi=None):
        left, right, sector, size, flags, count = struct.unpack_from("<HHIIBB", table, at)
        name = table[at + 14:at + 14 + count].lower()
        assert at % 4 == 0 and at % SECTOR + 14 + count <= SECTOR
        assert (lo is None or name > lo) and (hi is None or name < hi)
        return 1 + (visit(left * 4, lo, name) if left else 0) + (visit(right * 4, name, hi) if right else 0)
    assert visit(0) == 38  # 35 new levels, town, W1 and the Index directory.


def test_16_bit_directory_link_overflow_is_rejected():
    children = [{"path": str(i).zfill(4) + "x" * 247 + ".xbr", "sector": 100, "size": 64, "flags": 32}
                for i in range(1024)]
    with pytest.raises(ValueError, match="liens XDVDFS"):
        level_iso._directory_table(children)


@pytest.mark.parametrize("damage", ["bad_magic", "bad_link", "bad_extent", "truncated"])
def test_malformed_input_creates_nothing(source, tmp_path, damage):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/new.xbr", xbr(), None)])
    data = bytearray(source["bytes"])
    if damage == "bad_magic":
        data[0x10000] ^= 1
    elif damage == "bad_link":
        struct.pack_into("<H", data, 34 * SECTOR + 2, 0xFFFF)
    elif damage == "bad_extent":
        struct.pack_into("<I", data, 34 * SECTOR + 4, 99999)
    else:
        data = data[:100]
    source["path"].write_bytes(data)
    with pytest.raises(ValueError):
        level_iso.build_iso(source["path"], export, destination)
    assert not destination.exists() and source["path"].read_bytes() == data


PREFETCH = (b"tag=always\r\nfile=%LANGUAGE%.xbr\r\n\r\n"
            b"tag=w1\r\nfile=w1.xbr\r\nfile=shared.xbr\r\nneighbor=town\r\n\r\n"
            b"tag=town\r\nfile=town.xbr\r\nfile=gamedata/w1.xbr\r\nneighbor=w1\r\n")


@pytest.mark.parametrize("partition", [0, 4 * SECTOR])
def test_level_operations_derive_prefetch_from_input_without_staging(tmp_path, monkeypatch, partition):
    from level_prefetch import read_prefetch
    monkeypatch.setattr("xbox_iso.PARTITION_OFFSETS", (0, 4 * SECTOR))
    source = fixture_iso(tmp_path / "source.iso", partition=partition,
                         prefetch=PREFETCH, prefetch_name="Prefetch-Lists.TXT")
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    operations = {"created": [{"id": "custom", "template": "w1"}],
                  "deleted": [{"id": "w1", "replacement": "custom"}]}
    export_plan(export, [("gamedata/custom.xbr", xbr(b"custom"), None),
                         ("gamedata/index/index.xbr", xbr(b"index-new"), source["files"]["GameData/Index/index.xbr"])],
                [("gamedata/w1.xbr", source["files"]["GameData/w1.xbr"])], operations)
    # The derived text must come from the ISO, never an unlisted export file.
    (export / "prefetch-lists.txt").write_bytes(b"untrusted text that must not be read")
    before = {str(p.relative_to(export)): p.read_bytes() for p in export.rglob("*") if p.is_file()}
    report = level_iso.build_iso(source["path"], export, destination)
    actual = XboxISO(destination)
    entries = {e["path"].casefold(): e for e in actual.entries}
    entry = entries["prefetch-lists.txt"]
    result = read_file(destination, actual, entry)
    tags = {row["tag"]: row for row in read_prefetch(result)}
    assert tags["custom"]["files"] == ["custom.xbr", "shared.xbr"]
    assert tags["w1"]["files"] == ["custom.xbr", "shared.xbr"]
    assert tags["town"]["files"] == ["town.xbr", "gamedata/custom.xbr"]
    assert tags["w1"]["neighbors"] == ["town"] and tags["town"]["neighbors"] == ["w1"]
    assert entry["path"] == "Prefetch-Lists.TXT"
    assert attribute_map(destination, actual)["prefetch-lists.txt"] == 0x21
    assert actual.partition + entry["sector"] * SECTOR >= len(source["bytes"])
    changed = next(row for row in report["changedFiles"] if row["path"].casefold() == "prefetch-lists.txt")
    assert changed == {"path": "Prefetch-Lists.TXT", "sourceSha256": sha(PREFETCH), "sha256": sha(result)}
    expected = bytearray(source["bytes"])
    descriptor = partition + 0x10000 + 20
    expected[descriptor:descriptor + 8] = destination.read_bytes()[descriptor:descriptor + 8]
    assert destination.read_bytes()[:len(expected)] == expected
    assert source["path"].read_bytes() == source["bytes"]
    assert {str(p.relative_to(export)): p.read_bytes() for p in export.rglob("*") if p.is_file()} == before


def test_empty_level_operations_retain_prefetch_extent_and_bytes(tmp_path):
    source = fixture_iso(tmp_path / "source.iso", prefetch=PREFETCH)
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/new.xbr", xbr(), None)], operations={"created": [], "deleted": []})
    report = level_iso.build_iso(source["path"], export, destination)
    actual = XboxISO(destination)
    entry = next(e for e in actual.entries if e["path"] == "prefetch-lists.txt")
    assert entry["sector"] == 42 and read_file(destination, actual, entry) == PREFETCH
    assert all(row["path"] != "prefetch-lists.txt" for row in report["changedFiles"])


def test_deleted_level_archive_may_remain_for_shared_resources(tmp_path):
    from level_prefetch import read_prefetch
    source = fixture_iso(tmp_path / "source.iso", prefetch=PREFETCH)
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/town.xbr", xbr(b"aliases"), source["files"]["GameData/town.xbr"])],
                operations={"created": [], "deleted": [{"id": "w1", "replacement": "town"}]})
    level_iso.build_iso(source["path"], export, destination)
    actual = XboxISO(destination)
    entries = {e["path"].casefold(): e for e in actual.entries}
    assert entries["gamedata/w1.xbr"]["sector"] == 39
    tags = {row["tag"]: row for row in read_prefetch(read_file(destination, actual, entries["prefetch-lists.txt"]))}
    assert tags["w1"]["files"] == ["town.xbr", "shared.xbr"]


def test_missing_template_prefetch_tag_gets_optional_direct_tag(tmp_path):
    from level_prefetch import read_prefetch
    source = fixture_iso(tmp_path / "source.iso", prefetch=b"tag=w1\r\nfile=w1.xbr\r\n")
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/custom.xbr", xbr(), None)],
                operations={"created": [{"id": "custom", "template": "town"}], "deleted": []})
    level_iso.build_iso(source["path"], export, destination)
    actual = XboxISO(destination)
    entry = next(e for e in actual.entries if e["path"] == "prefetch-lists.txt")
    assert read_prefetch(read_file(destination, actual, entry))[-1] == {"tag": "custom", "files": ["custom.xbr"], "neighbors": []}


def test_deleted_unexported_custom_level_with_no_references_needs_no_new_prefetch(tmp_path):
    source = fixture_iso(tmp_path / "source.iso", prefetch=PREFETCH)
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/town.xbr", xbr(b"alias"), source["files"]["GameData/town.xbr"])],
                operations={"created": [], "deleted": [{"id": "discarded", "replacement": "town"}]})
    report = level_iso.build_iso(source["path"], export, destination)
    assert all(row["path"] != "prefetch-lists.txt" for row in report["changedFiles"])


@pytest.mark.parametrize("damage", ["missing_text", "invalid_text", "oversized_text", "missing_template",
                                    "missing_addition", "existing_addition", "missing_replacement"])
def test_invalid_derived_prefetch_creates_nothing(tmp_path, monkeypatch, damage):
    import level_prefetch
    source = fixture_iso(tmp_path / "source.iso", prefetch=None if damage == "missing_text" else
                         b"unexpected malformed native text" if damage == "invalid_text" else PREFETCH)
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    operations = {"created": [{"id": "custom", "template": "w1"}], "deleted": []}
    files = [("gamedata/custom.xbr", xbr(), None)]
    if damage == "oversized_text":
        monkeypatch.setattr(level_prefetch, "MAX_PREFETCH_BYTES", 8)
    elif damage == "missing_template":
        operations["created"][0]["template"] = "missing"
    elif damage == "missing_addition":
        operations["created"][0]["id"] = "missing"
    elif damage == "existing_addition":
        operations["created"][0]["id"] = "town"
        files = [("gamedata/town.xbr", xbr(), source["files"]["GameData/town.xbr"])]
    elif damage == "missing_replacement":
        operations = {"created": [], "deleted": [{"id": "w1", "replacement": "missing"}]}
    export_plan(export, files, operations=operations)
    with pytest.raises(ValueError):
        level_iso.build_iso(source["path"], export, destination)
    assert not destination.exists() and source["path"].read_bytes() == source["bytes"]


@pytest.mark.parametrize("operations", [None, [], {"created": []}, {"created": [], "deleted": [], "extra": []},
    {"created": "invalid", "deleted": []}, {"created": [{"id": "custom"}], "deleted": []},
    {"created": [{"id": "custom", "template": "w1", "extra": True}], "deleted": []},
    {"created": [{"id": "custom", "template": "w1"}, {"id": "CUSTOM", "template": "town"}], "deleted": []},
    {"created": [{"id": "custom", "template": "w1"}], "deleted": [{"id": "custom", "replacement": "town"}]},
    {"created": [], "deleted": [{"id": "w1", "replacement": "W1"}]},
    {"created": [], "deleted": [{"id": "w1", "replacement": "town"}, {"id": "town", "replacement": "w1"}]},
    {"created": [{"id": "x" * 64, "template": "w1"}], "deleted": []},
    {"created": [{"id": "../custom", "template": "w1"}], "deleted": []},
    {"created": [{"id": "custom", "template": "w1.xbr"}], "deleted": []},
    {"created": [{"id": "CON", "template": "w1"}], "deleted": []},
    {"created": [{"id": "default", "template": "w1"}], "deleted": []}])
def test_malformed_level_operations_create_nothing(source, tmp_path, operations):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    plan = export_plan(export, [("gamedata/custom.xbr", xbr(), None)])
    plan["levelOperations"] = operations
    (export / level_iso.PLAN_FILENAME).write_text(json.dumps(plan), "utf-8")
    with pytest.raises(ValueError):
        level_iso.build_iso(source["path"], export, destination)
    assert not destination.exists() and source["path"].read_bytes() == source["bytes"]


@pytest.mark.parametrize("which", ["unknown_top", "untrusted_root_text", "too_many_operations"])
def test_prefetch_plan_whitelist_and_operation_bounds(source, tmp_path, monkeypatch, which):
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    plan = export_plan(export, [("gamedata/custom.xbr", xbr(), None)])
    if which == "unknown_top":
        plan["payloads"] = {"default.xbe": "anything"}
    elif which == "untrusted_root_text":
        plan["gameFiles"][0]["path"] = "prefetch-lists.txt"
    else:
        monkeypatch.setattr(level_iso, "MAX_PLAN_FILES", 1)
        plan["levelOperations"] = {"created": [{"id": "custom", "template": "w1"}],
                                   "deleted": [{"id": "town", "replacement": "custom"}]}
    (export / level_iso.PLAN_FILENAME).write_text(json.dumps(plan), "utf-8")
    with pytest.raises(ValueError):
        level_iso.build_iso(source["path"], export, destination)
    assert not destination.exists()


def test_prefetch_replacement_chain_resolves_to_a_retained_archive(tmp_path):
    from level_prefetch import read_prefetch
    source = fixture_iso(tmp_path / "source.iso", prefetch=PREFETCH)
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/custom.xbr", xbr(), None)],
                [("gamedata/w1.xbr", source["files"]["GameData/w1.xbr"])],
                {"created": [{"id": "custom", "template": "w1"}],
                 "deleted": [{"id": "w1", "replacement": "town"}, {"id": "town", "replacement": "custom"}]})
    level_iso.build_iso(source["path"], export, destination)
    actual = XboxISO(destination)
    entry = next(e for e in actual.entries if e["path"] == "prefetch-lists.txt")
    tags = read_prefetch(read_file(destination, actual, entry))
    assert all(not file.lower().endswith(("w1.xbr", "town.xbr")) for tag in tags for file in tag["files"])


@pytest.mark.parametrize("bad_result", [b"", "not bytes", b"x" * (1024 * 1024 + 1)],
                         ids=["empty", "wrong_type", "oversized"])
def test_unexpected_prefetch_helper_output_is_bounded(tmp_path, monkeypatch, bad_result):
    import level_prefetch
    source = fixture_iso(tmp_path / "source.iso", prefetch=PREFETCH)
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/custom.xbr", xbr(), None)],
                operations={"created": [{"id": "custom", "template": "w1"}], "deleted": []})
    monkeypatch.setattr(level_prefetch, "update_prefetch", lambda *args, **kwargs: bad_result)
    with pytest.raises(ValueError):
        level_iso.build_iso(source["path"], export, destination)
    assert not destination.exists()


def test_derived_prefetch_readback_corruption_removes_only_owned_output(tmp_path, monkeypatch):
    source = fixture_iso(tmp_path / "source.iso", prefetch=PREFETCH)
    export, destination = tmp_path / "export", tmp_path / "new.iso"
    export_plan(export, [("gamedata/custom.xbr", xbr(), None)],
                operations={"created": [{"id": "custom", "template": "w1"}], "deleted": []})
    reader = level_iso.XboxISO
    def corrupt_then_read(path):
        if Path(path) == destination:
            actual = reader(destination)
            entry = next(e for e in actual.entries if e["path"] == "prefetch-lists.txt")
            with destination.open("r+b") as stream:
                stream.seek(actual._extent(entry["sector"], entry["size"]))
                stream.write(b"X")
        return reader(path)
    monkeypatch.setattr(level_iso, "XboxISO", corrupt_then_read)
    with pytest.raises(ValueError, match="relecture"):
        level_iso.build_iso(source["path"], export, destination)
    assert not destination.exists() and source["path"].read_bytes() == source["bytes"]
