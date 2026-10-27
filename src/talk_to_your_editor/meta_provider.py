"""Model providers: Muse Spark / Muse Wonder via the Meta Model API, plus a
deterministic mock for offline demos and tests.

Routing is deterministic and mirrors the verifier's risk tiers:

- Spark (``muse-spark-1.3``) proposes operations for ordinary comments.
- Wonder (``muse-wonder-1.0``) is required for restore and replace-take
  proposals, cuts longer than 20 words or 10 seconds, opening or closing
  passages, and any proposal under 0.80 confidence. When a Wonder review is
  required, the provider either escalates (Wonder receives the primary
  evidence plus the Spark proposal as the claim under review) or, when
  escalation is disabled, records the requirement and converts the operation
  to ``flag_for_review`` so nothing high-risk is silently applied.

Standard tier only. Contributor-tier model ids are rejected by the schema.
The API key comes from the ``MODEL_API_KEY`` environment variable and is
never written to code, files, or logs.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional

from .schema import (
    MAX_JOIN_PAUSE_S,
    MIN_JOIN_PAUSE_S,
    Operation,
    SchemaError,
    validate_operations,
)
from .timeline import Timeline
from .verifier import required_tier

API_URL = "https://api.meta.ai/v1/chat/completions"
MODELS_URL = "https://api.meta.ai/v1/models"
SPARK_MODEL = "muse-spark-1.3"
WONDER_MODEL = "muse-wonder-1.0"

SCHEMA_SPEC = """Return JSON only: {"operations": [ ... ]}. Allowed operation types:
- {"type":"cut_words","comment_id":str,"word_start":int,"word_end":int,"reason":str,"confidence":0..1,"model":str,"risk_tier":1..3}
- {"type":"restore_words", same word fields}
- {"type":"replace_take","cut_word_start":int,"cut_word_end":int,"restore_word_start":int,"restore_word_end":int, ...common}
- {"type":"set_join_pause","join_index":int,"target_pause_s":0.08..0.50, ...common}
- {"type":"flag_for_review","reason":str, ...common}
Word spans are half-open: words[word_start:word_end]. Use the exact model id
given in the request as "model". No other fields are allowed."""


class ProviderError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response, robustly."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ProviderError("model_response_not_json")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ProviderError(f"model_response_not_json:{exc.msg}") from exc


def _sentence_span(timeline: Timeline, word_index: int) -> tuple[int, int]:
    words = timeline.words
    start = word_index
    while start > 0 and not words[start - 1]["word"].rstrip().endswith(
        (".", "!", "?")
    ):
        start -= 1
    end = word_index + 1
    while end < len(words) and not words[end - 1]["word"].rstrip().endswith(
        (".", "!", "?")
    ):
        end += 1
    return start, end


class MockProvider:
    """Deterministic provider: derives operations from comment intent."""

    name = "mock"

    def direct(
        self,
        feedback: dict,
        timeline: Timeline,
        profile: Optional[dict] = None,
    ) -> tuple[list[dict], list[dict]]:
        operations: list[dict] = []
        routing: list[dict] = []
        for comment in _comments(feedback):
            text = str(comment.get("text", ""))
            comment_id = str(comment.get("id", "comment"))
            lowered = text.lower()
            base = {
                "comment_id": comment_id,
                "model": "mock",
                "risk_tier": 1,
                "wonder_reviewed": False,
            }
            ts = comment.get("ts")
            mapping = None
            if ts is not None:
                try:
                    # A pinned time is an estimate: resolve it to the
                    # nearest editable words within a few seconds
                    # either way instead of demanding the exact second.
                    mapping = timeline.map_output_time_approx(float(ts))
                except ValueError:
                    mapping = None
            if "pause" in lowered:
                operations.append(self._pause_op(comment, base, timeline))
            elif mapping is None:
                operations.append(
                    {**base, "type": "flag_for_review",
                     "reason": "comment timestamp could not be mapped",
                     "confidence": 0.5, "risk_tier": 3}
                )
            elif mapping["special"]:
                operations.append(
                    {**base, "type": "flag_for_review",
                     "reason": "comment is pinned to a protected insert",
                     "confidence": 0.5, "risk_tier": 3}
                )
            elif any(k in lowered for k in ("restore", "put back", "bring back")):
                operations.append(self._restore_op(comment, base, timeline, mapping))
            elif any(k in lowered for k in ("remove", "cut", "delete", "take out")):
                start, end = _sentence_span(timeline, mapping["word_index"])
                confidence = 0.6 if "maybe" in lowered else 0.9
                operations.append(
                    {**base, "type": "cut_words", "word_start": start,
                     "word_end": end,
                     "reason": f"mock: remove the sentence at word {start}",
                     "confidence": confidence}
                )
            else:
                operations.append(
                    {**base, "type": "flag_for_review",
                     "reason": "intent not recognized by the mock provider",
                     "confidence": 0.5, "risk_tier": 3}
                )
            routing.append(
                {"comment_id": comment_id, "route": "mock", "model": "mock"}
            )
        return operations, routing

    @staticmethod
    def _pause_op(comment: dict, base: dict, timeline: Timeline) -> dict:
        joins = timeline.joins()
        ts = float(comment.get("ts", 0.0))
        nearest = min(joins, key=lambda j: abs(j["output_time"] - ts))
        match = re.search(r"(\d*\.\d+|\d+)", str(comment.get("text", "")))
        target = float(match.group(1)) if match else 0.20
        target = min(max(target, MIN_JOIN_PAUSE_S), MAX_JOIN_PAUSE_S)
        return {
            **base,
            "type": "set_join_pause",
            "join_index": nearest["join_index"],
            "target_pause_s": target,
            "reason": f"mock: set join {nearest['join_index']} pause to {target}s",
            "confidence": 0.9,
        }

    @staticmethod
    def _restore_op(
        comment: dict, base: dict, timeline: Timeline, mapping: dict
    ) -> dict:
        source_time = mapping["source_time"]
        cut_indices = [
            i
            for i, w in enumerate(timeline.words)
            if timeline.word_status(i) == "cut"
            and abs((float(w["start"]) + float(w["end"])) / 2.0 - source_time) < 3.0
        ]
        if not cut_indices:
            return {
                **base, "type": "flag_for_review",
                "reason": "no cut words near the comment to restore",
                "confidence": 0.5, "risk_tier": 3,
            }
        start = cut_indices[0]
        end = cut_indices[-1] + 1
        while start > 0 and timeline.word_status(start - 1) == "cut":
            start -= 1
        while end < len(timeline.words) and timeline.word_status(end) == "cut":
            end += 1
        return {
            **base, "type": "restore_words", "word_start": start,
            "word_end": end, "reason": "mock: restore the cut span",
            "confidence": 0.85, "risk_tier": 2,
        }


def _comments(feedback: dict) -> list[dict]:
    if isinstance(feedback, list):
        return feedback
    return feedback.get("comments", [])


def _overall_guidance(feedback: dict) -> list[str]:
    """The feedback's overall notes as plain guidance texts.

    Overall notes are about the whole video rather than one moment,
    so they are handed to the model with every comment it directs.
    """
    if not isinstance(feedback, dict):
        return []
    notes = feedback.get("overall_feedback")
    if not isinstance(notes, list):
        return []
    texts = []
    for note in notes:
        if isinstance(note, dict):
            text = str(note.get("text") or "").strip()
        else:
            text = str(note or "").strip()
        if text:
            texts.append(text)
    return texts


def _edit_guidance(feedback: dict) -> list[dict]:
    """Notes the creator left on specific earlier edits.

    A note on an edit is about the removed material itself, not about
    a timestamp, so it is handed to the model labeled with what was
    removed and why, and the model applies it wherever that material
    is involved in this run.
    """
    if not isinstance(feedback, dict):
        return []
    notes = feedback.get("edit_notes")
    if not isinstance(notes, list):
        return []
    guidance = []
    for item in notes:
        if not isinstance(item, dict):
            continue
        text = str(item.get("note") or "").strip()
        if not text:
            continue
        guidance.append(
            {
                "removed_text": str(item.get("removed_text") or ""),
                "reason": str(item.get("reason") or ""),
                "note": text,
            }
        )
    return guidance


class MetaProvider:
    """Live provider over the Meta Model API (OpenAI-compatible)."""

    name = "meta"

    def __init__(
        self,
        api_key: Optional[str] = None,
        allow_wonder: bool = True,
        timeout_s: float = 60.0,
    ):
        self.api_key = api_key or os.environ.get("MODEL_API_KEY")
        self.allow_wonder = allow_wonder
        self.timeout_s = timeout_s

    def check_signin(self) -> bool:
        """One harmless list call to prove the sign-in works.

        Returns True when the service answers the models list, False
        when it refuses the key (401/403). Anything else (no key, an
        unreachable service, another HTTP error) raises ProviderError
        with the usual codes, so callers report it the same way they
        report a failed direction run. No key material is logged.
        """
        if not self.api_key:
            raise ProviderError("MODEL_API_KEY is not set")
        request = urllib.request.Request(
            MODELS_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_s
            ) as response:
                response.read()
            return True
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return False
            raise ProviderError(f"meta_api_http_{exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(
                f"meta_api_unreachable:{exc.reason}"
            ) from exc

    def _chat(self, model: str, messages: list[dict]) -> str:
        if not self.api_key:
            raise ProviderError("MODEL_API_KEY is not set")
        body = {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        for attempt in (True, False):
            payload = dict(body)
            if not attempt:
                payload.pop("response_format", None)
            request = urllib.request.Request(
                API_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_s
                ) as response:
                    data = json.loads(response.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as exc:
                if attempt and exc.code == 400:
                    continue  # response_format unsupported: retry without it
                raise ProviderError(f"meta_api_http_{exc.code}") from exc
            except urllib.error.URLError as exc:
                raise ProviderError(f"meta_api_unreachable:{exc.reason}") from exc
        raise ProviderError("meta_api_unreachable")

    def _propose(
        self, model: str, comment: dict, evidence: dict, extra: Optional[str] = None
    ) -> list[Operation]:
        system = (
            "You are the direction layer of a video editor. You convert one "
            "creator comment into strict edit operations. " + SCHEMA_SPEC
            + " The comment's timestamp is an estimate, not an exact "
            "point: it has been resolved to the nearest words within a "
            "few seconds either way. Trust the words in the evidence "
            "window and what the comment says over the exact second."
        )
        public_evidence = {
            k: v for k, v in evidence.items() if not k.startswith("_")
        }
        user = json.dumps(
            {"comment": comment, "evidence": public_evidence, "model": model,
             "note": extra or ""}
        )
        content = self._chat(
            model,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        parsed = extract_json(content)
        try:
            return validate_operations(parsed)
        except SchemaError as exc:
            raise ProviderError(
                f"model_operations_invalid:{'; '.join(exc.reasons)}"
            ) from exc

    def direct(
        self,
        feedback: dict,
        timeline: Timeline,
        profile: Optional[dict] = None,
    ) -> tuple[list[dict], list[dict]]:
        operations: list[dict] = []
        routing: list[dict] = []
        overall = _overall_guidance(feedback)
        edit_guidance = _edit_guidance(feedback)
        for comment in _comments(feedback):
            evidence = self._evidence(
                comment, timeline, profile, overall, edit_guidance
            )
            proposed = self._propose(SPARK_MODEL, comment, evidence)
            for op in proposed:
                route = self._route(op, comment, evidence, operations, routing)
                routing.append(route)
        return operations, routing

    def _route(
        self,
        op: Operation,
        comment: dict,
        evidence: dict,
        operations: list[dict],
        routing: list[dict],
    ) -> dict:
        timeline = evidence["_timeline"]
        needs_wonder = required_tier(op, timeline) >= 2
        record = {
            "comment_id": op.comment_id,
            "route": "spark",
            "model": SPARK_MODEL,
            "requires_wonder": needs_wonder,
        }
        if not needs_wonder or op.wonder_reviewed:
            operations.append(op.to_dict())
            return record
        record["route"] = "wonder"
        record["model"] = WONDER_MODEL
        if not self.allow_wonder:
            record["wonder_escalation"] = "disabled_recorded"
            operations.append(
                {
                    "type": "flag_for_review",
                    "comment_id": op.comment_id,
                    "reason": (
                        "requires_wonder_review: high-risk proposal held "
                        "because Wonder escalation is disabled"
                    ),
                    "confidence": op.confidence,
                    "model": SPARK_MODEL,
                    "risk_tier": 3,
                    "wonder_reviewed": False,
                }
            )
            return record
        reviewed = self._propose(
            WONDER_MODEL,
            comment,
            evidence,
            extra=(
                "Review this Spark proposal as the claim under review. "
                f"Proposal: {json.dumps(op.to_dict())}. Confirm it, correct "
                "it, or replace it with flag_for_review."
            ),
        )
        for reviewed_op in reviewed:
            data = reviewed_op.to_dict()
            data["wonder_reviewed"] = True
            data["risk_tier"] = max(data["risk_tier"], 2)
            operations.append(data)
        record["wonder_escalation"] = "completed"
        return record

    @staticmethod
    def _evidence(
        comment: dict,
        timeline: Timeline,
        profile: Optional[dict],
        overall: Optional[list] = None,
        edit_guidance: Optional[list] = None,
    ) -> dict:
        ts = comment.get("ts")
        mapping = None
        if ts is not None:
            try:
                mapping = timeline.map_output_time_approx(float(ts))
            except ValueError:
                mapping = None
        window: list[dict] = []
        if mapping and mapping.get("word_index") is not None:
            center = mapping["word_index"]
            for i in range(max(0, center - 15), min(len(timeline.words), center + 16)):
                word = timeline.words[i]
                window.append(
                    {
                        "index": i,
                        "word": word["word"],
                        "start": word["start"],
                        "end": word["end"],
                        "status": timeline.word_status(i),
                    }
                )
        return {
            "_timeline": timeline,
            "mapping": mapping,
            "timestamp_estimate": {
                "window_s": mapping.get("window_s") if mapping else None,
                "approximate": bool(mapping and mapping.get("approximate")),
                "note": (
                    "The pinned time is an estimate. The mapping was "
                    "resolved to the nearest words within a few seconds "
                    "either way; trust the words and the comment over "
                    "the exact second."
                ),
            },
            "words": window,
            "joins": timeline.joins(),
            "protected_spans": timeline.protected_spans(profile),
            "profile": profile or {},
            "overall_guidance": list(overall or []),
            "edit_guidance": list(edit_guidance or []),
        }
