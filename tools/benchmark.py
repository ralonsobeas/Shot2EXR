"""Throughput and peak-memory benchmark for the conversion engine (not part of the test suite).

    python tools/benchmark.py --frames 48 --size 1920x1080 --resolution 2048x1152

Generates a synthetic EXR sequence and an H.264 video in a temporary directory, converts both,
and prints seconds per frame plus the peak resident memory of this process.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import OpenImageIO as oiio

from shot2exr.color_manager import load_config
from shot2exr.converter import plan_conversion, run_conversion
from shot2exr.models import ConversionRequest, Resolution


def peak_rss_mb() -> float | None:
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KiB on Linux
    except ImportError:  # Windows
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("a", ctypes.c_size_t), ("b", ctypes.c_size_t), ("c", ctypes.c_size_t),
                            ("d", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
            return pmc.PeakWorkingSetSize / 2**20
        except Exception:  # noqa: BLE001
            return None


def make_sequence(directory: Path, frames: int, w: int, h: int) -> Path:
    directory.mkdir(parents=True)
    rng = np.random.default_rng(1)
    base = rng.random((h, w, 3), dtype=np.float32)
    for f in range(frames):
        spec = oiio.ImageSpec(w, h, 3, oiio.HALF)
        spec.attribute("compression", "zip")
        spec.attribute("colorInteropID", "lin_ap0_scene")
        out = oiio.ImageOutput.create("exr")
        out.open(str(directory / f"plate.{1001 + f:04d}.exr"), spec)
        out.write_image(np.roll(base, f, axis=1))
        out.close()
    return directory


def make_video(path: Path, frames: int, w: int, h: int) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=24",
                    "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
                    "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
                    str(path)], check=True)
    return path


def run(label: str, src: Path, out: Path, res: Resolution, frames: int) -> None:
    cfg = load_config(None)
    req = ConversionRequest(input_path=src, project="BEN", shot="010_0010", task="comp", element="bench",
                            version="001", start_frame=1001, output_resolution=res, output_directory=out,
                            accept_inferred_colorspace=True)
    plan = plan_conversion(req, cfg)
    if not plan.ok:
        sys.exit(f"{label}: plan failed: {[e.message for e in plan.errors]}")
    t0 = time.perf_counter()
    result = run_conversion(plan, cfg)
    dt = time.perf_counter() - t0
    print(f"{label:<6} {result.status:<8} {frames} frames  {dt:7.2f} s  {dt / frames:6.3f} s/frame  "
          f"peak RSS {peak_rss_mb() or 0:7.0f} MB")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=48)
    ap.add_argument("--size", default="1920x1080", help="Source WIDTHxHEIGHT.")
    ap.add_argument("--resolution", default="2048x1152", help="Output WIDTHxHEIGHT.")
    ap.add_argument("--only", choices=["exr", "video"])
    a = ap.parse_args()
    w, h = map(int, a.size.lower().split("x"))
    ow, oh = map(int, a.resolution.lower().split("x"))
    tmp = Path(tempfile.mkdtemp(prefix="shot2exr-bench-"))
    try:
        if a.only in (None, "exr"):
            run("exr", make_sequence(tmp / "seq", a.frames, w, h), tmp / "out_exr", Resolution(ow, oh), a.frames)
        if a.only in (None, "video") and shutil.which("ffmpeg"):
            run("video", make_video(tmp / "clip.mp4", a.frames, w, h), tmp / "out_video", Resolution(ow, oh), a.frames)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
