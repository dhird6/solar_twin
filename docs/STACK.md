# STACK.md — NVIDIA Physical-AI Stack Catalog for solar-twin

**Status:** v0.1 · **Date:** July 2026 · Companion to `PROJECT_BIBLE.md` (execution) and `solar-inspection-digital-twin-plan.md` (strategy).

> **Purpose.** The full, opinionated map of the NVIDIA stack (and the MCP/agent tooling around it) as it applies to *this* project — what each thing is, **why it matters to us**, and **when we adopt it**. Loaded on demand, not every session. Everything version/roadmap-specific carries a **⚠ verify** — the physical-AI stack is moving monthly (Cosmos 3, Isaac Lab 3.0, Omniverse Libraries all landed in 2026), so confirm on your build/date before committing.
>
> **How to read the "Adopt" column:** `S0` = useful in the Slice 0 thread · `P1/P2/P3` = the deepening phases in `PROJECT_BIBLE.md` §9 · `Deploy` = only when we put robots on a real farm · `Adjacent` = awareness, not core to our drone/AMR inspection use case.

---

## 1. World & simulation (the twin substrate)

| Tool | What it is | Why it matters to us | Adopt |
|---|---|---|---|
| **Isaac Sim 5.1** | Omniverse-based, physically accurate robot simulator (render + PhysX + sensors) | The world we build the farm and fleet in. Built from source on the Spark (aarch64). | **S0** |
| **Isaac Lab 3.0 (Beta)** | RL/learning framework on top of Isaac Sim; massively parallel envs | Train fleet nav/coverage/coordination policies. Now built on the modular Omniverse Libraries. ⚠ 3.0 is Beta — verify. | **P3** |
| **OpenUSD** | Open scene-description framework | The single spine across all three worlds; our `PVModule` prims live here. | **S0** |
| **Omniverse Libraries** *(GTC 2026)* | Modular, **headless-first** C APIs (C++/Python bindings): `ovrtx` (RTX render), `ovphysx` (PhysX sim), `ovstorage` (data) | Directly serves our "everything scripted + headless + decoupled" principle — explicit execution control, no UI lock-in, tensorized data exchange. **Supports agentic orchestration via MCP servers.** Used inside Isaac Lab 3.0 Beta. | **P1** |
| **Sensor RTX APIs** | Physically accurate camera/LiDAR/radar rendering (Omniverse Cloud) | High-fidelity synthetic sensors for the confirm-drone and training data. | **P1** |
| **Omniverse NuRec** *(GA)* | Turns real sensor scans into interactive sims via 3D Gaussian splatting | Build the twin (or patches of it) from **real farm drone scans** instead of hand-modeling — a fast path to a realistic World 1. ⚠ verify GA scope. | **P1/P2** |
| **World Labs "Marble" + Isaac Sim** | Fast 3D environment generation for sim | Rapidly stand up varied backdrops/terrain for the farm without weeks of modeling. ⚠ verify integration. | **P1** |
| **NVIDIA Warp** | Python framework for GPU-accelerated, differentiable simulation/kernels | Custom fast ops (procedural farm generation at scale, custom fault-signature physics, batched geometry). | **P2** |
| **Newton** | Emerging open GPU physics engine (NVIDIA + DeepMind + Disney) targeted at robotics/Isaac Lab | Watch item — may back Isaac Lab physics. ⚠ verify status/compat before relying on it. | **Adjacent** |

---

## 2. Data factory (World 2 — scale + realism)

| Tool | What it is | Why it matters to us | Adopt |
|---|---|---|---|
| **Omniverse Replicator** | Synthetic data generation: auto-labeled camera/depth/segmentation with ground-truth masks | Free perfectly-labeled training frames from our scene (the sim knows which panel is faulted). Semantic labels via `add_update_semantics` (⚠ API moved across 5.x). | **S0→P2** |
| **Cosmos 3** *(May 31, 2026)* | NVIDIA's open "omnimodel" for physical AI — unifies **reason + predict + transfer + action** in one architecture; multimodal in/out (text, image, video, ambient audio, action) | The engine of the data factory *and* the brain. Variants: **Nano ~16B**, **Super ~64B**, **Edge ~2B** (Edge announced for later). Open under **OpenMDW 1.1** (commercial use) on Hugging Face. ⚠ verify variant sizes/license/date. | **P2 (Reason: P1)** |
| **Cosmos Transfer (2.5)** | Control-net-style Sim2Real / Real2Real world translation (RGB/depth/segmentation → photoreal) | Multiply one authored fault scene into thousands of photoreal variants (dawn, haze, dust, monsoon). **Burst-out workload — not supported on the Spark.** | **P2** |
| **Cosmos Predict (2.5)** | World-state prediction; generate future/video worlds from text/image/video | Synthesize rare failures we can't wait to photograph (hail damage, spreading hot-spots, soiling). Burst-out. | **P2** |
| **Cosmos Reason (2)** | ~7B physical-AI reasoning **VLM** (tops physical-reasoning benchmarks) | Replaces the narrow detector: judges good/fault *in context*, plans missions, and auto-annotates data. Can prototype inference on the Spark; deploy as a **NIM**. | **P1/P2** |
| **Physical AI Data Factory Blueprint** | Reference workflow: **Cosmos + OSMO** to turn one real scenario into thousands of synthetic variations | Our World 2, officially blueprinted — the recipe to scale training data. | **P2** |
| **Cosmos Cookbook** | NVIDIA recipes for scaling physical-AI data generation | Practical patterns for the data factory. ⚠ verify current contents. | **P2** |
| **OSMO** | Cloud-native **agentic orchestrator** for robotics/data workloads (multi-stage pipelines across DGX/OVX/cloud) | Orchestrates the burst-out data-factory jobs and multi-stage train/eval pipelines. | **P2** |

---

## 3. Perception & navigation (real robots — Isaac ROS)

GPU-accelerated ROS 2 packages ("Isaac GEMs"). Core to the *deployable* path and to realistic sim of the ground bot. Isaac ROS ≠ the Isaac Sim ROS 2 bridge — different thing, same ROS 2 world.

| Tool | What it is | Why it matters to us | Adopt |
|---|---|---|---|
| **Isaac Perceptor** | Reference AMR perception workflow (cuVSLAM + nvblox + stereo-depth DNN); integrates with Nav2; Nova Carter tutorial exists | The ground bot's real-world perception: localize + build a costmap + avoid obstacles **without lidar**. A power-plant *inspection-robot* reference already uses this exact stack. | **Deploy / P3** |
| **cuVSLAM** | GPU stereo visual-inertial SLAM/odometry (sub-1% trajectory error; falls back to IMU) | Camera-based localization for the ground bot in the farm; runs on Jetson at the edge. | **Deploy / P3** |
| **nvblox** | CUDA 3D reconstruction → 2D costmap (obstacles to ~5 m; ~100× CPU) | Feeds Nav2 path planning / obstacle avoidance for the ground bot. | **Deploy / P3** |
| **NITROS** | GPU-accelerated ROS 2 transport (keeps data on GPU, avoids PCIe round-trips) | Makes the perception→nav pipeline fast enough for real-time on edge hardware. | **Deploy / P3** |
| **Nav2** | The ROS 2 navigation stack (planning, control, docking) | The ground bot's autonomy: waypoints, recovery, and **autonomous docking/charging**. (Slice 0 uses scripted waypoints instead.) | **P3** |
| **cuMotion / cuRobo** | CUDA motion planning; MoveIt 2 integration | Only if we add a **manipulator** (e.g., a contact inspection arm). | **Adjacent** |

---

## 4. Fleet orchestration & optimization (World 3)

| Tool | What it is | Why it matters to us | Adopt |
|---|---|---|---|
| **Mission Dispatch / Mission Client** | Isaac fleet-management microservices; **VDA5050 over MQTT**; ROS 2 Humble; pre-integrated with Nav2 (Mission Dispatch on NGC/GitHub, Mission Client in Isaac ROS) | Assign and track inspection tasks across a *fleet* of bots/drones — the industry-standard way to command many robots. This is our World 3 dispatch layer at scale. | **P3 / Deploy** |
| **cuOpt** | Open-source (Apache 2.0) GPU optimization engine: **VRP**, LP, MIP; Python/REST/CLI; has an AMR "intra-factory transport" example and **agent skills** | The **out-of-the-box coverage/routing brain**: given panels-to-inspect + battery/time-window + multi-depot (ground-bot garages) constraints, compute the optimal fleet route in seconds. Turns "which panels, in what order, by which robot" from a heuristic into a solver. | **P3** |

---

## 5. Reasoning, agents & serving

| Tool | What it is | Why it matters to us | Adopt |
|---|---|---|---|
| **Cosmos Reason** | (see §2) physical-AI VLM | The perception+planner brain behind the `Perception` interface. | **P1/P2** |
| **NeMo Agent Toolkit** *(open source; formerly AgentIQ / `nvidia-nat`)* | Framework-agnostic library to **build, profile, observe, and optimize multi-agent systems**; works with LangChain/CrewAI/LlamaIndex/etc.; **MCP client and server** (publish workflows as MCP via FastMCP); has an "AI Coding Agent Skills" feature and a **Physical-AI robot-sim** use case | If the mission planner grows into a *team* of agents (screen-planner, escalation-reasoner, report-writer), this gives orchestration + observability + profiling without replatforming, and speaks MCP. | **P2/P3** |
| **NIM microservices** | Containerized, optimized model-serving endpoints | Deploy Cosmos Reason (and other models) as stable API endpoints the orchestrator calls — same interface in sim and prod. | **P2/P3** |
| **Nemotron** | NVIDIA open reasoning LLMs | Optional reasoning backbone for agents (alternative/complement to Cosmos Reason for text planning). | **Adjacent** |
| **Metropolis + VSS Blueprint** | Visual-AI-agent stack: video search, dense captioning, real-time alerts | The **operator-facing layer** — turn inspection footage + the twin into queryable insight and alerts for the O&M team. | **P3** |

---

## 6. Agentic control & MCP for the *development* loop (Claude Code)

This is the part that makes Claude Code a hands-on collaborator in the sim, not just an editor of files.

| Tool | What it is | Why it matters to us | Adopt |
|---|---|---|---|
| **Isaac Sim MCP server** (community) | MCP servers that expose Isaac Sim to MCP clients for **natural-language / agentic control** — create robots, build scenes, add cameras/sensors, run + step the sim, capture images, **hot-reload Python controllers** without restarting. Notable: `nullbyte91/nvidia-isaac-mcp` (targets **Isaac Sim 5.x**, needs the `isaacsim.mcp.server` extension, configured via `ISAAC_SIM_HOST`/`ISAAC_SIM_PORT`, and explicitly lists **Claude Code / Claude Desktop / Windsurf** support); also `omni-mcp/isaac-sim-mcp`, `whats2000/isaacsim-mcp-server` | Lets Claude Code drive Isaac Sim while iterating on `farm_builder.py` / `sim_runtime.py` — "spawn the bot at row 3, capture the drone camera, step 60 frames" — tightening the loop dramatically. **Caveats:** community-maintained, small, Linux-only, needs a running Isaac + the extension; **⚠ verify it works on aarch64/Spark before depending on it.** Treat as a productivity aid, not part of the reproducible pipeline (our scripts remain the source of truth). | **Optional, S0+** |
| **Omniverse Libraries MCP** | Official direction: the modular Omniverse Libraries support agentic orchestration via MCP servers | The sanctioned, forward-looking path for agent-driven headless Omniverse; watch as it matures. | **P1+ (watch)** |
| **NeMo Agent Toolkit MCP** | (see §5) publish/consume tools over MCP | Bridge our own tools/agents into an MCP ecosystem later. | **P2/P3** |

**MCP hygiene for Claude Code (this repo):** any MCP that can execute code or mutate the sim is a real capability — enable deliberately, keep secrets (`NVIDIA_API_KEY`, etc.) out of the repo, and never let an MCP-driven step become the *only* way something happens (it must also exist as a script + config). See `CLAUDE.md` → "Do NOT".

---

## 7. Deployment & hardware

| Piece | Role | Note |
|---|---|---|
| **DGX Spark (GB10, aarch64)** | Our **dev bench** | Prototype twin + policies + Reason inference + orchestration. Not the scale/burst box. |
| **DGX systems** | Train foundation/multimodal models | For heavy training if we go there. |
| **OVX systems** | Simulate/test/train at scale | Where large Replicator/Cosmos generation and big sim farms run. |
| **Jetson Thor / Orin (AGX)** | **Edge inference on the real robot** | cuVSLAM/nvblox/Nav2 + a Cosmos Edge / NIM run here on a deployed bot. |
| **Cloud (build.nvidia.com blueprints)** | Burst-out | Run Cosmos Transfer/Predict and the Data Factory blueprint off-Spark. |

---

## 8. Data & models

- **Cosmos** model checkpoints on Hugging Face (Predict/Transfer/Reason; Cosmos 3 Nano/Super). ⚠ verify licenses per model (OpenMDW 1.1 for Cosmos 3).
- **NVIDIA Physical AI datasets** (Hugging Face) — reference robot/AV data for pretraining/eval.
- **Isaac GR00T open foundation models** — humanoid reasoning/skills; **Adjacent** to us (drones/AMRs), but its data workflows (GR00T-Gen/Dreams via world models) overlap the data-factory idea.

---

## 9. What we actually adopt, by phase (quick index)

- **Slice 0:** Isaac Sim 5.1, OpenUSD, Replicator (labels only), *(optional)* Isaac Sim MCP for the Claude Code loop.
- **P1 — twin fidelity:** Omniverse Libraries (headless), Sensor RTX, NuRec (twin-from-scans), Cosmos Reason (swap in as the brain).
- **P2 — data factory:** Replicator + Cosmos Transfer/Predict, Data Factory Blueprint + OSMO, NIM (serve Reason), Warp, Isaac Lab 3.0 (start policy training).
- **P3 — operational loop + deploy:** Isaac Perceptor (cuVSLAM/nvblox/NITROS) + Nav2, Mission Dispatch/Client, **cuOpt** (fleet routing), Metropolis/VSS (operator layer), NeMo Agent Toolkit (multi-agent planner), Jetson at the edge.

---

## 10. References (⚠ verify before committing to version/roadmap specifics)

- Isaac platform overview — developer.nvidia.com/isaac
- Isaac Perceptor / cuVSLAM / nvblox — developer.nvidia.com/isaac/perceptor ; Nav2 tutorial: docs.nav2.org/tutorials/docs/using_isaac_perceptor.html
- Isaac ROS Mission Dispatch/Client — developer.nvidia.com/blog/open-source-fleet-management-tools-for-autonomous-mobile-robots
- Omniverse Libraries (ovrtx/ovphysx/ovstorage + MCP) — developer.nvidia.com/blog/integrate-physical-ai-capabilities-into-existing-apps-with-nvidia-omniverse-libraries
- Cosmos overview / Cosmos 3 — nvidia.com/en-us/ai/cosmos ; github.com/nvidia-cosmos
- Physical AI Data Factory Blueprint + OSMO + NuRec — NVIDIA GTC 2026 announcements (verify current pages)
- NeMo Agent Toolkit — developer.nvidia.com/agentiq ; github.com/NVIDIA/NeMo-Agent-Toolkit ; docs.nvidia.com/nemo/agent-toolkit
- cuOpt — nvidia.com/en-us/ai-data-science/products/cuopt ; github.com/NVIDIA/cuopt ; github.com/NVIDIA/cuopt-examples
- Isaac Sim MCP servers — github.com/omni-mcp/isaac-sim-mcp ; lobehub.com/mcp/nullbyte91-nvidia-isaac-mcp
- Pegasus Simulator (drone/PX4, Isaac 5.1) — github.com/PegasusSimulator/PegasusSimulator
- Isaac GR00T (adjacent) — developer.nvidia.com/isaac/gr00t
