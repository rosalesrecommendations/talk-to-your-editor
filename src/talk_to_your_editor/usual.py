"""The creator's usual: front door choices that carry across jobs.

The front door's choices used to live only in one job's session, so
every new job started from the built-in defaults and the creator set
the same switches again. The usual is the creator-level copy of
those choices, kept in the app's own home folder
(``~/.talk-to-your-editor/usual.json``), beside the saved sign-in
keys. It sits between the built-in defaults and a job's own session
choices in precedence: defaults first, then the usual, then the
session, so what a creator settled on the last job is how the next
job starts, and a choice made inside a job still wins for that job.

The usual is a convenience, never a gate: a missing or damaged file
just means the defaults, and a failed save never stops an edit.
Only the front door keys are stored, each cleaned the same way
the front door itself cleans them, so a hand-edited file can never
smuggle in a value the controls themselves would refuse.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

USUAL_KEYS = (
    "remove_retakes",
    "cut_silence",
    "buffer_s",
    "keep_mode",
    "breaths",
    "remove_fillers",
    "mode",
)

_BOOL_KEYS = ("remove_retakes", "cut_silence", "remove_fillers")
_KEEP_MODES = ("last", "first", "best")
_BREATH_MODES = ("keep", "trim")
_INVOLVEMENT_MODES = ("cleanest", "tailor")
BUFFER_MIN_S = 0.05
BUFFER_MAX_S = 0.50

# The fallbacks behind the fallbacks: the front door's own defaults,
# used when a caller hands in defaults that are themselves missing
# or unusable for a key.
_FALLBACKS = {
    "remove_retakes": True,
    "cut_silence": True,
    "buffer_s": 0.1,
    "keep_mode": "last",
    "breaths": "keep",
    "remove_fillers": True,
    "mode": "cleanest",
}

USUAL_DIR_NAME = ".talk-to-your-editor"
USUAL_FILENAME = "usual.json"


def _usual_path() -> Path:
    """The usual's file, resolved at call time.

    Resolving through ``Path.home()`` on every call (never caching
    the path at import) is what lets a test point the home folder
    somewhere temporary and exercise the real load and save.
    """
    return Path.home() / USUAL_DIR_NAME / USUAL_FILENAME


def _clean_value(key: str, value, fallback):
    """One usual value, cleaned; ``fallback`` when it does not hold up.

    Booleans must really be booleans (a truthy word in a hand-edited
    file is not a choice the creator made), the buffer is a number
    clamped to the front door's own range, and the two word choices
    must be one of their known words.
    """
    if key in _BOOL_KEYS:
        return value if isinstance(value, bool) else fallback
    if key == "buffer_s":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return min(BUFFER_MAX_S, max(BUFFER_MIN_S, float(value)))
        return fallback
    if key == "keep_mode":
        return value if value in _KEEP_MODES else fallback
    if key == "breaths":
        return value if value in _BREATH_MODES else fallback
    if key == "mode":
        return value if value in _INVOLVEMENT_MODES else fallback
    return fallback


def _fallback_for(key: str, defaults: dict):
    """The default one key falls back to: the caller's, cleaned."""
    return _clean_value(key, defaults.get(key), _FALLBACKS[key])


def load_usual(defaults: dict) -> dict:
    """The usual choices laid over ``defaults``.

    Starts from a copy of ``defaults`` and overlays the file's
    values for the usual keys, each cleaned against the default
    it would replace. A missing or unreadable file, or one that does
    not parse to a JSON object, returns the defaults copy untouched.
    Never raises: the usual can only ever be a starting point.
    """
    result = dict(defaults)
    try:
        payload = json.loads(_usual_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    if not isinstance(payload, dict):
        return result
    for key in USUAL_KEYS:
        if key in payload:
            result[key] = _clean_value(
                key, payload[key], _fallback_for(key, defaults)
            )
    return result


def save_usual(settings: dict) -> None:
    """Save the usual keys from ``settings``, cleaned.

    Writes to a temp file in the same folder and replaces the real
    file with it, so a stopped save can never leave a half-written
    usual behind. The folder is made private (0o700) and the file
    private (0o600), the same way the app keeps its saved keys.
    Never raises on a filesystem problem: an edit that just happened
    matters more than whether its choices were remembered.
    """
    cleaned = {
        key: _clean_value(
            key, settings.get(key), _fallback_for(key, settings)
        )
        for key in USUAL_KEYS
    }
    path = _usual_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        tmp = path.parent / (path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(cleaned, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass
