"""Coach mode (v0.18): plain-language editing suggestions from the
local record alone.

The coach list is built only from files the session already has:
the transcript, the current cuts plan, the gold (held) record and
the retake record, plus the local retake detector in ``retakes.py``.
Nothing here calls a model or a transcription service, so building
the list costs $0. Running a suggestion the creator picks is a
different step: the thread route hands the suggestion's direction
words to the normal direction path, with that path's normal cost.

Every suggestion names something really in this video: the words,
the moment, or a structural fact (the opening line, the closing
line, the longest stretch). Generic tips are not suggestions. Each
suggestion carries a stable id built from its kind and the word
span it names, so the same record rebuilds the same ids and a
follow-up like "do the first one" can map back to one of them.

Two-way shape (Justin, 2026-11-01): a specific question is answered
first, in the terms the creator asked (a question about the opening
gets an answer about the opening), and the findings the creator did
not ask about follow in their own section. Future-recording tips
are built only when the creator asks how to record better; they are
marked for next time and never ride along on a plain coach answer.

Plain words throughout, and no em dashes in anything user-facing.
"""

from __future__ import annotations

import re
from typing import Optional

from . import retakes
from .timeline import Timeline

COACH_QUESTION = "What would you do to make this better?"

# A gap between two kept words at or above this many seconds is a
# long pause worth naming. Ordinary breaths and sentence breaks sit
# well under it.
LONG_PAUSE_S = 2.0

# Short lists only: the coach names the few changes that matter
# most, in priority order, instead of dumping every finding.
MAX_RECORD_SUGGESTIONS = 4
MAX_STORY_SUGGESTIONS = 3

# Openings that spend their first seconds on a greeting or a
# table of contents instead of the story itself.
_PREAMBLE_WORDS = (
    "welcome", "hello", "hey", "hi", "good morning", "good afternoon",
    "in this video", "today we", "today i am going to",
    "today i'm going to", "let's get started", "lets get started",
)

# Words that mark a line as carrying a payoff or a turn: a line
# with one of these is a candidate for the strongest line in the
# video, the one the opening and the ending are measured against.
_STRONG_WORDS = (
    "never", "always", "secret", "mistake", "wrong", "stopped",
    "changed", "saved", "miracle", "but", "why", "how", "what if",
    "truth", "afraid", "hope", "free", "won", "lost", "died",
    "alive", "answered",
)

_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5,
}


def _clock(seconds: float) -> str:
    """Seconds as the thread shows them: "0:07", "12:40"."""
    total = max(0, int(round(float(seconds or 0.0))))
    return f"{total // 60}:{total % 60:02d}"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _quote(text: str, limit: int = 90) -> str:
    """A short quoted span of the video's own words."""
    cleaned = _clean(text)
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def normalize(text: str) -> str:
    """Lowercase words only, single spaces: the intent matcher's text."""
    lowered = re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())
    return re.sub(r"\s+", " ", lowered).strip()


# ---------------------------------------------------------------------------
# Intent recognition
# ---------------------------------------------------------------------------

_CANNED_PHRASES = (
    "what would you do to make this better",
    "what would you do to make it better",
    "what would you do to improve",
    "how can i make this better",
    "how can i make it better",
    "how do i make this better",
    "how do i make it better",
    "how can i improve this video",
    "how can i improve my video",
    "how do i improve this video",
    "how can we make this better",
    "make this better",
    "make it better",
    "coach me",
    "any suggestions",
    "what are your suggestions",
    "what do you suggest",
    "how can this be better",
    "how could this be better",
)

# Topic words a coaching question can be about. A question naming
# one of these (or an advice shape below) is a coach question; a
# plain statement naming one stays a direction.
_TOPIC_WORDS = (
    "opening", "open", "start", "beginning", "hook", "intro",
    "ending", "end", "close", "closing", "payoff", "pacing", "pace",
    "pauses", "pause", "story", "order", "structure", "flow",
    "retention", "engagement", "audience", "viewers", "watch",
    "cut", "cuts", "trim", "edit", "editing", "retake", "retakes",
    "repeat", "repeats", "better", "improve", "improvement",
    "stronger", "tighter", "advice", "feedback", "think",
    "record", "recording", "film", "filming", "next time",
)

_ADVICE_STARTS = (
    "what", "how", "why", "should", "is", "are", "was", "were",
    "can", "could", "would", "do", "does", "did", "where", "when",
)

_RECORDING_PHRASES = (
    "record better", "recording better", "record it better",
    "film better", "filming better", "shoot better",
    "how should i record", "how do i record", "how can i record",
    "record next time", "recording tips", "for next time",
    "next time i record",
)


def is_recording_question(text: str) -> bool:
    """True when the creator is asking how to record better."""
    normalized = normalize(text)
    if any(phrase in normalized for phrase in _RECORDING_PHRASES):
        return True
    return "record" in normalized and (
        "better" in normalized or "next time" in normalized
    )


def coach_focus(text: str) -> Optional[str]:
    """The topic a coaching question is about, or None.

    One of "opening", "ending", "pacing", "order", "cuts",
    "retakes", "recording", "general". The focus shapes the first
    part of the answer; None means the words are not a coaching
    question at all.
    """
    normalized = normalize(text)
    if not normalized:
        return None
    if is_recording_question(text):
        return "recording"
    if any(phrase in normalized for phrase in _CANNED_PHRASES):
        return "general"
    words = normalized.split(" ")
    is_question = (
        "?" in str(text or "")
        or words[0] in _ADVICE_STARTS
        or normalized.startswith(("tell me", "give me"))
    )
    if not is_question:
        return None
    if not any(topic in normalized for topic in _TOPIC_WORDS):
        return None
    word_set = set(words)
    if word_set & {"opening", "hook", "intro", "beginning"} \
            or normalized.startswith(("how does it start", "how do i start")):
        return "opening"
    if word_set & {"ending", "end", "payoff", "close", "closing"}:
        return "ending"
    if any(word in normalized for word in ("pacing", "pace", "pause", "slow", "tight")):
        return "pacing"
    if any(word in normalized for word in ("order", "structure", "flow", "story")):
        return "order"
    if any(word in normalized for word in ("retake", "repeat")):
        return "retakes"
    if any(word in normalized for word in ("cut", "trim")):
        return "cuts"
    return "general"


def is_coach_question(text: str) -> bool:
    return coach_focus(text) is not None


_FOLLOWUP_RE = re.compile(
    r"^(?:yes\s+|ok\s+|okay\s+|please\s+)*"
    r"(?:do|run|apply|go with|take)\s+"
    r"(?:it|that|this|the\s+\w+(?:\s+one|\s+suggestion)?|"
    r"number\s+\w+|#?\d+|\w+\s+one|first|second|"
    r"coach-[a-z0-9-]+)$"
)


def followup_index(text: str) -> Optional[int]:
    """The 1-based suggestion number a follow-up names, or None.

    "Do it" and "do that" name the first suggestion. An ordinal
    ("do the second one") names that one. A bare suggestion id
    named in the words ("do coach-pause-12") resolves in the
    caller, which owns the list. Returns None when the words are
    not a coach follow-up at all.
    """
    if "coach-" in str(text or "").lower():
        return None  # The caller resolves an explicit id itself.
    normalized = normalize(text)
    if not normalized:
        return None
    if not _FOLLOWUP_RE.match(normalized):
        return None
    for word, number in _ORDINAL_WORDS.items():
        if re.search(rf"\b{word}\b", normalized):
            return number
    digits = re.search(r"\b(\d+)\b", normalized)
    if digits:
        return int(digits.group(1))
    if "number" in normalized:
        return None
    return 1


def followup_suggestion_id(text: str) -> Optional[str]:
    """A suggestion id named outright in a follow-up, or None."""
    match = re.search(
        r"coach-[a-z0-9]+(?:-[a-z0-9]+)*", str(text or "").lower()
    )
    if not match:
        return None
    # Only a do/run-shaped sentence counts as a follow-up.
    if re.match(
        r"^(yes\s+|ok\s+|okay\s+|please\s+)*(do|run|apply|go with|take)\b",
        normalize(text),
    ):
        return match.group(0)
    return None


# ---------------------------------------------------------------------------
# The local record, read into working shapes
# ---------------------------------------------------------------------------


def _sentences(words: list, statuses: list) -> list:
    """The kept words grouped into sentence-ish stretches.

    A stretch ends at sentence punctuation or at a long pause.
    Each stretch carries its word span and its own words, so story
    suggestions can quote the video exactly.
    """
    kept = [i for i in range(len(words)) if statuses[i] == "kept"]
    stretches = []
    start = None
    previous = None
    for index in kept:
        if start is None:
            start = index
        if previous is not None:
            gap = float(words[index]["start"]) - float(words[previous]["end"])
            ended = str(words[previous].get("word") or "").rstrip().endswith(
                (".", "!", "?")
            )
            if ended or gap >= LONG_PAUSE_S:
                stretches.append((start, previous + 1))
                start = index
        previous = index
    if start is not None and previous is not None:
        stretches.append((start, previous + 1))
    out = []
    for word_start, word_end in stretches:
        out.append(
            {
                "word_start": word_start,
                "word_end": word_end,
                "text": " ".join(
                    str(words[i].get("word") or "")
                    for i in range(word_start, word_end)
                ),
                "at_s": float(words[word_start]["start"]),
            }
        )
    return out


def _suggestion(
    kind, category, word_start, word_end, text, direction, at_s=0.0, quote=""
):
    span = ""
    if isinstance(word_start, int) and isinstance(word_end, int):
        span = f"-{word_start}-{word_end}"
    return {
        "id": f"coach-{kind}{span}",
        "kind": kind,
        "category": category,
        "word_start": word_start,
        "word_end": word_end,
        "at_s": round(float(at_s or 0.0), 3),
        "text": text,
        "direction": direction,
        "quote": quote,
    }


# ---------------------------------------------------------------------------
# Record suggestions: grounded in the transcript and editing record
# ---------------------------------------------------------------------------


def _retake_suggestions(timeline: Timeline, recorded_pairs: list) -> list:
    """Left-in retakes and repeated lines still in the edit.

    The retake record's own pairs come first (the pass already
    measured them), then the local detector over the current edit
    for anything the record does not carry. A pair only counts
    while its first try is still in the edit: words already cut
    are settled, not a suggestion.
    """
    words = timeline.words
    suggestions = []
    seen_spans = set()

    def add(cut_start, cut_end, keep_start, keep_end):
        if not all(
            isinstance(v, int) for v in (cut_start, cut_end, keep_start, keep_end)
        ):
            return
        if not (0 <= cut_start < cut_end <= len(words)):
            return
        if (cut_start, cut_end) in seen_spans:
            return
        if timeline.word_status(cut_start) != "kept":
            return
        seen_spans.add((cut_start, cut_end))
        cut_text = " ".join(
            str(words[i].get("word") or "") for i in range(cut_start, cut_end)
        )
        keep_text = " ".join(
            str(words[i].get("word") or "")
            for i in range(keep_start, min(keep_end, len(words)))
        )
        at = _clock(words[cut_start]["start"])
        text = (
            f"Take out the first try at {at}: \"{_quote(cut_text)}\". "
            f"You say the line again right after as \"{_quote(keep_text)}\", "
            "and the second try is the cleaner one to keep."
        )
        direction = (
            f"Cut the words \"{_quote(cut_text, 200)}\" at {at}, "
            "the first try of the line that is said again right after."
        )
        suggestions.append(
            _suggestion(
                "retake", "record", cut_start, cut_end, text, direction,
                at_s=words[cut_start]["start"], quote=_quote(cut_text),
            )
        )

    for pair in recorded_pairs or []:
        if not isinstance(pair, dict):
            continue
        cut = pair.get("cut") or {}
        keep = pair.get("keep") or {}
        add(
            cut.get("word_start"), cut.get("word_end"),
            keep.get("word_start"), keep.get("word_end"),
        )
    try:
        detected = retakes.find_pairs(timeline)
    except Exception:
        detected = []
    for pair in detected:
        cut = pair.get("cut") or {}
        keep = pair.get("keep") or {}
        add(
            cut.get("word_start"), cut.get("word_end"),
            keep.get("word_start"), keep.get("word_end"),
        )
    return suggestions


def _pause_suggestions(timeline: Timeline) -> list:
    """Long pauses between kept words, longest first."""
    words = timeline.words
    pauses = []
    for index in range(len(words) - 1):
        if timeline.word_status(index) != "kept":
            continue
        if timeline.word_status(index + 1) != "kept":
            continue
        gap = float(words[index + 1]["start"]) - float(words[index]["end"])
        if gap >= LONG_PAUSE_S:
            pauses.append((gap, index))
    pauses.sort(key=lambda pair: (-pair[0], pair[1]))
    suggestions = []
    for gap, index in pauses[:2]:
        before = str(words[index].get("word") or "")
        after = str(words[index + 1].get("word") or "")
        at = _clock(words[index]["end"])
        shown = f"{gap:.0f}" if gap >= 2.5 else f"{gap:.1f}"
        text = (
            f"Tighten the long pause at {at}. After \"{before}\" there are "
            f"about {shown} seconds of quiet before \"{after}\" starts, "
            "and the story stalls there."
        )
        direction = (
            f"Tighten the long pause after \"{before}\" at {at}, "
            "the quiet stretch before the next words, keeping it natural."
        )
        suggestions.append(
            _suggestion(
                "pause", "record", index, index + 1, text, direction,
                at_s=words[index]["end"],
            )
        )
    return suggestions


def _held_suggestions(timeline: Timeline, held: list) -> list:
    """Held items: spots the editor left for the creator's call."""
    words = timeline.words
    suggestions = []
    for view in held or []:
        if not isinstance(view, dict):
            continue
        start, end = view.get("word_start"), view.get("word_end")
        if not (isinstance(start, int) and isinstance(end, int)):
            continue
        if not (0 <= start < end <= len(words)):
            continue
        quote = _quote(
            str(view.get("text") or "")
            or " ".join(str(words[i].get("word") or "") for i in range(start, end))
        )
        at = _clock(words[start]["start"])
        reason = _clean(str(view.get("reason") or ""))
        text = (
            f"Decide the held spot at {at}: \"{quote}\". "
            "It is still in the video, waiting for your call."
        )
        if reason:
            text += f" Why it was held: {reason}"
            if not text.endswith("."):
                text += "."
        direction = (
            f"Cut the held words \"{_quote(quote, 200)}\" at {at}, "
            "the spot that was held for my call."
        )
        suggestions.append(
            _suggestion(
                "held", "record", start, end, text, direction,
                at_s=words[start]["start"],
            )
        )
    return suggestions


# ---------------------------------------------------------------------------
# Story suggestions: retention and engagement, from this video's shape
# ---------------------------------------------------------------------------


def _strongest_stretch(stretches: list, skip_first: int = 1) -> Optional[dict]:
    """The strongest later line: a question, or a payoff word carrier."""
    best = None
    best_score = 0
    for stretch in stretches[skip_first:]:
        text = stretch["text"]
        lowered = text.lower()
        score = 0
        if text.rstrip().endswith("?"):
            score += 3
        score += sum(1 for word in _STRONG_WORDS if word in lowered)
        if 4 <= len(text.split()) <= 22:
            score += 1
        if score > best_score:
            best = stretch
            best_score = score
    return best if best_score >= 2 else None


def _story_suggestions(stretches: list) -> list:
    """Opening, order and payoff suggestions quoting this video."""
    suggestions = []
    if not stretches:
        return suggestions
    opening = stretches[0]
    opening_text = opening["text"]
    opening_lowered = opening_text.lower()
    strongest = _strongest_stretch(stretches)
    preamble = any(word in opening_lowered for word in _PREAMBLE_WORDS)
    long_opening = len(opening_text.split()) > 22
    if strongest is not None and (preamble or long_opening):
        at = _clock(strongest["at_s"])
        why = (
            "the opening spends its first seconds on a greeting"
            if preamble
            else "the opening line runs long before the story starts"
        )
        text = (
            f"Open on the strong line instead. Right now {why}: "
            f"\"{_quote(opening_text)}\". The line at {at}, "
            f"\"{_quote(strongest['text'])}\", gives a viewer a reason "
            "to stay in the first seconds."
        )
        direction = (
            f"Move the line \"{_quote(strongest['text'], 200)}\" at {at} "
            "to the very start of the video, so the video opens on it "
            f"instead of on \"{_quote(opening_text, 200)}\"."
        )
        suggestions.append(
            _suggestion(
                "opening", "story", strongest["word_start"],
                strongest["word_end"], text, direction,
                at_s=strongest["at_s"],
            )
        )
    elif strongest is not None and strongest["word_start"] > opening["word_end"]:
        # The strongest line sits buried past the opening third.
        total_end = stretches[-1]["word_end"]
        if strongest["word_start"] > total_end * 0.4:
            at = _clock(strongest["at_s"])
            text = (
                f"Bring the strong line earlier. \"{_quote(strongest['text'])}\" "
                f"does not arrive until {at}, and a viewer who leaves before "
                "then never hears the best part."
            )
            direction = (
                f"Move the line \"{_quote(strongest['text'], 200)}\" at {at} "
                "earlier in the video, near the start."
            )
            suggestions.append(
                _suggestion(
                    "order", "story", strongest["word_start"],
                    strongest["word_end"], text, direction,
                    at_s=strongest["at_s"],
                )
            )
    if len(stretches) >= 3:
        longest = max(stretches, key=lambda s: len(s["text"].split()))
        if len(longest["text"].split()) > 26:
            at = _clock(longest["at_s"])
            text = (
                f"Tighten the longest stretch, at {at}: "
                f"\"{_quote(longest['text'])}\". It runs "
                f"{len(longest['text'].split())} words without a break, "
                "and that is where attention is most likely to drift."
            )
            direction = (
                f"Tighten the long stretch starting \""
                f"{_quote(longest['text'], 200)}\" at {at}, cutting the "
                "parts that repeat its point."
            )
            suggestions.append(
                _suggestion(
                    "tighten", "story", longest["word_start"],
                    longest["word_end"], text, direction, at_s=longest["at_s"],
                )
            )
    if len(stretches) >= 2:
        closing = stretches[-1]
        closing_lowered = closing["text"].lower()
        signoff = any(
            word in closing_lowered
            for word in ("thanks for watching", "see you", "bye", "subscribe")
        )
        payoff = stretches[-2]
        if signoff and len(payoff["text"].split()) >= 4:
            at = _clock(payoff["at_s"])
            text = (
                f"End on the payoff line. The video closes with "
                f"\"{_quote(closing['text'])}\", but the line before it, "
                f"\"{_quote(payoff['text'])}\" at {at}, is the one a "
                "viewer will remember. Land on that line and keep the "
                "sign-off short after it."
            )
            direction = (
                f"End the video on the line \"{_quote(payoff['text'], 200)}\" "
                f"at {at}, and keep only a short sign-off after it."
            )
            suggestions.append(
                _suggestion(
                    "payoff", "story", payoff["word_start"],
                    payoff["word_end"], text, direction, at_s=payoff["at_s"],
                )
            )
    return suggestions[:MAX_STORY_SUGGESTIONS]


# ---------------------------------------------------------------------------
# Focus answers and recording tips
# ---------------------------------------------------------------------------

_FOCUS_KINDS = {
    "opening": ("opening", "order"),
    "ending": ("payoff",),
    "pacing": ("pause", "tighten"),
    "order": ("order", "opening", "tighten"),
    "cuts": ("retake", "held", "pause", "tighten"),
    "retakes": ("retake",),
    "general": (),
    "recording": (),
}


def _focus_answer(focus: str, stretches: list, suggestions: list) -> str:
    """The first part of a coach reply: the answer to what was asked.

    Built from this video's own opening, closing, pauses and
    stretches, in the terms of the question, never a generic line.
    """
    if not stretches:
        return (
            "There is not enough of the video left in the edit for "
            "me to point at a specific spot yet."
        )
    opening = stretches[0]
    closing = stretches[-1]
    if focus == "opening":
        lowered = opening["text"].lower()
        if any(word in lowered for word in _PREAMBLE_WORDS):
            verdict = (
                "the opening spends its first seconds on a greeting "
                "before the story starts"
            )
        elif opening["text"].rstrip().endswith("?"):
            verdict = "the opening asks a question, which gives a viewer a reason to stay"
        elif len(opening["text"].split()) > 22:
            verdict = "the opening line runs long before it lands"
        else:
            verdict = "the opening gets to its point in one line"
        return (
            f"About the opening: you start with \"{_quote(opening['text'])}\", "
            f"and {verdict}."
        )
    if focus == "ending":
        return (
            f"About the ending: the video closes on \"{_quote(closing['text'])}\". "
            "That closing line is the last thing a viewer hears, so it "
            "carries the payoff."
        )
    if focus == "pacing":
        pauses = [s for s in suggestions if s["kind"] == "pause"]
        if pauses:
            return (
                "About the pacing: the quiet stretches are where it "
                f"slows down most. {pauses[0]['text']}"
            )
        longest = max(stretches, key=lambda s: len(s["text"].split()))
        return (
            "About the pacing: there are no long silences left in the "
            f"edit. The slowest stretch is the long run at "
            f"{_clock(longest['at_s'])}: \"{_quote(longest['text'])}\"."
        )
    if focus == "order":
        strongest = _strongest_stretch(stretches)
        if strongest is not None:
            return (
                f"About the order: the story starts with "
                f"\"{_quote(opening['text'])}\" and the strongest line, "
                f"\"{_quote(strongest['text'])}\", sits at "
                f"{_clock(strongest['at_s'])}. Where that line sits is "
                "the order choice that matters most here."
            )
        return (
            f"About the order: the story starts with "
            f"\"{_quote(opening['text'])}\" and closes with "
            f"\"{_quote(closing['text'])}\", in the order you said them."
        )
    if focus == "cuts":
        cuts = [
            s for s in suggestions
            if s["kind"] in ("retake", "held", "pause")
        ]
        if cuts:
            return (
                "About what to cut: the clearest cuts are the ones "
                f"below, starting with this. {cuts[0]['text']}"
            )
        return (
            "About what to cut: I do not see a repeated line, a long "
            "pause, or a held spot left in this edit to point at."
        )
    if focus == "retakes":
        retakes_found = [s for s in suggestions if s["kind"] == "retake"]
        if retakes_found:
            return (
                "About retakes: yes, at least one first try is still "
                f"in the edit. {retakes_found[0]['text']}"
            )
        return (
            "About retakes: I do not see a line said twice in a row "
            "left in this edit."
        )
    return ""


def recording_tips(suggestions: list, stretches: list) -> list:
    """Future-recording tips, each tied to something in this video.

    Only built when the creator asks how to record better. Every
    tip names the moment in this video that taught it, and the
    section is marked for next time by the caller.
    """
    tips = []
    for suggestion in suggestions:
        if suggestion["kind"] == "retake":
            tips.append(
                "When a line comes out wrong, stop, take a breath, and "
                "say the whole line again from its start. The line "
                f"\"{_quote(suggestion.get('quote') or '', 60)}\" was "
                "said twice in this video, and a clean restart is the "
                "copy that is easy to keep."
            )
            break
    for suggestion in suggestions:
        if suggestion["kind"] == "pause":
            tips.append(
                "Leave shorter quiet gaps between thoughts. The pause "
                f"in this video at {_clock(suggestion['at_s'])} is the "
                "kind that slows a viewer down."
            )
            break
    if stretches:
        opening = stretches[0]
        if any(word in opening["text"].lower() for word in _PREAMBLE_WORDS):
            tips.append(
                "Start recording with the strong line itself, not the "
                f"greeting. This video opens with \"{_quote(opening['text'])}\", "
                "and the story only starts after it."
            )
    return tips[:3]


# ---------------------------------------------------------------------------
# The whole analysis
# ---------------------------------------------------------------------------


def build_suggestions(
    transcript: dict,
    cuts: dict,
    held: Optional[list] = None,
    recorded_retake_pairs: Optional[list] = None,
    profile: Optional[dict] = None,
) -> list:
    """The ordered coach list for one job, from the local record.

    Record suggestions first (retakes, long pauses, held items),
    then story suggestions (opening, order, payoff). $0: this
    reads dicts the caller loaded from the session's own files
    and runs the local retake detector, nothing else.
    """
    if not isinstance(transcript, dict) or not transcript.get("words"):
        return []
    timeline = Timeline(transcript, cuts or {})
    words = timeline.words
    statuses = [timeline.word_status(i) for i in range(len(words))]
    if not any(status == "kept" for status in statuses):
        return []
    record = []
    record.extend(_retake_suggestions(timeline, recorded_retake_pairs or []))
    record.extend(_pause_suggestions(timeline))
    record.extend(_held_suggestions(timeline, held or []))
    record = record[:MAX_RECORD_SUGGESTIONS]
    stretches = _sentences(words, statuses)
    story = _story_suggestions(stretches)
    return record + story


def coach_reply(
    transcript: dict,
    cuts: dict,
    focus: str = "general",
    held: Optional[list] = None,
    recorded_retake_pairs: Optional[list] = None,
    profile: Optional[dict] = None,
) -> dict:
    """One coach answer: {text, lines, suggestions, focus}.

    A general ask gets the ordered list. A specific question gets
    its answer first (the ``text`` and the focused suggestions at
    the head of the list), then the unasked-for findings under
    their own heading. A recording question gets its answer plus
    the for-next-time tips, and only then.
    """
    suggestions = build_suggestions(
        transcript, cuts, held=held,
        recorded_retake_pairs=recorded_retake_pairs, profile=profile,
    )
    timeline = None
    stretches: list = []
    if isinstance(transcript, dict) and transcript.get("words"):
        timeline = Timeline(transcript, cuts or {})
        statuses = [
            timeline.word_status(i) for i in range(len(timeline.words))
        ]
        stretches = _sentences(timeline.words, statuses)

    if focus == "recording":
        tips = recording_tips(suggestions, stretches)
        text = (
            "For next time you record, from what is in this video:"
            if tips
            else (
                "I do not see a recording habit in this video worth "
                "changing. The takes here are clean enough that the "
                "work is all in the edit."
            )
        )
        lines = [f"For next time: {tip}" for tip in tips]
        if suggestions:
            lines.append("And for this video, the edit itself:")
            lines.extend(
                f"{n}. {s['text']}" for n, s in enumerate(suggestions, 1)
            )
        return {
            "text": text, "lines": lines,
            "suggestions": suggestions, "focus": focus,
        }

    if focus and focus != "general":
        answer = _focus_answer(focus, stretches, suggestions)
        kinds = _FOCUS_KINDS.get(focus, ())
        focused = [s for s in suggestions if s["kind"] in kinds]
        rest = [s for s in suggestions if s["kind"] not in kinds]
        ordered = focused + rest
        lines = []
        if focused:
            lines.append("Start with this one:")
            lines.extend(
                f"{n}. {s['text']}" for n, s in enumerate(focused, 1)
            )
        if rest:
            lines.append("Also, from watching the record:")
            lines.extend(
                f"{n}. {s['text']}"
                for n, s in enumerate(rest, len(focused) + 1)
            )
        if not ordered:
            lines.append(
                "I do not see a spot in this video that needs one of "
                "those changes right now."
            )
        return {
            "text": answer or "Here is what I see in this video.",
            "lines": lines, "suggestions": ordered, "focus": focus,
        }

    if not suggestions:
        return {
            "text": (
                "I looked through the record for this video and I do "
                "not see a repeated line, a long pause, a held spot, "
                "or a story spot I would change first. It is in good "
                "shape as it stands."
            ),
            "lines": [], "suggestions": [], "focus": focus,
        }
    text = (
        "Here is what I would do, in the order I would do it. "
        "Say \"do it\" for the first one, or name the one you want."
    )
    lines = [
        f"{n}. {s['text']}" for n, s in enumerate(suggestions, 1)
    ]
    return {
        "text": text, "lines": lines,
        "suggestions": suggestions, "focus": focus,
    }
