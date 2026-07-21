# solar-twin

Autonomous solar-farm inspection **digital twin**. A robot fleet inspects panels
in an Isaac Sim world; verdicts are written back onto USD panel prims; a closed
maintenance loop is the goal. See `CLAUDE.md` (operating brief) and
`docs/PROJECT_BIBLE.md` (full plan).

## Status
**Slice 0, Brain half complete** — the Isaac-free spine (schema, interfaces,
escalation FSM, run record) runs and is tested end-to-end. The Isaac world half
(`world/`, sim-native transport) is next, on the Spark. Rolling status in
[`SESSIONS.md`](SESSIONS.md).

## Quickstart (no GPU, no Isaac)
```bash
pip install --break-system-packages --user pytest    # pyyaml usually present
pytest                                                # 31 tests

# Run a mission against the pure-python backend -> runs/<ts>/results.json
PYTHONPATH=src python3 -m solar_twin.run configs/farm.yaml configs/mission.yaml --backend fake
```

## Full mission (Isaac world, on the Spark)
```bash
./python.sh -m solar_twin.run configs/farm.yaml configs/mission.yaml
```
Requires the World half (`world/farm_builder.py`, `world/sim_runtime.py`,
`transport/sim_native.py`) — not built yet. See `docs/ENVIRONMENT.md`.

## Layout
Pure-python (imports without Isaac): `schema/`, `perception/`, `transport/base`,
`control/base`, `orchestrator/`, `world/layout.py`, `run.py`, `tests/`.
Isaac-bound (run under `./python.sh`): `world/farm_builder`, `world/sim_runtime`,
`transport/sim_native`, `transport/ros2_bridge`.
