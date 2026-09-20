from image_renamer.geometry import (
    canvas_to_profile_rect,
    check_layout,
    clamp_profile_rect,
    pad_pixel_rect,
    profile_rect_to_pixels,
)


def test_region_drawn_on_canvas_reprojects_correctly_on_different_sized_image():
    # Reference image is 1000x2000 displayed at scale 0.5 -> canvas 500x1000.
    # Operator draws a rect from (100, 100) to (200, 300) in canvas pixels.
    canvas_rect = (100, 100, 200, 300)
    scale = 0.5
    ref_width, ref_height = 1000, 2000
    profile_rect = canvas_to_profile_rect(canvas_rect, scale, ref_width, ref_height)
    assert profile_rect == (0.2, 0.1, 0.4, 0.3)

    # Applied to a differently-sized image of the same layout (2000x4000),
    # the pixel rect should scale proportionally.
    target_width, target_height = 2000, 4000
    px_rect = profile_rect_to_pixels(profile_rect, target_width, target_height)
    assert px_rect == (400, 400, 800, 1200)


def test_profile_rect_to_pixels_rounds_outward():
    # A rect that doesn't divide evenly should never crop tighter than drawn.
    rect = (0.1, 0.1, 0.33333, 0.33333)
    px = profile_rect_to_pixels(rect, 100, 100)
    assert px[0] <= 10 and px[1] <= 10
    assert px[2] >= 34 and px[3] >= 34


def test_pad_pixel_rect_clamps_to_bounds():
    rect = (0, 0, 10, 10)
    padded = pad_pixel_rect(rect, image_width=10, image_height=10, pad_fraction=0.5)
    assert padded == (0, 0, 10, 10)


def test_clamp_profile_rect_rejects_zero_area():
    assert clamp_profile_rect((0.5, 0.5, 0.5, 0.6)) is None
    assert clamp_profile_rect((-0.1, -0.1, 1.5, 1.5)) == (0.0, 0.0, 1.0, 1.0)


def test_layout_guard_within_tolerance_ok():
    result = check_layout(3024, 4032, expected_aspect_ratio=0.75, tolerance=0.02)
    assert result.ok


def test_layout_guard_outside_tolerance_fails():
    # Landscape shot mixed into a portrait batch.
    result = check_layout(4032, 3024, expected_aspect_ratio=0.75, tolerance=0.02)
    assert not result.ok
