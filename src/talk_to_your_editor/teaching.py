"""Teaching intake: the fast way to teach the app how you edit.

Optional, never required: the app also learns over time from the
creator's edits and grades. A teaching drop is one of:

- a screen recording of an edit, with the creator talking through
  the choices (with or without the pair below),
- a before-and-after pair on its own (the raw video and the
  finished edit, no recording),
- a pasted link to a finished video,
- a finished video on its own, for "a style I like".

Every input ends the same way: draft rules, in plain words, that
enter the same candidate path as learned rules. Nothing applies
until the creator says yes.

A teaching session's media and work files live under
``~/.talk-to-your-editor/teaching/<teach_id>/``. The drafts a run
makes for the current app session live in that session's folder
as ``teaching_drafts.json``.

Standard library only. The one speech seam is ``transcribe_media``:
tests stub it, so no test ever makes a live call.
"""

from __future__ import annotations

import difflib
import json
import re
import string
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

from . import framing
from . import transcribe as speech

TEACHING_DIR_NAME = "teaching"
DRAFTS_FILENAME = "teaching_drafts.json"
FRAMING_FILENAME = "framing_profile.json"
SESSION_META_FILENAME = "teach.json"

VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm")

# A recording with fewer words than this has no usable talking in
# it: there is nothing to draft rules from.
MIN_TALKING_WORDS = 20

# A pasted link may bring in a video up to this size, and no more.
MAX_LINK_BYTES = 500 * 1024 * 1024
LINK_TIMEOUT_S = 120

PAIR_SOURCE_NOTE = (
    "From your cuts. No spoken reason was attached to this one."
)
FINISHED_SOURCE_NOTE = "From watching the finished video only."
RECORDING_SOURCE_NOTE = "From your teaching video."

FILLER_WORDS = ("um", "uh", "er")

_TEACH_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class TeachingError(RuntimeError):
    """A teaching failure whose message is a short plain code.

    ``no_speech`` is the one code today: no route could write the
    words down, or the recording held too few words to draft from.
    The web layer turns it into the one plain sentence the creator
    sees, never an error screen.
    """


class TeachingLinkError(RuntimeError):
    """The video at a pasted link could not be brought in."""


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def teaching_root() -> Path:
    """Where every teaching session lives, beside the app's data."""
    return Path.home() / ".talk-to-your-editor" / TEACHING_DIR_NAME


def new_teaching_session(kind: str) -> tuple:
    """Open a teaching session folder; returns (teach_id, folder)."""
    if kind not in ("mine", "style"):
        kind = "mine"
    teach_id = uuid.uuid4().hex[:12]
    folder = teaching_root() / teach_id
    folder.mkdir(parents=True, exist_ok=False)
    (folder / SESSION_META_FILENAME).write_text(
        json.dumps({"kind": kind}) + "\n", encoding="utf-8"
    )
    return teach_id, folder


def teaching_session_dir(teach_id: str) -> Optional[Path]:
    """The folder for one teaching session, or None when unknown."""
    if not _TEACH_ID.match(str(teach_id or "")):
        return None
    folder = teaching_root() / str(teach_id)
    return folder if folder.is_dir() else None


def session_kind(teach_id: str) -> str:
    """A teaching session's kind: "mine" or "style"."""
    folder = teaching_session_dir(teach_id)
    if folder is not None:
        try:
            payload = json.loads(
                (folder / SESSION_META_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict) and payload.get("kind") in (
            "mine",
            "style",
        ):
            return payload["kind"]
    return "mine"


# ---------------------------------------------------------------------------
# Drafts for the current app session
# ---------------------------------------------------------------------------


def drafts_path(session_dir: Path) -> Path:
    return Path(session_dir) / DRAFTS_FILENAME


def load_drafts(session_dir: Path) -> list:
    """The teaching drafts kept for one app session."""
    path = drafts_path(session_dir)
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict) and isinstance(
            payload.get("drafts"), list
        ):
            return [
                draft
                for draft in payload["drafts"]
                if isinstance(draft, dict)
            ]
    return []


def save_drafts(session_dir: Path, drafts: list) -> None:
    path = drafts_path(session_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"drafts": drafts}, handle, indent=2)
        handle.write("\n")


def add_drafts(session_dir: Path, drafts: list) -> list:
    """Add a run's drafts to the session's; returns the full list."""
    current = load_drafts(session_dir)
    known = {draft.get("draft_id") for draft in current}
    for draft in drafts:
        if isinstance(draft, dict) and draft.get("draft_id") not in known:
            current.append(draft)
            known.add(draft.get("draft_id"))
    save_drafts(session_dir, current)
    return current


def remove_draft(session_dir: Path, draft_id: str) -> bool:
    """Drop one draft by id (a dismissed suggestion); True if removed."""
    current = load_drafts(session_dir)
    kept = [d for d in current if d.get("draft_id") != draft_id]
    if len(kept) == len(current):
        return False
    save_drafts(session_dir, kept)
    return True


# ---------------------------------------------------------------------------
# Draft records
# ---------------------------------------------------------------------------


def make_draft(
    *,
    statement: str,
    reason: str,
    at_s: Optional[float],
    quote: str,
    basis: str,
    kind: str,
    source_note: str,
    proof: Optional[str] = None,
) -> dict:
    """One draft rule, in the one shape every teaching draft uses."""
    draft_id = uuid.uuid4().hex[:12]
    return {
        "draft_id": draft_id,
        "statement": str(statement or "").strip(),
        "reason": str(reason or "").strip(),
        "at_s": at_s,
        "quote": str(quote or ""),
        "basis": basis,
        "proof": proof,
        "kind": kind,
        "source_note": source_note,
        "pattern_key": f"teach:{draft_id}",
    }


def finalize_drafts(
    raw_drafts: list,
    *,
    basis: str,
    kind: str,
    source_note: str,
) -> list:
    """Stamp provider drafts (statement/reason/at_s/quote) as records."""
    drafts = []
    for raw in raw_drafts or []:
        if not isinstance(raw, dict):
            continue
        statement = str(raw.get("statement") or "").strip()
        if not statement:
            continue
        at_s = raw.get("at_s")
        if not isinstance(at_s, (int, float)) or isinstance(at_s, bool):
            at_s = None
        drafts.append(
            make_draft(
                statement=statement,
                reason=str(raw.get("reason") or "").strip(),
                at_s=float(at_s) if at_s is not None else None,
                quote=str(raw.get("quote") or "")[:200],
                basis=basis,
                kind=kind,
                source_note=source_note,
            )
        )
    return drafts


# ---------------------------------------------------------------------------
# Speech: the seam tests stub
# ---------------------------------------------------------------------------


def transcribe_media(
    path: Path,
    elevenlabs_key: Optional[str],
    work_dir: Optional[Path] = None,
) -> list:
    """The words of one media file, with their times.

    The sound is pulled out with the shared speech pipe, then
    written down by ElevenLabs Scribe when a key is given, else by
    the speech pack on this computer. Raises
    ``TeachingError("no_speech")`` when neither route exists or the
    transcript holds fewer than ``MIN_TALKING_WORDS`` words.
    """
    path = Path(path)
    folder = Path(work_dir) if work_dir is not None else path.parent
    folder.mkdir(parents=True, exist_ok=True)
    wav = folder / f"audio-{uuid.uuid4().hex[:8]}.wav"
    try:
        speech.extract_audio(path, wav)
        if elevenlabs_key:
            result = speech.scribe_transcribe_wav(wav, elevenlabs_key)
        elif speech.local_available():
            result = speech.local_transcribe_wav(wav)
        else:
            raise TeachingError("no_speech")
    except TeachingError:
        raise
    except Exception as exc:
        raise TeachingError("no_speech") from exc
    words = [
        word
        for word in (result.get("words") or [])
        if isinstance(word, dict) and str(word.get("word") or "").strip()
    ]
    if len(words) < MIN_TALKING_WORDS:
        raise TeachingError("no_speech")
    return words


# ---------------------------------------------------------------------------
# The pair: what the finished edit cut, and what it kept
# ---------------------------------------------------------------------------


def _norm(token: str) -> str:
    return str(token or "").lower().strip(string.punctuation)


def _fmt_seconds(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _kept_sound_pauses(words: list, finished_path) -> Optional[tuple]:
    """(kept, total) pauses in the finished edit that hold sound.

    A pause is the gap between one finished word's end and the
    next word's start, 0.15 to 1.5 s. It holds sound when the
    finished file's own audio is not silent across the whole
    gap: a breath, water, room life. The count comes from the
    sound envelope, never from the word stamps alone. Returns
    None when the sound cannot be measured, so the caller
    leaves the line out instead of guessing.
    """
    if finished_path is None:
        return None
    try:
        import tempfile

        from . import smooth

        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "finished.wav"
            speech.extract_audio(Path(finished_path), wav)
            env = smooth.envelope_db(wav)
        if not env:
            return None
        room = smooth.room_level_db(env)
        runs = smooth.audio_silence_runs(env, room, min_s=0.15)
    except Exception:
        return None
    kept = 0
    total = 0
    for prev, nxt in zip(words, words[1:]):
        try:
            gap_start = float(prev["end"])
            gap_end = float(nxt["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if not 0.15 <= gap_end - gap_start <= 1.5:
            continue
        total += 1
        silent = any(
            a <= gap_start + 0.03 and b >= gap_end - 0.03
            for a, b in runs
        )
        if not silent:
            kept += 1
    if not total:
        return None
    return kept, total


def measured_summary(
    raw_words: Optional[list],
    finished_words: Optional[list],
    finished_path=None,
) -> list:
    """What the pair actually measures, as plain lines for the chat.

    The teach summary used to print only the draft rules'
    statements, which read as generic advice (0.27.1, P2
    item 11). These lines are measured from the pair itself:
    the finished edit's opening words, the pauses between its
    sentences, and how many of its pauses keep real sound.
    A line that cannot be measured is left out, never
    estimated. ``raw_words`` is accepted for symmetry with
    the drafts and for future contrasts; today's lines read
    the finished edit, which is the style being taught.
    """
    lines: list = []
    words = [
        w for w in (finished_words or [])
        if isinstance(w, dict) and w.get("word")
    ]
    if not words:
        return lines
    opening = " ".join(
        str(w.get("word")) for w in words[:9]
    ).strip()
    if opening:
        lines.append(f"Your finished edit opens on: \u201c{opening}\u201d")
    gaps: list = []
    for prev, nxt in zip(words, words[1:]):
        tail = str(prev.get("word") or "").rstrip("\"'\u201d\u2019)")
        if not tail.endswith((".", "?", "!")):
            continue
        try:
            gap = float(nxt["start"]) - float(prev["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= gap < 5:
            gaps.append(gap)
    if len(gaps) >= 3:
        gaps.sort()
        median = gaps[len(gaps) // 2]
        lines.append(
            "Pauses between sentences in your edit run about "
            f"{_fmt_seconds(median)} seconds ({len(gaps)} measured)."
        )
    pauses = _kept_sound_pauses(words, finished_path)
    if pauses is not None:
        kept, total = pauses
        lines.append(
            f"You keep the sound in {kept} of the {total} pauses "
            "in your edit (breaths, water), not dead air."
        )
    return lines


def pair_diff(raw_words: list, finished_words: list) -> dict:
    """Word-diff a raw/finished pair, on the raw transcript's indices.

    Returns the set of raw indices the finished edit cut, the set
    it kept, the cut spans as half-open (start, end) runs, and the
    two counts.
    """
    raw_tokens = [_norm(word.get("word")) for word in raw_words]
    finished_tokens = [_norm(word.get("word")) for word in finished_words]
    matcher = difflib.SequenceMatcher(
        a=raw_tokens, b=finished_tokens, autojunk=False
    )
    cut: set = set()
    kept: set = set()
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag == "equal":
            kept.update(range(i1, i2))
        elif tag in ("delete", "replace"):
            cut.update(range(i1, i2))
    cut_spans = []
    for index in sorted(cut):
        if cut_spans and cut_spans[-1][1] == index:
            cut_spans[-1][1] = index + 1
        else:
            cut_spans.append([index, index + 1])
    return {
        "cut": cut,
        "kept": kept,
        "cut_spans": [(start, end) for start, end in cut_spans],
        "cut_count": len(cut),
        "kept_count": len(kept),
    }


_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on",
    "at", "for", "with", "is", "it", "its", "this", "that", "i",
    "you", "we", "he", "she", "they", "my", "your", "our", "so",
    "as", "be", "was", "are", "were", "do", "does", "did", "not",
    "no", "yes", "just", "like", "when", "what", "all",
}


def _content_words(text: str) -> list:
    """The distinct words that carry meaning in a short text."""
    words = []
    for token in str(text or "").split():
        word = _norm(token)
        if word and word not in _STOP_WORDS and word not in words:
            words.append(word)
    return words


def attach_proof(drafts: list, diff: dict, raw_words: list) -> list:
    """Tie each draft to the real cuts it matches, when a pair is here.

    A draft earns a proof note when at least two of the words that
    carry its quote's meaning were cut in the finished edit. A
    draft with no match keeps ``proof`` as None.
    """
    cut_words = {
        _norm(raw_words[index].get("word"))
        for index in diff["cut"]
        if 0 <= index < len(raw_words)
    }
    cut_words.discard("")
    for draft in drafts:
        found = [
            word
            for word in _content_words(draft.get("quote"))
            if word in cut_words
        ]
        if len(found) >= 2:
            named = found[:3]
            quoted = [f'"{word}"' for word in named]
            if len(quoted) == 2:
                joined = f"{quoted[0]} and {quoted[1]}"
            else:
                joined = ", ".join(quoted[:-1]) + f" and {quoted[-1]}"
            draft["proof"] = (
                "Your finished edit does this: "
                f"it cuts words like {joined}."
            )
    return drafts


def _mostly_in(indices, group: set) -> bool:
    if not indices:
        return False
    inside = sum(1 for index in indices if index in group)
    return inside >= 0.6 * len(indices)


def drafts_from_pair(diff: dict, raw_words: list, kind: str = "mine") -> list:
    """Deterministic drafts from the cuts alone, when there is no
    recording: what the pair itself shows, with the counts behind
    each draft in plain words. No spoken reason is attached, and
    the drafts do not pretend one exists."""
    drafts = []
    tokens = [_norm(word.get("word")) for word in raw_words]

    filler_cut = sum(
        1 for index in diff["cut"] if tokens[index] in FILLER_WORDS
    )
    if filler_cut >= 2:
        drafts.append(
            make_draft(
                statement="Filler words like um and uh are cut out.",
                reason=(
                    "In the pair you shared, filler words were cut "
                    f"{filler_cut} times."
                ),
                at_s=None,
                quote="",
                basis="pair",
                kind=kind,
                source_note=PAIR_SOURCE_NOTE,
            )
        )

    # A word sequence of four or more words said twice, where the
    # earlier try was mostly cut and the later one mostly kept.
    first_seen: dict = {}
    duplicated = 0
    counted: set = set()
    for start in range(0, max(0, len(tokens) - 3)):
        gram = tuple(tokens[start:start + 4])
        if any(not token for token in gram):
            continue
        if gram not in first_seen:
            first_seen[gram] = start
            continue
        earlier = first_seen[gram]
        if earlier == start or (earlier, start) in counted:
            continue
        if (
            earlier > 0
            and start > 0
            and tokens[earlier - 1] == tokens[start - 1]
        ):
            # The same match, one word further along: not a new line.
            continue
        length = 4
        while (
            start + length < len(tokens)
            and earlier + length < len(tokens)
            and tokens[earlier + length] == tokens[start + length]
        ):
            length += 1
        counted.add((earlier, start))
        earlier_span = list(range(earlier, earlier + length))
        later_span = list(range(start, start + length))
        if _mostly_in(earlier_span, diff["cut"]) and _mostly_in(
            later_span, diff["kept"]
        ):
            duplicated += 1
    if duplicated >= 1:
        drafts.append(
            make_draft(
                statement=(
                    "Earlier tries at a line are cut; "
                    "the last try stays."
                ),
                reason=(
                    "In the pair you shared, a line was said more "
                    f"than once {duplicated} "
                    f"time{'s' if duplicated != 1 else ''}, and the "
                    "earlier tries were cut while the last try stayed."
                ),
                at_s=None,
                quote="",
                basis="pair",
                kind=kind,
                source_note=PAIR_SOURCE_NOTE,
            )
        )

    long_pauses = 0
    for index in range(len(raw_words) - 1):
        if index in diff["kept"] and (index + 1) in diff["kept"]:
            gap = float(raw_words[index + 1].get("start") or 0.0) - float(
                raw_words[index].get("end") or 0.0
            )
            if gap > 1.0:
                long_pauses += 1
    if long_pauses >= 3:
        drafts.append(
            make_draft(
                statement="Long pauses between words are taken out.",
                reason=(
                    "In the pair you shared, pauses longer than a "
                    f"second were taken out {long_pauses} times."
                ),
                at_s=None,
                quote="",
                basis="pair",
                kind=kind,
                source_note=PAIR_SOURCE_NOTE,
            )
        )
    return drafts


# ---------------------------------------------------------------------------
# A finished video on its own: only what the finished video can show
# ---------------------------------------------------------------------------


def _words_text(words: list) -> str:
    return " ".join(str(word.get("word") or "") for word in words)


def surface_drafts(finished_words: list, kind: str = "style") -> list:
    """Drafts a lone finished video can honestly support: the pace,
    how the captions are chunked (from the word timings), and the
    order of the parts (the opening and closing words)."""
    if not finished_words:
        return []
    first_start = float(finished_words[0].get("start") or 0.0)
    last_end = float(finished_words[-1].get("end") or 0.0)
    span_s = max(0.0, last_end - first_start)
    drafts = []
    if span_s > 0:
        per_minute = len(finished_words) / (span_s / 60.0)
        drafts.append(
            make_draft(
                statement=(
                    "The talking runs at about "
                    f"{int(round(per_minute))} words a minute."
                ),
                reason=(
                    f"The finished video has {len(finished_words)} "
                    "words in about "
                    f"{int(round(span_s))} seconds of talking."
                ),
                at_s=None,
                quote="",
                basis="finished_only",
                kind=kind,
                source_note=FINISHED_SOURCE_NOTE,
            )
        )
    chunks = []
    current = 1
    for index in range(1, len(finished_words)):
        gap = float(finished_words[index].get("start") or 0.0) - float(
            finished_words[index - 1].get("end") or 0.0
        )
        if gap > 0.6:
            chunks.append(current)
            current = 1
        else:
            current += 1
    chunks.append(current)
    if chunks:
        average = sum(chunks) / len(chunks)
        average_text = (
            str(int(average))
            if float(average).is_integer()
            else f"{average:.1f}"
        )
        drafts.append(
            make_draft(
                statement=(
                    "Captions run about "
                    f"{average_text} words at a time."
                ),
                reason=(
                    "Measured from the pauses in the finished "
                    f"video: {len(chunks)} groups of words, about "
                    f"{average_text} words in each group."
                ),
                at_s=None,
                quote="",
                basis="finished_only",
                kind=kind,
                source_note=FINISHED_SOURCE_NOTE,
            )
        )
    opening = _words_text(finished_words[:6])
    closing = _words_text(finished_words[-6:])
    if opening and closing:
        drafts.append(
            make_draft(
                statement=(
                    f'The video opens with "{opening}" and closes '
                    f'with "{closing}".'
                ),
                reason=(
                    "These are the first words and the last words "
                    "of the finished video."
                ),
                at_s=None,
                quote="",
                basis="finished_only",
                kind=kind,
                source_note=FINISHED_SOURCE_NOTE,
            )
        )
    return drafts[:3]


# ---------------------------------------------------------------------------
# A pasted link: bring the video in, or say plainly that it failed
# ---------------------------------------------------------------------------


def _download(url: str, dest: Path, user_agent: str = "TalkToYourEditor") -> Path:
    """Download one URL to ``dest``, under the size cap."""
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent}
    )
    total = 0
    try:
        with urllib.request.urlopen(
            request, timeout=LINK_TIMEOUT_S
        ) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    if int(declared) > MAX_LINK_BYTES:
                        raise TeachingLinkError(
                            "That video is too large to bring in."
                        )
                except ValueError:
                    pass
            with open(dest, "wb") as handle:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_LINK_BYTES:
                        handle.close()
                        dest.unlink(missing_ok=True)
                        raise TeachingLinkError(
                            "That video is too large to bring in."
                        )
                    handle.write(chunk)
    except TeachingLinkError:
        raise
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise TeachingLinkError(
            "The app could not get that video from the link."
        ) from exc
    if total == 0:
        dest.unlink(missing_ok=True)
        raise TeachingLinkError(
            "The app could not get that video from the link."
        )
    return dest


def _fetch_page(url: str) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": "TalkToYourEditor"}
    )
    try:
        with urllib.request.urlopen(
            request, timeout=LINK_TIMEOUT_S
        ) as response:
            raw = response.read(2 * 1024 * 1024)
    except Exception as exc:
        raise TeachingLinkError(
            "The app could not get that video from the link."
        ) from exc
    return raw.decode("utf-8", "replace")


_OG_VIDEO_PATTERNS = (
    re.compile(
        r'<meta[^>]+property=["\']og:video(?::url)?["\'][^>]*'
        r'content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*'
        r'property=["\']og:video(?::url)?["\']',
        re.IGNORECASE,
    ),
)
_SOURCE_PATTERN = re.compile(
    r'<source[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE
)


def _video_candidate(page: str, base_url: str) -> Optional[str]:
    """The video file a page points at, if it points at one."""
    for pattern in _OG_VIDEO_PATTERNS:
        match = pattern.search(page)
        if match:
            return urllib.parse.urljoin(base_url, match.group(1))
    for match in _SOURCE_PATTERN.finditer(page):
        candidate = urllib.parse.urljoin(base_url, match.group(1))
        suffix = Path(urllib.parse.urlsplit(candidate).path).suffix.lower()
        if suffix in VIDEO_SUFFIXES:
            return candidate
    return None


# ---------------------------------------------------------------------------
# YouTube links (v0.17, feedback item 3)
#
# A YouTube watch page names no video file in its meta tags, so
# the generic page read above found nothing and Justin's own
# video would not come in ("It should be able to do that from
# YouTube. I need you to fix that"). The watch page does carry
# the player's own data, including the stream addresses for a
# public video. This branch reads that data: a ready-made
# (picture and sound together) stream is downloaded as it is;
# when only separate picture and sound streams are offered,
# both are downloaded and joined with ffmpeg, the same tool
# the app already uses. When YouTube offers neither (a private
# video, a sign-in wall, an encoded stream address), the error
# says what happened and what to do instead, in plain words.
# ---------------------------------------------------------------------------

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
_YOUTUBE_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
_YOUTUBE_FALLBACK = (
    "If it is your own video, YouTube Studio can download it: "
    "open the video there, choose Download, then drop the "
    "file in here instead."
)


def _youtube_video_id(parts) -> Optional[str]:
    """The video id in a YouTube link, or None for other links."""
    host = (parts.hostname or "").lower()
    if host not in _YOUTUBE_HOSTS:
        return None
    candidate = ""
    if host == "youtu.be":
        candidate = parts.path.lstrip("/").split("/")[0]
    elif parts.path == "/watch":
        query = urllib.parse.parse_qs(parts.query)
        candidate = (query.get("v") or [""])[0]
    else:
        segments = [s for s in parts.path.split("/") if s]
        if len(segments) >= 2 and segments[0] in (
            "shorts", "embed", "live", "v",
        ):
            candidate = segments[1]
    if candidate and _YOUTUBE_ID.match(candidate):
        return candidate
    return None


def _fetch_youtube_page(video_id: str) -> str:
    url = f"https://www.youtube.com/watch?v={video_id}"
    request = urllib.request.Request(
        url, headers={"User-Agent": _YOUTUBE_UA}
    )
    try:
        with urllib.request.urlopen(
            request, timeout=LINK_TIMEOUT_S
        ) as response:
            raw = response.read(8 * 1024 * 1024)
    except Exception as exc:
        raise TeachingLinkError(
            "The app could not get that video from the link."
        ) from exc
    return raw.decode("utf-8", "replace")


def _player_response(page: str) -> Optional[dict]:
    """The ytInitialPlayerResponse object embedded in a watch page.

    The object is JSON assigned to a page variable; it is read
    by brace matching, aware of strings, so nested objects and
    braces inside text cannot end it early.
    """
    marker = page.find("ytInitialPlayerResponse")
    if marker < 0:
        return None
    start = page.find("{", marker)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(page)):
        char = page[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(page[start:index + 1])
                except ValueError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _declared_size(entry: dict) -> Optional[int]:
    try:
        return int(entry.get("contentLength"))
    except (TypeError, ValueError):
        return None


def _pick_stream(entries: list) -> Optional[dict]:
    """The best direct stream: unknown size counts as fittable.

    Largest picture first among the entries whose declared size
    fits the cap (or say nothing about size); when every entry
    declares over the cap, the smallest is picked so the
    downloader's own cap answers with the too-large message.
    """
    usable = [entry for entry in entries if entry.get("url")]
    if not usable:
        return None

    def height(entry):
        try:
            return int(entry.get("height") or 0)
        except (TypeError, ValueError):
            return 0

    fitting = [
        entry for entry in usable
        if _declared_size(entry) is None
        or _declared_size(entry) <= MAX_LINK_BYTES
    ]
    if fitting:
        return max(fitting, key=height)
    return min(usable, key=lambda entry: _declared_size(entry) or 0)


def _youtube_plan(player: dict):
    """What to download for this video, or the plain reason not to.

    Returns ("single", entry) or ("pair", video_entry,
    audio_entry). Raises TeachingLinkError with the plain,
    actionable message for a video YouTube will not hand over.
    """
    playability = player.get("playabilityStatus") or {}
    status = str(playability.get("status") or "")
    if status and status != "OK":
        reason = str(playability.get("reason") or "").strip()
        if status == "LOGIN_REQUIRED" or "private" in reason.lower():
            raise TeachingLinkError(
                "YouTube says that video is private or needs a "
                "sign-in, so the app cannot bring it in from the "
                "link. " + _YOUTUBE_FALLBACK
            )
        raise TeachingLinkError(
            "YouTube would not play that video for the app"
            + (f" ({reason})" if reason else "")
            + ". " + _YOUTUBE_FALLBACK
        )
    streaming = player.get("streamingData") or {}
    formats = [
        entry for entry in streaming.get("formats") or []
        if isinstance(entry, dict)
    ]
    single = _pick_stream(formats)
    if single is not None:
        return ("single", single)
    adaptive = [
        entry for entry in streaming.get("adaptiveFormats") or []
        if isinstance(entry, dict)
    ]
    videos = [
        entry for entry in adaptive
        if str(entry.get("mimeType") or "").startswith("video/mp4")
    ] or [
        entry for entry in adaptive
        if str(entry.get("mimeType") or "").startswith("video/")
    ]
    audios = [
        entry for entry in adaptive
        if str(entry.get("mimeType") or "").startswith("audio/mp4")
    ] or [
        entry for entry in adaptive
        if str(entry.get("mimeType") or "").startswith("audio/")
    ]
    video = _pick_stream(videos)
    audio = _pick_stream(audios)
    if video is not None and audio is not None:
        return ("pair", video, audio)
    raise TeachingLinkError(
        "YouTube did not offer that video in a form the app "
        "can download. " + _YOUTUBE_FALLBACK
    )


def _merge_youtube_parts(
    video_path: Path, audio_path: Path, dest: Path
) -> Path:
    """Join a separate picture and sound download into one file."""
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-i", str(video_path),
                "-i", str(audio_path),
                "-c", "copy",
                str(dest),
            ],
            capture_output=True,
            timeout=LINK_TIMEOUT_S * 5,
        )
    except FileNotFoundError as exc:
        raise TeachingLinkError(
            "ffmpeg was not found on this computer, so the app "
            "could not join the video and sound YouTube sent."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TeachingLinkError(
            "Joining the video and sound YouTube sent took too "
            "long, so the app stopped. " + _YOUTUBE_FALLBACK
        ) from exc
    if result.returncode != 0 or not dest.is_file():
        dest.unlink(missing_ok=True)
        raise TeachingLinkError(
            "The app could not join the video and sound YouTube "
            "sent. " + _YOUTUBE_FALLBACK
        )
    return dest


def _fetch_youtube(video_id: str, dest_dir: Path) -> Path:
    """Bring in one public YouTube video by its id."""
    page = _fetch_youtube_page(video_id)
    player = _player_response(page)
    if player is None:
        raise TeachingLinkError(
            "YouTube did not give the app a playable copy of "
            "that video. " + _YOUTUBE_FALLBACK
        )
    plan = _youtube_plan(player)
    if plan[0] == "single":
        entry = plan[1]
        mime = str(entry.get("mimeType") or "")
        suffix = ".webm" if "webm" in mime else ".mp4"
        return _download(
            entry["url"],
            dest_dir / f"linked{suffix}",
            user_agent=_YOUTUBE_UA,
        )
    _kind, video_entry, audio_entry = plan
    video_part = dest_dir / ".youtube-video.part"
    audio_part = dest_dir / ".youtube-audio.part"
    try:
        _download(
            video_entry["url"], video_part, user_agent=_YOUTUBE_UA
        )
        _download(
            audio_entry["url"], audio_part, user_agent=_YOUTUBE_UA
        )
        return _merge_youtube_parts(
            video_part, audio_part, dest_dir / "linked.mp4"
        )
    finally:
        video_part.unlink(missing_ok=True)
        audio_part.unlink(missing_ok=True)


def direct_download_url(url: str) -> Optional[str]:
    """The file address behind a Drive or Dropbox share link.

    A Google Drive share page names no file the app can read, so
    the share link is turned into Drive's own download address
    for the same file. A Dropbox share link is served as the
    file itself by Dropbox's download host when asked with
    ``dl=1``. Anything else returns None and the caller falls
    back to reading the page. Only the pasted link's own file is
    ever addressed.
    """
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host in ("drive.google.com", "www.drive.google.com"):
        segments = [s for s in parts.path.split("/") if s]
        if len(segments) >= 3 and segments[0] == "file" and segments[1] == "d":
            return (
                "https://drive.google.com/uc?export=download&id="
                + urllib.parse.quote(segments[2])
            )
        query = urllib.parse.parse_qs(parts.query)
        file_id = (query.get("id") or [""])[0]
        if file_id:
            return (
                "https://drive.google.com/uc?export=download&id="
                + urllib.parse.quote(file_id)
            )
        return None
    if host in ("dropbox.com", "www.dropbox.com"):
        query = urllib.parse.parse_qs(parts.query)
        query["dl"] = ["1"]
        return urllib.parse.urlunsplit(
            (
                parts.scheme or "https",
                "dl.dropboxusercontent.com",
                parts.path,
                urllib.parse.urlencode(query, doseq=True),
                "",
            )
        )
    return None


def fetch_link(url: str, dest_dir: Path) -> Path:
    """Bring in the video at a pasted link; returns where it landed.

    A link straight to a video file is downloaded as it is. A
    YouTube link is read through the player's own data on its
    watch page (v0.17, feedback item 3). Any other link's page
    is read for the video file it points at (an og:video meta
    tag, or a source tag naming a video file), and that file is
    downloaded. Anything else raises TeachingLinkError. Only
    the pasted URL, and the one video file its own page names,
    are ever fetched.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
    except ValueError as exc:
        raise TeachingLinkError(
            "The app could not get that video from the link."
        ) from exc
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise TeachingLinkError(
            "The app could not get that video from the link."
        )
    video_id = _youtube_video_id(parts)
    if video_id:
        return _fetch_youtube(video_id, dest_dir)
    direct = direct_download_url(url)
    if direct is not None:
        return _download(direct, dest_dir / "linked.mp4")
    suffix = Path(parts.path).suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return _download(url, dest_dir / f"linked{suffix}")
    page = _fetch_page(url)
    candidate = _video_candidate(page, url)
    if not candidate:
        raise TeachingLinkError(
            "The app could not get that video from the link."
        )
    candidate_suffix = Path(
        urllib.parse.urlsplit(candidate).path
    ).suffix.lower()
    if candidate_suffix not in VIDEO_SUFFIXES:
        candidate_suffix = ".mp4"
    return _download(candidate, dest_dir / f"linked{candidate_suffix}")


# ---------------------------------------------------------------------------
# Framing: what the finished video's picture teaches
# ---------------------------------------------------------------------------


def shape_for_profile(profile: dict) -> Optional[str]:
    """The shape a profile's reference video is: "9:16" or "16:9".

    Each reference teaches the shape it is, so profiles are kept
    per shape and neither overwrites the other. The shape comes
    from the reference's own aspect: the nearer of the two shapes,
    measured in log space so a tall 4:5 still lands on 9:16. None
    when the profile carries no size to judge by.
    """
    if not isinstance(profile, dict):
        return None
    aspect = profile.get("source_aspect")
    if not isinstance(aspect, (int, float)) or aspect <= 0:
        width = profile.get("source_width")
        height = profile.get("source_height")
        if (
            isinstance(width, (int, float))
            and isinstance(height, (int, float))
            and width > 0
            and height > 0
        ):
            aspect = width / height
        else:
            return None
    import math

    return min(
        framing.ASPECTS,
        key=lambda shape: abs(
            math.log(aspect) - math.log(framing.ASPECTS[shape])
        ),
    )


def framing_path(session_dir: Path, shape: Optional[str] = None) -> Path:
    if shape in framing.ASPECTS:
        return Path(session_dir) / (
            "framing_profile_" + shape.replace(":", "x") + ".json"
        )
    return Path(session_dir) / FRAMING_FILENAME


def _read_framing_file(path: Path) -> Optional[dict]:
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        if isinstance(payload, dict) and isinstance(
            payload.get("profile"), dict
        ):
            return payload["profile"]
    return None


def save_framing_profile(session_dir: Path, profile: dict) -> None:
    """Keep one teaching run's framing profile, per shape.

    A 9:16 reference saves the 9:16 profile and a 16:9 reference
    saves the 16:9 one, side by side, so teaching one shape never
    overwrites the other. A profile with no size to judge by keeps
    the old single file name.
    """
    path = framing_path(session_dir, shape_for_profile(profile))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"profile": profile}, handle, indent=2)
        handle.write("\n")


def load_framing_profile(
    session_dir: Path, shape: Optional[str] = None
) -> Optional[dict]:
    """The saved framing profile, or None.

    With a shape, that shape's profile comes back: its own file
    first, then the old single file when that file's reference is
    the same shape (or carries no size). Without a shape, the old
    single file wins when it exists, else the newest per-shape
    file, so older callers keep working.
    """
    if shape in framing.ASPECTS:
        profile = _read_framing_file(framing_path(session_dir, shape))
        if profile is not None:
            return profile
        legacy = _read_framing_file(framing_path(session_dir))
        if legacy is not None and shape_for_profile(legacy) in (
            None,
            shape,
        ):
            return legacy
        return None
    legacy = _read_framing_file(framing_path(session_dir))
    if legacy is not None:
        return legacy
    candidates = []
    for known in framing.ASPECTS:
        path = framing_path(session_dir, known)
        if path.is_file():
            candidates.append(path)
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return _read_framing_file(newest)


def framing_draft_for(
    session_dir: Path,
    finished_path: Path,
    *,
    kind: str,
    basis: str,
    source_note: str,
    profile_fn=None,
) -> Optional[dict]:
    """Read a finished video's framing and draft the one line.

    Returns a draft in the usual shape carrying the plain
    framing sentence, and saves the profile with the session so
    a render can cut a new shape around the same face position.
    When no face can be read (none there, or this computer
    cannot spot faces), returns None and saves nothing: the
    teach summary never claims framing it did not see.
    ``profile_fn`` is the seam tests stub.
    """
    if profile_fn is None:
        # The reference is read with the one ruler, the same
        # read a rendered output is later checked with, so the
        # size comparison on Finish is honest (framing's
        # ruler_profile says why).
        profile_fn = framing.ruler_profile
    profile = profile_fn(Path(finished_path))
    if not isinstance(profile, dict) or profile.get("status") != "ok":
        return None
    sentence = framing.framing_sentence(profile)
    if not sentence:
        return None
    save_framing_profile(session_dir, profile)
    face = profile["face"]
    percent = int(round(float(face["width"]) * 100))
    return make_draft(
        statement=sentence,
        reason=(
            "From the finished video's picture: across "
            f"{profile.get('frames_with_face', 0)} sampled "
            "moments, your face takes about "
            f"{percent}% of the frame's width."
        ),
        at_s=None,
        quote="",
        basis=basis,
        kind=kind,
        source_note=source_note,
    )
