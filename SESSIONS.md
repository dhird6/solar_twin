# SESSIONS — running log

> Rolling context for new sessions. Newest entry on top. Keep it short: what the
> plan is, what got done, what's next, and any decisions/findings that aren't
> obvious from the code. Depth lives in `docs/`; this is the "where are we" file.

## The plan we're following
**Bible §8 Slice 0** — one seeded, scripted, headless run that inspects a row,
escalates on injected faults, writes verdicts back onto USD panels, and drops a
run record; orchestration covered by Isaac-free tests. It splits in two:
- **Brain half (pure-python, no Spark):** schema contract, the 3 interfaces
  (Perception/Transport/RobotControl), the escalation FSM, `FakeSimBackend`,
  configs, `run.py`, tests. → **Buildable + testable right here.**
- **World half (Isaac-bound, on the Spark):** `world/farm_builder.py`,
  `world/sim_runtime.py`, `transport/sim_native.py`, `transport/ros2_bridge.py`,
  plus the Day-1 ROS 2 de-risk. → **User runs on the Spark.**

---

## 2026-07-24 — Session 7: Environment realism + turbine keep-out + vision/specs ✅ (PR #2 merged to main)
**Done — all merged to `main` via PR #2. Made the sim world recognizable + safe, and wrote down where it's going.**
- **Render fix — the "featureless frame" bug.** The pre-fix Cosmos run saw flat
  colour swatches → `faults_detected 0/10`. Now panels are a real **PV cell grid
  + aluminium frame**; faults are **localized** (soiling = a dust patch, hotspot
  = 1–2 hot cells) via pure `fault_cells()`; directional **sun + shadows**; wider
  camera FOV; **panel-top-relative standoffs** (fixed the confirm camera landing
  *below* the panel — an abs-Z bug). Verified on real Isaac frames: reads
  unmistakably as a soiled/healthy PV module.
- **Terrain + turbines.** Deterministic `terrain_height()` (Isaac-free) → panels
  mount on the grade; heightfield **mesh** ground. Wind-turbine proxies
  (tower+nacelle+3 blades); `sim_runtime` spins each hub per step → blade shadows
  sweep the row (the false-fault stressor).
- **Turbine keep-out (no-fly), planning layer.** `world/keepout.py` (rotor-sphere
  ∪ tower, pure) + `control/safe.py::SafeControl` (clamps every waypoint, logs,
  tracks min clearance) + `run.py` `keepout` audit block + authored (inert) PhysX
  colliders + a translucent viz sphere. `FR-09` satisfied at the plan level,
  control-agnostic (protects kinematic today + PX4 later). Real plan clears
  turbines by **9.4 m**; a turbine on the row trips 14 waypoints.
- **Docs.** `docs/DIGITAL_TWIN_VISION_AND_RESEARCH.md` (13-agent research swarm —
  6-pillar architecture, hazard model, phased roadmap). `docs/specs/` (9
  traceable specs: FR/NFR/HAZ/KPI/SLICE/RISK). Reconciled specs to the shipped
  code and **fixed the CLAUDE.md version bug** (Isaac Sim 5.1 → **6.0.1** per
  `ENVIRONMENT.md`; Isaac Lab ⚠ verify).
- **Tests:** 66 passing (Isaac-free).

**State:** `main` current through the PR #2 merge. vLLM `vllm-cosmos` up on
`:8000` (served id `nvidia/cosmos-reason1-7b`). ⚠ **The render fix is NOT yet
validated end-to-end with Cosmos** — a soiled panel *looks* soiled and healthy
stays "clean" (0/6 shadow spot-test), but a full mission re-run to confirm Cosmos
now *detects* the soiled panels (vs the old 0/10) has not been done.

### Model-selection research (2026-07-24, web + HF): **Cosmos 3 Edge is the target**
- **Cosmos 3 Edge SHIPPED 2026-07-20** (SIGGRAPH) — an earlier read of "announced
  for later" was **stale**. `nvidia/Cosmos3-Edge` on HF: **3.86B**, arch
  `cosmos3_edge`, **license OpenMDW 1.1 (commercial OK)**, not gated.
  Sibling `nvidia/Cosmos3-Edge-Policy-DROID` is a real, downloadable
  **world action model** (robot-arm embodiments — Franka/UR — **not drones**, so
  not directly usable, but WAMs are no longer purely theoretical).
- **DGX Spark is an officially TESTED platform** for Edge (list: B200, H100, H20,
  RTX PRO 6000, DGX Station, **DGX Spark**, Jetson Thor, Jetson AGX Orin).
  Stronger evidence than Nano (whose vLLM recipe documents only H200/H100/A100;
  Spark support was a third-party report). Partially retires `RISK-04`.
- **Why Edge over Nano/Reason2 for us:** (1) vendor-tested on our exact box;
  (2) 4B → directly attacks `RISK-09` (unbenchmarked latency at inspection frame
  rates, feeds coverage/battery KPIs); (3) **it also runs on Jetson Thor/Orin —
  our SLICE-8 deploy target — so the twin tests the SAME perception model that
  will run on the robot**, de-risking `FR-24`/`RISK-17` (SIL→HIL parity).
  Reason2-8B is **gated on HF** *and* superseded → skip it, go 1 → 3.
  Nano (16B, reasoner tower ~17 GB) stays the quality-ceiling fallback.
- **⚠ BLOCKER found:** the running container `vllm/vllm-openai:cu130-nightly` is
  **vLLM 0.19.2rc1.dev134**; Cosmos 3 needs **≥ 0.21.0** on CUDA 13, and its
  arch registry lists **zero** cosmos3 architectures. So switching is a
  **container swap**, not just a weights download. `cu130-nightly` is a *moving
  tag* and the current one is our verified sm_121 escape from the NIM crash →
  **record the digest as a rollback point before pulling** (closes the Session-6
  "pin a vLLM digest" TODO). Disk is fine (2.7 TB free);
  `--gpu-memory-utilization 0.4` is already set for Isaac coexistence.
- **⚠ Integration risk:** Cosmos 3 is a *reasoning* model and may emit
  chain-of-thought before its answer; `cosmos_reason.py::_parse_json_response()`
  expects clean JSON. Budget a tolerance fix — otherwise a parsing failure will
  masquerade as "Edge is bad".

### ✅ Step 1 RUN (2026-07-24) — KPI-01 = **0/2 faults**, but the blocker is OURS, not the model's
Two runs (`runs/20260724T162536`, then `runs/20260724T164326` after the fix below).
Both: 10 panels inspected, **0/2 injected soiled panels detected**, detection_rate
0.80 (the 8 healthy panels are correct), ~75 s wall. Neither soiled panel even
escalated (`screen=clean`). **Three separate problems were isolated — which is
exactly why the control ran before any model swap:**
1. **Panel recognition — ✅ FIXED (the Session-7 render fix worked).** The VLM now
   says *"consistent grid pattern of dark blue photovoltaic cells, edges and
   structure intact"* vs the pre-fix *"plain beige background… not a photograph of
   a solar panel."*
2. **🐛 SELF-INFLICTED SENSOR BUG — FIXED.** The keep-out **viz spheres shadowed the
   whole farm**: display-translucent ≠ shadow-translucent, so two 9-10 m spheres at
   hub height **halved frame brightness (mean 126 → 46)** and crushed the contrast
   the dust patch depends on. Fix: author them `purpose = "guide"` (USD debug-only
   geometry, excluded from the default render) in `farm_builder.py`. Verified:
   R00-C002 screen 46 → **126**, R00-C000 96 → **137**. **+2 regression tests**
   (`tests/test_farm_builder_usd.py`, pxr-guarded): viz MUST be guide-purpose;
   turbines must NOT be (or they stop casting the blade shadows SLICE-3 needs).
   *Lesson: a debug aid silently corrupted the sensor path and would have been
   misread as "the model can't detect soiling."*
3. **❌ THE REAL BLOCKER — our soiling doesn't look like soiling.** On a clean,
   bright frame the model said: *"a consistent grid pattern of **blue and white
   squares**, indicating no visible signs of soiling."* **It SEES the pale cells and
   classifies them as a design pattern** — a fair reading of what we render:
   perfectly rectangular, fully opaque, uniformly beige cells snapped exactly to the
   cell grid. Real soiling is a **translucent film** — blue cell shows through,
   contrast drops, brownish tint, ragged edges, and it **does not respect cell
   boundaries**. Ours does, perfectly, which is what makes it read as designed.
   → **KPI-01=0 is NOT a Reason-1 capability verdict; it's our fidelity gap**, and
   precisely the `NFR-07` "no silent fidelity substitution" failure.
   *(Also ruled out: stale-frame-after-teleport — `sim_runtime` defines
   `_RENDER_SETTLE_UPDATES` but `capture()` never calls it; settled vs unsettled
   frames are byte-identical, so that is NOT the bug.)*

**→ Revised next step (do BEFORE any model A/B):** make the soiling physically
faithful — (a) **blend, don't replace** (semi-transparent dust film, blue cell
still reads underneath); (b) **ignore cell boundaries** (overlay on the module
surface, ragged edges, per-cell density falloff); (c) **physically-motivated
placement** (accumulation along the lower edge of the tilted panel). **Fix toward
realism, NOT toward making Cosmos say "soiled"** — tuning until the VLM agrees is
teaching to the test and would poison `KPI-03` later. Then re-baseline, then Edge A/B.

**Next (ordered):**
1. ~~**Baseline KPI-01 on Cosmos Reason 1**~~ — ✅ **DONE, see above.** Full `mission_cosmos`
   run on the fixed world: does it now catch the 2 soiled panels of 10 (vs the
   pre-fix **0/10**)? Server is already up at util 0.4 → zero setup. This is the
   **control**: it proves the render fix independent of model choice. Without it,
   an Edge failure tangles three unknowns (render fix? model? new container?).
2. **Pull a newer vLLM** (`cu130-nightly` re-pull or `vllm/vllm-omni:cosmos3`) +
   download `nvidia/Cosmos3-Edge` (~8 GB). Can start downloading during step 1.
   ✅ **ROLLBACK POINT RECORDED (2026-07-24)** — the currently-working sm_121
   container is `vllm/vllm-openai:cu130-nightly`, repo digest
   **`sha256:3dbe092ec5b2cef63b6104d33fa75d6ce53a7870962529ada69f78bbbc38e776`**
   (local image id `ffa30d66ff5c`, 23.3 GB, ~3 months old). `cu130-nightly` is a
   MOVING tag — if a re-pull regresses sm_121, restore with:
   `docker pull vllm/vllm-openai@sha256:3dbe092ec5b2cef63b6104d33fa75d6ce53a7870962529ada69f78bbbc38e776`
   Current serve args: `--served-model-name nvidia/cosmos-reason1-7b
   --trust-remote-code --max-model-len 32768 --gpu-memory-utilization 0.4
   --max-num-seqs 4`.
3. **A/B: serve Edge reasoner-only, re-run the identical scenario.** Compare
   KPI-01 + per-panel latency vs the Reason 1 baseline (and optionally Nano 16B
   as the quality ceiling). **If Edge ties or wins → it becomes the default and
   Reason 1 is dropped** (one model, Spark + Jetson, deploy parity).
   Serving form: `--hf-overrides '{"architectures": [...ReasonerForConditionalGeneration]}'`
   (reasoner tower only — we don't need the generator; that's burst-out).
4. **Then SLICE-3 core — the false-fault harness (the thesis: KPI-03 / HAZ-07).**
   Minimal `configs/scenarios/` surface (SC-05 `sweeping_shadow`), sun-angle /
   shadow-severity knob, sweep **including the worst-case hard shadow bisecting
   the cells** (the 0/6 was moderate only), compute KPI-03 into the run record.
   Needs no Pegasus — shadows + Cosmos work today.
5. **Parallel de-risk RISK-02 — cheap, no commitment.** Smoke-test that
   Pegasus/PX4 SITL launches on this aarch64/sm_121 Isaac 6.0.1 box. Unblocks
   SLICE-2 (physics that bites) later without diving into the full build now.

**Doc corrections owed** (from the research above): Cosmos 3 Edge is **released**,
not "announced for later"; sizes are **Edge 4B / Nano 16B / Super 64B** built on
dense **2B / 8B / 32B** transformers (reconciles `STACK.md`'s "~2B" vs the research
doc's "~4B" — both were half-right); **DGX Spark is vendor-tested** for Edge;
WAMs now have a concrete checkpoint. Update `STACK.md`, `docs/specs/01`,
`docs/specs/08` (`RISK-04`), and `DIGITAL_TWIN_VISION_AND_RESEARCH.md`.

## 2026-07-21 — Session 6: Cosmos Reason live on the GB10 ✅ (real VLM perception)
**Done — the "cheat" detector is now the real thing.**
- **Code wiring:** `cosmos_reason.py` frame-encoding TODO resolved. New
  `_frame_to_data_url()` turns the `H×W×{3,4}` uint8 frame from
  `Transport.capture` into a PNG `data:` URL; `_messages()` attaches it as an
  OpenAI `image_url` part. Fails soft (no frame/codec → text-only). numpy/PIL/
  imageio all lazy — module still imports Isaac-free. +4 tests (RGBA/RGB/
  malformed/none). Full suite **56 passed, 1 skipped**.
- **Served the model on the Spark.** ⚠ **The Cosmos Reason NIM does NOT run on
  GB10.** `nvcr.io/nim/nvidia/cosmos-reason1-7b:1.4.0`/`:1.4.1` load weights then
  crash in vision-encoder profiling: `sm_121 ... LLVM ERROR: Cannot select
  llvm.nvvm.shfl.sync.bfly.i32` (bundled Triton/LLVM compiled only ≤ sm_120;
  known ecosystem issue — vLLM #36821, NVIDIA DGX Spark forum).
  `NIM_DISABLE_CUDA_GRAPH=1` didn't help. **Fix: mainline vLLM
  `vllm/vllm-openai:cu130-nightly` (sm_121a)** serving the bf16 HF weights the NIM
  had already cached (`~/.cache/nim/ngc/hub/models--nim--nvidia--cosmos-reason1-7b`,
  rev `1.1-bf16-hf`; mount the whole repo dir — files are symlinks into blobs).
  Full recipe in `docs/ENVIRONMENT.md` → "Serving Cosmos Reason on the Spark".
- **Verified live:** container `vllm-cosmos` on `:8000`, served id
  `nvidia/cosmos-reason1-7b`. Ran `CosmosReasonPerception` (real HTTP + image
  payload) against a synthetic panel frame → model described the image and
  returned parseable `Verdict`/`Diagnosis`. Pipeline confirmed (detection
  accuracy on real frames is future work).
- **Config:** `mission.yaml` `perception_opts` now points at the local server
  (timeout 120s); default kept `perception: ground_truth` (works w/o GPU) — flip
  to `cosmos_reason` when the server's up.

**State:** vLLM container `vllm-cosmos` running (holds ~98 GB unified @ util 0.85).
NGC key staged at `~/.ngc_api_key` (0600). Changes NOT yet committed.
**Next:** (1) commit this work on a branch; (2) drive `sim_native` + `cosmos_reason`
together — **lower vLLM `--gpu-memory-utilization` to ~0.4 first** or Isaac Sim OOMs
on the shared GB10; (3) detection tuning on real sim frames; (4) pin a vLLM digest.

## 2026-07-21 — Session 5: Integrate Track N (teammate) into main
Merged `ID_1--Project-Setup` (Track N, normal-machine work) into the DGX branch
on an integration branch. Kept both halves: my Isaac world (farm_builder,
sim_runtime, sim_native, kinematic, artifacts) + their Brain follow-ups
(FaultReport, cosmos_reason, kinematic_math, ROS2_CONTRACT.md, TASKS.md, tests).
Fixes folded in during the merge: (1) `control/kinematic.py` now imports their
`kinematic_math.py` (the N3→S4 handoff); (2) `cosmos_reason.py` retargeted from
the local Qwen VLM → **Cosmos Reason** (per direction — Cosmos-only). Validated
with full `pytest` + a `--backend sim_native` smoke run before landing to main.

## 2026-07-21 — Session 4: Workstream C — sim loop runs end-to-end ✅ (Slice 0 gate MET)
**Done:** `world/sim_runtime.py` (SimulationApp + open farm USD + robots w/ downward
cameras + step/render/pose/capture), `transport/sim_native.py` (Transport on the
live stage), `control/kinematic.py` (teleport RobotControl, pure-python + unit test),
wired `sim_native` into `run._build_backend` (+ `--farm-usd/--gui/--width/--height`).
**Full mission runs on the real USD world:** `./python.sh -m solar_twin.run ...
--backend sim_native` → 10 panels, 2 faults escalated (R00-C002, R00-C009 soiled),
**detection_rate 1.00**, sim_native run record written. Robots move to each panel,
drone camera grabs real RGB (mean ~154), verdicts written back to USD prims.
→ **Slice 0 gate MET** (bible §8 one-liner). 33 pytest + 1 pxr-skip.

**Findings / bugs fixed this session:**
- Standalone replicator annotators are filled by **`rep.orchestrator.step(rt_subframes,
  pause_timeline=False)`**, NOT bare `app.update()` (capture returned None/empty until this).
- **`SimulationApp.close()` terminates the process** → write+print the run record
  BEFORE closing (it was being lost in a `finally` that closed the app first).
- Camera-on-drone: mount the camera below the marker cube or it renders the cube
  interior → all-black frame (mean 0).
- Benign warning: usdrt/Fabric can't populate `pv:inspection_log` (string array);
  the pxr stage write is unaffected.

**Next (polish / Phase 1 on-ramp):** optional `--save-usd` to persist post-run
stage; capture a demo video; swap ground-truth perception → `cosmos_reason.py`
against the local Qwen VLM (bring the vLLM service back first); update bible/CLAUDE
"5.1"→"6.0.1". Bigger: real panel assets + many rows (Phase 1).

## 2026-07-21 — Session 3: Workstream B — farm_builder (USD world)
**Done:** `world/farm_builder.py` — authors the USD farm from `farm.yaml`, reusing
`world/layout.py` so grid + seeded faults match the fake run (seed 20260721 →
R00-C002, R00-C009 soiled, same as `--backend fake`). Pure pxr, **no SimulationApp**
(fast). Per panel: `schema.create_panel` (pv: attrs + geo_position) + tilted box
mesh + UsdPreviewSurface material (hotspot=emissive red, soiled=tan) +
`UsdSemantics.LabelsAPI` label. Asserts Z-up/meters. Output `assets/farm.usd`
(gitignored). Verified by reopening: 10 queryable panels, 2 faults, labels
`[panel, soiled]`, material bound → **PASS**.
- **Schema bug fixed:** `pv:grid_index` Int2 must be set via `Gf.Vec2i` — a bare
  tuple made USD infer GfVec2d and raise. Pure tests can't catch it (no usd-core
  on aarch64); caught by running under `./python.sh`. Added `tests/test_schema_usd.py`
  (pxr-guarded; skips on aarch64, runs in x86 CI / Isaac python). 31 passed, 1 skip.
- **Verified 6.0 APIs:** semantics = `isaacsim.core.experimental.utils.semantics.
  add_labels` (or pure `pxr.UsdSemantics.LabelsAPI`); OpenUSD 0.25.5.

**Next:** Workstream C — `world/sim_runtime.py` (load `assets/farm.usd` into a
SimulationApp, add a camera render-product, step) + `transport/sim_native.py`
(capture/pose/read_panel/write_panel/step) + `control/kinematic.py`. Then wire
`sim_native` into `run._build_backend` for the full on-Spark mission.

## 2026-07-21 — Session 3 (Track N, parallel): Brain follow-ups + ROS2_CONTRACT
Split work by machine this session: `docs/TASKS.md` re-cuts `plan.md`'s
checklist into **Track N (normal machine, no Isaac)** and **Track S (DGX
Spark)** so both people can work without touching the same files. This entry
covers Track N's pass — all pure-python, done off the Spark.

**Done (49 pytest tests green, up from 31; verified with a live `--backend
fake` run):**
- `FaultReport` dataclass (`schema/pv_module.py`) — the payload shape now
  shared by the run record's `fault_events` and the future ROS 2
  `/mission/fault` topic. Wired into `orchestrator/mission.py`'s `WRITEBACK`
  phase and `run.py`'s record writer; round-trip tested
  (`tests/test_fault_report.py`).
- `perception/cosmos_reason.py` — `CosmosReasonPerception`, a `Perception`
  impl targeting the local Qwen2.5-VL-72B server behind a `ChatClient`
  protocol (stdlib `urllib`, no new dependency, network only touched inside
  `.complete()`). Fails safe: unparseable/garbage responses escalate rather
  than clearing a panel. Tested with a fake client, no network
  (`tests/test_cosmos_reason.py`). **Not yet wired** into `run._perception()`
  — `mission.yaml`'s `perception: cosmos_reason` still raises
  `NotImplementedError` until someone adds that branch.
- `control/kinematic_math.py` — pure waypoint interpolation (`step_towards`,
  `reached`, `steps_to_reach`), Isaac-free, clamped against overshoot with
  shortest-path yaw wraparound. Tested (`tests/test_kinematic_math.py`). The
  Isaac-bound `control/kinematic.py` (Track S) should import this rather than
  reimplementing the math.
- `docs/ROS2_CONTRACT.md` — didn't exist before; full topic table,
  `/mission/fault` locked to `FaultReport`, namespacing, the Best-Effort/
  RViz2 QoS gotcha, the Play-before-publish timing gotcha, and one flagged
  open question (`read_panel`/`write_panel` over ROS 2) for Track S.
- `docs/TASKS.md` (new) + `plan.md`/`CLAUDE.md` updated to check off the
  above and point at the new files.

**Git:** merged `origin/main` (Track S's Session 2 Day-1 findings) into
`ID_1--Project-Setup` — no conflicts, disjoint file sets. Local commits not
yet pushed as of this entry.

**Next (Track S, on the Spark):** WS0 remaining boxes (Isaac launch/render
smoke test, ROS 2 camera publish check), then WS B (`farm_builder.py`) reusing
`world/layout.py` unchanged. When WS D lands, import `kinematic_math.py`
rather than rewriting it. When WS E lands, resolve `docs/ROS2_CONTRACT.md`'s
open question before writing `ros2_bridge.py`.

---

## 2026-07-21 — Session 2: Day-1 de-risk + ROS 2 install
**Environment verified on the Spark (see `docs/ENVIRONMENT.md`):**
- aarch64 · CUDA **13.0** · GB10 · Isaac Sim **6.0.1-rc.7** (⚠ NOT 5.1 — verify
  APIs vs 6.0) · `python.sh` at `IsaacSim/_build/linux-aarch64/release/`.
- **ROS 2 was absent** (no `/opt/ros`). Ubuntu **24.04 noble** → **Jazzy** is the
  match, and Isaac 6.0's bridge bundles `jazzy` + `humble` internal libs.
- Isaac Sim **not running** (live MCP refused) → sandbox render proof still TODO.
- Local **Qwen2.5-VL-72B** vLLM on `:8000` (future `cosmos_reason.py` backend).

**Actions:**
- Wrote `plan.md` (divided workstreams A–F + Day-1 checklist).
- User granted passwordless sudo (Option B, `/etc/sudoers.d/99-simulationhub-nopasswd`).
- Wrote `tools/install_ros2_jazzy.sh`; installing **ros-jazzy-desktop + ros-dev-tools**.
- **Lesson / near-miss:** first script version had `apt-get upgrade -y` → started a
  359-pkg full-system upgrade (CUDA/nvidia/docker/systemd). Aborted in the
  download phase (nothing installed; dpkg clean). Removed the upgrade line —
  never blanket-upgrade this production box.
- Committed Brain spine to branch `slice0-brain-spine`, pushed to origin
  (`github.com/dhird6/solar_twin`). PR: /pull/new/slice0-brain-spine.

**Env change — vLLM stopped (2026-07-21):** the `Qwen2.5-VL-72B` vLLM
(`vllm.service`, was auto-restarting, held ~70% GPU) is **stopped + disabled +
unit moved aside** to free the GB10 for Isaac Sim. Unit backed up at
`/etc/systemd/system/vllm.service.disabled-by-claude-20260721`. Restore:
`sudo mv .../vllm.service.disabled-by-claude-20260721 /etc/systemd/system/vllm.service && sudo systemctl daemon-reload && sudo systemctl enable --now vllm.service`.
(Was the intended `cosmos_reason.py` backend — bring it back before that work.)

**Day-1 COMPLETE ✅ (camera→ROS 2 verified):** installed ROS 2 Jazzy (ros-base;
desktop conflicts with system python3-paraview) — `ros2 doctor` 5/5. Launched
Isaac Sim **6.0.1** (build `045ca8b`) headless via `tools/day1_ros2_camera_check.py`
(self-contained scene, no asset download), bridge `isaacsim.ros2.bridge-5.1.2`.
Verified from a sourced Jazzy shell: `/rgb` + `/camera_info` in `topic list`,
`/rgb` at **~50 Hz**, `/camera_info` echoes 640x480 + K matrix. **The feared Spark
ROS-2 sensor quirk does NOT affect this build** → ROS 2 is a viable Transport;
`ros2_bridge.py` can be real, not a stub (Slice 0 still defaults sim-native).
Sim stopped after; GPU free. Details in `docs/ENVIRONMENT.md`.

**Next:** Workstream B — `world/farm_builder.py` (build the USD farm from
`farm.yaml`, reuse `world/layout.py`, stamp PVModule prims). Then WS-C sim_runtime
+ sim_native transport. Optionally capture PyTorch cu13 version from Isaac python.

## 2026-07-21 — Session 1: Brain spine built end-to-end ✅
**Done (all pure-python, no Isaac; 31 pytest tests green):**
- `pyproject.toml` (pure-python deps only), package tree + `__init__`s.
- `schema/pv_module.py` — fault taxonomy (§6.5), `PanelRecord`, `pv:` attr
  constants, append-only log, `local_to_geo`/`geo_to_local` georef.
  **pxr imported lazily inside the USD fns only** so the module imports Isaac-free.
- Interfaces: `perception/base.py` (Verdict/Diagnosis), `transport/base.py`
  (Pose + panel read/write), `control/base.py` (Waypoint).
- `perception/ground_truth.py` — the Slice 0 "cheat": reads `pv:state` from ctx.
- `orchestrator/mission.py` — the escalation FSM (ADVANCE→SCREEN→CONFIRM→
  WRITEBACK), returns structured `MissionResult` (injected-vs-detected, events).
- `orchestrator/fake_backend.py` — `FakeSimBackend` (Transport+RobotControl in
  RAM) for Isaac-free logic tests.
- `world/layout.py` — pure geometry + **seeded** fault injection; shared by
  `run.py` now and `farm_builder.py` later so sim & tests get identical panels.
- `configs/farm.yaml`, `configs/mission.yaml` (seeded, sim_native default).
- `run.py` — config → mission → `runs/<ts>/results.json`. `--backend fake`
  works now; `--backend sim_native` raises a clear NotImplemented (Spark half).
- `docs/ENVIRONMENT.md` — platform + how-to-run + ROS 2 TODO.
- **Verified run:** `--backend fake` → 10 panels, 2 seeded faults, detection_rate
  1.00, run record emitted.

**Key finding:** `usd-core` has **no aarch64 wheel** → `pxr` only exists under
Isaac's Python on this box. Drove the lazy-pxr design in `pv_module.py`. See
`docs/ENVIRONMENT.md`.

**Decisions:** sim-native is the only Transport until Day-1 ROS 2 check (user
confirmed ROS 2 status = not yet checked). Plain FSM (not py_trees). Fault
subset healthy/hotspot/soiled. 0-based row/col indices in panel IDs.

**Next (World half, on the Spark, in Bible §8 order):**
1. **Day 1-2:** ROS 2 camera-publish de-risk; record result + build details in
   `docs/ENVIRONMENT.md` (the `[ ]` checklist there).
2. **Day 3-5:** `world/farm_builder.py` — build USD from `farm.yaml` reusing
   `world/layout.py`; stamp `PVModule` prims via `schema` USD fns; assert Z-up/
   meters; inject faults + emissive signature + semantics.
3. **Day 6-8:** `world/sim_runtime.py` + `transport/sim_native.py` (annotator/
   render-product camera reads + poses); kinematic `control/kinematic.py`.
4. Wire `sim_native` into `run._build_backend`; run the real mission on Spark.
5. `transport/ros2_bridge.py` per §6.3 (only depend on it once Day-1 passes).

**Not started:** everything World-half above; `docs/ARCHITECTURE.md`,
`docs/ROS2_CONTRACT.md`; git branch/commit (nothing committed yet this session).
