"""v0.14 Batch D: the action bar sits at the pick
(REPORT issue 1).

In 0.13 the picked-words bar docked at a fixed spot at the
top of the transcript pane (the review measured its top edge
at 143px at 1440x900 no matter where the pick was). The dock
was a coordinate bug: the bar's positioner read each word's
offsetTop, which is relative to its paragraph (the .tx-para
blocks are positioned), as if it were a panel coordinate, so
every pick read as near the pane's top and the clamp pinned
the bar there. Batch D anchors the bar to the picked words
instead, measuring the anchor in panel coordinates by
walking the offsetParent chain up to the panel:

- normal case: the bar sits just above the picked words;
- a pick near the top of the visible pane, with no room
  above, flips the bar to just below the picked words;
- the 0.13 clamp stays: the bar always stays fully inside
  the transcript pane's visible box, so it never covers the
  timeline band;
- the bar's height is measured live (offsetHeight, decision
  line and all), never hard-coded, so the anchor holds
  whatever size the bar currently is;
- the anchor is recomputed when the pick changes, when the
  transcript scrolls, and when the window resizes.

The positioning itself is inline DOM code in the page, so
the measured on-screen proofs live in the fit battery
(fit-test-014.mjs, [0.14D] section). What is pinned here is
the anchoring math, mirrored in Python over the page's
positionSelBar in the style of the v0.12 bar-actions mirror,
plus the source-level facts that keep the mirror honest.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "src" / "talk_to_your_editor" / "templates"
            / "test_app.html").read_text(encoding="utf-8")


def between(start, end):
    i = TEMPLATE.index(start)
    return TEMPLATE[i:TEMPLATE.index(end, i)]


# ---------------------------------------------------------------------------
# The anchoring math, mirrored over the page's positionSelBar
# ---------------------------------------------------------------------------

def bar_top(anchor_top, anchor_height, bar_height, vis_top, vis_bottom):
    """The bar's top, in panel content coordinates.

    anchor_* is the first picked word's line box; vis_* is
    the transcript pane's visible box in the same
    coordinates. Above by default, below when above has no
    room, then clamped inside the visible box (the clamp's
    own floor wins when the bar is taller than the box).
    """
    above = anchor_top - bar_height - 6
    below = anchor_top + anchor_height + 6
    top = above
    if above < vis_top + 2:
        top = below
    return min(max(vis_top + 2, top),
               max(vis_top + 2, vis_bottom - bar_height - 2))


class AnchorMathMirrorTests(unittest.TestCase):
    """D1/D2/D3 geometry, at mirror level."""

    BAR = 46   # a plain pick's bar in 0.13; the math never
               # assumes it (see the varied-height test).
    LINE = 26  # one transcript line at the 17px word size.

    def test_middle_pick_puts_the_bar_just_above(self):
        # D1: visible box 0..400, pick on a middle line.
        top = bar_top(200, self.LINE, self.BAR, 0, 400)
        self.assertEqual(top + self.BAR, 200 - 6)

    def test_last_line_pick_puts_the_bar_above_not_docked(self):
        # D2: the pick is on the pane's last visible line;
        # the bar rides just above it, nowhere near the
        # pane's top (the 0.13 dock sat at the top always).
        top = bar_top(368, self.LINE, self.BAR, 0, 400)
        self.assertEqual(top + self.BAR, 368 - 6)
        self.assertGreater(top, 100)

    def test_first_line_pick_flips_the_bar_below(self):
        # D3: the pick's line is the first visible line, so
        # above would leave the visible box; the bar flips
        # to just below the pick, still inside the pane.
        top = bar_top(2, self.LINE, self.BAR, 0, 400)
        self.assertEqual(top, 2 + self.LINE + 6)
        self.assertLessEqual(top + self.BAR, 400)

    def test_the_math_uses_the_measured_height(self):
        # A taller bar (the decision line rides with it)
        # anchors by its own height: the gap above the pick
        # is the same 6px whatever the bar's height is.
        for bar_h in (46, 74, 96):
            top = bar_top(200, self.LINE, bar_h, 0, 400)
            self.assertEqual(top + bar_h, 200 - 6)

    def test_a_scrolled_panes_first_line_still_flips(self):
        # Same flip in a scrolled pane: the visible box is
        # 500..900 and the pick sits on its first line.
        top = bar_top(504, self.LINE, self.BAR, 500, 900)
        self.assertEqual(top, 504 + self.LINE + 6)

    def test_a_scrolled_panes_middle_pick_stays_above(self):
        top = bar_top(700, self.LINE, self.BAR, 500, 900)
        self.assertEqual(top + self.BAR, 700 - 6)

    def test_the_clamp_keeps_the_bar_inside_a_short_pane(self):
        # A pane barely taller than the bar: flipped below
        # would run past the bottom, so the clamp pulls the
        # bar back inside the visible box.
        top = bar_top(30, self.LINE, self.BAR, 0, 60)
        self.assertEqual(top, 60 - self.BAR - 2)
        self.assertGreaterEqual(top, 2)


class AnchorSourceTests(unittest.TestCase):
    """The page implements the mirrored math, at the pick."""

    def setUp(self):
        self.body = between("function positionSelBar()",
                            "function updateSelBar()")

    def test_the_anchor_is_the_first_picked_word(self):
        self.assertIn('querySelectorAll(".tw")', self.body)
        self.assertIn("end > selStart && idx < selEnd", self.body)

    def test_the_anchor_is_measured_in_panel_coordinates(self):
        # A word's raw offsetTop is relative to its
        # paragraph (.tx-para is positioned); using it as a
        # panel coordinate was the 0.13 dock. The page walks
        # the offsetParent chain up to the panel and sums.
        self.assertIn(
            "while (node && node !== transcriptPanel) {", self.body)
        self.assertIn("anchorTop += node.offsetTop;", self.body)
        self.assertIn("anchorLeft += node.offsetLeft;", self.body)
        self.assertIn("node = node.offsetParent;", self.body)
        self.assertNotIn("anchor.offsetTop - barH", self.body)

    def test_above_is_the_default_and_below_the_flip(self):
        self.assertIn(
            "var above = anchorTop - barH - 6;", self.body)
        self.assertIn(
            "var below = anchorTop + anchor.offsetHeight + 6;",
            self.body)
        self.assertIn("top = above;", self.body)
        self.assertIn("if (above < visTop + 2) {", self.body)
        self.assertIn("top = below;", self.body)

    def test_the_height_is_measured_live_never_assumed(self):
        self.assertIn("var barH = selBubble.offsetHeight;", self.body)
        self.assertIn("var barW = selBubble.offsetWidth;", self.body)

    def test_the_013_clamp_stays(self):
        self.assertIn("visTop + 2", self.body)
        self.assertIn("visBottom - barH - 2", self.body)
        self.assertIn(
            "transcriptPanel.clientWidth - barW - 4", self.body)

    def test_the_anchor_recomputes_on_scroll_and_resize(self):
        scroll = between(
            'transcriptPanel.addEventListener("scroll"',
            'transcriptPanel.addEventListener("wheel"')
        self.assertIn("positionSelBar();", scroll)
        # v0.15 Batch F, item 37: the Change words box hangs on
        # the same anchoring, so the resize listener re-anchors
        # it beside the bar.
        self.assertIn(
            'window.addEventListener("resize", '
            'function () { positionSelBar(); positionFixPanel(); });',
            TEMPLATE)

    def test_update_repositions_whenever_the_pick_changes(self):
        bar = between("function updateSelBar()",
                      "function clearSelection()")
        self.assertIn("selBubble.hidden = false;", bar)
        self.assertIn("positionSelBar();", bar)


if __name__ == "__main__":
    unittest.main()
