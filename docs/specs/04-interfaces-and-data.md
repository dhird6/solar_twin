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

**Need:** `FR-16` (graded scenario suite) needs a config surface for the
*dynamic* hazard axis, seeded and composable, without forking `farm.yaml`.

**Site vs. scenario split (revised 2026-07-24 to match shipped code).** The
original draft folded *everything* hazard-related into a scenario file. That
overshoots: terrain and turbine **placement** are **site geography**, not test
knobs — the real farm sits on real ground next to real turbines at fixed
positions. So the boundary is:

- **`farm.yaml` owns the static site** — grid, panels, georef, faults, **and now
  `terrain:` + `turbines:` (structure: position, hub height, blade length).**
  *This shipped* (commit `7894eb3`): `farm_builder.py` authors the heightfield
  mesh + turbine proxies from these blocks; `world/keepout.py` derives the
  no-fly volumes from the same `turbines:` list. Actual shipped schema:

  ```yaml
  # configs/farm.yaml (excerpt, as implemented)
  terrain:  { kind: heightfield, amplitude: 0.6, wavelength: 14.0 }
  turbines:
    - { pos: [9.5, -14.0], hub_height: 18.0, blade_len: 8.0, rpm: 12.0 }
  ```

- **`configs/scenarios/<name>.yaml` owns the dynamic axis** — what *varies* per
  test: wind/gust, sun/shadow angle, dust, birds, per-run turbine spin/wake
  toggles, and `kpi_gates`. It `extends` farm + mission rather than forking them:

  ```yaml
  # configs/scenarios/sweeping_shadow.yaml  (illustrative — loader lands SLICE-3)
  seed: 20260724
  extends: [configs/farm.yaml, configs/mission.yaml]
  sun:   { elevation_deg: 12.0 }        # low sun -> long sweeping blade shadow
  wind:  { average_speed: 0.0, speed_variation: 0.0 }
  turbine_dynamics: { spin: true }      # rpm comes from farm.yaml turbines[]
  kpi_gates: { false_fault_rate_max: 0.05 }
  ```

**Field-naming note:** the shipped `turbines[]` uses `pos` / `blade_len`. When
the parametric wake model lands (`HAZ-02`, `SLICE-2`) it will additively need
`rotor_diameter_m` and `thrust_coefficient`; add them then (rotor_diameter ≈
2·blade_len) rather than renaming working fields now — per the README rule that
**the source wins and this doc follows it**.

**Status:** Site half (`terrain`/`turbines` in `farm.yaml`) **Locked/shipped**;
scenario-file half **Proposed** (no loader yet). **Slice:** `SLICE-3`.

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

### IF-07 — Keep-out enforcement (SHIPPED — recorded here for traceability)

**What shipped** (commit `7894eb3`, ahead of `SLICE-2`): turbine no-fly is
enforced at the **planning layer**, control-agnostic, satisfying `FR-09` today
without waiting on flight dynamics.

- `world/keepout.py` — pure, Isaac-free geometry: each turbine's forbidden
  volume = rotor-swept **sphere** (`blade_len + margin`) ∪ **tower cylinder**;
  `violation` / `clamp_out` / `segment_clear` helpers. Derived from the same
  `farm.yaml` `turbines:` list the builder uses (single source of truth).
- `control/safe.py::SafeControl` — wraps any `RobotControl`, vets every
  `move_to` waypoint, **clamps** violations to the nearest safe point, logs a
  `KeepoutEvent`, and tracks `min_clearance_m`. It is additive (`NFR-04`): the
  ABC is unchanged; `kinematic.py` is untouched; a future Pegasus impl is
  wrapped identically.
- `run.py` writes a `keepout` block into the run record
  (`{turbines, waypoints_clamped, min_clearance_m, events}`) — the direct feed
  for `KPI-04`.
- `farm_builder.py` additionally authors **PhysX colliders** on tower/nacelle/
  blades + a translucent no-fly **viz sphere**. Colliders are **inert under
  kinematic teleport**; they only physically bite once the drone has rigid-body
  dynamics (the Pegasus graduation, `FR-06`/`SLICE-2`).

**Why this is the planning layer, not physics:** with teleport control a
collider stops nothing — the drone ignores physics. The valuable, buildable-now
guarantee is a constraint on the *plan*, which protects the kinematic drone
today and the PX4 drone later, unchanged. `FR-15`'s "USD invisible-collider
zone / PX4 geofence" is the *deployment* enforcement point; `SafeControl` is the
*planner* enforcement point — same logical constraint, complementary.

**Status:** Locked (planning layer). Physics-collision + articulation remain
Proposed at `SLICE-2` (`HAZ-01`, `RISK-11`). **Slice:** delivered early;
tracked under `SLICE-2`.

## USD schema: explicitly NOT extended

No new `pv:` attributes are anticipated by this spec set. Environmental/hazard
state (wind, blade phase, keep-out volumes) belongs on **non-panel prims**
(turbine articulations, force-field prims, bird rigid bodies), never on the
panel prim itself — keeping the fault taxonomy exactly as scoped in
`CLAUDE.md`: *"Adding a fault type = enum entry + visual signature ... it must
not change orchestration."* If a future fault type genuinely needs a new
`pv:` field, that's a `PROJECT_BIBLE.md` §6 change, not a `specs/` one — update
both docs in the same commit per `NFR-10`.
