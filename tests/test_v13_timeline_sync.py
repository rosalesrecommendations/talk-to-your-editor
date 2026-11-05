"""v0.13, item 1: a timeline click moves the transcript too.

Any seek that starts from the timeline band (track click or
drag, ruler, overview, and the segments, which live inside
the track) runs the transcript sync: the word at the new
playhead is marked current and its line is scrolled into
view, on release after a drag and without fighting the drag
while it moves. Seeks from comment cards and word clicks
already synced and keep working (their paths are untouched).

Pinned at source level; the fit battery (fit-test-013.mjs,
checks T1-T2) drives the real events against the sample job.
Everything is offline.
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


class TimelineSyncSourceTests(unittest.TestCase):
    def test_the_seek_itself_syncs_throttled(self):
        body = between("function tlSeekEvent(e)",
                       "function tlHoverEvent(e)")
        # The seek still lands first; the sync follows it,
        # unforced so the 100ms guard keeps a drag live-safe.
        self.assertLess(body.index("vid.currentTime"),
                        body.index("syncTranscriptPlayback(false);"))
        self.assertIn("syncTranscriptPlayback(false);", body)

    def test_every_band_release_lands_the_full_jump(self):
        for marker in (
            'tlWaveRow.addEventListener("pointerup"',
            'tlTrack.addEventListener("pointerup"',
            'tlRuler.addEventListener("pointerup"',
        ):
            start = TEMPLATE.index(marker)
            body = TEMPLATE[start:start + 320]
            self.assertIn("syncTranscriptToPlayhead();", body, marker)
        overview = between("var tlOvUp = function ()",
                           'tlOverviewEl.addEventListener("pointerup"')
        self.assertIn("syncTranscriptToPlayhead();", overview)

    def test_the_landing_marks_and_scrolls_follow_or_not(self):
        body = between("function syncTranscriptToPlayhead()",
                       "function setFollow(on)")
        # It forces the mark through the standing sync...
        self.assertIn("syncTranscriptPlayback(true);", body)
        # ...then scrolls the current line into view itself,
        # without asking the follow state.
        self.assertNotIn("followOn", body)
        self.assertIn('wordEl.closest(".tx-para")', body)
        self.assertIn("transcriptPanel.scrollTo(", body)
        # It moves only when the line is actually out of view.
        self.assertIn("lineRect.bottom < panelRect.top", body)
        self.assertIn("lineRect.top > panelRect.bottom", body)

    def test_the_existing_sync_paths_are_untouched(self):
        # Comment cards still seek through seekToEdited.
        self.assertIn(
            "seekToEdited(commentEditedTs(first), first.fmt);",
            TEMPLATE)
        # Word clicks still sync after seeking.
        click = between(
            "// Clicking a word outside fix mode does one job only",
            'transcriptEl.addEventListener("dblclick"')
        self.assertIn("syncTranscriptPlayback(true);", click)
        # And the video's own seeked event still syncs.
        self.assertIn(
            'vid.addEventListener("seeked", function () {',
            TEMPLATE)


if __name__ == "__main__":
    unittest.main()
