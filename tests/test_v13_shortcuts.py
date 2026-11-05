"""v0.13 item 9 (shortcuts): the SYNTHESIS final map, with
the How this works list generated from the same SHORTCUTS
table that documents the handlers (criterion I3), and item
8's manual-editing keys deliberately absent (later gate).
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "src" / "talk_to_your_editor" / "templates"
            / "test_app.html").read_text(encoding="utf-8")


def between(start, end):
    i = TEMPLATE.index(start)
    return TEMPLATE[i:TEMPLATE.index(end, i)]


class ShortcutMapTests(unittest.TestCase):
    def test_the_table_carries_the_adopted_map(self):
        table = between("var SHORTCUTS = [", "];")
        for row in (
            '{ keys: "Space", action: "Play or pause" }',
            '{ keys: "J", action: "Shuttle back:',
            '{ keys: "K", action: "Pause and reset the shuttle',
            '{ keys: "L", action: "Shuttle forward:',
            '{ keys: "Up / Down", action: "Previous / next change" }',
            '{ keys: "; / \'", action: "Previous / next change, without the arrow keys" }',
            '{ keys: "+ / -", action: "Zoom the timeline in / out" }',
            '{ keys: "Shift+Z", action: "Fit the whole edit / back to your zoom" }',
            '{ keys: "M", action: "Pin a note at the playhead" }',
            # v0.15 Batch F drive-by (P8): the printed name
            # matches the Batch E2 carry action.
            '{ keys: "Cmd or Ctrl + Shift + M", action: "Send the picked words to the Editor" }',
            '{ keys: "Cmd or Ctrl + Z", action: "Undo the last edit" }',
            '{ keys: "Cmd or Ctrl + Shift + Z", action: "Redo" }',
            '{ keys: "Esc", action: "Put the pick away / close the surface" }',
        ):
            self.assertIn(row, table)

    def test_backspace_names_what_it_cuts(self):
        # v0.15 Batch G (item 23): Justin's mapping replaced
        # the old row — the strike moved to the modifier chord
        # and plain Backspace now names the whole-line removal.
        table = between("var SHORTCUTS = [", "];")
        self.assertIn(
            '{ keys: "Cmd or Ctrl + Backspace", action: '
            '"Strike the picked words" }', table)
        self.assertIn(
            '{ keys: "Backspace", action: "Remove the whole '
            'line the pick is in" }', table)
        self.assertNotIn("Cut the picked words", table)

    def test_no_manual_editing_keys_in_this_round(self):
        # v0.16: the manual-editing round landed the keys
        # this test used to ban. The split, clipboard, and
        # delete rows are now expected in the table; the
        # blade-tool and nudge keys the design deliberately
        # left out stay banned.
        table = between("var SHORTCUTS = [", "];")
        for banned in ("Cmd+B", "Shift+T", "nudge"):
            self.assertNotIn(banned, table)
        for row in (
            '{ keys: "Cmd or Ctrl + Shift + S", action: '
            '"Split the piece at the playhead" }',
            '{ keys: "Cmd or Ctrl + X / C / V", action: '
            '"Cut, copy, or paste a piece on the timeline" }',
            '{ keys: "Delete", action: "Take out the piece '
            'picked on the timeline" }',
        ):
            self.assertIn(row, table)

    def test_the_list_is_generated_from_the_table(self):
        self.assertIn(
            '<dl class="shortcut-list" id="keyboardShortcutsList"></dl>',
            TEMPLATE)
        render = between("function renderShortcutList()",
                         "var shortcutToggle")
        self.assertIn("SHORTCUTS.forEach", render)
        self.assertIn("dt.textContent = row.action;", render)
        self.assertIn("dd.textContent = row.keys;", render)

    def test_the_shortcut_line_keeps_only_its_switch(self):
        line = between('id="shortcutLine"', "</p>")
        self.assertIn('id="shortcutToggle"', line)
        self.assertNotIn("Shortcuts: Space play or pause", line)


class ShuttleTests(unittest.TestCase):
    def test_shuttle_helpers_exist_with_the_rate_cap(self):
        for marker in ("function shuttleFwd()", "function shuttleBack()",
                       "function shuttleStop()",
                       "Math.min(4, shuttleRate * 2)",
                       "vid.playbackRate = shuttleRate;"):
            self.assertIn(marker, TEMPLATE)

    def test_k_hold_plus_tap_steps_one_frame(self):
        handler = between('case "k":', 'case "ArrowLeft":')
        self.assertIn("kHeld = true;", handler)
        self.assertIn("stepBy(-1 / 30);", handler)
        self.assertIn("stepBy(1 / 30);", handler)
        self.assertIn("shuttleStop();", handler)

    def test_change_keys_and_their_aliases(self):
        handler = between('case "ArrowUp":', 'case "ArrowLeft":')
        self.assertIn("jumpChange(-1);", handler)
        self.assertIn("jumpChange(1);", handler)
        self.assertIn('case ";":', handler)
        self.assertIn('case "\'":', handler)


class ZoomKeyTests(unittest.TestCase):
    def test_zoom_is_plain_plus_and_minus_now(self):
        handler = between("Zoom keys are plain + and - in 0.13",
                          "vid.addEventListener(\"loadedmetadata\"")
        self.assertIn(
            '!mod && !e.altKey && (e.key === "=" || e.key === "+")',
            handler)
        self.assertIn(
            '!mod && !e.altKey && (e.key === "-" || e.key === "_")',
            handler)
        self.assertNotIn('mod && (e.key === "="', handler)
        # Fit and the mouse wheel zoom stay as they were.
        self.assertIn("tlToggleFit();", handler)


if __name__ == "__main__":
    unittest.main()
