"""Covers the output_crop feature: Apply saves a cropped copy under the new
name while backing up the untouched original into `_originals/`, and Undo
restores the original from that backup."""
from PIL import Image

from image_renamer import extract, runner
from image_renamer.profile import Field, LayoutGuard, Profile, ReferenceImage

REF_WIDTH, REF_HEIGHT = 400, 300


def build_profile(output_crop):
    fields = [Field(name="sample", type="text", rect=(0.02, 0.02, 0.3, 0.12))]
    return Profile(
        profile_name="fixture",
        reference_image=ReferenceImage(REF_WIDTH, REF_HEIGHT, REF_WIDTH / REF_HEIGHT, "ref.png"),
        fields=fields,
        filename_template="{sample}",
        layout_guard=LayoutGuard(enabled=True, aspect_tolerance=0.02),
        output_crop=output_crop,
    )


def test_apply_with_output_crop_backs_up_original_and_saves_cropped(tmp_path, monkeypatch):
    folder = tmp_path / "batch"
    folder.mkdir()
    Image.new("RGB", (REF_WIDTH, REF_HEIGHT), "red").save(folder / "IMG_0001.jpg")

    monkeypatch.setattr(extract, "ocr_engine", lambda image: "SAMPLEA")

    profile = build_profile(output_crop=(0.1, 0.1, 0.5, 0.5))
    rows = runner.preview(str(folder), profile)
    assert rows[0].status == runner.STATUS_OK

    report = runner.apply(rows, str(folder), profile)
    assert len(report.renamed) == 1
    assert not report.failed

    original_backup = folder / runner.ORIGINALS_SUBFOLDER / "IMG_0001.jpg"
    assert original_backup.exists()
    with Image.open(original_backup) as backup_img:
        assert backup_img.size == (REF_WIDTH, REF_HEIGHT)

    cropped_path = folder / "SAMPLEA.jpg"
    assert cropped_path.exists()
    with Image.open(cropped_path) as cropped_img:
        assert cropped_img.size == (int(0.4 * REF_WIDTH), int(0.4 * REF_HEIGHT))

    assert not (folder / "IMG_0001.jpg").exists()

    manifest_files = list(folder.glob("_rename_manifest_*.csv"))
    assert len(manifest_files) == 1
    assert "output_cropped" in manifest_files[0].read_text()


def test_undo_restores_original_from_backup_when_cropped(tmp_path, monkeypatch):
    folder = tmp_path / "batch"
    folder.mkdir()
    Image.new("RGB", (REF_WIDTH, REF_HEIGHT), "blue").save(folder / "IMG_0001.jpg")

    monkeypatch.setattr(extract, "ocr_engine", lambda image: "SAMPLEA")

    profile = build_profile(output_crop=(0.1, 0.1, 0.5, 0.5))
    rows = runner.preview(str(folder), profile)
    runner.apply(rows, str(folder), profile)

    assert (folder / "SAMPLEA.jpg").exists()
    assert not (folder / "IMG_0001.jpg").exists()

    undo_report = runner.undo_last_run(str(folder))
    assert undo_report.restored == ["IMG_0001.jpg"]
    assert (folder / "IMG_0001.jpg").exists()
    assert not (folder / "SAMPLEA.jpg").exists()
    with Image.open(folder / "IMG_0001.jpg") as restored_img:
        assert restored_img.size == (REF_WIDTH, REF_HEIGHT)


def test_apply_without_output_crop_is_a_plain_rename(tmp_path, monkeypatch):
    folder = tmp_path / "batch"
    folder.mkdir()
    Image.new("RGB", (REF_WIDTH, REF_HEIGHT), "green").save(folder / "IMG_0001.jpg")

    monkeypatch.setattr(extract, "ocr_engine", lambda image: "SAMPLEA")

    profile = build_profile(output_crop=None)
    rows = runner.preview(str(folder), profile)
    runner.apply(rows, str(folder), profile)

    assert (folder / "SAMPLEA.jpg").exists()
    assert not (folder / runner.ORIGINALS_SUBFOLDER).exists()
