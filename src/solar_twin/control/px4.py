"""`RobotControl` backed by PX4 offboard setpoints — `FR-06`'s final seam.

**Pure-python: no Isaac import.** That is possible because commanding PX4 is a
MAVLink conversation, not a simulator operation: this sends position setpoints and
reads PX4's own estimate. The physics lives in Isaac, the control loops live in
PX4, and this file is only the translator between the mission's waypoints and
PX4's protocol. So it is unit-testable with a fake link, on any machine.

WHAT THIS IS AND IS NOT
----------------------
`kinematic.py` *completes* a move — it places the robot at the waypoint. This
**issues a goal**: `move_to` streams a setpoint and returns immediately, and
`at_goal` reports whether PX4 has got there yet. That difference is exactly what
the ABC's docstring anticipates, and it is the whole point of `FR-06` — the drone
arrives because a real controller flew it, not because we moved it.

⚠ **THE FRAME CONVERSION IS THE DANGEROUS PART, so it is the tested part.**
The twin is **ENU, Z-up** (`CLAUDE.md`: "USD: Z-up, meters"). PX4 is **NED,
Z-down**. Getting this wrong does not raise — it produces a drone that flies to a
mirrored position, or descends when told to climb, and it looks like a tuning
problem. The mapping, in one place, verified in both directions:

    ENU (stage)      NED (PX4)
    x  (east)   ->   y
    y  (north)  ->   x
    z  (up)     ->  -z          (so +3 m altitude is -3 m NED)
    yaw (CCW from +x/east) -> heading (CW from north) = pi/2 - yaw

⚠ **Offboard mode requires a setpoint stream, not a single command.** PX4 drops
out of offboard if setpoints stop arriving (>~0.5 s), so `move_to` re-sends on
every call and the mission's per-tick loop is what keeps the link alive. A
one-shot setpoint would arm, twitch, and fail over to Hold.

⚠ **`at_goal` uses PX4's own estimate**, because that is what the autopilot is
flying to. Do **not** use it to compute `KPI-05`: PX4's altitude and the twin's
ground truth were measured ~0.23 m apart (`RISK-29`), and the autopilot is the
thing under test. Station-keep error comes from `Transport.pose()`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol

from solar_twin.control.base import RobotControl, Waypoint

#: PX4 custom main/sub mode for OFFBOARD, and the MAV_CMD to set it.
_MAV_CMD_DO_SET_MODE = 176
_MAV_MODE_FLAG_CUSTOM_MODE_ENABLED = 1
_PX4_CUSTOM_MAIN_MODE_OFFBOARD = 6
#: Type-mask bits for SET_POSITION_TARGET_LOCAL_NED: ignore velocity,
#: acceleration and yaw-rate, command position + yaw only.
_IGNORE_VEL_ACC_YAWRATE = 0b0000_1011_1111_1000


def enu_to_ned(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Stage ENU (Z-up) -> PX4 NED (Z-down). See the module docstring."""
    return (y, x, -z)


def ned_to_enu(n: float, e: float, d: float) -> tuple[float, float, float]:
    """PX4 NED (Z-down) -> stage ENU (Z-up). The exact inverse of `enu_to_ned`."""
    return (e, n, -d)


def yaw_enu_to_heading(yaw: float) -> float:
    """ENU yaw (radians CCW from +x/east) -> NED heading (CW from north).

    Wrapped to (-pi, pi] because PX4 rejects unwrapped angles, and an unwrapped
    setpoint makes the drone spin the long way round to the same attitude.
    """
    h = math.pi / 2.0 - yaw
    return math.atan2(math.sin(h), math.cos(h))


class MavlinkLink(Protocol):
    """What `PX4Control` needs from a MAVLink connection.

    Narrow on purpose: a fake implements three methods and the whole controller
    becomes testable with no PX4, no Isaac and no network.
    """

    def send_setpoint(self, north: float, east: float, down: float, heading: float) -> None:
        """Stream one position setpoint in NED metres + heading radians."""

    def local_position(self) -> tuple[float, float, float] | None:
        """PX4's current NED estimate, or None if no estimate has arrived yet."""

    def set_offboard(self) -> None:
        """Ask PX4 to enter OFFBOARD mode (idempotent)."""


@dataclass
class PymavlinkLink:
    """The real link, over the GCS UDP port PX4 already publishes on.

    Deliberately separate from Pegasus's `PX4MavlinkBackend`: that one owns the
    **HIL sensor stream** on TCP 4560 (simulator -> PX4). This is the **command**
    channel (GCS -> PX4) on UDP 14550. Two different conversations; sharing one
    socket would interleave them.
    """

    address: str = "udp:127.0.0.1:14550"
    _conn: object | None = field(default=None, repr=False)

    def _link(self):
        if self._conn is None:
            from pymavlink import mavutil  # noqa: PLC0415 — lazy: no import cost off-Spark

            conn = mavutil.mavlink_connection(self.address, source_system=255)
            conn.wait_heartbeat(timeout=30)
            self._conn = conn
        return self._conn

    def send_setpoint(self, north: float, east: float, down: float, heading: float) -> None:
        c = self._link()
        c.mav.set_position_target_local_ned_send(
            0,                      # time_boot_ms (0 = now)
            c.target_system, c.target_component,
            1,                      # MAV_FRAME_LOCAL_NED
            _IGNORE_VEL_ACC_YAWRATE,
            north, east, down,
            0.0, 0.0, 0.0,          # velocity (ignored)
            0.0, 0.0, 0.0,          # acceleration (ignored)
            heading, 0.0,           # yaw, yaw_rate (rate ignored)
        )

    def local_position(self) -> tuple[float, float, float] | None:
        c = self._link()
        msg = c.recv_match(type="LOCAL_POSITION_NED", blocking=False)
        if msg is None:
            return None
        return (float(msg.x), float(msg.y), float(msg.z))

    def set_offboard(self) -> None:
        c = self._link()
        c.mav.command_long_send(
            c.target_system, c.target_component, _MAV_CMD_DO_SET_MODE, 0,
            _MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, _PX4_CUSTOM_MAIN_MODE_OFFBOARD, 0,
            0, 0, 0, 0,
        )


@dataclass
class PX4Control(RobotControl):
    """Fly drones by streaming PX4 offboard setpoints.

    One link per robot id: PX4 SITL is one autopilot per vehicle, so a fleet is
    several instances on different ports rather than one link addressing many.

    Robots with **no** link fall through to `fallback` — which is how a mixed
    fleet works today: the ground bot stays kinematic (`FR-07` keeps that valid)
    while the drones fly under PX4. Without a fallback an unmapped robot raises,
    rather than silently not moving.
    """

    links: dict[str, MavlinkLink] = field(default_factory=dict)
    fallback: RobotControl | None = None
    #: Offboard needs a setpoint stream; PX4 leaves the mode if they stop. This
    #: counts how many have been sent per robot, so a caller can assert the
    #: stream exists rather than assume it.
    sent: dict[str, int] = field(default_factory=dict)
    _goals: dict[str, Waypoint] = field(default_factory=dict, repr=False)
    _offboard: set[str] = field(default_factory=set, repr=False)

    def move_to(self, robot_id: str, waypoint: Waypoint) -> None:
        link = self.links.get(robot_id)
        if link is None:
            if self.fallback is None:
                raise KeyError(
                    f"no PX4 link for {robot_id!r} and no fallback RobotControl — "
                    f"the robot would silently never move"
                )
            self.fallback.move_to(robot_id, waypoint)
            return

        if robot_id not in self._offboard:
            # Requested before the first setpoint: PX4 accepts OFFBOARD only once
            # setpoints are arriving, so callers that re-send are relying on this
            # being idempotent.
            link.set_offboard()
            self._offboard.add(robot_id)

        n, e, d = enu_to_ned(waypoint.x, waypoint.y, waypoint.z)
        link.send_setpoint(n, e, d, yaw_enu_to_heading(waypoint.yaw))
        self._goals[robot_id] = waypoint
        self.sent[robot_id] = self.sent.get(robot_id, 0) + 1

    def at_goal(self, robot_id: str, waypoint: Waypoint, tol: float = 0.05) -> bool:
        link = self.links.get(robot_id)
        if link is None:
            if self.fallback is None:
                raise KeyError(f"no PX4 link for {robot_id!r} and no fallback")
            return self.fallback.at_goal(robot_id, waypoint, tol)

        ned = link.local_position()
        if ned is None:
            # No estimate yet is NOT "arrived". Returning True here would let a
            # mission march through every waypoint before the drone had moved.
            return False
        x, y, z = ned_to_enu(*ned)
        return math.dist((x, y, z), (waypoint.x, waypoint.y, waypoint.z)) <= tol

    def goal(self, robot_id: str) -> Waypoint | None:
        """The last commanded waypoint, for a caller that wants to report error."""
        return self._goals.get(robot_id)
