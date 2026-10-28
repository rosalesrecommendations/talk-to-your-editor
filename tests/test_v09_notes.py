"""Tests for v0.9.0 Cluster 3: notes and popovers (items 5, 6, 7,
20, 22, 23).

The failure these pin, from Justin's 2026-10-27 session: he typed
a note on the 6:48 retake span, the popover vanished on him while
he was still writing, and when he opened a different span near
8:24 the same words were rendered inside that span's popover,
directly under its "No notes on this edit yet" line. Close then
appeared not to work on the first click.

Root causes found in the page (templates/test_app.html):

- Item 6: ONE shared textarea served every popover and was never
  scoped to a target. openEditPopup did not reset it, close did
  not clear it, and openGoldPopup wiped it, so text typed on one
  span showed (and could be saved) on another. The server store
  was already keyed by edit id; the leak was the shared client
  box. The fix keeps drafts per target in a note-box state module
  (createNoteBox, between the tye-note-box markers in the page);
  its behavior is exercised for real under Node below.
- Item 5: anchor and popover mouseleave timers closed the editor
  after 250 ms, and scrolling the transcript closed it too. Those
  closes are gone; only Save and Close close it, and an unsaved
  draft stays on its own span across close and reopen.
- Item 7: Close hid the popover and then returned focus to the
  anchor, whose focus handler immediately reopened it. The
  refocus now runs through a suppression flag the focus handler
  honors first.
- Item 22: the browser voice-typing button and its
  SpeechRecognition wiring are removed entirely.
- Item 23: the popover is compact (272px, small inline buttons)
  and its positioning falls back to beside the words when above
  and below would both cover them.
- Item 20: clicking the timeline surfaces a comment button at
  that spot; the picked-words strip gains a note button; both
  open the Pin a comment editor pinned to that time or word
  range, and a word range is stored with the comment server-side
  (both ends or neither) and rendered only at those words.
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

from talk_to_your_editor.webapp import WebAppConfig, create_server

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
)
NODE = shutil.which("node")
VIDEO_BYTES = bytes(range(256)) * 32


def page_html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def segment(html: str, start_marker: str, end_marker: str) -> str:
    start = html.index(start_marker)
    end = html.index(end_marker, start)
    return html[start:end]


# ---------------------------------------------------------------------------
# The note-box state module, exercised for real under Node.
# ---------------------------------------------------------------------------

NOTE_BOX_SCENARIO = r"""
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const begin = html.indexOf("// [tye-note-box-begin]");
const end = html.indexOf("// [tye-note-box-end]");
if (begin < 0 || end < 0 || end <= begin) {
  console.log(JSON.stringify({ error: "note-box markers missing" }));
  process.exit(0);
}
eval(html.slice(begin, end));
const box = createNoteBox();
const A = "edit:edit-16-24";   // the 6:48 retake span
const B = "edit:edit-40-48";   // the different span near 8:24
const G = "gold:gold-1";
const out = {};
// Write a note on span A.
out.shownA0 = box.openFor(A);
box.noteTyped("remove because the final sentence is at seven minutes");
// Item 6 regression: open span B; B must show no trace of A's note.
out.shownB = box.openFor(B);
out.notesListB = box.draftFor(B);
box.noteTyped("a note that belongs to B only");
// Back on A, A's own draft is what the box shows.
out.shownA1 = box.openFor(A);
out.draftB = box.draftFor(B);
// Item 5: clicking elsewhere / the mouse leaving never closes.
out.dismissReturns = box.dismissAttempt();
out.openAfterDismiss = box.isOpen();
box.noteTyped("remove because the final sentence is at seven minutes, still mine");
out.draftAafterDismiss = box.draftFor(A);
// Item 7 (state level): one close closes; drafts survive close.
out.closeReturns = box.close();
out.openAfterOneClose = box.isOpen();
out.draftAafterClose = box.draftFor(A);
out.reopenedA = box.openFor(A);
// A gold spot's note is its own target too.
box.openFor(G);
box.noteTyped("context for the gold call");
out.draftAwhileGold = box.draftFor(A);
out.shownB2 = box.openFor(B);
// Saving consumes only that target's draft.
box.saved(B);
out.draftBafterSave = box.draftFor(B);
out.draftAafterSaveB = box.draftFor(A);
out.draftGafterSaveB = box.draftFor(G);
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "needs node on PATH")
class NoteBoxBehaviorTests(unittest.TestCase):
    def run_scenario(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "scenario.js"
            script.write_text(NOTE_BOX_SCENARIO, encoding="utf-8")
            out = subprocess.run(
                [NODE, str(script), str(TEMPLATE)],
                capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads(out.stdout)
        self.assertNotIn("error", result, result)
        return result

    def test_note_written_on_span_a_never_shows_on_span_b(self):
        # THE item 6 regression: write on A, open B, no trace of A.
        result = self.run_scenario()
        self.assertEqual(result["shownA0"], "")
        self.assertEqual(result["shownB"], "")
        self.assertEqual(result["notesListB"], "")

    def test_each_span_keeps_its_own_draft(self):
        result = self.run_scenario()
        self.assertEqual(
            result["shownA1"],
            "remove because the final sentence is at seven minutes",
        )
        self.assertEqual(result["draftB"], "a note that belongs to B only")
        self.assertEqual(result["shownB2"], "a note that belongs to B only")

    def test_clicking_elsewhere_never_closes_or_loses_text(self):
        result = self.run_scenario()
        self.assertIs(result["dismissReturns"], False)
        self.assertIs(result["openAfterDismiss"], True)
        self.assertTrue(
            result["draftAafterDismiss"].startswith(
                "remove because the final sentence"
            )
        )

    def test_one_close_closes_and_the_draft_survives_it(self):
        result = self.run_scenario()
        self.assertIs(result["closeReturns"], True)
        self.assertIs(result["openAfterOneClose"], False)
        self.assertTrue(
            result["draftAafterClose"].startswith(
                "remove because the final sentence"
            )
        )
        # Reopening the same span hands the draft back (item 5).
        self.assertEqual(result["reopenedA"], result["draftAafterClose"])

    def test_gold_draft_is_separate_and_save_clears_only_its_own(self):
        result = self.run_scenario()
        self.assertTrue(
            result["draftAwhileGold"].startswith("remove because")
        )
        self.assertEqual(result["draftBafterSave"], "")
        self.assertTrue(result["draftAafterSaveB"].startswith("remove because"))
        self.assertEqual(
            result["draftGafterSaveB"], "context for the gold call"
        )


# ---------------------------------------------------------------------------
# Page wiring: static checks in the style of the existing template tests.
# ---------------------------------------------------------------------------


class PopoverWiringTests(unittest.TestCase):
    def test_open_edit_popup_loads_only_that_spans_draft(self):
        html = page_html()
        self.assertIn("noteBox.openFor(noteKeyForEdit(span))", html)
        self.assertIn("noteBox.openFor(noteKeyForGold(span))", html)
        # The old shared-box behavior: gold opening wiped the box.
        gold = segment(html, "function openGoldPopup", "function runGoldVerdict")
        self.assertNotIn('editNoteText.value = "";', gold)

    def test_typing_is_recorded_against_the_open_target(self):
        html = page_html()
        self.assertIn("noteBox.noteTyped(editNoteText.value)", html)
        # ...including the final sync before any close or switch.
        close = segment(html, "function closeEditPopup", "function positionEditPopup")
        self.assertIn("stashNoteDraft()", close)
        self.assertIn("noteBox.close()", close)

    def test_no_mouseleave_or_scroll_close_remains(self):
        html = page_html()
        self.assertNotIn(
            "setTimeout(function () { closeEditPopup(); }, 250)", html
        )
        self.assertNotIn('editPopup.addEventListener("mouseleave"', html)
        scroll = segment(
            html,
            'transcriptPanel.addEventListener("scroll"',
            'transcriptPanel.addEventListener("wheel"',
        )
        self.assertNotIn("closeEditPopup()", scroll)

    def test_save_button_is_plainly_labeled_save(self):
        html = page_html()
        self.assertRegex(
            html, r'<button id="editNoteSave"[^>]*>Save</button>'
        )
        self.assertIn(
            '<button id="editNoteSpeak" type="button">Dictate the note</button>',
            html,
        )

    def test_save_captures_its_edit_at_click_time(self):
        html = page_html()
        self.assertIn("var saveEditId = currentEditId;", html)
        self.assertIn(
            'postJson("/api/edit-note", { edit_id: saveEditId', html
        )
        # The late response re-renders only if its own edit is open.
        self.assertIn(
            'if (String(currentEditId) === String(saveEditId))', html
        )

    def test_close_click_closes_once_and_focus_cannot_reopen(self):
        html = page_html()
        handler = segment(
            html,
            "editPopupClose.addEventListener",
            "// Typing (and dictated text",
        )
        self.assertIn("closeEditPopup();", handler)
        self.assertIn("refocusAnchorWithoutReopening(anchor);", handler)
        self.assertNotIn("anchor.focus();", handler)
        # The helper suppresses BEFORE focusing, and the anchor's
        # focus handler honors the suppression before opening.
        helper = segment(
            html,
            "function refocusAnchorWithoutReopening",
            "// -- edit popup",
        )
        self.assertLess(
            helper.index("popupRefocusSuppress = true;"),
            helper.index("anchor.focus();"),
        )
        wiring = segment(
            html, "function wirePopupAnchor", "// The popover itself"
        )
        focus = segment(
            wiring, 'addEventListener("focus"', 'addEventListener("click"'
        )
        self.assertIn("popupRefocusSuppress", focus)
        self.assertLess(
            focus.index("popupRefocusSuppress"), focus.index("openFn(anchor)")
        )
        # Escape takes the same safe path.
        self.assertIn(
            "refocusAnchorWithoutReopening(anchor);",
            segment(html, 'e.key === "Escape"', "window.addEventListener"),
        )


class BrowserVoiceTypingRemovedTests(unittest.TestCase):
    def test_the_option_is_gone_everywhere_in_the_page(self):
        html = page_html()
        self.assertNotIn("Use the browser's voice typing instead", html)
        self.assertNotIn("editNoteSpeakBrowser", html)
        self.assertNotIn("SpeechRecognition", html)
        self.assertNotIn("voice typing did not work", html)
        # Dictate the note, the app's own path, is what remains.
        self.assertIn("Dictate the note", html)
        self.assertIn('wireDictation("editNoteSpeak"', html)


class CompactPopoverTests(unittest.TestCase):
    def test_popover_is_compact_with_small_inline_buttons(self):
        html = page_html()
        self.assertIn("width: min(272px, 92vw);", html)
        self.assertNotIn("width: min(320px, 92vw);", html)
        block = segment(html, "#editPopup button {", "}")
        self.assertIn("min-height: 30px;", block)
        self.assertIn("flex: 0 0 auto;", block)
        self.assertIn("font-size: 13px;", block)

    def test_positioning_steps_beside_words_it_would_cover(self):
        html = page_html()
        position = segment(
            html, "function positionEditPopup", "function setPopupFlagMode"
        )
        self.assertIn("overlapsSpan(top)", position)
        self.assertIn("spanRect.right + 10 + cardW", position)
        self.assertIn("spanRect.left - 10 - cardW", position)


class TimelineAndSelectionCommentTests(unittest.TestCase):
    def test_timeline_click_surfaces_a_comment_button_there(self):
        html = page_html()
        self.assertIn('id="tlCommentBtn"', html)
        self.assertIn("Leave a comment here", html)
        self.assertIn("showTimelineCommentButton(vid.currentTime || 0)", html)
        self.assertIn("pinDraft(tlCommentTs)", html)

    def test_selection_surfaces_a_note_button_for_those_words(self):
        html = page_html()
        self.assertIn('id="txCommentBtn"', html)
        self.assertIn("Leave a note on these words", html)
        self.assertIn("pinnedWordStart = selStart;", html)
        self.assertIn("pinnedWordEnd = selEnd;", html)

    def test_saved_comment_carries_its_word_target(self):
        html = page_html()
        self.assertIn("comment.word_start = pinnedWordStart;", html)
        self.assertIn("comment.word_end = pinnedWordEnd;", html)
        self.assertIn("comment.word_text = spanTextFor(", html)
        self.assertIn("On these words:", html)
        # ...and a time pin explicitly clears any earlier word pin.
        self.assertIn("pinnedWordStart = null;", html)

    def test_pin_a_comment_keeps_working(self):
        html = page_html()
        self.assertIn("<h2>Pin a comment</h2>", html)
        self.assertIn('id="pin"', html)
        self.assertIn("Comment at current time", html)
        self.assertIn('id="save"', html)


# ---------------------------------------------------------------------------
# Server side: notes and pinned comments over real HTTP.
# ---------------------------------------------------------------------------


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v09-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.job = self.tmp / "job"
        self.job.mkdir()
        for name, dest in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / name, self.job / dest)
        (self.job / "review.mp4").write_bytes(VIDEO_BYTES)
        self.config = WebAppConfig(self.job)
        self.server = create_server(self.config, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, path, data=None):
        payload = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(
            self.base + path,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def summary(self):
        status, payload = self.call("/api/job")
        self.assertEqual(status, 200, payload)
        return payload

    def cut_two_spans(self):
        # Two separate kept ranges become two separate edit spans:
        # A at words 0-4 and B at words 16-20.
        for start, end in ((0, 4), (16, 20)):
            status, result = self.call(
                "/api/word-range",
                {"word_start": start, "word_end": end, "action": "cut"},
            )
            self.assertEqual(status, 200, result)
            self.assertTrue(result["ok"])
        spans = {
            s["edit_id"]: s for s in self.summary()["edit_spans"]
        }
        self.assertIn("edit-0-4", spans)
        self.assertIn("edit-16-20", spans)
        return spans["edit-0-4"], spans["edit-16-20"]


class CrossSpanNoteServerTests(ServerCase):
    def test_note_on_span_a_never_appears_on_span_b(self):
        span_a, span_b = self.cut_two_spans()
        note_text = "remove because the final sentence is at seven minutes"
        status, payload = self.call(
            "/api/edit-note",
            {"edit_id": span_a["edit_id"], "text": note_text},
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])

        spans = {s["edit_id"]: s for s in self.summary()["edit_spans"]}
        self.assertEqual(
            [n["text"] for n in spans[span_a["edit_id"]]["notes"]],
            [note_text],
        )
        # Span B shows no trace of span A's note.
        self.assertEqual(spans[span_b["edit_id"]]["notes"], [])

        # A note on B lands on B only; A's list is unchanged.
        status, payload = self.call(
            "/api/edit-note",
            {"edit_id": span_b["edit_id"], "text": "B's own note"},
        )
        self.assertEqual(status, 200, payload)
        spans = {s["edit_id"]: s for s in self.summary()["edit_spans"]}
        self.assertEqual(
            [n["text"] for n in spans[span_a["edit_id"]]["notes"]],
            [note_text],
        )
        self.assertEqual(
            [n["text"] for n in spans[span_b["edit_id"]]["notes"]],
            ["B's own note"],
        )


class PinnedCommentTargetServerTests(ServerCase):
    def test_selection_comment_keeps_its_word_target(self):
        comments = [
            {
                "id": "sel1", "ts": 2.0, "fmt": "vertical",
                "scope": "both", "text": "these words drag",
                "word_start": 4, "word_end": 8,
                "word_text": "channel today my friends.",
            },
            {
                "id": "time1", "ts": 6.0, "fmt": "vertical",
                "scope": "both", "text": "a plain time pin",
            },
        ]
        status, payload = self.call(
            "/api/comments", {"video": "job", "comments": comments}
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"])

        by_id = {c["id"]: c for c in self.summary()["comments"]}
        self.assertEqual(by_id["sel1"]["word_start"], 4)
        self.assertEqual(by_id["sel1"]["word_end"], 8)
        self.assertEqual(
            by_id["sel1"]["word_text"], "channel today my friends."
        )
        # The time pin carries no word target.
        self.assertNotIn("word_start", by_id["time1"])
        self.assertNotIn("word_end", by_id["time1"])

        saved = json.loads(
            (self.job / "tye-session" / "feedback.json").read_text(
                encoding="utf-8"
            )
        )
        saved_by_id = {c["id"]: c for c in saved["comments"]}
        self.assertEqual(saved_by_id["sel1"]["word_start"], 4)
        self.assertEqual(saved_by_id["sel1"]["word_end"], 8)

    def test_half_or_empty_word_range_is_rejected(self):
        base = {
            "id": "bad", "ts": 2.0, "fmt": "vertical",
            "scope": "both", "text": "nowhere to land",
        }
        for extra in (
            {"word_start": 4},
            {"word_end": 8},
            {"word_start": 8, "word_end": 8},
            {"word_start": 9, "word_end": 8},
            {"word_start": -1, "word_end": 8},
        ):
            status, _payload = self.call(
                "/api/comments",
                {"video": "job", "comments": [{**base, **extra}]},
            )
            self.assertEqual(status, 400, extra)
        # Nothing was saved by the rejected posts.
        self.assertEqual(self.summary()["comments"], [])


if __name__ == "__main__":
    unittest.main()
