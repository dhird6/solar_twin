"""Slice 0 entry point: config -> run mission -> emit a run record.

    ./python.sh -m solar_twin.run configs/farm.yaml configs/mission.yaml

Every run writes ``runs/<timestamp>/`` with the configs used, the seed, and a
``results.json`` of injected-vs-detected per panel + timings — demo material and
regression baseline (Principle §2.8).

Backends:
  --backend fake         pure-python, no Isaac (the Brain spine; runs anywhere)
  --backend sim_native   the real Isaac world (Slice 0 Day 6-8, on the Spark)

Importing this module stays Isaac-free; the sim_native backend is imported
lazily only when selected.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

import yaml

from solar_twin.orchestrator.mission import Fleet, Mission
from solar_twin.world.layout import FarmLayout


def _load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _build_backend(name: str, layout: FarmLayout, mission_cfg: dict, sim_opts: dict):
    """Return (transport, control). Both interfaces may be one object."""
    if name == "fake":
        from solar_twin.orchestrator.fake_backend import FakeSimBackend

        backend = FakeSimBackend(layout.panel_records())
        return backend, backend
    if name == "sim_native":
        # Lazy Isaac import — only reached under ./python.sh on the Spark.
        from pathlib import Path as _Path

        from solar_twin.control.kinematic import KinematicControl
        from solar_twin.schema import pv_module as pv
        from solar_twin.transport.sim_native import SimNativeTransport
        from solar_twin.world.sim_runtime import SimRuntime

        farm_usd = sim_opts["farm_usd"]
        if not _Path(farm_usd).exists():
            raise FileNotFoundError(
                f"{farm_usd} not found — build it first:\n"
                f"  ./python.sh -m solar_twin.world.farm_builder <farm.yaml> --out {farm_usd}"
            )
        fleet = mission_cfg["fleet"]
        overview_pose = None
        if sim_opts.get("record"):
            xs = [s.position[0] for s in layout.sites]
            cx = (min(xs) + max(xs)) / 2 if xs else 0.0
            overview_pose = (cx, 0.0, 30.0)  # bird's-eye over the row
        runtime = SimRuntime(
            farm_usd,
            camera_robots=[fleet["screen_drone"], fleet["confirm_drone"]],
            marker_robots=[fleet["ground_bot"]],
            headless=sim_opts["headless"],
            resolution=sim_opts["resolution"],
            overview_pose=overview_pose,
        )
        panel_paths = {
            s.panel_id: pv.panel_path("/World/Farm", s.row, s.col)
            for s in layout.sites
        }
        return SimNativeTransport(runtime, panel_paths), KinematicControl(runtime)
    raise ValueError(f"unknown backend: {name!r}")


def _perception(name: str, opts: dict | None = None):
    opts = opts or {}
    if name == "ground_truth":
        from solar_twin.perception.ground_truth import GroundTruthPerception

        return GroundTruthPerception()
    if name == "cosmos_reason":
        # Config-driven: endpoint/model/timeout come from mission.yaml
        # perception_opts, not hardcoded. Requires a Cosmos Reason NIM
        # (⚠ verify served-model-name — see docs/ENVIRONMENT.md).
        from solar_twin.perception.cosmos_reason import (
            DEFAULT_BASE_URL,
            DEFAULT_MODEL,
            DEFAULT_TIMEOUT_S,
            CosmosReasonPerception,
        )

        return CosmosReasonPerception(
            base_url=opts.get("base_url", DEFAULT_BASE_URL),
            model=opts.get("model", DEFAULT_MODEL),
            timeout=float(opts.get("timeout", DEFAULT_TIMEOUT_S)),
        )
    raise NotImplementedError(
        f"perception {name!r} not wired yet (Slice 0 uses ground_truth)."
    )


def run(
    farm_path: str,
    mission_path: str,
    backend_name: str,
    runs_dir: str,
    sim_opts: dict | None = None,
) -> Path:
    farm_cfg = _load_yaml(farm_path)
    mission_cfg = _load_yaml(mission_path)

    layout = FarmLayout(farm_cfg)
    transport, control = _build_backend(
        backend_name, layout, mission_cfg, sim_opts or {}
    )
    perception = _perception(
        mission_cfg.get("perception", "ground_truth"),
        mission_cfg.get("perception_opts", {}),
    )
    fleet_cfg = mission_cfg["fleet"]
    fleet = Fleet(
        ground_bot=fleet_cfg["ground_bot"],
        screen_drone=fleet_cfg["screen_drone"],
        confirm_drone=fleet_cfg["confirm_drone"],
    )

    targets = layout.inspection_targets(mission_cfg)
    faults = layout.seeded_faults()

    sim_opts = sim_opts or {}
    record = sim_opts.get("record") and hasattr(transport, "capture_overview")
    frames: list = []

    def _progress(i: int, r) -> None:
        tag = f"{r.detected_state} ESCALATED" if r.escalated else r.detected_state
        print(f"  [{i + 1}/{len(targets)}] {r.panel_id}: {tag}", flush=True)
        if record:
            fr = transport.capture_overview()
            if fr is not None:
                frames.append(fr[..., :3])

    mission = Mission(transport, control, perception, fleet)
    t0 = time.perf_counter()
    result = mission.run(targets, on_result=_progress)
    wall_s = time.perf_counter() - t0

    # ---- run record --------------------------------------------------- #
    # Write (and print) the record BEFORE closing the sim: SimulationApp.close()
    # terminates the process, so anything after it would never run.
    ts = time.strftime("%Y%m%dT%H%M%S")
    out = Path(runs_dir) / ts
    out.mkdir(parents=True, exist_ok=True)

    (out / "farm.yaml").write_text(yaml.safe_dump(farm_cfg, sort_keys=False))
    (out / "mission.yaml").write_text(yaml.safe_dump(mission_cfg, sort_keys=False))

    record = {
        "timestamp": ts,
        "backend": backend_name,
        "seed": farm_cfg.get("seed"),
        "n_panels": layout.n_panels,
        "injected_faults": {pid: s.value for pid, s in faults.items()},
        "metrics": {
            "panels_inspected": result.panels_inspected,
            "faults_detected": result.faults_detected,
            "detection_rate": result.detection_rate,
            "sim_steps": result.steps,
            "wall_seconds": round(wall_s, 4),
        },
        "panels": [asdict(r) for r in result.results],
        "fault_events": [e.to_dict() for e in result.fault_events],
    }
    (out / "results.json").write_text(json.dumps(record, indent=2))
    m = record["metrics"]
    print(
        f"run record: {out}\n"
        f"panels={m['panels_inspected']} faults={m['faults_detected']} "
        f"detection_rate={m['detection_rate']:.2f} "
        f"injected={len(record['injected_faults'])}",
        flush=True,
    )

    # ---- optional artifacts (before close) ---------------------------- #
    if sim_opts.get("save_usd") and hasattr(transport, "export_usd"):
        usd_out = out / "farm_post.usda"
        transport.export_usd(str(usd_out))
        print(f"saved post-run USD (verdicts on prims): {usd_out}", flush=True)

    if record_frames := (frames if record else []):
        try:
            import imageio.v2 as imageio

            vid = out / "run.mp4"
            writer = imageio.get_writer(str(vid), fps=2, macro_block_size=None)
            for fr in record_frames:
                writer.append_data(fr)
            writer.close()
            print(f"wrote run video ({len(record_frames)} frames): {vid}", flush=True)
        except Exception as exc:  # noqa: BLE001 — video is a nice-to-have
            print(f"[warn] video write failed: {exc}", flush=True)

    # Close the sim LAST (may terminate the process).
    if hasattr(transport, "close"):
        transport.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run a Slice 0 inspection mission.")
    ap.add_argument("farm", help="path to farm.yaml")
    ap.add_argument("mission", help="path to mission.yaml")
    ap.add_argument(
        "--backend",
        default="sim_native",
        choices=["sim_native", "fake"],
        help="sim_native = Isaac world (Spark); fake = pure-python spine",
    )
    ap.add_argument("--runs-dir", default="runs", help="where to write run records")
    ap.add_argument(
        "--farm-usd",
        default="assets/farm.usd",
        help="built USD farm (sim_native); build via world.farm_builder",
    )
    ap.add_argument("--gui", action="store_true", help="sim_native: show the Isaac window")
    ap.add_argument("--width", type=int, default=640, help="sim_native camera width")
    ap.add_argument("--height", type=int, default=480, help="sim_native camera height")
    ap.add_argument(
        "--save-usd",
        action="store_true",
        help="sim_native: export the post-run stage (verdicts on prims) to the run dir",
    )
    ap.add_argument(
        "--record",
        action="store_true",
        help="sim_native: capture a bird's-eye run video (run.mp4) to the run dir",
    )
    args = ap.parse_args(argv)

    sim_opts = {
        "farm_usd": args.farm_usd,
        "headless": not args.gui,
        "resolution": (args.width, args.height),
        "save_usd": args.save_usd,
        "record": args.record,
    }
    # run() writes + prints the record before closing the sim (which may
    # terminate the process), so no extra printing is needed here.
    run(args.farm, args.mission, args.backend, args.runs_dir, sim_opts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
