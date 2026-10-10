"""Server ownership and native-window startup; never opens an actual GUI."""
import argparse
import io
import json
import subprocess
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

import desktop


class FakeProcess:
    def __init__(self, returncode=None, slow_terminate=False):
        self.returncode = returncode
        self.slow_terminate = slow_terminate
        self.terminated = self.killed = False
        self.waits = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.slow_terminate and not self.killed:
            raise subprocess.TimeoutExpired("server", timeout)
        self.returncode = 0
        return 0


def fake_start(monkeypatch, health, *, process=None):
    process = process or FakeProcess()
    probes = iter(health)
    calls = []
    monkeypatch.setattr(desktop, "probe_health", lambda *args, **kwargs: next(probes))
    monkeypatch.setattr(desktop, "port_is_occupied", lambda _: False)

    def popen(command, **options):
        calls.append((command, options))
        return process

    monkeypatch.setattr(desktop.subprocess, "Popen", popen)
    monkeypatch.setattr(desktop.time, "sleep", lambda _: None)
    return process, calls


def test_reuses_existing_server_without_spawn_or_cleanup(monkeypatch, tmp_path):
    process, calls = fake_start(monkeypatch, [True])
    with desktop.StudioSession(root=tmp_path) as session:
        assert session.url == "http://127.0.0.1:8766"
        assert session.process is None
    assert calls == []
    assert not process.terminated and not process.killed
    assert not (tmp_path / "logs").exists()


def test_owned_server_source_is_one_argument_and_cleanup(monkeypatch, tmp_path):
    process, calls = fake_start(monkeypatch, [False, False, True])
    source = r"C:\Games\[#1] Azurik original dump"
    with desktop.StudioSession(8777, source, root=tmp_path) as session:
        assert session.process is process
        assert session.url.endswith(":8777")
    command, options = calls[0]
    assert command == [desktop.sys.executable, str(tmp_path / "server.py"), "--port", "8777", "--source", source]
    assert "--open" not in command
    assert options["cwd"] == str(tmp_path)
    assert options["stdin"] is subprocess.DEVNULL
    assert process.terminated and not process.killed
    assert process.waits == [5]


def test_unresponsive_occupied_port_does_not_spawn(monkeypatch, tmp_path):
    process, calls = fake_start(monkeypatch, [False])
    monkeypatch.setattr(desktop, "port_is_occupied", lambda _: True)
    with pytest.raises(desktop.DesktopError, match="occupé"):
        with desktop.StudioSession(root=tmp_path):
            pytest.fail("Must not open a window")
    assert calls == []
    assert not process.terminated


def test_server_early_exit_reports_log_without_killing_exited_process(monkeypatch, tmp_path):
    process, calls = fake_start(monkeypatch, [False], process=FakeProcess(returncode=2))
    with pytest.raises(desktop.DesktopError, match="code 2.*desktop-server-error.log"):
        with desktop.StudioSession(root=tmp_path):
            pass
    assert len(calls) == 1
    assert not process.terminated


def test_readiness_timeout_stops_owned_server(monkeypatch, tmp_path):
    process, _ = fake_start(monkeypatch, [False, False])
    times = iter([0.0, 0.5, 2.0, 2.0])
    monkeypatch.setattr(desktop.time, "monotonic", lambda: next(times))
    with pytest.raises(desktop.DesktopError, match="pas répondu à temps"):
        with desktop.StudioSession(root=tmp_path, ready_timeout=1.0):
            pass
    assert process.terminated
    assert process.waits == [5]


def test_foreign_service_during_start_stops_only_owned_process(monkeypatch, tmp_path):
    process, _ = fake_start(monkeypatch, [False])
    calls = 0

    def probe(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return False
        raise desktop.DesktopError("autre service")

    monkeypatch.setattr(desktop, "probe_health", probe)
    with pytest.raises(desktop.DesktopError, match="autre service"):
        with desktop.StudioSession(root=tmp_path):
            pass
    assert process.terminated


def test_owned_server_escalates_only_after_termination_timeout(tmp_path):
    process = FakeProcess(slow_terminate=True)
    session = desktop.StudioSession(root=tmp_path)
    session.process = process
    session.close()
    session.close()
    assert process.terminated and process.killed
    assert process.waits == [5, 5]


def test_window_uses_native_engine_and_persistent_profile(monkeypatch, tmp_path):
    created, started = [], []
    webview = SimpleNamespace(create_window=lambda *args, **kwargs: created.append((args, kwargs)),
                              start=lambda **kwargs: started.append(kwargs))
    monkeypatch.setattr(desktop, "load_webview", lambda: webview)
    monkeypatch.setattr(desktop, "ROOT", tmp_path)
    monkeypatch.setattr(desktop, "DATA_ROOT", tmp_path)
    process, _ = fake_start(monkeypatch, [True])
    desktop.run_desktop(8766, debug=True)
    assert created == [((f"Azurik Level Studio {desktop.VERSION}", "http://127.0.0.1:8766"),
                        {"width": 1600, "height": 1000, "min_size": (1100, 700),
                         "background_color": "#111821", "js_api": None})]
    assert started[0]["private_mode"] is False
    assert started[0]["storage_path"] == str(tmp_path / ".webview")
    assert started[0]["debug"] is True
    if desktop.os.name == "nt":
        assert started[0]["gui"] == "edgechromium"
    assert not process.terminated


def test_gui_failure_cleans_owned_server(monkeypatch, tmp_path):
    def failed_start(**kwargs):
        raise RuntimeError("engine missing")

    webview = SimpleNamespace(create_window=lambda *args, **kwargs: None, start=failed_start)
    monkeypatch.setattr(desktop, "load_webview", lambda: webview)
    monkeypatch.setattr(desktop, "ROOT", tmp_path)
    monkeypatch.setattr(desktop, "DATA_ROOT", tmp_path)
    process, _ = fake_start(monkeypatch, [False, True])
    with pytest.raises(desktop.DesktopError, match="WebView2 Runtime.*engine missing"):
        desktop.run_desktop()
    assert process.terminated


def test_missing_dependency_does_not_start_server(monkeypatch):
    def missing_module(_):
        raise ImportError("No module named webview")

    monkeypatch.setattr(desktop.importlib, "import_module", missing_module)
    monkeypatch.setattr(desktop, "StudioSession", lambda *args: pytest.fail("No server without window engine"))
    with pytest.raises(desktop.DesktopError, match="requirements-desktop.txt"):
        desktop.run_desktop()


@pytest.mark.parametrize("value", [0, -1, 65536, "no-port"])
def test_invalid_port_rejected(value):
    with pytest.raises(argparse.ArgumentTypeError):
        desktop.validate_port(value)


def fake_opener(monkeypatch, payload=None, error=None):
    requests = []

    def open_request(request, timeout):
        requests.append((request, timeout))
        if error is not None:
            raise error
        return io.BytesIO(payload)

    monkeypatch.setattr(desktop, "build_opener", lambda *handlers: SimpleNamespace(open=open_request))
    return requests


def test_health_probe_checks_identity_and_loopback(monkeypatch):
    requests = fake_opener(monkeypatch, json.dumps({"app": desktop.APP_ID, "version": desktop.VERSION}).encode())
    assert desktop.probe_health("http://127.0.0.1:8766", timeout=0.4)
    assert requests[0][0].full_url == "http://127.0.0.1:8766/api/health"
    assert requests[0][1] == 0.4


@pytest.mark.parametrize("version", ["1.0.0-old", None])
def test_health_probe_rejects_incompatible_studio_version(monkeypatch, version):
    fake_opener(monkeypatch, json.dumps({"app": desktop.APP_ID, "version": version}).encode())
    with pytest.raises(desktop.VersionMismatchError, match="version"):
        desktop.probe_health("http://127.0.0.1:8766")


def test_old_studio_stays_running_while_new_version_uses_another_port(monkeypatch, tmp_path):
    process, calls = fake_start(monkeypatch, [True])
    probes = []
    def probe(url, **kwargs):
        probes.append(url)
        if len(probes) == 1:
            raise desktop.VersionMismatchError("old version")
        return True
    monkeypatch.setattr(desktop, "probe_health", probe)
    monkeypatch.setattr(desktop.StudioSession, "_available_port", lambda _: 8976)
    with desktop.StudioSession(root=tmp_path) as session:
        assert session.port == 8976
        assert session.url == "http://127.0.0.1:8976"
    assert probes == ["http://127.0.0.1:8766", "http://127.0.0.1:8976"]
    assert calls[0][0][-2:] == ["--port", "8976"]
    assert process.terminated  # only the new child is owned by this session


@pytest.mark.parametrize("payload", [b"not json", b"{}", b"[]", b'{"app":"different-app"}', b"x" * (desktop.MAX_HEALTH_BYTES + 1)],
                         ids=["invalid-json", "missing-app", "array", "different-app", "too-large"])
def test_health_probe_rejects_foreign_or_invalid_service(monkeypatch, payload):
    fake_opener(monkeypatch, payload)
    with pytest.raises(desktop.DesktopError, match="autre service"):
        desktop.probe_health("http://127.0.0.1:8766")


def test_http_error_is_foreign_service(monkeypatch):
    fake_opener(monkeypatch, error=HTTPError("http://127.0.0.1:8766", 404, "not found", {}, None))
    with pytest.raises(desktop.DesktopError, match="autre service"):
        desktop.probe_health("http://127.0.0.1:8766")


def test_connection_refusal_is_missing_service(monkeypatch):
    fake_opener(monkeypatch, error=URLError(ConnectionRefusedError()))
    assert desktop.probe_health("http://127.0.0.1:8766") is False


def test_main_passes_cli_and_reports_failure(monkeypatch):
    calls, errors = [], []

    def run(*args):
        calls.append(args)
        raise desktop.DesktopError("clear failure")

    monkeypatch.setattr(desktop, "run_desktop", run)
    monkeypatch.setattr(desktop, "show_error", errors.append)
    assert desktop.main(["--port", "8778", "--source", "game dump", "--debug"]) == 1
    assert calls == [(8778, "game dump", True)]
    assert errors == ["clear failure"]


def test_frozen_server_uses_executable_dispatch_and_writable_data_root(monkeypatch, tmp_path):
    process, calls = fake_start(monkeypatch, [False, True])
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    bundle = tmp_path / "extraction"
    data = tmp_path / "user-data"
    bundle.mkdir()
    source = "game dump with spaces"
    with desktop.StudioSession(8779, source, root=bundle, data_root=data):
        pass
    command, options = calls[0]
    assert command == [desktop.sys.executable, "--server", "--port", "8779", "--source", source]
    assert options["cwd"] == str(bundle)
    assert (data / "logs" / "desktop-server.log").is_file()
    assert not (bundle / "logs").exists()
    assert process.terminated


def test_native_profile_uses_data_folder_not_bundled_assets(monkeypatch, tmp_path):
    started = []
    webview = SimpleNamespace(create_window=lambda *args, **kwargs: None,
                              start=lambda **kwargs: started.append(kwargs))
    monkeypatch.setattr(desktop, "load_webview", lambda: webview)
    monkeypatch.setattr(desktop, "ROOT", tmp_path / "extraction")
    monkeypatch.setattr(desktop, "DATA_ROOT", tmp_path / "persisted")
    fake_start(monkeypatch, [True])
    desktop.run_desktop()
    assert started[0]["storage_path"] == str(tmp_path / "persisted" / ".webview")
    assert not (tmp_path / "extraction").exists()


def test_native_window_uses_packaged_application_icon(monkeypatch, tmp_path):
    started = []
    icon = tmp_path / "assets" / "studio.ico"
    icon.parent.mkdir()
    icon.write_bytes(b"test icon")
    monkeypatch.setattr(desktop, "ROOT", tmp_path)
    monkeypatch.setattr(desktop, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(desktop, "load_webview", lambda: SimpleNamespace(
        create_window=lambda *args, **kwargs: None, start=lambda **kwargs: started.append(kwargs)))
    fake_start(monkeypatch, [True])
    desktop.run_desktop()
    assert started[0]["icon"] == str(icon)


def test_server_dispatch_restores_argv_and_does_not_load_webview(monkeypatch):
    calls = []
    before = desktop.sys.argv

    def fake_import(name):
        assert name == "server"
        return SimpleNamespace(main=lambda: calls.append(desktop.sys.argv[:]))

    monkeypatch.setattr(desktop.importlib, "import_module", fake_import)
    monkeypatch.setattr(desktop, "load_webview", lambda: pytest.fail("Server must not initialize GUI"))
    assert desktop.main(["--server", "--port", "8780", "--source", "local dump"]) == 0
    assert calls == [[str(desktop.ROOT / "server.py"), "--port", "8780", "--source", "local dump"]]
    assert desktop.sys.argv is before


def test_frozen_server_repairs_console_streams_and_logs_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    before_argv, before_out, before_err = desktop.sys.argv, desktop.sys.stdout, desktop.sys.stderr

    def fail():
        assert desktop.sys.stdout is not None
        assert desktop.sys.stderr is desktop.sys.stdout
        print("server diagnostic", file=desktop.sys.stderr)
        raise RuntimeError("backend startup failed")

    monkeypatch.setattr(desktop.importlib, "import_module", lambda name: SimpleNamespace(main=fail))
    assert desktop.main(["--server", "--port", "8781"]) == 1
    log = (tmp_path / "logs" / "frozen-server.log").read_text("utf-8")
    assert "server diagnostic" in log and "backend startup failed" in log
    assert desktop.sys.argv is before_argv
    assert desktop.sys.stdout is before_out and desktop.sys.stderr is before_err


def test_window_creation_failure_cleans_owned_server(monkeypatch, tmp_path):
    def failed_create(*args, **kwargs):
        raise RuntimeError("window creation failed")

    webview = SimpleNamespace(create_window=failed_create,
                              start=lambda **kwargs: pytest.fail("No loop after failed window"))
    monkeypatch.setattr(desktop, "load_webview", lambda: webview)
    monkeypatch.setattr(desktop, "DATA_ROOT", tmp_path)
    process, _ = fake_start(monkeypatch, [False, True])
    with pytest.raises(desktop.DesktopError, match="window creation failed"):
        desktop.run_desktop()
    assert process.terminated


def test_unexpected_startup_error_is_reported(monkeypatch):
    errors = []

    def fail(*args):
        raise RuntimeError("unexpected startup failure")

    monkeypatch.setattr(desktop, "run_desktop", fail)
    monkeypatch.setattr(desktop, "show_error", errors.append)
    assert desktop.main([]) == 1
    assert "unexpected startup failure" in errors[0]
