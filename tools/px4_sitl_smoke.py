#!/usr/bin/env python3
"""PX4 SITL smoke test on this Spark — resolves the PX4 half of `RISK-02`.

**No Isaac, no GPU, no Pegasus.** This answers one question that gates all of
`SLICE-2`: *can PX4 SITL run on aarch64 at all, and can Isaac reach its
simulator seam?* Everything else about flight dynamics is downstream of that, so
it is checked first and cheaply.

The answer turned out to be yes, and by an easier route than the docs suggest:

* Pegasus's install guide has you **build PX4 v1.14.3 from source** — a 2023
  release, on a 2024 distro, on an architecture its docs do not mention. Skipped.
* PX4's own "pre-built SITL packages" page promises `.deb`s for Ubuntu 24.04
  arm64, but the tagged GitHub releases carry only a VOXL board package.
* What does exist is the **official multi-arch container** `px4io/px4-sitl`,
  which publishes a real `linux/arm64` manifest. Measured 2026-07-29: 119 MB,
  native aarch64 ELF, boots to
  ``INFO [simulator_mavlink] Waiting for simulator to accept connection on TCP
  port 4560`` — exactly the state an external simulator attaches to.

`PX4_SIM_MODEL=none_iris` selects the **external-simulator** path: Isaac owns the
physics and PX4 owns the control loops, which is what `FR-06` asks for. Left
unset, this image defaults to SIH (PX4 simulating its own dynamics), which is the
wrong half of the loop for a digital twin.

    python3 tools/px4_sitl_smoke.py            # start, verify the seam, tear down
    python3 tools/px4_sitl_smoke.py --pull     # refresh the image first
    python3 tools/px4_sitl_smoke.py --keep     # leave it running for a bridge

Exit code is 0 only if the seam was reachable, so it can gate CI or a session.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time

IMAGE = "px4io/px4-sitl:latest"
NAME = "px4sitl_smoke"
SIM_PORT = 4560  # PX4's simulator_mavlink listener (the sim connects IN to this)
GCS_PORT = 14550  # MAVLink to a ground station / MAVSDK, UDP
#: PX4 prints this once simulator_mavlink is listening. Waiting for the LINE
#: rather than sleeping a fixed time is the difference between a smoke test and a
#: coin flip on a loaded box.
READY_LINE = "Waiting for simulator to accept connection on TCP port"
BOOT_TIMEOUT_S = 90


def _run(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _kill() -> None:
    _run(["docker", "kill", NAME], timeout=30)
    _run(["docker", "rm", "-f", NAME], timeout=30)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pull", action="store_true", help="docker pull the image first")
    ap.add_argument("--keep", action="store_true",
                    help="leave PX4 running (for an actual bridge) instead of tearing down")
    ap.add_argument("--model", default="none_iris",
                    help="PX4_SIM_MODEL; `none_*` = external simulator (default), "
                         "otherwise PX4 runs its own SIH physics")
    args = ap.parse_args(argv)

    if not _run(["which", "docker"]).stdout.strip():
        print("FAIL: docker not on PATH", file=sys.stderr)
        return 2

    if args.pull:
        print(f"pulling {IMAGE} (linux/arm64) ...")
        p = _run(["docker", "pull", "--platform", "linux/arm64", IMAGE], timeout=900)
        if p.returncode:
            print(f"FAIL: pull failed\n{p.stderr[-500:]}", file=sys.stderr)
            return 2

    inspect = _run(["docker", "image", "inspect", IMAGE, "--format",
                    "{{.Architecture}} {{.Os}}"])
    if inspect.returncode:
        print(f"FAIL: {IMAGE} not present locally — run with --pull", file=sys.stderr)
        return 2
    arch = inspect.stdout.strip()
    print(f"image: {IMAGE}  [{arch}]")
    if not arch.startswith("arm64"):
        # Loud, because an amd64 image under emulation would "work" while telling
        # us nothing about native aarch64 support.
        print(f"⚠ image is {arch}, not arm64 — this proves nothing about this box")

    _kill()  # a stale container from a previous run would hold the port
    # ⚠ `--network host`, NOT `-p 4560:4560`. PX4 is the CLIENT here: it dials out
    # to the simulator, which listens. Publishing the port made docker-proxy bind
    # 4560 on the host, which (a) gave this script a FALSE PASS — the TCP connect
    # below reached docker-proxy, not PX4 — and (b) then blocked the real simulator
    # from binding it (`OSError: [Errno 98] Address already in use` inside
    # Pegasus's MAVLink backend). Sharing the host loopback lets PX4 reach a
    # simulator listening on 127.0.0.1.
    start = _run([
        "docker", "run", "-d", "--rm", "--name", NAME,
        "--platform", "linux/arm64", "--network", "host",
        "-e", f"PX4_SIM_MODEL={args.model}",
        "-e", "PX4_SIM_HOSTNAME=localhost", IMAGE,
    ])
    if start.returncode:
        print(f"FAIL: could not start container\n{start.stderr[-500:]}", file=sys.stderr)
        return 2

    print(f"waiting for PX4 to open the simulator seam (model={args.model}) ...")
    deadline = time.monotonic() + BOOT_TIMEOUT_S
    ready = False
    while time.monotonic() < deadline:
        logs = _run(["docker", "logs", NAME]).stdout
        if READY_LINE in logs:
            ready = True
            break
        if "ERROR" in logs and "px4 starting" not in logs:
            break
        time.sleep(2)

    logs = _run(["docker", "logs", NAME]).stdout
    if not ready:
        print("FAIL: PX4 never reported the simulator seam. Last log lines:",
              file=sys.stderr)
        print("\n".join(logs.splitlines()[-15:]), file=sys.stderr)
        _kill()
        return 1
    print(f"  PX4 booted: {READY_LINE} {SIM_PORT}")

    # PX4 dials OUT, so nothing should be listening on 4560 yet — the simulator
    # binds it. Asserting that is the real check; an earlier version connected to
    # the port and called it a pass, which only proved docker-proxy was up.
    listening = _run(["bash", "-c", f"ss -ltn 2>/dev/null | grep -c ':{SIM_PORT} '"]).stdout.strip()
    if listening not in ("", "0"):
        print(f"⚠ something is ALREADY listening on {SIM_PORT}. Pegasus needs to bind it;"
              f" a publisher/proxy there will fail the sim with EADDRINUSE.", file=sys.stderr)
        _kill()
        return 1
    print(f"  port {SIM_PORT} is free for the simulator to bind, and PX4 is dialing it")

    print("\nPASS — PX4 SITL runs natively on this aarch64 Spark and is waiting for a\n"
          "       simulator to connect. Next: `tools/px4_hover.py` flies the Iris\n"
          "       against it (FR-06). ⚠ PX4 SITL does NOT recover from a simulator\n"
          "       disconnect — restart this container for every flight.")
    if args.keep:
        print(f"\n(container `{NAME}` left running — `docker kill {NAME}` when done)")
    else:
        _kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
