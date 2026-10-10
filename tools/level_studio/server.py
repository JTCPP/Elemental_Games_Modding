"""Loopback-only desktop web application for the user's own Azurik dump."""
from __future__ import annotations

import argparse
from array import array
import base64
import binascii
from collections import OrderedDict
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import mimetypes
import math
import os
from pathlib import Path
import re
import threading
import time
import traceback
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit
import webbrowser
from uuid import uuid4
from editor_backend import DEFAULT_SOURCE, DEFAULT_TOOLKIT
from xbox_iso import ISOImports, MAX_IMAGE_BYTES
from studio_paths import ASSET_ROOT, DATA_ROOT
from studio_version import VERSION
from level_jobs import LevelBuildJobs

ROOT = ASSET_ROOT
MAX_REQUEST = 64 * 1024
MAX_CAPTURE_REQUEST = 16 * 1024 * 1024
MAX_ASSET_REQUEST = 32 * 1024 * 1024
STATIC_CACHE_BYTES = 32 * 1024 * 1024
STATIC_CACHE_ENTRY_BYTES = 8 * 1024 * 1024
ASSET_UPLOAD_ROUTES = {"/api/import-model", "/api/replace-model",
                       "/api/import-texture", "/api/replace-texture"}
GEOMETRY_BUFFER_KEYS = {"positions", "indices", "normals", "uvs", "uv2", "colors", "sourceNormals"}
MAX_GEOMETRY_BUFFER_VALUES = 16_000_000
CAPTURE_NAME = re.compile(r"capture-\d{8}-\d{6}-\d{6}-[0-9a-f]{8}\.png")


def pack_scene_transport(value, key=None):
    """Encode only geometry buffers at the float32 precision used by WebGL.

    This is opt-in transport, not a project/source change. Small transform
    vectors and all edit bindings retain their original JSON representation.
    """
    if isinstance(value, dict):
        return {name: pack_scene_transport(item, name) for name, item in value.items()}
    if isinstance(value, list):
        if key in GEOMETRY_BUFFER_KEYS and 256 <= len(value) <= MAX_GEOMETRY_BUFFER_VALUES:
            try:
                packed = array("I" if key == "indices" else "f", value)
            except (TypeError, ValueError, OverflowError):
                # Non-buffer lists or values outside float32 are still handled
                # by strict ordinary JSON; nothing is truncated or clamped.
                return value
            if packed.itemsize != 4:
                return value
            if key != "indices" and not all(math.isfinite(number) for number in packed):
                raise ValueError("Les coordonnées de géométrie doivent être finies.")
            if sys.byteorder != "little":
                packed.byteswap()
            return {"$studioBuffer": "u32" if key == "indices" else "f32", "length": len(packed),
                    "data": base64.b64encode(packed.tobytes()).decode("ascii")}
        # Plain scalar lists need no recursive copy and stay exact.
        if value and isinstance(value[0], (dict, list)):
            return [pack_scene_transport(item) for item in value]
    return value


def capture_directory(backend):
    project = Path(backend.project_dir).resolve()
    folder = (project / "captures").resolve()
    if not folder.is_relative_to(project):
        raise ValueError("Le dossier de captures doit rester dans le projet.")
    return folder


def save_capture(backend, data_url):
    from PIL import Image, UnidentifiedImageError
    prefix = "data:image/png;base64,"
    if not isinstance(data_url, str) or not data_url.startswith(prefix):
        raise ValueError("Une image PNG encodée est attendue.")
    try:
        binary = base64.b64decode(data_url[len(prefix):], validate=True)
        if not binary or len(binary) > MAX_CAPTURE_REQUEST:
            raise ValueError("La capture est vide ou trop volumineuse.")
        with Image.open(io.BytesIO(binary)) as image:
            width, height = image.size
            if image.format != "PNG" or not 0 < width <= 8192 or not 0 < height <= 8192 or width * height > 16_000_000:
                raise ValueError("La capture PNG dépasse les dimensions autorisées.")
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("Une capture PNG fixe est attendue.")
            image.verify()
        # verify checks container integrity; load also checks pixel decoding.
        with Image.open(io.BytesIO(binary)) as image:
            image.load()
    except (binascii.Error, UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("La capture PNG est invalide.") from exc
    folder = capture_directory(backend)
    folder.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime("capture-%Y%m%d-%H%M%S-%f-") + uuid4().hex[:8] + ".png"
    path = folder / name
    with path.open("xb") as stream:
        stream.write(binary)
    return {"path": str(path), "url": "/captures/" + name}


class StudioServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, backend, imports_dir=None):
        super().__init__(address, StudioHandler)
        self.backend = backend
        self.studio_lock = threading.RLock()
        self.character_library = None
        self.asset_library = None
        self.imports = ISOImports(imports_dir or DATA_ROOT / "imports")
        self.level_builds = LevelBuildJobs()
        self.original_backend = backend
        self.active_source = "original" if backend is not None else None
        self.static_cache = OrderedDict()
        self.static_cache_size = 0
        self.static_cache_lock = threading.RLock()

    def static_response(self, path):
        """Avoid rereading and recompressing unchanged bundled modules/images."""
        stat = path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns)
        with self.static_cache_lock:
            cached = self.static_cache.pop(path, None)
            if cached is not None:
                if cached["stamp"] == stamp:
                    self.static_cache[path] = cached
                    return cached
                self.static_cache_size -= cached["bytes"]
            content = path.read_bytes()
            encoded = gzip.compress(content, compresslevel=3, mtime=0) if path.suffix in (".js", ".css", ".html") else None
            result = {"stamp": stamp, "content": content, "gzip": encoded,
                      "etag": '"' + hashlib.sha256(content).hexdigest() + '"',
                      "bytes": len(content) + (len(encoded) if encoded is not None else 0)}
            if result["bytes"] <= STATIC_CACHE_ENTRY_BYTES:
                while self.static_cache and self.static_cache_size + result["bytes"] > STATIC_CACHE_BYTES:
                    _, removed = self.static_cache.popitem(last=False)
                    self.static_cache_size -= removed["bytes"]
                self.static_cache[path] = result
                self.static_cache_size += result["bytes"]
            return result

    def require_backend(self):
        if self.backend is None:
            raise ValueError("Importez une ISO Azurik pour ouvrir les niveaux.")
        return self.backend

    def sources(self):
        return {"active": self.active_source, "needsImport": self.backend is None,
                "sourceDir": str(self.backend.source_dir) if self.backend is not None else None,
                "originalAvailable": self.original_backend is not None, "imports": self.imports.list()}

    def open_source(self, identifier):
        with self.studio_lock:
            if identifier == "original":
                if self.original_backend is None:
                    raise ValueError("Aucun dump original n’est disponible.")
                backend = self.original_backend
            else:
                source = self.imports.source(identifier)
                from editor_backend import StudioBackend
                folder = self.imports.directory / identifier
                backend = StudioBackend(source, folder / "project", folder / "exports",
                                        texture_dir=folder / "cache" / "textures")
            # Existing projects are never replaced; each imported disc has its
            # own source, project and texture cache. Switch all libraries atomically.
            self.backend = backend
            self.active_source = identifier
            self.character_library = None
            self.asset_library = None
            return self.sources()

    def characters(self):
        self.require_backend()
        if self.character_library is None:
            from character_library import CharacterLibrary
            self.character_library = CharacterLibrary(
                self.backend.source_dir, getattr(self.backend, "texture_dir", DATA_ROOT / "cache" / "textures"))
        return self.character_library

    def graphics(self):
        self.require_backend()
        if self.asset_library is None:
            from asset_library import AssetLibrary
            self.asset_library = AssetLibrary(
                self.backend.source_dir, getattr(self.backend, "texture_dir", DATA_ROOT / "cache" / "textures"))
        return self.asset_library


class StudioHandler(BaseHTTPRequestHandler):
    server_version = f"AzurikLevelStudio/{VERSION}"
    # WebView2 needs persistent, length-delimited responses for large gzip
    # modules; HTTP/1.0 connection teardown could reset the Three.js transfer.
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"{self.log_date_time_string()} {fmt % args}", flush=True)

    def _send(self, content: bytes, mime: str, status: int = 200, compress=False, download_name=None,
              content_encoding=None, etag=None):
        compressed = compress and "gzip" in self.headers.get("Accept-Encoding", "")
        if compressed:
            content = gzip.compress(content, compresslevel=3)
            content_encoding = "gzip"
        if etag is not None and self.headers.get("If-None-Match") == etag:
            content = b""
            status = 304
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-cache")
        if etag is not None:
            self.send_header("ETag", etag)
        if download_name is not None:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        if content_encoding is not None:
            self.send_header("Content-Encoding", content_encoding)
            self.send_header("Vary", "Accept-Encoding")
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _json(self, value, status=200):
        if status == 200 and self.headers.get("X-Studio-Geometry") == "packed-v1":
            value = pack_scene_transport(value)
        self._send(json.dumps(value, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8"),
                   "application/json; charset=utf-8", status, compress=True)

    def _valid_host(self):
        allowed = {f"127.0.0.1:{self.server.server_port}",
                   f"localhost:{self.server.server_port}"}
        return self.headers.get("Host") in allowed

    def _scene(self, level):
        scene = self.server.require_backend().get_scene(level)
        return self._enrich_scene(scene)

    def _enrich_scene(self, scene):
        scene.setdefault("warnings", scene.get("capabilities", {}).get("warnings", []))
        return self.server.characters().enrich(scene)

    def _fail(self, exc):
        if isinstance(exc, (ValueError, KeyError, FileNotFoundError)):
            self._json({"error": str(exc)}, 400)
        else:
            traceback.print_exc()
            self._json({"error": "Le chargement a échoué. Consulte le journal de l'éditeur.",
                        "detail": str(exc)}, 500)

    def do_GET(self):
        if not self._valid_host():
            self._json({"error": "Hôte non autorisé"}, 403)
            return
        route = urlsplit(self.path)
        query = parse_qs(route.query)
        try:
            if route.path == "/api/health":
                self._json({"app": "azurik-level-studio", "version": VERSION})
                return
            if route.path == "/api/import-iso":
                self._json(self.server.imports.status(query.get("id", [""])[0]))
                return
            if route.path == '/api/build-iso':
                self._json(self.server.level_builds.status(query.get('id', [''])[0]))
                return
            if route.path == "/api/sources":
                with self.server.studio_lock:
                    self._json(self.server.sources())
                return
            if route.path == "/api/catalog":
                with self.server.studio_lock:
                    if self.server.backend is None:
                        self._json({"levels": [], "needsImport": True, "sourceDir": None, "projectDir": None})
                        return
                    catalog = self.server.backend.catalog()
                    if isinstance(catalog, list):
                        catalog = {"levels": [dict(level, group=level.get("family", "other"),
                                                   file=level["id"] + ".xbr") for level in catalog],
                                   "sourceDir": str(self.server.backend.source_dir),
                                   "projectDir": str(self.server.backend.project_dir)}
                    manager = getattr(self.server.backend, 'level_management', None)
                    catalog['levelManagement'] = manager() if manager else {'created': [], 'deleted': [], 'canUndo': False, 'canRedo': False}
                    self._json(catalog)
                return
            if route.path == "/api/scene":
                with self.server.studio_lock:
                    self._json(self._scene(query.get("level", ["town"])[0]))
                return
            if route.path == "/api/project":
                with self.server.studio_lock:
                    self._json(self.server.require_backend().project_summary())
                return
            if route.path in ("/api/characters", "/api/character"):
                with self.server.studio_lock:
                    library = self.server.characters()
                    self._json(library.catalog() if route.path == "/api/characters" else
                               library.model(query.get("name", [""])[0]))
                return
            if route.path in ("/api/library", "/api/library/model"):
                with self.server.studio_lock:
                    library = self.server.graphics()
                    if route.path == "/api/library/model":
                        result = library.model(query.get("archive", [""])[0], query.get("id", [""])[0])
                    elif "archive" in query:
                        result = library.catalog(query["archive"][0])
                    else:
                        result = library.inventory()
                    self._json(result)
                return
            if route.path.startswith("/api/"):
                self._json({"error": "Commande inconnue"}, 404)
                return
            if route.path.startswith("/captures/"):
                name = unquote(route.path[len("/captures/"):])
                if not CAPTURE_NAME.fullmatch(name):
                    self._json({"error": "Capture introuvable"}, 404)
                    return
                folder = capture_directory(self.server.backend)
                capture = (folder / name).resolve()
                if not capture.is_relative_to(folder) or not capture.is_file():
                    self._json({"error": "Capture introuvable"}, 404)
                    return
                with capture.open("rb") as stream:
                    content = stream.read(MAX_CAPTURE_REQUEST + 1)
                if len(content) > MAX_CAPTURE_REQUEST:
                    raise ValueError("La capture dépasse la taille autorisée.")
                self._send(content, "image/png", download_name=name if query.get("download") == ["1"] else None)
                return
            if route.path.startswith("/textures/"):
                static_root = Path(self.server.require_backend().texture_dir)
                relative = unquote(route.path[len("/textures/"):])
            elif route.path.startswith("/assets/"):
                static_root = ROOT / "assets"
                relative = unquote(route.path[len("/assets/"):])
            else:
                static_root = ROOT / "web"
                relative = unquote(route.path.lstrip("/")) or "index.html"
            candidate = (static_root / relative).resolve()
            if not candidate.is_relative_to(static_root.resolve()) or not candidate.is_file():
                self._json({"error": "Fichier introuvable"}, 404)
                return
            mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            if candidate.suffix in (".js", ".mjs"):
                mime = "text/javascript; charset=utf-8"
            cached = self.server.static_response(candidate)
            encoding = "gzip" if cached["gzip"] is not None and "gzip" in self.headers.get("Accept-Encoding", "") else None
            # Representation-specific validators avoid sharing the compressed
            # validator with clients which did not request gzip.
            etag = cached["etag"][:-1] + ('-gzip"' if encoding else '"')
            self._send(cached["gzip"] if encoding else cached["content"], mime,
                       content_encoding=encoding, etag=etag)
        except Exception as exc:
            self._fail(exc)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json({"error": "Taille de requête incorrecte"}, 400)
            return
        path = urlsplit(self.path).path
        if path == "/api/import-iso-upload":
            self._upload_iso(length)
            return
        maximum = (MAX_ASSET_REQUEST if path in ASSET_UPLOAD_ROUTES else
                   MAX_CAPTURE_REQUEST if path == "/api/capture" else MAX_REQUEST)
        if not 0 < length <= maximum:
            # Drain modest rejected bodies so Windows delivers the 413 response
            # instead of resetting the socket while the client finishes sending.
            self._drain_rejected_upload(length)
            self.close_connection = True
            self._json({"error": "Requête vide ou trop volumineuse"}, 413)
            return
        # Drain bounded bodies before rejecting, avoiding TCP resets on Windows.
        self.connection.settimeout(30)
        try:
            raw_payload = self.rfile.read(length)
        except OSError:
            self.close_connection = True
            self._json({"error": "La requête est incomplète ou a expiré."}, 400)
            return
        if len(raw_payload) != length:
            self.close_connection = True
            self._json({"error": "La requête est incomplète."}, 400)
            return
        origin = self.headers.get("Origin", "")
        allowed_origins = {f"http://127.0.0.1:{self.server.server_port}",
                           f"http://localhost:{self.server.server_port}"}
        if (not self._valid_host() or (origin and origin not in allowed_origins)
                or self.headers.get("Sec-Fetch-Site") == "cross-site"
                or self.headers.get("Content-Type", "").split(";")[0] != "application/json"):
            self._json({"error": "Requête non autorisée"}, 403)
            return
        try:
            def reject_constant(value):
                raise ValueError("Les nombres JSON doivent être finis.")
            try:
                payload = json.loads(raw_payload, parse_constant=reject_constant)
            except RecursionError as exc:
                raise ValueError("La structure JSON est trop imbriquée.") from exc
            if not isinstance(payload, dict):
                raise ValueError("Objet JSON attendu")
            if path == "/api/import-iso":
                self._json(self.server.imports.start(payload.get("path", "")), 202)
                return
            if path in ("/api/open-import", "/api/open-source"):
                self._json(self.server.open_source(payload.get("id", "")))
                return
            with self.server.studio_lock:
                backend = self.server.require_backend()
                if path == '/api/build-iso':
                    result = self.server.level_builds.start(payload.get('directory'), payload.get('input'),
                        payload.get('output'), backend.exports_dir, backend.source_dir)
                elif path == '/api/levels/create':
                    result = backend.create_level(payload.get('template'), payload.get('id'),
                        payload.get('name'), payload.get('family', 'perathia'))
                elif path == '/api/levels/delete':
                    result = backend.delete_level(payload.get('level'), payload.get('replacement'))
                elif path == '/api/levels/restore':
                    result = backend.restore_level(payload.get('level'))
                elif path in ('/api/levels/undo', '/api/levels/redo'):
                    result = backend.level_history(path.endswith('/undo'))
                elif path == "/api/capture":
                    result = save_capture(backend, payload.get("image"))
                elif path == "/api/reset-level":
                    result = backend.reset_level(payload["level"])
                    result["scene"] = self._enrich_scene(result["scene"])
                elif path == "/api/duplicate":
                    result = backend.duplicate(payload["level"], payload["id"], payload.get("offset"))
                    result["scene"] = self._enrich_scene(result["scene"])
                elif path == "/api/delete-asset":
                    result = backend.delete_asset(payload["level"], payload["id"])
                    result["scene"] = self._enrich_scene(result["scene"])
                elif path == "/api/import-model":
                    result = backend.import_model(payload["level"], payload.get("name"), payload.get("model"),
                                                  payload.get("position"))
                    result["scene"] = self._enrich_scene(result["scene"])
                elif path == "/api/replace-model":
                    result = backend.replace_model(payload["level"], payload["id"], payload.get("name"), payload.get("model"))
                    result["scene"] = self._enrich_scene(result["scene"])
                elif path == "/api/import-texture":
                    result = backend.import_texture(payload["level"], payload.get("name"), payload.get("image"))
                    result["scene"] = self._enrich_scene(result["scene"])
                elif path == "/api/replace-texture":
                    result = backend.replace_texture(payload["level"], payload["id"], payload.get("name"), payload.get("image"))
                    result["scene"] = self._enrich_scene(result["scene"])
                elif path == "/api/move":
                    result = backend.move(payload["level"], payload["id"], payload["position"])
                elif path == "/api/lock":
                    result = backend.set_lock(payload["level"], payload.get("locked"),
                                              payload.get("id"), payload.get("all", False))
                    result["scene"].setdefault("warnings", result["scene"].get("capabilities", {}).get("warnings", []))
                    result["scene"] = self.server.characters().enrich(result["scene"])
                elif path == "/api/transform":
                    if not any(key in payload for key in ("position", "rotation", "scale")):
                        raise ValueError("Une transformation est attendue")
                    result = backend.transform(payload["level"], payload["id"],
                                               position=payload.get("position"),
                                               rotation=payload.get("rotation"),
                                               scale=payload.get("scale"))
                    if "scene" in result:
                        result["scene"].setdefault("warnings", result["scene"].get("capabilities", {}).get("warnings", []))
                        result["scene"] = self.server.characters().enrich(result["scene"])
                    else:
                        result["scene"] = self._scene(payload["level"])
                elif path in ("/api/undo", "/api/redo"):
                    result = getattr(backend, path.split("/")[-1])(payload["level"])
                    if not result.get('catalogChanged'):
                        result = self._scene(result.get('activeLevel', payload['level']))
                elif path == "/api/save":
                    result = backend.save()
                elif path == "/api/export":
                    result = backend.export()
                elif path == "/api/reveal-export":
                    directory = Path(payload["directory"]).resolve()
                    exports = Path(backend.exports_dir).resolve()
                    if not directory.is_relative_to(exports) or not directory.is_dir():
                        raise ValueError("Dossier d'export inconnu")
                    os.startfile(str(directory))
                    result = {"ok": True}
                else:
                    self._json({"error": "Commande inconnue"}, 404)
                    return
                self._json(result if result is not None else {"ok": True})
        except Exception as exc:
            self._fail(exc)

    def _upload_iso(self, length):
        origin = self.headers.get("Origin", "")
        allowed_origins = {f"http://127.0.0.1:{self.server.server_port}",
                           f"http://localhost:{self.server.server_port}"}
        if (not self._valid_host() or (origin and origin not in allowed_origins)
                or self.headers.get("Sec-Fetch-Site") == "cross-site"
                or self.headers.get("Content-Type", "").split(";")[0] != "application/octet-stream"):
            self._drain_rejected_upload(length)
            self.close_connection = True
            self._json({"error": "Requête non autorisée"}, 403)
            return
        if not 33 * 2048 <= length <= MAX_IMAGE_BYTES:
            self._drain_rejected_upload(length)
            self.close_connection = True
            self._json({"error": "L’ISO est tronquée ou dépasse 16 Gio."}, 413)
            return
        received = 0
        handler = self
        class CountedInput:
            def read(self, size):
                nonlocal received
                chunk = handler.rfile.read(size)
                received += len(chunk)
                return chunk
        try:
            self.connection.settimeout(120)
            job = self.server.imports.upload(CountedInput(), length,
                                            unquote(self.headers.get("X-File-Name", "game.iso")))
            self._json(job, 202)
        except Exception as exc:
            if isinstance(exc, ValueError) and 0 < length - received <= MAX_CAPTURE_REQUEST:
                # Let modest rejected bodies finish so Windows returns the
                # JSON error rather than resetting an in-flight client socket.
                try:
                    self.connection.settimeout(2)
                    while received < length:
                        chunk = self.rfile.read(min(MAX_REQUEST, length - received))
                        if not chunk:
                            break
                        received += len(chunk)
                except OSError:
                    pass
            self.close_connection = True
            self._fail(exc)

    def _drain_rejected_upload(self, length):
        """Consume an already-sent modest body before returning a Windows error.

        Host-only / malicious requests may declare a body without sending it;
        the short total deadline keeps their rejection immediate and bounded.
        """
        if not 0 < length <= MAX_CAPTURE_REQUEST:
            return
        deadline = time.monotonic() + 0.15
        remaining = length
        try:
            while remaining and time.monotonic() < deadline:
                self.connection.settimeout(max(0.001, deadline - time.monotonic()))
                chunk = self.rfile.read1(min(MAX_REQUEST, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description="Azurik Level Studio — éditeur local")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--toolkit", type=Path, default=DEFAULT_TOOLKIT)
    parser.add_argument("--project-dir", type=Path, default=DATA_ROOT / "projects" / "default")
    parser.add_argument("--exports-dir", type=Path, default=DATA_ROOT / "exports")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true")
    options = parser.parse_args()
    from editor_backend import StudioBackend
    source = options.source.resolve()
    gamedata = source if source.name.lower() == "gamedata" else source / "gamedata"
    backend = (StudioBackend(source_dir=source, toolkit_dir=options.toolkit,
                             project_dir=options.project_dir, exports_dir=options.exports_dir,
                             texture_dir=DATA_ROOT / "cache" / "textures") if gamedata.is_dir() else None)
    server = StudioServer(("127.0.0.1", options.port), backend)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Azurik Level Studio — {url}", flush=True)
    if options.open:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
