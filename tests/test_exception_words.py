"""Tests for the after-5:30 exception, item 1: transcript word
editing in Studio (stdlib unittest, no dependencies).

Justin's ruling (2026-11-04, after the 5:30 kit): selecting
words in the transcript and pressing Backspace or Delete is
"Cut these words", and a small toolbar above any selection
offers Cut, Bring back, and Comment. Both run through the
existing word-range plan path (the same operation chat cuts
use), land under one checkpoint, and Undo restores the exact
words. Comment is the existing pin-a-note flow: the
selection's timestamp rides the next chat message.

The page tests read the served Studio template; the editing
tests run the same backend function the page's posts call.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import checkpoints, webapp
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig

FIXTURES = Path(__file__).parent / "fixtures"
STUDIO = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "studio.html"
)


def studio_html() -> str:
    return STUDIO.read_text(encoding="utf-8")


def word_status(config: WebAppConfig, index: int) -> str:
    transcript = webapp._load_json(str(config.job_dir / "transcript.json"))
    timeline = Timeline(transcript, webapp._effective_cuts(config))
    return timeline.word_status(index)


class SelectionToolbarPageTests(unittest.TestCase):
    def test_toolbar_markup_carries_the_three_actions(self):
        html = studio_html()
        self.assertIn('id="selBar"', html)
        self.assertIn('id="selCut"', html)
        self.assertIn('id="selRestore"', html)
        self.assertIn('id="selComment"', html)
        self.assertIn(">Cut<", html)
        self.assertIn(">Bring back<", html)
        self.assertIn(">Comment<", html)

    def test_toolbar_is_painted_from_the_pick_repaint(self):
        html = studio_html()
        self.assertIn("function paintSelBar()", html)
        pick = html.index("function paintPick()")
        self.assertIn("paintSelBar();", html[pick:pick + 4000])

    def test_cut_and_bring_back_run_the_word_range_operation(self):
        html = studio_html()
        start = html.index("function selWordRange(action)")
        handler = html[start:html.index("tx.addEventListener(\"scroll\"", start)]
        self.assertIn('"/api/word-range"', handler)
        self.assertIn("word_start: E.sel.a", handler)
        self.assertIn("word_end: E.sel.b", handler)
        self.assertIn('$("selCut").addEventListener("click"', html)
        self.assertIn('selWordRange("cut")', html)
        self.assertIn('$("selRestore").addEventListener("click"', html)
        self.assertIn('selWordRange("restore")', html)

    def test_comment_is_the_pin_a_note_flow(self):
        html = studio_html()
        start = html.index('$("selComment").addEventListener("click"')
        handler = html[start:html.index("tx.addEventListener(\"scroll\"", start)]
        # The selection's span and timestamp become the pin the
        # next chat message carries; the input takes focus.
        self.assertIn("E.pin = ", handler)
        self.assertIn("word_start: E.sel.a", handler)
        self.assertIn("word_end: E.sel.b", handler)
        self.assertIn("renderChips()", handler)
        self.assertIn("chat.focusInput()", handler)

    def test_backspace_and_delete_cut_the_selection(self):
        html = studio_html()
        # The exception's listener is the one wired beside the
        # selection toolbar, after the scroll listener (an
        # earlier keydown listener serves other keys).
        scroll = html.index('tx.addEventListener("scroll"')
        start = html.index('document.addEventListener("keydown"', scroll)
        handler = html[start:html.index("});", start) + 3]
        self.assertIn('e.key !== "Backspace"', handler)
        self.assertIn('e.key !== "Delete"', handler)
        # Typing in a field keeps its own keys.
        self.assertIn('"TEXTAREA"', handler)
        self.assertIn("isContentEditable", handler)
        self.assertLess(
            handler.index('"TEXTAREA"'), handler.index("e.preventDefault()")
        )
        self.assertIn('selWordRange("cut")', handler)

    def test_no_editor_voice_crept_in(self):
        self.assertEqual(studio_html().count("speechSynthesis"), 0)


class SelectionCutUndoTests(unittest.TestCase):
    """The backend path the toolbar and keys post to."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-exception-"))
        job = self.tmp / "job"
        job.mkdir(parents=True)
        for name, dest in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / name, job / dest)
        self.config = WebAppConfig(job)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_selection_cut_moves_exactly_the_selected_words(self):
        result = webapp._apply_word_range(self.config, 16, 24, "cut")
        self.assertEqual(result["moved"], 8)
        for index in range(16, 24):
            self.assertEqual(word_status(self.config, index), "cut")
        # The words outside the selection do not move: 24 and
        # 25 were kept and stay kept; 15 was already cut in the
        # sample and stays cut.
        self.assertEqual(word_status(self.config, 24), "kept")
        self.assertEqual(word_status(self.config, 25), "kept")
        self.assertEqual(word_status(self.config, 15), "cut")

    def test_undo_restores_the_exact_words_in_one_step(self):
        webapp._apply_word_range(self.config, 16, 24, "cut")
        state = checkpoints.undo(self.config)
        self.assertIsNotNone(state)
        for index in range(16, 24):
            self.assertEqual(word_status(self.config, index), "kept")

    def test_bring_back_restores_exactly_the_selection(self):
        webapp._apply_word_range(self.config, 16, 24, "cut")
        result = webapp._apply_word_range(self.config, 18, 21, "restore")
        self.assertEqual(result["moved"], 3)
        for index in (18, 19, 20):
            self.assertEqual(word_status(self.config, index), "kept")
        for index in (16, 17, 21, 22, 23):
            self.assertEqual(word_status(self.config, index), "cut")


if __name__ == "__main__":
    unittest.main()
