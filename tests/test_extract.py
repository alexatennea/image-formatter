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
