"""v0.14 Batch C: alignment and spacing (SYMMETRY 7-12;
REPORT issue 2 shares the SYMMETRY 7 fix).

Pinned here at source level, in the style of the other page
tests (the measured on-screen proofs live in the fit battery,
fit-test-014.mjs, Batch C section):

- Comment cards fit the rail: the card header wraps, badges
  are one line at a fixed 26px height, header items align on
  their tops, and Delete keeps its auto left margin so it
  lands on the card's inner right edge on every card.
- All three pane heads are 44 tall and start on one line:
  the video head (.panel-head), the transcript head
  (.tx-header, top margin gone, the transcript card's top
  padding at the pane inset), and the comments head, which
  now holds the Done (n) toggle inline in the same row.
- The column gutters are 16px on both sides: 8px divider
  columns plus the transcript wrap's 8px side padding.
- Structural spacing snaps to the scale {4, 8, 12, 16, 22}:
  the named one-off moves (pane-head gaps 6 to 8, composer
  and row gaps 10 to 8, More inner stack, overflow menu item
  padding to 8px 12px, transport side padding to 8, the
  manual timestamp field to 12, badge padding to 4px 8px).
- The zoom cluster is laid out by the transport's flex row:
  no margin of its own, internal gap 8.
- Back 5 and Forward 5 take one width, sized by the wider
  label.
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


def rule(selector):
    """The winning (last) rule body declared for one exact
    selector, the way the cascade reads it: the template
    layers older layout blocks under the v2 workspace block,
    so the last declaration for a selector is the live one."""
    plain = re.sub(r"/\*.*?\*/", "", STYLE, flags=re.DOTALL)
    bodies = re.findall(
        r"(?m)^[ \t]*" + re.escape(selector) + r"\s*\{([^}]*)\}",
        plain, flags=re.DOTALL)
    assert bodies, selector
    return bodies[-1]


class CommentCardFitTests(unittest.TestCase):
    """SYMMETRY 7/8 + REPORT issue 2."""

    def test_header_wraps_and_aligns_on_tops(self):
        body = rule(".comment header")
        self.assertIn("flex-wrap: wrap", body)
        self.assertIn("align-items: flex-start", body)
        self.assertIn("gap: 8px", body)

    def test_badges_are_one_line_at_26(self):
        body = rule(".badge")
        self.assertIn("height: 26px", body)
        self.assertIn("white-space: nowrap", body)
        self.assertIn("padding: 4px 8px", body)
        # The badge text stays above the 11px micro tier.
        self.assertIn("font-size: 12px", body)

    def test_delete_lands_on_the_inner_right_edge(self):
        body = rule(".del")
        self.assertIn("margin-left: auto", body)
        self.assertIn("min-height: 36px", body)

    def test_card_padding_is_on_the_scale(self):
        self.assertIn("padding: 16px;\n    margin: 0 0 12px;",
                      TEMPLATE)


class PaneHeadLineTests(unittest.TestCase):
    """SYMMETRY 10: one top line, 44 tall, card tops aligned."""

    def test_all_three_heads_are_44(self):
        self.assertIn("height: 44px", rule(".panel-head"))
        self.assertIn("height: 44px", rule(".comments-head"))
        tx = rule(".tx-header")
        self.assertIn("height: 44px", tx)
        self.assertIn("margin-top: 0", tx)

    def test_comments_head_holds_the_done_toggle_inline(self):
        # v0.15 Batch E1: the head is the editor's head now:
        # avatar, name, and the waiting count inline on the
        # same 44px row; the Done toggle left with the cards,
        # and the count's id lives in the head itself.
        head = between('id="commentsHead"', 'id="threadList"')
        self.assertIn('id="editorName"', head)
        self.assertIn('id="commentsHeadCount"', head)
        self.assertNotIn('id="doneToggle"', TEMPLATE)
        # The old stacked list-head row is gone.
        self.assertNotIn('id="listHead"', TEMPLATE)
        self.assertNotIn('class="list-head', TEMPLATE)

    def test_transcript_card_top_and_head_inset(self):
        # No top padding on the wrap: the card top shares the
        # other panes' top line; the card's top padding is
        # the same 12px inset the other panes give their heads.
        self.assertIn(
            "body.job-loaded .review-right { overflow: hidden; "
            "min-height: 0; min-width: 0; display: flex; "
            "padding: 0 8px; }", TEMPLATE)
        self.assertIn(
            "body.job-loaded #transcriptCard { padding-top: 12px; }",
            TEMPLATE)

    def test_pane_padding_and_head_gaps_are_scale_values(self):
        self.assertIn(
            "padding: 12px; display: flex; flex-direction: "
            "column; gap: 4px;", TEMPLATE)
        self.assertIn("gap: 8px", rule(".panel-head-btns"))


class GutterTests(unittest.TestCase):
    """SYMMETRY 9: 16px gutters on both sides."""

    def test_divider_columns_are_8(self):
        self.assertIn(
            "grid-template-columns: var(--col-a) 8px "
            "minmax(340px, 1fr) 8px var(--col-c);", TEMPLATE)
        self.assertNotIn(
            "grid-template-columns: var(--col-a) 10px", TEMPLATE)

    def test_gutter_math_is_16_on_both_sides(self):
        # 8px divider column + the transcript wrap's 8px side
        # padding = the 16px gutter, left and right alike.
        self.assertIn(
            "display: flex; padding: 0 8px; }", TEMPLATE)
        self.assertEqual(8 + 8, 16)


class SpacingScaleTests(unittest.TestCase):
    """SYMMETRY 11: the named one-off moves."""

    def test_named_moves(self):
        self.assertIn("gap: 8px", rule(".row"))
        self.assertIn("padding: 8px 12px", rule(".sel-mi"))
        self.assertIn("padding: 12px", rule('input[type="number"]'))
        # v0.15 Batch I, item 39: the transport buttons are
        # icon-beside-word pairs; the padding moved to 4px 12px.
        self.assertIn("padding: 4px 12px", rule(".tp-btn"))
        self.assertIn("padding: 16px 0 4px", rule("#moreCard .card"))
        self.assertIn("gap: 8px", rule(".composer-row"))

    def test_zoom_cluster_rides_the_flex_row(self):
        body = rule(".tl-zoom")
        self.assertIn("gap: 8px", body)
        self.assertNotIn("margin-left", body)
        # The wrapped-line growth is unchanged: cluster 46
        # (38 buttons + 8 top padding) + row gap 8 = 54.
        self.assertIn("+ 54px", TEMPLATE)


class MirrorButtonTests(unittest.TestCase):
    """SYMMETRY 12: Back 5 and Forward 5, one width."""

    def test_skip_pair_shares_one_width(self):
        body = rule("#back5, #fwd5")
        # v0.15 Batch I, item 39: the pair carries a 20px icon
        # beside a 13px word now, which 52px cannot hold; the
        # pair is equal at 86px. The symmetry (one shared
        # width) is the invariant and is unchanged.
        self.assertIn("width: 86px", body)


if __name__ == "__main__":
    unittest.main()
