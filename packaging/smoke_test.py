"""Distribution test for a built Shot2EXR bundle. Standard library only (runs on Rocky 9's Python 3.9 or
any Windows Python), so it can run on a clean machine with no conda env.

    python packaging/smoke_test.py dist/Shot2EXR [--gui offscreen|xcb|windows|skip]

Steps: version, --check-environment (every core dependency must pass), a synthetic ProRes MOV made
with the bundled FFmpeg, MOV -> EXR, that EXR sequence -> EXR (resized), error exit codes, and the
GUI self-test. Exits non-zero on the first failure.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

EXE = ".exe" if os.name == "nt" else ""


def step(title: str) -> None:
    print(f"\n== {title}", flush=True)


def run(cmd, expect=0, env=None, timeout=600):
    print("$ " + " ".join(str(c) for c in cmd), flush=True)
    res = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout,
                         env={**os.environ, **(env or {})})
    out = (res.stdout + res.stderr).strip()
    print(out[-3000:])
    if res.returncode != expect:
        sys.exit(f"FAILED: exit code {res.returncode}, expected {expect}")
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--gui", default="windows" if os.name == "nt" else "offscreen",
                    choices=["offscreen", "xcb", "windows", "skip"])
    a = ap.parse_args()
    bundle = a.bundle.resolve()
    cli, gui = bundle / f"shot2exr-cli{EXE}", bundle / f"Shot2EXR{EXE}"
    internal = bundle / "_internal"
    ffmpeg = internal / f"ffmpeg{EXE}"
    for path in (cli, gui, ffmpeg):
        if not path.is_file():
            sys.exit(f"FAILED: {path} is missing from the bundle")

    step("version")
    run([cli, "--version"])

    step("environment check")
    # Exit code ignored here: Qt may legitimately fail on a headless server; the core list below decides.
    res = subprocess.run([str(cli), "--check-environment", "--json"], capture_output=True, text=True, timeout=300)
    report = json.loads(res.stdout)
    for check in report["checks"]:
        print(f"  [{check['status']:<7}] {check['name']:<14} {check['detail']}")
    core = {c["name"]: c["status"] for c in report["checks"]}
    bad = [n for n in ("Python", "NumPy", "OpenImageIO", "OpenColorIO", "FFmpeg", "FFprobe") if core.get(n) != "ok"]
    if bad:
        sys.exit(f"FAILED: core dependencies not working in the bundle: {bad}")
    if not core.get("FFmpeg") or str(internal) not in next(c["detail"] for c in report["checks"] if c["name"] == "FFmpeg"):
        sys.exit("FAILED: the bundle is not using its own FFmpeg")

    with tempfile.TemporaryDirectory(prefix="shot2exr-dist-") as tmp:
        tmp = Path(tmp)
        settings = tmp / "settings.toml"
        root = tmp / "Projects"
        root.mkdir()
        root_toml = root.as_posix()
        settings.write_text(f'[paths.linux]\nprojects_root = "{root_toml}"\n[paths.windows]\nprojects_root = "{root_toml}"\n'
                            '[projects]\nGOD = "GodOfTides"\n[tasks]\nml = "MachineLearning"\n', encoding="utf-8")
        env = {"SHOT2EXR_SETTINGS": str(settings)}

        step("make a ProRes 422 HQ test MOV with the bundled FFmpeg")
        mov = tmp / "plate v01.mov"
        ff_env = {"LD_LIBRARY_PATH": str(internal)} if sys.platform.startswith("linux") else {}
        run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24", "-frames:v", "24",
             "-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
             "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
             "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv", mov],
            env=ff_env)

        shot = ["--project", "GOD", "--shot", "0046_005", "--task", "ml", "--element", "water"]
        step("MOV -> EXR (automatic project directory)")
        res = run([cli, "-i", mov, *shot, "--version", "001", "--start-frame", "1001", "--resolution", "320x180",
                   "--accept-inferred-colorspace", "--json"], env=env)
        result = json.loads(res.stdout)["result"]
        v1 = Path(result["output_directory"])
        frames = sorted(v1.glob("GOD_0046_005_ml_v001.*.exr"))
        expected_dir = root / "GodOfTides/VFX/GOD_0046/GOD_0046_005/Tasks/MachineLearning/ComfyUI/water/GOD_0046_005_ml_v001"
        if v1.resolve() != expected_dir.resolve() or len(frames) != 24:
            sys.exit(f"FAILED: expected 24 frames in {expected_dir}, got {len(frames)} in {v1}")
        rep = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
        if rep["input"]["frames_decoded"] != 24 or rep["general"]["status"] != "success":
            sys.exit("FAILED: video report is inconsistent")

        step("EXR sequence -> EXR (FILL resize to 256x256, ACEScg -> ACES2065-1)")
        res = run([cli, "-i", frames[0], *shot, "--version", "002", "--start-frame", "1009", "--resolution", "256x256",
                   "--resize-mode", "fill", "--output-colorspace", "ACES2065-1", "--json"], env=env)
        result = json.loads(res.stdout)["result"]
        if result["frames_written"] != 24:
            sys.exit("FAILED: EXR -> EXR did not write 24 frames")

        step("error handling")
        run([cli, "-i", mov, *shot, "--version", "001", "--start-frame", "1001", "--resolution", "320x180",
             "--accept-inferred-colorspace"], expect=5, env=env)  # version exists
        run([cli, "-i", tmp / "missing.mov", *shot, "--version", "009", "--start-frame", "1001",
             "--resolution", "320x180"], expect=3, env=env)

    if a.gui != "skip":
        step(f"GUI self-test ({a.gui})")
        gui_env = {} if a.gui == "windows" else {"QT_QPA_PLATFORM": a.gui}
        cmd = [gui, "--self-test"]
        if a.gui == "xcb" and not os.environ.get("DISPLAY"):
            cmd = ["xvfb-run", "-a", *cmd]
        run(cmd, env=gui_env, timeout=180)

    print("\nDISTRIBUTION SMOKE TEST PASSED", flush=True)


if __name__ == "__main__":
    main()
