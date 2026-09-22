from image_renamer.extract import (
    FieldStatus,
    extract_and_validate_field,
    postprocess_date,
    postprocess_digits,
)


def test_date_parses_day_first():
    _, parsed = postprocess_date("12/03/2026", {"day_first": True})
    assert parsed.isoformat() == "2026-03-12"


def test_date_parses_month_first():
    _, parsed = postprocess_date("12/03/2026", {"day_first": False})
    assert parsed.isoformat() == "2026-12-03"


def test_digits_confusion_map():
    assert postprocess_digits("l23O", {}) == "1230"


def test_empty_digits_field_yields_empty_field_status():
    result = extract_and_validate_field("ref_no", "digits", "", {}, required=True)
    assert result.status == FieldStatus.EMPTY_FIELD
    assert result.value == ""


def test_digits_field_out_of_range_length_fails_validation():
    result = extract_and_validate_field(
        "ref_no", "digits", "12", {"min_length": 6, "max_length": 8}, required=True
    )
    assert result.status == FieldStatus.VALIDATION_FAILED


def test_digits_field_within_length_ok():
    result = extract_and_validate_field(
        "ref_no", "digits", "l234S6", {"min_length": 6, "max_length": 8}, required=True
    )
    assert result.status == FieldStatus.OK
    assert result.value == "123456"


def test_date_field_year_out_of_range_fails():
    result = extract_and_validate_field(
        "doc_date", "date", "12/03/1899", {"day_first": True}, required=True
    )
    assert result.status == FieldStatus.VALIDATION_FAILED


def test_date_field_output_format_applied():
    result = extract_and_validate_field(
        "doc_date", "date", "12/03/2026",
        {"day_first": True, "output_format": "%Y-%m-%d"}, required=True,
    )
    assert result.value == "2026-03-12"
    assert result.status == FieldStatus.OK


def test_pattern_validates_checksum_style_field():
    options = {"pattern": r"[A-Z]{2}\d{4}", "case": "upper"}
    ok = extract_and_validate_field("code", "alphanumeric", "ab1234", options, required=True)
    assert ok.value == "AB1234"
    assert ok.status == FieldStatus.OK

    bad = extract_and_validate_field("code", "text", "wrong", {"pattern": r"^\d+$"}, required=True)
    assert bad.status == FieldStatus.VALIDATION_FAILED


def test_engine_is_built_with_detection_disabled(monkeypatch):
    # RapidOCR swallows unknown kwargs, so a misspelt option name silently
    # leaves text detection on and fragments every field into single-glyph
    # garbage. Pin the exact kwargs the engine is constructed with.
    import sys
    import types

    from image_renamer import extract

    captured = {}

    class FakeRapidOCR:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", types.SimpleNamespace(RapidOCR=FakeRapidOCR))
    monkeypatch.setattr(extract, "_shared_engine", None)
    extract._get_shared_engine()
    assert captured.get("use_det") is False
    assert "use_text_det" not in captured


def test_ocr_engine_reads_both_result_shapes(monkeypatch):
    from PIL import Image

    from image_renamer import extract

    blank = Image.new("L", (10, 10))
    monkeypatch.setattr(extract, "_shared_engine", lambda _img: ([["LUMBER", 0.97]], None))
    assert extract.ocr_engine(blank) == "LUMBER"
    monkeypatch.setattr(extract, "_shared_engine", lambda _img: ([[[[0, 0]] * 4, "LUMBER", 0.97]], None))
    assert extract.ocr_engine(blank) == "LUMBER"


def test_concurrent_first_use_builds_one_engine(monkeypatch):
    import sys
    import threading
    import time
    import types

    from PIL import Image

    from image_renamer import extract

    built = []

    class SlowRapidOCR:
        def __init__(self, **kwargs):
            time.sleep(0.05)  # widen the race window
            built.append(self)

        def __call__(self, _img):
            return [["X", 1.0]], None

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", types.SimpleNamespace(RapidOCR=SlowRapidOCR))
    monkeypatch.setattr(extract, "_shared_engine", None)
    blank = Image.new("L", (10, 10))
    threads = [threading.Thread(target=extract.ocr_engine, args=(blank,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(built) == 1
