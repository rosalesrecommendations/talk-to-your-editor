"""Chunk B (0.27.3): the conversation.

Pins, per item:
- item 4: the teach summary partitions what it shows into
  the rules the save will store and the measurements it will
  not, names both counts, and the save leads to the next
  step in his words (page source; the partition is the one
  function both teach flows render through);
- item 5: the status answer's panel shape (stage in plain
  words, about how long is left from the learned usual
  length, where the video lands) and the learned usual
  length itself, a median of this computer's finished runs;
- item 7: the review card's words on the page (Cut it, Keep
  it, Reply, Skip; the old approval word nowhere);
- item 8: the dead-space offer end to end: a note in the
  chat earns a measured answer with a chip, the chip's tap
  trims the join to his pause through the one pause path,
  the result entry's Undo restores it, and a typed "run it"
  runs the same offer with no chip tap;
- item 9: the chat export renders the CURRENT plan: after
  the plan changes, a new export measures the new length,
  and the measured entry carries the new plan's stamp.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import webapp
from talk_to_your_editor.webapp import WebAppConfig, create_server

STUDIO = (ROOT / "src/talk_to_your_editor/templates/studio.html").read_text(
    encoding="utf-8"
)
FFMPEG = shutil.which("ffmpeg")


# ---------------------------------------------------------------------------
# Item 5: the learned usual length and the panel-shaped answer
# ---------------------------------------------------------------------------


class UsualLengthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-b0273-"))
        self.launch = self.tmp / "launch"
        self.launch.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_runs(self, runs) -> None:
        (self.launch / "first-edit-runs.json").write_text(
            json.dumps(runs), encoding="utf-8"
        )

    def test_no_history_is_none(self):
        self.assertIsNone(webapp._first_edit_usual_seconds(self.launch))
        self.write_runs([240.0])
        self.assertIsNone(webapp._first_edit_usual_seconds(self.launch))

    def test_median_of_finished_runs(self):
        self.write_runs([240.0, 300.0, 270.0])
        self.assertEqual(
            webapp._first_edit_usual_seconds(self.launch), 270.0
        )


class StatusPanelShapeTests(unittest.TestCase):
    def test_answer_names_stage_left_and_landing(self):
        run = {
            "stage": "Taking out the retakes…",
            "elapsed_s": 65.0,
            "first_edit_eta_s": 305.0,
        }
        text = webapp._run_status_text(run, "how's it going?")
        self.assertIn("Finding retakes now", text)
        self.assertIn("about 4 minutes left", text)
        self.assertIn("The video lands here when it is done.", text)

    def test_no_history_no_invented_left(self):
        run = {"stage": "Taking out the retakes…", "elapsed_s": 65.0}
        text = webapp._run_status_text(run, "how's it going?")
        self.assertIn("Finding retakes now", text)
        self.assertNotIn("left", text)
        self.assertIn("The video lands here when it is done.", text)


# ---------------------------------------------------------------------------
# Items 4 and 7: the page's own words
# ---------------------------------------------------------------------------


class PageContractTests(unittest.TestCase):
    def test_teach_congruence_and_next_step(self):
        self.assertIn("function partitionFindings(", STUDIO)
        self.assertIn("That is ", STUDIO)
        self.assertIn("Rules I can save from this:", STUDIO)
        self.assertIn(
            "Drop a video and I will edit it in this style.", STUDIO
        )
        # The old summary hid findings behind a slice while
        # the save stored a different set.
        self.assertNotIn("measured.concat(lines).slice(0, 6)", STUDIO)

    def test_review_card_words(self):
        self.assertIn('"Cut it"', STUDIO)
        self.assertIn('"Keep it"', STUDIO)
        self.assertIn("now goes straight to", STUDIO)
        self.assertNotIn("Approve", STUDIO)

    def test_progress_panel_words(self):
        self.assertIn("Editing ", STUDIO)
        self.assertIn(
            "This takes about 5 minutes. Leave this open, grab "
            "a snack. I will tell you when it is your turn.",
            STUDIO,
        )
        self.assertIn("no usual time on this computer yet", STUDIO)
        self.assertIn("Your turn: ", STUDIO)
        self.assertIn("no spots to check, just export.", STUDIO)

    def test_export_panel_words(self):
        self.assertIn("Both shapes · -14 LUFS · subtitles inside", STUDIO)
        self.assertIn("Export both", STUDIO)
        self.assertIn("CapCut opens video, not timelines.", STUDIO)
        self.assertIn("Download video for CapCut", STUDIO)
        self.assertIn("✅ Exported to Downloads:", STUDIO)
        # One Export: the Finish panel's own finished-video
        # controls are gone.
        self.assertNotIn("Export the finished video", STUDIO)
        self.assertNotIn("Download the 9:16 video", STUDIO)
        self.assertNotIn("Download the 16:9 video", STUDIO)


# ---------------------------------------------------------------------------
# Server case shared by items 8 and 9
# ---------------------------------------------------------------------------


class BServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-b0273-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"
        self.job.mkdir(parents=True)
        self.launch = self.tmp / "launch"
        self.config = WebAppConfig(self.job, launch_home=self.launch)
        self.server = create_server(self.config, host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def request(self, path, data=None):
        payload = None
        headers = {}
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path,
            data=payload,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def post(self, path, data):
        return self.request(path, data=data)

    def get(self, path):
        return self.request(path)


# ---------------------------------------------------------------------------
# Item 8: the dead-space offer
# ---------------------------------------------------------------------------

GAP_WORDS = [
    {"word": "one", "start": 0.0, "end": 0.5},
    {"word": "two", "start": 0.6, "end": 1.0},
    {"word": "three", "start": 2.0, "end": 2.5},
    {"word": "four", "start": 2.6, "end": 3.0},
    {"word": "five", "start": 5.0, "end": 5.5},
]

GAP_RANGES = [
    {"start": 0.0, "end": 1.1},
    {"start": 1.5, "end": 3.0},
    {"start": 5.0, "end": 5.5},
]


def write_gap_job(job: Path) -> None:
    transcript = {"engine": "test", "duration": 5.5, "words": GAP_WORDS}
    cuts = {
        "source_duration_s": 5.5,
        "output_duration_s": 3.1,
        "kept_ranges": [dict(r) for r in GAP_RANGES],
        "join_pauses_s": [0.6, 0.0],
    }
    (job / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")


class OfferChipTests(BServerCase):
    def setUp(self) -> None:
        super().setUp()
        write_gap_job(self.job)

    def revised(self) -> dict:
        return json.loads(
            (self.job / "tye-session" / "revised_cuts.json").read_text(
                encoding="utf-8"
            )
        )

    def send(self, text):
        status, data = self.post("/api/thread/message", {"text": text})
        self.assertEqual(status, 200, data)
        return data

    def test_note_earns_a_measured_offer(self):
        data = self.send("There's dead space at 0:01")
        self.assertEqual(data["kind"], "offer")
        entry = data["entry"]
        self.assertIn("Found 0.6 s of silence at 0:01.", entry["text"])
        offer = entry["offer"]
        self.assertEqual(offer["kind"], "trim_pause")
        self.assertEqual(offer["label"], "Trim to your 0.1 s pause · free")

    def test_far_time_gets_an_honest_line_and_no_chip(self):
        data = self.send("There's dead space at 0:30")
        self.assertEqual(data["kind"], "offer")
        self.assertIn("I don't hear a long quiet at 0:30.", data["entry"]["text"])
        self.assertNotIn("offer", data["entry"])

    def test_chip_tap_trims_and_undo_restores(self):
        data = self.send("There's dead space at 0:01")
        entry_id = data["entry"]["id"]
        status, ran = self.post("/api/thread/offer", {"entry_id": entry_id})
        self.assertEqual(status, 200, ran)
        result = ran["entry"]
        self.assertIn("Trimmed.", result["text"])
        self.assertTrue(result.get("action_id"))
        # The join's pause went from 0.6 to the floor-clamped
        # head: tail 0.1 stays, head 0.5 -> 0.08.
        self.assertAlmostEqual(
            self.revised()["join_pauses_s"][0], 0.08, places=3
        )
        # The spent offer cannot run twice.
        status, again = self.post(
            "/api/thread/offer", {"entry_id": entry_id}
        )
        self.assertEqual(status, 400)
        # The result entry's own Undo restores the pause.
        # (Undo returns the session to the pre-trim state,
        # which had no revised plan file at all, so the read
        # is the effective plan, not the file.)
        status, undone = self.post(
            "/api/thread/action",
            {"entry_id": result["id"], "move": "undo"},
        )
        self.assertEqual(status, 200, undone)
        effective = webapp._effective_cuts(self.config)
        self.assertAlmostEqual(
            effective["join_pauses_s"][0], 0.6, places=3
        )

    def test_typed_run_it_runs_the_offer(self):
        self.send("There's dead space at 0:01")
        data = self.send("run it")
        self.assertEqual(data["kind"], "offer")
        self.assertIn("Trimmed.", data["entry"]["text"])
        self.assertAlmostEqual(
            self.revised()["join_pauses_s"][0], 0.08, places=3
        )


# ---------------------------------------------------------------------------
# Item 9: export freshness
# ---------------------------------------------------------------------------


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


EXPORT_WORDS = [
    {"word": f"w{i}", "start": round(i * 0.9, 2),
     "end": round(i * 0.9 + 0.5, 2)}
    for i in range(9)
]


def write_export_job(job: Path) -> None:
    make_source(job / "clip.mp4")
    transcript = {
        "engine": "test",
        "source_file": "clip.mp4",
        "duration": 8.0,
        "words": EXPORT_WORDS,
        "segments": [],
    }
    (job / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (job / "intake.json").write_text(
        json.dumps({"kind": "intake", "source_video": "clip.mp4"}),
        encoding="utf-8",
    )
    cuts = {
        "source_duration_s": 8.0,
        "output_duration_s": 6.0,
        "kept_ranges": [
            {"start": 0.0, "end": 3.0},
            {"start": 4.0, "end": 7.0},
        ],
        "join_pauses_s": [0.1],
    }
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")
    session = job / "tye-session"
    session.mkdir(parents=True, exist_ok=True)
    (session / "revised_cuts.json").write_text(
        json.dumps(cuts), encoding="utf-8"
    )


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class ExportFreshnessTests(BServerCase):
    def setUp(self) -> None:
        super().setUp()
        write_export_job(self.job)

    def export_and_wait(self, aspects):
        status, data = self.post(
            "/api/finish/export", {"aspects": aspects}
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"], data)
        export_id = data["export"]["id"]
        deadline = time.time() + 180
        view = None
        while time.time() < deadline:
            status, data = self.get(
                f"/api/finish/export/status?id={export_id}"
            )
            self.assertEqual(status, 200, data)
            view = data["export"]
            if view["status"] in ("done", "error"):
                break
            time.sleep(0.5)
        self.assertIsNotNone(view)
        self.assertEqual(view["status"], "done", view)
        return view

    def measured(self) -> dict:
        return json.loads(
            (self.job / "tye-session" / "finish_measured.json").read_text(
                encoding="utf-8"
            )
        )

    def test_export_renders_the_current_plan_each_time(self):
        first = self.export_and_wait(["16:9"])
        shape = first["shapes"]["16:9"]
        self.assertEqual(shape["state"], "done")
        self.assertAlmostEqual(shape["duration_s"], 6.0, delta=0.75)
        stamp1 = self.measured()["16:9"]["plan_stamp"]
        # Change the plan: keep only the first piece.
        session = self.job / "tye-session"
        cuts = json.loads((session / "revised_cuts.json").read_text())
        cuts["kept_ranges"] = [{"start": 0.0, "end": 3.0}]
        cuts["join_pauses_s"] = []
        (session / "revised_cuts.json").write_text(
            json.dumps(cuts), encoding="utf-8"
        )
        second = self.export_and_wait(["16:9"])
        shape2 = second["shapes"]["16:9"]
        self.assertAlmostEqual(shape2["duration_s"], 3.0, delta=0.75)
        stamp2 = self.measured()["16:9"]["plan_stamp"]
        self.assertNotEqual(stamp1, stamp2)
        # The exported file itself is the new length and shape.
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "stream=width,height,duration", "-of", "json",
                str(self.job / "tye-session" / "finish-16x9.mp4"),
            ],
            capture_output=True, text=True, check=True,
        ).stdout
        streams = json.loads(out)["streams"]
        video = next(s for s in streams if "width" in s)
        self.assertEqual((video["width"], video["height"]), (640, 360))


if __name__ == "__main__":
    unittest.main()
