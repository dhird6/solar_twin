"""Kinematic RobotControl (Slice 0) — move an Xform to the waypoint.

No flight dynamics either way: the robot is kinematically placed, it is not
flown. Later this swaps for real controllers / Pegasus PX4 without touching the
mission. Two modes:

* **teleport** (default) — one-shot placement. Two sim steps per panel, which is
  what the measurement runs want: nothing is learned from watching a camera
  traverse an aisle 560 times.
* **interpolated** (`speeds=...`) — steps `kinematic_math.step_towards` per tick
  so the vehicle actually travels between waypoints. Costs ~10x the sim steps
  and exists for the demo video: a teleporting drone cannot show *how* it
  inspects, only where it ended up.

Both are kinematic, so `NFR-07` applies to each — the interpolated path is
constant-speed and ignores mass, thrust, wind and traction. It is animation.

Pure-python: it drives any object exposing `set_pose`/`get_pose` (a `SimRuntime`
in the sim, or a fake in tests) — no Isaac import here.
"""

from __future__ import annotations

import math
from typing import Callable, Optional

from solar_twin.control.base import RobotControl, Waypoint
from solar_twin.control.kinematic_math import reached, step_towards

#: Hard stop on the interpolation loop. A waypoint that is unreachable (zero
#: speed, or a tolerance smaller than one step) must not spin forever; it should
#: land the robot and move on, loudly rather than by hanging the mission.
#:
#: ⚠ It is also a **distance** ceiling, which is the non-obvious part: the reach of
#: one `move_to` is `max_ticks * dt * cruise_speed`. At the defaults (4000, 0.1 s,
#: 6 m/s ground) that is 2.4 km — fine inside one block, short of the 4.84 km whole
#: plot. Raise `max_ticks` for a stage bigger than the budget rather than letting
#: every long commute end in the "did not reach" warning and a snap.
_MAX_TICKS = 4000


class KinematicControl(RobotControl):
    def __init__(
        self,
        runtime,
        speeds: Optional[dict[str, float]] = None,
        dt: float = 0.1,
        on_tick: Optional[Callable[[str], None]] = None,
        cruise_speeds: Optional[dict[str, float]] = None,
        cruise_above_m: float = 6.0,
        max_ticks: int = _MAX_TICKS,
    ):
        """`runtime` exposes set_pose(id, x, y, z, yaw), get_pose(id) and
        (for interpolated motion) step(n).

        `speeds` maps robot_id -> m/s. Omit it (or pass a speed <= 0) to keep the
        Slice 0 teleport. `on_tick(robot_id)` fires after every interpolated step
        with the sim already advanced — the video recorder grabs its frames there.

        **`cruise_speeds` is not a nicety.** A real survey drone transits between
        work at cruise and slows down to take the shot; flying an entire plant at
        inspection speed is not what the hardware does. It is also the difference
        between a demo that runs and one that does not: the first table of the
        full Khavda block is ~490 m from the stage origin, and covering that at
        1 m/s in 0.1 s ticks is 4,900 rendered frames for a commute — which is
        exactly how this hung. Beyond `cruise_above_m` of remaining distance the
        robot moves at its cruise speed, then eases back to inspection speed for
        the approach.
        """
        self._rt = runtime
        self._speeds = dict(speeds or {})
        self._cruise = dict(cruise_speeds or {})
        self._cruise_above = float(cruise_above_m)
        self._dt = float(dt)
        self._on_tick = on_tick
        self._max_ticks = int(max_ticks)

    def set_on_tick(self, callback: Optional[Callable[[str], None]]) -> None:
        """Set the per-tick observer after construction. The recorder needs the
        `SimRuntime`, which only exists once the backend is built, so it cannot
        be passed to the constructor."""
        self._on_tick = callback

    def move_to(self, robot_id: str, waypoint: Waypoint) -> None:
        speed = float(self._speeds.get(robot_id, 0.0))
        if speed <= 0.0:
            self._rt.set_pose(robot_id, waypoint.x, waypoint.y, waypoint.z, waypoint.yaw)
            return

        cruise = float(self._cruise.get(robot_id, 0.0)) or speed
        x, y, z, yaw = self._rt.get_pose(robot_id)
        current = Waypoint(x, y, z, yaw)
        for _ in range(self._max_ticks):
            if reached(current, waypoint):
                break
            remaining = math.dist(
                (current.x, current.y, current.z), (waypoint.x, waypoint.y, waypoint.z)
            )
            v = cruise if remaining > self._cruise_above else speed
            current = step_towards(current, waypoint, v, self._dt)
            # Yaw is left to the runtime, which derives heading from the motion
            # delta — passing this waypoint's yaw would snap the nose to the goal
            # orientation on tick one and undo that.
            self._rt.set_pose(robot_id, current.x, current.y, current.z)
            self._rt.step(1)
            if self._on_tick is not None:
                self._on_tick(robot_id)
        else:
            print(
                f"  [warn] {robot_id} did not reach {waypoint} in {self._max_ticks} "
                f"ticks ({self._max_ticks * self._dt * (cruise or speed):.0f} m of "
                "travel); placing it directly",
                flush=True,
            )
        # Land exactly on the waypoint: the interpolation stops within `reached`'s
        # tolerance, and the camera standoff should not inherit that slop.
        self._rt.set_pose(robot_id, waypoint.x, waypoint.y, waypoint.z, waypoint.yaw)

    def at_goal(self, robot_id: str, waypoint: Waypoint, tol: float = 0.05) -> bool:
        # Share one tolerance definition with the interp math (the N3->S4 seam).
        x, y, z, yaw = self._rt.get_pose(robot_id)
        return reached(Waypoint(x, y, z, yaw), waypoint, tol)
