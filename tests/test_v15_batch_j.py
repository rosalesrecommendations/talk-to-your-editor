"""Tests for v0.15 Batch J: the grades-store race (step 1).

The server answers each request on its own thread, and the
grades and thread stores are read-modify-write files. Two
writers that load the same starting store and save in turn used
to lose the first writer's record silently: a restore grade
could go missing while its why entry stayed in the thread, so
answering the question failed and the entry never settled (the
[0.14F] X3 battery failure, disclosed since Batch E1).

These tests force exactly that scheduling: a rendezvous inside
the store's load makes both writers read the same starting
store before either saves. Before the fix (per-store locks
around every load-merge-save cycle) one record is lost; after
it, both survive, and both why answers land.

Run from the repository root:
    PYTHONPATH=src python3 -m unittest tests.test_v15_batch_j
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

from talk_to_your_editor import webapp
from talk_to_your_editor.webapp import WebAppConfig, create_server


def write_job(job: Path, count: int = 300) -> None:
    job.mkdir(parents=True, exist_ok=True)
    words = [
        {"word": f"word{i}", "start": float(i), "end": float(i) + 0.9}
        for i in range(count)
    ]
    transcript = {"engine": "test", "duration": float(count), "words": words}
    cuts = {
        "source_duration_s": float(count),
        "kept_ranges": [{"start": 0.0, "end": float(count)}],
    }
    (job / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")


def rendezvous(real, parties=2, timeout=3.0):
    """Wrap a store load so the first ``parties`` callers all read
    before any of them goes on to save. Once the barrier breaks
    (the fixed code serializes the loads, so the second caller
    arrives only after the first has saved) later calls pass
    straight through."""
    barrier = threading.Barrier(parties)
    state = {"n": 0}

    def wrapped(*args, **kwargs):
        out = real(*args, **kwargs)
        state["n"] += 1
        if state["n"] <= parties:
            try:
                barrier.wait(timeout=timeout)
            except threading.BrokenBarrierError:
                pass
        return out

    return wrapped


class BatchJCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v15-j-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        self.job = self.tmp / "job"
        write_job(self.job)
        self.config = WebAppConfig(self.job)
        self.config.session_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class ConcurrentRestoreGradesTests(BatchJCase):
    def test_two_concurrent_restores_both_keep_their_grade(self):
        with mock.patch.object(
            webapp, "_load_grades", rendezvous(webapp._load_grades)
        ):
            threads = [
                threading.Thread(
                    target=webapp._record_restore_grade,
                    args=(self.config, start, start + 1, "test"),
                )
                for start in (100, 200)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        ids = {e.get("id") for e in webapp._load_grades(self.config)}
        self.assertIn("restore-grade-100-101", ids)
        self.assertIn("restore-grade-200-201", ids)
        why_ids = {
            e.get("entry_id")
            for e in webapp._load_thread(self.config)
            if e.get("kind") == "why"
        }
        self.assertEqual(
            why_ids, {"restore-grade-100-101", "restore-grade-200-201"}
        )


class ConcurrentThreadAppendTests(BatchJCase):
    def test_two_concurrent_appends_both_survive(self):
        with mock.patch.object(
            webapp, "_load_thread", rendezvous(webapp._load_thread)
        ):
            threads = [
                threading.Thread(
                    target=webapp._append_thread,
                    args=(self.config, {"kind": "editor", "text": text}),
                )
                for text in ("first", "second")
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        texts = {e.get("text") for e in webapp._load_thread(self.config)}
        self.assertEqual(texts, {"first", "second"})


class ConcurrentGradePostsHttpTests(BatchJCase):
    """The X3 end state over real HTTP: parallel grade saves all
    land, and a why answer for a restore grade settles its entry."""

    def setUp(self) -> None:
        super().setUp()
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
        super().tearDown()

    def post_json(self, path, data):
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_parallel_grade_posts_all_survive(self):
        def post(i):
            self.post_json(
                "/api/grades",
                {
                    "entries": [
                        {
                            "id": f"batchj-{i}",
                            "record_id": f"batchj-{i}",
                            "comment_id": "c",
                            "operation_type": "cut_words",
                            "verdict": "worked",
                            "pattern_key": f"batchj:{i}",
                            "pattern_description": "batch j",
                        }
                    ]
                },
            )

        threads = [threading.Thread(target=post, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ids = {e.get("id") for e in webapp._load_grades(self.config)}
        self.assertEqual({f"batchj-{i}" for i in range(12)} - ids, set())

    def test_why_answer_settles_the_entry_its_restore_raised(self):
        entry_id = webapp._record_restore_grade(self.config, 8, 9, "test")
        self.assertEqual(entry_id, "restore-grade-8-9")
        status, _ = self.post_json(
            "/api/why", {"entry_id": entry_id, "choice": "story", "note": ""}
        )
        self.assertEqual(status, 200)
        entry = next(
            e
            for e in webapp._load_grades(self.config)
            if e.get("id") == entry_id
        )
        self.assertEqual(entry["why"]["choice"], "story")
        settled = [
            e
            for e in webapp._sync_thread(self.config)
            if e.get("kind") == "why" and e.get("entry_id") == entry_id
        ]
        self.assertTrue(settled)
        self.assertEqual(settled[0]["status"], "settled")


TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")


class ReadingZoomSnapTests(unittest.TestCase):
    """Step 2: the slider's step aligns from its minimum (the fit
    scale), so '120' could land a hair under reading size while
    the readout rounded it to 'Zoom 100%' and the word row's
    hard >= gate drew nothing. tlSetZoom now snaps the last
    quarter-pixel onto the named reading stop."""

    def test_set_zoom_snaps_to_reading_size(self):
        start = TEMPLATE.index("function tlSetZoom(newScale")
        block = TEMPLATE[start:start + 1600]
        self.assertIn("Math.abs(clamped - TL_READ_PPS) <= 0.25", block)
        self.assertIn("clamped = TL_READ_PPS;", block)
        # The snap never swallows the Fit case itself.
        self.assertIn("clamped > fit + 0.001", block)


class CutGroupLineTechniqueTests(unittest.TestCase):
    """Step 4: the merged cut group's strike line is a
    pseudo-element, so the template carries no linear-gradient
    strike layer for the census (or a reader) to count."""

    def test_cut_group_line_is_a_pseudo_element(self):
        start = TEMPLATE.index(".tl-word-group.cut {")
        body = TEMPLATE[start:TEMPLATE.index("}", start)]
        self.assertNotIn("linear-gradient(to bottom", body)
        after = TEMPLATE.index(".tl-word-group.cut::after {")
        abody = TEMPLATE[after:TEMPLATE.index("}", after)]
        self.assertIn("top: calc(50% - 1px)", abody)
        self.assertIn("height: 2px", abody)
        self.assertIn("background: rgba(238, 244, 255, 0.85)", abody)


if __name__ == "__main__":
    unittest.main()
