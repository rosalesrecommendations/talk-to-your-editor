"""Manual timeline edits: hand transforms of the plan's kept ranges.

v0.16. The automatic edit is a plan: an ordered list of kept
ranges in the session's revised cuts (list order is output
order). Until now the only way to change that list was word by
word, through the transcript. These are the timeline's own
operations, the small set every editor Justin named (Descript
first, then Premiere, CapCut, Final Cut Pro) agrees on:

- split a piece at the playhead,
- drag a piece's edge earlier or later (trim),
- set one join's sound crossfade,
- cut / copy / paste pieces, paste landing at the playhead,
- drag a piece to a new place in the order,
- delete a piece.

Each operation is a pure transform: it takes the current cuts
dict and the timeline built from it, and returns a NEW cuts
dict plus a small info dict. Nothing here writes a file or
knows about HTTP; the web handlers land the result in
``revised_cuts.json`` under the same history checkpoint every
other mutation uses, so undo and redo cover these operations
exactly as they cover word edits, and the splice stamp, the
band, the transcript's word states, and the edited clock all
follow from the one plan, as they always have.

The protections are the plan's own: a special range (a
signature breath, a freeze frame, anything with donor audio)
can never be targeted, swallowed, or crossed; a protected word
can never be trimmed or deleted away; a piece keeps at least
the verifier's minimum length (MIN_RANGE_S). A refusal is a
plain sentence in a ManualEditRefused, and changes nothing.

Crossfades live on the piece they fade INTO, as ``fade_in_s``
on the ordinary range itself. Every structural operation above
changes which joins exist, and a positional list of fades
would silently point at the wrong joins after a split or a
move; a fade carried by its piece survives all of them and
keeps its meaning ("the sound blends into this piece"). The
first piece's fade is meaningless (nothing plays before it)
and is stripped whenever a plan passes through here.
"""

from __future__ import annotations

import copy
from typing import Optional

from .timeline import Timeline, is_special_range, quantize_ceil, quantize_floor, quantize_round
from .verifier import MIN_RANGE_S

EPS = 1e-6
MAX_FADE_S = 0.50


class ManualEditRefused(ValueError):
    """A manual edit that must not happen, with its plain reason."""


# -- shared helpers -------------------------------------------------------


def _mid(word: dict) -> float:
    return (float(word["start"]) + float(word["end"])) / 2.0


def _words_in(rng: dict, words: list) -> list:
    start, end = float(rng["start"]), float(rng["end"])
    return [
        i
        for i, w in enumerate(words)
        if start - EPS <= _mid(w) <= end + EPS
    ]


def compute_join_pauses(ranges: list, words: list) -> list:
    """The plan's join pauses, measured the house way.

    A join's pause is the tail of quiet after the left piece's
    last word plus the lead-in before the right piece's first
    word: the same two pieces of quiet the silence tools and
    the gap erase measure. A piece with no words contributes
    no tail or lead-in of its own.
    """
    pauses = []
    for j in range(len(ranges) - 1):
        prev, nxt = ranges[j], ranges[j + 1]
        prev_words = _words_in(prev, words)
        next_words = _words_in(nxt, words)
        tail = (
            float(prev["end"]) - float(words[prev_words[-1]]["end"])
            if prev_words
            else 0.0
        )
        head = (
            float(words[next_words[0]]["start"]) - float(nxt["start"])
            if next_words
            else 0.0
        )
        pauses.append(round(max(0.0, tail) + max(0.0, head), 4))
    return pauses


def _protected_word_indices(timeline: Timeline, profile: Optional[dict]) -> set:
    out = set()
    for span in timeline.protected_spans(profile):
        for i in range(span["word_start"], span["word_end"]):
            if 0 <= i < len(timeline.words):
                out.add(i)
    return out


def _finish(cuts: dict, ranges: list, timeline: Timeline, op_id: str) -> dict:
    """Assemble the new plan dict around transformed ranges."""
    new_cuts = copy.deepcopy(cuts)
    if ranges:
        ranges[0].pop("fade_in_s", None)
    new_cuts["kept_ranges"] = ranges
    fresh = Timeline(timeline.transcript, new_cuts)
    kept = len(fresh.kept_word_indices())
    new_cuts["output_duration_s"] = round(fresh.output_duration, 4)
    new_cuts["words_cut"] = len(timeline.words) - kept
    new_cuts["words_total"] = len(timeline.words)
    if isinstance(cuts.get("join_pauses_s"), list):
        new_cuts["join_pauses_s"] = compute_join_pauses(ranges, timeline.words)
    new_cuts["direction_layer"] = {
        "tool": "talk-to-your-editor",
        "applied_comment_ids": [op_id],
    }
    return new_cuts


def _working_ranges(cuts: dict) -> list:
    return copy.deepcopy(cuts.get("kept_ranges", []))


def _target_range(ranges: list, range_index: int) -> dict:
    if not 0 <= range_index < len(ranges):
        raise ManualEditRefused("That piece is not in the edit anymore.")
    rng = ranges[range_index]
    if is_special_range(rng):
        raise ManualEditRefused(
            "That piece is a special insert, so it stays as it is."
        )
    return rng


# -- split ------------------------------------------------------------------


def split_range(
    cuts: dict,
    timeline: Timeline,
    range_index: int,
    source_time: float,
    op_id: str = "timeline-split",
) -> tuple:
    """Split one ordinary piece at a source time; output unchanged."""
    ranges = _working_ranges(cuts)
    rng = _target_range(ranges, range_index)
    start, end = float(rng["start"]), float(rng["end"])
    at = quantize_round(float(source_time), timeline.fps)
    if at - start < MIN_RANGE_S - EPS or end - at < MIN_RANGE_S - EPS:
        raise ManualEditRefused(
            "That is too close to the edge of the piece to split it there."
        )
    left = dict(rng)
    left["end"] = at
    right = dict(rng)
    right["start"] = at
    right.pop("fade_in_s", None)
    ranges[range_index : range_index + 1] = [left, right]
    info = {
        "range_index": range_index,
        "new_range_index": range_index + 1,
        "at_source_s": round(at, 4),
    }
    return _finish(cuts, ranges, timeline, op_id), info


def split_at_output_time(
    cuts: dict, timeline: Timeline, output_time: float, op_id: str = "timeline-split"
) -> tuple:
    """Split the piece under an edited-clock (playhead) time."""
    t = float(output_time)
    for segment in timeline.segments:
        if segment["output_start"] - EPS <= t < segment["output_end"] - EPS:
            if segment["special"]:
                raise ManualEditRefused(
                    "The playhead is on a special insert, so there is "
                    "nothing there to split."
                )
            source_time = float(segment["range"]["start"]) + (
                t - segment["output_start"]
            )
            new_cuts, info = split_range(
                cuts, timeline, segment["range_index"], source_time, op_id
            )
            info["output_time"] = round(t, 4)
            return new_cuts, info
    raise ManualEditRefused("The playhead is not on any piece of the edit.")


# -- trim (edge drag) ---------------------------------------------------------


def trim_edge(
    cuts: dict,
    timeline: Timeline,
    range_index: int,
    edge: str,
    new_time: float,
    profile: Optional[dict] = None,
    op_id: str = "timeline-trim",
) -> tuple:
    """Move one edge of one ordinary piece to a new source time.

    Dragging outward pulls back material the edit cut; dragging
    inward trims more. The edge clamps, in order: at the
    recording's own bounds; so the piece keeps MIN_RANGE_S; so
    an extension never enters another piece's span (ordinary or
    special); and so a shrink never cuts a protected word. The
    info dict names every clamp that bit, so the page can say
    why the edge stopped where it did.
    """
    if edge not in ("start", "end"):
        raise ValueError("edge must be 'start' or 'end'")
    ranges = _working_ranges(cuts)
    rng = _target_range(ranges, range_index)
    start, end = float(rng["start"]), float(rng["end"])
    wanted = quantize_round(float(new_time), timeline.fps)
    clamps: list = []
    protected = _protected_word_indices(timeline, profile)
    protected_here = [
        i for i in _words_in(rng, timeline.words) if i in protected
    ]
    if edge == "start":
        new_start = max(0.0, wanted)
        if new_start < start - EPS:
            # Extending: never enter another piece's span.
            for j, other in enumerate(ranges):
                if j == range_index:
                    continue
                o_start, o_end = float(other["start"]), float(other["end"])
                if o_end > new_start and o_start < start - EPS:
                    if o_end > new_start:
                        new_start = max(new_start, o_end)
                        clamps.append("next-piece")
            if new_start > start:
                new_start = start
        elif new_start > start + EPS and protected_here:
            # Shrinking: protected words stay in.
            first = timeline.words[protected_here[0]]
            limit = quantize_floor(float(first["start"]), timeline.fps)
            if new_start > limit:
                new_start = limit
                clamps.append("protected")
        newest = end - MIN_RANGE_S
        if new_start > newest:
            new_start = quantize_floor(newest, timeline.fps)
            clamps.append("min-length")
        if abs(new_start - start) <= EPS:
            return cuts, {
                "range_index": range_index,
                "edge": edge,
                "changed": False,
                "clamps": clamps,
            }
        rng["start"] = round(new_start, 4)
        applied = new_start
    else:
        new_end = min(float(timeline.source_duration), wanted) \
            if timeline.source_duration else wanted
        if new_end > end + EPS:
            for j, other in enumerate(ranges):
                if j == range_index:
                    continue
                o_start, o_end = float(other["start"]), float(other["end"])
                if o_start < new_end and o_end > end + EPS:
                    new_end = min(new_end, o_start)
                    clamps.append("next-piece")
            if new_end < end:
                new_end = end
        elif new_end < end - EPS and protected_here:
            last = timeline.words[protected_here[-1]]
            limit = quantize_ceil(float(last["end"]), timeline.fps)
            if new_end < limit:
                new_end = limit
                clamps.append("protected")
        newest = start + MIN_RANGE_S
        if new_end < newest:
            new_end = quantize_ceil(newest, timeline.fps)
            clamps.append("min-length")
        if abs(new_end - end) <= EPS:
            return cuts, {
                "range_index": range_index,
                "edge": edge,
                "changed": False,
                "clamps": clamps,
            }
        rng["end"] = round(new_end, 4)
        applied = new_end
    info = {
        "range_index": range_index,
        "edge": edge,
        "changed": True,
        "applied_s": round(applied, 4),
        "wanted_s": round(wanted, 4),
        "clamps": clamps,
    }
    return _finish(cuts, ranges, timeline, op_id), info


# -- delete -------------------------------------------------------------------


def delete_range(
    cuts: dict,
    timeline: Timeline,
    range_index: int,
    profile: Optional[dict] = None,
    op_id: str = "timeline-delete",
) -> tuple:
    """Take one ordinary piece out; the edit closes up (ripple)."""
    ranges = _working_ranges(cuts)
    rng = _target_range(ranges, range_index)
    ordinary = [r for r in ranges if not is_special_range(r)]
    if len(ordinary) <= 1:
        raise ManualEditRefused(
            "That is the only piece left in the edit, so it stays."
        )
    protected = _protected_word_indices(timeline, profile)
    if any(i in protected for i in _words_in(rng, timeline.words)):
        raise ManualEditRefused(
            "That piece holds words you protected, so it cannot be "
            "taken out here."
        )
    del ranges[range_index]
    info = {"range_index": range_index}
    return _finish(cuts, ranges, timeline, op_id), info


# -- move (drag to reorder) ---------------------------------------------------


def move_range(
    cuts: dict,
    timeline: Timeline,
    from_index: int,
    to_index: int,
    op_id: str = "timeline-move",
) -> tuple:
    """Move one ordinary piece to a new place in the order.

    A piece may not cross a special insert: specials keep their
    places under every operation. The target clamps to the last
    place before the nearest special in the direction of the
    move, and the info dict says so when it clamped.
    """
    ranges = _working_ranges(cuts)
    _target_range(ranges, from_index)
    if not 0 <= to_index < len(ranges):
        raise ManualEditRefused("That place is not in the edit anymore.")
    if is_special_range(ranges[to_index]) and to_index != from_index:
        # Dropping ONTO a special's slot means just past it in
        # the direction of travel; the clamp below settles it.
        pass
    clamped_to = to_index
    clamps: list = []
    if to_index > from_index:
        for j in range(from_index + 1, to_index + 1):
            if is_special_range(ranges[j]):
                clamped_to = j - 1
                clamps.append("special")
                break
    elif to_index < from_index:
        for j in range(from_index - 1, to_index - 1, -1):
            if is_special_range(ranges[j]):
                clamped_to = j + 1
                clamps.append("special")
                break
    if clamped_to == from_index:
        if clamps:
            raise ManualEditRefused(
                "A special insert sits that way, and pieces cannot "
                "move across one."
            )
        return cuts, {
            "from_index": from_index,
            "to_index": to_index,
            "changed": False,
            "clamps": clamps,
        }
    piece = ranges.pop(from_index)
    ranges.insert(clamped_to, piece)
    info = {
        "from_index": from_index,
        "to_index": clamped_to,
        "changed": True,
        "clamps": clamps,
    }
    return _finish(cuts, ranges, timeline, op_id), info


# -- paste --------------------------------------------------------------------


def _clean_span(span: dict, timeline: Timeline) -> dict:
    try:
        start = float(span["start"])
        end = float(span["end"])
    except (KeyError, TypeError, ValueError):
        raise ManualEditRefused("That copied piece has no usable times.")
    if end - start < EPS:
        raise ManualEditRefused("That copied piece is empty.")
    if start < -EPS or (
        timeline.source_duration and end > timeline.source_duration + EPS
    ):
        raise ManualEditRefused("That copied piece sits outside the recording.")
    out = {"start": round(max(0.0, start), 4), "end": round(end, 4)}
    fade = span.get("fade_in_s")
    if isinstance(fade, (int, float)) and 0 < float(fade) <= MAX_FADE_S:
        out["fade_in_s"] = round(float(fade), 4)
    return out


def paste_spans(
    cuts: dict,
    timeline: Timeline,
    spans: list,
    output_time: float,
    op_id: str = "timeline-paste",
) -> tuple:
    """Insert copies of the given source spans at the playhead.

    The playhead's piece splits to make room when the playhead
    sits well inside an ordinary piece; near an edge the paste
    lands at that edge instead, and on a special insert it lands
    just after the insert. Pasted pieces are ordinary ranges and
    are NOT merged into their neighbors: a paste is a deliberate
    duplicate, the way every editor's paste behaves.
    """
    if not spans:
        raise ManualEditRefused("Nothing has been copied yet.")
    cleaned = [_clean_span(span, timeline) for span in spans]
    ranges = _working_ranges(cuts)
    t = float(output_time)
    insert_at = len(ranges)
    split_done = False
    for segment in timeline.segments:
        if segment["output_start"] - EPS <= t < segment["output_end"] - EPS:
            index = segment["range_index"]
            if segment["special"]:
                insert_at = index + 1
                break
            rng = ranges[index]
            source_time = float(rng["start"]) + (t - segment["output_start"])
            at = quantize_round(source_time, timeline.fps)
            if (
                at - float(rng["start"]) >= MIN_RANGE_S - EPS
                and float(rng["end"]) - at >= MIN_RANGE_S - EPS
            ):
                left = dict(rng)
                left["end"] = at
                right = dict(rng)
                right["start"] = at
                right.pop("fade_in_s", None)
                ranges[index : index + 1] = [left, right]
                insert_at = index + 1
                split_done = True
            else:
                insert_at = (
                    index
                    if source_time - float(rng["start"])
                    < float(rng["end"]) - source_time
                    else index + 1
                )
            break
    else:
        if t < 0:
            insert_at = 0
    for offset, span in enumerate(cleaned):
        ranges.insert(insert_at + offset, dict(span))
    info = {
        "inserted_at": insert_at,
        "count": len(cleaned),
        "split_piece": split_done,
        "range_indices": list(range(insert_at, insert_at + len(cleaned))),
    }
    return _finish(cuts, ranges, timeline, op_id), info


# -- crossfade at a join --------------------------------------------------------


def set_join_fade(
    cuts: dict,
    timeline: Timeline,
    join_index: int,
    fade_s: Optional[float],
    op_id: str = "timeline-fade",
) -> tuple:
    """Set (or clear) the sound crossfade at one join.

    The fade is stored on the piece the join plays into. A join
    into a special insert keeps the plan's ordinary behavior:
    specials are never targeted, so the fade refuses there.
    """
    ranges = _working_ranges(cuts)
    if not 0 <= join_index < len(ranges) - 1:
        raise ManualEditRefused("That join is not in the edit anymore.")
    target = ranges[join_index + 1]
    if is_special_range(target):
        raise ManualEditRefused(
            "That join leads into a special insert, so it keeps a "
            "plain cut."
        )
    if fade_s is None or float(fade_s) <= 0:
        target.pop("fade_in_s", None)
        applied = 0.0
    else:
        fade = float(fade_s)
        if fade > MAX_FADE_S + EPS:
            raise ManualEditRefused(
                "A crossfade here can be at most half a second."
            )
        target["fade_in_s"] = round(fade, 4)
        applied = round(fade, 4)
    info = {"join_index": join_index, "fade_s": applied}
    return _finish(cuts, ranges, timeline, op_id), info


def join_fades(ranges: list) -> list:
    """The fade at each join, read off the pieces (None = unset)."""
    out = []
    for j in range(len(ranges) - 1):
        fade = ranges[j + 1].get("fade_in_s")
        out.append(round(float(fade), 4) if isinstance(fade, (int, float)) else None)
    return out
