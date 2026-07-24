# 04 — Interfaces & Data

## Locked contracts (recap only — do not redefine here)

These are canonical in `docs/PROJECT_BIBLE.md` §6 and implemented today. If
anything below conflicts with the actual source, the source + `PROJECT_BIBLE.md`
win; fix this file.

| Contract | Where | Shape (as implemented) |
|---|---|---|
| `Perception` | `src/solar_twin/perception/base.py` | `assess(frame, context) -> Verdict`, `diagnose(frame, context) -> Diagnosis`. `PanelContext = dict[str, Any]` |
| `Transport` | `src/solar_twin/transport/base.py` | `capture(robot_id) -> Frame`, `pose(robot_id) -> Pose`, `read_panel`/`write_panel`, `step(dt=0.0)` |
| `RobotControl` | `src/solar_twin/control/base.py` | `move_to(robot_id, waypoint)`, `at_goal(robot_id, waypoint, tol=0.05) -> bool` |
| `PVModule` schema | `src/solar_twin/schema/pv_module.py` | `pv:panel_id`, `pv:grid_index`, `pv:state`, `pv:iv_yield`, `pv:rul_days`, `pv:last_inspected`, `pv:inspection_log`, `pv:geo_position` |
| `FaultReport` | `src/solar_twin/schema/pv_module.py` | `panel_id, fault_type, confidence, note, timestamp, panel_geo_position` — the `/mission/fault` payload shape |
| `Fleet` / `InspectionTarget` | `src/solar_twin/orchestrator/mission.py` | Fixed 3-robot fleet (`ground_bot`, `screen_drone`, `confirm_drone`); one `InspectionTarget` per panel with 3 waypoints |
| Fault taxonomy | `PanelState` enum | `healthy, soiled, hotspot, crack, string_dropout, diode_fault, shading, unknown`; Slice 0 subset `{healthy, hotspot, soiled}` |

**Key finding from re-reading the code against the roadmap:** the interfaces
are already designed for most of the physics graduation. `RobotControl.move_to`'s
own docstring anticipates it — *"Kinematic impls complete the move
(teleport/interp); dynamic impls issue the goal and drive toward it."* Combined
with `Transport.step(dt)` as the world-tick primitive, a Pegasus/PX4-backed
`RobotControl` (`FR-06`) needs **no signature change** — `move_to` issues the
PX4 goal, `Transport.step` advances physics, `at_goal` polls tolerance exactly
as today. Likewise `PanelContext` is an untyped dict, so enriching it with
environmental fields (`IF-03` below) is additive by construction. This
materially de-risks `SLICE-2`/`SLICE-3`: the interface layer was built right
the first time.

## Proposed extensions (additive only — `NFR-04`)

### IF-01 — Energy/battery query (optional capability, not an ABC change)

**Need:** `FR-22` (cuOpt battery-capacity routing) and a battery/time-window
KPI (`KPI-06`) need to know a robot's remaining energy.

**Proposal:** do **not** add an abstract method to `RobotControl` (that would
break every existing/future concrete impl, including `kinematic.py` and test
fakes — violates `NFR-04`). Instead, define an optional `Protocol`:

```python
# control/base.py — additive, no change to RobotControl itself
from typing import Protocol, runtime_checkable

@runtime_checkable
class EnergyAware(Protocol):
    def battery_fraction(self, robot_id: str) -> float:
        """Remaining energy, 0.0-1.0. Only meaningful for dynamic impls."""
```

Callers (cuOpt wrapper, KPI collector) do `isinstance(control, EnergyAware)`
and fall back to "unlimited" for kinematic/Slice-0 runs. `kinematic.py` is
unaffected; a Pegasus impl opts in by implementing the method.

**Status:** Proposed. **Slice:** `SLICE-7`.

### IF-02 — Environment/hazard context enrichment (no interface change)

**Need:** `FR-03` (false-fault measurement) and Cosmos Reason grounding need
the perception layer to know *why* a frame looks the way it does — a blade
shadow sweeping, high wind, dust — without conflating that with ground-truth
panel state.

**Proposal:** extend the `PanelContext` dict built by
`orchestrator/mission.py::_context()` with a nested, optional `environment`
key. This is a **pure addition to a dict literal**, not an interface change:

```python
{
    "true_state": ...,
    "panel_id": ...,
    "grid_index": ...,
    "history": ...,
    "environment": {          # NEW, optional — absent in Slice 0/1
        "blade_shadow_active": bool,
        "wind_speed_mps": float,
        "dust_level": float,        # 0.0-1.0, scenario-config-driven
        "sun_elevation_deg": float,
    },
}
```

A ground-truth or Cosmos Reason impl that doesn't care simply ignores unknown
keys (`dict.get`); `SLICE-3`'s false-fault harness is the first consumer.

**Status:** Proposed. **Slice:** `SLICE-3`.

### IF-03 — Scenario/hazard config schema (new `configs/` surface, additive)

**Need:** `FR-16` (graded scenario suite) needs a config surface parallel to
existing `farm.yaml`/`mission.yaml`, not folded into either (farm = static
layout, mission = fleet/perception/transport wiring; hazards are a third,
orthogonal axis that composes with both).

**Proposal:** `configs/scenarios/<name>.yaml`, seeded like its siblings:

```yaml
# configs/scenarios/gust_and_shadow.yaml
seed: 20260724
extends: [configs/farm.yaml, configs/mission.yaml]   # composition, not duplication

wind:
  average_speed: 6.0        # m/s
  speed_variation: 3.0      # gust magnitude
  direction_variation_deg: 20.0

turbine:
  count: 1
  hub_height_m: 80.0
  rotor_diameter_m: 100.0
  rpm: 12.0
  thrust_coefficient: 0.8   # feeds the parametric wake model (HAZ-02)
  position: [40.0, -30.0, 0.0]

birds:
  count: 3
  spawn_rate_hz: 0.1
  trajectory: scripted      # scripted | randomized

terrain:
  source: procedural        # procedural | dem
  max_grade_deg: 15.0

kpi_gates:                   # ties this scenario to 06-scenario-suite-and-kpis.md
  collision_free_flight_rate: 1.0
  false_fault_rate_max: 0.05
```

This keeps `farm.yaml` (layout/faults) and `mission.yaml` (fleet/perception/
transport) unchanged and composable — a scenario references both rather than
forking them.

**Status:** Proposed. **Slice:** `SLICE-3`.

### IF-04 — Multi-robot fleet generalization (design tension — flagged, not resolved)

**Need:** `FR-22`/`PIL-6` (fleet + cuOpt) implies more than one ground bot
and/or more than one drone pair; today's `Fleet` dataclass
(`orchestrator/mission.py`) hardcodes exactly three named roles
(`ground_bot`, `screen_drone`, `confirm_drone`).

**Tension:** generalizing `Fleet` to an arbitrary roster is *not* a safe
additive change to the existing dataclass — code that does `fleet.ground_bot`
would break under a list-based roster. Two additive paths, not yet decided:

1. Keep `Fleet` exactly as-is for the single-triad Slice 0–6 path; introduce a
   separate `FleetRoster` (list of `Fleet`-shaped units) and a new
   multi-unit mission runner in `SLICE-7`, leaving `Mission` untouched.
2. Widen `Fleet` to optionally hold lists, with the current three fields kept
   as properties over a length-1 list, preserving `fleet.ground_bot` call
   sites.

**Recommendation:** option 1 — it changes nothing about `Mission`/`Fleet`
until `SLICE-7` actually needs N robots, keeping `NFR-04` intact by
construction rather than by discipline.

**Status:** Research — decide at `SLICE-7` kickoff, not before. **Slice:**
`SLICE-7`.

### IF-05 — `orchestrator/routing.py` (new pure-python module, additive)

**Need:** `FR-22` — cuOpt produces an ordered visiting sequence; something
needs to turn that into the `list[InspectionTarget]` that `Mission.run`
already consumes.

**Proposal:** a new pure-python module, `orchestrator/routing.py`, with a
single function `plan_route(targets: list[InspectionTarget], constraints:
RouteConstraints) -> list[InspectionTarget]` that calls a cuOpt client and
reorders/filters the input list. It sits *upstream* of `Mission.run` and
never touches its signature. cuOpt's own client library is not
Isaac-bound, so this module stays inside the Isaac-free boundary (`NFR-01`) —
confirm this against the actual cuOpt Python package before committing
(`RISK-10`).

**Status:** Proposed. **Slice:** `SLICE-7`.

### IF-06 — Isaac-bound hazard authoring stays inside `world/` (non-change, stated for clarity)

**Finding:** wind/gust force fields, articulated turbines, terrain heightfields,
and bird rigid bodies are all **authoring-time / Isaac-bound** concerns —
they're built once per scenario inside `world/farm_builder.py` (or a new
sibling, e.g. `world/hazard_builder.py`) and then simply *exist* in the stage
for `Transport.step()` to advance. None of this requires a new pure-python
interface; it requires new Isaac-bound *builder* code, consistent with the
existing `farm_builder.py` pattern. This keeps `PIL-2` almost entirely out of
the interface layer, which is good — it means physics fidelity work in
`SLICE-2` cannot accidentally regress `NFR-01`.

**Status:** Proposed (as a design constraint on how `SLICE-2` is implemented,
not as a new interface). **Slice:** `SLICE-2`.

## USD schema: explicitly NOT extended

No new `pv:` attributes are anticipated by this spec set. Environmental/hazard
state (wind, blade phase, keep-out volumes) belongs on **non-panel prims**
(turbine articulations, force-field prims, bird rigid bodies), never on the
panel prim itself — keeping the fault taxonomy exactly as scoped in
`CLAUDE.md`: *"Adding a fault type = enum entry + visual signature ... it must
not change orchestration."* If a future fault type genuinely needs a new
`pv:` field, that's a `PROJECT_BIBLE.md` §6 change, not a `specs/` one — update
both docs in the same commit per `NFR-10`.
