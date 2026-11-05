"""Tests for v0.13 item 4: the app decides fragments and restart chains.

A pair is decided when the cut side is a fragment that stops
mid-word, or when two or more pairs share one complete keeper and
every cut is an earlier try at the same line with the same opening.
A decided pair is cut by the pass in strict and judged modes
alike, marked unsure so it can be brought back, and its cut is not
held for a second review. Pairs that are not decided keep the old
behavior exactly: a negation pair that is a genuine choice still
flags, and a chain whose keeper never finishes its line is not
decided. Offline only: the pass runs with the mock provider, which
judges nothing, so every cut below is local.
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

from talk_to_your_editor import retakes, webapp
from talk_to_your_editor.schema import SchemaError, validate_operations
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.verifier import verify_operations
from talk_to_your_editor.webapp import WebAppConfig

try:
    from test_retakes import make_cuts, make_transcript, timeline_for
except ImportError:  # discovery rooted at the repo instead of tests/
    from tests.test_retakes import make_cuts, make_transcript, timeline_for

NOTE_LAST = (
    "I was not sure about this one. I took it out so the last "
    "take stays. Double check here if you want it back."
)

FILLER_A = "We are talking about the morning routine again."
FILLER_B = "It shapes the whole day more than people think."

FRAGMENT = "But the f-"
FRAGMENT_KEEPER = "But one of the leaders of the study spoke up today."
FRAGMENT_SENTENCES = [FILLER_A, FRAGMENT, FRAGMENT_KEEPER, FILLER_B]
FRAGMENT_GAPS = {1: 2.0}
FRAGMENT_CUT_START = len(FILLER_A.split())                     # 8
FRAGMENT_CUT_END = FRAGMENT_CUT_START + len(FRAGMENT.split())  # 11

PRAYER_ATTEMPTS = [
    "God, shut the doors that",
    "God, shut the doors where you don't want us to be",
    "God, shut the doors that you don't want us to walk through, "
    "and help us",
]
PRAYER_KEEPER = (
    "God, shut the doors you don't want us to walk through, and "
    "redirect us to the path that you truly want us on."
)
def _extra_filler(sentences=9):
    """Unique-word filler sentences, so the job is long enough
    that cutting the three attempts stays well under the
    verifier's rule against removing a quarter of the edit at
    once (a protection decided cuts keep)."""
    out = []
    for s in range(sentences):
        tokens = [f"s{s}w{w}" for w in range(11)] + [f"s{s}end."]
        out.append(" ".join(tokens))
    return out


PRAYER_SENTENCES = (
    [FILLER_A] + PRAYER_ATTEMPTS + [PRAYER_KEEPER, FILLER_B] + _extra_filler()
)
PRAYER_GAPS = {1: 2.0, 2: 2.0, 3: 2.0}
PRAYER_FIRST = len(FILLER_A.split())
PRAYER_ATTEMPT_SPANS = []
_cursor = PRAYER_FIRST
for _attempt in PRAYER_ATTEMPTS:
    PRAYER_ATTEMPT_SPANS.append((_cursor, _cursor + len(_attempt.split())))
    _cursor += len(_attempt.split())
PRAYER_KEEPER_SPAN = (_cursor, _cursor + len(PRAYER_KEEPER.split()))


def run_pass(sentences, gap_after=None, strict=None):
    """Run the real retake pass on a fresh job, with no provider.

    Returns (summary, saved pair records, word statuses after).
    """
    tmp = Path(tempfile.mkdtemp(prefix="tye-v13-retakes-"))
    try:
        home = tmp / "home"
        home.mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(home)}, clear=False):
            job = tmp / "job"
            job.mkdir()
            transcript = make_transcript(sentences, gap_after=gap_after)
            (job / "transcript.json").write_text(
                json.dumps(transcript), encoding="utf-8"
            )
            (job / "cuts.json").write_text(
                json.dumps(make_cuts(transcript)), encoding="utf-8"
            )
            config = WebAppConfig(job)
            summary = webapp._run_retake_pass(config, "mock", strict=strict)
            record = json.loads(
                (job / "tye-session" / "retake_pass.json").read_text(
                    encoding="utf-8"
                )
            )
            revised_path = job / "tye-session" / "revised_cuts.json"
            cuts = (
                json.loads(revised_path.read_text(encoding="utf-8"))
                if revised_path.is_file()
                else make_cuts(transcript)
            )
            after = Timeline(transcript, cuts)
            statuses = [
                after.word_status(i) for i in range(len(transcript["words"]))
            ]
            return summary, record["pairs"], statuses
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class FragmentDecidedTests(unittest.TestCase):
    def test_hyphen_cut_fragment_pair_is_decided(self):
        tl = timeline_for(FRAGMENT_SENTENCES, gap_after=FRAGMENT_GAPS)
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        self.assertEqual(pair["band"], "b")
        self.assertEqual(pair["cut"]["text"], FRAGMENT)
        self.assertFalse(pair["cut_complete"])
        self.assertEqual(pair["decided"], "fragment")

    def test_fragment_cut_applies_end_to_end_with_no_provider(self):
        summary, pairs, statuses = run_pass(
            FRAGMENT_SENTENCES, gap_after=FRAGMENT_GAPS
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["decided"], "fragment")
        self.assertEqual(pairs[0]["outcome"], "applied")
        self.assertEqual(summary["applied"], 1)
        self.assertEqual(summary["flagged"], 0)
        # The cut operation itself carried the decision, and the
        # verifier let it apply: its confidence (0.67 from the
        # coverage) is under the second-review floor, so without
        # the decided marker this cut would have been held.
        ops = [
            op
            for op in summary["operations"]
            if op["comment_id"] == pairs[0]["pair_id"]
        ]
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["type"], "cut_words")
        self.assertEqual(ops[0]["status"], "applied")
        self.assertEqual(ops[0]["decided"], "fragment")
        self.assertLess(ops[0]["confidence"], 0.80)
        for i in range(FRAGMENT_CUT_START, FRAGMENT_CUT_END):
            self.assertEqual(statuses[i], "cut")
        for i in range(FRAGMENT_CUT_END, FRAGMENT_CUT_END + 11):
            self.assertEqual(statuses[i], "kept")

    def test_fragment_cut_also_applies_on_the_judged_path(self):
        # Loose mode (strict off) with a provider that judges
        # nothing: a decided borderline pair is still cut locally
        # instead of waiting for a judgment or becoming a flag.
        summary, pairs, statuses = run_pass(
            FRAGMENT_SENTENCES, gap_after=FRAGMENT_GAPS, strict=False
        )
        self.assertEqual(pairs[0]["outcome"], "applied")
        self.assertEqual(summary["applied"], 1)
        self.assertEqual(summary["flagged"], 0)
        for i in range(FRAGMENT_CUT_START, FRAGMENT_CUT_END):
            self.assertEqual(statuses[i], "cut")


class ChainDecidedTests(unittest.TestCase):
    def test_three_attempt_chain_sharing_one_complete_keeper(self):
        tl = timeline_for(PRAYER_SENTENCES, gap_after=PRAYER_GAPS)
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 3)
        keep_spans = {
            (p["keep"]["word_start"], p["keep"]["word_end"]) for p in pairs
        }
        self.assertEqual(keep_spans, {PRAYER_KEEPER_SPAN})
        for pair in pairs:
            # The line carries a negation, but no pair here is a
            # choice under the settled rule (0.27, fix 9): the
            # attempts that reach the "don't" share it with the
            # keeper, and the first attempt stops before it. The
            # chain decides all three as plain retakes.
            self.assertIn(pair["band"], ("a", "b"))
            self.assertFalse(pair["negation"])
            self.assertEqual(pair["decided"], "chain")
            self.assertEqual(pair["keep"]["text"], PRAYER_KEEPER)
        cut_texts = [p["cut"]["text"] for p in pairs]
        # Pairs sort by band, and the attempts no longer share
        # one band (0.27: the third attempt scores as a restart),
        # so the cut set is compared whole, not in order.
        self.assertEqual(sorted(cut_texts), sorted(PRAYER_ATTEMPTS))
        # A decided pair produces a cut, never a flag. (Before
        # 0.27 these pairs were also flag-BAND, held only by the
        # shared negation; under the settled asymmetry rule they
        # score as plain retakes, so no pair here is flag band.)
        self.assertEqual(retakes.flag_pairs(pairs), [])
        ops = retakes.decided_cut_operations(tl, None, pairs=pairs)
        self.assertEqual(len(ops), 3)
        for op in ops:
            self.assertEqual(op["type"], "cut_words")
            self.assertEqual(op["decided"], "chain")

    def test_chain_pass_excludes_every_attempt_and_keeps_the_keeper(self):
        summary, pairs, statuses = run_pass(
            PRAYER_SENTENCES, gap_after=PRAYER_GAPS
        )
        self.assertEqual(summary["applied"], 3)
        self.assertEqual(summary["flagged"], 0)
        for pair in pairs:
            self.assertEqual(pair["outcome"], "applied")
        for start, end in PRAYER_ATTEMPT_SPANS:
            for i in range(start, end):
                self.assertEqual(statuses[i], "cut")
        for i in range(*PRAYER_KEEPER_SPAN):
            self.assertEqual(statuses[i], "kept")

    def test_chain_with_an_incomplete_keeper_is_not_decided(self):
        unfinished = PRAYER_KEEPER[: -len(" that you truly want us on.")]
        sentences = [FILLER_A] + PRAYER_ATTEMPTS + [unfinished, FILLER_B]
        gaps = {1: 2.0, 2: 2.0, 3: 2.0, 4: 2.0}
        tl = timeline_for(sentences, gap_after=gaps)
        pairs = retakes.find_pairs(tl, None)
        self.assertTrue(pairs)
        for pair in pairs:
            self.assertFalse(pair["keep_complete"])
            self.assertIsNone(pair["decided"])


class NotDecidedTests(unittest.TestCase):
    def test_negation_genuine_choice_pair_still_flags(self):
        # A genuine choice: two complete copies, and exactly one
        # of them carries the "not" (0.27, fix 9). The pair
        # flags, is never decided, and the settlement does not
        # strike it either: the copies say different things, so
        # it waits in gold as the creator's call and every word
        # stays in the edit.
        negated = "I will not give up on this family."
        plain = "I will give up on this family."
        tl = timeline_for([FILLER_A, negated, plain, FILLER_B])
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        self.assertEqual(pair["band"], "flag")
        self.assertTrue(pair["negation"])
        self.assertIsNone(pair["decided"])
        self.assertEqual(retakes.decided_cut_operations(tl, None, pairs=pairs), [])
        summary, saved, statuses = run_pass(
            [FILLER_A, negated, plain, FILLER_B]
        )
        self.assertEqual(summary["applied"], 0)
        self.assertEqual(summary["flagged"], 1)
        self.assertEqual(saved[0]["outcome"], "flagged")
        self.assertEqual(summary["auto_struck_count"], 0)
        first = len(FILLER_A.split())
        width = len(negated.split()) + len(plain.split())
        for i in range(first, first + width):
            self.assertEqual(statuses[i], "kept")


class UnsureTreatmentTests(unittest.TestCase):
    def test_decided_pairs_and_their_cuts_carry_unsure(self):
        tl = timeline_for(PRAYER_SENTENCES, gap_after=PRAYER_GAPS)
        for pair in retakes.find_pairs(tl, None):
            self.assertTrue(pair["unsure"])
        tl = timeline_for(FRAGMENT_SENTENCES, gap_after=FRAGMENT_GAPS)
        for pair in retakes.find_pairs(tl, None):
            self.assertTrue(pair["unsure"])

    def test_decided_cuts_show_unsure_in_the_pass_output(self):
        summary, pairs, _statuses = run_pass(
            PRAYER_SENTENCES, gap_after=PRAYER_GAPS
        )
        self.assertEqual(summary["unsure"], 3)
        self.assertEqual(summary["unsure_note"], NOTE_LAST)
        for op in summary["operations"]:
            self.assertEqual(op["status"], "applied")
            self.assertTrue(op["retake"]["unsure"])
            self.assertEqual(op["retake"]["unsure_note"], NOTE_LAST)
            self.assertEqual(op["retake"]["decided"], "chain")
            self.assertEqual(op["reason"], NOTE_LAST)


class DecidedVerifierTests(unittest.TestCase):
    def _operation(self, start, end, decided="fragment"):
        return {
            "type": "cut_words",
            "comment_id": "retake-test",
            "reason": "A decided cut.",
            "confidence": 0.67,
            "model": "mock",
            "risk_tier": 1,
            "wonder_reviewed": False,
            "word_start": start,
            "word_end": end,
            "decided": decided,
        }

    def test_decided_marker_must_name_a_real_decision(self):
        tl = timeline_for([FILLER_A, FRAGMENT, FRAGMENT_KEEPER, FILLER_B],
                          gap_after=FRAGMENT_GAPS)
        ops = validate_operations(
            [self._operation(FRAGMENT_CUT_START, FRAGMENT_CUT_END)]
        )
        self.assertEqual(ops[0].decided, "fragment")
        with self.assertRaises(SchemaError):
            validate_operations(
                [self._operation(0, 2, decided="surely")]
            )

    def test_decided_cut_still_loses_to_a_protected_span(self):
        sentences = [FILLER_A, FRAGMENT, FRAGMENT_KEEPER, FILLER_B]
        transcript = make_transcript(sentences, gap_after=FRAGMENT_GAPS)
        tl = Timeline(transcript, make_cuts(transcript))
        profile = {
            "protected_spans": [
                {
                    "word_start": FRAGMENT_CUT_START,
                    "word_end": FRAGMENT_CUT_END,
                    "label": "the fragment is protected",
                }
            ]
        }
        ops = validate_operations(
            [self._operation(FRAGMENT_CUT_START, FRAGMENT_CUT_END)]
        )
        verdicts = verify_operations(ops, tl, profile)
        self.assertEqual(verdicts[0].status, "flag")
        self.assertTrue(
            any(r.startswith("protected_span:") for r in verdicts[0].reasons)
        )


if __name__ == "__main__":
    unittest.main()
