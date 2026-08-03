"""CosmosReasonPerception against a fake ChatClient — no network, no GPU, no
Isaac. Asserts prompt shape (taxonomy-constrained) and fail-safe parsing.
"""

from __future__ import annotations

import json

from solar_twin.perception.cosmos_reason import CosmosReasonPerception
from solar_twin.schema.pv_module import PanelState

CONTEXT = {"panel_id": "R01-C003", "history": []}


class FakeChatClient:
    """Records the last prompt sent and returns a canned response."""

    def __init__(self, response: str):
        self.response = response
        self.last_messages: list[dict] | None = None
        self.last_sampling: dict | None = None

    def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        timeout: float,
        sampling: dict | None = None,
    ) -> str:
        self.last_messages = messages
        self.last_sampling = sampling
        return self.response


def _perception(response: str) -> tuple[CosmosReasonPerception, FakeChatClient]:
    client = FakeChatClient(response)
    return CosmosReasonPerception(client=client), client


def test_assess_parses_clean_verdict():
    resp = json.dumps({"status": "clean", "confidence": 0.9, "note": "looks fine"})
    perception, client = _perception(resp)
    verdict = perception.assess(None, CONTEXT)
    assert verdict.status == "clean"
    assert verdict.confidence == 0.9
    assert verdict.note == "looks fine"
    assert not verdict.is_suspect
    # Prompt reached the client and named the panel.
    assert "R01-C003" in client.last_messages[0]["content"]


def test_assess_parses_suspect_verdict():
    resp = json.dumps({"status": "suspect", "confidence": 0.6, "note": "dust visible"})
    perception, _ = _perception(resp)
    verdict = perception.assess(None, CONTEXT)
    assert verdict.is_suspect


def test_assess_fails_safe_on_garbage_response():
    perception, _ = _perception("not json at all")
    verdict = perception.assess(None, CONTEXT)
    # Fail-safe: an unparseable response escalates, it never waves a panel through.
    assert verdict.status == "suspect"
    assert verdict.confidence == 0.0


def test_assess_extracts_json_from_prose_wrapper():
    resp = 'Sure! Here you go: {"status": "clean", "confidence": 0.8, "note": "ok"} thanks'
    perception, _ = _perception(resp)
    verdict = perception.assess(None, CONTEXT)
    assert verdict.status == "clean"
    assert verdict.confidence == 0.8


def test_diagnose_parses_taxonomy_fault_type():
    resp = json.dumps({"fault_type": "hotspot", "confidence": 0.75, "note": "hot cell"})
    perception, client = _perception(resp)
    diagnosis = perception.diagnose(None, CONTEXT)
    assert diagnosis.fault_type == PanelState.HOTSPOT.value
    assert diagnosis.confidence == 0.75
    # The taxonomy is spelled out in the prompt so the model can't invent states.
    assert "hotspot" in client.last_messages[0]["content"]


def test_diagnose_fails_safe_on_invalid_fault_type():
    resp = json.dumps({"fault_type": "on_fire", "confidence": 0.99, "note": "??"})
    perception, _ = _perception(resp)
    diagnosis = perception.diagnose(None, CONTEXT)
    assert diagnosis.fault_type == PanelState.UNKNOWN.value


def test_defaults_to_http_client_when_none_given():
    # Constructing without a client must not touch the network (lazy import
    # inside _HttpChatClient.complete, never in __post_init__/__init__).
    from solar_twin.perception.cosmos_reason import DEFAULT_BASE_URL

    perception = CosmosReasonPerception()
    assert perception.client is not None
    assert perception.base_url == DEFAULT_BASE_URL  # placeholder Cosmos NIM endpoint


def test_config_driven_overrides():
    # base_url / model / timeout must be overridable (config-driven, not hardcoded).
    p = CosmosReasonPerception(
        base_url="http://cosmos-nim:9000", model="cosmos-reason-x", timeout=5.0
    )
    assert p.base_url == "http://cosmos-nim:9000"
    assert p.model == "cosmos-reason-x"
    assert p.timeout == 5.0


# --- frame encoding (the sim-native camera path) ------------------------- #


def test_no_frame_sends_text_only_message():
    # frame=None must keep the message a plain text string (Slice 0 behavior).
    resp = json.dumps({"status": "clean", "confidence": 1.0, "note": "ok"})
    perception, client = _perception(resp)
    perception.assess(None, CONTEXT)
    assert isinstance(client.last_messages[0]["content"], str)


def test_rgba_frame_is_attached_as_png_image_url():
    import numpy as np

    frame = np.zeros((4, 4, 4), dtype=np.uint8)  # H x W x 4 (RGBA), like capture()
    frame[..., 3] = 255
    resp = json.dumps({"status": "suspect", "confidence": 0.7, "note": "dust"})
    perception, client = _perception(resp)
    perception.assess(frame, CONTEXT)

    content = client.last_messages[0]["content"]
    assert isinstance(content, list)
    kinds = {part["type"] for part in content}
    assert kinds == {"text", "image_url"}
    text_part = next(p for p in content if p["type"] == "text")
    assert "R01-C003" in text_part["text"]  # prompt still carries the context
    image_part = next(p for p in content if p["type"] == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_rgb_frame_without_alpha_is_also_encoded():
    import numpy as np

    frame = np.zeros((4, 4, 3), dtype=np.uint8)  # H x W x 3 (RGB)
    perception, client = _perception(json.dumps({"fault_type": "hotspot", "confidence": 0.8, "note": "x"}))
    perception.diagnose(frame, CONTEXT)
    assert isinstance(client.last_messages[0]["content"], list)


def test_malformed_frame_falls_back_to_text_only():
    import numpy as np

    frame = np.zeros((4, 4), dtype=np.uint8)  # 2-D: not a valid image frame
    perception, client = _perception(json.dumps({"status": "clean", "confidence": 1.0, "note": "ok"}))
    perception.assess(frame, CONTEXT)
    # Degrades to a text prompt rather than raising.
    assert isinstance(client.last_messages[0]["content"], str)


# --------------------------------------------------------------------------- #
# Sampling / provenance (the KPI-honesty seam — see DEFAULT_SAMPLING)
# --------------------------------------------------------------------------- #


def test_greedy_sampling_is_sent_by_default():
    """A KPI is only reproducible if the decoding config is pinned, so the
    request must carry it — measured: greedy selection, not `temperature`, is
    what makes serial responses repeatable on this build."""
    perception, client = _perception(json.dumps({"status": "clean", "confidence": 1.0, "note": "ok"}))
    perception.assess(None, CONTEXT)
    assert client.last_sampling == {
        "temperature": 0.0,
        "top_p": 1.0,
        "top_k": 1,
        "seed": 0,
    }


def test_sampling_overrides_merge_onto_defaults():
    """mission.yaml may re-seed without silently dropping greedy decoding."""
    client = FakeChatClient(json.dumps({"status": "clean", "confidence": 1.0, "note": "ok"}))
    perception = CosmosReasonPerception(client=client, sampling={"seed": 7})
    perception.assess(None, CONTEXT)
    assert client.last_sampling["seed"] == 7
    assert client.last_sampling["top_k"] == 1  # not lost by the override


def test_provenance_reports_model_and_sampling():
    perception, _ = _perception("{}")
    prov = perception.provenance()
    assert prov["kind"] == "cosmos_reason"
    assert prov["model"] == perception.model
    assert prov["sampling"]["top_k"] == 1
    # The batching caveat travels with the number, not just in a doc.
    assert "batching" in prov["determinism"]


# --------------------------------------------------------------------------- #
# Response parsing — the shapes a VLM actually emits
# --------------------------------------------------------------------------- #

#: The verbatim response that cost a KPI point. From `runs/20260729T112424`
#: (repeat 3, panel R258-C004): the model opened a ```json fence, wrote the
#: object, and closed the FENCE without closing the BRACE. The old parser found
#: no `}`, returned `{}`, the fail-safe mapped it to `unknown`, and because
#: `unknown != healthy` that scored as a FALSE FAULT — moving KPI-03 from 0.00 to
#: 0.053 on 19 healthy panels. The model had said "healthy" with confidence 1.0.
_REAL_UNCLOSED_RESPONSE = (
    '```json\n{"fault_type": "healthy", "confidence": 1.0, "note": "The image '
    "shows a section of a solar panel array with uniform blue cells arranged in a "
    "grid pattern. There are no visible signs of defects such as dirt, cracks, or "
    "overheating spots. The surface appears clean and consistent, indicating that "
    'the panel is in good condition without any immediate issues."\n```'
)


def test_the_real_unclosed_brace_response_is_recovered_not_discarded():
    """A parse failure must never be able to manufacture a fault verdict."""
    perception, _ = _perception(_REAL_UNCLOSED_RESPONSE)
    diagnosis = perception.diagnose(None, CONTEXT)
    assert diagnosis.fault_type == PanelState.HEALTHY.value  # was UNKNOWN → false fault
    assert diagnosis.confidence == 1.0
    assert "no visible signs of defects" in diagnosis.note


def test_parser_handles_the_shapes_models_actually_emit():
    from solar_twin.perception.cosmos_reason import _parse_json_response as parse

    assert parse('{"fault_type": "soiled"}') == {"fault_type": "soiled"}
    assert parse('```json\n{"fault_type": "soiled"}\n```') == {"fault_type": "soiled"}
    assert parse('Sure: {"status": "clean", "confidence": 0.9} ok') == {
        "status": "clean",
        "confidence": 0.9,
    }
    # Brace never closed.
    assert parse('{"fault_type": "healthy", "confidence": 1.0') == {
        "fault_type": "healthy",
        "confidence": 1.0,
    }
    # Cut off mid-string: keep the fields that did arrive.
    assert parse('{"fault_type": "healthy", "note": "the panel is')["fault_type"] == "healthy"


def test_parser_still_fails_closed_on_genuine_garbage():
    """Recovery must not become invention: with no object at all, the fail-safe
    still applies (assess escalates, diagnose reports unknown)."""
    from solar_twin.perception.cosmos_reason import _parse_json_response as parse

    assert parse("no json at all") == {}
    assert parse("") == {}
    assert parse(None) == {}  # type: ignore[arg-type]
    assert _perception("no json at all")[0].assess(None, CONTEXT).status == "suspect"
    assert (
        _perception("no json at all")[0].diagnose(None, CONTEXT).fault_type
        == PanelState.UNKNOWN.value
    )


# --------------------------------------------------------------- prompt version
# A KPI is produced by the prompt as much as by the decoding config. `sampling` was
# already recorded; the prompt was not, so changing a taxonomy definition made
# every earlier number non-comparable with no trace of why.


def test_provenance_records_the_prompt_version():
    perception = CosmosReasonPerception(client=FakeChatClient("{}"))
    prov = perception.provenance()
    assert prov["prompt_version"]
    from solar_twin.perception.cosmos_reason import PROMPT_VERSION

    assert prov["prompt_version"] == PROMPT_VERSION


# ------------------------------------------- the module-boundary fix (measured)
# Measured on SC-01 with prompt v1: the model called 3 of 33 healthy panels
# `soiled`, and its own notes echoed the taxonomy's own wording back nearly
# verbatim ("opaque tan or brown patch ... along the lower edge") on panels with
# nothing on them. In a desert plant the sand below and beside a module is in
# frame at exactly that edge, so a true-in-general prior was manufacturing false
# positives out of the ground.


def test_the_soiled_definition_keeps_its_load_bearing_location_cue():
    """Counter-intuitive, and measured. The "lower edge" cue *looks* like the
    culprit — the model echoed it back on healthy panels — so prompt v2 removed it.
    That cost the whole class: four panels injected `soiled` and detected
    `soiled/soiled/soiled` under v1 came back `hotspot/hotspot/hotspot` under v2
    (`SC-01`, 40 panels, N=3). The cue costs ~2 false alarms and buys ~5 correct
    soiled calls, so it stays until something measurably better replaces it.
    """
    from solar_twin.perception.cosmos_reason import _STATE_DEFINITIONS

    assert "lower edge" in _STATE_DEFINITIONS[PanelState.SOILED].lower()


def test_the_prompt_does_not_tell_the_model_to_ignore_ground():
    """The instruction that looked obviously right and measurably was not.

    Telling the model "bare ground/sand around the module is never a fault"
    suppressed the `soiled` class — sandy discoloration at the panel edge IS what
    soiling looks like — and four correctly-detected soiled panels came back
    `hotspot` (`SC-01`, N=3; detection_rate 0.900 -> 0.800-0.850). Re-adding it
    only makes sense once capture is cropped to the module's bounding box, so this
    guards against someone reintroducing the reasonable-sounding version.
    """
    client = FakeChatClient(
        json.dumps({"fault_type": "healthy", "confidence": 1.0, "note": "n"})
    )
    perception = CosmosReasonPerception(client=client)
    perception.diagnose(None, CONTEXT)
    sent = json.dumps(client.last_messages).lower()
    assert "never a fault" not in sent
    assert "module's own boundary" not in sent


# ------------------------------------------------------- framing (the real fix)
# Measured 2026-07-29: every false alarm on `SC-01`, across three prompt versions
# and nine repeats, sat beside a panel that really was faulted, and no
# clean-neighbourhood panel produced one (`kpi/confound.py`). The neighbouring
# module is in the confirm frame. That is a framing defect, not a prompt one.
#
# ⚠ These test the mechanism, NOT that it improves a KPI. The vLLM server went down
# before that could be measured, so the default stays 1.0 (no crop).


def test_the_default_shows_the_whole_frame():
    """Every KPI on record was measured with the full frame. A default that cropped
    would silently make all of them non-comparable."""
    assert CosmosReasonPerception(client=FakeChatClient("{}")).crop_fraction == 1.0


def test_provenance_names_the_crop():
    prov = CosmosReasonPerception(client=FakeChatClient("{}"), crop_fraction=0.5).provenance()
    assert prov["crop_fraction"] == 0.5


def test_a_full_crop_is_an_exact_no_op():
    from solar_twin.perception.cosmos_reason import centre_crop

    np = __import__("pytest").importorskip("numpy")
    frame = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)
    assert centre_crop(frame, 1.0) is frame


def test_a_crop_keeps_the_centre():
    import pytest as _pytest

    np = _pytest.importorskip("numpy")
    from solar_twin.perception.cosmos_reason import centre_crop

    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    frame[4:6, 4:6] = 255  # a 2x2 mark dead centre
    out = centre_crop(frame, 0.4)
    assert out.shape == (4, 4, 3)
    assert out.max() == 255  # the centre survived
    # ...and the periphery, where the neighbouring module sits, is gone.
    assert out.size < frame.size


def test_a_crop_never_produces_an_empty_frame():
    """An empty array would encode to nothing and silently downgrade the request to
    a text-only prompt — a verdict with no image, scored as if it had one."""
    import pytest as _pytest

    np = _pytest.importorskip("numpy")
    from solar_twin.perception.cosmos_reason import centre_crop

    tiny = np.zeros((2, 2, 3), dtype=np.uint8)
    out = centre_crop(tiny, 0.01)
    assert out.shape[0] >= 1 and out.shape[1] >= 1


def test_a_nonsense_fraction_is_refused():
    from solar_twin.perception.cosmos_reason import centre_crop

    for bad in (0.0, -0.5, 1.5):
        try:
            centre_crop([[1]], bad)
        except ValueError:
            continue
        raise AssertionError(f"fraction {bad} should have been refused")


def test_cropping_none_stays_none():
    from solar_twin.perception.cosmos_reason import centre_crop

    assert centre_crop(None, 0.5) is None


def test_the_cropped_frame_is_what_gets_sent():
    """The crop must happen before encoding, or `screen_frame_sha` would describe
    pixels the model never saw."""
    import pytest as _pytest

    np = _pytest.importorskip("numpy")
    client = FakeChatClient(
        json.dumps({"fault_type": "healthy", "confidence": 1.0, "note": "n"})
    )
    big = np.zeros((64, 64, 3), dtype=np.uint8)
    full = CosmosReasonPerception(client=FakeChatClient(client.response))
    cropped = CosmosReasonPerception(client=client, crop_fraction=0.25)
    full.diagnose(big, CONTEXT)
    cropped.diagnose(big, CONTEXT)
    # A smaller image encodes to a shorter data URL — crude, but it proves the
    # crop reached the wire rather than being computed and discarded.
    a = json.dumps(full.client.last_messages)
    b = json.dumps(client.last_messages)
    assert len(b) < len(a)
