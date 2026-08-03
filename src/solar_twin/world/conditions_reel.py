"""The conditions reel — the twin under day, golden hour, night, cloud, wind and
faults, with every shot labelled for what is real (pure python, no Isaac).

## What this is, and why it is labelled

A "showcase everything we can do" video is the easiest place in a project to
overclaim, because a rendered frame carries no provenance: a drone gliding down a row
looks autonomous whether it is flying or being teleported, and an orange cell looks
thermal whether it is radiometry or an emissive material. So every chapter here
carries a checklist using `tour.py`'s three-way status — **BUILT** (real, and here is
the number), **INFERRED** (in the picture, looks real, we invented it), **TODO** (not
modelled) — and the reel ends on what is still missing.

That is not a disclaimer bolted on. It is the difference between a demo someone can
act on and one that has to be walked back later.

## Why each lighting condition needs its own stage

`sun.timestamp` and the sky are resolved at **build** time: the sun vector sets the
`DistantLight` rotation, the tracker rotation of all 273 tables, and the procedural
sky shader's parameters. So "the same plant at night" is a different USD, not a
different camera — which is also why the trackers are correctly stowed flat at night
and pinned at their limit at golden hour rather than being posed by hand.

`CONDITIONS` is therefore a build matrix as well as a shot list.

## Honest inventory of what the reel shows

REAL, and the reel says so:
  * three procedural HDR skies driven by NOAA solar position for the real site
  * tracker angles that follow the sun, including the 60 deg mechanical limit
  * turbine blades turning, casting honestly ray-traced shadows
  * emissive hotspot cells and a dust film over the glass
  * real vendor-CAD layout on a real DEM

PROXY or absent, and the reel says that too:
  * **drone and ground-bot motion is scripted** — they are teleported along
    waypoints, they do not fly, localize or navigate (`control/kinematic.py`)
  * **thermal is an emissive proxy** — Isaac renders no true IR
  * **no dust storm / reduced visibility exists.** Deliberately not faked: the
    "dust" in this reel is soiling ON THE GLASS, which is real, not atmospheric
    obscuration, which is not built.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from solar_twin.world.tour import BUILT, INFERRED, TODO, Chapter, Item


@dataclass(frozen=True)
class Condition:
    """One lighting/weather condition: how to build its stage, and how to shoot it.

    `sun_timestamp` and `sky` go into `farm_overrides`; everything else is shot
    direction. A condition with `tracker_max_rotation_deg` set overrides the tracker
    limit — used for the stowed-flat night shot, which is a real operating state.
    """

    name: str
    title: str
    subtitle: str
    #: ISO-8601 UTC. Drives sun angle, tracker rotation AND the sky shader.
    sun_timestamp: str
    #: A `mdl_materials.SKIES` name.
    sky: str = "ClearSky"
    #: Extra `farm_overrides` merged into the build (faults, tracker limit, ...).
    overrides: dict = field(default_factory=dict)
    items: tuple = ()
    seconds: float = 11.0
    #: Shoot tight instead of wide. The wide pass is right for "look at the scale of
    #: this"; it is wrong for a chapter whose whole claim is a cell-level defect —
    #: measured in a smoke render, the fault chapters captioned hotspots that were a
    #: few pixels across and effectively invisible.
    close_up: bool = False

    def farm_overrides(self) -> dict:
        """The `farm_overrides` block that builds this condition's stage."""
        ov = {
            "sun": {"timestamp": self.sun_timestamp},
            "sky": {"kind": self.sky, "procedural": True},
            "realism": {"enabled": True},
        }
        for key, value in self.overrides.items():
            if isinstance(value, dict) and isinstance(ov.get(key), dict):
                ov[key] = {**ov[key], **value}
            else:
                ov[key] = value
        return ov


#: Shared caveat, on every chapter that shows the fleet or a fault. Repeated on
#: purpose: a viewer who joins mid-video must still see it.
_SCRIPTED_FLEET = Item(
    "drone / bot motion",
    TODO,
    "SCRIPTED — teleported along waypoints. No flight dynamics, no localization, "
    "no navigation.",
)
_THERMAL_PROXY = Item(
    "thermal signature", INFERRED, "emissive material proxy — Isaac renders no true IR"
)


#: Sun timestamps are real instants at 24.0915 N, 69.4205 E, and the elevations in
#: the `detail` strings are what `world/solar.py` computes for them — so a viewer can
#: check the claim rather than take it.
CONDITIONS: tuple[Condition, ...] = (
    Condition(
        name="day",
        title="MIDDAY",
        subtitle="the plant as it works — every table tracking the real sun",
        sun_timestamp="2026-06-21T04:00:00Z",
        sky="ClearSky",
        items=(
            Item("solar position", BUILT, "NOAA ephemeris: elev 43.7 deg, azim 79.9 deg"),
            Item("tracker angle", BUILT, "45.9 deg, driven by that sun — not authored"),
            # ⚠ `{layout}` is substituted from the STAGE by `build_chapters`. It used to
            # read "273 tables / 30,016 modules" unconditionally, and a smoke render
            # over a 60-table subset captioned it with the full plot's numbers — an
            # overclaim in the one video built to prevent overclaiming.
            Item("layout", BUILT, "{layout}, from the vendor DWG"),
            Item("terrain", BUILT, "Copernicus GLO-30 DEM, 2.2 m relief"),
            Item("sky", BUILT, "procedural HDR sky (NVIDIA ClearSky), lights the scene"),
        ),
    ),
    Condition(
        name="golden",
        title="LOW SUN",
        subtitle="trackers pinned at their mechanical limit, rows shading each other",
        sun_timestamp="2026-06-21T01:20:00Z",
        sky="ClearSky",
        items=(
            Item("solar position", BUILT, "elev 8.6 deg, azim 68.1 deg — early morning"),
            Item(
                "tracker limit",
                BUILT,
                "ideal angle 80.7 deg, so a 60 deg HSAT PINS at its stop — real hardware "
                "behaviour",
            ),
            Item(
                "inter-row shading",
                BUILT,
                "the KPI-03 false-fault stimulus, produced by the plant's own geometry",
            ),
            Item("glass", BUILT, "OmniGlass cover sheet reflecting the low sun"),
        ),
    ),
    Condition(
        name="night",
        close_up=True,
        title="NIGHT",
        subtitle="trackers stowed flat — and a hot cell is visible when nothing else is",
        sun_timestamp="2026-06-20T19:30:00Z",
        sky="NightSky",
        # Faults seeded here too, and not as decoration: the subtitle promises "a hot
        # cell is visible when nothing else is", and a stage with no hot cells would
        # make the reel claim something the frame does not show. Caught in a smoke
        # render — the night shot was captioned for a fault it had not seeded.
        overrides={
            "sun": {"tracker_max_rotation_deg": 0.0},
            "faults": {"rate": 0.05, "states": ["hotspot"]},
        },
        items=(
            Item("sun below horizon", BUILT, "trackers return to stow (flat) as they do in life"),
            Item("sky", BUILT, "procedural NightSky — the only light on the array"),
            Item(
                "hot cells",
                BUILT,
                "emissive, so a thermal anomaly reads at night when reflectance tells "
                "you nothing",
            ),
            _THERMAL_PROXY,
        ),
    ),
    Condition(
        name="cloud",
        title="CLOUD",
        subtitle="diffuse light, soft shadows — the condition that hides defects",
        sun_timestamp="2026-06-21T04:00:00Z",
        sky="CumulusHeavy",
        items=(
            Item("sky", BUILT, "procedural CumulusHeavy — changes the LIGHT, not just the backdrop"),
            Item(
                "why it matters",
                BUILT,
                "diffuse light removes the shadows a soiling gradient shows up against",
            ),
            Item("rain / snow", TODO, "not modelled"),
        ),
    ),
    Condition(
        name="wind",
        title="WIND & TURBINES",
        subtitle="blades turning among the modules, and a camera that gets pushed",
        sun_timestamp="2026-06-21T01:20:00Z",
        sky="ClearSky",
        items=(
            Item("blade rotation", BUILT, "11.5 rpm, shadows ray-traced honestly"),
            Item(
                "turbine positions",
                INFERRED,
                "REAL surveyed coordinates (GatiShakti, EPSG:32642); hub/blade geometry "
                "is our assumption",
            ),
            Item(
                "wind on the camera",
                INFERRED,
                "quasi-static drag offset, ~0.2 m at 12 m/s — NOT flight dynamics",
            ),
            Item(
                "blade shadow as a stimulus",
                TODO,
                "MEASURED USELESS: a 4 m blade at 780 m has a 7.2 m penumbra, so it casts "
                "no hard shadow at all",
            ),
        ),
    ),
    Condition(
        name="faults",
        close_up=True,
        title="FAULTS",
        subtitle="what the fleet is sent out to find",
        sun_timestamp="2026-06-21T04:00:00Z",
        sky="ClearSky",
        overrides={"faults": {"rate": 0.04, "states": ["hotspot", "soiled"]}},
        items=(
            Item("hotspot", BUILT, "cell-level, localized — not a recoloured panel"),
            Item("soiling", BUILT, "translucent dust film over the glass, crosses cell borders"),
            Item(
                "detection",
                BUILT,
                "Cosmos Reason VLM on the real rendered frame: soiling flagged 0.984, "
                "hotspot 0.397",
            ),
            _THERMAL_PROXY,
            _SCRIPTED_FLEET,
        ),
    ),
)


def condition(name: str) -> Condition:
    for c in CONDITIONS:
        if c.name == name:
            return c
    raise KeyError(f"unknown condition {name!r}; known: {[c.name for c in CONDITIONS]}")


# --------------------------------------------------------------------------- #
# Camera
# --------------------------------------------------------------------------- #


def close_keys(bounds: tuple[float, float, float, float], seconds: float) -> list:
    """A low, slow pass along one row — for chapters whose claim is a DETAIL.

    A hotspot is one cell of one module. From the wide pass's establishing altitude it
    is a couple of pixels, so a chapter captioned "cell-level, localized" would be
    asking the viewer to take that on trust. This drops to module height and tracks
    along the rows so a defect is actually resolvable.
    """
    from solar_twin.world.flythrough import Key

    min_x, min_y, max_x, max_y = bounds
    cx = (min_x + max_x) / 2.0
    span_y = max_y - min_y
    half = seconds / 2.0
    return [
        Key(cx - 14.0, min_y + span_y * 0.18, 3.2, 80.0, 4.0, 35.0, half, "close in"),
        Key(cx - 6.0, min_y + span_y * 0.42, 2.4, 84.0, 2.0, 40.0, half, "along the row"),
    ]


def shot_keys(bounds: tuple[float, float, float, float], seconds: float) -> list:
    """A three-move pass over the array, sized to whatever plot is loaded.

    Derived from `bounds` rather than hardcoded so the same reel works on a 24-table
    subset and on the full block — the subset is what a physics-enabled build can
    afford, and a shot list that only framed the full plot would silently fly off the
    edge of it.
    """
    from solar_twin.world.flythrough import Key

    min_x, min_y, max_x, max_y = bounds
    cx, cy = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0
    span = max(max_x - min_x, max_y - min_y)
    third = seconds / 3.0
    return [
        # High establishing shot, looking down the long axis.
        Key(cx, min_y - span * 0.55, span * 0.42, 62.0, 0.0, 20.0, third, "establishing"),
        # Descend toward the rows.
        Key(cx - span * 0.10, min_y + span * 0.10, span * 0.10, 74.0, 8.0, 24.0, third, "descend"),
        # Low pass just above the module plane, where glass and racking read.
        Key(cx - span * 0.02, cy - span * 0.12, 2.6, 86.0, 3.0, 28.0, third, "row level"),
    ]


def describe_extent(n_panels: int, n_tables: int) -> str:
    """The layout claim, from what is actually on the stage.

    Exists so a subset build cannot be captioned with the full plot's numbers. A
    60-table render saying "273 tables / 30,016 modules" is not a rounding error, it is
    the reel telling the viewer it is 5x bigger than it is.
    """
    if not n_panels:
        return "layout loaded from the vendor DWG"
    part = "" if n_tables >= 273 else " (a subset of the 273-table block)"
    return f"{n_tables:,} tables / {n_panels:,} modules{part}"


def build_chapters(
    bounds: tuple[float, float, float, float],
    conditions: tuple[Condition, ...] = CONDITIONS,
    facts: dict | None = None,
) -> list[Chapter]:
    """Title card, one chapter per condition, then the honest closing card.

    `facts` carries what was counted off the stage — `{"panels": n, "tables": n}` — and
    is substituted into any item detail containing `{layout}`.
    """
    facts = facts or {}
    layout_txt = describe_extent(int(facts.get("panels", 0)), int(facts.get("tables", 0)))
    n_panels = int(facts.get("panels", 0))
    headline = f"{n_panels:,}-module" if n_panels else "utility-scale"

    chapters = [
        Chapter(
            title="KHAVDA",
            subtitle=f"a digital twin of a real {headline} solar block — under six conditions",
            items=(
                Item("site", BUILT, "Adani Khavda A10b BLOCK-02, vendor DWG, EPSG:32642"),
                Item("what follows", BUILT, "every shot labelled: BUILT / INFERRED / TODO"),
                Item("why labels", BUILT, "a rendered frame carries no provenance on its own"),
            ),
            seconds=5.0,
        )
    ]
    for c in conditions:
        chapters.append(
            Chapter(
                title=c.title,
                subtitle=c.subtitle,
                items=[
                    Item(i.label, i.status, i.detail.replace("{layout}", layout_txt))
                    for i in c.items
                ],
                keys=(close_keys if c.close_up else shot_keys)(bounds, c.seconds),
                seconds=c.seconds,
            )
        )
    chapters.append(
        Chapter(
            title="WHAT IS NOT REAL YET",
            subtitle="the same list the engineers work from",
            # ⚠ SEVEN items, not eight. Measured in a smoke render: the eighth ran off
            # the bottom of a 720p card, and a truncated honesty list is worse than a
            # short one — the item you cannot read is the one you are not admitting.
            # `true thermal` and `atmospheric dust` are merged into one sensing entry
            # rather than dropped.
            items=(
                Item(
                    "robot autonomy",
                    TODO,
                    "no localization, no SLAM, no costmap, no path planning — the fleet is "
                    "teleported",
                ),
                Item("flight dynamics in the mission", TODO, "PX4 flies only in a separate hover test"),
                Item("ROS 2 in the loop", TODO, "bridge is written and smoke-tested, not load-bearing"),
                Item("physics at plant scale", TODO, "0.07x realtime on 82k prims — subset only"),
                Item("colliders on hardware", TODO, "a drone can fly through a module today"),
                Item("energy model", TODO, "no yield / power simulation — pvlib is the plan"),
                Item(
                    "sensing proxies",
                    TODO,
                    "thermal is emissive, not IR; soiling on glass is real but atmospheric "
                    "dust is not built",
                ),
            ),
            seconds=9.0,
        )
    )
    return chapters
