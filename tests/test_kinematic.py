"""KinematicControl against a fake runtime (pure-python, no Isaac)."""

from solar_twin.control.base import Waypoint
from solar_twin.control.kinematic import KinematicControl


class _FakeRuntime:
    def __init__(self):
        self.poses: dict[str, tuple] = {}

    def set_pose(self, rid, x, y, z, yaw=0.0):
        self.poses[rid] = (x, y, z, yaw)

    def get_pose(self, rid):
        return self.poses.get(rid, (0.0, 0.0, 0.0, 0.0))


def test_move_to_sets_pose_and_reaches_goal():
    rt = _FakeRuntime()
    ctrl = KinematicControl(rt)
    wp = Waypoint(1.0, 2.0, 3.0, 0.5)

    assert not ctrl.at_goal("drone1", wp)  # unmoved
    ctrl.move_to("drone1", wp)
    assert rt.poses["drone1"] == (1.0, 2.0, 3.0, 0.5)
    assert ctrl.at_goal("drone1", wp)


def test_at_goal_respects_tolerance():
    rt = _FakeRuntime()
    ctrl = KinematicControl(rt)
    rt.set_pose("bot", 1.0, 1.0, 0.0, 0.0)
    assert ctrl.at_goal("bot", Waypoint(1.02, 1.0, 0.0), tol=0.05)
    assert not ctrl.at_goal("bot", Waypoint(1.2, 1.0, 0.0), tol=0.05)
