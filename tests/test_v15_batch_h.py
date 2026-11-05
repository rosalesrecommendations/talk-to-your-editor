"""v0.15 Batch H: the word-state hierarchy (item 25).

Justin's words: the gray and the white are "hard to distinguish
who is who"; "something has to happen with that gray hierarchy
because that is way too confusing"; the font hierarchy is
revisited too (07:36).

The scheme (research note first, at
v015-work/evidence/word-state-research.md):

- One brightness ladder: kept / held / corrected at full
  brightness (opacity 1), unsure cuts at 0.75, cuts at 0.62 —
  up from 0.38, whose text measured contrast 3.3:1 against
  the panel, below the 4.5:1 reading line. At 0.62 a cut
  word measures 6.6:1 and stays readable, on a measured step
  below unsure and below kept.
- One form per state, so no pair is told by color alone:
  kept has no mark at all; cut has the strike; unsure has
  the strike plus an amber wash and a solid underline; held
  keeps its light red wash at full brightness and gains a
  DOTTED underline (the only dotted text underline); corrected
  keeps its blue wash and solid underline; a pick is an
  outline box.
- The timeline band takes the same scheme (P8): a band cut
  word is no longer red text; it is the kept color, struck,
  at the same 0.62 step, and a merged cut group carries a
  line through its middle at the same step.
- Transcript-area small print that sat in no tier (12px
  time chips and speaker names, the pick menu's 12px
  shortcut print, the why/gold pop-up's 14px text) now sits
  on the 0.14 tiers: hints 13, body 15. Words stay 17px.

Everything is offline; no provider is called.
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


def rule(selector: str) -> str:
    """The body of the rule whose selector stands alone at a
    line start (so `#transcriptPanel .tw-cut` is not read as
    `.tw-cut`, and `body.bigger-text .tx-time` is not read as
    `.tx-time`)."""
    bodies = re.findall(
        r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}",
        TEMPLATE)
    if not bodies:
        raise AssertionError(f"no rule for {selector}")
    return bodies[0]


def _comp(fg, bg):
    r, g, b, a = fg
    return tuple(a * f + (1 - a) * c for f, c in zip((r, g, b), bg))


def _lum(rgb):
    def f(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (f(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


PANEL = _comp((28, 28, 30, 0.94), (0, 0, 0))


def effective_luminance(wash, opacity):
    bg = _comp(wash, PANEL) if wash else PANEL
    text = _comp((255, 255, 255, 0.94), bg)
    eff = tuple(opacity * x + (1 - opacity) * p
                for x, p in zip(text, PANEL))
    return _lum(eff)


def opacity_of(selector: str) -> float:
    m = re.search(r"opacity:\s*([0-9.]+)", rule(selector))
    return float(m.group(1)) if m else 1.0


# ---------------------------------------------------------------------------
# The brightness ladder
# ---------------------------------------------------------------------------


class LadderTests(unittest.TestCase):
    def test_cut_rung_is_raised_to_the_stated_step(self):
        self.assertEqual(opacity_of(".tw-cut"), 0.62)
        self.assertNotIn("opacity: 0.38", rule(".tw-cut"))

    def test_unsure_rung_is_unchanged_between_kept_and_cut(self):
        self.assertEqual(opacity_of(".tw-unsure"), 0.75)

    def test_measured_luminance_steps(self):
        kept = effective_luminance(None, 1.0)
        unsure = effective_luminance(
            (255, 159, 10, 0.30), opacity_of(".tw-unsure"))
        cut = effective_luminance(None, opacity_of(".tw-cut"))
        # The research note's measured AFTER numbers.
        self.assertAlmostEqual(kept, 0.882, places=2)
        self.assertAlmostEqual(unsure, 0.512, places=2)
        self.assertAlmostEqual(cut, 0.350, places=2)
        # Each rung is a clear step from its neighbours.
        self.assertGreater(kept - unsure, 0.30)
        self.assertGreater(unsure - cut, 0.12)
        # A cut word is reading text: contrast vs the panel
        # clears the 4.5:1 line (it was 3.3:1 at 0.38).
        panel_lum = _lum(PANEL)
        contrast = (cut + 0.05) / (panel_lum + 0.05)
        self.assertGreater(contrast, 4.5)


# ---------------------------------------------------------------------------
# One form per state
# ---------------------------------------------------------------------------


class FormTests(unittest.TestCase):
    def test_kept_has_no_mark_of_its_own(self):
        # No .tw-kept rule exists: kept is the plain base word.
        self.assertIsNone(re.search(r"\.tw-kept\s*\{", TEMPLATE))
        base = rule(".tw")
        self.assertNotIn("text-decoration", base)
        self.assertNotIn("opacity:", base)
        self.assertNotIn("background:", base)

    def test_cut_is_the_strike_at_the_low_rung(self):
        body = rule(".tw-cut")
        self.assertIn("text-decoration: line-through", body)
        self.assertNotIn("background", body)

    def test_held_is_wash_plus_the_only_dotted_text_underline(self):
        body = rule(".tw-held")
        self.assertIn("background: rgba(255, 120, 110, 0.22)", body)
        self.assertIn("text-decoration: underline dotted", body)
        # The old solid inset line it shared with corrected
        # and unsure is gone, and held stays full brightness.
        self.assertNotIn("box-shadow", body)
        self.assertNotIn("opacity", body)

    def test_unsure_keeps_amber_wash_and_solid_underline(self):
        body = rule(".tw-unsure")
        self.assertIn("background: rgba(255, 159, 10, 0.30)", body)
        self.assertIn(
            "box-shadow: inset 0 -2px 0 rgba(255, 159, 10, 0.9)", body)
        self.assertNotIn("dotted", body)

    def test_corrected_keeps_blue_wash_and_solid_underline(self):
        body = rule(".tw-corrected")
        self.assertIn("background: rgba(0, 100, 209, 0.16)", body)
        self.assertIn("box-shadow: inset 0 -2px 0 var(--blue)", body)
        self.assertNotIn("dotted", body)

    def test_commented_dots_are_the_box_border_not_the_text_line(self):
        # Held's dots ride on the text (text-decoration);
        # commented's dots are the box's bottom border, with
        # no wash — the pair stays apart by form.
        body = rule(".tw-commented")
        self.assertIn("border-bottom: 2px dotted var(--blue)", body)
        self.assertNotIn("background", body)

    def test_picked_is_an_outline_box(self):
        body = rule(".tw-selected")
        self.assertIn("outline: 2px solid var(--blue)", body)

    def test_held_marker_still_rides_at_the_span_end(self):
        self.assertIn('marker.className = "theld";', TEMPLATE)
        self.assertIn('(inGold(w) ? " tw-held" : "")', TEMPLATE)


# ---------------------------------------------------------------------------
# The band takes the same scheme (P8)
# ---------------------------------------------------------------------------


class BandSchemeTests(unittest.TestCase):
    def test_band_cut_word_matches_the_transcript_cut(self):
        body = rule(".tl-word.cut")
        self.assertIn("color: #eef4ff", body)
        self.assertIn("text-decoration: line-through", body)
        self.assertIn("opacity: 0.62", body)
        # The old red text color is gone from the whole page.
        self.assertNotIn("#ff9d94", TEMPLATE)

    def test_band_cut_group_carries_a_line_through_its_middle(self):
        body = rule(".tl-word-group.cut")
        self.assertIn("opacity: 0.62", body)
        self.assertIn("rgba(255, 255, 255, 0.05)", body)
        # The red stripes are gone.
        self.assertNotIn("255, 69, 58", body)
        # Batch J supersedes the drawing technique (not the
        # look): the strike's form for a textless block is the
        # same solid light line across the middle, but drawn
        # by a pseudo-element, so the template carries no
        # linear-gradient outside the deleted primary. Same
        # geometry: full width, 2px, centered, same color.
        self.assertNotIn("linear-gradient(to bottom", body)
        after = rule(".tl-word-group.cut::after")
        self.assertIn("top: calc(50% - 1px)", after)
        self.assertIn("height: 2px", after)
        self.assertIn("background: rgba(238, 244, 255, 0.85)", after)

    def test_band_kept_word_is_full_brightness_and_unstruck(self):
        body = rule(".tl-word")
        self.assertIn("color: #eef4ff", body)
        self.assertNotIn("text-decoration", body)
        self.assertNotIn("opacity", body)


# ---------------------------------------------------------------------------
# Font hierarchy in the transcript area
# ---------------------------------------------------------------------------


class TranscriptFontTierTests(unittest.TestCase):
    def test_words_take_the_raised_base_size(self):
        # v0.20, chunk D supersedes the 17px base pinned
        # here: from Justin's Nov 1 recording the whole
        # editing page read small, so the transcript words
        # rise to 19px (the step Bigger text used to give;
        # Bigger text itself now steps to 21px).
        self.assertIn(
            "#transcriptPanel .transcript { font-size: 19px;",
            TEMPLATE)

    def test_paragraph_time_chips_take_the_hint_tier(self):
        self.assertIn("font-size: 13px", rule(".tx-time"))
        bigger = re.search(
            r"body\.bigger-text \.tx-time \{([^}]*)\}", TEMPLATE)
        self.assertIn("font-size: 15px", bigger.group(1))

    def test_speaker_names_take_the_hint_tier(self):
        self.assertIn("font-size: 13px", rule(".tx-speaker"))

    def test_pick_menu_shortcut_print_takes_the_hint_tier(self):
        self.assertIn("font-size: 13px", rule(".sel-mi-key"))

    def test_why_gold_popup_text_takes_the_body_tier(self):
        self.assertIn("font-size: 15px", rule("#editPopup"))
        self.assertIn(
            "font-size: 15px", rule("#editPopup .comment-text"))
        self.assertIn("font-size: 15px", rule("#editPopup textarea"))
        self.assertIn("font-size: 15px", rule("#editPopupTitle"))
        # Its hint lines stay on the hint tier.
        self.assertIn(
            "font-size: 13px",
            rule("#editPopup .action, #editPopup .note"))

    def test_counts_lines_take_the_body_tier_in_the_fitted_rules(self):
        for body in re.findall(
                r"body\.job-loaded[^{]*\.tx-counts \{([^}]*)\}",
                TEMPLATE):
            self.assertIn("font-size: 15px", body)
            self.assertNotIn("font-size: 14px", body)


if __name__ == "__main__":
    unittest.main()
