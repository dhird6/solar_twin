# Cosmos Transfer — the off-box scenario factory (plan)

> **Status:** design doc · **Written:** 2026-07-30 · **Not executed.**
>
> The seed → variant → evaluate pipeline for multiplying one seeded farm render
> into the dust / haze / low-sun / blade-shadow long tail that hardens
> `Perception`. Satisfies `FR-05` / `NFR-08` (the data-integrity gate) and sits
> under `NFR-05` (the on-box/off-box split).
>
> **Nothing here has been run.** The gating reason is in §1: this box has no
> usable off-box compute. Transfer was **not** attempted on-box — `NFR-05` locks
> that, and Cosmos Transfer is confirmed unsupported on sm_121/GB10.

---

## 1. Compute availability — the blocker (measured 2026-07-30)

**Verdict: no off-box compute is reachable from this box.** Checked, rather than
assumed:

| Path | State | Evidence |
|---|---|---|
| **AWS** | ✗ **Expired** | Profile `cctech-simulationhub` (`us-west-2`) exists with a populated key, but `sts get-caller-identity` returns `ExpiredToken`. These are temporary STS credentials, not long-lived keys. |
| **GCP / Azure** | ✗ Absent | No `gcloud`, no `az`, no `~/.config/gcloud`, no `~/.azure`. |
| **Remote hosts (RTX PRO 6000 / DGX)** | ✗ None configured | No `~/.ssh/config`; `known_hosts` holds 6 entries and no host is set up as a compute target. |
| **Kubernetes / Run:ai** | ✗ Absent | No `kubectl`, no `runai`. |
| **OSMO** | ✗ Absent | No `osmo` CLI, no `~/.osmo`. |
| **NGC** | ⚠ **Key present, CLI broken** | `~/.ngc/config` holds an API key with `org = nvidia`. But `~/.local/bin/ngc` is `exec ~/.local/ngc-cli/ngc` **with no `"$@"`** — it drops every argument, so *all* subcommands (including `ngc --version`) return `ERROR: Incomplete command received`. Untested whether the key is live or whether the org carries NVCF entitlement. |
| **Docker** | ✓ Present | But the only GPU here is the GB10 — exactly where Transfer is unsupported. Local Docker is not an off-box path. |

⚠ **The NGC wrapper is a one-line fix** (`exec ~/.local/ngc-cli/ngc "$@"`) and I
was blocked from applying it by the sandbox. It is worth fixing regardless of
this plan: without it there is no registry access for containers or model
weights. **Even fixed, NGC is a registry, not compute** — it would unblock
*pulling* Transfer artifacts, not running them. NVIDIA Cloud Functions (NVCF) is
the one plausible compute path behind that key and could not be queried.

**So step 2-4 of the factory (set up, generate, inspect) are blocked on access,
not on engineering.** What follows is the design, with what is already built and
measured marked separately from what is assumed.

---

## 2. What is already built and calibrated (do not re-derive)

More of this pipeline exists than the roadmap implies.

### The Evaluator exists and is reference-based — a measured conclusion

`src/solar_twin/wfm/evaluator.py` + `wfm/base.py` (+ `tests/test_wfm_evaluator.py`),
calibrated against **six real Cosmos3-Edge generations**. The headline finding is
a *pipeline* conclusion, not a threshold:

> **No-reference image statistics cannot separate good generated frames from bad.**

- `edge_try2` was photorealistic and **not a PV module at all**, yet scored the
  **highest grid-periodicity of the set** — so any "has a regular cell grid" test
  admits it.
- The *good* frames carried **more** high-frequency energy than the noise frame,
  because a real cell lattice is high-frequency. "Less noise is better" is
  **backwards**.
- `MAX_STDDEV = 100.0` is a narrow backstop only — uniform noise measures ~74 and
  slips under it. `MIN_GRID_PEAK = 10.0` is what actually rejects unstructured
  frames.

So the gate is **reference-based**: `edge_retention(seed, generated) >= 0.60` —
the fraction of the seed's panel edges still present, in place, in the output.
**A frame with no seed is rejected as unverifiable, however good it looks.**

⚠ **That single rule decides the architecture.** It makes **Transfer-class,
structure-conditioned generation the only admissible source** for anything that
touches fault ground truth. An unconditioned text-to-image or text-to-video
generator can never clear the gate, because there is no seed to retain edges
*from*. This is why Cosmos3-Edge — which **does** serve on-box (~9.8 GB,
~2 s/image, co-resident with Isaac Sim) — is **the wrong tool for the data
factory** despite being the only on-box generator available. It is not a
substitute for Transfer, and using it would be building a corpus the gate must
reject.

⚠ Thresholds are **provisional**: 6 frames, one model. Re-calibrate on real
Transfer output before quoting them.

### Semantic labels are already authored

`farm_builder._label()` applies `UsdSemantics.LabelsAPI` (taxonomy `class`) to
panels (`"panel"`, plus the `pv:state` value), turbines, roads and site works. So
the **segmentation control branch has real ground truth**, not an estimate.

### Seeded, reproducible renders

The world is seeded and every stage is reproducible from a script + config, so a
seed frame can be regenerated byte-for-byte-ish later. ⚠ With the caveat measured
as `RISK-24`: RTX capture is **not** bit-reproducible (4 captures from an unmoved
camera gave 4 distinct digests, ~0.86/255 mean difference). The *picture* is
stable; the bytes are not. Archive the seed frame itself, never a promise to
re-render it.

---

## 3. What is missing on our side (engineering, not access)

### The control-branch inputs are not being captured

`world/sim_runtime.py` creates Replicator render products but registers **only the
`"rgb"` annotator**. Transfer 2.5's four control branches want **edge, blur,
segmentation, depth**. Two of those we can emit as *ground truth* rather than
estimates, which is a real advantage over conditioning on a photo:

| Branch | Source | Status |
|---|---|---|
| **depth** | Replicator `distance_to_camera` annotator | ⚠ not wired — needs adding |
| **segmentation** | Replicator `semantic_segmentation`, backed by the existing `UsdSemantics` labels | ⚠ not wired — labels exist, annotator does not |
| **edge** | derived from the seed RGB (the evaluator's `_edge_map` already does this) | ✓ available |
| **blur** | derived from the seed RGB | ✓ available |

⚠ **Annotator latency gotcha already known:** annotators lag the render by a frame
or two; `sim_runtime` already pumps N app updates for `rgb`. Any new annotator
needs the same treatment or it returns the *previous* pose's buffer — which would
silently mis-pair a depth map with an RGB frame.

### There is no `WorldModel` implementation

`wfm/base.py` defines the interface (`transfer()`, `predict()`, `GeneratedFrame`)
with no concrete impl. That is the correct shape — the off-box service slots in
behind it exactly as `cosmos_reason.py` sits behind `Perception` — but it means
the client is unwritten.

---

## 4. The pipeline

```
   [ Spark, on-box ]                    [ off-box: RTX PRO 6000 / DGX / cloud ]
                                        
   farm_builder.py  (seeded)                    Cosmos Transfer 2.5
        |                                    (control: edge/blur/seg/depth)
        v                                              ^      |
   SimRuntime capture ──> seed RGB ─────────────────────┘      |
        |                 + depth  (⚠ to wire)                 |
        |                 + segmentation (⚠ to wire)           |
        |                                                      v
        |                                              variant frames
        |                                                      |
        v                                                      |
   wfm/evaluator.py  <────────────────────────────────────────-┘
   evaluate(generated, seed=THE SEED IT CAME FROM)
        |
        ├─ reject: no seed / edge_retention < 0.60 / grid_peak < 10
        |          -> quarantine, never enters a corpus
        v
   accepted corpus ──> harden Perception (KPI-03 regression scenarios)
```

**The seed must travel with the variant.** Not as provenance decoration — the
gate is reference-based, so a variant whose seed is lost is *unevaluable* and
therefore unusable. Every generated frame needs its seed's identity (run id,
panel id, camera pose) recorded alongside it, or it is dead weight.

**Evaluation runs on-box, generation does not.** The Evaluator is pure-python
(numpy) and needs no GPU; the Cosmos-Reason-backed semantic half, if added, has a
vLLM server already running here. So the expensive asymmetry is one-directional:
push seeds out, pull variants back, judge locally.

### First batch, when access exists (deliberately tiny)

One seeded frame → four variants: **dust**, **haze**, **low-sun**, **one
blade-shadow**. Do not scale up. The purpose of batch one is to answer three
questions that only real output can answer:

1. Does `edge_retention >= 0.60` hold on real Transfer output, or is the
   threshold mis-calibrated for this model? (It was set on Edge output.)
2. Does the blade-shadow variant put the shadow where **geometry** says it should
   be, or where it *looks* plausible? This is the `FR-05` poisoning risk in its
   sharpest form — a geometrically-wrong shadow is exactly the false-hotspot
   failure this project exists to prevent.
3. Does dust land as a *surface film on the module* or as *atmospheric haze in
   front of it*? Those are different faults (`soiled` vs. nothing) and a model
   that conflates them manufactures false `pv:state` ground truth.

**Look at the frames.** Session 9's Edge investigation is the precedent: the
`num_inference_steps` omission produced a valid-looking 640x640 PNG of **pure
noise**, with no error, no warning, and a byte size that could not detect it
(uncompressed → always 1,229,899 bytes). An automated gate is necessary and not
sufficient.

---

## 5. Proven vs. assumed

**Proven on this box:**
- Transfer does not run here (`NFR-05`, locked; sm_121/GB10 unsupported).
- No-reference gating does not work; reference-based gating does (6 frames).
- Cosmos3-Edge serves on-box but is a generator with no control branches — wrong
  tool for this pipeline.
- Semantic labels are authored on panels today.
- RTX capture is not bit-reproducible (`RISK-24`).
- No off-box compute is currently reachable (§1).

**Assumed / ⚠ verify before relying on:**
- Transfer 2.5's four control branches are edge / blur / segmentation / depth, on
  a Predict2.5 backbone (from the repo + cookbook; not run).
- That Transfer preserves geometrically-correct shadow motion. **This is the
  central data-integrity risk and it is unverified.** The research doc flags it
  explicitly; treat every blade-shadow variant as suspect until measured.
- `MIN_EDGE_RETENTION = 0.60` transfers from Edge output to Transfer output.
- Licensing for commercial photoreal augmentation of a real customer site
  (NVIDIA Open Model License specifics).
- Whether a distilled/edge Transfer variant or Cosmos 3 changes the off-box
  verdict. ⚠ Cosmos 3 supersedes Reason2 as of 2026-06-01 and the 2.x recipe line
  is the safer near-term target, but this should be re-checked — the answer moves
  monthly.

---

## 6. What OSMO adds at scale

Nothing, until batch one has been inspected by a human. OSMO is an orchestrator;
orchestrating a pipeline whose output has not been validated just produces
poisoned data faster.

Once the gate is calibrated on real Transfer output, OSMO earns its place at the
point where **three things stop fitting in a shell script**:

- **Heterogeneous placement.** Generation wants off-box GPUs; evaluation wants
  the box with the seeds and the vLLM. OSMO schedules across both instead of
  someone copying tarballs.
- **Fan-out with provenance.** One seed → N variants → M evaluations, with the
  seed identity surviving every hop. The reference-based gate makes this a *hard*
  requirement, not hygiene: lose the seed mapping and the whole batch becomes
  unevaluable.
- **Retry and quarantine as first-class states.** A rejected frame is a normal
  outcome here, not an error. The corpus needs an accepted set, a quarantined set,
  and the reason each frame landed where it did.

The Physical AI Data Factory Blueprint's chain — **Curator → Transfer →
Evaluator** — maps onto this directly, with our `wfm/evaluator.py` standing in for
the Evaluator stage (⚠ Blueprint GitHub availability was "planned April 2026";
confirm current state).

---

## 7. Recommended next steps

1. **Resolve compute access.** This is a commercial/access question, not
   engineering: either refresh the AWS STS credentials into something durable, or
   name the RTX PRO 6000 / DGX host that will carry this. Until then §3's work is
   the only movable part.
2. **Fix the `ngc` wrapper** (`"$@"`) and establish whether the key is live and
   whether the org carries NVCF. That decides if NVCF is a compute path at all.
3. **Wire the depth + segmentation annotators** in `sim_runtime.py` (with the same
   frame-pump treatment `rgb` gets). This is on-box, testable now, and is the real
   prerequisite for conditioning — worth doing before access lands.
4. **Write the `WorldModel` HTTP client** behind `wfm/base.py`, fake-able in
   tests, mirroring how `cosmos_reason.py` is structured.
5. **Then** batch one: 1 seed → 4 variants → evaluate → *look at them*.
6. Re-calibrate `MIN_EDGE_RETENTION` on real Transfer output and record the
   sample size beside the number.
