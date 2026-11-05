"""v0.17, feedback item 26: the Editor chat is a conversation,
not a word-vomit log of every cut.

Justin's words: "It's like a word-vomit list of things that
it did. The editor window needs to be a conversation, and
the review suggestions go somewhere else."

Covered here:

- a run summary's per-cut claims are filed as review
  suggestions and the chat entry itself carries none;
- the review view shows an item (words, time, quote) while
  its words are out, and stops showing it when they come
  back by any path;
- the verdicts work: Bring back restores the words (one
  undo step), Keep cut closes the suggestion and the words
  stay out;
- entries written before the diversion (kind "run" /
  "editor" carrying claims) render claim-free, while a
  record answer's claims, which ARE the answer, still show.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import webapp
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig

WORDS = (
    "the morning started quiet and cold over the water today "
    "we talked about the plan for the whole long day ahead "
    "and then the sun came up over the trees behind us"
).split()


class ReviewCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v17r-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"
        self.job.mkdir(parents=True)
        words = [
            {"word": token, "start": float(i), "end": float(i) + 0.9}
            for i, token in enumerate(WORDS)
        ]
        transcript = {
            "engine": "test",
            "duration": float(len(words)),
            "words": words,
        }
        cuts = {
            "source_duration_s": transcript["duration"],
            "kept_ranges": [
                {"start": 0.0, "end": transcript["duration"]}
            ],
        }
        (self.job / "transcript.json").write_text(
            json.dumps(transcript), encoding="utf-8"
        )
        (self.job / "cuts.json").write_text(
            json.dumps(cuts), encoding="utf-8"
        )
        self.config = WebAppConfig(self.job)

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def status(self, index):
        transcript = json.loads(
            (self.job / "transcript.json").read_text(encoding="utf-8")
        )
        timeline = Timeline(
            transcript, webapp._effective_cuts(self.config)
        )
        return timeline.word_status(index)

    def cut_and_file(self, start=10, end=13):
        webapp._apply_word_range(self.config, start, end, "cut")
        webapp._record_review_claims(
            self.config,
            [{"verb": "Cut", "word_start": start, "word_end": end}],
        )
        return f"review-{start}-{end}"


class ReviewStoreTests(ReviewCase):
    def test_run_summary_files_claims_and_chat_stays_clean(self):
        webapp._apply_word_range(self.config, 10, 13, "cut")
        entry = webapp._append_run_summary_thread(
            self.config,
            ["Made 1 change."],
            [{"verb": "Cut", "word_start": 10, "word_end": 13}],
        )
        self.assertEqual(entry["claims"], [])
        # 0.26, fix 3: the long recap bubble is gone. Filing a
        # run's claims posts no thread entry at all; the Done
        # bubble is a run's only summary in the chat.
        self.assertFalse(entry["posted"])
        view = webapp._thread_view(self.config)
        run_entries = [
            e for e in view["entries"] if e.get("kind") == "run"
        ]
        self.assertEqual(len(run_entries), 0)
        self.assertEqual(len(view["review"]), 1)
        item = view["review"][0]
        self.assertEqual(item["id"], "review-10-13")
        self.assertIn("Cut 3 words at", item["text"])
        self.assertEqual(
            item["quote"], " ".join(WORDS[10:13])
        )

    def test_item_stops_showing_when_words_come_back(self):
        review_id = self.cut_and_file()
        self.assertEqual(len(webapp._review_view(self.config)), 1)
        webapp._apply_word_range(self.config, 10, 13, "restore")
        self.assertEqual(webapp._review_view(self.config), [])
        self.assertEqual(review_id, "review-10-13")

    def test_verdict_restore_brings_words_back(self):
        review_id = self.cut_and_file()
        for index in range(10, 13):
            self.assertEqual(self.status(index), "cut")
        view = webapp._review_verdict(self.config, review_id, "restore")
        for index in range(10, 13):
            self.assertEqual(self.status(index), "kept")
        self.assertEqual(view["review"], [])
        stored = webapp._load_review(self.config)
        self.assertEqual(stored[0]["status"], "restored")

    def test_verdict_keep_closes_and_words_stay_out(self):
        review_id = self.cut_and_file()
        view = webapp._review_verdict(self.config, review_id, "keep")
        for index in range(10, 13):
            self.assertEqual(self.status(index), "cut")
        self.assertEqual(view["review"], [])
        stored = webapp._load_review(self.config)
        self.assertEqual(stored[0]["status"], "kept")

    def test_unknown_suggestion_is_a_plain_error(self):
        with self.assertRaises(ValueError):
            webapp._review_verdict(
                self.config, "review-99-100", "keep"
            )


class LegacyClaimsTests(ReviewCase):
    def test_old_run_entry_renders_without_claims(self):
        webapp._append_thread(
            self.config,
            {
                "kind": "run",
                "status": "settled",
                "text": "An old run.",
                "claims": [
                    {"verb": "Cut", "word_start": 10, "word_end": 13}
                ],
            },
        )
        views = webapp._thread_entries_view(self.config)
        self.assertEqual(views[-1]["claims"], [])

    def test_record_answer_keeps_its_claims(self):
        webapp._apply_word_range(self.config, 10, 13, "cut")
        webapp._append_thread(
            self.config,
            {
                "kind": "editor",
                "record_answer": True,
                "status": "settled",
                "text": "From the record.",
                "claims": [
                    {"verb": "Cut", "word_start": 10, "word_end": 13}
                ],
            },
        )
        views = webapp._thread_entries_view(self.config)
        claims = views[-1]["claims"]
        self.assertEqual(len(claims), 1)
        self.assertIn("those words are out now", claims[0]["text"])


if __name__ == "__main__":
    unittest.main()
