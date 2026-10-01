import pytest

from shot2exr import naming
from shot2exr.errors import ValidationError


@pytest.mark.parametrize("frame,text", [(0, "0000"), (7, "0007"), (1009, "1009"), (12345, "12345"), (1234567, "1234567")])
def test_frame_padding_never_truncates(frame, text):
    assert naming.format_frame(frame) == text


def test_output_range_and_names_cross_padding_boundary():
    assert naming.output_frame_range(9998, 4) == (9998, 10001)
    names = list(naming.output_filenames("A_B_c_v001", 9998, 4))
    assert names[-1] == "A_B_c_v001.10001.exr"


def test_start_frame_validation():
    assert naming.validate_frame_number("1009") == 1009
    for bad in (-1, "abc", 10**9):
        with pytest.raises(ValidationError):
            naming.validate_frame_number(bad)
    with pytest.raises(ValidationError):
        naming.output_frame_range(1001, 0)


def test_format_frame_ranges():
    assert naming.format_frame_ranges([1009, 1003, 1004, 1005, 1012, 1011]) == "1003-1005, 1009, 1011-1012"
    assert naming.format_frame_ranges([]) == ""
