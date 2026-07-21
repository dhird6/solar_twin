# PLAN — Slice 0 execution & work division

> The actionable checklist for Slice 0. Specs + steps + `[ ]` boxes, grouped into
> **workstreams** that can be assigned to different people/sessions. Strategy is
> in `docs/PROJECT_BIBLE.md` §8; status/history is in `SESSIONS.md`; exact box
> facts are in `docs/ENVIRONMENT.md`. When a spec here and the code disagree, fix
> one in the same commit. **For the live, per-person/per-machine cut of this
> same checklist (Normal machine vs DGX Spark, exact commands, handoff points),
> see `docs/TASKS.md` — keep both files' `[ ]` boxes in sync.**

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
- [x] Determine ROS 2 status: **absent** → sim-native confirmed for Slice 0.
- [~] Install ROS 2 Jazzy (`tools/install_ros2_jazzy.sh`).
- [ ] `source /opt/ros/jazzy/setup.bash && ros2 doctor` clean.
- [ ] Capture Isaac build commit, Isaac Lab symlink, PyTorch cu13 version.
- [ ] Launch Isaac Sim once; run a stock sample; confirm physics + render.
- [ ] Enable `isaacsim.ros2.bridge`; publish a camera image from a sample scene;
      confirm with `ros2 topic echo` / RViz2 (image Reliability = **Best Effort**).
- [ ] Record the ROS-2-camera outcome (works? / quirks?) in `docs/ENVIRONMENT.md`.
- [ ] Decide: update the "5.1" references in CLAUDE.md/bible to 6.0.1?
- **Done when:** we know if the ROS 2 camera path works on this box; sandbox proven.

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
- **Follow-ups (small, anywhere)  ·  done 2026-07-21 (Track N):**
  - [x] `FaultReport` payload dataclass (`schema/pv_module.py`) shared by the
        run record's `fault_events` and the future ROS 2 `/mission/fault`
        topic; wired into `orchestrator/mission.py` + `run.py`; round-trip
        tested in `tests/test_fault_report.py`.
  - [x] `perception/cosmos_reason.py` skeleton targeting the local Qwen VLM
        (`:8000`, OpenAI-compatible) behind the `Perception` interface —
        HTTP call isolated behind a `ChatClient` protocol so it's testable
        with a fake client (`tests/test_cosmos_reason.py`); fails safe
        (escalates / reports `unknown`) on any parse or network failure.
        Frame→image encoding is a Track S TODO once real frames exist.
  - [x] `control/kinematic_math.py` — pure waypoint interpolation
        (`step_towards`/`reached`, no Isaac import), tested in
        `tests/test_kinematic_math.py`. Track S's `control/kinematic.py`
        (Isaac-bound) should import this rather than reimplementing the math.
  - [x] `docs/ROS2_CONTRACT.md` drafted (Workstream E prep, below) — locks the
        `/mission/fault` payload to `FaultReport`, plus namespacing/QoS/timing
        gotchas and one open question for Track S to decide.

---

## Workstream B — WORLD scene / farm_builder  ·  where: spark
**Satisfies:** produces a USD stage whose panels are readable by `schema` USD fns.
**Reuses:** `world/layout.py` (identical grid + seeded faults as the fake run).
- [ ] `world/farm_builder.py`: entry `python.sh -m solar_twin.world.farm_builder configs/farm.yaml`.
  - [ ] Create stage; **assert Z-up + meters** (verify 6.0 stage API).
  - [ ] For each `FarmLayout` site: define panel prim + `schema.create_panel`
        (stamp `pv:` attrs incl. `geo_position`), place a textured box.
  - [ ] Apply seeded faults: set `pv:state`, add an **emissive signature** for
        hotspot/soiled (the visual the confirm pass reads).
  - [ ] Add semantic labels to faulted panels (verify 6.0 semantics API) so
        Replicator could read them later.
  - [ ] Write stage to `assets/` or a `--out` path (gitignored `.usd`).
- [ ] Test (spark smoke): build, reopen, assert 10 panels + states via `schema.read_panel`.
- **Done when:** the builder produces a USD with N queryable panels; states on prims.

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
- [x] Pure-python interp math (`control/kinematic_math.py`: `step_towards`,
      `reached`, `steps_to_reach`) — unit-tested `anywhere` (no Isaac), done
      2026-07-21 (Track N). Handles overshoot clamping + yaw wraparound.
- [ ] `control/kinematic.py` (Isaac-bound): wraps `kinematic_math.step_towards`
      around an actual Xform prim per tick; `at_goal` can delegate to
      `kinematic_math.reached`. Do not reimplement the interpolation math here.
- [ ] Ground base asset (simple: Jetbot/Carter v1) + a drone Xform + camera.
- **Done when:** both robots reach each panel's waypoints in sim.

---

## Workstream E — ROS 2 seam  ·  where: spark  ·  **[!] blocked on WS0 ROS 2 install**
**Satisfies:** `Transport` (`transport/ros2_bridge.py`), same interface as sim_native.
- [x] `docs/ROS2_CONTRACT.md` drafted (Track N, 2026-07-21) — topic table,
      `/mission/fault` = `FaultReport` payload, namespacing, QoS + timing
      gotchas, and one open question (`read_panel` over ROS 2) for Track S.
- [ ] After Jazzy install + Day-1 camera check passes: build `ros2_bridge.py`
      (camera sub, `cmd_vel` pub, `pose`, `/mission/fault`) per
      `docs/ROS2_CONTRACT.md` — resolve its open question first.
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
