"""Tests for the v0.5.2 no-job launch (stdlib unittest).

The app must open with no job required: the server starts empty,
the home page is the front door (drop zone, built-in sample, Meta
sign-in check), job-shaped requests get one plain sentence instead
of an error trace, and a dropped folder or the sample loads exactly
as it does on a server that started with a job.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.cli import build_parser
from talk_to_your_editor.webapp import (
    NO_JOB_MESSAGE,
    WebAppConfig,
    create_server,
    serve,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VIDEO_BYTES = bytes(range(256)) * 32  # 8192 deterministic dummy bytes
NO_JOB_PLAIN = "No job is loaded yet. Drop a runner job folder"


class NoJobConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-nojob-cfg-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_job_config_anchors_its_session_in_the_launch_home(self):
        home = self.tmp / "launch"
        config = WebAppConfig(None, launch_home=home)
        self.assertFalse(config.has_job)
        self.assertIsNone(config.job_dir)
        self.assertEqual(config.session_dir, home / "tye-session")
        self.assertEqual(config.job_label(), "no job yet")

    def test_job_config_is_unchanged(self):
        job = self.tmp / "job"
        job.mkdir()
        config = WebAppConfig(job)
        self.assertTrue(config.has_job)
        self.assertEqual(config.session_dir, job / "tye-session")
        self.assertEqual(config.job_label(), "job")

    def test_serve_cli_takes_no_job_dir(self):
        args = build_parser().parse_args(["serve"])
        self.assertIsNone(args.job_dir)
        args = build_parser().parse_args(
            ["serve", "--job-dir", "/somewhere/job"]
        )
        self.assertEqual(args.job_dir, "/somewhere/job")

    def test_serve_still_refuses_a_file_plainly(self):
        a_file = self.tmp / "clip.mp4"
        a_file.write_bytes(VIDEO_BYTES)
        code = serve(
            str(a_file), launch_home=str(self.tmp / "launch")
        )
        self.assertEqual(code, 2)


class NoJobServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-nojob-"))
        self.home = self.tmp / "launch-home"
        self.server = create_server(
            WebAppConfig(None, launch_home=self.home),
            host="127.0.0.1",
            port=0,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers -----------------------------------------------------------

    def request(self, path, data=None, raw=None, headers=None):
        url = self.base + path
        hdrs = dict(headers or {})
        payload = raw
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        method = "POST" if (data is not None or raw is not None) else "GET"
        req = urllib.request.Request(
            url, data=payload, headers=hdrs, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def get_json(self, path):
        status, _, body = self.request(path)
        self.assertEqual(status, 200, f"GET {path} -> {status}: {body!r}")
        return json.loads(body.decode("utf-8"))

    def post_json(self, path, data):
        status, _, body = self.request(path, data=data)
        return status, json.loads(body.decode("utf-8"))

    def drop_files(self, name, files):
        """Drive one whole drop: begin, one POST per file, finish."""
        status, begun = self.post_json("/api/intake/begin", {"name": name})
        self.assertEqual(status, 200, begun)
        intake_id = begun["intake_id"]
        for relative, payload in files:
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            url = (
                "/api/intake/file?intake=" + quote(intake_id)
                + "&path=" + quote(relative)
            )
            status, _, body = self.request(url, raw=payload)
            self.assertEqual(
                status, 200, f"upload {relative} -> {status}: {body!r}"
            )
        status, finished = self.post_json(
            "/api/intake/finish", {"intake_id": intake_id}
        )
        self.assertEqual(status, 200, finished)
        return intake_id, finished

    def fixture_bytes(self, name):
        return (FIXTURES / name).read_bytes()

    # -- the front door renders clean ---------------------------------------

    def test_home_page_is_the_front_door(self):
        status, _, body = self.request("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        self.assertIn('id="dropArea"', page)
        self.assertIn("Drop the job folder or a video here", page)
        self.assertIn("No job is loaded yet", page)
        self.assertIn('id="loadSample"', page)
        self.assertIn("Try the built-in sample", page)
        self.assertIn('id="checkSignin"', page)
        self.assertIn("var HAS_JOB = false;", page)
        # No leftover template placeholders, no machine wording.
        self.assertNotIn("{{", page)

    def test_job_summary_is_an_empty_clean_state(self):
        summary = self.get_json("/api/job")
        self.assertFalse(summary["has_job"])
        self.assertIsNone(summary["job_name"])
        self.assertEqual(summary["words_total"], 0)
        self.assertEqual(summary["words_kept"], 0)
        self.assertEqual(summary["videos"], [])
        self.assertEqual(summary["comments"], [])
        self.assertEqual(summary["transcript_words"], [])

    def test_signin_endpoints_work_with_no_job(self):
        state = self.get_json("/api/signin")
        self.assertFalse(state["meta_signin_set"])
        status, payload = self.post_json(
            "/api/signin", {"api_key": "sk-test-session-only"}
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["meta_signin_set"])
        self.assertEqual(payload["source"], "session")

    def test_history_reads_empty_with_no_job(self):
        payload = self.get_json("/api/history")
        self.assertEqual(payload["checkpoints"], [])
        self.assertFalse(payload["can_undo"])

    def test_video_route_is_a_plain_404_with_no_job(self):
        status, _, body = self.request("/video/review.mp4")
        self.assertEqual(status, 404)
        self.assertIn(b"no job is loaded yet", body)

    # -- job-shaped requests get one plain sentence --------------------------

    def test_job_posts_are_refused_in_plain_words(self):
        for path, payload in (
            ("/api/front-door", {"provider": "mock"}),
            ("/api/comments", {"comments": []}),
            ("/api/direct", {}),
            ("/api/render", {}),
            ("/api/history/undo", {}),
        ):
            status, body = self.post_json(path, payload)
            self.assertEqual(status, 400, f"{path} -> {status}: {body!r}")
            self.assertIn(NO_JOB_PLAIN, body["error"])
        self.assertEqual(NO_JOB_MESSAGE.split(".")[0] + ".",
                         "No job is loaded yet.")

    # -- the built-in sample loads with one call ------------------------------

    def test_load_sample_becomes_the_job(self):
        status, payload = self.post_json("/api/load-sample", {})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["kind"], "job")
        staged = self.home / "sample-job"
        for name in ("transcript.json", "cuts.json", "profile.json"):
            self.assertTrue((staged / name).is_file(), name)
        summary = self.get_json("/api/job")
        self.assertTrue(summary["has_job"])
        self.assertEqual(summary["words_kept"], 32)
        # The page now renders as a job page.
        status, _, body = self.request("/")
        self.assertIn("var HAS_JOB = true;", body.decode("utf-8"))
        # And the front door fires on the sample (mock provider).
        status, result = self.post_json(
            "/api/front-door", {"provider": "mock"}
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertTrue(
            (staged / "tye-session" / "front_door.json").is_file()
        )

    def test_sample_load_keeps_the_session_signin(self):
        self.post_json("/api/signin", {"api_key": "sk-carry-over"})
        self.post_json("/api/load-sample", {})
        state = self.get_json("/api/signin")
        self.assertTrue(state["meta_signin_set"])
        self.assertEqual(state["source"], "session")

    # -- drops work from the front door, staged in the launch home ------------

    def test_dropped_job_stages_under_the_launch_home(self):
        _, finished = self.drop_files(
            "dropped-job",
            [
                ("dropped-job/transcript.json", self.fixture_bytes("sample_transcript.json")),
                ("dropped-job/cuts.json", self.fixture_bytes("sample_cuts.json")),
                ("dropped-job/profile.json", self.fixture_bytes("sample_profile.json")),
            ],
        )
        self.assertEqual(finished["kind"], "job")
        staged = list(
            (self.home / "tye-session" / "intake").glob(
                "dropped-job-*/dropped-job"
            )
        )
        self.assertEqual(len(staged), 1)
        summary = self.get_json("/api/job")
        self.assertTrue(summary["has_job"])
        self.assertEqual(summary["job_name"], "dropped-job")
        self.assertEqual(summary["words_kept"], 32)

    def test_dropped_lone_video_plays_with_its_sentence(self):
        _, finished = self.drop_files(
            "clip", [("clip.mp4", VIDEO_BYTES)]
        )
        self.assertEqual(finished["kind"], "video")
        self.assertIn("make the first edit itself", finished["message"])
        status, _, body = self.request(finished["video_url"])
        self.assertEqual(status, 200)
        self.assertGreater(len(body), 0)
        # Still no job: the front door is still the state.
        summary = self.get_json("/api/job")
        self.assertFalse(summary["has_job"])


if __name__ == "__main__":
    unittest.main()
