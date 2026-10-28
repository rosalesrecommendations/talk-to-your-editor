"""Saved creator rules: the style the app learns, one yes at a time.

Grades the creator leaves on changes are grouped into patterns (the
learning module's grouping). When the same pattern keeps working, the
app offers it once, in plain words: "Pattern caught: ... Make this a
rule for future edits?" Nothing is ever saved without that yes. A
saved rule is a statement in a session file; it rides into every
later direction run as standing direction, alongside the creator's
own overall notes, and it can be removed at any time.

One explicit yes is enough: when the creator marks a single graded
change as "my style" on the review page, that pattern is offered
right away instead of waiting for the repeat threshold.

This module reads and writes plain JSON dicts. The store lives in
the session folder (``tye-session/creator_rules.json``), never in the
job's own files. Learning here is inference over the creator's own
grades, not model training.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .learning import extract_patterns, pattern_key_for

# How many supporting grades of the same pattern it takes before the
# pattern is offered as a rule. One explicit "this is my style" mark
# offers it immediately, whatever the count.
RULE_SUGGESTION_THRESHOLD = 3

_PLAIN_ACTION_BY_TYPE = {
    "cut_words": "taking words out",
    "restore_words": "bringing words back",
    "replace_take": "swapping in another take",
    "set_join_pause": "changing how long a pause runs",
    "flag_for_review": "leaving a part for your decision",
}

# A pattern description written by a person may be offered verbatim;
# one carrying a machine token (snake_case) may not, so the card can
# never show a code.
_CODE_TOKEN = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

# Learned rules run at two speeds. A "small" rule is only about a
# filler word, a pause, a breath, or one word kept or cut, so it is
# offered the way patterns have always been offered. A "story" rule
# can change what a video means, so it is offered one at a time and
# only ever saved on a clear yes. When the data cannot show a
# pattern is small, it is a story rule.
RULE_CLASS_SMALL = "small"
RULE_CLASS_STORY = "story"

# Whole words in a pattern key that mark the pattern as small: it is
# only about filler words, pauses, or breaths.
_SMALL_KEY_WORDS = frozenset({"filler", "pause", "pauses", "breath", "breaths"})

_SUPPORT_VERDICTS = frozenset({"worked", "partly"})


def _pattern_key_of(pattern) -> str:
    """The pattern key for a pattern dict (or a bare key string)."""
    if isinstance(pattern, str):
        return pattern.strip()
    if isinstance(pattern, dict):
        for field in ("pattern_key", "operation_type"):
            value = pattern.get(field)
            if value and str(value).strip():
                return str(value).strip()
    return ""


def _pattern_operation_type(pattern) -> Optional[str]:
    """The operation type a pattern dict records, when it records one."""
    if isinstance(pattern, dict):
        value = pattern.get("operation_type")
        if value and str(value).strip():
            return str(value).strip()
    return None


def _spans_one_word(entry: dict) -> bool:
    """True when an entry records word_start/word_end over one word."""
    start = entry.get("word_start")
    end = entry.get("word_end")
    if isinstance(start, bool) or isinstance(end, bool):
        return False
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return False
    return end - start == 1


def rule_class_for(pattern, entries=None) -> str:
    """Which speed a learned pattern runs at: "small" or "story".

    Small when the pattern is only about a pause length (operation
    type ``set_join_pause``), only about filler words, pauses, or
    breaths (named in the pattern key), or a single word kept or
    cut (every supporting grade covers exactly one word). Story
    for everything else: whole-thought cuts, kept repeats, take
    swaps, retake bands, gold patterns, and anything the data
    cannot show is small. When in doubt, story.
    """
    entries = [e for e in (entries or []) if isinstance(e, dict)]
    key = _pattern_key_of(pattern)
    if _pattern_operation_type(pattern) == "set_join_pause":
        return RULE_CLASS_SMALL
    key_words = re.findall(r"[a-z]+", key.lower())
    if any(word in _SMALL_KEY_WORDS for word in key_words):
        return RULE_CLASS_SMALL
    supporting = [
        entry
        for entry in entries
        if entry.get("verdict") in _SUPPORT_VERDICTS
        and pattern_key_for(entry) == key
    ]
    if supporting and all(
        entry.get("operation_type") == "set_join_pause" for entry in supporting
    ):
        return RULE_CLASS_SMALL
    # Take swaps, gold patterns, and retake bands can change what a
    # video means even when one graded change happened to cover a
    # single word, so the one-word check below never makes them
    # small.
    if _pattern_operation_type(pattern) == "replace_take":
        return RULE_CLASS_STORY
    if key_words and key_words[0] in ("gold", "retake"):
        return RULE_CLASS_STORY
    if supporting and all(_spans_one_word(entry) for entry in supporting):
        return RULE_CLASS_SMALL
    return RULE_CLASS_STORY


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def plain_rule_statement(pattern: dict) -> str:
    """The rule's statement, in words the creator already uses.

    Prefers a human pattern description when one came with the
    grades and carries no machine tokens. Otherwise the statement is
    built from the kind of change and, when there is one, the
    creator's own note that kept working.
    """
    description = str(pattern.get("pattern_description") or "").strip()
    if description and not _CODE_TOKEN.search(description):
        if not description.endswith((".", "!", "?")):
            description += "."
        return description[0].upper() + description[1:]
    action = _PLAIN_ACTION_BY_TYPE.get(
        str(pattern.get("operation_type") or ""),
        "making this kind of change",
    )
    samples = pattern.get("sample_feedback") or []
    sample = str(samples[0]).strip() if samples else ""
    if sample:
        if len(sample) > 90:
            sample = sample[:87].rstrip() + "..."
        return (
            f'When you leave a note like "{sample}", {action} is '
            "the right move."
        )
    return f"{action[0].upper() + action[1:]} is the right move when you ask for it."


def _entry_key(entry: dict) -> str:
    """A stable key for one graded entry, so re-grading replaces."""
    record_id = entry.get("record_id") or entry.get("id")
    if record_id:
        return f"record:{record_id}"
    return "entry:{}|{}|{}".format(
        entry.get("comment_id") or "",
        entry.get("operation_type") or "",
        entry.get("ts") or "",
    )


def merge_grade_entries(existing: list, incoming: list) -> list:
    """Merge new graded entries over the stored ones, by entry key.

    The latest grade for a change wins, so grading the same change
    again updates its record instead of counting it twice.
    """
    merged: dict[str, dict] = {}
    order: list[str] = []
    for entry in list(existing) + list(incoming):
        if not isinstance(entry, dict):
            continue
        key = _entry_key(entry)
        if key not in merged:
            order.append(key)
        merged[key] = entry
    return [merged[key] for key in order]


def _explicit_patterns(entries: list) -> set:
    """Pattern keys the creator explicitly marked as their style.

    The mark only counts on a change that worked (or partly worked):
    a style rule is never built out of a change that did not land.
    """
    keys = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("rule_request") and entry.get("verdict") in (
            "worked",
            "partly",
        ):
            keys.add(pattern_key_for(entry))
    return keys


def suggestions_for(entries: list, store: dict) -> list:
    """Patterns ready to offer as rules, in plain words.

    A pattern is offered when it has at least
    ``RULE_SUGGESTION_THRESHOLD`` supporting grades, or when the
    creator explicitly marked one of its changes as their style.
    Patterns already saved as rules, and patterns the creator
    dismissed at their current support count, are not offered again;
    a dismissed pattern comes back only when its support has grown
    past the count at which it was dismissed.

    Every suggestion carries its ``rule_class`` ("small" or
    "story", from :func:`rule_class_for`). Small suggestions come
    first and all of them surface; at most one story suggestion
    surfaces, after the small ones.
    """
    saved_keys = {
        rule.get("pattern_key")
        for rule in store.get("rules", [])
        if isinstance(rule, dict)
    }
    dismissed = store.get("dismissed") or {}
    explicit = _explicit_patterns(entries)
    suggestions = []
    for pattern in extract_patterns(entries):
        key = pattern["pattern_key"]
        if key in saved_keys:
            continue
        support = pattern["support_count"]
        offered = support >= RULE_SUGGESTION_THRESHOLD or key in explicit
        if not offered:
            continue
        if key in dismissed and support <= int(dismissed[key]):
            continue
        suggestions.append(
            {
                "pattern_key": key,
                "operation_type": pattern.get("operation_type"),
                "statement": plain_rule_statement(pattern),
                "support_count": support,
                "explicit": key in explicit,
                "sample_feedback": list(pattern.get("sample_feedback") or []),
                "rule_class": rule_class_for(pattern, entries),
            }
        )
    # Small suggestions all surface, as they always have. Story
    # suggestions are offered one at a time: only the strongest one
    # (extract_patterns already sorts by support, so the first story
    # suggestion is the strongest), and it stands apart from the
    # small ones instead of being bundled with them. The rest wait
    # until this one is saved or passed over.
    small = [s for s in suggestions if s["rule_class"] == RULE_CLASS_SMALL]
    story = [s for s in suggestions if s["rule_class"] != RULE_CLASS_SMALL]
    return small + story[:1]


# ---------------------------------------------------------------------------
# The store file
# ---------------------------------------------------------------------------


def load_rules_store(path: Path) -> dict:
    """The rules store at ``path``; an empty store when there is none."""
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict):
            return {
                "rules": [
                    rule
                    for rule in payload.get("rules", [])
                    if isinstance(rule, dict)
                ],
                "dismissed": dict(payload.get("dismissed") or {}),
            }
    return {"rules": [], "dismissed": {}}


def save_rules_store(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(store, handle, indent=2)
        handle.write("\n")


def save_rule(store: dict, suggestion: dict) -> dict:
    """Add the rule a suggestion offers; returns the rule record.

    Saving a pattern that is already a rule changes nothing: the
    existing record comes back, so a double tap can never stack two
    copies of the same rule.
    """
    for rule in store["rules"]:
        if rule.get("pattern_key") == suggestion["pattern_key"]:
            return rule
    rule_class = suggestion.get("rule_class")
    if rule_class not in (RULE_CLASS_SMALL, RULE_CLASS_STORY):
        # A suggestion with no class on it is treated as a story
        # rule: when in doubt, the slower speed wins.
        rule_class = RULE_CLASS_STORY
    rule = {
        "rule_id": "rule-" + re.sub(
            r"[^A-Za-z0-9_-]+", "-", str(suggestion["pattern_key"])
        ).strip("-").lower(),
        "pattern_key": suggestion["pattern_key"],
        "operation_type": suggestion.get("operation_type"),
        "statement": suggestion["statement"],
        "support_count": suggestion.get("support_count", 0),
        "source": "explicit" if suggestion.get("explicit") else "pattern",
        "rule_class": rule_class,
        "active": True,
        "created_at": _now(),
    }
    if suggestion.get("source") == "teaching":
        # A rule learned from a teaching drop starts untested: it
        # has not yet been seen working in a finished run. The
        # basis and kind ride along, so the record says where the
        # rule came from (a recording, a pair, a finished video)
        # and whose editing it describes (the creator's own, or a
        # style they like).
        rule["source"] = "teaching"
        rule["tested"] = False
        rule["basis"] = suggestion.get("basis")
        rule["kind"] = suggestion.get("kind")
    store["rules"].append(rule)
    store.get("dismissed", {}).pop(rule["pattern_key"], None)
    return rule


def remove_rule(store: dict, rule_id: str) -> Optional[dict]:
    """Remove one saved rule by id; the removed record, or None."""
    for index, rule in enumerate(store["rules"]):
        if rule.get("rule_id") == rule_id:
            removed = store["rules"].pop(index)
            # A removed rule's pattern may be offered again later if
            # the grades keep supporting it; a removal is a decision
            # about the rule, not a dismissal of the pattern.
            return removed
    return None


def dismiss_suggestion(store: dict, pattern_key: str, support_count: int) -> None:
    """Record that a suggestion was passed over at this support count."""
    store.setdefault("dismissed", {})[pattern_key] = int(support_count)


def active_rule_statements(store: dict) -> list[str]:
    """The statements of the rules currently in force, in save order."""
    return [
        str(rule["statement"])
        for rule in store.get("rules", [])
        if rule.get("active", True) and rule.get("statement")
    ]
