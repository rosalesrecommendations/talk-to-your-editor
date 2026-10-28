"""Tests for v0.8.0: the watchable-copy fix, the split layout,
and highlight editing in the transcript.

The playback fix, from the failure that shipped it: a 13-minute
HEVC video sat on "Getting your video ready to watch" for 45
minutes, because a conversion attempt could run for 30 minutes
with no progress shown, attempts ran back to back, and every job's
video converted at once. These tests pin the new shape:

- an attempt is bounded (a stall of 30 seconds, or a cap scaled
  to the video's length) and a failing attempt fails over;
- the whole conversion is bounded too;
- one build per job video, ever: a reload mid-build starts
  nothing new, and a finished copy is reused across restarts;
- the job summary carries the converter's real percent while a
  build runs;
- a 13-minute HEVC fixture converts, with progress, inside the
  bound this machine class allows (see mac-test-kit/TESTING.md
  for how the bar reads on Apple silicon).

The editing tests pin POST /api/word-range: one checkpoint per
phrase, through the same verifier as a word click, with gold,
protected, and special words partitioned out and reported.
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

from talk_to_your_editor import checkpoints, webapp
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig, create_server

FIXTURES = Path(__file__).parent / "fixtures"
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


def make_job(root: Path) -> WebAppConfig:
    job = root / "job"
    job.mkdir(parents=True)
    for name, dest in (
        ("sample_transcript.json", "transcript.json"),
        ("sample_cuts.json", "cuts.json"),
        ("sample_profile.json", "profile.json"),
    ):
        shutil.copyfile(FIXTURES / name, job / dest)
    (job / "review.mp4").write_bytes(b"sample-video")
    return WebAppConfig(job)


def word_status(config: WebAppConfig, index: int) -> str:
    transcript = webapp._load_json(
        str(config.job_dir / "transcript.json")
    )
    timeline = Timeline(transcript, webapp._effective_cuts(config))
    return timeline.word_status(index)


def checkpoint_labels(config: WebAppConfig) -> list:
    return [
        entry["label"]
        for entry in checkpoints.state(config)["checkpoints"]
    ]


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v08-"))
        self.config = make_job(self.tmp)
        self.server = create_server(self.config, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def request(self, method, path, payload=None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data, headers=headers, method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def page(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/", method="GET"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode()


class PlaybackBoundsTests(unittest.TestCase):
    def test_bounds_scale_with_duration(self):
        self.assertEqual(webapp._playback_bounds(None), (300.0, 600.0))
        self.assertEqual(webapp._playback_bounds(60.0), (300.0, 600.0))
        self.assertEqual(webapp._playback_bounds(780.0), (585.0, 975.0))
        self.assertEqual(
            webapp._playback_bounds(10000.0), (1200.0, 1800.0)
        )

    def test_progress_only_while_building(self):
        source = Path("/tmp/tye-v08-fake-source.mov")
        copy = Path("/tmp/tye-v08-fake-source.playback.mp4")
        key = str(copy)
        with mock.patch.object(
            webapp, "_needs_copy", return_value=True
        ), mock.patch.object(
            webapp, "_copy_ready", return_value=False
        ):
            self.assertIsNone(
                webapp._playback_progress_for(source, copy)
            )
            with webapp._PLAYBACK_BUILD_LOCK:
                webapp._PLAYBACK_PROGRESS[key] = 42
            try:
                self.assertEqual(
                    webapp._playback_progress_for(source, copy), 42
                )
            finally:
                with webapp._PLAYBACK_BUILD_LOCK:
                    webapp._PLAYBACK_PROGRESS.pop(key, None)
        with mock.patch.object(
            webapp, "_needs_copy", return_value=False
        ):
            self.assertIsNone(
                webapp._playback_progress_for(source, copy)
            )


FAKE_STALL = """\
import sys, time
sys.stdout.write("frame=10\\nout_time_us=1000000\\nprogress=continue\\n")
sys.stdout.flush()
time.sleep(120)
"""

FAKE_CREEP = """\
import sys, time
pos = 0
while True:
    pos += 500000
    sys.stdout.write(
        "frame=%d\\nout_time_us=%d\\nprogress=continue\\n" % (pos, pos)
    )
    sys.stdout.flush()
    time.sleep(0.1)
"""


@unittest.skipUnless(FFMPEG, "needs ffmpeg on PATH")
class AttemptRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v08-run-"))
        self.stall = self.tmp / "stall_ffmpeg.py"
        self.stall.write_text(FAKE_STALL)
        self.creep = self.tmp / "creep_ffmpeg.py"
        self.creep.write_text(FAKE_CREEP)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stalled_encoder_is_killed(self):
        with mock.patch.object(webapp, "_PLAYBACK_STALL_S", 1.5):
            started = time.monotonic()
            ok, _tail = webapp._run_ffmpeg_attempt(
                [sys.executable, str(self.stall)], 60.0, 300.0, None
            )
            elapsed = time.monotonic() - started
        self.assertFalse(ok)
        self.assertLess(elapsed, 15.0)

    def test_never_finishing_encoder_hits_the_attempt_cap(self):
        started = time.monotonic()
        ok, _tail = webapp._run_ffmpeg_attempt(
            [sys.executable, str(self.creep)], 60.0, 2.0, None
        )
        elapsed = time.monotonic() - started
        self.assertFalse(ok)
        self.assertGreaterEqual(elapsed, 1.5)
        self.assertLess(elapsed, 15.0)

    def test_failing_attempt_fails_over_to_the_next(self):
        source = self.tmp / "clip.mov"
        source.write_bytes(b"not-really-video")
        dest = self.tmp / "clip.playback.mp4"
        attempts = [
            (["-hwaccel", "videotoolbox"], ["-c:v", "h264_videotoolbox"]),
            ([], ["-c:v", "libx264"]),
        ]
        calls = []

        def fake_run(command, duration_s, cap_s, report):
            calls.append(command)
            if len(calls) == 1:
                return False, "encoder exploded"
            Path(command[-1]).write_bytes(b"watchable")
            return True, ""

        with mock.patch.object(
            webapp, "_playback_attempts", return_value=attempts
        ), mock.patch.object(
            webapp, "_run_ffmpeg_attempt", side_effect=fake_run
        ):
            self.assertTrue(webapp._convert_playback_copy(source, dest))
        self.assertEqual(len(calls), 2)
        self.assertEqual(dest.read_bytes(), b"watchable")

    def test_all_attempts_failing_lands_failed_fast(self):
        source = self.tmp / "clip.mov"
        source.write_bytes(b"not-really-video")
        dest = self.tmp / "clip.playback.mp4"
        attempts = [([], ["-c:v", "libx264"])]
        with mock.patch.object(
            webapp, "_playback_attempts", return_value=attempts
        ), mock.patch.object(
            webapp, "_run_ffmpeg_attempt",
            return_value=(False, "broken input"),
        ):
            started = time.monotonic()
            self.assertFalse(webapp._convert_playback_copy(source, dest))
            self.assertLess(time.monotonic() - started, 15.0)
        self.assertFalse(dest.exists())
        self.assertFalse(
            (self.tmp / "clip.playback.mp4.part").exists()
        )


class SingleConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v08-one-"))
        self.source = self.tmp / "clip.mov"
        self.source.write_bytes(b"fake-video")
        self.copy = self.tmp / "clip.playback.mp4"
        self.key = str(self.copy)

    def tearDown(self):
        with webapp._PLAYBACK_BUILD_LOCK:
            webapp._PLAYBACK_BUILDING.discard(self.key)
            webapp._PLAYBACK_FAILED.discard(self.key)
            webapp._PLAYBACK_PROGRESS.pop(self.key, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wait_for_state(self, want, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = webapp._playback_state_for(self.source, self.copy)
            if state == want:
                return state
            time.sleep(0.05)
        return webapp._playback_state_for(self.source, self.copy)

    def test_one_build_per_video_and_ready_copy_is_reused(self):
        calls = []

        def fake_convert(source, dest, progress=None):
            calls.append(dest)
            if progress is not None:
                progress(50)
            time.sleep(0.8)
            Path(dest).write_bytes(b"watchable")
            return True

        with mock.patch.object(
            webapp, "_needs_copy", return_value=True
        ), mock.patch.object(
            webapp, "_convert_playback_copy",
            side_effect=fake_convert,
        ):
            webapp._ensure_playback_copy_for(self.source, self.copy)
            # A page reload mid-build kicks again; nothing new starts.
            webapp._ensure_playback_copy_for(self.source, self.copy)
            self.assertEqual(self.wait_for_state("ready"), "ready")
            self.assertEqual(len(calls), 1)
            # A server restart forgets the registries; the copy on
            # disk is still reused, never rebuilt.
            with webapp._PLAYBACK_BUILD_LOCK:
                webapp._PLAYBACK_BUILDING.discard(self.key)
                webapp._PLAYBACK_FAILED.discard(self.key)
                webapp._PLAYBACK_PROGRESS.pop(self.key, None)
            webapp._ensure_playback_copy_for(self.source, self.copy)
            time.sleep(0.3)
            self.assertEqual(len(calls), 1)

    def test_failed_build_is_remembered_not_retried(self):
        calls = []

        def fake_convert(source, dest, progress=None):
            calls.append(dest)
            return False

        with mock.patch.object(
            webapp, "_needs_copy", return_value=True
        ), mock.patch.object(
            webapp, "_convert_playback_copy",
            side_effect=fake_convert,
        ):
            webapp._ensure_playback_copy_for(self.source, self.copy)
            self.assertEqual(self.wait_for_state("failed"), "failed")
            webapp._ensure_playback_copy_for(self.source, self.copy)
            time.sleep(0.3)
            self.assertEqual(len(calls), 1)


@unittest.skipUnless(
    FFMPEG and {"libx265", "libx264"} <= ENCODERS,
    "needs ffmpeg with libx265 and libx264",
)
class LongConversionTests(unittest.TestCase):
    """A 13-minute 1080p HEVC video, the shape of Justin's test.

    The fixture is representative rather than adversarial: a
    smooth animated scene with a smaller moving picture inside
    it, the statistics of a talking-head recording (a mostly
    steady frame, one moving subject), built once into the temp
    dir by looping a 65-second segment. The full-frame synthetic
    noise fixtures (testsrc, testsrc2) are several times more
    expensive per frame for both codecs than any camera footage;
    their measurements from this build are recorded in
    mac-test-kit/TESTING.md and the 0.8.0 delivery note. The
    bound asserted here is this machine class's: a small shared
    VM. Apple silicon reads in TESTING.md.
    """

    FIXTURE_DIR = Path(tempfile.gettempdir()) / "tye-v08-long-fixture"
    LONG_BOUND_S = 780.0  # faster than realtime on 2 shared vCPUs

    @classmethod
    def setUpClass(cls):
        cls.fixture = cls.FIXTURE_DIR / "long13.mov"
        duration = (
            webapp._probe_duration_s(cls.fixture)
            if cls.fixture.is_file()
            else None
        )
        if duration and duration > 700:
            return
        cls.FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        segment = cls.FIXTURE_DIR / "seg65.mov"
        subprocess.run(
            [
                FFMPEG, "-y", "-v", "error",
                "-f", "lavfi", "-i",
                "gradients=s=1920x1080:rate=30:duration=65",
                "-f", "lavfi", "-i",
                "testsrc=size=480x270:rate=30:duration=65",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=65",
                "-filter_complex",
                "[0:v][1:v]overlay="
                "x='100+mod(n*3,1200)':y='80+mod(n*2,600)'[v]",
                "-map", "[v]", "-map", "2:a",
                "-c:v", "libx265", "-preset", "ultrafast",
                "-tag:v", "hvc1", "-c:a", "aac", "-shortest",
                str(segment),
            ],
            check=True,
        )
        listing = cls.FIXTURE_DIR / "concat.txt"
        listing.write_text("file 'seg65.mov'\n" * 12)
        subprocess.run(
            [
                FFMPEG, "-y", "-v", "error", "-f", "concat",
                "-safe", "0", "-i", str(listing), "-c", "copy",
                str(cls.fixture),
            ],
            check=True,
        )

    def test_thirteen_minutes_converts_with_real_progress(self):
        dest = self.FIXTURE_DIR / "long13.playback.mp4"
        dest.unlink(missing_ok=True)
        pcts = []
        started = time.monotonic()
        ok = webapp._convert_playback_copy(
            self.fixture, dest, progress=pcts.append
        )
        elapsed = time.monotonic() - started
        self.assertTrue(ok)
        self.assertLess(elapsed, self.LONG_BOUND_S)
        self.assertGreaterEqual(len(pcts), 5)
        self.assertEqual(pcts, sorted(pcts))
        self.assertGreaterEqual(max(pcts), 90)
        self.assertEqual(webapp._probe_video_codec(dest), "h264")


@unittest.skipUnless(
    FFMPEG and "libx265" in ENCODERS, "needs ffmpeg with libx265"
)
class IntakePlaybackTests(unittest.TestCase):
    """A fresh drop's watchable copy, told truthfully.

    The intake status (the poll the page runs) must say "building"
    with the converter's percent while the copy is being made,
    never "ready" before the copy exists; the staged video URL
    must answer the pending page while building and the copy
    itself once ready. The converter is patched to block on an
    event so the building state is observed deterministically.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v08-intake-"))
        self.clip = self.tmp / "clip.mov"
        subprocess.run(
            [
                FFMPEG, "-y", "-v", "error",
                "-f", "lavfi", "-i",
                "testsrc=size=320x240:rate=30:duration=3",
                "-c:v", "libx265", "-preset", "ultrafast",
                "-tag:v", "hvc1", "-pix_fmt", "yuv420p",
                str(self.clip),
            ],
            check=True,
        )
        self.config = WebAppConfig(self.tmp / "launch")
        self.server = create_server(self.config, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

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
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.headers.get_content_type(), \
                    resp.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get_content_type(), \
                error.read()

    def test_intake_status_is_truthful_while_building(self):
        release = threading.Event()

        def blocking_convert(source, dest, progress=None):
            if progress is not None:
                progress(42)
            release.wait(timeout=30)
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"watchable-copy")
            return True

        with mock.patch.object(
            webapp, "_convert_playback_copy",
            side_effect=blocking_convert,
        ):
            status, _ctype, body = self.request(
                "POST", "/api/intake/begin", {"name": self.clip.name}
            )
            intake_id = json.loads(body)["intake_id"]
            status, _ctype, _body = self.request(
                "POST",
                f"/api/intake/file?intake={intake_id}"
                f"&path={self.clip.name}",
                raw=self.clip.read_bytes(),
            )
            self.assertEqual(status, 200)
            status, _ctype, body = self.request(
                "POST", "/api/intake/finish",
                {"intake_id": intake_id},
            )
            finished = json.loads(body)
            self.assertEqual(finished["kind"], "video")
            self.assertEqual(finished["playback"], "building")
            video_url = finished["video_url"]

            deadline = time.monotonic() + 15
            payload = {}
            while time.monotonic() < deadline:
                status, _ctype, body = self.request(
                    "GET",
                    f"/api/intake/status?intake={intake_id}",
                )
                payload = json.loads(body)
                if payload.get("playback_progress") == 42:
                    break
                time.sleep(0.1)
            self.assertEqual(payload.get("playback"), "building")
            self.assertEqual(payload.get("playback_progress"), 42)

            status, ctype, body = self.request("GET", video_url)
            self.assertNotEqual(body, b"watchable-copy")
            self.assertFalse(
                status == 200 and ctype.startswith("video"),
                "the staged URL served video before the copy "
                "was ready",
            )

            release.set()
            deadline = time.monotonic() + 15
            payload = {}
            while time.monotonic() < deadline:
                status, _ctype, body = self.request(
                    "GET",
                    f"/api/intake/status?intake={intake_id}",
                )
                payload = json.loads(body)
                if payload.get("playback") == "ready":
                    break
                time.sleep(0.1)
            self.assertEqual(payload.get("playback"), "ready")
            status, ctype, body = self.request("GET", video_url)
            self.assertEqual(status, 200)
            self.assertEqual(body, b"watchable-copy")


class WordRangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v08-range-"))
        self.config = make_job(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_phrase_cut_is_one_checkpoint_and_undo_restores(self):
        result = webapp._apply_word_range(self.config, 16, 24, "cut")
        self.assertEqual(result["moved"], 8)
        self.assertEqual(result["stayed"], [])
        for index in range(16, 24):
            self.assertEqual(word_status(self.config, index), "cut")
        self.assertEqual(word_status(self.config, 24), "kept")
        labels = checkpoint_labels(self.config)
        self.assertEqual(len(labels), 2)
        self.assertEqual(labels[-1], "Transcript phrase cut")
        checkpoints.undo(self.config)
        for index in range(16, 24):
            self.assertEqual(word_status(self.config, index), "kept")

    def test_phrase_restore_brings_the_whole_phrase_back(self):
        result = webapp._apply_word_range(
            self.config, 8, 16, "restore"
        )
        self.assertEqual(result["moved"], 8)
        self.assertEqual(result["stayed"], [])
        for index in range(8, 16):
            self.assertEqual(word_status(self.config, index), "kept")
        self.assertEqual(
            checkpoint_labels(self.config)[-1],
            "Transcript phrase brought back",
        )

    def test_mixed_selection_moves_only_target_state_words(self):
        # Kept 4-7, already cut 8-15, kept 16-19: a cut moves the
        # eight kept words and nothing else; nothing "stays",
        # because the cut words were never asked to move.
        result = webapp._apply_word_range(self.config, 4, 20, "cut")
        self.assertEqual(result["moved"], 8)
        self.assertEqual(result["stayed"], [])
        for index in list(range(4, 8)) + list(range(16, 20)):
            self.assertEqual(word_status(self.config, index), "cut")

    def test_protected_words_stay_with_a_plain_note(self):
        result = webapp._apply_word_range(self.config, 35, 40, "cut")
        self.assertEqual(result["moved"], 2)
        self.assertEqual(word_status(self.config, 35), "cut")
        self.assertEqual(word_status(self.config, 36), "cut")
        for index in range(37, 40):
            self.assertEqual(word_status(self.config, index), "kept")
        self.assertEqual(len(result["stayed"]), 1)
        stayed = result["stayed"][0]
        self.assertEqual(stayed["why"], "protected")
        self.assertEqual(
            (stayed["word_start"], stayed["word_end"]), (37, 40)
        )
        self.assertEqual(
            stayed["message"],
            "That part is protected (closing call to action), "
            "so it cannot be changed.",
        )

    def test_protected_prayer_partitions_a_selection_too(self):
        result = webapp._apply_word_range(self.config, 22, 26, "cut")
        self.assertEqual(result["moved"], 2)
        self.assertEqual(len(result["stayed"]), 1)
        self.assertEqual(result["stayed"][0]["why"], "protected")
        self.assertIn(
            "prayer protection", result["stayed"][0]["message"]
        )
        self.assertEqual(word_status(self.config, 24), "kept")
        self.assertEqual(word_status(self.config, 25), "kept")

    def test_gold_span_words_stay_and_report_the_span(self):
        candidate = {
            "gold_id": webapp._gold_id(18, 21, "held-cut"),
            "word_start": 18,
            "word_end": 21,
            "kind": "held-cut",
            "source": "test",
            "reason": "held for the creator's call",
            "text": "the words in gold",
            "keep_text": None,
            "keep_word_start": None,
            "keep_word_end": None,
            "operation": None,
            "status": "open",
            "note": None,
            "created_at": "2026-10-27T00:00:00Z",
            "decided_at": None,
        }
        webapp._record_gold_spans(self.config, [candidate])
        self.assertEqual(len(webapp._open_gold_spans(self.config)), 1)
        result = webapp._apply_word_range(self.config, 16, 24, "cut")
        self.assertEqual(result["moved"], 5)
        for index in (16, 17, 21, 22, 23):
            self.assertEqual(word_status(self.config, index), "cut")
        for index in (18, 19, 20):
            self.assertEqual(word_status(self.config, index), "kept")
        self.assertEqual(len(result["stayed"]), 1)
        stayed = result["stayed"][0]
        self.assertEqual(stayed["why"], "gold")
        self.assertEqual(stayed["gold_id"], candidate["gold_id"])
        self.assertEqual(
            (stayed["word_start"], stayed["word_end"]), (18, 21)
        )
        # The span is still open, still the creator's call to make.
        self.assertEqual(len(webapp._open_gold_spans(self.config)), 1)

    def test_special_insert_word_stays(self):
        # Forty words, the signature insert holding word 20. A
        # highlight across it cuts the words on both sides and
        # leaves the insert standing. (A realistic word count
        # matters: the verifier holds any single change that
        # removes over a quarter of a video's words, by design.)
        job = self.tmp / "special-job"
        job.mkdir()
        words = [
            {
                "word": f"w{i}",
                "start": i * 0.5,
                "end": i * 0.5 + 0.4,
            }
            for i in range(40)
        ]
        (job / "transcript.json").write_text(json.dumps({
            "engine": "test", "duration": 20.0,
            "words": words, "segments": [],
        }))
        (job / "cuts.json").write_text(json.dumps({
            "source_duration_s": 20.0,
            "kept_ranges": [
                {"start": 0.0, "end": 10.1},
                {
                    "start": 10.1, "end": 10.35,
                    "insert_kind": "signature_breath_gasp",
                    "audio_start": 10.1, "audio_end": 10.35,
                },
                {"start": 10.35, "end": 20.0},
            ],
        }))
        config = WebAppConfig(job)
        result = webapp._apply_word_range(config, 18, 24, "cut")
        self.assertEqual(result["moved"], 5)
        self.assertEqual(len(result["stayed"]), 1)
        stayed = result["stayed"][0]
        self.assertEqual(stayed["why"], "special")
        self.assertEqual(
            (stayed["word_start"], stayed["word_end"]), (20, 21)
        )
        self.assertEqual(word_status(config, 20), "special")
        for index in (18, 19, 21, 22, 23):
            self.assertEqual(word_status(config, index), "cut")

    def test_no_change_selection_writes_no_checkpoint(self):
        result = webapp._apply_word_range(self.config, 8, 16, "cut")
        self.assertEqual(result["moved"], 0)
        self.assertEqual(result["stayed"], [])
        self.assertEqual(checkpoint_labels(self.config), [])

    def test_bad_action_and_bounds_raise(self):
        with self.assertRaises(ValueError):
            webapp._apply_word_range(self.config, 0, 4, "delete")
        with self.assertRaises(ValueError):
            webapp._apply_word_range(self.config, 38, 45, "cut")
        with self.assertRaises(ValueError):
            webapp._apply_word_range(self.config, 5, 5, "cut")


class WordRangeHttpTests(ServerCase):
    def test_summary_carries_playback_progress(self):
        status, payload = self.request("GET", "/api/job")
        self.assertEqual(status, 200)
        self.assertIn("playback_progress", payload)
        # One entry per video; None while no honest percent exists
        # (a browser-playable file needs no copy at all).
        self.assertIsNone(payload["playback_progress"]["review.mp4"])

    def test_word_range_round_trip_over_http(self):
        status, payload = self.request(
            "POST", "/api/word-range",
            {"word_start": 16, "word_end": 20, "action": "cut"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["moved"], 4)
        self.assertEqual(payload["stayed"], [])
        labels = checkpoint_labels(self.config)
        self.assertEqual(labels[-1], "Transcript phrase cut")
        status, payload = self.request(
            "POST", "/api/word-range",
            {"word_start": 16, "word_end": 20, "action": "restore"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["moved"], 4)
        self.assertEqual(word_status(self.config, 16), "kept")

    def test_word_range_rejects_a_bad_action(self):
        status, _payload = self.request(
            "POST", "/api/word-range",
            {"word_start": 0, "word_end": 4, "action": "delete"},
        )
        self.assertEqual(status, 400)


class TemplateTests(ServerCase):
    def test_one_player_status_at_a_time(self):
        html = self.page()
        self.assertIn("setPlayerStatus", html)
        # Only the status controller touches the three overlays.
        self.assertEqual(html.count('getElementById("vidPending")'), 1)
        self.assertEqual(html.count('getElementById("vidError")'), 1)
        self.assertEqual(html.count('getElementById("vidLoading")'), 1)
        self.assertIn("the picture will catch up", html)
        self.assertIn("playback_progress", html)

    def test_split_layout_wide_and_stacked_narrow(self):
        html = self.page()
        self.assertIn("review-split", html)
        # v2: the stacked layout now starts below about 1100px
        # (the round 2 workspace spec), not below 1024px.
        self.assertIn("@media (min-width: 1100px)", html)
        self.assertIn(".review-right #transcriptPanel", html)
        self.assertIn("@media (max-width: 1099.98px)", html)

    def test_phrase_editing_is_wired(self):
        html = self.page()
        self.assertIn("/api/word-range", html)
        self.assertIn("Backspace cuts it", html)


if __name__ == "__main__":
    unittest.main()
