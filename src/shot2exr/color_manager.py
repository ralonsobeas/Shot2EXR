"""OpenColorIO configuration loading and colour space lookup.

Config precedence: explicit path (``--ocio-config``) > ``$OCIO`` > OCIO's built-in studio config.
Colour space names are always resolved against the *active* config; nothing is substituted silently.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shot2exr import config as app_config
from shot2exr.errors import ColorSpaceError, DependencyError


def _ocio():
    try:
        import PyOpenColorIO as ocio
    except ImportError as exc:
        raise DependencyError(
            "OpenColorIO Python bindings are not available. Install 'py-opencolorio' from conda-forge."
        ) from exc
    return ocio


def ocio_version() -> str | None:
    try:
        return _ocio().__version__
    except DependencyError:
        return None


def resolve_config_source(explicit: str | os.PathLike | None = None) -> tuple[str, str]:
    """Return ``(source, origin)`` where origin is ``explicit``, ``env:OCIO`` or ``builtin``."""
    if explicit:
        return str(explicit), "explicit"
    env = os.environ.get("OCIO", "").strip()
    if env:
        return env, "env:OCIO"
    return app_config.BUILTIN_OCIO_CONFIG, "builtin"


@dataclass
class ColorSpaceEntry:
    name: str
    family: str
    encoding: str
    is_display: bool
    is_data: bool
    aliases: list[str]
    interop_id: str | None


class ColorConfig:
    """Thin wrapper over ``PyOpenColorIO.Config`` with the lookups Shot2EXR needs."""

    def __init__(self, ocio_config: Any, source: str, origin: str):
        self._cfg = ocio_config
        self.source = source
        self.origin = origin
        self.name = ocio_config.getName() or None
        self.cache_id = ocio_config.getCacheID()
        self.file_sha256 = _sha256(source)
        ocio = _ocio()
        self._entries: list[ColorSpaceEntry] = []
        for cs in ocio_config.getColorSpaces():
            interop = cs.getInteropID() if hasattr(cs, "getInteropID") else None
            self._entries.append(
                ColorSpaceEntry(
                    name=cs.getName(),
                    family=cs.getFamily() or "",
                    encoding=cs.getEncoding() or "",
                    is_display=cs.getReferenceSpaceType() == ocio.REFERENCE_SPACE_DISPLAY,
                    is_data=bool(cs.isData()),
                    aliases=list(cs.getAliases()),
                    interop_id=interop or None,
                )
            )

    @property
    def raw(self) -> Any:
        return self._cfg

    def colorspaces(self) -> list[ColorSpaceEntry]:
        return list(self._entries)

    def colorspace_names(self) -> list[str]:
        return [e.name for e in self._entries]

    def entry(self, name: str) -> ColorSpaceEntry | None:
        canonical = self.find(name)
        return next((e for e in self._entries if e.name == canonical), None) if canonical else None

    def find(self, name: str | None) -> str | None:
        """Canonical name for ``name`` (exact name or alias, as OCIO resolves it), else ``None``."""
        if not name:
            return None
        cs = self._cfg.getColorSpace(name)
        return cs.getName() if cs is not None else None

    def find_by_interop(self, interop_id: str | None) -> str | None:
        if not interop_id:
            return None
        for e in self._entries:
            if e.interop_id == interop_id:
                return e.name
        return self.find(interop_id)  # older configs expose interop IDs as aliases

    def resolve_candidates(self, candidates: list[str]) -> str | None:
        for cand in candidates:
            found = self.find_by_interop(cand) or self.find(cand)
            if found:
                return found
        return None

    def require(self, name: str, role: str) -> str:
        found = self.find(name)
        if not found:
            raise ColorSpaceError(
                f"{role} colour space {name!r} does not exist in the active OCIO config "
                f"({self.name or self.source}). Use --list-colorspaces to see valid names."
            )
        return found

    def file_rule_colorspace(self, path: Path) -> str | None:
        """Colour space from the config's File Rules, ignoring the catch-all default rule."""
        p = str(path)
        try:
            if self._cfg.filepathOnlyMatchesDefaultRule(p):
                return None
            return self.find(self._cfg.getColorSpaceFromFilepath(p))
        except Exception:  # noqa: BLE001 - malformed rules must not break inspection
            return None

    def describe(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "origin": self.origin,
            "name": self.name,
            "cache_id": self.cache_id,
            "file_sha256": self.file_sha256,
            "ocio_version": ocio_version(),
        }


def _sha256(source: str) -> str | None:
    path = Path(source)
    if source.startswith("ocio://") or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_config(explicit: str | os.PathLike | None = None) -> ColorConfig:
    ocio = _ocio()
    source, origin = resolve_config_source(explicit)
    if not source.startswith("ocio://") and not Path(source).expanduser().is_file():
        raise ColorSpaceError(f"OCIO config not found ({origin}): {source}")
    if not source.startswith("ocio://"):
        source = str(Path(source).expanduser())
    try:
        cfg = ocio.Config.CreateFromFile(source)
        cfg.validate()
    except Exception as exc:  # PyOpenColorIO raises its own Exception type
        raise ColorSpaceError(f"Invalid OCIO config ({origin}) {source}: {exc}") from None
    return ColorConfig(cfg, source, origin)
