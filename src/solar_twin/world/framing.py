"""Where to park a free camera so the built plant is actually in frame (Isaac-free).

The bug this exists to prevent: `assets/khavda_4block.usd` opened in the viewer and
showed *empty desert*. Nothing was broken — `layout_import.select_blocks` keeps the
real surveyed coordinates on purpose ("nothing is tiled or mirrored"), so the four
kept blocks sit where they really are in the plot: **X from 566 m to 3717 m** east of
the stage origin. Kit's default perspective camera opens ~5 m from that origin, which
on this stage is bare ground half a kilometre short of the nearest table. A stage
whose geometry does not straddle the origin opens looking at nothing.

So the viewer has to *find* the geometry rather than assume it is at the origin. The
math is here, pure, because the Isaac half of this cannot be tested (`CLAUDE.md`:
new logic needs a test that runs without Isaac) and the part that was actually wrong
is arithmetic, not USD.

Convention: **Z-up, metres** (repo-wide). The returned eye sits south-west of the
target and above it, which on a Khavda-latitude site puts the sun behind/over the
camera's shoulder for most of the day rather than straight into the lens.
"""

from __future__ import annotations

Vec3 = tuple[float, float, float]


def camera_pose_for_bounds(
    lo: Vec3,
    hi: Vec3,
    *,
    fov_deg: float = 50.0,
    margin: float = 1.25,
    pitch: float = 0.45,
    min_distance: float = 25.0,
) -> tuple[Vec3, Vec3]:
    """Eye + target that frame the axis-aligned box `lo`..`hi`.

    `fov_deg` is the camera's *horizontal* field of view; the distance is solved from
    it so the box's widest horizontal span fits, then multiplied by `margin` for air
    around the edges. `pitch` is the eye's height above the target as a fraction of
    that distance (0.45 -> ~24 deg down, a survey view rather than a plan view).

    Returns `(eye, target)` in stage metres. The target is the box centre in X/Y and
    sits at the box's *top* in Z, which is the panel plane on a farm stage — aiming at
    the vertical centre of a box that includes terrain buries the look-at point in the
    ground.
    """
    import math

    cx = (lo[0] + hi[0]) / 2.0
    cy = (lo[1] + hi[1]) / 2.0
    span = max(hi[0] - lo[0], hi[1] - lo[1], 0.0)

    # Solve the distance that fits `span` across the horizontal FOV. Guard the
    # degenerate ends: a single-prim bounds has span 0, and a >=180 deg FOV has no
    # finite solution.
    half_fov = math.radians(max(min(fov_deg, 170.0), 1.0)) / 2.0
    distance = max((span / 2.0) / math.tan(half_fov) * margin, min_distance)

    target = (cx, cy, hi[2])
    # South-west of the target, on the diagonal, so both axes of a long thin plant
    # read as depth instead of a wall.
    offset = distance / math.sqrt(2.0)
    eye = (cx - offset, cy - offset, hi[2] + distance * pitch)
    return eye, target


def bounds_of(points: list[Vec3]) -> tuple[Vec3, Vec3]:
    """Axis-aligned bounds of `points`. Raises on empty — an empty frame is a bug."""
    if not points:
        raise ValueError("no points to bound — nothing to frame")
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def sample_stride(count: int, max_samples: int) -> int:
    """Stride that keeps at most `max_samples` of `count` items.

    Reading a translate off every one of 117,264 panel prims to find a bounding box
    costs seconds for a number that a few thousand samples pin down just as well.
    ⚠ A sampled bound can be slightly INSIDE the true one (it can miss the extreme
    prim), which is why `camera_pose_for_bounds` carries a margin.
    """
    if count <= max_samples or max_samples <= 0:
        return 1
    return (count + max_samples - 1) // max_samples
