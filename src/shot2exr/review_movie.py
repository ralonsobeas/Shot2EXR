"""ProRes review movie written next to every EXR sequence.

The movie is a *review proxy*: the converted frames go through an OCIO display/view transform
(Rec.709 by default) and are encoded as ProRes 422 HQ by FFmpeg. It is never pipeline data; the
EXRs are written before, and independently of, this transform.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from shot2exr import config
from shot2exr.errors import OutputError

_RATE = re.compile(r"^\d+(/\d+)?$")
NOTE = ("Review proxy only (display-referred, 10-bit ProRes); never use it as pipeline data. "
        "The EXR sequence is the deliverable.")


def movie_fps(source_fps: str | None) -> tuple[str, str]:
    """``(fps, origin)``: the source frame rate for video, else the default for EXR input."""
    if source_fps and _RATE.match(source_fps) and not source_fps.startswith("0"):
        return source_fps, "source"
    return config.REVIEW_MOVIE_DEFAULT_FPS, "default"


def build_command(ffmpeg: Path, out: Path, width: int, height: int, fps: str, comment: str) -> list[str]:
    tags = "color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"
    vf = ("pad=ceil(iw/2)*2:ceil(ih/2)*2,"  # 4:2:2 needs an even width
          "scale=out_color_matrix=bt709:out_range=tv:flags=spline+accurate_rnd+full_chroma_int,"
          f"format=yuv422p10le,setparams={tags}")
    return [str(ffmpeg), "-nostdin", "-hide_banner", "-v", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{width}x{height}", "-framerate", fps, "-i", "-",
            "-vf", vf, "-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
            "-metadata", f"comment={comment}", "-an", str(out)]


class ReviewMovieWriter:
    """Feed display-referred RGB frames (float 0..1, H x W x 3) to an FFmpeg ProRes encoder."""

    def __init__(self, ffmpeg: Path, out: Path, width: int, height: int, fps: str, comment: str = NOTE):
        self.path = out
        self.command = build_command(ffmpeg, out, width, height, fps, comment)
        self.width, self.height = width, height
        self.frames_written = 0
        self._proc: subprocess.Popen | None = None
        self._stderr = None

    def __enter__(self) -> "ReviewMovieWriter":
        self._stderr = tempfile.TemporaryFile()
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                          stderr=self._stderr, **kwargs)
        except OSError as exc:
            raise OutputError(f"Could not start FFmpeg for the review movie: {exc}") from None
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def write(self, rgb: Any) -> None:
        import numpy as np

        assert self._proc is not None and self._proc.stdin is not None, "use 'with ReviewMovieWriter(...)'"
        data = (np.clip(rgb, 0.0, 1.0) * 65535.0 + 0.5).astype("<u2")
        try:
            self._proc.stdin.write(np.ascontiguousarray(data).tobytes())
        except (BrokenPipeError, OSError):
            raise OutputError(f"The review movie encoder stopped after {self.frames_written} frames: "
                              f"{self._error_text() or 'broken pipe'}") from None
        self.frames_written += 1

    def finish(self) -> None:
        """Close the input and wait for FFmpeg; raises OutputError if encoding failed."""
        assert self._proc is not None and self._proc.stdin is not None
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        code = self._proc.wait()
        if code != 0 or not self.path.is_file():
            raise OutputError(f"The review movie could not be encoded: {self._error_text() or f'exit code {code}'}")

    def close(self) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.kill()
            if self._proc.stdin and not self._proc.stdin.closed:
                try:
                    self._proc.stdin.close()
                except OSError:
                    pass
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
