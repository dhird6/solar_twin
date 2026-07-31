"""The conditions reel's shot list and honesty labels — Isaac-free.

A showcase video is the easiest place in a project to overclaim, because a rendered
frame carries no provenance: a teleported drone looks autonomous and an emissive cell
looks thermal. These tests pin the labels, not the pictures — specifically that the
things this project has *measured* to be scripted or proxied cannot quietly lose their
caveat when someone edits the reel.
"""

from __future__ import annotations

import pytest

from solar_twin.world.conditions_reel import (
    CONDITIONS,
    build_chapters,
    condition,
    shot_keys,
)
from solar_twin.world.tour import BUILT, INFERRED, TODO

BOUNDS = (0.0, 0.0, 320.0, 647.0)  # block02's real extent


def test_every_condition_is_uniquely_named_and_resolvable():
    names = [c.name for c in CONDITIONS]
    assert len(names) == len(set(names))
    for n in names:
        assert condition(n).name == n
    with pytest.raises(KeyError):
        condition("monsoon")


def test_the_six_conditions_the_owner_asked_for_are_present():
    names = {c.name for c in CONDITIONS}
    assert {"day", "golden", "night", "cloud", "wind", "faults"} <= names


#: Mirrors `mdl_materials.SKIES`. Duplicated rather than imported because
#: `mdl_materials` imports `pxr` at module scope (it is the Isaac-bound look layer),
#: and this suite must run without Isaac. The names are a stable external contract —
#: NVIDIA's procedural sky shaders — so the duplication cannot silently drift into
#: something wrong; a typo here fails the build, not this test.
_KNOWN_SKIES = {
    "ClearSky", "CumulusLight", "CumulusHeavy", "Cirrus",
    "Overcast", "NightSky", "ProceduralSky",
}


def test_each_condition_pins_a_real_instant_and_a_known_sky():
    for c in CONDITIONS:
        assert c.sun_timestamp.endswith("Z"), c.name
        assert c.sky in _KNOWN_SKIES, f"{c.name} names an unknown sky {c.sky!r}"


def test_farm_overrides_carry_sun_sky_and_realism():
    """The reel is only watchable with the realism layer on, and the sky/sun are baked
    at build time — so a condition that forgot any of the three would render the flat
    look or the wrong time of day with no error."""
    for c in CONDITIONS:
        ov = c.farm_overrides()
        assert ov["sun"]["timestamp"] == c.sun_timestamp
        assert ov["sky"]["kind"] == c.sky
        assert ov["realism"]["enabled"] is True


def test_condition_overrides_merge_rather_than_replace():
    """`night` overrides the tracker limit AND needs its timestamp; a shallow update
    would drop one of them and stand the panels up at night."""
    night = condition("night")
    ov = night.farm_overrides()
    assert ov["sun"]["tracker_max_rotation_deg"] == 0.0
    assert ov["sun"]["timestamp"] == night.sun_timestamp


def test_faults_condition_actually_seeds_faults():
    ov = condition("faults").farm_overrides()
    assert ov["faults"]["rate"] > 0.0
    assert set(ov["faults"]["states"]) == {"hotspot", "soiled"}


# --------------------------------------------------------------------------- #
# The labels — the reason this module exists
# --------------------------------------------------------------------------- #


def test_the_scripted_fleet_caveat_appears_wherever_the_fleet_does():
    """⭐ The claim that keeps this video honest. A drone gliding down a row reads as
    autonomous; it is teleported (`control/kinematic.py`). Any chapter mentioning the
    drone or bot must say so."""
    for c in CONDITIONS:
        mentions_fleet = any(
            w in (c.subtitle + " " + " ".join(i.label for i in c.items)).lower()
            for w in ("drone", "bot", "fleet")
        )
        if not mentions_fleet:
            continue
        assert any(
            i.status == TODO and "SCRIPTED" in i.detail.upper() for i in c.items
        ), f"{c.name} shows the fleet without the scripted-motion caveat"


def test_thermal_is_never_presented_as_real():
    """Isaac renders no true IR. Any chapter that talks about hot cells or thermal must
    carry the emissive-proxy label — this is the claim most likely to be believed."""
    for c in CONDITIONS:
        text = (c.subtitle + " " + " ".join(i.label + i.detail for i in c.items)).lower()
        if "thermal" in text or "hot cell" in text or "hotspot" in text:
            assert any(
                "proxy" in i.detail.lower() and i.status == INFERRED for i in c.items
            ), f"{c.name} mentions thermal without the proxy caveat"


def test_turbine_geometry_is_labelled_inferred_not_built():
    """Positions are real and surveyed; hub height, blade length and rpm are our
    assumption. Calling the whole turbine BUILT would launder the invented half."""
    wind = condition("wind")
    turbine = [i for i in wind.items if "turbine" in i.label.lower()]
    assert turbine, "the wind chapter must say something about the turbines"
    assert all(i.status == INFERRED for i in turbine)
    assert any("assumption" in i.detail.lower() for i in turbine)


def test_the_blade_shadow_is_labelled_as_the_measured_dead_end():
    """Session 17 measured that a 4 m blade at 780 m casts no umbra at all. A reel that
    showed spinning blades and implied a shading stimulus would re-sell a refuted
    claim."""
    wind = condition("wind")
    assert any(
        i.status == TODO and "penumbra" in i.detail.lower() for i in wind.items
    ), "the wind chapter must carry the penumbra refutation"


def test_no_condition_claims_atmospheric_dust():
    """The owner asked for 'dust'. Soiling on the glass is real; atmospheric obscuration
    is not built. The reel must not blur the two."""
    for c in CONDITIONS:
        for i in c.items:
            text = (i.label + " " + i.detail).lower()
            if "dust" in text and i.status == BUILT:
                assert "glass" in text or "soil" in text or "film" in text, (
                    f"{c.name} claims BUILT dust without saying it is soiling on the "
                    "glass: {i}"
                )


# --------------------------------------------------------------------------- #
# Structure
# --------------------------------------------------------------------------- #


def test_chapters_open_with_a_title_and_close_with_what_is_missing():
    ch = build_chapters(BOUNDS)
    assert ch[0].keys == [], "the title card should cost no render"
    assert "KHAVDA" in ch[0].title
    last = ch[-1]
    assert last.keys == []
    assert "NOT REAL" in last.title.upper()
    # The closing card is the load-bearing one: it must name the big absences.
    detail = " ".join(i.label + i.detail for i in last.items).lower()
    for must in ("localization", "ros 2", "physics", "collider", "thermal"):
        assert must in detail, f"the closing card never mentions {must}"
    # ⚠ At most 7: measured in a smoke render that the 8th item ran off the bottom of a
    # 720p card, and an honesty list you cannot read is worse than a shorter one.
    assert len(last.items) <= 7, "the closing card will overflow at 720p"


def test_every_closing_item_is_a_todo():
    """It is the 'what is missing' card. A BUILT item there would read as a boast in the
    one place the video is supposed to be admitting things."""
    assert all(i.status == TODO for i in build_chapters(BOUNDS)[-1].items)


def test_one_chapter_per_condition_plus_two_cards():
    assert len(build_chapters(BOUNDS)) == len(CONDITIONS) + 2


def test_shot_keys_stay_inside_the_plot_they_are_given():
    """Derived from bounds, not hardcoded — so the same reel frames a 24-table subset
    and the full block. A shot list that only fitted the full plot would fly off the
    edge of the subset, which is the stage a physics build can actually afford."""
    for bounds in [BOUNDS, (0.0, 0.0, 60.0, 120.0)]:
        min_x, min_y, max_x, max_y = bounds
        span = max(max_x - min_x, max_y - min_y)
        keys = shot_keys(bounds, 12.0)
        assert len(keys) == 3
        for k in keys:
            assert min_x - span <= k.x <= max_x + span
            assert min_y - span <= k.y <= max_y + span
            assert k.z > 0.0
        # ...and it should end low, at module height, not still in the air.
        assert keys[-1].z < 5.0


def test_shot_seconds_are_split_across_the_moves():
    keys = shot_keys(BOUNDS, 12.0)
    assert sum(k.seconds for k in keys) == pytest.approx(12.0)


def test_the_layout_claim_comes_from_the_stage_not_a_constant():
    """⭐ Caught in a smoke render: a 60-table subset was captioned "273 tables / 30,016
    modules" — the reel telling the viewer it was 5x bigger than it was. The claim must
    track whatever stage is loaded, and must say so when it is a subset."""
    from solar_twin.world.conditions_reel import describe_extent

    full = describe_extent(30016, 273)
    assert "30,016" in full and "273" in full and "subset" not in full

    sub = describe_extent(6600, 60)
    assert "6,600" in sub and "60" in sub
    assert "subset" in sub.lower(), "a subset must be labelled as one"
    assert "30,016" not in sub, "a subset must not quote the full plot's module count"

    # And it must be substituted into the day chapter rather than left as a template.
    ch = build_chapters(BOUNDS, facts={"panels": 6600, "tables": 60})
    day = [c for c in ch if c.title == "MIDDAY"][0]
    layout = [i for i in day.items if i.label == "layout"][0]
    assert "{layout}" not in layout.detail
    assert "subset" in layout.detail.lower()


def test_unknown_extent_degrades_to_a_claim_free_string():
    """If nothing was counted, say nothing numeric — do not fall back to the full plot."""
    from solar_twin.world.conditions_reel import describe_extent

    txt = describe_extent(0, 0)
    assert "30,016" not in txt and "273" not in txt


def test_night_seeds_the_faults_its_caption_promises():
    """The night chapter's subtitle claims a hot cell is visible. A stage with no faults
    would make the reel caption something the frame does not contain."""
    ov = condition("night").farm_overrides()
    assert ov["faults"]["rate"] > 0.0
    assert "hotspot" in ov["faults"]["states"]
    # ...and the tracker override must survive alongside it.
    assert ov["sun"]["tracker_max_rotation_deg"] == 0.0


def test_detail_claim_chapters_are_shot_close():
    """A hotspot is one cell of one module. From the wide pass's establishing altitude it
    is a couple of pixels, so a chapter captioned "cell-level, localized" would be asking
    the viewer to take that on trust — caught in a smoke render."""
    from solar_twin.world.conditions_reel import close_keys

    assert condition("faults").close_up, "the fault chapter must be shot close"
    assert condition("night").close_up, "the night hot-cell claim needs a close pass"
    assert not condition("day").close_up, "the scale chapter should stay wide"

    keys = close_keys(BOUNDS, 10.0)
    assert keys, "a close pass needs keyframes"
    assert all(k.z < 6.0 for k in keys), "a close pass must be near module height"
    assert all(k.focal_mm >= 30.0 for k in keys), "a close pass needs a longer lens"
    assert sum(k.seconds for k in keys) == pytest.approx(10.0)


def test_close_chapters_actually_get_the_close_keys():
    """The flag has to reach the shot list, not just sit on the dataclass."""
    ch = build_chapters(BOUNDS)
    faults = [c for c in ch if c.title == "FAULTS"][0]
    day = [c for c in ch if c.title == "MIDDAY"][0]
    # Compared on PEAK altitude, not minimum: the wide pass descends to row level too
    # (2.6 m), so its minimum is not what distinguishes the two — its establishing
    # altitude is. The close pass never leaves module height at all.
    assert max(k.z for k in faults.keys) < max(k.z for k in day.keys) / 4.0, (
        "the fault chapter should never climb to the wide pass's establishing altitude"
    )
    assert len(faults.keys) < len(day.keys), "the close pass is a simpler, slower move"


def test_no_subtitle_hardcodes_the_full_plot_module_count():
    """The layout ITEM is stage-derived, but a subtitle claiming "30,016 modules" over a
    60-table render is the same overclaim in a different line — and it shipped once."""
    for c in CONDITIONS:
        assert "30,016" not in c.subtitle, f"{c.name}'s subtitle hardcodes a module count"
        assert "30016" not in c.subtitle, f"{c.name}'s subtitle hardcodes a module count"
