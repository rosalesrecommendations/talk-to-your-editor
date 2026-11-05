"""The final sweep: one last look at the settled plan.

Every pass that builds an edit decides from its own evidence:
the retake pass from pair scores over one transcript's
segmentation, the direction pass from a model's proposals, the
silence and smooth passes from the sound. A stopped take can
slip through the seams between them. On Justin's keyed 0.27.2
run the prayer try at "offer up to God" did exactly that: the
one band-B pair that cuts it locally owes its "fragment"
decision to a chain of exact conditions over the saved Scribe
segmentation (the pair's span is assembled by two extension
walks), and on the keyed path that decision is not guaranteed:
a fresh Scribe pass over mumbled, abandoned speech segments
the words differently, and an undecided band-B pair on the
loose path goes to the live judge, whose own style-pack
calibration (a prayer said several ways keeps all of them;
"to offer up to God" plus "my life" is an assembly, keep both)
points it at keeping the try. Once the retake pass left it in,
no later pass ever asked the question again, and the render
simply built the settled plan.

The sweep is that last look, and it asks the question on the
settled plan itself, in every path that settles one (the first
edit, the front door, a direction run, a chat direction), just
before the render and inside the same checkpoint as the run:

- A kept span that trails off mid-sentence into a second or
  more of dead space, whose thought is then said again
  complete later in the edit, is a stopped try: it is cut,
  together with its dead space. The shape is read from sound
  bounds and sentence shape, never from pair records, so it
  does not matter which pass settled the plan or how the
  transcript was segmented.
- The final saying of the video is never cut. If a pass left
  it cut in the settled plan, the sweep puts it back and
  records a your-call spot, so the creator can still cut it
  himself with one tap; it is never the app's call.

Complete sayings are never suspects: a unit must end
mid-sentence to open a span, so deliberate restatements (the
closing prayer says the video's point again, in complete
sentences) are never touched.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Optional

from .retakes import normalize_word
from .verifier import NEGATION_WORDS

# A stopped try trails into at least this much dead space
# (measured between the words' own sound bounds).
DEAD_SPACE_S = 1.0
# A word whose transcript stamp spans at least this long
# while its measured sound is at most half of that is a word
# carrying dead air in its own stamp: Scribe gave the pause
# before "Hey," to the "Hey," token itself (a 2.34 s stamp,
# 0.37 s of sound), and the 2.2 s silence Justin heard lives
# inside that token. Such a word starts its own unit, so the
# sentence tail before it is never glued to the try after it.
TOKEN_DEAD_STAMP_S = 1.5
TOKEN_DEAD_SOUND_SHARE = 0.5
# A tail fragment absorbed into a span ("My life."): at most
# this many words, sitting between the trailing-off unit and
# the completing saying.
FRAGMENT_MAX_WORDS = 5
# A span worth cutting carries at least this many words.
MIN_SPAN_WORDS = 3
# The completing saying must cover at least this share of the
# span's content words.
COVERAGE_MIN = 0.5
# The completing saying must start within this many seconds
# (source time) of the span's end: a thought re-said minutes
# later is a callback, not a retake.
COMPLETER_MAX_DELAY_S = 60.0
# Growth caps: a span is a try, not a passage.
MAX_SPAN_UNITS = 5
MAX_SPAN_WORDS = 40
# The completing saying is one sentence, however many breath
# pauses it carries: it is capped by words, not by units.
COMPLETER_MAX_WORDS = 60
# The final saying is the last sentence; a transcript with no
# usable punctuation must not turn the whole video into one
# "saying", so the span is capped at its last words.
FINAL_SAYING_MAX_WORDS = 40

STOPWORDS = frozenset(
    "the a an and or but if of to in on for with that this it is "
    "was are be been am do does did have has had will would can "
    "could should my your his her our their me him us them i you "
    "he she we they so as at by from not no yes just like going "
    "get got go see say said one really know even also now then "
    "there here what when all any about into over after before "
    "up down out off again once more most very too".split()
)


def punctuation_sentence_ends(words: list[dict]) -> set[int]:
    """Word indices that land a sentence, by punctuation alone.

    The app's canonical sentence ends also count transcript
    segment boundaries, but a segment break is a pause, not a
    landing: Scribe breaks a segment exactly where a take
    stops (the stopped try's "cancer," and "lo-" both end
    segments), so segment-inclusive ends would call every
    stopped try a finished sentence. A word lands its sentence
    when its text ends in ., !, or ? (closing quotes aside);
    a word ending in an ellipsis trails off and does not land.
    """
    ends: set = set()
    for i, word in enumerate(words):
        text = str(word.get("word", "")).strip().rstrip("\"'”’)")
        if not text:
            continue
        if text.endswith("...") or text.endswith(".."):
            continue
        if text[-1] in ".!?":
            ends.add(i)
    return ends


def word_bounds(words: list[dict], plan: dict) -> list[tuple[float, float]]:
    """Each word's sound bounds: the plan's measured bounds when
    it carries them, the transcript stamps otherwise."""
    raw = plan.get("word_sound_bounds") or []
    if len(raw) == len(words):
        try:
            return [(float(pair[0]), float(pair[1])) for pair in raw]
        except (TypeError, ValueError, IndexError):
            pass
    return [
        (float(w.get("start") or 0.0), float(w.get("end") or 0.0))
        for w in words
    ]


def _token_carries_dead_air(
    word: dict, sound: tuple[float, float]
) -> bool:
    """True when a word's stamp is long but its sound is short:
    the stamp is carrying the dead air before the word."""
    try:
        stamp = float(word.get("end") or 0.0) - float(word.get("start") or 0.0)
    except (TypeError, ValueError):
        return False
    if stamp < TOKEN_DEAD_STAMP_S:
        return False
    return (sound[1] - sound[0]) <= TOKEN_DEAD_SOUND_SHARE * stamp


def kept_units(
    words: list[dict],
    statuses: list[str],
    bounds: list[tuple[float, float]],
    sentence_ends: set[int],
) -> list[dict]:
    """The kept speech grouped into sentence-ish units.

    A new unit starts at a sentence end, or across dead space:
    a gap of DEAD_SPACE_S or more between two kept words that
    are adjacent in the transcript, with no words of any kind
    in between. A stretch a pass already cut is an edit, not
    dead space: when cut words sit between two kept words, the
    sentence flows across the seam and no gap is recorded,
    however far apart the kept words sit in the source. Each
    unit records whether it lands its sentence and the dead
    space after it (0.0 across an edit seam).
    """
    kept = [i for i in range(len(words)) if statuses[i] == "kept"]
    units: list[dict] = []
    start: Optional[int] = None
    prev: Optional[int] = None
    for index in kept:
        if start is None:
            start = index
        elif prev in sentence_ends or (
            index == prev + 1
            and (
                bounds[index][0] - bounds[prev][1] >= DEAD_SPACE_S
                or _token_carries_dead_air(words[index], bounds[index])
            )
        ):
            units.append({"word_start": start, "word_end": prev + 1})
            start = index
        prev = index
    if start is not None and prev is not None:
        units.append({"word_start": start, "word_end": prev + 1})
    for pos, unit in enumerate(units):
        unit["ends_sentence"] = (unit["word_end"] - 1) in sentence_ends
        if pos + 1 < len(units):
            nxt = units[pos + 1]["word_start"]
            last = unit["word_end"] - 1
            if nxt == last + 1:
                unit["gap_after"] = round(
                    max(0.0, bounds[nxt][0] - bounds[last][1]), 4
                )
            else:
                unit["gap_after"] = 0.0
        else:
            unit["gap_after"] = None
    return units


def final_saying_of_video(
    words: list[dict],
    bounds: list[tuple[float, float]],
    sentence_ends: set[int],
    end_word: Optional[int] = None,
) -> Optional[tuple[int, int]]:
    """The video's final saying: the last sentence of the
    recorded speech, whether the current plan keeps it or not.

    The saying is a property of the recording, not of the
    edit. Read off the kept plan, a pass that already cut the
    last line would redefine the final saying as whatever
    sentence now ends the edit, and its own cut would stand;
    read off the transcript, the last line is the last line,
    and the sweep can put it back. When the closing rule has
    settled where the video ends (``end_word``: the first
    word after the closing block), the saying is read only
    up to that end; speech after the sign-off is not the
    video's last line, it is after the video.
    """
    statuses = ["kept"] * len(words)
    units = kept_units(words, statuses, bounds, sentence_ends)
    if end_word is not None:
        units = [u for u in units if u["word_end"] <= end_word]
    return final_saying_span(units)


def final_saying_span(units: list[dict]) -> Optional[tuple[int, int]]:
    """The video's final saying, as (word_start, word_end).

    The last sentence of the kept speech: the last unit, plus
    any units it flows from since the previous sentence end.
    """
    if not units:
        return None
    start = len(units) - 1
    while start > 0 and not units[start - 1]["ends_sentence"]:
        start -= 1
    word_start = units[start]["word_start"]
    word_end = units[-1]["word_end"]
    if word_end - word_start > FINAL_SAYING_MAX_WORDS:
        word_start = word_end - FINAL_SAYING_MAX_WORDS
    return word_start, word_end


def _content_tokens(
    words: list[dict], start: int, end: int
) -> tuple[list[str], list[str]]:
    """A word span's content tokens, with the raw words beside
    them (a cut-off word's raw form ends in '-')."""
    tokens: list[str] = []
    raw: list[str] = []
    for i in range(start, end):
        token = normalize_word(words[i].get("word", "")).rstrip("-")
        if len(token) >= 2 and token not in STOPWORDS:
            tokens.append(token)
            raw.append(str(words[i].get("word", "")))
    return tokens, raw


def _coverage(span: list[str], span_raw: list[str], pool: list[str]) -> float:
    """How much of the span's content the pool says again.

    Counted over the span's distinct content words: a try that
    says "God" three times and is completed by a saying that
    says it once is covered, not diluted by its own repeats.
    A cut-off span token (its raw word ends in a dash) also
    counts when a pool token completes it: the stopped try's
    "lo-" is covered by the completing saying's "love". A
    two-letter cut-off is ambiguous ("lo-" could complete to
    "long"), so a short cut-off counts only when another of
    the span's words is covered directly: one ambiguous
    fragment alone is not evidence of a re-say.
    """
    if not span:
        return 0.0
    pool_set = set(pool)
    distinct: dict = {}
    for token, raw in zip(span, span_raw):
        distinct.setdefault(token, raw)
    if not distinct:
        return 0.0
    direct = {
        token for token in distinct if token in pool_set
    }
    covered = len(direct)
    for token, raw in distinct.items():
        if token in direct:
            continue
        if not (
            raw.rstrip("\"'”").endswith("-")
            and len(token) >= 2
            and any(
                candidate.startswith(token) for candidate in pool_set
            )
        ):
            continue
        if len(token) >= 3 or direct:
            covered += 1
    return covered / len(distinct)


def find_stopped_spans(
    words: list[dict],
    statuses: list[str],
    bounds: list[tuple[float, float]],
    sentence_ends: set[int],
    protected_words: Optional[set] = None,
    final_span: Optional[tuple[int, int]] = None,
) -> list[dict]:
    """Kept spans that read as stopped tries on the settled plan.

    Each result: {word_start, word_end, completer_word_start,
    completer_word_end, gap_s, coverage, blocked}. A blocked
    span (it touches protected words, an open your-call span,
    or the final saying) is reported, never cut by the caller.
    """
    protected_words = protected_words or set()
    units = kept_units(words, statuses, bounds, sentence_ends)
    results: list[dict] = []
    claimed: set = set()
    for i, unit in enumerate(units):
        if i in claimed:
            continue
        if unit["ends_sentence"] or unit["gap_after"] is None:
            continue
        if unit["gap_after"] < DEAD_SPACE_S:
            continue
        # Grow the span over the units that also trail off.
        # A sentence-ending unit is never grown over: it is
        # either the try's short tail fragment (handled below,
        # as the span's tail) or the completing saying itself.
        j = i
        while j + 1 < len(units) and (j - i + 1) < MAX_SPAN_UNITS:
            nxt = units[j + 1]
            if nxt["ends_sentence"]:
                break
            j += 1
        word_start = unit["word_start"]
        word_end = units[j]["word_end"]
        if not (MIN_SPAN_WORDS <= word_end - word_start <= MAX_SPAN_WORDS):
            continue
        # The unit right after the span may be the try's tail:
        # a short unit that lands a sentence ("My life." after
        # "I would lo-"). It belongs to the try, so the span's
        # end extends over it once the span itself validates,
        # and the completing saying is read after it.
        tail = None
        comp = j + 1
        if comp < len(units):
            nxt = units[comp]
            if (
                nxt["ends_sentence"]
                and nxt["word_end"] - nxt["word_start"]
                <= FRAGMENT_MAX_WORDS
            ):
                tail = comp
                comp += 1
        if comp >= len(units):
            continue
        # The completing saying: the run of units from there,
        # through the first one that lands a sentence.
        comp_end = comp
        while (
            comp_end < len(units)
            and not units[comp_end]["ends_sentence"]
            and units[comp_end]["word_end"] - units[comp]["word_start"]
            <= COMPLETER_MAX_WORDS
        ):
            comp_end += 1
        if comp_end >= len(units) or not units[comp_end]["ends_sentence"]:
            continue
        comp_start_word = units[comp]["word_start"]
        comp_end_word = units[comp_end]["word_end"]
        delay = bounds[comp_start_word][0] - bounds[word_end - 1][1]
        if delay > COMPLETER_MAX_DELAY_S:
            continue
        pool, _pool_raw = _content_tokens(words, comp_start_word, comp_end_word)
        # The completing saying must restart the thought, not
        # continue the span's own sentence. A continuation
        # ("...can" [pause] "do whatever he wants. I'm not
        # saying...") can share words with the span ("I'm",
        # "saying") and pass a coverage check, but its first
        # content words are new; a restart begins at the
        # thought's beginning. So one of the completer's first
        # two content words must be among the span's first
        # four content words, or this is no re-say.
        span_head, _span_head_raw = _content_tokens(
            words, word_start, word_end
        )
        if not any(
            token in set(span_head[:4]) for token in pool[:2]
        ):
            continue
        # The span starts where the re-said thought starts.
        # The unit that opened the span may be the tail of the
        # sentence before the try (the prayer lead-in "...to
        # offer up to God," flows straight into the take that
        # follows it): a leading unit the completing saying
        # barely re-says is not part of the try, so it is
        # trimmed off the span's head, and the span is judged
        # on what remains.
        while j - i + 1 > 1:
            head = units[i]
            head_tokens, head_raw = _content_tokens(
                words, head["word_start"], head["word_end"]
            )
            # A head with less than two content words is a
            # piece of the try itself (its "Hey,"), never a
            # sentence tail: trimming stops there.
            if len(head_tokens) < 2:
                break
            # The head stays only when the completing saying
            # re-says most of it; a head at exactly half is the
            # previous sentence's tail (the lead-in's "to offer
            # up to God" is half covered by its "God" alone),
            # so the bar to stop trimming is above COVERAGE_MIN.
            if _coverage(head_tokens, head_raw, list(pool)) > COVERAGE_MIN:
                break
            i += 1
            word_start = units[i]["word_start"]
        if not (MIN_SPAN_WORDS <= word_end - word_start <= MAX_SPAN_WORDS):
            continue
        span_tokens, span_raw = _content_tokens(words, word_start, word_end)
        coverage = _coverage(span_tokens, span_raw, pool)
        if coverage < COVERAGE_MIN:
            continue
        # The span validates: its tail fragment goes with it.
        if tail is not None:
            word_end = units[tail]["word_end"]
        blocked = None
        if any(k in protected_words for k in range(word_start, word_end)):
            blocked = "protected"
        elif final_span and word_start < final_span[1] and final_span[0] < word_end:
            blocked = "final_saying"
        results.append(
            {
                "word_start": word_start,
                "word_end": word_end,
                "completer_word_start": comp_start_word,
                "completer_word_end": comp_end_word,
                "gap_s": units[i]["gap_after"],
                "coverage": round(coverage, 3),
                "blocked": blocked,
            }
        )
        claimed.update(range(i, (tail if tail is not None else j) + 1))
    return results


# -- restart chains -----------------------------------------------------------
#
# The pair machinery reads one segmentation of the transcript
# and leaves its middle band to a judge. With no judge in the
# run, fluent restart chains survive it: the local transcript
# punctuates stopped tries as complete sentences (so the
# fragment rules never see them), merges a whole chain into a
# single unit (so no pair ever forms), and a chain's middle
# tries match no single later saying well enough to pass the
# pair gates. The sweep asks the chain question on the settled
# plan, at word level, in every path:
#
# - Inside one saying: a phrase begun, stopped, and begun
#   again with no sentence break between the tries. The tries
#   are cut; the saying keeps its last, complete run.
# - Across sayings: a run of sayings that keep returning to
#   the same opening, where the material after each earlier
#   saying says most of it again, and the run lands in a
#   complete saying. The earlier sayings are the tries; only
#   the landing saying stays. A single stopped fragment
#   before a fresh start is a chain of one.
#
# The protections mirror the rest of the sweep: protected
# words and the final saying block a cut (reported, never
# forced), and the closing section (the final fifth of the
# kept words, where the closing prayer restates the point on
# purpose) is never a chain try.

CHAIN_WINDOW_S = 45.0  # one thought, retried over seconds
CHAIN_MAX_TRIES = 6  # a longer run is a refrain, not a retry
CHAIN_COVER_CUT = 0.6  # the following material says the try again
CHAIN_COVER_STOPPED = 0.45  # a stopped-shaped try needs less cover
INTRA_COVER = 0.8  # inside one saying the cover must be near-total
CLOSING_SECTION_FRACTION = 0.8  # the final fifth is the closing

_FUNCTION_END_WORDS = frozenset(
    "that with and the a an to of in on at for from by as is are "
    "was were be been being if when because but or it its this "
    "these those there than then so has have had do does can "
    "could will would shall should may might must what which "
    "who where why how into about over under again".split()
)

_STOPWORDS = frozenset(
    "the a an and or but if then so of to in on at for with "
    "from by as it its this that these those i you he she we "
    "they me him her us them my your his their our am is are "
    "was were be been being do does did have has had can "
    "could will would shall should may might must not no".split()
)


def plan_sayings(
    words: list[dict],
    statuses: list[str],
    bounds: list[tuple[float, float]],
    sentence_ends: set,
) -> list[dict]:
    """The kept speech grouped into sayings.

    A saying ends at a sentence end, or where a second or
    more of dead space (measured between the words' own sound
    bounds) sits between one kept word and the next. Each
    saying carries its word span, whether it ends a sentence,
    and its normalized tokens.
    """
    kept = [i for i, s in enumerate(statuses) if s == "kept"]
    groups: list[list[int]] = []
    current: list[int] = []
    for pos, wi in enumerate(kept):
        if current:
            prev = kept[pos - 1]
            gap = bounds[wi][0] - bounds[prev][1]
            if prev in sentence_ends or gap >= DEAD_SPACE_S:
                groups.append(current)
                current = []
        current.append(wi)
    if current:
        groups.append(current)
    sayings = []
    for group in groups:
        sayings.append(
            {
                "word_start": group[0],
                "word_end": group[-1] + 1,
                "kept_pos": kept.index(group[0]),
                "ends_sentence": group[-1] in sentence_ends,
                "tokens": [
                    normalize_word(words[i].get("word", ""))
                    for i in group
                ],
            }
        )
    return sayings


def _start_match(a: list[str], b: list[str]) -> int:
    """Tokens matched from the start of both lists, allowing
    one slip (a word dropped or swapped on either side), the
    same tolerance the retake pair scoring uses."""
    i = j = matched = 0
    slipped = False
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            matched += 1
            i += 1
            j += 1
        elif not slipped:
            slipped = True
            if j + 1 < len(b) and a[i] == b[j + 1]:
                j += 1
            elif i + 1 < len(a) and a[i + 1] == b[j]:
                i += 1
            else:
                break
        else:
            break
    return matched


def _containment(a: list[str], b: list[str]) -> float:
    """How much of ``a`` is said again inside ``b``, in order."""
    if not a:
        return 0.0
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    matched = sum(
        block.size for block in matcher.get_matching_blocks()
    )
    return matched / len(a)


def _try_coverage(a: list[str], b: list[str]) -> float:
    """A try's cover by later material: said again in order,
    or restarted from the same opening."""
    if not a:
        return 0.0
    return max(_containment(a, b), _start_match(a, b) / len(a))


def _one_sided_negation(a: list[str], b: list[str]) -> bool:
    """One side carries a negation the other does not."""
    return any(t in NEGATION_WORDS for t in a) != any(
        t in NEGATION_WORDS for t in b
    )


def _negation_blocks(
    try_tokens: list[str],
    block_tokens: list[str],
    *,
    try_complete: bool,
    block_complete: bool,
    coverage: float,
) -> bool:
    """A one-sided negation makes the cut the creator's call
    only when the two sides are genuine alternative copies:
    both complete sayings, and the later material says the
    try almost whole (the pair machinery's settled rule,
    fix 9). A stopped try that never reached the negation
    asserts no competing meaning, and a keeper that reaches
    a negation only in its own new ending does not protect
    the tries before it."""
    return (
        _one_sided_negation(try_tokens, block_tokens)
        and try_complete
        and block_complete
        and coverage >= INTRA_COVER
    )


def _anchor_link(try_tokens: list[str], block_tokens: list[str]) -> bool:
    """The try and the material after it open the same way:
    the same start, the same start a few words in (the keeper
    opens with a lead-in the try did not have), or, for a
    short stumble, a shared content bigram in the block's
    head."""
    if _start_match(try_tokens, block_tokens) >= 3:
        return True
    for offset in range(1, min(9, len(block_tokens) - 3)):
        if _start_match(try_tokens, block_tokens[offset:]) >= 4:
            return True
    if len(try_tokens) <= 6:
        head = block_tokens[:12]
        head_bigrams = set(zip(head, head[1:]))
        for pair in zip(try_tokens, try_tokens[1:]):
            if pair in head_bigrams and (
                pair[0] not in _STOPWORDS
                or pair[1] not in _STOPWORDS
            ):
                return True
    return False


def _restart_pairs(tokens: list[str]) -> list[tuple[int, int, int]]:
    """(earlier, later, matched) triples: a later opening
    that matches an earlier opening for at least three
    tokens. Positions inside one repeated run match too
    (each suffix of the run opens like the run), so callers
    pick the strongest pair, not the last position."""
    n = len(tokens)
    pairs = []
    for p in range(2, n - 2):
        for q in range(0, p - 1):
            matched = _start_match(tokens[q:], tokens[p:])
            if matched >= 3:
                pairs.append((q, p, matched))
    return pairs


def _restart_positions(tokens: list[str]) -> list[int]:
    """Positions in a token list where a saying restarts."""
    return sorted({p for _q, p, _m in _restart_pairs(tokens)})


def _intra_saying_cuts(saying: dict) -> list[tuple[int, int]]:
    """Word spans inside one saying that are earlier tries at
    the saying's own ending.

    Restart positions are later openings that match an
    earlier opening for at least three tokens. The final
    completion starts at the last restart position; walking
    back from it, each earlier try is cut while the
    completion covers it near-totally. The saying must land
    (end a sentence): a saying that itself trails off is the
    chain scan's question, not this one's.
    """
    tokens = saying["tokens"]
    n = len(tokens)
    if n < 6 or not saying["ends_sentence"]:
        return []

    pairs = _restart_pairs(tokens)
    if not pairs:
        return []
    # The final completion starts where the strongest
    # repeated opening lands (the longest match; the latest
    # position breaks a tie).
    final_start = max(pairs, key=lambda pair: (pair[2], pair[1]))[1]
    completion = tokens[final_start:]
    # Try starts: the earlier openings, collapsed to the
    # strongest start in each cluster (openings a token or
    # two apart are the same start read at a slide).
    candidates = sorted(
        {q for q, p, _m in pairs if p <= final_start}
        | {p for _q, p, _m in pairs if p < final_start}
    )
    best_match: dict = {}
    for q, _p, m in pairs:
        best_match[q] = max(best_match.get(q, 0), m)
    starts: list[int] = []
    for q in sorted(
        candidates, key=lambda c: -best_match.get(c, 0)
    ):
        if all(abs(q - kept) >= 3 for kept in starts):
            starts.append(q)
    starts.sort()
    if not starts:
        return []
    edges = starts + [final_start]
    accepted_from = None
    for k in range(len(edges) - 2, -1, -1):
        try_tokens = tokens[edges[k]:edges[k + 1]]
        if _one_sided_negation(try_tokens, completion):
            break
        if _try_coverage(try_tokens, completion) >= INTRA_COVER:
            accepted_from = edges[k]
        else:
            break
    if accepted_from is None:
        return []
    return [
        (
            saying["word_start"] + accepted_from,
            saying["word_start"] + final_start,
        )
    ]


def _chain_try_spans(
    saying: dict, block: list[str], block_complete: bool = True
) -> list[tuple[int, int, float]]:
    """Word spans of one saying that are tries at the block
    after it, as (word_start, word_end, coverage).

    The whole saying is tested first. A saying can also glue
    two tries together with no sentence break (a stopped try,
    a restart, another stop): then its tail, split at the
    saying's last internal restart, is tested on its own,
    and the head that remains is tested again, so a glued
    pair of tries is cut piece by piece instead of surviving
    as one span that matches nothing.
    """
    tokens = saying["tokens"]
    stopped_shape = (
        not saying["ends_sentence"]
        or (tokens and tokens[-1] in _FUNCTION_END_WORDS)
        or len(tokens) <= 6
    )

    def passes(
        window: list[str], window_complete: bool
    ) -> Optional[float]:
        coverage = _try_coverage(window, block)
        if _negation_blocks(
            window,
            block,
            try_complete=window_complete,
            block_complete=block_complete,
            coverage=coverage,
        ):
            return None
        if _anchor_link(window, block) and (
            coverage >= CHAIN_COVER_CUT
            or (stopped_shape and coverage >= CHAIN_COVER_STOPPED)
        ):
            return coverage
        return None

    spans: list[tuple[int, int, float]] = []
    lo, hi = 0, len(tokens)
    while hi - lo >= 2:
        coverage = passes(
            tokens[lo:hi],
            saying["ends_sentence"] and lo == 0 and hi == len(tokens),
        )
        if coverage is not None:
            spans.append(
                (
                    saying["word_start"] + lo,
                    saying["word_start"] + hi,
                    coverage,
                )
            )
            break
        restarts = [
            p
            for p in _restart_positions(tokens[lo:hi])
            if 2 <= p <= (hi - lo) - 3
        ]
        if not restarts:
            break
        split = lo + restarts[-1]
        coverage = passes(tokens[split:hi], False)
        if coverage is None:
            break
        spans.append(
            (
                saying["word_start"] + split,
                saying["word_start"] + hi,
                coverage,
            )
        )
        hi = split
    return spans


def find_restart_chains(
    words: list[dict],
    statuses: list[str],
    bounds: list[tuple[float, float]],
    sentence_ends: set,
    *,
    protected_words: Optional[set] = None,
    final_span: Optional[tuple[int, int]] = None,
) -> list[dict]:
    """Restart chains in the settled plan, as cut records.

    Each record: the try's word span, the landing saying's
    word span, the cover, and ``blocked`` (None when the cut
    is clear, "protected" or "final_saying" when a hard
    protection holds it). Intra-saying records carry kind
    "intra" and their own saying as the keeper.
    """
    protected = protected_words or set()
    sayings = plan_sayings(words, statuses, bounds, sentence_ends)
    kept_total = sum(
        s["word_end"] - s["word_start"] for s in sayings
    )
    results: list[dict] = []
    emitted: set = set()

    def emit(record: dict) -> None:
        key = (record["word_start"], record["word_end"])
        if key in emitted:
            return
        emitted.add(key)
        results.append(record)

    def blocked_for(start: int, end: int) -> Optional[str]:
        if any(i in protected for i in range(start, end)):
            return "protected"
        if final_span is not None and (
            start < final_span[1] and final_span[0] < end
        ):
            return "final_saying"
        return None

    # Inside one saying first: the cuts shrink the sayings the
    # chain scan then reads, never the other way round.
    intra_spans: set = set()
    for saying in sayings:
        for start, end in _intra_saying_cuts(saying):
            if saying["kept_pos"] >= CLOSING_SECTION_FRACTION * max(
                kept_total, 1
            ):
                continue
            blocked = blocked_for(start, end)
            emit(
                {
                    "word_start": start,
                    "word_end": end,
                    "keeper_word_start": saying["word_start"],
                    "keeper_word_end": saying["word_end"],
                    "coverage": None,
                    "kind": "intra",
                    "blocked": blocked,
                }
            )
            intra_spans.update(range(start, end))

    # Across sayings: walk back from each landing saying.
    # The block a try is tested against is everything said
    # after it up to the landing saying: a keeper can open
    # with a short head saying of its own, and a chain's
    # middle tries are covered by the tries after them, not
    # by the landing saying alone.
    for j, keeper in enumerate(sayings):
        if not keeper["ends_sentence"] and j != len(sayings) - 1:
            continue
        tries = []
        skipped_head = False
        i = j - 1
        while i > 0 and len(tries) < CHAIN_MAX_TRIES:
            cand = sayings[i]
            if (
                words[keeper["word_start"]].get("start", 0.0)
                - words[cand["word_start"]].get("start", 0.0)
                > CHAIN_WINDOW_S
            ):
                break
            if any(
                wi in intra_spans
                for wi in range(cand["word_start"], cand["word_end"])
            ):
                break
            if cand["kept_pos"] >= CLOSING_SECTION_FRACTION * max(
                kept_total, 1
            ):
                break
            block = [
                token
                for s in sayings[i + 1: j + 1]
                for token in s["tokens"]
            ][:60]
            spans = _chain_try_spans(
                cand, block, block_complete=keeper["ends_sentence"]
            )
            if not spans:
                # The saying right before the landing saying can
                # be the landing saying's own head (a short
                # lead-in the transcript punctuated apart). Skip
                # it once: it stays in the edit, and the walk
                # tests what came before it against the whole
                # landing unit.
                if (
                    not skipped_head
                    and not tries
                    and i == j - 1
                    and len(cand["tokens"]) <= 6
                ):
                    skipped_head = True
                    i -= 1
                    continue
                break
            for start, end, coverage in spans:
                tries.append((start, end, coverage))
            i -= 1
        for start, end, coverage in tries:
            blocked = blocked_for(start, end)
            emit(
                {
                    "word_start": start,
                    "word_end": end,
                    "keeper_word_start": keeper["word_start"],
                    "keeper_word_end": keeper["word_end"],
                    "coverage": round(coverage, 3),
                    "kind": "chain",
                    "blocked": blocked,
                }
            )
    return results


# -- lead-in chains ------------------------------------------------------------
#
# Sayings that start the same way, close together, are the
# creator searching for the line, not separate points (his
# ruling, Nov 4): keep only the last complete saying, even
# when the earlier ones end differently. The coverage test
# in find_restart_chains cannot see these: two tries can land
# different thoughts and share almost no words beyond the
# opening, so no later saying ever "covers" them. The shared
# opening IS the evidence. A cluster is a run of complete
# sayings sharing their first LEAD_IN_WORDS tokens, with only
# fragments between them and successive starts inside
# CHAIN_WINDOW_S. Fragments between members that share the
# opening are tries too. The exception, as with every
# differing copy: a try carrying a negation the keeper does
# not carry, or a number the keeper does not say, is his
# call, never auto-cut; it waits, and it does not protect
# the other tries. A negation on both sides is no exception:
# the try is cut with the rest of the chain.

LEAD_IN_WORDS = 3

_NEGATION_TOKENS = frozenset(
    "not never no cannot cant wont dont doesnt didnt isnt "
    "arent wasnt werent couldnt shouldnt wouldnt havent "
    "hasnt hadnt aint".split()
)

_NUMBER_TOKENS = frozenset(
    "zero one two three four five six seven eight nine ten "
    "eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty thirty forty fifty "
    "sixty seventy eighty ninety hundred thousand million "
    "first second third".split()
)


def _tail_of_previous(tokens: list, member_tokens: list) -> bool:
    """A short saying folds into the member before it only
    when it re-says part of that member (its content words
    mostly appear there): a tail is the member's own ending
    said again in miniature, never the next saying's head."""
    content = [
        t for t in tokens if len(t) >= 2 and t not in _STOPWORDS
    ]
    if not content:
        return False
    member = set(member_tokens)
    hits = sum(1 for t in content if t in member)
    return hits * 2 >= len(content)


def _has_negation(tokens: list) -> bool:
    return any(
        t in _NEGATION_TOKENS or t.endswith("n't") for t in tokens
    )


def _try_waits(tokens: list, keeper_tokens: list) -> bool:
    """The differing-copy exception: a negation the keeper
    does not carry, or a number the keeper does not say,
    makes the try his call instead of an auto-cut. A
    negation on both sides is no difference at all (his
    ruling, Nov 4): the try is cut with the rest of the
    chain, under the same chain spot."""
    if _has_negation(tokens) and not _has_negation(keeper_tokens):
        return True
    keeper_numbers = {
        t for t in keeper_tokens
        if t.isdigit() or t in _NUMBER_TOKENS
    }
    return any(
        (t.isdigit() or t in _NUMBER_TOKENS)
        and t not in keeper_numbers
        for t in tokens
    )


def find_lead_in_chains(
    words: list[dict],
    statuses: list[str],
    bounds: list[tuple[float, float]],
    sentence_ends: set,
    *,
    protected_words: Optional[set] = None,
    final_span: Optional[tuple[int, int]] = None,
) -> list[dict]:
    """Lead-in chain clusters in the given segmentation.

    Each cluster: {word_start, word_end (the pre-keeper try
    block), keeper_word_start, keeper_word_end, tries:
    [{word_start, word_end, waits, blocked}]}. The keeper is
    the cluster's last complete saying; every earlier member
    and every same-opening fragment before it is a try.
    """
    protected = protected_words or set()
    sayings = plan_sayings(words, statuses, bounds, sentence_ends)
    kept_total = sum(
        s["word_end"] - s["word_start"] for s in sayings
    )
    if kept_total <= 0:
        return []

    def lead(saying: dict) -> tuple:
        return tuple(saying["tokens"][:LEAD_IN_WORDS])

    def blocked_for(start: int, end: int) -> Optional[str]:
        if any(i in protected for i in range(start, end)):
            return "protected"
        if final_span is not None and (
            start < final_span[1] and final_span[0] < end
        ):
            return "final_saying"
        return None

    clusters: list[dict] = []
    complete_idx = [
        i for i, s in enumerate(sayings) if s["ends_sentence"]
    ]
    grouped: set = set()
    for pos, idx in enumerate(complete_idx):
        if idx in grouped:
            continue
        first = sayings[idx]
        key = lead(first)
        if len(key) < LEAD_IN_WORDS:
            continue
        # Members are (saying index, end with any tail folded
        # in). A short complete saying (a tail sentence like
        # "God died for us.") sitting between two members is
        # the previous member's tail, not a new point: it
        # folds into that member instead of breaking the run.
        members: list = [(idx, first["word_end"])]
        last_start = bounds[first["word_start"]][0]
        j = pos + 1
        while j < len(complete_idx):
            cand = sayings[complete_idx[j]]
            if lead(cand) == key and (
                bounds[cand["word_start"]][0] - last_start
                <= CHAIN_WINDOW_S
            ):
                members.append(
                    (complete_idx[j], cand["word_end"])
                )
                last_start = bounds[cand["word_start"]][0]
                j += 1
                continue
            if (
                len(cand["tokens"]) <= 5
                and j + 1 < len(complete_idx)
                and lead(sayings[complete_idx[j + 1]]) == key
                and bounds[
                    sayings[complete_idx[j + 1]]["word_start"]
                ][0] - last_start
                <= CHAIN_WINDOW_S
                and _tail_of_previous(
                    cand["tokens"],
                    sayings[members[-1][0]]["tokens"],
                )
            ):
                prev_idx, _prev_end = members[-1]
                members[-1] = (prev_idx, cand["word_end"])
                j += 1
                continue
            break
        if len(members) < 2:
            continue
        grouped.update(m for m, _e in members)
        keeper_idx, keeper_end = members[-1]
        keeper = sayings[keeper_idx]
        tries: list[dict] = []

        def add_try(start: int, end: int, tokens: list) -> None:
            blocked = blocked_for(start, end)
            waits = blocked is not None or _try_waits(
                tokens, keeper["tokens"]
            )
            tries.append(
                {
                    "word_start": start,
                    "word_end": end,
                    "waits": waits,
                    "blocked": blocked,
                }
            )

        member_starts = {m for m, _e in members}
        for m_idx, m_end in members[:-1]:
            member = sayings[m_idx]
            if (
                member["kept_pos"]
                >= CLOSING_SECTION_FRACTION * kept_total
            ):
                tries.append(
                    {
                        "word_start": member["word_start"],
                        "word_end": m_end,
                        "waits": True,
                        "blocked": "closing",
                    }
                )
            else:
                add_try(
                    member["word_start"], m_end, member["tokens"]
                )
        for s_idx in range(members[0][0], keeper_idx):
            if s_idx in member_starts:
                continue
            saying = sayings[s_idx]
            if (
                not saying["ends_sentence"]
                and lead(saying) == key
            ):
                add_try(
                    saying["word_start"],
                    saying["word_end"],
                    saying["tokens"],
                )
        tries.sort(key=lambda t: t["word_start"])
        if not tries:
            continue
        clusters.append(
            {
                "word_start": tries[0]["word_start"],
                "word_end": tries[-1]["word_end"],
                "keeper_word_start": keeper["word_start"],
                "keeper_word_end": keeper_end,
                "tries": tries,
            }
        )
    return clusters


# -- the closing block ---------------------------------------------------------
#
# His videos end the same way: a sign-off (a farewell, a
# blessing) and the subscribe lines. That block is the end of
# the video. Speech recorded after it (he talks to the room,
# comments on the take) is off-script by shape, not by
# content: it sits after the closing block completes. The
# rule reads the block's shape only: the last sign-off-shaped
# saying near the end of the recording, extended through any
# subscribe-shaped sayings right after it. Anything kept
# after the block is the trailing speech the caller cuts.

_SIGNOFF_TOKENS = frozenset({"goodbye", "bye"})


def _signoff_shaped(tokens: list[str]) -> bool:
    """A saying shaped like a sign-off: a farewell formula
    ("see you tomorrow", a goodbye), or a short standalone
    blessing ("God bless you, friends"). A long prayer that
    asks God to bless someone is a petition, not a sign-off,
    so the blessing shape only counts on a short saying."""
    if any(t in _SIGNOFF_TOKENS for t in tokens):
        return True
    if "see" in tokens and "tomorrow" in tokens:
        return True
    return "bless" in tokens and len(tokens) <= 8


def closing_block_end(
    words: list[dict],
    statuses: list[str],
    bounds: list[tuple[float, float]],
    sentence_ends: set,
) -> Optional[int]:
    """The word index where the video's closing block ends,
    or None when no closing block settles an end.

    The block must sit at the end of the kept speech: it
    finishes in the last fifth of the kept words and what
    follows it is a short tail (at most a fifth). A sign-off
    shape in the middle of a video settles nothing.
    """
    sayings = plan_sayings(words, statuses, bounds, sentence_ends)
    if not sayings:
        return None
    kept_total = sum(
        s["word_end"] - s["word_start"] for s in sayings
    )
    if kept_total <= 0:
        return None
    last_signoff = None
    for idx, saying in enumerate(sayings):
        if _signoff_shaped(saying["tokens"]):
            last_signoff = idx
    if last_signoff is None:
        return None
    end_idx = last_signoff
    while end_idx + 1 < len(sayings):
        nxt = sayings[end_idx + 1]
        if _signoff_shaped(nxt["tokens"]) or (
            "subscribe" in nxt["tokens"]
        ):
            end_idx += 1
        else:
            break
    block = sayings[end_idx]
    kept_before_end = sum(
        s["word_end"] - s["word_start"] for s in sayings[: end_idx + 1]
    )
    trailing = kept_total - kept_before_end
    if kept_before_end < CLOSING_SECTION_FRACTION * kept_total:
        return None
    if trailing > 0.2 * kept_total:
        return None
    return block["word_end"]
