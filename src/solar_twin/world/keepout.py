"""Turbine keep-out volumes (no-fly) — pure geometry, Isaac-free.

A wind turbine's rotor sweeps a disk; conservatively we forbid a SPHERE of radius
``blade_len + margin`` centred on the hub, UNION a vertical tower cylinder. The
drone planner must never place a waypoint — or fly the straight segment between two
waypoints — inside these volumes.

This constrains the *plan*, so it is **control-agnostic**: it protects a kinematic
(teleport) drone today and a Pegasus/PX4 drone later without changing. It is the
planning-layer complement to the PhysX colliders authored on the turbine (which
only *physically* bite once the drone has rigid-body dynamics).

No Isaac import here — unit-tested without pxr.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from solar_twin.world.layout import terrain_height


@dataclass(frozen=True)
class TurbineKeepout:
    """A single turbine's forbidden volume: rotor sphere ∪ tower cylinder."""

    hub: tuple[float, float, float]  # world (x, y, z) of the rotor hub
    rotor_radius: float              # blade_len + margin (the swept sphere)
    tower_xy: tuple[float, float]
    tower_bottom_z: float
    tower_top_z: float
    tower_radius: float              # tower radius + margin

    def violation(self, x: float, y: float, z: float) -> float:
        """Signed penetration (m) into the volume: >0 inside (unsafe), <=0 clear.
        The max over the rotor sphere and the tower cylinder."""
        dx, dy, dz = x - self.hub[0], y - self.hub[1], z - self.hub[2]
        d_sphere = self.rotor_radius - math.sqrt(dx * dx + dy * dy + dz * dz)
        if self.tower_bottom_z <= z <= self.tower_top_z:
            rxy = math.hypot(x - self.tower_xy[0], y - self.tower_xy[1])
            d_tower = self.tower_radius - rxy
        else:
            d_tower = -math.inf
        return max(d_sphere, d_tower)

    def clears(self, x: float, y: float, z: float) -> bool:
        return self.violation(x, y, z) <= 0.0

    def push_out(self, x: float, y: float, z: float, eps: float = 0.05):
        """Nearest safe point just outside the volume. If the point is inside the
        rotor sphere, push it radially outward from the hub; the tower is thin
        enough that the rotor sphere dominates near the hub, so this is a good
        conservative clamp for waypoints."""
        if self.clears(x, y, z):
            return (x, y, z)
        dx, dy, dz = x - self.hub[0], y - self.hub[1], z - self.hub[2]
        dist = math.sqrt(dx * dx + dy * dy + dz * dz) or 1e-9
        s = (self.rotor_radius + eps) / dist
        return (self.hub[0] + dx * s, self.hub[1] + dy * s, self.hub[2] + dz * s)


def build_keepouts(
    farm_cfg: dict, rotor_margin: float = 2.0, tower_margin: float = 1.0
) -> list[TurbineKeepout]:
    """Keep-out volumes for every turbine in ``farm_cfg``, on the shared terrain."""
    outs: list[TurbineKeepout] = []
    for spec in farm_cfg.get("turbines", []) or []:
        x, y = float(spec["pos"][0]), float(spec["pos"][1])
        gz = terrain_height(x, y, farm_cfg)
        hub_h = float(spec.get("hub_height", 18.0))
        blade = float(spec.get("blade_len", 8.0))
        outs.append(
            TurbineKeepout(
                hub=(x, y, gz + hub_h),
                rotor_radius=blade + rotor_margin,
                tower_xy=(x, y),
                tower_bottom_z=gz,
                tower_top_z=gz + hub_h,
                tower_radius=float(spec.get("tower_radius", 0.6)) + tower_margin,
            )
        )
    return outs


def worst_violation(keepouts: list[TurbineKeepout], x: float, y: float, z: float) -> float:
    """Largest penetration across all keep-outs (>0 = unsafe)."""
    return max((k.violation(x, y, z) for k in keepouts), default=-math.inf)


def clamp_out(keepouts: list[TurbineKeepout], x: float, y: float, z: float):
    """Push a point out of whichever keep-out it most deeply violates. One pass is
    enough here because turbines are spaced far apart relative to their radii."""
    if not keepouts:
        return (x, y, z)
    worst = max(keepouts, key=lambda k: k.violation(x, y, z))
    return worst.push_out(x, y, z)


def segment_clear(keepouts: list[TurbineKeepout], a, b, samples: int = 32) -> bool:
    """True if the straight segment a→b never enters a keep-out (sampled). This is
    the meaningful check even for a teleporting drone: it validates the intended
    flight path, not just the endpoints."""
    for i in range(samples + 1):
        t = i / samples
        p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
        if worst_violation(keepouts, *p) > 0.0:
            return False
    return True
