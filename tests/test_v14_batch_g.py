"""v0.14 Batch G: the fix-up round from the end-of-round
measurement (fit battery + symmetry census on the frozen
0.14.0 tree). Pinned here at source level, in the style of
the other batch tests; the measured on-screen proofs live
in the fit battery (fit-test-014.mjs) run after this batch:

- G1: one blue survives. Bare anchors took the browser's
  default link blue (the pale 158-per-channel default);
  a single `a { color: var(--blue) }` rule covers them.
- G2: the pin button measures exactly 48: its 16px label
  wrapped to two lines in the rail (54 tall); it stays on
  one line now, side padding on the scale at 12px.
- G3: the band's Comment button measures the band row's
  46: its content follows the transport pattern (icon
  over the micro label) like every other band button.
- G4/G5: the comments head is one 44px row and the Done
  toggle measures its 36px tier: neither the toggle's
  label nor the head's button span wraps any more.
- G6: the why line stays closed after the first answer:
  each action that can raise it takes a sequence stamp
  when it begins (beginWhyPrompt), and only the action
  still holding the stamp may open the line.
- G7: every form control inherits the system stack, not
  just buttons.
- G8: the remaining off-scale spacing moves: plain pill
  side padding 20 to 22, hero action 30 to 22, the scope
  select's text inset 40 to 22 with its arrow re-seated
  at 4px (6px clear of the text), and the zoom slider's
  browser-default 2px margin zeroed.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "src" / "talk_to_your_editor" / "templates"
            / "test_app.html").read_text(encoding="utf-8")
STYLE = TEMPLATE[TEMPLATE.index("<style>"):TEMPLATE.index("</style>")]


def rule(selector):
    """The winning (last) rule body declared for one exact
    selector, the way the cascade reads it (same helper as
    test_v14_batch_c)."""
    plain = re.sub(r"/\*.*?\*/", "", STYLE, flags=re.DOTALL)
    bodies = re.findall(
        r"(?m)^[ \t]*" + re.escape(selector) + r"\s*\{([^}]*)\}",
        plain, flags=re.DOTALL)
    assert bodies, selector
    return bodies[-1]


def rule_first(selector):
    """The FIRST rule body for a selector: the base `button`
    rule is the one that sizes plain buttons, and later
    media-query overrides for the same selector must not
    shadow it here."""
    plain = re.sub(r"/\*.*?\*/", "", STYLE, flags=re.DOTALL)
    bodies = re.findall(
        r"(?m)^[ \t]*" + re.escape(selector) + r"\s*\{([^}]*)\}",
        plain, flags=re.DOTALL)
    assert bodies, selector
    return bodies[0]


def between(start, end):
    i = TEMPLATE.index(start)
    return TEMPLATE[i:TEMPLATE.index(end, i)]


class OneBlueTests(unittest.TestCase):
    """G1: the caption-download link's default blue is gone."""

    def test_no_default_link_blue_anywhere(self):
        self.assertNotIn("9e9eff", TEMPLATE)
        self.assertNotIn("158,158,255", TEMPLATE)
        self.assertNotIn("158, 158, 255", TEMPLATE)

    def test_every_link_takes_the_token_blue(self):
        self.assertIn("color: var(--blue)", rule("a"))
        # The caption anchors the census caught are still
        # plain anchors under that rule (no inline colour).
        self.assertIn(
            '<a href="/api/captions">download the caption '
            'file (WebVTT)</a>', TEMPLATE)


class PinHeightTests(unittest.TestCase):
    """G2: the pin control is exactly the 48px action tier."""

    def test_pin_rule_keeps_one_line_on_the_scale(self):
        body = rule(".comment-action")
        self.assertIn("min-height: 48px", body)
        self.assertIn("padding: 8px 12px", body)
        self.assertIn("font-size: 16px", body)
        self.assertIn("white-space: nowrap", body)

    def test_pin_keeps_its_words(self):
        # v0.15 Batch E1: the pin control is the + attach
        # button; its words are its name, "Pin this moment".
        pin = between('id="editorAttachBtn"', 'id="draft"')
        self.assertIn("Pin this moment", pin)


class BandCommentTests(unittest.TestCase):
    """G3: the band's Comment button joins the 46px row."""

    def test_comment_button_follows_the_transport_pattern(self):
        tag = between('id="tlCommentBtn"', '</button>')
        self.assertIn('<span class="tp-icon">', tag)
        self.assertIn('<span class="tp-label">Comment</span>', tag)

    def test_its_icon_is_block_so_no_baseline_descent(self):
        self.assertIn("display: block", rule("#tlCommentBtn svg"))


class CommentsHeadTests(unittest.TestCase):
    """G4/G5: one 44px head row; the toggle at its 36 tier."""

    def test_done_toggle_never_wraps(self):
        body = rule(".done-toggle")
        self.assertIn("min-height: 36px", body)
        self.assertIn("font-size: 13px", body)
        self.assertIn("white-space: nowrap", body)
        self.assertIn("padding: 8px", body)

    def test_head_gaps_and_button_span_cannot_stack(self):
        self.assertIn("gap: 4px", rule(".comments-head"))
        self.assertIn("flex-wrap: nowrap",
                      rule(".comments-head .panel-head-btns"))
        self.assertIn("margin-right: 4px",
                      rule(".comments-head h2 svg"))

    def test_done_toggle_still_rides_in_the_head(self):
        # v0.15 Batch E1: the waiting count rides in the head
        # where the Done toggle used to.
        head = between('id="commentsHead"', 'id="threadList"')
        self.assertIn('id="commentsHeadCount"', head)
        self.assertNotIn('id="doneToggle"', head)


class WhySequenceTests(unittest.TestCase):
    """G6: the first answer leaves the why line hidden."""

    def why_block(self):
        begin = TEMPLATE.index("// [tye-why-begin]")
        end = TEMPLATE.index("// [tye-why-end]")
        return TEMPLATE[begin:end]

    def test_the_stamp_lives_in_the_why_block(self):
        block = self.why_block()
        self.assertIn("var whyPromptSeq = 0;", block)
        self.assertIn("function beginWhyPrompt()", block)
        self.assertIn("seq !== whyPromptSeq", block)
        # Hiding always advances the sequence, so an answer
        # or Skip retires every stamp taken before it.
        hide = block[block.index("function hideWhyPrompt()"):]
        hide = hide[:hide.index("\n  }")]
        self.assertIn("whyPromptSeq += 1;", hide)

    def test_every_raising_action_takes_its_stamp_first(self):
        self.assertEqual(TEMPLATE.count("beginWhyPrompt()"), 5 + 1)
        # (the definition plus the five raisers: the picked
        # words toggle, the bar's Keep, the gold verdict,
        # the edit card's Bring back, and history undo)
        for call in (
            'showWhyPrompt(data.why_prompt.entry_id, "restore", whySeq)',
            'showWhyPrompt(data.why_prompt.entry_id, "leave", whySeq)',
            'showWhyPrompt(whyEntry, "leave", whySeq)',
        ):
            self.assertIn(call, TEMPLATE)


class FormControlFontTests(unittest.TestCase):
    """G7: inputs, selects and textareas inherit the stack."""

    def test_the_inherit_rule_covers_every_form_control(self):
        self.assertIn("font-family: inherit",
                      rule("input, select, textarea"))
        # Buttons were covered by Batch A and stay covered.
        self.assertIn("font-family: inherit", rule_first("button"))


class SpacingRemainderTests(unittest.TestCase):
    """G8: 20, 30, 40 and 2 each land on the scale (or 0)."""

    def test_plain_pill_side_padding_is_22(self):
        self.assertIn("padding: 12px 22px", rule_first("button"))
        self.assertNotIn("padding: 12px 20px", STYLE)

    def test_hero_action_side_padding_is_22(self):
        self.assertIn("padding: 12px 22px", rule(".hero-action"))
        self.assertNotIn("padding: 12px 30px", STYLE)

    def test_scope_select_inset_and_arrow(self):
        body = rule("select")
        self.assertIn("padding: 12px 22px 12px 16px", body)
        self.assertIn("background-position: right 4px center", body)
        self.assertNotIn("12px 40px", STYLE)

    def test_zoom_slider_margin_is_zeroed(self):
        self.assertIn("margin: 0", rule("#tlZoomSlider"))
        # The pinned width rule itself is untouched.
        self.assertIn(
            '.tl-zoom input[type="range"] { width: 120px; }',
            TEMPLATE)


if __name__ == "__main__":
    unittest.main()
