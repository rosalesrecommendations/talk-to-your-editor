"""Tests for the v0.4.1 drag-and-drop intake (stdlib unittest).

Spins the real HTTP server on an ephemeral port against a temp launch
job, then drops folders and files through the real intake endpoints:
a staged job folder becomes the current job, a lone video stages and
plays with its plain sentence, a folder that is not a job gets the
clear not-a-job state, and no upload path can escape the staging
folder.
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

from talk_to_your_editor.webapp import WebAppConfig, create_server

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VIDEO_BYTES = bytes(range(256)) * 32  # 8192 deterministic dummy bytes


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class IntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-intake-"))
        self.job = self.tmp / "launch-job"
        self.job.mkdir()
        for name, target in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / name, self.job / target)
        (self.job / "review.mp4").write_bytes(VIDEO_BYTES)
        self.server = create_server(
            WebAppConfig(self.job), host="127.0.0.1", port=0
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
        req = urllib.request.Request(url, data=payload, headers=hdrs, method=method)
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

    # -- the drop zone is on the page ---------------------------------------

    def test_home_page_carries_the_drop_zone(self):
        status, _, body = self.request("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        self.assertIn('id="dropArea"', page)
        self.assertIn("Drop the job folder or a video here", page)
        self.assertIn('id="dropStatus"', page)

    # -- a dropped job folder becomes the current job ------------------------

    def test_dropped_job_folder_loads_and_runs(self):
        intake_id, finished = self.drop_files(
            "dropped-job",
            [
                ("dropped-job/transcript.json", self.fixture_bytes("sample_transcript.json")),
                ("dropped-job/cuts.json", self.fixture_bytes("sample_cuts.json")),
                ("dropped-job/profile.json", self.fixture_bytes("sample_profile.json")),
                ("dropped-job/review.mp4", VIDEO_BYTES),
            ],
        )
        self.assertEqual(finished["kind"], "job")
        self.assertIn("running on dropped-job", finished["message"])
        # The app now answers for the dropped job, not the launch job.
        summary = self.get_json("/api/job")
        self.assertEqual(summary["job_name"], "dropped-job")
        self.assertEqual(summary["words_kept"], 32)
        # The staged copy lives under the launch job's session intake,
        # and the launch job itself gained no session of the new job.
        staged = list(
            (self.job / "tye-session" / "intake").glob("dropped-job-*/dropped-job")
        )
        self.assertEqual(len(staged), 1)
        self.assertTrue((staged[0] / "transcript.json").is_file())
        # The front door runs on the staged copy (mock provider): the
        # session it writes belongs to the staged job, not the launch job.
        status, result = self.post_json(
            "/api/front-door", {"provider": "mock"}
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertTrue(
            (staged[0] / "tye-session" / "front_door.json").is_file()
        )
        self.assertFalse(
            (self.job / "tye-session" / "front_door.json").exists()
        )

    def test_dropped_job_keeps_its_own_session_out_of_the_launch_job(self):
        # Saving a comment after the drop writes into the staged job's
        # session; the launch job's folder collects nothing but the
        # staged copy itself.
        self.drop_files(
            "dropped-job",
            [
                ("dropped-job/transcript.json", self.fixture_bytes("sample_transcript.json")),
                ("dropped-job/cuts.json", self.fixture_bytes("sample_cuts.json")),
            ],
        )
        status, payload = self.post_json(
            "/api/comments",
            {
                "video": "dropped-job",
                "comments": [
                    {"id": "c1", "ts": 1.0, "fmt": "vertical",
                     "text": "Remove this sentence here."}
                ],
            },
        )
        self.assertEqual(status, 200, payload)
        staged = list(
            (self.job / "tye-session" / "intake").glob("dropped-job-*/dropped-job")
        )[0]
        self.assertTrue((staged / "tye-session" / "feedback.json").is_file())
        self.assertFalse((self.job / "tye-session" / "feedback.json").exists())

    # -- a lone video stages and plays ---------------------------------------

    def test_dropped_single_video_stages_and_plays(self):
        intake_id, finished = self.drop_files(
            "clip",
            [("clip.mp4", VIDEO_BYTES)],
        )
        self.assertEqual(finished["kind"], "video")
        self.assertEqual(finished["video_name"], "clip.mp4")
        # The one plain sentence: what a full edit still needs.
        self.assertIn("word-by-word transcript", finished["message"])
        self.assertIn("part 1, the runner", finished["message"])
        # The staged video serves back over HTTP, ranges included.
        status, headers, body = self.request(finished["video_url"])
        self.assertEqual(status, 200)
        self.assertGreater(len(body), 0)
        status, headers, _ = self.request(
            finished["video_url"], headers={"Range": "bytes=0-99"}
        )
        self.assertIn(status, (200, 206))
        # A video alone never becomes the job.
        summary = self.get_json("/api/job")
        self.assertEqual(summary["job_name"], "launch-job")

    # -- a folder that is not a job ------------------------------------------

    def test_folder_without_job_files_gets_the_plain_state(self):
        _, finished = self.drop_files(
            "random-folder",
            [
                ("random-folder/readme.txt", "just some notes"),
                ("random-folder/photo.jpg", VIDEO_BYTES),
            ],
        )
        self.assertEqual(finished["kind"], "not_a_job")
        self.assertIn("not a finished-edit job", finished["message"])
        self.assertIn("transcript.json and cuts.json", finished["message"])
        # The current job is unchanged.
        summary = self.get_json("/api/job")
        self.assertEqual(summary["job_name"], "launch-job")

    def test_folder_with_only_half_a_job_is_not_a_job(self):
        _, finished = self.drop_files(
            "half-job",
            [
                ("half-job/transcript.json", self.fixture_bytes("sample_transcript.json")),
            ],
        )
        self.assertEqual(finished["kind"], "not_a_job")

    # -- safety of the staging itself -----------------------------------------

    def test_upload_paths_cannot_escape_the_staging_folder(self):
        status, begun = self.post_json("/api/intake/begin", {"name": "escape"})
        intake_id = begun["intake_id"]
        for bad in ("../evil.txt", "a/../../evil.txt", "/abs.txt"):
            url = (
                "/api/intake/file?intake=" + quote(intake_id)
                + "&path=" + quote(bad)
            )
            status, _, body = self.request(url, raw=b"nope")
            # "/abs.txt" loses its leading slash and lands inside as
            # abs.txt; the two ".." shapes are refused outright.
            if ".." in bad:
                self.assertEqual(status, 400, f"{bad} -> {status}: {body!r}")
        self.assertFalse((self.tmp / "evil.txt").exists())
        self.assertFalse((self.job / "tye-session" / "evil.txt").exists())

    def test_finish_with_an_unknown_drop_is_a_plain_refusal(self):
        status, payload = self.post_json(
            "/api/intake/finish", {"intake_id": "no-such-drop"}
        )
        self.assertEqual(status, 400)
        self.assertIn("not on record", payload["error"])

    def test_file_for_an_unknown_drop_is_a_plain_refusal(self):
        url = "/api/intake/file?intake=nope&path=" + quote("x/transcript.json")
        status, _, body = self.request(url, raw=b"{}")
        self.assertEqual(status, 400)
        self.assertIn(b"not on record", body)


if __name__ == "__main__":
    unittest.main()
