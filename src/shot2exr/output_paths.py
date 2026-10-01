"""The single place where output directories are built (shared by CLI, GUI and converter).

{projects_root}/{PROJECT_FOLDER}/VFX/{PROJECT}_{SEQ}/{PROJECT}_{SHOT}/Tasks/{TASK_FOLDER}/ComfyUI/{ELEMENT}/
    {PROJECT}_{SHOT}_{TASK}_v{VERSION}/
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

from shot2exr import naming
from shot2exr.errors import ConfigError
from shot2exr.settings import Settings, current_platform

VFX_DIR, TASKS_DIR, COMFYUI_DIR = "VFX", "Tasks", "ComfyUI"
_FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]*(?<! )$")


def sequence_from_shot(shot: str) -> str:
    """``0046_005`` -> ``0046`` (first ``_`` component, kept as text so leading zeros survive)."""
    return naming.validate_token(shot, "Shot").split("_", 1)[0]


def _folder(value: str, what: str, settings_hint: str) -> str:
    if not _FOLDER_RE.match(value or ""):
        raise ConfigError(f"{what} folder name {value!r} is invalid (letters, digits, space, '_' and '-' only). {settings_hint}")
    return value


def _mapped(mapping: dict[str, str], code: str, what: str, settings: Settings) -> str:
    hint = f"Edit {settings.user_file}" if settings.user_file else "Edit the settings file"
    if code not in mapping:
        table = "projects" if what == "Project" else "tasks"
        raise ConfigError(
            f"No folder mapping for {what.lower()} code {code!r}. {hint} and add it under [{table}], "
            f'e.g. {code} = "FolderName".'
        )
    return _folder(mapping[code], what, hint)


def build_version_directory(root: PurePath, project_folder: str, project: str, shot: str,
                            task_folder: str, element: str, basename: str) -> PurePath:
    """Pure path join; ``root``'s type decides the flavour (``Path``, ``PureWindowsPath``...)."""
    seq = sequence_from_shot(shot)
    return (root / project_folder / VFX_DIR / f"{project}_{seq}" / f"{project}_{shot}" / TASKS_DIR
            / task_folder / COMFYUI_DIR / element / basename)


@dataclass(frozen=True)
class OutputLocation:
    directory: PurePath
    origin: str  # "auto" or "manual"
    projects_root: str | None
    element: str


def resolve_output_location(project: str, shot: str, task: str, element: str, version: str | int, *,
                            settings: Settings, manual_directory: str | Path | None = None,
                            projects_root: str | None = None, platform: str | None = None) -> OutputLocation:
    """Validate the naming fields and return the final version directory.

    ``manual_directory`` (advanced override) wins; otherwise the directory is built from the
    projects root (``projects_root`` override, else the settings value for ``platform``).
    Raises ``ValidationError`` for bad names and ``ConfigError`` for missing configuration.
    """
    basename = naming.output_basename(project, shot, task, version)
    element = naming.validate_token(element, "Element")
    if manual_directory not in (None, ""):
        return OutputLocation(Path(manual_directory).expanduser(), "manual", None, element)

    plat = platform or current_platform()
    root_text = (projects_root or "").strip() or settings.projects_root(plat)
    if not root_text:
        where = settings.user_file or "the settings file"
        raise ConfigError(
            f"No projects root is configured for {plat}. Set [paths.{plat}] projects_root in {where} "
            "(or use --projects-root / the GUI settings), or pass an explicit output directory."
        )
    flavour = {"windows": PureWindowsPath, "linux": PurePosixPath}.get(plat, Path)
    if plat == current_platform():
        flavour = Path  # concrete, native path on this machine
    root = flavour(root_text).expanduser() if flavour is Path else flavour(root_text)
    if not root.is_absolute():
        raise ConfigError(f"Projects root must be an absolute path, got {root_text!r}.")
    directory = build_version_directory(
        root,
        _mapped(settings.projects, project.strip(), "Project", settings),
        project.strip(),
        shot.strip(),
        _mapped(settings.tasks, task.strip(), "Task", settings),
        element,
        basename,
    )
    return OutputLocation(directory, "auto", str(root), element)
