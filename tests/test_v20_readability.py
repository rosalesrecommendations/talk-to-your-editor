"""Tests for v0.20 Chunk D: editing-page readability.

From Justin's 2026-11-01 Mac run of 0.19 (friction log,
findings 2, 6, 7, 9, 10, and the 46-second recording of the
editing page):

- Type was small throughout the editing page. The base
  sizes rise: the transcript words from 17px to 19px (the
  step Bigger text used to give; Bigger text now steps to
  21px, so the setting still works on top of the new base),
  the panel names from 13px to 15px, the notes, counts, and
  card headings on a loaded job, and the direction card's
  tool rows from chunk B.
- The Review suggestions cards were small and dense; their
  text ran long and in places past the card's edge in the
  narrow rail. The text is 16px now and wraps inside the
  card (break-word), the card can shrink (min-width: 0),
  and the Your style suggestion cards take the same step.
- The video preview sat small inside a wide black box for
  a vertical video. The wrap now centers the picture and
  fitVideoToShape sizes the video element itself to the
  largest box of the loaded video's own shape that fits
  the wrap (respecting the stylesheet's height caps), so
  a tall video stands tall and large. The stylesheet's
  object-fit: contain rules stay as the fallback, so the
  picture is never stretched.
- The bottom Edit settings strip sat clipped at the
  window's bottom edge. In the fitted layouts the bar may
  now shrink (flex: 0 1 auto, min-height: 0) instead of
  pushing itself and the timeline band past the edge, its
  rows still scroll inside it under the v0.17 cap, and its
  summary line sticks to the top of the bar while they
  scroll.
- Train the editor read as an optional side extra with an
  oversized graduation-cap graphic and a vague "costs a
  few cents" line, though teaching is the product's core.
  On the start screen it is now its own titled section
  under the drop area, with plain words about what
  teaching does and the honest per-run cost (the same
  wording the teach card's step 3 uses). The cap on its
  button takes the same 20px size as the teach card's
  head (it had no size rule and rendered at the browser's
  default svg size). The teach card's own intro no longer
  calls teaching optional.
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.webapp import WebAppConfig, create_server  # noqa: E402

PAGE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")


def rule(selector: str) -> str:
    """The bodies of every rule whose selector stands alone
    at a line start, joined: a selector can carry more than
    one rule down the sheet."""
    bodies = re.findall(
        r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}",
        PAGE)
    if not bodies:
        raise AssertionError(f"no rule for {selector}")
    return "\n".join(bodies)


def between(start: str, end: str, text: str = PAGE) -> str:
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


class BaseSizeTests(unittest.TestCase):
    def test_transcript_words_take_the_raised_base(self):
        self.assertIn("font-size: 19px",
                      rule("#transcriptPanel .transcript"))

    def test_bigger_text_still_steps_up_from_the_new_base(self):
        self.assertIn(
            "font-size: 21px",
            rule("body.bigger-text #transcriptPanel .transcript"))
        # The setting itself is untouched.
        self.assertIn('id="biggerTextToggle"', PAGE)
        self.assertIn("body.bigger-text", PAGE)

    def test_panel_names_are_readable(self):
        self.assertIn("font-size: 15px", rule(".panel-head-name"))

    def test_notes_counts_and_headings_rise_on_a_loaded_job(self):
        self.assertIn("font-size: 16px", rule("body.job-loaded .note"))
        self.assertIn("font-size: 16px", rule("body.job-loaded .counts"))
        self.assertIn("font-size: 19px",
                      rule("body.job-loaded .card > h2"))
        self.assertIn("font-size: 15px",
                      rule("body.job-loaded .status-line"))

    def test_the_direction_card_type_rises(self):
        self.assertIn("font-size: 16px", rule("#firstEditBlock .note"))
        self.assertIn("font-size: 18px",
                      rule("#firstEditBlock textarea"))
        # The tool rows are shared between the direction
        # card and the Edit settings bar, so their raises
        # ride one combined rule for both homes.
        self.assertIn(
            "#firstEditBlock .tool-name, #editSettingsBar "
            ".tool-name { font-size: 17px; }", PAGE)
        self.assertIn(
            "#firstEditBlock .tool-summary, #editSettingsBar "
            ".tool-summary { font-size: 15px;", PAGE)
        self.assertIn(
            "#firstEditBlock .tool-head .switch, "
            "#editSettingsBar .tool-head .switch "
            "{ font-size: 16px; }", PAGE)
        self.assertIn(
            "#firstEditBlock .fd-note, #editSettingsBar "
            ".fd-note { font-size: 15px;", PAGE)


class SuggestionCardTests(unittest.TestCase):
    def test_review_text_is_bigger_and_wraps_inside_the_card(self):
        text = rule(".review-item-text")
        self.assertIn("font-size: 16px", text)
        self.assertIn("overflow-wrap: break-word", text)
        quote = rule(".review-item-quote")
        self.assertIn("font-size: 16px", quote)
        self.assertIn("overflow-wrap: break-word", quote)
        # The quote still keeps the words' own line breaks.
        self.assertIn("white-space: pre-wrap", quote)

    def test_review_cards_can_shrink_and_size_to_content(self):
        self.assertIn("min-width: 0", rule(".review-item"))
        self.assertIn("min-width: 0", rule("#reviewList"))

    def test_style_suggestion_cards_take_the_same_step(self):
        self.assertIn("font-size: 16px",
                      rule("#styleCard .comment-text"))
        self.assertIn("font-size: 15px", rule("#styleCard .action"))
        comment = rule("#styleCard .comment")
        self.assertIn("min-width: 0", comment)
        self.assertIn("overflow-wrap: break-word", comment)


class VideoFitTests(unittest.TestCase):
    def test_the_fit_reads_the_loaded_videos_own_shape(self):
        body = between("function fitVideoToShape()",
                       "vid.addEventListener(\"loadedmetadata\"")
        self.assertIn("vid.videoWidth", body)
        self.assertIn("vid.videoHeight", body)
        self.assertIn('document.getElementById("vidWrap")', body)
        self.assertIn("wrap.clientWidth", body)
        self.assertIn("wrap.clientHeight", body)
        # The largest box of the video's shape that fits.
        self.assertIn(
            "Math.min(availW / vw, availH / vh)", body)
        self.assertIn('vid.style.width = ', body)
        self.assertIn('vid.style.height = ', body)

    def test_the_fit_respects_the_stylesheets_height_caps(self):
        body = between("function fitVideoToShape()",
                       "vid.addEventListener(\"loadedmetadata\"")
        self.assertIn("getComputedStyle(vid).maxHeight", body)
        self.assertIn("cap < availH", body)

    def test_the_fit_only_runs_in_the_fitted_layouts(self):
        body = between("function fitVideoToShape()",
                       "vid.addEventListener(\"loadedmetadata\"")
        self.assertIn(
            'window.matchMedia("(min-width: 720px)")', body)
        # Outside them, any size set here is cleared and the
        # stylesheet's plain width rule stands.
        self.assertIn('vid.style.width = "";', body)
        self.assertIn('vid.style.height = "";', body)

    def test_the_fit_runs_on_metadata_resize_and_wrap_changes(self):
        self.assertIn(
            "vid.addEventListener(\"loadedmetadata\", function () {\n"
            "    fitVideoToShape();", PAGE)
        self.assertIn(
            'window.addEventListener("resize", '
            'function () { fitVideoToShape(); });', PAGE)
        self.assertIn("window.ResizeObserver", PAGE)
        self.assertIn("new ResizeObserver(", PAGE)

    def test_the_wrap_centers_the_picture_in_fitted_layouts(self):
        fitted = between("/* The video fits its own shape (v0.20, chunk D)",
                         "/* The Edit settings strip stays reachable")
        self.assertIn("@media (min-width: 720px)", fitted)
        self.assertIn("display: flex", fitted)
        self.assertIn("align-items: center", fitted)
        self.assertIn("justify-content: center", fitted)
        self.assertIn("body.job-loaded .review-left #vid { flex: none; }",
                      fitted)

    def test_the_contain_fallback_rules_stay(self):
        # Before the script runs, or when a shape cannot be
        # read, the picture is still never stretched.
        left = between("body.job-loaded .review-left #vid {",
                       "body.job-loaded.expanded-video .review-left #vid")
        self.assertIn("object-fit: contain", left)


class EditSettingsStripTests(unittest.TestCase):
    def test_the_bar_may_shrink_in_the_fitted_frame(self):
        # rule() joins every body the selector carries: the
        # v0.17 fitted cap below and this chunk's shrink.
        bodies = rule("body.job-loaded .edit-settings-bar")
        self.assertIn("flex: 0 1 auto", bodies)
        self.assertIn("min-height: 0", bodies)

    def test_the_strip_sticks_to_the_top_of_its_bar(self):
        summary = rule(".edit-settings-bar .settings-summary")
        self.assertIn("position: sticky", summary)
        self.assertIn("top: 0", summary)
        self.assertIn("background: var(--panel)", summary)

    def test_the_v017_inside_scroll_cap_stands(self):
        bodies = rule("body.job-loaded .edit-settings-bar")
        self.assertIn("max-height: 38vh", bodies)
        self.assertIn("overflow-y: auto", bodies)


class TrainIsCoreTests(unittest.TestCase):
    def train_block(self) -> str:
        return between('class="train-home"', 'id="intakePopup"')

    def test_train_stands_as_its_own_titled_section(self):
        block = self.train_block()
        self.assertIn('class="train-home"', block)
        self.assertIn("<h2>Train the editor</h2>", block)
        # Beside Bring in a video: the section lives in the
        # drop card, under the drop area.
        card = between('id="dropCard"', 'id="homeChatCard"')
        self.assertIn('id="trainHomeRow"', card)
        self.assertLess(card.index('id="dropArea"'),
                        card.index('id="trainHomeRow"'))

    def test_train_says_what_teaching_does_in_plain_words(self):
        block = self.train_block()
        self.assertIn(
            "show the editor one finished edit and it studies "
            "how you cut", block)
        self.assertIn(
            "It shows you the rules it learned in plain words "
            "before anything is saved", block)
        self.assertIn("its cuts start closer to yours", block)

    def test_no_vague_cost_line_and_no_optional_framing(self):
        block = self.train_block()
        self.assertNotIn("few cents", block)
        self.assertNotIn("Optional", block)
        # The honest per-run wording, the same basis the
        # teach card's step 3 uses.
        self.assertIn(
            "Listening costs about 4 cents for every 10 "
            "minutes of video, plus about 5 cents for the "
            "read.", block)

    def test_the_cap_on_the_button_is_the_normal_sized_mark(self):
        cap = rule("#trainHomeBtn svg")
        self.assertIn("width: 20px", cap)
        self.assertIn("height: 20px", cap)
        # The same 20px as the teach card's head.
        self.assertIn("width: 20px", rule("#teachCard h2 svg"))

    def test_the_teach_card_no_longer_calls_teaching_optional(self):
        card = between('id="teachCard"', 'id="novid"')
        self.assertNotIn("This is optional.", card)
        self.assertIn(
            "Show the editor one finished edit, and it learns "
            "how you cut.", card)
        self.assertIn("its cuts start closer to yours", card)
        # The card's own honest cost line stands.
        self.assertIn(
            "Listening costs about 4 cents for every 10 minutes "
            "of recording, plus about 5 cents for the read.", card)

    def test_the_button_still_opens_the_teach_card(self):
        self.assertIn('trainHomeBtn.addEventListener('
                      '"click", openTeachCard);', PAGE)


class ServedStartPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v20-d-"))
        self.home = self.tmp / "launch-home"
        self.server = create_server(
            WebAppConfig(None, launch_home=self.home),
            host="127.0.0.1", port=0,
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_served_start_page_carries_the_new_train_section(self):
        req = urllib.request.Request(self.base + "/")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            self.fail(f"start page status {exc.code}")
        self.assertIn('class="train-home" id="trainHomeRow"', text)
        self.assertIn("<h2>Train the editor</h2>", text)
        self.assertNotIn(
            "Optional, and it costs a few cents to listen "
            "and read.", text)
        self.assertIn("function fitVideoToShape()", text)


if __name__ == "__main__":
    unittest.main()
