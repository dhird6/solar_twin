"""Where the robot THINKS it is — GNSS/RTK + IMU (pure, Isaac-free).

Chosen over visual SLAM on evidence, not preference. `tools/slam_feasibility.py`
measured the drone's nadir view of Khavda BLOCK-02 at 12 m AGL and found the scene is
**not** feature-poor — 380 ORB features per frame — but that **92.3% of frame-to-frame
matches are ambiguous**, leaving ~26 trustworthy links against the 50-100 a
visual-odometry front end needs. Every panel corner is geometrically identical to every
other at the same pitch, so a matcher cannot tell which row it is looking at. A feature
COUNT passes that scene; a distinctiveness measurement fails it.

Which is why real utility-PV inspection drones navigate on RTK GNSS, not vision. The
panel field gives you nothing to navigate by; the sky does.

## ⭐ The failure this module exists to quantify

Position error is not an abstract quality metric here. Khavda's modules sit at a
**1.148 m pitch along the torque tube**, so a robot that is wrong about its position
by more than ~half a pitch **writes its verdict onto the wrong panel**. A perfect
perception model scoring 100% can still produce a useless inspection if the fix is
bad: the fault is real, the diagnosis is right, and the maintenance crew is sent to
the wrong module.

That reframes GNSS quality from a spec-sheet line into a KPI, and it is measurable
here because the twin knows the true panel positions:

    fix mode      typical sigma      sigma / 1.148 m pitch
    RTK fixed          0.02 m                 0.02
    RTK float          0.50 m                 0.44
    DGPS               1.00 m                 0.87
    single             3.00 m                 2.61

⚠ **These sigmas are published typical values for each fix class, not measurements of
any receiver we own.** They are a stated prior — the same footing as
`adaptive.EXPANSION` and `fleet_specs`' endurance derate. A real receiver's error is
also time-correlated (a wandering bias), not the independent draw modelled here, so
`misattribution_rate` is if anything OPTIMISTIC about how often consecutive panels are
mislabelled together.

## What this is not

⚠ No constellation geometry, no multipath, no ionosphere, no RTK baseline length, no
correction-stream latency. The one structural effect that IS modelled is **fix
degradation**, because that is what actually happens on a plant: an RTK fix drops to
float or single near structures or when corrections stall, and the interesting question
is what the mission does then.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from enum import Enum
from typing import Optional

Point = tuple[float, float, float]


class FixMode(Enum):
    """GNSS solution quality, best to worst."""

    RTK_FIXED = "rtk_fixed"
    RTK_FLOAT = "rtk_float"
    DGPS = "dgps"
    SINGLE = "single"
    NONE = "none"  # no fix at all — dead reckoning only


#: Typical horizontal 1-sigma error per fix class, metres. ⚠ Published typical values
#: for the CLASS, not a measurement of a receiver we hold — see the module header.
#: Vertical is taken as ~1.7x horizontal, the usual GNSS anisotropy (satellites are
#: only ever above you, so the vertical geometry is worse).
HORIZONTAL_SIGMA_M: dict[FixMode, float] = {
    FixMode.RTK_FIXED: 0.02,
    FixMode.RTK_FLOAT: 0.50,
    FixMode.DGPS: 1.00,
    FixMode.SINGLE: 3.00,
    FixMode.NONE: float("inf"),
}

VERTICAL_RATIO = 1.7


@dataclass(frozen=True)
class ImuSpec:
    """Dead-reckoning drift while there is no fix.

    ⚠ `drift_m_per_s` is a lumped rate, not an IMU error model — real drift grows
    super-linearly (position error integrates twice from accelerometer bias). Linear
    is therefore OPTIMISTIC for outages longer than a few seconds, and the docstring
    says so rather than the number pretending otherwise.
    """

    drift_m_per_s: float = 0.15


@dataclass(frozen=True)
class FixEstimate:
    """What the receiver reports, and how wrong it actually is."""

    believed: Point
    truth: Point
    mode: FixMode
    sigma_m: float

    @property
    def error_m(self) -> float:
        return math.dist(self.believed[:2], self.truth[:2])


def misattribution_rate(sigma_m: float, pitch_m: float, samples: int = 20000,
                        seed: int = 0) -> float:
    """Fraction of verdicts that would land on the WRONG panel.

    A robot at true panel centre `p` with a 2-D Gaussian position error attributes its
    verdict to whichever panel centre is nearest its BELIEVED position. Modules are on
    a 1-D pitch along the tube, so the verdict is misattributed whenever the along-tube
    error exceeds half a pitch.

    Analytic would be `erfc(pitch / (2*sqrt(2)*sigma))`, but this samples so the same
    function can later take a non-Gaussian or time-correlated error without changing
    its callers.
    """
    if pitch_m <= 0:
        raise ValueError("pitch_m must be positive")
    if not math.isfinite(sigma_m):
        return 1.0
    if sigma_m <= 0:
        return 0.0
    rng = random.Random(seed)
    half = pitch_m / 2.0
    wrong = sum(1 for _ in range(samples) if abs(rng.gauss(0.0, sigma_m)) > half)
    return wrong / samples


def misattribution_by_mode(pitch_m: float, **kw) -> dict[FixMode, float]:
    """`misattribution_rate` for every fix class, for a run record or a report."""
    return {m: misattribution_rate(s, pitch_m, **kw) for m, s in HORIZONTAL_SIGMA_M.items()}


class GnssReceiver:
    """A seeded GNSS/RTK receiver model.

    Deterministic in (seed, call sequence): two runs of the same mission see the same
    fixes, so a KPI measured with localisation error on is still reproducible.
    """

    def __init__(
        self,
        mode: FixMode = FixMode.RTK_FIXED,
        seed: int = 0,
        imu: Optional[ImuSpec] = None,
        degrade_probability: float = 0.0,
        degraded_mode: FixMode = FixMode.RTK_FLOAT,
    ) -> None:
        self.nominal_mode = mode
        self.degraded_mode = degraded_mode
        self.degrade_probability = float(degrade_probability)
        self.imu = imu or ImuSpec()
        self._rng = random.Random(seed)
        self._outage_s = 0.0
        #: Every fix produced, for the run record.
        self.history: list[FixEstimate] = []

    def fix(self, truth: Point, dt_s: float = 1.0) -> FixEstimate:
        """Believed position for a robot actually at `truth`.

        With no fix, error grows by the IMU drift rate for as long as the outage
        lasts — which is the whole reason a mission cares about degradation rather
        than about a single sigma.
        """
        mode = self.nominal_mode
        if self.degrade_probability > 0 and self._rng.random() < self.degrade_probability:
            mode = self.degraded_mode

        if mode is FixMode.NONE:
            self._outage_s += max(0.0, dt_s)
            sigma = self.imu.drift_m_per_s * self._outage_s
        else:
            self._outage_s = 0.0
            sigma = HORIZONTAL_SIGMA_M[mode]

        if not math.isfinite(sigma):
            sigma = 1e6
        believed = (
            truth[0] + self._rng.gauss(0.0, sigma),
            truth[1] + self._rng.gauss(0.0, sigma),
            truth[2] + self._rng.gauss(0.0, sigma * VERTICAL_RATIO),
        )
        est = FixEstimate(believed=believed, truth=truth, mode=mode, sigma_m=sigma)
        self.history.append(est)
        return est

    def summary(self) -> dict:
        """Run-record block. Empty history reports zeros rather than dividing by it."""
        if not self.history:
            return {"n_fixes": 0, "caveat": _CAVEAT}
        errs = [e.error_m for e in self.history]
        by_mode: dict[str, int] = {}
        for e in self.history:
            by_mode[e.mode.value] = by_mode.get(e.mode.value, 0) + 1
        return {
            "n_fixes": len(errs),
            "nominal_mode": self.nominal_mode.value,
            "fixes_by_mode": by_mode,
            "mean_error_m": round(sum(errs) / len(errs), 4),
            "max_error_m": round(max(errs), 4),
            "caveat": _CAVEAT,
        }


_CAVEAT = (
    "MODELLED GNSS: published typical sigmas per fix class, independent draws, no "
    "multipath/ionosphere/baseline effects. Real error is time-correlated, so "
    "consecutive-panel misattribution is UNDER-stated here."
)
