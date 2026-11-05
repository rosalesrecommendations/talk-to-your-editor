"""SMOOTH, NEVER CHOPPY (Message 2R, P0): the join-quality engine.

Synthetic envelopes only: a quiet room at -50 dB, speech at -20 dB,
breaths at -35 dB, frames of 10 ms like the real envelope.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talk_to_your_editor import smooth  # noqa: E402

ROOM = -50.0
SPEECH = -20.0
BREATH = -35.0


def env_with(spans, total_s=10.0):
    """spans: list of (start, end, level). Everything else is room."""
    env = [ROOM] * int(total_s / smooth.FRAME_S)
    for start, end, level in spans:
        a = int(start / smooth.FRAME_S)
        b = int(end / smooth.FRAME_S)
        for i in range(a, min(b, len(env))):
            env[i] = level
    return env


def word(text, start, end):
    return {"word": text, "start": start, "end": end}


class RoomAndBounds(unittest.TestCase):
    def test_room_level_is_the_quiet_floor(self):
        env = env_with([(1.0, 2.0, SPEECH)])
        self.assertAlmostEqual(smooth.room_level_db(env), ROOM, places=1)

    def test_word_end_follows_a_quiet_tail(self):
        # The transcript says the word ends at 2.0, but its sound
        # (a soft last syllable, only 8 dB over the room) runs to
        # 2.3. The measured end must follow the sound.
        env = env_with([(1.0, 2.0, SPEECH), (2.0, 2.3, ROOM + 8.0)])
        start, end = smooth.word_sound_bounds(
            env, ROOM, word("free", 1.0, 2.0)
        )
        self.assertGreaterEqual(end, 2.28)
        self.assertLessEqual(start, 1.01)


class SmoothPlan(unittest.TestCase):
    def setUp(self):
        # Two words with a long gap of room sound between them.
        self.words = [word("hello", 1.0, 1.5), word("world", 3.0, 3.5)]
        self.env = env_with([(1.0, 1.5, SPEECH), (3.0, 3.5, SPEECH)])
        self.ranges = [
            {"start": 0.9, "end": 2.2},
            {"start": 2.4, "end": 3.6},
        ]

    def test_edges_land_on_day30_numbers(self):
        new_ranges, report = smooth.smooth_plan(
            self.words, self.ranges, self.env, segments=None
        )
        prev_end = new_ranges[0]["end"]
        next_start = new_ranges[1]["start"]
        # Inside a sentence: 0.13 after the last word's sound,
        # 0.04 before the next word's first sound.
        self.assertAlmostEqual(prev_end, 1.5 + 0.13, delta=0.02)
        self.assertAlmostEqual(next_start, 3.0 - 0.04, delta=0.02)
        self.assertEqual(report["your_call"], [])

    def test_sentence_end_gets_the_longer_tail(self):
        segments = [{"start": 1.0, "end": 1.5, "text": "hello"}]
        new_ranges, _ = smooth.smooth_plan(
            self.words, self.ranges, self.env, segments=segments
        )
        self.assertAlmostEqual(new_ranges[0]["end"], 1.5 + 0.19, delta=0.02)

    def test_breath_is_never_cut_in_half(self):
        env = env_with(
            [(1.0, 1.5, SPEECH), (2.0, 2.4, BREATH), (3.0, 3.5, SPEECH)]
        )
        new_ranges, report = smooth.smooth_plan(
            self.words, self.ranges, env, segments=None
        )
        for boundary in (new_ranges[0]["end"], new_ranges[1]["start"]):
            self.assertFalse(
                2.0 < boundary < 2.4,
                f"boundary {boundary} lands inside the breath",
            )
        self.assertEqual(report["breaths_cut_whole"], 1)

    def test_mid_sentence_start_is_flagged_not_cut(self):
        words = [
            word("the", 1.0, 1.2),
            word("water", 1.3, 1.7),
            word("helps", 2.0, 2.4),
            word("people", 2.5, 2.9),
        ]
        env = env_with(
            [(1.0, 1.2, SPEECH), (1.3, 1.7, SPEECH),
             (2.0, 2.4, SPEECH), (2.5, 2.9, SPEECH)]
        )
        # The second piece starts at "helps", partway through the
        # sentence, and the piece before does not end that sentence
        # (it ends two words earlier, at "the").
        ranges = [
            {"start": 0.9, "end": 1.25},
            {"start": 1.95, "end": 3.0},
        ]
        _, report = smooth.smooth_plan(words, ranges, env, segments=None)
        kinds = [c["kind"] for c in report["your_call"]]
        self.assertIn("mid_sentence_start", kinds)

    def test_protected_join_is_untouched(self):
        new_ranges, _ = smooth.smooth_plan(
            self.words, self.ranges, self.env,
            segments=None, protected_joins={0},
        )
        self.assertEqual(new_ranges[0]["end"], 2.2)
        self.assertEqual(new_ranges[1]["start"], 2.4)


class BreathDetection(unittest.TestCase):
    def test_cut_speech_in_a_gap_is_not_a_breath(self):
        # A cut take sits between the two kept words; its speech
        # is a word, not a breath. The real breath after it is.
        words = [
            word("hello", 1.0, 1.5),
            word("um", 2.0, 2.4),
            word("world", 3.2, 3.6),
        ]
        env = env_with(
            [(1.0, 1.5, SPEECH), (2.0, 2.4, SPEECH),
             (2.7, 3.0, BREATH), (3.2, 3.6, SPEECH)]
        )
        bounds = smooth.all_word_bounds(words, env, ROOM)
        found = smooth.breath_segments(env, ROOM, 1.5, 3.2, bounds)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0][0], 2.7, delta=0.03)

    def test_bounds_never_overlap(self):
        words = [word("a", 1.0, 1.5), word("b", 1.52, 2.0)]
        env = env_with([(1.0, 2.0, SPEECH)])
        bounds = smooth.all_word_bounds(words, env, ROOM)
        self.assertLessEqual(bounds[0][1], bounds[1][0] + 1e-9)


class JoinCheck(unittest.TestCase):
    def test_cut_inside_a_word_is_found_and_fixed(self):
        words = [word("hello", 1.0, 1.5), word("world", 3.0, 3.5)]
        env = env_with([(1.0, 1.5, SPEECH), (3.0, 3.5, SPEECH)])
        # The first piece ends at 1.3, inside "hello" (sound to 1.5).
        ranges = [{"start": 0.9, "end": 1.3}, {"start": 2.9, "end": 3.6}]
        new_ranges, check = smooth.join_check(words, ranges, env)
        kinds = [k for i in check["issues"] for k in i["kinds"]]
        self.assertIn("cut_inside_word", kinds)
        self.assertGreaterEqual(new_ranges[0]["end"], 1.5)

    def test_spliced_join_gets_the_long_crossfade(self):
        words = [word("hello", 1.0, 1.5), word("world", 3.0, 3.5)]
        env = env_with([(1.0, 1.5, SPEECH), (3.0, 3.5, SPEECH)])
        ranges = [{"start": 0.9, "end": 1.63}, {"start": 2.96, "end": 3.6}]
        new_ranges, check = smooth.join_check(words, ranges, env)
        self.assertEqual(new_ranges[1].get("fade_in_s"), smooth.LONG_FADE_S)
        self.assertEqual(check["joins_checked"], 1)

    def test_pause_under_60ms_is_an_issue(self):
        words = [word("hello", 1.0, 1.5), word("world", 1.56, 2.0)]
        env = env_with([(1.0, 1.5, SPEECH), (1.56, 2.0, SPEECH)])
        ranges = [{"start": 0.9, "end": 1.52}, {"start": 1.53, "end": 2.1}]
        _, check = smooth.join_check(words, ranges, env)
        kinds = [k for i in check["issues"] for k in i["kinds"]]
        self.assertIn("pause_under_60ms", kinds)


if __name__ == "__main__":
    unittest.main()
