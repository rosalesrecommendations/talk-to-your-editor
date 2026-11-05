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

The server can also start with no job at all. The home page is then
the front door: the drop zone, one button for the built-in sample,
and the Meta sign-in check, with a neutral launch home (a folder
beside the app, or wherever ``--launch-home`` points) holding the
session and the staged drops until a job arrives.

Standard library only. Run it with::

    tye serve

then open the printed URL on this computer, or on a phone on the same
Wi-Fi, and drop a job folder on the page. ``tye serve --job-dir
/path/to/job`` still starts on a job directly.
"""

from __future__ import annotations

import copy
import hashlib
import html
import json
from difflib import SequenceMatcher
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import captions as clean_captions
from . import coach
from . import checkpoints, costs, creator_rules, retakes, session_log, skills, teaching, usual
from . import direction_parse, final_sweep, finish, framing, intake_build, manual_edits, opening, saved_transcripts, smooth, timeline_export, voice_commands
from . import transcribe as speech
from .after_review import generate_after_review
from .applier import ApplyError, apply_verdicts
from .checkpoints import (
    CREATOR_RULES_FILENAME,
    EDIT_NOTES_FILENAME,
    FRONT_DOOR_FILENAME,
    GOLD_FILENAME,
    GRADES_FILENAME,
    RETAKE_PASS_FILENAME,
)
from .cli import _load_json, _verify_and_apply, _write_json
from .meta_provider import (
    HOSTED_SPARK_ROUTES,
    ROUTE_LABELS,
    MetaProvider,
    MockProvider,
    ProviderError,
    check_hosted_signin,
)
from .schema import MAX_JOIN_PAUSE_S, MIN_JOIN_PAUSE_S, SchemaError, validate_operations
from .timeline import Timeline
from .verifier import (
    _norm_word,
    plain_reason_sentence,
    verify_operations,
)
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

    The walk reads box headers only and seeks past payloads: it
    used to read the whole file into memory, which on a 4K
    original meant gigabytes of RAM and seconds of wall time on
    every summary the page asked for (found in the Message 3
    proof, where it OOM-killed the proof server outright).
    """
    try:
        handle = path.open("rb")
    except OSError:
        return None
    with handle:
        try:
            total = path.stat().st_size
        except OSError:
            return None

        def boxes(start: int, end: int):
            offset = start
            while offset + 8 <= end:
                handle.seek(offset)
                head = handle.read(8)
                if len(head) < 8:
                    return
                size = int.from_bytes(head[0:4], "big")
                kind = head[4:8]
                header = 8
                if size == 1:
                    if offset + 16 > end:
                        return
                    ext = handle.read(8)
                    if len(ext) < 8:
                        return
                    size = int.from_bytes(ext, "big")
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

        moov = find(0, total, b"moov")
        if moov is None:
            return None
        for kind, trak_start, trak_end in boxes(*moov):
            if kind != b"trak":
                continue
            tkhd = find(trak_start, trak_end, b"tkhd")
            if tkhd is None or tkhd[1] - tkhd[0] < 8:
                continue
            handle.seek(tkhd[1] - 8)
            tail = handle.read(8)
            if len(tail) < 8:
                continue
            width = int.from_bytes(tail[0:4], "big") >> 16
            height = int.from_bytes(tail[4:8], "big") >> 16
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


PLAYBACK_BUILDING_MESSAGE = (
    "Your video is being made watchable here. You can go back to "
    "the app and keep working; the picture will appear when the "
    "copy is ready."
)
PLAYBACK_FAILED_MESSAGE = (
    "This video could not be made watchable here, so it cannot be "
    "shown yet. The edit still works from the words. The details "
    "were saved in a file called playback.log, in the playback "
    "folder inside this job's tye-session folder. Send that file "
    "back so the real cause can be found."
)
PLAYBACK_GETTING_READY = "Getting your video ready to watch…"

# The conversion log (v0.9.0): every watchable-copy build writes
# its state changes, one JSON object per line, to this file in the
# same playback folder as the copies themselves. A rehearsal (or
# a person) can read it afterwards and see exactly what happened:
# "building" when a build starts, "progress" as the converter's
# own percent crosses each tenth, "ready" when the copy lands,
# and "failed" when no copy could be made, with the failing
# attempt's last words kept on an "attempt_failed" line. Before
# this log existed, a conversion that stalled, died, or never
# started left no trace anywhere a rehearsal could read: the page
# just said "building" forever.
PLAYBACK_LOG_NAME = "playback.log"


def _playback_log_path(copy_path: Path) -> Path:
    """Where the conversion log for this copy's folder lives."""
    return copy_path.parent / PLAYBACK_LOG_NAME


def _log_playback_event(copy_path: Path, event: str, **fields) -> None:
    """Append one conversion state change to the playback log.

    Logging never breaks a build: a folder that cannot be written
    simply has no log, and the conversion carries on.
    """
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "copy": copy_path.name,
    }
    record.update(fields)
    try:
        copy_path.parent.mkdir(parents=True, exist_ok=True)
        with open(
            _playback_log_path(copy_path), "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError:
        pass

_ENCODERS_CACHE: Optional[frozenset] = None


def _ffmpeg_encoders() -> frozenset:
    """The encoder names this machine's ffmpeg offers (cached).

    A Mac's ffmpeg build may have no libx264 at all; the hardware
    encoder is then the only route, so what exists is checked
    before any conversion is attempted, never assumed.
    """
    global _ENCODERS_CACHE
    if _ENCODERS_CACHE is not None:
        return _ENCODERS_CACHE
    found = set()
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        try:
            out = subprocess.run(
                [ffmpeg, "-hide_banner", "-encoders"],
                capture_output=True, text=True, timeout=30,
            )
            for line in out.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    found.add(parts[1])
        except (OSError, subprocess.SubprocessError):
            pass
    _ENCODERS_CACHE = frozenset(found)
    return _ENCODERS_CACHE


def _playback_attempts() -> list:
    """Encoder argument sets for a watchable copy, best first.

    Each attempt is (input args, video args). On a Mac the
    hardware encoder is preferred (several times faster, and it
    leaves the CPU to transcription); the software ladder is the
    fallback: capped at 720p, veryfast, CRF 24 with a 5 Mbps
    ceiling, because a playback copy exists for watching and
    scrubbing, not for delivery. A machine with neither encoder
    yields no attempts, and the caller answers in plain words;
    the raw file is never served in the copy's place.
    """
    encoders = _ffmpeg_encoders()
    attempts = []
    if "h264_videotoolbox" in encoders:
        attempts.append((
            ["-hwaccel", "videotoolbox"],
            ["-c:v", "h264_videotoolbox", "-b:v", "5M"],
        ))
    if "libx264" in encoders:
        attempts.append((
            [],
            [
                "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "24", "-maxrate", "5M", "-bufsize", "10M",
                "-threads", "0",
            ],
        ))
    return attempts


_DURATION_CACHE: dict = {}


def _probe_duration_s(path: Path) -> Optional[float]:
    """The file's duration in seconds, or None when unreadable.

    Probed with ffprobe and cached per file version, the same
    discipline as the codec probe. Conversion progress is measured
    against this number; a file whose duration cannot be read
    still converts, just without a percent to report.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    key = f"{path}|{stat.st_mtime_ns}|{stat.st_size}"
    if key in _DURATION_CACHE:
        return _DURATION_CACHE[key]
    duration: Optional[float] = None
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            out = subprocess.run(
                [
                    ffprobe, "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "csv=p=0", str(path),
                ],
                capture_output=True, text=True, timeout=30,
            )
            lines = out.stdout.strip().splitlines()
            if lines:
                value = float(lines[0].strip())
                if value > 0:
                    duration = value
        except (OSError, subprocess.SubprocessError, ValueError):
            duration = None
    _DURATION_CACHE[key] = duration
    return duration


# A conversion's bounds (v0.8.0). The stall window is the real
# guard: an attempt whose encoded position has not advanced for
# this long is stuck, whatever its process thinks, and is killed
# so the ladder falls over to the next encoder. The caps scale
# with the video's own length, floored and ceilinged, so a short
# clip gets tight bounds and a long video gets room to finish,
# but no conversion can ever again sit in "building" for the
# better part of an hour the way two 30-minute attempt timeouts
# once allowed (Justin's 45-minute wait, October 27, 2026).
_PLAYBACK_STALL_S = 30.0
_PLAYBACK_ATTEMPT_FLOOR_S = 300.0
_PLAYBACK_ATTEMPT_CEIL_S = 1200.0
_PLAYBACK_TOTAL_FLOOR_S = 600.0
_PLAYBACK_TOTAL_CEIL_S = 1800.0


def _playback_bounds(duration_s: Optional[float]) -> tuple:
    """(per-attempt cap, total cap) in seconds for one copy."""
    if duration_s:
        attempt = min(
            _PLAYBACK_ATTEMPT_CEIL_S,
            max(_PLAYBACK_ATTEMPT_FLOOR_S, duration_s * 0.75),
        )
        total = min(
            _PLAYBACK_TOTAL_CEIL_S,
            max(_PLAYBACK_TOTAL_FLOOR_S, duration_s * 1.25),
        )
    else:
        attempt = _PLAYBACK_ATTEMPT_FLOOR_S
        total = _PLAYBACK_TOTAL_FLOOR_S
    return attempt, total


def _run_ffmpeg_attempt(command, duration_s, attempt_cap_s, report):
    """Run one conversion attempt, reporting progress as it goes.

    ffmpeg writes progress blocks to stdout (``-progress
    pipe:1``); a reader thread queues them and this loop watches
    the clock. Progress is the encoder's own position in the
    video, never a guess spun from elapsed time. An attempt whose
    encoded position has not advanced for ``_PLAYBACK_STALL_S``
    seconds is killed as stuck (a crawling hardware session is
    the known shape of that), and an attempt also dies at the
    attempt cap; either way the ladder moves on. Returns
    ``(ok, stderr_tail)``; ok only when ffmpeg exits 0.
    """
    import queue as queue_module

    log_fd, log_name = tempfile.mkstemp(
        prefix="tye-convert-", suffix=".log"
    )
    log_path = Path(log_name)
    proc = None
    try:
        with os.fdopen(log_fd, "wb") as log_file:
            proc = subprocess.Popen(
                command + ["-progress", "pipe:1"],
                stdout=subprocess.PIPE,
                stderr=log_file,
                text=True,
            )
            lines: "queue_module.Queue" = queue_module.Queue()

            def drain() -> None:
                try:
                    for line in proc.stdout:  # type: ignore[union-attr]
                        lines.put(line)
                except (OSError, ValueError):
                    pass
                lines.put(None)

            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            started = time.monotonic()
            last_advance = started
            best_position = -1.0
            last_reported = -1
            while True:
                try:
                    line = lines.get(timeout=0.2)
                except queue_module.Empty:
                    line = ""
                if line:
                    text = line.strip()
                    if text.startswith(
                        ("out_time_us=", "out_time_ms=")
                    ):
                        try:
                            position = (
                                int(text.split("=", 1)[1]) / 1_000_000.0
                            )
                        except ValueError:
                            position = None
                        if position is not None and position > best_position:
                            best_position = position
                            last_advance = time.monotonic()
                            if report is not None and duration_s:
                                pct = int(
                                    100.0 * position / duration_s
                                )
                                pct = max(0, min(99, pct))
                                if pct != last_reported:
                                    last_reported = pct
                                    report(pct)
                if proc.poll() is not None:
                    break
                now = time.monotonic()
                if now - last_advance > _PLAYBACK_STALL_S:
                    proc.kill()
                    break
                if now - started > attempt_cap_s:
                    proc.kill()
                    break
            try:
                proc.wait(timeout=30)
            except subprocess.SubprocessError:
                proc.kill()
            ok = proc.returncode == 0
        # Close the progress pipe so its handle does not linger
        # (the drain thread already guards its own read errors).
        if proc is not None and proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass
        try:
            tail = log_path.read_text(
                encoding="utf-8", errors="replace"
            ).strip()[-1000:]
        except OSError:
            tail = ""
        return ok, tail
    except OSError as exc:
        return False, str(exc)
    finally:
        log_path.unlink(missing_ok=True)


# One conversion at a time, across the whole app: the video the
# page is waiting for never queues behind background conversions
# of files nobody is watching, and the hardware encoder is never
# asked for two sessions at once.
_PLAYBACK_CONVERT_LOCK = threading.Lock()


def _convert_playback_copy(source: Path, dest: Path, progress=None) -> bool:
    """Build a watchable H.264 copy of ``source`` at ``dest``.

    The ladder above, at reduced process priority so a long
    conversion never starves transcription running alongside,
    one conversion at a time across the app, each attempt bounded
    and watched for stalls (see ``_run_ffmpeg_attempt``), with
    the whole build bounded too. ``progress`` (when given) is
    called with the converter's own percent as it advances. The
    copy is written to a scratch name and moved into place only
    when it is complete, so a stopped conversion never leaves a
    half-written file to serve. Returns False (with the detail
    in the server log) when no copy could be built.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print(
            f"[video] no ffmpeg available to convert {source.name}",
            file=sys.stderr,
        )
        _log_playback_event(
            dest, "failed", source=source.name,
            reason="no ffmpeg available on this machine",
        )
        return False
    attempts = _playback_attempts()
    if not attempts:
        print(
            "[video] this machine's ffmpeg has neither "
            "h264_videotoolbox nor libx264, so no watchable copy "
            f"can be made of {source.name}",
            file=sys.stderr,
        )
        _log_playback_event(
            dest, "failed", source=source.name,
            reason="no usable encoder on this machine",
        )
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (dest.name + ".part")
    nice = ["nice", "-n", "10"] if shutil.which("nice") else []
    duration_s = _probe_duration_s(source)
    attempt_cap, total_cap = _playback_bounds(duration_s)
    started = time.monotonic()
    _log_playback_event(
        dest, "start", source=source.name,
        duration_s=duration_s, attempts=len(attempts),
    )
    last_logged_decile = [-1]

    def report(pct: int) -> None:
        if progress is not None:
            progress(pct)
        decile = pct // 10
        if decile != last_logged_decile[0]:
            last_logged_decile[0] = decile
            _log_playback_event(dest, "progress", percent=pct)

    with _PLAYBACK_CONVERT_LOCK:
        for attempt_no, (input_args, video_args) in enumerate(
            attempts, start=1
        ):
            remaining = total_cap - (time.monotonic() - started)
            if remaining <= 0:
                break
            command = nice + [
                ffmpeg, "-y", "-v", "error",
                *input_args, "-i", str(source),
                # The comma inside min() is escaped: bare, ffmpeg
                # would read it as a filter-chain separator.
                "-vf", "scale=-2:min(720\\,ih)",
                *video_args,
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                # The scratch name has no video extension, so the
                # MP4 format is named outright.
                "-f", "mp4", str(tmp),
            ]
            ok, tail = _run_ffmpeg_attempt(
                command,
                duration_s,
                min(attempt_cap, remaining),
                report,
            )
            if ok and tmp.is_file() and tmp.stat().st_size > 0:
                tmp.replace(dest)
                _log_playback_event(
                    dest, "finish", source=source.name,
                    elapsed_s=round(time.monotonic() - started, 3),
                    size=dest.stat().st_size,
                )
                return True
            _log_playback_event(
                dest, "attempt_failed", source=source.name,
                attempt=attempt_no, detail=tail[-300:],
            )
            if tail:
                print(
                    f"[video] conversion of {source.name} failed:\n"
                    f"{tail}",
                    file=sys.stderr,
                )
            tmp.unlink(missing_ok=True)
    _log_playback_event(
        dest, "fail", source=source.name,
        elapsed_s=round(time.monotonic() - started, 3),
    )
    return False


# ---------------------------------------------------------------------------
# Watchable copies: built in the background, never inside a request,
# and the raw original is never served in a copy's place.
#
# A copy's life is tracked by copy path in the two sets below, so
# the state survives the config swaps that happen when a drop
# becomes the job. "Ready" is the copy file itself on disk (or a
# source that needs no copy); the sets only remember what the disk
# cannot say: that a build is running, or that one already failed.
# ---------------------------------------------------------------------------

_PLAYBACK_BUILD_LOCK = threading.Lock()
_PLAYBACK_BUILDING: set = set()
_PLAYBACK_FAILED: set = set()
# While a copy builds, the converter's own percent, by copy path:
# the number the page shows beside "Getting your video ready",
# so the wait is a measurement, not a promise.
_PLAYBACK_PROGRESS: dict = {}


def _copy_ready(copy_path: Path) -> bool:
    try:
        return copy_path.is_file() and copy_path.stat().st_size > 0
    except OSError:
        return False


def _needs_copy(source: Path) -> bool:
    """Whether this file needs a watchable copy at all.

    A file ffprobe cannot read at all (codec None) is not a video
    any player could show either way; it keeps the legacy
    byte-serve so odd files and test fixtures behave as they
    always have. A real codec that is not H.264 always needs one.
    """
    codec = _probe_video_codec(source)
    return codec is not None and codec != BROWSER_SAFE_CODEC


def _playback_state_for(source: Path, copy_path: Path) -> str:
    """One of ready, building, failed, unstarted, for the page."""
    if not _needs_copy(source):
        return "ready"
    if _copy_ready(copy_path):
        return "ready"
    key = str(copy_path)
    with _PLAYBACK_BUILD_LOCK:
        if key in _PLAYBACK_BUILDING:
            return "building"
        if key in _PLAYBACK_FAILED:
            return "failed"
    return "unstarted"


def _ensure_playback_copy_for(source: Path, copy_path: Path) -> None:
    """Start this copy's build in the background, if it needs one.

    Called when a drop finishes and when a job is loaded, never
    from inside a play request's critical path (a serve may kick
    it as a last resort, but answers "still being made" rather
    than waiting). A failed build is remembered for the life of
    the process, so a broken conversion is not retried on every
    poll; the next server start tries again.
    """
    if not _needs_copy(source) or _copy_ready(copy_path):
        return
    key = str(copy_path)
    with _PLAYBACK_BUILD_LOCK:
        if key in _PLAYBACK_BUILDING or key in _PLAYBACK_FAILED:
            return
        _PLAYBACK_BUILDING.add(key)

    def build() -> None:
        def report(pct: int) -> None:
            with _PLAYBACK_BUILD_LOCK:
                _PLAYBACK_PROGRESS[key] = pct

        _log_playback_event(
            copy_path, "building", source=source.name
        )
        log_session_dir = (
            copy_path.parent.parent
            if copy_path.parent.name == "playback"
            and copy_path.parent.parent.name == "tye-session"
            else None
        )
        session_log.log_stage(
            log_session_dir, "playback build", "start", source.name
        )
        try:
            ok = _convert_playback_copy(
                source, copy_path, progress=report
            )
        except Exception:
            ok = False
        with _PLAYBACK_BUILD_LOCK:
            _PLAYBACK_BUILDING.discard(key)
            _PLAYBACK_PROGRESS.pop(key, None)
            if not ok:
                _PLAYBACK_FAILED.add(key)
        # The end state, written where a rehearsal can read it:
        # a build always lands on exactly one of these two lines.
        _log_playback_event(
            copy_path, "ready" if ok else "failed",
            source=source.name,
        )
        session_log.log_stage(
            log_session_dir, "playback build",
            "finish" if ok else "fail", source.name,
        )

    threading.Thread(target=build, daemon=True).start()


def _playback_progress_for(source: Path, copy_path: Path) -> Optional[int]:
    """How far this copy's build has gotten (0 to 99), or None.

    None whenever no honest percent exists: the source needs no
    copy, the copy is ready, the build has not started, or the
    source's length could not be read. The percent is the
    converter's own reported position in the video, never an
    estimate spun from elapsed time.
    """
    if not _needs_copy(source) or _copy_ready(copy_path):
        return None
    with _PLAYBACK_BUILD_LOCK:
        return _PLAYBACK_PROGRESS.get(str(copy_path))


def _playback_state(config: "WebAppConfig", name: str) -> str:
    return _playback_state_for(
        config.job_dir / name, _playback_copy_path(config, name)
    )


def _playback_view(config: "WebAppConfig", videos: list) -> tuple:
    """(states, percents) for the job summary, by video name.

    A video whose copy has never been started is started here,
    on the poll the page already runs: "unstarted" must never be
    a resting state the page can poll forever while nothing is
    actually building (the eternal "building" of the 0.8.0
    session, where a surface could wait on a conversion nobody
    had kicked). Starting is guarded and cheap, so a summary
    that finds a build already running or failed changes nothing.
    """
    states = {}
    for name in videos:
        state = _playback_state(config, name)
        if state == "unstarted":
            _ensure_playback_copy(config, name)
            state = _playback_state(config, name)
        states[name] = state
    progress = {
        name: _playback_progress(config, name) for name in videos
    }
    return states, progress


def _playback_progress(config: "WebAppConfig", name: str) -> Optional[int]:
    return _playback_progress_for(
        config.job_dir / name, _playback_copy_path(config, name)
    )


def _ensure_playback_copy(config: "WebAppConfig", name: str) -> None:
    _ensure_playback_copy_for(
        config.job_dir / name, _playback_copy_path(config, name)
    )


def _kick_playback_copies(config: "WebAppConfig") -> None:
    """Start watchable copies for every video of the loaded job."""
    if not config.has_job:
        return
    for name in _job_videos(config.job_dir):
        _ensure_playback_copy(config, name)


def _playback_target(config: "WebAppConfig", name: str) -> Optional[Path]:
    """The file to serve for one of the job's videos, or None.

    The job's own file when it needs no copy; the finished copy
    when one exists. None while the copy is still being made or
    could not be made: the raw original is never served in the
    copy's place, and that answer is never cached as a decision.
    """
    source = config.job_dir / name
    if not _needs_copy(source):
        return source
    copy_path = _playback_copy_path(config, name)
    if _copy_ready(copy_path):
        return copy_path
    _ensure_playback_copy(config, name)
    return None


# ---------------------------------------------------------------------------
# Which video the review player shows (FIX A, v0.10)
#
# The 0.9.0 player was only ever pointed at the job folder's own
# videos, so after Make the edit it went on playing the original
# at full length while the words showed the cuts. The edited video
# is the session's splice; whether the player may show it as "the
# edited video" is decided by a stamp: every build of the splice
# records a hash of the plan it was built from, and the splice is
# current only while the plan still hashes the same. Anything
# else (no splice, a stale splice, a failed build) is shown for
# what it is, in plain words, never quietly dressed as the edit.
# ---------------------------------------------------------------------------

SPLICE_STAMP_FILENAME = "splice_stamp.json"
SPLICE_FAILED_FILENAME = "splice_failed.json"


def _plan_stamp_of(plan: dict) -> str:
    """A short hash of one plan dict."""
    blob = json.dumps(plan, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _plan_stamp(config: WebAppConfig) -> str:
    """A short hash of the plan the editor would build from now."""
    return _plan_stamp_of(_effective_cuts(config))


def _record_splice_built(config: WebAppConfig) -> None:
    """Stamp the session splice as built from the current plan."""
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    _write_json(
        session / SPLICE_STAMP_FILENAME,
        {"stamp": _plan_stamp(config), "at": _utc_now()},
    )
    (session / SPLICE_FAILED_FILENAME).unlink(missing_ok=True)


def _record_splice_failed(config: WebAppConfig, reason: str) -> None:
    """Remember, in plain words, why the edited video is missing."""
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    _write_json(
        session / SPLICE_FAILED_FILENAME,
        {"reason": reason, "at": _utc_now()},
    )


# -- Run video snapshots: the Done bubble's Undo (0.26) -------------------
#
# "Nothing is final until I say so": a chat run's Done bubble
# carries an Undo that restores the video to before the run.
# The plan side of that restore is the run's checkpoint (the
# per-action machinery above the thread actions). The rendered
# video is derived state and is never checkpointed, so each run
# also keeps two plain copies of the session splice here: the
# "before" copy, taken when the run's progress entry is first
# written (the state the run started from), and the "after"
# copy, taken when its Done payload is stored (the video the
# run built). Undo and redo then restore the plan from the
# checkpoint and the video from the matching copy: no model
# run, no transcription, and no re-render.
#
# The store lives in the launch session: that folder means the
# same thing before and after a job is adopted (a job-mode
# server's launch home is the job folder itself), so a snapshot
# taken when a run starts at the front door is still findable
# when its Done entry is undone from the job's thread.

RUN_VIDEOS_DIR_NAME = "run-videos"


def _run_video_store_dir(config: WebAppConfig) -> Path:
    launch = WebAppConfig(None, launch_home=config.launch_home)
    return launch.session_dir / RUN_VIDEOS_DIR_NAME


def _run_video_key(entry_id) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(entry_id))


def _snapshot_run_video(
    config: WebAppConfig, entry_id, side: str
) -> None:
    """Save the session splice as one side ("before"/"after")
    of a run, or record that no edited video existed then."""
    store = _run_video_store_dir(config)
    store.mkdir(parents=True, exist_ok=True)
    key = _run_video_key(entry_id)
    record_path = store / f"{key}.json"
    record: dict = {}
    if record_path.is_file():
        try:
            loaded = json.loads(record_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                record = loaded
        except (OSError, ValueError):
            record = {}
    saved = store / f"{key}-{side}.mp4"
    source = config.session_dir / SESSION_VIDEO_NAME
    if source.is_file():
        shutil.copyfile(source, saved)
        record[side] = {"exists": True}
    else:
        saved.unlink(missing_ok=True)
        record[side] = {"exists": False}
    _write_json(record_path, record)


def _load_run_video_record(
    config: WebAppConfig, entry_id
) -> Optional[dict]:
    path = (
        _run_video_store_dir(config)
        / f"{_run_video_key(entry_id)}.json"
    )
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _drop_session_playback_copy(config: WebAppConfig) -> None:
    """Forget any watchable copy of the session splice.

    A playback copy is made from the file it stands in for;
    after the splice is replaced or removed, a copy left behind
    would keep serving the old video under the same name.
    """
    copy_path = _playback_copy_path(config, SESSION_VIDEO_NAME)
    copy_path.unlink(missing_ok=True)
    key = str(copy_path)
    with _PLAYBACK_BUILD_LOCK:
        _PLAYBACK_BUILDING.discard(key)
        _PLAYBACK_FAILED.discard(key)


def _restore_run_video(
    config: WebAppConfig, entry_id, side: str
) -> None:
    """Put one side of a run's saved video back as the splice.

    The checkpoint restore has already put the plan back; the
    stamp is rewritten from that plan, so the splice reads as
    the current video of the restored edit. A side that had no
    edited video (a first edit's "before") removes the splice
    instead: the app then serves the original, exactly as it
    did before the run. A run with no snapshot record at all
    (its Done entry predates these snapshots) falls back to
    rebuilding the splice from the restored plan when this job
    renders through the app's own splice, and to removing the
    stale splice when it cannot, so the served video never
    quietly shows an edit the plan no longer holds.
    """
    session = config.session_dir
    target = session / SESSION_VIDEO_NAME
    record = _load_run_video_record(config, entry_id)
    if record is not None:
        saved = (
            _run_video_store_dir(config)
            / f"{_run_video_key(entry_id)}-{side}.mp4"
        )
        slot = record.get(side) or {}
        if slot.get("exists") and saved.is_file():
            session.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(saved, target)
            _record_splice_built(config)
        else:
            target.unlink(missing_ok=True)
            (session / SPLICE_STAMP_FILENAME).unlink(missing_ok=True)
            (session / SPLICE_FAILED_FILENAME).unlink(missing_ok=True)
        _drop_session_playback_copy(config)
        return
    # No snapshot record: rebuild or remove, never leave stale.
    rebuilt = False
    if (session / "revised_cuts.json").is_file() and config.has_job:
        try:
            source_name = intake_build.intake_source_video(
                config.job_dir
            )
        except Exception:
            source_name = None
        if source_name:
            source = config.job_dir / source_name
            try:
                media = speech.probe_media(source)
                has_audio = bool(media.get("has_audio", True))
            except Exception:
                has_audio = True
            try:
                rebuilt = (
                    _render_session_splice(config, source, has_audio)
                    is None
                )
            except Exception:
                rebuilt = False
    if not rebuilt:
        target.unlink(missing_ok=True)
        (session / SPLICE_STAMP_FILENAME).unlink(missing_ok=True)
        (session / SPLICE_FAILED_FILENAME).unlink(missing_ok=True)
        _drop_session_playback_copy(config)


def _render_framing(
    config: WebAppConfig, source: Path, aspect
) -> tuple:
    """The framing choice for one render, as (crop, note).

    ``aspect`` is "9:16" or "16:9" when the creator picked a
    shape, anything else when they kept the recorded framing:
    then there is no crop and no note, and the render is exactly
    what it has always been. With a shape, the crop window comes
    from the session's saved teaching framing, so the face lands
    where it sat in the reference; without one, the window is
    centered and the note says so plainly.
    """
    if aspect not in framing.ASPECTS:
        return None, None
    try:
        media = speech.probe_media(source)
        width = media.get("width")
        height = media.get("height")
    except speech.TranscribeError:
        width = height = None
    if not width or not height:
        return None, (
            f"Framing: the {aspect} shape was not applied, "
            "because this video's size could not be read."
        )
    _ensure_creator_store(config)
    profile = teaching.load_framing_profile(
        _creator_framing_dir(config), aspect
    )
    if profile is None:
        # Until the Message 3 framing work, a profile saved from
        # the other shape still serves, exactly as the single
        # profile served before profiles were kept per shape.
        profile = teaching.load_framing_profile(
            _creator_framing_dir(config)
        )
    matched = bool(profile and profile.get("found"))
    window = framing.crop_window(
        profile if matched else None, width, height, aspect
    )
    x, y, w, h = framing.crop_pixels(window, width, height)
    out_w, out_h = framing.output_size(w, h, aspect)
    if matched:
        note = (
            f"Framing: cut to {aspect} around your face, the "
            "way your teaching video is framed."
        )
    else:
        note = (
            f"Framing: cut to {aspect} with a center crop. No "
            "teaching framing is saved yet, so the face "
            "position could not be matched."
        )
    return (x, y, w, h, out_w, out_h), note


# -- Finish (Message 3): versions, framing match, export, project --------
#
# One main edit; the versions are shapes of it. The framing for a
# shape comes from the reference of that shape saved in the
# creator store on Train (teaching.save_framing_profile), matched
# against the face found in this job's own video
# (framing.match_window). Nothing here touches the plan: a nudge
# or a render changes only the shape's own state and file.

FINISH_MEASURED_FILENAME = "finish_measured.json"
FINISH_NEW_PROFILE_FILENAME = "finish_new_profile.json"

_FINISH_NOTE_LINES = {
    "no_reference": (
        "No reference of this shape has been learned yet, so "
        "this is a centered crop you can nudge. Teach me with a "
        "finished video of this shape and I will match its "
        "framing."
    ),
    "no_face_in_new": (
        "I could not find your face in this video, so this is a "
        "centered crop you can nudge."
    ),
    "too_tight": (
        "Your face fills more of this video than it does in your "
        "reference, so this is the widest crop that fits; your "
        "face will sit a little larger than in the reference."
    ),
    "may_look_soft": (
        "This crop is small, so the picture may look soft at "
        "full size."
    ),
}


def _finish_source(config: WebAppConfig) -> Optional[Path]:
    """The video the Finish renders are cut from, or None."""
    if not config.has_job:
        return None
    name = intake_build.intake_source_video(config.job_dir)
    if name:
        path = config.job_dir / name
        if path.is_file():
            return path
    if config.source_video:
        path = Path(config.source_video)
        if path.is_file():
            return path
    return None


def _finish_new_profile(config: WebAppConfig) -> Optional[dict]:
    """The face profile of this job's own video, cached.

    Sampled from the plan's KEPT footage only (brief 3: the same
    face sampling, run on the footage the edit actually shows),
    once per source and plan (the cache key carries both) and
    read back afterwards: the Finish screen opens fast, and the
    match it shows is the match the render uses.
    """
    source = _finish_source(config)
    if source is None:
        return None
    try:
        stat = source.stat()
        key = f"{source.name}:{stat.st_size}:{int(stat.st_mtime)}"
    except OSError:
        key = source.name
    if config.has_job and (config.session_dir / "revised_cuts.json").is_file():
        key += ":" + _plan_stamp(config) + ":kept21"
        cuts = _effective_cuts(config)
        # The kept moments, spread by the one ruler's count:
        # the same ruler Train reads the reference with and the
        # render check reads the output with, so the three
        # shares can honestly be compared.
        times = framing.kept_range_times(
            cuts.get("kept_ranges") or [],
            sample_count=framing.RULER_SAMPLE_COUNT,
        )
    else:
        times = []
    cache_path = config.session_dir / FINISH_NEW_PROFILE_FILENAME
    cached = _load_session_json(config, FINISH_NEW_PROFILE_FILENAME, {})
    if isinstance(cached, dict) and cached.get("key") == key:
        return cached.get("profile")
    profile = framing.ruler_profile(source, times=times or None)
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(cache_path, {"key": key, "profile": profile})
    return profile


def _finish_window(config: WebAppConfig, aspect: str) -> tuple:
    """(window, ref_profile, note_keys) for one shape.

    The reference is the one saved for THIS shape; another
    shape's profile never stands in, so the no-reference line is
    honest. The window carries the shape's saved nudge.
    """
    source = _finish_source(config)
    if source is None:
        return None, None, ["no_reference"]
    try:
        media = speech.probe_media(source)
        width = media.get("width")
        height = media.get("height")
    except speech.TranscribeError:
        width = height = None
    if not width or not height:
        return None, None, ["no_reference"]
    _ensure_creator_store(config)
    ref = teaching.load_framing_profile(
        _creator_framing_dir(config), aspect
    )
    if ref is not None and not ref.get("found"):
        ref = None
    new = _finish_new_profile(config)
    state = finish.load_state(config.session_dir)
    off = state["offsets"].get(aspect) or {"x": 0.0, "y": 0.0}
    window = framing.match_window(
        ref, new, int(width), int(height), aspect,
        offset_x=float(off.get("x") or 0.0),
        offset_y=float(off.get("y") or 0.0),
    )
    return window, ref, list(window.get("notes") or [])


def _finish_shape_view(config: WebAppConfig, aspect: str) -> dict:
    """Everything the Finish tab for one shape can honestly say."""
    session = config.session_dir
    state = finish.load_state(session)
    view = {
        "aspect": aspect,
        "chosen": aspect in state["versions"],
        "offset": state["offsets"].get(aspect) or {"x": 0.0, "y": 0.0},
        "has_render": (session / finish.finish_filename(aspect)).is_file(),
        "file": finish.finish_filename(aspect),
        "reference": None,
        "match_line": "",
        "notes": [],
        "measured": None,
    }
    if not config.has_job:
        view["notes"] = [_FINISH_NOTE_LINES["no_reference"]]
        return view
    # One shape's framing read must never take the whole
    # Finish screen down with it (0.27.1, P1b item 8): on his
    # Mac the state call died on the real video and the page
    # showed no shapes at all. A failed read degrades to the
    # honest no-reference notes for this shape alone.
    try:
        _ensure_creator_store(config)
        ref = teaching.load_framing_profile(
            _creator_framing_dir(config), aspect
        )
        if ref is not None and ref.get("found"):
            face = ref["face"]
            view["reference"] = {
                "center_x": face["center_x"],
                "center_y": face["center_y"],
                "width": face["width"],
            }
        window, _ref, note_keys = _finish_window(config, aspect)
        view["notes"] = [
            _FINISH_NOTE_LINES[key] for key in note_keys
            if key in _FINISH_NOTE_LINES
        ]
        if window is not None and window.get("matched") and window.get("predicted"):
            report = framing.match_report(
                ref, {"found": True, "face": window["predicted"]}
            )
            if report.get("ok"):
                view["match_line"] = "Framing plan: " + report["line"]
    except Exception:
        view["notes"] = [_FINISH_NOTE_LINES["no_reference"]]
    measured = _load_session_json(config, FINISH_MEASURED_FILENAME, {})
    if isinstance(measured, dict) and aspect in measured:
        view["measured"] = measured[aspect]
        if (measured[aspect] or {}).get("line"):
            view["match_line"] = measured[aspect]["line"]
    return view


def _finish_cost_line(config: WebAppConfig) -> str:
    """The cost of this run, in plain words, never invented.

    The figure is the job session's own recorded cost entries
    (the costs module's ledger). A run that spent nothing on
    model calls (an offline mock run, or local passes only)
    says so plainly instead of showing a made-up dollar figure.
    """
    if not config.has_job:
        return "No run yet, so there is no cost to show."
    entries = costs.load_session_entries(config.session_dir)
    total = sum(float(e.get("usd") or 0.0) for e in entries)
    if total >= 0.005:
        return f"Cost of this run: {costs.format_usd(total)}."
    if entries:
        return (
            "Cost of this run: nothing in model calls. It ran on "
            "this computer (offline mock and local passes only)."
        )
    return (
        "Cost of this run: nothing recorded. It ran on this "
        "computer, with no model calls."
    )


def _finish_state_view(config: WebAppConfig) -> dict:
    state = finish.load_state(config.session_dir)
    return {
        "versions": state["versions"],
        "shapes": {
            aspect: _finish_shape_view(config, aspect)
            for aspect in finish.ASPECTS
        },
        "cost_line": _finish_cost_line(config),
        "has_job": bool(config.has_job),
        "has_plan": (
            config.has_job
            and (config.session_dir / "revised_cuts.json").is_file()
        ),
    }


# -- The project file ------------------------------------------------------
#
# Export saves one alongside the video: the plan, the notes, the
# style, the Finish choices, and the names of the source videos
# it belongs to. Opening one puts all of that back into the
# session, so he picks up where he left off. The recent list
# lives in the launch home, so it survives across jobs.

PROJECTS_DIR_NAME = "projects"


def _projects_dir(config: WebAppConfig) -> Path:
    return config.launch_home / PROJECTS_DIR_NAME


def _project_payload(config: WebAppConfig) -> dict:
    session = config.session_dir
    plan = _load_session_json(config, "revised_cuts.json", None)
    videos = []
    if config.has_job:
        videos = [
            name for name in _job_videos(config.job_dir)
            if name.lower().endswith((".mp4", ".mov", ".m4v", ".webm"))
        ]
    return {
        "kind": "tye-project",
        "version": 1,
        "saved_at": _utc_now(),
        "job_name": config.job_dir.name if config.has_job else "",
        "source_videos": videos,
        "plan": plan,
        "edit_notes": _load_edit_notes(config),
        "style": _current_style(config),
        "finish_state": finish.load_state(session),
    }


def _save_project(config: WebAppConfig) -> dict:
    """Save the project file and register it in Recent projects."""
    payload = _project_payload(config)
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    _write_json(session / finish.PROJECT_FILENAME, payload)
    stamp = payload["saved_at"].replace(":", "").replace("-", "")
    project_id = f"project-{stamp}"
    folder = _projects_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    _write_json(folder / f"{project_id}.json", payload)
    index_path = folder / "index.json"
    index = []
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            index = []
    if not isinstance(index, list):
        index = []
    index = [e for e in index if e.get("id") != project_id]
    index.insert(
        0,
        {
            "id": project_id,
            "job_name": payload["job_name"],
            "saved_at": payload["saved_at"],
        },
    )
    _write_json(index_path, index[:20])
    return {"id": project_id, "saved_at": payload["saved_at"]}


def _recent_projects(config: WebAppConfig) -> list:
    index_path = _projects_dir(config) / "index.json"
    if not index_path.is_file():
        return []
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(index, list):
        return []
    return [
        {
            "id": str(e.get("id") or ""),
            "job_name": str(e.get("job_name") or ""),
            "saved_at": str(e.get("saved_at") or ""),
        }
        for e in index
        if isinstance(e, dict) and e.get("id")
    ]


def _open_project(config: WebAppConfig, project_id: str) -> tuple:
    """Restore a saved project into the current session.

    Returns (ok, plain message). The project's plan, notes, and
    Finish choices go back into the session; the splice stamp no
    longer matches the restored plan, so the Edit view rebuilds
    its video from the restored plan the same way it does after
    any hand change. A project whose source videos are not the
    ones in this job is refused in plain words: a plan cut for
    one video must never land on another.
    """
    if not config.has_job:
        return False, "Bring in a video first, then open the project."
    path = _projects_dir(config) / f"{project_id}.json"
    if not path.is_file():
        session_copy = config.session_dir / finish.PROJECT_FILENAME
        if project_id == "this-session" and session_copy.is_file():
            path = session_copy
        else:
            return False, "That project file is not here anymore."
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "That project file could not be read."
    if not isinstance(payload, dict) or payload.get("kind") != "tye-project":
        return False, "That file is not a project from this app."
    wanted = set(payload.get("source_videos") or [])
    current = set(_job_videos(config.job_dir))
    if wanted and not (wanted & current):
        return (
            False,
            "That project belongs to a different video ("
            + ", ".join(sorted(wanted))
            + "), which is not the video in this job, so it was "
            "not opened.",
        )
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    if isinstance(payload.get("plan"), dict):
        _write_json(session / "revised_cuts.json", payload["plan"])
    if isinstance(payload.get("edit_notes"), dict):
        _save_edit_notes(config, payload["edit_notes"])
    if isinstance(payload.get("finish_state"), dict):
        finish.save_state(session, payload["finish_state"])
    _write_json(session / finish.PROJECT_FILENAME, payload)
    return True, "Project opened. Your edit is back the way you left it."


def _render_session_splice(
    config: WebAppConfig, source_video: Path, has_audio: bool,
    crop: tuple = None,
) -> Optional[str]:
    """Build the session splice from the current plan, and stamp it.

    Returns None when the video was built, or the plain sentence
    saying why it could not be. The failure is recorded either
    way, so the page can say it out loud instead of quietly
    showing the original as if it were the edit.
    """
    session_log.log_stage(config.session_dir, "render", "start")
    # Smooth the joins (v0.15, Batch I, item 30): the stored
    # choice decides whether the built video's sound crossfades
    # at the joins; the picture cuts either way.
    crossfade_s = (
        intake_build.CROSSFADE_S
        if _front_door_settings(config).get("crossfade", True)
        else 0.0
    )
    try:
        intake_build.render_splice(
            source_video,
            _effective_cuts(config),
            config.session_dir / SESSION_VIDEO_NAME,
            has_audio=has_audio,
            crossfade_s=crossfade_s,
            crop=crop,
        )
    except speech.TranscribeError as exc:
        _record_splice_failed(config, str(exc))
        detail = str(exc)
        ffmpeg_tail = getattr(exc, "ffmpeg_stderr", "")
        if ffmpeg_tail:
            detail = f"{detail} | ffmpeg: {ffmpeg_tail[-250:]}"
        session_log.log_stage(
            config.session_dir, "render", "fail", detail
        )
        return str(exc)
    _record_splice_built(config)
    # Every export path renders at -14 (0.27.1, P2 item 10):
    # the Done message's export IS this file, and it used to
    # ship at the splice's own loudness (-17.9 on his Mac).
    # A loudness failure never fails the render: the file is
    # left exactly as built and the splice still counts.
    if has_audio:
        try:
            finish.normalize_loudness_in_place(
                config.session_dir / SESSION_VIDEO_NAME
            )
        except Exception:
            pass
    session_log.log_stage(config.session_dir, "render", "finish")
    return None


def _splice_state(config: WebAppConfig) -> dict:
    """The session splice: whether it exists, and whether it is
    still the video of the current plan."""
    session = config.session_dir
    exists = (session / SESSION_VIDEO_NAME).is_file()
    current = False
    stamp = None
    if exists:
        record = _load_session_json(config, SPLICE_STAMP_FILENAME, {})
        if isinstance(record, dict):
            stamp = record.get("stamp")
        if stamp is not None:
            try:
                current = stamp == _plan_stamp(config)
            except (OSError, ValueError):
                current = False
    failed = None
    failed_record = _load_session_json(config, SPLICE_FAILED_FILENAME, {})
    if isinstance(failed_record, dict) and failed_record.get("reason"):
        failed = str(failed_record["reason"])
    return {
        "exists": exists,
        "current": current,
        "stamp": stamp if current else None,
        "failed": failed,
    }


def _watching_state(config: WebAppConfig) -> dict:
    """What the review player should show, and what it is called.

    "edited" only when a splice exists and is current. Otherwise
    the job's own first video, named the original, with plain
    flags for the two honest extras: the plan has moved since the
    splice was built (stale), or the plan was changed in the app
    and no edited video was ever built (not_built), or the last
    build failed (failed, with its plain reason).
    """
    videos = _job_videos(config.job_dir) if config.has_job else []
    state = {
        "kind": "original",
        "name": videos[0] if videos else None,
        "stale": False,
        "not_built": False,
        "failed": None,
        "stamp": None,
        "building": False,
    }
    if not config.has_job:
        return state
    try:
        splice = _splice_state(config)
    except (OSError, ValueError):
        return state
    state["failed"] = splice["failed"]
    if splice["exists"] and splice["current"]:
        state.update(
            kind="edited", name=SESSION_VIDEO_NAME, stamp=splice["stamp"]
        )
        # The edit is ready as a plan, but the file the player
        # can show may still be being made. That is a building
        # state, named plainly, so the page never calls what is
        # on screen the edited video before it is ready (FIX A).
        splice_source = config.session_dir / SESSION_VIDEO_NAME
        state["building"] = (
            _playback_state_for(
                splice_source, _playback_copy_path(config, SESSION_VIDEO_NAME)
            )
            != "ready"
        )
    elif splice["exists"]:
        state["stale"] = True
    elif (config.session_dir / "revised_cuts.json").is_file():
        state["not_built"] = True
    return state


def _session_video_target(config: WebAppConfig) -> Optional[Path]:
    """The file to serve for the session splice, or None.

    The same discipline as a job video: the splice itself when it
    needs no copy (it is built browser-safe), its finished
    watchable copy when one is needed, and None while that copy
    is being made or could not be made.
    """
    source = config.session_dir / SESSION_VIDEO_NAME
    if not source.is_file():
        return None
    if not _needs_copy(source):
        return source
    copy_path = _playback_copy_path(config, SESSION_VIDEO_NAME)
    if _copy_ready(copy_path):
        return copy_path
    _ensure_playback_copy_for(source, copy_path)
    return None


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
SAMPLE_JOB_DIR_NAME = "sample-job"
SAMPLE_JOB_FILES = (
    "transcript.json",
    "cuts.json",
    "profile.json",
    "feedback.json",
)
NO_JOB_MESSAGE = (
    "No job is loaded yet. Drop a runner job folder on the page, "
    "or try the built-in sample."
)
# The one plain sentence for a job whose files are not all on disk
# (a folder dropped before its pieces landed, a file removed
# mid-session). It is the only wording a person ever sees for this
# case: in the page's banner, in its status lines, on the plain
# page a browser navigation lands on, and in the API's JSON for
# programmatic callers. Raw machine wording never reaches a screen.
JOB_INCOMPLETE_MESSAGE = (
    "That job is missing a file it needs. Drop the folder again, "
    "or try the built-in sample."
)
# The two files every job action reads. A job missing either one is
# not whole: its actions wait out of sight until it is.
JOB_REQUIRED_FILES = ("transcript.json", "cuts.json")
_CHUNK = 256 * 1024


def _job_files_missing(config: WebAppConfig) -> list:
    """The required job files not on disk right now ([] when whole)."""
    if not config.has_job:
        return []
    return [
        name
        for name in JOB_REQUIRED_FILES
        if not (config.job_dir / name).is_file()
    ]


class WebAppConfig:
    """Everything the handler needs, fixed at server start, except the
    session sign-in (see ``session_api_key``).

    A config either runs on a job folder (``job_dir`` set) or starts
    empty (``job_dir`` None): the no-job front door. Either way it
    carries a ``launch_home``, the neutral folder the launch owns:
    in no-job mode the session and the staged drops live there, and
    the built-in sample is staged there when it is loaded. When a
    dropped or sampled job becomes the current job, the new config
    inherits the same launch home, so the intake anchor never moves.
    """

    def __init__(
        self,
        job_dir: Optional[Path] = None,
        render_py: Optional[str] = None,
        source_video: Optional[str] = None,
        cleaned_wav: Optional[str] = None,
        launch_home: Optional[Path] = None,
    ):
        self.job_dir = Path(job_dir) if job_dir is not None else None
        if launch_home is not None:
            self.launch_home = Path(launch_home)
        elif self.job_dir is not None:
            self.launch_home = self.job_dir
        else:
            self.launch_home = Path.home() / ".talk-to-your-editor"
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
        if self.job_dir is not None and not self.render_py:
            candidate = self.job_dir / RENDER_SCRIPT_NAME
            if candidate.is_file():
                self.render_py = str(candidate)
                self.render_autowired.append("render_py")
        if self.job_dir is not None and not self.cleaned_wav:
            candidate = self.job_dir / CLEANED_WAV_NAME
            if candidate.is_file():
                self.cleaned_wav = str(candidate)
                self.render_autowired.append("cleaned_wav")
        if self.job_dir is not None and not self.source_video:
            found = _autodetect_source_video(self.job_dir)
            if found:
                self.source_video = found
                self.render_autowired.append("source_video")
        # The one piece of state that may change while the server
        # runs: a Meta sign-in pasted on the page. It is kept in this
        # attribute only, in memory: never written to a file, never
        # sent back to the page, and gone when the server stops.
        self.session_api_key: Optional[str] = None
        # The same for an ElevenLabs key pasted on the page
        # (v0.6.1): session only, unless the creator saves it on
        # this Mac through the sign-in card's own action.
        self.session_elevenlabs_key: Optional[str] = None
        # The same for the two hosted Spark routes' keys (v0.8.1):
        # Baseten and Fireworks keys pasted on the page live here,
        # session only, under the same discipline.
        self.session_baseten_key: Optional[str] = None
        self.session_fireworks_key: Optional[str] = None
        # Watchable-copy builds are tracked by copy path in the
        # module-level registry (see _ensure_playback_copy_for),
        # so their state survives this config being swapped when
        # a drop becomes the job.

    @property
    def has_job(self) -> bool:
        """Whether a job folder is loaded. False is the front door:
        the server is up, and no job has arrived yet."""
        return self.job_dir is not None

    @property
    def session_dir(self) -> Path:
        """Where this session's state lives.

        With a job, inside the job folder, as always. With no job,
        inside the launch home, so the sign-in, the history, and the
        staged drops have one known place before any job exists.
        """
        if self.job_dir is not None:
            return self.job_dir / SESSION_DIR_NAME
        return self.launch_home / SESSION_DIR_NAME

    def job_label(self) -> str:
        """The job's name for the page, or plain words when none."""
        if self.job_dir is not None:
            return self.job_dir.name
        return "no job yet"

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

    @property
    def is_intake_job(self) -> bool:
        """Whether the loaded job was born in this app from a raw
        video (it carries the intake marker). Intake jobs render
        with the app's own splice; runner jobs keep theirs."""
        return intake_build.is_intake_job(self.job_dir)

    def elevenlabs_key(self) -> Optional[str]:
        """The creator's ElevenLabs key, when the server has one.

        Used only by the intake's word route (Scribe). A key pasted
        on the page for this session wins over the environment the
        server started with, the way the Meta key works. When
        neither has it, the key saved on this Mac is read from its
        file, so what Settings reports as saved and what a run can
        use can never be two different things: until this fallback
        existed, a server that started without the launcher's
        export (or any start that missed it) showed the key as
        saved while the run itself had no key to send.
        """
        if self.session_elevenlabs_key:
            return self.session_elevenlabs_key
        return (
            os.environ.get("ELEVENLABS_API_KEY")
            or _read_mac_key("elevenlabs")
        )

    def baseten_key(self) -> Optional[str]:
        """The creator's Baseten key, when the server has one.

        Used only by Spark-tier model work when the Baseten route
        is chosen. A key pasted on the page for this session wins
        over the environment the server started with, and the key
        saved on this Mac is the last resort, the way the other
        keys work.
        """
        if self.session_baseten_key:
            return self.session_baseten_key
        return (
            os.environ.get("BASETEN_API_KEY")
            or _read_mac_key("baseten")
        )

    def fireworks_key(self) -> Optional[str]:
        """The creator's Fireworks key, when the server has one.

        Used only by Spark-tier model work when the Fireworks
        route is chosen, under the same discipline as the Baseten
        key.
        """
        if self.session_fireworks_key:
            return self.session_fireworks_key
        return (
            os.environ.get("FIREWORKS_API_KEY")
            or _read_mac_key("fireworks")
        )

    def hosted_key(self, route: str) -> Optional[str]:
        """The key for one hosted Spark route, or None."""
        if route == "baseten":
            return self.baseten_key()
        if route == "fireworks":
            return self.fireworks_key()
        return None

    def spark_route(self) -> str:
        """The route Spark-tier work rides: the creator's usual.

        The choice is creator-level, so it is read from the usual
        (default "meta") rather than from any one job's session.
        It names the CHOSEN route; a provider built from it
        resolves to Meta on its own when the chosen route has no
        key, and ``spark_route_effective`` says which that is.
        """
        return usual.load_usual({"spark_route": "meta"})["spark_route"]

    def spark_route_effective(self) -> str:
        """The route Spark-tier work will actually ride: the
        chosen route when its key is here, Meta otherwise."""
        route = self.spark_route()
        if route != "meta" and not self.hosted_key(route):
            return "meta"
        return route

    def render_missing(self) -> list[str]:
        if self.is_intake_job:
            source = intake_build.intake_source_video(self.job_dir)
            if source and (self.job_dir / source).is_file():
                return []
            return ["source_video"]
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
            if self.is_intake_job:
                return (
                    "Rendering is built into the app for a video "
                    "it took in."
                )
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
    "keep_mode": "last",
    "remove_fillers": True,
    "mode": "cleanest",
    "spark_route": "meta",
    "style": "justins-way",
    "crossfade": True,
}
# The editing styles (v0.10): which starter know-how packs the
# direction and retake reads use. The ids and labels live with the
# packs themselves, in skills.py. No pick means Justin's way.
EDITING_STYLES = skills.STYLES
# How involved the creator wants to be (v0.7.1). "cleanest" is the
# Cleanest cut: the editor finishes what it is sure about and the
# spots it is unsure about wait in gold. "tailor" is Tailor it with
# me: every unsure spot is presented as a decision. The mode is one
# of the front door choices, so it is saved with them, carried in
# the creator's usual, and named in every recap.
INVOLVEMENT_MODES = ("cleanest", "tailor")
MODE_LABELS = {
    "cleanest": "Proactive",
    "tailor": "Leave more in",
}
BUFFER_MIN_S = 0.05
BUFFER_MAX_S = 0.50
# Breaths are not a choice anymore (removed in v0.10, the
# creator's call): when the silence pass cuts quiet between
# words, the breath sounds inside that quiet are cut too, and
# the pass simply shortens the pause to the set buffer. There
# is no keep-breaths branch and no floor. A moment a person has
# marked to keep (a protected span or a pinned comment) is still
# never trimmed, because it is marked, not because it is a
# breath; see ``_protected_join_indices``.
RETAKES_GUIDANCE = (
    "Remove retakes: when the same line is said more than once, keep "
    "the last take and take the earlier tries out."
)
RETAKES_GUIDANCE_FIRST = (
    "Remove retakes: when the same line is said more than once, keep "
    "the first clean take and take the later tries out."
)


def _retakes_guidance(keep_mode: str) -> str:
    """The direction guidance for the active keeper mode.

    Best delivery applies the positional rule while it is in
    shadow, so its guidance is the last-take sentence; only the
    first-clean mode changes what direction is told.
    """
    if keep_mode == "first":
        return RETAKES_GUIDANCE_FIRST
    return RETAKES_GUIDANCE
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
    the session and offered back the same way next time. The keeper
    mode ("When a line is said more than once, keep:") starts from
    the standing profile's retakes knob when the job carries one,
    and from the last take otherwise; a changed mode is a session
    choice like the rest, and the job's own files are never touched.

    Seeding order (v0.7.0): the built-in defaults, then the
    creator's usual (the choices carried across jobs, see
    ``usual.py``), then the profile keeper seed, then this
    session's own saved choices, which always win. The profile seed
    keeps its standing precedence for the keeper mode: it outranks
    the usual, so one job's profile can never be silently flipped
    by a usual another job wrote. With no job loaded the config is
    the launch home's, so the same read gives the launch session's
    choices, which is how the drop block pre-sets itself before a
    job exists.
    """
    saved = _load_session_json(config, FRONT_DOOR_FILENAME, {})
    settings = dict(FRONT_DOOR_DEFAULTS)
    settings = usual.load_usual(settings)
    if config.job_dir is not None:
        profile = _load_optional_file(config.job_dir / "profile.json")
        settings["keep_mode"] = retakes.settings_from_profile(profile)["keep"]
    if isinstance(saved, dict):
        for key in ("remove_retakes", "cut_silence", "remove_fillers", "crossfade"):
            if isinstance(saved.get(key), bool):
                settings[key] = saved[key]
        buffer = saved.get("buffer_s")
        if isinstance(buffer, (int, float)) and not isinstance(buffer, bool):
            settings["buffer_s"] = min(
                BUFFER_MAX_S, max(BUFFER_MIN_S, float(buffer))
            )
        if saved.get("keep_mode") in retakes.KEEP_MODES:
            settings["keep_mode"] = saved["keep_mode"]
        if isinstance(saved.get("retake_strict"), bool):
            settings["retake_strict"] = saved["retake_strict"]
        if saved.get("mode") in INVOLVEMENT_MODES:
            settings["mode"] = saved["mode"]
        if saved.get("spark_route") in HOSTED_SPARK_ROUTES + ("meta",):
            settings["spark_route"] = saved["spark_route"]
        if saved.get("style") in EDITING_STYLES:
            settings["style"] = saved["style"]
    return settings


def _picked_style(config: WebAppConfig, settings: Optional[dict] = None) -> str:
    """The editing style for a run: the run's own pick when it has
    one, else the saved front door pick, else Justin's way."""
    if settings is not None and settings.get("style") in EDITING_STYLES:
        return str(settings["style"])
    try:
        saved = _front_door_settings(config)
    except Exception:
        saved = {}
    if saved.get("style") in EDITING_STYLES:
        return str(saved["style"])
    return "justins-way"


def _provider_skill_versions(provider, style: Optional[str] = None) -> dict:
    """A provider's record of the know-how it used.

    A provider that can say what it loaded (the live provider and
    the mock both can) is taken at its word. Any other provider
    stand-in is recorded as having used the built-in words, which
    is the only thing such a stand-in ever reads.
    """
    method = getattr(provider, "skill_versions", None)
    if callable(method):
        try:
            record = method()
        except Exception:
            record = None
        if (
            isinstance(record, dict)
            and isinstance(record.get("direction"), dict)
            and isinstance(record.get("retake"), dict)
        ):
            return record
    return skills.builtin_versions(style or "justins-way")


def _skills_recap_lines(record: dict) -> list:
    """The direction recap's know-how line, in plain words.

    Names the style and the starter pack version that ran, and
    adds one more line when the creator's own added know-how was
    used too.
    """
    style_id = record.get("style") if isinstance(record, dict) else None
    label = EDITING_STYLES.get(style_id, EDITING_STYLES["justins-way"])
    version = None
    yours = False
    for kind in ("direction", "retake"):
        entry = record.get(kind) if isinstance(record, dict) else None
        if not isinstance(entry, dict):
            continue
        if version is None and entry.get("version") is not None:
            version = entry.get("version")
        if entry.get("source") == "pack+yours":
            yours = True
    shown = version if version is not None else "built-in"
    lines = [f"Know-how used: {label}, starter pack version {shown}."]
    if yours:
        lines.append("Your own added know-how was used too.")
    return lines


def _keep_mode_recap(settings: dict) -> Optional[str]:
    """The mode announcement, present whenever the mode is not the default.

    A run can never quietly use a forgotten mode: whenever the
    keeper mode is not the last take, the recap names it in the
    research's wording.
    """
    mode = settings.get("keep_mode", "last")
    if mode == "last":
        return None
    label = retakes.MODE_LABELS[mode]
    if settings.get("remove_retakes"):
        return f"Remove retakes is on. Keeping: {label}."
    return f"Keeping: {label}."


def _save_front_door_settings(config: WebAppConfig, settings: dict) -> dict:
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(config.session_dir / FRONT_DOOR_FILENAME, settings)
    # The same choices become the creator's usual, so the next job
    # (and the drop block before any job) starts where this one
    # settled. The usual is a convenience copy; the session file
    # above stays this job's own record.
    usual.save_usual(settings)
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


TEST_VIDEO_FILENAME = "test_video.json"


# The reader for the mark (_is_test_video) lives further down,
# beside the suggestions it gates; the mark's file sits on purpose
# outside the checkpointed state files, so undoing a change never
# changes whether the job is a test video.


def _set_test_video(config: WebAppConfig, on: bool) -> None:
    """Write this session's test-video mark, on or off."""
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        config.session_dir / TEST_VIDEO_FILENAME,
        {"test_video": bool(on)},
    )


def _carry_launch_test_video(config: WebAppConfig) -> None:
    """Copy the launch session's test-video mark onto a new job.

    A mark set at the front door, before any job exists, lives in
    the launch home's session. When a drop becomes the job, that
    mark moves with it, so the choice made before the drop still
    counts for the job the drop turned into. If no mark was set at
    the front door, the new job's session is left alone.
    """
    launch_mark = (
        config.launch_home / SESSION_DIR_NAME / TEST_VIDEO_FILENAME
    )
    if not launch_mark.is_file():
        return
    launch = WebAppConfig(None, launch_home=config.launch_home)
    if launch.session_dir == config.session_dir:
        return
    _set_test_video(config, _is_test_video(launch))


def _carry_launch_thread(config: WebAppConfig) -> None:
    """Carry the launch session's thread into a new job's thread.

    The conversation starts at the front door, before any job
    exists: the creator's first message and the editor's answer
    live in the launch home's thread (0.25, finding 5: that first
    message used to be lost, because no route would store it
    before a job and the job's fresh thread then replaced it).
    When a drop becomes the job, the launch entries move with
    it, merged by entry id in the order they were said, so
    nothing said before the drop is left behind. A spend
    confirmation still waiting at the front door moves too.
    Entries the job's thread already holds are left as they are.
    """
    launch = WebAppConfig(None, launch_home=config.launch_home)
    if launch.session_dir == config.session_dir:
        return
    incoming = _load_thread(launch)
    if incoming:
        with _THREAD_LOCK:
            entries = _load_thread(config)
            known = {e.get("id") for e in entries}
            merged = entries + [
                e for e in incoming if e.get("id") not in known
            ]
            merged.sort(key=lambda e: str(e.get("created_at") or ""))
            if len(merged) != len(entries):
                _save_thread(config, merged)
    launch_pending = (
        config.launch_home / SESSION_DIR_NAME / THREAD_PENDING_FILENAME
    )
    job_pending = config.session_dir / THREAD_PENDING_FILENAME
    if launch_pending.is_file() and not job_pending.is_file():
        try:
            config.session_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(launch_pending, job_pending)
        except OSError:
            pass


# The server answers each request on its own thread
# (ThreadingHTTPServer), and the grades and thread stores are
# read-modify-write files: without a lock, two writers that load
# the same starting store and save in turn silently lose the
# first writer's record. That is how a restore grade could go
# missing while its why entry stayed in the thread, unsettleable
# (Batch J). Every load-merge-save cycle on these two stores
# runs under its store's lock; the locks are re-entrant because
# one writer can call another (a restore writes its grade, then
# its why entry).
_GRADES_LOCK = threading.RLock()
_THREAD_LOCK = threading.RLock()


def _merge_and_save_grades(config: WebAppConfig, incoming: list) -> list:
    """Merge ``incoming`` into the grades store, under its lock."""
    with _GRADES_LOCK:
        entries = creator_rules.merge_grade_entries(
            _load_grades(config), incoming
        )
        _save_grades(config, entries)
        return entries


def _load_grades(config: WebAppConfig) -> list:
    payload = _load_session_json(config, GRADES_FILENAME, {"entries": []})
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return [e for e in payload["entries"] if isinstance(e, dict)]
    return []


def _save_grades(config: WebAppConfig, entries: list) -> None:
    # A test video teaches the app nothing: every grade writer
    # (the grades endpoint, a gold verdict, any other) comes
    # through here, so this one check turns them all off. Grades
    # saved before the mark was set stay on disk as they are.
    if _is_test_video(config):
        return
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(config.session_dir / GRADES_FILENAME, {"entries": entries})


# The choices the why question offers, by the key the page sends.
WHY_CHOICES = frozenset({"story", "sound", "say_it", "other"})


def _record_restore_grade(
    config: WebAppConfig,
    word_start: int,
    word_end: int,
    via: str,
    ask_why: bool = True,
) -> Optional[str]:
    """Write the grade record for words the creator brought back.

    On a test video nothing is recorded (the app does not learn
    from a test), and the return is None so no why question is
    offered for a record that was never written.

    Bringing words back means the cut of those words did not
    work, so the record's verdict is ``did_not_work`` and it can
    never count as support for the cut. The record is keyed by
    the words brought back, and its pattern names the change
    that cut them when the session's record of changes has an
    applied cut covering them (the last such change wins);
    words cut outside a recorded change are ``restore:by-hand``.
    The record goes through the same grades store as every
    other grade. Returns the record's id, so the page can ask
    the why question about this exact record.
    """
    if _is_test_video(config):
        return None
    pattern_key = "restore:by-hand"
    operations = _audit_operations(config)
    covering = []
    overlapping = []
    for op in operations:
        if op.get("status") != "applied":
            continue
        if op.get("type") != "cut_words":
            continue
        if not op.get("comment_id"):
            continue
        op_start, op_end = op.get("word_start"), op.get("word_end")
        if not isinstance(op_start, int) or not isinstance(op_end, int):
            continue
        if op_start <= word_start and word_end <= op_end:
            covering.append(op)
        elif op_start < word_end and word_start < op_end:
            overlapping.append(op)
    matches = covering or overlapping
    if matches:
        pattern_key = f"restore:{matches[-1].get('comment_id')}"
    entry = {
        "id": f"restore-grade-{word_start}-{word_end}",
        "record_id": f"restore-grade-{word_start}-{word_end}",
        "comment_id": "restore",
        "operation_type": "restore_words",
        "verdict": "did_not_work",
        "pattern_key": pattern_key,
        "pattern_description": "Bringing back words the editor had cut",
        "word_start": word_start,
        "word_end": word_end,
        "source": via,
        "created_at": _utc_now(),
    }
    _merge_and_save_grades(config, [entry])
    if ask_why:
        try:
            _append_why_thread(
                config, entry["record_id"], "Why did you put that back?"
            )
        except Exception:
            pass
    return entry["record_id"]


# ---------------------------------------------------------------------------
# Creator-level rules and framing (Studio reset, approved 2026-11-01)
#
# Saved rules and the teaching framing profile are the creator's, not
# one job's. They live in one place in the launch home, so a rule saved
# on Train with no job loaded, or saved inside any job, is read by the
# next job's runs. The first time the creator store is used, rules and
# a framing profile already saved inside existing job folders (under
# the launch home, under ~/.talk-to-your-editor, or in the current
# job's own session) are merged in, so nothing saved before is lost.
# Each legacy file is merged once: the merged list is kept in the
# launch home, so removing a rule later never brings it back.
# ---------------------------------------------------------------------------

_CREATOR_MERGE_FILENAME = "creator_merge.json"
_creator_merge_done: set = set()

# The creator home (0.27.5): styles, rules, inspiration, and
# framing live in ~/.talk-to-your-editor, never inside a kit
# folder. The Mac launcher used to point the launch home at
# the unzipped kit's own .launch-home, so unzipping 0.27.4
# started Justin with an empty store and his saved style was
# "lost" (it was sitting in the old kit's folder). The launch
# home still holds the session and the staged drops; the
# creator's own learning lives at the machine level, so
# every update keeps it. First use copies any of the store
# files the launch home holds and the creator home does
# not, so nothing saved inside an old kit is stranded.
_creator_home_migrated: set = set()


def _creator_home(config: WebAppConfig) -> Path:
    home = Path.home() / MAC_KEY_DIR_NAME
    try:
        key = str(home.resolve())
    except OSError:
        key = str(home)
    if key not in _creator_home_migrated:
        _creator_home_migrated.add(key)
        try:
            legacy = config.launch_home
            if legacy.resolve() != home.resolve():
                for name in (
                    CREATOR_RULES_FILENAME,
                    STYLES_FILENAME,
                    INSPIRATION_FILENAME,
                    teaching.FRAMING_FILENAME,
                ):
                    src = legacy / name
                    dst = home / name
                    if src.is_file() and not dst.is_file():
                        home.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(src, dst)
        except OSError:
            pass
    return home


def _creator_rules_path(config: WebAppConfig) -> Path:
    return _creator_home(config) / CREATOR_RULES_FILENAME


INSPIRATION_FILENAME = "inspiration.json"


def _creator_inspiration_path(config: WebAppConfig) -> Path:
    return _creator_home(config) / INSPIRATION_FILENAME


def _load_inspiration(config: WebAppConfig) -> list:
    """The creator's saved Inspiration, at the creator level.

    Style statements learned from other people's finished videos:
    the surface only (pacing, cut rhythm, captions, framing),
    never the creator's own retake judgment. They live apart from
    the creator's rules on purpose, and each entry names the
    teaching draft it came from.
    """
    try:
        data = _load_json(str(_creator_inspiration_path(config)))
    except (OSError, ValueError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    return [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("statement")
    ]


def _save_inspiration(config: WebAppConfig, entries: list) -> None:
    path = _creator_inspiration_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, {"entries": entries})


def _inspiration_view(config: WebAppConfig) -> list:
    return [
        {
            "pattern_key": entry.get("pattern_key"),
            "statement": entry.get("statement"),
            "style": entry.get("style") or DEFAULT_STYLE_NAME,
        }
        for entry in _load_inspiration(config)
    ]


STYLES_FILENAME = "styles.json"
DEFAULT_STYLE_NAME = "My editing"

# The plain line a social link gets (Message 2R, P4): downloading
# other people's videos from those platforms breaks their terms,
# so the app never tries; it says what works instead.
SOCIAL_LINK_MESSAGE = (
    "That link is from a platform whose videos I cannot download; "
    "that would break its terms. What works: a direct video file "
    "link, a Google Drive or Dropbox share link, a Vimeo link "
    "where the owner allows download, or your own YouTube video "
    "downloaded from YouTube Studio, dropped in as a file."
)
STYLE_YOUTUBE_MESSAGE = (
    "For a style you like, I cannot download a YouTube video; "
    "that would break its terms. What works: a direct video file "
    "link, a Google Drive or Dropbox share link, or a Vimeo link "
    "where the owner allows download."
)
_SOCIAL_HOSTS = (
    "instagram.com", "tiktok.com", "facebook.com", "fb.watch",
    "threads.net",
)


def _styles_path(config: WebAppConfig) -> Path:
    return _creator_home(config) / STYLES_FILENAME


def _load_styles(config: WebAppConfig) -> dict:
    """The creator's named styles; the default always exists.

    Rules saved before styles existed carry no style name; they
    belong to the default style, so nothing saved in 0.21 or 0.22
    is ever lost or moved.
    """
    names: list[str] = []
    current = None
    path = _styles_path(config)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict):
            for entry in payload.get("styles") or []:
                if isinstance(entry, dict) and entry.get("name"):
                    name = str(entry["name"]).strip()
                    if name and name not in names:
                        names.append(name)
            if payload.get("current"):
                current = str(payload["current"]).strip()
    if DEFAULT_STYLE_NAME not in names:
        names.insert(0, DEFAULT_STYLE_NAME)
    if current not in names:
        current = names[0]
    return {"styles": [{"name": n} for n in names], "current": current}


def _save_styles(config: WebAppConfig, data: dict) -> None:
    _styles_path(config).parent.mkdir(parents=True, exist_ok=True)
    _write_json(_styles_path(config), data)


def _current_style(config: WebAppConfig) -> str:
    return _load_styles(config)["current"]


def _styles_view(config: WebAppConfig) -> dict:
    data = _load_styles(config)
    counts: dict[str, int] = {}
    store = _rules_store(config)
    for rule in store.get("rules", []):
        if isinstance(rule, dict):
            name = str(rule.get("style") or DEFAULT_STYLE_NAME)
            counts[name] = counts.get(name, 0) + 1
    inspiration = _load_inspiration(config)
    return {
        "current": data["current"],
        "styles": [
            {
                "name": entry["name"],
                "rules": counts.get(entry["name"], 0),
                "inspiration": sum(
                    1
                    for item in inspiration
                    if str(item.get("style") or DEFAULT_STYLE_NAME)
                    == entry["name"]
                ),
            }
            for entry in data["styles"]
        ],
    }


def _smooth_view(config: WebAppConfig) -> dict:
    """The last smooth pass's join check, for the chat's done line."""
    payload = _load_session_json(config, "smooth_pass.json", {})
    if not isinstance(payload, dict) or not payload.get("ran"):
        return {}
    return {
        "joins_checked": payload.get("joins_checked", 0),
        "joins_smooth": payload.get("joins_smooth", 0),
        "your_call": payload.get("your_call", []),
        "edges_set": payload.get("edges_set", 0),
    }


def _empty_edit_counts() -> dict:
    return {
        "retakes_removed": 0,
        "retakes_set_aside": 0,
        "joins_checked": 0,
        "joins_failed": 0,
        "spots_to_review": 0,
        "words_cut": 0,
        "words_kept": 0,
    }


def _edit_counts(
    config: WebAppConfig,
    *,
    timeline=None,
    edit_spans=None,
    gold=None,
    smooth=None,
) -> dict:
    """The one count every surface reads (0.25, finding 6).

    Computed once, from the session's final plan and its pass
    records: retake copies removed (distinct pairs whose earlier
    take the final plan has out, whether the pass cut them, the
    verifier held and settled them, or the detected-repeat
    settlement struck them), retake copies set aside as your
    call (the open gold retake spans, by pair), joins checked
    and joins still failing (the smooth pass's final join
    check), spots to review (open gold spans plus failing
    joins), and the plan's word counts. The Done bubble, its
    review link, the transcript header, and the persisted Done
    payload all read this dict; no surface recounts on its own
    (the 0.24 run showed "18 retakes cut" in one place against
    "2 removed, 7 set aside" in another because each surface
    counted a different thing in a different unit).
    """
    counts = _empty_edit_counts()
    if not config.has_job:
        return counts
    if timeline is None:
        transcript = _load_json(str(config.job_dir / "transcript.json"))
        timeline = Timeline(transcript, _effective_cuts(config))
    kept = timeline.kept_word_indices()
    counts["words_kept"] = len(kept)
    counts["words_cut"] = len(timeline.words) - len(kept)
    if edit_spans is None:
        edit_spans = _edit_spans(config)
    removed: set = set()
    for span in edit_spans:
        meta = span.get("retake")
        if isinstance(meta, dict):
            removed.add(
                str(
                    meta.get("pair_id")
                    or f"span:{span.get('word_start')}-{span.get('word_end')}"
                )
            )
    # Detected repeats the settlement struck sit in the gold
    # store as auto_cut records; their cuts are in the plan but
    # no audit operation names them, so they are counted here
    # from the store, by pair.
    for record in _load_gold(config):
        if (
            record.get("status") == "auto_cut"
            and record.get("source") == "retakes"
        ):
            removed.add(
                str(
                    record.get("pair_id")
                    or f"span:{record.get('word_start')}-{record.get('word_end')}"
                )
            )
    # And any recorded pair the final plan itself settles: the
    # earlier take's words all out, the kept take's words in.
    try:
        retake_record = _load_retake_record(config)
    except Exception:
        retake_record = {}
    kept_set = set(kept)
    for pair in retake_record.get("pairs") or []:
        if not isinstance(pair, dict):
            continue
        cut = pair.get("cut") or {}
        keep = pair.get("keep") or {}
        cs, ce = cut.get("word_start"), cut.get("word_end")
        ks, ke = keep.get("word_start"), keep.get("word_end")
        if not all(
            isinstance(v, int) for v in (cs, ce, ks, ke)
        ):
            continue
        if not (0 <= cs < ce <= len(timeline.words)):
            continue
        if any(i in kept_set for i in range(cs, ce)):
            continue
        if not any(i in kept_set for i in range(ks, ke)):
            continue
        removed.add(str(pair.get("pair_id") or retakes.pair_key(pair)))
    counts["retakes_removed"] = len(removed)
    open_gold = gold if gold is not None else _open_gold_spans(config)
    aside: set = set()
    for view in open_gold:
        if view.get("kind") == "retake" or view.get("source") == "retakes":
            aside.add(
                str(
                    view.get("pair_id")
                    or view.get("gold_id")
                    or f"span:{view.get('word_start')}-{view.get('word_end')}"
                )
            )
    counts["retakes_set_aside"] = len(aside)
    smooth_view = smooth if smooth is not None else _smooth_view(config)
    counts["joins_checked"] = int(smooth_view.get("joins_checked") or 0)
    counts["joins_failed"] = len(smooth_view.get("your_call") or [])
    counts["spots_to_review"] = len(open_gold) + counts["joins_failed"]
    return counts


def _creator_framing_dir(config: WebAppConfig) -> Path:
    return _creator_home(config)


def _legacy_store_candidates(config: WebAppConfig) -> list:
    """Per-job store files that predate the creator-level store."""
    roots = []
    for root in (config.launch_home, Path.home() / ".talk-to-your-editor"):
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved not in roots:
            roots.append(resolved)
    candidates = []
    seen = set()
    creator_path = _creator_rules_path(config).resolve()
    framing_name = teaching.FRAMING_FILENAME
    for root in roots:
        if not root.is_dir():
            continue
        found = list(root.rglob(f"tye-session/{CREATOR_RULES_FILENAME}"))
        found += list(root.rglob(f"tye-session/{framing_name}"))
        found += list(root.rglob("tye-session/framing_profile_*.json"))
        for path in found:
            if path.resolve() == creator_path or path in seen:
                continue
            seen.add(path)
            candidates.append(path)
    session_rules = config.session_dir / CREATOR_RULES_FILENAME
    if session_rules.is_file() and session_rules not in seen:
        candidates.append(session_rules)
    session_framing = config.session_dir / framing_name
    if session_framing.is_file() and session_framing not in seen:
        candidates.append(session_framing)
    if config.job_dir is not None:
        job_rules = config.job_dir / CREATOR_RULES_FILENAME
        if job_rules.is_file() and job_rules not in seen:
            candidates.append(job_rules)
    return candidates


def _ensure_creator_store(config: WebAppConfig) -> None:
    """Merge legacy per-job rules and framing into the creator store.

    Runs once per launch home per process, plus once per newly seen
    legacy file (recorded in the merge marker), so a job folder the
    creator opens later still gives up the rules it saved before.
    """
    try:
        launch_home = config.launch_home
        marker_path = launch_home / _CREATOR_MERGE_FILENAME
        merged_paths = []
        if marker_path.is_file():
            try:
                payload = json.loads(marker_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    merged_paths = [
                        str(p) for p in payload.get("merged", []) if p
                    ]
            except (OSError, ValueError):
                merged_paths = []
        creator_path = _creator_rules_path(config)
        candidates = _legacy_store_candidates(config)
        fresh = [
            path
            for path in candidates
            if path.name == CREATOR_RULES_FILENAME
            and str(path) not in merged_paths
        ]
        if not fresh and str(launch_home) in _creator_merge_done:
            return
        _creator_merge_done.add(str(launch_home))
        store = creator_rules.load_rules_store(creator_path)
        known_keys = {
            rule.get("pattern_key")
            for rule in store.get("rules", [])
            if isinstance(rule, dict)
        }
        framing_candidates = []
        changed = False
        for path in candidates:
            if path.name.startswith("framing_profile"):
                framing_candidates.append(path)
                continue
            if str(path) in merged_paths:
                continue
            legacy = creator_rules.load_rules_store(path)
            for rule in legacy.get("rules", []):
                key = rule.get("pattern_key")
                if key and key not in known_keys:
                    store["rules"].append(rule)
                    known_keys.add(key)
                    changed = True
            for key, count in (legacy.get("dismissed") or {}).items():
                if key not in store.get("dismissed", {}):
                    store.setdefault("dismissed", {})[key] = count
                    changed = True
            merged_paths.append(str(path))
        if changed or (store["rules"] and not creator_path.is_file()):
            creator_rules.save_rules_store(creator_path, store)
        # Framing merges per shape: a legacy 9:16 profile fills the
        # 9:16 slot and a 16:9 one fills the 16:9 slot, and neither
        # overwrites a profile the creator store already holds.
        for path in framing_candidates:
            payload = _load_json(str(path))
            profile = (
                payload.get("profile") if isinstance(payload, dict)
                else None
            )
            if not isinstance(profile, dict):
                continue
            shape = teaching.shape_for_profile(profile)
            if shape is None:
                continue
            if teaching.load_framing_profile(
                _creator_framing_dir(config), shape
            ) is None:
                teaching.save_framing_profile(
                    _creator_framing_dir(config), profile
                )
        launch_home.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            json.dumps({"merged": merged_paths}, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:  # the merge never blocks a run
        _log_raw_error("creator store merge", exc)


def _rules_store(config: WebAppConfig) -> dict:
    _ensure_creator_store(config)
    return creator_rules.load_rules_store(_creator_rules_path(config))


def _save_rules_store(config: WebAppConfig, store: dict) -> None:
    _ensure_creator_store(config)
    creator_rules.save_rules_store(_creator_rules_path(config), store)


def _rule_class_for(pattern, entries: list) -> str:
    """A suggestion's speed class, from item 3's classifier.

    The classifier lives in creator_rules in its own build; this
    copy may not have it yet, so it is called when present and the
    answer falls back to "story" (the careful class) when it is
    absent or cannot say.
    """
    classifier = getattr(creator_rules, "rule_class_for", None)
    if callable(classifier):
        try:
            result = classifier(pattern, entries)
        except Exception:
            result = None
        if result in ("small", "story"):
            return result
    return "story"


# The two plain sentences a teaching drop can end in (v0.10, item
# 5). They are the whole answer when a link or a recording cannot
# be learned from: never an error screen, never a fake success.
TEACH_LINK_MESSAGE = (
    "The app could not get that video from the link. Drop the "
    "video file here instead."
)
TEACH_NO_SPEECH_MESSAGE = (
    "The app could not learn from that recording. It needs to "
    "hear you talking through your choices."
)


def _rule_suggestions(config: WebAppConfig) -> list:
    """Patterns in the grades that are ready to offer as rules.

    The session's teaching drafts ride in the same list, through
    the same candidate path: each is offered as a suggestion with
    source "teaching", and none applies until the creator saves
    it. A draft already saved as a rule is not offered again.
    """
    # A test video offers no rules, even from grades saved before
    # the mark was set: those grades stay on disk, but nothing
    # surfaces from them while the job is marked as a test.
    # Teaching drafts wait too: a test job is for checking the
    # app, not for teaching it.
    if _is_test_video(config):
        return []
    entries = _load_grades(config)
    store = _rules_store(config)
    suggestions = creator_rules.suggestions_for(entries, store)
    saved_keys = {
        rule.get("pattern_key")
        for rule in store.get("rules", [])
        if isinstance(rule, dict)
    }
    inspiration_keys = {
        entry.get("pattern_key") for entry in _load_inspiration(config)
    }
    for draft in teaching.load_drafts(config.session_dir):
        key = draft.get("pattern_key")
        if not key or key in saved_keys or key in inspiration_keys:
            continue
        basis = draft.get("basis")
        if basis == "recording" and not draft.get("proof"):
            source_note = "From your teaching video. Not tested yet."
        else:
            source_note = draft.get("source_note") or ""
        suggestions.append(
            {
                "pattern_key": key,
                "statement": draft.get("statement"),
                "support_count": 0,
                "explicit": True,
                "rule_class": _rule_class_for(draft, entries),
                "source": "teaching",
                "source_note": source_note,
                "proof": draft.get("proof"),
                "basis": basis,
                "kind": draft.get("kind"),
            }
        )
    # An open decision proposal (source (c)) rides the same
    # list while it is open, so its Save and Not now go through
    # the rule endpoints unchanged; the proposal's own statement
    # is the one offered, never a re-derived one.
    offered = {s.get("pattern_key") for s in suggestions}
    for entry in _load_thread(config):
        if entry.get("kind") != "rule_proposal":
            continue
        if entry.get("status") != "open":
            continue
        key = entry.get("pattern_key")
        if not key or key in saved_keys or key in offered:
            continue
        suggestions.append(
            {
                "pattern_key": key,
                "statement": entry.get("statement"),
                "support_count": 1,
                "explicit": True,
                "rule_class": _rule_class_for(
                    {"pattern_key": key}, entries
                ),
                "source": "decision",
                "source_note": "From your call on a spot in this edit.",
            }
        )
        offered.add(key)
    return suggestions


def _saved_rules_view(config: WebAppConfig) -> list:
    """The saved rules as the page shows them (id, statement, pattern)."""
    return [
        {
            "rule_id": rule.get("rule_id"),
            "pattern_key": rule.get("pattern_key"),
            "statement": rule.get("statement"),
            "rule_class": rule.get("rule_class"),
            "source": rule.get("source"),
            "tested": rule.get("tested"),
        }
        for rule in _rules_store(config).get("rules", [])
        if rule.get("active", True)
    ]


def _is_test_video(config: WebAppConfig) -> bool:
    """Whether this session's job is marked as a test video.

    Reads the session's ``test_video.json`` flag directly; a
    missing or unreadable flag means a normal job.
    """
    payload = _load_session_json(config, TEST_VIDEO_FILENAME, {})
    return isinstance(payload, dict) and bool(payload.get("test_video"))


def _refresh_teaching_tested(config: WebAppConfig) -> bool:
    """Mark saved teaching rules as tested once a run proves them.

    A teaching rule counts as tested when a finished direction
    run includes it and either (a) the job is marked as a test
    video, or (b) the job is a normal one and its grades hold at
    least one change that worked or partly worked. Returns True
    when the store changed.
    """
    store = _rules_store(config)
    pending = [
        rule
        for rule in store.get("rules", [])
        if rule.get("source") == "teaching" and not rule.get("tested")
    ]
    if not pending:
        return False
    record = _load_session_json(config, DIRECTION_PASS_FILENAME, {})
    if not (isinstance(record, dict) and record.get("ran")):
        return False
    if not _is_test_video(config):
        grades = _load_grades(config)
        if not any(
            entry.get("verdict") in ("worked", "partly")
            for entry in grades
        ):
            return False
    for rule in pending:
        rule["tested"] = True
    _save_rules_store(config, store)
    return True


# ---------------------------------------------------------------------------
# Teaching intake (v0.10, item 5): the fast way to teach the app
#
# A teaching drop is staged under ~/.talk-to-your-editor/teaching/
# (see the teaching module), the same way a job drop is staged by
# the intake. A run turns whatever arrived (a recording with
# talking, a raw/finished pair, a lone finished video) into draft
# rules for the current session; the drafts surface through
# _rule_suggestions and nothing applies without the creator's yes.
# ---------------------------------------------------------------------------

TEACH_FILE_SUFFIXES = teaching.VIDEO_SUFFIXES + (
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
)


def _teaching_provider(config: WebAppConfig):
    """The provider a teaching read uses.

    The same construction the retake pass uses: the live provider
    when a Meta sign-in is here, the mock when there is no key.
    """
    if config.signin_key():
        return _live_provider(config)
    return MockProvider()


def _teach_staged_file(record: dict, role: str) -> Optional[Path]:
    """One staged teaching file by role, or None when it is absent."""
    name = record.get(role)
    if not name:
        return None
    path = Path(record["dir"]) / str(name)
    return path if path.is_file() else None


def _teach_words(config: WebAppConfig, path: Optional[Path]) -> Optional[list]:
    """The words of one staged teaching file, or None when the app
    could not hear usable talking in it."""
    if path is None:
        return None
    try:
        return teaching.transcribe_media(path, config.elevenlabs_key())
    except teaching.TeachingError:
        return None


def _run_teaching(config: WebAppConfig, record: dict) -> dict:
    """Do one teaching run over whatever the drop brought in.

    A recording's talking drafts rules through the provider; a
    raw/finished pair proves those drafts against the real cuts,
    or drafts from the cuts alone when there is no recording; a
    lone finished video drafts only what a finished video can
    show. The drafts land in the current session's teaching
    drafts, and the answer says how many were made. When nothing
    could be learned, the answer is the one plain message.
    """
    session_log.log_stage(config.session_dir, "teaching", "start")
    kind = record.get("kind") or "mine"
    recording = _teach_staged_file(record, "recording")
    raw = _teach_staged_file(record, "raw")
    finished = _teach_staged_file(record, "finished")
    if recording is None and raw is None and finished is None:
        return {
            "ok": True,
            "drafts": [],
            "message": (
                "Add a recording, a raw video and its finished "
                "edit, or a link first, then choose Teach from this."
            ),
        }
    diff = None
    raw_words = None
    finished_words = None
    if raw is not None and finished is not None:
        raw_words = _teach_words(config, raw)
        finished_words = _teach_words(config, finished)
        if raw_words and finished_words:
            diff = teaching.pair_diff(raw_words, finished_words)
    drafts: list = []
    recording_words = _teach_words(config, recording)
    if recording_words:
        provider = _teaching_provider(config)
        drafts = teaching.finalize_drafts(
            provider.draft_rules_from_teaching(recording_words),
            basis="recording",
            kind=kind,
            source_note=teaching.RECORDING_SOURCE_NOTE,
        )
        if diff is not None:
            teaching.attach_proof(drafts, diff, raw_words)
    if not drafts and diff is not None:
        drafts = teaching.drafts_from_pair(diff, raw_words, kind=kind)
    if not drafts and finished is not None:
        if finished_words is None:
            finished_words = _teach_words(config, finished)
        if finished_words:
            drafts = teaching.surface_drafts(finished_words, kind=kind)
    if not drafts:
        return {
            "ok": True,
            "drafts": [],
            "message": TEACH_NO_SPEECH_MESSAGE,
        }
    if finished is not None:
        # The picture teaches too (v0.19, chunk 4): when a
        # finished video was studied, read its framing and add
        # the one plain line, saving the profile with the
        # session for the render's shape choices. When no face
        # can be read, nothing is added and nothing is claimed.
        framing_draft = teaching.framing_draft_for(
            _creator_framing_dir(config),
            finished,
            kind=kind,
            basis="pair" if diff is not None else "finished_only",
            source_note=(
                teaching.PAIR_SOURCE_NOTE
                if diff is not None
                else teaching.FINISHED_SOURCE_NOTE
            ),
        )
        if framing_draft is not None:
            drafts.append(framing_draft)
    teaching.add_drafts(config.session_dir, drafts)
    count = len(drafts)
    # The summary in the chat leads with what was measured
    # (0.27.1, P2 item 11): the opening line, the sentence
    # pauses, the breaths kept, each with its number. A
    # measurement that cannot be made is simply absent.
    measured: list = []
    if finished is not None:
        try:
            if not finished_words:
                finished_words = _teach_words(config, finished)
            if finished_words:
                measured = teaching.measured_summary(
                    raw_words, finished_words, finished
                )
        except Exception:
            measured = []
    return {
        "ok": True,
        "drafts": drafts,
        "measured": measured,
        "message": (
            f"Made {count} draft rule{'s' if count != 1 else ''}. "
            "Find them with your other rule suggestions. Nothing "
            "applies until you say yes."
        ),
    }


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


def _span_record_cause(
    start: int,
    end: int,
    retake_pairs: list,
    gold_records: list,
    opening_record: dict,
):
    """A cut span's cause from the pass records, or None.

    The fallback half of the explainer (0.26, fix 5): when no
    applied audit operation covers a span, the pass records
    still name most cuts: the retake record's pairs, the
    detected-repeat settlements in the gold store, and the
    opening rule's record. Returns (source, reason,
    retake_meta) or None when no record names this span.
    """
    for pair in retake_pairs:
        if not isinstance(pair, dict):
            continue
        cut = pair.get("cut") or {}
        keep = pair.get("keep") or {}
        cs, ce = cut.get("word_start"), cut.get("word_end")
        if not (isinstance(cs, int) and isinstance(ce, int)):
            continue
        if not (cs < end and start < ce):
            continue
        keep_text = str(keep.get("text") or "").strip()
        reason = "The retake pass cut this earlier take."
        if keep_text:
            reason += f" The take that stayed: \"{keep_text}\""
        return (
            "retakes",
            reason,
            {
                "pair_id": pair.get("pair_id"),
                "band": pair.get("band"),
                "retake_class": pair.get("retake_class"),
                "keep_text": keep.get("text"),
                "keep_word_start": keep.get("word_start"),
                "keep_word_end": keep.get("word_end"),
                "shadow": pair.get("shadow"),
                "unsure": False,
                "unsure_note": None,
            },
        )
    for record in gold_records:
        if not isinstance(record, dict):
            continue
        if record.get("status") != "auto_cut":
            continue
        cs, ce = record.get("word_start"), record.get("word_end")
        if not (isinstance(cs, int) and isinstance(ce, int)):
            continue
        if not (cs < end and start < ce):
            continue
        keep_text = str(record.get("keep_text") or "").strip()
        reason = (
            "The editor detected this as a repeat of the take "
            "that stayed and cut it: detected retakes are cut, "
            "last take kept."
        )
        if keep_text:
            reason += f" The take that stayed: \"{keep_text}\""
        return (
            str(record.get("source") or "retakes"),
            reason,
            {
                "pair_id": record.get("pair_id"),
                "band": None,
                "retake_class": None,
                "keep_text": record.get("keep_text"),
                "keep_word_start": record.get("keep_word_start"),
                "keep_word_end": record.get("keep_word_end"),
                "shadow": None,
                "unsure": False,
                "unsure_note": None,
            },
        )
    if opening_record.get("fired"):
        cut_words = opening_record.get("cut_words") or []
        first_line = opening_record.get("first_line_word")
        named = set()
        for wi in cut_words:
            if isinstance(wi, int):
                named.add(wi)
        if (named and all(i in named for i in range(start, end))) or (
            isinstance(first_line, int) and end <= first_line
        ):
            return (
                "opening",
                "The opening rule cut this: the edit starts on "
                "its first real line, so the setup talk and "
                "abandoned takes before it are out.",
                None,
            )
    return None


# Action wording stored in audits by runs before 0.27 (fix 11):
# the bare fragments ("Removed the words this note pointed at
# from the finished edit.") read as if the assistant had
# performed the cut, so a stored value is re-voiced as what the
# record shows before it can reach an answer. New runs store
# the new wording already (verifier.PLAIN_ACTION_BY_TYPE).
_LEGACY_ACTION_WORDING = {
    "Removed the words this note pointed at from the finished edit.":
        "The record shows the words this note pointed at were "
        "removed from the finished edit.",
    "Swapped the take used here for the other take, as this note asked.":
        "The record shows the take used here was swapped for the "
        "other take, as this note asked.",
}


def _record_voiced_action(text: str) -> str:
    """One stored plain action, in the record's voice."""
    text = str(text or "").strip()
    return _LEGACY_ACTION_WORDING.get(text, text)


def _edit_spans(config: WebAppConfig) -> list:
    """The spans this app has taken out of the edit, with their reasons.

    A span is a run of words the job's own plan kept and the
    session's current plan has cut. Both Timelines read the same
    ruler: the current plan's sound bounds (0.26, fix 1) are
    lent to the base plan too, so a word is never counted cut
    on one side by its sound and kept on the other by its stamp.

    Each span's reason comes from the editor's own record, and
    only from it (0.26, fix 5): the applied change that removed
    it when the audit names one (a direction run's cut, the
    retake pass, the filler pass, or a transcript click the
    audit recorded), the retake record's pair whose earlier
    take it is, the detected-repeat settlement that struck it,
    or the opening rule's record. A span no record names gets
    an honest sentence: the edit's own passes took it out and
    the record does not say which one. No span is ever
    explained as something the creator did by hand unless his
    own recorded action says so. Notes left on an edit ride
    along with its span.
    """
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    words = transcript.get("words", [])
    effective = _effective_cuts(config)
    base_cuts = _load_json(str(config.job_dir / "cuts.json"))
    if isinstance(effective, dict) and isinstance(base_cuts, dict):
        bounds = effective.get("word_sound_bounds")
        if bounds and not base_cuts.get("word_sound_bounds"):
            base_cuts = {**base_cuts, "word_sound_bounds": bounds}
    base = Timeline(transcript, base_cuts)
    current = Timeline(transcript, effective)
    operations = _audit_operations(config)
    try:
        retake_pairs = _load_retake_record(config).get("pairs") or []
    except Exception:
        retake_pairs = []
    try:
        gold_records = _load_gold(config)
    except Exception:
        gold_records = []
    opening_record = _load_session_json(config, "opening_pass.json", {})
    if not isinstance(opening_record, dict):
        opening_record = {}
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
        retake_meta = None
        if op is not None and op.get("comment_id") == "transcript-toggle":
            # The one cut that is the creator's own click, and
            # the audit itself is the record of it: the reason
            # the click's operation carried is what is said.
            source = "transcript"
            reason = str(
                op.get("reason")
                or "You clicked these words in the transcript to cut them."
            )
            comment_id = op.get("comment_id")
            comment_text = None
        elif op is not None and op.get("comment_id") == FILLER_COMMENT_ID:
            source = "filler"
            reason = (
                "The filler pass took these words out: they are "
                "ums and uhs, and the choice to remove fillers "
                "was on."
            )
            comment_id = op.get("comment_id")
            comment_text = None
        elif op is not None:
            source = "direction"
            reason = _record_voiced_action(
                str(
                    op.get("plain_action")
                    or "These words were removed by a direction run."
                )
            )
            comment_id = op.get("comment_id")
            comment = comments_by_id.get(str(comment_id)) if comment_id else None
            comment_text = (
                str(comment.get("text")) if comment and comment.get("text") else None
            )
            if isinstance(op.get("retake"), dict):
                meta = op["retake"]
                retake_meta = {
                    "pair_id": meta.get("pair_id"),
                    "band": meta.get("band"),
                    "retake_class": meta.get("retake_class"),
                    "keep_text": meta.get("keep_text"),
                    "keep_word_start": meta.get("keep_word_start"),
                    "keep_word_end": meta.get("keep_word_end"),
                    "shadow": meta.get("shadow"),
                    "unsure": bool(meta.get("unsure")),
                    "unsure_note": meta.get("unsure_note"),
                }
        else:
            found = _span_record_cause(
                start, end, retake_pairs, gold_records, opening_record
            )
            if found is not None:
                source, reason, retake_meta = found
            else:
                source = "passes"
                reason = (
                    "The edit's own passes took these words out, "
                    "and the record does not name which pass. "
                    "It was not a change you made by hand."
                )
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
        shadow_note = ""
        shadow = pair.get("shadow")
        if isinstance(shadow, dict) and shadow.get("flip"):
            shadow_note = (
                " Best delivery (Beta) would have kept the other take: "
                + str(shadow.get("reason") or "")
            )
        synthetic.append(
            {
                "id": pair["pair_id"],
                "ts": _output_time_for_word(
                    config, int(cut.get("word_start") or 0)
                ),
                "text": (
                    "Retake found by the front door. "
                    + (
                        "Earlier: "
                        if int(cut.get("word_start") or 0)
                        <= int(keep.get("word_start") or 0)
                        else "Later: "
                    )
                    + f"\"{cut_text}\" "
                    f"The take that stayed: \"{keep_text}\""
                    + shadow_note
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


def _paragraph_indices(words: list) -> list:
    """Paragraph index per word, computed once from the original
    recording's word times (v2 workspace spec, section: transcript
    as flowing paragraphs).

    A new paragraph starts on a speaker change, on a pause of
    0.8 seconds or more between one word's end and the next word's
    start in the ORIGINAL recording, or after about 6 sentences or
    about 50 seconds, broken at the nearest sentence end. Because
    the indices come only from the original word times, edits never
    move a paragraph break.
    """
    indices: list = []
    current = -1
    para_start = 0.0
    sentences = 0
    prev = None
    for i, word in enumerate(words):
        try:
            start = float(word.get("start") or 0.0)
        except (TypeError, ValueError):
            start = 0.0
        brk = current < 0
        if prev is not None and not brk:
            try:
                prev_end = float(prev.get("end") or 0.0)
            except (TypeError, ValueError):
                prev_end = start
            if word.get("speaker") != prev.get("speaker"):
                brk = True
            elif start - prev_end >= 0.8:
                brk = True
            else:
                prev_text = str(prev.get("word") or "").rstrip()
                if prev_text.endswith((".", "!", "?")) and (
                    sentences >= 6 or start - para_start >= 50.0
                ):
                    brk = True
        if brk:
            current += 1
            para_start = start
            sentences = 0
        text = str(word.get("word") or "").rstrip()
        if text.endswith((".", "!", "?")):
            sentences += 1
        indices.append(current)
        prev = word
    return indices


def _edited_time_for_source(timeline: Timeline, source_t: float) -> float:
    """One source-recording time on the edited video's clock.

    Kept pieces map by their offset inside the piece; a time inside
    a cut part lands on the join where that part was taken out, so
    the result never runs backwards as source time grows.
    """
    t = float(source_t or 0.0)
    for segment in timeline.segments:
        rng = segment["range"]
        r_start = float(rng["start"])
        r_end = float(rng["end"])
        if t < r_start:
            return round(segment["output_start"], 4)
        if t <= r_end:
            return round(segment["output_start"] + (t - r_start), 4)
    return round(timeline.output_duration, 4)


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
    entries = transcript_display_entries(words, statuses, corrections)
    # The display entries carry no times (word_corrections is a
    # text-only view), so the source-clock seconds are laid on here,
    # from the words each entry stands for: an ordinary entry is its
    # one word, and a corrected span entry runs from its first
    # word's start to its last word's end.
    paragraphs = _paragraph_indices(words)
    for entry in entries:
        start = entry.get("index")
        end = entry.get("word_end")
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start < end <= len(words)
        ):
            entry["start"] = float(words[start]["start"])
            entry["end"] = float(words[end - 1]["end"])
            entry["paragraph"] = paragraphs[start]
            speaker = words[start].get("speaker")
            if speaker:
                entry["speaker"] = speaker
        else:
            entry["start"] = 0.0
            entry["end"] = 0.0
            entry["paragraph"] = paragraphs[start] if (
                isinstance(start, int) and 0 <= start < len(words)
            ) else 0
        entry["edited_start"] = _edited_time_for_source(
            word_timeline, entry["start"]
        )
        entry["edited_end"] = _edited_time_for_source(
            word_timeline, entry["end"]
        )
    return (entries, corrections)


# ---------------------------------------------------------------------------
# Timeline band data (v0.11.1, item 4): the waveform peaks and the
# caption cues the review page's bottom band draws. Both are
# rendering data only, computed from files the app already reads
# for this job; nothing here changes a cut, a word, or a caption.
# ---------------------------------------------------------------------------

WAVEFORM_PEAK_COUNT = 240


def waveform_peaks_for_samples(samples, segments, count=WAVEFORM_PEAK_COUNT):
    """Peak level per slice of the edited clock, from sound samples.

    ``samples`` are the source recording's samples as floats (the
    shape ``transcribe.waveform_samples`` returns: mono, 16 kHz).
    Each ordinary kept segment contributes its own span of samples
    to the output clock, in order, the same mapping the timeline
    pieces use; special segments (inserts with donor sound) are
    left at zero, because their sound does not sit at their place
    in the recording. Returns ``count`` numbers from 0 to 1, scaled
    so the loudest slice is 1; a silent or empty input returns an
    empty list, so the page can say there is no wave instead of
    drawing a flat line that was never measured.
    """
    if not samples or not segments or count <= 0:
        return []
    spans = []
    cursor = 0.0
    for segment in segments:
        duration = float(segment.get("duration") or 0.0)
        if duration <= 0:
            continue
        rng = segment.get("range") or {}
        spans.append(
            (
                cursor,
                cursor + duration,
                None
                if segment.get("special")
                else (
                    float(rng.get("start") or 0.0),
                    float(rng.get("end") or 0.0),
                ),
            )
        )
        cursor += duration
    total_out = cursor
    if total_out <= 0:
        return []
    rate = float(getattr(samples, "sample_rate", 0) or 16000)
    peaks = [0.0] * count
    for bucket in range(count):
        out_lo = bucket / count * total_out
        out_hi = (bucket + 1) / count * total_out
        for seg_lo, seg_hi, span in spans:
            if span is None:
                continue
            lo = max(out_lo, seg_lo)
            hi = min(out_hi, seg_hi)
            if hi <= lo:
                continue
            src_lo = span[0] + (lo - seg_lo)
            src_hi = span[0] + (hi - seg_lo)
            i_lo = max(0, int(src_lo * rate))
            i_hi = min(len(samples), int(src_hi * rate))
            if i_hi > i_lo:
                level = max(abs(float(v)) for v in samples[i_lo:i_hi])
                if level > peaks[bucket]:
                    peaks[bucket] = level
    loudest = max(peaks) if peaks else 0.0
    if loudest <= 0:
        return []
    return [round(min(1.0, p / loudest), 4) for p in peaks]


def _waveform_peaks(config: WebAppConfig, timeline: Timeline) -> list:
    """The job's waveform peaks for the timeline band.

    The sound is read from the files this app already keeps for a
    job, in this order: the intake work folder's own recording of
    the source sound (``intake-work/audio.wav``, the same WAV the
    silence measurement reads), then the cleaned audio the config
    was wired with (a job folder's ``cleaned.wav``). Any read
    problem returns an empty list: the band then says there is no
    wave for this job, and nothing else on the page changes.
    """
    candidates = []
    if config.job_dir is not None:
        candidates.append(
            config.job_dir / FIRST_EDIT_WORK_NAME / "audio.wav"
        )
    if config.cleaned_wav:
        candidates.append(Path(config.cleaned_wav))
    for path in candidates:
        try:
            if not path.is_file():
                continue
            samples = speech.waveform_samples(path)
            peaks = waveform_peaks_for_samples(samples, timeline.segments)
            if peaks:
                return peaks
        except Exception:
            continue
    return []


def _caption_cues(config: WebAppConfig, transcript: dict, cuts: dict) -> list:
    """Caption cues for the band, when captions are on for this job.

    Captions are on when the job's clean caption file exists in
    the session, the same fact the page's captions rows already
    use. The cues are the caption file's own cues, grouped by the
    captions module from the kept words on the edited clock, with
    the filler words left out of the text exactly the way
    ``build_captions`` writes the file. Before any edit has built
    the caption file there are no cues to show, and the band's
    captions row stays hidden.
    """
    captions_path = config.session_dir / clean_captions.CAPTIONS_FILENAME
    if not captions_path.is_file():
        return []
    rows = clean_captions.kept_word_rows(transcript, cuts)
    cues = clean_captions.group_cues(rows)
    return [
        {
            "start": round(float(cue["start"]), 4),
            "end": round(float(cue["end"]), 4),
            "text": cue["text"],
        }
        for cue in cues
    ]


AFTER_REVIEW_VISITED_FILENAME = "after_review_visited.json"
CAPTIONS_PREF_FILENAME = "captions_pref.json"


def _captions_hide_fillers(config: WebAppConfig) -> bool:
    """The captions choice for this session: hide ums and uhs, or not.

    On unless the creator turned it off where the captions are
    handed back (the Revised edit card, and the finish card). The
    choice is the session's, like the front door's choices.
    """
    payload = _load_session_json(config, CAPTIONS_PREF_FILENAME, {})
    if isinstance(payload, dict) and isinstance(
        payload.get("hide_fillers"), bool
    ):
        return payload["hide_fillers"]
    return True


def _save_captions_hide_fillers(config: WebAppConfig, on: bool) -> None:
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        config.session_dir / CAPTIONS_PREF_FILENAME,
        {"hide_fillers": bool(on)},
    )


def _set_captions_hide_fillers(config: WebAppConfig, on: bool) -> bool:
    """Apply the captions choice wherever it is flipped from.

    The one path behind the captions switches (the Revised edit
    card and the finish card, through /api/captions-pref) and the
    Editor panel's clean-up chip (Batch E2): the choice is saved
    in the session; for a job the app took in itself it also
    updates the job's marker, and the caption file is rebuilt
    (on) or removed (off) right away, so what downloads always
    matches the choice. For a runner job the choice is recorded
    and a caption file that exists is removed when it is off;
    building captions stays the intake's own work. Returns
    whether a caption file is available afterwards.
    """
    _save_captions_hide_fillers(config, on)
    session = config.session_dir
    captions_path = session / clean_captions.CAPTIONS_FILENAME
    available = captions_path.is_file()
    if config.is_intake_job:
        marker_path = config.job_dir / intake_build.INTAKE_MARKER
        try:
            marker = _load_json(str(marker_path))
        except (OSError, ValueError):
            marker = {}
        marker["captions"] = on
        try:
            _write_json(marker_path, marker)
        except OSError:
            _log_raw_error("captions marker", OSError("write failed"))
        if on:
            try:
                clean_captions.build_captions(
                    config.job_dir,
                    _effective_cuts(config),
                    captions_path,
                )
                available = captions_path.is_file()
            except Exception as exc:
                _log_raw_error("clean captions", exc)
                available = captions_path.is_file()
        else:
            captions_path.unlink(missing_ok=True)
            available = False
    elif not on:
        captions_path.unlink(missing_ok=True)
        available = False
    return available


def _finish_state(config: WebAppConfig) -> dict:
    """What the finish card can honestly say (v0.6.1).

    The card is ready only when both halves are things the app can
    see: a built video sitting in the session (the latest render;
    the card labels it exactly that, never a claim about grading),
    and a visit to the review page where the changes are graded.
    For a job the app took in itself, the clean captions file rides
    along when it exists.
    """
    state = {
        "ready": False,
        "has_render": False,
        "review_visited": False,
        "is_intake": False,
        "captions_available": False,
    }
    if not config.has_job:
        return state
    state["has_render"] = (
        config.session_dir / SESSION_VIDEO_NAME
    ).is_file()
    state["review_visited"] = (
        config.session_dir / AFTER_REVIEW_VISITED_FILENAME
    ).is_file()
    state["is_intake"] = bool(config.is_intake_job)
    state["captions_available"] = (
        config.session_dir / clean_captions.CAPTIONS_FILENAME
    ).is_file()
    state["ready"] = state["has_render"] and state["review_visited"]
    return state


def _money_view(config: WebAppConfig) -> dict:
    """The money view a summary carries (v0.7.2): the loaded
    job's own costs when there is a job, and the running total
    on this Mac either way. With no job there is no job block,
    and nothing is read from the launch home's session."""
    session_entries = (
        costs.load_session_entries(config.session_dir)
        if config.has_job
        else []
    )
    return costs.money_view(session_entries, costs.load_money_entries())


def _no_job_summary(config: WebAppConfig) -> dict:
    """The summary when no job is loaded: every field the page
    reads, at its empty value, so the front door renders cleanly
    (drop zone, sample offer, sign-in check) with no counts to
    break and no error to show. ``has_job`` is how the page knows.
    """
    return {
        "has_job": False,
        "test_video": False,
        "job_name": None,
        "source_duration_s": 0,
        "output_duration_s": 0,
        "fps": 0,
        "kept_ranges": 0,
        "special_ranges": 0,
        "joins": 0,
        "words_total": 0,
        "words_kept": 0,
        "words_cut": 0,
        "counts": _empty_edit_counts(),
        "transcript_words": [],
        "timeline_pieces": [],
        "waveform_peaks": [],
        "caption_cues": [],
        "word_corrections": [],
        "videos": [],
        "playback": {},
        "playback_progress": {},
        "video_shapes": {},
        "feedback_exists": False,
        "comments": [],
        "overall_feedback": [],
        "edit_spans": [],
        # With no job, this config is the launch home's own, so its
        # front door settings are the launch session's: the defaults
        # overlaid with the creator's usual and any launch-session
        # choices. That is what pre-sets the drop block before a job
        # exists.
        "front_door": _front_door_settings(config),
        "flags": [],
        "gold": [],
        "editor_name": usual.load_editor_name(),
        "waiting_count": 0,
        "beta": {
            "flips": 0,
            "settled": 0,
            "matched": 0,
            "recent_calls": 0,
            "line": None,
        },
        "saved_rules": _saved_rules_view(config),
        "inspiration": _inspiration_view(config),
        "smooth": _smooth_view(config),
        "styles": _styles_view(config),
        "rule_suggestions": _rule_suggestions(config),
        "meta_available": bool(config.signin_key()),
        "render_missing": ["render_py", "source_video", "cleaned_wav"],
        "render_ready": False,
        "render_message": NO_JOB_MESSAGE,
        "captions_available": False,
        "captions_hide_fillers": True,
        "is_intake": False,
        "job_whole": False,
        "job_missing": [],
        "finish": _finish_state(config),
        "money": _money_view(config),
    }


def _incomplete_job_summary(config: WebAppConfig, missing: list) -> dict:
    """The summary for a job whose required files are not on disk.

    The job is named and loaded, but nothing that reads its files
    can run yet (an intake still arriving, a folder dropped before
    its pieces landed, a file removed mid-session). The page gets
    the empty shape plus ``job_whole: False``, so it can show the
    one plain sentence and keep every job action out of sight until
    the job is whole, instead of failing on the first missing file.
    """
    summary = _no_job_summary(config)
    videos = _job_videos(config.job_dir)
    states, progress = _playback_view(config, videos)
    summary.update(
        {
            "has_job": True,
            "test_video": _is_test_video(config),
            "job_name": config.job_dir.name,
            "videos": videos,
            "playback": states,
            "playback_progress": progress,
            "watching": _watching_state(config),
            "front_door": _front_door_settings(config),
            "render_missing": config.render_missing(),
            "render_ready": False,
            "render_message": JOB_INCOMPLETE_MESSAGE,
            "captions_hide_fillers": _captions_hide_fillers(config),
            "is_intake": bool(config.is_intake_job),
            "job_whole": False,
            "job_missing": list(missing),
            "finish": _finish_state(config),
        }
    )
    # The job is named and loaded, so its own recorded costs (if
    # any) belong in its money block even while it is incomplete.
    summary["money"] = _money_view(config)
    return summary


# ---------------------------------------------------------------------------
# The problem report (v0.10, FIX B): one zip the creator can send back
# ---------------------------------------------------------------------------

_KEY_SHAPED_RE = re.compile(
    r"sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{20,}|xai-[A-Za-z0-9_\-]{8,}"
)
_SECRET_PAIR_RE = re.compile(
    r"((?:api[_-]?key|access[_-]?token|secret|password|authorization)"
    r"\s*[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+",
    re.I,
)


def _scrub_report_text(text: str, secrets: list) -> str:
    """Take keys and key-shaped values out of one report file.

    The creator's own saved keys go first (an exact match beats
    any pattern), then anything shaped like a key, then the
    value half of any key-named pair.
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[key removed]")
    text = _KEY_SHAPED_RE.sub("[key removed]", text)
    text = _SECRET_PAIR_RE.sub(r"\1[key removed]", text)
    return text


def _problem_report_costs(config: WebAppConfig) -> str:
    """The costs-and-rounds page of the report, in plain words."""
    lines = ["Costs and rounds", ""]
    view = None
    try:
        view = _money_view(config)
    except Exception:
        view = None
    if view:
        job = view.get("job")
        if job:
            lines.append("This job:")
            for line in job.get("recap_lines", []):
                lines.append(f"  {line}")
        mac = view.get("mac") or {}
        lines.append(
            "On this computer so far: "
            f"{mac.get('total_text', '$0')} across "
            f"{mac.get('entries', 0)} recorded runs. That total "
            "covers every run saved on this computer, including "
            "runs from earlier copies of the app; it is not the "
            "cost of this job alone."
        )
    if config.has_job:
        retake = _load_session_json(config, RETAKE_PASS_FILENAME, {})
        runs = retake.get("runs") if isinstance(retake, dict) else None
        if isinstance(runs, list) and runs:
            lines.append(
                f"Retake check rounds on this job: {len(runs)}."
            )
        direction = _load_session_json(
            config, DIRECTION_PASS_FILENAME, {}
        )
        usage = (
            direction.get("usage") if isinstance(direction, dict)
            else None
        )
        if isinstance(usage, list) and usage:
            lines.append(
                f"Direction reads on this job: {len(usage)}."
            )
    if len(lines) <= 2:
        lines.append("No costs were recorded for this job.")
    return "\n".join(lines) + "\n"


def _problem_report_counts(config: WebAppConfig) -> str:
    """The cut-counts page of the report, in plain words."""
    lines = ["Cut counts", ""]
    if not config.has_job:
        lines.append("No job is open.")
        return "\n".join(lines) + "\n"
    try:
        transcript = _load_json(str(config.job_dir / "transcript.json"))
        timeline = Timeline(transcript, _effective_cuts(config))
        kept = timeline.kept_word_indices()
        lines.append(
            f"Words in the edit: {len(kept)} of "
            f"{len(timeline.words)}."
        )
        lines.append(
            f"Finished length: "
            f"{_format_clock(timeline.output_duration)} "
            f"(the recording was "
            f"{_format_clock(timeline.source_duration)})."
        )
    except Exception:
        lines.append("The cut counts could not be read for this job.")
    return "\n".join(lines) + "\n"


def _reveal_in_finder(path: Path) -> tuple:
    """Show one file in Finder; (opened, one plain sentence).

    The Mac's `open -R` selects the file in its folder, ready
    to drag. Anywhere else there is no Finder; the sentence
    then names the folder the file is in. Tests drive this
    seam directly, so the real `open` only ever runs on a Mac.
    """
    if sys.platform == "darwin":
        try:
            proc = subprocess.run(
                ["open", "-R", str(path)],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode == 0:
                return True, (
                    "Finder is showing you the file, picked out "
                    "in its folder. Drag it into the chat to send it."
                )
        except Exception:
            pass
        return False, (
            "Finder could not be opened. The file is in this "
            f"folder: {path.parent}"
        )
    return False, f"The file is in this folder: {path.parent}"


def _build_problem_report(config: WebAppConfig) -> Path:
    """Make the problem report zip and return its path.

    The zip lands in the job's folder, or in the app's home
    folder when no job is open. Its contents are a fixed list,
    nothing else: a first page, the session log, the playback
    log when there is one, the run audit files that exist, a
    costs-and-rounds page, and a cut-counts page. Every text
    file passes through the scrub before it is zipped.
    """
    from . import __version__ as app_version

    if config.has_job:
        base = config.job_dir
        job_label = config.job_dir.name
    else:
        base = config.launch_home
        job_label = "no-job"
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    name = f"problem-report-{job_label}-{stamp}.zip"
    secrets = []
    for getter in (config.signin_key, config.elevenlabs_key):
        try:
            value = getter()
        except Exception:
            value = None
        if value:
            secrets.append(value)
    pages = {
        "report.txt": (
            "Talk to Your Editor: problem report\n"
            f"App version: {app_version}\n"
            f"Job: {job_label if config.has_job else 'no job open'}\n"
            f"Made: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}\n"
        ),
        "costs-and-rounds.txt": _problem_report_costs(config),
        "cut-counts.txt": _problem_report_counts(config),
    }
    # The session log rides along whether or not a job is open
    # (v0.10.1). It used to be added only for a loaded job, so a
    # failure at the front door, before any job existed, produced
    # a report with no log in it at all: three short pages and no
    # way to see what had actually happened.
    session = config.session_dir
    log_text = session_log.read_log(session)
    if log_text:
        pages[session_log.SESSION_LOG_NAME] = log_text
    playback_logs = sorted(session.glob(f"playback/{PLAYBACK_LOG_NAME}"))
    if playback_logs:
        try:
            pages[PLAYBACK_LOG_NAME] = playback_logs[0].read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            pass
    if config.has_job:
        # The front door's own log lives in the launch home; when
        # this job was born there, its first-edit lines (and any
        # failure on the way in) are in that log, not this one.
        front_session = config.launch_home / SESSION_DIR_NAME
        if front_session != session:
            front_log = session_log.read_log(front_session)
            if front_log:
                pages["front-door-session.log"] = front_log
        for filename in (
            "operations_audit.json",
            DIRECTION_PASS_FILENAME,
            RETAKE_PASS_FILENAME,
        ):
            path = session / filename
            if path.is_file():
                try:
                    pages[filename] = path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError:
                    pass
    base.mkdir(parents=True, exist_ok=True)
    target = base / name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for arcname, text in pages.items():
            archive.writestr(
                arcname, _scrub_report_text(text, secrets)
            )
    return target


def _session_flags(config: WebAppConfig) -> list:
    """The direction pass's surviving flags, as the page shows them.

    Read from the session's flags file each time, so the summary
    always carries the latest pass's flags and nothing stale when
    no pass has run.
    """
    payload = _load_session_json(config, FLAGS_FILENAME, {})
    if isinstance(payload, dict) and isinstance(payload.get("flags"), list):
        return [flag for flag in payload["flags"] if isinstance(flag, dict)]
    return []


def _job_summary(config: WebAppConfig) -> dict:
    if not config.has_job:
        return _no_job_summary(config)
    missing = _job_files_missing(config)
    if missing:
        return _incomplete_job_summary(config, missing)
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    # The headline counts describe the edit as the session currently
    # has it, not as the job originally shipped: once anything has
    # revised the plan, the session's plan is the truth here too,
    # the same one the transcript view reads.
    cuts = _effective_cuts(config)
    timeline = Timeline(transcript, cuts)
    kept = timeline.kept_word_indices()
    transcript_entries, corrections = _transcript_entries(config)
    edit_spans = _edit_spans(config)
    # Unsure cuts (v0.11.1, fix item 5): a cut word inside a span
    # the retake pass marked unsure is named on its entry, so the
    # review can show those words as unsure cuts, apart from sure
    # cuts and from kept words.
    unsure_spans = [
        (span["word_start"], span["word_end"])
        for span in edit_spans
        if isinstance(span.get("retake"), dict)
        and span["retake"].get("unsure")
    ]
    if unsure_spans:
        for entry in transcript_entries:
            start = entry.get("index")
            end = entry.get("word_end")
            if not isinstance(start, int) or not isinstance(end, int):
                continue
            if entry.get("status") == "cut" and any(
                start < span_end and span_start < end
                for span_start, span_end in unsure_spans
            ):
                entry["unsure_cut"] = True
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
    # A comment's stored time is already on the edited video's
    # clock: it was pinned while watching the edited video, and
    # the machinery has always read it that way (the protected
    # joins resolve it with map_output_time_approx, and the
    # direction pass anchors comments by it). So the page's
    # edited-clock field is the stored time itself, unconverted;
    # converting it again would move every dot and seek. (The
    # transcript entries above are the opposite case: their times
    # are the recording's, and they do get converted.)
    for comment in comments:
        try:
            comment["edited_ts"] = float(comment.get("ts") or 0.0)
        except (TypeError, ValueError):
            comment["edited_ts"] = 0.0
    timeline_pieces = []
    for segment in timeline.segments:
        if segment["duration"] <= 0:
            continue
        piece_words = timeline.words_in_range(segment["range"])
        fade_in = segment["range"].get("fade_in_s")
        timeline_pieces.append(
            {
                "output_start": round(segment["output_start"], 4),
                "output_end": round(segment["output_end"], 4),
                "word_start": piece_words[0] if piece_words else None,
                "word_end": (
                    piece_words[-1] + 1 if piece_words else None
                ),
                # v0.16: what the timeline's hand edits need to know
                # about each piece: where it sits in the plan, where
                # it came from in the recording, whether it is a
                # special insert (never hand-edited), and the
                # crossfade set at the join that plays into it.
                "range_index": segment["range_index"],
                "source_start": round(float(segment["range"]["start"]), 4),
                "source_end": round(float(segment["range"]["end"]), 4),
                "special": bool(segment["special"]),
                "fade_in_s": (
                    round(float(fade_in), 4)
                    if isinstance(fade_in, (int, float))
                    and not isinstance(fade_in, bool)
                    else None
                ),
            }
        )
    playback_states, playback_progress = _playback_view(config, videos)
    splice_path = config.session_dir / SESSION_VIDEO_NAME
    if splice_path.is_file():
        splice_copy = _playback_copy_path(config, SESSION_VIDEO_NAME)
        splice_state = _playback_state_for(splice_path, splice_copy)
        if splice_state == "unstarted":
            # Same discipline as the job's own videos: a copy
            # nobody has started must be started here, on the
            # poll the page already runs, so the edited video's
            # swap-in can actually land (FIX A).
            _ensure_playback_copy_for(splice_path, splice_copy)
            splice_state = _playback_state_for(splice_path, splice_copy)
        playback_states[SESSION_VIDEO_NAME] = splice_state
        playback_progress[SESSION_VIDEO_NAME] = _playback_progress_for(
            splice_path, splice_copy
        )
    gold_views = _open_gold_spans(config)
    smooth_view = _smooth_view(config)
    # The one count (0.25, finding 6): computed once here, from
    # the final plan this summary describes, and carried on the
    # summary so every surface reads the same numbers.
    counts = _edit_counts(
        config,
        timeline=timeline,
        edit_spans=edit_spans,
        gold=gold_views,
        smooth=smooth_view,
    )
    return {
        "has_job": True,
        "test_video": _is_test_video(config),
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
        "counts": counts,
        "transcript_words": transcript_entries,
        "timeline_pieces": timeline_pieces,
        "waveform_peaks": _waveform_peaks(config, timeline),
        "caption_cues": _caption_cues(config, transcript, cuts),
        "word_corrections": corrections,
        "videos": videos,
        "playback": playback_states,
        "playback_progress": playback_progress,
        "watching": _watching_state(config),
        "video_shapes": video_shapes,
        "feedback_exists": feedback_exists,
        "comments": comments,
        "overall_feedback": overall_feedback,
        "edit_spans": edit_spans,
        "decision_rules_used": len(
            {
                record.get("decision_rule")
                for record in _load_gold(config)
                if record.get("settled_by_rule")
                and record.get("decision_rule")
            }
        ),
        "front_door": _front_door_settings(config),
        "flags": _session_flags(config),
        "gold": gold_views,
        "editor_name": usual.load_editor_name(),
        "waiting_count": _waiting_count(config),
        "beta": _beta_score(config),
        "saved_rules": _saved_rules_view(config),
        "inspiration": _inspiration_view(config),
        "smooth": smooth_view,
        "styles": _styles_view(config),
        "rule_suggestions": _rule_suggestions(config),
        "meta_available": bool(config.signin_key()),
        "render_missing": config.render_missing(),
        "render_ready": not config.render_missing(),
        "render_message": config.render_message(),
        "captions_available": (
            config.session_dir / clean_captions.CAPTIONS_FILENAME
        ).is_file(),
        "captions_hide_fillers": _captions_hide_fillers(config),
        "is_intake": bool(config.is_intake_job),
        "job_whole": True,
        "job_missing": [],
        "finish": _finish_state(config),
        "money": _money_view(config),
    }


def _plain_direction_error(exc: Exception) -> str:
    """The one plain sentence the page may show for a direction failure.

    Raw detail (HTTP status codes, schema reason codes) is for the
    server log only; a viewer gets words they can act on.
    """
    raw = str(exc)
    if isinstance(exc, ProviderError):
        for route in HOSTED_SPARK_ROUTES:
            label = ROUTE_LABELS[route]
            if raw.startswith((f"{route}_api_http_401", f"{route}_api_http_403")):
                return (
                    f"The {label} sign-in was not accepted. Check "
                    "the key and try again."
                )
            if raw.startswith(f"{route}_api_http_429"):
                return (
                    f"{label}'s service is busy right now. Wait a "
                    "moment and try again."
                )
            if raw.startswith(f"{route}_api_http_"):
                return (
                    f"{label}'s service returned an error. Try "
                    "again in a moment."
                )
            if raw.startswith(f"{route}_api_unreachable"):
                return (
                    f"{label}'s service could not be reached. "
                    "Check the connection and try again."
                )
            if raw == f"{route} key is not set":
                return f"No {label} key is set on this server yet."
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


# ---------------------------------------------------------------------------
# Keys saved on this Mac (v0.6.1)
#
# The launcher has always read two key files from the creator's
# home folder at start: ~/.talk-to-your-editor/meta_key and
# ~/.talk-to-your-editor/elevenlabs_key. Until now nothing in the
# app ever wrote them, so a key pasted on the page died with the
# session and every restart asked again. The sign-in card's "Save
# on this Mac" action writes the same files, with the same
# permissions the original save flow used (folder 700, file 600),
# so a saved key survives restarts and new kit downloads alike:
# the files live in the home folder, never in the kit. The key
# itself is never sent back to the page; the state below carries
# only whether a saved key exists.
# ---------------------------------------------------------------------------

MAC_KEY_DIR_NAME = ".talk-to-your-editor"
MAC_KEY_FILES = {
    "meta": "meta_key",
    "elevenlabs": "elevenlabs_key",
    "baseten": "baseten_key",
    "fireworks": "fireworks_key",
}
KEY_KINDS = tuple(MAC_KEY_FILES)
# The session attribute each kind's pasted key lives in.
_SESSION_KEY_ATTRS = {
    "meta": "session_api_key",
    "elevenlabs": "session_elevenlabs_key",
    "baseten": "session_baseten_key",
    "fireworks": "session_fireworks_key",
}
KEY_KIND_LABELS = {
    "meta": "Meta",
    "elevenlabs": "ElevenLabs",
    "baseten": "Baseten",
    "fireworks": "Fireworks",
}


def _mac_key_path(kind: str) -> Path:
    return Path.home() / MAC_KEY_DIR_NAME / MAC_KEY_FILES[kind]


def _mac_key_saved(kind: str) -> bool:
    """Whether a usable key is saved on this Mac for ``kind``."""
    try:
        text = _mac_key_path(kind).read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(text.strip())


def _save_mac_key(kind: str, key: str) -> None:
    """Write one key file, private the way the original flow made it."""
    path = _mac_key_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    path.write_text(key.strip() + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_mac_key(kind: str) -> Optional[str]:
    try:
        text = _mac_key_path(kind).read_text(encoding="utf-8")
    except OSError:
        return None
    return text.strip() or None


def _remove_mac_key(kind: str) -> None:
    try:
        _mac_key_path(kind).unlink()
    except OSError:
        pass


def _set_session_key(config: WebAppConfig, kind: str, key: Optional[str]) -> None:
    setattr(config, _SESSION_KEY_ATTRS[kind], key)


def _session_key(config: WebAppConfig, kind: str) -> Optional[str]:
    return getattr(config, _SESSION_KEY_ATTRS[kind], None)


def _signin_state(config: WebAppConfig) -> dict:
    """What the page may know about the sign-ins: set or not, where
    they came from, and whether each is saved on this Mac. The keys
    themselves are never part of this."""
    source = config.signin_source()
    return {
        "meta_signin_set": source is not None,
        "source": source,
        "meta_saved_on_mac": _mac_key_saved("meta"),
        "elevenlabs_signin_set": bool(config.elevenlabs_key()),
        "elevenlabs_saved_on_mac": _mac_key_saved("elevenlabs"),
        "baseten_signin_set": bool(config.baseten_key()),
        "baseten_saved_on_mac": _mac_key_saved("baseten"),
        "fireworks_signin_set": bool(config.fireworks_key()),
        "fireworks_saved_on_mac": _mac_key_saved("fireworks"),
        "spark_route": config.spark_route(),
        "spark_route_effective": config.spark_route_effective(),
    }


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


def _check_hosted(config: WebAppConfig, route: str) -> Optional[dict]:
    """Check one hosted route's key, when one is set.

    Returns None when the route has no key at all (nothing to
    check, and nothing to say). Otherwise {accepted, message}:
    an accepted key names the pool Spark work can ride; a
    refused key says so in plain words and names the standing
    fallback, that Spark work runs on Meta until the key works.
    """
    key = config.hosted_key(route)
    if not key:
        return None
    label = ROUTE_LABELS[route]
    try:
        accepted = check_hosted_signin(route, key)
    except ProviderError as exc:
        _log_raw_error(f"{route} signin check", exc)
        return {"accepted": False, "message": _plain_direction_error(exc)}
    if accepted:
        return {
            "accepted": True,
            "message": (
                f"The {label} sign-in was accepted. Spark work "
                f"can ride the {label} credits."
            ),
        }
    return {
        "accepted": False,
        "message": (
            f"The {label} sign-in was not accepted. Check the "
            "key and try again. Until it works, Spark work "
            "runs on Meta."
        ),
    }


ELEVENLABS_USER_URL = "https://api.elevenlabs.io/v1/user"


def _elevenlabs_get(url: str, api_key: str) -> tuple:
    """One GET against ElevenLabs; returns (status, payload).

    The seam the ElevenLabs key check reads through, so tests
    can answer it offline. A connection that cannot be made at
    all returns status 0 with the error as the payload text.
    """
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url, headers={"xi-api-key": api_key}, method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", "replace")
        status = err.code
    except Exception as exc:  # no answer at all
        return 0, str(exc)
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def _check_elevenlabs(config: WebAppConfig) -> Optional[dict]:
    """Check the ElevenLabs key the way the hosted routes check.

    Returns None when no key is set (nothing to check). Otherwise
    {accepted, message}: the check asks ElevenLabs for the
    account page, a harmless read, so a wrong key is caught in
    Settings instead of halfway through a first edit, where until
    now it surfaced as a doubled sentence that named no reason.
    """
    key = config.elevenlabs_key()
    if not key:
        return None
    status, payload = _elevenlabs_get(ELEVENLABS_USER_URL, key)
    if status == 200:
        return {
            "accepted": True,
            "message": "The ElevenLabs key was accepted.",
        }
    if status in (401, 403):
        return {
            "accepted": False,
            "message": (
                "The ElevenLabs key was not accepted. Check the "
                "key in Settings and try again."
            ),
        }
    if status == 0:
        return {
            "accepted": False,
            "message": (
                "The ElevenLabs key could not be checked: the "
                "app could not reach ElevenLabs."
            ),
        }
    return {
        "accepted": False,
        "message": (
            "The ElevenLabs key could not be checked: "
            f"ElevenLabs answered with an error (HTTP {status})."
        ),
    }


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


def _live_provider(
    config: WebAppConfig, style: Optional[str] = None
) -> MetaProvider:
    """The live provider, built the one way every run builds it.

    The Meta sign-in keys it; the creator's chosen Spark route
    and any hosted keys ride along, so Spark-tier work (the
    direction reads, the retake judge and its shadow) runs on
    the chosen pool while Wonder stays on Meta. A style is passed
    only by the passes that pick one (the direction pass and the
    retake pass); with no style named, the provider loads its
    default style, as it always has.
    """
    kwargs: dict = {}
    if style is not None:
        kwargs["style"] = style
    return MetaProvider(
        api_key=config.signin_key(),
        allow_wonder=True,
        spark_route=config.spark_route(),
        baseten_key=config.baseten_key(),
        fireworks_key=config.fireworks_key(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# The direction pass holds its own proposals (0.27.2)
#
# A keyed front-door run hands the model the retakes guidance and
# no direction from the creator, and the model's proposals are
# applied through the verifier like any other edit. On Justin's
# 0.27.1 Mac run that read removed two things no local pass had
# touched: a whole sentence said nowhere else in the video (the
# first statement of the thyroid measurement, read as if it were
# the later restatement of it) and one side of a parallel line
# the retake pass had already set aside for his call. A model
# proposal is a suggestion, not a decision: in the direction
# paths, a proposed cut that removes unique content, or that
# touches a span already waiting open for the creator's call,
# is held for review instead of applied. The guard used to
# stand down for any run carrying the creator's own words; on
# the keyed 0.27.3 run the same two cuts went through under a
# typed direction, and Justin's filming-day ruling ends the
# stand-down: a sentence with no retake partner is never cut
# by a proposal, whatever the direction says.
# ---------------------------------------------------------------------------

_PROVIDER_HOLD_YOUR_CALL = "your_call_span"
_PROVIDER_HOLD_UNIQUE = "unique_content"
_PROVIDER_HOLD_FINAL_SAYING = "final_saying"
_UNIQUE_CONTENT_MIN_TOKENS = 3
_UNIQUE_CONTENT_MIN_SHARE = 0.25


def _named_content_tokens(text) -> set:
    """The content tokens in a person's own words."""
    tokens = set()
    for raw in str(text or "").split():
        token = retakes.normalize_word(raw)
        if len(token) >= 3 and token not in _SPOT_STOPWORDS:
            tokens.add(token)
    return tokens


def _feedback_comment_tokens(feedback: dict) -> set:
    """Content tokens from the run's actual comments (the
    creator's own notes), never from overall guidance the app
    composed for the run."""
    tokens: set = set()
    comments = (
        feedback.get("comments") if isinstance(feedback, dict) else None
    )
    for comment in comments or []:
        if isinstance(comment, dict):
            tokens |= _named_content_tokens(comment.get("text"))
    return tokens


def _sentence_spans(words) -> list:
    """(start, end) word ranges of the transcript's sentences,
    by punctuation alone (the final sweep's sentence ends)."""
    ends = sorted(final_sweep.punctuation_sentence_ends(words))
    spans = []
    start = 0
    for end in ends:
        spans.append((start, end + 1))
        start = end + 1
    if start < len(words):
        spans.append((start, len(words)))
    return spans


def _sentence_has_partner(words, timeline, sent, in_spans) -> bool:
    """True when another kept sentence outside the cut says
    this sentence's thought again: at least half of its content
    words covered by that one other sentence. A first statement
    later corrected ("2.18" then "actually it was 2.16") shares
    too little to count; a retake partner is the same thought
    said again."""
    tokens, raw = final_sweep._content_tokens(words, sent[0], sent[1])
    if not tokens:
        return True
    for other in _sentence_spans(words):
        if other[0] < sent[1] and sent[0] < other[1]:
            continue
        if any(i in in_spans for i in range(other[0], other[1])):
            continue
        kept = sum(
            1
            for i in range(other[0], other[1])
            if timeline.word_status(i) == "kept"
        )
        if kept < (other[1] - other[0]) // 2:
            continue
        pool, _ = final_sweep._content_tokens(words, other[0], other[1])
        if final_sweep._coverage(tokens, raw, pool) >= 0.5:
            return True
    return False


def _provider_cut_guard(config: WebAppConfig, named_tokens=frozenset()):
    """An ``adjust_verdicts`` callback for the direction paths.

    A model proposal is a suggestion, not a decision. The guard
    flips an applying provider-proposed cut to a hold when the
    cut touches an open your-call span or the video's final
    saying, or when it would remove unique content. Since
    0.27.3's keyed run cut the July measurement passage and the
    parallel "my wife" line AGAIN on a run carrying the
    creator's own typed direction, the guard no longer stands
    down for any of its holds: a sentence with no retake partner
    elsewhere in the transcript is never cut by a proposal,
    whatever the direction says (Justin's ruling, filming day).
    Unique content is read two ways: the span's own tokens (at
    least three said nowhere else in the kept speech, at least
    a quarter of its content), and sentence by sentence (a cut
    removing most of a sentence whose thought no other kept
    sentence says again). The retake, filler, and silence
    passes and the gold verdict path do not use it; their cuts
    are local decisions or the creator's own verdicts.
    """
    from collections import Counter

    def adjust(verdicts, timeline, profile):
        open_spans = [
            (view["word_start"], view["word_end"])
            for view in _open_gold_spans(config)
            if isinstance(view.get("word_start"), int)
            and isinstance(view.get("word_end"), int)
        ]
        words = timeline.words
        # The video's final saying, on the plan as it stands:
        # a proposed cut that touches it is held like the rest
        # (0.27.3). The final sweep is the backstop that puts
        # it back if any other pass cut it; this guard keeps a
        # model proposal from taking it in the first place.
        final_span = None
        try:
            sweep_bounds = final_sweep.word_bounds(words, timeline.cuts)
            sweep_ends = final_sweep.punctuation_sentence_ends(words)
            final_span = final_sweep.final_saying_of_video(
                words, sweep_bounds, sweep_ends
            )
        except Exception:
            final_span = None
        for verdict in verdicts:
            op = verdict.operation
            if (
                verdict.status != "apply"
                or op.type != "cut_words"
                or getattr(op, "decided", False)
            ):
                continue
            spans = [
                (start, end)
                for start, end, role in op.word_spans()
                if role == "cut"
            ]
            if not spans:
                continue
            reasons = []
            if final_span is not None and any(
                start < final_span[1] and final_span[0] < end
                for start, end in spans
            ):
                reasons.append(_PROVIDER_HOLD_FINAL_SAYING)
            if any(
                start < g_end and g_start < end
                for start, end in spans
                for g_start, g_end in open_spans
            ):
                reasons.append(_PROVIDER_HOLD_YOUR_CALL)
            span_tokens = []
            in_spans: set = set()
            for start, end in spans:
                in_spans.update(range(start, end))
                for i in range(start, end):
                    token = retakes.normalize_word(
                        words[i].get("word", "")
                    )
                    if (
                        len(token) >= 3
                        and token not in _SPOT_STOPWORDS
                    ):
                        span_tokens.append(token)
            distinct = set(span_tokens)
            unique_held = False
            if distinct:
                outside_counts: Counter = Counter()
                for i, word in enumerate(words):
                    if i in in_spans:
                        continue
                    if timeline.word_status(i) != "kept":
                        continue
                    token = retakes.normalize_word(
                        word.get("word", "")
                    )
                    if (
                        len(token) >= 3
                        and token not in _SPOT_STOPWORDS
                    ):
                        outside_counts[token] += 1
                unique = {
                    token
                    for token in distinct
                    if outside_counts.get(token, 0) == 0
                }
                if (
                    len(unique) >= _UNIQUE_CONTENT_MIN_TOKENS
                    and len(unique) / len(distinct)
                    >= _UNIQUE_CONTENT_MIN_SHARE
                ):
                    unique_held = True
            if not unique_held:
                # Sentence by sentence: a cut removing most of a
                # sentence no other kept sentence says again has
                # no retake partner to justify it, whatever the
                # run carries.
                for sent in _sentence_spans(words):
                    removed = sum(
                        1 for i in range(sent[0], sent[1])
                        if i in in_spans
                    )
                    if removed * 2 < (sent[1] - sent[0]):
                        continue
                    if not _sentence_has_partner(
                        words, timeline, sent, in_spans
                    ):
                        unique_held = True
                        break
            if unique_held:
                reasons.append(_PROVIDER_HOLD_UNIQUE)
            if reasons:
                verdict.status = "flag"
                verdict.reasons.extend(reasons)
                verdict.risk_tier = 3
                verdict.required_tier = max(verdict.required_tier, 3)
        return verdicts

    return adjust


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
        provider = _live_provider(config)
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
    audit = _verify_and_apply(
        args,
        {"operations": operations},
        session,
        adjust_verdicts=_provider_cut_guard(
            config, named_tokens=_feedback_comment_tokens(run_feedback)
        ),
    )
    audit_with_routing = dict(audit)
    audit_with_routing["routing"] = routing
    # Record what the run was handed, so the saved audit shows the
    # format it served and the overall guidance it carried.
    audit_with_routing["format"] = fmt
    audit_with_routing["overall_feedback"] = run_feedback["overall_feedback"]
    audit_with_routing["saved_rules"] = rule_statements
    audit_with_routing["edit_notes"] = run_feedback["edit_notes"]
    # What the run's model calls cost rides the audit, so every
    # caller can account the run the same way (v0.7.2). The drain
    # covers the Spark direction call and any Wonder reviews the
    # routing made on this same provider.
    drain = getattr(provider, "drain_usage", None)
    audit_with_routing["usage"] = (drain() or []) if callable(drain) else []
    drain_events = getattr(provider, "drain_route_events", None)
    audit_with_routing["route_events"] = (
        (drain_events() or []) if callable(drain_events) else []
    )
    if front_door is not None:
        audit_with_routing["front_door"] = front_door
    # Number the round before the audit is saved, so the saved audit
    # and the archived copy both carry it.
    round_number = _next_round_number(config)
    audit_with_routing["round"] = round_number
    _write_json(session / "operations_audit.json", audit_with_routing)
    _archive_direction_round(config, round_number)
    return audit_with_routing


# ---------------------------------------------------------------------------
# Cost accounting (v0.7.2): what each run cost, per feature
#
# Every figure comes from the costs module, the one place rates
# live. The writers below only translate a finished run's own
# records (captured token usage, the transcription route and
# duration, a dictation's length) into cost entries, and hand
# them to _account_costs. Accounting never changes what a run
# does, and it never raises: a lost entry loses a line of
# bookkeeping, never an edit.
# ---------------------------------------------------------------------------


def _account_costs(config: WebAppConfig, entries: list) -> None:
    """Record a run's cost entries: the job's own cost record in
    its session, and the running total on this Mac."""
    if not entries:
        return
    costs.append_session_entries(config.session_dir, entries)
    costs.append_money_entries(entries, config.job_label())


def _direction_cost_entries(usage) -> list:
    """The cost entries for one direction run's captured usage:
    the Spark read under Reading your direction, charged to the
    pool whose route actually served it (v0.8.1), any Wonder
    escalation under its own line (Wonder is always Meta's),
    and the also-noticed flags at $0 (they ride along on the
    direction read, so they carry its pool)."""
    by_route = costs.split_usage_cost_by_route(usage)
    routes = sorted(by_route, key=lambda r: costs.POOL_ORDER[r])
    entries = []
    for route in routes:
        spark_usd, _wonder_usd = by_route[route]
        entries.append(
            costs.make_entry(
                "direction", spark_usd, "direction run", pool=route
            )
        )
    wonder_usd = sum(pair[1] for pair in by_route.values())
    if wonder_usd > 0:
        entries.append(
            costs.make_entry("wonder", wonder_usd, "direction run", pool="meta")
        )
    if not entries:
        entries.append(
            costs.make_entry("direction", 0.0, "direction run", pool="meta")
        )
    entries.append(
        costs.make_entry(
            "flags",
            0.0,
            "rides along with the direction read",
            pool=routes[0] if routes else "meta",
        )
    )
    return entries


def _usage_cost_entries(feature: str, usage, detail: str) -> list:
    """One feature's cost entries from a pass summary's usage,
    one per pool the usage drew on.

    Spark usage prices onto the feature's own line; any Wonder
    usage prices onto the Second review line instead (v0.9.0,
    FLAG C). Folding Wonder dollars into the feature's line is
    how a real job once showed about 97 cents for a retake check
    published at about 2 cents: the line must only ever carry
    the model the feature's price describes. Wonder is always
    Meta's, whatever route an entry claims.
    """
    by_route = costs.split_usage_cost_by_route(usage)
    if not by_route:
        return [costs.make_entry(feature, 0.0, detail, pool="meta")]
    entries = [
        costs.make_entry(feature, spark_usd, detail, pool=route)
        for route, (spark_usd, _wonder_usd) in sorted(
            by_route.items(), key=lambda item: costs.POOL_ORDER[item[0]]
        )
    ]
    wonder_usd = sum(pair[1] for pair in by_route.values())
    if wonder_usd > 0:
        entries.append(
            costs.make_entry("wonder", wonder_usd, detail, pool="meta")
        )
    return entries


def _first_edit_cost_entries(
    route: str,
    duration_s: float,
    settings: dict,
    retake_summary,
    direction_summary,
    reused: bool = False,
) -> list:
    """The cost entries for one first edit, from its passes' records.

    A run that used a saved transcript records the transcription
    at no cost: the words were already paid for (or made on this
    computer) on the earlier run, and charging again here would
    say the reuse saved nothing.
    """
    if reused:
        entries = [
            costs.make_entry(
                "transcription",
                0.0,
                "saved transcript from an earlier run",
                pool="elevenlabs" if route == "scribe" else "local",
            )
        ]
    else:
        entries = [
            costs.make_entry(
                "transcription",
                costs.transcription_cost(route, duration_s),
                "ElevenLabs Scribe" if route == "scribe" else "on this computer",
                duration_s=duration_s if route == "scribe" else None,
                pool="elevenlabs" if route == "scribe" else "local",
            )
        ]
    if retake_summary is not None:
        entries.extend(
            _usage_cost_entries(
                "retake_judge", retake_summary.get("usage"), "first edit"
            )
        )
    if direction_summary is not None and direction_summary.get("ran"):
        entries.extend(
            _direction_cost_entries(direction_summary.get("usage"))
        )
    if settings.get("remove_fillers"):
        entries.append(
            costs.make_entry("filler", 0.0, "on this computer", pool="local")
        )
    if settings.get("cut_silence"):
        entries.append(
            costs.make_entry("silence", 0.0, "on this computer", pool="local")
        )
    return entries


def _protected_join_indices(config: WebAppConfig, timeline: Timeline) -> set:
    """Joins the silence pass must never trim.

    A join is protected when one of the two words that border it is
    inside a protected span (a protected moment from the profile or
    the plan), or when a pinned comment resolves to one of those
    two words: the creator pointed at that exact spot, so its pause
    is theirs to judge, not the pass's. Intention cannot be
    measured; protection, the default, and the transcript dots a
    trim leaves behind are how it is carried.
    """
    protected: set = set()
    joins = timeline.joins()
    if not joins:
        return protected
    border_words: dict = {}
    for join in joins:
        index = join["join_index"]
        prev_words = timeline.words_in_range(timeline.ranges[index])
        next_words = timeline.words_in_range(timeline.ranges[index + 1])
        border_words[index] = (
            prev_words[-1] if prev_words else None,
            next_words[0] if next_words else None,
        )
    profile = _load_optional_file(config.job_dir / "profile.json")
    protected_words: set = set()
    for span in timeline.protected_spans(profile):
        protected_words.update(
            range(span["word_start"], span["word_end"])
        )
    pinned_words: set = set()
    feedback_path = _feedback_path(config)
    if feedback_path is not None:
        try:
            payload = _load_json(str(feedback_path))
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict):
            for comment in payload.get("comments") or []:
                if not isinstance(comment, dict):
                    continue
                ts = comment.get("ts")
                if ts is None:
                    continue
                try:
                    resolved = timeline.map_output_time_approx(float(ts))
                except (TypeError, ValueError):
                    resolved = None
                if resolved is not None and resolved.get("word_index") is not None:
                    pinned_words.add(resolved["word_index"])
    for index, (last_prev, first_next) in border_words.items():
        if last_prev in protected_words or first_next in protected_words:
            protected.add(index)
        elif last_prev in pinned_words or first_next in pinned_words:
            protected.add(index)
    return protected


def _silence_operations(
    config: WebAppConfig, buffer_s: float
) -> list:
    """Join-pause operations that trim silence toward the buffer.

    The buffer is the silence left between one word and the next:
    the tail of quiet at the end of one kept piece plus the lead-in
    before the next word. A join whose silence already fits the
    buffer is left alone (silence is never added), and a join beside
    a special insert is never proposed. Each proposal is an ordinary
    ``set_join_pause`` operation, so the safety verifier and the
    applier decide what actually changes, exactly as they do for a
    note that asks for a shorter pause.

    Breath sounds inside a trimmed gap go with the quiet: there
    is no keep-breaths branch (removed in v0.10). Joins a pinned
    comment or a protected moment covers are never proposed (see
    ``_protected_join_indices``); every trim that does land stays
    a transcript dot with its reason, and restorable.
    """
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    timeline = Timeline(transcript, _effective_cuts(config))
    effective_s = buffer_s
    protected = _protected_join_indices(config, timeline)
    operations = []
    for join in timeline.joins():
        if join["prev_special"] or join["next_special"]:
            continue
        index = join["join_index"]
        if index in protected:
            continue
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
        if prev_tail + head <= effective_s + 1e-3:
            continue
        target = effective_s - prev_tail
        if target < MIN_JOIN_PAUSE_S:
            target = MIN_JOIN_PAUSE_S
        target = min(target, MAX_JOIN_PAUSE_S)
        if target >= head - 1e-3:
            continue  # This join cannot get any tighter from its head.
        reason = (
            "front door: trim the silence at this join "
            f"toward {effective_s:g} seconds"
        )
        operations.append(
            {
                "type": "set_join_pause",
                "comment_id": SILENCE_COMMENT_ID,
                "join_index": index,
                "target_pause_s": round(target, 3),
                "reason": reason,
                "confidence": 1.0,
                # A deterministic local proposal, like the mock's;
                # the comment id and the reason carry its provenance.
                "model": "mock",
                "risk_tier": 1,
                "wonder_reviewed": False,
            }
        )
    return operations


def _run_silence_pass(
    config: WebAppConfig, buffer_s: float
) -> dict:
    """Trim the session plan's long silences toward ``buffer_s``.

    Runs the join-pause proposals through the same verify-and-apply
    path as a direction run, against the session's current plan. The
    pass is recorded in its own session file, and its page
    operations are merged into the session audit so the removed and
    shortened moments stay explained in one place.
    """
    session_log.log_stage(config.session_dir, "silence", "start")
    summary = {
        "ran": True,
        "buffer_s": buffer_s,
        "joins_trimmed": 0,
        "operations": [],
    }
    operations = _silence_operations(config, buffer_s)
    if not operations:
        _record_silence_pass(config, summary)
        session_log.log_stage(config.session_dir, "silence", "finish")
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
        session_log.log_stage(
            config.session_dir, "silence", "fail",
            "the pass could not run; the plan was left as it was",
        )
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
    session_log.log_stage(config.session_dir, "silence", "finish")
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


def _run_smooth_pass(config: WebAppConfig) -> dict:
    """SMOOTH, NEVER CHOPPY (Message 2R, P0): set the plan's join
    edges on the measured quiet, then run the join check.

    Runs after the silence pass on the session's current plan.
    The edges move only inside the gap between two words' real
    sounds (measured from the extracted WAV against the room's
    own background level), breaths stay whole, and any join that
    cannot be made smooth without a judgment call is left alone
    and recorded as a "your call" spot in the session's
    smooth_pass.json. The revised plan is written back to the
    session exactly where the silence pass writes it.
    """
    summary = {"ran": False}
    try:
        transcript = _load_json(str(config.job_dir / "transcript.json"))
        words = transcript.get("words") or []
        if not words:
            return summary
        cuts = _effective_cuts(config)
        ranges = cuts.get("kept_ranges") or []
        if len(ranges) < 2:
            return summary
        wav_path = config.job_dir / FIRST_EDIT_WORK_NAME / "audio.wav"
        if not wav_path.is_file():
            source_name = intake_build.intake_source_video(config.job_dir)
            if not source_name:
                return summary
            config.session_dir.mkdir(parents=True, exist_ok=True)
            wav_path = config.session_dir / ".smooth-audio.wav"
            speech.extract_audio(config.job_dir / source_name, wav_path)
        env = smooth.envelope_db(wav_path)
        if not env:
            return summary
        timeline = Timeline(transcript, cuts)
        protected = _protected_join_indices(config, timeline)
        segments = transcript.get("segments")
        fps = timeline.fps
        # Silence from the audio, inside kept pieces (0.24.0,
        # Justin's 0.23 Mac test): transcript word times stretch
        # over pauses, so the join-only silence pass left long
        # silences sitting inside pieces (66 of 0.5 s or more in
        # his measured output against 1 in his finished edit).
        # Split the pieces at the silences the sound itself
        # measures, before the join edges are set, so the plan,
        # the transcript dots, and the counts all see the real
        # trimmed gaps.
        protected_words: set = set()
        profile_path = config.job_dir / "profile.json"
        if profile_path.is_file():
            try:
                profile = _load_json(str(profile_path))
            except Exception:
                profile = None
            if isinstance(profile, dict):
                for span in timeline.protected_spans(profile):
                    protected_words.update(
                        range(
                            int(span["word_start"]), int(span["word_end"])
                        )
                    )
        # A your-call span is never cut without the creator's
        # yes (0.27, fix 8): words waiting open in gold are
        # protected through every stage of this pass (the span
        # trim, the sentence guard's fragment rule, and the
        # sound truth remove step all read this set), so no
        # later pass can take a piece out of a span the creator
        # has not decided.
        for view in _open_gold_spans(config):
            gold_start = view.get("word_start")
            gold_end = view.get("word_end")
            if isinstance(gold_start, int) and isinstance(gold_end, int):
                protected_words.update(range(gold_start, gold_end))
        ranges_before = ranges
        spans = speech.measure_speech_spans(wav_path)
        ranges, internal = smooth.trim_internal_silences(
            words, ranges, env,
            segments=segments, protected_words=protected_words, fps=fps,
            spans=spans,
        )
        new_ranges, report = smooth.smooth_plan(
            words, ranges, env,
            segments=segments, protected_joins=protected, fps=fps,
        )
        # The edge setters work from word sound bounds, which run
        # wide in ambient noise, and can hand a piece back a long
        # quiet head or tail the span trim just removed. Run the
        # span trim once more on the shaped plan so the plan that
        # ships is the trimmed one; the join check then measures
        # the final edges.
        new_ranges, internal2 = smooth.trim_internal_silences(
            words, new_ranges, env,
            segments=segments, protected_words=protected_words, fps=fps,
            spans=spans,
        )
        internal = {
            "trimmed": internal["trimmed"] + internal2["trimmed"],
            "seconds_removed": round(
                internal["seconds_removed"]
                + internal2["seconds_removed"],
                4,
            ),
            "details": internal["details"] + internal2["details"],
        }
        # The settled retake record, read before the guard and
        # the final check below: words a settled retake cut took
        # out are blocked, so backing a start up never revives a
        # take, and a kept take's first word is a line start for
        # the final check (a flagged pair's words stay in the
        # plan awaiting review, so they do not block). The first
        # join check keeps its original form on purpose: the
        # zero push proved this exact sequence (check, guard,
        # guard) on the Day 30 pair, and the final check is the
        # only new measurement.
        blocked_words: set[int] = set()
        applied_cut_words: set[int] = set()
        take_start_words: set[int] = set()
        try:
            retake_record = json.loads(
                (config.session_dir / "retake_pass.json").read_text(
                    encoding="utf-8"
                )
            )
        except Exception:
            retake_record = {}
        if isinstance(retake_record, dict):
            for pair in retake_record.get("pairs") or []:
                if not isinstance(pair, dict):
                    continue
                if pair.get("outcome") not in ("applied", "held"):
                    continue
                cut_side = pair.get("cut") or {}
                cs = cut_side.get("word_start")
                ce = cut_side.get("word_end")
                if isinstance(cs, int) and isinstance(ce, int):
                    blocked_words.update(range(cs, ce))
                    if pair.get("outcome") == "applied":
                        # Settled cuts only: the sound-truth pass
                        # below treats these as words the plan
                        # means to keep out. A held pair is still
                        # the creator's call, so its words are
                        # never its targets.
                        applied_cut_words.update(range(cs, ce))
                keep_side = pair.get("keep") or {}
                ks = keep_side.get("word_start")
                if isinstance(ks, int):
                    # The kept take's first word is a line start:
                    # when a settled cut took the sentence head,
                    # the edited sentence begins there (0.25.0,
                    # the zero push).
                    take_start_words.add(ks)
        new_ranges, check = smooth.join_check(
            words, new_ranges, env, segments=segments, fps=fps,
        )
        # Edge setting on a sliver piece can leave its start a
        # hair past its end; a piece with no length carries no
        # words and must not reach the plan or the renderer. A
        # short piece holding measured speech sound is content,
        # not a sliver, and stays (0.27, fix 7).
        sound_bounds = smooth.sound_word_bounds(words, env, spans)
        kept_sound_mids = [
            (float(sound_bounds[i][0]) + float(sound_bounds[i][1])) / 2.0
            for i in range(len(words))
            if timeline.word_status(i) == "kept"
        ]
        new_ranges = smooth.drop_soundless_pieces(
            words, new_ranges, sound_bounds, kept_mids=kept_sound_mids,
        )
        # Sentence starts (0.25.0): a kept piece never starts
        # mid-sentence. This runs after the join check, whose own
        # edge fixes can shift a start past a head word, and the
        # span trim runs once more after it, because a restored
        # head can carry dead air the earlier trims never saw;
        # a second guard pass then repairs anything the trim's
        # splits separated.
        new_ranges, guard_report = smooth.enforce_sentence_starts(
            words, new_ranges, env,
            segments=segments, protected_words=protected_words,
            blocked_words=blocked_words,
            take_start_words=take_start_words, fps=fps, spans=spans,
        )
        new_ranges, internal3 = smooth.trim_internal_silences(
            words, new_ranges, env,
            segments=segments, protected_words=protected_words, fps=fps,
            spans=spans,
        )
        internal = {
            "trimmed": internal["trimmed"] + internal3["trimmed"],
            "seconds_removed": round(
                internal["seconds_removed"]
                + internal3["seconds_removed"],
                4,
            ),
            "details": internal["details"] + internal3["details"],
        }
        new_ranges, guard_report2 = smooth.enforce_sentence_starts(
            words, new_ranges, env,
            segments=segments, protected_words=protected_words,
            blocked_words=blocked_words,
            take_start_words=take_start_words, fps=fps, spans=spans,
        )
        guard_report = {
            "fixed": guard_report["fixed"] + guard_report2["fixed"],
            "left": guard_report2["left"],
            "details": guard_report["details"] + guard_report2["details"],
        }
        # Sound is the source of truth (0.26, fix 1): reconcile
        # the settled plan with the sound it will render. A cut
        # word whose sound sits inside a kept piece loses that
        # sound; a kept word whose sound fell outside its piece
        # gets the piece extended over it; no edge stays inside
        # a word's sound. The measured bounds ride into the
        # written plan, so the word list everywhere (score,
        # transcript, counts) is read from the sound from here
        # on, never the stamps. The truth pass can split a piece
        # where it takes a cut word's sound out, so the sentence
        # guard and the span trim run once more after it, in the
        # same guard, trim, guard order the stages above use,
        # to settle the new starts the way they settle any other.
        # Words any pass deliberately cut by operation (0.27,
        # fix 7): the sound truth bridge must never revive one.
        # The settled retake cuts are in applied_cut_words; gold
        # records marked auto_cut are settlements too (the
        # detected-repeat settlement stores its pair as held in
        # the retake record, so the record alone misses them);
        # the session audit carries every other pass's applied
        # word cuts.
        deliberate_words = set(applied_cut_words)
        for gold_record in _load_gold(config):
            if gold_record.get("status") != "auto_cut":
                continue
            g_start = gold_record.get("word_start")
            g_end = gold_record.get("word_end")
            if isinstance(g_start, int) and isinstance(g_end, int):
                deliberate_words.update(range(g_start, g_end))
        audit_path = config.session_dir / "operations_audit.json"
        if audit_path.is_file():
            try:
                audit_doc = json.loads(
                    audit_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                audit_doc = {}
            for operation in audit_doc.get("operations", []):
                if operation.get("status") not in (None, "applied"):
                    continue
                if operation.get("type") != "cut_words":
                    continue
                o_start = operation.get("word_start")
                o_end = operation.get("word_end")
                if isinstance(o_start, int) and isinstance(o_end, int):
                    deliberate_words.update(range(o_start, o_end))
        new_ranges, truth = smooth.sound_truth_pass(
            words, new_ranges, env,
            segments=segments, protected_words=protected_words,
            blocked_words=deliberate_words,
            deliberate_words=deliberate_words, fps=fps, spans=spans,
        )
        new_ranges, guard_report3 = smooth.enforce_sentence_starts(
            words, new_ranges, env,
            segments=segments, protected_words=protected_words,
            blocked_words=blocked_words,
            take_start_words=take_start_words, fps=fps, spans=spans,
        )
        new_ranges, internal4 = smooth.trim_internal_silences(
            words, new_ranges, env,
            segments=segments, protected_words=protected_words, fps=fps,
            spans=spans,
        )
        internal = {
            "trimmed": internal["trimmed"] + internal4["trimmed"],
            "seconds_removed": round(
                internal["seconds_removed"]
                + internal4["seconds_removed"],
                4,
            ),
            "details": internal["details"] + internal4["details"],
        }
        new_ranges, guard_report4 = smooth.enforce_sentence_starts(
            words, new_ranges, env,
            segments=segments, protected_words=protected_words,
            blocked_words=blocked_words,
            take_start_words=take_start_words, fps=fps, spans=spans,
        )
        guard_report = {
            "fixed": guard_report["fixed"] + guard_report3["fixed"]
            + guard_report4["fixed"],
            "left": guard_report4["left"],
            "details": guard_report["details"] + guard_report3["details"]
            + guard_report4["details"],
        }
        # The final join check (0.25, finding 6): the guard and
        # the last span trim moved edges after the first check
        # ran, so its spots can be stale in both directions (a
        # join it flagged has since been repaired; a join it
        # passed may have been split by the trim). The review
        # spots are read from THIS check, run on the finished
        # plan: a join is offered for review only when it still
        # fails a measured check here. The smooth plan's own
        # judgment flags (a dangling start it left alone) are
        # not join failures and are no longer review spots.
        new_ranges = smooth.drop_soundless_pieces(
            words, new_ranges, sound_bounds, kept_mids=kept_sound_mids,
        )
        new_ranges, final_check = smooth.join_check(
            words, new_ranges, env, segments=segments, fps=fps,
            spans=spans, take_start_words=take_start_words,
            measure_only=True,
        )
        changed = new_ranges != ranges_before
        bounds_changed = cuts.get("word_sound_bounds") != truth["bounds"]
        if changed or bounds_changed:
            new_cuts = copy.deepcopy(cuts)
            new_cuts["kept_ranges"] = new_ranges
            if truth["bounds"]:
                new_cuts["word_sound_bounds"] = truth["bounds"]
            config.session_dir.mkdir(parents=True, exist_ok=True)
            _write_json(config.session_dir / "revised_cuts.json", new_cuts)
        # Output times for the spots that need the creator, so the
        # chat can offer chips that jump to the exact moment.
        after = Timeline(transcript, {"kept_ranges": new_ranges,
                                      "source_duration_s": timeline.source_duration})
        spots = []
        for entry in final_check["your_call"]:
            join_index = entry["join_index"]
            if 0 <= join_index < len(after.segments):
                out_t = after.segments[join_index]["output_end"]
            else:
                out_t = entry.get("time_s", 0.0)
            spots.append({
                "join_index": join_index,
                "kind": entry["kind"] if "kind" in entry else ",".join(entry["kinds"]),
                "output_time_s": round(float(out_t), 3),
                "source_time_s": entry.get("time_s"),
                "text": entry.get("text", ""),
            })
        summary = {
            "ran": True,
            "internal_silences_trimmed": internal["trimmed"],
            "internal_silence_seconds_removed": internal["seconds_removed"],
            "edges_set": report["edges_set"],
            "sentence_starts_fixed": guard_report["fixed"],
            "sentence_starts_left": guard_report["left"],
            "breaths_cut_whole": report["breaths_cut_whole"],
            "breaths_kept_whole": report["breaths_kept_whole"],
            "joins_checked": final_check["joins_checked"],
            "joins_smooth": final_check["joins_smooth"],
            "issues_fixed": check["fixed"] + final_check["fixed"],
            "sound_truth": {
                "edges_moved": truth["edges_moved"],
                "cut_sounds_removed": truth["cut_sounds_removed"],
                "kept_sounds_restored": truth["kept_sounds_restored"],
                "orphan_sounds_bridged": truth["orphan_sounds_bridged"],
                "slivers_dropped": truth["slivers_dropped"],
                "status_changes": truth["status_changes"],
                "unresolved": truth["unresolved"],
            },
            "your_call": spots,
            "pauses": final_check["pauses"],
            "changed": changed,
        }
        config.session_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            config.session_dir / "smooth_pass.json",
            {"at": _utc_now(), **summary},
        )
    except Exception as exc:  # The plan stays as it was; say so in the log.
        _log_raw_error("smooth pass", exc)
        summary = {"ran": False, "error": str(exc)}
    return summary


def _run_opening_pass(config: WebAppConfig) -> dict:
    """The opening rule (0.24.0): the edit starts on the first
    real line. Setup talk and abandoned first takes before it are
    cut; the one breath right before it stays. Runs after the
    retake pass and before the silence and smooth passes, on the
    session's current plan, in both edit paths.
    """
    summary = {"ran": False}
    try:
        transcript = _load_json(str(config.job_dir / "transcript.json"))
        words = transcript.get("words") or []
        if not words:
            return summary
        cuts = _effective_cuts(config)
        ranges = cuts.get("kept_ranges") or []
        if not ranges:
            return summary
        wav_path = config.job_dir / FIRST_EDIT_WORK_NAME / "audio.wav"
        if not wav_path.is_file():
            source_name = intake_build.intake_source_video(config.job_dir)
            if not source_name:
                return summary
            config.session_dir.mkdir(parents=True, exist_ok=True)
            wav_path = config.session_dir / ".smooth-audio.wav"
            if not wav_path.is_file():
                speech.extract_audio(config.job_dir / source_name, wav_path)
        env = smooth.envelope_db(wav_path)
        if not env:
            return summary
        new_ranges, report = opening.opening_plan(
            words, ranges, env, segments=transcript.get("segments"),
        )
        if report["fired"] and new_ranges != ranges:
            new_cuts = copy.deepcopy(cuts)
            new_cuts["kept_ranges"] = new_ranges
            config.session_dir.mkdir(parents=True, exist_ok=True)
            _write_json(config.session_dir / "revised_cuts.json", new_cuts)
        summary = {"ran": True, **report}
        config.session_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            config.session_dir / "opening_pass.json",
            {"at": _utc_now(), **summary},
        )
    except Exception as exc:  # The plan stays as it was; say so in the log.
        _log_raw_error("opening pass", exc)
        summary = {"ran": False, "error": str(exc)}
    return summary


def _merge_page_operations(config: WebAppConfig, audit: dict, tmp: Path) -> list:
    """Merge one pass's verified result into the session.

    The pass ran ``_verify_and_apply`` into its own temp folder;
    this is the step every pass shares afterwards: the temp revised
    plan becomes the session's current plan, and the pass's page
    operations are appended to the session audit (or the pass's own
    audit becomes the session audit when there is none yet), so the
    transcript dots and the review page explain every pass's changes
    in one place. Returns the pass's page operations.
    """
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    revised_path = session / "revised_cuts.json"
    shutil.copyfile(Path(tmp) / "revised_cuts.json", revised_path)
    page_operations = audit.get("operations", [])
    session_audit_path = session / "operations_audit.json"
    if session_audit_path.is_file():
        existing = _load_json(str(session_audit_path))
        existing.setdefault("operations", []).extend(page_operations)
        existing["after"] = audit["after"]
        _write_json(session_audit_path, existing)
    else:
        shutil.copyfile(Path(tmp) / "operations_audit.json", session_audit_path)
    return page_operations


# ---------------------------------------------------------------------------
# The filler pass and the direction pass (v0.7.0)
#
# Two more passes over the same ground the retake and silence
# passes already walk. The filler pass is deterministic: it measures
# the transcript for pure filler words (the captions module's
# conservative list) that are still in the edit and outside every
# protected span, and cuts them through the same verify-and-apply
# path. The direction pass hands whatever direction the deterministic
# controls could not map to the model, as a read of the whole
# transcript: the model proposes ordinary operations (applied the
# same checked way) plus flags, observations about the talk itself
# that no operation can carry. Flags are filtered here, against the
# post-pass plan, so a flag can only point at words that survived,
# outside protected spans and out of the opening hook.
# ---------------------------------------------------------------------------

FILLER_PASS_FILENAME = "filler_pass.json"
DIRECTION_PASS_FILENAME = "direction_pass.json"
FLAGS_FILENAME = "flags.json"
FILLER_COMMENT_ID = "filler-pass"
DIRECTION_COMMENT_ID = "direction-pass"


def _filler_operations(
    timeline: Timeline, settings: dict, profile: Optional[dict] = None
) -> list:
    """Cut operations for the kept filler words, one per run.

    A word earns a cut when three things are all true: the current
    plan keeps it, the captions module's conservative filler check
    names it a pure filler (um, uh, and their stretched spellings,
    never a meaning-bearing word), and it sits outside every
    protected span. Adjacent filler words are proposed as one
    operation per run, so "um uh" is one cut, not two. The
    operations carry the pass's comment id, the deterministic-local
    model marker the schema expects, and a plain reason.
    """
    if not settings.get("remove_fillers", True):
        return []
    protected: set = set()
    for span in timeline.protected_spans(profile):
        protected.update(range(span["word_start"], span["word_end"]))
    fillers = [
        index
        for index, word in enumerate(timeline.words)
        if timeline.word_status(index) == "kept"
        and index not in protected
        and clean_captions.is_filler(str(word.get("word") or ""))
    ]
    operations = []
    for index in fillers:
        if operations and operations[-1]["word_end"] == index:
            operations[-1]["word_end"] = index + 1
            continue
        operations.append(
            {
                "type": "cut_words",
                "comment_id": FILLER_COMMENT_ID,
                "word_start": index,
                "word_end": index + 1,
                "reason": "Filler word, taken out by the ums and uhs pass.",
                "confidence": 1.0,
                "model": "mock",
                "risk_tier": 1,
                "wonder_reviewed": False,
            }
        )
    return operations


def _run_filler_pass(config: WebAppConfig, settings: dict) -> dict:
    """Take the kept filler words out of the session's current plan.

    Mirrors the silence pass: build the timeline from the session
    transcript and the current plan, propose the cuts, run them
    through verify-and-apply in a temp folder, merge the result
    into the session, and record the pass in its own session file.
    ``applied`` counts words removed, not operations, because one
    operation can carry a run of fillers. When the choice is off,
    or there is nothing to take out, the pass does not run.
    """
    session_log.log_stage(config.session_dir, "filler", "start")
    summary = {"ran": False, "applied": 0, "lines": []}
    if not settings.get("remove_fillers"):
        session_log.log_stage(config.session_dir, "filler", "finish")
        return summary
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    timeline = Timeline(transcript, _effective_cuts(config))
    profile = _load_optional_file(config.job_dir / "profile.json")
    operations = _filler_operations(timeline, settings, profile)
    if not operations:
        session_log.log_stage(config.session_dir, "filler", "finish")
        return summary
    summary["ran"] = True
    feedback = {
        "comments": [
            {
                "id": FILLER_COMMENT_ID,
                "timestamp": None,
                "text": "Take out the ums and uhs",
                "scope": "both",
            }
        ],
        "overall": [],
    }
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    revised_path = session / "revised_cuts.json"
    base_cuts_path = (
        revised_path if revised_path.is_file() else config.job_dir / "cuts.json"
    )
    profile_path = config.job_dir / "profile.json"
    tmp = session / ".filler-tmp"
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
        _log_raw_error("filler pass", exc)
        shutil.rmtree(tmp, ignore_errors=True)
        session_log.log_stage(
            config.session_dir, "filler", "fail",
            "the pass could not run; the plan was left as it was",
        )
        return summary
    page_operations = _merge_page_operations(config, audit, tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    applied = sum(
        int(op.get("word_end") or 0) - int(op.get("word_start") or 0)
        for op in page_operations
        if op.get("status") == "applied" and op.get("type") == "cut_words"
    )
    summary["applied"] = applied
    if applied > 0:
        summary["lines"] = [
            f"Took out {applied} filler word"
            f"{'s' if applied != 1 else ''} (ums and uhs)."
        ]
    _write_json(
        session / FILLER_PASS_FILENAME,
        {"applied": applied, "feedback": feedback},
    )
    session_log.log_stage(config.session_dir, "filler", "finish")
    return summary


def _direction_provider(config: WebAppConfig, style: Optional[str] = None):
    """The provider a direction pass reads with.

    The same construction every live run uses (see
    ``_live_provider``): the Meta provider keyed by the session
    sign-in, riding the creator's chosen Spark route. Kept as
    one small helper so the pass has a single provider seam.
    The pass's picked style rides along, and nothing else changes.
    """
    return _live_provider(config, style=_picked_style(config, {"style": style}))


def _format_flag_time(time_s: float) -> str:
    """A flag's time as minutes:seconds, seconds zero-padded."""
    total = int(float(time_s))
    return f"{total // 60}:{total % 60:02d}"


_FLAG_RECAPS = {
    "repeated_point": "One thing to see: you make the same point again at {t}.",
    "dropped_thread": (
        "One thing to see: you open a thread at {t} and never come back to it."
    ),
    "buried_line": "A strong line is buried at {t}: '{quote}'",
    "drifted_repeat": (
        "These two lines at {t} say almost the same thing. "
        "Retake, or said twice on purpose?"
    ),
    "wording_fights": (
        "One word fights the sentence around it at {t}: '{quote}'."
    ),
    "halfway_thought": (
        "This thought stops halfway at {t} and nothing finishes it."
    ),
}


def _opening_hook_end(words: list) -> int:
    """The word index just past the opening hook (the first sentence).

    The hook is the words up to and including the first word whose
    token ends a sentence, scanning at most the first 60 words;
    when no sentence ends there, the hook is the first 10 words.
    """
    for index in range(min(60, len(words))):
        token = str(words[index].get("word") or "").rstrip()
        if token.endswith((".", "?", "!")):
            return index + 1
    return min(10, len(words))


def _spans_overlap(start: int, end: int, other_start: int, other_end: int) -> bool:
    return start < other_end and other_start < end


def _filter_direction_flags(
    raw_flags: list, words: list, protected: list, post_timeline: Timeline
) -> list:
    """The flags that may be shown, filtered and formatted.

    A flag survives only when every span it names is outside the
    protected spans and still kept in the post-pass plan (a flag
    must point at words the listener will actually hear), and, for
    the two kinds that lose their meaning at the very start, when
    its span does not start inside the opening hook. Survivors are
    sorted by where they start, and any flag overlapping one
    already kept is dropped, so the creator sees each spot once.
    """
    hook_end = _opening_hook_end(words)

    def span_survives(start, end) -> bool:
        if not isinstance(start, int) or isinstance(start, bool):
            return False
        if not isinstance(end, int) or isinstance(end, bool):
            return False
        if not 0 <= start < end <= len(words):
            return False
        for span in protected:
            if _spans_overlap(start, end, span["word_start"], span["word_end"]):
                return False
        for index in range(start, end):
            if post_timeline.word_status(index) == "cut":
                return False
        return True

    candidates = []
    for flag in raw_flags:
        if not isinstance(flag, dict):
            continue
        kind = flag.get("type")
        if kind not in _FLAG_RECAPS:
            continue
        start, end = flag.get("word_start"), flag.get("word_end")
        if not span_survives(start, end):
            continue
        start2, end2 = flag.get("word_start2"), flag.get("word_end2")
        if kind == "drifted_repeat":
            if not span_survives(start2, end2):
                continue
        else:
            start2, end2 = None, None
        if kind in ("repeated_point", "buried_line") and start < hook_end:
            continue
        candidates.append(
            {
                "type": kind,
                "word_start": start,
                "word_end": end,
                "word_start2": start2,
                "word_end2": end2,
                "reason": str(flag.get("reason") or ""),
            }
        )
    candidates.sort(key=lambda item: item["word_start"])
    kept: list = []
    kept_spans: list = []
    for candidate in candidates:
        spans = [(candidate["word_start"], candidate["word_end"])]
        if candidate["word_start2"] is not None:
            spans.append((candidate["word_start2"], candidate["word_end2"]))
        if any(
            _spans_overlap(start, end, other_start, other_end)
            for start, end in spans
            for other_start, other_end in kept_spans
        ):
            continue
        kept.append(candidate)
        kept_spans.extend(spans)
    flags = []
    for candidate in kept:
        start = candidate["word_start"]
        time_s = float(words[start]["start"])
        if candidate["type"] == "wording_fights":
            quote = str(words[start].get("word") or "")
        else:
            quote = " ".join(
                str(words[i].get("word") or "")
                for i in range(start, candidate["word_end"])
            )
        recap = _FLAG_RECAPS[candidate["type"]].format(
            t=_format_flag_time(time_s), quote=quote
        )
        flags.append(
            {
                "type": candidate["type"],
                "word_start": start,
                "word_end": candidate["word_end"],
                "word_start2": candidate["word_start2"],
                "word_end2": candidate["word_end2"],
                "time_s": time_s,
                "quote": quote,
                "reason": candidate["reason"],
                "recap": recap,
            }
        )
    return flags


def _run_direction_pass(
    config: WebAppConfig, remainder: str, settings: dict
) -> dict:
    """Read the leftover direction against the whole transcript.

    The deterministic controls have already taken what they could
    map; what is left is a reading task, so it goes to the model
    with the cleaned state of the edit (the plan after the retake,
    filler, and silence passes) plus the session's overall notes
    and saved rules. The model's operations are applied through the
    same verify-and-apply path as every other pass, and its flags
    are filtered against the post-pass plan before they are saved
    or shown. With no Meta sign-in there is nothing to read with,
    and the pass says so in one line instead of failing.
    """
    summary = {
        "ran": False,
        "reason": "",
        "applied": 0,
        "flagged_ops": 0,
        "flags": [],
        "lines": [],
        "usage": [],
        "route_events": [],
        "mode": settings.get("mode", "cleanest"),
        "negation_applied": 0,
        "auto_struck": [],
        "auto_struck_count": 0,
        "gold": 0,
        "skills": skills.builtin_versions(_picked_style(config, settings)),
    }
    session_log.log_stage(config.session_dir, "direction", "start")
    if not config.signin_key():
        summary["reason"] = "no_key"
        summary["lines"] = [
            "Your direction will be read when a key is saved."
        ]
        session_log.log_stage(
            config.session_dir, "direction", "finish", "no key saved"
        )
        return summary
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    words = transcript.get("words", [])
    timeline = Timeline(transcript, _effective_cuts(config))
    profile = _load_optional_file(config.job_dir / "profile.json")
    feedback_path = _feedback_path(config)
    overall_texts: list = []
    if feedback_path is not None:
        try:
            payload = _load_json(str(feedback_path))
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict):
            overall_texts = [
                note["text"] for note in _overall_notes(payload)
            ]
    kept = timeline.kept_word_indices()
    packet = {
        "direction": remainder,
        "overall": overall_texts,
        "saved_rules": creator_rules.active_rule_statements(
            _rules_store(config)
        ),
        "plan_state": {
            "kept_words": len(kept),
            "cut_words": len(words) - len(kept),
        },
    }
    provider = _direction_provider(config, style=settings.get("style"))
    summary["skills"] = _provider_skill_versions(
        provider, _picked_style(config, settings)
    )
    operations, _routing, raw_flags = provider.direct_transcript(
        packet, timeline, profile
    )
    # The provider's operations carry a routing-only
    # ``target_comment_id`` beside the schema's ``comment_id``; the
    # apply path validates strictly and refuses unknown fields, so
    # the routing key comes off here and only the schema shape goes
    # forward. The feedback comment below keeps the same id the
    # operations carry, "direction-pass".
    operations = [
        {
            key: value
            for key, value in op.items()
            if key != "target_comment_id"
        }
        if isinstance(op, dict)
        else op
        for op in operations
    ]
    summary["ran"] = True
    feedback = {
        "comments": [
            {
                "id": DIRECTION_COMMENT_ID,
                "timestamp": None,
                "text": remainder,
                "scope": "both",
            }
        ],
        "overall": [],
    }
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
        tmp = session / ".direction-tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        args = SimpleNamespace(
            transcript=str(config.job_dir / "transcript.json"),
            cuts=str(base_cuts_path),
            profile=str(profile_path) if profile_path.is_file() else None,
        )
        flipped: list = []
        negation_adjust = _negation_policy(settings, remainder, flipped)
        cut_guard = _provider_cut_guard(
            config, named_tokens=_named_content_tokens(remainder)
        )
        try:
            audit = _verify_and_apply(
                args,
                {"operations": operations},
                tmp,
                adjust_verdicts=lambda verdicts, timeline, profile: (
                    cut_guard(
                        negation_adjust(verdicts, timeline, profile),
                        timeline,
                        profile,
                    )
                ),
            )
        except (SchemaError, ApplyError) as exc:
            # The batch as a whole did not pass the safety check, so
            # nothing in it is applied; every proposal counts as set
            # aside rather than silently vanishing.
            _log_raw_error("direction pass", exc)
            shutil.rmtree(tmp, ignore_errors=True)
            audit = None
            summary["flagged_ops"] = len(operations)
        if audit is not None:
            page_operations = _merge_page_operations(config, audit, tmp)
            shutil.rmtree(tmp, ignore_errors=True)
            if flipped:
                # The negation policy's settlements are marked in
                # the session audit, so the record says why a
                # held-looking cut applied: the words were a
                # repeat, or the direction named them. (The merge
                # has already written that file, from the pass's own
                # audit or by extending the session's, so the marks
                # land on the file itself.)
                session_audit_path = (
                    config.session_dir / "operations_audit.json"
                )
                session_audit = _load_json(str(session_audit_path))
                for flip in flipped:
                    for entry in session_audit.get("operations", []):
                        if (
                            entry.get("type") == "cut_words"
                            and entry.get("word_start")
                            == flip["word_start"]
                            and entry.get("word_end") == flip["word_end"]
                            and entry.get("status") == "applied"
                        ):
                            entry["negation_policy"] = flip["basis"]
                _write_json(session_audit_path, session_audit)
    summary["applied"] = sum(
        1 for op in page_operations if op.get("status") == "applied"
    )
    if page_operations:
        # Set aside means the proposal came back as, or was turned
        # into, a flag for review, plus anything the verifier
        # rejected outright. (When the apply failed as a whole,
        # flagged_ops was already set to the full proposal count.)
        summary["flagged_ops"] = sum(
            1
            for op in page_operations
            if op.get("status") in ("flagged", "rejected")
        )
        # What the pass could not settle waits in gold, recorded
        # from the same audit the page operations came from. A
        # candidate that is a detected repeat (its narrowed span
        # duplicates words that stay) settles as an applied cut
        # at this same record point instead, in every mode
        # (v0.17, feedback items 7 and 8).
        summary["negation_applied"] = len(flipped)
        protected_ranges = [
            (span["word_start"], span["word_end"])
            for span in timeline.protected_spans(profile)
        ]
        summary["auto_struck"] = _record_gold_detected_repeats(
            config,
            _gold_candidates_from_direction(
                page_operations,
                audit.get("verdicts") or [],
                words,
                protected_ranges=protected_ranges,
            ),
            settings.get("mode", "cleanest"),
            protected_ranges=protected_ranges,
        )
        summary["auto_struck_count"] = len(summary["auto_struck"])
        summary["gold"] = len(_open_gold_spans(config))
    drain = getattr(provider, "drain_usage", None)
    if callable(drain):
        summary["usage"] = drain() or []
    drain_events = getattr(provider, "drain_route_events", None)
    if callable(drain_events):
        summary["route_events"] = drain_events() or []
    post_timeline = Timeline(transcript, _effective_cuts(config))
    flags = _filter_direction_flags(
        raw_flags or [],
        words,
        timeline.protected_spans(profile),
        post_timeline,
    )
    summary["flags"] = flags
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    _write_json(session / FLAGS_FILENAME, {"flags": flags})
    _write_json(
        session / DIRECTION_PASS_FILENAME,
        {
            "ran": True,
            "applied": summary["applied"],
            "flagged_ops": summary["flagged_ops"],
            "flags": flags,
            "usage": summary["usage"],
            "feedback": feedback,
            "mode": summary["mode"],
            "negation_applied": summary["negation_applied"],
            "auto_struck": summary["auto_struck"],
            "auto_struck_count": summary["auto_struck_count"],
            "gold": summary["gold"],
            "skills": summary["skills"],
        },
    )
    lines: list = []
    if summary["applied"] > 0:
        lines.append(
            f"Read your direction and made {summary['applied']} "
            f"change{'s' if summary['applied'] != 1 else ''}."
        )
    if summary["auto_struck_count"] > 0:
        struck = summary["auto_struck_count"]
        lines.append(
            f"{struck} detected repeat"
            f"{'s' if struck != 1 else ''} "
            f"{'were' if struck != 1 else 'was'} struck "
            "automatically. You can bring any of them back."
        )
    if summary["flagged_ops"] > 0:
        lines.append(
            f"{summary['flagged_ops']} suggestion"
            f"{'s' if summary['flagged_ops'] != 1 else ''} set aside "
            "for your review."
        )
    for event in summary.get("route_events") or []:
        # A hosted route that could not serve the read fell back
        # to Meta; the recap says which route actually ran.
        lines.append(
            f"{ROUTE_LABELS.get(event.get('requested'), 'The hosted route')} "
            "could not be reached, so this read ran on Meta."
        )
    lines.extend(flag["recap"] for flag in flags)
    if not lines:
        lines = ["Read your direction. Nothing to change."]
    lines.extend(_skills_recap_lines(summary["skills"]))
    summary["lines"] = lines
    # A finished direction run is one of the two moments a saved
    # teaching rule can turn tested (the other is a grade landing).
    _refresh_teaching_tested(config)
    session_log.log_stage(config.session_dir, "direction", "finish")
    return summary


# ---------------------------------------------------------------------------
# Gold opportunities and the negation policy (v0.7.1)
#
# The creator chooses how involved to be (the mode, one of the
# front door choices). Spots a pass was not sure about are no
# longer just set aside: they wait in gold. A gold span is a run of
# words still in the edit, washed gold in the transcript, carrying
# why the editor is unsure. The creator judges it (cut it, or
# leave it), can attach a note with the context, and the verdict is
# recorded as a grade, so the verdicts teach the same rule learning
# the review grades teach, note and all.
#
# The mode also settles the standing negation question as product
# policy. A negation-bearing cut is never applied on a model's say
# alone; but a directed cut that Muse Wonder has reviewed applies
# when the words it removes are a repeat of words that stay (a cut
# of a copy cannot change what the kept copy says), or when the
# creator's own direction named that span. v0.17 (feedback items
# 7 and 8): the repeat settlement runs in Tailor it mode too, on
# Justin's direct instruction that detected retakes are cut
# proactively in every way of working; a negation pair whose
# copies say different things still waits in gold, because there
# the choice of copy is the meaning. Every other held negation
# cut waits in gold for the creator's call. When in
# doubt, gold: the duplicate and naming checks are exact on
# purpose, and a miss only means the creator sees the spot.
# ---------------------------------------------------------------------------

# The verdict holds a gold verdict may settle: the ones whose whole
# point is "a person should look at this". Every other hold
# (protected material, a special insert, the 25 percent rule, a
# structural problem) still refuses, gold or not.
GOLD_SOFT_HOLDS = {"negation_bearing_span", "wonder_review_required"}

# Held-span reasons (v0.9.0): one plain sentence each, naming the
# repeated words and the one call the creator has to make, so the
# sentence can be acted on without being parsed first.
_GOLD_REASON_NEGATION = (
    "The editor recommends cutting this repeated part, but it "
    "includes a negative statement, so check the meaning before "
    "you decide."
)
_GOLD_REASON_HELD = (
    "The editor recommends removing these words, but the change "
    "is a big one, so you decide whether they go."
)
_GOLD_REASON_RETAKE_HELD = (
    "This earlier take is said again right after, so the editor "
    "recommends cutting it and leaves the call to you."
)
# v0.17 (feedback item 8): the one retake case that still asks.
# The copies say different things, so which copy survives can
# flip the meaning; the card asks, and states the default first
# (Justin's settled rule: the copy that includes more stays).
_GOLD_REASON_RETAKE_DIFFER = (
    "This line is said again right after, and the copies say "
    "different things (one copy includes a 'not'), so this one "
    "is your call. The default is the copy that includes more: "
    "it stays. Cut this try, or leave both copies in."
)
# 0.25, finding 7: the other copies-differ case the detector
# can prove locally. The copies name different numbers, so
# which one stays changes what the video says.
_GOLD_REASON_RETAKE_NUMBERS = (
    "This line is said again right after, and the copies name "
    "different numbers, so this one is your call. The default "
    "is the last copy: it stays. Cut this try, or leave both "
    "copies in."
)

_GOLD_PATTERN_DESCRIPTIONS = {
    ("negation", "cut"): (
        "Cutting a passage the editor held back because it "
        "includes a negative statement, once you have seen it"
    ),
    ("negation", "leave"): (
        "Keeping a passage the editor held back because it "
        "includes a negative statement, once you have seen it"
    ),
    ("retake", "cut"): (
        "Taking out the earlier take of a repeated line the "
        "editor was not sure about, once you have seen both takes"
    ),
    ("retake", "leave"): (
        "Keeping both takes of a repeated line the editor was "
        "not sure about, once you have seen them"
    ),
    ("same_point", "cut"): (
        "Taking out a passage whose point is made again later, "
        "once you have seen both sayings"
    ),
    ("same_point", "leave"): (
        "Keeping a passage whose point is made again later, as "
        "a restatement you want in the video"
    ),
    ("held", "cut"): (
        "Making a change the editor held back for your decision, "
        "once you have seen it"
    ),
    ("held", "leave"): (
        "Leaving a change the editor held back for your decision "
        "unmade, once you have seen it"
    ),
}


_GOLD_RULE_STATEMENTS = {
    ("negation", "cut"): (
        "Cut a passage with a negative statement once you have "
        "seen it and said to cut it."
    ),
    ("negation", "leave"): (
        "Keep a passage with a negative statement once you have "
        "seen it and said to leave it."
    ),
    ("retake", "cut"): (
        "Take out the earlier take of a repeated line once you "
        "have seen both takes."
    ),
    ("retake", "leave"): (
        "Keep both takes of a repeated line once you have seen "
        "them."
    ),
    ("same_point", "cut"): (
        "Take out a passage whose point is made again later."
    ),
    ("same_point", "leave"): (
        "Keep a passage whose point is made again later, as a "
        "restatement you want in the video."
    ),
    ("held", "cut"): (
        "Make a change the editor held back for your decision, "
        "once you have seen it."
    ),
    ("held", "leave"): (
        "Leave a change the editor held back for your decision "
        "unmade, once you have seen it."
    ),
}


def _note_rule_statement(note: str) -> Optional[str]:
    """A verdict's own words as a rule statement, or None.

    His note, cleaned to one sentence: one line, first letter
    up, a period at the end when it has no closing punctuation.
    """
    text = " ".join(str(note or "").split()).strip()
    if not text:
        return None
    text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text


def _gold_id(word_start: int, word_end: int, kind: str) -> str:
    return f"gold-{word_start}-{word_end}-{kind}"


def _load_gold(config: WebAppConfig) -> list:
    payload = _load_session_json(config, GOLD_FILENAME, {"spans": []})
    if isinstance(payload, dict) and isinstance(payload.get("spans"), list):
        return [s for s in payload["spans"] if isinstance(s, dict)]
    return []


def _save_gold(config: WebAppConfig, records: list) -> None:
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(config.session_dir / GOLD_FILENAME, {"spans": records})


def _record_gold_spans(config: WebAppConfig, candidates: list) -> None:
    """Add newly spotted gold spans to the session's store.

    A span is keyed by its words and its kind. A span already in
    the store keeps its record, above all its settled state: a
    verdict the creator already gave is never undone by a later
    pass spotting the same words again.
    """
    if not candidates:
        return
    records = _load_gold(config)
    known = {record.get("gold_id") for record in records}
    changed = False
    fresh = []
    for candidate in candidates:
        if candidate.get("gold_id") in known:
            continue
        records.append(candidate)
        known.add(candidate.get("gold_id"))
        fresh.append(candidate)
        changed = True
    if fresh:
        # A rule he saved from one of his own verdicts settles a
        # new spot of the same kind on sight (source (c)): the
        # decision he already made is the decision, so the spot
        # never waits for a call he has already given.
        _settle_gold_by_decision_rules(config, fresh)
    if changed:
        records.sort(
            key=lambda r: (r.get("word_start", 0), r.get("word_end", 0))
        )
        _save_gold(config, records)


def _decision_rules_by_kind(config: WebAppConfig) -> dict:
    """Saved decision rules by gold kind: {kind: "cut"|"leave"}.

    A decision rule is a saved rule whose pattern key a gold
    verdict wrote (``gold:{kind}:{decision}``): it exists only
    because he tapped Save on the proposal that verdict made.
    """
    by_kind = {}
    for rule in _rules_store(config).get("rules", []):
        if not isinstance(rule, dict) or not rule.get("active", True):
            continue
        parts = str(rule.get("pattern_key") or "").split(":")
        if (
            len(parts) == 3
            and parts[0] == "gold"
            and parts[2] in ("cut", "leave")
        ):
            by_kind[parts[1]] = parts[2]
    return by_kind


def _settle_gold_by_decision_rules(
    config: WebAppConfig, fresh: list
) -> None:
    """Settle newly recorded spots a saved decision rule covers.

    A keep rule settles the spot as left; a cut rule runs the
    same detected-repeat cut a verdict runs, and a cut the
    safety rules refuse leaves the spot open, waiting for him
    as it always has. A performed parallel repeat still waits:
    it is emphasis said on purpose, and no saved verdict on
    another video's spot turns it into a cut here.
    """
    by_kind = _decision_rules_by_kind(config)
    if not by_kind:
        return
    for record in fresh:
        if record.get("status") != "open":
            continue
        kind = str(record.get("kind") or "held")
        move = by_kind.get(kind)
        if not move:
            continue
        if record.get("reason") == retakes._FLAG_PARALLEL_REASON:
            continue
        if move == "leave":
            record["status"] = "left"
        else:
            if not _apply_detected_repeat_cut(config, record):
                continue
            record["status"] = "cut"
        record["settled_by_rule"] = True
        record["decision_rule"] = f"gold:{kind}:{move}"
        record["decided_at"] = _utc_now()


# ---------------------------------------------------------------------------
# The same point, said twice: a review spot, never an auto-cut
# (0.27, Justin's length call)
#
# A passage whose point is made again later is an editorial
# call, not a retake: it may be a deliberate restatement (the
# closing prayer restates the video's point on purpose), so the
# app never cuts it on its own. What it does is offer ONE spot
# in the review walk, next to the your-call spans: "This makes
# the same point as M:SS. Cut it?", with the time where the
# point is made again in the edit. Approve runs the same cut a
# gold verdict runs; Reject settles the spot and it is not
# offered again.
#
# The detector stays tight. A candidate is a kept passage of 15
# to 40 words whose wording substantially repeats in a later
# kept passage (the shorter side's words largely contained in
# the longer, most of its clauses carried over, and at least
# four of its content words said again), the two passages at
# least 10 words apart so an adjacent retake pair (which
# settles as a retake) never lands here. Neither passage may
# touch a protected span, an open your-call span, or a special
# insert; the earlier passage may not be part of the video's
# opening; and neither passage may be one side of a recorded
# retake pair whose other side touches the other passage (the
# prayer and the declaration it restates are linked by a pair,
# and stay out). Spots already in the store are never re-found.
# ---------------------------------------------------------------------------

_SPOT_STOPWORDS = frozenset(
    "the a an and or but if of to in on for with that this it is "
    "was are be been am do does did have has had will would can "
    "could should my your his her our their me him us them i you "
    "he she we they so as at by from not no yes just like going "
    "get got go see say said one really know even also now then "
    "there here what when all any about into over after before "
    "up down out off again once more most very too".split()
)


def _record_same_point_spots(config: WebAppConfig) -> list:
    """Spot kept passages whose point is made again later, and
    record each as an open gold spot for the review walk.

    Returns the records added (empty when none qualify). A
    failure inside the detector never fails the edit: the spots
    are an offer, not a step the edit depends on.
    """
    if not config.has_job:
        return []
    try:
        return _same_point_spots(config)
    except Exception as exc:  # the spots never fail the edit
        _log_raw_error("same-point spot pass", exc)
        return []


def _spot_output_time(timeline, words, sound_bounds, word_index: int) -> float:
    """The word's moment in the edit, read from its measured
    sound (the plan's own truth since fix 1): a word's stamp
    can drift outside the piece that holds its sound, and a
    stamp reading then names the wrong moment, or none."""
    target = word_index
    while target < len(words) and timeline.word_status(
        target
    ) not in ("kept", "special"):
        target += 1
    if target >= len(words):
        return round(timeline.output_duration, 3)
    if len(sound_bounds) == len(words):
        mid = (
            float(sound_bounds[target][0])
            + float(sound_bounds[target][1])
        ) / 2.0
    else:
        word = words[target]
        mid = (float(word["start"]) + float(word["end"])) / 2.0
    for segment in timeline.segments:
        rng = segment["range"]
        if float(rng["start"]) - 1e-6 <= mid <= float(
            rng["end"]
        ) + 1e-6:
            return round(
                segment["output_start"] + (mid - float(rng["start"])),
                3,
            )
    return round(timeline.output_duration, 3)


def _same_point_spots(config: WebAppConfig) -> list:
    from bisect import bisect_left
    from collections import Counter

    transcript = _load_json(str(config.job_dir / "transcript.json"))
    words = transcript.get("words") or []
    if not words:
        return []
    profile = _load_optional_file(config.job_dir / "profile.json")
    plan = _effective_cuts(config)
    timeline = Timeline(transcript, plan)
    ends = smooth.sentence_end_indices(words, transcript.get("segments"))
    sound_bounds = plan.get("word_sound_bounds") or []

    # The closing section (0.27.2): the final stretch of the
    # edit, the last fifth of its running time on the settled
    # plan. A passage restated there is the video landing its
    # point on purpose (the closing prayer restates the premise
    # he opened with), so a spot whose later saying starts
    # inside the closing section is never recorded.
    closing_start_s = 0.8 * float(timeline.output_duration or 0.0)

    def output_time(word_index: int) -> float:
        return _spot_output_time(timeline, words, sound_bounds, word_index)

    def tokens(start: int, end: int) -> list:
        return [
            retakes.normalize_word(words[i].get("word", ""))
            for i in range(start, end)
        ]

    # Kept speech, clause by clause: a kept run breaks where
    # the transcript's sentences end (punctuation or segment
    # boundaries, the app's canonical ends).
    clauses: list = []
    i = 0
    while i < len(words):
        if timeline.word_status(i) != "kept":
            i += 1
            continue
        j = i
        while j < len(words) and timeline.word_status(j) == "kept":
            j += 1
        start = i
        for k in range(i, j):
            if k in ends:
                clauses.append((start, k + 1))
                start = k + 1
        if start < j:
            clauses.append((start, j))
        i = j
    if len(clauses) < 2:
        return []
    clause_tokens = [tokens(s, e) for s, e in clauses]

    def make_windows(min_words: int, max_words: int) -> list:
        found = []
        for ci in range(len(clauses)):
            total = 0
            for cj in range(ci, min(ci + 4, len(clauses))):
                total += clauses[cj][1] - clauses[cj][0]
                if total > max_words:
                    break
                if total >= min_words:
                    found.append(
                        (clauses[ci][0], clauses[cj][1], ci, cj)
                    )
        return found

    def window_tokens(window: tuple) -> list:
        _, _, ci, cj = window
        out: list = []
        for k in range(ci, cj + 1):
            out.extend(clause_tokens[k])
        return out

    def overlaps(a: tuple, b: tuple) -> bool:
        return a[0] < b[1] and b[0] < a[1]

    protected_words: set = set()
    for span in timeline.protected_spans(profile):
        protected_words.update(
            range(int(span["word_start"]), int(span["word_end"]))
        )

    def touches_protected(start: int, end: int) -> bool:
        return any(wi in protected_words for wi in range(start, end))

    gold_records = _load_gold(config)
    open_spans = [
        (r["word_start"], r["word_end"])
        for r in gold_records
        if r.get("status") == "open"
        and isinstance(r.get("word_start"), int)
        and isinstance(r.get("word_end"), int)
    ]
    known_spot_spans = [
        (r["word_start"], r["word_end"])
        for r in gold_records
        if r.get("kind") == "same_point"
        and isinstance(r.get("word_start"), int)
        and isinstance(r.get("word_end"), int)
    ]
    pair_spans = []
    for pair in _load_retake_record(config).get("pairs") or []:
        cut = pair.get("cut") or {}
        keep = pair.get("keep") or {}
        if all(
            isinstance(span.get(key), int)
            for span in (cut, keep)
            for key in ("word_start", "word_end")
        ):
            pair_spans.append(
                (
                    (cut["word_start"], cut["word_end"]),
                    (keep["word_start"], keep["word_end"]),
                )
            )

    # The opening is the video's first real line, which the
    # opening pass already settled: a spot never opens there.
    opening_end = clauses[0][1]
    opening_record = _load_session_json(config, "opening_pass.json", {})
    if isinstance(opening_record, dict):
        first_word = opening_record.get("first_line_word")
        if isinstance(first_word, int):
            later = [e for e in ends if e >= first_word]
            opening_end = (min(later) + 1) if later else first_word + 1

    a_windows = make_windows(15, 40)
    b_windows = make_windows(8, 40)
    b_starts = [w[0] for w in b_windows]
    b_data = [
        (w, window_tokens(w)) for w in b_windows
    ]
    candidates = []
    for a_window in a_windows:
        a_start, a_end = a_window[0], a_window[1]
        if a_start < opening_end:
            continue
        if touches_protected(a_start, a_end):
            continue
        if any(overlaps((a_start, a_end), span) for span in open_spans):
            continue
        if any(
            overlaps((a_start, a_end), span) for span in known_spot_spans
        ):
            continue
        a_tokens = window_tokens(a_window)
        a_content = {
            token
            for token in a_tokens
            if len(token) >= 3 and token not in _SPOT_STOPWORDS
        }
        if len(a_content) < 4:
            continue
        a_clauses = [
            clause_tokens[k]
            for k in range(a_window[2], a_window[3] + 1)
        ]
        first_b = bisect_left(b_starts, a_end + 10)
        for b_window, b_tokens in b_data[first_b:]:
            b_start, b_end = b_window[0], b_window[1]
            if (
                closing_start_s > 0
                and output_time(b_start) >= closing_start_s
            ):
                continue
            if touches_protected(b_start, b_end):
                continue
            if any(
                overlaps((b_start, b_end), span) for span in open_spans
            ):
                continue
            if len(a_content & set(b_tokens)) < 4:
                continue
            b_counter = Counter(b_tokens)
            covered = 0
            for clause in a_clauses:
                if not clause:
                    continue
                shared = sum((Counter(clause) & b_counter).values())
                if shared / len(clause) >= 0.5:
                    covered += 1
            coverage = covered / max(1, len(a_clauses))
            if coverage < 0.6:
                continue
            shorter, longer = (
                (a_tokens, b_tokens)
                if len(a_tokens) <= len(b_tokens)
                else (b_tokens, a_tokens)
            )
            containment = retakes._fuzzy_shared(shorter, longer) / max(
                1, len(shorter)
            )
            if containment < 0.5:
                continue
            crossed = False
            for cut_span, keep_span in pair_spans:
                if (
                    overlaps((a_start, a_end), cut_span)
                    and overlaps((b_start, b_end), keep_span)
                ) or (
                    overlaps((a_start, a_end), keep_span)
                    and overlaps((b_start, b_end), cut_span)
                ):
                    crossed = True
                    break
            if crossed:
                continue
            candidates.append(
                {
                    "word_start": a_start,
                    "word_end": a_end,
                    "again_word_start": b_start,
                    "again_word_end": b_end,
                    "containment": round(containment, 3),
                    "coverage": round(coverage, 3),
                }
            )
    candidates.sort(
        key=lambda c: (-c["containment"], -c["coverage"], c["word_start"])
    )
    accepted = []
    for candidate in candidates:
        span = (candidate["word_start"], candidate["word_end"])
        if any(
            overlaps(span, (a["word_start"], a["word_end"]))
            and min(span[1], a["word_end"]) - max(span[0], a["word_start"])
            > 0.5 * (span[1] - span[0])
            for a in accepted
        ):
            continue
        accepted.append(candidate)
    records = []
    for candidate in accepted:
        a_start, a_end = candidate["word_start"], candidate["word_end"]
        b_start = candidate["again_word_start"]
        b_end = candidate["again_word_end"]
        again_s = output_time(b_start)
        records.append(
            {
                "gold_id": _gold_id(a_start, a_end, "same_point"),
                "kind": "same_point",
                "source": "same_point",
                "word_start": a_start,
                "word_end": a_end,
                "text": " ".join(
                    str(words[i].get("word") or "")
                    for i in range(a_start, a_end)
                ),
                "keep_text": " ".join(
                    str(words[i].get("word") or "")
                    for i in range(b_start, b_end)
                ),
                "keep_word_start": b_start,
                "keep_word_end": b_end,
                "again_word_start": b_start,
                "again_word_end": b_end,
                "again_output_s": again_s,
                "reason": (
                    "This makes the same point as "
                    f"{_format_flag_time(again_s)}. Cut it?"
                ),
                "copies_differ": False,
                "operation": None,
                "pair_id": None,
                "pair_key": None,
                "status": "open",
            }
        )
    _record_gold_spans(config, records)
    return records


# ---------------------------------------------------------------------------
# The final sweep: one last look at the settled plan (0.27.3)
#
# Every pass decides from its own evidence, and a stopped take
# can slip through the seams between them: on Justin's keyed
# 0.27.2 run the prayer try at "offer up to God" survived
# because the one local pair that cuts it owes its decision to
# exact conditions over one transcript segmentation, and the
# keyed path's judge calibration points the same shape at
# keep-both. The sweep runs on the settled plan in every path
# that settles one, just before the render and inside the same
# checkpoint, and asks two questions of the plan itself (the
# shape logic lives in final_sweep.py):
#
# - a kept span that trails off mid-sentence into a second or
#   more of dead space, whose thought is said again complete
#   later, is a stopped try: cut, with its dead space, through
#   the same checked path as any pass; when a hard safety rule
#   holds the cut, the span becomes a your-call spot instead;
# - the video's final saying is never cut by a pass: if the
#   settled plan has it cut, it is restored, and a your-call
#   spot records that it stays unless the creator cuts it.
#
# The run's decisions land in final_sweep.json (counts, undo:
# the file rides the checkpoint like the other pass records),
# and the cuts themselves change the one plan, so the Done
# bubble's counts include them like any pass.
# ---------------------------------------------------------------------------

_FINAL_SWEEP_CUT_REASON = (
    "A stopped try: it trails off before the thought lands, "
    "and the thought is said again in full right after. The "
    "final check took it out, the same call the retake pass "
    "makes; bring the words back if you want this try in."
)
_FINAL_SWEEP_HELD_REASON = (
    "This looks like a stopped try: it trails off, and the "
    "thought is said again in full right after. A safety check "
    "held the cut, so the call is yours."
)
_FINAL_SWEEP_FINAL_REASON = (
    "This is the last line of the video. A pass had taken it "
    "out; the final check put it back, because the last line "
    "only goes when you say so."
)
_FINAL_SWEEP_CHAIN_REASON = (
    "A restart: this saying stops and the same thought is "
    "said again right after, in full. The final check kept "
    "the saying that lands and took the tries before it; "
    "bring the words back if you want a try in."
)
_FINAL_SWEEP_CHAIN_HELD_REASON = (
    "This looks like a restart: the same thought is said "
    "again right after, in full. A safety check held the "
    "cut, so the call is yours."
)
_FINAL_SWEEP_TRAILING_REASON = (
    "This comes after the sign-off and the subscribe lines, "
    "where the video ends. The final check took it out; "
    "bring the words back if you want it in."
)
_FINAL_SWEEP_UNIQUE_REASON = (
    "A pass took this sentence out, and nothing else in the "
    "video says it again. The final check put it back; a "
    "sentence said only once only goes when you say so."
)
_FINAL_SWEEP_UNIQUE_OP_REASON = (
    "This sentence is said once and nothing kept says it "
    "again. A pass had taken it out, so the final check put "
    "it back; cut it yourself if you want it gone."
)


def _apply_final_saying_restore(config: WebAppConfig, start: int, end: int) -> bool:
    """Put a cut final saying back, through the checked path.

    Mirrors _apply_detected_repeat_cut: the same validate,
    verify, and apply every edit runs, with the soft holds
    waived (the restore is this pass's own decision) and every
    hard rule holding exactly as written.
    """
    operation = {
        "type": "restore_words",
        "comment_id": "final-sweep",
        "word_start": start,
        "word_end": end,
        "reason": (
            "The last line of the video stays in. A pass had "
            "taken it out, so the final check put it back; cut "
            "it yourself if you want it gone."
        ),
        "confidence": 1.0,
        "model": "mock",
        "risk_tier": 3,
        "wonder_reviewed": False,
    }
    try:
        transcript = _load_json(str(config.job_dir / "transcript.json"))
        base_cuts = _effective_cuts(config)
        timeline = Timeline(transcript, base_cuts)
        ops = validate_operations([operation])
        profile = _load_optional_file(config.job_dir / "profile.json")
        verdict = verify_operations(ops, timeline, profile)[0]
    except (SchemaError, ApplyError, OSError, ValueError):
        return False
    if verdict.status == "flag" and set(verdict.reasons) <= GOLD_SOFT_HOLDS:
        verdict.status = "apply"
    if verdict.status != "apply":
        return False
    try:
        revised, _audit = apply_verdicts(base_cuts, timeline, [verdict])
    except (SchemaError, ApplyError):
        return False
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    _write_json(session / "revised_cuts.json", revised)
    return True


def _apply_unique_sentence_restore(
    config: WebAppConfig, start: int, end: int
) -> bool:
    """Put a removed once-said sentence back, through the
    checked path. Same shape as _apply_final_saying_restore:
    the soft holds are waived (the restore is this pass's own
    decision), every hard rule holds exactly as written. The
    verifier only accepts a restore over a wholly cut span,
    and a removed sentence is usually spliced (a few words
    kept), so the restore goes run by run over the cut
    stretches inside the sentence."""
    try:
        transcript = _load_json(str(config.job_dir / "transcript.json"))
    except (OSError, ValueError):
        return False
    profile = _load_optional_file(config.job_dir / "profile.json")
    runs = []
    plan = _effective_cuts(config)
    timeline = Timeline(transcript, plan)
    for i in range(start, end):
        if timeline.word_status(i) == "kept":
            continue
        if runs and i == runs[-1][1]:
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1])
    if not runs:
        return False
    for run_start, run_end in runs:
        operation = {
            "type": "restore_words",
            "comment_id": "final-sweep",
            "word_start": run_start,
            "word_end": run_end,
            "reason": _FINAL_SWEEP_UNIQUE_OP_REASON,
            "confidence": 1.0,
            "model": "mock",
            "risk_tier": 3,
            "wonder_reviewed": False,
        }
        try:
            base_cuts = _effective_cuts(config)
            timeline = Timeline(transcript, base_cuts)
            ops = validate_operations([operation])
            verdict = verify_operations(ops, timeline, profile)[0]
        except (SchemaError, ApplyError, OSError, ValueError):
            return False
        if (
            verdict.status == "flag"
            and set(verdict.reasons) <= GOLD_SOFT_HOLDS
        ):
            verdict.status = "apply"
        if verdict.status != "apply":
            return False
        try:
            revised, _audit = apply_verdicts(
                base_cuts, timeline, [verdict]
            )
        except (SchemaError, ApplyError):
            return False
        session = config.session_dir
        session.mkdir(parents=True, exist_ok=True)
        _write_json(session / "revised_cuts.json", revised)
    return True


def _final_sweep_spot(
    words: list, span: dict, reason: str, operation: Optional[dict]
) -> dict:
    """A your-call record in the established gold shape."""
    start, end = span["word_start"], span["word_end"]
    again_start = span.get("completer_word_start")
    again_end = span.get("completer_word_end")
    return {
        "gold_id": _gold_id(start, end, "held"),
        "kind": "held",
        "source": "final_sweep",
        "word_start": start,
        "word_end": end,
        "text": " ".join(
            str(words[i].get("word") or "") for i in range(start, end)
        ),
        "keep_text": (
            " ".join(
                str(words[i].get("word") or "")
                for i in range(again_start, again_end)
            )
            if isinstance(again_start, int) and isinstance(again_end, int)
            else None
        ),
        "keep_word_start": again_start,
        "keep_word_end": again_end,
        "again_word_start": again_start,
        "again_word_end": again_end,
        "reason": reason,
        "copies_differ": False,
        "operation": operation,
        "pair_id": None,
        "pair_key": None,
        "status": "open",
        "note": None,
        "created_at": _utc_now(),
        "decided_at": None,
    }


def _run_final_sweep(config: WebAppConfig) -> dict:
    """The final sweep over the session's settled plan.

    Runs inside the caller's checkpoint (the callers place it
    between checkpoints.begin and commit), so Undo covers its
    cuts and its restore like any pass's. Never raises: a sweep
    failure leaves the settled plan exactly as it was.
    """
    summary = {
        "ran": False,
        "cuts": [],
        "chains": [],
        "trailing_cut": None,
        "silence_ceiling": {
            "ran": False,
            "trimmed": 0,
            "seconds_removed": 0.0,
        },
        "restored_final_saying": False,
        "restored_sentences": [],
        "recording_chains": [],
        "recording_spans": [],
        "lead_in_chains": [],
        "spots": [],
        "left": [],
    }
    if not config.has_job:
        return summary
    try:
        transcript = _load_json(str(config.job_dir / "transcript.json"))
        words = transcript.get("words") or []
        if not words:
            return summary
        profile = _load_optional_file(config.job_dir / "profile.json")
        plan0 = _effective_cuts(config)
        timeline0 = Timeline(transcript, plan0)
        profile_words: set = set()
        for span in timeline0.protected_spans(profile):
            try:
                profile_words.update(
                    range(int(span["word_start"]), int(span["word_end"]))
                )
            except (KeyError, TypeError, ValueError):
                continue

        # The closing block settles where the video ends
        # (Chunk R): the sign-off and subscribe lines complete
        # the template, and kept speech after them is
        # off-script. It goes through the same checked cut as
        # any sweep cut; when it lands, the final saying below
        # is read only up to the block's end, so the trailing
        # speech is never mistaken for the last line.
        fin_cap = None
        statuses0 = [
            timeline0.word_status(i) for i in range(len(words))
        ]
        bounds0 = final_sweep.word_bounds(words, plan0)
        ends0 = final_sweep.punctuation_sentence_ends(words)
        closing_end = final_sweep.closing_block_end(
            words, statuses0, bounds0, ends0
        )
        if closing_end is not None:
            trailing = [
                i
                for i in range(closing_end, len(words))
                if statuses0[i] == "kept"
            ]
            if not trailing:
                fin_cap = closing_end
            else:
                decided_words: set = set()
                for record in _load_gold(config):
                    if record.get("status") in (None, "open", "auto_cut"):
                        continue
                    try:
                        decided_words.update(
                            range(
                                int(record["word_start"]),
                                int(record["word_end"]),
                            )
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
                open_gold = [
                    view
                    for view in _open_gold_spans(config)
                    if isinstance(view.get("word_start"), int)
                    and isinstance(view.get("word_end"), int)
                ]
                held = any(
                    i in profile_words
                    or i in decided_words
                    or any(
                        view["word_start"] <= i < view["word_end"]
                        for view in open_gold
                    )
                    for i in trailing
                )
                if held:
                    summary["left"].append(
                        {
                            "word_start": trailing[0],
                            "word_end": trailing[-1] + 1,
                            "blocked": "protected",
                        }
                    )
                else:
                    # Cut exactly the kept trailing words, in
                    # their contiguous runs: the checked path
                    # rejects a span that is not fully kept, and
                    # words after the block that an earlier pass
                    # already cut stay cut on their own record.
                    runs = []
                    for i in trailing:
                        if runs and i == runs[-1][1]:
                            runs[-1][1] = i + 1
                        else:
                            runs.append([i, i + 1])
                    landed = True
                    for run_start, run_end in runs:
                        operation = {
                            "type": "cut_words",
                            "comment_id": "final-sweep",
                            "word_start": run_start,
                            "word_end": run_end,
                            "reason": _FINAL_SWEEP_TRAILING_REASON,
                            "confidence": 1.0,
                            "model": "mock",
                            "risk_tier": 3,
                            "wonder_reviewed": False,
                        }
                        candidate = {
                            "word_start": run_start,
                            "word_end": run_end,
                            "operation": operation,
                        }
                        if not _apply_detected_repeat_cut(
                            config, candidate
                        ):
                            landed = False
                    if landed:
                        summary["trailing_cut"] = {
                            "word_start": trailing[0],
                            "word_end": trailing[-1] + 1,
                        }
                        fin_cap = closing_end
                    else:
                        summary["left"].append(
                            {
                                "word_start": trailing[0],
                                "word_end": trailing[-1] + 1,
                                "blocked": "trailing_cut_failed",
                            }
                        )

        def settled():
            plan = _effective_cuts(config)
            timeline = Timeline(transcript, plan)
            statuses = [
                timeline.word_status(i) for i in range(len(words))
            ]
            bounds = final_sweep.word_bounds(words, plan)
            ends = final_sweep.punctuation_sentence_ends(words)
            fin = final_sweep.final_saying_of_video(
                words, bounds, ends, end_word=fin_cap
            )
            return timeline, statuses, bounds, ends, fin

        timeline, statuses, bounds, ends, fin = settled()
        summary["ran"] = True

        # The final saying first: restoring it can change the
        # units the stopped-span scan reads.
        if fin is not None:
            fin_start, fin_end = fin
            decided = any(
                record.get("gold_id")
                == _gold_id(fin_start, fin_end, "held")
                and record.get("status") != "open"
                for record in _load_gold(config)
            )
            if not decided and any(
                statuses[i] == "cut" for i in range(fin_start, fin_end)
            ):
                if _apply_final_saying_restore(config, fin_start, fin_end):
                    summary["restored_final_saying"] = True
                    spot = _final_sweep_spot(
                        words,
                        {"word_start": fin_start, "word_end": fin_end},
                        _FINAL_SWEEP_FINAL_REASON,
                        {
                            "type": "cut_words",
                            "comment_id": "final-sweep",
                            "word_start": fin_start,
                            "word_end": fin_end,
                            "reason": (
                                "You looked at the last line of the "
                                "video and said to cut it."
                            ),
                            "confidence": 1.0,
                            "model": "mock",
                            "risk_tier": 3,
                            "wonder_reviewed": False,
                        },
                    )
                    _record_gold_spans(config, [spot])
                    summary["spots"].append(spot["gold_id"])
                    timeline, statuses, bounds, ends, fin = settled()
                else:
                    summary["left"].append(
                        {
                            "word_start": fin_start,
                            "word_end": fin_end,
                            "blocked": "restore_failed",
                        }
                    )

        protected_words: set = set()
        for span in timeline.protected_spans(profile):
            try:
                protected_words.update(
                    range(int(span["word_start"]), int(span["word_end"]))
                )
            except (KeyError, TypeError, ValueError):
                continue
        for view in _open_gold_spans(config):
            if isinstance(view.get("word_start"), int) and isinstance(
                view.get("word_end"), int
            ):
                protected_words.update(
                    range(view["word_start"], view["word_end"])
                )

        # The post-model gate, part one (the keyed path,
        # filming day): a sentence the settled plan removed,
        # whose thought no kept sentence says again, comes
        # back. The direction guard holds proposals at apply
        # time, but the keyed path's retake judge and the
        # restrictive auto-strike settle cuts no proposal ever
        # carries, so on Justin's keyed Day 48 run whole
        # once-said sentences (a verse quote, a prayer line)
        # left the edit with no guard ever seeing them go.
        # The gate asks the question of the settled plan
        # itself, in every path, just before the render: a
        # sentence counts as removed when half or fewer of
        # its words are kept, and it stays removed only when
        # a kept partner sentence says its thought again
        # (the guard's own _sentence_has_partner test).
        # Sentences are visited last-first, so when a thought
        # was said more than once and every saying was
        # removed, the LAST saying is the one restored and
        # the earlier ones then have their kept partner:
        # last take wins, the standing law. Decided spans and
        # protected words are his calls and are never
        # restored over; speech after the closing block
        # belongs to the trailing rule above, not this one.
        decided_words: set = set()
        for record in _load_gold(config):
            if record.get("status") in (None, "open", "auto_cut"):
                continue
            try:
                decided_words.update(
                    range(
                        int(record["word_start"]),
                        int(record["word_end"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        for sent in reversed(_sentence_spans(words)):
            s0, s1 = sent
            if s1 - s0 < 3:
                continue
            if closing_end is not None and s0 >= closing_end:
                continue
            kept_n = sum(
                1 for i in range(s0, s1) if statuses[i] == "kept"
            )
            if kept_n * 2 > (s1 - s0):
                continue
            if any(
                i in decided_words or i in protected_words
                for i in range(s0, s1)
            ):
                continue
            if _sentence_has_partner(
                words, timeline, sent, set(range(s0, s1))
            ):
                continue
            if _apply_unique_sentence_restore(config, s0, s1):
                summary["restored_sentences"].append(
                    {"word_start": s0, "word_end": s1}
                )
                spot = _final_sweep_spot(
                    words,
                    {"word_start": s0, "word_end": s1},
                    _FINAL_SWEEP_UNIQUE_REASON,
                    {
                        "type": "cut_words",
                        "comment_id": "final-sweep",
                        "word_start": s0,
                        "word_end": s1,
                        "reason": (
                            "You looked at this sentence and "
                            "said to cut it."
                        ),
                        "confidence": 1.0,
                        "model": "mock",
                        "risk_tier": 3,
                        "wonder_reviewed": False,
                    },
                )
                _record_gold_spans(config, [spot])
                summary["spots"].append(spot["gold_id"])
                timeline, statuses, bounds, ends, fin = settled()
            else:
                summary["left"].append(
                    {
                        "word_start": s0,
                        "word_end": s1,
                        "blocked": "unique_restore_failed",
                    }
                )

        spans = final_sweep.find_stopped_spans(
            words,
            statuses,
            bounds,
            ends,
            protected_words=protected_words,
            final_span=fin,
        )
        for span in spans:
            entry = {
                "word_start": span["word_start"],
                "word_end": span["word_end"],
                "completer_word_start": span["completer_word_start"],
                "completer_word_end": span["completer_word_end"],
                "gap_s": span["gap_s"],
                "coverage": span["coverage"],
            }
            if span["blocked"]:
                entry["blocked"] = span["blocked"]
                summary["left"].append(entry)
                continue
            operation = {
                "type": "cut_words",
                "comment_id": "final-sweep",
                "word_start": span["word_start"],
                "word_end": span["word_end"],
                "reason": _FINAL_SWEEP_CUT_REASON,
                "confidence": 1.0,
                "model": "mock",
                "risk_tier": 3,
                "wonder_reviewed": False,
            }
            candidate = {
                "word_start": span["word_start"],
                "word_end": span["word_end"],
                "operation": operation,
            }
            if _apply_detected_repeat_cut(config, candidate):
                summary["cuts"].append(entry)
            else:
                spot = _final_sweep_spot(
                    words, span, _FINAL_SWEEP_HELD_REASON, operation
                )
                _record_gold_spans(config, [spot])
                summary["spots"].append(spot["gold_id"])

        # Restart chains (Chunk R): sayings that keep returning
        # to the same opening, reduced to the saying that
        # lands. The settled plan is re-read first: the stopped
        # cuts above changed it. Undecided gold flags do not
        # block these cuts (a retake flag over the same words
        # is the same call, still unsettled, and waiting on it
        # is how the chains survived); the creator's protected
        # words, decided spans, and the final saying do.
        timeline, statuses, bounds, ends, fin = settled()
        chain_protected = set(profile_words)
        for record in _load_gold(config):
            if record.get("status") in (None, "open", "auto_cut"):
                continue
            try:
                chain_protected.update(
                    range(
                        int(record["word_start"]),
                        int(record["word_end"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        chains = final_sweep.find_restart_chains(
            words,
            statuses,
            bounds,
            ends,
            protected_words=chain_protected,
            final_span=fin,
        )
        for record in chains:
            entry = {
                "word_start": record["word_start"],
                "word_end": record["word_end"],
                "keeper_word_start": record["keeper_word_start"],
                "keeper_word_end": record["keeper_word_end"],
                "coverage": record["coverage"],
                "kind": record["kind"],
            }
            if record["blocked"]:
                entry["blocked"] = record["blocked"]
                summary["left"].append(entry)
                continue
            operation = {
                "type": "cut_words",
                "comment_id": "final-sweep",
                "word_start": record["word_start"],
                "word_end": record["word_end"],
                "reason": _FINAL_SWEEP_CHAIN_REASON,
                "confidence": 1.0,
                "model": "mock",
                "risk_tier": 3,
                "wonder_reviewed": False,
            }
            candidate = {
                "word_start": record["word_start"],
                "word_end": record["word_end"],
                "operation": operation,
            }
            if _apply_detected_repeat_cut(config, candidate):
                summary["chains"].append(entry)
            else:
                spot = _final_sweep_spot(
                    words,
                    record,
                    _FINAL_SWEEP_CHAIN_HELD_REASON,
                    operation,
                )
                _record_gold_spans(config, [spot])
                summary["spots"].append(spot["gold_id"])

        # The post-model gate, part two: the chain and
        # stopped-try detections read off the recording
        # itself. The scans above read the settled plan's
        # units; on the keyed path the model's passes splice
        # the tries apart first (a few words of each try cut,
        # a few kept), so the settled units are fragments and
        # the scans find nothing: Justin's keyed Day 48 edit
        # kept four "And then later on I learned" tries in a
        # row that no scan ever saw as a chain. Read off the
        # recording's own segmentation (every word kept), the
        # chains and tries stand whole. What they mark is
        # enforced on the words the plan still keeps, in
        # their contiguous runs, through the same checked
        # path the trailing cut uses. A detection whose
        # landing saying did not itself survive is left
        # alone, and the hard protections hold as above.
        timeline, statuses, bounds, ends, fin = settled()
        all_kept = ["kept"] * len(words)
        handled_spans = {
            (c["word_start"], c["word_end"])
            for c in summary["chains"] + summary["cuts"]
        }

        def cut_kept_runs(start, end, reason):
            runs = []
            for i in range(start, end):
                if statuses[i] != "kept":
                    continue
                if runs and i == runs[-1][1]:
                    runs[-1][1] = i + 1
                else:
                    runs.append([i, i + 1])
            landed_all = True
            for run_start, run_end in runs:
                operation = {
                    "type": "cut_words",
                    "comment_id": "final-sweep",
                    "word_start": run_start,
                    "word_end": run_end,
                    "reason": reason,
                    "confidence": 1.0,
                    "model": "mock",
                    "risk_tier": 3,
                    "wonder_reviewed": False,
                }
                candidate = {
                    "word_start": run_start,
                    "word_end": run_end,
                    "operation": operation,
                }
                if _apply_detected_repeat_cut(config, candidate):
                    for i in range(run_start, run_end):
                        statuses[i] = "cut"
                else:
                    landed_all = False
            return bool(runs) and landed_all

        rec_chains = final_sweep.find_restart_chains(
            words,
            all_kept,
            bounds,
            ends,
            protected_words=chain_protected,
            final_span=fin,
        )
        # A keeper survives when most of it is still in the
        # plan, or when it is itself a middle try whose own
        # record lands on a surviving saying: the records
        # form a ladder (try, try, landing), and the middle
        # rungs are cut precisely because the top one lands.
        rec_try_spans = {
            (r["word_start"], r["word_end"])
            for r in rec_chains
            if not r["blocked"]
        }
        covered_cache: dict = {}

        def keeper_survives(k0, k1):
            if k1 <= k0:
                return False
            key = (k0, k1)
            if key in covered_cache:
                return covered_cache[key]
            covered_cache[key] = False
            kept_k = sum(
                1 for i in range(k0, k1) if statuses[i] == "kept"
            )
            if kept_k * 2 > (k1 - k0):
                covered_cache[key] = True
            elif key in rec_try_spans:
                for r in rec_chains:
                    if (r["word_start"], r["word_end"]) == key and (
                        keeper_survives(
                            r["keeper_word_start"],
                            r["keeper_word_end"],
                        )
                    ):
                        covered_cache[key] = True
                        break
            return covered_cache[key]


        for record in rec_chains:
            key = (record["word_start"], record["word_end"])
            if record["blocked"] or key in handled_spans:
                continue
            if not keeper_survives(
                record["keeper_word_start"], record["keeper_word_end"]
            ):
                continue
            entry = {
                "word_start": record["word_start"],
                "word_end": record["word_end"],
                "keeper_word_start": record["keeper_word_start"],
                "keeper_word_end": record["keeper_word_end"],
                "coverage": record["coverage"],
                "kind": record["kind"],
            }
            if cut_kept_runs(
                record["word_start"],
                record["word_end"],
                _FINAL_SWEEP_CHAIN_REASON,
            ):
                summary["recording_chains"].append(entry)
            else:
                operation = {
                    "type": "cut_words",
                    "comment_id": "final-sweep",
                    "word_start": record["word_start"],
                    "word_end": record["word_end"],
                    "reason": _FINAL_SWEEP_CHAIN_REASON,
                    "confidence": 1.0,
                    "model": "mock",
                    "risk_tier": 3,
                    "wonder_reviewed": False,
                }
                spot = _final_sweep_spot(
                    words,
                    record,
                    _FINAL_SWEEP_CHAIN_HELD_REASON,
                    operation,
                )
                _record_gold_spans(config, [spot])
                summary["spots"].append(spot["gold_id"])

        rec_spans = final_sweep.find_stopped_spans(
            words,
            all_kept,
            bounds,
            ends,
            protected_words=protected_words,
            final_span=fin,
        )
        for span in rec_spans:
            key = (span["word_start"], span["word_end"])
            if span["blocked"] or key in handled_spans:
                continue
            if not keeper_survives(
                span["completer_word_start"],
                span["completer_word_end"],
            ):
                continue
            entry = {
                "word_start": span["word_start"],
                "word_end": span["word_end"],
                "completer_word_start": span["completer_word_start"],
                "completer_word_end": span["completer_word_end"],
                "gap_s": span["gap_s"],
                "coverage": span["coverage"],
            }
            if cut_kept_runs(
                span["word_start"],
                span["word_end"],
                _FINAL_SWEEP_CUT_REASON,
            ):
                summary["recording_spans"].append(entry)
            else:
                operation = {
                    "type": "cut_words",
                    "comment_id": "final-sweep",
                    "word_start": span["word_start"],
                    "word_end": span["word_end"],
                    "reason": _FINAL_SWEEP_CUT_REASON,
                    "confidence": 1.0,
                    "model": "mock",
                    "risk_tier": 3,
                    "wonder_reviewed": False,
                }
                spot = _final_sweep_spot(
                    words, span, _FINAL_SWEEP_HELD_REASON, operation
                )
                _record_gold_spans(config, [spot])
                summary["spots"].append(spot["gold_id"])

        # Lead-in chains (his ruling, Nov 4): sayings that
        # start the same way, close together, are the creator
        # searching for the line, not separate points. Keep
        # only the last complete saying, even when the earlier
        # ones end differently: the earlier tries are cut, and
        # the review shows ONE spot for the whole chain. A try
        # carrying a negation the keeper does not carry, or a
        # number the keeper does not say, is his call: it waits
        # as a your-call spot, and its presence does not protect
        # the other tries. A negation on both sides is no
        # exception: the try is cut with the rest. Read
        # off the recording like the chains above, enforced on
        # the words the plan still keeps.
        lead_clusters = final_sweep.find_lead_in_chains(
            words,
            all_kept,
            bounds,
            ends,
            protected_words=chain_protected,
            final_span=fin,
        )
        for cluster in lead_clusters:
            if not keeper_survives(
                cluster["keeper_word_start"],
                cluster["keeper_word_end"],
            ):
                continue
            landed: list = []
            for try_ in cluster["tries"]:
                key = (try_["word_start"], try_["word_end"])
                if key in handled_spans:
                    continue
                if try_["waits"]:
                    operation = {
                        "type": "cut_words",
                        "comment_id": "final-sweep",
                        "word_start": try_["word_start"],
                        "word_end": try_["word_end"],
                        "reason": _FINAL_SWEEP_CHAIN_HELD_REASON,
                        "confidence": 1.0,
                        "model": "mock",
                        "risk_tier": 3,
                        "wonder_reviewed": False,
                    }
                    spot = _final_sweep_spot(
                        words,
                        {
                            "word_start": try_["word_start"],
                            "word_end": try_["word_end"],
                            "completer_word_start":
                                cluster["keeper_word_start"],
                            "completer_word_end":
                                cluster["keeper_word_end"],
                        },
                        _FINAL_SWEEP_CHAIN_HELD_REASON,
                        operation,
                    )
                    _record_gold_spans(config, [spot])
                    summary["spots"].append(spot["gold_id"])
                    continue
                if cut_kept_runs(
                    try_["word_start"],
                    try_["word_end"],
                    _FINAL_SWEEP_CHAIN_REASON,
                ):
                    landed.append(try_)
            if not landed:
                continue
            # The spot counts every try at the line that is
            # cut in the final plan, including the ones an
            # earlier pass already took: from the chair they
            # are one chain with one keeper.
            cut_tries = sum(
                1
                for try_ in cluster["tries"]
                if not try_["waits"]
                and all(
                    statuses[i] == "cut"
                    for i in range(
                        try_["word_start"], try_["word_end"]
                    )
                )
            )
            entry = {
                "word_start": landed[0]["word_start"],
                "word_end": landed[-1]["word_end"],
                "keeper_word_start": cluster["keeper_word_start"],
                "keeper_word_end": cluster["keeper_word_end"],
                "tries": cut_tries,
                "kind": "lead_in_chain",
            }
            # ONE spot for the whole chain, in his shape:
            # the count of tries, kept the last, bring back
            # restores the tries (an auto_cut record over the
            # landed try block, like every settled repeat).
            block_start = landed[0]["word_start"]
            block_end = landed[-1]["word_end"]
            contiguous = all(
                landed[i + 1]["word_start"]
                <= landed[i]["word_end"]
                for i in range(len(landed) - 1)
            )
            if contiguous:
                record = {
                    "gold_id": _gold_id(block_start, block_end, "auto"),
                    "kind": "chain",
                    "source": "final_sweep",
                    "word_start": block_start,
                    "word_end": block_end,
                    "text": " ".join(
                        str(words[i].get("word") or "")
                        for i in range(block_start, block_end)
                    ),
                    "keep_text": " ".join(
                        str(words[i].get("word") or "")
                        for i in range(
                            cluster["keeper_word_start"],
                            cluster["keeper_word_end"],
                        )
                    ),
                    "keep_word_start": cluster["keeper_word_start"],
                    "keep_word_end": cluster["keeper_word_end"],
                    "again_word_start": cluster["keeper_word_start"],
                    "again_word_end": cluster["keeper_word_end"],
                    "reason": (
                        f"{cut_tries} "
                        f"{'try' if cut_tries == 1 else 'tries'} "
                        "at this line, kept the last. The final "
                        "check took the earlier tries out; bring "
                        "them back if you want one in."
                    ),
                    "copies_differ": False,
                    "operation": {
                        "type": "cut_words",
                        "comment_id": "final-sweep",
                        "word_start": block_start,
                        "word_end": block_end,
                        "reason": _FINAL_SWEEP_CHAIN_REASON,
                        "confidence": 1.0,
                        "model": "mock",
                        "risk_tier": 3,
                        "wonder_reviewed": False,
                    },
                    "pair_id": None,
                    "pair_key": None,
                    "status": "auto_cut",
                    "auto_struck": True,
                    "basis": "lead_in_chain",
                    "note": None,
                    "created_at": _utc_now(),
                    "decided_at": _utc_now(),
                }
                _record_gold_spans(config, [record])
                entry["spot"] = record["gold_id"]
                summary["spots"].append(record["gold_id"])
            summary["lead_in_chains"].append(entry)

        # The silence ceiling (Chunk R): no silence of half a
        # second or more survives inside a kept piece, whatever
        # path built the plan. The smooth pass trims these gaps
        # too, but it may not touch the quiet beside an
        # undecided your-call span's words, and flagged spans
        # sit exactly where the long pauses live. The sweep
        # trims the quiet and never the words: Keep-marked
        # words still shield the pauses beside them, and a
        # flagged span's piece is tightened, never dropped.
        ceiling = {"ran": False, "trimmed": 0, "seconds_removed": 0.0}
        try:
            wav_path = config.job_dir / FIRST_EDIT_WORK_NAME / "audio.wav"
            if not wav_path.is_file():
                source_name = intake_build.intake_source_video(
                    config.job_dir
                )
                if source_name:
                    config.session_dir.mkdir(parents=True, exist_ok=True)
                    candidate_wav = (
                        config.session_dir / ".sweep-audio.wav"
                    )
                    speech.extract_audio(
                        config.job_dir / source_name, candidate_wav
                    )
                    if candidate_wav.is_file():
                        wav_path = candidate_wav
            if wav_path.is_file():
                plan = _effective_cuts(config)
                ranges = plan.get("kept_ranges") or []
                if ranges:
                    env = smooth.envelope_db(wav_path)
                    spans = speech.measure_speech_spans(wav_path)
                    gold_words: set = set()
                    for view in _open_gold_spans(config):
                        if isinstance(
                            view.get("word_start"), int
                        ) and isinstance(view.get("word_end"), int):
                            gold_words.update(
                                range(
                                    view["word_start"],
                                    view["word_end"],
                                )
                            )
                    new_ranges, report = smooth.trim_internal_silences(
                        words,
                        ranges,
                        env,
                        segments=transcript.get("segments"),
                        protected_words=profile_words,
                        gold_words=gold_words,
                        fps=timeline.fps,
                        spans=spans,
                    )
                    ceiling = {
                        "ran": True,
                        "trimmed": report["trimmed"],
                        "seconds_removed": report["seconds_removed"],
                    }
                    if new_ranges != ranges:
                        new_cuts = copy.deepcopy(plan)
                        new_cuts["kept_ranges"] = new_ranges
                        config.session_dir.mkdir(
                            parents=True, exist_ok=True
                        )
                        _write_json(
                            config.session_dir / "revised_cuts.json",
                            new_cuts,
                        )
        except Exception as exc:
            _log_raw_error("final sweep silence ceiling", exc)
        summary["silence_ceiling"] = ceiling

        config.session_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            config.session_dir / checkpoints.FINAL_SWEEP_FILENAME,
            {"at": _utc_now(), **summary},
        )
        session_log.log_stage(
            config.session_dir,
            "final sweep",
            "finish",
            f"{len(summary['cuts'])} stopped tries cut, "
            f"{len(summary['chains'])} restarts cut, "
            f"trailing cut: {summary['trailing_cut'] is not None}, "
            f"ceiling trims: {summary['silence_ceiling']['trimmed']}, "
            f"{len(summary['spots'])} spots, "
            f"final saying restored: {summary['restored_final_saying']}",
        )
    except Exception as exc:  # the sweep never breaks a run
        _log_raw_error("final sweep", exc)
    return summary


# ---------------------------------------------------------------------------
# Detected repeats settle as cuts, in every mode (v0.15 item 22,
# extended by v0.17, feedback items 7 and 8)
#
# A span the app itself has detected as a repeat does not wait
# in gold: it is struck by default, and leaving it in takes an
# explicit ask (the creator brings the words back, like any
# cut). The settlement happens here, at gold-record time, in
# the two passes that produce gold candidates, so the decision
# lands at the same point that used to park the span.
#
# v0.15 settled repeats only in the Cleanest cut mode. Justin's
# 0.15 review (feedback items 7 and 8) rejected the other
# mode's behavior outright: detected retakes were left in and
# the app asked "recommend to remove?" instead of cutting.
# His words: "Be proactive, cut the retakes, and let me know
# if I need to bring them back." So the settlement now runs in
# every way of working. The one case that still asks is a pair
# whose copies genuinely say different things (a negation
# pair: one copy includes a "not"); those wait in gold with
# the default stated on the card (the copy that includes more
# stays). Everything else that is not a detected repeat (a big
# held change, a protected span) keeps the old behavior in
# both modes.
# ---------------------------------------------------------------------------


def _auto_struck_records(config: WebAppConfig) -> list:
    """The gold records this session settled automatically."""
    return [
        record
        for record in _load_gold(config)
        if record.get("status") == "auto_cut"
    ]


def _apply_detected_repeat_cut(config: WebAppConfig, candidate: dict) -> bool:
    """Apply one detected-repeat candidate as a cut, in place.

    The cut runs the same validate, verify, and apply path a gold
    "cut" verdict runs. The soft holds that made the span gold (a
    negation, a missing second review) are the decision this
    settlement is making, so they do not hold it again; every other
    safety rule (protected material, the 25 percent rule, a
    structural problem) holds exactly as written, and when one
    does, the cut does not land and the span stays gold.
    """
    start, end = candidate.get("word_start"), candidate.get("word_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    operation = candidate.get("operation")
    if not isinstance(operation, dict):
        operation = {
            "type": "cut_words",
            "comment_id": "gold-auto-repeat",
            "word_start": start,
            "word_end": end,
            "reason": (
                "The editor detected this as a repeat and struck "
                "it by default. Detected retakes are cut in every "
                "way of working; bring the words back if you want "
                "this copy in."
            ),
            "confidence": 1.0,
            "model": "mock",
            "risk_tier": 3,
            "wonder_reviewed": False,
        }
    try:
        transcript = _load_json(str(config.job_dir / "transcript.json"))
        base_cuts = _effective_cuts(config)
        timeline = Timeline(transcript, base_cuts)
        ops = validate_operations([operation])
        profile = _load_optional_file(config.job_dir / "profile.json")
        verdict = verify_operations(ops, timeline, profile)[0]
    except (SchemaError, ApplyError, OSError, ValueError):
        return False
    if verdict.status == "flag" and set(verdict.reasons) <= GOLD_SOFT_HOLDS:
        verdict.status = "apply"
    if verdict.status != "apply":
        return False
    try:
        revised, _audit = apply_verdicts(base_cuts, timeline, [verdict])
    except (SchemaError, ApplyError):
        return False
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    _write_json(session / "revised_cuts.json", revised)
    return True


def _record_gold_detected_repeats(
    config: WebAppConfig,
    candidates: list,
    mode: str,
    protected_ranges=(),
) -> list:
    """Record one pass's gold candidates, settling repeats first.

    In every way of working (v0.17; v0.15 settled only in
    cleanest mode), a candidate whose basis is detection of a
    repeat settles as an applied cut instead of waiting in gold:

    - every retake candidate (an earlier take said again right
      after; basis ``retake_repeat``);
    - a direction ``cut_words`` / ``replace_take`` candidate whose
      narrowed span duplicates words that stay, by the same exact
      test the negation policy uses (basis ``duplicate``),
      whatever its soft-hold reason.

    The one repeat that does not settle: a pair whose copies
    genuinely say different things (``copies_differ``: a
    negation pair, copies naming different numbers, or a
    parallel repeat said on purpose). Which copy survives can
    flip the meaning, so it waits in gold as a question, with
    the default stated on its card (feedback item 8). The
    ``mode`` argument is still accepted from the passes; it no
    longer gates the settlement.

    A candidate that overlaps protected material never settles,
    and a cut the verifier still refuses for any non-soft reason
    (protected material, the 25 percent rule, a structural hold)
    stays gold. Settled spans are recorded in the gold store with
    status ``auto_cut`` (word spans plus basis), so they are never
    offered as waiting spots, and their cuts sit in the plan like
    any cut, restorable by the usual bring-back and undo. Returns
    the settled spans, as the run record carries them.
    """
    if not candidates:
        _record_gold_spans(config, candidates)
        return []
    if not config.has_job:
        _record_gold_spans(config, candidates)
        return []
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    profile = _load_optional_file(config.job_dir / "profile.json")
    protected = [
        (span["word_start"], span["word_end"])
        for span in Timeline(
            transcript, _effective_cuts(config)
        ).protected_spans(profile)
    ]
    protected.extend(
        (int(s), int(e)) for s, e in (protected_ranges or ())
    )

    def overlaps_protected(start: int, end: int) -> bool:
        return any(
            start < p_end and p_start < end for p_start, p_end in protected
        )

    # A your-call span is never cut without the creator's yes
    # (0.27, fix 8): an open gold record whose copies genuinely
    # differ (copies_differ: the record IS a your-call), and any
    # span this same batch is parking as a your-call, blocks the
    # settlement for any candidate that overlaps it, even a
    # candidate that claims its own copies match. The settlement
    # used to replace an open record for the same span with its
    # auto_cut record; that is how a labeled your-call line
    # ("Because if I'm going to be praying...") was struck by a
    # later pass. An open record that is NOT a your-call (a
    # plain detected repeat parked by an older rule) gains no
    # such protection: re-spotting it settles it, per the v0.15
    # contract (test_v15_strictest).
    # Filming-day P0-4 (restrictive by default, his choice):
    # a likely retake settles as a cut and is shown as a spot
    # he can bring back, never left in for him to find. The
    # one differing repeat that still waits is the parallel
    # line inside one sentence: performed repetition is
    # content, not a take. Only those spans still block.
    def _still_waits(record) -> bool:
        return bool(
            record.get("copies_differ")
            and record.get("reason") == retakes._FLAG_PARALLEL_REASON
        )

    gold_blocked = [
        (int(record["word_start"]), int(record["word_end"]))
        for record in _load_gold(config)
        if record.get("status") == "open"
        and _still_waits(record)
        and isinstance(record.get("word_start"), int)
        and isinstance(record.get("word_end"), int)
    ]
    gold_blocked.extend(
        (int(c["word_start"]), int(c["word_end"]))
        for c in candidates
        if _still_waits(c)
        and isinstance(c.get("word_start"), int)
        and isinstance(c.get("word_end"), int)
    )

    def overlaps_gold(start: int, end: int) -> bool:
        return any(
            start < g_end and g_start < end
            for g_start, g_end in gold_blocked
        )

    settled: list = []
    waiting: list = []
    for candidate in candidates:
        start, end = candidate.get("word_start"), candidate.get("word_end")
        basis = None
        if (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start < end <= len(transcript.get("words", []))
            and not overlaps_protected(start, end)
            and not overlaps_gold(start, end)
            and not _still_waits(candidate)
        ):
            if candidate.get("source") == "retakes":
                basis = "retake_repeat"
            elif candidate.get("source") == "direction":
                operation = candidate.get("operation")
                op_type = (
                    operation.get("type")
                    if isinstance(operation, dict)
                    else candidate.get("type")
                )
                if op_type in ("cut_words", "replace_take"):
                    timeline = Timeline(
                        transcript, _effective_cuts(config)
                    )
                    if _span_duplicates_kept(timeline, start, end):
                        basis = "duplicate"
        if basis is None:
            waiting.append(candidate)
            continue
        if not _apply_detected_repeat_cut(config, candidate):
            waiting.append(candidate)
            continue
        record = dict(candidate)
        record["status"] = "auto_cut"
        record["auto_struck"] = True
        record["basis"] = basis
        record["decided_at"] = _utc_now()
        settled.append(record)
        if record.get("pair_key"):
            # The pair is decided, the same record a gold cut
            # verdict writes, so it is never proposed again.
            retake_record = _load_retake_record(config)
            decisions = retake_record.setdefault("decisions", {})
            decisions[record["pair_key"]] = {
                "keep_both": False,
                "source": "auto_repeat",
            }
            _save_retake_record(config, retake_record)
    _record_gold_spans(config, waiting)
    if settled:
        # A settled span REPLACES any record the store already
        # holds for it: the same span may have been recorded open
        # by an earlier run (the creator never decided it), and
        # the store's usual keep-the-existing-record rule, which
        # protects verdicts already given, must not leave a struck
        # span still offered as waiting. (A span the creator DID
        # decide can never be a candidate again, so no verdict is
        # ever overwritten here.)
        records = _load_gold(config)
        by_id = {
            record.get("gold_id"): index
            for index, record in enumerate(records)
        }
        for record in settled:
            index = by_id.get(record.get("gold_id"))
            if index is None:
                records.append(record)
            else:
                records[index] = record
        records.sort(
            key=lambda r: (r.get("word_start", 0), r.get("word_end", 0))
        )
        _save_gold(config, records)
    return [
        {
            "gold_id": record.get("gold_id"),
            "word_start": record.get("word_start"),
            "word_end": record.get("word_end"),
            "kind": record.get("kind"),
            "source": record.get("source"),
            "basis": record.get("basis"),
            "text": record.get("text"),
        }
        for record in settled
    ]


def _gold_view(record: dict) -> dict:
    """One gold span as the page receives it (no stored operation)."""
    return {
        "gold_id": record.get("gold_id"),
        "word_start": record.get("word_start"),
        "word_end": record.get("word_end"),
        "kind": record.get("kind"),
        "source": record.get("source"),
        "pair_id": record.get("pair_id"),
        "reason": record.get("reason"),
        "text": record.get("text"),
        "keep_text": record.get("keep_text"),
        "keep_word_start": record.get("keep_word_start"),
        "keep_word_end": record.get("keep_word_end"),
        "again_word_start": record.get("again_word_start"),
        "again_word_end": record.get("again_word_end"),
        "again_output_s": record.get("again_output_s"),
    }


def _refresh_same_point_times(
    config: WebAppConfig, records: list, transcript: dict, plan: dict, timeline
) -> None:
    """Re-read a stored spot's "says it again" time from sound.

    A spot stored before the detector learned to read sound
    names the moment its stamp arithmetic produced (2:57 on
    the stored Day 30 job), while the repeat actually sits at
    0:57 by measured sound. Fresh runs record the sound time;
    a stored spot is re-measured when the job opens, here, and
    the record is healed in place when the number really moved
    (0.27.1, run-through finding 5).
    """
    words = transcript.get("words") or []
    sound_bounds = plan.get("word_sound_bounds") or []
    changed = []
    for record in records:
        if record.get("kind") != "same_point":
            continue
        again = record.get("again_word_start")
        if not isinstance(again, int) or not 0 <= again < len(words):
            continue
        fresh = _spot_output_time(timeline, words, sound_bounds, again)
        stored = record.get("again_output_s")
        if isinstance(stored, (int, float)) and abs(
            float(stored) - fresh
        ) <= 0.5:
            continue
        record["again_output_s"] = fresh
        record["reason"] = (
            "This makes the same point as "
            f"{_format_flag_time(fresh)}. Cut it?"
        )
        changed.append(record)
    if not changed:
        return
    try:
        all_records = _load_gold(config)
        by_id = {r.get("gold_id"): r for r in all_records}
        for record in changed:
            saved = by_id.get(record.get("gold_id"))
            if saved is not None:
                saved["again_output_s"] = record["again_output_s"]
                saved["reason"] = record["reason"]
        _save_gold(config, all_records)
    except Exception:
        pass


def _open_gold_spans(config: WebAppConfig) -> list:
    """The gold spans still waiting, as the page shows them.

    A span is offered only while its words are all in one state in
    the current plan (all kept, for a cut that was held; all cut,
    for a change that would bring words back) and none of them sits
    inside a special insert. A span another change has already
    settled simply stops being offered.
    """
    records = [
        record
        for record in _load_gold(config)
        if record.get("status") == "open"
    ]
    if not records or not config.has_job:
        return []
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    plan = _effective_cuts(config)
    timeline = Timeline(transcript, plan)
    _refresh_same_point_times(config, records, transcript, plan, timeline)
    views = []
    for record in records:
        start, end = record.get("word_start"), record.get("word_end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if not 0 <= start < end <= len(timeline.words):
            continue
        statuses = {timeline.word_status(i) for i in range(start, end)}
        if statuses == {"kept"} or statuses == {"cut"}:
            views.append(_gold_view(record))
    return views


def _mode_and_gold_lines(config: WebAppConfig, settings: dict) -> list:
    """The recap's closing lines: the mode, and the gold count.

    The mode is always named, so a run can never quietly work a
    different way than the creator thinks it did. In the Cleanest
    cut mode the gold spots are named as waiting for whenever; in
    Tailor it mode they are presented as the decisions they are.
    """
    mode = settings.get("mode", "cleanest")
    label = MODE_LABELS.get(mode, MODE_LABELS["cleanest"])
    lines = [f"Your way of working: {label}."]
    # v0.17 (feedback items 7 and 8): detected repeats are struck
    # in every mode now, so the struck line is said in every
    # mode, and the spots still waiting are never dressed up as
    # "recommended to remove" for a repeat's sake: they are the
    # few genuine calls (copies that say different things, big
    # held changes), named as waiting for his call.
    # One count (0.26, fix 3): these lines used to recount on
    # their own, struck records in one unit and gold spans in
    # another, and disagreed with the Done bubble on the same
    # edit. They read the one count now: copies removed for the
    # struck line, spots to review for the waiting line. When
    # the count cannot be read, the lines name no number at all
    # rather than recounting in a different unit.
    try:
        counts = _edit_counts(config)
    except Exception:
        counts = None
    if counts is None:
        return lines
    struck = counts["retakes_removed"] if counts else 0
    if struck:
        lines.append(
            f"{struck} detected repeat"
            f"{'s' if struck != 1 else ''} "
            f"{'were' if struck != 1 else 'was'} struck "
            "automatically. You can bring any of them back."
        )
    count = counts["spots_to_review"] if counts else 0
    spots = f"{count} spot{'s' if count != 1 else ''}"
    if mode == "tailor":
        if count:
            lines.append(
                f"{spots} {'are' if count != 1 else 'is'} "
                "waiting for your call."
            )
        else:
            lines.append("No spots are waiting for your call.")
    elif count:
        lines.append(
            f"{spots} waiting for your call, "
            f"for when you want "
            f"{'them' if count != 1 else 'it'}."
        )
    return lines


def _normalized_span(timeline: Timeline, start: int, end: int) -> list:
    return [
        _norm_word(str(timeline.words[i].get("word") or ""))
        for i in range(start, end)
    ]


def _contains_run(tokens: list, run: list) -> bool:
    """Whether ``run`` appears in ``tokens`` as one unbroken stretch."""
    if not run or len(run) > len(tokens):
        return False
    return any(
        tokens[i:i + len(run)] == run
        for i in range(len(tokens) - len(run) + 1)
    )


def _span_duplicates_kept(timeline: Timeline, start: int, end: int) -> bool:
    """Whether the span's words are said again in what stays.

    Exact on purpose: the span's normalized words must appear,
    word for word, as one unbroken run of kept words somewhere
    outside the span, at consecutive word positions. Anything less
    (a near copy, a variant) is not a duplicate, and the span waits
    in gold instead.
    """
    seq = _normalized_span(timeline, start, end)
    if not seq or any(token == "" for token in seq):
        return False
    outside = [
        index
        for index in timeline.kept_word_indices()
        if not start <= index < end
    ]
    tokens = [
        _norm_word(str(timeline.words[index].get("word") or ""))
        for index in outside
    ]
    size = len(seq)
    for i in range(len(tokens) - size + 1):
        if tokens[i:i + size] != seq:
            continue
        if outside[i:i + size] == list(
            range(outside[i], outside[i] + size)
        ):
            return True
    return False


def _direction_names_span(
    direction_text: str, timeline: Timeline, start: int, end: int
) -> bool:
    """Whether the creator's direction names this span's words.

    The direction names a span when it carries the span's words:
    the whole normalized span, or, for a span of five words or
    more, one unbroken run of five of its words. A shorter overlap
    is a topic, not a naming, and does not count.
    """
    seq = _normalized_span(timeline, start, end)
    if not seq or any(token == "" for token in seq):
        return False
    tokens = [
        token
        for token in (
            _norm_word(word) for word in str(direction_text).split()
        )
        if token
    ]
    if _contains_run(tokens, seq):
        return True
    if len(seq) >= 5:
        return any(
            _contains_run(tokens, seq[i:i + 5])
            for i in range(len(seq) - 4)
        )
    return False


def _negation_policy(settings: dict, direction_text: str, flipped: list):
    """The verdict adjustment the direction pass runs under.

    In the Cleanest cut mode, a cut verdict held only for its
    negation, on an operation Muse Wonder has reviewed, is settled
    here when the span duplicates words that stay, or the
    direction named the span. In Tailor it mode nothing is settled:
    every held negation cut waits in gold. A settled cut never
    overlaps a cut that is applying on its own (first wins, as the
    verifier itself rules), and anything with another hold reason
    keeps its hold whatever the mode.
    """
    mode = settings.get("mode", "cleanest")

    def adjust(verdicts, timeline, profile):
        if mode != "cleanest":
            return verdicts
        applying = []
        for verdict in verdicts:
            if verdict.status == "apply":
                applying.extend(
                    (s, e)
                    for s, e, _role in verdict.operation.word_spans()
                )
        for verdict in verdicts:
            if verdict.status != "flag":
                continue
            if set(verdict.reasons) != {"negation_bearing_span"}:
                continue
            op = verdict.operation
            if op.type != "cut_words" or not op.wonder_reviewed:
                continue
            start, end = op.word_start, op.word_end
            if any(
                start < other_end and other_start < end
                for other_start, other_end in applying
            ):
                continue
            if _span_duplicates_kept(timeline, start, end):
                basis = "duplicate"
            elif _direction_names_span(
                direction_text, timeline, start, end
            ):
                basis = "named"
            else:
                continue
            verdict.status = "apply"
            applying.append((start, end))
            flipped.append(
                {"word_start": start, "word_end": end, "basis": basis}
            )
        return verdicts

    return adjust


def _split_around_protected(start: int, end: int, protected_ranges) -> list:
    """The pieces of [start, end) that no protected span touches.

    Protected material always wins: a held span that reaches into
    a protected part narrows around it, and only the unprotected
    pieces are ever offered for a call.
    """
    pieces = [(start, end)]
    for p_start, p_end in protected_ranges or ():
        narrowed = []
        for piece_start, piece_end in pieces:
            if p_end <= piece_start or piece_end <= p_start:
                narrowed.append((piece_start, piece_end))
                continue
            if piece_start < p_start:
                narrowed.append((piece_start, p_start))
            if p_end < piece_end:
                narrowed.append((p_end, piece_end))
        pieces = narrowed
    return [(s, e) for s, e in pieces if s < e]


def _narrow_held_span(
    words: list,
    start: int,
    end: int,
    reference_indices,
    protected_ranges=(),
    min_run: int = 2,
) -> list:
    """Narrow a held span to the words that are actually repeated.

    A held span can cover far more than the repeated words (one
    hold over a whole hook, when only one clause in it is said
    again later). The span is matched, word for word, against the
    words it repeats (``reference_indices``: the take that stays,
    or every word outside the span); each matching run of at
    least ``min_run`` words becomes its own held span, so a part
    that should stay and a part that should go are never settled
    together. Protected words are cut out of every piece. When
    no run qualifies, the span stands as it was (still narrowed
    around protected words): a hold is never lost just because
    its repeat is not exact, and a span that merely shares short
    fragments with other sentences is never shredded into them.
    """
    if end - start < 2:
        return _split_around_protected(start, end, protected_ranges)
    span_tokens = [
        _norm_word(str(words[i].get("word") or ""))
        for i in range(start, end)
    ]
    reference_tokens = [
        _norm_word(str(words[i].get("word") or ""))
        for i in reference_indices
    ]
    if not reference_tokens or any(token == "" for token in span_tokens):
        return _split_around_protected(start, end, protected_ranges)
    matcher = SequenceMatcher(
        None, span_tokens, reference_tokens, autojunk=False
    )
    runs = []
    for block in matcher.get_matching_blocks():
        if block.size >= min_run:
            runs.append((start + block.a, start + block.a + block.size))
    if not runs:
        return _split_around_protected(start, end, protected_ranges)
    runs.sort()
    merged = [runs[0]]
    for run_start, run_end in runs[1:]:
        last_start, last_end = merged[-1]
        if run_start <= last_end:
            merged[-1] = (last_start, max(last_end, run_end))
        else:
            merged.append((run_start, run_end))
    pieces = []
    for run_start, run_end in merged:
        pieces.extend(
            _split_around_protected(run_start, run_end, protected_ranges)
        )
    return pieces or _split_around_protected(
        start, end, protected_ranges
    )


def _gold_candidates_from_direction(
    page_operations: list,
    verdict_dicts: list,
    words: list,
    protected_ranges=(),
) -> list:
    """Gold spans for the direction pass's held operations.

    A held operation becomes gold when its holds are only the
    soft ones (a negation, or a review the pass could not give
    it). Operations held for anything else (protected material, a
    special insert, too much at once) stay set aside exactly as
    before: gold is for judgment calls, not for safety rails. The
    model's own flag_for_review proposals are not held operations
    and never become gold.

    Each span is narrowed to the words that are actually repeated
    before it is recorded (v0.9.0): a span that mixes words that
    stay with a clause that is said again is split, and each part
    is offered, and settled, on its own. A cut_words operation
    narrows with its span, so a verdict cuts exactly the words
    the span shows.
    """
    candidates = []
    for entry, verdict in zip(page_operations, verdict_dicts):
        if entry.get("status") != "flagged":
            continue
        if entry.get("type") == "flag_for_review":
            continue
        reasons = (verdict or {}).get("reasons") or []
        if not reasons or not set(reasons) <= GOLD_SOFT_HOLDS:
            continue
        if entry.get("type") == "replace_take":
            start = entry.get("cut_word_start")
            end = entry.get("cut_word_end")
        else:
            start = entry.get("word_start")
            end = entry.get("word_end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if not 0 <= start < end <= len(words):
            continue
        kind = (
            "negation"
            if "negation_bearing_span" in reasons
            else "held"
        )
        operation = {
            key: value
            for key, value in entry.items()
            if key not in ("status", "plain_action", "negation_policy")
        }
        if entry.get("type") == "cut_words":
            # The reference here is the whole rest of the
            # transcript, where short shared fragments ("i am
            # not", "because the") are common accidents: only a
            # run of four or more words counts as the repeated
            # part. (A retake span is matched against its own
            # paired keeper instead, where two words are evidence.)
            pieces = _narrow_held_span(
                words,
                start,
                end,
                [i for i in range(len(words)) if not start <= i < end],
                protected_ranges,
                min_run=4,
            )
        else:
            pieces = _split_around_protected(
                start, end, protected_ranges
            )
        for piece_start, piece_end in pieces:
            piece_operation = dict(operation)
            if entry.get("type") == "cut_words":
                piece_operation["word_start"] = piece_start
                piece_operation["word_end"] = piece_end
            elif entry.get("type") == "replace_take":
                piece_operation["cut_word_start"] = piece_start
                piece_operation["cut_word_end"] = piece_end
            candidates.append(
                {
                    "gold_id": _gold_id(piece_start, piece_end, kind),
                    "word_start": piece_start,
                    "word_end": piece_end,
                    "kind": kind,
                    "source": "direction",
                    "reason": (
                        _GOLD_REASON_NEGATION
                        if kind == "negation"
                        else _GOLD_REASON_HELD
                    ),
                    "text": " ".join(
                        str(words[i].get("word") or "")
                        for i in range(piece_start, piece_end)
                    ),
                    "keep_text": None,
                    "keep_word_start": None,
                    "keep_word_end": None,
                    "pair_id": None,
                    "pair_key": None,
                    "operation": piece_operation,
                    "status": "open",
                    "note": None,
                    "created_at": _utc_now(),
                    "decided_at": None,
                }
            )
    return candidates


def _words_key(text: str) -> str:
    """A comparison key for two copies of a line: the words
    only, case and punctuation aside."""
    return " ".join(re.findall(r"[a-z0-9']+", str(text).lower()))


def _gold_candidates_from_retakes(
    pairs: list, words: list, protected_ranges=()
) -> list:
    """Gold spans for the retake pass's unapplied suspicions.

    A pair the pass flagged (a policy hold, or a borderline pair no
    model judged) and a pair whose cut the verifier held are the
    same shape to the creator: the editor thinks this is a retake
    and could not settle it. Pairs the model judged and settled
    (keep both, not a pair) are decisions already, not gold.

    A pair's cut span is offered whole (0.25.0): the retake
    engine already measured that span as the earlier take, and
    narrowing it to the words the keeper repeats exactly (the
    v0.9.0 rule) cut the take in pieces whenever the reworded
    take diverged, leaving the earlier take's unmatched words in
    the edit, spliced against the keeper (Justin's 0.24 Mac
    test, finding 2). A detected retake settles as one cut: the
    whole earlier take out, the whole later take kept. The
    narrowing still applies to direction holds, whose spans are
    not delivery units.
    """
    candidates = []
    for pair in pairs:
        outcome = pair.get("outcome")
        if outcome not in ("flagged", "held"):
            continue
        cut = pair.get("cut") or {}
        start, end = cut.get("word_start"), cut.get("word_end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if not 0 <= start < end <= len(words):
            continue
        # v0.17 (feedback item 8): a pair whose copies genuinely
        # say different things is the one retake case that still
        # asks. Two things must both hold: the pair carries the
        # negation flag (one copy includes a 'not'; the flag
        # text rides on the pair as "reason" in a saved record
        # and as "flag_reason" in a freshly scored pair), and
        # the copies' words actually differ. The same line said
        # twice with a 'not' in it carries the flag too, but
        # identical copies do not differ in content and settle
        # like every other detected repeat.
        keep = pair.get("keep") or {}
        keep_start_pre = keep.get("word_start")
        keep_end_pre = keep.get("word_end")
        cut_text = " ".join(
            str(words[i].get("word") or "") for i in range(start, end)
        )
        keep_text = str(keep.get("text") or "")
        if not keep_text and isinstance(keep_start_pre, int) \
                and isinstance(keep_end_pre, int) \
                and 0 <= keep_start_pre < keep_end_pre <= len(words):
            keep_text = " ".join(
                str(words[i].get("word") or "")
                for i in range(keep_start_pre, keep_end_pre)
            )
        negation_flag = (
            pair.get("flag_reason") == retakes._FLAG_NEGATION_REASON
            or pair.get("reason") == retakes._FLAG_NEGATION_REASON
            # A pair the verifier held (not a policy flag) carries
            # no flag text, but its own record knows the copies
            # differ by a negation (0.27, fix 8: ``negation`` on a
            # pair means exactly one copy carries a "not"). That
            # is a your-call span too, and the settlement must not
            # strike it as a plain repeat.
            or bool(pair.get("negation"))
        )
        # 0.25, finding 7: a pair waits for the creator only
        # when its copies genuinely say different things. Three
        # shapes the local record can prove: a negation in one
        # copy and the words actually differ; the copies name
        # different numbers (a fact changed between tries); or
        # the pair is a parallel repeat inside one sentence,
        # which is emphasis said on purpose, not a take. Every
        # other detected retake settles as a cut at record time.
        flag_text = pair.get("flag_reason") or pair.get("reason")
        parallel_flag = flag_text == retakes._FLAG_PARALLEL_REASON
        numbers_differ = bool(
            flag_text == retakes._FLAG_NUMBER_REASON
            or pair.get("numbers_differ")
            or (
                cut_text
                and keep_text
                and retakes._numbers_differ(
                    [retakes.normalize_word(w) for w in cut_text.split()],
                    [retakes.normalize_word(w) for w in keep_text.split()],
                )
            )
        )
        copies_differ = bool(
            (
                negation_flag
                and keep_text
                and _words_key(cut_text) != _words_key(keep_text)
            )
            or parallel_flag
            or numbers_differ
        )
        if copies_differ and parallel_flag:
            reason = retakes._FLAG_PARALLEL_REASON
        elif copies_differ and numbers_differ and not negation_flag:
            reason = _GOLD_REASON_RETAKE_NUMBERS
        elif copies_differ:
            reason = _GOLD_REASON_RETAKE_DIFFER
        elif outcome == "flagged" and pair.get("band") == "flag":
            reason = str(pair.get("reason") or _GOLD_REASON_RETAKE_HELD)
        elif outcome == "flagged":
            reason = retakes.band_b_flag_reason(pair)
        else:
            reason = _GOLD_REASON_RETAKE_HELD
        keep = pair.get("keep") or {}
        keep_start = keep.get("word_start")
        keep_end = keep.get("word_end")
        # The pair is one choice: the whole earlier take is the
        # span on the card, never narrowed into pieces. (Before
        # 0.25.0 a non-differing pair was narrowed to the exact
        # repeated runs against the keeper; the unmatched words
        # of the earlier take then survived as splices.)
        pieces = _split_around_protected(
            start, end, protected_ranges
        )
        for piece_start, piece_end in pieces:
            candidates.append(
                {
                    "gold_id": _gold_id(piece_start, piece_end, "retake"),
                    "word_start": piece_start,
                    "word_end": piece_end,
                    "kind": "retake",
                    "source": "retakes",
                    "reason": reason,
                    "text": " ".join(
                        str(words[i].get("word") or "")
                        for i in range(piece_start, piece_end)
                    ),
                    "keep_text": keep.get("text"),
                    "keep_word_start": keep.get("word_start"),
                    "keep_word_end": keep.get("word_end"),
                    "pair_id": pair.get("pair_id"),
                    "pair_key": retakes.pair_key(pair),
                    "copies_differ": copies_differ,
                    "operation": None,
                    "status": "open",
                    "note": None,
                    "created_at": _utc_now(),
                    "decided_at": None,
                }
            )
    return candidates


_BOUNDARY_REJECT_REASONS = frozenset(
    {"word_boundary_split", "frame_quantization_clips_neighbor"}
)


def _expand_cut_span(timeline: Timeline, start: int, end: int) -> tuple:
    """Widen a cut span to whole words and whole units at its edges.

    A word whose span contains the cut's quantized edge joins the
    cut, so the edge can no longer land inside it. Then the saying
    the span starts inside starts the cut, when its head words are
    all still in the edit; and the saying it ends inside ends the
    cut, when that saying never lands its sentence and its
    remaining words are all still in the edit. A finished sentence
    is never swallowed past the span's last word: the widening
    covers the spot's own saying, not the next one.
    """
    from .timeline import EPS, quantize_ceil, quantize_floor

    words = timeline.words
    total = len(words)

    def edge_inside(q: float, i: int) -> bool:
        ws = float(words[i]["start"])
        we = float(words[i]["end"])
        return ws + EPS < q < we - EPS

    def widen_words(s: int, e: int) -> tuple:
        while s > 0 and edge_inside(
            quantize_floor(float(words[s]["start"]), timeline.fps), s - 1
        ):
            s -= 1
        while e < total and edge_inside(
            quantize_ceil(float(words[e - 1]["end"]), timeline.fps), e
        ):
            e += 1
        return s, e

    s, e = widen_words(start, end)
    for unit in retakes.segment_units(timeline):
        if unit["word_start"] < s < unit["word_end"]:
            if all(
                timeline.word_status(i) == "kept"
                for i in range(unit["word_start"], s)
            ):
                s = unit["word_start"]
        if (
            unit["word_start"] < e <= unit["word_end"]
            and e < unit["word_end"]
            and not unit["terminal"]
            and all(
                timeline.word_status(i) == "kept"
                for i in range(e, unit["word_end"])
            )
        ):
            e = unit["word_end"]
    s, e = widen_words(s, e)
    return s, e


def _propose_decision_rule(
    config: WebAppConfig,
    kind: str,
    decision: str,
    note: str,
    gold_id: str,
) -> None:
    """Offer one verdict's pattern as a rule, once (source (c)).

    The proposal is a thread entry carrying the pattern and its
    statement: his own note when he gave one (his words first),
    the pattern's plain statement otherwise. One open proposal
    per pattern; none once the pattern is already a saved rule.
    Nothing is saved here: the offer only shows, and Save and
    Not now ride the rule endpoints like every other offer.
    """
    pattern = f"gold:{kind}:{decision}"
    store = _rules_store(config)
    if any(
        isinstance(rule, dict) and rule.get("pattern_key") == pattern
        for rule in store.get("rules", [])
    ):
        return
    if any(
        entry.get("kind") == "rule_proposal"
        and entry.get("pattern_key") == pattern
        and entry.get("status") == "open"
        for entry in _load_thread(config)
    ):
        return
    statement = _note_rule_statement(note) or _GOLD_RULE_STATEMENTS.get(
        (kind, decision), _GOLD_RULE_STATEMENTS[("held", decision)]
    )
    _append_thread(
        config,
        {
            "kind": "rule_proposal",
            "status": "open",
            "pattern_key": pattern,
            "statement": statement,
            "gold_id": gold_id,
            "text": "Make this a rule for future edits? " + statement,
        },
    )


def _gold_verdict(
    config: WebAppConfig, gold_id: str, decision: str, note: str
) -> dict:
    """Settle one gold span on the creator's word.

    A cut verdict runs the span's change through the same
    validate, verify, and apply path as every other edit, with one
    difference: the holds that made the span gold (a negation, a
    missing second review) are the decision the creator is now
    making, so they do not hold it again. Every other safety rule
    holds exactly as written. A leave verdict changes no words; it
    settles the span so it is never offered again in this job, and
    for a retake it records the pair's keep-both decision, the same
    record a model judgment writes. Either way the verdict is
    graded into the session's learning store with its note, so the
    pattern it belongs to can surface as a rule with the creator's
    own context attached.
    """
    records = _load_gold(config)
    record = next(
        (r for r in records if r.get("gold_id") == gold_id), None
    )
    if record is None or record.get("status") != "open":
        raise ValueError("That spot is not waiting for a call anymore.")
    if decision not in ("cut", "leave"):
        raise ValueError("Say cut it, or leave it.")
    start, end = record["word_start"], record["word_end"]
    kind = str(record.get("kind") or "held")
    changed = False
    message = None
    checkpoints.begin(config)
    if decision == "cut":
        operation = record.get("operation")
        if not isinstance(operation, dict):
            operation = {
                "type": "cut_words",
                "comment_id": "gold-verdict",
                "word_start": start,
                "word_end": end,
                "reason": "You looked at this spot, which the editor recommended removing, and said to cut it.",
                "confidence": 1.0,
                "model": "mock",
                "risk_tier": 3,
                "wonder_reviewed": False,
            }
        transcript = _load_json(str(config.job_dir / "transcript.json"))
        base_cuts = _effective_cuts(config)
        timeline = Timeline(transcript, base_cuts)
        ops = validate_operations([operation])
        profile = _load_optional_file(config.job_dir / "profile.json")
        verdict = verify_operations(ops, timeline, profile)[0]
        if verdict.status == "flag" and set(verdict.reasons) <= GOLD_SOFT_HOLDS:
            verdict.status = "apply"
        if (
            verdict.status == "reject"
            and _BOUNDARY_REJECT_REASONS & set(verdict.reasons)
            and operation.get("type") == "cut_words"
        ):
            # An approval never refuses on the word-boundary guard
            # (0.27.2): the creator said cut this spot, so the cut
            # widens to whole words and whole units at its edges
            # and is checked again, instead of bouncing back with
            # "That edit would split a word in half." Every other
            # safety rule still judges the widened cut exactly as
            # written, and a widened cut that still fails is
            # refused with its real reason.
            op_start = operation.get("word_start", start)
            op_end = operation.get("word_end", end)
            if isinstance(op_start, int) and isinstance(op_end, int):
                wide_start, wide_end = _expand_cut_span(
                    timeline, op_start, op_end
                )
                if (wide_start, wide_end) != (op_start, op_end):
                    wide_operation = dict(operation)
                    wide_operation["word_start"] = wide_start
                    wide_operation["word_end"] = wide_end
                    wide_ops = validate_operations([wide_operation])
                    wide_verdict = verify_operations(
                        wide_ops, timeline, profile
                    )[0]
                    if (
                        wide_verdict.status == "flag"
                        and set(wide_verdict.reasons) <= GOLD_SOFT_HOLDS
                    ):
                        wide_verdict.status = "apply"
                    if wide_verdict.status == "apply":
                        verdict = wide_verdict
        if verdict.status != "apply":
            return {
                "ok": True,
                "gold_id": gold_id,
                "changed": False,
                "message": _plain_toggle_message(verdict),
                "gold": _open_gold_spans(config),
                "suggestions": _rule_suggestions(config),
            }
        revised, _audit = apply_verdicts(base_cuts, timeline, [verdict])
        session = config.session_dir
        session.mkdir(parents=True, exist_ok=True)
        _write_json(session / "revised_cuts.json", revised)
        changed = True
        message = "Cut. Those words are out of the edit."
    else:
        message = "Left in. The editor will not offer this spot again."
    # A retake verdict settles the pair itself: leave means both
    # takes stay (the record a keep-both judgment writes), and cut
    # means the pair is decided the other way. Either way the pair
    # is never proposed again in this job.
    if record.get("pair_key"):
        retake_record = _load_retake_record(config)
        decisions = retake_record.setdefault("decisions", {})
        decisions[record["pair_key"]] = {
            "keep_both": decision == "leave",
            "source": "gold",
        }
        _save_retake_record(config, retake_record)
    # The verdict is a grade. It joins the same store the review
    # page writes, keyed by the gold span so re-grading can never
    # double-count it, with the note riding as the feedback text:
    # when the pattern surfaces as a rule, the creator's own words
    # are the context it surfaces with.
    entry = {
        "id": f"gold-grade-{gold_id}",
        "record_id": f"gold-grade-{gold_id}",
        "comment_id": "gold",
        "operation_type": (
            "cut_words" if decision == "cut" else "flag_for_review"
        ),
        "verdict": "worked",
        "pattern_key": f"gold:{kind}:{decision}",
        "pattern_description": _GOLD_PATTERN_DESCRIPTIONS.get(
            (kind, decision), _GOLD_PATTERN_DESCRIPTIONS[("held", decision)]
        ),
        "feedback_text": note,
        "text": note,
        "gold_id": gold_id,
        "source": "gold",
        "created_at": _utc_now(),
    }
    _merge_and_save_grades(config, [entry])
    if note and decision == "cut":
        # The note also lands on the edit the cut just made, in the
        # same store the transcript dots read, so it stays visible
        # on the words it was about.
        edit_id = f"edit-{start}-{end}"
        payload = _load_edit_notes(config)
        existing = payload["edits"].get(edit_id)
        if not isinstance(existing, dict):
            existing = {
                "edit_id": edit_id,
                "word_start": start,
                "word_end": end,
                "removed_text": str(record.get("text") or ""),
                "reason": str(record.get("reason") or ""),
                "source": "gold",
                "notes": [],
            }
            payload["edits"][edit_id] = existing
        existing.setdefault("notes", []).append(
            {
                "id": uuid.uuid4().hex[:12],
                "text": note,
                "created_at": _utc_now(),
            }
        )
        _save_edit_notes(config, payload)
    if not _is_test_video(config):
        _propose_decision_rule(config, kind, decision, note, gold_id)
    record["status"] = "cut" if decision == "cut" else "left"
    record["note"] = note or None
    record["decided_at"] = _utc_now()
    _save_gold(config, records)
    checkpoints.commit(
        config,
        # v0.17 (feedback item 8): the held spot's history
        # label drops "recommended to remove" with the rest
        # of that wording.
        "Spot left for your call, cut"
        if decision == "cut"
        else "Spot left for your call, left in",
    )
    try:
        _settle_thread_entry(
            config,
            {"kind": "held", "gold_id": gold_id, "status": "open"},
            {"status": "settled", "decision": decision},
        )
        if decision == "leave" and not _is_test_video(config):
            _append_why_thread(
                config,
                f"gold-grade-{gold_id}",
                "Why are you leaving that in?",
            )
    except Exception:
        pass
    result = {
        "ok": True,
        "gold_id": gold_id,
        "changed": changed,
        "message": message,
        "gold": _open_gold_spans(config),
        "suggestions": _rule_suggestions(config),
    }
    if decision == "leave" and not _is_test_video(config):
        # Leaving the words in is a choice the app can learn
        # from, so the page asks the why question about the
        # grade record this verdict just wrote. On a test video
        # the verdict wrote no record, so there is nothing to
        # ask about.
        result["why_prompt"] = {"entry_id": f"gold-grade-{gold_id}"}
    return result


# ---------------------------------------------------------------------------
# The Editor thread (v0.15, Batch E1): the conversation panel's record
#
# One thread per job, stored in the job's session folder beside the
# gold store (thread.json, in the same _load/_save shape). Entries
# are creator messages, editor replies, run summaries, held items,
# rule approvals, pinned comments, and why questions. Nothing is
# ever rewritten or deleted by the editor: a correction is a new
# entry, and a decision settles its entry in place (decision plus
# the time of day). Each entry carries the time of day it was said,
# as plain text ("9:02 AM"), never as a video time.
#
# Held items, pins, and why questions are not a parallel record:
# the gold store, the feedback store, and the grades store stay
# the truth, and the thread sync below mirrors their open state
# into entries (and settles thread entries when those stores
# settle), so deciding in the thread goes through the existing
# endpoints and deciding anywhere else still shows here.
# ---------------------------------------------------------------------------

_WHY_CHOICE_LABELS = {
    "story": "It matters to the story",
    "sound": "It sounds better this way",
    "say_it": "That is how I say it",
    "other": "Other",
}

THREAD_FILENAME = "thread.json"
THREAD_PENDING_FILENAME = "thread_pending.json"


def _time_of_day(when=None) -> str:
    """A wall-clock time of day in plain words: "9:02 AM"."""
    moment = when or datetime.now().astimezone()
    return moment.strftime("%I:%M %p").lstrip("0")


def _time_label_from_iso(value) -> str:
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return _time_of_day()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return _time_of_day(moment.astimezone())


def _load_thread(config: WebAppConfig) -> list:
    payload = _load_session_json(config, THREAD_FILENAME, {"entries": []})
    if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
        return [e for e in payload["entries"] if isinstance(e, dict)]
    return []


def _save_thread(config: WebAppConfig, entries: list) -> None:
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(config.session_dir / THREAD_FILENAME, {"entries": entries})


# The run labels whose Done bubble carries the run's own Undo
# (0.26): a front-door "Make the edit" run and an intake first
# edit each commit exactly one checkpoint under one of these
# labels, and that checkpoint is the run's saved pre-run state.
_DONE_ACTION_LABELS = ("Front door run", "First edit")


def _prepare_run_entry(config: WebAppConfig, entry: dict) -> None:
    """Undo wiring for one chat run entry, before it is stored.

    A run begins when its progress entry is first written (and
    again when a later run in the same entry names a fresh
    start, the Try again shape): the session splice as it
    stands then is the run's "before" video, saved with the
    run's entry id (see the run video snapshots). When the
    run's Done payload is stored, the splice the run built is
    saved as its "after" video, and the entry takes the run's
    checkpoint as its action, the same contract direction runs
    have always carried, so the one per-action move path
    (_thread_action_move) undoes and redoes the whole run.
    """
    entry_id = entry.get("id")
    if not entry_id:
        return
    if "chat_progress" in entry:
        progress = entry.get("chat_progress")
        progress = progress if isinstance(progress, dict) else {}
        existing = _find_thread_entry(config, entry_id)
        begins = True
        if existing is not None and (
            "chat_progress" in existing or "chat_done" in existing
        ):
            old = existing.get("chat_progress")
            old = old if isinstance(old, dict) else {}
            named = progress.get("started_at_ms")
            begins = bool(named) and named != old.get(
                "started_at_ms"
            )
        if begins:
            try:
                _snapshot_run_video(config, entry_id, "before")
            except Exception:
                pass
    if "chat_done" in entry:
        if not entry.get("action_id"):
            action_id, label = _current_action_id(
                config, _DONE_ACTION_LABELS
            )
            if action_id:
                entry["action_id"] = action_id
                entry["action_label"] = label
                entry.setdefault("undone", False)
        try:
            _snapshot_run_video(config, entry_id, "after")
        except Exception:
            pass


def _append_thread(config: WebAppConfig, entry: dict) -> dict:
    """Append one entry to this job's thread; returns the entry.

    (0.26, fix 6) A chat run entry, one carrying a chat_progress
    or chat_done payload, is upserted by id instead of appended:
    the progress entry written when Make the edit starts is the
    same entry its stage updates rewrite and the Done payload
    finally replaces, so a run leaves exactly one entry in the
    thread, never a trail of stage entries and never a stale
    progress entry beside the Done. While the entry is progress
    it keeps its first created_at (its place in the thread is
    where the run began); the Done replacement is said when the
    edit finishes, so it takes a fresh created_at. The server
    stamps the progress payload's started_at_ms from its own
    clock when a run entry is first written; a stage update
    that does not name a start keeps the original, and only an
    update that names one outright (Try again, a new run in the
    same entry) moves the start.

    A run entry written while a job is current is mirrored
    into the launch home's thread as well. The run starts at
    the front door, in the launch thread, and the job's thread
    takes over at adoption; without the mirror the launch copy
    would stay frozen at its last pre-adoption stage, and the
    next launch, which reads the launch thread before any job
    is open, would show a stale progress entry for a run that
    finished (or never learn that it did). With the mirror,
    both stores hold the run's true state under the same id.
    """
    _prepare_run_entry(config, entry)
    stored = _append_thread_one(config, entry)
    if (
        ("chat_progress" in entry or "chat_done" in entry)
        and config.has_job
    ):
        launch = WebAppConfig(None, launch_home=config.launch_home)
        if launch.session_dir != config.session_dir:
            _append_thread_one(launch, entry)
    return stored


def _append_thread_one(config: WebAppConfig, entry: dict) -> dict:
    """The upsert above, for one thread store."""
    with _THREAD_LOCK:
        entries = _load_thread(config)
        stored = dict(entry)
        stored.setdefault("id", uuid.uuid4().hex[:12])
        stored.setdefault("created_at", _utc_now())
        stored["time_label"] = _time_of_day()
        progress = stored.get("chat_progress")
        names_start = bool(
            isinstance(progress, dict) and progress.get("started_at_ms")
        )
        if isinstance(progress, dict) and not names_start:
            progress = dict(progress)
            progress["started_at_ms"] = int(time.time() * 1000)
            stored["chat_progress"] = progress
        if "chat_progress" in stored or "chat_done" in stored:
            for index, existing in enumerate(entries):
                if existing.get("id") != stored["id"]:
                    continue
                if not (
                    "chat_progress" in existing
                    or "chat_done" in existing
                ):
                    break
                merged = dict(existing)
                merged.update(stored)
                if "chat_done" in stored:
                    merged.pop("chat_progress", None)
                else:
                    merged["created_at"] = (
                        existing.get("created_at")
                        or stored["created_at"]
                    )
                    old = existing.get("chat_progress") or {}
                    new = dict(merged.get("chat_progress") or {})
                    if old.get("started_at_ms") and not names_start:
                        new["started_at_ms"] = old["started_at_ms"]
                    merged["chat_progress"] = new
                entries[index] = merged
                _save_thread(config, entries)
                return merged
        entries.append(stored)
        _save_thread(config, entries)
        return stored


def _settle_thread_entry(config: WebAppConfig, match: dict, updates: dict) -> None:
    """Settle the first open thread entry matching ``match``, in place."""
    with _THREAD_LOCK:
        entries = _load_thread(config)
        changed = False
        for entry in entries:
            if all(entry.get(k) == v for k, v in match.items()):
                entry.update(updates)
                entry["settled_time_label"] = _time_of_day()
                changed = True
                break
        if changed:
            _save_thread(config, entries)


def _claim_view(config: WebAppConfig, claim: dict) -> dict:
    """One claim line as the page shows it, timed fresh from the words.

    The printed time is worked out from where the claim's words sit
    in the current edit, so an old claim still points at the right
    words after a later run moves things. When every word the claim
    touched is out of the current edit, the line is marked out.
    """
    view = dict(claim)
    start, end = claim.get("word_start"), claim.get("word_end")
    if not (isinstance(start, int) and isinstance(end, int) and end > start):
        return view
    try:
        transcript = _load_json(str(config.job_dir / "transcript.json"))
        timeline = Timeline(transcript, _effective_cuts(config))
        words = transcript.get("words", [])
        snippet = " ".join(
            str(words[i].get("word") or "") for i in range(start, min(end, start + 6))
        )
        statuses = {timeline.word_status(i) for i in range(start, end)}
        out = statuses == {"cut"}
        at = _output_time_for_word(config, start)
        verb = claim.get("verb") or "Cut"
        count = end - start
        text = f"{verb} {count} word{'s' if count != 1 else ''} at {_format_clock(at)}"
        if snippet:
            text += f", {snippet}"
        if out:
            text += " — those words are out now"
        view["text"] = text
        view["out"] = bool(out)
        view["at_s"] = at
    except Exception:
        pass
    return view


def _claims_for_spans(spans: list) -> list:
    claims = []
    for span in spans:
        start, end = span.get("word_start"), span.get("word_end")
        if isinstance(start, int) and isinstance(end, int) and end > start:
            claims.append(
                {"verb": "Cut", "word_start": start, "word_end": end}
            )
    return claims


def _edit_span_keys(config: WebAppConfig) -> set:
    try:
        return {
            (s.get("word_start"), s.get("word_end")) for s in _edit_spans(config)
        }
    except Exception:
        return set()


def _new_edit_spans(config: WebAppConfig, before_keys: set) -> list:
    try:
        return [
            s
            for s in _edit_spans(config)
            if (s.get("word_start"), s.get("word_end")) not in before_keys
        ]
    except Exception:
        return []


def _sync_thread(config: WebAppConfig) -> list:
    """Mirror the stores into the thread, under the thread lock.

    The sync is itself a load-modify-save cycle on the thread
    store, so it takes the same lock as appends and settles;
    without it a sync that loaded before a settle could save the
    unsettled entries back over it.
    """
    with _THREAD_LOCK:
        return _sync_thread_locked(config)


def _sync_thread_locked(config: WebAppConfig) -> list:
    """Mirror the gold, feedback, and grades stores into the thread.

    Open gold spans gain held entries, pins gain pin entries in the
    order they were made, and entries whose underlying record has
    settled are settled here too. Auto-struck gold spans are NOT
    held: they are already cuts, and they appear only as claim
    lines in run summaries.
    """
    if not config.has_job:
        return _load_thread(config)
    entries = _load_thread(config)
    changed = False

    def has(kind, key, value):
        return any(
            e.get("kind") == kind and str(e.get(key)) == str(value)
            for e in entries
        )

    gold_records = {r.get("gold_id"): r for r in _load_gold(config)}
    for view in _open_gold_spans(config):
        gold_id = view.get("gold_id")
        if gold_id and not has("held", "gold_id", gold_id):
            record = gold_records.get(gold_id) or {}
            entries.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "kind": "held",
                    "status": "open",
                    "gold_id": gold_id,
                    "word_start": view.get("word_start"),
                    "word_end": view.get("word_end"),
                    "text": str(
                        record.get("reason")
                        or view.get("reason")
                        or "The editor is holding this spot for your call."
                    ),
                    "quote": str(view.get("text") or record.get("text") or ""),
                    "created_at": _utc_now(),
                    "time_label": _time_of_day(),
                }
            )
            changed = True
    for entry in entries:
        if entry.get("kind") != "held" or entry.get("status") != "open":
            continue
        record = gold_records.get(entry.get("gold_id"))
        if record and record.get("status") in ("cut", "left", "auto_cut"):
            entry["status"] = "settled"
            entry["decision"] = (
                "cut" if record.get("status") in ("cut", "auto_cut") else "leave"
            )
            entry["settled_time_label"] = _time_label_from_iso(
                record.get("decided_at")
            )
            changed = True

    feedback_path = _feedback_path(config)
    comments = []
    if feedback_path is not None:
        try:
            payload = _load_json(str(feedback_path))
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("comments"), list):
            comments = [c for c in payload["comments"] if isinstance(c, dict)]
    applied_ids = {
        str(op.get("comment_id"))
        for op in _audit_operations(config)
        if op.get("status") == "applied" and op.get("comment_id") is not None
    }
    def made_order(comment):
        try:
            return float(comment.get("id") or 0)
        except (TypeError, ValueError):
            return 0.0

    for comment in sorted(comments, key=made_order):
        comment_id = str(comment.get("id"))
        used = comment_id in applied_ids
        existing = next(
            (
                e
                for e in entries
                if e.get("kind") == "pin"
                and str(e.get("comment_id")) == comment_id
            ),
            None,
        )
        if existing is None:
            try:
                ts = float(comment.get("ts") or 0.0)
            except (TypeError, ValueError):
                ts = 0.0
            entries.append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "kind": "pin",
                    "status": "open",
                    "comment_id": comment_id,
                    "ts": ts,
                    "moment_line": f"At {_format_clock(ts)} in the video",
                    "text": str(comment.get("text") or ""),
                    "word_start": comment.get("word_start"),
                    "word_end": comment.get("word_end"),
                    "used": used,
                    "used_line": (
                        f"Used in the {_time_of_day()} run." if used else ""
                    ),
                    "created_at": _utc_now(),
                    "time_label": _time_of_day(),
                }
            )
            changed = True
        else:
            if used and not existing.get("used"):
                existing["used"] = True
                existing["used_line"] = f"Used in the {_time_of_day()} run."
                changed = True
            if str(comment.get("text") or "") != str(existing.get("text") or ""):
                if not existing.get("used"):
                    existing["text"] = str(comment.get("text") or "")
                    changed = True

    grades = {str(e.get("id")): e for e in _load_grades(config)}
    grades.update(
        {str(e.get("record_id")): e for e in _load_grades(config)}
    )
    for entry in entries:
        if entry.get("kind") != "why" or entry.get("status") != "open":
            continue
        grade = grades.get(str(entry.get("entry_id")))
        if grade and isinstance(grade.get("why"), dict):
            entry["status"] = "settled"
            entry["decision"] = str(grade["why"].get("choice") or "")
            entry["settled_time_label"] = _time_of_day()
            changed = True

    if changed:
        _save_thread(config, entries)
    return entries


def _why_detail_lines(config: WebAppConfig, entry: dict) -> list:
    """The editor's context for a why question (Batch E2, item 16).

    The thread entry itself carries only the question; the reason
    behind it lives on the grade record the question rides on
    (and, for a spot the creator left in, on the gold record).
    These plain lines are what the entry's info button expands:
    the words the question is about, why the editor flagged or
    cut them, and what the answer is for. An entry whose grade
    record is gone still names its words when its id carries
    the restored range.
    """
    lines: list = []
    entry_id = str(entry.get("entry_id") or "")
    grade = None
    for record in _load_grades(config):
        if entry_id and (
            str(record.get("id")) == entry_id
            or str(record.get("record_id")) == entry_id
        ):
            grade = record
            break
    start = end = None
    if grade is not None:
        gs, ge = grade.get("word_start"), grade.get("word_end")
        if isinstance(gs, int) and isinstance(ge, int) and ge > gs:
            start, end = gs, ge
    if start is None and entry_id.startswith("restore-grade-"):
        parts = entry_id[len("restore-grade-"):].split("-")
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            start, end = int(parts[0]), int(parts[1])
    quote = ""
    if start is not None and end is not None and config.job_dir is not None:
        try:
            transcript = _load_json(str(config.job_dir / "transcript.json"))
            words = transcript.get("words", [])
            quote = " ".join(
                str(words[i].get("word") or "")
                for i in range(start, min(end, len(words)))
            )
        except Exception:
            quote = ""
    gold = None
    if entry_id.startswith("gold-grade-"):
        gold_id = entry_id[len("gold-grade-"):]
        for record in _load_gold(config):
            if str(record.get("gold_id")) == gold_id:
                gold = record
                break
    if gold is not None:
        if gold.get("text"):
            lines.append(f'The words: "{gold["text"]}"')
        if gold.get("reason"):
            lines.append(
                f"Why the editor flagged them: {gold['reason']}"
            )
    elif quote:
        lines.append(f'The words you put back: "{quote}"')
    if grade is not None and grade.get("pattern_description"):
        description = str(grade["pattern_description"])
        if not description.endswith("."):
            description += "."
        lines.append(description)
    lines.append(
        "Your answer teaches the editor what to do next time "
        "words like these come up. It can become one of your "
        "rules, and a rule is only ever saved with your yes."
    )
    return lines


def _thread_entries_view(config: WebAppConfig) -> list:
    entries = _sync_thread(config)
    views = []
    for entry in entries:
        view = dict(entry)
        view["settled"] = entry.get("status") == "settled"
        # The settled v0.18 contract fields, present on every
        # entry the page receives: reply_to on creator messages,
        # action_id on Editor entries that report an undoable
        # action, undone on those entries. Entries stored before
        # the contract simply read as null / false.
        view["reply_to"] = entry.get("reply_to")
        view["action_id"] = entry.get("action_id")
        view["undone"] = bool(entry.get("undone", False))
        if entry.get("kind") == "why":
            # The page names the grade the way the why machinery
            # does; the stored entry carries it as entry_id.
            view["grade_id"] = entry.get("entry_id")
            view["answer_label"] = _WHY_CHOICE_LABELS.get(
                str(entry.get("decision") or ""), "saved"
            )
            view["detail"] = _why_detail_lines(config, entry)
        if isinstance(entry.get("claims"), list):
            if (
                entry.get("kind") in ("run", "editor")
                and not entry.get("record_answer")
            ):
                # v0.17 (feedback item 26): entries written
                # before the diversion can still carry per-cut
                # claims; the chat no longer shows them. They
                # live in the review suggestions instead.
                # Record answers keep theirs (marked
                # record_answer where they are appended):
                # there the claim is the answer.
                view["claims"] = []
            else:
                view["claims"] = [
                    _claim_view(config, c)
                    for c in entry["claims"]
                    if isinstance(c, dict)
                ]
        views.append(view)
    return views


def _waiting_count(config: WebAppConfig) -> int:
    """The one waiting count: held items plus unread replies.

    The same number in the panel header and the top bar. A held
    item is an open gold span (the gold store is the truth); an
    unread reply is an editor or run entry the page has not marked
    read yet, plus a why question still waiting for its answer.
    """
    held = len(_open_gold_spans(config)) if config.has_job else 0
    entries = _load_thread(config)
    unread = sum(
        1 for e in entries if e.get("unread") and e.get("kind") in ("editor", "run")
    )
    why_open = sum(
        1 for e in entries if e.get("kind") == "why" and e.get("status") == "open"
    )
    return held + unread + why_open


# -- Clean-up quick actions (v0.15, Batch E2, item 17) ---------------------
#
# The clean-up choices that can act after the first edit live in
# the Editor panel as a chip row. Each chip flips one stored
# choice through the same setting path its existing control uses:
# the three Remove choices are front-door settings (saved by
# _save_front_door_settings, the store the Edit settings toggles
# set), and the captions choice goes through the very function
# /api/captions-pref runs. A flip is recorded in the thread as a
# short editor entry, so the panel stays the record (spec §4).
# The intake pop-up's copies of the Remove choices are first-edit
# stage choices and stay where they are; there is no studio-sound
# control anywhere in the app, so no chip invents one.

_CLEANUP_LABELS = {
    "remove_retakes": "Remove retakes",
    "cut_silence": "Word gaps (silence)",
    "remove_fillers": "Remove filler words",
    "captions_hide_fillers": "Hide ums and uhs in captions",
}

_CLEANUP_WHEN = {
    "remove_retakes": "It counts from the next Make the edit run.",
    "cut_silence": "It counts from the next Make the edit run.",
    "remove_fillers": "It counts from the next Make the edit run.",
    "captions_hide_fillers": "The caption file matches it now.",
}


def _cleanup_state(config: WebAppConfig) -> dict:
    """The clean-up choices as they stand, for the chip row."""
    settings = _front_door_settings(config)
    return {
        "remove_retakes": bool(settings.get("remove_retakes")),
        "cut_silence": bool(settings.get("cut_silence")),
        "remove_fillers": bool(settings.get("remove_fillers")),
        "captions_hide_fillers": _captions_hide_fillers(config),
    }


def _apply_cleanup(config: WebAppConfig, key: str, on: bool) -> dict:
    """Flip one clean-up choice and note the change in the thread."""
    if key == "captions_hide_fillers":
        _set_captions_hide_fillers(config, bool(on))
    else:
        settings = _front_door_settings(config)
        settings[key] = bool(on)
        _save_front_door_settings(config, settings)
    line = (
        f"{_CLEANUP_LABELS[key]} is {'on' if on else 'off'} now. "
        f"{_CLEANUP_WHEN[key]}"
    )
    return _append_thread(
        config,
        {
            "kind": "editor",
            "status": "settled",
            "text": line,
            "lines": [],
            "claims": [],
        },
    )


def _thread_view(config: WebAppConfig) -> dict:
    entries = _thread_entries_view(config)
    has_edit = (
        (config.session_dir / "revised_cuts.json").is_file()
        or (config.session_dir / "operations_audit.json").is_file()
    )
    return {
        "ok": True,
        "editor_name": usual.load_editor_name(),
        "entries": entries,
        "waiting_count": _waiting_count(config),
        "has_edit": bool(has_edit),
        "cleanup": _cleanup_state(config),
        "review": _review_view(config),
    }


# ---------------------------------------------------------------------------
# Review suggestions (v0.17, feedback item 26)
#
# A run's summary used to carry one claim line per cut ("Cut 3
# words at 0:05..."), and a long run filled the Editor chat
# with them: Justin called it "a word-vomit list of things
# that it did" and asked for a conversation instead, with the
# review suggestions in their own place. The claims now land
# here, in the review store, one item per cut span, and the
# page shows them in a Review suggestions card with Bring
# back / Keep cut on each. The chat entry itself keeps only
# its summary sentence. Answers the editor gives from the
# record (why / what questions) keep their claims in the chat:
# there the claim IS the answer, not a work log, and the 0.16
# conversation test depends on it.
# ---------------------------------------------------------------------------

REVIEW_FILENAME = "review.json"


def _load_review(config: WebAppConfig) -> list:
    payload = _load_session_json(config, REVIEW_FILENAME, {"items": []})
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        return [i for i in payload["items"] if isinstance(i, dict)]
    return []


def _save_review(config: WebAppConfig, items: list) -> None:
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        config.session_dir / REVIEW_FILENAME, {"items": items}
    )


def _record_review_claims(config: WebAppConfig, claims) -> None:
    """File one run's cut claims as review suggestions.

    One item per cut span, keyed by the span, so the same cut
    recorded twice is one suggestion. Decided items keep their
    decision; a span cut again after a restore reopens.
    """
    fresh = [
        claim for claim in (claims or [])
        if isinstance(claim, dict)
        and isinstance(claim.get("word_start"), int)
        and isinstance(claim.get("word_end"), int)
        and claim["word_end"] > claim["word_start"]
    ]
    if not fresh:
        return
    items = _load_review(config)
    by_id = {item.get("id"): item for item in items}
    changed = False
    for claim in fresh:
        item_id = f"review-{claim['word_start']}-{claim['word_end']}"
        existing = by_id.get(item_id)
        if existing is None:
            items.append(
                {
                    "id": item_id,
                    "word_start": claim["word_start"],
                    "word_end": claim["word_end"],
                    "status": "open",
                    "created_at": _utc_now(),
                }
            )
            changed = True
        elif existing.get("status") == "restored":
            existing["status"] = "open"
            changed = True
    if changed:
        _save_review(config, items)


def _review_view(config: WebAppConfig) -> list:
    """The open review suggestions, as the page shows them.

    An item shows while every word it names is still out of
    the edit. Words brought back by any path close the item
    on their own; there is nothing to keep in step.
    """
    views = []
    for item in _load_review(config):
        if item.get("status") != "open":
            continue
        claim = _claim_view(
            config,
            {
                "verb": "Cut",
                "word_start": item.get("word_start"),
                "word_end": item.get("word_end"),
            },
        )
        if not claim.get("out"):
            continue
        quote = ""
        try:
            transcript = _load_json(
                str(config.job_dir / "transcript.json")
            )
            words = transcript.get("words", [])
            quote = " ".join(
                str(words[i].get("word") or "")
                for i in range(
                    item["word_start"],
                    min(item["word_end"], len(words)),
                )
            )
        except Exception:
            quote = ""
        views.append(
            {
                "id": item.get("id"),
                "word_start": item.get("word_start"),
                "word_end": item.get("word_end"),
                "text": claim.get("text") or "",
                "quote": quote,
                "at_s": claim.get("at_s"),
            }
        )
    return views


def _review_verdict(config: WebAppConfig, review_id, decision) -> dict:
    """Settle one review suggestion: bring it back, or keep the cut.

    Bring back runs the same restore the transcript's own
    Bring back runs (one checkpoint, so it is one undo step).
    Keep cut just closes the suggestion; the cut itself was
    already the editor's settled work.
    """
    items = _load_review(config)
    item = next(
        (i for i in items if i.get("id") == review_id), None
    )
    if item is None:
        raise ValueError("That suggestion is not on the list anymore.")
    if decision == "restore":
        result = _apply_word_range(
            config,
            int(item["word_start"]),
            int(item["word_end"]),
            "restore",
        )
        if result.get("stayed"):
            raise ValueError(
                "Some of those words could not come back: "
                + "; ".join(
                    str(stay.get("why") or "held")
                    for stay in result["stayed"][:2]
                )
            )
        item["status"] = "restored"
    elif decision == "keep":
        item["status"] = "kept"
    else:
        raise ValueError("Choose bring back, or keep the cut.")
    _save_review(config, items)
    return _thread_view(config)


def _append_why_thread(config: WebAppConfig, entry_id: str, question: str) -> None:
    """Write a pending why question into the thread (Batch E1).

    The question used to sit under the transcript header; it now
    lives in the thread as an editor entry whose answer pills are
    quick-action buttons there. The grades store stays the truth:
    answering goes through /api/why, and the sync settles this
    entry from that record.
    """
    if not entry_id:
        return
    with _THREAD_LOCK:
        entries = _load_thread(config)
        if any(
            e.get("kind") == "why" and str(e.get("entry_id")) == str(entry_id)
            for e in entries
        ):
            return
        _append_thread(
            config,
            {
                "kind": "why",
                "status": "open",
                "entry_id": entry_id,
                "text": question,
                "question": question,
            },
        )


def _append_run_summary_thread(
    config: WebAppConfig,
    lines: list,
    claims: list,
    auto_struck_count: int = 0,
) -> dict:
    """File a finished run's review claims. No recap bubble.

    0.26, fix 3: the long recap bubble is removed. It recounted
    the run in its own units (the pass's struck records, the
    gold spans alone) and disagreed with the Done bubble on the
    same edit, so the thread no longer carries it: the Done
    bubble is a run's only summary in the chat, and every number
    on it comes from the one count (_edit_counts). What this
    step still does, because it was never the bubble's job, is
    file the run's per-cut claims as review suggestions (see
    _record_review_claims, v0.17 feedback item 26). The recap
    lines and the struck count are accepted and dropped, so the
    run paths keep their shape; the numbers live in the job
    summary's counts and nowhere else. Returns a small record
    of what was filed, not a thread entry.
    """
    _record_review_claims(config, claims)
    return {"kind": "run", "claims": [], "posted": False}


# -- Message actions: reply to one message, per-action undo (v0.18) --------
#
# Builder B's slice of the conversation release. Two contracts:
#
# Reply: a creator message may carry ``reply_to``, the id of the
# one thread entry it answers. The server stores it on the
# creator entry, and when that entry reports an action (below),
# the reply is settled against that entry alone: questions are
# answered from that entry's own record, and undo or redo words
# act on that entry's action. A reply is never silently treated
# as a question about the whole thread.
#
# Actions: an Editor entry that reports a state-changing run
# carries ``action_id``: "checkpoint:<n>", where n is the index
# of the checkpoint that run committed. That checkpoint is the
# app's existing identity for the action, and it is the one that
# truly reverses it: the checkpoint slots hold the session state
# right before and right after the run (revised_cuts.json and
# the rest of STATE_FILES), thread.json is not one of the
# checkpointed files so the entry and its undone mark survive a
# restore, and restoring costs no model or transcription call.
# Entry kinds whose change is NOT held in a checkpoint (the
# clean-up chips, whose captions file and usual copy live
# outside the checkpointed files) get no action_id, so their
# menu honestly offers no Undo.
#
# A checkpoint restore is a step on a single road: it can only
# reverse the action that is still the newest step. Undoing an
# older action would also take back every later change, so that
# case is the honest fallback below: the Editor says so in the
# thread, in plain words, and offers the safe next step. It
# never performs a global undo and calls it per-action.

_RUN_ACTION_LABELS = ("Direction run", "Front door run", "First edit")


def _current_action_id(config: WebAppConfig, labels) -> tuple:
    """("checkpoint:<n>", label) for the current checkpoint when
    its label is one of ``labels``; (None, None) otherwise."""
    state = checkpoints.state(config)
    current = state.get("current")
    if not isinstance(current, int) or current < 0:
        return None, None
    for item in state.get("checkpoints") or []:
        if item.get("index") == current and item.get("label") in labels:
            return f"checkpoint:{current}", str(item["label"])
    return None, None


def _parse_action_id(entry: dict) -> Optional[int]:
    """The checkpoint index an entry's action_id names, or None."""
    raw = str(entry.get("action_id") or "")
    if not raw.startswith("checkpoint:"):
        return None
    try:
        return int(raw.split(":", 1)[1])
    except ValueError:
        return None


def _find_thread_entry(config: WebAppConfig, entry_id) -> Optional[dict]:
    if not entry_id:
        return None
    for entry in _load_thread(config):
        if str(entry.get("id")) == str(entry_id):
            return entry
    return None


def _set_thread_entry_fields(
    config: WebAppConfig, entry_id, updates: dict
) -> Optional[dict]:
    """Write fields onto one stored thread entry, in place."""
    with _THREAD_LOCK:
        entries = _load_thread(config)
        found = None
        for entry in entries:
            if str(entry.get("id")) == str(entry_id):
                entry.update(updates)
                found = entry
                break
        if found is not None:
            _save_thread(config, entries)
        return found


def _action_record(config: WebAppConfig, entry: dict) -> dict:
    """What one action entry's own record says, from saved state.

    The word spans the action itself cut or brought back, worked
    out by comparing the checkpoint slot right before the action
    with the slot the action committed (falling back to the job's
    own plan where a slot holds no revised plan). No provider is
    involved: this reads files the session already saved.
    """
    record = {
        "index": _parse_action_id(entry),
        "label": entry.get("action_label") or "",
        "cut_spans": [],
        "restored_spans": [],
    }
    index = record["index"]
    if index is None or not config.has_job:
        return record
    state = checkpoints.state(config)
    for item in state.get("checkpoints") or []:
        if item.get("index") == index:
            record["label"] = str(item.get("label") or record["label"])
    try:
        transcript = _load_json(str(config.job_dir / "transcript.json"))
        base_cuts = _load_json(str(config.job_dir / "cuts.json"))

        def cuts_at(slot_index: int) -> dict:
            if slot_index < 0:
                return base_cuts
            slot = (
                config.session_dir
                / checkpoints.HISTORY_DIR_NAME
                / f"{slot_index:04d}"
                / "revised_cuts.json"
            )
            if slot.is_file():
                return _load_json(str(slot))
            return base_cuts

        before = Timeline(transcript, cuts_at(index - 1))
        after = Timeline(transcript, cuts_at(index))
        words = transcript.get("words", [])
        for verb, was, is_now in (
            ("cut_spans", "kept", "cut"),
            ("restored_spans", "cut", "kept"),
        ):
            spans = []
            i = 0
            while i < len(words):
                if before.word_status(i) == was and after.word_status(i) == is_now:
                    start = i
                    while (
                        i < len(words)
                        and before.word_status(i) == was
                        and after.word_status(i) == is_now
                    ):
                        i += 1
                    spans.append({"word_start": start, "word_end": i})
                else:
                    i += 1
            record[verb] = spans
    except Exception:
        pass
    return record


def _settle_run_entry_after_move(
    config: WebAppConfig, entry: dict, undone: bool
) -> None:
    """Settle a run's Done entry after its checkpoint moved.

    The checkpoint restore has put the plan back; this puts the
    run's saved video back beside it (the "before" copy on an
    undo, the "after" copy on a redo) and refreshes the stored
    Done payload from the restored plan: its counts are read
    from _edit_counts on the plan as it now stands (the one
    count, fix 3), its length is the restored plan's length,
    and its video points at what the app now serves for this
    job (the splice again after a redo; the original video
    after undoing a first edit, whose before side had no
    edited video at all). The refreshed payload is written to
    the job's thread and, like the run entry itself, mirrored
    into the launch home's thread.
    """
    if not entry.get("chat_done"):
        return
    entry_id = entry.get("id")
    try:
        _restore_run_video(
            config, entry_id, "before" if undone else "after"
        )
    except Exception:
        pass
    payload = dict(entry.get("chat_done") or {})
    try:
        counts = _edit_counts(config)
    except Exception:
        counts = None
    if counts is not None:
        payload.update(
            {
                "retakes": counts["retakes_removed"],
                "retakes_set_aside": counts["retakes_set_aside"],
                "joins_checked": counts["joins_checked"],
                "joins_smooth": max(
                    0,
                    counts["joins_checked"] - counts["joins_failed"],
                ),
                "joins_failed": counts["joins_failed"],
                "words_cut": counts["words_cut"],
                "counts": counts,
                "review_count": counts["spots_to_review"],
            }
        )
        try:
            transcript = _load_json(
                str(config.job_dir / "transcript.json")
            )
            timeline = Timeline(transcript, _effective_cuts(config))
            payload["output_duration_s"] = round(
                timeline.output_duration, 4
            )
        except Exception:
            pass
    try:
        watching = _watching_state(config)
    except Exception:
        watching = {}
    if watching.get("kind") == "edited":
        payload["video_src"] = "/revised-review.mp4"
        payload.pop("video_caption", None)
    elif watching.get("name"):
        payload["video_src"] = "/video/" + str(watching["name"])
        payload["video_caption"] = str(watching["name"])
    if undone and watching.get("name"):
        # The undone bubble names the video as brought in with
        # its full length (0.27, fix 11): the original file the
        # app now serves again, probed from the file itself.
        try:
            brought = _probe_duration_s(
                config.job_dir / str(watching["name"])
            )
        except Exception:
            brought = None
        if brought:
            payload["brought_in_duration_s"] = round(float(brought), 3)
    updates = {"chat_done": payload}
    _set_thread_entry_fields(config, entry_id, updates)
    if config.has_job:
        launch = WebAppConfig(None, launch_home=config.launch_home)
        if launch.session_dir != config.session_dir:
            _set_thread_entry_fields(launch, entry_id, updates)


# The plan files in a checkpoint: the kept and cut words
# themselves, and the audit that describes how they got there.
# Everything else a checkpoint holds (notes, rules, gold
# verdicts that left the words alone, front-door choices) is
# work a whole-edit undo must not quietly take back when it
# walks past it (0.27.1, run-through finding 2).
_PLAN_STATE_FILES = (
    "revised_cuts.json",
    "operations.json",
    "operations_audit.json",
)


def _plan_at_checkpoint(config: WebAppConfig, index: int):
    """The edit plan as checkpoint ``index`` holds it.

    A slot with no revised plan in it stands for the job's own
    plan, the same reading _action_record gives a slot. None
    when neither can be read.
    """
    slot = (
        config.session_dir
        / checkpoints.HISTORY_DIR_NAME
        / f"{index:04d}"
        / "revised_cuts.json"
    )
    if slot.is_file():
        try:
            return json.loads(slot.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    if not config.has_job:
        return None
    try:
        return _load_json(str(config.job_dir / "cuts.json"))
    except Exception:
        return None


def _plan_unchanged_since(
    config: WebAppConfig, index: int, tip: int
) -> bool:
    """True when the plan at ``tip`` is still the plan at ``index``.

    The live plan must read the same too: the pointer and the
    session files are one state, and a mismatch means the
    record cannot be trusted for a pass-through move.
    """
    if not config.has_job:
        return False
    try:
        then = _plan_at_checkpoint(config, index)
        at_tip = _plan_at_checkpoint(config, tip)
        live = _effective_cuts(config)
    except Exception:
        return False
    return then is not None and then == at_tip and then == live


def _redo_walk_is_plan_only(
    config: WebAppConfig, index: int, tip: int
) -> bool:
    """True when redoing to ``index`` past later slots is safe.

    The slots past the action must hold the action's own plan
    (nothing later changed it), and the live plan must be the
    one the pointer stands on now (the slot before the
    action): the state the walk starts from. The live reading
    differs from the undo check, whose walk starts at the tip.
    """
    if not config.has_job:
        return False
    try:
        at_action = _plan_at_checkpoint(config, index)
        at_tip = _plan_at_checkpoint(config, tip)
        before = _plan_at_checkpoint(config, index - 1)
        live = _effective_cuts(config)
    except Exception:
        return False
    return (
        at_action is not None
        and at_action == at_tip
        and before == live
    )


def _walk_pointer_preserving(
    config: WebAppConfig, target: int, forward: bool
) -> bool:
    """Move the checkpoint pointer to ``target``, plan only.

    The pointer's own restores roll every state file to the
    target slot. When later work changed nothing but files
    beside the plan, those files are set aside first and put
    back after the walk, so the only thing the move changes is
    the plan and its audit, and the later work (a told rule, a
    note) stays exactly as it was.
    """
    session = config.session_dir
    stash = {}
    for name in checkpoints.STATE_FILES:
        if name in _PLAN_STATE_FILES:
            continue
        path = session / name
        try:
            stash[name] = path.read_bytes() if path.is_file() else None
        except OSError:
            stash[name] = None
    step = checkpoints.redo if forward else checkpoints.undo
    while True:
        current = checkpoints.state(config).get("current")
        if current == target:
            break
        if not isinstance(current, int) or step(config) is None:
            return False
        now = checkpoints.state(config).get("current")
        if forward and isinstance(now, int) and now > target:
            return False
        if not forward and isinstance(now, int) and now < target:
            return False
    for name, data in stash.items():
        path = session / name
        try:
            if data is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(data)
        except OSError:
            pass
    return checkpoints.state(config).get("current") == target


def _set_undo_note(
    config: WebAppConfig, entry: dict, line: Optional[str]
) -> None:
    """Put a move's refusal on its Done entry, or clear it.

    A refusal that only arrives as a separate chat message
    leaves the Done bubble looking untouched and unexplained,
    which is what the run-through filmed (finding 2): the
    bubble carries the line where the tap happened. Mirrored
    into the launch thread like the run settle, so both copies
    of the entry agree.
    """
    entry_id = entry.get("id")
    if not entry_id or not entry.get("chat_done"):
        return
    updates = {"undo_note": line or None}
    _set_thread_entry_fields(config, entry_id, updates)
    if config.has_job:
        launch = WebAppConfig(None, launch_home=config.launch_home)
        if launch.session_dir != config.session_dir:
            _set_thread_entry_fields(launch, entry_id, updates)


def _thread_action_move(config: WebAppConfig, entry_id, move: str) -> dict:
    """Undo or redo ONE thread entry's action, from saved state only.

    Returns {line, moved, fallback, entry}: the plain result line
    (also posted into the thread as an editor entry), whether the
    checkpoint pointer actually moved, whether the honest
    fallback fired instead, and the updated entry. This path
    calls checkpoints.undo / checkpoints.redo and nothing else
    that changes the edit: no provider, no transcription, $0.
    """
    if move not in ("undo", "redo"):
        raise ValueError("Say undo or redo.")
    entry = _find_thread_entry(config, entry_id)
    if entry is None:
        raise ValueError("That message is not in this conversation anymore.")
    index = _parse_action_id(entry)
    if index is None:
        raise ValueError(
            "That message is not tied to an action, so there is "
            "nothing to undo or redo on it."
        )
    state = checkpoints.state(config)
    labels = {
        item.get("index"): str(item.get("label") or "")
        for item in state.get("checkpoints") or []
    }
    current = state.get("current")
    label = entry.get("action_label") or labels.get(index) or "that change"
    road_replaced = index in labels and labels[index] != str(
        entry.get("action_label") or labels[index]
    )

    def note(line: str) -> dict:
        _append_thread(
            config,
            {
                "kind": "editor",
                "status": "settled",
                "text": line,
                "lines": [],
                "claims": [],
            },
        )
        # A settled outcome clears any refusal an earlier tap
        # left on the bubble.
        _set_undo_note(config, entry, None)
        return {
            "line": line,
            "moved": False,
            "fallback": False,
            "entry": _find_thread_entry(config, entry_id),
        }

    def fallback(line: str) -> dict:
        result = note(line)
        # The refusal also lands on the entry itself, so the
        # Done bubble says why where the tap happened, not only
        # in a separate message below it (finding 2). Set after
        # note(), which clears any older refusal.
        _set_undo_note(config, entry, line)
        result["entry"] = _find_thread_entry(config, entry_id)
        result["fallback"] = True
        return result

    if move == "undo":
        if entry.get("undone"):
            return note(f"The {label} change is already undone.")
        if current == index - 1 and not road_replaced:
            # A history step already took this action back; the
            # entry just had not been marked yet.
            _set_thread_entry_fields(config, entry_id, {"undone": True})
            _settle_run_entry_after_move(config, entry, True)
            return note(f"The {label} change is already undone.")
        stepped = False
        if current == index and not road_replaced:
            stepped = checkpoints.undo(config) is not None
            if not stepped:
                return fallback(
                    f"I cannot undo the {label} change on its own. "
                    "Its saved step is not there anymore. The safe "
                    "next step is to make the change again as a new "
                    "direction if you still want it."
                )
        elif (
            not road_replaced
            and isinstance(current, int)
            and current > index
            and _plan_unchanged_since(config, index, current)
        ):
            # Later entries exist, but none of them changed the
            # plan (a question, a deferred direction, a told
            # rule): the undo walks the pointer back over them
            # changing only the plan, and their work stays
            # (0.27.1, run-through finding 2).
            stepped = _walk_pointer_preserving(config, index - 1, False)
            if not stepped:
                return fallback(
                    f"I cannot undo the {label} change on its own. "
                    "Its saved step is not there anymore. The safe "
                    "next step is to make the change again as a new "
                    "direction if you still want it."
                )
        else:
            return fallback(
                f"I cannot undo the {label} change on its own. "
                "Later work came after it, and undoing it now "
                "would take that later work back too. The safe "
                "next step is to undo the newest change first, "
                "or step back through the history one change at "
                "a time."
            )
        _set_thread_entry_fields(config, entry_id, {"undone": True})
        _settle_run_entry_after_move(config, entry, True)
        try:
            _refresh_after_review(config)
        except Exception:
            pass
        result = note(
            f"Undone: the {label} change. Its words are back "
            "the way they were before it, and nothing was "
            "remade or spent."
        )
        result["moved"] = True
        return result
    # redo
    if not entry.get("undone"):
        return note(f"The {label} change is not undone, so there is nothing to redo.")
    if current == index and not road_replaced:
        _set_thread_entry_fields(config, entry_id, {"undone": False})
        _settle_run_entry_after_move(config, entry, False)
        return note(f"The {label} change is already back in the edit.")
    if current != index - 1 or road_replaced:
        return fallback(
            f"I cannot redo the {label} change on its own. "
            "Other work happened after it was undone, so its "
            "saved step no longer lines up. The safe next step "
            "is to make that change again as a new direction."
        )
    tip = max(labels) if labels else index
    if tip > index and _redo_walk_is_plan_only(config, index, tip):
        # The mirror of the undo walk: later entries sit past
        # this action's step and none of them changed the plan,
        # so the step forward changes only the plan too.
        stepped = _walk_pointer_preserving(config, index, True)
    else:
        stepped = checkpoints.redo(config) is not None
    if not stepped:
        return fallback(
            f"I cannot redo the {label} change on its own. "
            "Its saved step is not there anymore. The safe "
            "next step is to make the change again as a new "
            "direction if you still want it."
        )
    _set_thread_entry_fields(config, entry_id, {"undone": False})
    _settle_run_entry_after_move(config, entry, False)
    try:
        _refresh_after_review(config)
    except Exception:
        pass
    result = note(
        f"Redone: the {label} change is back in the edit, "
        "just as it was. Nothing was remade or spent."
    )
    result["moved"] = True
    return result


_UNDO_REPLY_WORDS = {"undo that", "undo it", "undo this", "take that back"}
_REDO_REPLY_WORDS = {"redo that", "redo it", "redo this", "put that back in"}


def _reply_scope_answer(config: WebAppConfig, text: str, target: dict) -> dict:
    """An editor reply built from the replied-to entry alone.

    Its text, its lines, its claims, and (when it reports an
    action) that action's own checkpoint record: never a global
    summary of the thread or the edit.
    """
    lowered = str(text or "").lower()
    target_text = str(target.get("text") or "").strip()
    target_lines = [str(ln) for ln in (target.get("lines") or []) if str(ln).strip()]
    if target.get("action_id"):
        record = _action_record(config, target)
        label = record["label"] or "that change"
        if any(word in lowered for word in _RECORD_WHY_WORDS):
            reason = ""
            for span in record["cut_spans"]:
                for span_view in _edit_spans(config):
                    if (
                        span_view.get("word_start") == span["word_start"]
                        and span_view.get("word_end") == span["word_end"]
                    ):
                        reason = str(span_view.get("reason") or "")
                        break
                if reason:
                    break
            opening = (
                f"From that message's own record: {reason}"
                if reason
                else (
                    f"From that message's own record: that was the "
                    f"{label} change, and it said: {target_text}"
                )
            )
            return {
                "text": opening,
                "lines": [],
                "claims": _claims_for_spans(record["cut_spans"]),
            }
        if any(word in lowered for word in _RECORD_WHAT_WORDS):
            if record["cut_spans"]:
                count = len(record["cut_spans"])
                opening = (
                    f"From that message's own record: the {label} "
                    f"change took out {count} spot"
                    f"{'s' if count != 1 else ''}."
                )
                return {
                    "text": opening,
                    "lines": [],
                    "claims": _claims_for_spans(record["cut_spans"]),
                }
            if record["restored_spans"]:
                count = len(record["restored_spans"])
                opening = (
                    f"From that message's own record: the {label} "
                    f"change brought back {count} spot"
                    f"{'s' if count != 1 else ''}."
                )
                return {
                    "text": opening,
                    "lines": [],
                    "claims": _claims_for_spans(record["restored_spans"]),
                }
            return {
                "text": (
                    f"From that message's own record: the {label} "
                    "change did not take words out or bring words "
                    "back on its own."
                ),
                "lines": [],
                "claims": [],
            }
        if "cost" in lowered:
            return {
                "text": (
                    "Costs are kept for the whole video, not per "
                    "message, so I cannot split that one change "
                    "out on its own. Ask me what the video has "
                    "cost and I will add it all up from the record."
                ),
                "lines": [],
                "claims": [],
            }
        lines = list(target_lines)
        if target.get("undone"):
            lines.append("That change is undone right now.")
        return {
            "text": (
                f"That message was the {label} change. It said: "
                f"{target_text}"
            ),
            "lines": lines,
            "claims": [],
        }
    lines = list(target_lines)
    claims = target.get("claims") or []
    return {
        "text": f"About that message: {target_text}" if target_text else "About that message: it carried no words of its own.",
        "lines": lines,
        "claims": claims if isinstance(claims, list) else [],
    }


def _scoped_reply(
    config: WebAppConfig, text: str, target: Optional[dict]
) -> Optional[dict]:
    """Settle a reply against its one target entry, or None.

    None means the message is not a scoped reply matter (a plain
    direction answering an entry with no action), and the caller
    runs the normal send order. When this returns a result, the
    reply has been fully settled here, at $0.
    """
    if target is None:
        return None
    normalized = re.sub(r"[^a-z ]", "", str(text or "").lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    has_action = bool(target.get("action_id"))
    if has_action and normalized in _UNDO_REPLY_WORDS | {"undo"}:
        result = _thread_action_move(config, target.get("id"), "undo")
        return {"kind": "action", "entry": _find_thread_entry(config, target.get("id")), **result}
    if has_action and normalized in _REDO_REPLY_WORDS | {"redo"}:
        result = _thread_action_move(config, target.get("id"), "redo")
        return {"kind": "action", "entry": _find_thread_entry(config, target.get("id")), **result}
    is_question = (
        "?" in str(text or "")
        or _is_record_question(text)
        or normalized.split(" ", 1)[0]
        in ("why", "what", "how", "did", "was", "were", "is", "can", "could", "should")
    )
    if has_action and not is_question:
        record = _action_record(config, target)
        label = record["label"] or "that change"
        entry = _append_thread(
            config,
            {
                "kind": "editor",
                "status": "settled",
                "text": (
                    f"That reply is tied to the {label} change on "
                    "its own. A new direction would change the "
                    "whole video, not just that change, so I did "
                    "not run it as part of this reply. You can "
                    "undo or redo that change from its menu, or "
                    "send the direction again without the reply "
                    "and I will run it on the whole video."
                ),
                "lines": [],
                "claims": [],
            },
        )
        return {"kind": "scoped", "entry": entry}
    if not is_question:
        return None
    answer = _reply_scope_answer(config, text, target)
    entry = _append_thread(
        config,
        {
            "kind": "editor",
            "record_answer": bool(answer.get("claims")),
            "status": "settled",
            "text": answer["text"],
            "lines": answer.get("lines") or [],
            "claims": answer.get("claims") or [],
        },
    )
    return {"kind": "scoped", "entry": entry}


# -- Record questions: answered from the record, at $0 ----------------------
#
# Asking never runs an edit and never calls a provider. The
# answers below are built only from the session's own records:
# the edit spans (each matched to the change that removed it),
# the audit operations behind them, and the session's cost
# entries. The chat route's tests assert the provider is not
# invoked on this path.

_RECORD_COST_WORDS = ("cost", "how much", "price")
_RECORD_WHY_WORDS = ("why did you", "why was", "why were", "why is that cut")
_RECORD_WHAT_WORDS = (
    "what did you take out",
    "what did you cut",
    "what have you taken out",
    "what have you cut",
    "what was taken out",
    "what was cut",
    "what did you change",
    "what have you changed",
    "what changed",
)

# Question shapes (0.25, finding 8): a message that ends in a
# question mark, or opens with a question word or an auxiliary
# ("why", "what", "how", "when", "did", "does", "can you tell
# me", "is it", ...), is a question. Questions are answered
# from the record and never run an edit, a render, or a charge;
# only a clear statement command runs work. "do" alone is not
# a question start: "do it" and "do the edit" are commands.
_QUESTION_STARTS = {
    "why", "what", "how", "when", "did", "does", "can", "could",
    "would", "should", "is", "are", "was", "were", "who", "where",
    "which", "have", "has",
}


def _is_chat_question(text: str) -> bool:
    """True when a chat message is phrased as a question."""
    raw = str(text or "").strip()
    if not raw:
        return False
    if raw.endswith("?"):
        return True
    words = re.sub(r"[^a-z ]", " ", raw.lower()).split()
    while words and words[0] in ("and", "so", "but", "ok", "okay", "well"):
        words.pop(0)
    if not words:
        return False
    if words[0] in _QUESTION_STARTS:
        return True
    if words[0] == "do" and len(words) > 1 and words[1] in ("you", "we"):
        return True
    if (
        words[0] == "tell"
        and len(words) > 2
        and words[1] == "me"
        and words[2] in _QUESTION_STARTS
    ):
        return True
    return False


def _is_record_question(text: str) -> bool:
    lowered = " " + str(text or "").lower().strip() + " "
    if any(word in lowered for word in _RECORD_WHY_WORDS):
        return True
    if any(word in lowered for word in _RECORD_WHAT_WORDS):
        return True
    if "cost" in lowered and (
        "what" in lowered or "how much" in lowered or "did" in lowered
    ):
        return True
    return False


def _latest_cut_span(config: WebAppConfig, spans: list) -> Optional[dict]:
    """The span the record's most recent cut decision removed.

    Decision order is the record's own order (0.26, fix 5): the
    audit's operations were appended as the passes ran, the
    session audit first and each later round after it, so the
    last applied cut operation is the latest cut decision the
    record holds. When no operation names a span, the last
    detected-repeat settlement in the gold store is the latest
    recorded decision. Returns None when the record holds no
    dated decision at all, and the caller asks instead of
    guessing.
    """
    if not spans:
        return None

    def covering(span, start, end):
        return (
            isinstance(start, int)
            and isinstance(end, int)
            and start < (span.get("word_end") or 0)
            and (span.get("word_start") or 0) < end
        )

    operations = _audit_operations(config)
    for op in reversed(operations):
        if op.get("status") != "applied":
            continue
        ranges = []
        if op.get("type") == "cut_words":
            ranges = [(op.get("word_start"), op.get("word_end"))]
        elif op.get("type") == "replace_take":
            ranges = [(op.get("cut_word_start"), op.get("cut_word_end"))]
        for start, end in ranges:
            for span in spans:
                if covering(span, start, end):
                    return span
    try:
        gold_records = _load_gold(config)
    except Exception:
        gold_records = []
    for record in reversed(gold_records):
        if not isinstance(record, dict):
            continue
        if record.get("status") != "auto_cut":
            continue
        for span in spans:
            if covering(
                span, record.get("word_start"), record.get("word_end")
            ):
                return span
    return None


# How close a cut must sit to the playhead to be the cut the
# creator is looking at (0.27, fix 11): within this many
# seconds of output time. A playhead parked far from every cut
# is not pointing at one, and the answer asks instead.
_WHY_NEAR_S = 10.0


def _nearest_cut_span(
    config: WebAppConfig, spans: list, playhead_s
) -> Optional[tuple]:
    """The recorded cut nearest a playhead position, or None.

    Returns (span, output_time) for the span whose place in the
    output sits within _WHY_NEAR_S of the position; None when
    no position arrived or no cut is near it.
    """
    if not spans or isinstance(playhead_s, bool):
        return None
    if not isinstance(playhead_s, (int, float)) or playhead_s < 0:
        return None
    scored = []
    for span in spans:
        try:
            at = _output_time_for_word(
                config, span.get("word_start") or 0
            )
        except Exception:
            continue
        scored.append((abs(at - float(playhead_s)), at, span))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0])
    dist, at, span = scored[0]
    if dist > _WHY_NEAR_S:
        return None
    return span, at


def _record_answer(
    config: WebAppConfig, text: str, playhead_s=None
) -> dict:
    """An editor reply built from the record alone. No provider call."""
    lowered = str(text or "").lower()
    if "cost" in lowered:
        entries = costs.load_session_entries(config.session_dir)
        total = sum(float(e.get("usd") or 0.0) for e in entries)
        by_feature: dict = {}
        for entry in entries:
            label = costs.row_label(
                entry.get("feature"), costs.entry_pool(entry)
            )
            by_feature[label] = by_feature.get(label, 0.0) + float(
                entry.get("usd") or 0.0
            )
        lines = [
            f"{label}: {costs.format_usd(usd)}."
            for label, usd in by_feature.items()
        ]
        opening = (
            f"From the record: this video's work has cost "
            f"{costs.format_usd(total)} so far."
            if entries
            else "From the record: nothing has been charged for this video yet."
        )
        return {"text": opening, "lines": lines, "claims": []}
    spans = []
    try:
        spans = _edit_spans(config)
    except Exception:
        spans = []
    picked = spans
    has_referent = False
    time_match = re.search(r"(\d+):(\d\d)", lowered)
    if time_match:
        has_referent = True
        target = int(time_match.group(1)) * 60 + int(time_match.group(2))
        scored = []
        for span in spans:
            try:
                at = _output_time_for_word(config, span.get("word_start") or 0)
            except Exception:
                at = 0.0
            scored.append((abs(at - target), span))
        scored.sort(key=lambda pair: pair[0])
        picked = [span for _dist, span in scored[:3]]
    elif "near the end" in lowered or "at the end" in lowered:
        has_referent = True
        try:
            transcript = _load_json(str(config.job_dir / "transcript.json"))
            total_words = len(transcript.get("words", []))
        except Exception:
            total_words = 0
        picked = [
            s
            for s in spans
            if total_words and (s.get("word_start") or 0) >= total_words * 0.75
        ] or spans[-3:]
    if not picked:
        return {
            "text": (
                "From the record: I have not taken anything out yet, "
                "so there is nothing to point at."
            ),
            "lines": [],
            "claims": [],
        }
    if any(word in lowered for word in _RECORD_WHY_WORDS):
        if not has_referent:
            # "Why did you cut that?" with nothing naming which
            # cut: answer about the cut nearest the playhead
            # (0.27, fix 11). The page sends the player's
            # position in the output with the question, and the
            # span whose place in the output is nearest it is
            # the one he is looking at. Fix 5's most-recent-cut
            # answer named a cut at the last second of his
            # video, so recency no longer picks the span. With
            # no position, or no cut near it, ask which one he
            # means, listing real cuts; never guess a cause.
            near = _nearest_cut_span(config, spans, playhead_s)
            if near is None:
                named = []
                for span in spans[:3]:
                    try:
                        at = _output_time_for_word(
                            config, span.get("word_start") or 0
                        )
                    except Exception:
                        at = 0.0
                    quote = str(span.get("removed_text") or "").strip()
                    if len(quote) > 60:
                        quote = quote[:57].rstrip() + "..."
                    named.append(f"\"{quote}\" at {_format_clock(at)}")
                return {
                    "text": (
                        "From the record: I cannot tell which cut "
                        "you mean, and I will not guess. The cuts "
                        "include " + "; ".join(named) + ". Ask me "
                        "about one of those, by its words or its "
                        "time, and I will tell you what the record "
                        "says."
                    ),
                    "lines": [],
                    "claims": _claims_for_spans(spans[:3]),
                }
            span, at = near
            quote = str(span.get("removed_text") or "").strip()
            if len(quote) > 80:
                quote = quote[:77].rstrip() + "..."
            opening = (
                f"From the record: the cut nearest where you "
                f"are paused was \"{quote}\" at about "
                f"{_format_clock(at)} in the edit. "
                f"{span.get('reason') or ''}"
            ).strip()
            if span.get("comment_text"):
                opening += f" The note on it said: {span['comment_text']}"
            return {
                "text": opening,
                "lines": [],
                "claims": _claims_for_spans([span]),
            }
        first = picked[0]
        opening = f"From the record: {first.get('reason') or 'Those words were removed by a run.'}"
        if first.get("comment_text"):
            opening += f" The note on it said: {first['comment_text']}"
        return {
            "text": opening,
            "lines": [],
            "claims": _claims_for_spans(picked),
        }
    opening = (
        f"From the record: {len(picked)} spot"
        f"{'s' if len(picked) != 1 else ''} "
        f"{'were' if len(picked) != 1 else 'was'} taken out."
    )
    return {"text": opening, "lines": [], "claims": _claims_for_spans(picked)}


def _general_record_answer(config: WebAppConfig, text: str) -> dict:
    """A question answered from the record's counts and rules.

    The fallback for any question the specific record answers
    and the coach do not claim: what the edit currently is, in
    the one count's numbers, or the saved rules when the
    question is about them. No provider, no run, no charge.
    """
    lowered = str(text or "").lower()
    if not config.has_job:
        return {
            "text": (
                "I have not edited a video yet, so there is no "
                "record to read. Drop a video in the chat and "
                "ask me again after the first edit."
            ),
            "lines": [],
            "claims": [],
        }
    if "rule" in lowered:
        try:
            statements = creator_rules.active_rule_statements(
                _rules_store(config)
            )
        except Exception:
            statements = []
        if statements:
            return {
                "text": (
                    f"From the record: {len(statements)} saved "
                    f"rule{'s' if len(statements) != 1 else ''} "
                    "are switched on for your videos:"
                ),
                "lines": [f"· {s}" for s in statements],
                "claims": [],
            }
        return {
            "text": (
                "From the record: no saved rules are switched "
                "on right now. Say \"add a rule\" and the rule "
                "to save one in your own words."
            ),
            "lines": [],
            "claims": [],
        }
    try:
        counts = _edit_counts(config)
        transcript = _load_json(str(config.job_dir / "transcript.json"))
        timeline = Timeline(transcript, _effective_cuts(config))
        total = len(timeline.words)
        opening = (
            f"From the record: the edit runs "
            f"{_format_clock(timeline.output_duration)} and keeps "
            f"{counts['words_kept']} of {total} words "
            f"({counts['words_cut']} cut). "
            f"{counts['retakes_removed']} retake"
            f"{'s' if counts['retakes_removed'] != 1 else ''} "
            f"removed, {counts['retakes_set_aside']} set aside "
            f"for your call, {counts['joins_checked']} joins "
            f"checked, and {counts['spots_to_review']} spot"
            f"{'s' if counts['spots_to_review'] != 1 else ''} "
            "waiting for review."
        )
    except Exception:
        opening = (
            "From the record: I could not read this video's "
            "editing record just now, so I will not guess."
        )
    return {"text": opening, "lines": [], "claims": []}


def _keys_answer_line(config: WebAppConfig) -> str:
    """Whether the keys are working, in one plain line.

    A real check, the same harmless list call the sign-in card
    runs: a saved key Meta accepts, a saved key it does not, or
    no key at all. No key value is ever part of the answer.
    """
    state = _signin_state(config)
    if not state.get("meta_signin_set"):
        return (
            "Your Meta key: none is saved right now, so edits "
            "run on this computer and typed directions wait."
        )
    accepted, _sentence = _check_signin(config)
    if accepted:
        line = "Your Meta key: saved, and Meta accepts it."
    else:
        line = (
            "Your Meta key: one is saved, but Meta does not "
            "accept it right now."
        )
    if state.get("elevenlabs_signin_set"):
        line += " Your ElevenLabs key is set too."
    return line


def _howto_answer_send(
    config: WebAppConfig, text: str
) -> Optional[dict]:
    """Answer a how-to question with the answer, at $0.

    "How do I review the edit? or check api keys?" used to fall
    through to the coach and come back as a list of suggested
    edits (0.27.1, P1 item 6). A question about how to do
    something is answered about that thing: how reviewing works
    (the walk on the Done message), and whether the keys are
    working (a real check). Both halves are answered when both
    are asked, in the fewest plain lines that hold them.
    """
    lowered = str(text or "").lower()
    words = re.sub(r"[^a-z ]", " ", lowered).split()
    wants_review = bool(re.search(r"\breview\b", lowered)) and (
        "how" in words
        or "where" in words
        or lowered.strip().endswith("?")
    )
    wants_keys = (
        "api key" in lowered
        or "apikey" in lowered
        or (
            bool(re.search(r"\bkeys?\b", lowered))
            and (
                "check" in words
                or "working" in words
                or "how" in words
            )
        )
    )
    if not (wants_review or wants_keys):
        return None
    lines = []
    if wants_review:
        spots = None
        if config.has_job:
            try:
                spots = _edit_counts(config)["spots_to_review"]
            except Exception:
                spots = None
        if spots:
            lines.append(
                f"To review: there "
                + ("is " if spots == 1 else "are ")
                + f"{spots} "
                + ("spot" if spots == 1 else "spots")
                + " to check. Tap \u201cReview the spots\u201d on "
                "the Done message and walk them one by one: "
                "Cut it, Keep it, Reply, or Skip on each."
            )
        else:
            lines.append(
                "To review: tap \u201cReview the spots\u201d on "
                "the Done message and walk them one by one: "
                "Cut it, Keep it, Reply, or Skip on each."
            )
    if wants_keys:
        lines.append(_keys_answer_line(config))
    entry = _append_thread(
        config,
        {
            "kind": "editor",
            "record_answer": True,
            "status": "settled",
            "text": lines[0],
            "lines": lines[1:],
            "claims": [],
        },
    )
    return {"kind": "record", "entry": entry}


def _settle_chat_question(
    config: WebAppConfig, text: str, playhead_s=None
) -> dict:
    """Settle one question-shaped message as an answer, at $0.

    Record questions read the edit spans and costs; coaching
    questions read the coach's local record; anything else gets
    the general record answer. This path never runs a pass,
    never builds a provider, and never charges (0.25, finding 8).
    The playhead position the page sent rides along so a bare
    "why did you cut that?" can answer about the cut nearest
    where he is paused (0.27, fix 11).
    """
    # A status question while an edit runs is answered from the
    # run record, ahead of everything else (0.27.1, P1 item 4):
    # its stage and clock are the answer, not the record's
    # counts. A how-to question is answered with the how-to
    # (item 6), ahead of the coach it used to fall into.
    status = _status_answer_send(config, text)
    if status is not None:
        return status
    howto = _howto_answer_send(config, text)
    if howto is not None:
        return howto
    if _is_record_question(text):
        answer = _record_answer(config, text, playhead_s=playhead_s)
        cap_text, cap_lines, cap_more = _cap_answer(
            answer["text"], answer.get("lines") or []
        )
        entry = _append_thread(
            config,
            {
                "kind": "editor",
                "record_answer": True,
                "status": "settled",
                "text": cap_text,
                "lines": cap_lines,
                "lines_more": cap_more,
                "claims": answer.get("claims") or [],
            },
        )
        return {"kind": "record", "entry": entry}
    focus = coach.coach_focus(text)
    if focus is not None:
        return _coach_answer_send(config, focus)
    answer = _general_record_answer(config, text)
    cap_text, cap_lines, cap_more = _cap_answer(
        answer["text"], answer.get("lines") or []
    )
    entry = _append_thread(
        config,
        {
            "kind": "editor",
            "record_answer": True,
            "status": "settled",
            "text": cap_text,
            "lines": cap_lines,
            "lines_more": cap_more,
            "claims": answer.get("claims") or [],
        },
    )
    return {"kind": "record", "entry": entry}


ESTIMATE_FILENAME = "estimate.json"


def _store_job_estimate(config: WebAppConfig, estimate: dict) -> None:
    """Keep one estimate per job (0.27.1, P1 item 8).

    The estimate priced at the offer is the job's number from
    then on: the offer card, the chat's spend prompt, and any
    later surface read this stored copy instead of pricing the
    job again, so the same run never wears two different
    numbers (his Mac run showed 156 cents on the card, 67 in
    the chat, and 85 on Done).
    """
    try:
        config.session_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            config.session_dir / ESTIMATE_FILENAME,
            {
                "total_usd": float(estimate.get("total_usd") or 0.0),
                "total_dollars": estimate.get("total_dollars")
                or costs.format_dollars(estimate.get("total_usd")),
                "surface": estimate.get("surface"),
                "stored_at": _utc_now(),
            },
        )
    except Exception:
        pass


def _load_job_estimate(config: WebAppConfig) -> Optional[dict]:
    payload = _load_session_json(config, ESTIMATE_FILENAME, {})
    if isinstance(payload, dict) and isinstance(
        payload.get("total_usd"), (int, float)
    ):
        return payload
    return None


def _direction_estimate_line(config: WebAppConfig, settings: dict) -> str:
    stored = _load_job_estimate(config)
    if stored is not None:
        return (
            "This edit usually costs "
            + costs.format_dollars(stored.get("total_usd"))
            + "."
        )
    try:
        transcript = _load_json(str(config.job_dir / "transcript.json"))
        timeline = Timeline(transcript, _effective_cuts(config))
        estimate = costs.estimate_run(
            timeline.source_duration,
            "front_door",
            settings,
            has_direction=True,
            spark_route=settings.get("spark_route", "meta"),
        )
        return (
            "This edit usually costs "
            + costs.format_dollars(estimate.get("total_usd"))
            + "."
        )
    except Exception:
        return ""


def _load_thread_pending(config: WebAppConfig) -> Optional[dict]:
    payload = _load_session_json(config, THREAD_PENDING_FILENAME, {})
    if isinstance(payload, dict) and payload.get("text"):
        return payload
    return None


def _save_thread_pending(config: WebAppConfig, pending: Optional[dict]) -> None:
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        config.session_dir / THREAD_PENDING_FILENAME, pending or {}
    )


def _save_pin_comment(config: WebAppConfig, text: str, moment: dict) -> dict:
    """Save one pinned comment into the feedback store.

    The same store the comments routes write, so the pin's margin
    markers, timeline dots, and direction runs all see it exactly
    as they see any other comment. An existing comment id updates
    that comment's words (the thread's pin edit); a new id appends.
    """
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    payload = {
        "video": config.job_dir.name if config.job_dir else "",
        "feedback_done": False,
        "comments": [],
        "overall_feedback": [],
    }
    existing_path = _feedback_path(config)
    if existing_path is not None:
        try:
            existing = _load_json(str(existing_path))
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict):
            payload["video"] = str(existing.get("video") or payload["video"])
            payload["feedback_done"] = bool(existing.get("feedback_done", False))
            if isinstance(existing.get("comments"), list):
                payload["comments"] = existing["comments"]
            payload["overall_feedback"] = _overall_notes(existing)
    try:
        ts = float(moment.get("ts") or 0.0)
    except (TypeError, ValueError):
        ts = 0.0
    comment_id = str(moment.get("comment_id") or "") or str(
        int(datetime.now().timestamp() * 1000)
    )
    stored = next(
        (c for c in payload["comments"] if str(c.get("id")) == comment_id),
        None,
    )
    if stored is None:
        stored = {
            "id": comment_id,
            "ts": round(ts, 1),
            "time_is_estimate": True,
            "fmt": str(moment.get("fmt") or "vertical"),
            "scope": "both",
            "text": text,
            "done": False,
        }
        if moment.get("word_start") is not None and moment.get("word_end") is not None:
            stored["word_start"] = int(moment["word_start"])
            stored["word_end"] = int(moment["word_end"])
            if moment.get("word_text"):
                stored["word_text"] = str(moment["word_text"])
        payload["comments"].append(stored)
    else:
        stored["text"] = text
    checkpoints.begin(config)
    _write_json(session / "feedback.json", payload)
    checkpoints.commit(config, "Saved notes")
    return stored


_CONFIRM_WORDS = {"yes", "run it", "yes run it", "go ahead", "yes please"}

# Conversation that is not a command (0.27.1, P1 item 5): only
# a clear command runs anything. A complaint ("this is a long
# time to wait"), a keep-going response to the editor ("no keep
# going"), and anything else that carries no edit words at all
# get one short plain line back, at no cost: they never reach
# the direction spend gate, which on his Mac run answered a
# complaint with a paid direction offer. Messages WITH edit
# words are direction and keep the gate exactly as before.
_CONTINUATION_PHRASES = {
    "keep going",
    "keep it going",
    "carry on",
    "go on",
    "go ahead and keep going",
}

_COMPLAINT_MARKERS = (
    "long time",
    "taking forever",
    "takes forever",
    "too slow",
    "so slow",
    "still waiting",
    "been waiting",
    "taking so long",
    "too long",
    "hurry up",
)


def _conversational_line(text: str) -> Optional[str]:
    """The one plain line a non-command message gets, or None.

    None means the message carries edit words and is direction:
    the normal spend gate decides it. Anything else (a
    continuation, a complaint, words with no edit in them at
    all) is conversation, and conversation runs nothing.
    """
    normalized = voice_commands.normalize(text)
    if not normalized:
        return None
    if normalized in _CONTINUATION_PHRASES or (
        normalized.startswith("no ")
        and normalized[3:] in _CONTINUATION_PHRASES
    ):
        return "Okay, carrying on. Nothing ran, and nothing was spent."
    if voice_commands.reads_as_direction(text):
        return None
    if any(marker in normalized for marker in _COMPLAINT_MARKERS):
        return "Heard you. Nothing ran, and nothing was spent."
    return (
        "I did not run anything, and nothing was spent. "
        "Tell me a change to make and I will price it first."
    )


def _run_chat_direction(config: WebAppConfig, text: str) -> dict:
    """Run one chat ask through the direction pass and reply in shape."""
    settings = _front_door_settings(config)
    before_keys = _edit_span_keys(config)
    before_duration = None
    after_duration = None
    try:
        timeline = Timeline(
            _load_json(str(config.job_dir / "transcript.json")),
            _effective_cuts(config),
        )
        before_duration = timeline.output_duration
    except Exception:
        pass
    checkpoints.begin(config)
    summary = _run_direction_pass(config, text, settings)
    # The final sweep (0.27.3) settles what the direction run
    # settled, inside the same checkpoint.
    _run_final_sweep(config)
    committed = checkpoints.commit(config, "Direction run")
    try:
        timeline = Timeline(
            _load_json(str(config.job_dir / "transcript.json")),
            _effective_cuts(config),
        )
        after_duration = timeline.output_duration
    except Exception:
        pass
    claims = _claims_for_spans(_new_edit_spans(config, before_keys))
    lines = [str(line) for line in (summary.get("lines") or []) if str(line).strip()]
    opening = lines[0] if lines else "Done."
    rest = lines[1:]
    held = len(_open_gold_spans(config))
    if held:
        rest.append(
            f"{held} spot{'s' if held != 1 else ''} "
            f"{'are' if held != 1 else 'is'} waiting for your call below."
        )
    cost_total = 0.0
    if summary.get("usage"):
        entries = _direction_cost_entries(summary["usage"])
        _account_costs(config, entries)
        cost_total = sum(float(e.get("usd") or 0.0) for e in entries)
    changed = bool(claims) or (
        before_duration is not None
        and after_duration is not None
        and abs(after_duration - before_duration) > 0.05
    )
    if changed or cost_total > 0:
        closing = ""
        if before_duration is not None and after_duration is not None:
            closing = (
                f"Your video is now {_format_clock(after_duration)}, "
                f"was {_format_clock(before_duration)}."
            )
        if cost_total > 0 or summary.get("ran"):
            closing = (closing + " " if closing else "") + (
                f"Cost {costs.format_usd(cost_total)}."
            )
        if closing:
            rest.append(closing.strip())
    # v0.17 (feedback item 26): same diversion as the run
    # summaries: the per-cut claims are review suggestions
    # now, not chat lines.
    _record_review_claims(config, claims)
    opening, rest, more_lines = _cap_answer(opening, rest)
    entry_payload = {
        "kind": "editor",
        "status": "settled",
        "text": opening,
        "lines": rest,
        "lines_more": more_lines,
        "claims": [],
    }
    # Per-action undo (v0.18, Builder B): a run that actually
    # changed the edit reports the checkpoint it just committed
    # as its action, so its menu can undo or redo that one run.
    # A run that changed nothing gets no action: there is
    # nothing to take back.
    if changed:
        current = committed.get("current") if isinstance(committed, dict) else None
        if isinstance(current, int) and current >= 0:
            entry_payload["action_id"] = f"checkpoint:{current}"
            entry_payload["action_label"] = "Direction run"
            entry_payload["undone"] = False
    entry = _append_thread(config, entry_payload)
    return {"kind": "direction", "entry": entry, "summary": summary}


# -- Chat answers stay short (0.27.1, P1b item 7) --------------------
#
# A chat answer is at most three lines by default: the lead
# line plus two more. His recording showed a six-item coach
# list landing as one wall of text. Anything past three lines
# waits on the entry as ``lines_more`` and comes back whole
# when he says "more". A draft he is reading to decide on is
# not an answer (the teach summary, the Done message, the
# review walk) and is never capped here.

def _cap_answer(text, lines):
    """(text, lines, more): one answer, capped at three lines."""
    units = []
    if text:
        units.append(str(text))
    units.extend(
        str(line) for line in (lines or []) if str(line).strip()
    )
    if len(units) <= 3:
        return (text or ""), list(lines or []), []
    shown = units[:3]
    lead = shown[0] + " Say \u201cmore\u201d for the rest."
    return lead, shown[1:], units[3:]


_MORE_ASK_RE = re.compile(
    r"^(more|show more|show me more|see more|the rest|go on|"
    r"continue|list them|what else|and)$"
)


def _is_more_ask(text) -> bool:
    cleaned = re.sub(r"[^a-z ]", " ", str(text or "").lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return bool(_MORE_ASK_RE.match(cleaned))


def _more_answer_send(config: WebAppConfig) -> Optional[dict]:
    """Hand back the lines a capped answer held, when asked.

    Only the newest entry still holding lines counts, and
    expanding spends them (the stored entry is cleared), so a
    second "more" falls through to the normal order instead
    of repeating the same lines.
    """
    entries = _load_thread(config)
    for index in range(len(entries) - 1, -1, -1):
        held = entries[index].get("lines_more")
        if not isinstance(held, list):
            continue
        more = [
            str(line) for line in held if str(line).strip()
        ]
        if not more:
            continue
        entries[index]["lines_more"] = []
        _save_thread(config, entries)
        new_entry = _append_thread(
            config,
            {
                "kind": "editor",
                "status": "settled",
                "text": more[0],
                "lines": more[1:],
                "lines_more": [],
                "claims": [],
            },
        )
        return {"kind": "record", "entry": new_entry}
    return None


# -- Coach mode (v0.18, Builder C) -------------------------------------------
#
# A coach question in the thread is answered from the local record
# alone (the coach module): the transcript, the current cuts plan,
# the open held spans, and the retake record. No provider is built
# and nothing is transcribed on this path, so the list costs $0.
# The reply entry carries its suggestions, each with a stable id
# and the direction words that would run it. A follow-up ("do it",
# "do the first one", "do the second one", or a send naming a
# suggestion id) maps one suggestion into _run_chat_direction, the
# normal direction path: the creator's "do it" IS the plain yes
# for that one suggestion, so it runs the way a confirmed
# direction runs, with that path's normal cost accounting.


def _coach_records(config: WebAppConfig):
    """The local records the coach reads, or None with no job."""
    if not config.has_job:
        return None
    try:
        transcript = _load_json(str(config.job_dir / "transcript.json"))
        cuts = _effective_cuts(config)
    except Exception:
        return None
    try:
        held = _open_gold_spans(config)
    except Exception:
        held = []
    try:
        retake_record = _load_retake_record(config)
        recorded_pairs = [
            pair
            for pair in retake_record.get("pairs", [])
            if isinstance(pair, dict)
        ]
    except Exception:
        recorded_pairs = []
    profile = _load_optional_file(config.job_dir / "profile.json")
    return transcript, cuts, held, recorded_pairs, profile


def _last_coach_suggestions(config: WebAppConfig) -> list:
    """The suggestions on the newest coach reply in the thread."""
    for entry in reversed(_load_thread(config)):
        suggestions = entry.get("suggestions")
        if (
            entry.get("coach")
            and isinstance(suggestions, list)
            and suggestions
        ):
            return [s for s in suggestions if isinstance(s, dict)]
    return []


def _coach_note(config: WebAppConfig, line: str) -> dict:
    entry = _append_thread(
        config,
        {
            "kind": "editor",
            "status": "settled",
            "text": line,
            "lines": [],
            "claims": [],
            "coach": True,
            "suggestions": [],
        },
    )
    return {"kind": "coach", "entry": entry, "suggestions": []}


def _coach_followup(config: WebAppConfig, text: str) -> Optional[dict]:
    """Settle a "do it" style follow-up against the coach list.

    Returns None when the words are not a follow-up, or when no
    coach list is waiting (the words then take the normal send
    order, exactly as they did before coach mode existed).
    """
    suggestions = _last_coach_suggestions(config)
    if not suggestions:
        return None
    chosen = None
    suggestion_id = coach.followup_suggestion_id(text)
    if suggestion_id:
        chosen = next(
            (s for s in suggestions if s.get("id") == suggestion_id),
            None,
        )
        if chosen is None:
            return _coach_note(
                config,
                "That suggestion is not on the coach list I last "
                "gave you. Ask me what I would do to make this "
                "better and I will build the list again.",
            )
    else:
        index = coach.followup_index(text)
        if index is None:
            return None
        if not 1 <= index <= len(suggestions):
            return _coach_note(
                config,
                f"There are only {len(suggestions)} suggestions on "
                "the coach list, so there is no number "
                f"{index} to run. Say \"do it\" for the first one, "
                "or name the one you want.",
            )
        chosen = suggestions[index - 1]
    result = _run_chat_direction(
        config, str(chosen.get("direction") or chosen.get("text") or "")
    )
    result["coach_suggestion_id"] = chosen.get("id")
    result["suggestion"] = chosen
    return result


def _coach_answer_send(config: WebAppConfig, focus: str) -> dict:
    """Answer one coach question from the local record, at $0."""
    records = _coach_records(config)
    if records is None:
        return _coach_note(
            config,
            "Open a video first and I will coach from its own "
            "words and its editing record.",
        )
    transcript, cuts, held, recorded_pairs, profile = records
    reply = coach.coach_reply(
        transcript,
        cuts,
        focus=focus,
        held=held,
        recorded_retake_pairs=recorded_pairs,
        profile=profile,
    )
    cap_text, cap_lines, cap_more = _cap_answer(
        reply["text"], reply.get("lines") or []
    )
    entry = _append_thread(
        config,
        {
            "kind": "editor",
            "status": "settled",
            "text": cap_text,
            "lines": cap_lines,
            "lines_more": cap_more,
            "claims": [],
            "coach": True,
            "coach_focus": focus,
            "suggestions": reply.get("suggestions") or [],
        },
    )
    return {
        "kind": "coach",
        "entry": entry,
        "suggestions": reply.get("suggestions") or [],
        "focus": focus,
    }


def _coach_send_if_coach(config: WebAppConfig, text: str) -> Optional[dict]:
    """The coach intent check for one thread message, or None.

    Follow-ups first (they name a suggestion from the last coach
    reply), then coaching questions generally. A direction stays
    a direction: this returns None for anything that is not a
    question asking for advice about the video, and for any
    follow-up when no coach list is waiting.
    """
    followup = _coach_followup(config, text)
    if followup is not None:
        return followup
    focus = coach.coach_focus(text)
    if focus is None:
        return None
    return _coach_answer_send(config, focus)


def _queue_notes(raw) -> list:
    """Normalize the queued notes a Send to edit carries.

    Each note is one carried selection: its edited-clock moment,
    the word range, and the words themselves. The moment line is
    printed the way the thread's pins print it, from the moment
    the page carried (the edited clock), never recomputed here.
    """
    notes = []
    if not isinstance(raw, list):
        return notes
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            ts = float(item.get("ts") or 0.0)
        except (TypeError, ValueError):
            ts = 0.0
        word_start = item.get("word_start")
        word_end = item.get("word_end")
        notes.append(
            {
                "ts": round(ts, 1),
                "fmt": str(item.get("fmt") or "vertical"),
                "word_start": (
                    int(word_start) if isinstance(word_start, int) else None
                ),
                "word_end": (
                    int(word_end) if isinstance(word_end, int) else None
                ),
                "word_text": str(item.get("word_text") or ""),
                "moment_line": f"At {_format_clock(ts)} in the video",
            }
        )
    return notes


def _compose_queue_ask(text: str, notes: list) -> str:
    """One direction ask carrying the covering message and every
    queued moment, in the order they were queued."""
    lines = []
    body = str(text or "").strip()
    if body:
        lines.append(body)
        lines.append("Notes on moments in the video:")
    else:
        lines.append("Please look at these moments in the video:")
    for note in notes:
        line = "- " + note["moment_line"]
        if note["word_text"]:
            line += f': "{note["word_text"]}"'
        lines.append(line)
    return "\n".join(lines)


def _save_queue_pins(config: WebAppConfig, notes: list, text: str) -> list:
    """Save each queued note as a pinned comment in the feedback
    store, the way a sent moment pin is saved, so the direction
    run sees every note on its moment. The covering message is
    each pin's words; with no covering message the pin carries
    its own moment line."""
    stored = []
    cover = str(text or "").strip()
    for note in notes:
        pin_text = cover or (
            note["moment_line"]
            + (f': "{note["word_text"]}"' if note["word_text"] else "")
        )
        stored.append(_save_pin_comment(config, pin_text, note))
    return stored


def _chat_send_queue(
    config: WebAppConfig, text: str, notes: list, reply_to=None,
    playhead_s=None,
) -> dict:
    """One Send to edit (Batch E2, item 15): the queued moment
    notes plus the covering message go as a SINGLE direction ask
    through the same send path a typed ask takes — one run, one
    editor reply, and the spend gate unchanged. The creator
    entry in the thread carries the queue, so it lists each
    queued moment; each note also lands as a pinned comment
    when the run goes ahead."""
    composed = _compose_queue_ask(text, notes)
    _append_thread(
        config,
        {
            "kind": "creator",
            "status": "settled",
            "text": text,
            "queue": notes,
            "reply_to": reply_to,
        },
    )
    if _is_chat_question(text):
        # A question covering queued spots is answered, not run
        # (0.25, finding 8): the notes stay queued on the page
        # and no pin is saved and nothing is spent here.
        result = _settle_chat_question(config, text, playhead_s=playhead_s)
        result["queue"] = notes
        return result
    if config.signin_key():
        settings = _front_door_settings(config)
        estimate = _direction_estimate_line(config, settings)
        _save_thread_pending(
            config, {"text": composed, "queue": notes, "cover": text}
        )
        entry = _append_thread(
            config,
            {
                "kind": "editor",
                "status": "settled",
                "text": (
                    "That needs a direction run, which spends money. "
                    + (estimate + " " if estimate else "")
                    + "Say \u201crun it\u201d and I will, or change the ask."
                ),
                "lines": [],
                "claims": [],
            },
        )
        return {"kind": "needs_confirmation", "entry": entry, "queue": notes}
    _save_queue_pins(config, notes, text)
    result = _run_chat_direction(config, composed)
    result["queue"] = notes
    return result


def _chat_send(
    config: WebAppConfig,
    text: str,
    moment: Optional[dict] = None,
    queue=None,
    reply_to=None,
    playhead_s=None,
) -> dict:
    """Settle one message sent in the Editor panel.

    Order: a moment attachment makes the message a pinned comment;
    a pending spend confirmation is settled; the voice-command
    matcher runs exactly as it does for spoken words; a question
    about the record is answered from the record at $0; anything
    else is direction. A direction ask that would spend money is
    not run on the first send: the reply names the estimate and
    the run waits for a plain yes. A message carrying queued
    moment notes (Send to edit, Batch E2) goes as one direction
    ask composed from the queue and the covering message, under
    the same spend gate.
    """
    text = str(text or "").strip()
    notes = _queue_notes(queue)
    if not text and not notes:
        raise ValueError("Write or say something first.")
    # Reply to one message (v0.18, Builder B): the target entry
    # is resolved once, up front. The creator entry below stores
    # its id as reply_to, and _scoped_reply settles the message
    # against that entry alone when the entry reports an action
    # or the message asks about it. An id that names no entry is
    # dropped, so a stale reply can never scope a send wrongly.
    reply_target = _find_thread_entry(config, reply_to) if reply_to else None
    if reply_target is None:
        reply_to = None
    if notes and not moment:
        return _chat_send_queue(
            config, text, notes, reply_to=reply_to, playhead_s=playhead_s
        )
    if not text:
        raise ValueError("Write or say something first.")
    if moment:
        # The creator's words are a thread message in their
        # own right; the pin is the same words pinned to the
        # moment. A record question asked on a moment is
        # still answered from the record at $0; a direction
        # ask rides as the pinned comment, the way comments
        # have always fed direction runs.
        _append_thread(
            config,
            {
                "kind": "creator",
                "status": "settled",
                "text": text,
                "reply_to": reply_to,
            },
        )
        stored = _save_pin_comment(config, text, moment)
        entry = next(
            (
                e
                for e in _sync_thread(config)
                if e.get("kind") == "pin"
                and str(e.get("comment_id")) == str(stored.get("id"))
            ),
            None,
        )
        scoped = _scoped_reply(config, text, reply_target)
        if scoped is not None:
            scoped["pin_entry"] = entry
            scoped["comment"] = stored
            return scoped
        if _is_chat_question(text):
            # A question asked on a moment still gets an answer,
            # never a run (0.25, finding 8). The pin above is
            # saved, as it is for any moment message.
            result = _settle_chat_question(
                config, text, playhead_s=playhead_s
            )
            result["pin_entry"] = entry
            result["comment"] = stored
            return result
        return {"kind": "pin", "entry": entry, "comment": stored}
    _append_thread(
        config,
        {
            "kind": "creator",
            "status": "settled",
            "text": text,
            "reply_to": reply_to,
        },
    )
    normalized = re.sub(r"[^a-z ]", "", text.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    pending = _load_thread_pending(config)
    if pending and normalized in _CONFIRM_WORDS:
        _save_thread_pending(config, None)
        pending_notes = _queue_notes(pending.get("queue"))
        if pending_notes:
            _save_queue_pins(
                config, pending_notes, str(pending.get("cover") or "")
            )
            result = _run_chat_direction(config, str(pending["text"]))
            result["queue"] = pending_notes
            return result
        return _run_chat_direction(config, str(pending["text"]))
    if pending and normalized in {"no", "never mind", "cancel"}:
        _save_thread_pending(config, None)
        entry = _append_thread(
            config,
            {
                "kind": "editor",
                "status": "settled",
                "text": "Okay, I did not run it. Nothing was spent.",
                "lines": [],
                "claims": [],
            },
        )
        return {"kind": "cancelled", "entry": entry}
    # A status ask settles from the run record ahead of
    # everything else (0.27.1, P1 item 4): question-shaped ones
    # are caught again in _settle_chat_question below, but a
    # plain "I need you to update me" is not question-shaped,
    # and it must never reach the direction gate while an edit
    # is running (item 5: it asked for a paid direction run on
    # his Mac run).
    status = _status_answer_send(config, text)
    if status is not None:
        return status
    # A reply to one message settles against that message alone
    # (v0.18, Builder B), ahead of the voice matcher: "undo" as a
    # reply means that message's action, not the newest history
    # step. _scoped_reply returns None when the message is a
    # plain direction answering an entry with no action, and the
    # normal order below runs it.
    scoped = _scoped_reply(config, text, reply_target)
    if scoped is not None:
        return scoped
    # "More" after a capped answer hands back the held lines
    # (0.27.1, P1b item 7), ahead of every other reading of
    # the word.
    if _is_more_ask(text):
        more = _more_answer_send(config)
        if more is not None:
            return more
    # A coach follow-up ("do it", "do the first one") settles
    # here, ahead of the voice matcher: those exact words can
    # also read as a voice command, but when a coach list is
    # waiting they name a suggestion on it. With no coach list
    # waiting this returns None and the normal order runs, word
    # for word as before (v0.18, Builder C).
    coach_followup = _coach_followup(config, text)
    if coach_followup is not None:
        return coach_followup
    # An offer the chat made settles here (0.27.3, chunk B
    # item 8): a dead-space note gets its measured answer and
    # tap chip, and a bare "run it" runs the newest open
    # offer. Ahead of the question reading, because "there's
    # dead space at 2:10" is a note to act on, not a question
    # about the record.
    offer = _offer_send(config, text)
    if offer is not None:
        return offer
    # Questions are not commands (0.25, finding 8): anything
    # phrased as a question is answered from the record here,
    # ahead of the voice matcher (whose phrases a question can
    # echo: "did you undo that?" must not undo anything) and
    # ahead of the spend gate and the direction path.
    if _is_chat_question(text):
        return _settle_chat_question(config, text, playhead_s=playhead_s)
    match = voice_commands.match_voice_command(text)
    decision = match.get("decision")
    if decision in ("command", "ask", "unavailable"):
        if decision == "command":
            label = voice_commands.command_label(
                match["command"], match.get("seconds")
            )
            record = f'Heard: "{text}". Ran command: {label}.'
        elif decision == "ask":
            labels = [
                voice_commands.COMMAND_LABELS.get(c, c)
                for c in list(match.get("candidates") or [])[:2]
            ]
            record = (
                f'Heard: "{text}". I am not sure which you meant: '
                f"{' or '.join(labels)}? Nothing ran."
            )
        else:
            record = (
                f'Heard: "{text}". '
                + voice_commands.CENTER_UNAVAILABLE_SENTENCE
            )
        _record_voice_line(config, record)
        entry = _append_thread(
            config,
            {
                "kind": "editor",
                "status": "settled",
                "text": record,
                "lines": [],
                "claims": [],
            },
        )
        return {
            "kind": "command",
            "entry": entry,
            "decision": decision,
            "command": match.get("command"),
            "seconds": match.get("seconds"),
            "candidates": match.get("candidates"),
            "record": record,
        }
    if _is_record_question(text):
        answer = _record_answer(config, text, playhead_s=playhead_s)
        entry = _append_thread(
            config,
            {
                "kind": "editor",
                # Marked like the pin path's record answer:
                # its claims are the answer, not a work log.
                "record_answer": True,
                "status": "settled",
                "text": answer["text"],
                "lines": answer.get("lines") or [],
                "claims": answer.get("claims") or [],
            },
        )
        return {"kind": "record", "entry": entry}
    # Coach mode (v0.18, Builder C): the coach intent check sits
    # beside the record-question check above, before the spend
    # gate below, so a coach question is answered from the local
    # record at $0 and never falls through into a direction run
    # by accident. "Do it" style follow-ups map the coach
    # suggestion id into _run_chat_direction, the normal
    # direction path (see _coach_send_if_coach).
    coach_result = _coach_send_if_coach(config, text)
    if coach_result is not None:
        return coach_result
    # Conversation is not a command (0.27.1, P1 item 5): what
    # is left and carries no edit words gets one plain line at
    # no cost, never the direction gate. Only a clear direction
    # ask reaches the spend gate below.
    plain_line = _conversational_line(text)
    if plain_line is not None:
        entry = _append_thread(
            config,
            {
                "kind": "editor",
                "status": "settled",
                "text": plain_line,
                "lines": [],
                "claims": [],
            },
        )
        return {"kind": "note", "entry": entry}
    if config.signin_key():
        settings = _front_door_settings(config)
        estimate = _direction_estimate_line(config, settings)
        _save_thread_pending(config, {"text": text})
        entry = _append_thread(
            config,
            {
                "kind": "editor",
                "status": "settled",
                "text": (
                    "That needs a direction run, which spends money. "
                    + (estimate + " " if estimate else "")
                    + "Say \u201crun it\u201d and I will, or change the ask."
                ),
                "lines": [],
                "claims": [],
            },
        )
        return {"kind": "needs_confirmation", "entry": entry}
    return _run_chat_direction(config, text)


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
    payload.setdefault("shadow", [])
    return payload


def _beta_score(config: WebAppConfig) -> dict:
    """Best delivery's report card, as one plain count.

    Only flips count (the times Beta disagreed with the take that
    stayed), and only flips a grade or a swap has settled say
    whether Beta matched the creator's own pick. The promotion bar
    is 4 in 5 across the most recent 30 disagreements; this version
    shows the count and promotes nothing on its own.
    """
    record = _load_retake_record(config)
    shadow = [
        entry
        for entry in record.get("shadow", [])
        if isinstance(entry, dict) and entry.get("flip")
    ]
    settled = [entry for entry in shadow if entry.get("matched") is not None]
    recent = settled[-30:]
    matched = sum(1 for entry in recent if entry.get("matched"))
    line = None
    if recent:
        line = (
            "Best delivery has matched your pick in "
            f"{matched} of its last {len(recent)} calls."
        )
    elif shadow:
        line = (
            "Best delivery has disagreed with the take that stayed "
            f"{len(shadow)} {'time' if len(shadow) == 1 else 'times'}; "
            "none of those calls has been graded yet."
        )
    return {
        "flips": len(shadow),
        "settled": len(settled),
        "matched": matched,
        "recent_calls": len(recent),
        "line": line,
    }


def _resolve_shadow_match(
    record: dict,
    pair: dict,
    creators_keeper: Optional[str],
    source: str,
) -> bool:
    """Settle one shadow flip against the creator's actual keeper.

    ``creators_keeper`` is "positional" (the take that stayed is
    the creator's pick), "other" (the creator took the other
    delivery, by swapping), or None (the creator kept both, which
    settles neither side). A flip matches exactly when Beta's pick
    is the creator's keeper. Only the first settlement counts.
    Returns True when the record changed.
    """
    if creators_keeper is None:
        return False
    key = retakes.pair_key(pair)
    for entry in record.get("shadow", []):
        if not isinstance(entry, dict) or entry.get("pair_key") != key:
            continue
        if not entry.get("flip") or entry.get("matched") is not None:
            return False
        entry["matched"] = entry.get("beta_pick") == creators_keeper
        entry["match_source"] = source
        entry["matched_at"] = _utc_now()
        return True
    return False


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
    the operation type. The key carries the keeper mode that made
    the pick, so what the creator grades teaches the mode that made
    it, not some other mode. A "did not work" grade on a pair also
    records the creator's decision about it: that pair is never
    proposed again. A "worked" grade settles any shadow flip on
    the pair: the take that stayed was the creator's pick.
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
            mode = pair.get("keep_mode") or "last"
            entry["pattern_key"] = f"{pair['pattern_key']}:{mode}"
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
        if entry.get("verdict") == "worked":
            if _resolve_shadow_match(record, pair, "positional", "grade"):
                decisions_changed = True
    if decisions_changed:
        _save_retake_record(config, record)
    return raw_entries


def _run_shadow(
    config: WebAppConfig,
    provider_name: str,
    all_pairs: list,
    active_pairs: list,
    timeline: Timeline,
    spoken_direction: str,
    record: dict,
    style: Optional[str] = None,
    provider=None,
) -> dict:
    """Best delivery (Beta), in shadow: record picks, change nothing.

    Runs only when the keeper mode is Best delivery. Every pair
    with a genuine choice (both deliveries complete) gets a
    recorded pick: the keeper Beta would have chosen, and whether
    that disagrees with the take the positional rule kept (a flip).
    Pairs the creator already decided to keep whole are marked
    excluded and never scored; pairs under protection never reach
    the detector, so they never reach shadow either. Word-identical
    pairs are declared ties before any model is asked, with no
    exceptions. The rest go to the provider's shadow judge in one
    call when it can judge, and to the local ranking when it
    cannot or does not answer a pair. A settlement a grade or a
    swap already recorded on a pair survives the refresh.
    """
    counts = {"scored": 0, "flips": 0, "ties": 0, "excluded": 0}
    counts["usage"] = []
    counts["route_events"] = []
    decisions = record.get("decisions") or {}
    existing = {
        entry.get("pair_key"): entry
        for entry in record.get("shadow", [])
        if isinstance(entry, dict) and entry.get("pair_key")
    }
    active_ids = {pair["pair_id"] for pair in active_pairs}
    refreshed: dict = {}

    def base_entry(pair: dict) -> dict:
        old = existing.get(retakes.pair_key(pair)) or {}
        return {
            "pair_key": retakes.pair_key(pair),
            "pair_id": pair["pair_id"],
            "at": _utc_now(),
            "matched": old.get("matched"),
            "match_source": old.get("match_source"),
            "matched_at": old.get("matched_at"),
        }

    candidates = []
    for pair in all_pairs:
        key = retakes.pair_key(pair)
        if pair["pair_id"] not in active_ids:
            entry = base_entry(pair)
            entry.update(
                {
                    "status": "excluded",
                    "excluded_reason": "keep_both",
                    "beta_pick": None,
                    "flip": False,
                }
            )
            refreshed[key] = entry
            counts["excluded"] += 1
        elif pair["band"] in ("a", "b") and retakes.genuine_choice(pair):
            candidates.append(pair)

    judged: dict = {}
    to_judge = [
        pair
        for pair in candidates
        if not retakes.deliveries_identical(pair, timeline)
    ]
    if to_judge:
        if provider is None:
            if provider_name == "meta":
                provider = _live_provider(config, style=style)
            elif provider_name == "mock":
                provider = MockProvider(style=style)
        if provider is not None:
            guidance = creator_rules.active_rule_statements(
                _rules_store(config)
            )
            if spoken_direction:
                guidance = guidance + [spoken_direction]
            try:
                judged = provider.judge_shadow(
                    to_judge, timeline, guidance=guidance
                ) or {}
            except ProviderError as exc:
                _log_raw_error("retake shadow judgment", exc)
                judged = {}
            drain = getattr(provider, "drain_usage", None)
            if callable(drain):
                counts["usage"] = drain() or []
            drain_events = getattr(provider, "drain_route_events", None)
            if callable(drain_events):
                counts["route_events"] = drain_events() or []
    for pair in candidates:
        answer = judged.get(pair["pair_id"])
        if answer is None:
            answer = retakes.shadow_rank(pair, timeline)
        pick = answer["pick"]
        entry = base_entry(pair)
        beta_keep = pair["cut"] if pick == "other" else pair["keep"]
        entry.update(
            {
                "status": "scored",
                "excluded_reason": None,
                "beta_pick": pick,
                "reason": answer["reason"],
                "flip": pick == "other",
                "positional_keep": {
                    "word_start": pair["keep"]["word_start"],
                    "word_end": pair["keep"]["word_end"],
                    "text": pair["keep"]["text"],
                },
                "beta_keep": {
                    "word_start": beta_keep["word_start"],
                    "word_end": beta_keep["word_end"],
                    "text": beta_keep["text"],
                },
            }
        )
        refreshed[entry["pair_key"]] = entry
        pair["shadow"] = {
            "pick": pick,
            "reason": answer["reason"],
            "flip": pick == "other",
        }
        counts["scored"] += 1
        if pick == "other":
            counts["flips"] += 1
        elif pick == "tie":
            counts["ties"] += 1
    untouched = [
        entry
        for entry in record.get("shadow", [])
        if isinstance(entry, dict)
        and entry.get("pair_key") not in refreshed
    ]
    record["shadow"] = untouched + list(refreshed.values())
    return counts


def _run_retake_pass(
    config: WebAppConfig,
    provider_name: str,
    spoken_direction: str = "",
    keep_mode: Optional[str] = None,
    style: Optional[str] = None,
    strict: Optional[bool] = None,
    mode: Optional[str] = None,
) -> dict:
    """Measure the edit for retakes and act on what the bands allow.

    Band A pairs are cut through the verifier like any other change.
    Strict is the default (v0.11.1, fix item 5): a suspected retake
    is cut, keeping the take the keeper rule names, and a cut the
    pass was not sure about is marked unsure in the pair record and
    in the cut's own record, with the plain note the review shows.
    Under strict, Band B pairs are cut locally, so the pass makes
    no judgment call for them. Only an explicit choice runs the
    loose path: the profile's retakes knob set to not strict, or a
    per-video choice (a saved front door choice or a direction for
    this video) that says to only cut the sure ones. On the loose
    path Band B pairs are judged in one provider call when the
    provider can judge; a keep-both or not-a-pair verdict is
    recorded and no operation is made, and any pair the model did
    not answer, plus every Band B pair under a provider that cannot
    judge, becomes a scored flag. Policy-flag pairs (a negation in
    either copy, a short emphasis double) are flags by construction
    on both paths, because which copy stays can change the meaning.
    Decided pairs (v0.13) are the exception on both paths: a
    fragment that stops mid-word, and a chain of tries at one line
    with one complete take, are cut locally in strict and judged
    modes alike, marked unsure, and never become flags or wait for
    a judgment call.
    Pairs the creator already graded as keep-both are never
    proposed again.

    The keeper mode decides which complete delivery a cluster
    keeps: the last take (the default), the first clean take, or
    Best delivery (Beta), which in this version runs in shadow
    only: the positional last-take rule still applies, and Beta
    records the keeper it would have chosen on every pair where
    there is a genuine choice.
    """
    session_log.log_stage(config.session_dir, "retake", "start")
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
        "usage": [],
        "route_events": [],
        "auto_struck": [],
        "auto_struck_count": 0,
    }
    # The involvement mode decides what happens to the pairs this
    # pass cannot settle: in cleanest mode a detected repeat is
    # struck at gold-record time (v0.15, item 22); in tailor mode
    # it waits in gold. A caller that does not name the mode uses
    # the job's saved front-door mode, which defaults to cleanest.
    if mode not in INVOLVEMENT_MODES:
        mode = _front_door_settings(config).get("mode", "cleanest")
    involvement_mode = mode
    summary["mode"] = involvement_mode
    # The pass's provider is built once, with the picked style, and
    # reused for the shadow and the judgment, so the run record's
    # know-how record is the provider this pass actually used.
    style_id = _picked_style(config, {"style": style})
    retake_provider = None
    if provider_name == "meta":
        retake_provider = _live_provider(config, style=style_id)
    elif provider_name == "mock":
        retake_provider = MockProvider(style=style_id)
    summary["skills"] = _provider_skill_versions(retake_provider, style_id)
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    profile = _load_optional_file(config.job_dir / "profile.json")
    base_cuts = _effective_cuts(config)
    timeline = Timeline(transcript, base_cuts)
    record = _load_retake_record(config)
    decisions = record.get("decisions") or {}
    settings = retakes.settings_from_profile(profile)
    if keep_mode in retakes.KEEP_MODES:
        settings["keep"] = keep_mode
    if isinstance(strict, bool):
        settings["strict"] = strict
    mode = settings["keep"]
    summary["keep_mode"] = mode
    summary["strict"] = bool(settings.get("strict", True))
    summary["unsure"] = 0
    summary["unsure_note"] = retakes.unsure_note(settings)
    all_pairs = retakes.find_pairs(timeline, profile, settings)
    pairs = [
        pair
        for pair in all_pairs
        if decisions.get(retakes.pair_key(pair), {}).get("keep_both") is not True
    ]
    # A your-call span is never cut without the creator's yes
    # (0.27, fix 8): on a re-run, a pair that would cut words
    # already waiting open in gold is not proposed again. A pair
    # that only flags (it cuts nothing) is untouched: it simply
    # re-offers the same call, and the store keeps the record
    # the creator has not decided yet.
    open_gold_views = _open_gold_spans(config)
    if open_gold_views:
        gold_spans = [
            (view["word_start"], view["word_end"])
            for view in open_gold_views
            if isinstance(view.get("word_start"), int)
            and isinstance(view.get("word_end"), int)
        ]

        def _pair_cuts_gold(pair: dict) -> bool:
            if pair["band"] == "flag" and not pair.get("decided"):
                return False
            cut = pair["cut"]
            cut_start = cut["word_start"]
            cut_end = cut["word_end"]
            if not any(
                cut_start < g_end and g_start < cut_end
                for g_start, g_end in gold_spans
            ):
                return False
            # A whole-saying extension (0.27) that reaches into
            # an open your-call span is trimmed back to the
            # pair's measured cut instead of dropping the pair:
            # the measured cut settles as before, and the head
            # words stay with the creator's open call. Only the
            # added region may be trimmed; a measured cut that
            # itself overlaps the span still holds the pair out.
            whole_from = cut.get("whole_from")
            if isinstance(whole_from, int) and not any(
                whole_from < g_end and g_start < cut_end
                for g_start, g_end in gold_spans
            ):
                words = transcript.get("words") or []
                cut["word_start"] = whole_from
                cut["text"] = " ".join(
                    str(words[i].get("word") or "")
                    for i in range(whole_from, cut_end)
                )
                del cut["whole_from"]
                cut["whole_saying_trimmed"] = True
                keep = pair["keep"]
                pair["pair_id"] = retakes._pair_id(
                    whole_from,
                    cut_end,
                    keep["word_start"],
                    keep["word_end"],
                )
                pair["pair_key"] = (
                    f"{whole_from}-{cut_end}"
                    f"|{keep['word_start']}-{keep['word_end']}"
                )
                return False
            # The same trim for the stopped-take tail extension
            # (0.27.2): when only the added tail reaches into the
            # open span, the tail stays with the creator's call
            # and the measured cut settles as before.
            tail_from = cut.get("tail_from")
            if isinstance(tail_from, int) and not any(
                cut_start < g_end and g_start < tail_from
                for g_start, g_end in gold_spans
            ):
                words = transcript.get("words") or []
                cut["word_end"] = tail_from
                cut["text"] = " ".join(
                    str(words[i].get("word") or "")
                    for i in range(cut_start, tail_from)
                )
                del cut["tail_from"]
                cut["whole_saying_trimmed"] = True
                keep = pair["keep"]
                pair["pair_id"] = retakes._pair_id(
                    cut_start,
                    tail_from,
                    keep["word_start"],
                    keep["word_end"],
                )
                pair["pair_key"] = (
                    f"{cut_start}-{tail_from}"
                    f"|{keep['word_start']}-{keep['word_end']}"
                )
                return False
            return True

        pairs = [pair for pair in pairs if not _pair_cuts_gold(pair)]
    shadow = None
    if mode == "best":
        shadow = _run_shadow(
            config, provider_name, all_pairs, pairs, timeline,
            spoken_direction, record,
            style=style_id, provider=retake_provider,
        )
        summary["shadow"] = {
            key: shadow[key]
            for key in ("scored", "flips", "ties", "excluded")
        }
        summary["usage"].extend(shadow.get("usage") or [])
        summary["route_events"].extend(shadow.get("route_events") or [])
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
        session_log.log_stage(config.session_dir, "retake", "finish")
        return summary

    operations = retakes.band_a_operations(
        timeline, profile, settings=settings, pairs=pairs
    )
    outcomes: dict = {}
    for pair in pairs:
        if pair["band"] == "a":
            outcomes[pair["pair_id"]] = "proposed"
    for pair in retakes.flag_pairs(pairs):
        if pair.get("decided"):
            continue
        operations.append(retakes.flag_operation(pair))
        outcomes[pair["pair_id"]] = "flagged"
    # A decided band-flag pair is cut, never flagged: the chain of
    # tries (or the mid-word stop) is the decision, and the cut is
    # an unsure cut the creator can bring back.
    decided_flag = [
        pair for pair in retakes.flag_pairs(pairs) if pair.get("decided")
    ]
    if decided_flag:
        operations.extend(
            retakes.decided_cut_operations(
                timeline, profile, settings=settings, pairs=decided_flag
            )
        )
        for pair in decided_flag:
            outcomes[pair["pair_id"]] = "judged_cut"

    borderline = retakes.borderline_pairs(pairs)
    judged_ops: list = []
    verdicts: dict = {}
    if borderline and summary["strict"]:
        # Strict: the borderline pairs are cut here, marked unsure,
        # and no judgment call is made for them. The outcomes use
        # the judged-cut name so the applied and held bookkeeping
        # below treats them the same as any other cut.
        judged_ops = retakes.strict_cut_operations(
            timeline, profile, settings=settings, pairs=pairs
        )
        for pair in borderline:
            outcomes[pair["pair_id"]] = "judged_cut"
    elif borderline:
        # Decided borderline pairs are cut locally on this path
        # too; only the pairs the local evidence did not decide
        # go to the judgment call.
        decided_borderline = [
            pair for pair in borderline if pair.get("decided")
        ]
        open_borderline = [
            pair for pair in borderline if not pair.get("decided")
        ]
        if decided_borderline:
            judged_ops.extend(
                retakes.decided_cut_operations(
                    timeline,
                    profile,
                    settings=settings,
                    pairs=decided_borderline,
                )
            )
            for pair in decided_borderline:
                outcomes[pair["pair_id"]] = "judged_cut"
        provider = retake_provider
        judged = None
        if provider is not None and open_borderline:
            guidance = creator_rules.active_rule_statements(
                _rules_store(config)
            )
            if spoken_direction:
                guidance = guidance + [spoken_direction]
            try:
                judged = provider.judge_retakes(
                    open_borderline, timeline, guidance=guidance
                )
            except ProviderError as exc:
                # A failed judgment call never fails the pass: the
                # pairs degrade to scored flags, like having no
                # provider at all.
                _log_raw_error("retake judgment", exc)
                judged = None
            drain = getattr(provider, "drain_usage", None)
            if callable(drain):
                summary["usage"].extend(drain() or [])
            drain_events = getattr(provider, "drain_route_events", None)
            if callable(drain_events):
                summary["route_events"].extend(drain_events() or [])
        if judged is not None:
            provider_ops, verdicts = judged
            judged_ops.extend(provider_ops)
        for pair in open_borderline:
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
                    "decided": pair.get("decided"),
                    "retake_class": pair["retake_class"],
                    "pattern_key": pair["pattern_key"],
                    "coverage": pair.get("coverage"),
                    "ratio": pair.get("ratio"),
                    "cut_text": pair["cut"]["text"],
                    "keep_text": pair["keep"]["text"],
                    "keep_word_start": pair["keep"]["word_start"],
                    "keep_word_end": pair["keep"]["word_end"],
                    "keep_mode": mode,
                    "shadow": pair.get("shadow"),
                    "unsure": bool(
                        pair.get("unsure")
                        and entry.get("type") == "cut_words"
                    ),
                    "unsure_note": (
                        retakes.unsure_note(settings)
                        if pair.get("unsure")
                        and entry.get("type") == "cut_words"
                        else None
                    ),
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
    summary["unsure"] = sum(
        1
        for op in page_operations
        if op.get("status") == "applied"
        and isinstance(op.get("retake"), dict)
        and op["retake"].get("unsure")
    )
    summary["flagged"] = sum(
        1 for op in page_operations if op.get("status") == "flagged"
    )
    summary["operations"] = page_operations
    record["runs"].append({**summary, "at": _utc_now(), "operations": []})
    record["pairs"] = [
        {
            **pair,
            "outcome": outcomes.get(pair["pair_id"], "proposed"),
            "keep_mode": mode,
        }
        for pair in pairs
    ]
    _save_retake_record(config, record)
    # The pairs this pass could not settle (flagged by policy or by
    # the borderline band, or cut and then held by the verifier)
    # wait in gold for the creator's call — except a detected
    # repeat, which settles as an applied cut at this same record
    # point in every mode (v0.15 item 22, extended by v0.17,
    # feedback items 7 and 8). A pair whose copies say different
    # things still waits, with the default stated.
    retake_protected = [
        (span["word_start"], span["word_end"])
        for span in timeline.protected_spans(profile)
    ]
    summary["auto_struck"] = _record_gold_detected_repeats(
        config,
        _gold_candidates_from_retakes(
            record["pairs"],
            timeline.words,
            protected_ranges=retake_protected,
        ),
        involvement_mode,
        protected_ranges=retake_protected,
    )
    summary["auto_struck_count"] = len(summary["auto_struck"])
    if summary["auto_struck"]:
        # The run record carries the settled spans too: reload the
        # record the settlement just updated (it may have added
        # pair decisions) and stamp its latest run.
        saved_record = _load_retake_record(config)
        if saved_record.get("runs"):
            saved_record["runs"][-1]["auto_struck"] = summary["auto_struck"]
            saved_record["runs"][-1]["auto_struck_count"] = (
                summary["auto_struck_count"]
            )
        _save_retake_record(config, saved_record)
    session_log.log_stage(config.session_dir, "retake", "finish")
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
    keep_mode = str(
        body.get("keep_mode", defaults["keep_mode"]) or "last"
    ).strip().lower()
    if keep_mode not in retakes.KEEP_MODES:
        raise ValueError(
            "Choose the last take, the first clean take, or best "
            "delivery (Beta)."
        )
    mode = str(
        body.get("mode", defaults.get("mode", "cleanest")) or "cleanest"
    ).strip().lower()
    if mode not in INVOLVEMENT_MODES:
        raise ValueError(
            "Choose Proactive, or Leave more in."
        )
    style = str(
        body.get("style", defaults.get("style", "justins-way"))
        or "justins-way"
    ).strip().lower()
    if style not in EDITING_STYLES:
        raise ValueError(
            "Choose Justin's way, Keep it simple, or Hands off."
        )
    settings = {
        "remove_retakes": bool(
            body.get("remove_retakes", defaults["remove_retakes"])
        ),
        "cut_silence": bool(body.get("cut_silence", defaults["cut_silence"])),
        "buffer_s": min(BUFFER_MAX_S, max(BUFFER_MIN_S, buffer_s)),
        "keep_mode": keep_mode,
        "remove_fillers": bool(
            body.get("remove_fillers", defaults.get("remove_fillers", True))
        ),
        "crossfade": bool(body.get("crossfade", defaults.get("crossfade", True))),
        "mode": mode,
        "style": style,
    }
    scope = str(body.get("format_scope") or "both").strip().lower()
    if scope not in ("both", "vertical", "landscape"):
        raise ValueError("Choose both formats, vertical only, or landscape only.")
    direction_text = str(body.get("direction_text") or "").strip()
    direction_recap: list = []
    provider_name = str(body.get("provider") or "").strip().lower()
    if provider_name not in ("mock", "meta"):
        provider_name = "meta" if config.signin_key() else "mock"
    if provider_name == "meta" and not config.signin_key():
        raise ValueError(
            "The Meta provider is not set up on this server yet, "
            "so direction cannot run with it."
        )
    _save_front_door_settings(config, settings)
    session_log.log_stage(config.session_dir, "front door", "start")
    retake_summary = None
    if settings["remove_retakes"]:
        retake_summary = _run_retake_pass(
            config,
            provider_name,
            spoken_direction=direction_text,
            keep_mode=settings["keep_mode"],
            style=settings["style"],
            strict=settings.get("retake_strict"),
            mode=settings["mode"],
        )
    extras = []
    if settings["remove_retakes"]:
        extras.append(
            {
                "id": "front-door-retakes",
                "text": _retakes_guidance(settings["keep_mode"]),
            }
        )
    if direction_text:
        extras.append({"id": "front-door-direction", "text": direction_text})
    formats = ["vertical", "landscape"] if scope == "both" else [scope]
    has_feedback = _feedback_path(config) is not None
    direction_runs = []
    direction_usage: list = []
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
        direction_usage.extend(audit.get("usage") or [])
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
    # The filler pass runs after the retake pass (and after the
    # direction runs and the retake merge, which replace the session
    # audit) and before the silence pass, so its cuts land on the
    # plan the retakes left and its page operations merge into the
    # audit the direction runs settled.
    filler = None
    if settings["remove_fillers"]:
        filler = _run_filler_pass(config, settings)
    # The opening rule: the edit starts on the first real line.
    _run_opening_pass(config)
    silence = None
    if settings["cut_silence"]:
        silence = _run_silence_pass(config, settings["buffer_s"])
    # SMOOTH, NEVER CHOPPY (Message 2R, P0) runs last, on the plan
    # every other pass settled: it sets the join edges on the
    # measured quiet and checks every join before the render.
    smooth_summary = _run_smooth_pass(config)
    # The final sweep (0.27.3): one last look at the settled
    # plan before the spots are read off it, so a stopped try
    # no pass caught is cut here, and a cut final saying is
    # put back, in every path.
    sweep = _run_final_sweep(config)
    # The same-point spots are read off the settled plan (0.27):
    # a passage whose point is made again later becomes a review
    # spot, never a cut. Recorded before the first checkpoint
    # that follows, so Undo treats the spots as part of the edit.
    _record_same_point_spots(config)
    # What this run cost, per feature, from the passes' own
    # records (v0.7.2). The deterministic passes are $0 and say
    # so; the model work prices from its captured usage.
    cost_entries: list = []
    if retake_summary is not None:
        cost_entries.extend(
            _usage_cost_entries(
                "retake_judge", retake_summary.get("usage"), "front door"
            )
        )
    if direction_runs:
        cost_entries.extend(_direction_cost_entries(direction_usage))
    if filler is not None:
        cost_entries.append(costs.make_entry("filler", 0.0, "on this computer"))
    if silence is not None:
        cost_entries.append(
            costs.make_entry("silence", 0.0, "on this computer")
        )
    _account_costs(config, cost_entries)
    cost_block = costs.job_cost_block(cost_entries)
    render_note = None
    if config.is_intake_job:
        # An edit run on a job born here rebuilds the review
        # video from the new plan, so the player shows this edit
        # and not an older one (FIX A). A build that fails says
        # so here and in the watching state, never silently.
        source_name = intake_build.intake_source_video(config.job_dir)
        if source_name:
            try:
                media = speech.probe_media(config.job_dir / source_name)
                has_audio = bool(media.get("has_audio", True))
            except speech.TranscribeError:
                has_audio = True
            render_note = _render_session_splice(
                config, config.job_dir / source_name, has_audio
            )
    session_log.log_stage(config.session_dir, "front door", "finish")
    # The retake line, in the one count's numbers (0.25,
    # finding 6): copies removed and copies set aside, counted
    # from the final plan like every other surface, not from
    # the pass's own operation tally.
    retake_lines: list = []
    if retake_summary is not None:
        try:
            front_counts = _edit_counts(config)
            retake_lines.append(
                "Retakes: "
                f"{front_counts['retakes_removed']} removed, "
                f"{front_counts['retakes_set_aside']} set aside "
                "for your call."
            )
        except Exception:
            # No fallback tally (0.26, fix 3): a line the one
            # count cannot back is left out, never recounted
            # from the pass's own operations.
            pass
    if sweep and sweep.get("cuts"):
        _sweep_count = len(sweep["cuts"])
        retake_lines.append(
            "Final check: took out "
            f"{_sweep_count} stopped "
            f"{'try' if _sweep_count == 1 else 'tries'} "
            "that had slipped through."
        )
    # The length change, said plainly in its own line: the edit
    # can be much shorter than the recording (only the talking
    # parts are kept), and the creator should never have to
    # discover that from the player.
    length_line = None
    try:
        _fd_timeline = Timeline(
            _load_json(str(config.job_dir / "transcript.json")),
            _effective_cuts(config),
        )
        length_line = (
            f"Your video was "
            f"{_format_clock(_fd_timeline.source_duration)}. "
            f"The edit is "
            f"{_format_clock(_fd_timeline.output_duration)}."
        )
    except Exception:
        length_line = None
    return {
        "ok": True,
        "provider": provider_name,
        "settings": settings,
        "render_note": render_note,
        "length_recap": length_line,
        "recap": _keep_mode_recap(settings),
        "direction_recap": direction_recap,
        "beta": _beta_score(config),
        "format_scope": scope,
        "direction_runs": direction_runs,
        "retakes": retake_summary,
        "filler": filler,
        "lines": retake_lines + (list(filler["lines"]) if filler else []),
        "mode_lines": _mode_and_gold_lines(config, settings),
        "crossfade": bool(settings.get("crossfade", True)),
        "cost_lines": cost_block["recap_lines"] if cost_block else [],
        "costs": cost_block,
        "gold": len(_open_gold_spans(config)),
        "silence": silence,
        "smooth": smooth_summary,
        "saved_rules": creator_rules.active_rule_statements(
            _rules_store(config)
        ),
    }


# ---------------------------------------------------------------------------
# The first edit (v0.6.0): a dropped raw video becomes a job
#
# The lone-video drop used to stop at "you can watch it here." Now
# the creator can ask for the first edit: the app writes down the
# words (ElevenLabs Scribe when a key is set, else the speech pack
# on this computer), measures the silences from the sound itself,
# lays out a baseline plan that keeps every word, and then runs the
# front door's own deterministic engines once, with any direction
# given up front mapped onto the run's settings first. The result
# is rendered with the intake's own splice, and clean captions are
# written when the toggle is on. The dropped file is only ever read.
# ---------------------------------------------------------------------------

FIRST_EDIT_WORK_NAME = "intake-work"
NO_WORDS_MESSAGE = (
    "No words were found in that video, so there is nothing to "
    "edit yet. If someone is speaking, a video where the voice "
    "is clearer will work better."
)
NO_ROUTE_MESSAGE = (
    "Writing down the words needs the speech pack on this "
    "computer or an ElevenLabs key the app can use, and neither "
    "is set up in this copy. The video is still here to watch."
)
DICTATE_NO_KEY_MESSAGE = (
    "Voice dictation needs the Meta sign-in first, and typing "
    "works as always."
)


def _intake_word_route(config: WebAppConfig) -> Optional[str]:
    """Which word route intake can use: "scribe", "local", or None.

    Scribe wins when the creator's ElevenLabs key is set: it is
    verbatim and word-timed, and it keeps the kit light. The local
    speech pack is the zero-cost route and needs no key at all.
    Meta's speech-to-text is never a word route: it returns turn
    timings only, and no edit can be measured from a turn.
    """
    if config.elevenlabs_key():
        return "scribe"
    if speech.local_available():
        return "local"
    return None


def _adopt_job(httpd, job_root: Path) -> WebAppConfig:
    """Make a staged folder the server's current job.

    Mirrors the drop handler's own swap: the session sign-in and
    the launch home carry over, and the intake root stays anchored
    where the server started it.
    """
    current = httpd.config  # type: ignore[attr-defined]
    config = WebAppConfig(job_root, launch_home=current.launch_home)
    setattr(config, "session_api_key", getattr(current, "session_api_key"))
    setattr(
        config,
        "session_elevenlabs_key",
        getattr(current, "session_elevenlabs_key", None),
    )
    setattr(
        config,
        "session_baseten_key",
        getattr(current, "session_baseten_key", None),
    )
    setattr(
        config,
        "session_fireworks_key",
        getattr(current, "session_fireworks_key", None),
    )
    # A test-video mark set at the front door carries into the
    # job this first edit creates, the same as a dropped job.
    _carry_launch_test_video(config)
    # So does the estimate priced at the offer (0.27.1, P1
    # item 8): the job's one stored number, copied in from the
    # launch session that priced it.
    try:
        launch_estimate = current.session_dir / ESTIMATE_FILENAME
        job_estimate = config.session_dir / ESTIMATE_FILENAME
        if launch_estimate.is_file() and not job_estimate.is_file():
            config.session_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(launch_estimate, job_estimate)
    except Exception:
        pass
    # So does the front door's conversation: messages said before
    # this job existed are the start of this job's thread.
    _carry_launch_thread(config)
    httpd.config = config  # type: ignore[attr-defined]
    _kick_playback_copies(config)
    return config


def _validated_drop_settings(body) -> Optional[dict]:
    """The per-drop front door settings in a first-edit request.

    The drop block can carry the creator's choices for this one
    video. Only the front door keys are taken, and each is
    validated the way the front door itself validates it: booleans
    must be JSON booleans, the buffer is a number clamped to the
    front door's range, and the word choices must be known
    words. Anything else in the object is ignored, and a request
    with no settings object at all returns None.
    """
    if not isinstance(body, dict):
        return None
    raw = body.get("settings")
    if not isinstance(raw, dict):
        return None
    validated: dict = {}
    for key in ("remove_retakes", "cut_silence", "remove_fillers", "crossfade"):
        if isinstance(raw.get(key), bool):
            validated[key] = raw[key]
    if isinstance(raw.get("retake_strict"), bool):
        validated["retake_strict"] = raw["retake_strict"]
    buffer = raw.get("buffer_s")
    if isinstance(buffer, (int, float)) and not isinstance(buffer, bool):
        validated["buffer_s"] = min(
            BUFFER_MAX_S, max(BUFFER_MIN_S, float(buffer))
        )
    if raw.get("keep_mode") in retakes.KEEP_MODES:
        validated["keep_mode"] = raw["keep_mode"]
    if raw.get("mode") in INVOLVEMENT_MODES:
        validated["mode"] = raw["mode"]
    if raw.get("style") in EDITING_STYLES:
        validated["style"] = raw["style"]
    return validated


def _state_summary(summary: Optional[dict]) -> dict:
    """One pass's summary, safe to serve in the first-edit state.

    Pass summaries are plain JSON by construction; if one ever
    grows a value JSON cannot carry, the state falls back to the
    summary's scalar counts and lines rather than failing the poll
    the page is waiting on. A pass that did not run reads as
    ``{"ran": False}``.
    """
    if summary is None:
        return {"ran": False}
    try:
        json.dumps(summary)
        return summary
    except (TypeError, ValueError):
        return {
            key: value
            for key, value in summary.items()
            if isinstance(value, (bool, int, float, str, list))
            and all(
                isinstance(item, (bool, int, float, str))
                for item in (value if isinstance(value, list) else [value])
            )
        }


def _format_clock(seconds: float) -> str:
    """Seconds as the progress line shows them: "0:07", "12:40"."""
    total = max(0, int(round(float(seconds or 0.0))))
    return f"{total // 60}:{total % 60:02d}"


# The first edit's usual length (v0.20, chunk C): the app
# records how long each finished first edit took on this
# computer, in the launch home, and the progress display may
# say "usually about N minutes" from those measured runs.
# Fewer than two finished runs is not a "usually" yet, so the
# text stays None and the page shows the elapsed time alone.
# No figure is ever worked out from the pace of the run in
# progress: that was the 0.15 invention v0.17 removed, and it
# stays removed (percent and remaining_s are still always
# None while a run works). The usual length is known before
# a run starts and never counts down.
FIRST_EDIT_RUNS_NAME = "first-edit-runs.json"
FIRST_EDIT_RUNS_KEPT = 8
FIRST_EDIT_RUNS_MIN = 2


def _record_first_edit_run(launch_home, seconds: float) -> None:
    """Remember one finished first edit's length. Never raises."""
    try:
        if launch_home is None or seconds <= 0:
            return
        path = Path(launch_home) / FIRST_EDIT_RUNS_NAME
        runs = []
        if path.is_file():
            runs = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(runs, list):
                runs = []
        runs.append(round(float(seconds), 1))
        runs = runs[-FIRST_EDIT_RUNS_KEPT:]
        path.write_text(json.dumps(runs), encoding="utf-8")
    except Exception:
        pass


def _first_edit_usual_seconds(launch_home) -> Optional[float]:
    """The usual first-edit length on this computer, in seconds.

    The median of the measured lengths of the last few finished
    first edits (the same record _first_edit_eta_text words),
    or None when there is not enough history to say "usually"
    honestly. The progress bubble's bar and its time-left line
    are computed from this number and the run's own measured
    elapsed time, never from the pace of the run in progress
    (0.27.3, chunk B item 5).
    """
    try:
        if launch_home is None:
            return None
        path = Path(launch_home) / FIRST_EDIT_RUNS_NAME
        if not path.is_file():
            return None
        runs = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(runs, list):
            return None
        runs = [
            float(run) for run in runs
            if isinstance(run, (int, float)) and run > 0
        ]
        if len(runs) < FIRST_EDIT_RUNS_MIN:
            return None
        return float(statistics.median(runs[-FIRST_EDIT_RUNS_KEPT:]))
    except Exception:
        return None


def _first_edit_eta_text(launch_home) -> Optional[str]:
    """Plain words for how long a first edit usually takes.

    The median of the measured lengths of the last few
    finished first edits on this computer, or None when there
    is not enough history to say "usually" honestly.
    """
    try:
        if launch_home is None:
            return None
        path = Path(launch_home) / FIRST_EDIT_RUNS_NAME
        if not path.is_file():
            return None
        runs = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(runs, list):
            return None
        runs = [
            float(run) for run in runs
            if isinstance(run, (int, float)) and run > 0
        ]
        if len(runs) < FIRST_EDIT_RUNS_MIN:
            return None
        usual = statistics.median(runs[-FIRST_EDIT_RUNS_KEPT:])
    except Exception:
        return None
    if usual < 45:
        return "usually under a minute"
    minutes = max(1, int(round(usual / 60.0)))
    if minutes == 1:
        return "usually about 1 minute"
    return f"usually about {minutes} minutes"


def _progress_fields(
    started_at: float,
    stage_started_at: float,
    index: int,
    total: int,
    now: Optional[float] = None,
) -> dict:
    """The run-progress numbers one stage carries (item 15).

    Elapsed time is measured, never estimated: the whole run's
    clock and the current step's own clock, both from the
    monotonic timestamps the run recorded.

    v0.17 (feedback items 1 and 18): there is no percentage and
    no time remaining anymore, at any stage. The old fields
    computed both from the pace of the stages finished so far,
    and stage times vary too much for that to mean anything:
    at the last stage the share read "100% done" while the
    stage was still working, and the remaining figure cycled
    (9 seconds, 11 seconds, 9 again) as the clock ran. Justin,
    watching it: "It doesn't actually feel true. Something
    feels off and I'm losing trust." A run shows the step it
    is on and how long it has been running, and that is all;
    the keys stay in the dict, always None while a run works,
    so no consumer can fill them with a guess.
    """
    if now is None:
        now = time.monotonic()
    elapsed = (
        max(0.0, now - started_at) if started_at is not None else 0.0
    )
    stage_elapsed = (
        max(0.0, now - stage_started_at)
        if stage_started_at is not None
        else 0.0
    )
    return {
        "elapsed_s": round(elapsed, 1),
        "stage_elapsed_s": round(stage_elapsed, 1),
        "percent": None,
        "remaining_s": None,
    }


def _progress_line(fields: dict) -> str:
    """One stage's progress as a single plain line (item 15).

    "Step 3 of 8 · 0:42 so far." The step and the measured
    elapsed time are the whole line (v0.17): no percentage and
    no time remaining, because the app does not know either
    while a stage is still working, and saying so anyway is
    what broke trust in the 0.15 review.
    """
    parts = []
    index = fields.get("stage_index")
    total = fields.get("stage_total")
    if index and total:
        parts.append(f"Step {index} of {total}")
    parts.append(f"{_format_clock(fields.get('elapsed_s') or 0)} so far")
    return " · ".join(parts)


RUN_STATE_FILENAME = "first_edit_run.json"


def _persist_run_state(
    launch_home, state: dict, status: str, started_wall: float,
    intake_id: str,
) -> None:
    """Write the run's state where the chat can read it.

    The status endpoint's registry lives on the server object,
    which a chat send settling in another request cannot see.
    This file is the one run record every surface reads (the
    launch home, which every config of this server shares): a
    status question mid-run is answered from it, so two answers
    in a row can never disagree the way answers read off a
    half-settled job record did on his Mac run (0.27.1, P1
    item 4).
    """
    try:
        if launch_home is None:
            return
        path = Path(launch_home) / RUN_STATE_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "status": status,
                    "intake_id": intake_id,
                    "stage": state.get("stage"),
                    "stage_index": state.get("stage_index"),
                    "stage_total": state.get("stage_total"),
                    "started_at_wall": started_wall,
                    "first_edit_eta": state.get("first_edit_eta"),
                    "first_edit_eta_s": state.get("first_edit_eta_s"),
                }
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _active_edit_run(config: WebAppConfig) -> Optional[dict]:
    """The first edit currently running, from the run record.

    None when no run is working: the record says done or
    failed, or is older than any real run (a stale write from a
    server that died mid-run is not a run).
    """
    try:
        path = Path(config.launch_home) / RUN_STATE_FILENAME
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("status") != "working":
        return None
    started = data.get("started_at_wall")
    if not isinstance(started, (int, float)) or isinstance(started, bool):
        return None
    elapsed = time.time() - float(started)
    if elapsed < 0 or elapsed > 45 * 60:
        return None
    data["elapsed_s"] = elapsed
    return data


def _run_status_text(run: dict, question: str = "") -> str:
    """The progress answer, in at most three short lines.

    Stage and elapsed come from the run record alone (never
    the job's counts, which move while the passes settle), and
    the usual length is this computer's measured history, the
    same words the status endpoint carries. The video landing
    here is the whole answer to "where is it" while it works.
    """
    stage = str(run.get("stage") or "Working on it").strip()
    stage = stage.rstrip("….").strip()
    # The panel's answer shape (0.27.3, chunk B item 5): the
    # current stage in plain words, about how long is left,
    # and where the video lands. The stage phrases are the
    # progress bubble's own stage names, so the words he hears
    # here match the words on screen.
    lowered_stage = stage.lower()
    if "retake" in lowered_stage:
        phrase = "Finding retakes now"
    elif "sound" in lowered_stage:
        phrase = "Taking the sound out now"
    elif "silence" in lowered_stage and "measur" in lowered_stage:
        phrase = "Measuring the silences now"
    elif "silence" in lowered_stage or "gap" in lowered_stage:
        phrase = "Cutting the silence now"
    elif "um" in lowered_stage:
        phrase = "Taking out the ums and uhs now"
    elif "direction" in lowered_stage:
        phrase = "Reading your direction now"
    elif "plan" in lowered_stage:
        phrase = "Laying out the plan now"
    elif "review video" in lowered_stage or "building" in lowered_stage:
        phrase = "Building your video now"
    elif "word" in lowered_stage or "transcri" in lowered_stage:
        phrase = "Writing the words down now"
    elif "bringing" in lowered_stage or "starting" in lowered_stage:
        phrase = "Bringing in your video now"
    else:
        base = (stage[0].upper() + stage[1:]) if stage else "Working on it"
        phrase = base if base.endswith("now") else base + " now"
    elapsed_s = float(run.get("elapsed_s") or 0)
    eta_s = run.get("first_edit_eta_s")
    left = ""
    if isinstance(eta_s, (int, float)) and eta_s > 0:
        remaining = float(eta_s) - elapsed_s
        if remaining > 45:
            minutes = max(1, round(remaining / 60))
            left = f", about {minutes} minute{'s' if minutes != 1 else ''} left"
        elif remaining > 0:
            seconds = max(5, int(round(remaining / 5.0)) * 5)
            left = f", about {seconds} seconds left"
        else:
            left = ", taking a bit longer than usual"
    lowered = str(question or "").lower()
    opener = "Not yet. " if "review" in lowered else ""
    return (
        f"{opener}{phrase}{left}. "
        "The video lands here when it is done."
    )


# Status requests (0.27.1, P1 item 4): the shapes a "where is
# my edit?" message takes while a run is working, as questions
# ("how's it going?", "can I review now?", "where's the preview
# video?") and as plain asks ("I need you to update me"). These
# are answered from the run record alone, at $0: never the
# record's counts (which move while the passes settle, so two
# answers in a row contradicted each other on his Mac run),
# never a direction run.
_STATUS_PHRASES = (
    "update me",
    "give me an update",
    "any update",
    "how's it going",
    "hows it going",
    "how is it going",
    "how's the edit",
    "how is the edit",
    "can i review",
    "review now",
    "ready to review",
    "where's the preview",
    "wheres the preview",
    "where is the preview",
    "preview video",
    "where's the video",
    "where is the video",
    "how much longer",
    "how long left",
    "how long will it take",
    "is it done",
    "done yet",
    "is it ready",
    "almost done",
    "what stage",
    "is it still working",
    "still editing",
)


def _is_status_request(text: str) -> bool:
    """True when a chat message asks after a running edit."""
    lowered = " " + re.sub(
        r"\s+", " ", str(text or "").lower().strip()
    ) + " "
    return any(phrase in lowered for phrase in _STATUS_PHRASES)


# ---------------------------------------------------------------------------
# Offer chips (0.27.3, chunk B item 8): a pending action the
# chat proposes runs on a tap, not on a typed command. The
# first offer is the dead-space trim: the creator names a
# moment and a quiet ("there's dead space at 2:10"), the
# editor answers with what it measured there and one chip
# carrying the action and its cost ("Trim to your 0.2 s pause
# · free"). The trim itself is local and free: the one
# join-pause path the transcript gap erase uses, aimed at the
# creator's own pause setting. The offer rides on the editor's
# thread entry; tapping it posts to /api/thread/offer, and the
# result entry carries the trim's checkpoint as its action, so
# the entry's own Undo restores the pause. Typed "run it"
# still works: it runs the newest open offer.

_DEAD_SPACE_WORDS = (
    "dead space",
    "dead air",
    "silence",
    "silent",
    "quiet",
    "pause",
    "gap",
    "empty space",
)

_OFFER_CONFIRM_WORDS = {"run it", "do it", "yes", "go ahead", "ok", "okay"}


def _dead_space_candidates(config: WebAppConfig) -> list:
    """Long joins in the current plan, measured from the plan.

    Each candidate is the join's output time, its measured
    pause, and the two kept words it sits between. Joins a pin
    or protected moment covers are never offered.
    """
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    words = transcript.get("words") or []
    if not words:
        return []
    timeline = Timeline(transcript, _effective_cuts(config))
    try:
        buffer_s = float(
            _front_door_settings(config).get("buffer_s") or 0.1
        )
    except Exception:
        buffer_s = 0.1
    protected = _protected_join_indices(config, timeline)
    out = []
    for join in timeline.joins():
        index = join["join_index"]
        if index in protected:
            continue
        prev_words = timeline.words_in_range(timeline.ranges[index])
        next_words = timeline.words_in_range(timeline.ranges[index + 1])
        if not prev_words or not next_words:
            continue
        prev_idx, next_idx = prev_words[-1], next_words[0]
        tail = float(timeline.ranges[index]["end"]) - float(
            words[prev_idx]["end"]
        )
        head = float(words[next_idx]["start"]) - float(
            timeline.ranges[index + 1]["start"]
        )
        pause = tail + head
        if pause > buffer_s + 0.25:
            out.append(
                {
                    "join_index": index,
                    "output_s": float(join["output_time"]),
                    "pause_s": pause,
                    "after_word_index": prev_idx,
                    "before_word_index": next_idx,
                    "buffer_s": buffer_s,
                }
            )
    return out


def _run_offer(config: WebAppConfig, entry: dict) -> dict:
    """Run one entry's offer and record the result entry.

    The offer is spent either way (a second tap must not trim
    twice), the result says what happened in one line, and a
    trim that landed carries its checkpoint as the entry's
    action so the standing per-action Undo restores it.
    """
    offer = entry.get("offer") or {}
    kind = offer.get("kind")
    _set_thread_entry_fields(
        config, entry.get("id"), {"offer": None, "offer_spent": True}
    )
    if kind == "trim_pause":
        try:
            result = _apply_gap_erase(
                config,
                int(offer.get("after_word_index")),
                int(offer.get("before_word_index")),
                target_total_s=float(offer.get("target_pause_s")),
            )
        except Exception as exc:  # the gap moved under the offer
            result = {"changed": False, "message": str(exc)}
        if result.get("changed"):
            text = (
                f"Trimmed. The quiet at {offer.get('at_label', '')} "
                f"is now your {offer.get('target_pause_s')} s pause."
            )
            state = checkpoints.state(config)
            fields = {"kind": "editor", "status": "settled", "text": text}
            if state.get("current") is not None:
                fields["action_id"] = f"checkpoint:{state['current']}"
                fields["action_label"] = "Transcript gap shortened"
            return _append_thread(config, fields)
        return _append_thread(
            config,
            {
                "kind": "editor",
                "status": "settled",
                "text": (
                    "That trim could not be made: "
                    + str(
                        result.get("message")
                        or "the quiet there is not on a join."
                    )
                ),
            },
        )
    return _append_thread(
        config,
        {
            "kind": "editor",
            "status": "settled",
            "text": "That offer is not one I can run anymore.",
        },
    )


def _offer_send(config: WebAppConfig, text: str) -> Optional[dict]:
    """Item 8 settle step: dead-space notes and "run it".

    A message naming a quiet at a clock time gets a measured
    answer with a tap chip when a long join sits near that
    time, and an honest one line when none does. A bare
    confirm ("run it", "do it") runs the newest open offer.
    Anything else falls through untouched.
    """
    if not config.has_job:
        return None
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    normalized = normalized.strip(" .!")
    entries = _load_thread(config)
    if normalized in _OFFER_CONFIRM_WORDS:
        for entry in reversed(entries):
            if entry.get("offer"):
                result = _run_offer(config, entry)
                return {"kind": "offer", "entry": result}
        return None
    lowered = str(text or "").lower()
    if lowered.lstrip().startswith("why"):
        return None
    if not any(word in lowered for word in _DEAD_SPACE_WORDS):
        return None
    match = re.search(r"(\d{1,3}):(\d{2})", str(text or ""))
    if not match:
        return None
    target_s = int(match.group(1)) * 60 + int(match.group(2))
    try:
        candidates = _dead_space_candidates(config)
    except Exception:
        return None
    if not candidates:
        return None
    near = [c for c in candidates if abs(c["output_s"] - target_s) <= 6.0]
    if not near:
        buffer_s = float(
            _front_door_settings(config).get("buffer_s") or 0.1
        )
        entry = _append_thread(
            config,
            {
                "kind": "editor",
                "status": "settled",
                "text": (
                    f"I don't hear a long quiet at "
                    f"{_format_clock(target_s)}. The pauses near "
                    f"there are already at your {buffer_s} s."
                ),
            },
        )
        return {"kind": "offer", "entry": entry}
    best = min(near, key=lambda c: abs(c["output_s"] - target_s))
    at_label = _format_clock(best["output_s"])
    target_pause = best["buffer_s"]
    entry = _append_thread(
        config,
        {
            "kind": "editor",
            "status": "open",
            "text": (
                f"Found {best['pause_s']:.1f} s of silence at "
                f"{at_label}."
            ),
            "offer": {
                "kind": "trim_pause",
                "label": f"Trim to your {target_pause} s pause · free",
                "after_word_index": best["after_word_index"],
                "before_word_index": best["before_word_index"],
                "target_pause_s": target_pause,
                "at_label": at_label,
            },
        },
    )
    return {"kind": "offer", "entry": entry}


def _status_answer_send(config: WebAppConfig, text: str) -> Optional[dict]:
    """Settle a status request from the one run record, or None.

    While an edit runs, the answer is _run_status_text over the
    persisted record (stage, elapsed, usual length, where the
    video lands): every number in it comes from that one file,
    so two answers in a row can only move forward, never
    disagree. Just after a run lands, an update ask gets the
    one line that is true then. Anything else returns None and
    the normal order runs.
    """
    if not _is_status_request(text):
        return None
    run = _active_edit_run(config)
    if run is not None:
        entry = _append_thread(
            config,
            {
                "kind": "editor",
                "record_answer": True,
                "status": "settled",
                "text": _run_status_text(run, text),
                "lines": [],
                "claims": [],
            },
        )
        return {"kind": "status", "entry": entry}
    try:
        data = json.loads(
            (Path(config.launch_home) / RUN_STATE_FILENAME).read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        data = None
    if isinstance(data, dict) and data.get("status") == "done":
        entry = _append_thread(
            config,
            {
                "kind": "editor",
                "record_answer": True,
                "status": "settled",
                "text": (
                    "That edit is done. The video is here in this "
                    "chat, on the Done message."
                ),
                "lines": [],
                "claims": [],
            },
        )
        return {"kind": "status", "entry": entry}
    return None


def _run_first_edit(httpd, intake_id: str, direction_text: str,
                    captions_on: bool,
                    drop_settings: Optional[dict] = None) -> None:
    """Build a job from one staged video and make its first edit.

    Runs in its own thread; progress lands in the first-edit state
    the status endpoint serves, in plain words at every stage. The
    job becomes the server's job only once its files exist, so a
    failure leaves the app exactly as it was.

    The passes run in the front door's order: retakes, then the
    filler pass, then the silence sweep (pauses over the buffer are
    shortened toward the buffer), then the direction pass reads
    whatever direction the deterministic controls could not map.
    Per-drop settings outrank the saved ones, and spoken or typed
    direction outranks both.
    """
    registry = httpd.intakes  # type: ignore[attr-defined]
    states = httpd.first_edit_jobs  # type: ignore[attr-defined]
    state = states[intake_id]
    record = registry[intake_id]
    base = Path(record["dir"])
    video_path = base / record["video_rel"]
    job_root = video_path.parent

    # The front door's own session log (v0.10.1). Until now this
    # run wrote nothing to any log before the job existed, so a
    # failure at the front door (the words never written down,
    # say) left the problem report with nothing to show. The log
    # lives in the front door's session folder, the one a no-job
    # problem report reads, and it takes the start line, the
    # words line, and any failure with its full detail.
    front_session = httpd.config.session_dir  # type: ignore[attr-defined]
    session_log.log_stage(
        front_session, "first edit", "start",
        f"video: {video_path.name}",
    )

    # The run's clocks (item 15): when the run started, and when
    # the current stage started, both monotonic. Every stage
    # carries the measured elapsed time only (v0.17: no
    # percentage of stages and no computed time remaining;
    # both were guesses that read as facts, and they are gone).
    run_started_at = time.monotonic()
    run_started_wall = time.time()
    stage_marks = {"at": run_started_at}

    def stage(text: str, index: int, total: int) -> None:
        # The page shows the stage, its number, and how many there
        # are: progress a person can see, not a gray button. It
        # also carries the run's measured elapsed time, refreshed
        # on the same write. No percent and no time remaining are
        # computed (v0.17): the app does not know them while a
        # stage works, and the 0.15 review showed it guessing.
        now = time.monotonic()
        state["stage"] = text
        state["stage_index"] = index
        state["stage_total"] = total
        state["started_at"] = run_started_at
        state["stage_started_at"] = now
        stage_marks["at"] = now
        state.update(
            _progress_fields(run_started_at, now, index, total, now=now)
        )
        state["progress_line"] = _progress_line(
            {
                **state,
                "stage_index": index,
                "stage_total": total,
            }
        )
        _persist_run_state(
            httpd.config.launch_home,  # type: ignore[attr-defined]
            state, "working", run_started_wall, intake_id,
        )

    def stage_total(settings: dict, has_direction: bool = False) -> int:
        return (
            4
            + (1 if settings.get("remove_retakes") else 0)
            + (1 if settings.get("remove_fillers") else 0)
            + (1 if settings.get("cut_silence") else 0)
            + (1 if has_direction else 0)
            + 1
        )

    # The total is first estimated from the standing front-door
    # choices; once this video's direction has been read and the
    # settings are settled, the exact total replaces it. (The
    # watchable copy is not a stage: it has been building in the
    # background since the drop finished.)
    standing = (
        _front_door_settings(httpd.config)  # type: ignore[attr-defined]
        if httpd.config.has_job  # type: ignore[attr-defined]
        else usual.load_usual(dict(FRONT_DOOR_DEFAULTS))
    )
    total = stage_total(standing, bool(direction_text.strip()))

    try:
        # Step copy names only what the step does (item 26): this
        # step takes the sound out of the video, a quick step, and
        # no step promises a long wait it does not take. The one
        # honest length note in this run belongs to the local
        # route's first-run model download, below, and the run's
        # measured progress (item 15) carries the real timing.
        stage("Taking the sound out of your video…", 1, total)
        media = speech.probe_media(video_path)
        work = job_root / FIRST_EDIT_WORK_NAME
        wav_path = work / "audio.wav"
        duration = speech.extract_audio(video_path, wav_path)
        if media.get("duration") is None:
            media["duration"] = duration
        # Saved transcripts (v0.11.0, fix item 5): this video is
        # known by its size and a hash of the whole file, never
        # by its name. A saved copy found under that identity is
        # used before any provider is called, so the words are
        # not paid for twice; it carries the same shape as a
        # fresh result, so everything below runs the same either
        # way. When identity is uncertain there is no saved copy
        # to find, the words are written down fresh, and nothing
        # extra is said.
        video_identity = saved_transcripts.identity_for_file(video_path)
        saved_store = (
            httpd.config.launch_home  # type: ignore[attr-defined]
            / saved_transcripts.STORE_DIR_NAME
        )
        result = (
            saved_transcripts.load_saved(video_identity, saved_store)
            if video_identity is not None
            else None
        )
        transcript_reused = result is not None
        reuse_line = None
        if transcript_reused:
            stage("Using the transcript saved from the earlier run…", 2, total)
            result = {**result, "duration": duration}
            reuse_line = saved_transcripts.reuse_recap_line(
                result.get("engine")
            )
            route = (
                "local"
                if saved_transcripts.is_local_engine(result.get("engine"))
                else "scribe"
            )
        else:
            route = _intake_word_route(httpd.config)  # type: ignore[attr-defined]
            if route is None:
                raise speech.TranscribeError(NO_ROUTE_MESSAGE)
            if route == "scribe":
                stage(
                    "Writing the words down with ElevenLabs Scribe…",
                    2,
                    total,
                )
                result = speech.scribe_transcribe_wav(
                    wav_path,
                    httpd.config.elevenlabs_key(),  # type: ignore[attr-defined]
                    duration_s=duration,
                    work_dir=work,
                )
            else:
                stage(
                    "Writing the words down on this computer. The "
                    "first time, the speech model downloads once "
                    "(about 150 MB), so this can take a few minutes.",
                    2,
                    total,
                )
                result = speech.local_transcribe_wav(
                    wav_path, duration_s=duration
                )
            if video_identity is not None and result.get("words"):
                # Saving never fails the edit: a copy that cannot
                # be written only loses the future saving.
                saved_transcripts.save_transcript(
                    video_identity, result, saved_store
                )
        words = result["words"]
        if not words:
            session_log.log_stage(
                front_session, "first edit", "finish",
                "no words were found in that video",
            )
            state.update(
                {"status": "no_words", "message": NO_WORDS_MESSAGE}
            )
            shutil.rmtree(work, ignore_errors=True)
            return
        session_log.log_stage(
            front_session, "writing the words down", "finish",
            f"{len(words)} words, route: {route}",
        )
        stage("Measuring the silences from the sound itself…", 3, total)
        spans = speech.measure_speech_spans(wav_path)
        stage("Laying out the first plan…", 4, total)
        session_log.log_stage(
            job_root / "tye-session", "intake", "start"
        )
        intake_build.build_job_files(
            job_root,
            video_path.name,
            result,
            spans,
            media,
            captions_on=captions_on,
        )
        config = _adopt_job(httpd, job_root)
        session_log.log_stage(
            config.session_dir, "intake", "finish",
            "the job was built from the dropped video",
        )
        session_log.log_stage(config.session_dir, "first edit", "start")
        parsed = direction_parse.parse_direction(direction_text)
        settings = _front_door_settings(config)
        if drop_settings:
            settings.update(drop_settings)
        settings.update(parsed["overrides"])
        # Saving the settled settings here is also the write-back
        # that updates the creator's usual, so the drop block starts
        # from these choices next time.
        _save_front_door_settings(config, settings)
        session = config.session_dir
        session.mkdir(parents=True, exist_ok=True)
        _write_json(
            session / "intake_direction.json",
            {
                "text": direction_text,
                "overrides": parsed["overrides"],
                "recap": parsed["recap"],
                "remainder": parsed["remainder"],
            },
        )
        if parsed["remainder"]:
            feedback_path = session / "feedback.json"
            payload = {
                "video": job_root.name,
                "feedback_done": False,
                "comments": [],
                "overall_feedback": [],
            }
            if feedback_path.is_file():
                existing = _load_json(str(feedback_path))
                if isinstance(existing, dict):
                    payload = existing
            payload.setdefault("overall_feedback", []).append(
                {"id": "intake-direction", "text": parsed["remainder"]}
            )
            _write_json(feedback_path, payload)
        kept_before = len(words)
        total = stage_total(settings, bool(parsed["remainder"]))
        state["stage_total"] = total
        checkpoints.begin(config)
        next_index = 5
        retake_summary = None
        if settings["remove_retakes"]:
            stage("Checking for retakes…", next_index, total)
            next_index += 1
            retake_summary = _run_retake_pass(
                config,
                "meta" if config.signin_key() else "mock",
                spoken_direction=direction_text,
                keep_mode=settings["keep_mode"],
                style=settings.get("style"),
                strict=settings.get("retake_strict"),
                mode=settings.get("mode", "cleanest"),
            )
            retake_audit = retake_summary.pop("audit", None)
            if retake_audit is not None:
                audit_path = session / "operations_audit.json"
                if audit_path.is_file():
                    existing = _load_json(str(audit_path))
                    existing.setdefault("operations", []).extend(
                        retake_summary["operations"]
                    )
                    existing["after"] = retake_audit["after"]
                    _write_json(audit_path, existing)
                else:
                    _write_json(audit_path, retake_audit)
        filler_summary = None
        if settings.get("remove_fillers"):
            stage("Taking out the ums and uhs…", next_index, total)
            next_index += 1
            filler_summary = _run_filler_pass(config, settings)
        # The opening rule: the edit starts on the first real line.
        _run_opening_pass(config)
        silence_summary = None
        if settings["cut_silence"]:
            stage("Cutting the silence…", next_index, total)
            next_index += 1
            silence_summary = _run_silence_pass(
                config, settings["buffer_s"]
            )
        direction_summary = None
        if parsed["remainder"]:
            stage("Reading your direction…", next_index, total)
            next_index += 1
            try:
                direction_summary = _run_direction_pass(
                    config, parsed["remainder"], settings
                )
            except Exception as exc:  # the reading never fails the edit
                _log_raw_error("direction pass", exc)
                direction_summary = {
                    "ran": False,
                    "reason": "error",
                    "applied": 0,
                    "flagged_ops": 0,
                    "flags": [],
                    "lines": [
                        "The rest of your direction was saved as "
                        "overall guidance for the model."
                    ],
                    "usage": [],
                }
        # SMOOTH, NEVER CHOPPY (Message 2R, P0): edges on the
        # measured quiet and the join check, before the render.
        smooth_summary = _run_smooth_pass(config)
        # The final sweep (0.27.3): the settled plan's last
        # look, inside the First edit checkpoint.
        _run_final_sweep(config)
        # Same-point review spots (0.27): read off the settled
        # plan, recorded before the First edit checkpoint.
        _record_same_point_spots(config)
        checkpoints.commit(config, "First edit")
        transcript = _load_json(str(job_root / "transcript.json"))
        timeline = Timeline(transcript, _effective_cuts(config))
        kept_after = len(timeline.kept_word_indices())
        stage("Building your review video…", total, total)
        render_note = _render_session_splice(
            config, video_path, bool(media.get("has_audio", True))
        )
        caption_info = None
        if captions_on:
            try:
                caption_info = clean_captions.build_captions(
                    job_root,
                    _effective_cuts(config),
                    session / clean_captions.CAPTIONS_FILENAME,
                )
            except Exception as exc:  # captions never fail the edit
                _log_raw_error("clean captions", exc)
                caption_info = None
        shutil.rmtree(work, ignore_errors=True)
        recap = list(parsed["recap"])
        # The length change, said plainly at the head of the
        # recap (Justin's call): the splice keeps only the
        # talking parts, so the edit can be much shorter than
        # the recording, and the creator should never have to
        # discover that from the player.
        recap.insert(
            0,
            f"Your video was {_format_clock(timeline.source_duration)}. "
            f"The edit is {_format_clock(timeline.output_duration)}.",
        )
        if reuse_line is not None:
            # Said right under the length line, where the cost of
            # the run is talked about: the words came from the
            # saved copy, and no provider was paid again.
            recap.insert(1, reuse_line)
        if retake_summary is not None:
            # One count (0.25, finding 6): the recap reads the
            # central counts computed from the final plan, not
            # the pass's own operation tally. The tally counts
            # operations and flags; the plan counts copies
            # removed (settled strikes included) and copies
            # still waiting, which is what every other surface
            # shows.
            try:
                final_counts = _edit_counts(config, timeline=timeline)
            except Exception:
                final_counts = None
            if final_counts is not None:
                recap.append(
                    "Retakes: "
                    f"{final_counts['retakes_removed']} removed, "
                    f"{final_counts['retakes_set_aside']} set aside "
                    "for your call."
                )
            # No fallback tally (0.26, fix 3): when the one count
            # cannot be read, the line is left out rather than
            # recounted from the pass's own operations, which is
            # how the recap and the Done bubble came to disagree.
            if retake_summary.get("unsure"):
                recap.append(
                    f"{retake_summary['unsure']} of those cuts were "
                    "unsure ones, marked in the transcript. "
                    + retake_summary.get(
                        "unsure_note", retakes.unsure_note(settings)
                    )
                )
            for event in retake_summary.get("route_events") or []:
                recap.append(
                    f"{ROUTE_LABELS.get(event.get('requested'), 'The hosted route')} "
                    "could not be reached, so the retake check "
                    "ran on Meta."
                )
        if filler_summary is not None:
            recap.extend(filler_summary.get("lines", []))
        if silence_summary is not None:
            recap.append(
                "Silence: "
                f"{silence_summary.get('joins_trimmed', 0)} joins "
                f"trimmed toward {settings['buffer_s']:g} seconds."
            )
        if settings.get("crossfade", True):
            # The first edit always builds its review video with
            # the app's own splice, so the crossfade the recap
            # names here really is in the video just built.
            recap.append(
                "Smooth the joins was on: the sound fades across "
                "each join for 40 milliseconds in the built "
                "video. The picture still cuts."
            )
        if direction_summary is not None:
            # The direction lines come last: they end with the flag
            # recaps, the things left for the creator to see.
            recap.extend(direction_summary.get("lines", []))
        elif parsed["remainder"]:
            recap.append(
                "The rest of your direction was saved as overall "
                "guidance for the model."
            )
        # Saved creator rules ride into the passes that read them:
        # the retake judgment and the direction read both take the
        # saved statements as guidance. When one of those passes
        # ran, the recap names each saved rule it carried, in plain
        # words, so the creator can see a rule saved on Train
        # working on the next video.
        applied_rule_statements = (
            creator_rules.active_rule_statements(_rules_store(config))
            if (
                retake_summary is not None
                or (direction_summary is not None
                    and direction_summary.get("ran"))
            )
            else []
        )
        for statement in applied_rule_statements:
            recap.append(f"Saved rule applied: {statement}")
        # What the first edit cost, per feature, from the passes'
        # own records (v0.7.2): one line per feature that ran and
        # a total, just before the recap's closing lines.
        cost_entries = _first_edit_cost_entries(
            route, duration, settings, retake_summary, direction_summary,
            reused=transcript_reused,
        )
        _account_costs(config, cost_entries)
        cost_block = costs.job_cost_block(cost_entries)
        if cost_block is not None:
            recap.extend(cost_block["recap_lines"])
        # The recap closes by naming the way of working and the
        # spots waiting in gold, so the creator always knows how
        # involved this run was and what is theirs to call.
        recap.extend(_mode_and_gold_lines(config, settings))
        passes = {
            "retakes": _state_summary(retake_summary),
            "filler": _state_summary(filler_summary),
            "silence": _state_summary(silence_summary),
            "direction": _state_summary(direction_summary),
            "smooth": _state_summary(smooth_summary),
        }
        # The run's length joins the record this computer
        # keeps of finished first edits (v0.20, chunk C): it
        # is what a later run's "usually about" line is
        # measured from. A words-only run (Transcribe on its
        # own: every pass off and no direction) is not a first
        # edit, so it is not recorded.
        if (
            settings.get("remove_retakes")
            or settings.get("cut_silence")
            or settings.get("remove_fillers")
            or direction_text.strip()
        ):
            _record_first_edit_run(
                httpd.config.launch_home,  # type: ignore[attr-defined]
                time.monotonic() - run_started_at,
            )
        state.update(
            _progress_fields(
                run_started_at,
                stage_marks["at"],
                total,
                total,
            )
        )
        state.update(
            {
                "status": "done",
                "stage": "The first edit is ready.",
                "percent": 100,
                "remaining_s": 0.0,
                "job_name": job_root.name,
                "engine": result["engine"],
                "saved_transcript_reused": transcript_reused,
                "words": kept_before,
                "kept_before": kept_before,
                "kept_after": kept_after,
                "settings": settings,
                "recap": recap,
                "lines": recap,
                "saved_rules": applied_rule_statements,
                "costs": cost_block,
                "passes": passes,
                "flags": (
                    direction_summary.get("flags", [])
                    if direction_summary
                    else []
                ),
                "gold": _open_gold_spans(config),
                "render_note": render_note,
                "captions": caption_info,
            }
        )
        try:
            _append_run_summary_thread(
                config,
                recap,
                _claims_for_spans(_edit_spans(config)),
            )
        except Exception:
            pass
        _persist_run_state(
            httpd.config.launch_home,  # type: ignore[attr-defined]
            state, "done", run_started_wall, intake_id,
        )
        session_log.log_stage(
            config.session_dir, "first edit", "finish"
        )
    except speech.TranscribeError as exc:
        session_log.log_stage(
            front_session, "first edit", "fail",
            exc.detail or str(exc),
        )
        _log_raw_error("first edit", exc)
        state.update({"status": "failed", "message": str(exc)})
        _persist_run_state(
            httpd.config.launch_home,  # type: ignore[attr-defined]
            state, "failed", run_started_wall, intake_id,
        )
    except Exception as exc:  # the page still gets one plain sentence
        session_log.log_stage(
            front_session, "first edit", "fail",
            f"{type(exc).__name__}: {exc}",
        )
        _log_raw_error("first edit", exc)
        state.update(
            {
                "status": "failed",
                "message": (
                    "The first edit could not be made. The video "
                    "itself is unchanged, and dropping it again "
                    "is safe."
                ),
            }
        )
        _persist_run_state(
            httpd.config.launch_home,  # type: ignore[attr-defined]
            state, "failed", run_started_wall, intake_id,
        )


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
    new_status = Timeline(transcript, revised).word_status(word_index)
    why_prompt = None
    checkpoints.begin(config)
    if status == "cut" and new_status == "kept":
        # The click brought a cut word back: that is a grade on
        # the cut, and the page asks why about this record. The
        # record is written before the checkpoint lands, so it
        # is part of this change's checkpoint like the words.
        entry_id = _record_restore_grade(
            config, word_index, word_index + 1, "word-toggle"
        )
        if entry_id:
            why_prompt = {"entry_id": entry_id}
    _write_json(session / "revised_cuts.json", revised)
    checkpoints.commit(config, "Transcript word change")
    result = {
        "ok": True,
        "word_index": word_index,
        "status": new_status,
        "changed": new_status != status,
        "message": None,
    }
    if why_prompt is not None:
        result["why_prompt"] = why_prompt
    return result


def _apply_word_range(
    config: WebAppConfig, word_start: int, word_end: int, action: str
) -> dict:
    """Cut or bring back a whole highlighted phrase of words.

    The Descript-shaped sibling of the word click (v0.8.0): the
    creator highlights a run of words and erases it, or picks a
    struck-through run and brings it back, in one step. The words
    travel the same machinery as every other transcript edit: one
    ``cut_words`` / ``restore_words`` operation per contiguous
    run, validated and checked by the safety verifier against the
    plan as it evolves, applied by the applier, and landed in the
    session's ``revised_cuts.json`` under ONE checkpoint, so a
    phrase edit is one undo step, never one step per word.

    Three kinds of words are never moved by this path, and come
    back in ``stayed`` with a plain reason each:

    - Words inside an open gold span. The editor was not sure
      about those, so the call belongs to the span's own gold
      card, not to a highlight; the page opens that card when the
      answer names the span.
    - Words inside a protected span (the creator profile's, or a
      hold in the plan). They are partitioned out before any
      operation is built, exactly as if the creator had clicked
      around them: a highlight that reaches into a protected part
      still moves the words on either side of it.
    - Words inside a special insert, which no transcript edit
      can move.

    A run the verifier holds anyway is reported the same way,
    with the same plain sentence a held word gets. As with a word
    click, a lone ``wonder_review_required`` hold is forgiven
    (the creator is looking at the exact words; the highlight is
    the review); every other rule holds exactly as written.
    """
    if action not in ("cut", "restore"):
        raise ValueError("That action is not one this page can do.")
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    words = transcript.get("words", [])
    if not (0 <= word_start < word_end <= len(words)):
        raise ValueError("Those words are not in the transcript.")
    cutting = action == "cut"
    target_status = "kept" if cutting else "cut"
    base_cuts = _effective_cuts(config)
    timeline = Timeline(transcript, base_cuts)
    profile = _load_optional_file(config.job_dir / "profile.json")

    stayed: list = []
    gold_ranges: list = []
    for view in _open_gold_spans(config):
        start = max(word_start, view["word_start"])
        end = min(word_end, view["word_end"])
        if start < end:
            gold_ranges.append((start, end))
            stayed.append(
                {
                    "word_start": start,
                    "word_end": end,
                    "why": "gold",
                    "gold_id": view.get("gold_id"),
                    "message": (
                        "That spot is marked as recommended to "
                        "remove, waiting for your call on its "
                        "own card."
                    ),
                }
            )
    protected_ranges: list = []
    for span in timeline.protected_spans(profile):
        start = max(word_start, int(span["word_start"]))
        end = min(word_end, int(span["word_end"]))
        if start < end:
            protected_ranges.append((start, end))
            stayed.append(
                {
                    "word_start": start,
                    "word_end": end,
                    "why": "protected",
                    "gold_id": None,
                    "message": plain_reason_sentence(
                        f"protected_span:{span.get('label', '')} "
                        f"({span.get('source', '')})"
                    ),
                }
            )

    def in_gold(index: int) -> bool:
        return any(s <= index < e for s, e in gold_ranges)

    def in_protected(index: int) -> bool:
        return any(s <= index < e for s, e in protected_ranges)

    runs: list = []
    run: list = []
    special_run: list = []

    def flush_run() -> None:
        nonlocal run
        if run:
            runs.append((run[0], run[-1] + 1))
            run = []

    def flush_special() -> None:
        nonlocal special_run
        if special_run:
            stayed.append(
                {
                    "word_start": special_run[0],
                    "word_end": special_run[-1] + 1,
                    "why": "special",
                    "gold_id": None,
                    "message": (
                        "Those words are inside a special insert, "
                        "so they cannot be changed here."
                    ),
                }
            )
            special_run = []

    for index in range(word_start, word_end):
        if in_gold(index) or in_protected(index):
            flush_run()
            flush_special()
            continue
        status = timeline.word_status(index)
        if status == "special":
            flush_run()
            special_run.append(index)
            continue
        flush_special()
        if status == target_status:
            run.append(index)
        else:
            flush_run()
    flush_run()
    flush_special()

    moved = 0
    why_prompt = None
    if runs:
        current_cuts = base_cuts
        current_timeline = timeline
        applied: list = []
        for run_start, run_end in runs:
            operation = {
                "type": "cut_words" if cutting else "restore_words",
                "comment_id": "transcript-phrase",
                "word_start": run_start,
                "word_end": run_end,
                "reason": (
                    "You highlighted these words in the transcript "
                    "to cut them."
                    if cutting
                    else "You highlighted these words in the "
                    "transcript to bring them back."
                ),
                "confidence": 1.0,
                "model": "mock",
                "risk_tier": 1,
                "wonder_reviewed": False,
            }
            ops = validate_operations([operation])
            verdict = verify_operations(
                ops, current_timeline, profile
            )[0]
            if (
                verdict.status == "flag"
                and verdict.reasons == ["wonder_review_required"]
            ):
                verdict.status = "apply"
            if verdict.status != "apply":
                stayed.append(
                    {
                        "word_start": run_start,
                        "word_end": run_end,
                        "why": "protected",
                        "gold_id": None,
                        "message": _plain_toggle_message(verdict),
                    }
                )
                continue
            current_cuts, _audit = apply_verdicts(
                current_cuts, current_timeline, [verdict]
            )
            current_timeline = Timeline(transcript, current_cuts)
            applied.append((run_start, run_end))
            moved += run_end - run_start
        if applied:
            session = config.session_dir
            session.mkdir(parents=True, exist_ok=True)
            checkpoints.begin(config)
            if action == "restore" and moved > 0:
                # One grade record for the whole phrase the
                # creator asked to bring back, covering the full
                # range they picked, however many runs moved.
                entry_id = _record_restore_grade(
                    config, word_start, word_end, "word-range"
                )
                if entry_id:
                    why_prompt = {"entry_id": entry_id}
            _write_json(session / "revised_cuts.json", current_cuts)
            checkpoints.commit(
                config,
                "Transcript phrase cut"
                if cutting
                else "Transcript phrase brought back",
            )
    stayed.sort(key=lambda entry: entry["word_start"])
    result = {
        "ok": True,
        "changed": moved > 0,
        "moved": moved,
        "stayed": stayed,
    }
    if why_prompt is not None:
        result["why_prompt"] = why_prompt
    return result


def _apply_gap_erase(
    config: WebAppConfig,
    after_word_index: int,
    before_word_index: int,
    target_total_s: Optional[float] = None,
) -> dict:
    """Shorten the quiet between two words (v0.15, item 24).

    The creator double-clicked a word gap in the transcript and
    erased inside it. A gap's silence lives in the plan only
    when the gap sits on a join — the meeting point of two kept
    ranges, whose pause is the tail of quiet at the end of the
    first range plus the lead-in at the start of the second,
    recorded in ``join_pauses_s``. Those gaps are trimmed by
    the one existing pause path: a single ``set_join_pause``
    operation, validated and checked by the safety verifier
    and applied by the applier exactly as the silence pass's
    own proposals are, and landed in ``revised_cuts.json``
    under ONE checkpoint, so the trim is one undo step.

    A gap inside a single kept range has no pause entry of
    its own in the plan, so this path cannot trim it without
    inventing a second editing model; the answer says so
    plainly and changes nothing. A join a pinned comment or a
    protected moment covers is likewise left alone, with the
    same protection the silence pass gives it.
    """
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    words = transcript.get("words", [])
    if not (
        0 <= after_word_index < len(words)
        and before_word_index == after_word_index + 1
        and before_word_index < len(words)
    ):
        raise ValueError("That gap is not in the transcript.")
    base_cuts = _effective_cuts(config)
    timeline = Timeline(transcript, base_cuts)
    # The picked gap sits between two transcript entries, and
    # cut words can sit between those entries and the join
    # itself (a gap the creator sees between two kept words
    # may have a cut word inside it). The join that carries
    # the gap's pause is the one between the kept words that
    # surround the gap: the last kept word at or before the
    # gap's start and the first kept word at or after its end.
    prev_kept = None
    next_kept = None
    for index in range(len(words)):
        status = timeline.word_status(index)
        if status == "kept" and index <= after_word_index:
            prev_kept = index
        if status == "kept" and index >= before_word_index and next_kept is None:
            next_kept = index
    join_index = None
    if prev_kept is not None and next_kept is not None:
        for join in timeline.joins():
            index = join["join_index"]
            prev_words = timeline.words_in_range(timeline.ranges[index])
            next_words = timeline.words_in_range(timeline.ranges[index + 1])
            if (
                prev_words and next_words
                and prev_words[-1] == prev_kept
                and next_words[0] == next_kept
            ):
                join_index = index
                break
    if join_index is None:
        return {
            "ok": True,
            "changed": False,
            "join_index": None,
            "message": (
                "That quiet sits inside one kept piece, so the "
                "edit plan holds no pause there to shorten. "
                "Gaps where two pieces meet can be trimmed here."
            ),
        }
    if join_index in _protected_join_indices(config, timeline):
        return {
            "ok": True,
            "changed": False,
            "join_index": join_index,
            "message": (
                "That spot is protected or has a note pinned on "
                "it, so its pause is left as it is."
            ),
        }
    prev_range = timeline.ranges[join_index]
    next_range = timeline.ranges[join_index + 1]
    prev_tail = float(prev_range["end"]) - float(words[after_word_index]["end"])
    head = float(words[before_word_index]["start"]) - float(next_range["start"])
    current_pause = prev_tail + head
    if target_total_s is not None:
        # A named total pause (0.27.3, chunk B item 8): the
        # chat offer trims the join to the creator's own pause
        # setting, not the floor. The head lead-in is what
        # set_join_pause moves, so the target is the named
        # total minus the tail that stays, inside the plan's
        # own clamps.
        target_head = float(target_total_s) - prev_tail
        target_head = max(MIN_JOIN_PAUSE_S, min(MAX_JOIN_PAUSE_S, target_head))
    else:
        # Erasing takes the join's pause down to the shortest
        # the plan allows, the same floor the silence pass
        # clamps to: the head lead-in is what set_join_pause
        # moves, so the target is the floor minus the tail
        # that stays.
        target_head = MIN_JOIN_PAUSE_S - prev_tail
        if target_head < MIN_JOIN_PAUSE_S:
            target_head = MIN_JOIN_PAUSE_S
        target_head = min(target_head, MAX_JOIN_PAUSE_S)
    if head <= target_head + 1e-3:
        return {
            "ok": True,
            "changed": False,
            "join_index": join_index,
            "message": "That gap is already as short as it can be.",
        }
    operation = {
        "type": "set_join_pause",
        "comment_id": "transcript-gap-erase",
        "join_index": join_index,
        "target_pause_s": round(target_head, 3),
        "reason": (
            "You picked the quiet between these words in the "
            "transcript and erased inside it."
        ),
        "confidence": 1.0,
        "model": "mock",
        "risk_tier": 1,
        "wonder_reviewed": False,
    }
    ops = validate_operations([operation])
    profile = _load_optional_file(config.job_dir / "profile.json")
    verdict = verify_operations(ops, timeline, profile)[0]
    if (
        verdict.status == "flag"
        and verdict.reasons == ["wonder_review_required"]
    ):
        # The creator picked this exact gap; the pick is the
        # review, as with a word click.
        verdict.status = "apply"
    if verdict.status != "apply":
        return {
            "ok": True,
            "changed": False,
            "join_index": join_index,
            "message": _plain_toggle_message(verdict),
        }
    revised, _audit = apply_verdicts(base_cuts, timeline, [verdict])
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    checkpoints.begin(config)
    _write_json(session / "revised_cuts.json", revised)
    checkpoints.commit(config, "Transcript gap shortened")
    new_pause = prev_tail + target_head
    return {
        "ok": True,
        "changed": True,
        "join_index": join_index,
        "target_pause_s": round(target_head, 3),
        "gap_before_s": round(current_pause, 3),
        "gap_after_s": round(new_pause, 3),
        "output_duration_s": round(
            Timeline(transcript, revised).output_duration, 4
        ),
        "message": (
            "Shortened the quiet there from about "
            f"{current_pause:.1f} seconds to about "
            f"{new_pause:.1f} seconds."
        ),
    }


def _apply_join_pause(
    config: WebAppConfig,
    after_word_index: int,
    before_word_index: int,
    target_total_s: float,
) -> dict:
    """Set one join's pause from the transcript (after-5:30
    exception, item 2).

    The creator clicked the shortened-pause mark between two
    kept words and named the pause that join should hold, as a
    total: the tail of quiet at the end of the first piece plus
    the lead-in at the start of the second, the plan's own
    ``join_pauses_s`` shape. The change travels the one
    existing pause path, a single ``set_join_pause`` operation
    validated and checked by the safety verifier and applied
    by the applier, and lands in ``revised_cuts.json`` under
    ONE checkpoint, so it is one undo step, and it moves that
    join only. The named total is clamped to the plan's own
    bounds for the moving head, and it can never exceed the
    pause actually recorded between the two words: what was
    not recorded cannot be put back.
    """
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    words = transcript.get("words", [])
    if not (
        0 <= after_word_index < len(words)
        and 0 <= before_word_index < len(words)
        and after_word_index < before_word_index
    ):
        raise ValueError("That gap is not in the transcript.")
    base_cuts = _effective_cuts(config)
    timeline = Timeline(transcript, base_cuts)
    if (
        timeline.word_status(after_word_index) != "kept"
        or timeline.word_status(before_word_index) != "kept"
    ):
        return {
            "ok": True,
            "changed": False,
            "join_index": None,
            "message": (
                "Those words are not both in the edit, so there "
                "is no join pause to set there."
            ),
        }
    join_index = None
    for join in timeline.joins():
        index = join["join_index"]
        prev_words = timeline.words_in_range(timeline.ranges[index])
        next_words = timeline.words_in_range(timeline.ranges[index + 1])
        if (
            prev_words and next_words
            and prev_words[-1] == after_word_index
            and next_words[0] == before_word_index
        ):
            join_index = index
            break
    if join_index is None:
        return {
            "ok": True,
            "changed": False,
            "join_index": None,
            "message": (
                "That quiet sits inside one kept piece, so the "
                "edit plan holds no pause there to set. Pauses "
                "where two pieces meet can be set here."
            ),
        }
    if join_index in _protected_join_indices(config, timeline):
        return {
            "ok": True,
            "changed": False,
            "join_index": join_index,
            "message": (
                "That spot is protected or has a note pinned on "
                "it, so its pause is left as it is."
            ),
        }
    prev_range = timeline.ranges[join_index]
    next_range = timeline.ranges[join_index + 1]
    prev_tail = float(prev_range["end"]) - float(words[after_word_index]["end"])
    head = float(words[before_word_index]["start"]) - float(next_range["start"])
    current_total = prev_tail + head
    recorded_total = (
        float(words[before_word_index]["start"])
        - float(words[after_word_index]["end"])
    )
    cap_total = min(recorded_total, prev_tail + MAX_JOIN_PAUSE_S)
    floor_total = prev_tail + MIN_JOIN_PAUSE_S
    lo = min(floor_total, current_total)
    hi = max(cap_total, current_total)
    target_total = max(lo, min(hi, float(target_total_s)))
    if abs(target_total - current_total) < 0.005:
        return {
            "ok": True,
            "changed": False,
            "join_index": join_index,
            "gap_before_s": round(current_total, 3),
            "gap_after_s": round(current_total, 3),
            "recorded_s": round(recorded_total, 3),
            "message": (
                "That pause is already as long as the recorded "
                "pause allows."
                if float(target_total_s) > current_total
                else "That pause is already as short as it can be."
            ),
        }
    target_head = round(target_total - prev_tail, 3)
    operation = {
        "type": "set_join_pause",
        "comment_id": "transcript-join-pause",
        "join_index": join_index,
        "target_pause_s": target_head,
        "reason": (
            "You set the quiet between these words in the "
            "transcript."
        ),
        "confidence": 1.0,
        "model": "mock",
        "risk_tier": 1,
        "wonder_reviewed": False,
    }
    ops = validate_operations([operation])
    profile = _load_optional_file(config.job_dir / "profile.json")
    verdict = verify_operations(ops, timeline, profile)[0]
    if (
        verdict.status == "flag"
        and verdict.reasons == ["wonder_review_required"]
    ):
        # The creator picked this exact join; the pick is the
        # review, as with a word click.
        verdict.status = "apply"
    if verdict.status != "apply":
        return {
            "ok": True,
            "changed": False,
            "join_index": join_index,
            "message": _plain_toggle_message(verdict),
        }
    revised, _audit = apply_verdicts(base_cuts, timeline, [verdict])
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    checkpoints.begin(config)
    _write_json(session / "revised_cuts.json", revised)
    checkpoints.commit(config, "Join pause set")
    new_total = prev_tail + target_head
    return {
        "ok": True,
        "changed": True,
        "join_index": join_index,
        "target_pause_s": target_head,
        "gap_before_s": round(current_total, 3),
        "gap_after_s": round(new_total, 3),
        "recorded_s": round(recorded_total, 3),
        "output_duration_s": round(
            Timeline(transcript, revised).output_duration, 4
        ),
        "message": (
            "Set the quiet there from about "
            f"{current_total:.1f} seconds to about "
            f"{new_total:.1f} seconds."
        ),
    }


# ---------------------------------------------------------------------------
# Manual timeline edits (v0.16): split, trim, delete, move, paste, fade
#
# Each operation is a pure transform in manual_edits applied to the
# session's current plan, landed in revised_cuts.json under ONE
# history checkpoint, exactly like a word click or a gap erase, so
# undo and redo cover them and the band, the transcript, and the
# edited clock follow from the one plan. A refusal is the
# transform's own plain sentence and changes nothing.
# ---------------------------------------------------------------------------


def _apply_timeline_edit(config: WebAppConfig, label: str, fn) -> dict:
    """Run one manual timeline transform and land it, or refuse."""
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    base_cuts = _effective_cuts(config)
    timeline = Timeline(transcript, base_cuts)
    profile = _load_optional_file(config.job_dir / "profile.json")
    try:
        new_cuts, info = fn(timeline, profile, base_cuts)
    except manual_edits.ManualEditRefused as exc:
        return {"ok": True, "changed": False, "message": str(exc)}
    if info.get("changed") is False:
        result = {"ok": True, "changed": False}
        result.update(info)
        return result
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    checkpoints.begin(config)
    _write_json(session / "revised_cuts.json", new_cuts)
    checkpoints.commit(config, label)
    result = {"ok": True, "changed": True}
    result.update(info)
    result["output_duration_s"] = round(
        Timeline(transcript, new_cuts).output_duration, 4
    )
    return result


def _trim_clamp_sentence(clamps: list) -> str:
    if "protected" in clamps:
        return " It stopped at a word you protected."
    if "next-piece" in clamps:
        return " It stopped at the next piece."
    if "min-length" in clamps:
        return " A piece keeps at least a little length."
    return ""


def _timeline_split(config: WebAppConfig, output_time: float) -> dict:
    result = _apply_timeline_edit(
        config,
        "Timeline piece split",
        lambda tl, _p, cuts: manual_edits.split_at_output_time(
            cuts, tl, output_time
        ),
    )
    if result.get("changed"):
        result["message"] = "Split the piece at the playhead."
    return result


def _timeline_trim(
    config: WebAppConfig, range_index: int, edge: str, new_time: float
) -> dict:
    def fn(tl, profile, cuts):
        return manual_edits.trim_edge(
            cuts, tl, range_index, edge, new_time, profile=profile
        )

    result = _apply_timeline_edit(config, "Timeline edge moved", fn)
    if result.get("changed"):
        result["message"] = "Moved the edge." + _trim_clamp_sentence(
            result.get("clamps") or []
        )
    elif "message" not in result:
        result["message"] = (
            "The edge is already there."
            + _trim_clamp_sentence(result.get("clamps") or [])
        )
    return result


def _timeline_delete(config: WebAppConfig, range_index: int) -> dict:
    result = _apply_timeline_edit(
        config,
        "Timeline piece removed",
        lambda tl, profile, cuts: manual_edits.delete_range(
            cuts, tl, range_index, profile=profile
        ),
    )
    if result.get("changed"):
        result["message"] = "Took that piece out. The edit closed up."
    return result


def _timeline_move(config: WebAppConfig, from_index: int, to_index: int) -> dict:
    result = _apply_timeline_edit(
        config,
        "Timeline piece moved",
        lambda tl, _p, cuts: manual_edits.move_range(
            cuts, tl, from_index, to_index
        ),
    )
    if result.get("changed"):
        result["message"] = "Moved the piece."
        if "special" in (result.get("clamps") or []):
            result["message"] += (
                " It stopped before the special insert."
            )
    elif "message" not in result:
        result["message"] = "The piece is already there."
    return result


def _timeline_paste(
    config: WebAppConfig, spans: list, output_time: float
) -> dict:
    result = _apply_timeline_edit(
        config,
        "Timeline piece pasted",
        lambda tl, _p, cuts: manual_edits.paste_spans(
            cuts, tl, spans, output_time
        ),
    )
    if result.get("changed"):
        count = result.get("count", 1)
        result["message"] = (
            "Pasted the piece at the playhead."
            if count == 1
            else f"Pasted {count} pieces at the playhead."
        )
    return result


def _timeline_fade(
    config: WebAppConfig, join_index: int, fade_s
) -> dict:
    result = _apply_timeline_edit(
        config,
        "Timeline crossfade set",
        lambda tl, _p, cuts: manual_edits.set_join_fade(
            cuts, tl, join_index, fade_s
        ),
    )
    if result.get("changed"):
        fade = result.get("fade_s") or 0.0
        result["message"] = (
            "Took the crossfade off that cut."
            if not fade
            else f"That cut now crossfades over {fade:g} seconds."
        )
    return result


def _join_call(config: WebAppConfig, join_index: int, move: str) -> dict:
    """Settle one join the join check left for the creator's call.

    "smooth" applies the remedy the check itself uses for a
    join that sounds abrupt, the long crossfade, through the
    timeline fade path (with its checkpoint, so the bubble's
    Undo covers it). "keep" changes no sound at all. Either
    way the spot leaves the smooth pass's your-call list, so
    it is not offered again and the counts every surface
    reads agree that it is settled.
    """
    payload = _load_session_json(config, "smooth_pass.json", {})
    calls = payload.get("your_call") if isinstance(payload, dict) else None
    if not isinstance(calls, list):
        raise ValueError("That join is not waiting for a call anymore.")
    spot = next(
        (
            c
            for c in calls
            if isinstance(c, dict) and c.get("join_index") == join_index
        ),
        None,
    )
    if spot is None:
        raise ValueError("That join is not waiting for a call anymore.")
    if move == "smooth":
        result = _timeline_fade(config, join_index, 0.1)
        if not result.get("changed"):
            raise ValueError(
                result.get("message")
                or "That join could not be smoothed."
            )
    payload["your_call"] = [c for c in calls if c is not spot]
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(config.session_dir / "smooth_pass.json", payload)
    return {"ok": True, "join_index": join_index, "move": move}


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
    why_prompt = None
    checkpoints.begin(config)
    if action == "restore":
        # Bringing an edit back is a grade on the cut that made
        # it, over the edit's own span.
        entry_id = _record_restore_grade(config, start, end, "edit-action")
        if entry_id:
            why_prompt = {"entry_id": entry_id}
    _write_json(session / "revised_cuts.json", revised)
    checkpoints.commit(config, label)
    if action == "swap":
        # A swap is the creator naming their real keeper: the other
        # take. Settle any shadow flip on this pair against it.
        meta = span.get("retake") or {}
        pair = _retake_pair_for(config, meta.get("pair_id"))
        if pair is not None:
            record = _load_retake_record(config)
            if _resolve_shadow_match(record, pair, "other", "swap"):
                _save_retake_record(config, record)
    result = {"ok": True, "changed": True, "message": done_message}
    if why_prompt is not None:
        result["why_prompt"] = why_prompt
    return result


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
    "Your video is in, and you can watch it below. This app can "
    "also make the first edit itself: it writes down the words, "
    "lays out a first plan, and runs the same choices the front "
    "door offers. Give any direction you have, then choose Make "
    "the edit."
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


def _staged_copy_path(source: Path) -> Path:
    """Where a staged video's watchable copy lives.

    The same session playback folder the job will use once the
    drop becomes a job (a lone video's job is built in its staging
    folder), so the copy is built once, at drop time, and the job
    simply inherits it. The staged original is never touched.
    """
    suffix = source.suffix.lstrip(".").lower()
    return (
        source.parent
        / SESSION_DIR_NAME
        / PLAYBACK_DIR_NAME
        / f"{source.stem}-{suffix}.playback.mp4"
    )


def _staged_playback_target(source: Path) -> Optional[Path]:
    """The file to serve for a staged video, or None while its
    watchable copy is still being made or could not be made."""
    if not _needs_copy(source):
        return source
    copy_path = _staged_copy_path(source)
    if _copy_ready(copy_path):
        return copy_path
    _ensure_playback_copy_for(source, copy_path)
    return None


def _ordered_drop_videos(base: Path, ordered: list) -> list:
    """The drop's video files in the order they were brought in.

    The intake file route records each file as it arrives, so the
    recorded order is the order the parts were sent in, which the
    intake pop-up lets the creator arrange before anything is
    uploaded (v0.15 Batch C, item 2). Only files that landed and
    are videos count, each once.
    """
    seen = set()
    videos = []
    for rel in ordered or []:
        parts = _safe_relative_parts(str(rel))
        if not parts:
            continue
        path = base.joinpath(*parts)
        key = "/".join(parts)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            videos.append(path)
    return videos


def _join_ordered_parts(videos: list, out_path: Path) -> bool:
    """Join video parts into one file, in the given order.

    One ffmpeg pass with the concat filter, re-encoded the same
    way the intake render encodes, so the joined file is the
    single video the first edit then processes: the parts order
    is the processing order, end to end. Any failure (a part
    ffmpeg cannot read, no sound stream to join) returns False
    and the caller falls back to the first part alone, saying so.
    """
    if len(videos) < 2:
        return False
    inputs = []
    for video in videos:
        inputs += ["-i", str(video)]
    pieces = "".join(
        f"[{i}:v][{i}:a]" for i in range(len(videos))
    )
    graph = (
        pieces + f"concat=n={len(videos)}:v=1:a=1[outv][outa]"
    )
    scratch = out_path.with_name(out_path.stem + ".joining.mp4")
    command = [
        "ffmpeg", "-y", "-v", "error",
        *inputs,
        "-filter_complex", graph,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(scratch),
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=1800
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0 or not scratch.is_file()             or scratch.stat().st_size == 0:
        scratch.unlink(missing_ok=True)
        return False
    scratch.replace(out_path)
    return True


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
# Voice commands (v0.11.0, fix item 8)
#
# The local match lives in voice_commands.py and is pure text
# work: a matched command returns at once, with no model call,
# and the page fires the same handler the command's button
# fires. Only words that match nothing can go further, here:
# direction through the existing direction pass when a key is
# set and the words read as direction, otherwise a feedback
# note. Every outcome writes one record line to the session
# log, naming what was heard and what was done, and the same
# line goes back to the page for its recap and history.
# ---------------------------------------------------------------------------

VOICE_NOTE_SENTENCE = "I saved your words as a note."


def _record_voice_line(config: WebAppConfig, record: str) -> None:
    session_log.log_stage(
        config.session_dir, "voice command", "finish", record
    )


def _save_voice_note(config: WebAppConfig, text: str) -> None:
    """Save spoken words as an overall feedback note.

    The same feedback store the comments routes write, under
    its own checkpoint, so the note is in the history like any
    other saved note and goes with the next direction run as
    overall guidance.
    """
    session = config.session_dir
    session.mkdir(parents=True, exist_ok=True)
    payload = {
        "video": config.job_dir.name if config.job_dir else "",
        "feedback_done": False,
        "comments": [],
        "overall_feedback": [],
    }
    existing_path = _feedback_path(config)
    if existing_path is not None:
        try:
            existing = _load_json(str(existing_path))
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict):
            payload["video"] = str(
                existing.get("video") or payload["video"]
            )
            payload["feedback_done"] = bool(
                existing.get("feedback_done", False)
            )
            if isinstance(existing.get("comments"), list):
                payload["comments"] = existing["comments"]
            payload["overall_feedback"] = _overall_notes(existing)
    payload["overall_feedback"].append(
        {"id": f"voice-{uuid.uuid4().hex[:12]}", "text": text}
    )
    checkpoints.begin(config)
    _write_json(session / "feedback.json", payload)
    checkpoints.commit(config, "Voice words saved as a note")


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


# POST routes that work on the loaded job. With no job loaded they
# are refused with NO_JOB_MESSAGE before any handler runs. Intake,
# the sample load, and the sign-in routes stay open at the front
# door: they are how a job (or a sign-in) arrives in the first place.
# The test-video route stays open there too, on purpose: the mark
# can be set before a job exists, in the launch session, and a drop
# that becomes the job carries it into the job's own session.
# The rule routes (/api/rule-save, /api/rule-dismiss, /api/rule-remove)
# stay open at the front door too: saved rules are creator-level, so
# Train can save them with no job loaded.
# The thread's two write routes (/api/thread for an append,
# /api/thread/message for a send) stay open at the front door too
# (0.25, finding 5): the conversation starts before any job exists,
# so a first message must be able to land in the launch session's
# thread instead of being refused and lost. When a drop becomes
# the job, those entries carry into the job's thread
# (_carry_launch_thread). The routes that act on a job's own
# records (thread action, read, cleanup) stay job-required.
_JOB_REQUIRED_POSTS = frozenset({
    "/api/comments",
    "/api/direct",
    "/api/front-door",
    "/api/edit-note",
    "/api/grades",
    "/api/word-toggle",
    "/api/word-range",
    "/api/gap-erase",
    "/api/join-pause",
    "/api/timeline/split",
    "/api/timeline/trim",
    "/api/timeline/delete",
    "/api/timeline/move",
    "/api/timeline/paste",
    "/api/timeline/fade",
    "/api/edit-action",
    "/api/gold-verdict",
    "/api/join-call",
    "/api/why",
    "/api/word-correct",
    "/api/word-correct-remove",
    "/api/captions-pref",
    "/api/render",
    "/api/finish/versions",
    "/api/finish/offset",
    "/api/finish/render",
    "/api/finish/export",
    "/api/finish/reveal",
    "/api/project/save",
    "/api/project/open",
    "/api/history/undo",
    "/api/history/redo",
    "/api/voice-command",
    "/api/thread/action",
    "/api/thread/read",
    "/api/thread/cleanup",
    "/api/review/verdict",
})


# ---------------------------------------------------------------------------
# Chat export job (0.27.3, chunk B item 9): the Export bubble's
# renders run here, one shape at a time, always from the
# CURRENT plan (every export renders; nothing is served from
# an older render). Per-shape state feeds the bubble's bar
# and its "16:9 done · 9:16 72% · about 40 s left" line; the
# per-shape percent while a render runs is elapsed against
# the usual render length learned on this computer (the
# finish-export-runs.json record beside the first-edit one),
# capped below 100 until the render actually returns.

FINISH_EXPORT_RUNS_NAME = "finish-export-runs.json"
FINISH_EXPORT_RUNS_KEPT = 8
FINISH_EXPORT_DEFAULT_S = 60.0


def _finish_export_usual_seconds(launch_home, aspect) -> float:
    """The usual render length for one shape on this computer."""
    try:
        if launch_home is None:
            return FINISH_EXPORT_DEFAULT_S
        path = Path(launch_home) / FINISH_EXPORT_RUNS_NAME
        if not path.is_file():
            return FINISH_EXPORT_DEFAULT_S
        runs = json.loads(path.read_text(encoding="utf-8"))
        values = (runs or {}).get(aspect) if isinstance(runs, dict) else None
        values = [
            float(v) for v in (values or [])
            if isinstance(v, (int, float)) and v > 0
        ]
        if not values:
            return FINISH_EXPORT_DEFAULT_S
        return float(statistics.median(values[-FINISH_EXPORT_RUNS_KEPT:]))
    except Exception:
        return FINISH_EXPORT_DEFAULT_S


def _record_finish_export_run(launch_home, aspect, seconds) -> None:
    try:
        if launch_home is None:
            return
        path = Path(launch_home) / FINISH_EXPORT_RUNS_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            runs = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(runs, dict):
                runs = {}
        except Exception:
            runs = {}
        values = runs.get(aspect) or []
        values.append(round(float(seconds), 1))
        runs[aspect] = values[-FINISH_EXPORT_RUNS_KEPT:]
        path.write_text(json.dumps(runs) + "\n", encoding="utf-8")
    except Exception:
        pass


def _finish_export_view(state: dict, launch_home) -> dict:
    """One export job as the page reads it, with progress."""
    now = time.monotonic()
    shapes = {}
    remaining = 0.0
    for aspect, shape in (state.get("shapes") or {}).items():
        view = dict(shape)
        usual = _finish_export_usual_seconds(launch_home, aspect)
        if shape.get("state") == "working":
            started = shape.get("started_at") or now
            elapsed = max(0.0, now - started)
            view["percent"] = int(min(95, elapsed / usual * 100)) \
                if usual > 0 else 0
            remaining += max(0.0, usual - elapsed)
        elif shape.get("state") == "done":
            view["percent"] = 100
        elif shape.get("state") == "queued":
            view["percent"] = 0
            remaining += usual
        shapes[aspect] = view
    return {
        "id": state.get("id"),
        "status": state.get("status"),
        "aspects": list(state.get("aspects") or []),
        "shapes": shapes,
        "eta_s": round(remaining) if state.get("status") == "working" else 0,
        "error": state.get("error"),
    }


def _run_finish_export(registry: dict, export_id: str, config) -> None:
    """Render the job's shapes in order, recording state."""
    state = registry.get(export_id)
    if state is None:
        return
    try:
        # One job, one plan (filming day, P0-2): the plan is
        # snapshotted once, here, and every shape in the job
        # renders from that snapshot. Justin's keyed "Export
        # both" shipped two different edits because each shape
        # re-read the plan file at its own render moment and a
        # revision landed between them; a job can no longer
        # split that way.
        plan = json.loads(json.dumps(_effective_cuts(config)))
        state["plan_stamp"] = _plan_stamp_of(plan)
        for aspect in state["aspects"]:
            shape = state["shapes"][aspect]
            shape["state"] = "working"
            shape["started_at"] = time.monotonic()
            started = time.monotonic()
            try:
                result = _render_finish_shape(config, aspect, plan=plan)
            except Exception as exc:  # one shape must not strand the rest
                result = {"ok": False, "error": str(exc)}
            took = time.monotonic() - started
            if result.get("ok"):
                _record_finish_export_run(
                    config.launch_home, aspect, took
                )
                path = config.session_dir / finish.finish_filename(aspect)
                duration = None
                try:
                    duration = speech.probe_media(path).get("duration")
                except Exception:
                    duration = None
                shape["state"] = "done"
                shape["file"] = finish.finish_filename(aspect)
                shape["duration_s"] = duration
            else:
                shape["state"] = "error"
                shape["error"] = result.get("error") or "Export failed."
        states = [s.get("state") for s in state["shapes"].values()]
        state["status"] = "done" if all(
            s == "done" for s in states
        ) else "error"
        if state["status"] == "error":
            state["error"] = next(
                (
                    s.get("error")
                    for s in state["shapes"].values()
                    if s.get("state") == "error"
                ),
                "Export failed.",
            )
    except Exception as exc:  # the job record must always settle
        state["status"] = "error"
        state["error"] = str(exc)


def _render_finish_shape(
    config: WebAppConfig, aspect: str, plan: dict = None
) -> dict:
    """Render one shape's finished video from one plan.

    The body of the Finish export (see _post_finish_render
    for the full story), lifted out so the chat export job
    renders through exactly the same path: same splice,
    same framing correction loop, same -14 LUFS finish,
    same measured entry, now stamped with the plan it was
    rendered from (0.27.3, chunk B item 9). A caller running
    a multi-shape job passes its one snapshotted plan, so
    every shape in the job is the same edit (filming day,
    P0-2); a lone render reads the current plan. Returns
    the payload the endpoint sends.
    """
    session = config.session_dir
    if not config.has_job or not (
        session / "revised_cuts.json"
    ).is_file():
        return {
                "ok": False,
                "error": "Make the edit first, so there is an "
                "edit to finish.",
            }
    source = _finish_source(config)
    if source is None:
        return {
                "ok": False,
                "error": "The video this job came from is not "
                "in the folder anymore, so the finished video "
                "cannot be built.",
            }
    try:
        media = speech.probe_media(source)
        has_audio = bool(media.get("has_audio", True))
        width = int(media.get("width") or 0)
        height = int(media.get("height") or 0)
    except speech.TranscribeError:
        has_audio, width, height = True, 0, 0
    window, ref, _notes = _finish_window(config, aspect)
    if window is None or not width or not height:
        return {
                "ok": False,
                "error": "This video's size could not be read, "
                "so its framing could not be set.",
            }
    x, y, w, h = framing.crop_pixels(window, width, height)
    out_w, out_h = finish.finish_output_size(aspect, width, height)
    # The correction loop iterates at proxy size when the
    # full frame is bigger than the proxy (finish.proxy_
    # output_size); its renders go to a scratch file that
    # is removed afterwards, and the delivered file is
    # rendered once at full frame below. When the full
    # frame already is proxy-sized, the loop's renders are
    # the delivered renders and there is no second pass.
    loop_w, loop_h = finish.proxy_output_size(out_w, out_h)
    iterate_at_proxy = (loop_w, loop_h) != (out_w, out_h)
    crossfade_on = bool(
        _front_door_settings(config).get("crossfade", True)
    )
    out_path = session / finish.finish_filename(aspect)
    loop_path = (
        session / (out_path.stem + ".proxy.mp4")
        if iterate_at_proxy
        else out_path
    )
    cuts = plan if plan is not None else _effective_cuts(config)
    new_profile = _finish_new_profile(config)
    ref_face = (
        ref.get("face") if ref and ref.get("found") else None
    )
    corrections = 0
    result = None
    profile = None
    report = {"ok": False}
    try:
        while True:
            result = finish.render_finish_video(
                source,
                config.job_dir,
                cuts,
                loop_path,
                (x, y, w, h, loop_w, loop_h),
                crossfade_s=0.04 if crossfade_on else 0.0,
                has_audio=has_audio,
            )
            # The output is read with the one ruler, the
            # same read the reference was learned with.
            profile = framing.ruler_profile(loop_path)
            report = framing.match_report(ref, profile)
            # Correct the crop's size from the measured
            # share and render again, at most twice, while
            # the size misses the 3 percent bar and every
            # number the correction needs is really there.
            if not (
                report.get("ok")
                and window.get("matched")
                and ref_face
                and new_profile
                and new_profile.get("found")
                and float(report.get("size_off_pct") or 0.0) > 3.0
                and corrections < 2
            ):
                break
            measured_share = float(profile["face"]["width"])
            ref_share = float(ref_face["width"])
            if measured_share <= 0 or ref_share <= 0:
                break
            window = framing.scale_window(
                window,
                ref,
                new_profile,
                width,
                height,
                aspect,
                measured_share,
            )
            x, y, w, h = framing.crop_pixels(window, width, height)
            corrections += 1
        if iterate_at_proxy:
            # The delivered file, rendered once at full
            # frame with the crop the proxy loop settled,
            # then measured with the ruler: the stored
            # match line is this render's own measurement.
            result = finish.render_finish_video(
                source,
                config.job_dir,
                cuts,
                out_path,
                (x, y, w, h, out_w, out_h),
                crossfade_s=0.04 if crossfade_on else 0.0,
                has_audio=has_audio,
            )
            profile = framing.ruler_profile(out_path)
            report = framing.match_report(ref, profile)
    except speech.TranscribeError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if iterate_at_proxy:
            loop_path.unlink(missing_ok=True)
    if report.get("ok"):
        line = report["line"].replace(
            "the reference", f"your {aspect} reference"
        )
    else:
        line = (
            "The exported video's face could not be measured, "
            "so no match line is claimed for it."
        )
    entry = {
        "plan_stamp": _plan_stamp_of(cuts),
        "line": line,
        "position_off_pct": report.get("position_off_pct"),
        "size_off_pct": report.get("size_off_pct"),
        "loudness_before_lufs": result["loudness_before_lufs"],
        "loudness_after_lufs": result["loudness_after_lufs"],
        "subtitles": result["subtitles"],
        "framing_corrections": corrections,
        "output_width_share": (
            float(profile["face"]["width"])
            if profile and profile.get("found")
            else None
        ),
        "reference_width_share": (
            float(ref_face["width"]) if ref_face else None
        ),
        "frame_width": out_w,
        "frame_height": out_h,
        # How far the crop was scaled up to fill the
        # frame: the same ratio the crop's may-look-soft
        # note is decided by, recorded so it can be said
        # plainly instead of guessed from sizes later.
        "scale_up": (
            round(out_w / (float(window["width"]) * width), 2)
            if window.get("width") and width
            else None
        ),
    }
    measured = _load_session_json(config, FINISH_MEASURED_FILENAME, {})
    if not isinstance(measured, dict):
        measured = {}
    measured[aspect] = entry
    _write_json(session / FINISH_MEASURED_FILENAME, measured)
    project = _save_project(config)
    return {
            "ok": True,
            "file": finish.finish_filename(aspect),
            "match": entry,
            "project": project,
            "shape": _finish_shape_view(config, aspect),
    }


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

    def _send_plain_page(self, status: int, title: str, sentence: str) -> None:
        """A whole page that says one plain sentence, and nothing else.

        Browser navigations (a video link opened directly, a stale
        address) must never land on a bare JSON body: this is the
        page they get instead, styled like the app's own fallback
        pages, with the way back. Programmatic callers keep JSON.
        """
        page = (
            "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, "
            "initial-scale=1\"><title>" + html.escape(title) + "</title></head>"
            "<body style=\"font-family: sans-serif; background: #0b0b0f; "
            "color: #f5f5f7; padding: 24px;\">"
            "<h1>" + html.escape(title) + "</h1>"
            "<p>" + html.escape(sentence) + "</p>"
            "<p><a href=\"/\" style=\"color: #0064d1;\">Back to the app</a></p>"
            "</body></html>"
        )
        self._send_bytes(
            status, page.encode("utf-8"), "text/html; charset=utf-8"
        )

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
            # Only media is ever served through here, and a media
            # address is one a browser can land on: plain words on a
            # page, never a bare JSON body.
            self._send_plain_page(
                404, "A file is missing", JOB_INCOMPLETE_MESSAGE
            )
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
            if path == "/" or path == "/studio":
                self._page_studio()
            elif path == "/classic":
                self._page_index()
            elif path == "/api/job":
                self._send_json(200, _job_summary(self.config))
            elif path == "/api/signin":
                self._send_json(200, _signin_state(self.config))
            elif path == "/api/thread":
                # The thread reads the same with or without a job:
                # before a job exists it is the launch session's
                # thread, where the conversation starts (0.25,
                # finding 5). Its entries carry into the job when
                # one arrives (_carry_launch_thread).
                self._send_json(200, _thread_view(self.config))
            elif path == "/api/editor-name":
                self._send_json(200, {"ok": True, "editor_name": usual.load_editor_name()})
            elif path == "/api/saved-transcripts":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "saved_transcripts_count":
                            saved_transcripts.count_saved(
                                self.config.launch_home
                                / saved_transcripts.STORE_DIR_NAME
                            ),
                    },
                )
            elif path == "/api/intake/status":
                self._get_intake_status()
            elif path == "/api/captions":
                self._serve_captions()
            elif path == "/api/export":
                self._serve_export()
            elif path == "/api/finish/state":
                self._serve_finish_state()
            elif path == "/api/finish/frame":
                self._serve_finish_frame()
            elif path == "/api/finish/download":
                self._serve_finish_download()
            elif path == "/api/finish/export/status":
                self._get_finish_export_status()
            elif path == "/api/project/recent":
                self._serve_project_recent()
            elif path == "/api/project/file":
                self._serve_project_file()
            elif path == "/api/finish/video":
                self._serve_finish_video()
            elif path == "/api/history":
                self._send_json(
                    200, {"ok": True, **checkpoints.state(self.config)}
                )
            elif path.startswith("/video/"):
                if not self.config.has_job:
                    # A media route is something a browser can land
                    # on, so it answers with a page, never bare JSON.
                    self._send_plain_page(
                        404, "No job is loaded yet", NO_JOB_MESSAGE
                    )
                    return
                self._serve_video(path[len("/video/"):])
            elif path == "/after-review":
                self._serve_after_review()
            elif path.startswith("/api/intake/video/"):
                self._serve_intake_video(path[len("/api/intake/video/"):])
            elif path.startswith("/api/download/"):
                self._serve_download(path[len("/api/download/"):])
            elif self.config.has_job and (
                path[1:] in _job_videos(self.config.job_dir)
                or path[1:] == SESSION_VIDEO_NAME
            ):
                # The after-review page references its video by bare name;
                # resolve it from the session dir first, then the job dir.
                self._serve_media_by_name(path[1:])
            elif path.startswith("/api/"):
                self._error(404, "not found")
            else:
                self._send_plain_page(
                    404,
                    "That page is not here",
                    "That page is not part of this app.",
                )
        except FileNotFoundError:
            # A job file an action needs is not on disk. API callers
            # keep JSON, with the plain sentence as its wording; a
            # browser navigation gets the plain page instead, so a
            # person never lands on a bare JSON body for this.
            if path.startswith("/api/"):
                self._error(404, JOB_INCOMPLETE_MESSAGE)
            else:
                self._send_plain_page(
                    404, "A file is missing", JOB_INCOMPLETE_MESSAGE
                )
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(400, str(exc))

    def do_POST(self):  # noqa: N802 - stdlib name
        path = unquote(urlsplit(self.path).path)
        if (
            path in _JOB_REQUIRED_POSTS
            and not self.config.has_job
        ):
            # The front door is open but nothing is loaded: anything
            # that works on a job gets one plain sentence, never a
            # crash or a machine error.
            self._error(400, NO_JOB_MESSAGE)
            return
        if (
            path in _JOB_REQUIRED_POSTS
            and self.config.has_job
            and _job_files_missing(self.config)
        ):
            # A job whose files are not all on disk yet (or no
            # longer): its actions are unavailable until it is
            # whole, and the answer is the same plain sentence the
            # page's banner shows.
            self._error(400, JOB_INCOMPLETE_MESSAGE)
            return
        if path == "/api/load-sample":
            self._post_load_sample()
        elif path == "/api/intake/begin":
            self._post_intake_begin()
        elif path == "/api/intake/file":
            self._post_intake_file()
        elif path == "/api/intake/finish":
            self._post_intake_finish()
        elif path == "/api/intake/link":
            self._post_intake_link()
        elif path == "/api/intake/first-edit":
            self._post_intake_first_edit()
        elif path == "/api/teach/begin":
            self._post_teach_begin()
        elif path == "/api/teach/file":
            self._post_teach_file()
        elif path == "/api/teach/link":
            self._post_teach_link()
        elif path == "/api/teach/run":
            self._post_teach_run()
        elif path == "/api/cost-estimate":
            self._post_cost_estimate()
        elif path == "/api/money-reset":
            self._post_money_reset()
        elif path == "/api/dictate":
            self._post_dictate()
        elif path == "/api/comments":
            self._post_comments()
        elif path == "/api/signin":
            self._post_signin()
        elif path == "/api/signin/save":
            self._post_signin_save()
        elif path == "/api/signin/remove":
            self._post_signin_remove()
        elif path == "/api/signin/check":
            self._post_signin_check()
        elif path == "/api/saved-transcripts/clear":
            self._post_saved_transcripts_clear()
        elif path == "/api/spark-route":
            self._post_spark_route()
        elif path == "/api/direct":
            self._post_direct()
        elif path == "/api/front-door":
            self._post_front_door()
        elif path == "/api/test-video":
            self._post_test_video()
        elif path == "/api/problem-report":
            self._post_problem_report()
        elif path == "/api/problem-report/reveal":
            self._post_problem_report_reveal()
        elif path == "/api/edit-note":
            self._post_edit_note()
        elif path == "/api/grades":
            self._post_grades()
        elif path == "/api/rule-save":
            self._post_rule_save()
        elif path == "/api/inspiration-save":
            self._post_inspiration_save()
        elif path == "/api/rule-custom":
            self._post_rule_custom()
        elif path == "/api/rule-dismiss":
            self._post_rule_dismiss()
        elif path == "/api/rule-remove":
            self._post_rule_remove()
        elif path == "/api/rule-edit":
            self._post_rule_edit()
        elif path == "/api/styles":
            self._post_styles()
        elif path == "/api/word-toggle":
            self._post_word_toggle()
        elif path == "/api/word-range":
            self._post_word_range()
        elif path == "/api/gap-erase":
            self._post_gap_erase()
        elif path == "/api/join-pause":
            self._post_join_pause()
        elif path == "/api/timeline/split":
            self._post_timeline_split()
        elif path == "/api/timeline/trim":
            self._post_timeline_trim()
        elif path == "/api/timeline/delete":
            self._post_timeline_delete()
        elif path == "/api/timeline/move":
            self._post_timeline_move()
        elif path == "/api/timeline/paste":
            self._post_timeline_paste()
        elif path == "/api/timeline/fade":
            self._post_timeline_fade()
        elif path == "/api/edit-action":
            self._post_edit_action()
        elif path == "/api/gold-verdict":
            self._post_gold_verdict()
        elif path == "/api/join-call":
            self._post_join_call()
        elif path == "/api/why":
            self._post_why()
        elif path == "/api/word-correct":
            self._post_word_correct()
        elif path == "/api/word-correct-remove":
            self._post_word_correct_remove()
        elif path == "/api/captions-pref":
            self._post_captions_pref()
        elif path == "/api/render":
            self._post_render()
        elif path == "/api/finish/versions":
            self._post_finish_versions()
        elif path == "/api/finish/offset":
            self._post_finish_offset()
        elif path == "/api/finish/render":
            self._post_finish_render()
        elif path == "/api/finish/export":
            self._post_finish_export()
        elif path == "/api/finish/reveal":
            self._post_finish_reveal()
        elif path == "/api/project/save":
            self._post_project_save()
        elif path == "/api/project/open":
            self._post_project_open()
        elif path == "/api/history/undo":
            self._post_history_move(undo=True)
        elif path == "/api/history/redo":
            self._post_history_move(undo=False)
        elif path == "/api/voice-command":
            self._post_voice_command()
        elif path == "/api/thread":
            self._post_thread_append()
        elif path == "/api/thread/message":
            self._post_thread_message()
        elif path == "/api/thread/action":
            self._post_thread_action()
        elif path == "/api/thread/offer":
            self._post_thread_offer()
        elif path == "/api/thread/cleanup":
            self._post_thread_cleanup()
        elif path == "/api/review/verdict":
            self._post_review_verdict()
        elif path == "/api/thread/read":
            self._post_thread_read()
        elif path == "/api/editor-name":
            self._post_editor_name()
        else:
            self._error(404, "not found")

    # -- GET handlers -----------------------------------------------------

    def _page_studio(self) -> None:
        """Studio, the new front end (Message 1: shell and Train).

        One small file, served as it sits; Studio reads everything
        it needs from the existing endpoints. The classic page stays
        reachable at /classic, byte for byte as it was.
        """
        template_path = (
            Path(__file__).resolve().parent
            / "templates"
            / "studio.html"
        )
        try:
            page = template_path.read_text(encoding="utf-8")
        except OSError:
            self._send_plain_page(
                404,
                "Studio is not here yet",
                "Studio has not been built in this copy yet. "
                "The classic page is at /classic.",
            )
            return
        self._send_bytes(
            200, page.encode("utf-8"), "text/html; charset=utf-8"
        )

    def _page_index(self) -> None:
        template_path = (
            Path(__file__).resolve().parent
            / "templates"
            / "test_app.html"
        )
        page = template_path.read_text(encoding="utf-8")
        job_name = self.config.job_label()
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
        job_whole = True
        if summary is not None:
            job_whole = bool(summary.get("job_whole", True))
        if self.config.has_job and not job_whole:
            subtitle = JOB_INCOMPLETE_MESSAGE
        elif self.config.has_job:
            subtitle = (
                f"Test session for {job_name}. Watch the edit, pin a "
                "comment where something needs to change, then run "
                "direction and see exactly what the program did "
                "about each note."
            )
        else:
            subtitle = (
                "No job is loaded yet. Drop a runner job folder "
                "below, or try the built-in sample, and the app "
                "runs on it."
            )
        page = page.replace("{{SUBTITLE}}", html.escape(subtitle))
        page = page.replace("{{JOB_NAME}}", html.escape(job_name))
        page = page.replace("{{JOB_NAME_JSON}}", json.dumps(job_name))
        page = page.replace(
            "{{HAS_JOB_JSON}}", json.dumps(self.config.has_job)
        )
        page = page.replace("{{JOB_WHOLE_JSON}}", json.dumps(job_whole))
        page = page.replace("{{VIDEO_JSON}}", json.dumps(primary_video))
        page = page.replace(
            "{{META_AVAILABLE_JSON}}", json.dumps(meta_available)
        )
        self._send_bytes(
            200, page.encode("utf-8"), "text/html; charset=utf-8"
        )

    def _send_playback_pending(
        self, state: str, progress: Optional[int] = None
    ) -> None:
        """The answer while a watchable copy is not ready: one
        plain page, never the raw file in the copy's place. While
        a copy builds, the page carries the converter's own
        percent when one exists, the same number the in-app
        surfaces show."""
        if state == "failed":
            self._send_plain_page(
                503,
                "This video cannot be shown here yet",
                PLAYBACK_FAILED_MESSAGE,
            )
        elif isinstance(progress, int):
            self._send_plain_page(
                503,
                PLAYBACK_GETTING_READY,
                f"Getting your video ready to watch… {progress}%. "
                + PLAYBACK_BUILDING_MESSAGE,
            )
        else:
            self._send_plain_page(
                503, PLAYBACK_GETTING_READY, PLAYBACK_BUILDING_MESSAGE
            )

    def _serve_video(self, name: str) -> None:
        if name == SESSION_VIDEO_NAME:
            # The edited video lives in the session, not the job
            # folder; it is served under the same discipline as a
            # job video (its watchable copy while one is needed).
            target = _session_video_target(self.config)
            if target is None:
                source = self.config.session_dir / SESSION_VIDEO_NAME
                if not source.is_file():
                    self._send_plain_page(
                        404,
                        "That video is not here",
                        "The edited video has not been built yet. "
                        "Make an edit first, and it will be here.",
                    )
                    return
                copy_path = _playback_copy_path(
                    self.config, SESSION_VIDEO_NAME
                )
                self._send_playback_pending(
                    _playback_state_for(source, copy_path),
                    _playback_progress_for(source, copy_path),
                )
                return
            self._serve_file_with_range(
                target, _video_content_type(target)
            )
            return
        if name not in _job_videos(self.config.job_dir):
            self._send_plain_page(
                404,
                "That video is not here",
                "That video is not in this job. Drop the job folder "
                "again, or pick the video from the app.",
            )
            return
        # A video that is not browser-safe is served through its
        # watchable copy; while the copy is being made (or could
        # not be made) the answer is the plain state, never the
        # raw file.
        target = _playback_target(self.config, name)
        if target is None:
            self._send_playback_pending(
                _playback_state(self.config, name),
                _playback_progress(self.config, name),
            )
            return
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
            if target is None:
                self._send_playback_pending(
                    _playback_state(self.config, name),
                    _playback_progress(self.config, name),
                )
                return
            self._serve_file_with_range(
                target, _video_content_type(target)
            )
            return
        self._send_plain_page(
            404,
            "That video is not here yet",
            "That video is not here yet. Go back and build the "
            "fixed video, then open it from the app.",
        )

    def _serve_after_review(self) -> None:
        target = self.config.session_dir / "after-review.html"
        if target.is_file():
            # The visit is one half of what the finish card needs to
            # see; record it in the session, beside the render.
            try:
                self.config.session_dir.mkdir(parents=True, exist_ok=True)
                _write_json(
                    self.config.session_dir / AFTER_REVIEW_VISITED_FILENAME,
                    {"visited_at": _utc_now()},
                )
            except OSError:
                pass
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
            "<p><a href=\"/\" style=\"color: #0064d1;\">Back to the test "
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

    def _first_edits(self) -> dict:
        """First-edit runs in flight or finished, by intake id."""
        registry = getattr(self.server, "first_edit_jobs", None)
        if registry is None:
            registry = {}
            self.server.first_edit_jobs = registry  # type: ignore[attr-defined]
        return registry

    def _teachings(self) -> dict:
        """The teaching drop registry (teach id to staging record)."""
        registry = getattr(self.server, "teachings", None)
        if registry is None:
            registry = {}
            self.server.teachings = registry  # type: ignore[attr-defined]
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
        if served is None:
            self._send_playback_pending(
                _playback_state_for(target, _staged_copy_path(target)),
                _playback_progress_for(
                    target, _staged_copy_path(target)
                ),
            )
            return
        self._serve_file_with_range(served, _video_content_type(served))

    # -- POST handlers ----------------------------------------------------

    def _become_job(self, job_root: Path) -> None:
        """Swap the server onto a job folder that just arrived.

        The session sign-in carries over, and so does the launch
        home, so the intake anchor and the sample staging stay in
        the one place the launch put them.
        """
        new_config = WebAppConfig(
            job_root, launch_home=self.config.launch_home
        )
        new_config.session_api_key = self.config.session_api_key
        new_config.session_elevenlabs_key = (
            self.config.session_elevenlabs_key
        )
        new_config.session_baseten_key = self.config.session_baseten_key
        new_config.session_fireworks_key = (
            self.config.session_fireworks_key
        )
        self.server.config = new_config  # type: ignore[attr-defined]
        # The front door's conversation carries into the job that
        # just arrived, the same as on the intake path.
        _carry_launch_thread(new_config)
        _kick_playback_copies(new_config)

    def _post_load_sample(self) -> None:
        """Load the built-in sample job with one click.

        The sample ships inside the app; it is staged as a folder
        in the launch home and becomes the current job, exactly as
        if its folder had been dropped. The page reloads onto it.
        """
        source = Path(__file__).resolve().parent / "sample_job"
        target = self.config.launch_home / SAMPLE_JOB_DIR_NAME
        try:
            target.mkdir(parents=True, exist_ok=True)
            for name in SAMPLE_JOB_FILES:
                shutil.copyfile(source / name, target / name)
        except OSError as exc:
            _log_raw_error("load sample", exc)
            self._error(
                500,
                "The built-in sample could not be loaded. Drop a "
                "job folder instead, or try again in a moment.",
            )
            return
        self._become_job(target)
        self._send_json(
            200,
            {
                "ok": True,
                "kind": "job",
                "job_name": target.name,
                "message": (
                    "The built-in sample is loaded. The page will "
                    "reload on it."
                ),
            },
        )

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
        # The order files arrive in is the order the creator put
        # the parts in; record it on the drop (item 2).
        record.setdefault("files", []).append("/".join(parts))
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
        self._send_json(200, self._finish_intake(intake_id, record))

    def _finish_intake(self, intake_id: str, record: dict) -> dict:
        """What a closed drop turned out to be, as a payload.

        The body of _post_intake_finish, shared with the chat's
        link route (0.27.1, run-through finding 4): a video
        fetched from a pasted link is finished exactly like a
        dropped file, so both paths stage the same way.
        """
        base = Path(record["dir"])
        detected = _detect_intake(base)
        kind = detected["kind"]
        if kind == "job":
            job_root = detected["job_root"]
            self._become_job(job_root)
            # A test-video mark set at the front door, in the
            # launch session, carries into this job's session.
            _carry_launch_test_video(self.config)
            return {
                "ok": True,
                "kind": "job",
                "parts": list(record.get("files") or []),
                "job_name": job_root.name,
                "message": (
                    f"Your job folder is in. The app is now "
                    f"running on {job_root.name}, the copy it "
                    "just took in; your original folder was not "
                    "changed. The page will reload on the new job."
                ),
            }
        if kind == "video":
            video_path = detected["video_path"]
            # Parts brought in together are processed in the
            # order they were recorded in (item 2): with several
            # videos and no job files, the parts are joined into
            # one video in that order and the joined file is what
            # the first edit processes. If the join cannot be
            # made, the first part in the order is the video, and
            # the answer says the rest were not joined.
            ordered_videos = _ordered_drop_videos(
                base, list(record.get("files") or [])
            )
            joined = False
            join_note = ""
            if len(ordered_videos) >= 2:
                combined = base / "combined-parts.mp4"
                if _join_ordered_parts(ordered_videos, combined):
                    video_path = combined
                    joined = True
                else:
                    video_path = ordered_videos[0]
                    join_note = (
                        " The parts could not be joined into one "
                        "video here, so only the first part, in "
                        "the order you put them in, was taken in."
                    )
            elif len(ordered_videos) == 1:
                video_path = ordered_videos[0]
            relative = video_path.relative_to(base).as_posix()
            record["video_rel"] = relative
            record["parts"] = [
                path.relative_to(base).as_posix()
                for path in ordered_videos
            ]
            record["joined"] = joined
            # The watchable copy starts now, in the background,
            # the moment the drop is complete: the preview and
            # the later job both draw on it, and no play request
            # ever waits on the conversion itself.
            _ensure_playback_copy_for(
                video_path, _staged_copy_path(video_path)
            )
            return {
                "ok": True,
                "kind": "video",
                "parts": list(record.get("files") or []),
                "joined": joined,
                "video_name": video_path.name,
                "video_url": (
                    f"/api/intake/video/{intake_id}/"
                    + quote(relative)
                ),
                "playback": _playback_state_for(
                    video_path, _staged_copy_path(video_path)
                ),
                "can_first_edit": True,
                "message": VIDEO_ONLY_MESSAGE + join_note,
            }
        if kind == "empty":
            return {"ok": True, "kind": "empty", "message": EMPTY_DROP_MESSAGE}
        return {"ok": True, "kind": "not_a_job", "message": NOT_A_JOB_MESSAGE}

    def _post_intake_link(self) -> None:
        """Bring in the video at a link pasted into the chat.

        The same link handling Teach uses (0.27.1, run-through
        finding 4): the video is fetched into a staging drop and
        finished exactly like a dropped file, so the chat's
        offer can run on it. A link the app cannot get is the
        same honest answer the Teach field gives, and a social
        link gets Teach's plain refusal: never a fake success.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        url = str(body.get("url") or "").strip()
        if not (
            url.lower().startswith("http://")
            or url.lower().startswith("https://")
        ):
            self._error(400, "That is not a link the app can open.")
            return
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            host = ""
        if any(host == h or host.endswith("." + h) for h in _SOCIAL_HOSTS):
            self._send_json(
                200,
                {"ok": True, "landed": False, "message": SOCIAL_LINK_MESSAGE},
            )
            return
        name = "linked-video"
        intake_id = uuid.uuid4().hex[:12]
        staging = self._intake_root() / f"{name}-{intake_id}"
        try:
            staging.mkdir(parents=True, exist_ok=False)
        except OSError:
            _log_raw_error("intake link", OSError(str(staging)))
            self._error(
                500,
                "The link could not be taken in. Try again in a moment.",
            )
            return
        record = {"dir": str(staging), "name": name}
        self._intakes()[intake_id] = record
        try:
            landed = teaching.fetch_link(url, staging)
        except teaching.TeachingLinkError:
            self._send_json(
                200,
                {"ok": True, "landed": False, "message": TEACH_LINK_MESSAGE},
            )
            return
        record["files"] = [landed.name]
        payload = self._finish_intake(intake_id, record)
        payload["intake_id"] = intake_id
        payload["landed"] = True
        self._send_json(200, payload)

    def _post_teach_begin(self) -> None:
        """Open a teaching session folder and hand back its id.

        Files then arrive one by one at /api/teach/file, a link at
        /api/teach/link, and /api/teach/run does the learning.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        kind = str(body.get("kind") or "mine")
        if kind not in ("mine", "style"):
            kind = "mine"
        try:
            teach_id, folder = teaching.new_teaching_session(kind)
        except OSError as exc:
            _log_raw_error("teach begin", exc)
            self._error(
                500,
                "The teaching drop could not be taken in. Try "
                "again in a moment.",
            )
            return
        self._teachings()[teach_id] = {"dir": str(folder), "kind": kind}
        self._send_json(200, {"ok": True, "teach_id": teach_id})

    def _post_teach_file(self) -> None:
        """Receive one file of a teaching drop, byte for byte.

        The role (recording, raw, or finished) and the file's own
        name ride the query string; the body is the file itself,
        streamed to disk under the role's name, with the same
        safety pattern as the intake's file route.
        """
        query = parse_qs(urlsplit(self.path).query)
        teach_id = (query.get("teach") or [""])[0]
        role = (query.get("role") or [""])[0]
        record = self._teachings().get(teach_id)
        if record is None:
            self._error(
                400,
                "That teaching drop is not on record anymore. "
                "Add the files again.",
            )
            return
        if role not in ("recording", "raw", "finished"):
            self._error(
                400,
                "That part of the teaching drop is not one the "
                "app takes.",
            )
            return
        raw_name = (query.get("name") or [""])[0]
        parts = _safe_relative_parts(raw_name)
        if parts is None or len(parts) != 1:
            self._error(
                400,
                "That file's name could not be used, so it was "
                "not taken in.",
            )
            return
        suffix = Path(parts[0]).suffix.lower()
        if suffix not in TEACH_FILE_SUFFIXES:
            self._error(
                400,
                "That file is not a video or sound file the app "
                "can use.",
            )
            return
        base = Path(record["dir"]).resolve()
        target = (base / f"{role}{suffix}").resolve()
        if not target.is_relative_to(base):
            self._error(
                400,
                "That file's name could not be used, so it was "
                "not taken in.",
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
            with open(target, "wb") as handle:
                while remaining > 0:
                    chunk = self.rfile.read(min(_CHUNK, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    remaining -= len(chunk)
        except OSError as exc:
            _log_raw_error("teach file", exc)
            self._error(
                500,
                "That file could not be saved. Nothing was "
                "learned from it.",
            )
            return
        record[role] = target.name
        self._send_json(
            200,
            {"ok": True, "role": role, "size": target.stat().st_size},
        )

    def _post_teach_link(self) -> None:
        """Bring in the finished video at a pasted link.

        A link the app cannot get a video from is not an error:
        the answer says so in plain words and asks for the file
        instead. It is never a fake success.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        teach_id = str(body.get("teach_id") or "")
        record = self._teachings().get(teach_id)
        if record is None:
            self._error(
                400,
                "That teaching drop is not on record anymore. "
                "Add the files again.",
            )
            return
        folder = Path(record["dir"])
        url_text = str(body.get("url") or "")
        host = ""
        try:
            host = (urlsplit(url_text).hostname or "").lower()
        except ValueError:
            host = ""
        if any(host == h or host.endswith("." + h) for h in _SOCIAL_HOSTS):
            self._send_json(
                200,
                {"ok": True, "landed": False, "message": SOCIAL_LINK_MESSAGE},
            )
            return
        if record.get("kind") == "style" and teaching._youtube_video_id(
            urlsplit(url_text)
        ):
            self._send_json(
                200,
                {
                    "ok": True,
                    "landed": False,
                    "message": STYLE_YOUTUBE_MESSAGE,
                },
            )
            return
        try:
            landed = teaching.fetch_link(url_text, folder)
        except teaching.TeachingLinkError:
            self._send_json(
                200,
                {
                    "ok": True,
                    "landed": False,
                    "message": TEACH_LINK_MESSAGE,
                },
            )
            return
        target = folder / ("finished" + landed.suffix.lower())
        if landed != target:
            landed.replace(target)
        record["finished"] = target.name
        self._send_json(
            200,
            {
                "ok": True,
                "landed": True,
                "message": (
                    "The video from that link is in. Choose Teach "
                    "from this when you are ready."
                ),
            },
        )

    def _post_teach_run(self) -> None:
        """Run the learning over one teaching drop.

        The drafts land in the current session's teaching drafts
        and come back with the rule suggestions they now join, so
        the page can show them in place.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        teach_id = str(body.get("teach_id") or "")
        record = self._teachings().get(teach_id)
        if record is None:
            self._error(
                400,
                "That teaching drop is not on record anymore. "
                "Add the files again.",
            )
            return
        result = _run_teaching(self.config, record)
        session_log.log_stage(
            self.config.session_dir, "teaching", "finish"
        )
        result["suggestions"] = _rule_suggestions(self.config)
        self._send_json(200, result)

    def _post_intake_first_edit(self) -> None:
        """Start the first edit for one staged lone video.

        The build runs in its own thread (writing down the words
        can take a while) and reports through the status endpoint.
        Direction given with the request is mapped onto the run's
        settings by the deterministic parser, and spoken direction
        outranks the saved settings, as the front door settled.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        intake_id = str(body.get("intake_id") or "")
        record = self._intakes().get(intake_id)
        if record is None or not record.get("video_rel"):
            self._error(
                400,
                "That video is not on record anymore. Drop it again.",
            )
            return
        states = self._first_edits()
        existing = states.get(intake_id)
        if existing and existing.get("status") == "working":
            self._send_json(
                200,
                {
                    "ok": True,
                    "started": False,
                    "message": "The first edit is already being made.",
                },
            )
            return
        states[intake_id] = {
            "status": "working",
            "stage": "Starting…",
            "started_at": time.monotonic(),
            "stage_started_at": time.monotonic(),
            "elapsed_s": 0.0,
            "stage_elapsed_s": 0.0,
            "percent": None,
            "remaining_s": None,
            "progress_line": "0:00 so far",
            # The run's usual length (v0.20, chunk C), from
            # this computer's finished first edits; None until
            # enough of them are recorded to say "usually".
            "first_edit_eta_s": _first_edit_usual_seconds(
                self.config.launch_home
            ),
            "first_edit_eta": _first_edit_eta_text(
                self.config.launch_home
            ),
        }
        thread = threading.Thread(
            target=_run_first_edit,
            args=(
                self.server,
                intake_id,
                str(body.get("direction") or ""),
                bool(body.get("clean_captions", True)),
                _validated_drop_settings(body),
            ),
            daemon=True,
        )
        thread.start()
        self._send_json(200, {"ok": True, "started": True})

    def _get_intake_status(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        intake_id = (query.get("intake") or [""])[0]
        state = self._first_edits().get(intake_id)
        payload = {"ok": True, **(state or {"status": "idle"})}
        # The watchable copy's state rides along on the same poll
        # the page already runs: the preview swaps in when it is
        # ready, without a reload.
        record = self._intakes().get(intake_id)
        if record is not None and record.get("video_rel"):
            staged = Path(record["dir"]) / record["video_rel"]
            state = _playback_state_for(
                staged, _staged_copy_path(staged)
            )
            if state == "unstarted":
                # The front-door poll starts a build that never
                # started, the same rule as the job summary: this
                # surface must never wait on nothing.
                _ensure_playback_copy_for(
                    staged, _staged_copy_path(staged)
                )
                state = _playback_state_for(
                    staged, _staged_copy_path(staged)
                )
            payload["playback"] = state
            payload["playback_progress"] = _playback_progress_for(
                staged, _staged_copy_path(staged)
            )
        self._send_json(200, payload)

    def _serve_finish_video(self) -> None:
        """Download the session's built video, for the finish card.

        This is the latest render, under a plain download name. The
        same file stays watchable in the page through its usual
        address; this route is only the save-it copy.
        """
        if not self.config.has_job:
            self._error(404, NO_JOB_MESSAGE)
            return
        target = self.config.session_dir / SESSION_VIDEO_NAME
        if not target.is_file():
            self._error(
                404,
                "No video has been built for this job yet. Build "
                "the fixed video first.",
            )
            return
        self._send_bytes(
            200,
            target.read_bytes(),
            "video/mp4",
            {
                "Content-Disposition": (
                    'attachment; filename="finished-edit.mp4"'
                )
            },
        )

    def _serve_captions(self) -> None:
        if not self.config.has_job:
            self._error(404, "no job is loaded yet")
            return
        target = (
            self.config.session_dir / clean_captions.CAPTIONS_FILENAME
        )
        if not target.is_file():
            self._error(
                404,
                "No caption file has been made for this job yet.",
            )
            return
        self._send_bytes(
            200,
            target.read_bytes(),
            "text/vtt; charset=utf-8",
            {
                "Content-Disposition": (
                    'attachment; filename="clean-captions.vtt"'
                )
            },
        )

    # -- Finish (Message 3) ------------------------------------------------

    def _serve_finish_state(self) -> None:
        self._send_json(200, {"ok": True, **_finish_state_view(self.config)})

    def _serve_finish_frame(self) -> None:
        """One preview frame of a shape, cropped the way it exports."""
        query = parse_qs(urlsplit(self.path).query)
        aspect = (query.get("aspect") or [""])[0]
        if aspect not in finish.ASPECTS or not self.config.has_job:
            self._error(404, "no preview for that shape yet")
            return
        source = _finish_source(self.config)
        window, _ref, _notes = _finish_window(self.config, aspect)
        if source is None or window is None:
            self._error(404, "no preview for that shape yet")
            return
        try:
            media = speech.probe_media(source)
        except speech.TranscribeError:
            self._error(404, "no preview for that shape yet")
            return
        width = int(media.get("width") or 0)
        height = int(media.get("height") or 0)
        if not width or not height:
            self._error(404, "no preview for that shape yet")
            return
        at = query.get("t") or [None]
        try:
            at_s = float(at[0]) if at[0] is not None else None
        except (TypeError, ValueError):
            at_s = None
        if at_s is None:
            at_s = float(media.get("duration") or 0) / 2.0
        x, y, w, h = framing.crop_pixels(window, width, height)
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-ss", f"{at_s:.3f}", "-i", str(source),
                "-frames:v", "1",
                "-vf", f"crop={w}:{h}:{x}:{y},scale=360:-2",
                "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "-",
            ],
            capture_output=True, timeout=120,
        )
        if proc.returncode != 0 or not proc.stdout:
            self._error(404, "no preview for that shape yet")
            return
        self._send_bytes(200, proc.stdout, "image/jpeg", {})

    def _serve_finish_download(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        aspect = (query.get("aspect") or [""])[0]
        if aspect not in finish.ASPECTS:
            self._error(400, "unknown shape")
            return
        target = self.config.session_dir / finish.finish_filename(aspect)
        if not target.is_file():
            self._error(404, "that version has not been exported yet")
            return
        self._send_bytes(
            200,
            target.read_bytes(),
            "video/mp4",
            {
                "Content-Disposition": (
                    f'attachment; filename="{target.name}"'
                )
            },
        )

    def _post_finish_versions(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        state = finish.set_versions(
            self.config.session_dir, body.get("versions") or []
        )
        self._send_json(200, {"ok": True, "versions": state["versions"]})

    def _post_finish_offset(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        aspect = body.get("aspect")
        if aspect not in finish.ASPECTS:
            self._error(400, "unknown shape")
            return
        state = finish.nudge_offset(
            self.config.session_dir,
            aspect,
            float(body.get("dx") or 0.0),
            float(body.get("dy") or 0.0),
            reset=bool(body.get("reset")),
        )
        # A nudge changes the framing, so a measured line from an
        # earlier render no longer describes what will export.
        measured = _load_session_json(
            self.config, FINISH_MEASURED_FILENAME, {}
        )
        if isinstance(measured, dict) and aspect in measured:
            del measured[aspect]
            _write_json(
                self.config.session_dir / FINISH_MEASURED_FILENAME,
                measured,
            )
        self._send_json(
            200,
            {
                "ok": True,
                "offset": state["offsets"][aspect],
                "shape": _finish_shape_view(self.config, aspect),
            },
        )

    def _post_finish_render(self) -> None:
        """Export one shape's finished video (Message 3, beat 7).

        The render is the intake splice with this shape's framing
        crop, finished to his settings by finish.render_finish_
        video (loudness -14 LUFS, AAC 128 kbps stereo 48 kHz, a
        subtitle track inside the file), at the shape's full
        frame for the source. The rendered face is measured
        with the one ruler and compared with the shape's
        reference, so the match line on Finish is a measurement,
        not a promise. When the measured face size misses the
        reference by more than 3 percent, the crop is corrected
        from the measured share (framing.scale_window), at most
        twice. The correction loop iterates at a 1080-high
        proxy in the same shape (the crop math is in shares,
        so a correction measured there lands the same at full
        size); the delivered file is then rendered once, at
        full frame, with the settled crop, and measured for
        the stored match line. Export also saves the project
        file.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        aspect = body.get("aspect")
        if aspect not in finish.ASPECTS:
            self._error(400, "unknown shape")
            return
        self._send_json(200, _render_finish_shape(self.config, aspect))

    def _finish_exports(self) -> dict:
        """The chat export jobs on this server, by id.

        Same lazy pattern as the first-edit registry: created
        on first use so a server object built any way carries
        it (run_server also seeds it beside the others).
        """
        registry = getattr(self.server, "finish_exports", None)
        if registry is None:
            registry = {}
            self.server.finish_exports = registry
        return registry

    def _post_finish_export(self) -> None:
        """Start a chat export (0.27.3, chunk B item 9).

        Renders the named shapes from the current plan, in the
        background, one at a time. Starting while one is
        already working answers with the running job instead
        of doubling the renders.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        aspects = body.get("aspects")
        if (
            not isinstance(aspects, list)
            or not aspects
            or any(a not in finish.ASPECTS for a in aspects)
        ):
            self._error(400, "unknown shape")
            return
        if not self.config.has_job:
            self._send_json(
                200,
                {"ok": False, "error": "Make the edit first, so "
                 "there is an edit to export."},
            )
            return
        registry = self._finish_exports()
        for state in registry.values():
            if state.get("status") == "working":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "started": False,
                        "export": _finish_export_view(
                            state, self.config.launch_home
                        ),
                    },
                )
                return
        export_id = uuid.uuid4().hex[:12]
        state = {
            "id": export_id,
            "status": "working",
            "aspects": list(aspects),
            "shapes": {
                a: {"state": "queued"} for a in aspects
            },
            "error": None,
        }
        registry[export_id] = state
        worker = threading.Thread(
            target=_run_finish_export,
            args=(registry, export_id, self.config),
            daemon=True,
        )
        worker.start()
        self._send_json(
            200,
            {
                "ok": True,
                "started": True,
                "export": _finish_export_view(
                    state, self.config.launch_home
                ),
            },
        )

    def _get_finish_export_status(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        export_id = (query.get("id") or [None])[0]
        state = self._finish_exports().get(export_id or "")
        if state is None:
            self._error(404, "no such export")
            return
        self._send_json(
            200,
            {
                "ok": True,
                "export": _finish_export_view(
                    state, self.config.launch_home
                ),
            },
        )

    def _post_finish_reveal(self) -> None:
        """Show one exported file in Finder (chat export, item 9)."""
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        aspect = body.get("aspect")
        if aspect not in finish.ASPECTS:
            self._error(400, "unknown shape")
            return
        target = self.config.session_dir / finish.finish_filename(aspect)
        if not target.is_file():
            self._send_json(
                200,
                {
                    "ok": False,
                    "message": "That file is not exported yet.",
                },
            )
            return
        opened, message = _reveal_in_finder(target)
        self._send_json(200, {"ok": opened, "message": message})

    def _post_project_save(self) -> None:
        if not self.config.has_job:
            self._error(404, "no job is loaded yet")
            return
        saved = _save_project(self.config)
        self._send_json(200, {"ok": True, **saved})

    def _serve_project_file(self) -> None:
        target = self.config.session_dir / finish.PROJECT_FILENAME
        if not target.is_file():
            if self.config.has_job:
                _save_project(self.config)
            else:
                self._error(404, "no project has been saved yet")
                return
        self._send_bytes(
            200,
            target.read_bytes(),
            "application/json; charset=utf-8",
            {
                "Content-Disposition": (
                    f'attachment; filename="{finish.PROJECT_FILENAME}"'
                )
            },
        )

    def _serve_project_recent(self) -> None:
        self._send_json(
            200, {"ok": True, "projects": _recent_projects(self.config)}
        )

    def _post_project_open(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        ok, message = _open_project(
            self.config, str(body.get("id") or "")
        )
        self._send_json(200, {"ok": ok, "message": message})

    def _serve_export(self) -> None:
        """Hand the current edit to another editor as a timeline file.

        The format rides the query string: fcpxml for Final Cut
        Pro (DaVinci Resolve imports it too), premiere for
        Premiere Pro's XML, edl for most other editors. The file
        is built from the session's current plan, the same one
        the render and the captions read, by the timeline_export
        module: local text only, no run and no cost. The frame
        rate is the plan's own, which the module reads from the
        plan when none is handed to it. An unknown format gets
        one plain sentence and no file.
        """
        if not self.config.has_job:
            self._error(404, "no job is loaded yet")
            return
        query = parse_qs(urlsplit(self.path).query)
        fmt = (query.get("format") or [""])[0].strip().lower()
        if fmt not in ("fcpxml", "premiere", "edl"):
            self._error(
                400,
                "That is not one of the three timeline formats: "
                "fcpxml, premiere, or edl.",
            )
            return
        source_name = intake_build.intake_source_video(
            self.config.job_dir
        )
        if not source_name and self.config.source_video:
            source_name = Path(self.config.source_video).name
        if not source_name:
            source_name = self.config.job_dir.name
        cuts = _effective_cuts(self.config)
        filename, text, _notes = timeline_export.export_timeline(
            cuts, source_name
        )[fmt]
        content_type = (
            "text/plain; charset=utf-8"
            if fmt == "edl"
            else "application/xml; charset=utf-8"
        )
        self._send_bytes(
            200,
            text.encode("utf-8"),
            content_type,
            {
                "Content-Disposition": (
                    f'attachment; filename="{filename}"'
                )
            },
        )

    def _post_cost_estimate(self) -> None:
        """What one run will probably cost, before it runs (v0.7.2).

        The page asks with the video's length, the surface asking
        (a dropped video's first edit, or a job's Make the edit),
        the Remove choices currently set, and whether direction
        will be read. The estimate comes from the costs module:
        this Mac's recent-run history where it exists, the
        documented rates and typicals where it does not. Open at
        the front door like the intake routes: the first-edit
        card exists before any job does.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        surface = str(body.get("surface") or "front_door").strip().lower()
        if surface not in ("first_edit", "front_door"):
            surface = "front_door"
        settings = body.get("settings")
        if not isinstance(settings, dict):
            settings = {}
        try:
            duration_s = float(body.get("duration_s") or 0.0)
        except (TypeError, ValueError):
            duration_s = 0.0
        route = "local"
        if surface == "first_edit":
            route = _intake_word_route(self.config) or "local"
        estimate = costs.estimate_run(
            duration_s,
            surface,
            settings,
            bool(body.get("has_direction")),
            route=route,
            history_entries=costs.load_money_entries(),
            spark_route=self.config.spark_route(),
        )
        estimate["surface"] = surface
        _store_job_estimate(self.config, estimate)
        self._send_json(200, {"ok": True, "estimate": estimate})

    def _post_money_reset(self) -> None:
        """Clear the running total on this Mac (v0.7.2).

        The jobs' own cost records are not touched; only the
        Mac-wide total the money card shows starts over. The
        response carries the fresh money view so the page can
        re-render from it directly.
        """
        costs.reset_money()
        self._send_json(200, {"ok": True, "money": _money_view(self.config)})

    def _post_dictate(self) -> None:
        """Turn one recorded note into text, through Meta's voice.

        The browser records the note and sends the clip; the server
        takes its sound, hands it to Muse Voice Transcribe, and
        sends back the words for the box they were spoken into, so
        the creator reads and fixes them before anything counts.
        Typing never depends on this working.
        """
        key = self.config.signin_key()
        if not key:
            self._send_json(
                200, {"ok": False, "message": DICTATE_NO_KEY_MESSAGE}
            )
            return
        length = self.headers.get("Content-Length")
        try:
            size = int(length) if length else 0
        except ValueError:
            size = 0
        if size <= 0 or size > 32 * 1024 * 1024:
            self._send_json(
                200,
                {
                    "ok": False,
                    "message": (
                        "That recording did not arrive whole. Try "
                        "again, or type the note."
                    ),
                },
            )
            return
        raw = self.rfile.read(size)
        ctype = (
            (self.headers.get("Content-Type") or "")
            .split(";")[0]
            .strip()
            .lower()
        )
        ext = {
            "audio/webm": ".webm",
            "video/webm": ".webm",
            "audio/mp4": ".mp4",
            "audio/x-m4a": ".m4a",
            "audio/ogg": ".ogg",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
        }.get(ctype, ".webm")
        session = self.config.session_dir
        session.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex[:12]
        raw_path = session / f"dictation-{token}{ext}"
        wav_path = session / f"dictation-{token}-16k.wav"
        try:
            raw_path.write_bytes(raw)
            speech.extract_audio(raw_path, wav_path)
            result = speech.meta_transcribe_wav(wav_path, key)
        except speech.TranscribeError as exc:
            self._send_json(200, {"ok": False, "message": str(exc)})
            return
        finally:
            raw_path.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)
        if not result["text"]:
            self._send_json(
                200,
                {
                    "ok": False,
                    "message": (
                        "Nothing was heard in that recording. Try "
                        "again a little closer to the microphone, "
                        "or type the note."
                    ),
                },
            )
            return
        _account_costs(
            self.config,
            [
                costs.make_entry(
                    "dictation",
                    costs.dictation_cost(result["duration_s"]),
                    "one dictated note",
                    duration_s=result["duration_s"],
                    pool="meta",
                )
            ],
        )
        self._send_json(
            200,
            {
                "ok": True,
                "text": result["text"],
                "duration_s": result["duration_s"],
            },
        )

    def _post_voice_command(self) -> None:
        """Settle one dictated request (v0.11.0, fix item 8).

        The local match comes first and is the whole answer for
        a matched command: this route returns the decision at
        once, with no model call and nothing run on the server,
        and the page fires the same handler the command's own
        button fires, in the same step. That is what makes a
        misheard command one undo away: there is no side path.

        An ask and the known but unavailable centering command
        also end here: nothing runs, nothing is saved, and no
        direction is sent. Only words that match nothing go
        further: to the existing direction pass when a key is
        set and the words read as direction, otherwise into the
        feedback store as a note, said plainly.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        heard = str(body.get("text") or "").strip()
        if not heard:
            self._error(400, "Say something first, then stop the recording.")
            return
        match = voice_commands.match_voice_command(heard)
        decision = match.get("decision")

        if decision == "command":
            command = match["command"]
            seconds = match.get("seconds")
            label = voice_commands.command_label(command, seconds)
            record = f'Heard: "{heard}". Ran command: {label}.'
            _record_voice_line(self.config, record)
            self._send_json(
                200,
                {
                    "ok": True,
                    "decision": "command",
                    "command": command,
                    "seconds": seconds,
                    "heard": heard,
                    "record": record,
                    "message": record,
                },
            )
            return

        if decision == "ask":
            candidates = list(match.get("candidates") or [])[:2]
            labels = [
                voice_commands.COMMAND_LABELS.get(c, c)
                for c in candidates
            ]
            names = " or ".join(labels)
            record = (
                f'Heard: "{heard}". I am not sure which you meant: '
                f"{names}? Nothing ran."
            )
            _record_voice_line(self.config, record)
            self._send_json(
                200,
                {
                    "ok": True,
                    "decision": "ask",
                    "candidates": candidates,
                    "candidate_labels": labels,
                    "heard": heard,
                    "record": record,
                    "message": record,
                },
            )
            return

        if decision == "unavailable":
            record = (
                f'Heard: "{heard}". '
                + voice_commands.CENTER_UNAVAILABLE_SENTENCE
            )
            _record_voice_line(self.config, record)
            self._send_json(
                200,
                {
                    "ok": True,
                    "decision": "unavailable",
                    "command": match.get("command") or "center_frame",
                    "heard": heard,
                    "record": record,
                    "message": record,
                },
            )
            return

        # Nothing matched locally. Direction only when a key is
        # set and the words plainly read as direction; the test
        # for that is the deterministic one in voice_commands.
        if (
            voice_commands.reads_as_direction(heard)
            and self.config.signin_key()
        ):
            try:
                checkpoints.begin(self.config)
                summary = _run_direction_pass(
                    self.config,
                    heard,
                    _front_door_settings(self.config),
                )
                # The final sweep (0.27.3), same checkpoint.
                _run_final_sweep(self.config)
                checkpoints.commit(self.config, "Direction run")
            except Exception as exc:
                _log_raw_error("voice direction", exc)
                summary = None
            if summary is not None:
                record = (
                    f'Heard: "{heard}". Sent as direction.'
                )
                _record_voice_line(self.config, record)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "decision": "direction",
                        "heard": heard,
                        "record": record,
                        "message": record,
                        "direction_ran": bool(summary.get("ran")),
                    },
                )
                return

        _save_voice_note(self.config, heard)
        record = f'Heard: "{heard}". {VOICE_NOTE_SENTENCE}'
        _record_voice_line(self.config, record)
        self._send_json(
            200,
            {
                "ok": True,
                "decision": "note",
                "heard": heard,
                "record": record,
                "message": record,
            },
        )

    def _post_thread_append(self) -> None:
        """Append one raw entry to the thread (the store's own route)."""
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        kind = str(body.get("kind") or "creator")
        if kind not in ("creator", "editor", "run", "held", "pin", "rule", "why"):
            self._error(400, "That kind of thread entry is not one the panel keeps.")
            return
        entry = {
            key: value
            for key, value in body.items()
            if key not in ("kind",)
        }
        entry["kind"] = kind
        entry.setdefault("status", "settled")
        stored = _append_thread(self.config, entry)
        self._send_json(200, {"ok": True, "entry": stored, **_thread_view(self.config)})

    def _post_thread_action(self) -> None:
        """Undo or redo one thread entry's own action (v0.18).

        The body names the entry and the move. The move resolves
        through saved checkpoints only (see _thread_action_move):
        no provider or transcription call happens on this route.
        The answer is the updated thread view plus the plain
        result line, which is also posted into the thread.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        entry_id = str(body.get("entry_id") or "")
        move = str(body.get("move") or "")
        if not entry_id or move not in ("undo", "redo"):
            self._error(
                400, "Say which message, and whether to undo or redo it."
            )
            return
        try:
            result = _thread_action_move(self.config, entry_id, move)
        except ValueError as exc:
            self._error(400, str(exc))
            return
        view = _thread_view(self.config)
        entry_view = result.get("entry") or {}
        for candidate in view["entries"]:
            if candidate.get("id") == entry_view.get("id"):
                entry_view = candidate
                break
        payload = {
            "ok": True,
            "result": result["line"],
            "line": result["line"],
            "moved": result["moved"],
            "fallback": result["fallback"],
            "entry": entry_view,
            "thread": view,
            **view,
        }
        self._send_json(200, payload)

    def _post_thread_read(self) -> None:
        """Mark the thread's editor and run entries read."""
        if self._read_json_body() is None:
            self._error(400, "request body must be a JSON object")
            return
        entries = _load_thread(self.config)
        changed = False
        for entry in entries:
            if entry.get("unread"):
                entry["unread"] = False
                changed = True
        if changed:
            _save_thread(self.config, entries)
        self._send_json(200, {"ok": True, "waiting_count": _waiting_count(self.config)})

    def _post_review_verdict(self) -> None:
        """Settle one review suggestion (v0.17, feedback item 26)."""
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        try:
            view = _review_verdict(
                self.config,
                str(body.get("review_id") or ""),
                str(body.get("decision") or ""),
            )
        except ValueError as exc:
            self._error(400, str(exc))
            return
        self._send_json(200, {"ok": True, **view})

    def _post_editor_name(self) -> None:
        """Rename the editor. The name is the creator's, not this job's."""
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        name = str(body.get("name") or "").strip()
        if not name:
            self._error(400, "Give the editor a name first.")
            return
        saved = usual.save_editor_name(name)
        self._send_json(200, {"ok": True, "editor_name": saved})

    def _post_thread_offer(self) -> None:
        """Run the offer chip on one thread entry (item 8).

        The tap carries only the entry id; the action and its
        terms were fixed when the editor made the offer, so a
        tap can never become a different or paid action. The
        result entry is returned with the whole thread view, so
        the page redraws the conversation as it now stands.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        if not self.config.has_job:
            self._error(400, "Bring a video in first.")
            return
        entry = _find_thread_entry(self.config, body.get("entry_id"))
        if entry is None or not entry.get("offer"):
            self._error(400, "That offer is not open anymore.")
            return
        result = _run_offer(self.config, entry)
        result_view = next(
            (
                view
                for view in _thread_entries_view(self.config)
                if view.get("id") == result.get("id")
            ),
            dict(result),
        )
        self._send_json(
            200,
            {
                "ok": True,
                "kind": "offer",
                "entry": result_view,
                "thread": _thread_view(self.config),
            },
        )

    def _post_thread_cleanup(self) -> None:
        """Flip one clean-up choice from the Editor panel (E2).

        The chip row's one route: the flip goes through the same
        setting path the choice's existing control uses (see
        _apply_cleanup), and the thread gains the editor entry
        that says what changed."""
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        key = str(body.get("key") or "")
        if key not in _CLEANUP_LABELS:
            self._error(
                400, "That clean-up choice is not one the panel has."
            )
            return
        on = body.get("on")
        if not isinstance(on, bool):
            self._error(
                400, "Say whether the clean-up choice is on or off."
            )
            return
        entry = _apply_cleanup(self.config, key, on)
        view = _thread_view(self.config)
        payload = {
            "ok": True,
            "key": key,
            "on": on,
            "entry": entry,
            "thread": view,
            **view,
        }
        self._send_json(200, payload)

    def _post_thread_message(self) -> None:
        """Send one message in the Editor panel (Batch E1)."""
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        text = str(body.get("text") or "").strip()
        queue = body.get("queue")
        if queue is not None and not isinstance(queue, list):
            self._error(
                400, "The queued notes on that message are not shaped right."
            )
            return
        if not text and not queue:
            self._error(400, "Write or say something first.")
            return
        moment = body.get("moment")
        if moment is not None and not isinstance(moment, dict):
            self._error(400, "The moment on that message is not shaped right.")
            return
        reply_to = body.get("reply_to")
        if reply_to is not None and not isinstance(reply_to, str):
            self._error(400, "The message that one answers is not shaped right.")
            return
        # The player's position in the output, when the page
        # knows one (0.27, fix 11): a bare "why did you cut
        # that?" answers about the cut nearest it. Anything
        # that is not a plain number is dropped, never trusted.
        playhead_s = body.get("playhead_s")
        if isinstance(playhead_s, bool) or not isinstance(
            playhead_s, (int, float)
        ):
            playhead_s = None
        elif playhead_s < 0:
            playhead_s = None
        try:
            result = _chat_send(
                self.config, text, moment, queue,
                reply_to=reply_to, playhead_s=playhead_s,
            )
        except ValueError as exc:
            self._error(400, str(exc))
            return
        except Exception as exc:
            _log_raw_error("editor message", exc)
            self._error(400, "That message could not be settled just now.")
            return
        view = _thread_view(self.config)
        payload = {"ok": True, **result, "thread": view, **view}
        # The entry the send just made, in its page shape (claims
        # timed fresh from the words).
        if result.get("entry") and isinstance(result["entry"], dict):
            for view in payload["entries"]:
                if view.get("id") == result["entry"].get("id"):
                    payload["entry"] = view
                    break
        self._send_json(200, payload)

    def _post_signin(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        kind = str(body.get("kind") or "meta").strip().lower()
        if kind not in KEY_KINDS:
            self._error(
                400,
                "Choose which key it is: Meta, ElevenLabs, "
                "Baseten, or Fireworks.",
            )
            return
        key = body.get("api_key")
        if not isinstance(key, str) or not key.strip():
            self._error(400, "Paste the API key first.")
            return
        # Memory only: the key is never written to a file and never
        # sent back; the response carries the sign-in state alone.
        _set_session_key(self.config, kind, key.strip())
        self._send_json(200, {"ok": True, **_signin_state(self.config)})

    def _post_signin_save(self) -> None:
        """Save a pasted key on this Mac, in the launcher's own files.

        The same two files the launcher reads at start, with the
        same permissions, so the key is simply there next time, in
        this kit or a new one. The saved key also serves this
        session right away, like a paste does.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        kind = str(body.get("kind") or "").strip().lower()
        if kind not in KEY_KINDS:
            self._error(
                400,
                "Choose which key it is: Meta, ElevenLabs, "
                "Baseten, or Fireworks.",
            )
            return
        key = body.get("api_key")
        if not isinstance(key, str) or not key.strip():
            self._error(
                400,
                "Paste the key first, then save it on this Mac.",
            )
            return
        try:
            _save_mac_key(kind, key)
        except OSError:
            _log_raw_error("save key on this Mac", OSError("write failed"))
            self._error(
                400,
                "The key could not be saved on this Mac. It still "
                "works for this session if you use it that way.",
            )
            return
        _set_session_key(self.config, kind, key.strip())
        self._send_json(200, {"ok": True, **_signin_state(self.config)})

    def _post_signin_remove(self) -> None:
        """Remove a key saved on this Mac.

        The file goes; if this session's pasted key is the one that
        was saved, it goes too. A key the server started with (from
        its environment) is untouched: only the saved copy is the
        card's to remove, and the state says what remains.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        kind = str(body.get("kind") or "").strip().lower()
        if kind not in KEY_KINDS:
            self._error(
                400,
                "Choose which key it is: Meta, ElevenLabs, "
                "Baseten, or Fireworks.",
            )
            return
        saved = _read_mac_key(kind)
        _remove_mac_key(kind)
        if saved is not None and _session_key(self.config, kind) == saved:
            _set_session_key(self.config, kind, None)
        self._send_json(200, {"ok": True, **_signin_state(self.config)})

    def _post_saved_transcripts_clear(self) -> None:
        """Clear the saved transcripts on this Mac (fix item 5).

        Every saved copy goes; the next run of each video writes
        down its words again. The answer carries how many were
        cleared and the new count (zero), which the Settings
        panel also reads on its own from /api/saved-transcripts.
        """
        if self._read_json_body() is None:
            self._error(400, "request body must be a JSON object")
            return
        store_root = (
            self.config.launch_home / saved_transcripts.STORE_DIR_NAME
        )
        cleared = saved_transcripts.clear_saved(store_root)
        self._send_json(
            200,
            {
                "ok": True,
                "cleared": cleared,
                "saved_transcripts_count": saved_transcripts.count_saved(
                    store_root
                ),
                "message": (
                    "Saved transcripts cleared. The next run of "
                    "each video will write down its words again."
                ),
            },
        )

    def _post_signin_check(self) -> None:
        if self._read_json_body() is None:
            self._error(400, "request body must be a JSON object")
            return
        accepted, message = _check_signin(self.config)
        hosted = {}
        for route in HOSTED_SPARK_ROUTES:
            result = _check_hosted(self.config, route)
            if result is not None:
                hosted[route] = result
        elevenlabs = _check_elevenlabs(self.config)
        self._send_json(
            200,
            {
                "ok": True,
                "accepted": accepted,
                "message": message,
                "hosted": hosted,
                "elevenlabs": elevenlabs,
            },
        )

    def _post_spark_route(self) -> None:
        """Set the route Spark-tier work rides (v0.8.1).

        The choice is creator-level: it is written into the
        creator's usual, so it persists across jobs and restarts
        like the front door choices do, and the next run on any
        surface uses it. Wonder is not part of the choice: it
        only exists on Meta.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        route = str(body.get("route") or "").strip().lower()
        if route not in ("meta",) + HOSTED_SPARK_ROUTES:
            self._error(400, "Choose Meta, Baseten, or Fireworks.")
            return
        settings = usual.load_usual(dict(FRONT_DOOR_DEFAULTS))
        settings["spark_route"] = route
        usual.save_usual(settings)
        self._send_json(200, {"ok": True, **_signin_state(self.config)})

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
            # A comment may be pinned to a word selection instead
            # of a bare time (v0.9.0, item 20): the range is the
            # note's target, stored with it and shown only there.
            # Both ends or neither; a half range is rejected rather
            # than guessed at, so a note can never land on words it
            # was not written on.
            word_start = item.get("word_start")
            word_end = item.get("word_end")
            if (word_start is None) != (word_end is None):
                self._error(
                    400,
                    f"comment {index} has only half a word range",
                )
                return
            stored = {
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
            if word_start is not None:
                try:
                    word_start = int(word_start)
                    word_end = int(word_end)
                except (TypeError, ValueError):
                    self._error(
                        400, f"comment {index} has a bad word range"
                    )
                    return
                if word_start < 0 or word_end <= word_start:
                    self._error(
                        400, f"comment {index} has a bad word range"
                    )
                    return
                stored["word_start"] = word_start
                stored["word_end"] = word_end
                word_text = str(item.get("word_text") or "").strip()
                if word_text:
                    stored["word_text"] = word_text
            comments.append(stored)
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
            self._error(404, JOB_INCOMPLETE_MESSAGE)
            return
        # The final sweep (0.27.3) settles what the direction
        # run settled, before the review rebuilds and the
        # checkpoint commits.
        _run_final_sweep(self.config)
        try:
            _regenerate_after_review(self.config)
            after_review = True
        except Exception:
            after_review = False
        checkpoints.commit(self.config, "Direction run")
        _account_costs(
            self.config, _direction_cost_entries(audit.get("usage"))
        )
        render_note = None
        if self.config.is_intake_job and (
            audit.get("applied_comment_ids")
            or audit.get("before") != audit.get("after")
        ):
            # The run changed the plan: rebuild the review video
            # so the player shows this edit (FIX A).
            source_name = intake_build.intake_source_video(
                self.config.job_dir
            )
            if source_name:
                source = self.config.job_dir / source_name
                try:
                    media = speech.probe_media(source)
                    has_audio = bool(media.get("has_audio", True))
                except speech.TranscribeError:
                    has_audio = True
                render_note = _render_session_splice(
                    self.config, source, has_audio
                )
        self._send_json(
            200,
            {
                "ok": True,
                "provider": provider_name,
                "render_note": render_note,
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
        before_span_keys = _edit_span_keys(self.config)
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
            self._error(404, JOB_INCOMPLETE_MESSAGE)
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
        try:
            lines = []
            if result.get("length_recap"):
                lines.append(str(result["length_recap"]))
            if result.get("recap"):
                lines.append(str(result["recap"]))
            lines.extend(result.get("lines") or [])
            lines.extend(result.get("mode_lines") or [])
            lines.extend(result.get("cost_lines") or [])
            _append_run_summary_thread(
                self.config,
                lines,
                _claims_for_spans(
                    _new_edit_spans(self.config, before_span_keys)
                ),
            )
        except Exception:
            pass
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

    def _post_gold_verdict(self) -> None:
        """Settle one gold span on the creator's word.

        The body names the span (gold_id), the call (cut or leave),
        and an optional note carrying the creator's context; the
        note rides with the verdict into the learning store. The
        response carries what changed, the gold spans still waiting,
        and the rule suggestions the verdict now supports, so the
        page can refresh in place.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        gold_id = str(body.get("gold_id") or "")
        decision = str(body.get("decision") or "").strip().lower()
        note = str(body.get("note") or "").strip()
        try:
            result = _gold_verdict(self.config, gold_id, decision, note)
        except (ValueError, SchemaError) as exc:
            self._error(400, str(exc))
            return
        self._send_json(200, result)

    def _post_test_video(self) -> None:
        """Mark or unmark the current session's job as a test video.

        The body says whether this is a test video (``test`` true
        or false). The mark is written to the current session: the
        launch session when no job is loaded yet, so the choice can
        be made at the front door, or the job's own session once a
        job is loaded. The answer carries the mark as it now
        stands, the same field the job summary carries.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        on = body.get("test")
        if not isinstance(on, bool):
            on = body.get("test_video")
        if not isinstance(on, bool):
            self._error(400, "Say whether this is a test video.")
            return
        _set_test_video(self.config, on)
        self._send_json(
            200, {"ok": True, "test_video": _is_test_video(self.config)}
        )

    def _post_problem_report(self) -> None:
        """Make the problem report zip (FIX B) and name the file.

        Works with no job open too: the zip then lands in the
        app's home folder and still carries the session log, so
        a failure at the front door can be read, not guessed at.
        """
        try:
            path = _build_problem_report(self.config)
        except Exception as exc:  # one plain sentence, never a stack
            _log_raw_error("problem report", exc)
            self._error(
                500, "The problem report could not be made."
            )
            return
        if self.config.has_job:
            message = (
                f"Your problem report is saved as {path.name} "
                "in this job's folder."
            )
        else:
            message = (
                f"Your problem report is saved as {path.name} "
                "in the app's home folder."
            )
        self._send_json(
            200, {"ok": True, "file": path.name, "message": message}
        )

    def _post_problem_report_reveal(self) -> None:
        """Show a made problem report in Finder (v0.10.1).

        Justin's ask: after a report is made, its name on the
        page is something he can click, so the folder opens on
        his Mac and he can drag the file into the chat. The name
        is checked against the reports this app makes, in the
        folder this app puts them in, and nothing else can be
        asked for. Off a Mac there is no Finder to open; the
        answer then names the folder in plain words instead.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        name = str(body.get("file") or "")
        if (
            not name.startswith("problem-report-")
            or not name.endswith(".zip")
            or "/" in name
            or "\\" in name
        ):
            self._error(400, "That is not a problem report file.")
            return
        base = (
            self.config.job_dir
            if self.config.has_job
            else self.config.launch_home
        )
        path = base / name
        if not path.is_file():
            self._error(
                404,
                "That problem report is not in the folder anymore.",
            )
            return
        opened, message = _reveal_in_finder(path)
        self._send_json(
            200, {"ok": opened, "message": message}
        )

    def _post_why(self) -> None:
        """Save the answer to the why question on its grade record.

        The body names the grade record (entry_id), the choice
        picked (story, sound, say_it, or other), and a note,
        which may be empty unless the creator typed one. The
        answer rides on the record itself, in the same grades
        store as everything else; there is no second store.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        entry_id = str(body.get("entry_id") or "")
        choice = str(body.get("choice") or "")
        if choice not in WHY_CHOICES:
            self._error(
                400,
                "That answer is not one of the choices. Pick one "
                "of the answers shown, or skip the question.",
            )
            return
        note = body.get("note")
        note = note.strip() if isinstance(note, str) else ""
        with _GRADES_LOCK:
            entries = _load_grades(self.config)
            entry = next(
                (
                    e
                    for e in entries
                    if entry_id
                    and (
                        e.get("id") == entry_id
                        or e.get("record_id") == entry_id
                    )
                ),
                None,
            )
            if entry is None:
                self._error(
                    400,
                    "That change could not be found, so your answer "
                    "was not saved.",
                )
                return
            entry["why"] = {"choice": choice, "note": note}
            _save_grades(self.config, entries)
        try:
            _settle_thread_entry(
                self.config,
                {"kind": "why", "entry_id": entry_id, "status": "open"},
                {"status": "settled", "decision": choice},
            )
        except Exception:
            pass
        self._send_json(200, {"ok": True, "entry_id": entry_id})

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
        with _GRADES_LOCK:
            entries = creator_rules.merge_grade_entries(
                _load_grades(self.config), raw
            )
            checkpoints.begin(self.config)
            _save_grades(self.config, entries)
            checkpoints.commit(self.config, "Grades saved")
        # A worked or partly grade on a job a direction run already
        # finished is the other moment a teaching rule turns tested.
        _refresh_teaching_tested(self.config)
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
            "inspiration": _inspiration_view(self.config),
            "suggestions": _rule_suggestions(self.config),
        }

    def _post_inspiration_save(self) -> None:
        """Save style teachings as Inspiration, apart from the rules.

        Only ever on a yes, and only for suggestions the teaching
        run marked as a style (kind "style"). A style teaches the
        surface only, never the creator's retake judgment, so these
        statements land in the Inspiration store at the creator
        level, not in the creator's own rules.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        keys = body.get("pattern_keys")
        if not isinstance(keys, list):
            self._error(400, "pattern_keys must be a list")
            return
        offered = {
            s["pattern_key"]: s for s in _rule_suggestions(self.config)
        }
        entries = _load_inspiration(self.config)
        have = {entry.get("pattern_key") for entry in entries}
        saved = 0
        for key in keys:
            suggestion = offered.get(str(key))
            if (
                suggestion is None
                or suggestion.get("kind") != "style"
                or key in have
            ):
                continue
            entries.append(
                {
                    "pattern_key": key,
                    "statement": suggestion.get("statement"),
                    "saved_at": _utc_now(),
                    "style": str(body.get("style") or "")
                    or _current_style(self.config),
                }
            )
            have.add(key)
            saved += 1
        if saved:
            _save_inspiration(self.config, entries)
        self._send_json(
            200,
            {
                "ok": True,
                "saved": saved,
                "inspiration": _inspiration_view(self.config),
            },
        )

    def _post_rule_custom(self) -> None:
        """Save a rule the creator wrote themselves, in plain words.

        The save tap is the yes; nothing is stored before it. The
        rule is a statement like any other, so the passes that read
        rule statements read this one too.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        statement = str(body.get("statement") or "").strip()
        if not statement:
            self._error(400, "Write the rule first, in plain words.")
            return
        if len(statement) > 300:
            self._error(
                400,
                "That is long for one rule. Keep it to a sentence or two.",
            )
            return
        key = (
            "custom-"
            + hashlib.sha1(statement.encode("utf-8")).hexdigest()[:12]
        )
        suggestion = {
            "pattern_key": key,
            "statement": statement,
            "rule_class": "small",
            "support_count": 0,
            "explicit": True,
            "source": "creator",
        }
        store = _rules_store(self.config)
        rule = creator_rules.save_rule(store, suggestion)
        rule["style"] = str(body.get("style") or "") or _current_style(self.config)
        checkpoints.begin(self.config)
        _save_rules_store(self.config, store)
        checkpoints.commit(self.config, "Creator's own rule saved")
        response = self._rules_state_response()
        response["rule"] = {
            "rule_id": rule.get("rule_id"),
            "statement": rule.get("statement"),
        }
        self._send_json(200, response)

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
        rule["style"] = str(body.get("style") or "") or _current_style(self.config)
        checkpoints.begin(self.config)
        _save_rules_store(self.config, store)
        checkpoints.commit(self.config, "Rule saved")
        try:
            proposal_open = any(
                entry.get("kind") == "rule_proposal"
                and entry.get("pattern_key") == pattern_key
                and entry.get("status") == "open"
                for entry in _load_thread(self.config)
            )
            if proposal_open:
                # A save that answers a verdict's proposal
                # (source (c)) settles the proposal and says
                # what was learned, in one line.
                _settle_thread_entry(
                    self.config,
                    {
                        "kind": "rule_proposal",
                        "pattern_key": pattern_key,
                        "status": "open",
                    },
                    {"status": "settled", "decision": "saved"},
                )
                _append_thread(
                    self.config,
                    {
                        "kind": "rule",
                        "status": "settled",
                        "text": (
                            "Thanks. Learned: "
                            + str(rule.get("statement") or "")
                        ),
                    },
                )
            else:
                _append_thread(
                    self.config,
                    {
                        "kind": "rule",
                        "status": "settled",
                        "text": (
                            "Saved as one of your rules: "
                            + str(rule.get("statement") or "")
                        ),
                    },
                )
        except Exception:
            pass
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
        if pattern_key.startswith("teach:"):
            # A passed-over teaching draft leaves the session's
            # draft list too, so it does not come back on its own.
            teaching.remove_draft(
                self.config.session_dir, pattern_key[len("teach:"):]
            )
        try:
            _settle_thread_entry(
                self.config,
                {
                    "kind": "rule_proposal",
                    "pattern_key": pattern_key,
                    "status": "open",
                },
                {"status": "settled", "decision": "passed"},
            )
        except Exception:
            pass
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

    def _post_rule_edit(self) -> None:
        """Change a saved rule's own words, in place (Message 2R).

        A thin route: no existing endpoint could edit a rule's
        statement; remove-and-re-add would lose its examples and
        its place. The edit is the creator's yes, like the save.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        rule_id = str(body.get("rule_id") or "")
        statement = str(body.get("statement") or "").strip()
        if not statement:
            self._error(400, "Write the rule first, in plain words.")
            return
        if len(statement) > 300:
            self._error(
                400,
                "That is long for one rule. Keep it to a sentence or two.",
            )
            return
        store = _rules_store(self.config)
        target = next(
            (r for r in store.get("rules", []) if r.get("rule_id") == rule_id),
            None,
        )
        if target is None:
            self._error(400, "That rule is not saved.")
            return
        checkpoints.begin(self.config)
        target["statement"] = statement
        _save_rules_store(self.config, store)
        checkpoints.commit(self.config, "Rule changed")
        self._send_json(200, self._rules_state_response())

    def _post_styles(self) -> None:
        """Select, create, or rename a named style (Message 2R, P4).

        A thin route: styles are new in this round and no existing
        endpoint can hold them. Styles live in the launch home
        beside the rules store; renaming retags the rules and
        Inspiration that carried the old name, so nothing is lost.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        action = str(body.get("action") or "")
        data = _load_styles(self.config)
        names = [entry["name"] for entry in data["styles"]]
        if action == "select":
            name = str(body.get("name") or "").strip()
            if name not in names:
                self._error(400, "That style is not saved.")
                return
            data["current"] = name
            _save_styles(self.config, data)
        elif action == "new":
            name = str(body.get("name") or "").strip()
            if not name:
                self._error(400, "Give the style a name first.")
                return
            if len(name) > 60:
                self._error(400, "That name is long for a style. Keep it short.")
                return
            if name in names:
                self._error(400, "A style with that name is already saved.")
                return
            data["styles"].append({"name": name})
            data["current"] = name
            _save_styles(self.config, data)
        elif action == "rename":
            name = str(body.get("name") or "").strip()
            new_name = str(body.get("new_name") or "").strip()
            if name not in names:
                self._error(400, "That style is not saved.")
                return
            if not new_name or len(new_name) > 60:
                self._error(400, "Give the style a name first.")
                return
            if new_name in names and new_name != name:
                self._error(400, "A style with that name is already saved.")
                return
            for entry in data["styles"]:
                if entry["name"] == name:
                    entry["name"] = new_name
            if data["current"] == name:
                data["current"] = new_name
            _save_styles(self.config, data)
            store = _rules_store(self.config)
            touched = False
            for rule in store.get("rules", []):
                if str(rule.get("style") or DEFAULT_STYLE_NAME) == name:
                    rule["style"] = new_name
                    touched = True
            if touched:
                _save_rules_store(self.config, store)
            entries = _load_inspiration(self.config)
            touched = False
            for entry in entries:
                if str(entry.get("style") or DEFAULT_STYLE_NAME) == name:
                    entry["style"] = new_name
                    touched = True
            if touched:
                _save_inspiration(self.config, entries)
        else:
            self._error(400, "Unknown style action.")
            return
        self._send_json(
            200, {"ok": True, "styles": _styles_view(self.config)}
        )

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

    def _post_word_range(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        start = body.get("word_start")
        end = body.get("word_end")
        action = body.get("action")
        if (
            not isinstance(start, int) or isinstance(start, bool)
            or not isinstance(end, int) or isinstance(end, bool)
            or action not in ("cut", "restore")
        ):
            self._error(
                400,
                "word_start, word_end, and an action of cut or "
                "restore are required",
            )
            return
        try:
            result = _apply_word_range(self.config, start, end, action)
        except ValueError as exc:
            self._error(400, str(exc))
            return
        except (SchemaError, ApplyError) as exc:
            # The verifier and the applier mirror each other, so
            # this should not happen; if it ever does, the words
            # stay put and the page still gets one plain sentence,
            # never a code.
            _log_raw_error("word-range", exc)
            self._send_json(
                200,
                {
                    "ok": True,
                    "changed": False,
                    "moved": 0,
                    "stayed": [
                        {
                            "word_start": start,
                            "word_end": end,
                            "why": "protected",
                            "gold_id": None,
                            "message": (
                                "The safety check would not move "
                                "those words, so they were left "
                                "as they are."
                            ),
                        }
                    ],
                },
            )
            return
        self._send_json(200, result)

    def _post_join_pause(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        after = body.get("after_word_index")
        before = body.get("before_word_index")
        target = body.get("target_total_s")
        if (
            not isinstance(after, int) or isinstance(after, bool)
            or not isinstance(before, int) or isinstance(before, bool)
            or not isinstance(target, (int, float))
            or isinstance(target, bool)
        ):
            self._error(
                400,
                "after_word_index and before_word_index must be "
                "whole numbers and target_total_s a number",
            )
            return
        try:
            result = _apply_join_pause(
                self.config, after, before, float(target)
            )
        except ValueError as exc:
            self._error(400, str(exc))
            return
        except (SchemaError, ApplyError) as exc:
            _log_raw_error("join-pause", exc)
            self._send_json(
                200,
                {
                    "ok": True,
                    "changed": False,
                    "message": (
                        "The safety check would not change that "
                        "pause, so it was left as it is."
                    ),
                },
            )
            return
        self._send_json(200, result)

    def _post_gap_erase(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        after = body.get("after_word_index")
        before = body.get("before_word_index")
        if (
            not isinstance(after, int) or isinstance(after, bool)
            or not isinstance(before, int) or isinstance(before, bool)
        ):
            self._error(
                400,
                "after_word_index and before_word_index must be "
                "whole numbers",
            )
            return
        try:
            result = _apply_gap_erase(self.config, after, before)
        except ValueError as exc:
            self._error(400, str(exc))
            return
        except (SchemaError, ApplyError) as exc:
            _log_raw_error("gap-erase", exc)
            self._send_json(
                200,
                {
                    "ok": True,
                    "changed": False,
                    "message": (
                        "The safety check would not change that "
                        "gap, so it was left as it is."
                    ),
                },
            )
            return
        self._send_json(200, result)

    # -- manual timeline edits (v0.16) --------------------------------

    @staticmethod
    def _whole(value) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    @staticmethod
    def _number(value) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def _post_timeline_split(self) -> None:
        body = self._read_json_body()
        if body is None or not self._number(body.get("output_time")):
            self._error(400, "output_time must be a number")
            return
        try:
            result = _timeline_split(self.config, float(body["output_time"]))
        except ValueError as exc:
            self._error(400, str(exc))
            return
        self._send_json(200, result)

    def _post_timeline_trim(self) -> None:
        body = self._read_json_body()
        if (
            body is None
            or not self._whole(body.get("range_index"))
            or body.get("edge") not in ("start", "end")
            or not self._number(body.get("new_time"))
        ):
            self._error(
                400,
                "range_index must be a whole number, edge must be "
                "'start' or 'end', and new_time must be a number",
            )
            return
        try:
            result = _timeline_trim(
                self.config,
                body["range_index"],
                body["edge"],
                float(body["new_time"]),
            )
        except ValueError as exc:
            self._error(400, str(exc))
            return
        self._send_json(200, result)

    def _post_timeline_delete(self) -> None:
        body = self._read_json_body()
        if body is None or not self._whole(body.get("range_index")):
            self._error(400, "range_index must be a whole number")
            return
        try:
            result = _timeline_delete(self.config, body["range_index"])
        except ValueError as exc:
            self._error(400, str(exc))
            return
        self._send_json(200, result)

    def _post_timeline_move(self) -> None:
        body = self._read_json_body()
        if (
            body is None
            or not self._whole(body.get("from_index"))
            or not self._whole(body.get("to_index"))
        ):
            self._error(
                400, "from_index and to_index must be whole numbers"
            )
            return
        try:
            result = _timeline_move(
                self.config, body["from_index"], body["to_index"]
            )
        except ValueError as exc:
            self._error(400, str(exc))
            return
        self._send_json(200, result)

    def _post_timeline_paste(self) -> None:
        body = self._read_json_body()
        if (
            body is None
            or not isinstance(body.get("spans"), list)
            or not self._number(body.get("output_time"))
        ):
            self._error(
                400, "spans must be a list and output_time a number"
            )
            return
        try:
            result = _timeline_paste(
                self.config, body["spans"], float(body["output_time"])
            )
        except ValueError as exc:
            self._error(400, str(exc))
            return
        self._send_json(200, result)

    def _post_timeline_fade(self) -> None:
        body = self._read_json_body()
        if (
            body is None
            or not self._whole(body.get("join_index"))
            or not (
                body.get("fade_s") is None or self._number(body.get("fade_s"))
            )
        ):
            self._error(
                400, "join_index must be a whole number and fade_s "
                "a number or null",
            )
            return
        try:
            result = _timeline_fade(
                self.config,
                body["join_index"],
                None if body.get("fade_s") is None else float(body["fade_s"]),
            )
        except ValueError as exc:
            self._error(400, str(exc))
            return
        self._send_json(200, result)

    def _post_join_call(self) -> None:
        body = self._read_json_body()
        if (
            body is None
            or not self._whole(body.get("join_index"))
            or body.get("move") not in ("smooth", "keep")
        ):
            self._error(
                400, "join_index must be a whole number and move "
                "must be smooth or keep",
            )
            return
        try:
            result = _join_call(
                self.config, body["join_index"], body["move"]
            )
        except ValueError as exc:
            self._error(400, str(exc))
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

        def word_statuses() -> list:
            transcript = _load_json(
                str(self.config.job_dir / "transcript.json")
            )
            timeline = Timeline(transcript, _effective_cuts(self.config))
            return [
                timeline.word_status(i)
                for i in range(len(transcript.get("words", [])))
            ]

        before_statuses = word_statuses() if undo else None
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
        response = {"ok": True, **moved}
        if undo and before_statuses is not None:
            # An undo that brings back exactly one run of cut
            # words is a restore like any other: it writes the
            # same grade record, and the page asks why about it.
            # An undo that brings back more than one run, or no
            # words at all, writes no record and asks nothing.
            after_statuses = word_statuses()
            brought_back = [
                index
                for index, (before, after) in enumerate(
                    zip(before_statuses, after_statuses)
                )
                if before == "cut" and after == "kept"
            ]
            runs: list = []
            for index in brought_back:
                if runs and index == runs[-1][1]:
                    runs[-1] = (runs[-1][0], index + 1)
                else:
                    runs.append((index, index + 1))
            if len(runs) == 1:
                # The grade record still lands (the undo is a
                # signal like any other), but the editor never
                # questions him after an Undo (item 11): no why
                # question on the thread, no prompt in the reply.
                _record_restore_grade(
                    self.config,
                    runs[0][0],
                    runs[0][1],
                    "history-undo",
                    ask_why=False,
                )
        _refresh_after_review(self.config)
        self._send_json(200, response)

    def _post_captions_pref(self) -> None:
        """Set whether ums and uhs are hidden in this job's captions.

        The switch lives where the captions are handed back (the
        Revised edit card, and the finish card). The choice is saved
        in the session; for a job the app took in itself it also
        updates the job's marker, and the caption file is rebuilt
        (on) or removed (off) right away, so what downloads always
        matches the switch. For a runner job the choice is recorded
        and a caption file that exists is removed when it is off;
        building captions stays the intake's own work.
        """
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        on = body.get("hide_fillers")
        if not isinstance(on, bool):
            self._error(
                400,
                "Say whether ums and uhs stay hidden in the captions.",
            )
            return
        available = _set_captions_hide_fillers(self.config, on)
        self._send_json(
            200,
            {
                "ok": True,
                "captions_hide_fillers": on,
                "captions_available": available,
            },
        )

    def _post_render_intake(self, aspect=None) -> None:
        """Render an intake job with the app's own splice.

        Runner jobs render through the runner's script, untouched.
        A job born here from a raw video has no runner pieces, so
        its kept ranges are spliced from the dropped video itself.
        Clean captions are rebuilt from the same plan when the job
        was born with them on. ``aspect`` is the framing choice
        ("9:16" or "16:9"); absent, the recorded framing is kept
        and nothing is cropped.
        """
        session = self.config.session_dir
        revised = session / "revised_cuts.json"
        if not revised.is_file():
            self._send_json(
                200,
                {
                    "ok": False,
                    "missing": [],
                    "error": "Run the first edit or direction "
                    "first, so there is a revised plan to render.",
                },
            )
            return
        source_name = intake_build.intake_source_video(
            self.config.job_dir
        )
        source = (
            self.config.job_dir / source_name if source_name else None
        )
        if source is None or not source.is_file():
            self._send_json(
                200,
                {
                    "ok": False,
                    "missing": ["source_video"],
                    "error": "The video this job came from is not "
                    "in the folder anymore, so the fixed video "
                    "cannot be built.",
                },
            )
            return
        try:
            media = speech.probe_media(source)
            has_audio = bool(media.get("has_audio", True))
        except speech.TranscribeError:
            has_audio = True
        crop, framing_note = _render_framing(
            self.config, source, aspect
        )
        failure = _render_session_splice(
            self.config, source, has_audio, crop=crop
        )
        if failure is not None:
            self._send_json(200, {"ok": False, "error": failure})
            return
        crossfade_on = bool(
            _front_door_settings(self.config).get("crossfade", True)
        )
        marker_path = self.config.job_dir / intake_build.INTAKE_MARKER
        try:
            marker = _load_json(str(marker_path))
        except (OSError, ValueError):
            marker = {}
        if marker.get("captions"):
            try:
                clean_captions.build_captions(
                    self.config.job_dir,
                    _effective_cuts(self.config),
                    session / clean_captions.CAPTIONS_FILENAME,
                )
            except Exception as exc:
                _log_raw_error("clean captions", exc)
        try:
            _regenerate_after_review(self.config)
        except Exception:
            pass
        response = {"ok": True, "crossfade": crossfade_on}
        if framing_note is not None:
            response["framing"] = framing_note
        self._send_json(200, response)

    def _post_render(self) -> None:
        body = self._read_json_body()
        if body is None:
            self._error(400, "request body must be a JSON object")
            return
        aspect = body.get("aspect")
        if aspect not in framing.ASPECTS:
            aspect = None
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
        if self.config.is_intake_job:
            self._post_render_intake(aspect)
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
        _record_splice_built(self.config)
        try:
            _regenerate_after_review(self.config)
        except Exception:
            pass
        response = {"ok": True}
        if aspect is not None:
            # A shape was asked for, but this job renders through
            # the runner's script, which the app does not change:
            # say plainly that the recorded framing was kept.
            response["framing"] = (
                f"Framing: the {aspect} shape applies to videos "
                "the app took in itself. This job renders "
                "through the runner, so the recorded framing "
                "was kept."
            )
        self._send_json(200, response)


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
    # A job loaded at start gets its watchable copies building
    # right away, in the background, so no play request ever
    # waits on a conversion.
    _kick_playback_copies(config)
    # Drag-and-drop intake is anchored to the session area the
    # server started with, so staged drops stay in one known place
    # even after a dropped job becomes the current job and the
    # config is swapped for the staged copy's.
    httpd.intake_root = config.session_dir / INTAKE_DIR_NAME  # type: ignore[attr-defined]
    httpd.intakes = {}  # type: ignore[attr-defined]
    httpd.first_edit_jobs = {}  # type: ignore[attr-defined]
    httpd.finish_exports = {}  # type: ignore[attr-defined]
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


def _default_launch_home() -> Path:
    """The launch home when nobody names one: the folder the app
    already keeps beside the user's sign-in key file."""
    return Path.home() / ".talk-to-your-editor" / "launch-home"


def serve(
    job_dir: Optional[str] = None,
    host: str = "0.0.0.0",
    port: int = 8765,
    render_py: Optional[str] = None,
    source_video: Optional[str] = None,
    cleaned_wav: Optional[str] = None,
    launch_home: Optional[str] = None,
) -> int:
    """Run the test app, with a job loaded or with none.

    With no job the server still opens: the home page is the front
    door (drop zone, built-in sample, sign-in check), and the
    launch home holds the session and the staged drops until a job
    arrives. With a job, everything behaves as it always has.
    """
    home = Path(launch_home) if launch_home else _default_launch_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError:
        print(
            f"error: the launch folder could not be made: {home}",
            file=sys.stderr,
        )
        return 2
    config: WebAppConfig
    if job_dir is None:
        config = WebAppConfig(None, launch_home=home)
    else:
        job = Path(job_dir)
        if job.is_file():
            # A video file is the mistake everyone makes first; say
            # what was handed over and what the app actually runs
            # on, plainly.
            print(
                f"error: {job} is a file, not a folder. Talk to Your "
                "Editor runs on a runner job folder (the folder "
                "holding transcript.json and cuts.json), not on a "
                "video file. Or start with no job and drop the "
                "folder on the page.",
                file=sys.stderr,
            )
            return 2
        if not job.is_dir():
            print(
                f"error: job folder not found: {job}", file=sys.stderr
            )
            return 2
        for required in ("transcript.json", "cuts.json"):
            if not (job / required).is_file():
                print(
                    f"error: {job / required} not found",
                    file=sys.stderr,
                )
                return 2
        config = WebAppConfig(
            job,
            render_py=render_py,
            source_video=source_video,
            cleaned_wav=cleaned_wav,
            launch_home=home,
        )
    httpd = create_server(config, host=host, port=port)
    actual_port = httpd.server_address[1]
    if config.has_job:
        print(
            f"Talk to Your Editor test app, job: {config.job_dir}",
            flush=True,
        )
    else:
        print(
            "Talk to Your Editor test app, no job loaded yet.",
            flush=True,
        )
        print(
            "Drop a runner job folder on the page, or try the "
            "built-in sample.",
            flush=True,
        )
    print(f"On this computer: http://127.0.0.1:{actual_port}/", flush=True)
    lan = _lan_ip()
    if lan:
        print(f"On your phone:    http://{lan}:{actual_port}/", flush=True)
    print(
        "Your phone must be on the same Wi-Fi as this computer.",
        flush=True,
    )
    if config.has_job and config.render_missing():
        print(
            config.render_message()
            + " Direction still works; rendering is off.",
            flush=True,
        )
    elif config.has_job and config.render_autowired:
        print(config.render_message(), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0
