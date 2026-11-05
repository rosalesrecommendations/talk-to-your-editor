"""Signature beats: the sounds a creator splices in, learned.

Every pass in this app until now only removed. A finished edit
also inserts: in Justin's Day 30 edit, 1.4 s of wordless sound
(breath and water movement, taken from the setup before his
first line) sits between "Today's temperature is" and "40
degrees", material moved out of time order on purpose. That
insert is part of his style, so it is learned like his rules
are: from a before/after pair, stored per style, shown in the
style's rules list, and applied to the next video edited in
that style.

Learning aligns the finished edit to the raw by words, finds
the joins where the finished edit carries more sound than the
raw has between the same two words, locates that sound's
source in the raw's setup by matching its loudness envelope,
and stores the beat: its anchor words, its length and
loudness, the pauses on each side of it in his edit, and a
small envelope profile of the sound itself.

Applying searches the new video's own setup (the stretch
before its first line) for a wordless sound of similar length
and shape, and places it at the anchor with the measured
pauses, as a special insert range in the plan: the timeline's
protected kind, in list order, so the render splices it in
and no later pass can cut across it. The two new joins are
measured with the join check's own measures; a join that
jumps gets the engine's remedy, the longer crossfade.

Written fresh for this feature. Nothing here reads or copies
any pre-hackathon tool.
"""

from __future__ import annotations

import difflib
import hashlib
import math
import string
from pathlib import Path
from typing import Optional

from . import smooth
from . import transcribe as speech

# A finished join has to carry at least this much extra sound
# over the raw's own gap before it can be an inserted piece;
# below that it is just a longer pause, which the pacing rules
# already own.
MIN_EXTRA_S = 0.45
# The inserted piece itself: shorter than this is a breath
# fragment or a noise, not a beat.
MIN_BEAT_S = 0.30
# How far the source search may stretch the learned length.
LENGTH_TOLERANCE = 0.15
# Envelope correlation a learn-time source match must reach:
# the finished edit's piece is a copy of the raw's sound, so
# anything weaker is not the same sound.
LEARN_CORR_MIN = 0.70
# Envelope correlation an apply-time match must reach: a new
# recording's breath is the same shape, never the same sound.
APPLY_CORR_MIN = 0.50
# A beat's source must be a real sound over the room, at
# learn time and at apply time alike: room tone correlates
# with anything (two flat noise profiles score 0.9), and a
# signature beat is a sound he actually splices in, measured
# here at 39 dB over his room on the Day 30 pair.
SOURCE_ABOVE_ROOM_DB = 12.0
PROFILE_POINTS = 24

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def _norm(token) -> str:
    return str(token or "").lower().strip(string.punctuation)


def anchor_tokens_for_word(word) -> list:
    """One transcript word as anchor tokens.

    Number words and digits are the same token ("thirty" is
    "30"), and a contraction like "today's" is the two tokens
    it sounds like, so an anchor learned from one transcript
    spelling is found in another (0.27.1, the Scribe lesson).
    """
    tok = _norm(word.get("word") if isinstance(word, dict) else word)
    if not tok:
        return []
    if tok in _NUMBER_WORDS:
        return [str(_NUMBER_WORDS[tok])]
    if "-" in tok:
        parts = tok.split("-")
        if (
            len(parts) == 2
            and parts[0] in _NUMBER_WORDS
            and parts[1] in _NUMBER_WORDS
            and _NUMBER_WORDS[parts[0]] % 10 == 0
        ):
            return [str(_NUMBER_WORDS[parts[0]] + _NUMBER_WORDS[parts[1]])]
    if tok.endswith("'s") and len(tok) > 2:
        return [tok[:-2], "is"]
    return [tok]


def anchor_tokens(words: list) -> list:
    out: list = []
    for word in words:
        out.extend(anchor_tokens_for_word(word))
    return out


def _token_stream(words: list) -> list:
    """(token, word_index) pairs for a whole transcript."""
    stream = []
    for index, word in enumerate(words):
        for tok in anchor_tokens_for_word(word.get("word")):
            stream.append((tok, index))
    return stream


def find_anchors(words: list, beat: dict) -> list:
    """Every word index the beat could go after, in order.

    The beat's before-tokens must end on some word and its
    after-tokens must start on the very next word: the anchor
    is a join between two spoken words, never a place inside
    a phrase the video does not say. A template line can be
    said more than once (he restates his opening), and the
    edit keeps one saying; the caller tries each in order
    and the plan itself decides which saying stands.
    """
    before = list(beat.get("anchor_before_tokens") or [])
    after = list(beat.get("anchor_after_tokens") or [])
    if not before or not after:
        return []
    stream = _token_stream(words)
    toks = [tok for tok, _ in stream]
    width = len(before) + len(after)

    def matches(want: list, got: list) -> bool:
        # A number in the anchor is "the number" in his
        # words: the temperature and the day count change
        # every video, so a digit token matches any digit
        # token. Every other token must match exactly.
        return all(
            w == g or (w.isdigit() and g.isdigit())
            for w, g in zip(want, got)
        )

    found = []
    for k in range(len(toks) - width + 1):
        if not matches(before, toks[k:k + len(before)]):
            continue
        if not matches(after, toks[k + len(before):k + width]):
            continue
        before_word = stream[k + len(before) - 1][1]
        after_word = stream[k + len(before)][1]
        if after_word == before_word + 1:
            found.append(before_word)
    return found


def find_anchor(words: list, beat: dict) -> Optional[int]:
    """The first word index the beat goes after, or None."""
    anchors = find_anchors(words, beat)
    return anchors[0] if anchors else None


# -- envelopes ---------------------------------------------------------------


def _profile(env: list, start: float, end: float) -> list:
    """An envelope sampled to PROFILE_POINTS dB values."""
    if end <= start or not env:
        return []
    out = []
    for n in range(PROFILE_POINTS):
        t = start + (end - start) * (n + 0.5) / PROFILE_POINTS
        out.append(round(smooth.level_at(env, t), 2))
    return out


def _pearson(xs: list, ys: list) -> float:
    if len(xs) != len(ys) or len(xs) < 3:
        return 0.0
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x <= 1e-9 or den_y <= 1e-9:
        return 0.0
    return num / (den_x * den_y)


def _search_envelope(
    env: list,
    room_db: float,
    profile: list,
    length_s: float,
    region_start: float,
    region_end: float,
    *,
    corr_min: float,
    loudness_db: Optional[float] = None,
) -> Optional[dict]:
    """The window in a region whose envelope best matches.

    Slides the learned length (and a step shorter and longer)
    across the region. A candidate has to be real sound, not
    silence wearing the right shape: enough of it sits over
    the room, and its loudness is in the learned sound's
    neighbourhood when that is known.
    """
    best = None
    lengths = [
        length_s * (1.0 - LENGTH_TOLERANCE),
        length_s,
        length_s * (1.0 + LENGTH_TOLERANCE),
    ]
    step = 0.02
    for length in lengths:
        if length < MIN_BEAT_S * 0.8 or region_end - region_start < length:
            continue
        t = region_start
        while t + length <= region_end + 1e-9:
            window_profile = _profile(env, t, t + length)
            corr = _pearson(window_profile, profile)
            if corr >= corr_min:
                level = smooth.window_level_db(env, t, t + length)
                frames = max(1, int(length / smooth.FRAME_S))
                loud_frames = 0
                for n in range(frames):
                    if (
                        smooth.level_at(env, t + (n + 0.5) * smooth.FRAME_S)
                        > room_db + smooth.SOUND_ABOVE_ROOM_DB
                    ):
                        loud_frames += 1
                share = loud_frames / frames
                if share >= 0.5 and level >= room_db + 6.0:
                    score = corr
                    if loudness_db is not None:
                        score -= 0.02 * abs(level - loudness_db)
                    if best is None or score > best["score"]:
                        best = {
                            "start": round(t, 4),
                            "end": round(t + length, 4),
                            "corr": round(corr, 3),
                            "score": round(score, 3),
                            "level_db": round(level, 2),
                        }
            t = round(t + step, 6)
    return best


def _sound_clusters(
    env: list, room_db: float, start: float, end: float
) -> list:
    """Wordless sound clusters in a stretch: breath_segments'
    runs, merged across dips of up to 0.09 s. The merge stays
    under his shortest measured pause (0.12 s), so a real
    pause beside an insert still splits two sounds apart."""
    runs = smooth.breath_segments(env, room_db, start, end, [])
    clusters: list = []
    for run_start, run_end in runs:
        if clusters and run_start - clusters[-1][1] <= 0.09:
            clusters[-1][1] = run_end
        else:
            clusters.append([run_start, run_end])
    return [(round(a, 4), round(b, 4)) for a, b in clusters]


# -- learning ------------------------------------------------------------------


def _sound_end(env: list, room_db: float, word: dict) -> float:
    """Where a word's sound actually ends, measured.

    Transcript stamps are approximate, and a transcriber
    stretches a word's span over an insert it cannot name, so
    joins are read off the envelope: the last sound cluster
    overlapping the word's claimed span, else the stamp.
    """
    clusters = _sound_clusters(
        env,
        room_db,
        max(0.0, float(word["start"]) - 0.05),
        float(word["end"]) + 0.05,
    )
    return clusters[-1][1] if clusters else float(word["end"])


def _sound_start(env: list, room_db: float, word: dict) -> float:
    """Where a word's sound actually starts, measured.

    The word's own sound is the last cluster in its span: an
    insert the transcriber folded into the span sits before
    it, never after it.
    """
    clusters = _sound_clusters(
        env,
        room_db,
        max(0.0, float(word["start"]) - 0.35),
        float(word["end"]) + 0.05,
    )
    return clusters[-1][0] if clusters else float(word["start"])


def learn_beats(
    raw_media: Path,
    finished_media: Path,
    raw_words: list,
    finished_words: list,
    work_dir: Path,
) -> list:
    """Signature beats in one before/after pair.

    Aligns the finished edit to the raw by words and reads
    every join off the two envelopes. At a join where the
    finished edit carries sound the raw does not have there,
    the extra material is a piece placed out of time order:
    when it stands as its own cluster it is read directly;
    when it sits quiet under the finished mix (his water
    piece under the music bed), the join's whole stretch is
    the finder and the piece is located by its shape in the
    raw's setup, the stretch before the raw's first line.
    A beat is stored only when its source is found in that
    setup. Returns beat dicts without style or ids; the
    caller stamps those.
    """
    if not raw_words or not finished_words:
        return []
    work_dir.mkdir(parents=True, exist_ok=True)
    raw_wav = work_dir / "signature-raw.wav"
    fin_wav = work_dir / "signature-finished.wav"
    speech.extract_audio(raw_media, raw_wav)
    speech.extract_audio(finished_media, fin_wav)
    env_raw = smooth.envelope_db(raw_wav)
    env_fin = smooth.envelope_db(fin_wav)
    if not env_raw or not env_fin:
        return []
    room_raw = smooth.room_level_db(env_raw)
    room_fin = smooth.room_level_db(env_fin)

    raw_tokens = [_norm(w.get("word")) for w in raw_words]
    fin_tokens = [_norm(w.get("word")) for w in finished_words]
    matcher = difflib.SequenceMatcher(
        a=raw_tokens, b=fin_tokens, autojunk=False
    )
    fin_to_raw: dict = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for t in range(i2 - i1):
                fin_to_raw[j1 + t] = i1 + t

    setup_end = float(raw_words[0]["start"]) - 0.02
    beats: list = []
    seen_anchors: set = set()
    for j in range(len(finished_words) - 1):
        r_here = fin_to_raw.get(j)
        r_next = fin_to_raw.get(j + 1)
        if r_here is None or r_next is None or r_next != r_here + 1:
            continue
        fin_prev_end = _sound_end(env_fin, room_fin, finished_words[j])
        fin_next_start = _sound_start(
            env_fin, room_fin, finished_words[j + 1]
        )
        raw_prev_end = _sound_end(env_raw, room_raw, raw_words[r_here])
        raw_next_start = _sound_start(
            env_raw, room_raw, raw_words[r_next]
        )
        fin_stretch = fin_next_start - fin_prev_end
        raw_gap = raw_next_start - raw_prev_end

        found = None
        length = 0.0
        pause_before = 0.0
        pause_after = 0.0
        # Path A: the piece stands as its own cluster between
        # the two words' sounds, clear of the next word's
        # claimed span (a syllable fragment inside the span is
        # the word itself, not an insert).
        clusters = _sound_clusters(
            env_fin, room_fin, fin_prev_end - 0.05, fin_next_start + 0.05
        )
        claimed_next_start = float(finished_words[j + 1]["start"])
        candidates = [
            c
            for c in clusters
            if c[1] - c[0] >= MIN_BEAT_S
            and (c[0] + c[1]) / 2.0 <= claimed_next_start + 0.10
            and fin_next_start - c[1] >= 0.04
            and (c[1] - c[0]) > raw_gap + 0.1
        ]
        if candidates:
            c_start, c_end = max(
                candidates, key=lambda c: c[1] - c[0]
            )
            profile = _profile(env_fin, c_start, c_end)
            hit = _search_envelope(
                env_raw,
                room_raw,
                profile,
                c_end - c_start,
                0.0,
                setup_end,
                corr_min=LEARN_CORR_MIN,
            )
            if hit is not None:
                found = hit
                length = c_end - c_start
                pause_before = max(0.0, c_start - fin_prev_end)
                pause_after = max(0.0, fin_next_start - c_end)
        # Path B: the piece sits quiet in the finished mix, so
        # only the join's stretch betrays it: the finished edit
        # holds far more time between these two words' sounds
        # than the raw does. The stretch's own envelope is the
        # finder; the piece is located by shape in the setup.
        if found is None and fin_stretch - raw_gap >= MIN_EXTRA_S \
                and fin_stretch >= 0.75:
            threshold = room_fin + smooth.SOUND_ABOVE_ROOM_DB
            first_above = None
            last_above = None
            t = fin_prev_end
            while t <= fin_next_start:
                if smooth.level_at(env_fin, t) >= threshold:
                    if first_above is None:
                        first_above = t
                    last_above = t
                t += smooth.FRAME_S
            if first_above is not None:
                pause_before = min(0.30, max(0.0, first_above - fin_prev_end))
                pause_after = min(0.30, max(0.0, fin_next_start - last_above))
                profile = _profile(env_fin, fin_prev_end, fin_next_start)
                hit = _search_envelope(
                    env_raw,
                    room_raw,
                    profile,
                    fin_stretch,
                    0.0,
                    setup_end,
                    corr_min=LEARN_CORR_MIN,
                )
                if hit is not None:
                    found = hit
                    length = hit["end"] - hit["start"]
        if found is None:
            continue
        if found["level_db"] < room_raw + SOURCE_ABOVE_ROOM_DB:
            # The window the shape matched is room tone, not a
            # sound: flat noise correlates with anything, and
            # a beat is a sound he actually splices in.
            continue
        before_words = raw_words[max(0, r_here - 2):r_here + 1]
        after_words = raw_words[r_next:r_next + 2]
        before_tokens = anchor_tokens(before_words)
        after_tokens = anchor_tokens(after_words)
        key = (tuple(before_tokens), tuple(after_tokens))
        if key in seen_anchors:
            continue
        seen_anchors.add(key)
        before_text = " ".join(
            str(w.get("word") or "") for w in before_words
        ).strip()
        after_text = " ".join(
            str(w.get("word") or "") for w in after_words
        ).strip()
        label = (
            "water beat" if "temperature" in before_tokens
            else "signature beat"
        )
        beats.append(
            {
                "anchor_before_text": before_text,
                "anchor_after_text": after_text,
                "anchor_before_tokens": before_tokens,
                "anchor_after_tokens": after_tokens,
                "label": label,
                "length_s": round(length, 3),
                "loudness_db": found["level_db"],
                "pause_before_s": round(pause_before, 3),
                "pause_after_s": round(pause_after, 3),
                "source_start": found["start"],
                "source_end": found["end"],
                "source_corr": found["corr"],
                "profile": _profile(
                    env_raw, found["start"], found["end"]
                ),
                "plain": (
                    f"a {length:.1f} second wordless sound after "
                    f"\u201c{before_text}\u201d, before \u201c{after_text}\u201d"
                ),
            }
        )
    return beats


def beat_id_for(beat: dict, style: str) -> str:
    raw = "|".join(
        [
            style,
            " ".join(beat.get("anchor_before_tokens") or []),
            " ".join(beat.get("anchor_after_tokens") or []),
        ]
    )
    return "beat-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# -- applying ------------------------------------------------------------------


def search_setup(
    env: list,
    room_db: float,
    beat: dict,
    words: list,
) -> Optional[dict]:
    """A matching wordless sound in this video's own setup.

    The setup is the stretch before the first line. The match
    is the learned sound's shape (envelope correlation) at
    about the learned length and loudness; when no window
    carries the shape, the strongest wordless cluster of
    about the learned length is the same class of sound (a
    breath or water transient, never silence, never speech:
    the setup holds no words by construction, and a cluster
    is sound over the room by measurement).
    """
    if not words or not env:
        return None
    setup_end = float(words[0]["start"]) - 0.02
    if setup_end < MIN_BEAT_S:
        return None
    profile = beat.get("profile") or []
    length = float(beat.get("length_s") or 0.0)
    if profile and length > 0:
        found = _search_envelope(
            env,
            room_db,
            profile,
            length,
            0.0,
            setup_end,
            corr_min=APPLY_CORR_MIN,
            loudness_db=beat.get("loudness_db"),
        )
        if found is not None and found["level_db"] >= room_db + SOURCE_ABOVE_ROOM_DB:
            found["how"] = "shape"
            return found
    clusters = [
        c
        for c in _sound_clusters(env, room_db, 0.0, setup_end)
        if c[1] - c[0] >= MIN_BEAT_S
    ]
    if not clusters or length <= 0:
        return None
    candidates = []
    for c_start, c_end in clusters:
        span = c_end - c_start
        if abs(span - length) <= 0.4 * length:
            level = smooth.window_level_db(env, c_start, c_end)
            if level < room_db + SOURCE_ABOVE_ROOM_DB:
                continue
            candidates.append(
                (
                    abs(span - length),
                    -c_end,
                    c_start,
                    c_end,
                    level,
                )
            )
    if not candidates:
        return None
    candidates.sort()
    _, _, c_start, c_end, level = candidates[0]
    return {
        "start": c_start,
        "end": c_end,
        "corr": None,
        "score": None,
        "level_db": round(level, 2),
        "how": "cluster",
    }


def _bound_pair(bounds, index: int):
    """One word's stored sound bounds, in either storage form.

    Plans carry word_sound_bounds as a list indexed by word
    (the Timeline's own reading) or, in older cuts, a dict
    keyed by the index as a string.
    """
    if isinstance(bounds, dict):
        return bounds.get(str(index)) or bounds.get(index)
    if isinstance(bounds, (list, tuple)) and 0 <= index < len(bounds):
        return bounds[index]
    return None

def range_holding(ranges: list, words: list, bounds: dict, index: int):
    """The index of the kept range holding one word, or None.

    A word is held by the non-special range whose span covers
    the middle of the word's measured sound (the stored sound
    bounds when the plan carries them, else the transcript
    stamps), the same reading the Timeline gives word status.
    """
    from .timeline import is_special_range

    pair = _bound_pair(bounds, index)
    if pair:
        mid = (float(pair[0]) + float(pair[1])) / 2.0
    else:
        mid = (float(words[index]["start"]) + float(words[index]["end"])) / 2.0
    for pos, rng in enumerate(ranges):
        if is_special_range(rng):
            continue
        if float(rng["start"]) - 1e-9 <= mid <= float(rng["end"]) + 1e-9:
            return pos
    return None


def apply_to_plan(
    cuts: dict,
    words: list,
    beat: dict,
    source_start: float,
    source_end: float,
    anchor_word: int,
    env: list = None,
    room_db: float = None,
) -> Optional[dict]:
    """Insert the beat into a plan dict, in place.

    The two anchor words must both read kept. The insert is a
    special range holding the donor stretch, placed between
    the ranges that hold the anchor words, in list order. The
    pauses around it come from the natural gap at the anchor:
    the range before ends the learned pause after the first
    anchor word's sound ends, and the range after starts the
    learned pause before the next word's sound starts. When
    the two learned pauses together are wider than the
    natural gap, both shrink in proportion so they fit inside
    it; the words themselves are never touched, and no word's
    kept or cut state changes. Returns the placement detail,
    or None when there is no honest room for the insert.

    The join is measured off the envelope when the caller
    hands one over: the plan's stored per-word bounds are
    loose spans fit for membership midpoints, not edges (they
    overlap their neighbours), and transcript stamps drift.
    Without an envelope the stamps are the fallback.
    """
    from .timeline import Timeline

    timeline = Timeline({"words": words}, cuts)
    i = int(anchor_word)
    if i < 0 or i + 1 >= len(words):
        return None
    if timeline.word_status(i) != "kept" or timeline.word_status(i + 1) != "kept":
        return None
    bounds = cuts.get("word_sound_bounds") or {}

    ranges = cuts.get("kept_ranges") or []
    ri = range_holding(ranges, words, bounds, i)
    rj = range_holding(ranges, words, bounds, i + 1)
    if ri is None or rj is None:
        return None
    if rj not in (ri, ri + 1):
        # Adjacent words held two or more ranges apart: some
        # other insert already sits between them. Not this
        # beat's place; skip honestly.
        return None
    sound_end = float(words[i]["end"])
    sound_start = float(words[i + 1]["start"])
    if env is not None and room_db is not None:
        measured_end = _sound_end(env, room_db, words[i])
        measured_start = _sound_start(env, room_db, words[i + 1])
        # The measured pair refines the stamps only when it
        # is consistent: at a tight join inside continuous
        # speech one cluster spans both words and the pair
        # inverts, and there the stamps are the honest read.
        if (
            measured_end <= measured_start
            and abs(measured_end - sound_end) <= 0.35
            and abs(measured_start - sound_start) <= 0.35
        ):
            sound_end, sound_start = measured_end, measured_start
    gap = max(0.0, sound_start - sound_end)
    pause_before = float(beat.get("pause_before_s") or 0.0)
    pause_after = float(beat.get("pause_after_s") or 0.0)
    if pause_before + pause_after > gap:
        if gap <= 0.0:
            pause_before = 0.0
            pause_after = 0.0
        else:
            scale = gap / (pause_before + pause_after)
            pause_before *= scale
            pause_after *= scale
    prev_end = round(sound_end + pause_before, 4)
    next_start = round(sound_start - pause_after, 4)
    insert = {
        "start": round(float(source_start), 4),
        "end": round(float(source_end), 4),
        "insert_kind": "signature_beat",
        "beat_id": beat.get("beat_id"),
    }
    old_joins = list(cuts.get("join_pauses_s") or [])
    if ri == rj:
        holder = ranges[ri]
        if not (
            float(holder["start"]) < prev_end
            and next_start < float(holder["end"])
            and prev_end <= next_start
        ):
            return None
        left = dict(holder)
        right = dict(holder)
        left["end"] = prev_end
        right["start"] = next_start
        new_ranges = ranges[:ri] + [left, insert, right] + ranges[ri + 1:]
        if len(old_joins) == len(ranges) - 1:
            new_joins = (
                old_joins[:ri]
                + [round(pause_before, 4), round(pause_after, 4)]
                + old_joins[ri:]
            )
        else:
            new_joins = old_joins
        range_index = ri + 1
    else:
        first = ranges[ri]
        second = ranges[rj]
        if not (
            float(first["start"]) < prev_end
            and next_start < float(second["end"])
        ):
            return None
        first["end"] = prev_end
        second["start"] = next_start
        new_ranges = ranges[:ri + 1] + [insert] + ranges[ri + 1:]
        if len(old_joins) == len(ranges) - 1:
            new_joins = (
                old_joins[:ri]
                + [round(pause_before, 4), round(pause_after, 4)]
                + old_joins[rj:]
            )
        else:
            new_joins = old_joins
        range_index = ri + 1
    cuts["kept_ranges"] = new_ranges
    cuts["join_pauses_s"] = new_joins
    total = sum(
        float(r["end"]) - float(r["start"]) for r in new_ranges
    )
    cuts["output_duration_s"] = round(total, 4)
    record = {
        "kind": "signature_beat",
        "beat_id": beat.get("beat_id"),
        "placed_between_words": [i, i + 1],
        "source_start": insert["start"],
        "source_end": insert["end"],
        "pause_before_s": round(pause_before, 4),
        "pause_after_s": round(pause_after, 4),
    }
    cuts.setdefault("post_edits", []).append(record)
    cuts.setdefault("signature", {})["beat"] = record
    return {
        "range_index": range_index,
        "insert": insert,
        "pause_before_s": round(pause_before, 4),
        "pause_after_s": round(pause_after, 4),
        "record": record,
    }



def join_measure(env: list, room_db: float, prev_end: float, next_start: float) -> dict:
    """The join check's own two measures at one join.

    The same readings smooth.join_check takes: the loudness
    jump across the join (100 ms windows either side) and the
    click measure (the level step at the very edge). A join
    fails when either measure trips the check's threshold.
    """
    before = smooth.window_level_db(env, prev_end - 0.10, prev_end)
    after = smooth.window_level_db(env, next_start, next_start + 0.10)
    jump = abs(after - before)
    loudness_jump = bool(
        jump > 3.0 and before > room_db + 6 and after > room_db + 6
    )
    edge_before = smooth.level_at(env, prev_end - smooth.FRAME_S)
    edge_after = smooth.level_at(env, next_start)
    edge_jump = abs(edge_before - edge_after)
    click = bool(
        edge_jump > 14.0 and min(edge_before, edge_after) > room_db + 10
    )
    return {
        "loudness_jump_db": round(jump, 2),
        "edge_jump_db": round(edge_jump, 2),
        "failed": loudness_jump or click,
        "kinds": (
            (["loudness_jump"] if loudness_jump else [])
            + (["click"] if click else [])
        ),
    }
