"""Open a built plant in Isaac Sim and fly around it (Isaac-bound).

The answer to "why can't I just open this and look at it?" — which had no answer,
because the only way to see the twin was to watch a mission go past and the app
closed the moment that mission ended.

    ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh

    # a window on this machine's monitor (DGX: the HDMI seat is usually :1)
    DISPLAY=:1 PYTHONPATH=src $ISAAC -m solar_twin.world.viewer \\
        assets/khavda_bladeshadow.usd --gui

    # or headless + WebRTC, to watch from a laptop
    PYTHONPATH=src $ISAAC -m solar_twin.world.viewer assets/khavda_full.usd

No mission, no perception, no verdicts: this is the stage exactly as
`farm_builder` authored it (or exactly as a run left it, if you point at a
post-run export). Turbines turn, so the blade shadow sweeps — the one thing a
static USD opened in the editor by hand cannot show you.

## What this is NOT

⚠ **No fleet.** The drone and ground bot are built into the *live* stage at run
time by `sim_runtime` (`build_quadcopter` / `build_ugv`); they are never saved to
the USD. A stage opened here has panels, terrain, turbines and site works, and no
robots. Use `run.py --gui --hold` if you want the fleet parked in the scene.

⚠ **Nothing is simulated.** Physics is not stepped here any more than it is during
a run (`SimRuntime.step` spins rotors and draws a frame; there is no
`SimulationContext`). Turning blades are a kinematic proxy. This is a viewer.

Alternative worth knowing: Isaac Sim's own `isaac-sim.streaming.sh` will open any
of these USDs with the full editor UI and no code from us at all. This module
exists for the case where you want the project's own conventions applied — the
camera set up the way our runs see it, and the turbines turning.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Open a built farm USD in Isaac Sim and fly around it."
    )
    ap.add_argument("usd", help="a stage built by world.farm_builder")
    ap.add_argument(
        "--gui",
        action="store_true",
        help="open a window on THIS machine's display (needs DISPLAY; the DGX's HDMI "
        "seat is usually :1). Without it the app runs headless and streams over "
        "WebRTC instead — connect the Isaac Sim WebRTC Streaming Client "
        "(signal 49100 / stream 47998).",
    )
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument(
        "--still",
        action="store_true",
        help="do not turn the turbines — a static scene, if you want to inspect one "
        "blade-shadow position rather than watch it sweep",
    )
    ap.add_argument(
        "--no-tonemap",
        action="store_true",
        help="skip the photographic exposure. ON by default here, unlike a KPI run: "
        "without it the desert blows out to paper white (measured f/5 -> 195/255 vs "
        "f/9 -> 118). A viewer exists to be looked at, so it should be exposed.",
    )
    args = ap.parse_args(argv)

    usd = Path(args.usd)
    if not usd.exists():
        raise SystemExit(
            f"{usd} not found — build it first, e.g.:\n"
            f"  PYTHONPATH=src $ISAACSIM_PYTHON_EXE -m solar_twin.world.farm_builder "
            f"<farm.yaml|--scenario ...> --out {usd}"
        )

    # Imported here, not at module scope: this is the Isaac boundary, and keeping it
    # inside main() means `python3 -c "import solar_twin.world.viewer"` still works
    # off-Isaac (which is what the import-hygiene test checks).
    from solar_twin.world.sim_runtime import SimRuntime

    # `livestream` and `headless` are mutually exclusive in SimRuntime: livestream is
    # headless-with-UI. --gui therefore means a real window and no stream.
    runtime = SimRuntime(
        str(usd),
        camera_robots=[],
        marker_robots=[],
        headless=not args.gui,
        resolution=(args.width, args.height),
        livestream=not args.gui,
        tonemap=not args.no_tonemap,
    )
    print(f"opened {usd}", flush=True)
    runtime.hold(free_camera=True, spin_turbines=not args.still)
    runtime.close()
    return 0


if __name__ == "__main__":  # pragma: no cover — Isaac-bound entry point
    raise SystemExit(main())
