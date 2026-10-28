"""Tests for v0.9.0 Cluster 1: playback and the player.

From Justin's 0.8.0 session (feedback items 1, 2, 3, 4, 17 and
FLAGs A and B): the review video never became watchable, no
conversion percentage ever appeared, the old "A long video can
take a few minutes" wording showed the whole time, and the stale
"could not be played" message ghosted behind the preparing one.

The root causes these tests pin:

- The player overlays (.vid-loading / .vid-error) set
  ``display: flex`` in their own CSS, which beats the browser's
  built-in ``[hidden]`` rule, so ``hidden`` never hid anything:
  every overlay showed at once, whatever setPlayerStatus did.
  A ``[hidden]`` guard now outranks the overlay display.
- The pending overlay's static HTML and the server's plain
  pending page both still carried the old wording, and the plain
  page never carried the converter's percent at all.
- A copy whose build had never been started rested in
  "unstarted" forever: the polls that could see that state never
  started the build, so the page waited on nothing. The job
  summary and the intake status now kick an unstarted build.
- Nothing wrote a conversion's state changes anywhere a
  rehearsal could read. Builds now append start / progress /
  finish / fail lines to ``playback.log`` in the playback folder.
- The player never skipped cut parts, Space re-ran the seek
  button the creator had just clicked instead of playing, the
  clock and the playhead ran on different scales before the
  file's metadata landed, and a pending format kept its sound
  playing with no picture.

The long tests at the end prove the watchability path end to
end with a generated 100-second HEVC fixture, once through the
front-door staged surface and once through the job surface,
with real ffmpeg conversions and the real HTTP routes.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import webapp
from talk_to_your_editor.webapp import WebAppConfig, create_server

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATE = (
    Path(webapp.__file__).resolve().parent
    / "templates" / "test_app.html"
)
FFMPEG = shutil.which("ffmpeg")


def encoder_names():
    if not FFMPEG:
        return set()
    out = subprocess.run(
        [FFMPEG, "-hide_banner", "-encoders"],
        capture_output=True, text=True,
    ).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) > 1 and parts[0].startswith("V"):
            names.add(parts[1])
    return names


ENCODERS = encoder_names()


def make_hevc_clip(path: Path, seconds: int = 3) -> Path:
    subprocess.run(
        [
            FFMPEG, "-y", "-v", "error",
            "-f", "lavfi", "-i",
            f"testsrc=size=320x240:rate=30:duration={seconds}",
            "-c:v", "libx265", "-preset", "ultrafast",
            "-tag:v", "hvc1", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def clear_copy_state(copy_path: Path) -> None:
    key = str(copy_path)
    with webapp._PLAYBACK_BUILD_LOCK:
        webapp._PLAYBACK_BUILDING.discard(key)
        webapp._PLAYBACK_FAILED.discard(key)
        webapp._PLAYBACK_PROGRESS.pop(key, None)


def read_log(copy_path: Path) -> list:
    log = webapp._playback_log_path(copy_path)
    if not log.is_file():
        return []
    return [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class HttpCase(unittest.TestCase):
    """A running server on a throwaway config, with helpers."""

    def make_server(self, config):
        self.server = create_server(config, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        return self.server

    def tearDown(self):
        server = getattr(self, "server", None)
        if server is not None:
            server.shutdown()
            server.server_close()

    def request(self, method, path, payload=None, raw=None):
        data = raw
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        if raw is not None:
            headers["Content-Type"] = "application/octet-stream"
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data, headers=headers, method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.headers.get_content_type(), \
                    resp.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get_content_type(), \
                error.read()


class TemplatePlaybackTests(unittest.TestCase):
    """The page itself: one message, real wording, working keys."""

    @classmethod
    def setUpClass(cls):
        cls.page = TEMPLATE.read_text(encoding="utf-8")
        cls.server_source = Path(
            webapp.__file__
        ).read_text(encoding="utf-8")

    def slice(self, start_marker, end_marker):
        start = self.page.index(start_marker)
        end = self.page.index(end_marker, start)
        return self.page[start:end]

    # FLAG A / item 2 -------------------------------------------------

    def test_hidden_attribute_wins_over_overlay_display(self):
        # The overlays' own display:flex used to defeat the hidden
        # attribute, so all three messages showed at once. The
        # guard must be in the CSS, not just in the controller.
        self.assertIn(
            ".vid-loading[hidden], .vid-error[hidden] "
            "{ display: none; }",
            self.page,
        )

    def test_error_wording_never_blames_the_browser(self):
        self.assertNotIn("browser cannot show", self.page)
        self.assertNotIn("format this browser", self.page)
        self.assertIn(
            "This video could not be played here. The file could "
            "not be shown as a picture.",
            self.page,
        )

    # FLAG B / item 1 -------------------------------------------------

    def test_old_building_wording_is_gone_everywhere(self):
        self.assertNotIn(
            "A long video can take a few minutes", self.page
        )
        self.assertNotIn(
            "A long video can take a few minutes",
            self.server_source,
        )
        self.assertNotIn(
            "take a few minutes",
            webapp.PLAYBACK_BUILDING_MESSAGE,
        )

    def test_pending_overlay_starts_from_the_plain_ready_line(self):
        self.assertIn(
            'id="vidPending" hidden>Getting your video ready '
            "to watch…</div>",
            self.page,
        )

    def test_pending_pauses_so_no_sound_without_a_picture(self):
        block = self.slice(
            "function showPendingFor(name)", "function startPlayer"
        )
        self.assertIn("vid.pause();", block)

    def test_clock_and_playhead_share_the_source_scale(self):
        block = self.slice(
            "function tlDuration()", "function tlInsetLeft"
        )
        self.assertLess(
            block.index("jobSourceDuration"),
            block.index("state.outputDuration"),
        )
        seeked = self.slice(
            'vid.addEventListener("seeked"',
            'document.getElementById("btnV")',
        )
        self.assertIn("clockEl.textContent", seeked)
        start = self.page.index("function stepBy(seconds)")
        stepped = self.page[start:start + 400]
        self.assertIn("clockEl.textContent", stepped)

    # Item 3 ----------------------------------------------------------

    def test_timeline_markers_stay_inside_the_strip(self):
        self.assertIn("function tlInsetLeft(frac, insetPx)", self.page)
        self.assertIn("tlInsetLeft(frac, 6)", self.page)
        self.assertIn("tlInsetLeft(c.ts / d, 8)", self.page)
        self.assertIn("tlInsetLeft(frac, halfBubble)", self.page)
        self.assertNotIn(
            'tlPlayhead.style.left = Math.max(0, Math.min(100, pct))',
            self.page,
        )
        track = self.slice(".tl-track {", ".tl-playhead {")
        self.assertIn("max-width: 100%", track)

    # Item 4 ----------------------------------------------------------

    def test_play_skips_cut_parts_and_keeps_going(self):
        self.assertIn("function cutSkipRanges()", self.page)
        self.assertIn("function skipCutParts()", self.page)
        self.assertIn("function editPlaybackApplies()", self.page)
        self.assertIn(
            'vid.addEventListener("play", function () '
            "{ skipCutParts(); });",
            self.page,
        )
        block = self.slice(
            'vid.addEventListener("timeupdate"',
            "function updatePinNote",
        )
        self.assertLess(
            block.index("skipCutParts();"),
            block.index("clockEl.textContent"),
        )
        # Skipping is gated on the file running on the source
        # clock, so an already-edited render is never re-cut.
        gate = self.slice(
            "function editPlaybackApplies()",
            "function cutSkipRanges()",
        )
        self.assertIn("jobSourceDuration", gate)

    def test_space_plays_inside_the_player_areas(self):
        self.assertIn("function spacePlaysVideo(el)", self.page)
        self.assertIn(
            '"#vidWrap, #tlbar, #fmtRow, #transcriptCard"',
            self.page,
        )
        self.assertIn("!spacePlaysVideo(e.target)", self.page)

    # Item 17 ---------------------------------------------------------

    def test_forward_buttons_beside_the_back_buttons(self):
        row = self.slice('id="fmtRow"', 'id="fmtNote"')
        self.assertIn('id="back5"', row)
        self.assertIn('id="fwd1"', row)
        self.assertIn('id="fwd5"', row)
        self.assertLess(row.index('id="back5"'), row.index('id="fwd1"'))
        self.assertLess(row.index('id="fwd1"'), row.index('id="fwd5"'))
        self.assertIn(">Forward 1s<", row)
        self.assertIn(">Forward 5s<", row)
        self.assertIn(
            'document.getElementById("fwd1").addEventListener('
            '"click", function () { stepBy(1); });',
            self.page,
        )
        self.assertIn(
            'document.getElementById("fwd5").addEventListener('
            '"click", function () { stepBy(5); });',
            self.page,
        )


class ConversionLogTests(unittest.TestCase):
    """State changes land in playback.log, ready or failed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v09-log-"))
        self.source = self.tmp / "clip.mov"
        self.source.write_bytes(b"fake-video")
        self.copy = self.tmp / "playback" / "clip.playback.mp4"

    def tearDown(self):
        clear_copy_state(self.copy)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wait_for_state(self, want, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = webapp._playback_state_for(self.source, self.copy)
            if state == want:
                return state
            time.sleep(0.05)
        return webapp._playback_state_for(self.source, self.copy)

    def test_building_and_ready_are_logged(self):
        def fake_convert(source, dest, progress=None):
            if progress is not None:
                progress(50)
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"watchable")
            return True

        with mock.patch.object(
            webapp, "_needs_copy", return_value=True
        ), mock.patch.object(
            webapp, "_convert_playback_copy",
            side_effect=fake_convert,
        ):
            webapp._ensure_playback_copy_for(self.source, self.copy)
            self.assertEqual(self.wait_for_state("ready"), "ready")
        events = [r["event"] for r in read_log(self.copy)]
        self.assertEqual(events[0], "building")
        self.assertEqual(events[-1], "ready")

    def test_failed_build_is_logged_as_failed(self):
        with mock.patch.object(
            webapp, "_needs_copy", return_value=True
        ), mock.patch.object(
            webapp, "_convert_playback_copy", return_value=False
        ):
            webapp._ensure_playback_copy_for(self.source, self.copy)
            self.assertEqual(self.wait_for_state("failed"), "failed")
        events = [r["event"] for r in read_log(self.copy)]
        self.assertEqual(events[0], "building")
        self.assertEqual(events[-1], "failed")


@unittest.skipUnless(
    FFMPEG and {"libx265", "libx264"} <= ENCODERS,
    "needs ffmpeg with libx265 and libx264",
)
class RealConversionLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v09-real-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_real_conversion_logs_start_progress_finish(self):
        source = make_hevc_clip(self.tmp / "clip.mov", seconds=3)
        dest = self.tmp / "playback" / "clip.playback.mp4"
        pcts = []
        ok = webapp._convert_playback_copy(
            source, dest, progress=pcts.append
        )
        self.assertTrue(ok)
        self.assertEqual(webapp._probe_video_codec(dest), "h264")
        self.assertTrue(pcts)
        self.assertEqual(pcts, sorted(pcts))
        events = [r["event"] for r in read_log(dest)]
        self.assertEqual(events[0], "start")
        self.assertIn("progress", events)
        self.assertEqual(events[-1], "finish")

    def test_broken_file_logs_the_attempt_and_the_fail(self):
        source = self.tmp / "broken.mov"
        source.write_bytes(b"definitely not a video")
        dest = self.tmp / "playback" / "broken.playback.mp4"
        self.assertFalse(webapp._convert_playback_copy(source, dest))
        self.assertFalse(dest.exists())
        events = [r["event"] for r in read_log(dest)]
        self.assertEqual(events[0], "start")
        self.assertIn("attempt_failed", events)
        self.assertEqual(events[-1], "fail")


@unittest.skipUnless(
    FFMPEG and "libx265" in ENCODERS, "needs ffmpeg with libx265"
)
class UnstartedKickTests(HttpCase):
    """A build that never started is started by the polls."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v09-kick-"))
        self.clip = make_hevc_clip(self.tmp / "clip.mov", seconds=3)

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_job(self, name="job"):
        job = self.tmp / name
        job.mkdir(parents=True)
        for src, dest in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / src, job / dest)
        shutil.copyfile(self.clip, job / "review.mov")
        return WebAppConfig(job)

    def test_job_summary_kicks_an_unstarted_build(self):
        config = self.make_job()
        copy_path = webapp._playback_copy_path(config, "review.mov")
        clear_copy_state(copy_path)
        self.assertEqual(
            webapp._playback_state(config, "review.mov"), "unstarted"
        )
        calls = []
        release = threading.Event()

        def blocking_convert(source, dest, progress=None):
            calls.append(dest)
            release.wait(timeout=30)
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"watchable")
            return True

        try:
            with mock.patch.object(
                webapp, "_convert_playback_copy",
                side_effect=blocking_convert,
            ):
                summary = webapp._job_summary(config)
                self.assertEqual(
                    summary["playback"]["review.mov"], "building"
                )
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not calls:
                    time.sleep(0.05)
                self.assertEqual(len(calls), 1)
        finally:
            release.set()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if webapp._playback_state(
                    config, "review.mov"
                ) == "ready":
                    break
                time.sleep(0.05)
            clear_copy_state(copy_path)

    def test_intake_status_kicks_an_unstarted_build(self):
        config = WebAppConfig(self.tmp / "launch")
        self.make_server(config)

        def failing_convert(source, dest, progress=None):
            return False

        with mock.patch.object(
            webapp, "_convert_playback_copy",
            side_effect=failing_convert,
        ):
            status, _c, body = self.request(
                "POST", "/api/intake/begin", {"name": "clip.mov"}
            )
            intake_id = json.loads(body)["intake_id"]
            self.request(
                "POST",
                f"/api/intake/file?intake={intake_id}&path=clip.mov",
                raw=self.clip.read_bytes(),
            )
            status, _c, body = self.request(
                "POST", "/api/intake/finish",
                {"intake_id": intake_id},
            )
            self.assertEqual(json.loads(body)["kind"], "video")
            deadline = time.monotonic() + 15
            payload = {}
            while time.monotonic() < deadline:
                _s, _c, body = self.request(
                    "GET", f"/api/intake/status?intake={intake_id}"
                )
                payload = json.loads(body)
                if payload.get("playback") == "failed":
                    break
                time.sleep(0.1)
            self.assertEqual(payload.get("playback"), "failed")

        # The failed mark is lost (a restart, a cleared registry):
        # the state falls back to "unstarted", and the very next
        # status poll must start the build again rather than let
        # the front door wait on nothing.
        staged = (
            Path(self.server.intakes[intake_id]["dir"]) / "clip.mov"
        )
        copy_path = webapp._staged_copy_path(staged)
        clear_copy_state(copy_path)
        self.assertEqual(
            webapp._playback_state_for(staged, copy_path),
            "unstarted",
        )
        release = threading.Event()
        calls = []

        def blocking_convert(source, dest, progress=None):
            calls.append(dest)
            if progress is not None:
                progress(42)
            release.wait(timeout=30)
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"watchable")
            return True

        try:
            with mock.patch.object(
                webapp, "_convert_playback_copy",
                side_effect=blocking_convert,
            ):
                status, _c, body = self.request(
                    "GET", f"/api/intake/status?intake={intake_id}"
                )
                payload = json.loads(body)
                self.assertEqual(payload.get("playback"), "building")
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and not calls:
                    time.sleep(0.05)
                self.assertEqual(len(calls), 1)
        finally:
            release.set()
            clear_copy_state(copy_path)


@unittest.skipUnless(
    FFMPEG and "libx265" in ENCODERS, "needs ffmpeg with libx265"
)
class PendingPageTests(HttpCase):
    """The plain pending page carries the real percent (FLAG B)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v09-pend-"))
        self.clip = make_hevc_clip(self.tmp / "clip.mov", seconds=3)

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_staged_pending_page_shows_the_percent(self):
        config = WebAppConfig(self.tmp / "launch")
        self.make_server(config)
        release = threading.Event()

        def blocking_convert(source, dest, progress=None):
            if progress is not None:
                progress(42)
            release.wait(timeout=30)
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"watchable")
            return True

        try:
            with mock.patch.object(
                webapp, "_convert_playback_copy",
                side_effect=blocking_convert,
            ):
                status, _c, body = self.request(
                    "POST", "/api/intake/begin", {"name": "clip.mov"}
                )
                intake_id = json.loads(body)["intake_id"]
                self.request(
                    "POST",
                    f"/api/intake/file?intake={intake_id}"
                    "&path=clip.mov",
                    raw=self.clip.read_bytes(),
                )
                status, _c, body = self.request(
                    "POST", "/api/intake/finish",
                    {"intake_id": intake_id},
                )
                finished = json.loads(body)
                self.assertEqual(finished["playback"], "building")
                # Wait until the percent is on record, then ask
                # for the video itself: the plain page a browser
                # lands on must carry that same percent.
                deadline = time.monotonic() + 15
                payload = {}
                while time.monotonic() < deadline:
                    _s, _c, body = self.request(
                        "GET",
                        f"/api/intake/status?intake={intake_id}",
                    )
                    payload = json.loads(body)
                    if payload.get("playback_progress") == 42:
                        break
                    time.sleep(0.1)
                self.assertEqual(payload.get("playback_progress"), 42)
                status, _c, body = self.request(
                    "GET", finished["video_url"]
                )
                self.assertEqual(status, 503)
                text = body.decode("utf-8")
                self.assertIn("Getting your video ready to watch",
                              text)
                self.assertIn("42%", text)
                self.assertNotIn("few minutes", text)
        finally:
            release.set()


@unittest.skipUnless(
    FFMPEG and {"libx265", "libx264"} <= ENCODERS,
    "needs ffmpeg with libx265 and libx264",
)
class LongHevcWatchabilityTests(HttpCase):
    """Item 1, end to end: a long HEVC video becomes watchable.

    The fixture is 100 seconds of HEVC (a mostly steady frame
    with a moving inset, the statistics of a talking-head
    recording), built once by looping a 50-second segment. Both
    tests run the real converter through the real HTTP routes:
    the front-door staged surface, and the job surface a creator
    reviews on. Each asserts the truthful sequence (building
    with a climbing percent, then ready), that what is finally
    served is the H.264 copy and not the HEVC original, and that
    the conversion log in the playback folder tells the story.
    """

    FIXTURE_DIR = (
        Path(tempfile.gettempdir()) / "tye-v09-long-fixture"
    )
    READY_BOUND_S = 1500.0

    @classmethod
    def setUpClass(cls):
        cls.fixture = cls.FIXTURE_DIR / "long100.mov"
        duration = (
            webapp._probe_duration_s(cls.fixture)
            if cls.fixture.is_file()
            else None
        )
        if duration and duration > 95:
            return
        cls.FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        segment = cls.FIXTURE_DIR / "seg50.mov"
        subprocess.run(
            [
                FFMPEG, "-y", "-v", "error",
                "-f", "lavfi", "-i",
                "gradients=s=854x480:rate=30:duration=50",
                "-f", "lavfi", "-i",
                "testsrc=size=320x180:rate=30:duration=50",
                "-f", "lavfi", "-i",
                "sine=frequency=440:duration=50",
                "-filter_complex",
                "[0:v][1:v]overlay="
                "x='40+mod(n*2,400)':y='40+mod(n,200)'[v]",
                "-map", "[v]", "-map", "2:a",
                "-c:v", "libx265", "-preset", "ultrafast",
                "-tag:v", "hvc1", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest",
                str(segment),
            ],
            check=True,
        )
        listing = cls.FIXTURE_DIR / "concat.txt"
        listing.write_text("file 'seg50.mov'\n" * 2)
        subprocess.run(
            [
                FFMPEG, "-y", "-v", "error", "-f", "concat",
                "-safe", "0", "-i", str(listing), "-c", "copy",
                str(cls.fixture),
            ],
            check=True,
        )

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v09-long-"))

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def poll_until_ready(self, fetch):
        """fetch() -> (state, progress); collect the truth."""
        states = []
        progresses = []
        started = time.monotonic()
        deadline = started + self.READY_BOUND_S
        state = None
        while time.monotonic() < deadline:
            state, progress = fetch()
            if not states or states[-1] != state:
                states.append(state)
            if isinstance(progress, int):
                progresses.append(progress)
            if state == "ready":
                break
            time.sleep(0.5)
        return states, progresses, time.monotonic() - started

    def test_staged_front_door_long_hevc_becomes_watchable(self):
        config = WebAppConfig(self.tmp / "launch")
        self.make_server(config)
        status, _c, body = self.request(
            "POST", "/api/intake/begin", {"name": "long100.mov"}
        )
        intake_id = json.loads(body)["intake_id"]
        status, _c, _b = self.request(
            "POST",
            f"/api/intake/file?intake={intake_id}&path=long100.mov",
            raw=self.fixture.read_bytes(),
        )
        self.assertEqual(status, 200)
        status, _c, body = self.request(
            "POST", "/api/intake/finish",
            {"intake_id": intake_id},
        )
        finished = json.loads(body)
        self.assertEqual(finished["kind"], "video")
        self.assertEqual(finished["playback"], "building")

        def fetch():
            _s, _c, raw = self.request(
                "GET", f"/api/intake/status?intake={intake_id}"
            )
            payload = json.loads(raw)
            return payload.get("playback"), \
                payload.get("playback_progress")

        states, progresses, elapsed = self.poll_until_ready(fetch)
        self.assertEqual(states[0], "building")
        self.assertEqual(states[-1], "ready")
        self.assertLess(elapsed, self.READY_BOUND_S)
        self.assertGreaterEqual(len(set(progresses)), 2)
        self.assertEqual(progresses, sorted(progresses))

        status, ctype, body = self.request(
            "GET", finished["video_url"]
        )
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "video/mp4")
        self.assertGreater(len(body), 1_000_000)
        self.assertNotEqual(body, self.fixture.read_bytes())

        staged = (
            Path(self.server.intakes[intake_id]["dir"])
            / "long100.mov"
        )
        events = [
            r["event"]
            for r in read_log(webapp._staged_copy_path(staged))
        ]
        self.assertIn("building", events)
        self.assertIn("progress", events)
        self.assertEqual(events[-1], "ready")
        clear_copy_state(webapp._staged_copy_path(staged))

    def test_job_surface_long_hevc_becomes_watchable(self):
        job = self.tmp / "job"
        job.mkdir(parents=True)
        for src, dest in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / src, job / dest)
        shutil.copyfile(self.fixture, job / "plunge.mov")
        config = WebAppConfig(job)
        self.make_server(config)

        def fetch():
            _s, _c, raw = self.request("GET", "/api/job")
            payload = json.loads(raw)
            return payload["playback"]["plunge.mov"], \
                payload["playback_progress"]["plunge.mov"]

        states, progresses, elapsed = self.poll_until_ready(fetch)
        self.assertEqual(states[-1], "ready")
        self.assertLess(elapsed, self.READY_BOUND_S)
        self.assertGreaterEqual(len(set(progresses)), 2)
        self.assertEqual(progresses, sorted(progresses))

        status, ctype, body = self.request("GET", "/video/plunge.mov")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "video/mp4")
        self.assertGreater(len(body), 1_000_000)
        self.assertNotEqual(body, self.fixture.read_bytes())

        copy_path = webapp._playback_copy_path(config, "plunge.mov")
        self.assertEqual(
            webapp._probe_video_codec(copy_path), "h264"
        )
        events = [r["event"] for r in read_log(copy_path)]
        self.assertEqual(events[0], "building")
        self.assertIn("start", events)
        self.assertIn("finish", events)
        self.assertEqual(events[-1], "ready")
        clear_copy_state(copy_path)


if __name__ == "__main__":
    unittest.main()
