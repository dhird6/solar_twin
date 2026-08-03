#!/usr/bin/env python3
"""Can PhysX actually step the built farm stage, and how fast? (Isaac-bound)

**Isaac-bound — run under `./python.sh`.**

## The question this answers

The inspection mission has never stepped physics. `SimRuntime.step()` spins rotors,
turns turbine hubs and calls `app.update()` — there is no `SimulationContext`, no
`World`, no `play()`. So `/World/PhysicsScene` and every authored collider on the
panels and turbines are **inert**: they exist in the USD and nothing advances them.
And `tools/px4_hover.py`, the one place real dynamics do run, flies in
`world.scene.add_default_ground_plane()` — an empty world. **PX4 has never been
flown inside our stage.**

Before wiring PX4 into the mission it is worth knowing whether the stage can carry
physics at all. The realism stage is 81,961 prims with 30,016 instanced modules,
273 torque tubes, 5,900 piles and glass; a KPI run needs many thousands of steps.
If PhysX cannot step that at a usable rate, "put the drone on PX4" is the wrong
next move and it is much cheaper to learn that here than after the integration.

Deliberately **no PX4 and no MAVLink**: this isolates *stage cost* from *handshake*.
A falling rigid body is enough to force PhysX to build its scene, cook the
colliders it can, and integrate.

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh
    PYTHONPATH=src $ISAAC tools/physics_probe.py assets/khavda_real.usd
    PYTHONPATH=src $ISAAC tools/physics_probe.py assets/khavda_real.usd --render

Reports steps/second and the body's z over time. `--render` is the honest number for
a watched or camera-carrying run; without it you get the physics-only ceiling.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure PhysX step cost on a farm stage.")
    ap.add_argument("usd", help="a stage built by world.farm_builder")
    ap.add_argument("--steps", type=int, default=600, help="physics steps to time")
    ap.add_argument(
        "--render",
        action="store_true",
        help="render each step. This is what a camera-carrying mission actually pays; "
        "without it you measure the physics-only ceiling.",
    )
    ap.add_argument(
        "--drop-z", type=float, default=12.0, help="spawn height for the test body"
    )
    ap.add_argument("--out", default="", help="directory for a JSON record")
    ap.add_argument("--at", default="", help="drop over 'x,y' instead of the array centre")
    args = ap.parse_args(argv)

    src = Path(args.usd)
    if not src.exists():
        raise SystemExit(f"no such stage: {src}")

    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True})

    import omni.usd
    from isaacsim.core.api import World
    from pxr import Gf, UsdGeom, UsdPhysics

    # Open OUR stage first, then let World adopt it. The other order gives World a
    # fresh empty stage and the farm is never in the scene at all — which would
    # measure nothing and look like a pass.
    omni.usd.get_context().open_stage(str(src))
    for _ in range(120):
        app.update()
    stage = omni.usd.get_context().get_stage()
    n_prims = sum(1 for _ in stage.Traverse())
    scene = stage.GetPrimAtPath("/World/PhysicsScene")
    print(f"stage: {src}  prims={n_prims:,}  PhysicsScene={'yes' if scene else 'NO'}")

    world = World(physics_dt=1.0 / 200.0, rendering_dt=1.0 / 60.0, stage_units_in_meters=1.0)

    # A plain dynamic cube standing in for the drone body. Mass and size are an
    # M350-class figure so the integration cost is representative; nothing here is a
    # flight model (`NFR-07`) — the point is the SCENE's cost, not the vehicle's.
    body_path = "/World/PhysicsProbe/body"
    UsdGeom.Xform.Define(stage, "/World/PhysicsProbe")
    cube = UsdGeom.Cube.Define(stage, body_path)
    cube.CreateSizeAttr(1.0)
    capi = UsdGeom.XformCommonAPI(cube)
    # Over the middle of the array, so it falls toward real hardware and terrain.
    _dx, _dy = (160.0, 300.0)
    if args.at:
        _dx, _dy = (float(v) for v in args.at.split(","))
    capi.SetTranslate(Gf.Vec3d(_dx, _dy, float(args.drop_z)))
    capi.SetScale(Gf.Vec3f(0.45, 0.45, 0.25))
    UsdPhysics.RigidBodyAPI.Apply(cube.GetPrim())
    UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    mass = UsdPhysics.MassAPI.Apply(cube.GetPrim())
    mass.CreateMassAttr(6.47)

    world.reset()
    world.play()
    world.step(render=False)

    t_setup = time.perf_counter()
    print(f"  physics_dt={world.get_physics_dt()}  playing={world.is_playing()}")

    xf = UsdGeom.Xformable(stage.GetPrimAtPath(body_path))

    def z_now() -> float:
        m = xf.ComputeLocalToWorldTransform(0)
        return float(m.ExtractTranslation()[2])

    z0 = z_now()
    samples = []
    t0 = time.perf_counter()
    for i in range(args.steps):
        world.step(render=args.render)
        if i % max(1, args.steps // 10) == 0:
            samples.append((i, round(z_now(), 4)))
    elapsed = time.perf_counter() - t0
    z1 = z_now()

    rate = args.steps / elapsed if elapsed > 0 else float("inf")
    sim_seconds = args.steps * float(world.get_physics_dt())
    realtime = sim_seconds / elapsed if elapsed > 0 else float("inf")

    print(f"\n  z: {z0:.3f} -> {z1:.3f} m  (fell {z0 - z1:.3f} m)")
    print(f"  z trace: {samples}")
    print(
        f"\n  {args.steps} steps in {elapsed:.2f} s "
        f"= {rate:.0f} steps/s, {realtime:.2f}x realtime  "
        f"(render={'on' if args.render else 'off'})"
    )

    # Did physics actually do anything? A body that never moved means PhysX was not
    # integrating — the exact silent failure `px4_hover.py`'s rule 2 documents, where
    # every prim read returns the static USD pose and looks like a stable hover.
    moved = abs(z0 - z1) > 0.01
    verdict = []
    if not moved:
        verdict.append(
            "⚠ THE BODY NEVER MOVED — physics is not integrating (a free body must "
            "fall). Every pose read here is the static USD value."
        )
    else:
        # Free fall from `drop_z` over `sim_seconds` would be 0.5*g*t^2 if nothing
        # stopped it; landing short of that means something was hit.
        free_fall = 0.5 * 9.81 * sim_seconds**2
        if (z0 - z1) < free_fall * 0.85:
            verdict.append(
                f"landed / was stopped: fell {z0 - z1:.2f} m where free fall over "
                f"{sim_seconds:.2f} s would be {free_fall:.2f} m — colliders ARE live."
            )
        else:
            verdict.append(
                f"fell freely ({z0 - z1:.2f} m vs {free_fall:.2f} m predicted) — "
                "nothing was hit; terrain/hardware colliders may not be cooked."
            )
    if realtime < 1.0:
        verdict.append(
            f"⚠ SLOWER THAN REALTIME ({realtime:.2f}x). A PX4 loop expects wall-clock "
            "pace; below 1x the flight controller and the sim disagree about time."
        )
    for v in verdict:
        print(f"  {v}")

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "physics_probe.json").write_text(
            json.dumps(
                {
                    "stage": str(src),
                    "prims": n_prims,
                    "has_physics_scene": bool(scene),
                    "steps": args.steps,
                    "render": bool(args.render),
                    "physics_dt": float(world.get_physics_dt()),
                    "elapsed_s": round(elapsed, 3),
                    "steps_per_s": round(rate, 1),
                    "realtime_factor": round(realtime, 3),
                    "z_start": round(z0, 4),
                    "z_end": round(z1, 4),
                    "z_trace": samples,
                    "body_moved": moved,
                    "setup_to_first_step_s": round(t0 - t_setup, 3),
                },
                indent=2,
            )
        )
        print(f"\nwrote {out / 'physics_probe.json'}")

    app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
