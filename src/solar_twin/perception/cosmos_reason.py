"""Cosmos Reason Perception backend (Slice 0 skeleton).

Targets **NVIDIA Cosmos Reason** (the physical-AI VLM brain, §5 of the strategy
doc) served behind an OpenAI-compatible endpoint (NIM). Implementing the same
`Perception` interface as `ground_truth.py` means swapping this in is a
`mission.yaml` config flip, not an orchestration change (bible §2.4).

⚠ The endpoint and served-model-name below are **placeholders** — no Cosmos
Reason NIM is stood up on this box yet (see `docs/ENVIRONMENT.md`). Set the real
values via `mission.yaml`'s `perception_opts` (base_url / model / timeout);
verify the served-model-name against the actual NIM before relying on it. This
was deliberately NOT pointed at the local Qwen vLLM — Cosmos only.

The HTTP call is isolated behind a small `ChatClient` protocol so this module
imports and is fully unit-testable with a fake client — no network, no GPU, no
Isaac — on any machine (the "logic without the simulator" principle, §2.6,
applied to the VLM call instead of the sim). `_HttpChatClient` is the real
implementation; it only touches the network inside `.complete()`, never on
import, so nothing here requires the Spark's vLLM server to be running.

Frame encoding (turning a real camera frame into a VLM image payload) is
handled by `_frame_to_data_url`: a raw camera frame — the ``H x W x {3,4}``
uint8 ndarray that `SimRuntime.capture` / `Transport.capture` returns — is
converted to a PNG ``data:`` URL and attached as an OpenAI-style ``image_url``
content part. It fails soft: any missing codec / bad frame falls back to a
text-only prompt rather than crashing the mission, and all heavy imports
(numpy, an image codec) are lazy so importing this module still needs neither
them nor Isaac. Slice 0 tests exercise both paths (`frame=None` → text-only;
a small ndarray → image part).

Pure-python: no Isaac import.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from solar_twin.perception.base import (
    Diagnosis,
    Frame,
    PanelContext,
    Perception,
    Verdict,
)
from solar_twin.schema.pv_module import PanelState, is_valid_state

# ⚠ verify — placeholders for a Cosmos Reason NIM (OpenAI-compatible). Override
# via mission.yaml perception_opts; do not treat these as confirmed values.
DEFAULT_BASE_URL = "http://localhost:8000"  # ⚠ real Cosmos Reason NIM endpoint TBD
DEFAULT_MODEL = "nvidia/cosmos-reason1-7b"  # ⚠ verify served-model-name on the NIM
DEFAULT_TIMEOUT_S = 30.0

#: A screen verdict is fail-safe: an unparseable/uncertain response escalates
#: (status="suspect") rather than silently waving the panel through.
_FAILSAFE_STATUS = "suspect"
_FAILSAFE_FAULT_TYPE = PanelState.UNKNOWN.value


class ChatClient(Protocol):
    """What CosmosReasonPerception needs from an OpenAI-compatible client.
    Swap in a fake for tests; `_HttpChatClient` is the real Spark-only impl."""

    def complete(self, *, model: str, messages: list[dict], timeout: float) -> str:
        """Return the assistant's raw text response for one chat completion."""
        ...


class _HttpChatClient:
    """Talks to a real OpenAI-compatible `/v1/chat/completions` endpoint (a
    Cosmos Reason NIM). Uses stdlib `urllib` only — no new dependency — and only
    imports/opens a socket when `.complete()` is actually called."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        self.base_url = base_url.rstrip("/")

    def complete(self, *, model: str, messages: list[dict], timeout: float) -> str:
        import urllib.request  # noqa: PLC0415 — lazy: no network on import

        payload = json.dumps(
            {"model": model, "messages": messages, "temperature": 0.0}
        ).encode()
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
        return body["choices"][0]["message"]["content"]


def _frame_to_data_url(frame: Frame) -> str | None:
    """Encode a raw camera frame as a PNG ``data:`` URL for an OpenAI-style
    ``image_url`` content part, or return None (caller sends text-only).

    Accepts the ``H x W x {3,4}`` uint8 ndarray that `Transport.capture`
    returns (RGB or RGBA — the alpha channel is dropped). Fails soft on a
    missing frame, an unexpected shape, or a missing image codec: it returns
    None so a flaky sensor / thin runtime degrades to a text prompt instead of
    crashing the mission. numpy and the codec are imported lazily, so importing
    this module never requires them (Slice 0 runs text-only)."""
    if frame is None:
        return None
    try:
        import io

        import numpy as np

        arr = np.asarray(frame)
        if arr.ndim != 3 or arr.shape[2] not in (3, 4):
            return None
        rgb = arr[..., :3]
        if rgb.dtype != np.uint8:
            rgb = np.clip(rgb, 0, 255).astype(np.uint8)

        buf = io.BytesIO()
        try:
            from PIL import Image  # noqa: PLC0415 — lazy: no codec needed on import

            Image.fromarray(rgb, mode="RGB").save(buf, format="PNG")
        except ImportError:
            import imageio.v2 as imageio  # noqa: PLC0415 — Isaac-runtime fallback

            imageio.imwrite(buf, rgb, format="png")

        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:  # noqa: BLE001 — vision is best-effort; degrade to text
        return None


def _parse_json_response(raw: str) -> dict[str, Any]:
    """VLMs often wrap JSON in prose or a code fence; extract the first
    ``{...}`` block. Falls back to ``{}`` (callers apply fail-safe defaults)
    rather than raising — a flaky VLM response must not crash the mission."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def _assess_prompt(context: PanelContext) -> str:
    return (
        "You are inspecting a solar panel image for visible faults "
        "(soiling, hotspots, cracks, string dropout, diode faults, shading).\n"
        f"Panel: {context.get('panel_id')}\n"
        f"Prior inspection history: {context.get('history') or 'none'}\n"
        "Is this panel clean or does it look suspect? Respond with ONLY this "
        'JSON: {"status": "clean"|"suspect", "confidence": <0-1>, "note": "<short reason>"}'
    )


def _diagnose_prompt(context: PanelContext) -> str:
    taxonomy = ", ".join(s.value for s in PanelState)
    return (
        "Diagnose the exact fault on this solar panel image. "
        f"Choose exactly one of: {taxonomy}.\n"
        f"Panel: {context.get('panel_id')}\n"
        f"Prior inspection history: {context.get('history') or 'none'}\n"
        'Respond with ONLY this JSON: {"fault_type": "<one of the taxonomy>", '
        '"confidence": <0-1>, "note": "<short reason>"}'
    )


@dataclass
class CosmosReasonPerception(Perception):
    """VLM-backed `Perception`. Swaps in for `GroundTruthPerception` with no
    orchestration change. Any HTTP/parse failure fails safe: `assess` escalates
    (never silently clears a panel) and `diagnose` reports `unknown`."""

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = DEFAULT_TIMEOUT_S
    client: ChatClient = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = _HttpChatClient(self.base_url)

    def _messages(self, prompt: str, frame: Frame) -> list[dict]:
        data_url = _frame_to_data_url(frame)
        if data_url is None:
            # No frame (or no codec) — text-only, as in Slice 0.
            return [{"role": "user", "content": prompt}]
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]

    def _complete(self, prompt: str, frame: Frame) -> str:
        try:
            return self.client.complete(
                model=self.model,
                messages=self._messages(prompt, frame),
                timeout=self.timeout,
            )
        except Exception:
            return "{}"  # fail safe below applies fail-safe defaults to `{}`

    def assess(self, frame: Frame, context: PanelContext) -> Verdict:
        raw = self._complete(_assess_prompt(context), frame)
        data = _parse_json_response(raw)
        status = data.get("status")
        if status not in ("clean", "suspect"):
            status = _FAILSAFE_STATUS
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        note = str(data.get("note") or raw)[:500]
        return Verdict(status=status, confidence=confidence, note=note)

    def diagnose(self, frame: Frame, context: PanelContext) -> Diagnosis:
        raw = self._complete(_diagnose_prompt(context), frame)
        data = _parse_json_response(raw)
        fault_type = data.get("fault_type")
        if not isinstance(fault_type, str) or not is_valid_state(fault_type):
            fault_type = _FAILSAFE_FAULT_TYPE
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        note = str(data.get("note") or raw)[:500]
        return Diagnosis(fault_type=fault_type, confidence=confidence, note=note)
