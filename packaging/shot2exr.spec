# PyInstaller spec: one folder holding the windowed GUI (Shot2EXR) and the console CLI (shot2exr-cli),
# sharing one copy of Python, Qt, OpenImageIO, OpenColorIO and FFmpeg. Build it with
# `python packaging/build.py` from the activated conda env; that script also archives and tests it.
# The OCIO studio config is compiled into OpenColorIO, so no config files need to be shipped.

import os
import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

HERE = Path(SPECPATH)
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
from shot2exr import __version__  # noqa: E402

def _tool(name):
    """ffmpeg/ffprobe from the build env (conda puts them on PATH); never a system copy."""
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"{name} not found on PATH; build from the activated shot2exr conda env.")
    prefix = Path(sys.prefix).resolve()
    if prefix not in Path(found).resolve().parents:
        raise SystemExit(f"{found} is not from the build env ({prefix}); refusing to bundle a system FFmpeg.")
    return found

# FFmpeg executables go to the bundle root next to the shared libraries they need; the engine looks
# there first when frozen (media_probe.bundled_executable).
ffmpeg_bins = [(_tool("ffmpeg"), "."), (_tool("ffprobe"), ".")]
datas = collect_data_files("shot2exr")  # default_settings.toml
binaries = ffmpeg_bins + collect_dynamic_libs("PyOpenColorIO") + collect_dynamic_libs("OpenImageIO")

# Unused heavy modules: keep the bundle to QtCore/QtGui/QtWidgets.
excludes = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick",
            "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
            "PySide6.QtPdf", "PySide6.QtNetwork", "PySide6.QtSql", "PySide6.QtTest", "matplotlib", "IPython", "pytest"]

def analysis(script):
    return Analysis([str(HERE / script)], pathex=[str(ROOT / "src")], binaries=binaries, datas=datas,
                    hiddenimports=["shot2exr.diagnostics"], excludes=excludes, noarchive=False)

gui_a, cli_a = analysis("launch_gui.py"), analysis("launch_cli.py")
gui_exe = EXE(PYZ(gui_a.pure), gui_a.scripts, [], exclude_binaries=True, name="Shot2EXR",
              console=False, disable_windowed_traceback=False, upx=False)
cli_exe = EXE(PYZ(cli_a.pure), cli_a.scripts, [], exclude_binaries=True, name="shot2exr-cli",
              console=True, upx=False)
COLLECT(gui_exe, gui_a.binaries, gui_a.datas, cli_exe, cli_a.binaries, cli_a.datas,
        strip=False, upx=False, name="Shot2EXR")
