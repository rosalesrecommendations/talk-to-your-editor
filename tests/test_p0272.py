"""0.27.2 P0: the four fixes from Justin's 0.27.1 Mac run with
his key, where the keyed run's agreement measured 92.1 percent
against the plan-based 96.8.

1. A direction (model) proposal that would remove unique
   content, a whole sentence said nowhere else in the video, is
   held for review in the direction paths, never applied.
2. A direction proposal that touches a span already waiting
   open for the creator's call (the parallel wife/friends line)
   is held the same way: a your-call span is never cut without
   his yes on the spot itself.
3. A stopped take's own tail fragment, split off by the dead
   space after the stop ("My life." after "I would lo-"), goes
   with the take; and a review approval never refuses on the
   word-boundary guard: the cut widens to whole words and whole
   units at its edges and applies.
4. The same-point detector never records a spot whose later
   saying starts in the closing section, the final fifth of the
   edit: the closing prayer restating the premise is on purpose.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from talk_to_your_editor import retakes, webapp  # noqa: E402
from talk_to_your_editor.timeline import Timeline  # noqa: E402
from talk_to_your_editor.verifier import verify_operations  # noqa: E402
from talk_to_your_editor.webapp import WebAppConfig  # noqa: E402

try:
    from test_retakes import make_cuts, make_transcript, timeline_for
except ImportError:  # discovery rooted at the repo instead of tests/
    from tests.test_retakes import make_cuts, make_transcript, timeline_for


def word_index(sentences, sentence_i, word_i=0):
    return sum(len(s.split()) for s in sentences[:sentence_i]) + word_i


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
    (job / "transcript.json").write_text(json.dumps(transcript))
    (job / "cuts.json").write_text(json.dumps(cuts))


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


class IsolatedHomeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-p0272-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


def cut_op(start, end, comment_id="front-door-retakes"):
    return {
        "type": "cut_words",
        "comment_id": comment_id,
        "word_start": start,
        "word_end": end,
        "reason": "A model proposal.",
        "confidence": 1.0,
        "model": "muse-spark-1.3",
        "risk_tier": 1,
        "wonder_reviewed": False,
    }


# ---------------------------------------------------------------------------
# 3a. The stopped take's tail fragment goes with the take
# ---------------------------------------------------------------------------


class StoppedTakeTailTests(unittest.TestCase):
    SENTENCES = [
        "We finished the book years ago.",
        "Hey God if I ever get cancer",
        "I would lo-",
        "My life.",
        "That if God would ever want me to walk some",
        "That if God would ever want me to walk through something "
        "like cancer I would love to use the tools.",
        "The next line starts here now.",
    ]
    # Sentence 1 ends, a pause, the take stops mid-word, then the
    # dead space before its tail fragment; a pause also splits
    # the second stopped try from the complete saying.
    GAPS = {1: 2.4, 2: 5.4, 4: 2.0}

    def test_the_tail_fragment_is_inside_the_stopped_takes_cut(self):
        tl = timeline_for(self.SENTENCES, gap_after=self.GAPS)
        pairs = retakes.find_pairs(tl, None)
        frag_start = word_index(self.SENTENCES, 3)
        frag_end = word_index(self.SENTENCES, 4)
        covering = [
            p for p in pairs
            if p["cut"]["word_start"] <= frag_start
            and p["cut"]["word_end"] >= frag_end
        ]
        self.assertTrue(covering, pairs)
        pair = covering[0]
        self.assertEqual(pair["cut"].get("tail_from"), frag_start)
        # The fragment is never a keeper side.
        for p in pairs:
            self.assertFalse(
                p["keep"]["word_start"] <= frag_start
                and p["keep"]["word_end"] >= frag_end,
                p,
            )

    def test_no_dead_space_no_tail(self):
        # Without the dead space after the stop, a short finished
        # line after a stopped take is its own sentence and stays
        # out of the take's cut.
        gaps = {1: 2.4, 4: 2.0}
        tl = timeline_for(self.SENTENCES, gap_after=gaps)
        pairs = retakes.find_pairs(tl, None)
        frag_start = word_index(self.SENTENCES, 3)
        frag_end = word_index(self.SENTENCES, 4)
        for p in pairs:
            self.assertFalse(
                p["cut"]["word_start"] <= frag_start
                and p["cut"]["word_end"] >= frag_end
                and p["cut"].get("tail_from") == frag_start,
                p,
            )


# ---------------------------------------------------------------------------
# 1 + 2. The direction pass holds its own proposals
# ---------------------------------------------------------------------------

FILLER_A = "We are talking about the morning routine again."
REPEAT = "This part is hard to say out loud."
UNIQUE = (
    "The thyroid measured two point one eight centimeters in "
    "July and it is too large to surgically burn away."
)
FILLER_B = "It shapes the whole day more than people think."
GUARD_SENTENCES = [
    FILLER_A,
    "First we get up before the sun rises over the fields.",
    REPEAT,
    FILLER_B,
    UNIQUE,
    "We drove to the store on Tuesday morning for supplies.",
    REPEAT,
    "The dog slept under the porch all afternoon long today.",
    "She packed the cooler with ice and sandwiches for lunch.",
    "Then we sat on the steps and watched the evening come.",
]


class ProviderCutGuardTests(IsolatedHomeCase):
    def setUp(self) -> None:
        super().setUp()
        write_job(self.job, sentence_words(GUARD_SENTENCES))
        self.config = WebAppConfig(self.job)
        transcript = json.loads((self.job / "transcript.json").read_text())
        cuts = json.loads((self.job / "cuts.json").read_text())
        self.timeline = Timeline(transcript, cuts)

    def verdict_for(self, start, end, named_tokens=frozenset()):
        ops = webapp.validate_operations([cut_op(start, end)])
        verdicts = verify_operations(ops, self.timeline, None)
        guard = webapp._provider_cut_guard(
            self.config, named_tokens=named_tokens
        )
        return guard(verdicts, self.timeline, None)[0]

    def test_a_unique_sentence_cut_is_held(self):
        start = word_index(GUARD_SENTENCES, 4)
        end = word_index(GUARD_SENTENCES, 5)
        ops = webapp.validate_operations([cut_op(start, end)])
        raw = verify_operations(ops, self.timeline, None)[0]
        self.assertEqual(raw.status, "apply", raw.reasons)
        verdict = self.verdict_for(start, end)
        self.assertEqual(verdict.status, "flag")
        self.assertIn("unique_content", verdict.reasons)

    def test_a_repeated_sentence_cut_still_applies(self):
        start = word_index(GUARD_SENTENCES, 2)
        end = word_index(GUARD_SENTENCES, 3)
        verdict = self.verdict_for(start, end)
        self.assertEqual(verdict.status, "apply", verdict.reasons)

    def test_unique_cut_named_by_the_creator_is_still_held(self):
        # Filming-day ruling (0.27.3's keyed run cut the July
        # passage again under a typed direction): the stand-down
        # is over. A sentence with no retake partner is never
        # cut by a proposal, whatever the direction says; it
        # waits as a spot for his own tap.
        start = word_index(GUARD_SENTENCES, 4)
        end = word_index(GUARD_SENTENCES, 5)
        named = webapp._named_content_tokens(
            "cut the part about the thyroid centimeters in July"
        )
        verdict = self.verdict_for(start, end, named_tokens=named)
        self.assertEqual(verdict.status, "flag", verdict.reasons)
        self.assertIn("unique_content", verdict.reasons)

    def test_a_cut_touching_a_your_call_span_is_held(self):
        # The parallel pair waits open in gold over the second
        # copy's shared words; a proposal cutting that copy is
        # held, whatever it would remove.
        start = word_index(GUARD_SENTENCES, 6)
        end = word_index(GUARD_SENTENCES, 7)
        record = {
            "gold_id": webapp._gold_id(start + 1, end, "retake"),
            "kind": "retake",
            "source": "retakes",
            "word_start": start + 1,
            "word_end": end,
            "text": "hard to say out loud.",
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
        webapp._record_gold_spans(self.config, [record])
        self.assertEqual(len(webapp._open_gold_spans(self.config)), 1)
        verdict = self.verdict_for(start, end)
        self.assertEqual(verdict.status, "flag")
        self.assertIn("your_call_span", verdict.reasons)


# ---------------------------------------------------------------------------
# 3b. An approval never refuses on the word-boundary guard
# ---------------------------------------------------------------------------


class ApprovalExpansionTests(IsolatedHomeCase):
    def setUp(self) -> None:
        super().setUp()
        # Word 2 starts inside word 1's span, the Scribe shape:
        # a cut that starts at word 2 lands its edge inside
        # word 1 and the raw verdict refuses it.
        words = [
            {"word": "Start", "start": 0.0, "end": 0.40},
            {"word": "middle", "start": 0.45, "end": 0.85},
            {"word": "split", "start": 0.80, "end": 1.20},
            {"word": "end.", "start": 1.25, "end": 1.65},
            {"word": "Afterwards", "start": 1.70, "end": 2.10},
            {"word": "words", "start": 2.15, "end": 2.45},
            {"word": "continue", "start": 2.50, "end": 2.90},
            {"word": "on.", "start": 2.95, "end": 3.25},
            {"word": "Then", "start": 3.30, "end": 3.60},
            {"word": "the", "start": 3.65, "end": 3.80},
            {"word": "story", "start": 3.85, "end": 4.20},
            {"word": "moves", "start": 4.25, "end": 4.60},
            {"word": "forward", "start": 4.65, "end": 5.05},
            {"word": "with", "start": 5.10, "end": 5.30},
            {"word": "more", "start": 5.35, "end": 5.60},
            {"word": "talk", "start": 5.65, "end": 5.95},
            {"word": "about", "start": 6.00, "end": 6.30},
            {"word": "the", "start": 6.35, "end": 6.50},
            {"word": "morning", "start": 6.55, "end": 6.95},
            {"word": "routine.", "start": 7.00, "end": 7.45},
        ]
        write_job(self.job, words)
        self.config = WebAppConfig(self.job)

    def test_the_raw_cut_would_refuse_on_the_boundary_guard(self):
        transcript = json.loads((self.job / "transcript.json").read_text())
        timeline = Timeline(
            transcript, webapp._effective_cuts(self.config)
        )
        ops = webapp.validate_operations([cut_op(2, 3, "gold-verdict")])
        verdict = verify_operations(ops, timeline, None)[0]
        self.assertEqual(verdict.status, "reject")
        self.assertIn("word_boundary_split", verdict.reasons)

    def test_approval_expands_to_whole_words_and_applies(self):
        record = {
            "gold_id": webapp._gold_id(2, 3, "held"),
            "kind": "held",
            "source": "direction",
            "word_start": 2,
            "word_end": 3,
            "text": "split",
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
        webapp._record_gold_spans(self.config, [record])
        result = webapp._gold_verdict(
            self.config, record["gold_id"], "cut", ""
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"], result)
        transcript = json.loads((self.job / "transcript.json").read_text())
        timeline = Timeline(
            transcript, webapp._effective_cuts(self.config)
        )
        # The widened cut took the whole saying's head with it:
        # words 0, 1, and 2 are out; the finished sentence after
        # the spot is untouched.
        self.assertEqual(timeline.word_status(0), "cut")
        self.assertEqual(timeline.word_status(1), "cut")
        self.assertEqual(timeline.word_status(2), "cut")
        self.assertEqual(timeline.word_status(3), "kept")
        self.assertEqual(timeline.word_status(19), "kept")


# ---------------------------------------------------------------------------
# 4. The closing section never becomes a same-point spot
# ---------------------------------------------------------------------------

OPENING = "This is a test on my own body to see what the Lord will do."
TARGET_A = (
    "I am not saying this water will heal me of thyroid cancer. "
    "I am saying that the Lord can do whatever he wants. "
    "I am not saying everybody should get it."
)
TARGET_B = (
    "I am not saying that people who have thyroid cancer "
    "should get into this water to heal it."
)
PRAYER_A = (
    "I need to stop holding onto it. It is not mine. "
    "It is the Lord's to take away."
)
PRAYER_B = (
    "I need to stop holding onto it now. It is not mine "
    "anymore. It is the Lord's to take away."
)
SIGN_OFF = "God bless you friends and goodbye."
FILLERS = [
    "The recipe calls for two cups of flour and a pinch of salt.",
    "We drove to the store on Tuesday morning for supplies.",
    "The dog slept under the porch all afternoon long.",
    "She packed the cooler with ice and sandwiches for lunch.",
]


class SamePointClosingTests(unittest.TestCase):
    SENTENCES = [
        OPENING, FILLERS[0], TARGET_A, FILLERS[1], TARGET_B,
        PRAYER_A, FILLERS[2], FILLERS[3], PRAYER_B, SIGN_OFF,
    ]

    def test_the_closing_restatement_is_never_a_spot(self):
        tmp = Path(tempfile.mkdtemp(prefix="spot0272-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        transcript = make_transcript(self.SENTENCES)
        job = tmp / "job"
        job.mkdir(parents=True)
        (job / "transcript.json").write_text(json.dumps(transcript))
        (job / "cuts.json").write_text(
            json.dumps(make_cuts(transcript))
        )
        config = webapp.WebAppConfig(job, launch_home=tmp / "launch")
        records = webapp._record_same_point_spots(config)
        # The mid-video restatement still becomes a spot.
        target_start = word_index(self.SENTENCES, 2)
        self.assertTrue(
            any(r["word_start"] == target_start for r in records),
            records,
        )
        # The closing prayer's restatement does not: no spot's
        # later saying is the closing passage, and the early
        # declaration it restates is not offered either.
        prayer_b = word_index(self.SENTENCES, 8)
        prayer_a = word_index(self.SENTENCES, 5)
        for r in records:
            self.assertNotEqual(r["again_word_start"], prayer_b, r)
            self.assertNotEqual(r["word_start"], prayer_a, r)


if __name__ == "__main__":
    unittest.main()
