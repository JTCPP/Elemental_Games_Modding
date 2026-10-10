# Windows executable and build

The Windows x64 download contains **Azurik Level Studio.exe**, a PyInstaller one-file, windowed application with Python, Pillow, pywebview and dependencies. Microsoft Edge WebView2 Runtime and .NET Framework are prerequisites. It opens a desktop window without an external browser or console.

The executable is unsigned. Its SHA-256 is in `windows/build.json` and `windows/SHA256SUMS.txt`; the release ZIP includes these files. Verify downloads against the checksum. Security software can quarantine unsigned applications; this project does not alter antivirus settings. Verify provenance before restoring it, or rebuild from source.

## Launch and data

In the repository: `windows/Azurik Level Studio.exe`, **Ouvrir Azurik Level Studio.bat**, **Launch Studio.cmd**, or **Launch.ps1**. The release ZIP places the executable at its root with a matching `.bat`.

Optional arguments: `--source "C:\Games\Azurik dump"`, `--port 8770`, `--debug`. Diagnostics are off by default. An existing server is reused only when its version matches; an older editor is left running and a new free local port is selected. An unrelated service produces a conflict message. Only a server started by this launcher is closed with the window.

Frozen builds read bundled files from PyInstaller's extraction directory and write data under `%LOCALAPPDATA%\AzurikLevelStudio`. Logs include `logs/desktop-error.log` and `logs/frozen-server.log`. Source runs keep data beside the code. Imports and texture caches need additional disk space. Version 2.0.0 uses the owner's Perathia Modding Hub artwork for the executable and native window icon. Existing projects are compatible. Projects using level management require 2.1.0 or later; do not edit the same project with an older editor window.

## Rebuild

On Windows x64 with Python 3.11+ available through `py -3`:

```powershell
& '.\Build Windows.ps1'
```

The script creates `.venv-desktop`, installs `requirements-build.txt`, compiles `desktop.spec`, copies the binary into `windows/` and records its checksum. Only application resources and dependencies are bundled, excluding game data and user projects. UPX is not used.

Manual build:

```powershell
py -3 -m venv .venv-desktop
.\.venv-desktop\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv-desktop\Scripts\python.exe -m PyInstaller --noconfirm desktop.spec
```

Source development uses `requirements-desktop.txt` and `desktop.py`. Run `python -m pytest -q`; Node.js is required for frontend tests only.
