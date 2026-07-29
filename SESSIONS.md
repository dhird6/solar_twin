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

## 2026-07-29 — Session 14: the panels were never broken, and the site now stands in real OSM geography

Two parts. The first was a bug hunt whose answer was "not where you are looking".

### Part 1 — "the plates are not rendering" was a CAMERA fault, twice over

Checked every panel-authoring suspect against the stage and **all of them came back
clean**: the instanced prototype carried its full 75 prims at `purpose=default` with
`cell_healthy`/`frame` bound, and all 1,904 panels were instanceable, visible,
default-purpose, and cleared the ground by **1.95-2.60 m**. So the panels were fine.
Proved it by rendering the *unmodified* stage from hand-picked poses: a nadir at 120 m
showed real blue glass with cell grids. Two real bugs, both in how the shot was chosen:

**1. `subset_site` was a smear, not a patch.** It took the N southernmost tables — a
full-width BAND, which only looks compact on one DC block. On the 24-block S05b plot
`--subset 20` drew from several blocks over **1739 x 161 m at 1.8% occupancy**: a
bounding box almost entirely made of holes. A nadir from 400 m over the middle of it
contains **no panels at all**, only inverter pads. Now selects by distance from an
anchor: **117 x 161 m, 26.4%**.

⚠ **BLOCK-02 `--subset 5` and `--subset 20` return byte-identical tables**, so every
recorded KPI stage is untouched. Only `--subset 50`+ changes, and nothing recorded uses it.

**2. The tour was axis-blind.** `default_shots`/`build_chapters` sized standoff from
`max(span_x, span_y)` but travelled and pulled back along `span_y`. On that smear the
establishing aerial sat **956 m above a 161 m-wide strip** (a 2.278 m module = **1.8 px**)
and the road-level shots faced north out of the site while the plant ran away east — so
the only panel faces in frame were the trackers' pale *backs*. Shots are now written in
the footprint's own long/short frame (`flythrough.footprint_frame`), standoff capped
against the SHORT span: **956 -> 353 m**. Verified key-by-key that a north-south
footprint maps through identically, so BLOCK-02's hand-tuned tour is unchanged.

Also fixed `cx * 0.75`, which **scaled a world coordinate** instead of offsetting from
centre: a spanwise nudge near the origin, and a **498 m excursion** at Khavda's real
eastings.

**⭐ And the ground mesh was undersampling every terrain source in the project.** A fixed
48-160 verts over a horizon-sized sheet gave **65 m spacing against 20 m DEM posts**, and
40 m against the procedural farm's 14 m heightfield. That aliases, and the new clearance
test caught a panel **buried 158 mm** in its own drawn ground while clearing
`terrain_height` fine. `layout.terrain_feature_step` now reports what the source
justifies and the mesh is **graded** off it — fine over the hardware, expanding to the
sky dome. BLOCK-02: **25,600 verts @ 65 m -> 3,710 verts @ 19.8 m**, so 7x cheaper *and*
3.3x finer.

### Part 2 — real OSM geography, and the brief's bbox was 26 km wrong

⚠ **The brief gave the site as 23.80-23.95 N, 69.40-69.65 E. Converting the CAD's own
EPSG:32642 anchor puts the plant at 24.0898-24.1075 N, 69.4247-69.4723 E — ~26 km away.**
Fetching OSM for the given box returned real named roads (Khavda-Dhordo Gorewali, Rann
Bund) that are **29-32 km from our footprint**. Same shape as the clamped-DEM trap: real
data, wrong place, and no visible symptom because the Rann is featureless either way. So
the bbox is derived from the site file, never typed.

**Terrain was already real** — Copernicus GLO-30, since Session 12c — so the open item was
roads. `tools/osm_fetch.py` + `world/osm_features.py`, the same two-stage split
`dem_fetch`/`dem.py` uses: network and `pyproj` at ingest, pure metre geometry at build.

⚠ **Not Overpass.** Its main instance answered *every* request during this ingest with
`504 ... dispatcher timeout, the server is probably too busy`, and two mirrors were
unreachable. The **official OSM API 0.6 `/map`** call served immediately; its cost is a
hard 0.25 sq-deg bbox limit, which the tool fails loud on rather than truncating.

**What is actually there, measured:** 3 roads, 8 transmission lines including a **765 kV
2-circuit 6-cable** run, and the mapped boundaries of **"Khavda Renewable Energy Park"
(Adani Green, 1000 MW)** and **"NTPC Khavda" (397.7 MW)**. Prims carry
`st:provenance = "mapped"` — a **third** tag beside `site.py`'s derived/inferred. On the
built stage: **17 derived, 106 inferred, 10 mapped**.

⚠ **OSM has NO internal plant roads here** — the whole footprint returns **5 ways**. The
plant's own access roads are private and unmapped, so they stay derived/inferred and the
build prints the two tallies separately. Road **widths** are per-class defaults
(`st:width_source`) and conductor **sag** is not modelled: the centreline is real, the
breadth and the catenary are conventions.

⚠ **Radius clipping alone put 582 towers in the void.** `clip_to_radius` keeps whole ways
so a road never ends in mid-desert — but that dragged all **108 km** of a transmission
line on stage from one nearby vertex, giving an OSM layer spanning **-23 to +31 km east
and -70 km north against a 1.5 km ground mesh**. `clip_to_box` against the ground's own
extent fixes it (**582 -> 93 towers**), inset by the ribbon's miter bound so a road's
*edge* also stays on terrain. Verified: every OSM/Farm/Site/Turbine prim now inside the
ground mesh.

⚠ **A small subset may legitimately have no mapped geography, and that is reported not
hidden.** `--subset 20`'s nearest mapped way is ~1.5 km outside its ground mesh, so that
stage carries none; `--subset 50` picks up 2 roads, `--subset 200` 3 roads + 6 power ways.
The deliverable was therefore built at **`--subset 200`: 22,064 panels, 55,945 prims**.

**Deliverable: `assets/khavda_s05b_tour.mp4`, 769/769 frames at 1280x720, zero drops**
— 22,064 panels reading as blue PV glass with visible cell grids, on real GLO-30
terrain, with a mapped track and a 765 kV tower in shot.

**Two more defects fell out of validating it.**

⚠ **`flythrough.py` buffered 2.1 GB and then lied about it.** 769 frames at 720p held
in RAM, on a box already carrying a 56k-prim stage and a vLLM server in the same
unified memory — `recorder.py`'s own docstring says a few thousand buffered frames is
not fine, and `plant_tour.py` already streams for exactly that reason. Measured: it
captured **0 of 769 frames**, printed `wrote flythrough (0 frames)`, exited 0, and left
the PREVIOUS video in place. Fifteen minutes for an mp4 that was never rewritten, and
the cheerful log made it look like a scene bug. Isolated by rendering the same stage at
65 and 129 frames first, to separate resolution from frame count. Now streams, absorbs
RTX's warm-up, counts dropped frames, and raises instead of claiming success.

⚠ **Mapped substations were being authored as overhead cables.** Every non-`plant`
`power=*` way was treated as a conductor, so OSM's two real substations (`PSS 3`,
`KPS 2`) and a generator area — all closed rings — became 14 m cables strung around
their own perimeters, with towers. They happen to be clipped away at `--subset 200`, so
the stage before and after the fix is byte-identical; that is precisely why
`OSM_POWER_AREAS` lives in the Isaac-free module and the test asserts against the
**bake** rather than a built stage.

### ⭐⭐ `farm_builder` is O(n^2.4) in panel count — the whole-plot video is INFEASIBLE, not slow

Asked for a whole-plant video, I started the full 6,213-table S05b plot and estimated
32 min by scaling BLOCK-02's 85 s linearly. **It ran 1h16m with no end in sight**, so I
stopped guessing and measured two clean points:

| tables | panels | build |
|---|---|---|
| 200 | 22,064 | **55 s** |
| 500 | 55,328 | **495 s** |

**2.5x the panels cost 9x the time** -> `n^2.39`. Extrapolated, the full plot is
**~55 HOURS**, not 32 minutes. I killed the build. The estimate was wrong by a factor of
100 because it assumed a linear cost that this code does not have.

⚠ **So `configs/farm_khavda_s05b_full.yaml`'s "~680k prims" blocker was the wrong
blocker.** The prim count is real but it is not what stops you: the AUTHORING TIME is,
and it is superlinear, so no fault-rate trick helps. Prime suspect is
`prim.SetInstanceable(True)` re-resolving the instance master as the instance set grows
— unverified, and worth profiling before anyone attempts a multi-block plot again.

**Practical ceiling on this box, from the measured curve:** ~30k panels in ~1.6 min,
~110k in ~43 min. So a plant video means a COMPLETE BLOCK, not a complete plot.

### The full-plant video: the complete BLOCK-02, all 273 tables

`assets/khavda_full_plant.mp4` — **769/769 frames at 1280x720, zero drops. ALL 273
tracker tables / 30,016 modules / 75,777 prims, built in 96.75 s**, on real GLO-30
terrain with real OSM geography. Not a subset: a whole DC block with its 600 seeded
faults intact, which the 4.8 km plot cannot be at any sane cost. The establishing aerial
holds the entire block inside the fence line in one frame; the road-level pass shows blue
glass with legible cell grids, the inverter skids, and a mapped HV tower on the horizon.

⭐ **And it is where the substation fix finally bit.** BLOCK-02's own OSM bake contains
the real substation **`PSS 3`**, which S05b's clip had excluded — so this stage is the
first to prove `substation_03` lands under `/World/OSM/Boundaries` as a ground outline
rather than being strung up as a 14 m overhead cable with towers.

### The two videos, and what rendering them exposed

Asked for a proper status tour and a whole-plant video. Both delivered — and the
tour caught four more things, every one of them **visible on screen**:

⚠ **The overlay captioned "6213 tracker tables, 22,064 modules".** The table count
came from the layout SIDECAR (the whole 24-block plot) while the modules were counted
off the STAGE (the 200-table subset) — a **22x mismatch worn as a single fact**, burned
into the frame. Tables now come from the stage's own `pv:grid_index` rows and the
caption states the fraction: *"200 of 6,213 tracker tables (3% of the plot)"*.

⚠ **The title card named the wrong plot** — hard-coded `Adani Khavda PLOT A10b
BLOCK-02` and `configs/farm_khavda_block02.yaml` whatever was built, so an S05b tour
announced a different plot's hardware. Now passed in (`--site-name/--site-desc/
--config-name`), defaulting to a generic "Khavda" rather than a confident wrong answer.

⚠ **The sky texture filename collided across stages.** It was `sky_<elev>_<azim>.png`,
keyed on the sun alone, so any two stages sharing a timestamp shared one file — and a
full-plot build running in parallel **overwrote the texture the live tour was reading**
(`Failed to read texture file assets/sky_44_80.png or file is empty`). Now keyed on the
output stage too. Self-inflicted by running both at once, and a real latent bug either way.

⚠ **The closing backlog card under-claimed shipped work** — it still listed PX4 flight
dynamics and VLM run-to-run variance as open, both closed in Session 12. A backlog that
under-claims is as misleading as one that over-claims; it now reads "PX4 flies a hover;
the INSPECTION fleet is still kinematic" and "decoding is pinned, batching is not".

**On the mapped park boundaries: they are `guide` purpose, so they do NOT appear in
either video.** Deliberate — a 40 km outline crossing the site would land in every
drone camera frame perception scores, which is the keep-out-sphere bug again. They are
queryable tagged geometry on the stage, not scenery. Roads and HV lines DO render.

**The ground-mesh vertex cap had become a fidelity cap.** At 220/axis the whole plot was
forced to **42.2 m spacing against its own 20 m DEM** — a 2x undersample of the terrain
the panels mount on. Raised to 560 (68.6k verts for the whole plot, trivial beside 680k
panel prims), and the builder now WARNS when the cap coarsens the drape.

**Three videos, all verified frame-by-frame:**

| file | frames | what it answers |
|---|---|---|
| `khavda_full_plant.mp4` | 769 | the WHOLE block — 273 tables, 30,016 modules |
| `khavda_s05b_tour.mp4` | 769 | what a multi-block S05b patch looks like (22,064 panels) |
| `khavda_s05b_status_tour.mp4` | 1,119 | which parts are real, inferred, or missing |

**Tests: 497 Isaac-free (was 464) + 38 pxr.** New: panel visibility (visible,
material-bound, non-guide, above the *interpolated* ground mesh, instancing on and off),
Isaac-free framing guards (travel follows the long axis, standoff proportionate to the
short span, offsets survive translating the site), subset occupancy >= 15%, and OSM
geometry (miter, drape, clip invariants). `FR-27` and `IF-11` are Locked.

## 2026-07-29 — Session 13b: the multi-block plant builds and flies — on real S05b terrain

Closed the two build items Session 13 left. Both worked, and one exposed a stale
command in my own handoff notes.

**The multi-block plant builds.** 20 tables of plot S05b -> **1,904 panels, 3,535
prims** on `assets/khavda_s05b.usd`, with `faults.rate 0.0` so nothing escapes
instancing (a faulted panel is ~75 prims against ~1 healthy — the reason the full
6,213-table plot is ~1.69M prims and needs a subset).

**On the RE-BAKED DEM, and it shows.** Worst-row pile deviation **0.324 m** on
T0009 — a real number from real ground. Before the re-bake this plot sat on
BLOCK-02's clamped patch and would have reported terrain that did not exist.
`site works` also resolved 6 roads / 330 fence posts / 1 inverter, split
1 DERIVED + 6 INFERRED.

**Video: `assets/khavda_s05b_tour.mp4`, 769 frames at 960x540.**

⚠ **My own handoff command was wrong and the tool caught it.** I had written
`flythrough.py --budget-minutes 8`; that flag belongs to `plant_tour.py`, not
`flythrough.py` (whose flags are `--caption/--fps/--width/--height/--out`). It
failed with `unrecognized arguments` rather than silently ignoring it, which is
the right behaviour — but it is a reminder that a handoff command should be run
once before it is written down as the instruction.

## 2026-07-29 — Session 13: reviewed the two reference repos, and took the real plant data out of them

Asked to review `solar_plant_layout/reference/`. Findings, then what got integrated.

**Repo 1 (`adani-khavda-solar-park`, 22 MB) has NO 3D.** `Viewer3D.tsx` is a nine-line
stub rendering a static PNG, and the deps confirm it — React/Tailwind/MobX, no
renderer. The impressive plant view is `public/images/temp-bg.png`, an aerial photo.
Its real asset is a documented content hierarchy (catalog→group→experience→hotspot)
with placeholder camera coords. A presentation shell awaiting a 3D backend.

**Repo 2 (`gs-vr-variant-2`) is the real system** — R3F + Koota ECS + Robot FSM,
volumetric cloud/rain shaders, `wtg-blades.vert`, drone/first-person modes, Azure
SWA. ⚠ It arrived as a **stalled 433 MB `.part`** and is a *streaming* zip (all
entries have `csize 0`, sizes live in trailing data descriptors), so no normal tool
opens it; 148 of 150 source files were recovered by inflating entries directly.

**⭐ Its digests are in EPSG:32642 — our own CRS — so they compose by subtraction.**

| | A5 | S05b |
|---|---|---|
| blocks / tables | 40 / 11,134 | 24 / 6,213 |
| inverters / IDT | 2,229 / 40 | 1,102 / 24 |
| **5.2 MW WTG** | **14** | **8** |

**Shipped 1 — real turbines (`e919239`).** Our `turbines:` were always INFERRED
(the DC drawing carries hardware only). Measured against our footprint: **7 of
S05b's 8 WTGs are within 3 km, nearest 546 m**, while all 14 of A5's are ≥7.5 km
and correctly excluded by `--radius`. `SC-13`'s invented turbine is now those seven.

⚠ **THE FINDING: with real siting the SW-wind wake deficit over our block is 0.0%
everywhere** — all seven turbines are east/NE, so the block is *upwind* of every
one. Verified as geometry, not a broken model (wind from 45° → 9.2%, from 70° →
22.1%). So my invented turbine at x=−180 had put the block **downwind and was
manufacturing a wake hazard the real plant does not have.** The `NFR-07` failure
mode, caught only by using real data.

**Shipped 2 — the alternative to their extractor (`6f2ce14`).** We do not hold
`build_gis_digest.py` (different workspace), but we hold its OUTPUT, so
`tools/digest_to_site.py` converts rather than re-derives. Sound because the
hardware matches, measured: their boxes **2.3 × 128.6 m** vs our own DXF's
**2.278 × 128.58 m**, all `rot_deg 0.0`, same CRS. Verified end-to-end —
`load_site` accepts it, `FarmLayout` built 224 panels, `inspection_targets` 224.

⚠ **The fail-closed guard fired on real data, which is the point of it.** A
bounding box cannot express a rotated table. Plot A5 has **10 of 11,134 boxes over
4 m wide (worst 32.3 m)** → conversion REFUSED rather than emit a mis-sized plant.
S05b had none and converted cleanly. Same discipline as `layout_from_pdf.py`
refusing its 9.2% calibration disagreement.

**Shipped 3 — the whole plot (`dab1531`): 6,213 tables → 679,616 panels over
4.84 × 1.97 km**, 23× BLOCK-02, in 44 s pure-python. Committed **with both
blockers written into the config**, because one is silent:

1. **~1.69M prims** at `faults.rate 0.02` — worse than the 2.25M that gated the
   plant before IF-09, since faulted panels cannot be instanced (~75 prims each
   against ~1 healthy).
2. ⚠ **The DEM does not cover this plot and fails silently.** The baked patch is
   BLOCK-02's; S05b starts ~300 m east and runs 4.8 km further, and `dem._raw`
   **clamps** outside its grid *by design* (so the far ground mesh does not tear a
   cliff) — so most of S05b would sit at a flat clamped elevation **while looking
   like real terrain**. Re-bake for S05b's extent, or set `terrain.kind: flat` so
   the approximation is explicit. I would have shipped a plausible 4.8 km plant on
   fake ground had I not checked the extent.

**⚠ Two of their claims not to inherit.** Their DEM report says
`resolutionMeters: 1`, but `sourceDem: dem-30m.json` plus *"semi-manual corrections
inferred from imagery"* — it is **upsampled 30 m, not a 1 m survey**. And their
plot digests are second-hand for us (vendor DWG → their script → JSON → us), so
everything imported is tagged `provenance: digest`, never `derived`.

**On "the exact visualisation" as the end goal — the honest position.** Three of
four ingredients are now in place (whole-park layout, real turbines, graded pad).
The missing one is the one that actually makes their render look like Khavda: the
**~1 m imagery pyramid**, which is *photographed*, not modelled — and it lives in
**Azure blob**, not in the archive (the 426 MB `imagery.png` and 3.3 GB site-DEM
are both excluded from the deployable build). Also worth stating: they are
three.js/WebGL with raymarched clouds, we are Isaac RTX/USD — the *ingredients*
port, the shaders do not, and RTX will differ by nature. Repo 2 is a stakeholder
VR experience; this twin is a measurement instrument.

**Next, cheapest first:** re-bake the DEM for S05b's extent; build a few-block
subset at `faults.rate 0` to see the multi-block plant at a sane prim count; and
get a blob URL/SAS for `imagery_near.png` (the single biggest visual gap).

## 2026-07-29 — Session 12c: the physics finally run TOGETHER — graded pad, wind that bites, PX4 behind the ABC

Three things closed, and the theme is that each one produced a number rather than
a feature.

**⭐ The graded civil pad.** GLO-30 is a *pre-grading* DSM — the desert as the
satellite found it, not the surface the contractor handed over. On raw ground the
worst tracker row needs **0.532 m** of pile-height variation to sit on a straight
torque tube, far outside any real pile tolerance, so that ground *was* graded and
we were building on the wrong surface. `world/grading.py` fits a least-squares
plane: on the real site **grade 0.03%, cut 0.59 m / fill 0.80 m, 14,780 m3 each
way, balance ratio 1.00** — the cut/fill balance claim verified on real data.

⚠ **The first version was dishonest and the measurement caught it.** A plane is
perfectly straight along *every* line, so it reported **0.000 m** pile variation —
claiming the piles need no adjustment, which is not a buildable claim. Real grading
is signed off to a tolerance, so the pad carries a seeded 25 mm as-built deviation:
0.532 m raw -> 0.000 m (dishonest) -> **0.013 m** (plausible).

**⭐ Wind that measurably bites, in a scene with everything in it.** `SC-13`
(`khavda_windy_hover`) is the first scenario where the pieces run *together*: real
DEM + graded pad + an articulated 120 m turbine + 12 m/s wind with 35% gusts and a
Jensen wake, flown by PX4.

| | calm air | 12 m/s + 35% gusts |
|---|---|---|
| altitude hold | **43 mm** / 35 s | **~724 mm** (2.13–2.86 m) |
| roll / pitch | ±0.5° | **+36° / −36°** |
| settles? | yes | **no** — never meets \|vz\| < 0.05 |

So the honest statement is: at this wind the drone holds altitude and **does not
station-keep**. A ~17x degradation, reported rather than gated away.

⚠ **Two constraints that are physics, not shortcuts.** `SC-13` is a *hover*, not an
inspection, because wind is a **force** and the inspection mission drives its
robots kinematically — a force on a kinematically-driven body does *nothing*.
Running `wind:` against the normal mission would look like gust modelling and
measurably not be one. And wind is applied only once **airborne**: 15 N exceeds the
frame's own 14.7 N weight, and applied to a *parked* drone it tumbled it inverted
(roll −177°) so preflight failed and it never armed.

**⭐ `control/px4.py` — FR-06 behind the ABC.** Deliberately **Isaac-free**:
commanding PX4 is a MAVLink conversation, not a simulator operation, so it
unit-tests with a fake link on any machine and the real `orchestrator/mission.py`
drives it unchanged (`NFR-04`, asserted by running the FSM over it). Mixed fleets
fall back per robot, so the ground bot stays kinematic (`FR-07`).

The **ENU→NED conversion** gets the most test coverage on purpose: it is the classic
PX4 bug, it never raises, and its symptom (mirrored position, or descending when
told to climb) reads as a tuning problem. Also pinned: offboard needs a setpoint
*stream*, and `at_goal` returns False with no estimate — reporting arrival there
would let a mission march through every waypoint before the drone moved.

**On the live session you were watching.** Diagnosed three separate causes of poor
visibility, none of them a rendering fault: the Isaac window was **behind VS Code**;
you are viewing `:1` over **RustDesk**, which compresses a 3D viewport badly
(`--livestream` + the WebRTC client is the built-for-purpose path); and a live-VLM
run **freezes ~10.7 s per panel** (measured: 20 panels / 214 s) because Kit only
repaints between blocking `urllib` calls. Ground-bot motion also looks wrong and
is not: consecutive panels are **1.15 m** apart, so it creeps for under a second
then waits ~10 s for a verdict.

⚠ **A near-miss worth recording:** while freeing the GPU I identified a 42 GB
process as Isaac and sent it `kill -9`. It was **vLLM's EngineCore**. It survived
only because the PID was in the container's namespace. Check `ps -o cmd` before
killing by memory footprint.

FR-06 and FR-12 -> Locked. 450 Isaac-free tests (was 431).

## 2026-07-29 — Session 12b: ⭐ IT FLIES — PX4 governs an Iris in Isaac 6.0.1, hovering 2.562 m ± 43 mm

`FR-06` is achieved end to end. PX4 SITL owns the attitude/position loops, Isaac
owns the physics, they meet over MAVLink HIL, and the drone **arms, takes off and
holds a hover**:

| | value |
|---|---|
| hover altitude | **2.562 m** |
| altitude held within | **43 mm over 35 s** (8 samples) |
| worst \|vz\| while settled | **0.023 m/s** |
| roll / pitch | settle to ±0.5 deg |
| rotor speed | steady −955 rpm |
| reproducible | `tools/px4_sitl_smoke.py` then `PYTHONPATH=src $ISAAC tools/px4_hover.py` |

Both sides agree it is airborne: PX4's own EKF (`vehicle_local_position`, fresh to
one 4 ms physics step) and Isaac's ground-truth prim read.

**Three real bugs stood between the port and the hover, and each was mine to find:**

1. **`OSError: [Errno 98] Address already in use`, and it exposed a false PASS I had
   shipped.** Pegasus uses `mavtcpin` — the *simulator* listens on 4560 and PX4
   dials out to it. My earlier smoke test ran the container with `-p 4560:4560`, so
   **docker-proxy** held the port: the "TCP 4560 reachable" check I reported last
   session was connecting to the proxy, **not to PX4**, and it then blocked the real
   simulator from binding. Fixed both ways — the container now runs `--network host`,
   and the smoke test asserts the port is **free** (PX4 dials out) instead of
   connecting to it and calling that proof.
2. **PX4 SITL never recovers from a simulator disconnect.** Once Isaac exits, PX4
   spins on `poll timeout` forever and every later run looks broken for the wrong
   reason. The container must be restarted per flight; both tools now say so.
3. **The state freeze from Session 12 was Pegasus's own callback dispatch.** Its
   `Vehicle` registers four physics callbacks; on 6.0.1 they do not reliably fire,
   so `update_state` ran once and the vehicle froze at its spawn pose while the body
   fell. Driving those four methods explicitly from the loop we own — same methods,
   same order — fixed it instantly. Not the port's fault: each method is correct
   when invoked, and a plain `World.add_physics_callback` fires 22/20 steps in the
   same session.

**⚠ I also mis-reported my own metric once, and it is worth recording.** The first
verdict printed a **1180 mm** altitude spread. That was not hover quality — the
window included a mid-climb sample at 1.818 m. Measuring only the *settled* window
(takeoff + 12 s, and \|vz\| < 0.05) gives **43 mm**. A 27x error, purely from
choosing the window loosely, on exactly the kind of number that ends up in a
slide.

**Two findings logged rather than smoothed over:**
- **`RISK-26` RESOLVED — the protocol did not drift.** Pegasus's v1.14.3-era MAVLink
  backend interoperates with the container's ~v1.18-beta PX4 with no change and no
  version pin. That was the risk I rated most likely to bite.
- **`RISK-29` NEW — PX4's EKF altitude and ground truth disagree by ~0.23 m** in the
  hover. Benign for a first flight, but `KPI-05` is an *error* metric, so it must be
  computed from Isaac ground truth (`Transport.pose()`), never from the autopilot's
  estimate — the autopilot is the thing under test.
- **`RISK-28` residual:** *why* Pegasus's registrations do not fire is still
  unexplained. The drive-loop is a workaround, not a diagnosis, and the failure is
  silent — a frozen drone, not an error — so anyone reusing Pegasus outside
  `tools/px4_hover.py` can hit it again.

**Not done:** the hover is not yet behind the `RobotControl` ABC. That was deliberate
— wrapping an unproven controller would have made every failure look like an
orchestration bug. It is now proven, so the wrap is the next step, along with
re-measuring `KPI-05` under a real wind field (`FR-12`, blocked on `RISK-27`).

## 2026-07-29 — Session 12: Pegasus ported to Isaac 6.0.1 — the shim works; the hover is the next session

Picked `FR-06` off the front of the backlog, deliberately **not** perception
robustness: a parallel session is already inside `cosmos_reason.py`'s prompt, and
two of us re-measuring the same KPI would have collided.

**⭐ The port is done and it is smaller than I estimated — because the estimate was
the wrong shape.** Session 11d sized this as "17 call sites in 2 files". Reading
the code properly showed `Vehicle` **already extends** the modern
`isaacsim.core.api.robots.Robot`; the `_dynamic_control` calls are a legacy
leftover *alongside* it, and every one funnels through a single accessor,
`Vehicle.get_dc_interface()`. So the right fix is a **compatibility shim**, not a
rewrite: `dc_compat.py` reimplements the ten methods on `isaacsim.core.prims`, and
the patch touches **two imports plus that one accessor**. Upstream call sites stay
byte-identical, so future upstream merges stay clean.

Shipped as a pinned clone + a reviewable patch, never a vendored copy (240 MB of
BSD-3-Clause third-party code): `tools/install_pegasus_isaac6.sh` (idempotent,
verified from a pristine clone) and `tools/patches/pegasus-v5.1.0-isaac6.patch`.

**Verified, not assumed.** The mapping was derived by introspecting the installed
6.0.1 build, which caught two things a remembered API would have got wrong:
- **`SingleRigidPrim` has no force methods at all** — force/torque live only on the
  batched `RigidPrim`. So each body holds both views: single for reads, batched
  for writes.
- **`carb._carb.Float3` is not iterable** (`.x/.y/.z` only), and upstream passes it
  straight into `apply_body_force`.

Result: all of `Vehicle`, `Multirotor` and `PX4MavlinkBackend` import on 6.0.1,
and the shim's reads **tracked a falling Iris exactly** — matching a direct
`SingleRigidPrim` read to 4 dp.

**⚠⚠ The debugging lesson, which cost most of the session and is the useful part:
a prim view constructed before PhysX has a simulation view silently returns the
STATIC USD POSE forever.** No exception. Pegasus reported a constant `z = 4.9998`
while the body had genuinely fallen to `z = 3.80`. Left undiagnosed, that would
have fed PX4's EKF a drone that never moves — presenting as a control-tuning
problem, which is a very expensive place to look for a read bug. The related trap:
**without `world.play()` there is no physics view at all**, so every read is static
and the code looks like it works.

**Three of my own diagnostics were wrong before they were right — worth recording,
because each one nearly produced a false conclusion:**
1. I "proved" Pegasus's callbacks never fire by monkeypatching `drone.update_state`
   — but `add_physics_callback` had already captured the *original* bound method,
   so my counter could never increment. `calls=0` measured my patch, not the system.
2. I then clobbered `world._physics_callback_functions`, which Isaac does not
   consult at dispatch time; again inert.
3. Only calling `update_state(dt)` **by hand** settled it: state moved
   4.9998 → 4.6935, proving the shim and the callback body both work.

**What is NOT done, stated plainly: nothing has flown.** No PX4 connection, no
arm, no station-keep. Logged as **`RISK-28`** with the ordering trap written down:
Pegasus's four physics callbacks proved order-sensitive in a standalone app — the
state froze for 120 steps in one bootstrap and advanced correctly in another run of
the same script. A plain `World.add_physics_callback` fires reliably (22/20 steps,
measured) and `update_state` is correct when invoked, so the port is not the cause.
Next session: pin the ordering, attach `PX4MavlinkBackend` to
`tools/px4_sitl_smoke.py --keep`, watch PX4 leave `Waiting for simulator` (that one
line also settles `RISK-26`), and only wrap it in `RobotControl` **after** a hover
holds — an unstable `control/px4.py` behind the ABC would read as an orchestration
bug.

**One package entered Isaac's bundled Python** — `pymavlink 2.4.49`, `--no-deps`,
numpy/scipy verified unchanged — and `docs/ENVIRONMENT.md` now carries the note the
golden rule requires. Also avoided: Pegasus's own install guide says to
`pip install --editable` it, whose `setup.py` **rewrites Isaac's `.kit` app files**
as a side effect. The installer never does that.

## 2026-07-29 — Session 11e: PR #9 merged — `main` is finally the trunk again

**`main` had been 31+ commits behind for weeks.** `docs/TASKS.md` listed "no PR to
`main`" as the largest outstanding structural item across four sessions. It is
closed: **PR #9 merged as `71625a8`**, `main` fast-forwarded from `86dc834` to the
integrated branch, now **71 commits** with **396 Isaac-free tests collected** (it
was 74 tests on `main` before this).

**Checked before merging, not after:**
- `mergeable: CLEAN`, `mergeStateStatus: CLEAN`, and `git merge-base --is-ancestor`
  confirmed a true fast-forward — no merge conflicts were possible.
- **CI green on both interpreters** (`py3.10`, `py3.12`) against the pushed branch.
- ⚠ The local tree was **red at the time** (2 failures) and that was worth
  understanding rather than overriding: `tests/test_docs_fresh.py` was failing
  because a parallel session had **uncommitted** work adding tests, so the docs'
  stated count (393) lagged what pytest collected (396). The *committed* branch was
  green, which is why CI passed and the local run did not. The freshness test was
  doing exactly its job. It resolved itself when that session committed (`790bf70`)
  — no number was hand-patched mid-edit, which would only have gone stale again.

**Sequenced around a live parallel session.** Another session was editing
`cosmos_reason.py` / `test_cosmos_reason.py` / `CLAUDE.md` in the shared worktree,
so the working diff was backed up to the scratchpad before any branch operation.
By the time the merge ran they had committed, and the backup turned out empty —
kept the step anyway, because the cost of the safeguard is nothing and the cost of
losing someone else's uncommitted experiment is a day.

**New branch for the next phase: `ID-3-Testing-and-new-features-addin`**, cut from
the merged `main` and pushed with upstream tracking. ⚠ Named with **hyphens, not
spaces** — the request was "ID-3-Testing and new features addin", but git branch
names with spaces need quoting in every command and break scripts and CI matrices;
the repo's own convention is `ID-2-Layout-Integration`. Same words, hyphenated.

**What that branch inherits — read these before starting:**
- `KPI-03 = 0.00` is a property of the **all-healthy low-sun** scenarios
  (`SC-11`/`SC-12`), not of the model. On the fault-enriched `SC-01` it measures
  **0.030–0.091**. Never quote the 0.00 as a general result.
- `RISK-25` (verdicts fragile to sub-perceptual input noise) and `RISK-27`
  (`omni.physx.forcefields` absent from this build) are open.
- `FR-06`: PX4 SITL is proven on aarch64; the Isaac-side bridge is still a
  17-call-site port (`RISK-02`(b)) with a protocol-drift risk (`RISK-26`).
- `tools/run_livestream.sh` remains untracked and belongs to another session.

## 2026-07-29 — Session 11d: the Pegasus/PX4 investigation — PX4 runs on aarch64; the bridge is a 17-call-site port

`FR-06` (real flight dynamics) had sat behind `RISK-02` — "Pegasus on aarch64 is
unproven" — for weeks. Investigated it. **It was two risks wearing one label, and
the scary half is closed.**

**⭐ PX4 SITL runs natively on this Spark.** Verified, not assumed:
`px4io/px4-sitl` publishes a real `linux/arm64` manifest — 119 MB, native aarch64
ELF, Ubuntu 24.04 base, image built 2026-07-08 (≈v1.18.0-beta1). It boots to
`INFO [simulator_mavlink] Waiting for simulator to accept connection on TCP port
4560`, and that port is reachable from the host. `tools/px4_sitl_smoke.py`
reproduces it in ~30 s and exits non-zero if the seam does not open.

**Two documented routes that do NOT work, recorded so nobody burns a day on them:**
- Pegasus's install guide has you **build PX4 v1.14.3 from source** — a 2023
  release, on a 2024 distro, on an architecture its docs never mention.
- PX4's own "pre-built SITL packages" page advertises Ubuntu 24.04 **arm64
  `.deb`s**; the tagged GitHub releases carry only a VOXL *board* package. The page
  documents `main`, not the releases.

⚠ **`PX4_SIM_MODEL` is a trap worth knowing.** `none_*` selects the external
simulator (Isaac owns physics, PX4 owns control — what we want). Left unset, this
image runs **SIH**, where PX4 simulates its own dynamics: it starts cleanly, looks
healthy, and tells you nothing about your twin.

**The Isaac-side bridge is the real remaining work — now sized instead of feared.**
No Pegasus release targets Isaac 6.x (v5.1.0, Oct 2025, targets 5.0/5.1 on Ubuntu
22.04/x86_64; Isaac 6.0 is still Early Developer Release, so the ecosystem lag is
expected). Rather than compare version numbers, I cloned v5.1.0 and probed all 26
of its `omni.*`/`isaacsim.*` imports inside a real headless 6.0.1 session:

| result | count | detail |
|---|---|---|
| resolve fine | **21/26** | the modern `isaacsim.core.*` surface is intact |
| present on disk, merely **not enabled** | 3 | `isaacsim.ros2.bridge`, `isaacsim.replicator.agent.core`, `omni.anim.graph.core` — **not port work** |
| genuinely gone | 2 | `omni.isaac.sensor` (peripheral) and **`omni.isaac.dynamic_control`** (load-bearing) |

The whole legacy `omni.isaac.*` namespace is absent from this build except
`omni.isaac.core_archive`. That distinction mattered: a bare import probe
*over-reports* absence, because Kit modules only import once their extension is
enabled — so I checked the extension folders on disk too, which moved three
"failures" out of the port estimate.

**The port is 17 call sites in 2 files** (`vehicle.py`, `multirotor.py`), all
funnelled through one accessor `Vehicle.get_dc_interface()`: rigid-body
handle/pose/velocity reads, `apply_body_force`/`apply_body_torque`, and
articulation DOF velocity (the *visual* rotor spin only). Every one maps onto
`isaacsim.core.prims` / `omni.physics.tensors`, both verified present on 6.0.1.

**Recommendation: a time-boxed fork-and-patch spike**, not a from-scratch bridge.
What Pegasus actually buys us is the multirotor dynamics and the **HIL sensor
models** (IMU/GPS/baro/mag) that PX4's EKF needs to arm and hold position; that is
where a hand-rolled MAVLink bridge would sink. `FR-07` keeps the kinematic
controller valid as the exit if the patch does not converge, and `FR-06`'s wording
is now corrected to require *PX4-governed* control rather than Pegasus
specifically.

**New risk found on the way — `RISK-26`:** Pegasus's `px4_mavlink_backend.py` was
written against PX4 **v1.14.3**; this container ships ~**v1.18.0-beta1**. The HIL
protocol and lockstep handshake are not guaranteed stable across four minor
releases, and it is untested because the bridge does not exist yet. Verify the
handshake before trusting any hover result.

Also flagged rather than discovered later: Pegasus installs with
`ISAACSIM_PYTHON -m pip install --editable` (forbidden here without an
`ENVIRONMENT.md` note) and its docs register the extension **through the GUI**
(must be `--ext-folder`, per the no-GUI-only-steps rule).

## 2026-07-29 — Session 11c: the KPIs get honest — measured determinism, spreads, and gates that actually gate

**Integrated and pushed.** Three commits (`a02d912` perception, `eca6a25` scenario +
verifier, `052ea9a` harness + docs), rebased onto 11b's tip — the remote had moved 8
commits ahead while this work was in flight. Only the two narrative docs conflicted;
`run.py` merged cleanly, including 11b's `build_keepouts(farm_cfg, layout)` fix,
which was the cross-branch trap 11b warned about. **294 Isaac-free tests pass, and
at each of the three commits individually**, not just at the tip. `docs/TASKS.md`'s
own stale-entry warning caught one of mine: my "the full-plant video path is too
expensive" item was already resolved in 10e *with my diagnosis shown to be wrong*,
so it is dropped rather than carried forward.

**PR #9 opened into `main`** — the whole 31-commit branch, which TASKS has listed as
the largest outstanding structural item for several sessions (74 → 294 tests).

**This closes the first two items on Session 11b's `Next` list** — "quantify VLM run-to-run variance before quoting any KPI as a constant" and the low-sun (01:30Z) KPI-03 point. Developed in parallel with 11b, so it touches `perception/`, `orchestrator/`, `kpi/` and the specs while 11b worked in `world/`; `run.py` was the only code file both touched and it merged cleanly.

**The pending item at the top of `TASKS.md` was "quantify VLM run-to-run variance
before quoting any KPI as a constant." Measuring it changed the diagnosis.**

**⭐ The finding: the model is deterministic; the *batching* is not.** Probed
directly against the live vLLM server on a real saved camera frame from the
KPI-03 run (`runs/20260727T183423/shade_R253-C050_confirm.png`) — no Isaac, no
stage, ~2 minutes:

| config | serial, N=5 each | concurrent, N=4 |
|---|---|---|
| `temperature: 0.0` (what shipped) | **1 distinct response / 5** | — |
| `+ seed` | 1 / 5 | — |
| `+ top_p 1.0 + top_k 1` | 1 / 5 | — |
| real code path (taxonomy prompt) | **4/4 identical** (`clean` → `soiled`) | **2× `soiled`, 2× `healthy`** |

So the Session-10b suspicion — "vLLM clamps temperature 0.0 to 0.01, so the
sampler wobbles" — is **wrong**: served serially it is byte-repeatable even with
no seed at all. Fire four identical requests *concurrently* and the same frame
comes back two ways. **Continuous batching changes the arithmetic and no
request-level parameter fixes it** (`RISK-23`). The mission FSM is serial, so
today's runs are reproducible — but a future parallelised fleet would silently
make every KPI non-reproducible, and that is now written down instead of assumed.

That leaves the actual Session-10b flip (`R258-C013`: `soiled` vs `hotspot`)
unexplained, with the **renderer** as the remaining suspect. It cannot be settled
retroactively — those runs saved no frames — so the twin now records the evidence:

**What shipped (all of it Isaac-free-tested, 196 tests, was 157):**
- **Decoding pinned and recorded.** `cosmos_reason.DEFAULT_SAMPLING` sends
  greedy (`top_k: 1`, `top_p: 1.0`, `seed: 0`) — `top_k` makes the argmax
  explicit so a server default cannot reintroduce sampling — and
  `provenance()` stamps endpoint, model and the exact sampling into every run
  record's new `perception` block, batching caveat included.
- **Frame digests, then frame thumbnails** — and the second one only exists
  because the first one *measured something*. A digest of every judged frame
  (`PanelResult.screen_frame_sha`) went in to turn "renderer or model?" into a
  lookup; the first 3-repeat run then reported **40/40 panels rendered
  differently between repeats**, so the probe below was written to find out how
  differently:

  | `tools/probe_render_determinism.py`, 640x480 | distinct digests | mean pixel Δ | 8x8-thumbnail Δ |
  |---|---|---|---|
  | camera held still, 4 captures | **4/4** | 0.85/255 | 0.36 LSB |
  | leave the pose and return | 4/4 | 2.39/255 | 0.59 LSB |
  | + 10 settling steps (does it converge?) | 4/4 | 0.70/255 | 0.20 LSB |
  | **a different panel** (shaded vs. control) | 2/2 | — | **35.5 LSB** |

  **RTX capture is not bit-reproducible even from a camera that never moves, and
  more settling steps do not fix it** (`RISK-24`). Which means the digest-equality
  attribution I had just shipped was **wrong in practice**: exact digests always
  differ in a rendered run, so every future flip would have been blamed on the
  renderer. The amplitude is the way out — noise is sub-LSB after block
  averaging, a real difference is 35 LSB, a ~60x gap — so attribution now compares
  an **8x8 luminance thumbnail with a measured tolerance** (`frame_thumbnail` +
  `thumbnails_differ`, 1.0 LSB: 1.7x above the worst noise, 35x below the smallest
  real signal). A quantised *hash* was tried first and rejected by measurement, not
  by taste: 256 blocks at 16 levels still flipped a boundary on 3 of 4 unchanged
  captures. Both counts are reported — "same bits" and "same picture" — because the
  gap between them *is* the renderer's noise.
- **`run.py --repeat N`** — one scenario N times, `variance.json`: min/median/max
  per metric (`MetricSpread.quote()` formats a number so it *cannot* be quoted
  bare), every disagreeing panel attributed `model` / `render` / `both` /
  `unknown`, plus **renderer stability on every panel** so a flip-free repeat set
  still answers `RISK-24`.
  ⚠ **The trap in repeats:** the first mission writes its verdict onto
  `pv:state`, so repeat 2 would read that verdict as ground truth and every
  `injected_state` in the record would be fiction. `pv_module.restore_state` +
  `snapshot_panels`/`restore_panels` rewind it — including the **inspection log**,
  because the log feeds `history` into the perception prompt, so a leftover line
  asks the next repeat a different question. Verified at the USD level under
  Isaac's own Python, not just against the fake backend.
- **`kpi_gates` are enforced (`FR-17`).** They had been *loaded, printed and
  never checked* in every scenario config since `IF-03` landed. Now
  `kpi/gates.py` judges them: `gates.json`, a printed verdict, non-zero exit on
  breach, **worst-of-N** for a repeat set (not the mean — a fleet flies each
  sortie once), and a gate naming an unmeasured metric **fails** rather than
  passing silently.
- **The second KPI-03 point** (`configs/scenarios/khavda_selfshade_lowsun.yaml`,
  was `TASKS.md` item 1): 01:30Z, sun 10.7°, cross-axis angle 78.5°, shadow
  chord 10.87 m → **54% of each module shaded at the 5 m pitch** versus 30% at
  02:00Z, in a dimmer scene so shading is confounded with underexposure. The
  geometry is asserted in `test_solar.py` before any run — the SLICE-3 lesson
  that a KPI-03 of 0.00 means nothing if the stimulus was absent.

- **`tools/kpi_variance.py`** aggregates *archived* run directories, so history
  can be re-examined without re-running the sim. Pointed at the three 24-panel
  Session-10b/10c runs it reproduces the finding as a number:

  ```
  records: 3  scenario=demo_video  seed=20260728
    false_fault_rate = 0 (N=3, identical across repeats)
    detection_rate = 0.9167 median (N=3, range 0.875–0.9167)  ⚠ varies
    per-panel agreement 0.958 (1/24 panels flipped)
    - R258-C013 (injected soiled): soiled / soiled / hotspot → cause=unknown
  ```

  Two of three runs said `soiled`, one said `hotspot`; **`KPI-03` was rock
  stable at 0.00 across all three** while `KPI-01` moved. `cause=unknown` is the
  honest verdict for those runs — they carry no frame digests, which is exactly
  the gap now closed. It also refuses to aggregate records whose scenario, seed
  or decoding config differ: that would be an A/B test wearing a variance report's
  clothes.

**⭐ Measured result — KPI-03 at low sun (`runs/20260728T200755`, live Reason-1):**

| | value |
|---|---|
| **false_fault_rate (KPI-03)** | **0.000 — N=3, identical across all three repeats** |
| detection_rate (KPI-01) | 1.000, N=3, identical |
| panels | 40 healthy, stride 14 across all five tables (control included) |
| per-panel agreement | 1.000 — 0/40 panels flipped |
| gate | `false_fault_rate_max: 0.05` → **PASS**, basis **worst-of-3** |
| wall | 825 s for 3 repeats |

**And the stimulus is proven, not assumed** (`tools/verify_shade.py`, promoted out
of a run directory into a real tool, PV-glass masking built in): shaded rows read
**77.5–83.0% dark glass** against the unshaded control's **40.0%** — a **+40.1
point differential**, versus +14 at 02:00Z. Full evidence in the run's
`STIMULUS.md`. So Reason-1 held clean on ~half-shaded, foreshortened modules in a
dimmer scene — one geometry, honestly reported, now with N and a gate behind it.

⚠ **Third instance of the same class of bug, caught by the tool's own check.** The
verifier first announced "NO STIMULUS" on this stage — because it picked the
control table by **row number**, and row numbering does not run west-to-east here
(R258 is westmost, R243 eastmost). It was comparing the wrong panel. Ordering is
now by stage x. Worth noting that the fail-loud check is what surfaced it: an
honest verifier that shouts is better than a silent one that agrees with you.

**Denominator caveat, stated because `--panel-stride` changes it:** 40 of 560
panels. The metric denominator is panels VISITED, not the block. A full 560-panel
census at 3 repeats would be ~5.6 h; the stride keeps all five tables at ~1/14
the cost.

### The repeat set WITH faults — where the harness earned its keep

The low-sun set is all-healthy, so nothing escalated and no flip could be
attributed. `demo_video` (20% faults, 24 panels, **7 escalations per repeat**)
closed that, `--repeat 3`, twice — and the two runs tell the whole story:

| | set A `runs/20260729T112424` | set B `runs/20260729T113839` (after the fix below) |
|---|---|---|
| KPI-03 false_fault_rate | 0.00 median, **range 0.00–0.053 ⚠ varies** | **0.000, N=3, identical** |
| KPI-01 detection_rate | 0.875 median, **range 0.833–0.917 ⚠ varies** | **0.875, N=3, identical** |
| panels flipped | 2/24 | 1/24 |
| causes | `model` 1, `both` 1 | `model` 1 |

**⭐ 1. The Session-10b flip reproduced and is attributed.** `R258-C013` (injected
`soiled`) → `soiled / hotspot / hotspot`, with screen Δ 1.25 and confirm Δ 1.28
LSB — both inside the measured noise tolerance, so **the model saw the same
picture and read it differently** (`cause=model`). Its notes are two confident,
incompatible readings: "opaque tan or brown patch… soiling" vs. "a small, bright
red/orange spot… localized overheating".

**Be precise about what that means.** It is model **fragility**, not model
nondeterminism: given identical *bytes* the model is byte-repeatable (measured,
15/15), but the renderer never sends identical bytes, and a difference invisible
at picture level was enough to change the verdict. Set B flipped a *different*
panel the same way (`R258-C014`, injected `hotspot` → `healthy/healthy/soiled`),
which confirms the mechanism rather than the panel. Logged as **`RISK-25`** — and
deliberately not treated as a sim bug: a real camera has sensor noise too, so a
model that flips on it flips in the field. Both flips are on **faulted** panels,
so this is `KPI-01` fragility; `KPI-03` was 0.000 across all six runs.

**⚠⚠ 2. The harness found a bug in OUR code that was manufacturing false faults.**
`R258-C004` (healthy) came back `healthy / healthy / unknown` and pushed KPI-03
from 0.00 to 0.053. The model was not at fault — it answered
`"fault_type": "healthy"`, confidence 1.0 — and then **closed its ```json fence
without closing the brace**. No `}` existed anywhere, so `_parse_json_response`
returned `{}`, the fail-safe mapped it to `unknown`, and because `unknown !=
healthy` that scored as a **false fault**. *One missing character moved the
project's headline metric.* The parser now strips fences and repairs an unclosed
object (still failing closed on genuine garbage), tested against the verbatim
response; set B re-ran the identical scenario and KPI-03 came back **0.000,
identical across three repeats**.

**Left as an owner decision, flagged at the definition site:** `unknown` is
`!= healthy`, so "the model reported a fault that isn't there" and "we lost the
model's answer" score identically in KPI-03 — opposite fixes. Redefining it
touches a locked contract (§6.5 / `FR-03`); the recommendation is to report an
abstention rate alongside rather than change the metric.

**3. The "materially different picture" flag is one panel per CAMERA, explained.**
Both sets flagged the first frame each camera takes in a repeat — repeat 1
approaches from the stage home pose, later repeats from the previous repeat's last
panel, so the renderer's accumulation history genuinely differs. The confirm-camera
figure reproduced at **38.11 and 38.05 LSB** across two independent runs, which is
what makes it an approach artifact rather than noise. Verdicts on those panels were
unaffected in both sets.

**4. `KPI-01 = 0.875` is a discrimination problem before it is a variance problem.**
3 misses in 24, **2 of them stable across all repeats** (`R258-C013` reads
`hotspot` every time in set B; `R258-C014` reads `healthy` twice of three). The
`_STATE_DEFINITIONS` taxonomy block was added precisely to stop dust reading as a
hotspot — it reduced that confusion, it has not removed it.

## 2026-07-28 — Session 11b: audited the CAD ingest — BLOCK-02 is 100% in; the gap is that we only have ONE block's drawing

Asked to parse the DWG, cross-check the PDF, and rebuild `farm_builder`'s layout
generation because "only some panels/tables from that layout have been added".
**Audited it instead of rebuilding it, and the premise does not hold.** Findings,
all reproducible via the new `tools/audit_layout.py`:

**BLOCK-02 is completely ingested and completely built.** Independent count from the
plotted PDF's own vector geometry (not the ingest's self-report, which cannot
corroborate itself):

| | tables | 64.4 m | 96.5 m | 128.6 m | modules |
|---|---|---|---|---|---|
| ingest (DXF) | 273 | 6 | 8 | 259 | 30,016 |
| PDF vectors | 279 | 6 | 8 | 259 | — |

The residual of **6 is exactly the `DETAILS` entities the ingest reported skipping**
— three extra length pairs (66.4, 99.4, 132.5 m, two each) which are the **legend
swatches** showing one of each HSAT type, drawn a few metres longer than a real
table. Module arithmetic closes independently: 6x56 + 8x84 + 259x112 = 30,016, and
the layer names (`Interior HSAT (1x112)` etc.) state those counts. Geometry audit:
**0 overlaps, 0 missing dimensions, 0 pitch mismatches, 0 duplicate ids or
positions**, all `rot_deg` = 0. Nothing was approximated or silently skipped, so
there was nothing to rebuild — `farm_builder` already authors all 273 tables /
30,016 panels (verified on the stage). `--subset` is opt-in for fast builds; the
default is the whole block.

**⚠ The real gap is different, and bigger.** The title block reads *"BLOCK-02 PILE
FOUNDATION LAYOUT (PLOT: A10b - 567.5 MW)"*, sheets 1 and 2 of 2 — both sheets are
the same block. So the drawing we hold is **one ~18 MWdc DC block of a 567.5 MW
plot**, i.e. of order 3% of PLOT A10b, which is itself part of a much larger park.
Scaling the twin needs the *other blocks'* DC drawings, which we do not have.

**⚠ The master drawing cannot supply them.** `6841-Khavda Overall Master plant
layout` covers E 527k-552k / N 2,656k-2,677k (~25 x 21 km, and BLOCK-02 does fall
inside it), but it carries **no per-table geometry**. Measured: 400,878 vector paths
of which 92% are degenerate lines and only 49 are elongated at all, none with a
table's signature — against 435 elongated paths and 259 identical 631.4 x 11.2 pt
(56:1) table shapes in the one block sheet. Its own title block agrees: the block
drawing says *"FOR BLOCK LOCATION REFER OVERALL PLANT LAYOUT"* — the master gives
block **locations**, substations, 33 kV panels, gantries and ramps, not tables.

**⚠ Could NOT parse the DWG directly.** Both files are AC1032 (AutoCAD 2018).
`libredwg-tools` is not in the Ubuntu noble repos, no `dwg2dxf`/ODA converter is on
this box, and the DXF that produced the current layout is gone (gitignored). The
audit therefore corroborated the ingest from the **PDF**, which is sufficient to
answer "is it complete?" but is NOT a substitute for a DXF when ingesting new
geometry. To add blocks: export DXF from AutoCAD, or build LibreDWG.
(`tools/layout_from_pdf.py` still fails closed — its two calibration sources
disagree by 9.2%, so the PDF must never become the geometry source.)

**Calibrating the PDF cross-check took three anchors, two of them wrong** — worth
recording because both failures were silent and plausible:
- the **longest** elongated shape biased every length ~3% low (it is a legend
  swatch, longer than any real table);
- the **mode over all** elongated shapes was off by 30x (most shapes passing an
  aspect filter are thin hatch and dimension lines);
- correct: the mode **within 80% of the longest**, which lands on the 259 identical
  full-length tables — the one anchor a DC sheet is guaranteed to carry many of.

**209 Isaac-free tests** (was 201). New: `tools/audit_layout.py` (exits non-zero if
a layout cannot be reconciled with its drawing) + `tests/test_audit_layout.py`.

**⚠ Viewing this build: run from the WORKTREE, not the main checkout.** The siting /
roads / fleet-scale work lives on `feat/siting-roads-scale`. `assets/khavda_infra.usd`
was built from it and has **scattered** turbines, but the main checkout's
`configs/farm_khavda_block02.yaml` has no `turbine_scatter` block and its `run.py`
does not pass `layout` to `build_keepouts`. Mixing them puts the enforced no-fly
volumes at the OLD explicit turbine positions while the towers stand somewhere else —
the planner would route a drone through a tower and report a clean run. Code, config
and USD have to come from the same branch.

```bash
cd /home/simulationhub/solar-twin/.claude/worktrees/terrain-infra
DISPLAY=:1 PYTHONPATH=src "$ISAACSIM_PYTHON_EXE" -m solar_twin.run \
    configs/farm_khavda_block02.yaml configs/mission.yaml \
    --farm-usd assets/khavda_infra.usd --gui --live --max-panels 12
```
`--gui` alone teleports; `--live` is what makes the fleet fly. `mission.yaml` is on
`perception: ground_truth`, which is the watchable setting — `cosmos_reason` blocks
~12 s per panel inside a urllib call and freezes the window for that whole time.
⚠ The Cosmos Reason vLLM is currently holding **44 GB** of the unified 121 GB
(61 GB used overall). Isaac Sim fits alongside that, but it is not a lot of headroom:
if the sim OOMs, stop the container rather than lowering the render settings.

## 2026-07-28 — Session 11: wake-sited turbines, roads on the grade, fleet at named real scale

Worked a four-part brief (terrain / roads / robot+drone scale / windmill placement).
**Part 1 was already shipped and two of its instructions would have regressed it**,
so that is recorded first; parts 2-4 were real and are built.

**⚠ Terrain: the brief's premise was out of date.** It opened "current known gap:
terrain is flat with no elevation data". Session 10d shipped real Copernicus GLO-30
DEM terrain (`world/dem.py`, `assets/dem/khavda_block02.*`, `terrain: kind: dem`),
panel z already spans 1.10 m. Two of its instructions were declined, with reasons:
- **"Use SRTM 30m"** — 10d chose Copernicus *because* SRTM/NASADEM/AW3D30 all
  require an Earthdata or JAXA login and a reproducible pipeline must not depend on
  someone's password. Switching would trade a no-auth source for a gated one.
- **"Re-run the tilt/height calculation ... at each table's (x, y)"** — this is what
  the code deliberately does NOT do. A torque tube is a rigid beam up to 128 m long;
  sampling per module bends it into the shape of the desert. `fit_line` least-squares
  a straight line through the grade, and its residual is the pile-height variation
  the row needs (worst 0.461 m). Per-table draping would err in the flattering
  direction (`NFR-07`).
- Coordinates in the brief (23.85N, 69.55E) are ~27 km from the ingested block
  (24.0915N, 69.4205E, EPSG:32642 from the vendor CAD). The CAD survey coordinates
  are authoritative.
- **Ground albedo left alone on purpose.** The brief asked for a salt-flat material
  *and* asked not to break the VLM's shadow contrast. Those conflict: albedo feeds
  the KPI-03 false-fault measurement, so changing it invalidates the 0.00-on-560
  result until re-measured. Flagged, not silently changed.

**Roads — and an honest negative result.** `derived_ew_roads` looks for east-west
corridors the way `derived_roads` looks for north-south ones: gaps between the
merged northing bands of the tables. Measured on the real block, the bands are
`(0,128.6) (129.6,258.2) (259.2,387.7) (388.7,517.3) (518.3,646.9)` — **gaps of
exactly 1.0 m**, which are the physical end gaps between tracker tables, not
corridors. So **BLOCK-02's drawing contains no cross arterial**, and the brief's
"main arterial roads between block sections" cannot be honoured from the CAD. There
is deliberately no `inferred_ew_road`: an invented arterial would have to run
*through* surveyed tracker tables, which does not add an assumption so much as
contradict the drawing. Cross traffic uses the perimeter.
What did land: **access spurs** to all five inverter stations (they previously sat
in the array with no way in) and **roads that follow the grade**. A road was one
flat quad at the height of its own centre; `subdivide_strip` cuts it into <=25 m
segments (finer than GLO-30's 20 m grid) sampled individually. Measured per road on
the real DEM: z spans **0.20-0.87 m**, i.e. the perimeter-south road had been
floating/burying by nearly a metre. Roads 5 -> 10 logical (135 prims).

**⭐ Turbines: a lattice became a wake-constrained scatter.** The old field was five
hand-written positions — two columns at fixed eastings, evenly spaced; `lattice_score`
1.00. `world/siting.py` sites them by seeded dart-throwing under a spacing rule that
is an **ellipse, not a circle**: ~7 rotor diameters along the prevailing wind and 4
across, because a wake is long and narrow. A circular Poisson-disk radius cannot
express that — set it to the downwind figure and you waste the site, set it to the
crosswind figure and you allow illegal wake overlap. Shipped field scores **0.40**.
- **The keep-outs could have silently drifted.** `build_keepouts` read
  `farm_cfg["turbines"]` directly, so a scattered build would have enforced no-fly
  volumes at the OLD positions while the towers stood elsewhere — the planner would
  route a drone through a tower. Both now resolve through the same
  `siting.resolve_turbines`, with a test asserting they agree.
- An explicit `turbines:` list still WINS over the scatter, so a KPI run pinned to
  known positions restores with `turbine_scatter.enabled: false`.
- ⚠ Measured trade-off in `ring_depth_d`: 6.0D scatters to 1.2 km (lattice 0.00 but
  the machines read as distant specks), 2.5D keeps them 210-560 m out with presence
  at true scale but lattice 0.40 — a narrow band constrains one axis. Shipped 2.5D.
  A constrained band is not the old two-column lattice.

**Fleet scale: two errors that only measurement found.** Geometry now derives from
named real platforms (`world/fleet_specs.py`) instead of literals:
- The drone was an `arm=0.34` constant making a 0.96 m motor-to-motor diagonal while
  its docstring claimed "~0.9 m", and neither figure was tied to a machine. Now
  **DJI M350-class: 0.895 m diagonal, 0.533 m props**. ⚠ The brief asked for 0.4-0.6 m
  diagonal, which is Mavic-3-class; utility PV IR inspection flies M300/M350-class
  because that is what carries a radiometric thermal payload. Both are presets
  (`m350`, `mavic3t`) — the size question is really a payload question.
- **The rover measured 0.844 m wide against a published 0.670 m — 26% too wide.**
  Wheels were offset by a fraction of the body width; a platform's published width is
  its OVERALL width, wheels included. Also the sensor head was centred ON the stated
  total height, so the machine measured half a head taller than it claimed. Both
  fixed; the authored envelope now measures 0.670 x 1.010 x 1.050 m exactly.
- ⚠ The brief's rover spec ("1.0-1.2 m long x 0.6 m wide x **0.4-0.5 m tall including
  sensor mast**") is not satisfiable: 0.33 m wheels plus a deck reach 0.39 m before any
  mast exists. Resolved by making body height and payload height two numbers —
  Husky A200-class body 0.390 m, total with mast 1.050 m.
- `fits_between_rows` / `standoff_is_safe` are new checks with teeth: motion is
  kinematic, so a standoff that intersects a module renders as a clean flight through
  solid glass rather than a crash. (A 3 m-diagonal machine does still fit a 5.5 m
  aisle — worth knowing, and not obvious.)

**Instancing budget held: build 82.35 s** (budget ~90 s), 75,637 prims, 29,416 panels
from one prototype. **201 Isaac-free tests** (was 197) + 8 pxr-guarded geometry tests
that measure the authored robots and skip off the Spark.

**Next:** unchanged — VLM run-to-run variance, the low-sun KPI-03 point, Pegasus/PX4,
and the rest of the balance of plant (substation/control room/trenches, plus the
graded civil surface GLO-30 cannot supply).

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
tours comfortably in ~20 min. So the fix was never "make frames cheaper", it was
`--budget-minutes`: state the budget, project against it, and shorten the shots
proportionally (loudly — text cards are never cut).

⚠ **And then I budgeted off the wrong number.** 0.71 s is the RENDER; a frame
written to the mp4 also pays the PIL overlay and the encode. Measured over the
whole 720p tour: **0.895 s**, 1,300 rendered frames in 1,164 s, per-chapter spread
0.878-0.909. `SECONDS_PER_FRAME` is **0.92** — a budget projected off 0.71
under-promises by ~25% and would overrun the cap it exists to enforce.
Over-projecting shortens the tour a little and says so; under-projecting overruns
in silence, which is the worse failure. (Projected 20.6 min, actual 19.9.)
A 540p smoke had suggested the fleet chapter cost ~1.05 s/frame because it renders
two cameras; on the real tour it came in at **0.904**, inside the ordinary spread.
That was short-chapter overhead being amortised over 55 frames, not the second
camera — a reminder to measure the artifact rather than the probe.

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

**⚠ Two overlay defects the 540p smokes could not show**, both found by reading
the finished 720p frames — worth the habit of checking the artifact at delivery
size:
- The opening card rendered **"Khavda BLOCK-02 — the digital twin so fa"**, clipped
  at the frame edge. Headings are single-line by design, so they cannot wrap out of
  trouble; `_fit_font` shrinks them to fit instead. A video whose entire purpose is
  honest reporting must not open on a truncated sentence.
- Card detail lines sat at a FRACTION of the line pitch, so label descenders
  collided with them. They now clear the label's own measured height. (The in-shot
  checklist had already been rebuilt on measured metrics for the same reason.)

**195 Isaac-free tests (was 157), 3 skipped.**

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
