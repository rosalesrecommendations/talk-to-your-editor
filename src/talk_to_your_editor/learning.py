"""Pattern learning for Talk to Your Editor.

Reads graded correction records (the export of the after-review page,
optionally enriched with pattern keys and before/after transcript excerpts),
groups repeated patterns, and writes a creator-template DRAFT whose candidate
rules are all marked ``approved: false``. A draft is a proposal only: this
module never modifies an active template, and it refuses to write a draft
over an active template path.

Learning here is inference over records, not model training. Hosted models
are not fine-tuned by this code and no model weights change. The draft says
so in its own ``method_note`` field.

CLI:
    python -m talk_to_your_editor.learning GRADED_JSON --out DRAFT_JSON \
        [--active-template ACTIVE_JSON] [--min-support 2]

    python -m talk_to_your_editor.learning --pairs PAIRS_JSON --out DRAFT_JSON
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Optional

SUPPORT_VERDICTS = frozenset({"worked", "partly"})
DEFAULT_MIN_SUPPORT = 2

METHOD_NOTE = (
    "Produced by inference over the supplied records. This is inference, "
    "not model training: no model was trained or fine-tuned and no model "
    "weights were changed. Every candidate is a proposal for the creator "
    "to approve or reject."
)


# ---------------------------------------------------------------------------
# Loading graded records
# ---------------------------------------------------------------------------

def _entries_from_payload(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    if isinstance(payload, dict):
        for key in ("entries", "records", "corrections"):
            value = payload.get(key)
            if isinstance(value, list):
                return [e for e in value if isinstance(e, dict)]
    return []


def load_graded_records(source: Any) -> list[dict]:
    """Load graded correction entries from a path or an in-memory payload."""
    if isinstance(source, (str, Path)):
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
    else:
        payload = source
    return _entries_from_payload(payload)


def record_id_for(entry: dict, position: int) -> str:
    for key in ("record_id", "id", "comment_id"):
        value = entry.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return f"record-{position + 1}"


def pattern_key_for(entry: dict) -> str:
    """The grouping key for a graded entry.

    Prefers an explicit ``pattern_key``; falls back to the operation type so
    unenriched exports still group by the kind of edit that was graded.
    """
    for key in ("pattern_key", "operation_type"):
        value = entry.get(key)
        if value and str(value).strip():
            return str(value).strip()
    return "unspecified_pattern"


# ---------------------------------------------------------------------------
# Pattern extraction from graded corrections
# ---------------------------------------------------------------------------

def extract_patterns(records: list[dict]) -> list[dict]:
    """Group graded entries by pattern and summarize support.

    Support counts only graded records whose verdict is ``worked`` or
    ``partly``. Records graded ``did_not_work`` (or left ungraded) never
    count toward support.
    """
    groups: dict[str, dict] = {}
    for position, entry in enumerate(records):
        key = pattern_key_for(entry)
        group = groups.setdefault(
            key,
            {
                "pattern_key": key,
                "operation_types": Counter(),
                "total_records": 0,
                "support_count": 0,
                "supporting_record_ids": [],
                "verdicts": [],
                "sample_feedback": [],
                "pattern_description": None,
                "why_choices": Counter(),
                "why_notes": [],
            },
        )
        record_id = record_id_for(entry, position)
        group["total_records"] += 1
        op_type = entry.get("operation_type")
        if op_type:
            group["operation_types"][str(op_type)] += 1
        why = entry.get("why")
        if isinstance(why, dict):
            choice = why.get("choice")
            if choice and str(choice).strip():
                group["why_choices"][str(choice)] += 1
            note = why.get("note")
            if (
                note
                and str(note).strip()
                and len(group["why_notes"]) < 3
            ):
                group["why_notes"].append(str(note))
        verdict = entry.get("verdict")
        if verdict:
            group["verdicts"].append(str(verdict))
        if verdict in SUPPORT_VERDICTS:
            group["support_count"] += 1
            group["supporting_record_ids"].append(record_id)
        feedback_text = entry.get("original_text") or entry.get("feedback_text") or entry.get("text")
        if feedback_text and len(group["sample_feedback"]) < 3:
            group["sample_feedback"].append(str(feedback_text))
        if group["pattern_description"] is None and entry.get("pattern_description"):
            group["pattern_description"] = str(entry["pattern_description"])

    patterns = []
    for group in groups.values():
        op_types = group.pop("operation_types")
        group["operation_type"] = op_types.most_common(1)[0][0] if op_types else None
        group["why_choices"] = dict(group["why_choices"])
        patterns.append(group)
    patterns.sort(key=lambda g: (-g["support_count"], g["pattern_key"]))
    return patterns


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(text)).strip("-").lower()
    return slug or "pattern"


def build_template_draft(
    records: list[dict],
    creator: str = "Sample Creator",
    min_support: int = DEFAULT_MIN_SUPPORT,
) -> dict:
    """Build a creator-template draft from graded correction records.

    Only patterns backed by at least ``min_support`` graded records with a
    verdict of worked or partly become candidate rules. Every candidate is
    marked ``approved: false``; a person must approve it before it can ever
    enter an active template.
    """
    patterns = extract_patterns(records)
    candidates = []
    for pattern in patterns:
        if pattern["support_count"] < min_support:
            continue
        statement = pattern.get("pattern_description")
        if not statement:
            example = pattern["sample_feedback"][0] if pattern["sample_feedback"] else ""
            statement = (
                f"When feedback like \"{example}\" appears, apply "
                f"{pattern['operation_type'] or 'the same edit'} the same way."
            )
        candidates.append(
            {
                "rule_id": "candidate-" + _slug(pattern["pattern_key"]),
                "pattern_key": pattern["pattern_key"],
                "operation_type": pattern["operation_type"],
                "statement": statement,
                "support_count": pattern["support_count"],
                "supporting_record_ids": list(pattern["supporting_record_ids"]),
                "verdicts": list(pattern["verdicts"]),
                "sample_feedback": list(pattern["sample_feedback"]),
                "approved": False,
            }
        )
    return {
        "kind": "creator_template_draft",
        "draft": True,
        "creator": creator,
        "method": "inference",
        "method_note": METHOD_NOTE,
        "min_support": min_support,
        "generated_from_record_ids": [
            record_id_for(entry, i) for i, entry in enumerate(records)
        ],
        "candidate_rules": candidates,
        "patterns_considered": [
            {
                "pattern_key": p["pattern_key"],
                "support_count": p["support_count"],
                "total_records": p["total_records"],
                "became_candidate": p["support_count"] >= min_support,
            }
            for p in patterns
        ],
        "active_template_modified": False,
    }


# ---------------------------------------------------------------------------
# Inference-based template draft from raw-to-finished pair summaries
# ---------------------------------------------------------------------------

def _numeric_candidates(summaries: list[dict], section: str) -> dict:
    """Average numeric fields of a summary section across pair summaries."""
    values: dict[str, list[float]] = {}
    evidence: dict[str, list[str]] = {}
    for position, summary in enumerate(summaries):
        record_id = record_id_for(summary, position)
        fields = summary.get(section)
        if not isinstance(fields, dict):
            continue
        for name, value in fields.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.setdefault(name, []).append(float(value))
                evidence.setdefault(name, []).append(record_id)
    return {
        name: {
            "value": round(sum(vals) / len(vals), 3),
            "observations": len(vals),
            "evidence_record_ids": evidence[name],
            "approved": False,
        }
        for name, vals in sorted(values.items())
    }


def _structure_candidates(summaries: list[dict]) -> dict:
    """Collect the most common categorical structure values across pairs."""
    counts: dict[str, Counter] = {}
    evidence: dict[str, dict[str, list[str]]] = {}
    for position, summary in enumerate(summaries):
        record_id = record_id_for(summary, position)
        structure = summary.get("structure")
        if not isinstance(structure, dict):
            continue
        for name, value in structure.items():
            if isinstance(value, str):
                display = value
            elif isinstance(value, list):
                display = " > ".join(str(v) for v in value)
            else:
                continue
            counts.setdefault(name, Counter())[display] += 1
            evidence.setdefault(name, {}).setdefault(display, []).append(record_id)
    candidates = {}
    for name, counter in sorted(counts.items()):
        value, count = counter.most_common(1)[0]
        candidates[name] = {
            "value": value,
            "observations": count,
            "evidence_record_ids": evidence[name][value],
            "approved": False,
        }
    return candidates


def _pair_rule_candidates(summaries: list[dict]) -> list[dict]:
    """Group rule suggestions embedded in pair summaries by statement."""
    groups: dict[str, dict] = {}
    for position, summary in enumerate(summaries):
        record_id = record_id_for(summary, position)
        for rule in summary.get("candidate_rules") or []:
            if isinstance(rule, dict):
                statement = str(rule.get("statement") or rule.get("text") or "").strip()
                pattern_key = rule.get("pattern_key") or "pair_inference"
                op_type = rule.get("operation_type")
            else:
                statement = str(rule).strip()
                pattern_key = "pair_inference"
                op_type = None
            if not statement:
                continue
            group = groups.setdefault(
                statement,
                {
                    "rule_id": "candidate-" + _slug(str(pattern_key)),
                    "pattern_key": pattern_key,
                    "operation_type": op_type,
                    "statement": statement,
                    "support_count": 0,
                    "supporting_record_ids": [],
                    "approved": False,
                },
            )
            group["support_count"] += 1
            group["supporting_record_ids"].append(record_id)
    return sorted(groups.values(), key=lambda g: (-g["support_count"], g["statement"]))


def infer_template_from_pairs(pair_summaries: list[dict]) -> dict:
    """Draft a creator template by inference from pair summaries.

    ``pair_summaries`` are structured summaries of raw-to-finished video
    pairs (never the raw private video itself): cadence numbers, structure
    fields, and rule suggestions, each tied to a record id so every
    candidate carries its evidence. This is inference over supplied
    summaries, not model training.
    """
    summaries = [s for s in pair_summaries if isinstance(s, dict)]
    return {
        "kind": "creator_template_draft",
        "draft": True,
        "creator": "Sample Creator",
        "method": "inference",
        "method_note": METHOD_NOTE,
        "evidence_record_ids": [
            record_id_for(summary, i) for i, summary in enumerate(summaries)
        ],
        "cadence_candidates": _numeric_candidates(summaries, "cadence"),
        "structure_candidates": _structure_candidates(summaries),
        "candidate_rules": _pair_rule_candidates(summaries),
        "active_template_modified": False,
    }


# ---------------------------------------------------------------------------
# Writing drafts (never mutates an active template)
# ---------------------------------------------------------------------------

def _file_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_template_draft(
    draft: dict,
    output_path: Path | str,
    active_template_path: Optional[Path | str] = None,
) -> Path:
    """Write a template draft to ``output_path``.

    Raises ``ValueError`` if the output would land on the active template,
    and verifies the active template file is byte-unchanged afterwards.
    """
    out = Path(output_path)
    active = Path(active_template_path) if active_template_path else None
    if active is not None and out.resolve() == active.resolve():
        raise ValueError(
            "Refusing to write a template draft over the active template. "
            "Drafts are proposals; the active template only changes by a "
            "separate, approved step."
        )
    before = _file_digest(active) if active is not None and active.exists() else None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(draft, indent=2) + "\n", encoding="utf-8")
    if before is not None and _file_digest(active) != before:
        raise RuntimeError("Active template changed while writing the draft.")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="talk_to_your_editor.learning",
        description="Draft candidate creator rules from graded corrections "
        "or from raw-to-finished pair summaries. Never edits an active template.",
    )
    parser.add_argument(
        "graded",
        nargs="?",
        help="Path to a graded-corrections JSON file.",
    )
    parser.add_argument(
        "--pairs",
        default=None,
        help="Path to a JSON list of raw-to-finished pair summaries "
        "(inference mode; alternative to a graded file).",
    )
    parser.add_argument("--out", required=True, help="Where to write the template draft JSON.")
    parser.add_argument(
        "--active-template",
        default=None,
        help="Path to the active template, guarded against modification.",
    )
    parser.add_argument("--creator", default="Sample Creator")
    parser.add_argument("--min-support", type=int, default=DEFAULT_MIN_SUPPORT)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.pairs:
        payload = json.loads(Path(args.pairs).read_text(encoding="utf-8"))
        summaries = _entries_from_payload(payload) if not isinstance(payload, list) else payload
        draft = infer_template_from_pairs(summaries)
    elif args.graded:
        records = load_graded_records(args.graded)
        draft = build_template_draft(
            records, creator=args.creator, min_support=args.min_support
        )
    else:
        parser.error("Provide a graded-corrections file or --pairs.")

    out = write_template_draft(
        draft, args.out, active_template_path=args.active_template
    )
    print(
        f"Wrote template draft: {out} "
        f"({len(draft.get('candidate_rules', []))} candidate rule(s), all unapproved)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
