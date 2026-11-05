"""v0.14 Batch F: the secondary items (REPORT issues
5-10), each landed only where it rode machinery the app
already had, and named as deferred where it did not.

- X1 (issue 5): a pick touching a held "Recommended to
  remove" spot (the gold store) shows the recommendation
  as a line in the picked-words bar, with Cut offered
  first for that pick. The line rides the existing
  decision line: decisionLineFor already read the flags
  and the cut spans; heldLineFor adds the gold store
  between them, so flags keep their precedence and no new
  state machinery exists. A held earlier take reads the
  review's settled line; any other held spot reads its
  own one-sentence reason. The Cut-first part is a row
  reorder inside updateSelBar, for the held pick only;
  the settled markup order never changes.
- X2 (issue 6): clicking (or scrubbing) the timeline
  overview strip seeks the playhead to that spot, the way
  the ruler, the track, and the waveform already seek,
  while the window still centres on the spot in the same
  gesture. The seek sets the file time directly (no
  playback starts), and release syncs the transcript to
  the playhead even when the window did not move.
- X3 (issue 7): the "Why did you put that back?" prompt
  is one slim line under the transcript header (question,
  the same four answers, and Skip on one row) instead of
  a card parked at the bottom of the reading column. It
  still leaves after the first answer or Skip, the
  answers still post the same /api/why save, and the
  Other note box still opens when asked for. Only the
  markup's home and shape changed.
- X4 (issue 8): the transport clock is labeled "Edited
  time" in the micro tier, beside the readout; the
  readout's own text (and every writer of it) is
  untouched, and the video's native controls stay.
- X5 (issue 9): below reading size (120 px/s, the zoom
  the fit battery's readability checks settle on: at
  120 px/s at least 90% of word slots render whole) the
  band draws its blocks without the word row. The gate
  is the renderBlocks word-print condition itself, so
  the blocks, the map, and the 120 px/s rendering are
  exactly as they were.
- X6 (issue 10): DEFERRED, no code change. The pane-head
  glyph is the Make wider / Back to normal toggle
  (toggleExpandedPanel); the app's own icon set has no
  plainer glyph for that action (the only other
  two-arrow glyph belongs to Swap sides), so there was
  nothing to swap in. "Edit choices" also stays as is:
  its 48px text-button shape is counted in the symmetry
  audit's consistent 48 family. The X6 tests below pin
  the identification the deferral rests on.

The on-screen proofs for the landed items live in the
fit battery (fit-test-014.mjs, [0.14F] section). What is
pinned here is the logic, mirrored in Python where the
page's rule is a pure choice, plus the source-level
facts that keep the mirrors honest.
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


# ---------------------------------------------------------------------------
# X1: the held pick's line and Cut first
# ---------------------------------------------------------------------------

def held_line(gold, sel_start, sel_end):
    """The page's heldLineFor over decisionLineFor's gold
    branch: the first gold span overlapping the pick gives
    the line; a retake reads the settled line, any other
    held spot reads its own reason, and no overlap reads
    no line."""
    if sel_start is None or sel_end is None:
        return ""
    for g in gold:
        if g["word_start"] < sel_end and sel_start < g["word_end"]:
            if g.get("kind") == "retake":
                return ("Earlier take, said again right after. "
                        "Cut it, or leave it as is.")
            return g.get("reason") or ""
    return ""


GOLD = [
    {"word_start": 807, "word_end": 810, "kind": "retake",
     "reason": "This earlier take is said again right after, so "
               "the editor recommends cutting it and leaves the "
               "call to you."},
    {"word_start": 200, "word_end": 206, "kind": "negation",
     "reason": "The editor recommends cutting this repeated "
               "part, but it includes a negative statement, so "
               "check the meaning before you decide."},
]


class HeldLineMirrorTests(unittest.TestCase):
    def test_a_pick_on_a_held_retake_reads_the_settled_line(self):
        self.assertEqual(
            held_line(GOLD, 807, 810),
            "Earlier take, said again right after. "
            "Cut it, or leave it as is.")

    def test_a_pick_partly_overlapping_still_reads_the_line(self):
        self.assertEqual(
            held_line(GOLD, 805, 808),
            "Earlier take, said again right after. "
            "Cut it, or leave it as is.")

    def test_another_held_kind_reads_its_own_reason(self):
        self.assertEqual(
            held_line(GOLD, 200, 206),
            "The editor recommends cutting this repeated part, "
            "but it includes a negative statement, so check the "
            "meaning before you decide.")

    def test_no_overlap_and_no_pick_read_no_line(self):
        self.assertEqual(held_line(GOLD, 300, 310), "")
        self.assertEqual(held_line(GOLD, None, None), "")
        self.assertEqual(held_line([], 807, 810), "")


class HeldLineSourceTests(unittest.TestCase):
    def test_the_line_rides_the_existing_decision_line(self):
        body = between("function decisionLineFor()",
                       "function closeSelMenu()")
        # Flags keep their precedence, the held line comes
        # next, the cut spans last: the order in the body is
        # the order the bar reads them in.
        self.assertLess(body.index("flags[0].recap"),
                        body.index("heldLineFor()"))
        self.assertLess(body.index("heldLineFor()"),
                        body.index("editSpans"))

    def test_the_held_helpers_read_the_gold_store(self):
        helper = between("function heldSpanForPick()",
                         "function heldLineFor()")
        self.assertIn("jobGold", helper)
        self.assertIn("g.word_start < selEnd && selStart < g.word_end",
                      helper)
        line = between("function heldLineFor()",
                       "// The decision line:")
        self.assertIn('held.kind === "retake"', line)
        self.assertIn(
            '"Earlier take, said again right after. '
            'Cut it, or leave it as is."', line)
        self.assertIn("held.reason || \"\"", line)

    def test_cut_is_offered_first_for_a_held_pick_only(self):
        bar = between("function updateSelBar()",
                      "function clearSelection()")
        self.assertIn("heldSpanForPick()", bar)
        self.assertIn("selRow.insertBefore(selCutBtn, selCommentBtn)",
                      bar)
        # Every other pick puts the settled order back.
        self.assertIn(
            "selRow.insertBefore(selCommentBtn, selCutBtn)", bar)

    def test_the_markup_order_itself_never_changed(self):
        row = between('<div class="sel-row">', '<div id="selDecision"')
        self.assertLess(row.index('id="selCount"'),
                        row.index('id="selCommentBtn"'))
        self.assertLess(row.index('id="selCommentBtn"'),
                        row.index('id="selCutBtn"'))
        self.assertLess(row.index('id="selCutBtn"'),
                        row.index('id="selContextBtn"'))


# ---------------------------------------------------------------------------
# X2: the overview strip seeks
# ---------------------------------------------------------------------------

class OverviewSeekTests(unittest.TestCase):
    def test_the_seek_is_the_band_seek_without_playback(self):
        seek = between("function tlOverviewSeek(t)",
                       "if (tlOverviewEl && tlScrollEl)")
        self.assertIn("editedToFileTime(t)", seek)
        self.assertIn("vid.currentTime", seek)
        self.assertIn("updatePlayhead();", seek)
        self.assertIn("syncTranscriptPlayback(false);", seek)
        # Unlike seekTo/seekToEdited, a band seek never
        # starts playback on its own.
        self.assertNotIn("vid.play()", seek)
        self.assertNotIn("seekTo(", seek)

    def test_a_click_seeks_and_still_centres_the_window(self):
        down = between('tlOverviewEl.addEventListener("pointerdown"',
                       'tlOverviewEl.addEventListener("pointermove"')
        self.assertIn('mode: "scrub"', down)
        self.assertIn("t * scale - tlScrollEl.clientWidth / 2", down)
        self.assertIn("tlOverviewSeek(t);", down)

    def test_a_scrub_seeks_as_it_moves(self):
        move = between('tlOverviewEl.addEventListener("pointermove"',
                       "var tlOvUp")
        self.assertIn('tlOvDrag.mode === "box"', move)
        self.assertIn("tlOverviewSeek(t);", move)

    def test_release_syncs_even_when_the_window_did_not_move(self):
        up = between("var tlOvUp = function ()",
                     'tlOverviewEl.addEventListener("pointerup"')
        self.assertIn("seeked", up)
        self.assertIn("syncTranscriptToPlayhead();", up)

    def test_the_strip_says_it_seeks_now(self):
        self.assertIn("Click to seek there", TEMPLATE)


# ---------------------------------------------------------------------------
# X3: the slim why line under the transcript header
# ---------------------------------------------------------------------------

class SlimWhyLineTests(unittest.TestCase):
    """X3's slim line moved into the Editor thread (v0.15
    Batch E1): the question is a thread entry whose answer
    pills are quick-action buttons there."""

    def why_entry(self) -> str:
        return between("function threadWhyEl(entry)",
                       "function threadEntryEl(entry)")

    def test_the_line_sits_under_the_header_before_the_panel(self):
        # It sits nowhere under the header now: no why card
        # exists in the transcript column at all, and the
        # question renders inside the thread list.
        self.assertNotIn('id="whyCard"', TEMPLATE)
        self.assertIn('id="threadList"', TEMPLATE)
        self.assertIn("thread-why", self.why_entry())

    def test_one_row_carries_question_answers_and_skip(self):
        entry = self.why_entry()
        order = [entry.index('"%s"' % name) for name in (
            "whyStory", "whySound", "whySayIt", "whyOther",
            "whySkip")]
        self.assertEqual(order, sorted(order))
        # The same four answers, with the same choices.
        for choice in ("story", "sound", "say_it"):
            self.assertIn(f'"{choice}"', entry)
        self.assertIn('"other"', entry)

    def test_the_line_is_styled_slim(self):
        # The thread entry's question is one bold line, and
        # its answer buttons take the thread's action tier
        # (40 tall since v0.17's compact chat pass, Justin's
        # "that chat interface is huge" note).
        self.assertIn(
            ".thread-why .thread-question { font-weight: 600; }",
            STYLE)
        self.assertIn(
            ".thread-why .thread-actions button { min-height: 40px; }",
            STYLE)
        self.assertNotIn("#whyCard", STYLE)

    def test_the_note_box_still_rides_with_the_line(self):
        entry = self.why_entry()
        self.assertIn("noteRow.hidden = true;", entry)
        self.assertIn('note.id = "whyNote";', entry)
        self.assertIn('save.id = "whySave";', entry)
        self.assertIn('mic.id = "whyDictate";', entry)


# ---------------------------------------------------------------------------
# X4: the transport clock is labeled "Edited time"
# ---------------------------------------------------------------------------

class EditedTimeLabelTests(unittest.TestCase):
    def test_the_label_sits_beside_the_readout_not_inside_it(self):
        row = between('id="fmtRow"', 'id="tlZoom"')
        self.assertIn(
            '<span class="transport-clock-label">Edited time</span>',
            row)
        self.assertLess(row.index("transport-clock-label"),
                        row.index('id="clock"'))
        # The readout element itself still starts as the
        # two times and nothing else, so every writer of
        # #clock (which sets its text from the readout
        # function) is untouched.
        self.assertIn('id="clock">0:00 / 0:00<', TEMPLATE)
        # The words appear exactly once in the whole page:
        # the label. They are not baked into the readout
        # string anywhere.
        self.assertEqual(TEMPLATE.count("Edited time"), 1)

    def test_the_label_uses_the_micro_tier(self):
        self.assertIn(
            ".transport-clock-label { font-size: 11px;", STYLE)

    def test_the_readout_function_is_unchanged(self):
        body = between("function transportReadout()",
                       "function tlDuration()")
        self.assertIn('fmtTransport(displayTime()) + " / " + '
                      'fmtTransport(editedDuration())', body)


# ---------------------------------------------------------------------------
# X5: no word row below reading size
# ---------------------------------------------------------------------------

def word_row_drawn(scale, read_pps=120):
    """The page's renderBlocks gate: the word row prints
    only at reading size or above."""
    return scale >= read_pps


class WordRowGateMirrorTests(unittest.TestCase):
    def test_fit_zoom_draws_no_word_row(self):
        # The IMG_2516 edit at fit is about 2 px/s.
        self.assertFalse(word_row_drawn(2))
        self.assertFalse(word_row_drawn(60))
        self.assertFalse(word_row_drawn(119.9))

    def test_reading_size_and_above_draw_it(self):
        self.assertTrue(word_row_drawn(120))
        self.assertTrue(word_row_drawn(200))


class WordRowGateSourceTests(unittest.TestCase):
    def test_the_gate_is_on_the_word_print_condition(self):
        blocks = between("function renderBlocks()",
                         "function renderTlCaption()")
        self.assertIn("if (scale >= TL_READ_PPS &&", blocks)
        # The fit rule inside the gate is the one that was
        # always there.
        self.assertIn("if (wp >= tlTextWidth(text) + 4)", blocks)
        self.assertLess(blocks.index("scale >= TL_READ_PPS"),
                        blocks.index("wp >= tlTextWidth(text)"))

    def test_reading_size_is_the_settled_120(self):
        self.assertIn("var TL_READ_PPS = 120;", TEMPLATE)


# ---------------------------------------------------------------------------
# X6: deferred — the identification it rests on
# ---------------------------------------------------------------------------

class PaneHeadGlyphIdentificationTests(unittest.TestCase):
    """The </>-looking glyph is the pane width toggle.
    These pins record what it does, which is what the
    deferral was decided from: the action it names, and
    that the app's one other two-direction arrow glyph
    already belongs to a different action (Swap sides)."""

    def test_the_glyph_is_the_make_wider_toggle(self):
        for btn in ("videoExpandBtn", "transcriptExpandBtn",
                    "commentsExpandBtn"):
            self.assertIn(f'id="{btn}" class="icon-btn"', TEMPLATE)
        script = between("function toggleExpandedPanel(name)",
                         "var swapSidesBtn")
        self.assertIn('toggleExpandedPanel("video")', TEMPLATE)
        self.assertIn('toggleExpandedPanel("transcript")', TEMPLATE)
        self.assertIn('toggleExpandedPanel("comments")', TEMPLATE)
        self.assertIn("layoutState.expanded", script)

    def test_its_name_flips_with_its_state(self):
        layout = between("function applyLayout()",
                         "function clearExpandedPanel(name)")
        self.assertIn('"Back to normal" : "Make wider"', layout)


if __name__ == "__main__":
    unittest.main()
