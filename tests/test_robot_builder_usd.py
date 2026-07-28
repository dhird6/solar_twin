"""The AUTHORED robot geometry, measured (pxr-guarded).

`tests/test_fleet_specs.py` checks the numbers; this checks that the geometry
actually built from them has the envelope those numbers promise. Both are needed:
the specs were already roughly right when the wheels were placed by a fraction of
the body width, which measured 0.844 m against a published 0.670 m.

Skips off the Spark — `usd-core` has no aarch64 wheel, so `pxr` exists only inside
Isaac Sim's bundled Python (see `docs/ENVIRONMENT.md`). Runs under `./python.sh`
and in x86 CI. No SimulationApp needed: this only authors USD.
"""

import pytest

pytest.importorskip("pxr")

from pxr import Usd, UsdGeom  # noqa: E402

from solar_twin.world.fleet_specs import (  # noqa: E402
    CLEARPATH_HUSKY,
    DJI_M350,
    DJI_MAVIC3T,
)
from solar_twin.world.robot_builder import build_quadcopter, build_ugv  # noqa: E402

MODULE_CHORD_M = 2.278


def _bbox(stage, path):
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
    )
    rng = cache.ComputeWorldBound(stage.GetPrimAtPath(path)).ComputeAlignedRange()
    lo, hi = rng.GetMin(), rng.GetMax()
    return (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])


@pytest.fixture
def stage():
    s = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(s, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(s, 1.0)
    return s


def test_the_drone_envelope_is_the_swept_disc_not_the_motor_diagonal(stage):
    """A quadcopter's footprint is set by its PROP TIPS, so the bbox must be the
    swept diameter (1.166 m for an M350), which is larger than the 0.895 m
    motor-to-motor figure — not equal to it, and not 2*arm."""
    build_quadcopter(stage, "/D")
    dx, dy, _ = _bbox(stage, "/D")
    assert dx == pytest.approx(2 * DJI_M350.swept_radius_m, abs=0.01)
    assert dy == pytest.approx(2 * DJI_M350.swept_radius_m, abs=0.01)
    assert dx > DJI_M350.diagonal_m


def test_a_different_preset_actually_changes_the_geometry(stage):
    """Otherwise the spec would be decoration on top of hardcoded literals."""
    build_quadcopter(stage, "/Big")
    build_quadcopter(stage, "/Small", spec=DJI_MAVIC3T)
    assert _bbox(stage, "/Small")[0] < _bbox(stage, "/Big")[0]
    assert _bbox(stage, "/Small")[0] == pytest.approx(2 * DJI_MAVIC3T.swept_radius_m, abs=0.01)


def test_the_rover_overall_width_includes_its_wheels(stage):
    """Regression: wheels were offset by a fraction of the body width, so the
    measured envelope was 0.844 m against a published overall width of 0.670 m —
    26% too wide. A platform's published width is its OVERALL width."""
    build_ugv(stage, "/R")
    dx, _, _ = _bbox(stage, "/R")
    assert dx == pytest.approx(CLEARPATH_HUSKY.body_w_m, abs=0.01)


def test_the_rover_total_height_is_the_top_of_the_machine(stage):
    """Regression: the sensor head was centred ON the stated total height, so the
    machine measured half a head taller than it claimed."""
    build_ugv(stage, "/R")
    _, _, dz = _bbox(stage, "/R")
    assert dz == pytest.approx(CLEARPATH_HUSKY.total_height_m, abs=0.01)


def test_the_rover_length_is_about_the_platform_length(stage):
    build_ugv(stage, "/R")
    _, dy, _ = _bbox(stage, "/R")
    # Bonnet and wheels add a little; it must not drift far from the platform.
    assert CLEARPATH_HUSKY.body_l_m - 0.05 <= dy <= CLEARPATH_HUSKY.body_l_m + 0.08


def test_the_rover_sits_on_the_ground_not_through_it(stage):
    """Origin is at ground level, so nothing may hang below z=0."""
    build_ugv(stage, "/R")
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
    )
    lo = cache.ComputeWorldBound(stage.GetPrimAtPath("/R")).ComputeAlignedRange().GetMin()
    assert lo[2] >= -0.01


def test_neither_machine_is_toy_scale_or_bigger_than_a_module(stage):
    """The concrete complaint this work answers: both must read as a believable
    fraction of a real 2.278 m module chord."""
    build_quadcopter(stage, "/D")
    build_ugv(stage, "/R")
    for path in ("/D", "/R"):
        dx, dy, _ = _bbox(stage, path)
        ratio = max(dx, dy) / MODULE_CHORD_M
        assert 0.2 < ratio < 0.8, f"{path} is {ratio:.2f} of a module chord"


def test_articulated_parts_are_reported_for_the_runtime(stage):
    d = build_quadcopter(stage, "/D")
    r = build_ugv(stage, "/R")
    assert len(d.rotors) == 4 and d.gimbal
    assert len(r.wheels) == 4 and r.gimbal
    for p in d.rotors + r.wheels:
        assert stage.GetPrimAtPath(p).IsValid()
