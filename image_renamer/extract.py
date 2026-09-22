"""Per-field crop, preprocess, OCR, postprocess and validation.

Only `ocr_engine` touches an external OCR library; every postprocess and
validate function here is pure and testable without a display or OCR
installed.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from PIL import Image, ImageOps

from .geometry import pad_pixel_rect, profile_rect_to_pixels

DIGIT_CONFUSION = {"O": "0", "D": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2"}
LETTER_CONFUSION = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z"}

MIN_YEAR = 1990


class FieldStatus:
    OK = "OK"
    EMPTY_FIELD = "EMPTY_FIELD"
    VALIDATION_FAILED = "VALIDATION_FAILED"


@dataclass
class FieldResult:
    value: str
    status: str
    raw_text: str = ""


def load_image(path: str) -> Image.Image:
    """Opens an image, applies EXIF-transpose, and converts to RGB.
    Must run before any measurement -- EXIF orientation otherwise makes
    every region land sideways with no error raised."""
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def crop_field(
    image: Image.Image,
    rect: tuple[float, float, float, float],
    pad_fraction: float = 0.02,
) -> Image.Image:
    w, h = image.size
    px_rect = profile_rect_to_pixels(rect, w, h)
    padded = pad_pixel_rect(px_rect, w, h, pad_fraction)
    return image.crop(padded)


CANONICAL_LINE_HEIGHT = 96
"""Target crop height fed to OCR. The recognizer internally normalises every
line to a fixed height regardless of input size, so a crop far from this
height gets resized twice: once here (high-quality LANCZOS) and once inside
the engine (a cheaper resize). Skipping our own resize lets a very tall crop
(common when cropping a high-resolution field straight off a 6000x4000
photo) hit the engine's internal resize at an extreme compression ratio,
which was observed to blur bold/embossed text into unrecognisable noise --
the engine returned an empty read at effectively zero confidence even
though the crop was clearly legible. Pre-resizing to this canonical height
ourselves keeps the engine's own resize modest and recovers the read."""


def trim_to_ink(crop: Image.Image, threshold_fraction: float = 0.12, gap_px: int = 60, pad: int = 15) -> Image.Image:
    """Trims a crop horizontally to the column span actually covered by
    text, discarding empty margin.

    A field's box is sized for the longest value it will ever hold, so a
    short value (e.g. "L1" in a box wide enough for "LA4-069-VC") leaves
    most of the crop empty. Handed to the OCR engine as-is, that near-empty
    line gets recognised as a whole, and the engine was observed to drop
    the value's leading character (reading "L1" as "1") -- the real text
    become a vanishingly small fraction of what the engine treats as "the
    line". Finding the actual ink and cropping to it (plus a small pad)
    fixes this without needing per-field tuning.

    Columns are scored by how much of the crop's height they cover in
    contrast to the background, then grouped into runs separated by more
    than `gap_px`; the run with the most total ink wins, so a stray dust
    speck or scratch elsewhere in the crop does not get selected over the
    genuine text.
    """
    import numpy as np

    grey = np.array(crop.convert("L"), dtype=np.float64)
    h, w = grey.shape
    if h == 0 or w == 0:
        return crop
    background_level = np.median(grey)
    ink_mask = np.abs(grey - background_level) > 35
    col_counts = ink_mask.sum(axis=0)
    text_cols = np.where(col_counts > threshold_fraction * h)[0]
    if len(text_cols) == 0:
        return crop

    clusters: list[tuple[int, int]] = []
    start = int(text_cols[0])
    prev = int(text_cols[0])
    for col in text_cols[1:]:
        col = int(col)
        if col - prev > gap_px:
            clusters.append((start, prev))
            start = col
        prev = col
    clusters.append((start, prev))

    best_start, best_end = max(clusters, key=lambda cl: col_counts[cl[0]:cl[1] + 1].sum())
    x0 = max(0, best_start - pad)
    x1 = min(w, best_end + 1 + pad)
    return crop.crop((x0, 0, x1, h))


def preprocess_for_ocr(crop: Image.Image, target_height: int = CANONICAL_LINE_HEIGHT) -> Image.Image:
    """Trim to the actual text, resize to a canonical line height,
    greyscale, autocontrast."""
    from PIL import ImageOps as _ImageOps

    crop = trim_to_ink(crop)
    w, h = crop.size
    if h != target_height and h > 0:
        scale = target_height / h
        crop = crop.resize((max(1, round(w * scale)), target_height), Image.LANCZOS)
    grey = crop.convert("L")
    return _ImageOps.autocontrast(grey)


def ocr_engine(image: Image.Image) -> str:
    """Runs RapidOCR against a preprocessed crop and returns concatenated
    recognised text. Imported lazily so the rest of the module (and its
    tests) never require rapidocr_onnxruntime to be installed."""
    import numpy as np

    array = np.array(image)
    with _engine_lock:
        engine = _get_shared_engine()
        result, _elapse = engine(array)
    if not result:
        return ""
    return " ".join(_result_text(item) for item in result)


def _result_text(item) -> str:
    """RapidOCR's result rows are [box, text, score] when its detection
    stage ran, but just [text, score] when it was skipped (use_det=False,
    as configured below). Accept both so a change in engine settings can't
    silently turn every read into a type error or a score."""
    return item[1] if len(item) == 3 else item[0]


_shared_engine = None
_engine_lock = threading.Lock()
"""Serialises construction and use of the shared engine. The Template tab's
Preview runs OCR on the Tk thread while a Run-tab Preview may be running on
its worker thread; without this, both could race to build their own engine
on first use, and RapidOCR makes no thread-safety promise for concurrent
calls. Per-field inference is ~10ms, so the contention costs nothing
noticeable."""


def _get_shared_engine():
    """Callers must hold _engine_lock."""
    global _shared_engine
    if _shared_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        # use_det=False: each crop is already exactly one field by
        # construction (the operator drew the box around a single value),
        # so the engine's own text-detection stage only gets in the way --
        # observed to fragment bold/embossed digits into several tiny
        # boxes that recognise as garbage instead of reading the whole
        # crop as the single line it is. NB: RapidOCR silently accepts
        # unknown keyword arguments, so a misspelt option (this was once
        # `use_text_det`) leaves detection ON with no error -- producing
        # the scattered single-glyph "0 D D S 2"-style reads that were
        # previously blamed on hardware. test_extract pins the real name.
        #
        # text_score=0.0: the engine's own confidence gate silently drops
        # low-scoring-but-correct reads (a legible value scored ~0.5-0.8
        # was seen filtered out entirely). Per the extraction pipeline's
        # design, the extracted *value* is validated downstream (length,
        # pattern, date parse) rather than trusted on the engine's
        # self-reported confidence, so that gate is redundant here and
        # only costs recall.
        _shared_engine = RapidOCR(use_det=False, text_score=0.0)
    return _shared_engine


# ---------------------------------------------------------------------------
# Postprocess (type-scoped, pure)
# ---------------------------------------------------------------------------


def postprocess_digits(text: str, options: dict[str, Any]) -> str:
    upper = text.upper()
    mapped = "".join(DIGIT_CONFUSION.get(ch, ch) for ch in upper)
    return "".join(ch for ch in mapped if ch.isdigit())


def postprocess_letters(text: str, options: dict[str, Any]) -> str:
    mapped = "".join(LETTER_CONFUSION.get(ch, ch) for ch in text)
    letters = "".join(ch for ch in mapped if ch.isalpha())
    case = options.get("case")
    if case == "upper":
        letters = letters.upper()
    elif case == "lower":
        letters = letters.lower()
    return letters


def postprocess_alphanumeric(text: str, options: dict[str, Any]) -> str:
    kept = "".join(ch for ch in text if ch.isalnum())
    case = options.get("case")
    if case == "upper":
        kept = kept.upper()
    elif case == "lower":
        kept = kept.lower()
    return kept


def postprocess_text(text: str, options: dict[str, Any]) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed


def postprocess_date(text: str, options: dict[str, Any]) -> tuple[str, date | None]:
    """Returns (cleaned_raw_text, parsed_date_or_None)."""
    day_first = bool(options.get("day_first", False))
    cleaned = text.strip()
    dayfirst_formats = ["%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y"]
    monthfirst_formats = ["%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y", "%m/%d/%y", "%m-%d-%y"]
    iso_formats = ["%Y-%m-%d", "%Y/%m/%d"]
    formats = iso_formats + (dayfirst_formats if day_first else monthfirst_formats)
    for fmt in formats:
        try:
            return cleaned, datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return cleaned, None


POSTPROCESSORS = {
    "digits": postprocess_digits,
    "letters": postprocess_letters,
    "alphanumeric": postprocess_alphanumeric,
    "text": postprocess_text,
}


# ---------------------------------------------------------------------------
# Validation (pure)
# ---------------------------------------------------------------------------


def validate_length(value: str, options: dict[str, Any]) -> bool:
    min_len = options.get("min_length")
    max_len = options.get("max_length")
    if min_len is not None and len(value) < min_len:
        return False
    if max_len is not None and len(value) > max_len:
        return False
    return True


def validate_pattern(value: str, options: dict[str, Any]) -> bool:
    pattern = options.get("pattern")
    if not pattern:
        return True
    return re.fullmatch(pattern, value) is not None


def extract_and_validate_field(
    field_name: str,
    field_type: str,
    raw_text: str,
    options: dict[str, Any],
    required: bool,
) -> FieldResult:
    """Applies postprocess + validation for one field's raw OCR text.
    Pure function -- no image or OCR involvement, fully testable."""
    if field_type == "date":
        cleaned, parsed = postprocess_date(raw_text, options)
        if parsed is None:
            if not cleaned and not required:
                return FieldResult("", FieldStatus.OK, raw_text)
            if not cleaned:
                return FieldResult("", FieldStatus.EMPTY_FIELD, raw_text)
            return FieldResult(cleaned, FieldStatus.VALIDATION_FAILED, raw_text)
        if parsed.year < MIN_YEAR or parsed.year > date.today().year + 1:
            return FieldResult(cleaned, FieldStatus.VALIDATION_FAILED, raw_text)
        output_format = options.get("output_format", "%Y-%m-%d")
        value = parsed.strftime(output_format)
        if not validate_pattern(value, options):
            return FieldResult(value, FieldStatus.VALIDATION_FAILED, raw_text)
        return FieldResult(value, FieldStatus.OK, raw_text)

    postprocessor = POSTPROCESSORS.get(field_type)
    if postprocessor is None:
        raise ValueError(f"Unknown field type {field_type!r}")
    value = postprocessor(raw_text, options)

    if not value:
        if required:
            return FieldResult("", FieldStatus.EMPTY_FIELD, raw_text)
        return FieldResult("", FieldStatus.OK, raw_text)

    if field_type == "text":
        if not validate_pattern(value, options):
            return FieldResult(value, FieldStatus.VALIDATION_FAILED, raw_text)
        return FieldResult(value, FieldStatus.OK, raw_text)

    if not validate_length(value, options):
        return FieldResult(value, FieldStatus.VALIDATION_FAILED, raw_text)
    if not validate_pattern(value, options):
        return FieldResult(value, FieldStatus.VALIDATION_FAILED, raw_text)
    return FieldResult(value, FieldStatus.OK, raw_text)
