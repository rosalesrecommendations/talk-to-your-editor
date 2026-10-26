"""Strict operation schema for the Talk to Your Editor direction layer.

Version one allows exactly five operation types:

- ``cut_words``: remove an exact kept word span.
- ``restore_words``: restore an exact currently cut word span.
- ``replace_take``: atomically restore one cut span and cut one kept span.
- ``set_join_pause``: set the pause at one numbered join (0.08 to 0.50 s).
- ``flag_for_review``: make no change; a person must decide.

Word spans are half-open: ``word_start`` inclusive, ``word_end`` exclusive,
so a span covers ``words[word_start:word_end]``.

Every operation carries the originating comment id, a plain-language reason,
a confidence in [0, 1], the proposing model id, a risk tier (1 to 3), and an
optional ``wonder_reviewed`` flag recording that Muse Wonder reviewed the
proposal. Unknown fields, unknown types, wrong value types, invalid ranges,
contributor-tier model ids, and overlapping operations are all rejected with
named reasons. Standard tier models only; the discounted contributor tier
trains on prompts and is forbidden for creator content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# Standard-tier hosted models the direction layer may use, plus the offline
# deterministic mock used for demos and tests.
ALLOWED_MODELS = {
    "muse-spark-1.3",
    "muse-spark-1.2",
    "muse-spark-1.1",
    "muse-wonder-1.0",
    "mock",
}

OPERATION_TYPES = {
    "cut_words",
    "restore_words",
    "replace_take",
    "set_join_pause",
    "flag_for_review",
}

COMMON_FIELDS = {
    "type",
    "comment_id",
    "reason",
    "confidence",
    "model",
    "risk_tier",
    "wonder_reviewed",
}

TYPE_FIELDS = {
    "cut_words": {"word_start", "word_end"},
    "restore_words": {"word_start", "word_end"},
    "replace_take": {
        "cut_word_start",
        "cut_word_end",
        "restore_word_start",
        "restore_word_end",
    },
    "set_join_pause": {"join_index", "target_pause_s"},
    # flag_for_review may optionally pin a word span or a join, never both.
    "flag_for_review": {"word_start", "word_end", "join_index"},
}

MIN_JOIN_PAUSE_S = 0.08
MAX_JOIN_PAUSE_S = 0.50


class SchemaError(ValueError):
    """Raised when operation data fails strict validation."""

    def __init__(self, reasons: Iterable[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass(frozen=True)
class Operation:
    """One validated edit operation."""

    type: str
    comment_id: str
    reason: str
    confidence: float
    model: str
    risk_tier: int
    wonder_reviewed: bool = False
    word_start: Optional[int] = None
    word_end: Optional[int] = None
    cut_word_start: Optional[int] = None
    cut_word_end: Optional[int] = None
    restore_word_start: Optional[int] = None
    restore_word_end: Optional[int] = None
    join_index: Optional[int] = None
    target_pause_s: Optional[float] = None
    extra: dict = field(default_factory=dict, compare=False)

    def word_spans(self) -> list[tuple[int, int, str]]:
        """Return (start, end, role) word spans this operation touches."""
        if self.type in ("cut_words", "restore_words"):
            role = "cut" if self.type == "cut_words" else "restore"
            return [(self.word_start, self.word_end, role)]
        if self.type == "replace_take":
            return [
                (self.cut_word_start, self.cut_word_end, "cut"),
                (self.restore_word_start, self.restore_word_end, "restore"),
            ]
        if self.type == "flag_for_review" and self.word_start is not None:
            return [(self.word_start, self.word_end, "flag")]
        return []

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "type": self.type,
            "comment_id": self.comment_id,
            "reason": self.reason,
            "confidence": self.confidence,
            "model": self.model,
            "risk_tier": self.risk_tier,
            "wonder_reviewed": self.wonder_reviewed,
        }
        for name in (
            "word_start",
            "word_end",
            "cut_word_start",
            "cut_word_end",
            "restore_word_start",
            "restore_word_end",
            "join_index",
            "target_pause_s",
        ):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        return out


def _check_word_span(
    data: dict, start_key: str, end_key: str, label: str, reasons: list[str]
) -> None:
    start, end = data.get(start_key), data.get(end_key)
    if not _is_int(start):
        reasons.append(f"invalid_type:{start_key}")
        return
    if not _is_int(end):
        reasons.append(f"invalid_type:{end_key}")
        return
    if start < 0 or end < 0:
        reasons.append(f"word_index_negative:{label}")
    elif end <= start:
        reasons.append(f"invalid_word_range:{label}")


def _validate_one(raw: Any, index: int) -> tuple[Optional[Operation], list[str]]:
    label = f"op[{index}]"
    reasons: list[str] = []
    if not isinstance(raw, dict):
        return None, [f"invalid_type:{label}"]
    op_type = raw.get("type")
    if op_type not in OPERATION_TYPES:
        return None, [f"unknown_operation_type:{op_type!r}"]

    allowed = COMMON_FIELDS | TYPE_FIELDS[op_type]
    for key in raw:
        if key not in allowed:
            reasons.append(f"unknown_field:{key}")
    for key in COMMON_FIELDS - {"wonder_reviewed"}:
        if key not in raw:
            reasons.append(f"missing_field:{key}")

    comment_id = raw.get("comment_id")
    if not isinstance(comment_id, str) or not comment_id.strip():
        reasons.append("invalid_type:comment_id")
    reason = raw.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reasons.append("invalid_type:reason")

    confidence = raw.get("confidence")
    if not _is_number(confidence):
        reasons.append("invalid_type:confidence")
    elif not 0.0 <= float(confidence) <= 1.0:
        reasons.append("confidence_out_of_range")

    model = raw.get("model")
    if not isinstance(model, str):
        reasons.append("invalid_type:model")
    elif "contributor" in model.lower():
        # Checked before the allow-list so the reason names the real problem:
        # the contributor tier trains on prompts and is forbidden here.
        reasons.append("contributor_model_forbidden")
    elif model not in ALLOWED_MODELS:
        reasons.append(f"unknown_model:{model}")

    risk_tier = raw.get("risk_tier")
    if not _is_int(risk_tier) or risk_tier not in (1, 2, 3):
        reasons.append("invalid_type:risk_tier")

    wonder_reviewed = raw.get("wonder_reviewed", False)
    if not isinstance(wonder_reviewed, bool):
        reasons.append("invalid_type:wonder_reviewed")

    if op_type in ("cut_words", "restore_words"):
        for key in ("word_start", "word_end"):
            if key not in raw:
                reasons.append(f"missing_field:{key}")
        if "word_start" in raw and "word_end" in raw:
            _check_word_span(raw, "word_start", "word_end", label, reasons)
    elif op_type == "replace_take":
        for key in TYPE_FIELDS["replace_take"]:
            if key not in raw:
                reasons.append(f"missing_field:{key}")
        if all(k in raw for k in ("cut_word_start", "cut_word_end")):
            _check_word_span(raw, "cut_word_start", "cut_word_end", label, reasons)
        if all(k in raw for k in ("restore_word_start", "restore_word_end")):
            _check_word_span(
                raw, "restore_word_start", "restore_word_end", label, reasons
            )
    elif op_type == "set_join_pause":
        join_index = raw.get("join_index")
        if not _is_int(join_index):
            reasons.append("invalid_type:join_index")
        elif join_index < 0:
            reasons.append("join_index_negative")
        target = raw.get("target_pause_s")
        if not _is_number(target):
            reasons.append("invalid_type:target_pause_s")
        elif not MIN_JOIN_PAUSE_S <= float(target) <= MAX_JOIN_PAUSE_S:
            reasons.append("target_pause_out_of_range")
    elif op_type == "flag_for_review":
        has_words = "word_start" in raw or "word_end" in raw
        has_join = "join_index" in raw
        if has_words and has_join:
            reasons.append("flag_span_ambiguous")
        if has_words:
            if "word_start" not in raw or "word_end" not in raw:
                reasons.append("missing_field:word_span_pair")
            else:
                _check_word_span(raw, "word_start", "word_end", label, reasons)
        if has_join and not _is_int(raw.get("join_index")):
            reasons.append("invalid_type:join_index")

    if reasons:
        return None, reasons
    fields = {k: raw[k] for k in raw if k in allowed and k != "type"}
    fields["confidence"] = float(fields["confidence"])
    if "target_pause_s" in fields:
        fields["target_pause_s"] = float(fields["target_pause_s"])
    return Operation(type=op_type, **fields), []


def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def find_overlaps(ops: list[Operation]) -> list[str]:
    """Named reasons for any pair of operations touching the same target."""
    reasons: list[str] = []
    seen_joins: dict[int, str] = {}
    for op in ops:
        if op.type == "set_join_pause":
            if op.join_index in seen_joins:
                reasons.append(
                    f"overlapping_operations:join {op.join_index} targeted by "
                    f"{seen_joins[op.join_index]} and {op.comment_id}"
                )
            seen_joins[op.join_index] = op.comment_id
    spans: list[tuple[int, int, str, int]] = []
    for op_index, op in enumerate(ops):
        for start, end, _role in op.word_spans():
            if op.type == "flag_for_review":
                continue
            for o_start, o_end, o_comment, o_index in spans:
                if o_index == op_index:
                    continue  # an operation never overlaps itself
                if _spans_overlap((start, end), (o_start, o_end)):
                    reasons.append(
                        f"overlapping_operations:{o_comment} and {op.comment_id} "
                        f"share words {max(start, o_start)}..{min(end, o_end)}"
                    )
            spans.append((start, end, op.comment_id, op_index))
    return reasons


def validate_operations(data: Any) -> list[Operation]:
    """Validate operation data strictly. Raises SchemaError with reasons.

    Accepts either a list of operation objects or a dict with an
    ``operations`` list. Overlaps between operations are schema failures.
    """
    if isinstance(data, dict):
        if "operations" not in data:
            raise SchemaError(["missing_field:operations"])
        items = data["operations"]
    else:
        items = data
    if not isinstance(items, list):
        raise SchemaError(["invalid_type:operations"])
    ops: list[Operation] = []
    reasons: list[str] = []
    for index, raw in enumerate(items):
        op, errs = _validate_one(raw, index)
        reasons.extend(errs)
        if op is not None:
            ops.append(op)
    if reasons:
        raise SchemaError(reasons)
    overlap_reasons = find_overlaps(ops)
    if overlap_reasons:
        raise SchemaError(overlap_reasons)
    return ops
