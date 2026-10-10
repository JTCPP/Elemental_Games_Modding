"""Record the distributed executable checksum without any user-specific paths."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import platform
from studio_version import VERSION

ROOT = Path(__file__).resolve().parent

def main():
    executable = ROOT / "windows" / "Azurik Level Studio.exe"
    with executable.open("rb") as stream:
        hasher = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
        digest = hasher.hexdigest()
    result = {"file": executable.name, "version": VERSION, "platform": "Windows x64",
              "bytes": executable.stat().st_size, "sha256": digest,
              "builtUtc": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
              "pywebview": importlib.metadata.version("pywebview"),
              "pyinstaller": importlib.metadata.version("pyinstaller"), "gameDataIncluded": False}
    (executable.parent / "build.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (executable.parent / "SHA256SUMS.txt").write_text(digest + "  " + executable.name + "\n", encoding="utf-8")
    print(json.dumps(result))

if __name__ == "__main__":
    main()
