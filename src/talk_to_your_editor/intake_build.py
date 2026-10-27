"""The fresh intake: a dropped raw video becomes a working job.

Everything here is new code written for this app. A lone video is
turned into the same job shape the rest of the app already runs on:
a word-timed transcript, a baseline edit plan that keeps every word,
and a marker file (``intake.json``) that says the job was born here,
so the render step can use this module's own simple splice while
runner jobs keep their existing render path untouched.

The baseline plan's geometry comes from the audio itself. Words are
grouped into the phrases the speaker actually said (split where the
silence between words runs long), and each kept piece is drawn to
the speech span the waveform measurement found for it, so the
silence at every join is the real measured quiet, not a guess from
any transcript's habits.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .transcribe import TranscribeError

INTAKE_MARKER = "intake.json"
BREAK_GAP_S = 0.5  # silence between words that ends one kept piece
FALLBACK_HEAD_S = 0.06
FALLBACK_TAIL_S = 0.04


def is_intake_job(job_dir) -> bool:
    if not job_dir:
        return False
    return (Path(job_dir) / INTAKE_MARKER).is_file()


def intake_source_video(job_dir) -> str | None:
    """The video file an intake job was built from, if recorded."""
    try:
        payload = json.loads(
            (Path(job_dir) / INTAKE_MARKER).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    name = payload.get("source_video")
    return str(name) if name else None


def _units(words: list) -> list:
    """Word groups split where the quiet between words runs long."""
    units = []
    current: list = []
    for index, word in enumerate(words):
        if current:
            gap = float(word["start"]) - float(words[index - 1]["end"])
            if gap > BREAK_GAP_S:
                units.append(current)
                current = []
        current.append(word)
    if current:
        units.append(current)
    return units


def _span_for(unit: list, spans: list) -> tuple:
    """The waveform speech span covering one unit of words.

    The span is widened to include the words themselves, so a word
    the energy measurement heard softly is never clipped by its own
    plan. With no span nearby, a small fixed margin stands in.
    """
    first = float(unit[0]["start"])
    last = float(unit[-1]["end"])
    covering = [
        span
        for span in spans
        if float(span["end"]) >= first - 0.25
        and float(span["start"]) <= last + 0.25
    ]
    if not covering:
        return first - FALLBACK_HEAD_S, last + FALLBACK_TAIL_S
    start = min(float(span["start"]) for span in covering)
    end = max(float(span["end"]) for span in covering)
    return min(start, first), max(end, last)


def build_baseline_cuts(words: list, spans: list, duration: float,
                        fps) -> dict:
    """A first plan that keeps every word, in the app's plan shape.

    Joins carry the measured silence the rest of the app expects:
    the quiet tail at the end of one kept piece plus the lead-in
    before the next piece's first word, the same two pieces the
    silence tools measure everywhere else.
    """
    units = _units(words)
    ranges = []
    for unit in units:
        start, end = _span_for(unit, spans)
        start = max(0.0, start)
        end = min(float(duration), end)
        if ranges and start < ranges[-1]["end"]:
            start = ranges[-1]["end"]
        if end <= start:
            end = round(start + 0.01, 4)
        ranges.append(
            {"start": round(start, 4), "end": round(end, 4)}
        )
    joins = []
    for j in range(len(ranges) - 1):
        prev_words = units[j]
        next_words = units[j + 1]
        tail = ranges[j]["end"] - float(prev_words[-1]["end"])
        head = float(next_words[0]["start"]) - ranges[j + 1]["start"]
        joins.append(round(max(0.0, tail) + max(0.0, head), 4))
    output = sum(r["end"] - r["start"] for r in ranges)
    return {
        "gap_seconds_setting": BREAK_GAP_S,
        "source_duration_s": round(float(duration), 4),
        "output_duration_s": round(output, 4),
        "words_total": len(words),
        "words_cut": 0,
        "frame_quantization": {
            "fps": fps,
            "ranges": len(ranges),
            "repairs": 0,
        },
        "join_pauses_s": joins,
        "kept_ranges": ranges,
        "curation": {"applied": 0, "holds": []},
        "signature": {"breath": None, "beat": None},
        "post_edits": [],
    }


def build_job_files(
    job_dir: Path,
    video_name: str,
    transcript_result: dict,
    spans: list,
    media: dict,
    *,
    captions_on: bool,
) -> dict:
    """Write transcript.json, cuts.json and the intake marker.

    The dropped video already sits in this folder and is never
    touched; the three files land beside it. Returns the cuts plan.
    """
    duration = float(
        media.get("duration") or transcript_result["duration"]
    )
    transcript = {
        "engine": transcript_result["engine"],
        "source_file": video_name,
        "language": transcript_result.get("language") or "en",
        "duration": duration,
        "words": transcript_result["words"],
        "segments": transcript_result.get("segments") or [],
    }
    cuts = build_baseline_cuts(
        transcript_result["words"], spans, duration, media.get("fps")
    )
    marker = {
        "kind": "intake",
        "source_video": video_name,
        "engine": transcript_result["engine"],
        "captions": bool(captions_on),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "transcript.json").write_text(
        json.dumps(transcript, indent=2), encoding="utf-8"
    )
    (job_dir / "cuts.json").write_text(
        json.dumps(cuts, indent=2), encoding="utf-8"
    )
    (job_dir / INTAKE_MARKER).write_text(
        json.dumps(marker, indent=2), encoding="utf-8"
    )
    return cuts


# ---------------------------------------------------------------------------
# The intake render: a plain splice of the kept pieces
# ---------------------------------------------------------------------------


def render_splice(
    source_video: Path,
    cuts: dict,
    out_path: Path,
    *,
    has_audio: bool = True,
) -> Path:
    """Render the kept ranges of an intake job into one review video.

    A single ffmpeg pass: each kept piece is trimmed out and the
    pieces are joined in order, re-encoded to H.264 with AAC sound
    so every browser can play the result. Written to a scratch file
    first and moved into place, so a stopped render never leaves a
    half-built video wearing the finished name.
    """
    ranges = [
        r
        for r in cuts.get("kept_ranges") or []
        if float(r["end"]) > float(r["start"])
    ]
    if not ranges:
        raise TranscribeError(
            "There is nothing left to build a video from: every "
            "word is cut in the current plan."
        )
    filters = []
    labels = []
    for i, item in enumerate(ranges):
        start = float(item["start"])
        end = float(item["end"])
        filters.append(
            f"[0:v]trim=start={start:.4f}:end={end:.4f},"
            f"setpts=PTS-STARTPTS[v{i}]"
        )
        if has_audio:
            filters.append(
                f"[0:a]atrim=start={start:.4f}:end={end:.4f},"
                f"asetpts=PTS-STARTPTS[a{i}]"
            )
        labels.append(f"[v{i}]" + (f"[a{i}]" if has_audio else ""))
    graph = ";".join(filters) + ";" + "".join(labels)
    graph += (
        f"concat=n={len(ranges)}:v=1:a={1 if has_audio else 0}"
    )
    graph += "[outv]" + ("[outa]" if has_audio else "")
    scratch = out_path.with_name(out_path.stem + ".building.mp4")
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(source_video),
        "-filter_complex", graph,
        "-map", "[outv]",
    ]
    if has_audio:
        command += ["-map", "[outa]", "-c:a", "aac", "-b:a", "160k"]
    command += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(scratch),
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=1800
        )
    except FileNotFoundError:
        raise TranscribeError(
            "ffmpeg was not found on this computer, so the fixed "
            "video could not be built."
        )
    except subprocess.TimeoutExpired:
        raise TranscribeError(
            "Building the fixed video took too long, so it was "
            "stopped. The revised plan is still saved."
        )
    if proc.returncode != 0 or not scratch.is_file():
        scratch.unlink(missing_ok=True)
        raise TranscribeError(
            "The fixed video could not be built. Nothing was "
            "changed, and the revised plan is still saved."
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    scratch.replace(out_path)
    return out_path
