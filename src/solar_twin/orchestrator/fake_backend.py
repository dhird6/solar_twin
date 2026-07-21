"""FakeSimBackend — the Isaac-free world for logic tests (Principle §2.6).

Implements Transport + RobotControl with plain Python dicts so the entire
mission FSM runs in a unit test with no GPU and no Isaac Sim. Panel state lives
in :class:`PanelRecord` objects that stand in for USD prims: ``read_panel``
returns the injected ground truth, ``write_panel`` records the verdict exactly
as the USD adapter would. Motion is instantaneous (kinematic teleport).
"""

from __future__ import annotations

from dataclasses import replace

from solar_twin.control.base import RobotControl, Waypoint
from solar_twin.perception.base import Frame
from solar_twin.schema.pv_module import (
    PanelRecord,
    PanelState,
    append_inspection,
)
from solar_twin.transport.base import Pose, Transport


class FakeSimBackend(Transport, RobotControl):
    """In-memory stand-in for the Isaac world. Serves as both Transport and
    RobotControl for the mission under test."""

    def __init__(self, panels: list[PanelRecord]):
        # Source-of-truth mirror, keyed by panel_id.
        self._panels: dict[str, PanelRecord] = {p.panel_id: p for p in panels}
        self._poses: dict[str, Pose] = {}
        # Counters/log for assertions.
        self.step_count = 0
        self.writes: list[tuple[str, PanelState]] = []

    # ------------------------------------------------------------------ #
    # Transport
    # ------------------------------------------------------------------ #
    def capture(self, robot_id: str) -> Frame:
        # No pixels in the fake world; the ground-truth stub ignores the frame.
        return None

    def pose(self, robot_id: str) -> Pose:
        return self._poses.get(robot_id, Pose(0.0, 0.0, 0.0))

    def read_panel(self, panel_id: str) -> PanelRecord:
        # Return a copy so callers can't mutate the source of truth by accident.
        return replace(self._panels[panel_id])

    def write_panel(
        self, panel_id: str, state: PanelState, note: str, timestamp: str
    ) -> None:
        self._panels[panel_id] = append_inspection(
            self._panels[panel_id], state, note, timestamp
        )
        self.writes.append((panel_id, state))

    def step(self, dt: float = 0.0) -> None:
        self.step_count += 1

    # ------------------------------------------------------------------ #
    # RobotControl
    # ------------------------------------------------------------------ #
    def move_to(self, robot_id: str, waypoint: Waypoint) -> None:
        # Kinematic teleport: the robot is simply where it was told to go.
        self._poses[robot_id] = Pose(waypoint.x, waypoint.y, waypoint.z, waypoint.yaw)

    def at_goal(self, robot_id: str, waypoint: Waypoint, tol: float = 0.05) -> bool:
        p = self._poses.get(robot_id)
        if p is None:
            return False
        return (
            abs(p.x - waypoint.x) <= tol
            and abs(p.y - waypoint.y) <= tol
            and abs(p.z - waypoint.z) <= tol
        )

    # ------------------------------------------------------------------ #
    # Test helpers
    # ------------------------------------------------------------------ #
    def panel(self, panel_id: str) -> PanelRecord:
        """Peek at the current stored record (post-mission assertions)."""
        return self._panels[panel_id]
