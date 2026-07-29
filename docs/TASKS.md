# TASKS — live per-person tracker (Normal machine vs DGX Spark)

> Referenced by `CLAUDE.md` §10 ("keep a `docs/TASKS.md` for live work so sessions
> resume cleanly"). This is `plan.md`'s checklist **re-cut by owner/machine**
> instead of by workstream, so two people can work the same day without
> touching the same files. Specs are unchanged — `docs/PROJECT_BIBLE.md` §8,
> `plan.md`, `docs/ENVIRONMENT.md`. Update the `[ ]` boxes here **and** in
> `plan.md` when something completes (same commit).

## ⇢ NEXT SESSION — start here (updated 2026-07-29, Session 13b)

Everything below this block is the older two-track plan and is still valid; this
is just the current front of work. Full detail in `SESSIONS.md` Sessions 10d-13.

**State:** ✅ **`main` IS the trunk again — PR #9 merged 2026-07-29 as `71625a8`.**
That was the largest outstanding structural item for four sessions ("no PR to
`main`"), and it is closed: `main` fast-forwarded from `86dc834` to the integrated
branch, **71 commits**, and its test count went **74 → 396**. Merged only after
`mergeable: CLEAN`, a verified fast-forward, and **CI green on py3.10 + py3.12**.
Work now happens on **`ID-3-Testing-and-new-features-addin`**, cut from the merged
`main` (hyphens not spaces — a branch name with spaces needs quoting in every
command and breaks CI matrices; matches `ID-2-Layout-Integration`'s convention).
**497 Isaac-free tests** collected off-Isaac — 9 more need `pxr` and do not collect
without it, and `tests/test_docs_fresh.py` **enforces this number** so it cannot rot
a fourth time. CI (`.github/workflows/ci.yml`) gates every push. The twin
runs on the real Khavda
BLOCK-02 layout, on **real Copernicus GLO-30 terrain**, and KPI-03 now has **two
verified-stimulus points, both 0.00** — 560 healthy panels at 02:00Z
(`runs/20260727T183423`) and 40 panels at 01:30Z measured as a **spread over 3
repeats** with the gate enforced (`runs/20260728T200755`). KPI numbers are quoted
with N and a gate from here on, not from a single run.

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
  ~~⚠ `build_keepouts` needs `layout` threaded in~~ ✅ **done in 11c** —
  `run.py:176` calls `build_keepouts(farm_cfg, layout)` and
  `tests/test_siting.py::test_keepouts_resolve_from_the_same_scattered_field_as_the_build`
  is the regression test. This warning outlived its fix by two sessions; see the
  freshness note below.
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
- ~~**Quantify VLM run-to-run variance** (was item 0)~~ ✅ **and the diagnosis
  changed.** Measured directly against the live server on a real saved frame:
  **served serially the model is byte-repeatable — 15/15 identical, even without
  a seed.** Fire 4 identical requests *concurrently* and the same frame returns
  2× `soiled` / 2× `healthy`. So `temperature` was never the culprit; continuous
  **batching** is, and no request-level parameter fixes it (`RISK-23`). Shipped:
  greedy decoding pinned + recorded (`DEFAULT_SAMPLING` → the run record's
  `perception` block), `run.py --repeat N` reporting a spread (`variance.json`),
  and per-panel **frame digests** so a flip is attributed to the renderer or the
  model instead of argued about. `kpi_gates` are now **enforced** (`FR-17`) —
  they had been loaded, printed and never checked.
- ~~**Second KPI-03 point at a lower sun** (was item 1)~~ ✅ 01:30Z scenario
  (`configs/scenarios/khavda_selfshade_lowsun.yaml`, sun 10.7 deg, 54% of each
  module shaded, geometry asserted in `test_solar.py`). **Measured:
  false_fault_rate 0.000, N=3, identical across repeats; gate PASS worst-of-3**
  (`runs/20260728T200755`, 40 panels at stride 14, 825 s). Stimulus proven with
  `tools/verify_shade.py`: shaded rows 77.5-83.0% dark glass vs the control's
  40.0%, a **+40 point differential** (02:00Z measured +14). See that run's
  `STIMULUS.md`.
- ⚠ **Stale-entry warning that keeps recurring — now partly automated.** This
  block has claimed "nothing pushed" after a push, listed the DEM as to-do after
  it shipped, warned about the `build_keepouts` fix for two sessions after it
  landed, and quoted three test counts (204 / 247 / 294) none of which were
  current. The count is the one claim a machine can check, so
  `tests/test_docs_fresh.py` now checks it and fails the PR instead of a reader
  catching it later. **Everything else here is still just prose** — check
  `git log`, `gh pr list` and `SESSIONS.md` before trusting any of it.

**Do these first, in this order:**

-1. **Finish the reference-repo integration (Session 13).** Three ingredients landed
   — real surveyed turbines, `tools/digest_to_site.py`, and the whole 24-block S05b
   plot (6,213 tables / 679,616 panels). What remains, cheapest first:
   (a) ~~re-bake the DEM for S05b's extent~~ ✅ **done**: `assets/dem/khavda_s05b.yaml`
   (284x140 @ 20 m, relief 5.4 m). The bug it fixed is worth remembering — `dem._raw`
   CLAMPS outside its grid *by design*, so a plot pointed at the wrong patch sits at
   one flat elevation **while looking like real terrain** (`NFR-07`);
   (b) ~~build a few-block subset~~ ✅ **done (Session 13b)**: 20 S05b tables ->
   1,904 panels / 3,535 prims (`assets/khavda_s05b.usd`), flown as
   `assets/khavda_s05b_tour.mp4` (769 frames). Worst pile deviation 0.324 m on the
   re-baked DEM. ⚠ `faults.rate 0.0` is what keeps it sane — the full 6,213-table
   plot is ~1.69M prims because faulted panels cannot be instanced;
   (c) **get a blob URL/SAS for `imagery_near.png`** — the ~1 m imagery is the single
   biggest remaining visual gap and is NOT in the archive (Azure blob only).
   ⚠ Do not inherit their `resolutionMeters: 1` claim: it is upsampled 30 m plus
   semi-manual corrections, not a survey. Everything imported is `provenance:
   digest` (second-hand), never `derived`.


0. **Perception robustness, now that the flip is explained (`RISK-25`).** Two
   `--repeat 3` sets on `demo_video` reproduced and attributed it: the model reads
   the *same picture* two ways (`R258-C013` `soiled`→`hotspot`; in the second set
   `R258-C014` `hotspot`→`healthy`/`soiled`). Given identical bytes it is
   repeatable, but the renderer never sends identical bytes and the difference is
   invisible at picture level — so this is **fragility, not nondeterminism**, and
   it is not a sim bug to fix (a real camera has sensor noise too). It is a
   soiled↔hotspot discrimination problem that `_STATE_DEFINITIONS` reduced but did
   not remove.

   ⚠ **Prompt engineering has been tried twice and measured out — do not start
   there.** On `SC-01`: removing the `soiled` "lower edge" cue + adding a
   module-boundary instruction (`v2`), then restoring the cue and keeping only the
   boundary rule (`v3`). Both LOST — `detection_rate` **0.900 → 0.850 → 0.825** —
   and both broke a class v1 got right: **4 panels injected `soiled` and detected
   `soiled/soiled/soiled` under v1 came back `hotspot/hotspot/hotspot`.** `v3`
   falsified the obvious diagnosis, since the cue was restored verbatim and the same
   four still failed: the **boundary instruction** was the culprit, not the
   descriptor. Telling the model that sandy discoloration near the module edge "is
   never a fault" suppresses `soiled`, because that is what soiling looks like.

   **Root cause: the false alarms and the true soiled detections rest on the same
   pixels** — sand-coloured discoloration at the panel's lower edge, in a frame that
   also contains real desert ground. No wording can separate them, which is why both
   attempts traded error classes instead of reducing error.

   **Do the FRAME next, not the prompt:** crop/mask capture to the module's own
   bounding box so ground is not in the image at all, then re-measure on `SC-01` with
   `--repeat 3`. ⚠ And quote agreement alongside accuracy — `v3` had the *best*
   per-panel agreement of the three (0.950 vs v1's 0.875) and the worst accuracy, so
   it is possible to "fix" the flipping by making the model confidently wrong the
   same way every time. The prompt is now pinned in the run record as
   `perception.prompt_version`, so a future comparison cannot be silently invalid.

   ⚠ **The number to drive changed on 2026-07-29, and so did its provenance.**
   `KPI-01 = 0.875` came from `demo_video.yaml` — a file whose own header says in
   capitals that it is a DEMO, not a measurement (`--video` swaps to interpolated
   motion, `--max-panels` truncates to a 16-panel denominator). It was never
   quotable. `SC-01` was in the spec's scenario table and on **no disk**, so it has
   been built (`configs/scenarios/nominal_calm.yaml` + `_vlm.yaml`, one shared
   stage so `perception` is the only variable). On that scenario, 40 panels, N=3:

   | | `detection_rate` | `false_fault_rate` | abstention |
   |---|---|---|---|
   | stub baseline | **1.000** identical | 0.000 identical | 0.000 |
   | Reason-1 live | **0.900** median (0.875–0.900) | 0.091 median (0.030–0.091) | 0.000 |

   So the real gap is **−0.10**, the stub at exactly 1.000 is the control working,
   and abstention 0.000 on both sides means the gap is **misdiagnosis, not lost
   answers**. Per-panel agreement 0.875 (5/40 flipped, `model` 4 / `both` 1).
   **And `KPI-03` is NOT stable on this stage** — 0.030–0.091 across repeats, where
   `SC-11`/`SC-12` gave 0.000 *identical*. Those are all-healthy low-sun stages;
   this one is fault-enriched at mid-morning, and 3 of the 5 flips are healthy
   panels called `soiled`. The 0.00 headline is a property of those scenarios, as
   they always said — not of the model. Drive KPI-01 against `SC-01` from here.
1. ~~**Decide how `unknown` should score in KPI-03.**~~ ✅ **decided and shipped
   2026-07-29 — report the split, do not redefine the metric.** `KPI-03` keeps its
   formula (locked contract, §6.5 / `FR-03`; redefining it would make every
   recorded number non-comparable, including both verified-stimulus 0.00 points),
   and two new metrics decompose it *exactly*:
   `KPI-03 == false_alarm_rate + (healthy abstentions / healthy)`.
   - `false_alarm_rate` (`KPI-03a`) — healthy panels given a specific wrong
     diagnosis. The number the project is actually driving down.
   - `abstention_rate` (`KPI-03b`) — panels with no usable verdict, over **all**
     panels, because losing the answer for a faulted panel is equally a plumbing
     failure (it just surfaces as a missed detection in `KPI-01`).
   Both are in `variance.py`'s `DEFAULT_METRICS` so neither can be quoted from a
   single run, and all five KPI-03 scenarios now gate them —
   `abstention_rate_max: 0.0`, deliberately zero, because a lost verdict is a bug
   rather than a budget. Verified end-to-end (`--repeat 2`, fake backend): gates
   report `abstention_rate_max: PASS (0 <= 0)` on worst-of-N.
   ⚠ The headline stays conservative on purpose — an abstention still counts
   against `KPI-03`, so the metric can never flatter the system. And a `KPI-03` of
   0.00 on a run with **no healthy panels** is vacuous; check `KPI-03b`.
2. **`FR-06` FLIES ✅ — wrap it behind `RobotControl` next.** PX4 SITL governs a
   Pegasus Iris in Isaac 6.0.1 and holds a hover: **2.562 m within 43 mm over 35 s**,
   worst |vz| 0.023 m/s (`tools/px4_hover.py`; start PX4 first with
   `python3 tools/px4_sitl_smoke.py`). `RISK-26` resolved — the v1.14.3-era MAVLink
   backend interoperates with ~v1.18-beta PX4 unchanged. Remaining:
   (a) implement `control/px4.py` behind the existing `RobotControl` ABC — deferred
   until now on purpose, because an unproven controller behind the ABC makes every
   failure look like an orchestration bug; `FR-07` keeps `kinematic.py` as fallback;
   (b) re-measure `KPI-05` under a real wind field (`FR-12`, blocked on `RISK-27`) —
   the 43 mm figure is a **calm-air baseline**, not the KPI.
   ⚠ **Operational facts that will waste an hour if forgotten:** PX4 SITL never
   recovers from a simulator disconnect (restart the container per flight); PX4 is
   the TCP *client* so the simulator must bind 4560 — publishing the port makes
   docker-proxy steal it and Pegasus dies with `EADDRINUSE`; and Pegasus's own
   physics callbacks do not reliably fire on 6.0.1, so its four update methods are
   driven explicitly from our loop (`RISK-28` residual — the freeze is SILENT).
   ⚠ **Compute `KPI-05` from Isaac ground truth, never PX4's estimate** — they
   differ by ~0.23 m and the autopilot is what is under test (`RISK-29`).
3. **More balance-of-plant** — substation / control room, module-level torque
   tube and pile geometry, cable trenches. `world/site.py` is the place, and
   anything not in the drawing must be tagged `INFERRED` like the rest. The
   **graded civil surface** belongs here too: GLO-30 is a pre-grading DSM, so the
   twin's ground is the desert's shape, not the engineered pad's.
4. ~~**`transport/ros2_bridge.py`** — does not exist.~~ ✅ **built 2026-07-29
   (`FR-23`, ROS 2 half).** Full `Transport` ABC over the §2 topic table, **35
   conformance tests that run with no ROS 2 installed**, and **13/13 legs green
   against real ROS 2 Jazzy** (`tools/ros2_bridge_smoke.py`: real `rclpy`, real
   `sensor_msgs/Image`, real QoS, real DDS round-trip). Both open contract
   questions are now decided and recorded: `capture` is **fresh-or-fail** (a
   last-seen frame would attribute one panel's pixels to another panel's verdict,
   and the KPIs are measured off those pixels), and `read_panel` uses a
   `PanelStore` protocol rather than inventing a request-reply topic (§8 option 2),
   while `write_panel` publishes the event *and* writes through so USD stays
   authoritative.
   ⚠ **Left to do:** Isaac has never been the publisher — both ends of the smoke
   test are ours, deliberately, to isolate the bridge from Isaac's camera helper.
   Driving it from a playing sim (§6) and adding a `--backend ros2` flip to
   `run.py` (it needs a `PanelStore`, which in the twin is the Isaac-side
   transport) are the next steps. **VDA5050 / Mission Dispatch is untouched** —
   that is the other half of `FR-23`, so it stays Partial, not Locked.

**⚠ The renderer is stochastic — do not design around bit-equality.** Measured
(`tools/probe_render_determinism.py`): 4 captures from a camera that never moved
gave 4 different images (0.85/255 mean pixel delta, ~52% of pixels), and extra
settling steps do not converge it. Block-averaged to an 8x8 thumbnail that noise
is 0.2–0.6 LSB while a real difference (shaded panel vs the unshaded control) is
**35.5 LSB**, so attribution compares *pictures* with a tolerance, never hashes
(`RISK-24`). A quantised hash was tried and rejected — it still flipped a
quantisation boundary on 3 of 4 unchanged captures.

**How to quote a KPI from now on:** run it with `--repeat N` and quote
`variance.json`'s spread (`MetricSpread.quote()` formats it), with the run
record's `perception.sampling` naming the decoding config. A bare single-run
number is a sample presented as a constant, and this project has already been
bitten by one.

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
- Reason-1's confirm-pass notes are **near-boilerplate on an all-healthy stage**
  (the KPI-03 scenarios) — do not read those as per-panel analysis. On the faulted
  `demo_video` stage they are genuinely panel-specific and diagnostic ("opaque tan
  or brown patch… soiling" vs "a small, bright red/orange spot"), which is what
  made the `RISK-25` flips readable. Judge the notes by what the frame contains.
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
- **Never parallelise VLM inference in a measurement run.** Serial is repeatable
  on this build, batched is not (`RISK-23`, measured). If a future fleet screens
  panels concurrently for throughput, every KPI it produces needs its variance
  re-measured — the fix is not a seed.
- The **nadir-viewpoint** caveat above matters more at 01:30Z than at 02:00Z: a
  half-shaded, foreshortened module in dimmer light is the hardest frame the
  suite currently produces. Do not read a clean KPI-03 there as proof the model
  handles low sun in general — it is one geometry, honestly reported.

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
