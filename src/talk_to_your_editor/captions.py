"""Clean captions: the kept words, with the fillers left off screen.

A caption file (WebVTT) built from a job's final kept plan. The
words shown are the kept words of the plan, timed on the rendered
video's own timeline, with one conservative change: filler words
(um, uh, er, ah, hmm, and their stretched spellings) never appear
in the caption text. The fillers stay in the audio and stay in the
transcript, where the editing engines need them; they are only kept
off the viewer's screen. Meaning-bearing words are never on the
list: words like "like", "so" or "well" can carry the sentence, so
they always show.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .timeline import Timeline

CAPTIONS_FILENAME = "clean-captions.vtt"
MAX_CUE_S = 4.0
MIN_SENTENCE_CUE_S = 1.2
GAP_BREAK_S = 1.0
MAX_CUE_WORDS = 14

_FILLER_RE = re.compile(
    r"^(?:u+m+|u+h+|u+hm|erm|e+r+|a+h+|h+m{2,}|m+h+m+)$"
)


def is_filler(word: str) -> bool:
    """True for a pure filler token and nothing else."""
    token = re.sub(r"[^a-z]", "", str(word).lower())
    if not token:
        return False
    return bool(_FILLER_RE.fullmatch(token))


def _vtt_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def kept_word_rows(transcript: dict, cuts: dict) -> list:
    """Kept words with their times on the output (rendered) timeline.

    A word's output time is its own offset inside the kept piece
    that holds it, added to that piece's place in the output: the
    same arithmetic the timeline itself is built on.
    """
    timeline = Timeline(transcript, cuts)
    rows = []
    for index, word in enumerate(timeline.words):
        if timeline.word_status(index) != "kept":
            continue
        mid = (float(word["start"]) + float(word["end"])) / 2.0
        segment = None
        for candidate in timeline.segments:
            rng = candidate["range"]
            if (
                not candidate["special"]
                and float(rng["start"]) - 1e-6
                <= mid
                <= float(rng["end"]) + 1e-6
            ):
                segment = candidate
                break
        if segment is None:
            continue
        base = float(segment["range"]["start"])
        rows.append(
            {
                "word": str(word.get("word") or ""),
                "start": segment["output_start"]
                + (float(word["start"]) - base),
                "end": segment["output_start"]
                + (float(word["end"]) - base),
            }
        )
    return rows


def group_cues(rows: list) -> list:
    """Group kept words into caption cues on the output timeline.

    A cue ends at a sentence end once it has some length, at a long
    quiet gap, when it has run long enough, or when it has enough
    words. Filler words are counted and left out of the text; a cue
    made only of fillers disappears.
    """
    cues = []
    current: list = []

    def flush():
        if not current:
            return
        shown = [r for r in current if not is_filler(r["word"])]
        hidden = len(current) - len(shown)
        if shown:
            cues.append(
                {
                    "start": current[0]["start"],
                    "end": current[-1]["end"],
                    "text": " ".join(r["word"] for r in shown),
                    "hidden": hidden,
                }
            )
        current.clear()

    for row in rows:
        if current:
            gap = row["start"] - current[-1]["end"]
            span = row["end"] - current[0]["start"]
            prev_text = current[-1]["word"].rstrip()
            sentence_end = prev_text.endswith((".", "?", "!"))
            if (
                gap > GAP_BREAK_S
                or span > MAX_CUE_S
                or len(current) >= MAX_CUE_WORDS
                or (
                    sentence_end
                    and current[-1]["end"] - current[0]["start"]
                    >= MIN_SENTENCE_CUE_S
                )
            ):
                flush()
        current.append(row)
    flush()
    return cues


def render_vtt(cues: list) -> str:
    lines = ["WEBVTT", ""]
    for cue in cues:
        lines.append(
            f"{_vtt_time(cue['start'])} --> {_vtt_time(cue['end'])}"
        )
        lines.append(cue["text"])
        lines.append("")
    return "\n".join(lines)


def build_captions(job_dir: Path, cuts: dict, out_path: Path) -> dict:
    """Write the clean caption file for a job's current plan.

    Returns {"cues": n, "hidden_fillers": n, "path": str}. The
    transcript file is only read; nothing about it changes.
    """
    transcript = json.loads(
        (Path(job_dir) / "transcript.json").read_text(encoding="utf-8")
    )
    rows = kept_word_rows(transcript, cuts)
    cues = group_cues(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_vtt(cues), encoding="utf-8")
    return {
        "cues": len(cues),
        "hidden_fillers": sum(cue["hidden"] for cue in cues),
        "path": str(out_path),
    }
