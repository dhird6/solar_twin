"""Wind reaching the camera in the inspection loop — Isaac-free.

`khavda_windy_hover.yaml` records why wind never appeared in an inspection run: a
force applied to a kinematically-driven body does nothing, so a scenario that
declares `wind:` against the normal mission would *look* like it modelled gusts and
measurably would not. These tests pin the narrow thing that is honest instead — the
commanded pose and the achieved pose differ, by a realistic and reproducible amount —
and they pin the boundaries of the claim just as hard, because the failure mode here
is not a wrong number, it is a number that sounds like flight dynamics.
"""

from __future__ import annotations

import math

import pytest

from solar_twin.control.base import Waypoint
from solar_twin.control.wind_drift import (
    DEFAULT_HOLD_STIFFNESS_N_PER_M,
    HoldModel,
    WindDisturbedControl,
    drift_at,
)
from solar_twin.world.fleet_specs import DJI_M350
from solar_twin.world.windfield import WindField

#: A steady 12 m/s south-westerly, matching `khavda_windy_hover.yaml`.
STEADY = WindField(mean_speed_ms=12.0, wind_dir_deg=225.0, seed=7)
GUSTY = WindField(
    mean_speed_ms=12.0,
    wind_dir_deg=225.0,
    speed_variation=0.35,
    direction_variation_deg=15.0,
    gust_period_s=4.0,
    seed=7,
)
DRONE = HoldModel.for_drone(DJI_M350)


class FakeInner:
    """Records what the real controller was actually told to do."""

    def __init__(self):
        self.moves: list[tuple[str, Waypoint]] = []
        self.pose = {}
        self.reset_calls = 0

    def move_to(self, robot_id, waypoint):
        self.moves.append((robot_id, waypoint))
        self.pose[robot_id] = waypoint

    def at_goal(self, robot_id, waypoint, tol=0.05):
        p = self.pose.get(robot_id)
        if p is None:
            return False
        return (
            abs(p.x - waypoint.x) <= tol
            and abs(p.y - waypoint.y) <= tol
            and abs(p.z - waypoint.z) <= tol
        )

    def reset(self):
        self.reset_calls += 1


# --------------------------------------------------------------------------- #
# The drift model
# --------------------------------------------------------------------------- #


def test_drone_frontal_area_comes_from_published_dimensions():
    """0.42 x 0.43 m body box. Traced to a dimension sheet rather than typed in
    beside the drag model — and a lower bound, as its docstring says."""
    assert DJI_M350.frontal_area_m2 == pytest.approx(0.42 * 0.43)


def test_drift_is_drag_over_stiffness_with_the_arithmetic_the_docstring_claims():
    """The constant `DEFAULT_HOLD_STIFFNESS_N_PER_M` is justified in its docstring by
    a worked example: ~15.9 N on an M350 at 12 m/s, giving ~0.20 m at 80 N/m. If the
    code and that example ever disagree, one of them is lying to a reader."""
    dx, dy, dz = drift_at(STEADY, 0.0, 0.0, 10.0, DRONE)
    drag = 0.5 * 1.225 * 1.0 * DJI_M350.frontal_area_m2 * 12.0**2
    assert drag == pytest.approx(15.9, abs=0.2)
    assert math.hypot(dx, dy) == pytest.approx(drag / DEFAULT_HOLD_STIFFNESS_N_PER_M, rel=1e-6)
    assert math.hypot(dx, dy) == pytest.approx(0.20, abs=0.02)


def test_drift_pushes_downwind():
    """A south-westerly (met convention: blowing FROM 225) must push the drone
    toward the north-east. Getting this backwards is invisible in a magnitude test
    and obvious in a rendered frame, so it is asserted here instead."""
    dx, dy, _ = drift_at(STEADY, 0.0, 0.0, 10.0, DRONE)
    assert dx > 0.0 and dy > 0.0  # +x is east, +y is north


def test_drift_grows_with_the_square_of_wind_speed():
    slow = drift_at(WindField(mean_speed_ms=6.0, wind_dir_deg=225.0), 0, 0, 10, DRONE)
    fast = drift_at(WindField(mean_speed_ms=12.0, wind_dir_deg=225.0), 0, 0, 10, DRONE)
    assert math.hypot(*fast[:2]) == pytest.approx(4.0 * math.hypot(*slow[:2]), rel=1e-6)


def test_no_wind_means_no_drift():
    """The off switch has to be exact: a calm scenario must reproduce the undisturbed
    run byte-for-byte, or every KPI recorded before this change becomes unquotable."""
    calm = WindField(mean_speed_ms=0.0)
    assert drift_at(calm, 0.0, 0.0, 10.0, DRONE) == (0.0, 0.0, 0.0)


def test_gusts_move_the_drone_over_time_and_repeat_exactly():
    """Two halves of one requirement (`RISK-23`): the disturbance must vary with time,
    and the same seed must give the same variation."""
    a = [drift_at(GUSTY, 0.0, 0.0, 10.0, DRONE, t=t) for t in (0.0, 1.0, 2.0, 3.0)]
    assert len({tuple(round(v, 6) for v in d) for d in a}) > 1, "gust never varies"

    same = WindField(
        mean_speed_ms=12.0,
        wind_dir_deg=225.0,
        speed_variation=0.35,
        direction_variation_deg=15.0,
        gust_period_s=4.0,
        seed=7,
    )
    b = [drift_at(same, 0.0, 0.0, 10.0, DRONE, t=t) for t in (0.0, 1.0, 2.0, 3.0)]
    assert a == b


def test_drift_is_order_independent():
    """`windfield` samples a closed form of t precisely so two robots queried in
    either order see the same air. That property has to survive this layer."""
    first = drift_at(GUSTY, 10.0, 20.0, 5.0, DRONE, t=3.0)
    _ = drift_at(GUSTY, -50.0, 99.0, 30.0, DRONE, t=11.0)
    again = drift_at(GUSTY, 10.0, 20.0, 5.0, DRONE, t=3.0)
    assert first == again


def test_wake_shelters_a_drone_downwind_of_a_turbine():
    """The wake deficit already in `WindField` must show up as *less* drift, not more
    — a drone in a turbine's shadow sees slower air."""
    from solar_twin.world.windfield import WakeSource

    sheltered = WindField(
        mean_speed_ms=12.0,
        wind_dir_deg=270.0,  # westerly: travels east
        wakes=(WakeSource(x=0.0, y=0.0, hub_height_m=20.0, rotor_diameter_m=40.0),),
    )
    open_air = WindField(mean_speed_ms=12.0, wind_dir_deg=270.0)
    at = (60.0, 0.0, 20.0)  # downwind of the rotor, at hub height
    assert math.hypot(*drift_at(sheltered, *at, DRONE)[:2]) < math.hypot(
        *drift_at(open_air, *at, DRONE)[:2]
    )


def test_ground_bot_is_not_lifted():
    """`vertical=False` is how a ground vehicle opts out of being blown upward, which
    a rover held on the terrain plainly is not."""
    rover = HoldModel(drag_area_m2=0.5, vertical=False)
    updraft = WindField(mean_speed_ms=8.0, wind_dir_deg=225.0, vertical_ms=5.0)
    assert drift_at(updraft, 0.0, 0.0, 1.0, rover)[2] == 0.0
    assert drift_at(updraft, 0.0, 0.0, 1.0, HoldModel(drag_area_m2=0.5))[2] != 0.0


def test_absurd_wind_is_clamped_rather_than_flying_through_a_panel():
    model = HoldModel(drag_area_m2=1.0, max_drift_m=0.5)
    gale = WindField(mean_speed_ms=60.0, wind_dir_deg=225.0)
    d = drift_at(gale, 0.0, 0.0, 10.0, model)
    assert math.sqrt(sum(v * v for v in d)) == pytest.approx(0.5)


def test_clamp_is_on_magnitude_not_per_axis():
    """A diagonal gust must not be allowed to displace the vehicle sqrt(2) times
    further than a head-on one just because each axis is separately under the cap."""
    model = HoldModel(drag_area_m2=1.0, max_drift_m=0.5)
    for bearing in (225.0, 270.0, 0.0, 47.0):
        d = drift_at(WindField(mean_speed_ms=60.0, wind_dir_deg=bearing), 0, 0, 10, model)
        assert math.sqrt(sum(v * v for v in d)) == pytest.approx(0.5)


def test_rejects_a_stiffness_that_would_divide_by_zero():
    with pytest.raises(ValueError, match="hold_stiffness"):
        HoldModel(drag_area_m2=0.2, hold_stiffness_n_per_m=0.0)


# --------------------------------------------------------------------------- #
# The controller wrapper
# --------------------------------------------------------------------------- #


def _wrap(field=GUSTY, **kw):
    inner = FakeInner()
    return inner, WindDisturbedControl(inner, field, {"drone": DRONE}, **kw)


def test_the_inner_controller_is_commanded_an_offset_pose():
    """The whole point: the mission asks for a pose, the vehicle is put somewhere
    else. If this passes through unchanged, nothing downstream can differ."""
    inner, ctl = _wrap()
    ctl.move_to("drone", Waypoint(10.0, 20.0, 5.0))
    (_, got), = inner.moves
    assert (got.x, got.y, got.z) != (10.0, 20.0, 5.0)
    assert math.dist((got.x, got.y, got.z), (10.0, 20.0, 5.0)) == pytest.approx(
        math.sqrt(sum(v * v for v in ctl.last_drift["drone"]))
    )


def test_yaw_is_untouched_because_attitude_is_not_modelled():
    """Stated in the module docstring as a limit; asserted here so it cannot silently
    acquire a made-up attitude term later."""
    inner, ctl = _wrap()
    ctl.move_to("drone", Waypoint(1.0, 2.0, 3.0, yaw=0.75))
    assert inner.moves[0][1].yaw == pytest.approx(0.75)


def test_a_robot_without_a_model_passes_through_untouched():
    """How the ground bot opts out — and how a calm run stays byte-identical."""
    inner, ctl = _wrap()
    ctl.move_to("bot", Waypoint(3.0, 4.0, 0.5))
    got = inner.moves[0][1]
    assert (got.x, got.y, got.z, got.yaw) == (3.0, 4.0, 0.5, 0.0)
    assert "bot" not in ctl.last_drift


def test_at_goal_is_true_at_the_pose_the_wind_allowed():
    """The mission-hang guard. Drift (0.2 m) is far larger than the default tolerance
    (0.05 m), so testing the commanded waypoint would leave the FSM waiting forever
    for a drone that had already arrived wherever the wind let it."""
    inner, ctl = _wrap()
    wp = Waypoint(10.0, 20.0, 5.0)
    ctl.move_to("drone", wp)
    assert ctl.at_goal("drone", wp)
    assert not inner.at_goal("drone", wp)  # ...and it is genuinely NOT at the command


def test_at_goal_does_not_advance_the_gust_clock():
    """`at_goal` may be polled any number of times; if each poll advanced the weather,
    the disturbance would depend on how often the FSM happened to ask."""
    inner, ctl = _wrap()
    wp = Waypoint(10.0, 20.0, 5.0)
    ctl.move_to("drone", wp)
    before = ctl.last_drift["drone"]
    for _ in range(5):
        assert ctl.at_goal("drone", wp)
    assert ctl.last_drift["drone"] == before


def test_the_gust_advances_between_waypoints():
    """Successive panels must be inspected under different air, or the 'disturbance'
    is a fixed offset and every frame carries the identical error."""
    inner, ctl = _wrap(seconds_per_move=4.0)
    seen = set()
    for i in range(6):
        ctl.move_to("drone", Waypoint(float(i), 0.0, 5.0))
        seen.add(tuple(round(v, 6) for v in ctl.last_drift["drone"]))
    assert len(seen) > 1


def test_an_injected_clock_wins_over_the_move_counter():
    inner, ctl = _wrap(time_source=lambda: 2.5)
    ctl.move_to("drone", Waypoint(0.0, 0.0, 5.0))
    expected = drift_at(GUSTY, 0.0, 0.0, 5.0, DRONE, t=2.5)
    assert ctl.last_drift["drone"] == pytest.approx(expected)


def test_reset_restarts_the_weather_for_the_next_repeat():
    """`run.py --repeat` compares repeats against each other. If repeat 2 started
    mid-gust, `variance.json` would attribute weather to the decoder (`RISK-23`)."""
    inner, ctl = _wrap()
    first = []
    for i in range(3):
        ctl.move_to("drone", Waypoint(float(i), 0.0, 5.0))
        first.append(ctl.last_drift["drone"])
    ctl.reset()
    assert ctl.max_drift_seen_m == 0.0
    assert inner.reset_calls == 1  # the tally below us is cleared too
    second = []
    for i in range(3):
        ctl.move_to("drone", Waypoint(float(i), 0.0, 5.0))
        second.append(ctl.last_drift["drone"])
    assert first == second


def test_summary_reports_the_disturbance_and_names_its_limits():
    """A disturbance nobody can read is indistinguishable from one that never
    happened — and the run record must not let a reader mistake this for dynamics."""
    inner, ctl = _wrap()
    ctl.move_to("drone", Waypoint(10.0, 20.0, 5.0))
    s = ctl.summary()
    assert s["max_drift_m"] > 0.0
    assert s["mean_wind_ms"] == 12.0
    assert "NOT flight dynamics" in s["model"]


def test_calm_air_leaves_the_commanded_pose_exactly_alone():
    """The regression that protects every KPI measured before this existed."""
    inner, ctl = _wrap(field=WindField(mean_speed_ms=0.0))
    ctl.move_to("drone", Waypoint(10.0, 20.0, 5.0))
    got = inner.moves[0][1]
    assert (got.x, got.y, got.z) == (10.0, 20.0, 5.0)
    assert ctl.max_drift_seen_m == 0.0


def test_it_wraps_safe_control_so_keepouts_vet_the_drifted_pose():
    """Stack order is load-bearing, and it is the reason this is asserted rather than
    only documented: with the wind OUTSIDE, `SafeControl` clamps the pose the vehicle
    will really hold, and a gust that pushes a cleared waypoint into the rotor volume
    is caught and logged instead of flown."""
    from solar_twin.control.safe import SafeControl
    from solar_twin.world.keepout import TurbineKeepout

    inner = FakeInner()
    model = HoldModel(drag_area_m2=1.0)
    gale = WindField(mean_speed_ms=40.0, wind_dir_deg=225.0)
    commanded = Waypoint(10.0, 20.0, 5.0)

    # Put the forbidden sphere exactly where the wind will carry the drone, and
    # nowhere near where the mission aimed it — so the assertion below can only pass
    # if the DRIFTED pose was the thing vetted.
    dx, dy, dz = drift_at(gale, commanded.x, commanded.y, commanded.z, model)
    hub = (commanded.x + dx, commanded.y + dy, commanded.z + dz)
    keepout = TurbineKeepout(
        hub=hub,
        rotor_radius=1.0,
        tower_xy=(hub[0], hub[1]),
        tower_bottom_z=0.0,
        tower_top_z=1.0,  # well below the drone: the sphere is what must catch it
        tower_radius=0.5,
    )
    assert keepout.clears(commanded.x, commanded.y, commanded.z), (
        "the commanded waypoint must be safe, or this test proves nothing about drift"
    )

    ctl = WindDisturbedControl(SafeControl(inner, [keepout]), gale, {"drone": model})
    ctl.move_to("drone", commanded)
    assert ctl.inner.events, "the wind-displaced pose was never vetted against keep-outs"
