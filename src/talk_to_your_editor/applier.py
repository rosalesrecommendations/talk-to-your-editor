"""Apply verified operations to a copy of a cuts plan.

The input plan is never mutated. Only operations whose verdict is ``apply``
change the revised plan; flagged and rejected operations are recorded in the
audit and change nothing. ``replace_take`` is atomic: both halves are
revalidated against the working copy before either is applied, and any
failure aborts the whole apply with no files written by the caller.

Join-pause operations are applied first, while the original join indices
still refer to the original ranges; word-span operations follow.
"""

from __future__ import annotations

import copy
from typing import Optional

from .timeline import (
    Timeline,
    add_span,
    is_special_range,
    quantize_ceil,
    quantize_floor,
    quantize_round,
    subtract_span,
)
from .verifier import Verdict, plan_cut_absorption


class ApplyError(RuntimeError):
    """Raised when an apply verdict cannot be applied atomically."""


def _plan_stats(timeline_words: list[dict], ranges: list[dict]) -> dict:
    kept_words = 0
    for word in timeline_words:
        mid = (float(word["start"]) + float(word["end"])) / 2.0
        for rng in ranges:
            if is_special_range(rng):
                continue
            if float(rng["start"]) - 1e-6 <= mid <= float(rng["end"]) + 1e-6:
                kept_words += 1
                break
    return {
        "kept_ranges": len(ranges),
        "ordinary_ranges": sum(1 for r in ranges if not is_special_range(r)),
        "special_ranges": sum(1 for r in ranges if is_special_range(r)),
        "output_duration_s": round(
            sum(float(r["end"]) - float(r["start"]) for r in ranges), 4
        ),
        "kept_words": kept_words,
        "cut_words": len(timeline_words) - kept_words,
    }


def _cut_span_times(timeline: Timeline, start: int, end: int) -> tuple[float, float]:
    q_start = quantize_floor(float(timeline.words[start]["start"]), timeline.fps)
    q_end = quantize_ceil(float(timeline.words[end - 1]["end"]), timeline.fps)
    return q_start, q_end


def apply_verdicts(
    cuts: dict,
    timeline: Timeline,
    verdicts: list[Verdict],
) -> tuple[dict, dict]:
    """Return (revised_cuts, audit). The input ``cuts`` is left unchanged."""
    new_cuts = copy.deepcopy(cuts)
    ranges: list[dict] = new_cuts.get("kept_ranges", [])
    words = timeline.words
    applied: list[str] = []
    absorbed_slivers: list[dict] = []
    pause_updates: dict[int, float] = {}

    ordered = sorted(
        (v for v in verdicts if v.status == "apply"),
        key=lambda v: 0 if v.operation.type == "set_join_pause" else 1,
    )

    for verdict in ordered:
        op = verdict.operation
        if op.type == "set_join_pause":
            next_rng = ranges[op.join_index + 1]
            next_words = [
                i
                for i, w in enumerate(words)
                if float(next_rng["start"]) - 1e-6
                <= (float(w["start"]) + float(w["end"])) / 2.0
                <= float(next_rng["end"]) + 1e-6
            ]
            first_word = words[next_words[0]]
            new_start = quantize_round(
                float(first_word["start"]) - op.target_pause_s, timeline.fps
            )
            next_rng["start"] = new_start
            pause_updates[op.join_index] = op.target_pause_s
            applied.append(op.comment_id)
        elif op.type == "cut_words":
            # Mirror the verifier: absorb word-free sub-minimum remnants
            # into the cut instead of stranding them as tiny ranges.
            plan = plan_cut_absorption(
                words, ranges, timeline.fps, op.word_start, op.word_end
            )
            if plan["blocked"]:
                raise ApplyError(
                    f"min_range_duration:{op.comment_id} cut would strand a "
                    "kept word in a sub-minimum range piece"
                )
            ranges = subtract_span(ranges, plan["cut_start"], plan["cut_end"])
            if plan["absorbed_s"] > 0:
                absorbed_slivers.append(
                    {
                        "comment_id": op.comment_id,
                        "absorbed_s": round(plan["absorbed_s"], 4),
                    }
                )
            applied.append(op.comment_id)
        elif op.type == "restore_words":
            q_start, q_end = _cut_span_times(
                timeline, op.word_start, op.word_end
            )
            # The restored range takes the output slot of the next kept word.
            following = [
                i
                for i in range(op.word_end, len(words))
                if timeline.word_status(i) == "kept"
            ]
            ranges = add_span(
                ranges,
                q_start,
                q_end,
                following[0] if following else None,
                words,
            )
            applied.append(op.comment_id)
        elif op.type == "replace_take":
            # Atomic: revalidate both halves against the working copy first.
            probe = Timeline(timeline.transcript, {"kept_ranges": ranges,
                                                    "source_duration_s": timeline.source_duration,
                                                    "frame_quantization": {"fps": timeline.fps} if timeline.fps else {}})
            for i in range(op.cut_word_start, op.cut_word_end):
                if probe.word_status(i) != "kept":
                    raise ApplyError(
                        f"replace_take_not_atomic:{op.comment_id} cut span no longer kept"
                    )
            for i in range(op.restore_word_start, op.restore_word_end):
                if probe.word_status(i) != "cut":
                    raise ApplyError(
                        f"replace_take_not_atomic:{op.comment_id} restore span no longer cut"
                    )
            plan = plan_cut_absorption(
                words,
                ranges,
                timeline.fps,
                op.cut_word_start,
                op.cut_word_end,
            )
            if plan["blocked"]:
                raise ApplyError(
                    f"min_range_duration:{op.comment_id} cut would strand a "
                    "kept word in a sub-minimum range piece"
                )
            ranges = subtract_span(ranges, plan["cut_start"], plan["cut_end"])
            if plan["absorbed_s"] > 0:
                absorbed_slivers.append(
                    {
                        "comment_id": op.comment_id,
                        "absorbed_s": round(plan["absorbed_s"], 4),
                    }
                )
            r_start, r_end = _cut_span_times(
                timeline, op.restore_word_start, op.restore_word_end
            )
            following = [
                i
                for i in range(op.restore_word_end, len(words))
                if timeline.word_status(i) == "kept"
            ]
            ranges = add_span(
                ranges, r_start, r_end, following[0] if following else None, words
            )
            applied.append(op.comment_id)

    new_cuts["kept_ranges"] = ranges
    after = _plan_stats(words, ranges)
    new_cuts["output_duration_s"] = after["output_duration_s"]
    new_cuts["words_cut"] = after["cut_words"]
    new_cuts["words_total"] = len(words)
    if pause_updates and isinstance(new_cuts.get("join_pauses_s"), list):
        pauses = list(new_cuts["join_pauses_s"])
        for join_index, target in pause_updates.items():
            if join_index < len(pauses):
                pauses[join_index] = target
        new_cuts["join_pauses_s"] = pauses
    new_cuts["direction_layer"] = {
        "tool": "talk-to-your-editor",
        "applied_comment_ids": applied,
    }

    audit = {
        "before": _plan_stats(words, timeline.ranges),
        "after": after,
        "applied_comment_ids": applied,
        "absorbed_slivers": absorbed_slivers,
        "flagged_comment_ids": [
            v.operation.comment_id for v in verdicts if v.status == "flag"
        ],
        "rejected_comment_ids": [
            v.operation.comment_id for v in verdicts if v.status == "reject"
        ],
        "verdicts": [v.to_dict() for v in verdicts],
    }
    return new_cuts, audit
