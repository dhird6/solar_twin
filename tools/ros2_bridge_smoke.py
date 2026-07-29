#!/usr/bin/env python3
"""Smoke-test `transport/ros2_bridge.py` against the REAL ROS 2 stack.

The unit tests (`tests/test_ros2_bridge.py`) drive the bridge with a node double
so they run on a machine with no ROS 2 — which is most machines, and all of CI.
That proves the logic, not the wiring. This proves the wiring: real `rclpy`, real
`sensor_msgs/Image`, real QoS profiles, a real DDS round-trip.

It is a script rather than a test because it needs a sourced ROS 2, so it cannot
be part of the Isaac-free suite by construction::

    source /opt/ros/jazzy/setup.bash
    PYTHONPATH=src python3 tools/ros2_bridge_smoke.py

Exit 0 = the seam is real. Exit non-zero prints which leg failed.

What it deliberately does NOT test: Isaac publishing the topics. That needs the
sim playing (`ROS2_CONTRACT.md` §6) and is the next step up. Here both ends are
ours, which isolates the bridge from Isaac's camera helper.
"""

from __future__ import annotations

import sys
import time

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    try:
        import rclpy
        from geometry_msgs.msg import PoseStamped
        from rclpy.qos import qos_profile_sensor_data, qos_profile_system_default
        from sensor_msgs.msg import Image
        from std_msgs.msg import String
    except ImportError as exc:
        print(f"ROS 2 is not sourced ({exc}). Run: source /opt/ros/jazzy/setup.bash")
        return 2

    import math

    from solar_twin.schema.pv_module import PanelRecord, PanelState
    from solar_twin.transport import ros2_bridge as rb

    class Store:
        def __init__(self):
            self.written = []

        def read_panel(self, pid):
            return PanelRecord(
                panel_id=pid, grid_index=(0, 1), geo_position=(24.088, 69.418, 31.0)
            )

        def write_panel(self, pid, state, note, ts):
            self.written.append((pid, state, note, ts))

    rclpy.init()
    store = Store()
    transport = rb.Ros2Transport(
        store,
        robot_ids=("drone1",),
        node_name="solar_twin_smoke_transport",
        capture_timeout_s=5.0,
    )

    # The other side of the wire: a node standing in for what Isaac publishes.
    pub_node = rclpy.create_node("solar_twin_smoke_publisher")
    img_pub = pub_node.create_publisher(
        Image, "/drone1/camera/image_raw", qos_profile_sensor_data
    )
    pose_pub = pub_node.create_publisher(
        PoseStamped, "/drone1/pose", qos_profile_system_default
    )
    # And a consumer of the fault topic, as Mission Dispatch or a SCADA shim.
    fault_node = rclpy.create_node("solar_twin_smoke_fault_consumer")
    received: list[str] = []
    fault_node.create_subscription(
        String,
        rb.FAULT_TOPIC,
        lambda m: received.append(m.data),
        qos_profile_system_default,
    )

    print("ros2_bridge smoke test")
    try:
        # Let discovery settle; DDS matching is not instantaneous.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and img_pub.get_subscription_count() == 0:
            rclpy.spin_once(pub_node, timeout_sec=0.05)
        check(
            "camera publisher matched the bridge's subscription",
            img_pub.get_subscription_count() > 0,
            f"{img_pub.get_subscription_count()} subscriber(s)",
        )

        # ---- capture: a real Image over Best-Effort QoS --------------------- #
        h, w = 4, 3
        payload = bytes(range(h * w * 3))
        msg = Image()
        msg.height, msg.width, msg.encoding = h, w, "rgb8"
        msg.step = w * 3
        # NOTE: rclpy coerces `Image.data` to `array.array('B')` on assignment, so
        # `msg.data == payload` is False by *type* even when the bytes match. Keep
        # the original around and compare content — and this is why
        # `frame_from_image_msg` calls `bytes(msg.data)` rather than assuming it
        # already has a buffer of the type numpy wants.
        msg.data = payload
        img_pub.publish(msg)

        frame = transport.capture("drone1")
        check("capture() returned a frame", frame is not None)
        check(
            "frame has the published shape",
            tuple(frame.shape) == (h, w, 3),
            f"{tuple(frame.shape)}",
        )
        check(
            "frame pixels survived the round-trip",
            bytes(bytearray(frame.reshape(-1).tolist())) == payload,
        )

        # ---- capture is fresh-or-fail, not last-seen ------------------------ #
        try:
            transport.capture("drone1")
            check("a served frame is not handed out twice", False, "got it again")
        except rb.FrameTimeout:
            check("a served frame is not handed out twice", True, "FrameTimeout")

        # ---- pose: quaternion -> yaw over the wire -------------------------- #
        yaw = math.radians(30.0)
        pmsg = PoseStamped()
        pmsg.pose.position.x, pmsg.pose.position.y, pmsg.pose.position.z = 1.0, 2.0, 3.0
        pmsg.pose.orientation.z = math.sin(yaw / 2.0)
        pmsg.pose.orientation.w = math.cos(yaw / 2.0)
        pose_pub.publish(pmsg)

        pose = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                pose = transport.pose("drone1")
                break
            except rb.FrameTimeout:
                transport.step()
        check("pose() returned a pose", pose is not None)
        if pose is not None:
            check(
                "position survived", (pose.x, pose.y, pose.z) == (1.0, 2.0, 3.0), f"{pose}"
            )
            check(
                "yaw decoded from the quaternion",
                abs(pose.yaw - yaw) < 1e-6,
                f"{math.degrees(pose.yaw):.3f} deg",
            )

        # ---- write_panel: publish AND write through ------------------------- #
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and fault_node.count_publishers(
            rb.FAULT_TOPIC
        ) == 0:
            rclpy.spin_once(fault_node, timeout_sec=0.05)

        transport.write_panel(
            "R00-C001", PanelState.SOILED, "smoke", "2026-07-29T00:00:00+00:00"
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not received:
            rclpy.spin_once(fault_node, timeout_sec=0.05)

        check("fault event arrived on /mission/fault", bool(received))
        if received:
            report = rb.fault_from_payload(received[0])
            check("payload is a FaultReport", report.panel_id == "R00-C001")
            check("fault_type is the taxonomy value", report.fault_type == "soiled")
            check(
                "geo position came through",
                report.panel_geo_position == (24.088, 69.418, 31.0),
                f"{report.panel_geo_position}",
            )
        check(
            "write_panel also wrote through to the store (USD stays truth)",
            len(store.written) == 1,
            f"{store.written}",
        )
    finally:
        transport.close()
        pub_node.destroy_node()
        fault_node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} leg(s) failed: {', '.join(FAILURES)}")
        return 1
    print("all legs passed — the ROS 2 Transport seam is real")
    return 0


if __name__ == "__main__":
    sys.exit(main())
