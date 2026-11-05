"""v0.11.1 comments composer: item 3, as sharpened at 9:05 PM.

Justin's Oct 28, 2026 screenshots of 0.11 showed the pin a comment
card with three big buttons: a blue "Comment at current time"
pill, a "Dictate" pill, and a blue "Save comment" pill. His model
screenshot showed what he wanted instead: a chat style input bar
with a microphone icon and a round blue send button with an up
arrow at the right end.

The composer is now built that way:

- The text box is one clean input. Beside it, in the same row,
  sit the microphone button and the round send button.
- Dictate (#draftDictate) is an icon button with a microphone
  drawn in inline SVG. Its plain name rides along as a title and
  an aria-label: "Dictate".
- Save (#save) is an icon button with an up arrow in inline
  SVG, still blue, with title and aria-label "Save comment".
  v0.14 Batch B: it takes the one icon-button shape, 38x38
  with radius 10 (it was a 40px round button before).
- Comment at current time (#pin) is a quiet text button with
  a small clock icon, no blue fill. v0.14 Batch B: it takes
  the rail's action tier, 48 tall with a 16px label (it was
  40 tall at 14px, the old header button size, before). It
  is still one tap to pin at the playhead.
- The seconds entry, the format picker, and the keep playing
  switch are still in the card, and the dictation status line
  still sits under the composer row.

Same ids, same handlers, same dictation route. One supporting
change inside the shared dictation code: the button's starting
content is now kept as markup and restored as markup, so the
microphone icon comes back after a recording. For the plain word
Dictate buttons the markup is only their word, so their behavior
is unchanged. (v0.15 Batch D, item 7: those plain word buttons
are microphone icon buttons now too, so every dictation button
restores an icon the same way.)

These tests read the page template the same way the other layout
tests do. Everything is offline.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")

# The standard small button size in this app: the header buttons
# (.topbar-btn) carry exactly these values, and so do the pass
# buttons and the transcript action strip buttons.
STANDARD = {
    "min-height": "40px",
    "padding": "8px 14px",
    "font-size": "14px",
}

# The page wide button size plain buttons size from.
# v0.14 Batch G (G8): the side padding moved 20px to 22px,
# the nearest step on the spacing scale {4, 8, 12, 16, 22};
# 20 sat off the scale on every plain pill button.
PAGE_WIDE = {
    "min-height": "48px",
    "padding": "12px 22px",
    "font-size": "16px",
}

# v0.14 Batch B: the pin button's action tier in the rail —
# 48 tall with a 16px label (padding unchanged from the old
# header size it used to share with .topbar-btn).
# v0.14 Batch G (G2): the side padding moved 14px to 12px,
# the nearest step on the spacing scale that still leaves
# the 16px label room on one line (16px leaves less, and
# the label wrapped to two lines at 54 tall before); the
# rule also carries white-space: nowrap now, which the
# exact-48 measurement needs (checked in test_v14_batch_g).
PIN_ACTION = {
    "min-height": "48px",
    "padding": "8px 12px",
    "font-size": "16px",
}


def button_tag(button_id: str) -> str:
    match = re.search(
        r'<button[^>]*id="' + re.escape(button_id) + r'"[^>]*>'
        r".*?</button>",
        TEMPLATE,
        re.DOTALL,
    )
    assert match is not None, button_id
    return match.group(0)


def visible_text(tag: str) -> str:
    """The words a sighted user reads on the button itself."""
    body = tag[tag.index(">") + 1:tag.rindex("</button>")]
    body = re.sub(r"<svg.*?</svg>", "", body, flags=re.DOTALL)
    return body.strip()


def rule_values(selector_pattern: str) -> dict:
    match = re.search(selector_pattern + r"\s*\{\s*([^}]*)\}", TEMPLATE)
    assert match is not None, selector_pattern
    values = {}
    for part in match.group(1).split(";"):
        part = part.strip()
        if ":" in part:
            name, value = part.split(":", 1)
            values[name.strip()] = value.strip()
    return values


def sizing(rule: dict) -> dict:
    return {name: rule[name] for name in STANDARD}


def pin_card() -> str:
    # v0.15 Batch E1: the pin card is gone; the composer is
    # the Editor panel's input row. This slice is the panel.
    start = TEMPLATE.index('id="editorPanel"')
    end = TEMPLATE.index('id="moreCard"', start)
    return TEMPLATE[start:end]


class NoBigPillsTests(unittest.TestCase):
    def test_pin_is_not_a_primary_pill(self):
        # v0.15 Batch E1: the pin button is the + attach button
        # on the input row now, and it is not primary either.
        tag = button_tag("editorAttachBtn")
        self.assertNotIn("primary", tag)
        self.assertNotIn('id="pin"', TEMPLATE)

    def test_no_button_shows_the_old_pill_words(self):
        # The blue pills carried their words as big button text.
        # Neither the pin button nor the save button does now:
        # the pin words are small quiet text (checked below) and
        # the save words live only in its title and aria-label.
        self.assertEqual(visible_text(button_tag("save")), "")
        self.assertEqual(visible_text(button_tag("draftDictate")), "")

    def test_page_wide_button_rule_is_untouched(self):
        # The composer fix does not resize other buttons: the
        # rule every other plain button sizes from keeps the
        # page-wide values (whose side padding Batch G later
        # moved to the scale, 20px to 22px; see PAGE_WIDE).
        page_wide = rule_values(r"(?m)^  button")
        self.assertEqual(sizing(page_wide), PAGE_WIDE)


class PinControlTests(unittest.TestCase):
    """The pin control is the + moment attachment now (E1)."""

    def test_pin_is_a_text_button_at_the_action_tier(self):
        # The attach button takes the input row's icon tier:
        # the composer-btn shape, 38x38 radius 10.
        tag = button_tag("editorAttachBtn")
        self.assertIn("composer-btn", tag)
        self.assertEqual(visible_text(tag), "")
        icon_rule = rule_values(r"\.composer-btn")
        self.assertEqual(icon_rule["width"], "38px")
        self.assertEqual(icon_rule["height"], "38px")
        self.assertEqual(icon_rule["border-radius"], "10px")

    def test_pin_carries_a_clock_icon_and_plain_words(self):
        tag = button_tag("editorAttachBtn")
        self.assertIn("<svg", tag)
        self.assertIn('d="M12 5v14"', tag)
        self.assertIn('d="M5 12h14"', tag)
        self.assertIn('title="Pin this moment"', tag)
        self.assertIn('aria-label="Pin this moment"', tag)


class ComposerRowTests(unittest.TestCase):
    def test_input_mic_and_send_share_one_row_in_that_order(self):
        # v0.15 Batch E1: the row is + attach, box, mic, send.
        card = pin_card()
        row_start = card.index('id="editorInputRow"')
        row_end = card.index('id="draftDictateStatus"', row_start)
        row = card[row_start:row_end]
        positions = [
            row.index('id="%s"' % name)
            for name in ("editorAttachBtn", "draft",
                         "draftDictate", "save")
        ]
        self.assertEqual(positions, sorted(positions))

    def test_composer_row_is_a_flex_row_and_the_input_grows(self):
        row_rule = rule_values(r"\.composer-row")
        self.assertEqual(row_rule["display"], "flex")
        input_rule = rule_values(r"\.composer-row #draft")
        self.assertIn("flex: 1 1 auto", TEMPLATE)
        self.assertEqual(input_rule["min-width"], "0")

    def test_text_box_placeholder_names_the_microphone(self):
        # v0.15 Batch E1: the box is the chat input; its
        # placeholder is the Muse reference's one word.
        card = pin_card()
        self.assertIn('placeholder="Message"', card)


class IconButtonTests(unittest.TestCase):
    def test_mic_button_is_an_icon_button_with_plain_words(self):
        tag = button_tag("draftDictate")
        self.assertIn("composer-btn", tag)
        self.assertIn('title="Dictate"', tag)
        self.assertIn('aria-label="Dictate"', tag)
        self.assertIn("<svg", tag)
        # A microphone: the rounded body and the cradle arc.
        self.assertIn('<rect x="9" y="3" width="6" height="11" rx="3"',
                      tag)
        self.assertIn('d="M5.5 11.5a6.5 6.5 0 0 0 13 0"', tag)

    def test_send_button_is_square_blue_with_an_up_arrow(self):
        tag = button_tag("save")
        self.assertIn("composer-btn", tag)
        self.assertIn("primary", tag)
        # v0.15 Batch E1: the send button is named Send, and it
        # is the one round exception: round, token blue, 38
        # diameter, shown only when the box holds words.
        self.assertIn('title="Send"', tag)
        self.assertIn('aria-label="Send"', tag)
        self.assertIn("hidden", tag)
        self.assertIn("<svg", tag)
        # An up arrow: the stem and the arrow head.
        self.assertIn('d="M12 19V5"', tag)
        self.assertIn('d="M5.5 11.5L12 5l6.5 6.5"', tag)
        icon_rule = rule_values(r"\.composer-btn")
        self.assertEqual(icon_rule["width"], "38px")
        self.assertEqual(icon_rule["height"], "38px")
        send_rule = rule_values(r"#save\.editor-send")
        self.assertEqual(send_rule["border-radius"], "999px")
        self.assertEqual(send_rule["background"], "var(--blue)")
        self.assertEqual(send_rule["width"], "38px")
        self.assertEqual(send_rule["height"], "38px")

    def test_icons_are_inline_svg_with_no_emoji_or_external_assets(self):
        for button_id in ("editorAttachBtn", "draftDictate", "save"):
            tag = button_tag(button_id)
            self.assertIn("<svg", tag)
            self.assertIn('aria-hidden="true"', tag)
            self.assertNotIn("http", tag)
            self.assertNotIn("<img", tag)

    def test_recording_words_still_fit_the_mic_button(self):
        # Through 0.16 the icon button grew into a pill to fit
        # the recording words. v0.17 (feedback item 19): the
        # button keeps its shape; the words ride the status
        # line and a voice level meter sits beside the button.
        self.assertNotIn(
            ".composer-btn.recording, .composer-btn.processing",
            TEMPLATE)
        self.assertIn(".dictate-meter", TEMPLATE)
        self.assertIn("Recording ", TEMPLATE)

    def test_only_the_composer_buttons_carry_the_icon_class(self):
        # The input row's three controls carry the class in
        # markup (attach, mic, send); the thread's why mic is
        # built with it in code, as part of the same row family.
        carriers = re.findall(
            r'<button[^>]*class="[^"]*composer-btn[^"]*"', TEMPLATE
        )
        self.assertEqual(len(carriers), 3)
        for button_id in ("intakeDictate", "fdDictate"):
            self.assertNotIn("composer-btn", button_tag(button_id))
            self.assertNotIn("comment-action", button_tag(button_id))


class StillWiredTests(unittest.TestCase):
    def test_handlers_are_untouched(self):
        # v0.15 Batch E1: the attach button and the send button
        # carry the handlers now; send runs the chat send.
        self.assertIn(
            'editorAttachBtn.addEventListener("click"',
            TEMPLATE,
        )
        self.assertIn(
            'document.getElementById("save").addEventListener("click"',
            TEMPLATE,
        )
        self.assertIn("sendEditorMessage();", TEMPLATE)
        self.assertIn(
            'wireDictation("draftDictate", "draft", '
            '"draftDictateStatus")',
            TEMPLATE,
        )

    def test_dictation_restores_the_buttons_starting_content(self):
        # The icon survives a recording because the starting
        # content is kept and restored as markup.
        self.assertIn("var label = btn.innerHTML;", TEMPLATE)
        self.assertIn("btn.innerHTML = label;", TEMPLATE)
        self.assertNotIn("var label = btn.textContent;", TEMPLATE)

    def test_status_line_stays_right_after_the_composer_row(self):
        card = pin_card()
        self.assertIn('id="draftDictateStatus"', card)
        self.assertLess(
            card.index('id="editorInputRow"'),
            card.index('id="draftDictateStatus"'),
        )
        self.assertLess(
            card.index('id="draftDictateStatus"'),
            card.index('id="saveStatus"'),
        )

    def test_seconds_picker_and_keep_playing_stay_in_the_card(self):
        # v0.15 Batch E1: the seconds picker, the scope picker,
        # and the keep-playing switch left with the pin card;
        # the attached moment shows as one line over the input
        # with an x to take it off.
        card = pin_card()
        self.assertNotIn('id="manualTs"', TEMPLATE)
        self.assertNotIn('id="scope"', TEMPLATE)
        self.assertNotIn('id="keepPlaying"', TEMPLATE)
        self.assertIn('id="editorMomentRow"', card)
        self.assertIn('id="editorMomentText"', card)
        self.assertIn('id="editorMomentClear"', card)

    def test_other_dictate_buttons_are_mic_icon_buttons_too(self):
        # Superseded at v0.15 (Batch D, item 7): the word labels
        # this test used to pin are exactly what the item
        # removes. Every dictation button is now the microphone
        # icon button, its name only in title and aria-label.
        for button_id in ("intakeDictate", "fdDictate"):
            tag = button_tag(button_id)
            self.assertEqual(visible_text(tag), "")
            self.assertIn("dictate-btn", tag)
            self.assertIn('title="Dictate"', tag)
            self.assertIn('aria-label="Dictate"', tag)
            self.assertIn("<svg", tag)
        # v0.15 Batch E1: the why mic is built inside the
        # thread's why entry with the composer's icon class;
        # the overall box and its buttons are gone entirely.
        self.assertIn('mic.id = "whyDictate";', TEMPLATE)
        self.assertIn('mic.className = "composer-btn";', TEMPLATE)
        self.assertNotIn('id="overallDictate"', TEMPLATE)
        self.assertNotIn('id="saveOverall"', TEMPLATE)


if __name__ == "__main__":
    unittest.main()
