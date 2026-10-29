"""v0.11 layout fit: teach card, panel limits, one window.

Items 1, 2, and 4 from Justin's Oct 28, 2026 screen recording
of the 0.10.1 app (feedback-2026-10-28/FIX-ITEMS.md):

1. The Teach the app card filled the top of the review screen
   for the whole review. Once a job is loaded it collapsed to a
   slim bar behind one clearly named button, the same way the
   drop card already did. With no job loaded the front door
   keeps the full card. (v0.11.1, item 1: the slim bar itself is
   gone now. The Teach the app button lives in the header row
   and the card stays out of sight until that button opens it;
   test_v111_header.py pins the new shape.)
2. The arrangeable panels had no fit limits, so the workspace
   could be stretched past the window and the comments column
   could fall off the right edge, while the video sat small
   inside a wide black box. Panel widths now have minimums and
   maximums that the drag and the stored values both respect,
   and the video keeps its own shape inside its panel.
4. At 100% zoom on a 1440px window, video, transcript, and
   comments are all visible together; scrolling happens inside
   panels only and the page itself never scrolls sideways.

These tests read the page template the same way test_v09_layout
and test_v101_fixes do, plus one server check that the served
front door carries the new pieces. Everything is offline.
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


def clamp_numbers(var_name: str):
    """The (min, default, max) of one clamped column variable."""
    match = re.search(
        re.escape(var_name) + r": clamp\((\d+)px, var\(--pane-[ac], "
        r"(\d+)px\), (\d+)px\);",
        TEMPLATE,
    )
    assert match is not None, var_name
    return tuple(int(match.group(i)) for i in (1, 2, 3))


# ---------------------------------------------------------------------------
# 1: the Teach the app card stays out of the review until opened
# (v0.11.1, item 1: the slim bar is gone; the button moved to the
# header row, and the whole card stays out of sight in review
# until that button opens it)
# ---------------------------------------------------------------------------


class TeachCardCollapseTests(unittest.TestCase):
    def teach_card(self) -> str:
        return between('id="teachCard"', 'id="novid"')

    def test_header_button_with_one_clear_name(self):
        head = between('<header class="app-head">', "</header>")
        self.assertIn('id="teachToggle"', head)
        self.assertIn(">Teach the app<", head)
        self.assertIn('aria-expanded="false"', head)
        self.assertIn('aria-controls="teachFull"', head)
        # No slim bar sits in the card, or anywhere else.
        self.assertNotIn('id="teachSlimBar"', self.teach_card())
        self.assertNotIn('id="teachSlimBar"', TEMPLATE)
        self.assertNotIn(
            "Teaching is tucked away while you review.", TEMPLATE)

    def test_full_choices_live_inside_the_collapsible_part(self):
        full = between('id="teachFull"', 'id="novid"')
        for marker in (
            'id="teachRecording"', 'id="teachRaw"', 'id="teachFinished"',
            'id="teachLink"', 'id="teachRun"', 'id="teachStatus"',
        ):
            self.assertIn(marker, full)

    def test_card_collapses_only_once_a_job_is_loaded(self):
        # The whole card hides only in review, and shows there
        # only once opened; the front door (no job) is untouched.
        self.assertIn(
            "body.job-loaded #teachCard { display: none; }",
            TEMPLATE,
        )
        self.assertIn(
            "body.job-loaded #teachCard.expanded .teach-full "
            "{ display: block;",
            TEMPLATE,
        )
        self.assertNotIn("body.no-job #teachCard", TEMPLATE)
        self.assertNotIn("body.no-job #teachSlimBar", TEMPLATE)

    def test_one_click_opens_and_closes_the_teaching_choices(self):
        self.assertIn(
            'var teachToggle = document.getElementById("teachToggle");',
            TEMPLATE,
        )
        self.assertIn('teachCardEl.classList.toggle("expanded")', TEMPLATE)
        self.assertIn(
            'teachToggle.setAttribute("aria-expanded"', TEMPLATE)
        self.assertIn('"Hide teaching choices"', TEMPLATE)
        self.assertIn('"Teach the app"', TEMPLATE)


# ---------------------------------------------------------------------------
# 2: panel width limits, and the video keeps its shape
# ---------------------------------------------------------------------------


class PanelWidthLimitTests(unittest.TestCase):
    def test_stored_widths_are_read_through_clamps(self):
        # v0.11.1, item 2: the video column's widest is no
        # longer the fixed 420px. It is the window width minus
        # 620px (the other panels at their smallest, the
        # dividers, and the page padding), and the comments
        # clamp reads the video column so the two can never add
        # up past the window. See test_v111_resize for the full
        # pinning of the new behavior.
        self.assertIn(
            "--col-a: clamp(280px, var(--pane-a, 350px), "
            "calc(100vw - 620px));",
            TEMPLATE,
        )
        self.assertIn(
            "--col-c: clamp(260px, var(--pane-c, 310px), "
            "min(360px, calc(100vw - var(--col-a) - 360px)));",
            TEMPLATE,
        )

    def test_no_column_reads_a_stored_width_without_a_clamp(self):
        # Every grid use of the stored --pane-a / --pane-c values
        # goes through a clamp; nothing can stretch a column past
        # its limit, whatever an older visit saved.
        for line in TEMPLATE.splitlines():
            if "var(--pane-a" in line or "var(--pane-c" in line:
                self.assertIn("clamp(", line, line)

    def test_the_drag_stops_at_the_same_limits(self):
        # v0.11.1, item 2: the video drag's widest is worked out
        # from the split width at drag time (the transcript and
        # comments at their smallest, plus the dividers), not
        # the old fixed 420px cap.
        self.assertIn(
            "var maxVideoPx = Math.max(280, rect.width - 320 - 260 - 20);",
            TEMPLATE,
        )
        self.assertIn("Math.min(maxVideoPx, Math.max(280, px))", TEMPLATE)
        self.assertIn("Math.min(360, Math.max(260, px))", TEMPLATE)
        self.assertNotIn("Math.min(420, Math.max(280, px))", TEMPLATE)
        self.assertNotIn("Math.min(560", TEMPLATE)
        self.assertNotIn("Math.min(480", TEMPLATE)

    def test_every_arrangement_uses_the_limited_columns(self):
        for marker in (
            "grid-template-columns: var(--col-a) 10px "
            "minmax(320px, 1fr) 10px var(--col-c);",
            "grid-template-columns: minmax(320px, 1fr) 10px "
            "var(--col-a) 10px var(--col-c);",
            "grid-template-columns: var(--col-a) 10px "
            "minmax(0, 1fr) 0 0;",
            "grid-template-columns: minmax(0, 1fr) 10px var(--col-c);",
            "grid-template-columns: var(--col-a) 10px minmax(0, 1fr);",
            "grid-template-columns: var(--col-a) 0 0;",
        ):
            self.assertIn(marker, TEMPLATE)

    def test_hiding_a_panel_gives_its_space_to_the_others(self):
        # Video hidden: the transcript takes the flexible column.
        video_hidden = between(
            "body.job-loaded.video-hidden .review-split",
            "body.job-loaded.video-hidden .review-right",
        )
        self.assertIn("minmax(0, 1fr) 10px var(--col-c)", video_hidden)
        # Transcript hidden: the comments panel takes it instead.
        transcript_hidden = between(
            "body.job-loaded.transcript-hidden .review-split",
            "body.job-loaded.transcript-hidden .review-left",
        )
        self.assertIn("var(--col-a) 10px minmax(0, 1fr)", transcript_hidden)
        # Comments closed: the transcript takes the flexible column.
        closed = between(
            "body.comments-closed .review-split",
            "body.comments-closed #toolsPane",
        )
        self.assertIn("var(--col-a) 10px minmax(0, 1fr) 0 0", closed)


class VideoShapeTests(unittest.TestCase):
    def test_video_box_is_sized_by_its_width_and_its_shape(self):
        rule = between("body.job-loaded #vid {", "expanded-video #vid")
        self.assertIn("width: 100%", rule)
        self.assertIn("height: auto", rule)
        self.assertIn("max-width: 100%", rule)
        self.assertIn("object-fit: contain", rule)

    def test_video_wrap_never_shrinks_in_its_column(self):
        self.assertIn(
            "body.job-loaded .vid-wrap { flex: none;", TEMPLATE)

    def test_widened_video_panel_is_capped_by_the_window(self):
        # v0.11.1, item 2: the old fixed 720px cap on the
        # widened picture is gone. The picture can take the
        # panel's full width, and its height is capped by the
        # window height, so it can grow big without leaving
        # the view.
        self.assertIn(
            "body.job-loaded.expanded-video #vid "
            "{ max-width: 100%; max-height: calc(100dvh - 160px); }",
            TEMPLATE,
        )
        self.assertNotIn("max-width: 720px", TEMPLATE)

    def test_player_takes_the_files_real_shape_on_load(self):
        body = between("function loadVideo(name)", "function showPendingFor")
        self.assertIn("videoShapes[name]", body)
        self.assertIn("vid.style.aspectRatio", body)
        self.assertIn('shape.width + " / " + shape.height', body)


# ---------------------------------------------------------------------------
# 4: the whole workspace fits a normal window at 100% zoom
# ---------------------------------------------------------------------------


class WholeWorkspaceFitTests(unittest.TestCase):
    def test_three_columns_fit_a_1440_window_at_their_widest(self):
        # v0.11.1, item 2: the video column's widest is derived
        # from the window (window width minus 620px, parsed out
        # of the clamp), and at that widest the comments column
        # is read at its smallest (260px) because its own clamp
        # subtracts the video column. The fit question is now:
        # at the video's widest, do video + comments smallest +
        # transcript smallest + dividers + padding still fit?
        a_line = re.search(r"--col-a: clamp\(([^;]*)\);", TEMPLATE)
        self.assertIsNotNone(a_line)
        reserve = int(
            re.search(r"calc\(100vw - (\d+)px\)", a_line.group(1)).group(1)
        )
        a_min, a_default = 280, 350
        c_min, c_default = 260, 310
        transcript_min = 320
        self.assertIn("minmax(320px, 1fr)", TEMPLATE)
        dividers = 10 + 10
        page_padding = 10 + 10
        # The reserve is exactly the pieces that must survive at
        # the video's widest.
        self.assertEqual(
            reserve,
            transcript_min + c_min + dividers + page_padding,
        )
        a_max_at_1440 = 1440 - reserve
        widest = (
            a_max_at_1440 + c_min + transcript_min + dividers + page_padding
        )
        self.assertLessEqual(widest, 1440)
        # At the defaults the transcript gets a wide, readable share.
        transcript_at_default = (
            1440 - page_padding - a_default - c_default - dividers
        )
        self.assertGreaterEqual(transcript_at_default, 600)
        # And the minimums fit the smallest laptop layout width.
        narrowest = a_min + c_min + transcript_min + dividers + page_padding
        self.assertLessEqual(narrowest, 1100)

    def test_page_never_scrolls_sideways_in_review(self):
        self.assertIn("body.job-loaded { overflow: hidden; }", TEMPLATE)
        main_rule = between("body.job-loaded main {", "body.job-loaded .app-head")
        self.assertIn("width: 100%", main_rule)
        self.assertIn("max-width: 100%", main_rule)
        self.assertIn("overflow-x: hidden", main_rule)
        split_rule = between(
            "body.job-loaded .review-split {",
            "body.job-loaded .vid-wrap",
        )
        self.assertIn("width: 100%", split_rule)
        self.assertIn("max-width: 100%", split_rule)
        self.assertIn("min-width: 0", split_rule)

    def test_scrolling_happens_inside_panels_only(self):
        for marker in (
            "body.job-loaded .review-left",
            "body.job-loaded .review-tools",
            "body.job-loaded .review-right #transcriptPanel",
        ):
            self.assertIn(marker, TEMPLATE)
        self.assertIn("overflow-y: auto", TEMPLATE)

    def test_all_three_panels_are_in_the_split(self):
        split = between('id="reviewSplit"', "</main>")
        self.assertIn('id="vid"', split)
        self.assertIn('id="transcriptCard"', split)
        self.assertIn('id="toolsPane"', split)


class ServedFrontDoorTests(unittest.TestCase):
    """The served page (no job loaded) carries the new pieces,
    and the teach card is in its full front door shape there."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v11-"))
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

    def test_served_page_carries_the_layout_fit_pieces(self):
        with urllib.request.urlopen(self.base + "/", timeout=30) as resp:
            self.assertEqual(resp.status, 200)
            page = resp.read().decode("utf-8")
        for marker in (
            'id="bringInBtn"', 'id="teachToggle"', 'id="teachFull"',
            "--col-a: clamp(280px", "--col-c: clamp(260px",
            "body.job-loaded #vid",
        ):
            self.assertIn(marker, page)
        self.assertNotIn('id="teachSlimBar"', page)


if __name__ == "__main__":
    unittest.main()
