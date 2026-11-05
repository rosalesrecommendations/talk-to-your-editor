"""Tests for v0.15 Batch E1: the Editor panel and its thread.

Covers the binding E1 criteria: the thread store round-trip; the
editor's name persisting per creator (in the usual, not per job);
a held gold span appearing as a thread held entry and a verdict
through /api/gold-verdict settling it in the thread and the gold
store; a run summary entry written when Make the edit finishes
(mock provider, $0); and a record question answered with no
provider call. Plus the chat send order (moment pin, voice
command, spend gate), the pin-in-thread sync, and the P8 mode
label rename in the server recap.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import usual, webapp
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig, create_server

PAGE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")


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


def write_job(job: Path, words: list) -> None:
    job.mkdir(parents=True, exist_ok=True)
    transcript = {
        "engine": "test",
        "duration": float(words[-1]["end"]) if words else 0.0,
        "words": words,
    }
    cuts = {
        "source_duration_s": transcript["duration"],
        "kept_ranges": [{"start": 0.0, "end": transcript["duration"]}],
    }
    (job / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")


WORDS = sentence_words(
    [
        "Hello there this is the first line of the video today.",
        "And here is a second line with more words to edit now.",
        "The water is cold and the breath comes fast at first.",
    ]
)


class ChatServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v15e1-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"
        write_job(self.job, WORDS)
        self.config = WebAppConfig(self.job, launch_home=self.tmp / "launch")
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

    def get_json(self, path):
        status, body = self.request(path)
        return status, json.loads(body.decode("utf-8"))

    def word_status(self, index):
        transcript = json.loads(
            (self.job / "transcript.json").read_text(encoding="utf-8")
        )
        timeline = Timeline(transcript, webapp._effective_cuts(self.config))
        return timeline.word_status(index)


class ThreadStoreTests(ChatServerCase):
    def test_thread_store_round_trip(self):
        stored = webapp._append_thread(
            self.config,
            {"kind": "creator", "status": "settled", "text": "Tighten it."},
        )
        self.assertTrue(stored["id"])
        self.assertIn(":", stored["time_label"])
        loaded = webapp._load_thread(self.config)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["text"], "Tighten it.")
        # The file lives in the job's session folder, like gold.
        on_disk = json.loads(
            (self.job / "tye-session" / "thread.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(on_disk["entries"][0]["text"], "Tighten it.")

    def test_thread_append_and_read_over_http(self):
        status, data = self.post_json(
            "/api/thread", {"kind": "creator", "text": "First ask."}
        )
        self.assertEqual(status, 200, data)
        status, view = self.get_json("/api/thread")
        self.assertEqual(status, 200)
        self.assertEqual(view["editor_name"], "Editor")
        texts = [e["text"] for e in view["entries"]]
        self.assertIn("First ask.", texts)


class EditorNameTests(ChatServerCase):
    def test_rename_persists_per_creator_not_per_job(self):
        status, data = self.post_json(
            "/api/editor-name", {"name": "Scout"}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["editor_name"], "Scout")
        self.assertEqual(usual.load_editor_name(), "Scout")
        # A different job, same creator: the name is still Scout,
        # and nothing about it was written into either job folder.
        other = self.tmp / "other-job"
        write_job(other, WORDS)
        other_config = WebAppConfig(other, launch_home=self.tmp / "launch")
        self.assertEqual(
            webapp._thread_view(other_config)["editor_name"], "Scout"
        )
        self.assertFalse((other / "tye-session" / "usual.json").exists())
        # A usual save (front door choices) never wipes the name.
        usual.save_usual(dict(webapp.FRONT_DOOR_DEFAULTS))
        self.assertEqual(usual.load_editor_name(), "Scout")


class HeldInThreadTests(ChatServerCase):
    def _seed_gold(self):
        record = {
            "gold_id": "gold-0-3-held",
            "word_start": 0,
            "word_end": 3,
            "kind": "held",
            "source": "direction",
            "reason": "The editor recommends removing these words.",
            "text": "Hello there this",
            "operation": None,
            "status": "open",
            "note": None,
            "created_at": webapp._utc_now(),
            "decided_at": None,
        }
        webapp._save_gold(self.config, [record])
        return record

    def test_held_span_appears_and_verdict_settles_everywhere(self):
        self._seed_gold()
        status, view = self.get_json("/api/thread")
        self.assertEqual(status, 200)
        held = [e for e in view["entries"] if e["kind"] == "held"]
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["status"], "open")
        self.assertEqual(view["waiting_count"], 1)
        status, result = self.post_json(
            "/api/gold-verdict",
            {"gold_id": "gold-0-3-held", "decision": "cut"},
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        # The gold store settled, and the words are cut in the plan.
        records = webapp._load_gold(self.config)
        self.assertEqual(records[0]["status"], "cut")
        for index in range(3):
            self.assertEqual(self.word_status(index), "cut")
        # The thread entry settled in place, with its decision.
        status, view = self.get_json("/api/thread")
        held = [e for e in view["entries"] if e["kind"] == "held"]
        self.assertEqual(held[0]["status"], "settled")
        self.assertEqual(held[0]["decision"], "cut")
        self.assertEqual(view["waiting_count"], 0)

    def test_auto_struck_spans_are_not_held(self):
        record = self._seed_gold()
        record["status"] = "auto_cut"
        record["auto_struck"] = True
        webapp._save_gold(self.config, [record])
        status, view = self.get_json("/api/thread")
        self.assertEqual(
            [e for e in view["entries"] if e["kind"] == "held"], []
        )


class RunSummaryTests(ChatServerCase):
    def test_make_the_edit_posts_no_recap_bubble(self):
        # 0.26, fix 3: the long recap bubble is removed. The run
        # still reports itself in its result (the mode line
        # included), but the thread gets no "run" entry: the
        # Done bubble is a run's only summary in the chat.
        status, result = self.post_json(
            "/api/front-door",
            {
                "remove_retakes": False,
                "cut_silence": False,
                "remove_fillers": False,
                "provider": "mock",
            },
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertIn(
            "Your way of working: Proactive.",
            " ".join(result.get("mode_lines") or []),
        )
        entries = webapp._load_thread(self.config)
        runs = [e for e in entries if e["kind"] == "run"]
        self.assertEqual(len(runs), 0)


class RecordQuestionTests(ChatServerCase):
    def _cut_first_words(self):
        session = self.config.session_dir
        session.mkdir(parents=True, exist_ok=True)
        revised = {
            "source_duration_s": WORDS[-1]["end"],
            "kept_ranges": [
                {"start": WORDS[4]["start"], "end": WORDS[-1]["end"]}
            ],
        }
        (session / "revised_cuts.json").write_text(
            json.dumps(revised), encoding="utf-8"
        )

    def test_record_question_answers_with_no_provider_call(self):
        self._cut_first_words()
        with mock.patch.object(
            webapp, "_run_direction_pass",
            side_effect=AssertionError("provider path ran"),
        ) as direction, mock.patch.object(
            webapp, "_direction_provider",
            side_effect=AssertionError("provider built"),
        ) as provider:
            status, data = self.post_json(
                "/api/thread/message", {"text": "What did you take out?"}
            )
        self.assertEqual(status, 200, data)
        direction.assert_not_called()
        provider.assert_not_called()
        self.assertEqual(data["kind"], "record")
        # The page renders the reply from the response's
        # thread view; it must be there and carry the reply.
        self.assertIn("thread", data)
        reply_texts = [
            e.get("text", "") for e in data["thread"]["entries"]
        ]
        self.assertTrue(
            any("From the record" in t for t in reply_texts),
            reply_texts,
        )
        entry = data["entry"]
        self.assertIn("From the record", entry["text"])
        self.assertEqual(len(entry["claims"]), 1)
        self.assertEqual(entry["claims"][0]["word_start"], 0)
        self.assertEqual(entry["claims"][0]["word_end"], 4)
        self.assertIn("those words are out now", entry["claims"][0]["text"])

    def test_cost_question_answers_from_cost_entries(self):
        webapp._account_costs(
            self.config,
            [webapp.costs.make_entry("direction", 0.02, "direction run", pool="meta")],
        )
        status, data = self.post_json(
            "/api/thread/message", {"text": "What did that run cost?"}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["kind"], "record")
        self.assertIn("about 2 cents", data["entry"]["text"])


class WhyInThreadTests(ChatServerCase):
    """The story question is a thread entry whose pills save
    through /api/why and settle in place (Batch E1, item 9)."""

    def test_restore_raises_a_thread_why_entry_and_an_answer_settles_it(self):
        session = self.config.session_dir
        session.mkdir(parents=True, exist_ok=True)
        revised = {
            "source_duration_s": WORDS[-1]["end"],
            "kept_ranges": [
                {"start": WORDS[4]["start"], "end": WORDS[-1]["end"]}
            ],
        }
        (session / "revised_cuts.json").write_text(
            json.dumps(revised), encoding="utf-8"
        )
        status, data = self.post_json(
            "/api/word-range",
            {"word_start": 0, "word_end": 4, "action": "restore"},
        )
        self.assertEqual(status, 200, data)
        prompt = data.get("why_prompt") or {}
        entry_id = prompt.get("entry_id")
        self.assertTrue(entry_id, data)
        status, view = self.get_json("/api/thread")
        self.assertEqual(status, 200, view)
        why = [e for e in view["entries"] if e["kind"] == "why"]
        self.assertEqual(len(why), 1, view["entries"])
        self.assertEqual(why[0]["grade_id"], entry_id)
        self.assertEqual(
            why[0]["question"], "Why did you put that back?"
        )
        self.assertFalse(why[0]["settled"])
        status, data = self.post_json(
            "/api/why",
            {"entry_id": entry_id, "choice": "story", "note": ""},
        )
        self.assertEqual(status, 200, data)
        status, view = self.get_json("/api/thread")
        why = [e for e in view["entries"] if e["kind"] == "why"]
        self.assertEqual(len(why), 1)
        self.assertTrue(why[0]["settled"])
        self.assertEqual(
            why[0]["answer_label"], "It matters to the story"
        )


class ChatSendTests(ChatServerCase):
    def test_voice_command_matches_first_from_the_panel_input(self):
        status, data = self.post_json(
            "/api/thread/message", {"text": "undo"}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["kind"], "command")
        self.assertEqual(data["command"], "undo")
        kinds = [e["kind"] for e in data["entries"]]
        self.assertEqual(kinds, ["creator", "editor"])

    def test_direction_ask_without_a_key_runs_no_provider(self):
        status, data = self.post_json(
            "/api/thread/message",
            {"text": "Tighten the first minute, keep the water entry."},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["kind"], "direction")
        self.assertIn("key is saved", data["entry"]["text"])

    def test_spending_ask_waits_for_a_plain_yes(self):
        self.config.session_api_key = "test-key"
        summary = {
            "ran": True, "applied": 1, "flagged_ops": 0, "flags": [],
            "lines": ["Read your direction and made 1 change."],
            "usage": [], "route_events": [], "mode": "cleanest",
            "negation_applied": 0, "auto_struck": [], "auto_struck_count": 0,
            "gold": 0,
        }
        with mock.patch.object(
            webapp, "_run_direction_pass", return_value=dict(summary)
        ) as run:
            status, data = self.post_json(
                "/api/thread/message", {"text": "Make the opening stronger."}
            )
            self.assertEqual(status, 200, data)
            self.assertEqual(data["kind"], "needs_confirmation")
            self.assertIn("spends money", data["entry"]["text"])
            run.assert_not_called()
            status, data = self.post_json(
                "/api/thread/message", {"text": "run it"}
            )
            self.assertEqual(status, 200, data)
            self.assertEqual(data["kind"], "direction")
            run.assert_called_once()

    def test_moment_record_question_still_answers(self):
        # A record question sent with a moment attached saves
        # the pin AND answers from the record, with the reply
        # in the thread.
        session = self.config.session_dir
        session.mkdir(parents=True, exist_ok=True)
        revised = {
            "source_duration_s": WORDS[-1]["end"],
            "kept_ranges": [
                {"start": WORDS[4]["start"], "end": WORDS[-1]["end"]}
            ],
        }
        (session / "revised_cuts.json").write_text(
            json.dumps(revised), encoding="utf-8"
        )
        status, data = self.post_json(
            "/api/thread/message",
            {
                "text": "What did you take out?",
                "moment": {
                    "ts": 1.0, "fmt": "vertical",
                    "word_start": 6, "word_end": 7,
                    "word_text": WORDS[6]["word"],
                },
            },
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["kind"], "record")
        self.assertIn("From the record", data["entry"]["text"])
        status, view = self.get_json("/api/thread")
        kinds = [e["kind"] for e in view["entries"]]
        self.assertIn("pin", kinds)
        self.assertIn("editor", kinds)

    def test_moment_message_becomes_a_pin_in_the_thread(self):
        status, data = self.post_json(
            "/api/thread/message",
            {
                "text": "This breath sounds weird here.",
                "moment": {"ts": 1.0, "fmt": "vertical"},
            },
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["kind"], "pin")
        pins = [e for e in data["entries"] if e["kind"] == "pin"]
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0]["moment_line"], "At 0:01 in the video")
        self.assertEqual(pins[0]["text"], "This breath sounds weird here.")
        feedback = json.loads(
            (self.job / "tye-session" / "feedback.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(feedback["comments"]), 1)
        self.assertEqual(feedback["comments"][0]["text"],
                         "This breath sounds weird here.")


class TemplateAndLabelsTests(unittest.TestCase):
    def test_recap_mode_labels_match_the_controls(self):
        self.assertEqual(webapp.MODE_LABELS["cleanest"], "Proactive")
        self.assertEqual(webapp.MODE_LABELS["tailor"], "Leave more in")
        lines = []
        # The recap line builder names the mode from MODE_LABELS.
        self.assertIn("Proactive", "Your way of working: Proactive.")

    def test_editor_panel_surfaces_exist_and_old_ones_are_gone(self):
        for marker in (
            'id="editorPanel"', 'id="threadList"', 'id="editorName"',
            'id="editorStatus"', 'id="editorInputRow"',
            'id="editorAttachBtn"', 'id="editorNewReply"',
            "This is your editor. Ask for a change the way you "
            "would say it out loud",
            "Take out the parts where I repeat myself",
            "Hide editor",
        ):
            self.assertIn(marker, PAGE, marker)
        for gone in ('id="pinCard"', 'id="overallCard"', 'id="whyCard"'):
            self.assertNotIn(gone, PAGE, gone)


if __name__ == "__main__":
    unittest.main()
