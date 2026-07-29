# 06 — Scenario Suite & KPIs

This is the acceptance-testing spec: how "good enough to fly" is measured, in
numbers, from a reproducible config — never a GUI demo (`FR-17`, `NFR-02`).

## KPI definitions

| ID | Name | Formula | Gated by | Source today |
|---|---|---|---|---|
| `KPI-01` | Detection rate | fraction of panels where `detected_state == injected_state` | Every slice ≥ `SLICE-0` | **Already implemented**: `MissionResult.detection_rate` in `orchestrator/mission.py` |
| `KPI-02` | Coverage % | panels inspected / panels in scope, within the mission time budget | `SLICE-7` (fleet) | New — count from `MissionResult.panels_inspected` vs. `farm.yaml` grid size |
| `KPI-03` | False-fault rate | fraction of **healthy** panels whose `detected_state != healthy` under an adversarial (shadow/blur/dust) scenario | `SLICE-3` (the central thesis metric — see `HAZ-07`) | New — filter `MissionResult.results` where `injected_state == healthy` and `not correct`. **Baseline harness demonstrated 2026-07-24**: live Cosmos Reason over a healthy panel under sweeping turbine-blade shadows returned 0/6 false faults (moderate shadow, not worst-case) — the `SC-05` harness starting point |
| `KPI-04` | Collision-free-flight rate | fraction of scenario runs with zero collisions and zero keep-out-volume intrusions | `SLICE-2` | **Partial** — keep-out intrusions already captured in the run-record `keepout` block (`waypoints_clamped`, `min_clearance_m`) via `SafeControl` (`IF-07`); physics-contact events pending the articulation/Pegasus work |
| `KPI-05` | Station-keep error | max/mean deviation (meters) from the commanded hold pose during a screen/confirm pass under wind/wake | `SLICE-2` | New — from `Transport.pose()` samples during the hold window |
| `KPI-06` | Battery/time-window adherence | fraction of missions completed without breaching the declared battery reserve floor or daylight/time window | `SLICE-7` | New — requires `IF-01` (`EnergyAware`) |
| `KPI-07` | Terrain traversal pass/fail | ground bot completes the ramp testbed at the declared max grade without loss of contact/stall | `SLICE-2`/`SLICE-6` | New — pass/fail per grade angle |
| `KPI-08` | Generated-frame validity rate | fraction of Cosmos Transfer/Predict output frames that pass the Evaluator filter | `SLICE-4` | New — from the Data Factory Blueprint's Evaluator stage (`NFR-08`) |

**Note on `KPI-01` vs `KPI-03`:** these are deliberately distinct. `KPI-01` is
overall accuracy across all injected states (including real faults); `KPI-03`
isolates the specific "swept blade shadow → false hotspot" failure mode this
project exists to prevent. A system can have decent `KPI-01` and still be
unsafe to deploy if `KPI-03` is high on adversarial scenarios — report both,
always.

## Scenario suite

Each scenario is a `configs/scenarios/<name>.yaml` per `04-interfaces-and-data.md`
`IF-03`, composing `farm.yaml` + `mission.yaml`, seeded, with declared
`kpi_gates`. The suite starts small and grows with each slice — this is the
starting set, not the final one.

| ID | Name | Composition | Hazards exercised | Primary KPIs | Introduced |
|---|---|---|---|---|---|
| `SC-01` | `nominal_calm` | Slice-0 farm, no wind/turbine/birds | none | `KPI-01`, `KPI-02` | `SLICE-0` |
| `SC-02` | `gust_only` | + wind force field, no turbine | `HAZ-03` | `KPI-05` | `SLICE-2` |
| `SC-03` | `turbine_static_keepout` | + one articulated (non-spinning) turbine | `HAZ-01` | `KPI-04` | `SLICE-2` |
| `SC-04` | `turbine_wake` | + spinning turbine, wake field active | `HAZ-01`, `HAZ-02` | `KPI-04`, `KPI-05` | `SLICE-2` |
| `SC-05` | `sweeping_shadow` | + low sun angle, blade shadow crosses panel row, no wind | `HAZ-07` | `KPI-03` | `SLICE-3` |
| `SC-06` | `shadow_plus_gust` | `SC-04` + `SC-05` combined (motion blur likely) | `HAZ-02`, `HAZ-03`, `HAZ-07` | `KPI-03`, `KPI-05` | `SLICE-3` |
| `SC-07` | `bird_crossing` | + scripted bird trajectories through the drone lane | `HAZ-05`, `HAZ-07` | `KPI-04`, `KPI-03` | `SLICE-5` |
| `SC-08` | `graded_terrain` | ground bot on 5/10/15/20° ramp testbed | `HAZ-04` | `KPI-07` | `SLICE-2`/`SLICE-6` |
| `SC-09` | `dust_haze_variant_pack` | off-box Transfer/Predict-generated corpus over `SC-05`/`SC-06` | `HAZ-07` | `KPI-03`, `KPI-08` | `SLICE-4` |
| `SC-10` | `full_farm_battery_window` | full farm, both robots, N panels, declared daylight/battery window | `HAZ-06` | `KPI-02`, `KPI-06` | `SLICE-7` |
| `SC-11` | `khavda_selfshade` | real Khavda BLOCK-02, HSAT trackers pinned at their 60° stop, sun 17.2° (02:00Z), every panel healthy, no turbines | `HAZ-07` | `KPI-03` | `SLICE-3` — **built**, `configs/scenarios/khavda_selfshade.yaml` |
| `SC-12` | `khavda_selfshade_lowsun` | `SC-11` one hour earlier (01:30Z, sun 10.7°): ~54% of each module shaded *and* the whole scene dimmer, so shading is confounded with underexposure | `HAZ-07` | `KPI-03` | `SLICE-3` — **built**, `configs/scenarios/khavda_selfshade_lowsun.yaml` |

`SC-11`/`SC-12` supersede `SC-05`'s original stimulus rather than extending it:
the turbine-blade shadow sailed over the elevated rows onto the ground, while
tracker self-shading is a real, on-surface, many-panel shadow produced by the
plant's own hardware. Both are asserted geometrically in `tests/test_solar.py`
before any run — a KPI-03 of 0.00 means nothing if the stimulus was absent.

## Gating discipline

- Every scenario config declares its own `kpi_gates` block (see `IF-03`
  example); a slice's exit criteria (`07-roadmap-and-milestones.md`) is
  "all scenarios introduced at or before this slice meet their gates."
- **Gates are enforced, not decorative** (`FR-17`). `run.py` evaluates the
  declared block against the measured metrics via `kpi/gates.py`, writes
  `gates.json`, prints the verdict and exits non-zero on a breach. A gate naming
  a metric the run never measured **does not pass** — it reports `unmeasured`
  and fails, because a silently unevaluated bound reads as a green tick.
- **Quote a spread, not a number.** The world is seeded; the VLM is only
  reproducible when served serially (measured — see `08-platform-and-risk-register.md`
  `RISK-23`). `run.py --repeat N` re-runs one scenario N times, rewinding panel
  state between repeats, and writes `variance.json`: per-metric min/median/max
  plus every panel the repeats disagreed about, each attributed to the
  **renderer** or the **model** by comparing frame thumbnails with a measured
  tolerance — bit-exact digests cannot do this job, because the renderer is
  stochastic (`RISK-24`). Repeat sets are gated on the
  **worst** run, never the mean — a fleet flies each sortie once.
- A KPI regression on an **earlier**-slice scenario blocks merging
  **later**-slice work until fixed or explicitly waived with a tracked
  `RISK-nn` entry — fidelity deepens along a working loop, it should never
  silently break an earlier one (`01-scope-and-vision.md`, "thin
  end-to-end thread").
- `KPI-08` (generated-frame validity) is the data-integrity gate protecting
  `NFR-08`: a corpus that fails this gate must not be used to evaluate or
  fine-tune `Perception`, full stop — no averaging it away.

## What this suite deliberately does not attempt

- It does not attempt statistical bird-strike realism (`HAZ-05` residual
  risk) or true wake CFD validation (`HAZ-02`) — those require off-box
  reference data not yet available; scenarios exercise the *behavior*, not a
  certified physical envelope, per the non-goals in `01-scope-and-vision.md`.
- It is not a substitute for SIL→HIL validation before real flight
  (`FR-24`); it is the sim-side half of that gate.
