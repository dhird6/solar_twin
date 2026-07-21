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

    def complete(self, *, model: str, messages: list[dict], timeout: float) -> str:
        self.last_messages = messages
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
    perception = CosmosReasonPerception()
    assert perception.client is not None
    assert perception.base_url == "http://localhost:8000"
