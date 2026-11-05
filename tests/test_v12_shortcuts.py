"""v0.12, closing polish: the keyboard shortcuts list.

The shortcuts in this round (timeline zoom, the Fit toggle,
commenting on picked words) shipped with no list anywhere a
person could find them; the older playback shortcuts already
lived under a "Keyboard shortcuts" heading in the How this
works pop up. The new list joins them there, in the same
panel (not Settings, and not a new panel), as quiet small
lines: a definition list, one line each, in plain words.

Pinned here, at source level, in the style of the other
v0.12 tests:

- the list lives in How this works, under its heading, and
  names the five shortcuts from this round, word for word;
- it is quiet: small text, no new card, panel, or scroll
  region of its own;
- every shortcut it names is real: each key combination is
  checked against the handler that actually runs it.

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


class KeyboardShortcutsListTests(unittest.TestCase):
    """The list exists, in How this works, naming the real keys."""

    def test_the_list_lives_in_how_this_works_under_its_heading(self):
        panel = between('id="howItWorksPanel"', 'id="settingsPanel"')
        self.assertIn("<h3>Keyboard shortcuts</h3>", panel)
        self.assertIn(
            '<dl class="shortcut-list" id="keyboardShortcutsList">', panel
        )
        # It adds to the panel the playback shortcuts already
        # live in; it does not move or duplicate that line.
        self.assertIn('id="shortcutLine"', panel)
        self.assertLess(
            panel.index("<h3>Keyboard shortcuts</h3>"),
            panel.index('id="keyboardShortcutsList"'),
        )

    def test_the_list_is_generated_from_the_shortcut_map(self):
        # v0.13, criterion I3: the How this works list is
        # built by the script from the SHORTCUTS table, the
        # single source of truth for the keys, so the list a
        # reader sees cannot drift from the keys that work.
        # The markup list starts empty.
        listing = between('id="keyboardShortcutsList"', "</dl>")
        self.assertNotIn("<dt>", listing)
        table = between("var SHORTCUTS = [", "];")
        for line in (
            '{ keys: "+ / -", action: "Zoom the timeline in / out" }',
            '{ keys: "Shift+Z", action: "Fit the whole edit / back to your zoom" }',
            '{ keys: "Cmd or Ctrl + scroll", action: "Zoom with the mouse, over the timeline" }',
            # v0.15 Batch F drive-by (P8): since Batch E2 the
            # key carries the pick to the Editor, and the
            # printed name now says so.
            '{ keys: "Cmd or Ctrl + Shift + M", action: "Send the picked words to the Editor" }',
            '{ keys: "Esc", action: "Put the pick away / close the surface" }',
        ):
            self.assertIn(line, table)
        render = between("function renderShortcutList()",
                         "var shortcutToggle")
        self.assertIn('document.createElement("dt")', render)
        self.assertIn('document.createElement("dd")', render)
        self.assertIn("dt.textContent = row.action;", render)
        self.assertIn("dd.textContent = row.keys;", render)

    def test_the_list_is_quiet_and_adds_no_panel_or_scroll_region(self):
        css = between(".shortcut-list {", "}")
        self.assertIn("font-size: 14px", css)
        self.assertIn("color: var(--muted)", css)
        listing = between('id="keyboardShortcutsList"', "</dl>")
        self.assertNotIn("card", listing)
        self.assertNotIn("popup-panel", listing)
        self.assertNotIn("overflow", listing)

    def test_every_named_shortcut_is_a_real_handler(self):
        # Zoom in / out: Cmd/Ctrl with + (=) and -.
        keys = between("function tlZoomKeyOk(e)",
                       'window.addEventListener("resize"')
        self.assertIn('e.key === "=" || e.key === "+"', keys)
        self.assertIn('e.key === "-" || e.key === "_"', keys)
        self.assertIn("e.metaKey || e.ctrlKey", keys)
        # Fit: Shift+Z and the Fit button run the same toggle.
        self.assertIn('e.key === "Z" || e.key === "z"', keys)
        self.assertIn("tlToggleFit();", keys)
        self.assertIn('<button id="tlFitBtn" type="button"', TEMPLATE)
        self.assertIn('tlFitBtn.addEventListener("click"', TEMPLATE)
        # Mouse zoom: Cmd/Ctrl + wheel over the timeline band.
        wheel = between('addEventListener("wheel"',
                        "function tlZoomKeyOk(e)")
        self.assertIn("e.ctrlKey || e.metaKey", wheel)
        # Comment on picked words: Cmd/Ctrl+Shift+M. (The
        # handler moved in v0.13, item 3, next to clearSelection;
        # v0.15 Batch E2, item 15: it now runs the Send to Editor
        # carry, queueing the pick in the Editor panel.)
        comment = between(
            "// Cmd/Ctrl+Shift+M anywhere in the review",
            "// -- leaving the transcript ends its states",
        )
        self.assertIn("e.metaKey || e.ctrlKey", comment)
        self.assertIn("e.shiftKey", comment)
        self.assertIn('.toLowerCase() !== "m"', comment)
        self.assertIn("carryPickToEditor()", comment)
        # Close a pop up: Esc closes the pop ups.
        self.assertIn(
            'if (e.key === "Escape") { closePopups(); }', TEMPLATE)


if __name__ == "__main__":
    unittest.main()
