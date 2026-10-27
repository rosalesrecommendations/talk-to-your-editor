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
            }
        )
    return suggestions


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
    rule = {
        "rule_id": "rule-" + re.sub(
            r"[^A-Za-z0-9_-]+", "-", str(suggestion["pattern_key"])
        ).strip("-").lower(),
        "pattern_key": suggestion["pattern_key"],
        "operation_type": suggestion.get("operation_type"),
        "statement": suggestion["statement"],
        "support_count": suggestion.get("support_count", 0),
        "source": "explicit" if suggestion.get("explicit") else "pattern",
        "active": True,
        "created_at": _now(),
    }
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
