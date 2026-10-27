"""Direction given up front, read deterministically.

When a creator drops a raw video they can say, in plain words, how
the first edit should go: "remove retakes, last take is the best
take." This module maps that sentence onto the controls the first
edit actually has (retakes on or off, which take is kept, silence
on or off and how much of it to leave, and whether the breaths
stay natural or trim away with the pauses) with fixed rules, never a
model call, so the same words always set the same controls. Every
setting it sets is named back in the recap, in plain words. Whatever
does not map to a control is returned as a remainder, and the
remainder rides into the job as overall guidance for later model
direction, through the same channel overall feedback already uses.

Spoken direction outranks the saved setting for that video, the
precedence the front door already follows: these overrides are
applied over the saved front-door settings for the first edit's run.
"""

from __future__ import annotations

import re

BUFFER_MIN_S = 0.05
BUFFER_MAX_S = 0.50

_KEEPER_RECAP = {
    "last": (
        "When a line is said more than once, the last take stays."
    ),
    "first": (
        "When a line is said more than once, the first clean take "
        "stays."
    ),
    "best": (
        "Best delivery (Beta) will watch the retakes and record "
        "the take it would have kept. In this version the last "
        "take still stays in the edit itself."
    ),
}

# (pattern, overrides, recap) in the order they are tried. Wordings
# that turn something off are tried before the wordings that turn
# it on, so "don't remove retakes" can never read as "remove".
_RULES = [
    (
        re.compile(
            r"\b(?:don'?t|do not|never)\s+(?:remove|cut|take out)\s+"
            r"(?:the\s+)?retakes?\b"
            r"|\bkeep\s+(?:all\s+)?(?:the\s+)?takes\b"
            r"|\bleave\s+(?:the\s+)?retakes?\b",
            re.I,
        ),
        {"remove_retakes": False},
        "Retakes stay in: no take will be removed as a retake.",
    ),
    (
        re.compile(
            r"\b(?:remove|cut|take out)\s+(?:the\s+)?retakes?\b", re.I
        ),
        {"remove_retakes": True},
        "Retakes will be removed.",
    ),
    (
        re.compile(r"\bfirst\s+(?:clean\s+)?take\b", re.I),
        {"keep_mode": "first"},
        _KEEPER_RECAP["first"],
    ),
    (
        re.compile(r"\bbest\s+delivery\b", re.I),
        {"keep_mode": "best"},
        _KEEPER_RECAP["best"],
    ),
    (
        re.compile(r"\blast\s+take\b", re.I),
        {"keep_mode": "last"},
        _KEEPER_RECAP["last"],
    ),
    (
        re.compile(r"\bbest\s+take\b", re.I),
        {"keep_mode": "best"},
        _KEEPER_RECAP["best"],
    ),
    (
        re.compile(
            r"\b(?:don'?t|do not|never)\s+(?:cut|trim|remove|tighten)\s+"
            r"(?:the\s+)?silences?\b"
            r"|\bkeep\s+(?:the\s+)?silences?\b"
            r"|\bleave\s+(?:the\s+)?silences?\b",
            re.I,
        ),
        {"cut_silence": False},
        "The silences stay as they are.",
    ),
    (
        re.compile(
            r"\b(?:cut|trim|remove|tighten)\s+(?:the\s+)?silences?\b",
            re.I,
        ),
        {"cut_silence": True},
        "Silence will be cut.",
    ),
    # Breaths: the wordings that keep them are tried before the
    # wordings that trim them, so "don't cut the breaths" can
    # never read as "cut the breaths".
    (
        re.compile(
            r"\bkeep\s+(?:the\s+)?breaths?\s+natural\b"
            r"|\bkeep\s+(?:the\s+)?natural\s+breaths?\b"
            r"|\bkeep\s+(?:the\s+)?breaths?\b"
            r"|\bleave\s+(?:the\s+)?breaths?\b"
            r"|\b(?:don'?t|do not|never)\s+(?:cut|trim|remove)\s+"
            r"(?:the\s+)?breaths?\b",
            re.I,
        ),
        {"breaths": "keep"},
        "Breaths will be kept natural.",
    ),
    (
        re.compile(
            r"\b(?:cut|trim|remove|take out)\s+(?:the\s+)?breaths?\b",
            re.I,
        ),
        {"breaths": "trim"},
        "Breaths will be trimmed with the pauses.",
    ),
]

_BUFFER_PATTERNS = [
    re.compile(
        r"\bbuffer\s*(?:of|to|at)?\s*(\d+(?:\.\d+)?)\s*"
        r"(?:s|sec|seconds?)?\b",
        re.I,
    ),
    re.compile(
        r"\b(?:leave|keep|with)\s+(\d+(?:\.\d+)?)\s*"
        r"(?:s|sec|seconds?)\b[^.]*?"
        r"\b(?:silence|pause|gap|between)\b",
        re.I,
    ),
    re.compile(
        r"\bsilence\s+(?:of|at|to)?\s*(\d+(?:\.\d+)?)\s*"
        r"(?:s|sec|seconds?)\b",
        re.I,
    ),
    # A number with a time unit, once the wordings above have had
    # their turn: "cut silence to 0.2 seconds" has already given up
    # its "cut silence" by the time this runs, and the number is
    # what is left of the thought.
    re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:s|sec|seconds?)\b", re.I),
]


def parse_direction(text: str) -> dict:
    """Map plain direction onto first-edit settings.

    Returns {"overrides": {...}, "recap": [str], "remainder": str}.
    Matched phrases are cut out of the remainder, so what is left
    is only what no control could take. A spoken buffer number also
    turns silence cutting on, unless the direction turned it off in
    so many words.
    """
    remaining = " " + (text or "").strip() + " "
    overrides: dict = {}
    recap: list = []
    for pattern, settings, line in _RULES:
        match = pattern.search(remaining)
        if not match:
            continue
        for key, value in settings.items():
            if key not in overrides:
                overrides[key] = value
                recap.append(line)
        remaining = (
            remaining[: match.start()] + " " + remaining[match.end():]
        )
    buffer_value = None
    for pattern in _BUFFER_PATTERNS:
        match = pattern.search(remaining)
        if match:
            try:
                buffer_value = float(match.group(1))
            except ValueError:
                buffer_value = None
            if buffer_value is not None:
                remaining = (
                    remaining[: match.start()]
                    + " "
                    + remaining[match.end():]
                )
            break
    if buffer_value is not None:
        clamped = min(BUFFER_MAX_S, max(BUFFER_MIN_S, buffer_value))
        overrides["buffer_s"] = clamped
        if overrides.get("cut_silence") is not False:
            overrides["cut_silence"] = True
        recap.append(
            f"Silence will be left at {clamped:g} seconds "
            "between words."
        )
    remainder = re.sub(r"[^A-Za-z0-9' ]+", " ", remaining)
    remainder = re.sub(r"\s+", " ", remainder).strip()
    remainder = re.sub(
        r"^(and|please|also|with|then)\s+", "", remainder,
        flags=re.I,
    ).strip()
    if len(remainder.split()) < 3:
        remainder = ""
    return {
        "overrides": overrides,
        "recap": recap,
        "remainder": remainder,
    }
