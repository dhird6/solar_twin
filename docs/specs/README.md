# solar-twin — Specification Set

> **Source:** distilled and made traceable from `docs/DIGITAL_TWIN_VISION_AND_RESEARCH.md`
> (13-agent research swarm, generated 2026-07-23) and the codebase as it stands on
> `cosmos-reason-live`. **Generated:** 2026-07-24.

This folder is the engineering specification for the full vision — a photoreal,
physically-honest digital twin of a real solar farm, per `CLAUDE.md`'s framing:
*"A robot fleet inspects panels in an Isaac Sim world; verdicts are written back
onto USD panel prims; a closed maintenance loop is the end goal."* Where the
research document argues and cites sources, these documents specify: numbered
requirements, hazard entries, KPIs, and gates an engineer can implement and test
against directly.

## How this relates to the other docs

| Doc | Role | Relationship to `specs/` |
|---|---|---|
| `CLAUDE.md` | Always-loaded operating brief, golden rules | Specs never override it; any spec that conflicts with a golden rule is wrong and must be fixed here |
| `docs/PROJECT_BIBLE.md` §6 | **Locked** canonical contracts (`PVModule`, coordinates, ROS 2 topics, `Perception`, fault taxonomy) | Specs **reference** these, never restate or redefine them. `04-interfaces-and-data.md` proposes only *additive* extensions |
| `docs/DIGITAL_TWIN_VISION_AND_RESEARCH.md` | Research narrative: why, what NVIDIA offers, what's ⚠ unverified | Specs are the **derived, actionable form** of this document. If the research doc is revised, re-derive the affected spec section |
| `docs/TASKS.md` | Live per-person task board | Specs are the backlog *source*; TASKS.md is the day-to-day execution view |
| `docs/ENVIRONMENT.md`, `docs/STACK.md`, `docs/ROS2_CONTRACT.md` | Environment/build and stack detail | `08-platform-and-risk-register.md` cross-references, doesn't duplicate |

**Rule:** if a spec here and `PROJECT_BIBLE.md` §6 disagree about a locked
contract, `PROJECT_BIBLE.md` wins and this folder is wrong — fix it in the same
commit, per `CLAUDE.md`'s commit convention.

## Document map

| # | Document | Answers |
|---|---|---|
| 01 | [Scope & Vision](01-scope-and-vision.md) | What is "done"? What's explicitly out of scope? |
| 02 | [Requirements](02-requirements.md) | What must the system do (FR) and how well (NFR)? |
| 03 | [Architecture](03-architecture.md) | What are the pillars/components and how do they compose? |
| 04 | [Interfaces & Data](04-interfaces-and-data.md) | What's locked today vs. what additive extensions does the roadmap need? |
| 05 | [Hazard & Safety](05-hazard-and-safety.md) | What can hurt the fleet or corrupt a verdict, and how is each mitigated? |
| 06 | [Scenario Suite & KPIs](06-scenario-suite-and-kpis.md) | How do we measure "good enough to fly," and what config drives it? |
| 07 | [Roadmap & Milestones](07-roadmap-and-milestones.md) | What ships in what order, with what entry/exit gate? |
| 08 | [Platform & Risk Register](08-platform-and-risk-register.md) | What's verified on this Spark vs. still a ⚠, and what's the resolution plan? |

## ID conventions (stable across documents — cite these, don't renumber)

| Prefix | Meaning | Defined in |
|---|---|---|
| `FR-nn` | Functional requirement | `02-requirements.md` |
| `NFR-nn` | Non-functional requirement | `02-requirements.md` |
| `PIL-n` | Architecture pillar (1–6) | `03-architecture.md` |
| `IF-nn` | Interface/data extension proposal | `04-interfaces-and-data.md` |
| `HAZ-nn` | Hazard entry | `05-hazard-and-safety.md` |
| `KPI-nn` | Measured, gated metric | `06-scenario-suite-and-kpis.md` |
| `SC-nn` | Named scenario | `06-scenario-suite-and-kpis.md` |
| `SLICE-n` | Roadmap slice (0–8) | `07-roadmap-and-milestones.md` |
| `RISK-nn` | Open, tracked risk (mostly ex-⚠verify items) | `08-platform-and-risk-register.md` |

Cross-references use these IDs, e.g. "`HAZ-02` is gated by `KPI-03`, delivered in
`SLICE-3`." When you close a `RISK-nn` or promote a requirement's status, edit
in place — these are living specs, not a frozen snapshot of the research report.

## Status legend (used throughout)

| Status | Meaning |
|---|---|
| **Locked** | Already true of the shipped code; changing it is a breaking change |
| **Proposed** | Specified here, not yet implemented; ready to build |
| **Research** | Vision-level; NVIDIA capability or our approach is not yet validated enough to commit an interface to it |
| **⚠ Verify** | Blocked on confirming a fact about the installed Spark build (version, API, hardware support) before design can be trusted |

## Non-negotiables inherited from `CLAUDE.md` (do not re-litigate here)

- The USD stage is the source of truth for panel state; nothing in this spec
  set introduces a side store.
- `Perception` / `Transport` / `RobotControl` stay swappable; every extension
  proposed here is additive to the existing ABCs, never a breaking rewrite.
- Isaac/`pxr`/`omni` imports stay out of `orchestrator/`, `perception/base.py`,
  `transport/base.py`, `control/base.py` — pure-python testability is a hard
  boundary, not a preference.
- Everything reproducible is a script + a config in `configs/`; no GUI-only
  step is ever the only way to reach a state described in these specs.
- Isaac Sim version and any version-pinned API/asset path is **⚠ verify**
  against the installed build before trusting a spec that assumes it — see
  `08-platform-and-risk-register.md` `RISK-01`.
