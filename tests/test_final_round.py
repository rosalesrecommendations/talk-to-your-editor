"""Filming-day final round: P0 fixes proven keyed-shaped.

P0-1 replays, through the real provider path's guard, the two
proposals Justin's keyed 0.27.3 run actually made on the real
Day 30 Scribe transcript: the cut of the July measurement
sentence (words 468 to 495) and the cut of one side of the
parallel wife/friends line (words 134 to 141). The replay runs
WITH the creator's own direction words present, the exact
configuration that let both cuts through on his Mac.
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

from talk_to_your_editor import webapp  # noqa: E402
from talk_to_your_editor.timeline import Timeline  # noqa: E402
from talk_to_your_editor.verifier import verify_operations  # noqa: E402
from talk_to_your_editor.webapp import WebAppConfig  # noqa: E402

SCRIBE_TX = (
    Path.home() / "workspace" / "user" / "files" / "transcript.json"
)

JULY_START, JULY_END = 468, 495
WIFE_START, WIFE_END = 134, 141


def cut_op(start, end):
    return {
        "type": "cut_words",
        "comment_id": "front-door-retakes",
        "word_start": start,
        "word_end": end,
        "reason": "A model proposal.",
        "confidence": 1.0,
        "model": "muse-spark-1.3",
        "risk_tier": 1,
        # On his keyed run the proposal had passed Wonder review
        # and applied; the replay starts from that same state.
        "wonder_reviewed": True,
    }


@unittest.skipUnless(SCRIBE_TX.is_file(), "Scribe transcript present")
class KeyedShapedReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-final-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"
        self.job.mkdir()
        data = json.loads(SCRIBE_TX.read_text())
        transcript = {
            "engine": data.get("engine", "scribe"),
            "duration": float(data["duration"]),
            "words": data["words"],
            "segments": data.get("segments"),
        }
        cuts = {
            "source_duration_s": transcript["duration"],
            "kept_ranges": [
                {"start": 0.0, "end": transcript["duration"]}
            ],
        }
        (self.job / "transcript.json").write_text(json.dumps(transcript))
        (self.job / "cuts.json").write_text(json.dumps(cuts))
        self.config = WebAppConfig(self.job)
        self.timeline = Timeline(transcript, cuts)
        # His keyed run carried his own typed direction; the
        # guard's old stand-down keyed on exactly this.
        self.named = webapp._named_content_tokens(
            "Keep it tight and keep the story moving, cut the "
            "thyroid part and the wife part"
        )

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def replay(self, start, end):
        ops = webapp.validate_operations([cut_op(start, end)])
        verdicts = verify_operations(ops, self.timeline, None)
        self.assertEqual(verdicts[0].status, "apply", verdicts[0].reasons)
        guard = webapp._provider_cut_guard(
            self.config, named_tokens=self.named
        )
        return guard(verdicts, self.timeline, None)[0]

    def test_the_july_sentence_cut_is_held_with_his_words_present(self):
        verdict = self.replay(JULY_START, JULY_END)
        self.assertEqual(verdict.status, "flag", verdict.reasons)
        self.assertIn("unique_content", verdict.reasons)

    def test_the_wife_line_cut_is_held_with_his_words_present(self):
        verdict = self.replay(WIFE_START, WIFE_END)
        self.assertEqual(verdict.status, "flag", verdict.reasons)
        self.assertTrue(
            "unique_content" in verdict.reasons
            or "your_call_span" in verdict.reasons,
            verdict.reasons,
        )


# ---------------------------------------------------------------------------
# P0-2: one export job renders every shape from one plan
# ---------------------------------------------------------------------------

import subprocess  # noqa: E402

FFMPEG = shutil.which("ffmpeg") is not None


def make_source(path: Path, seconds: float = 8.0) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=640x360:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", str(seconds),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", str(path),
        ],
        check=True,
    )
    return path


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class OnePlanExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-oneplan-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"
        self.job.mkdir()
        make_source(self.job / "clip.mp4")
        words = [
            {"word": f"w{i}", "start": round(i * 0.9, 2),
             "end": round(i * 0.9 + 0.5, 2)}
            for i in range(9)
        ]
        transcript = {
            "engine": "test", "source_file": "clip.mp4",
            "duration": 8.0, "words": words, "segments": [],
        }
        (self.job / "transcript.json").write_text(json.dumps(transcript))
        (self.job / "intake.json").write_text(json.dumps(
            {"kind": "intake", "source_video": "clip.mp4"}
        ))
        cuts = {
            "source_duration_s": 8.0,
            "output_duration_s": 6.0,
            "kept_ranges": [
                {"start": 0.0, "end": 3.0},
                {"start": 4.0, "end": 7.0},
            ],
            "join_pauses_s": [0.1],
        }
        (self.job / "cuts.json").write_text(json.dumps(cuts))
        self.session = self.job / "tye-session"
        self.session.mkdir(parents=True)
        (self.session / "revised_cuts.json").write_text(json.dumps(cuts))
        self.config = WebAppConfig(self.job)

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_both_shapes_come_from_the_jobs_one_plan(self):
        registry = {}
        state = {
            "id": "x", "status": "working",
            "aspects": ["16:9", "9:16"],
            "shapes": {a: {"state": "queued"} for a in ("16:9", "9:16")},
            "error": None,
        }
        registry["x"] = state
        original = webapp._render_finish_shape
        calls = []

        def rewriting(config, aspect, plan=None):
            result = original(config, aspect, plan=plan)
            calls.append(aspect)
            if len(calls) == 1:
                # A revision lands between the two renders, the
                # exact split from Justin's keyed run.
                cuts = json.loads(
                    (self.session / "revised_cuts.json").read_text()
                )
                cuts["kept_ranges"] = [{"start": 0.0, "end": 3.0}]
                cuts["join_pauses_s"] = []
                (self.session / "revised_cuts.json").write_text(
                    json.dumps(cuts)
                )
            return result

        with mock.patch.object(
            webapp, "_render_finish_shape", rewriting
        ):
            webapp._run_finish_export(registry, "x", self.config)
        self.assertEqual(state["status"], "done", state)
        d16 = state["shapes"]["16:9"]["duration_s"]
        d9 = state["shapes"]["9:16"]["duration_s"]
        self.assertAlmostEqual(d16, 6.0, delta=0.75)
        self.assertAlmostEqual(d9, 6.0, delta=0.75)
        self.assertAlmostEqual(d16, d9, delta=0.2)
        measured = json.loads(
            (self.session / "finish_measured.json").read_text()
        )
        self.assertEqual(
            measured["16:9"]["plan_stamp"],
            measured["9:16"]["plan_stamp"],
        )
        self.assertEqual(
            state["plan_stamp"], measured["16:9"]["plan_stamp"]
        )


if __name__ == "__main__":
    unittest.main()
