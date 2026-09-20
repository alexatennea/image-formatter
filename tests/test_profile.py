import json

import pytest

from image_renamer.profile import Field, Profile, ProfileError, ReferenceImage


def make_profile(**overrides):
    defaults = dict(
        profile_name="Delivery notes",
        reference_image=ReferenceImage(3024, 4032, 0.75, "IMG_4471.JPG"),
        fields=[
            Field(name="ref_no", type="digits", rect=(0.1, 0.1, 0.2, 0.2), options={"min_length": 6}),
            Field(name="site_code", type="letters", rect=(0.3, 0.3, 0.4, 0.4)),
        ],
        filename_template="{doc_date}_{site_code}_{ref_no}",
    )
    defaults.update(overrides)
    return Profile(**defaults)


def test_profile_round_trips_through_save_and_load(tmp_path):
    profile = make_profile()
    path = tmp_path / "profile.json"
    profile.save(path)
    loaded = Profile.load(path)
    assert loaded.to_dict() == profile.to_dict()


def test_unknown_schema_version_refuses_to_load(tmp_path):
    profile = make_profile()
    data = profile.to_dict()
    data["schema_version"] = 99
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ProfileError):
        Profile.load(path)


def test_duplicate_field_names_rejected():
    with pytest.raises(ProfileError):
        make_profile(
            fields=[
                Field(name="ref_no", type="digits", rect=(0.1, 0.1, 0.2, 0.2)),
                Field(name="ref_no", type="text", rect=(0.3, 0.3, 0.4, 0.4)),
            ]
        )


def test_invalid_field_name_rejected():
    with pytest.raises(ProfileError):
        Field(name="RefNo", type="digits", rect=(0.1, 0.1, 0.2, 0.2))


def test_non_normalised_rect_rejected():
    with pytest.raises(ProfileError):
        Field(name="ref_no", type="digits", rect=(0.5, 0.1, 0.2, 0.2))


def test_unknown_options_keys_ignored_not_rejected():
    field = Field(
        name="ref_no", type="digits", rect=(0.1, 0.1, 0.2, 0.2),
        options={"min_length": 6, "some_future_key": "x"},
    )
    assert field.options["some_future_key"] == "x"
