"""Framing math — the arithmetic behind "open the viewer ON the plant" (Isaac-free)."""

from __future__ import annotations

import math

import pytest

from solar_twin.world.framing import bounds_of, camera_pose_for_bounds, sample_stride


def test_bounds_of_is_axis_aligned():
    lo, hi = bounds_of([(1.0, -2.0, 3.0), (-4.0, 5.0, 0.5)])
    assert lo == (-4.0, -2.0, 0.5)
    assert hi == (1.0, 5.0, 3.0)


def test_bounds_of_rejects_empty():
    with pytest.raises(ValueError):
        bounds_of([])


def test_target_is_box_centre_at_panel_height():
    # Aiming at the vertical centre of a box that includes terrain buries the
    # look-at point underground; the target must sit at the top of the box.
    _, target = camera_pose_for_bounds((0.0, 0.0, -10.0), (100.0, 40.0, 2.0))
    assert target == pytest.approx((50.0, 20.0, 2.0))


def test_eye_is_offset_and_above_the_target():
    eye, target = camera_pose_for_bounds((0.0, 0.0, 0.0), (100.0, 100.0, 2.0))
    assert eye[0] < target[0] and eye[1] < target[1], "eye should sit south-west"
    assert eye[2] > target[2], "eye should look down, not up"


def test_distance_grows_with_the_plant():
    """A 3 km plant must pull the camera back further than a 300 m one."""

    def dist(span: float) -> float:
        eye, target = camera_pose_for_bounds((0.0, 0.0, 0.0), (span, 400.0, 2.0))
        return math.dist(eye[:2], target[:2])

    assert dist(3151.0) > dist(300.0) * 5


def test_whole_span_fits_inside_the_horizontal_fov():
    """The regression that started this: the plant must be INSIDE the frame."""
    fov = 50.0
    lo, hi = (566.0, 0.0, 0.0), (3717.0, 421.0, 2.0)
    eye, target = camera_pose_for_bounds(lo, hi, fov_deg=fov, margin=1.0)
    d = math.dist(eye[:2], target[:2])
    half_visible = d * math.tan(math.radians(fov) / 2.0)
    assert half_visible >= (hi[0] - lo[0]) / 2.0 - 1e-6


def test_degenerate_bounds_do_not_put_the_camera_on_the_target():
    """A single prim has zero span; the camera still needs somewhere to stand."""
    eye, target = camera_pose_for_bounds((5.0, 5.0, 1.0), (5.0, 5.0, 1.0))
    assert math.dist(eye, target) >= 25.0


def test_far_offset_geometry_is_framed_where_it_actually_is():
    """khavda_4block sits 566-3717 m east of the origin — not at it."""
    eye, target = camera_pose_for_bounds((566.0, 0.0, 0.0), (3717.0, 421.0, 2.0))
    assert target[0] == pytest.approx((566.0 + 3717.0) / 2.0)
    assert target[0] > 2000.0, "framing must follow the geometry away from the origin"


@pytest.mark.parametrize(
    "count,max_samples,expected",
    [(10, 2000, 1), (2000, 2000, 1), (117264, 2000, 59), (0, 2000, 1), (10, 0, 1)],
)
def test_sample_stride(count, max_samples, expected):
    assert sample_stride(count, max_samples) == expected


def test_sample_stride_actually_caps_the_sample():
    count, cap = 117264, 2000
    stride = sample_stride(count, cap)
    assert len(range(0, count, stride)) <= cap
