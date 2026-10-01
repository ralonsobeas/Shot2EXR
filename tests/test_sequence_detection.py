import pytest

from shot2exr.errors import InputError
from shot2exr.sequence_detector import MultipleSequencesError, discover_sequences, find_sequence


def touch(d, *names):
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"")
    return d


def test_numeric_sort_range_and_unrelated_files(tmp_path):
    d = touch(tmp_path / "src", "plate.1002.exr", "plate.1001.exr", "plate.1004.exr", "plate.1003.exr",
              "notes.txt", "thumb.jpg", "plate.exr", ".plate.1005.exr")
    seq, warnings = find_sequence(d)
    assert seq.frames == [1001, 1002, 1003, 1004]
    assert [f.name for f in seq.files][0] == "plate.1001.exr"
    assert (seq.first, seq.last, seq.count, seq.missing_frames) == (1001, 1004, 4, [])
    assert seq.pattern == "plate.####.exr" and warnings == []


def test_unpadded_numbers_sort_numerically(tmp_path):
    d = touch(tmp_path / "s", "a.998.exr", "a.999.exr", "a.1000.exr", "a.1001.exr")
    seq, _ = find_sequence(d)
    assert seq.frames == [998, 999, 1000, 1001] and seq.issues == []


def test_missing_frames(tmp_path):
    d = touch(tmp_path / "s", *(f"p.{f:04d}.exr" for f in (1001, 1002, 1005, 1007)))
    seq, _ = find_sequence(d)
    assert seq.missing_frames == [1003, 1004, 1006]


def test_multiple_sequences_error_and_selection_by_frame(tmp_path):
    d = touch(tmp_path / "s", "bg.1001.exr", "bg.1002.exr", "fg.0001.exr", "fg.0002.exr", "FG.v2.1001.EXR")
    assert len(discover_sequences(d)) == 3
    with pytest.raises(MultipleSequencesError) as exc:
        find_sequence(d)
    assert len(exc.value.problems) == 3
    seq, warnings = find_sequence(d / "fg.0002.exr")
    assert seq.prefix == "fg." and seq.frames == [1, 2] and warnings


def test_duplicate_frame_numbers_reported(tmp_path):
    d = touch(tmp_path / "s", "p.1001.exr", "p.01001.exr", "p.1002.exr")
    seq, _ = find_sequence(d)
    assert seq.frames == [1001, 1002]
    assert any("more than once" in i for i in seq.issues)


def test_empty_or_invalid(tmp_path):
    with pytest.raises(InputError):
        find_sequence(touch(tmp_path / "empty", "readme.txt"))
    with pytest.raises(InputError):
        find_sequence(tmp_path / "nope")
