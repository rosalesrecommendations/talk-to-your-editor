"""v0.16 Batch A: manual timeline edits, the pure plan transforms.

The fixture is a ten-word recording, one word a second with a
second of quiet between words, and a four-piece plan:

- A [0,5]    words 0,1,2
- S [6,7]    a special insert (signature breath) over word 3
- B [8,13]   words 4,5,6   (word 5 is protected by the profile)
- C [16,19]  words 8,9     (word 7 is cut)

Output order A, S, B, C plays 5 + 1 + 5 + 3 = 14 seconds.
Everything is offline; no provider is called.
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import manual_edits as me
from talk_to_your_editor.timeline import Timeline


def make_words():
    return [
        {"word": f"w{i}", "start": float(2 * i), "end": float(2 * i + 1)}
        for i in range(10)
    ]


def make_cuts():
    ranges = [
        {"start": 0.0, "end": 5.0},
        {"start": 6.0, "end": 7.0, "insert_kind": "signature_breath"},
        {"start": 8.0, "end": 13.0},
        {"start": 16.0, "end": 19.0},
    ]
    return {
        "kept_ranges": copy.deepcopy(ranges),
        "source_duration_s": 20.0,
        "join_pauses_s": [0.0, 0.0, 0.0],
    }


PROFILE = {"protected_spans": [{"word_start": 5, "word_end": 6, "label": "keep"}]}


def make_timeline(cuts=None):
    cuts = cuts if cuts is not None else make_cuts()
    return Timeline({"words": make_words(), "duration": 20.0}, cuts), cuts


def spans(cuts):
    return [(r["start"], r["end"]) for r in cuts["kept_ranges"]]


class SplitTests(unittest.TestCase):
    def test_split_makes_two_pieces_and_changes_nothing_heard(self):
        timeline, cuts = make_timeline()
        new_cuts, info = me.split_range(cuts, timeline, 2, 10.5)
        self.assertEqual(
            spans(new_cuts),
            [(0.0, 5.0), (6.0, 7.0), (8.0, 10.5), (10.5, 13.0), (16.0, 19.0)],
        )
        fresh = Timeline(timeline.transcript, new_cuts)
        self.assertEqual(fresh.output_duration, timeline.output_duration)
        self.assertEqual(fresh.kept_word_indices(), timeline.kept_word_indices())

    def test_split_too_close_to_an_edge_refuses(self):
        timeline, cuts = make_timeline()
        with self.assertRaises(me.ManualEditRefused):
            me.split_range(cuts, timeline, 2, 12.98)

    def test_split_special_refuses(self):
        timeline, cuts = make_timeline()
        with self.assertRaises(me.ManualEditRefused):
            me.split_range(cuts, timeline, 1, 6.5)

    def test_split_at_output_time_uses_the_playhead(self):
        timeline, cuts = make_timeline()
        # Output 8.0 sits inside B (output 6..11) at source 10.0.
        new_cuts, info = me.split_at_output_time(cuts, timeline, 8.0)
        self.assertEqual(info["range_index"], 2)
        self.assertIn((8.0, 10.0), spans(new_cuts))
        self.assertIn((10.0, 13.0), spans(new_cuts))

    def test_split_at_output_time_on_special_refuses(self):
        timeline, cuts = make_timeline()
        with self.assertRaises(me.ManualEditRefused):
            me.split_at_output_time(cuts, timeline, 5.5)

    def test_input_plan_is_never_mutated(self):
        timeline, cuts = make_timeline()
        before = copy.deepcopy(cuts)
        me.split_range(cuts, timeline, 2, 10.5)
        self.assertEqual(cuts, before)


class TrimTests(unittest.TestCase):
    def test_extend_end_pulls_back_a_cut_word(self):
        timeline, cuts = make_timeline()
        new_cuts, info = me.trim_edge(cuts, timeline, 2, "end", 15.5)
        self.assertTrue(info["changed"])
        self.assertIn((8.0, 15.5), spans(new_cuts))
        fresh = Timeline(timeline.transcript, new_cuts)
        self.assertIn(7, fresh.kept_word_indices())

    def test_extend_start_clamps_at_a_special_insert(self):
        timeline, cuts = make_timeline()
        new_cuts, info = me.trim_edge(cuts, timeline, 2, "start", 6.5)
        self.assertIn((7.0, 13.0), spans(new_cuts))
        self.assertIn("next-piece", info["clamps"])

    def test_shrink_start_clamps_at_a_protected_word(self):
        timeline, cuts = make_timeline()
        new_cuts, info = me.trim_edge(
            cuts, timeline, 2, "start", 10.5, profile=PROFILE
        )
        self.assertIn((10.0, 13.0), spans(new_cuts))
        self.assertIn("protected", info["clamps"])
        fresh = Timeline(timeline.transcript, new_cuts)
        self.assertIn(5, fresh.kept_word_indices())
        self.assertNotIn(4, fresh.kept_word_indices())

    def test_shrink_end_keeps_the_minimum_length(self):
        timeline, cuts = make_timeline()
        new_cuts, info = me.trim_edge(cuts, timeline, 0, "end", 0.05)
        self.assertIn((0.0, 0.1), spans(new_cuts))
        self.assertIn("min-length", info["clamps"])

    def test_trim_special_refuses(self):
        timeline, cuts = make_timeline()
        with self.assertRaises(me.ManualEditRefused):
            me.trim_edge(cuts, timeline, 1, "end", 7.5)


class DeleteTests(unittest.TestCase):
    def test_delete_takes_the_piece_out(self):
        timeline, cuts = make_timeline()
        new_cuts, _info = me.delete_range(cuts, timeline, 3, profile=PROFILE)
        self.assertEqual(spans(new_cuts), [(0.0, 5.0), (6.0, 7.0), (8.0, 13.0)])

    def test_delete_piece_holding_protected_words_refuses(self):
        timeline, cuts = make_timeline()
        with self.assertRaises(me.ManualEditRefused):
            me.delete_range(cuts, timeline, 2, profile=PROFILE)

    def test_delete_special_refuses(self):
        timeline, cuts = make_timeline()
        with self.assertRaises(me.ManualEditRefused):
            me.delete_range(cuts, timeline, 1)

    def test_delete_the_only_piece_refuses(self):
        cuts = {"kept_ranges": [{"start": 0.0, "end": 5.0}],
                "source_duration_s": 20.0}
        timeline = Timeline({"words": make_words(), "duration": 20.0}, cuts)
        with self.assertRaises(me.ManualEditRefused):
            me.delete_range(cuts, timeline, 0)


class MoveTests(unittest.TestCase):
    def test_move_reorders_the_pieces(self):
        timeline, cuts = make_timeline()
        new_cuts, info = me.move_range(cuts, timeline, 3, 2)
        self.assertTrue(info["changed"])
        self.assertEqual(
            spans(new_cuts),
            [(0.0, 5.0), (6.0, 7.0), (16.0, 19.0), (8.0, 13.0)],
        )

    def test_move_across_a_special_refuses(self):
        timeline, cuts = make_timeline()
        with self.assertRaises(me.ManualEditRefused):
            me.move_range(cuts, timeline, 0, 3)

    def test_move_a_special_refuses(self):
        timeline, cuts = make_timeline()
        with self.assertRaises(me.ManualEditRefused):
            me.move_range(cuts, timeline, 1, 3)


class PasteTests(unittest.TestCase):
    def test_paste_at_playhead_splits_the_piece_and_inserts(self):
        timeline, cuts = make_timeline()
        new_cuts, info = me.paste_spans(
            cuts, timeline, [{"start": 14.0, "end": 15.0}], 8.0
        )
        self.assertTrue(info["split_piece"])
        self.assertEqual(
            spans(new_cuts),
            [(0.0, 5.0), (6.0, 7.0), (8.0, 10.0), (14.0, 15.0),
             (10.0, 13.0), (16.0, 19.0)],
        )
        fresh = Timeline(timeline.transcript, new_cuts)
        self.assertEqual(fresh.output_duration, timeline.output_duration + 1.0)

    def test_paste_on_a_special_lands_just_after_it(self):
        timeline, cuts = make_timeline()
        new_cuts, info = me.paste_spans(
            cuts, timeline, [{"start": 14.0, "end": 15.0}], 5.5
        )
        self.assertEqual(info["inserted_at"], 2)
        self.assertEqual(spans(new_cuts)[2], (14.0, 15.0))

    def test_paste_with_nothing_copied_refuses(self):
        timeline, cuts = make_timeline()
        with self.assertRaises(me.ManualEditRefused):
            me.paste_spans(cuts, timeline, [], 8.0)

    def test_paste_outside_the_recording_refuses(self):
        timeline, cuts = make_timeline()
        with self.assertRaises(me.ManualEditRefused):
            me.paste_spans(cuts, timeline, [{"start": 25.0, "end": 26.0}], 8.0)


class FadeTests(unittest.TestCase):
    def test_set_and_read_a_join_fade(self):
        timeline, cuts = make_timeline()
        new_cuts, info = me.set_join_fade(cuts, timeline, 2, 0.25)
        self.assertEqual(info["fade_s"], 0.25)
        self.assertEqual(me.join_fades(new_cuts["kept_ranges"]), [None, None, 0.25])
        cleared, _ = me.set_join_fade(new_cuts, Timeline(timeline.transcript, new_cuts), 2, 0)
        self.assertEqual(me.join_fades(cleared["kept_ranges"]), [None, None, None])

    def test_fade_into_a_special_refuses(self):
        timeline, cuts = make_timeline()
        with self.assertRaises(me.ManualEditRefused):
            me.set_join_fade(cuts, timeline, 0, 0.25)

    def test_fade_over_half_a_second_refuses(self):
        timeline, cuts = make_timeline()
        with self.assertRaises(me.ManualEditRefused):
            me.set_join_fade(cuts, timeline, 2, 0.75)

    def test_fade_stays_with_its_piece_across_a_move(self):
        timeline, cuts = make_timeline()
        faded, _ = me.set_join_fade(cuts, timeline, 2, 0.25)
        timeline2 = Timeline(timeline.transcript, faded)
        moved, _ = me.move_range(faded, timeline2, 3, 2)
        self.assertEqual(moved["kept_ranges"][2].get("fade_in_s"), 0.25)

    def test_split_right_half_starts_with_no_fade(self):
        timeline, cuts = make_timeline()
        faded, _ = me.set_join_fade(cuts, timeline, 1, 0.1)
        timeline2 = Timeline(timeline.transcript, faded)
        split, _ = me.split_range(faded, timeline2, 2, 10.5)
        self.assertEqual(split["kept_ranges"][2].get("fade_in_s"), 0.1)
        self.assertNotIn("fade_in_s", split["kept_ranges"][3])


class BookkeepingTests(unittest.TestCase):
    def test_join_pauses_are_recomputed_to_match_the_joins(self):
        timeline, cuts = make_timeline()
        new_cuts, _ = me.split_range(cuts, timeline, 2, 10.5)
        self.assertEqual(len(new_cuts["join_pauses_s"]), 4)
        self.assertEqual(
            new_cuts["join_pauses_s"],
            me.compute_join_pauses(new_cuts["kept_ranges"], timeline.words),
        )

    def test_stats_follow_the_plan(self):
        timeline, cuts = make_timeline()
        new_cuts, _ = me.delete_range(cuts, timeline, 3, profile=PROFILE)
        self.assertEqual(new_cuts["output_duration_s"], 11.0)
        self.assertEqual(new_cuts["words_total"], 10)


if __name__ == "__main__":
    unittest.main()
