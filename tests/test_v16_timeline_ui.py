"""v0.16 Batch C: the band's hand-editing surface, statically.

The gestures themselves are proven by the fit battery against a
real browser; these checks pin the surface the battery drives:
the controls exist, renderBlocks draws picks, edge grips, and
join tabs, the operations post to the six endpoints, the keys
are in the one SHORTCUTS table, and the Delete branch reaches
the picked piece only while the band's pick stands.

Everything is offline; no provider is called.
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


class MarkupTests(unittest.TestCase):
    def test_the_tools_row_and_status_line_exist(self):
        self.assertIn('id="tlSplitBtn"', TEMPLATE)
        self.assertIn("Split at playhead", TEMPLATE)
        self.assertIn('id="tlOpStatus"', TEMPLATE)
        tools = between('id="tlTools"', 'id="tlOpStatus"')
        self.assertIn("Drag a piece's edge", tools)
        self.assertIn("Click the small diamond", tools)

    def test_the_tools_live_below_the_band_not_inside_it(self):
        # Inside the band's fixed height, the tools row, the
        # status line, and the fade choices squeeze the
        # scroll row (the only flexible row) to zero and
        # paint over the pieces, which makes the pieces
        # unclickable. They belong after the band's close:
        # between the caption (the band's last row) and the
        # tools, the band and wrap closers must come first.
        between_rows = between('id="tlCaption"', 'id="tlTools"')
        self.assertEqual(between_rows.count("</div>"), 2, between_rows)

    def test_the_join_tab_escapes_the_button_minimum_height(self):
        # The page's base button rule sets min-height: 48px;
        # without its own min-height: 0 the 12px diamond tab
        # is stretched to 48px and its middle sits under the
        # edge grips, so clicks start a trim instead of
        # opening the crossfade choices. It must also paint
        # above the grips (z-index 5) inside its small patch.
        rule = between(".tl-join {", "}")
        self.assertIn("min-height: 0", rule)
        self.assertIn("z-index: 6", rule)

    def test_a_press_on_a_piece_or_tab_stays_with_it(self):
        # The track's scrub handler sits on an ancestor: a
        # press that bubbles up hands it the pointer capture,
        # and the piece (or the join tab) never sees its own
        # click. Both handlers must stop the press first.
        block_wire = between('block.addEventListener("pointerdown"',
                             "tlBlockPointerDown(e, piece, block);")
        self.assertIn("e.stopPropagation();", block_wire)
        tab_wire = between('tab.addEventListener("pointerdown"',
                           'tab.addEventListener("click"')
        self.assertIn("e.stopPropagation();", tab_wire)

    def test_the_fade_pop_offers_the_five_choices(self):
        pop = between('id="tlFadePop"', 'id="tlFadeClose"')
        self.assertIn("No crossfade", pop)
        self.assertIn('data-fade="0.04"', pop)
        self.assertIn('data-fade="0.1"', pop)
        self.assertIn('data-fade="0.25"', pop)
        self.assertIn('data-fade="0.5"', pop)

    def test_the_styles_for_pick_grips_and_tabs_exist(self):
        self.assertIn(".tl-block.tl-picked", TEMPLATE)
        self.assertIn(".tl-edge-l", TEMPLATE)
        self.assertIn(".tl-edge-r", TEMPLATE)
        self.assertIn(".tl-join", TEMPLATE)
        self.assertIn(".tl-join.has-fade", TEMPLATE)


class RenderBlocksTests(unittest.TestCase):
    def setUp(self):
        self.blocks = between("function renderBlocks()", "function renderTlCaption()")

    def test_blocks_carry_their_plan_index_and_pick(self):
        self.assertIn("block.dataset.rangeIndex = piece.range_index", self.blocks)
        self.assertIn('" tl-picked"', self.blocks)
        self.assertIn("tlBlockPointerDown", self.blocks)

    def test_ordinary_pieces_get_edge_grips_specials_do_not(self):
        self.assertIn("if (!piece.special && typeof piece.range_index", self.blocks)
        self.assertIn("tlEdgePointerDown", self.blocks)
        self.assertIn('"tl-edge tl-edge-" + side', self.blocks)

    def test_join_tabs_open_the_fade_choices(self):
        self.assertIn('"tl-join"', self.blocks)
        self.assertIn("openTlFadePop(j - 1, piece)", self.blocks)
        self.assertIn("if (j === 0 || piece.special)", self.blocks)

    def test_a_drag_suppresses_the_click_that_follows(self):
        self.assertIn("block.dataset.tlDragged", self.blocks)


class InteractionTests(unittest.TestCase):
    def setUp(self):
        self.section = between(
            "manual timeline editing (v0.16)",
            "// -- captions row",
        )

    def test_every_operation_posts_to_its_endpoint(self):
        for route in (
            "/api/timeline/split",
            "/api/timeline/trim",
            "/api/timeline/delete",
            "/api/timeline/move",
            "/api/timeline/paste",
            "/api/timeline/fade",
        ):
            self.assertIn(route, TEMPLATE)

    def test_split_and_paste_land_at_the_playhead(self):
        self.assertIn(
            'tlPost("/api/timeline/split", { output_time: displayTime() })',
            self.section,
        )
        self.assertIn("{ spans: tlClipboard, output_time: displayTime() }",
                      self.section)

    def test_the_clipboard_reads_the_piece_or_the_word_pick(self):
        self.assertIn("function tlPickedSpans()", self.section)
        self.assertIn("piece.source_start", self.section)
        self.assertIn("selectedEntries()", self.section)

    def test_the_clipboard_keys_keep_a_fields_own_clipboard(self):
        keys = between('key === "x"', "});")
        self.assertIn("tlCopy(true)", self.section)
        self.assertIn("typingInField(e.target)", self.section)

    def test_a_cut_word_pick_rides_the_word_path(self):
        self.assertIn('runSelectionToggle("cut")', self.section)

    def test_each_post_refreshes_history_and_job(self):
        post = between("function tlPost(", "function tlEditedTimeAt(")
        self.assertIn("refreshHistory()", post)
        self.assertIn("reloadJobState()", post)


class KeyboardTests(unittest.TestCase):
    def test_the_shortcuts_table_names_the_new_keys(self):
        table = between("var SHORTCUTS = [", "];")
        self.assertIn(
            '{ keys: "Cmd or Ctrl + Shift + S", action: '
            '"Split the piece at the playhead" }',
            table,
        )
        self.assertIn(
            '{ keys: "Cmd or Ctrl + X / C / V", action: '
            '"Cut, copy, or paste a piece on the timeline" }',
            table,
        )
        self.assertIn(
            '{ keys: "Delete", action: '
            '"Take out the piece picked on the timeline" }',
            table,
        )

    def test_delete_reaches_a_picked_piece_before_the_line(self):
        handler = between(
            "Keyboard editing (v0.15 Batch G",
            "// Cmd+Z / Ctrl+Z undoes",
        )
        self.assertIn("tlPicked !== null && !mod", handler)
        self.assertIn('"/api/timeline/delete"', handler)

    def test_the_dismissal_owner_clears_the_piece_pick(self):
        clear = between("function clearSelection()", "// Comment on the pick")
        self.assertIn("tlPicked = null", clear)

    def test_picking_words_in_the_transcript_ends_the_piece_pick(self):
        down = between(
            'transcriptEl.addEventListener("pointerdown"',
            'document.addEventListener("pointermove"',
        )
        self.assertIn("tlClearPicked()", down)


if __name__ == "__main__":
    unittest.main()
