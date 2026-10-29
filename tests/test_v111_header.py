"""v0.11.1 header consolidation: the three bars are gone.

Item 1 from Justin's Oct 28, 2026 evening test of 0.11
(v0111-work/FIX-ITEMS.md): the review screen stacked three
full-width bars between the header and the workspace, the
test-video toggle bar, the "Job loaded" bar, and the teaching
bar. Fixed looks like:

- No bars between the header and the workspace. The workspace
  starts right under the header and gets the recovered height.
- Bring in another job is a plus (+) button in the header row,
  named in plain words ("Bring in another job" as its label and
  its hover text). It opens the same bring-in card the old bar
  button opened; the card itself, with its own test-video
  switch, is unchanged.
- The test-video switch lives in the Settings panel now, with
  the same words and the same wiring: it keeps its id, it is
  set from the job summary, and changing it posts to the same
  /api/test-video endpoint the bring-in card switch uses.
- Teach the app is a button in the header row. It opens the
  same teaching choices (#teachFull) the old bar button opened,
  with the same open and close behavior and aria attributes.
- The "Job loaded. The review below belongs to this job."
  sentence is gone. The header still centers the job name, and
  that stays the one place the job is named.
- The front door (no job loaded) works as before: the new
  header buttons and the Settings test-video switch are
  job-only, and the bring-in and teach cards keep their full
  front door shape there.

These tests read the page template the same way the v0.11
layout tests do, plus served page and endpoint checks over
HTTP. Everything is offline.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.webapp import WebAppConfig, create_server  # noqa: E402

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")

SWITCH_TEXT = "This is a test video. The app will not learn from it."


def between(start_marker: str, end_marker: str) -> str:
    start = TEMPLATE.index(start_marker)
    end = TEMPLATE.index(end_marker, start)
    return TEMPLATE[start:end]


def header() -> str:
    return between('<header class="app-head">', "</header>")


# ---------------------------------------------------------------------------
# The bars are gone
# ---------------------------------------------------------------------------


class NoBarsTests(unittest.TestCase):
    def test_no_slim_bars_in_the_template(self):
        for marker in (
            'id="dropSlimBar"', 'id="teachSlimBar"',
            'id="dropSlimToggle"', 'id="testVideoCard"',
            "drop-slim-text", "teach-slim-text",
            "#dropSlimBar", "#teachSlimBar",
        ):
            self.assertNotIn(marker, TEMPLATE)

    def test_the_bar_sentences_are_gone(self):
        self.assertNotIn(
            "Job loaded. The review below belongs to this job.",
            TEMPLATE,
        )
        self.assertNotIn(
            "Teaching is tucked away while you review.", TEMPLATE
        )

    def test_cards_stay_out_of_sight_in_review_until_opened(self):
        # With a job loaded, neither card takes any space above
        # the workspace until its header button opens it.
        self.assertIn(
            "body.job-loaded #dropCard { display: none; }", TEMPLATE)
        self.assertIn(
            "body.job-loaded #dropCard.expanded "
            "{ display: block;", TEMPLATE)
        self.assertIn(
            "body.job-loaded #teachCard { display: none; }", TEMPLATE)
        self.assertIn(
            "body.job-loaded #teachCard.expanded "
            "{ display: block;", TEMPLATE)

    def test_header_actions_wrap_on_a_narrow_window(self):
        # The header gained two buttons this round (the plus and
        # Teach the app). Measured in the 0.11.1 fit proof: at
        # 640px wide the unwrapped actions row ran to 691px and
        # pushed the page sideways. The actions must be allowed
        # to wrap so the header fits every proof size.
        self.assertIn(
            ".topbar-actions { display: flex; gap: 8px; "
            "align-items: center; flex: 0 1 auto; flex-wrap: wrap; "
            "justify-content: flex-end; }",
            TEMPLATE,
        )

    def test_header_still_centers_the_job_name(self):
        self.assertIn('id="topJobName"', header())


# ---------------------------------------------------------------------------
# Bring in another job: the plus button in the header row
# ---------------------------------------------------------------------------


class BringInButtonTests(unittest.TestCase):
    def test_plus_button_sits_in_the_header_with_its_plain_name(self):
        head = header()
        self.assertIn('id="bringInBtn"', head)
        self.assertIn('aria-label="Bring in another job"', head)
        self.assertIn('title="Bring in another job"', head)
        self.assertIn('aria-expanded="false"', head)
        self.assertIn('aria-controls="dropFull"', head)
        # The visible face of the button is a plus sign.
        btn = between('id="bringInBtn"', "</button>")
        self.assertTrue(btn.rstrip().endswith(">+"))

    def test_plus_button_is_a_review_only_control(self):
        btn = between('id="bringInBtn"', "</button>")
        self.assertIn("job-only", btn)

    def test_plus_button_opens_the_bring_in_card(self):
        self.assertIn(
            'var bringInBtn = document.getElementById("bringInBtn");',
            TEMPLATE,
        )
        self.assertIn('bringInBtn.addEventListener("click"', TEMPLATE)
        self.assertIn('dropCardEl.classList.toggle("expanded")', TEMPLATE)
        self.assertIn(
            'bringInBtn.setAttribute("aria-expanded"', TEMPLATE)
        self.assertIn('"Bring in another job"', TEMPLATE)
        self.assertIn('"Hide the bring-in card"', TEMPLATE)

    def test_bring_in_card_itself_is_unchanged(self):
        card = between('id="dropCard"', 'id="teachCard"')
        for marker in (
            'id="dropFull"', 'id="dropArea"', 'id="dropPickFolder"',
            'id="dropPickFiles"', 'id="dropTestVideo"',
            'id="loadSample"',
        ):
            self.assertIn(marker, card)
        # Its own test-video switch keeps the plain words.
        self.assertIn(SWITCH_TEXT, card)


# ---------------------------------------------------------------------------
# The test-video switch: inside Settings, same words, same wiring
# ---------------------------------------------------------------------------


class SettingsTestVideoTests(unittest.TestCase):
    def test_settings_panel_carries_the_switch(self):
        panel = between('id="settingsPanel"', 'id="directionCard"')
        self.assertIn('id="testVideoBlock"', panel)
        self.assertIn('id="jobTestVideo"', panel)
        self.assertIn(SWITCH_TEXT, panel)

    def test_the_switch_exists_once_and_the_wording_twice(self):
        # Once in Settings (the moved switch), and the wording
        # once more on the bring-in card's own switch.
        self.assertEqual(TEMPLATE.count('id="jobTestVideo"'), 1)
        self.assertEqual(TEMPLATE.count('id="dropTestVideo"'), 1)
        self.assertEqual(TEMPLATE.count(SWITCH_TEXT), 2)

    def test_settings_switch_is_job_only_like_the_old_bar(self):
        block = between('id="testVideoBlock"', 'id="savedTranscriptsBlock"')
        self.assertIn("job-only", block)
        self.assertIn("body.no-job .job-only", TEMPLATE)

    def test_the_wiring_is_the_one_the_old_switch_had(self):
        # Set from the job summary when a job state lands.
        self.assertIn(
            'var jobTestVideo = document.getElementById("jobTestVideo");',
            TEMPLATE,
        )
        self.assertIn("jobTestVideo.checked = !!job.test_video", TEMPLATE)
        # Kept in step with the bring-in card switch.
        self.assertIn('["dropTestVideo", "jobTestVideo"]', TEMPLATE)
        # Changing it posts to the same endpoint as before.
        self.assertIn('wireTestVideoSwitch("jobTestVideo")', TEMPLATE)
        self.assertIn('wireTestVideoSwitch("dropTestVideo")', TEMPLATE)
        self.assertIn('postJson("/api/test-video", { test: on })', TEMPLATE)
        # The banner that follows the mark is still on the page.
        self.assertIn('id="testVideoBanner"', TEMPLATE)
        self.assertIn(
            "Test video: the app will not learn from this one.", TEMPLATE)


# ---------------------------------------------------------------------------
# Teach the app: a header button opening the same teaching choices
# ---------------------------------------------------------------------------


class TeachHeaderButtonTests(unittest.TestCase):
    def test_teach_button_sits_in_the_header(self):
        head = header()
        self.assertIn('id="teachToggle"', head)
        self.assertIn(">Teach the app<", head)
        self.assertIn('aria-expanded="false"', head)
        self.assertIn('aria-controls="teachFull"', head)
        btn = between('id="teachToggle"', "</button>")
        self.assertIn("job-only", btn)

    def test_teach_button_opens_the_same_teaching_choices(self):
        self.assertIn(
            'var teachToggle = document.getElementById("teachToggle");',
            TEMPLATE,
        )
        self.assertIn('teachCardEl.classList.toggle("expanded")', TEMPLATE)
        self.assertIn('teachToggle.setAttribute("aria-expanded"', TEMPLATE)
        self.assertIn('"Hide teaching choices"', TEMPLATE)
        full = between('id="teachFull"', 'id="novid"')
        for marker in (
            'id="teachRecording"', 'id="teachRaw"', 'id="teachFinished"',
            'id="teachLink"', 'id="teachRun"', 'id="teachStatus"',
        ):
            self.assertIn(marker, full)

    def test_front_door_cards_keep_their_full_shape(self):
        # No rule hides the bring-in or teach card when no job is
        # loaded; the front door is untouched.
        self.assertNotIn("body.no-job #dropCard", TEMPLATE)
        self.assertNotIn("body.no-job #teachCard", TEMPLATE)


# ---------------------------------------------------------------------------
# Served page and the endpoint the switches drive
# ---------------------------------------------------------------------------


class ServedPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v111-header-"))
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

    def call(self, path, data=None):
        payload = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(
            self.base + path,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def test_served_front_door_has_the_new_shape(self):
        status, page = self.call("/")
        self.assertEqual(status, 200)
        for marker in (
            'id="bringInBtn"', 'aria-label="Bring in another job"',
            'id="teachToggle"', ">Teach the app<",
            'id="jobTestVideo"', 'id="dropTestVideo"', SWITCH_TEXT,
            'id="teachFull"', 'id="dropFull"', 'id="topJobName"',
        ):
            self.assertIn(marker, page)
        for gone in (
            'id="dropSlimBar"', 'id="teachSlimBar"',
            'id="testVideoCard"',
            "Job loaded. The review below belongs to this job.",
            "Teaching is tucked away while you review.",
        ):
            self.assertNotIn(gone, page)

    def test_test_video_endpoint_behaves_as_before(self):
        status, body = self.call("/api/job")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["test_video"])
        status, body = self.call("/api/test-video", {"test": True})
        self.assertEqual(status, 200, body)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["test_video"])
        # With no job loaded the summary still reports no test
        # video, exactly as before (the launch mark itself is
        # what carries into a dropped job; test_v10_testvideo
        # pins that path, and the on-the-job summary path).
        status, body = self.call("/api/job")
        self.assertFalse(json.loads(body)["test_video"])
        status, body = self.call("/api/test-video", {"test": False})
        self.assertEqual(status, 200, body)
        self.assertFalse(json.loads(body)["test_video"])


if __name__ == "__main__":
    unittest.main()
