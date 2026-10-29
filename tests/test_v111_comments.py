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
- Save (#save) is a round icon button with an up arrow in inline
  SVG, still blue, with title and aria-label "Save comment".
- Comment at current time (#pin) is a small, quiet text button
  at the header button size, with a small clock icon, no blue
  fill. It is still one tap to pin at the playhead.
- The seconds entry, the format picker, and the keep playing
  switch are still in the card, and the dictation status line
  still sits under the composer row.

Same ids, same handlers, same dictation route. One supporting
change inside the shared dictation code: the button's starting
content is now kept as markup and restored as markup, so the
microphone icon comes back after a recording. For the plain word
Dictate buttons the markup is only their word, so their behavior
is unchanged.

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

# The old, bigger page wide button size the three buttons used
# before this fix.
PAGE_WIDE = {
    "min-height": "48px",
    "padding": "12px 20px",
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
    start = TEMPLATE.index('id="pinCard"')
    end = TEMPLATE.index('id="overallCard"', start)
    return TEMPLATE[start:end]


class NoBigPillsTests(unittest.TestCase):
    def test_pin_is_not_a_primary_pill(self):
        tag = button_tag("pin")
        self.assertNotIn("primary", tag)

    def test_no_button_shows_the_old_pill_words(self):
        # The blue pills carried their words as big button text.
        # Neither the pin button nor the save button does now:
        # the pin words are small quiet text (checked below) and
        # the save words live only in its title and aria-label.
        self.assertEqual(visible_text(button_tag("save")), "")
        self.assertEqual(visible_text(button_tag("draftDictate")), "")

    def test_page_wide_button_rule_is_untouched(self):
        # No other button in the app changes size: the rule every
        # other plain button sizes from keeps its old values.
        page_wide = rule_values(r"(?m)^  button")
        self.assertEqual(sizing(page_wide), PAGE_WIDE)


class PinControlTests(unittest.TestCase):
    def test_pin_is_a_compact_text_button_at_the_standard_size(self):
        tag = button_tag("pin")
        self.assertIn("comment-action", tag)
        self.assertEqual(visible_text(tag), "Comment at current time")
        comment_action = rule_values(r"\.comment-action")
        self.assertEqual(sizing(comment_action), STANDARD)
        topbar = rule_values(r"\.topbar-btn")
        self.assertEqual(sizing(comment_action), sizing(topbar))

    def test_pin_carries_a_clock_icon_and_plain_words(self):
        tag = button_tag("pin")
        self.assertIn("<svg", tag)
        self.assertIn('<circle cx="12" cy="12" r="8.5">', tag)
        self.assertIn('title="Comment at current time"', tag)
        self.assertIn('aria-label="Comment at current time"', tag)


class ComposerRowTests(unittest.TestCase):
    def test_input_mic_and_send_share_one_row_in_that_order(self):
        card = pin_card()
        row_start = card.index('class="composer-row"')
        row_end = card.index('id="draftDictateStatus"', row_start)
        row = card[row_start:row_end]
        positions = [
            row.index('id="%s"' % name)
            for name in ("draft", "draftDictate", "save")
        ]
        self.assertEqual(positions, sorted(positions))

    def test_composer_row_is_a_flex_row_and_the_input_grows(self):
        row_rule = rule_values(r"\.composer-row")
        self.assertEqual(row_rule["display"], "flex")
        input_rule = rule_values(r"\.composer-row #draft")
        self.assertIn("flex: 1 1 auto", TEMPLATE)
        self.assertEqual(input_rule["min-width"], "0")

    def test_text_box_placeholder_names_the_microphone(self):
        card = pin_card()
        self.assertIn(
            'placeholder="What did you notice here? Type it, or '
            'tap the microphone to dictate it."',
            card,
        )
        self.assertNotIn("button below", card)


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

    def test_send_button_is_round_blue_with_an_up_arrow(self):
        tag = button_tag("save")
        self.assertIn("composer-btn", tag)
        self.assertIn("primary", tag)
        self.assertIn('title="Save comment"', tag)
        self.assertIn('aria-label="Save comment"', tag)
        self.assertIn("<svg", tag)
        # An up arrow: the stem and the arrow head.
        self.assertIn('d="M12 19V5"', tag)
        self.assertIn('d="M5.5 11.5L12 5l6.5 6.5"', tag)
        # Round: the shared icon button is as wide as it is tall,
        # and the page wide button rule rounds it fully (999px).
        icon_rule = rule_values(r"\.composer-btn")
        self.assertEqual(icon_rule["width"], "40px")
        self.assertEqual(icon_rule["height"], "40px")
        self.assertEqual(icon_rule["min-height"], "40px")
        self.assertEqual(icon_rule["padding"], "0")
        page_wide = rule_values(r"(?m)^  button")
        self.assertEqual(page_wide["border-radius"], "999px")

    def test_icons_are_inline_svg_with_no_emoji_or_external_assets(self):
        for button_id in ("pin", "draftDictate", "save"):
            tag = button_tag(button_id)
            self.assertIn("<svg", tag)
            self.assertIn('aria-hidden="true"', tag)
            self.assertNotIn("http", tag)
            self.assertNotIn("<img", tag)

    def test_recording_words_still_fit_the_mic_button(self):
        # The dictation code puts words on the button while it
        # records; the icon button grows into a pill for them.
        grown = rule_values(r"\.composer-btn\.recording, "
                            r"\.composer-btn\.processing")
        self.assertEqual(grown["width"], "auto")
        self.assertEqual(grown["padding"], "8px 14px")

    def test_only_the_composer_buttons_carry_the_icon_class(self):
        carriers = re.findall(
            r'<button[^>]*class="[^"]*composer-btn[^"]*"', TEMPLATE
        )
        self.assertEqual(len(carriers), 2)
        for button_id in (
            "intakeDictate", "fdDictate", "whyDictate",
            "overallDictate", "saveOverall",
        ):
            self.assertNotIn("composer-btn", button_tag(button_id))
            self.assertNotIn("comment-action", button_tag(button_id))


class StillWiredTests(unittest.TestCase):
    def test_handlers_are_untouched(self):
        self.assertIn(
            'document.getElementById("pin").addEventListener("click"',
            TEMPLATE,
        )
        self.assertIn(
            'document.getElementById("save").addEventListener("click"',
            TEMPLATE,
        )
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
            card.index('class="composer-row"'),
            card.index('id="draftDictateStatus"'),
        )
        self.assertLess(
            card.index('id="draftDictateStatus"'),
            card.index('id="pinNote"'),
        )

    def test_seconds_picker_and_keep_playing_stay_in_the_card(self):
        card = pin_card()
        self.assertIn('id="manualTs"', card)
        self.assertIn("or at", card)
        self.assertIn("seconds", card)
        self.assertIn('id="scope"', card)
        for value in ('value="both"', 'value="vertical"',
                      'value="landscape"'):
            self.assertIn(value, card)
        self.assertIn('id="keepPlaying"', card)
        self.assertIn("Keep playing while I type", card)

    def test_other_dictate_buttons_keep_their_word_labels(self):
        for button_id in ("intakeDictate", "fdDictate", "whyDictate",
                          "overallDictate"):
            self.assertEqual(
                visible_text(button_tag(button_id)), "Dictate")
        self.assertEqual(
            visible_text(button_tag("saveOverall")),
            "Save overall note",
        )


if __name__ == "__main__":
    unittest.main()
