"""Kinematic RobotControl (Slice 0) — teleport an Xform to the waypoint.

No flight dynamics: the robot is simply placed at the commanded waypoint (the
simplest thing that carries a camera along an inspection path). Later this swaps
for real controllers / Pegasus PX4 without touching the mission.

Pure-python: it drives any object exposing `set_pose`/`get_pose` (a `SimRuntime`
in the sim, or a fake in tests) — no Isaac import here.
"""

from __future__ import annotations

from solar_twin.control.base import RobotControl, Waypoint


class KinematicControl(RobotControl):
    def __init__(self, runtime):
        """`runtime` exposes set_pose(id, x, y, z, yaw) and
        get_pose(id) -> (x, y, z, yaw)."""
        self._rt = runtime

    def move_to(self, robot_id: str, waypoint: Waypoint) -> None:
        self._rt.set_pose(
            robot_id, waypoint.x, waypoint.y, waypoint.z, waypoint.yaw
        )

    def at_goal(self, robot_id: str, waypoint: Waypoint, tol: float = 0.05) -> bool:
        x, y, z, _ = self._rt.get_pose(robot_id)
        return (
            abs(x - waypoint.x) <= tol
            and abs(y - waypoint.y) <= tol
            and abs(z - waypoint.z) <= tol
        )
