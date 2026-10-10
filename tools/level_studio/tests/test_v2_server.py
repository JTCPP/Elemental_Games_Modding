"""V2 API uploads and static caching exercised through actual loopback HTTP."""
import json
import base64
from array import array
import math
import threading
from pathlib import Path

import pytest

import server
from test_server import request, studio
from test_project_assets import studio as asset_backend, image_data, model


@pytest.mark.parametrize("route,method,body,args", [
    ("reset-level", "reset_level", {"level": "town"}, ("town",)),
    ("duplicate", "duplicate", {"level": "town", "id": "mesh-1", "offset": [1, 2, 3]}, ("town", "mesh-1", [1, 2, 3])),
    ("delete-asset", "delete_asset", {"level": "town", "id": "import-1"}, ("town", "import-1")),
    ("import-model", "import_model", {"level": "town", "name": "rock", "model": {"positions": []}, "position": [4, 5, 6]},
     ("town", "rock", {"positions": []}, [4, 5, 6])),
    ("replace-model", "replace_model", {"level": "town", "id": "mesh-1", "name": "rock", "model": {"indices": []}},
     ("town", "mesh-1", "rock", {"indices": []})),
    ("import-texture", "import_texture", {"level": "town", "name": "rock.png", "image": "data:image/png;base64,"},
     ("town", "rock.png", "data:image/png;base64,")),
    ("replace-texture", "replace_texture", {"level": "town", "id": "surface-1", "name": "rock.png", "image": "data:image/png;base64,"},
     ("town", "surface-1", "rock.png", "data:image/png;base64,")),
])
def test_v2_routes_dispatch_exact_contract_and_enrich_scene(studio, route, method, body, args):
    received = []
    def mutation(*values):
        received.append(values)
        return {"scene": {"level": "town", "meshes": [], "objects": [], "capabilities": {"warnings": ["preview"]}},
                "id": "new-item", "gameExportable": False, "canUndo": True}
    setattr(studio.backend, method, mutation)
    class Library:
        def enrich(self, scene):
            scene["characterLibrary"] = {"available": False}
            return scene
    studio.character_library = Library()
    status, _, raw = request(studio, "POST", "/api/" + route, body, {"Content-Type": "application/json"})
    result = json.loads(raw)
    assert status == 200 and received == [args]
    assert result["gameExportable"] is False and result["canUndo"]
    assert result["scene"]["warnings"] == ["preview"]
    assert result["scene"]["characterLibrary"]["available"] is False


def test_asset_uploads_allow_large_models_without_relaxing_other_routes(studio):
    seen = []
    studio.backend.import_model = lambda *args: seen.append(args) or {"scene": {"level": "town", "meshes": [], "objects": []}}
    body = {"level": "town", "name": "mesh", "model": {"positions": [0] * 50_000}}
    assert len(json.dumps(body)) > server.MAX_REQUEST
    status, _, _ = request(studio, "POST", "/api/import-model", body, {"Content-Type": "application/json"})
    assert status == 200 and len(seen) == 1
    assert request(studio, "POST", "/api/duplicate", body, {"Content-Type": "application/json"})[0] == 413


@pytest.mark.parametrize("route", sorted(server.ASSET_UPLOAD_ROUTES))
def test_oversized_asset_uploads_rejected_before_dispatch(studio, route):
    status, _, raw = request(studio, "POST", route, headers={"Content-Type": "application/json",
                            "Content-Length": str(server.MAX_ASSET_REQUEST + 1)})
    assert status == 413 and "error" in json.loads(raw)


@pytest.mark.parametrize("headers", [{"Origin": "https://example.com"}, {"Host": "evil.example"},
                                    {"Sec-Fetch-Site": "cross-site"}, {"Content-Type": "text/plain"}])
def test_asset_uploads_keep_loopback_origin_and_type_guards(studio, headers):
    studio.backend.import_model = lambda *args: pytest.fail("Unauthorized request mutated the project")
    status, _, _ = request(studio, "POST", "/api/import-model", {"level": "town"},
                           {"Content-Type": "application/json", **headers})
    assert status == 403


def test_nonfinite_json_never_reaches_backend(studio):
    studio.backend.import_model = lambda *args: pytest.fail("Invalid JSON reached the backend")
    status, _, raw = request(studio, "POST", "/api/import-model", {"level": "town", "model": {"positions": [float("nan")]}},
                             {"Content-Type": "application/json"})
    assert status == 400 and "finis" in json.loads(raw)["error"]


def test_static_validators_and_file_changes_preserve_persistent_http(studio, tmp_path, monkeypatch):
    web = tmp_path / "web"
    web.mkdir()
    path = web / "module.js"
    path.write_text("export const value = 1;", encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", tmp_path)
    status, headers, raw = request(studio, "GET", "/module.js", headers={"Accept-Encoding": "gzip"})
    assert status == 200 and raw
    original_entry = studio.static_cache[path.resolve()]
    status, repeated_headers, raw = request(studio, "GET", "/module.js",
         headers={"Accept-Encoding": "gzip", "If-None-Match": headers["ETag"]})
    assert status == 304 and raw == b""
    assert studio.static_cache[path.resolve()] is original_entry
    assert repeated_headers["ETag"] == headers["ETag"]
    path.write_text("export const value = 2000;", encoding="utf-8")
    status, changed, raw = request(studio, "GET", "/module.js",
         headers={"Accept-Encoding": "gzip", "If-None-Match": headers["ETag"]})
    assert status == 200 and raw and changed["ETag"] != headers["ETag"]
    status, plain, raw = request(studio, "GET", "/module.js", headers={"If-None-Match": changed["ETag"]})
    assert status == 200 and raw == path.read_bytes()
    assert plain["ETag"] != changed["ETag"]


def test_static_cache_bound_evicts_oldest_entry(studio, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATIC_CACHE_BYTES", 15)
    monkeypatch.setattr(server, "STATIC_CACHE_ENTRY_BYTES", 12)
    first, second, third = [tmp_path / f"{name}.png" for name in ("one", "two", "large")]
    first.write_bytes(b"a" * 10)
    second.write_bytes(b"b" * 10)
    third.write_bytes(b"c" * 20)
    studio.static_response(first)
    studio.static_response(second)
    assert list(studio.static_cache) == [second] and studio.static_cache_size == 10
    studio.static_response(third)
    assert list(studio.static_cache) == [second] and studio.static_cache_size == 10


def test_packaged_asset_route_is_bounded_to_asset_directory(studio, tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "studio.ico").write_bytes(b"icon")
    (tmp_path / "secret.txt").write_text("private", encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", tmp_path)
    assert request(studio, "GET", "/assets/studio.ico")[2] == b"icon"
    assert request(studio, "GET", "/assets/%2e%2e/secret.txt")[0] == 404


def test_optional_geometry_transport_preserves_source_and_binding_precision(studio):
    positions = [index / 7 for index in range(270)]
    indices = list(range(270))
    binding = {"position": [1.234567890123, 2.123456789012, 3.123456789012], "offset": 100}
    scene = {"level": "town", "meshes": [{"positions": positions, "indices": indices, "editBinding": binding}], "objects": []}
    studio.backend.get_scene = lambda _: scene
    status, _, raw = request(studio, "GET", "/api/scene?level=town", headers={"X-Studio-Geometry": "packed-v1"})
    packed = json.loads(raw)
    assert status == 200 and packed["meshes"][0]["editBinding"] == binding
    buffer = packed["meshes"][0]["positions"]
    assert buffer["$studioBuffer"] == "f32" and buffer["length"] == len(positions)
    decoded = array("f")
    decoded.frombytes(base64.b64decode(buffer["data"]))
    assert decoded == array("f", positions)
    index_buffer = packed["meshes"][0]["indices"]
    assert index_buffer["$studioBuffer"] == "u32"
    assert scene["meshes"][0]["positions"] is positions  # no source/project mutation
    status, _, ordinary = request(studio, "GET", "/api/scene?level=town")
    assert status == 200 and json.loads(ordinary)["meshes"][0]["positions"] == positions


def test_geometry_transport_also_encodes_mutation_scene(studio):
    studio.backend.import_model = lambda *args: {"scene": {"meshes": [{"positions": [1.0] * 270}], "objects": []}}
    status, _, raw = request(studio, "POST", "/api/import-model", {"level": "town", "name": "mesh", "model": {}},
                             {"Content-Type": "application/json", "X-Studio-Geometry": "packed-v1"})
    assert status == 200 and json.loads(raw)["scene"]["meshes"][0]["positions"]["$studioBuffer"] == "f32"


def test_geometry_transport_never_packs_small_vectors_or_unrelated_arrays():
    value = {"positions": [1, 2, 3], "anything": [1] * 400, "offsets": [100] * 400,
             "rotation": [0.123456789012, 0, 0], "indices": [-1] * 400}
    assert server.pack_scene_transport(value) == value
    with pytest.raises(ValueError, match="finies"):
        server.pack_scene_transport({"positions": [float("nan")] * 400})


def test_geometry_transport_buffer_count_limit_keeps_unpacked_fallback(monkeypatch):
    monkeypatch.setattr(server, "MAX_GEOMETRY_BUFFER_VALUES", 300)
    positions = [0.123456789012] * 400
    assert server.pack_scene_transport({"positions": positions})["positions"] is positions


@pytest.fixture
def asset_studio(asset_backend, tmp_path):
    instance = server.StudioServer(("127.0.0.1", 0), asset_backend, tmp_path / "imports")
    worker = threading.Thread(target=instance.serve_forever, daemon=True)
    worker.start()
    yield instance
    instance.shutdown()
    instance.server_close()
    worker.join(timeout=3)


def test_actual_asset_api_texture_model_duplicate_reset_and_undo_preserve_source(asset_studio):
    original = asset_studio.backend._source_path("town").read_bytes()
    headers = {"Content-Type": "application/json", "X-Studio-Geometry": "packed-v1"}
    def post(route, body):
        status, _, raw = request(asset_studio, "POST", route, {"level": "town", **body}, headers)
        assert status == 200, raw
        return json.loads(raw)
    texture = post("/api/import-texture", {"name": "Texture", "image": image_data()})
    imported = post("/api/import-model", {"name": "Triangle", "model": model() | {"textureId": texture["id"]}, "position": [10, 20, 30]})
    duplicate = post("/api/duplicate", {"id": imported["id"], "offset": [0, 0, 4]})
    assert not duplicate["gameExportable"] and len(duplicate["scene"]["meshes"]) == 3
    reset = post("/api/reset-level", {})
    assert len(reset["scene"]["meshes"]) == 1
    restored = post("/api/undo", {})
    assert len(restored["meshes"]) == 3
    assert asset_studio.backend._source_path("town").read_bytes() == original
