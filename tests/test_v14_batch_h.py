"""v0.14 Batch H: the final micro-fix round from the
closing symmetry census (ledger Row 11). Pinned here at
source level, in the style of the other batch tests; the
measured on-screen proofs live in the re-run structural
census (sym014-h) and the fit battery run after this batch:

- H1: the 14px card margins are on the scale at 16. The
  settings panel's two cards (#directionCard, #moneyCard)
  took 14 from `.settings-panel > .card`; #rulesEmpty is
  a <p> with no margin of its own, so it took the
  browser's 1em paragraph margin (14px at its 14px font
  size) and now carries an explicit 16. The other literal
  14px margins on structural containers found by grep
  (the Remove group, the drop area, the drop-video wrap,
  the timeline wrap's bottom margin) move to 16 as well.
  14px font sizes, text margins (.fe-stage, .transcript,
  .tx-para), button paddings and the mobile frame padding
  are not structural container spacing and do not move.
- H2: the flex gap 2 is a named internal inset, left as
  is: it sits inside each single .tp-btn transport
  button, between its .tp-icon and its .tp-label. The
  gaps between separate transport controls are the row's
  8 and the centre cluster's 4, both already on scale.
- H3: the 1.1875 computed margin-left on the comments
  head's .panel-head-btns is a named exception, left as
  is: the chosen value is `auto`, the right-alignment
  mechanism, and 1.1875 is simply the free space the
  flex row left it to absorb (the heading's fractional
  text width makes the remainder fractional). No literal
  or em/rem value in the CSS produces the fraction.
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
    test_v14_batch_g)."""
    plain = re.sub(r"/\*.*?\*/", "", STYLE, flags=re.DOTALL)
    bodies = re.findall(
        r"(?m)^[ \t]*" + re.escape(selector) + r"\s*\{([^}]*)\}",
        plain, flags=re.DOTALL)
    assert bodies, selector
    return bodies[-1]


def rule_first(selector):
    """The FIRST rule body for a selector (same helper as
    test_v14_batch_g)."""
    plain = re.sub(r"/\*.*?\*/", "", STYLE, flags=re.DOTALL)
    bodies = re.findall(
        r"(?m)^[ \t]*" + re.escape(selector) + r"\s*\{([^}]*)\}",
        plain, flags=re.DOTALL)
    assert bodies, selector
    return bodies[0]


def between(start, end):
    i = TEMPLATE.index(start)
    return TEMPLATE[i:TEMPLATE.index(end, i)]


class CardMarginTests(unittest.TestCase):
    """H1: the three census cards measure 16, not 14."""

    def test_settings_panel_cards_take_16(self):
        body = rule(".settings-panel > .card")
        self.assertIn("margin: 16px 0 0", body)
        self.assertNotIn("14px", body)
        # The two cards the census named are the panel's
        # direct card children in the markup.
        panel = between('id="settingsPanel"', 'id="jobIncompleteBanner"')
        self.assertIn('id="directionCard"', panel)
        self.assertIn('id="moneyCard"', panel)

    def test_rules_empty_carries_an_explicit_16(self):
        self.assertIn("margin: 16px 0", rule("#rulesEmpty"))
        # Scoped to the id: the shared .empty rule keeps no
        # margin, so the script-made <div> empty notes
        # (margin 0) do not move.
        self.assertNotIn("margin", rule(".empty"))


class ContainerMarginSweepTests(unittest.TestCase):
    """H1: every other literal 14px margin on a structural
    container found by grep is on the scale too."""

    def test_remove_group_margin_top_is_16(self):
        self.assertIn("margin-top: 16px", rule(".remove-group"))

    def test_drop_area_margin_top_is_16(self):
        self.assertIn("margin-top: 16px", rule(".drop-area"))

    def test_drop_video_wrap_margin_top_is_16(self):
        self.assertIn("margin-top: 16px", rule("#dropVideoWrap"))

    def test_timeline_wrap_bottom_margin_is_16(self):
        self.assertIn("margin: 4px 0 16px", rule_first(".tl-wrap"))

    def test_no_container_rule_keeps_a_14_margin(self):
        for selector in (".settings-panel > .card", "#rulesEmpty",
                         ".remove-group", ".drop-area",
                         "#dropVideoWrap"):
            self.assertNotIn("14px", rule(selector), selector)
        self.assertNotIn("14px", rule_first(".tl-wrap"))


class TransportGapTests(unittest.TestCase):
    """H2: the gap 2 is an internal inset inside each
    single transport button, named and left in place."""

    def test_tp_btn_gap_is_the_icon_to_label_inset(self):
        body = rule(".tp-btn")
        # v0.15 Batch I, item 39: the label sits beside the
        # icon, not under it; the inset is the row gap, 8px.
        self.assertIn("flex-direction: row", body)
        self.assertIn("gap: 8px", body)

    def test_every_transport_button_is_icon_over_label(self):
        for btn in ("prevChangeBtn", "goStartBtn", "back5",
                    "playPauseBtn", "fwd5", "goEndBtn",
                    "nextChangeBtn", "tlCommentBtn"):
            tag = between('id="%s"' % btn, '</button>')
            self.assertIn('<span class="tp-icon"', tag, btn)
            self.assertIn('<span class="tp-label"', tag, btn)

    def test_gaps_between_controls_are_on_the_scale(self):
        self.assertIn("gap: 8px", rule(".transport"))
        self.assertIn("gap: 4px", rule(".transport-center"))


class CommentsHeadMarginTests(unittest.TestCase):
    """H3: the fractional computed margin is the auto
    alignment mechanism's artifact, named and left."""

    def test_the_chosen_value_is_auto_not_a_fraction(self):
        body = rule(".comments-head .panel-head-btns")
        self.assertIn("margin-left: auto", body)
        # No declaration anywhere carries the fraction (the
        # CSS comment naming it is prose, not a value).
        plain = re.sub(r"/\*.*?\*/", "", STYLE, flags=re.DOTALL)
        self.assertNotIn("1.1875", plain)


if __name__ == "__main__":
    unittest.main()
