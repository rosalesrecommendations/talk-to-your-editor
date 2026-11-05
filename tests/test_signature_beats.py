"""Signature beats (0.28): learn, store, find, and place.

The engine used to only remove. A signature beat is the
wordless sound a creator splices in out of time order (his
breath and water sound after "Today's temperature is"),
learned from a before/after pair, stored per style beside
the rules, and placed into a new edit in that style from the
new video's own setup. These tests pin the anchor reading
(number words and digits are one token; "today's" sounds
like "today is"), the pair learning on a synthetic pair with
a known splice, the store round trip, and the plan surgery
(words never touched, pauses fit the natural gap).
"""

import json
import math
import random
import sys
import tempfile
import unittest
import wave
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import creator_rules, signature  # noqa: E402

SR = 16000


def _words(*specs):
    return [
        {"word": w, "start": s, "end": e} for (w, s, e) in specs
    ]


DAY_WORDS = _words(
    ("Today's", 152.4, 152.9),
    ("temperature", 153.0, 153.6),
    ("is", 153.7, 153.9),
    ("40", 154.1, 154.4),
    ("degrees", 154.5, 155.0),
)

SCRIBE_WORDS = _words(
    ("Today", 152.4, 152.7),
    ("is", 152.7, 152.9),
    ("day", 153.0, 153.3),
    ("thirty", 153.4, 153.8),
    ("of", 153.9, 154.1),
)


def _beat(**over):
    beat = {
        "anchor_before_tokens": ["today", "is", "temperature", "is"],
        "anchor_after_tokens": ["40"],
        "anchor_before_text": "Today's temperature is",
        "anchor_after_text": "40",
        "label": "water beat",
        "length_s": 1.38,
        "loudness_db": -25.0,
        "pause_before_s": 0.15,
        "pause_after_s": 0.15,
        "profile": [-30.0] * 24,
    }
    beat.update(over)
    return beat


class AnchorTokenTests(unittest.TestCase):
    def test_number_words_and_digits_are_one_token(self):
        self.assertEqual(
            signature.anchor_tokens_for_word({"word": "thirty"}), ["30"]
        )
        self.assertEqual(
            signature.anchor_tokens_for_word({"word": "30"}), ["30"]
        )
        self.assertEqual(
            signature.anchor_tokens_for_word({"word": "forty-two"}),
            ["42"],
        )

    def test_contraction_sounds_like_two_words(self):
        self.assertEqual(
            signature.anchor_tokens_for_word({"word": "Today's"}),
            ["today", "is"],
        )
        self.assertEqual(
            signature.anchor_tokens_for_word({"word": "today"}), ["today"]
        )

    def test_find_anchor_in_either_spelling(self):
        beat = _beat()
        self.assertEqual(signature.find_anchor(DAY_WORDS, beat), 2)
        scribe_beat = _beat(
            anchor_before_tokens=["today", "is"],
            anchor_after_tokens=["day", "30"],
        )
        self.assertEqual(signature.find_anchor(SCRIBE_WORDS, scribe_beat), 1)

    def test_find_anchor_missing_returns_none(self):
        beat = _beat(anchor_after_tokens=["banana"])
        self.assertIsNone(signature.find_anchor(DAY_WORDS, beat))
        beat = _beat(anchor_before_tokens=["yesterday", "is"])
        self.assertIsNone(signature.find_anchor(DAY_WORDS, beat))

    def test_find_anchors_returns_every_saying(self):
        # He restates his opening: the template line can be
        # said twice, and the pass tries each saying in order.
        words = _words(
            ("Today's", 10.0, 10.5), ("temperature", 10.6, 11.0),
            ("is", 11.1, 11.3), ("40", 11.5, 11.8),
            ("degrees", 11.9, 12.3), ("Today's", 20.0, 20.5),
            ("temperature", 20.6, 21.0), ("is", 21.1, 21.3),
            ("38", 21.5, 21.8), ("degrees", 21.9, 22.3),
        )
        self.assertEqual(
            signature.find_anchors(words, _beat()), [2, 7]
        )

    def test_anchor_number_matches_any_number(self):
        # The temperature changes every day: an anchor learned
        # at 40 degrees must land at 38 degrees too.
        words = _words(
            ("Today's", 10.0, 10.5), ("temperature", 10.6, 11.0),
            ("is", 11.1, 11.3), ("38", 11.5, 11.8),
            ("degrees", 11.9, 12.3),
        )
        self.assertEqual(signature.find_anchor(words, _beat()), 2)


def _burst(samples, t0, dur, seed):
    rng = random.Random(seed)
    start = int(t0 * SR)
    for k in range(int(dur * SR)):
        t = k / SR
        env = (math.sin(math.pi * t / dur) ** 2) * (
            0.55 + 0.45 * math.sin(2 * math.pi * 1.3 * t)
        )
        samples[start + k] = 0.34 * env * rng.uniform(-1, 1)


def _speech(samples, t0, dur, seed):
    rng = random.Random(seed + 100)
    start = int(t0 * SR)
    for k in range(int(dur * SR)):
        samples[start + k] = 0.22 * rng.uniform(-1, 1)


def _make_wav(path, total_s, bursts, speeches):
    rng = random.Random(7)
    samples = [0.004 * rng.uniform(-1, 1) for _ in range(int(total_s * SR))]
    for n, (t0, dur) in enumerate(speeches):
        _speech(samples, t0, dur, n)
    for n, (t0, dur) in enumerate(bursts):
        _burst(samples, t0, dur, n)
    frames = array(
        "h", [max(-32767, min(32767, int(v * 32767))) for v in samples]
    )
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(frames.tobytes())


class LearnTests(unittest.TestCase):
    def test_learn_finds_the_spliced_sound(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            raw = work / "raw.wav"
            fin = work / "fin.wav"
            _make_wav(
                raw, 6.0, [(1.0, 1.4)],
                [(3.0, 0.4), (3.5, 0.6), (4.2, 0.2), (4.5, 0.3), (4.9, 0.5)],
            )
            _make_wav(
                fin, 7.6, [(4.55, 1.4)],
                [(3.0, 0.4), (3.5, 0.6), (4.2, 0.2), (6.1, 0.3), (6.5, 0.5)],
            )
            raw_words = _words(
                ("Today's", 3.0, 3.4), ("temperature", 3.5, 4.1),
                ("is", 4.2, 4.4), ("40", 4.5, 4.8), ("degrees", 4.9, 5.4),
            )
            fin_words = _words(
                ("Today's", 3.0, 3.4), ("temperature", 3.5, 4.1),
                ("is", 4.2, 4.4), ("40", 6.1, 6.4), ("degrees", 6.5, 7.0),
            )
            beats = signature.learn_beats(
                raw, fin, raw_words, fin_words, work / "wd"
            )
            self.assertEqual(len(beats), 1, beats)
            beat = beats[0]
            self.assertEqual(beat["label"], "water beat")
            self.assertEqual(
                beat["anchor_before_tokens"][-2:], ["temperature", "is"]
            )
            self.assertEqual(beat["anchor_after_tokens"][0], "40")
            # The source is the setup burst, not the speech.
            self.assertGreaterEqual(beat["source_start"], 0.9)
            self.assertLessEqual(beat["source_end"], 2.5)
            self.assertAlmostEqual(beat["length_s"], 1.4, delta=0.25)
            self.assertGreater(beat["pause_before_s"], 0.0)
            self.assertGreater(beat["pause_after_s"], 0.0)

    def test_learn_without_a_splice_finds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            raw = work / "raw.wav"
            fin = work / "fin.wav"
            spans = [(3.0, 0.4), (3.5, 0.6), (4.2, 0.2), (4.5, 0.3)]
            _make_wav(raw, 6.0, [], spans)
            _make_wav(fin, 6.0, [], spans)
            words = _words(
                ("Today's", 3.0, 3.4), ("temperature", 3.5, 4.1),
                ("is", 4.2, 4.4), ("40", 4.5, 4.8),
            )
            beats = signature.learn_beats(raw, fin, words, words, work / "wd")
            self.assertEqual(beats, [])


class StoreTests(unittest.TestCase):
    def test_beats_survive_a_store_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "creator_rules.json"
            store = creator_rules.load_rules_store(path)
            beat = _beat()
            beat["beat_id"] = signature.beat_id_for(beat, "My editing")
            beat["style"] = "My editing"
            creator_rules.save_beat(store, beat)
            creator_rules.save_rules_store(path, store)
            loaded = creator_rules.load_rules_store(path)
            beats = creator_rules.beats_for_style(loaded, "My editing")
            self.assertEqual(len(beats), 1)
            self.assertEqual(beats[0]["label"], "water beat")
            self.assertTrue(beats[0]["active"])
            # Learning the same beat again refreshes, never stacks,
            # and keeps the creator's off switch.
            creator_rules.set_beat_active(loaded, beat["beat_id"], False)
            creator_rules.save_beat(loaded, dict(beat))
            beats = creator_rules.beats_for_style(loaded, "My editing")
            self.assertEqual(len(beats), 1)
            self.assertFalse(beats[0]["active"])
            removed = creator_rules.remove_beat(loaded, beat["beat_id"])
            self.assertIsNotNone(removed)
            self.assertEqual(creator_rules.beats_for_style(loaded, "My editing"), [])


class ApplyTests(unittest.TestCase):
    def _plan(self):
        return {
            "kept_ranges": [{"start": 152.0, "end": 156.0}],
            "join_pauses_s": [],
            "word_sound_bounds": {
                str(k): [w["start"] + 0.03, w["end"] - 0.03]
                for k, w in enumerate(DAY_WORDS)
            },
            "signature": {"breath": None, "beat": None},
            "post_edits": [],
        }

    def test_apply_splits_the_holding_range(self):
        cuts = self._plan()
        beat = _beat()
        beat["beat_id"] = "beat-test"
        placed = signature.apply_to_plan(
            cuts, DAY_WORDS, beat, 135.9, 137.3, 2
        )
        self.assertIsNotNone(placed)
        ranges = cuts["kept_ranges"]
        self.assertEqual(len(ranges), 3)
        insert = ranges[placed["range_index"]]
        self.assertEqual(insert["insert_kind"], "signature_beat")
        self.assertEqual((insert["start"], insert["end"]), (135.9, 137.3))
        # The words themselves are untouched: the left range
        # still ends after "is" sounds, the right still starts
        # before "40" sounds, and the pauses fit the gap.
        self.assertGreaterEqual(ranges[0]["end"], 153.87)
        self.assertLessEqual(ranges[2]["start"], 154.13)
        total_pause = placed["pause_before_s"] + placed["pause_after_s"]
        self.assertLessEqual(total_pause, 0.21)
        self.assertGreater(total_pause, 0.19)
        self.assertEqual(cuts["signature"]["beat"]["kind"], "signature_beat")
        self.assertAlmostEqual(
            cuts["output_duration_s"],
            sum(r["end"] - r["start"] for r in ranges),
            places=3,
        )

    def test_apply_accepts_list_form_sound_bounds(self):
        # Real plans store word_sound_bounds as a list indexed
        # by word, not a dict; the surgery must read that form.
        cuts = self._plan()
        bounds = cuts.pop("word_sound_bounds")
        cuts["word_sound_bounds"] = [
            bounds[str(k)] for k in range(len(DAY_WORDS))
        ]
        beat = _beat()
        placed = signature.apply_to_plan(
            cuts, DAY_WORDS, beat, 135.9, 137.3, 2
        )
        self.assertIsNotNone(placed)

    def test_apply_with_loose_plan_bounds(self):
        # Real plans carry per-word bounds as loose spans that
        # overlap their neighbours (fit for membership
        # midpoints, not edges): "is" [157.43, 158.29] and
        # "forty" [157.67, 158.63] on the Day 30 Scribe plan.
        # The join geometry must not read edges off them.
        cuts = self._plan()
        cuts["word_sound_bounds"] = [
            [w["start"] - 0.35, w["end"] + 0.35] for w in DAY_WORDS
        ]
        beat = _beat()
        placed = signature.apply_to_plan(
            cuts, DAY_WORDS, beat, 135.9, 137.3, 2
        )
        self.assertIsNotNone(placed)

    def test_apply_refuses_a_cut_anchor_word(self):
        cuts = self._plan()
        # Cut "40" out of the plan: shrink the range to end
        # before it and add nothing after.
        cuts["kept_ranges"] = [{"start": 152.0, "end": 154.0}]
        beat = _beat()
        self.assertIsNone(
            signature.apply_to_plan(cuts, DAY_WORDS, beat, 135.9, 137.3, 2)
        )

    def test_beat_id_is_stable_per_style_and_anchor(self):
        beat = _beat()
        self.assertEqual(
            signature.beat_id_for(beat, "My editing"),
            signature.beat_id_for(beat, "My editing"),
        )
        self.assertNotEqual(
            signature.beat_id_for(beat, "My editing"),
            signature.beat_id_for(beat, "Another style"),
        )


if __name__ == "__main__":
    unittest.main()
