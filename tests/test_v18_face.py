"""Tests for v0.18 Builder A: the Editor's face in the thread.

Covers success criteria 1 to 3 of the v0.18 plan:

1. Editor identity: a round avatar with the clapper board
   emoji, not the old letter avatar; the name sits with the
   avatar, defaults to "Editor", and double-clicking it turns
   it into an editable field whose save persists through the
   app's usual file (the same store the rename has always
   used) and is the name the thread view carries.
2. Conversation feel: opening a job shows one warm greeting
   from the Editor; creator messages sit on the right and
   Editor messages on the left, as rounded bubbles.
3. Spoken replies shipped here in v0.18 and were removed in
   v0.20 at Justin's direction (text-only chat); the removal
   has its own tests in test_v20_identity_text_chat.py.

The greeting is rendered by the page at the top of the thread
list, once per render; it is not a stored thread entry, so the
server thread counts other builders rely on do not change.
"""

from __future__ import annotations

import json
import os
import re
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

from talk_to_your_editor import usual, webapp  # noqa: E402
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
    ["Hello there this is the first line of the video today."]
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


class FaceServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v18-face-"))
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

    def get_text(self, path):
        status, body = self.request(path)
        return status, body.decode("utf-8")


class AvatarTests(unittest.TestCase):
    def test_avatar_is_the_clapper_board_not_the_letter(self):
        head = between('id="commentsHead"', 'id="threadList"')
        self.assertIn('id="editorAvatar"', head)
        self.assertIn(
            '<span class="editor-avatar" id="editorAvatar" '
            'aria-hidden="true">🎬</span>',
            head,
        )
        self.assertNotIn('aria-hidden="true">E</span>', head)
        # The render no longer rewrites the avatar to the first
        # letter of the name.
        self.assertNotIn("avatar.textContent = (editorNameStr", PAGE)
        self.assertIn('avatar.textContent = "🎬"', PAGE)
        # Round, as before.
        self.assertIn("border-radius: 999px", rule(".editor-avatar"))

    def test_served_page_carries_the_clapper_board_avatar(self):
        case = FaceServerCase("run")
        case.setUp()
        try:
            status, text = case.get_text("/")
            self.assertEqual(status, 200)
            self.assertIn('id="editorAvatar" aria-hidden="true">🎬<', text)
        finally:
            case.tearDown()


class RenameTests(FaceServerCase):
    def test_double_click_turns_the_name_into_an_editable_field(self):
        self.assertIn('id="editorNameInput"', PAGE)
        self.assertIn('class="editor-name-input"', PAGE)
        self.assertIn(
            'editorNameBtn.addEventListener("dblclick", startEditorRename)',
            PAGE,
        )
        rename = between("function startEditorRename", 'transcriptEl.addEventListener("dblclick"')
        self.assertIn("editorNameInput.hidden = false", rename)
        self.assertIn("editorNameBtn.hidden = true", rename)
        # Saving posts to the existing name endpoint.
        self.assertIn('postJson("/api/editor-name", { name: next })', rename)
        # The old click prompt is gone, so a double-click cannot
        # open prompts on its way to the field.
        self.assertNotIn('window.prompt("What should the editor be called?"', PAGE)

    def test_rename_persists_through_the_usual_and_names_the_thread(self):
        status, data = self.post_json("/api/editor-name", {"name": "Scout"})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["editor_name"], "Scout")
        # The app's existing persistence for user choices: the
        # usual file in the app's home folder.
        self.assertEqual(usual.load_editor_name(), "Scout")
        on_disk = json.loads(
            (self.home / ".talk-to-your-editor" / "usual.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(on_disk["editor_name"], "Scout")
        # The thread view for this job and for a different job
        # (a page reload, a new job) uses the new name.
        status, view = self.get_json("/api/thread")
        self.assertEqual(status, 200)
        self.assertEqual(view["editor_name"], "Scout")
        other = self.tmp / "other-job"
        write_job(other, WORDS)
        other_config = WebAppConfig(other, launch_home=self.tmp / "launch")
        self.assertEqual(
            webapp._thread_view(other_config)["editor_name"], "Scout"
        )

    def test_one_name_everywhere_the_page_names_the_editor(self):
        render = between("function renderThreadView", "function refreshThread")
        # Head name, top bar word and greeting all read the same
        # editorNameStr the thread view set.
        self.assertIn("nameBtn.textContent = editorNameStr", render)
        self.assertIn("word.textContent = editorNameStr", render)
        greeting = between("function threadGreetingEl", "function renderThreadView")
        self.assertIn('"Hi, I am " + editorNameStr', greeting)


class GreetingAndBubbleTests(unittest.TestCase):
    def test_greeting_entry_present_when_a_job_opens(self):
        greeting = between("function threadGreetingEl", "function renderThreadView")
        self.assertIn('data-thread-greeting", "true"', PAGE)
        self.assertIn("your editor", greeting)
        self.assertIn("glad you are here", greeting)
        # renderThreadView puts the greeting at the top of the
        # list, ahead of the stored entries, exactly once per
        # render (the list is cleared first).
        render = between("function renderThreadView", "function refreshThread")
        self.assertLess(
            render.index("threadListEl.appendChild(threadGreetingEl())"),
            render.index("entries.forEach"),
        )
        self.assertEqual(render.count("threadGreetingEl()"), 1)

    def test_greeting_is_render_only_not_a_stored_entry(self):
        # The server thread store and view gain no greeting
        # entry; other builders count on the stored entries.
        self.assertNotIn("greeting", webapp._thread_view.__doc__ or "")
        case = FaceServerCase("run")
        case.setUp()
        try:
            status, view = case.get_json("/api/thread")
            self.assertEqual(status, 200)
            kinds = [e.get("kind") for e in view["entries"]]
            self.assertNotIn("greeting", kinds)
        finally:
            case.tearDown()

    def test_bubble_alignment_classes_for_creator_and_editor(self):
        creator_css = rule(".thread-creator-bubble")
        self.assertIn("align-self: flex-end", creator_css)
        editor_css = rule(".thread-editor-bubble")
        self.assertIn("align-self: flex-start", editor_css)
        # Both sides are rounded bubbles.
        self.assertIn("border-radius: 16px", rule(".thread-bubble"))
        entry = between("function threadEntryEl", "// -- Clean-up quick actions")
        self.assertIn('" thread-entry-creator"', entry)
        self.assertIn('" thread-entry-editor"', entry)
        self.assertIn(
            '"thread-creator thread-bubble thread-creator-bubble"', entry
        )
        self.assertIn('"thread-bubble thread-editor-bubble"', entry)

    def test_existing_thread_kinds_still_render_inside_the_bubbles(self):
        entry = between("function threadEntryEl", "// -- Clean-up quick actions")
        for kind in ('"held"', '"pin"', '"why"', '"run"'):
            self.assertIn(f"entry.kind === {kind}", entry)
        self.assertIn("threadClaimBtn(claim)", entry)
        self.assertIn("settleHeldFromThread(entry,", PAGE)

    def test_one_plain_status_line_while_the_editor_works(self):
        self.assertEqual(PAGE.count('id="editorStatus"'), 1)
        send = between("function sendEditorMessage", "var editorNewReplyBtn")
        self.assertIn('setEditorStatus("Working on it")', send)
        self.assertIn("idleEditorStatus()", send)

    def test_undone_and_contract_fields_have_a_minimal_render_hook(self):
        entry = between("function threadEntryEl", "// -- Clean-up quick actions")
        self.assertIn('wrap.classList.add("thread-undone")', entry)
        self.assertIn('setAttribute("data-reply-to"', entry)
        self.assertIn('setAttribute("data-action-id"', entry)
        undone_css = PAGE[PAGE.index(".thread-undone .thread-editor"):]
        self.assertIn(
            "text-decoration: line-through", undone_css.split("}", 1)[0]
        )


if __name__ == "__main__":
    unittest.main()
