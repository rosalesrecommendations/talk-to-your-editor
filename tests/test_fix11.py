"""0.27, fix 11: three small items from Justin's 0.26.0 Mac test.

1. The undone Done bubble is one line ("Undone. Back to the
   video as brought in (M:SS).") plus the Redo link, nothing
   else; the server settles the undone payload with the
   brought-in video's probed full length.
2. "Why did you cut that?" answers state what the record
   shows: the old fragment "Removed the words this note
   pointed at from the finished edit." is gone from the
   templates and re-voiced wherever a stored audit still
   carries it; and a bare "that" answers about the cut
   nearest the playhead the page sends, not the most recent
   cut, asking (listing real cuts) when no position arrives
   or no cut is near.
3. The teach offer shows only in the pre-edit state (its
   placement machinery is pinned here as page text; the
   browser proof in v027-work/shots-fix11 exercises it live).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from talk_to_your_editor import after_review, verifier, webapp  # noqa: E402
from talk_to_your_editor.webapp import WebAppConfig  # noqa: E402
from test_undo_run import RunUndoCase  # noqa: E402

STUDIO = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "studio.html"
).read_text(encoding="utf-8")

OLD_SENTENCE = (
    "Removed the words this note pointed at from the finished edit."
)
NEW_SENTENCE = (
    "The record shows the words this note pointed at were "
    "removed from the finished edit."
)
OLD_SWAP = "Swapped the take used here for the other take, as this note asked."
NEW_SWAP = (
    "The record shows the take used here was swapped for the "
    "other take, as this note asked."
)


def tiny_job(tmp: Path, with_legacy_audit: bool = False) -> WebAppConfig:
    """A 60-second job with two cuts far apart: words 10-14
    (out at about 0:05) and words 100-104 (out at about 0:47)."""
    job = tmp / "job"
    job.mkdir()
    words = [
        {
            "word": f"tok{i:03d}",
            "start": round(i * 0.5, 3),
            "end": round(i * 0.5 + 0.4, 3),
        }
        for i in range(120)
    ]
    (job / "transcript.json").write_text(
        json.dumps({"words": words, "duration": 60.0}), encoding="utf-8"
    )
    (job / "cuts.json").write_text(
        json.dumps({
            "kept_ranges": [{"start": 0.0, "end": 60.0}],
            "source_duration_s": 60.0,
        }),
        encoding="utf-8",
    )
    session = job / "tye-session"
    session.mkdir()
    (session / "revised_cuts.json").write_text(
        json.dumps({
            "kept_ranges": [
                {"start": 0.0, "end": 5.0},
                {"start": 7.5, "end": 50.0},
                {"start": 52.5, "end": 60.0},
            ],
            "source_duration_s": 60.0,
        }),
        encoding="utf-8",
    )
    if with_legacy_audit:
        (session / "operations_audit.json").write_text(
            json.dumps({
                "operations": [{
                    "type": "cut_words",
                    "status": "applied",
                    "word_start": 10,
                    "word_end": 15,
                    "comment_id": "note-1",
                    "plain_action": OLD_SENTENCE,
                }]
            }),
            encoding="utf-8",
        )
    (job / "review.mp4").write_bytes(b"fake")
    return WebAppConfig(job)


class NearestPlayheadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-fix11-"))
        self.config = tiny_job(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_nearest_cut_span_picks_by_output_place(self):
        spans = webapp._edit_spans(self.config)
        self.assertEqual(len(spans), 2)
        near_b = webapp._nearest_cut_span(self.config, spans, 47.7)
        self.assertIsNotNone(near_b)
        span, at = near_b
        self.assertEqual(span["word_start"], 100)
        self.assertAlmostEqual(at, 47.7, delta=1.0)
        near_a = webapp._nearest_cut_span(self.config, spans, 5.2)
        self.assertIsNotNone(near_a)
        self.assertEqual(near_a[0]["word_start"], 10)

    def test_nearest_cut_span_refuses_far_or_missing_positions(self):
        spans = webapp._edit_spans(self.config)
        self.assertIsNone(webapp._nearest_cut_span(self.config, spans, None))
        self.assertIsNone(webapp._nearest_cut_span(self.config, spans, True))
        self.assertIsNone(webapp._nearest_cut_span(self.config, spans, -1))
        # Parked midway between the two cuts, far from both.
        self.assertIsNone(webapp._nearest_cut_span(self.config, spans, 26.0))

    def test_bare_why_answers_about_the_cut_nearest_the_playhead(self):
        answer = webapp._record_answer(
            self.config, "Why did you cut that?", playhead_s=47.7
        )
        self.assertIn("nearest where you are paused", answer["text"])
        self.assertIn("tok100", answer["text"])
        self.assertNotIn("most recent cut", answer["text"])
        # And near the other cut, the answer names that one:
        # the pick follows the playhead, not the record's order.
        answer = webapp._record_answer(
            self.config, "Why did you cut that?", playhead_s=5.2
        )
        self.assertIn("tok010", answer["text"])
        self.assertNotIn("tok100", answer["text"])

    def test_bare_why_without_a_position_asks_listing_real_cuts(self):
        answer = webapp._record_answer(self.config, "Why did you cut that?")
        self.assertIn("cannot tell which cut", answer["text"])
        self.assertIn("tok010", answer["text"])
        self.assertNotIn("most recent cut", answer["text"])

    def test_bare_why_with_no_cut_near_asks(self):
        answer = webapp._record_answer(
            self.config, "Why did you cut that?", playhead_s=26.0
        )
        self.assertIn("cannot tell which cut", answer["text"])

    def test_playhead_rides_the_thread_message(self):
        import threading

        server = webapp.create_server(
            self.config, host="127.0.0.1", port=0
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            payload = json.dumps({
                "text": "Why did you cut that?",
                "playhead_s": 47.7,
            }).encode()
            req = urllib.request.Request(
                base + "/api/thread/message",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode() or "{}")
            self.assertEqual(data["kind"], "record")
            self.assertIn("tok100", data["entry"]["text"])
        finally:
            server.shutdown()
            server.server_close()


class RemovedSentenceTests(unittest.TestCase):
    def test_templates_carry_the_record_voice(self):
        self.assertEqual(
            verifier.PLAIN_ACTION_BY_TYPE["cut_words"], NEW_SENTENCE
        )
        self.assertEqual(
            verifier.PLAIN_ACTION_BY_TYPE["replace_take"], NEW_SWAP
        )
        self.assertEqual(
            after_review._OPERATION_ACTION_TEMPLATES["cut_words"],
            NEW_SENTENCE,
        )
        self.assertEqual(
            after_review._OPERATION_ACTION_TEMPLATES["replace_take"],
            NEW_SWAP,
        )

    def test_stored_legacy_wording_is_revoiced(self):
        self.assertEqual(webapp._record_voiced_action(OLD_SENTENCE), NEW_SENTENCE)
        self.assertEqual(webapp._record_voiced_action(OLD_SWAP), NEW_SWAP)
        self.assertEqual(
            after_review._plain_action(
                {"plain_action": OLD_SENTENCE}, "applied"
            ),
            NEW_SENTENCE,
        )

    def test_span_reason_from_a_legacy_audit_is_revoiced(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-fix11-legacy-"))
        try:
            config = tiny_job(tmp, with_legacy_audit=True)
            spans = webapp._edit_spans(config)
            first = [s for s in spans if s["word_start"] == 10][0]
            self.assertEqual(first["source"], "direction")
            self.assertEqual(first["reason"], NEW_SENTENCE)
            answer = webapp._record_answer(
                config, "Why did you cut that?", playhead_s=5.2
            )
            self.assertIn(NEW_SENTENCE, answer["text"])
            self.assertNotIn(OLD_SENTENCE, answer["text"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class UndonePayloadTests(RunUndoCase):
    """The settle after an undo names the brought-in video's
    full length, probed from the restored original file."""

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg needed")
    def test_undo_settles_brought_in_duration(self):
        from talk_to_your_editor import checkpoints
        from test_v18_actions import apply_cut

        session = self.config.session_dir
        session.mkdir(parents=True, exist_ok=True)
        clip = self.job / "clip.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:d=3:r=5",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(clip),
            ],
            check=True, capture_output=True, timeout=60,
        )
        self.store_progress()
        checkpoints.begin(self.config)
        apply_cut(self.config, 0, 4)
        checkpoints.commit(self.config, "First edit")
        (session / webapp.SESSION_VIDEO_NAME).write_bytes(b"EDITED-VIDEO")
        webapp._record_splice_built(self.config)
        self.store_done(words_cut=4)
        self.move("chatrun-7", "undo")
        payload = self.stored_done()["chat_done"]
        self.assertIn("brought_in_duration_s", payload)
        self.assertAlmostEqual(payload["brought_in_duration_s"], 3.0, delta=0.6)


class StudioShapeTests(unittest.TestCase):
    """The undone bubble and the teach-offer placement, as the
    page carries them (the browser proof exercises both live)."""

    def test_undone_bubble_is_one_line(self):
        self.assertIn(
            '"Undone. Back to the video as brought in ("', STUDIO
        )
        self.assertIn("p.brought_in_duration_s || p.output_duration_s", STUDIO)
        # The undone branch sits at the top of fillDoneBubble,
        # ahead of the counts and the joins line it suppresses.
        fill = STUDIO.index("function fillDoneBubble")
        undone = STUDIO.index("done-undone-line", fill)
        joins = STUDIO.index("joins checked", fill)
        self.assertLess(undone, joins)

    def test_teach_offer_state_machinery(self):
        self.assertIn("window.__tyeSetEditState", STUDIO)
        self.assertIn('classList.add("teach-offer")', STUDIO)
        self.assertIn('editState !== "pre"', STUDIO)
        # The state setters are wired to the run, the stored
        # progress render, the stored Done render, and a new
        # video coming in.
        self.assertIn('__tyeSetEditState("running")', STUDIO)
        self.assertIn('__tyeSetEditState("done")', STUDIO)
        self.assertIn('__tyeSetEditState("pre")', STUDIO)
        self.assertIn('__tyeSetEditState("running", entryTsMs(en))', STUDIO)
        self.assertIn('__tyeSetEditState("done", entryTsMs(en))', STUDIO)

    def test_thread_send_carries_the_playhead(self):
        self.assertIn("body.playhead_s = playhead", STUDIO)
        self.assertIn("function chatPlayheadS()", STUDIO)

    def test_no_editor_voice(self):
        self.assertEqual(STUDIO.count("speechSynthesis"), 0)


if __name__ == "__main__":
    unittest.main()
