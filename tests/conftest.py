"""Shared fixtures: synthetic EXR sequences (OIIO) and videos (FFmpeg), generated on the fly."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Never read or write the real per-user settings file during tests."""
    path = tmp_path / "user_settings.toml"
    monkeypatch.setenv("SHOT2EXR_SETTINGS", str(path))
    return path


AP1 = (0.713, 0.293, 0.165, 0.830, 0.128, 0.044, 0.32168, 0.33767)


@pytest.fixture(scope="session")
def ocio_cfg():
    pytest.importorskip("PyOpenColorIO")
    from shot2exr.color_manager import load_config

    return load_config("ocio://studio-config-latest")


@pytest.fixture
def make_exr_sequence(tmp_path):
    """make_exr_sequence(frames, name='plate', attrs=None, size=(32, 18), channels=3, directory=None)."""
    oiio = pytest.importorskip("OpenImageIO")
    np = pytest.importorskip("numpy")

    def _make(frames, name="plate", attrs=None, size=(32, 18), channels=3, directory=None, per_frame_attrs=None):
        directory = Path(directory or tmp_path / "seq")
        directory.mkdir(parents=True, exist_ok=True)
        for frame in frames:
            spec = oiio.ImageSpec(size[0], size[1], channels, oiio.HALF)
            for key, value in {**(attrs or {}), **((per_frame_attrs or {}).get(frame, {}))}.items():
                if key == "chromaticities":
                    spec.attribute(key, oiio.TypeDesc("float[8]"), tuple(value))
                else:
                    spec.attribute(key, value)
            out = oiio.ImageOutput.create("exr")
            path = directory / f"{name}.{frame:04d}.exr"
            assert out.open(str(path), spec), oiio.geterror()
            out.write_image(np.full((size[1], size[0], channels), 0.18, np.float32))
            out.close()
        return directory

    return _make


@pytest.fixture(scope="session")
def ffmpeg_bin():
    path = shutil.which("ffmpeg")
    if not path or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe not on PATH")
    return path


@pytest.fixture
def make_video(tmp_path, ffmpeg_bin):
    """make_video(name='clip.mov', frames=10, tags=True, pix_fmt='yuv420p', codec='libx264')."""

    def _make(name="my clip.mov", frames=10, tags=True, pix_fmt="yuv420p", codec="libx264"):
        path = tmp_path / name
        cmd = [ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24",
               "-frames:v", str(frames), "-c:v", codec, "-pix_fmt", pix_fmt]
        if tags:
            # setparams tags the frames (needed by FFmpeg >= 9); the output options tag the stream (FFmpeg 6).
            cmd += ["-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"]
            cmd += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
        subprocess.run(cmd + [str(path)], check=True)
        return path

    return _make
