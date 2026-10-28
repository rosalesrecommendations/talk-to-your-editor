"""Tests for v0.10 item 4: test-video marking (stdlib unittest).

A job marked as a test video teaches the app nothing. The mark is
one session file (``test_video.json``), and the learning stop sits
at the two places every learning signal passes: ``_save_grades``
writes nothing for a test job (the grades endpoint and gold
verdict grades both write through it), and ``_rule_suggestions``
offers nothing for a test job, even from grades saved before the
mark was set (those grades stay on disk; only the surfacing is
stopped). A gold verdict still settles its span: settling is edit
state, not learning. The mark is not one of the checkpointed state
files, so undo never changes it. A mark set at the front door, in
the launch session, carries into the session of the job a drop
becomes.
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
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import checkpoints, webapp
from talk_to_your_editor.webapp import WebAppConfig, create_server

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
)
VIDEO_BYTES = bytes(range(256)) * 32
BANNER_TEXT = "Test video: the app will not learn from this one."
SWITCH_TEXT = "This is a test video. The app will not learn from it."


def page_html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def write_job(job: Path, words: list) -> None:
    job.mkdir(parents=True, exist_ok=True)
    transcript = {
        "engine": "test",
        "duration": float(words[-1]["end"]) if words else 0.0,
        "words": words,
    }
    cuts = {
        "source_duration_s": transcript["duration"],
        "kept_ranges": [{"start": 0.0, "end": transcript["duration"]}],
    }
    (job / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")


def graded_entry(record_id, pattern_key="cut_words"):
    return {
        "id": record_id,
        "record_id": record_id,
        "comment_id": "review",
        "operation_type": "cut_words",
        "verdict": "worked",
        "pattern_key": pattern_key,
        "pattern_description": "taking out a repeated line",
        "feedback_text": "this repeat adds nothing",
        "text": "this repeat adds nothing",
    }


def three_grades():
    return [graded_entry(f"g{i}") for i in (1, 2, 3)]


def gold_candidate(word_start, word_end, kind="negation"):
    operation = {
        "type": "cut_words",
        "comment_id": "direction-pass",
        "word_start": word_start,
        "word_end": word_end,
        "reason": "Held for the creator.",
        "confidence": 0.9,
        "model": "muse-spark-1.3",
        "risk_tier": 2,
        "wonder_reviewed": True,
    }
    return {
        "gold_id": webapp._gold_id(word_start, word_end, kind),
        "word_start": word_start,
        "word_end": word_end,
        "kind": kind,
        "source": "direction",
        "reason": "This part includes a negative statement.",
        "text": "some held words",
        "keep_text": None,
        "keep_word_start": None,
        "keep_word_end": None,
        "pair_id": None,
        "pair_key": None,
        "operation": operation,
        "status": "open",
        "note": None,
        "created_at": webapp._utc_now(),
        "decided_at": None,
    }


class IsolatedHomeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v10-testvideo-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_config(self) -> WebAppConfig:
        tokens = [f"word{i}" for i in range(80)]
        tokens[3] = "friends."
        write_job(
            self.job,
            [
                {"word": token, "start": float(i), "end": float(i) + 0.9}
                for i, token in enumerate(tokens)
            ],
        )
        return WebAppConfig(self.job)


# ---------------------------------------------------------------------------
# The mark itself
# ---------------------------------------------------------------------------


class MarkTests(IsolatedHomeCase):
    def test_mark_round_trips_in_its_own_session_file(self):
        config = self.make_config()
        self.assertFalse(webapp._is_test_video(config))
        webapp._set_test_video(config, True)
        self.assertTrue(webapp._is_test_video(config))
        payload = json.loads(
            (config.session_dir / webapp.TEST_VIDEO_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(payload, {"test_video": True})
        self.assertEqual(webapp.TEST_VIDEO_FILENAME, "test_video.json")
        webapp._set_test_video(config, False)
        self.assertFalse(webapp._is_test_video(config))

    def test_mark_is_not_a_checkpointed_state_file(self):
        self.assertNotIn(
            webapp.TEST_VIDEO_FILENAME, checkpoints.STATE_FILES
        )


# ---------------------------------------------------------------------------
# The two learning stops
# ---------------------------------------------------------------------------


class GradeGateTests(IsolatedHomeCase):
    def test_save_grades_writes_nothing_while_marked(self):
        config = self.make_config()
        webapp._set_test_video(config, True)
        webapp._save_grades(config, three_grades())
        self.assertFalse(
            (config.session_dir / "grades.json").is_file()
        )
        self.assertEqual(webapp._load_grades(config), [])

    def test_preseeded_grades_surface_nothing_while_marked(self):
        config = self.make_config()
        # Grades saved before the marking, while the mark is off.
        webapp._save_grades(config, three_grades())
        self.assertEqual(len(webapp._rule_suggestions(config)), 1)

        webapp._set_test_video(config, True)
        self.assertEqual(webapp._rule_suggestions(config), [])
        # What was already saved stays on disk, word for word.
        self.assertEqual(len(webapp._load_grades(config)), 3)
        self.assertTrue((config.session_dir / "grades.json").is_file())

        # Turning the mark back off surfaces the patterns again.
        webapp._set_test_video(config, False)
        suggestions = webapp._rule_suggestions(config)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["support_count"], 3)

    def test_learning_behaves_as_before_while_unmarked(self):
        # The existing behavior, unchanged: three worked grades of
        # one pattern surface one suggestion, and the grades land
        # in the session's grades file.
        config = self.make_config()
        self.assertFalse(webapp._is_test_video(config))
        webapp._save_grades(config, three_grades()[:2])
        self.assertEqual(webapp._rule_suggestions(config), [])
        webapp._save_grades(config, three_grades())
        stored = json.loads(
            (config.session_dir / "grades.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(stored["entries"]), 3)
        suggestions = webapp._rule_suggestions(config)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["pattern_key"], "cut_words")


class GoldGateTests(IsolatedHomeCase):
    def test_leave_verdict_settles_the_span_but_writes_no_grade(self):
        config = self.make_config()
        webapp._record_gold_spans(config, [gold_candidate(40, 45)])
        webapp._set_test_video(config, True)
        result = webapp._gold_verdict(
            config, "gold-40-45-negation", "leave", "on purpose"
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        # The span is settled: it is never offered again.
        self.assertEqual(webapp._open_gold_spans(config), [])
        records = webapp._load_gold(config)
        self.assertEqual(records[0]["status"], "left")
        # But no grade was recorded for it.
        self.assertEqual(webapp._load_grades(config), [])
        self.assertFalse((config.session_dir / "grades.json").is_file())
        self.assertEqual(result["suggestions"], [])


class UndoTests(IsolatedHomeCase):
    def test_undo_does_not_clear_the_mark(self):
        config = self.make_config()
        # One checkpointed change (grades, while unmarked)...
        checkpoints.begin(config)
        webapp._save_grades(config, three_grades())
        checkpoints.commit(config, "Grades saved")
        # ...then the mark is set, outside the checkpoints.
        webapp._set_test_video(config, True)
        state = checkpoints.undo(config)
        self.assertIsNotNone(state)
        # The change walked back, and the mark still stands.
        self.assertEqual(webapp._load_grades(config), [])
        self.assertTrue(webapp._is_test_video(config))
        self.assertTrue(
            (config.session_dir / webapp.TEST_VIDEO_FILENAME).is_file()
        )


# ---------------------------------------------------------------------------
# Over HTTP: the endpoint, the grades stop, the summary, the page
# ---------------------------------------------------------------------------


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v10-tv-http-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.job = self.tmp / "job"
        self.job.mkdir()
        for name, dest in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / name, self.job / dest)
        (self.job / "review.mp4").write_bytes(VIDEO_BYTES)
        self.config = WebAppConfig(self.job)
        self.server = create_server(self.config, "127.0.0.1", 0)
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

    def page(self):
        with urllib.request.urlopen(self.base + "/", timeout=10) as resp:
            return resp.read().decode("utf-8")

    def summary(self):
        status, payload = self.call("/api/job")
        self.assertEqual(status, 200, payload)
        return payload


class EndpointAndSummaryTests(ServerCase):
    def test_endpoint_sets_the_mark_and_the_summary_carries_it(self):
        self.assertFalse(self.summary()["test_video"])
        status, payload = self.call("/api/test-video", {"test": True})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["test_video"])
        self.assertTrue(self.summary()["test_video"])
        self.assertTrue(webapp._is_test_video(self.config))

        status, payload = self.call("/api/test-video", {"test": False})
        self.assertEqual(status, 200, payload)
        self.assertFalse(payload["test_video"])
        self.assertFalse(self.summary()["test_video"])

    def test_endpoint_needs_a_plain_yes_or_no(self):
        status, payload = self.call("/api/test-video", {})
        self.assertEqual(status, 400, payload)
        self.assertIn("error", payload)
        self.assertFalse(webapp._is_test_video(self.config))

    def test_posting_grades_saves_nothing_while_marked(self):
        status, payload = self.call("/api/test-video", {"test": True})
        self.assertEqual(status, 200, payload)
        status, payload = self.call(
            "/api/grades", {"entries": three_grades()}
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["suggestions"], [])
        self.assertFalse(
            (self.job / "tye-session" / "grades.json").is_file()
        )
        self.assertEqual(self.summary()["rule_suggestions"], [])

    def test_served_page_carries_the_banner_and_switches(self):
        status, _payload = self.call("/api/test-video", {"test": True})
        self.assertEqual(status, 200)
        page = self.page()
        self.assertIn(BANNER_TEXT, page)
        self.assertIn(SWITCH_TEXT, page)
        self.assertIn('id="testVideoBanner"', page)
        self.assertIn('id="jobTestVideo"', page)
        self.assertIn('id="dropTestVideo"', page)
        self.assertIn("/api/test-video", page)


class TemplateTests(unittest.TestCase):
    def test_both_switches_carry_the_plain_words(self):
        html = page_html()
        # One in the drop block, one on the job page.
        self.assertGreaterEqual(html.count(SWITCH_TEXT), 2)
        self.assertIn('id="dropTestVideo"', html)
        self.assertIn('id="jobTestVideo"', html)
        self.assertIn('postJson("/api/test-video", { test: on })', html)

    def test_banner_line_is_exact(self):
        html = page_html()
        self.assertIn(BANNER_TEXT, html)
        self.assertIn('id="testVideoBanner"', html)


# ---------------------------------------------------------------------------
# The front door: a mark in the launch session becomes the job's
# ---------------------------------------------------------------------------


class FrontDoorServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v10-tv-door-"))
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

    def request(self, path, data=None, raw=None):
        headers = {}
        payload = raw
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        method = "POST" if (data is not None or raw is not None) else "GET"
        req = urllib.request.Request(
            self.base + path, data=payload, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def post_json(self, path, data):
        status, body = self.request(path, data=data)
        return status, json.loads(body.decode("utf-8"))

    def get_json(self, path):
        status, body = self.request(path)
        self.assertEqual(status, 200, body)
        return json.loads(body.decode("utf-8"))

    def drop_job(self):
        status, begun = self.post_json(
            "/api/intake/begin", {"name": "dropped-job"}
        )
        self.assertEqual(status, 200, begun)
        intake_id = begun["intake_id"]
        for relative, fixture in (
            ("dropped-job/transcript.json", "sample_transcript.json"),
            ("dropped-job/cuts.json", "sample_cuts.json"),
            ("dropped-job/profile.json", "sample_profile.json"),
        ):
            url = (
                "/api/intake/file?intake=" + quote(intake_id)
                + "&path=" + quote(relative)
            )
            status, body = self.request(
                url, raw=(FIXTURES / fixture).read_bytes()
            )
            self.assertEqual(status, 200, body)
        status, finished = self.post_json(
            "/api/intake/finish", {"intake_id": intake_id}
        )
        self.assertEqual(status, 200, finished)
        self.assertEqual(finished["kind"], "job")
        return finished

    def test_no_job_summary_is_not_a_test_video(self):
        self.assertFalse(self.get_json("/api/job")["test_video"])

    def test_launch_mark_carries_into_a_dropped_job(self):
        # The mark is settable with no job loaded, in the launch
        # session...
        status, payload = self.post_json("/api/test-video", {"test": True})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["test_video"])
        launch_mark = (
            self.home / "tye-session" / webapp.TEST_VIDEO_FILENAME
        )
        self.assertTrue(launch_mark.is_file())

        # ...and when the drop becomes the job, the job's own
        # session carries the same mark.
        self.drop_job()
        summary = self.get_json("/api/job")
        self.assertTrue(summary["has_job"])
        self.assertTrue(summary["test_video"])
        staged = list(
            (self.home / "tye-session" / "intake").glob(
                "dropped-job-*/dropped-job"
            )
        )
        self.assertEqual(len(staged), 1)
        job_mark = (
            staged[0] / "tye-session" / webapp.TEST_VIDEO_FILENAME
        )
        self.assertTrue(job_mark.is_file())
        self.assertEqual(
            json.loads(job_mark.read_text(encoding="utf-8")),
            {"test_video": True},
        )

    def test_no_launch_mark_leaves_a_dropped_job_unmarked(self):
        self.drop_job()
        summary = self.get_json("/api/job")
        self.assertTrue(summary["has_job"])
        self.assertFalse(summary["test_video"])


class AdoptJobTests(IsolatedHomeCase):
    def test_first_edit_swap_carries_the_launch_mark(self):
        # The first-edit path swaps the config through _adopt_job,
        # not through the drop handler; the mark carries there too.
        launch_home = self.tmp / "launch"
        launch = WebAppConfig(None, launch_home=launch_home)
        webapp._set_test_video(launch, True)
        self.make_config()
        httpd = SimpleNamespace(config=launch)
        config = webapp._adopt_job(httpd, self.job)
        self.assertTrue(webapp._is_test_video(config))
        self.assertIs(httpd.config, config)


if __name__ == "__main__":
    unittest.main()
