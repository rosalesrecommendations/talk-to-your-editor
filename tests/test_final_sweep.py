"""0.27.3 chunk A: the final sweep's shape rules.

The sweep reads a settled plan on sound and sentence shape:
a kept span that trails off mid-sentence into a second or
more of word-free dead space, whose thought is said again
complete later, is a stopped try. Complete sayings are never
suspects, dead space across an edit seam is an edit (not
silence), the sentence tail before a try is not part of the
try, and the video's final saying is never cut: a span that
would touch it is reported blocked instead.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talk_to_your_editor import final_sweep  # noqa: E402


def make_words(sentences, gaps=None, durations=None):
    """Words on a simple clock. ``gaps`` maps a word index to
    the extra dead seconds inserted BEFORE that word;
    ``durations`` maps a word index to its stamp length (for
    the dead-air token shape)."""
    gaps = gaps or {}
    durations = durations or {}
    words = []
    t = 0.0
    index = 0
    for sentence in sentences:
        for token in sentence.split():
            t += gaps.get(index, 0.0)
            dur = durations.get(index, 0.35)
            words.append(
                {"word": token, "start": round(t, 3),
                 "end": round(t + dur, 3)}
            )
            t += dur + 0.1
            index += 1
    return words


def scan(words, cut=(), **kwargs):
    statuses = [
        "cut" if i in cut else "kept" for i in range(len(words))
    ]
    # Sound bounds as the plan carries them: a word whose stamp
    # is over-long (it carries dead air) measures as a short
    # sound at its stamp's start; every other word sounds for
    # its whole stamp.
    bounds = [
        (
            w["start"],
            w["start"] + 0.37
            if w["end"] - w["start"] > 1.5
            else w["end"],
        )
        for w in words
    ]
    ends = final_sweep.punctuation_sentence_ends(words)
    units = final_sweep.kept_units(words, statuses, bounds, ends)
    fin = final_sweep.final_saying_span(units)
    spans = final_sweep.find_stopped_spans(
        words, statuses, bounds, ends,
        final_span=kwargs.get("final_span", fin),
        protected_words=kwargs.get("protected_words"),
    )
    return spans, fin


PRAYER_LEAD = "I felt a nudge to offer up to God,"
TAKE = '"Hey, God, if I ever get cancer, I would lo-"'
FRAGMENT = "My life."
COMPLETER = (
    "That if God would ever want me to walk through something "
    "like cancer, I would love to use the tools."
)
SIGN_OFF = "God bless you, friends, and I'll see you tomorrow."


class StoppedShapeTests(unittest.TestCase):
    def test_the_stopped_take_and_its_tail_are_found(self):
        sentences = [
            "We finished the book years ago.",
            PRAYER_LEAD + " " + TAKE,
            FRAGMENT,
            COMPLETER,
            "The next line starts here now.",
            SIGN_OFF,
        ]
        # The take stops: 2.4 s before "I would" (word 22),
        # 5.4 s before the fragment (word 25); the word "Hey,"
        # (word 15) carries dead air in its own stamp, as
        # Scribe wrote it on the real transcript.
        words = make_words(sentences, gaps={22: 2.4, 25: 5.4},
                           durations={15: 2.34})
        spans, fin = scan(words)
        self.assertEqual(len(spans), 1, spans)
        span = spans[0]
        self.assertEqual(span["word_start"], 15, spans)  # "Hey,"
        self.assertEqual(span["word_end"], 27, spans)  # past "life."
        self.assertIsNone(span["blocked"])
        self.assertEqual(fin[0], len(words) - 9)

    def test_no_dead_space_no_span(self):
        sentences = [
            "We finished the book years ago.",
            PRAYER_LEAD + " " + TAKE,
            FRAGMENT,
            COMPLETER,
            SIGN_OFF,
        ]
        words = make_words(sentences, durations={15: 2.34})
        spans, _fin = scan(words)
        self.assertEqual(spans, [])

    def test_a_continuation_after_a_thinking_pause_is_not_a_span(self):
        # The gate transcript's shape: he pauses mid-sentence
        # for two seconds and then finishes the sentence, and
        # the continuation echoes the span's words ("I'm not
        # saying..."). It is one sentence flowing on, not a
        # stopped try: the completing saying never restarts
        # the thought from its beginning.
        sentences = [
            "I'm saying that the Lord can",
            "do whatever he wants I'm not saying everybody "
            "should get it.",
            "A plain filler sentence follows here.",
            SIGN_OFF,
        ]
        words = make_words(sentences, gaps={6: 2.2})
        spans, _fin = scan(words)
        self.assertEqual(spans, [])

    def test_a_complete_restatement_is_never_a_span(self):
        # The closing prayer shape: the same thought said again
        # in complete sentences, with a long pause before it.
        sentences = [
            "This water will not heal me by itself.",
            "I need to stop holding onto it. It is not mine.",
            "So here we are at the end of the day.",
            "I need to stop holding onto it now. "
            "It is not mine anymore.",
            SIGN_OFF,
        ]
        words = make_words(sentences, gaps={11: 2.5})
        spans, _fin = scan(words)
        self.assertEqual(spans, [])

    def test_dead_space_across_an_edit_seam_is_not_a_span(self):
        # The take was already cut by another pass: kept words
        # sit far apart in the source, but cut words lie
        # between them, so there is no word-free dead space.
        sentences = [
            PRAYER_LEAD + " " + TAKE,
            FRAGMENT,
            COMPLETER,
            SIGN_OFF,
        ]
        words = make_words(sentences, gaps={16: 2.4, 19: 5.4})
        cut = set(range(9, 21))  # the take and fragment are out
        spans, _fin = scan(words, cut=cut)
        self.assertEqual(spans, [])

    def test_a_completer_too_far_away_is_not_a_span(self):
        sentences = [
            PRAYER_LEAD + " " + TAKE,
            FRAGMENT,
            "A long unrelated story fills the next minute here.",
            COMPLETER,
            SIGN_OFF,
        ]
        words = make_words(
            sentences,
            gaps={16: 2.4, 19: 5.4, 30: 75.0},
            durations={9: 2.34},
        )
        spans, _fin = scan(words)
        self.assertEqual(spans, [])

    def test_a_span_over_protected_words_is_blocked(self):
        sentences = [
            "We finished the book years ago.",
            PRAYER_LEAD + " " + TAKE,
            FRAGMENT,
            COMPLETER,
            SIGN_OFF,
        ]
        words = make_words(sentences, gaps={22: 2.4, 25: 5.4},
                           durations={15: 2.34})
        spans, _fin = scan(words, protected_words=set(range(15, 27)))
        self.assertEqual(len(spans), 1, spans)
        self.assertEqual(spans[0]["blocked"], "protected")

    def test_a_span_touching_the_final_saying_is_blocked(self):
        sentences = [
            "We finished the book years ago.",
            PRAYER_LEAD + " " + TAKE,
            FRAGMENT,
            COMPLETER,
            SIGN_OFF,
        ]
        words = make_words(sentences, gaps={22: 2.4, 25: 5.4},
                           durations={15: 2.34})
        # The same found span, with the final saying declared
        # over it: reported blocked, never cut.
        spans, _fin = scan(words, final_span=(20, 27))
        self.assertEqual(len(spans), 1, spans)
        self.assertEqual(spans[0]["blocked"], "final_saying")


class FinalSayingTests(unittest.TestCase):
    def test_the_final_saying_is_the_last_sentence(self):
        words = make_words([
            "One sentence here.",
            "Another one follows it.",
            SIGN_OFF,
        ])
        ends = final_sweep.punctuation_sentence_ends(words)
        statuses = ["kept"] * len(words)
        bounds = [(w["start"], w["end"]) for w in words]
        units = final_sweep.kept_units(words, statuses, bounds, ends)
        fin = final_sweep.final_saying_span(units)
        self.assertEqual(fin, (len(words) - 9, len(words)))

    def test_the_final_saying_is_read_off_the_recording(self):
        # Even on a plan where the last line is already cut,
        # the video's final saying is still the sign-off: the
        # saying is a property of the recording, not the edit.
        words = make_words([
            "One sentence here.",
            "Another one follows it.",
            SIGN_OFF,
        ])
        bounds = [(w["start"], w["end"]) for w in words]
        ends = final_sweep.punctuation_sentence_ends(words)
        fin = final_sweep.final_saying_of_video(words, bounds, ends)
        self.assertEqual(fin, (len(words) - 9, len(words)))

    def test_segment_breaks_are_not_sentence_ends(self):
        # Scribe ends a segment where a take stops; punctuation
        # alone decides what landed.
        words = make_words(['"Hey,', "God,", "cancer,", 'lo-"', "life."])
        ends = final_sweep.punctuation_sentence_ends(words)
        self.assertEqual(ends, {4})


if __name__ == "__main__":
    unittest.main()
