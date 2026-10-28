"""Saved transcripts: a video written down once is not paid for twice.

When intake writes down a video's words, the result (the words
with their timings, and which engine made them) is saved on this
computer under ``~/.talk-to-your-editor/saved-transcripts/``, one
small JSON file per video. Bringing the same video in again, in
any later session, finds the saved copy and uses it instead of
calling ElevenLabs Scribe or the speech pack on this computer
again.

A video is known by what it is, not by its name: the pair of its
file size and a SHA-256 hash of the whole file. The same video
under a new name matches; a different video that happens to
share a name does not. Anything uncertain (the file cannot be
read, the saved copy is damaged, the saved identity does not
match the file in hand) answers "no saved copy", and the caller
writes the words down fresh and says nothing extra.

The saved copy carries exactly the shape a fresh transcription
returns (``words``, ``segments``, ``language``, ``duration``,
``engine``), so a reused transcript flows through the same
downstream path as a fresh one, unchanged.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

APP_DIR_NAME = ".talk-to-your-editor"
STORE_DIR_NAME = "saved-transcripts"
STORE_VERSION = 1
_HASH_CHUNK = 1024 * 1024

# The intake recap lines for a reuse, in plain words. The Scribe
# line is the one the fix item names; the Whisper line names the
# engine that made the saved copy, the way the item asks.
SCRIBE_REUSE_LINE = (
    "Used the transcript saved from the earlier run of this "
    "video. No new transcription cost."
)
LOCAL_REUSE_LINE = (
    "Used the transcript saved from the earlier run of this "
    "video. It was made with Whisper on this computer. No new "
    "transcription cost."
)


def store_dir(store_root: Optional[Path] = None) -> Path:
    """The saved-transcripts folder, resolved at call time.

    Resolving through ``Path.home()`` on every call (never
    caching) means a test can point the home folder somewhere
    temporary and exercise the real paths. ``store_root`` lets a
    caller name the folder outright.
    """
    if store_root is not None:
        return Path(store_root)
    return Path.home() / APP_DIR_NAME / STORE_DIR_NAME


def identity_for_file(path) -> Optional[dict]:
    """The content identity of one file: its size and its hash.

    Returns None when the file cannot be read in full: identity
    is uncertain then, and the caller must not reuse or save.
    """
    try:
        target = Path(path)
        size = target.stat().st_size
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            while True:
                chunk = handle.read(_HASH_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return None
    if size <= 0:
        return None
    return {"size": int(size), "sha256": digest.hexdigest()}


def _clean_identity(identity) -> Optional[dict]:
    if not isinstance(identity, dict):
        return None
    size = identity.get("size")
    sha = identity.get("sha256")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return None
    if not isinstance(sha, str) or len(sha) != 64:
        return None
    return {"size": size, "sha256": sha}


def _entry_path(identity: dict, store_root: Optional[Path] = None) -> Path:
    name = f"{identity['size']}-{identity['sha256']}.json"
    return store_dir(store_root) / name


def _clean_words(words) -> Optional[list]:
    """Words in the fresh-transcription shape, or None.

    Every word needs its text and its start and end times as
    numbers; anything else makes the saved copy unusable, which
    the caller treats as no saved copy at all.
    """
    if not isinstance(words, list) or not words:
        return None
    cleaned = []
    for item in words:
        if not isinstance(item, dict):
            return None
        text = item.get("word")
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            return None
        if not isinstance(text, str) or not text:
            return None
        cleaned.append({"word": text, "start": start, "end": end})
    return cleaned


def _result_from_payload(payload) -> Optional[dict]:
    """A saved payload back into the fresh result shape, or None."""
    if not isinstance(payload, dict):
        return None
    words = _clean_words(payload.get("words"))
    engine = payload.get("engine")
    if words is None or not isinstance(engine, str) or not engine:
        return None
    segments = payload.get("segments")
    if not isinstance(segments, list):
        # Segments are a reading aid rebuilt from the words; the
        # words themselves are the saved truth.
        from .transcribe import segments_from_words

        segments = segments_from_words(words)
    try:
        duration = float(payload.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0.0:
        duration = float(words[-1]["end"])
    return {
        "words": words,
        "segments": segments,
        "language": str(payload.get("language") or "en"),
        "duration": duration,
        "engine": engine,
    }


def load_saved(identity, store_root: Optional[Path] = None) -> Optional[dict]:
    """The saved transcript for one identity, or None.

    None covers every uncertain case alike: no file, a file that
    does not parse, a stored identity that does not match the
    identity asked for, or words that are not in shape. The
    caller then transcribes fresh and says nothing extra.
    """
    identity = _clean_identity(identity)
    if identity is None:
        return None
    try:
        payload = json.loads(
            _entry_path(identity, store_root).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if _clean_identity(payload.get("identity")) != identity:
        return None
    return _result_from_payload(payload)


def save_transcript(identity, result: dict,
                    store_root: Optional[Path] = None) -> bool:
    """Save one fresh transcription result under its identity.

    Returns True when the copy is on disk. A save that cannot be
    made returns False and never raises: losing the saved copy
    loses a future saving, never the edit in hand.
    """
    identity = _clean_identity(identity)
    if identity is None or not isinstance(result, dict):
        return False
    cleaned = _result_from_payload(
        {
            "words": result.get("words"),
            "segments": result.get("segments"),
            "language": result.get("language"),
            "duration": result.get("duration"),
            "engine": result.get("engine"),
        }
    )
    if cleaned is None:
        return False
    payload = {
        "version": STORE_VERSION,
        "identity": identity,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        **cleaned,
    }
    folder = store_dir(store_root)
    target = _entry_path(identity, store_root)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        scratch = target.with_name(target.name + ".building")
        scratch.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        scratch.replace(target)
    except OSError:
        return False
    return True


def count_saved(store_root: Optional[Path] = None) -> int:
    """How many transcripts are saved right now."""
    try:
        return len(list(store_dir(store_root).glob("*.json")))
    except OSError:
        return 0


def clear_saved(store_root: Optional[Path] = None) -> int:
    """Remove every saved transcript; returns how many went."""
    folder = store_dir(store_root)
    removed = 0
    try:
        entries = list(folder.glob("*.json"))
    except OSError:
        return 0
    for entry in entries:
        try:
            entry.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def is_local_engine(engine) -> bool:
    """Whether an engine string names the on-this-computer route."""
    text = str(engine or "").lower()
    return "whisper" in text or "on this computer" in text


def reuse_recap_line(engine) -> str:
    """The one plain recap line that says a saved copy was used."""
    if is_local_engine(engine):
        return LOCAL_REUSE_LINE
    return SCRIBE_REUSE_LINE
