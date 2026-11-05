"""v0.13 item 7 (calm transcript header) + item 9 (icon top
bar): the header is one row (title, Follow icon toggle, the
pane buttons), the word-count headline and the "Times
match..." sentence are gone from it (the count lives on as
quiet text in the More card), and the top bar's text
buttons are icon buttons whose toggles show their state.
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


class TranscriptHeaderTests(unittest.TestCase):
    def test_header_is_one_row_with_the_title_inside(self):
        card = between('id="transcriptCard"', 'id="transcriptPanel"')
        self.assertIn('<div class="tx-header">', card)
        head = between('<div class="tx-header">',
                       'id="transcriptPanel"')
        self.assertIn("<h2>Transcript</h2>", head)
        self.assertNotIn('id="txCounts"', head)
        self.assertNotIn("txClockNote", head)

    def test_follow_is_a_small_icon_toggle(self):
        tag = re.search(r'<button id="txFollow"[^>]*>',
                        TEMPLATE).group(0)
        self.assertIn("icon-btn", tag)
        self.assertIn('aria-pressed="true"', tag)
        self.assertIn('title="Follow the video: on"', tag)
        follow = between("function setFollow(on)",
                         "// -- play the edit as edited")
        self.assertIn("txFollowBtn.title = label;", follow)
        self.assertNotIn("txFollowBtn.textContent", follow)
        # Turning it on returns the view to the playhead (P4).
        self.assertIn("syncTranscriptToPlayhead();", follow)
        self.assertIn('classList.toggle("follow-paused"',
                      follow)

    def test_word_count_lives_in_the_more_card(self):
        # v0.15 Batch E1: the Overall section left the More
        # card (absorbed by the thread); the count paragraph
        # still sits at the More card's head.
        more = between('id="moreCard"', 'id="styleCard"')
        self.assertIn('<p class="note" id="txCounts"></p>', more)
        counts = between("function updateTxCounts()",
                         "function fmtClock")
        self.assertIn('" words in the edit"', counts)

    def test_header_height_is_capped_by_its_parts(self):
        self.assertIn(".tx-header h2 { margin: 0 auto 0 0;",
                      TEMPLATE)
        # v0.14 Batch C (SYMMETRY 10): the transcript head
        # takes the shared pane-head height, 44, like the
        # video and comments heads.
        self.assertIn("min-height: 44px", between(
            ".tx-header {", ".tx-header h2"))
        self.assertIn("height: 44px", between(
            ".tx-header {", ".tx-header h2"))


class TopBarIconTests(unittest.TestCase):
    def test_top_bar_buttons_are_named_icon_buttons(self):
        head = between('<div class="topbar-actions">',
                       "</header>")
        for bid, name in (
                ("bringInBtn", "Bring in another video"),
                ("teachToggle", "Train the editor"),
                ("howItWorksToggle", "How this works"),
                ("commentsToggle", "Hide editor"),
                ("settingsToggle", "Settings")):
            tag = re.search(r'<button id="%s"[^>]*>' % bid,
                            head).group(0)
            self.assertIn("icon-btn", tag, bid)
            self.assertIn('title="%s"' % name, tag, bid)
            self.assertIn('aria-label="%s"' % name, tag, bid)

    def test_no_text_buttons_left_in_the_top_bar(self):
        head = between('<div class="topbar-actions">',
                       "</header>")
        self.assertNotIn(">Train the editor<", head)
        self.assertNotIn(">How this works<", head)
        self.assertNotIn("&#9881; Settings", head)

    def test_comments_button_is_the_toggle(self):
        tag = re.search(r'<button id="commentsToggle"[^>]*>',
                        TEMPLATE).group(0)
        self.assertIn('aria-pressed="true"', tag)
        apply = between("function applyCommentsOpen(open)",
                        "var commentsOpenSaved")
        self.assertIn('setAttribute("aria-pressed"', apply)
        self.assertIn('"Hide editor" : "Show editor"', apply)
        click = between("commentsToggle.addEventListener",
                        "var hideCommentsBtn")
        # A click toggles; it no longer only opens.
        self.assertIn('classList.contains("comments-closed")',
                      click)

    def test_comments_count_is_a_badge_number(self):
        # v0.15 Batch E1: the badge is the one waiting count.
        self.assertIn(
            "cc.textContent = n ? String(n) : \"\";",
            TEMPLATE)
        self.assertIn("#commentsCount:not(:empty)", TEMPLATE)

    def test_toggle_on_state_covers_expanded_too(self):
        self.assertIn('button.icon-btn[aria-expanded="true"]',
                      TEMPLATE)


if __name__ == "__main__":
    unittest.main()
