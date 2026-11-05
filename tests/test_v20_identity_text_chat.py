"""Tests for v0.20 Chunk A: Editor identity + text-only chat.

From Justin's 2026-11-01 Mac run of 0.19 (friction log):

1. The start screen chat showed no name and no avatar, only
   "Ask the editor", and asking "whats your name" returned a
   capability paragraph. The home chat head now carries the
   same avatar and the same renameable name as the editing
   page's Editor head, and a name question is answered with
   the Editor's actual current name.
2. Justin's correction, in his words: "i don't want the
   voice talking to me... i want it chatting with me like an
   imessage convo." The conversation is text only. The
   spoken-replies playback from v0.18 is removed whole: no
   toggle, no speech synthesis call anywhere in the served
   page or the server, so nothing can speak on a chat reply,
   on a clean-up switch tap, or anywhere else.
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

from talk_to_your_editor import webapp  # noqa: E402
from talk_to_your_editor.webapp import WebAppConfig, create_server  # noqa: E402

PAGE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")
WEBAPP_SRC = (
    ROOT / "src" / "talk_to_your_editor" / "webapp.py"
).read_text(encoding="utf-8")


def between(start: str, end: str, text: str = PAGE) -> str:
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def rule(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", PAGE)
    assert match, selector
    return match.group(1)


WORDS = [
    {"word": w, "start": float(i), "end": float(i) + 0.4}
    for i, w in enumerate("Hello there this is the video today".split())
]


def write_job(job: Path) -> None:
    job.mkdir(parents=True, exist_ok=True)
    transcript = {"engine": "test", "duration": 4.0, "words": WORDS}
    cuts = {
        "source_duration_s": 4.0,
        "kept_ranges": [{"start": 0.0, "end": 4.0}],
    }
    (job / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")


class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v20-a-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"
        write_job(self.job)
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

    def get_text(self, path):
        status, body = self.request(path)
        return status, body.decode("utf-8")


HOME_CARD = between('id="homeChatCard"', 'id="runOverlay"')


class HomeIdentityTests(unittest.TestCase):
    def test_home_chat_head_shows_avatar_and_name(self):
        self.assertIn(
            '<span class="editor-avatar" id="homeEditorAvatar" '
            'aria-hidden="true">🎬</span>',
            HOME_CARD,
        )
        self.assertIn('id="homeEditorName"', HOME_CARD)
        self.assertIn('id="homeEditorNameInput"', HOME_CARD)
        # The bare heading that hid the person is gone.
        self.assertNotIn("<h2>Ask the editor</h2>", HOME_CARD)

    def test_home_name_is_renameable_through_the_same_route(self):
        self.assertIn(
            'homeEditorNameBtn.addEventListener("dblclick", '
            "startHomeEditorRename)",
            PAGE,
        )
        rename = between(
            "function finishHomeEditorRename", 'fetch("/api/editor-name")'
        )
        self.assertIn('postJson("/api/editor-name", { name: next })', rename)

    def test_saved_name_loads_on_the_start_screen(self):
        # The head is not stuck on the default: on arrival the
        # page reads the saved name from the name route and
        # writes it to both heads.
        load = between('fetch("/api/editor-name")', "var howItWorksClose")
        self.assertIn("editorNameStr = data.editor_name", load)
        self.assertIn("showEditorNameEverywhere()", load)

    def test_served_page_carries_the_home_identity(self):
        case = ServerCase("run")
        case.setUp()
        try:
            status, text = case.get_text("/")
            self.assertEqual(status, 200)
            self.assertIn('id="homeEditorAvatar" aria-hidden="true">🎬<', text)
            self.assertIn('id="homeEditorName"', text)
        finally:
            case.tearDown()

    def test_renamed_name_is_the_name_the_start_screen_reads(self):
        case = ServerCase("run")
        case.setUp()
        try:
            status, data = case.post_json(
                "/api/editor-name", {"name": "Scout"}
            )
            self.assertEqual(status, 200, data)
            status, data = case.get_json("/api/editor-name")
            self.assertEqual(status, 200)
            self.assertEqual(data["editor_name"], "Scout")
        finally:
            case.tearDown()


def run_node(body: str) -> subprocess.CompletedProcess:
    script = Path(tempfile.mkdtemp(prefix="tye-v20-node-")) / "check.js"
    script.write_text(body, encoding="utf-8")
    return subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=30
    )


class NameQuestionTests(unittest.TestCase):
    def test_name_question_answers_with_the_current_name(self):
        answers = between("var HOME_CHAT_ANSWERS", "function homeChatAsk")
        body = (
            'var editorNameStr = "Scout";\n'
            + answers
            + "\nconst assert = require('assert');\n"
            "assert.strictEqual(homeChatAnswer('whats your name'),"
            " 'I am Scout, your editor.');\n"
            "assert.strictEqual(homeChatAnswer('What is your name?'),"
            " 'I am Scout, your editor.');\n"
            "assert.strictEqual(homeChatAnswer('who are you'),"
            " 'I am Scout, your editor.');\n"
            # The canned answers otherwise stay as they were.
            "assert.ok(homeChatAnswer('What can you do?')"
            ".indexOf('I take a video you bring in') === 0);\n"
            "assert.ok(homeChatAnswer('What does it cost?')"
            ".indexOf('Asking me here costs nothing') === 0);\n"
            "console.log('name-question-ok');\n"
        )
        result = run_node(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("name-question-ok", result.stdout)


SPEECH_TOKENS = [
    "speechSynthesis",
    "SpeechSynthesisUtterance",
    "editorSpeak",
    "speakEditorReply",
    "noteEditorEntriesForSpeech",
    "setEditorSpeak",
    "Spoken replies",
    'id="editorSpeakToggle"',
    "spoken-replies-begin",
    "spoken-replies-end",
]


class TextOnlyChatTests(unittest.TestCase):
    def test_no_speech_path_anywhere_in_the_served_page(self):
        for token in SPEECH_TOKENS:
            self.assertNotIn(token, PAGE, token)

    def test_no_speech_path_in_the_server_routes(self):
        for token in ("speechSynthesis", "SpeechSynthesisUtterance",
                      "editorSpeak", "Spoken replies"):
            self.assertNotIn(token, WEBAPP_SRC, token)

    def test_no_speech_path_in_any_served_text(self):
        case = ServerCase("run")
        case.setUp()
        try:
            status, text = case.get_text("/")
            self.assertEqual(status, 200)
            for token in SPEECH_TOKENS:
                self.assertNotIn(token, text, token)
        finally:
            case.tearDown()

    def test_thread_render_never_calls_a_speaker(self):
        render = between("function renderThreadView", "function refreshThread")
        self.assertNotIn("Speech", render)
        self.assertNotIn("speak", render)

    def test_home_chat_is_text_bubbles_on_two_sides(self):
        # His words one side, the Editor's the other, like a
        # text conversation.
        self.assertIn("align-self: flex-end", rule(".home-chat-line.q"))
        self.assertIn("align-self: flex-start", rule(".home-chat-line.a"))
        ask = between("function homeChatAsk", "var homeChatSend")
        self.assertIn('qLine.className = "home-chat-line q"', ask)
        self.assertIn('aLine.className = "home-chat-line a"', ask)

    def test_stored_settings_with_a_speak_key_do_not_break(self):
        # A usual file saved by 0.18/0.19 can carry keys this
        # version no longer knows; loading settings must ignore
        # them, not fail.
        case = ServerCase("run")
        case.setUp()
        try:
            usual_file = (
                case.home / ".talk-to-your-editor" / "usual.json"
            )
            usual_file.parent.mkdir(parents=True, exist_ok=True)
            usual_file.write_text(
                json.dumps(
                    {"editor_name": "Scout", "spoken_replies": True}
                ),
                encoding="utf-8",
            )
            status, data = case.get_json("/api/editor-name")
            self.assertEqual(status, 200)
            self.assertEqual(data["editor_name"], "Scout")
            status, _text = case.get_text("/")
            self.assertEqual(status, 200)
        finally:
            case.tearDown()


if __name__ == "__main__":
    unittest.main()
