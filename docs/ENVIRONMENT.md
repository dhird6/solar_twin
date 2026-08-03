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
- **Isaac Lab:** `/home/simulationhub/IsaacLab`, **git tag `v3.0.0-beta2.patch1`**
  (commit `ffff603eaf`), with `_isaac_sim` →
  `/home/simulationhub/IsaacSim/_build/linux-aarch64/release`, i.e. paired with the
  **6.0.1-rc.7** build above. Verified 2026-07-30.
  ⚠⚠ **IT IS A BETA, AND `VERSION` HIDES THAT.** The `IsaacLab/VERSION` file reads a
  bare **`3.0.0`**, so anything quoting that file — including CLAUDE.md's "Isaac Lab
  3.0" — reads as a stable release. The git tag is the truth: `3.0.0-beta2.patch1`.
  ⚠ **And it cannot simply be downgraded.** Isaac Lab **2.3.0** is the current stable
  line but is built on **Isaac Sim 5.1**, while this box runs Isaac Sim 6.0.1-rc.7.
  So on this machine the only Isaac Lab that pairs with the installed Isaac Sim is a
  beta — an RL result from here carries *two* pre-release dependencies (Isaac Sim RC
  + Isaac Lab beta) and must be reported as such.
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

## Cosmos 3 Edge on the Spark — ✅ SERVES (verified 2026-07-27), but it is a GENERATOR
**Edge now runs on this GB10.** The earlier blocker was misdiagnosed twice over, so
record what is actually true.

**Why the Docker image never worked, and why patching it was hopeless.** The
`vllm/vllm-omni:cosmos3` image (Transformers 5.13.0, vLLM 0.25.0, diffusers 0.38.0)
knows only `cosmos3_omni`. That is NOT a stale-version problem that a model-type
alias fixes: Cosmos3 **Omni** (Nano/Super) is a **Qwen3-VL** architecture
(`Qwen3VLTextConfig` / `Qwen3VLVisionConfig`), while **Edge is Nemotron-based** with
its own `cosmos3_edge_text` / `cosmos3_edge_vision` / `cosmos3_edge_projector`
sub-configs and a projector Omni doesn't have. Aliasing `cosmos3_edge →
Cosmos3OmniConfig` dies on `KeyError: 'cosmos3_edge_vision'`, and forcing it would
map Edge weights onto Qwen3-VL classes. There is also **no newer image**: the
`cosmos3` arm64 layer and `cosmos3-arm64` are the same digest (`sha256:c386850…`),
both 2026-07-20. Do not chase image tags.

**What works — vllm-omni from `main` in its own venv** (`vllm-omni` is a *plugin*: it
does NOT depend on `vllm`, so install both; it pins `diffusers==0.38.0` on purpose
and supplies its own `Cosmos3EdgeVFMTransformer` / `Cosmos3OmniDiffusersPipeline`):
```bash
uv venv --python 3.13 --seed --managed-python /home/simulationhub/venvs/vllm-omni-edge
uv pip install --python /home/simulationhub/venvs/vllm-omni-edge/bin/python \
  --torch-backend=cu130 "vllm-omni @ git+https://github.com/vllm-project/vllm-omni.git@main"
uv pip install --python /home/simulationhub/venvs/vllm-omni-edge/bin/python \
  --torch-backend=cu130 "vllm==0.25.0"        # aarch64 wheel exists on PyPI
/home/simulationhub/venvs/vllm-omni-edge/bin/vllm serve nvidia/Cosmos3-Edge \
  --omni --no-guardrails --host 127.0.0.1 --port 8000 --init-timeout 1800
```
Installed: vllm-omni `0.25.0rc2.dev131+gd688aa82f`, vLLM 0.25.0, torch 2.11.0+cu130,
Transformers 5.14.1, diffusers 0.38.0. `torch.cuda.get_device_capability()` →
**(12, 1) = sm_121**, so sm_121 was never the Edge blocker. Ready in ~65 s;
`curl localhost:8000/v1/models` → `nvidia/Cosmos3-Edge`, `/health` → 200.
⚠ Transformers 5.14.1 still lacks `cosmos3_edge` (it is in transformers `main`);
Edge support here comes from **vllm-omni**, not Transformers.

**⚠⚠ `num_inference_steps` is MANDATORY — the default silently produces garbage.**
A request with no step count returns a syntactically valid 640×640 PNG that is
**abstract noise**, with no error and no warning. `num_inference_steps: 35` returns a
crisp photoreal image; `guidance_scale: 5.0` alone is NOT enough (smeared output).
This is a silent-cap failure of exactly the class `NFR-07` exists to catch — always
pass the step count, and always LOOK at a generated frame before trusting a corpus.
(PNG byte size is useless as a check: the encoder stores uncompressed, so every
640×640 result is exactly 1,229,899 bytes regardless of content.)

**Footprint: ~9.8 GB GPU, ~2 s/image (640×640).** Far friendlier than Reason-1's
~98 GB at `--gpu-memory-utilization 0.85`, so **Edge can co-reside with Isaac Sim**
on this one GB10. Both default to port 8000 — you cannot run Edge and Reason-1
there simultaneously; Reason-1's rollback image `cu130-nightly-WORKING-sm121` is
preserved and untouched.

**Edge is NOT a drop-in for `perception: cosmos_reason`.** Served this way the log
says `Detected pure diffusion mode (single diffusion stage)` — **one** stage, and it
is diffusion. There is no text/understanding stage, so `/v1/chat/completions` exists
but is a *diffusion* route: it answers with an `image_url` content part, not a
string. `perception/cosmos_reason.py` reads
`body["choices"][0]["message"]["content"]` as text, so it cannot consume this, and
`"modalities": ["text"]` does not help (it errors in the image-return path). Edge's
real surface is `/v1/images/generations`, `/v1/videos`, `/v1/videos/sync`, plus the
action modes (`policy` / `forward_dynamics` / `inverse_dynamics`). Edge also
**rejects video-to-video and transfer V2V** — Cosmos-Transfer-style sim2real stays
off-box. **So Edge belongs behind a future `WorldModel` seam (Predict/action-class
generation), not behind `Perception`; Cosmos Reason-1 remains the perception brain.**
`configs/mission_edge.yaml` was removed because it encoded the disproven assumption
that Edge was a model-string flip on the perception endpoint.
⚠ The cosmos README lists Edge's on-device targets as Jetson AGX Orin / Thor /
RTX PRO 6000 — **DGX Spark is not named** (Blackwell is listed as a supported
architecture generally). An earlier session note claiming Spark was a vendor-tested
Edge platform is unconfirmed; it serves here regardless.

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

**Full mission (Isaac world, on the Spark).** Run from the project root so the
config's relative paths (`configs/layouts/...`, `assets/dem/...`) resolve:
```bash
PYTHONPATH=src "$ISAACSIM_PYTHON_EXE" -m solar_twin.run \
    configs/farm_khavda_block02.yaml configs/mission.yaml \
    --farm-usd assets/khavda_full.usd          # --backend sim_native is the default
```
`$ISAACSIM_PYTHON_EXE` is exported by `~/.bashrc`
(`$HOME/IsaacSim/_build/linux-aarch64/release/python.sh`).

### Watching a run in the Isaac Sim viewport (added 2026-07-28)
Three separate things, deliberately separate flags — a measurement run must never
inherit any of them:

| flag | what it does |
|---|---|
| `--gui` | opens the Isaac Sim window on **this machine's** display (`DISPLAY=:1`, GNOME on seat0) |
| `--livestream` | stays headless but streams the UI over **WebRTC** — the only way to watch from another machine |
| `--live` | **interpolated** motion: the fleet actually flies between waypoints instead of teleporting |
| `--video` | unrelated to the above: renders offscreen and writes `inspection.mp4` |

```bash
# watch locally, fleet actually flying, short demo sweep
DISPLAY=:1 PYTHONPATH=src "$ISAACSIM_PYTHON_EXE" -m solar_twin.run \
    configs/farm_khavda_block02.yaml configs/mission.yaml \
    --farm-usd assets/khavda_full.usd --gui --live --max-panels 12

# watch from your laptop instead
... --livestream --live --max-panels 12
# then point the Isaac Sim WebRTC Streaming Client at this host
```

Notes, each of which was a real trap:
- **`--gui` alone teleports.** Interpolated motion used to be reachable only via
  `--video`, so a GUI run showed robots popping between waypoints. `--live` is
  now the knob; teleport stays the default (a KPI run must not silently take
  ~10x the sim steps).
- **The viewport must be aimed.** Default is the perspective camera, in which the
  fleet is a few pixels of a 320 x 647 m block. `SimRuntime.set_viewport_camera()`
  points it at `/World/Overview`, the same chase camera `--video` uses, and
  `chase()` keeps it on the fleet.
- **Livestream is `headless: True` + `hide_ui: False`**, NOT `headless: False` —
  per this build's `standalone_examples/api/isaacsim.simulation_app/livestream.py`.
  Ports come from `apps/isaacsim.exp.full.streaming.kit`: signal **49100**,
  stream **47998**.
- **A clean zone shows you one robot.** Measured 2026-07-30: a 12-panel run of
  BLOCK-02 escalated **0 of 12** (`--max-panels` takes the first N panels, and at
  `faults.rate: 0.02` R00-C000…C011 are all healthy), so the `CONFIRM` phase never
  ran and the confirm drone never moved. Nothing was broken — the sweep FSM only
  moves drone 2 on a suspect verdict. To watch the full ground-bot-then-drones
  choreography use `mission_mode: scout_dispatch` (below), not a longer sweep.
- **`mission_mode: scout_dispatch`** (added 2026-07-30,
  `orchestrator/scout_dispatch.py`) is the watchable mission, in four beats:
  SCOUT (one drone surveys the zone and flags suspects) → DISPATCH (ground bot
  drives to a flagged panel) → CONVERGE (both drones take station) → INSPECT
  (close pass, `diagnose`, verdict written). `mission_mode: sweep` is the default
  and is the measurement FSM — unchanged, because every recorded KPI came from it.
  ```bash
  tools/run_livestream.sh 24 mission \
      --scenario configs/scenarios/fault_response_demo.yaml
  ```
  ⚠ **Not a measurement.** It pairs with `route: fault_zone`, which centres the
  survey window on a seeded fault *using ground truth*, and it judges a flagged
  panel twice (survey standoff + close standoff). Both break the denominators
  `KPI-01`/`KPI-03` are defined over. Measurement stays `nominal_calm` (SC-01) and
  `khavda_selfshade{,_lowsun}` (SC-11/SC-12).
- **A scenario that overrides `faults.rate` or `turbine_scatter` needs its OWN
  USD.** Both are baked in at build time. `fault_response_demo` raises the whole
  plot's rate from 0.0 to 5e-4, so it builds `assets/fault_response_demo.usd` —
  **measured 2026-07-30: 679,616 panels (340 faulted), 8 turbines, 712,795 prims
  on stage, ~5 min**. Two corrections to what was assumed: the faulted panels cost
  ~33k prims (~97 each, not ~75), and the build is longer than the wide shot's
  documented **176 s** because that figure is for `rate: 0.0` and faulted panels
  take the slow per-prim path. Pointed at `assets/khavda_s05b_full.usd`
  instead, the survey flies a stage with zero faults and flags nothing — which
  looks exactly like a broken escalation path. `tools/run_livestream.sh` derives
  the USD name from the scenario to make this unmissable.
- **`cruise_speeds` was dead in the real path** (fixed 2026-07-30). `run.py` built
  `KinematicControl` without it, so every commute ran at inspection speed and only
  the tests ever exercised cruise. That is a distance ceiling, not just a slow
  transit: one `move_to` reaches `max_ticks * dt * speed`, so at 1.0 m/s and
  dt 0.1 the ground bot could cover **400 m** before the "did not reach" warning
  fired and it snapped to the waypoint — against a ~490 m first commute on
  BLOCK-02, and a 4.84 × 1.97 km whole plot. `bot_cruise`/`drone_cruise`/
  `max_ticks` are now read from `kinematics`.
- **A stale farm USD makes a code change look like a no-op.** `--farm-usd` loads
  whatever is on disk; it does not check that the stage is newer than the code
  that authored it. This bit: the interspersed turbine field was committed while
  every USD on disk predated it, so a run would have shown the old layout and
  "disproved" a working change. `tools/run_livestream.sh` now compares the USD's
  mtime against the farm config *and* `world/`+`schema/` sources, rebuilds when
  stale, and takes `--farm block02|s05b_full|s05b` / `--no-build`.
- **The client is not in the build.** This build ships only the *server*
  (`omni.kit.livestream.webrtc`, "Kit Livestream WebRTC Server"). There is no
  browser client and nothing on port 8211 — a plain `GET :49100` returns 501
  because it is a signalling endpoint. Install the **Isaac Sim WebRTC Streaming
  Client** on the machine you watch from, then Connect to this host's LAN IP
  with signal **49100**.
- **`allowDynamicResize` must be on, or a connected client sees nothing**
  (fixed 2026-07-30). The client negotiates the stream size from *its* window
  (e.g. 1280x720) while our frames come out at `window_width x window_height`
  (1920x1080), and the server then drops every frame: *"Cannot stream video
  frame with resolution 1920x1080 that differs from that of 1280x720 established
  when the client connected"*. `isaacsim.exp.full.streaming.kit` sets
  `primaryStream.allowDynamicResize = true`, but the `SimulationApp` path — and
  NVIDIA's own `livestream.py` example — leaves it false, so `sim_runtime.py`
  sets it explicitly before enabling `omni.kit.livestream.app`.
- **Kit only repaints when `app.update()` is called.** With
  `perception: cosmos_reason` each panel blocks ~12 s inside a `urllib` request
  and the window is frozen for that whole time. For a watchable run use
  `perception: ground_truth`; making a live Cosmos run smooth needs perception on
  a worker thread with the app pumped on the main thread (not done).
- A live viewport gets the chase **camera prim** but **no render product** —
  attaching an annotator nobody reads would render the scene an extra time per
  step.

### Render cost, measured (added 2026-07-28)
Offscreen frame cost on the full Khavda block (`assets/khavda_full.usd`, 75.5k
prims, `RaytracedLighting`), via a 4-pose probe reading `capture_overview()`:

| camera | altitude | mean s/frame |
|---|---|---|
| aerial, whole block in frame | 420 m | 0.71 |
| mid-descent | 140 m | 0.72 |
| at row level, in the array | 6 m | 0.71 |
| low along a torque tube | 2.5 m | 0.70 |

**Flat with altitude, and the same at 960x540 and 1280x720.** So the cost of any
video on this stage is its **frame count** alone. This retires the standing
suspicion that ground-level rendering on the full plant is expensive: 4,900 ticks
of fleet commute looked like a hang because it is 58 minutes of render, not
because row-level frames are dear. Startup is ~23 s.

**A rendered frame is not a finished frame.** Costs of one frame written to an mp4,
measured over whole chapters of a 720p tour:

| stage | s/frame |
|---|---|
| render only (`step` + `capture_overview`) | 0.71 |
| + overlay + streaming encode (the finished mp4) | 0.895 (1,300 frames in 1,164 s; per-chapter 0.878-0.909) |
| fleet chapter (`capture_pair` renders two cameras, plus inset compositing) | 0.904 — inside the ordinary spread, not above it |

Budget off the **end-to-end** figure. `plant_tour.SECONDS_PER_FRAME` is 0.92, not
0.71: projecting from the render alone under-promises by ~25% and overruns the cap
it exists to enforce. Projected 20.6 min against an actual 19.9 on the 85 s tour.

Consequences worth knowing:
- Budget frames, not pixels. `world/plant_tour.py --budget-minutes` projects
  `frames x 0.92 s` and shortens the shots (loudly) to fit.
- **Fixed 2026-07-28:** the overview render product was hardcoded to `(960, 540)`,
  so `flythrough.py --width/--height` silently did nothing and every flythrough
  was 540p regardless of flags. Now `SimRuntime(overview_resolution=...)`,
  defaulted to `(960, 540)` rather than hardcoded. It is deliberately separate
  from `resolution`, which sizes the drone/inspection cameras.
- Buffering frames is the real memory risk, not rendering them: 1,300 frames at
  720p is ~5.5 GB. `RunRecorder(stream_path=...)` encodes incrementally.

## PX4 SITL status — ✅ RUNS on this aarch64 Spark (verified 2026-07-29)

Investigating `FR-06` (real flight dynamics) starts here, because everything else
about it is downstream of "can PX4 run on this box at all".

- **Route: the official multi-arch container.** `docker pull --platform linux/arm64
  px4io/px4-sitl:latest` → 119 MB, `Architecture=arm64`, base Ubuntu 24.04, image
  built 2026-07-08 (≈ v1.18.0-beta1 era). Docker 27.5.1 is already on this box.
- **Verified:** boots to `INFO [simulator_mavlink] Waiting for simulator to accept
  connection on TCP port 4560`, and that port is reachable from the host.
  Reproduce with `python3 tools/px4_sitl_smoke.py` (exit 0 = seam open, tears the
  container down; `--keep` leaves it up for a bridge).
- ⚠ **`PX4_SIM_MODEL=none_iris` matters.** `none_*` selects the **external
  simulator** path — Isaac owns the physics, PX4 owns the control loops, which is
  what the twin needs. Left unset, this image runs **SIH** (PX4 simulating its own
  dynamics), which is the wrong half of the loop and would look like it works.
- **Two routes NOT taken, so nobody retries them blind:**
  - Pegasus's install guide has you build **PX4 v1.14.3 from source** — a 2023
    release, on a 2024 distro, on an architecture its docs never mention.
  - PX4's "pre-built SITL packages" page advertises Ubuntu 24.04 **arm64 `.deb`s**,
    but the tagged GitHub releases carry only a VOXL board package. The docs
    describe `main`, not the releases.
- **Not installed system-wide.** No `apt`, no source build, nothing in Isaac's
  bundled Python — the container is the whole footprint (`docker rmi
  px4io/px4-sitl` removes it). This respects the "do NOT `apt upgrade` this box"
  rule below.
- ⚠ **The Isaac-side bridge does not exist yet.** PX4 running is necessary, not
  sufficient: Pegasus v5.1.0 does not support Isaac 6.x and needs a bounded port
  (`RISK-02`(b)), and its MAVLink backend was written for PX4 v1.14.3 against this
  container's much newer PX4 (`RISK-26`). Do not read "PX4 runs" as "we have
  flight dynamics".

## Pegasus Simulator — ported to Isaac 6.0.1 (2026-07-29)

Installed with `bash tools/install_pegasus_isaac6.sh` (idempotent). Layout mirrors
Isaac itself: an external pinned clone at `/home/simulationhub/PegasusSimulator`
(v5.1.0, commit `644da37`), plus one reviewable patch in-repo at
`tools/patches/pegasus-v5.1.0-isaac6.patch`. Pegasus is ~240 MB of BSD-3-Clause
third-party code, so it is deliberately NOT vendored.

- **⚠ One package added to Isaac's bundled Python** (this is the note
  `CLAUDE.md` requires): `pymavlink 2.4.49`, aarch64 wheel, installed
  `--no-deps`. It is the only Pegasus dependency missing — numpy 2.5.1, scipy
  1.17.0 and pyyaml are already in the 6.0.1 bundle. `--no-deps` is deliberate:
  a transitive numpy upgrade already broke scipy on this box once (Session 10d).
  **Verified after install: numpy and scipy were unchanged.**
- **⚠ Do NOT run `ISAACSIM_PYTHON -m pip install --editable pegasus.simulator`**,
  which is what Pegasus's own install guide tells you to do. Its `setup.py`
  carries a `PatchIsaacSimKitApp` hook that **rewrites Isaac's `.kit` app files**
  to inject a replicator extension — i.e. it mutates our source-built Isaac
  install as a side effect of a pip command. Use PYTHONPATH / `--ext-folder`
  instead; the installer never pip-installs Pegasus itself.
- **What the patch changes:** `omni.isaac.dynamic_control` was retired in the
  4.5/5.0 API migration and is absent from this build, so Pegasus's
  `Vehicle`/`Multirotor` could not even be constructed. All of its legacy calls
  funnel through one accessor, so the patch adds `dc_compat.py` (the same ten
  methods on `isaacsim.core.prims`) and touches only two imports plus that
  accessor — upstream call sites stay byte-identical so future merges stay clean.

**Two bootstrap traps, both measured, both silent:**

1. A standalone app must give Pegasus's singleton the World **before**
   constructing any vehicle, or `Vehicle.__init__` dies on `self._world.stage`:
   `pg = PegasusInterface(); pg._world = World(**pg._world_settings)`.
2. **`world.play()` before stepping.** Without it there is no physics simulation
   view, and every prim read returns the **static USD pose** — no exception, no
   warning that matters. It looks exactly like working code with a frozen drone.

**Status — what is and is not proven.** Imports: ✅ all vehicle/backend modules.
Physics through the shim: ✅ reads tracked a falling Iris exactly (matched a
direct `SingleRigidPrim` read to 4 dp), and `update_state` writes correct state
when invoked. **A PX4-governed hover is NOT yet demonstrated** — see `RISK-28`.

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

## Looks: what this build has for materials, lighting and CAD (verified 2026-07-31)

Established while fixing the "it looks nothing like real" problem. Every line here
was measured or listed on this box, not read from docs.

### MDL materials — available, and by bare name

The build ships **zero `.mdl` files** (`find / -name '*.mdl'` → none outside test
fixtures) and configures no MDL search path in any `.kit`. That is why the project
never moved off `UsdPreviewSurface`. But a render probe
(`tools/material_probe.py`, `mdl_probe`) settled it:

| binding | result |
|---|---|
| `OmniPBR.mdl` / `OmniGlass.mdl` by **bare name** | ✅ resolve and render — built into the RTX renderer, **no download, no network** |
| library MDL over **https** (`.../Materials/Base/Metals/Aluminum_Anodized.mdl`) | ✅ resolves and renders (adds a network dependency at render time) |
| `UsdPreviewSurface` | renders, but RTX only *translates* it — this is why the textured path's diffuse input was "ignored outright" |

The contract is `SetSourceAsset(file, "mdl")` + `SetSourceAssetSubIdentifier(fn, "mdl")`
+ the material's **`mdl`** surface output. Get any one wrong and you get a silent
default grey, indistinguishable from "the material is just flat".

⚠ Honest scope: for rough dielectrics (sand, concrete, asphalt) MDL and
`UsdPreviewSurface` render nearly identically — both are Lambert+GGX. The real wins
are **glass** (measured 111 vs 67 on a sphere) and texture inputs that work.

Asset root that resolves: `https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/6.0/Isaac/`
(`SimReady/ Environments/ Props/ Materials/ Robots/ People/ Sensors/ Samples/`).
`Materials/vMaterials_2/` has Metal, Stone, Masonry, Paint, Plastic, Ceramic, Wood —
**no ground/sand/gravel**, so the desert floor stays OmniPBR + our own maps.
**There is no PV module, tracker or inverter asset in any NVIDIA library.**

### Exposure — only the photographic triad works

| key | effect |
|---|---|
| `/rtx/post/tonemap/fNumber`, `cameraShutter`, `filmIso` | ✅ strong |
| `exposure/compensation`, `exposureKey`, `maxWhiteLuminance`, `whiteScale` | ❌ **inert** (<0.3/255 across their range) |

The active tonemap `op` is **6**, which ignores the Reinhard parameters. Measured
ground brightness on block02 at a 43° sun: f/5 → 195 (blown white), f/7 → 153,
**f/9 → 118**, f/11 → 91, f/16 → 51.

⚠⚠ **Apply these AFTER `open_stage`.** Set in `SimulationApp` construction they are
accepted by carb and then reset when the renderer re-initialises for the new stage —
measured as identical frame means (182.2 vs 182.3) with the tonemap "applied".

### The sky is the remaining defect

At the shipped `DomeLight` 300 / `DistantLight` 2400 the sky renders **0.16× the
ground** — six times darker than the desert it supposedly lights, which is the dark
brown band at the top of every render. Measured at f/9:

| dome | sun | sky | ground | sky/ground |
|---|---|---|---|---|
| 300 | 2400 | 15.5 | 100.1 | **0.16** (shipped) |
| 3000 | 2400 | 109.1 | 131.0 | 0.83 |
| 6000 | 1200 | 156.8 | 135.8 | 1.15 (realistic) |

Raising the dome fixes the sky and floods the ground with blue skylight, attacking
the Session-10c warm-ground invariant. **Not tunable:** a `DomeLight`'s texture is
both background and fill, and ours is an 8-bit LDR PNG where a real HDRI spans
~1e3–1e5. The fix is an HDRI or NVIDIA's dynamic sky —
`Assets/Skies/2022_1/Skies/Dynamic/ClearSky.usd` returns **HTTP 200**, and
`omni.kit.environment.core-1.4.2` (with `sunstudy_player/`) plus
`omni.usd.schema.physical_lighting-0.1.0` are **already installed**. ⚠ `SkyHelper`'s
API is unverified against 6.0.1.

### CAD → USD — installed and working

`tools/cad_to_usd.py`, verified end to end on this box (STL → USD).

    omni.kit.converter.cad 209.4.0    bundle
    omni.kit.converter.hoops 510.3.0  step stp iges igs sldprt sldasm catpart
                                      catproduct prt asm x_t x_b jt dwg dxf 3dm
                                      ipt iam dgn obj stl glb gltf fbx
    omni.kit.converter.dgn 510.1.5    MicroStation
    omni.kit.converter.jt 509.1.2     Siemens JT
    omni.kit.asset_converter 6.0.1    OBJ/STL/glTF/FBX
    omni.importer.onshape 2.0.3
    omni.kit.converter.gsplat 0.1.14  Gaussian splats (the NuRec path)

API: enable `omni.kit.converter.{common,hoops_core,hoops}`, then
`hoops_core.get_instance().create_converter_task(src, dst, config_path_to_args(json))`.

⚠ **`.dwg`/`.dxf` is the important one** — the Khavda vendor drawing is already in
that format (we currently parse it for coordinates and discard the geometry), and it
is what PVcase and RatedPower export.

⚠ A raw import is **not** SimReady. The STL test came back `metersPerUnit=0.001`
against our metres/Z-up convention; expect to rescale, decimate and re-bind
materials. The tool prints prim count, units and up-axis so those show up
immediately rather than after the asset is instanced 30,016 times.

## Physics on a farm stage — measured 2026-07-31 (`tools/physics_probe.py`)

The inspection mission has never stepped physics: `SimRuntime.step()` spins rotors,
turns turbine hubs and calls `app.update()` — no `SimulationContext`, no `World`, no
`play()`. And `tools/px4_hover.py`, the one place real dynamics run, flies in
`world.scene.add_default_ground_plane()` — an **empty world**. So the twin's own
terrain had never been stood on. Two findings, both blocking, both now measured.

### 1. There was no floor — only 25 colliders in 81,961 prims

| | |
|---|---|
| prims (full block, realism) | 81,961 |
| `CollisionAPI` | **25 — all on turbines** |
| `RigidBodyAPI` | **0** |
| `/World/Ground` collision | **none** |

A 6.47 kg body dropped from 12 m over the array fell **44.66 m** against a 44.15 m
free-fall prediction — it hit nothing and kept going to z = −32 m. Panels, racking,
roads and fence have no colliders either.

**Fixed for the ground:** `_build_ground_heightfield` now applies `CollisionAPI` +
`MeshCollisionAPI` with `approximation = meshSimplification` (a `convexHull` over a
320 × 647 m heightfield is a blob that would put the drone metres off the grade).
Verified by drop test: 12.0 m → **rests at z = 0.073 m**.

⚠ Still absent: colliders on panels, racking, roads, fence. A drone can fly through
a module. And a trimesh collider is static-only and can be tunnelled at speed — see
the Isaac trimesh fall-through issue; this is verified by drop test, not assumed.

### 2. Physics does not scale to the full plant

| stage | prims | steps/s | realtime |
|---|---|---|---|
| block02 subset, 24 tables (flat) | 7,053 | 217 | **1.09×** |
| block02 subset, 24 tables (realism) | 7,614 | 200–211 | **1.00–1.06×** |
| **block02 full** | **81,961** | **14** | **0.07×** |

At `physics_dt = 1/200` and render off. The realism layer costs ~8% — **it is not the
problem**; plant scale is. ~11× the prims gave ~14× the slowdown.

**Consequence for PX4 in the mission:** feasible on a **subset** (≈8k prims, ~24
tables) at ~1× realtime; **not feasible on the full 30,016-module block**, because a
flight controller runs on wall-clock and at 0.07× the sim and PX4 disagree about time.

### 3. The cost is scene-graph sync, NOT collision — confirmed

Same stage, same body, branches progressively deactivated:

| configuration | live prims | steps/s | realtime |
|---|---|---|---|
| all active (baseline) | 81,974 | 13 | 0.07× |
| deactivate `/World/Farm` | 7,270 | **288** | **1.44×** |
| + `/World/Site` | 236 | 966 | 4.83× |
| + `/World/OSM` | 96 | 1,077 | 5.38× |
| + `/World/Turbines` | 55 | 1,241 | 6.20× |
| all reactivated | 81,974 | 11 | 0.05× |

**`/World/Farm` alone costs 22×, and it carries ZERO colliders.** PhysX is colliding
against 25 turbine prims plus one ground mesh; the 30,016 physically-inert panel
Xforms are what the time goes to. Reactivating restores the baseline, so the effect is
causal and not a warming cache.

⚠ **Fabric does not fix it — measured, not assumed.** Enabling `omni.physx.fabric` at
runtime (reports `True`), setting `/physics/updateToUsd = False`, and
`omnihydra.useFastSceneDelegate` at launch each left the rate at **13–14 steps/s**.
Untested: launching the dedicated `isaac-sim.fabric.sh` app, where those are set
before app init rather than after.

So full-plant physics is **not currently available**, and the practical answer is to
run physics on a subset (~24 tables, ~8k prims, ~1× realtime) while rendering and
KPI-scoring on the full block, which needs no physics. Panel-level instancing is
already in place (29,416 modules from 1 prototype) — the residual cost is the
per-panel Xform each module needs to carry its `pv:` state.

## Isaac ROS on this Spark — ✅ GO, but containers only (verified 2026-08-03)

The feasibility check that gates the localization/navigation arc (cuVSLAM → nvblox →
Nav2). Verdict: **available and supported**, with one route and two real constraints.

### Platform check — every requirement met

| requirement | needed | this box |
|---|---|---|
| platform | DGX Spark is **in the official test matrix** | DGX Spark (GB10) ✅ |
| ROS 2 | Jazzy | Jazzy, sourceable ✅ |
| CUDA | 13.0+ | 13.0.88 ✅ |
| driver | 580+ | 580.142 ✅ |
| Docker + NVIDIA container toolkit | required | 27.5.1, `nvidia` runtime present ✅ |
| `nvcr.io` access | required | authenticated, verified against a public image ✅ |

### ⚠ The apt debs are x86_64-ONLY — the container route is mandatory

Repo `deb https://isaac.download.nvidia.com/isaac-ros/release-4.5 noble main` is live
and advertises `Architectures: amd64 arm64`. That advertisement is misleading:

| | amd64 | arm64 |
|---|---|---|
| total packages | **392** | **50** |
| `isaac-ros-visual-slam` | 3 | **0** |
| `isaac-ros-nvblox` | 13 | **0** |
| `isaac-ros-nitros` | 33 | **0** |

Every one of the 50 arm64 entries is a `python3-*-pip-shim` or a dependency, plus
`isaac-ros-cli` — which is `Architecture: all`, i.e. a pure-Python launcher, not a
build. **There is no compiled Isaac ROS package for aarch64 in apt.**

That is not a blocker, it is the wrong route. NVIDIA's documented path for Spark is
the **Isaac ROS CLI driving arm64v8 Docker containers**, and the images are real:

    nvcr.io/nvidia/isaac/ros    267 tags, 31 aarch64

Verified to resolve (manifest inspected, not guessed):

    noble-ros2_jazzy_<hash>-arm64            18.0 GB compressed, 91 layers
    isaac_ros_<hash>-arm64-fastos             0.5 GB compressed, 25 layers

Tag suffixes distinguish the aarch64 targets: **`-fastos`** (DGX OS / Spark) vs
**`-jetpack`** (Jetson).

### ⚠ Two constraints that change the plan

1. **cuVSLAM needs STEREO, and our drone is monocular.** `isaac_ros_visual_slam`
   requires "one or more stereo cameras and optionally an IMU" (RGBD also supported;
   **monocular is not**), at ≥30 Hz with ≤±100 µs stereo sync and ≤±2 ms jitter.
   `SimRuntime` authors ONE RGB camera per drone. Publishing a synchronised stereo
   pair out of Isaac is a prerequisite task that was not in the original estimate.
2. **Known DGX Spark issue:** `rosdep install` fails with package-downgrade errors on
   bare-metal/venv workflows, because DGX OS ships newer packages than Isaac ROS pins.
   Another reason to stay in the container.

### Not yet done

The 18 GB image has **not** been pulled — that is a large download and a system
change, so it needs a decision rather than an assumption. Nothing above required it:
every line here is from repo metadata and registry manifests.
