"""v0.13 item 6 (layout) + item 9's pane toggles: fixed-size
icon buttons in the panel headers, pane minimum widths, and
the comments column's escape behind its top-bar toggle.

Justin's 0.12 shot-8 showed a giant "Make wider" button:
header buttons flexed and stretched. In 0.13 every header
button is the same fixed-size icon button (title tooltip and
aria-label carry its name), the grid guarantees the pane
minimums (video 260, transcript 340, comments 260), and
below the fitting width the comments sit in the stacked /
bottom-row arrangements behind the Comments toggle instead
of crushing the transcript.
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


class FixedHeaderButtonsTests(unittest.TestCase):
    def test_icon_buttons_are_fixed_size_and_capped(self):
        css = between("button.icon-btn {", "button.icon-btn svg")
        for marker in ("flex: 0 0 auto", "width: 38px",
                       "height: 38px", "max-width: 150px",
                       "max-height: 44px"):
            self.assertIn(marker, css)

    def test_toggle_on_state_is_the_highlighted_fill(self):
        self.assertIn('button.icon-btn[aria-pressed="true"]',
                      TEMPLATE)

    def test_make_wider_and_hide_are_icon_buttons(self):
        for bid, name in (
                # v0.15 Batch F, item 14b: the swap trades the
                # transcript and Editor sides, and says so.
                ("swapSidesBtn", "Swap transcript and Editor sides"),
                ("videoExpandBtn", "Make wider"),
                ("videoHideBtn", "Hide video"),
                ("transcriptExpandBtn", "Make wider"),
                ("transcriptHideBtn", "Hide transcript"),
                ("commentsExpandBtn", "Make wider"),
                ("hideCommentsBtn", "Hide editor"),
                ("showVideoBtn", "Show video"),
                ("showTranscriptBtn", "Show transcript")):
            tag = re.search(r'<button id="%s"[^>]*>' % bid,
                            TEMPLATE).group(0)
            self.assertIn('class="', tag)
            self.assertIn("icon-btn", tag, bid)
            self.assertIn('title="%s"' % name, tag, bid)
            self.assertIn('aria-label="%s"' % name, tag, bid)

    def test_no_text_left_in_the_pane_header_buttons(self):
        head = between('id="videoPanelHead"', 'id="watchingLine"')
        self.assertNotIn(">Make wider<", head)
        self.assertNotIn(">Hide video<", head)

    def test_expand_state_lives_in_the_tooltip_now(self):
        layout = between("function applyLayout()",
                         "function toggleExpandedPanel")
        self.assertIn('btn.title = label;', layout)
        self.assertIn('on ? "Back to normal" : "Make wider"',
                      layout)
        self.assertNotIn("btn.textContent", layout)


class PaneMinimumTests(unittest.TestCase):
    def test_wide_grid_carries_the_minimums(self):
        self.assertIn("minmax(260px, clamp(260px, "
                      "var(--pane-a, 410px)", TEMPLATE)
        self.assertIn("--col-a: clamp(260px, var(--pane-a, "
                      "410px), calc(100vw - 616px));", TEMPLATE)
        self.assertIn("minmax(340px, 1fr)", TEMPLATE)
        self.assertIn("--col-c: clamp(260px, var(--pane-c, "
                      "310px), min(360px,", TEMPLATE)

    def test_no_grid_still_uses_the_old_minimums(self):
        self.assertNotIn("minmax(320px, 1fr)", TEMPLATE)
        self.assertNotIn("clamp(280px, var(--pane-a", TEMPLATE)

    def test_mid_layout_video_floor(self):
        self.assertIn("minmax(260px, 300px) minmax(0, 1fr)",
                      TEMPLATE)


if __name__ == "__main__":
    unittest.main()
