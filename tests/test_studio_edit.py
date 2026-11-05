"""Tests for Studio's Edit screen wiring (Message 2, 2026-11-01).

The Edit screen is front-end only: it calls the endpoints the
classic page already uses (intake, job, thread, word-toggle,
word-range, gap-erase, timeline, history, gold-verdict). These
tests pin the page contract: the screen's building blocks are in
the served Studio page, the endpoints the screen calls answer on
a loaded job, and the page still carries no speech output.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.webapp import WebAppConfig, create_server

TEMPLATES = ROOT / "src" / "talk_to_your_editor" / "templates"


def make_job(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    words = []
    t = 0.0
    for w in ["um", "i", "was", "going", "to", "say", "i", "was",
              "going", "to", "say", "it", "is", "fine"]:
        words.append({"word": w, "start": round(t, 2), "end": round(t + 0.4, 2)})
        t += 0.5
    (folder / "transcript.json").write_text(
        json.dumps({"engine": "test", "duration": t, "words": words}),
        encoding="utf-8",
    )
    (folder / "cuts.json").write_text(
        json.dumps({
            "source_duration_s": t,
            "kept_ranges": [{"start": 0.0, "end": t}],
            "silence_cuts": [],
        }),
        encoding="utf-8",
    )
    return folder


class StudioEditPage(unittest.TestCase):
    def setUp(self):
        self.html = (TEMPLATES / "studio.html").read_text(encoding="utf-8")

    def test_edit_screen_building_blocks_present(self):
        for marker in (
            'id="editFile"', 'id="editSample"', 'id="requestsBox"',
            'id="swRetakes"', 'id="swGaps"', 'id="swFillers"',
            'id="makeEditBtn"', 'id="editLevels"',
            'id="transcript"', 'id="pickBar"', 'id="gapBar"',
            'id="yourCall"', 'id="chipRow"', 'id="pinNote"',
            'id="tlStrip"', 'id="tlLane"', 'id="tlFadePop"',
            'id="joinsBtn"', 'id="keysCard"',
            'id="tbSwap"', 'id="tbReset"',
            # The Oct 31 recording rules on the Edit screen: an
            # empty screen with one drop target, parts staged as
            # pictures in order, worded panel toggles, and a
            # Download button that shows once a render exists.
            'id="editDropRow"', 'id="editParts"', 'id="partsList"',
            'id="editUseParts"', 'id="editCostLine"',
            'id="tbVideo"', 'id="tbTranscript"', 'id="tbEditor"',
            'id="tbTimeline"', 'id="tbDownload"',
            "Beta", "Changing a switch costs nothing",
            "Before you start",
        ):
            self.assertIn(marker, self.html)

    def test_edit_screen_calls_only_existing_endpoints(self):
        for route in (
            "/api/intake/begin", "/api/intake/file", "/api/intake/finish",
            "/api/intake/first-edit", "/api/intake/status",
            "/api/load-sample", "/api/job", "/api/thread",
            "/api/thread/message", "/api/thread/action",
            "/api/gold-verdict", "/api/word-toggle", "/api/word-range",
            "/api/gap-erase", "/api/timeline/split", "/api/timeline/trim",
            "/api/timeline/move", "/api/timeline/delete",
            "/api/timeline/paste", "/api/timeline/fade",
            "/api/history/undo", "/api/history/redo",
        ):
            self.assertIn(route, self.html)

    def test_no_speech_output(self):
        self.assertEqual(
            self.html.count("speechSynthesis")
            + self.html.count("SpeechSynthesisUtterance"),
            0,
        )


class StudioEditEndpoints(unittest.TestCase):
    """The endpoints the Edit screen calls answer on a loaded job."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tye-studio-edit-")
        launch = Path(cls.tmp) / "launch"
        launch.mkdir(parents=True)
        job = make_job(Path(cls.tmp) / "job")
        config = WebAppConfig(job, launch_home=launch)
        cls.httpd = create_server(config, host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get_json(self, path):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{path}", timeout=10
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post_json(self, path, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_job_state_carries_edit_surface_data(self):
        data = self.get_json("/api/job")
        self.assertTrue(data["has_job"])
        self.assertIn("transcript_words", data)
        self.assertIn("timeline_pieces", data)
        self.assertIn("comments", data)
        self.assertGreaterEqual(data["words_total"], 10)

    def test_thread_answers_empty_to_start(self):
        data = self.get_json("/api/thread")
        self.assertIn("entries", data)

    def test_word_toggle_round_trip(self):
        status, _ = self.post_json("/api/word-toggle", {"word_index": 0})
        self.assertEqual(status, 200)
        data = self.get_json("/api/job")
        entry = next(e for e in data["transcript_words"] if e["index"] == 0)
        self.assertEqual(entry["status"], "cut")
        status, _ = self.post_json("/api/history/undo", {})
        self.assertEqual(status, 200)

    def test_timeline_split_and_undo(self):
        before = len(self.get_json("/api/job")["timeline_pieces"])
        status, _ = self.post_json("/api/timeline/split", {"output_time": 1.2})
        self.assertEqual(status, 200)
        after = len(self.get_json("/api/job")["timeline_pieces"])
        self.assertGreater(after, before)
        status, _ = self.post_json("/api/history/undo", {})
        self.assertEqual(status, 200)
        back = len(self.get_json("/api/job")["timeline_pieces"])
        self.assertEqual(back, before)


if __name__ == "__main__":
    unittest.main()
