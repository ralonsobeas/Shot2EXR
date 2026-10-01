import pytest

from shot2exr import naming
from shot2exr.errors import ValidationError


@pytest.mark.parametrize("raw,expected", [
    ("001", "v001"), ("v001", "v001"), ("V001", "v001"), ("1", "v001"), (1, "v001"),
    ("v12", "v012"), ("1234", "v1234"), (" v007 ", "v007"), ("0", "v000"),
])
def test_normalize_version(raw, expected):
    assert naming.normalize_version(raw) == expected


@pytest.mark.parametrize("raw", ["", "vv001", "v", "1a", "-1", "v-1", "1.0", None])
def test_normalize_version_rejects(raw):
    with pytest.raises(ValidationError):
        naming.normalize_version(raw)


def test_spec_example_filenames():
    base = naming.output_basename("GOD", "0046_005", "ml", "001")
    assert base == "GOD_0046_005_ml_v001"
    assert list(naming.output_filenames(base, 1009, 4)) == [
        "GOD_0046_005_ml_v001.1009.exr", "GOD_0046_005_ml_v001.1010.exr",
        "GOD_0046_005_ml_v001.1011.exr", "GOD_0046_005_ml_v001.1012.exr",
    ]
    assert naming.report_filename(base) == "GOD_0046_005_ml_v001.conversion_report.json"
    assert naming.output_pattern(base) == "GOD_0046_005_ml_v001.####.exr"


def test_version_inputs_produce_same_name():
    assert naming.output_basename("GOD", "0046_005", "ml", "v001") == naming.output_basename("GOD", "0046_005", "ml", "1")


@pytest.mark.parametrize("token", ["../etc", "a/b", "a\\b", "a b", "a.b", "", "  ", "_lead", "-lead", "x" * 65, "é"])
def test_invalid_tokens(token):
    with pytest.raises(ValidationError):
        naming.validate_token(token, "Shot")


@pytest.mark.parametrize("token", ["GOD", "0046_005", "ml", "comp-v2", "A1"])
def test_valid_tokens(token):
    assert naming.validate_token(token, "Shot") == token
