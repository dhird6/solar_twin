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
- **Isaac Sim build commit:** `045ca8b` ("Isaac Sim Update 6.0.1", 2026-06-22).
- **ROS 2 bridge extension:** `isaacsim.ros2.bridge-5.1.2` (loads system rclpy).
- **Perception backend = Cosmos Reason (NOT Qwen).** `perception/cosmos_reason.py`
  targets **NVIDIA Cosmos Reason** (physical-AI VLM, `cosmos-reason1-7b`, a
  Qwen2.5-VL-7B arch) behind an OpenAI-compatible endpoint — the brain behind the
  `Perception` interface. The unrelated `Qwen2.5-VL-72B` vLLM that used to run on
  `localhost:8000` is stopped/disabled; do not wire it into perception. The
  **code side is fully wired and verified live** on this box: `cosmos_reason.py`
  encodes a camera frame (the `H x W x {3,4}` uint8 array from
  `Transport.capture`) into an OpenAI-style `image_url` PNG data-URL part; a live
  `assess`/`diagnose` round-trip through the real HTTP client + a served model
  succeeds. Flip `mission.yaml: perception: cosmos_reason` (opts already point at
  the local server) once the server below is up.

## Serving Cosmos Reason on the Spark (GB10 / sm_121)
**The NVIDIA Cosmos Reason *NIM* does NOT run on this GB10.** Tested
`nvcr.io/nim/nvidia/cosmos-reason1-7b:1.4.0` (and `:1.4.1`): weights load, then the
engine dies during vision-encoder profiling with
`'sm_121' is not a recognized processor ... LLVM ERROR: Cannot select: intrinsic
llvm.nvvm.shfl.sync.bfly.i32`. Root cause: the NIM's bundled Triton/LLVM/PyTorch is
compiled only through `sm_120`; the GB10 is `sm_121`. This is a known
ecosystem-wide gap (NVIDIA dev forum "NIM LLM Containers Fail on DGX Spark (GB10)";
vLLM issue #36821). `NIM_DISABLE_CUDA_GRAPH=1` (→ `enforce_eager`) does **not** fix
it — the Triton JIT path still targets `sm_121`.

**What works: mainline vLLM built for `sm_121a`** (`sm_121` is binary-compatible
with `sm_120`). Serve the model's bf16 HF weights (already cached locally by the
NIM pull, under `~/.cache/nim/ngc/hub/models--nim--nvidia--cosmos-reason1-7b/`, rev
`1.1-bf16-hf`) with the cu130-nightly image. Mount the **whole repo dir** (not just
the snapshot — its files are symlinks into `../../blobs/`):
```bash
REPO=~/.cache/nim/ngc/hub/models--nim--nvidia--cosmos-reason1-7b
docker run -d --name vllm-cosmos --ipc=host --gpus all -p 8000:8000 \
  -v "$REPO":/models/repo:ro \
  vllm/vllm-openai:cu130-nightly \
  /models/repo/snapshots/1.1-bf16-hf \
  --served-model-name nvidia/cosmos-reason1-7b \
  --trust-remote-code --max-model-len 32768 \
  --gpu-memory-utilization 0.85 --max-num-seqs 4
```
Ready in ~3–4 min (weights load ~100 s, then warmup); health: `curl localhost:8000/v1/models`.
⚠ `--gpu-memory-utilization 0.85` grabs ~98 GB of the unified 121 GB — fine for
perception alone, but **lower it (~0.4) when running Isaac Sim + vLLM together**
on this one GB10, or the sim will OOM. ⚠ `cu130-nightly` is a moving tag; pin a
digest for reproducibility. ⚠ eager-ish paths → first-token latency is slow, so
`perception_opts.timeout` is set to 120 s.

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

### Day-1 result — ✅ ROS 2 camera path WORKS (verified 2026-07-21)
```
[x] ROS 2 camera publish works from Isaac Sim 6.0 on this Spark?   result: YES
[x] ROS 2 distro installed:                        jazzy (/opt/ros/jazzy)
[x] Isaac Sim build commit:                        045ca8b (6.0.1)
[x] Isaac Sim launches + renders headless:         yes (~14s warm start)
[ ] PyTorch(cu13) version (Isaac python):          not yet captured
```
Reproduce with `tools/day1_ros2_camera_check.py` (self-contained scene, no asset
download). Steps:
```
source /opt/ros/jazzy/setup.bash
/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh \
    tools/day1_ros2_camera_check.py        # publishes /rgb + /camera_info, loops
# in another sourced shell:
ros2 topic list          # -> /rgb /camera_info
ros2 topic hz /rgb       # -> ~50 Hz, real frames flowing
ros2 topic echo /camera_info --once   # -> 640x480, frame_id sim_camera, K populated
```
**Conclusion:** the feared Spark "ROS 2 sensor-rendering quirk" does NOT affect
this box/build. ROS 2 is a viable Transport, not just sim-native — so
`transport/ros2_bridge.py` can be built for real (not left a stub). Slice 0 still
defaults to sim-native for simplicity, but the seam is proven.
Note: sourcing system ROS 2 Jazzy before launch makes the bridge + `ros2` CLI
share middleware. RViz2 image display still needs the deferred VTK/paraview fix;
`ros2 topic echo/hz` is sufficient for verification.
