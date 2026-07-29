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

from solar_twin.control.safe import SafeControl
from solar_twin.kpi import gates as kpi_gates_mod
from solar_twin.kpi import variance as kpi_variance
from solar_twin.orchestrator.mission import Fleet, Mission
from solar_twin.world.keepout import build_keepouts
from solar_twin.world.layout import FarmLayout


#: Human labels for the FSM phases, for the demo video's caption. The enum names
#: are fine in a log and terse on screen.
_PHASE_LABELS = {
    "ADVANCE": "ground bot advancing",
    "SCREEN": "screening pass",
    "CONFIRM": "close confirm pass",
    "WRITEBACK": "writing verdict to USD",
}


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
        # The chase camera is wanted by the video AND by a live viewport, but only
        # the video needs a render product behind it.
        capture_overview = bool(sim_opts.get("record") or sim_opts.get("video"))
        watching = bool(sim_opts.get("livestream") or not sim_opts["headless"])
        overview_pose = None
        if capture_overview or watching:
            xs = [s.position[0] for s in layout.sites]
            cx = (min(xs) + max(xs)) / 2 if xs else 0.0
            overview_pose = (cx, 0.0, 30.0)  # bird's-eye; re-aimed as a chase cam
        runtime = SimRuntime(
            farm_usd,
            camera_robots=[fleet["screen_drone"], fleet["confirm_drone"]],
            marker_robots=[fleet["ground_bot"]],
            headless=sim_opts["headless"],
            resolution=sim_opts["resolution"],
            overview_pose=overview_pose,
            overview_capture=capture_overview,
            livestream=bool(sim_opts.get("livestream")),
        )
        panel_paths = {
            s.panel_id: pv.panel_path("/World/Farm", s.row, s.col)
            for s in layout.sites
        }
        # Interpolated flight is opt-in: it costs ~10x the sim steps, and only a
        # human watching (video, window, or stream) needs to see the robot travel
        # (see kinematic.py). Teleport stays the default so KPI runs cannot
        # silently pick up the extra steps.
        kin = mission_cfg.get("kinematics", {}) or {}
        speeds = {}
        if sim_opts.get("video") or sim_opts.get("live"):
            speeds = {
                fleet["ground_bot"]: float(kin.get("bot_speed", 1.0)),
                fleet["screen_drone"]: float(kin.get("drone_speed", 2.0)),
                fleet["confirm_drone"]: float(kin.get("drone_speed", 2.0)),
            }
        control = KinematicControl(runtime, speeds=speeds, dt=float(kin.get("dt", 0.1)))
        return SimNativeTransport(runtime, panel_paths), control
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
            # Decoding is pinned greedy by default; `sampling:` in
            # perception_opts merges onto those defaults (never replaces them).
            sampling=dict(opts.get("sampling") or {}),
        )
    raise NotImplementedError(
        f"perception {name!r} not wired yet (Slice 0 uses ground_truth)."
    )


def _perception_provenance(name: str, perception) -> dict:
    """What judged the panels, stamped into every run record. A KPI whose
    decoding config is not recorded cannot be reproduced or defended."""
    prov = {"name": name}
    if hasattr(perception, "provenance"):
        prov.update(perception.provenance())
    return prov


def run(
    farm_path: str,
    mission_path: str,
    backend_name: str,
    runs_dir: str,
    sim_opts: dict | None = None,
    farm_cfg: dict | None = None,
    mission_cfg: dict | None = None,
    scenario_name: str | None = None,
    kpi_gates: dict | None = None,
) -> Path:
    # Pre-composed dicts (from a scenario) win over path loading, so a scenario
    # variant runs through the exact same pipeline without temp config files.
    farm_cfg = farm_cfg if farm_cfg is not None else _load_yaml(farm_path)
    mission_cfg = mission_cfg if mission_cfg is not None else _load_yaml(mission_path)

    layout = FarmLayout(farm_cfg)
    transport, control = _build_backend(
        backend_name, layout, mission_cfg, sim_opts or {}
    )
    # Planning-layer no-fly: vet every commanded waypoint against turbine keep-out
    # volumes (control-agnostic, so it protects kinematic and future PX4 alike).
    # `layout` matters when the config scatters turbines: the keep-outs must
    # resolve from the same field the builder authored, not from a stale list.
    keepouts = build_keepouts(farm_cfg, layout)
    if keepouts:
        control = SafeControl(control, keepouts)
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
    max_panels = int(sim_opts.get("max_panels") or 0)
    if max_panels and max_panels < len(targets):
        # Loud, not silent: a shortened sweep changes every denominator in the
        # run record, so it is stated here and stamped into the record below.
        print(
            f"  [note] --max-panels {max_panels}: inspecting the first "
            f"{max_panels} of {len(targets)} panels",
            flush=True,
        )
        targets = targets[:max_panels]

    record_overview = sim_opts.get("record") and hasattr(transport, "capture_overview")
    frames: list = []

    # --- repeats: a KPI is a sample, not a constant ------------------------- #
    n_repeats = max(1, int(sim_opts.get("repeat") or 1))
    if n_repeats > 1:
        if sim_opts.get("video"):
            raise ValueError(
                "--repeat with --video: the video path uses interpolated motion "
                "and ~10x the sim steps — it is a demo, not a measurement. "
                "Run them separately."
            )
        if not hasattr(transport, "snapshot_panels"):
            raise ValueError(
                f"--repeat {n_repeats} needs a transport that can rewind panel "
                f"state; {type(transport).__name__} cannot. Without it, repeat 2 "
                "reads repeat 1's verdicts as ground truth and every "
                "injected_state in the record is wrong."
            )

    # --- optional demo video (chase view + drone camera, captioned) --------
    recorder = None
    runtime = getattr(transport, "runtime", None)
    if sim_opts.get("video") and runtime is not None and hasattr(runtime, "capture_pair"):
        from solar_twin.world.recorder import Caption, RunRecorder

        recorder = RunRecorder(fps=int(sim_opts.get("video_fps", 15)))
        recorder.caption = Caption(
            subtitle=" · ".join(
                (
                    scenario_name or (Path(farm_path).stem if farm_path else "farm"),
                    str(mission_cfg.get("perception", "ground_truth")),
                    f"{len(targets)} panels",
                )
            )
        )
        screen_drone = fleet_cfg["screen_drone"]
        ground_bot = fleet_cfg["ground_bot"]

        def _on_tick(robot_id: str) -> None:
            runtime.chase(robot_id)
            # The ground bot carries no camera, so keep the drone's view in the
            # inset while the bot advances rather than blanking the picture.
            main, inset = runtime.capture_pair(
                screen_drone if robot_id == ground_bot else robot_id
            )
            recorder.add(main, inset)

        target_ctl = control.inner if isinstance(control, SafeControl) else control
        if hasattr(target_ctl, "set_on_tick"):
            target_ctl.set_on_tick(_on_tick)

        def _on_phase(panel_id: str, phase: str) -> None:
            if panel_id != recorder.caption.panel_id:
                # Drop the previous panel's verdict the moment the caption names
                # a new one — otherwise the overlay reads as a verdict for a
                # panel the fleet has not looked at yet.
                recorder.caption.verdict = ""
            recorder.caption.panel_id = panel_id
            recorder.caption.phase = _PHASE_LABELS.get(phase, phase.title())
    else:
        _on_phase = None

    # --- live view: a window or a WebRTC stream, nothing written to disk ----
    # Distinct from --video, which renders offscreen products and stitches an mp4.
    # Here the human IS the consumer, so all this needs is the viewport aimed at
    # the chase camera and that camera kept on the fleet.
    live_view = (
        not sim_opts.get("video")
        and runtime is not None
        and getattr(runtime, "interactive", False)
    )
    if live_view:
        runtime.set_viewport_camera("/World/Overview")
        target_ctl = control.inner if isinstance(control, SafeControl) else control
        if hasattr(target_ctl, "set_on_tick"):
            # With --live this fires every interpolation tick, so the camera flies
            # with the drone. Under teleport it never fires and the per-panel
            # chase below is the only thing moving the view.
            target_ctl.set_on_tick(runtime.chase)

    def _progress(i: int, r) -> None:
        tag = f"{r.detected_state} ESCALATED" if r.escalated else r.detected_state
        print(f"  [{i + 1}/{len(targets)}] {r.panel_id}: {tag}", flush=True)
        if live_view:
            # Re-aim on the drone that actually made the call, and pump the app so
            # the viewport repaints — Kit only draws when update() is called, so a
            # loop that never steps shows a frozen window.
            runtime.chase(fleet_cfg["confirm_drone"] if r.escalated else fleet_cfg["screen_drone"])
            runtime.step(2)
        if recorder is not None:
            recorder.caption.verdict = (
                f"{r.detected_state.upper()}"
                if r.detected_state != "healthy"
                else "healthy"
            )
            # Hold the finished verdict on screen for a beat, or a 15 fps video
            # flashes each result for a single frame and is unreadable. Show the
            # camera the verdict actually came FROM: an escalated panel was
            # judged on the confirm drone's close pass, and the screening drone
            # is looking down at the confirm drone by then anyway.
            judged_by = fleet_cfg["confirm_drone"] if r.escalated else fleet_cfg["screen_drone"]
            runtime.chase(judged_by)
            main, inset = runtime.capture_pair(judged_by)
            for _ in range(int(sim_opts.get("video_fps", 15)) // 2):
                recorder.add(main, inset)
        if record_overview:
            fr = transport.capture_overview()
            if fr is not None:
                frames.append(fr[..., :3])

    if recorder is not None and targets:
        # Deploy the fleet AT the first panel instead of flying it there from the
        # stage origin. On the full block that origin is ~490 m from the first
        # table, and rendering that commute tick-by-tick is thousands of frames of
        # empty desert before anything is inspected — which is exactly how the
        # first attempt at this appeared to hang. A real survey launches from a
        # point at the work, so this is also the more honest opening.
        first = targets[0]
        runtime.set_pose(
            fleet_cfg["ground_bot"], first.approach.x, first.approach.y, first.approach.z
        )
        runtime.set_pose(
            fleet_cfg["screen_drone"], first.screen.x, first.screen.y, first.screen.z
        )
        runtime.set_pose(
            fleet_cfg["confirm_drone"], first.confirm.x, first.confirm.y, first.confirm.z
        )

    mission = Mission(transport, control, perception, fleet)
    provenance = _perception_provenance(
        str(mission_cfg.get("perception", "ground_truth")), perception
    )

    # ---- run (once, or N repeats of the identical scenario) ------------- #
    ts = time.strftime("%Y%m%dT%H%M%S")
    out = Path(runs_dir) / ts
    out.mkdir(parents=True, exist_ok=True)
    (out / "farm.yaml").write_text(yaml.safe_dump(farm_cfg, sort_keys=False))
    (out / "mission.yaml").write_text(yaml.safe_dump(mission_cfg, sort_keys=False))

    snapshot = (
        transport.snapshot_panels([t.panel_id for t in targets])
        if n_repeats > 1
        else None
    )
    records: list[dict] = []
    for rep in range(n_repeats):
        if rep:
            # Rewind the stage: without this, repeat 2 reads repeat 1's verdict
            # as ground truth (see pv_module.restore_state).
            transport.restore_panels(snapshot)
            print(f"  --- repeat {rep + 1}/{n_repeats} ---", flush=True)
            if isinstance(control, SafeControl):
                control.reset()  # per-repeat keep-out tally, not cumulative
        t0 = time.perf_counter()
        result = mission.run(targets, on_result=_progress, on_phase=_on_phase)
        wall_s = time.perf_counter() - t0

        # ---- run record ------------------------------------------------ #
        # Write (and print) records BEFORE closing the sim: SimulationApp.close()
        # terminates the process, so anything after it would never run.
        record = {
            "timestamp": ts,
            "repeat": rep + 1,
            "repeats": n_repeats,
            "backend": backend_name,
            "scenario": scenario_name,
            "seed": farm_cfg.get("seed"),
            # What judged the panels, including the decoding config — a KPI
            # without this cannot be reproduced.
            "perception": provenance,
            "n_panels": layout.n_panels,
            # Stated explicitly: with --max-panels the stage holds more panels than
            # the mission visited, so `n_panels` is NOT the metric denominator.
            "panels_targeted": len(targets),
            "injected_faults": {pid: s.value for pid, s in faults.items()},
            "metrics": {
                "panels_inspected": result.panels_inspected,
                "faults_detected": result.faults_detected,
                "detection_rate": result.detection_rate,
                "false_fault_rate": result.false_fault_rate,  # KPI-03
                "sim_steps": result.steps,
                "wall_seconds": round(wall_s, 4),
            },
            "panels": [asdict(r) for r in result.results],
            "fault_events": [e.to_dict() for e in result.fault_events],
        }
        if isinstance(control, SafeControl):
            record["keepout"] = {
                "turbines": len(keepouts),
                "waypoints_clamped": len(control.events),
                "min_clearance_m": (
                    None if control.min_clearance_m == float("inf")
                    else round(control.min_clearance_m, 3)
                ),
                "events": [e.to_dict() for e in control.events],
            }
        records.append(record)

        # Single run keeps the historic layout (results.json at the top); repeats
        # get a directory each, so no repeat is silently "the" result.
        rec_dir = out if n_repeats == 1 else out / f"repeat_{rep + 1:02d}"
        rec_dir.mkdir(parents=True, exist_ok=True)
        (rec_dir / "results.json").write_text(json.dumps(record, indent=2))
        m = record["metrics"]
        print(
            f"run record: {rec_dir}\n"
            f"panels={m['panels_inspected']} faults={m['faults_detected']} "
            f"detection_rate={m['detection_rate']:.2f} "
            f"false_fault_rate={m['false_fault_rate']:.3f} "
            f"injected={len(record['injected_faults'])}",
            flush=True,
        )

    # ---- variance across repeats ---------------------------------------- #
    var_report = None
    if n_repeats > 1:
        var_report = kpi_variance.summarize(records)
        (out / "variance.json").write_text(
            json.dumps(var_report.to_dict(), indent=2)
        )
        print(var_report.describe(), flush=True)

    # ---- KPI gates (FR-17) ---------------------------------------------- #
    # The scenario's declared bounds are now CHECKED, not just printed. Repeats
    # are judged on the worst run in the set, never the mean.
    gate_report = None
    if kpi_gates:
        if n_repeats > 1:
            measured = kpi_gates_mod.worst_metrics(records, kpi_gates)
            basis = f"worst-of-{n_repeats}"
        else:
            measured = records[0]["metrics"]
            basis = None
        gate_report = kpi_gates_mod.evaluate(kpi_gates, measured, basis=basis)
        (out / "gates.json").write_text(json.dumps(gate_report.to_dict(), indent=2))
        print(gate_report.describe(), flush=True)

    if n_repeats > 1 or gate_report is not None:
        (out / "summary.json").write_text(
            json.dumps(
                {
                    "timestamp": ts,
                    "scenario": scenario_name,
                    "repeats": n_repeats,
                    "perception": provenance,
                    "variance": var_report.to_dict() if var_report else None,
                    "gates": gate_report.to_dict() if gate_report else None,
                },
                indent=2,
            )
        )

    # ---- optional artifacts (before close) ---------------------------- #
    if sim_opts.get("save_usd") and hasattr(transport, "export_usd"):
        usd_out = out / "farm_post.usda"
        transport.export_usd(str(usd_out))
        print(f"saved post-run USD (verdicts on prims): {usd_out}", flush=True)

    if recorder is not None:
        try:
            vid = recorder.write(str(out / "inspection.mp4"))
            print(
                f"wrote demo video ({len(recorder.frames)} frames @ {recorder.fps} fps): {vid}"
                if vid
                else "[warn] no video frames captured",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 — the video must not lose the run record
            print(f"[warn] demo video write failed: {exc}", flush=True)

    if record_frames := (frames if record_overview else []):
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
    ap.add_argument("farm", nargs="?", help="path to farm.yaml (omit with --scenario)")
    ap.add_argument("mission", nargs="?", help="path to mission.yaml (omit with --scenario)")
    ap.add_argument(
        "--scenario",
        help="path to a scenario YAML (composes farm+mission + hazard overrides "
        "+ kpi_gates, per docs/specs IF-03). Overrides the positional configs.",
    )
    ap.add_argument(
        "--backend",
        default="sim_native",
        choices=["sim_native", "fake"],
        help="sim_native = Isaac world (Spark); fake = pure-python spine",
    )
    ap.add_argument("--runs-dir", default="runs", help="where to write run records")
    ap.add_argument(
        "--subset",
        type=int,
        default=0,
        help="layout.kind=file only: inspect just the first N tracker tables "
        "(contiguous southern band; 0 = all). ⚠ MUST match the --subset used to "
        "build the USD, or the mission targets panels the stage does not contain.",
    )
    ap.add_argument(
        "--farm-usd",
        default="assets/farm.usd",
        help="built USD farm (sim_native); build via world.farm_builder",
    )
    ap.add_argument(
        "--gui",
        action="store_true",
        help="sim_native: open the Isaac Sim window on THIS machine's display and "
        "watch the run live (viewport follows the fleet). Needs a display.",
    )
    ap.add_argument(
        "--livestream",
        action="store_true",
        help="sim_native: run headless but stream the Isaac Sim UI over WebRTC, so "
        "you can watch from another machine — connect the Isaac Sim WebRTC "
        "Streaming Client to this host (signal 49100 / stream 47998).",
    )
    ap.add_argument(
        "--live",
        action="store_true",
        help="interpolated motion WITHOUT recording a video: the fleet actually "
        "flies between waypoints instead of teleporting. Pair with --gui or "
        "--livestream. ⚠ ~10x the sim steps — for watching, not for KPIs.",
    )
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
    ap.add_argument(
        "--video",
        action="store_true",
        help="sim_native: write inspection.mp4 — a chase view of the robot flying "
        "the row with the drone camera inset and the verdict captioned. Switches "
        "the controller from teleport to INTERPOLATED motion (~10x the sim steps), "
        "so pair it with --max-panels; it is a demo, not a measurement run.",
    )
    ap.add_argument("--video-fps", type=int, default=15, help="--video frame rate")
    ap.add_argument(
        "--route",
        choices=["linear", "serpentine"],
        help="panel visit order. serpentine turns round at the end of each table "
        "instead of deadheading 128 m back to the next row's start. Overrides "
        "mission.yaml's `route`.",
    )
    ap.add_argument(
        "--panel-stride",
        type=int,
        default=0,
        help="inspect every Nth panel — a coverage sweep rather than a census. "
        "⚠ changes what the run measures (denominator = panels VISITED).",
    )
    ap.add_argument(
        "--max-panels",
        type=int,
        default=0,
        help="inspect only the first N panels (0 = all). ⚠ changes every "
        "denominator in the run record — for demos, not for KPIs.",
    )
    ap.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="run the SAME scenario N times and report the spread instead of one "
        "number (writes variance.json; panel state is rewound between repeats). "
        "The world is seeded but the VLM is only reproducible when served "
        "serially, so a single-run KPI is a sample — use this before quoting one.",
    )
    args = ap.parse_args(argv)

    sim_opts = {
        "farm_usd": args.farm_usd,
        "headless": not args.gui,
        "livestream": args.livestream,
        "live": args.live,
        "resolution": (args.width, args.height),
        "save_usd": args.save_usd,
        "record": args.record,
        "video": args.video,
        "video_fps": args.video_fps,
        "max_panels": args.max_panels,
        "repeat": args.repeat,
    }
    if args.live and not (args.gui or args.livestream or args.video):
        print(
            "  [note] --live without --gui/--livestream/--video: the fleet will fly "
            "the route at ~10x the sim steps with nobody watching",
            flush=True,
        )

    farm_cfg = mission_cfg = scenario_name = None
    gates = None
    if args.scenario:
        from solar_twin.scenario import load_scenario

        scn = load_scenario(args.scenario)
        farm_cfg, mission_cfg, scenario_name = scn.farm_cfg, scn.mission_cfg, scn.name
        gates = scn.kpi_gates
        print(f"scenario: {scn.name}  kpi_gates={scn.kpi_gates or '{}'}", flush=True)
    elif not (args.farm and args.mission):
        ap.error("provide farm and mission paths, or --scenario")

    # CLI route/stride override mission.yaml so a demo does not need its own file.
    route_overrides = {}
    if args.route:
        route_overrides["route"] = args.route
    if args.panel_stride:
        route_overrides["panel_stride"] = args.panel_stride
    if route_overrides:
        if mission_cfg is None:
            mission_cfg = _load_yaml(args.mission)
        mission_cfg = {**mission_cfg, **route_overrides}
        print(f"  route: {mission_cfg.get('route', 'linear')} "
              f"stride={mission_cfg.get('panel_stride', 1)}", flush=True)

    if args.subset:
        # Must mirror farm_builder's --subset: the mission may only target panels
        # the authored stage actually contains, or SimNativeTransport looks up prim
        # paths that were never created.
        if farm_cfg is None:
            farm_cfg = _load_yaml(args.farm)
        layout_cfg = dict(farm_cfg.get("layout") or {})
        if layout_cfg.get("kind") != "file":
            ap.error("--subset only applies to layout.kind: file")
        layout_cfg["max_tables"] = args.subset
        farm_cfg = {**farm_cfg, "layout": layout_cfg}
        print(f"subset: first {args.subset} tracker tables only", flush=True)

    # run() writes + prints the record (and the gate verdict) before closing the
    # sim, because SimulationApp.close() may terminate the process — so the
    # printed verdict and gates.json are authoritative, and this exit code is a
    # convenience for the runs that do return.
    out = run(
        args.farm,
        args.mission,
        args.backend,
        args.runs_dir,
        sim_opts,
        farm_cfg=farm_cfg,
        mission_cfg=mission_cfg,
        scenario_name=scenario_name,
        kpi_gates=gates,
    )
    gates_file = Path(out) / "gates.json" if out else None
    if gates_file and gates_file.exists():
        if not json.loads(gates_file.read_text())["passed"]:
            return 1  # a breached KPI gate fails the run (FR-17)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
