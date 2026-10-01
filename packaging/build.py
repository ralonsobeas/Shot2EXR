"""Build the standalone Shot2EXR bundle for the current platform (run inside the activated conda env).

    python packaging/build.py            # -> dist/Shot2EXR/ and dist/Shot2EXR-<version>-<platform>.<zip|tar.gz>
    python packaging/build.py --test     # also run packaging/smoke_test.py against the result

Build on each target separately: Windows bundles on Windows, the Linux bundle on Rocky Linux 9
(or an older glibc), never an Ubuntu build shipped untested to Rocky.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from shot2exr import __version__  # noqa: E402


def platform_tag() -> str:
    if os.name == "nt":
        return "windows-x64"
    if sys.platform.startswith("linux"):
        return f"linux-{platform.machine()}"
    return sys.platform


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", type=Path, default=ROOT / "dist")
    ap.add_argument("--work", type=Path, default=ROOT / "build" / "pyinstaller")
    ap.add_argument("--test", action="store_true", help="Run the distribution smoke test after building.")
    ap.add_argument("--gui", default=None, help="GUI mode for the smoke test (offscreen, xcb, windows, skip).")
    a = ap.parse_args()

    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                    "--distpath", str(a.dist), "--workpath", str(a.work), str(ROOT / "packaging" / "shot2exr.spec")],
                   check=True)
    bundle = a.dist / "Shot2EXR"
    cli = bundle / ("shot2exr-cli.exe" if os.name == "nt" else "shot2exr-cli")
    info = subprocess.run([str(cli), "--env-info"], capture_output=True, text=True, check=True).stdout
    (bundle / "BUILD_INFO.txt").write_text(
        f"Shot2EXR {__version__} standalone bundle\nBuilt on: {platform.platform()} "
        f"{' '.join(platform.libc_ver())}\n\n{info}", encoding="utf-8")
    shutil.copy2(ROOT / "INSTALL.md", bundle / "INSTALL.md")

    name = f"Shot2EXR-{__version__}-{platform_tag()}"
    fmt = "zip" if os.name == "nt" else "gztar"
    archive = shutil.make_archive(str(a.dist / name), fmt, root_dir=a.dist, base_dir="Shot2EXR")
    size = sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file())
    print(f"Bundle: {bundle} ({size / 2**20:.0f} MB)\nArchive: {archive} ({Path(archive).stat().st_size / 2**20:.0f} MB)")

    if a.test:
        cmd = [sys.executable, str(ROOT / "packaging" / "smoke_test.py"), str(bundle)]
        if a.gui:
            cmd += ["--gui", a.gui]
        return subprocess.run(cmd).returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
