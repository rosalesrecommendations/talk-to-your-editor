"""Tests for v0.15 Batch E2: selection carry + queue + Send to
edit (item 15), clean-up quick actions (item 17), and the why
entry's info expansion (item 16 remainder).

Server criteria: two carried selections plus a typed message
become ONE direction ask whose text carries both moments, and
the spend gate still gates it; the creator entry in the thread
lists each queued moment; a clean-up chip flip walks the
existing setting path (the stored setting changes, $0 / mock
paths only) and the thread gains the editor entry; a why entry
carries the editor's reason as detail lines. Page criteria are
pinned as template structure: the bar's Send to Editor action,
the shortcut carrying instead of pinning, the queue chips, the
clean-up chip row, and the why info button.
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

from talk_to_your_editor import webapp
from talk_to_your_editor.webapp import WebAppConfig, create_server

PAGE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")


def between(start, end, text=PAGE):
    i = text.index(start)
    return text[i:text.index(end, i)]


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

NOTE_ONE = {
    "ts": 5.0, "fmt": "vertical",
    "word_start": 0, "word_end": 3, "word_text": "Hello there this",
}
NOTE_TWO = {
    "ts": 84.0, "fmt": "vertical",
    "word_start": 20, "word_end": 24, "word_text": "The water is cold",
}

SUMMARY = {
    "ran": True, "applied": 1, "flagged_ops": 0, "flags": [],
    "lines": ["Read your direction and made 1 change."],
    "usage": [], "route_events": [], "mode": "cleanest",
    "negation_applied": 0, "auto_struck": [], "auto_struck_count": 0,
    "gold": 0,
}


class ChatServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v15e2-"))
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


class QueueCompositionTests(ChatServerCase):
    def test_two_notes_and_a_message_become_one_direction_ask(self):
        calls = []

        def fake_direction(config, text, settings):
            calls.append(text)
            return dict(SUMMARY)

        with mock.patch.object(
            webapp, "_run_direction_pass", side_effect=fake_direction
        ):
            status, data = self.post_json(
                "/api/thread/message",
                {
                    "text": "Tighten both of these spots.",
                    "queue": [NOTE_ONE, NOTE_TWO],
                },
            )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["kind"], "direction")
        # One run, and its ask carries the message and both moments.
        self.assertEqual(len(calls), 1)
        ask = calls[0]
        self.assertIn("Tighten both of these spots.", ask)
        self.assertIn("At 0:05 in the video", ask)
        self.assertIn("At 1:24 in the video", ask)
        self.assertIn('"Hello there this"', ask)
        self.assertIn('"The water is cold"', ask)
        # One editor reply, not two.
        replies = [e for e in data["entries"] if e["kind"] == "editor"]
        self.assertEqual(len(replies), 1)
        # The creator entry lists each queued moment.
        creators = [e for e in data["entries"] if e["kind"] == "creator"]
        self.assertEqual(len(creators), 1)
        queue = creators[0].get("queue") or []
        self.assertEqual(len(queue), 2)
        self.assertEqual(queue[0]["moment_line"], "At 0:05 in the video")
        self.assertEqual(queue[1]["moment_line"], "At 1:24 in the video")
        self.assertEqual(queue[0]["word_text"], "Hello there this")
        # Each note also landed as a pinned comment for the run.
        feedback = json.loads(
            (self.job / "tye-session" / "feedback.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(feedback["comments"]), 2)
        self.assertTrue(
            all(
                c["text"] == "Tighten both of these spots."
                for c in feedback["comments"]
            )
        )
        pins = [e for e in data["entries"] if e["kind"] == "pin"]
        self.assertEqual(len(pins), 2)

    def test_queue_send_needs_no_covering_message(self):
        calls = []

        def fake_direction(config, text, settings):
            calls.append(text)
            return dict(SUMMARY)

        with mock.patch.object(
            webapp, "_run_direction_pass", side_effect=fake_direction
        ):
            status, data = self.post_json(
                "/api/thread/message",
                {"text": "", "queue": [NOTE_ONE]},
            )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["kind"], "direction")
        self.assertEqual(len(calls), 1)
        self.assertIn("At 0:05 in the video", calls[0])

    def test_spend_gate_still_gates_a_queue_send(self):
        self.config.session_api_key = "test-key"
        calls = []

        def fake_direction(config, text, settings):
            calls.append(text)
            return dict(SUMMARY)

        with mock.patch.object(
            webapp, "_run_direction_pass", side_effect=fake_direction
        ) as run:
            status, data = self.post_json(
                "/api/thread/message",
                {"text": "Fix these two.", "queue": [NOTE_ONE, NOTE_TWO]},
            )
            self.assertEqual(status, 200, data)
            self.assertEqual(data["kind"], "needs_confirmation")
            self.assertIn("spends money", data["entry"]["text"])
            run.assert_not_called()
            # Nothing was pinned yet: the run has not gone ahead.
            self.assertFalse(
                (self.job / "tye-session" / "feedback.json").exists()
            )
            status, data = self.post_json(
                "/api/thread/message", {"text": "run it"}
            )
            self.assertEqual(status, 200, data)
            self.assertEqual(data["kind"], "direction")
            run.assert_called_once()
        self.assertEqual(len(calls), 1)
        self.assertIn("At 0:05 in the video", calls[0])
        self.assertIn("At 1:24 in the video", calls[0])
        feedback = json.loads(
            (self.job / "tye-session" / "feedback.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(feedback["comments"]), 2)

    def test_compose_queue_ask_carries_both_moments(self):
        notes = webapp._queue_notes([NOTE_ONE, NOTE_TWO])
        ask = webapp._compose_queue_ask("One covering message.", notes)
        self.assertTrue(ask.startswith("One covering message."))
        self.assertIn("- At 0:05 in the video: \"Hello there this\"", ask)
        self.assertIn(
            "- At 1:24 in the video: \"The water is cold\"", ask
        )


class CleanupChipTests(ChatServerCase):
    def test_flip_changes_the_stored_front_door_setting(self):
        self.assertTrue(
            webapp._front_door_settings(self.config)["remove_fillers"]
        )
        status, data = self.post_json(
            "/api/thread/cleanup", {"key": "remove_fillers", "on": False}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["key"], "remove_fillers")
        self.assertFalse(data["cleanup"]["remove_fillers"])
        # The stored setting itself changed (the Edit settings
        # toggles read this same store).
        self.assertFalse(
            webapp._front_door_settings(self.config)["remove_fillers"]
        )
        # The thread gained the short editor entry.
        texts = [e.get("text", "") for e in data["entries"]]
        self.assertIn(
            "Remove filler words is off now. "
            "It counts from the next Make the edit run.",
            texts,
        )
        # And it flips back.
        status, data = self.post_json(
            "/api/thread/cleanup", {"key": "remove_fillers", "on": True}
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(
            webapp._front_door_settings(self.config)["remove_fillers"]
        )

    def test_captions_chip_uses_the_captions_setting_path(self):
        self.assertTrue(webapp._captions_hide_fillers(self.config))
        status, data = self.post_json(
            "/api/thread/cleanup",
            {"key": "captions_hide_fillers", "on": False},
        )
        self.assertEqual(status, 200, data)
        self.assertFalse(data["cleanup"]["captions_hide_fillers"])
        # The same store /api/captions-pref writes.
        self.assertFalse(webapp._captions_hide_fillers(self.config))
        pref = json.loads(
            (self.job / "tye-session" / "captions_pref.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(pref, {"hide_fillers": False})
        texts = [e.get("text", "") for e in data["entries"]]
        self.assertIn(
            "Hide ums and uhs in captions is off now. "
            "The caption file matches it now.",
            texts,
        )

    def test_thread_view_carries_the_cleanup_state(self):
        status, view = self.get_json("/api/thread")
        self.assertEqual(status, 200)
        self.assertEqual(
            set(view["cleanup"]),
            {
                "remove_retakes", "cut_silence",
                "remove_fillers", "captions_hide_fillers",
            },
        )
        self.assertTrue(view["cleanup"]["remove_retakes"])

    def test_cleanup_rejects_an_unknown_key_and_a_missing_state(self):
        status, _ = self.post_json(
            "/api/thread/cleanup", {"key": "studio_sound", "on": True}
        )
        self.assertEqual(status, 400)
        status, _ = self.post_json(
            "/api/thread/cleanup", {"key": "remove_fillers"}
        )
        self.assertEqual(status, 400)


class WhyDetailTests(ChatServerCase):
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

    def test_restore_why_entry_carries_the_reason_as_detail(self):
        self._cut_first_words()
        status, data = self.post_json(
            "/api/word-range",
            {"word_start": 0, "word_end": 4, "action": "restore"},
        )
        self.assertEqual(status, 200, data)
        status, view = self.get_json("/api/thread")
        why = [e for e in view["entries"] if e["kind"] == "why"]
        self.assertEqual(len(why), 1)
        detail = why[0].get("detail") or []
        joined = " ".join(detail)
        self.assertIn(
            'The words you put back: "Hello there this is"', joined
        )
        self.assertIn("Bringing back words the editor had cut.", joined)
        self.assertIn("Your answer teaches the editor", joined)

    def test_gold_leave_why_entry_carries_the_flag_reason(self):
        record = {
            "gold_id": "gold-0-3-held",
            "word_start": 0,
            "word_end": 3,
            "kind": "retake",
            "source": "direction",
            "reason": "Earlier take, said again right after.",
            "text": "Hello there this",
            "operation": None,
            "status": "open",
            "note": None,
            "created_at": webapp._utc_now(),
            "decided_at": None,
        }
        webapp._save_gold(self.config, [record])
        status, result = self.post_json(
            "/api/gold-verdict",
            {"gold_id": "gold-0-3-held", "decision": "leave"},
        )
        self.assertEqual(status, 200, result)
        status, view = self.get_json("/api/thread")
        why = [e for e in view["entries"] if e["kind"] == "why"]
        self.assertEqual(len(why), 1)
        self.assertEqual(why[0]["question"], "Why are you leaving that in?")
        detail = why[0].get("detail") or []
        joined = " ".join(detail)
        self.assertIn('The words: "Hello there this"', joined)
        self.assertIn(
            "Why the editor flagged them: "
            "Earlier take, said again right after.",
            joined,
        )
        self.assertIn("Keeping both takes of a repeated line", joined)


class TemplateTests(unittest.TestCase):
    def test_the_bar_has_a_send_to_editor_action(self):
        tag = between('id="selSendBtn"', 'id="selContextBtn"')
        self.assertIn('title="Send to Editor"', tag)
        self.assertIn('aria-label="Send to Editor"', tag)
        menu = between('id="selMenuSend"', 'id="selMenuFix"')
        self.assertIn("Send to Editor", menu)
        self.assertIn("Cmd+Shift+M", menu)
        # The shortcut hint moved to the action it now runs; the
        # Comment item keeps no hint.
        comment = between('id="selMenuComment"', 'id="selMenuSend"')
        self.assertNotIn("Cmd+Shift+M", comment)

    def test_shortcut_and_bar_actions_carry_the_pick(self):
        wire = between("if (selSendBtn)", "if (selMenuSend)")
        self.assertIn("carryPickToEditor()", wire)
        shortcut = between(
            "// Cmd/Ctrl+Shift+M anywhere in the review",
            "// -- leaving the transcript",
        )
        self.assertIn("carryPickToEditor()", shortcut)
        self.assertNotIn("pinCommentOnPick()", shortcut)

    def test_carrying_never_clears_the_pick(self):
        carry = between(
            "function carryPickToEditor()",
            "// Cmd/Ctrl+Shift+M anywhere in the review",
        )
        self.assertNotIn("clearSelection", carry)
        self.assertIn("editorQueue.push(", carry)
        self.assertIn("edited_start", carry)

    def test_queue_chips_and_removal_live_above_the_input(self):
        self.assertIn('id="editorQueueRow"', PAGE)
        render = between(
            "function renderEditorQueue()",
            "function carryPickToEditor()",
        )
        self.assertIn("queue-chip", render)
        self.assertIn("editorQueue.splice(idx, 1);", render)
        self.assertIn("Take this note off", render)
        self.assertIn("fmtMinSec(note.ts)", render)

    def test_send_clears_the_queue_and_names_send_to_edit(self):
        send = between(
            "function sendEditorMessage()",
            "if (threadListEl)",
        )
        self.assertIn("body.queue = sendQueue;", send)
        self.assertIn("editorQueue = [];", send)
        self.assertIn("renderEditorQueue();", send)
        update = between(
            "function updateEditorSend()",
            "draft.addEventListener(\"input\"",
        )
        self.assertIn('"Send to edit"', update)
        self.assertIn("editorQueue.length > 0", update)

    def test_cleanup_chip_row_lives_above_the_input(self):
        self.assertIn('id="editorCleanupRow"', PAGE)
        self.assertIn('id="editorCleanupChips"', PAGE)
        chips = between("var CLEANUP_CHIPS", "function renderCleanupChips()")
        for key in (
            "remove_retakes", "cut_silence",
            "remove_fillers", "captions_hide_fillers",
        ):
            self.assertIn(key, chips)
        flip = between("function flipCleanup(key)", "function renderThreadView(view)")
        self.assertIn('"/api/thread/cleanup"', flip)
        self.assertIn("syncCleanupSurfaces(key", flip)

    def test_why_entries_have_the_info_expansion(self):
        self.assertIn("thread-why-info", PAGE)
        self.assertIn('title = "More about this"', PAGE)
        why = between("function threadWhyEl(entry)", "function threadEntryEl(entry)")
        self.assertIn("entry.detail", why)
        self.assertIn("detail.hidden = !detail.hidden;", why)
        self.assertIn('setAttribute("aria-expanded"', why)

    def test_creator_entries_list_their_queued_moments(self):
        entry = between(
            "function threadEntryEl(entry)", "function renderThreadView(view)"
        )
        self.assertIn("entry.queue", entry)
        self.assertIn("thread-queue-line", entry)
        self.assertIn("note.moment_line", entry)


if __name__ == "__main__":
    unittest.main()
