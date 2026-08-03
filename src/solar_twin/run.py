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
from solar_twin.control.wind_drift import WindDisturbedControl
from solar_twin.kpi import confound as kpi_confound
from solar_twin.kpi import gates as kpi_gates_mod
from solar_twin.kpi import variance as kpi_variance
from solar_twin.orchestrator import grid_dispatch, sortie as sortie_mod
from solar_twin.orchestrator.mission import Fleet, Mission
from solar_twin.orchestrator.scout_dispatch import BEAT_LABELS, ScoutDispatchMission
from solar_twin.world.keepout import build_keepouts
from solar_twin.world.layout import FarmLayout


#: Human labels for the FSM phases, for the demo video's caption. The enum names
#: are fine in a log and terse on screen.
_PHASE_LABELS = {
    "ADVANCE": "ground bot advancing",
    "SCREEN": "screening pass",
    "CONFIRM": "close confirm pass",
    "WRITEBACK": "writing verdict to USD",
    # The scout->dispatch beats share this table so the overlay and the live
    # narration caption either mission without knowing which one is running.
    **BEAT_LABELS,
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
            tonemap=bool(sim_opts.get("tonemap")),
        )
        # Opt-in PhysX. OFF by default: every KPI on record was measured with physics
        # inert, and it costs ~1x realtime at 8k prims but 0.07x at 82k.
        if sim_opts.get("physics"):
            runtime.start_physics()
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
        cruise: dict[str, float] = {}
        if sim_opts.get("video") or sim_opts.get("live"):
            speeds = {
                fleet["ground_bot"]: float(kin.get("bot_speed", 1.0)),
                fleet["screen_drone"]: float(kin.get("drone_speed", 2.0)),
                fleet["confirm_drone"]: float(kin.get("drone_speed", 2.0)),
            }
            # `cruise_speeds` existed in KinematicControl and was exercised only by
            # tests — this call never passed it, so every commute ran at inspection
            # speed. That is not a cosmetic loss: at 1.0 m/s and dt=0.1 the
            # interpolator's `_MAX_TICKS` budget of 4000 covers just 400 m, while the
            # first table of the full block sits ~490 m from the origin, so the ground
            # bot burned the whole budget, warned, and snapped to the waypoint. On the
            # whole plot (4.84 x 1.97 km) that failure is the normal case, not an edge.
            cruise = {
                fleet["ground_bot"]: float(kin.get("bot_cruise", 6.0)),
                fleet["screen_drone"]: float(kin.get("drone_cruise", 18.0)),
                fleet["confirm_drone"]: float(kin.get("drone_cruise", 18.0)),
            }
        control = KinematicControl(
            runtime,
            speeds=speeds,
            cruise_speeds=cruise,
            cruise_above_m=float(kin.get("cruise_above_m", 6.0)),
            dt=float(kin.get("dt", 0.1)),
            max_ticks=int(kin.get("max_ticks", 4000)),
        )
        return SimNativeTransport(runtime, panel_paths), control
    raise ValueError(f"unknown backend: {name!r}")


def _wrapper(control, cls):
    """First wrapper of type `cls` in a decorator chain, or None.

    `SafeControl` and `WindDisturbedControl` both wrap a `RobotControl` and both
    expose `.inner`, so the outermost object is no longer a reliable `isinstance`
    target. Walking the chain is what stops adding a wrapper from silently dropping
    another's contribution to the run record — which is exactly what an
    `isinstance(control, SafeControl)` check does the moment anything wraps it.
    """
    seen = 0
    while control is not None and seen < 8:  # 8 = a chain depth nothing should reach
        if isinstance(control, cls):
            return control
        control = getattr(control, "inner", None)
        seen += 1
    return None


def _wrap_wind(control, farm_cfg: dict, mission_cfg: dict, layout: FarmLayout):
    """Opt-in: let the wind push the drones off their commanded pose.

    OFF by default and off is the identity — a run without
    `kinematics.wind_disturbance` gets the same object back, so every KPI recorded
    before this existed stays reproducible.

    ⚠ This disturbs the **camera pose**, not the airframe. It is not flight dynamics
    and `KPI-05` does not come from it (see `control/wind_drift.py`, and
    `tools/px4_hover.py` for the real-dynamics measurement). It is wrapped OUTSIDE
    `SafeControl` on purpose so the keep-out layer vets the pose the drone will
    actually hold rather than the one the mission wished for.
    """
    kin = mission_cfg.get("kinematics", {}) or {}
    if not kin.get("wind_disturbance"):
        return control

    # `WindDisturbedControl` is already imported at module scope (the run record
    # needs it to find the wrapper); these two are only needed on the opt-in path.
    from solar_twin.control.wind_drift import DEFAULT_HOLD_STIFFNESS_N_PER_M, HoldModel
    from solar_twin.world.fleet_specs import DRONES
    from solar_twin.world.siting import resolve_turbines
    from solar_twin.world.windfield import from_cfg as wind_from_cfg

    # Wake sources must be the turbines the stage was BUILT with, not the config's
    # explicit list — a scattered field wakes (and shelters) the drone somewhere else.
    field = wind_from_cfg(farm_cfg, resolve_turbines(farm_cfg, layout))
    if field.mean_speed_ms <= 0.0:
        print(
            "  [warn] kinematics.wind_disturbance is ON but this farm config declares "
            "no wind (mean_speed 0) — the fleet will fly undisturbed and any KPI will "
            "read as a calm-air result. This is the NFR-07 failure mode: asking for a "
            "hazard and then measuring without it.",
            flush=True,
        )
        return control

    spec_name = str(kin.get("wind_drone_spec", "m350"))
    if spec_name not in DRONES:
        raise ValueError(
            f"kinematics.wind_drone_spec={spec_name!r} is not one of {sorted(DRONES)}"
        )
    spec = DRONES[spec_name]
    stiffness = float(
        kin.get("wind_hold_stiffness_n_per_m", DEFAULT_HOLD_STIFFNESS_N_PER_M)
    )
    model = HoldModel.for_drone(spec, hold_stiffness_n_per_m=stiffness)

    # Drones only. The ground bot is deliberately left undisturbed: `HoldModel`'s
    # stiffness is a rotor position-hold figure, and applying it to a 50 kg wheeled
    # rover held on the terrain by friction would invent a number, not model one.
    fleet = mission_cfg["fleet"]
    models = {fleet["screen_drone"]: model, fleet["confirm_drone"]: model}
    print(
        f"  wind disturbance ON: {field.mean_speed_ms:.1f} m/s from "
        f"{field.wind_dir_deg:.0f} deg, gust {100 * field.speed_variation:.0f}%, "
        f"{spec.name} @ {stiffness:.0f} N/m (camera pose only — NOT flight dynamics)",
        flush=True,
    )
    return WindDisturbedControl(control, field, models)


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
            # 1.0 = show the whole frame, which is what every recorded KPI used.
            # Lowering it excludes the neighbouring module from the confirm frame
            # (`kpi/confound.py`) — ⚠ not yet validated against a KPI, so the
            # default must stay 1.0 until a --repeat comparison says otherwise.
            crop_fraction=float(opts.get("crop_fraction", 1.0)),
        )
    raise NotImplementedError(
        f"perception {name!r} not wired yet (Slice 0 uses ground_truth)."
    )


def _plan_sorties(mission_cfg: dict, targets: list, mode: str = "report"):
    """Ask whether the fleet could actually FLY this mission, and say so.

    Until this existed nothing in the pipeline knew a robot has a battery: a
    10-panel sweep and a 30,016-panel sweep were equally acceptable plans. This
    packs the (already ordered) targets into out-and-back sorties the vehicle can
    complete, so the run record carries how many trips the work really costs and
    what does not fit at all.

    **`report` is the default and is the identity.** It returns `targets` unchanged
    — the mission visits exactly the panels it would have visited before this layer
    existed, so no recorded KPI moves. Same discipline as `grid_dispatch`, and for
    the same reason.

    `enforce` truncates to what the first sortie can actually achieve. That DOES
    change the denominator of every rate in the record, so it is opt-in and loud.

    ⚠ The endurance is a datasheet figure times an assumed derate
    (`fleet_specs.DEFAULT_ENDURANCE_DERATE`), and **nothing here has been flown**.
    This tests a plan against a model of a battery, not against a battery.
    """
    if mode == "off":
        return targets, None
    cfg = (mission_cfg or {}).get("sorties", {}) or {}
    if not (cfg.get("enabled") or cfg.get("always_report")):
        return targets, None

    from solar_twin.world import fleet_specs  # noqa: PLC0415 — pure, but keep it local

    platform = str(cfg.get("platform", "m350"))
    spec = fleet_specs.DRONES.get(platform) or fleet_specs.ROVERS.get(platform)
    if spec is None:
        known = sorted([*fleet_specs.DRONES, *fleet_specs.ROVERS])
        raise ValueError(f"sorties.platform={platform!r} unknown; known: {known}")

    vehicle = sortie_mod.VehicleEndurance.from_spec(
        spec,
        dwell_s=float(cfg.get("dwell_s", 20.0)),
        derate=float(cfg["derate"]) if "derate" in cfg else None,
        reserve_fraction=float(
            cfg.get("reserve_fraction", sortie_mod.DEFAULT_RESERVE_FRACTION)
        ),
    )
    base = tuple(float(v) for v in cfg.get("base", (0.0, 0.0, 0.0)))

    # WHICH waypoint depends on the vehicle, and the distinction is real: a drone
    # flies to the `screen` standoff above the module, a ground bot drives to the
    # `approach` pose beside it. Using one for both would cost the rover its drive
    # distance and give the drone the wrong altitude. `is_ground` is read from the
    # spec's own registry rather than from the platform string, so adding a rover
    # to fleet_specs.ROVERS is enough.
    is_ground = platform in fleet_specs.ROVERS

    def _xyz(t):
        wp = t.approach if is_ground else t.screen
        return (float(wp.x), float(wp.y), float(wp.z))

    pts = [(t.panel_id, _xyz(t)) for t in targets]
    plan = sortie_mod.plan_sorties(pts, vehicle, base, max_sorties=int(cfg.get("max_sorties", 0)))

    print(
        f"  [note] sorties ({platform}): {len(plan.sorties)} trip(s) for "
        f"{plan.n_planned} panels — {plan.total_duration_s/60:.1f} min, "
        f"{plan.total_travel_m:.0f} m, budget {vehicle.budget_s/60:.1f} min/trip"
        + (f", {len(plan.unreachable)} unreachable" if plan.unreachable else "")
        + (f", {len(plan.deferred)} deferred" if plan.deferred else "")
        + ". ⚠ MODELLED endurance (datasheet x derate); nothing has been flown.",
        flush=True,
    )

    if mode == "enforce" and plan.sorties:
        keep = set(plan.sorties[0].target_ids)
        if len(keep) < len(targets):
            print(
                f"  [note] sorties enforce: cutting {len(targets)} -> {len(keep)} panels, "
                "what ONE sortie can achieve. ⚠ This changes every denominator in the "
                "run record.",
                flush=True,
            )
        targets = [t for t in targets if t.panel_id in keep]

    return targets, plan


def _energy_report(farm_cfg: dict, layout: FarmLayout, results: list, faults: dict):
    """What the faults cost in kWh — and how much of that the mission actually found.

    The pillar's payoff. A verdict of "R12-C047 is soiled" is only actionable next to
    a number, and the twin now has the geometry, the sun and the shading to compute
    one. `energy/model.py` is the solver (pvlib as our ETAP, the DSX pattern); this
    just runs it twice over the same instant.

    **Twice, because the interesting quantity is the gap.** Once over each panel's
    `injected_state` (what the plant is really losing) and once over its
    `detected_state` (what the mission concluded it is losing). The difference is the
    inspection's blind spot expressed in kWh instead of in recall — which matters
    here specifically, because hotspot recall is 0.40 against soiling's 0.98 and
    those two states carry different derates. A recall number hides that; a kWh
    number cannot.

    Both are computed over **the inspected panels only**, so they share a
    denominator. The whole-stage figure is reported separately and is NOT comparable
    to them — a mission that visits 12 of 30,016 panels has not measured the plant.

    Returns None (silently) when pvlib is absent: an optional analysis must never
    fail a mission that completed. ⚠ Every number carries `energy.model`'s four
    assumptions — clear sky, generic electricals, assumed derates, and no SCADA.
    """
    try:
        from solar_twin.energy import model as energy_model  # noqa: PLC0415
        from solar_twin.world.solar import parse_timestamp, solar_position  # noqa: PLC0415
    except ImportError:
        return None

    sun_cfg = (farm_cfg.get("sun", {}) or {})
    if "timestamp" not in sun_cfg:
        # Without a real instant there is no sun, and inventing one would put a
        # confident kWh next to a scenario that never specified a time of day.
        return None

    try:
        plant = energy_model.plant_from_layout_cfg(farm_cfg, layout)
        when = parse_timestamp(sun_cfg["timestamp"])
        elev, azim = solar_position(plant.latitude, plant.longitude, when)

        from solar_twin.schema.pv_module import coerce_state  # noqa: PLC0415

        injected = {r.panel_id: coerce_state(r.injected_state) for r in results}
        detected = {r.panel_id: coerce_state(r.detected_state) for r in results}

        kw = dict(sun_elevation_deg=elev, sun_azimuth_deg=azim)
        actual = energy_model.fault_cost(plant, when, states=injected, **kw)
        believed = energy_model.fault_cost(plant, when, states=detected, **kw)
        stage = energy_model.fault_cost(plant, when, states=faults, **kw)
    except Exception as exc:  # noqa: BLE001 — analysis must not fail a good mission
        print(f"  [warn] energy report skipped: {exc}", flush=True)
        return None

    found = (believed.lost_w / actual.lost_w) if actual.lost_w > 0 else None
    print(
        f"  [note] energy @ {sun_cfg['timestamp']}: plant {actual.healthy_ac_w/1e6:.2f} MW "
        f"healthy; inspected panels losing {actual.lost_w/1000:.1f} kW, mission found "
        + (f"{found:.0%} of it" if found is not None else "n/a (no injected loss)")
        + ". ⚠ MODELLED clear-sky, no SCADA — quote the ratio, not the magnitude.",
        flush=True,
    )
    return {
        "timestamp": sun_cfg["timestamp"],
        "sun_elevation_deg": round(elev, 3),
        "plant_healthy_ac_w": round(actual.healthy_ac_w, 1),
        # Over INSPECTED panels only — these two share a denominator.
        "inspected": {
            "n_panels": len(results),
            "lost_w_actual": round(actual.lost_w, 2),
            "lost_w_detected": round(believed.lost_w, 2),
            # ⭐ The mission's blind spot, in watts rather than in recall.
            "loss_found_fraction": round(found, 4) if found is not None else None,
            "n_faulted_actual": actual.n_faulted,
            "n_faulted_detected": believed.n_faulted,
        },
        # ⚠ Whole stage, NOT comparable to the block above: a mission that visits
        # 12 of 30,016 panels has not measured the plant.
        "whole_stage": {
            "n_panels": plant.n_modules,
            "lost_w": round(stage.lost_w, 2),
            "lost_fraction": round(stage.lost_fraction, 5),
        },
        "caveat": energy_model.UNVALIDATED_CAVEAT,
    }


def _dispatch(layout: FarmLayout, farm_cfg: dict, mission_cfg: dict, targets: list):
    """Apply the suspicion-first prioritisation layer. Returns (targets, result).

    ⚠⚠ **The prior is SIMULATED and circular by construction.** There is no SCADA
    feed on this project: `kpi/simulated_scada.rank_cells_simulated` derives a
    cell's "measured" output from the twin's own `pv:state`/`pv:iv_yield` — the
    very ground truth the mission is sent out to discover. A run with this on
    therefore says nothing about a real plant, and `dispatch.scada_source` /
    `dispatch.caveat` in the run record say so in words.

    **Off by default, and off is the identity.** `order_targets` returns `targets`
    itself when disabled (see its docstring and
    `test_disabled_reproduces_layout_order_exactly`), so a run with
    `grid_dispatch.enabled` unset is byte-identical to one from before this layer
    existed — every recorded KPI stays reproducible. `panel_records()` is not even
    built in that case: on the full plot that is 30k records, and a disabled run
    must cost what it costs today.

    ⚠ Cell membership comes from the LAYOUT/STAGE (`farm.yaml`'s `grid:` block,
    via `layout.cell_id_for`), never from the mission. `grid_dispatch.
    modules_per_cell` is a stub reserved for a real string map and is *not* read
    by `order_targets`, so a mission that sets it is warned rather than silently
    ranked against a different grouping than the stage was authored with.

    ⚠ **Why the input is `layout.panel_records()` and not `transport.read_panel`,
    given that the USD stage is the source of truth (golden rule 3).** That rule
    governs panel state *during* sim — the FSM still reads and writes every verdict
    through the Transport, untouched. This runs strictly BEFORE the mission starts,
    and it needs the same seeded layout the builder authored the stage from:
    `layout.cell_id_for` is the single derivation both use, so the `cell_id` here
    and the `grid:id` on the prim are the same string by construction. Reading it
    back off the stage instead would be N USD attribute reads (30k on the full
    plot) to recover what the config already determines. If the ranker ever needs
    *live* panel state — a mid-mission re-rank from accumulated verdicts — that
    must come through the Transport, and this is the line to revisit.
    """
    cfg = grid_dispatch.DispatchConfig.from_mission_cfg(mission_cfg)
    if not cfg.enabled:
        return grid_dispatch.order_targets(targets, {}, cfg)

    farm_grouping = int((farm_cfg.get("grid", {}) or {}).get("modules_per_cell", 0))
    mission_grouping = int(
        ((mission_cfg or {}).get("grid_dispatch", {}) or {}).get("modules_per_cell", 0)
    )
    if mission_grouping and mission_grouping != farm_grouping:
        print(
            f"  [warn] mission grid_dispatch.modules_per_cell={mission_grouping} is "
            f"IGNORED: cells come from the stage (farm.yaml grid.modules_per_cell="
            f"{farm_grouping}), so the ranker and the authored grid:id agree. Change "
            "it in farm.yaml and rebuild the stage.",
            flush=True,
        )

    records_by_panel = {r.panel_id: r for r in layout.panel_records()}
    ordered, result = grid_dispatch.order_targets(targets, records_by_panel, cfg)
    print(
        "  [note] grid_dispatch enabled: "
        f"{result.reason}. ⚠ The prior is SIMULATED — derived from the twin's own "
        "pv:state/pv:iv_yield, i.e. from the ground truth being sought. Circular by "
        "construction; do NOT read KPI-09 from this as a claim about a plant.",
        flush=True,
    )
    if result.plan is not None:
        print(
            f"  [note] dispatch plan: solver={result.plan.solver} "
            f"cells={len(result.plan.cell_order)} dropped={len(result.plan.dropped)} "
            f"travel={result.plan.travel_m:.1f} m "
            f"suspicion/m={result.plan.suspicion_per_m:.6f} (KPI-09, SIMULATED). "
            f"escalation_arm={result.plan.escalation_arm} is RECORDED, not enacted — "
            "the FSM's ADVANCE->SCREEN->CONFIRM is ground-first by construction.",
            flush=True,
        )
    return ordered, result


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
    # Wind on the camera, OUTSIDE the keep-out clamp so a gust cannot push an
    # already-vetted waypoint back into the rotor volume unchecked. No-op unless
    # `kinematics.wind_disturbance` is set.
    control = _wrap_wind(control, farm_cfg, mission_cfg, layout)
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

    # Prioritisation, strictly UPSTREAM of the FSM: it only decides which panels in
    # what order. Placed BEFORE --max-panels on purpose — `docs/specs/06` requires
    # KPI-09's ranker-ON and ranker-OFF arms to be compared at the same seed and
    # the same *panel budget*, so the budget must bite after the ranking, not
    # before it. Disabled (the default) this is the identity, so the truncation
    # below sees exactly the list it sees today.
    targets, dispatch = _dispatch(layout, farm_cfg, mission_cfg, targets)

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

    # Feasibility, AFTER --max-panels: the question is whether the fleet can fly the
    # sweep that will actually happen, not the one that was asked for. Default mode
    # is report-only and returns `targets` unchanged, so this cannot move a KPI.
    targets, sortie_plan = _plan_sorties(
        mission_cfg, targets, str(sim_opts.get("sorties") or "report")
    )

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

    recorder = None
    runtime = getattr(transport, "runtime", None)

    # Keep the viewport painting through a slow perception call. Only when a human is
    # watching AND the backend is slow enough to matter: `cosmos_reason` blocks ~12 s
    # per panel, and without a pump the window shows a stale/black surface and the WM
    # reports Isaac Sim "not responding" for that whole time. A measurement run is
    # untouched — headless has no interactive runtime, and the stub returns in
    # microseconds. Placed HERE, not next to `_perception()`: `runtime` is resolved
    # off the transport above and does not exist earlier in this function.
    if (
        runtime is not None
        and getattr(runtime, "interactive", False)
        and hasattr(runtime, "pump")
        and mission_cfg.get("perception") == "cosmos_reason"
    ):
        from solar_twin.perception.pumped import PumpedPerception

        perception = PumpedPerception(perception, pump=runtime.pump)
        print(
            "  [note] perception wrapped in PumpedPerception: the viewport keeps "
            "repainting while each panel is judged.",
            flush=True,
        )

    # --- optional demo video (chase view + drone camera, captioned) --------
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

    def _surveyed(i: int, panel_id: str, suspect: bool) -> None:
        """Progress during a scout_dispatch survey.

        `_progress` cannot serve here: that mission files every result after the
        whole survey and all responses are done, so without this the terminal is
        silent for the entire sweep and then prints everything at once.
        """
        mark = "FLAGGED" if suspect else "clean"
        print(f"  [scout {i + 1}/{len(targets)}] {panel_id}: {mark}", flush=True)
        if live_view:
            runtime.step(2)

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

    # Two mission shapes, chosen by config. `sweep` is the measurement FSM and the
    # default — every KPI on record was produced by it, so it must stay reachable
    # unchanged. `scout_dispatch` is the demonstration: survey, then send the fleet
    # to what the survey found (see orchestrator/scout_dispatch.py).
    mode = str(mission_cfg.get("mission_mode", "sweep"))
    if mode == "scout_dispatch":
        mission = ScoutDispatchMission(transport, control, perception, fleet)
        print(
            "  [note] mission_mode: scout_dispatch — survey sweep, then dispatch to "
            "flagged panels. A demo shape: do NOT quote KPIs from this run.",
            flush=True,
        )
    elif mode == "sweep":
        mission = Mission(transport, control, perception, fleet)
    else:
        raise ValueError(
            f"unknown mission_mode: {mode!r} (expected 'sweep' or 'scout_dispatch')"
        )
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
            # Per-repeat tallies, not cumulative. Called on the OUTERMOST wrapper:
            # each `reset` delegates inward, so one call clears the keep-out tally
            # and rewinds the gust clock (repeat 2 must see repeat 1's weather, or
            # `variance.json` would read the wind as a decoding spread — RISK-23).
            _reset = getattr(control, "reset", None)
            if callable(_reset):
                _reset()
        t0 = time.perf_counter()
        run_kw = (
            {"on_scouted": _surveyed} if isinstance(mission, ScoutDispatchMission) else {}
        )
        result = mission.run(targets, on_result=_progress, on_phase=_on_phase, **run_kw)
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
            # What decided the visit ORDER. Present on every record, including
            # disabled runs (`scada_source: "none"`), so no record is ambiguous
            # about whether a simulated prior influenced what got inspected first.
            "dispatch": dispatch.to_dict(),
            # Whether the fleet could physically FLY this sweep, and in how many
            # trips. Null when the layer is off. ⚠ Endurance is a datasheet figure
            # times an assumed derate — a modelled battery, never a measured one.
            "sorties": sortie_plan.to_dict() if sortie_plan else None,
            # What the faults cost in kWh, and how much of that the mission found.
            # Null when pvlib is absent or the scenario names no sun instant.
            "energy": _energy_report(farm_cfg, layout, result.results, faults),
            "injected_faults": {pid: s.value for pid, s in faults.items()},
            "metrics": {
                "panels_inspected": result.panels_inspected,
                "faults_detected": result.faults_detected,
                "detection_rate": result.detection_rate,
                # ⚠ `detection_rate` is ACCURACY over every panel, so on a mostly
                # healthy scenario it flatters a model that finds nothing. These
                # four make that impossible to miss in a run record: the null
                # baseline it must beat, recall on faulted panels only (named and
                # flagged), and the per-state split the pooled number hides.
                "healthy_fraction": result.healthy_fraction,
                "fault_recall": result.fault_recall,
                "fault_flagged_rate": result.fault_flagged_rate,
                "recall_by_state": result.recall_by_state(),
                "false_fault_rate": result.false_fault_rate,  # KPI-03
                # KPI-03's two halves, reported so "called a fault that isn't
                # there" and "we lost the answer" stop being one number with
                # opposite fixes. false_fault_rate is unchanged (locked, §6.5).
                "false_alarm_rate": result.false_alarm_rate,
                "abstention_rate": result.abstention_rate,
                "abstentions": result.abstentions,
                "sim_steps": result.steps,
                "wall_seconds": round(wall_s, 4),
            },
            "panels": [asdict(r) for r in result.results],
            "fault_events": [e.to_dict() for e in result.fault_events],
        }
        # Is a "false fault" actually the panel next door? Measured on SC-01: every
        # false alarm across three prompt versions and nine repeats sat beside a
        # faulted panel, and none of the clean-neighbourhood panels ever produced
        # one. The confirm frame shows more than one module, so KPI-03 can be
        # scoring a real defect against the wrong panel — recorded per run rather
        # than left for someone to notice (`kpi/confound.py`).
        record["confound"] = kpi_confound.analyse(record).to_dict()
        safe = _wrapper(control, SafeControl)
        if safe is not None:
            record["keepout"] = {
                "turbines": len(keepouts),
                "waypoints_clamped": len(safe.events),
                "min_clearance_m": (
                    None if safe.min_clearance_m == float("inf")
                    else round(safe.min_clearance_m, 3)
                ),
                "events": [e.to_dict() for e in safe.events],
            }
        # How much wind actually reached the camera. Present only when the
        # disturbance is on, and it names its own limits in `model` so a reader
        # cannot mistake a drift figure for a flight-dynamics result.
        wind_ctl = _wrapper(control, WindDisturbedControl)
        if wind_ctl is not None:
            record["wind_disturbance"] = wind_ctl.summary()
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
            # Printed next to KPI-03 on purpose: a false_fault_rate that is
            # really abstentions in disguise should be obvious at the console,
            # not something you find by reading results.json afterwards.
            f"(false_alarm={m['false_alarm_rate']:.3f} "
            f"abstained={m['abstentions']}) "
            f"injected={len(record['injected_faults'])}",
            flush=True,
        )
        # Same reasoning as the line above: if the false-fault rate is the panel
        # next door, that has to be visible where the number is, not in a JSON file.
        if record["confound"]["false_alarms"]:
            print(f"  confound: {record['confound']['verdict']}", flush=True)

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

    # ---- optionally stay open so the plant can be inspected by hand ------ #
    # Deliberately after every artefact is written and before `close()`: the run
    # record must not depend on how long someone flies around, and `close()` may
    # terminate the process outright.
    if sim_opts.get("hold"):
        runtime = getattr(transport, "runtime", None)
        if runtime is not None and hasattr(runtime, "hold"):
            if sim_opts.get("headless") and not sim_opts.get("livestream"):
                print(
                    "  [warn] --hold does nothing headless: there is no window to hold "
                    "open and no stream to watch. Pair it with --gui (a window on this "
                    "machine's display) or --livestream (WebRTC).",
                    flush=True,
                )
            else:
                runtime.hold()
        else:
            print(
                "  [warn] --hold needs the sim_native backend (this one has no "
                "runtime to hold open).",
                flush=True,
            )

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
        "--physics",
        action="store_true",
        help="step PhysX during the mission so the authored colliders are live. "
        "⚠ OFF by default — every recorded KPI was measured with physics inert, and "
        "it costs ~1x realtime on a ~24-table subset but 0.07x on the full block.",
    )
    ap.add_argument(
        "--tonemap",
        action="store_true",
        help="apply the measured photographic exposure (f/9). Off for KPI runs so a "
        "pixel-scored metric cannot move because of a render setting.",
    )
    ap.add_argument(
        "--hold",
        action="store_true",
        help="sim_native + --gui/--livestream: when the mission ends, DON'T close — "
        "hand the camera back and keep the stage open so you can fly around the "
        "plant with the fleet parked and the verdicts already on the prims. "
        "Turbines keep turning, so the blade shadow keeps sweeping.",
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
        "--sorties",
        choices=["off", "report", "enforce"],
        default="report",
        help="whether the fleet could actually FLY this sweep on one battery. "
        "`report` (default) plans out-and-back sorties and stamps them into the run "
        "record WITHOUT changing which panels are visited, so no KPI moves. "
        "`enforce` cuts the sweep to what one sortie achieves — which changes every "
        "denominator in the record. `off` skips it. Needs `sorties.enabled` (or "
        "`always_report`) in the mission config to do anything. ⚠ Endurance is a "
        "datasheet figure times an assumed derate; nothing has been flown.",
    )
    ap.add_argument(
        "--route",
        choices=["linear", "serpentine", "fault_zone"],
        help="panel visit order. serpentine turns round at the end of each table "
        "instead of deadheading 128 m back to the next row's start. fault_zone "
        "surveys a compact window centred on a seeded fault (for "
        "mission_mode: scout_dispatch) and is NOT a measurement route — it picks "
        "the window using ground truth. Overrides mission.yaml's `route`.",
    )
    ap.add_argument(
        "--panel-stride",
        type=int,
        default=0,
        help="inspect every Nth panel — a coverage sweep rather than a census. "
        "⚠ changes what the run measures (denominator = panels VISITED).",
    )
    ap.add_argument(
        "--grid-dispatch",
        action="store_true",
        help="order panels suspicion-first instead of in layout order: rank "
        "`grid:id` cells by PR anomaly, then sweep the worst cells first. "
        "⚠⚠ THE RANKING IS SIMULATED — derived from the twin's own "
        "pv:state/pv:iv_yield, i.e. from the ground truth being sought — so it is "
        "circular by construction and proves nothing about a real plant. OFF by "
        "default; off is the identity, so every recorded KPI stays reproducible. "
        "Needs a stage built with `grid.enabled: true` in farm.yaml. "
        "Overrides mission.yaml's `grid_dispatch.enabled`.",
    )
    ap.add_argument(
        "--dispatch-max-cells",
        type=int,
        default=0,
        help="--grid-dispatch only: visit at most N cells (0 = all). Dropped cells "
        "are NAMED in the run record, never silently truncated. This is the panel "
        "budget KPI-09's ranker-ON/ranker-OFF arms must share.",
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
        "--repeats",
        dest="repeat",
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
        "hold": args.hold,
        "physics": args.physics,
        "tonemap": args.tonemap,
        "resolution": (args.width, args.height),
        "save_usd": args.save_usd,
        "record": args.record,
        "video": args.video,
        "video_fps": args.video_fps,
        "max_panels": args.max_panels,
        "repeat": args.repeat,
        "sorties": args.sorties,
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

    # Suspicion-first ordering, same CLI-overrides-config shape as `route`. Merged
    # ONTO the mission's own `grid_dispatch` block rather than replacing it, so a
    # mission that pins `min_anomaly`/`escalation_arm`/`solver` keeps them when the
    # flag turns the layer on. Nothing is written unless a flag was actually
    # passed — a run without them must not gain a `grid_dispatch` key it did not
    # have, since the mission config is copied verbatim into the run dir.
    if args.grid_dispatch or args.dispatch_max_cells:
        if mission_cfg is None:
            mission_cfg = _load_yaml(args.mission)
        dispatch_cfg = dict(mission_cfg.get("grid_dispatch") or {})
        if args.grid_dispatch:
            dispatch_cfg["enabled"] = True
        if args.dispatch_max_cells:
            dispatch_cfg["max_cells"] = args.dispatch_max_cells
        mission_cfg = {**mission_cfg, "grid_dispatch": dispatch_cfg}
        if args.dispatch_max_cells and not dispatch_cfg.get("enabled"):
            print(
                "  [warn] --dispatch-max-cells without --grid-dispatch (and no "
                "`grid_dispatch.enabled` in the mission): the ranker is off, so the "
                "cell budget does nothing.",
                flush=True,
            )

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
