"""The local retake engine: measure, band, propose. No model, no network.

Cut silence works because it measures the timeline locally and proposes
ordinary operations the safety verifier can judge. This module gives
Remove retakes the same shape:

- Tier 0/1 (here): normalize the transcript, segment it into sentence-ish
  units, and score nearby unit pairs on coverage (containment, the
  runner's own S12 measure), anchor match, and sequence ratio. Repeated
  runs inside one unit and fragments left beside already-settled takes
  are candidates too.
- Tier 2 (here, in code, never in a prompt): the policy. The flow
  layer governs take choice: a take that completes its sentence
  cleanly beats a broken or trailing one, and among complete takes
  the last one wins; chains group and keep the last complete
  delivery. Every pair records whether each side completes its
  sentence, so the judgment pass (and the creator, in the dots) can
  see the flow evidence, not just the similarity. What pulls
  attention away from the story (false starts, restarts, dead-end
  fragments) is what gets proposed; a pair touching a protected
  span or a special insert is never a candidate; a pair whose
  earlier take is already cut is settled; a pair whose copies
  differ by a negation (one copy carries a "not", the other does
  not; 0.27, fix 9), and a short complete line said twice in a
  row, surface as flags only, because which copy survives can
  flip the meaning or be emphasis. A "not" on both copies (or
  neither) is a plain retake. And a pair is a candidate at all
  only when the overlap covers most of the shorter side AND a
  real share of the longer side (0.27, fix 8): a complete line
  and a short fragment sharing only a phrase are not a pair,
  and a complete take is never struck in favor of a fragment.
  Story grounds alone never cut anything here: a line that
  merely fails to serve the direction is a flag for review, not
  a proposal.
- Decided pairs (v0.13): two shapes need no one's call. A fragment
  whose cut side stops mid-word (or that the fragment-chain shape
  found) is a broken try, not a choice. And when two or more pairs
  point at the same complete take and every cut is an earlier try
  at the same line with the same opening, the chain is decided:
  the last full take stays and every earlier try comes out. A
  negation the copies share (both carry a "not", or neither
  does) does not stop a chain; a one-sided negation does,
  because there the copies say different things. A decided pair
  is cut in both the strict and the judged modes, carries the
  unsure mark like any unsure cut, and its cut operation names
  the decision, so the verifier counts the local evidence as
  the review. A negation pair whose copies say different things
  is a genuine choice and still flags, and a chain whose last
  take does not finish its line is not decided.
- The bands: Band A (trusted) emits ``cut_words`` proposals in the exact
  shape the silence pass emits, so the existing verifier and applier
  decide what applies. Band B (borderline) is a suspected retake the
  measurement alone could not settle. Strict is the default
  (v0.11.1): a borderline pair is cut locally and marked unsure, so
  no judgment call is made for it. Only an explicit choice for the
  loose path (the profile's retakes knob, or a per-video direction
  that says to only cut the sure ones) returns Band B to its older
  route: a packet for one model judgment call per video, degrading
  to scored flags with no model, never to silence. Band C is not a
  candidate at all.

Everything here is a pure function over a ``Timeline`` plus the creator
profile's knobs (``profile["retakes"]``). Standard library only.
"""

from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Optional

from .timeline import Timeline
from .verifier import NEGATION_WORDS

RETAKE_COMMENT_ID = "front-door-retakes"

DEFAULT_SETTINGS = {
    # The later unit must start within this many seconds of the earlier
    # one ending. Retakes cluster; callbacks live at distance.
    "window_s": 60.0,
    # Band A: coverage of the shorter unit at or above this, plus an
    # anchor match or a sequence ratio at or above ``ratio_a``.
    "coverage_a": 0.90,
    "ratio_a": 0.85,
    # Band B floors: coverage or ratio at or above these is borderline.
    "coverage_b": 0.60,
    "ratio_b": 0.65,
    # A pause longer than this also ends a unit, so a reparandum that
    # starts mid-sentence is still a candidate shape.
    "pause_split_s": 1.5,
    # Which complete delivery a cluster keeps: "last", "first", or
    # "best" (Best delivery (Beta), which runs in shadow: the
    # positional last-take rule still decides what applies, and Beta
    # only records the keeper it would have chosen).
    "keep": "last",
    # Strict by default (v0.11.1, fix item 5): a suspected retake is
    # taken out, keeping the take the keeper rule names, instead of
    # waiting for a judgment call or a flag. A cut the pass was not
    # sure about is marked unsure (see ``_unsure_mark``). An explicit
    # choice says otherwise and wins: the profile's own retakes knob
    # can set this to False, and a per-video direction that says to
    # only cut the sure ones sets it for that video. With no explicit
    # choice anywhere, a fresh state is strict.
    "strict": True,
}

KEEP_MODES = ("last", "first", "best")

MODE_LABELS = {
    "last": "the last take",
    "first": "the first clean take",
    "best": "best delivery (Beta)",
}

# The Beta tie sentence: when the words cannot separate two complete
# deliveries, the honest answer is a tie, and position decides.
TIE_REASON = (
    "The words could not separate these two, so the last take stays."
)

_CLASS_TEXT = {
    "earlier_take": (
        "Earlier take of the next line. The same line is said again "
        "right after, and the later take completes the sentence "
        "cleanly, so it stays."
    ),
    "restarted_sentence": (
        "Restarted sentence. This try stops early, and the line is "
        "said again in full right after, so the story keeps moving."
    ),
    "false_start": (
        "False start. These words were started, then the line was "
        "said again in full. A broken start pulls attention away "
        "from the story."
    ),
}

_FUNCTION_WORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at",
    "it", "i", "you", "he", "she", "we", "they", "that", "this", "is",
    "was", "are", "were", "be", "been", "have", "has", "had", "do",
    "does", "did", "so", "if", "then", "than", "as", "by", "for",
    "with", "from", "my", "your", "his", "her", "our", "their", "me",
    "him", "them", "us",
}

_FIRST_CLEAN_REASON = (
    "Kept the first clean take; the later tries come out."
)
_FIRST_CLEAN_FRAGMENT_REASON = (
    "This try comes after the line was already said in full, and "
    "it stops early. The first clean take stays."
)
_LATER_FRAGMENT_REASON = (
    "This try comes after the line was already said in full, and "
    "it stops early, so the complete take before it stays."
)


def _pair_reason(
    scored: dict,
    shape: str,
    cut_is_later: bool,
    cut_complete: bool,
    settings: dict,
) -> str:
    """The dot's sentence for a pair, naming the decider.

    The flag text always wins. A cut that sits later than the take
    that stays is the one case the class texts do not describe:
    either the creator's first-clean setting kept the earlier
    delivery, or a trailing fragment lost to the complete take
    before it, and the sentence says which.
    """
    if scored.get("flag_reason"):
        return scored["flag_reason"]
    if shape == "unit_pair" and cut_is_later:
        if positional_keep(settings) == "first":
            if cut_complete:
                return _FIRST_CLEAN_REASON
            return _FIRST_CLEAN_FRAGMENT_REASON
        return _LATER_FRAGMENT_REASON
    return _CLASS_TEXT[scored["retake_class"]]


_FLAG_NEGATION_REASON = (
    "This line is said again right after and one copy includes "
    "a 'not', so you decide which copy stays."
)
_FLAG_DOUBLE_REASON = (
    "This short line is said twice in a row and may be on "
    "purpose, so you decide whether one copy goes."
)
_FLAG_PARALLEL_REASON = (
    "The same words repeat inside one sentence, after words "
    "that stay. That is a parallel line said on purpose more "
    "often than a stopped try, so you decide whether one "
    "copy goes."
)
_FLAG_NUMBER_REASON = (
    "This line is said again right after and the copies name "
    "different numbers, so you decide which copy stays."
)

_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"thousand": 1_000, "million": 1_000_000}


def _number_word_value(token: str) -> Optional[int]:
    """One token as a number, or None when it names no number.

    Digits ("29", "2026") and number words ("twenty", "twentyone",
    "five") both count; the copies of a retake say different
    things when the numbers they name differ, however they are
    written.
    """
    if token.isdigit():
        return int(token)
    if token in _ONES:
        return _ONES[token]
    if token in _TENS:
        return _TENS[token]
    for tens, value in _TENS.items():
        if token.startswith(tens):
            rest = token[len(tens):]
            if rest in _ONES and 1 <= _ONES[rest] <= 9:
                return value + _ONES[rest]
    return None


def _number_tokens(tokens: list) -> list:
    """The numbers a run of tokens names, in order.

    A run of number words ("one hundred twenty three") is one
    number, the way a digit string is one number.
    """
    values: list = []
    i = 0
    while i < len(tokens):
        if _number_word_value(tokens[i]) is None and tokens[i] not in _SCALES:
            i += 1
            continue
        total = 0
        current = 0
        seen = False
        while i < len(tokens):
            token = tokens[i]
            value = _number_word_value(token)
            if value is not None:
                current += value
                seen = True
                i += 1
                continue
            if token == "hundred" and current:
                current *= 100
                i += 1
                continue
            if token in _SCALES and (current or total):
                total += (current or 1) * _SCALES[token]
                current = 0
                i += 1
                continue
            if (
                token == "and"
                and seen
                and i + 1 < len(tokens)
                and _number_word_value(tokens[i + 1]) is not None
            ):
                i += 1
                continue
            break
        if seen:
            values.append(total + current)
        else:
            i += 1
    return values


def _numbers_differ(tokens_a: list, tokens_b: list) -> bool:
    """True when both copies name numbers and the numbers differ.

    A retake whose copies disagree on a number is a choice about
    facts, not a wording variant: the detector flags it for the
    creator instead of cutting either copy (0.25, finding 7).
    Both copies must name a number: a number word on one side
    only, above all "one" doing pronoun work ("but one of the
    leaders"), is wording, not a changed fact, and pairs like
    that keep their ordinary bands.
    """
    numbers_a = _number_tokens(tokens_a)
    numbers_b = _number_tokens(tokens_b)
    if not numbers_a or not numbers_b:
        return False
    return numbers_a != numbers_b


def settings_from_profile(profile: Optional[dict]) -> dict:
    """The detector's knobs: the defaults, plus the profile's overrides."""
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(profile, dict):
        knobs = profile.get("retakes")
        if isinstance(knobs, dict):
            for key in (
                "window_s",
                "coverage_a",
                "ratio_a",
                "coverage_b",
                "ratio_b",
                "pause_split_s",
            ):
                value = knobs.get(key)
                if isinstance(value, (int, float)) and not isinstance(
                    value, bool
                ):
                    settings[key] = float(value)
            if knobs.get("keep") in KEEP_MODES:
                settings["keep"] = knobs["keep"]
            if isinstance(knobs.get("strict"), bool):
                settings["strict"] = knobs["strict"]
    return settings


def unsure_note(settings: Optional[dict] = None) -> str:
    """The plain note an unsure cut carries, in the creator's pattern.

    The keeper is named as it actually is: the first clean take
    under the first-clean mode, and the last take in every other
    mode, because Best delivery still applies the last take while
    it runs in shadow.
    """
    keeper = (
        "the first clean take"
        if positional_keep(settings or {}) == "first"
        else "the last take"
    )
    return (
        "I was not sure about this one. I took it out so "
        f"{keeper} stays. Double check here if you want it back."
    )


def positional_keep(settings: dict) -> str:
    """The positional rule a mode applies with.

    "first" applies the first clean take; every other mode, "best"
    included, applies the last take while Best delivery is still
    earning its place in shadow.
    """
    return "first" if settings.get("keep") == "first" else "last"


# ---------------------------------------------------------------------------
# Tier 0: normalization and segmentation
# ---------------------------------------------------------------------------

_STRIP = " \t\n.,!?;:\"'“”‘’()[]"


def normalize_word(raw: str) -> str:
    """One transcript word, folded for comparison only.

    Lowercase, punctuation stripped from the edges. Indices never
    change: token i is always word i, so every score maps back to an
    exact word span.
    """
    return str(raw or "").strip(_STRIP).lower()


def _ends_sentence(raw: str) -> bool:
    """True when a raw word ends a sentence (an ellipsis does not)."""
    text = str(raw or "").rstrip()
    if text.endswith("...") or text.endswith("…"):
        return False
    return text.endswith((".", "!", "?"))


def _opens_quote(raw: str) -> bool:
    """True when a raw word opens a quoted sentence.

    A word carrying an opening quote starts a new sentence even
    when the punctuation before it is only a comma: Scribe writes
    the prayer he starts mid-line as `...up to God, "Hey, God,
    if I ever...`, and the quoted sentence is its own unit, not
    the tail of the sentence that introduced it (0.27.1, P0).
    """
    return str(raw or "").lstrip().startswith(('"', "“"))


def _range_index_by_word(timeline: Timeline) -> list:
    """The kept-range index holding each word's midpoint, or None."""
    out = []
    for i in range(len(timeline.words)):
        mid = timeline._word_midpoint(i)
        found = None
        for index, rng in enumerate(timeline.ranges):
            if float(rng["start"]) - 1e-6 <= mid <= float(rng["end"]) + 1e-6:
                found = index
                break
        out.append(found)
    return out


def segment_units(
    timeline: Timeline,
    profile: Optional[dict] = None,
    settings: Optional[dict] = None,
) -> list:
    """Sentence-ish units over the whole transcript.

    A unit ends at terminal punctuation, at a pause longer than the
    split setting, and at every kept-range boundary, so a unit never
    straddles a protected insert or a seam in the edit.
    """
    settings = settings or settings_from_profile(profile)
    words = timeline.words
    if not words:
        return []
    protected_words: set = set()
    for span in timeline.protected_spans(profile):
        protected_words.update(
            range(int(span["word_start"]), int(span["word_end"]))
        )
    range_of = _range_index_by_word(timeline)
    statuses = [timeline.word_status(i) for i in range(len(words))]

    spans: list = []
    start = 0
    for i in range(len(words) - 1):
        word = words[i]
        nxt = words[i + 1]
        gap = float(nxt["start"]) - float(word["end"])
        if (
            _ends_sentence(word.get("word", ""))
            or gap > settings["pause_split_s"]
            or range_of[i] != range_of[i + 1]
            or _opens_quote(nxt.get("word", ""))
        ):
            spans.append((start, i + 1))
            start = i + 1
    spans.append((start, len(words)))
    # A lone word that ends a sentence, sitting right after a
    # unit that does not, is that unit's own tail, not a sentence
    # of its own: the speaker paused before the last word, and
    # the pause split cut the sentence in two (0.27.1, P0: the
    # book sentence, whose "safely." sat alone after a 2.3 s
    # pause and left the real take reading as a fragment that
    # stops early). Merge it back so completeness is judged on
    # the whole sentence.
    merged: list = []
    for span_start, span_end in spans:
        if (
            merged
            and span_end - span_start == 1
            and statuses[span_start] == "kept"
            and statuses[merged[-1][1] - 1] == "kept"
            and _ends_sentence(words[span_start].get("word", ""))
            and not _ends_sentence(
                words[merged[-1][1] - 1].get("word", "")
            )
        ):
            merged[-1] = (merged[-1][0], span_end)
        else:
            merged.append((span_start, span_end))
    spans = merged

    units = []
    for word_start, word_end in spans:
        tokens = [normalize_word(words[i].get("word", "")) for i in range(word_start, word_end)]
        kept = [
            i
            for i in range(word_start, word_end)
            if statuses[i] == "kept"
        ]
        units.append(
            {
                "word_start": word_start,
                "word_end": word_end,
                "tokens": tokens,
                "kept_indices": kept,
                "kept_tokens": [
                    tokens[i - word_start] for i in kept
                ],
                "kept_start": kept[0] if kept else None,
                "kept_end": kept[-1] + 1 if kept else None,
                "terminal": _ends_sentence(words[word_end - 1].get("word", "")),
                "protected": any(
                    i in protected_words or statuses[i] == "special"
                    for i in range(word_start, word_end)
                ),
                "settled": not kept,
                "start_s": float(words[word_start]["start"]),
                "end_s": float(words[word_end - 1]["end"]),
            }
        )
    return units


# ---------------------------------------------------------------------------
# Tier 1: scoring
# ---------------------------------------------------------------------------


def _coverage(tokens_a: list, tokens_b: list) -> float:
    """Containment: shared tokens over the shorter unit (the S12 shape)."""
    if not tokens_a or not tokens_b:
        return 0.0
    shared = sum((Counter(tokens_a) & Counter(tokens_b)).values())
    return shared / min(len(tokens_a), len(tokens_b))


def _fuzzy_shared(tokens_a: list, pool: list) -> int:
    """How many of ``tokens_a`` are matched inside ``pool``.

    A match is the same token, or one token starting with the
    other (a reworded verb form: "got" for "gotten", "i" for
    "i've"), at least three letters on the shorter side so small
    words cannot match by accident.
    """
    remaining = list(pool)
    shared = 0
    for token in tokens_a:
        hit = None
        for i, other in enumerate(remaining):
            if other == token or (
                min(len(token), len(other)) >= 3
                and (other.startswith(token) or token.startswith(other))
            ):
                hit = i
                break
        if hit is not None:
            remaining.pop(hit)
            shared += 1
    return shared


def _window_containment(tokens_a: list, tokens_b: list) -> tuple:
    """Best (exact, fuzzy) containment of A inside a window of B.

    The window is A's length plus three, slid over every start in
    B: a reworded retake restarts the same line somewhere inside
    the later unit (after a leftover fragment, say), with the
    words reordered, so whole-unit coverage and the opening
    anchor both miss it. Returns (exact share, fuzzy share, the
    offset of the best window in B); the offset says where the
    real take starts inside B, which the leftover-head rule
    needs.
    """
    if not tokens_a or not tokens_b:
        return 0.0, 0.0, 0
    width = min(len(tokens_b), len(tokens_a) + 3)
    best_key = None
    best_exact = best_fuzzy = 0.0
    best_offset = 0
    for offset in range(0, len(tokens_b) - width + 1):
        window = tokens_b[offset:offset + width]
        exact = _coverage(tokens_a, window)
        fuzzy = _fuzzy_shared(tokens_a, window) / len(tokens_a)
        # Ties go to the window that starts with A's own first
        # word (that is where the take itself starts, which the
        # leftover-head rule measures from), then to the earliest.
        key = (
            fuzzy,
            exact,
            1 if window and window[0] == tokens_a[0] else 0,
            -offset,
        )
        if best_key is None or key > best_key:
            best_key = key
            best_exact, best_fuzzy, best_offset = exact, fuzzy, offset
    return best_exact, best_fuzzy, best_offset


def _leftover_head(scored: dict, unit: dict, raw_words: list) -> Optional[dict]:
    """The kept unit's own head is a leftover fragment (0.24.0).

    When a reworded retake is decided and the real take starts
    partway into the kept unit, the words before it can be the
    abandoned start of the same line: "I said today's day blank
    of" said, dropped, then the line said properly. The head
    earns a cut when it is short, does not end a sentence, and
    its words come back inside the real take that follows it.
    Anything weaker stays silent here; the pair itself is
    already flagged or cut on its own evidence.
    """
    if not scored.get("reworded") or scored.get("negation"):
        return None
    offset = scored.get("window_offset") or 0
    if offset < 2 or offset > 8:
        return None
    kept_indices = unit.get("kept_indices") or []
    tokens = unit.get("kept_tokens") or []
    if len(kept_indices) != len(tokens) or offset >= len(tokens):
        return None
    head = tokens[:offset]
    rest = tokens[offset:offset + 20]
    if len(rest) < 4:
        return None
    coverage = _fuzzy_shared(head, rest) / len(head)
    if coverage < 0.6:
        return None
    last_head_word = str(
        raw_words[kept_indices[offset - 1]].get("word") or ""
    )
    if last_head_word.rstrip().endswith((".", "?", "!")):
        return None
    band = "a" if coverage >= 0.85 and scored.get("band") == "a" else "b"
    cut_start = kept_indices[0]
    keep_start = kept_indices[offset]
    return {
        "cut_start": cut_start,
        "cut_end": keep_start,
        "keep_start": keep_start,
        "keep_end": kept_indices[-1] + 1,
        "coverage": round(coverage, 4),
        "ratio": None,
        "anchor": True,
        "adjacent": True,
        "negation": False,
        "band": band,
        "flag_reason": None,
        "retake_class": "false_start",
        "shape": "leftover_head",
    }


def _ratio(tokens_a: list, tokens_b: list) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    return SequenceMatcher(None, tokens_a, tokens_b).ratio()


def _anchor_len(tokens_a: list, tokens_b: list) -> int:
    length = 0
    for a, b in zip(tokens_a, tokens_b):
        if a != b:
            break
        length += 1
    return length


def _fuzzy_anchor_len(tokens_a: list, tokens_b: list) -> int:
    """The shared opening of two token runs, matched fuzzily.

    A token matches when it is the same token, a prefix form of
    the other with at least three letters on the shorter side
    (a reworded verb form: "got" for "gotten"), or a contraction
    pair ("i" for "i've"). A restart rewords its opening as
    often as it repeats it, so the exact anchor misses it.
    """
    length = 0
    for a, b in zip(tokens_a, tokens_b):
        if a == b:
            length += 1
            continue
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        if longer.startswith(shorter) and (
            len(shorter) >= 3 or "'" in longer
        ):
            length += 1
            continue
        break
    return length


def _fuzzy_run_in(attempt: list, completion: list) -> int:
    """The longest run of the attempt's tokens appearing in
    order, contiguously and fuzzily, inside the completion."""
    best = 0
    for offset in range(len(completion)):
        run = _fuzzy_anchor_len(attempt, completion[offset:])
        if run > best:
            best = run
    return best


def _has_negation(tokens: list) -> bool:
    return any(token in NEGATION_WORDS for token in tokens)


def _unit_text(timeline: Timeline, start: int, end: int) -> str:
    return " ".join(
        str(timeline.words[i].get("word") or "") for i in range(start, end)
    )


def _pair_id(cut_start: int, cut_end: int, keep_start: int, keep_end: int) -> str:
    return f"retake-{cut_start}-{cut_end}-{keep_start}-{keep_end}"


def _score_pair(
    earlier: dict, later: dict, units_between: list, settings: dict
) -> Optional[dict]:
    """Score one earlier/later unit pair; None when it is not a candidate."""
    if earlier["protected"] or later["protected"]:
        return None
    if earlier["settled"] or later["settled"]:
        return None
    gap_s = later["start_s"] - earlier["end_s"]
    if gap_s > settings["window_s"]:
        return None
    e_tokens = earlier["kept_tokens"]
    l_tokens = later["kept_tokens"]
    if len(e_tokens) < 2 or not l_tokens:
        # A one-word earlier unit is a seam fragment, not a delivery;
        # mid-sentence one-word restarts are the intra-unit path's job.
        return None
    if len(l_tokens) < 3 and len(l_tokens) < len(e_tokens):
        # The kept telling is never the tiny fragment when a fuller
        # earlier unit is on the table.
        return None
    coverage = _coverage(e_tokens, l_tokens)
    ratio = _ratio(e_tokens, l_tokens)
    anchor_len = _anchor_len(e_tokens, l_tokens)
    anchor = anchor_len >= min(3, len(e_tokens), len(l_tokens))
    if min(len(e_tokens), len(l_tokens)) <= 2 and not anchor:
        # A one or two word unit is contained in almost anything by
        # multiset accident. It only pairs when the later unit starts
        # with those very words.
        return None
    # Adjacent in the edit: no complete kept sentence of any substance
    # sits between the two deliveries. A two-word fragment between
    # them is debris from the same repair, not a sentence; anything
    # wider is a callback until proven otherwise.
    adjacent = not any(
        unit["terminal"]
        and not unit["settled"]
        and not unit["protected"]
        and len(unit["kept_tokens"]) >= 5
        for unit in units_between
    )
    if not adjacent and anchor_len < 2:
        # A callback at distance whose repeat is buried inside the
        # later unit (it does not start with the earlier line) is
        # not a take of that line: the Day 30 pair, where the day
        # line's tail recurs inside the real take's sentence
        # ("...I've said today's day blank of seeing if God...")
        # and pairing them put the video's first line itself on
        # the cut list. A distant re-take starts the later unit;
        # anything else is the creator reusing a phrase.
        return None
    fragment = not earlier["terminal"]
    duplicate = coverage >= 0.95 and ratio >= 0.90
    # A negation makes the pair the creator's call only when
    # exactly one copy carries it AND both copies are complete
    # (0.27, fix 9): when both copies carry a "not" (or neither
    # does), the copies say the same thing and the pair is a
    # plain retake. And a stopped try that never reached the
    # negation asserts no competing meaning at all, so a
    # one-sided "not" against an incomplete side is a plain
    # retake too. ``negation`` on a scored pair therefore means
    # a genuine choice: asymmetric, between two finished lines.
    negation = (
        _has_negation(e_tokens) != _has_negation(l_tokens)
        and earlier["terminal"]
        and later["terminal"]
    )
    numbers_differ = _numbers_differ(e_tokens, l_tokens)
    # A reworded retake (0.24.0, Justin's 0.23 Mac test): the same
    # line said again with the words reordered and a verb form
    # changed ("Every time I've gotten into this tank." /
    # "Every time I got into this tank I've said"). Whole-unit
    # coverage and the anchor both miss it, but the earlier unit
    # sits whole inside a window of the later one: every earlier
    # word is there under fuzzy matching (gotten/got and i've/i
    # count), and at least three in five match exactly. Next door
    # in the edit, that is a duplicate, not a callback.
    win_exact, win_fuzzy, win_offset = _window_containment(
        e_tokens, l_tokens
    )
    reworded = (
        len(e_tokens) >= 5
        and adjacent
        and win_fuzzy >= 1.0
        and win_exact >= 0.60
    )
    if reworded:
        duplicate = True
    # A restart (0.27, fix 9): the earlier try stops
    # mid-sentence and the unit right after it opens with the
    # same words and finishes the line. The opening matches
    # fuzzily (a verb form changes between tries), the try's
    # words sit almost whole inside the completion, and the
    # completion ends its sentence. That is a stopped take,
    # decided locally, with no model needed.
    fuzzy_anchor = _fuzzy_anchor_len(e_tokens, l_tokens)
    restart = (
        adjacent
        and fragment
        and bool(later["terminal"])
        and len(e_tokens) >= 4
        and fuzzy_anchor >= 4
        and win_fuzzy >= 0.9
    )
    if restart:
        duplicate = True
    # A few shared words inside a long line are not a retake
    # (0.27, fix 8): unless the pair's stronger evidence says
    # otherwise (a near-duplicate, a reworded or restarted take
    # restated whole, an anchored fragment, or the earlier unit
    # sitting almost whole inside a window at the later unit's
    # opening, the leftover-head range), the overlap must also
    # cover a real share of the longer side, at least a quarter
    # of it, not only most of the shorter side. And when both
    # sides are complete lines, the share must reach half of
    # the longer side: two finished sentences that share only
    # a phrase (a closing formula, a repeated "worth it") are
    # two lines, not two takes of one line. A match buried at
    # the later unit's tail is a phrase coming back, not a
    # take: the window waiver only counts near its opening.
    shared = sum((Counter(e_tokens) & Counter(l_tokens)).values())
    longer = max(len(e_tokens), len(l_tokens))
    restated_whole = win_fuzzy >= 0.9 and win_offset <= 8
    # A try that stops mid-word (its last kept token is a single
    # letter or a hyphen-cut stub, the "f-" shape) is a decided
    # fragment on its own evidence; the share gate does not
    # apply to it.
    last_token = e_tokens[-1] if e_tokens else ""
    partial_stop = fragment and (
        len(last_token) == 1
        or (last_token.endswith("-") and last_token[:-1].isalpha())
    )
    if (
        not duplicate
        and not (anchor and fragment)
        and not restated_whole
        and not partial_stop
    ):
        if shared < 0.25 * longer:
            return None
        if (
            earlier["terminal"]
            and later["terminal"]
            and shared < 0.5 * longer
        ):
            return None

    if (reworded or restart) and not negation:
        measured = "a"
    elif coverage >= settings["coverage_a"] and (
        anchor or ratio >= settings["ratio_a"]
    ) and (fragment or duplicate):
        measured = "a"
    elif (
        coverage >= settings["coverage_b"]
        or ratio >= settings["ratio_b"]
        or (anchor and fragment)
    ):
        measured = "b"
    else:
        return None

    band = measured
    flag_reason = None
    if negation:
        band = "flag"
        flag_reason = _FLAG_NEGATION_REASON
    elif numbers_differ:
        # The copies name different numbers: which copy stays
        # can change what the video says, so this is the
        # creator's call, never a cut (0.25, finding 7).
        band = "flag"
        flag_reason = _FLAG_NUMBER_REASON
    elif earlier["terminal"] and len(e_tokens) <= 4 and adjacent:
        # A short complete line said twice in a row is emphasis until
        # a creator rule says otherwise.
        band = "flag"
        flag_reason = _FLAG_DOUBLE_REASON
    elif not adjacent:
        # A callback at distance never earns the trusted band.
        band = "b" if measured == "a" else band

    if duplicate:
        retake_class = "earlier_take"
    elif fragment and len(e_tokens) <= 6:
        retake_class = "false_start"
    elif fragment:
        retake_class = "restarted_sentence"
    else:
        retake_class = "earlier_take"
    return {
        "coverage": round(coverage, 4),
        "ratio": round(ratio, 4),
        "anchor": anchor,
        "adjacent": adjacent,
        "negation": negation,
        "numbers_differ": numbers_differ,
        "band": band,
        "flag_reason": flag_reason,
        "retake_class": retake_class,
        "reworded": bool(reworded),
        "window_offset": win_offset if reworded else 0,
    }


def _cluster_units(pairs: list) -> list:
    """Group paired unit indexes into clusters (union-find)."""
    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    clusters: dict = {}
    for a, b in pairs:
        clusters.setdefault(find(a), set()).update((a, b))
    return [sorted(members) for members in clusters.values()]


def _kept_unit_index(
    units: list,
    members: list,
    settings: dict,
    reworded_a_pairs: Optional[set] = None,
) -> int:
    """The delivery a cluster keeps: the last complete one by default.

    Under Best delivery (Beta) the positional rule still decides:
    Beta runs in shadow and applies nothing in this version.

    One exception (0.24.0): a reworded retake scored in the
    trusted band means the later unit restates the earlier unit's
    whole line inside itself, so the line's complete telling is
    the later one even when the later unit runs on into the next
    sentences and never earns the terminal flag (the Day 30 pair:
    the real take continues "This time I switched it..." inside
    the same unit, so the unit reads as mid-sentence while the
    take itself is complete). The earlier unit is exactly that
    line and nothing else, so keeping the later unit loses
    nothing. A keep-first setting still wins.
    """
    candidates = [
        i for i in members if not units[i]["settled"] and not units[i]["protected"]
    ]
    if positional_keep(settings) != "first" and reworded_a_pairs:
        later_takes = [
            later_i
            for earlier_i, later_i in reworded_a_pairs
            if earlier_i in candidates
            and later_i in candidates
            and units[earlier_i]["terminal"]
        ]
        if later_takes:
            return max(later_takes)
    if positional_keep(settings) == "first":
        ordered = candidates
    else:
        ordered = list(reversed(candidates))
    for i in ordered:
        if units[i]["terminal"]:
            return i
    return ordered[0] if ordered else members[-1]


# ---------------------------------------------------------------------------
# Intra-unit restarts and settled-cluster leftovers
# ---------------------------------------------------------------------------


def _repeated_runs(tokens: list) -> list:
    """Restart chains inside one unit: (reparandum_lo, reparandum_hi, repair_lo).

    A restart link joins two positions whose token runs agree: at
    least three tokens, or a shorter head when the piece between the
    two starts is only a word or two and the repair then diverges
    (the first-word restart: "God redirected, God took..."). Chained
    links give one reparandum: everything from the first run's start
    to the last run's start.
    """
    n = len(tokens)

    def common(p1: int, p2: int) -> int:
        length = 0
        while (
            p2 + length < n
            and p1 + length < p2
            and tokens[p1 + length] == tokens[p2 + length]
        ):
            length += 1
        return length

    links: dict = {}
    for p1 in range(n - 1):
        for p2 in range(p1 + 2, n):
            length = common(p1, p2)
            if length >= 3 or (
                length >= 1
                and p2 - p1 <= 3
                and p2 + length < n
                and tokens[p2 + length] != tokens[p1 + length]
                and any(
                    token not in _FUNCTION_WORDS
                    for token in tokens[p1 : p1 + length]
                )
            ):
                # A short link needs a content word in the run: a lone
                # repeated "to" or "that" mid-sentence is grammar, not
                # a restart. A repeated head word ("God redirected,
                # God took...") is a first-word restart.
                links[p1] = p2
                break
    chains = []
    used: set = set()
    for p1 in sorted(links):
        if p1 in used:
            continue
        first_run_len = common(p1, links[p1])
        last = links[p1]
        used.add(p1)
        while last in links and last not in used:
            used.add(last)
            last = links[last]
        if last - p1 >= 2:
            chains.append((p1, last, last, first_run_len))
    return chains


def _intra_unit_candidates(unit: dict, settings: dict) -> list:
    """Candidates whose reparandum and repair live in the same unit."""
    if unit["settled"] or unit["protected"]:
        return []
    kept = unit["kept_indices"]
    if not kept:
        return []
    kept_set = set(kept)
    out = []
    tokens = unit["tokens"]
    for lo, hi, repair_lo, first_run_len in _repeated_runs(tokens):
        span = [
            unit["word_start"] + k
            for k in range(lo, hi)
            if unit["word_start"] + k in kept_set
        ]
        if len(span) < 2:
            continue
        cut_start, cut_end = span[0], span[-1] + 1
        repair = [
            unit["word_start"] + k
            for k in range(repair_lo, len(tokens))
            if unit["word_start"] + k in kept_set
        ]
        if not repair:
            continue
        # Asymmetry only (0.27, fix 9): a "not" on both sides
        # (or neither) is a plain restart; only a one-sided
        # "not" makes the pair the creator's call. The repair
        # side is read over the repair's own saying of the line
        # (the reparandum's length plus the completion's extra
        # words), not the whole tail of the unit: a negation in
        # a later sentence of a run-on unit is not this pair's.
        repair_window = tokens[
            repair_lo : repair_lo + max(hi - lo, 8) + 6
        ]
        negation = _has_negation(tokens[lo:hi]) != _has_negation(
            repair_window
        )
        # A restart is a stopped try only when the repeated words
        # begin the unit: the speaker started the line, stopped,
        # and said it again. A repeat that begins after kept words
        # inside the same unit is the sentence flowing on, a
        # parallel construction ("My wife has been telling me
        # this, my friends have been telling me this"), and the
        # head before it is not a take. Those are never settled
        # as cuts; they wait for the creator's call (0.25.0, the
        # zero push: Justin's Day 30 edit keeps every word of
        # the parallel line).
        parallel = any(i < cut_start for i in kept)
        # A long verbatim run repeated inside the unit, with a real
        # delivery still to come after it, is the cleanest restart
        # shape there is: the abandoned words are a prefix of the
        # repair, word for word. That earns the trusted band. Shorter
        # or messier restarts stay in the judgment band.
        trusted = (
            not negation
            and not parallel
            and first_run_len >= 4
            and hi - lo >= 4
            and len(tokens) - repair_lo >= 4
        )
        if negation:
            band, flag_reason = "flag", _FLAG_NEGATION_REASON
        elif parallel:
            band, flag_reason = "flag", _FLAG_PARALLEL_REASON
        else:
            band, flag_reason = ("a" if trusted else "b"), None
        out.append(
            {
                "cut_start": cut_start,
                "cut_end": cut_end,
                "keep_start": repair[0],
                "keep_end": repair[-1] + 1,
                "coverage": None,
                "ratio": None,
                "anchor": True,
                "adjacent": True,
                "negation": negation,
                "band": band,
                "flag_reason": flag_reason,
                "retake_class": (
                    "false_start" if hi - lo <= 6 else "restarted_sentence"
                ),
                "shape": "internal_restart",
            }
        )
    # A reworded restart at the unit's very start (0.27, fix 9):
    # the exact-run scan above misses a stopped take whose
    # completion rewords the opening ("Every time I've gotten
    # into this tank I said..." said again as "Every time I got
    # into this tank I've said..."). The try begins the unit, so
    # no kept words precede it and it cannot be a parallel
    # construction; the completion restates the try almost whole
    # and then goes on past it. The scan is deterministic, so a
    # fresh install with no model catches the shape too.
    tokens_all = unit["tokens"]
    exact_repairs = {
        candidate["keep_start"] for candidate in out
    }
    for p2 in range(4, len(tokens_all) - 3):
        run = _fuzzy_anchor_len(tokens_all, tokens_all[p2:])
        if run < 4:
            continue
        attempt = tokens_all[:p2]
        completion = tokens_all[p2 : p2 + p2 + 6]
        containment = _fuzzy_shared(attempt, completion) / len(attempt)
        if containment < 0.85:
            continue
        span = [
            unit["word_start"] + k
            for k in range(0, p2)
            if unit["word_start"] + k in kept_set
        ]
        if len(span) < 2:
            continue
        repair = [
            unit["word_start"] + k
            for k in range(p2, len(tokens_all))
            if unit["word_start"] + k in kept_set
        ]
        if not repair or repair[0] in exact_repairs:
            break
        cut_start, cut_end = span[0], span[-1] + 1
        repair_window = tokens_all[p2 : p2 + max(p2, 8) + 6]
        negation = _has_negation(attempt) != _has_negation(
            repair_window
        )
        numbers_differ = _numbers_differ(attempt, repair_window)
        if negation:
            band, flag_reason = "flag", _FLAG_NEGATION_REASON
        elif numbers_differ:
            band, flag_reason = "flag", _FLAG_NUMBER_REASON
        elif run >= 5 and containment >= 0.95:
            band, flag_reason = "a", None
        else:
            band, flag_reason = "b", None
        out.append(
            {
                "cut_start": cut_start,
                "cut_end": cut_end,
                "keep_start": repair[0],
                "keep_end": repair[-1] + 1,
                "coverage": round(containment, 4),
                "ratio": None,
                "anchor": True,
                "adjacent": True,
                "negation": negation,
                "numbers_differ": numbers_differ,
                "band": band,
                "flag_reason": flag_reason,
                "retake_class": (
                    "false_start" if p2 <= 6 else "restarted_sentence"
                ),
                "shape": "internal_restart",
            }
        )
        break
    return out


def _leftover_candidates(units: list, settings: dict) -> list:
    """Kept fragments whose words an adjacent settled take already used.

    The runner settled the takes around this fragment; a fragment
    still in the edit whose words are almost all inside the settled
    take beside it is a leftover of the same repair, surfaced for
    judgment, never trusted on measurement alone.
    """
    def settled_neighbours(index: int) -> list:
        """Settled units near this one, past fragment debris only.

        The scan walks a few units each way and stops at the first
        complete kept unit: a settled take further away than a whole
        kept sentence is a different moment, not this fragment's pair.
        """
        found = []
        for step in (1, 2, 3):
            for j in (index - step, index + step):
                if not 0 <= j < len(units):
                    continue
                other = units[j]
                if other["settled"]:
                    found.append(other)
                elif other["terminal"]:
                    break
        return found

    out = []
    for index, unit in enumerate(units):
        if unit["settled"] or unit["protected"] or unit["terminal"]:
            continue
        if len(unit["kept_tokens"]) < 2 or len(unit["kept_tokens"]) > 12:
            continue
        neighbours = settled_neighbours(index)
        if not neighbours:
            continue
        settled_tokens: Counter = Counter()
        for neighbour in neighbours:
            settled_tokens.update(neighbour["tokens"])
        shared = sum(
            (Counter(unit["kept_tokens"]) & settled_tokens).values()
        )
        if not unit["kept_tokens"] or shared / len(unit["kept_tokens"]) < 0.85:
            continue
        following = next(
            (
                units[j]
                for j in range(index + 1, len(units))
                if not units[j]["settled"]
            ),
            None,
        )
        # Asymmetry only (0.27, fix 9): the fragment and the
        # take that follows it are the two copies; a "not" on
        # both sides (or neither) is a plain leftover.
        negation = _has_negation(unit["kept_tokens"]) != _has_negation(
            following["kept_tokens"] if following is not None else []
        )
        numbers_differ = (
            _numbers_differ(
                unit["kept_tokens"],
                following["kept_tokens"] if following is not None else [],
            )
            if following is not None
            else False
        )
        out.append(
            {
                "cut_start": unit["kept_start"],
                "cut_end": unit["kept_end"],
                "keep_start": (
                    following["kept_start"]
                    if following is not None
                    else unit["kept_start"]
                ),
                "keep_end": (
                    following["kept_end"]
                    if following is not None
                    else unit["kept_end"]
                ),
                "coverage": round(shared / len(unit["kept_tokens"]), 4),
                "ratio": None,
                "anchor": False,
                "adjacent": True,
                "negation": negation,
                "numbers_differ": numbers_differ,
                "band": "flag" if (negation or numbers_differ) else "b",
                "flag_reason": (
                    _FLAG_NEGATION_REASON
                    if negation
                    else _FLAG_NUMBER_REASON
                    if numbers_differ
                    else None
                ),
                "retake_class": "false_start",
                "shape": "settled_leftover",
            }
        )
    return out


def _fragment_chain_candidates(units: list, settings: dict) -> list:
    """A short fragment whose line is then said in full, in pieces.

    The kept telling of a restarted line can itself be split into
    fragment units by pauses and range seams ("God redirected, God /
    took what I thought / ... / and aimed it towards something else
    that I was way more passionate about."). Scored as a unit pair,
    the fragment faces a one-word unit and loses. This family scores
    it against the whole following sentence: the first-word restart,
    where the head word repeats and the repair immediately diverges.
    Judgment band only; two words of evidence is never trusted alone.
    """
    out = []
    n = len(units)
    for head in range(n):
        end = head
        while (
            end + 1 < n
            and not units[end]["terminal"]
            and end - head < 3
        ):
            end += 1
        if end == head or not units[end]["terminal"]:
            continue
        chain = units[head : end + 1]
        # Protection is checked on the cut side (the fragment, just
        # below): nothing here ever cuts the chain's words, and a
        # two-word hold inside the kept telling must not make the
        # false start beside it invisible. The verifier still guards
        # every cut that is actually proposed.
        if any(unit["settled"] for unit in chain):
            continue
        chain_tokens: list = []
        for unit in chain:
            chain_tokens.extend(unit["kept_tokens"])
        if len(chain_tokens) < 3:
            continue
        for back in range(1, 4):
            i = head - back
            if i < 0:
                break
            earlier = units[i]
            if earlier["terminal"]:
                break
            if earlier["settled"] or earlier["protected"]:
                continue
            e_tokens = earlier["kept_tokens"]
            if not 2 <= len(e_tokens) <= 3:
                continue
            anchor_len = _anchor_len(e_tokens, chain_tokens)
            if anchor_len < 1:
                continue
            if anchor_len < 2 and e_tokens[1] in chain_tokens[:6]:
                continue
            negation = _has_negation(e_tokens) != _has_negation(chain_tokens)
            numbers_differ = _numbers_differ(e_tokens, chain_tokens)
            out.append(
                {
                    "cut_start": earlier["kept_start"],
                    "cut_end": earlier["kept_end"],
                    "keep_start": chain[0]["kept_start"],
                    "keep_end": chain[-1]["kept_end"],
                    "coverage": round(
                        _coverage(e_tokens, chain_tokens), 4
                    ),
                    "ratio": None,
                    "anchor": True,
                    "adjacent": True,
                    "negation": negation,
                    "numbers_differ": numbers_differ,
                    "band": "flag" if (negation or numbers_differ) else "b",
                    "flag_reason": (
                        _FLAG_NEGATION_REASON
                        if negation
                        else _FLAG_NUMBER_REASON
                        if numbers_differ
                        else None
                    ),
                    "retake_class": "false_start",
                    "shape": "fragment_chain",
                }
            )
    return out


def _unsure_mark(
    timeline: Timeline,
    cut_start: int,
    cut_end: int,
    keep_start: int,
    keep_end: int,
    band: str,
    cut_complete: bool,
    keep_complete: bool,
) -> tuple:
    """Whether a cut is an unsure one, and why, from signals on hand.

    Two signals, both already measured by this pass:

    - A near-duplicate that might carry something new: the pair only
      reached the borderline band, or the cut side is a complete
      delivery whose words include a content word the take that
      stays does not say. Exact duplicates carry nothing new, and a
      broken try that stops early is clearly superseded, so neither
      is unsure on this signal alone.
    - A close keeper choice: both deliveries complete the sentence,
      they are not the same words, and the local keeper ranking
      cannot separate them (a tie) or would have kept the other
      one. Word-identical deliveries are a tie by definition too,
      but they are exact duplicates, which the first rule already
      settles as sure.

    Returns (unsure, kind) where kind is "near_duplicate",
    "close_keeper", or None.
    """
    cut_tokens = [
        normalize_word(timeline.words[i].get("word", ""))
        for i in range(cut_start, cut_end)
    ]
    keep_tokens = [
        normalize_word(timeline.words[i].get("word", ""))
        for i in range(keep_start, keep_end)
    ]
    identical = cut_tokens == keep_tokens
    if band == "b":
        return True, "near_duplicate"
    if identical or not (cut_complete and keep_complete):
        return False, None
    extra = Counter(cut_tokens) - Counter(keep_tokens)
    carries_new = any(
        token and token not in _FUNCTION_WORDS for token in extra
    )
    pair = {
        "cut": {"word_start": cut_start, "word_end": cut_end},
        "keep": {"word_start": keep_start, "word_end": keep_end},
        "cut_complete": cut_complete,
        "keep_complete": keep_complete,
    }
    close = shadow_rank(pair, timeline)["pick"] in ("tie", "other")
    if carries_new:
        return True, "near_duplicate"
    if close:
        return True, "close_keeper"
    return False, None


def _ends_in_partial_word(text: str) -> bool:
    """True when a cut side's last word is cut off partway.

    A hyphen-cut token ("f-", "b-") or a lone single letter: the
    speaker stopped in the middle of a word, so the try is a
    fragment by its own shape, whatever the words around it say.
    """
    tokens = str(text or "").split()
    if not tokens:
        return False
    last = tokens[-1].strip(_STRIP).lower()
    if re.fullmatch(r"[a-z]+-", last):
        return True
    return len(last) == 1 and last.isalpha()


def _decided_kinds(records: list) -> dict:
    """The pairs the app decides itself: pair id -> "fragment"/"chain".

    Fragment: the cut side did not finish its sentence and ends in
    a partial word, or the pair came from the fragment-chain shape.
    Chain: two or more pairs share the same keeper span, every cut
    sits before that keeper, did not finish its sentence, and
    shares the keeper's opening (the anchor the scoring already
    measured), and the keeper finishes its sentence. A group whose
    keeper is unfinished, or whose cuts are complete deliveries,
    is not a chain: those copies are a choice, not restarts.
    """
    by_keeper: dict = {}
    for record in records:
        key = (
            record["keep"]["word_start"],
            record["keep"]["word_end"],
        )
        by_keeper.setdefault(key, []).append(record)
    chain_ids: set = set()
    for (keep_start, _keep_end), group in by_keeper.items():
        earlier = [
            record
            for record in group
            if record["cut"]["word_start"] < keep_start
        ]
        if len(earlier) < 2:
            continue
        if all(
            record["keep_complete"]
            and not record["cut_complete"]
            and record["anchor"]
            and not record["negation"]
            for record in earlier
        ):
            chain_ids.update(record["pair_id"] for record in earlier)
    kinds: dict = {}
    for record in records:
        if (
            not record["cut_complete"]
            and not record["negation"]
            and (
                _ends_in_partial_word(record["cut"]["text"])
                or record["shape"] == "fragment_chain"
            )
        ):
            kinds[record["pair_id"]] = "fragment"
        elif record["pair_id"] in chain_ids:
            kinds[record["pair_id"]] = "chain"
    return kinds


#: Words that carry no echo for the whole-saying test: a head
#: sharing only these with the keeper is a different thought
#: ("but" starting the keeper's sentence proves nothing).
_ECHO_STOPWORDS = frozenset(
    "the and but that this with from have will would when what "
    "your you for are was were they them then than into just "
    "like been doing".split()
)


def _echoes_keeper(head_tokens: list, keeper_tokens: list) -> bool:
    """Does the head belong to the keeper's saying? The head of
    a retaken saying echoes the completed saying: a shared word
    pair, or at least two shared content words. A finished line
    that merely stands before the try ("This was worth it"
    before a new sentence's false start) shares neither."""
    if len(head_tokens) < 2 or len(keeper_tokens) < 2:
        return False
    head_pairs = set(zip(head_tokens, head_tokens[1:]))
    keeper_pairs = set(zip(keeper_tokens, keeper_tokens[1:]))
    if head_pairs & keeper_pairs:
        return True
    head_content = {
        token
        for token in head_tokens
        if len(token) >= 3 and token not in _ECHO_STOPWORDS
    }
    keeper_content = {
        token
        for token in keeper_tokens
        if len(token) >= 3 and token not in _ECHO_STOPWORDS
    }
    return len(head_content & keeper_content) >= 2


def _extend_whole_sayings(
    timeline: Timeline,
    resolved: list,
    units: list,
    profile: Optional[dict] = None,
) -> None:
    """Widen each earlier-take cut to the whole saying (0.27).

    Justin's rule, the one the gold candidates already state for
    their spans: retakes settle whole, so the head of a cut take
    never plays. A pair's measured cut can cover only the
    stopped try, leaving the saying's opening words standing in
    front of the keeper. The saying is found two ways. Inside
    the cut's own transcript unit, the cut extends back to the
    start of its sentence, read the app's canonical way (the
    transcript's punctuation plus its segment boundaries, the
    ends the sound truth pass reads). Across units, the cut
    extends back over the unbroken chain of unfinished units in
    front of it (the speaker never landed a sentence, he was
    building the same thought), but only as far as the chain
    echoes the keeper's saying; a finished line standing before
    the try does not echo it and stays. Every walked word must
    be kept by the plan this pass read, unprotected, no special
    insert, and unclaimed by another pair (its cut or its
    keeper); a guard refusal stops the walk and the pair records
    why in ``extension_stopped``. The walk never moves forward
    past the pair's own span, never touches the keeper, and
    never extends a pair cutting a later take. A pair that only
    flags (no cut of its own, not decided by the app) keeps its
    measured span: its span is a question for the creator, not
    a cut, and the gold card already offers the whole take as
    measured.
    """
    from . import smooth

    if not resolved:
        return
    words = timeline.words
    ends = smooth.sentence_end_indices(
        words, timeline.transcript.get("segments")
    )
    protected: set = set()
    for span in timeline.protected_spans(profile):
        protected.update(
            range(int(span["word_start"]), int(span["word_end"]))
        )
    unit_of = {}
    for index, unit in enumerate(units):
        for wi in range(unit["word_start"], unit["word_end"]):
            unit_of[wi] = index

    def guard(wi: int, others: list) -> Optional[str]:
        if wi in protected:
            return "protected"
        status = timeline.word_status(wi)
        if status == "special":
            return "special"
        if status != "kept":
            return "not_kept"
        if any(a <= wi < b for a, b in others):
            return "claimed"
        return None

    for record in resolved:
        if record["band"] not in ("a", "b") and not record.get("decided"):
            continue
        cut = record["cut"]
        keep = record["keep"]
        start, end = cut["word_start"], cut["word_end"]
        if start >= keep["word_start"] or start not in unit_of:
            continue
        others = [
            (other["cut"]["word_start"], other["cut"]["word_end"])
            for other in resolved
            if other is not record
        ] + [
            (other["keep"]["word_start"], other["keep"]["word_end"])
            for other in resolved
            if other is not record
        ]
        s = start
        stopped = None
        # Inside the cut's own unit: back to the sentence start.
        unit = units[unit_of[start]]
        while s > unit["word_start"] and (s - 1) not in ends:
            refusal = guard(s - 1, others)
            if refusal:
                stopped = refusal
                break
            s -= 1
        # Across units: the unbroken chain of unfinished units
        # in front, as far as the chain echoes the keeper.
        if stopped is None and s == unit["word_start"]:
            keeper_tokens = [
                normalize_word(words[i].get("word", ""))
                for i in range(keep["word_start"], keep["word_end"])
            ]
            head_tokens: list = []
            best = s
            index = unit_of[start]
            steps = 0
            while index > 0 and steps < 6:
                prev_unit = units[index - 1]
                if prev_unit["word_end"] != s or prev_unit["terminal"]:
                    break
                chain = list(range(prev_unit["word_start"], s))
                if len(head_tokens) + len(chain) > 40:
                    break
                refusal = None
                for wi in chain:
                    refusal = guard(wi, others)
                    if refusal:
                        break
                if refusal:
                    stopped = refusal
                    break
                head_tokens = prev_unit["tokens"] + head_tokens
                s = prev_unit["word_start"]
                index -= 1
                steps += 1
                if _echoes_keeper(head_tokens, keeper_tokens):
                    best = s
                elif steps == 1 and record.get("decided"):
                    # The unit right in front of a decided
                    # fragment, unfinished and abutting it with
                    # no sentence end between, is the fragment's
                    # own sentence: the speaker started the line
                    # there and stopped inside the cut span (the
                    # stopped prayer after "offer up to God",
                    # whose head shares almost no words with the
                    # completion that decided its tail, 0.27.1
                    # P0). A decided fragment's own head goes
                    # with it; the walk goes no further without
                    # the echo.
                    best = s
                    break
            s = best
        if s < start:
            cut["whole_from"] = start
            cut["word_start"] = s
            cut["text"] = _unit_text(timeline, s, end)
            record["pair_id"] = _pair_id(
                s, end, keep["word_start"], keep["word_end"]
            )
            record["pair_key"] = (
                f"{s}-{end}|{keep['word_start']}-{keep['word_end']}"
            )
        if stopped and "extension_stopped" not in cut:
            cut["extension_stopped"] = stopped


def _extend_stopped_take_tails(
    timeline: Timeline,
    resolved: list,
    units: list,
    profile: Optional[dict] = None,
) -> None:
    """Grow a stopped take's cut over its own tail fragment (0.27.2).

    The backward walk above widens a cut to the whole saying in
    front of it. The mirror shape escaped it: a take stops
    mid-thought, dead space follows, and then a short fragment
    lands that finishes the take's last broken phrase before the
    speaker gives up and says the passage again in full. The
    segmentation splits the fragment off at the dead space, and
    its full stop makes it read as a sentence of its own, so no
    pair claims it and it plays as an orphan between two cuts
    (Day 30: "My life." standing between the stopped prayer try
    and the next stopped try, in front of the complete saying).

    The configuration is exact before the fragment joins the
    cut: the cut before it is an earlier take that never landed
    its sentence; at least a second of dead space separates the
    cut's last word from the fragment; the fragment is short,
    fully kept, unprotected, and claimed by no pair as a cut or
    a keeper; and the unit right after it is another stopped
    take's cut whose keeper, the complete saying, follows. The
    extension is one unit forward, never a chain.
    """
    if not resolved:
        return
    words = timeline.words
    statuses = [timeline.word_status(i) for i in range(len(words))]
    protected: set = set()
    for span in timeline.protected_spans(profile):
        protected.update(
            range(int(span["word_start"]), int(span["word_end"]))
        )
    unit_by_start = {unit["word_start"]: unit for unit in units}
    claimed = [
        (record["cut"]["word_start"], record["cut"]["word_end"])
        for record in resolved
    ] + [
        (record["keep"]["word_start"], record["keep"]["word_end"])
        for record in resolved
    ]

    def inside_claimed(start: int, end: int) -> bool:
        return any(start < b and a < end for a, b in claimed)

    for record in resolved:
        if record["band"] not in ("a", "b") and not record.get("decided"):
            continue
        cut = record["cut"]
        keep = record["keep"]
        if cut["word_start"] >= keep["word_start"]:
            continue
        if record.get("cut_complete"):
            continue
        end = cut["word_end"]
        frag = unit_by_start.get(end)
        if frag is None or frag["word_end"] - frag["word_start"] > 5:
            continue
        f_start, f_end = frag["word_start"], frag["word_end"]
        if frag.get("protected") or frag.get("settled"):
            continue
        if any(statuses[i] != "kept" for i in range(f_start, f_end)):
            continue
        if any(i in protected for i in range(f_start, f_end)):
            continue
        if inside_claimed(f_start, f_end):
            continue
        gap = float(words[f_start]["start"]) - float(words[end - 1]["end"])
        if gap < 1.0:
            continue
        nxt = unit_by_start.get(f_end)
        if nxt is None:
            continue
        follower = None
        for other in resolved:
            if other is record:
                continue
            other_cut = other["cut"]
            if (
                other_cut["word_start"] == nxt["word_start"]
                and other_cut["word_end"] >= nxt["word_end"]
            ):
                follower = other
                break
        if follower is None or follower.get("cut_complete"):
            continue
        if follower["keep"]["word_start"] < f_end:
            continue
        cut["tail_from"] = end
        cut["word_end"] = f_end
        cut["text"] = _unit_text(timeline, cut["word_start"], f_end)
        record["pair_id"] = _pair_id(
            cut["word_start"], f_end, keep["word_start"], keep["word_end"]
        )
        record["pair_key"] = (
            f"{cut['word_start']}-{f_end}"
            f"|{keep['word_start']}-{keep['word_end']}"
        )
        claimed.append((f_start, f_end))


# ---------------------------------------------------------------------------
# The pass: pairs, bands, operations
# ---------------------------------------------------------------------------


def find_pairs(
    timeline: Timeline,
    profile: Optional[dict] = None,
    settings: Optional[dict] = None,
) -> list:
    """Every surfaced retake pair, best band first, overlaps resolved.

    Each record names the span proposed for cutting, the take that
    stays, the deterministic scores, the band, and the pattern key
    graded records will carry. A record the app decided itself also
    carries ``decided`` ("fragment" or "chain"; None otherwise).
    """
    settings = settings or settings_from_profile(profile)
    units = segment_units(timeline, profile, settings)

    raw_pairs = []
    for i, earlier in enumerate(units):
        for j in range(i + 1, min(i + 5, len(units))):
            scored = _score_pair(earlier, units[j], units[i + 1 : j], settings)
            if scored is not None:
                raw_pairs.append((i, j, scored))

    records: list = []
    seen_units: set = set()
    clusters = _cluster_units([(i, j) for i, j, _ in raw_pairs])
    reworded_a_pairs = {
        (i, j)
        for i, j, scored in raw_pairs
        if scored.get("reworded") and scored.get("band") == "a"
    }
    for members in clusters:
        keep_index = _kept_unit_index(
            units, members, settings, reworded_a_pairs
        )
        keep_unit = units[keep_index]
        for i in members:
            if i == keep_index:
                continue
            unit = units[i]
            first, second = (
                (unit, keep_unit)
                if unit["word_start"] <= keep_unit["word_start"]
                else (keep_unit, unit)
            )
            scored = _score_pair(
                first,
                second,
                units[
                    min(i, keep_index) + 1 : max(i, keep_index)
                ],
                settings,
            )
            if scored is None:
                continue
            # The record carries (member, keeper), not time order:
            # the cluster has already decided which delivery stays
            # under the flow policy (the last complete one, or the
            # first when the creator's setting says so), and the
            # keeper is not always the later unit.
            records.append((unit, keep_unit, scored, "unit_pair"))
            seen_units.add(i)
            # When the keeper is the later delivery and the pair
            # was a reworded retake, the keeper's own head can be
            # the abandoned start of the same line (the fragment
            # before the real take). It gets its own record.
            if keep_unit is second:
                head = _leftover_head(scored, second, timeline.words)
                if head is not None:
                    records.append(
                        (second, second, head, "leftover_head")
                    )
            # A stopped take the segmentation split in two
            # (0.27, fix 9): the unit right after this cut unit
            # can be the take's own tail, a fragment that stops
            # mid-sentence and whose words the keeper restates
            # as one run on its way to finishing the line. The
            # share gate refuses it as a pair on its own (its
            # overlap is a small share of the long keeper), but
            # sitting between an established cut and its keeper
            # it is plainly the same stopped take, so it gets a
            # band b record and the whole take goes. A one-sided
            # negation between the fragment and the keeper
            # leaves it alone: there the copies may not say the
            # same thing.
            if keep_unit is second and keep_index == i + 2:
                frag = units[i + 1]
                frag_tokens = frag["kept_tokens"]
                if (
                    (i + 1) not in seen_units
                    and not any(r[0] is frag for r in records)
                    and not frag["terminal"]
                    and not frag["protected"]
                    and not frag["settled"]
                    and frag["kept_start"] is not None
                    and len(frag_tokens) >= 3
                    and _has_negation(frag_tokens)
                    == _has_negation(keep_unit["kept_tokens"])
                ):
                    run = _fuzzy_run_in(
                        frag_tokens, keep_unit["kept_tokens"]
                    )
                    if run >= 3 and run >= 0.85 * len(frag_tokens):
                        records.append(
                            (
                                frag,
                                keep_unit,
                                {
                                    "coverage": round(
                                        run / len(frag_tokens), 4
                                    ),
                                    "ratio": round(
                                        run
                                        / max(
                                            1,
                                            len(keep_unit["kept_tokens"]),
                                        ),
                                        4,
                                    ),
                                    "anchor": False,
                                    "adjacent": True,
                                    "negation": False,
                                    "numbers_differ": False,
                                    "band": "b",
                                    "flag_reason": None,
                                    "retake_class": (
                                        "false_start"
                                        if len(frag_tokens) <= 6
                                        else "restarted_sentence"
                                    ),
                                    "reworded": False,
                                    "window_offset": 0,
                                },
                                "unit_pair",
                            )
                        )
                        seen_units.add(i + 1)

    # Units the cluster pass did not claim still get the intra-unit
    # and settled-leftover shapes checked.
    leftover_by_start = {
        candidate["cut_start"]: candidate
        for candidate in _leftover_candidates(units, settings)
    }
    for index, unit in enumerate(units):
        if index in seen_units:
            continue
        for candidate in _intra_unit_candidates(unit, settings):
            records.append((unit, unit, candidate, candidate["shape"]))
        leftover = leftover_by_start.get(unit["kept_start"])
        if leftover is not None:
            records.append((unit, unit, leftover, leftover["shape"]))
    claimed_cuts = {
        record[2].get("cut_start")
        for record in records
        if isinstance(record[2], dict)
    }
    for candidate in _fragment_chain_candidates(units, settings):
        if candidate["cut_start"] in claimed_cuts:
            continue
        records.append((None, None, candidate, candidate["shape"]))

    band_rank = {"a": 0, "b": 1, "flag": 2}
    built: list = []
    for earlier_unit, later_unit, scored, shape in records:
        if shape in (
            "internal_restart",
            "settled_leftover",
            "fragment_chain",
            "leftover_head",
        ):
            cut_start, cut_end = scored["cut_start"], scored["cut_end"]
            keep_start, keep_end = scored["keep_start"], scored["keep_end"]
            # The flow layer's completeness, stated plainly: every
            # one of these shapes is a delivery that did not finish
            # its sentence, against one that did.
            cut_complete = False
            keep_complete = True
        else:
            # Unit pairs arrive as (member, keeper): the keeper is
            # the delivery the policy chose, whichever side of the
            # member it sits on.
            cut_start, cut_end = (
                earlier_unit["kept_start"],
                earlier_unit["kept_end"],
            )
            keep_start, keep_end = later_unit["kept_start"], later_unit["kept_end"]
            cut_complete = bool(earlier_unit["terminal"])
            keep_complete = bool(later_unit["terminal"])
        if cut_start is None or keep_start is None or cut_start == keep_start:
            continue
        band = scored["band"]
        unsure, unsure_kind = _unsure_mark(
            timeline,
            cut_start,
            cut_end,
            keep_start,
            keep_end,
            band,
            cut_complete,
            keep_complete,
        )
        built.append(
            {
                "pair_id": _pair_id(cut_start, cut_end, keep_start, keep_end),
                "unsure": unsure,
                "unsure_kind": unsure_kind,
                "pair_key": f"{cut_start}-{cut_end}|{keep_start}-{keep_end}",
                "cut": {
                    "word_start": cut_start,
                    "word_end": cut_end,
                    "text": _unit_text(timeline, cut_start, cut_end),
                },
                "keep": {
                    "word_start": keep_start,
                    "word_end": keep_end,
                    "text": _unit_text(timeline, keep_start, keep_end),
                },
                "retake_class": scored["retake_class"],
                "band": band,
                "cut_complete": cut_complete,
                "keep_complete": keep_complete,
                "coverage": scored.get("coverage"),
                "ratio": scored.get("ratio"),
                "anchor": bool(scored.get("anchor")),
                "negation": bool(scored.get("negation")),
                "shape": shape,
                "pattern_key": (
                    f"retake:{scored['retake_class']}:"
                    + ("flag" if band == "flag" else f"band_{band}")
                ),
                "reason": _pair_reason(
                    scored,
                    shape,
                    cut_is_later=cut_start > keep_start,
                    cut_complete=cut_complete,
                    settings=settings,
                ),
            }
        )

    # Adjacent settled-leftover fragments are one leftover the
    # segmentation split at a range seam; merge them back into one
    # record so the review list shows the piece whole.
    built.sort(key=lambda r: r["cut"]["word_start"])
    merged: list = []
    for record in built:
        if (
            merged
            and record["shape"] == "settled_leftover"
            and merged[-1]["shape"] == "settled_leftover"
            and merged[-1]["band"] == record["band"]
            and merged[-1]["cut"]["word_end"] == record["cut"]["word_start"]
            and merged[-1]["keep"]["word_start"] == record["keep"]["word_start"]
        ):
            merged[-1]["cut"]["word_end"] = record["cut"]["word_end"]
            merged[-1]["cut"]["text"] = (
                merged[-1]["cut"]["text"] + " " + record["cut"]["text"]
            )
            merged[-1]["pair_id"] = _pair_id(
                merged[-1]["cut"]["word_start"],
                merged[-1]["cut"]["word_end"],
                merged[-1]["keep"]["word_start"],
                merged[-1]["keep"]["word_end"],
            )
            merged[-1]["pair_key"] = (
                f"{merged[-1]['cut']['word_start']}-"
                f"{merged[-1]['cut']['word_end']}|"
                f"{merged[-1]['keep']['word_start']}-"
                f"{merged[-1]['keep']['word_end']}"
            )
        else:
            merged.append(record)

    merged.sort(
        key=lambda r: (band_rank[r["band"]], r["cut"]["word_start"])
    )
    resolved: list = []
    claimed: list = []
    for record in merged:
        span = (record["cut"]["word_start"], record["cut"]["word_end"])
        if any(span[0] < end and start < span[1] for start, end in claimed):
            continue
        claimed.append(span)
        resolved.append(record)
    # Decided pairs are named on the record, and a decided cut is
    # an unsure cut: the app took it out on local evidence alone,
    # so it carries the mark that lets the creator bring it back.
    kinds = _decided_kinds(resolved)
    for record in resolved:
        record["decided"] = kinds.get(record["pair_id"])
        if record["decided"]:
            record["unsure"] = True
    # Retakes settle whole (0.27): a cutting pair's cut covers
    # the earlier take's whole saying, from the sentence's first
    # word, so the head of a cut take never plays in front of
    # the keeper. Runs last, once decided pairs are named, so
    # the extension knows which pairs actually cut.
    _extend_whole_sayings(timeline, resolved, units, profile)
    # And the mirror (0.27.2): a stopped take's own tail fragment,
    # split off by the dead space after the stop, goes with the
    # take instead of playing as an orphan in front of the keeper.
    _extend_stopped_take_tails(timeline, resolved, units, profile)
    return resolved


def band_a_operations(
    timeline: Timeline,
    profile: Optional[dict] = None,
    settings: Optional[dict] = None,
    pairs: Optional[list] = None,
) -> list:
    """Band A proposals as operations, in the silence pass's shape.

    Provenance rides in the comment id (the pair id) and the reason;
    the model field is "mock", the same deterministic-local marker
    the silence pass uses, because the schema's model list is the
    contract the verifier checks.
    """
    pairs = pairs if pairs is not None else find_pairs(timeline, profile, settings)
    operations = []
    for pair in pairs:
        if pair["band"] != "a":
            continue
        coverage = pair["coverage"] or 0.9
        operation = {
            "type": "cut_words",
            "comment_id": pair["pair_id"],
            "word_start": pair["cut"]["word_start"],
            "word_end": pair["cut"]["word_end"],
            "reason": pair["reason"],
            "confidence": round(min(0.99, max(0.90, coverage)), 2),
            "model": "mock",
            "risk_tier": 1,
            "wonder_reviewed": False,
        }
        if pair.get("decided"):
            operation["decided"] = pair["decided"]
            # A decided cut is an unsure cut in every band: its
            # operation carries the unsure note, the way the
            # decided and strict builders already do, so the
            # review shows the same bring-back note.
            operation["reason"] = unsure_note(settings)
        operations.append(operation)
    return operations


def strict_cut_operations(
    timeline: Timeline,
    profile: Optional[dict] = None,
    settings: Optional[dict] = None,
    pairs: Optional[list] = None,
) -> list:
    """Strict cuts for borderline pairs, in the same operation shape.

    Under the strict default a borderline pair is cut locally, the
    way a trusted pair is, instead of waiting for a judgment call.
    The reason each operation carries is the unsure note, because
    every borderline pair is unsure by the mark above: its words
    only nearly match, so the cut words might carry something new.
    """
    settings = settings or settings_from_profile(profile)
    pairs = pairs if pairs is not None else find_pairs(timeline, profile, settings)
    operations = []
    for pair in pairs:
        if pair["band"] != "b":
            continue
        coverage = pair["coverage"] or 0.6
        operation = {
            "type": "cut_words",
            "comment_id": pair["pair_id"],
            "word_start": pair["cut"]["word_start"],
            "word_end": pair["cut"]["word_end"],
            "reason": unsure_note(settings),
            "confidence": round(min(0.89, max(0.60, coverage)), 2),
            "model": "mock",
            "risk_tier": 1,
            "wonder_reviewed": False,
        }
        if pair.get("decided"):
            operation["decided"] = pair["decided"]
        operations.append(operation)
    return operations


def decided_cut_operations(
    timeline: Timeline,
    profile: Optional[dict] = None,
    settings: Optional[dict] = None,
    pairs: Optional[list] = None,
) -> list:
    """Cuts for decided pairs no other builder claims.

    A decided pair is cut in both modes: a decided band-flag pair
    never becomes a flag, and a decided borderline pair on the
    judged path never waits for a judgment call. The reason is the
    unsure note, because every decided cut is an unsure cut. The
    operation names the decision in its ``decided`` field, so the
    verifier can count the local evidence as the review.
    """
    settings = settings or settings_from_profile(profile)
    pairs = pairs if pairs is not None else find_pairs(timeline, profile, settings)
    operations = []
    for pair in pairs:
        if not pair.get("decided"):
            continue
        coverage = pair["coverage"] or 0.6
        operations.append(
            {
                "type": "cut_words",
                "comment_id": pair["pair_id"],
                "word_start": pair["cut"]["word_start"],
                "word_end": pair["cut"]["word_end"],
                "reason": unsure_note(settings),
                "confidence": round(min(0.89, max(0.60, coverage)), 2),
                "model": "mock",
                "risk_tier": 1,
                "wonder_reviewed": False,
                "decided": pair["decided"],
            }
        )
    return operations


def decided_pairs(pairs: list) -> list:
    """Pairs the app decided itself, in either decided shape."""
    return [pair for pair in pairs if pair.get("decided")]


def flag_operation(pair: dict, reason: Optional[str] = None) -> dict:
    """A flag_for_review operation pinning a pair's cut span."""
    return {
        "type": "flag_for_review",
        "comment_id": pair["pair_id"],
        "word_start": pair["cut"]["word_start"],
        "word_end": pair["cut"]["word_end"],
        "reason": reason or pair["reason"],
        "confidence": 0.5,
        "model": "mock",
        "risk_tier": 3,
        "wonder_reviewed": False,
    }


def borderline_pairs(pairs: list) -> list:
    """The Band B packet: pairs only a judgment call can settle."""
    return [pair for pair in pairs if pair["band"] == "b"]


def flag_pairs(pairs: list) -> list:
    """Pairs the policy layer itself holds for a person."""
    return [pair for pair in pairs if pair["band"] == "flag"]


def pair_key(pair: dict) -> str:
    """The stable identity of a pair, for keep-both decisions."""
    cut = pair["cut"]
    keep = pair["keep"]
    return (
        f"{cut['word_start']}-{cut['word_end']}|"
        f"{keep['word_start']}-{keep['word_end']}"
    )


def band_b_flag_reason(pair: dict) -> str:
    """The scored flag text for a borderline pair with no model to ask."""
    coverage = pair.get("coverage")
    if coverage is None:
        return (
            "These words were started and then said again, so you "
            "decide whether they were a first try or said twice "
            "on purpose."
        )
    return (
        "These words are said again in the line after, so you "
        "decide whether this first copy should be cut."
    )


# ---------------------------------------------------------------------------
# Best delivery (Beta), in shadow
#
# Beta is a keeper-choice layer, not a detector: detection, bands,
# protections, and the verifier do not change, and in this version
# Beta applies nothing. On every pair where there is a genuine
# choice (both deliveries complete), it records the keeper it would
# have chosen, so its disagreements with the positional pick can be
# graded against the creator's own keeps. The local ranking below is
# the deterministic half of that judgment, and the offline answer
# when no model judge is available: evidence only, in plain words.
#
# The hard gate, with no exceptions: two word-identical deliveries
# are a tie by definition. A version that "picks" between identical
# twins from text evidence is inventing evidence, so identity is
# checked before any ranking or any model call.
# ---------------------------------------------------------------------------


def genuine_choice(pair: dict) -> bool:
    """A real keeper choice exists only when both sides complete."""
    return bool(pair.get("cut_complete")) and bool(pair.get("keep_complete"))


def _span_tokens(timeline: Timeline, span: dict) -> list:
    return [
        normalize_word(timeline.words[i].get("word", ""))
        for i in range(int(span["word_start"]), int(span["word_end"]))
    ]


def deliveries_identical(pair: dict, timeline: Timeline) -> bool:
    """True when the two deliveries are the same words, word for word."""
    return _span_tokens(timeline, pair["cut"]) == _span_tokens(
        timeline, pair["keep"]
    )


def _delivery_evidence(tokens: list, words: list) -> dict:
    """What the words alone can say about one delivery.

    A restart inside the delivery counts against it (the speaker
    started over mid-line); a long dead gap inside it counts
    against it (pause structure is the one delivery signal the
    timing in the transcript can honestly carry); and the share of
    the pair's combined words the delivery itself says.
    """
    restart = bool(_repeated_runs(tokens)) if len(tokens) >= 4 else False
    max_gap = 0.0
    for first, second in zip(words, words[1:]):
        gap = float(second["start"]) - float(first["end"])
        if gap > max_gap:
            max_gap = gap
    return {"restart": restart, "max_gap_s": round(max_gap, 3)}


def shadow_rank(pair: dict, timeline: Timeline) -> dict:
    """Beta's local pick for one genuine-choice pair.

    Returns {"pick": "positional" | "other" | "tie", "reason": str}.
    "positional" names the delivery the active positional rule
    kept (the pair's keep side); "other" names the delivery it
    cut. Timing counts only as a tiebreaker, after a restart and
    after how much of the line each delivery actually says.
    """
    if deliveries_identical(pair, timeline):
        return {"pick": "tie", "reason": TIE_REASON}
    cut_tokens = _span_tokens(timeline, pair["cut"])
    keep_tokens = _span_tokens(timeline, pair["keep"])
    cut_ev = _delivery_evidence(
        cut_tokens,
        timeline.words[pair["cut"]["word_start"] : pair["cut"]["word_end"]],
    )
    keep_ev = _delivery_evidence(
        keep_tokens,
        timeline.words[
            pair["keep"]["word_start"] : pair["keep"]["word_end"]
        ],
    )
    union = Counter(cut_tokens) | Counter(keep_tokens)
    total = sum(union.values()) or 1
    cut_share = sum((Counter(cut_tokens) & union).values()) / total
    keep_share = sum((Counter(keep_tokens) & union).values()) / total

    def score(ev: dict, share: float) -> tuple:
        return (
            0 if ev["restart"] else 1,
            round(share, 3),
            -round(ev["max_gap_s"], 2),
        )

    cut_score = score(cut_ev, cut_share)
    keep_score = score(keep_ev, keep_share)
    if cut_score == keep_score:
        return {"pick": "tie", "reason": TIE_REASON}
    if keep_score > cut_score:
        pick, winner_ev, loser_ev = "positional", keep_ev, cut_ev
        winner_share, loser_share = keep_share, cut_share
    else:
        pick, winner_ev, loser_ev = "other", cut_ev, keep_ev
        winner_share, loser_share = cut_share, keep_share
    if winner_ev["restart"] != loser_ev["restart"]:
        reason = (
            "The other try restarts inside the line; this one says "
            "it in one clean run, so the story keeps moving."
        )
    elif round(winner_share, 3) != round(loser_share, 3):
        reason = (
            "This try says the whole line; the other leaves part "
            "of it out, so this one carries the story."
        )
    else:
        reason = (
            "This try runs clean through; the other has a long "
            "dead pause inside it, which pulls attention away."
        )
    return {"pick": pick, "reason": reason}
