"""The shared speech pipe: audio in, words or text out.

One module serves both speech jobs in this app:

- **Intake** needs every word with its start and end time, filler
  words included, because the editing engines cut on those timings.
  Two routes can do that: ElevenLabs Scribe (a hosted service, when
  the creator's ElevenLabs key is set) and a Whisper model that runs
  on this computer (the speech pack). Meta's speech-to-text is not
  used here: it returns turn timings only, never word timings, so
  no edit could be measured from it.
- **Dictation** needs clean text for a short spoken note, fast.
  That is Meta's Muse Voice Transcribe through the Model API, the
  same key the direction features already use.

Silence is measured from the audio itself, never from a
transcript's habits: ``measure_speech_spans`` reads the waveform's
energy and reports where speech actually is, and the intake builder
aligns those spans with the words when it lays out the first plan.

Everything external is reached through small seams (``_post``,
``_model_factory``) so tests can drive the whole module offline.
"""

from __future__ import annotations

import array
import json
import math
import os
import subprocess
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Callable, Optional

SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"
SCRIBE_MODEL = "scribe_v2"
META_ASR_URL = "https://api.meta.ai/v1/asr/transcribe"
META_ASR_MODEL = "muse-voice-transcribe-1.0"
META_ASR_MAX_S = 590.0  # the service takes at most 10 minutes at once
SCRIBE_CHUNK_S = 600.0
DEFAULT_LOCAL_MODEL = "base"
SAMPLE_RATE = 16000


class TranscribeError(RuntimeError):
    """A speech failure whose message is already plain words."""


# ---------------------------------------------------------------------------
# Audio plumbing (ffmpeg, which the app already leans on for playback)
# ---------------------------------------------------------------------------


def _run_ffmpeg(args: list, timeout: int = 1800) -> None:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise TranscribeError(
            "ffmpeg was not found on this computer, so the sound "
            "could not be taken out of the video."
        )
    except subprocess.TimeoutExpired:
        raise TranscribeError(
            "Working with that video took too long, so it was "
            "stopped. Nothing was changed."
        )
    if proc.returncode != 0:
        raise TranscribeError(
            "The sound could not be read from that file. It may be "
            "in a format this app cannot open."
        )


def extract_audio(source: Path, dest_wav: Path,
                  sample_rate: int = SAMPLE_RATE) -> float:
    """Pull a video or audio file's sound into a mono PCM WAV.

    Returns the WAV's duration in seconds, measured from the file
    itself. The source is only read, never changed.
    """
    dest_wav.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-i", str(source),
            "-vn", "-ac", "1", "-ar", str(sample_rate),
            "-sample_fmt", "s16", str(dest_wav),
        ]
    )
    return wav_duration(dest_wav)


def wav_duration(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def probe_media(path: Path) -> dict:
    """Shape facts about a media file, from ffprobe.

    Returns duration, the first video stream's width, height, frame
    rate and codec, and whether any audio stream exists. Missing
    facts come back as None rather than raising: a file ffprobe
    cannot read at all raises TranscribeError.
    """
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_streams",
                "-show_format", "-of", "json", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        raise TranscribeError(
            "ffprobe was not found on this computer, so that file "
            "could not be checked."
        )
    if proc.returncode != 0:
        raise TranscribeError(
            "That file could not be read as a video or a sound file."
        )
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError:
        raise TranscribeError(
            "That file could not be read as a video or a sound file."
        )
    info = {
        "duration": None,
        "width": None,
        "height": None,
        "fps": None,
        "video_codec": None,
        "has_audio": False,
    }
    fmt = payload.get("format") or {}
    if fmt.get("duration") is not None:
        try:
            info["duration"] = float(fmt["duration"])
        except (TypeError, ValueError):
            pass
    for stream in payload.get("streams") or []:
        if stream.get("codec_type") == "audio":
            info["has_audio"] = True
        elif stream.get("codec_type") == "video" and info["width"] is None:
            info["width"] = stream.get("width")
            info["height"] = stream.get("height")
            info["video_codec"] = stream.get("codec_name")
            rate = str(stream.get("avg_frame_rate") or "")
            if "/" in rate:
                num, den = rate.split("/", 1)
                try:
                    if float(den):
                        info["fps"] = round(float(num) / float(den), 3)
                except ValueError:
                    pass
            if info["duration"] is None and stream.get("duration"):
                try:
                    info["duration"] = float(stream["duration"])
                except (TypeError, ValueError):
                    pass
    return info


# ---------------------------------------------------------------------------
# Silence measured from the waveform itself
# ---------------------------------------------------------------------------


def measure_speech_spans(
    wav_path: Path,
    *,
    frame_s: float = 0.01,
    min_silence_s: float = 0.30,
    min_speech_s: float = 0.09,
    pad_s: float = 0.03,
) -> list:
    """Speech spans in a mono PCM WAV, measured from its energy.

    The threshold adapts to the recording: a quiet room and a loud
    one both work, because the line is drawn between this file's own
    quiet floor and its speech. Short gaps inside a phrase are
    bridged, blips too short to be a word are dropped, and each span
    is padded a little so word edges are never clipped. Returns a
    list of {"start", "end"} dicts in seconds, sorted and
    non-overlapping. An all-quiet file returns an empty list.
    """
    with wave.open(str(wav_path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    if width != 2 or not raw:
        return []
    samples = array.array("h")
    samples.frombytes(raw)
    if channels > 1:
        samples = samples[::channels]
    frame = max(1, int(rate * frame_s))
    count = len(samples) // frame
    if count == 0:
        return []
    energies = []
    for i in range(count):
        chunk = samples[i * frame:(i + 1) * frame]
        energies.append(
            math.sqrt(sum(v * v for v in chunk) / len(chunk))
        )
    ordered = sorted(energies)
    floor = ordered[int(0.2 * (len(ordered) - 1))]
    peak = ordered[int(0.98 * (len(ordered) - 1))]
    if peak <= 0:
        return []
    threshold = max(floor * 3.0, peak * 0.04, 12.0)
    voiced = [e > threshold for e in energies]
    # Bridge silences shorter than min_silence_s inside speech.
    bridge = max(1, int(min_silence_s / frame_s))
    i = 0
    while i < count:
        if not voiced[i]:
            j = i
            while j < count and not voiced[j]:
                j += 1
            if 0 < i and j < count and (j - i) <= bridge:
                for k in range(i, j):
                    voiced[k] = True
            i = j
        else:
            i += 1
    spans = []
    i = 0
    min_frames = max(1, int(min_speech_s / frame_s))
    while i < count:
        if voiced[i]:
            j = i
            while j < count and voiced[j]:
                j += 1
            if (j - i) >= min_frames:
                start = max(0.0, i * frame_s - pad_s)
                end = min(count * frame_s, j * frame_s + pad_s)
                if spans and start < spans[-1]["end"]:
                    start = spans[-1]["end"]
                if end > start:
                    spans.append(
                        {"start": round(start, 4), "end": round(end, 4)}
                    )
            i = j
        else:
            i += 1
    return spans


# ---------------------------------------------------------------------------
# HTTP seam
# ---------------------------------------------------------------------------


def _post(url: str, body: bytes, headers: dict,
          timeout: int = 900) -> tuple:
    """One POST; returns (status, parsed JSON or raw text).

    Redirects are refused on authenticated requests, matching the
    rest of the app's provider calls.
    """

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            raise TranscribeError(
                "The speech service sent the request somewhere "
                "unexpected, so it was not followed."
            )

    request = urllib.request.Request(
        url, data=body, headers=headers, method="POST"
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", "replace")
        status = err.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


def _multipart(parts: list) -> tuple:
    """Build a multipart/form-data body from (name, filename, ctype, bytes)."""
    boundary = "----tye" + uuid.uuid4().hex
    out = []
    for name, filename, ctype, data in parts:
        head = (
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="{name}"'
        )
        if filename:
            head += f'; filename="{filename}"'
        head += "\r\n"
        if ctype:
            head += f"Content-Type: {ctype}\r\n"
        head += "\r\n"
        out.append(head.encode("utf-8"))
        out.append(data)
        out.append(b"\r\n")
    out.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


def _service_error(payload, service: str) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if isinstance(payload.get("detail"), str):
            return payload["detail"]
        if isinstance(payload.get("message"), str):
            return payload["message"]
    return f"{service} could not take that recording."


# ---------------------------------------------------------------------------
# Word routes (intake)
# ---------------------------------------------------------------------------


def segments_from_words(words: list) -> list:
    """Group timed words into utterance segments for the transcript.

    A new segment starts at a silence longer than about a second,
    or after a sentence-ending word followed by a real pause. The
    segments are a reading aid; every edit is measured from the
    words themselves.
    """
    segments = []
    current: list = []
    for index, word in enumerate(words):
        if current:
            prev = words[index - 1]
            gap = float(word["start"]) - float(prev["end"])
            ended = str(prev["word"]).rstrip().endswith(
                (".", "?", "!")
            )
            if gap > 1.0 or (ended and gap > 0.55):
                segments.append(_segment_of(current))
                current = []
        current.append(word)
    if current:
        segments.append(_segment_of(current))
    return segments


def _segment_of(words: list) -> dict:
    return {
        "start": float(words[0]["start"]),
        "end": float(words[-1]["end"]),
        "text": " ".join(str(w["word"]) for w in words),
    }


def scribe_transcribe_wav(
    wav_path: Path,
    api_key: str,
    *,
    duration_s: Optional[float] = None,
    post: Optional[Callable] = None,
    work_dir: Optional[Path] = None,
) -> dict:
    """Words with timings from ElevenLabs Scribe.

    Scribe is asked for word timings and nothing else: no speaker
    labels, no sound-event tags, and its wording is verbatim, so
    fillers and false starts arrive as words the engines can cut.
    Long audio is sent in chunks of ``SCRIBE_CHUNK_S`` and the word
    times are shifted back onto the full timeline.
    """
    post = post or _post
    if duration_s is None:
        duration_s = wav_duration(wav_path)
    chunks = []
    if duration_s <= SCRIBE_CHUNK_S:
        chunks.append((0.0, wav_path))
    else:
        if work_dir is None:
            raise TranscribeError(
                "That video is long, and there is nowhere to stage "
                "its sound in pieces."
            )
        work_dir.mkdir(parents=True, exist_ok=True)
        start = 0.0
        index = 0
        while start < duration_s:
            piece = work_dir / f"scribe-piece-{index}.wav"
            _run_ffmpeg(
                [
                    "-ss", f"{start:.3f}", "-t", f"{SCRIBE_CHUNK_S:.0f}",
                    "-i", str(wav_path), "-ac", "1", "-ar",
                    str(SAMPLE_RATE), "-sample_fmt", "s16", str(piece),
                ]
            )
            chunks.append((start, piece))
            start += SCRIBE_CHUNK_S
            index += 1
    words: list = []
    language = None
    for offset, piece in chunks:
        body, ctype = _multipart(
            [
                ("model_id", None, None, SCRIBE_MODEL.encode()),
                ("timestamps_granularity", None, None, b"word"),
                ("diarize", None, None, b"false"),
                ("tag_audio_events", None, None, b"false"),
                (
                    "file", piece.name, "audio/wav",
                    piece.read_bytes(),
                ),
            ]
        )
        status, payload = post(
            SCRIBE_URL, body,
            {"Content-Type": ctype, "xi-api-key": api_key},
        )
        if status != 200 or not isinstance(payload, dict):
            raise TranscribeError(
                "ElevenLabs Scribe did not take that recording: "
                + _service_error(payload, "ElevenLabs Scribe")
            )
        language = payload.get("language_code") or language
        for entry in payload.get("words") or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") not in (None, "word"):
                continue
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            words.append(
                {
                    "word": text,
                    "start": round(float(entry["start"]) + offset, 4),
                    "end": round(float(entry["end"]) + offset, 4),
                }
            )
    words.sort(key=lambda w: (w["start"], w["end"]))
    return {
        "words": words,
        "segments": segments_from_words(words),
        "language": language or "en",
        "duration": duration_s,
        "engine": "ElevenLabs Scribe v2",
    }


def local_available() -> bool:
    """Whether the on-this-computer speech pack is installed."""
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return True


def local_model_name() -> str:
    return os.environ.get("TYE_WHISPER_MODEL") or DEFAULT_LOCAL_MODEL


def _model_factory(model_size: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device="cpu", compute_type="int8")


def waveform_samples(wav_path: Path) -> array.array:
    """Mono PCM samples from the app's own 16 kHz WAV, as floats.

    Intake always hands this module the WAV that ``extract_audio``
    made, so its shape is known: mono, 16-bit, 16 kHz. Reading it
    here keeps the local model away from third-party media decoding,
    whose interfaces have changed under otherwise compatible speech
    packages before.
    """
    with wave.open(str(wav_path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())
    if width != 2 or not raw:
        raise TranscribeError(
            "The sound taken from the video was not in the shape "
            "the on-this-computer speech pack expects."
        )
    samples = array.array("h")
    samples.frombytes(raw)
    if channels > 1:
        samples = samples[::channels]
    return array.array("f", (value / 32768.0 for value in samples))


def local_transcribe_wav(
    wav_path: Path,
    *,
    duration_s: Optional[float] = None,
    model_factory: Optional[Callable] = None,
) -> dict:
    """Words with timings from a Whisper model on this computer.

    The model is downloaded once, the first time, by the library
    itself; after that this route needs no connection and costs
    nothing per video. Voice-activity filtering stays off and the
    wording is taken as it comes, so quiet fillers are not cleaned
    away before the engines can see them.
    """
    if not local_available():
        raise TranscribeError(
            "The speech pack that runs on this computer is not "
            "installed in this copy of the app, so it cannot write "
            "down the words itself."
        )
    factory = model_factory or _model_factory
    model = factory(local_model_name())
    # Feed the model the waveform itself, decoded above with the
    # standard library, rather than a file path. A path would send
    # faster-whisper through PyAV just to decode a WAV this app made,
    # and that decoder call is the one that broke in a fresh install.
    try:
        import numpy as np

        audio = np.asarray(waveform_samples(wav_path), dtype=np.float32)
    except TranscribeError:
        raise
    except Exception as exc:
        raise TranscribeError(
            "The sound taken from the video could not be prepared "
            "for the on-this-computer speech pack."
        ) from exc
    segments_iter, info = model.transcribe(
        audio,
        word_timestamps=True,
        vad_filter=False,
    )
    words: list = []
    for segment in segments_iter:
        for item in getattr(segment, "words", None) or []:
            text = str(item.word).strip()
            if not text:
                continue
            words.append(
                {
                    "word": text,
                    "start": round(float(item.start), 4),
                    "end": round(float(item.end), 4),
                }
            )
    words.sort(key=lambda w: (w["start"], w["end"]))
    if duration_s is None:
        duration_s = wav_duration(wav_path)
    return {
        "words": words,
        "segments": segments_from_words(words),
        "language": getattr(info, "language", None) or "en",
        "duration": duration_s,
        "engine": f"Whisper {local_model_name()} on this computer",
    }


# ---------------------------------------------------------------------------
# Text route (dictation)
# ---------------------------------------------------------------------------


def meta_transcribe_wav(
    wav_path: Path,
    api_key: str,
    *,
    sample_rate: int = SAMPLE_RATE,
    post: Optional[Callable] = None,
) -> dict:
    """Clean text for a short recording, from Muse Voice Transcribe.

    The file route takes mono PCM WAV only, in two parts: a JSON
    ``request`` part naming the model and the encoding, and the
    ``audio`` part itself. The answer carries the transcript text
    and turn timings; dictation needs the text alone.
    """
    post = post or _post
    duration_s = wav_duration(wav_path)
    if duration_s > META_ASR_MAX_S:
        raise TranscribeError(
            "That recording is longer than the voice service takes "
            "at once (10 minutes). Type the note, or say it in "
            "shorter pieces."
        )
    request_part = json.dumps(
        {
            "model": META_ASR_MODEL,
            "audioEncoding": "WAV",
            "sampleRateHertz": sample_rate,
        }
    ).encode("utf-8")
    body, ctype = _multipart(
        [
            ("request", None, "application/json", request_part),
            ("audio", wav_path.name, "audio/wav", wav_path.read_bytes()),
        ]
    )
    status, payload = post(
        META_ASR_URL, body,
        {"Content-Type": ctype, "Authorization": f"Bearer {api_key}"},
        timeout=300,
    )
    if status != 200 or not isinstance(payload, dict):
        raise TranscribeError(
            "The voice service did not take that recording: "
            + _service_error(payload, "The voice service")
        )
    text = str(payload.get("transcript") or "").strip()
    heard_ms = payload.get("audioDurationMs")
    return {
        "text": text,
        "duration_s": (
            float(heard_ms) / 1000.0
            if isinstance(heard_ms, (int, float))
            else duration_s
        ),
        "engine": "Muse Voice Transcribe",
    }
