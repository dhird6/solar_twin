"""The Transport interface — the brain↔world seam.

How the orchestrator gets sensor data + panel state and writes verdicts back.
`sim_native.py` (default) reads render products / the USD stage in-process;
`ros2_bridge.py` (later, validated Day 1) carries the same data over topics.
Nothing downstream knows which is behind it.

Pure-python: no Isaac import here (golden rule / Do-NOT list). The USD stage is
the source of truth for panel state, so panel reads/writes route through here
rather than a side store.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from solar_twin.perception.base import Frame
from solar_twin.schema.pv_module import PanelRecord, PanelState


@dataclass
class Pose:
    """A robot pose in stage-local coordinates (Z-up, meters)."""

    x: float
    y: float
    z: float
    yaw: float = 0.0  # radians about +Z


class Transport(ABC):
    """Sensor + panel-state conduit between the mission brain and the world."""

    @abstractmethod
    def capture(self, robot_id: str) -> Frame:
        """Grab the current camera frame for a robot."""

    @abstractmethod
    def pose(self, robot_id: str) -> Pose:
        """Current pose of a robot."""

    @abstractmethod
    def read_panel(self, panel_id: str) -> PanelRecord:
        """Read a panel's current state from the source of truth (USD)."""

    @abstractmethod
    def write_panel(
        self, panel_id: str, state: PanelState, note: str, timestamp: str
    ) -> None:
        """Write a verdict back onto the panel and append to its log (§6.1)."""

    @abstractmethod
    def step(self, dt: float = 0.0) -> None:
        """Advance the world one tick (sim-native single-process); may spin/no-op
        for out-of-process transports."""
