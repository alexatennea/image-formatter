import pytest

from image_renamer.naming import (
    TemplateError,
    build_filename,
    resolve_collision,
    sanitise_stem,
    validate_template,
)


def test_illegal_characters_removed():
    stem = sanitise_stem('a<b>c:d"e/f\\g|h?i*j', max_length=100)
    for ch in '< > : " / \\ | ? *'.split():
        assert ch not in stem


def test_reserved_windows_name_prefixed():
    assert sanitise_stem("CON", max_length=100) == "_CON"
    assert sanitise_stem("con", max_length=100) == "_con"


def test_truncation_keeps_extension_intact():
    name = build_filename(
        "{orig}",
        {},
        orig_stem="x" * 200,
        ext=".jpg",
        index=1,
        max_filename_length=20,
    )
    assert name is not None
    assert name.endswith(".jpg")
    assert len(name) <= 20


def test_template_naming_undefined_field_is_rejected():
    with pytest.raises(TemplateError) as exc_info:
        validate_template("{date}_{ref_no}", {"ref_no"})
    assert "date" in str(exc_info.value)


def test_hash_suffix_deterministic_for_same_file_different_names_for_collision():
    taken = {"a.jpg"}
    result1 = resolve_collision(
        "a", ".jpg", strategy="hash_suffix", file_hash="abcdef1234",
        sequence_index=2, taken=taken, max_filename_length=120,
    )
    assert result1 == "a_abcdef.jpg"

    # same file hash on a rerun produces the same name
    result2 = resolve_collision(
        "a", ".jpg", strategy="hash_suffix", file_hash="abcdef1234",
        sequence_index=2, taken=taken, max_filename_length=120,
    )
    assert result1 == result2

    # a different file colliding on the same proposed name gets a different suffix
    result3 = resolve_collision(
        "a", ".jpg", strategy="hash_suffix", file_hash="000000ffff",
        sequence_index=3, taken=taken, max_filename_length=120,
    )
    assert result3 == "a_000000.jpg"
    assert result3 != result1


def test_empty_sanitised_stem_yields_none():
    assert sanitise_stem("....", max_length=100) == ""


def test_max_filename_length_respected_with_extension():
    name = sanitise_stem("a" * 50, max_length=10)
    assert len(name) <= 10
