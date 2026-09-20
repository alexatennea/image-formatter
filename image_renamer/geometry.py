"""Coordinate conversion between canvas, image and profile (fractional) space,
region padding, and the aspect-ratio layout guard. No GUI imports."""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PAD_FRACTION = 0.02


def canvas_to_profile_rect(
    canvas_rect: tuple[float, float, float, float],
    scale: float,
    image_width: int,
    image_height: int,
) -> tuple[float, float, float, float]:
    """Convert a rectangle drawn on the scaled canvas into normalised
    profile-space fractions of the reference image's own dimensions."""
    cx0, cy0, cx1, cy1 = canvas_rect
    ix0, iy0, ix1, iy1 = cx0 / scale, cy0 / scale, cx1 / scale, cy1 / scale
    x0, x1 = sorted((ix0, ix1))
    y0, y1 = sorted((iy0, iy1))
    return (x0 / image_width, y0 / image_height, x1 / image_width, y1 / image_height)


def profile_rect_to_pixels(
    rect: tuple[float, float, float, float],
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    """Convert a normalised profile rect into pixel coordinates on a
    (possibly differently sized) target image. Rounds outward so a region
    never crops tighter than it was drawn."""
    x0, y0, x1, y1 = rect
    import math

    px0 = max(0, math.floor(x0 * image_width))
    py0 = max(0, math.floor(y0 * image_height))
    px1 = min(image_width, math.ceil(x1 * image_width))
    py1 = min(image_height, math.ceil(y1 * image_height))
    return (px0, py0, px1, py1)


def pad_pixel_rect(
    rect: tuple[int, int, int, int],
    image_width: int,
    image_height: int,
    pad_fraction: float = DEFAULT_PAD_FRACTION,
) -> tuple[int, int, int, int]:
    """Pad a pixel rect outward by pad_fraction of its own width/height,
    clamped to the image bounds."""
    x0, y0, x1, y1 = rect
    w = x1 - x0
    h = y1 - y0
    pad_x = round(w * pad_fraction)
    pad_y = round(h * pad_fraction)
    return (
        max(0, x0 - pad_x),
        max(0, y0 - pad_y),
        min(image_width, x1 + pad_x),
        min(image_height, y1 + pad_y),
    )


def clamp_profile_rect(
    rect: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    """Clamp a profile rect into [0, 1] on save. Returns None if the
    resulting region has zero area (rejected, not silently accepted)."""
    x0, y0, x1, y1 = rect
    x0 = min(max(x0, 0.0), 1.0)
    y0 = min(max(y0, 0.0), 1.0)
    x1 = min(max(x1, 0.0), 1.0)
    y1 = min(max(y1, 0.0), 1.0)
    if x0 >= x1 or y0 >= y1:
        return None
    return (x0, y0, x1, y1)


@dataclass
class LayoutCheck:
    ok: bool
    expected_ratio: float
    actual_ratio: float
    tolerance: float


def check_layout(
    image_width: int,
    image_height: int,
    expected_aspect_ratio: float,
    tolerance: float,
    enabled: bool = True,
) -> LayoutCheck:
    """Compare an image's aspect ratio to the reference. LAYOUT_MISMATCH is
    the caller's concern; this just reports the comparison."""
    actual = image_width / image_height
    if not enabled:
        return LayoutCheck(True, expected_aspect_ratio, actual, tolerance)
    ok = abs(actual - expected_aspect_ratio) <= tolerance
    return LayoutCheck(ok, expected_aspect_ratio, actual, tolerance)
