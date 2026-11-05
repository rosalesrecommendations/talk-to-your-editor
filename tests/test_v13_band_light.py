"""v0.13 item 1, criterion T4 (added by Justin's Descript
steering): the timeline block for the playhead word is lit
on the band, in sync with the transcript's current word,
during playback and after any seek, in both directions.

The band's pieces print each word as its own span when it
fits, or merge words into a group span. The light follows
the same index the transcript marks: a full word span
carries data-index; a group carries the word range it
merged. The sync funnel (syncTranscriptPlayback) drives
both marks, and a band re-render re-lights the standing
word.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "src" / "talk_to_your_editor" / "templates"
            / "test_app.html").read_text(encoding="utf-8")
STYLE = TEMPLATE[TEMPLATE.index("<style>"):TEMPLATE.index("</style>")]


def between(start, end):
    i = TEMPLATE.index(start)
    return TEMPLATE[i:TEMPLATE.index(end, i)]


class BandLightMarkupTests(unittest.TestCase):
    def test_word_spans_carry_their_index(self):
        body = between("function renderBlocks()",
                       "function renderTlCaption()")
        self.assertIn("wd.dataset.index = w.index;", body)

    def test_group_spans_carry_their_word_range(self):
        body = between("function renderBlocks()",
                       "function renderTlCaption()")
        self.assertIn("g.dataset.wordStart = group.firstIndex;",
                      body)
        self.assertIn("g.dataset.wordEnd = group.lastIndex + 1;",
                      body)
        self.assertIn("firstIndex: w.index", body)
        self.assertIn("group.lastIndex = w.index;", body)

    def test_a_fresh_band_relights_the_standing_word(self):
        body = between("function renderBlocks()",
                       "function renderTlCaption()")
        self.assertIn("updateTlWordNow(currentWordIndex);", body)


class BandLightSyncTests(unittest.TestCase):
    def test_the_sync_funnel_drives_the_band_light(self):
        body = between("function syncTranscriptPlayback(force)",
                       "function syncTranscriptToPlayhead()")
        self.assertIn("updateTlWordNow(current.index);", body)
        self.assertIn("updateTlWordNow(null);", body)

    def test_the_light_picks_the_word_or_its_group(self):
        body = between("function updateTlWordNow(idx)",
                       "function syncTranscriptPlayback(force)")
        self.assertIn("'.tl-word[data-index=\"'", body)
        self.assertIn("idx >= s && idx < e", body)
        self.assertIn('classList.add("tl-word-now")', body)
        self.assertIn('classList.remove("tl-word-now")', body)

    def test_the_light_has_its_own_style(self):
        self.assertIn(".tl-word.tl-word-now", STYLE)
        self.assertIn(".tl-word-group.tl-word-now", STYLE)


if __name__ == "__main__":
    unittest.main()
