"""Tests for the v0.2 local web test app (stdlib unittest, no dependencies).

Spins the real HTTP server on an ephemeral port against a temp job dir
built from the sample fixture, then drives it with urllib: job summary,
comment save, mock direction, downloads, after-review, and video range
requests.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.webapp import WebAppConfig, create_server

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

    def test_render_reports_missing_configuration(self):
        status, payload = self.post_json("/api/render", {})
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(
            sorted(payload["missing"]),
            ["cleaned_wav", "render_py", "source_video"],
        )


if __name__ == "__main__":
    unittest.main()
