"""Sim-native Transport (default) — in-process reads against the live stage.

Implements the `Transport` interface by delegating to a `SimRuntime`: camera
frames come from the drone render-product annotators; panel state reads/writes go
straight to the USD prims via the schema (the source of truth). This is the
Slice 0 default (the Spark's ROS 2 sensor path is proven but sim-native is
simpler; ROS 2 is the swap-in `ros2_bridge.py`).

Isaac-bound module (allowed Isaac imports per CLAUDE.md), but note it holds a
`SimRuntime` rather than importing pxr directly; panel IO uses the schema's
lazy-pxr helpers.
"""

from __future__ import annotations

from solar_twin.perception.base import Frame
from solar_twin.schema import pv_module as pv
from solar_twin.schema.pv_module import PanelRecord, PanelState
from solar_twin.transport.base import Pose, Transport


class SimNativeTransport(Transport):
    def __init__(self, runtime, panel_paths: dict[str, str]):
        """`runtime` is a SimRuntime; `panel_paths` maps panel_id -> USD prim path."""
        self._rt = runtime
        self._panel_paths = panel_paths
        self.step_count = 0

    def capture(self, robot_id: str) -> Frame:
        return self._rt.capture(robot_id)

    def pose(self, robot_id: str) -> Pose:
        x, y, z, yaw = self._rt.get_pose(robot_id)
        return Pose(x, y, z, yaw)

    def read_panel(self, panel_id: str) -> PanelRecord:
        return pv.read_panel(self._rt.get_prim(self._panel_paths[panel_id]))

    def write_panel(
        self, panel_id: str, state: PanelState, note: str, timestamp: str
    ) -> None:
        pv.write_state(
            self._rt.get_prim(self._panel_paths[panel_id]), state, note, timestamp
        )

    def step(self, dt: float = 0.0) -> None:
        self._rt.step()
        self.step_count += 1

    # --- extras used by run.py for artifacts (not part of the Transport API) --
    def capture_overview(self):
        return self._rt.capture_overview()

    def export_usd(self, path: str) -> None:
        self._rt.export(path)

    def close(self) -> None:
        self._rt.close()
