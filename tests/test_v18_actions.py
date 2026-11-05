"""Tests for v0.18 Builder B: message actions in the thread.

Covers success criteria 4 and 5 of the v0.18 plan:

4. Reply to one message: every Editor message carries a
   three-dot options button; the same menu opens on
   press-and-hold (about half a second) and on right-click;
   the menu order is exactly Copy, Reply, Undo (Undo only when
   the message is tied to an undoable action, Redo in its place
   once undone). Copy puts the message text on the clipboard.
   Reply sets a context bar above the composer; sending carries
   the message's id as reply_to, the server stores it on the
   creator entry, and the answer is scoped to that message: a
   question about an action entry is answered from that
   action's own checkpoint record, not a global summary.
5. Per-action undo and redo, no regeneration: an entry that
   reports a run carries action_id naming the checkpoint that
   run committed. POST /api/thread/action undo reverses that
   action's effect on the job state using saved checkpoints
   only (no provider call, no spend), marks the entry undone,
   and redo reapplies it and clears the mark. An action that is
   no longer the newest checkpoint cannot be reversed on its
   own: the honest fallback fires in plain words, and no global
   undo happens.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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

from talk_to_your_editor import checkpoints, costs, webapp  # noqa: E402
from talk_to_your_editor.timeline import Timeline  # noqa: E402
from talk_to_your_editor.webapp import WebAppConfig, create_server  # noqa: E402

PAGE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")


def between(start: str, end: str, text: str = PAGE) -> str:
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


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
    (job / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")


def apply_cut(config, start: int, end: int) -> None:
    """Cut one word range from the session's current plan, the
    way a direction pass would land it (revised_cuts.json)."""
    transcript = json.loads(
        (config.job_dir / "transcript.json").read_text(encoding="utf-8")
    )
    words = transcript["words"]
    timeline = Timeline(transcript, webapp._effective_cuts(config))
    kept = [
        i
        for i in range(len(words))
        if timeline.word_status(i) == "kept" and not (start <= i < end)
    ]
    ranges = []
    for i in kept:
        if ranges and i == ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], i + 1)
        else:
            ranges.append((i, i + 1))
    revised = {
        "source_duration_s": transcript["duration"],
        "kept_ranges": [
            {"start": words[s]["start"], "end": words[e - 1]["end"]}
            for s, e in ranges
        ],
    }
    config.session_dir.mkdir(parents=True, exist_ok=True)
    (config.session_dir / "revised_cuts.json").write_text(
        json.dumps(revised), encoding="utf-8"
    )


def cutting_pass(start: int, end: int, line: str):
    """A fake direction pass: lands one real cut, spends nothing."""

    def run(config, text, settings):
        apply_cut(config, start, end)
        return {
            "ran": True,
            "applied": 1,
            "flagged_ops": 0,
            "flags": [],
            "lines": [line],
            "usage": [],
            "route_events": [],
            "mode": "cleanest",
            "negation_applied": 0,
            "auto_struck": [],
            "auto_struck_count": 0,
            "gold": 0,
        }

    return run


class ActionsServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v18-actions-"))
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

    def request(self, path, data=None):
        payload = None
        headers = {}
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path, data=payload, headers=headers,
            method="POST" if payload is not None else "GET",
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

    def make_action(self, start=0, end=4, text="Please tighten the opening a lot."):
        """One chat direction run that really cuts words, $0."""
        with mock.patch.object(
            webapp,
            "_run_direction_pass",
            side_effect=cutting_pass(start, end, "Cut those words."),
        ):
            status, data = self.post_json(
                "/api/thread/message", {"text": text}
            )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["kind"], "direction")
        return data["entry"]


class ContractTests(ActionsServerCase):
    def test_thread_view_carries_the_contract_fields(self):
        webapp._append_thread(
            self.config,
            {"kind": "editor", "status": "settled", "text": "Hello."},
        )
        status, view = self.get_json("/api/thread")
        self.assertEqual(status, 200)
        entry = view["entries"][0]
        self.assertIn("reply_to", entry)
        self.assertIn("action_id", entry)
        self.assertIn("undone", entry)
        self.assertIsNone(entry["reply_to"])
        self.assertIsNone(entry["action_id"])
        self.assertFalse(entry["undone"])

    def test_direction_entry_reports_its_checkpoint_as_action(self):
        entry = self.make_action()
        # Checkpoint 0 is the session start; the run's own
        # commit is checkpoint 1, and that is the action's id.
        self.assertEqual(entry["action_id"], "checkpoint:1")
        self.assertEqual(entry["action_label"], "Direction run")
        self.assertFalse(entry["undone"])
        state = checkpoints.state(self.config)
        self.assertEqual(state["current"], 1)
        self.assertEqual(state["checkpoints"][1]["label"], "Direction run")

    def test_front_door_run_posts_no_recap_entry(self):
        # 0.26, fix 3: the long recap bubble is removed. A front
        # door run files its review claims but posts no "run"
        # entry to the thread; the Done bubble is the only
        # summary a run leaves in the chat.
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
        runs = [
            e for e in webapp._load_thread(self.config) if e["kind"] == "run"
        ]
        self.assertEqual(len(runs), 0)


class UndoRedoTests(ActionsServerCase):
    def test_undo_reverts_the_action_and_marks_the_entry(self):
        entry = self.make_action()
        self.assertEqual(self.word_status(0), "cut")
        spend_before = len(costs.load_session_entries(self.config.session_dir))
        with mock.patch.object(
            webapp, "_run_direction_pass",
            side_effect=AssertionError("provider path ran"),
        ), mock.patch.object(
            webapp, "_direction_provider",
            side_effect=AssertionError("provider built"),
        ):
            status, data = self.post_json(
                "/api/thread/action",
                {"entry_id": entry["id"], "move": "undo"},
            )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        self.assertTrue(data["moved"])
        self.assertFalse(data["fallback"])
        self.assertIn("Undone", data["result"])
        # The action's effect on the job state is gone.
        self.assertEqual(self.word_status(0), "kept")
        self.assertEqual(checkpoints.state(self.config)["current"], 0)
        # The entry is marked undone in the view the page renders.
        self.assertTrue(data["entry"]["undone"])
        # No provider call, no spend on this path.
        self.assertEqual(
            len(costs.load_session_entries(self.config.session_dir)),
            spend_before,
        )

    def test_redo_reapplies_the_action_and_clears_the_mark(self):
        entry = self.make_action()
        status, data = self.post_json(
            "/api/thread/action", {"entry_id": entry["id"], "move": "undo"}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(self.word_status(0), "kept")
        with mock.patch.object(
            webapp, "_run_direction_pass",
            side_effect=AssertionError("provider path ran"),
        ):
            status, data = self.post_json(
                "/api/thread/action",
                {"entry_id": entry["id"], "move": "redo"},
            )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["moved"])
        self.assertIn("Redone", data["result"])
        self.assertEqual(self.word_status(0), "cut")
        self.assertFalse(data["entry"]["undone"])
        self.assertEqual(checkpoints.state(self.config)["current"], 1)

    def test_fallback_for_an_action_that_is_not_the_newest(self):
        first = self.make_action()
        second = self.make_action(
            start=10, end=14, text="Now tighten the middle part too."
        )
        self.assertEqual(second["action_id"], "checkpoint:2")
        status, data = self.post_json(
            "/api/thread/action", {"entry_id": first["id"], "move": "undo"}
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["fallback"])
        self.assertFalse(data["moved"])
        self.assertIn("on its own", data["result"])
        self.assertIn("safe next step", data["result"])
        # No global undo happened: the pointer and both cuts stand.
        self.assertEqual(checkpoints.state(self.config)["current"], 2)
        self.assertEqual(self.word_status(0), "cut")
        self.assertEqual(self.word_status(10), "cut")
        self.assertFalse(data["entry"]["undone"])
        # The fallback is said in the thread, in plain words.
        texts = [e.get("text", "") for e in data["thread"]["entries"]]
        self.assertTrue(any("on its own" in t for t in texts), texts)

    def test_newest_action_still_undoes_after_an_older_one_exists(self):
        self.make_action()
        second = self.make_action(
            start=10, end=14, text="Now tighten the middle part too."
        )
        status, data = self.post_json(
            "/api/thread/action", {"entry_id": second["id"], "move": "undo"}
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["moved"])
        # Only the second action's words came back.
        self.assertEqual(self.word_status(10), "kept")
        self.assertEqual(self.word_status(0), "cut")

    def test_action_endpoint_rejects_plain_and_missing_entries(self):
        plain = webapp._append_thread(
            self.config,
            {"kind": "editor", "status": "settled", "text": "Just words."},
        )
        status, data = self.post_json(
            "/api/thread/action", {"entry_id": plain["id"], "move": "undo"}
        )
        self.assertEqual(status, 400)
        self.assertIn("not tied to an action", data["error"])
        status, data = self.post_json(
            "/api/thread/action", {"entry_id": "nope", "move": "undo"}
        )
        self.assertEqual(status, 400)
        status, data = self.post_json(
            "/api/thread/action", {"entry_id": plain["id"], "move": "sideways"}
        )
        self.assertEqual(status, 400)


class ReplyTests(ActionsServerCase):
    def test_reply_is_stored_on_the_creator_entry(self):
        target = webapp._append_thread(
            self.config,
            {
                "kind": "editor",
                "status": "settled",
                "text": "I tightened the opening.",
                "lines": [],
                "claims": [],
            },
        )
        status, data = self.post_json(
            "/api/thread/message",
            {"text": "What did you say there?", "reply_to": target["id"]},
        )
        self.assertEqual(status, 200, data)
        creators = [
            e for e in webapp._load_thread(self.config)
            if e["kind"] == "creator"
        ]
        self.assertEqual(len(creators), 1)
        self.assertEqual(creators[0]["reply_to"], target["id"])
        # The answer is scoped to that message, not the thread.
        self.assertEqual(data["kind"], "scoped")
        self.assertIn("About that message", data["entry"]["text"])
        self.assertIn("I tightened the opening.", data["entry"]["text"])

    def test_scoped_question_answers_from_the_action_record(self):
        entry = self.make_action()
        # A later global cut, with no thread entry of its own:
        # a global answer would count both spots.
        status, data = self.post_json(
            "/api/word-range",
            {"word_start": 10, "word_end": 14, "action": "cut"},
        )
        self.assertEqual(status, 200, data)
        status, data = self.post_json(
            "/api/thread/message",
            {"text": "What did you take out?", "reply_to": entry["id"]},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["kind"], "scoped")
        self.assertIn("that message's own record", data["entry"]["text"])
        self.assertIn("1 spot", data["entry"]["text"])
        claims = data["entry"]["claims"]
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["word_start"], 0)
        self.assertEqual(claims[0]["word_end"], 4)

    def test_scoped_undo_words_act_on_that_action(self):
        entry = self.make_action()
        self.assertEqual(self.word_status(0), "cut")
        status, data = self.post_json(
            "/api/thread/message",
            {"text": "undo that", "reply_to": entry["id"]},
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["kind"], "action")
        self.assertTrue(data["moved"])
        self.assertEqual(self.word_status(0), "kept")
        refreshed = webapp._find_thread_entry(self.config, entry["id"])
        self.assertTrue(refreshed["undone"])

    def test_scoped_direction_on_an_action_is_not_run_globally(self):
        entry = self.make_action()
        with mock.patch.object(
            webapp, "_run_direction_pass",
            side_effect=AssertionError("provider path ran"),
        ):
            status, data = self.post_json(
                "/api/thread/message",
                {
                    "text": "Make the whole thing punchier.",
                    "reply_to": entry["id"],
                },
            )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["kind"], "scoped")
        self.assertIn("did not run it", data["entry"]["text"])


MENU_BLOCK = between("// == message-actions-begin", "// == message-actions-end ==")


def run_node(body: str) -> subprocess.CompletedProcess:
    script = Path(tempfile.mkdtemp(prefix="tye-v18-node-")) / "check.js"
    script.write_text(body, encoding="utf-8")
    return subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=30
    )


class MenuTemplateTests(unittest.TestCase):
    def test_every_editor_bubble_gets_the_three_dot_button(self):
        entry_fn = between("function threadEntryEl", "// == message-actions-begin")
        # The button is wired on the bubble path, after the
        # held, pin and why entries have already returned.
        self.assertIn("threadMsgMenuBtn(entry)", entry_fn)
        self.assertGreater(
            entry_fn.index("threadMsgMenuBtn(entry)"),
            entry_fn.index('entry.kind === "why"'),
        )
        self.assertIn('b.className = "thread-msg-menu-btn"', MENU_BLOCK)
        self.assertIn('b.textContent = "•••"', MENU_BLOCK)
        # The greeting wrapper never grows a menu.
        greeting = between("function threadGreetingEl", "function renderThreadView")
        self.assertNotIn("threadMsgMenuBtn", greeting)
        self.assertNotIn("thread-msg-menu", greeting)

    def test_menu_opens_on_hold_and_on_right_click(self):
        self.assertIn('addEventListener("contextmenu"', MENU_BLOCK)
        self.assertIn("event.preventDefault()", MENU_BLOCK)
        self.assertIn('addEventListener("pointerdown"', MENU_BLOCK)
        self.assertIn("}, 500);", MENU_BLOCK)
        # Both gestures and the button open the one menu builder.
        self.assertGreaterEqual(MENU_BLOCK.count("openThreadMsgMenu(entry"), 3)

    def test_undone_mark_is_rendered_in_the_bubble(self):
        entry_fn = between("function threadEntryEl", "// == message-actions-begin")
        self.assertIn('"thread-undone-mark"', entry_fn)
        self.assertIn('mark.textContent = "Undone"', entry_fn)

    def test_reply_bar_and_send_carry_reply_to(self):
        self.assertIn('id="editorReplyRow"', PAGE)
        self.assertIn('id="editorReplyText"', PAGE)
        self.assertIn('id="editorReplyClear"', PAGE)
        send = between("function sendEditorMessage", "var editorNewReplyBtn")
        self.assertIn("body.reply_to = editorReplyTo.id", send)
        self.assertIn("clearEditorReplyTo()", send)
        self.assertIn('"/api/thread/action"', MENU_BLOCK)

    def test_menu_items_copy_and_reply_in_node(self):
        body = (
            "global.window = global;\n"
            "global.editorNameStr = 'Editor';\n"
            "var copied = [];\n"
            # Node's own navigator global is getter-only, so the
            # clipboard stub is defined onto it outright.
            "Object.defineProperty(globalThis, 'navigator', {"
            " configurable: true, value: { clipboard: {"
            " writeText: function (t) {"
            " copied.push(t); return Promise.resolve(); } } } });\n"
            "var elements = {\n"
            "  editorReplyRow: { hidden: true },\n"
            "  editorReplyText: { textContent: '' }\n"
            "};\n"
            "global.document = {\n"
            "  getElementById: function (id) { return elements[id] ||"
            " { addEventListener: function () {}, hidden: false }; },\n"
            "  addEventListener: function () {},\n"
            "  body: { appendChild: function () {} }\n"
            "};\n"
            + MENU_BLOCK
            + "\nconst assert = require('assert');\n"
            "function labels(entry) {\n"
            "  return threadMsgMenuItems(entry).map(function (i) { return i[1]; });\n"
            "}\n"
            # Exactly Copy, Reply, Undo, in that order, when the
            # message carries an action.
            "assert.deepStrictEqual(labels({id: 'a', action_id: 'checkpoint:1'}),"
            " ['Copy', 'Reply', 'Undo']);\n"
            # No Undo when the entry has no action.
            "assert.deepStrictEqual(labels({id: 'b'}), ['Copy', 'Reply']);\n"
            # Redo replaces Undo once the action is undone.
            "assert.deepStrictEqual(labels({id: 'a', action_id: 'checkpoint:1', undone: true}),"
            " ['Copy', 'Reply', 'Redo']);\n"
            # Copy writes the message text to the clipboard.
            "copyThreadEntryText({text: 'Hello there.', lines: ['Second line.']});\n"
            "assert.deepStrictEqual(copied, ['Hello there.\\nSecond line.']);\n"
            # Reply sets the context bar naming the message.
            "setEditorReplyTo({id: 'abc', text: 'Some editor words'});\n"
            "assert.strictEqual(editorReplyTo.id, 'abc');\n"
            "assert.strictEqual(elements.editorReplyRow.hidden, false);\n"
            "assert.ok(elements.editorReplyText.textContent.indexOf('Replying to') === 0);\n"
            "assert.ok(elements.editorReplyText.textContent.indexOf('Some editor words') > 0);\n"
            "clearEditorReplyTo();\n"
            "assert.strictEqual(editorReplyTo, null);\n"
            "assert.strictEqual(elements.editorReplyRow.hidden, true);\n"
            "console.log('menu-stub-ok');\n"
        )
        result = run_node(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("menu-stub-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
