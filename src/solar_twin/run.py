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


def _build_backend(name: str, layout: FarmLayout, mission_cfg: dict):
    """Return (transport, control). Both interfaces may be one object."""
    if name == "fake":
        from solar_twin.orchestrator.fake_backend import FakeSimBackend

        backend = FakeSimBackend(layout.panel_records())
        return backend, backend
    if name == "sim_native":
        # Lazy Isaac import — only reached under ./python.sh on the Spark.
        raise NotImplementedError(
            "sim_native backend is the Spark half (Slice 0 Day 6-8): build "
            "world/farm_builder.py + world/sim_runtime.py + transport/sim_native.py, "
            "then wire them here. Use --backend fake to run the Brain spine now."
        )
    raise ValueError(f"unknown backend: {name!r}")


def _perception(name: str):
    if name == "ground_truth":
        from solar_twin.perception.ground_truth import GroundTruthPerception

        return GroundTruthPerception()
    raise NotImplementedError(
        f"perception {name!r} not wired yet (Slice 0 uses ground_truth)."
    )


def run(farm_path: str, mission_path: str, backend_name: str, runs_dir: str) -> Path:
    farm_cfg = _load_yaml(farm_path)
    mission_cfg = _load_yaml(mission_path)

    layout = FarmLayout(farm_cfg)
    transport, control = _build_backend(backend_name, layout, mission_cfg)
    perception = _perception(mission_cfg.get("perception", "ground_truth"))
    fleet_cfg = mission_cfg["fleet"]
    fleet = Fleet(
        ground_bot=fleet_cfg["ground_bot"],
        screen_drone=fleet_cfg["screen_drone"],
        confirm_drone=fleet_cfg["confirm_drone"],
    )

    targets = layout.inspection_targets(mission_cfg)
    faults = layout.seeded_faults()

    mission = Mission(transport, control, perception, fleet)
    t0 = time.perf_counter()
    result = mission.run(targets)
    wall_s = time.perf_counter() - t0

    # ---- run record --------------------------------------------------- #
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
    args = ap.parse_args(argv)

    out = run(args.farm, args.mission, args.backend, args.runs_dir)
    record = json.loads((out / "results.json").read_text())
    m = record["metrics"]
    print(f"run record: {out}")
    print(
        f"panels={m['panels_inspected']} faults={m['faults_detected']} "
        f"detection_rate={m['detection_rate']:.2f} "
        f"injected={len(record['injected_faults'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
