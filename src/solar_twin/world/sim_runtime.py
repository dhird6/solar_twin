"""Isaac Sim runtime for Slice 0 (Isaac-bound).

Launches a headless-capable SimulationApp, opens the built `assets/farm.usd`,
spawns kinematic robots (each drone carries a downward camera), and exposes the
low-level ops the sim-native Transport + kinematic Control need: step/render,
set/get pose, grab a camera RGB frame, and get a panel prim.

Camera capture uses `omni.replicator.core` render products + the "rgb" annotator
(pattern verified against this build's
standalone_examples/testing/isaacsim.simulation_app/test_frame_delay.py).

Runs only under Isaac Sim's Python (`./python.sh`). pxr/omni are imported after
SimulationApp starts, so this module is never imported by the Isaac-free tests.
"""

from __future__ import annotations

from typing import Optional

# Annotators can lag the render by a frame or two; pump this many app updates
# before reading a freshly-moved camera so capture() returns the current view.
_RENDER_SETTLE_UPDATES = 3


class SimRuntime:
    def __init__(
        self,
        farm_usd: str,
        camera_robots: list[str],
        marker_robots: list[str],
        headless: bool = True,
        resolution: tuple[int, int] = (640, 480),
        overview_pose: Optional[tuple[float, float, float]] = None,
    ):
        from isaacsim import SimulationApp

        self._app = SimulationApp({"renderer": "RaytracedLighting", "headless": headless})

        import isaacsim.core.experimental.utils.app as app_utils
        import omni.usd
        from pxr import Gf, UsdGeom

        app_utils.enable_extension("omni.replicator.core")
        self._app.update()
        import omni.replicator.core as rep

        self._rep = rep
        self._Gf = Gf
        self._UsdGeom = UsdGeom
        self._resolution = resolution
        # RTX subframes accumulated per capture (denoise/settle). Standalone
        # render products are driven by the replicator orchestrator, not bare
        # app.update() — that is what actually renders + fills the annotator.
        self._rt_subframes = 4

        # Open the built farm stage.
        omni.usd.get_context().open_stage(farm_usd)
        self._app.update()
        self._stage = omni.usd.get_context().get_stage()
        assert UsdGeom.GetStageUpAxis(self._stage) == UsdGeom.Tokens.z, "farm must be Z-up"

        UsdGeom.Xform.Define(self._stage, "/World/Robots")

        self._robot_paths: dict[str, str] = {}
        self._annots: dict[str, object] = {}

        # Robots that carry a camera (drones): Xform + downward Camera + annotator.
        for rid in camera_robots:
            path = f"/World/Robots/{rid}"
            UsdGeom.Xform.Define(self._stage, path)
            self._add_marker(path, size=0.25)
            cam_path = f"{path}/Camera"
            cam = UsdGeom.Camera.Define(self._stage, cam_path)
            # Camera looks down its local -Z (drone hovers above the panel). Mount
            # it 0.3 m below the drone origin so the marker cube doesn't occlude it.
            UsdGeom.XformCommonAPI(cam).SetTranslate(Gf.Vec3d(0.0, 0.0, -0.3))
            cam.CreateClippingRangeAttr(Gf.Vec2f(0.01, 1000.0))
            rp = rep.create.render_product(cam_path, resolution)
            annot = rep.AnnotatorRegistry.get_annotator("rgb")
            annot.attach([rp])
            self._robot_paths[rid] = path
            self._annots[rid] = annot

        # Robots that are just a moving marker (ground bot): Xform + box.
        for rid in marker_robots:
            path = f"/World/Robots/{rid}"
            UsdGeom.Xform.Define(self._stage, path)
            self._add_marker(path, size=0.4)
            self._robot_paths[rid] = path

        # Optional fixed bird's-eye camera for a run video.
        self._overview_annot = None
        if overview_pose is not None:
            ov = UsdGeom.Camera.Define(self._stage, "/World/Overview")
            UsdGeom.XformCommonAPI(ov).SetTranslate(Gf.Vec3d(*overview_pose))
            ov.CreateFocalLengthAttr(15.0)
            ov.CreateHorizontalApertureAttr(36.0)
            ov.CreateClippingRangeAttr(Gf.Vec2f(0.1, 10000.0))
            ov_rp = rep.create.render_product("/World/Overview", (960, 540))
            self._overview_annot = rep.AnnotatorRegistry.get_annotator("rgb")
            self._overview_annot.attach([ov_rp])

        # Warm up the renderer so the first capture is valid.
        self.step(_RENDER_SETTLE_UPDATES + 2)

    # ------------------------------------------------------------------ #
    def _add_marker(self, path: str, size: float) -> None:
        cube = self._UsdGeom.Cube.Define(self._stage, f"{path}/Marker")
        cube.CreateSizeAttr(1.0)
        self._UsdGeom.XformCommonAPI(cube).SetScale(
            self._Gf.Vec3f(size, size, size)
        )

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            self._app.update()

    def is_running(self) -> bool:
        return self._app.is_running()

    def set_pose(self, robot_id: str, x: float, y: float, z: float, yaw: float = 0.0) -> None:
        prim = self._stage.GetPrimAtPath(self._robot_paths[robot_id])
        api = self._UsdGeom.XformCommonAPI(prim)
        api.SetTranslate(self._Gf.Vec3d(float(x), float(y), float(z)))
        import math

        api.SetRotate(
            (0.0, 0.0, math.degrees(yaw)),
            self._UsdGeom.XformCommonAPI.RotationOrderXYZ,
        )

    def get_pose(self, robot_id: str) -> tuple[float, float, float, float]:
        import math
        from pxr import Usd

        prim = self._stage.GetPrimAtPath(self._robot_paths[robot_id])
        translate, rotate, _, _, _ = self._UsdGeom.XformCommonAPI(prim).GetXformVectors(
            Usd.TimeCode.Default()
        )
        return (translate[0], translate[1], translate[2], math.radians(rotate[2]))

    def capture(self, robot_id: str):
        """Return the drone camera's latest RGB frame (H x W x 4 uint8), or None."""
        import numpy as np

        annot = self._annots.get(robot_id)
        if annot is None:
            return None
        # Drive the render products so the annotator sees the current pose.
        self._rep.orchestrator.step(
            rt_subframes=self._rt_subframes, pause_timeline=False
        )
        data = np.asarray(annot.get_data())
        if data.size == 0:
            return None
        return data

    def capture_overview(self):
        """RGB from the fixed bird's-eye camera (H x W x 4 uint8), or None."""
        import numpy as np

        if self._overview_annot is None:
            return None
        self._rep.orchestrator.step(
            rt_subframes=self._rt_subframes, pause_timeline=False
        )
        data = np.asarray(self._overview_annot.get_data())
        return None if data.size == 0 else data

    def export(self, path: str) -> None:
        """Save the current (post-run) stage — panels now hold verdicts."""
        self._stage.Export(path)

    def get_prim(self, prim_path: str):
        return self._stage.GetPrimAtPath(prim_path)

    @property
    def stage(self):
        return self._stage

    def close(self) -> None:
        self._app.close()
