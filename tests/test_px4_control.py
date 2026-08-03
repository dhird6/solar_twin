"""`control/px4.py` — PX4 offboard behind the `RobotControl` ABC. No Isaac, no PX4.

The frame conversion gets the most attention here on purpose: ENU->NED is the
classic PX4 integration bug, it never raises, and its symptom (a drone flying to
a mirrored position, or descending when told to climb) reads as a tuning problem.
"""

from __future__ import annotations

import math

import pytest

from solar_twin.control.base import RobotControl, Waypoint
from solar_twin.control.px4 import (
    PX4Control,
    enu_to_ned,
    ned_to_enu,
    yaw_enu_to_heading,
)


class FakeLink:
    """Records setpoints; reports whatever NED position the test dictates."""

    def __init__(self, position=None):
        self.setpoints: list[tuple[float, float, float, float]] = []
        self.offboard_calls = 0
        self._position = position

    def send_setpoint(self, north, east, down, heading):
        self.setpoints.append((north, east, down, heading))

    def local_position(self):
        return self._position

    def set_offboard(self):
        self.offboard_calls += 1

    def arrive_at_enu(self, x, y, z):
        """Pretend PX4 reports being at this ENU point."""
        n, e, d = enu_to_ned(x, y, z)
        self._position = (n, e, d)


class _Recorder(RobotControl):
    """Stands in for the kinematic controller a mixed fleet falls back to."""

    def __init__(self):
        self.moves: list[tuple[str, Waypoint]] = []

    def move_to(self, robot_id, waypoint):
        self.moves.append((robot_id, waypoint))

    def at_goal(self, robot_id, waypoint, tol=0.05):
        return True


# --- the frame conversion (the dangerous part) ----------------------------- #


def test_enu_to_ned_maps_up_to_down():
    """+3 m altitude must become -3 m NED. Getting this sign wrong commands a
    dive when the mission asks for a climb."""
    assert enu_to_ned(0.0, 0.0, 3.0) == (0.0, 0.0, -3.0)


def test_enu_to_ned_swaps_east_and_north():
    # 10 m EAST is 10 m in NED's *east* slot, which is the SECOND component.
    assert enu_to_ned(10.0, 0.0, 0.0) == (0.0, 10.0, 0.0)
    # 7 m NORTH is NED's *first* component.
    assert enu_to_ned(0.0, 7.0, 0.0) == (7.0, 0.0, 0.0)


def test_ned_to_enu_is_the_exact_inverse():
    for p in [(1.0, 2.0, 3.0), (-4.5, 0.0, 12.25), (0.0, -7.0, -1.0)]:
        assert ned_to_enu(*enu_to_ned(*p)) == pytest.approx(p)


def test_yaw_east_is_heading_90_degrees():
    """ENU yaw 0 points EAST; NED heading 0 points NORTH. So they differ by 90
    deg, and a controller that passes yaw straight through flies sideways."""
    assert yaw_enu_to_heading(0.0) == pytest.approx(math.pi / 2)          # east
    assert yaw_enu_to_heading(math.pi / 2) == pytest.approx(0.0)          # north
    assert abs(yaw_enu_to_heading(math.pi)) == pytest.approx(math.pi / 2)  # west


def test_heading_is_wrapped_into_range():
    """PX4 rejects unwrapped angles, and an unwrapped setpoint makes the drone
    spin the long way to the same attitude."""
    for yaw in (-10.0, -3.0, 0.0, 3.0, 10.0, 100.0):
        h = yaw_enu_to_heading(yaw)
        assert -math.pi - 1e-9 <= h <= math.pi + 1e-9, (yaw, h)


# --- issuing goals --------------------------------------------------------- #


def test_move_to_streams_a_setpoint_in_ned():
    link = FakeLink()
    ctl = PX4Control(links={"d1": link})
    ctl.move_to("d1", Waypoint(x=10.0, y=20.0, z=5.0, yaw=0.0))
    assert len(link.setpoints) == 1
    n, e, d, h = link.setpoints[0]
    assert (n, e, d) == (20.0, 10.0, -5.0)      # north, east, down
    assert h == pytest.approx(math.pi / 2)


def test_offboard_is_requested_once_per_robot():
    """Idempotent by contract — callers re-send setpoints every tick."""
    link = FakeLink()
    ctl = PX4Control(links={"d1": link})
    for _ in range(5):
        ctl.move_to("d1", Waypoint(0.0, 0.0, 2.0))
    assert link.offboard_calls == 1
    assert ctl.sent["d1"] == 5   # but the STREAM continues, which offboard needs


def test_the_setpoint_stream_is_counted_not_assumed():
    """PX4 drops out of offboard if setpoints stop, so the count is observable
    rather than a thing the caller has to trust."""
    link = FakeLink()
    ctl = PX4Control(links={"d1": link})
    assert ctl.sent.get("d1") is None
    ctl.move_to("d1", Waypoint(1.0, 1.0, 1.0))
    ctl.move_to("d1", Waypoint(1.0, 1.0, 1.0))
    assert ctl.sent["d1"] == 2 == len(link.setpoints)


def test_move_to_does_not_teleport_the_robot():
    """The core difference from `kinematic.py`: issuing a goal is not arriving.
    A controller that reported arrival immediately would let the mission march
    through every panel before the drone had moved."""
    link = FakeLink(position=None)
    ctl = PX4Control(links={"d1": link})
    wp = Waypoint(50.0, 50.0, 5.0)
    ctl.move_to("d1", wp)
    assert ctl.at_goal("d1", wp) is False


# --- arrival --------------------------------------------------------------- #


def test_at_goal_is_false_until_an_estimate_arrives():
    ctl = PX4Control(links={"d1": FakeLink(position=None)})
    assert ctl.at_goal("d1", Waypoint(0.0, 0.0, 0.0)) is False


def test_at_goal_true_once_px4_reports_the_position():
    link = FakeLink()
    ctl = PX4Control(links={"d1": link})
    wp = Waypoint(12.0, -3.0, 4.0)
    link.arrive_at_enu(12.0, -3.0, 4.0)
    assert ctl.at_goal("d1", wp) is True


def test_at_goal_respects_the_tolerance():
    link = FakeLink()
    ctl = PX4Control(links={"d1": link})
    wp = Waypoint(0.0, 0.0, 5.0)
    link.arrive_at_enu(0.0, 0.0, 5.3)          # 0.30 m away
    assert ctl.at_goal("d1", wp, tol=0.05) is False
    assert ctl.at_goal("d1", wp, tol=0.5) is True


def test_at_goal_measures_3d_distance_not_altitude_alone():
    """A drone at the right height but 5 m downwind has NOT arrived — that is
    exactly the failure wind causes."""
    link = FakeLink()
    ctl = PX4Control(links={"d1": link})
    wp = Waypoint(0.0, 0.0, 5.0)
    link.arrive_at_enu(5.0, 0.0, 5.0)
    assert ctl.at_goal("d1", wp, tol=0.5) is False


# --- mixed fleet ----------------------------------------------------------- #


def test_robots_without_a_link_use_the_fallback():
    """How a mixed fleet works today: drones on PX4, ground bot kinematic
    (`FR-07` keeps that valid)."""
    rec = _Recorder()
    ctl = PX4Control(links={"d1": FakeLink()}, fallback=rec)
    wp = Waypoint(1.0, 2.0, 0.0)
    ctl.move_to("bot", wp)
    assert rec.moves == [("bot", wp)]
    assert ctl.at_goal("bot", wp) is True


def test_an_unmapped_robot_without_a_fallback_raises():
    """Silently not moving would look like a mission bug, so it fails loudly."""
    ctl = PX4Control(links={})
    with pytest.raises(KeyError, match="silently never move"):
        ctl.move_to("ghost", Waypoint(0.0, 0.0, 0.0))
    with pytest.raises(KeyError):
        ctl.at_goal("ghost", Waypoint(0.0, 0.0, 0.0))


def test_it_satisfies_the_RobotControl_abc():
    """NFR-04: the graduation must be a swap behind the existing ABC."""
    ctl = PX4Control(links={"d1": FakeLink()})
    assert isinstance(ctl, RobotControl)


def test_goal_is_recoverable_for_error_reporting():
    ctl = PX4Control(links={"d1": FakeLink()})
    assert ctl.goal("d1") is None
    wp = Waypoint(3.0, 4.0, 5.0)
    ctl.move_to("d1", wp)
    assert ctl.goal("d1") == wp


def test_the_mission_fsm_drives_it_unchanged():
    """The point of the ABC: `orchestrator/mission.py` must not know PX4 exists."""
    from solar_twin.orchestrator.fake_backend import FakeSimBackend
    from solar_twin.orchestrator.mission import Fleet, InspectionTarget, Mission
    from solar_twin.perception.ground_truth import GroundTruthPerception
    from solar_twin.schema.pv_module import PanelRecord, PanelState

    panels = [PanelRecord(panel_id="R01-C001", grid_index=(1, 1), state=PanelState.SOILED)]
    backend = FakeSimBackend(panels)
    links = {"d1": FakeLink(), "d2": FakeLink()}
    ctl = PX4Control(links=links, fallback=backend)   # bot falls back, drones fly
    mission = Mission(backend, ctl, GroundTruthPerception(), Fleet("bot", "d1", "d2"))
    res = mission.run([InspectionTarget("R01-C001", Waypoint(0, -3, 0),
                                        Waypoint(0, 0, 3), Waypoint(0, 0, 1))])
    assert res.panels_inspected == 1
    assert res.results[0].detected_state == "soiled"
    # Both drones were commanded through PX4, and the bot through the fallback.
    assert links["d1"].setpoints and links["d2"].setpoints
