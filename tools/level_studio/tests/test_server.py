import base64
import gzip
import http.client
import io
import json
import os
import threading
from pathlib import Path

import pytest

from server import StudioServer


class StubBackend:
    source_dir = Path("source")
    project_dir = Path("project")

    def catalog(self):
        return [{"id": "town", "family": "perathia"}]

    def get_scene(self, level):
        return {"level": level, "meshes": [], "objects": []}

    def move(self, level, item, position):
        return {"position": position, "pendingCount": 1}

    def transform(self, level, item, rotation=None, scale=None, position=None):
        return {"id": item, "rotation": rotation, "scale": scale, "position": position,
                "pendingCount": 1}


@pytest.fixture
def studio(tmp_path):
    backend = StubBackend()
    backend.project_dir = tmp_path / "project"
    server = StudioServer(("127.0.0.1", 0), backend)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def request(studio, method, path, data=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", studio.server_port, timeout=5)
    content = json.dumps(data) if data is not None else None
    conn.request(method, path, body=content, headers=headers or {})
    response = conn.getresponse()
    body = response.read()
    result = response.status, dict(response.getheaders()), body
    conn.close()
    return result


def test_catalog_and_gzip_scene(studio):
    status, headers, body = request(studio, "GET", "/api/scene?level=town", headers={"Accept-Encoding": "gzip"})
    assert status == 200
    assert headers["Content-Encoding"] == "gzip"
    assert json.loads(gzip.decompress(body))["level"] == "town"


def test_large_gzip_module_keeps_connection_for_next_request(studio):
    # Exercise the real bundled module, rather than a tiny synthetic response:
    # WebView2 previously reset this transfer before it could start the editor.
    from server import ROOT
    conn = http.client.HTTPConnection("127.0.0.1", studio.server_port, timeout=10)
    try:
        conn.request("GET", "/vendor/three.module.js", headers={"Accept-Encoding": "gzip"})
        response = conn.getresponse()
        assert response.status == 200 and response.version == 11
        assert response.getheader("Content-Encoding") == "gzip"
        compressed = response.read()
        assert len(compressed) == int(response.getheader("Content-Length"))
        assert gzip.decompress(compressed) == (ROOT / "web/vendor/three.module.js").read_bytes()
        socket = conn.sock
        assert socket is not None
        conn.request("GET", "/api/catalog")
        response = conn.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["levels"][0]["id"] == "town"
        assert conn.sock is socket
    finally:
        conn.close()


def test_catalog_adapts_backend_levels_for_browser(studio):
    status, _, body = request(studio, "GET", "/api/catalog")
    assert status == 200
    catalog = json.loads(body)
    assert catalog["levels"][0] == {"id": "town", "family": "perathia", "group": "perathia", "file": "town.xbr"}
    assert catalog["sourceDir"] == "source"


def test_cross_origin_move_is_rejected(studio):
    status, _, _ = request(studio, "POST", "/api/move", {"level": "town", "id": "one", "position": [1, 2, 3]},
                           {"Content-Type": "application/json", "Origin": "https://untrusted.example"})
    assert status == 403


def test_local_move_returns_actual_coordinates(studio):
    status, _, body = request(studio, "POST", "/api/move", {"level": "town", "id": "one", "position": [1, 2, 3]},
                              {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{studio.server_port}"})
    assert status == 200
    assert json.loads(body)["position"] == [1, 2, 3]


def test_static_path_cannot_escape_web_directory(studio):
    status, _, body = request(studio, "GET", "/%2e%2e/server.py")
    assert status == 404
    assert b"Loopback-only" not in body


def test_transform_returns_updated_scene_and_local_parameters(studio):
    status, _, body = request(studio, "POST", "/api/transform",
                              {"level": "town", "id": "one", "rotation": [0, 0, 1.5], "scale": [1, 2, 1]},
                              {"Content-Type": "application/json"})
    assert status == 200
    result = json.loads(body)
    assert result["rotation"] == [0, 0, 1.5]
    assert result["scale"] == [1, 2, 1]
    assert result["scene"]["level"] == "town"


def test_transform_requires_an_explicit_channel(studio):
    status, _, _ = request(studio, "POST", "/api/transform", {"level": "town", "id": "one"},
                           {"Content-Type": "application/json"})
    assert status == 400


def test_character_name_cannot_be_used_as_a_filesystem_path(studio):
    status, _, _ = request(studio, "GET", "/api/character?name=../../server.py")
    assert status == 400


def test_unrecognized_host_is_rejected(studio):
    status, _, _ = request(studio, "GET", "/api/catalog", headers={"Host": "evil.example"})
    assert status == 403


def png_capture(size=(3, 2), random_pixels=False):
    from PIL import Image
    image = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)) if random_pixels else Image.new("RGB", size, "blue")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    binary = buffer.getvalue()
    return "data:image/png;base64," + base64.b64encode(binary).decode("ascii"), binary


def test_capture_persists_exact_png_and_serves_generated_download(studio):
    data_url, expected = png_capture()
    headers = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{studio.server_port}"}
    status, _, body = request(studio, "POST", "/api/capture", {"image": data_url}, headers)
    assert status == 200
    capture = json.loads(body)
    path = Path(capture["path"])
    assert path.parent == studio.backend.project_dir / "captures"
    assert path.read_bytes() == expected
    assert capture["url"] == "/captures/" + path.name
    status, response_headers, image = request(studio, "GET", capture["url"])
    assert status == 200 and response_headers["Content-Type"] == "image/png" and image == expected
    assert "Content-Disposition" not in response_headers
    status, response_headers, image = request(studio, "GET", capture["url"] + "?download=1")
    assert status == 200 and image == expected
    assert response_headers["Content-Disposition"] == f'attachment; filename="{path.name}"'
    status, _, body = request(studio, "POST", "/api/capture", {"image": data_url}, headers)
    assert status == 200 and json.loads(body)["path"] != str(path)
    assert path.read_bytes() == expected  # A second capture never overwrites the first.


def test_capture_has_larger_limit_without_relaxing_other_post_routes(studio):
    data_url, expected = png_capture((256, 256), random_pixels=True)
    assert len(json.dumps({"image": data_url})) > 64 * 1024
    status, _, body = request(studio, "POST", "/api/capture", {"image": data_url}, {"Content-Type": "application/json"})
    assert status == 200 and Path(json.loads(body)["path"]).read_bytes() == expected
    status, _, _ = request(studio, "POST", "/api/move", {"image": "x" * (65 * 1024)}, {"Content-Type": "application/json"})
    assert status == 413


def test_capture_rejects_request_larger_than_16_mebibytes(studio):
    from server import MAX_CAPTURE_REQUEST
    # Oversized declared bodies are rejected before image parsing or writes.
    status, _, _ = request(studio, "POST", "/api/capture", headers={"Content-Type": "application/json",
                                                                  "Content-Length": str(MAX_CAPTURE_REQUEST + 1)})
    assert status == 413
    assert not (studio.backend.project_dir / "captures").exists()


@pytest.mark.parametrize("image", [None, "data:image/jpeg;base64,aW52YWxpZA==", "data:image/png;base64,???",
                                 "data:image/png;base64," + base64.b64encode(b"not a PNG").decode("ascii")])
def test_invalid_capture_does_not_create_a_file(studio, image):
    status, _, _ = request(studio, "POST", "/api/capture", {"image": image}, {"Content-Type": "application/json"})
    assert status == 400
    assert not (studio.backend.project_dir / "captures").exists()


@pytest.mark.parametrize("size", [(8193, 1), (4001, 4000)])
def test_capture_dimension_and_pixel_limits(studio, size):
    data_url, _ = png_capture(size)
    status, _, _ = request(studio, "POST", "/api/capture", {"image": data_url}, {"Content-Type": "application/json"})
    assert status == 400
    assert not (studio.backend.project_dir / "captures").exists()


@pytest.mark.parametrize("url", ["/captures/%2e%2e/server.py", "/captures/%2e%2e%2fserver.py",
                                "/captures/%2e%2e%5cserver.py", "/captures/arbitrary.png"])
def test_capture_paths_cannot_escape_generated_image_names(studio, url):
    status, _, body = request(studio, "GET", url)
    assert status == 404 and b"Loopback-only" not in body


def test_capture_preserves_host_and_origin_checks(studio):
    data_url, _ = png_capture()
    for extra in ({"Origin": "https://untrusted.example"}, {"Host": "evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        status, _, _ = request(studio, "POST", "/api/capture", {"image": data_url}, {"Content-Type": "application/json", **extra})
        assert status == 403
    assert not (studio.backend.project_dir / "captures").exists()


def test_capture_get_reads_at_most_the_capture_limit(studio):
    from server import MAX_CAPTURE_REQUEST
    folder = studio.backend.project_dir / "captures"
    folder.mkdir(parents=True)
    name = "capture-20261007-120000-123456-01234567.png"
    with (folder / name).open("wb") as stream:
        stream.truncate(MAX_CAPTURE_REQUEST + 1)
    status, _, body = request(studio, "GET", "/captures/" + name)
    assert status == 400 and len(body) < 1024


def test_graphics_library_routes_use_explicit_archive_and_model(studio):
    class Library:
        def inventory(self):
            return {"archives": [{"id": "fx"}]}
        def catalog(self, archive):
            if archive != "fx":
                raise ValueError("Invalid archive")
            return {"archive": {"id": archive}, "textures": [], "models": []}
        def model(self, archive, identifier):
            if archive != "fx" or identifier != "graph-4":
                raise ValueError("Invalid model")
            return {"id": identifier, "sourceArchive": "fx.xbr"}
    studio.asset_library = Library()
    for path, key, value in (("/api/library", "archives", [{"id": "fx"}]),
                             ("/api/library?archive=fx", "archive", {"id": "fx"}),
                             ("/api/library/model?archive=fx&id=graph-4", "id", "graph-4")):
        status, _, body = request(studio, "GET", path)
        assert status == 200 and json.loads(body)[key] == value
    for path in ("/api/library?archive=../../server.py", "/api/library/model?archive=fx&id=../x",
                 "/api/library/model?id=graph-4"):
        assert request(studio, "GET", path)[0] == 400
