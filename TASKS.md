# Tasks — done, and next

Plain log of what has been built and what is next. One line each.
Live per-person/per-machine tasks live in `docs/TASKS.md`; this is the whole-project view.

---

## Done

### Day 1 — 21 Jul: foundations
- Repo, project brief, docs skeleton, gitignore.
- `pv:` USD panel schema — the source of truth for panel state.
- `Perception` / `Transport` / `RobotControl` interfaces + escalation FSM.
- `FaultReport` payload and the run-record format.
- ROS 2 Jazzy installed and de-risked on the Spark.
- Verified Isaac Sim 6.0 → ROS 2 camera path end to end.
- `farm_builder` — the procedural USD farm.
- Sim-native runtime + transport + kinematic control: the full Slice 0 loop running on Isaac.
- `--save-usd` and `--record` (run video).
- Cosmos Reason wired to real camera frames, served on GB10 via vLLM (the NIM crashes on sm_121).

### 23–24 Jul: realism, hazards, first KPIs
- Research report: the six-pillar target architecture.
- Full written specs.
- Environment realism + turbine keep-out volumes (planning-layer no-fly).
- Fixed fault realism and taxonomy — KPI-01 went 0 → 1.00.
- SLICE-3 false-fault harness and the first trustworthy KPI-03 measurement.
- Parked the Cosmos 3 Edge A/B: it serves, but it is a generator, not a Reason replacement.

### 27 Jul: the real site
- PDF layout extractor with fail-closed dual-source calibration.
- Extracted the exact BLOCK-02 layout from the vendor DWG via DXF.
- Built the twin from the real Khavda layout — 273 tables / 30,016 modules, EPSG:32642.
- `--subset` so the real block can be proved before all 2.2 M prims.
- WorldModel seam + reference-based Evaluator gate.
- Real robot geometry replacing marker cubes (visual, not dynamics).
- Sun and HSAT tracker angle driven by real NOAA solar position.
- Fixed transposed module geometry and the tilt-blind panel-top bug.
- KPI-03 self-shading scenario on the real block; 0.00 on 560 panels, stimulus proven.

### 28 Jul: the whole plant
- Watchable demo video: interpolated flight, chase cam, drone-cam inset.
- Whole-plant build via USD instancing, plus roads, fence, inverters, real sky.
- Real terrain: Copernicus GLO-30 DEM with straight torque tubes through the grade.
- Wind turbines, serpentine routing, cruise speeds.
- Status-tour video: what is built, what is ours, what is missing.
- Turbines sited by wake spacing; roads put on the grade; fleet scaled to real platforms.
- Audited the CAD ingest instead of rebuilding it — BLOCK-02 was already fully in.

### 29 Jul: defensible measurement, and physics that runs
- Pinned VLM decoding; fixed a missing brace that was inventing faults.
- KPIs reported as spreads, gates enforced, renderer-vs-model flips attributed.
- Isaac-free rule enforced in CI instead of in markdown.
- PX4 SITL verified running on aarch64.
- ROS 2 Transport seam built and proved on real DDS.
- Turbine given real articulation; wind field authored ourselves (PhysX forcefields don't exist on this build).
- SC-01 measurement scenario; stopped `turbines: []` silently lying.
- SLICE-1 closed: VLM measured against the stub on one shared stage.
- Prompt engineering measured out of the KPI-01 problem.
- Pegasus ported onto Isaac 6.0.1 via a compatibility shim.
- **PX4 flies an Iris in Isaac** — hover 2.562 m ± 43 mm.
- Graded civil pad modelled, without claiming a perfect surface.
- Turbines re-sited from real survey data (GatiShakti digest) instead of our guess.
- Whole-plot S05b farm added, with both blockers stated.
- Found the false faults were the panel next door, not the model.
- Plant stood in real OSM geography, with the bbox derived rather than trusted.

### 30 Jul: scale, and the suspicion-first layer
- Fixed the quadratic build: panels authored as Sdf specs in one ChangeBlock.
- WebRTC livestream made to actually reach a client.
- Turbines stood among the DC blocks; measured what "inside" means.
- Physical sky + PBR for the balance of plant, holding the fill constant.
- `grid:id` added as a join key above `pv:`.
- Suspicion-first cell ranking from simulated SCADA — with "off" as the identity.
- KPI-09, the `grid:` contract and the Cosmos Transfer plan written down.

### 31 Jul: honesty passes, then the look
- Fixed the shadowed module alias that killed every PBR farm build.
- Defaulted the PBR layer off: it rendered the desert near-black.
- Measured recall properly — **KPI-01 is accuracy, not recall; a null model passes its gate**.
- Fixed the glass mask a blue sky had broken; re-derived the stimulus.
- Proved the terrain in pixels; found its relief sits inside GLO-30's error bars.
- `world/bladeshadow.py` — locate a blade-shadow stimulus geometrically before measuring it.
- `khavda_bladeshadow{,_windy}` (SC-14/15) — a control/treatment pair differing only in wind.
- **Refuted the blade shadow in pixels**: a 4 m blade at 780 m has a 7.2 m penumbra, so it casts no hard shadow at all.
- Found the tower shadow instead — 3 tables darkened, continuously, and pixel-verified.
- `control/wind_drift.py` — wind pushes the camera off station, so perception is scored on a moved frame.
- `--hold` + `world/viewer.py` — open the plant in Isaac and fly around it.
- **MDL materials** (OmniPBR/OmniGlass) replacing 13 flat colours; glass on the modules.
- **Module rebuilt** as glass over flush cells with a perimeter frame — it read as bathroom tile before.
- **Racking added** — 273 torque tubes and 5,900 piles; 30,016 modules had been floating on nothing.
- Fixed the OSM roads: quads wound clockwise, so every road was backfacing.
- Photographic exposure (f/9) — and found it must be applied *after* `open_stage`.
- **Procedural HDR sky** — ours rendered 0.14× the ground brightness; ClearSky fixes it.
- `tools/cad_to_usd.py` — CAD → USD verified working (step/dwg/dxf/sldprt/jt/stl).
- **Gave the world a floor** — only 25 colliders existed in 82k prims, all on turbines.
- Measured the physics ceiling: 1.0× realtime at 8k prims, **0.07× at 82k** — and it's scene-graph sync, not collision.
- **Conditions reel** — 45 s, six lighting/weather conditions, every shot labelled.

### 3 Aug: physics, colliders, a bigger plant, first flight in the plant
- **Gave the hardware colliders** — per-table boxes + piles; verified by drop test (rests at 1.816 m, predicted 1.815).
- Removed a `module` collider option that silently did nothing — instancing blocks prototype colliders.
- Measured that collider count costs nothing: 16 → 568 colliders, 212 → 211 steps/s.
- **Physics now steps during a mission** (`--physics`), and turning it on changes no verdict.
- **Four-block plant** — 117,264 modules over 3.15 km, all real surveyed positions, nothing tiled.
- **PX4 flew inside the real plant for the first time** — armed, took off, held 2.7 m over the array.
- Found three silent PX4 failures: no sensor stream from `world.step()` alone, a wrong arm binary path, and pre-warm being necessary but not sufficient.

### 3 Aug: Isaac ROS feasibility
- Verified Isaac ROS on this Spark: **GO** — official test matrix, all version requirements met.
- Found the apt debs are **x86_64-only** (arm64 ships 50 shim packages and zero compiled ones) — containers are the only route.
- Confirmed 31 aarch64 container tags exist and resolve; `-fastos` is the DGX-OS variant, `-jetpack` the Jetson one.
- Found cuVSLAM needs **stereo** — our drone is monocular, so a synced stereo pair is a new prerequisite.

---

## Next

### Now — make the robots real (2–3 weeks, low/medium risk)
- ~~Colliders on panels and racking~~ ✅ per-table boxes + piles; drop test lands at 1.816 m vs 1.815 predicted.
- ~~Collider granularity~~ ✅ per-table; `module` removed (instancing blocks it), and count costs nothing measurable.
- ~~Step physics in the mission loop~~ ✅ `--physics`; verified identical verdicts with it on.
- ~~Fly a PX4-governed drone in the real plant~~ ✅ armed, took off, held 2.7 m over the array (`tools/px4_in_plant.py`). ⚠ 0.11× realtime — not a KPI-05 sample.
- Fly a WAYPOINT PASS, not just a hover — needs `MIS_TAKEOFF_ALT` or position setpoints.
- Cut the ~10× Pegasus/PX4 overhead, or accept flight only on small stages.
- Make ROS 2 the live transport instead of an interface that is never exercised.

### Then — perception and navigation (2–3 months, risk now MEDIUM)
- ~~Feasibility check: does Isaac ROS run on this Spark?~~ ✅ **GO** — Spark is in the official test matrix; CUDA/driver/ROS/Docker all satisfied.
- Pull the arm64 Isaac ROS container (18 GB) — needs a decision, not yet done.
- **Publish a synchronised STEREO pair from Isaac** — cuVSLAM will not take our single mono camera. New prerequisite, found by the check.
- cuVSLAM for visual odometry — the first time a robot estimates where it is.
- nvblox for a 3D costmap.
- Nav2 for the ground bot — plan around obstacles instead of being clamped away from a sphere.
- Close the loop: sense → localize → plan → act → perceive → verdict.

### Open engineering questions
- Full-plant physics: try the dedicated `isaac-sim.fabric.sh` app (runtime Fabric enabling did nothing).
- Ground textures and scatter — the desert is still flat untextured beige.
- Get a real tracker/module CAD file and put it through the converter; no NVIDIA PV asset exists.
- Scale the 4-block stage further (24 blocks / 679,616 modules exist in the digest) once physics cost is addressed.
- Turbine geometry is still a cylinder plus three flat paddles.

### The missing pillar — the plant as an energy asset
- pvlib as our ETAP: real per-string DC → plant AC from the real geometry, sun and DEM.
- Retire `simulated_scada.py`, which is circular by its own admission.
- Link fault → kWh → rupees: "this hotspot costs 43 kWh/day".
- Show solver results on the geometry, so opening the stage *is* the dashboard.
- Turbine wake as a trained PhysicsNeMo surrogate (the Siemens Gamesa pattern), not CFD in our loop.

---

## Known-not-real (the honest list)
- Robot motion is **scripted** — teleported along waypoints. No localization, no SLAM, no costmap, no path planning.
- PX4 flies only in a separate hover scenario, never in the inspection mission.
- The ROS 2 bridge is written and smoke-tested, but nothing depends on it.
- Physics is 0.07× realtime on the full plant — subset only.
- Thermal is an emissive proxy; Isaac renders no true IR.
- Soiling on glass is real; atmospheric dust/visibility is not built.
- No energy or power model at all.
- Turbine positions are real and surveyed; hub height, blade length and rpm are assumptions.
