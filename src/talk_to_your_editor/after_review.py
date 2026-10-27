"""After-review page generation for Talk to Your Editor.

Takes the creator's original structured feedback JSON plus the operations /
audit JSON produced by the direction layer, and renders a self-contained
after-review HTML page. Every input comment appears exactly once, with its
timestamp, original text, the outcome status (applied / flagged / rejected),
a plain-language description of what was done, and controls for the creator
to grade the change (worked / partly / did not work) and add notes.

The page is fully self-contained: no network references. Grades save locally
in the browser via localStorage and export as a graded-corrections JSON file
whose shape matches ``examples/graded-corrections.sample.json``.

This module works on plain JSON-compatible dicts only. It deliberately does
not import any other package module, so it can be built and tested on its own.

CLI:
    python -m talk_to_your_editor.after_review FEEDBACK_JSON OPERATIONS_JSON \
        --out AFTER.html [--video revised-review.mp4] [--template TEMPLATE.html]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

STATUSES = ("applied", "flagged", "rejected")
_STATUS_PRIORITY = {"applied": 3, "rejected": 2, "flagged": 1}
_STATUS_LABELS = {
    "applied": "Applied",
    "flagged": "Flagged for review",
    "rejected": "Rejected",
}
_OPERATION_ACTION_TEMPLATES = {
    "cut_words": "Removed the words this note pointed at from the finished edit.",
    "restore_words": "Restored words that had been cut, as this note asked.",
    "replace_take": "Swapped the take used here for the other take, as this note asked.",
    "set_join_pause": "Changed the pause at this join to the length this note asked for.",
    "flag_for_review": "No change was made. A person needs to decide this one.",
}


# ---------------------------------------------------------------------------
# Input normalization helpers
# ---------------------------------------------------------------------------

def _comments_from_feedback(feedback: Any) -> list[dict]:
    """Return the comment list from a feedback payload of any known shape."""
    if isinstance(feedback, list):
        return [c for c in feedback if isinstance(c, dict)]
    if isinstance(feedback, dict):
        comments = feedback.get("comments")
        if isinstance(comments, list):
            return [c for c in comments if isinstance(c, dict)]
    return []


def _operations_from_payload(operations: Any) -> list[dict]:
    """Return the operation list from an operations/audit payload."""
    if isinstance(operations, list):
        return [op for op in operations if isinstance(op, dict)]
    if isinstance(operations, dict):
        for key in ("operations", "audit", "results"):
            value = operations.get(key)
            if isinstance(value, list):
                return [op for op in value if isinstance(op, dict)]
    return []


def normalize_status(raw: Any) -> str:
    """Map a free-form operation status onto applied / flagged / rejected."""
    text = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text.startswith("applied") or text in {"apply", "done", "ok"}:
        return "applied"
    if text.startswith("reject") or text in {"denied", "refused"}:
        return "rejected"
    if text.startswith("flag") or text in {"flag_for_review", "needs_review", "skipped"}:
        return "flagged"
    return "flagged"


def _operation_comment_id(op: dict) -> Optional[str]:
    for key in ("comment_id", "commentId", "comment", "id"):
        value = op.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def index_operations(operations: Any) -> dict[str, dict]:
    """Index operations by originating comment id.

    When several operations trace back to one comment, the strongest outcome
    wins (applied over rejected over flagged) so the row reflects the best
    result the comment achieved.
    """
    index: dict[str, dict] = {}
    for op in _operations_from_payload(operations):
        comment_id = _operation_comment_id(op)
        if comment_id is None:
            continue
        status = normalize_status(op.get("status") or op.get("outcome"))
        existing = index.get(comment_id)
        if existing is None or _STATUS_PRIORITY[status] > _STATUS_PRIORITY[existing["_status"]]:
            entry = dict(op)
            entry["_status"] = status
            index[comment_id] = entry
    return index


# A provider's raw ``reason`` is internal text: it can carry the
# provider's own name as a prefix ("mock: ..."), word-index jargon
# ("at word 16"), or a machine code in snake_case. Only a reason that
# survives cleaning may be shown; anything else falls back to the
# per-type template, which is plain by construction.
_PROVIDER_PREFIX = re.compile(r"^(?:mock|meta|spark|wonder)\s*:\s*", re.IGNORECASE)
_CODE_TOKEN = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_INDEX_JARGON = re.compile(r"\b(?:word|join)\s+\d+\b", re.IGNORECASE)


def _clean_reason(value: Any) -> Optional[str]:
    """A provider reason made safe to show, or None when it is not.

    Strips a leading provider-name prefix, then refuses anything
    still carrying a machine code or a word/join index reference.
    """
    text = str(value or "").strip()
    text = _PROVIDER_PREFIX.sub("", text).strip()
    if not text:
        return None
    if _CODE_TOKEN.search(text) or _INDEX_JARGON.search(text):
        return None
    text = text[0].upper() + text[1:]
    if not text.endswith((".", "!", "?")):
        text += "."
    return text


def _plain_action(op: Optional[dict], status: str) -> str:
    """Plain-language description of what happened for one comment."""
    if op is not None:
        for key in ("plain_action", "action_taken", "summary", "action"):
            value = op.get(key)
            if value and str(value).strip():
                return str(value).strip()
        cleaned = _clean_reason(op.get("reason"))
        if cleaned:
            return cleaned
        template = _OPERATION_ACTION_TEMPLATES.get(str(op.get("type") or ""))
        if template:
            return template
    if status == "applied":
        return "The change this note asked for was applied to the revised edit."
    if status == "rejected":
        return (
            "The change was checked and rejected by the safety verifier, so the "
            "revised edit keeps the original here."
        )
    return (
        "No automatic change was made for this note. It is flagged so a person "
        "can decide what to do."
    )


def format_timestamp(seconds: Any) -> str:
    """Format a seconds value as m:ss (or h:mm:ss past an hour)."""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "0:00"
    total = max(total, 0)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# ---------------------------------------------------------------------------
# Row and page rendering
# ---------------------------------------------------------------------------

_VERDICT_OPTIONS = (
    ("worked", "Worked"),
    ("partly", "Partly"),
    ("did_not_work", "Did not work"),
)


def render_comment_row(comment: dict, op: Optional[dict]) -> str:
    """Render one comment row. The comment text is emitted exactly once."""
    comment_id = str(comment.get("id") or comment.get("comment_id") or "")
    ts_value = comment.get("ts")
    if ts_value is None:
        ts_value = comment.get("timestamp", 0)
    # ``fmt`` in a feedback export names the format the comment belongs to
    # (``vertical`` or ``landscape``), never the timestamp. The visible time
    # always comes from the numeric ``ts`` value.
    ts_display = format_timestamp(ts_value)
    raw_fmt = str(comment.get("fmt") or "").strip().lower()
    format_badge = ""
    if raw_fmt in {"vertical", "landscape"}:
        format_badge = f'\n        <span class="badge">{raw_fmt}</span>'
    try:
        ts_number = float(ts_value)
    except (TypeError, ValueError):
        ts_number = 0.0
    text = str(comment.get("text") or comment.get("comment") or "")

    status = "flagged" if op is None else op["_status"]
    op_type = "" if op is None else str(op.get("type") or "")
    action = _plain_action(op, status)

    radios = "\n          ".join(
        '<label><input type="radio" name="verdict-{cid}" value="{value}"> {label}</label>'.format(
            cid=html.escape(comment_id, quote=True), value=value, label=label
        )
        for value, label in _VERDICT_OPTIONS
    )
    return (
        '<article class="comment" data-comment-id="{cid}" data-ts="{ts}" '
        'data-status="{status}" data-operation-type="{op_type}">\n'
        "      <header>\n"
        '        <span class="ts">{ts_display}</span>\n'
        '        <span class="badge {status}">{status_label}</span>{format_badge}\n'
        "      </header>\n"
        '      <p class="comment-text">{text}</p>\n'
        '      <p class="action"><span class="action-label">What happened:</span> '
        '<span class="action-text">{action}</span></p>\n'
        '      <fieldset class="verdicts">\n'
        "        <legend>Did the change work?</legend>\n"
        '        <div class="verdict-options">\n'
        "          {radios}\n"
        "        </div>\n"
        "      </fieldset>\n"
        '      <label class="notes-label">Notes\n'
        '        <textarea class="notes" rows="2" placeholder="Anything the editor should know"></textarea>\n'
        "      </label>\n"
        "    </article>"
    ).format(
        cid=html.escape(comment_id, quote=True),
        ts=ts_number,
        status=status,
        op_type=html.escape(op_type, quote=True),
        ts_display=html.escape(str(ts_display)),
        format_badge=format_badge,
        status_label=_STATUS_LABELS[status],
        text=html.escape(text),
        action=html.escape(action),
        radios=radios,
    )


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(text or "video")).strip("-")
    return slug or "video"


def default_template_path() -> Path:
    return Path(__file__).resolve().parent / "templates" / "after_review_template.html"


def load_template(template_path: Optional[Path] = None) -> str:
    path = Path(template_path) if template_path else default_template_path()
    return path.read_text(encoding="utf-8")


def render_after_review(
    feedback: Any,
    operations: Any,
    video_filename: Optional[str] = None,
    template_text: Optional[str] = None,
) -> str:
    """Render the complete after-review HTML page as a string.

    ``feedback`` is the original feedback payload, ``operations`` the
    operations/audit payload. ``video_filename`` names a local video file
    sitting next to the page; only the filename is referenced, the media is
    never embedded.
    """
    template = template_text if template_text is not None else load_template()
    comments = _comments_from_feedback(feedback)
    op_index = index_operations(operations)

    video_name = ""
    if isinstance(feedback, dict):
        video_name = str(feedback.get("video") or "")

    rows_html = "\n".join(
        render_comment_row(
            comment,
            op_index.get(str(comment.get("id") or comment.get("comment_id") or "")),
        )
        for comment in comments
    )

    video_block = ""
    video_ref = ""
    if video_filename:
        video_ref = Path(str(video_filename)).name
        video_block = (
            '<video controls preload="metadata" src="{src}"></video>'.format(
                src=html.escape(video_ref, quote=True)
            )
        )

    display_name = video_name or video_ref or "your video"
    storage_key = "tty-after-review:" + _slug(display_name)

    page = template
    page = page.replace("{{VIDEO_NAME}}", html.escape(display_name))
    page = page.replace("{{VIDEO_BLOCK}}", video_block)
    page = page.replace("{{ROWS}}", rows_html)
    page = page.replace("{{VIDEO_JSON}}", json.dumps(display_name))
    page = page.replace("{{STORAGE_KEY_JSON}}", json.dumps(storage_key))
    return page


def generate_after_review(
    feedback_path: Path | str,
    operations_path: Path | str,
    output_path: Path | str,
    video_filename: Optional[str] = None,
    template_path: Optional[Path] = None,
) -> Path:
    """Load JSON inputs, render the page, and write it to ``output_path``."""
    feedback = json.loads(Path(feedback_path).read_text(encoding="utf-8"))
    operations = json.loads(Path(operations_path).read_text(encoding="utf-8"))
    template_text = load_template(template_path) if template_path else None
    page = render_after_review(
        feedback,
        operations,
        video_filename=video_filename,
        template_text=template_text,
    )
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="talk_to_your_editor.after_review",
        description="Generate the after-review page for one feedback file.",
    )
    parser.add_argument("feedback", help="Path to the original feedback JSON.")
    parser.add_argument("operations", help="Path to the operations / audit JSON.")
    parser.add_argument("--out", required=True, help="Where to write the HTML page.")
    parser.add_argument(
        "--video",
        default=None,
        help="Optional local video filename to reference in the player.",
    )
    parser.add_argument(
        "--template",
        default=None,
        help="Optional alternate template HTML path.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    out = generate_after_review(
        args.feedback,
        args.operations,
        args.out,
        video_filename=args.video,
        template_path=Path(args.template) if args.template else None,
    )
    print(f"Wrote after-review page: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
