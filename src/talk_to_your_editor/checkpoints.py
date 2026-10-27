"""Session checkpoints: undo and redo for a test-app session.

Every change a session makes (saving notes, a direction run, a
transcript word toggle, a word correction) is recorded as a
checkpoint: a copy of the session's state files taken right after
the change landed. The state before the first change is kept as the
starting checkpoint, so the session can always walk back to where it
began. Undo and redo move a pointer through the checkpoints and
restore the files; a new change made after walking back replaces
the road not taken, the way every editor's undo history works.

Nothing here ever touches the job's own files. Checkpoints live in
``<job-dir>/tye-session/history/`` and only ever copy the session's
own state files.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from .word_corrections import CORRECTIONS_FILENAME

# Session files owned by the v0.4 surfaces: the front door's saved
# choices, the notes left on individual edits, the grades the app has
# received, and the creator's saved rules. They live beside the other
# session state and are checkpointed with it, so undo covers them too.
FRONT_DOOR_FILENAME = "front_door.json"
EDIT_NOTES_FILENAME = "edit_notes.json"
GRADES_FILENAME = "grades.json"
CREATOR_RULES_FILENAME = "creator_rules.json"
RETAKE_PASS_FILENAME = "retake_pass.json"
GOLD_FILENAME = "gold.json"

# The session files that together are the state of an edit in
# progress. The after-review page and the rendered video are derived
# from these, so they are regenerated, never checkpointed.
STATE_FILES = (
    "feedback.json",
    "revised_cuts.json",
    "operations.json",
    "operations_audit.json",
    CORRECTIONS_FILENAME,
    FRONT_DOOR_FILENAME,
    EDIT_NOTES_FILENAME,
    GRADES_FILENAME,
    CREATOR_RULES_FILENAME,
    RETAKE_PASS_FILENAME,
    GOLD_FILENAME,
)
HISTORY_DIR_NAME = "history"
META_FILENAME = "meta.json"
START_LABEL = "Session start"


def _history_dir(config) -> Path:
    return config.session_dir / HISTORY_DIR_NAME


def _slot_dir(config, index: int) -> Path:
    return _history_dir(config) / f"{index:04d}"


def _load_meta(config) -> Optional[dict]:
    path = _history_dir(config) / META_FILENAME
    if not path.is_file():
        return None
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(meta, dict)
        or not isinstance(meta.get("labels"), list)
        or not isinstance(meta.get("current"), int)
    ):
        return None
    return meta


def _save_meta(config, meta: dict) -> None:
    history = _history_dir(config)
    history.mkdir(parents=True, exist_ok=True)
    (history / META_FILENAME).write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )


def _snapshot(config, meta: dict, label: str) -> None:
    """Copy the session's current state files in as the next slot."""
    slot = _slot_dir(config, len(meta["labels"]))
    slot.mkdir(parents=True, exist_ok=True)
    for name in STATE_FILES:
        source = config.session_dir / name
        if source.is_file():
            shutil.copyfile(source, slot / name)
    meta["labels"].append(label)


def _restore(config, index: int) -> None:
    """Make the session's state files match checkpoint ``index``."""
    slot = _slot_dir(config, index)
    config.session_dir.mkdir(parents=True, exist_ok=True)
    for name in STATE_FILES:
        saved = slot / name
        target = config.session_dir / name
        if saved.is_file():
            shutil.copyfile(saved, target)
        else:
            target.unlink(missing_ok=True)


def begin(config) -> None:
    """Guarantee a starting checkpoint of the pre-change state.

    Mutation handlers call this before they change anything, so the
    state the session started from is on record before the first
    change lands. Later calls do nothing: the tip checkpoint is
    already the current state.
    """
    if _load_meta(config) is not None:
        return
    meta: dict = {"labels": [], "current": 0}
    _snapshot(config, meta, START_LABEL)
    _save_meta(config, meta)


def commit(config, label: str) -> dict:
    """Record the state a change just produced; returns the state."""
    begin(config)
    meta = _load_meta(config)
    assert meta is not None  # begin() just wrote it
    # A change after walking back replaces the road not taken.
    while len(meta["labels"]) - 1 > meta["current"]:
        index = len(meta["labels"]) - 1
        shutil.rmtree(_slot_dir(config, index), ignore_errors=True)
        meta["labels"].pop()
    _snapshot(config, meta, label)
    meta["current"] = len(meta["labels"]) - 1
    _save_meta(config, meta)
    return state(config)


def state(config) -> dict:
    """The checkpoint list and where the session currently stands."""
    meta = _load_meta(config)
    if meta is None:
        return {"checkpoints": [], "current": -1,
                "can_undo": False, "can_redo": False}
    labels = meta["labels"]
    current = meta["current"]
    return {
        "checkpoints": [
            {"index": index, "label": label}
            for index, label in enumerate(labels)
        ],
        "current": current,
        "can_undo": current > 0,
        "can_redo": 0 <= current < len(labels) - 1,
    }


def undo(config) -> Optional[dict]:
    """Step one checkpoint back; the new state, or None if at start."""
    meta = _load_meta(config)
    if meta is None or meta["current"] <= 0:
        return None
    meta["current"] -= 1
    _restore(config, meta["current"])
    _save_meta(config, meta)
    return state(config)


def redo(config) -> Optional[dict]:
    """Step one checkpoint forward; the new state, or None if at tip."""
    meta = _load_meta(config)
    if meta is None or meta["current"] >= len(meta["labels"]) - 1:
        return None
    meta["current"] += 1
    _restore(config, meta["current"])
    _save_meta(config, meta)
    return state(config)
