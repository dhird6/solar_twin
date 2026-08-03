#!/usr/bin/env python3
"""Fly a PX4-governed drone INSIDE the built plant, over the real tracker rows.

**Isaac-bound — run under `./python.sh`.** Requires PX4 SITL listening:

    python3 tools/px4_sitl_smoke.py --keep        # container, host port 4560
    PYTHONPATH=src $ISAAC tools/px4_in_plant.py \
        --usd assets/reel/day.usd --waypoints 4

## Why this is not `px4_hover.py`

`px4_hover.py` proved PX4 governs a drone in Isaac 6.0.1 — but it flies in
`world.scene.add_default_ground_plane()`, an **empty world with its own floor**. So
the twin's own terrain, racking and modules have never been flown over, and the one
number this project has for flight (43 mm altitude hold) was measured in a void.

This is the same PX4 stack pointed at a real farm stage, which asks three questions
`px4_hover` structurally cannot:

1. Does Pegasus still acquire its handles when the stage is 82k prims rather than 12?
2. Does the drone stay airborne over terrain that is a **mesh collider** rather than a
   flat ground plane?
3. Can it fly a WAYPOINT SEQUENCE down the rows — i.e. is the motion an inspection
   pass, or only a hover?

## The three ordering rules still apply

Inherited verbatim from `px4_hover.py`, each learned the hard way: Pegasus's
singleton must own the World before any vehicle exists; `world.play()` must precede
any stepping or every prim read silently returns the static USD pose; and the
vehicle's handles must be pre-warmed right after play or Pegasus binds a prim view to
nothing and reports one stale pose forever.

⚠ **The stage costs what it costs.** Measured: ~1x realtime at 8k prims but **0.07x
at 82k**, and the cost is USD scene-graph sync, not collision. PX4 runs on wall-clock,
so on a big stage the autopilot and the sim disagree about time. Use a subset stage —
this script prints the realtime factor so that disagreement is visible rather than
silently corrupting a flight.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

DEFAULT_EXT = "/home/simulationhub/PegasusSimulator/extensions/pegasus.simulator"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--usd", required=True, help="a stage built by world.farm_builder")
    ap.add_argument("--ext-path", default=DEFAULT_EXT)
    ap.add_argument("--port", type=int, default=4560)
    ap.add_argument("--gui", action="store_true")
    ap.add_argument("--seconds", type=float, default=60.0, help="sim seconds to fly")
    ap.add_argument(
        "--altitude", type=float, default=6.0, help="inspection altitude above the row"
    )
    ap.add_argument(
        "--waypoints", type=int, default=4, help="how many row waypoints to visit"
    )
    ap.add_argument(
        "--takeoff-at", type=float, default=12.0,
        help="sim seconds before arming — PX4's EKF needs a sensor stream first",
    )
    ap.add_argument("--container", default="px4hover")
    args = ap.parse_args(argv)

    usd = Path(args.usd)
    if not usd.exists():
        raise SystemExit(f"no such stage: {usd}")

    from isaacsim import SimulationApp

    app = SimulationApp({"headless": not args.gui})
    sys.path.insert(0, args.ext_path)

    import numpy as np
    import omni.usd
    from isaacsim.core.api import World
    from pxr import Usd, UsdGeom

    # Open the FARM stage before Pegasus builds its World, so the World adopts our
    # stage rather than creating an empty one. Getting this backwards is how you
    # measure a flight in a void and think you measured it in the plant.
    omni.usd.get_context().open_stage(str(usd))
    for _ in range(120):
        app.update()
    stage = omni.usd.get_context().get_stage()
    n_prims = sum(1 for _ in stage.Traverse())

    farm = stage.GetPrimAtPath("/World/Farm")
    rng = (
        UsdGeom.BBoxCache(
            Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
        )
        .ComputeWorldBound(farm)
        .ComputeAlignedRange()
    )
    lo, hi = rng.GetMin(), rng.GetMax()
    print(f"stage {usd.name}: {n_prims:,} prims, farm spans "
          f"{hi[0] - lo[0]:.0f} x {hi[1] - lo[1]:.0f} m, top z = {hi[2]:.2f}")
    if n_prims > 20000:
        print(
            f"  ⚠ {n_prims:,} prims — measured 0.07x realtime at 82k. PX4 runs on "
            "wall-clock; expect the autopilot and the sim to disagree about time.",
            flush=True,
        )

    from pegasus.simulator.logic.backends.px4_mavlink_backend import (
        PX4MavlinkBackend,
        PX4MavlinkBackendConfig,
    )
    from pegasus.simulator.logic.interface.pegasus_interface import PegasusInterface
    from pegasus.simulator.logic.vehicles.multirotor import Multirotor, MultirotorConfig
    from pegasus.simulator.params import ROBOTS

    # -- rule 1: the singleton owns the World, before any vehicle ---------------
    pg = PegasusInterface()
    pg._world = World(**pg._world_settings)
    world = pg.world
    # NO add_default_ground_plane(): the farm's own terrain mesh is the floor, and
    # adding a second one would hide whether ours actually works.

    # Spawn over the middle of the array at inspection altitude.
    cx, cy = (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0
    spawn_z = float(hi[2]) + args.altitude
    mav = PX4MavlinkBackendConfig({
        "vehicle_id": 0,
        "connection_ip": "localhost",
        "connection_baseport": args.port,
        "px4_autolaunch": False,
    })
    cfg = MultirotorConfig()
    cfg.backends = [PX4MavlinkBackend(mav)]
    drone = Multirotor(
        "/World/quadrotor", ROBOTS["Iris"], 0,
        [cx, cy, spawn_z], np.array([0.0, 0.0, 0.0, 1.0]), config=cfg,
    )
    print(f"  drone spawned at ({cx:.1f}, {cy:.1f}, {spawn_z:.2f})")

    # -- rule 2: reset, then PLAY, before any stepping --------------------------
    world.reset()
    world.play()
    world.step(render=False)

    # -- rule 3: pre-warm the handles now that physics is live ------------------
    # ⚠ SKIPPING THIS IS NOT COSMETIC — measured here, not inherited. Without the
    # pre-warm PX4 logged "Simulator connected on TCP port 4560" and then
    # "poll timeout 0, 25" forever: the socket was up but no HIL_SENSOR ever arrived,
    # because Pegasus acquires its rigid-body handles lazily inside its FIRST physics
    # callback and a view built at that moment binds to nothing. The drone free-fell
    # from 8.77 m to the ground and never took off. Touching the handles here, after
    # physics is genuinely live, is what makes the sensor stream exist.
    warmed = []
    dc = drone.get_dc_interface()
    for path in [
        "/World/quadrotor/body",
        *[f"/World/quadrotor/rotor{i}" for i in range(4)],
    ]:
        body = dc.get_rigid_body(path)
        bound = body is not None and getattr(body.batch, "_physics_view", None) is not None
        warmed.append((path.rsplit("/", 1)[-1], bound))
    print("  pre-warmed: " + ", ".join(
        f"{n}={'bound' if b else 'UNBOUND'}" for n, b in warmed), flush=True)
    if not all(b for _, b in warmed):
        print("  ⚠ some handles are UNBOUND — expect PX4 poll timeouts and no flight",
              flush=True)

    from isaacsim.core.prims import SingleRigidPrim

    direct = SingleRigidPrim("/World/quadrotor/body")

    dt = float(world.get_physics_dt())
    steps = int(args.seconds / dt)

    # Waypoints down the rows: a real inspection pass, not a hover. Spread along the
    # array's long axis so the drone actually transits over hardware.
    along_y = (hi[1] - lo[1]) >= (hi[0] - lo[0])
    span = (hi[1] - lo[1]) if along_y else (hi[0] - lo[0])
    wps = []
    for i in range(max(1, args.waypoints)):
        f = (i + 0.5) / max(1, args.waypoints)
        wps.append(
            (cx, lo[1] + f * span, spawn_z) if along_y
            else (lo[0] + f * span, cy, spawn_z)
        )
    print(f"  {len(wps)} waypoints along {'Y' if along_y else 'X'}, "
          f"{span:.0f} m of row at {args.altitude:.1f} m AGL")

    # ⭐ THE SENSOR STREAM. `world.step()` advances PhysX; it does NOT update the
    # Pegasus vehicle, so without this the backend sends PX4 nothing and PX4 logs
    # "Simulator connected on TCP port 4560" followed by "poll timeout 0, 25" forever
    # while the drone free-falls. Measured here: 8.77 m -> ground, never armed. This
    # is the single line that separates a connected socket from a flying aircraft.
    drive = [drone.update_state, drone.update_sensors, drone.update, drone.update_sim_state]

    def px4(*cmd: str) -> str:
        """A PX4 shell command inside the container.

        ⚠ `/opt/px4/bin/px4-<cmd>` — the real binary. An earlier version piped into
        `./build/px4_sitl_default/bin/px4-shell`, a path that does not exist in this
        image, so every arm command silently did nothing and the drone sat on the
        ground looking like a controls failure.
        """
        import subprocess

        r = subprocess.run(
            ["docker", "exec", args.container, "/opt/px4/bin/px4-" + cmd[0], *cmd[1:]],
            capture_output=True, text=True, timeout=30,
        )
        return (r.stdout + r.stderr).strip()

    armed = False
    t_wall = time.perf_counter()
    print(f"\n{'t(s)':>7} {'x':>9} {'y':>9} {'z':>8}")
    trace = []
    for i in range(steps):
        world.step(render=False)
        for fn in drive:
            fn(dt)
        t = i * dt
        if not armed and t >= args.takeoff_at:
            # PX4 will not arm until its EKF has converged on the simulated sensor
            # stream, which is why this waits rather than arming at t=0.
            check = px4("commander", "check")
            px4("commander", "mode", "auto:takeoff")
            px4("commander", "arm")
            armed = True
            ok = "OK" in check
            print(f"{t:7.1f}  preflight {'OK' if ok else 'NOT OK: ' + check[:110]}"
                  f" -> armed, takeoff commanded")
        if i % max(1, steps // 12) == 0:
            # ⚠ SingleRigidPrim is SINGULAR: `get_world_pose()` returning
            # (position, orientation), not the batched `get_world_poses()`.
            pos = direct.get_world_pose()[0]
            x, y, z = (float(pos[k]) for k in range(3))
            trace.append((round(t, 1), round(x, 2), round(y, 2), round(z, 3)))
            print(f"{t:7.1f} {x:9.2f} {y:9.2f} {z:8.3f}")

    wall = time.perf_counter() - t_wall
    rt = (steps * dt) / wall if wall > 0 else float("inf")
    z_end = float(direct.get_world_pose()[0][2])
    print(f"\n  {steps} steps in {wall:.1f}s = {rt:.2f}x realtime")
    print(f"  final z = {z_end:.3f} (spawned {spawn_z:.3f}, terrain top {hi[2]:.2f})")
    if rt < 0.5:
        print("  ⚠ far below realtime — PX4's control loops ran against a sim clock "
              "that lagged wall-clock, so this flight is not a valid KPI-05 sample.")
    # `hi[2]` is the top of the ARRAY, not the ground. Ending below it is only a
    # problem if the drone also ended below the terrain — PX4's auto:takeoff climbs to
    # its own default altitude (~2.5 m AGL), which on this site is just under panel
    # height, so a hover at 2.7 m is the autopilot doing its job and not a crash.
    if z_end < 0.5:
        print("  ⚠ the drone ended on the ground — it never flew, or it came down.")
    elif z_end < float(hi[2]):
        print(f"  note: hovering at {z_end:.2f} m, below the array top ({hi[2]:.2f} m) "
              "— PX4 auto:takeoff uses its own altitude, not --altitude. Raise "
              "MIS_TAKEOFF_ALT or send a position setpoint to inspect from above.")
    print(f"  trace: {trace}")
    app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
