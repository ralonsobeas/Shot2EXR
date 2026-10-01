"""Application-wide constants and defaults."""

from __future__ import annotations

VIDEO_EXTENSIONS = frozenset({".mov", ".mp4"})
EXR_EXTENSION = ".exr"

DEFAULT_INPUT_COLORSPACE = "auto"
DEFAULT_OUTPUT_COLORSPACE = "ACEScg"
DEFAULT_RESIZE_MODE = "fit"
DEFAULT_START_FRAME = 1001
DEFAULT_RESOLUTION = (2048, 1152)

# Used when neither --ocio-config nor $OCIO is set. The ACES studio config ships
# inside OpenColorIO >= 2.2, so no external file is required.
BUILTIN_OCIO_CONFIG = "ocio://studio-config-latest"

# Naming rules (see naming.py).
VERSION_MIN_DIGITS = 3
FRAME_MIN_DIGITS = 4
MAX_TOKEN_LENGTH = 64
MAX_FRAME_NUMBER = 99_999_999

# EXR output defaults (applied by the Milestone 2 writer, shown in dry runs).
EXR_PIXEL_TYPE = "half"
EXR_COMPRESSION = "zip"

FFPROBE_TIMEOUT_S = 120
