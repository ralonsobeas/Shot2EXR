"""Studio settings: per-OS projects root and code -> folder mappings (TOML).

Precedence: bundled ``default_settings.toml`` < user settings file (``$SHOT2EXR_SETTINGS``, else
``%APPDATA%/Shot2EXR/settings.toml`` on Windows or ``$XDG_CONFIG_HOME/shot2exr/settings.toml``
(default ``~/.config``) on Linux). Tables are merged key by key.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from shot2exr.errors import ConfigError


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform  # unsupported platforms get a clear "no projects_root" message


def user_config_dir() -> Path:
    """Per-user config folder: ``%APPDATA%\\Shot2EXR`` on Windows, ``~/.config/shot2exr`` on Linux."""
    if current_platform() == "windows":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Shot2EXR"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "shot2exr"


def user_settings_path() -> Path:
    env = os.environ.get("SHOT2EXR_SETTINGS", "").strip()
    if env:
        return Path(env).expanduser()
    return user_config_dir() / "settings.toml"


@dataclass
class Settings:
    roots: dict[str, str] = field(default_factory=dict)  # platform -> projects_root
    projects: dict[str, str] = field(default_factory=dict)  # project code -> folder
    tasks: dict[str, str] = field(default_factory=dict)  # task code -> folder
    user_file: Path | None = None  # where user overrides are read from / saved to

    def projects_root(self, platform: str | None = None) -> str:
        return (self.roots.get(platform or current_platform()) or "").strip()

    def to_toml(self) -> str:
        def q(v: str) -> str:
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'

        lines = []
        for plat in sorted(self.roots):
            lines += [f"[paths.{plat}]", f"projects_root = {q(self.roots[plat])}", ""]
        for table, mapping in (("projects", self.projects), ("tasks", self.tasks)):
            lines.append(f"[{table}]")
            lines += [f"{q(k)} = {q(v)}" for k, v in sorted(mapping.items())]
            lines.append("")
        return "\n".join(lines)


def _merge(settings: Settings, data: dict, origin: str) -> None:
    try:
        for plat, table in (data.get("paths") or {}).items():
            if "projects_root" in table:
                settings.roots[str(plat)] = str(table["projects_root"])
        settings.projects.update({str(k): str(v) for k, v in (data.get("projects") or {}).items()})
        settings.tasks.update({str(k): str(v) for k, v in (data.get("tasks") or {}).items()})
    except (AttributeError, TypeError) as exc:
        raise ConfigError(f"Malformed settings in {origin}: {exc}") from None


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    """Load defaults plus the user file (``path`` overrides the default user file location)."""
    settings = Settings()
    _merge(settings, tomllib.loads(resources.files("shot2exr").joinpath("default_settings.toml").read_text("utf-8")), "defaults")
    user = Path(path).expanduser() if path else user_settings_path()
    settings.user_file = user
    if path and not user.is_file():
        raise ConfigError(f"Settings file not found: {user}")
    if user.is_file():
        try:
            data = tomllib.loads(user.read_text("utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"Cannot read settings file {user}: {exc}") from None
        _merge(settings, data, str(user))
    return settings


def save_settings(settings: Settings) -> Path:
    """Write all settings to the user file (used by the GUI settings dialog)."""
    if settings.user_file is None:
        settings.user_file = user_settings_path()
    settings.user_file.parent.mkdir(parents=True, exist_ok=True)
    settings.user_file.write_text(settings.to_toml(), encoding="utf-8")
    return settings.user_file
