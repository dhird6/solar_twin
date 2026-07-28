# TASKS — live per-person tracker (Normal machine vs DGX Spark)

> Referenced by `CLAUDE.md` §10 ("keep a `docs/TASKS.md` for live work so sessions
> resume cleanly"). This is `plan.md`'s checklist **re-cut by owner/machine**
> instead of by workstream, so two people can work the same day without
> touching the same files. Specs are unchanged — `docs/PROJECT_BIBLE.md` §8,
> `plan.md`, `docs/ENVIRONMENT.md`. Update the `[ ]` boxes here **and** in
> `plan.md` when something completes (same commit).

## ⇢ NEXT SESSION — start here (updated 2026-07-28, Session 11c)

Everything below this block is the older two-track plan and is still valid; this
is just the current front of work. Full detail in `SESSIONS.md` Sessions 10d-11b.

**State:** branch `ID-2-Layout-Integration` — **integrated**: Sessions 10e, 11 and
11b are merged in (PRs #7 and #8), so there is ONE trunk again rather than three
divergent worktrees. **247 Isaac-free tests**, 4 skipped. ⚠ Still **no PR to `main`**,
now 26 commits ahead of it — that is the largest outstanding structural item. The twin runs on the real Khavda
BLOCK-02 layout, on **real Copernicus GLO-30 terrain**, with a **real KPI-03
number: 0.00 false faults on 560 healthy panels** under a verified on-panel
tracker-self-shading stimulus (`runs/20260727T183423`).

**Done since the last list:**
- ~~Real DEM~~ ✅ (was item 2) — Session 10d. Plus turbines, serpentine routing,
  cruise speeds.
- **Status tour video** ✅ — Session 10e. `world/plant_tour.py` renders
  `assets/plant_status_tour.mp4`: the plant with every shot labelled built /
  inferred / not-modelled, closing on the backlog below.
- **Turbine siting, roads on the grade, fleet scale** ✅ — Session 11 (PR #8).
  Turbines were a lattice; `world/siting.py` sites them under a wake ellipse
  (7D x 4D), `lattice_score` 1.00 -> 0.40. Roads were single flat quads floating up
  to 0.87 m off the real DEM; now segmented and sampled. Fleet derives from named
  real platforms (`world/fleet_specs.py`) — the rover had measured 26% too wide.
  ⚠ `build_keepouts` now needs `layout` threaded in, or a scattered field enforces
  no-fly volumes at the OLD turbine positions.
- **CAD ingest audited** ✅ — Session 11b (`tools/audit_layout.py`). BLOCK-02 is
  **100% ingested**: 273 tables / 30,016 modules, reconciled entity-for-entity
  against the PDF (residual 6 = the DETAILS legend swatches), 0 overlaps, 0 missing
  dimensions. ⚠ The real scope limit: we hold **one ~18 MWdc block of a 567.5 MW
  plot**, and the overall master DWG has **no per-table geometry**, so more blocks
  need their own DC drawings exported to DXF. Neither DWG is parseable on this box
  (AC1032, no converter, `libredwg-tools` absent from the noble repos).
- **Session 10d's "video path is too expensive" is resolved and its diagnosis was
  wrong.** The render costs ~0.71 s/frame and that is FLAT with altitude (measured,
  4 poses, 540p and 720p) — the cost is frame COUNT alone. `--budget-minutes`
  projects and shortens loudly. ⚠ Budget off the **end-to-end** 0.92 s/frame
  (render + overlay + encode, measured 0.895 over a whole tour), not the 0.71
  render figure — that mistake under-promises by ~25%. Also fixed: the overview render
  product was hardcoded to 960x540, so `flythrough.py --width/--height` had been
  silently doing nothing.
- ⚠ Stale-entry warning that keeps recurring: this block claimed "nothing pushed"
  and listed the DEM as to-do for a day after both were false. Check `git log`
  and `SESSIONS.md` before trusting it.

**Do these first, in this order:**

0. **⚠ Quantify VLM run-to-run variance before quoting any KPI as a constant.**
   Two identical 24-panel runs (2026-07-28) disagreed on one panel: R258-C013
   diagnosed `soiled` in one and `hotspot` in the other. `cosmos_reason.py` sends
   `temperature: 0.0` but vLLM clamps it to 0.01 and logs the substitution, and
   GPU batching is not bit-reproducible regardless. **The world is seeded; the
   model is not.** Either pin a sampling seed / force greedy decoding, or run
   each KPI N times and report a spread. Cheap to do and it gates the honesty of
   every number in `SESSIONS.md`.
1. **A second KPI-03 point at a lower sun** (`2026-06-21T01:30:00Z`: elevation
   10.7 deg, ~50% of each row shaded, whole scene dimmer). 02:00Z is the readable
   hard-shadow case and the model was untroubled by it — 01:30Z is where the
   shading-vs-defect distinction should start to break, and where "shadow"
   becomes confounded with "underexposed". Same scenario file, one timestamp
   change (and update the `test_solar.py` guard's expected band).
2. **Pegasus / PX4 flight dynamics (`FR-06`)** — its own investigation. Not
   installed, and v5.1.0 targets Isaac 5.1 against the installed 6.0.1.
3. **More balance-of-plant** — substation / control room, module-level torque
   tube and pile geometry, cable trenches. `world/site.py` is the place, and
   anything not in the drawing must be tagged `INFERRED` like the rest. The
   **graded civil surface** belongs here too: GLO-30 is a pre-grading DSM, so the
   twin's ground is the desert's shape, not the engineered pad's.
4. **`transport/ros2_bridge.py`** — does not exist. camera→ROS 2 is proven on this
   box (Session 2), the contract is written (`docs/ROS2_CONTRACT.md`), the file
   is not.

**Done 2026-07-28 (Session 10c), was items 2-4:**
- ~~Instancing / LOD (`IF-09`)~~ ✅ the full 273 tables now build: 2.25M prims →
  75,464, 85 s. Healthy panels reference one prototype; faulted ones stay unique.
- ~~PBR materials + HDRI sky~~ ✅ generated latlong sky on the `DomeLight` (one
  object is both background and fill), ground reaching the horizon with distance
  haze, and roads/fence/inverter stations via `world/site.py`.
  ⚠ **Do not "improve" this back into an emissive sky dome** — measured, it acts
  as a giant area light and turns the desert floor blue (R-B +16 → -38).

**Known ⚠ to resolve, not to forget:**
- `panel.mount_height: 1.5` in `configs/farm_khavda_block02.yaml` is a guess —
  needs the MMS/tracker datasheet.
- Module width `1.134 m` is inferred from pitch minus a standard ~14 mm gap.
  Self-consistent, but confirm against the module datasheet.
- Tracker **backtracking** is not modelled, so self-shading is worst-case.
- **Nadir viewpoint on a 60 deg tracker** sees the module heavily foreshortened.
  Realistic for the hazard, but not an inspection-optimal camera pose — a real
  survey would fly the panel normal. Worth a `mission.yaml` knob before drawing
  conclusions about detection at low sun.
- Reason-1's confirm-pass notes are **near-boilerplate** across panels. Fine for
  a 0/560 false-fault result, but do not read them as per-panel analysis.
- `tools/layout_from_pdf.py` deliberately **fails closed** — the PDF's two
  calibration sources disagree by 9.2%. Use the DXF path.
- **Camera framing on this stage is lens arithmetic — do not eyeball it.** Three
  separate attempts at the turbine shot failed (hazed lines at 453 m; a perfect
  turbine with the entire array below the frame at 38 m up aiming 18 deg up). A
  22 mm lens on the 36 mm aperture is a ~49 deg vertical field; work out where the
  frame's bottom edge meets the ground before rendering. `tour.look_at` exists so
  shots aim at a prim position instead of a guessed heading.
- **Turbine blades render very thin** and read faintly beyond ~200 m. Cosmetic,
  lives in `farm_builder`'s turbine geometry.

**Never score a shading stimulus on whole-frame brightness.** Twice now the frame
mean separated shaded from unshaded while the PANELS were identically lit — the
difference was dark ground in frame. Mask to PV-glass pixels
(`runs/20260727T183423/verify_shade.py`).

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
| Touches | `src/solar_twin/schema/`, `perception/` (non-Isaac files), `control/base.py` + `control/kinematic_math.py`, `orchestrator/`, `tests/`, `configs/`, `docs/` | `src/solar_twin/world/`, `transport/sim_native.py`, `transport/ros2_bridge.py`, `control/kinematic.py` (imports `kinematic_math.py`) |
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
pytest                                                       # 49 passed as of 2026-07-21
PYTHONPATH=src python3 -m solar_twin.run configs/farm.yaml configs/mission.yaml --backend fake
```

### N1 — `FaultReport` payload dataclass  ·  plan.md Workstream A follow-up  ·  **[x] DONE 2026-07-21**
**Owner interface:** a plain dataclass, shared later by the run record writer
(`run.py`) and the ROS 2 seam (`transport/ros2_bridge.py`'s `/mission/fault`
topic, §6.3 of the bible) — so both serialize the exact same shape.
- [x] Added `FaultReport` to `src/solar_twin/schema/pv_module.py`: `panel_id`,
      `fault_type`, `confidence`, `note`, `timestamp`, `panel_geo_position`,
      plus `to_dict()`/`from_dict()`.
- [x] `orchestrator/mission.py`'s `WRITEBACK` phase now emits `FaultReport`
      instances (`MissionResult.fault_events: list[FaultReport]`); `run.py`
      serializes them with `[e.to_dict() for e in result.fault_events]`.
- [x] Round-trip unit tests: `tests/test_fault_report.py` (dict, JSON,
      optional `panel_geo_position`); existing `tests/test_mission.py`
      assertions updated from dict-indexing to attribute access.
- **Verified:** `pytest` (49 passed) + a live `--backend fake` run whose
  `results.json.fault_events` shows the exact shape above.
- `docs/ROS2_CONTRACT.md` (N4, below) now says "payload = `FaultReport`"
  instead of inventing the JSON shape a second time.

### N2 — `perception/cosmos_reason.py` skeleton  ·  plan.md Workstream A follow-up  ·  **[x] DONE 2026-07-21**
**Owner interface:** `Perception` (`assess`/`diagnose`, `perception/base.py`) —
a drop-in swap for `ground_truth.py` with zero orchestration changes.
- [x] `perception/cosmos_reason.py`: `CosmosReasonPerception` implements
      `Perception`, talks to an OpenAI-compatible `/v1/chat/completions`
      endpoint (Qwen2.5-VL-72B on `:8000` per `docs/ENVIRONMENT.md`) via a
      `ChatClient` protocol. Real HTTP goes through `_HttpChatClient` (stdlib
      `urllib`, no new dependency, network only touched inside `.complete()`).
  - [x] Constructor: `base_url`, `model`, `timeout`, `client` — defaults match
        `docs/ENVIRONMENT.md`.
  - [x] `assess`/`diagnose` build a text prompt from `PanelContext` (taxonomy
        spelled out for `diagnose` so the model can't invent a fault type);
        frame image-encoding is left a `# TODO (Track S...)` for when a real
        camera frame exists — Slice 0 tests pass `frame=None`.
  - [x] Fail-safe parsing: any HTTP exception, malformed JSON, or JSON
        wrapped in prose is tolerated (`_parse_json_response` extracts the
        first `{...}` block); an unparseable `assess` escalates
        (`status="suspect"`) rather than silently clearing a panel, and an
        invalid `diagnose` fault type reports `unknown`.
- [x] `tests/test_cosmos_reason.py`: fake `ChatClient`, no network — asserts
      prompt content, clean/suspect parsing, prose-wrapped JSON extraction,
      and both fail-safe paths.
- **Not yet done (Track S, later):** wiring `perception: cosmos_reason` from
  `mission.yaml` into `run._perception()` (currently only `ground_truth` is
  wired — see Handoff §4 below), and real frame→image encoding.

### N3 — `control/kinematic_math.py` — the pure-math half  ·  plan.md Workstream D  ·  **[x] DONE 2026-07-21**
**Owner interface:** `RobotControl` (`move_to`/`at_goal`, `control/base.py`).
Split into two files, not two halves of one file: `kinematic_math.py` (Track
N, no Isaac) vs. the future `control/kinematic.py` (Track S, needs `pxr`,
wraps this module around an Xform prim).
- [x] `step_towards(current: Waypoint, target: Waypoint, speed, dt,
      angular_speed=pi) -> Waypoint` — pure function, clamped so it never
      overshoots position or yaw; yaw takes the shortest wrapped direction.
- [x] `reached(current, target, tol=0.05) -> bool` — position-only tolerance
      check (matches `FakeSimBackend.at_goal`).
- [x] `steps_to_reach(...)` helper (simulate to convergence; used by tests and
      usable by Track S for timing estimates).
- [x] `tests/test_kinematic_math.py`: straight-line + diagonal convergence,
      overshoot clamping, zero-speed non-convergence (capped, not infinite),
      tolerance edges, yaw wraparound (170°→−170° turns +20°, not −340°).
- **Handoff to Track S:** `control/kinematic.py` should `import
  step_towards, reached from kinematic_math` and wrap an Xform prim around
  it — not reimplement the math. Signature is the seam; flag before changing it.

### N4 — `docs/ROS2_CONTRACT.md` draft  ·  plan.md Workstream E prep  ·  **[x] DONE 2026-07-21**
Referenced by `CLAUDE.md` and the bible (§6.3); did not exist before this session.
- [x] Full topic table (topic, type, direction, QoS) plus namespacing
      convention (`/<robot_ns>/...` from `mission.yaml`'s `fleet:` ids),
      the Best-Effort/Reliable QoS split with the RViz2 "silently see
      nothing" gotcha, and the "OmniGraph only publishes after Play" timing
      gotcha.
- [x] `/mission/fault` payload locked to `FaultReport.to_dict()` (N1) — one
      shape, not invented twice, with a worked JSON example.
- [x] Explicit banner: do not implement `ros2_bridge.py` against this until
      Track S's WS0 Day-1 camera check passes.
- [x] One flagged open question for Track S to resolve when building the
      bridge: how `read_panel`/`write_panel` (USD-backed, not a real topic in
      the table) work when `Transport` is ROS 2 — recommendation given
      (keep as a direct side-channel), decision left to Track S.
- **Done when:** Track S can implement `transport/ros2_bridge.py` straight
  from this doc without guessing message shapes. *(Doc is ready; Track S
  still needs to actually build against it once WS0 unblocks.)*

### N5 — Docs upkeep (ongoing, either track can also do this)
- [x] `plan.md` boxes for the WS A follow-ups, WS D math half, and WS E prep
      flipped to `[x]` in the same session as the code (this pass).
- [ ] Keep `SESSIONS.md` current — newest entry on top, one paragraph per
      session (add the Track N session entry — see below).
- [ ] If a spec here and the code disagree, fix one in the same commit
      (repo-wide rule, `CLAUDE.md` line 1) — ongoing, not a one-time task.

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
- [ ] Import `step_towards`/`reached` from `control/kinematic_math.py` (N3,
      already built + tested); wrap them around an actual Xform prim +
      `.Set()` calls per tick — do not reimplement the interpolation math.
- [ ] Ground base asset (Jetbot/Carter v1) + drone Xform + camera.
- [ ] Smoke test: both robots reach each panel's waypoints in sim.

### S5 — WS E: ROS 2 seam  (blocked on S1)
- [ ] After Jazzy install + Day-1 camera check passes: build
      `transport/ros2_bridge.py` from `docs/ROS2_CONTRACT.md` (N4, drafted —
      resolve its §8 open question on `read_panel`/`write_panel` over ROS 2
      first) — camera sub, `cmd_vel` pub, `pose`, `/mission/fault`.
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

1. **N1 → S6 — READY.** `FaultReport` shape has landed (`schema/pv_module.py`,
   wired into `mission.py`/`run.py`, tested). Track S's real run already
   produces the same shape once `--backend sim_native` exists — nothing
   further needed from Track N here.
2. **N3 → S4 — READY.** `control/kinematic_math.py`'s `step_towards`/`reached`
   exist and are tested. Track S: import them into `control/kinematic.py`
   rather than rewriting the math; if the signature must change, flag it here
   (Track N depends on it staying stable for its tests).
3. **N4 → S5 — READY.** `docs/ROS2_CONTRACT.md` is drafted, including one
   open question (§8, `read_panel`/`write_panel` over ROS 2) Track S should
   resolve *before* writing `ros2_bridge.py`, not while guessing mid-build.
4. **N2 stays inert until Track S** wires `perception: cosmos_reason` into
   `run._perception()` (`src/solar_twin/run.py` — currently only
   `"ground_truth"` is a valid choice, `mission.yaml`'s `perception:
   cosmos_reason` would raise `NotImplementedError` today). That wiring is a
   small S-track task not yet in `plan.md`; add it under S6 once S1–S5 land.
   Track N also owes real frame→image encoding once a camera frame exists.

## Branch convention (observed in this repo)
Existing history uses `ID_<n>--<Short-Description>` branch names merged via PR
(`git log`: `slice0-brain-spine` → PR #1 into what's now `ID_1--Project-Setup`).
Keep using one branch per workstream/task above (e.g. `ID_2--fault-report-dataclass`,
`ID_3--cosmos-reason-skeleton`) so Track N and Track S PRs never touch the same
files and merges stay conflict-free.
