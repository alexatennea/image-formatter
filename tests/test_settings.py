from image_renamer import settings


def test_round_trip(tmp_path):
    path = tmp_path / "sub" / "settings.json"
    settings.save({settings.LAST_FOLDER: "C:/batch"}, path)
    assert settings.load(path) == {settings.LAST_FOLDER: "C:/batch"}


def test_missing_or_corrupt_file_loads_empty(tmp_path):
    assert settings.load(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert settings.load(bad) == {}
    bad.write_text("[1, 2]", encoding="utf-8")
    assert settings.load(bad) == {}


def test_nearest_existing_dir_walks_up_from_a_gone_folder(tmp_path):
    batch = tmp_path / "RAW - Bench 1" / "2026_10_13"
    batch.mkdir(parents=True)
    assert settings.nearest_existing_dir(str(batch)) == str(batch)
    assert settings.nearest_existing_dir(str(batch / "gone" / "deeper")) == str(batch)
    assert settings.nearest_existing_dir("") is None
    assert settings.nearest_existing_dir(None) is None
