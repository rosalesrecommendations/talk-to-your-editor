"""Tests for 0.27.1 P1 completion and the run-through fixes.

Covers: the status answer wired into the chat route (item 4:
stage and elapsed from the one run record, moving forward,
never a direction run); the clear-command rule (item 5: a
complaint, a keep-going, and other non-direction words run
nothing and offer nothing paid, while a bare "pause"/"stop"
still commands and a real direction still reaches the gate);
the how-to answer (item 6: review walk plus a real key check,
not a suggestion list); the whole-edit Undo walking past
later entries that did not change the plan, and refusing on
the Done bubble itself when one did (run-through finding 2);
and a stored same-point spot's time re-read from sound when
the job opens (finding 5).
"""

from __future__ import annotations

import json
import os
import shutil
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

from talk_to_your_editor import checkpoints, voice_commands, webapp
from talk_to_your_editor.webapp import WebAppConfig, create_server


def sentence_words(sentences):
    words = []
    t = 0.0
    for sentence in sentences:
        for token in sentence.split():
            words.append(
                {"word": token, "start": round(t, 3),
                 "end": round(t + 0.35, 3)}
            )
            t += 0.45
    return words


WORDS = sentence_words(
    [
        "Hello there this is the first line of the video today.",
        "And here is a second line with more words to edit now.",
        "The water is cold and the breath comes fast at first.",
    ]
)

BASE_CUTS = {
    "source_duration_s": 0.0,
    "kept_ranges": [],
}


def write_job(job: Path, words: list) -> dict:
    job.mkdir(parents=True, exist_ok=True)
    duration = float(words[-1]["end"]) if words else 0.0
    transcript = {"engine": "test", "duration": duration, "words": words}
    cuts = {
        "source_duration_s": duration,
        "kept_ranges": [{"start": 0.0, "end": duration}],
    }
    (job / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")
    return cuts


def plan_without(words, cut_start, cut_end):
    """A plan dict in the job's own shape, one span cut out."""
    first = words[cut_start]
    last = words[cut_end - 1]
    return {
        "source_duration_s": float(words[-1]["end"]),
        "kept_ranges": [
            {"start": 0.0, "end": float(first["start"])},
            {"start": float(last["end"]),
             "end": float(words[-1]["end"])},
        ],
    }


class P1ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-p1-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"
        self.base_cuts = write_job(self.job, WORDS)
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

    def request(self, path, data=None, method=None):
        payload = None
        headers = {}
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path, data=payload, headers=headers,
            method=method or ("POST" if payload is not None else "GET"),
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def post_json(self, path, data):
        status, body = self.request(path, data=data)
        return status, json.loads(body.decode("utf-8"))

    def send(self, text):
        status, data = self.post_json("/api/thread/message", {"text": text})
        self.assertEqual(status, 200, data)
        return data

    def write_run(self, stage, index, started_ago_s):
        self.launch.mkdir(parents=True, exist_ok=True)
        (self.launch / "first_edit_run.json").write_text(
            json.dumps(
                {
                    "status": "working",
                    "stage": stage,
                    "stage_index": index,
                    "stage_total": 5,
                    "started_at_wall": time.time() - started_ago_s,
                }
            ),
            encoding="utf-8",
        )


class StatusAnswerTests(P1ServerCase):
    """Item 4: a status question mid-run is answered from the
    run record; two answers move forward and never disagree."""

    def test_two_status_questions_track_the_run(self):
        self.write_run("Listening to your video", 1, 30)
        with mock.patch.object(webapp, "_run_direction_pass") as run:
            first = self.send("how's it going?")
        run.assert_not_called()
        self.assertEqual(first["kind"], "status")
        text1 = first["entry"]["text"]
        # Panel answer shape (0.27.3, chunk B item 5): the
        # current stage in plain words, then where the video
        # lands. No learned usual length is on this record, so
        # no time-left clause is invented.
        self.assertIn("Listening to your video now", text1)
        self.assertIn("The video lands here when it is done.", text1)

        self.write_run("Taking out the retakes", 2, 75)
        with mock.patch.object(webapp, "_run_direction_pass") as run:
            second = self.send("can I review now?")
        run.assert_not_called()
        self.assertEqual(second["kind"], "status")
        text2 = second["entry"]["text"]
        self.assertTrue(text2.startswith("Not yet. "), text2)
        self.assertIn("Finding retakes now", text2)
        # Forward only: a later stage and a longer clock, and no
        # edit counts anywhere that could contradict the first.
        self.assertNotIn("listening", text2)
        for text in (text1, text2):
            self.assertNotIn("joins", text)
            self.assertNotIn("words out", text)

    def test_update_me_is_a_status_answer_not_a_direction(self):
        self.write_run("Smoothing the joins", 4, 130)
        with mock.patch.object(webapp, "_run_direction_pass") as run:
            data = self.send("I need you to update me")
        run.assert_not_called()
        self.assertEqual(data["kind"], "status")
        self.assertIn("Smoothing the joins now", data["entry"]["text"])
        self.assertIsNone(webapp._load_thread_pending(self.config))

    def test_status_falls_through_with_no_run(self):
        # No run record: the question takes the normal answer
        # path, not the status one.
        data = self.send("how's it going?")
        self.assertNotEqual(data["kind"], "status")
        self.assertNotEqual(data["kind"], "direction")


class ClearCommandTests(P1ServerCase):
    """Item 5: only a clear command runs anything. The key is
    set, so the direction gate is live and would have offered
    a paid run for each of these before."""

    def setUp(self) -> None:
        super().setUp()
        os.environ["MODEL_API_KEY"] = "test-key"

    def test_complaint_runs_nothing(self):
        with mock.patch.object(webapp, "_run_direction_pass") as run:
            data = self.send("this is a long time to wait")
        run.assert_not_called()
        self.assertEqual(data["kind"], "note")
        self.assertIn("Nothing ran", data["entry"]["text"])
        self.assertIsNone(webapp._load_thread_pending(self.config))

    def test_no_keep_going_runs_nothing(self):
        with mock.patch.object(webapp, "_run_direction_pass") as run:
            data = self.send("no keep going")
        run.assert_not_called()
        self.assertEqual(data["kind"], "note")
        self.assertIn("carrying on", data["entry"]["text"])
        self.assertIsNone(webapp._load_thread_pending(self.config))

    def test_plain_words_run_nothing(self):
        with mock.patch.object(webapp, "_run_direction_pass") as run:
            data = self.send("the weather is nice today")
        run.assert_not_called()
        self.assertEqual(data["kind"], "note")
        self.assertIsNone(webapp._load_thread_pending(self.config))

    def test_bare_pause_and_stop_still_command(self):
        for word in ("pause", "stop"):
            data = self.send(word)
            self.assertEqual(data["kind"], "command", (word, data))
            self.assertEqual(data["command"], "pause")

    def test_real_direction_still_reaches_the_gate(self):
        with mock.patch.object(webapp, "_run_direction_pass") as run:
            data = self.send(
                "Tighten the first minute, keep the water entry."
            )
        run.assert_not_called()
        self.assertEqual(data["kind"], "needs_confirmation")


class VoiceMatcherTests(unittest.TestCase):
    """The matcher half of item 5: a command word buried in a
    longer message is not a command; the bare word still is."""

    def test_bare_stop_and_pause(self):
        for word in ("stop", "pause"):
            result = voice_commands.match_voice_command(word)
            self.assertEqual(result["decision"], "command", word)
            self.assertEqual(result["command"], "pause")

    def test_buried_command_words_do_not_count(self):
        for text in (
            "this is a long time to wait",
            "no keep going",
            "pause the video when it gets loud",
            "the part where he says cut the retakes is loud",
        ):
            result = voice_commands.match_voice_command(text)
            self.assertEqual(result["decision"], "none", text)

    def test_clear_commands_still_match(self):
        result = voice_commands.match_voice_command(
            "please remove the retakes from the video"
        )
        self.assertEqual(result["decision"], "command")
        self.assertEqual(result["command"], "remove_retakes")


class HowtoAnswerTests(P1ServerCase):
    """Item 6: the question asked is the question answered."""

    def test_review_and_keys_both_answered(self):
        with mock.patch.object(webapp, "_run_direction_pass") as run:
            data = self.send("how do I review the edit? or check api keys?")
        run.assert_not_called()
        self.assertEqual(data["kind"], "record")
        entry = data["entry"]
        lines = [entry["text"]] + list(entry.get("lines") or [])
        joined = "\n".join(lines)
        self.assertTrue(lines[0].startswith("To review:"), joined)
        # The how-to names the review flow as it now is
        # (0.27.3, chunk B item 7): cards with Cut it, Keep
        # it, Reply, Skip; the word Approve is gone.
        self.assertIn("Review the spots", joined)
        self.assertIn("Cut it, Keep it, Reply, or Skip", joined)
        self.assertNotIn("Approve", joined)
        self.assertIn("Your Meta key: none is saved", joined)
        # Not a suggestion list of edits he could make.
        self.assertNotIn("idea", joined.lower())


class UndoWalkTests(P1ServerCase):
    """Finding 2: Undo past later entries that left the plan
    alone proceeds and keeps their work; past an entry that
    changed the plan it refuses, on the Done bubble itself."""

    def _seed_done_entry(self):
        session = self.config.session_dir
        session.mkdir(parents=True, exist_ok=True)
        checkpoints.begin(self.config)
        plan_a = plan_without(WORDS, 4, 7)
        (session / "revised_cuts.json").write_text(
            json.dumps(plan_a), encoding="utf-8"
        )
        checkpoints.commit(self.config, "First edit")
        entry = webapp._append_thread(
            self.config,
            {
                "kind": "editor",
                "status": "settled",
                "text": "Done. Your edit is ready.",
                "chat_done": {"video_url": "/edited.mp4"},
                "action_id": "checkpoint:1",
                "action_label": "First edit",
            },
        )
        return plan_a, entry

    def test_undo_walks_past_a_told_rule(self):
        plan_a, entry = self._seed_done_entry()
        session = self.config.session_dir
        rules = [{"rule_id": "r1", "statement": "Keep the water entry."}]
        (session / "creator_rules.json").write_text(
            json.dumps(rules), encoding="utf-8"
        )
        checkpoints.commit(self.config, "Creator's own rule saved")
        self.assertEqual(
            webapp._effective_cuts(self.config), plan_a
        )
        status, data = self.post_json(
            "/api/thread/action", {"entry_id": entry["id"], "move": "undo"}
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["moved"], data)
        self.assertFalse(data["fallback"], data)
        # The plan is back to the base cuts (the pre-edit state)...
        self.assertEqual(
            webapp._effective_cuts(self.config), self.base_cuts
        )
        # ...and the later work, the told rule, is still there.
        saved_rules = json.loads(
            (session / "creator_rules.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved_rules, rules)
        updated = webapp._find_thread_entry(self.config, entry["id"])
        self.assertTrue(updated["undone"])
        self.assertFalse(updated.get("undo_note"))
        # Redo walks the same road back: the plan returns, and
        # the told rule STILL survives (a plain redo restore
        # would roll the session files back to the edit's own
        # step and take the rule with it).
        status, data = self.post_json(
            "/api/thread/action", {"entry_id": entry["id"], "move": "redo"}
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["moved"], data)
        self.assertFalse(data["fallback"], data)
        self.assertEqual(webapp._effective_cuts(self.config), plan_a)
        saved_rules = json.loads(
            (session / "creator_rules.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved_rules, rules)
        updated = webapp._find_thread_entry(self.config, entry["id"])
        self.assertFalse(updated["undone"])

    def test_undo_refuses_on_the_bubble_when_the_plan_changed(self):
        _plan_a, entry = self._seed_done_entry()
        session = self.config.session_dir
        plan_b = plan_without(WORDS, 10, 14)
        (session / "revised_cuts.json").write_text(
            json.dumps(plan_b), encoding="utf-8"
        )
        checkpoints.commit(self.config, "Direction run")
        status, data = self.post_json(
            "/api/thread/action", {"entry_id": entry["id"], "move": "undo"}
        )
        self.assertEqual(status, 200, data)
        self.assertFalse(data["moved"], data)
        self.assertTrue(data["fallback"], data)
        # The plan is untouched, and the refusal is written on
        # the Done entry itself, not only in a separate message.
        self.assertEqual(webapp._effective_cuts(self.config), plan_b)
        updated = webapp._find_thread_entry(self.config, entry["id"])
        self.assertFalse(updated.get("undone"))
        self.assertIn("Later work came after it", updated["undo_note"])
        self.assertIn(
            "Later work came after it", data["entry"]["undo_note"]
        )


class StoredSpotTimeTests(unittest.TestCase):
    """Finding 5: a stored same-point spot's named time is
    re-read from measured sound when the job opens."""

    def test_stale_time_heals_on_open(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-p1-spot-"))
        try:
            job = tmp / "job"
            words = []
            t = 0.0
            for i in range(40):
                words.append(
                    {"word": f"w{i}", "start": round(t, 3),
                     "end": round(t + 0.4, 3)}
                )
                t += 0.5
            write_job(job, words)
            config = WebAppConfig(job, launch_home=tmp / "launch")
            session = config.session_dir
            session.mkdir(parents=True, exist_ok=True)
            record = {
                "gold_id": "gold-2-5-same_point",
                "kind": "same_point",
                "source": "same_point",
                "status": "open",
                "word_start": 2,
                "word_end": 5,
                "again_word_start": 20,
                "again_word_end": 23,
                "again_output_s": 999.0,
                "reason": "This makes the same point as 16:39. Cut it?",
                "operation": None,
            }
            (session / "gold.json").write_text(
                json.dumps({"spans": [record]}), encoding="utf-8"
            )
            views = webapp._open_gold_spans(config)
            self.assertEqual(len(views), 1)
            # Word 20 sounds at 10.0 to 10.4 in a plan that keeps
            # everything: its moment in the edit is about 10.2.
            self.assertAlmostEqual(
                views[0]["again_output_s"], 10.2, delta=0.3
            )
            self.assertIn("0:10", views[0]["reason"])
            stored = json.loads(
                (session / "gold.json").read_text(encoding="utf-8")
            )
            self.assertAlmostEqual(
                stored["spans"][0]["again_output_s"], 10.2, delta=0.3
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
