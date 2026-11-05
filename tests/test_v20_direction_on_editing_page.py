"""Tests for v0.20 Chunk B: one bring-in path, and direction
on the editing page.

From Justin's 2026-11-01 Mac run of 0.19 (friction log,
findings 3 and 4):

3. The start screen stacked two "Bring in a video" sections
   (the drop card, then the bring-in pop-up's own heading)
   plus three empty Video/Transcript/Editor placeholder
   panels. Now there is exactly one intake surface and one
   "Bring in a video" heading before a job exists, and the
   empty placeholder panels are gone.
4. The direction surface (the Requests for your editor box,
   the Remove retakes / Word gaps / Remove filler words
   switches, the involvement and usual-way controls, and
   Make the edit) lived inside the bring-in dialog, crushed
   into a narrow column that overlapped the Remove panel.
   It now lives on the page, ahead of the workspace, as its
   own card; the dialog keeps only what intake needs (the
   parts list and its order controls, Add more videos,
   Bring these in, the test-video switch) plus one plain
   note saying direction is set on the editing page. The
   first-edit flow is unchanged underneath: same routes,
   same payloads; showing the surface now closes the pop-up
   so the direction is set on the page.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.webapp import WebAppConfig, create_server  # noqa: E402

PAGE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")


def between(start: str, end: str, text: str = PAGE) -> str:
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


DIRECTION_IDS = (
    'id="intakeDirection"', 'id="inRetakes"', 'id="inSilence"',
    'id="inFillers"', 'id="firstEditBtn"',
)


class OneBringInPathTests(unittest.TestCase):
    def test_exactly_one_bring_in_heading_in_the_template(self):
        self.assertEqual(PAGE.count("<h2>Bring in a video</h2>"), 1)

    def test_the_one_heading_is_the_drop_cards(self):
        card = between('id="dropCard"', 'id="homeChatCard"')
        self.assertIn("<h2>Bring in a video</h2>", card)
        self.assertIn('id="dropArea"', card)

    def test_no_empty_placeholder_panels_anywhere(self):
        for marker in ('id="editorBlank"', 'id="blankVideoPane"',
                       'id="blankTranscriptPane"',
                       'id="blankCommentsPane"', 'id="blankPickBtn"',
                       'id="blankTxLoading"', "blank-pane"):
            self.assertNotIn(marker, PAGE)
        self.assertNotIn(
            "Drop a video here to start. Your video, its words, "
            "and the editor open together.", PAGE)
        self.assertNotIn(
            "Your conversation with the editor will live here.",
            PAGE)

    def test_no_blank_editor_css_left(self):
        self.assertNotIn("#editorBlank", PAGE)
        self.assertNotIn("body.no-job #editorBlank", PAGE)


class DialogKeepsOnlyIntakeTests(unittest.TestCase):
    def test_the_drop_card_holds_no_direction_surface(self):
        card = between('id="dropCard"', 'id="homeChatCard"')
        for marker in DIRECTION_IDS:
            self.assertNotIn(marker, card)
        self.assertNotIn("Requests for your editor", card)

    def test_the_popup_holds_only_what_intake_needs(self):
        popup = between('id="intakePopup"', 'id="dropFolderInput"')
        self.assertIn('id="intakeParts"', popup)
        self.assertIn('id="intakeAddMoreBtn"', popup)
        self.assertIn('id="intakeContinueBtn"', popup)
        self.assertIn('id="intakeCloseBtn"', popup)
        self.assertNotIn("<h2>Bring in a video</h2>", popup)
        for marker in DIRECTION_IDS:
            self.assertNotIn(marker, popup)

    def test_the_popup_says_where_direction_lives(self):
        popup = between('id="intakePopup"', 'id="dropFolderInput"')
        self.assertIn(
            "Direction for the edit is set on the editing page, "
            "once these are in.", popup)

    def test_the_test_video_switch_stays_with_intake(self):
        card = between('id="dropCard"', 'id="homeChatCard"')
        self.assertIn('id="dropTestVideo"', card)


class DirectionOnThePageTests(unittest.TestCase):
    def test_the_block_is_a_card_of_its_own_on_the_page(self):
        self.assertIn(
            '<div class="card" id="firstEditBlock" hidden>', PAGE)
        # After the drop card, ahead of the workspace.
        self.assertLess(
            PAGE.index('id="dropCard"'),
            PAGE.index('id="firstEditBlock"'))
        self.assertLess(
            PAGE.index('id="firstEditBlock"'),
            PAGE.index('id="reviewSplit"'))

    def test_the_block_carries_the_whole_direction_surface(self):
        block = between('id="firstEditBlock"', 'id="reviewSplit"')
        self.assertIn("Requests for your editor", block)
        for marker in DIRECTION_IDS:
            self.assertIn(marker, block)
        self.assertIn("Remove retakes", block)
        self.assertIn("Word gaps (silence)", block)
        self.assertIn("Remove filler words", block)
        self.assertIn("How involved do you want to be?", block)
        self.assertIn('id="inModePills"', block)
        self.assertIn("Your usual way", block)
        self.assertIn('id="inStyle"', block)
        self.assertIn(">Make the edit</button>", block)

    def test_the_layout_is_a_wide_grid_not_a_crushed_column(self):
        # On a wide screen the hero and the Remove tools sit
        # side by side in the block's own grid (the rule the
        # v0.9 layout tests pin), and nothing in the pop-up's
        # old two-column grid mentions the block anymore.
        self.assertIn('#firstEditBlock:not([hidden])', PAGE)
        self.assertIn('grid-template-areas: "hero tools"', PAGE)
        self.assertNotIn("body.intake-open #firstEditBlock", PAGE)
        self.assertNotIn("body.intake-open #inRemoveGroup", PAGE)

    def test_showing_the_surface_closes_the_popup(self):
        shown = between(
            'data.kind === "video" && data.video_url',
            'if (data.playback && data.playback !== "ready")')
        self.assertIn("closeIntakePopup();", shown)
        self.assertLess(
            shown.index("closeIntakePopup();"),
            shown.index("firstEditBlock.hidden = false;"))

    def test_the_first_edit_payload_is_unchanged(self):
        body = between("if (firstEditBtn) {", "var transcribeBtn = ")
        self.assertIn("startFirstEdit(", body)
        self.assertIn("collectIntakeSettings()", body)
        i = PAGE.index('postJson("/api/intake/first-edit"')
        payload = PAGE[i:i + 300]
        self.assertIn("intake_id: currentIntakeId", payload)
        self.assertIn('direction: directionText || ""', payload)
        self.assertIn("settings: settings", payload)


class ServedStartPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v20-b-"))
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

    def request(self, path, data=None, raw=None):
        payload = raw
        headers = {}
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path, data=payload, headers=headers,
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_served_start_page_has_one_heading_and_no_panels(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertEqual(text.count("<h2>Bring in a video</h2>"), 1)
        self.assertNotIn('id="editorBlank"', text)
        self.assertNotIn("blank-pane", text)
        # The direction surface is in the served page, in the
        # block that lives outside the drop card.
        self.assertIn('id="firstEditBlock"', text)
        self.assertIn('id="intakeDirection"', text)
        self.assertIn('id="firstEditBtn"', text)
        self.assertLess(
            text.index('id="homeChatCard"'),
            text.index('id="firstEditBlock"'))

    def test_a_lone_video_still_reaches_the_first_edit(self):
        # The server half of the flow, end to end with the
        # same calls the page makes: begin, file, finish. A
        # lone video answers that the first edit can be made,
        # which is what shows the direction surface on the
        # page.
        status, begun = self.request(
            "/api/intake/begin", data={"name": "one-video"})
        self.assertEqual(status, 200, begun)
        begun = json.loads(begun.decode("utf-8"))
        intake_id = begun["intake_id"]
        dummy = bytes(range(256)) * 16
        url = ("/api/intake/file?intake=" + quote(intake_id)
               + "&path=" + quote("clip.mp4"))
        status, body = self.request(url, raw=dummy)
        self.assertEqual(status, 200, body)
        status, finished = self.request(
            "/api/intake/finish", data={"intake_id": intake_id})
        self.assertEqual(status, 200, finished)
        finished = json.loads(finished.decode("utf-8"))
        self.assertEqual(finished["kind"], "video")
        self.assertTrue(finished["can_first_edit"])


if __name__ == "__main__":
    unittest.main()
