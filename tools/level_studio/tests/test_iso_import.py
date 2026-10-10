"""Synthetic Xbox disc coverage; no proprietary game files are needed."""
import hashlib
import http.client
import io
import json
import struct
import threading
import time
from pathlib import Path

import pytest

from server import StudioServer
from xbox_iso import XboxISO, ISOImports, MAGIC, SECTOR, MAX_IMAGE_BYTES
from test_editor_backend import fixture_data, FixtureBackend


def iso_fixture(path, partition=0, filename=b"town.xbr", title_id=0x4D530007):
    executable = bytearray(512)
    executable[:4] = b"XBEH"
    struct.pack_into("<I", executable, 0x104, 0x10000)
    struct.pack_into("<I", executable, 0x118, 0x10178)
    struct.pack_into("<I", executable, 0x180, title_id)
    title = "Azurik - Rise of Perathia".encode("utf-16-le")
    executable[0x184:0x184 + len(title)] = title
    disc = bytearray(40 * SECTOR)
    disc[0x10000:0x10000 + 20] = MAGIC
    disc[0x10000 + SECTOR - 20:0x10000 + SECTOR] = MAGIC
    struct.pack_into("<II", disc, 0x10000 + 20, 34, 64)
    # Root tree: default.xbe, then gamedata at the right child.
    struct.pack_into("<HHIIBB", disc, 34 * SECTOR, 0, 8, 36, len(executable), 0, 11)
    disc[34 * SECTOR + 14:34 * SECTOR + 25] = b"default.xbe"
    struct.pack_into("<HHIIBB", disc, 34 * SECTOR + 32, 0, 0, 35, 64, 0x10, 8)
    disc[34 * SECTOR + 46:34 * SECTOR + 54] = b"gamedata"
    struct.pack_into("<HHIIBB", disc, 35 * SECTOR, 0, 0, 37, len(fixture_data()), 0, len(filename))
    disc[35 * SECTOR + 14:35 * SECTOR + 14 + len(filename)] = filename
    disc[36 * SECTOR:36 * SECTOR + len(executable)] = executable
    disc[37 * SECTOR:37 * SECTOR + len(fixture_data())] = fixture_data()
    with Path(path).open("wb") as stream:
        stream.seek(partition)
        stream.write(disc)
    return bytes(executable), fixture_data()


def wait_ready(manager, identifier):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = manager.status(identifier)
        if result["status"] in ("ready", "error"):
            return result
        time.sleep(0.01)
    pytest.fail("ISO import did not complete")


@pytest.mark.parametrize("corrupt", [False, True])
def test_european_empty_localization_placeholder_is_preserved(tmp_path, corrupt):
    path = tmp_path / "game.iso"
    iso_fixture(path)
    data = bytearray(path.read_bytes())
    metadata = [int.from_bytes(b"xobx", "little"), 4, 0, 0, 4096,
                0, 4096, 0, 4096, 0, 4096, 0, 4096, 4096, 0]
    if corrupt:
        metadata[-1] = 1
    placeholder = struct.pack("<15I", *metadata)
    struct.pack_into("<H", data, 35 * SECTOR + 2, 8)
    struct.pack_into("<HHIIBB", data, 35 * SECTOR + 32, 0, 0, 39, len(placeholder), 0, 7)
    data[35 * SECTOR + 46:35 * SECTOR + 53] = b"loc.xbr"
    data[39 * SECTOR:39 * SECTOR + len(placeholder)] = placeholder
    path.write_bytes(data)
    if corrupt:
        with pytest.raises(ValueError, match="tronquée"):
            XboxISO(path)
        return
    image = XboxISO(path)
    image.extract(tmp_path / "extracted")
    assert (tmp_path / "extracted/gamedata/loc.xbr").read_bytes() == placeholder


@pytest.mark.parametrize("partition", [0, 0x18300000, 0x0FD90000, 0x02080000])
def test_xiso_and_known_disc_partition_offsets_extract_exact_bytes(tmp_path, partition):
    path = tmp_path / "game.iso"
    executable, archive = iso_fixture(path, partition)
    image = XboxISO(path)
    assert image.partition == partition
    before = path.stat()
    result = image.extract(tmp_path / "imported")
    assert (tmp_path / "imported/default.xbe").read_bytes() == executable
    assert (tmp_path / "imported/gamedata/town.xbr").read_bytes() == archive
    assert result["scope"] == "default.xbe and gamedata"
    assert result["files"][0]["sha256"] == hashlib.sha256(executable).hexdigest()
    assert path.stat().st_size == before.st_size and path.stat().st_mtime_ns == before.st_mtime_ns
    with pytest.raises(ValueError, match="existe déjà"):
        image.extract(tmp_path / "imported")


@pytest.mark.parametrize("name", [b"../town.xbr", b"..", b"C:town.xbr", b"town.xbr.", b"CON.xbr", b"a\\b.xbr", b"a\0b.xbr"])
def test_malicious_names_rejected_before_any_extraction(tmp_path, name):
    path = tmp_path / "game.iso"
    iso_fixture(path, filename=name)
    with pytest.raises(ValueError, match="nom de fichier"):
        XboxISO(path)
    assert not (tmp_path / "source").exists()


@pytest.mark.parametrize("patch", ["cycle", "out-of-bounds", "tail", "overlap", "directory-cycle", "wrong-game", "bad-xbr"])
def test_truncated_and_cyclic_tables_invalid_extents_and_other_games(tmp_path, patch):
    path = tmp_path / "game.iso"
    iso_fixture(path, title_id=0xDEADBEEF if patch == "wrong-game" else 0x4D530007)
    data = bytearray(path.read_bytes())
    if patch == "cycle":
        struct.pack_into("<H", data, 34 * SECTOR + 32 + 2, 8)
    elif patch == "out-of-bounds":
        struct.pack_into("<I", data, 35 * SECTOR + 4, 999999)
    elif patch == "tail":
        data[0x10000 + SECTOR - 1] = 0
    elif patch == "overlap":
        struct.pack_into("<H", data, 34 * SECTOR + 2, 1)
    elif patch == "directory-cycle":
        struct.pack_into("<I", data, 34 * SECTOR + 32 + 4, 34)
    elif patch == "bad-xbr":
        data[37 * SECTOR:37 * SECTOR + 4] = b"BAD!"
    path.write_bytes(data)
    with pytest.raises(ValueError):
        XboxISO(path)
    with path.open("r+b") as stream:
        stream.truncate(33 * SECTOR)
    with pytest.raises(ValueError):
        XboxISO(path)


def test_case_insensitive_duplicate_entries_rejected(tmp_path):
    path = tmp_path / "game.iso"
    iso_fixture(path)
    data = bytearray(path.read_bytes())
    struct.pack_into("<H", data, 35 * SECTOR + 2, 8)
    struct.pack_into("<HHIIBB", data, 35 * SECTOR + 32, 0, 0, 37, len(fixture_data()), 0, 8)
    data[35 * SECTOR + 46:35 * SECTOR + 54] = b"TOWN.XBR"
    path.write_bytes(data)
    with pytest.raises(ValueError, match="dupliqués"):
        XboxISO(path)


def test_async_job_durable_import_and_separate_source(tmp_path):
    path = tmp_path / "game.iso"
    iso_fixture(path)
    manager = ISOImports(tmp_path / "imports")
    job = manager.start(path)
    result = wait_ready(manager, job["id"])
    assert result["status"] == "ready" and result["progress"] == 1
    assert manager.source(job["id"]) != path.parent
    assert manager.source(job["id"]).is_relative_to(tmp_path / "imports")
    # Durable state is written after the worker completes.
    deadline = time.monotonic() + 5
    manifest = tmp_path / "imports" / job["id"] / "import.json"
    while not manifest.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ISOImports(tmp_path / "imports").status(job["id"])["status"] == "ready"
    for identifier in ("../source", "", "a" * 32):
        with pytest.raises(ValueError):
            manager.source(identifier)


def test_streamed_upload_roundtrip_and_truncated_upload(tmp_path):
    path = tmp_path / "game.iso"
    iso_fixture(path)
    manager = ISOImports(tmp_path / "imports")
    job = manager.upload(io.BytesIO(path.read_bytes()), path.stat().st_size, "local.iso")
    result = wait_ready(manager, job["id"])
    assert result["status"] == "ready"
    assert (manager.source(job["id"]) / "gamedata/town.xbr").read_bytes() == fixture_data()
    with pytest.raises(ValueError, match="incomplet"):
        manager.upload(io.BytesIO(b"short"), path.stat().st_size, "local.iso")
    with pytest.raises(ValueError):
        manager.upload(io.BytesIO(b""), MAX_IMAGE_BYTES + 1, "local.iso")


def test_one_import_at_a_time_and_failed_import_cannot_be_opened(tmp_path, monkeypatch):
    path = tmp_path / "game.iso"
    iso_fixture(path)
    manager = ISOImports(tmp_path / "imports")
    monkeypatch.setattr(manager, "_run", lambda *args: None)
    job = manager.start(path)
    with pytest.raises(ValueError, match="déjà en cours"):
        manager.start(path)
    with pytest.raises(ValueError, match="pas prêt"):
        manager.source(job["id"])


def test_source_switch_has_separate_project_and_restores_original(tmp_path):
    original = tmp_path / "original/gamedata"
    original.mkdir(parents=True)
    (original / "town.xbr").write_bytes(fixture_data())
    backend = FixtureBackend(original.parent, tmp_path / "original-project", tmp_path / "exports",
                             texture_dir=tmp_path / "textures")
    backend.move("town", "entity-00000064", [11, 22, 33])
    project = backend.project_path.read_bytes()
    server = StudioServer(("127.0.0.1", 0), backend, tmp_path / "imports")
    try:
        path = tmp_path / "game.iso"
        iso_fixture(path)
        job = server.imports.start(path)
        assert wait_ready(server.imports, job["id"])["status"] == "ready"
        sentinel = object()
        server.asset_library = server.character_library = sentinel
        server.open_source(job["id"])
        assert server.backend.project_dir != backend.project_dir
        assert server.backend.source_dir != backend.source_dir
        assert server.asset_library is None and server.character_library is None
        assert backend.project_path.read_bytes() == project
        server.open_source("original")
        assert server.backend is backend
        assert backend.get_scene("town")["objects"][0]["position"] == [11, 22, 33]
        assert backend.project_path.read_bytes() == project
    finally:
        server.server_close()


def test_empty_startup_catalog_and_upload_host_origin_checks(tmp_path):
    server = StudioServer(("127.0.0.1", 0), None, tmp_path / "imports")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/api/catalog")
        response = conn.getresponse()
        assert response.status == 200 and json.loads(response.read())["needsImport"]
        conn.close()
        for extra in ({"Origin": "https://evil.example"}, {"Host": "evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request("POST", "/api/import-iso-upload", headers={"Content-Type": "application/octet-stream",
                                                                    "Content-Length": "81920", **extra})
            response = conn.getresponse()
            assert response.status == 403
            response.read()
            conn.close()
        assert not server.imports.list()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
