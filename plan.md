# PLAN — Slice 0 execution & work division

> The actionable checklist for Slice 0. Specs + steps + `[ ]` boxes, grouped into
> **workstreams** that can be assigned to different people/sessions. Strategy is
> in `docs/PROJECT_BIBLE.md` §8; status/history is in `SESSIONS.md`; exact box
> facts are in `docs/ENVIRONMENT.md`. When a spec here and the code disagree, fix
> one in the same commit.

## Legend
- **[ ]** todo · **[x]** done · **[~]** in progress · **[!]** blocked
- **Where:** `anywhere` (pure-python, no Isaac) · `spark` (needs Isaac `python.sh`)
- Each workstream lists its **owner interface** (what it must satisfy) so it can
  be built and tested against the contract independently.

## Locked contracts (do not drift — §6 of the bible)
- **Panel schema** — `pv:` attributes in `schema/pv_module.py` (`panel_id`,
  `grid_index`, `geo_position`, `state`, `iv_yield`, `rul_days`,
  `last_inspected`, `inspection_log`). USD stage is the source of truth.
- **Coordinates** — Z-up, meters; assert on build. Georef anchor + `local_to_geo`.
- **Fault taxonomy** — `healthy·soiled·hotspot·crack·string_dropout·diode_fault·
  shading·unknown`; Slice 0 subset `healthy·hotspot·soiled`.
- **Interfaces** — `Perception` (assess/diagnose), `Transport` (capture/pose/
  read_panel/write_panel/step), `RobotControl` (move_to/at_goal). Swappable.
- **ROS 2 topics** — §6.3 table; images use **Best Effort** QoS; nodes publish
  only after Play.

---

## Environment reality (verified 2026-07-21 — see docs/ENVIRONMENT.md)
- aarch64 · CUDA 13.0 · GB10 · **Isaac Sim 6.0.1-rc.7** (⚠ NOT 5.1 — verify APIs
  against 6.0) · `python.sh` at `IsaacSim/_build/linux-aarch64/release/`.
- **ROS 2 was absent**; installing **Jazzy** (24.04 native, bridge bundles it).
- `usd-core` has no aarch64 wheel → `pxr` only under Isaac's Python.
- Local `Qwen2.5-VL-72B` vLLM on `:8000` (later perception backend).

---

## Workstream 0 — Environment / Day-1 de-risk  ·  where: spark
Owner: whoever has the box. Prereq for all `spark` work.
- [x] Capture arch/CUDA/GPU/Isaac version/python.sh path → `docs/ENVIRONMENT.md`.
- [x] Determine ROS 2 status: was **absent** → installed Jazzy.
- [x] Install ROS 2 Jazzy (`tools/install_ros2_jazzy.sh`, ros-base).
- [x] `source /opt/ros/jazzy/setup.bash && ros2 doctor` → all 5 checks passed.
- [x] Capture Isaac build commit (045ca8b, 6.0.1). [ ] Isaac Lab symlink + PyTorch cu13 version.
- [x] Launch Isaac Sim headless; confirmed render (~14s warm start).
- [x] Enable `isaacsim.ros2.bridge`; publish a camera; confirmed via
      `ros2 topic list` + `hz /rgb` (~50 Hz) + `echo /camera_info`.
- [x] **Result recorded: ROS 2 camera path WORKS on this Spark** (ENVIRONMENT.md).
- [ ] Decide: update the "5.1" references in CLAUDE.md/bible to 6.0.1?
- **Done:** ✅ ROS 2 camera path proven; sandbox proven. WS0 complete.

---

## Workstream A — BRAIN spine (pure-python)  ·  where: anywhere  ·  **[x] COMPLETE**
Runs and is tested with no Isaac. 31 pytest tests green; `--backend fake` emits a
run record with detection_rate 1.00.
- [x] `schema/pv_module.py` — taxonomy, `PanelRecord`, `pv:` constants, log,
      `local_to_geo`/`geo_to_local`. pxr lazy-imported inside USD fns only.
- [x] `perception/base.py`, `transport/base.py`, `control/base.py` — interfaces.
- [x] `perception/ground_truth.py` — reads `pv:state` from context (the cheat).
- [x] `orchestrator/mission.py` — escalation FSM + structured `MissionResult`.
- [x] `orchestrator/fake_backend.py` — `FakeSimBackend` (Transport+RobotControl).
- [x] `world/layout.py` — geometry + seeded fault injection (shared w/ builder).
- [x] `configs/farm.yaml`, `configs/mission.yaml` (seeded).
- [x] `run.py` — config → mission → `runs/<ts>/results.json`.
- [x] `tests/` — schema, georef, perception, mission, layout.
- **Follow-ups (small, anywhere):**
  - [ ] Custom `FaultReport` payload dataclass shared by run record + ROS 2 seam.
  - [ ] `perception/cosmos_reason.py` skeleton targeting the local Qwen VLM
        (`:8000`, OpenAI-compatible) behind the `Perception` interface.

---

## Workstream B — WORLD scene / farm_builder  ·  where: spark  ·  **[x] COMPLETE**
**Satisfies:** produces a USD stage whose panels are readable by `schema` USD fns.
**Reuses:** `world/layout.py` (identical grid + seeded faults as the fake run).
- [x] `world/farm_builder.py`: `PYTHONPATH=src ./python.sh -m solar_twin.world.farm_builder configs/farm.yaml --out assets/farm.usd`.
  Authors USD with pure pxr — **no SimulationApp needed** (fast).
  - [x] Create stage; **assert Z-up + meters** (UsdGeom, verified 6.0).
  - [x] Per `FarmLayout` site: `schema.create_panel` (pv: attrs incl geo_position)
        + a box mesh (unit cube scaled to panel dims, tilted).
  - [x] Seeded faults: set `pv:state` + **emissive material signature**
        (hotspot = emissive red, soiled = tan) via UsdPreviewSurface.
  - [x] Semantic labels via `pxr.UsdSemantics.LabelsAPI` (taxonomy "class").
  - [x] Writes to `--out` (default `assets/farm.usd`, gitignored).
- [x] Verified (spark): reopened, 10 queryable panels, 2 faults read back
      (soiled), labels `[panel, soiled]`, material bound. Matches fake-run faults.
- [x] Regression test `tests/test_schema_usd.py` (pxr-guarded; skips on aarch64).
- **Fixed a real schema bug** found here: `pv:grid_index` Int2 needs `Gf.Vec2i`
      (a bare tuple made USD infer double + raise). Pure tests couldn't catch it
      (no usd-core on aarch64) — running under `./python.sh` did.
- **Done.** ✅

---

## Workstream C — SIM runtime + sim-native transport  ·  where: spark
**Satisfies:** `Transport` (`transport/sim_native.py`) + drives stepping/sensors.
- [ ] `world/sim_runtime.py`: launch `SimulationApp` (headless option), load the
      built stage, add a camera render-product, step the world.
- [ ] `transport/sim_native.py` implements `Transport`:
  - [ ] `capture(robot_id)` → camera frame via annotator/render-product (⚠ 6.0 API).
  - [ ] `pose(robot_id)` → read prim xform.
  - [ ] `read_panel`/`write_panel` → `schema` USD fns on the live stage.
  - [ ] `step(dt)` → advance the sim.
- [ ] Wire `sim_native` into `run._build_backend` (replace the NotImplementedError).
- **Done when:** the drone camera frame is grabbable in Python; poses read back.

---

## Workstream D — CONTROL (kinematic)  ·  where: spark (impl) / anywhere (math)
**Satisfies:** `RobotControl` (`control/kinematic.py`).
- [ ] `control/kinematic.py`: `move_to` teleports/interps an Xform toward the
      waypoint at the configured speed; `at_goal` checks tolerance.
- [ ] Ground base asset (simple: Jetbot/Carter v1) + a drone Xform + camera.
- [ ] Pure-python interp math can be unit-tested `anywhere` (no Isaac).
- **Done when:** both robots reach each panel's waypoints in sim.

---

## Workstream E — ROS 2 seam  ·  where: spark  ·  **UNBLOCKED (camera path proven Day-1)**
**Satisfies:** `Transport` (`transport/ros2_bridge.py`), same interface as sim_native.
- [ ] After Jazzy install + Day-1 camera check passes: build `ros2_bridge.py`
      (camera sub, `cmd_vel` pub, `pose`, `/mission/fault`) per §6.3.
- [ ] `docs/ROS2_CONTRACT.md` — finalize the topic/message contract.
- [ ] Smoke-test against the sim if the camera path works; else keep it a
      validated-but-unused stub and log why.
- **Done when:** the seam exists; used only once proven (Slice 0 stays sim-native).

---

## Workstream F — Integration + run record  ·  where: spark
- [ ] `python.sh -m solar_twin.run configs/farm.yaml configs/mission.yaml`
      (`--backend sim_native`) drives the full thread end to end.
- [ ] Confirm USD reflects updated states + inspection logs after a run.
- [ ] Assert run-record detection == injected (ground truth ⇒ 100%).
- [ ] (Optional) capture a video/gif of a run.
- [ ] Update `README.md`, `SESSIONS.md`, and the bible/`CLAUDE.md` with learnings.
- **Done when:** Slice 0 gate met (bible §8 one-liner).

---

## Suggested division
- **Person/Session 1 (has the Spark):** WS0 → WS B → WS C → WS F. The critical path.
- **Person/Session 2 (any machine):** WS A follow-ups (FaultReport, cosmos_reason
  skeleton), WS C interp math + tests, `docs/ROS2_CONTRACT.md` draft (WS E prep).
- **WS E** unblocks after WS0's ROS 2 install + camera check; can then be a 3rd track.
- WS B and WS C both need the Spark → serialize on one box, or parallelize if two.
