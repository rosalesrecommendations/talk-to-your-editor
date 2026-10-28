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

from . import transcribe as speech

TEACHING_DIR_NAME = "teaching"
DRAFTS_FILENAME = "teaching_drafts.json"
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


def _download(url: str, dest: Path) -> Path:
    """Download one URL to ``dest``, under the size cap."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "TalkToYourEditor"}
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


def fetch_link(url: str, dest_dir: Path) -> Path:
    """Bring in the video at a pasted link; returns where it landed.

    A link straight to a video file is downloaded as it is. Any
    other link's page is read for the video file it points at
    (an og:video meta tag, or a source tag naming a video file),
    and that file is downloaded. Anything else raises
    TeachingLinkError. Only the pasted URL, and the one video file
    its own page names, are ever fetched.
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
