"""v0.13 item 3 (revised by Justin's Descript recording):
the one surface for a pick is a compact bar at the pick.

History: v0.12, change 4 shipped a selection bubble over
picked words, alongside the strip under the panel. 0.13's
first design merged them into the strip; Justin's Descript
recording reversed the shape — the surface is Descript's
selection toolbar instead. The bubble element is RESTYLED
into that bar, inside the transcript pane, and the strip is
deleted entirely. The bar carries a tiny word count, icon
buttons (Comment, Cut, one contextual slot: Bring back or
Keep the cut), a "…" overflow whose menu prints each
action's shortcut (Change words and Erase live only there),
and one decision line when the pick touches a decided or
flagged pair.

These tests pin the bar's shape, its action mapping
(mirrored in Python over the page's rules), the decision
line, the flag markers' click, Comment's pin path, the
clamped positioning, and Esc — plus what the revision must
not disturb: the hidden-words machinery, fix mode, the
shared word popup, and the pin composer.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "src" / "talk_to_your_editor" / "templates"
            / "test_app.html").read_text(encoding="utf-8")
STYLE = TEMPLATE[TEMPLATE.index("<style>"):TEMPLATE.index("</style>")]


def between(start, end):
    i = TEMPLATE.index(start)
    return TEMPLATE[i:TEMPLATE.index(end, i)]


# ---------------------------------------------------------------------------
# The action mapping, mirrored over the page's barActionsFor
# ---------------------------------------------------------------------------

def bar_actions(words, decided_overlap=False):
    """words: dicts with status and unsure_cut."""
    has_cut = any(w["status"] == "cut" for w in words)
    has_unsure = any(w["unsure_cut"] for w in words)
    all_kept = bool(words) and all(
        w["status"] == "kept" for w in words)
    context = None
    if has_unsure and not decided_overlap:
        context = "keep"
    elif has_cut:
        context = "restore"
    return {
        "cut": all_kept,
        "context": context,
        "comment": True,
        "fix": True,
        "erase": True,
    }


def W(status="kept", unsure=False):
    return {"status": status, "unsure_cut": unsure}


class BarMarkupTests(unittest.TestCase):
    def test_the_bar_lives_inside_the_transcript_panel(self):
        panel = between('id="transcriptPanel"', 'id="transcriptStatus"')
        self.assertIn('id="selBubble"', panel)
        self.assertIn('role="toolbar"', panel)
        self.assertIn('aria-label="Actions for the picked words"',
                      panel)

    def test_the_strip_is_gone_entirely(self):
        self.assertNotIn("txActionStrip", TEMPLATE)
        self.assertNotIn("txSelLabel", TEMPLATE)
        self.assertNotIn("txUndoHint", TEMPLATE)

    def test_the_bar_row_is_count_comment_cut_context_more(self):
        bar = between('id="selBubble"', 'id="selDecision"')
        for marker in ('id="selCount"', 'id="selCommentBtn"',
                       'id="selCutBtn"', 'id="selContextBtn"',
                       'id="selMoreBtn"', 'id="selCloseBtn"'):
            self.assertIn(marker, bar)
        order = [bar.index(m) for m in (
            'id="selCount"', 'id="selCommentBtn"', 'id="selCutBtn"',
            'id="selContextBtn"', 'id="selMoreBtn"',
            'id="selCloseBtn"')]
        self.assertEqual(order, sorted(order))

    def test_change_and_erase_live_only_in_the_overflow(self):
        bar_row = between('id="selBubble"', 'id="selDecision"')
        self.assertNotIn("selMenuFix", bar_row)
        self.assertNotIn("Change words", bar_row)
        menu = between('id="selMenu"', 'id="fixPanel"')
        for marker in ('id="selMenuCut"', 'id="selMenuComment"',
                       'id="selMenuFix"', 'id="selMenuErase"'):
            self.assertIn(marker, menu)
        # v0.15 item 27: the old clear-the-pick menu item is
        # deleted; the pick clears by clicking elsewhere, by
        # Escape, or by the bar's Done (x) control.
        self.assertNotIn('id="selMenuClear"', menu)
        self.assertNotIn("Clear the pick", menu)
        self.assertIn("Change words", menu)
        erase = re.search(r'<button id="selMenuErase"[^>]*>',
                          menu).group(0)
        self.assertIn('title="Erase these words (you can undo)"',
                      erase)

    def test_the_overflow_prints_each_shortcut(self):
        menu = between('id="selMenu"', 'id="fixPanel"')
        rows = re.findall(
            r'id="(selMenu\w+)"[^>]*>(.*?)</button>', menu, re.S)
        keys = {}
        for bid, body in rows:
            km = re.search(r'sel-mi-key">([^<]*)<', body)
            keys[bid] = km.group(1) if km else None
        # v0.15 Batch G (item 23): the Cut hint is the strike
        # chord now, matching the SHORTCUTS table.
        self.assertEqual(keys.get("selMenuCut"), "Cmd+Backspace")
        # v0.15 Batch E2, item 15: Cmd+Shift+M now runs the
        # Send to Editor carry, so the hint prints on that item;
        # the Comment item keeps no hint.
        self.assertEqual(keys.get("selMenuComment"), "")
        self.assertEqual(keys.get("selMenuSend"), "Cmd+Shift+M")
        self.assertEqual(keys.get("selMenuFix"), "")
        self.assertEqual(keys.get("selMenuErase"), "")

    def test_every_bar_button_is_an_icon_with_a_plain_name(self):
        for bid, name in (("selCommentBtn", "Comment"),
                          ("selCutBtn", "Cut"),
                          ("selCloseBtn", "Done"),
                          ("selMoreBtn", "More actions")):
            tag = re.search(r'<button id="%s"[^>]*>' % bid,
                            TEMPLATE).group(0)
            self.assertIn('title="%s"' % name, tag, bid)
            self.assertIn('aria-label="%s"' % name, tag, bid)
            i = TEMPLATE.index('id="%s"' % bid)
            self.assertIn("<svg", TEMPLATE[i:i + 700], bid)
        tag = re.search(r'<button id="selMoreBtn"[^>]*>',
                        TEMPLATE).group(0)
        self.assertIn('aria-haspopup="true"', tag)
        self.assertIn('aria-expanded="false"', tag)

    def test_the_context_slot_swaps_its_name_and_glyph(self):
        bar = between("function updateSelBar()",
                      "function clearSelection()")
        self.assertIn('keep ? "Keep the cut" : "Bring back"', bar)
        self.assertIn("selContextBtn.title = label;", bar)
        self.assertIn('querySelector(".sel-ico-restore")', bar)
        self.assertIn('querySelector(".sel-ico-keep")', bar)

    def test_the_count_is_tiny_text_only(self):
        bar = between("function updateSelBar()",
                      "function clearSelection()")
        self.assertIn('count === 1 ? " word" : " words"', bar)
        self.assertNotIn("words picked", bar)
        self.assertNotIn("these words are", bar)


class BarActionsMirrorTests(unittest.TestCase):
    def test_kept_words_offer_cut_and_no_context(self):
        a = bar_actions([W(), W()])
        self.assertTrue(a["cut"])
        self.assertIsNone(a["context"])

    def test_cut_words_offer_bring_back_not_cut(self):
        a = bar_actions([W("cut")])
        self.assertFalse(a["cut"])
        self.assertEqual(a["context"], "restore")

    def test_unsure_cut_offers_keep_the_cut(self):
        a = bar_actions([W("cut", unsure=True)])
        self.assertEqual(a["context"], "keep")

    def test_a_decided_pick_brings_back_even_with_unsure(self):
        a = bar_actions([W("cut", unsure=True)],
                        decided_overlap=True)
        self.assertEqual(a["context"], "restore")

    def test_a_mixed_pick_never_offers_cut(self):
        a = bar_actions([W(), W("cut")])
        self.assertFalse(a["cut"])
        self.assertEqual(a["context"], "restore")

    def test_the_page_mirrors_this_mapping(self):
        body = between("function barActionsFor(entries)",
                       "function decisionLineFor()")
        for marker in ("hasCut", "hasUnsure", "allKept",
                       'context = "keep"', 'context = "restore"',
                       "decided"):
            self.assertIn(marker, body)


class DecisionLineTests(unittest.TestCase):
    def test_the_decision_line_lives_in_the_bar(self):
        self.assertIn('id="selDecision"', TEMPLATE)
        body = between("function decisionLineFor()",
                       "function closeSelMenu()")
        self.assertIn('"Kept the fuller take. This copy is out."',
                      body)
        self.assertIn(".decided", body)
        # It never asks which copy stays.
        self.assertNotIn("Cut it", body)
        self.assertNotIn("Leave it", body)

    def test_update_shows_and_hides_the_line(self):
        bar = between("function updateSelBar()",
                      "function clearSelection()")
        self.assertIn("decisionLineFor()", bar)
        self.assertIn("selDecision.hidden = !line;", bar)


class FlagMarkerTests(unittest.TestCase):
    def test_clicking_a_flag_picks_its_words(self):
        click = between('marker.addEventListener("click"',
                        "var firstFlagWord")
        self.assertIn("selStart = flag.word_start;", click)
        self.assertIn("selEnd = flag.word_end;", click)
        self.assertIn("updateSelBar();", click)

    def test_the_flag_popover_path_is_gone(self):
        self.assertNotIn("openFlagPopup", TEMPLATE)


class CommentActionTests(unittest.TestCase):
    def test_the_bar_comment_pins_the_pick(self):
        body = between("function pinCommentOnPick()",
                       "// Cmd/Ctrl+Shift+M anywhere in the review")
        self.assertIn("pinnedWordStart = selStart;", body)
        self.assertIn("pinnedWordEnd = selEnd;", body)
        self.assertIn("pinDraft(", body)
        self.assertIn("applyCommentsOpen(true);", body)
        self.assertIn("draft.focus();", body)

    def test_cmd_shift_m_carries_a_pick_to_the_editor(self):
        # v0.15 Batch E2, item 15 supersedes the pin behavior
        # this test pinned through E1: the shortcut now runs the
        # Send to Editor carry (the picked words queue up in the
        # Editor panel); the bar's Comment button still pins.
        body = between("// Cmd/Ctrl+Shift+M anywhere in the review",
                       "// -- leaving the transcript")
        self.assertIn("carryPickToEditor()", body)
        self.assertNotIn("pinCommentOnPick()", body)
        self.assertIn("e.preventDefault();", body)


class BarActionTests(unittest.TestCase):
    def test_erase_acts_on_the_bar_pick(self):
        body = between("function runBarErase()",
                       "// -- the bar's wiring")
        self.assertIn("entriesOverlapping(selStart, selEnd)", body)
        self.assertIn("hiddenIndexSet()", body)
        self.assertIn("saveHiddenSet(set);", body)
        self.assertIn("renderTranscript(transcriptEntries);", body)

    def test_keep_settles_gold_like_the_gold_card(self):
        body = between("function runBarKeep()",
                       "function runBarErase()")
        self.assertIn('postJson("/api/gold-verdict"', body)
        self.assertIn('decision: "leave"', body)
        self.assertIn('"Kept the cut. Those words stay out of '
                      'the edit."', body)

    def test_the_wiring_runs_the_same_paths(self):
        wire = between("// -- the bar's wiring",
                       "// The 0.12 one-tap restore")
        self.assertIn('runSelectionToggle("cut")', wire)
        self.assertIn('runSelectionToggle("kept")', wire)
        self.assertIn("runBarKeep()", wire)
        self.assertIn("runBarErase()", wire)
        self.assertIn("pinCommentOnPick()", wire)
        self.assertIn("enterFixMode()", wire)


class PositioningTests(unittest.TestCase):
    def test_the_bar_clamps_inside_the_panel(self):
        body = between("function positionSelBar()",
                       "function updateSelBar()")
        self.assertIn("transcriptPanel.scrollTop", body)
        self.assertIn("transcriptPanel.clientHeight", body)
        # v0.14 Batch D: the bar defaults to above the pick
        # and flips below only when above has no room (the
        # old below-first flip condition is gone).
        self.assertIn("above < visTop + 2", body)
        self.assertNotIn("above >= visTop + 2", body)
        self.assertIn("visBottom - barH - 2", body)

    def test_the_bar_is_positioned_in_panel_coordinates(self):
        css = between("#selBubble {", "#selBubble[hidden]")
        self.assertIn("position: absolute;", css)
        self.assertIn("#transcriptPanel { position: relative; }",
                      STYLE)


class EscTests(unittest.TestCase):
    def test_esc_clears_the_pick_and_folds_the_menu_first(self):
        wire = between("// -- the bar's wiring",
                       "// The 0.12 one-tap restore")
        self.assertIn('e.key !== "Escape"', wire)
        self.assertIn("closeSelMenu(); return;", wire)
        self.assertIn("clearSelection();", wire)


class PreservationTests(unittest.TestCase):
    def test_the_hidden_words_machinery_survives(self):
        for marker in ("function hiddenIndexSet()",
                       "function saveHiddenSet(set)",
                       "function showAllHiddenWords(",
                       "function updateShowHiddenBtn()"):
            self.assertIn(marker, TEMPLATE)

    def test_the_old_bubble_machinery_is_gone(self):
        for gone in ("bubbleButtonsFor", "positionSelBubble",
                     "showSelBubble", "hideSelBubble",
                     "runBubble", "selBubbleEl",
                     "updateTxActionStrip"):
            self.assertNotIn(gone, TEMPLATE)

    def test_the_popups_and_fix_mode_survive(self):
        self.assertIn('id="editPopup"', TEMPLATE)
        self.assertIn("function wirePopupAnchor", TEMPLATE)
        self.assertIn("function enterFixMode()", TEMPLATE)
        self.assertIn("function openGoldPopup", TEMPLATE)


if __name__ == "__main__":
    unittest.main()
