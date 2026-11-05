"""v0.17, feedback items 7 and 8: detected retakes are cut by
default in every way of working, and the one case that still
asks is a pair whose copies genuinely say different things.

Justin's words from the 0.15 review: "Be proactive, cut the
retakes, and let me know if I need to bring them back." And:
"when the editor doesn't know the difference between the two,
it asks me, but it says what the default is."

Covered here, at the record point the passes use:

- a differing-copies pair (one copy includes a "not") waits in
  gold in BOTH modes, and its card states the default (the
  copy that includes more stays);
- the same span without the differing flag settles in tailor
  (the v0.15 gate is gone);
- an identical-copies pair (the said-twice flag) settles too:
  its copies do not differ in content.
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
from talk_to_your_editor.webapp import WebAppConfig


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


# First saying 0..7, second saying 7..15: the second copy
# includes a "not" the first lacks. The filler sentences pad
# the timeline so the 7-word cut stays under the verifier's
# 25 percent rule, like a real video's proportions.
PLAIN = "I will give up on this family"
NEGATED = "I will not give up on this family"
FILLER = "The water was cold this morning."
FILLER_TWO = "We drove out before the sun came up."
FILLER_THREE = "The kids were still asleep in the back."


class DifferCopiesCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v17-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"
        self.words = sentence_words(
            [PLAIN, NEGATED, FILLER, FILLER_TWO, FILLER_THREE]
        )
        self.job.mkdir(parents=True)
        transcript = {
            "engine": "test",
            "duration": float(self.words[-1]["end"]),
            "words": self.words,
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

    def pair(self, flag_reason):
        return {
            "pair_id": "retake-0-7-7-15",
            "outcome": "flagged",
            "band": "flag",
            "flag_reason": flag_reason,
            "negation": flag_reason == retakes._FLAG_NEGATION_REASON,
            "cut": {"word_start": 0, "word_end": 7},
            "keep": {
                "word_start": 7,
                "word_end": 15,
                "text": NEGATED,
            },
        }

    def candidates(self, flag_reason):
        return webapp._gold_candidates_from_retakes(
            [self.pair(flag_reason)], self.words
        )


class DifferCopiesTests(DifferCopiesCase):
    def test_differing_copies_settle_cut_with_a_record(self):
        # Filming-day P0-4 (restrictive by default): a likely
        # retake is cut and kept as a bring-back record, never
        # left in for him to find, even when the copies differ
        # by a negation. The parallel line inside one sentence
        # is the one differing repeat that still waits.
        for mode in ("cleanest", "tailor"):
            with self.subTest(mode=mode):
                candidates = self.candidates(
                    retakes._FLAG_NEGATION_REASON
                )
                self.assertEqual(len(candidates), 1)
                self.assertTrue(candidates[0]["copies_differ"])
                settled = webapp._record_gold_detected_repeats(
                    self.config, candidates, mode
                )
                self.assertEqual(len(settled), 1)
                self.assertEqual(settled[0]["basis"], "retake_repeat")
                self.assertEqual(webapp._open_gold_spans(self.config), [])
                struck = webapp._auto_struck_records(self.config)
                self.assertEqual(len(struck), 1)
                # Reset for the other mode's pass: the gold
                # store and the plan the first pass cut into.
                (self.config.session_dir / "gold.json").unlink()
                revised = self.config.session_dir / "revised_cuts.json"
                if revised.is_file():
                    revised.unlink()

    def test_a_parallel_repeat_still_waits(self):
        candidates = self.candidates(retakes._FLAG_PARALLEL_REASON)
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["copies_differ"])
        settled = webapp._record_gold_detected_repeats(
            self.config, candidates, "cleanest"
        )
        self.assertEqual(settled, [])
        self.assertEqual(len(webapp._open_gold_spans(self.config)), 1)

    def test_same_span_without_the_differ_flag_settles_in_tailor(self):
        candidates = self.candidates(None)
        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0]["copies_differ"])
        settled = webapp._record_gold_detected_repeats(
            self.config, candidates, "tailor"
        )
        self.assertEqual(len(settled), 1)
        self.assertEqual(settled[0]["basis"], "retake_repeat")
        self.assertEqual(webapp._open_gold_spans(self.config), [])

    def test_real_record_shape_differ_pair_waits(self):
        # A pair as the retake pass actually saves it: the
        # negation flag text rides under "reason", there is no
        # "flag_reason" key, and both sides carry their text.
        pair = {
            "pair_id": "retake-0-7-7-15",
            "outcome": "flagged",
            "band": "flag",
            "negation": True,
            "reason": retakes._FLAG_NEGATION_REASON,
            "cut": {"word_start": 0, "word_end": 7, "text": PLAIN},
            "keep": {
                "word_start": 7,
                "word_end": 15,
                "text": NEGATED,
            },
        }
        candidates = webapp._gold_candidates_from_retakes(
            [pair], self.words
        )
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["copies_differ"])

    def test_real_record_shape_identical_negation_settles(self):
        # The same saved shape, but the line is said twice word
        # for word, 'not' and all: the flag rides along, yet
        # the copies do not differ, so nothing waits.
        pair = {
            "pair_id": "retake-7-15-7-15",
            "outcome": "flagged",
            "band": "flag",
            "negation": True,
            "reason": retakes._FLAG_NEGATION_REASON,
            "cut": {"word_start": 7, "word_end": 15, "text": NEGATED},
            "keep": {
                "word_start": 7,
                "word_end": 15,
                "text": NEGATED,
            },
        }
        candidates = webapp._gold_candidates_from_retakes(
            [pair], self.words
        )
        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0]["copies_differ"])

    def test_identical_copies_settle_too(self):
        candidates = self.candidates(retakes._FLAG_DOUBLE_REASON)
        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0]["copies_differ"])
        settled = webapp._record_gold_detected_repeats(
            self.config, candidates, "cleanest"
        )
        self.assertEqual(len(settled), 1)
        self.assertEqual(webapp._open_gold_spans(self.config), [])


if __name__ == "__main__":
    unittest.main()
