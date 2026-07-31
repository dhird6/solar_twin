# solar-twin

Autonomous solar-farm inspection **digital twin**. A robot fleet inspects panels
in an Isaac Sim world; verdicts are written back onto USD panel prims; a closed
maintenance loop is the goal. See `CLAUDE.md` (operating brief) and
`docs/PROJECT_BIBLE.md` (full plan).

## Status
**Slice 0-3 shipped; the twin now runs on a REAL plant.** Khavda PLOT A10b
BLOCK-02 is ingested from the vendor CAD — 273 tracker tables, 30,016 modules at
exact survey coordinates (EPSG:32642) — built in Isaac and inspected end-to-end
(560-panel subset: detection_rate 1.00 on 11/11 seeded faults, 105 s). Panels are
sun-tracking HSAT; the fleet is real quadcopter + rover geometry with heading,
rotor spin and rolling wheels. 785 Isaac-free tests.

**KPI-03 (false-fault rate) = 0.00 on 560 healthy panels**, measured against a
*verified* stimulus: at low sun the HSAT trackers pin at their 60° stop and shade
~30% of each row, with the eastmost table acting as an unshaded control in the
same run. Escalation rate was 1.8% in both groups, so the shadow did not shift the
screening decision either. See `runs/<ts>/STIMULUS.md` for the evidence a shadow
was actually on the glass — a false-fault rate measured without that proof is
worthless (it read 0.00 once before with no shadow anywhere near a panel).

Rolling status and the next steps are in [`SESSIONS.md`](SESSIONS.md) — **read it
first**.

## Quickstart (no GPU, no Isaac)
```bash
pip install --break-system-packages --user pytest    # pyyaml usually present
PYTHONPATH=src python3 -m pytest -q                   # 247 tests, ~6 s, no GPU

# Run a mission against the pure-python backend -> runs/<ts>/results.json
PYTHONPATH=src python3 -m solar_twin.run configs/farm.yaml configs/mission.yaml --backend fake
```

## Full mission (Isaac world, on the Spark)
`python.sh` is **Isaac Sim's** launcher, not a file in this repo. Run from the
project root with an absolute path, and set `PYTHONPATH` (the package is not
installed into Isaac's bundled Python):

```bash
ISAAC=/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh

# The REAL plant (Khavda BLOCK-02). --subset keeps it to 5 tracker tables /
# 560 panels / ~42k prims; the full 273 tables is ~2.2M prims and needs the
# instancing work (IF-09) first. Pass the SAME --subset to both commands.
PYTHONPATH=src $ISAAC -m solar_twin.world.farm_builder \
    configs/farm_khavda_block02.yaml --subset 5 --out assets/khavda.usd
PYTHONPATH=src $ISAAC -m solar_twin.run \
    configs/farm_khavda_block02.yaml configs/mission.yaml \
    --subset 5 --farm-usd assets/khavda.usd

# The original procedural 10-panel row still works unchanged:
PYTHONPATH=src $ISAAC -m solar_twin.run configs/farm.yaml configs/mission.yaml
```

## The whole plant
All 273 tracker tables / 30,016 modules build into one stage (75k prims via
`IF-09` instancing, ~85 s), with access roads, perimeter fencing and inverter
stations. `world/site.py` tags every element `derived` (read from the vendor CAD
— e.g. its 11 m internal corridor) or `inferred` (standard plant practice placed
by us, since the drawing describes hardware only), and the build prints the split.

```bash
PYTHONPATH=src $ISAAC -m solar_twin.world.farm_builder \
    configs/farm_khavda_block02.yaml --out assets/khavda_full.usd
PYTHONPATH=src $ISAAC -m solar_twin.world.flythrough assets/khavda_full.usd \
    --out assets/khavda_flythrough.mp4
```

## A run you can watch
`--video` writes `runs/<ts>/inspection.mp4`: a chase camera following the fleet
down the row, the drone's own camera inset, and the panel / phase / verdict
captioned as it happens. It switches the controller from teleport to
**interpolated** motion (~10x the sim steps), so pair it with `--max-panels` —
it is a demo, not a measurement.

```bash
PYTHONPATH=src $ISAAC -m solar_twin.world.farm_builder \
    --scenario configs/scenarios/demo_video.yaml --subset 1 --out assets/demo.usd
PYTHONPATH=src $ISAAC -m solar_twin.run \
    --scenario configs/scenarios/demo_video.yaml --subset 1 \
    --farm-usd assets/demo.usd --video --max-panels 24
```

## A status tour you can hand to a reviewer
`world/plant_tour.py` renders the annotated tour: the same plant, but every shot
carries a checklist of what is in it, tagged **built** (filled dot) / **inferred —
ours, not the drawing's** (barred dot) / **not modelled** (hollow dot), and it
closes on the backlog. Every number in the overlay is counted off the stage or
read from a generated sidecar, so the captions cannot drift from the build.

```bash
PYTHONPATH=src $ISAAC -m solar_twin.world.plant_tour assets/khavda_full.usd \
    --layout configs/layouts/khavda_a10b_block02.yaml \
    --dem assets/dem/khavda_block02.yaml \
    --out assets/plant_status_tour.mp4 --budget-minutes 30
```

`--budget-minutes` is a wall-clock budget. A finished frame costs **~0.90 s**
end-to-end here (a 0.71 s render, plus the overlay and the encode) and — measured,
not assumed — that cost does *not* rise at ground level and is the same at 540p and
720p, so a tour's cost is set by its frame count alone. Over budget, the shots are
shortened proportionally and the shortening is logged; the text cards are never
cut. The 85 s tour above is 1,300 rendered frames, ~20 min.

So the three video artifacts answer three different questions:

| script | question |
|---|---|
| `world/flythrough.py` | what does the site look like? |
| `run.py --video` | what did the fleet do on this run? |
| `world/plant_tour.py` | which parts of the twin are real, and what is missing? |

## Site layout, turbine siting and fleet scale
`world/site.py` derives roads from the drawing (a corridor the CAD leaves empty IS a
road) and infers the rest, tagging every element `derived` or `inferred`. Roads
follow the grade: each strip is cut into <=25 m segments and sampled individually,
because one flat quad per road floated or buried itself by up to 0.87 m on the real
DEM. Inverter stations get access spurs.

⚠ **BLOCK-02's drawing contains no east-west vehicle corridor** — its five table
bands are separated by 1.0 m end gaps, not roads. `derived_ew_roads` therefore finds
none, and there is deliberately no inferred counterpart: an invented arterial would
have to run through surveyed tracker tables. Cross traffic uses the perimeter.

`world/siting.py` sites wind turbines the way a wind farm is laid out — seeded
dart-throwing under a **wake** constraint that is an ellipse (7 rotor diameters
downwind, 4 across), not a circle, plus a setback that keeps blade shadows off the
panels. `lattice_score` makes "not a grid" measurable: the old hand-written field of
two evenly-spaced columns scores 1.00, the shipped scatter 0.40. An explicit
`turbines:` list still wins, so a KPI run pinned to known positions restores with
`turbine_scatter.enabled: false`. Keep-outs resolve through the same function as the
geometry, so the enforced no-fly volumes cannot drift from the towers.

`world/fleet_specs.py` gives the fleet named real platforms rather than plausible
sizes — a **DJI M350-class** drone (0.895 m motor-to-motor, 0.533 m props; the class
that carries a radiometric thermal payload) and a **Husky A200-class** rover
(0.990 x 0.670 x 0.390 m body, 0.330 m wheels, 1.050 m with its mast). Body height
and payload height are reported separately, because a masted rover cannot be 0.4-0.5 m
tall when its wheels and deck already reach 0.39 m. `tests/test_robot_builder_usd.py`
measures the authored geometry against those figures.

## Is the layout the whole drawing?
`tools/audit_layout.py` answers that with evidence rather than the ingest's own
word — it recounts the hardware independently from the plotted PDF's vector geometry
and reconciles the two, and lists anything ambiguous (overlapping tables, missing
dimension data, a length that disagrees with its own module count) instead of
approximating it. Exits non-zero if a layout cannot be reconciled.

```bash
python3 tools/audit_layout.py configs/layouts/khavda_a10b_block02.yaml \
    --pdf solar_plant_layout/6024-E-A10-PLE-DC-L-I-0002_01.pdf
```

For BLOCK-02 it reconciles exactly: **273 tables / 30,016 modules ingested**, 279
table-shaped paths in the PDF, residual 6 = the `DETAILS` legend swatches showing one
of each HSAT type. 0 overlaps, 0 missing dimensions, 0 pitch mismatches.

⚠ **Scope of what we hold.** That drawing is *"BLOCK-02 PILE FOUNDATION LAYOUT (PLOT:
A10b - 567.5 MW)"*, sheets 1-2 of 2 — **one ~18 MWdc block**, fully ingested. The
rest of the plot needs the other blocks' DC drawings. The **overall master layout
cannot supply them**: it carries block locations, substations and 33 kV gear but no
per-table geometry (measured — 49 elongated paths in 400,878, none table-shaped,
versus 259 identical table shapes in the one block sheet). Both DWGs are AC1032 and
there is no DWG converter on this box, so new geometry needs a DXF export.

Re-generate the site file from the vendor CAD with
`tools/layout_from_dxf.py` (DWG → DXF via LibreDWG first; see
`docs/ENVIRONMENT.md`).

## A measurement you can defend
A KPI from a single run is a **sample**, not a constant: the world is seeded but
the VLM is only byte-reproducible while it is served serially — fire four
identical requests concurrently and the same frame comes back `soiled` twice and
`healthy` twice (measured; `docs/specs/08-platform-and-risk-register.md`
`RISK-23`). So:

- decoding is pinned greedy and **recorded** in every run record's `perception`
  block, alongside the endpoint and served model;
- `--repeat N` runs one scenario N times — rewinding panel state between repeats,
  or repeat 2 would read repeat 1's verdicts as ground truth — and writes
  `variance.json`: min/median/max per metric plus every panel the repeats
  disagreed about, each attributed to the **renderer** or the **model** by frame
  digest;
- a scenario's `kpi_gates` are **checked**, not just printed: `gates.json`, a
  printed verdict, non-zero exit on breach, worst-of-N for a repeat set, and an
  unmeasured gate counts as a failure rather than a silent pass.

```bash
# KPI-03 (false-fault rate) on the real block, tracker self-shading at low sun
PYTHONPATH=src $ISAAC -m solar_twin.world.farm_builder \
    --scenario configs/scenarios/khavda_selfshade_lowsun.yaml --subset 5 \
    --out assets/khavda_selfshade_lowsun.usd
PYTHONPATH=src $ISAAC -m solar_twin.run \
    --scenario configs/scenarios/khavda_selfshade_lowsun.yaml --subset 5 \
    --farm-usd assets/khavda_selfshade_lowsun.usd --panel-stride 14 --repeat 3
```

## Layout
Pure-python (imports without Isaac): `schema/`, `perception/`, `transport/base`,
`control/base`, `orchestrator/`, `world/layout.py`, `run.py`, `tests/`.
Isaac-bound (run under `./python.sh`): `world/farm_builder`, `world/sim_runtime`,
`transport/sim_native`, `transport/ros2_bridge`.
