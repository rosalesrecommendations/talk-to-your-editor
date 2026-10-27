"""Core tests for Talk to Your Editor (stdlib unittest, no dependencies)."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.applier import apply_verdicts
from talk_to_your_editor.meta_provider import MockProvider
from talk_to_your_editor.schema import SchemaError, validate_operations
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.verifier import verify_operations

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_op(**overrides):
    op = {
        "type": "cut_words",
        "comment_id": "t1",
        "word_start": 16,
        "word_end": 24,
        "reason": "test cut",
        "confidence": 0.9,
        "model": "muse-spark-1.3",
        "risk_tier": 1,
        "wonder_reviewed": False,
    }
    op.update(overrides)
    # A None override removes the field (e.g. word spans on a pause op).
    return {k: v for k, v in op.items() if v is not None}


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.transcript = load("sample_transcript.json")
        self.cuts = load("sample_cuts.json")
        self.feedback = load("sample_feedback.json")
        self.profile = load("sample_profile.json")
        self.timeline = Timeline(self.transcript, self.cuts)


class SchemaTests(unittest.TestCase):
    def test_valid_operations_pass(self):
        ops = validate_operations({"operations": [make_op()]})
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].type, "cut_words")

    def test_unknown_field_rejected(self):
        with self.assertRaises(SchemaError) as ctx:
            validate_operations([make_op(shell="rm -rf /")])
        self.assertIn("unknown_field:shell", str(ctx.exception))

    def test_unknown_type_rejected(self):
        with self.assertRaises(SchemaError) as ctx:
            validate_operations([make_op(type="run_command")])
        self.assertIn("unknown_operation_type", str(ctx.exception))

    def test_contributor_model_rejected(self):
        with self.assertRaises(SchemaError) as ctx:
            validate_operations([make_op(model="muse-spark-1.3-contributor")])
        self.assertIn("contributor_model_forbidden", str(ctx.exception))

    def test_invalid_range_and_confidence_rejected(self):
        with self.assertRaises(SchemaError) as ctx:
            validate_operations([make_op(word_start=24, word_end=24)])
        self.assertIn("invalid_word_range", str(ctx.exception))
        with self.assertRaises(SchemaError) as ctx:
            validate_operations([make_op(confidence=1.5)])
        self.assertIn("confidence_out_of_range", str(ctx.exception))

    def test_overlapping_operations_rejected(self):
        with self.assertRaises(SchemaError) as ctx:
            validate_operations(
                [
                    make_op(comment_id="a"),
                    make_op(comment_id="b", word_start=20, word_end=28),
                ]
            )
        self.assertIn("overlapping_operations", str(ctx.exception))

    def test_pause_target_bounds(self):
        with self.assertRaises(SchemaError) as ctx:
            validate_operations(
                [
                    make_op(
                        type="set_join_pause",
                        word_start=None,
                        word_end=None,
                        join_index=2,
                        target_pause_s=0.9,
                    )
                ]
            )
        self.assertIn("target_pause_out_of_range", str(ctx.exception))


class TimelineTests(FixtureCase):
    def test_mapping_at_start(self):
        mapping = self.timeline.map_output_time(0.5)
        self.assertEqual(mapping["range_index"], 0)
        self.assertEqual(mapping["word_index"], 0)
        self.assertAlmostEqual(mapping["source_time"], 1.3, places=3)

    def test_mapping_inside_range(self):
        mapping = self.timeline.map_output_time(6.2)
        self.assertEqual(mapping["word_index"], 17)
        self.assertAlmostEqual(mapping["source_time"], 11.3, places=3)

    def test_mapping_after_a_cut(self):
        # Words 8-15 are cut in the source; output 12.2 lands on source
        # word 27 ("you") at source time 17.7.
        mapping = self.timeline.map_output_time(12.2)
        self.assertEqual(mapping["word_index"], 27)
        self.assertAlmostEqual(mapping["source_time"], 17.7, places=3)

    def test_mapping_special_insert_is_protected(self):
        mapping = self.timeline.map_output_time(5.05)
        self.assertTrue(mapping["special"])
        self.assertTrue(mapping["protected"])
        self.assertIsNone(mapping["word_index"])

    def test_word_status(self):
        self.assertEqual(self.timeline.word_status(0), "kept")
        self.assertEqual(self.timeline.word_status(10), "cut")
        self.assertEqual(self.timeline.word_status(39), "kept")


class ApplyTests(FixtureCase):
    def _run_ops(self, raw_ops):
        ops = validate_operations(raw_ops)
        verdicts = verify_operations(ops, self.timeline, self.profile)
        revised, audit = apply_verdicts(self.cuts, self.timeline, verdicts)
        return verdicts, revised, audit

    def test_cut_applies_only_intended_span(self):
        before_hash = hashlib.sha256(
            (FIXTURES / "sample_cuts.json").read_bytes()
        ).hexdigest()
        original_copy = copy.deepcopy(self.cuts)
        verdicts, revised, audit = self._run_ops([make_op()])
        self.assertEqual(verdicts[0].status, "apply")
        self.assertEqual(audit["before"]["kept_words"], 32)
        self.assertEqual(audit["after"]["kept_words"], 24)
        self.assertEqual(audit["after"]["output_duration_s"], 15.2)
        new_timeline = Timeline(self.transcript, revised)
        for i in range(16, 24):
            self.assertEqual(new_timeline.word_status(i), "cut")
        self.assertEqual(new_timeline.word_status(0), "kept")
        self.assertEqual(new_timeline.word_status(24), "kept")
        # Input plan object and the fixture file are both unchanged.
        self.assertEqual(self.cuts, original_copy)
        after_hash = hashlib.sha256(
            (FIXTURES / "sample_cuts.json").read_bytes()
        ).hexdigest()
        self.assertEqual(before_hash, after_hash)
        # Special insert preserved untouched.
        specials = [r for r in revised["kept_ranges"] if "insert_kind" in r]
        self.assertEqual(len(specials), 1)
        self.assertEqual(specials[0]["start"], 6.1667)

    def test_protected_prayer_flagged(self):
        verdicts, revised, _ = self._run_ops(
            [make_op(word_start=24, word_end=32, reason="cut the prayer")]
        )
        self.assertEqual(verdicts[0].status, "flag")
        self.assertTrue(
            any(r.startswith("protected_span") for r in verdicts[0].reasons)
        )
        self.assertEqual(revised["kept_ranges"], self.cuts["kept_ranges"])

    def test_profile_protected_closing_flagged(self):
        verdicts, _, _ = self._run_ops(
            [make_op(word_start=37, word_end=40, reason="cut the closing")]
        )
        self.assertEqual(verdicts[0].status, "flag")
        self.assertTrue(
            any("closing call to action" in r for r in verdicts[0].reasons)
        )

    def test_post_edit_signature_span_protected(self):
        verdicts, _, _ = self._run_ops(
            [make_op(word_start=30, word_end=32, reason="trim near the insert")]
        )
        self.assertEqual(verdicts[0].status, "flag")
        self.assertTrue(
            any(r.startswith("protected_span") for r in verdicts[0].reasons)
        )

    def test_negation_span_flagged(self):
        verdicts, _, _ = self._run_ops(
            [make_op(word_start=32, word_end=37, reason="cut this bit")]
        )
        self.assertEqual(verdicts[0].status, "flag")
        self.assertIn("negation_bearing_span", verdicts[0].reasons)

    def test_over_25_percent_flagged(self):
        verdicts, _, _ = self._run_ops(
            [
                make_op(
                    word_start=16,
                    word_end=40,
                    reason="cut almost everything",
                    risk_tier=2,
                    wonder_reviewed=True,
                )
            ]
        )
        self.assertEqual(verdicts[0].status, "flag")
        self.assertIn("removes_over_25_percent", verdicts[0].reasons)

    def test_restore_requires_wonder(self):
        raw = make_op(
            type="restore_words",
            word_start=8,
            word_end=16,
            risk_tier=2,
            reason="restore the first take",
        )
        verdicts, _, _ = self._run_ops([raw])
        self.assertEqual(verdicts[0].status, "flag")
        self.assertIn("wonder_review_required", verdicts[0].reasons)

    def test_restore_applies_with_wonder_review(self):
        raw = make_op(
            type="restore_words",
            word_start=8,
            word_end=16,
            risk_tier=2,
            wonder_reviewed=True,
            model="muse-wonder-1.0",
            reason="restore the first take",
        )
        verdicts, revised, audit = self._run_ops([raw])
        self.assertEqual(verdicts[0].status, "apply")
        new_timeline = Timeline(self.transcript, revised)
        for i in range(8, 16):
            self.assertEqual(new_timeline.word_status(i), "kept")
        self.assertEqual(audit["after"]["cut_words"], 0)

    def test_pause_adjustment_applies(self):
        raw = make_op(
            type="set_join_pause",
            word_start=None,
            word_end=None,
            join_index=2,
            target_pause_s=0.4,
            reason="lengthen the pause before the prayer",
        )
        verdicts, revised, _ = self._run_ops([raw])
        self.assertEqual(verdicts[0].status, "apply")
        self.assertAlmostEqual(revised["kept_ranges"][3]["start"], 15.5, places=3)
        self.assertEqual(revised["join_pauses_s"][2], 0.4)

    def test_pause_out_of_bounds_rejected(self):
        raw = make_op(
            type="set_join_pause",
            word_start=None,
            word_end=None,
            join_index=4,
            target_pause_s=0.5,
            reason="pause at a join with no headroom",
        )
        verdicts, _, _ = self._run_ops([raw])
        self.assertEqual(verdicts[0].status, "reject")
        self.assertIn("pause_out_of_bounds", verdicts[0].reasons)

    def test_replace_take_atomic(self):
        raw = make_op(
            type="replace_take",
            word_start=None,
            word_end=None,
            cut_word_start=16,
            cut_word_end=24,
            restore_word_start=8,
            restore_word_end=16,
            risk_tier=2,
            wonder_reviewed=True,
            model="muse-wonder-1.0",
            reason="use the first take instead",
        )
        verdicts, revised, _ = self._run_ops([raw])
        self.assertEqual(verdicts[0].status, "apply")
        new_timeline = Timeline(self.transcript, revised)
        for i in range(8, 16):
            self.assertEqual(new_timeline.word_status(i), "kept")
        for i in range(16, 24):
            self.assertEqual(new_timeline.word_status(i), "cut")

    def test_overlapping_edits_first_applies_second_flagged(self):
        # Regression: two notes whose edits share words used to take
        # the whole run down. Now the first edit that checks out
        # applies and the conflicting one is flagged on its own note
        # with a plain reason.
        ops = validate_operations(
            [make_op(comment_id="first")]
        ) + validate_operations(
            [make_op(comment_id="second", word_start=18, word_end=22)]
        )
        verdicts = verify_operations(ops, self.timeline, self.profile)
        by_comment = {v.operation.comment_id: v for v in verdicts}
        self.assertEqual(by_comment["first"].status, "apply")
        self.assertEqual(by_comment["second"].status, "flag")
        self.assertTrue(
            any(
                "overlaps another note" in r
                for r in by_comment["second"].reasons
            ),
            by_comment["second"].reasons,
        )
        revised, audit = apply_verdicts(self.cuts, self.timeline, verdicts)
        self.assertEqual(audit["applied_comment_ids"], ["first"])
        self.assertEqual(audit["flagged_comment_ids"], ["second"])
        self.assertEqual(audit["after"]["kept_words"], 24)

    def test_replace_take_invalid_half_rejected_nothing_applied(self):
        raw = make_op(
            type="replace_take",
            word_start=None,
            word_end=None,
            cut_word_start=16,
            cut_word_end=24,
            restore_word_start=20,  # words 20-27 are kept, not cut
            restore_word_end=28,
            risk_tier=2,
            wonder_reviewed=True,
            model="muse-wonder-1.0",
            reason="bad replace",
        )
        verdicts, revised, _ = self._run_ops([raw])
        self.assertEqual(verdicts[0].status, "reject")
        self.assertEqual(revised["kept_ranges"], self.cuts["kept_ranges"])


class MockDirectTests(FixtureCase):
    def test_mock_direct_end_to_end(self):
        provider = MockProvider()
        operations, routing = provider.direct(
            self.feedback, self.timeline, self.profile
        )
        self.assertEqual(len(operations), 4)
        ops = validate_operations(operations)
        verdicts = verify_operations(ops, self.timeline, self.profile)
        by_comment = {v.operation.comment_id: v for v in verdicts}
        self.assertEqual(by_comment["c1"].status, "apply")   # repeated line cut
        self.assertEqual(by_comment["c2"].status, "flag")    # protected prayer
        self.assertEqual(by_comment["c3"].status, "flag")    # negation span
        self.assertEqual(by_comment["c4"].status, "apply")   # pause change
        revised, audit = apply_verdicts(self.cuts, self.timeline, verdicts)
        self.assertEqual(audit["applied_comment_ids"], ["c4", "c1"])
        self.assertEqual(audit["after"]["kept_words"], 24)
        new_timeline = Timeline(self.transcript, revised)
        self.assertEqual(new_timeline.word_status(27), "kept")  # prayer intact


class SliverAbsorptionTests(unittest.TestCase):
    """Regression: a cut that splits a kept range and leaves a sub-0.10s
    remnant absorbs the remnant when it holds no kept word outside the
    target span, and still rejects when it does."""

    def _job(self, sliver_word: bool):
        words = []
        for i in range(60):
            start = i * 0.6
            words.append({"word": f"w{i}", "start": start, "end": start + 0.5})
        if sliver_word:
            # Word 33 becomes a very short kept word sitting inside the
            # trailing padding of the first range.
            words[33] = {
                "word": "w33",
                "start": words[32]["end"] + 0.02,
                "end": words[32]["end"] + 0.06,
            }
            range_end = words[33]["end"] + 0.03
        else:
            range_end = words[32]["end"] + 0.08
        transcript = {"words": words, "duration": 36.0}
        cuts = {
            "kept_ranges": [
                {"start": 0.0, "end": range_end},
                {"start": words[40]["start"], "end": words[59]["end"]},
            ],
            "source_duration_s": 36.0,
        }
        return words, transcript, cuts

    def _run(self, transcript, cuts):
        ops = validate_operations(
            [make_op(comment_id="sliver", word_start=30, word_end=33)]
        )
        timeline = Timeline(transcript, cuts)
        verdicts = verify_operations(ops, timeline, None)
        revised, audit = apply_verdicts(cuts, timeline, verdicts)
        return verdicts, revised, audit

    def test_word_free_padding_sliver_absorbs_and_applies(self):
        words, transcript, cuts = self._job(sliver_word=False)
        verdicts, revised, audit = self._run(transcript, cuts)
        self.assertEqual(verdicts[0].status, "apply")
        self.assertEqual(audit["before"]["kept_words"], 53)
        self.assertEqual(audit["after"]["kept_words"], 50)
        # The cut extends to the range edge: no tiny range is left behind.
        first = revised["kept_ranges"][0]
        self.assertAlmostEqual(first["start"], 0.0, places=6)
        self.assertAlmostEqual(first["end"], words[30]["start"], places=6)
        self.assertEqual(len(revised["kept_ranges"]), 2)
        self.assertEqual(len(audit["absorbed_slivers"]), 1)
        entry = audit["absorbed_slivers"][0]
        self.assertEqual(entry["comment_id"], "sliver")
        self.assertAlmostEqual(entry["absorbed_s"], 0.08, places=3)

    def test_preexisting_tiny_range_elsewhere_does_not_block(self):
        # The foundation plan already ships a 0.06s range of its own; an
        # unrelated clean cut must not be refused because of it.
        words = []
        for i in range(60):
            start = i * 0.6
            words.append({"word": f"w{i}", "start": start, "end": start + 0.5})
        transcript = {"words": words, "duration": 36.0}
        cuts = {
            "kept_ranges": [
                {"start": 0.0, "end": words[19]["end"]},
                {
                    "start": words[25]["start"],
                    "end": words[25]["start"] + 0.06,
                },
                {"start": words[40]["start"], "end": words[59]["end"]},
            ],
            "source_duration_s": 36.0,
        }
        ops = validate_operations(
            [make_op(comment_id="clean", word_start=12, word_end=15)]
        )
        timeline = Timeline(transcript, cuts)
        verdicts = verify_operations(ops, timeline, None)
        self.assertEqual(verdicts[0].status, "apply")
        revised, audit = apply_verdicts(cuts, timeline, verdicts)
        self.assertEqual(audit["before"]["kept_words"], 40)
        self.assertEqual(audit["after"]["kept_words"], 37)
        self.assertEqual(audit["absorbed_slivers"], [])
        # The foundation's own tiny range is left exactly as it was.
        self.assertIn(
            {"start": words[25]["start"], "end": words[25]["start"] + 0.06},
            revised["kept_ranges"],
        )

    def test_sliver_holding_another_kept_word_still_rejects(self):
        words, transcript, cuts = self._job(sliver_word=True)
        verdicts, revised, audit = self._run(transcript, cuts)
        self.assertEqual(verdicts[0].status, "reject")
        self.assertIn("min_range_duration", verdicts[0].reasons)
        self.assertEqual(revised["kept_ranges"], cuts["kept_ranges"])
        self.assertEqual(audit["absorbed_slivers"], [])


if __name__ == "__main__":
    unittest.main()
