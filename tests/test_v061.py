"""Tests for v0.6.1 (stdlib unittest, no dependencies).

Covers the finish card, breaths as a choice, the plain-words
missing-file surfaces, keys saved on this Mac, the captions
switch's new home on the delivery cards, the visible first-edit
progress stages, and the watchable H.264 copy an intake job's
source video gets at intake time. HTTP tests spin the real server
on an ephemeral port, following tests/test_v04.py and
tests/test_v06.py; the HEVC fixture is a real libx265 encode, the
same codec family as an iPhone video.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
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

from talk_to_your_editor import direction_parse, intake_build, webapp
from talk_to_your_editor import transcribe as speech
from talk_to_your_editor.webapp import WebAppConfig, create_server

FIXTURES = Path(__file__).resolve().parent / "fixtures"
VIDEO_BYTES = bytes(range(256)) * 32
FFMPEG = shutil.which("ffmpeg")


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def encoders_available(*names: str) -> bool:
    if not FFMPEG:
        return False
    out = subprocess.run(
        [FFMPEG, "-hide_banner", "-encoders"],
        capture_output=True, text=True,
    ).stdout
    return all(name in out for name in names)


HEVC = encoders_available("libx265")


def request_raw(base, path, data=None, raw=None, headers=None,
                method=None):
    hdrs = dict(headers or {})
    payload = raw
    if data is not None:
        payload = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base + path,
        data=payload,
        headers=hdrs,
        method=method or ("POST" if payload is not None else "GET"),
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def request_json(base, path, data=None):
    status, _, body = request_raw(base, path, data=data)
    return status, json.loads(body)


class ServerCase(unittest.TestCase):
    """A real server on an ephemeral port over a prepared job dir."""

    def build_job(self, job: Path) -> None:
        raise NotImplementedError

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v061-"))
        self.job = self.tmp / "job"
        self.job.mkdir()
        # v0.7.0: front-door saves also write the creator's usual
        # (~/.talk-to-your-editor/usual.json); each test gets its own
        # HOME so one test's choices cannot seed the next test's job.
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = mock.patch.dict(
            os.environ, {"HOME": str(self.home)}, clear=False
        )
        env.start()
        self.addCleanup(env.stop)
        self.build_job(self.job)
        self.server = create_server(
            WebAppConfig(self.job), host="127.0.0.1", port=0
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
        return request_json(self.base, path, data=data)

    def raw(self, path, **kwargs):
        return request_raw(self.base, path, **kwargs)

    def summary(self):
        status, payload = self.call("/api/job")
        self.assertEqual(status, 200, payload)
        return payload


def copy_sample_job(job: Path) -> None:
    for name, target in (
        ("sample_transcript.json", "transcript.json"),
        ("sample_cuts.json", "cuts.json"),
        ("sample_profile.json", "profile.json"),
    ):
        shutil.copyfile(FIXTURES / name, job / target)
    (job / "review.mp4").write_bytes(VIDEO_BYTES)


# ---------------------------------------------------------------------------
# Breaths: a custom job whose joins are known to the fraction.
#
# Words 0-2 end at 2.6s; the next range starts at 3.3s and its first
# word at 4.0s, so join 0's audible pause is 0.7s. Join 1's pause is
# 0.9s, and its next word (index 6) sits inside a protected span.
# ---------------------------------------------------------------------------

BREATH_WORDS = [
    ("One", 1.0, 1.4), ("two", 1.6, 2.0), ("three.", 2.2, 2.6),
    ("Four", 4.0, 4.4), ("five", 4.6, 5.0), ("six.", 5.2, 5.6),
    ("Seven", 7.5, 7.9), ("eight", 8.1, 8.5), ("nine.", 8.7, 9.1),
]


def write_breaths_job(job: Path) -> None:
    words = [
        {"word": w, "start": s, "end": e} for w, s, e in BREATH_WORDS
    ]
    transcript = {
        "engine": "test",
        "source_file": "clip.mp4",
        "language": "en",
        "duration": 10.0,
        "words": words,
        "segments": [],
    }
    cuts = {
        "gap_seconds_setting": 0.2,
        "source_duration_s": 10.0,
        "output_duration_s": 6.4,
        "words_total": 9,
        "words_cut": 0,
        "frame_quantization": {},
        "join_pauses_s": [0.7, 0.9],
        "kept_ranges": [
            {"start": 1.0, "end": 2.6},
            {"start": 3.3, "end": 5.6},
            {"start": 6.6, "end": 9.1},
        ],
        "curation": [],
        "signature": None,
        "post_edits": [],
    }
    profile = {
        "protected_spans": [
            {"word_start": 6, "word_end": 9, "label": "protected moment"}
        ]
    }
    (job / "transcript.json").write_text(json.dumps(transcript))
    (job / "cuts.json").write_text(json.dumps(cuts))
    (job / "profile.json").write_text(json.dumps(profile))
    (job / "review.mp4").write_bytes(VIDEO_BYTES)


class BreathsDirectionParseTests(unittest.TestCase):
    def test_keep_wordings(self):
        for text in (
            "keep the breaths natural",
            "keep natural breaths",
            "leave the breaths",
            "don't cut the breaths",
            "do not trim the breaths",
        ):
            parsed = direction_parse.parse_direction(text)
            self.assertEqual(
                parsed["overrides"].get("breaths"), "keep", text
            )
            self.assertIn(
                "Breaths will be kept natural.", parsed["recap"]
            )

    def test_trim_wordings(self):
        parsed = direction_parse.parse_direction("cut the breaths too")
        self.assertEqual(parsed["overrides"].get("breaths"), "trim")
        self.assertIn(
            "Breaths will be trimmed with the pauses.", parsed["recap"]
        )

    def test_breaths_composes_with_other_direction(self):
        parsed = direction_parse.parse_direction(
            "remove retakes, last take is the best take, "
            "and cut the breaths too"
        )
        self.assertEqual(parsed["overrides"].get("breaths"), "trim")
        self.assertEqual(parsed["overrides"].get("remove_retakes"), True)
        self.assertEqual(parsed["overrides"].get("keep_mode"), "last")


class BreathsTests(ServerCase):
    def build_job(self, job: Path) -> None:
        write_breaths_job(job)

    def revised_ranges(self):
        session = self.job / "tye-session"
        cuts = json.loads(
            (session / "revised_cuts.json").read_text()
        )
        return cuts["kept_ranges"]

    def fire_front_door(self, **overrides):
        body = {
            "remove_retakes": False,
            "cut_silence": True,
            "buffer_s": 0.1,
        }
        body.update(overrides)
        status, payload = self.call("/api/front-door", body)
        self.assertEqual(status, 200, payload)
        return payload

    def test_default_breaths_is_keep(self):
        summary = self.summary()
        self.assertEqual(summary["front_door"]["breaths"], "keep")

    def test_keep_floor_holds_the_same_join(self):
        payload = self.fire_front_door()
        self.assertEqual(payload["settings"]["breaths"], "keep")
        self.assertEqual(
            payload["breaths_recap"], "Breaths are being kept natural."
        )
        self.assertEqual(payload["silence"]["floor_s"], 0.3)
        ranges = self.revised_ranges()
        # Join 0 trimmed toward the floor, not the buffer: the next
        # range now starts 0.3s before its first word (4.0 - 0.3).
        self.assertAlmostEqual(ranges[1]["start"], 3.7, places=4)
        # The protected join is untouched.
        self.assertAlmostEqual(ranges[2]["start"], 6.6, places=4)

    def test_trim_lets_the_buffer_rule_the_same_join(self):
        payload = self.fire_front_door(breaths="trim")
        self.assertEqual(payload["settings"]["breaths"], "trim")
        self.assertEqual(
            payload["breaths_recap"],
            "Breaths are trimmed with the pauses.",
        )
        self.assertIsNone(payload["silence"]["floor_s"])
        ranges = self.revised_ranges()
        # The same join, the same buffer: now the pause is the
        # buffer alone (4.0 - 0.1).
        self.assertAlmostEqual(ranges[1]["start"], 3.9, places=4)
        self.assertAlmostEqual(ranges[2]["start"], 6.6, places=4)
        # The choice persists in the saved front-door settings.
        self.assertEqual(self.summary()["front_door"]["breaths"], "trim")

    def test_same_join_unit_proof_and_protection(self):
        config = WebAppConfig(self.job)
        keep_ops = webapp._silence_operations(config, 0.1, "keep")
        trim_ops = webapp._silence_operations(config, 0.1, "trim")
        keep_targets = {op["join_index"]: op["target_pause_s"]
                        for op in keep_ops}
        trim_targets = {op["join_index"]: op["target_pause_s"]
                        for op in trim_ops}
        self.assertEqual(keep_targets, {0: 0.3})
        self.assertEqual(trim_targets, {0: 0.1})
        # Join 1 borders a protected word: no operation under
        # either setting, which the two dicts above already prove.

    def test_pinned_comment_protects_the_join(self):
        config = WebAppConfig(self.job)
        before = webapp._silence_operations(config, 0.1, "trim")
        self.assertEqual(len(before), 1)
        session = self.job / "tye-session"
        session.mkdir(exist_ok=True)
        (session / "feedback.json").write_text(json.dumps({
            "video": "clip",
            "feedback_done": False,
            "comments": [
                {"id": "c1", "ts": 1.6, "text": "Keep this pause here."}
            ],
            "overall_feedback": [],
        }))
        after = webapp._silence_operations(config, 0.1, "trim")
        self.assertEqual(after, [])

    def test_direction_box_sets_breaths(self):
        payload = self.fire_front_door(
            direction_text="cut the breaths too"
        )
        self.assertEqual(payload["settings"]["breaths"], "trim")
        self.assertEqual(
            payload["direction_recap"],
            ["Breaths will be trimmed with the pauses."],
        )
        ranges = self.revised_ranges()
        self.assertAlmostEqual(ranges[1]["start"], 3.9, places=4)

    def test_breaths_value_is_validated(self):
        status, payload = self.call(
            "/api/front-door",
            {"remove_retakes": False, "breaths": "sometimes"},
        )
        self.assertEqual(status, 400)
        self.assertIn("natural breath", payload["error"])


# ---------------------------------------------------------------------------
# The finish card
# ---------------------------------------------------------------------------


class FinishCardTests(ServerCase):
    def build_job(self, job: Path) -> None:
        copy_sample_job(job)

    def test_finish_waits_for_render_and_review_visit(self):
        summary = self.summary()
        self.assertFalse(summary["finish"]["ready"])
        self.assertFalse(summary["finish"]["has_render"])
        session = self.job / "tye-session"
        session.mkdir(exist_ok=True)
        (session / "revised-review.mp4").write_bytes(VIDEO_BYTES)
        summary = self.summary()
        self.assertTrue(summary["finish"]["has_render"])
        self.assertFalse(summary["finish"]["ready"])
        # A direction run rebuilds the after-review page from the
        # saved notes; visiting it completes the honest signal.
        feedback = load_fixture("sample_feedback.json")
        status, _ = self.call("/api/comments", feedback)
        self.assertEqual(status, 200)
        status, _ = self.call("/api/front-door", {})
        self.assertEqual(status, 200)
        status, _, body = self.raw("/after-review")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body)
        summary = self.summary()
        self.assertTrue(summary["finish"]["ready"])
        self.assertTrue(summary["finish"]["review_visited"])
        status, headers, body = self.raw("/api/finish/video")
        self.assertEqual(status, 200)
        self.assertEqual(body, VIDEO_BYTES)
        self.assertIn(
            "finished-edit.mp4", headers.get("Content-Disposition", "")
        )

    def test_finish_video_without_a_render_is_a_plain_404(self):
        status, payload = self.call("/api/finish/video")
        self.assertEqual(status, 404)
        self.assertIn("No video has been built", payload["error"])

    def test_page_carries_the_finish_card(self):
        status, _, body = self.raw("/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="finishCard"', body)
        self.assertIn(b"Finished edit", body)
        self.assertIn(b"/api/finish/video", body)


class IntakeFinishTests(ServerCase):
    def build_job(self, job: Path) -> None:
        write_breaths_job(job)
        (job / "intake.json").write_text(json.dumps({
            "kind": "intake",
            "source_video": "clip.mp4",
            "captions": True,
        }))
        (job / "clip.mp4").write_bytes(VIDEO_BYTES)
        session = job / "tye-session"
        session.mkdir()
        (session / "revised-review.mp4").write_bytes(VIDEO_BYTES)
        (session / "clean-captions.vtt").write_text(
            "WEBVTT\n\n00:00.000 --> 00:01.000\nOne two three.\n"
        )
        (session / "after-review.html").write_text(
            "<html><body>review</body></html>"
        )

    def test_intake_finish_carries_captions(self):
        status, _, _ = self.raw("/after-review")
        self.assertEqual(status, 200)
        summary = self.summary()
        finish = summary["finish"]
        self.assertTrue(finish["ready"], finish)
        self.assertTrue(finish["is_intake"])
        self.assertTrue(finish["captions_available"])


# ---------------------------------------------------------------------------
# The missing-file rider: plain words everywhere a person can land
# ---------------------------------------------------------------------------


class MissingFileTests(ServerCase):
    def build_job(self, job: Path) -> None:
        # A job folder missing cuts.json: not whole.
        shutil.copyfile(
            FIXTURES / "sample_transcript.json", job / "transcript.json"
        )
        shutil.copyfile(
            FIXTURES / "sample_profile.json", job / "profile.json"
        )
        (job / "review.mp4").write_bytes(VIDEO_BYTES)

    def test_posts_answer_with_the_plain_sentence(self):
        for path, body in (
            ("/api/front-door", {}),
            ("/api/direct", {"provider": "mock"}),
        ):
            status, payload = self.call(path, body)
            self.assertEqual(status, 400, (path, payload))
            self.assertEqual(
                payload["error"], webapp.JOB_INCOMPLETE_MESSAGE
            )

    def test_summary_names_what_is_missing(self):
        summary = self.summary()
        self.assertFalse(summary["job_whole"])
        self.assertEqual(summary["job_missing"], ["cuts.json"])
        self.assertFalse(summary["render_ready"])
        self.assertEqual(
            summary["render_message"], webapp.JOB_INCOMPLETE_MESSAGE
        )

    def test_page_shows_the_banner_not_json(self):
        status, _, body = self.raw("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body)
        self.assertIn(
            webapp.JOB_INCOMPLETE_MESSAGE.encode(), body
        )
        self.assertIn(b"jobIncompleteBanner", body)
        self.assertNotIn(b'{"error"', body)


class BrokenNavigationTests(ServerCase):
    def build_job(self, job: Path) -> None:
        copy_sample_job(job)

    def test_unknown_video_name_is_a_plain_page(self):
        status, _, body = self.raw("/video/ghost.mp4")
        self.assertEqual(status, 404)
        self.assertIn(b"<html", body)
        self.assertNotIn(b'{"error"', body)

    def test_deleted_job_files_never_show_raw_json(self):
        for name in ("transcript.json", "cuts.json", "review.mp4"):
            (self.job / name).unlink()
        status, _, body = self.raw("/review.mp4")
        self.assertEqual(status, 404)
        self.assertIn(b"<html", body)
        self.assertIn(b"not here", body)
        self.assertNotIn(b'{"error"', body)

    def test_unknown_page_is_a_plain_page(self):
        status, _, body = self.raw("/no-such-page")
        self.assertEqual(status, 404)
        self.assertIn(b"<html", body)
        self.assertNotIn(b'{"error"', body)


# ---------------------------------------------------------------------------
# Keys saved on this Mac
# ---------------------------------------------------------------------------


class KeyPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v061-keys-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = mock.patch.dict(
            os.environ, {"HOME": str(self.home)}, clear=False
        )
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("MODEL_API_KEY", None)
        os.environ.pop("ELEVENLABS_API_KEY", None)
        self.job = self.tmp / "job"
        self.job.mkdir()
        copy_sample_job(self.job)
        self.server = create_server(
            WebAppConfig(self.job), host="127.0.0.1", port=0
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

    def key_dir(self) -> Path:
        return self.home / ".talk-to-your-editor"

    def test_save_meta_key_round_trip(self):
        status, payload = request_json(
            self.base, "/api/signin/save",
            {"kind": "meta", "api_key": "sk-meta-test-123"},
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["meta_saved_on_mac"])
        self.assertTrue(payload["meta_signin_set"])
        key_file = self.key_dir() / "meta_key"
        self.assertEqual(key_file.read_text().strip(), "sk-meta-test-123")
        self.assertEqual(
            stat.S_IMODE(key_file.stat().st_mode), 0o600
        )
        self.assertEqual(
            stat.S_IMODE(self.key_dir().stat().st_mode), 0o700
        )
        # The state never carries the key itself.
        status, _, body = request_raw(self.base, "/api/signin")
        self.assertEqual(status, 200)
        self.assertNotIn(b"sk-meta-test-123", body)
        state = json.loads(body)
        self.assertTrue(state["meta_saved_on_mac"])
        # Remove takes the file and the matching session key.
        status, payload = request_json(
            self.base, "/api/signin/remove", {"kind": "meta"}
        )
        self.assertEqual(status, 200, payload)
        self.assertFalse(payload["meta_saved_on_mac"])
        self.assertFalse(payload["meta_signin_set"])
        self.assertFalse(key_file.exists())

    def test_save_elevenlabs_key_round_trip(self):
        status, payload = request_json(
            self.base, "/api/signin/save",
            {"kind": "elevenlabs", "api_key": "el-test-456"},
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["elevenlabs_saved_on_mac"])
        self.assertTrue(payload["elevenlabs_signin_set"])
        key_file = self.key_dir() / "elevenlabs_key"
        self.assertEqual(key_file.read_text().strip(), "el-test-456")
        self.assertEqual(
            self.server.config.elevenlabs_key(), "el-test-456"
        )

    def test_session_only_elevenlabs_key(self):
        status, payload = request_json(
            self.base, "/api/signin",
            {"kind": "elevenlabs", "api_key": "el-session-789"},
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["elevenlabs_signin_set"])
        self.assertFalse(payload["elevenlabs_saved_on_mac"])
        self.assertFalse((self.key_dir() / "elevenlabs_key").exists())

    def test_save_needs_a_key_and_a_kind(self):
        status, payload = request_json(
            self.base, "/api/signin/save",
            {"kind": "meta", "api_key": "  "},
        )
        self.assertEqual(status, 400)
        self.assertIn("Paste the key first", payload["error"])
        status, payload = request_json(
            self.base, "/api/signin/save",
            {"kind": "other", "api_key": "x"},
        )
        self.assertEqual(status, 400)


# ---------------------------------------------------------------------------
# The captions switch: renamed, and living where captions are handed back
# ---------------------------------------------------------------------------


class CaptionsSwitchTests(ServerCase):
    def build_job(self, job: Path) -> None:
        write_breaths_job(job)
        (job / "intake.json").write_text(json.dumps({
            "kind": "intake",
            "source_video": "clip.mp4",
            "captions": True,
        }))
        (job / "clip.mp4").write_bytes(VIDEO_BYTES)
        session = job / "tye-session"
        session.mkdir()
        (session / "clean-captions.vtt").write_text(
            "WEBVTT\n\n00:00.000 --> 00:01.000\nOne two three.\n"
        )

    def test_switch_lives_on_the_delivery_cards_not_intake(self):
        status, _, body = self.raw("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Hide ums and uhs in captions", body)
        self.assertIn(b'id="captionsHideFillers"', body)
        self.assertIn(b'id="finishCaptionsHideFillers"', body)
        self.assertNotIn(b'id="intakeCaptions"', body)
        self.assertNotIn(b"Clean captions", body)

    def test_default_is_on(self):
        summary = self.summary()
        self.assertTrue(summary["captions_hide_fillers"])
        self.assertTrue(summary["is_intake"])

    def test_turning_off_removes_the_file_and_updates_the_marker(self):
        status, payload = self.call(
            "/api/captions-pref", {"hide_fillers": False}
        )
        self.assertEqual(status, 200, payload)
        self.assertFalse(payload["captions_available"])
        session = self.job / "tye-session"
        self.assertFalse((session / "clean-captions.vtt").exists())
        marker = json.loads((self.job / "intake.json").read_text())
        self.assertFalse(marker["captions"])
        self.assertFalse(self.summary()["captions_hide_fillers"])

    def test_turning_on_rebuilds_from_the_kept_words(self):
        self.call("/api/captions-pref", {"hide_fillers": False})
        status, payload = self.call(
            "/api/captions-pref", {"hide_fillers": True}
        )
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["captions_available"])
        vtt = (
            self.job / "tye-session" / "clean-captions.vtt"
        ).read_text()
        self.assertIn("One", vtt)

    def test_pref_needs_a_clear_answer(self):
        status, payload = self.call("/api/captions-pref", {})
        self.assertEqual(status, 400)
        self.assertIn("ums and uhs", payload["error"])


# ---------------------------------------------------------------------------
# Visible progress: page surfaces, dictated states, stage numbers
# ---------------------------------------------------------------------------


class PageSurfaceTests(ServerCase):
    def build_job(self, job: Path) -> None:
        copy_sample_job(job)

    def test_progress_block_and_dictation_states_are_on_the_page(self):
        status, _, body = self.raw("/")
        self.assertEqual(status, 200)
        self.assertIn(b'id="firstEditProgress"', body)
        self.assertIn(b'id="firstEditBar"', body)
        self.assertIn(b"Recording ", body)
        self.assertIn("Writing your words…".encode(), body)
        self.assertIn(b"Save on this Mac", body)
        self.assertIn(b'id="elevenKey"', body)


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class FirstEditProgressTests(unittest.TestCase):
    """The first-edit status carries stage numbers while it works."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v061-prog-"))
        self.launch = self.tmp / "launch home"
        self.launch.mkdir()
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("ELEVENLABS_API_KEY", None)
        os.environ.pop("MODEL_API_KEY", None)
        self.video = self.tmp / "clip.mp4"
        subprocess.run(
            [
                FFMPEG, "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30",
                "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", "6", str(self.video),
            ],
            check=True,
        )
        self.video_bytes = self.video.read_bytes()
        self.server = create_server(
            WebAppConfig(None, launch_home=self.launch),
            host="127.0.0.1",
            port=0,
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

    def drop_video(self):
        status, begun = request_json(
            self.base, "/api/intake/begin", {"name": "clip"}
        )
        self.assertEqual(status, 200, begun)
        intake_id = begun["intake_id"]
        status, _, _ = request_raw(
            self.base,
            f"/api/intake/file?intake={intake_id}&path=clip.mp4",
            raw=self.video_bytes,
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(status, 200)
        status, finished = request_json(
            self.base, "/api/intake/finish", {"intake_id": intake_id}
        )
        self.assertEqual(status, 200, finished)
        self.assertTrue(finished["can_first_edit"])
        return intake_id

    def test_status_reports_stage_numbers(self):
        intake_id = self.drop_video()
        release = threading.Event()

        def blocking_result(wav_path, duration_s=None):
            release.wait(timeout=20)
            words = [
                {"word": "One", "start": 0.5, "end": 0.9},
                {"word": "two", "start": 1.1, "end": 1.5},
                {"word": "three.", "start": 1.7, "end": 2.1},
                {"word": "Four", "start": 3.0, "end": 3.4},
                {"word": "five", "start": 3.6, "end": 4.0},
                {"word": "six.", "start": 4.2, "end": 4.6},
            ]
            return {
                "words": words,
                "segments": speech.segments_from_words(words),
                "language": "en",
                "duration": duration_s or 6.0,
                "engine": "test local route",
            }

        with mock.patch.object(
            speech, "local_available", return_value=True
        ), mock.patch.object(
            speech, "local_transcribe_wav",
            side_effect=blocking_result,
        ):
            status, started = request_json(
                self.base, "/api/intake/first-edit",
                {"intake_id": intake_id, "direction": ""},
            )
            self.assertEqual(status, 200, started)
            self.assertTrue(started["started"])
            seen = None
            deadline = time.time() + 10
            while time.time() < deadline:
                _, state = request_json(
                    self.base,
                    f"/api/intake/status?intake={intake_id}",
                )
                # The transcription stage is the one the stub holds
                # open; earlier stages pass in a blink.
                if (state.get("stage_index") or 0) >= 2:
                    seen = state
                    break
                time.sleep(0.1)
            self.assertIsNotNone(seen, "no numbered stage was reported")
            self.assertEqual(seen["status"], "working")
            self.assertEqual(seen["stage_index"], 2)
            # Retakes, fillers, and silence on by default, H.264
            # source: 4 + 1 + 1 + 1 + 1 = 8 stages (v0.7.0 added
            # the filler pass stage), no watchable-copy stage.
            self.assertEqual(seen["stage_total"], 8)
            self.assertIn("Writing the words down", seen["stage"])
            release.set()
            state = None
            deadline = time.time() + 60
            while time.time() < deadline:
                _, state = request_json(
                    self.base,
                    f"/api/intake/status?intake={intake_id}",
                )
                if state.get("status") in ("done", "failed", "no_words"):
                    break
                time.sleep(0.2)
        self.assertEqual(state["status"], "done", state)
        self.assertEqual(state["stage_index"], 8)
        self.assertEqual(state["stage_total"], 8)


# ---------------------------------------------------------------------------
# The watchable copy: an intake job's HEVC source becomes H.264 at
# intake, and the splice render always encodes H.264 + AAC.
# ---------------------------------------------------------------------------


class PlaybackLadderTests(unittest.TestCase):
    """The conversion ladder, unit-level: encoder preference and
    the capped encode arguments, and no route when neither
    encoder exists on the machine."""

    def test_videotoolbox_is_preferred_when_present(self):
        with mock.patch.object(
            webapp, "_ffmpeg_encoders",
            return_value=frozenset({"h264_videotoolbox", "libx264"}),
        ):
            attempts = webapp._playback_attempts()
        self.assertIn("h264_videotoolbox", attempts[0][1])
        self.assertIn("libx264", attempts[1][1])

    def test_no_encoder_no_attempts(self):
        with mock.patch.object(
            webapp, "_ffmpeg_encoders", return_value=frozenset()
        ):
            self.assertEqual(webapp._playback_attempts(), [])
            tmp = Path(tempfile.mkdtemp(prefix="tye-v061-ladder-"))
            try:
                self.assertFalse(
                    webapp._convert_playback_copy(
                        tmp / "in.mov", tmp / "out.mp4"
                    )
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    def test_libx264_ladder_arguments(self):
        captured = {}

        class Done:
            returncode = 0
            stderr = ""

        def fake_run(command, **kwargs):
            captured["command"] = command
            Path(command[-1]).write_bytes(b"x")
            return Done()

        with mock.patch.object(
            webapp, "_ffmpeg_encoders",
            return_value=frozenset({"libx264"}),
        ), mock.patch.object(webapp.subprocess, "run", fake_run):
            tmp = Path(tempfile.mkdtemp(prefix="tye-v061-ladder-"))
            try:
                ok = webapp._convert_playback_copy(
                    tmp / "in.mov", tmp / "out.mp4"
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        self.assertTrue(ok)
        command = captured["command"]
        joined = " ".join(command)
        self.assertIn("scale=-2:min(720\\,ih)", joined)
        self.assertIn("veryfast", command)
        self.assertIn("24", command)  # CRF 24
        self.assertIn("5M", command)  # maxrate ceiling
        self.assertIn("128k", command)  # AAC at 128k
        self.assertIn("libx264", command)


@unittest.skipUnless(HEVC, "libx265 encoder not available")
class WatchableCopyTests(unittest.TestCase):
    """Realistic scale, per the playback research: a 65-second
    1080p HEVC clip, the shape of a phone original in miniature.
    The copy must be pre-built by the drop finishing (in the
    background, before any play request), capped at 720p, and be
    what /video/ serves; a failed conversion must never be
    answered with the raw file."""

    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = Path(tempfile.mkdtemp(prefix="tye-v061-hevc-"))
        cls.source = cls.fixture_dir / "IMG_9999.MOV"
        subprocess.run(
            [
                FFMPEG, "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=30",
                "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", "65",
                "-c:v", "libx265", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-tag:v", "hvc1",
                str(cls.source),
            ],
            check=True,
        )
        cls.source_bytes = cls.source.read_bytes()
        assert webapp._probe_video_codec(cls.source) == "hevc"
        info = speech.probe_media(cls.source)
        assert info["height"] == 1080 and info["duration"] >= 60

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fixture_dir, ignore_errors=True)

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v061-watch-"))
        self.launch = self.tmp / "launch home"
        self.launch.mkdir()
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("ELEVENLABS_API_KEY", None)
        os.environ.pop("MODEL_API_KEY", None)
        self.server = create_server(
            WebAppConfig(None, launch_home=self.launch),
            host="127.0.0.1",
            port=0,
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

    def test_splice_render_encodes_h264_aac_from_hevc(self):
        out = self.tmp / "render.mp4"
        cuts = {"kept_ranges": [{"start": 0.5, "end": 2.5}]}
        intake_build.render_splice(self.source, cuts, out)
        info = speech.probe_media(out)
        self.assertEqual(info["video_codec"], "h264")
        self.assertTrue(info["has_audio"])
        audio_codec = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_name",
                "-of", "csv=p=0", str(out),
            ],
            capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(audio_codec, "aac")

    def drop_source(self):
        status, begun = request_json(
            self.base, "/api/intake/begin", {"name": "IMG_9999"}
        )
        self.assertEqual(status, 200, begun)
        intake_id = begun["intake_id"]
        status, _, _ = request_raw(
            self.base,
            f"/api/intake/file?intake={intake_id}"
            "&path=IMG_9999.MOV",
            raw=self.source_bytes,
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(status, 200)
        status, finished = request_json(
            self.base, "/api/intake/finish", {"intake_id": intake_id}
        )
        self.assertEqual(status, 200, finished)
        self.assertTrue(finished["can_first_edit"])
        return intake_id, finished

    def test_copy_is_prebuilt_at_drop_and_served(self):
        intake_id, finished = self.drop_source()
        self.assertIn(
            finished["playback"], ("building", "unstarted", "ready")
        )
        # No play request has been made; the copy must still
        # arrive on its own, in the background.
        copies = []
        deadline = time.time() + 240
        while time.time() < deadline:
            copies = list(
                self.launch.rglob("*.playback.mp4")
            )
            ready = [
                p for p in copies
                if p.is_file() and p.stat().st_size > 0
                and ".part" not in p.name
            ]
            if ready:
                copies = ready
                break
            time.sleep(1.0)
        self.assertTrue(copies, "the watchable copy was never built")
        copy_path = copies[0]
        self.assertEqual(
            webapp._probe_video_codec(copy_path), "h264"
        )
        info = speech.probe_media(copy_path)
        self.assertLessEqual(info["height"], 720)
        # The intake status says it is ready, too.
        _, state = request_json(
            self.base, f"/api/intake/status?intake={intake_id}"
        )
        self.assertEqual(state.get("playback"), "ready")
        # And once the job exists, /video/ serves that very copy,
        # never the HEVC original, which stays byte-identical.
        def fake_result(wav_path, duration_s=None):
            words = [
                {"word": "One", "start": 0.5, "end": 0.9},
                {"word": "two", "start": 1.1, "end": 1.5},
                {"word": "three.", "start": 1.7, "end": 2.1},
                {"word": "Four", "start": 3.0, "end": 3.4},
                {"word": "five", "start": 3.6, "end": 4.0},
                {"word": "six.", "start": 4.2, "end": 4.6},
            ]
            return {
                "words": words,
                "segments": speech.segments_from_words(words),
                "language": "en",
                "duration": duration_s or 65.0,
                "engine": "test local route",
            }

        with mock.patch.object(
            speech, "local_available", return_value=True
        ), mock.patch.object(
            speech, "local_transcribe_wav", side_effect=fake_result,
        ):
            status, started = request_json(
                self.base, "/api/intake/first-edit",
                {"intake_id": intake_id, "direction": ""},
            )
            self.assertEqual(status, 200, started)
            state = None
            deadline = time.time() + 120
            while time.time() < deadline:
                _, state = request_json(
                    self.base,
                    f"/api/intake/status?intake={intake_id}",
                )
                if state.get("status") in ("done", "failed", "no_words"):
                    break
                time.sleep(0.2)
        self.assertEqual(state["status"], "done", state)
        config = self.server.config
        original = config.job_dir / "IMG_9999.MOV"
        self.assertEqual(original.read_bytes(), self.source_bytes)
        job_copy = webapp._playback_copy_path(config, "IMG_9999.MOV")
        self.assertEqual(job_copy, copy_path)
        status, _, body = request_raw(self.base, "/video/IMG_9999.MOV")
        self.assertEqual(status, 200)
        self.assertEqual(body, copy_path.read_bytes())
        _, summary = request_json(self.base, "/api/job")
        self.assertEqual(summary["playback"]["IMG_9999.MOV"], "ready")

    def test_failed_conversion_never_serves_the_original(self):
        job = self.tmp / "job"
        job.mkdir()
        copy_sample_job(job)
        small = self.tmp / "small.mov"
        subprocess.run(
            [
                FFMPEG, "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30",
                "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", "3",
                "-c:v", "libx265", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-tag:v", "hvc1",
                str(small),
            ],
            check=True,
        )
        (job / "clip.MOV").write_bytes(small.read_bytes())
        original = (job / "clip.MOV").read_bytes()
        # A machine with no H.264 encoder at all: every conversion
        # fails, and the answer must be the plain sentence, twice,
        # never the raw file cached as the thing to serve.
        with mock.patch.object(
            webapp, "_ffmpeg_encoders", return_value=frozenset()
        ):
            server = create_server(
                WebAppConfig(job), host="127.0.0.1", port=0
            )
            thread = threading.Thread(
                target=server.serve_forever, daemon=True
            )
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                state = None
                deadline = time.time() + 30
                while time.time() < deadline:
                    _, summary = request_json(base, "/api/job")
                    state = summary["playback"].get("clip.MOV")
                    if state == "failed":
                        break
                    time.sleep(0.2)
                self.assertEqual(state, "failed", summary)
                for _ in range(2):
                    status, _, body = request_raw(
                        base, "/video/clip.MOV"
                    )
                    self.assertEqual(status, 503)
                    self.assertIn(
                        b"could not be made watchable", body
                    )
                    self.assertNotEqual(body, original)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
