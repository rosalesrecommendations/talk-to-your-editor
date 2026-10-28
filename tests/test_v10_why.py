"""Tests for v0.10 item 2: the in-app why question.

When the creator brings cut words back (a word click, a phrase
restore, the edit card's Bring back, or an undo that brings back
exactly one cut) or leaves a held spot in, the app records the
choice in the learning store and asks one short question about
it. The answer is saved on that same grade record as a ``why``
field, and pattern extraction collects the answers per pattern.

Run from the repository root:
    PYTHONPATH=src python3 -m unittest tests.test_v10_why
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

from talk_to_your_editor import checkpoints, learning, webapp
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig, create_server

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
)


def page_html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def timed_words(count: int) -> list:
    return [
        {"word": f"word{i}", "start": float(i), "end": float(i) + 0.9}
        for i in range(count)
    ]


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


def restore_entries(config: WebAppConfig) -> list:
    return [
        entry
        for entry in webapp._load_grades(config)
        if entry.get("operation_type") == "restore_words"
    ]


class WhyServerCase(unittest.TestCase):
    """A real server on an ephemeral port over a minimal job."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v10-why-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"
        write_job(self.job, timed_words(20))
        self.config = WebAppConfig(self.job)
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

    def write_audit(self, operations: list) -> None:
        self.config.session_dir.mkdir(parents=True, exist_ok=True)
        (self.config.session_dir / "operations_audit.json").write_text(
            json.dumps({"operations": operations}), encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# Restore grade records
# ---------------------------------------------------------------------------


class WordToggleRestoreTests(WhyServerCase):
    def test_restore_writes_grade_with_the_cutting_ops_pattern_key(self):
        status, cut = self.post_json("/api/word-toggle", {"word_index": 8})
        self.assertEqual(status, 200, cut)
        self.assertTrue(cut["changed"])
        self.assertNotIn("why_prompt", cut)
        # Two applied cuts cover the word; the last one is the
        # change that cut it for this restore.
        self.write_audit(
            [
                {
                    "type": "cut_words",
                    "status": "applied",
                    "comment_id": "first-cut",
                    "word_start": 6,
                    "word_end": 12,
                },
                {
                    "type": "cut_words",
                    "status": "applied",
                    "comment_id": "last-cut",
                    "word_start": 8,
                    "word_end": 9,
                },
                {
                    "type": "cut_words",
                    "status": "flagged",
                    "comment_id": "not-applied",
                    "word_start": 8,
                    "word_end": 9,
                },
            ]
        )
        status, result = self.post_json(
            "/api/word-toggle", {"word_index": 8}
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["changed"])
        self.assertEqual(result["status"], "kept")
        self.assertEqual(
            result["why_prompt"], {"entry_id": "restore-grade-8-9"}
        )
        entries = restore_entries(self.config)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["id"], "restore-grade-8-9")
        self.assertEqual(entry["record_id"], "restore-grade-8-9")
        self.assertEqual(entry["comment_id"], "restore")
        self.assertEqual(entry["verdict"], "did_not_work")
        self.assertEqual(entry["pattern_key"], "restore:last-cut")
        self.assertEqual(
            entry["pattern_description"],
            "Bringing back words the editor had cut",
        )
        self.assertEqual(entry["word_start"], 8)
        self.assertEqual(entry["word_end"], 9)
        self.assertEqual(entry["source"], "word-toggle")
        self.assertIn("created_at", entry)

    def test_restore_with_no_audit_op_is_by_hand(self):
        self.post_json("/api/word-toggle", {"word_index": 9})
        status, result = self.post_json(
            "/api/word-toggle", {"word_index": 9}
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(
            result["why_prompt"], {"entry_id": "restore-grade-9-10"}
        )
        entries = restore_entries(self.config)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["pattern_key"], "restore:by-hand")

    def test_cutting_a_word_writes_no_restore_grade(self):
        status, result = self.post_json(
            "/api/word-toggle", {"word_index": 3}
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["changed"])
        self.assertNotIn("why_prompt", result)
        self.assertEqual(restore_entries(self.config), [])


class PhraseRestoreTests(WhyServerCase):
    def test_phrase_restore_writes_one_record_for_the_full_range(self):
        status, cut = self.post_json(
            "/api/word-range",
            {"word_start": 4, "word_end": 7, "action": "cut"},
        )
        self.assertEqual(status, 200, cut)
        self.assertEqual(cut["moved"], 3)
        status, result = self.post_json(
            "/api/word-range",
            {"word_start": 4, "word_end": 7, "action": "restore"},
        )
        self.assertEqual(status, 200, result)
        self.assertEqual(result["moved"], 3)
        self.assertEqual(
            result["why_prompt"], {"entry_id": "restore-grade-4-7"}
        )
        entries = restore_entries(self.config)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["word_start"], 4)
        self.assertEqual(entries[0]["word_end"], 7)
        self.assertEqual(entries[0]["source"], "word-range")


class EditActionRestoreTests(WhyServerCase):
    def test_bring_back_writes_a_record_over_the_edits_span(self):
        webapp._apply_word_range(self.config, 5, 8, "cut")
        spans = webapp._edit_spans(self.config)
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["edit_id"], "edit-5-8")
        status, result = self.post_json(
            "/api/edit-action",
            {"edit_id": "edit-5-8", "action": "restore"},
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["changed"])
        self.assertEqual(
            result["why_prompt"], {"entry_id": "restore-grade-5-8"}
        )
        entries = restore_entries(self.config)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["word_start"], 5)
        self.assertEqual(entries[0]["word_end"], 8)
        self.assertEqual(entries[0]["source"], "edit-action")


# ---------------------------------------------------------------------------
# The /api/why endpoint
# ---------------------------------------------------------------------------


class WhyEndpointTests(WhyServerCase):
    def make_restore_entry(self) -> str:
        self.post_json("/api/word-toggle", {"word_index": 9})
        _, result = self.post_json("/api/word-toggle", {"word_index": 9})
        return result["why_prompt"]["entry_id"]

    def test_why_answer_lands_on_the_grade_entry(self):
        entry_id = self.make_restore_entry()
        status, result = self.post_json(
            "/api/why",
            {"entry_id": entry_id, "choice": "story", "note": ""},
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        entries = restore_entries(self.config)
        self.assertEqual(
            entries[0]["why"], {"choice": "story", "note": ""}
        )

    def test_why_answer_with_a_note_for_other(self):
        entry_id = self.make_restore_entry()
        status, _ = self.post_json(
            "/api/why",
            {
                "entry_id": entry_id,
                "choice": "other",
                "note": "It is the line people remember.",
            },
        )
        self.assertEqual(status, 200)
        entries = restore_entries(self.config)
        self.assertEqual(
            entries[0]["why"],
            {"choice": "other", "note": "It is the line people remember."},
        )

    def test_unknown_entry_is_a_plain_400(self):
        status, result = self.post_json(
            "/api/why",
            {"entry_id": "restore-grade-1-2", "choice": "story", "note": ""},
        )
        self.assertEqual(status, 400)
        self.assertIn("could not be found", result["error"])
        self.assertTrue(result["error"].endswith("."))

    def test_bad_choice_is_a_plain_400(self):
        entry_id = self.make_restore_entry()
        status, result = self.post_json(
            "/api/why",
            {"entry_id": entry_id, "choice": "because", "note": ""},
        )
        self.assertEqual(status, 400)
        self.assertIn("not one of the choices", result["error"])
        # Nothing was saved on the entry.
        self.assertNotIn("why", restore_entries(self.config)[0])

    def test_skipping_writes_no_why(self):
        # Skipping is the page closing the card and posting
        # nothing: the record stands with no why on it.
        self.make_restore_entry()
        entries = restore_entries(self.config)
        self.assertEqual(len(entries), 1)
        self.assertNotIn("why", entries[0])


# ---------------------------------------------------------------------------
# Held spans: Leave it
# ---------------------------------------------------------------------------


class GoldLeaveWhyTests(WhyServerCase):
    def seed_gold(self, start=2, end=5):
        candidate = {
            "gold_id": webapp._gold_id(start, end, "held"),
            "word_start": start,
            "word_end": end,
            "kind": "held",
            "source": "direction",
            "reason": webapp._GOLD_REASON_HELD,
            "text": "word2 word3 word4",
            "keep_text": None,
            "keep_word_start": None,
            "keep_word_end": None,
            "pair_id": None,
            "pair_key": None,
            "operation": None,
            "status": "open",
            "note": None,
            "created_at": webapp._utc_now(),
            "decided_at": None,
        }
        webapp._record_gold_spans(self.config, [candidate])
        return candidate

    def test_leave_it_offers_the_prompt_and_the_why_lands_on_its_grade(self):
        candidate = self.seed_gold()
        status, result = self.post_json(
            "/api/gold-verdict",
            {
                "gold_id": candidate["gold_id"],
                "decision": "leave",
                "note": "",
            },
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        entry_id = f"gold-grade-{candidate['gold_id']}"
        self.assertEqual(result["why_prompt"], {"entry_id": entry_id})
        status, answer = self.post_json(
            "/api/why",
            {"entry_id": entry_id, "choice": "sound", "note": ""},
        )
        self.assertEqual(status, 200, answer)
        grades = webapp._load_grades(self.config)
        gold_grades = [g for g in grades if g.get("id") == entry_id]
        self.assertEqual(len(gold_grades), 1)
        self.assertEqual(
            gold_grades[0]["why"], {"choice": "sound", "note": ""}
        )
        # A leave verdict writes no restore record.
        self.assertEqual(restore_entries(self.config), [])

    def test_cut_verdict_offers_no_why_prompt(self):
        candidate = self.seed_gold()
        status, result = self.post_json(
            "/api/gold-verdict",
            {
                "gold_id": candidate["gold_id"],
                "decision": "cut",
                "note": "",
            },
        )
        self.assertEqual(status, 200, result)
        self.assertNotIn("why_prompt", result)


# ---------------------------------------------------------------------------
# Checkpoint undo
# ---------------------------------------------------------------------------


class UndoWhyTests(WhyServerCase):
    def test_undo_bringing_back_one_cut_offers_the_prompt(self):
        status, cut = self.post_json(
            "/api/word-range",
            {"word_start": 3, "word_end": 6, "action": "cut"},
        )
        self.assertEqual(status, 200, cut)
        status, result = self.post_json("/api/history/undo", {})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["why_prompt"], {"entry_id": "restore-grade-3-6"}
        )
        entries = restore_entries(self.config)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["word_start"], 3)
        self.assertEqual(entries[0]["word_end"], 6)
        self.assertEqual(entries[0]["source"], "history-undo")
        # And the answer can land on the undo's record too.
        status, _ = self.post_json(
            "/api/why",
            {
                "entry_id": "restore-grade-3-6",
                "choice": "say_it",
                "note": "",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            restore_entries(self.config)[0]["why"],
            {"choice": "say_it", "note": ""},
        )

    def test_undo_bringing_back_two_cuts_writes_none(self):
        # One checkpoint whose state cuts two separate runs.
        config = WebAppConfig(self.job)
        checkpoints.begin(config)
        revised = {
            "source_duration_s": 19.9,
            "kept_ranges": [
                {"start": 0.0, "end": 1.9},
                {"start": 4.0, "end": 9.9},
                {"start": 12.0, "end": 19.9},
            ],
        }
        (config.session_dir / "revised_cuts.json").write_text(
            json.dumps(revised), encoding="utf-8"
        )
        checkpoints.commit(config, "Two cuts at once")
        transcript = webapp._load_json(str(self.job / "transcript.json"))
        timeline = Timeline(transcript, webapp._effective_cuts(config))
        self.assertEqual(timeline.word_status(2), "cut")
        self.assertEqual(timeline.word_status(10), "cut")
        status, result = self.post_json("/api/history/undo", {})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertNotIn("why_prompt", result)
        self.assertEqual(restore_entries(self.config), [])

    def test_redo_offers_no_prompt(self):
        self.post_json(
            "/api/word-range",
            {"word_start": 3, "word_end": 6, "action": "cut"},
        )
        self.post_json("/api/history/undo", {})
        # A redo is not a restore: it offers no prompt. (The
        # grades file is checkpointed state, so walking forward
        # again restores the grades as the cut's checkpoint left
        # them; what matters here is that redo itself records
        # no restore and asks nothing.)
        status, result = self.post_json("/api/history/redo", {})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertNotIn("why_prompt", result)


# ---------------------------------------------------------------------------
# Pattern extraction
# ---------------------------------------------------------------------------


class ExtractPatternsWhyTests(unittest.TestCase):
    def test_why_choices_and_notes_are_collected_per_pattern(self):
        records = [
            {
                "record_id": "r1",
                "pattern_key": "restore:c1",
                "operation_type": "restore_words",
                "verdict": "did_not_work",
                "why": {"choice": "story", "note": "first reason"},
            },
            {
                "record_id": "r2",
                "pattern_key": "restore:c1",
                "operation_type": "restore_words",
                "verdict": "did_not_work",
                "why": {"choice": "story", "note": ""},
            },
            {
                "record_id": "r3",
                "pattern_key": "restore:c1",
                "operation_type": "restore_words",
                "verdict": "did_not_work",
                "why": {"choice": "sound", "note": "second reason"},
            },
            {
                "record_id": "r4",
                "pattern_key": "restore:c1",
                "operation_type": "restore_words",
                "verdict": "did_not_work",
                "why": {"choice": "other", "note": "third reason"},
            },
            {
                "record_id": "r5",
                "pattern_key": "restore:c1",
                "operation_type": "restore_words",
                "verdict": "did_not_work",
                "why": {"choice": "other", "note": "fourth reason"},
            },
            {
                "record_id": "r6",
                "pattern_key": "restore:c1",
                "operation_type": "restore_words",
                "verdict": "did_not_work",
            },
        ]
        patterns = learning.extract_patterns(records)
        self.assertEqual(len(patterns), 1)
        pattern = patterns[0]
        self.assertEqual(
            pattern["why_choices"], {"story": 2, "sound": 1, "other": 2}
        )
        # Up to three non-empty notes, in record order.
        self.assertEqual(
            pattern["why_notes"],
            ["first reason", "second reason", "third reason"],
        )
        # A restore never counts as support, why or not.
        self.assertEqual(pattern["support_count"], 0)

    def test_pattern_without_any_why_has_empty_collections(self):
        patterns = learning.extract_patterns(
            [
                {
                    "record_id": "x1",
                    "pattern_key": "some-cut",
                    "operation_type": "cut_words",
                    "verdict": "worked",
                }
            ]
        )
        self.assertEqual(patterns[0]["why_choices"], {})
        self.assertEqual(patterns[0]["why_notes"], [])


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


class WhyPageTests(unittest.TestCase):
    def why_block(self) -> str:
        html = page_html()
        begin = html.index("// [tye-why-begin]")
        end = html.index("// [tye-why-end]")
        return html[begin:end]

    def test_the_card_is_near_the_transcript_status_and_hidden(self):
        html = page_html()
        status_at = html.index('id="transcriptStatus"')
        card_at = html.index('id="whyCard"')
        self.assertLess(status_at, card_at)
        self.assertLess(card_at, html.index('id="fixWordsBtn"'))
        card = html[card_at:html.index('id="fixWordsBtn"', card_at)]
        self.assertIn("hidden", card)
        for label in (
            "It matters to the story",
            "It sounds better this way",
            "That is how I say it",
            "Other",
            "Skip",
            "Dictate",
            "Save",
        ):
            self.assertIn(label, card)

    def test_the_two_questions_and_their_kinds(self):
        block = self.why_block()
        self.assertIn("function showWhyPrompt(entryId, kind)", block)
        self.assertIn('"Why did you put that back?"', block)
        self.assertIn('"Why are you leaving that in?"', block)
        self.assertIn('kind === "leave"', block)

    def test_choices_post_at_once_and_other_uses_the_note(self):
        block = self.why_block()
        self.assertIn('postJson("/api/why"', block)
        self.assertIn('"story"', block)
        self.assertIn('"sound"', block)
        self.assertIn('"say_it"', block)
        self.assertIn('saveWhyChoice("other")', block)
        # The note box is dictated the same way as the others.
        self.assertIn(
            'wireDictation("whyDictate", "whyNote", "whyDictateStatus")',
            block,
        )

    def test_skip_hides_and_posts_nothing(self):
        block = self.why_block()
        start = block.index("whySkipBtn.addEventListener")
        handler = block[start:block.index("});", start)]
        self.assertIn("hideWhyPrompt()", handler)
        self.assertNotIn("postJson", handler)
        self.assertNotIn("/api/why", handler)

    def test_the_success_handlers_show_the_prompt(self):
        html = page_html()
        # Phrase restore (which also carries a single picked word).
        start = html.index("function runSelectionToggle")
        body = html[start:html.index("// Backspace / Delete", start)]
        self.assertIn('action === "restore"', body)
        self.assertIn("data.why_prompt", body)
        self.assertIn('showWhyPrompt(data.why_prompt.entry_id, "restore")', body)
        # The edit card's Bring back.
        start = html.index("function runEditAction")
        body = html[start:html.index("editBringBack.addEventListener", start)]
        self.assertIn('action === "restore"', body)
        self.assertIn('showWhyPrompt(data.why_prompt.entry_id, "restore")', body)
        # A held spot left in.
        start = html.index("function runGoldVerdict")
        body = html[start:html.index("editGoldCut.addEventListener", start)]
        self.assertIn('decision === "leave"', body)
        self.assertIn('showWhyPrompt(data.why_prompt.entry_id, "leave")', body)
        # An undo that brought words back.
        start = html.index("function moveHistory")
        body = html[start:html.index("undoBtn.addEventListener", start)]
        self.assertIn('which === "undo"', body)
        self.assertIn('showWhyPrompt(data.why_prompt.entry_id, "restore")', body)


if __name__ == "__main__":
    unittest.main()
