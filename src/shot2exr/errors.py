"""Exception hierarchy and CLI exit codes shared by the engine, CLI and GUI."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1  # unexpected / internal error
    USAGE = 2  # invalid parameters
    INPUT = 3  # missing, unreadable or inconsistent source media
    COLORSPACE = 4  # unknown colour space, confirmation required, bad OCIO config
    OUTPUT = 5  # output collisions, permissions
    DEPENDENCY = 6  # FFmpeg/FFprobe/OIIO/OCIO missing
    NOT_IMPLEMENTED = 7  # feature arrives in a later milestone
    CONFIG = 8  # settings: projects root / folder mappings missing or invalid
    CANCELLED = 9  # conversion cancelled by the user


class Shot2EXRError(Exception):
    """Base class. ``exit_code`` maps the error to a CLI exit status."""

    exit_code = ExitCode.ERROR

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.problems = list(problems or [])

    def __str__(self) -> str:
        if not self.problems:
            return self.message
        return self.message + "\n" + "\n".join(f"  - {p}" for p in self.problems)


class ValidationError(Shot2EXRError):
    exit_code = ExitCode.USAGE


class InputError(Shot2EXRError):
    exit_code = ExitCode.INPUT


class ColorSpaceError(Shot2EXRError):
    exit_code = ExitCode.COLORSPACE


class OutputError(Shot2EXRError):
    exit_code = ExitCode.OUTPUT


class DependencyError(Shot2EXRError):
    exit_code = ExitCode.DEPENDENCY


class ConfigError(Shot2EXRError):
    exit_code = ExitCode.CONFIG
