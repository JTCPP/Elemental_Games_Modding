"""Local import and persistent locks through the browser-facing API."""
import http.client
import json
import threading
from pathlib import Path

import pytest

import editor_backend
from server import StudioServer
from test_editor_backend import FixtureBackend, fixture_data
from test_iso_import import iso_fixture, wait_ready


@pytest.fixture
def studio(tmp_path):
    source = tmp_path / "source/gamedata"
    source.mkdir(parents=True)
    (source / "town.xbr").write_bytes(fixture_data())
    backend = FixtureBackend(source.parent, tmp_path / "project", tmp_path / "exports", texture_dir=tmp_path / "textures")
    server = StudioServer(("127.0.0.1", 0), backend, tmp_path / "imports")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def request(server, method, path, payload=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    if isinstance(payload, dict):
        payload = json.dumps(payload)
        headers = {"Content-Type": "application/json", **(headers or {})}
    conn.request(method, path, payload, headers or {})
    response = conn.getresponse()
    status, body = response.status, json.loads(response.read())
    conn.close()
    return status, body


def test_lock_api_enriches_character_scene_and_blocks_writes_and_reset(studio):
    class Library:
        def enrich(self, scene):
            scene["characterModels"] = [{"name": "fixture"}]
            return scene
    studio.character_library = Library()
    status, result = request(studio, "POST", "/api/lock", {"level": "town", "id": "entity-00000064", "locked": True})
    assert status == 200 and result["scene"]["objects"][0]["locked"]
    assert result["scene"]["characterModels"] == [{"name": "fixture"}]
    before = studio.backend.project_path.read_bytes()
    for path, payload in (("/api/move", {"position": [12, 34, 56]}),
                          ("/api/transform", {"position": [10, 20, 30]})):
        status, result = request(studio, "POST", path, {"level": "town", "id": "entity-00000064", **payload})
        assert status == 400 and "verrouillé" in result["error"]
        assert studio.backend.project_path.read_bytes() == before
    status, _ = request(studio, "POST", "/api/lock", {"level": "town", "all": True, "locked": False})
    assert status == 200
    assert request(studio, "POST", "/api/move", {"level": "town", "id": "entity-00000064", "position": [12, 34, 56]})[0] == 200


def test_import_job_poll_open_and_restore_source_api(studio, tmp_path):
    path = tmp_path / "game.iso"
    iso_fixture(path)
    original_backend = studio.backend
    status, job = request(studio, "POST", "/api/import-iso", {"path": str(path)})
    assert status == 202
    assert wait_ready(studio.imports, job["id"])["status"] == "ready"
    status, polled = request(studio, "GET", "/api/import-iso?id=" + job["id"])
    assert status == 200 and polled["progress"] == 1
    status, result = request(studio, "POST", "/api/open-import", {"id": job["id"]})
    assert status == 200 and result["active"] == job["id"]
    assert studio.backend.source_dir.is_relative_to(studio.imports.directory)
    status, catalog = request(studio, "GET", "/api/catalog")
    assert status == 200 and catalog["levels"][0]["id"] == "town"
    status, result = request(studio, "POST", "/api/open-source", {"id": "original"})
    assert status == 200 and result["active"] == "original" and studio.backend is original_backend


def test_iso_upload_api_streams_binary_and_enforces_size_type_and_filename(studio, tmp_path):
    path = tmp_path / "game.iso"
    iso_fixture(path)
    status, job = request(studio, "POST", "/api/import-iso-upload", path.read_bytes(),
                          {"Content-Type": "application/octet-stream", "X-File-Name": "Azurik%20Disc.iso"})
    assert status == 202 and job["name"] == "Azurik Disc.iso"
    assert wait_ready(studio.imports, job["id"])["status"] == "ready"
    status, _ = request(studio, "POST", "/api/import-iso-upload", b"", {"Content-Type": "application/octet-stream"})
    assert status == 413
    status, _ = request(studio, "POST", "/api/import-iso-upload", b"invalid", {"Content-Type": "application/json"})
    assert status == 403
    status, _ = request(studio, "POST", "/api/import-iso-upload", path.read_bytes(),
                         {"Content-Type": "application/octet-stream", "X-File-Name": "program.exe"})
    assert status == 400


def test_iso_api_rejects_cross_origin_and_unknown_jobs_without_switching(studio, tmp_path):
    path = tmp_path / "game.iso"
    iso_fixture(path)
    original_backend = studio.backend
    status, _ = request(studio, "POST", "/api/import-iso", {"path": str(path)}, {"Origin": "https://evil.example"})
    assert status == 403 and not studio.imports.list()
    assert request(studio, "GET", "/api/import-iso?id=../source")[0] == 400
    assert request(studio, "POST", "/api/open-import", {"id": "a" * 32})[0] == 400
    assert studio.backend is original_backend
    for bad in (None, "", [], 1):
        assert request(studio, "POST", "/api/import-iso", {"path": bad})[0] == 400


@pytest.mark.parametrize("extra", [{"Content-Type": "application/json"}, {"Origin": "https://evil.example"},
                                   {"Host": "evil.example"}, {"Sec-Fetch-Site": "cross-site"}])
def test_rejected_upload_with_body_returns_json_error_without_windows_reset(studio, extra):
    status, result = request(studio, "POST", "/api/import-iso-upload", b"x" * (200 * 1024),
                             {"Content-Type": "application/octet-stream", **extra})
    assert status == 403 and "error" in result
    assert not studio.imports.list()


def test_small_truncated_image_upload_returns_json_size_error(studio):
    status, result = request(studio, "POST", "/api/import-iso-upload", b"x" * 1000,
                             {"Content-Type": "application/octet-stream"})
    assert status == 413 and "error" in result
    assert not studio.imports.list()


def test_portable_defaults_environment_saved_project_and_empty_install(tmp_path, monkeypatch):
    monkeypatch.setattr(editor_backend, "STUDIO_ROOT", tmp_path)
    monkeypatch.delenv("AZURIK_SOURCE", raising=False)
    monkeypatch.delenv("AZURIK_TOOLKIT", raising=False)
    assert editor_backend.default_source() == tmp_path / "source"
    project = tmp_path / "projects/default/project.json"
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps({"sourceDir": str(tmp_path / "existing-source")}), "utf-8")
    assert editor_backend.default_source() == tmp_path / "existing-source"
    monkeypatch.setenv("AZURIK_SOURCE", str(tmp_path / "explicit-source"))
    monkeypatch.setenv("AZURIK_TOOLKIT", str(tmp_path / "explicit-toolkit"))
    assert editor_backend.default_source() == tmp_path / "explicit-source"
    assert editor_backend.default_toolkit() == tmp_path / "explicit-toolkit"
    monkeypatch.delenv("AZURIK_SOURCE")
    project.write_text("[]", "utf-8")
    assert editor_backend.default_source() == tmp_path / "source"
