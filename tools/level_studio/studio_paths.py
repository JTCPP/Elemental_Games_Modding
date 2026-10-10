"""Separate packaged read-only resources from persistent user data."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def studio_roots():
    if getattr(sys, "frozen", False):
        assets = Path(sys._MEIPASS).resolve()
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return assets, (local / "AzurikLevelStudio").resolve()
    root = Path(__file__).resolve().parent
    return root, root


ASSET_ROOT, DATA_ROOT = studio_roots()
