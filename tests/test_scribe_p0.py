"""0.27.1 P0, the Scribe path: two retake shapes Scribe's
spelling and timing exposed on Justin's Mac run.

The book sentence: Scribe pauses 2.3 s before the last word of
the real take, so the pause split left "safely." as its own
one-word unit and the take it completes read as a fragment that
stops early; the pass then cut the complete take and kept the
earlier one, and the output spliced "They've done some
research" straight into "safely.". A lone sentence-ending word
after an unfinished unit is that unit's tail: segment_units
merges it back, the later take reads complete, and the last
complete take wins.

The stopped prayer: Scribe writes the sentence that introduces
a quote with a comma, so the quoted sentence the speaker stops
inside ("Hey, God, if I ever get cancer, I would lo-") glued
onto the sentence before it. A word carrying an opening quote
starts its own unit, and a decided fragment's whole-saying walk
takes that head unit with it, so the whole stopped take goes
and the sentence before it ("...to offer up to God,") stays.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from talk_to_your_editor import retakes  # noqa: E402

try:
    from test_retakes import timeline_for
except ImportError:  # discovery rooted at the repo instead of tests/
    from tests.test_retakes import timeline_for


BOOK = [
    "They've done some research.",
    "Him and I published a book about how anybody can cold train.",
    "Him and I published a book about ways that people could cold train",
    "safely.",
    "And when we finished that book it mattered.",
]

PRAYER = [
    "And when we finished that book fifteen years ago I felt a nudge",
    'to offer up to God, "Hey, God, if I ever get cancer,',
    '"I would lo-"',
    "My life.",
    "That if God would ever want me to walk through something like cancer,",
    "I would love to be able to use the tools and resources he has given me",
    "to see if it could help other people.",
]


def _settings():
    return retakes.settings_from_profile(None)


class LoneTailMergeTests(unittest.TestCase):
    def test_the_lone_last_word_merges_into_its_sentence(self):
        tl = timeline_for(BOOK, gap_after={2: 2.3})
        units = retakes.segment_units(tl, None, _settings())
        book = [u for u in units if u["word_start"] == 16]
        self.assertEqual(len(book), 1)
        self.assertEqual(book[0]["word_end"], 30)
        self.assertTrue(book[0]["terminal"])

    def test_the_complete_later_take_wins(self):
        tl = timeline_for(BOOK, gap_after={2: 2.3})
        records = retakes.find_pairs(tl, None, _settings())
        cuts = {
            (r["cut"]["word_start"], r["cut"]["word_end"])
            for r in records
        }
        # The earlier take (words 4 to 16) is the one proposed
        # for cutting; the later take, completed by "safely.",
        # is never the cut side of any record.
        self.assertIn((4, 16), cuts)
        self.assertNotIn((16, 30), cuts)


class StoppedQuoteTests(unittest.TestCase):
    def test_the_quote_opens_its_own_unit(self):
        tl = timeline_for(PRAYER, gap_after={0: 3.0, 1: 2.4, 2: 5.4, 4: 2.0})
        units = retakes.segment_units(tl, None, _settings())
        starts = {u["word_start"] for u in units}
        self.assertIn(18, starts)

    def test_the_whole_stopped_take_is_the_cut(self):
        tl = timeline_for(PRAYER, gap_after={0: 3.0, 1: 2.4, 2: 5.4, 4: 2.0})
        records = retakes.find_pairs(tl, None, _settings())
        cuts = {
            (r["cut"]["word_start"], r["cut"]["word_end"])
            for r in records
        }
        # From the quoted sentence's first word (18) through the
        # mid-word stop (28): the whole stopped take, one cut.
        self.assertIn((18, 28), cuts)
        # The sentence that introduced it (13 to 18) is never cut.
        self.assertNotIn((13, 18), cuts)
        for start, end in cuts:
            self.assertFalse(start <= 17 < end and start < 18)


if __name__ == "__main__":
    unittest.main()
