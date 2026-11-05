"""Chunk R: the final sweep's general guarantees.

Restart chains (a phrase or saying begun, stopped, and said
again, reduced to the saying that lands), the closing block
(the video ends where its sign-off and subscribe lines
complete; later speech is trailing), and the silence ceiling
(quiet inside a kept piece is trimmed even beside an
undecided your-call span's words, while the span's piece is
never dropped). All examples are synthetic; the rules are
shape rules, not fitted to any one video.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talk_to_your_editor import final_sweep, smooth  # noqa: E402


def make_words(sentences, gaps=None):
    """Words on a simple clock, in the sweep tests' style.
    ``gaps`` maps a word index to extra dead seconds inserted
    BEFORE that word."""
    gaps = gaps or {}
    words = []
    t = 0.0
    index = 0
    for sentence in sentences:
        for token in sentence.split():
            t += gaps.get(index, 0.0)
            words.append(
                {"word": token, "start": round(t, 3),
                 "end": round(t + 0.35, 3)}
            )
            t += 0.45
            index += 1
    return words


def scan_chains(words, **kwargs):
    statuses = ["kept"] * len(words)
    bounds = [(w["start"], w["end"]) for w in words]
    ends = final_sweep.punctuation_sentence_ends(words)
    return final_sweep.find_restart_chains(
        words, statuses, bounds, ends, **kwargs
    )


def cut_spans(records):
    return sorted(
        (r["word_start"], r["word_end"])
        for r in records
        if not r["blocked"]
    )


class RestartChainTests(unittest.TestCase):
    def test_three_tries_one_landing(self):
        words = make_words([
            "A short opening line lands here.",
            "The gate code is.",
            "The gate code is red.",
            "The gate code is red and it opens at six.",
            "Another line follows after that one.",
        ])
        records = scan_chains(words)
        # Words 6-9 ("The gate code is.") and 10-14 ("The gate
        # code is red.") are the tries; the landing saying
        # (15-23) stays.
        self.assertEqual(cut_spans(records), [(6, 10), (10, 15)])

    def test_a_single_fragment_before_a_fresh_start(self):
        words = make_words([
            "A short opening line lands here.",
            "When the rain",
            "When the rain comes we stay inside and rest.",
            "Another line follows after that one.",
        ])
        records = scan_chains(words)
        # The fragment is words 6-8; only it goes.
        self.assertEqual(cut_spans(records), [(6, 9)])

    def test_a_repeat_inside_one_saying(self):
        words = make_words([
            "A short opening line lands here.",
            "Now the pastor dug into this now the pastor dug "
            "into this and it was incredible.",
            "Another line follows after that one.",
        ])
        records = scan_chains(words)
        # The saying runs words 6-22; its first run (6-11) is
        # the try, the second run lands the sentence.
        self.assertEqual(cut_spans(records), [(6, 12)])

    def test_glued_tries_in_one_fragment_saying(self):
        # Two tries glued into one fragment saying (no
        # sentence break, stops mid-phrase), then the landing.
        words = make_words(
            [
                "Opening line one is here.",
                "The log it is called in the log it is marked in",
                "The log it is marked for the crew.",
                "One more line to close it.",
            ],
            gaps={17: 1.2},
        )
        records = scan_chains(words)
        # The fragment saying is words 5-16; both glued tries
        # go (the seam between the two pieces is the sweep's
        # choice; the union is the whole fragment).
        spans = cut_spans(records)
        self.assertTrue(spans)
        merged = (spans[0][0], spans[-1][1])
        self.assertEqual(merged, (5, 17))

    def test_a_negation_pair_is_never_a_chain(self):
        # The copies say different things (one carries the
        # "not"); that pair is the creator's call, never an
        # automatic cut, however close the wording runs.
        words = make_words([
            "Welcome back to the channel today friends.",
            "I will not give up on this plan.",
            "I will give up on this plan.",
            "That is everything for today folks.",
        ])
        self.assertEqual(scan_chains(words), [])

    def test_a_keeper_carrying_a_not_does_not_protect_a_stopped_try(self):
        # The try stopped before any negation was said; the
        # landing saying's own new ending carries the "not".
        # No competing meaning was ever asserted, so the try
        # is a plain restart and goes.
        words = make_words(
            [
                "A short opening line lands here.",
                "And then I found that when we give the whole thing",
                "And then I found that we do not need to carry "
                "it alone.",
                "Another line follows after that one.",
            ],
            gaps={16: 1.2},
        )
        records = scan_chains(words)
        self.assertEqual(cut_spans(records), [(6, 16)])

    def test_unrelated_neighbors_are_untouched(self):
        words = make_words([
            "The crew opens the gate at six.",
            "The crew closes the gate at nine.",
            "Dinner is at seven in the hall.",
        ])
        self.assertEqual(scan_chains(words), [])

    def test_a_refrain_in_the_closing_section_is_untouched(self):
        sentences = [
            "The harbor lights came on at dusk.",
            "A ferry crossed over to the island.",
            "Nobody hurried on the quiet dock.",
            "The crew coiled ropes and told stories.",
            "Supper was bread and soup and tea.",
            "Someone played a fiddle by the fire.",
            "The tide went out before the moon rose.",
            "We counted stars over the black water.",
            "Morning would bring the market boats.",
            "Everyone slept while the anchor held.",
            "We sing the same good song.",
            "We sing the same good song.",
        ]
        words = make_words(sentences)
        records = scan_chains(words)
        self.assertEqual(cut_spans(records), [])

    def test_a_try_over_protected_words_is_blocked(self):
        words = make_words([
            "A short opening line lands here.",
            "The gate code is.",
            "The gate code is red and it opens at six.",
        ])
        records = scan_chains(
            words, protected_words=set(range(6, 10))
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["blocked"], "protected")

    def test_a_try_touching_the_final_saying_is_blocked(self):
        words = make_words([
            "A short opening line lands here.",
            "The gate code is.",
            "The gate code is red and it opens at six.",
        ])
        records = scan_chains(words, final_span=(6, 10))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["blocked"], "final_saying")


class ClosingBlockTests(unittest.TestCase):
    def test_trailing_speech_after_the_closing_block(self):
        words = make_words([
            "A story line opens the talk.",
            "We learn something good today.",
            "God bless you friends and I'll see you tomorrow.",
            "Subscribe and join the journey with us.",
            "That take felt very cold.",
        ])
        statuses = ["kept"] * len(words)
        bounds = [(w["start"], w["end"]) for w in words]
        ends = final_sweep.punctuation_sentence_ends(words)
        # The block ends after the subscribe line (word 27);
        # the last saying is trailing speech.
        self.assertEqual(
            final_sweep.closing_block_end(words, statuses, bounds, ends),
            27,
        )

    def test_no_trailing_speech_no_cut(self):
        words = make_words([
            "A story line opens the talk.",
            "We learn something good today.",
            "God bless you friends and I'll see you tomorrow.",
            "Subscribe and join the journey with us.",
        ])
        statuses = ["kept"] * len(words)
        bounds = [(w["start"], w["end"]) for w in words]
        ends = final_sweep.punctuation_sentence_ends(words)
        self.assertEqual(
            final_sweep.closing_block_end(words, statuses, bounds, ends),
            len(words),
        )

    def test_a_prayer_petition_is_not_a_signoff(self):
        words = make_words([
            "God I pray that you bless my friends and give "
            "them strength for what they face today.",
            "It's in Jesus name I pray amen.",
            "God bless you friends and I'll see you tomorrow.",
        ])
        statuses = ["kept"] * len(words)
        bounds = [(w["start"], w["end"]) for w in words]
        ends = final_sweep.punctuation_sentence_ends(words)
        # The block is the real sign-off at the very end:
        # nothing trails it, and the petition is untouched.
        self.assertEqual(
            final_sweep.closing_block_end(words, statuses, bounds, ends),
            len(words),
        )

    def test_a_signoff_in_the_middle_settles_nothing(self):
        words = make_words([
            "God bless you friends and I'll see you tomorrow.",
            "But first a long story about the harbor lights.",
            "The boats came in one by one at dusk.",
            "And the crew told tales until midnight came.",
            "Then everyone walked home under the stars.",
        ])
        statuses = ["kept"] * len(words)
        bounds = [(w["start"], w["end"]) for w in words]
        ends = final_sweep.punctuation_sentence_ends(words)
        self.assertIsNone(
            final_sweep.closing_block_end(words, statuses, bounds, ends)
        )


class SilenceCeilingTests(unittest.TestCase):
    """The trim itself lives in smooth.trim_internal_silences;
    the ceiling is the split of protections it now accepts:
    gold (undecided) words no longer shield the quiet beside
    them, but a piece holding gold words is never dropped."""

    def _fixture(self):
        words = [
            {"word": "one", "start": 0.0, "end": 0.35},
            {"word": "two", "start": 0.45, "end": 0.8},
            {"word": "three", "start": 0.9, "end": 1.25},
            {"word": "four", "start": 2.45, "end": 2.8},
            {"word": "five", "start": 2.9, "end": 3.25},
            {"word": "six.", "start": 3.35, "end": 3.7},
            {"word": "ghost", "start": 5.0, "end": 5.4},
        ]
        spans = [
            {"start": 0.0, "end": 1.28},
            {"start": 2.42, "end": 3.73},
        ]
        env = []
        for i in range(600):
            t = i * smooth.FRAME_S
            loud = any(s["start"] <= t < s["end"] for s in spans)
            env.append(-20.0 if loud else -70.0)
        ranges = [
            {"start": 0.0, "end": 3.7},
            {"start": 5.0, "end": 5.4},
        ]
        return words, ranges, env, spans

    def test_quiet_beside_gold_words_is_trimmed(self):
        words, ranges, env, spans = self._fixture()
        # The old call shape: gold folded into protected. The
        # piece holding words 0-5 stays in one piece: the
        # 1.2 s gap between words 2 and 3 survives.
        old_ranges, _old_report = smooth.trim_internal_silences(
            words, ranges, env,
            protected_words={2, 3}, spans=spans,
        )
        self.assertTrue(
            any(
                r["start"] == 0.0 and r["end"] == 3.7
                for r in old_ranges
            ),
            old_ranges,
        )
        # The ceiling's call shape: gold apart. The gap is
        # trimmed to the standard pause.
        new_ranges, report = smooth.trim_internal_silences(
            words, ranges, env,
            protected_words=set(), gold_words={2, 3}, spans=spans,
        )
        self.assertGreaterEqual(report["trimmed"], 1)
        # The piece is split at the gap, and what remains of
        # the 1.2 s of quiet is the standard pause (a 0.13 s
        # tail plus a 0.04 s head), well under the ceiling.
        pieces = [
            r for r in new_ranges if r["start"] < 3.7 and r["end"] <= 3.7
        ]
        self.assertGreaterEqual(len(pieces), 2)
        self.assertGreaterEqual(report["seconds_removed"], 1.0)

    def test_a_piece_holding_gold_words_is_never_dropped(self):
        words, ranges, env, spans = self._fixture()
        # Word 6 ("ghost") is stamped over pure quiet: its
        # piece would be dropped as soundless, unless the
        # word is gold.
        new_ranges, _report = smooth.trim_internal_silences(
            words, ranges, env,
            protected_words=set(), gold_words={6}, spans=spans,
        )
        self.assertTrue(
            any(r["start"] == 5.0 for r in new_ranges),
            new_ranges,
        )
        dropped_ranges, _report = smooth.trim_internal_silences(
            words, ranges, env,
            protected_words=set(), spans=spans,
        )
        self.assertFalse(
            any(r["start"] == 5.0 for r in dropped_ranges),
            dropped_ranges,
        )


if __name__ == "__main__":
    unittest.main()
