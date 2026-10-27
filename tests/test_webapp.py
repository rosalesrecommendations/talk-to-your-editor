"""Tests for the v0.2 local web test app (stdlib unittest, no dependencies).

Spins the real HTTP server on an ephemeral port against a temp job dir
built from the sample fixture, then drives it with urllib: job summary,
comment save, mock direction, downloads, after-review, and video range
requests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
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

from talk_to_your_editor import webapp as webapp_module
from talk_to_your_editor.meta_provider import MetaProvider, ProviderError
from talk_to_your_editor.schema import SchemaError
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import (
    WebAppConfig,
    _feedback_for_run,
    _plain_direction_error,
    create_server,
)
from talk_to_your_editor.word_corrections import transcript_with_corrections

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VIDEO_BYTES = bytes(range(256)) * 32  # 8192 deterministic dummy bytes


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class WebAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="tye-webapp-"))
        cls.job = cls.tmp / "job"
        cls.job.mkdir()
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
            shutil.copyfile(FIXTURES / name, cls.job / target)
        (cls.job / "review.mp4").write_bytes(VIDEO_BYTES)
        cls.original_cuts = (cls.job / "cuts.json").read_bytes()
        cls.original_transcript = (cls.job / "transcript.json").read_bytes()
        cls.server = create_server(
            WebAppConfig(cls.job), host="127.0.0.1", port=0
        )
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- helpers -----------------------------------------------------------

    def request(self, path, data=None, headers=None):
        url = self.base + path
        payload = None
        hdrs = dict(headers or {})
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(
            url, data=payload, headers=hdrs, method="POST" if data is not None else "GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
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

    def save_fixture_comments(self):
        feedback = load_fixture("sample_feedback.json")
        status, payload = self.post_json(
            "/api/comments",
            {"video": feedback["video"], "comments": feedback["comments"]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        return payload

    # -- tests ---------------------------------------------------------------

    def test_index_page_served(self):
        status, _, body = self.request("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        self.assertIn("Talk to Your Editor", page)
        self.assertIn("job", page)  # the job dir name
        self.assertNotIn("{{JOB_NAME}}", page)
        self.assertNotIn("{{VIDEO_JSON}}", page)

    def test_job_summary_shape(self):
        summary = self.get_json("/api/job")
        for key in (
            "job_name",
            "source_duration_s",
            "output_duration_s",
            "kept_ranges",
            "words_total",
            "words_kept",
            "words_cut",
            "videos",
            "feedback_exists",
            "meta_available",
        ):
            self.assertIn(key, summary)
        self.assertEqual(summary["words_kept"], 32)
        self.assertEqual(
            summary["words_cut"], summary["words_total"] - 32
        )
        self.assertEqual(summary["videos"], ["review.mp4"])
        self.assertIsInstance(summary["meta_available"], bool)

    def test_comment_save_then_direct_mock(self):
        payload = self.save_fixture_comments()
        self.assertEqual(payload["saved"], 4)
        session_feedback = self.job / "tye-session" / "feedback.json"
        self.assertTrue(session_feedback.is_file())
        saved = json.loads(session_feedback.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["comments"]), 4)
        self.assertEqual(saved["comments"][0]["id"], "c1")

        status, result = self.post_json("/api/direct", {"provider": "mock"})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertIn("c1", result["applied_comment_ids"])
        # c2 and c3 point at protected / negation-bearing spans in the
        # fixture, so the verifier flags them instead of applying.
        self.assertIn("c2", result["flagged_comment_ids"])
        self.assertIn("c3", result["flagged_comment_ids"])
        self.assertEqual(result["before"]["kept_words"], 32)
        self.assertLess(
            result["after"]["kept_words"], result["before"]["kept_words"]
        )
        statuses = {
            op["comment_id"]: op["status"] for op in result["operations"]
        }
        self.assertEqual(statuses["c1"], "applied")
        self.assertEqual(statuses["c2"], "flagged")

        session = self.job / "tye-session"
        for name in (
            "operations.json",
            "operations_audit.json",
            "revised_cuts.json",
            "after-review.html",
        ):
            self.assertTrue((session / name).is_file(), name)
        # The job's own files are never touched.
        self.assertEqual((self.job / "cuts.json").read_bytes(), self.original_cuts)
        self.assertEqual(
            (self.job / "transcript.json").read_bytes(),
            self.original_transcript,
        )

    def test_direct_overlapping_notes_finish_one_applies_one_flagged(self):
        # Regression: two notes whose edits share words (both point at
        # the same sentence, so the mock proposes the same cut twice)
        # used to kill the whole run with a raw schema code. The run
        # now completes: the first applies, the second is flagged on
        # its own note with a plain reason.
        comments = [
            {
                "id": "x1",
                "ts": 6.0,
                "fmt": "vertical",
                "text": "Remove this repeated line, we already said it.",
            },
            {
                "id": "x2",
                "ts": 6.2,
                "fmt": "vertical",
                "text": "Cut this part too please.",
            },
        ]
        status, payload = self.post_json(
            "/api/comments", {"video": "job", "comments": comments}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

        status, result = self.post_json("/api/direct", {"provider": "mock"})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["applied_comment_ids"], ["x1"])
        self.assertEqual(result["flagged_comment_ids"], ["x2"])
        self.assertEqual(result["before"]["kept_words"], 32)
        self.assertEqual(result["after"]["kept_words"], 24)
        by_id = {op["comment_id"]: op for op in result["operations"]}
        self.assertEqual(by_id["x1"]["status"], "applied")
        self.assertEqual(by_id["x2"]["status"], "flagged")
        self.assertIn("overlaps another note", by_id["x2"]["plain_action"])
        # Nothing the viewer sees carries an internal error code.
        shown = json.dumps(result)
        self.assertNotIn("overlapping_operations", shown)
        self.assertNotIn("overlapping_operations", by_id["x2"]["plain_action"])
        self.assertNotIn("meta_api", shown)
        audit = json.loads(
            (self.job / "tye-session" / "operations_audit.json").read_text(
                encoding="utf-8"
            )
        )
        verdicts = {v["operation"]["comment_id"]: v for v in audit["verdicts"]}
        self.assertEqual(verdicts["x2"]["status"], "flag")
        self.assertTrue(
            any(
                "overlaps another note" in r for r in verdicts["x2"]["reasons"]
            )
        )

    def test_direction_error_messages_are_plain(self):
        refused = _plain_direction_error(ProviderError("meta_api_http_401"))
        self.assertIn("sign-in was not accepted", refused)
        self.assertNotIn("401", refused)
        self.assertNotIn("meta_api", refused)
        unreachable = _plain_direction_error(
            ProviderError("meta_api_unreachable:Name or service not known")
        )
        self.assertIn("could not be reached", unreachable)
        self.assertNotIn("meta_api", unreachable)
        invalid = _plain_direction_error(
            ProviderError("model_operations_invalid:overlapping_operations:x")
        )
        self.assertNotIn("overlapping_operations", invalid)
        self.assertNotIn("model_operations_invalid", invalid)
        schema = _plain_direction_error(
            SchemaError(["overlapping_operations:a and b share words 1..2"])
        )
        self.assertNotIn("overlapping_operations", schema)
        for message in (refused, unreachable, invalid, schema):
            self.assertNotIn("_operations", message)

    def test_index_uses_plain_labels_and_advanced_session_files(self):
        status, _, body = self.request("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        self.assertIn("Build the fixed video", page)
        self.assertIn("Grade the changes", page)
        self.assertNotIn("Render revised review", page)
        self.assertNotIn("Open after-review", page)
        # The session-file links sit behind the Advanced disclosure.
        self.assertIn("<details", page)
        self.assertIn("Advanced: session files", page)
        self.assertLess(
            page.index("Advanced: session files"),
            page.index("/api/download/feedback.json"),
        )

    def test_direct_without_comments_is_a_clean_error(self):
        empty_job = self.tmp / "empty-job"
        empty_job.mkdir(exist_ok=True)
        for src, dst in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
        ):
            shutil.copyfile(FIXTURES / src, empty_job / dst)
        server = create_server(
            WebAppConfig(empty_job), host="127.0.0.1", port=0
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/api/direct"
            req = urllib.request.Request(
                url,
                data=json.dumps({"provider": "mock"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=10)
            self.assertEqual(ctx.exception.code, 400)
            payload = json.loads(ctx.exception.read().decode("utf-8"))
            self.assertIn("error", payload)
        finally:
            server.shutdown()
            server.server_close()

    def test_download_whitelist(self):
        self.save_fixture_comments()
        status, payload = self.post_json("/api/direct", {"provider": "mock"})
        self.assertEqual(status, 200)
        status, headers, body = self.request(
            "/api/download/operations_audit.json"
        )
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        self.assertIn("applied_comment_ids", body.decode("utf-8"))
        for bad in (
            "/api/download/transcript.json",
            "/api/download/secret.json",
            "/api/download/..%2F..%2Fcuts.json",
            "/api/download/%2e%2e%2f%2e%2e%2fcuts.json",
        ):
            status, _, _ = self.request(bad)
            self.assertEqual(status, 404, bad)

    def test_after_review_served_after_run(self):
        self.save_fixture_comments()
        status, _ = self.post_json("/api/direct", {"provider": "mock"})
        self.assertEqual(status, 200)
        status, _, body = self.request("/after-review")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        self.assertIn("After review", page)
        self.assertIn("Remove this repeated line", page)

    def test_video_range_request(self):
        status, headers, body = self.request(
            "/video/review.mp4", headers={"Range": "bytes=100-199"}
        )
        self.assertEqual(status, 206)
        self.assertEqual(
            headers.get("Content-Range"),
            f"bytes 100-199/{len(VIDEO_BYTES)}",
        )
        self.assertEqual(headers.get("Accept-Ranges"), "bytes")
        self.assertEqual(len(body), 100)
        self.assertEqual(body, VIDEO_BYTES[100:200])
        # A full request still works, and unknown names are refused.
        status, _, body = self.request("/video/review.mp4")
        self.assertEqual(status, 200)
        self.assertEqual(body, VIDEO_BYTES)
        status, _, _ = self.request("/video/other.mp4")
        self.assertEqual(status, 404)
        status, _, _ = self.request("/video/..%2Fcuts.json")
        self.assertEqual(status, 404)

    def test_index_has_timeline_bar(self):
        status, _, body = self.request("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        # The bar sits directly under the player.
        self.assertIn('id="vid"', page)
        self.assertIn('id="tlbar"', page)
        self.assertIn('id="tlTrack"', page)
        self.assertIn('id="tlPlayhead"', page)
        self.assertLess(page.index('id="vid"'), page.index('id="tlbar"'))
        # One dot per saved comment, click seeks, drag moves the playhead.
        self.assertIn("renderDots", page)
        self.assertIn("tl-dot", page)
        self.assertIn("tl-tip", page)
        self.assertIn("seekTo(c.ts, c.fmt)", page)
        self.assertIn('tlTrack.addEventListener("pointerdown"', page)
        self.assertIn('tlTrack.addEventListener("pointermove"', page)

    def test_comment_timing_pins_on_typing_start(self):
        status, _, body = self.request("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        # The keep-playing switch is on the page, next to Save.
        self.assertIn('id="keepPlaying"', page)
        self.assertIn("Keep playing while I type", page)
        # Typing into an empty draft pins the note to the playhead at
        # that moment and pauses, unless the switch is on.
        self.assertIn('draft.addEventListener("input"', page)
        self.assertIn("pinDraft(vid.currentTime || 0)", page)
        self.assertIn("if (!keepPlayingEl.checked) { vid.pause(); }", page)
        # The note shows where it is pinned.
        self.assertIn('id="pinNote"', page)
        self.assertIn("Pinned at ", page)

    def test_comment_timing_never_inherits_a_stale_time(self):
        status, _, body = self.request("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        # The old save-time fallback (a leftover manual seconds value
        # silently becoming the next note's time) is gone.
        self.assertNotIn("? parseFloat(manual) :", page)
        self.assertNotIn("pendingTs", page)
        # Save clears both the pin and the manual seconds field.
        self.assertIn("pinnedTs = null;", page)
        self.assertIn('manualEl.value = "";', page)
        # Clearing the draft unpins, so the next note re-pins fresh.
        self.assertIn("draftWasEmpty", page)

    def test_index_has_keyboard_shortcuts(self):
        status, _, body = self.request("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        # One quiet line under the player lists the shared editor set,
        # with the switch that turns the shortcuts off.
        self.assertIn('id="shortcutLine"', page)
        self.assertIn("Shortcuts:", page)
        self.assertIn("M pin a note here", page)
        self.assertIn('id="shortcutToggle"', page)
        # The switch choice is remembered for the browser session.
        self.assertIn('sessionStorage.setItem("tyeShortcuts"', page)
        self.assertIn('sessionStorage.getItem("tyeShortcuts")', page)
        # The handler stays out of form fields, out of the timeline
        # drag, and out of modified key presses.
        self.assertIn('document.addEventListener("keydown"', page)
        self.assertIn("typingInField(e.target)", page)
        self.assertIn("if (!shortcutsOn || tlDragging", page)
        # Space toggles play (and is default-prevented, so it never
        # scrolls); J and L step five seconds; the arrows step one
        # second, five with Shift; K pauses; M pins a note in the box.
        self.assertIn(
            "if (vid.paused) { vid.play(); } else { vid.pause(); }", page
        )
        self.assertIn("stepBy(-5);", page)
        self.assertIn("stepBy(5);", page)
        self.assertIn("stepBy(e.shiftKey ? -5 : -1)", page)
        self.assertIn("stepBy(e.shiftKey ? 5 : 1)", page)
        self.assertIn('case "m":', page)
        self.assertIn("draft.focus();", page)

    def test_saved_comments_carry_dot_positions(self):
        # The timeline dots are placed from the job summary's comments,
        # so every saved comment needs a time inside the output length.
        self.save_fixture_comments()
        summary = self.get_json("/api/job")
        comments = summary["comments"]
        self.assertEqual(len(comments), 4)
        times = sorted(c["ts"] for c in comments)
        self.assertEqual(times, [6.0, 10.0, 12.2, 16.4])
        for c in comments:
            self.assertGreaterEqual(c["ts"], 0)
            self.assertLessEqual(c["ts"], summary["output_duration_s"])
            self.assertTrue(c["text"])

    def test_transcript_section_and_word_states(self):
        status, _, body = self.request("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        # The Transcript section is on the player page, with the two
        # word styles and the toggle endpoint wired in.
        self.assertIn('id="transcriptCard"', page)
        self.assertIn("<h2>Transcript</h2>", page)
        self.assertIn('id="transcriptWords"', page)
        self.assertIn(".tw-cut", page)
        self.assertIn("line-through", page)
        self.assertIn("renderTranscript", page)
        self.assertIn("/api/word-toggle", page)
        # The job summary carries every word with its state, kept and
        # cut styled differently by the page. Word 0 opens the edit
        # (kept in every flow) and word 8 is the cut first take (no
        # flow restores it), so both are stable on the shared server.
        summary = self.get_json("/api/job")
        words = summary["transcript_words"]
        self.assertEqual(len(words), summary["words_total"])
        by_index = {w["index"]: w for w in words}
        self.assertEqual(by_index[0]["word"], "Welcome")
        self.assertEqual(by_index[0]["status"], "kept")
        self.assertEqual(by_index[8]["status"], "cut")
        for w in words:
            self.assertIn(w["status"], ("kept", "cut", "special"))

    def test_word_toggle_round_trip(self):
        # Isolated job and server: toggles rewrite the session's
        # revised cuts, so they must not share state with other tests.
        job = self.tmp / "toggle-job"
        job.mkdir()
        for src, dst in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / src, job / dst)
        original_cuts = (job / "cuts.json").read_bytes()
        server = create_server(WebAppConfig(job), host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def call(path, data=None):
            payload = None if data is None else json.dumps(data).encode()
            req = urllib.request.Request(
                base + path,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST" if data is not None else "GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status, json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read().decode())

        def word_status(index):
            summary_status, summary = call("/api/job")
            self.assertEqual(summary_status, 200)
            return {
                w["index"]: w["status"] for w in summary["transcript_words"]
            }[index]

        def revised_status(index):
            revised = json.loads(
                (job / "tye-session" / "revised_cuts.json").read_text(
                    encoding="utf-8"
                )
            )
            timeline = Timeline(load_fixture("sample_transcript.json"), revised)
            return timeline.word_status(index)

        try:
            # Fixture state: word 20 is kept, word 8 is cut.
            self.assertEqual(word_status(20), "kept")
            self.assertEqual(word_status(8), "cut")

            # Clicking a kept word cuts it, in the page data and in
            # the session's revised cuts on disk.
            status, payload = call("/api/word-toggle", {"word_index": 20})
            self.assertEqual(status, 200, payload)
            self.assertTrue(payload["changed"])
            self.assertEqual(payload["status"], "cut")
            self.assertEqual(revised_status(20), "cut")
            self.assertEqual(word_status(20), "cut")

            # Clicking the now-struck word restores it.
            status, payload = call("/api/word-toggle", {"word_index": 20})
            self.assertEqual(status, 200, payload)
            self.assertTrue(payload["changed"])
            self.assertEqual(payload["status"], "kept")
            self.assertEqual(revised_status(20), "kept")
            self.assertEqual(word_status(20), "kept")

            # The other direction round-trips too: a word that
            # started cut comes back, then goes again.
            status, payload = call("/api/word-toggle", {"word_index": 8})
            self.assertEqual(status, 200, payload)
            self.assertTrue(payload["changed"])
            self.assertEqual(payload["status"], "kept")
            self.assertEqual(revised_status(8), "kept")
            status, payload = call("/api/word-toggle", {"word_index": 8})
            self.assertEqual(status, 200, payload)
            self.assertTrue(payload["changed"])
            self.assertEqual(payload["status"], "cut")
            self.assertEqual(revised_status(8), "cut")

            # A protected word (word 37 opens the profile-protected
            # closing span) stays put, with one plain sentence why.
            status, payload = call("/api/word-toggle", {"word_index": 37})
            self.assertEqual(status, 200, payload)
            self.assertFalse(payload["changed"])
            self.assertEqual(payload["status"], "kept")
            self.assertIn("protected", payload["message"])
            self.assertEqual(revised_status(37), "kept")

            # Bad indexes are clean errors, not crashes.
            status, _ = call("/api/word-toggle", {"word_index": 999})
            self.assertEqual(status, 400)
            status, _ = call("/api/word-toggle", {"word_index": "20"})
            self.assertEqual(status, 400)

            # The job's own cuts file is never touched.
            self.assertEqual((job / "cuts.json").read_bytes(), original_cuts)
        finally:
            server.shutdown()
            server.server_close()

    def test_render_reports_missing_configuration(self):
        status, payload = self.post_json("/api/render", {})
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(
            sorted(payload["missing"]),
            ["cleaned_wav", "render_py", "source_video"],
        )


class WordCorrectionTests(unittest.TestCase):
    """Word corrections: a session-only text layer over the transcript.

    Each test gets its own job and server, because corrections live in
    the session folder and must not leak between tests.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-correct-"))
        self.job = self.tmp / "job"
        self.job.mkdir()
        for src, dst in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / src, self.job / dst)
        self.original_transcript = (self.job / "transcript.json").read_bytes()
        self.original_cuts = (self.job / "cuts.json").read_bytes()
        self.server = create_server(
            WebAppConfig(self.job), host="127.0.0.1", port=0
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------

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

    def correct(self, start, end, text):
        return self.call(
            "/api/word-correct",
            {"word_start": start, "word_end": end, "text": text},
        )

    def summary(self):
        status, payload = self.call("/api/job")
        self.assertEqual(status, 200)
        return payload

    def entry_at(self, summary, index):
        for entry in summary["transcript_words"]:
            if entry["index"] == index:
                return entry
        self.fail(f"no transcript entry at index {index}")

    @property
    def corrections_file(self):
        return self.job / "tye-session" / "word_corrections.json"

    # -- tests -----------------------------------------------------------

    def test_page_has_correction_editor(self):
        status, _, body = self._raw_get("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        self.assertIn("Fix words", page)
        self.assertIn("Save correction", page)
        self.assertIn("Remove correction", page)
        self.assertIn("tw-corrected", page)
        self.assertIn("/api/word-correct", page)
        self.assertIn("/api/word-correct-remove", page)

    def _raw_get(self, path):
        req = urllib.request.Request(self.base + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_spelling_fix_one_word(self):
        # Word 2 is "to"; the viewer fixes it to "too".
        status, payload = self.correct(2, 3, "too")
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(
            payload["corrections"],
            [{"word_start": 2, "word_end": 3, "text": "too"}],
        )
        # Stored in the session folder, in the documented shape.
        stored = json.loads(self.corrections_file.read_text(encoding="utf-8"))
        self.assertEqual(
            stored["corrections"],
            [{"word_start": 2, "word_end": 3, "text": "too"}],
        )
        # The summary shows the corrected text in place, marked, with
        # the original alongside; nothing else about the entry moves.
        summary = self.summary()
        entry = self.entry_at(summary, 2)
        self.assertEqual(entry["word"], "too")
        self.assertTrue(entry["corrected"])
        self.assertEqual(entry["original"], "to")
        self.assertEqual(entry["word_end"], 3)
        self.assertEqual(summary["word_corrections"], payload["corrections"])
        self.assertEqual(len(summary["transcript_words"]), 40)
        # Neighbors are untouched and unmarked.
        self.assertEqual(self.entry_at(summary, 1)["word"], "back")
        self.assertFalse(self.entry_at(summary, 1)["corrected"])
        # The job's own files are never touched.
        self.assertEqual(
            (self.job / "transcript.json").read_bytes(),
            self.original_transcript,
        )
        self.assertEqual(
            (self.job / "cuts.json").read_bytes(), self.original_cuts
        )

    def test_split_one_word_into_three(self):
        # Word 4 is the single word "channel"; it should read as three.
        status, payload = self.correct(4, 5, "channel of ours")
        self.assertEqual(status, 200, payload)
        summary = self.summary()
        entry = self.entry_at(summary, 4)
        self.assertEqual(entry["word"], "channel of ours")
        self.assertTrue(entry["corrected"])
        self.assertEqual(entry["original"], "channel")
        self.assertEqual(entry["word_end"], 5)
        # One span still covers one index, so the entry count holds.
        self.assertEqual(len(summary["transcript_words"]), 40)
        # The transcript a direction run reads carries the three
        # words in the span's own slot, with its timing untouched.
        corrected = transcript_with_corrections(
            load_fixture("sample_transcript.json"), payload["corrections"]
        )
        self.assertEqual(corrected["words"][4]["word"], "channel of ours")
        self.assertEqual(corrected["words"][4]["start"], 3.4)
        self.assertEqual(corrected["words"][4]["end"], 3.8)

    def test_merge_three_words_into_one(self):
        # Words 33..35 are "will not give"; the viewer contracts the
        # run, and words 26..27 "thank you" become the single word
        # "thankyou".
        status, payload = self.correct(33, 36, "won't give")
        self.assertEqual(status, 200, payload)
        status, payload = self.correct(26, 28, "thankyou")
        self.assertEqual(status, 200, payload)
        summary = self.summary()
        merged = self.entry_at(summary, 33)
        self.assertEqual(merged["word"], "won't give")
        self.assertTrue(merged["corrected"])
        self.assertEqual(merged["word_end"], 36)
        self.assertEqual(merged["original"], "will not give")
        single = self.entry_at(summary, 26)
        self.assertEqual(single["word"], "thankyou")
        self.assertEqual(single["word_end"], 28)
        # Two spans collapse five slots into two entries.
        self.assertEqual(len(summary["transcript_words"]), 37)
        # In the direction transcript the replacement text is laid
        # over the span's own slots: extra slots read empty, and the
        # merged pair's text sits in the first slot.
        corrected = transcript_with_corrections(
            load_fixture("sample_transcript.json"), payload["corrections"]
        )
        self.assertEqual(
            [corrected["words"][i]["word"] for i in (33, 34, 35)],
            ["won't", "give", ""],
        )
        self.assertEqual(
            [corrected["words"][i]["word"] for i in (26, 27)],
            ["thankyou", ""],
        )

    def test_remove_correction_restores_original(self):
        status, _ = self.correct(2, 3, "too")
        self.assertEqual(status, 200)
        self.assertEqual(self.entry_at(self.summary(), 2)["word"], "too")
        status, payload = self.call(
            "/api/word-correct-remove", {"word_start": 2, "word_end": 3}
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["corrections"], [])
        entry = self.entry_at(self.summary(), 2)
        self.assertEqual(entry["word"], "to")
        self.assertFalse(entry["corrected"])
        # Removing a correction that is not there is a clean error.
        status, payload = self.call(
            "/api/word-correct-remove", {"word_start": 2, "word_end": 3}
        )
        self.assertEqual(status, 400)
        self.assertIn("no correction", payload["error"])

    def test_correction_leaves_cuts_and_timings_untouched(self):
        before = self.summary()
        status, _ = self.correct(4, 5, "channel of ours")
        self.assertEqual(status, 200)
        status, _ = self.correct(33, 36, "won't give")
        self.assertEqual(status, 200)
        after = self.summary()
        # The edit itself does not move: same kept and cut counts, and
        # saving a correction never writes a revised cuts plan.
        self.assertEqual(after["words_kept"], before["words_kept"])
        self.assertEqual(after["words_cut"], before["words_cut"])
        self.assertFalse(
            (self.job / "tye-session" / "revised_cuts.json").exists()
        )
        # Every word keeps its exact start and end under the layer.
        original = load_fixture("sample_transcript.json")
        corrected = transcript_with_corrections(
            original, after["word_corrections"]
        )
        self.assertEqual(len(corrected["words"]), len(original["words"]))
        for new, old in zip(corrected["words"], original["words"]):
            self.assertEqual(new["start"], old["start"])
            self.assertEqual(new["end"], old["end"])
        # The corrected spans keep their original outer times.
        self.assertEqual(corrected["words"][4]["start"], 3.4)
        self.assertEqual(corrected["words"][4]["end"], 3.8)
        self.assertEqual(corrected["words"][33]["start"], 21.3)
        self.assertEqual(corrected["words"][35]["end"], 22.9)
        # And the job's own files stay byte-identical.
        self.assertEqual(
            (self.job / "transcript.json").read_bytes(),
            self.original_transcript,
        )
        self.assertEqual(
            (self.job / "cuts.json").read_bytes(), self.original_cuts
        )

    def test_direction_run_reads_corrected_text(self):
        # Baseline: a note on the opening line makes the mock propose
        # cutting the first sentence, words 0..8 ("friends." ends it).
        comment = {
            "id": "d1",
            "ts": 0.4,
            "fmt": "vertical",
            "text": "Remove this opening line please.",
        }

        def propose(job):
            server = create_server(WebAppConfig(job), host="127.0.0.1", port=0)
            thread = threading.Thread(
                target=server.serve_forever, daemon=True
            )
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"

            def call(path, data=None):
                payload = (
                    None if data is None else json.dumps(data).encode()
                )
                req = urllib.request.Request(
                    base + path,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST" if data is not None else "GET",
                )
                try:
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        return resp.status, json.loads(resp.read().decode())
                except urllib.error.HTTPError as exc:
                    return exc.code, json.loads(exc.read().decode())

            try:
                status, _ = call(
                    "/api/comments",
                    {"video": job.name, "comments": [comment]},
                )
                self.assertEqual(status, 200)
                status, result = call("/api/direct", {"provider": "mock"})
                self.assertEqual(status, 200, result)
                ops = [
                    op for op in result["operations"]
                    if op["comment_id"] == "d1"
                ]
                self.assertEqual(len(ops), 1)
                return ops[0]
            finally:
                server.shutdown()
                server.server_close()

        baseline = propose(self.job)
        self.assertEqual(baseline["word_start"], 0)
        self.assertEqual(baseline["word_end"], 8)

        # Same note on a fresh job where word 7 lost its period to a
        # correction ("friends." -> "friends"): the sentence the
        # direction step sees now runs to the next full stop, at
        # word 16, and the proposal follows it.
        job2 = self.tmp / "job2"
        job2.mkdir()
        for src, dst in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / src, job2 / dst)
        server = create_server(WebAppConfig(job2), host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = (
                f"http://127.0.0.1:{server.server_address[1]}"
                "/api/word-correct"
            )
            req = urllib.request.Request(
                url,
                data=json.dumps(
                    {"word_start": 7, "word_end": 8, "text": "friends"}
                ).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            server.shutdown()
            server.server_close()
        corrected = propose(job2)
        self.assertEqual(corrected["word_start"], 0)
        self.assertEqual(corrected["word_end"], 16)

    def test_correction_validation_errors(self):
        # Empty text, a reversed span, and an out-of-range span are
        # clean 400s with plain messages.
        for body in (
            {"word_start": 2, "word_end": 3, "text": "  "},
            {"word_start": 3, "word_end": 3, "text": "x"},
            {"word_start": 0, "word_end": 99, "text": "x"},
            {"word_start": "2", "word_end": 3, "text": "x"},
            {"word_start": 2, "text": "x"},
        ):
            status, payload = self.call("/api/word-correct", body)
            self.assertEqual(status, 400, body)
            self.assertIn("error", payload)
        # A partly overlapping second correction is refused; saving
        # over the exact same span replaces it.
        status, _ = self.correct(2, 5, "back to the")
        self.assertEqual(status, 200)
        status, payload = self.correct(4, 6, "channel today")
        self.assertEqual(status, 400)
        self.assertIn("already have a correction", payload["error"])
        status, payload = self.correct(2, 5, "back to the!")
        self.assertEqual(status, 200, payload)
        self.assertEqual(
            payload["corrections"],
            [{"word_start": 2, "word_end": 5, "text": "back to the!"}],
        )


class CommentScopeAndOverallTests(unittest.TestCase):
    """Comment scope and overall feedback, each on its own server.

    A direction run serves one format, so it only receives the
    comments whose scope covers that format (Both always applies);
    overall notes ride along with every run.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-scope-"))
        self.job = self.tmp / "job"
        self.job.mkdir()
        for src, dst in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / src, self.job / dst)
        self.server = create_server(
            WebAppConfig(self.job), host="127.0.0.1", port=0
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------

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
        self.assertEqual(status, 200)
        return payload

    def save(self, comments, overall=None):
        body = {"video": "job", "comments": comments}
        if overall is not None:
            body["overall_feedback"] = overall
        return self.call("/api/comments", body)

    def saved_feedback(self):
        return json.loads(
            (self.job / "tye-session" / "feedback.json").read_text(
                encoding="utf-8"
            )
        )

    def direct(self, fmt=None):
        body = {"provider": "mock"}
        if fmt is not None:
            body["format"] = fmt
        return self.call("/api/direct", body)

    # -- tests -----------------------------------------------------------

    def test_scope_defaults_to_both(self):
        status, payload = self.save(
            [
                {
                    "id": "d1",
                    "ts": 6.0,
                    "fmt": "vertical",
                    "text": "Remove this repeated line, we already said it.",
                }
            ]
        )
        self.assertEqual(status, 200, payload)
        # Saved on disk with the default, and the summary shows it.
        self.assertEqual(
            self.saved_feedback()["comments"][0]["scope"], "both"
        )
        self.assertEqual(self.summary()["comments"][0]["scope"], "both")

    def test_bad_scope_is_a_clean_error(self):
        status, payload = self.save(
            [
                {
                    "id": "d1",
                    "ts": 6.0,
                    "fmt": "vertical",
                    "scope": "sideways",
                    "text": "Remove this repeated line, we already said it.",
                }
            ]
        )
        self.assertEqual(status, 400)
        self.assertIn("scope", payload["error"])

    def test_scope_saved_and_shown_on_the_page(self):
        status, _ = self.save(
            [
                {
                    "id": "v1",
                    "ts": 6.0,
                    "fmt": "vertical",
                    "scope": "vertical",
                    "text": "Remove this repeated line, we already said it.",
                },
                {
                    "id": "l1",
                    "ts": 6.0,
                    "fmt": "landscape",
                    "scope": "landscape",
                    "text": "Remove this repeated line, we already said it.",
                },
            ]
        )
        self.assertEqual(status, 200)
        by_id = {c["id"]: c for c in self.summary()["comments"]}
        self.assertEqual(by_id["v1"]["scope"], "vertical")
        self.assertEqual(by_id["l1"]["scope"], "landscape")
        # The form lets the viewer pick, the list shows the pick, and
        # the overall field and list are on the page too.
        req = urllib.request.Request(self.base + "/", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            page = resp.read().decode("utf-8")
        self.assertIn('id="scope"', page)
        self.assertIn("Both formats", page)
        self.assertIn("Vertical only", page)
        self.assertIn("Landscape only", page)
        self.assertIn("SCOPE_LABELS", page)
        self.assertIn("vertical only", page)
        self.assertIn("<h2>Overall feedback</h2>", page)
        self.assertIn('id="overallDraft"', page)
        self.assertIn('id="overallList"', page)

    def test_scoped_comment_only_reaches_its_format_run(self):
        comments = [
            {
                "id": "v1",
                "ts": 6.0,
                "fmt": "vertical",
                "scope": "vertical",
                "text": "Remove this repeated line, we already said it.",
            },
            {
                "id": "l1",
                "ts": 6.0,
                "fmt": "landscape",
                "scope": "landscape",
                "text": "Remove this repeated line, we already said it.",
            },
            {
                "id": "b1",
                "ts": 10.0,
                "fmt": "vertical",
                "scope": "both",
                "text": "Make the pause here longer, 0.4 seconds please.",
            },
        ]
        status, _ = self.save(comments)
        self.assertEqual(status, 200)

        def run_ids(result):
            ops = result.get("operations", [])
            return {
                op["comment_id"] for op in ops
            } | set(result.get("applied_comment_ids", [])) | set(
                result.get("flagged_comment_ids", [])
            ) | set(result.get("rejected_comment_ids", []))

        # The vertical run sees the vertical note and the Both note,
        # and the landscape note never reaches it.
        status, vertical = self.direct("vertical")
        self.assertEqual(status, 200, vertical)
        self.assertEqual(vertical["format"], "vertical")
        self.assertEqual(run_ids(vertical), {"v1", "b1"})
        self.assertIn("v1", vertical["applied_comment_ids"])

        # The landscape run sees the landscape note and the Both note,
        # and the vertical note never reaches it.
        status, landscape = self.direct("landscape")
        self.assertEqual(status, 200, landscape)
        self.assertEqual(landscape["format"], "landscape")
        self.assertEqual(run_ids(landscape), {"l1", "b1"})
        self.assertIn("l1", landscape["applied_comment_ids"])

    def test_overall_note_reaches_every_direction_run(self):
        status, _ = self.save(
            [
                {
                    "id": "c1",
                    "ts": 6.0,
                    "fmt": "vertical",
                    "text": "Remove this repeated line, we already said it.",
                }
            ],
            overall=[{"id": "g1", "text": "Too many retakes in this one."}],
        )
        self.assertEqual(status, 200)
        # Saved alongside the comments, and the summary shows it in
        # its own list.
        self.assertEqual(
            self.saved_feedback()["overall_feedback"],
            [{"id": "g1", "text": "Too many retakes in this one."}],
        )
        self.assertEqual(
            self.summary()["overall_feedback"],
            [{"id": "g1", "text": "Too many retakes in this one."}],
        )
        # The note rides with the vertical run...
        status, vertical = self.direct("vertical")
        self.assertEqual(status, 200, vertical)
        self.assertEqual(
            vertical["overall_feedback"],
            [{"id": "g1", "text": "Too many retakes in this one."}],
        )
        audit = json.loads(
            (self.job / "tye-session" / "operations_audit.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            audit["overall_feedback"],
            [{"id": "g1", "text": "Too many retakes in this one."}],
        )
        # ...and with the landscape run too.
        status, landscape = self.direct("landscape")
        self.assertEqual(status, 200, landscape)
        self.assertEqual(
            landscape["overall_feedback"],
            [{"id": "g1", "text": "Too many retakes in this one."}],
        )

    def test_overall_notes_are_kept_when_only_comments_are_saved(self):
        # A client that posts comments without mentioning overall
        # notes must not wipe notes already saved.
        status, _ = self.save(
            [
                {
                    "id": "c1",
                    "ts": 6.0,
                    "fmt": "vertical",
                    "text": "Remove this repeated line, we already said it.",
                }
            ],
            overall=[{"id": "g1", "text": "Too many retakes in this one."}],
        )
        self.assertEqual(status, 200)
        status, _ = self.save(
            [
                {
                    "id": "c1",
                    "ts": 6.0,
                    "fmt": "vertical",
                    "text": "Remove this repeated line, we already said it.",
                }
            ]
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            self.summary()["overall_feedback"],
            [{"id": "g1", "text": "Too many retakes in this one."}],
        )

    def test_run_feedback_filtering_unit(self):
        feedback = {
            "comments": [
                {"id": "a", "scope": "vertical"},
                {"id": "b", "scope": "landscape"},
                {"id": "c", "scope": "both"},
                {"id": "d"},  # no scope: Both
                {"id": "e", "scope": "sideways"},  # junk: Both
            ],
            "overall_feedback": [{"id": "g1", "text": "Too many retakes."}],
        }
        vertical = _feedback_for_run(feedback, "vertical")
        self.assertEqual(
            [c["id"] for c in vertical["comments"]], ["a", "c", "d", "e"]
        )
        landscape = _feedback_for_run(feedback, "landscape")
        self.assertEqual(
            [c["id"] for c in landscape["comments"]], ["b", "c", "d", "e"]
        )
        for run in (vertical, landscape):
            self.assertEqual(
                run["overall_feedback"],
                [{"id": "g1", "text": "Too many retakes."}],
            )

    def test_meta_evidence_carries_overall_guidance(self):
        timeline = Timeline(
            load_fixture("sample_transcript.json"),
            load_fixture("sample_cuts.json"),
        )
        comment = {"id": "c1", "ts": 6.0, "text": "Remove this line."}
        evidence = MetaProvider._evidence(
            comment,
            timeline,
            load_fixture("sample_profile.json"),
            ["Too many retakes in this one.", "Keep the opening."],
        )
        self.assertEqual(
            evidence["overall_guidance"],
            ["Too many retakes in this one.", "Keep the opening."],
        )
        # No guidance, no key: nothing the model would see changes.
        empty = MetaProvider._evidence(comment, timeline, None)
        self.assertEqual(empty["overall_guidance"], [])


def _probe_dimensions(path: Path):
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0", str(path),
        ],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    width, height = out.split(",")
    return int(width), int(height)


def _make_video(path: Path, width: int, height: int) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i",
            f"color=c=black:s={width}x{height}:d=0.5",
            "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True, text=True, check=True,
    )


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "needs ffmpeg and ffprobe to build the format fixture",
)
class FormatHonestyTests(unittest.TestCase):
    """The format buttons must serve files whose real shape matches."""

    TALL = (270, 480)   # 9:16
    WIDE = (480, 270)   # 16:9

    @classmethod
    def _start_server(cls, job: Path):
        server = create_server(WebAppConfig(job), host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, f"http://127.0.0.1:{server.server_address[1]}"

    @classmethod
    def _get(cls, base: str, path: str):
        with urllib.request.urlopen(base + path, timeout=10) as resp:
            return resp.status, resp.read()

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="tye-formats-"))
        # A job with both formats, shapes verified below by ffprobe.
        cls.job = cls.tmp / "job-both"
        cls.job.mkdir()
        for fixture, target in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / fixture, cls.job / target)
        _make_video(cls.job / "final.mp4", *cls.TALL)
        _make_video(cls.job / "final-long.mp4", *cls.WIDE)
        # The shapes really are what the names in this test claim.
        assert _probe_dimensions(cls.job / "final.mp4") == cls.TALL
        assert _probe_dimensions(cls.job / "final-long.mp4") == cls.WIDE
        cls.server, cls.base = cls._start_server(cls.job)
        # A second job with only the tall cut.
        cls.tall_only = cls.tmp / "job-tall-only"
        cls.tall_only.mkdir()
        for fixture, target in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
        ):
            shutil.copyfile(FIXTURES / fixture, cls.tall_only / target)
        shutil.copyfile(cls.job / "final.mp4", cls.tall_only / "final.mp4")
        cls.server2, cls.base2 = cls._start_server(cls.tall_only)

    @classmethod
    def tearDownClass(cls) -> None:
        for server in (cls.server, cls.server2):
            server.shutdown()
            server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_summary_reports_measured_shapes(self):
        _, body = self._get(self.base, "/api/job")
        summary = json.loads(body)
        self.assertEqual(
            summary["video_shapes"]["final.mp4"],
            {"width": 270, "height": 480},
        )
        self.assertEqual(
            summary["video_shapes"]["final-long.mp4"],
            {"width": 480, "height": 270},
        )

    def test_each_format_serves_the_matching_shape(self):
        # The page picks by measured shape; here both candidate files
        # are fetched the way the buttons fetch them, and each served
        # file's probed shape must match its button's format.
        _, body = self._get(self.base, "/api/job")
        shapes = json.loads(body)["video_shapes"]
        vertical = next(
            n for n, s in shapes.items() if s["height"] >= s["width"]
        )
        landscape = next(
            n for n, s in shapes.items() if s["width"] > s["height"]
        )
        for name, expected in (
            (vertical, self.TALL),
            (landscape, self.WIDE),
        ):
            status, served = self._get(self.base, f"/video/{name}")
            self.assertEqual(status, 200)
            probe_copy = self.tmp / f"served-{name}"
            probe_copy.write_bytes(served)
            self.assertEqual(_probe_dimensions(probe_copy), expected)
            self.assertEqual(served, (self.job / name).read_bytes())

    def test_page_selects_by_shape_and_never_crops(self):
        _, body = self._get(self.base, "/")
        page = body.decode("utf-8")
        self.assertIn("video_shapes", page)
        self.assertIn("shape.width > shape.height", page)
        self.assertIn("shape.height >= shape.width", page)
        self.assertIn("object-fit: contain", page)

    def test_page_shows_loading_until_the_video_can_play(self):
        _, body = self._get(self.base, "/")
        page = body.decode("utf-8")
        self.assertIn("Loading the video…", page)
        self.assertIn('vid.addEventListener("loadstart", showLoading)', page)
        self.assertIn('vid.addEventListener("canplay", hideLoading)', page)

    def test_missing_format_button_is_disabled_with_a_plain_line(self):
        _, body = self._get(self.base2, "/api/job")
        summary = json.loads(body)
        self.assertEqual(summary["videos"], ["final.mp4"])
        self.assertNotIn("final-long.mp4", summary["video_shapes"])
        _, page_body = self._get(self.base2, "/")
        page = page_body.decode("utf-8")
        self.assertIn('getElementById("btnL").disabled = true', page)
        self.assertIn("not in this job.", page)
        self.assertIn('id="fmtNote"', page)


class SigninTests(unittest.TestCase):
    """The page-checkable Meta sign-in: state, check, and session paste.

    Runs against its own server and job dir, so the in-memory session
    sign-in set here can never leak into the shared-server tests. The
    environment is scrubbed of MODEL_API_KEY per test, so "set" can
    only come from each test's own arrangement.
    """

    SECRET = "sk-session-secret-0123456789"

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="tye-signin-"))
        cls.job = cls.tmp / "job"
        cls.job.mkdir()
        for fixture, target in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / fixture, cls.job / target)
        (cls.job / "review.mp4").write_bytes(VIDEO_BYTES)
        cls.config = WebAppConfig(cls.job)
        cls.server = create_server(cls.config, host="127.0.0.1", port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self) -> None:
        self._env_patch = mock.patch.dict(os.environ)
        self._env_patch.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.config.session_api_key = None

    def tearDown(self) -> None:
        self.config.session_api_key = None
        self._env_patch.stop()

    # -- helpers -----------------------------------------------------------

    def request(self, path, data=None):
        payload = None
        headers = {}
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path,
            data=payload,
            headers=headers,
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def get_json(self, path):
        status, body = self.request(path)
        self.assertEqual(status, 200, f"GET {path} -> {status}: {body!r}")
        return json.loads(body.decode("utf-8"))

    def post_json(self, path, data):
        status, body = self.request(path, data=data)
        return status, body, json.loads(body.decode("utf-8"))

    # -- tests ---------------------------------------------------------------

    def test_state_reports_unset_then_server_set(self):
        state = self.get_json("/api/signin")
        self.assertEqual(
            state, {"meta_signin_set": False, "source": None}
        )
        os.environ["MODEL_API_KEY"] = "sk-server-side-key"
        state = self.get_json("/api/signin")
        self.assertEqual(
            state, {"meta_signin_set": True, "source": "server"}
        )

    def test_page_has_signin_controls(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        for marker in (
            'id="signinState"',
            'id="checkSignin"',
            'id="signinKey"',
            'id="saveSignin"',
            "/api/signin/check",
        ):
            self.assertIn(marker, page)

    def test_check_maps_accepted_and_refused_to_plain_sentences(self):
        os.environ["MODEL_API_KEY"] = "sk-env-key"
        with mock.patch.object(
            MetaProvider, "check_signin", return_value=True
        ):
            status, _, payload = self.post_json("/api/signin/check", {})
        self.assertEqual(status, 200)
        self.assertTrue(payload["accepted"])
        self.assertEqual(
            payload["message"], webapp_module.SIGNIN_ACCEPTED_MESSAGE
        )
        self.assertIn("accepted", payload["message"])
        self.assertNotIn("meta_api", payload["message"])

        with mock.patch.object(
            MetaProvider, "check_signin", return_value=False
        ):
            status, _, payload = self.post_json("/api/signin/check", {})
        self.assertEqual(status, 200)
        self.assertFalse(payload["accepted"])
        self.assertEqual(
            payload["message"],
            webapp_module.SIGNIN_NOT_ACCEPTED_MESSAGE,
        )
        self.assertIn("not accepted", payload["message"])
        self.assertNotIn("401", payload["message"])

    def test_check_without_any_signin_is_not_accepted(self):
        # The provider stub would record a call; with no sign-in set
        # the check must not even place the call.
        calls = []

        def recording_check(provider_self):
            calls.append(provider_self.api_key)
            return True

        with mock.patch.object(
            MetaProvider, "check_signin", recording_check
        ):
            status, _, payload = self.post_json("/api/signin/check", {})
        self.assertEqual(status, 200)
        self.assertFalse(payload["accepted"])
        self.assertEqual(
            payload["message"],
            webapp_module.SIGNIN_NOT_ACCEPTED_MESSAGE,
        )
        self.assertEqual(calls, [])

    def test_pasted_signin_is_saved_and_used_by_a_later_check(self):
        status, raw, payload = self.post_json(
            "/api/signin", {"api_key": self.SECRET}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["meta_signin_set"])
        self.assertEqual(payload["source"], "session")
        self.assertNotIn(self.SECRET, raw.decode("utf-8"))

        state = self.get_json("/api/signin")
        self.assertEqual(
            state, {"meta_signin_set": True, "source": "session"}
        )

        seen = []

        def recording_check(provider_self):
            seen.append(provider_self.api_key)
            return True

        with mock.patch.object(
            MetaProvider, "check_signin", recording_check
        ):
            status, _, payload = self.post_json("/api/signin/check", {})
        self.assertEqual(status, 200)
        self.assertTrue(payload["accepted"])
        self.assertEqual(seen, [self.SECRET])

    def test_pasted_signin_is_used_by_a_direction_run(self):
        self.post_json(
            "/api/comments",
            {
                "video": "job",
                "comments": [
                    {
                        "id": "c1",
                        "ts": 6.0,
                        "fmt": "vertical",
                        "text": "Remove this repeated line.",
                    }
                ],
            },
        )
        self.post_json("/api/signin", {"api_key": self.SECRET})

        class FakeMetaProvider:
            keys = []

            def __init__(self, api_key=None, allow_wonder=True):
                FakeMetaProvider.keys.append(api_key)

            def direct(self, feedback, timeline, profile=None):
                return [], []

        with mock.patch.object(
            webapp_module, "MetaProvider", FakeMetaProvider
        ):
            status, _, payload = self.post_json(
                "/api/direct", {"provider": "meta"}
            )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(FakeMetaProvider.keys, [self.SECRET])

    def test_pasted_signin_never_echoed_or_written_to_a_file(self):
        bodies = []
        _, raw, _ = self.post_json(
            "/api/signin", {"api_key": self.SECRET}
        )
        bodies.append(raw)
        _, raw = self.request("/api/signin")
        bodies.append(raw)
        _, raw = self.request("/api/job")
        bodies.append(raw)
        _, raw = self.request("/")
        bodies.append(raw)
        with mock.patch.object(
            MetaProvider, "check_signin", return_value=True
        ):
            _, raw, _ = self.post_json("/api/signin/check", {})
        bodies.append(raw)
        for body in bodies:
            self.assertNotIn(self.SECRET.encode("utf-8"), body)
        for path in self.job.rglob("*"):
            if path.is_file():
                self.assertNotIn(
                    self.SECRET.encode("utf-8"),
                    path.read_bytes(),
                    f"session sign-in leaked into {path}",
                )

    def test_empty_signin_is_rejected(self):
        status, _, payload = self.post_json(
            "/api/signin", {"api_key": "   "}
        )
        self.assertEqual(status, 400)
        self.assertIn("error", payload)
        state = self.get_json("/api/signin")
        self.assertFalse(state["meta_signin_set"])


if __name__ == "__main__":
    unittest.main()
