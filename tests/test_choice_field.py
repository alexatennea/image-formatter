from PIL import Image

from image_renamer import extract, runner
from image_renamer.profile import Field, LayoutGuard, Profile, ReferenceImage

REF_WIDTH, REF_HEIGHT = 400, 300


def build_profile():
    fields = [
        Field(name="sample", type="text", rect=(0.02, 0.02, 0.3, 0.12)),
        Field(
            name="condition", type="choice", rect=(0.02, 0.4, 0.3, 0.5),
            options={"choices": ["intact", "split"], "default": "intact"},
        ),
    ]
    return Profile(
        profile_name="fixture",
        reference_image=ReferenceImage(REF_WIDTH, REF_HEIGHT, REF_WIDTH / REF_HEIGHT, "ref.png"),
        fields=fields,
        filename_template="{sample}_{condition}",
        layout_guard=LayoutGuard(enabled=True, aspect_tolerance=0.02),
    )


def test_choice_field_seeds_default_without_ocr(tmp_path, monkeypatch):
    folder = tmp_path / "batch"
    folder.mkdir()
    Image.new("RGB", (REF_WIDTH, REF_HEIGHT), "white").save(folder / "IMG_0001.jpg")

    def fake_ocr(image):
        return "SAMPLEA"

    monkeypatch.setattr(extract, "ocr_engine", fake_ocr)

    profile = build_profile()
    rows = runner.preview(str(folder), profile)
    assert len(rows) == 1
    row = rows[0]
    assert row.status == runner.STATUS_OK
    assert row.extracted["condition"] == "intact"
    assert row.proposed_name == "SAMPLEA_intact.jpg"


def test_cycle_choice_value_wraps_around():
    assert runner.cycle_choice_value("intact", ["intact", "split"]) == "split"
    assert runner.cycle_choice_value("split", ["intact", "split"]) == "intact"
    assert runner.cycle_choice_value("unknown", ["intact", "split"]) == "intact"


def test_recompute_row_filename_after_toggle(tmp_path, monkeypatch):
    folder = tmp_path / "batch"
    folder.mkdir()
    Image.new("RGB", (REF_WIDTH, REF_HEIGHT), "white").save(folder / "IMG_0001.jpg")

    monkeypatch.setattr(extract, "ocr_engine", lambda image: "SAMPLEA")

    profile = build_profile()
    rows = runner.preview(str(folder), profile)
    row = rows[0]
    assert row.proposed_name == "SAMPLEA_intact.jpg"

    row.extracted["condition"] = runner.cycle_choice_value(row.extracted["condition"], ["intact", "split"])
    runner.recompute_row_filename(row, profile, rows, str(folder))
    assert row.proposed_name == "SAMPLEA_split.jpg"
    assert row.status == runner.STATUS_OK
