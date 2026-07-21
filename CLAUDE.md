# CLAUDE.md — solar-twin

Autonomous solar-farm inspection **digital twin**. A robot fleet (ground bot + drones) inspects panels in an Isaac Sim world; verdicts are written back onto USD panel prims; a closed maintenance loop is the end goal. This file is the always-loaded operating brief. Depth lives in `docs/PROJECT_BIBLE.md` — read it when you need detail; it is intentionally NOT imported here (too large for every session).

<!-- Maintainer note: keep this file under ~200 lines and command-first. Move procedures to docs/ or .claude/rules/. -->

## Golden rules (read every time)
- **Platform is a DGX Spark: aarch64, CUDA ≥ 13, cu13 PyTorch.** Isaac Sim 5.1 is built from source; Isaac Lab is symlinked to it (`_isaac_sim`). Exact build/versions are in `docs/ENVIRONMENT.md`.
- **Isaac-bound code runs only under Isaac Sim's Python** (`./python.sh`). Pure-python code (orchestrator, perception interfaces, transport/base, tests) must import WITHOUT Isaac — keep every `import omni`/`isaacsim`/`pxr` inside `world/`, `transport/sim_native.py`, `transport/ros2_bridge.py`.
- **No GUI-only steps in the pipeline.** Everything reproducible is a Python entry point + a config in `configs/`. The Isaac Sim UI is for inspection, never construction.
- **The USD stage is the source of truth for panel state.** Read/write panel fields via `src/solar_twin/schema/pv_module.py` — never a side store during sim.
- **Version-specific Isaac APIs and asset paths drift between releases. Verify against the installed 5.1 build; do not trust remembered snippets** (including ones in the bible). If unsure, say so and check the docs.
- **Default Transport is sim-native**, not ROS 2 — the Spark has reported ROS 2 sensor-rendering quirks. ROS 2 is behind an interface and optional until proven (see `docs/ENVIRONMENT.md`).

## Environment / how to run
- Package management: `pip install --break-system-packages ...` for the pure-python deps. **Never** `pip install` into Isaac Sim's bundled Python without a note in `docs/ENVIRONMENT.md`.
- Run an Isaac-bound script: `./python.sh -m solar_twin.<module> <args>` (⚠ from the Isaac Sim build dir, or via the project's launch alias — see `docs/ENVIRONMENT.md`).
- Build the farm: `./python.sh -m solar_twin.world.farm_builder configs/farm.yaml`
- Run a full mission: `./python.sh -m solar_twin.run configs/farm.yaml configs/mission.yaml`
- ROS 2: source ROS 2 before launching, or enable the bridge with `--enable isaacsim.ros2.bridge`. Images publish with **Sensor Data QoS** → in RViz2 set image Reliability to **Best Effort**. ROS 2 OmniGraph nodes only publish **after Play**.
- Tests (no GPU, no Isaac): `pytest tests/`

## Repo map
- `configs/` — `farm.yaml`, `mission.yaml` (seeded, drive everything)
- `src/solar_twin/schema/pv_module.py` — PVModule USD read/write (the panel contract)
- `src/solar_twin/world/` — `farm_builder.py`, `sim_runtime.py` (Isaac-bound)
- `src/solar_twin/transport/` — `base.py`, `sim_native.py` (default), `ros2_bridge.py`
- `src/solar_twin/perception/` — `base.py`, `ground_truth.py` (stub), `cosmos_reason.py` (later)
- `src/solar_twin/control/` — `base.py`, `kinematic.py` (waypoint drone/bot)
- `src/solar_twin/orchestrator/` — `mission.py` (escalation FSM), `fake_backend.py` (for tests)
- `src/solar_twin/run.py` — entry point → writes `runs/<ts>/`
- `tests/` — pytest, Isaac-free · `docs/` — bible, ARCHITECTURE, ENVIRONMENT, ROS2_CONTRACT · `runs/` — gitignored

## Conventions
- **USD: Z-up, meters.** Assert on load; farm sets it at build.
- Panel attributes are namespaced `pv:` (`pv:panel_id`, `pv:grid_index`, `pv:state`, `pv:iv_yield`, `pv:rul_days`, `pv:inspection_log`). Panel IDs look like `R12-C047`.
- `pv:state` ∈ { healthy, soiled, hotspot, crack, string_dropout, diode_fault, shading, unknown }. Slice 0 uses healthy/hotspot/soiled.
- Interfaces are swappable — code against `Perception`, `Transport`, `RobotControl` base classes, never a concrete impl. Adding a fault type = enum entry + visual signature + (later) a data recipe; it must not change orchestration.
- Python: type hints on public functions; dataclasses for structured returns (`Verdict`, `Diagnosis`); 4-space indent; f-strings.
- Commits: small and scoped; imperative subject; if code and `docs/PROJECT_BIBLE.md` disagree, fix one in the same commit.

## Do NOT
- Do not put Isaac/`pxr`/`omni` imports in `orchestrator/`, `perception/base.py`, or `transport/base.py` — it breaks Isaac-free tests and CI.
- Do not add GUI-only setup that the pipeline can't reproduce from a script + config.
- Do not commit large binaries (USD assets, videos, checkpoints). `runs/` and heavy assets are gitignored.
- Do not hand-edit generated USD; regenerate via `farm_builder.py`.
- Do not push to `main`; branch + PR.
- Do not invent Isaac API names or asset paths — verify, or flag uncertainty.

## Definition of done
A change is done when: the closest `pytest` tests pass, new logic has a test that runs without Isaac, and any behavior change is reflected in `docs/` if it touches a contract (schema, coordinates, ROS 2 topics, or an interface).

## Stack & tooling (know these; full catalog in `docs/STACK.md`)
- **Sim/world:** Isaac Sim 5.1, Isaac Lab 3.0, OpenUSD, Omniverse Libraries (headless), Sensor RTX, NuRec, Warp.
- **Data factory (P2, burst-out):** Replicator + **Cosmos 3** (reason/predict/transfer/action) + OSMO (Data Factory Blueprint). Cosmos Transfer is NOT supported on the Spark — it's off-box.
- **Reasoning brain:** **Cosmos Reason** behind the `Perception` interface (swaps in for the stub, no orchestration change); served via NIM.
- **Real-robot perception/nav (P3/deploy):** Isaac ROS — cuVSLAM, nvblox, Isaac Perceptor, NITROS + Nav2.
- **Fleet + routing (P3):** Mission Dispatch/Client (VDA5050/MQTT); **cuOpt** for optimal coverage/route under battery+time-window constraints.
- **Agents:** NeMo Agent Toolkit (MCP-capable) if the planner becomes multi-agent.
- **Drone realism (P2):** Pegasus Simulator v5.1.0 (PX4). Slice 0 drone is kinematic.
- Anything version/roadmap-specific is **⚠ verify** — this stack moves monthly.

## MCP (optional dev aid — not part of the reproducible pipeline)
- A community **Isaac Sim MCP** server (e.g. `nullbyte91/nvidia-isaac-mcp`, Isaac Sim 5.x + `isaacsim.mcp.server` extension) can let Claude Code drive the running sim in natural language (spawn bot, add camera, step, capture, hot-reload). ⚠ verify it runs on aarch64/Spark first.
- Rules: keep API keys (`NVIDIA_API_KEY`, etc.) out of the repo; an MCP-driven action must ALSO exist as a script + config — MCP is never the only way something happens.

## Where to go deeper (load on demand — don't pull these in every session)
- Full plan, day-by-day Slice 0, architecture, contracts → `docs/PROJECT_BIBLE.md`
- Full NVIDIA stack catalog (roles + adoption phase) → `docs/STACK.md`
- Exact Spark build, versions, launch alias, ROS 2 status → `docs/ENVIRONMENT.md`
- ROS 2 topic/message contract → `docs/ROS2_CONTRACT.md`
- Strategy (problem, three worlds) → `docs/solar-inspection-digital-twin-plan.md`

## Enforcement note
This file is context, not a hard gate. Anything that MUST happen (tests before "done", no commits to `main`) belongs in a pre-commit / PreToolUse hook or CI, not only here.
