"""Tests for the Studio reset's creator-level store (2026-11-01).

Approved by Justin as the one Python change beyond the /studio route:
saved rules and the framing profile live in one creator-level place
in the launch home, read and written by every job and by Train with
no job loaded. Rules already saved inside existing job folders merge
in on first run, so nothing saved before is lost.

Covers:
(i)   /studio serves Studio, /classic serves the old page, and /
      serves Studio (the page that opens on launch);
(ii)  a teaching suggestion can be saved with no job loaded, and the
      rule lands in launch_home/creator_rules.json, not in a session;
(iii) a job loaded afterwards, and a second job after that, both see
      the rule through /api/job;
(iv)  rules and a framing profile saved inside an existing job
      folder under the launch home merge into the creator store on
      first use, and a rule removed afterwards does not come back
      from the legacy file;
(v)   studio.html carries no speech output.
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

from talk_to_your_editor import creator_rules, teaching, webapp
from talk_to_your_editor.webapp import WebAppConfig, create_server

TEMPLATES = ROOT / "src" / "talk_to_your_editor" / "templates"


def make_job(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    transcript = {
        "engine": "test",
        "duration": 1.0,
        "words": [{"word": "hi", "start": 0.0, "end": 0.9}],
    }
    (folder / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (folder / "cuts.json").write_text(
        json.dumps(
            {
                "source_duration_s": 1.0,
                "kept_ranges": [{"start": 0.0, "end": 1.0}],
            }
        ),
        encoding="utf-8",
    )
    return folder


class ServerCase(unittest.TestCase):
    """A real HTTP server with a temp HOME and a temp launch home."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-studio-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        for key in ("MODEL_API_KEY", "ELEVENLABS_API_KEY"):
            os.environ.pop(key, None)
        self.launch = self.tmp / "launch"
        self.launch.mkdir()
        self.server = None

    def tearDown(self) -> None:
        self.stop()
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, config: WebAppConfig) -> str:
        self.stop()
        self.server = create_server(config, host="127.0.0.1", port=0)
        port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        return f"http://127.0.0.1:{port}"

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def request(self, base: str, path: str, data=None):
        headers = {}
        payload = None
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            base + path,
            data=payload,
            headers=headers,
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def post_json(self, base: str, path: str, data: dict):
        status, body = self.request(base, path, data)
        return status, json.loads(body)


class StudioRoutesTest(ServerCase):
    def test_studio_is_the_launch_page_and_classic_survives(self):
        # Message 2R: the launch page opens on the conversation;
        # teaching lives in the chat (the Train screen's markup is
        # still in the page, reached from the chat, headed "Teach
        # me your style"). The classic page is untouched.
        base = self.start(WebAppConfig(None, launch_home=self.launch))
        status, page = self.request(base, "/studio")
        self.assertEqual(status, 200)
        self.assertIn('id="screenHome"', page)
        self.assertIn("Teach me your style", page)
        status, root = self.request(base, "/")
        self.assertEqual(status, 200)
        self.assertIn('id="screenHome"', root)
        status, classic = self.request(base, "/classic")
        self.assertEqual(status, 200)
        self.assertNotIn('id="screenHome"', classic[:2000])

    def test_studio_has_no_speech_output(self):
        # The no-speech rule is a hard gate and stays strict. The
        # brief's under-1,500-lines page target is not asserted:
        # the brief itself required porting the classic transcript
        # and timeline into the one file, which passed the target;
        # the overage is reported to Justin as a standing gap.
        page = (TEMPLATES / "studio.html").read_text(encoding="utf-8")
        self.assertNotIn("speechSynthesis", page)
        self.assertNotIn("SpeechSynthesisUtterance", page)


class CreatorStoreTest(ServerCase):
    def seed_teaching_draft(self, statement: str) -> dict:
        config = WebAppConfig(None, launch_home=self.launch)
        drafts = teaching.add_drafts(
            config.session_dir,
            [
                teaching.make_draft(
                    statement=statement,
                    reason="From the pair.",
                    at_s=None,
                    quote="",
                    basis="pair",
                    kind="mine",
                    source_note=teaching.PAIR_SOURCE_NOTE,
                )
            ],
        )
        return drafts[-1]

    def test_save_with_no_job_lands_in_the_creator_store(self):
        draft = self.seed_teaching_draft(
            "Filler words like um and uh are cut out."
        )
        config = WebAppConfig(None, launch_home=self.launch)
        base = self.start(config)
        # The no-job summary already offers the draft, unsaved.
        status, body = self.request(base, "/api/job")
        summary = json.loads(body)
        self.assertEqual(summary["saved_rules"], [])
        offered = [
            s["pattern_key"] for s in summary["rule_suggestions"]
        ]
        self.assertIn(draft["pattern_key"], offered)
        # Save it with no job loaded: the whole point of the change.
        status, saved = self.post_json(
            base, "/api/rule-save", {"pattern_key": draft["pattern_key"]}
        )
        self.assertEqual(status, 200)
        self.assertTrue(saved["ok"])
        creator_file = self.launch / "creator_rules.json"
        self.assertTrue(creator_file.is_file())
        store = creator_rules.load_rules_store(creator_file)
        statements = creator_rules.active_rule_statements(store)
        self.assertEqual(
            statements, ["Filler words like um and uh are cut out."]
        )
        # Nothing was written into the launch session's own store.
        self.assertFalse(
            (config.session_dir / "creator_rules.json").is_file()
        )

    def test_rule_saved_with_no_job_is_seen_by_later_jobs(self):
        draft = self.seed_teaching_draft(
            "Earlier tries at a line are cut; the last try stays."
        )
        base = self.start(WebAppConfig(None, launch_home=self.launch))
        status, _saved = self.post_json(
            base, "/api/rule-save", {"pattern_key": draft["pattern_key"]}
        )
        self.assertEqual(status, 200)
        self.stop()
        for name in ("job-a", "job-b"):
            job = make_job(self.tmp / name)
            base = self.start(
                WebAppConfig(job, launch_home=self.launch)
            )
            status, body = self.request(base, "/api/job")
            summary = json.loads(body)
            statements = [
                rule["statement"] for rule in summary["saved_rules"]
            ]
            self.assertIn(
                "Earlier tries at a line are cut; the last try stays.",
                statements,
            )
            self.stop()

    def test_legacy_job_rules_and_framing_merge_on_first_use(self):
        old_job_session = (
            self.launch / "intake" / "old-job" / "tye-session"
        )
        old_job_session.mkdir(parents=True)
        legacy_store = {
            "rules": [
                {
                    "rule_id": "rule-legacy",
                    "pattern_key": "legacy-cut",
                    "statement": "A rule saved in an old job.",
                    "active": True,
                    "source": "teaching",
                }
            ],
            "dismissed": {},
        }
        (old_job_session / "creator_rules.json").write_text(
            json.dumps(legacy_store), encoding="utf-8"
        )
        teaching.save_framing_profile(
            old_job_session,
            {
                "status": "ok", "found": True,
                "source_width": 360, "source_height": 640,
                "face": {"width": 0.4},
            },
        )
        config = WebAppConfig(None, launch_home=self.launch)
        store = webapp._rules_store(config)
        statements = creator_rules.active_rule_statements(store)
        self.assertIn("A rule saved in an old job.", statements)
        profile = teaching.load_framing_profile(self.launch, "9:16")
        self.assertIsNotNone(profile)
        self.assertTrue(profile["found"])
        self.assertIsNone(
            teaching.load_framing_profile(self.launch, "16:9")
        )
        # Removing the merged rule sticks: the legacy file is merged
        # once, so the rule does not come back on the next load.
        store = webapp._rules_store(config)
        creator_rules.remove_rule(store, "rule-legacy")
        webapp._save_rules_store(config, store)
        store = webapp._rules_store(config)
        self.assertNotIn(
            "A rule saved in an old job.",
            creator_rules.active_rule_statements(store),
        )


class FramingPerShapeTest(unittest.TestCase):
    def test_both_shapes_live_side_by_side(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-framing-shapes-"))
        try:
            tall = {
                "status": "ok", "found": True,
                "source_width": 360, "source_height": 640,
                "face": {"center_x": 0.5, "center_y": 0.4,
                         "width": 0.5, "height": 0.3},
            }
            wide = {
                "status": "ok", "found": True,
                "source_width": 640, "source_height": 360,
                "face": {"center_x": 0.4, "center_y": 0.5,
                         "width": 0.3, "height": 0.5},
            }
            teaching.save_framing_profile(tmp, tall)
            teaching.save_framing_profile(tmp, wide)
            got_tall = teaching.load_framing_profile(tmp, "9:16")
            got_wide = teaching.load_framing_profile(tmp, "16:9")
            self.assertEqual(got_tall["face"]["width"], 0.5)
            self.assertEqual(got_wide["face"]["width"], 0.3)
            self.assertTrue((tmp / "framing_profile_9x16.json").is_file())
            self.assertTrue((tmp / "framing_profile_16x9.json").is_file())
            # Saving one shape again leaves the other untouched.
            tall2 = dict(tall)
            tall2["face"] = dict(tall["face"], width=0.6)
            teaching.save_framing_profile(tmp, tall2)
            self.assertEqual(
                teaching.load_framing_profile(tmp, "16:9")["face"]["width"],
                0.3,
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
