"""The opening rule: the edit starts on the first real line.

Justin's template (from his finished videos, and his 0.23 Mac
test): everything before the first real line is cut, setup talk
and abandoned first takes alike, and the one breath right before
that line stays, the signature breath.

His daily videos open on a template line ("Today's day N of
..."). A sentence at the head that starts with that template IS
the first real line, full stop (his 0.24 Mac test: the coverage
test below read the opener as an abandoned take because he
quotes it later in the video, and cut it; the edit then opened
on the temperature line). When an opener sentence sits in the
head window it anchors the opening: the last one in the window
is the real line (an earlier one is an abandoned take of it,
the last-take rule), everything before it is pre-content, and a
later quotation or mention of the opener is never a retake of
it, because a quotation does not start with the template.

With no template opener in the window, "first real line" is
found without naming any video: walk the opening sentences of
what the plan still keeps. A sentence before the content counts
as pre-content only when it is clearly not the content itself:

- an abandoned take: a sentence whose words mostly come back in
  one of the next three sentences (the retake pass usually cuts
  these too; this rule is the backstop at the head of the video),
- or chatter: at most four words, set apart from what follows by
  a real pause (0.8 s or more), the way setup talk sits while the
  camera is being fiddled with ("I love you. Good. Thank you.").

The first sentence of five words or more that is neither is the
first real line. If a short sentence flows straight into what
follows, or nothing qualifies, the opening is left exactly as it
was: this rule never eats a real opening to satisfy itself.
"""

from __future__ import annotations

from . import smooth
from .retakes import _coverage, _number_word_value, normalize_word

MIN_CONTENT_WORDS = 5
CHATTER_MAX_WORDS = 4
CHATTER_MIN_PAUSE_S = 0.8
TAKE_OVERLAP = 0.6
MAX_HEAD_SENTENCES = 6
MAX_HEAD_WORDS = 60
BREATH_SEARCH_S = 2.0
# The longest head the video may open with (0.27.3): a "breath"
# run longer than this is setup ambience with a breath in it,
# not the breath itself. His own finished edit opens with
# about 0.7 s of breath before the line; the uncapped run on
# the Day 30 raw measured 1.6 s and read as opening silence.
BREATH_MAX_S = 0.8


def _opener_tokens(tokens: list[str]) -> list[str]:
    """One sentence's tokens with the template's spelling folded.

    A transcript engine that writes the contraction out in full
    names the same line: "today is day thirty" is the template
    opener exactly as "today's day 30" is (Scribe writes the
    first form, with the day as a number word; the study
    transcripts write the second, with digits). Folding "today
    is" to "today's" here lets the one opener check below read
    every engine's spelling the same way.
    """
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if (
            tokens[i] == "today"
            and i + 1 < len(tokens)
            and tokens[i + 1] == "is"
        ):
            out.append("today's")
            i += 2
            continue
        out.append(tokens[i])
        i += 1
    return out


def _kept_indices(words: list[dict], ranges: list[dict]) -> list[int]:
    kept: set[int] = set()
    for rng in ranges:
        if smooth._is_special(rng):
            continue
        kept.update(smooth._range_words(words, rng))
    return sorted(kept)


def _sentences(words: list[dict], kept: list[int], ends: set[int]) -> list[list[int]]:
    """Sentence groups over the kept words at the head."""
    groups: list[list[int]] = []
    current: list[int] = []
    for pos, wi in enumerate(kept):
        current.append(wi)
        nxt = kept[pos + 1] if pos + 1 < len(kept) else None
        gap = (
            float(words[nxt]["start"]) - float(words[wi]["end"])
            if nxt is not None
            else 0.0
        )
        if wi in ends or gap > 1.5 or nxt is None:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def opening_plan(
    words: list[dict],
    ranges: list[dict],
    env: list[float],
    *,
    segments: list[dict] | None = None,
) -> tuple[list[dict], dict]:
    """Cut pre-content talk from the head of the plan.

    Returns (new_ranges, report). When the rule does not fire,
    the ranges come back unchanged and the report says why.
    """
    report = {
        "fired": False,
        "cut_words": [],
        "cut_text": "",
        "first_line_word": None,
        "breath_kept": None,
        "reason": "",
    }
    if not words or not ranges or not env:
        report["reason"] = "nothing to measure"
        return [dict(r) for r in ranges], report
    kept = _kept_indices(words, ranges)
    if not kept:
        report["reason"] = "no kept words"
        return [dict(r) for r in ranges], report
    ends = smooth.sentence_end_indices(words, segments)
    groups = _sentences(words, kept, ends)
    tokens = {
        id(g): [normalize_word(words[i].get("word", "")) for i in g]
        for g in groups
    }

    def is_take(gi: int) -> bool:
        mine = tokens[id(groups[gi])]
        if len(mine) < 2:
            return False
        for gj in range(gi + 1, min(gi + 4, len(groups))):
            if _coverage(mine, tokens[id(groups[gj])]) >= TAKE_OVERLAP:
                return True
        return False

    def is_opener(gi: int) -> bool:
        mine = _opener_tokens(tokens[id(groups[gi])])
        if len(mine) < 3:
            return False
        if mine[0] not in ("today's", "todays") or mine[1] != "day":
            return False
        num = mine[2]
        return (
            num.isdigit()
            or (
                len(num) > 2
                and num[:-2].isdigit()
                and num[-2:] in ("st", "nd", "rd", "th")
            )
            or _number_word_value(num) is not None
        )

    # The template opener anchors the opening when one sits in
    # the head window: the last opener there is the first real
    # line, and it is never read as a take of anything.
    content_at = None
    scanned = 0
    for gi, group in enumerate(groups[:MAX_HEAD_SENTENCES]):
        scanned += len(group)
        if scanned > MAX_HEAD_WORDS and gi > 0:
            break
        if is_opener(gi):
            content_at = gi
    scanned_words = 0
    for gi, group in enumerate(groups[:MAX_HEAD_SENTENCES]):
        if content_at is not None:
            break  # The opener anchor already named the first line.
        scanned_words += len(group)
        if scanned_words > MAX_HEAD_WORDS and gi > 0:
            break
        if len(group) >= MIN_CONTENT_WORDS and not is_take(gi):
            content_at = gi
            break
        if is_take(gi):
            continue
        if len(group) <= CHATTER_MAX_WORDS:
            last = group[-1]
            nxt_pos = kept.index(last) + 1
            pause = (
                float(words[kept[nxt_pos]]["start"]) - float(words[last]["end"])
                if nxt_pos < len(kept)
                else 0.0
            )
            if pause >= CHATTER_MIN_PAUSE_S:
                continue
        # A sentence that is neither a take nor set-apart chatter
        # and is not long enough to be the content line: the head
        # is a real opening in progress. Leave it all alone.
        report["reason"] = "the opening flows; nothing reads as setup talk"
        return [dict(r) for r in ranges], report
    if content_at is None:
        report["reason"] = "no first real line found at the head"
        return [dict(r) for r in ranges], report
    if content_at == 0:
        first_word = groups[0][0]
        report["first_line_word"] = first_word
        report["reason"] = "the video already starts on its first line"
        # Even then, the breath right before the line belongs to
        # the template when the plan does not already hold it.
        new_ranges, breath = _keep_head_breath(
            words, ranges, env, first_word
        )
        if breath:
            report["breath_kept"] = breath
            report["fired"] = True
        return new_ranges, report

    cut_words = [wi for g in groups[:content_at] for wi in g]
    first_word = groups[content_at][0]
    room = smooth.room_level_db(env)
    bounds = smooth.all_word_bounds(words, env, room)
    first_sound_start = bounds[first_word][0]
    cut_set = set(cut_words)
    new_ranges: list[dict] = []
    for rng in ranges:
        if smooth._is_special(rng):
            new_ranges.append(dict(rng))
            continue
        its = smooth._range_words(words, rng)
        if not its:
            new_ranges.append(dict(rng))
            continue
        if all(i in cut_set for i in its):
            continue  # The whole piece is pre-content.
        if any(i in cut_set for i in its):
            piece = dict(rng)
            piece["start"] = round(
                max(float(rng["start"]), first_sound_start - smooth.HEAD_IN_S),
                4,
            )
            if piece["end"] > piece["start"]:
                new_ranges.append(piece)
            continue
        new_ranges.append(dict(rng))
    new_ranges.sort(key=lambda r: float(r["start"]))
    new_ranges, breath = _keep_head_breath(words, new_ranges, env, first_word)
    report.update({
        "fired": True,
        "cut_words": cut_words,
        "cut_text": " ".join(
            str(words[i].get("word", "")) for i in cut_words
        ),
        "first_line_word": first_word,
        "breath_kept": breath,
        "reason": "setup talk and abandoned takes before the first line",
    })
    return new_ranges, report


HEAD_PAD_S = 0.1


def _keep_head_breath(
    words: list[dict], ranges: list[dict], env: list[float], first_word: int
) -> tuple[list[dict], dict | None]:
    """Trim the head to right before the first line.

    Filming-day ruling (P0-3): the video starts right before
    the first word, the first word's measured sound start
    minus a small pad. An earlier rule kept the breath before
    the line whole (capped at 0.8 s in 0.27.3); his keyed run
    still opened on 0.9 s of breath, and he called it: the
    head is trimmed, and any signature-breath piece at the
    head goes with it. (The wordless Signature Beat inside
    the first line, after "Today's temperature is", is a
    different feature and is not touched here.)
    """
    room = smooth.room_level_db(env)
    bounds = smooth.all_word_bounds(words, env, room)
    first_sound_start = bounds[first_word][0]
    head_start = max(0.0, first_sound_start - HEAD_PAD_S)
    out: list[dict] = []
    for rng in ranges:
        if rng.get("insert_kind") == "signature_breath" and float(
            rng["end"]
        ) <= first_sound_start:
            continue  # the kept head breath goes; the head is trimmed
        piece = dict(rng)
        its = smooth._range_words(words, piece)
        if first_word in its and float(piece["start"]) < head_start:
            piece["start"] = round(head_start, 4)
        out.append(piece)
    out.sort(key=lambda r: float(r["start"]))
    return out, None
