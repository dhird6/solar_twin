# 08 — Platform Constraints & Risk Register

## Platform capability matrix (DGX Spark: GB10, sm_121, CUDA ≥13, aarch64)

| Capability | Spark status | Confidence | Tracked as |
|---|---|---|---|
| Isaac Sim rendering (procedural + splat) | Runs on Spark | Version to confirm | `RISK-01` |
| Isaac Lab RL/IL training (small runs) | Runs on Spark; large-scale training bursts out | Version to confirm | `RISK-03` |
| Pegasus Simulator (PX4 SITL) | Unproven on aarch64 | Untested | `RISK-02` |
| PhysX force fields (wind/drag/noise) | Should run (standard PhysX extension) | API/schema vs. installed build unconfirmed | `RISK-01`, `RISK-13` |
| Sensor RTX / `ovrtx` | aarch64 wheel exists; runtime on sm_121 unconfirmed | Plausible | `RISK-05` |
| Cosmos Reason (2B/8B) inference | Runs via mainline vLLM (NIM crashes on sm_121) | **Verified live on this box** per `mission.yaml` comments | `RISK-04`, `RISK-09` |
| Cosmos Transfer (generation) | **Confirmed unsupported** | Non-negotiable, off-box | — (design constraint, not open) |
| Cosmos Predict (2B) | Blackwell+ARM inference support claimed (v1.3.3) | Unverified on this Spark | `RISK-04` |
| 3dgrut (NuRec/3DGUT reconstruction) | CUDA 13.0 "experimental"; arm64 Docker build exists | Untested on sm_121 | `RISK-06` |
| cuOpt (routing) | Apache-2.0, GPU solver | aarch64/GB10 build unverified | `RISK-10` |
| Isaac ROS (cuVSLAM, nvblox, Perceptor, NITROS) | **Supported since 4.2.0** (nvblox explicitly lists DGX Spark) | Confirmed for prototyping | — |
| Mission Dispatch / Mission Database | **Not yet Spark-supported** | Confirmed gap | `RISK-08` |
| ROS 2 sensor image topics (sim-side) | Reported rendering quirks | Why sim-native is default | `RISK-21` |

## Risk register

Each entry: description, what it blocks, the action that resolves it, and
current status. "Blocks" cites the `FR`/`SLICE`/`HAZ`/`NFR` it gates.

| ID | Description | Blocks | Verification action | Status |
|---|---|---|---|---|
| `RISK-01` | Isaac Sim version tension: `CLAUDE.md` pins 5.1 (source build); the research brief assumes 6.0.1 (GA'd 2026-06-04). NuRec render path, Sensor RTX/`ovrtx` API, and Pegasus (built for 5.1) compatibility all differ across this boundary. | All of `PIL-1`, `PIL-2`; every slice from `SLICE-2` on | Confirm the installed build on the actual Spark; reconcile `CLAUDE.md` in the same commit if it's wrong (`NFR-10`) | Open |
| `RISK-02` | Pegasus Simulator v5.1.0 was validated on x86_64 + driver 550.163.01; aarch64/GB10 (sm_121, CUDA 13) is unproven. PX4 SITL itself is CPU-side, but Isaac build compatibility and Warp-side kernels need a smoke test. | `FR-06`–`FR-09`, `SLICE-2` | Smoke-test Pegasus on this Spark before committing further `SLICE-2` design | Open — **gating risk for `SLICE-2`** |
| `RISK-03` | Isaac Lab version: `CLAUDE.md` says "Isaac Lab 3.0"; the current stable release is 2.3.0 (built on Isaac Sim 5.1), while 3.0.0-beta exists as a beta (~July 2026). | `PIL-4`, `SLICE-5` | Confirm the actual installed version against `github.com/isaac-sim/IsaacLab/releases` | Open |
| `RISK-04` | Cosmos generation/reasoning footprint on GB10: Cosmos Transfer is confirmed unsupported; the Reason NIM crashes on sm_121 (served via vLLM as the workaround — verified live per `mission.yaml`). Unconfirmed: whether Predict2.5-2B, a distilled Transfer variant, or Cosmos 3 Edge/Nano can run locally, and whether a newer Reason2/Cosmos 3 NIM now supports sm_121 (superseding the vLLM workaround). | `FR-02`, `FR-18`, `PIL-3`, `PIL-5`, `NFR-05` | Test each candidate model/variant on GB10 before assuming strictly off-box; re-check NIM sm_121 support periodically | Open (vLLM Reason2 path partially resolved; generation confirmed off-box) |
| `RISK-05` | Sensor RTX / `ovrtx` ships an aarch64 wheel (`manylinux_2_35_aarch64`), but the API was early-access in 2025 ("Omniverse Cloud Sensor RTX APIs"). Runtime behavior on sm_121/GB10 is unconfirmed. | `FR-11` (blade shadow rendering), `HAZ-07`, `SLICE-3` | Confirm the current `ovrtx` release actually runs on sm_121/GB10 before relying on it for the false-fault harness | Open |
| `RISK-06` | 3dgrut's supported CUDA versions include 13.0 flagged **experimental**; an arm64 Docker build is published but untested on sm_121/GB10. | `FR-19`, `PIL-1`, `SLICE-6` entry criterion | Verify a working CUDA-13/aarch64/sm_121 3dgrut build on the Spark; if it fails, formally designate reconstruction as burst-out (this is the `SLICE-6` decision gate) | Open |
| `RISK-07` | 3dgrut's custom NuRec USDZ export is slated for deprecation, to be replaced by "ParticleField." Scripting the reconstruction pipeline against the wrong export format wastes the work. | `FR-19`, `SLICE-6` | Confirm the current target export format immediately before scripting the capture→COLMAP→3DGUT pipeline, not at design time | Open |
| `RISK-08` | Isaac ROS supports the Spark since 4.2.0, but **Mission Dispatch / Mission Database containers are not yet Spark-supported**. This directly affects prototyping the fleet-command loop on-box. | `FR-23`, `PIL-6`, `SLICE-7`/`SLICE-8` | Plan the fleet-command loop assuming off-box or a supported Jetson until Spark support lands; re-check Isaac ROS release notes each cadence | Open — **gating risk for `SLICE-7`** |
| `RISK-09` | Cosmos Reason inference latency/throughput on GB10 via vLLM is unbenchmarked at actual inspection frame rates (screen + confirm passes per panel). | `NFR-06`, `KPI-02`, `KPI-06` | Benchmark end-to-end latency per panel under the vLLM path; feed the number into coverage/time-window KPI budgets | Open |
| `RISK-10` | cuOpt's aarch64/GB10 build is unverified; additionally, `orchestrator/routing.py` (`IF-05`) assumes the cuOpt Python client has no Isaac/GPU-driver dependency that would violate the Isaac-free boundary (`NFR-01`). | `FR-22`, `IF-05`, `SLICE-7` | Verify the cuOpt build on this Spark and confirm the client package imports cleanly without Isaac before writing `orchestrator/routing.py` | Open |
| `RISK-11` | Articulated-turbine collider cooking (mesh/convex colliders on tower/nacelle/blades) has not been verified against the installed Isaac build. | `FR-11`, `HAZ-01`, `SLICE-2` | Build one articulated turbine and confirm collision fires correctly before relying on it for the keep-out design | Open |
| `RISK-12` | No CFD reference data exists to validate the parametric wake model's turbulence-intensity parameters against a real turbine — the wake model is a knowing approximation, not a validated one. | `FR-13`, `HAZ-02` | Best-effort: compare qualitative behavior (near-wake intensity growth, downstream decay) against any published reference turbine wake data; document as an approximation regardless | Open (accepted as a permanent fidelity trade-off, not fully resolvable on this platform) |
| `RISK-13` | Documented PhysX forum issue: applying a force field to an articulation link throws `"PxArticulationLink - Articulation Link must be in a Scene"`. The drone is an articulation, so wind/wake force fields may hit this directly. | `FR-12`, `FR-13`, `SLICE-2` | Reproduce or avoid on the installed build; confirm the correct attach-order/schema before shipping `SLICE-2` | Open |
| `RISK-14` | Trimesh terrain has a documented PhysX mesh-cooking/fall-through failure mode (Isaac Lab issue #2323, now closed upstream, but not validated on our own DEM). | `FR-10`, `HAZ-04`, `SLICE-2`/`SLICE-6` | Validate contact behavior on our specific procedural terrain and, later, the real DEM — don't assume the upstream fix generalizes | Open |
| `RISK-15` | Bird trajectory realism is an open design choice: procedural/scripted trajectories vs. a recorded or learned motion set. Affects how meaningful `HAZ-05` testing actually is. | `FR-14`, `SLICE-5` | Decide at `SLICE-5` kickoff based on available data; document the choice as a fidelity trade-off either way (`NFR-07`) | Open |
| `RISK-16` | Whether Cosmos Transfer preserves geometrically-correct blade-shadow motion or hallucinates it is unconfirmed — this is the single highest-consequence unknown for `NFR-08`/`FR-05`, since a hallucinated shadow would poison exactly the fault ground truth the project protects. | `FR-05`, `NFR-08`, `HAZ-07`, `SLICE-4` | Spot-check generated shadow geometry/motion against the known turbine articulation state before trusting any generated corpus for training or evaluation | Open — **high priority** |
| `RISK-17` | SIL→HIL KPI-parity tolerance for `SLICE-8` is undefined — there is currently no number for "how close is close enough" between a twin run and a real-robot run on the same scenario. | `FR-24`, `SLICE-8` | Define the tolerance with input from whoever owns acceptance for real flight, before `SLICE-8` exit criteria are enforced | Open |
| `RISK-18` | ROS 2 distro target (Humble vs. Jazzy) is inconsistent across Isaac ROS package docs for the specific packages we'd deploy. | `FR-23`, `PIL-6` | Confirm the exact distro per package before wiring `ros2_bridge.py`'s dependencies | Open |
| `RISK-19` | The canonical fleet-command repo is unsettled among `isaac_ros_mission_client` / `isaac_mission_dispatch` / `isaac_mission_control` / `isaac_ros_cloud_control` — the lineage is genuinely in flux. | `FR-23`, `SLICE-7`/`SLICE-8` | Confirm the canonical, currently-maintained repo immediately before integration work starts, not at design time | Open |
| `RISK-20` | cuVSLAM robustness over repetitive panel rows (visual aliasing) and in GPS-degraded, wind-buffeted flight is unverified for this specific site geometry. | `FR-24`, `SLICE-8` | Test cuVSLAM against a recorded or simulated repetitive-row traverse before trusting it as the on-robot pose source | Open |
| `RISK-21` | Reported ROS 2 sensor-rendering quirks on the Spark are the stated reason sim-native `Transport` is the default. Sim-side ROS 2 image topics should not be trusted without verification. | `NFR-05`, `SLICE-8` | Re-verify before any `SLICE-8` work depends on sim-side ROS 2 image publishing | Open |
| `RISK-22` | Jetson AGX Thor module (T5000) pricing (~$2,999/1k units reported) and exact SKU for the robot BOM are unverified secondhand figures. | Deploy BOM planning only — not a technical blocker | Confirm current pricing/SKU with NVIDIA before BOM commitment | Open (low priority, non-technical) |

## Register hygiene

- When a `RISK-nn` resolves, update its **Status** in place (`Resolved
  YYYY-MM-DD — <what was confirmed>`); do not delete the row — it's the audit
  trail for why a design decision was made the way it was.
- When a new ⚠-verify item surfaces during implementation, add it here with
  the next sequential ID and link it from whichever requirement/hazard/slice
  it blocks — don't let open questions live only in code comments or PR
  descriptions.
- `RISK-02`, `RISK-06`, and `RISK-08` are called out as **gating risks** in
  `07-roadmap-and-milestones.md` because their slice cannot start design work
  in earnest until they're resolved one way or the other — everything else is
  a parallel-track verification.
