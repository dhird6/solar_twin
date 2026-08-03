#!/usr/bin/env python3
"""Are NVIDIA's library robots usable as our fleet? Measure, don't assume.

Our robots are `UsdGeom.Cube` + `UsdGeom.Cylinder` (`world/robot_builder.py`) — the
"black flying box". Isaac ships a real robot library, so the obvious question is why
we are not using it. This answers that with measurements rather than opinion:

* does the asset resolve and open at all (the library is CLOUD-hosted — `data/` on
  this build is 8 KB, so every asset is an https fetch),
* what are its REAL dimensions (⚠ a raw import is not SimReady: `CLAUDE.md` records
  an STL that came back `metersPerUnit=0.001`, i.e. 1000x wrong),
* is it articulated — joints and an ArticulationRoot — or just a shell that looks
  like a robot and cannot be driven.

That last one is the whole question for us: a prettier mesh that cannot move is a
downgrade from a cube that can.

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh
    PYTHONPATH=src $ISAAC tools/probe_library_robots.py
"""

from __future__ import annotations

import sys

ROOT = (
    "https://omniverse-content-production.s3-us-west-2.amazonaws.com"
    "/Assets/Isaac/6.0/Isaac/Robots"
)

#: Candidates, chosen against what `world/fleet_specs.py` already names as our fleet.
#: ⚠ Note what is NOT here: Clearpath's **Husky**, which is the rover our specs are
#: derived from. The library ships Jackal and Dingo instead. That mismatch is the
#: point of this probe, not an oversight — see the summary it prints.
CANDIDATES = [
    ("clearpath-jackal", f"{ROOT}/Clearpath/Jackal/jackal.usd"),
    ("clearpath-dingo", f"{ROOT}/Clearpath/Dingo/dingo.usd"),
    ("nvidia-nova-carter", f"{ROOT}/NVIDIA/NovaCarter/nova_carter.usd"),
    ("bitcraze-crazyflie", f"{ROOT}/Bitcraze/Crazyflie/cf2x.usd"),
    ("isaacsim-quadcopter", f"{ROOT}/IsaacSim/Quadcopter/quadcopter.usd"),
]


def main() -> int:
    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True})

    from pxr import Usd, UsdGeom, UsdPhysics  # noqa: PLC0415

    print(f"\n{'asset':<22} {'m/unit':>7} {'L x W x H (m)':>22} {'joints':>7} "
          f"{'artic':>6} {'prims':>7}")
    print("-" * 78)

    rows = []
    for name, url in CANDIDATES:
        try:
            stage = Usd.Stage.Open(url)
            if stage is None:
                print(f"{name:<22} FAILED TO OPEN")
                continue
            mpu = UsdGeom.GetStageMetersPerUnit(stage)
            prims = list(stage.Traverse())
            joints = [p for p in prims if p.IsA(UsdPhysics.Joint)]
            artic = [p for p in prims if p.HasAPI(UsdPhysics.ArticulationRootAPI)]

            # World-space extent of the default prim, in METRES (mpu applied).
            cache = UsdGeom.BBoxCache(
                Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
            )
            rng = cache.ComputeWorldBound(stage.GetDefaultPrim()).ComputeAlignedRange()
            size = rng.GetSize()
            dims = tuple(round(float(s) * mpu, 3) for s in size)

            print(f"{name:<22} {mpu:>7.4f} {str(dims):>22} {len(joints):>7} "
                  f"{len(artic):>6} {len(prims):>7}")
            rows.append((name, mpu, dims, len(joints), len(artic)))
        except Exception as exc:  # noqa: BLE001 — a probe reports, never raises
            print(f"{name:<22} ERROR {type(exc).__name__}: {str(exc)[:40]}")

    print("\nWhat this means for our fleet (world/fleet_specs.py):")
    print("  our ground bot is spec'd as husky-a200-class: 0.990 x 0.670 x 0.390 m")
    print("  our drone     is spec'd as dji-m350-class:    0.895 m diagonal, 6.47 kg")
    for name, mpu, dims, joints, artic in rows:
        note = []
        if abs(mpu - 1.0) > 1e-6:
            note.append(f"⚠ metersPerUnit={mpu} — NOT metres, must be scaled")
        if joints == 0:
            note.append("⚠ no joints — a shell, cannot be driven")
        if artic == 0:
            note.append("⚠ no ArticulationRoot")
        print(f"  {name:<22} {' · '.join(note) if note else 'articulated, metric'}")

    app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
