"""Turbine keep-out geometry + SafeControl enforcement (no Isaac)."""

import math

from solar_twin.control.base import RobotControl, Waypoint
from solar_twin.control.safe import SafeControl
from solar_twin.world.keepout import (
    build_keepouts,
    clamp_out,
    segment_clear,
    worst_violation,
)

FARM = {
    "grid": {"rows": 1, "cols": 10, "row_pitch": 6.0, "col_pitch": 2.2},
    "georef": {"lat0": 0.0, "lon0": 0.0},
    "terrain": {"kind": "flat"},
    "turbines": [
        {"pos": [9.5, -14.0], "hub_height": 18.0, "blade_len": 8.0, "rpm": 12.0},
    ],
}


def test_build_keepouts_places_sphere_at_hub():
    ko = build_keepouts(FARM)
    assert len(ko) == 1
    k = ko[0]
    assert k.hub == (9.5, -14.0, 18.0)          # flat terrain -> gz=0
    assert k.rotor_radius == 8.0 + 2.0          # blade_len + default margin


def test_point_inside_and_outside_rotor_sphere():
    k = build_keepouts(FARM)[0]
    # Dead centre of the hub is deep inside (penetration ~ rotor_radius).
    assert k.violation(9.5, -14.0, 18.0) > 0
    assert not k.clears(9.5, -14.0, 18.0)
    # A point 50 m away is clear.
    assert k.clears(9.5, -14.0, 80.0)
    assert worst_violation([k], 9.5, -14.0, 80.0) < 0


def test_clamp_pushes_a_violating_point_just_outside():
    ko = build_keepouts(FARM)
    # A point 3 m above the hub is inside the 10 m sphere.
    p = (9.5, -14.0, 21.0)
    assert worst_violation(ko, *p) > 0
    sx, sy, sz = clamp_out(ko, *p)
    # Now outside (with a tiny epsilon margin).
    assert worst_violation(ko, sx, sy, sz) <= 0.01
    # Pushed straight up (same x,y, higher z than the hub).
    assert math.isclose(sx, 9.5) and math.isclose(sy, -14.0)
    assert sz > 18.0


def test_segment_through_rotor_is_flagged():
    ko = build_keepouts(FARM)
    # A path that passes straight through the hub is NOT clear.
    assert not segment_clear(ko, (0.0, -14.0, 18.0), (20.0, -14.0, 18.0))
    # A path well above everything is clear.
    assert segment_clear(ko, (0.0, 0.0, 60.0), (20.0, 0.0, 60.0))


class _RecordingControl(RobotControl):
    def __init__(self):
        self.moves = []

    def move_to(self, robot_id, wp):
        self.moves.append((robot_id, wp))

    def at_goal(self, robot_id, wp, tol=0.05):
        return True


def test_safecontrol_clamps_and_records_violation():
    ko = build_keepouts(FARM)
    inner = _RecordingControl()
    ctl = SafeControl(inner, ko)
    # Command a waypoint inside the rotor sphere.
    ctl.move_to("drone1", Waypoint(9.5, -14.0, 21.0))
    assert len(ctl.events) == 1                      # violation recorded
    passed = inner.moves[-1][1]                      # what actually reached the drone
    assert worst_violation(ko, passed.x, passed.y, passed.z) <= 0.01  # clamped safe


def test_safecontrol_passes_safe_waypoint_untouched():
    ko = build_keepouts(FARM)
    inner = _RecordingControl()
    ctl = SafeControl(inner, ko)
    ctl.move_to("drone1", Waypoint(0.0, 0.0, 3.0))   # over a panel, far from turbine
    assert ctl.events == []
    assert inner.moves[-1][1] == Waypoint(0.0, 0.0, 3.0)
    assert ctl.min_clearance_m > 0
