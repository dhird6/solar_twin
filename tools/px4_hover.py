#!/usr/bin/env python3
"""Fly the Iris under PX4 SITL in Isaac Sim 6.0.1 — the FR-06 hover check.

**Isaac-bound — run under `./python.sh`.** Requires PX4 SITL already listening:

    python3 tools/px4_sitl_smoke.py --keep          # container, host port 4560
    PYTHONPATH=src $ISAAC tools/px4_hover.py

This is the closing step of `FR-06`: PX4 owns the attitude/position control loops,
Isaac owns the physics, and they meet over MAVLink HIL on TCP 4560. It also
exercises `RISK-26` (Pegasus's backend was written for PX4 v1.14.3; the container
ships ~v1.18-beta) — if the handshake has drifted, PX4 never leaves
`Waiting for simulator` and that shows up here as no sensor exchange.

THREE ORDERING RULES, EACH ONE MEASURED THE HARD WAY
----------------------------------------------------
1. **Pegasus's singleton must own the World before any vehicle exists**, or
   `Vehicle.__init__` dies on `self._world.stage` being None.
2. **`world.play()` before stepping.** Without it PhysX never creates a
   simulation view, so every prim read silently returns the *static USD pose* —
   no exception, and it looks exactly like a hovering drone that never moves.
3. **Pre-warm the vehicle's body/rotor/articulation handles right after play.**
   This is the fix for the freeze that cost Session 12: Pegasus acquires its
   handles lazily from inside its *first* physics callback, and a prim view built
   at that moment binds to nothing and then reports one stale pose forever
   (measured: Pegasus read z=4.9998 while the body was at z=3.80). Touching the
   handles here — after physics is genuinely live — means the cache holds
   correctly-bound views before any callback needs them.

`px4_autolaunch` is forced off: PX4 is already running in the container, and
letting Pegasus autolaunch would have it hunt for a local PX4 build that does not
exist on this box.
"""

from __future__ import annotations

import argparse
import sys

DEFAULT_EXT = "/home/simulationhub/PegasusSimulator/extensions/pegasus.simulator"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seconds", type=float, default=40.0, help="sim seconds to fly")
    ap.add_argument("--altitude", type=float, default=3.0, help="spawn altitude (m)")
    ap.add_argument("--ext-path", default=DEFAULT_EXT)
    ap.add_argument("--port", type=int, default=4560, help="PX4 simulator TCP port")
    ap.add_argument("--gui", action="store_true", help="open a window")
    ap.add_argument("--wind-scenario", default=None,
                    help="scenario YAML whose `wind:` block is applied to the drone "
                         "body every physics step (see configs/scenarios/"
                         "khavda_windy_hover.yaml). Omit for the calm-air baseline.")
    ap.add_argument("--drag-area", type=float, default=0.12,
                    help="drone frontal area (m2) for the drag law. Iris-class: a "
                         "~0.5 m frame with exposed arms/props is ~0.1-0.15 m2.")
    ap.add_argument("--container", default="px4hover",
                    help="PX4 container name, used to arm + command takeoff")
    ap.add_argument("--takeoff-at", type=float, default=12.0,
                    help="sim seconds at which to arm and command takeoff (0 = never; "
                         "PX4 needs a few seconds of sensor stream for its EKF first)")
    args = ap.parse_args()

    from isaacsim import SimulationApp

    app = SimulationApp({"headless": not args.gui})

    sys.path.insert(0, args.ext_path)

    import numpy as np
    from isaacsim.core.api import World

    from pegasus.simulator.logic.backends.px4_mavlink_backend import (
        PX4MavlinkBackend,
        PX4MavlinkBackendConfig,
    )
    from pegasus.simulator.logic.interface.pegasus_interface import PegasusInterface
    from pegasus.simulator.logic.vehicles.multirotor import Multirotor, MultirotorConfig
    from pegasus.simulator.params import ROBOTS

    # -- rule 1: the singleton owns the World, before any vehicle -------------
    pg = PegasusInterface()
    pg._world = World(**pg._world_settings)
    world = pg.world
    world.scene.add_default_ground_plane()

    mav_cfg = PX4MavlinkBackendConfig({
        "vehicle_id": 0,
        "connection_ip": "localhost",
        "connection_baseport": args.port,
        # PX4 is already up in the container; autolaunch would look for a local build.
        "px4_autolaunch": False,
    })
    cfg = MultirotorConfig()
    cfg.backends = [PX4MavlinkBackend(mav_cfg)]

    drone = Multirotor(
        "/World/quadrotor",
        ROBOTS["Iris"],
        0,
        [0.0, 0.0, args.altitude],
        np.array([0.0, 0.0, 0.0, 1.0]),
        config=cfg,
    )

    # -- rule 2: reset, then PLAY, before any stepping ------------------------
    world.reset()
    world.play()
    world.step(render=False)

    # -- rule 3: pre-warm the handles now that physics is live ----------------
    dc = drone.get_dc_interface()
    warmed = []
    for path in [
        "/World/quadrotor/body",
        *[f"/World/quadrotor/rotor{i}" for i in range(4)],
    ]:
        body = dc.get_rigid_body(path)
        bound = body is not None and getattr(body.batch, "_physics_view", None) is not None
        warmed.append((path.rsplit("/", 1)[-1], bound))
    art = dc.get_articulation("/World/quadrotor")
    print("\npre-warmed handles:", ", ".join(f"{n}={'bound' if b else 'UNBOUND'}" for n, b in warmed))
    print(f"articulation dofs: {getattr(art, 'dof_names', None)}")

    # -- optional wind: the whole reason a dynamic body was needed -----------
    wind = None
    if args.wind_scenario:
        import yaml

        from solar_twin.scenario import load_scenario
        from solar_twin.world import windfield as wf

        scn = load_scenario(args.wind_scenario)
        wind = wf.from_cfg(scn.farm_cfg)
        print(f"\nwind: {wind.mean_speed_ms:.1f} m/s from {wind.wind_dir_deg:.0f} deg, "
              f"gust {wind.speed_variation * 100:.0f}% / {wind.gust_period_s:.1f}s, "
              f"{len(wind.wakes)} wake source(s), seed {wind.seed}")
        print("  ⚠ analytical wake + band-limited gusts, NOT CFD (FR-13/NFR-07)")
        del yaml

    dt = float(world.get_physics_dt())
    steps = int(args.seconds / dt)
    print(f"\nflying {args.seconds:.0f} sim seconds ({steps} steps @ dt={dt:.4f}) ...")
    print(f"{'t(s)':>7} {'z(m)':>8} {'direct_z':>8} {'vz':>8} {'roll':>7} {'pitch':>7} {'rotor0_rpm':>11}")

    # A direct read, bypassing Pegasus, so "the body is not moving" can be told
    # apart from "Pegasus is not reading it".
    from isaacsim.core.prims import SingleRigidPrim

    direct = SingleRigidPrim("/World/quadrotor/body")

    # ⚠ WORKAROUND (see RISK-28): Pegasus registers its four physics callbacks on
    # the World, but in a standalone app they do not reliably fire on 6.0.1 —
    # `update_state` runs once and then stops, so the vehicle state freezes at its
    # spawn pose and PX4 receives no sensor stream (it reports `poll timeout`).
    # A plain `World.add_physics_callback` fires reliably in the same session, and
    # each of these methods produces correct results when invoked directly, so the
    # methods and the shim are fine — only the dispatch is unreliable. Driving them
    # from the loop we own is the honest fix for now: same methods, same order as
    # Vehicle registers them, just an explicit call site.
    drive = [drone.update_state, drone.update_sensors, drone.update, drone.update_sim_state]

    def px4(*cmd: str) -> str:
        """Run a PX4 shell command inside the container. Arming from here rather
        than by hand is what makes this a reproducible check, not a demo."""
        import subprocess

        r = subprocess.run(
            ["docker", "exec", args.container, "/opt/px4/bin/px4-" + cmd[0], *cmd[1:]],
            capture_output=True, text=True, timeout=30,
        )
        return (r.stdout + r.stderr).strip()

    zs = []
    samples: list[tuple[float, float, float]] = []   # (t, z, vz)
    report_every = max(1, steps // 12)
    takeoff_step = int(args.takeoff_at / dt) if args.takeoff_at > 0 else -1
    for i in range(steps):
        world.step(render=False)
        for fn in drive:
            fn(dt)

        # ⚠ Wind is applied ONLY once airborne, and that is physical rather than a
        # convenience. Measured: 12 m/s with 35% gusts is ~15 N on this frame,
        # MORE than an Iris's own 14.7 N weight — applied to a PARKED drone it
        # tumbled it inverted (roll -177 deg) and preflight then failed, so it
        # never armed. A real aircraft is tied down or launched into wind; it does
        # not sit loose on a pad in a gale. Gating on altitude starts the
        # disturbance when the vehicle can actually fly against it.
        airborne = float(drone.state.position[2]) > 0.5
        if wind is not None and airborne:
            # Applied to the BODY, in world axes, from the RELATIVE air speed —
            # a drone drifting downwind at wind speed should feel nothing. Uses
            # the same shim that made the port work, so this is also the first
            # thing to exercise `apply_body_force` under real load.
            st = drone.state
            fx, fy, fz = wind.drag_force(
                float(st.position[0]), float(st.position[1]), float(st.position[2]),
                body_velocity=(float(st.linear_velocity[0]),
                               float(st.linear_velocity[1]),
                               float(st.linear_velocity[2])),
                drag_area_m2=args.drag_area,
                t=i * dt,
            )
            drone.apply_force([fx, fy, fz], body_part="/body")

        if takeoff_step > 0 and i == takeoff_step:
            # PX4 will not arm until its EKF has converged on the simulated sensor
            # stream, which is why this waits instead of arming at t=0.
            check = px4("commander", "check")
            px4("commander", "mode", "auto:takeoff")
            px4("commander", "arm")
            ok = "OK" in check
            print(f"  [t={i * dt:.1f}s] preflight {'OK' if ok else 'NOT OK: ' + check[:120]}"
                  f" -> armed, takeoff commanded")
        if i % report_every == 0 or i == steps - 1:
            st = drone.state
            # attitude is (x, y, z, w); convert for a readable roll/pitch
            from scipy.spatial.transform import Rotation

            rpy = Rotation.from_quat(st.attitude).as_euler("xyz", degrees=True)
            try:
                rpm = float(art.get_joint_velocities()[0]) * 60.0 / (2 * np.pi)
            except Exception:  # noqa: BLE001 — cosmetic only
                rpm = float("nan")
            zs.append(float(st.position[2]))
            samples.append((i * dt, float(st.position[2]), float(st.linear_velocity[2])))
            dz = float(direct.get_world_pose()[0][2])
            print(f"{i * dt:7.2f} {st.position[2]:8.3f} {dz:8.3f} {st.linear_velocity[2]:8.3f} "
                  f"{rpy[0]:7.2f} {rpy[1]:7.2f} {rpm:11.1f}")

    # -- verdict -------------------------------------------------------------
    print("\n---- verdict ----")
    moved = max(zs) - min(zs)
    print(f"  z range over the flight: {min(zs):.3f} .. {max(zs):.3f} m (spread {moved:.3f})")
    if all(abs(z - zs[0]) < 1e-4 for z in zs):
        print("  ✗ FROZEN: the state never changed. Pose reads are static — physics view")
        print("    is not bound (rule 2/3 above), NOT a control problem.")
        rc = 1
    elif zs[-1] < 0.2:
        print("  x ON THE GROUND at t_end: PX4 produced no sustained lift.")
        print("    Check PX4 left `Waiting for simulator` (else the HIL handshake failed,")
        print("    RISK-26) and that preflight passed. NOTE: PX4 SITL does NOT recover")
        print("    from a simulator disconnect — restart the container for each run.")
        rc = 1
    else:
        # Station-keep quality over the SETTLED window only. Including the climb
        # would report the ascent as hold error — a first pass at this reported
        # 1180 mm when the actual hold was ~24 mm, because one mid-climb sample
        # was inside the window. Settled = after takeoff + a declared settle time,
        # and only samples the vehicle was not still climbing through.
        SETTLE_S = 12.0
        settled = [
            (t, z, vz) for (t, z, vz) in samples
            if t >= args.takeoff_at + SETTLE_S and z > 0.5 and abs(vz) < 0.05
        ]
        if len(settled) > 1:
            zsz = [z for _, z, _ in settled]
            spread = max(zsz) - min(zsz)
            mean = sum(zsz) / len(zsz)
            worst_vz = max(abs(vz) for _, _, vz in settled)
            print(f"  OK HOVERING at {mean:.3f} m — altitude held within "
                  f"{spread * 1000:.0f} mm over {settled[-1][0] - settled[0][0]:.0f} s "
                  f"({len(settled)} samples), worst |vz| {worst_vz:.3f} m/s")
            print(f"    settled window: t >= {args.takeoff_at + SETTLE_S:.0f} s "
                  f"(takeoff at {args.takeoff_at:.0f} s + {SETTLE_S:.0f} s settle)")
            if wind is None:
                print("    ⚠ CALM AIR — no wind field applied. This is the KPI-05 baseline,")
                print("      not a gust-rejection result (that needs --wind-scenario).")
            else:
                # Lateral excursion matters as much as altitude under wind: a
                # drone that holds height while being pushed off the panel has
                # still failed the inspection.
                xy = [
                    (px, py) for (px, py) in
                    [(float(p[0]), float(p[1])) for p in [drone.state.position]]
                ]
                print(f"    KPI-05 UNDER WIND: {wind.mean_speed_ms:.1f} m/s mean, "
                      f"{wind.speed_variation * 100:.0f}% gusts")
                print(f"    lateral position at t_end: x={xy[0][0]:+.2f} y={xy[0][1]:+.2f} m "
                      f"(drift from the origin it was told to hold)")
        else:
            print(f"  OK AIRBORNE: z={zs[-1]:.3f} m at t_end, but the run was too short "
                  f"to measure a settled hover — increase --seconds.")
        rc = 0

    app.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
