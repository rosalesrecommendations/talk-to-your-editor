"""Tests for v0.11.0 fix item 5: saved transcripts (stdlib unittest).

A video written down once is saved on this Mac, under
``~/.talk-to-your-editor/saved-transcripts/``, known by its size
and a hash of the whole file. Bringing the same video in again
reuses the saved words instead of paying ElevenLabs again; a
different video under the same name does not reuse; identity
that cannot be settled falls back to a fresh transcription and
says nothing extra.

Everything here is offline. The Scribe provider is a mock at the
speech module's seam, and it must stay uncalled on a reuse run:
spend is $0, and no live ElevenLabs or model call happens.
"""

from __future__ import annotations

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
from pathlib import Path
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import saved_transcripts as saved
from talk_to_your_editor import transcribe as speech
from talk_to_your_editor import webapp
from talk_to_your_editor.webapp import WebAppConfig, create_server

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
)
FFMPEG = shutil.which("ffmpeg")

SCRIBE_ENGINE = "ElevenLabs Scribe v2"
LOCAL_ENGINE = "Whisper base on this computer"


def make_result(engine=SCRIBE_ENGINE):
    words = [
        {"word": "hello", "start": 0.2, "end": 0.45},
        {"word": "world", "start": 0.5, "end": 0.75},
        {"word": "again.", "start": 0.8, "end": 1.1},
    ]
    return {
        "words": words,
        "segments": speech.segments_from_words(words),
        "language": "en",
        "duration": 1.4,
        "engine": engine,
    }


class IsolatedHomeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v11-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("ELEVENLABS_API_KEY", None)
        os.environ.pop("MODEL_API_KEY", None)

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# The store itself
# ---------------------------------------------------------------------------


class StoreTests(IsolatedHomeCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = self.tmp / "store"
        self.video = self.tmp / "first take.mp4"
        self.video.write_bytes(b"fake-video-bytes-" * 64)

    def test_default_store_lives_under_the_per_user_folder(self):
        self.assertEqual(
            saved.store_dir(),
            self.home / ".talk-to-your-editor" / "saved-transcripts",
        )

    def test_first_run_saves_and_the_copy_loads_in_the_fresh_shape(self):
        identity = saved.identity_for_file(self.video)
        self.assertIsNotNone(identity)
        self.assertIsNone(saved.load_saved(identity, self.store))
        self.assertTrue(
            saved.save_transcript(identity, make_result(), self.store)
        )
        self.assertEqual(saved.count_saved(self.store), 1)
        loaded = saved.load_saved(identity, self.store)
        self.assertIsNotNone(loaded)
        fresh = make_result()
        self.assertEqual(loaded["words"], fresh["words"])
        self.assertEqual(loaded["segments"], fresh["segments"])
        self.assertEqual(loaded["engine"], SCRIBE_ENGINE)
        self.assertEqual(loaded["language"], "en")
        self.assertEqual(loaded["duration"], fresh["duration"])
        # The stored file itself names the engine that made it.
        stored = json.loads(
            next(self.store.glob("*.json")).read_text(encoding="utf-8")
        )
        self.assertEqual(stored["engine"], SCRIBE_ENGINE)
        self.assertEqual(stored["identity"], identity)

    def test_same_video_under_a_new_name_reuses(self):
        renamed = self.tmp / "a completely different name.mov"
        renamed.write_bytes(self.video.read_bytes())
        first = saved.identity_for_file(self.video)
        second = saved.identity_for_file(renamed)
        self.assertEqual(first, second)
        saved.save_transcript(first, make_result(), self.store)
        loaded = saved.load_saved(second, self.store)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["words"], make_result()["words"])

    def test_same_name_with_different_content_does_not_reuse(self):
        identity = saved.identity_for_file(self.video)
        saved.save_transcript(identity, make_result(), self.store)
        # The same file name, carrying different content.
        self.video.write_bytes(b"a-different-video-entirely" * 32)
        other = saved.identity_for_file(self.video)
        self.assertNotEqual(identity, other)
        self.assertIsNone(saved.load_saved(other, self.store))

    def test_saved_engine_is_labeled_and_the_recap_lines_name_it(self):
        scribe_identity = saved.identity_for_file(self.video)
        saved.save_transcript(
            scribe_identity, make_result(SCRIBE_ENGINE), self.store
        )
        loaded = saved.load_saved(scribe_identity, self.store)
        self.assertEqual(loaded["engine"], SCRIBE_ENGINE)
        self.assertEqual(
            saved.reuse_recap_line(loaded["engine"]),
            "Used the transcript saved from the earlier run of "
            "this video. No new transcription cost.",
        )
        other = self.tmp / "local.mp4"
        other.write_bytes(b"local-video" * 48)
        local_identity = saved.identity_for_file(other)
        saved.save_transcript(
            local_identity, make_result(LOCAL_ENGINE), self.store
        )
        loaded_local = saved.load_saved(local_identity, self.store)
        self.assertEqual(loaded_local["engine"], LOCAL_ENGINE)
        line = saved.reuse_recap_line(loaded_local["engine"])
        self.assertIn("Whisper", line)
        self.assertEqual(
            line,
            "Used the transcript saved from the earlier run of "
            "this video. It was made with Whisper on this "
            "computer. No new transcription cost.",
        )
        for text in (line, saved.SCRIBE_REUSE_LINE):
            self.assertNotIn("\u2014", text)

    def test_clearing_the_store_empties_it(self):
        first = saved.identity_for_file(self.video)
        saved.save_transcript(first, make_result(), self.store)
        other = self.tmp / "other.mp4"
        other.write_bytes(b"other-content" * 40)
        saved.save_transcript(
            saved.identity_for_file(other), make_result(), self.store
        )
        self.assertEqual(saved.count_saved(self.store), 2)
        self.assertEqual(saved.clear_saved(self.store), 2)
        self.assertEqual(saved.count_saved(self.store), 0)
        self.assertIsNone(saved.load_saved(first, self.store))

    def test_uncertain_identity_falls_back_silently(self):
        # A file that cannot be read has no identity at all.
        self.assertIsNone(saved.identity_for_file(self.tmp / "missing.mp4"))
        self.assertIsNone(saved.load_saved(None, self.store))
        self.assertIsNone(
            saved.load_saved({"size": "big", "sha256": "x"}, self.store)
        )
        # A damaged saved copy reads as no saved copy.
        identity = saved.identity_for_file(self.video)
        saved.save_transcript(identity, make_result(), self.store)
        entry = next(self.store.glob("*.json"))
        entry.write_text("this is not json", encoding="utf-8")
        self.assertIsNone(saved.load_saved(identity, self.store))
        # A saved copy whose stored identity was tampered with
        # does not match the video in hand.
        saved.save_transcript(identity, make_result(), self.store)
        payload = json.loads(entry.read_text(encoding="utf-8"))
        payload["identity"] = {"size": 3, "sha256": "0" * 64}
        entry.write_text(json.dumps(payload), encoding="utf-8")
        self.assertIsNone(saved.load_saved(identity, self.store))
        # Words that are not in shape are never saved.
        bad = make_result()
        bad["words"] = [{"word": "no times here"}]
        self.assertFalse(saved.save_transcript(identity, bad, self.store))


class CostTests(unittest.TestCase):
    def test_a_reused_transcript_records_no_new_cost(self):
        entries = webapp._first_edit_cost_entries(
            "scribe", 600.0, {}, None, None, reused=True
        )
        self.assertEqual(entries[0]["feature"], "transcription")
        self.assertEqual(entries[0]["usd"], 0.0)
        fresh = webapp._first_edit_cost_entries(
            "scribe", 600.0, {}, None, None
        )
        self.assertGreater(fresh[0]["usd"], 0.0)


# ---------------------------------------------------------------------------
# Settings: the count, and clearing from the Settings surface
# ---------------------------------------------------------------------------


class SettingsClearTests(IsolatedHomeCase):
    def setUp(self) -> None:
        super().setUp()
        self.job = self.tmp / "launch-job"
        self.job.mkdir()
        for name, target in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / name, self.job / target)
        self.server = create_server(
            WebAppConfig(self.job), host="127.0.0.1", port=0
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"
        # Two saved transcripts in the store this server's config
        # points at: with a job config, launch_home is the job
        # folder, so the store is the saved-transcripts folder
        # inside it.
        self.store = self.job / "saved-transcripts"
        for label, blob in (("a", b"video-a" * 50), ("b", b"video-b" * 60)):
            path = self.tmp / f"{label}.mp4"
            path.write_bytes(blob)
            saved.save_transcript(
                saved.identity_for_file(path), make_result(), self.store
            )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def request(self, path, data=None):
        payload = None
        headers = {}
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path, data=payload, headers=headers,
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_saved_transcripts_endpoint_carries_the_count(self):
        status, state = self.request("/api/saved-transcripts")
        self.assertEqual(status, 200)
        self.assertEqual(state["saved_transcripts_count"], 2)
        # The sign-in state keeps its established shape: the count
        # is not folded into it.
        status, signin = self.request("/api/signin")
        self.assertEqual(status, 200)
        self.assertNotIn("saved_transcripts_count", signin)

    def test_clearing_from_settings_empties_the_store(self):
        status, payload = self.request("/api/saved-transcripts/clear", {})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["cleared"], 2)
        self.assertEqual(payload["saved_transcripts_count"], 0)
        self.assertEqual(
            payload["message"],
            "Saved transcripts cleared. The next run of each "
            "video will write down its words again.",
        )
        self.assertEqual(saved.count_saved(self.store), 0)
        status, state = self.request("/api/saved-transcripts")
        self.assertEqual(state["saved_transcripts_count"], 0)

    def test_settings_page_has_the_clear_line_and_button(self):
        html = TEMPLATE.read_text(encoding="utf-8")
        self.assertIn('id="savedTranscriptsBlock"', html)
        self.assertIn('id="savedTranscriptsLine"', html)
        self.assertIn('id="clearSavedTranscripts"', html)
        self.assertIn("Clear saved transcripts", html)
        self.assertIn("saved transcripts. Bringing one of those", html)
        self.assertIn("uses its saved transcript and costs nothing new", html)
        self.assertIn('"/api/saved-transcripts/clear"', html)
        self.assertIn('"/api/saved-transcripts"', html)
        self.assertNotIn("\u2014", html[html.index('id="savedTranscriptsBlock"'):html.index('id="savedTranscriptsBlock"') + 400])


# ---------------------------------------------------------------------------
# The first edit, end to end: save, reuse, and no second provider call
# ---------------------------------------------------------------------------

FAKE_WORDS = [
    {"word": "hello", "start": 0.2, "end": 0.45},
    {"word": "world", "start": 0.5, "end": 0.75},
    {"word": "this", "start": 0.8, "end": 1.0},
    {"word": "is", "start": 1.05, "end": 1.2},
    {"word": "one", "start": 1.25, "end": 1.45},
    {"word": "take.", "start": 1.5, "end": 1.8},
]


def fake_scribe_result(wav_path, api_key, duration_s=None, work_dir=None,
                       post=None):
    return {
        "words": [dict(word) for word in FAKE_WORDS],
        "segments": speech.segments_from_words(FAKE_WORDS),
        "language": "en",
        "duration": duration_s or 4.0,
        "engine": SCRIBE_ENGINE,
    }


def make_video(path: Path, frequency: int, seconds: int) -> bytes:
    subprocess.run(
        [
            FFMPEG, "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30",
            "-f", "lavfi", "-i", f"sine=frequency={frequency}",
            "-t", str(seconds), str(path),
        ],
        check=True,
    )
    return path.read_bytes()


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class FirstEditReuseTests(IsolatedHomeCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = Path(tempfile.mkdtemp(prefix="tye-v11-fix-"))
        cls.video_a = make_video(cls.fixture_dir / "a.mp4", 440, 4)
        cls.video_b = make_video(cls.fixture_dir / "b.mp4", 880, 5)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fixture_dir, ignore_errors=True)

    def setUp(self) -> None:
        super().setUp()
        os.environ["ELEVENLABS_API_KEY"] = "sk-fake-test-only"
        self.servers = []
        # One launch home for every session in a test, and the
        # saved-transcripts store inside it. Later sessions on
        # the same computer share that store, which is what lets
        # a later session find an earlier run's saved copy.
        self.launch = self.tmp / "shared-launch"
        self.store = self.launch / "saved-transcripts"

    def tearDown(self) -> None:
        for server in self.servers:
            server.shutdown()
            server.server_close()
        super().tearDown()

    # -- helpers -----------------------------------------------------------

    def run_first_edit(self, launch_name: str, file_name: str,
                       video_bytes: bytes):
        """One whole later session: a fresh server, one drop, one run."""
        launch = self.launch
        launch.mkdir(exist_ok=True)
        server = create_server(
            WebAppConfig(None, launch_home=launch),
            host="127.0.0.1", port=0,
        )
        self.servers.append(server)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def request(path, data=None, raw=None):
            payload = raw
            headers = {}
            if data is not None:
                payload = json.dumps(data).encode("utf-8")
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(
                base + path, data=payload, headers=headers,
                method="POST" if payload is not None else "GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()

        def post_json(path, data):
            status, body = request(path, data=data)
            return status, json.loads(body)

        stem = file_name.rsplit(".", 1)[0]
        status, begun = post_json("/api/intake/begin", {"name": stem})
        self.assertEqual(status, 200, begun)
        intake_id = begun["intake_id"]
        status, _ = request(
            f"/api/intake/file?intake={intake_id}"
            f"&path={quote(file_name)}",
            raw=video_bytes,
        )
        self.assertEqual(status, 200)
        status, finished = post_json(
            "/api/intake/finish", {"intake_id": intake_id}
        )
        self.assertEqual(status, 200, finished)
        self.assertEqual(finished["kind"], "video")
        status, started = post_json(
            "/api/intake/first-edit",
            {
                "intake_id": intake_id,
                "direction": "",
                "clean_captions": True,
                "settings": {
                    "remove_retakes": False,
                    "remove_fillers": False,
                    "cut_silence": False,
                },
            },
        )
        self.assertEqual(status, 200, started)
        self.assertTrue(started["started"])
        deadline = time.time() + 60
        while time.time() < deadline:
            _, body = request(f"/api/intake/status?intake={intake_id}")
            state = json.loads(body)
            if state.get("status") in ("done", "failed", "no_words"):
                break
            time.sleep(0.2)
        else:
            self.fail("first edit did not finish in time")
        self.assertEqual(state["status"], "done", state)
        return server, state

    # -- the flow ------------------------------------------------------------

    def test_save_then_reuse_then_no_reuse_for_new_content(self):
        with mock.patch.object(
            speech, "scribe_transcribe_wav",
            side_effect=fake_scribe_result,
        ) as scribe:
            # First run: the provider is called, and the result saves.
            server, state = self.run_first_edit(
                "launch-one", "first take.mp4", self.video_a
            )
            self.assertEqual(scribe.call_count, 1)
            self.assertFalse(state["saved_transcript_reused"])
            self.assertFalse(
                any("saved from the earlier run" in line
                    for line in state["recap"]),
                state["recap"],
            )
            self.assertEqual(saved.count_saved(self.store), 1)
            job_dir = server.config.job_dir
            transcript = json.loads(
                (job_dir / "transcript.json").read_text(encoding="utf-8")
            )
            self.assertEqual(transcript["words"], FAKE_WORDS)
            self.assertEqual(transcript["engine"], SCRIBE_ENGINE)

            # Second run, a later session, the same video under a
            # new name: the saved copy is used and the provider is
            # NOT called again.
            scribe.reset_mock()
            server, state = self.run_first_edit(
                "launch-two", "renamed take.mp4", self.video_a
            )
            self.assertEqual(scribe.call_count, 0)
            self.assertTrue(state["saved_transcript_reused"])
            self.assertEqual(state["engine"], SCRIBE_ENGINE)
            self.assertIn(
                "Used the transcript saved from the earlier run "
                "of this video. No new transcription cost.",
                state["recap"],
            )
            # The reused words flow through the same path: the job
            # built from them holds the same words and engine.
            job_dir = server.config.job_dir
            transcript = json.loads(
                (job_dir / "transcript.json").read_text(encoding="utf-8")
            )
            self.assertEqual(transcript["words"], FAKE_WORDS)
            self.assertEqual(transcript["engine"], SCRIBE_ENGINE)

            # Third run: the first run's file name, but different
            # content. That must NOT reuse; the provider runs again.
            scribe.reset_mock()
            _, state = self.run_first_edit(
                "launch-three", "first take.mp4", self.video_b
            )
            self.assertEqual(scribe.call_count, 1)
            self.assertFalse(state["saved_transcript_reused"])
            self.assertFalse(
                any("saved from the earlier run" in line
                    for line in state["recap"]),
                state["recap"],
            )

    def test_uncertain_identity_transcribes_fresh_and_says_nothing(self):
        with mock.patch.object(
            speech, "scribe_transcribe_wav",
            side_effect=fake_scribe_result,
        ) as scribe:
            self.run_first_edit("launch-one", "first take.mp4", self.video_a)
            self.assertEqual(saved.count_saved(self.store), 1)
            scribe.reset_mock()
            # The same video again, but its identity cannot be
            # settled this run: fresh transcription, no reuse line,
            # nothing extra said.
            with mock.patch.object(
                saved, "identity_for_file", return_value=None
            ):
                _, state = self.run_first_edit(
                    "launch-two", "renamed take.mp4", self.video_a
                )
            self.assertEqual(scribe.call_count, 1)
            self.assertFalse(state["saved_transcript_reused"])
            self.assertFalse(
                any("saved" in line.lower() and "transcript" in line.lower()
                    for line in state["recap"]),
                state["recap"],
            )


if __name__ == "__main__":
    unittest.main()
