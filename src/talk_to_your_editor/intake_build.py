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
import shutil
import subprocess
from datetime import datetime, timezone
from fractions import Fraction
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


CROSSFADE_S = 0.04


def splice_filter_graph(
    ranges: list,
    has_audio: bool = True,
    crossfade_s: float = 0.0,
    crop: tuple = None,
) -> str:
    """The ffmpeg filter graph joining the kept ranges, in order.

    Video always joins with plain cuts. Sound joins the same way
    unless ``crossfade_s`` is set (v0.15, Batch I, item 30): then
    each join is an acrossfade of that many seconds (the app's
    Smooth the joins choice, 40ms), shortened per join so a very
    short piece is never asked to fade for longer than it lasts,
    and a join with no room to fade falls back to a plain concat
    of the two streams. Only the sound is smoothed; the picture
    still cuts.

    v0.16: a piece can carry its own ``fade_in_s`` (the manual
    crossfade set at the join that plays into it). That length
    wins at its join, over the plan-wide ``crossfade_s``; a join
    whose piece carries none uses the plan-wide choice, exactly
    as before.

    v0.19: ``crop`` is an optional (x, y, w, h, out_w, out_h)
    pixel window: after the pieces join, the picture is cropped
    to that window and scaled to the output size. This is the
    framing choice (a 9:16 or 16:9 cut around the face). The
    default, None, builds exactly the graph it always has: the
    recorded framing is never cropped unless a shape is chosen.
    """
    vout = "[catv]" if crop else "[outv]"
    filters = []
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
    vlabels = "".join(f"[v{i}]" for i in range(len(ranges)))

    def finish(graph: str) -> str:
        if not crop:
            return graph
        x, y, w, h, out_w, out_h = crop
        return (
            graph
            + f";[catv]crop={w}:{h}:{x}:{y},"
            + f"scale={out_w}:{out_h}[outv]"
        )

    if not has_audio:
        return finish(
            ";".join(filters) + ";" + vlabels
            + f"concat=n={len(ranges)}:v=1:a=0{vout}"
        )
    join_fades = []
    for i in range(1, len(ranges)):
        own = ranges[i].get("fade_in_s")
        if isinstance(own, (int, float)) and not isinstance(own, bool):
            join_fades.append(float(own))
        else:
            join_fades.append(float(crossfade_s or 0.0))
    if len(ranges) >= 2 and any(f > 0 for f in join_fades):
        joins = []
        prev = "[a0]"
        for i in range(1, len(ranges)):
            out = "[outa]" if i == len(ranges) - 1 else f"[x{i}]"
            room = min(
                float(ranges[i - 1]["end"]) - float(ranges[i - 1]["start"]),
                float(ranges[i]["end"]) - float(ranges[i]["start"]),
            )
            fade = min(join_fades[i - 1], room * 0.45)
            if fade >= 0.005:
                joins.append(
                    f"{prev}[a{i}]acrossfade=d={fade:.4f}"
                    f":c1=tri:c2=tri{out}"
                )
            else:
                joins.append(f"{prev}[a{i}]concat=n=2:v=0:a=1{out}")
            prev = out
        return finish(
            ";".join(filters) + ";" + vlabels
            + f"concat=n={len(ranges)}:v=1:a=0{vout};"
            + ";".join(joins)
        )
    labels = "".join(
        f"[v{i}][a{i}]" for i in range(len(ranges))
    )
    return finish(
        ";".join(filters) + ";" + labels
        + f"concat=n={len(ranges)}:v=1:a=1{vout}[outa]"
    )


def _run_ffmpeg(command: list, timeout: int = 1800) -> None:
    """Run one ffmpeg command, raising the app's plain errors."""
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout
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
    if proc.returncode != 0:
        # The user-facing sentence stays plain, but the real
        # ffmpeg error rides on the exception so the session log
        # can record what actually failed (a render failure on
        # 2026-11-04 was undiagnosable because this stderr was
        # thrown away).
        err = TranscribeError(
            "The fixed video could not be built. Nothing was "
            "changed, and the revised plan is still saved."
        )
        err.ffmpeg_stderr = (proc.stderr or "")[-2000:]
        err.ffmpeg_command = " ".join(str(c) for c in command)
        raise err


def _probe_render_geometry(source_video: Path) -> tuple:
    """(fps as a Fraction or None, audio sample rate or None,
    whether the source carries an audio stream at all).

    Probed from the source itself, so the render's frame math
    follows the file in hand rather than a plan's paperwork.
    """
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "stream=codec_type,avg_frame_rate,sample_rate",
                "-of", "json", str(source_video),
            ],
            capture_output=True, text=True, timeout=120,
        )
        data = json.loads(proc.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, None, False
    fps = None
    sample_rate = None
    has_audio_stream = False
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video" and fps is None:
            raw = str(stream.get("avg_frame_rate") or "")
            if "/" in raw:
                num, _, den = raw.partition("/")
                try:
                    num_i, den_i = int(num), int(den)
                except ValueError:
                    num_i, den_i = 0, 0
                if num_i > 0 and den_i > 0:
                    fps = Fraction(num_i, den_i)
        elif stream.get("codec_type") == "audio":
            has_audio_stream = True
            if sample_rate is None:
                try:
                    sample_rate = int(stream.get("sample_rate") or 0)
                except (TypeError, ValueError):
                    sample_rate = None
                if not sample_rate:
                    sample_rate = None
    return fps, sample_rate, has_audio_stream


def _join_fades(ranges: list, spans: list, crossfade_s: float) -> list:
    """The fade at each join, in seconds (one per gap between
    pieces). A piece's own ``fade_in_s`` (the manual crossfade
    set at the join that plays into it) wins over the plan-wide
    ``crossfade_s``, as it did in the old graph; every fade is
    shortened so a short piece is never asked to fade for longer
    than it can hold, and a join with no room falls back to a
    plain butt join (fade 0)."""
    fades = []
    for i in range(1, len(ranges)):
        own = ranges[i].get("fade_in_s")
        if isinstance(own, (int, float)) and not isinstance(own, bool):
            want = float(own)
        else:
            want = float(crossfade_s or 0.0)
        room = min(spans[i - 1], spans[i])
        fade = min(want, room * 0.45)
        fades.append(fade if fade >= 0.005 else 0.0)
    return fades


def _continuous_audio_graph(
    ranges: list,
    spans: list,
    fades: list,
    target_samples: int,
) -> str:
    """One filter graph building the whole soundtrack in a
    single pass over the source audio (Fix 2, 0.26).

    Each piece is atrimmed to its timeline span plus, when the
    join after it fades, the fade's length past its end: that
    extension is exactly the stretch the acrossfade consumes,
    so piece i's contribution to the total is its span, neither
    more nor less, and the chain's length is the sum of the
    spans by construction. The blend at a join is the source
    audio just past the piece's end (the pause the edit trimmed,
    on its way out) under the next piece's own head (on its way
    in); no edge ever fades to silence. The chain is then padded
    or trimmed to the exact sample count the video pieces sum
    to, so the one AAC encode of the finished track carries its
    priming only at the two ends of the whole edit, never at a
    join.
    """
    filters = []
    for i, item in enumerate(ranges):
        start = float(item["start"])
        ext = fades[i] if i < len(fades) else 0.0
        end = start + spans[i] + ext
        filters.append(
            f"[0:a]atrim=start={start:.4f}:end={end:.4f},"
            f"asetpts=PTS-STARTPTS[pa{i}]"
        )
    if len(ranges) == 1:
        joined = "[pa0]"
    else:
        prev = "[pa0]"
        for i in range(1, len(ranges)):
            out = "[joined]" if i == len(ranges) - 1 else f"[px{i}]"
            fade = fades[i - 1]
            if fade > 0:
                filters.append(
                    f"{prev}[pa{i}]acrossfade=d={fade:.4f}"
                    f":c1=tri:c2=tri{out}"
                )
            else:
                filters.append(f"{prev}[pa{i}]concat=n=2:v=0:a=1{out}")
            prev = out
        joined = "[joined]"
    filters.append(
        f"{joined}apad,atrim=end_sample={target_samples},"
        f"asetpts=PTS-STARTPTS[outa]"
    )
    return ";".join(filters)


def render_splice(
    source_video: Path,
    cuts: dict,
    out_path: Path,
    *,
    has_audio: bool = True,
    crossfade_s: float = 0.0,
    crop: tuple = None,
) -> Path:
    """Render the kept ranges of an intake job into one review video.

    Piece by piece for the picture (0.24.0, Justin's Mac test of
    0.23.0): each kept piece is rendered on its own, so only one
    piece is ever in flight and memory stays flat no matter how
    many pieces the plan has, on a 10-minute 4K file as much as
    a short one. Each piece is rendered to an exact frame count
    (its span rounded to the nearest frame), so the pieces sum
    to a known video length before anything is joined.

    The sound is no longer rendered piece by piece (Fix 2,
    0.26, Justin's 0.25.0 Mac test). Encoding every piece's AAC
    separately and concatenating put an encoder's priming and
    padding at every join (his output's decoded audio ran 7.1 s
    longer than its picture), and the 30 ms fade to silence at
    every piece edge left 114 stretches of digital silence in
    the result. Now the picture still joins with the concat
    demuxer, but the soundtrack is built in one pass over the
    source audio (see ``_continuous_audio_graph``) and encoded
    once, then muxed with the picture: the two streams are the
    same length by construction, and no join fades to zero.

    Written to scratch files first and moved into place, so a
    stopped render never leaves a half-built video wearing the
    finished name. ``crossfade_s`` carries the stored
    Smooth-the-joins choice: the acrossfade length at the joins
    (40 ms when the choice is on, plain butt joins when off).
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
    fps, sample_rate, source_has_audio = _probe_render_geometry(
        source_video
    )
    if fps is None:
        plan_fps = (cuts.get("frame_quantization") or {}).get("fps")
        if isinstance(plan_fps, (int, float)) and plan_fps > 0:
            fps = Fraction(plan_fps).limit_denominator(100000)
    # Each piece's timeline span: its length rounded to whole
    # frames, the same span for the picture and the soundtrack,
    # so a word sits at the same output time in both.
    spans: list[float] = []
    for item in ranges:
        length = float(item["end"]) - float(item["start"])
        if fps is not None:
            frames = max(1, round(length * fps))
            spans.append(frames / fps)
        else:
            spans.append(length)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    scratch = out_path.with_name(out_path.stem + ".building.mp4")
    parts_dir = out_path.with_name(out_path.stem + ".building-parts")
    shutil.rmtree(parts_dir, ignore_errors=True)
    parts_dir.mkdir(parents=True)
    try:
        part_paths: list[Path] = []
        for i, item in enumerate(ranges):
            start = float(item["start"])
            part = parts_dir / f"piece-{i:04d}.mp4"
            command = [
                "ffmpeg", "-y", "-v", "error",
                "-ss", f"{start:.4f}",
                "-i", str(source_video),
            ]
            if fps is not None:
                frames = max(1, round(spans[i] * fps))
                command += ["-frames:v", str(frames)]
            else:
                command += ["-t", f"{spans[i]:.4f}"]
            if crop:
                x, y, w, h, out_w, out_h = crop
                command += [
                    "-vf", f"crop={w}:{h}:{x}:{y},scale={out_w}:{out_h}"
                ]
            command += [
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-an",
                str(part),
            ]
            _run_ffmpeg(command)
            part_paths.append(part)
        list_path = parts_dir / "concat.txt"
        list_path.write_text(
            "".join(
                "file '{}'\n".format(
                    str(p.resolve()).replace("'", "'\\''")
                )
                for p in part_paths
            ),
            encoding="utf-8",
        )
        video_scratch = parts_dir / "video-only.mp4"
        _run_ffmpeg([
            "ffmpeg", "-y", "-v", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(list_path),
            "-c", "copy",
            str(video_scratch),
        ])
        if has_audio and source_has_audio:
            rate = sample_rate or 48000
            target_samples = int(round(sum(spans) * rate))
            fades = _join_fades(ranges, spans, crossfade_s)
            graph = _continuous_audio_graph(
                ranges, spans, fades, target_samples
            )
            audio_scratch = parts_dir / "audio-only.m4a"
            _run_ffmpeg([
                "ffmpeg", "-y", "-v", "error",
                "-i", str(source_video),
                "-filter_complex", graph,
                "-map", "[outa]",
                "-c:a", "aac", "-b:a", "160k",
                str(audio_scratch),
            ])
            _run_ffmpeg([
                "ffmpeg", "-y", "-v", "error",
                "-i", str(video_scratch),
                "-i", str(audio_scratch),
                "-c", "copy", "-movflags", "+faststart",
                str(scratch),
            ])
        else:
            _run_ffmpeg([
                "ffmpeg", "-y", "-v", "error",
                "-i", str(video_scratch),
                "-c", "copy", "-movflags", "+faststart",
                str(scratch),
            ])
    except TranscribeError:
        scratch.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(parts_dir, ignore_errors=True)
    if not scratch.is_file():
        raise TranscribeError(
            "The fixed video could not be built. Nothing was "
            "changed, and the revised plan is still saved."
        )
    scratch.replace(out_path)
    return out_path
