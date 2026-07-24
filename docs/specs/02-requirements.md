# 02 — Requirements

Functional requirements (`FR-nn`) state what the system must do. Non-functional
requirements (`NFR-nn`) state how well, under what constraints. Each entry
carries a status (see `README.md` legend), the pillar(s) it belongs to
(`PIL-n`, defined in `03-architecture.md`), and the slice it's targeted for
(`SLICE-n`, defined in `07-roadmap-and-milestones.md`). "Acceptance" is either
a pointer to an existing test/metric or a KPI to be defined in
`06-scenario-suite-and-kpis.md`.

## Functional requirements

### Perception & fault judgment

| ID | Requirement | Pillar | Slice | Status | Acceptance |
|---|---|---|---|---|---|
| FR-01 | The system SHALL screen every inspection target with a cheap wide pass (`Perception.assess`) before any expensive confirm pass, per the two-tier escalation already implemented in `orchestrator/mission.py`. | PIL-5 | SLICE-0 | Locked | `tests/` FSM tests |
| FR-02 | The system SHALL run Cosmos Reason (2B/8B, vLLM/FP8) as a drop-in `Perception` implementation against real RTX camera frames, with no change to `orchestrator/mission.py`. | PIL-5 | SLICE-1 | Proposed (in flight) | KPI-01 (detection rate) measured with `perception: cosmos_reason` |
| FR-03 | The system SHALL measure Cosmos Reason's **false-fault rate** when a non-fault visual disturbance (sweeping blade shadow, motion blur, dust) crosses a healthy panel, as a first-class metric, not an afterthought. | PIL-5 | SLICE-3 | Proposed | KPI-03 |
| FR-04 | Verdicts SHALL be written back onto the panel exclusively through `Transport.write_panel` → the `pv:` USD attributes + `FaultReport`, never a side store. | PIL-5 | SLICE-0 | Locked | `schema/pv_module.py` |
| FR-05 | Generated/augmented training frames (Cosmos Transfer/Predict, NuRec Harmonizer) SHALL pass through an evaluator/filter step before being used to harden or evaluate `Perception`, to prevent hallucinated panel detail or geometrically-wrong shadows from poisoning fault ground truth. | PIL-3 | SLICE-4 | Proposed | Data-integrity gate in the scenario-factory pipeline (`06`) |

### Flight dynamics & control

| ID | Requirement | Pillar | Slice | Status | Acceptance |
|---|---|---|---|---|---|
| FR-06 | The system SHALL provide a `RobotControl` implementation backed by Pegasus Simulator (PX4 SITL) so drone motion is governed by real attitude/position control loops rather than teleport/interpolation. | PIL-2 | SLICE-2 | Proposed | Drone completes a coverage pass + station-keep under `RobotControl` conformance tests (`04`) |
| FR-07 | The kinematic `RobotControl` implementation (`kinematic.py`) SHALL remain available as a fallback and for Isaac-free tests; graduating to Pegasus SHALL NOT change the `RobotControl` ABC. | PIL-2 | SLICE-2 | Locked (constraint) | Existing tests continue to pass unmodified against `kinematic.py` |
| FR-08 | The drone SHALL hold station over a panel within a defined tolerance against an applied wind/gust force field for the confirm pass duration. | PIL-2 | SLICE-2 | Proposed | KPI-05 (station-keep error) |
| FR-09 | The drone's planner SHALL treat each turbine's precomputed swept-disk keep-out volume as a hard no-fly constraint; the planner SHALL NOT rely on instantaneous collision detection alone. | PIL-2, PIL-6 | SLICE-2 | **Partial — planning layer Locked** (`world/keepout.py` + `control/safe.py::SafeControl` clamp every waypoint out of the rotor-sphere∪tower volume, control-agnostic; see `IF-07`). Physics-collision half still Proposed. | `tests/test_keepout.py`; run-record `keepout` block (`waypoints_clamped`, `min_clearance_m`) feeds KPI-04 |
| FR-10 | The ground bot SHALL traverse terrain up to a declared maximum grade without loss of traction/contact, using a heightfield/DEM collision mesh. | PIL-2 | SLICE-2/6 | Proposed | Ramp testbed (5/10/15/20°) pass/fail per `06` |

### Hazards & environment

| ID | Requirement | Pillar | Slice | Status | Acceptance |
|---|---|---|---|---|---|
| FR-11 | Each turbine SHALL be modeled as a USD articulation (driven revolute joint + colliders on tower/nacelle/blades) producing real collision geometry and a moving blade shadow. | PIL-2 | SLICE-2 | Proposed | HAZ-01 verification |
| FR-12 | Wind/gust SHALL be injected via `omni.physx.forcefields` (Wind + Drag + Noise), parameterized by average speed, speed variation, and direction variation, settable from a scenario config. | PIL-2 | SLICE-2 | Proposed | HAZ-03 verification |
| FR-13 | Turbine wake SHALL be modeled as a parametric velocity-deficit/actuator-disk force field (Jensen/Gaussian profile), localized to the downstream volume, and explicitly documented as not CFD-accurate. | PIL-2 | SLICE-2 | Proposed | HAZ-02 verification |
| FR-14 | Birds SHALL be simulated as rigid-body agents on scripted or randomized trajectories that can cross the drone's flight lane and cast a moving shape distinguishable (by design, for training purposes) from a genuine obstacle. | PIL-2 | SLICE-5 | Research | HAZ-05 verification |
| FR-15 | No-fly volumes (turbine keep-out, site boundary) SHALL be expressible as USD invisible-collider zones in the twin and, on real hardware, as a PX4 geofence — the same logical constraint, two enforcement points. | PIL-2, PIL-6 | SLICE-2/8 | Proposed | HAZ-01, HAZ-06 |

### Scenario, evaluation & data factory

| ID | Requirement | Pillar | Slice | Status | Acceptance |
|---|---|---|---|---|---|
| FR-16 | The system SHALL define a graded scenario suite (nominal + adversarial) in `configs/`, each scenario reproducible from a seed + config, per the existing `farm.yaml`/`mission.yaml` convention. | PIL-4 | SLICE-3 | Proposed | `06-scenario-suite-and-kpis.md` |
| FR-17 | Every roadmap slice SHALL be gated on measured KPIs from the scenario suite (coverage %, false-fault rate, collision-free-flight rate, battery/time-window adherence) — not on a GUI demo. | PIL-4 | all | Proposed | `07-roadmap-and-milestones.md` exit criteria |
| FR-18 | The system SHALL support an off-box scenario-multiplication pipeline (Cosmos Transfer/Predict + Data Factory Blueprint/OSMO pattern) that fans one seeded Spark render into dust/haze/low-sun/blade-shadow/bird-strike variants. | PIL-3 | SLICE-4 | Proposed | Corpus produced + Evaluator pass rate reported |

### Reconstruction & georeferencing

| ID | Requirement | Pillar | Slice | Status | Acceptance |
|---|---|---|---|---|---|
| FR-19 | The system SHALL support referencing a neurally-reconstructed (NuRec/3DGUT) appearance layer of the real site into the same USD stage as the procedural farm, without changing panel IDs, the `pv:` schema, or fault injection — those stay owned by `farm_builder.py`. | PIL-1 | SLICE-6 | Proposed | Stage loads with both layers; `pv:` reads unaffected |
| FR-20 | The reconstructed stage SHALL be geo-referenced (Cesium/WGS84 or equivalent globe anchor) using the same `GeoAnchor` local↔geo mapping already defined in `schema/pv_module.py`. | PIL-1 | SLICE-6 | Proposed | Round-trip `local_to_geo`/`geo_to_local` test against survey control points |
| FR-21 | Dynamic elements (turbine blades, birds, robots) SHALL be composited as conventional kinematic/simulated USD assets over the static neural reconstruction, never reconstructed neurally themselves. | PIL-1 | SLICE-6 | Proposed | Visual QA + collision proxy present for every dynamic asset |

### Fleet, routing & deploy

| ID | Requirement | Pillar | Slice | Status | Acceptance |
|---|---|---|---|---|---|
| FR-22 | The system SHALL plan multi-robot coverage (ground bot + drones) under battery-capacity, time-window, and multi-depot constraints via cuOpt, consuming upstream-generated coverage waypoints. | PIL-6 | SLICE-7 | Proposed | Routing solution respects declared battery/time-window bounds in the scenario suite |
| FR-23 | The system SHALL provide a ROS 2 / VDA5050 `Transport` bridge implementation swappable for `sim_native`, per `docs/ROS2_CONTRACT.md`, with no orchestrator change. | PIL-6 | SLICE-7/8 | Proposed (contract exists, impl pending) | Conformance tests against `transport/base.py` |
| FR-24 | The system SHALL provide an Isaac ROS + on-Thor `Perception` implementation for real-robot deployment, validated against the same twin scenario suite (SIL→HIL) before real flight. | PIL-6 | SLICE-8 | Research | KPI parity check: real-robot run vs. twin run on the same scenario |
| FR-25 | Real-world telemetry (SCADA, weather, IV-curve) SHALL flow back through the same `FaultReport`/`pv:` payload shape to confirm or overturn a visual verdict, updating `pv:state`, `pv:iv_yield`, `pv:rul_days`. | PIL-6 | SLICE-8+ | Research | Payload round-trips through `FaultReport.from_dict`/`to_dict` unchanged |

## Non-functional requirements

| ID | Requirement | Rationale | Status |
|---|---|---|---|
| NFR-01 | **Isaac-free boundary.** `orchestrator/`, `perception/base.py`, `transport/base.py`, `control/base.py` MUST import without Isaac (`omni`/`isaacsim`/`pxr` forbidden). | CI runs `pytest tests/` with no GPU/Isaac; this is the hard testability seam per `CLAUDE.md`. | Locked |
| NFR-02 | **Reproducibility.** Every scenario in the graded suite MUST be fully specified by (seed + config + build) with no GUI-only step required to reach it. | Regression testing depends on determinism; matches the existing `farm.yaml`/`mission.yaml` seeded convention. | Locked |
| NFR-03 | **USD-as-truth.** Panel state MUST be read/written only via `schema/pv_module.py`; no fidelity upgrade (neural reconstruction, physics, perception swap) may introduce a parallel state store. | Prevents divergence between "what the twin shows" and "what the mission record says." | Locked |
| NFR-04 | **Interface stability under fidelity upgrades.** Every graduation named in `07` (kinematic→Pegasus, ground-truth→Cosmos Reason, sim-native→ROS 2/VDA5050) MUST be a swap behind the existing ABC, not a breaking signature change. | This is the entire point of the swappable-interface design; breaking it defeats the architecture. | Locked |
| NFR-05 | **On-box/off-box split.** Cosmos Transfer-class generation and (until verified) NuRec/3dgrut-class training MUST run off-box; the Spark runs only the interactive loop (world + Reason inference + fleet policy + rendering). | Cosmos Transfer is confirmed unsupported on sm_121/GB10; CUDA 13 is experimental for 3dgrut. | Locked (until `RISK-04`/`RISK-06` resolve otherwise) |
| NFR-06 | **Perception latency.** Cosmos Reason inference (screen + confirm passes) MUST complete within whatever cycle time the mission FSM budgets per panel; exact figure TBD once vLLM path is benchmarked on GB10. | Needed to reason about coverage-rate/battery-window KPIs; currently unmeasured. | ⚠ Verify — see `RISK-09` |
| NFR-07 | **No silent fidelity substitution.** Any place a physics approximation stands in for ground truth (parametric wake vs. CFD, kinematic bird trajectories vs. recorded ones) MUST be documented as such in the code/config, not presented as validated physics. | Directly protects against the "honest constraints" failure mode called out repeatedly in the research doc. | Locked |
| NFR-08 | **Data integrity of generated frames.** Any Cosmos Transfer/Predict/NuRec-Harmonizer output used to train or evaluate Perception MUST be filtered by an Evaluator step before use. | Prevents hallucinated shadows/panel detail from creating false ground truth (the exact false-hotspot failure this project exists to prevent). | Proposed |
| NFR-09 | **Version honesty.** No spec, config default, or code comment may assume a specific Isaac Sim / Isaac Lab / Pegasus / Cosmos version without a checked ⚠-verify note pointing at `08-platform-and-risk-register.md`. | The stack moves monthly; CLAUDE.md already flags this as a golden rule. | Locked |
| NFR-10 | **Small, scoped commits.** Any change touching a contract (schema, coordinates, ROS 2 topics, an interface) MUST update the relevant doc (`PROJECT_BIBLE.md` §6 and/or this `specs/` folder) in the same commit. | Matches `CLAUDE.md`'s commit convention; prevents spec drift. | Locked |
