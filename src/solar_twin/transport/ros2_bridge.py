"""ROS 2 `Transport` — the same brain↔world seam as `sim_native`, over topics.

`FR-23`. Implements `transport/base.py`'s `Transport` against the topic/QoS
contract in `docs/ROS2_CONTRACT.md`, so the orchestrator cannot tell which
transport is underneath it (bible §2.4). Nothing in `orchestrator/mission.py`
changes to use this.

**Importing this module must never require `rclpy`.** ROS 2 lives at
`/opt/ros/jazzy` and is only on `sys.path` once sourced; the Isaac-free test suite
and CI have no ROS 2 at all. So every `rclpy`/`sensor_msgs` import sits inside the
function that needs it, and all message-shape logic lives in module-level pure
functions that take duck-typed objects. That split is what makes the interesting
parts — QoS selection, quaternion→yaw, the fault payload — testable on a runner
with no ROS 2 installed, which is where they would otherwise never be checked.

Two contract questions §7/§8 left open are decided here and recorded back in
`docs/ROS2_CONTRACT.md`:

**`capture` blocks for a *fresh* frame and raises on timeout** — it does not
return the last-seen one. Returning a stale frame would silently attribute one
panel's pixels to another panel's verdict, and this project measures its headline
KPIs off exactly those pixels; a hang is debuggable, a quietly mis-attributed
diagnosis is not. `/camera/image_raw` is Best-Effort (§5) so frames genuinely do
get dropped, which is why the wait is bounded and the failure is loud.

**`read_panel` uses a direct panel store, not a topic** (§8, option 2). Panel
state lives on the USD stage, which is the source of truth (bible §2.3); it is a
query, not sensor or actuator data, and inventing a request-reply topic for it
would add a wire format with no second implementation to justify it. `write_panel`
does both: it publishes the verdict as an *event* on `/mission/fault` (that is
what a topic is for) **and** writes through to the store, so USD stays
authoritative either way.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any, Optional, Protocol, runtime_checkable

from solar_twin.perception.base import Frame
from solar_twin.schema.pv_module import FaultReport, PanelRecord, PanelState
from solar_twin.transport.base import Pose, Transport

# --------------------------------------------------------------------------- #
# QoS, as a plain value. `docs/ROS2_CONTRACT.md` §5: Isaac's camera bridge
# publishes Sensor Data (Best Effort), and a Reliable subscriber sees **nothing,
# with no error**. That gotcha is a one-line mistake, so the choice is data here
# and asserted in tests rather than buried in a create_subscription call.
# --------------------------------------------------------------------------- #

SENSOR_DATA = "sensor_data"  # Best Effort — high-rate camera streams
RELIABLE = "reliable"  # control/state — dropping one matters

#: topic suffix -> QoS, per the contract's topic table (§2).
QOS_BY_TOPIC: dict[str, str] = {
    "camera/image_raw": SENSOR_DATA,
    "camera/camera_info": SENSOR_DATA,
    "cmd_vel": RELIABLE,
    "pose": RELIABLE,
}

#: Global topics (no robot namespace, §4).
FAULT_TOPIC = "/mission/fault"
FAULT_TOPIC_QOS = RELIABLE


class FrameTimeout(RuntimeError):
    """No fresh camera frame arrived within the timeout.

    Deliberately an exception rather than a stale frame — see the module
    docstring. A run that hits this has a real problem (§6: ROS 2 OmniGraph nodes
    publish only after Play, so "sees nothing" is usually that, not a bridge bug).
    """


@runtime_checkable
class PanelStore(Protocol):
    """The non-ROS side-channel for panel state (§8, option 2).

    In the twin this is backed by the USD stage (`SimNativeTransport` already
    satisfies it structurally). On real hardware it would be backed by whatever
    owns panel state there — a service, a database, SCADA. The point of naming it
    is that `ros2_bridge` does not care which.
    """

    def read_panel(self, panel_id: str) -> PanelRecord: ...

    def write_panel(
        self, panel_id: str, state: PanelState, note: str, timestamp: str
    ) -> None: ...


# --------------------------------------------------------------------------- #
# Pure helpers — no rclpy, no Isaac. Everything that decides a wire detail lives
# here so it can be tested where ROS 2 is not installed.
# --------------------------------------------------------------------------- #


def robot_topic(robot_id: str, suffix: str) -> str:
    """``("ground_bot", "camera/image_raw") -> "/ground_bot/camera/image_raw"``.

    One namespace per robot id from `mission.yaml`'s `fleet:` block (§4), mapping
    1:1 onto `Transport`'s `robot_id` params. Explicit namespaces on purpose — the
    bible flags Isaac's auto-namespace as flaky in deep hierarchies (§6.3).
    """
    if not robot_id:
        raise ValueError("robot_id must be non-empty — topics would collide at /")
    return f"/{robot_id.strip('/')}/{suffix.strip('/')}"


def qos_for(suffix: str) -> str:
    """QoS for a topic suffix, defaulting to Reliable.

    Defaulting to Reliable is the safe direction: a Reliable subscriber on a
    Best-Effort publisher sees nothing (loud, and caught by the tests below),
    whereas the reverse merely tolerates drops.
    """
    return QOS_BY_TOPIC.get(suffix.strip("/"), RELIABLE)


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    """Yaw (rotation about +Z, radians) from a ROS quaternion.

    USD is Z-up and `Pose.yaw` is about +Z (`CLAUDE.md` conventions), which lines
    up with ROS's ENU convention, so this is the standard yaw extraction with no
    axis remap. Only yaw is carried: `Pose` has no roll/pitch, and inventing them
    here would imply the twin tracks an attitude it does not.
    """
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def pose_from_msg(msg: Any) -> Pose:
    """`geometry_msgs/PoseStamped` (duck-typed) -> `Pose`."""
    p = msg.pose.position
    q = msg.pose.orientation
    return Pose(
        x=float(p.x),
        y=float(p.y),
        z=float(p.z),
        yaw=yaw_from_quaternion(float(q.x), float(q.y), float(q.z), float(q.w)),
    )


def frame_from_image_msg(msg: Any) -> Frame:
    """`sensor_msgs/Image` (duck-typed) -> an HxWxC array, like `sim_native`.

    `Frame` is `Any` by design (`perception/base.py`) but the perception backends
    expect something array-shaped, and `frame_digest`/`frame_thumbnail` — which
    the KPI variance attribution depends on — need real pixels. numpy is imported
    lazily here for the same reason it is in `perception/base.py`: the ABC layer
    must import on a bare interpreter.
    """
    import numpy as np  # noqa: PLC0415 — lazy: this module must import bare

    channels = _channels_for_encoding(msg.encoding)
    buf = np.frombuffer(bytes(msg.data), dtype=np.uint8)
    height, width = int(msg.height), int(msg.width)

    # `step` is the row stride in bytes and may exceed width*channels (padding).
    step = int(getattr(msg, "step", 0)) or width * channels
    expected = step * height
    if buf.size < expected:
        raise ValueError(
            f"image buffer too small: {buf.size} bytes for {height}x{step} "
            f"({msg.encoding!r}) — a truncated frame must not be diagnosed"
        )
    rows = buf[:expected].reshape(height, step)
    return rows[:, : width * channels].reshape(height, width, channels)


def _channels_for_encoding(encoding: str) -> int:
    enc = (encoding or "").lower()
    if enc in ("rgb8", "bgr8"):
        return 3
    if enc in ("rgba8", "bgra8"):
        return 4
    if enc in ("mono8", "8uc1"):
        return 1
    raise ValueError(
        f"unsupported image encoding {encoding!r} — Isaac's camera helper "
        f"publishes rgb8/rgba8; add the mapping rather than guessing a stride"
    )


def fault_payload(report: FaultReport) -> str:
    """The `/mission/fault` `std_msgs/String.data` body (§3).

    Reuses `FaultReport.to_dict` rather than re-deriving the JSON, so this topic
    and the run record's `fault_events` cannot drift — that is the whole reason
    §3 locked the payload to the dataclass.
    """
    return json.dumps(report.to_dict())


def fault_from_payload(data: str) -> FaultReport:
    """Inverse of `fault_payload` — the subscriber side, and the round-trip a
    real consumer (Mission Dispatch, a SCADA shim) would do."""
    return FaultReport.from_dict(json.loads(data))


# --------------------------------------------------------------------------- #
# The transport
# --------------------------------------------------------------------------- #


class Ros2Transport(Transport):
    """`Transport` over the topics in `docs/ROS2_CONTRACT.md`.

    `node` exists so this class is testable where ROS 2 is not installed: pass a
    node-shaped double and no `rclpy` import happens at all. In production leave
    it None and the constructor builds a real node.
    """

    def __init__(
        self,
        panel_store: PanelStore,
        *,
        robot_ids: "tuple[str, ...] | list[str] | None" = None,
        node: Any = None,
        node_name: str = "solar_twin_transport",
        capture_timeout_s: float = 5.0,
        spin_timeout_s: float = 0.05,
        clock_driven: bool = False,
    ):
        """`robot_ids` subscribes eagerly, and production should pass it.

        Subscriptions are otherwise created on first `capture`/`pose`, which means
        the first call races the subscription setup and can time out on frames
        that were already in flight. Passing the fleet from `mission.yaml` lets
        subscriptions exist *before* Play (§6), which is when the publishers start.
        """
        self._store = panel_store
        self._capture_timeout_s = float(capture_timeout_s)
        self._spin_timeout_s = float(spin_timeout_s)
        self._clock_driven = bool(clock_driven)
        self.step_count = 0

        #: robot_id -> (frame, sequence). The sequence increments on every
        #: received frame so `capture` can tell "fresh" from "the one I already
        #: served" without relying on header stamps, which Isaac's camera helper
        #: does not guarantee to be monotonic across a Stop/Play.
        self._frames: dict[str, tuple[Frame, int]] = {}
        self._served: dict[str, int] = {}
        self._poses: dict[str, Pose] = {}
        self._seq = 0

        self._owns_context = node is None
        if node is None:
            node = self._make_node(node_name)
        self._node = node
        self._fault_pub = self._create_publisher_string(FAULT_TOPIC, FAULT_TOPIC_QOS)
        self._subscribed: set[str] = set()
        for rid in robot_ids or ():
            self._subscribe_robot(rid)

    # -- rclpy-touching plumbing, isolated ---------------------------------- #

    def _make_node(self, node_name: str) -> Any:
        import rclpy  # noqa: PLC0415 — lazy: no ROS 2 on the CI runner

        if not rclpy.ok():
            rclpy.init()
        return rclpy.create_node(node_name)

    @staticmethod
    def _resolve_qos(qos: str) -> Any:
        """Our QoS token -> an rclpy profile. Only called with real rclpy."""
        from rclpy.qos import (  # noqa: PLC0415
            qos_profile_sensor_data,
            qos_profile_system_default,
        )

        return qos_profile_sensor_data if qos == SENSOR_DATA else qos_profile_system_default

    def _create_publisher_string(self, topic: str, qos: str) -> Any:
        try:
            from std_msgs.msg import String  # noqa: PLC0415
        except ImportError:
            String = None  # a node double supplies its own type expectations
        resolved = self._resolve_qos(qos) if String is not None else qos
        return self._node.create_publisher(String, topic, resolved)

    def _subscribe_robot(self, robot_id: str) -> None:
        """Camera + pose subscriptions for one robot, created on first use.

        Lazily, because the fleet is declared in `mission.yaml` but a given run
        may only drive some of it, and a subscription to a topic nobody publishes
        is a silent source of "capture times out".
        """
        if robot_id in self._subscribed:
            return
        try:
            from geometry_msgs.msg import PoseStamped  # noqa: PLC0415
            from sensor_msgs.msg import Image  # noqa: PLC0415
        except ImportError:
            Image = PoseStamped = None

        image_topic = robot_topic(robot_id, "camera/image_raw")
        pose_topic = robot_topic(robot_id, "pose")

        self._node.create_subscription(
            Image,
            image_topic,
            lambda msg, rid=robot_id: self._on_image(rid, msg),
            self._resolve_qos(qos_for("camera/image_raw"))
            if Image is not None
            else qos_for("camera/image_raw"),
        )
        self._node.create_subscription(
            PoseStamped,
            pose_topic,
            lambda msg, rid=robot_id: self._on_pose(rid, msg),
            self._resolve_qos(qos_for("pose"))
            if PoseStamped is not None
            else qos_for("pose"),
        )
        self._subscribed.add(robot_id)

    def _spin_once(self) -> None:
        """Drain pending callbacks once.

        Pumping is the node's concern, so a node that knows how to pump itself is
        asked to. That is what lets an injected double drive `capture`/`step`
        without ROS 2 present — otherwise the injection point would be useless for
        exactly the paths most worth testing.
        """
        spin = getattr(self._node, "spin_once", None)
        if spin is not None:
            spin()
            return
        import rclpy  # noqa: PLC0415 — lazy: no ROS 2 on the CI runner

        rclpy.spin_once(self._node, timeout_sec=self._spin_timeout_s)

    # -- callbacks (pure bookkeeping) --------------------------------------- #

    def _on_image(self, robot_id: str, msg: Any) -> None:
        self._seq += 1
        self._frames[robot_id] = (frame_from_image_msg(msg), self._seq)

    def _on_pose(self, robot_id: str, msg: Any) -> None:
        self._poses[robot_id] = pose_from_msg(msg)

    # -- Transport ---------------------------------------------------------- #

    def capture(self, robot_id: str) -> Frame:
        """The latest *unserved* frame for `robot_id`, or raise `FrameTimeout`.

        Fresh-or-fail, never last-seen: see the module docstring. The bound comes
        from `capture_timeout_s`.
        """
        self._subscribe_robot(robot_id)
        deadline = self._monotonic() + self._capture_timeout_s
        while True:
            entry = self._frames.get(robot_id)
            if entry is not None and entry[1] > self._served.get(robot_id, 0):
                frame, seq = entry
                self._served[robot_id] = seq
                return frame
            if self._monotonic() >= deadline:
                raise FrameTimeout(
                    f"no fresh frame on {robot_topic(robot_id, 'camera/image_raw')} "
                    f"within {self._capture_timeout_s}s. Check that the sim is "
                    f"PLAYING (ROS 2 OmniGraph nodes publish only after Play, "
                    f"ROS2_CONTRACT.md §6) and that the subscriber QoS is Best "
                    f"Effort (§5)."
                )
            self._spin_once()

    def pose(self, robot_id: str) -> Pose:
        """Latest pose. Unlike `capture` this may be last-seen: a pose is state
        that changes continuously, so a slightly old one is an approximation of
        the same quantity, not a different panel's data."""
        self._subscribe_robot(robot_id)
        if robot_id not in self._poses:
            self._spin_once()
        try:
            return self._poses[robot_id]
        except KeyError:
            raise FrameTimeout(
                f"no pose seen on {robot_topic(robot_id, 'pose')} — is the robot "
                f"publishing, and is the sim playing (§6)?"
            ) from None

    def read_panel(self, panel_id: str) -> PanelRecord:
        """Direct store read (§8 option 2) — not a topic. See module docstring."""
        return self._store.read_panel(panel_id)

    def write_panel(
        self, panel_id: str, state: PanelState, note: str, timestamp: str
    ) -> None:
        """Publish the verdict on `/mission/fault` **and** write it through.

        Both, deliberately: the topic is how the rest of a fleet hears about a
        fault, and the store keeps USD authoritative (bible §2.3). If only the
        topic were written, the twin's own source of truth would drift from what
        it told everyone else.
        """
        self._store.write_panel(panel_id, state, note, timestamp)
        record: Optional[PanelRecord] = None
        try:
            record = self._store.read_panel(panel_id)
        except Exception:  # noqa: BLE001 — geo is a nice-to-have, not a contract
            record = None
        report = FaultReport(
            panel_id=panel_id,
            fault_type=state.value if hasattr(state, "value") else str(state),
            confidence=1.0,
            note=note,
            timestamp=timestamp,
            panel_geo_position=getattr(record, "geo_position", None),
        )
        self._publish_fault(report)

    def _publish_fault(self, report: FaultReport) -> None:
        try:
            from std_msgs.msg import String  # noqa: PLC0415

            msg = String()
            msg.data = fault_payload(report)
        except ImportError:
            msg = fault_payload(report)  # node double: hand it the payload
        self._fault_pub.publish(msg)

    def step(self, dt: float = 0.0) -> None:
        """Spin callbacks; the sim process owns real time here (§7).

        Not a world step — unlike `sim_native`, this transport does not advance
        anything. Calling it drains the subscription queues so the next
        `capture`/`pose` sees current data.
        """
        self._spin_once()
        self.step_count += 1

    # -- lifecycle ---------------------------------------------------------- #

    @staticmethod
    def _monotonic() -> float:
        return time.monotonic()

    def close(self) -> None:
        try:
            self._node.destroy_node()
        finally:
            if self._owns_context:
                try:
                    import rclpy  # noqa: PLC0415

                    if rclpy.ok():
                        rclpy.shutdown()
                except ImportError:
                    pass
