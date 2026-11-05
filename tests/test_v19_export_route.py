"""Tests for the v0.19 timeline export download route (Builder B).

Spins the real HTTP server on an ephemeral port against a temp job
dir built from the sample fixture, the same arrangement as
test_webapp.py, and drives GET /api/export: the right file, content
type, and filename per format; the session's revised plan winning
over the job's shipped plan; and a plain error with no file for a
format the app does not offer. Everything is offline.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.webapp import WebAppConfig, create_server

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VIDEO_BYTES = bytes(range(256)) * 32  # 8192 deterministic dummy bytes

EDL_EVENT = re.compile(
    r"^(\d{3})\s+(\S+)\s+([VA])\s+C\s+"
    r"(\d{2}:\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2}:\d{2})\s+"
    r"(\d{2}:\d{2}:\d{2}:\d{2})\s+(\d{2}:\d{2}:\d{2}:\d{2})$"
)


class ExportRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-export-"))
        self.job = self.tmp / "job"
        self.job.mkdir()
        for name in (
            "sample_transcript.json",
            "sample_cuts.json",
            "sample_profile.json",
        ):
            target = {
                "sample_transcript.json": "transcript.json",
                "sample_cuts.json": "cuts.json",
                "sample_profile.json": "profile.json",
            }[name]
            shutil.copyfile(FIXTURES / name, self.job / target)
        (self.job / "review.mp4").write_bytes(VIDEO_BYTES)
        self.kept_count = len(
            json.loads(
                (self.job / "cuts.json").read_text(encoding="utf-8")
            )["kept_ranges"]
        )
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

    def request(self, path):
        req = urllib.request.Request(self.base + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    # -- the three formats ---------------------------------------------------

    def test_fcpxml_download(self):
        status, headers, body = self.request("/api/export?format=fcpxml")
        self.assertEqual(status, 200)
        self.assertTrue(
            headers.get_content_type() == "application/xml",
            headers.get("Content-Type"),
        )
        disposition = headers.get("Content-Disposition") or ""
        self.assertTrue(disposition.startswith("attachment;"), disposition)
        self.assertIn('filename="', disposition)
        self.assertTrue(
            disposition.rstrip('"').endswith("-final-cut.fcpxml"),
            disposition,
        )
        root = ET.fromstring(body.decode("utf-8"))
        self.assertEqual(root.tag, "fcpxml")
        clips = root.findall(".//asset-clip")
        self.assertEqual(len(clips), self.kept_count)

    def test_premiere_download(self):
        status, headers, body = self.request("/api/export?format=premiere")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/xml")
        disposition = headers.get("Content-Disposition") or ""
        self.assertTrue(disposition.startswith("attachment;"), disposition)
        self.assertTrue(
            disposition.rstrip('"').endswith("-premiere.xml"),
            disposition,
        )
        root = ET.fromstring(body.decode("utf-8"))
        self.assertEqual(root.tag, "xmeml")
        items = root.findall(".//video/track/clipitem")
        self.assertEqual(len(items), self.kept_count)

    def test_edl_download(self):
        status, headers, body = self.request("/api/export?format=edl")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "text/plain")
        disposition = headers.get("Content-Disposition") or ""
        self.assertTrue(disposition.startswith("attachment;"), disposition)
        self.assertTrue(disposition.endswith('.edl"'), disposition)
        text = body.decode("utf-8")
        self.assertTrue(text.startswith("TITLE:"), text[:40])
        events = [
            match.groups()
            for match in (
                EDL_EVENT.match(line) for line in text.splitlines()
            )
            if match
        ]
        video = [event for event in events if event[2] == "V"]
        self.assertEqual(len(video), self.kept_count)

    # -- the plan it reads -----------------------------------------------------

    def test_revised_plan_wins_over_the_shipped_plan(self):
        session = self.job / "tye-session"
        session.mkdir()
        revised = {
            "source_duration_s": 26.5,
            "frame_quantization": {"fps": 30.0, "ranges": 2, "repairs": 0},
            "kept_ranges": [
                {"start": 1.0, "end": 4.0},
                {"start": 8.0, "end": 9.5},
            ],
        }
        (session / "revised_cuts.json").write_text(
            json.dumps(revised), encoding="utf-8"
        )
        status, _headers, body = self.request("/api/export?format=fcpxml")
        self.assertEqual(status, 200)
        root = ET.fromstring(body.decode("utf-8"))
        self.assertEqual(len(root.findall(".//asset-clip")), 2)

    # -- plain errors, no file ---------------------------------------------------

    def test_unknown_format_is_a_plain_error_and_no_file(self):
        status, headers, body = self.request("/api/export?format=bogus")
        self.assertEqual(status, 400)
        self.assertIsNone(headers.get("Content-Disposition"))
        payload = json.loads(body.decode("utf-8"))
        self.assertIn("error", payload)
        self.assertIn("fcpxml", payload["error"])

    def test_missing_format_is_a_plain_error_and_no_file(self):
        status, headers, body = self.request("/api/export")
        self.assertEqual(status, 400)
        self.assertIsNone(headers.get("Content-Disposition"))
        payload = json.loads(body.decode("utf-8"))
        self.assertIn("error", payload)

    # -- the page ----------------------------------------------------------------

    def test_page_carries_the_three_buttons_and_the_plain_line(self):
        status, _headers, body = self.request("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        self.assertIn("Final Cut or DaVinci (timeline file)", page)
        self.assertIn("Premiere (timeline file)", page)
        self.assertIn("Any editor (EDL)", page)
        self.assertIn('href="/api/export?format=fcpxml"', page)
        self.assertIn('href="/api/export?format=premiere"', page)
        self.assertIn('href="/api/export?format=edl"', page)
        self.assertIn(
            "These hand the edit to the other program as a timeline "
            "you can keep changing; the rendered video is still "
            "there as before.",
            page,
        )
        self.assertNotIn("CapCut", page)
        self.assertNotIn("capcut", page)


class ExportRouteNoJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-export-nojob-"))
        self.server = create_server(
            WebAppConfig(None, launch_home=self.tmp / "launch"),
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

    def test_no_job_gets_one_plain_sentence_and_no_file(self):
        req = urllib.request.Request(
            self.base + "/api/export?format=edl", method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status, headers, body = (
                    resp.status,
                    resp.headers,
                    resp.read(),
                )
        except urllib.error.HTTPError as exc:
            status, headers, body = exc.code, exc.headers, exc.read()
        self.assertEqual(status, 404)
        self.assertIsNone(headers.get("Content-Disposition"))
        payload = json.loads(body.decode("utf-8"))
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
