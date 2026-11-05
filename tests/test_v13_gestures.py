"""v0.13, item 2: the word tapping model (Justin's spec).

One click on a word does one job only: it moves the playhead
there. It never picks, it leaves no highlight behind, and it
clears any pick that was standing, so two words can never stay
highlighted from two separate clicks (the bug Justin hit on
0.12). Picking takes a deliberate gesture: a double click
picks exactly that word and opens the action surface, and a
drag across words paints the pick while it moves and opens
the surface once, on release. Shift+click extends a pick only
when a pick already exists. Fix-words mode keeps its own
click picking, unchanged.

Named behavior change: the 0.12 one-tap restore on a struck
word is retired. A struck word now comes back from the action
surface (double-click or drag to pick it, then Bring back),
because the single click has one job only. The orange unsure
flag and its note wording stay.

Pinned here, at source level, in the style of the other page
tests (the scripted pointer proofs live in the fit harness,
fit-test-013.mjs, checks W1-W4):

- W1/W4: the plain click seeks, clears the pick, and never
  sets one; the shift extension is guarded on a standing pick;
- W2: the double click picks exactly the word and opens the
  surface, and no longer pins a comment directly;
- W3: the drag paints through refreshFixSelection only, and
  the surface opens in the pointerup, once;
- W5: the gesture state machine, mirrored below over the
  page's own branch conditions, plus the retirement: no
  tapPress machinery remains anywhere in the template.

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


def click_handler() -> str:
    return between(
        "// Clicking a word outside fix mode does one job only",
        'transcriptEl.addEventListener("dblclick"')


def dblclick_handler() -> str:
    return between(
        'transcriptEl.addEventListener("dblclick"',
        "// The strip's own wiring retired with it (v0.13, item 3):")


def drag_block() -> str:
    return between(
        "// -- word gestures (v0.13, item 2)",
        "// Clicking a word outside fix mode does one job only")


# ---------------------------------------------------------------------------
# The gesture state machine, mirrored over the page's branches
# ---------------------------------------------------------------------------

def gesture(events, pick=None):
    """Run a sequence of word gestures the way the page does.

    events: tuples of ("click", index, end, shift),
    ("dblclick", index, end), or ("drag", start, end, moved).
    Returns (pick, surface_open, seeks): the pick standing at
    the end, whether the action surface is open, and the list
    of words a seek landed on."""
    seeks = []
    surface = pick is not None
    for ev in events:
        if ev[0] == "click":
            _, index, end, shift = ev
            seeks.append(index)
            if shift and pick is not None:
                lo, hi = pick
                pick = (min(lo, index), max(hi, end))
                surface = True
            else:
                pick = None
                surface = False
        elif ev[0] == "dblclick":
            _, index, end = ev
            pick = (index, end)
            surface = True
        elif ev[0] == "drag":
            _, start, end, moved = ev
            if moved:
                pick = (min(start, end), max(start, end))
                surface = True
    return pick, surface, seeks


class StateMachineMirrorTests(unittest.TestCase):
    """W5: click vs double-click vs drag, as Justin specced."""

    def test_two_plain_clicks_leave_no_pick(self):
        pick, surface, seeks = gesture(
            [("click", 3, 4, False), ("click", 9, 10, False)])
        self.assertIsNone(pick)
        self.assertFalse(surface)
        self.assertEqual(seeks, [3, 9])

    def test_a_plain_click_clears_a_standing_pick(self):
        pick, surface, _ = gesture(
            [("dblclick", 5, 6), ("click", 20, 21, False)])
        self.assertIsNone(pick)
        self.assertFalse(surface)

    def test_a_double_click_picks_exactly_that_word(self):
        pick, surface, _ = gesture([("dblclick", 5, 6)])
        self.assertEqual(pick, (5, 6))
        self.assertTrue(surface)

    def test_a_drag_picks_the_run_it_crossed(self):
        pick, surface, _ = gesture([("drag", 4, 9, True)])
        self.assertEqual(pick, (4, 9))
        self.assertTrue(surface)
        # A press that never moved is a click, not a drag pick.
        pick, surface, _ = gesture([("drag", 4, 5, False)])
        self.assertIsNone(pick)
        self.assertFalse(surface)

    def test_shift_click_extends_only_a_standing_pick(self):
        pick, surface, _ = gesture(
            [("dblclick", 5, 6), ("click", 9, 10, True)])
        self.assertEqual(pick, (5, 10))
        self.assertTrue(surface)
        pick, surface, seeks = gesture([("click", 9, 10, True)])
        self.assertIsNone(pick)
        self.assertFalse(surface)
        self.assertEqual(seeks, [9])


class ClickSourceTests(unittest.TestCase):
    """W1/W4: the plain click, read off the page itself."""

    def test_the_plain_click_seeks(self):
        body = click_handler()
        self.assertIn("vid.currentTime = editPlaybackApplies()", body)
        self.assertIn("syncTranscriptPlayback(true);", body)

    def test_the_plain_click_clears_the_pick_and_never_sets_one(self):
        body = click_handler()
        # v0.15 Batch G (item 32): the seek gained its own
        # if/else (the edited-clock conversion), so the plain
        # branch is the handler's LAST else now.
        plain = body[body.rindex("} else {"):]
        self.assertIn("selStart = null;", plain)
        self.assertIn("selEnd = null;", plain)
        self.assertIn("refreshFixSelection();", plain)
        self.assertIn("updateSelBar();", plain)
        # The plain path never assigns a word to the pick: the
        # only selStart/selEnd assignments in the handler are
        # the shift extension's growth of a standing pick and
        # this clearing.
        self.assertNotIn("selStart = index;", plain)
        self.assertNotIn("selEnd = spanEnd;", plain)

    def test_shift_extend_is_guarded_on_a_standing_pick(self):
        body = click_handler()
        self.assertIn(
            "if (e.shiftKey && selStart !== null && selEnd !== null)",
            body)

    def test_a_drag_that_just_ended_is_not_a_click(self):
        body = click_handler()
        self.assertIn("if (dragJustEnded) { return; }", body)

    def test_fix_mode_keeps_its_own_click_picking(self):
        body = click_handler()
        self.assertIn("if (fixMode) {", body)
        self.assertIn("selectSpan(index, spanEnd);", body)
        # Fix picking is decided before any gesture branch.
        self.assertLess(body.index("selectSpan(index, spanEnd);"),
                        body.index("e.shiftKey"))


class DoubleClickSourceTests(unittest.TestCase):
    """W2: the double click picks the word, opens the surface."""

    def test_it_picks_exactly_the_word(self):
        body = dblclick_handler()
        self.assertIn("selStart = index;", body)
        self.assertIn("selEnd = spanEnd;", body)
        self.assertIn("refreshFixSelection();", body)
        self.assertIn("updateSelBar();", body)

    def test_it_no_longer_pins_a_comment_directly(self):
        body = dblclick_handler()
        self.assertNotIn("pinDraft", body)
        self.assertNotIn("pinnedWordStart", body)
        self.assertNotIn("draft.focus", body)

    def test_it_steps_around_markers_and_fix_mode(self):
        body = dblclick_handler()
        self.assertIn("if (fixMode) { return; }", body)
        self.assertIn('e.target.closest(".tflag")', body)


class DragSourceTests(unittest.TestCase):
    """W3: the drag paints live; the surface opens on release."""

    def test_pointerdown_starts_an_unmoved_pick(self):
        body = drag_block()
        self.assertIn(
            "dragPick = { start: index, end: end, moved: false };",
            body)

    def test_the_move_paints_without_opening_the_surface(self):
        body = drag_block()
        move = between(
            'document.addEventListener("pointermove"',
            'document.addEventListener("pointerup"')
        # v0.17 (feedback item 5): the move only records the
        # point and queues one paint per frame; paintDragPick
        # does the picking and the painting.
        self.assertIn("dragPaintMoved = true;", move)
        self.assertIn("requestAnimationFrame(paintDragPick);", move)
        self.assertNotIn("updateSelBar();", move)
        paint = TEMPLATE[
            TEMPLATE.index("function paintDragPick()"):
            TEMPLATE.index("function paintDragPick()") + 900]
        self.assertIn("dragPick.moved = true;", paint)
        self.assertIn("selStart = lo;", paint)
        self.assertIn("selEnd = hi;", paint)
        self.assertIn("refreshFixSelection();", paint)

    def test_the_release_opens_the_surface_once(self):
        body = drag_block()
        up = body[body.index('document.addEventListener("pointerup"'):]
        self.assertIn("var wasMoved = dragPick.moved;", up)
        self.assertIn("dragJustEnded = true;", up)
        self.assertIn("updateSelBar();", up)

    def test_a_release_outside_the_transcript_arms_nothing(self):
        # v0.15, item 18: the release point decides. Inside
        # the transcript panel the surface opens; anywhere
        # else the painted pick is dropped, and a cancelled
        # pointer drops it the same way.
        body = drag_block()
        up = body[body.index('document.addEventListener("pointerup"'):]
        self.assertIn("document.elementFromPoint(e.clientX, "
                      "e.clientY)", up)
        self.assertIn("transcriptPanel.contains(el)", up)
        self.assertIn("selStart = null;", up)
        cancel = body[body.index(
            'document.addEventListener("pointercancel"'):]
        self.assertIn("selStart = null;", cancel)
        self.assertIn("refreshFixSelection();", cancel)


class OneTapRetirementTests(unittest.TestCase):
    """W5: the 0.12 one-tap restore is retired, by name."""

    def test_no_tap_press_machinery_remains(self):
        self.assertNotIn("tapPress", TEMPLATE)
        self.assertNotIn("one-tap restore, kept", TEMPLATE)

    def test_the_retirement_is_named_where_the_block_stood(self):
        note = between("The 0.12 one-tap restore on a struck word "
                       "is retired", "// -- keyboard shortcuts")
        self.assertIn("double-click or drag to pick it, then Bring",
                      note)
        self.assertIn("The orange unsure flag and its note wording "
                      "stay.", note)

    def test_no_click_path_restores_a_word(self):
        # Restoring now runs only from the surface's Bring back
        # (and the popup's own button): the word click handlers
        # never call the range path.
        self.assertNotIn('runSelectionToggle("kept");',
                         click_handler())
        self.assertNotIn('runSelectionToggle("kept");',
                         dblclick_handler())


if __name__ == "__main__":
    unittest.main()
