"""Pure waypoint-interpolation math for kinematic `RobotControl` impls.

`control/kinematic.py` (Isaac-bound, built on the Spark — plan.md Workstream D)
wraps this around an actual Xform prim: read the prim's current transform,
call `step_towards` once per physics tick, write the result back with
`prim.GetAttribute(...).Set(...)` / `Xformable.AddTranslateOp` etc. This module
has no knowledge of USD/Isaac and is fully unit-testable off the Spark. Track
S: import `step_towards`/`reached` here rather than reimplementing the math —
if the signature must change, treat it as a breaking change to this seam and
flag it (mission tests + the Spark controller both depend on it).

Pure-python: no Isaac import (golden rule).
"""

from __future__ import annotations

import math

from solar_twin.control.base import Waypoint


def _wrap_angle(a: float) -> float:
    """Wrap an angle (radians) to (-pi, pi]."""
    return math.atan2(math.sin(a), math.cos(a))


def step_towards(
    current: Waypoint,
    target: Waypoint,
    speed: float,
    dt: float,
    angular_speed: float = math.pi,
) -> Waypoint:
    """One kinematic tick toward ``target``.

    Moves at most ``speed * dt`` meters along the straight line from
    ``current`` to ``target``'s position, and turns at most
    ``angular_speed * dt`` radians toward ``target.yaw`` (shortest direction).
    Never overshoots either — repeated calls converge on ``target`` exactly
    and then hold it, so a caller can step this every tick without special
    "arrived" handling.
    """
    dx, dy, dz = target.x - current.x, target.y - current.y, target.z - current.z
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    max_move = max(speed, 0.0) * max(dt, 0.0)
    if dist <= max_move or dist == 0.0:
        x, y, z = target.x, target.y, target.z
    else:
        frac = max_move / dist
        x = current.x + dx * frac
        y = current.y + dy * frac
        z = current.z + dz * frac

    dyaw = _wrap_angle(target.yaw - current.yaw)
    max_turn = max(angular_speed, 0.0) * max(dt, 0.0)
    if abs(dyaw) <= max_turn:
        yaw = target.yaw
    else:
        yaw = _wrap_angle(current.yaw + math.copysign(max_turn, dyaw))

    return Waypoint(x=x, y=y, z=z, yaw=yaw)


def reached(current: Waypoint, target: Waypoint, tol: float = 0.05) -> bool:
    """Position-only tolerance check (matches `FakeSimBackend.at_goal` — a
    camera-carrying drone's yaw is cosmetic, not a goal condition for Slice 0)."""
    return (
        abs(current.x - target.x) <= tol
        and abs(current.y - target.y) <= tol
        and abs(current.z - target.z) <= tol
    )


def steps_to_reach(
    start: Waypoint,
    target: Waypoint,
    speed: float,
    dt: float,
    tol: float = 0.05,
    max_steps: int = 100_000,
) -> int:
    """Simulate `step_towards` from `start` until `reached`; return the step
    count. Useful in tests and for a Track S caller estimating mission timing.
    ``max_steps`` guards against a zero/negative speed looping forever."""
    current = start
    n = 0
    while not reached(current, target, tol) and n < max_steps:
        current = step_towards(current, target, speed, dt)
        n += 1
    return n
