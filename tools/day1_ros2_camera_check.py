"""Day-1 de-risk: does the camera -> ROS 2 publish path work on this Spark?

Isaac Sim 6.0 standalone script. Builds a self-contained scene (no external
assets to download: a dome light + a cube + a camera), wires the ROS 2 camera
OmniGraph, and publishes /rgb + /camera_info while looping. API verified against
this build's standalone_examples/api/isaacsim.ros2.bridge/camera_periodic.py.

Run (from the Isaac Sim build dir, with ROS 2 sourced so the bridge + system
`ros2` CLI share middleware):
    source /opt/ros/jazzy/setup.bash
    ./python.sh /path/to/day1_ros2_camera_check.py            # runs until killed

Verify from another sourced shell:
    ros2 topic list
    ros2 topic hz /rgb
    ros2 topic echo /camera_info --once
"""

import argparse

from isaacsim import SimulationApp

parser = argparse.ArgumentParser()
parser.add_argument("--frames", type=int, default=0, help="0 = run until killed")
args, _ = parser.parse_known_args()

# RaytracedLighting (RTX real-time) is lighter than path tracing and enough to
# render an RGB frame; headless renders offscreen (no display needed).
CONFIG = {"renderer": "RaytracedLighting", "headless": True}
simulation_app = SimulationApp(CONFIG)

import carb  # noqa: E402
import isaacsim.core.experimental.utils.app as app_utils  # noqa: E402
import isaacsim.core.experimental.utils.stage as stage_utils  # noqa: E402
import omni  # noqa: E402
import omni.graph.core as og  # noqa: E402
import usdrt.Sdf  # noqa: E402
from isaacsim.core.simulation_manager import SimulationManager  # noqa: E402
from pxr import Gf, Sdf, UsdGeom, UsdLux  # noqa: E402

CAMERA_STAGE_PATH = "/Camera"
ROS_CAMERA_GRAPH_PATH = "/ROS_Camera"

# --- enable the ROS 2 bridge -------------------------------------------------
app_utils.enable_extension("isaacsim.ros2.bridge")
simulation_app.update()

stage_utils.set_stage_units(meters_per_unit=1.0)
stage = omni.usd.get_context().get_stage()
UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)  # match project convention
print("DAY1 >>> stage ready (Z-up, meters)", flush=True)

# --- minimal self-contained scene: dome light + a cube -----------------------
dome = UsdLux.DomeLight.Define(stage, "/World/DomeLight")
dome.CreateIntensityAttr(1000.0)

cube = UsdGeom.Cube.Define(stage, "/World/Cube")
cube.CreateSizeAttr(1.0)
UsdGeom.XformCommonAPI(cube).SetTranslate(Gf.Vec3d(0.0, 0.0, 0.5))

# --- camera looking at the cube (Z-up) ---------------------------------------
camera_prim = UsdGeom.Camera(stage.DefinePrim(CAMERA_STAGE_PATH, "Camera"))
cam_xform = UsdGeom.XformCommonAPI(camera_prim)
cam_xform.SetTranslate(Gf.Vec3d(0.0, -5.0, 2.0))
cam_xform.SetRotate((75.0, 0.0, 0.0), UsdGeom.XformCommonAPI.RotationOrderXYZ)
camera_prim.GetHorizontalApertureAttr().Set(21)
camera_prim.GetVerticalApertureAttr().Set(16)
camera_prim.GetFocalLengthAttr().Set(24)
simulation_app.update()
print("DAY1 >>> scene + camera created", flush=True)

# --- ROS 2 camera graph (rgb + camera_info) ----------------------------------
keys = og.Controller.Keys
ros_camera_graph, _, _, _ = og.Controller.edit(
    {
        "graph_path": ROS_CAMERA_GRAPH_PATH,
        "evaluator_name": "push",
        "pipeline_stage": og.GraphPipelineStage.GRAPH_PIPELINE_STAGE_ONDEMAND,
    },
    {
        keys.CREATE_NODES: [
            ("OnTick", "omni.graph.action.OnTick"),
            ("createRenderProduct", "isaacsim.core.nodes.IsaacCreateRenderProduct"),
            ("cameraHelperRgb", "isaacsim.ros2.bridge.ROS2CameraHelper"),
            ("cameraHelperInfo", "isaacsim.ros2.bridge.ROS2CameraInfoHelper"),
        ],
        keys.CONNECT: [
            ("OnTick.outputs:tick", "createRenderProduct.inputs:execIn"),
            ("createRenderProduct.outputs:execOut", "cameraHelperRgb.inputs:execIn"),
            ("createRenderProduct.outputs:execOut", "cameraHelperInfo.inputs:execIn"),
            ("createRenderProduct.outputs:renderProductPath", "cameraHelperRgb.inputs:renderProductPath"),
            ("createRenderProduct.outputs:renderProductPath", "cameraHelperInfo.inputs:renderProductPath"),
        ],
        keys.SET_VALUES: [
            ("createRenderProduct.inputs:cameraPrim", [usdrt.Sdf.Path(CAMERA_STAGE_PATH)]),
            ("createRenderProduct.inputs:width", 640),
            ("createRenderProduct.inputs:height", 480),
            ("cameraHelperRgb.inputs:frameId", "sim_camera"),
            ("cameraHelperRgb.inputs:topicName", "rgb"),
            ("cameraHelperRgb.inputs:type", "rgb"),
            ("cameraHelperInfo.inputs:frameId", "sim_camera"),
            ("cameraHelperInfo.inputs:topicName", "camera_info"),
        ],
    },
)
og.Controller.evaluate_sync(ros_camera_graph)
simulation_app.update()
print("DAY1 >>> ROS2 camera graph created (topics: /rgb, /camera_info)", flush=True)

# --- run ---------------------------------------------------------------------
SimulationManager.setup_simulation(dt=1.0 / 60.0, device="cpu")
app_utils.play()
simulation_app.update()
print("DAY1 >>> PLAYING - publishing /rgb + /camera_info (Sensor Data QoS)", flush=True)

frame = 0
while simulation_app.is_running():
    simulation_app.update()
    if app_utils.is_playing():
        frame += 1
        if frame % 60 == 0:
            print(f"DAY1 >>> heartbeat frame={frame}", flush=True)
        if args.frames and frame >= args.frames:
            break

print("DAY1 >>> stopping", flush=True)
app_utils.stop()
simulation_app.close()
