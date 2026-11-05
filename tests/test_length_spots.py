"""Length round (0.27): whole first sayings, and the same-point
review spot.

Whole sayings: when a retake pair cuts an earlier take, the cut
covers the whole first saying, so the head of a cut take never
plays. The cut extends back over the unbroken chain of
unfinished units in front of the try, as far as the chain echoes
the keeper's saying; a finished line standing before the try
does not echo it and stays. Inside the cut's own unit the cut
extends back to its sentence start. A pair that only flags
keeps its measured span.

The same-point spot: a kept passage whose point is made again
later becomes a spot in the review walk, never an auto-cut. The
detector is tight: passage-level containment, most clauses
carried over, at least four content words said again, the two
passages apart, and guards for protected spans, open your-call
spans, the opening, and recorded retake pairs.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from talk_to_your_editor import retakes, webapp  # noqa: E402
from talk_to_your_editor.timeline import Timeline  # noqa: E402

try:
    from test_retakes import make_cuts, make_transcript, timeline_for
except ImportError:  # discovery rooted at the repo instead of tests/
    from tests.test_retakes import make_cuts, make_transcript, timeline_for


def word_index(sentences, sentence_i, word_i=0):
    return sum(len(s.split()) for s in sentences[:sentence_i]) + word_i


# ---------------------------------------------------------------------------
# Whole sayings
# ---------------------------------------------------------------------------


class WholeSayingTests(unittest.TestCase):
    SENTENCES = [
        "We finished the book years ago.",
        "Hey God if I ever get cancer",
        "I would love",
        "my life",
        "That if God would ever want me to walk some",
        "That if God would ever want me to walk through something "
        "like cancer I would love to use the tools.",
        "The next line starts here now.",
    ]
    GAPS = {1: 2.0, 2: 2.0, 3: 2.0}

    def pair_for_keeper(self, pairs, sentences):
        start = word_index(sentences, 5)
        for pair in pairs:
            if pair["keep"]["word_start"] == start:
                return pair
        return None

    def test_cut_extends_over_the_echoing_chain(self):
        tl = timeline_for(self.SENTENCES, gap_after=self.GAPS)
        pairs = retakes.find_pairs(tl, None)
        pair = self.pair_for_keeper(pairs, self.SENTENCES)
        self.assertIsNotNone(pair, pairs)
        self.assertEqual(
            pair["cut"]["word_start"], word_index(self.SENTENCES, 1)
        )
        self.assertEqual(
            pair["cut"]["whole_from"], word_index(self.SENTENCES, 4)
        )
        self.assertEqual(
            pair["cut"]["word_end"], word_index(self.SENTENCES, 5)
        )

    def test_a_finished_line_before_the_try_stays(self):
        sentences = [
            "And I already shared even if it was just for that.",
            "This was worth it",
            "But when I went back to my thyroid cancer",
            "But when I went back to the thyroid cancer "
            "appointment today.",
            "Closing words go here now.",
        ]
        tl = timeline_for(sentences, gap_after={1: 2.0, 2: 2.0})
        pairs = retakes.find_pairs(tl, None)
        start = word_index(sentences, 3)
        pair = next(
            (p for p in pairs if p["keep"]["word_start"] == start), None
        )
        self.assertIsNotNone(pair, pairs)
        self.assertEqual(
            pair["cut"]["word_start"], word_index(sentences, 2)
        )
        self.assertNotIn("whole_from", pair["cut"])

    def test_a_cut_word_in_the_chain_stops_the_walk(self):
        cut_word = word_index(self.SENTENCES, 2, 1)
        tl = timeline_for(
            self.SENTENCES,
            cut_spans=[(cut_word, cut_word + 1)],
            gap_after=self.GAPS,
        )
        pairs = retakes.find_pairs(tl, None)
        pair = self.pair_for_keeper(pairs, self.SENTENCES)
        self.assertIsNotNone(pair, pairs)
        self.assertEqual(
            pair["cut"]["word_start"], word_index(self.SENTENCES, 4)
        )
        self.assertNotIn("whole_from", pair["cut"])
        self.assertEqual(pair["cut"]["extension_stopped"], "not_kept")

    def test_a_flagged_pair_keeps_its_measured_span(self):
        sentences = [
            "We finished the book years ago.",
            "Hey God if I ever get cancer",
            "I would love",
            "my life",
            "That if God would not ever want me to walk some",
            "That if God would ever want me to walk through "
            "something like cancer I would love to use the tools.",
            "The next line starts here now.",
        ]
        tl = timeline_for(sentences, gap_after=self.GAPS)
        pairs = retakes.find_pairs(tl, None)
        pair = self.pair_for_keeper(pairs, sentences)
        self.assertIsNotNone(pair, pairs)
        self.assertEqual(pair["band"], "flag")
        self.assertEqual(
            pair["cut"]["word_start"], word_index(sentences, 4)
        )
        self.assertNotIn("whole_from", pair["cut"])

    def test_inside_a_unit_the_cut_reaches_its_sentence_start(self):
        transcript = make_transcript(
            ["If the Lord will heal it through the through the water"]
        )
        words = transcript["words"]
        transcript["segments"] = [
            {"start": 0.0, "end": words[5]["end"], "text": "a"},
            {"start": words[5]["end"], "end": words[-1]["end"],
             "text": "b"},
        ]
        tl = Timeline(transcript, make_cuts(transcript))
        settings = retakes.settings_from_profile(None)
        units = retakes.segment_units(tl, None, settings)
        record = {
            "band": "b",
            "decided": None,
            "cut": {
                "word_start": 7,
                "word_end": 9,
                "text": "through the",
            },
            "keep": {
                "word_start": 9,
                "word_end": 11,
                "text": "the water",
            },
        }
        retakes._extend_whole_sayings(tl, [record], units, None)
        self.assertEqual(record["cut"]["word_start"], 6)
        self.assertEqual(record["cut"]["whole_from"], 7)


# ---------------------------------------------------------------------------
# The same-point review spot
# ---------------------------------------------------------------------------

OPENING = (
    "This is a test on my own body to see what the Lord "
    "will do with it."
)
PASSAGE_A = (
    "I am not saying this water will heal me of thyroid cancer. "
    "I am saying that the Lord can do whatever he wants. "
    "I am not saying everybody should get it."
)
PASSAGE_B = (
    "I am not saying that people who have thyroid cancer "
    "should get into this water to heal it."
)
PRAYER_A = "I trust the Lord with my whole heart and my future."
PRAYER_B = (
    "Father I give you everything I am and all that lies "
    "ahead of me now."
)
FILLER_1 = "The recipe calls for two cups of flour and a pinch of salt."
FILLER_2 = "We drove to the store on Tuesday morning for supplies."


def make_job(tmp: Path, sentences) -> webapp.WebAppConfig:
    transcript = make_transcript(sentences)
    job = tmp / "job"
    job.mkdir(parents=True)
    (job / "transcript.json").write_text(json.dumps(transcript))
    (job / "cuts.json").write_text(json.dumps(make_cuts(transcript)))
    return webapp.WebAppConfig(job, launch_home=tmp / "launch")


class SamePointSpotTests(unittest.TestCase):
    SENTENCES = [
        OPENING, FILLER_1, PASSAGE_A, FILLER_2, PASSAGE_B,
        PRAYER_A, FILLER_1.replace("recipe", "shopping"), PRAYER_B,
        OPENING,
    ]

    def test_the_repeated_point_becomes_a_spot(self):
        import shutil
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="spot-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        config = make_job(tmp, self.SENTENCES)
        records = webapp._record_same_point_spots(config)
        a_start = word_index(self.SENTENCES, 2)
        a_end = word_index(self.SENTENCES, 3)
        target = [
            r for r in records
            if r["word_start"] == a_start and r["word_end"] == a_end
        ]
        self.assertEqual(len(target), 1, records)
        record = target[0]
        self.assertEqual(record["kind"], "same_point")
        self.assertEqual(record["status"], "open")
        self.assertEqual(
            record["again_word_start"], word_index(self.SENTENCES, 4)
        )
        self.assertTrue(
            record["reason"].startswith("This makes the same point as "),
            record["reason"],
        )
        self.assertTrue(record["reason"].endswith(". Cut it?"))
        # The prayer restatement (different words) is not a spot,
        # and neither is the opening, which repeats word for word
        # at the end: the opening is never a spot's passage.
        prayer_a = word_index(self.SENTENCES, 5)
        for r in records:
            self.assertNotEqual(r["word_start"], 0)
            self.assertFalse(
                r["word_start"] <= prayer_a < r["word_end"], r
            )
        # Recorded once: a second read finds nothing new.
        self.assertEqual(webapp._record_same_point_spots(config), [])
        views = [
            v for v in webapp._open_gold_spans(config)
            if v.get("kind") == "same_point"
        ]
        self.assertEqual(len(views), 1)
        self.assertIsInstance(views[0].get("again_output_s"), float)


if __name__ == "__main__":
    unittest.main()
