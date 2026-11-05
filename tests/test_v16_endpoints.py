"""v0.16 Batch B: the manual timeline endpoints and the render.

The server half of the timeline round: the six operations land
in the session's revised cuts under one history checkpoint each
(so undo and redo cover them exactly like a word click), a
refusal writes nothing, the /api/job payload tells the band
what it needs about each piece, and the splice builder fades a
join by the length stored on the piece it plays into.

The fixture matches test_v16_manual_edits: ten words, pieces
A [0,5], S [6,7] (special), B [8,13], C [16,19]; word 5 is
protected. Everything is offline; no provider is called.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import checkpoints, intake_build, webapp
from talk_to_your_editor.webapp import WebAppConfig


def make_words():
    return [
        {"word": f"w{i}", "start": float(2 * i), "end": float(2 * i + 1)}
        for i in range(10)
    ]


def write_job(job: Path) -> None:
    job.mkdir(parents=True, exist_ok=True)
    transcript = {"engine": "test", "duration": 20.0, "words": make_words()}
    cuts = {
        "source_duration_s": 20.0,
        "output_duration_s": 14.0,
        "kept_ranges": [
            {"start": 0.0, "end": 5.0},
            {"start": 6.0, "end": 7.0, "insert_kind": "signature_breath"},
            {"start": 8.0, "end": 13.0},
            {"start": 16.0, "end": 19.0},
        ],
        "join_pauses_s": [1.0, 1.0, 3.0],
    }
    (job / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")
    (job / "profile.json").write_text(
        json.dumps(
            {"protected_spans": [{"word_start": 5, "word_end": 6, "label": "keep"}]}
        ),
        encoding="utf-8",
    )


def spans(cuts):
    return [(r["start"], r["end"]) for r in cuts["kept_ranges"]]


class TimelineEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v16-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        self.job = self.tmp / "job"
        write_job(self.job)

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self) -> WebAppConfig:
        return WebAppConfig(job_dir=self.job)

    def revised_path(self) -> Path:
        return self.job / "tye-session" / "revised_cuts.json"

    def revised(self) -> dict:
        return json.loads(self.revised_path().read_text(encoding="utf-8"))

    def test_split_lands_in_the_session_plan(self):
        result = webapp._timeline_split(self.config(), 8.0)
        self.assertTrue(result["changed"])
        self.assertIn("Split", result["message"])
        self.assertEqual(len(self.revised()["kept_ranges"]), 5)

    def test_a_refusal_writes_nothing(self):
        result = webapp._timeline_split(self.config(), 5.5)
        self.assertFalse(result["changed"])
        self.assertIn("special insert", result["message"])
        self.assertFalse(self.revised_path().exists())

    def test_split_is_one_undo_and_redo_step(self):
        config = self.config()
        webapp._timeline_split(config, 8.0)
        labels = [c["label"] for c in checkpoints.state(config)["checkpoints"]]
        self.assertIn("Timeline piece split", labels)
        checkpoints.undo(config)
        self.assertEqual(len(webapp._effective_cuts(config)["kept_ranges"]), 4)
        checkpoints.redo(config)
        self.assertEqual(len(webapp._effective_cuts(config)["kept_ranges"]), 5)

    def test_trim_delete_move_paste_fade_round_trip(self):
        config = self.config()
        # Trim B's end out over the cut word 7.
        result = webapp._timeline_trim(config, 2, "end", 15.5)
        self.assertTrue(result["changed"])
        self.assertIn((8.0, 15.5), spans(self.revised()))
        # Fade the join into C.
        result = webapp._timeline_fade(config, 2, 0.25)
        self.assertTrue(result["changed"])
        self.assertEqual(self.revised()["kept_ranges"][3].get("fade_in_s"), 0.25)
        # Move C before B.
        result = webapp._timeline_move(config, 3, 2)
        self.assertTrue(result["changed"])
        self.assertEqual(spans(self.revised())[2], (16.0, 19.0))
        # Delete C again (now at index 2).
        result = webapp._timeline_delete(config, 2)
        self.assertTrue(result["changed"])
        self.assertEqual(len(self.revised()["kept_ranges"]), 3)
        # Paste word 7's span at the playhead (output 8.0, inside B).
        result = webapp._timeline_paste(
            config, [{"start": 14.0, "end": 15.0}], 8.0
        )
        self.assertTrue(result["changed"])
        self.assertIn((14.0, 15.0), spans(self.revised()))

    def test_delete_protected_piece_refuses_in_plain_words(self):
        result = webapp._timeline_delete(self.config(), 2)
        self.assertFalse(result["changed"])
        self.assertIn("protected", result["message"])

    def test_job_payload_names_each_piece_for_the_band(self):
        summary = webapp._job_summary(self.config())
        pieces = summary["timeline_pieces"]
        self.assertEqual(len(pieces), 4)
        self.assertEqual(pieces[2]["range_index"], 2)
        self.assertEqual(pieces[2]["source_start"], 8.0)
        self.assertEqual(pieces[2]["source_end"], 13.0)
        self.assertFalse(pieces[2]["special"])
        self.assertTrue(pieces[1]["special"])
        self.assertIsNone(pieces[2]["fade_in_s"])


class SpliceFadeTests(unittest.TestCase):
    RANGES = [
        {"start": 0.0, "end": 5.0},
        {"start": 6.0, "end": 9.0},
        {"start": 10.0, "end": 14.0},
    ]

    def test_no_fades_anywhere_is_the_plain_concat(self):
        graph = intake_build.splice_filter_graph(self.RANGES, True, 0.0)
        self.assertNotIn("acrossfade", graph)
        self.assertIn("concat=n=3:v=1:a=1[outv][outa]", graph)

    def test_the_global_choice_still_fades_every_join(self):
        graph = intake_build.splice_filter_graph(self.RANGES, True, 0.04)
        self.assertEqual(graph.count("acrossfade=d=0.0400"), 2)

    def test_a_piece_fade_wins_at_its_own_join_only(self):
        ranges = [dict(r) for r in self.RANGES]
        ranges[2]["fade_in_s"] = 0.25
        graph = intake_build.splice_filter_graph(ranges, True, 0.0)
        self.assertIn("acrossfade=d=0.2500", graph)
        self.assertNotIn("acrossfade=d=0.0400", graph)
        # The unfaded join falls back to a plain concat of the two.
        self.assertIn("concat=n=2:v=0:a=1", graph)

    def test_a_piece_fade_overrides_the_global_at_its_join(self):
        ranges = [dict(r) for r in self.RANGES]
        ranges[1]["fade_in_s"] = 0.25
        graph = intake_build.splice_filter_graph(ranges, True, 0.04)
        self.assertIn("acrossfade=d=0.2500", graph)
        self.assertIn("acrossfade=d=0.0400", graph)


if __name__ == "__main__":
    unittest.main()
