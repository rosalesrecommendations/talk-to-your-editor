"""Tests for the after-review page and the pattern-learning draft.

Covers the Builder B gates from PLAN.md:
- After-review gate: every input comment appears exactly once, statuses
  carry through, and the page exports a grading JSON with verdict and
  notes fields (shape present in the template script).
- Learning gate: a repeated pattern backed by worked/partly verdicts
  becomes a candidate rule marked unapproved; one-off patterns and
  did-not-work patterns do not; the active template is never mutated.

Run from the repository root:
    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
EXAMPLES = REPO_ROOT / "examples"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from talk_to_your_editor import after_review, learning  # noqa: E402


FEEDBACK = {
    "video": "sample-video",
    "runner_version": "0.0.0-sample",
    "exported_at": "2026-10-26T16:00:00Z",
    "feedback_done": True,
    "comments": [
        {"id": "c1", "ts": 12.0, "fmt": "vertical",
         "text": "Take the repeat out here.", "done": True},
        {"id": "c2", "ts": 64.0, "fmt": "vertical",
         "text": "This opening line must stay exactly as it is.", "done": True},
        {"id": "c3", "ts": 148.5, "fmt": "landscape",
         "text": "Swap in the other take for this part.", "done": True},
        {"id": "c4", "ts": 201.0, "fmt": "vertical",
         "text": "Tighten the long pause before this section.", "done": True},
    ],
}

OPERATIONS = {
    "operations": [
        {"comment_id": "c1", "type": "cut_words", "status": "applied",
         "plain_action": "Removed the repeated sentence."},
        {"comment_id": "c2", "type": "flag_for_review", "status": "flagged",
         "plain_action": "No change. This span is protected, so a person decides."},
        {"comment_id": "c3", "type": "replace_take", "status": "rejected",
         "plain_action": "The swap was rejected by the safety verifier."},
        # c4 intentionally has no operation: it must still appear, flagged.
    ]
}


def _row(page: str, comment_id: str) -> str:
    start = page.index(f'data-comment-id="{comment_id}"')
    end = page.index("</article>", start)
    return page[start:end]


class AfterReviewPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = after_review.render_after_review(FEEDBACK, OPERATIONS)

    def test_every_comment_appears_exactly_once(self) -> None:
        for comment in FEEDBACK["comments"]:
            cid = comment["id"]
            self.assertEqual(
                self.page.count(f'data-comment-id="{cid}"'), 1,
                f"comment {cid} should render as exactly one row",
            )
            self.assertEqual(
                self.page.count(comment["text"]), 1,
                f"comment {cid} text should appear exactly once",
            )

    def test_timestamps_and_text_present(self) -> None:
        for comment in FEEDBACK["comments"]:
            row = _row(self.page, comment["id"])
            self.assertIn(after_review.format_timestamp(comment["ts"]), row)
            self.assertIn(comment["text"], row)

    def test_format_name_is_a_badge_not_the_timestamp(self) -> None:
        # Regression: in a real runner export ``fmt`` is the format name
        # (vertical / landscape). It must never render in the timestamp slot.
        for comment in FEEDBACK["comments"]:
            row = _row(self.page, comment["id"])
            self.assertIn(f'>{comment["fmt"]}</span>', row)
            ts_span = row.split('<span class="ts">', 1)[1].split("</span>", 1)[0]
            self.assertEqual(
                ts_span, after_review.format_timestamp(comment["ts"])
            )

    def test_statuses_carry_through(self) -> None:
        self.assertIn('data-status="applied"', _row(self.page, "c1"))
        self.assertIn("Removed the repeated sentence.", _row(self.page, "c1"))
        self.assertIn('data-status="flagged"', _row(self.page, "c2"))
        self.assertIn('data-status="rejected"', _row(self.page, "c3"))
        # A comment with no operation still appears, flagged for a person.
        self.assertIn('data-status="flagged"', _row(self.page, "c4"))
        self.assertIn("No automatic change was made", _row(self.page, "c4"))

    def test_verdict_and_notes_controls_present(self) -> None:
        for comment in FEEDBACK["comments"]:
            row = _row(self.page, comment["id"])
            for verdict in ("worked", "partly", "did_not_work"):
                self.assertIn(f'value="{verdict}"', row)
            self.assertIn('class="notes"', row)

    def test_grading_export_shape_exists_in_template_script(self) -> None:
        for token in (
            '"record_type": "graded_corrections"',
            '"grading_version": 1',
            '"comment_id"',
            '"original_text"',
            '"status"',
            '"action_taken"',
            '"verdict"',
            '"notes"',
            '"exported_at"',
            "localStorage.setItem",
            "localStorage.getItem",
        ):
            self.assertIn(token, self.page, f"missing export token: {token}")

    def test_page_is_self_contained(self) -> None:
        self.assertNotIn("http://", self.page)
        self.assertNotIn("https://", self.page)

    def test_video_is_referenced_not_embedded(self) -> None:
        with_video = after_review.render_after_review(
            FEEDBACK, OPERATIONS, video_filename="revised-review.mp4"
        )
        self.assertIn("<video", with_video)
        self.assertIn('src="revised-review.mp4"', with_video)
        self.assertNotIn("data:video", with_video)
        self.assertNotIn("<video", self.page)

    def test_generate_after_review_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            feedback_path = tmp_path / "feedback.json"
            ops_path = tmp_path / "operations.json"
            out_path = tmp_path / "after.html"
            feedback_path.write_text(json.dumps(FEEDBACK), encoding="utf-8")
            ops_path.write_text(json.dumps(OPERATIONS), encoding="utf-8")
            result = after_review.generate_after_review(
                feedback_path, ops_path, out_path, video_filename="revised.mp4"
            )
            self.assertEqual(result, out_path)
            page = out_path.read_text(encoding="utf-8")
            self.assertIn('data-comment-id="c1"', page)
            self.assertIn('src="revised.mp4"', page)


class LearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.records = learning.load_graded_records(
            EXAMPLES / "graded-corrections.sample.json"
        )
        self.draft = learning.build_template_draft(self.records)

    def test_repeated_supported_pattern_becomes_unapproved_candidate(self) -> None:
        by_key = {c["pattern_key"]: c for c in self.draft["candidate_rules"]}
        self.assertIn("remove_repeated_line", by_key)
        candidate = by_key["remove_repeated_line"]
        self.assertIs(candidate["approved"], False)
        self.assertEqual(candidate["support_count"], 2)
        self.assertEqual(
            sorted(candidate["supporting_record_ids"]), ["rec-001", "rec-002"]
        )

    def test_one_off_pattern_does_not_become_candidate(self) -> None:
        keys = {c["pattern_key"] for c in self.draft["candidate_rules"]}
        self.assertNotIn("tighten_long_join_pause", keys)
        considered = {
            p["pattern_key"]: p for p in self.draft["patterns_considered"]
        }
        self.assertEqual(considered["tighten_long_join_pause"]["support_count"], 1)
        self.assertIs(considered["tighten_long_join_pause"]["became_candidate"], False)

    def test_did_not_work_pattern_does_not_become_candidate(self) -> None:
        keys = {c["pattern_key"] for c in self.draft["candidate_rules"]}
        self.assertNotIn("restore_cut_sentence", keys)
        considered = {
            p["pattern_key"]: p for p in self.draft["patterns_considered"]
        }
        self.assertEqual(considered["restore_cut_sentence"]["support_count"], 0)

    def test_draft_declares_inference_not_training(self) -> None:
        self.assertEqual(self.draft["method"], "inference")
        self.assertIn("not model training", self.draft["method_note"])
        self.assertIs(self.draft["active_template_modified"], False)

    def test_active_template_is_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            active_path = tmp_path / "creator-template.json"
            active_path.write_bytes(
                (EXAMPLES / "creator-template.sample.json").read_bytes()
            )
            before = hashlib.sha256(active_path.read_bytes()).hexdigest()
            draft_path = tmp_path / "draft.json"
            learning.write_template_draft(
                self.draft, draft_path, active_template_path=active_path
            )
            after = hashlib.sha256(active_path.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            written = json.loads(draft_path.read_text(encoding="utf-8"))
            self.assertTrue(written["candidate_rules"])
            self.assertTrue(
                all(c["approved"] is False for c in written["candidate_rules"])
            )

    def test_refuses_to_write_draft_over_active_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            active_path = Path(tmp) / "creator-template.json"
            active_path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                learning.write_template_draft(
                    self.draft, active_path, active_template_path=active_path
                )
            self.assertEqual(active_path.read_text(encoding="utf-8"), "{}")


class PairInferenceTests(unittest.TestCase):
    PAIRS = [
        {
            "record_id": "pair-001",
            "cadence": {"words_per_minute": 160, "target_join_pause_s": 0.2,
                        "avg_sentence_words": 13},
            "structure": {"hook_style": "Question aimed at the viewer",
                          "segment_order": ["hook", "setup", "steps", "recap"]},
            "candidate_rules": [
                {"pattern_key": "remove_repeated_line", "operation_type": "cut_words",
                 "statement": "Cut repeated lines, keep the first clean delivery."}
            ],
        },
        {
            "record_id": "pair-002",
            "cadence": {"words_per_minute": 170, "target_join_pause_s": 0.22,
                        "avg_sentence_words": 15},
            "structure": {"hook_style": "Question aimed at the viewer",
                          "segment_order": ["hook", "setup", "steps", "recap"]},
            "candidate_rules": [
                {"pattern_key": "remove_repeated_line", "operation_type": "cut_words",
                 "statement": "Cut repeated lines, keep the first clean delivery."}
            ],
        },
    ]

    def test_pair_inference_produces_unapproved_candidates_with_evidence(self) -> None:
        draft = learning.infer_template_from_pairs(self.PAIRS)
        self.assertEqual(draft["method"], "inference")
        self.assertIn("not model training", draft["method_note"])
        cadence = draft["cadence_candidates"]["words_per_minute"]
        self.assertEqual(cadence["value"], 165.0)
        self.assertEqual(cadence["evidence_record_ids"], ["pair-001", "pair-002"])
        self.assertIs(cadence["approved"], False)
        structure = draft["structure_candidates"]["hook_style"]
        self.assertEqual(structure["value"], "Question aimed at the viewer")
        self.assertEqual(structure["evidence_record_ids"], ["pair-001", "pair-002"])
        self.assertIs(structure["approved"], False)
        rules = draft["candidate_rules"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["support_count"], 2)
        self.assertEqual(rules[0]["supporting_record_ids"], ["pair-001", "pair-002"])
        self.assertIs(rules[0]["approved"], False)


if __name__ == "__main__":
    unittest.main()
