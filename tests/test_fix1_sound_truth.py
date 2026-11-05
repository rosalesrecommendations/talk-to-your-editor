"""Fix 1 (0.26): sound is the source of truth.

The bug, from Justin's 0.25.0 Mac run: the reworded retake was
audible in the output ("Every time I've gotten into this tank,
I said, Today's day blank of...") while the word list marked it
cut. The plan's ranges were set from measured sound, but the
word bookkeeping (editor score, transcript dots and labels,
counts) still assigned words to pieces by transcript stamp
midpoints, and stamps drift. These tests pin the new contract:

- a word the plan cut whose sound sits inside a kept piece has
  that sound removed from the piece;
- a word the plan keeps whose sound fell outside its piece gets
  the piece extended over its sound;
- no piece edge stays inside a word's sound;
- a case that cannot be settled without a judgment call (a cut
  word sharing one stretch of sound with a kept word) is
  reported unresolved and left alone;
- Timeline reads word membership from the plan's stored sound
  bounds when they are present, and from stamps when they are
  not (the old plans' behaviour is unchanged).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talk_to_your_editor import smooth  # noqa: E402
from talk_to_your_editor.timeline import Timeline  # noqa: E402

ROOM = -50.0
SPEECH = -20.0


def env_with(spans, total_s=12.0):
    env = [ROOM] * int(total_s / smooth.FRAME_S)
    for start, end, level in spans:
        a = int(start / smooth.FRAME_S)
        b = int(end / smooth.FRAME_S)
        for i in range(a, min(b, len(env))):
            env[i] = level
    return env


def word(text, start, end):
    return {"word": text, "start": start, "end": end}


def span(start, end):
    return {"start": start, "end": end}


class CutSoundInsideKeptPiece(unittest.TestCase):
    """The named shape: a settled retake's words are marked cut
    (their stamps sit outside the piece) while their sound sits
    inside it and would play."""

    def _fixture(self):
        words = [
            word("Hello", 1.0, 1.4),
            word("there.", 1.5, 1.9),
            # The cut take's last word: stamped at 2.75-3.05, so
            # its stamp midpoint (2.90) is outside the piece, but
            # it sounds at 2.27-2.57, inside the piece.
            word("gotten", 2.75, 3.05),
            word("Again", 6.0, 6.4),
            word("now.", 6.5, 6.9),
        ]
        spans = [
            span(0.97, 1.43), span(1.47, 1.93),
            span(2.27, 2.57),
            span(5.97, 6.43), span(6.47, 6.93),
        ]
        env = env_with(
            [(s["start"], s["end"], SPEECH) for s in spans],
            total_s=9.0,
        )
        ranges = [
            {"start": 0.95, "end": 2.6},
            {"start": 5.95, "end": 7.0},
        ]
        return words, spans, env, ranges

    def test_the_bug_exists_on_the_stamp_ruler(self):
        words, spans, env, ranges = self._fixture()
        bounds = smooth.sound_word_bounds(words, env, spans)
        self.assertEqual(
            smooth.stamp_statuses(words, ranges)[2], "cut"
        )
        self.assertEqual(
            smooth.sound_statuses(words, ranges, bounds)[2], "kept"
        )

    def test_the_cut_words_sound_is_removed_from_the_piece(self):
        words, spans, env, ranges = self._fixture()
        out, report = smooth.sound_truth_pass(
            words, ranges, env, spans=spans, blocked_words={2}
        )
        self.assertEqual(report["cut_sounds_removed"], 1)
        self.assertEqual(report["unresolved"], [])
        bounds = smooth.sound_word_bounds(words, env, spans)
        after = smooth.sound_statuses(words, out, bounds)
        self.assertEqual(after[2], "cut")
        # The kept words around it are untouched.
        self.assertEqual(after[0], "kept")
        self.assertEqual(after[1], "kept")
        self.assertEqual(after[3], "kept")
        self.assertEqual(after[4], "kept")

    def test_a_stamp_cut_word_is_removed_even_without_a_pair_record(self):
        words, spans, env, ranges = self._fixture()
        out, report = smooth.sound_truth_pass(words, ranges, env, spans=spans)
        self.assertEqual(report["cut_sounds_removed"], 1)
        bounds = smooth.sound_word_bounds(words, env, spans)
        self.assertEqual(
            smooth.sound_statuses(words, out, bounds)[2], "cut"
        )


class KeptSoundOutsidePiece(unittest.TestCase):
    """The December shape in reverse: the plan keeps a word (its
    stamp is inside the piece) but the word sounds before the
    piece starts, so the render would not play it."""

    def test_the_piece_extends_over_the_kept_words_sound(self):
        words = [
            word("December", 2.1, 2.4),
            word("29th.", 2.6, 3.0),
        ]
        spans = [span(1.67, 1.97), span(2.57, 3.03)]
        env = env_with(
            [(s["start"], s["end"], SPEECH) for s in spans],
            total_s=5.0,
        )
        ranges = [{"start": 2.0, "end": 3.2}]
        bounds = smooth.sound_word_bounds(words, env, spans)
        self.assertEqual(
            smooth.sound_statuses(words, ranges, bounds)[0], "cut"
        )
        out, report = smooth.sound_truth_pass(
            words, ranges, env, spans=spans
        )
        self.assertEqual(report["kept_sounds_restored"], 1)
        self.assertLessEqual(out[0]["start"], bounds[0][0])
        after = smooth.sound_statuses(words, out, bounds)
        self.assertEqual(after[0], "kept")
        self.assertEqual(after[1], "kept")


class EdgeInsideWord(unittest.TestCase):
    def test_a_start_edge_inside_a_kept_word_moves_before_its_sound(self):
        words = [word("Hello", 1.0, 1.5), word("there.", 1.6, 2.0)]
        spans = [span(0.97, 1.53), span(1.57, 2.03)]
        env = env_with(
            [(s["start"], s["end"], SPEECH) for s in spans],
            total_s=4.0,
        )
        ranges = [{"start": 1.2, "end": 2.2}]
        out, report = smooth.sound_truth_pass(
            words, ranges, env, spans=spans
        )
        self.assertGreaterEqual(report["edges_moved"], 1)
        bounds = smooth.sound_word_bounds(words, env, spans)
        self.assertLessEqual(out[0]["start"], bounds[0][0])

    def test_a_start_edge_inside_a_cut_word_moves_past_its_sound(self):
        words = [word("gotten", 1.0, 1.5), word("Hello", 1.6, 2.0)]
        spans = [span(0.97, 1.53), span(1.57, 2.03)]
        env = env_with(
            [(s["start"], s["end"], SPEECH) for s in spans],
            total_s=4.0,
        )
        ranges = [{"start": 1.45, "end": 2.2}]
        out, report = smooth.sound_truth_pass(
            words, ranges, env, spans=spans, blocked_words={0}
        )
        bounds = smooth.sound_word_bounds(words, env, spans)
        self.assertGreaterEqual(out[0]["start"], bounds[0][1])
        after = smooth.sound_statuses(words, out, bounds)
        self.assertEqual(after[0], "cut")
        self.assertEqual(after[1], "kept")


class SharedSoundIsReportedNotForced(unittest.TestCase):
    """Continuous speech: the cut take and the kept take share
    one stretch of sound. There is no edge that removes one
    without the other, so the pass reports it and leaves the
    plan alone."""

    def test_a_cut_word_sharing_sound_with_a_kept_word_is_unresolved(self):
        words = [word("Through", 1.0, 1.4), word("the", 1.5, 1.9)]
        spans = [span(0.97, 1.93)]
        env = env_with([(0.97, 1.93, SPEECH)], total_s=4.0)
        ranges = [{"start": 0.95, "end": 2.2}]
        out, report = smooth.sound_truth_pass(
            words, ranges, env, spans=spans, blocked_words={1}
        )
        self.assertEqual(report["cut_sounds_removed"], 0)
        self.assertEqual(len(report["unresolved"]), 1)
        self.assertEqual(report["unresolved"][0]["word_index"], 1)
        self.assertEqual(out, ranges)


class SliverPieces(unittest.TestCase):
    """A fragment under a frame or two that holds no kept word's
    sound is dropped (it can render with no picture at all); a
    short piece holding a kept word's sound midpoint stays."""

    def test_fragment_without_a_kept_word_is_dropped(self):
        # Word 1 is a settled cut whose sound tail leaves a
        # 0.03 s fragment behind when the edge moves out of it.
        words = [
            word("Hello", 1.0, 1.4),
            word("gotten", 2.0, 2.3),
            word("world", 3.0, 3.4),
        ]
        env = env_with([
            (1.0, 1.4, SPEECH), (2.0, 2.3, SPEECH), (3.0, 3.4, SPEECH),
        ])
        ranges = [
            {"start": 0.95, "end": 1.6},
            {"start": 2.27, "end": 2.30},
            {"start": 2.95, "end": 3.6},
        ]
        out, report = smooth.sound_truth_pass(
            words, ranges, env, blocked_words={1},
        )
        self.assertEqual(report["slivers_dropped"], 1)
        self.assertEqual(
            [(r["start"], r["end"]) for r in out],
            [(0.95, 1.6), (2.95, 3.6)],
        )

    def test_short_piece_holding_a_kept_word_stays(self):
        # A tiny kept word ("a") whose whole sound is 0.04 s:
        # its piece is short but carries a whole word.
        words = [word("a", 1.0, 1.04), word("world", 3.0, 3.4)]
        env = env_with([(1.0, 1.04, SPEECH), (3.0, 3.4, SPEECH)])
        ranges = [
            {"start": 0.99, "end": 1.03},
            {"start": 2.95, "end": 3.6},
        ]
        out, report = smooth.sound_truth_pass(words, ranges, env)
        self.assertEqual(report["slivers_dropped"], 0)
        self.assertEqual(len(out), 2)


class TimelineReadsSound(unittest.TestCase):
    def _transcript(self):
        return {
            "duration": 4.0,
            "words": [word("Hello", 1.0, 1.4), word("gotten", 2.75, 3.05)],
        }

    def test_without_bounds_the_stamp_reading_is_unchanged(self):
        cuts = {"kept_ranges": [{"start": 0.95, "end": 2.6}]}
        timeline = Timeline(self._transcript(), cuts)
        self.assertEqual(timeline.word_status(0), "kept")
        self.assertEqual(timeline.word_status(1), "cut")

    def test_with_bounds_the_sound_reading_wins(self):
        cuts = {
            "kept_ranges": [{"start": 0.95, "end": 2.6}],
            # "gotten" sounds at 2.27-2.57, inside the piece,
            # whatever its stamp says.
            "word_sound_bounds": [[1.0, 1.4], [2.27, 2.57]],
        }
        timeline = Timeline(self._transcript(), cuts)
        self.assertEqual(timeline.word_status(0), "kept")
        self.assertEqual(timeline.word_status(1), "kept")
        self.assertEqual(timeline.kept_word_indices(), [0, 1])

    def test_mismatched_bounds_fall_back_to_stamps(self):
        cuts = {
            "kept_ranges": [{"start": 0.95, "end": 2.6}],
            "word_sound_bounds": [[1.0, 1.4]],
        }
        timeline = Timeline(self._transcript(), cuts)
        self.assertEqual(timeline.word_status(1), "cut")


if __name__ == "__main__":
    unittest.main()
