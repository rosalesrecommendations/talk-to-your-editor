"""v0.13 item 7, added by Justin's Descript steering: the
right rail is the calm benchmark — Descript's comments
column header and More card use quiet grouped rows, a
small icon plus a short label per row, and quiet group
headings. Applied light: the More groups' headings go
quiet, their action buttons carry a small icon next to
the label they already had, and the comments header gets
the same quiet glyph. Nothing about what the groups
contain changes.
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


class MoreRailTests(unittest.TestCase):
    def test_group_headings_are_quiet(self):
        css = between("#moreCard .card h2 {", "}")
        self.assertIn("font-size: 13px", css)
        self.assertIn("color: var(--muted)", css)

    def test_the_five_groups_are_all_still_there(self):
        # v0.15 Batch E1: Overall feedback was absorbed by the
        # Editor thread; the other four groups stay.
        more = between('id="moreCard"', "</aside>")
        for gid in ("styleCard", "revisedCard",
                    "finishCard", "historyCard"):
            self.assertIn('id="%s"' % gid, more)
        self.assertNotIn('id="overallCard"', TEMPLATE)

    def test_group_action_rows_carry_a_small_icon(self):
        for bid in ("runRender", "undoBtn", "redoBtn"):
            i = TEMPLATE.index('id="%s"' % bid)
            seg = TEMPLATE[i:i + 320]
            self.assertIn("<svg", seg, bid)
        i = TEMPLATE.index("Grade the changes")
        self.assertIn("<svg", TEMPLATE[max(0, i - 320):i])

    def test_the_row_icons_are_sized_small(self):
        self.assertIn("#moreCard button svg", STYLE)

    def test_the_comments_header_carries_the_quiet_glyph(self):
        # v0.15 Batch E1: the head is the editor's: its avatar
        # mark, its name, the waiting count, and the pane
        # buttons with their glyphs, on the same 44px row.
        head = between('id="commentsHead"', 'id="threadList"')
        self.assertIn("<svg", head)
        self.assertIn('id="editorAvatar"', head)
        self.assertIn('id="editorName"', head)
        self.assertIn('id="commentsHeadCount"', head)


if __name__ == "__main__":
    unittest.main()
