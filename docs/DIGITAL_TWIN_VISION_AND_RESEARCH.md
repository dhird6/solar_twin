# Solar-Twin: Vision & Reference Architecture for a Full NVIDIA Physical-AI Digital Twin

> **Status:** research report · **Generated:** 2026-07-23 · **Branch:** cosmos-reason-live
>
> The *target* the project is building toward — a photoreal, physically-honest,
> trainable-before-real digital twin of a real solar farm — with the reference
> architecture, hazard model, and phased roadmap to get there on NVIDIA's Physical-AI
> stack. Produced by a 13-agent research swarm (6 fact-checked research streams + a
> lead-architect synthesis) with live web research. Claims marked **⚠ verify** are
> version/roadmap-specific — check them against the installed build first; this stack
> moves monthly.
>
> Companion docs: `PROJECT_BIBLE.md`, `solar-inspection-digital-twin-plan.md`,
> `STACK.md`, `ENVIRONMENT.md`.

---

## Executive summary

The target is a photoreal, physically-honest, geo-referenced digital twin of a *real* solar site — the actual panels, the actual wind turbines with turning blades, the actual undulating terrain — in which a drone-plus-ground-bot fleet can be trained, tested, and signed off *before* it flies over real hardware, exactly the way NVIDIA's factory/warehouse twins (Mega Blueprint) and AV teams (DRIVE Sim) validate autonomy in simulation before deployment. The core architectural insight is that this requires **two complementary layers, not one**: a **physics-sim layer** (PhysX / Pegasus-PX4 / Isaac Lab) that answers *"can the fleet OPERATE?"* — can the drone hold station in a gust, survive turbine wake, avoid the swept blade volume, and traverse graded terrain within battery and daylight windows — and a **world-model layer** (Cosmos WFM: Reason + Predict/Transfer, now converging into Cosmos 3) that answers *"does PERCEPTION still WORK while it operates?"* — does Cosmos Reason still read a panel correctly when a blade shadow sweeps across it, when the camera is motion-blurred by wind, when dust and low sun degrade the frame. Physics tests the *body*; world models test the *eyes*. Neither alone is sufficient, and both slot behind our existing `Perception` / `Transport` / `RobotControl` interfaces with the USD stage remaining the source of truth for panel state — so the twin's fidelity can graduate over many quarters without churning orchestration.

## The target vision — what "done" looks like

In the owner's terms, "done" is a twin you can **see with your own eyes and trust**:

- You open the stage and you are looking at *our actual site* — terrain reconstructed from a real drone survey, panel rows where they really stand, real turbine towers — not a generic procedural field.
- The turbines' blades **turn**, casting moving shadows across panel rows; wind **gusts**; the terrain **undulates**; birds **cross** the drone's lane. These are not cosmetic — they push the drone, occlude the camera, and threaten collision.
- A drone and a ground bot **operate the site autonomously inside the twin**: the drone flies a coverage pass at a chosen altitude, holds station over a panel against wind and rotor wake, avoids the blade keep-out volume and birds; the ground bot traverses grade. Cosmos Reason judges each panel; verdicts are written back onto USD prims via the `pv:` schema and `FaultReport`.
- The **whole operation is de-risked in the twin first**: flight altitude vs. resolution trade-offs, coverage efficiency, battery/time-window feasibility, and — critically — the **false-fault rate** (a swept blade shadow must not be logged as a hotspot) are measured against a graded scenario suite *before* a real drone launches.
- Everything is **reproducible from a script + a seeded config**, not a GUI one-off, so scenarios become regression tests.
- The heavy generation and training that make this robust (scenario multiplication, policy training) **burst off-box**; the Spark runs the interactive loop.

## Six-pillar reference architecture

| # | Pillar | Role | NVIDIA stack | Spark or burst-out |
|---|--------|------|--------------|--------------------|
| 1 | **Photoreal neural-reconstructed twin** | Turn the *real* site (terrain, panels, turbine towers) into a photoreal, geo-referenced USD scene you can inspect and render sensors against | OpenUSD + Omniverse; **NuRec / 3DGUT** (`nv-tlabs/3dgrut`), COLMAP SfM; **Cesium for Omniverse** (WGS84 georeference); **Sensor RTX / `ovrtx`** rendering; Isaac Sim 6.0 Fabric Scene Delegate for splats | **Rendering on Spark** (Isaac Sim, ovrtx ships an aarch64 wheel — ⚠ verify sm_121). **Reconstruction/training burst-out** unless a CUDA-13/aarch64 3dgrut build checks out on GB10 (CUDA 13 is "experimental") |
| 2 | **Physics that bites** | Make the drone stop teleporting: real flight dynamics, wind/gust forces, spinning turbines with collision + swept keep-out, traversable graded terrain, birds | **PhysX** + `omni.physx.forcefields` (Wind/Drag/Noise); **Pegasus Simulator v5.1.0** (PX4 SITL v1.14.3); USD articulated turbines; Isaac Lab terrain generators (heightfield→trimesh) | **Spark-local** (⚠ Pegasus validated on x86_64/Isaac 5.1 — aarch64/sm_121 and Isaac-6.x unproven). Turbine-wake CFD is off-box; on-box wake is a **parametric velocity-deficit field**, not CFD |
| 3 | **Cosmos WFM data / scenario factory** | Fan one seeded farm render into the long-tail we can't safely fly: dust/haze/low-sun/dew variants, sweeping blade shadows, gusts, bird strikes — to harden perception | **Cosmos Transfer 2.5** (sim2real, 4 control branches) + **Predict 2.5** (future video) → converging into **Cosmos 3**; **Physical AI Data Factory Blueprint** (Curator→Transfer→Evaluator) + **OSMO** orchestration | **Burst-out** (RTX PRO 6000 / DGX / cloud). Cosmos Transfer is explicitly **not supported on the Spark**. ⚠ verify whether a distilled Transfer variant or Cosmos 3 Edge/Nano changes this |
| 4 | **Closed-loop sim + policy training** | Train and gate autonomy: RL flight/coverage policies under domain-randomized wind + terrain; closed-loop scenario suites with measured KPIs before deployment | **Isaac Lab** (GPU-parallel RL/IL, first-class domain randomization; 2.3 stable / 3.0-beta); Omniverse Blueprint for closed-loop validation; **OmniDreams**-style closed-loop world models (research) | **Training bursts out** at scale; **Spark** runs the interactive closed loop and small validation runs. ⚠ confirm installed Isaac Lab version |
| 5 | **Reason + act brains** | The perception/decision brain (judge panels) and, longer-term, action policies that predict-and-act | **Cosmos Reason 2** (2B/8B, vLLM + FP8) behind our `Perception` interface; **Cosmos Policy** / **GR00T-Dreams / DreamGen** for WAM-style data-factory policy training | **Reason inference on Spark via vLLM** (the sm_121 NIM-crash workaround). Policy/WAM training bursts out. Online WAM-in-the-loop planning is **research-only** for us |
| 6 | **Real-robot deploy** | Re-implement the same interfaces on metal: GPU perception, nav, fleet routing, on-robot compute | **Isaac ROS 4.5** (cuVSLAM, nvblox, Perceptor, NITROS) + **Nav2**; **cuOpt** (Apache-2.0) routing; **Mission Dispatch / VDA5050 / MQTT**; **Jetson AGX Thor / Orin** | **Spark supported since Isaac ROS 4.2** (nvblox lists Spark) for prototyping — **but Mission Dispatch/Database containers are NOT yet Spark-supported**. Real robots run on-Jetson |

## Hazard model

| Hazard | Which pillar solves it | Honest status |
|--------|------------------------|---------------|
| **Blade collision** (tall turbines, moving blades) | P2 — USD articulated turbine (driven revolute joint, mesh colliders) for real collision + a **precomputed conservative swept-disk keep-out** cylinder/annulus the planner never threads | **Buildable on Spark now.** Instantaneous collision + static keep-out are standard; verify articulation collider cooking |
| **Wake / rotor-wash turbulence** pushing the drone | P2 — **parametric velocity-deficit / actuator-disk** force field (Jensen/Gaussian profile driving `averageSpeed`+`speedVariation`); P4 to train a policy against it | **Deliberate fidelity trade-off.** Captures mean push + turbulence intensity, **not** true vortex shedding / blade-passing frequency. True CFD is off-box. ⚠ verify vs. reference turbine data |
| **Gust winds** (flight stability, motion blur) | P2 — `omni.physx.forcefields` Wind + Drag + Noise (`speedVariation` = gust term); P4 — RL station-keeping policy under DR wind; P3 — motion-blur variants for perception robustness | **Buildable on Spark.** ⚠ watch the "articulation link must be in a scene" force-field error; verify 5.1 schema/API |
| **Non-flat terrain** (mounting on grade, traversability, AGL vs absolute altitude) | P1 — Cesium georeference (real WGS84 terrain, AGL reference) + DEM/photogrammetry mesh; P2 — Isaac Lab heightfield→trimesh collision; P6 — nvblox costmap for the ground bot | **Buildable.** Slope-aware Nav2 traversability is **emerging, bespoke tuning**, not turnkey. ⚠ trimesh mesh-cooking fall-through (issue #2323, now closed) — validate on our DEM |
| **Birds** (dynamic obstacles) | P2 — rigid-body agents on scripted/randomized trajectories; P6 — detect/avoid at the RTX-camera + depth + costmap layer; P3 — generate bird-strike scenario clips | **Buildable on Spark;** avoidance is a perception+planner problem. Shadow-vs-obstacle disambiguation is a **perception task to train against**, not solved by Nav2 alone |
| **Altitude / coverage trade-off** (resolution vs wind/collision risk, pass coverage, battery/time windows) | P4 — closed-loop evaluation of flight-policy KPIs; P6 — **cuOpt** for battery-capacity + time-window + multi-depot routing over inspection waypoints | **cuOpt is mature** for the routing/VRP problem, but is **not a native 3D coverage-path planner** — we generate coverage waypoints upstream, then hand cuOpt the routing. ⚠ verify aarch64/GB10 build |

## How this maps onto our codebase

The whole architecture is designed to slot in **behind the three existing interfaces with the USD stage staying authoritative**, so orchestration (`orchestrator/mission.py` FSM) never churns:

- **USD-as-source-of-truth is preserved.** Panel state stays in `pv:` attributes read/written via `schema/pv_module.py`; verdicts still flow as `FaultReport`. Every fidelity upgrade below is an *appearance* or *dynamics* change layered under the interfaces — the state model does not move.
- **`Perception`** — today `ground_truth.py` (stub) and `cosmos_reason.py` (HTTP skeleton, served via vLLM to dodge the sm_121 NIM crash). This becomes the seam where Cosmos Reason 2 (vLLM/FP8) drops in unchanged, and later where an Isaac ROS + on-Thor perception impl takes over for real robots. No orchestration change — a `mission.yaml` flip, as the memory note already records.
- **`Transport`** — today sim-native (default). The deploy path swaps in the ROS 2 / VDA5050 bridge (`ros2_bridge.py`, per `docs/ROS2_CONTRACT.md`) without touching callers — consistent with the adopted "sim-native default, ROS 2 behind an interface" rule.
- **`RobotControl`** — today `kinematic.py` (teleport, Slice 0). This is exactly what graduates: the **kinematic drone becomes a Pegasus/PX4 SITL drone** the moment we need station-keeping, gust rejection, or battery-limited coverage to be *emergent* rather than scripted. `kinematic_math.py` (pure, Isaac-free) stays as fallback/tests.
- **The procedural-USD farm keeps its job.** `farm_builder.py` remains the **Slice-0 default and the analysis ground truth** — it owns panel IDs, the `pv:` schema, and fault injection. The neural-reconstructed twin is added as a **parallel appearance layer**: drone-survey → COLMAP + 3DGUT → splat scene → **referenced** (non-destructively) into a Cesium-georeferenced stage, with kinematic turbines/birds/robots composited over the static reconstruction and rendered through Sensor RTX. Procedural and neural coexist in the same USD; the state model is unchanged.
- **Isaac-free boundary holds.** Everything above keeps `omni`/`isaacsim`/`pxr` inside `world/`, `transport/sim_native.py`, `transport/ros2_bridge.py`, and the new Pegasus-backed control impl — orchestrator, perception/base, transport/base stay importable without Isaac, per CLAUDE.md.

## Phased roadmap

Each slice is **one thin end-to-end thread**, not a layer built in isolation. This is explicitly **multi-quarter**.

- **Slice 0 (done / current):** procedural farm, fixed panel rendering just verified, kinematic teleport drone, stub perception → VLM verdict → USD write. Frames → verdict is **open-loop**. *Spark-local.*
- **Slice 1 — Reason on the real feed, unblocked:** wire **Cosmos Reason 2 (2B/8B, vLLM/FP8)** as the `Perception` impl on the actual RTX camera frames, confirming the sm_121 vLLM path. Measure baseline verdict accuracy on healthy/hotspot/soiled. *Spark-local. (Partly in flight per recent commits.)*
- **Slice 2 — physics that bites, one drone:** replace kinematic control with **Pegasus/PX4 SITL**; add `omni.physx.forcefields` wind + one **articulated turbine** with a swept-disk keep-out. Thread: drone flies a coverage pass under gust, holds station, avoids the keep-out, judges a panel. *Spark-local (⚠ Pegasus-on-aarch64 smoke test is a gating risk).*
- **Slice 3 — the false-fault loop:** add **sweeping blade shadows** + motion blur through Sensor RTX/`ovrtx`; measure Reason's **false-fault rate** when a shadow crosses a healthy panel. Build the first graded scenario in `configs/`. *Spark-local rendering.*
- **Slice 4 — scenario factory (first burst-out):** stand up the **Cosmos Transfer / Data Factory Blueprint + OSMO** pipeline off-box to fan the seeded scene into dust/haze/low-sun/blade-shadow/bird variants; Evaluator filters implausible frames; use the corpus to harden Reason. *Burst-out (RTX PRO 6000 / DGX / cloud); Spark seeds and consumes.*
- **Slice 5 — trained flight policy:** train an **Isaac Lab RL policy** for station-keeping + gust rejection under domain-randomized wind and procedural graded terrain; add birds + terrain grade + parametric turbine wake. Gate on closed-loop KPIs. *Training burst-out; closed loop runs on Spark.*
- **Slice 6 — the real site:** **drone-survey → NuRec/3DGUT reconstruction → Cesium-georeferenced USD**, composited with kinematic turbines/birds and rendered through Sensor RTX. The twin now *looks like our site*. *Reconstruction burst-out (unless CUDA-13/aarch64 3dgrut verified); rendering Spark-local.*
- **Slice 7 — fleet + coverage brain:** add the **ground bot**; **cuOpt** plans battery/time-window coverage across both robots; **Mission Dispatch/VDA5050** dispatches. *Spark for cuOpt + interactive loop (⚠ Mission Dispatch containers not yet Spark-supported — may need off-box or a Jetson).*
- **Slice 8 — deploy bridge:** swap `Transport` to the ROS 2 bridge, `Perception` to **Isaac ROS + on-Thor Cosmos Reason**; validate against the same twin scenario suite; SIL→HIL before real flight. *Spark prototype → Jetson Thor/Orin on metal.*

## Honest constraints & risks

- **Spark (GB10 / sm_121 / CUDA 13) hard limits.** **Cosmos Transfer is not supported on the Spark** — all heavy generation is off-box, non-negotiable. The **Cosmos Reason NIM crashes on sm_121** — served via mainline vLLM instead (the near-term unblock; ⚠ verify Reason 2-2B/8B FP8 under vLLM ≥ 0.11 on GB10). **Isaac ROS supports the Spark since 4.2, but Mission Dispatch/Database containers do not** — the fleet-command loop may not prototype cleanly on-box. Reported **ROS 2 sensor-rendering quirks** are why sim-native is the default Transport; don't trust sim-side ROS 2 image topics without verification.
- **Version tension to resolve first.** CLAUDE.md pins **Isaac Sim 5.1 (source build)** while the brief says **6.0.1** (6.0 GA'd June 2026). The NuRec render path, Sensor RTX/`ovrtx` API, and **Pegasus (built for 5.1)** compatibility all differ across this boundary. **Confirm the installed build before scripting any of the above** — this is a cross-cutting gate, not a footnote.
- **What's genuinely frontier / research.** **Online WAM-in-the-loop planning** ("imagine the drone's next state under wake, then act") is research-grade — long-horizon physical fidelity and GB10 latency aren't there; use it only as an experiment behind the interfaces, never a dependency. **Turbine-wake fidelity** is a knowing trade: a parametric velocity-deficit field, not CFD — good enough to stress the controller and planner, **not** to certify a real flight envelope. NVIDIA WFMs model visual plausibility, not honest aerodynamics — do not trust "imagined" flight outcomes. Cosmos 3 (public but very new) and Cosmos 3 Edge on sm_121 are unverified; the **mature 2.x recipe line is the safer near-term build target**.
- **Data-integrity risk.** Generative augmentation (Transfer, NuRec Harmonizer) must not **hallucinate panel detail or geometrically-wrong shadows** that poison fault ground truth — this is the exact false-hotspot failure we're trying to prevent. Gate generated frames through Cosmos Evaluator and keep the procedural farm as authoritative ground truth.
- **The boil-the-ocean risk — the biggest one.** Six pillars across many quarters can stall if attempted breadth-first. **Mitigation: thin end-to-end thread first.** Every slice above is one working path through all relevant layers; fidelity deepens *along* a working loop, never as a big-bang integration. The interfaces + USD-as-truth are what make this safe — each upgrade is a swap, not a rewrite.

## Recommended next steps

1. **Resolve the Isaac Sim version now (5.1 vs 6.0.1)** on the actual Spark, and reconcile CLAUDE.md. Everything downstream (NuRec path, Sensor RTX/`ovrtx`, Pegasus compatibility) branches on this — it is the cheapest, highest-leverage unblock.
2. **Confirm the Cosmos Reason 2 vLLM path on GB10** (2B/8B, FP8, vLLM ≥ 0.11) as a drop-in behind the existing `cosmos_reason.py` skeleton, and measure baseline verdict accuracy on healthy/hotspot/soiled — this closes the Slice-1 thread and validates the sm_121 workaround.
3. **Smoke-test Pegasus Simulator v5.1.0 (PX4 SITL) on aarch64/GB10.** This is the single biggest feasibility gate for physics realism; if it fails on the Spark, the whole "physics that bites" pillar needs a rethink. Test early.
4. **Build the false-fault scenario as a reproducible config:** one articulated turbine + sweeping blade shadow + wind force field, rendered through Sensor RTX, scored on Reason's false-hotspot rate. This is the smallest thread that proves the physics-tests-body / world-model-tests-eyes thesis.
5. **Stand up the off-box burst path** (RTX PRO 6000 / DGX / cloud) with the Cosmos Transfer / Data Factory Blueprint + OSMO, seeded from one Spark render — establishing the on-box-loop / off-box-generation split as infrastructure before it's needed at scale.
6. **Verify a CUDA-13/aarch64/sm_121 `3dgrut` build** on the Spark; if it doesn't train on-box, formally designate reconstruction as burst-out and script the capture→COLMAP→3DGUT→USDZ pipeline against the **current** export format (not the deprecating NuRec USDZ path).
7. **Define the graded scenario suite + KPIs in `configs/`** (coverage %, false-fault rate, collision-free-flight rate, battery/time-window adherence) so that from here on every fidelity upgrade is measured against reproducible regression scenarios, not GUI one-offs.

---

# Part II — Detailed research streams

## Photoreal digital twin & neural reconstruction

The owner's brief — a photoreal, physically-honest, geo-referenced twin of a *real* solar site you can "see with your own eyes," built and validated before touching hardware — maps cleanly onto NVIDIA's current (2025–2026) Omniverse + Isaac Sim stack. This section lays out the scene spine, the real-to-sim capture path, sensor-accurate rendering, the factory-twin precedents, and where each piece runs given the DGX Spark constraint.

### OpenUSD + Omniverse as the scene spine
OpenUSD remains the single composition layer: terrain, panel cell-grids, turbines, robots, and reconstructed real-world geometry all live as USD prims and can be layered/referenced non-destructively. Our procedural farm (`farm_builder.py`) already emits USD; the graduation path is to *reference* a neural reconstruction of the real site into the same stage rather than replace the builder. Isaac Sim **6.0** (GA **June 4, 2026**, Kit 110) is the runtime; per its docs it renders 3D Gaussian Splat scenes through the **Fabric Scene Delegate** with multi-GPU support, letting splat scenes and conventional mesh/MaterialX content coexist and light each other ([radiancefields.com](https://radiancefields.com/nvidia-s-isaac-sim-6.0-ships-with-nurec-gaussian-splatting), [Isaac Sim 6.0 NuRec docs](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/assets/usd_assets_nurec.html)). ⚠ CLAUDE.md still says "Isaac Sim 5.1, built from source" while the brief says 6.0.1 — given 6.0 only reached GA in June 2026, confirm which build is actually installed, as the NuRec rendering path differs between 5.x and 6.0.

### NuRec + 3D Gaussian Splatting: capturing the real farm
**Omniverse NuRec** (Neural Reconstruction) is NVIDIA's suite for turning real camera/lidar footage into interactive, photoreal USD scenes via ray-traced 3D Gaussian Splatting, using their 3D Gaussian Unscented Transform (**3DGUT**) so splats render correctly under a physically-based ray-tracer with distorted/rolling-shutter cameras, not only rasterization ([developer.nvidia.com/omniverse/nurec](https://developer.nvidia.com/omniverse/nurec), [SIGGRAPH 2025 announce](https://radiancefields.com/nvidia-announces-nurec-libraries-at-siggraph)). A concrete, drone-friendly workflow:

**capture → COLMAP (structure-from-motion) → 3DGUT training → USDZ export → load in Isaac Sim.**

- **Capture:** overlapping frames (~60% overlap), multiple heights/angles, sharp focus, fast shutter, locked exposure — essentially a drone survey grid, so our inspection drone doubles as the capture rig.
- **COLMAP:** `feature_extractor` → `exhaustive_matcher` → `mapper` yields camera poses + a sparse cloud.
- **3DGUT training:** open-source [`nv-tlabs/3dgrut`](https://github.com/nv-tlabs/3dgrut); the `colmap_3dgut_mcmc.yaml` config is real, and MCMC densification helps thin structures (panel frames, mounting rails). ⚠ Note the repo warns its custom NuRec USDZ export "is going to be deprecated and replaced by ParticleField" — verify the current export format before scripting the pipeline.
- **Enhancement:** a **Harmonizer** model (built on Cosmos) corrects artifacts/lighting/shadow; an **Asset Harvester** extracts reusable objects from a reconstruction ([nurec page](https://developer.nvidia.com/omniverse/nurec)). Inputs follow the open **NCore** data standard.

**Where it runs — corrected:** 3dgrut's supported CUDA versions are **11.8 / 12.4 / 12.6 / 12.8 (default) / 13.0 (experimental)**, and the repo publishes a **linux/arm64 Docker build**. So the earlier claim that its only documented environment is "CUDA 11.8 + x86 + GCC ≤ 11" is outdated — on-box training on the Spark is not obviously excluded. That said, CUDA 13.0 is flagged **experimental** and sm_121/GB10 is untested here. ⚠ Verify a working CUDA-13/aarch64/sm_121 3dgrut build on the Spark before committing to on-box training; otherwise keep heavy reconstruction as a burst-out job (RTX PRO 6000 / DGX / cloud), consistent with the project's off-box rule. **Rendering** the finished splat scene runs on the Spark inside Isaac Sim 6.0.

**Quality/limits:** splats are photoreal for *appearance* but carry no collision geometry — pair them with a proxy mesh for physics, shadows, and floor alignment (normalization does not guarantee z=0). Dynamic elements (turning blades, birds, waving vegetation) reconstruct poorly from a static SfM pass — reconstruct the *static* site (terrain, panels, turbine towers) neurally, then composite **kinematic/simulated** blades, birds, and robots as conventional USD assets over it.

### Geo-referencing to a real site
**Cesium for Omniverse** (open source, Apache-2.0, free for commercial use) anchors the USD stage to the real WGS84 globe: a georeference prim plus a **Globe Anchor** on any prim pins it to true lat/long/altitude, and Cesium ion streams global terrain/imagery as 3D Tiles ([cesium-omniverse](https://github.com/CesiumGS/cesium-omniverse), [NVIDIA blog](https://developer.nvidia.com/blog/leverage-3d-geospatial-data-for-immersive-environments-with-cesium/)). This gives real **undulating terrain** and correct AGL-vs-absolute altitude references for drone flight policy. ⚠ Verify WGS84 anchoring precision at panel scale and whether streamed terrain resolution suffices versus our own drone-reconstructed terrain.

### Sensor-accurate rendering: RTX + Sensor RTX
Omniverse RTX sensor simulation is now packaged as the **`ovrtx`** C/Python library — physically-accurate camera/lidar/radar/semantic outputs from USD scenes ([NVIDIA-Omniverse/ovrtx](https://github.com/nvidia-omniverse/ovrtx), [NVIDIA blog](https://developer.nvidia.com/blog/integrate-nvidia-omniverse-rtx-sensor-simulation-into-existing-apps)). Encouragingly, ovrtx ships an **aarch64 wheel** (`manylinux_2_35_aarch64`), so GB10/Spark support is plausible at the packaging level. For solar-twin this is what makes the drone feed *honest* — sweeping blade shadows, glare/specular off panel glass, motion blur — so Cosmos Reason's false-fault behavior is testable in-twin. ⚠ Sensor RTX was early-access ("Omniverse Cloud Sensor RTX APIs") in 2025; confirm the current ovrtx release actually runs on sm_121/GB10.

### Precedents: build-and-validate-before-real
The **Mega Omniverse Blueprint** (CES Jan 2025) is exactly this playbook — testing robot *fleets* at scale in a factory/warehouse twin, on Sensor RTX, before real deployment. **KION Group with Accenture** were the first named adopters; a March 2025 expansion added Schaeffler, Foxconn, Pegatron and Kenmec, with Hyundai, Mercedes-Benz and others simulating humanoid/AMR fleets ([Mega blueprint blog](https://blogs.nvidia.com/blog/how-digital-twins-scale-industrial-ai/), [NVIDIA newsroom](https://nvidianews.nvidia.com/news/nvidia-omniverse-physical-ai-operating-system-expands-to-more-industries-and-partners)). (The broader Omniverse factory-twin roster of manufacturers is real but distinct from Mega's named adopters — don't conflate them.) NuRec is already wired into Isaac Sim and the AV simulators [AlpaSim](https://github.com/NVlabs/alpasim) and CARLA — the same real-to-sim loop AV teams use. **Mega is our template:** swap "warehouse + AMRs" for "solar farm + drone/ground-bot fleet."

### Fast environment/terrain generation: World Labs "Marble"
**Marble** (World Labs, GA **Nov 12, 2025**) generates persistent, editable 3D worlds from text/image/video/coarse-layout and exports **Gaussian splats, meshes (incl. collider meshes), or video** ([worldlabs.ai](https://www.worldlabs.ai/blog/marble-world-model), [TechCrunch](https://techcrunch.com/2025/11/12/fei-fei-lis-world-labs-speeds-up-the-world-model-race-with-marble-its-first-commercial-product/)). Use it for *speculative* surroundings or gap-filling before a real drone survey exists — never as the honest twin of the actual site (that's NuRec). Its splat export can drop into the same USD pipeline. ⚠ Verify commercial/industrial licensing terms and splat/mesh→USD import fidelity.

### What this means for solar-twin
Keep the seeded procedural farm as the Slice-0 default and the *analysis ground truth* (panel IDs, `pv:` schema, fault injection). Add a parallel path: drone-survey the real site → COLMAP + 3DGUT (on-box if a CUDA-13/aarch64 build checks out, else burst-out) → splat scene → **reference** it into a Cesium-georeferenced stage at true coordinates → composite kinematic turbines/birds/robots and render through Sensor RTX/ovrtx. Orchestration and the Perception/Transport/Control interfaces don't change — the twin's *appearance layer* graduates from procedural to neural-reconstructed while the state model stays authoritative in USD.

### ⚠ verify / open questions
- **Isaac Sim version:** CLAUDE.md (5.1 source build) vs brief (6.0.1); 6.0 GA'd June 2026, so confirm the installed build and its NuRec path.
- **On-Spark training:** 3dgrut lists CUDA 13.0 as experimental and ships an arm64 Docker build — confirm it actually trains on sm_121/GB10, or keep reconstruction burst-out.
- **Export format:** 3dgrut's NuRec USDZ export is slated for deprecation (→ ParticleField) — confirm the current target format.
- **Splat physics:** splats carry no collision; confirm the proxy-mesh workflow for traversability and drone collision.
- **Dynamics:** validate splat-static + mesh-dynamic compositing lights/shadows correctly in 6.0.
- **Sensor RTX / ovrtx on GB10:** aarch64 wheel exists — confirm runtime on sm_121.
- **Marble:** commercial licensing and splat/mesh→USD fidelity for industrial use.
- **NuRec Harmonizer:** built on Cosmos — confirm it runs off-box and doesn't hallucinate panel detail that corrupts fault ground truth.

---

## Cosmos World Foundation Models (Predict / Transfer / Reason)

The Cosmos WFM family is NVIDIA's open stack of "world foundation models" for Physical AI — the intended data/scenario factory and reasoning brain for solar-twin. Three functions matter to us: **Predict** (generate future world video), **Transfer** (sim2real / photoreal augmentation), and **Reason** (physical-AI VLM). As of mid-2026 these are converging into a single omni-modal line (**Cosmos 3**, now released — see below), but the 2.x models remain the most documented, recipe-supported option to build on today.

### Cosmos Predict — future/video world generation
Cosmos-Predict2.5 (released **Oct 6, 2025**) unifies Text2World, Image2World, and Video2World into one model built for Physical AI/robotics. It ships at **2B and 14B** scales (14B added in v1.4.0, **Dec 5, 2025**). Specialized/post-trained checkpoints exist: **auto/multiview** (AV), and robot **action-conditioned**, **multiview**, and **policy** variants (Robot/Policy on RoboCasa/LIBERO landed in v1.5.0, **Feb 24, 2026**). **Blackwell + ARM inference support** arrived in v1.3.3 (**Nov 26, 2025**) and HuggingFace **Diffusers** support in v1.4.1 (**Dec 19, 2025**) — relevant to whether the GB10 Spark can run the 2B variant locally at all (⚠ verify VRAM headroom). Claims of ~30 s / multi-view length, "flow-based" architecture, use of Cosmos-Reason1 for text grounding, and a ~200M-clip / RL-post-training corpus are **⚠ verify** (not confirmed in the release notes). Invocation is via the `nvidia-cosmos/cosmos-predict2.5` GitHub repo (inference + post-training) and HF checkpoints. [[predict2.5 repo](https://github.com/nvidia-cosmos/cosmos-predict2.5), [research page](https://research.nvidia.com/labs/cosmos-lab/cosmos-predict2.5/)]

For us, Predict is the engine for **rare-event / long-tail scenario video**: a bird crossing the drone's lane, a gust-induced camera jerk, a turbine blade sweeping a hard shadow across a panel row at low sun. The action-conditioned/policy variants are the path toward evaluating drone/ground-bot motion policies against generated futures.

### Cosmos Transfer — sim2real & environmental multiplication
Cosmos-Transfer2.5 (also **Oct 6, 2025**) is a multi-control conditional generation model on the Predict2.5 backbone with **four spatial control branches — edge, blur, segmentation, depth** — driven by cheap sim renders (e.g., Isaac Sim). It does **Sim2Real** (turn low-fidelity sim output into photoreal video) and real-to-real domain adaptation (re-light, re-weather, change backgrounds), with **~3.5× fewer parameters than Transfer-1** yet higher fidelity. A published cookbook covers CARLA/Isaac Sim SDG augmentation. (A distilled low-latency edge variant and Image2Image/ImagePrompt modes are referenced in the cookbook/changelog — exact availability dates ⚠ verify.) [[transfer2.5 repo](https://github.com/nvidia-cosmos/cosmos-transfer2.5), [cookbook recipe](https://nvidia-cosmos.github.io/cosmos-cookbook/recipes/inference/transfer2_5/inference-carla-sdg-augmentation/inference.html)]

Transfer is our **weather/dust/season/time-of-day multiplier**: take one seeded farm render and emit dusty, hazy, low-sun, dew-covered, and blade-shadow variants of the same panels — attacking the soiled/hotspot false-positive problem and the sim2real gap for Reason's judgments. **This is the workload the CLAUDE brief flags as Spark-incompatible** — a burst-out, off-box job (RTX PRO 6000 / DGX / cloud). The Spark prototypes the loop; heavy generation runs elsewhere (⚠ verify whether the distilled edge Transfer variant changes what's feasible on Blackwell/ARM — worth testing on GB10 before assuming it's strictly off-box).

### Cosmos Reason — the physical-AI VLM
Cosmos-Reason1 (7B, GTC 2025) was the original reasoning VLM behind our `Perception` interface: long chain-of-thought over spatial/temporal video, embodied decisions in natural language, offered as an **NVIDIA NIM**. Its successor, **Cosmos-Reason2**, was released **Dec 19, 2025** (not CES 2026) in **2B and 8B**, with a **32B** added **Apr 29, 2026**. For deployment, **vLLM ≥ 0.11.0** is recommended, with **FP8 (vLLM)** and **FP4/GGUF (llama.cpp)** quantized formats, plus Jetson tutorials (edge/ARM viability). Hardware notes: ~24 GB for 2B, ~32 GB for 8B; runs on Hopper/Blackwell. Corrections to the earlier draft: the context window is **~16K tokens for the 2B** (the "~256K" claim is wrong), and there is **no confirmed 2D/3D localization or trajectory-output capability** (⚠ verify — treat as unconfirmed). Note the repo now carries a banner that **Cosmos 3 supersedes Reason2 as of Jun 1, 2026.** [[Reason2 repo](https://github.com/nvidia-cosmos/cosmos-reason2), [build.nvidia.com](https://build.nvidia.com/nvidia/cosmos-reason2-8b)]

This matters directly: our memory note records the **Reason NIM crashing on sm_121 (GB10) → served via mainline vLLM**. Reason2's vLLM + FP8/2B path is the likeliest fix — evaluate Reason2-2B/8B on vLLM as a drop-in for the existing skeleton (no orchestration change). ⚠ verify vLLM support maturity: some sources still flag broader vLLM inference as "coming soon."

### Cosmos 3 — the convergence (now released)
Cosmos 3 shipped **Jun 1, 2026** — not just a report. It is an open **two-tower Mixture-of-Transformers**: an autoregressive **reasoner** tower and a diffusion-based **generator** tower that share layers via joint attention, jointly handling **text, image, video, audio, and action** for both understanding and generation — subsuming VLMs, video generators, world simulators, and world-action models. **Public checkpoints are available on HuggingFace** — **Nano** (16B: 8B+8B) and **Super** (64B: 32B+32B) — with **NIM microservices**, and an **Edge** variant (~4B, Jetson-class) announced. Strategically, Predict/Transfer/Reason are becoming facets of one model; practically, Cosmos 3 is very new, so the mature-recipe 2.x line is still the safer near-term build target while we evaluate Cosmos 3 Nano/Edge on the Spark. [[HF blog](https://huggingface.co/blog/nvidia/cosmos-3-for-physical-ai), [NVIDIA dev blog](https://developer.nvidia.com/blog/develop-physical-ai-reasoning-world-and-action-models-with-nvidia-cosmos-3/)]

### Physical AI Data Factory Blueprint + OSMO (scaling off-box)
Announced at **GTC 2026 (Mar 16, 2026)**, the **Physical AI Data Factory Blueprint** is an open reference architecture chaining **Cosmos Curator** (curate/annotate) → **Cosmos Transfer** (augment/multiply long-tail) → **Cosmos Evaluator** (score/validate physical accuracy; powered by Cosmos Reason, on GitHub), with **NVIDIA OSMO** orchestrating compute across heterogeneous clusters. Broad GitHub availability of the blueprint was **planned for April 2026** (⚠ verify current status). Early adopters include Uber, Skild AI, Hexagon Robotics, Teradyne, FieldAI, Linker Vision, and Milestone Systems. [[NVIDIA newsroom](https://nvidianews.nvidia.com/news/nvidia-announces-open-physical-ai-data-factory-blueprint-to-accelerate-robotics-vision-ai-agents-and-autonomous-vehicle-development)]

**What this means for solar-twin:** the blueprint is our exact off-box pipeline — seed one photoreal farm scenario on the Spark, then OSMO-orchestrate Transfer to fan it into dusty/hazy/low-sun/blade-shadow/bird-strike variants on RTX PRO 6000/DGX/cloud, with Evaluator filtering physically-implausible frames before they poison Reason's fault training. The Spark keeps the interactive loop (world + Reason inference + fleet policy); generation and training burst out. Reason2 on vLLM is the near-term unblock for our sm_121 NIM crash.

### ⚠ verify / open questions
- **GB10/sm_121 footprint:** can Predict2.5-2B, a distilled Transfer2.5 variant, or Cosmos 3 Edge/Nano actually run on the Spark (Blackwell+ARM claimed), or is generation strictly off-box? Test before committing.
- **Reason on sm_121:** confirm Reason2-2B/8B FP8 runs under vLLM ≥ 0.11 on GB10, and whether a Reason2 NIM (or Cosmos 3 NIM) now supports sm_121 and supersedes the workaround.
- **Cosmos 3 adoption call:** it's public (checkpoints + NIM), but new — does its action tower obsolete the separate Predict Robot/Policy path for our drone/ground-bot, and is it stable enough to skip the 2.x line?
- **Transfer licensing/asset terms** for commercial photoreal augmentation of a real customer site (NVIDIA Open Model License specifics — ⚠ verify).
- **Blueprint availability:** confirm the Data Factory Blueprint's current GitHub release state and whether OSMO runs on-prem or cloud-only.
- **Physical honesty of generated shadows:** does Transfer preserve geometrically-correct blade-shadow motion, or hallucinate it? Critical for not training false hotspot/soiling faults.

---

## World Action Models (WAMs) & foundation robot policies

### What a "world action model" actually is

NVIDIA now uses **world action model (WAM)** as an explicit term, and its definition is narrower than "any action-conditioned world model." Per NVIDIA's glossary, a WAM is "a type of AI model for robotics that learns both how the world is likely to change and what actions a robot can take to shape that change" — it works by "jointly learning to predict both future world states … together with the robot actions needed to influence the world," typically as a single end-to-end Joint Video-Action Diffusion Transformer trained on large-scale video ([NVIDIA glossary](https://www.nvidia.com/en-us/glossary/world-action-model/); [NVIDIA blog: *Pretrained to Imagine, Fine-Tuned to Act*](https://developer.nvidia.com/blog/pretrained-to-imagine-fine-tuned-to-act-the-rise-of-world-action-models/)). NVIDIA frames this as a shift *away from* "head-heavy" Vision-Language-Action (VLA) models — which map observations + instructions to actions without explicitly modeling physical dynamics — *toward* models that internalize physics, motion, and interaction from video. This distinguishes a WAM from two neighbours:

- **Passive world models** (Text/Image/Video→World generation) forecast how a scene *will* evolve but are not conditioned on the agent's action — you can't ask "what if the drone yaws left into the gust?" and get a counterfactual.
- **VLMs** (e.g. Cosmos Reason, the perception brain already wired into solar-twin) *judge* an observation — "is this panel soiled?" — but don't roll the world forward under an action. Reasoning, not simulation.

NVIDIA positions Cosmos as the foundational infrastructure for building WAMs ([nvidia.com/en-us/ai/cosmos](https://www.nvidia.com/en-us/ai/cosmos/)). *The exact marketing claim that Cosmos can "predict multiple approaches, evaluate outcomes in a closed loop, and converge on the right behavior without real-world risk" is ⚠ verify — treat it as vision, not a benchmarked capability.*

### The frontier landscape (2025–2026)

**Cosmos 3** — announced June 1, 2026 at Computex / GTC Taipei (weights on Hugging Face from ~May 31), described as the first fully open **omnimodel** for Physical AI, built on a **mixture-of-transformers** architecture that combines vision reasoning, world generation, and action prediction in one system across text, image, video, ambient sound, and actions ([NVIDIA newsroom](https://nvidianews.nvidia.com/news/nvidia-launches-cosmos-3-the-open-frontier-foundation-model-for-physical-ai); [Cosmos 3 technical report PDF](https://research.nvidia.com/labs/cosmos-lab/cosmos3/technical-report.pdf)). It ships as **Cosmos 3 Super** (post-training for robotics/AV), **Cosmos 3 Nano** (fast video + action reasoning), and **Cosmos 3 Edge** — a lightweight world model for on-device robotics that launched ~mid-July 2026 (so it is *no longer* "coming soon"). *Reported parameter sizes (~32B Super / ~8B Nano / ~4B Edge) are ⚠ verify.* Cosmos 3 largely subsumes the earlier split line (Predict / Transfer / Reason), though "Reason"-style reasoning persists as a mode/capability. Spark constraints from CLAUDE.md still apply: **Cosmos Transfer-class generation is off-box**, and the Reason NIM crashes on sm_121 (served via vLLM). *Cosmos 3's aarch64/sm_121 support is ⚠ verify; the on-device Edge variant is the most likely candidate to run locally, but this is unconfirmed.*

**Cosmos Policy** — a distinct, recent release: a robot-control policy that post-trains **Cosmos Predict-2** for manipulation, directly encoding robot actions and future states, with a diffusion formulation for multimodal outputs; NVIDIA reports SOTA on the LIBERO and RoboCasa benchmarks and calls it an early step toward adapting WFMs for control ([The Robot Report](https://www.therobotreport.com/nvidia-adds-cosmos-policy-world-foundation-models/); [Hugging Face](https://huggingface.co/blog/nvidia/cosmos-policy-for-robot-control)).

**"Playable"/interactive world models** — real-time, drivable neural simulators — continue to advance, but for robotics they remain **research-grade**: long-horizon physical consistency, controllability, and latency are not yet at the level where you'd trust one as a *primary* planner. *(⚠ verify any specific "playable model" product claim — this space is noisy and full of unofficial coverage.)*

### NVIDIA's robot foundation policies

- **Isaac GR00T** — open humanoid foundation policies: **GR00T N1** (GTC, March 2025), **N1.5** (mid-2025), **N1.6** (early 2026), the latter using a sim-to-real workflow and leveraging Cosmos Reason to turn instructions into plans ([Tag: GR00T](https://developer.nvidia.com/blog/tag/gr00t/)). *Exact N1.5/N1.6 dates are ⚠ verify — coverage varies (CoRL 2025 vs. a Jan 2026 blog).*
- **GR00T-Dreams / DreamGen** — the flagship "world-model-as-data-factory": prompt a WFM with an image + instruction, generate synthetic "dream" video, and extract **neural trajectories** via an inverse-dynamics model to train policies. It fine-tunes **Cosmos Predict-2** and filters with **Cosmos Reason**. Verified figures: **780K synthetic trajectories in 11 hours** (≈6.5K hours / ~9 months of human demo), and NVIDIA used the blueprint to develop **GR00T N1.5 in ~36 hours vs. nearly three months** of manual collection ([DreamGen paper, arXiv 2505.12705](https://arxiv.org/abs/2505.12705); [NVIDIA developer blog](https://developer.nvidia.com/blog/enhance-robot-learning-with-synthetic-trajectory-data-generated-by-world-foundation-models)). This is the *mature, shipping* use of WAM-adjacent tech: **offline training-data generation**, not online planning.
- **GR00T-Gen** — the Omniverse + Cosmos workflow that expands datasets via domain randomization and 3D upscaling, feeding Isaac Lab ([Isaac GR00T blueprint blog](https://blogs.nvidia.com/blog/isaac-gr00t-blueprint-humanoid-robotics/)).
- **Isaac Lab** — the GPU-parallel RL/IL training framework where these datasets and policies are trained ([developer.nvidia.com/isaac/lab](https://developer.nvidia.com/isaac/lab)). *Version note: **3.0.0 exists as a beta** (3.0.0-beta, ~July 2026); **2.3.0** is the current stable release, built on Isaac Sim 5.1 — so CLAUDE.md's "3.0" pin is plausible but points at a beta. Confirm the actual installed version ([releases](https://github.com/isaac-sim/IsaacLab/releases)).*

### What this means for solar-twin

Split WAM use into two honest tiers. **(A) Data factory — realistic today, off-box.** The GR00T-Dreams / GR00T-Gen pattern maps cleanly onto our hazards: seed a WFM with real/twin frames + prompts to synthesize the *rare, dangerous* conditions we can't safely fly — sweeping turbine-blade shadows on panels (the false-fault case), gust-induced motion blur, birds crossing frame — and use that corpus to harden the **Cosmos Reason perception** brain and any learned flight/coverage policy. This is a burst-out job (RTX PRO 6000 / DGX / cloud), consistent with our adopted rule. **(B) Online model-based planning — research-only for us today.** Using a WAM live to "imagine" the drone's next state under wind/wake before a station-keeping or clearance maneuver is the theoretical sweet spot, but long-horizon physical fidelity and latency (especially on GB10) aren't there yet. For flight stability under wind/turbulence, **train an RL policy in Isaac Lab with domain-randomized wind and procedural non-flat terrain** — a validated, deployable path — and treat WAM-in-the-loop planning as an experiment behind the existing swappable interfaces, not a dependency. Nothing here should churn the `Perception`/`Transport`/`RobotControl` contracts.

### ⚠ verify / open questions

- **Cosmos 3 on the Spark:** does any variant (Super/Nano/**Edge**) run on aarch64 / sm_121, or is it strictly off-box? Edge (on-device, single-GPU) is the candidate to test first.
- **Cosmos action-conditioning embodiments:** which embodiment classes are natively supported (arm/humanoid/vehicle appear documented) — **is a free-flying quadrotor with wind/wake a supported class, or would it need post-training?**
- **Wind/wake/turbulence physics:** does any NVIDIA WFM model aerodynamic disturbance honestly, or only visual plausibility? Likely the latter — validate before trusting imagined flight outcomes.
- **Latency budget:** Cosmos 3 Nano/Edge inference latency for closed-loop drone control on GB10 is unquantified.
- **Non-humanoid embodiments:** GR00T-Dreams / GR00T-Gen tooling is humanoid/manipulation-centric today — confirm support for a ground bot + drone before assuming reuse.

---

## AV / robotics sim-to-real training playbook

NVIDIA and the leading AV/robotics teams do not treat simulation as a demo — they treat it as the primary place where autonomy is *trained, tested, and signed off* before hardware moves. Below is that playbook, translated to a drone + ground-bot fleet inspecting solar panels near wind turbines.

### The reference stack: DRIVE Sim / Omniverse for AV
NVIDIA's AV simulator, **DRIVE Sim**, is built on **Omniverse** as a physically accurate, open platform for perception training, planning/control, and full-stack validation, scaling from one workstation to multi-GPU cloud ([developer.nvidia.com/drive/simulation](https://developer.nvidia.com/drive/simulation)). Its modern packaging is the **Omniverse Blueprint for AV Simulation** — a standardized, API-driven workflow (powered by Omniverse Sensor RTX APIs) for building digital twins, replaying real sensor data, and generating new ground-truth for **closed-loop testing** ([nvidianews.nvidia.com](https://nvidianews.nvidia.com/news/nvidia-expands-omniverse-with-generative-physical-ai)). For robotics, the same primitives live in **Isaac Sim** + **Isaac Lab** (open-source, GPU-parallel policy training) ([arXiv:2511.04831](https://arxiv.org/abs/2511.04831)).

### Closed-loop vs open-loop (the core distinction)
- **Open-loop / replay**: play back recorded or generated sensor data and score the stack's outputs. Cheap, good for perception metrics, but the world never *reacts*.
- **Closed-loop**: the policy acts → the world updates → sensors **re-render** → the policy acts again. NVIDIA's **OmniDreams** is a real-time generative world model that does this, autoregressively conditioning photorealistic sensor generation on past frames, current simulator state, and immediate driving actions ([arXiv:2606.03159](https://arxiv.org/abs/2606.03159); [research.nvidia.com/labs/sil](https://research.nvidia.com/labs/sil/projects/omnidreams-blog/paper.pdf)). Closed loop is the only way to catch compounding errors, recovery behavior, and interaction with dynamic agents.

**For solar-twin:** open-loop is our current stub-perception mission (frames → VLM verdict → USD). The vision needs closed-loop: the drone's controller reacts to gusts/wake, re-renders its camera, and a sweeping blade shadow must feed back into whether the next pass reads a false "hotspot." Our `Perception`/`Transport`/`RobotControl` interfaces are the right seam to run either loop without churning orchestration.

### Domain randomization & scenario generation at scale
Isaac Lab makes **domain randomization (DR)** first-class: physics params (friction, mass, armature, gravity) *and* rendering params (texture, material, lighting), most randomizable at runtime ([arXiv:2511.04831](https://arxiv.org/abs/2511.04831)). Coupled with curriculum mechanisms, DR is what enables **zero-shot** transfer, as in NVIDIA's Spot-quadruped locomotion and industrial-assembly work ([Spot](https://developer.nvidia.com/blog/closing-the-sim-to-real-gap-training-spot-quadruped-locomotion-with-nvidia-isaac-lab/), [assembly](https://developer.nvidia.com/blog/bridging-the-sim-to-real-gap-for-industrial-robotic-assembly-applications-using-nvidia-isaac-lab/)). *(Whether Isaac Lab ships a named "Automatic Domain Randomization" that auto-ramps difficulty with performance — as opposed to manual curriculum terms — is ⚠ verify.)* On top of DR, **Cosmos** world-foundation models generate scenario *coverage* beyond collected data; NVIDIA's Physical AI Dataset shipped ~40,000 Cosmos-generated clips ([nvidia.com/ai/cosmos](https://www.nvidia.com/en-us/ai/cosmos/)).

**For solar-twin:** randomize sun angle/time-of-day, soiling patterns, dust/haze, wind magnitude and gust profile, blade rotation phase, terrain grade, and bird trajectories. Randomize camera exposure/motion-blur so the VLM learns a *real* hotspot from a *swept blade shadow* — exactly the "false-fault" hazard called out.

### Closing the sim-to-real gap: concrete techniques
1. **Domain randomization** (above) — robustness over exact fidelity.
2. **Real-data anchoring / neural rendering.** **Omniverse NuRec** ingests real sensor data and reconstructs it as **3D Gaussian Splats** (also NeRF and 3DGUT) in OpenUSD, giving photoreal, interactive replicas; it is integrated into Isaac Sim ([developer.nvidia.com/omniverse/nurec](https://developer.nvidia.com/omniverse/nurec)). Mcity and CARLA have adopted NuRec-based reconstruction ([Mcity](https://mcity.umich.edu/photo-realistic-3d-models-mcity-nvidia-omniverse-nurec-of-the-mcity-test-facility-created-using-nvidia-omniverse-nurec/)).
3. **Generative augmentation (Cosmos).** The **Cosmos 3** family (unified Reasoner + Generator towers; **Nano 16B** / **Super 64B**) is the current mid-2026 generation ([developer.nvidia.com Cosmos 3](https://developer.nvidia.com/blog/develop-physical-ai-reasoning-world-and-action-models-with-nvidia-cosmos-3/)). Its predecessors demonstrated the techniques we want: **Cosmos-Transfer-1** conditions on sim's depth/segmentation/edge maps to re-render photoreal weather/lighting/terrain variants while preserving structure ([arXiv:2503.14492](https://arxiv.org/html/2503.14492v1)); prior **Cosmos Predict** generated future world states and **Cosmos Reason** added reasoning/safety-validation. *Exact current product naming (whether "Transfer/Predict/Reason" persist as separate SKUs under Cosmos 3) is ⚠ verify.*
4. **SIL → HIL.** Software-in-the-loop runs the full stack in sim; hardware-in-the-loop feeds rendered sensor data to the *real* compute and loops its decisions back. NVIDIA's AV HIL rig historically was **DRIVE Constellation** ([nvidianews, 2019](https://nvidianews.nvidia.com/news/nvidia-drive-constellation-now-available-virtual-proving-ground-for-validating-autonomous-vehicles)) — an older platform whose current branding/successor and any regulator (e.g. TÜV SÜD) usage are ⚠ verify.

**For solar-twin:** anchor the twin to the *real* site by reconstructing it with **NuRec** from drone footage (terrain, panel rows, turbines). Use Cosmos generative augmentation *off-box* — Cosmos Transfer is unsupported on the Spark — to expand weather/soiling variants for VLM robustness. Prototype the reasoning loop + fleet policies **on** the Spark; burst generation/training off-box per our adopted rule.

### Validation / eval-in-sim before deployment
The AV discipline: define a scenario suite (nominal + adversarial edge cases), run it **closed-loop at scale**, and gate deployment on measured KPIs — coverage, disengagements, collision/near-miss rates — before the vehicle touches a road ([nvidia.com AV simulation](https://www.nvidia.com/en-us/use-cases/autonomous-vehicle-simulation/)). Simulation increasingly feeds *regulatory* validation, not just internal QA.

**For solar-twin:** build a graded scenario suite in `configs/` — gusts, turbine wake near a tower, sweeping blade shadows, birds crossing, panels on grade — and gate on inspection coverage %, false-fault rate, collision-free-flight rate, and battery/time-window adherence *in the twin* before any real flight. Because the USD stage is source of truth and everything is script+config driven, these become reproducible regression scenarios, not GUI one-offs.

### ⚠ verify / open questions
- **OmniDreams** licensing and **GB10/sm_121** support: paper (arXiv:2606.03159) and `nvidia/omni-dreams-models` weights exist on Hugging Face, but on-Spark runnability is ⚠ verify.
- **NuRec on aarch64/Spark**: confirm 3DGS reconstruction *and* the interactive render path run on GB10, or whether reconstruction must be off-box with only the USD consumed on-Spark.
- **Cosmos version drift**: Cosmos 3 (Nano 16B / Super 64B) is current; confirm which components map to our stub-replacement perception and which remain burst-out. Cosmos Transfer confirmed unsupported on Spark.
- **HIL for robot compute**: DRIVE Constellation is AV-specific and dated — verify the equivalent Jetson/robot-compute HIL story for our flight/ground controllers.
- **Isaac Sim version**: brief says 5.1 (built), task says 6.0.1; public releases seen are 5.x — confirm the installed build and that NuRec/Sensor RTX APIs match it before trusting remembered snippets.
- **Aerodynamics fidelity**: Isaac Sim/PhysX does not natively model rotor wake or gust turbulence — verify whether Pegasus/PX4 SITL or an external wind-field/CFD source is needed to make the "wind + wake pushes the drone" loop physically honest.

---

## Physics realism for the hazards (wind, turbines, terrain, birds, collision)

This layer decides how honest the twin is once the drone stops teleporting. It splits cleanly into what PhysX/Isaac Sim gives us out of the box, what Pegasus adds for flight, and what we must author ourselves (wake fields, keep-out volumes, route logic).

### Flight dynamics: Pegasus Simulator v5.1.0 (PX4 SITL)

Pegasus Simulator **v5.1.0** shipped 2025-10-26 and was developed and tested against **Isaac Sim 5.1.0**; the maintainers state it is **not compatible with older Isaac releases** ([Pegasus docs](https://pegasussimulator.github.io/PegasusSimulator/), [GitHub](https://github.com/PegasusSimulator/PegasusSimulator)). It was tested on **Ubuntu 22.04 LTS + NVIDIA driver 550.163.01** with **PX4-Autopilot v1.14.3**; only **multirotor** topologies are supported, with PX4 SITL and ROS 2 integration plus mag/GPS/barometer sensors (the ArduPilot interface was not tested this release). Launching uses an **`isaac_run` helper that wraps the Isaac Sim Python interpreter** (`ISAACSIM_PYTHON`/`ISAACSIM`) for standalone vs. GUI runs — a wrapper, not a wholesale replacement of the old entry point. Its dynamics stack exposes a `QuadraticThrustCurve` thruster model and linear-drag aerodynamic components ([API reference](https://pegasussimulator.github.io/PegasusSimulator/source/api/index.html)). This is where the drone graduates from kinematic teleport: real PX4 attitude/position loops run in the loop, so station-keeping error, motor saturation and battery-limited flight time become emergent rather than scripted.

⚠ verify: Pegasus 5.1.0 was validated on x86_64 + driver 550; **aarch64/GB10 (sm_121, CUDA 13) is unproven**. PX4 SITL itself is CPU-side (fine on ARM), but the Isaac build compatibility and any Warp-side kernels need a smoke test on the Spark.
⚠ verify: Pegasus targets **Isaac Sim 5.1**, whereas the twin has also been described as running on **Isaac Sim 6.0.1** — confirm which Isaac build we are standardizing on before committing to Pegasus, since 6.x support is not stated by the project.

### Wind and gusts

Pegasus does not expose a first-class atmospheric-wind model beyond its drag terms, so wind is best injected at the PhysX layer via the **`omni.physx.forcefields`** extension. The **Wind** force field pushes rigid bodies with a tunable drag/coupling rate, `averageSpeed`, `speedVariation` (random speed magnitude — our gust term) and `directionVariation` ([Wind node docs](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/latest/_build/ogn/docs/omni.physx.forcefields/OgnForceFieldWind.html)); companion Drag and Noise fields let us layer turbulence. These are driven through a PhysX force-field schema, settable from Python/USD. ⚠ verify the exact schema/API name and enable steps against the installed 5.1 build. Known gotcha: applying a force field to an **articulation link throws "link must be in a scene"** unless attached correctly ([forum](https://forums.developer.nvidia.com/t/wind-force-field-physx-error-pxarticulationlink-articulation-link-must-be-in-a-scene/310921)) — relevant because the drone is an articulation.

### Turbine wake / rotor-wash

There is no CFD here, and we should not pretend otherwise. The feasible approach is a **parametric velocity-deficit / actuator-disk approximation** authored as a localized custom force field (or a scripted per-step external force on the drone body), parameterized by turbine thrust coefficient, rotor diameter and downstream distance — a Jensen/Gaussian wake profile driving `averageSpeed`+`speedVariation` in the affected volume. This captures the right *behavior* (mean push + added turbulence intensity that grows in the near-wake and decays downstream) but not true unsteady vortex shedding; blade-passing frequency effects and tip vortices are out of scope without off-box CFD. Honest limit: good enough to stress the flight controller and coverage planner, not to certify a real flight envelope. ⚠ verify against reference turbine data.

### Articulated turbines (collision + moving keep-out)

Model each turbine as a USD articulation: a driven revolute joint spinning the blade assembly, with convex or mesh colliders on tower, nacelle and blades. This gives real collision against the drone and a time-varying swept volume. For planning, don't rely on instantaneous collision alone — precompute the blade swept-disk as a **static conservative keep-out** (a no-fly cylinder/annulus) so the planner never threads between blades. The rotating blades also cast **sweeping shadows** on panels — exactly the false-fault case the perception stack must be robust to, so a physics feature that doubles as a data-generation asset.

### Terrain and ground-bot traversability

Isaac Lab's terrain generators produce **heightfields converted to triangle meshes** (`convert_heightfield_to_trimesh`) for PhysX collision, with primitives including `sloped_terrain`, `pyramid_sloped_terrain`, `random_uniform_terrain`, `stairs_terrain` and `wave_terrain` ([Isaac Lab terrains](https://isaac-sim.github.io/IsaacLab/main/source/api/lab/isaaclab.terrains.html)). For a real undulating site, import a DEM/photogrammetry mesh as the collision surface. Ramp testbeds (5/10/15/20°) are a standard Isaac pattern for probing traversability and directly reusable to set the rover's max-grade envelope. ⚠ verify: trimesh terrain has a documented PhysX mesh-cooking / fall-through failure mode ([#2323](https://github.com/isaac-sim/IsaacLab/issues/2323), reported and now **closed**) — validate contact behavior on our specific DEM.

### Birds (dynamic obstacles) and geofencing

Birds are moving rigid-body agents on scripted/randomized trajectories; detection + avoidance is a perception+planner concern layered on the RTX camera and any depth sensor. No-fly volumes (around turbines, off-site) are best expressed as USD invisible-collider zones for the sim plus explicit altitude/polytope constraints in the planner; a PX4 geofence can enforce a hard backstop.

### Route optimization: cuOpt

**NVIDIA cuOpt** is the GPU routing/optimization solver for coverage and route planning; it was **open-sourced under Apache 2.0 in June 2025** ([NVIDIA blog](https://developer.nvidia.com/blog/accelerate-decision-optimization-using-open-source-nvidia-cuopt/), [GitHub](https://github.com/NVIDIA/cuopt)). Current docs are at **26.02** (prior **25.12 / 25.05**), and it solves **CVRPTW and PDPTW** with time windows, vehicle capacity, breaks and max travel-time/distance limits ([routing features 26.02](https://docs.nvidia.com/cuopt/user-guide/26.02.00/routing-features.html)). We map inspection waypoints → nodes, drone battery → capacity/range + return-to-charge, and daylight/low-wind windows → time windows; keep-out volumes shape the cost/feasibility matrix. ⚠ verify: cuOpt is VRP-centric, **not a native 3D coverage-path planner** — we generate coverage waypoints upstream, then hand cuOpt the routing problem; confirm the aarch64/GB10 build.

### What this means for solar-twin

Everything except turbine-wake CFD is **buildable on the Spark now**: Pegasus PX4 SITL for real flight dynamics, PhysX force fields for wind/gusts, articulated turbines for collision + moving shadows, heightfield/DEM terrain, and cuOpt for battery/time-window routing. The kinematic drone graduates to Pegasus the moment we need station-keeping, gust rejection or battery-limited coverage to be emergent. Wake stays a tunable parametric field — the one place we knowingly trade fidelity for feasibility until off-box CFD is available.

### ⚠ verify / open questions
- Pegasus 5.1.0 on aarch64/GB10 (sm_121, CUDA 13) — untested; confirm the Isaac build + PX4 SITL run on the Spark.
- Isaac target version: Pegasus is built for Isaac 5.1; reconcile with any Isaac 6.0.1 plan.
- Force fields on the drone **articulation** — reproduce/avoid the "link must be in a scene" error; confirm the 5.1 schema/API.
- Parametric wake fidelity vs. reference turbine data — near-wake turbulence intensity is a guess without CFD.
- Trimesh↔robot collision cooking on a custom DEM — validate contacts and slope limits.
- cuOpt aarch64/GB10 build, plus an upstream coverage-path generator feeding it.
- Bird agents: procedural trajectories vs. a recorded/learned motion set for realistic avoidance testing.

---

## Real-robot perception, navigation & fleet autonomy (deploy path)

This is the bridge from the twin to metal: the same interfaces (`Perception`, `Transport`, `RobotControl`) that today wrap Isaac Sim ground truth get re-implemented on **Isaac ROS** running on an on-robot Jetson, while orchestration and the USD source-of-truth stay untouched. The stack below is what NVIDIA ships in 2025–2026 for exactly this transition.

### GPU perception, localization & mapping — lidar-free (Isaac ROS)
**Isaac ROS 4.5.0** (July 6 2026) is NVIDIA's hardware-accelerated ROS 2 suite, on a roughly monthly cadence (4.0.0 Oct 2025 → 4.5.0) ([release notes](https://nvidia-isaac-ros.github.io/releases/index.html)). It targets **ROS 2 Humble**; some package docs now reference **Jazzy** (⚠ verify the exact distro for the packages you deploy). The pieces relevant to solar-twin:

- **cuVSLAM** (`isaac_ros_visual_slam`, cuVSLAM 11 as of mid-2026) — GPU stereo-visual-inertial odometry/SLAM with a multi-camera localization mode. The lidar-free pose source for both robots.
- **nvblox** (`isaac_ros_nvblox`) — real-time 3D reconstruction from depth + pose, sliced into a **2D costmap for Nav2** via a costmap plugin; supports multi-camera fusion and 3D-lidar input ([nvblox repo](https://github.com/NVIDIA-ISAAC-ROS/isaac_ros_nvblox)). nvblox explicitly lists **DGX Spark** as a supported platform.
- **Isaac Perceptor** — the packaged reference workflow that fuses a **Nova** multi-camera rig (cuVSLAM + nvblox) into a 3D map with no lidar required. Nav2 publishes a "[Lidar-Free, Vision-Based Navigation](https://docs.nav2.org/tutorials/docs/using_isaac_perceptor.html)" tutorial built on it.
- **NITROS** — the zero-copy, type-adapted transport that keeps GPU tensors on-device between nodes (4.5.0 sunset the GXF backend and added CUDA-streaming support). It is what makes detection/pose graphs run at frame rate on embedded GPUs.

Maturity: cuVSLAM, nvblox, NITROS and Perceptor are production-track; the vision-only path is well-supported, which matters for a low-cost outdoor fleet.

### Ground-bot navigation on terrain — Nav2
Nav2 consumes the nvblox costmap for local planning and obstacle avoidance. On non-flat ground the honest gap is 3D/gradient-aware traversability: standard Nav2 costmaps are 2D and slope-aware planning is emerging rather than turnkey. nvblox supplies the raw height/voxel data, but terrain-cost tuning is bespoke. Birds and moving blade shadows are handled at the costmap layer, but shadow-vs-obstacle disambiguation is a perception problem the twin should train against, not something Nav2 solves alone.

### Drone autonomy
NVIDIA does not ship a "drone Nav2." The realistic 2025–2026 stack is a **PX4** autopilot + a companion Jetson running Isaac ROS perception (cuVSLAM for GPS-degraded pose, nvblox for a 3D avoidance volume), bridged over ROS 2. In sim this is **Pegasus Simulator v5.1.0** (Oct 26 2025, Isaac Sim 5.1, PX4 v1.14.3) ([Pegasus](https://github.com/PegasusSimulator/PegasusSimulator)); community flight-avoidance projects (e.g. PX4 avoidance stacks) fill the loop (⚠ verify currency/maintenance). Wind/gust/wake, station-keeping over a panel, and altitude reference (AGL vs absolute over graded terrain) are policy/controls problems to prototype in the twin — no NVIDIA product does this out of the box (⚠ verify).

### Fleet command — VDA5050 over MQTT
For multi-robot command NVIDIA open-sourced **Isaac Mission Dispatch** (cloud/edge micro-service) and a ROS 2 **Mission Client**, speaking the **VDA5050** fleet standard over **MQTT** ([mission_dispatch](https://github.com/nvidia-isaac/isaac_mission_dispatch)). Dispatch assigns/monitors missions; each robot runs a client that reports state and executes Nav2 actions. The repo lineage is genuinely in flux — current repos include `isaac_ros_mission_client`, `isaac_mission_dispatch`, `isaac_mission_control`, and the newer `isaac_ros_cloud_control` — so **confirm which is canonical before committing** (⚠ verify). VDA5050 is AMR-centric (ground fleets); using it to also command drones is a stretch of the spec — treat the drone as a custom VDA5050 device or a parallel channel (⚠ verify).

### Routing / coverage brain — cuOpt
**NVIDIA cuOpt** is open source under **Apache 2.0** — announced as an intent at GTC (Mar 18 2025) with the code released mid-2025 ([open-source blog](https://developer.nvidia.com/blog/accelerate-decision-optimization-using-open-source-nvidia-cuopt/), [GitHub](https://github.com/NVIDIA/cuopt)). It is a GPU solver for **VRP, LP, and MILP** with Python/REST/CLI. For solar-twin it is the coverage-and-route planner — which robot inspects which rows under **battery, time-window, multi-depot, and capacity** constraints — with a claimed ~240× dynamic-routing speedup. cuOpt produces the ordered task list that Mission Dispatch then dispatches.

### Edge compute on the real robots
**Jetson AGX Thor** (Blackwell, 128 GB, up to ~2,070 FP4 TFLOPS, 40–130 W) reached general availability **Aug 25 2025**; dev kit ~$3,499 ([hardware page](https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-thor/)), with T5000 module pricing reported around ~$2,999/1k units (⚠ verify). Thor's ~7.5× the AI compute of Orin is what lets a VLM-class reasoner (Cosmos Reason) run *on the robot* alongside Isaac ROS. **Jetson Orin** remains the mature, cheaper option where a full on-board VLM isn't needed. Note: **Thor is a supported Isaac ROS target since 4.0.0**, and **DGX Spark since 4.2.0** — but the release notes flag that **Mission Dispatch / Mission Database containers are not yet supported on DGX Spark**, which directly affects prototyping the fleet-command loop on-box.

### Closing the operational loop
Real-world telemetry — **SCADA** inverter/string data, on-site **weather** (wind, irradiance), and **IV-curve** measurements — flows back as evidence that confirms or overturns a visual verdict, updating `pv:state`, `pv:iv_yield`, and `pv:rul_days` on the USD prims via the same `FaultReport` payload. Field outcomes retrain perception and re-weight cuOpt priorities (inspect suspect strings first).

### What this means for solar-twin
The deploy path is interface-compatible with today's Slice 0: swap the sim-native `Transport` for a ROS 2 / VDA5050 bridge, swap ground-truth `Perception` for Isaac ROS + on-Thor Cosmos Reason, and let cuOpt feed Mission Dispatch — the orchestrator is untouched. Prototype the loop on the **Spark** (nvblox/Isaac ROS support it) and burst heavy generation/training off-box. Mature blocks: cuVSLAM/nvblox/NITROS/Perceptor, cuOpt, Mission Dispatch. Emerging/bespoke: 3D terrain traversability, drone wind/avoidance policy, and drone-over-VDA5050.

### ⚠ verify / open questions
- **Isaac ROS on the Spark's GB10 (sm_121, CUDA 13):** supported since 4.2.0, but **Mission Dispatch/Database containers are not** — plan around that, and reconcile with the repo's known **ROS 2 sensor-rendering quirks** before trusting sim-side ROS 2 image topics.
- **ROS 2 distro** (Humble vs Jazzy) for the specific Isaac ROS packages you deploy.
- **Drone over VDA5050 / Mission Dispatch** — AMR-centric spec; confirm feasibility or plan a parallel drone channel.
- **Canonical mission repo** among `isaac_ros_mission_client` / `isaac_mission_dispatch` / `isaac_mission_control` / `isaac_ros_cloud_control`.
- **Slope/traversability-aware Nav2** — how much custom costmap work is needed beyond nvblox's 2D output.
- **On-Thor Cosmos Reason latency/throughput** at inspection frame rates (recall the NIM crashes on sm_121; verify the vLLM path ports to Thor).
- **cuVSLAM robustness** over repetitive panel rows (visual aliasing) and in GPS-degraded, wind-buffeted flight.
- **Jetson T5000 module pricing** and exact Thor SKU for the robot BOM.
