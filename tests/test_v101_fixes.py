"""Tests for the v0.10.1 fix round, from Justin's first 0.10 test.

His test stopped at writing the words down: ElevenLabs Scribe
refused the recording and the page answered with a doubled
sentence that named no reason, while the problem report held no
log at all. These tests pin the six fixes:

1. A provider refusal is read in the shapes the providers
   actually send (a detail object, a detail list, plain text),
   and the page sentence names the plain reason class.
2. Front-door failures land in the front door's session log,
   and a no-job problem report carries that log (acceptance).
3. A run reads the key from the same saved file Settings
   writes, and the sign-in check can test the ElevenLabs key.
4. The problem report's name on the page is a working reveal
   control (the Finder part is Mac-only; the endpoint and its
   checks are proven here).
5. The Settings pop up scrolls inside itself: opaque
   background, contained overscroll, the page locked while it
   is open, and no scrollIntoView reaching out to the page.
6. The money total says what it covers, and the reset clears
   it fully.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import costs, session_log  # noqa: E402
from talk_to_your_editor import transcribe as speech  # noqa: E402
from talk_to_your_editor import webapp  # noqa: E402
from talk_to_your_editor.webapp import WebAppConfig, create_server  # noqa: E402

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")

FFMPEG = shutil.which("ffmpeg")


def request_json(base, path, data=None, raw=None, headers=None):
    hdrs = dict(headers or {})
    payload = raw
    if data is not None:
        payload = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base + path, data=payload, headers=hdrs,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class ServerCase(unittest.TestCase):
    """A no-job server over a temp home and a spaced launch home."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v101-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.launch = self.tmp / "launch home"
        self.launch.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)},
                              clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in ("ELEVENLABS_API_KEY", "MODEL_API_KEY",
                     "BASENTEN_API_KEY", "BASENTEN_API_KEY",
                     "FIREWORKS_API_KEY"):
            os.environ.pop(name, None)
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


# ---------------------------------------------------------------------------
# 1 + 3: the provider's answer is read, and named in plain words
# ---------------------------------------------------------------------------


class ScribeErrorShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        import wave

        self.wav = Path(tempfile.mkdtemp(prefix="tye-v101-wav-")) / "a.wav"
        with wave.open(str(self.wav), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 1600)

    def transcribe_with(self, post_result):
        with mock.patch.object(speech, "_post",
                               return_value=post_result):
            with self.assertRaises(speech.TranscribeError) as ctx:
                speech.scribe_transcribe_wav(self.wav, "sk_fake")
        return ctx.exception

    def test_401_detail_object_names_the_key(self):
        exc = self.transcribe_with((401, {
            "detail": {
                "status": "invalid_api_key",
                "message": "Invalid API key",
            }
        }))
        self.assertIn("did not accept the key", str(exc))
        self.assertNotIn("could not take that recording", str(exc))
        self.assertIn("HTTP 401", exc.detail)
        self.assertIn("Invalid API key", exc.detail)

    def test_403_string_detail_names_the_key(self):
        exc = self.transcribe_with((403, {"detail": "bad key"}))
        self.assertIn("did not accept the key", str(exc))
        self.assertIn("bad key", exc.detail)

    def test_400_detail_list_attaches_the_providers_words(self):
        exc = self.transcribe_with((400, {
            "detail": [{"msg": "file is not valid audio"}]
        }))
        self.assertIn("refused that recording", str(exc))
        self.assertIn("file is not valid audio", str(exc))

    def test_422_without_words_still_plain(self):
        exc = self.transcribe_with((422, {"detail": {}}))
        self.assertIn("refused that recording", str(exc))
        self.assertIn("did not say why", str(exc))

    def test_500_raw_text_is_a_problem_on_its_side(self):
        exc = self.transcribe_with((500, "upstream exploded"))
        self.assertIn("problem on its side", str(exc))
        self.assertIn("upstream exploded", exc.detail)

    def test_429_names_the_limit(self):
        exc = self.transcribe_with((429, {"detail": "slow down"}))
        self.assertIn("at its limit", str(exc))

    def test_200_without_a_transcript_is_named(self):
        exc = self.transcribe_with((200, ["not", "a", "dict"]))
        self.assertIn("could not read", str(exc))

    def test_network_failure_says_could_not_reach(self):
        # _post goes through an opener, not urlopen; failing the
        # opener's open is the network failing.
        with mock.patch.object(
            urllib.request.OpenerDirector, "open",
            side_effect=urllib.error.URLError("no route"),
        ):
            with self.assertRaises(speech.TranscribeError) as ctx:
                speech.scribe_transcribe_wav(self.wav, "sk_fake")
        self.assertIn("could not reach the speech service",
                      str(ctx.exception))

    def test_voice_route_uses_its_own_name(self):
        def fake_post(url, body, headers, **kwargs):
            return 401, {"detail": {"message": "bad key"}}

        with self.assertRaises(speech.TranscribeError) as ctx:
            speech.meta_transcribe_wav(
                self.wav, "meta-key", post=fake_post
            )
        self.assertIn("did not accept the Meta sign-in",
                      str(ctx.exception))


# ---------------------------------------------------------------------------
# 1: the saved key and the used key are the same file
# ---------------------------------------------------------------------------


class KeyFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v101-key-"))
        self.home = self.tmp / "home"
        (self.home / ".talk-to-your-editor").mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)},
                              clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in ("ELEVENLABS_API_KEY", "BASENTEN_API_KEY",
                     "BASENTEN_API_KEY", "FIREWORKS_API_KEY"):
            os.environ.pop(name, None)
        self.config = WebAppConfig(None, launch_home=self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, kind, value):
        path = self.home / ".talk-to-your-editor" / f"{kind}_key"
        path.write_text(value + "\n", encoding="utf-8")

    def test_saved_file_is_used_when_nothing_else_is_set(self):
        self.save("elevenlabs", "sk_saved")
        self.assertEqual(self.config.elevenlabs_key(), "sk_saved")

    def test_environment_still_wins_over_the_file(self):
        self.save("elevenlabs", "sk_saved")
        os.environ["ELEVENLABS_API_KEY"] = "sk_env"
        self.assertEqual(self.config.elevenlabs_key(), "sk_env")

    def test_session_still_wins_over_both(self):
        self.save("elevenlabs", "sk_saved")
        os.environ["ELEVENLABS_API_KEY"] = "sk_env"
        self.config.session_elevenlabs_key = "sk_session"
        self.assertEqual(self.config.elevenlabs_key(), "sk_session")

    def test_hosted_keys_fall_back_too(self):
        self.save("baseten", "bt_saved")
        self.save("fireworks", "fw_saved")
        self.assertEqual(self.config.baseten_key(), "bt_saved")
        self.assertEqual(self.config.fireworks_key(), "fw_saved")


class ElevenLabsCheckTests(ServerCase):
    def test_no_key_means_no_check(self):
        self.assertIsNone(webapp._check_elevenlabs(self.server.config))

    def test_accepted_key(self):
        self.server.config.session_elevenlabs_key = "sk_x"
        with mock.patch.object(
            webapp, "_elevenlabs_get", return_value=(200, {})
        ):
            result = webapp._check_elevenlabs(self.server.config)
        self.assertTrue(result["accepted"])
        self.assertIn("accepted", result["message"])

    def test_refused_key(self):
        self.server.config.session_elevenlabs_key = "sk_x"
        with mock.patch.object(
            webapp, "_elevenlabs_get", return_value=(401, {})
        ):
            result = webapp._check_elevenlabs(self.server.config)
        self.assertFalse(result["accepted"])
        self.assertIn("was not accepted", result["message"])

    def test_unreachable(self):
        self.server.config.session_elevenlabs_key = "sk_x"
        with mock.patch.object(
            webapp, "_elevenlabs_get", return_value=(0, None)
        ):
            result = webapp._check_elevenlabs(self.server.config)
        self.assertFalse(result["accepted"])
        self.assertIn("could not reach", result["message"])

    def test_signin_check_endpoint_includes_elevenlabs(self):
        self.server.config.session_elevenlabs_key = "sk_x"
        with mock.patch.object(
            webapp, "_elevenlabs_get", return_value=(200, {})
        ):
            status, data = request_json(
                self.base, "/api/signin/check", {}
            )
        self.assertEqual(status, 200)
        self.assertTrue(data["elevenlabs"]["accepted"])


# ---------------------------------------------------------------------------
# 2: the acceptance test. A front-door transcription failure, then
# the problem report, with no job open: the zip must hold the log.
# ---------------------------------------------------------------------------


@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class FrontDoorFailureReportTests(ServerCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = Path(tempfile.mkdtemp(prefix="tye-v101-fix-"))
        video = cls.fixture_dir / "raw take.mp4"
        subprocess.run(
            [
                FFMPEG, "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30",
                "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", "4", str(video),
            ],
            check=True,
        )
        cls.video_bytes = video.read_bytes()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fixture_dir, ignore_errors=True)

    def test_failure_lands_in_the_report_log(self):
        os.environ["ELEVENLABS_API_KEY"] = "sk_fake_key"
        status, begun = request_json(
            self.base, "/api/intake/begin", {"name": "raw take"}
        )
        self.assertEqual(status, 200)
        intake_id = begun["intake_id"]
        status, _ = request_json(
            self.base,
            f"/api/intake/file?intake={intake_id}"
            "&path=raw%20take.mp4",
            raw=self.video_bytes,
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(status, 200)
        status, finished = request_json(
            self.base, "/api/intake/finish", {"intake_id": intake_id}
        )
        self.assertEqual(status, 200)
        self.assertTrue(finished["can_first_edit"])

        refusal = (401, {
            "detail": {
                "status": "invalid_api_key",
                "message": "Invalid API key",
            }
        })
        with mock.patch.object(speech, "_post", return_value=refusal):
            status, started = request_json(
                self.base, "/api/intake/first-edit",
                {"intake_id": intake_id, "direction": "",
                 "clean_captions": True},
            )
            self.assertEqual(status, 200)
            self.assertTrue(started["started"])
            deadline = time.time() + 60
            state = {}
            while time.time() < deadline:
                _, state = request_json(
                    self.base,
                    f"/api/intake/status?intake={intake_id}",
                )
                if state.get("status") in ("done", "failed",
                                           "no_words"):
                    break
                time.sleep(0.2)
        # The page line names the plain reason, never the doubled
        # sentence from 0.10.0.
        self.assertEqual(state["status"], "failed", state)
        self.assertIn("did not accept the key", state["message"])
        self.assertNotIn("could not take that recording",
                         state["message"])

        # The problem report, still with no job open, carries the
        # session log, and the log carries the failure with the
        # provider's own words in it.
        status, data = request_json(self.base, "/api/problem-report", {})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        zip_path = self.launch / data["file"]
        self.assertTrue(zip_path.is_file())
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            self.assertIn(session_log.SESSION_LOG_NAME, names)
            log_text = zf.read(
                session_log.SESSION_LOG_NAME
            ).decode("utf-8")
        self.assertIn("first edit", log_text)
        self.assertIn("fail", log_text)
        self.assertIn("HTTP 401", log_text)
        self.assertIn("Invalid API key", log_text)
        # The fake key never reaches the report.
        self.assertNotIn("sk_fake_key", log_text)


# ---------------------------------------------------------------------------
# 4: the report name is a working reveal control
# ---------------------------------------------------------------------------


class RevealTests(ServerCase):
    def test_reveal_calls_the_finder_seam_with_the_zip(self):
        name = "problem-report-no-job-2026-10-28-9999.zip"
        (self.launch / name).write_bytes(b"PK\x03\x04")
        seen = {}

        def fake_reveal(path):
            seen["path"] = path
            return True, "Finder is showing you the file."

        with mock.patch.object(webapp, "_reveal_in_finder",
                               side_effect=fake_reveal):
            status, data = request_json(
                self.base, "/api/problem-report/reveal",
                {"file": name},
            )
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(seen["path"], self.launch / name)

    def test_reveal_refuses_other_names(self):
        status, _ = request_json(
            self.base, "/api/problem-report/reveal",
            {"file": "../money.json"},
        )
        self.assertEqual(status, 400)

    def test_reveal_names_a_missing_file(self):
        status, _ = request_json(
            self.base, "/api/problem-report/reveal",
            {"file": "problem-report-no-job-2026-01-01-0000.zip"},
        )
        self.assertEqual(status, 404)

    @unittest.skipIf(sys.platform == "darwin", "Mac-only behavior")
    def test_off_a_mac_the_answer_names_the_folder(self):
        path = self.launch / "problem-report-x.zip"
        opened, message = webapp._reveal_in_finder(path)
        self.assertFalse(opened)
        self.assertIn(str(self.launch), message)

    def test_page_renders_the_name_as_a_button(self):
        self.assertIn("problemReportReveal", TEMPLATE)
        self.assertIn("/api/problem-report/reveal", TEMPLATE)


# ---------------------------------------------------------------------------
# 5: the pop up scrolls inside itself
# ---------------------------------------------------------------------------


class PopupScrollTests(unittest.TestCase):
    def test_popup_background_is_opaque(self):
        self.assertIn(".popup-panel {", TEMPLATE)
        panel_rule = TEMPLATE.split(".popup-panel {", 1)[1].split("}", 1)[0]
        self.assertIn("background: #1c1c1e", panel_rule)

    def test_overscroll_is_contained(self):
        panel_rule = TEMPLATE.split(".popup-panel {", 1)[1].split("}", 1)[0]
        self.assertIn("overscroll-behavior: contain", panel_rule)

    def test_page_locks_while_a_popup_is_open(self):
        self.assertIn("body.popup-open { overflow: hidden; }", TEMPLATE)
        self.assertIn('classList.toggle("popup-open"', TEMPLATE)

    def test_money_jump_scrolls_the_panel_not_the_page(self):
        fn = TEMPLATE.split("function openSettingsAtMoney()", 1)[1]
        fn = fn.split("\n  }", 1)[0]
        self.assertNotIn(".scrollIntoView(", fn)
        self.assertIn("settingsPanel.scrollTop", fn)


# ---------------------------------------------------------------------------
# 6: the money total says what it covers; the reset clears it
# ---------------------------------------------------------------------------


class MoneyHonestyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v101-money-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)},
                              clear=False)
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_view_counts_the_runs_it_covers(self):
        entries = [
            {"feature": "retake_judge", "pool": "meta",
             "label": "Retake check", "usd": 0.97, "job": "job one"},
            {"feature": "retake_judge", "pool": "meta",
             "label": "Retake check", "usd": 0.10, "job": "job one"},
        ]
        view = costs.money_view([], entries)
        self.assertEqual(view["mac"]["entries"], 2)
        self.assertAlmostEqual(view["mac"]["total_usd"], 1.07)

    def test_reset_clears_the_mac_total_fully(self):
        costs.append_money_entries(
            [{"feature": "retake_judge", "pool": "meta",
              "label": "Retake check", "usd": 0.5}], "job one"
        )
        self.assertEqual(len(costs.load_money_entries()), 1)
        costs.reset_money()
        self.assertEqual(costs.load_money_entries(), [])

    def test_words_say_what_the_total_covers(self):
        self.assertIn("every run saved on this computer", TEMPLATE)
        self.assertIn("earlier copies of the app", TEMPLATE)


if __name__ == "__main__":
    unittest.main()
