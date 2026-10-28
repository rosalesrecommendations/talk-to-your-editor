"""FIX A (v0.10): the review player shows the edited video.

The 0.9.0 defect, from Justin's hands-on test: after Make the
edit, the words showed the cuts while the player went on playing
the original at full length, because the player was only ever
pointed at the job folder's own videos and the edited splice
lives in the session. These tests pin the fix:

- after a first edit, the job summary's watching state names the
  edited video, and the file served under that name measures the
  kept plan's length, not the original's;
- a plan change the splice does not cover flips the state back
  to the original, marked stale, so nothing stale is ever
  presented as the edit;
- the render action rebuilds and the state returns to edited;
- a runner job with no session splice reads as the original,
  plainly, with nothing stale or missing claimed.
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
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from talk_to_your_editor import transcribe as speech  # noqa: E402
from talk_to_your_editor.webapp import WebAppConfig, create_server  # noqa: E402

import test_v06  # noqa: E402  (the suite's canned words + fake route)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


class FixAHttpBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-fixa-"))
        self.launch = self.tmp / "launch home"
        self.launch.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ["HOME"] = str(self.home)
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

    def request(self, path, data=None, raw=None, headers=None):
        hdrs = dict(headers or {})
        payload = raw
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path,
            data=payload,
            headers=hdrs,
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def post_json(self, path, data):
        _, body = self.request(path, data=data)
        return json.loads(body)

    def get_json(self, path):
        _, body = self.request(path)
        return json.loads(body)

    def get_file(self, path, dest_name):
        _, body = self.request(path)
        dest = self.tmp / dest_name
        dest.write_bytes(body)
        return dest


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not available")
class PlayerShowsTheEditTests(FixAHttpBase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = Path(tempfile.mkdtemp(prefix="tye-fixa-fix-"))
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

    def run_first_edit(self):
        _, begun = self.request("/api/intake/begin", data={"name": "raw take"})
        intake_id = json.loads(begun)["intake_id"]
        self.request(
            f"/api/intake/file?intake={intake_id}&path=raw%20take.mp4",
            raw=self.video_bytes,
            headers={"Content-Type": "application/octet-stream"},
        )
        finished = self.post_json(
            "/api/intake/finish", {"intake_id": intake_id}
        )
        self.assertTrue(finished["can_first_edit"])
        with mock.patch.object(
            speech, "local_available", return_value=True
        ), mock.patch.object(
            speech, "local_transcribe_wav",
            side_effect=test_v06.fake_local_result,
        ):
            started = self.post_json(
                "/api/intake/first-edit", {"intake_id": intake_id}
            )
            self.assertTrue(started["started"], started)
            deadline = time.time() + 120
            state = {}
            while time.time() < deadline:
                state = self.get_json(
                    f"/api/intake/status?intake={intake_id}"
                )
                if state.get("status") in ("done", "failed", "no_words"):
                    break
                time.sleep(0.25)
        self.assertEqual(state.get("status"), "done", state)
        return state

    def test_player_is_served_the_edited_length_after_the_edit(self):
        self.run_first_edit()
        summary = self.get_json("/api/job")
        watching = summary["watching"]
        self.assertEqual(watching["kind"], "edited")
        self.assertEqual(watching["name"], "revised-review.mp4")
        self.assertTrue(watching["stamp"])
        # The served edited video matches the kept plan's length.
        served = self.get_file("/video/revised-review.mp4", "served-edit.mp4")
        served_dur = probe_duration(served)
        plan_dur = summary["output_duration_s"]
        self.assertAlmostEqual(served_dur, plan_dur, delta=0.6)
        # And it is plainly not the original's length.
        self.assertLess(served_dur, 21.0)
        original = self.get_file(
            "/video/" + urllib.parse.quote("raw take.mp4"), "served-orig.mp4"
        )
        self.assertAlmostEqual(probe_duration(original), 22.0, delta=0.6)
        # The page names what is on screen, in plain words.
        _, page = self.request("/")
        html = page.decode("utf-8")
        self.assertIn('id="watchingLine"', html)
        self.assertIn("You are watching: the edited video", html)
        self.assertIn("You are watching: the original video", html)
        self.assertIn("Your newest cuts are not in a video yet.", html)

    def test_a_plan_change_flips_the_state_until_a_rebuild(self):
        self.run_first_edit()
        summary = self.get_json("/api/job")
        self.assertEqual(summary["watching"]["kind"], "edited")
        # Bring back words the silence pass trimmed: any plan
        # change the splice does not cover must flip the state.
        words = summary["transcript_words"]
        cut_index = next(
            w["index"] for w in words if w["status"] == "cut"
        ) if any(w["status"] == "cut" for w in words) else None
        if cut_index is None:
            # No single word is marked cut (the trims were joins);
            # cut a kept word by hand instead: the plan still moves.
            kept_index = next(
                w["index"] for w in words if w["status"] == "kept"
            )
            self.post_json(
                "/api/word-toggle",
                {"word_index": kept_index, "scope": "vertical"},
            )
        else:
            self.post_json(
                "/api/word-toggle",
                {"word_index": cut_index, "scope": "vertical"},
            )
        summary = self.get_json("/api/job")
        self.assertEqual(summary["watching"]["kind"], "original")
        self.assertTrue(summary["watching"]["stale"])
        # The render action rebuilds, and the state returns.
        result = self.post_json("/api/render", {})
        self.assertTrue(result.get("ok"), result)
        summary = self.get_json("/api/job")
        self.assertEqual(summary["watching"]["kind"], "edited")
        served = self.get_file("/video/revised-review.mp4", "rebuilt.mp4")
        self.assertAlmostEqual(
            probe_duration(served), summary["output_duration_s"], delta=0.6
        )


class RunnerJobWatchingTests(FixAHttpBase):
    def test_a_runner_job_with_no_splice_reads_as_the_original(self):
        job = self.tmp / "runner job"
        job.mkdir()
        transcript = {
            "words": [
                {"word": "hello", "start": 0.0, "end": 0.4},
                {"word": "there", "start": 0.5, "end": 0.9},
            ]
        }
        (job / "transcript.json").write_text(json.dumps(transcript))
        (job / "cuts.json").write_text(
            json.dumps({"kept_ranges": [{"start": 0.0, "end": 1.0}]})
        )
        (job / "profile.json").write_text(json.dumps({}))
        (job / "feedback.json").write_text(
            json.dumps({"comments": [], "overall_feedback": []})
        )
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.server = create_server(
            WebAppConfig(job, launch_home=self.launch),
            host="127.0.0.1",
            port=0,
        )
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        summary = self.get_json("/api/job")
        watching = summary["watching"]
        self.assertEqual(watching["kind"], "original")
        self.assertFalse(watching["stale"])
        self.assertFalse(watching["not_built"])
        self.assertIsNone(watching["failed"])


class RecapLengthLineTests(PlayerShowsTheEditTests):
    """The recap says the length change out loud (Justin's rider
    from his real session): the splice keeps only the talking
    parts, so the edit can be far shorter than the recording,
    and no one should have to discover that from the player."""

    def test_lengths_are_named_in_both_recaps(self):
        state = self.run_first_edit()
        lines = state.get("lines") or state.get("recap") or []
        self.assertTrue(lines, state)
        self.assertTrue(
            lines[0].startswith("Your video was 0:22."), lines[0]
        )
        self.assertIn("The edit is 0:", lines[0])
        payload = self.post_json(
            "/api/front-door",
            {"remove_retakes": False, "cut_silence": True,
             "buffer_s": 0.1},
        )
        self.assertTrue(payload["ok"], payload)
        line = payload.get("length_recap")
        self.assertIsNotNone(line)
        self.assertTrue(
            line.startswith("Your video was 0:22."), line
        )
        self.assertIn("The edit is 0:", line)


class FailureWordsTests(unittest.TestCase):
    """A video that cannot be made watchable names the log file
    to send back, in plain words, on every surface, with no
    error codes."""

    def test_failure_words_name_the_playback_log(self):
        from talk_to_your_editor import webapp

        template = (
            Path(__file__).resolve().parents[1]
            / "src" / "talk_to_your_editor" / "templates"
            / "test_app.html"
        ).read_text(encoding="utf-8")
        self.assertIn("playback.log", webapp.PLAYBACK_FAILED_MESSAGE)
        self.assertIn(
            "in the playback folder inside this job's "
            "tye-session folder",
            webapp.PLAYBACK_FAILED_MESSAGE,
        )
        self.assertIn("playback.log", template)
        self.assertNotIn("503", webapp.PLAYBACK_FAILED_MESSAGE)


if __name__ == "__main__":
    unittest.main()
