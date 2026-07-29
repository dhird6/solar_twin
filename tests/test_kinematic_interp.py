"""Interpolated kinematic control (pure, no Isaac) — the demo-video motion mode."""

from solar_twin.control.base import Waypoint
from solar_twin.control.kinematic import KinematicControl


class FakeRuntime:
    """Minimal stand-in for SimRuntime: records every pose it is given."""

    def __init__(self, start=(0.0, 0.0, 0.0, 0.0)):
        self.pose = start
        self.poses = [start]
        self.steps = 0

    def set_pose(self, robot_id, x, y, z, yaw=0.0):
        self.pose = (x, y, z, yaw)
        self.poses.append(self.pose)

    def get_pose(self, robot_id):
        return self.pose

    def step(self, n=1):
        self.steps += n


def test_default_is_still_teleport():
    """Measurement runs must not silently get 10x the sim steps."""
    rt = FakeRuntime()
    KinematicControl(rt).move_to("drone1", Waypoint(10.0, 0.0, 3.0))
    assert rt.pose == (10.0, 0.0, 3.0, 0.0)
    assert len(rt.poses) == 2   # start + the one placement
    assert rt.steps == 0


def test_interpolated_mode_traverses_the_distance_in_steps():
    rt = FakeRuntime()
    ctl = KinematicControl(rt, speeds={"drone1": 2.0}, dt=0.1)
    ctl.move_to("drone1", Waypoint(4.0, 0.0, 0.0))
    # 4 m at 2 m/s in 0.1 s ticks -> ~20 intermediate poses, not one jump.
    assert rt.steps >= 15
    xs = [p[0] for p in rt.poses]
    assert xs == sorted(xs)                    # monotone approach, no overshoot
    assert max(xs) <= 4.0 + 1e-9
    assert rt.pose[:3] == (4.0, 0.0, 0.0)      # lands exactly on the waypoint


def test_on_tick_fires_once_per_step_with_the_robot_id():
    rt = FakeRuntime()
    seen = []
    ctl = KinematicControl(rt, speeds={"drone1": 2.0}, dt=0.1)
    ctl.set_on_tick(seen.append)
    ctl.move_to("drone1", Waypoint(2.0, 0.0, 0.0))
    assert len(seen) == rt.steps
    assert set(seen) == {"drone1"}


def test_a_robot_without_a_speed_still_teleports():
    """Mixed fleets are fine: only the robots given a speed fly."""
    rt = FakeRuntime()
    ctl = KinematicControl(rt, speeds={"drone1": 2.0})
    ctl.move_to("ground_bot", Waypoint(5.0, 0.0, 0.0))
    assert rt.steps == 0
    assert rt.pose[:3] == (5.0, 0.0, 0.0)


def test_already_at_the_goal_does_not_loop():
    rt = FakeRuntime(start=(3.0, 0.0, 1.0, 0.0))
    ctl = KinematicControl(rt, speeds={"drone1": 2.0})
    ctl.move_to("drone1", Waypoint(3.0, 0.0, 1.0))
    assert rt.steps == 0


def test_cruise_speed_is_used_for_long_hops_and_inspection_speed_for_the_approach():
    """A survey drone transits at cruise and slows to take the shot. It is also
    what stops a 490 m commute from becoming 4,900 rendered frames."""
    rt = FakeRuntime()
    ctl = KinematicControl(
        rt, speeds={"drone1": 2.0}, cruise_speeds={"drone1": 20.0},
        cruise_above_m=6.0, dt=0.1,
    )
    ctl.move_to("drone1", Waypoint(100.0, 0.0, 0.0))
    # At inspection speed alone this is 500 ticks; cruising most of it is ~50.
    assert rt.steps < 100, rt.steps
    assert rt.pose[:3] == (100.0, 0.0, 0.0)

    # The last stretch is flown slowly: steps inside the final 6 m are small.
    xs = [p[0] for p in rt.poses]
    approach = [b - a for a, b in zip(xs, xs[1:]) if b > 94.0]
    assert approach and max(approach) <= 2.0 * 0.1 + 1e-9


def test_without_a_cruise_speed_behaviour_is_unchanged():
    rt_a = FakeRuntime()
    KinematicControl(rt_a, speeds={"d": 2.0}, dt=0.1).move_to("d", Waypoint(20.0, 0, 0))
    rt_b = FakeRuntime()
    KinematicControl(
        rt_b, speeds={"d": 2.0}, cruise_speeds={"d": 2.0}, dt=0.1
    ).move_to("d", Waypoint(20.0, 0, 0))
    assert rt_a.steps == rt_b.steps
