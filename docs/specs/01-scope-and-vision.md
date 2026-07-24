# 01 — Scope & Vision

## Problem statement

A solar farm's fault surface (soiling, hotspots, cracks, string dropouts,
shading, diode faults) is expensive to inspect manually and expensive to get
wrong: a false fault sends a truck roll for nothing; a missed fault loses yield
or damages equipment. solar-twin's bet is that a robot fleet (ground bot +
drones) can inspect autonomously and reliably — but only if the autonomy is
**trained, tested, and signed off in a digital twin first**, the same
discipline NVIDIA's own AV (DRIVE Sim) and factory-fleet (Mega Blueprint) teams
use before hardware moves (`docs/DIGITAL_TWIN_VISION_AND_RESEARCH.md`, "AV /
robotics sim-to-real training playbook").

## What "done" looks like (the owner's bar)

A twin you can **see with your own eyes and trust**:

1. The stage shows *our actual site* — real terrain, real panel rows, real
   turbine towers — not a generic procedural field.
2. Turbine blades **turn** and cast moving shadows; wind **gusts**; terrain
   **undulates**; birds **cross** the drone's lane — and these are physically
   consequential, not cosmetic: they push the drone, occlude the camera,
   threaten collision.
3. A drone and a ground bot **operate the site autonomously inside the twin**:
   coverage passes, station-keeping against wind/wake, keep-out avoidance,
   grade traversal. Cosmos Reason judges each panel; verdicts land on USD
   prims via `pv:` + `FaultReport` (locked, `PROJECT_BIBLE.md` §6).
4. The operation is **de-risked in the twin first**: altitude-vs-resolution,
   coverage efficiency, battery/time-window feasibility, and the
   **false-fault rate** (a blade shadow must not become a logged hotspot) are
   measured against a graded scenario suite before a real drone launches.
5. Everything is **reproducible from a script + a seeded config** — scenarios
   are regression tests, not GUI one-offs.
6. Heavy generation/training **bursts off-box**; the Spark runs the
   interactive loop.

## In scope (this spec set covers)

- The six-pillar reference architecture (`03-architecture.md`) and how each
  pillar slots behind the existing `Perception` / `Transport` / `RobotControl`
  interfaces without churning `orchestrator/mission.py`.
- Physics fidelity for the named hazards: wind/gust, turbine wake, articulated
  turbine collision + keep-out, terrain traversability, birds.
- The Cosmos Reason perception path (already partially wired, per recent
  commits) and its accuracy/false-fault measurement.
- A graded, reproducible scenario suite and the KPIs that gate each roadmap
  slice.
- The off-box burst path for Cosmos Transfer/Predict-class generation and
  NuRec/3DGUT-class reconstruction.
- The real-robot deploy bridge (Isaac ROS, VDA5050/Mission Dispatch, cuOpt).

## Out of scope / explicit non-goals

These are named in the research as **research-grade**, not because they're
unimportant, but because committing an interface or a schedule to them today
would be premature:

- **Online WAM-in-the-loop planning** ("imagine the drone's next state under
  wake, then act"). Long-horizon physical fidelity and GB10 latency aren't
  there. Treated as an experiment behind existing interfaces only, never a
  roadmap dependency (see `07-roadmap-and-milestones.md`).
- **True CFD turbine-wake simulation.** We commit to a parametric
  velocity-deficit/actuator-disk approximation and say so explicitly — it
  stresses the controller and planner, it does not certify a real flight
  envelope.
- **Cosmos 3 as the default build target.** It's public but very new (shipped
  2026-06-01); the mature Cosmos 2.x recipe line (Reason2, Predict2.5,
  Transfer2.5) is the near-term commitment. Cosmos 3 Nano/Edge is evaluated,
  not adopted, until proven on this Spark.
- **Photoreal reconstruction training on-box as a hard requirement.** 3dgrut's
  CUDA 13/aarch64 path is experimental; reconstruction defaults to burst-out
  until a Spark build is verified (`RISK-06`).
- **A native 3D coverage-path planner.** cuOpt is a VRP/routing solver, not a
  coverage-path generator; coverage waypoints are produced upstream by us.

## Guiding constraint: thin end-to-end thread, not breadth-first

The single largest risk this spec set defends against is "boiling the ocean"
across six pillars over many quarters. Every roadmap slice
(`07-roadmap-and-milestones.md`) is **one working path through all relevant
layers**; fidelity deepens *along* a working loop, never as a big-bang
integration. The `Perception`/`Transport`/`RobotControl` interfaces plus
USD-as-truth are what make this safe: each fidelity upgrade is a swap behind
an interface, not a rewrite of the orchestrator.
  