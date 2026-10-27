"""Tests for the v0.4 additions (stdlib unittest, no dependencies).

Spins the real HTTP server on an ephemeral port against a temp job dir
built from the sample fixtures, following tests/test_webapp.py, then
drives it with urllib: the front door, edit spans and notes on edits,
grades and the rules they offer, and the comment estimate marker. The
approximate timeline mapping is covered with plain unit tests.
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig, create_server

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VIDEO_BYTES = bytes(range(256)) * 32  # 8192 deterministic dummy bytes


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def graded_entry(comment_id, operation_type, verdict="worked", **extra):
    entry = {
        "comment_id": comment_id,
        "ts": 6.0,
        "original_text": "Remove this repeated line, we already said it.",
        "status": "applied",
        "action_taken": "Removed the words this note pointed at.",
        "operation_type": operation_type,
        "verdict": verdict,
        "notes": "",
    }
    entry.update(extra)
    return entry


class V04Tests(unittest.TestCase):
    # Each test gets its own job and server, like test_webapp.py:
    # front-door runs, grades, and rules all build on session state,
    # so isolation keeps every baseline the job's own.
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v04-"))
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

    def call(self, path, data=None):
        payload = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(
            self.base + path,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def summary(self):
        status, payload = self.call("/api/job")
        self.assertEqual(status, 200, payload)
        return payload

    def save_fixture_comments(self):
        feedback = load_fixture("sample_feedback.json")
        comments = [
            {**comment, "scope": "both"}
            for comment in feedback["comments"]
        ]
        status, payload = self.call(
            "/api/comments",
            {"video": feedback["video"], "comments": comments},
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        return payload

    def front_door_with_fixture_comments(self):
        self.save_fixture_comments()
        status, result = self.call("/api/front-door", {})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        return result

    def repeated_span(self, summary):
        for span in summary["edit_spans"]:
            if span["word_start"] == 16 and span["word_end"] == 24:
                return span
        self.fail(f"no edit span at words 16..24 in {summary['edit_spans']!r}")

    # -- front door --------------------------------------------------------

    def test_front_door_defaults(self):
        summary = self.summary()
        self.assertEqual(
            summary["front_door"],
            {"remove_retakes": True, "cut_silence": True, "buffer_s": 0.1},
        )
        self.assertEqual(summary["saved_rules"], [])
        self.assertEqual(summary["rule_suggestions"], [])
        self.assertEqual(summary["edit_spans"], [])

    def test_front_door_fire(self):
        self.assertEqual(self.summary()["words_kept"], 32)
        result = self.front_door_with_fixture_comments()
        applied = [
            comment_id
            for run in result["direction_runs"]
            for comment_id in run["applied_comment_ids"]
        ]
        self.assertIn("c1", applied)
        self.assertIsNotNone(result["silence"])
        self.assertTrue(result["silence"]["ran"])
        self.assertGreaterEqual(result["silence"]["joins_trimmed"], 1)
        after = self.summary()
        self.assertLess(after["words_kept"], 32)
        # A second read of the job shows the choices the run saved.
        second = self.summary()
        self.assertEqual(second["front_door"]["buffer_s"], 0.1)

    def test_front_door_buffer_clamp(self):
        status, result = self.call("/api/front-door", {"buffer_s": 5})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["settings"]["buffer_s"], 0.5)
        status, result = self.call("/api/front-door", {"buffer_s": 0.01})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["settings"]["buffer_s"], 0.05)

    def test_front_door_silence_only_without_comments(self):
        status, result = self.call(
            "/api/front-door",
            {"remove_retakes": False, "cut_silence": True},
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertIsNotNone(result["silence"])
        self.assertTrue(result["silence"]["ran"])
        self.assertGreaterEqual(result["silence"]["joins_trimmed"], 1)

    # -- edit spans and notes ----------------------------------------------

    def test_edit_spans_after_front_door(self):
        self.front_door_with_fixture_comments()
        span = self.repeated_span(self.summary())
        self.assertEqual(
            span["removed_text"], "This part is hard to say out loud."
        )
        self.assertEqual(span["source"], "direction")
        # The reason is a plain sentence, never a machine code.
        self.assertIsNone(re.search(r"[a-z]+_[a-z]+", span["reason"]))

    def test_edit_note_round_trip_and_audit(self):
        self.front_door_with_fixture_comments()
        span = self.repeated_span(self.summary())
        note_text = "Keep making this kind of cut."
        status, payload = self.call(
            "/api/edit-note",
            {"edit_id": span["edit_id"], "text": note_text},
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["edit_id"], span["edit_id"])
        self.assertEqual(len(payload["notes"]), 1)
        self.assertEqual(payload["notes"][0]["text"], note_text)

        span_after = self.repeated_span(self.summary())
        self.assertEqual(len(span_after["notes"]), 1)
        self.assertEqual(span_after["notes"][0]["text"], note_text)

        status, payload = self.call(
            "/api/edit-note",
            {"edit_id": "edit-999-1000", "text": note_text},
        )
        self.assertEqual(status, 400)
        self.assertIn("error", payload)
        status, payload = self.call(
            "/api/edit-note", {"edit_id": span["edit_id"], "text": ""}
        )
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

        status, result = self.call(
            "/api/direct", {"provider": "mock", "format": "vertical"}
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        audit = json.loads(
            (self.job / "tye-session" / "operations_audit.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(
            any(
                item.get("removed_text") == span["removed_text"]
                and item.get("note") == note_text
                for item in audit.get("edit_notes", [])
            ),
            audit.get("edit_notes"),
        )

    # -- grades and rules ----------------------------------------------------

    def test_grades_threshold_then_rule_save_and_remove(self):
        self.save_fixture_comments()
        entries = [
            graded_entry("g1", "cut_words", ts=6.0),
            graded_entry("g2", "cut_words", ts=10.0),
        ]
        status, payload = self.call("/api/grades", {"entries": entries})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["suggestions"], [])

        status, payload = self.call(
            "/api/grades",
            {"entries": [graded_entry("g3", "cut_words", ts=12.0)]},
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(payload["suggestions"]), 1)
        suggestion = payload["suggestions"][0]
        self.assertEqual(suggestion["pattern_key"], "cut_words")
        self.assertEqual(suggestion["support_count"], 3)
        statement = suggestion["statement"]

        status, payload = self.call(
            "/api/rule-save", {"pattern_key": "cut_words"}
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["saved_rules"]), 1)
        self.assertEqual(payload["saved_rules"][0]["pattern_key"], "cut_words")
        self.assertEqual(payload["saved_rules"][0]["statement"], statement)
        rule_id = payload["saved_rules"][0]["rule_id"]

        status, result = self.call("/api/direct", {"provider": "mock"})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        audit = json.loads(
            (self.job / "tye-session" / "operations_audit.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn(statement, audit["saved_rules"])

        status, payload = self.call(
            "/api/rule-remove", {"rule_id": rule_id}
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["saved_rules"], [])

    def test_explicit_rule_request_suggests_immediately(self):
        entry = graded_entry(
            "e1",
            "set_join_pause",
            ts=10.0,
            rule_request=True,
        )
        status, payload = self.call("/api/grades", {"entries": [entry]})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        matches = [
            s
            for s in payload["suggestions"]
            if s["pattern_key"] == "set_join_pause"
        ]
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]["explicit"])

    def test_rule_dismiss_clears_suggestion(self):
        entries = [
            graded_entry("g1", "cut_words", ts=6.0),
            graded_entry("g2", "cut_words", ts=10.0),
            graded_entry("g3", "cut_words", ts=12.0),
        ]
        status, payload = self.call("/api/grades", {"entries": entries})
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(payload["suggestions"]), 1)

        status, payload = self.call(
            "/api/rule-dismiss", {"pattern_key": "cut_words"}
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["suggestions"], [])
        self.assertEqual(self.summary()["rule_suggestions"], [])

    # -- comment estimate marker ---------------------------------------------

    def test_comment_time_is_estimate_marker(self):
        comment = {
            "id": "m1",
            "ts": 6.0,
            "fmt": "vertical",
            "text": "Remove this repeated line, we already said it.",
        }
        status, payload = self.call(
            "/api/comments", {"video": "job", "comments": [comment]}
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        saved = json.loads(
            (self.job / "tye-session" / "feedback.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(saved["comments"]), 1)
        self.assertIs(saved["comments"][0]["time_is_estimate"], True)


class ApproxMappingTests(unittest.TestCase):
    """Timeline.map_output_time_approx against the sample fixtures.

    The fixture's output spans: r0 [0, 4.9), the special insert
    [4.9, 5.2), r2 [5.2, 10.2) holding words 16..23, r3 [10.2, 15.1),
    r4 [15.1, 18.1), r5 [18.1, 19.8).
    """

    def timeline(self):
        return Timeline(
            load_fixture("sample_transcript.json"),
            load_fixture("sample_cuts.json"),
        )

    def test_exact_time_in_second_range(self):
        result = self.timeline().map_output_time_approx(6.0)
        self.assertIsNotNone(result)
        self.assertFalse(result["approximate"])
        self.assertIn(result["word_index"], range(16, 24))

    def test_time_inside_special_segment_resolves_to_nearby_word(self):
        result = self.timeline().map_output_time_approx(5.05)
        self.assertIsNotNone(result)
        self.assertTrue(result["approximate"])
        self.assertIsNotNone(result["word_index"])
        self.assertFalse(result["special"])

    def test_exact_time_in_last_range(self):
        result = self.timeline().map_output_time_approx(19.7)
        self.assertIsNotNone(result)
        self.assertFalse(result["approximate"])
        self.assertIsNotNone(result["word_index"])

    def test_time_far_beyond_the_edit_is_none(self):
        timeline = self.timeline()
        self.assertIsNone(timeline.map_output_time_approx(25.0))
        self.assertIsNone(
            timeline.map_output_time_approx(25.0, window_s=0.5)
        )


if __name__ == "__main__":
    unittest.main()
