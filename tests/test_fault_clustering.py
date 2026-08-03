"""Clustered fault injection, and the identity guarantee on the uniform path.

Why this exists: measured 2026-08-03, the uniform path produces faults that are
spatially INDEPENDENT — 40.0% of faulted panels had a faulted neighbour within 2,
against 38.4% expected by chance. Adaptive expansion assumes clustering, so without
this its benefit would have measured ~0 for the same reason `SC-05`'s false-fault
rate did: no stimulus.
"""

from __future__ import annotations

import copy

import pytest

from solar_twin.world.layout import FarmLayout
from solar_twin.schema.pv_module import parse_panel_id

CFG = {
    "seed": 20260727,
    "grid": {"rows": 40, "cols": 40, "row_pitch": 5.0, "col_pitch": 2.3},
    "panel": {"width": 2.3, "length": 1.1, "height": 0.035, "mount_height": 1.5,
              "tilt_deg": 20.0},
    "faults": {"rate": 0.05, "states": ["soiled", "hotspot"]},
}


def _layout(**faults):
    cfg = copy.deepcopy(CFG)
    cfg["faults"].update(faults)
    return FarmLayout(cfg)


def _neighbour_rate(faults) -> float:
    rc = {parse_panel_id(p) for p in faults}
    if not rc:
        return 0.0
    def nb(r, c):
        return {(r + dr, c + dc) for dr in range(-2, 3) for dc in range(-2, 3)} - {(r, c)}
    return sum(1 for (r, c) in rc if nb(r, c) & rc) / len(rc)


# --------------------------------------------------------------------------- #
# ⭐ The identity guarantee. Every recorded KPI was measured on uniform faults.
# --------------------------------------------------------------------------- #


def test_clustering_zero_is_byte_identical_to_no_clustering_key():
    """Not 'statistically similar' — the SAME panels with the SAME states.

    Even drawing one extra number from the shared RNG would move which panels are
    faulted and silently invalidate every archived run.
    """
    assert _layout().seeded_faults() == _layout(clustering=0.0).seeded_faults()


def _chance_neighbour_rate(layout, faults) -> float:
    """Neighbour rate expected if faults were placed independently.

    ⚠ An ABSOLUTE threshold is meaningless here and an earlier version of this test
    used one and failed: at a 5% fault rate a 5x5 neighbourhood is expected to
    contain another fault 1-(0.95)^24 = 71% of the time BY CHANCE. Clustering is only
    visible as an excess over that baseline, which is why the original measurement
    (40.0% observed vs 38.4% chance -> not clustered) was reported as a pair.
    """
    p = len(faults) / layout.n_panels
    return 1.0 - (1.0 - p) ** 24


def test_the_default_is_uniform():
    """The default must produce faults indistinguishable from independent placement."""
    assert "clustering" not in CFG["faults"]
    layout = _layout()
    faults = layout.seeded_faults()
    observed = _neighbour_rate(faults)
    chance = _chance_neighbour_rate(layout, faults)
    assert observed < chance * 1.25, (
        f"default path shows {observed:.1%} against {chance:.1%} by chance — "
        "it is clustering when it should not"
    )


# --------------------------------------------------------------------------- #
# Clustering actually clusters.
# --------------------------------------------------------------------------- #


def test_clustering_raises_the_neighbour_rate_well_above_chance():
    """Measured against CHANCE, not against an absolute — see `_chance_neighbour_rate`."""
    layout = _layout(clustering=0.9, rate=0.01)
    faults = layout.seeded_faults()
    observed = _neighbour_rate(faults)
    chance = _chance_neighbour_rate(layout, faults)
    assert observed > chance * 2.0, (
        f"clustered run shows {observed:.1%} against {chance:.1%} by chance — "
        "that is not clustering"
    )


def test_the_total_fault_count_is_preserved():
    """⭐ Clustering must not silently change the denominator of every rate."""
    n = len(_layout().seeded_faults())
    for c in (0.25, 0.5, 0.8, 1.0):
        assert len(_layout(clustering=c).seeded_faults()) == n, f"count moved at {c}"


def test_more_clustering_means_more_clustering():
    low = _neighbour_rate(_layout(clustering=0.2).seeded_faults())
    high = _neighbour_rate(_layout(clustering=1.0).seeded_faults())
    assert high > low


def test_a_bigger_radius_spreads_a_patch_wider():
    tight = _layout(clustering=1.0, cluster_radius=1).seeded_faults()
    wide = _layout(clustering=1.0, cluster_radius=4).seeded_faults()
    assert len(tight) == len(wide)  # same count, different shape
    # A tight patch packs neighbours more densely than a wide one.
    assert _neighbour_rate(tight) >= _neighbour_rate(wide) - 0.05


def test_clustered_faults_are_deterministic():
    assert (
        _layout(clustering=0.8).seeded_faults()
        == _layout(clustering=0.8).seeded_faults()
    )


def test_a_patch_carries_one_state_not_a_lucky_dip():
    """A soiling drift is soiling throughout — mixing states within a patch would
    make the clustering physically meaningless."""
    faults = _layout(clustering=1.0, cluster_radius=2, rate=0.02).seeded_faults()
    by_rc = {parse_panel_id(p): s for p, s in faults.items()}
    same = tot = 0
    for (r, c), st in by_rc.items():
        for d in ((0, 1), (1, 0)):
            other = by_rc.get((r + d[0], c + d[1]))
            if other is not None:
                tot += 1
                same += other is st
    if tot:
        assert same / tot > 0.5, "adjacent faults disagree more often than not"


def test_faults_never_exceed_the_panel_count():
    layout = _layout(clustering=1.0, rate=2.0)
    assert len(layout.seeded_faults()) <= layout.n_panels


def test_zero_rate_still_yields_nothing_when_clustered():
    assert _layout(clustering=1.0, rate=0.0).seeded_faults() == {}
