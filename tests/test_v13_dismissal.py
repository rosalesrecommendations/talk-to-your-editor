"""v0.13, item 5: composer and surface discipline — rebuilt
in v0.15 (Batch B, item 19) around the ONE dismissal owner.

History: 0.13 wired pointerdown handlers on three panes
(the video pane, the timeline band, the comments column) to
clear the pick when the reader left the transcript, and the
note popup kept a standing rule that an outside click never
closes it. Justin's 0.14 hand-test showed the regression:
clicking empty transcript space dismissed nothing, and the
"Recommended to remove" card (the same popup, in gold mode)
never dismissed on click-away at all. The root cause,
proven by a headless reproduction on the saved IMG_2516 job
(v015-work/RUNNING-NOTES.md): dismissal was scattered across
three mechanisms and two whole classes of press fell through
the gaps.

0.15 gives every transient surface ONE owner: a document
pointerdown handler plus one Escape handler in the bar's
wiring block. Its rules, pinned here at source level (the
fit battery, fit-test-015.mjs checks C1-C3 and [0.15B],
drives the real pointer events):

- a press outside the bar folds the overflow menu;
- a press outside the popup card closes it, except a press
  on the card's own anchor, whose click keeps its toggle;
- a press that is neither inside the bar nor on a transcript
  word clears the pick through clearSelection;
- no draft state is touched directly: the pin and the
  composer text are never named by the owner, and closing
  the card stashes its note draft first (closeEditPopup),
  so a note in progress survives click-away as a draft on
  its own spot instead of being discarded;
- the handler only listens: nothing is cancelled or
  stopped, so the band's seek and the panes' own controls
  run exactly as before.

Everything is offline.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")


def between(start_marker: str, end_marker: str) -> str:
    start = TEMPLATE.index(start_marker)
    end = TEMPLATE.index(end_marker, start)
    return TEMPLATE[start:end]


def dismissal() -> str:
    return between(
        "// -- ONE dismissal owner",
        "// The 0.12 one-tap restore")


class DismissalSourceTests(unittest.TestCase):
    def test_one_document_pointerdown_owner_for_every_surface(self):
        body = dismissal()
        self.assertIn('document.addEventListener("pointerdown"',
                      body)
        # The menu, the card, and the pick each close from it.
        self.assertIn("closeSelMenu();", body)
        self.assertIn("closeEditPopup();", body)
        self.assertIn("clearSelection();", body)
        # The scattered 0.13 mechanism is gone.
        self.assertNotIn(
            '["vidWrap", "timelineBand", "toolsPane"]', TEMPLATE)
        self.assertNotIn('pane.addEventListener("pointerdown"',
                         TEMPLATE)

    def test_the_dismissal_clears_the_pick_through_clear_selection(self):
        body = dismissal()
        # v0.15 Batch G (item 24): the owner also clears a
        # picked word gap, so the condition names pickedGap
        # alongside the word pick.
        self.assertIn(
            "(selStart !== null || selEnd !== null || pickedGap)",
            body)
        self.assertIn("clearSelection();", body)
        # clearSelection is what closes the bar: with no
        # pick, its refresh hides the surface.
        clear = between("function clearSelection()",
                        "// Comment on the pick")
        self.assertIn("updateSelBar();", clear)

    def test_the_anchor_and_the_bar_and_words_are_not_dismissals(self):
        body = dismissal()
        # The card's own anchor keeps its click toggle.
        self.assertIn("popupAnchor.contains(t)", body)
        # Presses inside the bar are the bar's own business.
        self.assertIn("selBubble.contains(t)", body)
        # A press on a word is a pick gesture.
        self.assertIn('t.closest(".tw")', body)

    def test_no_draft_state_is_touched(self):
        # The owner never names the pin, the composer text, or
        # the note box: those drafts are not its business.
        # The card's own close path stashes its draft first,
        # which is what makes click-away safe for a note in
        # progress (the v0.9.0 protection, kept).
        body = dismissal()
        self.assertNotIn("pinnedWordStart", body)
        self.assertNotIn("pinnedWordEnd", body)
        self.assertNotIn("editNoteText", body)
        close = between("function closeEditPopup()",
                        "function positionEditPopup()")
        self.assertIn("stashNoteDraft()", close)
        self.assertIn("noteBox.close()", close)

    def test_the_handler_only_listens(self):
        body = dismissal()
        # No cancelling, no stopping: the band's seek and the
        # panes' buttons must run as they always have.
        self.assertNotIn("preventDefault", body)
        self.assertNotIn("stopPropagation", body)

    def test_escape_is_the_same_owner(self):
        body = dismissal()
        esc = body[body.index('document.addEventListener("keydown"'):]
        self.assertIn("closeSelMenu(); return;", esc)
        self.assertIn("closeEditPopup();", esc)
        self.assertIn(
            "refocusAnchorWithoutReopening(anchor);", esc)
        self.assertIn("clearSelection();", esc)


if __name__ == "__main__":
    unittest.main()
