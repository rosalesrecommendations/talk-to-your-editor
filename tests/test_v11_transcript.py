"""Tests for v0.11.0 item 3: transcript lines never overlap
(stdlib unittest, no dependencies).

From Justin's 2026-10-28 screen recording of 0.10.1 (FIX-ITEMS
item 3): lines containing struck (cut) words overlapped the line
below them, and word markers sat on the text, worst around the
repeated "I got one for you / Today is day thirty-one" takes
where edit decisions are densest.

Root cause in the old CSS: the transcript ran at line-height
1.55 (a 26px line at the 17px word size), the words were plain
inline spans whose padding, washes, and inset underlines paint
outside the line box without pushing the next line down, and the
dot and marker hit areas (the ::after boxes at inset -7.5px and
-6.5px) stood about 28px tall against that 26px line.

The fix is one additive block in the template, marked
"Transcript line spacing (v0.11.0, item 3)". These tests read
that block and the rules it overrides and check the numbers
that guarantee no overlap:

- the line height is taller than every box that can sit in a
  line: a word box, a marker, and a marker hit area, at the
  normal word size and at the bigger-text size;
- words are inline-block, so their padding and washes count
  toward the line height instead of painting over the next
  line;
- nothing in the block takes a word or marker out of the flow,
  shifts it with a transform, or pulls lines together with a
  negative margin;
- cut words are never shrunk or shifted by their cut styling;
- comment markers stay in the paragraph margin, clear of the
  words.

The page tests read the served template text, the same way
test_v09_transcript.py does.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
)

BLOCK_MARKER = "Transcript line spacing (v0.11.0, item 3)"


def page_html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def fix_block(html: str) -> str:
    """The v0.11.0 item 3 CSS block: from its comment to the rule
    that follows it in the file (the comments head, untouched)."""
    start = html.index(BLOCK_MARKER)
    start = html.rindex("/*", 0, start)
    end = html.index(".comments-head", start)
    return html[start:end]


def rules_for(html: str, selector: str) -> list:
    """Every rule body declared for one exact selector, in file
    order, so a test can read the winning (last) declaration."""
    bodies = []
    # Anchored to a line start so a longer selector that merely
    # ends with this one (say the bigger-text transcript rule)
    # is not counted as a rule for this selector.
    pattern = re.compile(
        r"(?m)^[ \t]*" + re.escape(selector) + r"\s*\{([^}]*)\}",
        re.DOTALL,
    )
    for match in pattern.finditer(html):
        bodies.append(match.group(1))
    return bodies


def declared(body: str, prop: str):
    match = re.search(
        r"(?:^|;)\s*" + re.escape(prop) + r"\s*:\s*([^;]+);", body
    )
    return match.group(1).strip() if match else None


def px(value: str) -> float:
    return float(value.replace("px", "").strip())


class LineHeightGuaranteeTests(unittest.TestCase):
    """The line box is taller than anything that renders in it."""

    def setUp(self):
        self.html = page_html()
        self.block = fix_block(self.html)

    def test_block_comes_after_the_tight_line_height_it_replaces(self):
        # The old 1.55 rule only loses because this block is later
        # in the file at the same specificity.
        old = self.html.index("line-height: 1.55")
        self.assertLess(old, self.html.index(BLOCK_MARKER))
        bodies = rules_for(self.html, "#transcriptPanel .transcript")
        self.assertEqual(declared(bodies[-1], "line-height"), "1.9")

    def test_words_container_shares_the_line_height(self):
        bodies = rules_for(self.block, "#transcriptPanel .tx-words")
        self.assertEqual(len(bodies), 1)
        self.assertEqual(declared(bodies[0], "line-height"), "1.9")

    def _sizes(self):
        """(line pitch, word box, dot box, flag box, held box)
        in px, read from the CSS itself."""
        line_height = float(
            declared(
                rules_for(self.block, "#transcriptPanel .transcript")[-1],
                "line-height",
            )
        )
        tw = rules_for(self.block, "#transcriptPanel .tw")[-1]
        tw_line_height = float(declared(tw, "line-height"))
        tw_pad = declared(tw, "padding").split()
        tw_pad_v = px(tw_pad[0])
        dot_h = px(declared(rules_for(self.html, ".tdot")[0], "height"))
        flag_h = px(declared(rules_for(self.html, ".tflag")[0], "height"))
        held_h = px(declared(rules_for(self.html, ".theld")[0], "height"))

        def hit(selector):
            bodies = rules_for(self.block, selector)
            inset = declared(bodies[-1], "inset")
            return px(inset.replace("-", ""))

        # The flag and held markers share one grouped ::after rule
        # (the held selector is the one right before its brace).
        marker_extra = hit("#transcriptPanel .theld::after")
        return line_height, tw_line_height, tw_pad_v, {
            "dot": dot_h + 2 * hit("#transcriptPanel .tdot::after"),
            "flag": flag_h + 2 * marker_extra,
            "held": held_h + 2 * marker_extra,
        }

    def test_line_pitch_beats_every_in_line_box_at_both_text_sizes(self):
        line_height, tw_lh, tw_pad_v, hits = self._sizes()
        # 17px is the normal word size; 19px is bigger-text mode.
        # The unitless line heights scale with both, and the fixed
        # marker sizes are the same in both modes.
        for font_size in (17, 19):
            pitch = font_size * line_height
            word_box = font_size * tw_lh + 2 * tw_pad_v
            self.assertGreater(pitch, word_box)
            for name, box in hits.items():
                self.assertGreater(
                    pitch, box, f"{name} hit box {box} vs pitch {pitch}"
                )

    def test_marker_hit_areas_shrank_from_the_old_overflow(self):
        # Old hit areas: a 13px dot with -7.5px inset (28px) and
        # 15px markers with -6.5px inset (28px), against a 26px
        # line. The block caps the inset at -6px.
        for selector in (
            "#transcriptPanel .tdot::after",
            "#transcriptPanel .theld::after",
        ):
            bodies = rules_for(self.block, selector)
            self.assertTrue(bodies, selector)
            self.assertEqual(declared(bodies[-1], "inset"), "-6px")


class WordBoxTests(unittest.TestCase):
    def setUp(self):
        self.html = page_html()
        self.block = fix_block(self.html)

    def test_words_are_inline_block_so_paint_counts_toward_the_line(self):
        bodies = rules_for(self.block, "#transcriptPanel .tw")
        self.assertEqual(len(bodies), 1)
        self.assertEqual(declared(bodies[0], "display"), "inline-block")
        self.assertEqual(declared(bodies[0], "vertical-align"), "top")

    def test_no_flow_removal_transform_or_negative_margin_in_block(self):
        self.assertNotIn("position:", self.block)
        self.assertNotIn("transform:", self.block)
        self.assertNotIn("float:", self.block)
        self.assertIsNone(
            re.search(r"margin[^:;]*:\s*-", self.block),
            "no negative margins pull transcript lines together",
        )
        self.assertIsNone(
            re.search(r"margin-(top|bottom)\s*:\s*-", self.block)
        )

    def test_cut_words_are_never_shrunk_or_shifted(self):
        # Every .tw-cut rule in the page: the strike and the
        # dimming only. No smaller size, no tighter line height,
        # no transform, nothing that moves a cut word off its line.
        bodies = rules_for(self.html, ".tw-cut") + rules_for(
            self.block, "#transcriptPanel .tw-cut"
        )
        self.assertTrue(bodies)
        for body in bodies:
            self.assertNotIn("font-size", body)
            self.assertNotIn("line-height", body)
            self.assertNotIn("transform", body)
            self.assertNotIn("margin", body)
        joined = " ".join(bodies)
        self.assertIn("line-through", joined)

    def test_selection_outlines_stay_on_their_own_line(self):
        for selector in (
            "#transcriptPanel .tw-selected",
            "#transcriptPanel .tw.tw-resting",
        ):
            bodies = rules_for(self.block, selector)
            self.assertEqual(len(bodies), 1, selector)
            self.assertEqual(declared(bodies[0], "outline-offset"), "0")


class MarkerPlacementTests(unittest.TestCase):
    def setUp(self):
        self.html = page_html()
        self.block = fix_block(self.html)

    def test_inline_markers_align_inside_the_line(self):
        # The grouped marker rule in the block: all three marker
        # kinds sit on the middle of the text line, in the flow,
        # at their small fixed sizes.
        match = re.search(
            r"#transcriptPanel \.tdot,\s*#transcriptPanel \.tflag,"
            r"\s*#transcriptPanel \.theld\s*\{([^}]*)\}",
            self.block,
        )
        self.assertIsNotNone(match)
        self.assertEqual(
            declared(match.group(1), "vertical-align"), "middle"
        )
        for selector, size in ((".tdot", 13), (".tflag", 15), (".theld", 15)):
            body = rules_for(self.html, selector)[0]
            self.assertEqual(px(declared(body, "height")), size)
            self.assertLess(size, 17 * 1.9)

    def test_comment_markers_stay_in_the_paragraph_margin(self):
        # The paragraph reserves right padding and the comment
        # markers live in that reserved strip, so they can never
        # sit on top of the words.
        bodies = rules_for(self.block, "#transcriptPanel .tx-para")
        self.assertEqual(len(bodies), 1)
        padding_right = px(declared(bodies[0], "padding-right"))
        marker = rules_for(self.html, ".tx-comment-marker")[0]
        marker_w = px(declared(marker, "width"))
        self.assertGreaterEqual(padding_right, marker_w + 10)
        margin = rules_for(self.html, ".tx-margin")[0]
        self.assertEqual(declared(margin, "position"), "absolute")
        self.assertEqual(declared(margin, "right"), "0")


class UnchangedRenderingTests(unittest.TestCase):
    """The fix changes spacing only: the marks themselves, their
    colors, and the markup the script builds are untouched."""

    def setUp(self):
        self.html = page_html()

    def test_marker_and_wash_styles_survive(self):
        self.assertIn(
            ".tw-held { background: rgba(255, 120, 110, 0.22)", self.html
        )
        self.assertIn(".tw-cut", self.html)
        self.assertIn("text-decoration: line-through", self.html)

    def test_render_markup_still_built_the_same_way(self):
        self.assertIn('paraWords.className = "tx-words"', self.html)
        self.assertIn('span.className = "tw tw-" + w.status', self.html)
        self.assertIn('dot.className = "tdot"', self.html)
        self.assertIn('marker.className = "tflag"', self.html)
        self.assertIn('marker.className = "theld"', self.html)


if __name__ == "__main__":
    unittest.main()
