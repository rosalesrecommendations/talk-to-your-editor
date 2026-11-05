"""v0.15 Batch B: the selection system (items 18, 19, 20, 21,
27, 28, 29).

Item 19 is the root-cause fix: dismissal of every transient
surface (the pick and its action bar, the overflow menu, the
"Recommended to remove" card) has ONE owner — a document
pointerdown handler plus one Escape handler. Before this
batch the pick cleared only from three wired panes and the
card had no outside-press close at all, so both survived
click-away in real use (proven by a headless reproduction on
the saved IMG_2516 job; see v015-work/RUNNING-NOTES.md).

These tests pin the owner's shape, the drag and tap rules
that arm a pick, the slimmed gold card, and the renamed
actions, in the static style of the other template tests.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "src" / "talk_to_your_editor" / "templates"
            / "test_app.html").read_text(encoding="utf-8")


def between(start, end):
    i = TEMPLATE.index(start)
    return TEMPLATE[i:TEMPLATE.index(end, i)]


class DismissalOwnerTests(unittest.TestCase):
    def owner(self):
        return between("// -- ONE dismissal owner",
                       "// The 0.12 one-tap restore")

    def test_one_pointerdown_owner_covers_bar_menu_and_card(self):
        body = self.owner()
        self.assertIn('document.addEventListener("pointerdown"',
                      body)
        self.assertIn("closeSelMenu();", body)
        self.assertIn("closeEditPopup();", body)
        self.assertIn("clearSelection();", body)

    def test_the_owner_keeps_the_anchors_own_toggle(self):
        body = self.owner()
        # A press on the card's own anchor is not a dismissal:
        # the anchor's click keeps its open/close toggle.
        self.assertIn("popupAnchor.contains(t)", body)
        # Presses inside the bar are the bar's own business.
        self.assertIn('selBubble.contains(t)', body)
        # A press on a word is a pick gesture, not a dismissal.
        self.assertIn('t.closest(".tw")', body)

    def test_one_escape_owner_folds_menu_then_card_then_pick(self):
        body = self.owner()
        esc = body[body.index('document.addEventListener("keydown"'):]
        self.assertIn("closeSelMenu(); return;", esc)
        self.assertIn("closeEditPopup();", esc)
        self.assertIn("refocusAnchorWithoutReopening(anchor);", esc)
        self.assertIn("clearSelection();", esc)
        order = [esc.index("closeSelMenu(); return;"),
                 esc.index("closeEditPopup();"),
                 esc.index("clearSelection();")]
        self.assertEqual(order, sorted(order))

    def test_the_three_pane_handler_is_gone(self):
        self.assertNotIn(
            '["vidWrap", "timelineBand", "toolsPane"]', TEMPLATE)
        self.assertNotIn('pane.addEventListener("pointerdown"',
                         TEMPLATE)

    def test_the_popup_has_no_second_escape_handler(self):
        wire = between("function wirePopupAnchor",
                       "function openGoldPopup")
        self.assertNotIn('addEventListener("keydown"', wire)


class DragAndTapTests(unittest.TestCase):
    def test_a_drag_ending_outside_the_transcript_arms_nothing(self):
        body = between('document.addEventListener("pointerup"',
                       'document.addEventListener("pointercancel"')
        # The release point decides: inside the transcript the
        # bar opens; anywhere else the painted pick is dropped.
        self.assertIn("document.elementFromPoint(e.clientX, "
                      "e.clientY)", body)
        self.assertIn("transcriptPanel.contains(el)", body)
        self.assertIn("updateSelBar();", body)
        self.assertIn("selStart = null;", body)
        self.assertIn("refreshFixSelection();", body)

    def test_a_cancelled_drag_also_drops_the_painted_pick(self):
        body = between('document.addEventListener("pointercancel"',
                       "// Clicking a word outside fix mode")
        self.assertIn("selStart = null;", body)
        self.assertIn("refreshFixSelection();", body)

    def test_spaced_taps_within_900ms_pick_the_word(self):
        body = between('transcriptEl.addEventListener("click"',
                       'transcriptEl.addEventListener("dblclick"')
        self.assertIn("lastWordTap.index === index", body)
        self.assertIn("<= 900", body)
        self.assertIn("selStart = index;", body)
        self.assertIn("selEnd = spanEnd;", body)


class GoldCardSlimTests(unittest.TestCase):
    def test_gold_mode_hides_the_note_box(self):
        body = between("function setPopupGoldMode(on)",
                       "function wirePopupAnchor")
        self.assertIn("editNoteText.hidden = !!on;", body)
        self.assertIn("editNoteSpeak.hidden = !!on;", body)

    def test_the_card_shows_the_advice_line_not_the_explanation(self):
        body = between("function openGoldPopup",
                       "function runGoldVerdict")
        self.assertIn(
            '"Earlier take, said again right after. Cut it, or '
            'leave it as is."', body)
        self.assertIn('span.kind === "retake"', body)
        # The take-that-stayed block stays out of the slim card.
        self.assertIn("editPopupKept.hidden = true;", body)

    def test_the_card_keeps_its_three_controls(self):
        card = between('id="editPopup"', "</div>\n\n<script>")
        for marker in ('id="editGoldCut"', "Cut it",
                       'id="editGoldLeave"', "Leave it",
                       'id="editPopupClose"', "Close"):
            self.assertIn(marker, card)


class RenameTests(unittest.TestCase):
    def test_no_old_names_remain(self):
        self.assertNotIn("Fix the words", TEMPLATE)
        self.assertNotIn("Clear the pick", TEMPLATE)
        self.assertNotIn('id="selMenuClear"', TEMPLATE)
        self.assertNotIn('id="selMenuHide"', TEMPLATE)

    def test_change_words_is_the_menu_name(self):
        menu = between('id="selMenu"', 'id="fixPanel"')
        self.assertIn("Change words", menu)
        self.assertIn('id="selMenuFix"', menu)

    def test_erase_is_the_menu_name_with_its_tooltip(self):
        menu = between('id="selMenu"', 'id="fixPanel"')
        self.assertIn('id="selMenuErase"', menu)
        self.assertIn('<span class="sel-mi-label">Erase</span>',
                      menu)
        tag = re.search(r'<button id="selMenuErase"[^>]*>',
                        menu).group(0)
        self.assertIn('title="Erase these words (you can undo)"',
                      tag)
        self.assertIn("function runBarErase()", TEMPLATE)
        self.assertIn('"Erased " + words', TEMPLATE)

    def test_the_bar_close_control_is_an_x_named_done(self):
        tag = re.search(r'<button id="selCloseBtn"[^>]*>',
                        TEMPLATE).group(0)
        self.assertIn('title="Done"', tag)
        self.assertIn('aria-label="Done"', tag)
        body = between('document.getElementById("selCloseBtn")',
                       "if (selCloseBtn)")
        self.assertIn("getElementById", body)
        wire = between("if (selCloseBtn) {",
                       "// -- ONE dismissal owner")
        self.assertIn("clearSelection();", wire)

    def test_change_words_wording_reaches_the_fix_panel(self):
        self.assertIn("part to change", TEMPLATE)
        self.assertNotIn("part to fix", TEMPLATE)
        self.assertNotIn("Fixing words changes", TEMPLATE)
        self.assertIn("Changing words changes", TEMPLATE)
        self.assertNotIn("words to fix first", TEMPLATE)


class TooltipAndHeaderTests(unittest.TestCase):
    def test_transcript_markers_carry_plain_word_tooltips(self):
        self.assertIn('dot.title = "Why these words were '
                      'removed, and notes on this edit";', TEMPLATE)
        self.assertIn('marker.title = "Also noticed: "',
                      TEMPLATE)
        self.assertIn('marker.title = "Your call: "',
                      TEMPLATE)
        self.assertIn('time.title = "Play from " + '
                      'fmtTransport(et);', TEMPLATE)

    def test_the_teach_button_never_gets_text_written_into_it(self):
        body = between(
            'var teachToggle = document.getElementById('
            '"teachToggle");',
            "var loadSampleBtn")
        self.assertNotIn("teachToggle.textContent", body)
        self.assertIn('teachToggle.setAttribute("aria-expanded"',
                      body)


if __name__ == "__main__":
    unittest.main()
