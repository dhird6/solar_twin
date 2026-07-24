"""Keep-out-aware RobotControl wrapper (planning-layer no-fly enforcement).

Wraps any `RobotControl` and vets every commanded waypoint against the turbine
keep-out volumes before it reaches the real controller. A waypoint inside a
no-fly volume is clamped to the nearest safe point and the event is logged, so a
misplaced goal can never drive the drone into the rotor-swept volume or the tower.

Control-agnostic and Isaac-free: it protects the Slice-0 kinematic drone today and
a Pegasus/PX4 drone later, unchanged. It does NOT simulate collision physics — that
is the PhysX layer, which only bites once the drone has rigid-body dynamics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from solar_twin.control.base import RobotControl, Waypoint
from solar_twin.world.keepout import TurbineKeepout, clamp_out, worst_violation


@dataclass
class KeepoutEvent:
    robot_id: str
    requested: tuple[float, float, float]
    clamped: tuple[float, float, float]
    penetration_m: float

    def to_dict(self) -> dict:
        return {
            "robot_id": self.robot_id,
            "requested": [round(v, 3) for v in self.requested],
            "clamped": [round(v, 3) for v in self.clamped],
            "penetration_m": round(self.penetration_m, 3),
        }


class SafeControl(RobotControl):
    """Vet every move against keep-outs; clamp violations, record them, and track
    the tightest clearance any commanded waypoint came to a no-fly volume."""

    def __init__(self, inner: RobotControl, keepouts: list[TurbineKeepout]):
        self._inner = inner
        self._keepouts = keepouts
        self.events: list[KeepoutEvent] = []
        self.min_clearance_m: float = math.inf

    def move_to(self, robot_id: str, waypoint: Waypoint) -> None:
        pen = worst_violation(self._keepouts, waypoint.x, waypoint.y, waypoint.z)
        # clearance = -penetration (positive when outside the volume)
        self.min_clearance_m = min(self.min_clearance_m, -pen)
        if pen > 0.0:
            sx, sy, sz = clamp_out(self._keepouts, waypoint.x, waypoint.y, waypoint.z)
            self.events.append(
                KeepoutEvent(
                    robot_id=robot_id,
                    requested=(waypoint.x, waypoint.y, waypoint.z),
                    clamped=(sx, sy, sz),
                    penetration_m=pen,
                )
            )
            waypoint = Waypoint(sx, sy, sz, waypoint.yaw)
        self._inner.move_to(robot_id, waypoint)

    def at_goal(self, robot_id: str, waypoint: Waypoint, tol: float = 0.05) -> bool:
        return self._inner.at_goal(robot_id, waypoint, tol)
