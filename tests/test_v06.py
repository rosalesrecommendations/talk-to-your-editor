"""Tests for v0.6.0: the fresh intake, direction at intake,
Meta dictation, and clean captions (stdlib unittest).

Unit tests drive the new modules directly. The HTTP tests spin the
real server with no job, drop a tiny real video through the real
intake endpoints, and run the first edit with the word route
stubbed at the speech module's seam, so the whole flow (baseline
plan, settings flip from spoken-style direction, engines, splice
render, captions) is exercised offline.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import captions as clean_captions
from talk_to_your_editor import direction_parse, intake_build
from talk_to_your_editor import transcribe as speech
from talk_to_your_editor import webapp
from talk_to_your_editor.webapp import WebAppConfig, create_server

FFMPEG = shutil.which("ffmpeg")


SCRIPT_SENTENCES = [
    "This video is about the game on my phone.",
    "I am going to delete the game today.",
    "I am going to delete the game today.",
    "I played it every day for seven years straight.",
    "It took my time and it took my focus.",
    "Then one morning I read a verse about pruning.",
    "And that's all.",
]


def canned_words():
    """The script above, laid out on a timeline sentence by sentence.

    The repeated sentence is long and plain, the shape the retake
    engine treats as a retake it may cut (Band A), and it is a
    small enough share of the whole that the safety verifier's
    too-much-at-once guard stays out of the way, as it should.
    """
    words = []
    cursor = 0.20
    for sentence in SCRIPT_SENTENCES:
        for token in sentence.split():
            words.append(
                {"word": token, "start": round(cursor, 3),
                 "end": round(cursor + 0.22, 3)}
            )
            cursor += 0.28
        cursor += 0.80
    return words


def small_words():
    """A hand-laid three-sentence script for the baseline tests."""
    rows = [
        ("I", 0.20, 0.35), ("am", 0.35, 0.55), ("going", 0.55, 0.85),
        ("to", 0.85, 1.00), ("delete", 1.00, 1.35), ("the", 1.35, 1.50),
        ("game", 1.50, 1.75), ("today.", 1.75, 2.05),
        ("I", 3.10, 3.25), ("am", 3.25, 3.45), ("going", 3.45, 3.75),
        ("to", 3.75, 3.90), ("delete", 3.90, 4.25), ("the", 4.25, 4.40),
        ("game", 4.40, 4.65), ("today.", 4.65, 4.95),
        ("And", 6.10, 6.25), ("that's", 6.25, 6.55), ("all.", 6.55, 6.90),
    ]
    return [
        {"word": w, "start": s, "end": e} for w, s, e in rows
    ]


def fake_local_result(wav_path, duration_s=None):
    words = canned_words()
    return {
        "words": words,
        "segments": speech.segments_from_words(words),
        "language": "en",
        "duration": duration_s or 22.0,
        "engine": "test local route",
    }


# ---------------------------------------------------------------------------
# Direction parsing
# ---------------------------------------------------------------------------


class DirectionParseTests(unittest.TestCase):
    def test_justins_example(self):
        parsed = direction_parse.parse_direction(
            "remove retakes, last take is the best take"
        )
        self.assertEqual(parsed["overrides"]["remove_retakes"], True)
        self.assertEqual(parsed["overrides"]["keep_mode"], "last")
        self.assertEqual(parsed["remainder"], "")

    def test_first_clean_take_flips_the_keeper(self):
        parsed = direction_parse.parse_direction(
            "keep the first clean take"
        )
        self.assertEqual(parsed["overrides"]["keep_mode"], "first")
        # "keep the first clean take" is not "keep all takes".
        self.assertNotIn("remove_retakes", parsed["overrides"])

    def test_negation_wins_over_the_plain_wording(self):
        parsed = direction_parse.parse_direction(
            "don't remove retakes on this one"
        )
        self.assertEqual(parsed["overrides"]["remove_retakes"], False)

    def test_best_delivery_is_beta(self):
        parsed = direction_parse.parse_direction(
            "use best delivery for the retakes"
        )
        self.assertEqual(parsed["overrides"]["keep_mode"], "best")
        self.assertTrue(
            any("Beta" in line for line in parsed["recap"])
        )

    def test_spoken_buffer_number(self):
        parsed = direction_parse.parse_direction(
            "cut silence to 0.2 seconds"
        )
        self.assertEqual(parsed["overrides"]["cut_silence"], True)
        self.assertAlmostEqual(parsed["overrides"]["buffer_s"], 0.2)

    def test_buffer_clamps_to_the_apps_range(self):
        parsed = direction_parse.parse_direction("a buffer of 0.9")
        self.assertAlmostEqual(parsed["overrides"]["buffer_s"], 0.5)

    def test_unmapped_words_ride_in_as_the_remainder(self):
        parsed = direction_parse.parse_direction(
            "remove retakes and make the middle part feel tighter"
        )
        self.assertEqual(parsed["overrides"]["remove_retakes"], True)
        self.assertIn("middle part feel tighter", parsed["remainder"])

    def test_empty_direction_maps_nothing(self):
        parsed = direction_parse.parse_direction("")
        self.assertEqual(parsed["overrides"], {})
        self.assertEqual(parsed["recap"], [])
        self.assertEqual(parsed["remainder"], "")


# ---------------------------------------------------------------------------
# Clean captions
# ---------------------------------------------------------------------------


class CaptionTests(unittest.TestCase):
    def test_filler_list_is_conservative(self):
        for word in ("um", "Um,", "uh", "uhh", "umm", "er", "ah",
                     "hmm", "uhm", "erm"):
            self.assertTrue(clean_captions.is_filler(word), word)
        for word in ("umbrella", "like", "so", "well", "ahem",
                     "the", "God", "am"):
            self.assertFalse(clean_captions.is_filler(word), word)

class CaptionBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-cap-"))
        words = [
            {"word": "Um,", "start": 0.0, "end": 0.3},
            {"word": "the", "start": 0.3, "end": 0.5},
            {"word": "game", "start": 0.5, "end": 0.9},
            {"word": "stays.", "start": 0.9, "end": 1.4},
            {"word": "Cut", "start": 2.0, "end": 2.3},
            {"word": "words.", "start": 2.3, "end": 2.8},
            {"word": "uh", "start": 3.5, "end": 3.8},
            {"word": "forever.", "start": 3.8, "end": 4.4},
        ]
        transcript = {
            "engine": "test", "source_file": "x.mp4",
            "language": "en", "duration": 5.0,
            "words": words, "segments": [],
        }
        (self.tmp / "transcript.json").write_text(
            json.dumps(transcript), encoding="utf-8"
        )
        self.before = (self.tmp / "transcript.json").read_bytes()
        # Keep everything except the middle range (the cut words).
        self.cuts = {
            "source_duration_s": 5.0,
            "output_duration_s": 3.3,
            "kept_ranges": [
                {"start": 0.0, "end": 1.5},
                {"start": 3.4, "end": 4.5},
            ],
            "join_pauses_s": [0.1],
            "frame_quantization": {"fps": 30, "ranges": 2,
                                   "repairs": 0},
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build(self):
        out = self.tmp / "clean-captions.vtt"
        info = clean_captions.build_captions(self.tmp, self.cuts, out)
        text = out.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("WEBVTT"))
        self.assertIn("the game stays.", text)
        self.assertIn("forever.", text)
        self.assertNotIn("Cut", text)
        self.assertNotIn("Um", text)
        self.assertEqual(info["hidden_fillers"], 2)
        # The transcript itself is never touched by this feature.
        self.assertEqual(
            (self.tmp / "transcript.json").read_bytes(), self.before
        )


# ---------------------------------------------------------------------------
# Speech helpers
# ---------------------------------------------------------------------------


def write_wav(path: Path, seconds: float, bursts=()):
    """A 16 kHz mono WAV: quiet, with loud bursts at (start, end)."""
    rate = 16000
    frames = bytearray()
    import math
    total = int(seconds * rate)
    loud = [False] * total
    for start, end in bursts:
        for i in range(int(start * rate), int(end * rate)):
            loud[i] = True
    for i in range(total):
        value = int(12000 * math.sin(2 * math.pi * 220 * i / rate)) \
            if loud[i] else 0
        frames += int(value).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))


class SpeechSpanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-vad-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_spans_follow_the_bursts(self):
        path = self.tmp / "a.wav"
        write_wav(path, 4.0, bursts=[(0.5, 1.5), (2.5, 3.2)])
        spans = speech.measure_speech_spans(path)
        self.assertEqual(len(spans), 2)
        self.assertAlmostEqual(spans[0]["start"], 0.5, delta=0.1)
        self.assertAlmostEqual(spans[0]["end"], 1.5, delta=0.1)
        self.assertAlmostEqual(spans[1]["start"], 2.5, delta=0.1)

    def test_quiet_file_has_no_speech(self):
        path = self.tmp / "q.wav"
        write_wav(path, 2.0)
        self.assertEqual(speech.measure_speech_spans(path), [])

    def test_segments_split_at_long_gaps(self):
        words = [
            {"word": "One.", "start": 0.0, "end": 0.4},
            {"word": "Two", "start": 2.0, "end": 2.3},
            {"word": "three.", "start": 2.3, "end": 2.8},
        ]
        segments = speech.segments_from_words(words)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[1]["text"], "Two three.")


class ScribeClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-scribe-"))
        self.wav = self.tmp / "a.wav"
        write_wav(self.wav, 5.0, bursts=[(0.2, 4.5)])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_words_come_back_filtered_and_timed(self):
        seen = {}

        def fake_post(url, body, headers, timeout=900):
            seen["url"] = url
            seen["key"] = headers.get("xi-api-key")
            seen["body"] = body
            return 200, {
                "language_code": "eng",
                "words": [
                    {"text": "Hello", "start": 0.2, "end": 0.6,
                     "type": "word"},
                    {"text": " ", "start": 0.6, "end": 0.7,
                     "type": "spacing"},
                    {"text": "um", "start": 0.7, "end": 0.9,
                     "type": "word"},
                    {"text": "world.", "start": 0.9, "end": 1.4,
                     "type": "word"},
                ],
            }

        result = speech.scribe_transcribe_wav(
            self.wav, "test-key", post=fake_post
        )
        self.assertEqual(seen["key"], "test-key")
        self.assertEqual(
            [w["word"] for w in result["words"]],
            ["Hello", "um", "world."],
        )
        self.assertAlmostEqual(result["words"][1]["start"], 0.7)

    def test_chunks_offset_their_words(self):
        calls = []

        def fake_post(url, body, headers, timeout=900):
            calls.append(body)
            return 200, {
                "language_code": "eng",
                "words": [
                    {"text": "piece", "start": 0.1, "end": 0.3,
                     "type": "word"},
                ],
            }

        with mock.patch.object(speech, "SCRIBE_CHUNK_S", 2.0):
            result = speech.scribe_transcribe_wav(
                self.wav, "k", duration_s=5.0, post=fake_post,
                work_dir=self.tmp / "work",
            )
        self.assertEqual(len(calls), 3)
        starts = [w["start"] for w in result["words"]]
        self.assertEqual(starts, [0.1, 2.1, 4.1])

    def test_rejection_is_a_plain_error(self):
        def fake_post(url, body, headers, timeout=900):
            return 401, {"detail": "bad key"}

        with self.assertRaises(speech.TranscribeError):
            speech.scribe_transcribe_wav(
                self.wav, "k", post=fake_post
            )


class MetaClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-meta-"))
        self.wav = self.tmp / "note.wav"
        write_wav(self.wav, 2.0, bursts=[(0.2, 1.5)])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_request_shape_and_text(self):
        seen = {}

        def fake_post(url, body, headers, timeout=900):
            seen["url"] = url
            seen["auth"] = headers.get("Authorization")
            seen["body"] = body
            return 200, {
                "transcript": "cut the long pause here",
                "audioDurationMs": 1900,
            }

        result = speech.meta_transcribe_wav(
            self.wav, "meta-key", post=fake_post
        )
        self.assertEqual(result["text"], "cut the long pause here")
        self.assertAlmostEqual(result["duration_s"], 1.9)
        self.assertEqual(seen["auth"], "Bearer meta-key")
        self.assertIn(b'"audioEncoding": "WAV"', seen["body"])
        self.assertIn(b'muse-voice-transcribe-1.0', seen["body"])
        self.assertIn(b'name="audio"', seen["body"])

    def test_ten_minute_cap_is_a_plain_refusal(self):
        with mock.patch.object(
            speech, "wav_duration", return_value=700.0
        ):
            with self.assertRaises(speech.TranscribeError):
                speech.meta_transcribe_wav(self.wav, "k")


class LocalClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-local-"))
        self.wav = self.tmp / "speech.wav"
        write_wav(self.wav, 1.0, bursts=[(0.1, 0.8)])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_waveform_samples_decode_the_apps_wav(self):
        samples = speech.waveform_samples(self.wav)
        self.assertEqual(len(samples), 16000)
        self.assertGreater(max(samples), 0.3)
        self.assertEqual(samples[0], 0.0)

    def test_model_gets_waveform_samples_not_a_file_path(self):
        # Regression for the fresh-install failure: passing a path
        # sends faster-whisper through PyAV to decode a WAV this app
        # made itself, and that decoder call broke. The model must
        # receive the waveform samples directly.
        seen = {}

        def fake_transcribe(audio, **kwargs):
            seen["audio"] = audio
            seen["kwargs"] = kwargs
            word = SimpleNamespace(word=" Hello ", start=0.1, end=0.4)
            segment = SimpleNamespace(words=[word])
            info = SimpleNamespace(language="en")
            return [segment], info

        model = SimpleNamespace(transcribe=fake_transcribe)
        with mock.patch.object(
            speech, "local_available", return_value=True
        ):
            result = speech.local_transcribe_wav(
                self.wav,
                duration_s=1.0,
                model_factory=lambda name: model,
            )
        self.assertNotIsInstance(seen["audio"], (str, Path))
        self.assertEqual(tuple(seen["audio"].shape), (16000,))
        self.assertTrue(seen["kwargs"]["word_timestamps"])
        self.assertFalse(seen["kwargs"]["vad_filter"])
        self.assertEqual(result["words"][0]["word"], "Hello")


# ---------------------------------------------------------------------------
# Baseline plan building
# ---------------------------------------------------------------------------


class BaselineTests(unittest.TestCase):
    def test_units_ranges_and_joins(self):
        words = small_words()
        spans = [
            {"start": 0.15, "end": 2.10},
            {"start": 3.05, "end": 5.00},
            {"start": 6.05, "end": 6.95},
        ]
        cuts = intake_build.build_baseline_cuts(words, spans, 8.0, 30)
        self.assertEqual(cuts["words_total"], 19)
        self.assertEqual(cuts["words_cut"], 0)
        self.assertEqual(len(cuts["kept_ranges"]), 3)
        self.assertEqual(len(cuts["join_pauses_s"]), 2)
        # Join = tail of one piece + lead-in of the next, the same
        # measure the silence tools use.
        self.assertAlmostEqual(
            cuts["join_pauses_s"][0], (2.10 - 2.05) + (3.10 - 3.05)
        )
        self.assertAlmostEqual(
            cuts["output_duration_s"],
            sum(
                r["end"] - r["start"] for r in cuts["kept_ranges"]
            ),
        )
        # Every word sits inside some kept range.
        for word in words:
            mid = (word["start"] + word["end"]) / 2
            self.assertTrue(
                any(
                    r["start"] <= mid <= r["end"]
                    for r in cuts["kept_ranges"]
                )
            )

    def test_no_spans_falls_back_to_small_margins(self):
        cuts = intake_build.build_baseline_cuts(
            small_words(), [], 8.0, None
        )
        self.assertEqual(len(cuts["kept_ranges"]), 3)
        self.assertAlmostEqual(cuts["join_pauses_s"][0], 0.10)


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class SpliceTests(unittest.TestCase):
    def test_splice_keeps_only_the_kept_pieces(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-splice-"))
        try:
            source = tmp / "src.mp4"
            subprocess.run(
                [
                    FFMPEG, "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30",
                    "-f", "lavfi", "-i", "sine=frequency=440",
                    "-t", "4", str(source),
                ],
                check=True,
            )
            cuts = {
                "kept_ranges": [
                    {"start": 0.0, "end": 1.0},
                    {"start": 2.5, "end": 3.5},
                ]
            }
            out = tmp / "out.mp4"
            intake_build.render_splice(source, cuts, out)
            info = speech.probe_media(out)
            self.assertAlmostEqual(info["duration"], 2.0, delta=0.25)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# HTTP: drop a video, make the first edit, dictate
# ---------------------------------------------------------------------------


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class FirstEditHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = Path(tempfile.mkdtemp(prefix="tye-v06-fix-"))
        cls.video = cls.fixture_dir / "raw take.mp4"
        subprocess.run(
            [
                FFMPEG, "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30",
                "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", "22", str(cls.video),
            ],
            check=True,
        )
        cls.video_bytes = cls.video.read_bytes()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fixture_dir, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v06-"))
        self.launch = self.tmp / "launch home"
        self.launch.mkdir()
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("ELEVENLABS_API_KEY", None)
        os.environ.pop("MODEL_API_KEY", None)
        self.server = create_server(
            WebAppConfig(None, launch_home=self.launch),
            host="127.0.0.1",
            port=0,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def request(self, path, data=None, raw=None, headers=None,
                method=None):
        hdrs = dict(headers or {})
        payload = raw
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path,
            data=payload,
            headers=hdrs,
            method=method or ("POST" if payload is not None else "GET"),
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def post_json(self, path, data):
        status, body = self.request(path, data=data)
        return status, json.loads(body)

    def get_json(self, path):
        status, body = self.request(path)
        return status, json.loads(body)

    def drop_video(self):
        status, begun = self.post_json(
            "/api/intake/begin", {"name": "raw take"}
        )
        self.assertEqual(status, 200)
        intake_id = begun["intake_id"]
        status, _ = self.request(
            f"/api/intake/file?intake={intake_id}"
            "&path=raw%20take.mp4",
            raw=self.video_bytes,
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(status, 200)
        status, finished = self.post_json(
            "/api/intake/finish", {"intake_id": intake_id}
        )
        self.assertEqual(status, 200)
        self.assertEqual(finished["kind"], "video")
        self.assertTrue(finished["can_first_edit"])
        return intake_id

    def wait_first_edit(self, intake_id, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, state = self.get_json(
                f"/api/intake/status?intake={intake_id}"
            )
            if state.get("status") in ("done", "failed", "no_words"):
                return state
            time.sleep(0.2)
        self.fail("first edit did not finish in time")

    def test_first_edit_end_to_end_with_direction(self):
        intake_id = self.drop_video()
        with mock.patch.object(
            speech, "local_available", return_value=True
        ), mock.patch.object(
            speech, "local_transcribe_wav",
            side_effect=fake_local_result,
        ):
            status, started = self.post_json(
                "/api/intake/first-edit",
                {
                    "intake_id": intake_id,
                    "direction": "keep the first clean take",
                    "clean_captions": True,
                },
            )
            self.assertEqual(status, 200)
            self.assertTrue(started["started"])
            state = self.wait_first_edit(intake_id)
        self.assertEqual(state["status"], "done", state)
        # The direction flipped the keeper from the saved default
        # (last take) and the recap names the change.
        self.assertEqual(state["settings"]["keep_mode"], "first")
        self.assertTrue(
            any(
                "first clean take" in line
                for line in state["recap"]
            ),
            state["recap"],
        )
        # Fifty-five words went in; under first-take rules the
        # second saying of the sentence (eight words) is cut and
        # forty-seven stay.
        self.assertEqual(state["kept_before"], 55)
        self.assertEqual(state["kept_after"], 47)
        # The job is live, with a transcript that keeps everything.
        _, summary = self.get_json("/api/job")
        self.assertEqual(summary["words_total"], 55)
        job_dir = self.server.config.job_dir
        transcript = json.loads(
            (job_dir / "transcript.json").read_text()
        )
        self.assertEqual(len(transcript["words"]), 55)
        # The dropped file itself was never modified.
        staged = job_dir / "raw take.mp4"
        self.assertEqual(staged.read_bytes(), self.video_bytes)
        # Clean captions exist, hold the kept words, and are served.
        status, body = self.request("/api/captions")
        self.assertEqual(status, 200)
        vtt = body.decode("utf-8")
        self.assertIn("I am going to delete the game today.", vtt)
        self.assertIn("And that's all.", vtt)
        # The splice render produced a watchable review video whose
        # length is the kept pieces and nothing else.
        review = self.server.config.session_dir / "revised-review.mp4"
        self.assertTrue(review.is_file())
        info = speech.probe_media(review)
        self.assertAlmostEqual(info["duration"], 13.4, delta=0.5)

    def test_no_words_is_a_plain_state_not_an_error(self):
        intake_id = self.drop_video()

        def silent_result(wav_path, duration_s=None):
            return {
                "words": [], "segments": [], "language": "en",
                "duration": duration_s or 6.0, "engine": "test",
            }

        with mock.patch.object(
            speech, "local_available", return_value=True
        ), mock.patch.object(
            speech, "local_transcribe_wav", side_effect=silent_result
        ):
            self.post_json(
                "/api/intake/first-edit", {"intake_id": intake_id}
            )
            state = self.wait_first_edit(intake_id)
        self.assertEqual(state["status"], "no_words")
        self.assertIn("No words were found", state["message"])
        _, summary = self.get_json("/api/job")
        self.assertFalse(summary["has_job"])

    def test_no_word_route_is_a_plain_refusal(self):
        intake_id = self.drop_video()
        with mock.patch.object(
            speech, "local_available", return_value=False
        ):
            self.post_json(
                "/api/intake/first-edit", {"intake_id": intake_id}
            )
            state = self.wait_first_edit(intake_id)
        self.assertEqual(state["status"], "failed")
        self.assertIn("speech pack", state["message"])

    def test_dictate_needs_the_signin_first(self):
        status, payload = self.post_json_raw_dictate()
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["message"], webapp.DICTATE_NO_KEY_MESSAGE
        )

    def post_json_raw_dictate(self):
        status, body = self.request(
            "/api/dictate",
            raw=b"fake-webm-bytes",
            headers={"Content-Type": "audio/webm"},
        )
        return status, json.loads(body)

    def test_dictate_returns_words_for_the_box(self):
        os.environ["MODEL_API_KEY"] = "test-key"

        def fake_meta(wav_path, key):
            return {"text": "cut the long pause", "duration_s": 1.5,
                    "engine": "test"}

        with mock.patch.object(
            speech, "meta_transcribe_wav", side_effect=fake_meta
        ), mock.patch.object(
            speech, "extract_audio", return_value=1.5
        ):
            status, payload = self.post_json_raw_dictate()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["text"], "cut the long pause")

    def test_dictate_with_a_real_wav_clip(self):
        # Regression: a clip that already arrives as WAV must not
        # be overwritten by its own conversion (the raw file and
        # the converted file need different names).
        os.environ["MODEL_API_KEY"] = "test-key"
        wav_path = self.tmp / "note.wav"
        write_wav(wav_path, 2.0, bursts=[(0.2, 1.5)])

        def fake_meta(path, key):
            # The conversion really ran: a real WAV sits here.
            self.assertTrue(Path(path).is_file())
            self.assertGreater(Path(path).stat().st_size, 1000)
            return {"text": "leave the first take",
                    "duration_s": 2.0, "engine": "test"}

        with mock.patch.object(
            speech, "meta_transcribe_wav", side_effect=fake_meta
        ):
            status, body = self.request(
                "/api/dictate",
                raw=wav_path.read_bytes(),
                headers={"Content-Type": "audio/wav"},
            )
        payload = json.loads(body)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["text"], "leave the first take")


if __name__ == "__main__":
    unittest.main()
