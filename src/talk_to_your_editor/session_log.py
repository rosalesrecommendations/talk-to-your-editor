"""The session's step-by-step log (v0.10, FIX B).

Every stage of work on a job writes lines to
``tye-session/session.log`` as it goes: when it starts, when it
finishes, when it fails, each with the time and how long it
took. The log is what the Problem report button sends back, so
a failure on the creator's machine can be read instead of
guessed at.

One JSON object per line: ``{"at", "stage", "event", "detail"}``.
Writing a line never fails the stage that wrote it: every call
is wrapped so a full disk or a missing folder costs the log
line, never the edit.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

SESSION_LOG_NAME = "session.log"

_STARTS: dict = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_stage(session_dir, stage: str, event: str, detail: str = "") -> None:
    """Append one line to the session log. Never raises.

    ``session_dir`` is the job's ``tye-session`` folder (a Path
    or a string). ``event`` is "start", "finish", or "fail"; a
    finish or fail line carries the seconds since the matching
    start when one was logged in this process.
    """
    try:
        if session_dir is None:
            return
        folder = Path(session_dir)
        key = (str(folder), stage)
        entry = {"at": _now(), "stage": stage, "event": event}
        if event == "start":
            _STARTS[key] = time.monotonic()
        else:
            started = _STARTS.pop(key, None)
            if started is not None:
                entry["seconds"] = round(time.monotonic() - started, 2)
        if detail:
            entry["detail"] = str(detail)[:300]
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / SESSION_LOG_NAME, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def read_log(session_dir) -> str:
    """The log's text, or "" when there is none."""
    try:
        path = Path(session_dir) / SESSION_LOG_NAME
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return ""
