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

    def scene_linear(self) -> str | None:
        """The config's ``scene_linear`` role (the working space for resizing), if defined."""
        try:
            cs = self._cfg.getColorSpace("scene_linear")
        except Exception:  # noqa: BLE001
            cs = None
        return cs.getName() if cs is not None else None

    def processor(self, src: str, dst: str) -> Any | None:
        """Optimised CPU processor ``src -> dst``; ``None`` when the transform is a no-op."""
        if src == dst:
            return None
        ocio = _ocio()
        try:
            proc = self._cfg.getProcessor(src, dst)
        except Exception as exc:  # noqa: BLE001
            raise ColorSpaceError(f"OCIO cannot build a transform '{src}' -> '{dst}': {exc}") from None
        if proc.isNoOp():
            return None
        return proc.getOptimizedCPUProcessor(ocio.OPTIMIZATION_LOSSLESS)

    def review_display_view(self) -> tuple[str, str]:
        """Display and view used for the review movie: Rec.1886 Rec.709 if the config has it."""
        displays = list(self._cfg.getDisplays())
        display = app_config.REVIEW_MOVIE_DISPLAY if app_config.REVIEW_MOVIE_DISPLAY in displays else self._cfg.getDefaultDisplay()
        return display, self._cfg.getDefaultView(display)

    def display_processor(self, src: str) -> Any | None:
        """CPU processor ``src`` -> review display/view; ``None`` for data spaces (shown as-is)."""
        entry = self.entry(src)
        if entry is not None and entry.is_data:
            return None
        ocio = _ocio()
        display, view = self.review_display_view()
        try:
            transform = ocio.DisplayViewTransform(src=src, display=display, view=view)
            return self._cfg.getProcessor(transform).getOptimizedCPUProcessor(ocio.OPTIMIZATION_LOSSLESS)
        except Exception as exc:  # noqa: BLE001
            raise ColorSpaceError(f"OCIO cannot build the review transform '{src}' -> {display} / {view}: {exc}") from None

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


@dataclass
class ColorPipeline:
    """Colour steps around the resize: ``input -> resize_space`` before, ``resize_space -> output`` after.

    Resizing happens in scene-linear light: the input space itself when it is scene-linear
    (or data), otherwise the config's ``scene_linear`` role. Identical spaces produce no transform.
    """

    input: str
    output: str
    resize_space: str
    pre: Any | None
    post: Any | None
    steps: list[str]

    @classmethod
    def build(cls, cfg: ColorConfig, input_cs: str, output_cs: str, resizing: bool) -> "ColorPipeline":
        entry = cfg.entry(input_cs)
        linear_input = entry is not None and (entry.is_data or entry.encoding == "scene-linear")
        working = cfg.scene_linear()
        if not resizing or linear_input or working is None:
            space = input_cs
        else:
            space = working
        pre, post = cfg.processor(input_cs, space), cfg.processor(space, output_cs)
        steps = []
        if pre is not None:
            steps.append(f"OCIO '{input_cs}' -> '{space}' (scene-linear working space for resizing)")
        if post is not None:
            steps.append(f"OCIO '{space}' -> '{output_cs}'")
        if resizing and not linear_input and working is None:
            steps.append(f"note: no scene_linear role in the config; resized in '{input_cs}'")
        return cls(input_cs, output_cs, space, pre, post, steps)

    @staticmethod
    def apply(processor: Any | None, pixels: Any) -> Any:
        """Apply to RGB in place. With alpha, RGB is un-premultiplied around the transform."""
        if processor is None:
            return pixels
        import numpy as np

        rgb = np.ascontiguousarray(pixels[..., :3])
        if pixels.shape[2] == 4:
            alpha = pixels[..., 3]
            mask = alpha > 0
            rgb[mask] /= alpha[mask][:, None]
            processor.applyRGB(rgb)
            rgb[mask] *= alpha[mask][:, None]
        else:
            processor.applyRGB(rgb)
        pixels[..., :3] = rgb
        return pixels
