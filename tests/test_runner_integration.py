"""Integration tests against generated fixture images with known text,
using a fake OCR engine so no OCR install is required."""
from __future__ import annotations

from PIL import Image, ImageDraw

from image_renamer import extract, runner
from image_renamer.profile import Field, LayoutGuard, Profile, ReferenceImage

REF_WIDTH, REF_HEIGHT = 400, 300


def make_fixture_image(path, ref_no: str, code: str, rotate: bool = False):
    img = Image.new("RGB", (REF_WIDTH, REF_HEIGHT), "white")
    draw = ImageDraw.Draw(img)
    draw.text((20, 20), ref_no, fill="black")
    draw.text((20, 120), code, fill="black")
    if rotate:
        # Simulate a phone photo with EXIF rotation rather than pixel rotation:
        # PIL has no simple way to inject EXIF here, so this fixture instead
        # rotates pixels 180 and we assert extraction still matches when we
        # feed both variants through the same fake OCR lookup keyed by path.
        img = img.rotate(180)
    img.save(path)


def install_fake_ocr(monkeypatch, lookup: dict[str, str]):
    """Fakes OCR by keying off a per-crop-position marker: we cheat by
    stashing the expected raw text for each field rect in `lookup` and have
    the fake engine just look up based on crop size/position bucket."""

    def fake_ocr(image):
        # The two fields are drawn far apart vertically; use crop height
        # position stored via image.info to disambiguate in this fixture.
        return image.info.get("expected_text", "")

    monkeypatch.setattr(extract, "ocr_engine", fake_ocr)


def build_profile(tmp_path):
    fields = [
        Field(name="ref_no", type="digits", rect=(0.02, 0.02, 0.3, 0.12), options={"min_length": 4, "max_length": 8}),
        Field(name="site_code", type="letters", rect=(0.02, 0.4, 0.3, 0.5), options={"case": "upper"}),
    ]
    return Profile(
        profile_name="fixture",
        reference_image=ReferenceImage(REF_WIDTH, REF_HEIGHT, REF_WIDTH / REF_HEIGHT, "ref.png"),
        fields=fields,
        filename_template="{site_code}_{ref_no}",
        layout_guard=LayoutGuard(enabled=True, aspect_tolerance=0.02),
    )


def test_preview_and_apply_and_manifest_and_undo(tmp_path, monkeypatch):
    folder = tmp_path / "batch"
    folder.mkdir()

    # Two fixture files with distinguishable, deterministic "extracted" text
    # via a crop-preprocess monkeypatch keyed by field rect position.
    for i in range(1, 3):
        make_fixture_image(folder / f"IMG_{i:04d}.jpg", ref_no=f"{1000 + i}", code=f"ST{i}")

    profile = build_profile(tmp_path)

    def fake_crop_field(image, rect, pad_fraction=0.02):
        # rect distinguishes the two fields by y0
        return ("ref_no_zone" if rect[1] < 0.2 else "site_code_zone", image)

    call_state = {}

    def fake_preprocess(crop_tuple, upscale=2.0):
        return crop_tuple

    def fake_ocr(crop_tuple):
        zone, image = crop_tuple
        # Recover which fixture image this came from using its path, stashed
        # by load_image via a side channel set below.
        path = call_state["current_path"]
        index = int(path.stem.split("_")[1])
        if zone == "ref_no_zone":
            return str(1000 + index)
        return f"STA{index}"

    orig_load_image = extract.load_image

    def fake_load_image(path):
        call_state["current_path"] = __import__("pathlib").Path(path)
        return orig_load_image(path)

    monkeypatch.setattr(extract, "crop_field", fake_crop_field)
    monkeypatch.setattr(extract, "preprocess_for_ocr", fake_preprocess)
    monkeypatch.setattr(extract, "ocr_engine", fake_ocr)
    monkeypatch.setattr(extract, "load_image", fake_load_image)

    rows = runner.preview(str(folder), profile)
    assert len(rows) == 2
    assert all(r.status == runner.STATUS_OK for r in rows)
    proposed = {r.original_name: r.proposed_name for r in rows}
    # site_code is a `letters` field: the confusion map runs before the
    # alpha-only filter, so trailing digits 1/2 become I/Z respectively.
    assert proposed["IMG_0001.jpg"] == "STAI_1001.jpg"
    assert proposed["IMG_0002.jpg"] == "STAZ_1002.jpg"

    report = runner.apply(rows, str(folder), profile)
    assert len(report.renamed) == 2
    assert not report.failed
    assert (folder / "STAI_1001.jpg").exists()
    assert (folder / "STAZ_1002.jpg").exists()
    assert not (folder / "IMG_0001.jpg").exists()

    manifest_files = list(folder.glob("_rename_manifest_*.csv"))
    assert len(manifest_files) == 1
    manifest_text = manifest_files[0].read_text()
    assert "extracted_ref_no" in manifest_text
    assert "extracted_site_code" in manifest_text

    # Re-running preview should mark both as already processed.
    rows2 = runner.preview(str(folder), profile)
    assert all(r.status == runner.STATUS_ALREADY_PROCESSED for r in rows2)

    undo_report = runner.undo_last_run(str(folder))
    assert set(undo_report.restored) == {"IMG_0001.jpg", "IMG_0002.jpg"}
    assert (folder / "IMG_0001.jpg").exists()
    assert (folder / "IMG_0002.jpg").exists()
    assert not (folder / "STAI_1001.jpg").exists()


def test_layout_mismatch_row_not_extracted(tmp_path, monkeypatch):
    folder = tmp_path / "batch2"
    folder.mkdir()
    # A landscape image where the profile expects roughly this aspect ratio's
    # inverse triggers LAYOUT_MISMATCH.
    img = Image.new("RGB", (300, 400), "white")
    img.save(folder / "bad.jpg")

    profile = build_profile(tmp_path)  # expects 400x300 -> aspect ~1.333
    rows = runner.preview(str(folder), profile)
    assert len(rows) == 1
    assert rows[0].status == runner.STATUS_LAYOUT_MISMATCH
