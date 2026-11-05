"""Finish: versions, the export render, and the project file.

Message 3 (beat 7 and 7a). One main edit; the versions (9:16,
16:9, or both) are shapes of that same edit. This module holds
the per-session Finish state (which versions he chose, and his
per-shape framing nudges), the export render itself, and the
subtitle track's text.

The export render reuses the intake splice (per-piece picture,
one continuous soundtrack, fix 2) with the framing crop for the
shape, then finishes the file the way his settings say: loudness
normalized to -14 LUFS (measured first, then applied, the
standard two-pass loudnorm), AAC 128 kbps stereo at 48 kHz, and
a subtitle track inside the file carrying sentence lines in
normal case, built from the plan's kept words on their sound
bounds (the captions module's cue grouping). The picture is
never re-encoded in the finishing pass, and the soundtrack is
encoded exactly once there, so no join can gain a fade or a
drift the splice did not have.

The framing crop itself comes from framing.match_window: the
reference of the shape (learned on Train, kept in the creator
store) says where the face sits and how large; the face is found
in the new video; the crop follows. The render fills the shape's
full frame for the source (16:9 at the source's own frame, 9:16
at 2160 tall), the crop scaled up to fill it.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import captions as clean_captions
from . import framing
from . import intake_build
from .transcribe import TranscribeError

FINISH_STATE_FILENAME = "finish_state.json"
PROJECT_FILENAME = "project.tye.json"
ASPECTS = ("9:16", "16:9")
TARGET_LUFS = -14.0


def finish_filename(aspect: str) -> str:
    return "finish-" + aspect.replace(":", "x") + ".mp4"


# ---------------------------------------------------------------------------
# The Finish state: chosen versions and per-shape nudges
# ---------------------------------------------------------------------------


def load_state(session_dir: Path) -> dict:
    """The session's Finish state, with honest defaults.

    Default versions are both shapes (the demo's choice); he can
    narrow it on the Edit screen's direction card or on Finish.
    Offsets are the per-shape framing nudge in shares of the
    output frame, (0, 0) until he moves one.
    """
    state = {"versions": list(ASPECTS), "offsets": {}}
    path = Path(session_dir) / FINISH_STATE_FILENAME
    if path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = None
        if isinstance(saved, dict):
            versions = [
                v for v in saved.get("versions") or [] if v in ASPECTS
            ]
            if versions:
                state["versions"] = versions
            offsets = saved.get("offsets") or {}
            if isinstance(offsets, dict):
                for aspect in ASPECTS:
                    off = offsets.get(aspect) or {}
                    if isinstance(off, dict):
                        state["offsets"][aspect] = {
                            "x": float(off.get("x") or 0.0),
                            "y": float(off.get("y") or 0.0),
                        }
    for aspect in ASPECTS:
        state["offsets"].setdefault(aspect, {"x": 0.0, "y": 0.0})
    return state


def save_state(session_dir: Path, state: dict) -> None:
    Path(session_dir).mkdir(parents=True, exist_ok=True)
    (Path(session_dir) / FINISH_STATE_FILENAME).write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )


def set_versions(session_dir: Path, versions: list) -> dict:
    state = load_state(session_dir)
    chosen = [v for v in ASPECTS if v in (versions or [])]
    if chosen:
        state["versions"] = chosen
    save_state(session_dir, state)
    return state


def nudge_offset(
    session_dir: Path, aspect: str, dx: float, dy: float, reset=False
) -> dict:
    state = load_state(session_dir)
    if aspect in ASPECTS:
        if reset:
            state["offsets"][aspect] = {"x": 0.0, "y": 0.0}
        else:
            off = state["offsets"][aspect]
            # A nudge is small by design: at most a tenth of the
            # frame in either direction, so a stray tap cannot
            # push the face out of the picture.
            off["x"] = min(0.10, max(-0.10, off["x"] + float(dx)))
            off["y"] = min(0.10, max(-0.10, off["y"] + float(dy)))
    save_state(session_dir, state)
    return state


# ---------------------------------------------------------------------------
# Sizing and subtitles
# ---------------------------------------------------------------------------


def finish_output_size(
    aspect: str, source_width: int, source_height: int
) -> tuple:
    """The export's frame: the shape's full frame for the source.

    16:9 renders at the source's own frame (3840x2160 for his
    4K), 9:16 at full portrait 4K (2160x3840 for his source);
    the crop is scaled up to fill the frame in both cases,
    never exported at the crop's own size. The math lives in
    framing.full_frame_size, beside the crop math that has to
    agree with it about when a crop may look soft.
    """
    return framing.full_frame_size(aspect, source_width, source_height)


PROXY_HEIGHT = 1080


def proxy_output_size(out_w: int, out_h: int) -> tuple:
    """The frame the face-size correction loop iterates at.

    A 1080-high frame in the export's own shape. The crop
    math is in shares of the frame, so a correction measured
    at proxy size lands the same at full size, while a proxy
    render costs a fraction of a full-frame one: the loop
    settles the crop here, and the delivered file is rendered
    once, at full frame, with the settled crop (Justin's
    direction, set with the finish fixes). When the full
    frame is already 1080 high or smaller, the proxy is the
    full frame itself and no second render is needed.
    """
    out_w, out_h = int(out_w), int(out_h)
    if out_h <= PROXY_HEIGHT:
        return out_w, out_h

    def even(value: float) -> int:
        return max(2, int(round(value / 2)) * 2)

    return even(out_w * PROXY_HEIGHT / out_h), PROXY_HEIGHT


def _srt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}".replace(".", ",")


def srt_from_plan(job_dir: Path, cuts: dict) -> str:
    """The subtitle track's text: sentence lines, normal case.

    Built from the plan's kept words on the output timeline (the
    captions module's rows and cue grouping, which break at
    sentence ends), written as SRT for the muxer.
    """
    transcript = json.loads(
        (Path(job_dir) / "transcript.json").read_text(encoding="utf-8")
    )
    rows = clean_captions.kept_word_rows(transcript, cuts)
    cues = clean_captions.group_cues(rows)
    lines = []
    for index, cue in enumerate(cues, start=1):
        lines.append(str(index))
        lines.append(
            f"{_srt_time(cue['start'])} --> {_srt_time(cue['end'])}"
        )
        lines.append(cue["text"])
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The export render
# ---------------------------------------------------------------------------


def _loudnorm_measure(media: Path) -> dict | None:
    """One loudness measurement pass, or None when it fails."""
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-i", str(media),
            "-vn", "-af",
            f"loudnorm=I={TARGET_LUFS}:TP=-1.0:LRA=11:print_format=json",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=1800,
    )
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr or "")
    if not blocks:
        return None
    try:
        return json.loads(blocks[-1])
    except ValueError:
        return None


def _gain_correction_filter(miss: float) -> str:
    """The loudness correction as a filter: a plain gain of
    exactly the miss, nothing else riding along.

    An earlier version paired the gain with a limiter; the
    limiter's automatic level added gain of its own on top, and
    the correction overshot (0.84 dB asked, 1.40 dB measured,
    landing at -13.44 against the -14 target). A plain volume
    gain moves the integrated loudness by exactly the miss.
    """
    return f"volume={float(miss):.2f}dB"


def normalize_loudness_in_place(path: Path) -> dict:
    """Bring one already-rendered file to the export loudness, in place.

    The Done message's export is the session splice itself, and
    it used to ship at whatever loudness the splice rendered
    (0.27.1, P2 item 10: -17.9 LUFS on his Mac against the -14
    every Finish export hits). Same two steps as the Finish
    render: loudnorm fed the file's own measured values, then,
    when the result lands more than 0.3 LUFS off target, one
    plain-gain correction of exactly the miss. Returns the
    before/after measurements; on any failure the file is left
    exactly as it was and the numbers say so (None).
    """
    result: dict = {"before_lufs": None, "after_lufs": None}
    if not path.is_file():
        return result
    measured = _loudnorm_measure(path)
    if not measured:
        return result
    result["before_lufs"] = float(measured["input_i"])
    if abs(result["before_lufs"] - TARGET_LUFS) <= 0.3:
        result["after_lufs"] = result["before_lufs"]
        return result
    norm = (
        f"loudnorm=I={TARGET_LUFS}:TP=-1.0:LRA=11"
        f":measured_I={measured['input_i']}"
        f":measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}"
        f":measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true"
    )
    try:
        from . import transcribe as _speech

        video_dur = _speech.probe_media(path).get("duration")
    except Exception:
        video_dur = None
    if video_dur:
        norm += (
            f",atrim=0:{float(video_dur):.3f}"
            ",asetpts=N/SR/TB"
        )
    tmp_path = path.with_name(path.stem + ".norm.mp4")
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-i", str(path),
                "-map", "0", "-c:v", "copy", "-c:s", "copy",
                "-af", norm, "-c:a", "aac", "-b:a", "128k",
                "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart", str(tmp_path),
            ],
            capture_output=True, text=True, timeout=1800,
        )
        if proc.returncode != 0 or not tmp_path.is_file():
            return result
        tmp_path.replace(path)
        after = _loudnorm_measure(path)
        if after:
            result["after_lufs"] = float(after["input_i"])
        if (
            after
            and abs(float(after["input_i"]) - TARGET_LUFS) > 0.3
        ):
            miss = TARGET_LUFS - float(after["input_i"])
            fix_path = path.with_name(path.stem + ".fix.mp4")
            fix = subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error", "-i", str(path),
                    "-map", "0", "-c:v", "copy", "-c:s", "copy",
                    "-af", _gain_correction_filter(miss),
                    "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                    "-ac", "2", "-movflags", "+faststart",
                    str(fix_path),
                ],
                capture_output=True, text=True, timeout=1800,
            )
            if fix.returncode == 0 and fix_path.is_file():
                fix_path.replace(path)
                after2 = _loudnorm_measure(path)
                if after2:
                    result["after_lufs"] = float(after2["input_i"])
            else:
                fix_path.unlink(missing_ok=True)
        return result
    finally:
        tmp_path.unlink(missing_ok=True)


def render_finish_video(
    source: Path,
    job_dir: Path,
    cuts: dict,
    out_path: Path,
    crop: tuple,
    *,
    crossfade_s: float = 0.0,
    has_audio: bool = True,
) -> dict:
    """Render one shape's finished video, to his export settings.

    ``crop`` is (x, y, w, h, out_w, out_h) in source pixels, from
    the framing match. Returns the loudness story: the splice's
    measured integrated loudness and the finished file's, both
    measured, neither invented.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    splice_path = out_path.with_name(out_path.stem + ".splice.mp4")
    srt_path = out_path.with_name(out_path.stem + ".srt")
    try:
        intake_build.render_splice(
            source, cuts, splice_path,
            has_audio=has_audio, crossfade_s=crossfade_s, crop=crop,
        )
        srt_text = ""
        try:
            srt_text = srt_from_plan(job_dir, cuts)
        except (OSError, ValueError, KeyError):
            srt_text = ""
        measured = _loudnorm_measure(splice_path) if has_audio else None
        if measured:
            norm = (
                f"loudnorm=I={TARGET_LUFS}:TP=-1.0:LRA=11"
                f":measured_I={measured['input_i']}"
                f":measured_TP={measured['input_tp']}"
                f":measured_LRA={measured['input_lra']}"
                f":measured_thresh={measured['input_thresh']}"
                f":offset={measured['target_offset']}:linear=true"
            )
        else:
            norm = f"loudnorm=I={TARGET_LUFS}:TP=-1.0:LRA=11"
        # loudnorm's resampling leaves the soundtrack a few
        # dozen milliseconds longer than the picture (its tail
        # flush): trim the audio back to the splice's own length
        # in the same graph, so the finished file's streams agree
        # to the frame the way the splice's do. The start is not
        # touched: the measured onset of the finished file sits
        # within a few milliseconds of the splice's.
        if has_audio:
            try:
                from . import transcribe as _speech
                video_dur = _speech.probe_media(splice_path).get(
                    "duration"
                )
            except Exception:
                video_dur = None
            if video_dur:
                norm += (
                    f",atrim=0:{float(video_dur):.3f}"
                    ",asetpts=N/SR/TB"
                )
        command = ["ffmpeg", "-y", "-v", "error", "-i", str(splice_path)]
        if srt_text:
            srt_path.write_text(srt_text, encoding="utf-8")
            command += ["-i", str(srt_path)]
        command += ["-map", "0:v:0"]
        if has_audio:
            command += ["-map", "0:a:0"]
        if srt_text:
            command += ["-map", "1:0"]
        command += ["-c:v", "copy"]
        if has_audio:
            command += [
                "-af", norm, "-c:a", "aac", "-b:a", "128k",
                "-ar", "48000", "-ac", "2",
            ]
        if srt_text:
            command += [
                "-c:s", "mov_text",
                "-metadata:s:s:0", "language=eng",
            ]
        command += ["-movflags", "+faststart", str(out_path)]
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=1800
        )
        if proc.returncode != 0 or not out_path.is_file():
            raise TranscribeError(
                "The finished video could not be exported. "
                "Nothing was changed, and the edit is still saved."
            )
        after = _loudnorm_measure(out_path) if has_audio else None
        # Measure-and-correct, once: when the measured result
        # lands more than 0.3 LUFS from the target, apply a
        # plain gain of exactly the miss and measure again. The
        # reported number is always the final measurement.
        if (
            after
            and abs(float(after["input_i"]) - TARGET_LUFS) > 0.3
        ):
            miss = TARGET_LUFS - float(after["input_i"])
            fix_path = out_path.with_name(out_path.stem + ".fix.mp4")
            fix_filter = _gain_correction_filter(miss)
            fix = subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error", "-i", str(out_path),
                    "-map", "0", "-c:v", "copy", "-c:s", "copy",
                    "-af", fix_filter,
                    "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                    "-ac", "2", "-movflags", "+faststart",
                    str(fix_path),
                ],
                capture_output=True, text=True, timeout=1800,
            )
            if fix.returncode == 0 and fix_path.is_file():
                fix_path.replace(out_path)
                after = _loudnorm_measure(out_path) or after
            else:
                fix_path.unlink(missing_ok=True)
        return {
            "path": str(out_path),
            "loudness_before_lufs": (
                float(measured["input_i"]) if measured else None
            ),
            "loudness_after_lufs": (
                float(after["input_i"]) if after else None
            ),
            "subtitles": bool(srt_text),
        }
    finally:
        splice_path.unlink(missing_ok=True)
        srt_path.unlink(missing_ok=True)
