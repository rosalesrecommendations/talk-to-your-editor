"""Word corrections: a session-only text layer over the transcript.

A correction replaces the text of one word, or of a run of neighboring
words, with text the creator typed: fixing a spelling, splitting one
mis-heard word into two or three, or merging two or three fragments
back into one word. A correction is stored as a half-open word span
``[word_start, word_end)`` plus its replacement text.

Corrections are text only. They never change the audio, the word
timings, or the cuts: a corrected span keeps the original span's start
and end times, and every word index still refers to the transcript's
own word list. They live in the session folder
(``tye-session/word_corrections.json``), never in the job's files.

Two readers use them:

- The player page, through :func:`transcript_display_entries`: a
  corrected span renders as one entry carrying the corrected text,
  marked as corrected, with the original words alongside so the
  correction can be removed.
- A direction run, through :func:`transcript_with_corrections`: the
  transcript handed to the provider carries the corrected text, so
  the direction step reads the fixed words.

Nothing else consumes corrections: in particular, a rendered video's
captions are built by the render step from the original transcript,
so they keep the original words.
"""

from __future__ import annotations

import json
from pathlib import Path

CORRECTIONS_FILENAME = "word_corrections.json"


def _coerce_span(item: dict, word_count: int) -> dict | None:
    """One stored correction, cleaned, or None if it does not hold up."""
    try:
        start = int(item["word_start"])
        end = int(item["word_end"])
    except (KeyError, TypeError, ValueError):
        return None
    text = str(item.get("text") or "").strip()
    if not text or not 0 <= start < end <= word_count:
        return None
    return {"word_start": start, "word_end": end, "text": text}


def load_corrections(path: Path, word_count: int) -> list[dict]:
    """Corrections stored at ``path``, sorted by span; [] if none."""
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = payload.get("corrections") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    corrections = []
    for item in items:
        if isinstance(item, dict):
            cleaned = _coerce_span(item, word_count)
            if cleaned is not None:
                corrections.append(cleaned)
    corrections.sort(key=lambda c: (c["word_start"], c["word_end"]))
    return corrections


def save_corrections(path: Path, corrections: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"corrections": corrections}, handle, indent=2)
        handle.write("\n")


def _validate_new_span(
    word_start, word_end, text, word_count: int
) -> tuple[int, int, str]:
    if (
        not isinstance(word_start, int)
        or isinstance(word_start, bool)
        or not isinstance(word_end, int)
        or isinstance(word_end, bool)
    ):
        raise ValueError("word_start and word_end must be whole numbers")
    cleaned = str(text or "").strip()
    if not cleaned:
        raise ValueError("Type the corrected words first.")
    if word_end <= word_start:
        raise ValueError("Pick at least one word to fix.")
    if word_start < 0 or word_end > word_count:
        raise ValueError("Those words are not in the transcript.")
    return word_start, word_end, cleaned


def upsert_correction(
    corrections: list[dict],
    word_start,
    word_end,
    text,
    word_count: int,
) -> list[dict]:
    """Add a correction, replacing one on the exact same span.

    A span that only partly overlaps an existing correction is a
    clean error: the viewer removes the old correction first, so two
    corrections never fight over the same words.
    """
    start, end, cleaned = _validate_new_span(
        word_start, word_end, text, word_count
    )
    out = []
    for existing in corrections:
        if existing["word_start"] == start and existing["word_end"] == end:
            continue  # replaced below
        if existing["word_start"] < end and start < existing["word_end"]:
            raise ValueError(
                "Those words already have a correction. Remove that "
                "correction first, then save the new one."
            )
        out.append(existing)
    out.append({"word_start": start, "word_end": end, "text": cleaned})
    out.sort(key=lambda c: (c["word_start"], c["word_end"]))
    return out


def remove_correction(
    corrections: list[dict], word_start, word_end
) -> list[dict]:
    """Drop the correction on exactly ``[word_start, word_end)``."""
    out = [
        c
        for c in corrections
        if not (c["word_start"] == word_start and c["word_end"] == word_end)
    ]
    if len(out) == len(corrections):
        raise ValueError("There is no correction on those words to remove.")
    return out


def transcript_display_entries(
    words: list[dict], statuses: list[str], corrections: list[dict]
) -> list[dict]:
    """Per-entry view of the transcript for the player page.

    Ordinary words keep one entry each. A corrected span becomes a
    single entry whose ``word`` is the corrected text, with
    ``corrected`` set, the span's ``word_end``, and the ``original``
    words it replaced, so the page can show the fix and offer to
    remove it. The entry's status is the span's shared status when
    the whole span agrees, else "kept" when any word in it is kept.
    """
    by_start = {c["word_start"]: c for c in corrections}
    entries: list[dict] = []
    index = 0
    while index < len(words):
        correction = by_start.get(index)
        if correction is None:
            entries.append(
                {
                    "index": index,
                    "word": words[index]["word"],
                    "status": statuses[index],
                    "corrected": False,
                    "word_end": index + 1,
                }
            )
            index += 1
            continue
        end = correction["word_end"]
        span_statuses = statuses[index:end]
        if all(s == span_statuses[0] for s in span_statuses):
            status = span_statuses[0]
        elif "kept" in span_statuses:
            status = "kept"
        else:
            status = span_statuses[0]
        entries.append(
            {
                "index": index,
                "word": correction["text"],
                "status": status,
                "corrected": True,
                "word_end": end,
                "original": " ".join(
                    str(words[i]["word"]) for i in range(index, end)
                ),
            }
        )
        index = end
    return entries


def transcript_with_corrections(transcript: dict, corrections: list[dict]) -> dict:
    """A transcript copy whose word texts carry the corrections.

    The word list keeps its exact length, order, and timings, so word
    indices and every span a direction run proposes still line up
    with the real transcript. A corrected span's replacement text is
    laid over the span's own slots: when the replacement has fewer
    words than the span, the leftover slots read as empty; when it
    has more, the extra words ride in the span's last slot. Either
    way the span as a whole reads as the corrected text and keeps its
    original start and end times.
    """
    words = [dict(word) for word in transcript.get("words", [])]
    for correction in corrections:
        start, end = correction["word_start"], correction["word_end"]
        if not 0 <= start < end <= len(words):
            continue
        tokens = correction["text"].split()
        if not tokens:
            continue
        slots = words[start:end]
        if len(tokens) <= len(slots):
            for offset, slot in enumerate(slots):
                slot["word"] = tokens[offset] if offset < len(tokens) else ""
        else:
            for offset, slot in enumerate(slots[:-1]):
                slot["word"] = tokens[offset]
            slots[-1]["word"] = " ".join(tokens[len(slots) - 1 :])
    result = dict(transcript)
    result["words"] = words
    return result
