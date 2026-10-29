"""v0.11.1 two-direction resizing: item 2 of the Oct 28, 2026
evening fix round (v0111-work/FIX-ITEMS.md).

In 0.11 the panels resized sideways only, inside stored width
limits, and the video could not grow past its column: the video
column was capped at a fixed 420px and the widened picture at a
fixed 720px. Justin: "I can't make the video window bigger to
see things and I want to. I want more customizability for those
windows, to drag up & down and make bigger, etc."

What changed, and what these tests pin:

- A horizontal drag bar under the workspace row makes the row
  (and so the video panel) taller or shorter. The height is
  stored in the same local storage the pane widths use, under
  its own key (tye-pane-height, beside tye-pane-a and
  tye-pane-c), and Reset layout clears it with the widths.
- The video column's widest is derived from the window: the
  window width minus the transcript and comments at their
  smallest, the dividers, and the page padding. The comments
  clamp reads the video column, so the columns still add up
  to no more than the window at any stored sizes.
- The 0.11 fit rules keep holding: panes scroll inside
  themselves, the 720px to 1099px comments band arrangement
  and the under-720px page scroll fallback are untouched, and
  the panel buttons keep working exactly as before.

These tests read the page template the same way
test_v11_layout and test_v11_scroll do, plus one served page
check. Everything is offline.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.webapp import WebAppConfig, create_server  # noqa: E402

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")


def between(start_marker: str, end_marker: str) -> str:
    start = TEMPLATE.index(start_marker)
    end = TEMPLATE.index(end_marker, start)
    return TEMPLATE[start:end]


def col_a_reserve() -> int:
    """The window reserve in the video column clamp: the
    620 in calc(100vw - 620px)."""
    line = re.search(r"--col-a: clamp\(([^;]*)\);", TEMPLATE)
    assert line is not None
    return int(re.search(r"calc\(100vw - (\d+)px\)", line.group(1)).group(1))


def clamp(value: int, low: int, high: int) -> int:
    return min(high, max(low, value))


def column_widths(window: int, stored_a: int = 350, stored_c: int = 310):
    """The layout math, replayed from the clamps in the page:
    the effective (video, comments) column widths at one
    window width, for given stored widths."""
    video = clamp(stored_a, 280, window - col_a_reserve())
    comments_cap = min(360, window - video - 360)
    comments = clamp(stored_c, 260, comments_cap)
    return video, comments


# ---------------------------------------------------------------------------
# The vertical drag handle exists and is wired to the stored height
# ---------------------------------------------------------------------------


class RowDividerMarkupTests(unittest.TestCase):
    def test_handle_lives_inside_the_split_below_the_panes(self):
        split = between('id="reviewSplit"', "</main>")
        self.assertIn('id="rowDivider"', split)
        self.assertIn('class="row-divider"', split)
        # It comes after the tools pane: it is the bottom edge
        # of the workspace row, not a bar between the panels.
        self.assertLess(
            split.index('id="toolsPane"'), split.index('id="rowDivider"')
        )

    def test_handle_is_a_plain_labelled_separator(self):
        self.assertIn('role="separator"', TEMPLATE)
        self.assertIn('aria-orientation="horizontal"', TEMPLATE)
        self.assertIn(
            'aria-label="Resize the workspace taller or shorter"',
            TEMPLATE,
        )
        self.assertIn('id="rowDivider" role="separator"', TEMPLATE)

    def test_handle_shows_only_in_the_wide_layout(self):
        # Hidden by default, so the narrower layouts (which size
        # the panels from the window) and the front door never
        # show it; the wide layout shows it as a row-resize bar
        # across the bottom of the split.
        self.assertIn(".row-divider { display: none; }", TEMPLATE)
        block = between(
            "Two-direction resizing (v0.11.1, item 2)",
            "@media (min-width: 1100px) and (max-width: 1400px)",
        )
        self.assertIn("@media (min-width: 1100px)", block)
        rule = between("body.job-loaded #rowDivider {", "body.job-loaded #rowDivider:hover")
        self.assertIn("display: block", rule)
        self.assertIn("grid-row: 2", rule)
        self.assertIn("grid-column: 1 / -1", rule)
        self.assertIn("cursor: row-resize", rule)

    def test_panels_stay_in_the_first_row(self):
        self.assertIn(
            "grid-template-rows: minmax(0, 1fr) 10px;", TEMPLATE)
        self.assertIn(".pane-divider { grid-row: 1; }", TEMPLATE)


class RowDividerWiringTests(unittest.TestCase):
    def wiring(self) -> str:
        return between("function wireRowDivider()", "wireRowDivider();")

    def test_drag_sets_the_stored_layout_height(self):
        body = self.wiring()
        self.assertIn('document.getElementById("rowDivider")', body)
        self.assertIn('document.getElementById("reviewSplit")', body)
        self.assertIn('divider.addEventListener("pointerdown"', body)
        # Dragging measures the row from the split's top edge
        # to the pointer, and hands it to the same setter the
        # keyboard path uses.
        self.assertIn("setRowHeight(ev.clientY - rect.top);", body)

    def test_saved_height_is_read_back_at_load(self):
        body = self.wiring()
        self.assertIn(
            'window.localStorage.getItem("tye-pane-height")', body)
        self.assertIn(
            'split.style.setProperty("--pane-height", saved);', body)
        self.assertIn('split.classList.add("row-sized");', body)

    def test_setter_clamps_stores_and_marks_the_row(self):
        body = between("function setRowHeight(px)", "function wireRowDivider()")
        self.assertIn("clampRowHeight(px)", body)
        self.assertIn(
            'split.style.setProperty("--pane-height", heightValue);',
            body,
        )
        self.assertIn(
            'window.localStorage.setItem("tye-pane-height", heightValue);',
            body,
        )

    def test_arrow_keys_resize_the_row_too(self):
        body = self.wiring()
        self.assertIn('divider.addEventListener("keydown"', body)
        self.assertIn('"ArrowDown"', body)
        self.assertIn('"ArrowUp"', body)
        self.assertIn("setRowHeight(rect.height + step);", body)


# ---------------------------------------------------------------------------
# Heights persist in the layout store; Reset restores them
# ---------------------------------------------------------------------------


class StoredHeightTests(unittest.TestCase):
    def test_stored_height_is_read_through_a_window_clamp(self):
        # A height saved on a big screen is capped by the
        # window it is read in: at least 320px, no more than
        # the window height minus 120px of header and padding.
        self.assertIn(
            "body.job-loaded .review-split.row-sized {\n"
            "      flex: 0 0 auto;\n"
            "      height: clamp(320px, var(--pane-height), "
            "calc(100dvh - 120px));",
            TEMPLATE,
        )

    def test_drag_clamp_in_js_matches_the_css_clamp(self):
        body = between("function clampRowHeight(px)", "function setRowHeight(px)")
        self.assertIn(
            "var maxPx = Math.max(320, window.innerHeight - 120);", body)
        self.assertIn(
            "return Math.min(maxPx, Math.max(320, Math.round(px)));",
            body,
        )

    def test_height_math_stays_inside_the_window(self):
        for window_height in (700, 900, 1080, 1440):
            widest = clamp(5000, 320, window_height - 120)
            self.assertLessEqual(widest, window_height - 120)
            self.assertGreaterEqual(widest, 320)

    def test_row_height_also_sizes_a_tall_video(self):
        # While the row is sized by dragging, the picture reads
        # the same stored height, so a tall video grows taller
        # with the row instead of only wider with its column.
        self.assertIn(
            "body.job-loaded .review-split.row-sized #vid {\n"
            "      max-height: max(120px, "
            "calc(var(--pane-height) - 260px));",
            TEMPLATE,
        )

    def test_reset_restores_widths_and_height_together(self):
        body = between(
            'resetLayoutBtn.addEventListener("click"',
            "// -- panel arrangement",
        )
        for marker in (
            'removeItem("tye-pane-a")',
            'removeItem("tye-pane-c")',
            'removeItem("tye-pane-height")',
            'removeItem("tye-layout")',
            'split.style.removeProperty("--pane-a")',
            'split.style.removeProperty("--pane-c")',
            'split.style.removeProperty("--pane-height")',
            'split.classList.remove("row-sized")',
        ):
            self.assertIn(marker, body)
        # The Settings line says so in plain words.
        self.assertIn(
            "Put the panel widths, the workspace height, and "
            "the comments panel back the way they started.",
            TEMPLATE,
        )


# ---------------------------------------------------------------------------
# The video maximum is derived from the window
# ---------------------------------------------------------------------------


class WindowDerivedVideoMaxTests(unittest.TestCase):
    def test_no_fixed_small_clamp_on_the_video_column(self):
        self.assertIn(
            "--col-a: clamp(280px, var(--pane-a, 350px), "
            "calc(100vw - 620px));",
            TEMPLATE,
        )
        self.assertNotIn("var(--pane-a, 350px), 420px)", TEMPLATE)
        self.assertNotIn("Math.min(420, Math.max(280, px))", TEMPLATE)

    def test_reserve_is_the_other_panels_at_their_smallest(self):
        # 620 = transcript 320 + comments 260 + dividers 20 +
        # page padding 20.
        self.assertEqual(col_a_reserve(), 320 + 260 + 20 + 20)

    def test_comments_clamp_reads_the_video_column(self):
        self.assertIn(
            "--col-c: clamp(260px, var(--pane-c, 310px), "
            "min(360px, calc(100vw - var(--col-a) - 360px)));",
            TEMPLATE,
        )

    def test_drag_max_comes_from_the_split_width(self):
        self.assertIn(
            "var maxVideoPx = Math.max(280, "
            "rect.width - 320 - 260 - 20);",
            TEMPLATE,
        )
        self.assertIn(
            "Math.min(maxVideoPx, Math.max(280, px))", TEMPLATE)

    def test_video_can_get_big_on_a_full_screen(self):
        video, _comments = column_widths(1920, stored_a=5000)
        self.assertEqual(video, 1920 - 620)
        self.assertGreater(video, 1000)
        video_1440, _ = column_widths(1440, stored_a=5000)
        self.assertEqual(video_1440, 820)
        self.assertGreater(video_1440, 420)

    def test_widened_picture_is_capped_by_the_window_not_720(self):
        self.assertIn(
            "body.job-loaded.expanded-video #vid "
            "{ max-width: 100%; max-height: calc(100dvh - 160px); }",
            TEMPLATE,
        )
        self.assertNotIn("max-width: 720px", TEMPLATE)


# ---------------------------------------------------------------------------
# The fit invariants still hold in the layout math
# ---------------------------------------------------------------------------


class FitInvariantTests(unittest.TestCase):
    def test_columns_sum_to_no_more_than_the_window(self):
        # At the standard widths, and at the stored extremes in
        # both directions, video + transcript smallest +
        # comments + dividers + padding never passes the window.
        for window in (1100, 1280, 1440, 1920, 2560):
            for stored_a, stored_c in (
                (350, 310), (5000, 360), (5000, 5000), (280, 260),
            ):
                video, comments = column_widths(
                    window, stored_a=stored_a, stored_c=stored_c)
                total = video + 320 + comments + 20 + 20
                self.assertLessEqual(
                    total, window,
                    (window, stored_a, stored_c, video, comments),
                )
                self.assertGreaterEqual(video, 280)
                self.assertGreaterEqual(comments, 260)
                self.assertLessEqual(comments, 360)

    def test_columns_template_and_inside_scrolling_survive(self):
        self.assertIn(
            "grid-template-columns: var(--col-a) 10px "
            "minmax(320px, 1fr) 10px var(--col-c);",
            TEMPLATE,
        )
        for marker in (
            "body.job-loaded .review-left",
            "body.job-loaded .review-tools",
            "body.job-loaded .review-right #transcriptPanel",
        ):
            self.assertIn(marker, TEMPLATE)
        self.assertIn("overflow-y: auto", TEMPLATE)
        self.assertIn("body.job-loaded { overflow: hidden; }", TEMPLATE)

    def test_comments_band_and_small_window_fallback_untouched(self):
        self.assertIn(
            "@media (min-width: 720px) and (max-width: 1099.98px)",
            TEMPLATE,
        )
        self.assertIn(
            "grid-template-rows: minmax(0, 1fr) minmax(96px, 24%);",
            TEMPLATE,
        )
        small = between("@media (max-width: 719.98px) {", "</style>")
        self.assertIn("overflow-y: visible", small)
        self.assertIn("height: auto", small)
        # The row bar never takes part below the wide layout:
        # the only place it is switched on is the wide block.
        self.assertEqual(TEMPLATE.count('id="rowDivider"'), 1)
        wide = between(
            "Two-direction resizing (v0.11.1, item 2)",
            "@media (min-width: 1100px) and (max-width: 1400px)",
        )
        self.assertIn("display: block", wide)

    def test_expanded_panels_hide_the_row_bar(self):
        for name in ("video", "transcript", "comments"):
            self.assertIn(
                f"body.job-loaded.expanded-{name} #rowDivider",
                TEMPLATE,
            )

    def test_old_buttons_and_sideways_drags_keep_working(self):
        for marker in (
            'id="swapSidesBtn"', ">Swap sides<",
            'id="videoExpandBtn"', 'id="transcriptExpandBtn"',
            'id="commentsExpandBtn"', ">Make wider<",
            'id="videoHideBtn"', ">Hide video<",
            "function applyLayout()",
            "function toggleExpandedPanel(name)",
            'wirePaneDivider("paneDivider", "--pane-a", '
            '"tye-pane-a", false);',
            'wirePaneDivider("toolsDivider", "--pane-c", '
            '"tye-pane-c", true);',
            "Math.min(360, Math.max(260, px))",
        ):
            self.assertIn(marker, TEMPLATE)


class ServedPageTests(unittest.TestCase):
    """The served page carries the resize pieces."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v111-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.launch = self.tmp / "launch home"
        self.launch.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)},
                              clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.server = create_server(
            WebAppConfig(None, launch_home=self.launch),
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

    def test_served_page_carries_the_resize_pieces(self):
        with urllib.request.urlopen(self.base + "/", timeout=30) as resp:
            self.assertEqual(resp.status, 200)
            page = resp.read().decode("utf-8")
        for marker in (
            'id="rowDivider"',
            "calc(100vw - 620px)",
            '"tye-pane-height"',
            "function wireRowDivider()",
            "maxVideoPx",
        ):
            self.assertIn(marker, page)


if __name__ == "__main__":
    unittest.main()
