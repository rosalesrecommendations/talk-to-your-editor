"""SMOOTH, NEVER CHOPPY: the join-quality engine (Message 2R, P0).

Everything here is measured from the recording's own sound, never
from transcript timestamps alone (they drift 50 to 100 ms):

- A frame envelope (10 ms RMS, in dB) is built from the extracted
  WAV. The room's own background level is a low percentile of that
  envelope, so a voice that drops quiet at the end of a sentence is
  judged against the room it was recorded in, not a fixed loudness.
- Each word's real sound bounds are found by walking out from its
  transcript span until the sound falls to the room level. Cuts are
  placed on that measured quiet.
- Breaths are the sounds in the gaps between words that rise above
  the room level without being a word. They are all or nothing: a
  plan boundary may never land inside one.
- The Day 30 defaults (measured from Justin's own edit, brief 8):
  cut tight into the next word (0.04 s before its first sound),
  leave the last word 0.13 s of room inside a sentence and 0.19 s
  at a sentence end, which lands the heard pause at about 0.19 s
  inside a sentence and about 0.25 s between sentences.
- After the edges are set, every join is checked: a click, a
  loudness jump over 3 dB, a cut inside a word, a pause under
  60 ms, a half breath, a piece starting partway through a
  sentence. What can be fixed is fixed (nudge into the quiet,
  lengthen that join's crossfade to 0.1 s); the rest is reported
  as "your call" spots, never cut badly in silence.

The plan's ranges keep the real room sound: edges only ever move
inside the gap between two words' sounds, so a gap is the
recording's own quiet, never digital silence.
"""

from __future__ import annotations

import array
import math
import wave
from pathlib import Path
from typing import Optional

FRAME_S = 0.01
HEAD_IN_S = 0.04          # cut this far before the next word's first sound
TAIL_INSIDE_S = 0.13      # room left after the last word, inside a sentence
TAIL_END_S = 0.19         # room left after the last word, at a sentence end
MIN_PAUSE_S = 0.06        # a heard pause shorter than this is an issue
LONG_FADE_S = 0.10        # crossfade where a join is tight or spliced
TIGHT_PAUSE_S = 0.15      # joins under this heard pause get the long fade
SOUND_ABOVE_ROOM_DB = 3.0   # sound counts as present this far over the room
WORD_END_ABOVE_ROOM_DB = 2.0  # a quiet last syllable still counts as sound
BREATH_MIN_S = 0.08
DANGLING = {"so", "and", "it", "the", "but"}


# -- envelope ---------------------------------------------------------------


def envelope_db(wav_path: Path, frame_s: float = FRAME_S) -> list[float]:
    """RMS level per frame, in dB relative to full scale."""
    with wave.open(str(wav_path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    if width != 2 or not raw:
        return []
    samples = array.array("h")
    samples.frombytes(raw)
    if channels > 1:
        samples = samples[::channels]
    frame = max(1, int(rate * frame_s))
    count = len(samples) // frame
    out: list[float] = []
    for i in range(count):
        chunk = samples[i * frame:(i + 1) * frame]
        if not chunk:
            out.append(-90.0)
            continue
        mean_sq = sum(v * v for v in chunk) / len(chunk)
        rms = math.sqrt(mean_sq)
        out.append(20.0 * math.log10(rms / 32768.0) if rms > 0 else -90.0)
    return out


def room_level_db(env: list[float]) -> float:
    """The room's background level: the 15th percentile frame."""
    if not env:
        return -60.0
    ordered = sorted(env)
    return ordered[int(0.15 * (len(ordered) - 1))]


def _frame(env: list[float], t: float) -> int:
    return max(0, min(len(env) - 1, int(t / FRAME_S)))


def level_at(env: list[float], t: float) -> float:
    if not env:
        return -90.0
    return env[_frame(env, t)]


def window_level_db(env: list[float], start: float, end: float) -> float:
    """Mean-energy level over a window, in dB."""
    if not env or end <= start:
        return -90.0
    a, b = _frame(env, start), _frame(env, end)
    if b <= a:
        return env[a]
    total = sum(10 ** (v / 10.0) for v in env[a:b])
    return 10.0 * math.log10(total / (b - a))


# -- measured sound bounds ----------------------------------------------------


def word_sound_bounds(
    env: list[float], room_db: float, word: dict
) -> tuple[float, float]:
    """Where a word's sound really starts and stops.

    Walks out from the transcript span while the sound stays above
    the room level (a low margin, so a quiet last syllable is not
    clipped), and in from a little outside it to find the first
    real sound. Bounded so a neighbour word is never swallowed:
    the search never crosses the midpoint to the next word's
    transcript span is the caller's job; here the walk is capped
    at 0.35 s each way.
    """
    if not env:
        return float(word["start"]), float(word["end"])
    thresh = room_db + WORD_END_ABOVE_ROOM_DB
    start_t, end_t = float(word["start"]), float(word["end"])
    # First sound: walk back from the transcript start while the
    # sound stays up; the first quiet frame is where it begins.
    found_start = start_t
    t = start_t
    floor_t = max(0.0, start_t - 0.15)
    while t > floor_t:
        if level_at(env, t - FRAME_S) <= thresh:
            found_start = t
            break
        t = round(t - FRAME_S, 6)
    else:
        found_start = floor_t if level_at(env, start_t) > thresh else start_t
    # Last sound: walk forward from the transcript end while the
    # sound stays up, tolerating dips of up to 30 ms inside the
    # word's own tail (a soft syllable is never clipped).
    found_end = end_t
    last_loud = end_t if level_at(env, end_t) > thresh else None
    quiet_run = 0
    t = end_t
    limit = end_t + 0.35
    while t < limit:
        if level_at(env, t) > thresh:
            last_loud = t
            quiet_run = 0
        else:
            quiet_run += 1
            if quiet_run >= 3 and last_loud is not None:
                break
            if last_loud is None and quiet_run >= 3:
                break
        t = round(t + FRAME_S, 6)
    if last_loud is not None:
        found_end = max(end_t, last_loud + FRAME_S)
    return round(found_start, 4), round(found_end, 4)


def breath_segments(
    env: list[float],
    room_db: float,
    gap_start: float,
    gap_end: float,
    word_spans: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Non-word sounds in a gap: contiguous runs above the room.

    A run counts when it lasts at least BREATH_MIN_S and does not
    overlap a word's measured sound span.
    """
    if not env or gap_end - gap_start < BREATH_MIN_S:
        return []
    thresh = room_db + SOUND_ABOVE_ROOM_DB
    out: list[tuple[float, float]] = []
    run_start: Optional[float] = None
    t = gap_start
    while t <= gap_end:
        loud = level_at(env, t) > thresh
        if loud and run_start is None:
            run_start = t
        if (not loud or t >= gap_end) and run_start is not None:
            run_end = t
            if run_end - run_start >= BREATH_MIN_S:
                overlaps = any(
                    run_start < w_end - 0.01 and w_start + 0.01 < run_end
                    for w_start, w_end in word_spans
                )
                if not overlaps:
                    out.append((round(run_start, 4), round(run_end, 4)))
            run_start = None
        t = round(t + FRAME_S, 6)
    return out


def all_word_bounds(
    words: list[dict], env: list[float], room_db: float
) -> list[tuple[float, float]]:
    """Every word's measured sound bounds, kept from overlapping.

    One word's sound can run into the next word's transcript span
    when speech is dense; the clamped bounds put the boundary
    between two neighbouring words at the point their measured
    sounds meet, so a cut placed on a bound never lands inside
    either word's share of the sound.
    """
    raw = [word_sound_bounds(env, room_db, w) for w in words]
    out: list[list[float]] = []
    prev_end = 0.0
    for start, end in raw:
        start = max(start, prev_end)
        end = max(end, start)
        out.append([start, end])
        prev_end = end
    for i in range(len(out) - 2, -1, -1):
        if out[i][1] > out[i + 1][0]:
            out[i][1] = out[i + 1][0]
        if out[i][1] < out[i][0]:
            out[i][1] = out[i][0]
    return [(round(s, 4), round(e, 4)) for s, e in out]


# -- sentences ------------------------------------------------------------------


def sentence_end_indices(words: list[dict], segments: list[dict] | None) -> set[int]:
    """Indices of words that end a sentence.

    From the transcript's own punctuation when the words carry it;
    otherwise from the segment boundaries (a segment ends where a
    sentence ends in the study transcripts this app learns from).
    """
    ends: set[int] = set()
    for i, w in enumerate(words):
        text = str(w.get("word", "")).rstrip("\"'”’)")
        if text.endswith((".", "?", "!")):
            ends.add(i)
    if segments:
        for seg in segments:
            end_t = float(seg.get("end", 0.0))
            best = None
            for i, w in enumerate(words):
                if abs(float(w.get("end", 0.0)) - end_t) < 0.05:
                    best = i
            if best is not None:
                ends.add(best)
    if words:
        ends.add(len(words) - 1)
    return ends


def is_sentence_start(words: list[dict], ends: set[int], index: int) -> bool:
    return index == 0 or (index - 1) in ends


# -- the pass -------------------------------------------------------------------


def _range_words(words: list[dict], rng: dict) -> list[int]:
    start, end = float(rng["start"]), float(rng["end"])
    return [
        i for i, w in enumerate(words)
        if start - 1e-6 <= (float(w["start"]) + float(w["end"])) / 2.0 <= end + 1e-6
    ]


def _is_special(rng: dict) -> bool:
    return "insert_kind" in rng or "audio_start" in rng or "audio_end" in rng


def span_sound_bounds(
    words: list[dict], spans: list[dict]
) -> list[tuple[float, float]]:
    """Each word's sound interval from the speech spans.

    A span edge sits one pad (0.03 s) outside the sound, so the
    sound interval is the span shrunk by the pad at both ends.
    A word no span overlaps (transcript drift) takes the nearest
    span within 0.75 s; a word with no span near it keeps its
    transcript stamp. This is the ruler the span trim cuts by,
    so every later stage measures with it too: the envelope
    ruler reads this recording's -60 dB ambient tone as sound
    and grades span-set edges as cuts inside words that are
    fully there.
    """
    span_list = sorted(
        (float(sp["start"]), float(sp["end"])) for sp in spans
    )
    out: list[tuple[float, float]] = []
    for w in words:
        ws, we = float(w["start"]), float(w["end"])
        best = None
        for s, e in span_list:
            if s <= we and ws <= e:
                if best is None or (e - s) < (best[1] - best[0]):
                    best = (s, e)
        if best is None:
            nearest = None
            for s, e in span_list:
                gap = max(ws - e, s - we, 0.0)
                if gap <= 0.75 and (
                    nearest is None or gap < nearest[0]
                ):
                    nearest = (gap, (s, e))
            best = nearest[1] if nearest else None
        if best is None:
            out.append((ws, we))
        else:
            # Clip to the word's own stamp window: a span can
            # run across a cut (continuous speech with no quiet
            # in it), and the whole span is not this word's
            # sound. The stamp can drift, so the window is the
            # stamp widened by the same 0.35 s the envelope walk
            # allows.
            lo = max(best[0] + SPAN_PAD_S, ws - 0.35)
            hi = min(best[1] - SPAN_PAD_S, we + 0.35)
            if hi < lo:
                lo = hi = (best[0] + best[1]) / 2
            out.append((lo, hi))
    return out


def _gap_breaths_from_spans(
    words: list[dict], spans: list[dict], gap_lo: float, gap_hi: float
) -> list[tuple[float, float]]:
    """Breaths inside a gap, from the speech spans: a span that
    holds no word is a breath or a noise burst, and it is the
    only sound in the gap by construction. (The envelope breath
    finder reads this recording's ambient tone bursts as
    breaths; inside a span gap those bursts ARE spans.)"""
    stamps = [(float(w["start"]), float(w["end"])) for w in words]
    out: list[tuple[float, float]] = []
    for sp in spans:
        s, e = float(sp["start"]), float(sp["end"])
        if e <= gap_lo or s >= gap_hi:
            continue
        if any(s <= we and ws <= e for ws, we in stamps):
            continue
        out.append((max(s, gap_lo), min(e, gap_hi)))
    return out


def smooth_plan(
    words: list[dict],
    ranges: list[dict],
    env: list[float],
    *,
    segments: list[dict] | None = None,
    protected_joins: set[int] | None = None,
    fps: float | None = None,
    spans: list[dict] | None = None,
) -> tuple[list[dict], dict]:
    """Set every join's edges on the measured quiet.

    Returns (new_ranges, report). The input ranges are not
    mutated. Joins that cannot be made smooth without a judgment
    call (a piece starting partway through a sentence the piece
    before does not finish, a dangling start fragment, a breath
    with no room around it) are left exactly as they were and
    reported in ``report['your_call']``.
    """
    protected_joins = protected_joins or set()
    room = room_level_db(env)
    ends = sentence_end_indices(words, segments)
    bounds = (
        span_sound_bounds(words, spans)
        if spans
        else all_word_bounds(words, env, room)
    )
    new_ranges = [dict(r) for r in ranges]
    report = {
        "room_level_db": round(room, 2),
        "joins": 0,
        "edges_set": 0,
        "breaths_cut_whole": 0,
        "breaths_kept_whole": 0,
        "your_call": [],
    }
    for j in range(len(new_ranges) - 1):
        prev_r, next_r = new_ranges[j], new_ranges[j + 1]
        if _is_special(prev_r) or _is_special(next_r):
            continue
        report["joins"] += 1
        if j in protected_joins:
            continue
        prev_words = _range_words(words, prev_r)
        next_words = _range_words(words, next_r)
        if not prev_words or not next_words:
            continue
        last_i, first_i = prev_words[-1], next_words[0]
        # A kept piece may start partway through a sentence only
        # when the piece before it ends that same sentence (the
        # two pieces are neighbours in one sentence). Anything
        # else is flagged, never cut badly.
        starts_mid = not is_sentence_start(words, ends, first_i)
        continues = last_i == first_i - 1
        if starts_mid and not continues:
            # Flagged, never silently fixed: which words are kept
            # is the creator's call here. The edges still get set
            # on the measured sound below, so the splice itself
            # never lands inside a word or half a breath; only the
            # sentence question is left to him.
            report["your_call"].append({
                "join_index": j,
                "kind": "mid_sentence_start",
                "word_index": first_i,
                "time_s": round(float(words[first_i]["start"]), 3),
                "text": str(words[first_i].get("word", "")),
            })
        # A dangling start fragment (a lone "So"/"And"/"It"/"the"
        # whose sentence actually starts later) is flagged for
        # the creator rather than silently kept or cut here; the
        # retake pass owns cutting it with its take.
        first_text = str(words[first_i].get("word", "")).strip(".,!?\"'").lower()
        if (
            first_text in DANGLING
            and len(next_words) >= 2
            and is_sentence_start(words, ends, next_words[1])
            and not continues
        ):
            report["your_call"].append({
                "join_index": j,
                "kind": "dangling_start",
                "word_index": first_i,
                "time_s": round(float(words[first_i]["start"]), 3),
                "text": str(words[first_i].get("word", "")),
            })
        prev_sound_end = bounds[last_i][1]
        next_sound_start = bounds[first_i][0]
        tail = TAIL_END_S if last_i in ends else TAIL_INSIDE_S
        want_end = prev_sound_end + tail
        want_start = next_sound_start - HEAD_IN_S
        gap_lo, gap_hi = prev_sound_end, next_sound_start
        # Breaths in this gap: all or nothing. The default is to
        # cut the whole breath (both edges land outside it); when
        # there is no room to do that and keep the minimum pause,
        # the whole breath is kept instead.
        breaths = (
            _gap_breaths_from_spans(words, spans, gap_lo, gap_hi)
            if spans
            else breath_segments(env, room, gap_lo, gap_hi, bounds)
        )
        for b_start, b_end in breaths:
            room_after = gap_hi - b_end
            room_before = b_start - gap_lo
            if room_before >= 0.02 and room_after >= HEAD_IN_S:
                want_end = min(want_end, b_start - 0.01)
                want_start = max(want_start, b_end + 0.01)
                report["breaths_cut_whole"] += 1
            else:
                want_end = max(want_end, b_end + 0.01)
                report["breaths_kept_whole"] += 1
        # Never overlap the neighbours, never cross a word's sound,
        # and keep at least the minimum heard pause.
        want_end = min(want_end, want_start - 0.01)
        want_end = max(want_end, prev_sound_end + 0.01)
        want_start = max(want_start, want_end + 0.01)
        want_start = min(want_start, next_sound_start - 0.01)
        if want_start - FRAME_S < want_end:
            want_start = want_end + FRAME_S
        # The clamps can push an edge back inside a breath (the
        # kept-whole case near the next word is the common one).
        # Snap out again; if there is no room on either side, the
        # join is the creator's call and the edges stay as planned
        # by the earlier passes.
        for b_start, b_end in breaths:
            if b_start + 0.01 < want_end < b_end - 0.01:
                if b_start - 0.02 >= prev_sound_end + 0.01:
                    want_end = b_start - 0.02
                else:
                    want_end = b_end + 0.02
            if b_start + 0.01 < want_start < b_end - 0.01:
                if b_end + 0.02 <= next_sound_start - 0.01:
                    want_start = b_end + 0.02
                else:
                    want_start = b_start - 0.02
        if (
            want_start > next_sound_start - 0.005
            or want_end < prev_sound_end + 0.005
            or want_start <= want_end
        ):
            # No room to place both edges outside the breath:
            # fall back to the plain Day 30 split on the measured
            # word bounds. The edges are never inside a word this
            # way; the join check then tries the breath again and
            # reports whatever it cannot fix.
            want_end = prev_sound_end + tail
            want_start = next_sound_start - HEAD_IN_S
            want_end = min(want_end, want_start - 0.01)
            want_end = max(want_end, prev_sound_end + 0.01)
            want_start = max(want_start, want_end + 0.01)
            want_start = min(want_start, next_sound_start - 0.01)
        if fps:
            want_end = round(want_end * fps) / fps
            want_start = round(want_start * fps) / fps
        if abs(want_end - float(prev_r["end"])) > 0.001 or abs(
            want_start - float(next_r["start"])
        ) > 0.001:
            report["edges_set"] += 1
        prev_r["end"] = round(want_end, 4)
        next_r["start"] = round(want_start, 4)
    return new_ranges, report


# -- silence measured from the audio, inside kept pieces -----------------------

INTERNAL_SILENCE_MIN_S = 0.30  # trim silences longer than this inside a piece


def audio_silence_runs(
    env: list[float],
    room_db: float,
    *,
    min_s: float = INTERNAL_SILENCE_MIN_S,
    above_room_db: float = WORD_END_ABOVE_ROOM_DB,
) -> list[tuple[float, float]]:
    """Runs of real quiet in the recording, from the sound itself.

    A frame counts as quiet when its level sits at or below the
    room's own background plus the same small margin the word-end
    walk uses, so a quiet syllable is never called silence. Dips
    of up to two frames above the line inside a run are bridged
    (a breath's tail, a click of room noise). Transcript word
    times are never consulted: they stretch over pauses, which is
    exactly how long silences used to survive inside kept pieces
    (Justin's 0.23 Mac test: 66 silences of 0.5 s or more inside
    the output against 1 in his finished edit).
    """
    if not env:
        return []
    thresh = room_db + above_room_db
    runs: list[tuple[float, float]] = []
    start: Optional[int] = None
    loud_streak = 0
    for i, level in enumerate(env):
        if level <= thresh:
            if start is None:
                start = i
            loud_streak = 0
        else:
            if start is not None:
                loud_streak += 1
                if loud_streak > 2:
                    end = i - loud_streak + 1
                    if (end - start) * FRAME_S >= min_s:
                        runs.append((start * FRAME_S, end * FRAME_S))
                    start = None
                    loud_streak = 0
    if start is not None:
        end = len(env)
        if (end - start) * FRAME_S >= min_s:
            runs.append((start * FRAME_S, end * FRAME_S))
    return [(round(a, 4), round(b, 4)) for a, b in runs]


SPAN_PAD_S = 0.03  # the speech-span measure's own edge padding


def trim_internal_silences(
    words: list[dict],
    ranges: list[dict],
    env: list[float],
    *,
    segments: list[dict] | None = None,
    protected_words: set[int] | None = None,
    fps: float | None = None,
    spans: list[dict] | None = None,
    gold_words: set[int] | None = None,
) -> tuple[list[dict], dict]:
    """Trim the quiet inside kept pieces, measured from the audio.

    The measure is the project's speech spans (energy against the
    recording's own floor, the stage0 approach): the gaps between
    spans are the silences, including the quiet at a piece's head
    and tail. Transcript word times never decide; they stretch
    over pauses, and a low fixed line over the room level reads
    this recording's ambient sound as speech, which is how the
    0.23 output kept 66 long silences Justin counted in his Mac
    test while its plan claimed the joins were fine.

    The audio is the truth about words too (0.25.0, Justin's
    0.24 Mac test): Whisper can stamp a word across pure silence
    (his "I'm" sat at 485.92 to 491.04 over room tone at -70 dB),
    and a stamp like that used to anchor its piece and shield
    the quiet around it, because the words around a span gap
    were picked by their stamps. Words are now picked by their
    measured sound bounds, and a word whose sound bounds hold
    nothing above the room level is silent: it anchors nothing,
    and a piece holding only silent words and no speech span is
    dropped, because there is nothing to hear in it.

    Every span gap longer than 0.3 s touching a kept piece is
    trimmed to the Day 30 pause for its spot: the last word's
    tail (0.13 s inside a sentence, 0.19 s at its end) plus the
    0.04 s head into the next word, measured from the span edges
    (a span edge sits one pad, 0.03 s, outside the sound). A span
    that holds no word is a breath or a noise: it stays whole as
    its own piece, with the quiet around it trimmed. Heads and
    tails are trimmed the same way, except the head before the
    very first word, which the opening rule owns. Special pieces
    and protected words (his Keep marks) are never touched.
    Silence is only ever removed, never added. Returns
    (new_ranges, report).
    """
    protected_words = protected_words or set()
    # Gold words (an undecided your-call span) are protected
    # from having their piece dropped, but a gap trim beside
    # them removes no words, so it is not blocked by them: the
    # final sweep's silence ceiling passes the two sets apart
    # so quiet inside a flagged span is still trimmed. Callers
    # that fold gold into protected_words (the smooth pass)
    # keep the old behavior exactly.
    gold_words = gold_words or set()
    ends = sentence_end_indices(words, segments)
    report = {"trimmed": 0, "seconds_removed": 0.0, "details": []}
    if not ranges or not words:
        return [dict(r) for r in ranges], report
    if spans is None:
        # Fallback for callers without the span measure: merge the
        # words' measured sound bounds into phrase spans.
        room = room_level_db(env)
        bounds = all_word_bounds(words, env, room)
        merged: list[list[float]] = []
        for s, e in bounds:
            if merged and s - merged[-1][1] < INTERNAL_SILENCE_MIN_S:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        spans = [
            {"start": s - SPAN_PAD_S, "end": e + SPAN_PAD_S}
            for s, e in merged
        ]
    span_list = sorted(
        (float(sp["start"]), float(sp["end"])) for sp in spans
    )
    if not span_list:
        return [dict(r) for r in ranges], report
    sbounds = span_sound_bounds(words, spans)
    room = room_level_db(env)

    def word_is_silent(wi: int) -> bool:
        """A word the audio says is not there: no frame inside
        its measured sound bounds rises above the room level."""
        s, e = sbounds[wi]
        if e <= s or not env:
            return True
        a, b = _frame(env, s), max(_frame(env, s) + 1, _frame(env, e))
        return max(env[a:b]) <= room + WORD_END_ABOVE_ROOM_DB

    silent_word = [word_is_silent(i) for i in range(len(words))]

    def span_covering(wi: int) -> Optional[tuple[float, float]]:
        w = words[wi]
        ws, we = float(w["start"]), float(w["end"])
        best = None
        for s, e in span_list:
            if s <= we and ws <= e:
                if best is None or (e - s) < (best[1] - best[0]):
                    best = (s, e)
        if best is not None:
            return best
        # Transcript times drift: a word can sit wholly in the
        # quiet between two spans (its span starts a few tenths
        # after its stamped start). Take the nearest span within
        # 0.75 s rather than skipping the word's edge entirely;
        # the Day 30 pair has exactly this at "But" (word stamped
        # 494.92, its span 495.30, and the head quiet survived
        # every pass until this fallback landed).
        nearest = None
        for s, e in span_list:
            gap = max(ws - e, s - we, 0.0)
            if gap <= 0.75 and (
                nearest is None or gap < nearest[0]
            ):
                nearest = (gap, (s, e))
        return nearest[1] if nearest else None

    out: list[dict] = []
    for rng in ranges:
        r_start, r_end = float(rng["start"]), float(rng["end"])
        if _is_special(rng):
            out.append(dict(rng))
            continue
        its = _range_words(words, rng)
        if not its:
            out.append(dict(rng))
            continue
        removals: list[tuple[float, float]] = []

        def trim_gap(gap_lo: float, gap_hi: float, prev_i, next_i,
                     kind: str) -> None:
            """Record the removal that shrinks one quiet stretch."""
            if prev_i is not None and prev_i in protected_words:
                return
            if next_i is not None and next_i in protected_words:
                return
            tail = (
                TAIL_END_S
                if prev_i is not None and prev_i in ends
                else TAIL_INSIDE_S
            )
            if kind == "head":
                keep_from = gap_hi - HEAD_IN_S
                lo, hi = gap_lo, min(keep_from, gap_hi)
            elif kind == "tail":
                keep_to = gap_lo + tail
                lo, hi = max(keep_to, gap_lo), gap_hi
            else:
                lo, hi = gap_lo + tail, gap_hi - HEAD_IN_S
            # A removal may only ever take quiet: clamp it so it
            # never enters any word's measured sound bounds. A
            # word whose sound sits wholly inside the removal, or
            # whose sound covers the removal whole, cancels it:
            # span edges and word bounds can disagree by drift,
            # and the word wins.
            for wi in its:
                ws, we = sbounds[wi]
                if we <= lo or ws >= hi:
                    continue
                if ws <= lo and we >= hi:
                    return
                if lo < ws and we < hi:
                    return
                if ws > lo:
                    hi = min(hi, ws)
                if we < hi:
                    lo = max(lo, we)
            if hi - lo < 0.02:
                return
            removals.append((lo, hi))
            report["trimmed"] += 1
            report["seconds_removed"] = round(
                report["seconds_removed"] + (hi - lo), 4
            )
            report["details"].append({
                "kind": kind,
                "silence_start": round(gap_lo, 4),
                "silence_end": round(gap_hi, 4),
                "removed_s": round(hi - lo, 4),
                "prev_word": prev_i,
                "next_word": next_i,
            })

        first_i, last_i = its[0], its[-1]
        # The words that anchor this piece are the ones the audio
        # says are there; a silent word (a stamp over room tone)
        # anchors nothing.
        head_i = next((wi for wi in its if not silent_word[wi]), None)
        tail_i = next(
            (wi for wi in reversed(its) if not silent_word[wi]), None
        )
        if head_i is None and not any(
            wi in protected_words or wi in gold_words for wi in its
        ) and not any(e > r_start and s < r_end for s, e in span_list):
            # Only silent words and no measured sound at all:
            # there is nothing to hear in this piece. (Skipping
            # the append below drops it from the plan.)
            report["trimmed"] += 1
            report["seconds_removed"] = round(
                report["seconds_removed"] + (r_end - r_start), 4
            )
            report["details"].append({
                "kind": "silent_piece",
                "silence_start": round(r_start, 4),
                "silence_end": round(r_end, 4),
                "removed_s": round(r_end - r_start, 4),
                "prev_word": None,
                "next_word": None,
            })
            continue
        # Head: quiet before the first heard word's span. The head
        # of the whole video belongs to the opening rule, not here.
        first_span = span_covering(head_i) if head_i is not None else None
        if first_span and head_i != 0:
            sound_start = first_span[0] + SPAN_PAD_S
            if sound_start - HEAD_IN_S - r_start > 0.06:
                trim_gap(r_start, sound_start, None, head_i, "head")
        # Tail: quiet after the last heard word's span.
        last_span = span_covering(tail_i) if tail_i is not None else None
        if last_span:
            sound_end = last_span[1] - SPAN_PAD_S
            tail_target = (
                TAIL_END_S if tail_i in ends else TAIL_INSIDE_S
            )
            if r_end - (sound_end + tail_target) > 0.06:
                trim_gap(sound_end, r_end, tail_i, None, "tail")
        # Inside: span gaps within the piece, between its words.
        inner_spans = [
            (s, e) for s, e in span_list
            if e > r_start and s < r_end
        ]
        for (s1, e1), (s2, e2) in zip(inner_spans, inner_spans[1:]):
            gap = s2 - e1
            # The sound-to-sound silence is the span gap plus the
            # pad at both span edges.
            if gap + 2 * SPAN_PAD_S <= INTERNAL_SILENCE_MIN_S:
                continue
            prev_i = None
            next_i = None
            for wi in its:
                # The words around the gap are picked by their
                # measured sound bounds, never their stamps: a
                # word stamped across the silence (Justin's
                # "I'm", stamped 485.92 to 491.04 over room tone)
                # starts, by its sound, where the next span does,
                # so the gap beside it is trimmed like any other.
                if sbounds[wi][1] <= e1 + 0.05:
                    prev_i = wi
                if sbounds[wi][0] >= s2 - 0.05 and next_i is None:
                    next_i = wi
            if prev_i is None or next_i is None or next_i <= prev_i:
                continue
            trim_gap(e1 - SPAN_PAD_S, s2 + SPAN_PAD_S, prev_i, next_i,
                     "inside")
        if not removals:
            out.append(dict(rng))
            continue
        removals.sort()
        cursor = r_start
        for lo, hi in removals:
            if lo - cursor >= 0.05:
                piece = dict(rng)
                piece["start"] = round(cursor, 4)
                piece["end"] = round(lo, 4)
                out.append(piece)
            cursor = max(cursor, hi)
        if r_end - cursor >= 0.05:
            piece = dict(rng)
            piece["start"] = round(cursor, 4)
            piece["end"] = round(r_end, 4)
            out.append(piece)
    if fps:
        for piece in out:
            piece["start"] = round(round(piece["start"] * fps) / fps, 4)
            piece["end"] = round(round(piece["end"] * fps) / fps, 4)
    return out, report


# -- sentence starts ------------------------------------------------------------

MAX_HEAD_WORDS_RESTORED = 8   # a longer missing head is a judgment cut, not a splice
MAX_HEAD_RESTORE_S = 2.5      # restoring a head never re-adds a long stretch
HEAD_STAMP_SLACK_S = 0.35     # a word's sound starts at most this far before its stamp
MAX_FRAGMENT_WORDS_CUT_WITH_TAKE = 4  # a short tail of a cut take goes with its take


def enforce_sentence_starts(
    words: list[dict],
    ranges: list[dict],
    env: list[float],
    *,
    segments: list[dict] | None = None,
    protected_words: set[int] | None = None,
    blocked_words: set[int] | None = None,
    take_start_words: set[int] | None = None,
    fps: float | None = None,
    spans: list[dict] | None = None,
) -> tuple[list[dict], dict]:
    """Every kept piece starts at a sentence start (0.25.0).

    Justin's rule (his 0.24 Mac test, finding 2): a kept piece
    must start at a sentence start; when a cut would land
    mid-sentence, the piece's start backs up to the sentence
    boundary. Pieces that start mid-sentence because the words
    before them were cut are splices: his "Did you" was cut off
    "Did you catch what I said differently there?", and the edit
    opened at "catch". A piece whose previous kept word is the
    word right before it is a pause trim inside one sentence,
    not a splice, and is left alone.

    The zero push (0.25.0) resolves a mid-sentence start the way
    Justin's own finished edit does, in this order:

    1. Restore the missing run: the contiguous stretch of words
       right before the piece's first word that is neither kept
       in an earlier piece nor blocked. Only that stretch is
       missing; the rest of the sentence head is already in the
       piece before (his Day 30 heads measured 9 and 18 words
       that way, over the old whole-head caps, while the truly
       missing runs were 6, 3, 2, and 1 words). The run is
       restored only when none of it is protected or blocked,
       it is short (at most MAX_HEAD_WORDS_RESTORED words and
       MAX_HEAD_RESTORE_S seconds), and the stretch holds no
       measured silence of 0.5 s or more outside the run's own
       stamps, so restoring never brings back dead air the
       trimmer removed (the span trim runs after this guard
       and re-trims any gap inside the restored stretch). The
       run's first word is placed by its envelope sound start,
       or by its span sound start when the envelope start does
       not move the edge (a word the spans place a second
       before its stamp: "December", stamped inside a quiet
       stretch it does not sound in).
    2. A start at a kept take's first word is a line start:
       when every head word was taken out by settled retake
       cuts and the piece starts exactly where the kept take
       starts (``take_start_words``, built by the caller from
       the settled pairs), the edited sentence begins there.
    3. A short tail of a cut take goes with its take: when
       every head word was taken out by settled cuts and the
       kept fragment from here to the sentence end is at most
       MAX_FRAGMENT_WORDS_CUT_WITH_TAKE words, the fragment is
       cut too. In his Day 30 edit the whole first attempt at
       "Hey God, if I ever get cancer..." is gone; the two
       words "my life" are not in it, only in the later line
       that replaced the thought.

    The caller runs this after the join check (whose edge fixes
    can shift a start past a head word) and runs the span trim
    after it, then this guard once more. Anything the tests
    above refuse is left as it was and counted in the report,
    never silently forced. Returns (new_ranges, report).
    """
    protected_words = protected_words or set()
    blocked_words = blocked_words or set()
    take_start_words = take_start_words or set()
    report = {"fixed": 0, "left": 0, "details": []}
    if not words or not ranges or not env:
        return [dict(r) for r in ranges], report
    # Words belong to pieces by their measured sound when spans
    # are known (the same ruler the trimmer and the word scoring
    # use): a word stamped across a piece edge but heard inside
    # the piece is that piece's word, and a stamp over silence
    # is no piece's word. Without spans, stamp midpoints decide.
    if spans:
        sbounds = span_sound_bounds(words, spans)

        def piece_words(rng: dict) -> list[int]:
            lo, hi = float(rng["start"]), float(rng["end"])
            return [
                i for i in range(len(words))
                if lo - 1e-6 <= (sbounds[i][0] + sbounds[i][1]) / 2 <= hi + 1e-6
            ]
    else:
        def piece_words(rng: dict) -> list[int]:
            return _range_words(words, rng)

    room = room_level_db(env)
    ends = sentence_end_indices(words, segments)
    # The head word's sound start, from its own envelope walk.
    # The walk is not clamped to the neighbouring words (the
    # clamped bounds are what cut "Did you" in the first place),
    # but it is held to the word's stamp window: in unbroken
    # fluent speech the walk runs back to the start of the whole
    # unbroken run, seconds before the word, and a start that
    # early is the run's, not the word's. The span measure clips
    # a word's sound to the same 0.35 s window around its stamp.
    starts = [
        max(
            word_sound_bounds(env, room, w)[0],
            float(w["start"]) - HEAD_STAMP_SLACK_S,
        )
        for w in words
    ]
    silence_runs = audio_silence_runs(env, room, min_s=0.5)

    def head_blocked(missing: list[int], new_start: float,
                     piece_start: float) -> bool:
        if len(missing) > MAX_HEAD_WORDS_RESTORED:
            return True
        if piece_start - new_start > MAX_HEAD_RESTORE_S:
            return True
        # A settled cut in the run blocks the restore (backing
        # the edge up would play the cut take's words). A
        # protected word does not (0.27, fix 8): protection
        # means a span is never cut without the creator's yes,
        # and restoring a sentence head only keeps those words
        # in; a your-call span at a sentence head (the "Because
        # if I'm going to be praying..." line's opening words)
        # must not strand the sentence it heads.
        if any(wi in blocked_words for wi in missing):
            return True
        stamp_lo = min(float(words[wi]["start"]) for wi in missing) - 0.1
        stamp_hi = max(float(words[wi]["end"]) for wi in missing) + 0.1
        for a, b in silence_runs:
            if b <= new_start or a >= piece_start:
                continue
            outside = (min(b, stamp_lo) - max(a, new_start)) + (
                min(b, piece_start) - max(a, stamp_hi)
            )
            if outside >= 0.5:
                return True
        return False

    def quantize(t: float) -> float:
        t = round(t, 4)
        if fps:
            t = round(round(t * fps) / fps, 4)
        return t

    out: list[dict] = []
    prev_last: Optional[int] = None
    kept_so_far: set[int] = set()
    for rng in ranges:
        piece = dict(rng)
        if _is_special(piece):
            out.append(piece)
            continue
        its = piece_words(piece)
        if not its:
            out.append(piece)
            continue
        first_i = its[0]
        if not is_sentence_start(words, ends, first_i) and (
            prev_last is None or prev_last != first_i - 1
        ):
            s_i = first_i
            while s_i > 0 and not is_sentence_start(words, ends, s_i):
                s_i -= 1
            head = list(range(s_i, first_i))
            # The missing run: the contiguous stretch right
            # before this piece's first word that no earlier
            # piece kept and no settled cut took. The walk stops
            # at the first kept or blocked word, and never
            # crosses the sentence start.
            run_lo = first_i
            while (
                run_lo - 1 >= s_i
                and (run_lo - 1) not in kept_so_far
                and (run_lo - 1) not in blocked_words
            ):
                run_lo -= 1
            run = list(range(run_lo, first_i))
            resolved = False
            if run:
                # Earliest valid start wins: the envelope start
                # and the span start can disagree by the stamp
                # drift, and a start that moves the edge but
                # still lands after the run's first word sounds
                # leaves that word outside the piece.
                cand_starts = {
                    quantize(max(0.0, cand - HEAD_IN_S))
                    for cand in (
                        [starts[run_lo]]
                        + ([sbounds[run_lo][0]] if spans else [])
                    )
                }
                for new_start in sorted(cand_starts):
                    if new_start >= float(piece["start"]) - 0.005 or (
                        head_blocked(
                            run, new_start, float(piece["start"])
                        )
                    ):
                        continue
                    if spans:
                        # The edge must not swallow a settled
                        # cut's word: in continuous speech the
                        # sound start of the run's first word can
                        # sit before a blocked word's sound, and
                        # backing the edge to it would play words
                        # of the cut take. Clamp the edge to just
                        # past the last blocked sound midpoint in
                        # the way (the ruler's own boundary), and
                        # give up on this candidate if the clamp
                        # passes the run word's own midpoint.
                        swallowed = [
                            (sbounds[wi][0] + sbounds[wi][1]) / 2
                            for wi in range(run_lo)
                            if wi in blocked_words
                            and new_start
                            <= (sbounds[wi][0] + sbounds[wi][1]) / 2
                            < float(piece["start"])
                        ]
                        if swallowed:
                            new_start = quantize(
                                max(swallowed) + 0.011
                            )
                            run_mid = (
                                sbounds[run_lo][0] + sbounds[run_lo][1]
                            ) / 2
                            if not (
                                new_start
                                < float(piece["start"]) - 0.005
                                and new_start < run_mid
                            ):
                                continue
                    # Merge into the previous piece when the
                    # restored run reaches it; the words
                    # between are this sentence's head, not a
                    # new piece. The merged piece starts at
                    # the restored run, not at the previous
                    # piece's old start: keeping the old start
                    # would drop the run's words that sound
                    # before it (the "Why cold water 15" case,
                    # whose head sounds between the two old
                    # edges). The start never crosses the end
                    # of the piece before that, so pieces stay
                    # in order.
                    if (
                        out
                        and not _is_special(out[-1])
                        and new_start <= float(out[-1]["end"]) + 0.02
                    ):
                        floor = (
                            float(out[-2]["end"])
                            if len(out) >= 2 else 0.0
                        )
                        out[-1]["start"] = round(
                            max(
                                min(
                                    float(out[-1]["start"]),
                                    new_start,
                                ),
                                floor,
                            ),
                            4,
                        )
                        out[-1]["end"] = max(
                            float(out[-1]["end"]),
                            float(piece["end"]),
                        )
                        out[-1]["end"] = round(
                            float(out[-1]["end"]), 4
                        )
                    else:
                        piece["start"] = new_start
                        out.append(piece)
                    report["fixed"] += 1
                    report["details"].append({
                        "word_index": first_i,
                        "sentence_start_word": s_i,
                        "from_s": round(float(rng["start"]), 4),
                        "to_s": new_start,
                        "how": "restore_run",
                        "run_words": run,
                    })
                    prev_last = its[-1]
                    kept_so_far.update(its)
                    resolved = True
                    break
            if resolved:
                # The restore above already appended or merged
                # the piece and updated the trackers.
                continue
            if not resolved and (
                first_i in take_start_words
                and head
                and all(wi in blocked_words for wi in head)
            ):
                # The edited sentence begins at the kept take:
                # every head word belongs to a settled cut, and
                # this piece starts where that take starts.
                report["details"].append({
                    "word_index": first_i,
                    "sentence_start_word": s_i,
                    "from_s": round(float(rng["start"]), 4),
                    "to_s": round(float(rng["start"]), 4),
                    "how": "take_start",
                })
                resolved = True
            if not resolved and head and all(
                wi in blocked_words for wi in head
            ):
                e_i = first_i
                while e_i < len(words) - 1 and e_i not in ends:
                    e_i += 1
                fragment = list(range(first_i, e_i + 1))
                rest = [wi for wi in its if wi > e_i]
                if (
                    len(fragment) <= MAX_FRAGMENT_WORDS_CUT_WITH_TAKE
                    and not any(
                        wi in protected_words for wi in fragment
                    )
                ):
                    report["fixed"] += 1
                    report["details"].append({
                        "word_index": first_i,
                        "sentence_start_word": s_i,
                        "from_s": round(float(rng["start"]), 4),
                        "to_s": None,
                        "how": "fragment_cut_with_take",
                        "fragment_words": fragment,
                    })
                    resolved = True
                    if rest:
                        piece["start"] = quantize(
                            max(
                                float(rng["start"]),
                                starts[rest[0]] - HEAD_IN_S,
                            )
                        )
                        out.append(piece)
                        prev_last = its[-1]
                        kept_so_far.update(rest)
                    # With no rest the piece held only the
                    # fragment; it is dropped, and prev_last keeps
                    # naming the last word actually kept.
                    continue
            if not resolved:
                report["left"] += 1
                report["details"].append({
                    "word_index": first_i,
                    "sentence_start_word": s_i,
                    "from_s": round(float(rng["start"]), 4),
                    "to_s": None,
                })
                out.append(piece)
                prev_last = its[-1]
                kept_so_far.update(its)
                continue
            out.append(piece)
            prev_last = its[-1]
            kept_so_far.update(its)
            continue
        out.append(piece)
        prev_last = its[-1]
        kept_so_far.update(its)
    return out, report


# -- the join check ---------------------------------------------------------------


def join_check(
    words: list[dict],
    ranges: list[dict],
    env: list[float],
    *,
    segments: list[dict] | None = None,
    fps: float | None = None,
    spans: list[dict] | None = None,
    take_start_words: set[int] | None = None,
    measure_only: bool = False,
) -> tuple[list[dict], dict]:
    """Scan every join; fix what can be fixed; report the rest.

    Returns (new_ranges, check) where check carries per-join
    issues, the fixes applied, the remaining "your call" spots,
    and each join's heard pause so callers can take medians.
    Fixes: a boundary inside a word's sound or inside a breath
    moves to the quietest point of the gap; a click or loudness
    jump lengthens that join's crossfade (``fade_in_s`` on the
    next range, the render's own per-join fade).

    A spot is reported only for a join that still fails one of
    these measured checks after the fixes: a click, a loudness
    jump, a cut inside a word, a kept pause under 60 ms, a half
    breath, or a mid-sentence start. A start at a kept take's
    first word (``take_start_words``, from settled retake cuts)
    is the edited sentence's start, not a mid-sentence start:
    the sentence guard's own rule (0.25, the zero push).
    ``joins_smooth`` counts the joins with no remaining failure,
    fixed joins included: a join the check fixed is smooth. A
    click or loudness failure whose remedy, the long crossfade,
    is already on the join counts as fixed: the remedy is the
    whole treatment the engine has for a level jump, so a join
    already carrying it is handled, not a review spot.

    ``measure_only`` runs the same measures without moving an
    edge or setting a fade: the smooth pass's final check uses
    it on the finished plan, after the sentence guard has had
    the last word on placement, so the review spots describe
    the plan as it stands and the check itself cannot undo
    the guard's work.
    """
    room = room_level_db(env)
    ends = sentence_end_indices(words, segments)
    bounds = (
        span_sound_bounds(words, spans)
        if spans
        else all_word_bounds(words, env, room)
    )
    take_start_words = take_start_words or set()
    if spans:
        # Words belong to pieces by their measured sound, the
        # same ruler the sentence guard and the span trim use:
        # a stamp can sit over silence or drift across an edge.
        def range_words(rng: dict) -> list[int]:
            lo, hi = float(rng["start"]), float(rng["end"])
            return [
                i for i in range(len(words))
                if lo - 1e-6 <= (bounds[i][0] + bounds[i][1]) / 2 <= hi + 1e-6
            ]
    else:
        def range_words(rng: dict) -> list[int]:
            return _range_words(words, rng)
    new_ranges = [dict(r) for r in ranges]
    issues: list[dict] = []
    pauses: list[dict] = []
    fixed = 0
    for j in range(len(new_ranges) - 1):
        prev_r, next_r = new_ranges[j], new_ranges[j + 1]
        if _is_special(prev_r) or _is_special(next_r):
            continue
        prev_words = range_words(prev_r)
        next_words = range_words(next_r)
        if not prev_words or not next_words:
            continue
        last_i, first_i = prev_words[-1], next_words[0]
        prev_sound_end = bounds[last_i][1]
        next_sound_start = bounds[first_i][0]
        heard_pause = max(
            0.0,
            (float(prev_r["end"]) - prev_sound_end)
            + (next_sound_start - float(next_r["start"])),
        )
        pauses.append({
            "join_index": j,
            "pause_s": round(heard_pause, 4),
            "between_sentences": last_i in ends,
            "output_time_s": None,
        })
        kinds: list[str] = []
        # Cut inside a word: a boundary strictly inside a word's
        # measured sound span.
        for label, boundary in (
            ("prev", float(prev_r["end"])), ("next", float(next_r["start"]))
        ):
            for wi in (last_i, first_i):
                s, e = bounds[wi]
                if s + 0.01 < boundary < e - 0.01:
                    kinds.append("cut_inside_word")
        if heard_pause < MIN_PAUSE_S:
            kinds.append("pause_under_60ms")
        # Half breath: a boundary inside a breath segment.
        gap_breaths = (
            _gap_breaths_from_spans(
                words, spans, prev_sound_end, next_sound_start
            )
            if spans
            else breath_segments(
                env, room, prev_sound_end, next_sound_start, bounds,
            )
        )
        for b_start, b_end in gap_breaths:
            for boundary in (float(prev_r["end"]), float(next_r["start"])):
                if b_start + 0.01 < boundary < b_end - 0.01:
                    kinds.append("half_breath")
        # Loudness jump and click, measured on the sound itself.
        before = window_level_db(env, float(prev_r["end"]) - 0.10, float(prev_r["end"]))
        after = window_level_db(env, float(next_r["start"]), float(next_r["start"]) + 0.10)
        if abs(after - before) > 3.0 and before > room + 6 and after > room + 6:
            kinds.append("loudness_jump")
        edge_jump = abs(
            level_at(env, float(prev_r["end"]) - FRAME_S)
            - level_at(env, float(next_r["start"]))
        )
        if edge_jump > 14.0 and min(
            level_at(env, float(prev_r["end"]) - FRAME_S),
            level_at(env, float(next_r["start"])),
        ) > room + 10:
            kinds.append("click")
        if (
            not is_sentence_start(words, ends, first_i)
            and last_i != first_i - 1
            and first_i not in take_start_words
        ):
            kinds.append("mid_sentence_start")
        if not kinds:
            continue
        kinds = sorted(set(kinds))
        entry = {
            "join_index": j,
            "kinds": kinds,
            "time_s": round(float(words[first_i]["start"]), 3),
            "fixed": False,
        }
        # Fixes that can be made without a judgment call. An
        # entry counts as fixed only when every failure it
        # carries has its remedy in place (0.25, finding 6):
        # before this, any one fix marked the whole entry fixed,
        # and a level failure whose crossfade was already set
        # could never be marked fixed at all.
        movable = {"cut_inside_word", "half_breath", "pause_under_60ms"} & set(kinds)
        movable_fixed = not movable
        if (
            movable
            and not measure_only
            and next_sound_start - prev_sound_end >= MIN_PAUSE_S + 0.02
        ):
            # Move both edges to the quietest point of the gap,
            # keeping the Day 30 split of the heard pause.
            best_t, best_level = prev_sound_end, None
            t = prev_sound_end
            while t <= next_sound_start:
                lv = level_at(env, t)
                if best_level is None or lv < best_level:
                    best_level, best_t = lv, t
                t = round(t + FRAME_S, 6)
            tail = TAIL_END_S if last_i in ends else TAIL_INSIDE_S
            new_end = min(prev_sound_end + tail, best_t + 0.03)
            new_start = max(next_sound_start - HEAD_IN_S, new_end + 0.01)
            new_start = min(new_start, next_sound_start - 0.01)
            # The quietest point can sit inside a breath (a breath
            # decays to quiet at its tail). A boundary inside a
            # breath is a half breath however quiet it is, so snap
            # each edge out of every gap breath: the end edge to
            # just before the breath (cutting it whole) when there
            # is room, else just after it (keeping it whole); the
            # start edge the mirror image.
            for b_start, b_end in gap_breaths:
                if b_start + 0.01 < new_end < b_end - 0.01:
                    if b_start - 0.02 >= prev_sound_end + 0.02:
                        new_end = b_start - 0.02
                    else:
                        new_end = b_end + 0.02
                if b_start + 0.01 < new_start < b_end - 0.01:
                    if b_end + 0.02 <= next_sound_start - 0.02:
                        new_start = b_end + 0.02
                    else:
                        new_start = b_start - 0.02
            # Re-measure before calling it fixed: the moved edges
            # must be out of every word and every breath, with at
            # least the minimum pause between them.
            def _inside_spans(value: float) -> bool:
                for wi in (last_i, first_i):
                    s, e = bounds[wi]
                    if s + 0.01 < value < e - 0.01:
                        return True
                for b_start, b_end in gap_breaths:
                    if b_start + 0.01 < value < b_end - 0.01:
                        return True
                return False

            heard_after = (new_end - prev_sound_end) + (next_sound_start - new_start)
            if (
                new_start - new_end >= 0.01
                and heard_after >= MIN_PAUSE_S
                and not _inside_spans(new_end)
                and not _inside_spans(new_start)
            ):
                prev_r["end"] = round(new_end, 4)
                next_r["start"] = round(new_start, 4)
                movable_fixed = True
        level_kinds = {"click", "loudness_jump"} & set(kinds)
        level_fixed = not level_kinds
        if level_kinds:
            if float(next_r.get("fade_in_s") or 0) >= LONG_FADE_S:
                # The remedy is already on this join.
                level_fixed = True
            elif not measure_only:
                next_r["fade_in_s"] = LONG_FADE_S
                level_fixed = True
        entry["fixed"] = bool(
            movable_fixed
            and level_fixed
            and "mid_sentence_start" not in kinds
        )
        if entry["fixed"]:
            fixed += 1
        issues.append(entry)
    # Tight or spliced joins get the longer crossfade by default.
    for j in range(len(new_ranges) - 1):
        if measure_only:
            break
        prev_r, next_r = new_ranges[j], new_ranges[j + 1]
        if _is_special(prev_r) or _is_special(next_r):
            continue
        spliced = abs(float(next_r["start"]) - float(prev_r["end"])) > 0.05
        match = next((p for p in pauses if p["join_index"] == j), None)
        tight = match is not None and match["pause_s"] < TIGHT_PAUSE_S
        if (spliced or tight) and "fade_in_s" not in next_r:
            next_r["fade_in_s"] = LONG_FADE_S
    remaining = [i for i in issues if not i["fixed"]]
    check = {
        "joins_checked": len(pauses),
        # Smooth means no failure remains: a join whose only
        # issues the check fixed counts as smooth (0.25,
        # finding 6). Before this, every join that had ever
        # carried an issue counted against it as abrupt, so a
        # fully fixed plan still reported all of them.
        "joins_smooth": len(pauses) - len({i["join_index"] for i in remaining}),
        "issues": issues,
        "fixed": fixed,
        "your_call": remaining,
        "pauses": pauses,
        "room_level_db": round(room, 2),
    }
    return new_ranges, check


def median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return round(ordered[mid], 4)
    return round((ordered[mid - 1] + ordered[mid]) / 2.0, 4)


# -- sound is the source of truth (0.26, fix 1) -------------------------------
#
# The passes above place edges from measured sound, but the word
# bookkeeping (which words the plan calls kept or cut: the editor
# score, the transcript dots and labels, the counts) still read
# transcript stamp midpoints, and stamps drift 50 to 300 ms. A
# word could read "cut" while its sound sat inside a kept piece
# and played in the render: on Justin's 0.25 Mac run the reworded
# retake ("Every time I've gotten into this tank I said") was
# audible in the output while the word list marked it cut. This
# pass runs on the settled plan and makes the sound the truth in
# both directions:
#
# - Every word's real sound bounds are measured from the audio
#   (the span ruler when spans are known, the envelope's clamped
#   bounds otherwise) and returned with the plan, so every later
#   reader assigns words to pieces by their measured sound.
# - A word the plan cut (by its stamp, or a settled retake cut's
#   blocked word) whose sound sits inside a kept piece has that
#   sound taken out of the piece, unless a kept word's sound
#   shares the stretch; then the case is reported, never forced.
# - A word the plan keeps whose sound fell outside every piece
#   (its stamp drifted into the piece, its sound did not) gets
#   its piece extended over its sound, clamped so the extension
#   never swallows a settled cut word's sound.
# - An edge landing inside a word's sound moves to the word's
#   true boundary: the whole word in when the plan keeps it, the
#   whole word out when it does not. Half a word never plays.
# - A leftover fragment shorter than a frame or two is dropped
#   only when it holds no measured speech sound (0.27, fix 7):
#   a short spoken line between two pauses is content, not a
#   sliver, so a piece holding a word's sound bounds inside it,
#   or the middle of a kept word's sound, is never dropped for
#   its length. A piece with no word sound in it carries
#   nothing, and at render time it can come out with no picture
#   at all, which breaks the join of the pieces around it.
# - The remove step never cuts part of a sentence (0.27,
#   fix 7): when taking a cut word's sound out of a piece would
#   leave a fragment of that word's sentence standing alone in
#   the piece (one or two kept words of the sentence, nothing
#   else), the cut is refused, the line stays, and the case is
#   recorded as unresolved rather than forced.


SOUND_EDGE_SLACK_S = 0.01   # an edge this far inside a sound is inside it
SOUND_CUT_OVERLAP = 0.5     # a cut word this far inside a piece is audible
SOUND_KEEP_OVERLAP = 0.03   # kept sound a removal window may not swallow
SOUND_SLIVER_S = 0.05       # a piece shorter than this is a sliver only
                            # when it holds no word sound at all (fix 7)


def sound_word_bounds(
    words: list[dict],
    env: list[float],
    spans: list[dict] | None = None,
) -> list[tuple[float, float]]:
    """The one ruler for word membership: measured sound bounds.

    The span ruler when the speech spans are known (the ruler the
    span trim, the sentence guard, and the word scoring already
    use); the envelope's clamped bounds otherwise.
    """
    if spans:
        return span_sound_bounds(words, spans)
    return all_word_bounds(words, env, room_level_db(env))


def sound_statuses(
    words: list[dict],
    ranges: list[dict],
    bounds: list[tuple[float, float]],
) -> list[str]:
    """kept / special / cut per word, by its measured sound.

    A word belongs to a piece when the middle of its sound sits
    inside the piece, the same membership Timeline.word_status
    reports once the plan carries these bounds. A word with no
    measured sound (a stamp over room tone) is silent: it is in
    no piece, whatever its stamp says.
    """
    out: list[str] = []
    for s, e in bounds:
        mid = (s + e) / 2.0
        status = "cut"
        special = False
        for rng in ranges:
            if float(rng["start"]) - 1e-6 <= mid <= float(rng["end"]) + 1e-6:
                if _is_special(rng):
                    special = True
                else:
                    status = "kept"
                    break
        out.append(status if status == "kept" else ("special" if special else "cut"))
    return out


def piece_holds_sound(
    bounds: list[tuple[float, float]],
    lo: float,
    hi: float,
    kept_mids: Optional[list] = None,
) -> bool:
    """Whether a piece's span holds measured speech sound (fix 7).

    A piece holds sound when a word's measured sound bounds sit
    inside it (a whole short word's sound: a short spoken line
    between two pauses is content, not a sliver), or when the
    middle of a kept word's sound sits inside it. A bare tail of
    a cut word's sound sticking through a fragment is neither:
    the fragment carries no whole word and still goes.
    """
    for s, e in bounds:
        if s >= lo - 0.005 and e <= hi + 0.005:
            return True
    for m in kept_mids or ():
        if lo - 1e-6 <= m <= hi + 1e-6:
            return True
    return False


def drop_soundless_pieces(
    words: list[dict],
    ranges: list[dict],
    bounds: list[tuple[float, float]],
    min_s: float = 0.03,
    kept_mids: Optional[list] = None,
) -> list[dict]:
    """Drop pieces too short to carry anything, by the fix 7 rule.

    A piece shorter than ``min_s`` is dropped only when it holds
    no measured speech sound (see ``piece_holds_sound``). A piece
    with no length at all (its start at or past its end) carries
    nothing and always goes.
    """
    out: list[dict] = []
    for rng in ranges:
        lo, hi = float(rng["start"]), float(rng["end"])
        if hi - lo >= min_s:
            out.append(rng)
            continue
        if hi <= lo:
            continue
        if piece_holds_sound(bounds, lo, hi, kept_mids):
            out.append(rng)
    return out


def stamp_statuses(words: list[dict], ranges: list[dict]) -> list[str]:
    """kept / special / cut per word, by transcript stamp midpoint.

    The old ruler, kept so the truth pass can count how many
    words it reassigns; nothing else reads it.
    """
    out: list[str] = []
    for w in words:
        mid = (float(w["start"]) + float(w["end"])) / 2.0
        status = "cut"
        special = False
        for rng in ranges:
            if float(rng["start"]) - 1e-6 <= mid <= float(rng["end"]) + 1e-6:
                if _is_special(rng):
                    special = True
                else:
                    status = "kept"
                    break
        out.append(status if status == "kept" else ("special" if special else "cut"))
    return out


def sound_truth_pass(
    words: list[dict],
    ranges: list[dict],
    env: list[float],
    *,
    segments: list[dict] | None = None,
    protected_words: set[int] | None = None,
    blocked_words: set[int] | None = None,
    deliberate_words: set[int] | None = None,
    fps: float | None = None,
    spans: list[dict] | None = None,
) -> tuple[list[dict], dict]:
    """Reconcile the settled plan with the sound it will render.

    Returns (new_ranges, report). The report carries the measured
    sound bounds (``report['bounds']``, one [start, end] per word)
    so the caller can store them with the plan: every later word
    assignment then reads sound, not stamps. Cases the pass
    cannot settle without a judgment call are listed in
    ``report['unresolved']`` and the plan is left as it was there;
    the word's membership then follows its sound, so the list
    still tells the truth about what plays.

    ``blocked_words`` is the words settled retake cuts took out
    (applied pairs only): a held pair is still the creator's
    call, so its words are never targets here, and a removal
    window that would swallow one is refused and reported.
    """
    protected_words = protected_words or set()
    blocked_words = blocked_words or set()
    # Words some pass deliberately cut by operation (0.27, fix 7):
    # the orphan bridge below must never revive one. When the
    # caller does not say, the settled retake cuts are the
    # deliberate ones.
    if deliberate_words is None:
        deliberate_words = set(blocked_words)
    else:
        deliberate_words = set(deliberate_words)
    report: dict = {
        "bounds": [],
        "edges_moved": 0,
        "cut_sounds_removed": 0,
        "kept_sounds_restored": 0,
        "orphan_sounds_bridged": 0,
        "slivers_dropped": 0,
        "status_changes": {"cut_to_kept": 0, "kept_to_cut": 0},
        "unresolved": [],
        "details": [],
    }
    if not words or not ranges or not env:
        return [dict(r) for r in ranges], report
    bounds = sound_word_bounds(words, env, spans)
    report["bounds"] = [[round(s, 4), round(e, 4)] for s, e in bounds]
    ends = sentence_end_indices(words, segments)
    # The sentence each word belongs to (fix 7): sentences end at
    # the measured sentence ends; the remove step below refuses a
    # cut that would split one.
    sentence_of: list[int] = []
    _sid = 0
    for i in range(len(words)):
        sentence_of.append(_sid)
        if i in ends:
            _sid += 1
    before = stamp_statuses(words, ranges)
    # A word whose stamp window overlaps a kept piece is the
    # plan's word, not a cut word (0.27, fix 7): the ranges are
    # built from stamps, so a piece reaching into a word's stamp
    # window claims that stretch of the word, and a sentence
    # head heard just before its stamp (in the piece its stamp
    # touches) must not have its sound taken out as if the plan
    # had cut it, stranding the rest of its sentence. A settled
    # cut outranks the overlap: a blocked word stays a remove
    # target whatever its stamp does.
    for i, w in enumerate(words):
        if before[i] == "kept" or i in blocked_words:
            continue
        w_start, w_end = float(w["start"]), float(w["end"])
        if any(
            not _is_special(r)
            and float(r["start"]) < w_end
            and w_start < float(r["end"])
            for r in ranges
        ):
            before[i] = "kept"
    new_ranges = [dict(r) for r in ranges]

    def mid(i: int) -> float:
        return (bounds[i][0] + bounds[i][1]) / 2.0

    def sound_len(i: int) -> float:
        return max(0.0, bounds[i][1] - bounds[i][0])

    def ordinary_index_at(t: float) -> Optional[int]:
        for idx, rng in enumerate(new_ranges):
            if _is_special(rng):
                continue
            if float(rng["start"]) - 1e-6 <= t <= float(rng["end"]) + 1e-6:
                return idx
        return None

    def blocked_mid_between(lo: float, hi: float) -> Optional[float]:
        hits = [
            mid(i) for i in blocked_words
            if 0 <= i < len(words) and lo < mid(i) < hi
        ]
        return max(hits) if hits else None

    def quantize(t: float) -> float:
        t = round(t, 4)
        if fps:
            t = round(round(t * fps) / fps, 4)
        return t


    # -- 1. restore: a kept word whose sound fell outside its piece --------
    for idx, rng in enumerate(new_ranges):
        if _is_special(rng):
            continue
        r_start, r_end = float(rng["start"]), float(rng["end"])
        members = [
            i for i in range(len(words))
            if before[i] == "kept"
            and (float(words[i]["start"]) + float(words[i]["end"])) / 2.0
            >= r_start - 1e-6
            and (float(words[i]["start"]) + float(words[i]["end"])) / 2.0
            <= r_end + 1e-6
        ]
        for i in members:
            if i in blocked_words or sound_len(i) <= 0.0:
                continue
            if ordinary_index_at(mid(i)) is not None:
                continue  # its sound plays from some piece already
            s, e = bounds[i]
            if e <= r_start + SOUND_EDGE_SLACK_S:
                # The sound sits before the piece: extend the start.
                new_start = s - HEAD_IN_S
                prev_end = 0.0
                for other in new_ranges[:idx]:
                    prev_end = max(prev_end, float(other["end"]))
                new_start = max(new_start, prev_end)
                clash = blocked_mid_between(new_start, r_start)
                if clash is not None:
                    new_start = max(new_start, clash + 0.011)
                if new_start < r_start - 0.005 and new_start < mid(i):
                    rng["start"] = quantize(new_start)
                    r_start = float(rng["start"])
                    report["kept_sounds_restored"] += 1
                    report["details"].append({
                        "kind": "kept_sound_restored",
                        "word_index": i,
                        "edge": "start",
                        "to_s": round(r_start, 4),
                    })
                else:
                    report["unresolved"].append({
                        "word_index": i,
                        "reason": "kept word's sound before its piece, "
                                  "no room to extend without a settled cut",
                    })
            elif s >= r_end - SOUND_EDGE_SLACK_S:
                # The sound sits after the piece: extend the end.
                tail = TAIL_END_S if i in ends else TAIL_INSIDE_S
                new_end = e + tail
                next_start = None
                for other in new_ranges[idx + 1:]:
                    next_start = float(other["start"])
                    break
                if next_start is not None:
                    new_end = min(new_end, next_start)
                clash = blocked_mid_between(r_end, new_end)
                if clash is not None:
                    new_end = min(new_end, clash - 0.011)
                if new_end > r_end + 0.005 and new_end > mid(i):
                    rng["end"] = quantize(new_end)
                    r_end = float(rng["end"])
                    report["kept_sounds_restored"] += 1
                    report["details"].append({
                        "kind": "kept_sound_restored",
                        "word_index": i,
                        "edge": "end",
                        "to_s": round(r_end, 4),
                    })
                else:
                    report["unresolved"].append({
                        "word_index": i,
                        "reason": "kept word's sound after its piece, "
                                  "no room to extend without a settled cut",
                    })

    # -- 2. remove: a cut word whose sound sits inside a kept piece -------
    targets_by_range: dict[int, list[int]] = {}
    for i in range(len(words)):
        if i in protected_words or sound_len(i) <= 0.0:
            continue
        if i not in blocked_words and before[i] != "cut":
            continue
        owner = ordinary_index_at(mid(i))
        if owner is None:
            continue
        rng = new_ranges[owner]
        overlap = min(bounds[i][1], float(rng["end"])) - max(
            bounds[i][0], float(rng["start"])
        )
        if overlap >= SOUND_CUT_OVERLAP * sound_len(i):
            targets_by_range.setdefault(owner, []).append(i)
    rebuilt: list[dict] = []
    for idx, rng in enumerate(new_ranges):
        targets = targets_by_range.get(idx)
        if _is_special(rng) or not targets:
            rebuilt.append(rng)
            continue
        r_start, r_end = float(rng["start"]), float(rng["end"])
        windows = sorted(
            (bounds[i][0] - 0.005, bounds[i][1] + 0.005, i) for i in targets
        )
        merged: list[list] = []
        for lo, hi, i in windows:
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
                merged[-1][2].append(i)
            else:
                merged.append([lo, hi, [i]])
        # Never cut part of a sentence (0.27, fix 7): when the
        # removals in this piece would leave a fragment of a
        # sentence standing alone in a remainder segment (one or
        # two kept words of the sentence and nothing else beside
        # them), the sentence's cuts in this piece are refused
        # and recorded, and the line stays whole. Justin's case:
        # "This was worth it." with "this was worth" targeted,
        # which would have left a lone "it." playing.
        clamped = [
            (max(lo, r_start), min(hi, r_end), group)
            for lo, hi, group in merged
            if min(hi, r_end) - max(lo, r_start) >= 0.02
        ]
        remainder_segments: list[tuple[float, float]] = []
        cursor = r_start
        for w_lo, w_hi, _group in clamped:
            if w_lo > cursor:
                remainder_segments.append((cursor, w_lo))
            cursor = max(cursor, w_hi)
        if cursor < r_end:
            remainder_segments.append((cursor, r_end))
        target_set = set(targets)
        refused_sentences: set[int] = set()
        for seg_lo, seg_hi in remainder_segments:
            standing = [
                j for j in range(len(words))
                if before[j] == "kept" and j not in target_set
                and seg_lo - 1e-6 <= mid(j) <= seg_hi + 1e-6
            ]
            if not standing or len(standing) > 2:
                continue
            sids = {sentence_of[j] for j in standing}
            if len(sids) != 1:
                continue
            sid = sids.pop()
            if any(sentence_of[t] == sid for t in targets):
                refused_sentences.add(sid)
        removals: list[tuple[float, float]] = []
        for lo, hi, group in merged:
            lo = max(lo, r_start)
            hi = min(hi, r_end)
            if hi - lo < 0.02:
                continue
            if any(sentence_of[i] in refused_sentences for i in group):
                for i in group:
                    if sentence_of[i] in refused_sentences:
                        report["unresolved"].append({
                            "word_index": i,
                            "reason": "cutting this word's sound "
                                      "would split its sentence "
                                      "and leave a fragment "
                                      "standing alone",
                        })
                continue
            # A removal may not swallow a word the plan keeps:
            # in continuous speech a cut take and the kept take
            # can share one stretch of sound, and there is no
            # edge that takes one out without the other. Those
            # cases are reported, never forced.
            guards = [
                j for j in range(len(words))
                if j not in targets and j not in blocked_words
                and lo - 1e-6 <= mid(j) <= hi + 1e-6
            ]
            overlap_guards = [
                j for j in range(len(words))
                if j not in targets and j not in blocked_words
                and j not in protected_words
                and min(bounds[j][1], hi) - max(bounds[j][0], lo)
                > SOUND_KEEP_OVERLAP
                and ordinary_index_at(mid(j)) == idx
            ]
            if guards or overlap_guards:
                for i in group:
                    report["unresolved"].append({
                        "word_index": i,
                        "reason": "cut word's sound shares its stretch "
                                  "with a kept word's sound",
                    })
                continue
            removals.append((lo, hi))
            # This pass's own settled removals are deliberate
            # cuts: the orphan bridge below must never revive a
            # word whose sound was taken out here.
            deliberate_words.update(group)
            report["cut_sounds_removed"] += len(group)
            report["details"].append({
                "kind": "cut_sound_removed",
                "word_indices": group,
                "from_s": round(lo, 4),
                "to_s": round(hi, 4),
            })
        if not removals:
            rebuilt.append(rng)
            continue
        # A remainder segment is emitted when it has real length,
        # or however short it is when it holds a kept word's sound
        # (0.27, fix 7): discarding it would drop a spoken line
        # the plan keeps, the same loss the sliver rule now bans.
        def holds_kept_sound(a: float, b: float) -> bool:
            return any(
                before[j] == "kept" and j not in target_set
                and a - 1e-6 <= mid(j) <= b + 1e-6
                for j in range(len(words))
            )

        cursor = r_start
        for lo, hi in removals:
            if lo - cursor >= 0.04 or holds_kept_sound(cursor, lo):
                piece = dict(rng)
                piece["start"] = quantize(cursor)
                piece["end"] = quantize(lo)
                rebuilt.append(piece)
            cursor = max(cursor, hi)
        if r_end - cursor >= 0.04 or holds_kept_sound(cursor, r_end):
            piece = dict(rng)
            piece["start"] = quantize(cursor)
            piece["end"] = quantize(r_end)
            rebuilt.append(piece)
    new_ranges = rebuilt

    # -- 3. edges: no edge lands inside a word's sound ----------------------
    # One word decides each edge: the one whose sound the edge
    # actually clips (the latest-starting sound containing it).
    # Moving an edge and then re-judging it against every other
    # word walks it across the piece in continuous speech, where
    # neighbouring words' span slices overlap; when the moved
    # edge lands inside the neighbouring word's slice, it settles
    # at the point the two measured sounds meet (the envelope's
    # clamped boundary between the two words) instead.
    ebounds = all_word_bounds(words, env, room_level_db(env))

    def clipped_word(t: float) -> Optional[int]:
        best = None
        for i in range(len(words)):
            s, e = bounds[i]
            if s + SOUND_EDGE_SLACK_S < t < e - SOUND_EDGE_SLACK_S:
                if best is None or s > bounds[best][0]:
                    best = i
        return best

    def settle(t: float, i: int) -> float:
        """If t sits inside another word's sound slice, move it
        to the meeting point of the two words' sounds."""
        for j in range(len(words)):
            if j == i:
                continue
            s, e = bounds[j]
            if s + SOUND_EDGE_SLACK_S < t < e - SOUND_EDGE_SLACK_S:
                if abs(j - i) == 1:
                    lo_i, hi_i = (i, j) if i < j else (j, i)
                    return ebounds[lo_i][1] if lo_i == i else ebounds[hi_i][0]
                return (max(bounds[i][0], s) + min(bounds[i][1], e)) / 2.0
        return t

    for idx, rng in enumerate(new_ranges):
        if _is_special(rng):
            continue
        # The start edge.
        t = float(rng["start"])
        i = clipped_word(t)
        if i is not None:
            s, e = bounds[i]
            prev_end = 0.0
            for other in new_ranges[:idx]:
                prev_end = max(prev_end, float(other["end"]))
            if i in blocked_words or mid(i) < t:
                new_t = min(e + 0.01, float(rng["end"]) - 0.03)
                new_t = max(settle(new_t, i), prev_end)
                kind = "edge_out_of_cut_word"
            else:
                new_t = max(s - HEAD_IN_S, prev_end)
                clash = blocked_mid_between(new_t, t)
                if clash is not None:
                    new_t = max(new_t, clash + 0.011)
                new_t = settle(new_t, i)
                kind = "edge_out_of_kept_word"
                if new_t >= mid(i):
                    report["unresolved"].append({
                        "word_index": i,
                        "reason": "piece start inside a kept word's "
                                  "sound, no room to back up",
                    })
                    new_t = t
            if abs(new_t - t) > 0.001 and new_t < float(rng["end"]):
                rng["start"] = quantize(new_t)
                report["edges_moved"] += 1
                report["details"].append({
                    "kind": kind, "word_index": i,
                    "edge": "start", "to_s": round(float(rng["start"]), 4),
                })
        # The end edge.
        t = float(rng["end"])
        i = clipped_word(t)
        if i is not None:
            s, e = bounds[i]
            next_start = None
            for other in new_ranges[idx + 1:]:
                next_start = float(other["start"])
                break
            if i in blocked_words or mid(i) > t:
                new_t = max(s - 0.01, float(rng["start"]) + 0.03)
                new_t = settle(new_t, i)
                if next_start is not None:
                    new_t = min(new_t, next_start)
                kind = "edge_out_of_cut_word"
            else:
                tail = TAIL_END_S if i in ends else TAIL_INSIDE_S
                new_t = e + tail
                if next_start is not None:
                    new_t = min(new_t, next_start)
                clash = blocked_mid_between(t, new_t)
                if clash is not None:
                    new_t = min(new_t, clash - 0.011)
                new_t = settle(new_t, i)
                kind = "edge_out_of_kept_word"
                if new_t <= mid(i):
                    report["unresolved"].append({
                        "word_index": i,
                        "reason": "piece end inside a kept word's "
                                  "sound, no room to extend",
                    })
                    new_t = t
            if abs(new_t - t) > 0.001 and new_t > float(rng["start"]):
                rng["end"] = quantize(new_t)
                report["edges_moved"] += 1
                report["details"].append({
                    "kind": kind, "word_index": i,
                    "edge": "end", "to_s": round(float(rng["end"]), 4),
                })
    # -- 3b. bridge orphan sounds (0.27, fix 7) ---------------------------
    # A word no pass cut can still lose its sound entirely: the
    # span trim and the edge setting can split the pieces inside
    # one shared speech span (several short words sliced from a
    # single measured span), stranding a whole short sentence's
    # sounds in the gaps between pieces, so the line reads cut
    # though nothing ever decided to cut it. On the Day 30 run
    # "This was worth it." vanished this way once the retake pair
    # that used to mask the loss was fixed. Orphan runs are
    # bridged sentence by sentence: the last piece ending before
    # the run's sounds and the first piece starting after them
    # merge over the gap, so the sentence plays whole. A run is
    # never bridged when one of its words was deliberately cut
    # (an applied operation's word, a settled retake cut, a
    # protected span), when a settled cut's sound midpoint lies
    # in the gap (the bridge would revive it), when a kept word's
    # piece or an insert sits between the bounds, or when the
    # run touches either end of the video.
    def _status_now() -> dict[int, str]:
        plist = [r for r in new_ranges if not r.get("special")]
        return {
            i: (
                "kept"
                if any(
                    float(r["start"]) <= mid(i) <= float(r["end"])
                    for r in plist
                )
                else "cut"
            )
            for i in range(len(words))
        }

    def _orphan(i: int, status: dict[int, str]) -> bool:
        return (
            sound_len(i) > 0
            and i not in blocked_words
            and i not in protected_words
            and i not in deliberate_words
            and status[i] == "cut"
        )

    def _bounded_side(i: int, status: dict[int, str]) -> bool:
        return (
            status[i] == "kept"
            or i in blocked_words
            or i in protected_words
            or i in deliberate_words
            or _orphan(i, status)
        )

    for _bridge_round in range(12):
        status_now = _status_now()
        bridged_this_round = False
        wi = 0
        while wi < len(words):
            if not _orphan(wi, status_now):
                wi += 1
                continue
            run = [wi]
            wj = wi + 1
            while (
                wj < len(words)
                and _orphan(wj, status_now)
                and sentence_of[wj] == sentence_of[wi]
            ):
                run.append(wj)
                wj += 1
            prev_i, next_i = wi - 1, wj
            did = False
            if (
                prev_i >= 0
                and next_i < len(words)
                and _bounded_side(prev_i, status_now)
                and _bounded_side(next_i, status_now)
            ):
                span_lo = min(bounds[k][0] for k in run)
                span_hi = max(bounds[k][1] for k in run)
                plist = [r for r in new_ranges if not r.get("special")]
                p_candidates = [
                    r for r in plist if float(r["end"]) <= span_lo + 0.02
                ]
                q_candidates = [
                    r for r in plist if float(r["start"]) >= span_hi - 0.02
                ]
                if p_candidates and q_candidates:
                    prev_piece = max(
                        p_candidates, key=lambda r: float(r["end"])
                    )
                    next_piece = min(
                        q_candidates, key=lambda r: float(r["start"])
                    )
                    if (
                        prev_piece is not next_piece
                        and float(prev_piece["end"])
                        <= float(next_piece["start"])
                    ):
                        gap_lo = float(prev_piece["end"])
                        gap_hi = float(next_piece["start"])
                        revive = any(
                            gap_lo < mid(k) < gap_hi
                            for k in blocked_words | deliberate_words
                            if sound_len(k) > 0
                        )
                        between = [
                            r for r in plist
                            if float(r["start"]) >= gap_lo - 1e-9
                            and float(r["end"]) <= gap_hi + 1e-9
                            and r is not prev_piece
                            and r is not next_piece
                        ]
                        holds_kept = any(
                            float(r["start"]) <= mid(k) <= float(r["end"])
                            for r in between
                            for k in range(len(words))
                            if status_now[k] == "kept"
                        )
                        special_between = any(
                            r.get("special")
                            for r in new_ranges
                            if float(r["start"]) >= gap_lo - 1e-9
                            and float(r["end"]) <= gap_hi + 1e-9
                        )
                        if not revive and not holds_kept and not special_between:
                            merged = dict(prev_piece)
                            merged["end"] = float(next_piece["end"])
                            rebuilt = []
                            for r in new_ranges:
                                if r is prev_piece:
                                    rebuilt.append(merged)
                                elif r is next_piece:
                                    continue
                                elif (
                                    float(r["start"]) >= gap_lo - 1e-9
                                    and float(r["end"]) <= gap_hi + 1e-9
                                ):
                                    continue  # swallowed by the bridge
                                else:
                                    rebuilt.append(r)
                            new_ranges = rebuilt
                            report["orphan_sounds_bridged"] += len(run)
                            report["details"].append({
                                "kind": "orphan_sound_bridged",
                                "word_indices": run,
                                "from_s": round(float(merged["start"]), 4),
                                "to_s": round(float(merged["end"]), 4),
                            })
                            did = True
            if did:
                bridged_this_round = True
                break
            wi = wj
        if not bridged_this_round:
            break
    # -- 4. slivers: a fragment holding no word sound at all goes --------
    # The moves above can leave a piece a few hundredths of a
    # second long: the tail of a cut word's sound, or room tone
    # between two settled edges. A piece shorter than a video
    # frame can render with no picture stream at all, which
    # breaks the concat join, so a short piece that holds no
    # measured speech sound goes. But a short spoken line
    # between two pauses is content, not a sliver (0.27, fix 7):
    # a piece holding a word's sound bounds inside it, or the
    # middle of a kept word's sound, is never dropped for its
    # length.
    kept_mids = [
        mid(i) for i in range(len(words)) if before[i] == "kept"
    ]
    settled: list[dict] = []
    for rng in new_ranges:
        if _is_special(rng):
            settled.append(rng)
            continue
        lo, hi = float(rng["start"]), float(rng["end"])
        if hi - lo < SOUND_SLIVER_S and not piece_holds_sound(
            bounds, lo, hi, kept_mids
        ):
            report["slivers_dropped"] += 1
            report["details"].append({
                "kind": "sliver_dropped",
                "from_s": round(lo, 4),
                "to_s": round(hi, 4),
            })
            continue
        settled.append(rng)
    new_ranges = settled
    after = sound_statuses(words, new_ranges, bounds)
    for was, is_now in zip(before, after):
        simple_was = "kept" if was == "kept" else "cut"
        simple_now = "kept" if is_now == "kept" else "cut"
        if simple_was == "cut" and simple_now == "kept":
            report["status_changes"]["cut_to_kept"] += 1
        elif simple_was == "kept" and simple_now == "cut":
            report["status_changes"]["kept_to_cut"] += 1
    return new_ranges, report
