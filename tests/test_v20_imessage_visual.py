"""Tests for v0.20 Chunk A2: the iMessage visual pass.

Justin pointed at his own Muse chat and confirmed: "I
want this experience built into the editor platform." The
pass is a visual one, modeled on that screenshot (saved
with the 0.20 work papers):

- The Editor's avatar and name sit centered at the top of
  the thread, like the contact at the top of a text
  conversation, on the start screen chat and on the
  editing page alike.
- His messages ride in filled blue bubbles on the right;
  the Editor's in plain dark bubbles on the left. (Before
  this pass the pairing was the other way around: his
  dark, the Editor's blue tinted.)
- While the Editor works on a reply, three dots breathe
  in a small bubble; on the start screen the dots appear
  in the flow for a beat before the canned answer lands.
- One message bar sits at the bottom of each thread with
  a round send button.

Nothing about the chat logic changed: the same routes,
the same payloads, the same per-message Copy, Reply and
Undo menu beside each Editor bubble, rename by
double-clicking the name, Coach mode, and the Chunk A
canned answers including the name answer.
"""

from __future__ import annotations

import json
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.webapp import WebAppConfig, create_server  # noqa: E402

PAGE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")


def between(start: str, end: str, text: str = PAGE) -> str:
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def rule(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", PAGE)
    assert match, selector
    return match.group(1)


class BubblePairingTests(unittest.TestCase):
    def test_home_bubbles_pair_blue_right_dark_left(self):
        q = rule(".home-chat-line.q")
        self.assertIn("align-self: flex-end", q)
        self.assertIn("background: var(--blue)", q)
        self.assertIn("color: #fff", q)
        a = rule(".home-chat-line.a")
        self.assertIn("align-self: flex-start", a)
        self.assertIn("background: var(--panel-2)", a)

    def test_thread_bubbles_pair_blue_right_dark_left(self):
        creator = rule(".thread-creator-bubble")
        self.assertIn("align-self: flex-end", creator)
        self.assertIn("background: var(--blue)", creator)
        self.assertIn("color: #fff", creator)
        # The base creator class rides on the same element;
        # it must not repaint the bubble dark underneath.
        self.assertIn("background: var(--blue)", rule(".thread-creator"))
        editor = rule(".thread-editor-bubble")
        self.assertIn("align-self: flex-start", editor)
        self.assertIn("background: var(--panel-2)", editor)

    def test_bubbles_keep_comfortable_padding(self):
        self.assertIn("padding: 10px 14px", rule(".thread-bubble"))
        self.assertIn("padding: 10px 14px", rule(".home-chat-line"))


class CenteredContactTests(unittest.TestCase):
    def test_home_head_centers_the_face_and_name(self):
        head = rule(".home-chat-head")
        self.assertIn("flex-direction: column", head)
        self.assertIn("align-items: center", head)
        self.assertIn("text-align: center", head)
        self.assertIn("width: 46px", rule("#homeEditorAvatar"))

    def test_editor_head_groups_the_face_and_name_centered(self):
        head = between('id="commentsHead"', 'id="threadList"')
        self.assertIn('class="editor-identity"', head)
        identity = between('class="editor-identity"', 'id="commentsHeadCount"')
        self.assertIn('id="editorAvatar"', identity)
        self.assertIn('id="editorName"', identity)
        self.assertIn('id="editorNameInput"', identity)
        css = rule(".editor-identity")
        self.assertIn("margin-left: auto", css)
        self.assertIn("margin-right: auto", css)


class TypingIndicatorTests(unittest.TestCase):
    def test_thread_typing_bubble_is_in_the_markup(self):
        self.assertIn('id="editorTyping"', PAGE)
        typing = between('id="editorTyping"', 'id="editorNewReply"')
        self.assertEqual(typing.count('class="typing-dot"'), 3)
        self.assertIn("thread-typing[hidden]", PAGE)
        self.assertIn("animation: typingPulse", rule(".typing-dot"))

    def test_send_shows_and_hides_the_typing_dots(self):
        send = between("function sendEditorMessage", "var editorNewReplyBtn")
        self.assertIn('setEditorStatus("Working on it")', send)
        self.assertIn("showEditorTyping()", send)
        self.assertEqual(send.count("hideEditorTyping()"), 2)
        self.assertIn(
            'document.getElementById("editorTyping")', PAGE)

    def test_home_ask_types_then_answers(self):
        # Run the real homeChatAsk in node against a small
        # stand-in document: the question lands, a typing
        # bubble with three dots follows, and once the beat
        # passes the typing bubble goes away and the answer
        # bubble takes its place.
        flow = between("var HOME_CHAT_ANSWERS", "var homeChatSend")
        body = (
            "function makeEl() {\n"
            "  return {\n"
            "    className: '', textContent: '', children: [],\n"
            "    removed: false,\n"
            "    appendChild: function (c) {"
            " this.children.push(c); return c; },\n"
            "    remove: function () { this.removed = true; },\n"
            "    setAttribute: function () {}\n"
            "  };\n"
            "}\n"
            "var logEl = makeEl();\n"
            "var pending = [];\n"
            "var document = {\n"
            "  getElementById: function (id) {"
            " return id === 'homeChatLog' ? logEl : null; },\n"
            "  createElement: function () { return makeEl(); }\n"
            "};\n"
            "var window = { setTimeout: function (fn) {"
            " pending.push(fn); } };\n"
            'var editorNameStr = "Editor";\n'
            + flow
            + "\nconst assert = require('assert');\n"
            "homeChatAsk('What can you do?');\n"
            "assert.strictEqual(logEl.children.length, 2);\n"
            "assert.strictEqual(logEl.children[0].className,"
            " 'home-chat-line q');\n"
            "var typing = logEl.children[1];\n"
            "assert.ok(typing.className.indexOf('home-chat-typing')"
            " >= 0, typing.className);\n"
            "assert.strictEqual(typing.children[0].children.length, 3);\n"
            "assert.strictEqual(pending.length, 1);\n"
            "pending[0]();\n"
            "assert.strictEqual(typing.removed, true);\n"
            "assert.strictEqual(logEl.children.length, 3);\n"
            "var answer = logEl.children[2];\n"
            "assert.strictEqual(answer.className, 'home-chat-line a');\n"
            "assert.ok(answer.textContent.indexOf("
            "'I take a video you bring in') === 0);\n"
            "console.log('home-typing-ok');\n"
        )
        script = Path(tempfile.mkdtemp(prefix="tye-v20-a2-")) / "check.js"
        script.write_text(body, encoding="utf-8")
        result = subprocess.run(
            ["node", str(script)], capture_output=True, text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("home-typing-ok", result.stdout)


class MessageBarTests(unittest.TestCase):
    def test_home_composer_is_a_bar_with_a_round_send(self):
        card = between('id="homeChatCard"', 'id="runOverlay"')
        self.assertIn('class="home-chat-composer"', card)
        self.assertIn('id="homeChatSend" class="home-chat-send"', card)
        send = rule(".home-chat-send")
        self.assertIn("border-radius: 999px", send)
        self.assertIn("background: var(--blue)", send)
        self.assertIn(
            "border-radius: 999px", rule(".home-chat-composer #homeChatInput"))

    def test_editor_composer_keeps_its_round_send(self):
        panel = between('id="editorPanel"', 'id="reviewCard"')
        self.assertIn('id="editorInputRow"', panel)
        self.assertIn('class="primary composer-btn editor-send"', panel)
        self.assertIn("border-radius: 999px", rule("#save.editor-send"))


class BehaviorKeptTests(unittest.TestCase):
    def test_message_actions_still_ride_each_editor_bubble(self):
        entry = between("function threadEntryEl", "// -- Clean-up quick actions")
        self.assertIn("threadMsgMenuBtn(entry)", entry)
        self.assertIn('row.className = "thread-bubble-row"', entry)

    def test_rename_still_on_both_heads(self):
        self.assertIn(
            'editorNameBtn.addEventListener("dblclick", '
            "startEditorRename)", PAGE)
        self.assertIn(
            'homeEditorNameBtn.addEventListener("dblclick", '
            "startHomeEditorRename)", PAGE)


class ServedPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v20-a2-"))
        self.home = self.tmp / "launch-home"
        self.server = create_server(
            WebAppConfig(None, launch_home=self.home),
            host="127.0.0.1", port=0,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_served_page_carries_the_imessage_thread(self):
        req = urllib.request.Request(self.base + "/")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            self.fail(f"GET / -> {exc.code}")
        self.assertIn('id="editorTyping"', text)
        self.assertIn('class="editor-identity"', text)
        self.assertIn('class="home-chat-composer"', text)
        self.assertIn('id="homeEditorAvatar"', text)


if __name__ == "__main__":
    unittest.main()
