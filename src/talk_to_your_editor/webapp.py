"""Local web test app for Talk to Your Editor (v0.2).

Serves a one-job test page on the local network so the creator can watch
the finished edit on a phone, pin timestamped comments, run the direction
layer (mock or live Meta), and open the after-review page, all against one
runner job folder.

Job-dir contract: the job dir holds ``transcript.json`` and ``cuts.json``,
optionally ``profile.json``, ``feedback.json``, and any video files
(``.mp4``, ``.mov``, or ``.m4v``). A job folder can also be dropped
onto the page: the drop is staged as a copy under the starting
session's ``intake`` folder and becomes the current job, so no path
typing is needed; a lone dropped video is staged and served for
watching, with a plain note about what a full edit still needs. A video that is not already in a
browser-safe codec is served through a converted playback copy kept
in the session folder; the job's own files are never modified. Every
output of a test session lands in ``<job-dir>/tye-session/``; the
job's own ``transcript.json`` and ``cuts.json`` are never modified.

Standard library only. Run it with::

    tye serve --job-dir /path/to/job

then open the printed URL on this computer, or on a phone on the same
Wi-Fi.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import checkpoints, creator_rules, retakes
from .after_review import generate_after_review
from .applier import ApplyError, apply_verdicts
from .checkpoints import (
    CREATOR_RULES_FILENAME,
    EDIT_NOTES_FILENAME,
    FRONT_DOOR_FILENAME,
    GRADES_FILENAME,
    RETAKE_PASS_FILENAME,
)
from .cli import _load_json, _verify_and_apply, _write_json
from .meta_provider import MetaProvider, MockProvider, ProviderError
from .schema import MAX_JOIN_PAUSE_S, MIN_JOIN_PAUSE_S, SchemaError, validate_operations
from .timeline import Timeline
from .verifier import plain_reason_sentence, verify_operations
from .word_corrections import (
    CORRECTIONS_FILENAME,
    load_corrections,
    remove_correction,
    save_corrections,
    transcript_display_entries,
    transcript_with_corrections,
    upsert_correction,
)

def _mp4_dimensions(path: Path) -> Optional[tuple]:
    """(width, height) of the first video track in an MP4, or None.

    Pure-Python box walk (moov > trak > tkhd); the track header carries
    its display width and height as the last two 32-bit fixed-point
    values. Anything unreadable or not really an MP4 returns None, and
    the caller falls back to the filename.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None

    def boxes(start: int, end: int):
        offset = start
        while offset + 8 <= end:
            size = int.from_bytes(data[offset:offset + 4], "big")
            kind = data[offset + 4:offset + 8]
            header = 8
            if size == 1:
                if offset + 16 > end:
                    return
                size = int.from_bytes(data[offset + 8:offset + 16], "big")
                header = 16
            elif size == 0:
                size = end - offset
            if size < header or offset + size > end:
                return
            yield kind, offset + header, offset + size
            offset += size

    def find(start: int, end: int, kind: bytes):
        for k, payload_start, box_end in boxes(start, end):
            if k == kind:
                return payload_start, box_end
        return None

    moov = find(0, len(data), b"moov")
    if moov is None:
        return None
    for kind, trak_start, trak_end in boxes(*moov):
        if kind != b"trak":
            continue
        tkhd = find(trak_start, trak_end, b"tkhd")
        if tkhd is None or tkhd[1] - tkhd[0] < 8:
            continue
        width = int.from_bytes(data[tkhd[1] - 8:tkhd[1] - 4], "big") >> 16
        height = int.from_bytes(data[tkhd[1] - 4:tkhd[1]], "big") >> 16
        if width and height:
            return width, height
    return None


VIDEO_NAMES = ("review.mp4", "final.mp4", "final-long.mp4")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v")
SESSION_VIDEO_NAME = "revised-review.mp4"
PLAYBACK_DIR_NAME = "playback"
BROWSER_SAFE_CODEC = "h264"


def _job_videos(job_dir: Path) -> list[str]:
    """The job folder's video files, best first.

    Any file with a video extension counts, whatever its name or
    the case of its extension: a runner export called ``review.mp4``
    and an original dropped in as ``IMG_2383.MOV`` are both videos
    the creator may need to watch. The known export names keep
    their familiar order at the front; anything else follows,
    sorted by name.
    """
    try:
        names = [
            path.name
            for path in job_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in VIDEO_EXTENSIONS
        ]
    except OSError:
        return []
    preferred = [name for name in VIDEO_NAMES if name in names]
    rest = sorted(name for name in names if name not in VIDEO_NAMES)
    return preferred + rest


_CODEC_CACHE: dict = {}


def _probe_video_codec(path: Path) -> Optional[str]:
    """The first video stream's codec name, or None when unreadable.

    Probed with ffprobe when it is available; results are cached
    per file version. A file ffprobe cannot read at all (not really
    a video) also returns None, and is treated as not browser-safe.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    key = f"{path}|{stat.st_mtime_ns}|{stat.st_size}"
    if key in _CODEC_CACHE:
        return _CODEC_CACHE[key]
    codec: Optional[str] = None
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            out = subprocess.run(
                [
                    ffprobe, "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=codec_name",
                    "-of", "csv=p=0", str(path),
                ],
                capture_output=True, text=True, timeout=30,
            )
            lines = out.stdout.strip().splitlines()
            codec = lines[0].strip() if lines and lines[0].strip() else None
        except (OSError, subprocess.SubprocessError):
            codec = None
    _CODEC_CACHE[key] = codec
    return codec


def _playback_copy_path(config: "WebAppConfig", name: str) -> Path:
    """Where a video's converted playback copy lives in the session."""
    stem = Path(name).stem
    suffix = Path(name).suffix.lstrip(".").lower()
    return (
        config.session_dir
        / PLAYBACK_DIR_NAME
        / f"{stem}-{suffix}.playback.mp4"
    )


def _convert_playback_copy(source: Path, dest: Path) -> bool:
    """Build an H.264 copy of ``source`` at ``dest`` with ffmpeg.

    The copy is written to a scratch name and moved into place only
    when it is complete, so a stopped conversion never leaves a
    half-written file to serve. Returns False (with the detail in
    the server log) when no copy could be built.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print(
            f"[video] no ffmpeg available to convert {source.name}",
            file=sys.stderr,
        )
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (dest.name + ".part")
    try:
        proc = subprocess.run(
            [
                ffmpeg, "-y", "-v", "error", "-i", str(source),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-movflags", "+faststart",
                # The scratch name has no video extension, so the
                # MP4 format is named outright.
                "-f", "mp4", str(tmp),
            ],
            capture_output=True, text=True, timeout=1800,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[video] conversion of {source.name} failed: {exc}",
              file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return False
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tail = (proc.stderr or "").strip()[-1000:]
        print(
            f"[video] conversion of {source.name} failed:\n{tail}",
            file=sys.stderr,
        )
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(dest)
    return True


def _playback_target(config: "WebAppConfig", name: str) -> Path:
    """The file to actually serve for one of the job's videos.

    The job's own file when it is already encoded the way browsers
    can play (H.264). Otherwise a converted playback copy inside
    the session folder, built once and then reused; the original is
    never touched. When no copy can be built, the original is
    served as-is: the browser refuses it and the player shows its
    plain could-not-play message instead of sitting on a loading
    note forever.
    """
    source = config.job_dir / name
    with config.playback_lock:
        cached = config.playback_cache.get(name)
        if cached is not None:
            return cached
        target = source
        if _probe_video_codec(source) != BROWSER_SAFE_CODEC:
            copy_path = _playback_copy_path(config, name)
            if copy_path.is_file() and copy_path.stat().st_size > 0:
                target = copy_path
            elif _convert_playback_copy(source, copy_path):
                target = copy_path
        config.playback_cache[name] = target
        return target


def _video_content_type(path: Path) -> str:
    if path.suffix.lower() == ".mov":
        return "video/quicktime"
    return "video/mp4"
DOWNLOAD_NAMES = (
    "feedback.json",
    "operations.json",
    "operations_audit.json",
    "revised_cuts.json",
)
SESSION_DIR_NAME = "tye-session"
INTAKE_DIR_NAME = "intake"
_CHUNK = 256 * 1024


class WebAppConfig:
    """Everything the handler needs, fixed at server start, except the
    session sign-in (see ``session_api_key``)."""

    def __init__(
        self,
        job_dir: Path,
        render_py: Optional[str] = None,
        source_video: Optional[str] = None,
        cleaned_wav: Optional[str] = None,
    ):
        self.job_dir = Path(job_dir)
        self.render_py = render_py
        self.source_video = source_video
        self.cleaned_wav = cleaned_wav
        # Render auto-wiring: a piece the server was not handed
        # explicitly is picked up from the job folder itself when the
        # folder carries it (the runner's render script, the original
        # video, the cleaned audio). Explicit flags always win, and
        # the pieces found this way are named in ``render_autowired``
        # so the status sentence can say where setup came from.
        self.render_autowired: list[str] = []
        if not self.render_py:
            candidate = self.job_dir / RENDER_SCRIPT_NAME
            if candidate.is_file():
                self.render_py = str(candidate)
                self.render_autowired.append("render_py")
        if not self.cleaned_wav:
            candidate = self.job_dir / CLEANED_WAV_NAME
            if candidate.is_file():
                self.cleaned_wav = str(candidate)
                self.render_autowired.append("cleaned_wav")
        if not self.source_video:
            found = _autodetect_source_video(self.job_dir)
            if found:
                self.source_video = found
                self.render_autowired.append("source_video")
        # The one piece of state that may change while the server
        # runs: a Meta sign-in pasted on the page. It is kept in this
        # attribute only, in memory: never written to a file, never
        # sent back to the page, and gone when the server stops.
        self.session_api_key: Optional[str] = None
        # Which file to serve for each job video (the original, or
        # its converted playback copy), decided once per video; the
        # lock keeps two watchers from converting the same file at
        # the same time.
        self.playback_cache: dict = {}
        self.playback_lock = threading.Lock()

    @property
    def session_dir(self) -> Path:
        return self.job_dir / SESSION_DIR_NAME

    def signin_key(self) -> Optional[str]:
        """The Meta sign-in checks and direction runs use, if any.

        A key pasted on the page for this session wins over the one
        the server started with (``MODEL_API_KEY``).
        """
        if self.session_api_key:
            return self.session_api_key
        return os.environ.get("MODEL_API_KEY") or None

    def signin_source(self) -> Optional[str]:
        """Where the current sign-in came from, or None when unset.

        One of "session" (pasted on the page), "server" (the
        environment the server started with), or None.
        """
        if self.session_api_key:
            return "session"
        if os.environ.get("MODEL_API_KEY"):
            return "server"
        return None

    def render_missing(self) -> list[str]:
        missing = []
        if not self.render_py:
            missing.append("render_py")
        if not self.source_video:
            missing.append("source_video")
        if not self.cleaned_wav:
            missing.append("cleaned_wav")
        return missing

    def render_message(self) -> str:
        """One plain sentence about rendering, for people to read.

        Says rendering is ready (and whether the job folder itself
        provided the pieces), or names what is missing in plain
        words. Machine names stay in ``render_missing``; this is the
        sentence the startup line, the render response, and the job
        summary carry.
        """
        missing = self.render_missing()
        if not missing:
            if self.render_autowired:
                return "Rendering is set up from this job folder."
            return "Rendering is set up."
        names = [
            _RENDER_PIECE_PLAIN[piece]
            for piece in _RENDER_PIECE_ORDER
            if piece in missing
        ]
        return (
            "Rendering is not set up yet: this job is missing "
            + _join_plain(names)
            + "."
        )


def _load_optional_file(path: Path) -> Optional[dict]:
    if path.is_file():
        return _load_json(str(path))
    return None


COMMENT_SCOPES = ("both", "vertical", "landscape")
RUN_FORMATS = ("vertical", "landscape")


def _normalize_scope(value) -> str:
    """A comment's scope: both formats, or one of them.

    Missing or unrecognized values mean the comment applies to both
    formats, which is the default a viewer gets when they never touch
    the scope picker.
    """
    scope = str(value or "both").strip().lower()
    return scope if scope in COMMENT_SCOPES else "both"


def _scope_applies(comment: dict, fmt: str) -> bool:
    """Whether a comment is in scope for a direction run for ``fmt``."""
    scope = _normalize_scope(comment.get("scope"))
    return scope == "both" or scope == fmt


def _overall_notes(feedback) -> list:
    """The feedback's overall notes, normalized to {id, text} dicts.

    Overall notes are about the whole video rather than one moment
    ("too many retakes in this one"). Plain strings are accepted and
    given an id, so older or hand-written feedback files still work.
    """
    if not isinstance(feedback, dict):
        return []
    raw = feedback.get("overall_feedback")
    if not isinstance(raw, list):
        return []
    notes = []
    for index, item in enumerate(raw):
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            note_id = str(item.get("id") or f"g{index + 1}")
        else:
            text = str(item or "").strip()
            note_id = f"g{index + 1}"
        if text:
            notes.append({"id": note_id, "text": text})
    return notes


def _feedback_for_run(feedback: dict, fmt: str) -> dict:
    """The feedback one direction run receives.

    A run is for one format, so it only receives the comments whose
    scope covers that format; a scope of Both always applies. The
    overall notes are tied to neither a moment nor a format, so they
    ride along with every run as overall guidance.
    """
    comments = feedback.get("comments")
    if not isinstance(comments, list):
        comments = []
    run = dict(feedback)
    run["comments"] = [
        comment
        for comment in comments
        if isinstance(comment, dict) and _scope_applies(comment, fmt)
    ]
    run["overall_feedback"] = _overall_notes(feedback)
    return run


def _feedback_path(config: WebAppConfig) -> Optional[Path]:
    """Session feedback wins; the job's own feedback.json is the fallback."""
    session = config.session_dir / "feedback.json"
    if session.is_file():
        return session
    job = config.job_dir / "feedback.json"
    if job.is_file():
        return job
    return None


# ---------------------------------------------------------------------------
# v0.4 session state: front door choices, notes on edits, grades, rules
#
# The simplified build keeps four small stores in the session folder,
# beside the state the earlier versions already kept. None of them
# ever touches the job's own files, and all four ride the checkpoint
# history, so undo covers them like everything else.
# ---------------------------------------------------------------------------

FRONT_DOOR_DEFAULTS = {
    "remove_retakes": True,
    "cut_silence": True,
    "buffer_s": 0.1,
}
BUFFER_MIN_S = 0.05
BUFFER_MAX_S = 0.50
RETAKES_GUIDANCE = (
    "Remove retakes: when the same line is said more than once, keep "
    "the last take and take the earlier tries out."
)
SILENCE_COMMENT_ID = "front-door-silence"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_session_json(config: WebAppConfig, name: str, fallback):
    path = config.session_dir / name
    if path.is_file():
        try:
            return _load_json(str(path))
        except (OSError, ValueError):
            return fallback
    return fallback


def _front_door_settings(config: WebAppConfig) -> dict:
    """The front door's choices: the saved ones, else the defaults.

    Remove retakes and Cut silence start on; the silence buffer
    starts at 0.1 seconds. A choice the creator changed is kept in
    the session and offered back the same way next time.
    """
    saved = _load_session_json(config, FRONT_DOOR_FILENAME, {})
    settings = dict(FRONT_DOOR_DEFAULTS)
    if isinstance(saved, dict):
        for key in ("remove_retakes", "cut_silence"):
            if isinstance(saved.get(key), bool):
                settings[key] = saved[key]
        buffer = saved.get("buffer_s")
        if isinstance(buffer, (int, float)) and not isinstance(buffer, bool):
            settings["buffer_s"] = min(
                BUFFER_MAX_S, max(BUFFER_MIN_S, float(buffer))
            )
    return settings


def _save_front_door_settings(config: WebAppConfig, settings: dict) -> dict:
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(config.session_dir / FRONT_DOOR_FILENAME, settings)
    return settings


def _load_edit_notes(config: WebAppConfig) -> dict:
    """Notes left on individual edits, keyed by edit id."""
    payload = _load_session_json(
        config, EDIT_NOTES_FILENAME, {"edits": {}}
    )
    if isinstance(payload, dict) and isinstance(payload.get("edits"), dict):
        return payload
    return {"edits": {}}


def _save_edit_notes(config: WebAppConfig, payload: dict) -> None:
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(config.session_dir / EDIT_NOTES_FILENAME, payload)


def _edit_guidance(config: WebAppConfig) -> list:
    """Every note left on an edit, flattened for a direction run.

    Each item names what was removed and why, so the direction step
    can apply the note to that material wherever it is involved,
    instead of guessing from a timestamp.
    """
    guidance = []
    for record in _load_edit_notes(config)["edits"].values():
        if not isinstance(record, dict):
            continue
        for note in record.get("notes", []):
            if isinstance(note, dict) and str(note.get("text") or "").strip():
                guidance.append(
                    {
                        "removed_text": str(record.get("removed_text") or ""),
                        "reason": str(record.get("reason") or ""),
                        "note": str(note["text"]),
                    }
                )
    return guidance


def _load_grades(config: WebAppConfig) -> list:
    payload = _load_session_json(config, GRADES_FILENAME, {"entries": []})
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return [e for e in payload["entries"] if isinstance(e, dict)]
    return []


def _save_grades(config: WebAppConfig, entries: list) -> None:
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(config.session_dir / GRADES_FILENAME, {"entries": entries})


def _rules_store(config: WebAppConfig) -> dict:
    return creator_rules.load_rules_store(
        config.session_dir / CREATOR_RULES_FILENAME
    )


def _save_rules_store(config: WebAppConfig, store: dict) -> None:
    creator_rules.save_rules_store(
        config.session_dir / CREATOR_RULES_FILENAME, store
    )


def _rule_suggestions(config: WebAppConfig) -> list:
    """Patterns in the grades that are ready to offer as rules."""
    return creator_rules.suggestions_for(_load_grades(config), _rules_store(config))


def _saved_rules_view(config: WebAppConfig) -> list:
    """The saved rules as the page shows them (id, statement, pattern)."""
    return [
        {
            "rule_id": rule.get("rule_id"),
            "pattern_key": rule.get("pattern_key"),
            "statement": rule.get("statement"),
        }
        for rule in _rules_store(config).get("rules", [])
        if rule.get("active", True)
    ]


def _audit_operations(config: WebAppConfig) -> list:
    """Page operations from every run this session has recorded.

    The latest run's audit plus the archived rounds, so a removed
    span can be matched to the change that removed it even when a
    later run has happened since.
    """
    operations: list = []
    session = config.session_dir
    audit_paths = []
    if (session / "operations_audit.json").is_file():
        audit_paths.append(session / "operations_audit.json")
    rounds = session / "rounds"
    if rounds.is_dir():
        audit_paths.extend(
            sorted(rounds.glob("round-*/operations_audit.json"))
        )
    for path in audit_paths:
        try:
            audit = _load_json(str(path))
        except (OSError, ValueError):
            continue
        if isinstance(audit, dict):
            operations.extend(
                op
                for op in audit.get("operations", [])
                if isinstance(op, dict)
            )
    return operations


def _edit_spans(config: WebAppConfig) -> list:
    """The spans this app has taken out of the edit, with their reasons.

    A span is a run of words the job's own plan kept and the
    session's current plan has cut. Each span is matched to the
    recorded change that removed it (a direction run's applied cut,
    with the note that asked for it when there is one); a span no
    recorded change explains was cut by clicking in the transcript.
    Notes left on an edit ride along with its span.
    """
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    words = transcript.get("words", [])
    base = Timeline(transcript, _load_json(str(config.job_dir / "cuts.json")))
    current = Timeline(transcript, _effective_cuts(config))
    operations = _audit_operations(config)
    comments_by_id: dict = {}
    feedback_path = _feedback_path(config)
    if feedback_path is not None:
        try:
            payload = _load_json(str(feedback_path))
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict):
            for comment in payload.get("comments", []):
                if isinstance(comment, dict) and comment.get("id") is not None:
                    comments_by_id[str(comment["id"])] = comment
    notes_by_edit = _load_edit_notes(config)["edits"]

    def covering_op(start: int, end: int) -> Optional[dict]:
        for op in operations:
            if op.get("status") != "applied":
                continue
            spans = []
            if op.get("type") == "cut_words":
                spans = [(op.get("word_start"), op.get("word_end"))]
            elif op.get("type") == "replace_take":
                spans = [(op.get("cut_word_start"), op.get("cut_word_end"))]
            for span_start, span_end in spans:
                if (
                    isinstance(span_start, int)
                    and isinstance(span_end, int)
                    and span_start < end
                    and start < span_end
                ):
                    return op
        return None

    spans = []
    index = 0
    while index < len(words):
        removed_here = (
            base.word_status(index) == "kept"
            and current.word_status(index) == "cut"
        )
        if not removed_here:
            index += 1
            continue
        start = index
        while index < len(words) and (
            base.word_status(index) == "kept"
            and current.word_status(index) == "cut"
        ):
            index += 1
        end = index
        edit_id = f"edit-{start}-{end}"
        op = covering_op(start, end)
        if op is not None:
            source = "direction"
            reason = str(
                op.get("plain_action")
                or "These words were removed by a direction run."
            )
            comment_id = op.get("comment_id")
            comment = comments_by_id.get(str(comment_id)) if comment_id else None
            comment_text = (
                str(comment.get("text")) if comment and comment.get("text") else None
            )
        else:
            source = "transcript"
            reason = "You cut these words by clicking in the transcript."
            comment_id = None
            comment_text = None
        record = notes_by_edit.get(edit_id)
        notes = []
        if isinstance(record, dict) and (
            record.get("word_start") == start and record.get("word_end") == end
        ):
            notes = [
                {"id": n.get("id"), "text": n.get("text")}
                for n in record.get("notes", [])
                if isinstance(n, dict) and n.get("text")
            ]
        retake_meta = None
        if op is not None and isinstance(op.get("retake"), dict):
            meta = op["retake"]
            retake_meta = {
                "pair_id": meta.get("pair_id"),
                "band": meta.get("band"),
                "retake_class": meta.get("retake_class"),
                "keep_text": meta.get("keep_text"),
                "keep_word_start": meta.get("keep_word_start"),
                "keep_word_end": meta.get("keep_word_end"),
            }
        spans.append(
            {
                "edit_id": edit_id,
                "word_start": start,
                "word_end": end,
                "removed_text": " ".join(
                    str(words[i].get("word") or "") for i in range(start, end)
                ),
                "reason": reason,
                "source": source,
                "comment_id": comment_id,
                "comment_text": comment_text,
                "notes": notes,
                "retake": retake_meta,
            }
        )
    return spans


def _video_name_for_review(config: WebAppConfig) -> Optional[str]:
    """Video name the after-review page should reference, if any."""
    if (config.session_dir / SESSION_VIDEO_NAME).is_file():
        return SESSION_VIDEO_NAME
    videos = _job_videos(config.job_dir)
    return videos[0] if videos else None


def _output_time_for_word(config: WebAppConfig, word_index: int) -> float:
    """Finished-edit time at (or just after) one transcript word.

    A retake pair's removed words are cut in the current plan, so
    the time is taken from the first kept word at or after them:
    the moment the listener reaches that point of the edit.
    """
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    timeline = Timeline(transcript, _effective_cuts(config))
    words = timeline.words
    if not words:
        return 0.0
    target = None
    for index in range(min(word_index, len(words) - 1), len(words)):
        if timeline.word_status(index) in ("kept", "special"):
            target = index
            break
    if target is None:
        return round(timeline.output_duration, 3)
    word = words[target]
    mid = (float(word["start"]) + float(word["end"])) / 2.0
    for segment in timeline.segments:
        rng = segment["range"]
        if float(rng["start"]) - 1e-6 <= mid <= float(rng["end"]) + 1e-6:
            return round(
                segment["output_start"] + (mid - float(rng["start"])), 3
            )
    return round(timeline.output_duration, 3)


def _augmented_review_feedback(config: WebAppConfig) -> Optional[Path]:
    """Feedback for the review page, with the retake pairs as rows.

    The review page renders one row per feedback comment, and a
    retake found by the front door has no comment behind it. Rather
    than bend the renderer, the pass's pairs that produced a
    recorded change (a cut or a flag) are written as plain synthetic
    comments in a session copy of the feedback, so each pair gets
    its row, its grade, and its place in the learning loop. Returns
    None when there is nothing to add.
    """
    record = _load_retake_record(config)
    pairs = [
        pair for pair in record.get("pairs", []) if isinstance(pair, dict)
    ]
    if not pairs:
        return None
    audit_path = config.session_dir / "operations_audit.json"
    if not audit_path.is_file():
        return None
    try:
        audit = _load_json(str(audit_path))
    except (OSError, ValueError):
        return None
    op_ids = {
        op.get("comment_id")
        for op in audit.get("operations", [])
        if isinstance(op, dict)
    }
    base_comments: list = []
    feedback_path = _feedback_path(config)
    if feedback_path is not None:
        try:
            payload = _load_json(str(feedback_path))
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict):
            base_comments = [
                comment
                for comment in payload.get("comments", [])
                if isinstance(comment, dict)
            ]
    synthetic = []
    for pair in pairs:
        if pair.get("pair_id") not in op_ids:
            continue
        cut = pair.get("cut") or {}
        keep = pair.get("keep") or {}
        cut_text = str(cut.get("text") or "")
        keep_text = str(keep.get("text") or "")
        if len(cut_text) > 110:
            cut_text = cut_text[:107].rstrip() + "..."
        if len(keep_text) > 110:
            keep_text = keep_text[:107].rstrip() + "..."
        synthetic.append(
            {
                "id": pair["pair_id"],
                "ts": _output_time_for_word(
                    config, int(cut.get("word_start") or 0)
                ),
                "text": (
                    "Retake found by the front door. "
                    f"Earlier: \"{cut_text}\" "
                    f"The take that stayed: \"{keep_text}\""
                ),
            }
        )
    if not synthetic:
        return None
    out_path = config.session_dir / "review_feedback.json"
    _write_json(out_path, {"comments": base_comments + synthetic})
    return out_path


def _regenerate_after_review(config: WebAppConfig) -> Path:
    feedback = _augmented_review_feedback(config) or _feedback_path(config)
    audit = config.session_dir / "operations_audit.json"
    return generate_after_review(
        feedback,
        audit,
        config.session_dir / "after-review.html",
        video_filename=_video_name_for_review(config),
    )


def _refresh_after_review(config: WebAppConfig) -> None:
    """Rebuild the after-review page after a checkpoint restore.

    The page is derived from the session state, so a restore rebuilds
    it from the restored audit; a restore to before any direction run
    has no audit, and the stale page is removed so the placeholder
    shows instead of grades for edits that are no longer current.
    """
    if (config.session_dir / "operations_audit.json").is_file():
        try:
            _regenerate_after_review(config)
            return
        except Exception:
            pass
    (config.session_dir / "after-review.html").unlink(missing_ok=True)


def _effective_cuts(config: WebAppConfig) -> dict:
    """The cuts plan the session is currently working from.

    Once anything has written a revised plan into the session (a
    direction run, a transcript word toggle), that plan is the current
    state of the edit; until then the job's own cuts.json is.
    """
    revised = config.session_dir / "revised_cuts.json"
    if revised.is_file():
        return _load_json(str(revised))
    return _load_json(str(config.job_dir / "cuts.json"))


def _session_corrections(config: WebAppConfig, word_count: int) -> list:
    """The session's word corrections, cleaned against this transcript."""
    return load_corrections(
        config.session_dir / CORRECTIONS_FILENAME, word_count
    )


def _transcript_entries(config: WebAppConfig) -> tuple[list, list]:
    """Display entries for the transcript view, plus the corrections.

    Entries reflect the session's current cuts (kept and cut words as
    they stand now) with the session's word corrections laid over the
    top: a corrected span is one entry carrying the corrected text.
    """
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    words = transcript.get("words", [])
    word_timeline = Timeline(transcript, _effective_cuts(config))
    statuses = [word_timeline.word_status(i) for i in range(len(words))]
    corrections = _session_corrections(config, len(words))
    return (
        transcript_display_entries(words, statuses, corrections),
        corrections,
    )


def _job_summary(config: WebAppConfig) -> dict:
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    # The headline counts describe the edit as the session currently
    # has it, not as the job originally shipped: once anything has
    # revised the plan, the session's plan is the truth here too,
    # the same one the transcript view reads.
    cuts = _effective_cuts(config)
    timeline = Timeline(transcript, cuts)
    kept = timeline.kept_word_indices()
    transcript_entries, corrections = _transcript_entries(config)
    videos = _job_videos(config.job_dir)
    video_shapes = {}
    for name in videos:
        dims = _mp4_dimensions(config.job_dir / name)
        video_shapes[name] = (
            {"width": dims[0], "height": dims[1]} if dims else None
        )
    feedback_path = _feedback_path(config)
    comments: list = []
    overall_feedback: list = []
    feedback_exists = feedback_path is not None
    if feedback_exists:
        payload = _load_json(str(feedback_path))
        if isinstance(payload, dict):
            if isinstance(payload.get("comments"), list):
                comments = [
                    {**c, "scope": _normalize_scope(c.get("scope"))}
                    for c in payload["comments"]
                    if isinstance(c, dict)
                ]
            overall_feedback = _overall_notes(payload)
    return {
        "job_name": config.job_dir.name,
        "source_duration_s": timeline.source_duration,
        "output_duration_s": round(timeline.output_duration, 4),
        "fps": timeline.fps,
        "kept_ranges": len(timeline.ranges),
        "special_ranges": sum(
            1 for s in timeline.segments if s["special"]
        ),
        "joins": len(timeline.joins()),
        "words_total": len(timeline.words),
        "words_kept": len(kept),
        "words_cut": len(timeline.words) - len(kept),
        "transcript_words": transcript_entries,
        "word_corrections": corrections,
        "videos": videos,
        "video_shapes": video_shapes,
        "feedback_exists": feedback_exists,
        "comments": comments,
        "overall_feedback": overall_feedback,
        "edit_spans": _edit_spans(config),
        "front_door": _front_door_settings(config),
        "saved_rules": _saved_rules_view(config),
        "rule_suggestions": _rule_suggestions(config),
        "meta_available": bool(config.signin_key()),
        "render_missing": config.render_missing(),
        "render_ready": not config.render_missing(),
        "render_message": config.render_message(),
    }


def _plain_direction_error(exc: Exception) -> str:
    """The one plain sentence the page may show for a direction failure.

    Raw detail (HTTP status codes, schema reason codes) is for the
    server log only; a viewer gets words they can act on.
    """
    raw = str(exc)
    if isinstance(exc, ProviderError):
        if raw.startswith(("meta_api_http_401", "meta_api_http_403")):
            return (
                "The Meta sign-in was not accepted. Check the API key "
                "and try again."
            )
        if raw.startswith("meta_api_http_429"):
            return (
                "Meta's service is busy right now. Wait a moment and "
                "try again."
            )
        if raw.startswith("meta_api_http_"):
            return (
                "Meta's service returned an error. Try again in a "
                "moment."
            )
        if raw.startswith("meta_api_unreachable"):
            return (
                "Meta's service could not be reached. Check the "
                "connection and try again."
            )
        if raw.startswith("model_response_not_json"):
            return (
                "The editor service replied in a form that could not "
                "be read. Try running direction again."
            )
        if raw.startswith("model_operations_invalid"):
            return (
                "The editor service suggested edits that did not pass "
                "the safety check, so nothing was changed."
            )
        if raw == "MODEL_API_KEY is not set":
            return "The Meta provider is not set up on this server yet."
        return (
            "The direction service hit a problem, so nothing was "
            "changed. Try again in a moment."
        )
    if isinstance(exc, SchemaError):
        return (
            "The suggested edits did not pass the safety check, so "
            "nothing was changed."
        )
    if isinstance(exc, ApplyError):
        return (
            "The checked edits could not be put together, so nothing "
            "was changed."
        )
    return "Direction did not run, and nothing was changed."


def _log_raw_error(kind: str, exc: Exception) -> None:
    """Raw failure detail belongs in the server log, never on the page."""
    print(f"[direction] {kind} error: {exc}", file=sys.stderr)


SIGNIN_ACCEPTED_MESSAGE = "The Meta sign-in was accepted."
SIGNIN_NOT_ACCEPTED_MESSAGE = (
    "The Meta sign-in was not accepted. Check the API key and try again."
)


def _signin_state(config: WebAppConfig) -> dict:
    """What the page may know about the sign-in: set or not, and where
    it came from. The key itself is never part of this."""
    source = config.signin_source()
    return {"meta_signin_set": source is not None, "source": source}


def _check_signin(config: WebAppConfig) -> tuple[bool, str]:
    """Run the harmless list call; (accepted, one plain sentence).

    Uses the pasted session sign-in when there is one. A refused key
    and a missing key both read as "not accepted"; anything else that
    goes wrong is mapped to a plain sentence the same way direction
    failures are, with the raw detail going to the server log.
    """
    key = config.signin_key()
    if not key:
        return False, SIGNIN_NOT_ACCEPTED_MESSAGE
    provider = MetaProvider(api_key=key)
    try:
        accepted = provider.check_signin()
    except ProviderError as exc:
        _log_raw_error("signin check", exc)
        return False, _plain_direction_error(exc)
    if accepted:
        return True, SIGNIN_ACCEPTED_MESSAGE
    return False, SIGNIN_NOT_ACCEPTED_MESSAGE


def _next_round_number(config: WebAppConfig) -> int:
    """The number the next direction run in this session gets."""
    rounds_dir = config.session_dir / "rounds"
    if not rounds_dir.is_dir():
        return 1
    return 1 + sum(
        1
        for path in rounds_dir.iterdir()
        if path.is_dir() and path.name.startswith("round-")
    )


def _archive_direction_round(config: WebAppConfig, number: int) -> None:
    """Copy the run that just finished into its own round folder.

    The session's operations.json, operations_audit.json, and
    revised_cuts.json always hold the latest run, so without this a
    new run would silently overwrite the previous round's record.
    Each run is copied to ``rounds/round-NNN/`` in the session, and
    those folders are append-only: undo and redo never remove them.
    """
    session = config.session_dir
    target = session / "rounds" / f"round-{number:03d}"
    target.mkdir(parents=True, exist_ok=True)
    for name in (
        "operations.json",
        "operations_audit.json",
        "revised_cuts.json",
    ):
        source = session / name
        if source.is_file():
            shutil.copyfile(source, target / name)


def _run_direction(
    config: WebAppConfig,
    provider_name: str,
    fmt: str = "vertical",
    extra_overall: Optional[list] = None,
    front_door: Optional[dict] = None,
) -> dict:
    """Mirror cli.cmd_direct, writing every output into the session dir.

    The run is for one format (``fmt``): the provider only receives
    the comments whose scope covers that format, plus the overall
    notes, which go with every run.

    The run builds on the session's current plan: when anything has
    already revised the edit (an earlier direction run, a transcript
    word click), that revised plan is the run's starting point, so
    round two continues from round one instead of re-deciding it,
    and a word cut by clicking is never silently restored by a run.
    Only a session with no revised plan yet starts from the job's
    own cuts.

    ``extra_overall`` carries guidance that belongs to this run only
    (the front door's choices). Saved creator rules and notes left
    on edits ride into every run: the rules as standing direction,
    ahead of the run's own notes, and the edit notes as edit-scoped
    guidance labeled with what was removed.
    """
    feedback_path = _feedback_path(config)
    if feedback_path is None:
        if extra_overall is None and front_door is None:
            raise ValueError(
                "No feedback yet. Save at least one comment first."
            )
        feedback = {"comments": [], "overall_feedback": []}
    else:
        feedback = _load_json(str(feedback_path))
    transcript_path = config.job_dir / "transcript.json"
    cuts_path = config.job_dir / "cuts.json"
    profile_path = config.job_dir / "profile.json"
    profile = _load_optional_file(profile_path)
    session = config.session_dir
    revised_path = session / "revised_cuts.json"
    base_cuts_path = revised_path if revised_path.is_file() else cuts_path
    # The provider reads the corrected transcript, so a direction run
    # works from the fixed words. Corrections change word texts only:
    # indices, timings, and the cuts the verifier checks against are
    # the transcript's own, so proposals still line up exactly.
    transcript_data = _load_json(str(transcript_path))
    corrections = _session_corrections(
        config, len(transcript_data.get("words", []))
    )
    timeline = Timeline(
        transcript_with_corrections(transcript_data, corrections),
        _load_json(str(base_cuts_path)),
    )
    if provider_name == "meta":
        provider = MetaProvider(
            api_key=config.signin_key(), allow_wonder=True
        )
    else:
        provider = MockProvider()
    run_feedback = _feedback_for_run(feedback, fmt)
    # Standing direction goes first: the creator's saved rules, then
    # this run's own extra guidance, then the saved overall notes.
    rule_statements = creator_rules.active_rule_statements(_rules_store(config))
    standing = [
        {"id": f"saved-rule-{index + 1}", "text": statement}
        for index, statement in enumerate(rule_statements)
    ]
    run_feedback["overall_feedback"] = (
        standing
        + list(extra_overall or [])
        + run_feedback["overall_feedback"]
    )
    run_feedback["edit_notes"] = _edit_guidance(config)
    operations, routing = provider.direct(run_feedback, timeline, profile)
    session.mkdir(parents=True, exist_ok=True)
    _write_json(session / "operations.json", {"operations": operations})
    args = SimpleNamespace(
        transcript=str(transcript_path),
        cuts=str(base_cuts_path),
        profile=str(profile_path) if profile is not None else None,
    )
    audit = _verify_and_apply(args, {"operations": operations}, session)
    audit_with_routing = dict(audit)
    audit_with_routing["routing"] = routing
    # Record what the run was handed, so the saved audit shows the
    # format it served and the overall guidance it carried.
    audit_with_routing["format"] = fmt
    audit_with_routing["overall_feedback"] = run_feedback["overall_feedback"]
    audit_with_routing["saved_rules"] = rule_statements
    audit_with_routing["edit_notes"] = run_feedback["edit_notes"]
    if front_door is not None:
        audit_with_routing["front_door"] = front_door
    # Number the round before the audit is saved, so the saved audit
    # and the archived copy both carry it.
    round_number = _next_round_number(config)
    audit_with_routing["round"] = round_number
    _write_json(session / "operations_audit.json", audit_with_routing)
    _archive_direction_round(config, round_number)
    return audit_with_routing


def _silence_operations(config: WebAppConfig, buffer_s: float) -> list:
    """Join-pause operations that trim silence toward the buffer.

    The buffer is the silence left between one word and the next:
    the tail of quiet at the end of one kept piece plus the lead-in
    before the next word. A join whose silence already fits the
    buffer is left alone (silence is never added), and a join beside
    a special insert is never proposed. Each proposal is an ordinary
    ``set_join_pause`` operation, so the safety verifier and the
    applier decide what actually changes, exactly as they do for a
    note that asks for a shorter pause.
    """
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    timeline = Timeline(transcript, _effective_cuts(config))
    operations = []
    for join in timeline.joins():
        if join["prev_special"] or join["next_special"]:
            continue
        index = join["join_index"]
        prev_range = timeline.ranges[index]
        next_range = timeline.ranges[index + 1]
        prev_words = timeline.words_in_range(prev_range)
        next_words = timeline.words_in_range(next_range)
        if not prev_words or not next_words:
            continue
        prev_tail = float(prev_range["end"]) - float(
            timeline.words[prev_words[-1]]["end"]
        )
        head = float(timeline.words[next_words[0]]["start"]) - float(
            next_range["start"]
        )
        if prev_tail + head <= buffer_s + 1e-3:
            continue
        target = buffer_s - prev_tail
        if target < MIN_JOIN_PAUSE_S:
            target = MIN_JOIN_PAUSE_S
        target = min(target, MAX_JOIN_PAUSE_S)
        if target >= head - 1e-3:
            continue  # This join cannot get any tighter from its head.
        operations.append(
            {
                "type": "set_join_pause",
                "comment_id": SILENCE_COMMENT_ID,
                "join_index": index,
                "target_pause_s": round(target, 3),
                "reason": (
                    "front door: trim the silence at this join "
                    f"toward {buffer_s:g} seconds"
                ),
                "confidence": 1.0,
                # A deterministic local proposal, like the mock's;
                # the comment id and the reason carry its provenance.
                "model": "mock",
                "risk_tier": 1,
                "wonder_reviewed": False,
            }
        )
    return operations


def _run_silence_pass(config: WebAppConfig, buffer_s: float) -> dict:
    """Trim the session plan's long silences toward ``buffer_s``.

    Runs the join-pause proposals through the same verify-and-apply
    path as a direction run, against the session's current plan. The
    pass is recorded in its own session file, and its page
    operations are merged into the session audit so the removed and
    shortened moments stay explained in one place.
    """
    summary = {
        "ran": True,
        "buffer_s": buffer_s,
        "joins_trimmed": 0,
        "operations": [],
    }
    operations = _silence_operations(config, buffer_s)
    if not operations:
        _record_silence_pass(config, summary)
        return summary
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    revised_path = session / "revised_cuts.json"
    base_cuts_path = (
        revised_path if revised_path.is_file() else config.job_dir / "cuts.json"
    )
    profile_path = config.job_dir / "profile.json"
    tmp = session / ".silence-tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    args = SimpleNamespace(
        transcript=str(config.job_dir / "transcript.json"),
        cuts=str(base_cuts_path),
        profile=str(profile_path) if profile_path.is_file() else None,
    )
    try:
        audit = _verify_and_apply(args, {"operations": operations}, tmp)
    except (SchemaError, ApplyError) as exc:
        # The proposals are built here and checked by the same
        # verifier either way; if the pass still cannot run, the
        # plan stays exactly as it was and the raw detail goes to
        # the server log.
        _log_raw_error("silence pass", exc)
        shutil.rmtree(tmp, ignore_errors=True)
        _record_silence_pass(config, summary)
        return summary
    shutil.copyfile(tmp / "revised_cuts.json", revised_path)
    page_operations = audit.get("operations", [])
    summary["operations"] = page_operations
    summary["joins_trimmed"] = sum(
        1 for op in page_operations if op.get("status") == "applied"
    )
    session_audit_path = session / "operations_audit.json"
    if session_audit_path.is_file():
        existing = _load_json(str(session_audit_path))
        existing.setdefault("operations", []).extend(page_operations)
        existing["after"] = audit["after"]
        _write_json(session_audit_path, existing)
    else:
        shutil.copyfile(tmp / "operations_audit.json", session_audit_path)
    shutil.rmtree(tmp, ignore_errors=True)
    _record_silence_pass(config, summary)
    return summary


def _record_silence_pass(config: WebAppConfig, summary: dict) -> None:
    """Append one silence pass to the session's own record of them."""
    path = config.session_dir / "silence_pass.json"
    payload = _load_session_json(config, "silence_pass.json", {"runs": []})
    runs = payload.get("runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        runs = []
    runs.append(
        {
            "at": _utc_now(),
            "buffer_s": summary["buffer_s"],
            "joins_trimmed": summary["joins_trimmed"],
        }
    )
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(path, {"runs": runs})


# ---------------------------------------------------------------------------
# The retake pass (v0.5.0)
#
# The front door's Remove retakes choice used to be one guidance
# sentence handed to the direction model; with no comments pinned it
# measured nothing. Now the local detector in ``retakes.py`` measures
# the transcript itself: clear pairs (Band A) become ordinary
# cut_words operations through the same verify-and-apply path the
# silence pass uses, policy holds (a negation in either copy, a short
# emphasis double) become flags, and borderline pairs (Band B) go to
# one model judgment call when a provider is configured, or degrade
# to scored flags when there is none. The pass runs against the
# session's current plan before any direction run, so it works with
# zero comments pinned, and every outcome is recorded in
# ``retake_pass.json`` and merged into the session audit with the
# pair it came from, which is what puts both takes in the transcript
# dots and the pair's rows on the review page.
# ---------------------------------------------------------------------------

_RETAKE_PATTERN_DESCRIPTIONS = {
    "a": (
        "Taking out the earlier take of a repeated line when the "
        "local check was sure about it"
    ),
    "b": (
        "Taking out the earlier take of a repeated line when a "
        "model judged the pair"
    ),
    "flag": "Leaving a repeated line for the creator to decide",
}


def _load_retake_record(config: WebAppConfig) -> dict:
    payload = _load_session_json(
        config, RETAKE_PASS_FILENAME, {"runs": [], "pairs": [], "decisions": {}}
    )
    if not isinstance(payload, dict):
        payload = {"runs": [], "pairs": [], "decisions": {}}
    payload.setdefault("runs", [])
    payload.setdefault("pairs", [])
    payload.setdefault("decisions", {})
    return payload


def _save_retake_record(config: WebAppConfig, record: dict) -> None:
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(config.session_dir / RETAKE_PASS_FILENAME, record)


def _retake_pair_for(config: WebAppConfig, pair_id: Optional[str]) -> Optional[dict]:
    """The recorded pair behind one review row or grade, if any."""
    if not pair_id:
        return None
    for pair in _load_retake_record(config).get("pairs", []):
        if isinstance(pair, dict) and pair.get("pair_id") == pair_id:
            return pair
    return None


def _enrich_retake_grades(config: WebAppConfig, raw_entries: list) -> list:
    """Give graded retake rows their per-band pattern key.

    A grade from a retake pair's review row carries the pair id as
    its comment id. Looking the pair up in the pass record attaches
    the explicit pattern key and a plain description (no machine
    tokens, so it can be shown), which is what lets grades tune the
    bands through the rule suggestions instead of dissolving into
    the operation type. A "did not work" grade on a pair also
    records the creator's decision about it: that pair is never
    proposed again.
    """
    record = _load_retake_record(config)
    pairs_by_id = {
        pair.get("pair_id"): pair
        for pair in record.get("pairs", [])
        if isinstance(pair, dict) and pair.get("pair_id")
    }
    if not pairs_by_id:
        return raw_entries
    decisions_changed = False
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        pair = pairs_by_id.get(
            entry.get("comment_id") or entry.get("record_id")
        )
        if pair is None:
            continue
        if not entry.get("pattern_key"):
            entry["pattern_key"] = pair["pattern_key"]
            entry.setdefault(
                "pattern_description",
                _RETAKE_PATTERN_DESCRIPTIONS.get(
                    pair.get("band"),
                    _RETAKE_PATTERN_DESCRIPTIONS["flag"],
                ),
            )
        if entry.get("verdict") == "did_not_work":
            key = retakes.pair_key(pair)
            decisions = record.setdefault("decisions", {})
            if not (decisions.get(key) or {}).get("keep_both"):
                decisions[key] = {"keep_both": True, "at": _utc_now()}
                decisions_changed = True
    if decisions_changed:
        _save_retake_record(config, record)
    return raw_entries


def _run_retake_pass(
    config: WebAppConfig, provider_name: str, spoken_direction: str = ""
) -> dict:
    """Measure the edit for retakes and act on what the bands allow.

    Band A pairs are cut through the verifier like any other change.
    Band B pairs are judged in one provider call when the provider
    can judge; a keep-both or not-a-pair verdict is recorded and no
    operation is made, and any pair the model did not answer, plus
    every Band B pair under a provider that cannot judge, becomes a
    scored flag. Policy-flag pairs are flags by construction. Pairs
    the creator already graded as keep-both are never proposed again.
    """
    summary = {
        "ran": True,
        "provider": provider_name,
        "pairs": 0,
        "band_a": 0,
        "band_b": 0,
        "flags": 0,
        "applied": 0,
        "flagged": 0,
        "kept_both": 0,
    }
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    profile = _load_optional_file(config.job_dir / "profile.json")
    base_cuts = _effective_cuts(config)
    timeline = Timeline(transcript, base_cuts)
    record = _load_retake_record(config)
    decisions = record.get("decisions") or {}
    pairs = [
        pair
        for pair in retakes.find_pairs(timeline, profile)
        if decisions.get(retakes.pair_key(pair), {}).get("keep_both") is not True
    ]
    summary["pairs"] = len(pairs)
    summary["band_a"] = len(
        [pair for pair in pairs if pair["band"] == "a"]
    )
    summary["band_b"] = len(retakes.borderline_pairs(pairs))
    summary["flags"] = len(retakes.flag_pairs(pairs))
    if not pairs:
        record["runs"].append({**summary, "at": _utc_now()})
        record["pairs"] = []
        _save_retake_record(config, record)
        return summary

    operations = retakes.band_a_operations(timeline, profile, pairs=pairs)
    outcomes: dict = {}
    for pair in pairs:
        if pair["band"] == "a":
            outcomes[pair["pair_id"]] = "proposed"
    for pair in retakes.flag_pairs(pairs):
        operations.append(retakes.flag_operation(pair))
        outcomes[pair["pair_id"]] = "flagged"

    borderline = retakes.borderline_pairs(pairs)
    judged_ops: list = []
    verdicts: dict = {}
    if borderline:
        provider = None
        if provider_name == "meta":
            provider = MetaProvider(config.signin_key())
        elif provider_name == "mock":
            provider = MockProvider()
        judged = None
        if provider is not None:
            guidance = creator_rules.active_rule_statements(
                _rules_store(config)
            )
            if spoken_direction:
                guidance = guidance + [spoken_direction]
            try:
                judged = provider.judge_retakes(
                    borderline, timeline, guidance=guidance
                )
            except ProviderError as exc:
                # A failed judgment call never fails the pass: the
                # pairs degrade to scored flags, like having no
                # provider at all.
                _log_raw_error("retake judgment", exc)
                judged = None
        if judged is not None:
            judged_ops, verdicts = judged
        for pair in borderline:
            verdict = verdicts.get(pair["pair_id"])
            if verdict in ("keep_both", "not_a_pair"):
                outcomes[pair["pair_id"]] = (
                    "kept_both" if verdict == "keep_both" else "not_a_pair"
                )
                summary["kept_both"] += 1
            elif verdict in ("cut_earlier", "cut_later"):
                outcomes[pair["pair_id"]] = "judged_cut"
            else:
                operations.append(
                    retakes.flag_operation(
                        pair, reason=retakes.band_b_flag_reason(pair)
                    )
                )
                outcomes[pair["pair_id"]] = "flagged"
    operations.extend(judged_ops)

    page_operations: list = []
    if operations:
        session = config.session_dir
        session.mkdir(parents=True, exist_ok=True)
        revised_path = session / "revised_cuts.json"
        base_cuts_path = (
            revised_path
            if revised_path.is_file()
            else config.job_dir / "cuts.json"
        )
        profile_path = config.job_dir / "profile.json"
        tmp = session / ".retake-tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        args = SimpleNamespace(
            transcript=str(config.job_dir / "transcript.json"),
            cuts=str(base_cuts_path),
            profile=str(profile_path) if profile_path.is_file() else None,
        )
        try:
            audit = _verify_and_apply(
                args, {"operations": operations}, tmp
            )
        except (SchemaError, ApplyError) as exc:
            _log_raw_error("retake pass", exc)
            shutil.rmtree(tmp, ignore_errors=True)
            audit = None
        if audit is not None:
            shutil.copyfile(tmp / "revised_cuts.json", revised_path)
            page_operations = audit.get("operations", [])
            pairs_by_id = {pair["pair_id"]: pair for pair in pairs}
            for entry in page_operations:
                pair = pairs_by_id.get(entry.get("comment_id"))
                if pair is None:
                    continue
                entry["pattern_key"] = pair["pattern_key"]
                entry["retake"] = {
                    "pair_id": pair["pair_id"],
                    "band": pair["band"],
                    "retake_class": pair["retake_class"],
                    "pattern_key": pair["pattern_key"],
                    "coverage": pair.get("coverage"),
                    "ratio": pair.get("ratio"),
                    "cut_text": pair["cut"]["text"],
                    "keep_text": pair["keep"]["text"],
                    "keep_word_start": pair["keep"]["word_start"],
                    "keep_word_end": pair["keep"]["word_end"],
                }
                if outcomes.get(pair["pair_id"]) == "proposed":
                    outcomes[pair["pair_id"]] = (
                        "applied"
                        if entry.get("status") == "applied"
                        else "held"
                    )
                elif outcomes.get(pair["pair_id"]) == "judged_cut":
                    outcomes[pair["pair_id"]] = (
                        "applied"
                        if entry.get("status") == "applied"
                        else "held"
                    )
            # The pass's audit is handed to the caller, not merged
            # here: the front door runs direction next, and a
            # direction run replaces the session audit with its own,
            # so the merge has to happen after those runs (the
            # silence pass, which runs last, merges its own inline).
            summary["audit"] = audit
            shutil.rmtree(tmp, ignore_errors=True)
    summary["applied"] = sum(
        1 for op in page_operations if op.get("status") == "applied"
    )
    summary["flagged"] = sum(
        1 for op in page_operations if op.get("status") == "flagged"
    )
    summary["operations"] = page_operations
    record["runs"].append({**summary, "at": _utc_now(), "operations": []})
    record["pairs"] = [
        {**pair, "outcome": outcomes.get(pair["pair_id"], "proposed")}
        for pair in pairs
    ]
    _save_retake_record(config, record)
    return summary


def _run_front_door(config: WebAppConfig, body: dict) -> dict:
    """The simplified front door: two choices, one buffer, one button.

    Remove retakes and Cut silence are on unless the creator turned
    them off. Firing first runs the local retake pass (which measures
    the transcript itself, so it works with no comments pinned; its
    page operations are merged into the session audit after the
    direction runs, which replace that file with their own), then
    runs direction for the chosen formats with the retakes guidance
    riding along (plus anything typed in the spoken direction box,
    which adds to the choices rather than replacing them), then
    trims the plan's long silences toward the buffer. Saved rules
    ride into the run inside ``_run_direction`` itself.
    """
    defaults = _front_door_settings(config)
    try:
        buffer_s = float(body.get("buffer_s", defaults["buffer_s"]))
    except (TypeError, ValueError):
        raise ValueError("The silence buffer must be a number of seconds.")
    settings = {
        "remove_retakes": bool(
            body.get("remove_retakes", defaults["remove_retakes"])
        ),
        "cut_silence": bool(body.get("cut_silence", defaults["cut_silence"])),
        "buffer_s": min(BUFFER_MAX_S, max(BUFFER_MIN_S, buffer_s)),
    }
    scope = str(body.get("format_scope") or "both").strip().lower()
    if scope not in ("both", "vertical", "landscape"):
        raise ValueError("Choose both formats, vertical only, or landscape only.")
    direction_text = str(body.get("direction_text") or "").strip()
    provider_name = str(body.get("provider") or "").strip().lower()
    if provider_name not in ("mock", "meta"):
        provider_name = "meta" if config.signin_key() else "mock"
    if provider_name == "meta" and not config.signin_key():
        raise ValueError(
            "The Meta provider is not set up on this server yet, "
            "so direction cannot run with it."
        )
    _save_front_door_settings(config, settings)
    retake_summary = None
    if settings["remove_retakes"]:
        retake_summary = _run_retake_pass(
            config, provider_name, spoken_direction=direction_text
        )
    extras = []
    if settings["remove_retakes"]:
        extras.append({"id": "front-door-retakes", "text": RETAKES_GUIDANCE})
    if direction_text:
        extras.append({"id": "front-door-direction", "text": direction_text})
    formats = ["vertical", "landscape"] if scope == "both" else [scope]
    has_feedback = _feedback_path(config) is not None
    direction_runs = []
    for fmt in formats:
        if not has_feedback and not extras:
            continue
        audit = _run_direction(
            config,
            provider_name,
            fmt,
            extra_overall=extras,
            front_door=settings,
        )
        direction_runs.append(
            {
                "format": fmt,
                "round": audit.get("round"),
                "before": audit["before"],
                "after": audit["after"],
                "applied_comment_ids": audit["applied_comment_ids"],
                "flagged_comment_ids": audit["flagged_comment_ids"],
            }
        )
    if retake_summary is not None:
        retake_audit = retake_summary.pop("audit", None)
        if retake_audit is not None:
            session_audit_path = config.session_dir / "operations_audit.json"
            if session_audit_path.is_file():
                existing = _load_json(str(session_audit_path))
                existing.setdefault("operations", []).extend(
                    retake_summary["operations"]
                )
                existing["after"] = retake_audit["after"]
                _write_json(session_audit_path, existing)
            else:
                _write_json(session_audit_path, retake_audit)
    silence = None
    if settings["cut_silence"]:
        silence = _run_silence_pass(config, settings["buffer_s"])
    return {
        "ok": True,
        "provider": provider_name,
        "settings": settings,
        "format_scope": scope,
        "direction_runs": direction_runs,
        "retakes": retake_summary,
        "silence": silence,
        "saved_rules": creator_rules.active_rule_statements(
            _rules_store(config)
        ),
    }


def _plain_toggle_message(verdict) -> str:
    """One plain sentence for a transcript word the verifier would not move.

    Verdict reasons are machine codes; the page shows a person one
    sentence they can understand and act on. The code-to-sentence
    mapping is shared with the per-note result text (see
    ``verifier.plain_reason_sentence``), so a held word and a held
    note are explained in the same words.
    """
    for reason in verdict.reasons:
        return plain_reason_sentence(reason)
    return "The safety check would not move that word, so it was left as it is."


def _toggle_word(config: WebAppConfig, word_index: int) -> dict:
    """Cut or restore one transcript word at the creator's click.

    The click becomes a single ``cut_words`` or ``restore_words``
    operation, validated and checked by the existing safety verifier
    against the current plan (the session's revised cuts when they
    exist, else the job's cuts), and applied with the existing
    applier. Only an applied verdict changes anything, and the new
    plan lands in the session's ``revised_cuts.json``; the job's own
    files are never touched.

    One verifier hold does not block a click: ``wonder_review_required``
    exists so a model's riskier proposals get a second review. A
    transcript click is the creator's own instruction, made while
    looking at the exact word, so when that hold is the only reason a
    verdict was flagged, the click itself is the review. Every other
    rule (protected spans, negations, special inserts, range minimums,
    the 25 percent rule) still holds the word exactly as it would for
    a model proposal, and the page explains it in one plain sentence.
    """
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    words = transcript.get("words", [])
    if not 0 <= word_index < len(words):
        raise ValueError("that word is not in the transcript")
    base_cuts = _effective_cuts(config)
    timeline = Timeline(transcript, base_cuts)
    status = timeline.word_status(word_index)
    if status == "special":
        return {
            "ok": True,
            "word_index": word_index,
            "status": status,
            "changed": False,
            "message": (
                "That word is inside a special insert, so it cannot "
                "be changed here."
            ),
        }
    cutting = status == "kept"
    operation = {
        "type": "cut_words" if cutting else "restore_words",
        "comment_id": "transcript-toggle",
        "word_start": word_index,
        "word_end": word_index + 1,
        "reason": (
            "You clicked this word in the transcript to cut it."
            if cutting
            else "You clicked this word in the transcript to bring it back."
        ),
        "confidence": 1.0,
        "model": "mock",
        "risk_tier": 1,
        "wonder_reviewed": False,
    }
    ops = validate_operations([operation])
    profile = _load_optional_file(config.job_dir / "profile.json")
    verdict = verify_operations(ops, timeline, profile)[0]
    if verdict.status == "flag" and verdict.reasons == ["wonder_review_required"]:
        verdict.status = "apply"
    if verdict.status != "apply":
        return {
            "ok": True,
            "word_index": word_index,
            "status": status,
            "changed": False,
            "message": _plain_toggle_message(verdict),
        }
    revised, _audit = apply_verdicts(base_cuts, timeline, [verdict])
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    checkpoints.begin(config)
    _write_json(session / "revised_cuts.json", revised)
    checkpoints.commit(config, "Transcript word change")
    new_status = Timeline(transcript, revised).word_status(word_index)
    return {
        "ok": True,
        "word_index": word_index,
        "status": new_status,
        "changed": new_status != status,
        "message": None,
    }


def _edit_action(config: WebAppConfig, edit_id: str, action: str) -> dict:
    """Bring a removed span back, or swap it with the take that stayed.

    Both actions are the transcript click's bigger siblings: one
    operation, built here, checked by the same verifier against the
    session's current plan, applied by the same applier. Bring back
    is a ``restore_words`` over the removed span. Swap takes is a
    ``replace_take``: the take that stayed is cut and the removed
    take is restored, in one checked step. As with a word click, a
    lone ``wonder_review_required`` hold is forgiven, because the
    creator is looking at the exact words and the click is the
    review; every other safety rule holds exactly as written.
    """
    if action not in ("restore", "swap"):
        raise ValueError("That action is not one this page can do.")
    span = next(
        (s for s in _edit_spans(config) if s.get("edit_id") == edit_id),
        None,
    )
    if span is None:
        raise ValueError("That edit is not in the transcript any more.")
    start, end = span["word_start"], span["word_end"]
    if action == "restore":
        operation = {
            "type": "restore_words",
            "comment_id": "transcript-restore",
            "word_start": start,
            "word_end": end,
            "reason": "You brought these words back from the transcript.",
            "confidence": 1.0,
            "model": "mock",
            "risk_tier": 2,
            "wonder_reviewed": False,
        }
        label = "Edit brought back"
        done_message = "Those words are back in the edit."
    else:
        meta = span.get("retake")
        if not isinstance(meta, dict) or meta.get("keep_word_start") is None:
            return {
                "ok": True,
                "changed": False,
                "message": (
                    "That edit does not name the take that stayed, so "
                    "the takes cannot be swapped from here."
                ),
            }
        operation = {
            "type": "replace_take",
            "comment_id": "transcript-swap",
            "cut_word_start": meta["keep_word_start"],
            "cut_word_end": meta["keep_word_end"],
            "restore_word_start": start,
            "restore_word_end": end,
            "reason": "You swapped the takes from the transcript.",
            "confidence": 1.0,
            "model": "mock",
            "risk_tier": 2,
            "wonder_reviewed": False,
        }
        label = "Takes swapped"
        done_message = "The takes are swapped."
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    base_cuts = _effective_cuts(config)
    timeline = Timeline(transcript, base_cuts)
    ops = validate_operations([operation])
    profile = _load_optional_file(config.job_dir / "profile.json")
    verdict = verify_operations(ops, timeline, profile)[0]
    if verdict.status == "flag" and verdict.reasons == ["wonder_review_required"]:
        verdict.status = "apply"
    if verdict.status != "apply":
        return {
            "ok": True,
            "changed": False,
            "message": _plain_toggle_message(verdict),
        }
    revised, _audit = apply_verdicts(base_cuts, timeline, [verdict])
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    checkpoints.begin(config)
    _write_json(session / "revised_cuts.json", revised)
    checkpoints.commit(config, label)
    return {"ok": True, "changed": True, "message": done_message}


# ---------------------------------------------------------------------------
# Drag-and-drop intake (v0.4.1)
#
# A job folder (or a single video) dropped on the page is uploaded
# file by file and staged under the session area the server started
# with: ``<launch job>/tye-session/intake/<name>-<id>/``. The staged
# copy is a copy; whatever the creator dropped is never modified. A
# staged folder that carries the two files a finished-edit job is
# made of (transcript.json and cuts.json) becomes the server's
# current job on the spot, exactly as if its path had been picked
# at launch. A staged video on its own is served for watching, with
# one plain sentence about what a full edit still needs.
# ---------------------------------------------------------------------------

VIDEO_ONLY_MESSAGE = (
    "Your video is in, and you can watch it below. A full edit "
    "needs more than the video: it needs the word-by-word "
    "transcript and the edit plan, which part 1, the runner, "
    "makes from the original recording. Drop the finished-edit "
    "job folder and the app can work on it."
)
NOT_A_JOB_MESSAGE = (
    "That folder is not a finished-edit job. A job folder carries "
    "transcript.json and cuts.json together, the two files part 1, "
    "the runner, makes. Nothing was loaded and nothing was "
    "changed. Drop the job folder itself and it will load here."
)
EMPTY_DROP_MESSAGE = (
    "Nothing arrived with that drop. Try again, and drop the job "
    "folder itself."
)


def _safe_drop_name(name: str) -> str:
    """A display name for a drop, safe as one folder name segment."""
    tail = str(name or "").replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "-", tail).strip(" .")
    return cleaned[:60] or "dropped-files"


def _safe_relative_parts(raw: str) -> Optional[list]:
    """The segments of an uploaded file's relative path, or None.

    Empty and "." segments are dropped. Any ".." refuses the whole
    path: an upload may only ever land inside its own staging
    folder, never outside it.
    """
    parts = []
    for segment in str(raw or "").replace("\\", "/").split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            return None
        parts.append(segment)
    return parts or None


def _intake_candidates(base: Path) -> list:
    """The staging folder itself, then each folder directly inside it.

    A dropped job folder lands one level down (its own name is the
    first path segment); loose files land at the top. Job detection
    looks at both, shallowest first.
    """
    candidates = [base]
    try:
        candidates += sorted(
            path for path in base.iterdir() if path.is_dir()
        )
    except OSError:
        pass
    return candidates


def _find_job_root(base: Path) -> Optional[Path]:
    """The folder in the drop that is a finished-edit job, if any.

    A finished-edit job is the folder carrying transcript.json and
    cuts.json together: the two files every other part of the app
    reads from the job folder.
    """
    for candidate in _intake_candidates(base):
        if (candidate / "transcript.json").is_file() and (
            candidate / "cuts.json"
        ).is_file():
            return candidate
    return None


def _find_staged_video(base: Path) -> Optional[tuple]:
    """(folder, name) of the first watchable video in the drop."""
    for candidate in _intake_candidates(base):
        names = _job_videos(candidate)
        if names:
            return candidate, names[0]
    return None


def _has_any_file(base: Path) -> bool:
    try:
        return any(path.is_file() for path in base.rglob("*"))
    except OSError:
        return False


_STAGED_PLAYBACK_LOCK = threading.Lock()


def _staged_playback_target(source: Path) -> Path:
    """The file to serve for a staged video.

    The staged file itself when it is already browser-safe; else a
    converted playback copy in a ``playback`` folder beside it,
    built once and reused. The staged original is never touched.
    """
    if _probe_video_codec(source) == BROWSER_SAFE_CODEC:
        return source
    suffix = source.suffix.lstrip(".").lower()
    dest = (
        source.parent
        / PLAYBACK_DIR_NAME
        / f"{source.stem}-{suffix}.playback.mp4"
    )
    with _STAGED_PLAYBACK_LOCK:
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        if _convert_playback_copy(source, dest):
            return dest
    return source


def _detect_intake(base: Path) -> dict:
    """What a finished drop turned out to be, in plain categories.

    One of "job" (a finished-edit job folder; ``job_root`` set),
    "video" (a watchable video but no job files; ``video_path``
    set), "not_a_job" (files arrived, but no job and no video), or
    "empty" (nothing arrived at all).
    """
    job_root = _find_job_root(base)
    if job_root is not None:
        return {"kind": "job", "job_root": job_root}
    found = _find_staged_video(base)
    if found is not None:
        folder, name = found
        return {"kind": "video", "video_path": folder / name}
    if _has_any_file(base):
        return {"kind": "not_a_job"}
    return {"kind": "empty"}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    server_version = "TalkToYourEditor/0.2"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------

    @property
    def config(self) -> WebAppConfig:
        return self.server.config  # type: ignore[attr-defined]

    def log_message(self, format, *args):  # noqa: A002 - stdlib name
        pass

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra_headers: Optional[dict] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send_bytes(
            status,
            json.dumps(payload, indent=2).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _error(self, status: int, message: str, **extra) -> None:
        payload = {"error": message}
        payload.update(extra)
        self._send_json(status, payload)

    def _read_json_body(self) -> Optional[dict]:
        length = self.headers.get("Content-Length")
        if length is None:
            return {}
        try:
            raw = self.rfile.read(int(length))
        except (ValueError, OSError):
            return None
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _serve_file_with_range(self, path: Path, content_type: str) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            self._error(404, "file not found")
            return
        start, end, status = 0, size - 1, 200
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(
                r"bytes=(\d*)-(\d*)", range_header.strip()
            )
            if match is None:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            first, last = match.groups()
            if first == "" and last == "":
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if first == "":
                start = max(0, size - int(last))
            else:
                start = int(first)
                if last:
                    end = min(int(last), size - 1)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header(
                "Content-Range", f"bytes {start}-{end}/{size}"
            )
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # -- routes -------------------------------------------------------------

    def do_GET(self):  # noqa: N802 - stdlib name
        path = unquote(urlsplit(self.path).path)
        try:
            if path == "/":
                self._page_index()
            elif path == "/api/job":
                self._send_json(200, _job_summary(self.config))
            elif path == "/api/signin":
                self._send_json(200, _signin_state(self.config))
            elif path == "/api/history":
                self._send_json(
                    200, {"ok": True, **checkpoints.state(self.config)}
                )
            elif path.startswith("/video/"):
                self._serve_video(path[len("/video/"):])
            elif path == "/after-review":
                self._serve_after_review()
            elif path.startswith("/api/intake/video/"):
                self._serve_intake_video(path[len("/api/intake/video/"):])
            elif path.startswith("/api/download/"):
                self._serve_download(path[len("/api/download/"):])
            elif (
                path[1:] in _job_videos(self.config.job_dir)
                or path[1:] == SESSION_VIDEO_NAME
            ):
                # The after-review page references its video by bare name;
                # resolve it from the session dir first, then the job dir.
                self._serve_media_by_name(path[1:])
            else:
                self._error(404, "not found")
        except FileNotFoundError:
            self._error(404, "a job file is missing")
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(400, str(exc))

    def do_POST(self):  # noqa: N802 - stdlib name
        path = unquote(urlsplit(self.path).path)
        if path == "/api/intake/begin":
            self._post_intake_begin()
        elif path == "/api/intake/file":
            self._post_intake_file()
        elif path == "/api/intake/finish":
            self._post_intake_finish()
        elif path == "/api/comments":
            self._post_comments()
        elif path == "/api/signin":
            self._post_signin()
        elif path == "/api/signin/check":
            self._post_signin_check()
        elif path == "/api/direct":
            self._post_direct()
        elif path == "/api/front-door":
            self._post_front_door()
        elif path == "/api/edit-note":
            self._post_edit_note()
        elif path == "/api/grades":
            self._post_grades()
        elif path == "/api/rule-save":
            self._post_rule_save()
        elif path == "/api/rule-dismiss":
            self._post_rule_dismiss()
        elif path == "/api/rule-remove":
            self._post_rule_remove()
        elif path == "/api/word-toggle":
            self._post_word_toggle()
        elif path == "/api/edit-action":
            self._post_edit_action()
        elif path == "/api/word-correct":
            self._post_word_correct()
        elif path == "/api/word-correct-remove":
            self._post_word_correct_remove()
        elif path == "/api/render":
            self._post_render()
        elif path == "/api/history/undo":
            self._post_history_move(undo=True)
        elif path == "/api/history/redo":
            self._post_history_move(undo=False)
        else:
            self._error(404, "not found")

    # -- GET handlers -----------------------------------------------------

    def _page_index(self) -> None:
        template_path = (
            Path(__file__).resolve().parent
            / "templates"
            / "test_app.html"
        )
        page = template_path.read_text(encoding="utf-8")
        job_name = self.config.job_dir.name
        summary = None
        try:
            summary = _job_summary(self.config)
        except Exception:
            summary = None
        primary_video = None
        meta_available = bool(self.config.signin_key())
        if summary:
            videos = summary["videos"]
            primary_video = videos[0] if videos else None
            meta_available = summary["meta_available"]
        page = page.replace("{{JOB_NAME}}", html.escape(job_name))
        page = page.replace("{{JOB_NAME_JSON}}", json.dumps(job_name))
        page = page.replace("{{VIDEO_JSON}}", json.dumps(primary_video))
        page = page.replace(
            "{{META_AVAILABLE_JSON}}", json.dumps(meta_available)
        )
        self._send_bytes(
            200, page.encode("utf-8"), "text/html; charset=utf-8"
        )

    def _serve_video(self, name: str) -> None:
        if name not in _job_videos(self.config.job_dir):
            self._error(404, "unknown video")
            return
        # A video that is not browser-safe is served through its
        # converted playback copy; the job's file is never served
        # in a form the browser cannot decode when a copy exists.
        target = _playback_target(self.config, name)
        self._serve_file_with_range(target, _video_content_type(target))

    def _serve_media_by_name(self, name: str) -> None:
        session_target = self.config.session_dir / name
        if session_target.is_file():
            self._serve_file_with_range(
                session_target, _video_content_type(session_target)
            )
            return
        if name in _job_videos(self.config.job_dir):
            target = _playback_target(self.config, name)
            self._serve_file_with_range(
                target, _video_content_type(target)
            )
            return
        self._error(404, "video not found")

    def _serve_after_review(self) -> None:
        target = self.config.session_dir / "after-review.html"
        if target.is_file():
            self._send_bytes(
                200,
                target.read_bytes(),
                "text/html; charset=utf-8",
            )
            return
        page = (
            "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, "
            "initial-scale=1\"><title>After review</title></head>"
            "<body style=\"font-family: sans-serif; background: #0b0b0f; "
            "color: #f5f5f7; padding: 24px;\">"
            "<h1>No after-review yet</h1>"
            "<p>Run direction first, then this page will show what "
            "changed, comment by comment.</p>"
            "<p><a href=\"/\" style=\"color: #0a84ff;\">Back to the test "
            "page</a></p></body></html>"
        )
        self._send_bytes(
            200, page.encode("utf-8"), "text/html; charset=utf-8"
        )

    def _serve_download(self, name: str) -> None:
        if name not in DOWNLOAD_NAMES:
            self._error(404, "unknown download")
            return
        target = self.config.session_dir / name
        if not target.is_file():
            self._error(404, "that file has not been generated yet")
            return
        self._send_bytes(
            200,
            target.read_bytes(),
            "application/json; charset=utf-8",
            {"Content-Disposition": f'attachment; filename="{name}"'},
        )

    # -- intake plumbing --------------------------------------------------

    def _intake_root(self) -> Path:
        """Where drops are staged: the session area the server
        started with, fixed at server start so it stays one place
        even after a dropped job becomes the current job."""
        root = getattr(self.server, "intake_root", None)
        if root is None:
            root = self.config.session_dir / INTAKE_DIR_NAME
        return Path(root)

    def _intakes(self) -> dict:
        """The drop registry (intake id to staging record)."""
        registry = getattr(self.server, "intakes", None)
        if registry is None:
            registry = {}
            self.server.intakes = registry  # type: ignore[attr-defined]
        return registry

    def _serve_intake_video(self, rest: str) -> None:
        """Serve one staged video from a drop that was not a job.

        The path is <intake id>/<relative path>. A staged video in
        a format the browser cannot play is served through a
        converted playback copy, the same arrangement as job videos.
        """
        parts = rest.split("/", 1)
        if len(parts) != 2:
            self._error(404, "video not found")
            return
        intake_id, raw_relative = parts
        record = self._intakes().get(intake_id)
        if record is None:
            self._error(404, "video not found")
            return
        segments = _safe_relative_parts(raw_relative)
        if segments is None:
            self._error(404, "video not found")
            return
        base = Path(record["dir"]).resolve()
        target = base.joinpath(*segments).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            self._error(404, "video not found")
            return
        if target.suffix.lower() not in VIDEO_EXTENSIONS:
            self._error(404, "video not found")
            return
        served = _staged_playback_target(target)
        self._serve_file_with_range(served, _video_content_type(served))

    # -- POST handlers ----------------------------------------------------

    def _post_intake_begin(self) -> None:
        """Open a staging folder for one drop and hand back its id.

        Files then arrive one by one at /api/intake/file, and
        /api/intake/finish closes the drop and says what it was.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        name = _safe_drop_name(str(body.get("name") or ""))
        intake_id = uuid.uuid4().hex[:12]
        staging = self._intake_root() / f"{name}-{intake_id}"
        try:
            staging.mkdir(parents=True, exist_ok=False)
        except OSError:
            _log_raw_error("intake begin", OSError(str(staging)))
            self._error(
                500,
                "The drop could not be taken in. Try again in a moment.",
            )
            return
        self._intakes()[intake_id] = {"dir": str(staging), "name": name}
        self._send_json(200, {"ok": True, "intake_id": intake_id})

    def _post_intake_file(self) -> None:
        """Receive one file of a drop, byte for byte, into staging.

        The file's path inside the dropped folder rides the query
        string; the body is the file itself, streamed to disk so a
        large video never has to sit in memory. The path is checked
        segment by segment and the finished path must resolve inside
        the staging folder.
        """
        query = parse_qs(urlsplit(self.path).query)
        intake_id = (query.get("intake") or [""])[0]
        raw_path = (query.get("path") or [""])[0]
        record = self._intakes().get(intake_id)
        if record is None:
            self._error(
                400,
                "That drop is not on record anymore. Drop the folder again.",
            )
            return
        parts = _safe_relative_parts(raw_path)
        if parts is None:
            self._error(
                400,
                "That file's name could not be used, so it was not taken in.",
            )
            return
        base = Path(record["dir"]).resolve()
        target = base.joinpath(*parts).resolve()
        if not target.is_relative_to(base):
            self._error(
                400,
                "That file's name could not be used, so it was not taken in.",
            )
            return
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            self._error(400, "The file arrived without a size.")
            return
        try:
            remaining = int(length_header)
        except ValueError:
            self._error(400, "The file arrived without a size.")
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as handle:
                while remaining > 0:
                    chunk = self.rfile.read(min(_CHUNK, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    remaining -= len(chunk)
        except OSError as exc:
            _log_raw_error("intake file", exc)
            self._error(
                500,
                "That file could not be saved. The drop was stopped; "
                "nothing was loaded.",
            )
            return
        self._send_json(
            200,
            {"ok": True, "path": "/".join(parts), "size": target.stat().st_size},
        )

    def _post_intake_finish(self) -> None:
        """Close a drop and say, in plain words, what it turned out to be.

        A finished-edit job becomes the server's current job on the
        spot, with the session sign-in carried over. A lone video is
        offered back for watching with the sentence about what a
        full edit still needs. Anything else is the clear not-a-job
        state. In every case the dropped originals are untouched:
        the app works on the staged copy it took in.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        intake_id = str(body.get("intake_id") or "")
        record = self._intakes().get(intake_id)
        if record is None:
            self._error(
                400,
                "That drop is not on record anymore. Drop the folder again.",
            )
            return
        base = Path(record["dir"])
        detected = _detect_intake(base)
        kind = detected["kind"]
        if kind == "job":
            job_root = detected["job_root"]
            new_config = WebAppConfig(job_root)
            new_config.session_api_key = self.config.session_api_key
            self.server.config = new_config  # type: ignore[attr-defined]
            self._send_json(
                200,
                {
                    "ok": True,
                    "kind": "job",
                    "job_name": job_root.name,
                    "message": (
                        f"Your job folder is in. The app is now "
                        f"running on {job_root.name}, the copy it "
                        "just took in; your original folder was not "
                        "changed. The page will reload on the new job."
                    ),
                },
            )
            return
        if kind == "video":
            video_path = detected["video_path"]
            relative = video_path.relative_to(base).as_posix()
            self._send_json(
                200,
                {
                    "ok": True,
                    "kind": "video",
                    "video_name": video_path.name,
                    "video_url": (
                        f"/api/intake/video/{intake_id}/"
                        + quote(relative)
                    ),
                    "message": VIDEO_ONLY_MESSAGE,
                },
            )
            return
        if kind == "empty":
            self._send_json(
                200, {"ok": True, "kind": "empty", "message": EMPTY_DROP_MESSAGE}
            )
            return
        self._send_json(
            200,
            {"ok": True, "kind": "not_a_job", "message": NOT_A_JOB_MESSAGE},
        )

    def _post_signin(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        key = body.get("api_key")
        if not isinstance(key, str) or not key.strip():
            self._error(400, "Paste the API key first.")
            return
        # Memory only: the key is never written to a file and never
        # sent back; the response carries the sign-in state alone.
        self.config.session_api_key = key.strip()
        self._send_json(200, {"ok": True, **_signin_state(self.config)})

    def _post_signin_check(self) -> None:
        if self._read_json_body() is None:
            self._error(400, "request body must be a JSON object")
            return
        accepted, message = _check_signin(self.config)
        self._send_json(
            200, {"ok": True, "accepted": accepted, "message": message}
        )

    def _post_comments(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        raw_comments = body.get("comments")
        if not isinstance(raw_comments, list):
            self._error(400, "comments must be a list")
            return
        comments = []
        for index, item in enumerate(raw_comments):
            if not isinstance(item, dict):
                self._error(400, f"comment {index} must be an object")
                return
            text = str(item.get("text") or "").strip()
            if not text:
                self._error(400, f"comment {index} has no text")
                return
            try:
                ts = float(item.get("ts", 0))
            except (TypeError, ValueError):
                self._error(400, f"comment {index} has a bad timestamp")
                return
            if (
                item.get("scope") is not None
                and str(item["scope"]).strip().lower() not in COMMENT_SCOPES
            ):
                self._error(400, f"comment {index} has a bad scope")
                return
            comments.append(
                {
                    "id": str(item.get("id") or f"c{index + 1}"),
                    "ts": round(ts, 1),
                    # A pinned time is an estimate, never a claim of
                    # the exact frame: direction resolves it to the
                    # nearest words within a few seconds either way.
                    "time_is_estimate": True,
                    "fmt": str(item.get("fmt") or "vertical"),
                    "scope": _normalize_scope(item.get("scope")),
                    "text": text,
                    "done": bool(item.get("done", False)),
                }
            )
        if "overall_feedback" in body:
            overall, error = self._read_overall(body["overall_feedback"])
            if error is not None:
                self._error(400, error)
                return
        else:
            # Not mentioned: keep the overall notes already saved, so
            # a client that only knows about comments cannot wipe them.
            overall = []
            existing_path = _feedback_path(self.config)
            if existing_path is not None:
                try:
                    overall = _overall_notes(
                        _load_json(str(existing_path))
                    )
                except (OSError, ValueError):
                    overall = []
        payload = {
            "video": str(body.get("video") or self.config.job_dir.name),
            "feedback_done": bool(body.get("feedback_done", False)),
            "comments": comments,
            "overall_feedback": overall,
        }
        session = self.config.session_dir
        session.mkdir(parents=True, exist_ok=True)
        checkpoints.begin(self.config)
        _write_json(session / "feedback.json", payload)
        checkpoints.commit(self.config, "Saved notes")
        self._send_json(
            200,
            {
                "ok": True,
                "saved": len(comments),
                "count": len(comments),
                "overall_saved": len(overall),
            },
        )

    @staticmethod
    def _read_overall(raw) -> tuple[list, Optional[str]]:
        """Validate an overall_feedback list; (notes, error) pair."""
        if not isinstance(raw, list):
            return [], "overall_feedback must be a list"
        notes = []
        for index, item in enumerate(raw):
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                note_id = str(item.get("id") or f"g{index + 1}")
            elif isinstance(item, str):
                text = item.strip()
                note_id = f"g{index + 1}"
            else:
                return [], f"overall note {index} must be an object"
            if not text:
                return [], f"overall note {index} has no text"
            notes.append({"id": note_id, "text": text})
        return notes, None

    def _post_direct(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        provider_name = str(body.get("provider") or "mock")
        if provider_name not in ("mock", "meta"):
            self._error(400, "provider must be mock or meta")
            return
        fmt = str(body.get("format") or "vertical").strip().lower()
        if fmt not in RUN_FORMATS:
            self._error(400, "format must be vertical or landscape")
            return
        if provider_name == "meta" and not self.config.signin_key():
            self._error(
                400,
                "The Meta provider is not set up on this server yet, "
                "so direction cannot run with it.",
            )
            return
        checkpoints.begin(self.config)
        try:
            audit = _run_direction(self.config, provider_name, fmt)
        except ValueError as exc:
            self._error(400, str(exc))
            return
        except ProviderError as exc:
            _log_raw_error("provider", exc)
            self._error(502, _plain_direction_error(exc))
            return
        except SchemaError as exc:
            _log_raw_error("schema", exc)
            self._error(400, _plain_direction_error(exc))
            return
        except ApplyError as exc:
            _log_raw_error("apply", exc)
            self._error(400, _plain_direction_error(exc))
            return
        except FileNotFoundError:
            self._error(404, "a job file is missing")
            return
        try:
            _regenerate_after_review(self.config)
            after_review = True
        except Exception:
            after_review = False
        checkpoints.commit(self.config, "Direction run")
        self._send_json(
            200,
            {
                "ok": True,
                "provider": provider_name,
                "format": fmt,
                "round": audit.get("round"),
                "overall_feedback": audit.get("overall_feedback", []),
                "applied_comment_ids": audit["applied_comment_ids"],
                "flagged_comment_ids": audit["flagged_comment_ids"],
                "rejected_comment_ids": audit["rejected_comment_ids"],
                "before": audit["before"],
                "after": audit["after"],
                "operations": audit.get("operations", []),
                "routing": audit.get("routing", []),
                "after_review": after_review,
            },
        )

    def _post_front_door(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        checkpoints.begin(self.config)
        try:
            result = _run_front_door(self.config, body)
        except ValueError as exc:
            self._error(400, str(exc))
            return
        except ProviderError as exc:
            _log_raw_error("provider", exc)
            self._error(502, _plain_direction_error(exc))
            return
        except SchemaError as exc:
            _log_raw_error("schema", exc)
            self._error(400, _plain_direction_error(exc))
            return
        except ApplyError as exc:
            _log_raw_error("apply", exc)
            self._error(400, _plain_direction_error(exc))
            return
        except FileNotFoundError:
            self._error(404, "a job file is missing")
            return
        after_review = False
        if result["direction_runs"] or (result.get("retakes") or {}).get(
            "pairs"
        ):
            try:
                _regenerate_after_review(self.config)
                after_review = True
            except Exception:
                after_review = False
        checkpoints.commit(self.config, "Front door run")
        result["after_review"] = after_review
        self._send_json(200, result)

    def _post_edit_note(self) -> None:
        """Save a note against one edit in the transcript.

        The note anchors to the edit itself (the removed words and
        their span), never to a timestamp, so it keeps meaning what
        it meant even as the edit's timing shifts. It is stored in
        the session with a snapshot of the edit, listed under that
        edit from then on, and handed to the next direction run as
        guidance labeled with what was removed. No note is ever
        dropped: every save appends to the edit's own list.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        edit_id = str(body.get("edit_id") or "")
        text = str(body.get("text") or "").strip()
        if not text:
            self._error(400, "Type or say the note first.")
            return
        span = next(
            (s for s in _edit_spans(self.config) if s["edit_id"] == edit_id),
            None,
        )
        if span is None:
            self._error(
                400,
                "That edit is not in the transcript anymore, so the "
                "note had nowhere to land.",
            )
            return
        payload = _load_edit_notes(self.config)
        record = payload["edits"].get(edit_id)
        if not isinstance(record, dict):
            record = {
                "edit_id": edit_id,
                "word_start": span["word_start"],
                "word_end": span["word_end"],
                "removed_text": span["removed_text"],
                "reason": span["reason"],
                "source": span["source"],
                "notes": [],
            }
            payload["edits"][edit_id] = record
        note = {
            "id": uuid.uuid4().hex[:12],
            "text": text,
            "created_at": _utc_now(),
        }
        record.setdefault("notes", []).append(note)
        checkpoints.begin(self.config)
        _save_edit_notes(self.config, payload)
        checkpoints.commit(self.config, "Note on an edit")
        self._send_json(
            200,
            {
                "ok": True,
                "edit_id": edit_id,
                "notes": [
                    {"id": n.get("id"), "text": n.get("text")}
                    for n in record["notes"]
                ],
            },
        )

    def _post_grades(self) -> None:
        """Receive graded changes from the review page.

        Grades accumulate in the session, merged by entry so grading
        the same change again updates it instead of counting it
        twice. The response carries the rule suggestions the grades
        now support, so the style card can appear right away.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        raw = body.get("entries")
        if raw is None and isinstance(body.get("graded"), list):
            raw = body.get("graded")
        if not isinstance(raw, list) or not all(
            isinstance(entry, dict) for entry in raw
        ):
            self._error(400, "entries must be a list of graded changes")
            return
        raw = _enrich_retake_grades(self.config, raw)
        entries = creator_rules.merge_grade_entries(
            _load_grades(self.config), raw
        )
        checkpoints.begin(self.config)
        _save_grades(self.config, entries)
        checkpoints.commit(self.config, "Grades saved")
        self._send_json(
            200,
            {
                "ok": True,
                "saved": len(entries),
                "suggestions": _rule_suggestions(self.config),
            },
        )

    def _rules_state_response(self) -> dict:
        return {
            "ok": True,
            "saved_rules": _saved_rules_view(self.config),
            "suggestions": _rule_suggestions(self.config),
        }

    def _post_rule_save(self) -> None:
        """Save one offered pattern as a rule. Only ever on a yes."""
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        pattern_key = str(body.get("pattern_key") or "")
        suggestion = next(
            (
                s
                for s in _rule_suggestions(self.config)
                if s["pattern_key"] == pattern_key
            ),
            None,
        )
        if suggestion is None:
            self._error(400, "That pattern is not ready to save as a rule yet.")
            return
        store = _rules_store(self.config)
        rule = creator_rules.save_rule(store, suggestion)
        checkpoints.begin(self.config)
        _save_rules_store(self.config, store)
        checkpoints.commit(self.config, "Rule saved")
        response = self._rules_state_response()
        response["rule"] = {
            "rule_id": rule.get("rule_id"),
            "statement": rule.get("statement"),
        }
        self._send_json(200, response)

    def _post_rule_dismiss(self) -> None:
        """Pass over one offered pattern, at its current support."""
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        pattern_key = str(body.get("pattern_key") or "")
        suggestion = next(
            (
                s
                for s in _rule_suggestions(self.config)
                if s["pattern_key"] == pattern_key
            ),
            None,
        )
        if suggestion is None:
            self._error(400, "That pattern is not being offered right now.")
            return
        store = _rules_store(self.config)
        creator_rules.dismiss_suggestion(
            store, pattern_key, suggestion["support_count"]
        )
        checkpoints.begin(self.config)
        _save_rules_store(self.config, store)
        checkpoints.commit(self.config, "Rule suggestion passed over")
        self._send_json(200, self._rules_state_response())

    def _post_rule_remove(self) -> None:
        """Remove one saved rule. The pattern may be offered again."""
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        rule_id = str(body.get("rule_id") or "")
        store = _rules_store(self.config)
        removed = creator_rules.remove_rule(store, rule_id)
        if removed is None:
            self._error(400, "That rule is not saved.")
            return
        checkpoints.begin(self.config)
        _save_rules_store(self.config, store)
        checkpoints.commit(self.config, "Rule removed")
        self._send_json(200, self._rules_state_response())

    def _post_word_toggle(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        index = body.get("word_index")
        if not isinstance(index, int) or isinstance(index, bool):
            self._error(400, "word_index must be an integer")
            return
        try:
            result = _toggle_word(self.config, index)
        except ValueError as exc:
            self._error(400, str(exc))
            return
        except (SchemaError, ApplyError) as exc:
            # The verifier and the applier mirror each other, so this
            # should not happen; if it ever does, the word stays put
            # and the page still gets one plain sentence, never a code.
            _log_raw_error("word-toggle", exc)
            self._send_json(
                200,
                {
                    "ok": True,
                    "word_index": index,
                    "status": None,
                    "changed": False,
                    "message": (
                        "The safety check would not move that word, "
                        "so it was left as it is."
                    ),
                },
            )
            return
        self._send_json(200, result)

    def _post_edit_action(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        edit_id = str(body.get("edit_id") or "")
        action = str(body.get("action") or "")
        if not edit_id or action not in ("restore", "swap"):
            self._error(
                400, "edit_id and an action of restore or swap are required"
            )
            return
        try:
            result = _edit_action(self.config, edit_id, action)
        except ValueError as exc:
            self._error(400, str(exc))
            return
        except (SchemaError, ApplyError) as exc:
            _log_raw_error("edit-action", exc)
            self._send_json(
                200,
                {
                    "ok": True,
                    "changed": False,
                    "message": (
                        "The safety check would not move those words, "
                        "so they were left as they are."
                    ),
                },
            )
            return
        self._send_json(200, result)

    def _correction_span(self, body: dict):
        """(word_start, word_end) from a correction request, or None.

        Sends the 400 itself when the fields are not whole numbers,
        so callers just check for None.
        """
        start = body.get("word_start")
        end = body.get("word_end")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
        ):
            self._error(
                400, "word_start and word_end must be whole numbers"
            )
            return None
        return start, end

    def _send_correction_state(self, corrections: list) -> None:
        entries, _ = _transcript_entries(self.config)
        self._send_json(
            200,
            {
                "ok": True,
                "corrections": corrections,
                "transcript_words": entries,
            },
        )

    def _post_word_correct(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        span = self._correction_span(body)
        if span is None:
            return
        text = body.get("text")
        if text is not None and not isinstance(text, str):
            self._error(400, "text must be a string")
            return
        transcript = _load_json(str(self.config.job_dir / "transcript.json"))
        word_count = len(transcript.get("words", []))
        corrections = _session_corrections(self.config, word_count)
        try:
            corrections = upsert_correction(
                corrections, span[0], span[1], text, word_count
            )
        except ValueError as exc:
            self._error(400, str(exc))
            return
        checkpoints.begin(self.config)
        save_corrections(
            self.config.session_dir / CORRECTIONS_FILENAME, corrections
        )
        checkpoints.commit(self.config, "Word correction")
        self._send_correction_state(corrections)

    def _post_word_correct_remove(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        span = self._correction_span(body)
        if span is None:
            return
        transcript = _load_json(str(self.config.job_dir / "transcript.json"))
        word_count = len(transcript.get("words", []))
        corrections = _session_corrections(self.config, word_count)
        try:
            corrections = remove_correction(corrections, span[0], span[1])
        except ValueError as exc:
            self._error(400, str(exc))
            return
        checkpoints.begin(self.config)
        save_corrections(
            self.config.session_dir / CORRECTIONS_FILENAME, corrections
        )
        checkpoints.commit(self.config, "Word correction removed")
        self._send_correction_state(corrections)

    def _post_history_move(self, undo: bool) -> None:
        """Undo or redo one checkpoint, restoring the session state.

        The after-review page is derived state, so it is refreshed
        from whatever the restore brought back. Both moves answer in
        plain words when there is nowhere to go.
        """
        if self._read_json_body() is None:
            self._error(400, "request body must be a JSON object")
            return
        moved = (
            checkpoints.undo(self.config)
            if undo
            else checkpoints.redo(self.config)
        )
        if moved is None:
            message = (
                "There is nothing to go back to yet."
                if undo
                else "There is nothing to redo."
            )
            self._send_json(
                200,
                {
                    "ok": False,
                    "message": message,
                    **checkpoints.state(self.config),
                },
            )
            return
        _refresh_after_review(self.config)
        self._send_json(200, {"ok": True, **moved})

    def _post_render(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        missing = self.config.render_missing()
        if missing:
            self._send_json(
                200,
                {
                    "ok": False,
                    "missing": missing,
                    "message": self.config.render_message(),
                },
            )
            return
        session = self.config.session_dir
        revised = session / "revised_cuts.json"
        if not revised.is_file():
            self._send_json(
                200,
                {
                    "ok": False,
                    "missing": [],
                    "error": "Run direction first, so there is a revised "
                    "plan to render.",
                },
            )
            return
        session.mkdir(parents=True, exist_ok=True)
        cleaned = self.config.cleaned_wav
        splice = session / "cleaned_spliced.wav"
        if splice.is_symlink() or splice.is_file():
            cleaned = str(splice)
        command = [
            sys.executable,
            self.config.render_py,
            self.config.source_video,
            cleaned,
            str(revised),
            str(session / SESSION_VIDEO_NAME),
            str(session / "revised-master.mp4"),
            "review",
        ]
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=1800,
            )
        except subprocess.TimeoutExpired:
            self._send_json(
                200,
                {
                    "ok": False,
                    "error": "Building the fixed video took too "
                    "long, so it was stopped. The revised plan is "
                    "still saved.",
                },
            )
            return
        except OSError as exc:
            # Raw detail is for the server log; the page gets words.
            _log_raw_error("render start", exc)
            self._send_json(
                200,
                {
                    "ok": False,
                    "error": "The fixed video could not be started. "
                    "The revised plan is still saved.",
                },
            )
            return
        if proc.returncode != 0:
            tail = (proc.stdout + "\n" + proc.stderr).strip()[-2000:]
            print(f"[render] render script failed:\n{tail}",
                  file=sys.stderr)
            self._send_json(
                200,
                {
                    "ok": False,
                    "error": "The fixed video could not be built. "
                    "Nothing was changed, and the revised plan is "
                    "still saved.",
                },
            )
            return
        try:
            _regenerate_after_review(self.config)
        except Exception:
            pass
        self._send_json(200, {"ok": True})


# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------


class _Server(ThreadingHTTPServer):
    """Threading server that stays quiet when a browser hangs up.

    Browsers cut a video stream mid-flight all the time (seeking,
    switching formats, closing the tab), and the default error
    handler prints a full BrokenPipeError traceback for each one,
    which reads like a crash to anyone watching the log. A client
    disconnect is expected, so it logs nothing; every other error
    keeps the default traceback.
    """

    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(
            exc,
            (BrokenPipeError, ConnectionResetError, ConnectionAbortedError),
        ):
            return
        super().handle_error(request, client_address)


def create_server(
    config: WebAppConfig,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    httpd = _Server((host, port), _Handler)
    httpd.daemon_threads = True
    httpd.config = config  # type: ignore[attr-defined]
    # Drag-and-drop intake is anchored to the session area the
    # server started with, so staged drops stay in one known place
    # even after a dropped job becomes the current job and the
    # config is swapped for the staged copy's.
    httpd.intake_root = config.session_dir / INTAKE_DIR_NAME  # type: ignore[attr-defined]
    httpd.intakes = {}  # type: ignore[attr-defined]
    return httpd


RENDER_SCRIPT_NAME = "render.py"
CLEANED_WAV_NAME = "cleaned.wav"
SOURCE_VIDEO_NAMES = (
    "source.mov",
    "source.MOV",
    "source.mp4",
    "source.MP4",
    "source.m4v",
)

# The render pieces in the order the plain sentence names them, each
# with the words a person would use for it. The machine names stay in
# the ``missing`` list for the page's own wording; these are for the
# one plain sentence the server itself speaks (startup line, render
# response, job summary).
_RENDER_PIECE_ORDER = ("render_py", "source_video", "cleaned_wav")
_RENDER_PIECE_PLAIN = {
    "render_py": "the render script",
    "source_video": "the original video",
    "cleaned_wav": "the cleaned audio",
}


def _join_plain(items: list) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _autodetect_source_video(job_dir: Path) -> Optional[str]:
    """The job folder's own original video, when it carries one.

    A file named ``source.*`` wins. Otherwise any ``.mov`` in the
    folder: the runner's finished cuts are all ``.mp4`` under known
    names, so a ``.mov`` can only be an original the creator dropped
    in. Anything else is a guess, and rendering never guesses.
    """
    for name in SOURCE_VIDEO_NAMES:
        candidate = job_dir / name
        if candidate.is_file():
            return str(candidate)
    try:
        movs = sorted(
            path
            for path in job_dir.iterdir()
            if path.is_file() and path.suffix.lower() == ".mov"
        )
    except OSError:
        return None
    if movs:
        return str(movs[0])
    return None


def _lan_ip() -> Optional[str]:
    """Best-effort primary LAN address, without sending any traffic."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def serve(
    job_dir: str,
    host: str = "0.0.0.0",
    port: int = 8765,
    render_py: Optional[str] = None,
    source_video: Optional[str] = None,
    cleaned_wav: Optional[str] = None,
) -> int:
    job = Path(job_dir)
    if job.is_file():
        # A video file is the mistake everyone makes first; say what
        # was handed over and what the app actually runs on, plainly.
        print(
            f"error: {job} is a file, not a folder. Talk to Your "
            "Editor runs on a runner job folder (the folder holding "
            "transcript.json and cuts.json), not on a video file.",
            file=sys.stderr,
        )
        return 2
    if not job.is_dir():
        print(f"error: job folder not found: {job}", file=sys.stderr)
        return 2
    for required in ("transcript.json", "cuts.json"):
        if not (job / required).is_file():
            print(f"error: {job / required} not found", file=sys.stderr)
            return 2
    config = WebAppConfig(
        job,
        render_py=render_py,
        source_video=source_video,
        cleaned_wav=cleaned_wav,
    )
    httpd = create_server(config, host=host, port=port)
    actual_port = httpd.server_address[1]
    print(f"Talk to Your Editor test app, job: {job}", flush=True)
    print(f"On this computer: http://127.0.0.1:{actual_port}/", flush=True)
    lan = _lan_ip()
    if lan:
        print(f"On your phone:    http://{lan}:{actual_port}/", flush=True)
    print(
        "Your phone must be on the same Wi-Fi as this computer.",
        flush=True,
    )
    if config.render_missing():
        print(
            config.render_message()
            + " Direction still works; rendering is off.",
            flush=True,
        )
    elif config.render_autowired:
        print(config.render_message(), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0
