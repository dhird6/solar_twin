"""The RobotControl interface — how the mission moves the fleet.

Slice 0: `kinematic.py` teleports/interpolates an Xform along waypoints (no
flight dynamics). Later: real controllers / Pegasus PX4. The mission issues
goals through this interface and never knows which is behind it.

Pure-python: no Isaac import here (golden rule).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Waypoint:
    """A motion goal in stage-local coordinates (Z-up, meters)."""

    x: float
    y: float
    z: float
    yaw: float = 0.0  # radians about +Z


class RobotControl(ABC):
    """Move a ground base or fly a drone to waypoints."""

    @abstractmethod
    def move_to(self, robot_id: str, waypoint: Waypoint) -> None:
        """Move a robot to a waypoint. Kinematic impls complete the move
        (teleport/interp); dynamic impls issue the goal and drive toward it."""

    @abstractmethod
    def at_goal(self, robot_id: str, waypoint: Waypoint, tol: float = 0.05) -> bool:
        """True once the robot is within ``tol`` meters of ``waypoint``."""
