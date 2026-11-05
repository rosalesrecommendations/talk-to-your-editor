"""v0.15 Batch F: layout and panes (items 14, 26, 36, 37,
plus the coordinator's P8 drive-by).

Item 14a, the restore guarantee: the top bar always carries
one named button per pane showing its state and toggling it;
a stored pane width under the usable minimum is repaired to
the default at load; a layout that loads with more than one
pane hidden gets its transcript back.

Item 14b: the swap control lives in the transcript head and
trades the transcript and Editor sides, widths and dividers
unchanged in kind.

Item 26: the reading measure is the narrower ~57ch by
default, with a Reading width control cycling to the old
68ch, remembered with the layout.

Item 36: the Editor panel's expand is a second width, about
double, taken from the transcript's share, never below the
transcript minimum; the toggle keeps its Make wider / Back
to normal names.

Item 37: the Change words box floats inside the transcript
panel at the picked words on the action bar's anchoring, and
the Batch B dismissal owner governs it.

Drive-by (P8): the printed shortcut table names
Cmd/Ctrl+Shift+M for what Batch E2 made it do, carrying the
pick to the Editor.
"""

import re
import unittest
from pathlib import Path

TEMPLATE = (
    Path(__file__).resolve().parent.parent
    / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")


def between(start, end):
    i = TEMPLATE.index(start)
    return TEMPLATE[i:TEMPLATE.index(end, i)]


class TopBarPaneButtonsTests(unittest.TestCase):
    def test_one_named_button_per_pane_in_the_top_bar(self):
        for bid, word in (
            ("showVideoBtn", "Video"),
            ("showTranscriptBtn", "Transcript"),
        ):
            tag = re.search(
                r'<button id="%s"[^>]*>' % bid, TEMPLATE).group(0)
            self.assertNotIn("hidden", tag, bid)
            self.assertIn("aria-pressed", tag, bid)
        bar = between('id="showVideoBtn"', 'id="settingsToggle"')
        self.assertIn('<span class="pane-btn-word">Video</span>', bar)
        self.assertIn(
            '<span class="pane-btn-word">Transcript</span>', bar)
        # The Editor's button is the E1 toggle, word and all.
        self.assertIn('id="commentsToggle"', bar)

    def test_apply_layout_names_each_panes_state(self):
        layout = between("function applyLayout()", "function clearExpandedPanel")
        self.assertIn('"showVideoBtn", "video"', layout)
        self.assertIn('"showTranscriptBtn", "transcript"', layout)
        self.assertIn('(shown ? "Hide " : "Show ") + row[1]', layout)
        self.assertIn("btn.hidden = false;", layout)

    def test_the_top_bar_buttons_toggle_both_ways(self):
        show_video = between(
            'var showVideoBtn = document.getElementById("showVideoBtn");',
            'var transcriptHideBtn')
        self.assertIn("layoutState.video = false;", show_video)
        self.assertIn("layoutState.video = true;", show_video)
        show_tx = between(
            'var showTranscriptBtn = document.getElementById("showTranscriptBtn");',
            'var readingWidthBtn')
        self.assertIn("layoutState.transcript = false;", show_tx)
        self.assertIn("layoutState.transcript = true;", show_tx)


class RestoreGuaranteeTests(unittest.TestCase):
    def test_load_repair_restores_the_transcript(self):
        repair = between("function repairLayoutAtLoad()", "// -- row and band fit")
        # The Editor's separate closed store counts as hidden.
        self.assertIn('commentsOpenSaved === "closed"', repair)
        self.assertIn("hiddenCount > 1", repair)
        self.assertIn("layoutState.transcript = false;", repair)
        # A leftover full-width expand of the video or the
        # Editor hides the transcript too, so it goes.
        self.assertIn('layoutState.expanded === "video"', repair)
        self.assertIn('layoutState.expanded === "comments"', repair)
        self.assertIn("layoutState.expanded = null;", repair)

    def test_stored_widths_under_the_minimum_are_repaired(self):
        wire = between("function wirePaneDivider", 'wirePaneDivider("paneDivider"')
        self.assertIn("var minPx = fromRight ? 260 : 280;", wire)
        self.assertIn('saved = fromRight ? "310px" : "410px";', wire)
        # The repaired value is written back, not just applied.
        self.assertIn("window.localStorage.setItem(key, saved);", wire)


class SwapTests(unittest.TestCase):
    def test_the_swap_control_lives_in_the_transcript_head(self):
        head = between('id="transcriptCard"', 'id="transcriptPanel"')
        self.assertIn('id="swapSidesBtn"', head)
        self.assertIn('title="Swap transcript and Editor sides"', head)
        video_head = between('id="videoPanelHead"', 'id="watchingLine"')
        self.assertNotIn('id="swapSidesBtn"', video_head)

    def test_swapped_row_trades_transcript_and_editor(self):
        self.assertIn(
            "body.job-loaded.panels-swapped .review-split {\n"
            "      grid-template-columns: var(--col-a) 8px "
            "var(--col-c) 8px minmax(340px, 1fr);",
            TEMPLATE)
        self.assertIn(
            "body.job-loaded.panels-swapped #toolsPane "
            "{ grid-column: 3; }", TEMPLATE)
        self.assertIn(
            "body.job-loaded.panels-swapped .review-right "
            "{ grid-column: 5; }", TEMPLATE)

    def test_the_tools_divider_measures_the_editor_after_a_swap(self):
        wire = between("function wirePaneDivider", 'wirePaneDivider("paneDivider"')
        self.assertIn('document.body.classList.contains("panels-swapped")', wire)
        self.assertIn("px = Math.round(ev.clientX - toolsLeft);", wire)
        # The choice persists with the layout, as before.
        self.assertIn("layoutState.swapped = !layoutState.swapped;", TEMPLATE)


class ReadingWidthTests(unittest.TestCase):
    def test_the_default_measure_is_the_narrower_one(self):
        self.assertIn(
            ".tx-words { flex: 1; min-width: 0; max-width: 57ch; }",
            TEMPLATE)
        # v0.17 (feedback item 27): Wider used to cap at 68ch,
        # barely wider than Narrow's 57ch, so the control read
        # as doing nothing. Wide is now genuinely uncapped.
        self.assertIn(
            "body.reading-wide .tx-words { max-width: none; }",
            TEMPLATE)

    def test_the_control_cycles_and_the_choice_is_remembered(self):
        head = between('id="transcriptCard"', 'id="transcriptPanel"')
        self.assertIn('id="readingWidthBtn"', head)
        self.assertIn('title="Reading width: Narrow"', head)
        self.assertIn('layoutState.readingWidth === "wide"', TEMPLATE)
        self.assertIn(
            'layoutState.readingWidth =\n'
            '        layoutState.readingWidth === "wide" ? "narrow" : "wide";',
            TEMPLATE)
        layout = between("function applyLayout()", "function clearExpandedPanel")
        self.assertIn('"reading-wide", layoutState.readingWidth === "wide"',
                      layout)
        # Reset layout restores the narrow default with the rest.
        self.assertIn('readingWidth: "narrow"', TEMPLATE)


class WideEditorTests(unittest.TestCase):
    def test_expand_is_a_second_width_not_the_whole_row(self):
        self.assertIn(
            "--col-c-wide: clamp(260px, calc(var(--pane-c, 310px) * 2), "
            "calc(100vw - var(--col-a) - 376px));", TEMPLATE)
        self.assertIn(
            "body.job-loaded.expanded-comments .review-split {\n"
            "      --col-c-wide:", TEMPLATE)
        # The other panes stay in the row in wide mode.
        self.assertIn(
            "body.job-loaded.expanded-comments:not(.video-hidden) "
            ".review-left { display: flex; }", TEMPLATE)
        self.assertIn(
            "body.job-loaded.expanded-comments:not(.transcript-hidden) "
            ".review-right { display: flex; }", TEMPLATE)
        self.assertIn(
            "body.job-loaded.expanded-comments #toolsPane "
            "{ grid-column: 5; display: block; }", TEMPLATE)

    def test_the_toggle_keeps_its_two_names(self):
        layout = between("function applyLayout()", "function clearExpandedPanel")
        self.assertIn('on ? "Back to normal" : "Make wider"', layout)
        self.assertIn('["commentsExpandBtn", "comments"]', layout)


class ChangeWordsPlacementTests(unittest.TestCase):
    def test_the_box_lives_on_the_transcript_card(self):
        # On the card, outside the scrolling panel, so the
        # card's full height is the box's visible box; and no
        # longer in its old place in the card's flow at the
        # bottom of the pane (it is positioned, not flowed).
        panel = between('id="transcriptPanel"', 'id="transcriptStatus"')
        self.assertNotIn('id="fixPanel"', panel)
        after = between('id="transcriptStatus"', 'id="toolsDivider"')
        self.assertIn('id="fixPanel"', after)
        self.assertIn("#transcriptCard { position: relative; }",
                      TEMPLATE)

    def test_the_box_floats_on_the_bars_treatment(self):
        css = between("#fixPanel {", "#fixPanel[hidden]")
        self.assertIn("position: absolute;", css)
        self.assertIn("z-index: 41;", css)
        self.assertIn("border-radius: 12px;", css)

    def test_the_box_is_positioned_at_the_pick(self):
        pos = between("function positionFixPanel()", "function enterFixMode()")
        self.assertIn("fixStart !== null ? fixStart : selStart", pos)
        self.assertIn('document.getElementById("transcriptCard")', pos)
        self.assertIn("anchor.getBoundingClientRect()", pos)
        enter = between("function enterFixMode()", "function exitFixMode()")
        self.assertIn("positionFixPanel();", enter)
        span = between("function selectSpan(start, end)",
                       "function positionFixPanel()")
        self.assertIn("positionFixPanel();", span)

    def test_the_dismissal_owner_governs_the_box(self):
        owner = between("ONE dismissal owner", "// The 0.12 one-tap restore")
        self.assertIn("fixMode && fixPanel && !fixPanel.contains(t)",
                      owner)
        self.assertIn('!t.closest(".tw")', owner)
        # Escape closes it through the same owner, and both
        # paths return focus to the transcript (the press path
        # defers its focus a tick, so the press's own focus
        # move cannot land after it).
        self.assertIn(
            "transcriptPanel.focus({ preventScroll: true })", owner)
        self.assertIn(
            "panelToFocus.focus({ preventScroll: true })", owner)


class ShortcutDriveByTests(unittest.TestCase):
    def test_the_printed_name_matches_the_carry(self):
        table = between("var SHORTCUTS = [", "];")
        self.assertIn(
            '{ keys: "Cmd or Ctrl + Shift + M", '
            'action: "Send the picked words to the Editor" }', table)
        self.assertNotIn("Comment on the picked words", TEMPLATE)


if __name__ == "__main__":
    unittest.main()
