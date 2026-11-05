"""Tests for the after-5:30 exception, item 2: per-gap
control in Studio (stdlib unittest, no dependencies).

Justin's ruling (2026-11-04, after the 5:30 kit): every
shortened pause shows in the transcript as a small mark with
its current length ("·0.2 s"); clicking it offers − 0.1 s,
+ 0.1 s, Original, and Listen. The change moves that join
only, through the plan's existing per-join pause path (the
``set_join_pause`` operation the gap erase and the silence
pass use), lands under one checkpoint so Undo restores the
prior value, and it can never exceed the pause actually
recorded between the two words.

The backend tests run the same function the page's post
calls, on a job whose one join was shortened from a recorded
0.6 s pause to 0.3 s. The page tests read the Studio
template.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import checkpoints, webapp
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig

STUDIO = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "studio.html"
)

# Twelve words, 1 s apart, except the recorded pause between
# word 5 (ends 5.9) and word 6 (starts 6.5): 0.6 s.
STARTS = [0, 1, 2, 3, 4, 5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5]


def studio_html() -> str:
    return STUDIO.read_text(encoding="utf-8")


def write_join_job(job: Path) -> None:
    job.mkdir(parents=True, exist_ok=True)
    words = [
        {"word": f"w{i}", "start": float(s), "end": float(s) + 0.9}
        for i, s in enumerate(STARTS)
    ]
    duration = 12.4
    transcript = {"engine": "test", "duration": duration, "words": words}
    (job / "transcript.json").write_text(json.dumps(transcript))
    cuts = {
        "source_duration_s": duration,
        "kept_ranges": [{"start": 0.0, "end": duration}],
    }
    (job / "cuts.json").write_text(json.dumps(cuts))
    session = job / "tye-session"
    session.mkdir(parents=True, exist_ok=True)
    # One join between word 5 and word 6: tail 0.1 (range ends
    # 6.0, word 5 ends 5.9) plus head 0.2 (word 6 starts 6.5,
    # range starts 6.3) = a 0.3 s pause, shortened from the
    # recorded 0.6 s.
    revised = {
        "source_duration_s": duration,
        "kept_ranges": [
            {"start": 0.0, "end": 6.0},
            {"start": 6.3, "end": duration},
        ],
    }
    (session / "revised_cuts.json").write_text(json.dumps(revised))


def current_join_total(config: WebAppConfig) -> float:
    transcript = webapp._load_json(str(config.job_dir / "transcript.json"))
    timeline = Timeline(transcript, webapp._effective_cuts(config))
    words = transcript["words"]
    prev_range, next_range = timeline.ranges[0], timeline.ranges[1]
    tail = float(prev_range["end"]) - float(words[5]["end"])
    head = float(words[6]["start"]) - float(next_range["start"])
    return round(tail + head, 3)


class JoinPauseBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-exception-gap-"))
        self.job = self.tmp / "job"
        write_join_job(self.job)
        self.config = WebAppConfig(self.job)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_seed_join_is_the_shortened_pause(self):
        self.assertEqual(current_join_total(self.config), 0.3)

    def test_step_up_moves_only_that_join(self):
        result = webapp._apply_join_pause(self.config, 5, 6, 0.4)
        self.assertTrue(result["changed"])
        self.assertEqual(result["gap_before_s"], 0.3)
        self.assertEqual(result["gap_after_s"], 0.4)
        self.assertEqual(current_join_total(self.config), 0.4)

    def test_never_exceeds_the_recorded_pause(self):
        result = webapp._apply_join_pause(self.config, 5, 6, 2.0)
        self.assertTrue(result["changed"])
        self.assertEqual(result["recorded_s"], 0.6)
        self.assertEqual(result["gap_after_s"], 0.6)
        self.assertEqual(current_join_total(self.config), 0.6)
        # At the cap, another raise changes nothing.
        again = webapp._apply_join_pause(self.config, 5, 6, 0.7)
        self.assertFalse(again["changed"])
        self.assertEqual(current_join_total(self.config), 0.6)

    def test_original_restores_the_recorded_pause(self):
        webapp._apply_join_pause(self.config, 5, 6, 0.2)
        self.assertEqual(current_join_total(self.config), 0.2)
        result = webapp._apply_join_pause(self.config, 5, 6, 0.6)
        self.assertTrue(result["changed"])
        self.assertEqual(result["gap_after_s"], 0.6)

    def test_floor_is_the_plan_floor(self):
        result = webapp._apply_join_pause(self.config, 5, 6, 0.0)
        self.assertTrue(result["changed"])
        # Tail 0.1 plus the plan's minimum head 0.08.
        self.assertEqual(result["gap_after_s"], 0.18)

    def test_undo_restores_the_prior_value(self):
        webapp._apply_join_pause(self.config, 5, 6, 0.5)
        self.assertEqual(current_join_total(self.config), 0.5)
        state = checkpoints.undo(self.config)
        self.assertIsNotNone(state)
        self.assertEqual(current_join_total(self.config), 0.3)

    def test_a_gap_inside_one_piece_is_refused_plainly(self):
        result = webapp._apply_join_pause(self.config, 2, 3, 0.4)
        self.assertFalse(result["changed"])
        self.assertIn("inside one kept piece", result["message"])


class GapMarkPageTests(unittest.TestCase):
    def test_mark_is_rendered_from_the_join_values(self):
        html = studio_html()
        self.assertIn("joinMarks", html)
        self.assertIn('"jmark"', html)
        self.assertIn('className = "jmark"', html)
        # The mark's text is the current length in the
        # creator's shape: "·0.3 s".
        self.assertIn('"·" +', html)
        self.assertIn('" s"', html)

    def test_popover_carries_the_four_actions(self):
        html = studio_html()
        self.assertIn('id="gapPop"', html)
        self.assertIn('id="gapMinus"', html)
        self.assertIn('id="gapPlus"', html)
        self.assertIn('id="gapOriginal"', html)
        self.assertIn('id="gapListen"', html)
        self.assertIn(">− 0.1 s<", html)
        self.assertIn(">+ 0.1 s<", html)
        self.assertIn(">Original<", html)
        self.assertIn(">Listen<", html)

    def test_actions_post_the_named_total_to_the_join_path(self):
        html = studio_html()
        self.assertIn('"/api/join-pause"', html)
        self.assertIn("target_total_s", html)
        self.assertIn("gapPost(E.gapCtl.cur - 0.1)", html)
        self.assertIn("gapPost(E.gapCtl.cur + 0.1)", html)
        self.assertIn("gapPost(E.gapCtl.rec)", html)

    def test_no_editor_voice_crept_in(self):
        self.assertEqual(studio_html().count("speechSynthesis"), 0)


if __name__ == "__main__":
    unittest.main()
