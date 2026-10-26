"""Timeline mapping between the finished edit and the source recording.

The foundation runner exports two files this layer reads:

- ``transcript.json``: ``words`` is a list of ``{word, start, end}`` in
  source seconds; word indices everywhere refer to this list.
- ``cuts.json``: ``kept_ranges`` is the finished edit as an ordered list of
  source ranges. List order is the output order. Ordinary ranges carry only
  ``start``/``end``. Special inserts (signature breath, freeze frames) carry
  ``insert_kind`` and/or separate ``audio_start``/``audio_end`` donor times;
  they are preserved untouched and can never be targeted by an operation.

The output timestamp of a kept range is the cumulative sum of the durations
of every range before it in list order.
"""

from __future__ import annotations

import math
from typing import Any, Optional

EPS = 1e-6


def is_special_range(rng: dict) -> bool:
    """A kept range is special if it is an insert or uses donor audio."""
    return "insert_kind" in rng or "audio_start" in rng or "audio_end" in rng


def quantize_floor(value: float, fps: Optional[float]) -> float:
    if not fps:
        return value
    return math.floor(value * fps + EPS) / fps


def quantize_ceil(value: float, fps: Optional[float]) -> float:
    if not fps:
        return value
    return math.ceil(value * fps - EPS) / fps


def quantize_round(value: float, fps: Optional[float]) -> float:
    if not fps:
        return value
    return round(value * fps) / fps


class Timeline:
    """Maps finished-edit output times to source times and word indices."""

    def __init__(self, transcript: dict, cuts: dict):
        self.transcript = transcript
        self.cuts = cuts
        self.words: list[dict] = transcript.get("words", [])
        self.ranges: list[dict] = cuts.get("kept_ranges", [])
        self.source_duration = float(
            cuts.get("source_duration_s") or transcript.get("duration") or 0.0
        )
        fq = cuts.get("frame_quantization") or {}
        self.fps = float(fq["fps"]) if fq.get("fps") else None
        self.segments = self._build_segments(self.ranges)
        self.output_duration = self.segments[-1]["output_end"] if self.segments else 0.0

    @staticmethod
    def _build_segments(ranges: list[dict]) -> list[dict]:
        segments = []
        cursor = 0.0
        for index, rng in enumerate(ranges):
            duration = float(rng["end"]) - float(rng["start"])
            segments.append(
                {
                    "range_index": index,
                    "output_start": cursor,
                    "output_end": cursor + duration,
                    "duration": duration,
                    "special": is_special_range(rng),
                    "range": rng,
                }
            )
            cursor += duration
        return segments

    # -- word status -----------------------------------------------------

    def _word_midpoint(self, index: int) -> float:
        word = self.words[index]
        return (float(word["start"]) + float(word["end"])) / 2.0

    def word_status(self, index: int) -> str:
        """'kept', 'special', or 'cut' for a transcript word index."""
        mid = self._word_midpoint(index)
        in_special = False
        for rng in self.ranges:
            if float(rng["start"]) - EPS <= mid <= float(rng["end"]) + EPS:
                if is_special_range(rng):
                    in_special = True
                else:
                    return "kept"
        return "special" if in_special else "cut"

    def kept_word_indices(self) -> list[int]:
        return [i for i in range(len(self.words)) if self.word_status(i) == "kept"]

    def words_in_range(self, rng: dict) -> list[int]:
        start, end = float(rng["start"]), float(rng["end"])
        return [
            i
            for i in range(len(self.words))
            if start - EPS <= self._word_midpoint(i) <= end + EPS
        ]

    # -- output mapping ----------------------------------------------------

    def map_output_time(self, output_time: float) -> dict:
        """Map one finished-edit timestamp back to source and word index.

        Special segments map as protected/special: they carry no editable
        word, so ``word_index`` is None and ``special`` is True.
        """
        if output_time < -EPS or output_time > self.output_duration + EPS:
            raise ValueError(
                f"output_time_out_of_range:{output_time} not in "
                f"[0, {self.output_duration}]"
            )
        t = min(max(output_time, 0.0), self.output_duration)
        segment = self.segments[-1]
        for candidate in self.segments:
            if candidate["output_start"] - EPS <= t < candidate["output_end"] - EPS:
                segment = candidate
                break
            if abs(t - candidate["output_end"]) <= EPS:
                segment = candidate
        rng = segment["range"]
        offset = t - segment["output_start"]
        source_time = float(rng["start"]) + offset
        result: dict[str, Any] = {
            "output_time": round(t, 4),
            "range_index": segment["range_index"],
            "source_time": round(source_time, 4),
            "special": segment["special"],
            "protected": segment["special"],
            "word_index": None,
            "word": None,
        }
        if "audio_start" in rng:
            result["audio_source_time"] = round(float(rng["audio_start"]) + offset, 4)
        if segment["special"]:
            result["insert_kind"] = rng.get("insert_kind")
            return result
        word_index = self._word_at_source_time(source_time, rng)
        result["word_index"] = word_index
        if word_index is not None:
            result["word"] = self.words[word_index]["word"]
        return result

    def _word_at_source_time(self, source_time: float, rng: dict) -> Optional[int]:
        candidates = self.words_in_range(rng)
        if not candidates:
            return None
        for i in candidates:
            word = self.words[i]
            if float(word["start"]) - EPS <= source_time <= float(word["end"]) + EPS:
                return i
        earlier = [i for i in candidates if float(self.words[i]["start"]) <= source_time]
        return earlier[-1] if earlier else candidates[0]

    # -- joins ---------------------------------------------------------------

    def joins(self) -> list[dict]:
        """Join j sits between kept_ranges[j] and kept_ranges[j + 1]."""
        out = []
        for j in range(len(self.ranges) - 1):
            out.append(
                {
                    "join_index": j,
                    "output_time": round(self.segments[j]["output_end"], 4),
                    "prev_range_index": j,
                    "next_range_index": j + 1,
                    "prev_special": is_special_range(self.ranges[j]),
                    "next_special": is_special_range(self.ranges[j + 1]),
                }
            )
        return out

    # -- protected spans -----------------------------------------------------

    def protected_spans(self, profile: Optional[dict] = None) -> list[dict]:
        """Protected word spans from the creator profile and the cuts plan.

        Profile spans use this package's half-open convention. Foundation
        records (curation holds and signature/post-edit records in cuts.json)
        use inclusive ``word_end`` and are converted here.
        """
        spans: list[dict] = []
        if profile:
            for entry in profile.get("protected_spans", []):
                spans.append(
                    {
                        "word_start": int(entry["word_start"]),
                        "word_end": int(entry["word_end"]),
                        "label": entry.get("label", "creator profile"),
                        "source": "profile",
                    }
                )
        curation = self.cuts.get("curation") or {}
        for hold in curation.get("holds", []):
            if "word_start" in hold and "word_end" in hold:
                spans.append(
                    {
                        "word_start": int(hold["word_start"]),
                        "word_end": int(hold["word_end"]) + 1,
                        "label": hold.get("rule", "curation hold"),
                        "source": "curation",
                    }
                )
        for edit in self.cuts.get("post_edits", []) or []:
            kind = str(edit.get("kind", ""))
            structural = (
                "signature" in kind or "prayer" in kind or edit.get("protected")
            )
            if not structural:
                continue
            if "word_start" in edit and "word_end" in edit:
                spans.append(
                    {
                        "word_start": int(edit["word_start"]),
                        "word_end": int(edit["word_end"]) + 1,
                        "label": kind,
                        "source": "post_edits",
                    }
                )
            elif "placed_between_words" in edit:
                first, second = edit["placed_between_words"][:2]
                spans.append(
                    {
                        "word_start": int(first),
                        "word_end": int(second) + 1,
                        "label": kind,
                        "source": "post_edits",
                    }
                )
        for key in ("signature", "prayer"):
            node = self.cuts.get(key)
            if isinstance(node, dict):
                for sub in node.values():
                    if isinstance(sub, dict) and "word_start" in sub and "word_end" in sub:
                        spans.append(
                            {
                                "word_start": int(sub["word_start"]),
                                "word_end": int(sub["word_end"]) + 1,
                                "label": key,
                                "source": "cuts",
                            }
                        )
        return spans


# -- pure range math (shared by the verifier and the applier) -----------------


def subtract_span(
    ranges: list[dict], cut_start: float, cut_end: float
) -> list[dict]:
    """Remove [cut_start, cut_end] from ordinary ranges; specials untouched."""
    out: list[dict] = []
    for rng in ranges:
        if is_special_range(rng):
            out.append(rng)
            continue
        start, end = float(rng["start"]), float(rng["end"])
        if cut_end <= start + EPS or cut_start >= end - EPS:
            out.append(rng)
            continue
        if cut_start > start + EPS:
            left = dict(rng)
            left["end"] = cut_start
            out.append(left)
        if cut_end < end - EPS:
            right = dict(rng)
            right["start"] = cut_end
            out.append(right)
    return out


def add_span(
    ranges: list[dict],
    new_start: float,
    new_end: float,
    insert_before_word: Optional[int],
    timeline_words: list[dict],
) -> list[dict]:
    """Add a restored ordinary range, merging overlaps, keeping list order.

    The restored range takes the output position of the first ordinary range
    whose words come after the restored span; specials keep their places.
    """
    overlapping = [
        rng
        for rng in ranges
        if not is_special_range(rng)
        and float(rng["start"]) <= new_end + EPS
        and new_start <= float(rng["end"]) + EPS
    ]
    if overlapping:
        merged_start = min([new_start] + [float(r["start"]) for r in overlapping])
        merged_end = max([new_end] + [float(r["end"]) for r in overlapping])
        out: list[dict] = []
        placed = False
        for rng in ranges:
            if rng in overlapping:
                if not placed:
                    replacement = dict(rng)
                    replacement["start"], replacement["end"] = merged_start, merged_end
                    out.append(replacement)
                    placed = True
                continue
            out.append(rng)
        return out
    new_range = {"start": new_start, "end": new_end}
    kept: list[dict] = list(ranges)
    if insert_before_word is None:
        kept.append(new_range)
        return kept
    position = len(kept)
    for index, rng in enumerate(kept):
        if is_special_range(rng):
            continue
        inside = [
            i
            for i, w in enumerate(timeline_words)
            if float(rng["start"]) - EPS
            <= (float(w["start"]) + float(w["end"])) / 2.0
            <= float(rng["end"]) + EPS
        ]
        if inside and min(inside) > insert_before_word:
            position = index
            break
    kept.insert(position, new_range)
    return kept
