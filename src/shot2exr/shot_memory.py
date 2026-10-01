"""Per-shot memory of conversion settings.

After a successful conversion the resolution, resize mode, colour spaces and OCIO config are stored
for that project + shot, so the next conversion of the same shot (any task) starts from them. Element,
version and start frame are never remembered. The file is per user and lives next to the user
settings (``shot_history.json``); ``$SHOT2EXR_HISTORY`` overrides the path.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shot2exr.models import ConversionRequest
from shot2exr.settings import user_config_dir

HISTORY_ENV = "SHOT2EXR_HISTORY"
FILENAME = "shot_history.json"
FIELDS = ("resolution", "resize_mode", "input_colorspace", "output_colorspace", "ocio_config")


def history_path() -> Path:
    env = os.environ.get(HISTORY_ENV, "").strip()
    return Path(env).expanduser() if env else user_config_dir() / FILENAME


class HistoryError(Exception):
    """The remembered-settings file could not be read or written (the message names the file)."""


def shot_key(project: str | None, shot: str | None) -> str | None:
    """``PROJECT/SHOT``, upper-cased and stripped, so ``proj``/``PROJ `` find the same entry."""
    project, shot = (project or "").strip().upper(), (shot or "").strip().upper()
    return f"{project}/{shot}" if project and shot else None


def load_history(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Every remembered shot. A missing file is empty; an unreadable or damaged one raises ``HistoryError``."""
    path = path or history_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise HistoryError(f"Cannot read the remembered settings file {path}: {exc.strerror or exc}") from None
    except ValueError as exc:
        raise HistoryError(f"The remembered settings file {path} is damaged ({exc}); delete it to start again.") from None
    shots = data.get("shots") if isinstance(data, dict) else None
    if not isinstance(shots, dict):
        return {}
    # Keys are normalised on read too, so entries written by older versions (case as typed) still match.
    return {shot_key(*k.split("/", 1)) if "/" in k else k: v for k, v in shots.items() if isinstance(v, dict)}


def recall(project: str | None, shot: str | None, path: Path | None = None) -> dict[str, Any] | None:
    """The settings last used for this project + shot, or ``None``. Raises ``HistoryError`` if the file is unreadable."""
    key = shot_key(project, shot)
    entry = load_history(path).get(key) if key else None
    if not entry:
        return None
    return {k: entry[k] for k in (*FIELDS, "task", "updated") if entry.get(k) not in (None, "")}


def remember(request: ConversionRequest, path: Path | None = None) -> Path | None:
    """Store the settings of a successful conversion and return the file. Raises ``HistoryError`` on failure."""
    key = shot_key(request.project, request.shot)
    if not key:
        return None
    path = path or history_path()
    try:
        shots = load_history(path)
    except HistoryError:
        shots = {}  # a damaged file is replaced; an unreadable one fails again on write below
    shots[key] = {
        "resolution": str(request.output_resolution),
        "resize_mode": request.resize_mode.value,
        "input_colorspace": request.input_colorspace,
        "output_colorspace": request.output_colorspace,
        "ocio_config": request.ocio_config,
        "task": request.task,
        "updated": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".shot_history-", suffix=".json", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "shots": dict(sorted(shots.items()))}, fh, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
        raise HistoryError(f"Cannot save remembered settings to {path}: {exc.strerror or exc}") from None
    return path
