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
rotor spin and rolling wheels. 108 Isaac-free tests.

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
PYTHONPATH=src python3 -m pytest -q                   # 100 tests, ~4 s, no GPU

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

Re-generate the site file from the vendor CAD with
`tools/layout_from_dxf.py` (DWG → DXF via LibreDWG first; see
`docs/ENVIRONMENT.md`).

## Layout
Pure-python (imports without Isaac): `schema/`, `perception/`, `transport/base`,
`control/base`, `orchestrator/`, `world/layout.py`, `run.py`, `tests/`.
Isaac-bound (run under `./python.sh`): `world/farm_builder`, `world/sim_runtime`,
`transport/sim_native`, `transport/ros2_bridge`.
