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

Decoding is pinned for reproducibility (`DEFAULT_SAMPLING`) and reported through
`provenance()` so every KPI in a run record names the decoding config that
produced it. Measured on this box: serial requests are byte-repeatable, batched
ones are not — read that note before quoting a number as a constant.

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

#: Sampling parameters, chosen for REPRODUCIBILITY rather than variety, and
#: **measured on this box** (2026-07-28, vLLM serving `nvidia/cosmos-reason1-7b`)
#: rather than assumed:
#:
#: * `temperature: 0.0` alone was already repeatable **when requests are issued
#:   serially** — 15/15 byte-identical responses to one real camera frame. vLLM
#:   clamps 0.0 to 0.01 and logs the substitution, so the temperature field is
#:   not what makes it repeatable; greedy selection is.
#: * `top_k: 1` makes that argmax choice explicit (and `top_p: 1.0` a no-op), so
#:   a future server default cannot quietly reintroduce sampling. Both fields are
#:   accepted by this vLLM build; `top_k` is a vLLM extension to the OpenAI body.
#: * `seed` pins the per-request RNG. It is *not* sufficient on its own: with 4
#:   identical requests in flight CONCURRENTLY, the same frame returned 2×
#:   `soiled` and 2× `healthy`. Continuous batching changes the arithmetic, and
#:   no request-level parameter fixes that.
#:
#: So: the mission's serial screen→confirm loop is reproducible; a parallelised
#: fleet would not be. Do not quote a KPI from a batched run as a constant —
#: use `--repeat` and report the spread (`kpi/variance.py`).
DEFAULT_SAMPLING: dict[str, Any] = {
    "temperature": 0.0,
    "top_p": 1.0,
    "top_k": 1,
    "seed": 0,
}

#: A screen verdict is fail-safe: an unparseable/uncertain response escalates
#: (status="suspect") rather than silently waving the panel through.
_FAILSAFE_STATUS = "suspect"
_FAILSAFE_FAULT_TYPE = PanelState.UNKNOWN.value


class ChatClient(Protocol):
    """What CosmosReasonPerception needs from an OpenAI-compatible client.
    Swap in a fake for tests; `_HttpChatClient` is the real Spark-only impl."""

    def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        timeout: float,
        sampling: dict[str, Any] | None = None,
    ) -> str:
        """Return the assistant's raw text response for one chat completion.

        `sampling` is merged into the request body verbatim (temperature, seed,
        top_k, ...) so the caller — not this client — owns reproducibility."""
        ...


class _HttpChatClient:
    """Talks to a real OpenAI-compatible `/v1/chat/completions` endpoint (a
    Cosmos Reason NIM). Uses stdlib `urllib` only — no new dependency — and only
    imports/opens a socket when `.complete()` is actually called."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        self.base_url = base_url.rstrip("/")

    def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        timeout: float,
        sampling: dict[str, Any] | None = None,
    ) -> str:
        import urllib.request  # noqa: PLC0415 — lazy: no network on import

        # Sampling comes from the caller (DEFAULT_SAMPLING unless mission.yaml
        # overrides it) and is stamped into the run record, so a number can
        # always be traced back to the decoding config that produced it.
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                **(DEFAULT_SAMPLING if sampling is None else sampling),
            }
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
    """Extract the JSON object from a VLM response, tolerating how models
    actually write it. Falls back to ``{}`` (callers apply fail-safe defaults)
    rather than raising — a flaky VLM response must not crash the mission.

    Handled, in order: clean JSON; JSON wrapped in prose or a ``` fence; and an
    object whose **closing brace is missing entirely**.

    That last case is not hypothetical. Measured 2026-07-29
    (`runs/20260729T112424`, `R258-C004`): the model returned

        ```json
        {"fault_type": "healthy", "confidence": 1.0, "note": "...good condition
        without any immediate issues."
        ```

    — fence closed, brace never closed. The old parser found no ``}``, returned
    ``{}``, the fail-safe mapped it to ``unknown``, and because ``unknown !=
    healthy`` that scored as a **false fault**: one missing character moved
    KPI-03 from 0.00 to 0.053 on a 19-healthy-panel denominator. A parse failure
    must not be able to manufacture a fault verdict, so a truncated-looking
    object is repaired by closing it rather than discarded.
    """
    if not isinstance(raw, str):
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass

    # Strip a code fence if present, so the brace scan below sees only content.
    text = raw.strip()
    if "```" in text:
        parts = text.split("```")
        # Prefer the longest fenced section that contains an object.
        candidates = [p for p in parts[1::2] if "{" in p] or parts
        text = max(candidates, key=len)
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]

    start = text.find("{")
    if start == -1:
        return {}
    end = text.rfind("}")
    if end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    # No usable closing brace: repair rather than throw the answer away. Trim any
    # trailing partial token, close an unterminated string, then balance braces.
    body = text[start:].rstrip().rstrip(",")
    for attempt in (body, body + '"', body):
        if attempt.count('"') % 2:
            continue  # unbalanced quotes; the next attempt closes the string
        missing = attempt.count("{") - attempt.count("}")
        if missing <= 0:
            continue
        try:
            return json.loads(attempt + "}" * missing)
        except json.JSONDecodeError:
            continue
    return {}


def _assess_prompt(context: PanelContext) -> str:
    return (
        "You are inspecting a solar panel image for visible faults.\n"
        f"Faults to look for:\n{_taxonomy_block()}\n"
        f"Panel: {context.get('panel_id')}\n"
        f"Prior inspection history: {context.get('history') or 'none'}\n"
        "Is this panel clean or does it look suspect? Respond with ONLY this "
        'JSON: {"status": "clean"|"suspect", "confidence": <0-1>, "note": "<short reason>"}'
    )


#: What each taxonomy term MEANS, in visible-light terms. Passing bare enum names
#: ("soiled, hotspot, crack, ...") makes the model guess: it reliably mapped a dusty
#: patch to "hotspot" because any localized anomaly matches its hotspot prior. Given
#: these definitions and the identical frame, it answered "soiled" correctly.
#: Every class is defined — defining only the one we want would bias the classifier.
_STATE_DEFINITIONS: dict[PanelState, str] = {
    PanelState.HEALTHY: "no visible defect; uniform cells, clean glass",
    PanelState.SOILED: (
        "dust/dirt/sand deposited ON the glass surface — an opaque tan or brown "
        "patch lying over the cells, often heaviest at the panel's lower edge"
    ),
    PanelState.HOTSPOT: (
        "a single overheating CELL — a small bright or glowing red/orange spot "
        "confined to one cell, not a deposit on the surface"
    ),
    PanelState.CRACK: "a fracture line running across the glass or cells",
    PanelState.STRING_DROPOUT: (
        "a whole row/string of cells uniformly darker or inactive"
    ),
    PanelState.DIODE_FAULT: (
        "a bypass-diode failure — one contiguous SECTION of the module inactive"
    ),
    PanelState.SHADING: (
        "a shadow CAST BY an external object (pole, turbine blade, cloud) — grey "
        "and darker than the cells, following the object's shape, with no deposit"
    ),
    PanelState.UNKNOWN: "the image is unclear or the fault does not match the above",
}


def _taxonomy_block() -> str:
    return "\n".join(
        f"- {state.value}: {desc}" for state, desc in _STATE_DEFINITIONS.items()
    )


def _diagnose_prompt(context: PanelContext) -> str:
    taxonomy = ", ".join(s.value for s in PanelState)
    return (
        "Diagnose the exact fault on this solar panel image.\n"
        f"Fault definitions:\n{_taxonomy_block()}\n"
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
    #: Decoding config. Overrides merge ONTO `DEFAULT_SAMPLING` rather than
    #: replacing it, so a config that sets one knob cannot silently drop the
    #: greedy-decoding guarantee the other three provide.
    sampling: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = _HttpChatClient(self.base_url)
        self.sampling = {**DEFAULT_SAMPLING, **(self.sampling or {})}

    def provenance(self) -> dict[str, Any]:
        """What produced a verdict, for the run record: endpoint, served model
        and the exact decoding config. Deliberately not the frame — that is
        per-panel (`PanelResult.screen_frame_sha`)."""
        return {
            "kind": "cosmos_reason",
            "base_url": self.base_url,
            "model": self.model,
            "timeout_s": self.timeout,
            "sampling": dict(self.sampling),
            # Honesty, not decoration: serial requests were measured repeatable
            # on this build, concurrent ones were not (see DEFAULT_SAMPLING).
            "determinism": "serial-only; continuous batching is not reproducible",
        }

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
                sampling=dict(self.sampling),
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
