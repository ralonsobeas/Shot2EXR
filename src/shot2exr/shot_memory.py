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


def shot_key(project: str | None, shot: str | None) -> str | None:
    project, shot = (project or "").strip(), (shot or "").strip()
    return f"{project}/{shot}" if project and shot else None


def load_history(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Every remembered shot; an unreadable or damaged file counts as empty (it is only a convenience)."""
    try:
        data = json.loads((path or history_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    shots = data.get("shots") if isinstance(data, dict) else None
    return {k: v for k, v in shots.items() if isinstance(v, dict)} if isinstance(shots, dict) else {}


def recall(project: str | None, shot: str | None, path: Path | None = None) -> dict[str, Any] | None:
    """The settings last used for this project + shot, or ``None``."""
    key = shot_key(project, shot)
    entry = load_history(path).get(key) if key else None
    if not entry:
        return None
    return {k: entry[k] for k in (*FIELDS, "task", "updated") if entry.get(k) not in (None, "")}


def remember(request: ConversionRequest, path: Path | None = None) -> Path | None:
    """Store the settings of a successful conversion. Best effort: returns ``None`` if the file can't be written."""
    key = shot_key(request.project, request.shot)
    if not key:
        return None
    path = path or history_path()
    shots = load_history(path)
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
    except OSError:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
        return None
    return path
