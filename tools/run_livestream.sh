#!/usr/bin/env bash
# Watch an inspection mission live — on the Spark's own monitor, or streamed to
# another machine over WebRTC.
#
# Wraps `solar_twin.run` with the preflight checks that were each a real trap on
# this Spark: a stale sim holding port 49100, a missing farm USD, a farm USD older
# than the code that authors it, a perception backend whose server is not up, no
# display for a window. Nothing here is load-bearing for the pipeline — every stage
# is still a python entry point + a config (CLAUDE.md); this only saves typing and
# tells you where to look.
#
#   tools/run_livestream.sh                    # stream to a laptop (WebRTC)
#   tools/run_livestream.sh 12 mission --gui   # window on the Spark's HDMI monitor
#   tools/run_livestream.sh 100                # 100 panels instead of 12
#   tools/run_livestream.sh 12 mission_cosmos  # real VLM (⚠ freezes ~12 s/panel)
#   tools/run_livestream.sh 12 mission --route serpentine   # extra flags pass through
#
#   tools/run_livestream.sh 12 mission --farm s05b_full  # whole plot, interspersed
#   tools/run_livestream.sh 12 mission --no-build        # never rebuild the USD
#
#   # The one that shows the choreography: survey -> ground bot dispatched ->
#   # drones converge -> close inspection, with turbines standing among the panels.
#   tools/run_livestream.sh 24 mission \
#       --scenario configs/scenarios/fault_response_demo.yaml
#
# --gui renders straight to the attached display — no encode, no network, so it is
# the sharpest and cheapest way to watch when you are AT the machine. Streaming is
# for watching from elsewhere. Override the display with DISPLAY=:0 if needed.
set -euo pipefail

PANELS="${1:-12}"
MISSION_NAME="${2:-mission}"
shift 2 2>/dev/null || shift $# # drop the two positionals; the rest passes through

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# Ports are set by the build, not by us: apps/isaacsim.exp.full.streaming.kit.
SIGNAL_PORT=49100
STREAM_PORT=47998

# --- our own flags, parsed out of the passthrough ------------------------- #
# A window on the attached monitor and a WebRTC stream are mutually exclusive
# here: run.py maps --gui to headless=False and --livestream to headless=True +
# hide_ui, so asking for both would silently give you one of them.
MODE=stream
FARM_NAME=block02
BUILD=auto
SCENARIO=""
USD_OVERRIDE=""
ARGS=()
while (($#)); do
  case "$1" in
    --gui)         MODE=gui ;;
    --no-build)    BUILD=never ;;
    --farm)        FARM_NAME="${2:?--farm needs a name}"; shift ;;
    --farm=*)      FARM_NAME="${1#*=}" ;;
    # A scenario is consumed AND forwarded: the build needs it (fault rate and
    # turbine placement are baked into the USD) and so does the run (it carries
    # mission_mode/route). Passing it to only one of them is the trap below.
    --scenario)    SCENARIO="${2:?--scenario needs a path}"; ARGS+=("$1" "$2"); shift ;;
    --scenario=*)  SCENARIO="${1#*=}"; ARGS+=("$1") ;;
    # Reuse a stage another scenario already built. Two scenarios that differ ONLY
    # in mission settings (perception, route, mode) author byte-identical USDs, and
    # rebuilding the whole plot for one of them costs ~5 min for nothing.
    # ⚠ Not checked for you: pass this only when the farm_overrides really match.
    --usd)         USD_OVERRIDE="${2:?--usd needs a path}"; shift ;;
    --usd=*)       USD_OVERRIDE="${1#*=}" ;;
    *)             ARGS+=("$1") ;;
  esac
  shift
done

die() { printf '\n[FAIL] %s\n' "$*" >&2; exit 1; }

# --- which plot ----------------------------------------------------------- #
# config → USD is not derivable either way (block02's stage is historically
# `khavda_full.usd`), so the mapping stays explicit.
case "$FARM_NAME" in
  block02)
    FARM_CFG=configs/farm_khavda_block02.yaml
    FARM_USD=assets/khavda_full.usd
    ;;
  s05b_full)
    # The wide shot: 6,213 tables / 679,616 modules, ~680k prims, builds in ~176 s.
    # The only buildable stage carrying `turbine_scatter.placement: interspersed`
    # — the machines standing among the DC blocks rather than ringing them.
    FARM_CFG=configs/farm_khavda_s05b_full.yaml
    FARM_USD=assets/khavda_s05b_full.usd
    ;;
  s05b)
    # ⚠ Its own header lists two blockers: at faults.rate 0.02 this is ~1.69M prims,
    # worse than the 2.25M that gated the plant before instancing. Expect it to
    # fail to build until those are addressed; use --subset for measured runs.
    FARM_CFG=configs/farm_khavda_s05b.yaml
    FARM_USD=assets/khavda_s05b.usd
    ;;
  *) die "unknown --farm '${FARM_NAME}'. Known: block02, s05b_full, s05b" ;;
esac

# --- preflight ------------------------------------------------------------ #
[[ -n "${ISAACSIM_PYTHON_EXE:-}" ]] || die \
  'ISAACSIM_PYTHON_EXE is not set. Open a new shell (~/.bashrc exports it), or:
  export ISAACSIM_PYTHON_EXE=$HOME/IsaacSim/_build/linux-aarch64/release/python.sh'
[[ -x "$ISAACSIM_PYTHON_EXE" ]] || die "not executable: $ISAACSIM_PYTHON_EXE"
MISSION_CFG="configs/${MISSION_NAME}.yaml"
[[ -f "$MISSION_CFG" ]] || die "no such mission config: $MISSION_CFG"
[[ -f "$FARM_CFG"    ]] || die "no such farm config: $FARM_CFG"

# --- a scenario gets its OWN stage ---------------------------------------- #
# `faults.rate` and `turbine_scatter.placement` are baked into the USD at BUILD
# time, so a scenario overriding either cannot reuse the base farm's stage.
# fault_response_demo raises the whole plot's rate from 0.0 to 5e-4; pointed at
# assets/khavda_s05b_full.usd the survey would fly a stage with ZERO faults and
# flag nothing — a failure indistinguishable from a broken escalation path.
if [[ -n "$SCENARIO" ]]; then
  [[ -f "$SCENARIO" ]] || die "no such scenario: $SCENARIO"
  FARM_USD="assets/$(basename "$SCENARIO" .yaml).usd"
  BUILD_SRC=(--scenario "$SCENARIO")
  STALE_REF="$SCENARIO"
else
  BUILD_SRC=("$FARM_CFG")
  STALE_REF="$FARM_CFG"
fi

# An explicit stage wins over the derived name, and is never rebuilt: the caller is
# asserting this USD is already the right world.
if [[ -n "$USD_OVERRIDE" ]]; then
  [[ -f "$USD_OVERRIDE" ]] || die "no such stage: $USD_OVERRIDE
  Build it first, e.g. from the scenario that owns it:
    PYTHONPATH=src \"\$ISAACSIM_PYTHON_EXE\" -m solar_twin.world.farm_builder \\
        --scenario ${SCENARIO:-<scenario>} --out $USD_OVERRIDE"
  FARM_USD="$USD_OVERRIDE"
  BUILD=never
  printf '\n  [note] reusing stage %s (--usd): no build, staleness NOT checked.\n' \
    "$USD_OVERRIDE"
fi

# A stage built before the code that authors it shows you the OLD world, silently,
# and the run looks perfectly healthy while doing it. This check exists because it
# happened: the interspersed turbine field was committed while every USD on disk
# predated it, so watching a run would have "proved" the change did nothing.
# Staleness is mtime against the config AND the authoring sources — the config
# alone is not enough when the change landed in world/siting.py.
STALE_WHY=""
if [[ -n "$USD_OVERRIDE" ]]; then
  # Already validated above. Comparing it against the scenario's mtime would call a
  # freshly-written sibling scenario "newer" and warn about a stage that is correct.
  STALE_WHY=""
elif [[ ! -f "$FARM_USD" ]]; then
  STALE_WHY="it does not exist yet"
elif [[ "$FARM_USD" -ot "$STALE_REF" ]]; then
  STALE_WHY="$STALE_REF is newer"
else
  NEWER="$(find src/solar_twin/world src/solar_twin/schema -name '*.py' \
           -newer "$FARM_USD" -printf '%f ' 2>/dev/null)"
  [[ -n "$NEWER" ]] && STALE_WHY="newer sources: ${NEWER% }"
fi

if [[ -n "$STALE_WHY" ]]; then
  if [[ "$BUILD" == never ]]; then
    [[ -f "$FARM_USD" ]] || die "no built world at $FARM_USD, and --no-build was given"
    printf '\n  [warn] %s is STALE (%s) — running it anyway (--no-build).\n' \
      "$FARM_USD" "$STALE_WHY"
    printf '         What you watch will NOT include your latest changes.\n\n'
  else
    cat <<EOF

  ── rebuilding the world ───────────────────────────────────────
    stale             $FARM_USD
    because           $STALE_WHY
    authored from     ${SCENARIO:-$FARM_CFG}
  ───────────────────────────────────────────────────────────────
  ⚠ the whole plot takes ~176 s; block02 is quick. --no-build skips this.

EOF
    PYTHONPATH=src "$ISAACSIM_PYTHON_EXE" -m solar_twin.world.farm_builder \
      "${BUILD_SRC[@]}" --out "$FARM_USD" \
      || die "farm build failed — fix that before watching a run"
    printf '\n  [ok] rebuilt %s\n' "$FARM_USD"
  fi
fi

# faults.rate is 0.0 on the wide shot, so every panel is healthy: nothing escalates
# and no KPI is scoreable from it (that config says so in its own header). A
# scenario may override the rate, so only warn when nothing has.
if [[ "$FARM_NAME" == s05b_full && -z "$SCENARIO" ]]; then
  printf '\n  [note] %s has faults.rate 0.0 — every verdict will be "healthy" and\n' \
    "$FARM_NAME"
  printf '         nothing will escalate, so the drones never converge. For the\n'
  printf '         ground-bot-then-drones choreography add:\n'
  printf '           --scenario configs/scenarios/fault_response_demo.yaml\n'
fi

if [[ "$MODE" == stream ]]; then
  # A sim that did not exit cleanly still owns the signalling port, and the second
  # one fails to stream with no obvious reason why.
  if ss -tln 2>/dev/null | grep -q ":${SIGNAL_PORT}\b"; then
    die "port ${SIGNAL_PORT} is already in use — a previous sim is still alive:
  pkill -f solar_twin.run"
  fi
else
  # A window needs an X display that actually exists. Default to the seat that
  # owns the HDMI output on this box; DISPLAY from the environment wins.
  export DISPLAY="${DISPLAY:-:1}"
  [[ -S "/tmp/.X11-unix/X${DISPLAY#:}" ]] || die \
    "no X display at ${DISPLAY}. Available: $(ls /tmp/.X11-unix 2>/dev/null | tr '\n' ' ')
  Set it explicitly, e.g.  DISPLAY=:1 tools/run_livestream.sh 12 mission --gui"
fi

# Which mission config actually decides perception? With --scenario it is the file
# named by `extends.mission`, NOT the positional one: fault_response_demo_vlm.yaml
# extends mission_cosmos.yaml, so reading $MISSION_CFG here reported ground_truth
# and skipped the VLM preflight entirely for the one run that needs it.
PERCEPTION_CFG="$MISSION_CFG"
if [[ -n "$SCENARIO" ]]; then
  # `^ *mission:` matches only the extends key — `mission_mode:`/`mission_overrides:`
  # do not, because the colon must follow "mission" directly.
  SCN_MISSION="$(sed -n 's/^[[:space:]]*mission:[[:space:]]*\([^[:space:]#]*\).*/\1/p' \
                 "$SCENARIO" | head -1)"
  [[ -n "$SCN_MISSION" && -f "$SCN_MISSION" ]] && PERCEPTION_CFG="$SCN_MISSION"
  # A scenario may also flip perception in mission_overrides, which wins over the file.
  SCN_PERC="$(sed -n 's/^[[:space:]]\+perception:[[:space:]]*\([a-z_]*\).*/\1/p' \
              "$SCENARIO" | head -1)"
fi
# Only cosmos_reason needs the VLM server; say so before a 30 s startup, not after.
PERCEPTION="${SCN_PERC:-$(sed -n 's/^perception:[[:space:]]*\([a-z_]*\).*/\1/p' \
            "$PERCEPTION_CFG" | head -1)}"
if [[ "$PERCEPTION" == "cosmos_reason" ]]; then
  curl -sf -m 5 http://localhost:8000/v1/models >/dev/null || die \
    'perception: cosmos_reason but nothing serves localhost:8000. Start it:
  docker start vllm-cosmos    # then wait ~3 min for the weights to load
  (see docs/ENVIRONMENT.md "Serving Cosmos Reason on the Spark")'
  printf '\n  [warn] perception=cosmos_reason: the viewport FREEZES ~12 s per panel\n'
  printf '         (blocking HTTP on the main thread — docs/ENVIRONMENT.md).\n'
fi

if [[ "$MODE" == stream ]]; then
  LAN_IP="$(ip -4 -o addr show scope global 2>/dev/null \
            | awk '$2 !~ /^(docker|br-|veth|tailscale)/ {sub(/\/.*/,"",$4); print $4; exit}')"
  cat <<EOF

  ── connect the Isaac Sim WebRTC Streaming Client ──────────────
    Server            ${LAN_IP:-<this host>}
    Signalling port   ${SIGNAL_PORT}
    Stream port       ${STREAM_PORT}
  ───────────────────────────────────────────────────────────────
  The client picks the resolution from its own window and the server follows it
  (allowDynamicResize, set in world/sim_runtime.py). Without that the server
  refused every frame and the client showed BLACK — see docs/ENVIRONMENT.md.

  Wait for the "livestream: connect..." line below, THEN hit Connect. The app
  closes when the mission ends, so connect early or raise the panel count.
EOF
else
  MONITOR="$(DISPLAY="$DISPLAY" xrandr --query 2>/dev/null \
             | awk '/ connected/ {print $1; exit}')"
  cat <<EOF

  ── the Isaac Sim window opens on this machine's screen ────────
    Display           ${DISPLAY}   ${MONITOR:+(output: ${MONITOR})}
  ───────────────────────────────────────────────────────────────
  Verdicts print HERE in the terminal, not in the window: a verdict
  writes pv:state onto the panel prim, which has no visual effect.
EOF
fi

if [[ -n "$SCENARIO" ]]; then
  # --scenario overrides the positional configs inside run.py, so naming --farm here
  # would be a lie: the stage came from the scenario's own `extends.farm`.
  SCN_FARM="$(sed -n 's/^[[:space:]]*farm:[[:space:]]*\([^[:space:]#]*\).*/\1/p' \
              "$SCENARIO" | head -1)"
  SCN_MODE="$(sed -n 's/^[[:space:]]*mission_mode:[[:space:]]*\([a-z_]*\).*/\1/p' \
              "$SCENARIO" | head -1)"
  SCN_ROUTE="$(sed -n 's/^[[:space:]]*route:[[:space:]]*\([a-z_]*\).*/\1/p' \
               "$SCENARIO" | head -1)"
  cat <<EOF
  scenario    ${SCENARIO}
  farm        ${SCN_FARM:-?}   (from the scenario, not --farm)
  world       ${FARM_USD}
  mission     ${SCN_MODE:-sweep} / route ${SCN_ROUTE:-linear}
  perception  ${PERCEPTION:-?}
  panels      ${PANELS}

EOF
else
  cat <<EOF
  farm        ${FARM_NAME}   (${FARM_CFG})
  world       ${FARM_USD}
  mission     ${MISSION_CFG}  (perception: ${PERCEPTION:-?})
  panels      ${PANELS}

EOF
fi

# --- run ------------------------------------------------------------------ #
# --live is what makes the fleet fly instead of teleport; without it a watched
# run shows robots popping between waypoints. Both cost ~10x the sim steps, so
# this script is for demos — a KPI run uses neither (see run.py --live help).
VIEW_FLAG=--livestream
[[ "$MODE" == gui ]] && VIEW_FLAG=--gui

exec env PYTHONPATH=src "$ISAACSIM_PYTHON_EXE" -m solar_twin.run \
  "$FARM_CFG" "$MISSION_CFG" \
  --farm-usd "$FARM_USD" \
  "$VIEW_FLAG" --live \
  --max-panels "$PANELS" \
  "${ARGS[@]+"${ARGS[@]}"}"
