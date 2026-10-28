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

import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import captions as clean_captions
from . import checkpoints, costs, creator_rules, retakes, usual
from . import direction_parse, intake_build
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


PLAYBACK_BUILDING_MESSAGE = (
    "Your video is being made watchable here. A long video can "
    "take a few minutes."
)
PLAYBACK_FAILED_MESSAGE = (
    "This video could not be made watchable here, so it cannot be "
    "shown yet. The edit still works from the words."
)
PLAYBACK_GETTING_READY = "Getting your video ready to watch…"

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
        return False
    attempts = _playback_attempts()
    if not attempts:
        print(
            "[video] this machine's ffmpeg has neither "
            "h264_videotoolbox nor libx264, so no watchable copy "
            f"can be made of {source.name}",
            file=sys.stderr,
        )
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / (dest.name + ".part")
    nice = ["nice", "-n", "10"] if shutil.which("nice") else []
    duration_s = _probe_duration_s(source)
    attempt_cap, total_cap = _playback_bounds(duration_s)
    started = time.monotonic()
    with _PLAYBACK_CONVERT_LOCK:
        for input_args, video_args in attempts:
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
                progress,
            )
            if ok and tmp.is_file() and tmp.stat().st_size > 0:
                tmp.replace(dest)
                return True
            if tail:
                print(
                    f"[video] conversion of {source.name} failed:\n"
                    f"{tail}",
                    file=sys.stderr,
                )
            tmp.unlink(missing_ok=True)
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
        server started with, the way the Meta key works.
        """
        if self.session_elevenlabs_key:
            return self.session_elevenlabs_key
        return os.environ.get("ELEVENLABS_API_KEY") or None

    def baseten_key(self) -> Optional[str]:
        """The creator's Baseten key, when the server has one.

        Used only by Spark-tier model work when the Baseten route
        is chosen. A key pasted on the page for this session wins
        over the environment the server started with, the way the
        other keys work.
        """
        if self.session_baseten_key:
            return self.session_baseten_key
        return os.environ.get("BASETEN_API_KEY") or None

    def fireworks_key(self) -> Optional[str]:
        """The creator's Fireworks key, when the server has one.

        Used only by Spark-tier model work when the Fireworks
        route is chosen, under the same discipline as the Baseten
        key.
        """
        if self.session_fireworks_key:
            return self.session_fireworks_key
        return os.environ.get("FIREWORKS_API_KEY") or None

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
    "breaths": "keep",
    "remove_fillers": True,
    "mode": "cleanest",
    "spark_route": "meta",
}
# How involved the creator wants to be (v0.7.1). "cleanest" is the
# Cleanest cut: the editor finishes what it is sure about and the
# spots it is unsure about wait in gold. "tailor" is Tailor it with
# me: every unsure spot is presented as a decision. The mode is one
# of the front door choices, so it is saved with them, carried in
# the creator's usual, and named in every recap.
INVOLVEMENT_MODES = ("cleanest", "tailor")
MODE_LABELS = {
    "cleanest": "Cleanest cut",
    "tailor": "Tailor it with me",
}
BUFFER_MIN_S = 0.05
BUFFER_MAX_S = 0.50
# Breaths are a choice, not a law (v0.6.1). "keep" leaves the
# natural breath before a sentence alone; "trim" lets the buffer
# rule the whole join, breath and all. The keep floor comes from
# the app's own waveform measurement: ``measure_speech_spans``
# bridges quiet gaps of up to 0.30 seconds as part of one stretch
# of speech, so 0.30 seconds is the longest quiet this app itself
# still calls speaking. Keeping breaths therefore never trims a
# join below 0.30 seconds of quiet: the in-breath that starts the
# next sentence lives in the last stretch of the pause, and the
# floor is set conservatively at that measured line. Where a plan
# was built from measured speech spans (an intake job), the join
# being trimmed is exactly a measured gap between spans; the same
# floor applies to runner jobs, whose joins are measured the same
# two ways (the quiet tail of one piece plus the lead-in to the
# next word).
BREATH_MODES = ("keep", "trim")
BREATH_FLOOR_S = 0.30
BREATHS_RECAP = {
    "keep": "Breaths are being kept natural.",
    "trim": "Breaths are trimmed with the pauses.",
}
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
        for key in ("remove_retakes", "cut_silence", "remove_fillers"):
            if isinstance(saved.get(key), bool):
                settings[key] = saved[key]
        buffer = saved.get("buffer_s")
        if isinstance(buffer, (int, float)) and not isinstance(buffer, bool):
            settings["buffer_s"] = min(
                BUFFER_MAX_S, max(BUFFER_MIN_S, float(buffer))
            )
        if saved.get("keep_mode") in retakes.KEEP_MODES:
            settings["keep_mode"] = saved["keep_mode"]
        if saved.get("breaths") in BREATH_MODES:
            settings["breaths"] = saved["breaths"]
        if saved.get("mode") in INVOLVEMENT_MODES:
            settings["mode"] = saved["mode"]
        if saved.get("spark_route") in HOSTED_SPARK_ROUTES + ("meta",):
            settings["spark_route"] = saved["spark_route"]
    return settings


def _breaths_recap(settings: dict) -> str:
    """The breaths state, named in plain words in every recap."""
    return BREATHS_RECAP[settings.get("breaths", "keep")]


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
                "shadow": meta.get("shadow"),
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
        else:
            entry["start"] = 0.0
            entry["end"] = 0.0
    return (entries, corrections)


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
        "transcript_words": [],
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
        "beta": {
            "flips": 0,
            "settled": 0,
            "matched": 0,
            "recent_calls": 0,
            "line": None,
        },
        "saved_rules": [],
        "rule_suggestions": [],
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
    summary.update(
        {
            "has_job": True,
            "job_name": config.job_dir.name,
            "videos": _job_videos(config.job_dir),
            "playback": {
                name: _playback_state(config, name)
                for name in _job_videos(config.job_dir)
            },
            "playback_progress": {
                name: _playback_progress(config, name)
                for name in _job_videos(config.job_dir)
            },
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
        "has_job": True,
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
        "playback": {
            name: _playback_state(config, name) for name in videos
        },
        "playback_progress": {
            name: _playback_progress(config, name) for name in videos
        },
        "video_shapes": video_shapes,
        "feedback_exists": feedback_exists,
        "comments": comments,
        "overall_feedback": overall_feedback,
        "edit_spans": _edit_spans(config),
        "front_door": _front_door_settings(config),
        "flags": _session_flags(config),
        "gold": _open_gold_spans(config),
        "beta": _beta_score(config),
        "saved_rules": _saved_rules_view(config),
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


def _live_provider(config: WebAppConfig) -> MetaProvider:
    """The live provider, built the one way every run builds it.

    The Meta sign-in keys it; the creator's chosen Spark route
    and any hosted keys ride along, so Spark-tier work (the
    direction reads, the retake judge and its shadow) runs on
    the chosen pool while Wonder stays on Meta.
    """
    return MetaProvider(
        api_key=config.signin_key(),
        allow_wonder=True,
        spark_route=config.spark_route(),
        baseten_key=config.baseten_key(),
        fireworks_key=config.fireworks_key(),
    )


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
    audit = _verify_and_apply(args, {"operations": operations}, session)
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
    one per pool the usage drew on."""
    by_route = costs.split_usage_cost_by_route(usage)
    if not by_route:
        return [costs.make_entry(feature, 0.0, detail, pool="meta")]
    return [
        costs.make_entry(
            feature, spark_usd + wonder_usd, detail, pool=route
        )
        for route, (spark_usd, wonder_usd) in sorted(
            by_route.items(), key=lambda item: costs.POOL_ORDER[item[0]]
        )
    ]


def _first_edit_cost_entries(
    route: str,
    duration_s: float,
    settings: dict,
    retake_summary,
    direction_summary,
) -> list:
    """The cost entries for one first edit, from its passes' records."""
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
    """Joins the silence pass must never trim, under either breaths choice.

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
    config: WebAppConfig, buffer_s: float, breaths: str = "keep"
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

    Breaths (v0.6.1): with ``breaths`` "keep", a join is never
    trimmed below ``BREATH_FLOOR_S`` of quiet, so the natural
    breath before the next sentence survives even when the buffer
    is tighter; with "trim", the buffer alone rules and the breath
    inside a trimmed gap goes with it. Joins a pinned comment or a
    protected moment covers are never proposed under either choice
    (see ``_protected_join_indices``); every trim that does land
    stays a transcript dot with its reason, and restorable.
    """
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    timeline = Timeline(transcript, _effective_cuts(config))
    floor_s = BREATH_FLOOR_S if breaths == "keep" else 0.0
    effective_s = max(buffer_s, floor_s)
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
        if breaths == "keep" and floor_s > buffer_s:
            reason = (
                "front door: trim the silence at this join "
                f"toward {effective_s:g} seconds, keeping the "
                "natural breath"
            )
        else:
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
    config: WebAppConfig, buffer_s: float, breaths: str = "keep"
) -> dict:
    """Trim the session plan's long silences toward ``buffer_s``.

    Runs the join-pause proposals through the same verify-and-apply
    path as a direction run, against the session's current plan. The
    pass is recorded in its own session file, and its page
    operations are merged into the session audit so the removed and
    shortened moments stay explained in one place. ``breaths`` is
    the front door's breaths choice, passed through to the
    operations and named in the summary.
    """
    summary = {
        "ran": True,
        "buffer_s": buffer_s,
        "breaths": breaths,
        "floor_s": BREATH_FLOOR_S if breaths == "keep" else None,
        "joins_trimmed": 0,
        "operations": [],
    }
    operations = _silence_operations(config, buffer_s, breaths)
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
            "breaths": summary.get("breaths", "keep"),
            "joins_trimmed": summary["joins_trimmed"],
        }
    )
    config.session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(path, {"runs": runs})


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
    summary = {"ran": False, "applied": 0, "lines": []}
    if not settings.get("remove_fillers"):
        return summary
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    timeline = Timeline(transcript, _effective_cuts(config))
    profile = _load_optional_file(config.job_dir / "profile.json")
    operations = _filler_operations(timeline, settings, profile)
    if not operations:
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
    return summary


def _direction_provider(config: WebAppConfig):
    """The provider a direction pass reads with.

    The same construction every live run uses (see
    ``_live_provider``): the Meta provider keyed by the session
    sign-in, riding the creator's chosen Spark route. Kept as
    one small helper so the pass has a single provider seam.
    """
    return _live_provider(config)


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
        "gold": 0,
    }
    if not config.signin_key():
        summary["reason"] = "no_key"
        summary["lines"] = [
            "Your direction will be read when a key is saved."
        ]
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
    provider = _direction_provider(config)
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
        try:
            audit = _verify_and_apply(
                args,
                {"operations": operations},
                tmp,
                adjust_verdicts=_negation_policy(
                    settings, remainder, flipped
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
        # from the same audit the page operations came from.
        summary["negation_applied"] = len(flipped)
        _record_gold_spans(
            config,
            _gold_candidates_from_direction(
                page_operations, audit.get("verdicts") or [], words
            ),
        )
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
            "gold": summary["gold"],
        },
    )
    lines: list = []
    if summary["applied"] > 0:
        lines.append(
            f"Read your direction and made {summary['applied']} "
            f"change{'s' if summary['applied'] != 1 else ''}."
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
    summary["lines"] = lines
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
# alone; but in the Cleanest cut mode, a directed cut that Muse
# Wonder has reviewed applies when the words it removes are a
# repeat of words that stay (a cut of a copy cannot change what the
# kept copy says), or when the creator's own direction named that
# span. Every other held negation cut, and every one of them in
# Tailor it mode, waits in gold for the creator's call. When in
# doubt, gold: the duplicate and naming checks are exact on
# purpose, and a miss only means the creator sees the spot.
# ---------------------------------------------------------------------------

# The verdict holds a gold verdict may settle: the ones whose whole
# point is "a person should look at this". Every other hold
# (protected material, a special insert, the 25 percent rule, a
# structural problem) still refuses, gold or not.
GOLD_SOFT_HOLDS = {"negation_bearing_span", "wonder_review_required"}

_GOLD_REASON_NEGATION = (
    "This part includes a negative statement. Changing a negative "
    "can change the meaning, so the editor leaves this one to you."
)
_GOLD_REASON_HELD = (
    "This is a bigger change, so the editor held it for your eyes "
    "before it counts."
)
_GOLD_REASON_RETAKE_HELD = (
    "The editor measured this as a retake, and the safety check "
    "held the cut, so this one is yours to call."
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
    ("held", "cut"): (
        "Making a change the editor held back for your decision, "
        "once you have seen it"
    ),
    ("held", "leave"): (
        "Leaving a change the editor held back for your decision "
        "unmade, once you have seen it"
    ),
}


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
    for candidate in candidates:
        if candidate.get("gold_id") in known:
            continue
        records.append(candidate)
        known.add(candidate.get("gold_id"))
        changed = True
    if changed:
        records.sort(
            key=lambda r: (r.get("word_start", 0), r.get("word_end", 0))
        )
        _save_gold(config, records)


def _gold_view(record: dict) -> dict:
    """One gold span as the page receives it (no stored operation)."""
    return {
        "gold_id": record.get("gold_id"),
        "word_start": record.get("word_start"),
        "word_end": record.get("word_end"),
        "kind": record.get("kind"),
        "reason": record.get("reason"),
        "text": record.get("text"),
        "keep_text": record.get("keep_text"),
        "keep_word_start": record.get("keep_word_start"),
        "keep_word_end": record.get("keep_word_end"),
    }


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
    timeline = Timeline(transcript, _effective_cuts(config))
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
    count = len(_open_gold_spans(config))
    spots = f"{count} spot{'s' if count != 1 else ''}"
    if mode == "tailor":
        if count:
            lines.append(
                f"{spots} in gold "
                f"{'are' if count != 1 else 'is'} waiting for your call."
            )
        else:
            lines.append("No spots are waiting in gold.")
    elif count:
        lines.append(
            f"{spots} in gold for when you want "
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


def _gold_candidates_from_direction(
    page_operations: list, verdict_dicts: list, words: list
) -> list:
    """Gold spans for the direction pass's held operations.

    A held operation becomes gold when its holds are only the
    soft ones (a negation, or a review the pass could not give
    it). Operations held for anything else (protected material, a
    special insert, too much at once) stay set aside exactly as
    before: gold is for judgment calls, not for safety rails. The
    model's own flag_for_review proposals are not held operations
    and never become gold.
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
        candidates.append(
            {
                "gold_id": _gold_id(start, end, kind),
                "word_start": start,
                "word_end": end,
                "kind": kind,
                "source": "direction",
                "reason": (
                    _GOLD_REASON_NEGATION
                    if kind == "negation"
                    else _GOLD_REASON_HELD
                ),
                "text": " ".join(
                    str(words[i].get("word") or "")
                    for i in range(start, end)
                ),
                "keep_text": None,
                "keep_word_start": None,
                "keep_word_end": None,
                "pair_id": None,
                "pair_key": None,
                "operation": operation,
                "status": "open",
                "note": None,
                "created_at": _utc_now(),
                "decided_at": None,
            }
        )
    return candidates


def _gold_candidates_from_retakes(pairs: list, words: list) -> list:
    """Gold spans for the retake pass's unapplied suspicions.

    A pair the pass flagged (a policy hold, or a borderline pair no
    model judged) and a pair whose cut the verifier held are the
    same shape to the creator: the editor thinks this is a retake
    and could not settle it. Pairs the model judged and settled
    (keep both, not a pair) are decisions already, not gold.
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
        if outcome == "flagged" and pair.get("band") == "flag":
            reason = str(pair.get("reason") or _GOLD_REASON_RETAKE_HELD)
        elif outcome == "flagged":
            reason = retakes.band_b_flag_reason(pair)
        else:
            reason = _GOLD_REASON_RETAKE_HELD
        keep = pair.get("keep") or {}
        candidates.append(
            {
                "gold_id": _gold_id(start, end, "retake"),
                "word_start": start,
                "word_end": end,
                "kind": "retake",
                "source": "retakes",
                "reason": reason,
                "text": str(cut.get("text") or ""),
                "keep_text": keep.get("text"),
                "keep_word_start": keep.get("word_start"),
                "keep_word_end": keep.get("word_end"),
                "pair_id": pair.get("pair_id"),
                "pair_key": retakes.pair_key(pair),
                "operation": None,
                "status": "open",
                "note": None,
                "created_at": _utc_now(),
                "decided_at": None,
            }
        )
    return candidates


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
                "reason": "You looked at this spot in gold and said to cut it.",
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
    entries = creator_rules.merge_grade_entries(
        _load_grades(config), [entry]
    )
    _save_grades(config, entries)
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
    record["status"] = "cut" if decision == "cut" else "left"
    record["note"] = note or None
    record["decided_at"] = _utc_now()
    _save_gold(config, records)
    checkpoints.commit(
        config,
        "Gold spot cut" if decision == "cut" else "Gold spot left in",
    )
    return {
        "ok": True,
        "gold_id": gold_id,
        "changed": changed,
        "message": message,
        "gold": _open_gold_spans(config),
        "suggestions": _rule_suggestions(config),
    }


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
        provider = None
        if provider_name == "meta":
            provider = _live_provider(config)
        elif provider_name == "mock":
            provider = MockProvider()
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
) -> dict:
    """Measure the edit for retakes and act on what the bands allow.

    Band A pairs are cut through the verifier like any other change.
    Band B pairs are judged in one provider call when the provider
    can judge; a keep-both or not-a-pair verdict is recorded and no
    operation is made, and any pair the model did not answer, plus
    every Band B pair under a provider that cannot judge, becomes a
    scored flag. Policy-flag pairs are flags by construction. Pairs
    the creator already graded as keep-both are never proposed again.

    The keeper mode decides which complete delivery a cluster
    keeps: the last take (the default), the first clean take, or
    Best delivery (Beta), which in this version runs in shadow
    only: the positional last-take rule still applies, and Beta
    records the keeper it would have chosen on every pair where
    there is a genuine choice.
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
        "usage": [],
        "route_events": [],
    }
    transcript = _load_json(str(config.job_dir / "transcript.json"))
    profile = _load_optional_file(config.job_dir / "profile.json")
    base_cuts = _effective_cuts(config)
    timeline = Timeline(transcript, base_cuts)
    record = _load_retake_record(config)
    decisions = record.get("decisions") or {}
    settings = retakes.settings_from_profile(profile)
    if keep_mode in retakes.KEEP_MODES:
        settings["keep"] = keep_mode
    mode = settings["keep"]
    summary["keep_mode"] = mode
    all_pairs = retakes.find_pairs(timeline, profile, settings)
    pairs = [
        pair
        for pair in all_pairs
        if decisions.get(retakes.pair_key(pair), {}).get("keep_both") is not True
    ]
    shadow = None
    if mode == "best":
        shadow = _run_shadow(
            config, provider_name, all_pairs, pairs, timeline,
            spoken_direction, record,
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
        return summary

    operations = retakes.band_a_operations(
        timeline, profile, settings=settings, pairs=pairs
    )
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
            provider = _live_provider(config)
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
            drain = getattr(provider, "drain_usage", None)
            if callable(drain):
                summary["usage"].extend(drain() or [])
            drain_events = getattr(provider, "drain_route_events", None)
            if callable(drain_events):
                summary["route_events"].extend(drain_events() or [])
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
                    "keep_mode": mode,
                    "shadow": pair.get("shadow"),
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
    # wait in gold for the creator's call.
    _record_gold_spans(
        config,
        _gold_candidates_from_retakes(record["pairs"], timeline.words),
    )
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
    breaths = str(
        body.get("breaths", defaults["breaths"]) or "keep"
    ).strip().lower()
    if breaths not in BREATH_MODES:
        raise ValueError(
            "Choose keeping the natural breath, or trimming "
            "breaths with the pause."
        )
    mode = str(
        body.get("mode", defaults.get("mode", "cleanest")) or "cleanest"
    ).strip().lower()
    if mode not in INVOLVEMENT_MODES:
        raise ValueError(
            "Choose the Cleanest cut, or Tailor it with me."
        )
    settings = {
        "remove_retakes": bool(
            body.get("remove_retakes", defaults["remove_retakes"])
        ),
        "cut_silence": bool(body.get("cut_silence", defaults["cut_silence"])),
        "buffer_s": min(BUFFER_MAX_S, max(BUFFER_MIN_S, buffer_s)),
        "keep_mode": keep_mode,
        "breaths": breaths,
        "remove_fillers": bool(
            body.get("remove_fillers", defaults.get("remove_fillers", True))
        ),
        "mode": mode,
    }
    scope = str(body.get("format_scope") or "both").strip().lower()
    if scope not in ("both", "vertical", "landscape"):
        raise ValueError("Choose both formats, vertical only, or landscape only.")
    direction_text = str(body.get("direction_text") or "").strip()
    # Spoken direction in the box can set the breaths choice too,
    # through the same deterministic mapping intake uses; what it
    # sets wins for this video and is named in the result.
    direction_recap: list = []
    if direction_text:
        spoken = direction_parse.parse_direction(direction_text)
        if "breaths" in spoken["overrides"]:
            settings["breaths"] = spoken["overrides"]["breaths"]
            direction_recap = [
                line
                for line in spoken["recap"]
                if line.startswith("Breaths will be")
            ]
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
            config,
            provider_name,
            spoken_direction=direction_text,
            keep_mode=settings["keep_mode"],
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
    silence = None
    if settings["cut_silence"]:
        silence = _run_silence_pass(
            config, settings["buffer_s"], settings["breaths"]
        )
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
    return {
        "ok": True,
        "provider": provider_name,
        "settings": settings,
        "recap": _keep_mode_recap(settings),
        "breaths_recap": _breaths_recap(settings),
        "direction_recap": direction_recap,
        "beta": _beta_score(config),
        "format_scope": scope,
        "direction_runs": direction_runs,
        "retakes": retake_summary,
        "filler": filler,
        "lines": list(filler["lines"]) if filler else [],
        "mode_lines": _mode_and_gold_lines(config, settings),
        "cost_lines": cost_block["recap_lines"] if cost_block else [],
        "costs": cost_block,
        "gold": len(_open_gold_spans(config)),
        "silence": silence,
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
    for key in ("remove_retakes", "cut_silence", "remove_fillers"):
        if isinstance(raw.get(key), bool):
            validated[key] = raw[key]
    buffer = raw.get("buffer_s")
    if isinstance(buffer, (int, float)) and not isinstance(buffer, bool):
        validated["buffer_s"] = min(
            BUFFER_MAX_S, max(BUFFER_MIN_S, float(buffer))
        )
    if raw.get("keep_mode") in retakes.KEEP_MODES:
        validated["keep_mode"] = raw["keep_mode"]
    if raw.get("breaths") in BREATH_MODES:
        validated["breaths"] = raw["breaths"]
    if raw.get("mode") in INVOLVEMENT_MODES:
        validated["mode"] = raw["mode"]
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
    shortened toward the buffer by default; keeping breaths natural
    is the one named exception), then the direction pass reads
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

    def stage(text: str, index: int, total: int) -> None:
        # The page shows the stage, its number, and how many there
        # are: progress a person can see, not a gray button.
        state["stage"] = text
        state["stage_index"] = index
        state["stage_total"] = total

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
        stage("Listening to your video…", 1, total)
        media = speech.probe_media(video_path)
        work = job_root / FIRST_EDIT_WORK_NAME
        wav_path = work / "audio.wav"
        duration = speech.extract_audio(video_path, wav_path)
        if media.get("duration") is None:
            media["duration"] = duration
        route = _intake_word_route(httpd.config)  # type: ignore[attr-defined]
        if route is None:
            raise speech.TranscribeError(NO_ROUTE_MESSAGE)
        minutes = duration / 60.0
        long_note = ""
        if minutes >= 5:
            long_note = (
                f" This video is about {minutes:.0f} minutes long, "
                "so this step takes a while. Leave this page open."
            )
        if route == "scribe":
            stage(
                "Writing the words down with ElevenLabs Scribe…"
                + long_note,
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
                "(about 150 MB), so this can take a few minutes."
                + long_note,
                2,
                total,
            )
            result = speech.local_transcribe_wav(
                wav_path, duration_s=duration
            )
        words = result["words"]
        if not words:
            state.update(
                {"status": "no_words", "message": NO_WORDS_MESSAGE}
            )
            shutil.rmtree(work, ignore_errors=True)
            return
        stage("Measuring the silences from the sound itself…", 3, total)
        spans = speech.measure_speech_spans(wav_path)
        stage("Laying out the first plan…", 4, total)
        intake_build.build_job_files(
            job_root,
            video_path.name,
            result,
            spans,
            media,
            captions_on=captions_on,
        )
        config = _adopt_job(httpd, job_root)
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
        silence_summary = None
        if settings["cut_silence"]:
            stage("Cutting the silence…", next_index, total)
            next_index += 1
            silence_summary = _run_silence_pass(
                config, settings["buffer_s"], settings["breaths"]
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
        checkpoints.commit(config, "First edit")
        transcript = _load_json(str(job_root / "transcript.json"))
        timeline = Timeline(transcript, _effective_cuts(config))
        kept_after = len(timeline.kept_word_indices())
        stage("Building your review video…", total, total)
        render_note = None
        try:
            intake_build.render_splice(
                video_path,
                _effective_cuts(config),
                session / SESSION_VIDEO_NAME,
                has_audio=bool(media.get("has_audio", True)),
            )
        except speech.TranscribeError as exc:
            render_note = str(exc)
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
        if retake_summary is not None:
            recap.append(
                "Retakes: "
                f"{retake_summary.get('applied', 0)} removed, "
                f"{retake_summary.get('flagged', 0)} set aside for "
                "your call."
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
        if "breaths" not in parsed["overrides"]:
            # When the direction set the breaths choice, the parsed
            # recap above already names it; otherwise the recap
            # still names the state the first edit ran with.
            recap.append(_breaths_recap(settings))
        if direction_summary is not None:
            # The direction lines come last: they end with the flag
            # recaps, the things left for the creator to see.
            recap.extend(direction_summary.get("lines", []))
        elif parsed["remainder"]:
            recap.append(
                "The rest of your direction was saved as overall "
                "guidance for the model."
            )
        # What the first edit cost, per feature, from the passes'
        # own records (v0.7.2): one line per feature that ran and
        # a total, just before the recap's closing lines.
        cost_entries = _first_edit_cost_entries(
            route, duration, settings, retake_summary, direction_summary
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
        }
        state.update(
            {
                "status": "done",
                "stage": "The first edit is ready.",
                "job_name": job_root.name,
                "engine": result["engine"],
                "words": kept_before,
                "kept_before": kept_before,
                "kept_after": kept_after,
                "settings": settings,
                "recap": recap,
                "lines": recap,
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
    except speech.TranscribeError as exc:
        state.update({"status": "failed", "message": str(exc)})
    except Exception as exc:  # the page still gets one plain sentence
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
                        "That spot is in gold, waiting for your "
                        "call on its own card."
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
            _write_json(session / "revised_cuts.json", current_cuts)
            checkpoints.commit(
                config,
                "Transcript phrase cut"
                if cutting
                else "Transcript phrase brought back",
            )
    stayed.sort(key=lambda entry: entry["word_start"])
    return {
        "ok": True,
        "changed": moved > 0,
        "moved": moved,
        "stayed": stayed,
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
    if action == "swap":
        # A swap is the creator naming their real keeper: the other
        # take. Settle any shadow flip on this pair against it.
        meta = span.get("retake") or {}
        pair = _retake_pair_for(config, meta.get("pair_id"))
        if pair is not None:
            record = _load_retake_record(config)
            if _resolve_shadow_match(record, pair, "other", "swap"):
                _save_retake_record(config, record)
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
    "Your video is in, and you can watch it below. This app can "
    "also make the first edit itself: it writes down the words, "
    "lays out a first plan, and runs the same choices the front "
    "door offers. Give any direction you have, then choose Make "
    "the first edit."
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


# POST routes that work on the loaded job. With no job loaded they
# are refused with NO_JOB_MESSAGE before any handler runs. Intake,
# the sample load, and the sign-in routes stay open at the front
# door: they are how a job (or a sign-in) arrives in the first place.
_JOB_REQUIRED_POSTS = frozenset({
    "/api/comments",
    "/api/direct",
    "/api/front-door",
    "/api/edit-note",
    "/api/grades",
    "/api/rule-save",
    "/api/rule-dismiss",
    "/api/rule-remove",
    "/api/word-toggle",
    "/api/word-range",
    "/api/edit-action",
    "/api/gold-verdict",
    "/api/word-correct",
    "/api/word-correct-remove",
    "/api/captions-pref",
    "/api/render",
    "/api/history/undo",
    "/api/history/redo",
})


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
            "<p><a href=\"/\" style=\"color: #0a84ff;\">Back to the app</a></p>"
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
            if path == "/":
                self._page_index()
            elif path == "/api/job":
                self._send_json(200, _job_summary(self.config))
            elif path == "/api/signin":
                self._send_json(200, _signin_state(self.config))
            elif path == "/api/intake/status":
                self._get_intake_status()
            elif path == "/api/captions":
                self._serve_captions()
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
        elif path == "/api/intake/first-edit":
            self._post_intake_first_edit()
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
        elif path == "/api/spark-route":
            self._post_spark_route()
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
        elif path == "/api/word-range":
            self._post_word_range()
        elif path == "/api/edit-action":
            self._post_edit_action()
        elif path == "/api/gold-verdict":
            self._post_gold_verdict()
        elif path == "/api/word-correct":
            self._post_word_correct()
        elif path == "/api/word-correct-remove":
            self._post_word_correct_remove()
        elif path == "/api/captions-pref":
            self._post_captions_pref()
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

    def _send_playback_pending(self, state: str) -> None:
        """The answer while a watchable copy is not ready: one
        plain page, never the raw file in the copy's place."""
        if state == "failed":
            self._send_plain_page(
                503,
                "This video cannot be shown here yet",
                PLAYBACK_FAILED_MESSAGE,
            )
        else:
            self._send_plain_page(
                503, PLAYBACK_GETTING_READY, PLAYBACK_BUILDING_MESSAGE
            )

    def _serve_video(self, name: str) -> None:
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
                _playback_state(self.config, name)
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
                    _playback_state(self.config, name)
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

    def _first_edits(self) -> dict:
        """First-edit runs in flight or finished, by intake id."""
        registry = getattr(self.server, "first_edit_jobs", None)
        if registry is None:
            registry = {}
            self.server.first_edit_jobs = registry  # type: ignore[attr-defined]
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
                _playback_state_for(target, _staged_copy_path(target))
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
            self._become_job(job_root)
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
            record["video_rel"] = relative
            # The watchable copy starts now, in the background,
            # the moment the drop is complete: the preview and
            # the later job both draw on it, and no play request
            # ever waits on the conversion itself.
            _ensure_playback_copy_for(
                video_path, _staged_copy_path(video_path)
            )
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
                    "playback": _playback_state_for(
                        video_path, _staged_copy_path(video_path)
                    ),
                    "can_first_edit": True,
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
        states[intake_id] = {"status": "working", "stage": "Starting…"}
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
            payload["playback"] = _playback_state_for(
                staged, _staged_copy_path(staged)
            )
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
        self._send_json(
            200,
            {
                "ok": True,
                "accepted": accepted,
                "message": message,
                "hosted": hosted,
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
            self._error(404, JOB_INCOMPLETE_MESSAGE)
            return
        try:
            _regenerate_after_review(self.config)
            after_review = True
        except Exception:
            after_review = False
        checkpoints.commit(self.config, "Direction run")
        _account_costs(
            self.config, _direction_cost_entries(audit.get("usage"))
        )
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
        config = self.config
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
        self._send_json(
            200,
            {
                "ok": True,
                "captions_hide_fillers": on,
                "captions_available": available,
            },
        )

    def _post_render_intake(self) -> None:
        """Render an intake job with the app's own splice.

        Runner jobs render through the runner's script, untouched.
        A job born here from a raw video has no runner pieces, so
        its kept ranges are spliced from the dropped video itself.
        Clean captions are rebuilt from the same plan when the job
        was born with them on.
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
        try:
            intake_build.render_splice(
                source,
                _effective_cuts(self.config),
                session / SESSION_VIDEO_NAME,
                has_audio=has_audio,
            )
        except speech.TranscribeError as exc:
            self._send_json(200, {"ok": False, "error": str(exc)})
            return
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
        self._send_json(200, {"ok": True})

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
        if self.config.is_intake_job:
            self._post_render_intake()
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
