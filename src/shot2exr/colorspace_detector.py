"""Input colour space detection from metadata (never from pixel appearance).

Every result is DETECTED, INFERRED or UNKNOWN, with the evidence used and a short explanation.
Identifiers are Color Interop Forum IDs (e.g. ``lin_ap1_scene``) resolved against the active
OCIO config; if the config has no matching space the result is UNKNOWN, never a substitute.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from shot2exr.color_manager import ColorConfig
from shot2exr.models import ColorDetection, DetectionState

DETECTED, INFERRED, UNKNOWN = DetectionState.DETECTED, DetectionState.INFERRED, DetectionState.UNKNOWN

# Fallback names for configs that predate interop IDs / aliases.
FALLBACK_NAMES: dict[str, list[str]] = {
    "lin_ap1_scene": ["ACEScg", "lin_ap1", "ACES - ACEScg"],
    "lin_ap0_scene": ["ACES2065-1", "lin_ap0", "aces"],
    "lin_rec709_scene": ["Linear Rec.709 (sRGB)", "lin_rec709", "lin_srgb", "Utility - Linear - sRGB"],
    "lin_p3d65_scene": ["Linear P3-D65", "lin_p3d65"],
    "lin_rec2020_scene": ["Linear Rec.2020", "lin_rec2020"],
    "srgb_rec709_scene": ["sRGB Encoded Rec.709 (sRGB)", "srgb_tx", "sRGB - Texture", "Utility - sRGB - Texture"],
    "srgb_p3d65_scene": ["sRGB Encoded P3-D65", "srgb_p3d65"],
    "g22_rec709_scene": ["Gamma 2.2 Encoded Rec.709", "g22_rec709_tx"],
    "g24_rec709_scene": ["Gamma 2.4 Encoded Rec.709", "g24_rec709_tx"],
    "ocio:itu709_rec709_scene": ["Camera Rec.709", "camera_rec709", "Utility - Rec.709 - Camera"],
    "pq_rec2020_display": ["Rec.2100-PQ - Display", "rec2100_pq_display"],
    "hlg_rec2020_display": ["Rec.2100-HLG - Display", "rec2100_hlg_display"],
}

# CIE xy of R, G, B, white (OpenEXR ``chromaticities`` order).
PRIMARIES: dict[str, tuple[float, ...]] = {
    "ap0": (0.7347, 0.2653, 0.0, 1.0, 0.0001, -0.0770, 0.32168, 0.33767),
    "ap1": (0.713, 0.293, 0.165, 0.830, 0.128, 0.044, 0.32168, 0.33767),
    "rec709": (0.64, 0.33, 0.30, 0.60, 0.15, 0.06, 0.3127, 0.3290),
    "p3d65": (0.680, 0.320, 0.265, 0.690, 0.150, 0.060, 0.3127, 0.3290),
    "rec2020": (0.708, 0.292, 0.170, 0.797, 0.131, 0.046, 0.3127, 0.3290),
}
LINEAR_INTEROP = {g: f"lin_{g}_scene" for g in PRIMARIES}
_TOLERANCE = 0.002


def _resolve(cfg: ColorConfig | None, interop_id: str) -> str | None:
    if cfg is None:
        return None
    return cfg.resolve_candidates([interop_id, *FALLBACK_NAMES.get(interop_id, [])])


def match_primaries(chroma: Sequence[float] | None) -> str | None:
    if not chroma or len(chroma) != 8:
        return None
    for name, ref in PRIMARIES.items():
        if all(abs(float(a) - b) <= _TOLERANCE for a, b in zip(chroma, ref)):
            return name
    return None


def gamut_of_interop(interop_id: str) -> str | None:
    parts = interop_id.split(":")[-1].split("_")
    return next((p for p in parts if p in PRIMARIES), None)


def _finish(state, interop_id, explanation, evidence, cfg, decode=None) -> ColorDetection:
    """Map an interop ID to the active config; downgrade to UNKNOWN if it is not available."""
    evidence = dict(evidence)
    if interop_id:
        evidence["proposed_interop_id"] = interop_id
    if state is UNKNOWN or not interop_id:
        return ColorDetection(UNKNOWN, None, explanation, evidence, decode)
    name = _resolve(cfg, interop_id)
    if name is None:
        where = f"the active OCIO config ({cfg.name or cfg.source})" if cfg else "any loaded OCIO config"
        return ColorDetection(
            UNKNOWN, None,
            f"{explanation} However, {where} has no colour space matching '{interop_id}'; select one manually.",
            evidence, decode,
        )
    return ColorDetection(state, name, explanation, evidence, decode)


# --------------------------------------------------------------------------- video

_SD_MATRICES = {"smpte170m", "bt470bg"}
_BT709_TRC = {"bt709", "smpte170m", "bt2020-10", "bt2020-12", "bt470bg"}


def _video_decode(meta: dict[str, Any], height: int | None) -> tuple[dict[str, Any], list[str]]:
    """How the decoder must turn the stored samples into RGB (before any OCIO transform)."""
    assumed: list[str] = []
    rng = meta.get("color_range")
    if meta.get("is_rgb_pixel_format"):
        if not rng:
            rng = "pc"
            assumed.append("RGB pixel format without range tag: full range assumed.")
        return {"yuv_to_rgb": False, "matrix": None, "range": rng, "assumed": assumed}, assumed
    matrix = meta.get("color_space")
    if not matrix:
        matrix = "bt709" if (height or 0) >= 720 else "smpte170m"
        assumed.append(f"YUV matrix not tagged: {matrix} assumed from frame height.")
    if not rng:
        rng = "tv"
        assumed.append("Colour range not tagged: limited (tv) range assumed.")
    return {"yuv_to_rgb": True, "matrix": matrix, "range": rng, "assumed": assumed}, assumed


def detect_video(meta: dict[str, Any], cfg: ColorConfig | None, height: int | None = None) -> ColorDetection:
    prim, trc, matrix = meta.get("color_primaries"), meta.get("color_transfer"), meta.get("color_space")
    decode, assumptions = _video_decode(meta, height)
    evidence = {k: meta.get(k) for k in ("color_primaries", "color_transfer", "color_space", "color_range", "pix_fmt")}
    evidence["candidates"] = []

    state, interop, why = UNKNOWN, None, ""
    if not prim and not trc and not matrix:
        why = ("The video carries no colour primaries, transfer or matrix tags. Rec.709 is a common "
               "convention for untagged HD video but cannot be verified; select the input colour space.")
    elif trc == "smpte2084" and prim in ("bt2020", None):
        state, interop = INFERRED, "pq_rec2020_display"
        why = "PQ (SMPTE ST 2084) transfer with BT.2020 primaries: Rec.2100-PQ, display-referred HDR."
    elif trc == "arib-std-b67" and prim in ("bt2020", None):
        state, interop = INFERRED, "hlg_rec2020_display"
        why = "HLG (ARIB STD-B67) transfer with BT.2020 primaries: Rec.2100-HLG, display-referred HDR."
    elif trc == "linear" and prim in ("bt709", "bt2020"):
        state, interop = DETECTED, "lin_rec709_scene" if prim == "bt709" else "lin_rec2020_scene"
        why = f"Linear transfer with {prim} primaries."
    elif trc == "iec61966-2-1" and prim in ("bt709", "smpte432"):
        state, interop = DETECTED, "srgb_rec709_scene" if prim == "bt709" else "srgb_p3d65_scene"
        why = f"sRGB transfer (IEC 61966-2-1) with {prim} primaries."
    elif trc == "gamma22" and prim == "bt709":
        state, interop = DETECTED, "g22_rec709_scene"
        why = "Gamma 2.2 transfer with BT.709 primaries."
    elif prim == "bt709" or (prim is None and (trc in _BT709_TRC or matrix == "bt709")):
        state, interop = INFERRED, "g24_rec709_scene"
        evidence["candidates"] = ["g24_rec709_scene", "ocio:itu709_rec709_scene"]
        why = ("Tagged as Rec.709. The BT.709 transfer tag names the camera OETF, but mastered video is "
               "normally decoded with the BT.1886 (gamma 2.4) EOTF, so 'Gamma 2.4 Encoded Rec.709' is "
               "proposed. Use 'Camera Rec.709' instead if the footage is unaltered camera output.")
        if prim is None:
            why += " Primaries are not tagged; Rec.709 is inferred from the other tags."
    elif prim in ("smpte170m", "bt470bg"):
        why = (f"Standard-definition primaries ({prim}) are not mapped automatically; "
               "select the input colour space manually.")
    else:
        why = (f"Unrecognised colour tag combination (primaries={prim}, transfer={trc}, matrix={matrix}); "
               "select the input colour space manually.")

    notes = []
    if prim == "bt709" and matrix in _SD_MATRICES:
        notes.append(f"Matrix ({matrix}) is inconsistent with BT.709 primaries; metadata may be wrong.")
    notes.extend(assumptions)
    if notes and state is DETECTED:
        state = INFERRED
    if notes:
        why = f"{why} {' '.join(notes)}"
    if state is not UNKNOWN:
        why += (" YUV->RGB decoding (matrix and range) is a separate step done before the OCIO transform,"
                " so the transfer function is applied only once.") if decode["yuv_to_rgb"] else ""
    return _finish(state, interop, why.strip(), evidence, cfg, decode)


# --------------------------------------------------------------------------- EXR

def detect_exr(frame_attributes: Sequence[dict[str, Any]], cfg: ColorConfig | None,
               sample_path: Path | None = None) -> ColorDetection:
    """``frame_attributes``: colour attributes of every inspected frame, in order."""
    if not frame_attributes:
        return ColorDetection(UNKNOWN, None, "No EXR headers were read.", {})
    first = frame_attributes[0]
    inconsistent = [i for i, a in enumerate(frame_attributes) if a != first]
    evidence: dict[str, Any] = {"header": first, "frames_checked": len(frame_attributes)}
    if inconsistent:
        evidence["inconsistent_frame_indices"] = inconsistent[:20]
        return ColorDetection(
            UNKNOWN, None,
            f"Colour metadata differs across the sequence ({len(inconsistent)} frames differ from the first); "
            "select the input colour space manually.",
            evidence,
        )

    interop = first.get("colorInteropID")
    chroma = first.get("chromaticities")
    gamut = match_primaries(chroma)
    aces_flag = first.get("acesImageContainerFlag")
    evidence["chromaticities_match"] = gamut
    file_rule = cfg.file_rule_colorspace(sample_path) if (cfg and sample_path) else None
    evidence["ocio_file_rule"] = file_rule

    if interop:
        expected = gamut_of_interop(str(interop))
        if gamut and expected and gamut != expected:
            return _finish(INFERRED, str(interop),
                           f"colorInteropID is '{interop}' but the chromaticities match {gamut}; confirm which is correct.",
                           evidence, cfg)
        return _finish(DETECTED, str(interop), f"Header declares colorInteropID '{interop}'.", evidence, cfg)

    if aces_flag in (1, "1", True):
        if chroma and gamut != "ap0":
            return _finish(INFERRED, "lin_ap0_scene",
                           "acesImageContainerFlag is set but chromaticities are not AP0; confirm the encoding.",
                           evidence, cfg)
        return _finish(DETECTED, "lin_ap0_scene",
                       "acesImageContainerFlag=1 (SMPTE ST 2065-4 container: linear AP0, ACES2065-1).", evidence, cfg)

    if gamut:
        why = (f"Chromaticities match {gamut} primaries. Chromaticities do not state the transfer function; "
               "linear encoding is assumed (usual for EXR) and must be confirmed.")
        if file_rule:
            why += f" The OCIO file rules suggest '{file_rule}'."
        return _finish(INFERRED, LINEAR_INTEROP[gamut], why, evidence, cfg)

    if chroma:
        why = "Chromaticities do not match any known primaries (AP0, AP1, Rec.709, P3-D65, Rec.2020)."
    else:
        why = "The EXR headers contain no colour metadata (no colorInteropID, chromaticities or ACES flag)."
    if file_rule:
        return ColorDetection(INFERRED, file_rule, f"{why} The active OCIO config's file rules map this file to '{file_rule}'.", evidence)
    return ColorDetection(UNKNOWN, None, f"{why} Select the input colour space manually.", evidence)
