"""Deterministic verification and risk tiers.

The verifier never trusts the proposing model. Every operation is rechecked
against the transcript, the cuts plan, the creator profile, and the locked
risk rules from the plan:

- Tier 1 (apply after checks): a short exact cut of kept words; a bounded
  join-pause change.
- Tier 2 (Wonder review required): restore, replace-take, a cut longer than
  20 words, a cut over 10 seconds, the opening or closing passage, or Spark
  confidence below 0.80.
- Tier 3 (flag only in v1): protected material, a special insert, a
  negation-bearing span, an unclear target, overlapping operations, or a
  change removing more than 25 percent of the finished edit.

A verdict is one of ``apply``, ``flag`` (turned into flag_for_review for the
creator), or ``reject`` (structurally invalid). Nothing flagged or rejected
is ever applied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .schema import Operation
from .timeline import (
    EPS,
    Timeline,
    is_special_range,
    quantize_ceil,
    quantize_floor,
    subtract_span,
)

MIN_RANGE_S = 0.10
MAX_REMOVAL_FRACTION = 0.25
LONG_CUT_WORDS = 20
LONG_CUT_SECONDS = 10.0
WONDER_CONFIDENCE_FLOOR = 0.80

# The one verdict reason written for a person, not a machine: an edit
# held back only because an earlier note's edit already claimed the
# same words. It is shown to the creator verbatim, so it is a sentence,
# not a code.
OVERLAP_FLAG_REASON = (
    "this edit overlaps another note's edit, so the first edit was "
    "applied and this one was held for your review"
)

NEGATION_WORDS = {
    "no", "not", "never", "nor", "without", "cannot", "can't", "cant",
    "don't", "dont", "won't", "wont", "isn't", "isnt", "didn't", "didnt",
    "doesn't", "doesnt", "couldn't", "couldnt", "shouldn't", "shouldnt",
    "wouldn't", "wouldnt", "ain't", "aint", "n't",
}


@dataclass
class Verdict:
    operation: Operation
    status: str  # apply | flag | reject
    reasons: list[str] = field(default_factory=list)
    risk_tier: int = 1
    required_tier: int = 1

    def to_dict(self) -> dict:
        return {
            "operation": self.operation.to_dict(),
            "status": self.status,
            "reasons": self.reasons,
            "risk_tier": self.risk_tier,
            "required_tier": self.required_tier,
        }


def _norm_word(text: str) -> str:
    return text.strip(" \t\n.,!?;:'\"").lower()


def span_has_negation(timeline: Timeline, start: int, end: int) -> bool:
    return any(
        _norm_word(timeline.words[i]["word"]) in NEGATION_WORDS
        for i in range(start, min(end, len(timeline.words)))
    )


def _opening_closing_window(timeline: Timeline) -> int:
    return max(10, -(-len(timeline.words) // 20))  # max(10, ceil(5% of words))


def required_tier(op: Operation, timeline: Timeline) -> int:
    """The minimum risk tier an operation must carry, from the plan rules."""
    if op.type == "flag_for_review":
        return 3
    if op.type == "set_join_pause":
        return 1
    if op.type in ("restore_words", "replace_take"):
        tier = 2
    else:  # cut_words
        tier = 1
        start, end = op.word_start, op.word_end
        duration = (
            float(timeline.words[end - 1]["end"]) - float(timeline.words[start]["start"])
            if 0 <= start < end <= len(timeline.words)
            else 0.0
        )
        if end - start > LONG_CUT_WORDS or duration > LONG_CUT_SECONDS:
            tier = 2
    if op.confidence < WONDER_CONFIDENCE_FLOOR:
        tier = max(tier, 2)
    kept = timeline.kept_word_indices()
    if kept and op.type in ("cut_words", "restore_words", "replace_take"):
        spans = op.word_spans()
        window = _opening_closing_window(timeline)
        first_kept, last_kept = kept[0], kept[-1]
        for start, end, _role in spans:
            if start <= first_kept + window or end - 1 >= last_kept - window:
                tier = max(tier, 2)
    return tier


def _span_duration(timeline: Timeline, start: int, end: int) -> float:
    return float(timeline.words[end - 1]["end"]) - float(timeline.words[start]["start"])


def _edit_targets(op: Operation) -> tuple[list[tuple[int, int]], set[int]]:
    """The word spans and joins an operation would change.

    ``flag_for_review`` operations change nothing, so they claim no
    targets and can never conflict with a real edit.
    """
    if op.type == "flag_for_review":
        return [], set()
    spans = [(start, end) for start, end, _role in op.word_spans()]
    joins = {op.join_index} if op.type == "set_join_pause" else set()
    return spans, joins


def _targets_conflict(
    a_spans: list[tuple[int, int]],
    a_joins: set[int],
    b_spans: list[tuple[int, int]],
    b_joins: set[int],
) -> bool:
    if a_joins & b_joins:
        return True
    return any(
        a_start < b_end and b_start < a_end
        for a_start, a_end in a_spans
        for b_start, b_end in b_spans
    )


def plan_cut_absorption(
    words: list[dict],
    ranges: list[dict],
    fps: Optional[float],
    word_start: int,
    word_end: int,
) -> dict:
    """Plan a word-span cut, absorbing word-free sub-minimum remnants.

    Subtracting a span from a kept range can split it and leave a residual
    piece shorter than ``MIN_RANGE_S``. The foundation planner's rule for
    that case is to drop or absorb the remnant, not to refuse the edit: so
    when a sub-minimum remnant holds no kept word outside the target span,
    the cut is extended to that range edge and the remnant is absorbed.

    A remnant that does hold a kept word outside the target span blocks the
    cut (``blocked`` is True); absorbing it would remove speech nobody
    asked to remove.

    Returns ``{cut_start, cut_end, absorbed_s, blocked}`` where
    ``absorbed_s`` is the total remnant duration folded into the cut.
    Shared by the verifier (simulation) and the applier so both agree.
    """
    q_start = quantize_floor(float(words[word_start]["start"]), fps)
    q_end = quantize_ceil(float(words[word_end - 1]["end"]), fps)
    cut_start, cut_end = q_start, q_end
    absorbed = 0.0
    blocked = False
    for rng in ranges:
        if is_special_range(rng):
            continue
        start, end = float(rng["start"]), float(rng["end"])
        if q_end <= start + EPS or q_start >= end - EPS:
            continue
        pieces = []
        if q_start > start + EPS:
            pieces.append((start, q_start))
        if q_end < end - EPS:
            pieces.append((q_end, end))
        for piece_start, piece_end in pieces:
            if piece_end - piece_start >= MIN_RANGE_S - EPS:
                continue
            outside = [
                i
                for i, word in enumerate(words)
                if piece_start - EPS
                <= (float(word["start"]) + float(word["end"])) / 2.0
                <= piece_end + EPS
                and not (word_start <= i < word_end)
            ]
            if outside:
                blocked = True
            else:
                absorbed += piece_end - piece_start
                cut_start = min(cut_start, piece_start)
                cut_end = max(cut_end, piece_end)
    return {
        "cut_start": cut_start,
        "cut_end": cut_end,
        "absorbed_s": absorbed,
        "blocked": blocked,
    }


def verify_operations(
    ops: list[Operation],
    timeline: Timeline,
    profile: Optional[dict] = None,
) -> list[Verdict]:
    protected = timeline.protected_spans(profile)
    total_words = len(timeline.words)
    verdicts: list[Verdict] = []

    for op in ops:
        reasons: list[str] = []
        status = "apply"
        tier3 = False

        def flag(reason: str) -> None:
            nonlocal status, tier3
            reasons.append(reason)
            tier3 = True
            if status == "apply":
                status = "flag"

        def reject(reason: str) -> None:
            nonlocal status
            reasons.append(reason)
            status = "reject"

        if op.type == "flag_for_review":
            verdicts.append(Verdict(op, "flag", ["creator_review_requested"], 3, 3))
            continue

        # Bounds.
        spans = op.word_spans()
        for start, end, _role in spans:
            if start < 0 or end > total_words:
                reject("word_index_out_of_range")
            elif float(timeline.words[end - 1]["end"]) > timeline.source_duration + EPS:
                reject("span_beyond_source_duration")
        if op.type == "set_join_pause" and not (
            0 <= op.join_index < len(timeline.ranges) - 1
        ):
            reject("join_index_out_of_range")

        # Kept / cut / special status per span role.
        for start, end, role in spans:
            if status == "reject":
                break
            for i in range(start, end):
                word_status = timeline.word_status(i)
                if word_status == "special":
                    flag("special_insert_targeted")
                    break
                if role == "cut" and word_status == "cut":
                    reject("cut_span_not_kept")
                    break
                if role == "restore" and word_status == "kept":
                    reject("restore_span_not_cut")
                    break

        # replace_take parts must not overlap each other.
        if op.type == "replace_take" and status != "reject":
            cut_span = (op.cut_word_start, op.cut_word_end)
            restore_span = (op.restore_word_start, op.restore_word_end)
            if cut_span[0] < restore_span[1] and restore_span[0] < cut_span[1]:
                reject("replace_take_spans_overlap")

        # Protected spans (profile, curation holds, signature/post-edit).
        for start, end, _role in spans:
            for span in protected:
                if start < span["word_end"] and span["word_start"] < end:
                    flag(f"protected_span:{span['label']} ({span['source']})")

        # Negation-bearing spans are flagged in v1, never auto-applied.
        for start, end, _role in spans:
            if 0 <= start < end <= total_words and span_has_negation(
                timeline, start, end
            ):
                flag("negation_bearing_span")

        # Word boundaries: operations are word-index based, so the only way
        # to split a word is a quantized edge landing strictly inside some
        # word's span (possible when neighbouring word timestamps overlap).
        for start, end, _role in spans:
            if status == "reject":
                break
            q_start = quantize_floor(
                float(timeline.words[start]["start"]), timeline.fps
            )
            q_end = quantize_ceil(
                float(timeline.words[end - 1]["end"]), timeline.fps
            )
            for word in timeline.words:
                ws, we = float(word["start"]), float(word["end"])
                if (ws + EPS < q_start < we - EPS) or (
                    ws + EPS < q_end < we - EPS
                ):
                    reject("word_boundary_split")
                    break

        # Frame quantization: quantized cut points must not clip neighbours.
        if timeline.fps and status != "reject":
            for start, end, role in spans:
                if role != "cut":
                    continue
                q_start = quantize_floor(
                    float(timeline.words[start]["start"]), timeline.fps
                )
                q_end = quantize_ceil(
                    float(timeline.words[end - 1]["end"]), timeline.fps
                )
                if start > 0 and timeline.word_status(start - 1) == "kept":
                    if q_start < float(timeline.words[start - 1]["end"]) - EPS:
                        reject("frame_quantization_clips_neighbor")
                if end < total_words and timeline.word_status(end) == "kept":
                    if q_end > float(timeline.words[end]["start"]) + EPS:
                        reject("frame_quantization_clips_neighbor")

        # Resulting range lengths and pause feasibility: simulate.
        if status == "apply":
            if op.type in ("cut_words", "replace_take"):
                start, end = (
                    (op.word_start, op.word_end)
                    if op.type == "cut_words"
                    else (op.cut_word_start, op.cut_word_end)
                )
                # Mirror the applier: word-free sub-minimum remnants are
                # absorbed into the cut; a remnant holding a kept word
                # outside the span blocks it.
                plan = plan_cut_absorption(
                    timeline.words,
                    timeline.ranges,
                    timeline.fps,
                    start,
                    end,
                )
                if plan["blocked"]:
                    reject("min_range_duration")
                else:
                    # Only pieces of ranges this cut actually touches are
                    # judged: a sub-minimum range that already exists
                    # elsewhere in the plan is the foundation's own state,
                    # not something this operation would leave behind.
                    for rng in timeline.ranges:
                        if is_special_range(rng):
                            continue
                        r_start = float(rng["start"])
                        r_end = float(rng["end"])
                        if (
                            plan["cut_end"] <= r_start + EPS
                            or plan["cut_start"] >= r_end - EPS
                        ):
                            continue
                        pieces = subtract_span(
                            [rng], plan["cut_start"], plan["cut_end"]
                        )
                        if any(
                            float(p["end"]) - float(p["start"])
                            < MIN_RANGE_S - EPS
                            for p in pieces
                        ):
                            reject("min_range_duration")
                            break
            elif op.type == "set_join_pause":
                join = timeline.joins()[op.join_index]
                if join["prev_special"] or join["next_special"]:
                    flag("special_insert_join")
                else:
                    prev_rng = timeline.ranges[op.join_index]
                    next_rng = timeline.ranges[op.join_index + 1]
                    next_words = timeline.words_in_range(next_rng)
                    prev_words = timeline.words_in_range(prev_rng)
                    if not next_words or not prev_words:
                        reject("join_has_no_speech_edge")
                    else:
                        first_next = timeline.words[next_words[0]]
                        last_prev = timeline.words[prev_words[-1]]
                        new_start = float(first_next["start"]) - op.target_pause_s
                        earliest = max(
                            float(prev_rng["end"]), float(last_prev["end"])
                        )
                        if new_start < earliest - EPS:
                            reject("pause_out_of_bounds")
                        elif new_start > float(first_next["start"]) + EPS:
                            reject("pause_out_of_bounds")

        req = required_tier(op, timeline)
        if tier3:
            req = 3
        effective = max(op.risk_tier, req)
        if status == "apply" and req >= 2 and not op.wonder_reviewed:
            flag("wonder_review_required")
            effective = max(effective, req)
        if status == "flag":
            effective = max(effective, 3) if tier3 else effective
        verdicts.append(Verdict(op, status, reasons, effective, req))

    # 25 percent rule, evaluated last, per operation and per applying
    # batch: one operation removing over 25 percent is flagged even when
    # other rules flagged it first, and operations that would otherwise
    # apply are flagged when their combined removal crosses the line.
    # Operations already flagged for other reasons do not count toward the
    # batch total, so they cannot poison safe changes in the same batch.
    if timeline.output_duration > 0:
        limit = MAX_REMOVAL_FRACTION * timeline.output_duration
        cut_seconds: dict[int, float] = {}
        for index, verdict in enumerate(verdicts):
            total = 0.0
            for start, end, role in verdict.operation.word_spans():
                if role == "cut" and 0 <= start < end <= total_words:
                    total += _span_duration(timeline, start, end)
            cut_seconds[index] = total
            if total > limit and verdict.status == "apply":
                verdict.status = "flag"
                verdict.reasons.append("removes_over_25_percent")
                verdict.risk_tier = 3
                verdict.required_tier = max(verdict.required_tier, 3)
            elif total > limit and verdict.status == "flag":
                verdict.reasons.append("removes_over_25_percent")
        # The batch total is what the applying edits would actually
        # remove: the union of their cut spans. Summing per-edit spans
        # would double-count words two edits share, so two notes
        # pointing at the same sentence could together cross the line
        # for a removal neither one makes on its own.
        applying_spans = []
        for i, v in enumerate(verdicts):
            if v.status != "apply":
                continue
            for start, end, role in v.operation.word_spans():
                if role == "cut" and 0 <= start < end <= total_words:
                    applying_spans.append((start, end))
        applying_total = 0.0
        merged_start = merged_end = None
        for start, end in sorted(applying_spans):
            if merged_start is None:
                merged_start, merged_end = start, end
            elif start <= merged_end:
                merged_end = max(merged_end, end)
            else:
                applying_total += _span_duration(
                    timeline, merged_start, merged_end
                )
                merged_start, merged_end = start, end
        if merged_start is not None:
            applying_total += _span_duration(
                timeline, merged_start, merged_end
            )
        if applying_total > limit:
            for index, verdict in enumerate(verdicts):
                if verdict.status == "apply" and cut_seconds[index] > 0:
                    verdict.status = "flag"
                    verdict.reasons.append("removes_over_25_percent")
                    verdict.risk_tier = 3
                    verdict.required_tier = max(verdict.required_tier, 3)

    # Overlap resolution, in the order the operations were proposed:
    # the first edit that checks out claims its words and joins, and a
    # later edit that would change the same words is flagged for the
    # creator instead of applied on top of it. Edits already flagged or
    # rejected claim nothing, so they cannot block a later edit that
    # does check out. This replaces flagging every overlapping edit:
    # one applies, the conflicting one is held, and the run completes.
    claimed_spans: list[tuple[int, int]] = []
    claimed_joins: set[int] = set()
    for verdict in verdicts:
        if verdict.status != "apply":
            continue
        spans, joins = _edit_targets(verdict.operation)
        if _targets_conflict(claimed_spans, claimed_joins, spans, joins):
            verdict.status = "flag"
            verdict.reasons.append(OVERLAP_FLAG_REASON)
            verdict.risk_tier = 3
            verdict.required_tier = max(verdict.required_tier, 3)
        else:
            claimed_spans.extend(spans)
            claimed_joins |= joins
    return verdicts
