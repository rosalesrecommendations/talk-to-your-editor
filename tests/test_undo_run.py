"""0.26: the Done bubble's Undo comes back.

Fix 3 removed the long recap bubble, and the per-action Undo
for front-door and first-edit runs rode on it and was lost.
The restore, in the shape Justin set ("nothing is final until
I say so"):

- a chat run's Done entry carries the run's own checkpoint as
  its action (the same contract direction runs have always
  carried), attached by the server when the Done payload is
  stored, so Undo works from the stored bubble after a reload;
- the run's rendered video is saved beside the entry, a
  "before" copy when the run's progress entry is first written
  and an "after" copy when its Done payload lands, so undo and
  redo restore plan and video from saved state only: no model
  run, no re-render;
- undoing a first edit returns the app to the untouched video;
  undoing a later run returns it to the edit that stood before
  that run;
- after a move, the stored Done payload is refreshed from
  _edit_counts on the restored plan (the one count), never the
  numbers from before the move;
- per-action Undo for chat-directed changes keeps working.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from talk_to_your_editor import checkpoints, webapp  # noqa: E402
from talk_to_your_editor.timeline import Timeline  # noqa: E402
from talk_to_your_editor.webapp import SESSION_VIDEO_NAME  # noqa: E402
from test_v18_actions import ActionsServerCase, apply_cut  # noqa: E402


def done_payload(**overrides):
    payload = {
        "intake_id": "7",
        "took_s": 31,
        "cents": 12,
        "output_duration_s": 12.0,
        "retakes": 0,
        "retakes_set_aside": 0,
        "silences": 0,
        "fillers": 0,
        "notes": 0,
        "joins_checked": 0,
        "joins_smooth": 0,
        "joins_failed": 0,
        "words_cut": 0,
        "your_call": [],
        "review_count": 0,
    }
    payload.update(overrides)
    return payload


class RunUndoCase(ActionsServerCase):
    def store_progress(self, run_id="chatrun-7", started=111):
        status, data = self.post_json("/api/thread", {
            "id": run_id,
            "kind": "editor",
            "status": "open",
            "text": "Making your edit…",
            "chat_progress": {
                "intake_id": run_id.rsplit("-", 1)[-1],
                "started_at_ms": started,
                "stage": "Words",
            },
        })
        self.assertEqual(status, 200, data)
        return data

    def store_done(self, run_id="chatrun-7", **overrides):
        status, data = self.post_json("/api/thread", {
            "id": run_id,
            "kind": "editor",
            "status": "settled",
            "text": "✅ Done",
            "chat_done": done_payload(**overrides),
        })
        self.assertEqual(status, 200, data)
        return data["entry"]

    def served_video(self):
        status, body = self.request("/revised-review.mp4")
        self.assertEqual(status, 200)
        return body

    def stored_done(self, run_id="chatrun-7"):
        status, view = self.get_json("/api/thread")
        self.assertEqual(status, 200)
        for entry in view["entries"]:
            if entry["id"] == run_id:
                return entry
        self.fail(f"no stored entry {run_id}")

    def move(self, run_id, move):
        status, data = self.post_json(
            "/api/thread/action", {"entry_id": run_id, "move": move}
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"], data)
        self.assertTrue(data["moved"], data)
        self.assertFalse(data["fallback"], data)
        return data


class FirstEditRunUndoTests(RunUndoCase):
    def test_undo_returns_to_the_untouched_video_and_redo_restores(self):
        session = self.config.session_dir
        session.mkdir(parents=True, exist_ok=True)
        (self.job / "clip.mp4").write_bytes(b"ORIGINAL-VIDEO")
        # The run starts: its progress entry is stored while no
        # edited video exists yet.
        self.store_progress()
        # The run lands: the pre-run state is committed, a plan
        # with four words cut is written, the splice is built.
        checkpoints.begin(self.config)
        apply_cut(self.config, 0, 4)
        checkpoints.commit(self.config, "First edit")
        (session / SESSION_VIDEO_NAME).write_bytes(b"EDITED-VIDEO")
        webapp._record_splice_built(self.config)
        entry = self.store_done(words_cut=4)
        # The Done entry carries the run's checkpoint as its
        # action, so its bubble can offer Undo after a reload.
        self.assertEqual(entry["action_id"], "checkpoint:1")
        self.assertEqual(entry["action_label"], "First edit")
        self.assertFalse(entry["undone"])
        self.assertEqual(self.served_video(), b"EDITED-VIDEO")
        self.assertEqual(webapp._edit_counts(self.config)["words_cut"], 4)

        # Undo: plan and served video return to before the run.
        data = self.move("chatrun-7", "undo")
        self.assertTrue(data["entry"]["undone"])
        self.assertFalse((session / "revised_cuts.json").exists())
        self.assertEqual(self.word_status(0), "kept")
        # No edited video exists any more, and the original is
        # what the app serves, exactly as before the run.
        status, _ = self.request("/revised-review.mp4")
        self.assertEqual(status, 404)
        status, body = self.request("/video/clip.mp4")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"ORIGINAL-VIDEO")
        # The stored payload now reads the restored plan's
        # counts, not the numbers from before the undo.
        done = self.stored_done()
        self.assertTrue(done["undone"])
        payload = done["chat_done"]
        self.assertEqual(payload["words_cut"], 0)
        self.assertEqual(payload["review_count"], 0)
        self.assertEqual(payload["video_src"], "/video/clip.mp4")
        transcript = webapp._load_json(
            str(self.job / "transcript.json")
        )
        full = Timeline(transcript, webapp._effective_cuts(self.config))
        self.assertAlmostEqual(
            payload["output_duration_s"], round(full.output_duration, 4)
        )

        # Redo: the edit is back, plan, video and counts alike.
        data = self.move("chatrun-7", "redo")
        self.assertFalse(data["entry"]["undone"])
        self.assertEqual(self.word_status(0), "cut")
        self.assertEqual(self.served_video(), b"EDITED-VIDEO")
        done = self.stored_done()
        self.assertFalse(done["undone"])
        payload = done["chat_done"]
        self.assertEqual(payload["words_cut"], 4)
        self.assertEqual(
            payload["video_src"], "/revised-review.mp4"
        )
        edited = Timeline(transcript, webapp._effective_cuts(self.config))
        self.assertAlmostEqual(
            payload["output_duration_s"],
            round(edited.output_duration, 4),
        )


class LaterRunUndoTests(RunUndoCase):
    def test_undo_restores_the_edit_that_stood_before_the_run(self):
        session = self.config.session_dir
        session.mkdir(parents=True, exist_ok=True)
        (self.job / "clip.mp4").write_bytes(b"ORIGINAL-VIDEO")
        # An earlier edit already stands: four words cut, its
        # own checkpoint and its own rendered video.
        checkpoints.begin(self.config)
        apply_cut(self.config, 0, 4)
        checkpoints.commit(self.config, "First edit")
        (session / SESSION_VIDEO_NAME).write_bytes(b"EDIT-A")
        webapp._record_splice_built(self.config)
        # A second run starts from that edit and cuts four more.
        self.store_progress(run_id="chatrun-9", started=222)
        apply_cut(self.config, 10, 14)
        checkpoints.commit(self.config, "Front door run")
        (session / SESSION_VIDEO_NAME).write_bytes(b"EDIT-B")
        webapp._record_splice_built(self.config)
        entry = self.store_done(run_id="chatrun-9", words_cut=8)
        self.assertEqual(entry["action_id"], "checkpoint:2")
        self.assertEqual(entry["action_label"], "Front door run")
        self.assertEqual(self.served_video(), b"EDIT-B")

        # Undo returns to the earlier edit, not to untouched.
        self.move("chatrun-9", "undo")
        self.assertEqual(self.word_status(0), "cut")
        self.assertEqual(self.word_status(10), "kept")
        self.assertEqual(self.served_video(), b"EDIT-A")
        payload = self.stored_done("chatrun-9")["chat_done"]
        self.assertEqual(payload["words_cut"], 4)
        self.assertEqual(payload["video_src"], "/revised-review.mp4")

        # Redo puts the second run's edit back.
        self.move("chatrun-9", "redo")
        self.assertEqual(self.word_status(10), "cut")
        self.assertEqual(self.served_video(), b"EDIT-B")
        payload = self.stored_done("chatrun-9")["chat_done"]
        self.assertEqual(payload["words_cut"], 8)


class RunUndoHonestyTests(RunUndoCase):
    def test_done_entry_without_a_run_checkpoint_gets_no_action(self):
        # A Done payload stored with no run checkpoint behind it
        # (nothing was committed) carries no action: the bubble
        # must not offer an Undo that has nothing saved to
        # restore from.
        entry = self.store_done(run_id="chatrun-3")
        self.assertNotIn("action_id", entry)
        status, data = self.post_json(
            "/api/thread/action",
            {"entry_id": "chatrun-3", "move": "undo"},
        )
        self.assertEqual(status, 400)
        self.assertIn("nothing to undo", data["error"])


class DirectionUndoStillWorksTests(RunUndoCase):
    def test_direction_run_undo_and_redo_unchanged(self):
        entry = self.make_action()
        self.assertEqual(self.word_status(0), "cut")
        status, data = self.post_json(
            "/api/thread/action",
            {"entry_id": entry["id"], "move": "undo"},
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual(self.word_status(0), "kept")
        status, data = self.post_json(
            "/api/thread/action",
            {"entry_id": entry["id"], "move": "redo"},
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual(self.word_status(0), "cut")


if __name__ == "__main__":
    unittest.main()
