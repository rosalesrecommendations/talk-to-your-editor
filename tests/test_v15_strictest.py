"""Tests for v0.15 Batch A (item 22): in the Cleanest cut mode, a
phrase the app itself has detected as a repeat is struck by
default; leaving it in takes an explicit ask.

Covers criteria A1: a retake gold candidate ends applied (and
restorable) in cleanest; a direction candidate whose narrowed
span duplicates words that stay ends applied in cleanest,
whatever its soft-hold reason; a negation candidate that does
not duplicate kept words follows the old policy in both modes;
a protected-overlapping candidate never settles.

v0.17 update (feedback items 7 and 8): Justin rejected the
tailor half of the original criteria ("Be proactive, cut the
retakes"), so detected repeats now settle in BOTH modes. The
two tests that asserted "waits in gold in tailor" were updated
in place to assert the new contract, and the differ-copies
exception (the one case that still asks) is covered in
tests/test_v17_retakes.py.

The harness mirrors tests/test_v071.py (temp home, minimal jobs).
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


def write_job(job: Path, words: list, profile: dict | None = None) -> None:
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
    if profile is not None:
        (job / "profile.json").write_text(
            json.dumps(profile), encoding="utf-8"
        )


class IsolatedHomeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v15-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def word_status(self, config, index):
        transcript = json.loads(
            (self.job / "transcript.json").read_text(encoding="utf-8")
        )
        timeline = Timeline(transcript, webapp._effective_cuts(config))
        return timeline.word_status(index)


# ---------------------------------------------------------------------------
# Retake candidates
# ---------------------------------------------------------------------------

FILLER_A = "We are talking about the morning routine again."
FILLER_B = "It shapes the whole day more than people think."
NEGATION_LINE = "I will not give up on this family."
# Word spans in the retake job: filler A 0..8, first saying 8..16,
# second saying 16..24, filler B 24..33.


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


class CleanestRetakeTests(IsolatedHomeCase):
    def setUp(self) -> None:
        super().setUp()
        write_job(
            self.job,
            sentence_words(
                [FILLER_A, NEGATION_LINE, NEGATION_LINE, FILLER_B]
            ),
        )
        self.config = WebAppConfig(self.job)

    def test_retake_candidate_is_struck_in_cleanest(self):
        summary = webapp._run_retake_pass(
            self.config, "mock", mode="cleanest"
        )
        # The earlier take is out; the keeper stays.
        for index in range(8, 16):
            self.assertEqual(self.word_status(self.config, index), "cut")
        for index in range(16, 24):
            self.assertEqual(self.word_status(self.config, index), "kept")
        # Nothing waits in gold, and the run record names the span.
        self.assertEqual(webapp._open_gold_spans(self.config), [])
        self.assertEqual(summary["auto_struck_count"], 1)
        struck = summary["auto_struck"][0]
        self.assertEqual(
            (struck["word_start"], struck["word_end"]), (8, 16)
        )
        self.assertEqual(struck["basis"], "retake_repeat")
        stored = webapp._auto_struck_records(self.config)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["status"], "auto_cut")
        self.assertEqual(stored[0]["basis"], "retake_repeat")
        # The recap names the count in plain words.
        lines = webapp._mode_and_gold_lines(
            self.config, {"mode": "cleanest"}
        )
        self.assertTrue(
            any(
                "1 detected repeat was struck automatically" in line
                for line in lines
            ),
            lines,
        )

    def test_struck_retake_is_restorable_by_bring_back(self):
        # A plain (non-negation) detected repeat, settled through
        # the same record point the retake pass uses, then brought
        # back through the existing pick-and-Bring-back path
        # (_apply_word_range restore), like any other cut.
        tokens = [f"word{i}" for i in range(80)]
        tokens[3] = "friends."
        plain_line = (
            "the morning light makes the cold water look calm "
            "and bright today now"
        ).split()
        tokens[10:10 + len(plain_line)] = plain_line
        tokens[40:40 + len(plain_line)] = plain_line
        job = self.tmp / "job-restore"
        write_job(
            job,
            [
                {"word": token, "start": float(i), "end": float(i) + 0.9}
                for i, token in enumerate(tokens)
            ],
        )
        config = WebAppConfig(job)
        candidate = bare_candidate(40, 53, "retakes", "retake")
        settled = webapp._record_gold_detected_repeats(
            config, [candidate], "cleanest"
        )
        self.assertEqual(len(settled), 1)

        def status(index):
            transcript = json.loads(
                (job / "transcript.json").read_text(encoding="utf-8")
            )
            timeline = Timeline(
                transcript, webapp._effective_cuts(config)
            )
            return timeline.word_status(index)

        for index in range(40, 53):
            self.assertEqual(status(index), "cut")
        result = webapp._apply_word_range(config, 40, 53, "restore")
        self.assertEqual(result.get("stayed"), [])
        self.assertTrue(result.get("changed"))
        for index in range(40, 53):
            self.assertEqual(status(index), "kept")

    def test_span_recorded_open_by_an_earlier_run_still_settles(self):
        # The span waits in gold from an earlier run (seeded
        # directly: under the v0.17 rule a fresh run would strike
        # it on the spot); a later run spotting it again must
        # strike it and stop offering it, not leave the old open
        # record standing.
        seeded = webapp._gold_candidates_from_retakes(
            [
                {
                    "pair_id": "retake-8-16-16-24",
                    "outcome": "flagged",
                    "band": "flag",
                    "reason": "said again right after",
                    "cut": {"word_start": 8, "word_end": 16},
                    "keep": {
                        "word_start": 16,
                        "word_end": 24,
                        "text": NEGATION_LINE,
                    },
                }
            ],
            sentence_words(
                [FILLER_A, NEGATION_LINE, NEGATION_LINE, FILLER_B]
            ),
        )
        webapp._record_gold_spans(self.config, seeded)
        self.assertEqual(len(webapp._open_gold_spans(self.config)), 1)
        candidates = webapp._gold_candidates_from_retakes(
            [
                {
                    "pair_id": "retake-8-16-16-24",
                    "outcome": "flagged",
                    "band": "flag",
                    "reason": "said again right after",
                    "cut": {"word_start": 8, "word_end": 16},
                    "keep": {
                        "word_start": 16,
                        "word_end": 24,
                        "text": NEGATION_LINE,
                    },
                }
            ],
            sentence_words(
                [FILLER_A, NEGATION_LINE, NEGATION_LINE, FILLER_B]
            ),
        )
        settled = webapp._record_gold_detected_repeats(
            self.config, candidates, "cleanest"
        )
        self.assertEqual(len(settled), 1)
        self.assertEqual(webapp._open_gold_spans(self.config), [])
        stored = webapp._auto_struck_records(self.config)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["gold_id"], "gold-8-16-retake")
        for index in range(8, 16):
            self.assertEqual(self.word_status(self.config, index), "cut")

    def test_same_retake_candidate_is_struck_in_tailor_too(self):
        # v0.17 (feedback items 7 and 8): the v0.15 contract had
        # this span wait in gold in tailor; Justin rejected that
        # outright. Detected retakes are cut in every mode now.
        summary = webapp._run_retake_pass(
            self.config, "mock", mode="tailor"
        )
        self.assertEqual(summary["auto_struck_count"], 1)
        for index in range(8, 16):
            self.assertEqual(self.word_status(self.config, index), "cut")
        for index in range(16, 24):
            self.assertEqual(self.word_status(self.config, index), "kept")
        self.assertEqual(webapp._open_gold_spans(self.config), [])
        lines = webapp._mode_and_gold_lines(
            self.config, {"mode": "tailor"}
        )
        self.assertTrue(
            any(
                "1 detected repeat was struck automatically" in line
                for line in lines
            ),
            lines,
        )


# ---------------------------------------------------------------------------
# Direction candidates
# ---------------------------------------------------------------------------

REPEATED_LINE = (
    "i am not giving up on this because the story is not over"
).split()
UNIQUE_LINE = (
    "i am not afraid of the winter because the cold is not forever"
).split()


def policy_words(second_line):
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


class CleanestDirectionTests(IsolatedHomeCase):
    def run_pass(self, second_line, operation, mode):
        write_job(self.job, policy_words(second_line))
        self.config = WebAppConfig(self.job)
        self.config.session_api_key = "test-key"
        settings = dict(webapp.FRONT_DOOR_DEFAULTS)
        settings["mode"] = mode
        stub = StubProvider([operation])
        with mock.patch.object(
            webapp, "_direction_provider", return_value=stub
        ):
            return webapp._run_direction_pass(
                self.config, "keep it tight", settings
            )

    def test_duplicating_direction_candidate_struck_in_cleanest(self):
        # Unreviewed: under the old behavior this went gold even in
        # cleanest (the negation policy only flips reviewed cuts).
        # Its span duplicates words that stay, so it now settles.
        summary = self.run_pass(
            REPEATED_LINE, negation_operation(40, 53, reviewed=False),
            "cleanest",
        )
        self.assertEqual(summary["auto_struck_count"], 1)
        struck = summary["auto_struck"][0]
        self.assertEqual(
            (struck["word_start"], struck["word_end"]), (40, 53)
        )
        self.assertEqual(struck["basis"], "duplicate")
        for index in range(40, 53):
            self.assertEqual(self.word_status(self.config, index), "cut")
        # The first saying stays, word for word.
        for index in range(10, 23):
            self.assertEqual(self.word_status(self.config, index), "kept")
        self.assertEqual(webapp._open_gold_spans(self.config), [])
        self.assertTrue(
            any(
                "1 detected repeat was struck automatically" in line
                for line in summary["lines"]
            ),
            summary["lines"],
        )

    def test_duplicating_direction_candidate_struck_in_tailor_too(self):
        # v0.17 (feedback items 7 and 8): a direction cut whose
        # span duplicates words that stay is a detected repeat,
        # and detected repeats settle in every mode now.
        summary = self.run_pass(
            REPEATED_LINE, negation_operation(40, 53, reviewed=False),
            "tailor",
        )
        self.assertEqual(summary["auto_struck_count"], 1)
        struck = summary["auto_struck"][0]
        self.assertEqual(
            (struck["word_start"], struck["word_end"]), (40, 53)
        )
        self.assertEqual(struck["basis"], "duplicate")
        for index in range(40, 53):
            self.assertEqual(self.word_status(self.config, index), "cut")
        self.assertEqual(webapp._open_gold_spans(self.config), [])

    def test_non_duplicating_negation_follows_old_policy_in_both_modes(self):
        for mode in ("cleanest", "tailor"):
            with self.subTest(mode=mode):
                summary = self.run_pass(
                    UNIQUE_LINE, negation_operation(40, 53), mode
                )
                self.assertEqual(summary["applied"], 0)
                self.assertEqual(summary["negation_applied"], 0)
                self.assertEqual(summary["auto_struck_count"], 0)
                for index in range(40, 53):
                    self.assertEqual(
                        self.word_status(self.config, index), "kept"
                    )
                gold = webapp._open_gold_spans(self.config)
                self.assertEqual(len(gold), 1)
                self.assertEqual(gold[0]["kind"], "negation")


# ---------------------------------------------------------------------------
# Protected material never settles
# ---------------------------------------------------------------------------


def bare_candidate(start, end, source, kind, operation=None):
    return {
        "gold_id": webapp._gold_id(start, end, kind),
        "word_start": start,
        "word_end": end,
        "kind": kind,
        "source": source,
        "reason": "Held for the creator.",
        "text": "some repeated words",
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


class ProtectedNeverSettlesTests(IsolatedHomeCase):
    def setUp(self) -> None:
        super().setUp()
        tokens = [f"word{i}" for i in range(80)]
        tokens[3] = "friends."
        tokens[10:23] = REPEATED_LINE
        tokens[40:53] = REPEATED_LINE
        write_job(
            self.job,
            [
                {"word": token, "start": float(i), "end": float(i) + 0.9}
                for i, token in enumerate(tokens)
            ],
            profile={
                "protected_spans": [
                    {
                        "word_start": 40,
                        "word_end": 53,
                        "label": "protected moment",
                    }
                ]
            },
        )
        self.config = WebAppConfig(self.job)

    def test_protected_retake_candidate_never_settles(self):
        candidate = bare_candidate(40, 53, "retakes", "retake")
        settled = webapp._record_gold_detected_repeats(
            self.config, [candidate], "cleanest",
            protected_ranges=[(40, 53)],
        )
        self.assertEqual(settled, [])
        for index in range(40, 53):
            self.assertEqual(self.word_status(self.config, index), "kept")
        gold = webapp._open_gold_spans(self.config)
        self.assertEqual(len(gold), 1)

    def test_protected_direction_candidate_never_settles(self):
        operation = negation_operation(40, 53, reviewed=False)
        candidate = bare_candidate(
            40, 53, "direction", "negation", operation=operation
        )
        settled = webapp._record_gold_detected_repeats(
            self.config, [candidate], "cleanest",
            protected_ranges=[(40, 53)],
        )
        self.assertEqual(settled, [])
        for index in range(40, 53):
            self.assertEqual(self.word_status(self.config, index), "kept")
        gold = webapp._open_gold_spans(self.config)
        self.assertEqual(len(gold), 1)


if __name__ == "__main__":
    unittest.main()
