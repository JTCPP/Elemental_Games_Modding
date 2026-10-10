"""Packaged resources are read-only; user data survives executable restarts.

Each mode is imported in a fresh process so fake PyInstaller state and paths
cannot leak into the real studio, other tests, or the user's saved project.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


STUDIO = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated_studio(tmp_path):
    module_root = tmp_path / "source checkout [isolated]"
    assets = tmp_path / "_MEI bundled assets"
    local = tmp_path / "user local data"
    home = tmp_path / "isolated home"
    for folder in (module_root, assets, local, home):
        folder.mkdir()
    shutil.copyfile(STUDIO / "studio_paths.py", module_root / "studio_paths.py")
    # Pytest updates its own phase marker between fixture setup and teardown.
    parent_environment = {key: value for key, value in os.environ.items()
                          if key != "PYTEST_CURRENT_TEST"}
    parent_runtime = {key: (hasattr(sys, key), getattr(sys, key, None))
                      for key in ("frozen", "_MEIPASS")}

    def run(code, *, frozen=False, localappdata=True):
        env = dict(os.environ)
        env.pop("AZURIK_SOURCE", None)
        env.pop("AZURIK_TOOLKIT", None)
        if localappdata:
            env["LOCALAPPDATA"] = str(local)
        else:
            env.pop("LOCALAPPDATA", None)
        settings = {"studio": str(STUDIO), "module_root": str(module_root),
                    "assets": str(assets), "local": str(local), "home": str(home),
                    "frozen": frozen, "code": code}
        script = """
import importlib.util
import json
import os
from pathlib import Path
import sys

settings = json.load(sys.stdin)
sys.path.insert(0, settings['studio'])
sys.frozen = settings['frozen']
sys._MEIPASS = settings['assets']
Path.home = classmethod(lambda cls: Path(settings['home']))
spec = importlib.util.spec_from_file_location(
    'studio_paths', Path(settings['module_root']) / 'studio_paths.py')
paths = importlib.util.module_from_spec(spec)
sys.modules['studio_paths'] = paths
spec.loader.exec_module(paths)
exec(settings['code'])
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script],
            input=json.dumps(settings), text=True, capture_output=True,
            cwd=tmp_path, env=env, timeout=20)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return json.loads(completed.stdout.splitlines()[-1])

    yield run
    assert {key: value for key, value in os.environ.items()
            if key != "PYTEST_CURRENT_TEST"} == parent_environment
    assert {key: (hasattr(sys, key), getattr(sys, key, None))
            for key in ("frozen", "_MEIPASS")} == parent_runtime


def test_source_mode_keeps_checkout_and_existing_source(isolated_studio):
    result = isolated_studio("""
source = Path(settings['module_root']) / 'original dump [source]'
(source / 'gamedata').mkdir(parents=True)
sentinel = source / 'gamedata' / 'source-marker'
sentinel.write_bytes(b'original untouched source')
project = paths.DATA_ROOT / 'projects' / 'default' / 'project.json'
project.parent.mkdir(parents=True)
saved = {'version': 1, 'sourceDir': str(source), 'levels': {}}
project.write_text(json.dumps(saved), encoding='utf-8')
before = project.read_bytes()
import editor_backend
backend = editor_backend.StudioBackend()
assert project.read_bytes() == before
assert sentinel.read_bytes() == b'original untouched source'
print(json.dumps({'assets': str(paths.ASSET_ROOT), 'data': str(paths.DATA_ROOT),
                  'source': str(backend.source_dir), 'expected': str(source),
                  'module_root': settings['module_root']}))
""")
    assert result["assets"] == result["data"] == result["module_root"]
    assert result["source"] == result["expected"]


def test_frozen_roots_use_bundle_only_for_assets(isolated_studio):
    result = isolated_studio("""
print(json.dumps({'assets': str(paths.ASSET_ROOT), 'data': str(paths.DATA_ROOT),
                  'expected_assets': settings['assets'],
                  'expected_data': str(Path(settings['local']) / 'AzurikLevelStudio'),
                  'roots_created': (paths.DATA_ROOT.exists())}))
""", frozen=True)
    assert result["assets"] == result["expected_assets"]
    assert result["data"] == result["expected_data"]
    assert result["data"] != result["assets"]
    assert not result["roots_created"], "Merely importing paths must not create user data."


def test_frozen_localappdata_falls_back_to_home(isolated_studio):
    result = isolated_studio("""
print(json.dumps({'data': str(paths.DATA_ROOT),
                  'expected': str(Path(settings['home']) / 'AppData' / 'Local' /
                                  'AzurikLevelStudio')}))
""", frozen=True, localappdata=False)
    assert result["data"] == result["expected"]


def test_frozen_saved_source_is_persistent_and_not_bundled(isolated_studio):
    result = isolated_studio("""
user_source = Path(settings['home']) / 'real game dump'
for root, source in ((paths.ASSET_ROOT, 'discarded bundled source'),
                     (paths.DATA_ROOT, str(user_source))):
    saved = root / 'projects' / 'default' / 'project.json'
    saved.parent.mkdir(parents=True)
    saved.write_text(json.dumps({'sourceDir': source}), encoding='utf-8')
import editor_backend
assert editor_backend.DEFAULT_SOURCE == user_source
assert editor_backend.default_source() == user_source
override = Path(settings['home']) / 'explicit game dump'
os.environ['AZURIK_SOURCE'] = str(override)
assert editor_backend.default_source() == override
print(json.dumps({'saved': str(editor_backend.DEFAULT_SOURCE),
                  'expected_saved': str(user_source)}))
""", frozen=True)
    assert result["saved"] == result["expected_saved"]


def test_frozen_backend_defaults_do_not_write_inside_bundle(isolated_studio):
    result = isolated_studio("""
import editor_backend
source = paths.DATA_ROOT / 'source'
(source / 'gamedata').mkdir(parents=True)
before = {str(p.relative_to(paths.ASSET_ROOT)): p.read_bytes()
          for p in paths.ASSET_ROOT.rglob('*') if p.is_file()}
backend = editor_backend.StudioBackend()
assert editor_backend.STUDIO_ROOT == paths.DATA_ROOT
assert backend.source_dir == source
assert backend.project_dir == paths.DATA_ROOT / 'projects' / 'default'
assert backend.exports_dir == paths.DATA_ROOT / 'exports'
assert backend.texture_dir == paths.DATA_ROOT / 'cache' / 'textures'
after = {str(p.relative_to(paths.ASSET_ROOT)): p.read_bytes()
         for p in paths.ASSET_ROOT.rglob('*') if p.is_file()}
assert before == after
assert all(not p.is_relative_to(paths.ASSET_ROOT)
           for p in (backend.project_dir, backend.exports_dir, backend.texture_dir))
print(json.dumps({'default_source': str(editor_backend.DEFAULT_SOURCE),
                  'expected': str(source)}))
""", frozen=True)
    assert result["default_source"] == result["expected"]


def test_frozen_server_serves_assets_and_user_textures_from_separate_roots(isolated_studio):
    result = isolated_studio("""
import contextlib
import http.client
import io
import threading
from types import SimpleNamespace
import server
import desktop

assets = paths.ASSET_ROOT
data = paths.DATA_ROOT
(assets / 'web').mkdir()
(assets / 'web' / 'index.html').write_bytes(b'packaged studio interface')
(data / 'web').mkdir(parents=True)
(data / 'web' / 'index.html').write_bytes(b'wrong mutable interface')
texture_dir = data / 'cache' / 'textures'
texture_dir.mkdir(parents=True)
(texture_dir / 'fixture.png').write_bytes(b'persistent cached texture')
bundle_before = {str(p.relative_to(assets)): p.read_bytes()
                 for p in assets.rglob('*') if p.is_file()}
backend = SimpleNamespace(source_dir=data / 'source', texture_dir=texture_dir)
session = desktop.StudioSession()
assert session.root == assets
assert session.data_root == data
assert server.ROOT == assets
with contextlib.redirect_stdout(io.StringIO()):
    studio = server.StudioServer(('127.0.0.1', 0), backend)
    thread = threading.Thread(target=studio.serve_forever, daemon=True)
    thread.start()
    try:
        assert studio.imports.directory == data / 'imports'
        responses = []
        for route in ('/', '/textures/fixture.png'):
            conn = http.client.HTTPConnection('127.0.0.1', studio.server_port, timeout=3)
            try:
                conn.request('GET', route)
                response = conn.getresponse()
                responses.append((response.status, response.read().decode()))
            finally:
                conn.close()
    finally:
        studio.shutdown()
        studio.server_close()
        thread.join(timeout=3)
bundle_after = {str(p.relative_to(assets)): p.read_bytes()
                for p in assets.rglob('*') if p.is_file()}
assert bundle_after == bundle_before
print(json.dumps({'responses': responses, 'stopped': not thread.is_alive()}))
""", frozen=True)
    assert result["responses"] == [
        [200, "packaged studio interface"], [200, "persistent cached texture"]]
    assert result["stopped"]


def test_frozen_library_cache_fallback_stays_in_user_data(isolated_studio):
    result = isolated_studio("""
from types import ModuleType, SimpleNamespace
import server
source = paths.DATA_ROOT / 'source'
calls = []
for module_name, class_name in (('character_library', 'CharacterLibrary'),
                                ('asset_library', 'AssetLibrary')):
    module = ModuleType(module_name)
    def library(source, cache):
        calls.append((str(source), str(cache)))
        return SimpleNamespace()
    setattr(module, class_name, library)
    sys.modules[module_name] = module
studio = server.StudioServer(('127.0.0.1', 0), SimpleNamespace(source_dir=source))
try:
    studio.characters()
    studio.graphics()
finally:
    studio.server_close()
print(json.dumps({'calls': calls,
                  'expected': [str(source), str(paths.DATA_ROOT / 'cache' / 'textures')]}))
""", frozen=True)
    assert result["calls"] == [result["expected"], result["expected"]]
