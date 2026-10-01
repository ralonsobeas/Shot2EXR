import pytest

from shot2exr.colorspace_detector import detect_exr, detect_video, match_primaries
from shot2exr.models import DetectionState as S

from conftest import AP1

REC709 = (0.64, 0.33, 0.30, 0.60, 0.15, 0.06, 0.3127, 0.3290)


def meta(prim=None, trc=None, matrix=None, rng=None, pix="yuv420p", rgb=False):
    return {"color_primaries": prim, "color_transfer": trc, "color_space": matrix, "color_range": rng,
            "pix_fmt": pix, "is_rgb_pixel_format": rgb}


@pytest.mark.parametrize("m,state,name", [
    (meta("bt709", "bt709", "bt709", "tv"), S.INFERRED, "Gamma 2.4 Encoded Rec.709"),
    (meta("bt709", "iec61966-2-1", "bt709", "tv"), S.DETECTED, "sRGB Encoded Rec.709 (sRGB)"),
    (meta("bt709", "linear", "bt709", "pc"), S.DETECTED, "Linear Rec.709 (sRGB)"),
    (meta("bt2020", "smpte2084", "bt2020nc", "tv"), S.INFERRED, "Rec.2100-PQ - Display"),
    (meta("bt2020", "arib-std-b67", "bt2020nc", "tv"), S.INFERRED, "Rec.2100-HLG - Display"),
    (meta(), S.UNKNOWN, None),
    (meta("smpte170m", "smpte170m", "smpte170m", "tv"), S.UNKNOWN, None),
])
def test_video_detection(ocio_cfg, m, state, name):
    det = detect_video(m, ocio_cfg, height=1080)
    assert det.state is state and det.colorspace == name
    assert det.explanation


def test_video_missing_range_downgrades_and_records_decode(ocio_cfg):
    det = detect_video(meta("bt709", "iec61966-2-1", "bt709", None), ocio_cfg, 1080)
    assert det.state is S.INFERRED
    assert det.decode == {"yuv_to_rgb": True, "matrix": "bt709", "range": "tv", "assumed": det.decode["assumed"]}
    assert det.decode["assumed"]


def test_video_rgb_pixel_format_needs_no_matrix(ocio_cfg):
    det = detect_video(meta("bt709", "linear", None, "pc", pix="gbrpf32le", rgb=True), ocio_cfg, 1080)
    assert det.decode["yuv_to_rgb"] is False and det.state is S.DETECTED


def test_unresolvable_space_becomes_unknown():
    det = detect_video(meta("bt709", "iec61966-2-1", "bt709", "tv"), None, 1080)
    assert det.state is S.UNKNOWN and det.colorspace is None


def test_match_primaries():
    assert match_primaries(AP1) == "ap1" and match_primaries(REC709) == "rec709"
    assert match_primaries((0.1,) * 8) is None and match_primaries(None) is None


@pytest.mark.parametrize("attrs,state,name", [
    ({"colorInteropID": "lin_ap1_scene"}, S.DETECTED, "ACEScg"),
    ({"colorInteropID": "lin_ap1_scene", "chromaticities": list(AP1)}, S.DETECTED, "ACEScg"),
    ({"colorInteropID": "lin_ap1_scene", "chromaticities": list(REC709)}, S.INFERRED, "ACEScg"),
    ({"colorInteropID": "no_such_space"}, S.UNKNOWN, None),
    ({"acesImageContainerFlag": 1}, S.DETECTED, "ACES2065-1"),
    ({"chromaticities": list(AP1)}, S.INFERRED, "ACEScg"),
    ({"chromaticities": list(REC709)}, S.INFERRED, "Linear Rec.709 (sRGB)"),
    ({}, S.UNKNOWN, None),
])
def test_exr_detection(ocio_cfg, attrs, state, name):
    det = detect_exr([attrs, dict(attrs)], ocio_cfg)
    assert det.state is state and det.colorspace == name


def test_exr_inconsistent_metadata_is_unknown(ocio_cfg):
    det = detect_exr([{"colorInteropID": "lin_ap1_scene"}, {"colorInteropID": "lin_ap0_scene"}], ocio_cfg)
    assert det.state is S.UNKNOWN and det.evidence["inconsistent_frame_indices"] == [1]


def test_exr_detection_from_real_headers(ocio_cfg, make_exr_sequence):
    from shot2exr.converter import inspect_source

    d = make_exr_sequence([1001, 1002], attrs={"colorInteropID": "lin_ap0_scene"})
    insp = inspect_source(d, ocio_cfg)
    assert insp.detection.state is S.DETECTED and insp.detection.colorspace == "ACES2065-1"
