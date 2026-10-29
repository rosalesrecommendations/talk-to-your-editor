"""v0.11 workspace fit: everything in the main view, item 7.

From Justin's Oct 28, 2026 clarification (feedback-2026-10-28/
FIX-ITEMS.md, item 7, with item 4): he does not want to scroll
to reach parts of the review. The whole workspace conforms to
the window at any reasonable size: panels resize, the video
gets smaller, the transcript area flexes, and every control
stays inside the visible view. Page scrolling is only a last
resort on a very small window, and then it must work normally,
with no scroll lock left on after a pop up closes.

The 0.10.1 trap had two halves, both reproduced in a browser
before this fix:

1. The drop card was never closed in the markup. The teach
   card, the no-video card, and the whole review split were
   children of the drop card instead of the page column, so
   the split was never a flex child of the page, its flex
   sizing never applied, and the workspace grew to its full
   natural height (about 3300px on a 1080px window) inside a
   page locked against scrolling. The parts below the window
   were simply unreachable.
2. The fitted frame only existed at 1100px and wider, with a
   100dvh height and no fallback; below that width the split
   unwrapped into one long stacked page, and the pop up lock
   was never synced at load.

These tests read the page template the same way
test_v11_layout and test_v11_transcript do, plus a small
markup parse that proves the nesting itself, and one served
page check. Everything is offline.
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
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.webapp import WebAppConfig, create_server  # noqa: E402

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")

BLOCK_MARKER = "Workspace fit at every size (v0.11.0, item 7)"


def fit_block() -> str:
    """The v0.11.0 item 7 CSS block, from its comment to the
    end of the style sheet."""
    start = TEMPLATE.index(BLOCK_MARKER)
    start = TEMPLATE.rindex("/*", 0, start)
    end = TEMPLATE.index("</style>", start)
    return TEMPLATE[start:end]


def media_block(css: str, query: str) -> str:
    """One @media block's text: from the query line to the
    next @media or the end of the given CSS."""
    start = css.index(query)
    end = css.find("@media", start + len(query))
    return css[start:] if end == -1 else css[start:end]


def rule_body(css: str, selector: str) -> str:
    match = re.search(
        re.escape(selector) + r"\s*\{([^}]*)\}", css, re.DOTALL
    )
    assert match is not None, selector
    return match.group(1)


def rule_bodies(css: str, selector: str) -> str:
    """Every rule body declared for one selector, joined: a
    selector can carry its guarantees across several rules."""
    bodies = re.findall(
        re.escape(selector) + r"\s*\{([^}]*)\}", css, re.DOTALL
    )
    assert bodies, selector
    return " ".join(bodies)


# ---------------------------------------------------------------------------
# The root cause: the drop card must close before the teach card
# ---------------------------------------------------------------------------


VOID_TAGS = {
    "meta", "link", "br", "hr", "img", "input", "source",
    "wbr", "col", "area", "base", "embed", "track",
}


class MarkupTree(HTMLParser):
    """Records each watched element's parent and the drop
    card's direct children, over the body markup only."""

    WATCHED = {
        "dropCard", "dropFull", "teachCard", "novid",
        "reviewSplit", "transcriptCard", "toolsPane",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list = []
        self.parents: dict = {}
        self.drop_children: list = []

    def _label(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            return attrs["id"]
        classes = (attrs.get("class") or "").split()
        return classes[0] if classes else tag

    def handle_starttag(self, tag, attrs):
        el_id = dict(attrs).get("id")
        if self.stack and self.stack[-1][1] == "dropCard" and el_id:
            self.drop_children.append(el_id)
        if el_id in self.WATCHED:
            parent = self.stack[-1][1] if self.stack else None
            self.parents[el_id] = parent
        if tag not in VOID_TAGS:
            self.stack.append((tag, self._label(tag, attrs)))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return


def parsed_tree() -> MarkupTree:
    body = TEMPLATE.split("</style>", 1)[1].split("<script>", 1)[0]
    tree = MarkupTree()
    tree.feed(body)
    return tree


class DropCardNestingTests(unittest.TestCase):
    """The split, the teach card, and the no-video card are
    children of the page column, never of the drop card: that
    nesting is what lets the page's flex sizing reach them."""

    def setUp(self):
        self.tree = parsed_tree()

    def test_review_split_is_a_direct_child_of_main(self):
        self.assertEqual(self.tree.parents["reviewSplit"], "main")
        self.assertEqual(self.tree.parents["teachCard"], "main")
        self.assertEqual(self.tree.parents["novid"], "main")
        self.assertEqual(self.tree.parents["dropCard"], "main")

    def test_drop_card_holds_only_its_own_pieces(self):
        # v0.11.1, item 1: the slim bar that used to sit at the
        # top of this card is gone; the card holds only its full
        # contents now.
        self.assertEqual(self.tree.drop_children, ["dropFull"])
        self.assertEqual(self.tree.parents["dropFull"], "dropCard")

    def test_panes_stay_inside_the_split(self):
        self.assertEqual(
            self.tree.parents["transcriptCard"], "review-right"
        )
        self.assertEqual(self.tree.parents["toolsPane"], "reviewSplit")


# ---------------------------------------------------------------------------
# The fitted frame holds from 720px up
# ---------------------------------------------------------------------------


class FittedFrameTests(unittest.TestCase):
    def setUp(self):
        self.block = fit_block()
        self.frame = media_block(self.block, "@media (min-width: 720px) {")

    def test_page_height_has_a_plain_fallback_before_dvh(self):
        main = rule_body(self.frame, "body.job-loaded main")
        self.assertIn("height: 100vh; height: 100dvh;", main)
        self.assertIn("display: flex", main)
        self.assertIn("flex-direction: column", main)
        self.assertIn("overflow: hidden", main)
        self.assertIn("overflow-x: hidden", main)
        self.assertIn("max-width: 100%", main)

    def test_the_wide_frame_rule_also_carries_the_fallback(self):
        # The older workspace frame block (v2) declares the same
        # height pair now, not a bare 100dvh.
        self.assertGreaterEqual(
            TEMPLATE.count("height: 100vh; height: 100dvh;"), 2
        )
        self.assertIn(
            "max-height: calc(100vh - 72px); "
            "max-height: calc(100dvh - 72px);",
            TEMPLATE,
        )

    def test_split_takes_the_leftover_space_and_can_shrink(self):
        split = rule_body(self.frame, "body.job-loaded .review-split")
        self.assertIn("flex: 1 1 auto", split)
        self.assertIn("min-height: 0", split)
        self.assertIn("min-width: 0", split)
        self.assertIn("overflow: hidden", split)

    def test_every_pane_can_shrink_and_scrolls_inside_itself(self):
        panes = rule_body(
            self.frame,
            "body.job-loaded .review-left,\n"
            "    body.job-loaded .review-right,\n"
            "    body.job-loaded .review-tools",
        )
        self.assertIn("min-height: 0", panes)
        self.assertIn("min-width: 0", panes)
        left = rule_body(self.frame, "body.job-loaded .review-left")
        self.assertIn("overflow-y: auto", left)
        tools = rule_bodies(self.frame, "body.job-loaded .review-tools")
        self.assertIn("overflow-y: auto", tools)
        panel = rule_bodies(
            self.frame, "body.job-loaded .review-right #transcriptPanel"
        )
        self.assertIn("flex: 1 1 auto", panel)
        self.assertIn("min-height: 0", panel)
        self.assertIn("overflow-y: auto", panel)

    def test_page_lock_only_exists_inside_fitted_widths(self):
        # Every body.job-loaded overflow:hidden sits inside a
        # min-width media block (the fitted frame), so at the
        # fallback widths nothing locks the page.
        for match in re.finditer(
            r"body\.job-loaded \{ overflow: hidden; \}", TEMPLATE
        ):
            before = TEMPLATE.rfind("@media", 0, match.start())
            self.assertIn("min-width", TEMPLATE[before:match.start()])

    def test_no_sideways_scroll_at_any_size(self):
        self.assertIn("body { overflow-x: hidden; }", self.block)
        self.assertIn("max-width: 100%", self.frame)

    def test_transport_wraps_inside_its_pane(self):
        # The transport row is wider than a narrow video pane;
        # wrapping keeps every button inside the pane's width
        # instead of making the pane scroll sideways.
        self.assertIn(
            "body.job-loaded .transport { flex-wrap: wrap; }",
            self.frame,
        )
        self.assertIn(
            "body.job-loaded .transport-center "
            "{ flex-wrap: wrap; justify-content: center; }",
            self.frame,
        )


# ---------------------------------------------------------------------------
# Half-size windows: the columns rearrange, nothing unwraps
# ---------------------------------------------------------------------------


class HalfSizeRearrangeTests(unittest.TestCase):
    def setUp(self):
        self.block = fit_block()
        self.medium = media_block(
            self.block,
            "@media (min-width: 720px) and (max-width: 1099.98px) {",
        )

    def test_two_columns_and_a_tools_band(self):
        split = rule_body(self.medium, "body.job-loaded .review-split")
        self.assertIn(
            "grid-template-columns: minmax(0, 300px) minmax(0, 1fr)",
            split,
        )
        self.assertIn(
            "grid-template-rows: minmax(0, 1fr) minmax(96px, 24%)",
            split,
        )
        self.assertIn("display: grid", split)

    def test_tools_band_is_a_real_box_not_display_contents(self):
        # display:contents here would spill the tools cards into
        # the grid as loose items and break the rows; the band is
        # one box across the bottom that scrolls inside itself.
        tools = rule_body(self.medium, "body.job-loaded .review-tools")
        self.assertIn("display: block", tools)
        self.assertIn("grid-column: 1 / -1", tools)
        self.assertIn("grid-row: 2", tools)

    def test_dividers_step_aside_when_the_window_sizes_columns(self):
        rule = rule_body(
            self.medium,
            "body.job-loaded #paneDivider,\n"
            "    body.job-loaded #toolsDivider",
        )
        self.assertIn("display: none", rule)

    def test_arrangements_stay_inside_the_same_rows(self):
        for marker in (
            "body.job-loaded.panels-swapped .review-left",
            "body.job-loaded.video-hidden .review-right",
            "body.job-loaded.transcript-hidden .review-left",
            "body.job-loaded.comments-closed .review-tools",
            "body.job-loaded.expanded-video .review-left",
            "body.job-loaded.expanded-transcript .review-right",
            "body.job-loaded.expanded-comments .review-tools",
        ):
            self.assertIn(marker, self.medium)

    def test_transcript_keeps_a_visible_share(self):
        # The card's trimmings shrink so the words are not
        # squeezed to nothing: tighter card, no help line next
        # to the counts, a compact header and status line.
        self.assertIn(
            "body.job-loaded #transcriptCard { padding: 14px 16px; }",
            self.medium,
        )
        self.assertIn(
            "body.job-loaded #txClockNote { display: none; }",
            self.medium,
        )
        panel = rule_body(
            self.medium, "body.job-loaded #transcriptPanel"
        )
        self.assertIn("margin-top: 4px", panel)


# ---------------------------------------------------------------------------
# Very small windows: the page scrolls normally as a last resort
# ---------------------------------------------------------------------------


class SmallWindowFallbackTests(unittest.TestCase):
    def setUp(self):
        self.small = media_block(
            fit_block(), "@media (max-width: 719.98px) {"
        )

    def test_frame_lets_go_and_the_page_scrolls(self):
        body = rule_body(self.small, "body.job-loaded")
        self.assertIn("overflow-y: visible", body)
        self.assertNotIn("overflow: hidden", body)
        main = rule_body(self.small, "body.job-loaded main")
        self.assertIn("height: auto", main)
        self.assertIn("overflow: visible", main)

    def test_panes_are_not_height_bound_in_the_fallback(self):
        rule = rule_body(
            self.small,
            "body.job-loaded .review-left,\n"
            "    body.job-loaded .review-tools",
        )
        self.assertIn("max-height: none", rule)
        self.assertIn("overflow-y: visible", rule)
        panel = rule_body(
            self.small,
            "body.job-loaded .review-right #transcriptPanel",
        )
        self.assertIn("max-height: 52vh", panel)
        self.assertIn("overflow-y: auto", panel)


# ---------------------------------------------------------------------------
# The pop up lock: only while a pop up is open, synced at load
# ---------------------------------------------------------------------------


class PopupLockTests(unittest.TestCase):
    def test_lock_is_synced_as_the_page_loads(self):
        # The standalone sync call sits between the closePopups
        # definition and the first open path, so the page's
        # starting state is read from the panels themselves
        # (both hidden) and can never start locked.
        close_def = TEMPLATE.index("function closePopups()")
        at_load = TEMPLATE.index("\n  syncPopupScrim();\n", close_def)
        first_open = TEMPLATE.index("function openSettingsAtMoney()")
        self.assertLess(close_def, at_load)
        self.assertLess(at_load, first_open)
        self.assertIn(
            "Sync the pop up lock with the page as it loads",
            TEMPLATE,
        )

    def test_lock_follows_the_panels_hidden_flags(self):
        body = TEMPLATE.split("function syncPopupScrim()", 1)[1]
        body = body.split("function closePopups()", 1)[0]
        self.assertIn("!settingsPanel.hidden", body)
        self.assertIn("!howItWorksPanel.hidden", body)
        self.assertIn(
            'classList.toggle("popup-open", !!anyOpen)', body
        )

    def test_every_close_path_releases_the_lock(self):
        # Close buttons, scrim click, and Escape all go through
        # closePopups; the two toggles and the money jump end in
        # syncPopupScrim.
        for marker in (
            'settingsClose.addEventListener("click", closePopups)',
            'howItWorksClose.addEventListener("click", closePopups)',
            'popupScrim.addEventListener("click", closePopups)',
            'if (e.key === "Escape") { closePopups(); }',
        ):
            self.assertIn(marker, TEMPLATE)
        money = TEMPLATE.split("function openSettingsAtMoney()", 1)[1]
        money = money.split("\n  }", 1)[0]
        self.assertIn("syncPopupScrim();", money)


# ---------------------------------------------------------------------------
# The front door: nothing there can trap the page either
# ---------------------------------------------------------------------------


class FrontDoorScrollTests(unittest.TestCase):
    def test_no_job_body_is_never_locked_or_height_bound(self):
        self.assertNotIn("body.no-job { overflow", TEMPLATE)
        for match in re.finditer(
            r"body\.no-job main \{([^}]*)\}", TEMPLATE
        ):
            self.assertNotIn("height:", match.group(1))
            self.assertNotIn("overflow", match.group(1))

    def test_front_door_keeps_the_normal_page_flow(self):
        # The base page is a plain stacked column; only the
        # job-loaded frame binds heights, and only from 720px.
        self.assertIn(
            "main { max-width: 760px; margin: 0 auto; "
            "padding: 40px 20px 96px; }",
            TEMPLATE,
        )


# ---------------------------------------------------------------------------
# Served page: the fix pieces arrive over the wire
# ---------------------------------------------------------------------------


class ServedPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v11-scroll-"))
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

    def test_served_page_carries_the_fit_pieces(self):
        with urllib.request.urlopen(self.base + "/", timeout=30) as resp:
            self.assertEqual(resp.status, 200)
            page = resp.read().decode("utf-8")
        for marker in (
            BLOCK_MARKER,
            "@media (min-width: 720px) and (max-width: 1099.98px)",
            "@media (max-width: 719.98px)",
            "Sync the pop up lock with the page as it loads",
            "height: 100vh; height: 100dvh;",
        ):
            self.assertIn(marker, page)


if __name__ == "__main__":
    unittest.main()
