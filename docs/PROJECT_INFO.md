# PROJECT_INFO — solar-twin at a glance

> **Purpose.** One document a new contributor (human or Claude) can read start-to-finish
> and understand *what this project is, what it runs on, which technologies it uses and
> why, how the world is built, and where it's going.* It is a map, not a contract — when
> it and a locked doc disagree, the locked doc wins (see [Where truth lives](#where-truth-lives)).
>
> **Last synced:** 2026-07-24 · branch `cosmos-reason-live`. Anything version- or
> roadmap-specific is **⚠ verify** — this stack moves monthly.

---

## 1. What the project is (in one breath)

**solar-twin is an autonomous solar-farm inspection *digital twin*.** A fleet of robots —
a ground bot and drones — flies/drives an Isaac Sim reconstruction of a real solar farm,
a vision-language model judges each panel's health, and the verdicts are written back onto
the panel's USD prim. The end goal is a **closed maintenance loop**: inspect → diagnose →
decide → act, all rehearsed and signed off in simulation before a real robot ever launches.

The bet, stated plainly in `docs/specs/01-scope-and-vision.md`:

> A robot fleet can inspect a solar farm autonomously and reliably — **but only if the
> autonomy is trained, tested, and signed off in a digital twin first**, the same
> discipline NVIDIA's own self-driving (DRIVE Sim) and factory-fleet (Mega Blueprint)
> teams use before hardware moves.

### Why a twin, not just a detector
A solar farm's fault surface is expensive to inspect by hand *and* expensive to get wrong:
a false fault sends a truck out for nothing; a missed fault loses yield or damages gear.
The hard problem isn't "can a model spot a hotspot in a clean photo" — it's **"does
perception still work while the robot is being pushed by wind, under a moving turbine-blade
shadow, in low sun, with motion blur."** That question can only be answered in a physically
honest simulated world, cheaply and repeatedly. Hence the twin.

### The owner's bar for "done"
A twin you can **see with your own eyes and trust** (from spec 01):
1. The stage shows *the actual site* — real terrain, real panel rows, real turbine towers —
   not a generic procedural field.
2. Turbine blades **turn** and cast moving shadows; wind **gusts**; terrain **undulates**;
   birds **cross** the drone's lane — and these are *physically consequential*, not
   cosmetic: they push the drone, occlude the camera, threaten collision.
3. A drone and a ground bot **operate the site autonomously** — coverage passes,
   station-keeping against wind/wake, keep-out avoidance, grade traversal. Cosmos Reason
   judges each panel; verdicts land on USD prims.
4. The operation is **de-risked in the twin first** — altitude-vs-resolution, coverage
   efficiency, battery/time-window feasibility, and especially the **false-fault rate**
   (a blade shadow must *not* become a logged hotspot) are measured against a graded
   scenario suite before a real drone flies.
5. Everything is **reproducible from a script + a seeded config** — scenarios are
   regression tests, not GUI one-offs.
6. Heavy generation/training **bursts off-box**; the DGX Spark runs the interactive loop.

> ⚠ **Do not scope this down to a toy sim.** The owner explicitly wants the full photoreal
> AV/factory-twin playbook. See `docs/DIGITAL_TWIN_VISION_AND_RESEARCH.md`.

---

## 2. The platform it runs on (know this first — it constrains everything)

| Fact | Value | Consequence |
|---|---|---|
| **Machine** | DGX Spark — GB10, **aarch64**, unified memory (~121 GB), GPU driver 580.142 | Not x86. Wheels/containers that assume x86 often don't exist. |
| **GPU compute capability** | **sm_121** | Bleeding edge — many prebuilt CUDA binaries stop at sm_120 and crash. This is the single biggest source of "it should work but doesn't." |
| **CUDA** | **13.0** (`nvcc` 13.0.88) | Satisfies the ≥13 golden rule; PyTorch must be a cu13 build. |
| **System Python** | 3.12.3 (`/usr/bin/python3`) | Runs the pure-python "Brain" half + tests. |
| **Isaac Sim** | **6.0.1-rc.7**, built from source at `/home/simulationhub/IsaacSim` (commit `045ca8b`) | Has its own bundled Python — `./python.sh`. **⚠ Note the version drift below.** |
| **ROS 2** | **Jazzy** (`/opt/ros/jazzy`, ros-base not desktop), verified working | A proven Transport seam, though sim-native is the default. |

**⚠ Version drift to keep in your head:** older docs (`STACK.md`, parts of the bible) say
"Isaac Sim **5.1**". The *installed build is 6.0.1-rc.7.* CLAUDE.md has been updated to
6.0.1. **Verify every Isaac API snippet and asset path against 6.0, not 5.1** — namespaces
and paths drift between releases. Treat any remembered 5.1 snippet as a hint, never truth.

### Two platform gotchas that shape the architecture
- **`usd-core` has no aarch64 wheel.** `pip install usd-core` fails on this box, so `pxr`
  is *only* available under Isaac Sim's bundled Python here — never in system Python/CI on
  the Spark. That's *why* `schema/pv_module.py` imports `pxr` lazily inside its USD
  functions: the pure-python contract tests without it.
- **Serving the VLM: the Cosmos Reason NIM does NOT run on GB10.** The official NIM
  container's bundled Triton/LLVM is compiled only through sm_120; on sm_121 it dies during
  vision-encoder profiling (`'sm_121' is not a recognized processor`). **Workaround that
  works: mainline vLLM built for sm_121a** (`vllm/vllm-openai:cu130-nightly`), serving the
  model's bf16 HF weights that the NIM pull already cached locally. Full command in
  `docs/ENVIRONMENT.md`. This is a known ecosystem-wide GB10 gap, not our bug.
- **Do NOT `apt upgrade` this box.** A blanket upgrade would bump CUDA/nvidia/docker under
  a source-built Isaac Sim + a live vLLM. Install scoped packages only.

---

## 3. The core architecture (how it doesn't become a rewrite)

### Everything hangs off three swappable interfaces
The whole design exists so that "six pillars of NVIDIA stack" can slot in over many
quarters **without ever churning the orchestrator**. Everything is coded against three
abstract base classes, never a concrete implementation:

| Interface | What it abstracts | Slice-0 impl (today) | Target impl |
|---|---|---|---|
| **`Perception`** | "look at a frame, judge the panel" (`assess`/`diagnose`) | `ground_truth.py` (stub, reads the truth from USD) | `cosmos_reason.py` (VLM, **wired**) → on-robot Isaac ROS variant |
| **`Transport`** | move data & robots: `capture`/`pose`/`read_panel`/`write_panel`/`step` | `sim_native.py` (in-process, default) | `ros2_bridge.py` (VDA5050/MQTT, per `ROS2_CONTRACT.md`) |
| **`RobotControl`** | "get the robot to a goal": `move_to`/`at_goal` | `kinematic.py` (teleport/interp) | Pegasus/PX4 SITL flight dynamics |

Swapping perception from the stub to the real VLM is literally **one line in a config**
(`perception: cosmos_reason` in the mission YAML). The escalation FSM in
`orchestrator/mission.py` does not change.

### The USD stage is the single source of truth
Panel state does **not** live in a database or a Python dict during a run — it lives on the
USD prim, read/written only through `schema/pv_module.py`. Two layers coexist in one stage:
- **State layer** (authoritative, changes every run): panel `pv:` attributes + `FaultReport`
  events, owned entirely by `farm_builder.py` / `pv_module.py`. This never moves regardless
  of how photoreal the appearance gets.
- **Appearance layer** (cosmetic, graduates over time): procedural boxes today →
  NuRec/3DGUT neural reconstruction of the *real* site later, referenced non-destructively,
  with turbines/birds/robots composited as kinematic USD assets on top.

### Two layers of fidelity: "can it OPERATE?" vs. "does PERCEPTION WORK?"
The key research insight — this needs **two complementary layers, not one**:
- **Physics-sim layer** (PhysX / Pegasus-PX4 / Isaac Lab) answers *can the fleet operate?* —
  hold station in a gust, survive turbine wake, avoid the swept-blade volume, traverse
  graded terrain inside battery/daylight windows. **Tests the body.**
- **World-model layer** (Cosmos Reason / Predict / Transfer) answers *does perception still
  work while it operates?* — does the VLM still read a panel correctly under a blade shadow,
  motion blur, dust, low sun. **Tests the eyes.**

### Open-loop today → closed-loop is the destination
- **Open-loop (now):** frames → VLM verdict → USD write. The world never reacts to a verdict
  within a run.
- **Closed-loop (target):** the controller reacts to gust/wake, the camera re-renders, and a
  sweeping blade shadow feeds back into whether the *next* pass logs a false hotspot.
  Delivered incrementally across the roadmap, not big-bang.

### The hard boundary: Isaac-free purity
`import omni` / `isaacsim` / `pxr` may **only** appear in `world/`, `transport/sim_native.py`,
and `transport/ros2_bridge.py`. The orchestrator, all `base.py` interfaces, perception, and
control-math must import and test **without Isaac** — `pytest tests/` runs on any box with
no GPU. This is a boundary, not a preference; breaking it breaks CI.

---

## 4. The tech stack — what, why, and when we adopt it

The project is built almost entirely on NVIDIA's "Physical AI" stack. Full catalog with
adoption phases in `docs/STACK.md`; this is the orientation version. **S0** = used now;
**P1/P2/P3** = later deepening phases; **Deploy** = only on real robots.

### World & simulation (the twin substrate)
| Tool | What it is | Why we use it | When |
|---|---|---|---|
| **Isaac Sim 6.0.1** | Omniverse-based physically-accurate robot simulator (RTX render + PhysX + sensors) | The world we build the farm and fleet in. Built from source on the Spark. | **S0** |
| **OpenUSD** | Open scene-description framework | The spine across everything; our `PVModule` panel prims live here. Source of truth. | **S0** |
| **Isaac Lab** | RL/learning framework on Isaac Sim; massively-parallel envs | Train fleet nav/coverage/station-keeping policies under domain randomization. | **P2/P3** |
| **Omniverse Libraries** | Modular, **headless-first** C/Python APIs (`ovrtx` render, `ovphysx` sim, `ovstorage`) | Serves our "scripted + headless + decoupled" principle; supports agentic MCP control. | **P1** |
| **Sensor RTX** | Physically-accurate camera/LiDAR/radar rendering | High-fidelity synthetic sensors for the drone and for training data. | **P1** |
| **NuRec / 3DGUT** | Turns real sensor scans into interactive sim via 3D Gaussian splatting | Build the twin from **real farm drone scans** instead of hand-modeling. | **P1/P2** |
| **Warp** | Python GPU-accelerated / differentiable kernels | Custom fast ops — procedural farm at scale, fault-signature physics. | **P2** |

### Physics that bites (the "can it operate?" layer)
| Tool | What it is | Why we use it | When |
|---|---|---|---|
| **PhysX + `omni.physx.forcefields`** | GPU rigid-body physics + force fields | Wind/gust/wake as real forces that push the drone. | **P2** |
| **Pegasus Simulator v5.1.0 (PX4)** | Drone flight-dynamics + PX4 SITL for Isaac Sim | Real flight dynamics replacing the kinematic teleport drone. **⚠ verify on aarch64.** | **P2** |
| **USD articulations** | Jointed/animated prims | Turbine blades that turn + swept-disk keep-out volume. | **P2** |

### Data factory (the "world model" / long-tail generation layer)
| Tool | What it is | Why we use it | When |
|---|---|---|---|
| **Cosmos Reason (2)** | ~7B physical-AI **reasoning VLM** (`cosmos-reason1-7b`, Qwen2.5-VL-7B arch) | **The brain.** Judges good/fault *in context* behind the `Perception` interface. Served via vLLM on the Spark. | **P1** |
| **Cosmos Transfer (2.5)** | ControlNet-style Sim2Real world translation (RGB/depth/seg → photoreal) | Fan one authored fault scene into thousands of photoreal variants (dawn, haze, dust). **Burst-out — not supported on the Spark.** | **P2** |
| **Cosmos Predict (2.5)** | World-state / future-video prediction | Synthesize rare failures we can't wait to photograph (hail, spreading hotspots). Burst-out. | **P2** |
| **Replicator** | Synthetic data gen with auto ground-truth labels | Free perfectly-labeled training frames — the sim knows which panel is faulted. | **S0→P2** |
| **Data Factory Blueprint + OSMO** | Reference workflow + agentic orchestrator to turn one scene into thousands of variants across DGX/OVX/cloud | Our scaled training-data recipe, officially blueprinted. | **P2** |
| **Cosmos 3** | NVIDIA's unified reason+predict+transfer+action "omnimodel" (shipped 2026-06-01) | The future engine — but **evaluated, not adopted**: too new. Mature Cosmos 2.x line is the near-term commitment. | Research |

### Real-robot perception, nav, fleet (the deploy layer)
| Tool | What it is | Why we use it | When |
|---|---|---|---|
| **Isaac ROS** (cuVSLAM, nvblox, Perceptor, NITROS) | GPU-accelerated ROS 2 perception "GEMs" | The ground bot's real-world localize + costmap + obstacle-avoid, **without lidar**. | **Deploy/P3** |
| **Nav2** | ROS 2 navigation stack | Waypoints, recovery, autonomous docking/charging. (Slice 0 uses scripted waypoints.) | **P3** |
| **cuOpt** | GPU optimization engine (VRP/LP/MIP, Apache 2.0) | The coverage/routing brain: optimal fleet route under battery + time-window + multi-depot constraints. **Note:** it's a *routing* solver — coverage waypoints are produced upstream by us, it doesn't generate 3D coverage paths. | **P3** |
| **Mission Dispatch/Client** | Isaac fleet microservices (VDA5050 over MQTT) | Command many robots the industry-standard way. **⚠ containers not yet Spark-supported.** | **P3/Deploy** |
| **NeMo Agent Toolkit** | Framework-agnostic multi-agent builder, MCP client+server | If the planner grows into a team of agents (planner / escalation-reasoner / report-writer). | **P2/P3** |

### Serving & hardware
- **vLLM (cu130-nightly, sm_121a)** — how Cosmos Reason is actually served on the Spark (the
  NIM won't run — see §2). OpenAI-compatible endpoint on `localhost:8000`.
- **NIM microservices** — the *intended* serving path once the sm_121 gap closes.
- **Hardware tiers:** DGX Spark = dev bench (interactive loop, Reason inference); DGX/OVX/cloud
  = burst-out for heavy generation & training; Jetson Thor/Orin = edge inference on the real robot.

---

## 5. World modes — how the farm gets built and rendered

"World mode" = how much fidelity the scene has. The **state layer never changes** across
these; only the appearance/physics deepens.

| Mode | Appearance | Physics | Perception | Where |
|---|---|---|---|---|
| **Slice 0 — Procedural sandbox** (current) | Procedural panel boxes, fixed lighting | Kinematic teleport drone, no forces | Ground-truth stub → Cosmos Reason wiring | Spark-local |
| **Cosmos Reason live** (in flight) | Same procedural scene | Same | **Real VLM** on actual RTX camera frames | Spark-local (vLLM) |
| **Physics that bites** | + articulated turbine, terrain undulation | PhysX force-field wind/gust, Pegasus/PX4 flight, swept-blade keep-out | VLM | Spark-local |
| **False-fault loop** | + sweeping blade shadows, motion blur (Sensor RTX) | reacts to wind/wake | VLM, **false-fault rate measured** | Spark-local |
| **Scenario factory** | Cosmos Transfer/Predict variants (dust/haze/low-sun/bird) | domain-randomized | VLM hardened on the corpus | **Burst-out** generation |
| **The real site** | NuRec/3DGUT reconstruction of the actual farm, Cesium-georeferenced | real DEM terrain mesh | VLM | Reconstruction burst-out, render Spark-local |

**Environment realism already in the sim** (per the latest commit `7894eb3`): environment
realism + a **turbine keep-out**. The keep-out is a **planning-layer** concept
(control-agnostic), not a PhysX collider — PhysX colliders stay inert until Pegasus/PX4
lands. See the `keepout-nofly-decision` memory.

**Panel state model** — attributes are namespaced `pv:` on each prim:
`pv:panel_id` (IDs like `R12-C047`), `pv:grid_index`, `pv:state`, `pv:iv_yield`,
`pv:rul_days`, `pv:inspection_log`. `pv:state` ∈
`{healthy, soiled, hotspot, crack, string_dropout, diode_fault, shading, unknown}`.
Slice 0 exercises `healthy` / `hotspot` / `soiled`. Adding a fault type = one enum entry +
a visual signature + (later) a data recipe — it must **not** change orchestration.

---

## 6. Repo map (where things live)

```
solar-twin/
├── CLAUDE.md                 # always-loaded operating brief + golden rules (read every session)
├── configs/
│   ├── farm.yaml             # seeded farm layout
│   ├── mission.yaml          # default mission (sim-native, ground-truth perception)
│   └── mission_cosmos.yaml   # demo config with perception: cosmos_reason
├── src/solar_twin/
│   ├── schema/pv_module.py   # PVModule USD read/write (the panel contract) + FaultReport
│   ├── world/                # ISAAC-BOUND: farm_builder, sim_runtime, layout, keepout
│   ├── transport/            # base.py, sim_native.py (default), ros2_bridge.py (planned)
│   ├── perception/           # base.py, ground_truth.py (stub), cosmos_reason.py (VLM, wired)
│   ├── control/              # base.py, kinematic_math.py (pure), kinematic.py (Isaac), safe.py
│   ├── orchestrator/         # mission.py (escalation FSM), fake_backend.py (tests)
│   └── run.py                # entry point → writes runs/<ts>/results.json
├── docs/                     # see "Where truth lives" below
├── tests/                    # pytest, Isaac-free (31 tests, no GPU)
├── runs/                     # gitignored run records
└── tools/                    # de-risk scripts (e.g. day1_ros2_camera_check.py)
```

---

## 7. How to run it

```bash
# Brain spine — no GPU, no Isaac, runs anywhere (incl. this box and CI)
pytest                                              # 31 tests, ~0.03s
PYTHONPATH=src python3 -m solar_twin.run configs/farm.yaml configs/mission.yaml --backend fake
#   → writes runs/<ts>/results.json (injected-vs-detected, detection_rate)

# Build the farm USD world (on the Spark, under Isaac's Python)
./python.sh -m solar_twin.world.farm_builder configs/farm.yaml

# Full mission — Isaac world (on the Spark)
./python.sh -m solar_twin.run configs/farm.yaml configs/mission.yaml   # --backend sim_native (default)

# Serve Cosmos Reason (the VLM brain) — vLLM, NOT the NIM (see docs/ENVIRONMENT.md for the full cmd)
#   then flip mission.yaml: perception: cosmos_reason
```

- **Package management:** `pip install --break-system-packages ...` for pure-python deps.
  **Never** `pip install` into Isaac's bundled Python without a note in `docs/ENVIRONMENT.md`.
- **ROS 2:** `source /opt/ros/jazzy/setup.bash` before launch; images publish with Sensor
  Data QoS → in RViz2 set image Reliability to **Best Effort**; ROS 2 OmniGraph nodes only
  publish **after Play**.

---

## 8. Roadmap in one screen

Each slice is **one thin end-to-end thread** through all relevant layers — fidelity deepens
*along* a working loop, never as a big-bang integration. Exit criteria are measured KPIs, not
GUI demos. Explicitly multi-quarter. (Full detail: `docs/specs/07-roadmap-and-milestones.md`.)

| Slice | Thread | Status |
|---|---|---|
| **0** | Procedural farm, stub perception → VLM verdict → USD write (open-loop) | **Done** |
| **1** | Cosmos Reason on the real RTX camera feed (confirm sm_121 vLLM path) | **In flight** |
| **2** | Physics that bites: Pegasus/PX4 flight + force-field wind + turbine keep-out | Next |
| **3** | The false-fault loop: sweeping blade shadows + motion blur; measure false-fault rate | |
| **4** | Scenario factory: Cosmos Transfer/Data Factory off-box; harden Reason on the corpus | |
| **5** | Trained flight policy: Isaac Lab RL station-keeping under domain-randomized wind | |
| **6** | The real site: drone-survey → NuRec/3DGUT reconstruction → Cesium-georeferenced USD | |
| **7** | Fleet + coverage brain: ground bot as full fleet member, cuOpt routing, Mission Dispatch | |
| **8** | Deploy bridge: swap Transport to ROS 2/VDA5050, Perception to on-robot; SIL→HIL | |

**Explicit non-goals (research-grade, not committed):** online world-model-in-the-loop
planning; true CFD turbine-wake (we use a parametric velocity-deficit approximation); Cosmos 3
as the default build target; on-box photoreal reconstruction training as a hard requirement;
a native 3D coverage-path planner (cuOpt routes, it doesn't generate coverage paths).

---

## 9. Working rules that bite if you forget them

From `CLAUDE.md` "Do NOT" + the golden rules — the ones that actually break things:
- **No `omni`/`pxr`/`isaacsim` imports** in `orchestrator/`, any `*/base.py`, perception, or
  control-math. It breaks the Isaac-free tests and CI.
- **Verify Isaac 6.0 APIs and asset paths against the installed build** — do not trust
  remembered 5.1 snippets (including ones in the bible). If unsure, say so and check.
- **No GUI-only pipeline steps.** Everything reproducible is a script + a config. The Isaac
  UI is for inspection, never construction. An MCP-driven action must *also* exist as a script.
- **USD stage is the source of truth** for panel state — never a side store during sim.
- **Don't commit large binaries** (USD assets, videos, checkpoints); `runs/` is gitignored.
- **Don't push to `main`** — branch + PR. **Don't `apt upgrade` the Spark.**
- **Definition of done:** closest `pytest` tests pass, new logic has an Isaac-free test, and
  any contract change (schema, coordinates, ROS 2 topics, an interface) is reflected in `docs/`.

---

## 10. Where truth lives {#where-truth-lives}

This doc orients; these own the details. When they disagree with each other, the more
specific/locked one wins.

| Doc | Owns |
|---|---|
| `CLAUDE.md` | Always-loaded operating brief + golden rules. **Never overridden by anything below.** |
| `docs/PROJECT_BIBLE.md` §6 | **Locked** canonical contracts: `PVModule`, coordinates, ROS 2 topics, `Perception`, fault taxonomy. |
| `docs/ENVIRONMENT.md` | **Ground truth** for the actual Spark build, versions, the vLLM serving command, ROS 2 status. |
| `docs/STACK.md` | Full NVIDIA stack catalog: what each tool is, why it matters, adoption phase. |
| `docs/specs/01`–`08` | Numbered requirements (FR/NFR), architecture pillars (PIL), hazards (HAZ), KPIs, scenarios (SC), roadmap slices, risk register. |
| `docs/DIGITAL_TWIN_VISION_AND_RESEARCH.md` | The research narrative (13-agent swarm) behind the vision. Don't scope down from it. |
| `docs/ROS2_CONTRACT.md` | ROS 2 topic/message contract for the eventual bridge. |
| `docs/TASKS.md` | Live per-person task board, split by machine. |
| `SESSIONS.md` | Rolling session-by-session status log. |

> If code and `docs/PROJECT_BIBLE.md` disagree, fix one of them in the same commit
> (`CLAUDE.md` commit convention). If a spec and the bible §6 disagree, the bible wins.
