"""Native desktop window for Azurik Level Studio, with an owned local server."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import importlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from studio_paths import ASSET_ROOT, DATA_ROOT
from studio_version import VERSION


ROOT = ASSET_ROOT
APP_ID = "azurik-level-studio"
MAX_HEALTH_BYTES = 64 * 1024
READY_TIMEOUT = 30.0


class DesktopError(RuntimeError):
    """An actionable startup failure that can be shown without a traceback."""


class VersionMismatchError(DesktopError):
    """The responding Studio belongs to a different installed version."""


def validate_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("Le port doit être un nombre entre 1 et 65535.") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Le port doit être compris entre 1 et 65535.")
    return port


def probe_health(url, *, timeout=1.0):
    """Return False for an unreachable server; reject a responding foreign app."""
    opener = build_opener(ProxyHandler({}))
    request = Request(url + "/api/health", headers={"Accept": "application/json"})
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = response.read(MAX_HEALTH_BYTES + 1)
    except HTTPError as exc:
        raise DesktopError("Le port est utilisé par un autre service. Choisis un autre port avec --port.") from exc
    except (URLError, TimeoutError, ConnectionError, OSError):
        return False
    try:
        if len(payload) > MAX_HEALTH_BYTES:
            raise ValueError("Oversized health response")
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get("app") != APP_ID:
            raise ValueError("Unrecognized service")
    except (UnicodeError, ValueError) as exc:
        raise DesktopError("Le port est utilisé par un autre service. Choisis un autre port avec --port.") from exc
    if data.get("version") != VERSION:
        raise VersionMismatchError(
            f"L’éditeur déjà ouvert utilise la version {data.get('version', 'inconnue')}, "
            f"alors que cette fenêtre utilise la version {VERSION}.")
    return True


def port_is_occupied(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


class StudioSession:
    """Reuse an existing studio; otherwise stop only the server we launch."""

    def __init__(self, port=8766, source=None, *, root=None, data_root=None, ready_timeout=READY_TIMEOUT):
        self.port = validate_port(port)
        self.source = source
        self.root = Path(ROOT if root is None else root)
        self.data_root = Path((DATA_ROOT if root is None else root) if data_root is None else data_root)
        self.ready_timeout = ready_timeout
        self.url = f"http://127.0.0.1:{self.port}"
        self.process = None

    def start(self):
        try:
            if probe_health(self.url):
                return self.url
        except VersionMismatchError:
            # Leave an older editor and its unsaved project alone. A new window
            # must always use its bundled server rather than silently showing V1.
            self.port = self._available_port()
            self.url = f"http://127.0.0.1:{self.port}"
        if port_is_occupied(self.port):
            raise DesktopError(f"Le port {self.port} est déjà occupé et l’éditeur ne répond pas. Choisis un autre port avec --port.")
        log_dir = self.data_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "--server" if getattr(sys, "frozen", False) else str(self.root / "server.py"),
                   "--port", str(self.port)]
        if self.source is not None:
            command.extend(["--source", os.fspath(self.source)])
        options = {"cwd": str(self.root), "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        error_log = log_dir / "desktop-server-error.log"
        try:
            with (log_dir / "desktop-server.log").open("ab") as output, error_log.open("ab") as errors:
                self.process = subprocess.Popen(command, stdout=output, stderr=errors, **options)
        except OSError as exc:
            raise DesktopError(f"Impossible de démarrer le serveur local : {exc}") from exc
        deadline = time.monotonic() + self.ready_timeout
        while True:
            code = self.process.poll()
            if code is not None:
                raise DesktopError(f"Le serveur local s’est arrêté (code {code}). Consulte {error_log} et, pour l’exécutable, {log_dir / 'frozen-server.log'}.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DesktopError(f"Le serveur local n’a pas répondu à temps. Consulte {error_log}.")
            if probe_health(self.url, timeout=min(1.0, remaining)):
                return self.url
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    def _available_port(self):
        # Ask the OS for a free loopback port; Popen readiness still detects a
        # competing process which happens to claim it before our server binds.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]

    def close(self):
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def __enter__(self):
        try:
            self.start()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_):
        self.close()


def load_webview():
    try:
        return importlib.import_module("webview")
    except ImportError as exc:
        raise DesktopError("Le moteur de fenêtre est absent. Lance Launch.ps1 pour installer les dépendances, ou installe requirements-desktop.txt dans ton environnement Python.") from exc


def run_desktop(port=8766, source=None, debug=False):
    webview = load_webview()
    storage = DATA_ROOT / ".webview"
    storage.mkdir(parents=True, exist_ok=True)
    with StudioSession(port, source) as session:
        options = {"private_mode": False, "storage_path": str(storage), "debug": debug}
        icon = ROOT / "assets" / "studio.ico"
        if icon.is_file():
            options["icon"] = str(icon)
        if os.name == "nt":
            options["gui"] = "edgechromium"
        try:
            webview.create_window(f"Azurik Level Studio {VERSION}", session.url,
                                  width=1600, height=1000, min_size=(1100, 700),
                                  background_color="#111821", js_api=None)
            webview.start(**options)
        except Exception as exc:
            raise DesktopError("Impossible d’ouvrir la fenêtre. Sur Windows, vérifie que Microsoft Edge WebView2 Runtime est installé. " + str(exc)) from exc


def show_error(message):
    try:
        log_dir = DATA_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "desktop-error.log").open("a", encoding="utf-8") as stream:
            stream.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
            if sys.exc_info()[0] is not None:
                traceback.print_exc(file=stream)
    except OSError:
        pass
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    if os.name != "nt":
        return
    try:
        from tkinter import Tk, messagebox
        window = Tk()
        window.withdraw()
        try:
            messagebox.showerror("Azurik Level Studio", message, parent=window)
        finally:
            window.destroy()
    except Exception:
        pass


def run_server(argv):
    """Dispatch an executable's child server without initializing a desktop UI."""
    original_argv = sys.argv
    original_stdout, original_stderr = sys.stdout, sys.stderr
    with ExitStack() as stack:
        try:
            # A windowed executable has no console streams even when spawned
            # with redirected handles. The HTTP server requires real streams.
            if getattr(sys, "frozen", False) or sys.stdout is None or sys.stderr is None:
                log_dir = DATA_ROOT / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                stream = stack.enter_context((log_dir / "frozen-server.log").open("a", encoding="utf-8", buffering=1))
                sys.stdout = sys.stderr = stream
            sys.argv = [str(ROOT / "server.py"), *argv]
            server = importlib.import_module("server")
            server.main()
            return 0
        except Exception:
            if sys.stderr is not None:
                traceback.print_exc(file=sys.stderr)
            return 1
        finally:
            sys.argv = original_argv
            sys.stdout, sys.stderr = original_stdout, original_stderr


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--server":
        return run_server(argv[1:])
    parser = argparse.ArgumentParser(description="Azurik Level Studio — fenêtre de bureau")
    parser.add_argument("--port", type=validate_port, default=8766)
    parser.add_argument("--source", help="Dump du jeu utilisé uniquement si un serveur doit être démarré.")
    parser.add_argument("--debug", action="store_true", help="Activer les outils de diagnostic du moteur WebView2.")
    options = parser.parse_args(argv)
    try:
        run_desktop(options.port, options.source, options.debug)
    except (DesktopError, OSError) as exc:
        show_error(str(exc))
        return 1
    except Exception as exc:
        show_error("Impossible de démarrer Azurik Level Studio : " + str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
