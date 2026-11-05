"""v0.12, change 3: the left column is video first, words gone.

From Justin's Oct 29, 2026 review of 0.11.1 (PLAN.md, left
column item): "way too much text under the video." The column
used to hold the video and then a whole card under it (a
heading, an explainer paragraph, Edit settings, the format
buttons, Make the edit, Talk, and the recap and money lines),
all inside the pane's own scroll, so the picture stayed small
and the button sat below the fold of the pane.

Now the column is a flex column: the video block grows to
take the height the column has first, one action row sits
fixed at the column's foot, and everything the card held
lives in an Edit choices region under that row, closed at
load. Nothing is deleted and nothing is rewritten: the
region is the old card itself (same id, makeEditCard, so the
tool-row wiring and the one-pass buttons that look their
rows up inside it keep working), and Make the edit is the
same button (same id, fdFire, same handler), moved into the
action row.

Pinned here, at source level, in the style of the other page
tests (the scripted probes live in the fit harness):

- F3: the action row holds exactly two controls, Make the
  edit first and the Edit choices toggle second; the choices
  region is collapsed by default and holds every control the
  0.11.1 card held, ids and handlers preserved; the card's
  explainer paragraph is gone from the page, and the one
  fact it carried (the Remove choices live in Edit settings)
  is stated inside Edit settings itself;
- F2/F3 fit, at source level: at the fitted sizes the video
  block flexes to fill the column, the picture is sized by
  its height as well as its width, and the action row does
  not flex, so with the choices closed Make the edit sits at
  the foot of the pane with no pane scroll needed to reach
  it; the pane keeps its own scroll for when the choices are
  open on a small window;
- preservation: the video controls, the widened and
  drag-sized picture caps, the pane's resize and layout
  memory, and the front door (no job loaded) are untouched;
  the new code calls no endpoint.

Everything is offline; no provider is called.
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

STYLE = TEMPLATE[TEMPLATE.index("<style>"):TEMPLATE.index("</style>")]


def between(start_marker: str, end_marker: str) -> str:
    start = TEMPLATE.index(start_marker)
    end = TEMPLATE.index(end_marker, start)
    return TEMPLATE[start:end]


def left() -> str:
    """The left column's markup, panel head to the divider."""
    return between('class="review-left"', 'id="paneDivider"')


def action_row() -> str:
    return between('id="editActionRow"', 'id="makeEditCard"')


def region() -> str:
    """The Edit choices region: the old card, from its opening
    tag to the end of the left column."""
    return between('id="makeEditCard"', 'id="paneDivider"')


def settings() -> str:
    """Inside Edit settings. v0.15 Batch I, item 35: the
    disclosure moved out of the card into the settings bar
    directly above the timeline band, so the slice now runs
    from its summary to the band that follows the bar."""
    return between('id="editSettings"', 'id="timelineBand"')


def left_css() -> str:
    """This change's CSS block, comment to the end of style."""
    start = STYLE.index("Left column (v0.12, change 3)")
    return STYLE[STYLE.rindex("/*", 0, start):]


# Every control the 0.11.1 card held, which must now live
# inside the Edit choices region with its id unchanged.
MOVED_IDS = (
    "editSettings",
    "fdRetakes", "fdSilence", "fdFillers",
    "fdBufferRange", "fdBufferNum", "fdKeepPills",
    "fdModePills", "fdStyle", "fdScope",
    "fdDirection", "fdDictate", "fdDictateStatus",
    "passList", "passRetakesBtn", "passSilenceBtn",
    "passFillersBtn",
    "formatRow", "btnV", "btnL", "fmtNote",
    "voiceTalkBtn", "voiceStatus", "voiceText",
    "fdCostLine", "fdRunSummary", "fdRunSummaryText",
    "moneyDetailsLink", "fdCostEstimate", "fdCostWhy",
    "fdStatus", "fdResults",
)


class ActionRowTests(unittest.TestCase):
    """F3: one row at the foot, exactly two controls in it."""

    def test_the_row_sits_between_the_video_and_the_choices(self):
        col = left()
        positions = [
            col.index('id="%s"' % name)
            for name in ("vidWrap", "editActionRow", "makeEditCard")
        ]
        self.assertEqual(positions, sorted(positions))

    def test_the_row_is_a_job_only_row_under_the_video(self):
        tag = re.search(r'<div[^>]*id="editActionRow"[^>]*>', TEMPLATE)
        self.assertIsNotNone(tag)
        self.assertIn('class="edit-action-row job-only"', tag.group(0))

    def test_exactly_two_controls_and_fd_fire_is_the_first(self):
        # v0.17 (feedback item 29): the row gained a third
        # control, the export download, which appears once the
        # finished video is built.
        buttons = re.findall(r'<button[^>]*>', action_row())
        self.assertEqual(len(buttons), 3)
        self.assertIn('id="fdFire"', buttons[0])
        self.assertIn('id="editChoicesBtn"', buttons[1])
        row = action_row()
        self.assertIn('id="exportBtn"', row)
        self.assertIn('href="/api/finish/video"', row)
        self.assertIn("Export the finished video", row)

    def test_make_the_edit_keeps_its_id_classes_and_words(self):
        tag = re.search(r'<button[^>]*id="fdFire"[^>]*>[^<]*</button>',
                        TEMPLATE)
        self.assertIsNotNone(tag)
        self.assertIn('class="primary hero-action"', tag.group(0))
        self.assertIn(">Make the edit</button>", tag.group(0))
        # Exactly one Make the edit button on the page, and it
        # is not inside the choices region any more.
        self.assertEqual(TEMPLATE.count('id="fdFire"'), 1)
        self.assertNotIn('id="fdFire"', region())

    def test_edit_choices_button_names_the_region_it_opens(self):
        tag = re.search(
            r'<button[^>]*id="editChoicesBtn"[^>]*>[^<]*</button>',
            TEMPLATE)
        self.assertIsNotNone(tag)
        self.assertIn('aria-expanded="false"', tag.group(0))
        self.assertIn('aria-controls="makeEditCard"', tag.group(0))
        self.assertIn(">Edit choices</button>", tag.group(0))

    def test_the_toggle_flips_the_region_and_its_own_state(self):
        wiring = between("var editChoicesBtn", "// -- front door")
        self.assertIn(
            'addEventListener("click"', wiring)
        self.assertIn("var open = editChoicesRegion.hidden;", wiring)
        self.assertIn("editChoicesRegion.hidden = !open;", wiring)
        self.assertIn(
            'editChoicesBtn.setAttribute("aria-expanded", '
            'open ? "true" : "false");',
            wiring,
        )
        # Opening and closing the choices calls no endpoint.
        self.assertNotIn("fetch(", wiring)
        self.assertNotIn("/api/", wiring)


class ChoicesRegionTests(unittest.TestCase):
    """F3: collapsed by default, holding everything the card held.

    v0.15 Batch I, item 35 supersedes the one home: Edit
    settings (the Remove family + involvement) moved into
    the settings bar above the timeline band; the region
    keeps the rest, and these tests follow the move."""

    def test_region_is_the_old_card_collapsed_by_default(self):
        tags = re.findall(r'<div[^>]*id="makeEditCard"[^>]*>', TEMPLATE)
        self.assertEqual(len(tags), 1)
        self.assertIn('class="card job-only"', tags[0])
        self.assertIn(" hidden", tags[0])
        # The hidden attribute wins: no rule in this change's
        # CSS can show the region while it is closed.
        self.assertIn("#makeEditCard[hidden] { display: none; }",
                      left_css())

    def test_every_moved_control_lives_inside_the_region(self):
        # v0.15 Batch I, item 35: the Edit settings family
        # moved out of the region into the settings bar above
        # the band; every other control the card held still
        # lives inside the region. Every id still appears
        # exactly once on the page.
        reg = region()
        inside = settings()
        settings_ids = (
            "editSettings", "fdRetakes", "fdSilence", "fdFillers",
            "fdBufferRange", "fdBufferNum", "fdKeepPills",
            "fdModePills", "fdStyle", "fdScope",
            "fdDirection", "fdDictate", "fdDictateStatus",
            "passList", "passRetakesBtn", "passSilenceBtn",
            "passFillersBtn",
        )
        for name in MOVED_IDS:
            home = inside if name in settings_ids else reg
            self.assertIn('id="%s"' % name, home, name)
            self.assertEqual(TEMPLATE.count('id="%s"' % name), 1, name)

    def test_the_region_keeps_the_cards_order(self):
        # The card's own order is unchanged (format, Talk,
        # costs, results); Edit settings now sits ahead of
        # the whole workspace's band in its bar (item 35).
        reg = region()
        positions = [
            reg.index('id="%s"' % name)
            for name in (
                "formatRow", "voiceTalkBtn",
                "fdCostLine", "fdResults",
            )
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(
            TEMPLATE.index('id="editSettingsBar"'),
            TEMPLATE.index('id="timelineBand"'),
        )

    def test_edit_settings_is_unchanged_inside(self):
        inside = settings()
        self.assertIn(
            '<details class="settings-details" id="editSettings">',
            between('id="editSettingsBar"', 'id="timelineBand"'),
        )
        self.assertIn("Edit settings</summary>", inside)
        self.assertIn(
            "These choices are saved as your usual choices", inside)
        # All seven tool rows still live inside the disclosure
        # (six, plus Smooth the joins from v0.15 Batch I).
        self.assertEqual(inside.count('class="tool-row"'), 7)
        for name in ("fdRetakes", "fdSilence", "fdFillers",
                     "fdDirection", "fdDictate", "passList",
                     "fdCrossfade"):
            self.assertIn('id="%s"' % name, inside, name)

    def test_the_tool_row_wiring_still_roots_at_the_region(self):
        # wireToolRows and the one-pass buttons look their rows
        # up inside makeEditCard; the region kept that id, so
        # both still find the rows after the move.
        self.assertIn(
            'wireToolRows(document.getElementById("makeEditCard"));',
            TEMPLATE,
        )
        self.assertIn(
            'var root = document.getElementById("makeEditCard");',
            TEMPLATE,
        )

    def test_make_the_edit_pipeline_is_the_same_handler(self):
        self.assertIn('fdFire.addEventListener("click"', TEMPLATE)
        self.assertIn('postJson("/api/front-door", {', TEMPLATE)
        self.assertIn("direction_text: fdDirection ? "
                      "fdDirection.value : \"\"", TEMPLATE)


class NoExplainerTests(unittest.TestCase):
    """F3: zero always-visible explainer text in the column."""

    def test_the_cards_intro_paragraph_is_gone_from_the_page(self):
        self.assertNotIn("Watch, set the choices", TEMPLATE)

    def test_its_one_fact_is_stated_inside_edit_settings(self):
        # The removed paragraph's only fact worth keeping: the
        # Remove choices live in Edit settings. It was not
        # stated inside the disclosure in 0.11.1, so the move
        # puts that one short line there, ahead of the rows.
        # v0.15 Batch I, items 33/35: the Remove choices gained
        # synced faces in the Editor panel and the timeline
        # row, so "exactly one place" stopped being true; the
        # line now states the one-setting fact instead.
        inside = settings()
        line = ("The Remove choices are one setting: set them "
                "here, in the Editor panel, or in the timeline "
                "row, and every place agrees.")
        self.assertIn(line, inside)
        self.assertLess(
            inside.index(line), inside.index('id="fdRetakes"'))
        # And it is stated exactly once on the whole page.
        self.assertEqual(TEMPLATE.count(line), 1)

    def test_the_duplicate_make_the_edit_heading_is_gone(self):
        # The action row's button already says Make the edit;
        # the region carries no second heading saying it again.
        self.assertNotIn("<h2>Make the edit</h2>", TEMPLATE)

    def test_nothing_but_hidden_status_lines_sit_above_the_row(self):
        above = left()[:left().index('id="editActionRow"')]
        notes = re.findall(r'<p[\s>][^>]*>', above)
        # The two watching lines are the only paragraphs up
        # there, and both start hidden.
        self.assertEqual(len(notes), 2)
        for tag in notes:
            self.assertIn("hidden", tag)
            self.assertTrue(
                'id="watchingLine"' in tag
                or 'id="watchingNote"' in tag,
                tag,
            )


class VideoPrimaryTests(unittest.TestCase):
    """F2/F3 fit, at source level: the video takes the height
    first, the action row is fixed at the foot, and with the
    choices closed the pane needs no scroll to reach it."""

    def test_the_column_is_a_flex_column_at_fitted_sizes(self):
        rule = between(
            "body.job-loaded .review-left {\n"
            "      display: flex;", "body.job-loaded .review-left .vid-wrap")
        self.assertIn("flex-direction: column;", rule)
        self.assertIn("@media (min-width: 720px)", left_css())

    def test_the_video_block_grows_to_take_the_height_first(self):
        rule = between(
            "body.job-loaded .review-left .vid-wrap {",
            "body.job-loaded .review-left #vid")
        self.assertIn("flex: 1 1 auto", rule)
        self.assertIn("min-height: 0", rule)

    def test_the_picture_is_sized_by_height_as_well_as_width(self):
        rule = between(
            "body.job-loaded .review-left #vid {",
            "body.job-loaded.expanded-video .review-left #vid")
        self.assertIn("width: 100%", rule)
        self.assertIn("height: 100%", rule)
        self.assertIn("max-height: 100%", rule)
        self.assertIn("object-fit: contain", rule)

    def test_the_action_row_does_not_flex(self):
        rule = between(
            "body.job-loaded .edit-action-row {",
            "body.job-loaded .review-left #makeEditCard")
        self.assertIn("flex: none", rule)
        # The choices region does not flex either: closed it
        # takes no space at all, open it takes its own height
        # and the pane's scroll (kept below) reaches it.
        self.assertIn(
            "body.job-loaded .review-left #makeEditCard "
            "{ flex: none; }",
            left_css(),
        )

    def test_the_pane_keeps_its_scroll_for_open_choices(self):
        self.assertIn(
            "body.job-loaded .review-left { overflow-y: auto; }",
            TEMPLATE,
        )

    def test_the_widened_and_drag_sized_caps_still_win(self):
        # Make wider: the widened picture keeps its window cap.
        self.assertIn(
            "body.job-loaded.expanded-video .review-left #vid {",
            left_css(),
        )
        self.assertIn(
            "max-height: calc(100dvh - 160px);", left_css())
        # Drag-sized row: its cap rule carries more weight
        # (three classes) than this change's height rule (two),
        # and the rule itself is untouched.
        self.assertIn(
            "body.job-loaded .review-split.row-sized #vid {\n"
            "      max-height: max(120px, "
            "calc(var(--pane-height) - 260px));",
            TEMPLATE,
        )


class PreservationTests(unittest.TestCase):
    """The video controls and the panel machinery stay."""

    def test_video_controls_stay_in_the_panel_head(self):
        head = between('id="videoPanelHead"', 'id="watchingLine"')
        for marker in (
            'id="videoExpandBtn"', 'title="Make wider"',
            'id="videoHideBtn"', 'title="Hide video"',
        ):
            self.assertIn(marker, head)
        # v0.15 Batch F, item 14b supersedes the v2 placement:
        # the swap control lives in the transcript head now and
        # trades the transcript and Editor sides.
        self.assertNotIn('id="swapSidesBtn"', head)
        tx_head = between('id="transcriptCard"', 'id="transcriptPanel"')
        self.assertIn('id="swapSidesBtn"', tx_head)
        self.assertIn('title="Swap transcript and Editor sides"',
                      tx_head)

    def test_resize_and_layout_memory_are_untouched(self):
        for marker in (
            "function wirePaneDivider",
            '"--pane-a", "tye-pane-a"',
            '"--pane-c", "tye-pane-c"',
            "function applyLayout()",
            '"tye-layout"',
            'id="paneDivider"', 'id="toolsDivider"',
        ):
            self.assertIn(marker, TEMPLATE)


class FrontDoorTests(unittest.TestCase):
    """With no job loaded the front door is unchanged: both
    new pieces are job-only, so the no-job rule keeps them
    (and the whole column) out of sight exactly as before."""

    def test_both_new_pieces_are_job_only(self):
        for name in ("editActionRow", "makeEditCard"):
            tag = re.search(
                r'<div[^>]*id="%s"[^>]*>' % name, TEMPLATE)
            self.assertIsNotNone(tag, name)
            self.assertIn("job-only", tag.group(0), name)
        self.assertIn(
            "body.no-job .job-only { display: none !important; }",
            TEMPLATE,
        )

    def test_the_front_door_pieces_are_still_on_the_page(self):
        for marker in (
            'id="dropArea"', 'id="loadSample"', 'id="checkSignin"',
            'id="directionHero"', 'id="firstEditBtn"',
        ):
            self.assertIn(marker, TEMPLATE)


class ServedFrontDoorTests(unittest.TestCase):
    """The served no-job page renders the front door with the
    new pieces present in the markup and held out of sight by
    the same job-only rule as every other review piece."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v12-left-"))
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

    def test_served_front_door_is_unchanged(self):
        with urllib.request.urlopen(self.base + "/", timeout=30) as resp:
            self.assertEqual(resp.status, 200)
            page = resp.read().decode("utf-8")
        self.assertIn('id="dropArea"', page)
        self.assertIn("var HAS_JOB = false;", page)
        self.assertIn("No job is loaded yet", page)
        for marker in (
            'id="editActionRow"', 'id="editChoicesBtn"',
            'id="makeEditCard" hidden',
            "body.no-job .job-only { display: none !important; }",
        ):
            self.assertIn(marker, page)
        self.assertNotIn("Watch, set the choices", page)


if __name__ == "__main__":
    unittest.main()
