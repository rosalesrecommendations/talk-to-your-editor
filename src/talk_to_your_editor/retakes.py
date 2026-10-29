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
  earlier take is already cut is settled; a pair carrying a
  negation, and a short complete line said twice in a row, surface
  as flags only, because which copy survives can flip the meaning
  or be emphasis. Story grounds alone never cut anything here: a
  line that merely fails to serve the direction is a flag for
  review, not a proposal.
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
        ):
            spans.append((start, i + 1))
            start = i + 1
    spans.append((start, len(words)))

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
    fragment = not earlier["terminal"]
    duplicate = coverage >= 0.95 and ratio >= 0.90
    negation = _has_negation(e_tokens) or _has_negation(l_tokens)

    if coverage >= settings["coverage_a"] and (
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
        "band": band,
        "flag_reason": flag_reason,
        "retake_class": retake_class,
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


def _kept_unit_index(units: list, members: list, settings: dict) -> int:
    """The delivery a cluster keeps: the last complete one by default.

    Under Best delivery (Beta) the positional rule still decides:
    Beta runs in shadow and applies nothing in this version.
    """
    candidates = [
        i for i in members if not units[i]["settled"] and not units[i]["protected"]
    ]
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
        negation = _has_negation(tokens[lo:hi]) or _has_negation(
            tokens[repair_lo:]
        )
        # A long verbatim run repeated inside the unit, with a real
        # delivery still to come after it, is the cleanest restart
        # shape there is: the abandoned words are a prefix of the
        # repair, word for word. That earns the trusted band. Shorter
        # or messier restarts stay in the judgment band.
        trusted = (
            not negation
            and first_run_len >= 4
            and hi - lo >= 4
            and len(tokens) - repair_lo >= 4
        )
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
                "band": "flag" if negation else ("a" if trusted else "b"),
                "flag_reason": _FLAG_NEGATION_REASON if negation else None,
                "retake_class": (
                    "false_start" if hi - lo <= 6 else "restarted_sentence"
                ),
                "shape": "internal_restart",
            }
        )
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
        negation = _has_negation(unit["kept_tokens"])
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
                "band": "flag" if negation else "b",
                "flag_reason": _FLAG_NEGATION_REASON if negation else None,
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
            negation = _has_negation(e_tokens) or _has_negation(chain_tokens)
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
                    "band": "flag" if negation else "b",
                    "flag_reason": (
                        _FLAG_NEGATION_REASON if negation else None
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
    graded records will carry.
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
    for members in clusters:
        keep_index = _kept_unit_index(units, members, settings)
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
        if shape in ("internal_restart", "settled_leftover", "fragment_chain"):
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
        operations.append(
            {
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
        )
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
            }
        )
    return operations


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
