#!/usr/bin/env python3
"""Assemble the boss-facing cinematic cut from rendered footage.

Pure CPU: PIL for the cards, ffmpeg for the assembly. No Isaac, no GPU.

Structure — site, then scale, then the fleet doing the thing, then the numbers:

    [title]  KHAVDA                       4s
    [A]      flythrough over the plant    ~32s
    [card]   the scale claim              4s
    [B]      the mission, four beats      ~31s
    [end]    what actually happened       6s

Everything on the end card is read out of the run record, never typed in --
same rule as `plant_tour.py`: if the run changes, the card changes with it.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
FPS = 24
OUT = Path("assets/khavda_cinematic.mp4")
SCRATCH = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/cine")
FLY = Path("assets/khavda_cinematic_flythrough.mp4")
MISSION = Path("runs/20260731T022151/inspection.mp4")
RUN = Path("runs/20260731T022151/results.json")

FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_R = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Sampled from the twin's own materials: PV cell diffuse and the Kutch ground.
INK = (232, 234, 239)
DIM = (150, 158, 172)
GLASS = (10, 20, 40)
SAND = (201, 183, 154)
HOT = (224, 85, 26)


def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def card(path: Path, lines, sub=None, accent=None, rule=True) -> None:
    """A title card. `lines` is a list of (text, size, colour)."""
    img = Image.new("RGB", (W, H), GLASS)
    d = ImageDraw.Draw(img)
    # A faint horizon band, so the card belongs to the same world as the footage.
    for y in range(H):
        t = y / H
        if t > 0.72:
            k = (t - 0.72) / 0.28
            d.line([(0, y), (W, y)], fill=(
                int(GLASS[0] + (28 - GLASS[0]) * k),
                int(GLASS[1] + (30 - GLASS[1]) * k),
                int(GLASS[2] + (38 - GLASS[2]) * k)))

    total = sum(s + 22 for _, s, _ in lines)
    y = (H - total) // 2
    for text, size, col in lines:
        f = _font(FONT_B if size > 44 else FONT_R, size)
        w = d.textbbox((0, 0), text, font=f)[2]
        d.text(((W - w) // 2, y), text, font=f, fill=col)
        y += size + 22

    if rule:
        d.line([(W // 2 - 90, y + 12), (W // 2 + 90, y + 12)],
               fill=accent or SAND, width=3)
    if sub:
        f = _font(FONT_R, 30)
        w = d.textbbox((0, 0), sub, font=f)[2]
        d.text(((W - w) // 2, y + 46), sub, font=f, fill=DIM)
    img.save(path)


def clip_from_card(png: Path, out: Path, seconds: float) -> None:
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(png),
        "-t", f"{seconds}", "-r", str(FPS),
        "-vf", f"scale={W}:{H},format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", str(out),
    ], check=True)


def normalise(src: Path, out: Path) -> None:
    """Footage arrives at mixed sizes; pad rather than stretch so nothing distorts."""
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-i", str(src), "-r", str(FPS),
        "-vf", (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0A1428,format=yuv420p"),
        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18", str(out),
    ], check=True)


def main() -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if not FLY.exists():
        print(f"MISSING {FLY} — render the flythrough first", file=sys.stderr)
        return 1
    if not MISSION.exists():
        print(f"MISSING {MISSION}", file=sys.stderr)
        return 1

    rec = json.loads(RUN.read_text())
    m = rec["metrics"]
    n = m["panels_inspected"]
    found = m["faults_detected"]
    healthy = n - found
    faulted = [p for p in rec["panels"] if p.get("escalated")]
    pid = faulted[0]["panel_id"] if faulted else "-"
    state = faulted[0]["detected_state"] if faulted else "-"
    total_panels = rec["n_panels"]

    card(SCRATCH / "c1.png",
         [("KHAVDA", 132, INK), ("GUJARAT, INDIA", 40, SAND)],
         sub="Autonomous solar inspection - digital twin")
    card(SCRATCH / "c2.png",
         [(f"{total_panels:,}", 150, INK), ("MODULES ON ONE STAGE", 38, SAND)],
         sub="Real vendor CAD - real Copernicus terrain - physically-based sky")
    card(SCRATCH / "c3.png",
         [("THE FLEET INSPECTS", 84, INK)],
         sub="scout  -  dispatch  -  converge  -  inspect", accent=HOT)
    # ⚠ The perception backend is named on the card, and that is NOT optional.
    # This run used the ground-truth reference, which reads the injected state off
    # the prim and therefore cannot miss -- it demonstrates the CHOREOGRAPHY, not
    # detection skill. Unlabelled, a viewer reasonably concludes "the AI found the
    # fault". The scenario's own header says "DO NOT QUOTE A KPI FROM THIS FILE",
    # and this project has already had KPI-01 quoted off a demo config for weeks.
    backend = rec["perception"]["name"]
    honest = ("reference perception - choreography, not a detection score"
              if backend == "ground_truth"
              else f"perception: {backend}")
    card(SCRATCH / "c4.png",
         [(f"{n} inspected   {healthy} healthy   {found} fault", 66, INK),
          (f"{pid} confirmed {state}", 40, HOT)],
         sub="verdict written back onto the USD panel - closed loop", accent=HOT)
    card(SCRATCH / "c5.png",
         [("HOW THIS WAS MEASURED", 54, INK)],
         sub=honest, accent=SAND)

    parts = []
    for name, secs in (("c1", 4.0), ("c2", 3.5), ("c3", 3.5), ("c4", 6.0), ("c5", 4.0)):
        p = SCRATCH / f"{name}.mp4"
        clip_from_card(SCRATCH / f"{name}.png", p, secs)
    normalise(FLY, SCRATCH / "a.mp4")
    normalise(MISSION, SCRATCH / "b.mp4")

    parts = [SCRATCH / "c1.mp4", SCRATCH / "a.mp4", SCRATCH / "c2.mp4",
             SCRATCH / "c3.mp4", SCRATCH / "b.mp4", SCRATCH / "c4.mp4",
             SCRATCH / "c5.mp4"]

    lst = SCRATCH / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    # Concat demuxer, not xfade: every part is already the same size/fps/codec,
    # so this is a stream copy -- no re-encode, no generation loss, and it cannot
    # silently drop a segment the way a long filter_complex can.
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
        "-i", str(lst), "-c", "copy", str(OUT),
    ], check=True)

    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(OUT)],
        capture_output=True, text=True).stdout.strip()
    print(f"wrote {OUT}  {dur}s  {OUT.stat().st_size/1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
