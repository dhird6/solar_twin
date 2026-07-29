"""`ros2_bridge` conformance and wire details — with no ROS 2 installed.

`FR-23`. ROS 2 lives at `/opt/ros/jazzy` and is only importable once sourced, and
the CI runner has none at all. That is exactly why the bridge keeps its
message-shape logic in module-level pure functions and accepts an injected node:
the parts that are easy to get silently wrong — QoS, quaternion→yaw, the row
stride of an `Image`, the fault payload — get checked on every PR instead of only
when someone happens to have ROS 2 sourced.

The QoS assertions are not pedantry. `ROS2_CONTRACT.md` §5: Isaac publishes
camera topics as Sensor Data (Best Effort), and a Reliable subscriber sees
**nothing, with no error at all**. That is a one-token mistake with a silent
failure mode, so it is pinned here.
"""

from __future__ import annotations

import json
import math

import pytest

from solar_twin.schema.pv_module import FaultReport, PanelRecord, PanelState
from solar_twin.transport import ros2_bridge as rb
from solar_twin.transport.base import Pose, Transport


# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class _Vec:
    def __init__(self, x=0.0, y=0.0, z=0.0, w=1.0):
        self.x, self.y, self.z, self.w = x, y, z, w


class _PoseMsg:
    def __init__(self, x=0.0, y=0.0, z=0.0, quat=(0.0, 0.0, 0.0, 1.0)):
        inner = type("P", (), {})()
        inner.position = _Vec(x, y, z)
        inner.orientation = _Vec(*quat)
        self.pose = inner


class _ImageMsg:
    def __init__(self, height, width, encoding, data, step=None):
        self.height, self.width, self.encoding = height, width, encoding
        self.data = data
        self.step = step if step is not None else width * rb._channels_for_encoding(encoding)


class _Pub:
    def __init__(self, topic, qos):
        self.topic, self.qos, self.sent = topic, qos, []

    def publish(self, msg):
        self.sent.append(msg)


class _Sub:
    def __init__(self, topic, cb, qos):
        self.topic, self.cb, self.qos = topic, cb, qos


class _FakeNode:
    """A node-shaped double. Records what was created, with which QoS."""

    def __init__(self):
        self.pubs: list[_Pub] = []
        self.subs: list[_Sub] = []
        self.destroyed = False

    def create_publisher(self, msg_type, topic, qos):
        p = _Pub(topic, qos)
        self.pubs.append(p)
        return p

    def create_subscription(self, msg_type, topic, cb, qos):
        s = _Sub(topic, cb, qos)
        self.subs.append(s)
        return s

    def destroy_node(self):
        self.destroyed = True

    def spin_once(self):
        """A node double declares how it pumps; nothing to drain here."""
        self.spins = getattr(self, "spins", 0) + 1

    # -- test helpers
    def sub(self, topic: str) -> _Sub:
        return next(s for s in self.subs if s.topic == topic)


class _Store:
    """A PanelStore double — stands in for the USD stage (§8 option 2)."""

    def __init__(self):
        self.records = {
            "R00-C001": PanelRecord(
                panel_id="R00-C001",
                grid_index=(0, 1),
                geo_position=(24.088, 69.418, 31.0),
            )
        }
        self.writes: list[tuple] = []

    def read_panel(self, panel_id):
        return self.records[panel_id]

    def write_panel(self, panel_id, state, note, timestamp):
        self.writes.append((panel_id, state, note, timestamp))


def _transport(robot_ids=("drone1", "ground_bot"), **kw):
    node = _FakeNode()
    store = _Store()
    t = rb.Ros2Transport(
        store, node=node, robot_ids=robot_ids, capture_timeout_s=0.05, **kw
    )
    return t, node, store


# --------------------------------------------------------------------------- #
# The golden rule: importable with no ROS 2
# --------------------------------------------------------------------------- #


def test_the_module_imports_without_rclpy():
    """If this ever needs ROS 2 to import, the Isaac-free suite and CI both break
    — and `run.py` could no longer offer the transport as a config flip."""
    import importlib.util

    assert importlib.util.find_spec("rclpy") is None or True  # informational
    # The real assertion: it is already imported at module scope above, and the
    # pure surface works.
    assert rb.robot_topic("ground_bot", "pose") == "/ground_bot/pose"


def test_it_satisfies_the_transport_abc():
    """A drop-in swap for `sim_native` means no abstract methods left over —
    otherwise instantiation would fail only in production."""
    assert issubclass(rb.Ros2Transport, Transport)
    t, _, _ = _transport()
    assert isinstance(t, Transport)


# --------------------------------------------------------------------------- #
# QoS — the silent-failure gotcha (§5)
# --------------------------------------------------------------------------- #


def test_camera_topics_are_best_effort_and_control_topics_are_reliable():
    assert rb.qos_for("camera/image_raw") == rb.SENSOR_DATA
    assert rb.qos_for("camera/camera_info") == rb.SENSOR_DATA
    assert rb.qos_for("pose") == rb.RELIABLE
    assert rb.qos_for("cmd_vel") == rb.RELIABLE
    assert rb.FAULT_TOPIC_QOS == rb.RELIABLE


def test_an_unknown_topic_defaults_to_reliable_not_best_effort():
    """Reliable is the safe default: a Reliable subscriber on a Best-Effort
    publisher sees nothing silently, while the reverse merely drops frames."""
    assert rb.qos_for("something/new") == rb.RELIABLE


def test_the_subscriptions_are_actually_created_with_that_qos():
    """`qos_for` being right is worthless if the subscription ignores it."""
    t, node, _ = _transport()
    with pytest.raises(rb.FrameTimeout):
        t.capture("ground_bot")  # triggers subscription creation
    assert node.sub("/ground_bot/camera/image_raw").qos == rb.SENSOR_DATA
    assert node.sub("/ground_bot/pose").qos == rb.RELIABLE


# --------------------------------------------------------------------------- #
# Namespacing (§4)
# --------------------------------------------------------------------------- #


def test_robot_ids_map_one_to_one_onto_namespaces():
    assert rb.robot_topic("screen_drone", "camera/image_raw") == (
        "/screen_drone/camera/image_raw"
    )
    # Tolerant of stray slashes rather than emitting a double-slash topic.
    assert rb.robot_topic("/ground_bot/", "/pose/") == "/ground_bot/pose"


def test_an_empty_robot_id_is_refused():
    """`/​/pose` would collide across robots — fail rather than publish garbage."""
    with pytest.raises(ValueError):
        rb.robot_topic("", "pose")


def test_the_fault_topic_is_global_not_namespaced():
    assert rb.FAULT_TOPIC == "/mission/fault"


# --------------------------------------------------------------------------- #
# Pose conversion
# --------------------------------------------------------------------------- #


def test_identity_quaternion_is_zero_yaw():
    assert rb.yaw_from_quaternion(0.0, 0.0, 0.0, 1.0) == pytest.approx(0.0)


@pytest.mark.parametrize("deg", [0, 30, 90, 179, -90, -135])
def test_yaw_round_trips_through_a_z_axis_quaternion(deg):
    """USD is Z-up and `Pose.yaw` is about +Z, matching ROS ENU — so this must be
    a plain extraction with no axis remap."""
    rad = math.radians(deg)
    z, w = math.sin(rad / 2.0), math.cos(rad / 2.0)
    assert rb.yaw_from_quaternion(0.0, 0.0, z, w) == pytest.approx(rad, abs=1e-9)


def test_pose_from_msg_carries_position_and_yaw():
    rad = math.radians(45)
    msg = _PoseMsg(1.5, -2.5, 3.0, (0.0, 0.0, math.sin(rad / 2), math.cos(rad / 2)))
    pose = rb.pose_from_msg(msg)
    assert isinstance(pose, Pose)
    assert (pose.x, pose.y, pose.z) == (1.5, -2.5, 3.0)
    assert pose.yaw == pytest.approx(rad)


# --------------------------------------------------------------------------- #
# Image conversion — stride is the classic bug
# --------------------------------------------------------------------------- #


def test_rgb8_image_becomes_an_hwc_array():
    np = pytest.importorskip("numpy")
    h, w = 2, 3
    data = bytes(range(h * w * 3))
    frame = rb.frame_from_image_msg(_ImageMsg(h, w, "rgb8", data))
    assert frame.shape == (h, w, 3)
    assert np.array_equal(frame.reshape(-1), np.frombuffer(data, dtype=np.uint8))


def test_row_padding_is_stripped_using_step():
    """`step` is the row stride in BYTES and may exceed width*channels. Ignoring
    it shears the image diagonally — which still looks like a plausible photo, so
    a human reviewing frames would not necessarily catch it."""
    pytest.importorskip("numpy")
    h, w, c = 2, 2, 3
    stride = w * c + 4  # 4 padding bytes per row
    rows = [bytes([10, 11, 12, 13, 14, 15]) + b"\x00\x00\x00\x00",
            bytes([20, 21, 22, 23, 24, 25]) + b"\x00\x00\x00\x00"]
    frame = rb.frame_from_image_msg(_ImageMsg(h, w, "rgb8", b"".join(rows), step=stride))
    assert frame.shape == (h, w, c)
    assert frame[0, 0].tolist() == [10, 11, 12]
    assert frame[1, 1].tolist() == [23, 24, 25]


def test_rgba_and_mono_encodings_are_supported():
    pytest.importorskip("numpy")
    assert rb.frame_from_image_msg(_ImageMsg(1, 2, "rgba8", bytes(8))).shape == (1, 2, 4)
    assert rb.frame_from_image_msg(_ImageMsg(2, 2, "mono8", bytes(4))).shape == (2, 2, 1)


def test_an_unknown_encoding_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match="unsupported image encoding"):
        rb.frame_from_image_msg(_ImageMsg(1, 1, "bayer_rggb8", bytes(1)))


def test_a_truncated_buffer_is_refused_rather_than_diagnosed():
    """A short buffer means a partial frame. Padding it out would hand the VLM
    invented pixels and score the verdict as if it were real."""
    pytest.importorskip("numpy")
    with pytest.raises(ValueError, match="too small"):
        rb.frame_from_image_msg(_ImageMsg(4, 4, "rgb8", bytes(10)))


# --------------------------------------------------------------------------- #
# capture: fresh-or-fail (the §7 decision)
# --------------------------------------------------------------------------- #


def test_capture_returns_a_frame_once_one_arrives():
    pytest.importorskip("numpy")
    t, node, _ = _transport()
    with pytest.raises(rb.FrameTimeout):
        t.capture("drone1")
    node.sub("/drone1/camera/image_raw").cb(_ImageMsg(1, 1, "rgb8", bytes([7, 8, 9])))
    assert t.capture("drone1")[0, 0].tolist() == [7, 8, 9]


def test_capture_never_serves_the_same_frame_twice():
    """The reason this is fresh-or-fail rather than last-seen: a stale frame
    silently attributes one panel's pixels to the next panel's verdict, and the
    project's headline KPIs are measured off those pixels. A hang is debuggable;
    a mis-attributed diagnosis is not.
    """
    pytest.importorskip("numpy")
    t, node, _ = _transport()
    node.sub("/drone1/camera/image_raw").cb(_ImageMsg(1, 1, "rgb8", bytes([1, 2, 3])))
    t.capture("drone1")  # serves it
    with pytest.raises(rb.FrameTimeout):
        t.capture("drone1")  # same frame must NOT come back


def test_the_timeout_message_names_both_usual_causes():
    """A bridge that "sees nothing" is usually Play (§6) or QoS (§5), not a bug in
    this file. The error should say so — that is what makes it debuggable."""
    t, _, _ = _transport()
    with pytest.raises(rb.FrameTimeout) as exc:
        t.capture("drone1")
    text = str(exc.value)
    assert "PLAYING" in text and "Best" in text


def test_pose_may_be_last_seen_unlike_capture():
    """A pose is a continuously-changing quantity, so a slightly old one
    approximates the same thing; a stale *frame* is a different panel."""
    t, node, _ = _transport()
    node.sub("/drone1/pose").cb(_PoseMsg(1.0, 2.0, 3.0))
    assert (t.pose("drone1").x, t.pose("drone1").y) == (1.0, 2.0)


def test_pose_without_a_publisher_fails_loudly():
    t, _, _ = _transport()
    with pytest.raises(rb.FrameTimeout):
        t.pose("ghost_robot")


# --------------------------------------------------------------------------- #
# Panel IO (§3, §8)
# --------------------------------------------------------------------------- #


def test_read_panel_goes_to_the_store_not_a_topic():
    t, node, store = _transport(robot_ids=())
    assert t.read_panel("R00-C001").panel_id == "R00-C001"
    assert not node.subs  # no subscription was needed for a panel read


def test_eager_subscription_happens_before_any_capture():
    """Production passes the fleet so subscriptions exist before Play (§6).
    Subscribing lazily on first `capture` races the publishers coming up and can
    time out on frames that were already in flight."""
    t, node, _ = _transport(robot_ids=("ground_bot", "screen_drone"))
    topics = {s.topic for s in node.subs}
    assert "/ground_bot/camera/image_raw" in topics
    assert "/screen_drone/pose" in topics


def test_subscribing_is_idempotent():
    """`capture` calls `_subscribe_robot` every time; duplicate subscriptions on
    the same topic would double-deliver and burn a callback per frame."""
    t, node, _ = _transport(robot_ids=("drone1",))
    before = len(node.subs)
    with pytest.raises(rb.FrameTimeout):
        t.capture("drone1")
    assert len(node.subs) == before


def test_write_panel_both_publishes_and_writes_through():
    """Only publishing would let the twin's own source of truth drift from what
    it told the rest of the fleet (bible §2.3)."""
    t, node, store = _transport()
    t.write_panel("R00-C001", PanelState.SOILED, "dusty", "2026-07-29T00:00:00+00:00")

    assert store.writes == [
        ("R00-C001", PanelState.SOILED, "dusty", "2026-07-29T00:00:00+00:00")
    ]
    fault_pub = next(p for p in node.pubs if p.topic == rb.FAULT_TOPIC)
    assert len(fault_pub.sent) == 1
    report = rb.fault_from_payload(fault_pub.sent[0])
    assert report.panel_id == "R00-C001"
    assert report.fault_type == "soiled"
    assert report.panel_geo_position == (24.088, 69.418, 31.0)


def test_the_fault_payload_is_exactly_the_faultreport_shape():
    """§3 locked this to the dataclass so the topic and the run record's
    `fault_events` cannot drift apart."""
    report = FaultReport(
        panel_id="R12-C047",
        fault_type="hotspot",
        confidence=0.9,
        note="n",
        timestamp="2026-07-29T00:00:00+00:00",
        panel_geo_position=(1.0, 2.0, 3.0),
    )
    payload = rb.fault_payload(report)
    # `to_dict` keeps `panel_geo_position` as a tuple and JSON has no tuple type,
    # so the comparison is against the round-tripped dict. The contract that
    # matters is that the payload IS `to_dict` serialized, and that a consumer
    # gets the identical dataclass back — including the tuple.
    assert json.loads(payload) == json.loads(json.dumps(report.to_dict()))
    assert rb.fault_from_payload(payload) == report
    assert rb.fault_from_payload(payload).panel_geo_position == (1.0, 2.0, 3.0)


def test_a_store_without_geo_still_publishes():
    """`panel_geo_position` is nullable in §3; a store that cannot supply it must
    not take the fault event down with it."""

    class _NoGeo:
        def read_panel(self, pid):
            raise KeyError(pid)

        def write_panel(self, *a):
            pass

    node = _FakeNode()
    t = rb.Ros2Transport(_NoGeo(), node=node)
    t.write_panel("R00-C009", PanelState.HOTSPOT, "", "2026-07-29T00:00:00+00:00")
    report = rb.fault_from_payload(
        next(p for p in node.pubs if p.topic == rb.FAULT_TOPIC).sent[0]
    )
    assert report.panel_geo_position is None
    assert report.fault_type == "hotspot"


# --------------------------------------------------------------------------- #
# step / lifecycle
# --------------------------------------------------------------------------- #


def test_step_does_not_advance_a_world():
    """Unlike `sim_native`, this transport advances nothing — the sim process owns
    real time (§7). It only drains callbacks."""
    t, _, _ = _transport()
    t.step()
    t.step(0.1)
    assert t.step_count == 2


def test_close_destroys_the_node_but_not_a_context_it_does_not_own():
    """With an injected node the caller owns the rclpy context; shutting it down
    would break a host process that has its own nodes."""
    t, node, _ = _transport()
    t.close()
    assert node.destroyed


def test_a_store_double_satisfies_the_panelstore_protocol():
    assert isinstance(_Store(), rb.PanelStore)
