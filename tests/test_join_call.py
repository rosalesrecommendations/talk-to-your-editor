"""Tests for the join spot's call (P1 item 10, 2026-11-04).

A join the join check left for the creator settles through
POST /api/join-call: "smooth" applies the long crossfade the
check itself uses for abrupt joins (through the timeline fade
path, so Undo covers it), "keep" changes no sound. Either way
the spot leaves the smooth pass's your-call list.

Run from the repository root:
    PYTHONPATH=src python3 -m unittest tests.test_join_call
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
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.webapp import WebAppConfig, create_server


def timed_words(count: int) -> list:
    return [
        {"word": f"word{i}", "start": float(i), "end": float(i) + 0.9}
        for i in range(count)
    ]


class JoinCallCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-join-call-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"
        self.job.mkdir(parents=True)
        words = timed_words(20)
        transcript = {"engine": "test", "duration": 19.9, "words": words}
        cuts = {
            "source_duration_s": 19.9,
            "kept_ranges": [{"start": 0.0, "end": 19.9}],
        }
        (self.job / "transcript.json").write_text(
            json.dumps(transcript), encoding="utf-8"
        )
        (self.job / "cuts.json").write_text(
            json.dumps(cuts), encoding="utf-8"
        )
        self.config = WebAppConfig(self.job)
        session = self.config.session_dir
        session.mkdir(parents=True, exist_ok=True)
        revised = {
            "source_duration_s": 19.9,
            "kept_ranges": [
                {"start": 0.0, "end": 9.9},
                {"start": 10.0, "end": 19.9},
            ],
        }
        (session / "revised_cuts.json").write_text(
            json.dumps(revised), encoding="utf-8"
        )
        self.spot = {
            "join_index": 0,
            "kinds": ["loudness_jump"],
            "time_s": 10.0,
            "output_time_s": 10.0,
            "detail": "level steps by 9.0 dB across the cut",
        }
        self.write_smooth([self.spot])
        self.server = create_server(self.config, host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_smooth(self, calls: list) -> None:
        payload = {"ran": True, "your_call": calls}
        (self.config.session_dir / "smooth_pass.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def join_fade(self) -> float:
        revised = json.loads(
            (self.config.session_dir / "revised_cuts.json").read_text(
                encoding="utf-8"
            )
        )
        ranges = revised.get("kept_ranges") or []
        if len(ranges) < 2:
            return 0.0
        return float(ranges[1].get("fade_in_s") or 0.0)

    def read_smooth_calls(self) -> list:
        payload = json.loads(
            (self.config.session_dir / "smooth_pass.json").read_text(
                encoding="utf-8"
            )
        )
        return payload.get("your_call") or []

    def post_json(self, path, data):
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_keep_settles_the_spot_without_touching_sound(self):
        status, result = self.post_json(
            "/api/join-call", {"join_index": 0, "move": "keep"}
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertEqual(self.read_smooth_calls(), [])
        self.assertEqual(self.join_fade(), 0.0)

    def test_smooth_applies_the_long_crossfade_and_settles(self):
        status, result = self.post_json(
            "/api/join-call", {"join_index": 0, "move": "smooth"}
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertEqual(self.read_smooth_calls(), [])
        self.assertAlmostEqual(self.join_fade(), 0.1)

    def test_a_settled_spot_refuses_a_second_call(self):
        status, _ = self.post_json(
            "/api/join-call", {"join_index": 0, "move": "keep"}
        )
        self.assertEqual(status, 200)
        status, result = self.post_json(
            "/api/join-call", {"join_index": 0, "move": "smooth"}
        )
        self.assertEqual(status, 400, result)

    def test_a_bad_move_is_refused(self):
        status, result = self.post_json(
            "/api/join-call", {"join_index": 0, "move": "delete"}
        )
        self.assertEqual(status, 400, result)
        self.assertEqual(len(self.read_smooth_calls()), 1)


if __name__ == "__main__":
    unittest.main()
