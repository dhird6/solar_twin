# TASKS — live per-person tracker (Normal machine vs DGX Spark)

> Referenced by `CLAUDE.md` §10 ("keep a `docs/TASKS.md` for live work so sessions
> resume cleanly"). This is `plan.md`'s checklist **re-cut by owner/machine**
> instead of by workstream, so two people can work the same day without
> touching the same files. Specs are unchanged — `docs/PROJECT_BIBLE.md` §8,
> `plan.md`, `docs/ENVIRONMENT.md`. Update the `[ ]` boxes here **and** in
> `plan.md` when something completes (same commit).

## Why two tracks, not one

Only one box in this project can import `pxr`/`omni` (Isaac Sim's bundled
Python on the aarch64 DGX Spark — see `docs/ENVIRONMENT.md`). Everything else
— schema logic, interfaces, the FSM, tests, docs, config — is plain Python
3.10+ and runs on **any machine, including yours**. The repo is already split
on this exact line (`CLAUDE.md` golden rule #2), so the two-person division
falls directly out of the code layout instead of being an arbitrary task split.

| | **Track N — Normal machine (you)** | **Track S — DGX Spark (teammate)** |
|---|---|---|
| Needs | Python 3.10+, `pytest`, `pyyaml`. Nothing else. | The Spark: aarch64, CUDA 13, Isaac Sim `6.0.1-rc.7`, `./python.sh`. |
| Touches | `src/solar_twin/schema/`, `perception/` (non-Isaac files), `control/base.py` + pure-math parts of `control/kinematic.py`, `orchestrator/`, `tests/`, `configs/`, `docs/` | `src/solar_twin/world/`, `transport/sim_native.py`, `transport/ros2_bridge.py`, Isaac-only parts of `control/kinematic.py` |
| Verifies with | `pytest` (no GPU) | manual smoke test on the Spark (headless run + `runs/<ts>/results.json`) |
| Cannot do here | Anything importing `pxr`/`omni`/`isaacsim` — will simply fail to import off the Spark. Don't try to work around this; it's the intended boundary. | — |

**Rule of thumb:** if a task's `plan.md` line says `where: anywhere`, it's
Track N. If it says `where: spark`, it's Track S. Two lines are split
(marked below) because part of the work is pure math and part needs Isaac.

---

## Track N — Normal machine (you)

Setup once:
```bash
pip install --break-system-packages --user pytest pyyaml   # or a venv, your call
cd solar_twin
pytest                                                       # should show 31 passed
PYTHONPATH=src python3 -m solar_twin.run configs/farm.yaml configs/mission.yaml --backend fake
```

### N1 — `FaultReport` payload dataclass  ·  plan.md Workstream A follow-up
**Owner interface:** a plain dataclass, shared later by the run record writer
(`run.py`) and the ROS 2 seam (`transport/ros2_bridge.py`'s `/mission/fault`
topic, §6.3 of the bible) — so both serialize the exact same shape.
- [ ] Add `FaultReport` to `src/solar_twin/schema/pv_module.py` (or a new
      `schema/fault_report.py` if it gets big): `panel_id`, `fault_type`,
      `confidence`, `note`, `timestamp`, `panel_geo_position`.
- [ ] `run.py` build its `results.json` entries from `FaultReport` instances
      (not ad-hoc dicts) so the shape is enforced in one place.
- [ ] Unit test: round-trip a `FaultReport` to dict/JSON and back.
- **Done when:** `orchestrator/mission.py`'s escalation path emits
  `FaultReport`s and a test asserts the shape; `docs/ROS2_CONTRACT.md` (below)
  can then just say "payload = `FaultReport`" instead of inventing JSON shape twice.

### N2 — `perception/cosmos_reason.py` skeleton  ·  plan.md Workstream A follow-up
**Owner interface:** `Perception` (`assess`/`diagnose`, `perception/base.py`) —
must be a drop-in swap for `ground_truth.py` with zero orchestration changes.
- [ ] `perception/cosmos_reason.py`: a class implementing `Perception` that
      calls the local Qwen2.5-VL-72B server (`docs/ENVIRONMENT.md`,
      OpenAI-compatible, `:8000`) via HTTP — **but stub/mock the HTTP call**
      here, since that server only exists on the Spark. Use `requests` or
      stdlib `urllib` behind a small client you can fake in tests.
  - [ ] Constructor takes `base_url`, `model_name`, `timeout` — defaults match
        `docs/ENVIRONMENT.md` (`http://localhost:8000`, `qwen2.5-vl-72b`).
  - [ ] `assess`/`diagnose` build a prompt from `PanelContext` + encode `frame`
        (skip real image encoding for now — accept `None`/placeholder frames
        in tests); parse the response into `Verdict`/`Diagnosis`.
- [ ] Test with a fake HTTP client (no network) asserting prompt shape and
      response parsing — this is exactly the "logic without the simulator"
      principle (bible §2.6), just applied to the VLM call instead of Isaac.
- **Done when:** the class satisfies `Perception`, imports with no network/GPU,
  and a swap in `mission.yaml` (`perception: cosmos_reason`) is a config flip
  once the Spark half wires it in — Track S does NOT need to write this file,
  only point `run._build_backend`/whatever wiring exists at it later.

### N3 — `control/kinematic.py` — the pure-math half  ·  plan.md Workstream D
**Owner interface:** `RobotControl` (`move_to`/`at_goal`, `control/base.py`).
Split this file in two conceptually even if it's one file: interpolation math
(Track N) vs. the actual Xform prim it moves (Track S, needs `pxr`).
- [ ] Write the waypoint interpolation as a **pure function** taking
      `(current_pose, target: Waypoint, speed, dt) -> next_pose`  — no Isaac
      import, testable with plain floats.
- [ ] `at_goal(...)` tolerance check — also pure math.
- [ ] Tests: straight-line interp reaches target within N steps; `at_goal`
      tolerance edge cases; yaw wraparound if you implement rotation.
- **Handoff to Track S:** they import your pure function inside the class that
  actually owns an Xform prim and calls `prim.GetAttribute(...).Set(...)` each
  step — they should not need to touch the math, only wire it to USD.
- **Done when:** the interp function + tests exist and are Isaac-free; leave a
  one-line note in this file's docstring for Track S on how to wire it to a prim.

### N4 — `docs/ROS2_CONTRACT.md` draft  ·  plan.md Workstream E prep
Referenced by `CLAUDE.md` and the bible (§6.3) but not yet created.
- [ ] Create it: expand the §6.3 topic table (topic, type, direction, QoS) into
      a full contract — add message field definitions for `/mission/fault`
      once N1's `FaultReport` shape is locked, namespacing convention,
      and the Best-Effort/Reliable QoS rule with the RViz2 gotcha spelled out.
- [ ] Mark it explicitly: "seam validated once Track S completes WS0's Day-1
      camera check; do not implement `ros2_bridge.py` against this until then."
- **Done when:** Track S can implement `transport/ros2_bridge.py` straight
  from this doc without guessing message shapes.

### N5 — Docs upkeep (ongoing, either track can also do this)
- [ ] Keep `SESSIONS.md` current — newest entry on top, one paragraph per session.
- [ ] Update `plan.md` `[ ]`→`[x]` in the same commit as the code that finishes it.
- [ ] If a spec here and the code disagree, fix one in the same commit
      (repo-wide rule, `CLAUDE.md` line 1).

---

## Track S — DGX Spark (teammate)

Setup once (per `docs/ENVIRONMENT.md`):
```bash
source /opt/ros/jazzy/setup.bash   # after WS0 install
/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh -m solar_twin.world.farm_builder configs/farm.yaml
```

### S1 — WS0: Environment / Day-1 de-risk  (blocks S2–S5)
- [~] Install ROS 2 Jazzy (`tools/install_ros2_jazzy.sh`), then `ros2 doctor` clean.
- [ ] Capture Isaac build commit, Isaac Lab symlink, PyTorch cu13 version → `docs/ENVIRONMENT.md`.
- [ ] Launch Isaac Sim once; run a stock sample; confirm physics + render.
- [ ] Enable `isaacsim.ros2.bridge`; publish a camera image; confirm with
      `ros2 topic echo` / RViz2 (Reliability = **Best Effort**).
- [ ] Record the ROS 2 camera outcome in `docs/ENVIRONMENT.md`'s checklist.
- [ ] Decide + update the stale "Isaac Sim 5.1" references in `CLAUDE.md`/bible
      to `6.0.1-rc.7` (verified discrepancy already logged there).
- **Done when:** known whether the ROS 2 camera path works; sandbox proven.

### S2 — WS B: `world/farm_builder.py`
**Satisfies:** a USD stage whose panels round-trip through
`schema/pv_module.py`'s USD fns. **Reuses:** `world/layout.py` (already built,
Isaac-free, shared with `run.py --backend fake` so sim and tests get identical
panels/faults — do not reimplement the grid/fault logic here, import it).
- [ ] Entry point `python.sh -m solar_twin.world.farm_builder configs/farm.yaml`.
- [ ] Create stage; assert Z-up + meters (verify against 6.0 stage API, not 5.1).
- [ ] Per `FarmLayout` site: define panel prim + `schema.create_panel` (stamp
      `pv:` attrs incl. `geo_position`); place a textured box.
- [ ] Seeded faults → `pv:state` + emissive signature for hotspot/soiled.
- [ ] Semantic labels on faulted panels (verify 6.0 semantics API).
- [ ] Write stage to `assets/` or `--out` path (gitignored `.usd`).
- [ ] Smoke test: build, reopen, assert 10 panels + states via `schema.read_panel`.

### S3 — WS C: `world/sim_runtime.py` + `transport/sim_native.py`
- [ ] `sim_runtime.py`: launch `SimulationApp` (headless option), load the
      built stage, add a camera render-product, step the world.
- [ ] `transport/sim_native.py` implements `Transport`: `capture` (render
      product/annotator, ⚠ verify 6.0 API), `pose` (read prim xform),
      `read_panel`/`write_panel` (via `schema` USD fns), `step`.
- [ ] Wire into `run._build_backend` (replaces the current `NotImplementedError`).

### S4 — WS D: `control/kinematic.py` — the Isaac half
- [ ] Import Track N's pure interpolation function (N3); wrap it around an
      actual Xform prim + `.Set()` calls per tick.
- [ ] Ground base asset (Jetbot/Carter v1) + drone Xform + camera.
- [ ] Smoke test: both robots reach each panel's waypoints in sim.

### S5 — WS E: ROS 2 seam  (blocked on S1)
- [ ] After Jazzy install + Day-1 camera check passes: build
      `transport/ros2_bridge.py` from `docs/ROS2_CONTRACT.md` (N4) — camera
      sub, `cmd_vel` pub, `pose`, `/mission/fault`.
- [ ] Smoke-test against the sim if the camera path works; else keep it a
      validated-but-unused stub and log why in `docs/ENVIRONMENT.md`.

### S6 — WS F: Integration + run record
- [ ] `python.sh -m solar_twin.run configs/farm.yaml configs/mission.yaml`
      (`--backend sim_native`) drives the full thread end to end.
- [ ] Confirm USD reflects updated states + inspection logs after a run.
- [ ] Assert run-record detection == injected (ground truth ⇒ 100%).
- [ ] (Optional) capture a video/gif.
- [ ] Update `README.md`, `SESSIONS.md`, bible/`CLAUDE.md` with learnings.

---

## Handoff points (where the two tracks touch)

1. **N1 → S6:** `FaultReport` shape must land before `run.py`'s writer and the
   Spark's real run both serialize results the same way. Land N1 early.
2. **N3 → S4:** Track S imports Track N's pure interp function rather than
   rewriting it — keep the function's signature stable once written; if it
   must change, flag it (Track S is depending on it).
3. **N4 → S5:** `ros2_bridge.py` should not start until `docs/ROS2_CONTRACT.md`
   is drafted, so Track S isn't guessing message shapes mid-implementation.
4. **N2 stays inert until Track S** wires `perception: cosmos_reason` into
   whatever builds the `Perception` instance from `mission.yaml` — that wiring
   itself is a small S-track task not yet in `plan.md`; add it under S6 once
   S1–S5 land.

## Branch convention (observed in this repo)
Existing history uses `ID_<n>--<Short-Description>` branch names merged via PR
(`git log`: `slice0-brain-spine` → PR #1 into what's now `ID_1--Project-Setup`).
Keep using one branch per workstream/task above (e.g. `ID_2--fault-report-dataclass`,
`ID_3--cosmos-reason-skeleton`) so Track N and Track S PRs never touch the same
files and merges stay conflict-free.
