"""Tests for the v0.7.1 backend: gold opportunities (stdlib
unittest, no dependencies).

Covers the involvement mode (usual persistence, write-back through
the front door, drop settings), gold spans from each source (held
direction operations, retake suspicions), gold verdicts (apply and
settle, the learning feed, rule surfacing with the note as
context), and the negation policy in both modes: a reviewed repeat
applies in the Cleanest cut mode, a unique span goes gold, and in
Tailor it mode everything negation-bearing goes gold.

Every test isolates the usual store the way the v0.7.0 backend
tests do: the home folder is a temp dir for the whole case.
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

from talk_to_your_editor import usual, webapp
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig


def write_job(job: Path, words: list, profile: dict | None = None) -> None:
    """A minimal job: one kept range covering every word."""
    job.mkdir(parents=True, exist_ok=True)
    transcript = {
        "engine": "test",
        "duration": float(words[-1]["end"]) if words else 0.0,
        "words": words,
    }
    cuts = {
        "source_duration_s": transcript["duration"],
        "kept_ranges": [
            {"start": 0.0, "end": transcript["duration"]}
        ],
    }
    (job / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")
    if profile is not None:
        (job / "profile.json").write_text(
            json.dumps(profile), encoding="utf-8"
        )


class IsolatedHomeCase(unittest.TestCase):
    """A temp home folder, so the usual store is the test's own."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v071-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(
            os.environ, {"HOME": str(self.home)}
        )
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def usual_path(self) -> Path:
        return self.home / ".talk-to-your-editor" / "usual.json"


# ---------------------------------------------------------------------------
# The involvement mode
# ---------------------------------------------------------------------------


class ModeTests(IsolatedHomeCase):
    def test_mode_round_trips_through_the_usual(self):
        settings = dict(webapp.FRONT_DOOR_DEFAULTS)
        settings["mode"] = "tailor"
        usual.save_usual(settings)
        payload = json.loads(self.usual_path().read_text(encoding="utf-8"))
        self.assertEqual(payload["mode"], "tailor")
        loaded = usual.load_usual(dict(webapp.FRONT_DOOR_DEFAULTS))
        self.assertEqual(loaded["mode"], "tailor")

    def test_mode_defaults_to_cleanest(self):
        loaded = usual.load_usual(dict(webapp.FRONT_DOOR_DEFAULTS))
        self.assertEqual(loaded["mode"], "cleanest")
        write_job(self.job, [{"word": "hi", "start": 0.0, "end": 0.4}])
        config = WebAppConfig(self.job)
        self.assertEqual(
            webapp._front_door_settings(config)["mode"], "cleanest"
        )

    def test_saved_mode_seeds_the_front_door(self):
        settings = dict(webapp.FRONT_DOOR_DEFAULTS)
        settings["mode"] = "tailor"
        usual.save_usual(settings)
        write_job(self.job, [{"word": "hi", "start": 0.0, "end": 0.4}])
        config = WebAppConfig(self.job)
        self.assertEqual(
            webapp._front_door_settings(config)["mode"], "tailor"
        )

    def test_drop_settings_carry_a_valid_mode_only(self):
        validated = webapp._validated_drop_settings(
            {"settings": {"mode": "tailor"}}
        )
        self.assertEqual(validated, {"mode": "tailor"})
        validated = webapp._validated_drop_settings(
            {"settings": {"mode": "sometimes"}}
        )
        self.assertEqual(validated, {})

    def test_front_door_rejects_an_unknown_mode(self):
        write_job(self.job, [{"word": "hi", "start": 0.0, "end": 0.4}])
        config = WebAppConfig(self.job)
        with self.assertRaises(ValueError):
            webapp._run_front_door(config, {"mode": "sometimes"})

    def test_mode_lines_name_the_mode(self):
        write_job(self.job, [{"word": "hi", "start": 0.0, "end": 0.4}])
        config = WebAppConfig(self.job)
        lines = webapp._mode_and_gold_lines(
            config, dict(webapp.FRONT_DOOR_DEFAULTS)
        )
        self.assertEqual(
            lines, ["Your way of working: Cleanest cut."]
        )
        settings = dict(webapp.FRONT_DOOR_DEFAULTS)
        settings["mode"] = "tailor"
        lines = webapp._mode_and_gold_lines(config, settings)
        self.assertEqual(
            lines,
            [
                "Your way of working: Tailor it with me.",
                "No spots are waiting in gold.",
            ],
        )


# ---------------------------------------------------------------------------
# The negation policy, through the direction pass
# ---------------------------------------------------------------------------

# The repeated line: thirteen words, two negations, long enough
# (thirteen seconds at one word per second) that the verifier asks
# for the second review.
REPEATED_LINE = (
    "i am not giving up on this because the story is not over"
).split()
# The same shape, said once only, about something else.
UNIQUE_LINE = (
    "i am not afraid of the winter because the cold is not forever"
).split()


def policy_words(second_line):
    """Eighty words: the first line at 10..23, a second at 40..53."""
    tokens = [f"word{i}" for i in range(80)]
    tokens[3] = "friends."
    tokens[10:10 + len(REPEATED_LINE)] = REPEATED_LINE
    tokens[40:40 + len(second_line)] = second_line
    return [
        {"word": token, "start": float(i), "end": float(i) + 0.9}
        for i, token in enumerate(tokens)
    ]


def negation_operation(word_start, word_end, reviewed=True):
    return {
        "type": "cut_words",
        "comment_id": "direction-pass",
        "target_comment_id": "direction-pass",
        "word_start": word_start,
        "word_end": word_end,
        "reason": "This repeats what was already said.",
        "confidence": 0.9,
        "model": "muse-spark-1.3",
        "risk_tier": 2,
        "wonder_reviewed": reviewed,
    }


class StubProvider:
    def __init__(self, operations):
        self._operations = operations

    def direct_transcript(self, packet, timeline, profile=None):
        return list(self._operations), [], []

    def drain_usage(self):
        return []


class NegationPolicyTests(IsolatedHomeCase):
    def setUp(self) -> None:
        super().setUp()
        self.settings = dict(webapp.FRONT_DOOR_DEFAULTS)

    def run_pass(self, second_line, operation, remainder="keep it tight"):
        write_job(self.job, policy_words(second_line))
        self.config = WebAppConfig(self.job)
        self.config.session_api_key = "test-key"
        stub = StubProvider([operation])
        with mock.patch.object(
            webapp, "_direction_provider", return_value=stub
        ):
            return webapp._run_direction_pass(
                self.config, remainder, self.settings
            )

    def word_status(self, index):
        transcript = json.loads(
            (self.job / "transcript.json").read_text(encoding="utf-8")
        )
        timeline = Timeline(
            transcript, webapp._effective_cuts(self.config)
        )
        return timeline.word_status(index)

    def session_operations(self):
        audit = json.loads(
            (self.config.session_dir / "operations_audit.json").read_text(
                encoding="utf-8"
            )
        )
        return audit["operations"]

    def test_reviewed_repeat_applies_in_cleanest_mode(self):
        summary = self.run_pass(
            REPEATED_LINE, negation_operation(40, 53)
        )
        self.assertEqual(summary["mode"], "cleanest")
        self.assertEqual(summary["applied"], 1)
        self.assertEqual(summary["negation_applied"], 1)
        self.assertEqual(self.word_status(40), "cut")
        self.assertEqual(self.word_status(52), "cut")
        # The first saying stays, word for word.
        self.assertEqual(self.word_status(10), "kept")
        entry = next(
            op
            for op in self.session_operations()
            if op.get("word_start") == 40
        )
        self.assertEqual(entry["status"], "applied")
        self.assertEqual(entry["negation_policy"], "duplicate")
        # Nothing waits in gold for a settled cut.
        self.assertEqual(webapp._open_gold_spans(self.config), [])

    def test_repeat_goes_gold_in_tailor_mode(self):
        self.settings["mode"] = "tailor"
        summary = self.run_pass(
            REPEATED_LINE, negation_operation(40, 53)
        )
        self.assertEqual(summary["applied"], 0)
        self.assertEqual(summary["negation_applied"], 0)
        self.assertEqual(self.word_status(40), "kept")
        gold = webapp._open_gold_spans(self.config)
        self.assertEqual(len(gold), 1)
        self.assertEqual(gold[0]["kind"], "negation")
        self.assertEqual(gold[0]["word_start"], 40)
        self.assertEqual(gold[0]["word_end"], 53)
        self.assertIn("negative statement", gold[0]["reason"])

    def test_unique_span_goes_gold_in_cleanest_mode(self):
        summary = self.run_pass(
            UNIQUE_LINE, negation_operation(40, 53)
        )
        self.assertEqual(summary["applied"], 0)
        self.assertEqual(summary["negation_applied"], 0)
        self.assertEqual(self.word_status(40), "kept")
        gold = webapp._open_gold_spans(self.config)
        self.assertEqual(len(gold), 1)
        self.assertEqual(gold[0]["kind"], "negation")

    def test_named_span_applies_in_cleanest_mode(self):
        summary = self.run_pass(
            UNIQUE_LINE,
            negation_operation(40, 53),
            remainder=(
                "keep it tight, and take out the part where i say "
                "i am not afraid of the winter"
            ),
        )
        self.assertEqual(summary["applied"], 1)
        self.assertEqual(summary["negation_applied"], 1)
        entry = next(
            op
            for op in self.session_operations()
            if op.get("word_start") == 40
        )
        self.assertEqual(entry["negation_policy"], "named")

    def test_unreviewed_negation_cut_goes_gold(self):
        summary = self.run_pass(
            REPEATED_LINE, negation_operation(40, 53, reviewed=False)
        )
        self.assertEqual(summary["applied"], 0)
        self.assertEqual(summary["negation_applied"], 0)
        gold = webapp._open_gold_spans(self.config)
        self.assertEqual(len(gold), 1)
        self.assertEqual(gold[0]["kind"], "negation")

    def test_duplicate_detection_is_exact(self):
        write_job(self.job, policy_words(REPEATED_LINE))
        transcript = json.loads(
            (self.job / "transcript.json").read_text(encoding="utf-8")
        )
        config = WebAppConfig(self.job)
        timeline = Timeline(transcript, webapp._effective_cuts(config))
        self.assertTrue(
            webapp._span_duplicates_kept(timeline, 40, 53)
        )
        # A near copy is not a duplicate.
        write_job(self.job, policy_words(UNIQUE_LINE))
        transcript = json.loads(
            (self.job / "transcript.json").read_text(encoding="utf-8")
        )
        timeline = Timeline(transcript, webapp._effective_cuts(config))
        self.assertFalse(
            webapp._span_duplicates_kept(timeline, 40, 53)
        )


# ---------------------------------------------------------------------------
# Gold from the retake pass
# ---------------------------------------------------------------------------

FILLER_A = "We are talking about the morning routine again."
FILLER_B = "It shapes the whole day more than people think."
NEGATION_LINE = "I will not give up on this family."


def sentence_words(sentences):
    words = []
    t = 0.0
    for sentence in sentences:
        for token in sentence.split():
            words.append(
                {"word": token, "start": round(t, 3),
                 "end": round(t + 0.35, 3)}
            )
            t += 0.45
    return words


class RetakeGoldTests(IsolatedHomeCase):
    def setUp(self) -> None:
        super().setUp()
        write_job(
            self.job,
            sentence_words(
                [FILLER_A, NEGATION_LINE, NEGATION_LINE, FILLER_B]
            ),
        )
        self.config = WebAppConfig(self.job)

    def test_flagged_pair_waits_in_gold(self):
        webapp._run_retake_pass(self.config, "mock")
        gold = webapp._open_gold_spans(self.config)
        self.assertEqual(len(gold), 1)
        self.assertEqual(gold[0]["kind"], "retake")
        self.assertEqual(gold[0]["text"], NEGATION_LINE)
        self.assertEqual(gold[0]["keep_text"], NEGATION_LINE)
        self.assertIn("not", gold[0]["reason"])

    def test_leave_verdict_settles_the_pair(self):
        webapp._run_retake_pass(self.config, "mock")
        gold = webapp._open_gold_spans(self.config)
        result = webapp._gold_verdict(
            self.config, gold[0]["gold_id"], "leave", ""
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual(webapp._open_gold_spans(self.config), [])
        record = webapp._load_retake_record(self.config)
        decisions = record["decisions"]
        self.assertEqual(len(decisions), 1)
        self.assertTrue(
            next(iter(decisions.values()))["keep_both"]
        )
        # The pair is never proposed again in this job.
        webapp._run_retake_pass(self.config, "mock")
        self.assertEqual(webapp._open_gold_spans(self.config), [])


# ---------------------------------------------------------------------------
# Gold verdicts and the learning feed
# ---------------------------------------------------------------------------


def gold_candidate(word_start, word_end, kind="negation", operation=None):
    if operation is None:
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


class GoldVerdictTests(IsolatedHomeCase):
    def setUp(self) -> None:
        super().setUp()
        tokens = [f"word{i}" for i in range(80)]
        tokens[3] = "friends."
        write_job(
            self.job,
            [
                {"word": token, "start": float(i), "end": float(i) + 0.9}
                for i, token in enumerate(tokens)
            ],
        )
        self.config = WebAppConfig(self.job)

    def seed(self, *candidates):
        webapp._record_gold_spans(self.config, list(candidates))

    def test_cut_verdict_applies_through_the_verifier(self):
        self.seed(gold_candidate(40, 45))
        result = webapp._gold_verdict(
            self.config, "gold-40-45-negation", "cut", "it repeats"
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        transcript = json.loads(
            (self.job / "transcript.json").read_text(encoding="utf-8")
        )
        timeline = Timeline(
            transcript, webapp._effective_cuts(self.config)
        )
        self.assertEqual(timeline.word_status(40), "cut")
        self.assertEqual(timeline.word_status(44), "cut")
        self.assertEqual(webapp._open_gold_spans(self.config), [])
        # The verdict is in the learning store, with its note.
        grades = webapp._load_grades(self.config)
        self.assertEqual(len(grades), 1)
        self.assertEqual(
            grades[0]["pattern_key"], "gold:negation:cut"
        )
        self.assertEqual(grades[0]["feedback_text"], "it repeats")
        # And the note sits on the edit the cut made.
        notes = webapp._load_edit_notes(self.config)
        record = notes["edits"]["edit-40-45"]
        self.assertEqual(record["notes"][0]["text"], "it repeats")

    def test_leave_verdict_settles_without_cutting(self):
        self.seed(gold_candidate(40, 45))
        result = webapp._gold_verdict(
            self.config, "gold-40-45-negation", "leave", "on purpose"
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        transcript = json.loads(
            (self.job / "transcript.json").read_text(encoding="utf-8")
        )
        timeline = Timeline(
            transcript, webapp._effective_cuts(self.config)
        )
        self.assertEqual(timeline.word_status(40), "kept")
        self.assertEqual(webapp._open_gold_spans(self.config), [])
        # A later pass spotting the same span never re-offers it.
        self.seed(gold_candidate(40, 45))
        self.assertEqual(webapp._open_gold_spans(self.config), [])
        grades = webapp._load_grades(self.config)
        self.assertEqual(
            grades[0]["pattern_key"], "gold:negation:leave"
        )

    def test_verdicts_surface_a_rule_with_the_note_as_context(self):
        spans = [(10, 14), (30, 34), (56, 60)]
        self.seed(*(gold_candidate(s, e) for s, e in spans))
        for s, e in spans:
            webapp._gold_verdict(
                self.config,
                f"gold-{s}-{e}-negation",
                "cut",
                "the second saying adds nothing",
            )
        suggestions = webapp._rule_suggestions(self.config)
        self.assertEqual(len(suggestions), 1)
        suggestion = suggestions[0]
        self.assertEqual(
            suggestion["pattern_key"], "gold:negation:cut"
        )
        self.assertEqual(suggestion["support_count"], 3)
        self.assertIn(
            "negative statement", suggestion["statement"]
        )
        self.assertIn(
            "the second saying adds nothing",
            suggestion["sample_feedback"],
        )

    def test_unknown_or_settled_span_is_refused_plainly(self):
        with self.assertRaises(ValueError):
            webapp._gold_verdict(
                self.config, "gold-1-2-negation", "cut", ""
            )
        self.seed(gold_candidate(40, 45))
        webapp._gold_verdict(
            self.config, "gold-40-45-negation", "leave", ""
        )
        with self.assertRaises(ValueError):
            webapp._gold_verdict(
                self.config, "gold-40-45-negation", "cut", ""
            )

    def test_a_hard_hold_still_refuses_a_cut_verdict(self):
        # The stored operation points at protected words: the
        # verifier's protection is not a soft hold, so the verdict
        # changes nothing and the span stays open.
        write_job(
            self.job,
            [
                {"word": f"w{i}", "start": float(i), "end": float(i) + 0.9}
                for i in range(80)
            ],
            profile={
                "protected_spans": [
                    {
                        "word_start": 40,
                        "word_end": 45,
                        "label": "protected moment",
                    }
                ]
            },
        )
        self.config = WebAppConfig(self.job)
        self.seed(gold_candidate(40, 45))
        result = webapp._gold_verdict(
            self.config, "gold-40-45-negation", "cut", ""
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual(len(webapp._open_gold_spans(self.config)), 1)


if __name__ == "__main__":
    unittest.main()
