# 05 — Hazard & Safety Specification

Each hazard is specified as: trigger condition, mitigation design, the
fidelity trade-off being made (be honest about it — `NFR-07`), the
verification method, and which requirement/pillar/slice owns it. This table
is the formalized version of the research doc's hazard model, structured for
tracking rather than narrative.

## HAZ-01 — Blade collision

- **Trigger:** drone flight path intersects the turbine tower, nacelle, or
  the instantaneous swept volume of a rotating blade.
- **Mitigation:** each turbine is a USD articulation (driven revolute joint,
  mesh/convex colliders on tower/nacelle/blades) for real instantaneous
  collision, **plus** a precomputed conservative swept-disk keep-out
  cylinder/annulus that the planner treats as a hard no-fly constraint — the
  planner must never rely on instantaneous collision alone (`FR-09`, `FR-11`).
- **Fidelity trade-off:** none claimed here — this is meant to be a real
  safety margin, not an approximation. The open item is whether articulation
  collider cooking behaves as expected on the installed build.
- **Verification:** `KPI-04` (collision-free-flight rate) = 1.0 across the
  scenario suite; zero recorded keep-out intrusions.
- **Owner:** `FR-09`, `FR-11` · `PIL-2` · `SLICE-2`.
- **Residual risk:** `RISK-11` (articulation collider cooking unverified on
  installed build).

## HAZ-02 — Wake / rotor-wash turbulence

- **Trigger:** drone enters the downstream volume of a spinning turbine
  rotor.
- **Mitigation:** a parametric velocity-deficit / actuator-disk force field
  (Jensen/Gaussian profile) driving `averageSpeed` + `speedVariation` in the
  affected volume, parameterized by thrust coefficient, rotor diameter, and
  downstream distance (`FR-13`).
- **Fidelity trade-off — stated explicitly, not hidden (`NFR-07`):** this
  captures mean push + turbulence *intensity*, not true vortex shedding or
  blade-passing-frequency effects. It is good enough to stress the flight
  controller and the coverage planner. It is **not** sufficient to certify a
  real flight envelope. True CFD is out of scope (see `01-scope-and-vision.md`
  non-goals).
- **Verification:** station-keeping error and controller saturation measured
  under the wake field in the scenario suite (`KPI-05`); qualitative check
  against any available reference turbine wake data (`RISK-12`).
- **Owner:** `FR-13` · `PIL-2` · `SLICE-2`.
- **Residual risk:** `RISK-12` (no CFD ground truth to validate wake
  parameters against).

## HAZ-03 — Gust winds

- **Trigger:** ambient wind exceeds the station-keeping controller's
  rejection capability during a screen/confirm pass, or induces
  frame-to-frame motion blur.
- **Mitigation:** `omni.physx.forcefields` Wind + Drag + Noise, with
  `speedVariation` as the gust term (`FR-12`); longer-term, an RL
  station-keeping policy trained under domain-randomized wind (`FR-06`
  graduation path, `PIL-4`); motion-blur scenario variants feed perception
  robustness testing (`HAZ-07`).
- **Fidelity trade-off:** force-field wind is a reasonable approximation of
  ambient turbulence; it is not a weather model.
- **Verification:** `KPI-05` (station-keep error) under a swept range of
  `average_speed`/`speed_variation` values defined per-scenario
  (`04-interfaces-and-data.md` IF-03).
- **Owner:** `FR-08`, `FR-12` · `PIL-2` · `SLICE-2`.
- **Residual risk:** `RISK-13` (the documented "articulation link must be in
  a scene" force-field error — the drone is an articulation; reproduce/avoid
  before relying on this).

## HAZ-04 — Non-flat terrain

- **Trigger:** ground bot traverses a slope, or the drone's altitude
  reference (AGL vs. absolute) is wrong because terrain isn't flat.
- **Mitigation:** Isaac Lab heightfield→trimesh collision for procedural
  grades (`FR-10`); a real DEM/photogrammetry mesh for the reconstructed site
  (`PIL-1`, `SLICE-6`); Cesium/WGS84 georeference gives a correct AGL
  reference; nvblox costmap feeds ground-bot local planning post-deploy
  (`PIL-6`).
- **Fidelity trade-off:** slope-aware Nav2 traversability is emerging,
  bespoke tuning, not turnkey — this is a real engineering task per
  deployment terrain, not a solved library call.
- **Verification:** ramp testbed pass/fail at 5°/10°/15°/20° grades
  (`KPI-07`); confirm the twin's declared max-grade envelope matches
  ground-bot behavior at that grade.
- **Owner:** `FR-10` · `PIL-2`, `PIL-6` · `SLICE-2`/`SLICE-6`.
- **Residual risk:** `RISK-14` (trimesh mesh-cooking fall-through history on
  custom DEMs — validate on our specific terrain, not just Isaac Lab's
  built-in primitives).

## HAZ-05 — Birds (dynamic obstacles)

- **Trigger:** a bird crosses the drone's flight lane, either as a genuine
  obstacle (avoid) or as a visual disturbance that must not be confused with
  a panel fault.
- **Mitigation:** rigid-body agents on scripted/randomized trajectories
  (`FR-14`); avoidance is a perception+planner concern at the RTX-camera +
  depth + costmap layer, not a hazard the physics layer alone resolves.
- **Fidelity trade-off:** procedural/scripted bird trajectories are not a
  substitute for real bird-strike statistics; treat as a training/testing
  stimulus, not a certified avoidance benchmark.
- **Verification:** `KPI-04` (collision-free-flight rate) includes bird
  encounters in the adversarial scenario set; a shadow-vs-obstacle
  disambiguation check feeds `HAZ-07`.
- **Owner:** `FR-14`, `FR-15` · `PIL-2`, `PIL-6` · `SLICE-5`.
- **Residual risk:** `RISK-15` (procedural trajectories vs. a
  recorded/learned motion set for realism — open design choice).

## HAZ-06 — Altitude / coverage trade-off

- **Trigger:** flight altitude choice trades panel-image resolution against
  wind/collision exposure and coverage efficiency; battery/daylight windows
  bound how much of the farm one sortie can cover.
- **Mitigation:** closed-loop evaluation of flight-policy KPIs (`PIL-4`);
  cuOpt for battery-capacity + time-window + multi-depot routing over
  inspection waypoints (`FR-22`, `PIL-6`).
- **Fidelity trade-off:** cuOpt is a mature VRP/routing solver, **not** a
  native 3D coverage-path planner — coverage waypoints are generated
  upstream by us, then handed to cuOpt for routing (`01-scope-and-vision.md`
  non-goals).
- **Verification:** `KPI-02` (coverage %) and `KPI-06` (battery/time-window
  adherence) measured per scenario.
- **Owner:** `FR-22` · `PIL-4`, `PIL-6` · `SLICE-7`.
- **Residual risk:** `RISK-10` (cuOpt aarch64/GB10 build unverified).

## HAZ-07 — False-fault perception failure (cross-cutting; the central thesis hazard)

- **Trigger:** any of the above hazards (blade shadow, motion blur, dust,
  wake-induced camera jitter) produces a visual disturbance on a **healthy**
  panel that `Perception` misreads as a fault (or the converse: a real fault
  masked by a disturbance and missed).
- **Mitigation:** the `SLICE-3` false-fault harness — sweeping blade
  shadow + motion blur rendered through Sensor RTX/`ovrtx`, scored against
  Cosmos Reason's verdict (`FR-03`); the `SLICE-4` scenario factory
  (Transfer/Predict + Evaluator) hardens perception against the long tail
  off-box (`FR-18`); generated frames are gated through an Evaluator before
  use to avoid training on hallucinated shadows (`FR-05`, `NFR-08`).
- **Fidelity trade-off:** this hazard is *the reason the project exists* —
  it gets the most measurement rigor of any hazard, not the least.
- **Verification:** `KPI-03` (false-fault rate) — the single most
  important gating metric in `06-scenario-suite-and-kpis.md`.
- **Owner:** `FR-03`, `FR-05` · `PIL-3`, `PIL-5` · `SLICE-3`/`SLICE-4`.
- **Residual risk:** `RISK-16` (does Cosmos Transfer preserve geometrically
  correct shadow motion, or hallucinate it? Unresolved — gates `NFR-08`).

## Safety envelope summary

| Hazard | Hard constraint (never violated) | Soft/measured (KPI-gated) |
|---|---|---|
| HAZ-01 Blade collision | Keep-out volume never entered | Collision-free-flight rate = 1.0 |
| HAZ-02 Wake | — (no hard stop; a controls problem) | Station-keep error bound |
| HAZ-03 Gusts | — | Station-keep error bound |
| HAZ-04 Terrain | Ground bot never exceeds declared max grade in autonomous mode | Grade pass/fail per testbed |
| HAZ-05 Birds | No-fly geofence honored | Collision-free-flight rate |
| HAZ-06 Altitude/coverage | Battery reserve floor never breached | Coverage %, time-window adherence |
| HAZ-07 False-fault | — (probabilistic by nature) | False-fault rate ≤ threshold per scenario |
