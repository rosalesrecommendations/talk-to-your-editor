"""Timeline export: hand the finished edit to another editor.

v0.19. The app renders the finished video itself, but a creator who
finishes in Premiere, DaVinci Resolve, or Final Cut Pro needs the
edit as a timeline they can keep changing there. This module turns
the job's cuts plan into three text formats:

- FCPXML 1.10 (.fcpxml): Final Cut Pro; Resolve imports it too.
- Premiere Pro XML (.xml, xmeml version 5): Premiere; Resolve too.
- CMX 3600 EDL (.edl): the universal fallback most editors read.

Pure functions only: no file writes, no HTTP, no model calls. The
input is the cuts dict the rest of the app uses: ``kept_ranges`` is
the ordered list of kept source ranges (list order is output
order), each range carrying ``start``/``end`` in source seconds and
optionally ``fade_in_s``, the audio crossfade at the join that
plays into that piece (set by manual_edits.set_join_fade, at most
half a second). Special ranges (signature breath inserts, donor
audio; see timeline.is_special_range) carry ``insert_kind`` and/or
separate ``audio_start``/``audio_end`` donor times.

Timebase honesty: every time in a file is an integer frame count
derived from the plan's own seconds. A range's source in/out are
its start/end rounded to the nearest frame; the record (output)
timeline is the cumulative sum of the clip durations those rounded
values declare, so a file can never drift against itself. The
frame rate is the caller's ``fps`` when given, else the plan's own
``frame_quantization`` fps, else 30 with a note saying so.

What a format cannot say, the result says instead, in the notes
list, and never silently:

- A special insert's picture window is exported like any other
  piece (so every kept range appears exactly once and the record
  timeline stays gapless), but its sound in the app comes from the
  donor times, which a single clip in another editor cannot point
  at separately; a note names the piece and the donor times.
- Audio crossfades ride in FCPXML and xmeml as audio transitions
  centered on the join (the picture still cuts, exactly as the
  app's own render does). An EDL has no way to carry them, so the
  EDL result notes each fade it leaves behind.
- A range with no usable length is left out, with a note; the
  rest still exports.
- Picture size is not recorded in the plan, so the files do not
  invent one; a note says the other editor sets it from the clip.
- The media is referenced by the source's file name only. The
  other editor may ask to relink it; a note says that too.
"""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import PurePath
from typing import Optional
from urllib.parse import quote
from xml.sax.saxutils import escape

from .timeline import is_special_range

DEFAULT_FPS = 30.0


# -- plan reading -------------------------------------------------------------


def _resolve_fps(cuts: dict, fps: Optional[float], notes: list) -> float:
    if fps:
        return float(fps)
    fq = cuts.get("frame_quantization") or {}
    if fq.get("fps"):
        return float(fq["fps"])
    notes.append(
        "The plan does not record a frame rate, so the timeline "
        "files assume 30 frames per second."
    )
    return DEFAULT_FPS


def _frame(seconds: float, fps: float) -> int:
    return int(round(float(seconds) * fps))


def _pieces(cuts: dict, fps: float, notes: list) -> list:
    """The exportable pieces, in output order, in integer frames.

    Returns one dict per kept range that can be represented, with
    source/record frame spans and its fade (in frames) at the join
    that plays into it. Unrepresentable ranges are skipped with a
    note, per the module docstring.
    """
    pieces = []
    record = 0
    ranges = cuts.get("kept_ranges") or []
    for index, rng in enumerate(ranges):
        label = f"Piece {index + 1}"
        try:
            start = float(rng["start"])
            end = float(rng["end"])
        except (KeyError, TypeError, ValueError):
            notes.append(f"{label} has no usable times and was left out.")
            continue
        if end - start <= 0:
            notes.append(f"{label} has no length and was left out.")
            continue
        start_f = _frame(start, fps)
        end_f = _frame(end, fps)
        clamped = False
        if end_f <= start_f:
            end_f = start_f + 1
            clamped = True
        piece = {
            "index": index,
            "label": label,
            "src_in": start_f,
            "src_out": end_f,
            "duration": end_f - start_f,
            "rec_in": record,
            "rec_out": record + (end_f - start_f),
            "special": is_special_range(rng),
            "range": rng,
            "fade_frames": 0,
        }
        record = piece["rec_out"]
        if clamped:
            notes.append(
                f"{label} is shorter than one frame; it is exported "
                "as a single frame."
            )
        if piece["special"]:
            kind = rng.get("insert_kind") or "special insert"
            donor = ""
            if "audio_start" in rng and "audio_end" in rng:
                a_start = _frame(float(rng["audio_start"]), fps)
                a_end = _frame(float(rng["audio_end"]), fps)
                if (a_start, a_end) != (start_f, end_f):
                    donor = (
                        f" Its sound in the app comes from "
                        f"{float(rng['audio_start']):.2f}s to "
                        f"{float(rng['audio_end']):.2f}s of the "
                        "recording, which the timeline file cannot "
                        "point at separately, so check that "
                        "piece's sound after import."
                    )
            notes.append(
                f"{label} is a {kind} in the app.{donor}"
            )
        fade = rng.get("fade_in_s")
        if isinstance(fade, (int, float)) and not isinstance(fade, bool):
            fade_s = float(fade)
            if fade_s > 0:
                if index == 0:
                    notes.append(
                        "The first piece carries a crossfade, but "
                        "there is no join before it, so no fade is "
                        "exported for it."
                    )
                else:
                    piece["fade_s"] = fade_s
                    piece["fade_frames"] = _frame(fade_s, fps)
        pieces.append(piece)
    return pieces


def _source_duration_frames(cuts: dict, pieces: list, fps: float) -> int:
    raw = cuts.get("source_duration_s")
    if isinstance(raw, (int, float)) and float(raw) > 0:
        return _frame(float(raw), fps)
    return max((p["src_out"] for p in pieces), default=0)


def _stem(source_name: str) -> str:
    stem = PurePath(str(source_name)).stem or "recording"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "recording"


def _filename(source_name: str, suffix: str) -> str:
    return f"{_stem(source_name)}{suffix}"


# -- shared time formatting -----------------------------------------------------


def _fps_rational(fps: float) -> tuple:
    """(numerator, denominator) of one frame's length, FCPXML style."""
    if abs(fps - round(fps)) < 1e-6:
        return (1, int(round(fps)))
    for target, num, den in (
        (29.97, 1001, 30000),
        (23.976, 1001, 24000),
        (59.94, 1001, 60000),
        (47.952, 1001, 48000),
        (119.88, 1001, 120000),
    ):
        if abs(fps - target) < 0.01:
            return (num, den)
    frac = Fraction(1, 1) / Fraction(fps).limit_denominator(100000)
    return (frac.numerator, frac.denominator)


def _rational_time(frames: int, fps: float) -> str:
    if frames == 0:
        return "0s"
    num, den = _fps_rational(fps)
    return f"{frames * num}/{den}s"


def _timecode(frames: int, fps: float) -> str:
    """Non-drop-frame HH:MM:SS:FF from a frame count."""
    rate = int(round(fps))
    frames = max(0, int(frames))
    ff = frames % rate
    total_s = frames // rate
    return (
        f"{total_s // 3600:02d}:{(total_s // 60) % 60:02d}:"
        f"{total_s % 60:02d}:{ff:02d}"
    )


def _record_tc(frames: int, fps: float) -> str:
    """Record timecode: the EDL convention starts at 01:00:00:00."""
    return _timecode(int(round(fps)) * 3600 + frames, fps)


# -- FCPXML 1.10 ------------------------------------------------------------------


def _fcpxml(pieces: list, source_name: str, src_duration_f: int,
            fps: float, notes: list) -> str:
    name = escape(source_name)
    stem = escape(_stem(source_name))
    src_url = "file://localhost/" + quote(str(source_name))
    frame_dur = _rational_time(1, fps)
    total = pieces[-1]["rec_out"] if pieces else 0
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!DOCTYPE fcpxml>",
        '<fcpxml version="1.10">',
        "  <resources>",
        f'    <format id="r1" name="ExportFormat" '
        f'frameDuration="{frame_dur}"/>',
        f'    <asset id="r2" name="{name}" start="0s" '
        f'duration="{_rational_time(src_duration_f, fps)}" '
        f'hasVideo="1" hasAudio="1" src="{escape(src_url)}"/>',
        "  </resources>",
        "  <library>",
        f'    <event name="{stem}">',
        f'      <project name="{stem}">',
        f'        <sequence format="r1" '
        f'duration="{_rational_time(total, fps)}" tcStart="0s">',
        "          <spine>",
    ]
    for piece in pieces:
        if piece["fade_frames"] > 0:
            fade = piece["fade_frames"]
            offset = piece["rec_in"] - fade // 2
            lines.append(
                f'            <transition name="Cross Fade" '
                f'offset="{_rational_time(offset, fps)}" '
                f'duration="{_rational_time(fade, fps)}"/>'
            )
        lines.append(
            f'            <asset-clip name="{escape(piece["label"])}" '
            f'ref="r2" offset="{_rational_time(piece["rec_in"], fps)}" '
            f'start="{_rational_time(piece["src_in"], fps)}" '
            f'duration="{_rational_time(piece["duration"], fps)}"/>'
        )
    lines += [
        "          </spine>",
        "        </sequence>",
        "      </project>",
        "    </event>",
        "  </library>",
        "</fcpxml>",
    ]
    return "\n".join(lines) + "\n"


# -- Premiere Pro XML (xmeml version 5) -------------------------------------------


def _xmeml_rate(fps: float) -> str:
    timebase = int(round(fps))
    # NTSC rates are the x.97/x.976 family (30000/1001 and kin).
    ntsc = "TRUE" if any(
        abs(fps - t) < 0.01 for t in (29.97, 23.976, 59.94, 47.952, 119.88)
    ) else "FALSE"
    return f"<rate><timebase>{timebase}</timebase><ntsc>{ntsc}</ntsc></rate>"


def _xmeml_clipitem(piece: dict, fps: float, media: str, file_ref: str,
                    cid: str) -> str:
    rate = _xmeml_rate(fps)
    return (
        f'<clipitem id="{cid}"><name>{escape(piece["label"])}</name>'
        f"{rate}"
        f"<start>{piece['rec_in']}</start><end>{piece['rec_out']}</end>"
        f"<in>{piece['src_in']}</in><out>{piece['src_out']}</out>"
        f"{file_ref}"
        f"<sourcetrack><mediatype>{media}</mediatype>"
        f"<trackindex>1</trackindex></sourcetrack></clipitem>"
    )


def _xmeml_transition(piece: dict, fps: float) -> str:
    fade = piece["fade_frames"]
    before = fade // 2
    start = piece["rec_in"] - before
    end = piece["rec_in"] + (fade - before)
    return (
        "<transitionitem>"
        f"{_xmeml_rate(fps)}"
        f"<start>{start}</start><end>{end}</end>"
        "<alignment>center</alignment>"
        "<effect><name>Cross Fade</name>"
        "<effectid>Cross Fade</effectid>"
        "<effectcategory>Audio</effectcategory>"
        "<effecttype>transition</effecttype>"
        "<mediatype>audio</mediatype></effect>"
        "</transitionitem>"
    )


def _premiere_xml(pieces: list, source_name: str, src_duration_f: int,
                  fps: float, notes: list) -> str:
    stem = escape(_stem(source_name))
    rate = _xmeml_rate(fps)
    total = pieces[-1]["rec_out"] if pieces else 0
    src_url = "file://localhost/" + quote(str(source_name))
    file_def = (
        '<file id="file-1">'
        f"<name>{escape(source_name)}</name>"
        f"<pathurl>{escape(src_url)}</pathurl>"
        f"{rate}<duration>{src_duration_f}</duration>"
        "<media><video/><audio/></media></file>"
    )
    video_items = []
    audio_items = []
    for i, piece in enumerate(pieces):
        video_items.append(
            _xmeml_clipitem(
                piece, fps, "video",
                file_def if i == 0 else '<file id="file-1"/>',
                f"clipitem-v{i + 1}",
            )
        )
        if piece["fade_frames"] > 0:
            audio_items.append(_xmeml_transition(piece, fps))
        audio_items.append(
            _xmeml_clipitem(
                piece, fps, "audio", '<file id="file-1"/>',
                f"clipitem-a{i + 1}",
            )
        )
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!DOCTYPE xmeml>",
        '<xmeml version="5">',
        '  <sequence id="sequence-1">',
        f"    <name>{stem}</name>",
        f"    <duration>{total}</duration>",
        f"    {rate}",
        "    <media>",
        "      <video>",
        "        <track>",
        *[f"          {item}" for item in video_items],
        "        </track>",
        "      </video>",
        "      <audio>",
        "        <track>",
        *[f"          {item}" for item in audio_items],
        "        </track>",
        "      </audio>",
        "    </media>",
        "  </sequence>",
        "</xmeml>",
    ]
    return "\n".join(lines) + "\n"


# -- CMX 3600 EDL -------------------------------------------------------------------


def _edl_reel(source_name: str) -> str:
    reel = re.sub(r"[^A-Za-z0-9]", "", _stem(source_name)).upper()[:8]
    return reel or "SOURCE"


def _edl(pieces: list, source_name: str, fps: float, notes: list) -> str:
    title = re.sub(r"[^\x20-\x7E]", "", _stem(source_name))[:64] or "Recording"
    reel = _edl_reel(source_name)
    lines = [
        f"TITLE: {title}",
        "FCM: NON-DROP FRAME",
        "",
    ]
    for number, piece in enumerate(pieces, start=1):
        src_in = _timecode(piece["src_in"], fps)
        src_out = _timecode(piece["src_out"], fps)
        rec_in = _record_tc(piece["rec_in"], fps)
        rec_out = _record_tc(piece["rec_out"], fps)
        for channel in ("V", "A"):
            lines.append(
                f"{number:03d}  {reel:<8} {channel:<7}C        "
                f"{src_in} {src_out} {rec_in} {rec_out}"
            )
        lines.append(f"* FROM CLIP NAME: {source_name} ({piece['label']})")
    return "\n".join(lines) + "\n"


# -- the entry point ------------------------------------------------------------------


def export_timeline(cuts: dict, source_name: str,
                    fps: Optional[float] = None) -> dict:
    """Export the cuts plan as three timeline files.

    Returns ``{format_key: (filename, text, notes)}`` with keys
    ``"fcpxml"``, ``"premiere"``, and ``"edl"``. ``notes`` is a
    list of plain sentences naming anything that format could not
    carry; it is empty only when the file says everything the plan
    says. Pure: the input dict is not changed and nothing is
    written anywhere.
    """
    shared_notes: list = []
    resolved_fps = _resolve_fps(cuts or {}, fps, shared_notes)
    pieces = _pieces(cuts or {}, resolved_fps, shared_notes)
    if not pieces:
        shared_notes.append(
            "The edit has no kept pieces yet, so the timeline "
            "files are empty."
        )
    shared_notes.append(
        "The files point at the recording by its file name "
        f"({source_name}); the other editor may ask you to "
        "relink it if the file lives somewhere else."
    )
    shared_notes.append(
        "The plan does not record the picture size, so the "
        "timeline files leave it to the other editor, which sets "
        "it from the recording itself."
    )
    src_duration_f = _source_duration_frames(
        cuts or {}, pieces, resolved_fps
    )

    fcpxml_notes = list(shared_notes)
    premiere_notes = list(shared_notes)
    edl_notes = list(shared_notes)
    for piece in pieces:
        if "fade_s" not in piece:
            continue
        if piece["fade_frames"] >= 1:
            edl_notes.append(
                f"The sound crossfade at {piece['label']} "
                f"({piece['fade_s']:.2f}s in the app) is not in "
                "the EDL; an EDL cannot carry it. It is in the "
                "Final Cut and Premiere files."
            )
        else:
            for target in (fcpxml_notes, premiere_notes, edl_notes):
                target.append(
                    f"The sound crossfade at {piece['label']} "
                    "is shorter than one frame, so no timeline "
                    "file carries it."
                )
    return {
        "fcpxml": (
            _filename(source_name, "-final-cut.fcpxml"),
            _fcpxml(pieces, source_name, src_duration_f,
                    resolved_fps, fcpxml_notes),
            fcpxml_notes,
        ),
        "premiere": (
            _filename(source_name, "-premiere.xml"),
            _premiere_xml(pieces, source_name, src_duration_f,
                          resolved_fps, premiere_notes),
            premiere_notes,
        ),
        "edl": (
            _filename(source_name, ".edl"),
            _edl(pieces, source_name, resolved_fps, edl_notes),
            edl_notes,
        ),
    }
