"""Decode video frames with FFmpeg, one frame at a time, as float32 RGB(A).

Decoding is kept separate from colour management: FFmpeg only converts the stored samples
to full-range R'G'B' using an explicit YUV matrix and range (from ``ColorDetection.decode``).
No transfer function or primaries conversion happens here, so OCIO is the only place where
colour is transformed (no double conversion).

Frame exactness: ``-fps_mode passthrough`` emits every decoded frame once, in presentation
order, with no duplication, dropping or rate conversion. ``-noautorotate`` keeps the stored
orientation (rotation metadata is reported, not applied).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from shot2exr.errors import InputError
from shot2exr.exr_reader import Frame
from shot2exr.models import Resolution

# ffprobe ``color_space`` (matrix coefficients) -> swscale ``in_color_matrix``.
_MATRIX = {
    "bt709": "bt709",
    "smpte170m": "bt601",
    "bt470bg": "bt601",
    "bt2020nc": "bt2020",
    "bt2020c": "bt2020",
    "fcc": "fcc",
    "smpte240m": "smpte240m",
}
_RANGE = {"tv": "tv", "limited": "tv", "mpeg": "tv", "pc": "pc", "full": "pc", "jpeg": "pc"}
SWS_FLAGS = "spline+accurate_rnd+full_chroma_int"


def build_command(ffmpeg: Path, path: Path, decode: dict[str, Any], alpha: bool) -> list[str]:
    """FFmpeg argument list producing 16-bit full-range RGB(A) rawvideo on stdout."""
    out_fmt = "rgba64le" if alpha else "rgb48le"
    in_range = _RANGE.get(str(decode.get("range") or ""), "tv" if decode.get("yuv_to_rgb") else "pc")
    scale = [f"in_range={in_range}", "out_range=pc", f"flags={SWS_FLAGS}"]
    if decode.get("yuv_to_rgb"):
        scale.insert(0, f"in_color_matrix={_MATRIX.get(str(decode.get('matrix')), 'bt709')}")
    return [
        str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error",
        "-noautorotate", "-i", str(path),
        "-map", "0:v:0", "-an", "-sn", "-dn",
        "-fps_mode", "passthrough",
        "-vf", "scale=" + ":".join(scale),
        "-f", "rawvideo", "-pix_fmt", out_fmt, "-",
    ]


class VideoDecoder:
    """Iterate decoded frames. Use as a context manager so FFmpeg is always reaped."""

    def __init__(self, ffmpeg: Path, path: Path, resolution: Resolution, decode: dict[str, Any], alpha: bool):
        self.command = build_command(ffmpeg, path, decode, alpha)
        self.resolution = resolution
        self.channels = ["R", "G", "B", "A"] if alpha else ["R", "G", "B"]
        self.frames_decoded = 0
        self._proc: subprocess.Popen | None = None
        self._stderr = None

    def __enter__(self) -> "VideoDecoder":
        self._stderr = tempfile.TemporaryFile()
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._proc = subprocess.Popen(self.command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=self._stderr, bufsize=0, **kwargs)
        except OSError as exc:
            raise InputError(f"Could not start FFmpeg: {exc}") from None
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.kill()
            if self._proc.stdout:
                self._proc.stdout.close()
            self._proc.wait()
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None

    def _error_text(self) -> str:
        if self._stderr is None:
            return ""
        self._stderr.seek(0)
        lines = self._stderr.read().decode("utf-8", "replace").strip().splitlines()
        return lines[-1] if lines else ""

    def __iter__(self) -> Iterator[Frame]:
        import numpy as np

        assert self._proc is not None and self._proc.stdout is not None, "use 'with VideoDecoder(...)'"
        w, h, c = self.resolution.width, self.resolution.height, len(self.channels)
        size = w * h * c * 2
        stdout = self._proc.stdout
        while True:
            buf = bytearray(size)
            view, got = memoryview(buf), 0
            while got < size:
                n = stdout.readinto(view[got:])
                if not n:
                    break
                got += n
            if got == 0:
                break
            if got < size:
                raise InputError(f"FFmpeg returned a truncated frame ({got} of {size} bytes) after "
                                 f"{self.frames_decoded} frames. {self._error_text()}".strip())
            px = np.frombuffer(buf, dtype="<u2").reshape(h, w, c).astype(np.float32) / 65535.0
            if c == 4:  # video alpha is straight; EXR stores premultiplied colour
                px[..., :3] *= px[..., 3:4]
            self.frames_decoded += 1
            yield Frame(px, list(self.channels))
        code = self._proc.wait()
        if code != 0:
            raise InputError(f"FFmpeg failed decoding after {self.frames_decoded} frames: "
                             f"{self._error_text() or f'exit code {code}'}")
