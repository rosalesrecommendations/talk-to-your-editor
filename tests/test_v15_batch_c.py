"""v0.15 Batch C: the first page and intake (items 1, 2, 3, 4,
8, 10, 11, 12).

The first page keeps only the app name and one job: bring in a
video. Dropping or picking files opens the editor page (blank
until a job is processed) with the intake pop-up as a floating
layer over it; the pop-up lists the drop's files as parts of
one job, reorderable by drag and by up/down buttons, and the
recorded order is the processing order end to end (the server
records the upload order, joins several videos in that order,
and the first edit processes the joined file). The stages
list, the cost detail, and the test-video explanation live
behind the How this works door. Settings is short labeled
rows. Processing shows a bar (real stages for a first edit,
indeterminate for the synchronous front-door run), a blue
spinner over the dimmed video, and flashing dots in the
transcript area.
"""

from __future__ import annotations

import json
import shutil
import subprocess
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

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")


def between(start_marker: str, end_marker: str) -> str:
    start = TEMPLATE.index(start_marker)
    end = TEMPLATE.index(end_marker, start)
    return TEMPLATE[start:end]


class FirstPageTests(unittest.TestCase):
    def test_first_page_view_is_only_the_one_job(self):
        view = between('id="dropCard"', 'id="intakePopup"')
        self.assertIn('id="dropArea"', view)
        self.assertIn("Bring in a video, or a job folder.", view)
        # No stages list, no cost paragraph, no test-video
        # explanation in the main view.
        self.assertNotIn('id="stagedFlow"', view)
        self.assertNotIn('id="firstEditCost"', view)
        self.assertNotIn('id="dropTestVideo"', view)
        self.assertNotIn("One video: it plays here", view)

    def test_detail_lives_behind_how_this_works_not_deleted(self):
        how = between('id="howItWorksPanel"', 'id="settingsPanel"')
        self.assertIn('id="stagedFlow"', how)
        self.assertIn("The stages, in order", how)
        self.assertIn('id="firstEditCost"', how)
        self.assertIn('id="firstEditCostWhy"', how)
        self.assertIn("no Terminal", how)
        self.assertIn("does not learn from a test video", how)

    def test_the_drop_opens_the_editor_with_the_popup_layer(self):
        self.assertIn('id="intakePopup"', TEMPLATE)
        # v0.20 chunk B removed the blank editor panes at
        # Justin's direction (one bring-in path, no empty
        # placeholder panels); their absence is asserted in
        # tests/test_v20_direction_on_editing_page.py.
        self.assertIn('id="intakeCloseBtn"', TEMPLATE)
        self.assertIn("function openIntakePopup()", TEMPLATE)
        self.assertIn("function closeIntakePopup()", TEMPLATE)
        self.assertIn('document.body.classList.add("intake-open")',
                      TEMPLATE)
        # The pop-up is a floating layer at card radius 16.
        self.assertIn("body.intake-open #dropCard", TEMPLATE)
        self.assertIn("position: fixed", between(
            "body.intake-open #dropCard", ".intake-popup-head"))
        self.assertIn("border-radius: 16px", between(
            "body.intake-open #dropCard", ".intake-popup-head"))
        # The first page alone does not show the teaching card.
        self.assertIn("body.no-job #teachCard { display: none; }",
                      TEMPLATE)


class PartsTests(unittest.TestCase):
    def test_parts_list_and_both_reorder_paths_exist(self):
        self.assertIn('id="intakeParts"', TEMPLATE)
        self.assertIn('id="intakeContinueBtn"', TEMPLATE)
        self.assertIn("function stageDrop(files)", TEMPLATE)
        self.assertIn("function renderIntakeParts()", TEMPLATE)
        self.assertIn("function moveIntakePart(index, delta)",
                      TEMPLATE)
        # The non-drag path: up and down buttons per part.
        self.assertIn('"Move this part up"', TEMPLATE)
        self.assertIn('"Move this part down"', TEMPLATE)
        # The drag path.
        self.assertIn('addEventListener("dragstart"', TEMPLATE)
        self.assertIn('addEventListener("drop"', TEMPLATE)
        self.assertIn("li.draggable = true;", TEMPLATE)

    def test_every_arrival_stages_instead_of_uploading(self):
        self.assertIn("collectDrop(e.dataTransfer).then(stageDrop)",
                      TEMPLATE)
        # Both file inputs stage their files the same way.
        self.assertEqual(TEMPLATE.count("stageDrop(files);"), 2)
        cont = between("intakeContinueBtn.addEventListener",
                       "// -- processing states")
        self.assertIn("sendDrop(pendingParts.slice())", cont)

    def test_the_server_records_and_reports_the_order(self):
        self.assertIn('record.setdefault("files", []).append',
                      (ROOT / "src" / "talk_to_your_editor"
                       / "webapp.py").read_text(encoding="utf-8"))
        self.assertIn("intakePartsEl.dataset.sentOrder", TEMPLATE)
        self.assertIn('data.parts.join("|")', TEMPLATE)


class NamingTests(unittest.TestCase):
    def test_make_the_edit_is_the_one_name(self):
        self.assertNotIn("Make the first edit", TEMPLATE)
        self.assertIn(
            '<button id="firstEditBtn" class="primary hero-action" '
            'type="button">Make the edit</button>', TEMPLATE)
        self.assertIn(
            '<button id="fdFire" class="primary hero-action" '
            'type="button">Make the edit</button>', TEMPLATE)

    def test_your_usual_way_replaces_editing_style(self):
        self.assertNotIn("Editing style", TEMPLATE)
        self.assertEqual(TEMPLATE.count("Your usual way"), 5)
        for marker in ('for="inStyle">Your usual way',
                       'for="fdStyle">Your usual way'):
            self.assertIn(marker, TEMPLATE)
        # The Change door is the row's own open button.
        row = between('data-tool="style"', 'id="inStyle"')
        self.assertIn(">Change</button>", row)


class HeaderTests(unittest.TestCase):
    def test_settings_button_says_settings_on_the_header_line(self):
        tag = between('id="settingsToggle"', "</button>")
        self.assertIn('<span class="settings-word">Settings</span>',
                      tag)
        # No dislodging top margin; the head centers its row.
        self.assertNotIn(".settings-btn { flex: none; margin-top:",
                         TEMPLATE)
        self.assertIn(
            ".app-head { display: flex; justify-content: "
            "space-between; align-items: center; gap: 16px; }",
            TEMPLATE)
        self.assertIn("#settingsToggle { width: auto;", TEMPLATE)


class SettingsRowsTests(unittest.TestCase):
    def test_short_labeled_rows(self):
        panel = between('id="settingsPanel"', 'id="jobIncompleteBanner"')
        for label in ("Problem report", "Text size", "Layout",
                      "Saved transcripts", "Sign-in"):
            self.assertIn(f"<h3>{label}</h3>", panel)

    def test_reset_layout_run_on_is_gone(self):
        self.assertNotIn(
            "Put the panel widths, the workspace height, and the "
            "comments panel back the way they started", TEMPLATE)
        block = between('id="layoutBlock"', 'id="testVideoBlock"')
        self.assertIn('id="resetLayoutBtn"', block)
        self.assertIn(">Reset layout</button>", block)
        self.assertIn("Panel sizes go back to how they started.",
                      block)


class ProcessingStatesTests(unittest.TestCase):
    def test_all_three_states_exist(self):
        self.assertIn('id="txProgress"', TEMPLATE)
        self.assertIn('id="txProgressBar"', TEMPLATE)
        self.assertIn('id="txLoading"', TEMPLATE)
        self.assertIn("Writing down the words…", TEMPLATE)
        # v0.20 chunk B: the blank transcript pane that carried
        # the third flashing-dots surface is gone with the
        # blank editor; the real transcript pane keeps it.
        self.assertIn("function setProcessing(on, pct)", TEMPLATE)
        body = between("function setProcessing(on, pct)",
                       "function sendDrop(files)")
        self.assertIn('classList.toggle("processing-video"', body)
        self.assertIn("txL.hidden = !on;", body)

    def test_spinner_is_blue_not_amber(self):
        css = between(".processing-video::after", ".tx-loading")
        self.assertIn("border-top-color: var(--blue)", css)
        self.assertNotIn("amber", css)

    def test_first_edit_progress_drives_the_states_from_stages(self):
        body = between("function showFirstEditProgress",
                       "function stopFirstEditTick")
        # v0.17 (feedback items 1 and 18): no percent exists to
        # drive the states with; the bar is indeterminate.
        self.assertIn("setProcessing(true, null);", body)
        self.assertNotIn("pct", body)
        hide = between("function hideFirstEditProgress",
                       "function pollFirstEdit")
        self.assertIn("setProcessing(false);", hide)

    def test_front_door_run_is_honest_indeterminate(self):
        self.assertIn("setProcessing(true, null);", TEMPLATE)


# ---------------------------------------------------------------------------
# The server side of item 2, live: recorded order, the join, and
# the fallback that names itself.
# ---------------------------------------------------------------------------


class IntakeOrderServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v15c-"))
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

    def post_json(self, path, data):
        status, body = self.request(path, data=data)
        return status, json.loads(body.decode("utf-8"))

    def drop_files(self, name, files):
        status, begun = self.post_json("/api/intake/begin",
                                       {"name": name})
        self.assertEqual(status, 200, begun)
        intake_id = begun["intake_id"]
        for relative, payload in files:
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            url = ("/api/intake/file?intake=" + quote(intake_id)
                   + "&path=" + quote(relative))
            status, body = self.request(url, raw=payload)
            self.assertEqual(status, 200, body)
        status, finished = self.post_json(
            "/api/intake/finish", {"intake_id": intake_id})
        self.assertEqual(status, 200, finished)
        return finished

    def test_recorded_order_decides_the_video(self):
        dummy = bytes(range(256)) * 16
        finished = self.drop_files("parts", [
            ("part-b.mp4", dummy),
            ("part-a.mp4", dummy),
        ])
        self.assertEqual(finished["kind"], "video")
        # The recorded order is the upload order, not the
        # alphabetical one, and the first part in it is the
        # video when the parts cannot be joined (dummy bytes
        # are not real video; the answer says so plainly).
        self.assertEqual(finished["parts"],
                         ["part-b.mp4", "part-a.mp4"])
        self.assertEqual(finished["video_name"], "part-b.mp4")
        self.assertFalse(finished["joined"])
        self.assertIn("could not be joined", finished["message"])
        self.assertIn("Make the edit", finished["message"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg needed")
    def test_two_real_parts_join_in_the_recorded_order(self):
        def tiny_video(path, color):
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error",
                 "-f", "lavfi", "-i", f"color=c={color}:s=64x64:d=0.5",
                 "-f", "lavfi", "-i", "sine=frequency=440:d=0.5",
                 "-shortest", "-c:v", "libx264", "-c:a", "aac",
                 str(path)],
                check=True, capture_output=True, timeout=60,
            )
            return path.read_bytes()

        first = tiny_video(self.tmp / "one.mp4", "red")
        second = tiny_video(self.tmp / "two.mp4", "blue")
        finished = self.drop_files("joined", [
            ("zzz-first.mp4", first),
            ("aaa-second.mp4", second),
        ])
        self.assertEqual(finished["kind"], "video")
        self.assertTrue(finished["joined"])
        self.assertEqual(finished["video_name"], "combined-parts.mp4")
        self.assertEqual(
            finished["parts"], ["zzz-first.mp4", "aaa-second.mp4"])
        status, body = self.request(finished["video_url"])
        self.assertEqual(status, 200)
        self.assertGreater(len(body), 1000)


if __name__ == "__main__":
    unittest.main()
