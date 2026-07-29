#!/usr/bin/env bash
# Install Pegasus Simulator v5.1.0, ported to run on this box's Isaac Sim 6.0.1.
#
# WHY A SCRIPT AND A PATCH, rather than a vendored copy or hand instructions:
#   * Pegasus is ~240 MB and BSD-3-Clause third-party code. Committing it would
#     break `CLAUDE.md`'s "do not commit large binaries" rule and bury our ~270
#     lines of actual work in someone else's tree.
#   * "Everything reproducible is a script + a config" — so the port is a pinned
#     clone plus one reviewable patch, not a README someone re-derives.
#
# WHAT THE PATCH DOES (tools/patches/pegasus-v5.1.0-isaac6.patch)
#   `omni.isaac.dynamic_control` was retired in Isaac's 4.5/5.0 API migration and
#   is ABSENT from the 6.0.1 build, so Pegasus's Vehicle/Multirotor cannot even be
#   constructed. Every legacy call funnels through one accessor, so the patch adds
#   `dc_compat.py` — the same ten methods reimplemented on `isaacsim.core.prims` —
#   and changes only two imports and that accessor. Upstream call sites stay
#   byte-identical, so future upstream merges stay clean.
#
# ⚠ DELIBERATELY NOT `pip install -e pegasus.simulator`. Its `setup.py` carries a
#   `PatchIsaacSimKitApp` hook that REWRITES Isaac's own `.kit` app files to add a
#   replicator extension. That mutates the source-built Isaac install out from
#   under us. Instead only the one missing dependency is installed, and the
#   extension is reached via PYTHONPATH — see docs/ENVIRONMENT.md.
#
# ⚠ SCOPE, honestly: this gets Pegasus IMPORTING and its vehicle reading/writing
#   physics through the shim on 6.0.1 (both measured). It does NOT yet demonstrate
#   a PX4-governed hover — see docs/specs/08-platform-and-risk-register.md
#   `RISK-02`(b) and `RISK-28`.
#
# Usage:  bash tools/install_pegasus_isaac6.sh [--force]
set -euo pipefail

PEGASUS_DIR="${PEGASUS_DIR:-/home/simulationhub/PegasusSimulator}"
PEGASUS_TAG="v5.1.0"
PEGASUS_COMMIT="644da37"   # what the patch was generated against
ISAAC_PY="${ISAAC_PY:-/home/simulationhub/IsaacSim/_build/linux-aarch64/release/python.sh}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATCH="$REPO_ROOT/tools/patches/pegasus-v5.1.0-isaac6.patch"

[[ "${1:-}" == "--force" ]] && rm -rf "$PEGASUS_DIR"

if [[ ! -x "$ISAAC_PY" ]]; then
  echo "ERROR: Isaac python not found at $ISAAC_PY" >&2
  exit 2
fi
[[ -f "$PATCH" ]] || { echo "ERROR: patch missing: $PATCH" >&2; exit 2; }

if [[ -d "$PEGASUS_DIR/.git" ]]; then
  echo "==> Pegasus already present at $PEGASUS_DIR (use --force to re-clone)"
else
  echo "==> Cloning Pegasus $PEGASUS_TAG"
  git clone -q --branch "$PEGASUS_TAG" \
      https://github.com/PegasusSimulator/PegasusSimulator.git "$PEGASUS_DIR"
fi

cd "$PEGASUS_DIR"
HEAD_SHORT="$(git rev-parse --short HEAD)"
if [[ "$HEAD_SHORT" != "$PEGASUS_COMMIT"* && "$PEGASUS_COMMIT" != "$HEAD_SHORT"* ]]; then
  # Loud, not fatal: the patch may still apply, but the pin is the thing that
  # makes this reproducible, so a drift must be stated rather than absorbed.
  echo "⚠ WARNING: Pegasus is at $HEAD_SHORT; the patch was generated against $PEGASUS_COMMIT."
fi

echo "==> Applying the Isaac 6 port patch"
if git apply --check "$PATCH" 2>/dev/null; then
  git apply "$PATCH"
  echo "    applied"
elif git apply --reverse --check "$PATCH" 2>/dev/null; then
  echo "    already applied — nothing to do"
else
  echo "ERROR: patch does not apply cleanly to $HEAD_SHORT." >&2
  echo "       Upstream has moved; re-derive it rather than forcing." >&2
  exit 1
fi

echo "==> Installing the one missing dependency into Isaac's Python"
# numpy / scipy / pyyaml are already present in the Isaac 6.0.1 bundle; only
# pymavlink is missing. `--no-deps` is deliberate: this box has been broken once
# already by a transitive numpy upgrade (see SESSIONS.md Session 10d).
if "$ISAAC_PY" -c "import pymavlink" >/dev/null 2>&1; then
  echo "    pymavlink already installed"
else
  "$ISAAC_PY" -m pip install --no-deps pymavlink
fi

echo "==> Verifying the port imports under Isaac Sim 6.0.1 (headless)"
EXT_PATH="$PEGASUS_DIR/extensions/pegasus.simulator"
cat > /tmp/_pegasus_verify.py <<PYEOF
from isaacsim import SimulationApp
app = SimulationApp({"headless": True})
import sys
sys.path.insert(0, "$EXT_PATH")
mods = [
    "pegasus.simulator.logic.vehicles.dc_compat",
    "pegasus.simulator.logic.vehicles.vehicle",
    "pegasus.simulator.logic.vehicles.multirotor",
    "pegasus.simulator.logic.backends.px4_mavlink_backend",
]
bad = []
for m in mods:
    try:
        __import__(m)
    except Exception as exc:
        bad.append((m, f"{type(exc).__name__}: {exc}"))
# Write the verdict to a file rather than stdout: Isaac floods the tail of the
# log with extension-shutdown lines, and SimulationApp can outlive the exit code,
# so neither grepping stdout nor \$? is trustworthy here.
with open("/tmp/_pegasus_verify.result", "w") as fh:
    fh.write("OK" if not bad else "\n".join(f"FAIL {m}: {why}" for m, why in bad))
app.close()
PYEOF
rm -f /tmp/_pegasus_verify.result
"$ISAAC_PY" /tmp/_pegasus_verify.py >/tmp/_pegasus_verify.log 2>&1 || true
if [[ "$(cat /tmp/_pegasus_verify.result 2>/dev/null)" == "OK" ]]; then
  echo "    OK — Vehicle, Multirotor and the PX4 MAVLink backend all import"
else
  echo "ERROR: import verification failed:" >&2
  cat /tmp/_pegasus_verify.result 2>/dev/null >&2 || echo "  (no result written — see /tmp/_pegasus_verify.log)" >&2
  exit 1
fi

cat <<EOF

==> Done.

    Extension path (add to PYTHONPATH, or pass --ext-folder to Isaac):
      $EXT_PATH

    A standalone app must create the World on Pegasus's own singleton before
    constructing any vehicle, or Vehicle.__init__ dies on \`self._world.stage\`:

      pg = PegasusInterface()
      pg._world = World(**pg._world_settings)

    Then \`world.reset()\` and \`world.play()\` BEFORE stepping — without play there
    is no physics simulation view and every prim read silently returns the static
    USD pose.
EOF
