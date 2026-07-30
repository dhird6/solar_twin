#!/usr/bin/env bash
# Watch an inspection mission live — on the Spark's own monitor, or streamed to
# another machine over WebRTC.
#
# Wraps `solar_twin.run` with the preflight checks that were each a real trap on
# this Spark: a stale sim holding port 49100, a missing farm USD, a perception
# backend whose server is not up, no display for a window. Nothing here is
# load-bearing for the pipeline — every stage is still a python entry point + a
# config (CLAUDE.md); this only saves typing and tells you where to look.
#
#   tools/run_livestream.sh                    # stream to a laptop (WebRTC)
#   tools/run_livestream.sh 12 mission --gui   # window on the Spark's HDMI monitor
#   tools/run_livestream.sh 100                # 100 panels instead of 12
#   tools/run_livestream.sh 12 mission_cosmos  # real VLM (⚠ freezes ~12 s/panel)
#   tools/run_livestream.sh 12 mission --route serpentine   # extra flags pass through
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

FARM_CFG=configs/farm_khavda_block02.yaml
FARM_USD=assets/khavda_full.usd
MISSION_CFG="configs/${MISSION_NAME}.yaml"

# Ports are set by the build, not by us: apps/isaacsim.exp.full.streaming.kit.
SIGNAL_PORT=49100
STREAM_PORT=47998

# A window on the attached monitor and a WebRTC stream are mutually exclusive
# here: run.py maps --gui to headless=False and --livestream to headless=True +
# hide_ui, so asking for both would silently give you one of them.
MODE=stream
for a in "$@"; do [[ "$a" == "--gui" ]] && MODE=gui; done
# Drop --gui from the passthrough args; the run command below adds it explicitly.
ARGS=(); for a in "$@"; do [[ "$a" == "--gui" ]] || ARGS+=("$a"); done

die() { printf '\n[FAIL] %s\n' "$*" >&2; exit 1; }

# --- preflight ---------------------------------------------------------- #
[[ -n "${ISAACSIM_PYTHON_EXE:-}" ]] || die \
  'ISAACSIM_PYTHON_EXE is not set. Open a new shell (~/.bashrc exports it), or:
  export ISAACSIM_PYTHON_EXE=$HOME/IsaacSim/_build/linux-aarch64/release/python.sh'
[[ -x "$ISAACSIM_PYTHON_EXE" ]] || die "not executable: $ISAACSIM_PYTHON_EXE"
[[ -f "$MISSION_CFG" ]] || die "no such mission config: $MISSION_CFG"
[[ -f "$FARM_CFG"    ]] || die "no such farm config: $FARM_CFG"
[[ -f "$FARM_USD"    ]] || die "no built world at $FARM_USD — build it first:
  PYTHONPATH=src \"\$ISAACSIM_PYTHON_EXE\" -m solar_twin.world.farm_builder \\
      $FARM_CFG --out $FARM_USD"

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

# Only cosmos_reason needs the VLM server; say so before a 30 s startup, not after.
PERCEPTION="$(sed -n 's/^perception:[[:space:]]*\([a-z_]*\).*/\1/p' "$MISSION_CFG" | head -1)"
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
    Resolution        1280 x 720      (matches world/sim_runtime.py)
  ───────────────────────────────────────────────────────────────
  Wait for the "livestream: connect..." line below, THEN hit Connect.
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

cat <<EOF
  world       ${FARM_USD}
  mission     ${MISSION_CFG}  (perception: ${PERCEPTION:-?})
  panels      ${PANELS}

EOF

# --- run ---------------------------------------------------------------- #
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
