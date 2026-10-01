"""Discover numbered EXR sequences in a directory (filesystem only, no image decoding)."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from shot2exr import config
from shot2exr.errors import InputError
from shot2exr.models import ExrSequence

# Shortest prefix, then the trailing digit run before ".exr" (case-insensitive):
# "plate.1001.exr" -> ("plate.", "1001"); "shot_v002.0099.exr" -> ("shot_v002.", "0099").
_FRAME_RE = re.compile(r"^(?P<prefix>.*?)(?P<frame>\d+)(?P<ext>\.exr)$", re.IGNORECASE)


class MultipleSequencesError(InputError):
    def __init__(self, directory: Path, sequences: list[ExrSequence]):
        listing = [f"{s.pattern} ({s.count} files, {s.first}-{s.last})" for s in sequences]
        super().__init__(
            f"{len(sequences)} EXR sequences found in {directory}. Select one by passing the path "
            "of any of its frames instead of the directory.",
            listing,
        )
        self.sequences = sequences


def discover_sequences(directory: Path) -> list[ExrSequence]:
    """Return every numbered EXR sequence in ``directory`` (non-recursive), sorted by pattern."""
    if not directory.is_dir():
        raise InputError(f"Not a directory: {directory}")
    groups: dict[tuple[str, str], list[tuple[int, str, Path]]] = defaultdict(list)
    try:
        entries = list(directory.iterdir())
    except OSError as exc:
        raise InputError(f"Cannot read directory {directory}: {exc}") from None
    for entry in entries:
        if entry.name.startswith(".") or not entry.is_file():
            continue
        match = _FRAME_RE.match(entry.name)
        if not match:
            continue  # unrelated file
        groups[(match["prefix"], match["ext"])].append((int(match["frame"]), match["frame"], entry))

    sequences = [_build(directory, prefix, ext, items) for (prefix, ext), items in groups.items()]
    return sorted(sequences, key=lambda s: s.pattern)


def _build(directory: Path, prefix: str, ext: str, items: list[tuple[int, str, Path]]) -> ExrSequence:
    items.sort(key=lambda t: (t[0], t[1]))
    issues: list[str] = []
    by_frame: dict[int, Path] = {}
    for number, text, path in items:
        if number in by_frame:
            issues.append(f"Frame {number} exists more than once ({by_frame[number].name}, {path.name}).")
            continue
        by_frame[number] = path
    # Padding = width of zero-padded numbers. Unpadded numbers may be longer (1000 after 999 is fine).
    padded_widths = {len(t) for _, t, _ in items if t.startswith("0") and len(t) > 1}
    widths = {len(t) for _, t, _ in items}
    if len(padded_widths) > 1:
        issues.append(f"Inconsistent frame padding: widths {sorted(padded_widths)}.")
    padding = min(padded_widths) if padded_widths else min(widths)
    frames = sorted(by_frame)
    return ExrSequence(
        directory=directory,
        prefix=prefix,
        extension=ext,
        padding=padding,
        frames=frames,
        files=[by_frame[f] for f in frames],
        issues=issues,
    )


def find_sequence(path: Path) -> tuple[ExrSequence, list[str]]:
    """Resolve ``path`` (directory or one frame of a sequence) to a single sequence.

    Returns the sequence and warnings. A directory with several sequences raises
    ``MultipleSequencesError``; pointing at a frame file selects its sequence.
    """
    warnings: list[str] = []
    if path.is_dir():
        sequences = discover_sequences(path)
        if not sequences:
            raise InputError(f"No numbered EXR sequence found in {path} (expected e.g. plate.1001{config.EXR_EXTENSION}).")
        if len(sequences) > 1:
            raise MultipleSequencesError(path, sequences)
        return sequences[0], warnings

    if path.is_file() and path.suffix.lower() == config.EXR_EXTENSION:
        sequences = discover_sequences(path.parent)
        for seq in sequences:
            if any(f.name == path.name for f in seq.files):
                if len(sequences) > 1:
                    others = ", ".join(s.pattern for s in sequences if s is not seq)
                    warnings.append(f"Other EXR sequences in the same directory were ignored: {others}.")
                return seq, warnings
        raise InputError(f"{path.name} is not part of a numbered EXR sequence (expected NAME.####.exr).")
    raise InputError(f"Not an EXR sequence directory or frame: {path}")
