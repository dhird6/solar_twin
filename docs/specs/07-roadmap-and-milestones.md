# 07 — Roadmap & Milestones

Each slice is **one thin end-to-end thread**, not a layer built in isolation
(`01-scope-and-vision.md`). Exit criteria are measured KPIs
(`06-scenario-suite-and-kpis.md`), not GUI demos (`FR-17`). This is
explicitly multi-quarter — do not compress it.

## SLICE-0 — Procedural farm, stub perception (done / current)

- **Goal:** prove the sandbox — procedural farm, fixed panel rendering,
  kinematic teleport drone, stub perception → VLM verdict → USD write.
- **Entry:** none (starting point).
- **Deliverables:** `farm_builder.py`, `pv_module.py`, `ground_truth.py`,
  `kinematic.py`, `sim_native.py`, `orchestrator/mission.py` FSM.
- **Exit criteria:** `SC-01` passes; `KPI-01` measured and recorded as
  baseline; loop is explicitly open-loop (frames → verdict, no reaction).
- **Runs:** Spark-local.
- **Status:** Done.

## SLICE-1 — Cosmos Reason on the real feed

- **Goal:** wire Cosmos Reason 2 (2B/8B, vLLM/FP8) as the live `Perception`
  impl against actual RTX camera frames, confirming the sm_121 vLLM path
  (`FR-02`).
- **Entry:** `SLICE-0` exit criteria met.
- **Deliverables:** `cosmos_reason.py` behind `Perception` with no
  orchestration change; `mission_cosmos.yaml`-style config flip.
- **Exit criteria:** `KPI-01` measured with `perception: cosmos_reason` on
  `SC-01` for healthy/hotspot/soiled; result compared against the
  `SLICE-0` ground-truth baseline.
- **Runs:** Spark-local.
- **Status:** Partly in flight per recent commits (`mission_cosmos.yaml`,
  camera-frame wiring).

## SLICE-2 — Physics that bites, one drone

- **Goal:** replace kinematic control with Pegasus/PX4 SITL; add
  `omni.physx.forcefields` wind + one articulated turbine with a swept-disk
  keep-out (`FR-06`–`FR-13`).
- **Entry:** `SLICE-1` exit criteria met; `RISK-02` (Pegasus on aarch64)
  smoke-tested.
- **Deliverables:** Pegasus-backed `RobotControl` impl; `world/hazard_builder.py`
  (or equivalent) for turbine + force fields; `SC-02`, `SC-03`, `SC-04`,
  `SC-08` scenario configs.
- **Precursors already shipped (2026-07-24, commit `7894eb3`):** the
  planning-layer keep-out (`SafeControl` + `world/keepout.py`, `IF-07`) — `FR-09`
  is done; kinematic spun-turbine proxies + a heightfield **mesh**; PhysX
  colliders authored (inert). **Remaining for this slice:** Pegasus/PX4 control,
  `omni.physx.forcefields` wind/wake, turbine as a real *articulation* whose
  colliders fire (`RISK-11`), and heightfield **trimesh collision** (`RISK-14`).
- **Exit criteria:** drone flies a coverage pass under gust, holds station
  within `KPI-05` bounds, avoids the keep-out (`KPI-04` = 1.0 on `SC-03`/
  `SC-04`); ground bot passes `SC-08` at the declared max grade.
- **Runs:** Spark-local. **Gating risk:** `RISK-02` (Pegasus-on-aarch64 smoke
  test).

## SLICE-3 — The false-fault loop

- **Goal:** add sweeping blade shadows + motion blur through Sensor
  RTX/`ovrtx`; measure Cosmos Reason's false-fault rate when a shadow
  crosses a healthy panel (`FR-03`).
- **Entry:** `SLICE-2` exit criteria met (turbine + shadow-casting geometry
  exists).
- **Deliverables:** `SC-05`, `SC-06` scenario configs; `IF-02` (environment
  context enrichment) implemented; `KPI-03` measurement harness.
- **Precursors already shipped (2026-07-24):** blade-shadow-casting geometry
  (spun turbines over the panel row) and a first `KPI-03` datapoint — live
  Cosmos Reason held "clean" 0/6 under moderate sweeping shadow. This slice
  extends that to a proper harness (low-sun `SC-05`, motion blur `SC-06`,
  worst-case shadow bisecting the cells) via Sensor RTX/`ovrtx`.
- **Exit criteria:** `KPI-03` measured and reported (no fixed pass/fail
  threshold yet — this slice establishes the baseline the project will drive
  down); `KPI-01` on `SC-01`/`SC-02` has not regressed.
- **Runs:** Spark-local rendering.

## SLICE-4 — Scenario factory (first burst-out)

- **Goal:** stand up Cosmos Transfer/Data Factory Blueprint + OSMO off-box to
  fan the seeded scene into dust/haze/low-sun/blade-shadow/bird variants;
  Evaluator filters implausible frames; harden Reason on the corpus
  (`FR-18`).
- **Entry:** `SLICE-3` baseline `KPI-03` established; off-box compute
  provisioned (RTX PRO 6000 / DGX / cloud).
- **Deliverables:** `SC-09` scenario pack; Evaluator integration (`NFR-08`
  gate); a re-measured `KPI-03` after hardening.
- **Exit criteria:** `KPI-08` (generated-frame validity rate) reported;
  `KPI-03` on `SC-05`/`SC-06`/`SC-09` shows measurable improvement over the
  `SLICE-3` baseline, or a documented reason it doesn't.
- **Runs:** Burst-out generation; Spark seeds and consumes.

## SLICE-5 — Trained flight policy

- **Goal:** train an Isaac Lab RL policy for station-keeping + gust
  rejection under domain-randomized wind and procedural graded terrain; add
  birds + terrain grade + parametric turbine wake to the training
  distribution (`FR-14`, `PIL-4`).
- **Entry:** `SLICE-2`/`SLICE-3` exit criteria met.
- **Deliverables:** `SC-07` scenario; trained policy checkpoint; closed-loop
  KPI gate replacing the open-loop `SLICE-2` controller where applicable.
- **Exit criteria:** `KPI-05` under domain-randomized wind meets or beats the
  hand-tuned `SLICE-2` controller; `KPI-04` on `SC-07` (birds) = 1.0.
- **Runs:** Training burst-out; closed loop runs on Spark.

## SLICE-6 — The real site

- **Goal:** drone-survey → NuRec/3DGUT reconstruction → Cesium-georeferenced
  USD, composited with kinematic turbines/birds, rendered through Sensor RTX
  (`FR-19`–`FR-21`).
- **Entry:** `RISK-06` (3dgrut CUDA-13/aarch64) resolved one way or the
  other — either it trains on-box, or reconstruction is formally burst-out.
- **Deliverables:** capture→COLMAP→3DGUT→USDZ (or current export format,
  `RISK-07`) pipeline; georeferenced stage with both appearance layers
  present; `SC-08` re-run against the real terrain mesh.
- **Exit criteria:** stage loads with procedural state layer + neural
  appearance layer coexisting; `local_to_geo`/`geo_to_local` round-trip
  validated against real survey control points; `KPI-07` re-measured on the
  real DEM.
- **Runs:** Reconstruction burst-out (unless `RISK-06` resolves otherwise);
  rendering Spark-local.

## SLICE-7 — Fleet + coverage brain

- **Goal:** add the ground bot as a full fleet member; cuOpt plans
  battery/time-window coverage across both robots; Mission
  Dispatch/VDA5050 dispatches (`FR-22`, `IF-01`, `IF-04`, `IF-05`).
- **Entry:** `SLICE-2` (ground bot terrain traversal) and `SLICE-6`
  (real coverage area) exit criteria met.
- **Deliverables:** `orchestrator/routing.py` (`IF-05`); `EnergyAware`
  protocol (`IF-01`); `Fleet`/`FleetRoster` decision resolved (`IF-04`);
  `SC-10` scenario.
- **Exit criteria:** `KPI-02` (coverage %) and `KPI-06` (battery/time-window
  adherence) measured on `SC-10`; routing solution never breaches the
  declared battery reserve floor.
- **Runs:** Spark for cuOpt + interactive loop. **Gating risk:** `RISK-08`
  (Mission Dispatch containers not yet Spark-supported — may need off-box or
  a Jetson for the fleet-command loop specifically).

## SLICE-8 — Deploy bridge

- **Goal:** swap `Transport` to the ROS 2/VDA5050 bridge, `Perception` to
  Isaac ROS + on-Thor Cosmos Reason; validate against the same twin scenario
  suite; SIL→HIL before real flight (`FR-23`–`FR-25`).
- **Entry:** `SLICE-7` exit criteria met; `ros2_bridge.py` implemented
  per `docs/ROS2_CONTRACT.md`.
- **Deliverables:** ROS 2 `Transport` impl; on-Thor `Perception` impl;
  SIL→HIL comparison report against the `SLICE-0`–`SLICE-7` scenario suite.
- **Exit criteria:** KPI parity check — real-robot run vs. twin run on the
  same scenario, within a declared tolerance (tolerance TBD, tracked as
  `RISK-17`); `FaultReport` round-trips unchanged over the real telemetry
  path (`FR-25`).
- **Runs:** Spark prototype → Jetson Thor/Orin on metal.

## Cross-slice invariants (must hold at every exit gate, not just the current one)

- `NFR-01` (Isaac-free boundary) — checked by `pytest tests/` passing with no
  GPU/Isaac at every slice, not just `SLICE-0`.
- `NFR-04` (interface stability) — no slice may change an existing ABC
  signature; extensions follow `04-interfaces-and-data.md`.
- Every scenario introduced at or before the current slice must still meet
  its `kpi_gates` (`06-scenario-suite-and-kpis.md`, "Gating discipline") —
  regressions block new work, they don't get silently accepted.
