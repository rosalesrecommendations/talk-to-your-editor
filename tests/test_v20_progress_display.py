"""Tests for v0.20 Chunk C: one steady clock, an honest
estimate, and a progress overlay that never clips the page.

From Justin's 2026-11-01 Mac run of 0.19 (friction log,
finding 5):

- The elapsed timer did not count steadily: "it jumps,
  falls back to 2 seconds, sits there until the next jump,
  then counts again." The cause was in the page: every poll
  re-based the clock from the server's elapsed figure, and
  that figure only changes when a stage changes, so between
  stages the count kept snapping back. Now the run has ONE
  clock, anchored once when the run starts, rendered from
  the wall clock (Date.now() minus the anchor) by a single
  one-second interval. Polls update the stage words and the
  step count; they never move the anchor.
- The working display showed counts with no estimated time.
  Long steps now show the app's own measured usual length
  beside the elapsed time, in plain words ("usually about
  2 minutes"), from the finished first edits recorded on
  this computer (webapp's first_edit_eta). With too little
  history the field is empty and the line shows the elapsed
  time alone. No figure is worked out from the pace of the
  run in progress, and the page still never reads a
  remaining-time field (v0.17 stands).
- Page words were clipped at the edges behind the progress
  overlay ("rd gaps", "ed do you want to be?"). The overlay
  now sits inside the viewport with margins on every side,
  its card can never be wider than the space inside those
  margins, and while a step runs the page carries a
  run-active guard that holds it to the window's width.
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

from talk_to_your_editor import webapp  # noqa: E402
from talk_to_your_editor.webapp import (  # noqa: E402
    WebAppConfig, create_server,
)

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")
SOURCE = (
    ROOT / "src" / "talk_to_your_editor" / "webapp.py"
).read_text(encoding="utf-8")


def between(start: str, end: str, text: str = TEMPLATE) -> str:
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


class OneSteadyClockTests(unittest.TestCase):
    def test_one_interval_drives_the_elapsed_render(self):
        self.assertEqual(
            TEMPLATE.count(
                "setInterval(renderFirstEditProgressDetail, 1000)"),
            1,
        )

    def test_the_render_reads_the_wall_clock_from_one_anchor(self):
        body = between(
            "function renderFirstEditProgressDetail",
            "function showFirstEditProgress",
        )
        self.assertIn("Date.now() - firstEditClock.startAt", body)
        self.assertIn('fmtClock(elapsed) + " so far"', body)

    def test_no_rebase_state_survives(self):
        # The old shape re-based on every poll from the server
        # figure; both of its pieces are gone.
        self.assertNotIn("elapsedBase", TEMPLATE)
        self.assertNotIn("firstEditProgressState", TEMPLATE)

    def test_the_anchor_is_set_once_behind_a_guard(self):
        body = between(
            "function showFirstEditProgress",
            "function stopFirstEditTick",
        )
        self.assertIn("if (!firstEditClock)", body)
        self.assertLess(
            body.index("if (!firstEditClock)"),
            body.index("firstEditClock = {"),
        )
        # The anchor comes from the server's measured elapsed
        # time at that first sighting, and from nowhere else.
        self.assertIn(
            "Date.now() -\n            (Number(progress.elapsed_s) || 0) * 1000",
            body,
        )

    def test_hiding_the_progress_clears_the_clock(self):
        body = between(
            "function hideFirstEditProgress",
            "function pollFirstEdit",
        )
        self.assertIn("firstEditClock = null;", body)


class EstimateWiringTests(unittest.TestCase):
    def test_the_page_reads_the_eta_field(self):
        body = between(
            "function showFirstEditProgress",
            "function stopFirstEditTick",
        )
        self.assertIn("progress.first_edit_eta", body)

    def test_the_render_appends_the_eta_when_there_is_one(self):
        body = between(
            "function renderFirstEditProgressDetail",
            "function showFirstEditProgress",
        )
        self.assertIn("firstEditClock.eta", body)
        self.assertIn('" · " + firstEditClock.eta', body)

    def test_no_remaining_time_field_is_read_anywhere(self):
        # v0.17 stands: the estimate is a usual length known
        # before the run starts, never a live remaining figure.
        self.assertNotIn("remaining_s", TEMPLATE)

    def test_the_server_state_carries_the_eta(self):
        self.assertIn('"first_edit_eta": _first_edit_eta_text(', SOURCE)
        self.assertIn("def _first_edit_eta_text(", SOURCE)


class EtaHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v20-c-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_history_says_nothing(self):
        self.assertIsNone(webapp._first_edit_eta_text(self.tmp))
        self.assertIsNone(webapp._first_edit_eta_text(None))

    def test_one_run_is_not_a_usually(self):
        webapp._record_first_edit_run(self.tmp, 120.0)
        self.assertIsNone(webapp._first_edit_eta_text(self.tmp))

    def test_two_runs_give_plain_words(self):
        webapp._record_first_edit_run(self.tmp, 110.0)
        webapp._record_first_edit_run(self.tmp, 130.0)
        self.assertEqual(
            webapp._first_edit_eta_text(self.tmp),
            "usually about 2 minutes",
        )

    def test_short_runs_read_under_a_minute_or_one_minute(self):
        webapp._record_first_edit_run(self.tmp, 10.0)
        webapp._record_first_edit_run(self.tmp, 20.0)
        self.assertEqual(
            webapp._first_edit_eta_text(self.tmp),
            "usually under a minute",
        )
        other = Path(tempfile.mkdtemp(prefix="tye-v20-c-"))
        try:
            webapp._record_first_edit_run(other, 50.0)
            webapp._record_first_edit_run(other, 70.0)
            self.assertEqual(
                webapp._first_edit_eta_text(other),
                "usually about 1 minute",
            )
        finally:
            shutil.rmtree(other, ignore_errors=True)

    def test_the_history_keeps_only_the_last_few(self):
        for _ in range(12):
            webapp._record_first_edit_run(self.tmp, 300.0)
        runs = json.loads(
            (self.tmp / webapp.FIRST_EDIT_RUNS_NAME).read_text(
                encoding="utf-8")
        )
        self.assertEqual(len(runs), webapp.FIRST_EDIT_RUNS_KEPT)

    def test_recording_never_raises(self):
        webapp._record_first_edit_run(None, 10.0)
        webapp._record_first_edit_run(self.tmp, 0.0)
        webapp._record_first_edit_run(self.tmp, -5.0)
        self.assertIsNone(webapp._first_edit_eta_text(self.tmp))


class ServedStatusCarriesEtaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v20-c-"))
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

    def test_a_started_runs_status_carries_the_eta_key(self):
        status, begun = self.request(
            "/api/intake/begin", data={"name": "eta-check"})
        self.assertEqual(status, 200, begun)
        intake_id = json.loads(begun.decode("utf-8"))["intake_id"]
        dummy = bytes(range(256)) * 16
        url = ("/api/intake/file?intake=" + quote(intake_id)
               + "&path=" + quote("clip.mp4"))
        status, body = self.request(url, raw=dummy)
        self.assertEqual(status, 200, body)
        status, finished = self.request(
            "/api/intake/finish", data={"intake_id": intake_id})
        self.assertEqual(status, 200, finished)
        status, started = self.request(
            "/api/intake/first-edit", data={"intake_id": intake_id})
        self.assertEqual(status, 200, started)
        status, polled = self.request(
            "/api/intake/status?intake=" + quote(intake_id))
        self.assertEqual(status, 200, polled)
        payload = json.loads(polled.decode("utf-8"))
        # No runs are recorded on this fresh launch home, so
        # the honest value is null, and the page will show the
        # elapsed time alone.
        self.assertIn("first_edit_eta", payload)
        self.assertIsNone(payload["first_edit_eta"])


class OverlayNeverClipsTests(unittest.TestCase):
    def test_the_overlay_sits_inside_the_viewport_with_margins(self):
        css = between("#runOverlay {", "#runOverlay[hidden]")
        self.assertIn("box-sizing: border-box;", css)
        self.assertIn("padding: 20px;", css)

    def test_the_card_can_never_be_wider_than_its_space(self):
        css = between(".run-overlay-card {", ".run-spinner {")
        self.assertIn("box-sizing: border-box;", css)
        self.assertIn("max-width: 100%;", css)
        self.assertIn("min-width: min(260px, 100%);", css)

    def test_overlay_words_wrap_instead_of_running_off(self):
        self.assertIn(
            ".run-stage { font-size: 17px; font-weight: 700;"
            " margin: 0 0 6px; overflow-wrap: break-word; }",
            TEMPLATE,
        )
        self.assertIn(
            ".run-meta { color: var(--muted); font-size: 14px;"
            " margin: 0; overflow-wrap: break-word; }",
            TEMPLATE,
        )

    def test_while_a_step_runs_the_page_is_held_to_the_window(self):
        self.assertIn("body.run-active { overflow-x: clip;", TEMPLATE)
        show = between(
            "function showFirstEditProgress",
            "function stopFirstEditTick",
        )
        self.assertIn(
            'document.body.classList.add("run-active");', show)
        hide = between(
            "function hideFirstEditProgress",
            "function pollFirstEdit",
        )
        self.assertIn(
            'document.body.classList.remove("run-active");', hide)

    def test_the_in_card_progress_line_wraps(self):
        self.assertIn(
            "#firstEditProgressDetail { overflow-wrap: break-word; }",
            TEMPLATE,
        )


if __name__ == "__main__":
    unittest.main()
