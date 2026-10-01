from pathlib import Path

import pytest

from shot2exr.errors import DependencyError, InputError
from shot2exr.media_probe import parse_ffprobe, probe_video, resolve_executable


def _probe(**stream):
    st = {"index": 0, "codec_type": "video", "codec_name": "prores", "width": 1920, "height": 1080,
          "pix_fmt": "yuv422p10le", "r_frame_rate": "24000/1001", "avg_frame_rate": "24000/1001",
          "time_base": "1/24000", "nb_frames": "48", "color_primaries": "bt709", "color_transfer": "bt709",
          "color_space": "bt709", "color_range": "tv"}
    st.update(stream)
    return {"streams": [st, {"codec_type": "audio"}], "format": {"format_name": "mov,mp4", "duration": "2.002"}}


def test_parse_tagged_prores():
    info = parse_ffprobe(_probe(), Path("a.mov"))
    assert (info.resolution.width, info.resolution.height) == (1920, 1080)
    assert info.frame_count == 48 and info.frame_count_method == "nb_frames"
    assert info.fps == "24000/1001" and info.variable_frame_rate is False
    assert info.color_metadata["color_primaries"] == "bt709" and info.has_alpha is False
    assert info.warnings == [] and info.frame_range is None


def test_parse_unset_values_and_estimated_count():
    info = parse_ffprobe(_probe(nb_frames="N/A", color_primaries="unknown", color_transfer="unspecified",
                                duration="2.002"), Path("a.mp4"))
    assert info.color_metadata["color_primaries"] is None and info.color_metadata["color_transfer"] is None
    assert info.frame_count == 48 and info.frame_count_method == "estimated" and not info.frame_count_exact


def test_parse_vfr_alpha_and_warnings():
    data = _probe(avg_frame_rate="2997/125", pix_fmt="yuva444p10le", sample_aspect_ratio="2:1", field_order="tt",
                  side_data_list=[{"rotation": -90}])
    info = parse_ffprobe(data, Path("a.mov"))
    assert info.variable_frame_rate is True and info.has_alpha is True and info.channels[-1] == "A"
    assert len(info.warnings) == 4  # VFR, non-square pixels, rotation, interlaced


def test_parse_skips_cover_art_and_rejects_no_video():
    data = _probe()
    data["streams"].insert(0, {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}})
    assert parse_ffprobe(data, Path("a.mp4")).codec == "prores"
    with pytest.raises(InputError):
        parse_ffprobe({"streams": [{"codec_type": "audio"}]}, Path("a.mp4"))


def test_missing_executable():
    with pytest.raises(DependencyError):
        resolve_executable("ffprobe", "/definitely/not/here/ffprobe")


def test_probe_real_video_with_spaces_in_path(make_video):
    path = make_video("my clip (v1).mov", frames=12)
    info = probe_video(path)
    assert info.frame_count == 12 and info.frame_count_exact
    assert str(info.resolution) == "160x90"
    assert info.color_metadata["color_transfer"] == "bt709"


def test_probe_unreadable_video(tmp_path, ffmpeg_bin):
    bad = tmp_path / "broken.mov"
    bad.write_bytes(b"not a movie")
    with pytest.raises(InputError):
        probe_video(bad)
