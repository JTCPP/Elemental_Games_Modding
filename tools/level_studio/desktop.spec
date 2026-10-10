# Source-only bundle: include application resources and dependencies, never game data.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH)
data = [(str(root / "web"), "web"), (str(root / "assets"), "assets")]
data += collect_data_files("webview", subdir="js")
for package in ["pywebview", "pythonnet", "clr_loader", "bottle", "proxy_tools", "Pillow", "cffi"]:
    data += copy_metadata(package)
analysis = Analysis(
    [str(root / "desktop.py")], pathex=[str(root)], binaries=[], datas=data,
    hiddenimports=["server", "editor_backend", "studio_paths", "xbox_iso",
                   "renderer_parser", "scene_graph", "static_scene", "environment_scene",
                   "platform_render", "retail_lighting", "character_library", "library_bindings",
                   "asset_library", "asset_export", "config_library", "webview.platforms.winforms",
                   "webview.platforms.edgechromium", "clr", "pythonnet", "clr_loader"],
    hookspath=[], runtime_hooks=[],
    excludes=["pytest", "numpy", "scipy", "pandas", "matplotlib", "IPython",
              "webview.platforms.qt", "webview.platforms.gtk", "webview.platforms.cocoa",
              "webview.platforms.android", "webview.platforms.cef"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
exe = EXE(archive, analysis.scripts, analysis.binaries, analysis.datas, [],
          name="Azurik Level Studio", debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False,
          icon=str(root / "assets" / "studio.ico"),
          version=str(root / "assets" / "windows-version.txt"))
