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

## 2026-07-28 — Session 10e: the status tour video ✅ + the "video path is too expensive" claim was wrong

**Asked for:** a video of the twin as it stands, watchable end to end, that says
which parts are done and which still need building. Built as `world/plant_tour.py`
(Isaac-bound renderer) + `world/tour.py` (pure: chapters, budget, overlay) →
`assets/plant_status_tour.mp4`, 85 s at 1280x720, 8 chapters.

**⭐ The measurement that unblocked it — Session 10d's diagnosis was wrong.**
10d left the video path "open: render cost at ground level on the full plant …
thousands of panels in frame". Measured on the real block with a 4-pose probe:

| camera | z | mean frame |
|---|---|---|
| aerial, whole block | 420 m | 0.71 s |
| mid-descent | 140 m | 0.72 s |
| in the rows | 6 m | 0.71 s |
| low along a row | 2.5 m | 0.70 s |

**Flat. Ground level is not dearer than the aerial, at 540p or at 720p.** The
cost of a video here is its FRAME COUNT and nothing else — which is why 4,900
ticks of commute read as a hang (that is 58 min of render) while the same stage
tours comfortably in 15. So the fix was never "make frames cheaper", it was
`--budget-minutes`: state the budget, project frames x 0.71 s against it, and
shorten the shots proportionally (loudly — text cards are never cut).

**A real bug this surfaced:** `sim_runtime.py` hardcoded the overview render
product at `(960, 540)`, so `flythrough.py --width/--height` had been silently
doing nothing — every flythrough ever rendered was 540p whatever the flags said.
Now `overview_resolution`, defaulted not hardcoded. Deliberately NOT reusing
`resolution`: that one sizes the drone cameras, and a run wanting 640x480
inspection frames still wants a watchable external view.

**Three-way status, not two.** `BUILT` / `TODO` cannot express the status most of
this site actually has, so `INFERRED` is a first-class tag: the roads, fence,
inverter stations and turbines are *in* the twin and look real, but they are our
placement, not the drawing's. A test asserts each of those four is labelled
`INFERRED` — mislabelling one as built would overclaim the CAD ingest, which is
the one thing this video must not do. `TODO` items sit in the shot where their
absence is visible, not quarantined in the end card (also tested).

**Captions are counted, never typed.** The overlay's numbers come from the prims
(30,016 modules · 29,416 instanced · 313 hotspot + 287 soiled · 75,572 prims · 5
turbines/inverters/roads) or from a generated sidecar (273 tables; 2.17 m of DEM
relief). A test changes `facts` and asserts the captions change with it.

**⚠ Framing is lens arithmetic, and guessing it cost three iterations.** Worth
recording because every instinct here was wrong:
- Turbines, guess #1: heading westward from the block centre → two hazed white
  lines 160 m off. Correct geometry, no evidence of anything.
- Guess #2: aim at the prim, stand off 2.4 tip-heights (453 m) → worse, pure haze.
- Guess #3: 1.05 tip-heights, camera 38 m up, aimed at the hub → a fine turbine
  and **no panels at all**. A 22 mm lens on a 36 mm aperture has a ~49 deg
  vertical field; aiming 18 deg up puts the frame's bottom edge 6 deg below
  horizontal, which from 38 m up first meets the ground **355 m away** — past the
  turbine. The array was under the frame the whole time.
- Works: 1.4 tip-heights, camera low (~17 m), aim at 0.37 of tip. Panels in the
  foreground, machine standing clear of them — the chapter's claim, shown.
Same lesson for the balance-of-plant chapter: a shot down the middle of the site
contains the inverters and renders them as an 8-pixel grey box. Both chapters now
aim at a prim position read off the stage (`tour.look_at`, `_turbine_shot`,
`_plant_shot`), and `facts["turbine_tip_m"]` is named `tip` on purpose — the Hub
prim's bound includes its blade children, so it is the 189 m blade-tip height, and
calling it the hub height aims the camera 70 m too high.

**Also:** `RunRecorder` gained a streaming mode. A 1,300-frame tour buffered at
720p is ~5.5 GB of RAM on a box already holding a 75k-prim stage in the same
unified memory; frames now go straight to the encoder. Buffered mode is unchanged
(`max_frames` still logs what it drops). Its tests inject a fake writer, because
`imageio` lives only in Isaac's bundled python and the logic must stay Isaac-free.

**188 Isaac-free tests (was 157), 3 skipped.**

**Next:** unchanged and still the honest backlog — quantify VLM run-to-run
variance before quoting any KPI as a constant; the low-sun (01:30Z) KPI-03 point;
Pegasus/PX4; the rest of the balance of plant. The tour's closing card is that
list, so it stays current with the docs by construction.
⚠ Turbine blades render very thin and read faintly at distance. Cosmetic, in
`farm_builder`'s turbine geometry, not in the tour.

## 2026-07-28 — Session 10d: real DEM terrain ✅ + turbines + serpentine routing (full-plant fleet video ⚠ IN PROGRESS)
**The plant now stands on the real ground.** Terrain was `flat` with a note not to
ship a synthetic sine field on a real site; it now samples **Copernicus DEM GLO-30**.

- **Source:** AWS Open Data, **no credentials, no registration** — SRTM, NASADEM and
  AW3D30 all need an Earthdata or JAXA login, which a reproducible pipeline should not
  depend on. Khavda BLOCK-02 measures **3.3–5.4 m above sea level: 2.2 m of relief over
  1.1 x 1.4 km**, which is what the Rann of Kutch actually is.
- **Two-stage, mirroring the CAD ingest.** `tools/dem_fetch.py` needs GDAL, which must
  not go into Isaac's bundled Python because the build runs there — so it lives in
  `/home/simulationhub/venvs/dem-ingest` and bakes a `.npy` + YAML sidecar;
  `world/dem.py` samples that with **numpy alone**.
  ⚠ **Near-miss on this box:** `pip install --user rasterio` dragged numpy 2.5.1 over the
  system 1.26.4 and broke scipy 1.11.4. Reverted and isolated in the venv. Never
  `--user`-install a package with a numpy pin on this machine.
- **⭐ Straight torque tubes — the fidelity point.** A tracker's tube is a rigid beam up
  to 128 m long. Sampling the DEM per module and mounting each at its own height would
  **bend that beam into the shape of the desert** — wrong, and wrong in the flattering
  direction (`NFR-07`). `fit_line` does what an installer does: least-squares a straight
  line through the grade. Its residual is a real engineering quantity, and the build
  prints it. Measured: tube slopes **-0.64% to +0.89%**, worst row **T0130 needs 0.461 m
  of pile-height variation**. Panel z now spans 1.10 m across the block.
- **Datum:** absolute elevation would sit the plant 4 m off the stage origin and silently
  invalidate every waypoint standoff (they are measured from the panel).
  `datum: hardware_mean` puts the site mean at z=0; relief is preserved. Sampling outside
  the DEM patch **clamps to the edge on purpose** — the ground mesh reaches kilometres
  further, and returning 0.0 would tear a cliff around the site.

**Wind turbines added** (5, utility class: 120 m hub, 70 m blade, ~11-12 rpm). ⚠ Tagged
**INFERRED** like the roads: Khavda is a real hybrid wind+solar park but this drawing
carries DC block hardware only, so the placement is ours. They sit **outside** the panel
footprint (x < 0 and x > 321) — both how a hybrid park is laid out, and the honest choice,
because a turbine standing inside the array would throw blade shadows on panels and any
KPI-03 number measured against it would be an artefact of where *we* put it.

**Serpentine routing + panel stride** (`route:`/`panel_stride`, `--route`/`--panel-stride`).
A one-way sweep of a 128 m table means a 128 m deadhead back to the next row's start,
every row; serpentine turns round instead (worst consecutive hop drops >4x in test).
Default stays `linear` so existing KPI numbers remain comparable.

**⚠⚠ WHAT IS NOT DONE: the full-plant fleet video.** It looked like a hang; it was
geometry, twice over, and only the first is fixed:
1. **Fixed — a 490 m commute at walking pace.** On the full block the first table is
   ~490 m from the stage origin. At 1 m/s in 0.1 s ticks that is 4,900 *rendered* frames
   of empty desert before anything is inspected. Two fixes, both what real hardware does:
   `cruise_speeds` (transit at 16 m/s, ease to 2 m/s inside 6 m of the target — a survey
   drone cruises and slows for the shot), and the fleet is now **deployed at the first
   panel** instead of flying there from the origin.
2. **Open — render cost at ground level on the full plant.** `capture_pair` measures
   160 ms with the camera high over a small stage, but the chase cam at row level on the
   real block has *thousands* of panels in frame, and a single stride-14 panel took over
   5 minutes. Next step is a frame budget, not more speed: raise `dt` so each rendered
   frame covers more ground (a labelled time-lapse patrol), and/or drop the chase render
   to 960x540. **Do not conclude the pipeline is broken — teleport mode inspects the full
   block fine (5 panels in 7 s); only the frame-per-tick video path is too expensive.**

157 Isaac-free tests (was 137).

## 2026-07-28 — Session 10c: the WHOLE plant builds and looks like a plant ✅ (IF-09 done)
**All 273 tables, 30,016 modules, in one stage, with roads, fencing, inverter stations
and a real sky.** `assets/khavda_flythrough.mp4` (769 frames / 32 s) is the tour:
establishing aerial over the block, descent onto the internal access road past the
inverter skids, low pass along the rows, then a climb turning back over the site.

**IF-09 instancing — the thing that gated all of it.** Every module authored its own
geometry (one Geom + a 12x6 cell grid = **75 prims each**), so the real block came to
**~2.25M prims** and had never been built whole. Healthy panels now reference a single
instanced prototype:

| | before | after |
|---|---|---|
| 5 tables / 560 panels | 42,025 prims | **582** |
| 273 tables / 30,016 panels | ~2.25M prims (never built) | **75,464**, 85 s build, opens in 24 s |

The panel prim is still a real per-panel prim carrying the `pv:` attrs — **only the
geometry is shared** — so the stage remains the source of truth and verdict writeback is
untouched. Faulted panels (~2%, 600 of them) still author in full, because a hotspot
recolours specific cells and soiling bakes a per-panel dust film; neither survives
instancing. Fence posts use the same trick.

**`world/site.py` — balance of plant, with provenance.** Access roads, perimeter fence,
inverter/transformer skids. Split honestly: **DERIVED** (the CAD's own 11 m corridor at
x=143 among 5-6 m maintenance aisles really is a road — found by threshold, not
invented) vs **INFERRED** (the drawing describes hardware only, so the ring road, fence
and inverters are standard practice placed by us). Every element carries
`st:provenance`, the build log prints the split, and inverter COUNT follows capacity
(30,016 x ~600 W / ~4 MW per station → 5) rather than a magic number. 9 tests.

**⚠ The sky mistake worth remembering: an emissive dome is a light.** First version was
a self-lit hemisphere. It looked right and was wrong — under raytraced lighting a 1.4 km
emissive dome is a colossal area light and it **lit the desert floor blue**. Measured, on
identical stages: dome OFF → ground R-B **+16** (warm), dome ON → **-38** (cold). Two
objects were disagreeing about the sky, which is the same failure the sun-vs-tracker fix
already dealt with once. Now **one `DomeLight` carries both the generated latlong sky
image and the fill**, so they cannot diverge. Orientation **verified empirically, not
assumed**: looking east gives a saturated sun glow (max 255), west does not (128), and
the zenith is darkest — so no Z-up correction rotation is needed on this build.

**Other visual fixes, each from looking at a frame:**
- Ground albedo 0.17 → 0.30. The old near-black was chosen when the ground was a small
  backdrop behind ten panels; across 320 x 647 m it read as cold grey slate. 0.44 was
  then measured as a near-white blowout that buried the roads and fence in glare.
- Ground now reaches ~5 km and **fades into the sky's own horizon haze**. A finite plane
  ends in a hard edge with void beyond it from any altitude — visible in the first
  aerial as a literal hole in the world.
- Below-horizon sky is haze, not ground tone: looking down from altitude puts that
  region on screen, where a dark value reads as void.
- Ground colour variation moved to three incommensurate octaves; one sin*cos pair beat
  into visible corduroy stripes across a site this size.

**Gotcha banked:** `import pxr` must come **after** `SimulationApp` exists. A module-level
`from pxr import ...` leaves Isaac's schema extensions unregistered and the app dies in a
wall of `TfNotice wrapper has not been created yet` errors.

**Scope:** terrain is still deliberately `flat` (no real DEM), and there is no substation,
control room, or module-level torque-tube/pile geometry yet. 137 Isaac-free tests
(was 123).

## 2026-07-28 — Session 10b: a demo video you can watch ✅ + the VLM is NOT deterministic ⚠
**`--video` makes the twin show its work.** `runs/20260728T115737/inspection.mp4` —
24 panels of the real Khavda block, live Reason-1, 576 frames / 38 s: a chase camera
following the fleet down a 128 m tracker table, the drone's own camera inset, and a
caption naming the panel, the FSM phase and the verdict as it lands on the USD prim.

**Teleport was why no video existed.** `control/kinematic.py` placed each robot AT its
waypoint, so the fleet never travelled and there was nothing to film. It now has an
**interpolated** mode driving the `step_towards` math that had been sitting built and
tested since Session 3. Teleport stays the default — the KPI runs must not silently
pick up ~10x the sim steps. Both modes are kinematic (`NFR-07`): this is animation, not
flight dynamics.

Also built: `world/recorder.py` (Isaac-free, 8 tests), `SimRuntime.capture_pair` (both
views from ONE render pass — two passes would put the two cameras a render apart, so a
moving drone would sit in different places in the same frame), `SimRuntime.chase()`
(the old fixed bird's-eye was written for a 10-panel row and loses the drone within a
few panels of a real table), `Mission.run(on_phase=...)`, and `--max-panels` with
`panels_targeted` stamped into the record so a truncated sweep cannot be read as a full
one. 123 Isaac-free tests (was 108).

**⚠⚠ THE FINDING THAT MATTERS: the same panel got two different diagnoses across two
identical runs.** Same stage, same config, same seed, back to back:

| panel | injected | run A | run B |
|---|---|---|---|
| R258-C013 | soiled | screen suspect → **soiled** | screen suspect → **hotspot** |

detection_rate 0.917 vs 0.875 on 24 panels — one flip. `cosmos_reason.py` does send
`temperature: 0.0`, but vLLM clamps that to 0.01 (it logs the substitution), and GPU
batching is not bit-reproducible either way. **So the world is seeded and the model is
not: a KPI from a single run carries unquantified run-to-run variance.** This does not
overturn Session 10's KPI-03 = 0.00 (0 false faults across 560 panels is a lot of
evidence), but every future single-run KPI should be treated as a sample, not a
constant. Repeat-runs or a fixed sampling seed are owed before any KPI is quoted as
*the* number.

**Smaller notes.** A 15-min hang on the first `--video` attempt did not reproduce and
sent no request to vLLM (its log shows a 17-hour gap) — cause unknown, watch for it.
The confirm drone is visible in the screening drone's camera when both are over the
same panel; the verdict hold now shows the CONFIRM drone's view for an escalated panel,
which is the frame the diagnosis was actually made from.

## 2026-07-27 — Session 10: KPI-03 measured for real ✅ (0.00 on 560 panels) + two geometry bugs the check exposed
**The false-fault number is finally trustworthy.** SLICE-3's 0.00 was hollow — the
turbine shadow missed the panels. This one has a **verified on-panel stimulus and its
own unshaded control inside the same run**, so it means what it says.

**The result.** Run `runs/20260727T183423`, scenario `khavda_selfshade`, live Cosmos
Reason-1, 560 healthy panels of the real Khavda BLOCK-02 (`--subset 5`), 6773 s
(~12.1 s/panel):

| group | n | escalated | **false faults** |
|---|---|---|---|
| shaded (4 tables, self-shaded by their eastern neighbour) | 448 | 8 (1.8%) | **0** |
| control (R243, eastmost — nothing up-sun of it) | 112 | 2 (1.8%) | **0** |

**KPI-03 = 0.00**, gate was 0.05. The finding is not only the zero: the escalation
rate is **1.8% in both groups**, so the tracker shadow does not measurably shift the
screening decision either. Escalations do not track shading depth (R244 at ~30%
shaded escalated 0; R253 at ~17% escalated 4), which is what you would expect if
they are model noise rather than a shadow response. The confirm pass cleared all 10.

**The stimulus, quantified BEFORE rendering** (`world/solar.py`: new
`cross_axis_angle_deg` / `shadow_chord_m` / `self_shaded_fraction`, pure, tested).
At `2026-06-21T02:00Z` the sun is 17.2 deg up, the cross-axis angle is 72 deg against
a **60 deg mechanical stop**, so every tracker is pinned and throws a 7.20 m shadow
into 5/6 m aisles → predicted ~30%/~17% of each row shaded. Measured on the frames the
VLM actually received, counting **PV-glass pixels only**: 34.1% / 34.0% dark on the two
5 m-pitch tables, 29.8% / 26.6% on the 6 m ones, **19.9% on the control**. The two
identical-pitch tables agree to 0.1%. Evidence + captures archived in the run dir
(`STIMULUS.md`). `tests/test_solar.py` asserts the timestamp still produces shading, so
editing it cannot silently gut the test.

**⚠⚠ TWO GEOMETRY BUGS, found only because the stimulus was checked first.** Both were
live in the Session-9 "verified on the Spark" build.
1. **The module was authored TRANSPOSED.** `panel.width/length` mapped straight to
   stage X/Y, but the procedural farm's rows run along **+X** while a CAD table's
   torque tube runs along **+Y** — the two sources need opposite mappings. The real
   block was built with a **1.134 m chord instead of 2.278 m**, and 112 modules at a
   1.148 m pitch each 2.278 m long **overlapped their neighbours 2:1 along their own
   tube**. It also erased the hazard: a 1.134 m chord throws a 3.6 m shadow into a
   5-6 m aisle, so nothing lands on the next row — **KPI-03 would have read a hollow
   0.00 for the second time, on a farm with no shadow on any panel.** Fix: module
   extent is now **per-site** (`PanelSite.size_x_m/size_y_m`), fed from the CAD site
   file for an import. The site file already carried the real dimensions; naming them
   a second time in the config is what transposed them.
2. **`panel_top_z` ignored TILT** — it returned mount height + half thickness. A
   2.278 m module at the 60 deg stop raises its upper edge **0.99 m** above the torque
   tube, so the 0.8 m confirm standoff put the camera **below that edge, inside the
   row**. Same shape as the old abs-Z bug, one layer up. Top is now the panel's highest
   point, and the tracker angle behind it comes from `FarmLayout.tracker_rotation_deg()`
   — the builder authors panels from that same call, so geometry and waypoints cannot
   drift apart (they already did once for the sun light vs the trackers).

**Method note, worth keeping.** The aggregate frame brightness *did* separate shaded
from control (52.9 vs 81.8 mean) **while the panels were identically lit** — the
difference was entirely dark GROUND in frame. That is the precise Session-8 near-miss
repeating itself. Only masking to PV-glass pixels showed the truth. **Never score a
shading stimulus on whole-frame statistics.**

**Honest scope.** One instant, one seed, one site, no backtracking (worst case), and
Reason-1's confirm-pass notes are noticeably boilerplate across panels — a 0.00 here is
a green light for the shading-vs-defect distinction, not a robustness claim. The panel
is also viewed obliquely: at a 60 deg tracker angle a nadir camera sees a foreshortened
module, which is realistic for this hazard but not an inspection-optimal viewpoint.

**Also:** `TASKS.md` item 2 (merge `docs/cosmos3-edge-serving`) was **already done** at
`16e9a35` — the list was stale. 108 Isaac-free tests (was 100). vLLM `vllm-cosmos` is
left **running** on :8000 at util 0.4 (Isaac-coexistence setting).

**⇢ NEXT:** (1) a second KPI-03 point at `01:30Z` (~50% shaded, dimmer) to see where the
distinction breaks; (2) PBR materials + HDRI sky; (3) instancing/LOD (`IF-09`) before
all 273 tables; (4) Pegasus/PX4 (`FR-06`); (5) real DEM.

## 2026-07-27 — Session 9: REAL Khavda layout in the twin ✅ + world-model reality check
**The twin now runs on real hardware geometry.** BLOCK-02 of Khavda PLOT A10b,
extracted from the vendor DWG, built in Isaac, inspected end-to-end.

**Verified on the Spark (not just in tests):**
- `farm_builder --subset 5` → **42,025 prims, 560 panels**, Z-up/metres, `pv:panel_id`
  `R258-C000`, `pv:geo_position` **(24.0880, 69.4176)** — real Khavda lat/lon via pyproj.
- Full mission → **560 panels, 11/11 faults detected, detection_rate 1.00, 105 s**
  (~0.19 s/panel). Run record `runs/20260727T153355`.
- Both held bugs confirmed fixed on real USD: per-panel tilt/azimuth, and the ground
  mesh now sized from `bounds()` (x[-28.9,53.1] y[-29.4,158.0] — the old
  `cols x col_pitch` maths would have undersized it badly).

**Layout ingestion (FR-26/27, IF-08/09).** DWG → DXF via LibreDWG built from source
(no apt needed). `$INSUNITS=6` (metres) and model space holds real survey coords, so
**no scale inference**: 273 tables / **30,016 modules** / 320 x 646 m, CRS **EPSG:32642**
verified by round-trip. CAD is self-describing — block names carry dimensions
(`MMS Table (128.58 x 2.278)`), layers carry module counts (`Interior HSAT (1x112)`).
Traps: layer `DETAILS` holds legend copies ~12 km away (would stretch the bbox from
319 m to 12 km); LibreDWG emits raw newlines in text values, defeating `ezdxf.recover`.
The PDF path is kept but **fails closed** — its two calibration sources disagreed 9.2%.
Site is **HSAT**, so tilt is DYNAMIC; any static tilt is an `NFR-07` approximation.

**⚠ Cosmos 3 Edge: serving, but NOT usable for our data factory.** Edge runs on-box
(vllm-omni from `main`, own venv, ~9.8 GB, ~2 s/image) — sm_121 was never the blocker;
the image was a dead end because Omni is Qwen3-VL and Edge is Nemotron. **But it is a
pure-diffusion GENERATOR with no text stage**, so it cannot back `Perception`
(`mission_edge.yaml` removed). And across **6 generations it never produced a
physically valid PV module** — one photoreal frame was not a solar panel at all.
`num_inference_steps` is mandatory or you get valid-looking pure noise, silently.
→ **Text-to-image is the wrong tool; Cosmos Transfer (conditioned on our render) is
the right one.** Edge explicitly rejects V2V/transfer, so that stays off-box (`NFR-05`).
Edge now **stopped**; port 8000 free for Reason-1.

**Evaluator gate built (FR-05/NFR-08)** — `wfm/base.py` + `wfm/evaluator.py`, 91 tests.
Calibrated on the 6 real Edge frames, which proved **no-reference statistics cannot
work**: the non-PV frame scored the HIGHEST grid-periodicity, and good frames carry
MORE high-frequency energy than noise. So the gate is **reference-based** (edge
retention vs the seed) and **rejects any frame with no seed as unverifiable**.

**Realism pass — robots and sun (same session, later commits).**
- **`world/robot_builder.py`** — the fleet was three marker CUBES (0.25 m drones with
  a camera slung 0.3 m below, 0.4 m ground bot) that also slid sideways down the row.
  Now a real quadcopter (fuselage, canopy, hazard tail, 4 booms + motors, spinning
  rotor discs, skids, gimbal, ~0.9 m span) and a real rover (1.0 x 0.7 m chassis,
  four 0.34 m wheels, bonnet, beacon, sensor mast). Procedural — this box has **no
  Isaac asset pack and no configured asset root**, and procedural keeps it
  reproducible from script + config.
- **Motion**: rotors spin every update (alternating direction, as torque balance
  requires), wheels roll by GROUND distance travelled, and `set_pose` derives heading
  from the motion delta so vehicles face where they are going. ⚠ **Appearance and
  articulation, NOT dynamics** — no lift, no traction, no collision (`NFR-07`).
- **`world/solar.py`** — NOAA solar position + HSAT tracker angle, pure/Isaac-free.
  `farm.yaml: sun.timestamp` (ISO-8601 UTC) now drives **both** the sun light and the
  tracker rotation from one vector, so they cannot silently disagree (they were set
  independently before). Verified at `2026-06-21T04:00Z` (09:30 local): sun 43.7 deg
  elev / 79.9 deg azim → tracker **+45.9 deg east**; render shows rows foreshortened
  from nadir, shadows thrown into the aisle west of each row, specular glint off the
  sun-facing glass, drone frame mean 79.7 → 116.1.
- Two sign conventions written out in-code because both are easy to get silently
  wrong: light `rx = 90 - elev, rz = 180 - azim` (DistantLight emits along -Z); and
  tracker rotation is **about Y, not X** — the torque tube runs N-S, so rotating
  about X would tilt panels *along* the tube, which the hardware cannot do.
- **Mistakes I made and fixed** (all caught by tests): subset builds renumbered panel
  IDs, so `R00-C000` meant different hardware in a subset than in the full build and
  verdicts would have landed on the wrong panels — `TableSpec.index` is now canonical.
  Solar noon at 69.418 E is **07:22 UTC**, not 06:22. Asserting a "due south" azimuth
  at the June solstice is meaningless at 24 N (sun passes 0.65 deg from zenith, azimuth
  ill-conditioned) — moved to December. Float `rel_tol=1e-12` on differenced ~2.66e6 m
  northings is unachievable — `abs_tol=1e-6`. And the block's hardware extent is
  320 x **647** m, not 518 m: modules run a table-length north of each insert point.

**⇢ NEXT SESSION: see `docs/TASKS.md` "NEXT SESSION — start here".** Short version:
(1) **re-run KPI-03 on the real block** with a low-sun timestamp + `cosmos_reason` —
tracker self-shading is finally a real on-panel stimulus, which retires the
`kpi03-denominator-caveat`; (2) **merge branch `docs/cosmos3-edge-serving`** (`7319924`),
it holds the Edge serving recipe and is NOT on this branch; (3) PBR materials + HDRI
sky; (4) instancing/LOD (`IF-09`) before all 273 tables; (5) Pegasus/PX4 (`FR-06`) as
its own investigation; (6) real DEM. Off-box Cosmos **Transfer** (not Edge, not
text-to-image) remains the right data-factory tool.

**Branch state:** `ID-2-Layout-Integration`, **16 commits ahead of origin, nothing
pushed**, tree clean, **100 tests + 2 skipped**. Vendor CAD is gitignored (proprietary);
only the derived `configs/layouts/*.yaml` is tracked.

### Session 9 detail — Cosmos 3 Edge serving investigation
**The parked Edge A/B is unparked and simultaneously invalidated.** Edge now runs on
this GB10; the reason it never worked was misdiagnosed, and the reason we wanted it
was wrong. Full recipe + caveats in `docs/ENVIRONMENT.md`.
- **Serving works** via **vllm-omni from `main`** in `/home/simulationhub/venvs/vllm-omni-edge`
  (a plugin — it does NOT depend on `vllm`, install both; aarch64 vLLM wheels exist).
  `sm_121` was never the Edge blocker: `get_device_capability()` → `(12, 1)`, fine.
- **The image was a dead end, not a stale pin.** Cosmos3 Omni (Nano/Super) is
  **Qwen3-VL**-based; **Edge is Nemotron**-based with its own sub-configs + a projector.
  A model-type alias `cosmos3_edge → Cosmos3OmniConfig` dies on
  `KeyError: 'cosmos3_edge_vision'`. And there is **no newer image** — `cosmos3`'s arm64
  layer and `cosmos3-arm64` are the same digest. Stop chasing tags.
- **⚠⚠ `num_inference_steps` is mandatory.** Omitting it returns a valid-looking
  640×640 PNG of **pure noise** — no error, no warning. `num_inference_steps: 35` →
  crisp photoreal PV imagery; `guidance_scale` alone → smeared. A textbook `NFR-07`
  silent cap. Byte size can't detect it (uncompressed → always 1,229,899 bytes).
  **Look at a frame before trusting any generated corpus.**
- **Edge ≠ perception backend.** Served this way it is `pure diffusion mode (single
  diffusion stage)` — no text stage. `/v1/chat/completions` exists but answers with an
  `image_url` part, so `cosmos_reason.py` (which reads `content` as a string) cannot
  consume it. Its real surface is `/v1/images/generations` + `/v1/videos` + the action
  modes; it also **rejects V2V/transfer**, so Cosmos-Transfer sim2real stays off-box.
  → **Edge belongs behind a future `WorldModel` seam, not `Perception`.** Reason-1
  stays the brain. `configs/mission_edge.yaml` **removed** (it encoded the disproven
  "model-string flip" assumption).
- **Good news for the roadmap:** ~**9.8 GB GPU, ~2 s/image** — Edge can co-reside with
  Isaac Sim (Reason-1 at 0.85 util takes ~98 GB and cannot). On-box world-model
  generation is viable, which softens `NFR-05` for the predict/action arm. Both
  default to port 8000, so only one at a time; Reason-1's
  `cu130-nightly-WORKING-sm121` rollback is untouched.

**Also:** `ID-2-Layout-Integration` fast-forwarded to `main` (`86dc834`) — it had zero
unique commits, so no rebase/force-push was needed. 74 tests pass, 2 skipped.

**Next:** layout ingestion (`world/layout_import.py`, table-level site file) — NOT yet
started, awaiting plan confirmation. Two bugs found and deliberately NOT fixed:
`farm_builder.py:256` applies ONE global `tilt_deg` to every panel, and
`farm_builder.py:107-110` sizes the ground mesh from `layout.cols * col_pitch` — both
break for a real multi-block imported layout.

## 2026-07-24 — Session 8b: SLICE-3 done (KPI-03 harness + 2 verified 0.00 results); Cosmos 3 Edge A/B PARKED
**SLICE-3 shipped** (PRs #3→#4→#5, stacked; merge in that order). KPI-01 = **1.00**
(detection), KPI-03 = **0.00** on BOTH a soft and a near-black hard shadow — the
model reasons about shading and correctly does not fault a shadowed healthy panel.
Two verified false-fault data points on stimuli confirmed on-surface (not the
earlier hollow ground-shadow null). Scope stays honest: 10 panels, one seed.

**Cosmos 3 Edge A/B — attempted, PARKED (option C).** Edge won't serve on the
available `vllm/vllm-omni:cosmos3` image: model type `cosmos3_edge` (shipped
2026-07-20) is unrecognized by that image's Transformers 5.13.0, which only knows
`cosmos3_omni` (Nano/Super). **NOT an sm_121 issue** — Edge is just days newer than
the serving stack. Box left clean: failed container removed, **Reason-1 restarted
and serving on :8000**, rollback image preserved (`:cu130-nightly-WORKING-sm121`),
Edge weights (8.6 GB) cached for resume. Full detail + resume path in memory
`cosmos3-edge-serving-blocker.md`. Reason-1 baseline is already recorded, so the
A/B is cheap to finish once a newer Edge-capable image exists. `configs/mission_edge.yaml`
is staged for that.
> **SUPERSEDED by Session 9 (2026-07-27):** Edge does serve (vllm-omni from `main`), but
> it is a pure-diffusion **generator** with no text stage, so the perception A/B this
> entry planned is not possible and `configs/mission_edge.yaml` was **removed**.

## 2026-07-24 — Session 8: SLICE-3 false-fault harness (KPI-03) — built, first measurement is a honest null
**Built + tested (74 Isaac-free tests pass), on branch `feat/slice3-false-fault-kpi03` (stacked on #4):**
- **Scenario layer** `src/solar_twin/scenario.py` (IF-03): composes `farm.yaml` +
  `mission.yaml` and deep-merges a dynamic-hazard override layer (`farm_overrides`
  / `mission_overrides`) + `kpi_gates`. Pure, Isaac-free. `--scenario` flag added to
  both `farm_builder` and `run`.
- **KPI-03** `MissionResult.false_fault_rate` = fraction of HEALTHY panels misread
  as faulted; in the run record. Sun elevation now a config knob in `farm_builder`.
- **SC-05** `configs/scenarios/sweeping_shadow.yaml`: all-healthy panels + low-ish
  sun + one turbine, blades spinning → a blade shadow meant to sweep the row.

**⚠ FIRST MEASUREMENT = KPI-03 0.00, but it's a HOLLOW null — do not trust it as
"VLM is shadow-robust".** Root cause found by looking at the actual frame: **the
turbine shadow lands on the GROUND, not on the panels.** Panels are mounted ~0.8 m
up and tilted toward the sun, so a distant occluder's shadow sails *over* them onto
the ground beyond. The drone (straight down over a panel) sees a fully-lit panel
with shadow on the surrounding ground; the VLM correctly reported *"no signs of
shadows"*. My brightness-dip check (C004/C005 ~85 vs ~120) was measuring the dark
GROUND in-frame, not a shadowed panel — a near-miss that would have shipped a
confidently-wrong "0% false faults, shadow-robust" claim. (This is the exact
`NFR-07`/"no silent caps" failure the harness exists to prevent — the metric was
right, the STIMULUS was absent.)

**Geometry lessons banked:** shadow LENGTH must match turbine→row distance (elev 16°
overshot a 12 m-away row by ~40 m); shadow DIRECTION = -Y for a +X sun tilt, so the
turbine must sit on the +Y side to cast back across the row; and landing a shadow on
the ELEVATED tilted panel surface (not the ground) is precise, intermittent geometry
for a thin spinning blade.

**Open decision (asked user):** how to make the KPI-03 stimulus real —
(A) precise blade-on-panel turbine geometry [faithful, fiddly, intermittent];
(B) a dedicated close occluder casting a hard shadow across each panel surface
[reliable worst-case, = the "shading" hazard, one-shot]; or (C) ship SC-05 honestly
as a weak stimulus and track "shadow-on-panel" as a refinement. Recommended **B first**
(real number, tests the shading-vs-soiling distinction) then **A** as the turbine version.
Nothing committed yet on this branch.

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

### ✅ RESOLVED — **KPI-01 = 1.00** (run `20260724T172011`), SLICE-1 closed
`faults_detected 2/2 · detection_rate 1.00 · false positives 0/8`. Both soiled
panels: screen=suspect → escalated → diagnosed **`soiled`**, with correct reasoning
(*"a tan-coloured deposit along the lower edge … opaque, uneven layer"* — it names
the lower-edge accumulation we modelled). **Four distinct root causes**, in order
found:
1. **Flat colour swatches** → PV cell grid + sun/shadows + panel-relative standoffs.
2. **Keep-out viz spheres shadowing the farm** (brightness 126→46) → `purpose="guide"`.
3. **Unphysical fault signature** (opaque, cell-aligned) → translucent dust *film*
   crossing cell borders, ragged outline, lower-edge accumulation. Two stack findings
   learned the hard way: **RTX renders `UsdPreviewSurface` opacity as a hard CUTOUT**
   (bake the blend into per-face `displayColor` instead), and the baked sub-grid must
   be **~8× the cell grid** or it quantises into slabs. Also: alpha must stay HIGH
   (0.72–0.94) — at low alpha the bright aluminium frame survived *through* the dust
   as a grid of bright lines, which the VLM read as *"a cluster of bright pixels…
   characteristic of a hotspot"*.
4. **⭐ THE ACTUAL CLASSIFICATION BUG — the PROMPT, not the world.** `_diagnose_prompt`
   passed **bare enum names** (`soiled, hotspot, crack, …`) with no definitions, so the
   model guessed and mapped any localized anomaly to its *hotspot* prior. A 60-second
   HTTP probe of the SAME frame settled it: bare taxonomy → `hotspot`; free description
   → *"a shadow or different material"*; asked directly *"is there dust?"* → *"No, the
   panel appears clean"*; **taxonomy + per-class definitions → `soiled`** ✅. Fix:
   `_STATE_DEFINITIONS` in `cosmos_reason.py` defines **all 8** states (defining only
   `soiled` would bias the classifier) in visible-light terms — soiling = deposit ON
   the glass; hotspot = one glowing CELL; shading = shadow CAST BY an object, no deposit.

**METHOD LESSON (worth keeping):** ~3 world-rebuild cycles were spent tuning materials
when the failure was in the prompt. **When the MODEL's output is what's failing,
interrogate the model directly (cheap HTTP probe on a saved frame) BEFORE rebuilding
the world.** The material work wasn't wasted — the bright-frame-line artifact was real
— but it was not this bug.

**⚠ Scope caveat:** 10 panels, one seed, one fault type. KPI-01=1.00 is a green light,
NOT a robustness claim. The real test is `KPI-03` (false-fault under sweeping blade
shadows) — where the *shading vs soiling* distinction we just defined is exactly what
gets stressed.

**→ Superseded next step** (kept for the record): make the soiling physically
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
