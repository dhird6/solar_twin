# ENVIRONMENT — exact Spark build, versions, how to run

> Referenced by CLAUDE.md and the bible. Capture what is **actually true on this
> box**, not what the docs assume. ⚠-verify everything version-specific.

## Platform (verified 2026-07-21, Day-1 de-risk)
- **Arch:** `aarch64` (DGX Spark, GB10, unified memory). GPU driver 580.142.
- **CUDA:** **13.0** (`nvcc` release 13.0.88) — satisfies the ≥13 golden rule.
- **System Python:** 3.12.3 (`/usr/bin/python3`) — used for the pure-python
  "Brain" half + tests. Isaac Sim has its own bundled Python (`./python.sh`).
- **Isaac Sim:** built from source at `/home/simulationhub/IsaacSim`, version
  **`6.0.1-rc.7`** (from `IsaacSim/VERSION`). Launch Python:
  `/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh`.
  ⚠ **VERSION DISCREPANCY:** CLAUDE.md and `docs/PROJECT_BIBLE.md` say "Isaac
  Sim **5.1**"; the installed build is **6.0.1-rc.7**. API namespaces / asset
  paths differ — **verify every Isaac snippet against 6.0, not 5.1**, and treat
  the bible's 5.1-specific paths as hints, not truth. (Decide whether to update
  the 5.1 references in CLAUDE.md/bible to 6.0.1.)
- **Isaac Lab:** not yet verified (symlink `_isaac_sim`).
- **PyTorch (cu13):** lives in Isaac's bundled Python — not yet captured.
- **Isaac Sim build commit:** ⚠ not yet captured (`git -C ~/IsaacSim rev-parse HEAD`).
- **Local VLM (bonus):** a `Qwen2.5-VL-72B-AWQ` vLLM server runs on
  `localhost:8000` (OpenAI-compatible, served name `qwen2.5-vl-72b`). Candidate
  backend for `perception/cosmos_reason.py` when we swap the ground-truth stub.
  Note it holds ~70% of GPU memory — Isaac Sim launches must share the GB10.

## Key finding — `usd-core` has no aarch64 wheel
`pip install usd-core` fails on this box (`No matching distribution found`, py3.12
aarch64). So **`pxr` is only available under Isaac Sim's bundled Python here**,
not in system Python / CI on the Spark.

**Consequence (already designed around):** `schema/pv_module.py` imports `pxr`
*lazily inside the USD functions only*; its pure-python contract (taxonomy,
`PanelRecord`, log/validation, `local_to_geo`) imports and tests without pxr.
The bible §11 line "schema tests against in-memory USD in CI" holds on x86 CI
(where usd-core installs) but **not on the aarch64 Spark** — there, the pxr
adapter is exercised by an Isaac smoke test, not a pip-usd-core unit test.

## Python deps (pure-python half)
Installed with `pip install --break-system-packages --user ...`:
- `pyyaml` (present system-wide)
- `pytest` 9.1.1

`pyproject.toml` lists only pure-python deps on purpose. Never `pip install` into
Isaac Sim's bundled Python without a note here.

## How to run
**Brain spine (no Isaac, runs anywhere incl. this box):**
```bash
pytest                                   # 31 tests, ~0.03s, no GPU
PYTHONPATH=src python3 -m solar_twin.run configs/farm.yaml configs/mission.yaml --backend fake
```
Writes `runs/<ts>/` with `results.json` (injected-vs-detected, detection_rate).

**Full mission (Isaac world, on the Spark — Slice 0 Day 6-8+):**
```bash
./python.sh -m solar_twin.run configs/farm.yaml configs/mission.yaml   # --backend sim_native (default)
```
`--backend sim_native` currently raises `NotImplementedError` until
`world/farm_builder.py`, `world/sim_runtime.py`, and `transport/sim_native.py`
are built and wired into `run._build_backend`.

## ROS 2 status (updated 2026-07-21)
- **Distro: Jazzy** (Ubuntu 24.04 native; Isaac 6.0 bridge bundles jazzy+humble).
  Installed via `tools/install_ros2_jazzy.sh` → `/opt/ros/jazzy`, 201 pkgs.
  `ros2 doctor` = **all 5 checks passed**; `rclpy` imports; `sensor_msgs` /
  `geometry_msgs` present. Source with `source /opt/ros/jazzy/setup.bash`.
- **Installed `ros-jazzy-ros-base`, NOT `-desktop`:** desktop pulls
  `python3-vtk9`, which **conflicts with the system `python3-paraview 5.11.2`**
  (OpenFOAM CFD tooling). So RViz2/VTK is deferred; use `ros2 topic echo`/`hz`
  for the camera check. (To get RViz2 later: resolve the paraview/vtk conflict
  in an isolated env or container — do not remove python3-paraview, it's not ours.)
- **Do NOT `apt upgrade` this box.** Near-miss on 2026-07-21: a blanket upgrade
  would bump CUDA/nvidia/docker/systemd under the source-built Isaac Sim + live
  vLLM. Install scoped packages only.

### Still open (Day-1 remainder)
```
[ ] ROS 2 camera publish works from Isaac Sim 6.0 on this Spark?   result: UNKNOWN
[x] ROS 2 distro installed:                        jazzy (/opt/ros/jazzy)
[ ] Isaac Sim build commit:                        (git -C ~/IsaacSim rev-parse HEAD)
[ ] PyTorch(cu13) version (Isaac python):           ____
[ ] Isaac Sim launches + renders a sample:          not yet (sim not running)
```
Next: launch Isaac Sim, enable `isaacsim.ros2.bridge`, publish a camera image,
verify with `ros2 topic echo` (image QoS = **Best Effort**), record result here.
