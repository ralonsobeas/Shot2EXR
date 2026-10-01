"""Native dependency compatibility checks (``shot2exr --check-environment``, GUI Help menu).

Each check exercises the dependency the way the engine uses it (an EXR round trip through
OpenImageIO, an OCIO processor, a real FFmpeg decode to 16-bit RGB, loading the Qt platform
plugin), so a broken install is reported up front with a readable reason instead of failing
half-way through a conversion.
"""

from __future__ import annotations

import ctypes
import os
import platform
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from shot2exr import TOOL_NAME, __version__, config
from shot2exr.errors import Shot2EXRError

OK, WARN, FAIL = "ok", "warning", "failed"
MIN_FFMPEG = (5, 1)  # -fps_mode


@dataclass
class Check:
    name: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess:
    if os.name == "nt":
        kw.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return subprocess.run(cmd, capture_output=True, timeout=60, check=False, **kw)


def os_release() -> str:
    """'Rocky Linux 9.6 (Blue Onyx)' / 'Windows 10 (10.0.19045)' / platform string."""
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/etc/os-release").read_text().splitlines():
                if line.startswith("PRETTY_NAME="):
                    name = line.split("=", 1)[1].strip('"')
                    libc = " ".join(platform.libc_ver())
                    return f"{name}, {libc}".strip(", ")
        except OSError:
            pass
    if os.name == "nt":
        return f"Windows {platform.release()} ({platform.version()})"
    return platform.platform()


def check_platform() -> Check:
    name = os_release()
    frozen = " (standalone bundle)" if getattr(sys, "frozen", False) else ""
    supported = "Rocky Linux 9" in name or os.name == "nt" and platform.release() in ("10", "11")
    return Check("Platform", OK if supported else WARN,
                 name + frozen + ("" if supported else " - not a target platform (Rocky Linux 9, Windows 10/11)"))


def check_python() -> Check:
    ok = sys.version_info >= (3, 11)
    return Check("Python", OK if ok else FAIL, f"{platform.python_version()} ({sys.executable})"
                 + ("" if ok else " - 3.11 or newer is required"))


def check_numpy() -> Check:
    try:
        import numpy
    except ImportError as exc:
        return Check("NumPy", FAIL, f"not importable: {exc}")
    return Check("NumPy", OK, numpy.__version__)


def check_openimageio() -> Check:
    try:
        import numpy as np
        import OpenImageIO as oiio
    except ImportError as exc:
        return Check("OpenImageIO", FAIL, f"not importable: {exc}")
    version = getattr(oiio, "__version__", "?")
    try:
        from shot2exr.exr_reader import read_frame, read_header
        from shot2exr.exr_writer import write_frame

        with tempfile.TemporaryDirectory(prefix="shot2exr-check-") as tmp:
            path = Path(tmp) / "check.0001.exr"
            px = np.linspace(-0.5, 4.0, 4 * 2 * 3, dtype=np.float32).reshape(2, 4, 3)
            write_frame(path, px, ["R", "G", "B"], {"colorInteropID": "lin_ap1_scene"})
            hdr = read_header(path)
            back = read_frame(path).pixels
            if hdr.compression != "zip" or hdr.attributes.get("colorInteropID") != "lin_ap1_scene":
                return Check("OpenImageIO", FAIL, f"{version}: EXR metadata did not round-trip "
                             f"(compression={hdr.compression})")
            if float(abs(back - px).max()) > 4.0 / 1024:
                return Check("OpenImageIO", FAIL, f"{version}: half-float EXR pixels did not round-trip")
    except Exception as exc:  # noqa: BLE001 - report, don't raise
        return Check("OpenImageIO", FAIL, f"{version}: EXR write/read failed: {exc}")
    return Check("OpenImageIO", OK, f"{version}, half-float ZIP EXR write/read OK")


def check_opencolorio(ocio_config: str | None = None) -> Check:
    try:
        from shot2exr.color_manager import load_config, ocio_version

        cfg = load_config(ocio_config)
        out = cfg.require(config.DEFAULT_OUTPUT_COLORSPACE, "Default output")
        linear = cfg.scene_linear()
        if linear is None:
            return Check("OpenColorIO", WARN, f"{ocio_version()}, config {cfg.source}: no scene_linear role "
                         "(resizing then happens in the input colour space)")
        cfg.processor("sRGB Encoded Rec.709 (sRGB)" if cfg.find("sRGB Encoded Rec.709 (sRGB)") else linear, out)
    except Shot2EXRError as exc:
        return Check("OpenColorIO", FAIL, exc.message)
    except ImportError as exc:
        return Check("OpenColorIO", FAIL, f"not importable: {exc}")
    except Exception as exc:  # noqa: BLE001
        return Check("OpenColorIO", FAIL, f"processor creation failed: {exc}")
    return Check("OpenColorIO", OK, f"{ocio_version()}, config {cfg.source} ({cfg.origin}), "
                 f"default output '{out}' available")


def _version_tuple(text: str) -> tuple[int, ...] | None:
    m = re.search(r"version n?(\d+)\.(\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _version_line(exe: Path) -> str:
    """First line of ``exe -version``; RuntimeError with stderr when it cannot start (e.g. a missing library)."""
    proc = _run([str(exe), "-hide_banner", "-version"], text=True)
    lines = proc.stdout.splitlines()
    if proc.returncode or not lines:
        raise RuntimeError((proc.stderr or "").strip()[:300] or f"exit code {proc.returncode}")
    return lines[0].split(" Copyright")[0]


def check_ffmpeg(ffmpeg_path: str | None = None) -> Check:
    from shot2exr.media_probe import resolve_executable
    from shot2exr.video_reader import SWS_FLAGS

    try:
        exe = resolve_executable("ffmpeg", ffmpeg_path)
    except Shot2EXRError as exc:
        return Check("FFmpeg", FAIL, exc.message)
    try:
        head = _version_line(exe)
        version = _version_tuple(head)
        if version and version < MIN_FFMPEG:
            return Check("FFmpeg", FAIL, f"{head} at {exe}: {'.'.join(map(str, MIN_FFMPEG))} or newer is required")
        # Decode two synthetic BT.709 limited-range frames exactly the way the engine does.
        w, h, n = 64, 36, 2
        vf = f"scale=in_color_matrix=bt709:in_range=tv:out_range=pc:flags={SWS_FLAGS}"
        proc = _run([str(exe), "-nostdin", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=24",
                     "-frames:v", str(n), "-pix_fmt", "yuv420p", "-f", "nut", "-c:v", "rawvideo", "-"])
        if proc.returncode:
            return Check("FFmpeg", WARN, f"{head} at {exe}; self-test source unavailable "
                         f"({proc.stderr.decode(errors='replace').strip()[:200]})")
        dec = _run([str(exe), "-nostdin", "-v", "error", "-f", "nut", "-i", "-", "-fps_mode", "passthrough",
                    "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb48le", "-"], input=proc.stdout)
        expected = w * h * 3 * 2 * n
        if dec.returncode or len(dec.stdout) != expected:
            err = dec.stderr.decode(errors="replace").strip()[:300]
            return Check("FFmpeg", FAIL, f"{head} at {exe}: 16-bit RGB decode test failed ({err or len(dec.stdout)})")
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return Check("FFmpeg", FAIL, f"{exe} could not run: {exc}")
    return Check("FFmpeg", OK, f"{head.replace('ffmpeg version ', '')} at {exe}, 16-bit RGB decode OK")


def check_ffprobe(ffprobe_path: str | None = None) -> Check:
    from shot2exr.media_probe import resolve_executable

    try:
        exe = resolve_executable("ffprobe", ffprobe_path)
        head = _version_line(exe)
    except Shot2EXRError as exc:
        return Check("FFprobe", FAIL, exc.message)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return Check("FFprobe", FAIL, f"could not run: {exc}")
    return Check("FFprobe", OK, f"{head.replace('ffprobe version ', '')} at {exe}")


def _platform_plugin(plugins: Path) -> Path | None:
    names = {"win32": "qwindows.dll", "darwin": "libqcocoa.dylib"}
    plugin = plugins / "platforms" / names.get(sys.platform, "libqxcb.so")
    return plugin if plugin.is_file() else None


LINUX_GUI_PACKAGES = ("libglvnd-opengl libglvnd-egl libglvnd-glx mesa-libGL mesa-libEGL fontconfig libxcb "
                      "libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-keysyms xcb-util-renderutil xcb-util-image "
                      "libwayland-client libwayland-cursor libwayland-egl")


def missing_library_hint() -> str:
    if sys.platform.startswith("linux"):
        return f"On Rocky Linux 9 install the desktop libraries: sudo dnf install {LINUX_GUI_PACKAGES}"
    if os.name == "nt":
        return "Reinstall Shot2EXR or the Microsoft Visual C++ Redistributable (x64)."
    return ""


def check_qt() -> Check:
    try:
        import PySide6
        from PySide6.QtCore import QLibraryInfo, qVersion
    except ImportError as exc:
        return Check("PySide6 / Qt", FAIL, f"not importable: {exc}")
    try:
        import PySide6.QtWidgets  # noqa: F401 - QtGui needs the system OpenGL/EGL libraries
    except ImportError as exc:
        return Check("PySide6 / Qt", FAIL, f"PySide6 {PySide6.__version__}: Qt GUI libraries cannot load: {exc}. "
                     f"{missing_library_hint()}")
    plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    head = f"PySide6 {PySide6.__version__}, Qt {qVersion()}"
    plugin = _platform_plugin(plugins)
    if plugin is None:
        return Check("PySide6 / Qt", FAIL, f"{head}: desktop platform plugin missing under {plugins / 'platforms'}")
    try:  # loading the plugin library reports missing system libraries (e.g. libxcb-cursor on Linux)
        ctypes.CDLL(str(plugin))
    except OSError as exc:
        return Check("PySide6 / Qt", FAIL, f"{head}: {plugin.name} cannot load: {exc}. {missing_library_hint()}")
    detail = f"{head}, {plugin.name} loads"
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return Check("PySide6 / Qt", WARN, detail + "; no DISPLAY/WAYLAND_DISPLAY, so the GUI needs a desktop session")
    return Check("PySide6 / Qt", OK, detail)


def check_settings(settings_path: str | None = None) -> Check:
    from shot2exr.settings import current_platform, load_settings

    try:
        settings = load_settings(settings_path)
    except Shot2EXRError as exc:
        return Check("Settings", FAIL, exc.message)
    root = settings.projects_root()
    where = f"user file {settings.user_file}"
    if not root:
        return Check("Settings", WARN, f"no projects_root for {current_platform()} ({where}); "
                     "set it, or pass --projects-root / --output-dir")
    if not Path(root).is_dir():
        return Check("Settings", WARN, f"projects_root {root} is not reachable (not mounted?) ({where})")
    return Check("Settings", OK, f"projects_root {root} ({where})")


def run_checks(*, ocio_config: str | None = None, ffmpeg_path: str | None = None,
               ffprobe_path: str | None = None, settings_path: str | None = None, include_gui: bool = True) -> list[Check]:
    checks = [check_platform(), check_python(), check_numpy(), check_openimageio(), check_opencolorio(ocio_config),
              check_ffmpeg(ffmpeg_path), check_ffprobe(ffprobe_path)]
    if include_gui:
        checks.append(check_qt())
    checks.append(check_settings(settings_path))
    return checks


def summary(checks: list[Check]) -> dict[str, Any]:
    failed = [c.name for c in checks if c.status == FAIL]
    return {"tool": f"{TOOL_NAME} {__version__}", "ok": not failed, "failed": failed,
            "checks": [c.to_dict() for c in checks]}


def format_checks(checks: list[Check]) -> str:
    marks = {OK: "OK  ", WARN: "WARN", FAIL: "FAIL"}
    width = max(len(c.name) for c in checks)
    lines = [f"{TOOL_NAME} {__version__} environment check"]
    lines += [f"  [{marks[c.status]}] {c.name:<{width}}  {c.detail}" for c in checks]
    failed = [c.name for c in checks if c.status == FAIL]
    lines.append("RESULT: " + ("all required dependencies work." if not failed else f"FAILED: {', '.join(failed)}"))
    return "\n".join(lines)
