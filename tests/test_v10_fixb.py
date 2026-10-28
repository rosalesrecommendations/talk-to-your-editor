"""Tests for v0.10 FIX B: the session log and the problem report.

Every stage writes step-by-step lines to tye-session/session.log
as it goes, and the Problem report button makes one zip in the
job's folder holding a fixed list: a first page (app version,
job name, when made), the session log, the playback log when
there is one, the run audit files that exist, a costs-and-rounds
page, and the current cut counts. Keys and key-shaped values are
scrubbed out of every file in the zip. With no job open, the zip
lands in the app's home folder instead.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.webapp import WebAppConfig, create_server

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VIDEO_BYTES = bytes(range(256)) * 32
FAKE_KEY = "sk-FAKEKEY1234567890"


def request_json(base, path, data=None):
    payload = None
    headers = {}
    if data is not None:
        payload = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base + path, data=payload, headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def copy_sample_job(job: Path) -> None:
    for name, target in (
        ("sample_transcript.json", "transcript.json"),
        ("sample_cuts.json", "cuts.json"),
        ("sample_profile.json", "profile.json"),
    ):
        shutil.copyfile(FIXTURES / name, job / target)
    (job / "review.mp4").write_bytes(VIDEO_BYTES)


class ProblemReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v10-fixb-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = mock.patch.dict(
            os.environ, {"HOME": str(self.home)}, clear=False
        )
        env.start()
        self.addCleanup(env.stop)
        self.job = self.tmp / "job"
        self.job.mkdir()
        copy_sample_job(self.job)
        session = self.job / "tye-session"
        (session / "playback").mkdir(parents=True)
        # Planted audit material carrying key-shaped secrets: the
        # report must scrub them, never carry them.
        (session / "operations_audit.json").write_text(json.dumps({
            "operations": [],
            "note": f"the key was {FAKE_KEY} at the time",
            "api_key": "abc123secret",
        }))
        (session / "playback" / "playback.log").write_text(
            "building review.mp4\nready review.mp4\n"
        )
        # The other two run-audit files are included when they
        # exist (their "when present" branch needs a planted-file
        # proof, not just a code read).
        (session / "direction_pass.json").write_text(json.dumps({
            "ran": True, "applied": 2,
        }))
        (session / "retake_pass.json").write_text(json.dumps({
            "ran": True, "pairs": [],
        }))
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

    def test_stages_write_the_session_log(self):
        status, payload = request_json(
            self.base, "/api/front-door",
            {"remove_retakes": False, "cut_silence": True,
             "buffer_s": 0.1},
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        log_path = self.job / "tye-session" / "session.log"
        self.assertTrue(log_path.is_file())
        entries = [
            json.loads(line)
            for line in log_path.read_text().splitlines() if line
        ]
        stages = {(e["stage"], e["event"]) for e in entries}
        self.assertIn(("front door", "start"), stages)
        self.assertIn(("front door", "finish"), stages)
        self.assertIn(("silence", "start"), stages)
        self.assertIn(("silence", "finish"), stages)
        for entry in entries:
            self.assertIn("at", entry)

    def test_report_zip_contents_and_scrub(self):
        request_json(
            self.base, "/api/front-door",
            {"remove_retakes": False, "cut_silence": True,
             "buffer_s": 0.1},
        )
        status, payload = request_json(
            self.base, "/api/problem-report", {}
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertIn(payload["file"], payload["message"])
        self.assertIn("in this job's folder", payload["message"])
        zip_path = self.job / payload["file"]
        self.assertTrue(zip_path.is_file())
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
            texts = {
                name: archive.read(name).decode("utf-8")
                for name in names
            }
        for expected in (
            "report.txt", "session.log", "playback.log",
            "operations_audit.json", "costs-and-rounds.txt",
            "cut-counts.txt", "direction_pass.json",
            "retake_pass.json",
        ):
            self.assertIn(expected, names)
        self.assertIn("App version:", texts["report.txt"])
        self.assertIn("Job: job", texts["report.txt"])
        self.assertIn("Words in the edit:", texts["cut-counts.txt"])
        self.assertIn("Costs and rounds", texts["costs-and-rounds.txt"])
        # The scrub: no planted secret survives anywhere in the zip.
        for name, text in texts.items():
            self.assertNotIn(FAKE_KEY, text, name)
            self.assertNotIn("abc123secret", text, name)
        self.assertIn("[key removed]", texts["operations_audit.json"])

    def test_page_carries_the_button(self):
        req = urllib.request.Request(self.base + "/")
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
        self.assertIn(b'id="problemReportBtn"', body)
        self.assertIn(b"Make a problem report", body)


class ProblemReportNoJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v10-fixb-nojob-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = mock.patch.dict(
            os.environ, {"HOME": str(self.home)}, clear=False
        )
        env.start()
        self.addCleanup(env.stop)
        self.launch = self.tmp / "launch home"
        self.launch.mkdir()
        self.server = create_server(
            WebAppConfig(None, launch_home=self.launch),
            host="127.0.0.1", port=0,
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

    def test_report_with_no_job_lands_in_the_home_folder(self):
        status, payload = request_json(
            self.base, "/api/problem-report", {}
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertIn("app's home folder", payload["message"])
        zip_path = self.launch / payload["file"]
        self.assertTrue(zip_path.is_file())
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
            first_page = archive.read("report.txt").decode("utf-8")
        self.assertIn("report.txt", names)
        self.assertIn("costs-and-rounds.txt", names)
        self.assertIn("cut-counts.txt", names)
        self.assertIn("no job open", first_page)


if __name__ == "__main__":
    unittest.main()
