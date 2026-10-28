"""Tests for v0.10 item 1: skills become files, starter styles.

Covers the pack format and locations, the three starter styles
(Justin's way byte-identical to the built-in constants it came
from), the provider loading packs at construction with the
built-in fallback, the versions record on the direction and
retake run records, the direction recap's know-how line, and the
front door style pick through the real test server.

No live model call is made anywhere: provider system strings are
captured through a stubbed ``_chat``, the direction pass runs on
a capturing stand-in, and the server runs on the mock provider.
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

from talk_to_your_editor import skills, usual, webapp
from talk_to_your_editor.meta_provider import (
    DIRECTION_PASS_FLAGS,
    DIRECTION_PASS_PAUSES,
    DIRECTION_PASS_RULES,
    RETAKE_CALIBRATION,
    SCHEMA_SPEC,
    MetaProvider,
    MockProvider,
)
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig, create_server

FIXTURES = Path(__file__).resolve().parent / "fixtures"

STYLE_COPY = (
    "If you pick nothing, this edit uses Justin's way. "
    "Your own saved rules win over the style when they disagree."
)


def write_job(job: Path, words: list) -> None:
    job.mkdir(parents=True, exist_ok=True)
    transcript = {
        "engine": "test",
        "duration": float(words[-1]["end"]) if words else 0.0,
        "words": words,
    }
    cuts = {
        "source_duration_s": transcript["duration"],
        "kept_ranges": [{"start": 0.0, "end": transcript["duration"]}],
    }
    (job / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")


def spaced_words(tokens: list) -> list:
    return [
        {"word": token, "start": float(i), "end": float(i) + 0.9}
        for i, token in enumerate(tokens)
    ]


def sample_timeline() -> Timeline:
    transcript = json.loads(
        (FIXTURES / "sample_transcript.json").read_text(encoding="utf-8")
    )
    cuts = json.loads(
        (FIXTURES / "sample_cuts.json").read_text(encoding="utf-8")
    )
    return Timeline(transcript, cuts)


def capture_direction_system(provider) -> str:
    """The system string a direction pass would send, via a
    stubbed ``_chat`` that answers with an empty, valid reply."""
    seen = []

    def fake_chat(model, messages):
        seen.append(messages)
        return '{"operations": [], "flags": []}'

    provider._chat = fake_chat
    provider.direct_transcript(
        {"direction": "keep it tight"}, sample_timeline(), None
    )
    return seen[0][0]["content"]


def capture_retake_system(provider) -> str:
    """The system string the retake judge would send."""
    seen = []

    def fake_chat(model, messages):
        seen.append(messages)
        return '{"verdicts": []}'

    provider._chat = fake_chat
    pair = {
        "pair_id": "p1",
        "cut": {"word_start": 0, "word_end": 2, "text": "one two"},
        "keep": {"word_start": 3, "word_end": 5, "text": "one two"},
        "coverage": 0.9,
        "ratio": 1.0,
        "shape": "same",
        "cut_complete": True,
        "keep_complete": True,
    }
    provider.judge_retakes([pair], sample_timeline(), guidance=[])
    return seen[0][0]["content"]


class IsolatedHomeCase(unittest.TestCase):
    """A temp home folder, so no test reads or writes real state."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v10-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        for name in (
            "MODEL_API_KEY",
            "BASETEN_API_KEY",
            "FIREWORKS_API_KEY",
        ):
            os.environ.pop(name, None)
        self.job = self.tmp / "job"

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# The pack format, the seed packs, and loading
# ---------------------------------------------------------------------------


class PackFormatTests(IsolatedHomeCase):
    def test_parse_pack_header_and_verbatim_body(self):
        header, body = skills.parse_pack(
            "name: A pack\nversion: 2\ngroup: starter\n"
            "style: justins-way\n---\n\nBody starts blank.\n"
        )
        self.assertEqual(header["name"], "A pack")
        self.assertEqual(header["version"], 2)
        self.assertEqual(header["group"], "starter")
        self.assertEqual(header["style"], "justins-way")
        self.assertEqual(body, "\nBody starts blank.\n")

    def test_parse_pack_without_separator_is_all_body(self):
        header, body = skills.parse_pack("just words\n")
        self.assertEqual(header, {})
        self.assertEqual(body, "just words\n")

    def test_justins_way_seed_bodies_are_byte_identical(self):
        base = skills.seed_packs_dir() / "justins-way"
        _h, direction = skills.parse_pack(
            (base / "direction.md").read_text(encoding="utf-8")
        )
        _h, retake = skills.parse_pack(
            (base / "retake-judge.md").read_text(encoding="utf-8")
        )
        self.assertEqual(
            direction,
            DIRECTION_PASS_RULES + DIRECTION_PASS_FLAGS + DIRECTION_PASS_PAUSES,
        )
        self.assertEqual(retake, RETAKE_CALIBRATION)

    def test_keep_it_simple_packs(self):
        base = skills.seed_packs_dir() / "keep-it-simple"
        _h, direction = skills.parse_pack(
            (base / "direction.md").read_text(encoding="utf-8")
        )
        _h, retake = skills.parse_pack(
            (base / "retake-judge.md").read_text(encoding="utf-8")
        )
        # The hard rules ride along; the story examples do not.
        self.assertIn("Hard rules:", direction)
        self.assertNotIn("CALIBRATION EXAMPLES", direction)
        self.assertNotIn("Example D1", direction)
        # The flags and pauses parts still ride along.
        self.assertIn("repeated_point", direction)
        self.assertIn("Pauses: silence is handled", direction)
        self.assertIn(
            "prefer the later, fuller version of a repeated point",
            direction,
        )
        self.assertIn("The last take wins", retake)
        self.assertIn("corrects a fact is not a retake", retake)
        self.assertIn("said twice on purpose stays", retake)

    def test_hands_off_packs(self):
        base = skills.seed_packs_dir() / "hands-off"
        _h, direction = skills.parse_pack(
            (base / "direction.md").read_text(encoding="utf-8")
        )
        _h, retake = skills.parse_pack(
            (base / "retake-judge.md").read_text(encoding="utf-8")
        )
        self.assertEqual(retake, "")
        self.assertIn("Hard rules:", direction)
        self.assertNotIn("Example D1", direction)
        self.assertIn(
            "flag it instead of proposing the cut", direction
        )


class LoadAndSeedTests(IsolatedHomeCase):
    def test_ensure_seeded_copies_missing_and_never_overwrites(self):
        target = self.tmp / "skills"
        skills.ensure_seeded(target)
        placed = target / "starter" / "justins-way" / "direction.md"
        self.assertTrue(placed.is_file())
        self.assertTrue((target / "yours").is_dir())
        placed.write_text("name: edited\n---\nmy own words\n", encoding="utf-8")
        skills.ensure_seeded(target)
        self.assertEqual(
            placed.read_text(encoding="utf-8"),
            "name: edited\n---\nmy own words\n",
        )

    def test_load_with_no_packs_is_a_clean_empty_result(self):
        empty = self.tmp / "empty-skills"
        empty.mkdir()
        loaded = skills.load(skills_dir=empty, style="justins-way", seed=False)
        self.assertFalse(loaded.found)
        self.assertIsNone(loaded.direction_body)
        self.assertIsNone(loaded.retake_body)
        self.assertEqual(
            loaded.versions(),
            {
                "style": "justins-way",
                "direction": {"version": None, "source": "built-in"},
                "retake": {"version": None, "source": "built-in"},
            },
        )
        # A folder that does not exist at all loads the same way.
        loaded = skills.load(
            skills_dir=self.tmp / "no-such-folder", seed=False
        )
        self.assertFalse(loaded.found)

    def test_yours_pack_appends_to_the_starter_body(self):
        target = self.tmp / "skills"
        skills.ensure_seeded(target)
        (target / "yours" / "direction.md").write_text(
            "Always keep the opening story.\n", encoding="utf-8"
        )
        (target / "yours" / "retake-judge.md").write_text(
            "name: mine\nversion: 1\ngroup: yours\nstyle: justins-way\n"
            "---\nKeep the way he says hello.\n",
            encoding="utf-8",
        )
        loaded = skills.load(skills_dir=target, seed=False)
        self.assertTrue(
            loaded.direction_body.startswith(
                DIRECTION_PASS_RULES[:40]
            )
        )
        self.assertIn("Always keep the opening story.", loaded.direction_body)
        self.assertIn("Keep the way he says hello.", loaded.retake_body)
        versions = loaded.versions()
        self.assertEqual(versions["direction"]["source"], "pack+yours")
        self.assertEqual(versions["retake"]["source"], "pack+yours")
        self.assertEqual(versions["direction"]["version"], 1)


# ---------------------------------------------------------------------------
# The providers
# ---------------------------------------------------------------------------


class ProviderSkillsTests(IsolatedHomeCase):
    def provider(self, **kwargs) -> MetaProvider:
        kwargs.setdefault("api_key", "test-key")
        return MetaProvider(**kwargs)

    def test_no_pack_provider_system_strings_are_byte_identical(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        no_pack = self.provider(skills_dir=empty, seed_skills=False)
        also_no_pack = self.provider(
            skills_dir=self.tmp / "missing", seed_skills=False
        )
        self.assertEqual(
            capture_direction_system(no_pack),
            capture_direction_system(also_no_pack),
        )
        self.assertEqual(
            capture_retake_system(no_pack),
            capture_retake_system(also_no_pack),
        )
        # And they are exactly the strings the built-in constants
        # have always assembled.
        self.assertEqual(
            capture_direction_system(no_pack),
            SCHEMA_SPEC
            + DIRECTION_PASS_RULES
            + DIRECTION_PASS_FLAGS
            + DIRECTION_PASS_PAUSES,
        )
        self.assertIn(RETAKE_CALIBRATION, capture_retake_system(no_pack))
        self.assertEqual(
            no_pack.skill_versions(),
            {
                "style": "justins-way",
                "direction": {"version": None, "source": "built-in"},
                "retake": {"version": None, "source": "built-in"},
            },
        )

    def test_seeded_provider_matches_the_no_pack_strings(self):
        seeded_dir = self.tmp / "seeded"
        seeded = self.provider(skills_dir=seeded_dir)
        empty = self.tmp / "empty"
        empty.mkdir()
        no_pack = self.provider(skills_dir=empty, seed_skills=False)
        self.assertEqual(
            capture_direction_system(seeded),
            capture_direction_system(no_pack),
        )
        self.assertEqual(
            capture_retake_system(seeded), capture_retake_system(no_pack)
        )
        self.assertEqual(
            seeded.skill_versions(),
            {
                "style": "justins-way",
                "direction": {"version": 1, "source": "pack"},
                "retake": {"version": 1, "source": "pack"},
            },
        )

    def test_editing_a_pack_body_changes_the_system_string(self):
        target = self.tmp / "skills"
        skills.ensure_seeded(target)
        pack_path = target / "starter" / "justins-way" / "direction.md"
        _header, _body = skills.parse_pack(
            pack_path.read_text(encoding="utf-8")
        )
        pack_path.write_text(
            "name: Edited\nversion: 7\ngroup: starter\n"
            "style: justins-way\n---\nEDITED DIRECTION WORDS ONLY.\n",
            encoding="utf-8",
        )
        provider = self.provider(skills_dir=target, seed_skills=False)
        system = capture_direction_system(provider)
        self.assertEqual(system, SCHEMA_SPEC + "EDITED DIRECTION WORDS ONLY.\n")
        self.assertEqual(
            provider.skill_versions()["direction"],
            {"version": 7, "source": "pack"},
        )

    def test_skill_versions_shape_and_sources_for_each_style(self):
        for style_id in ("justins-way", "keep-it-simple", "hands-off"):
            provider = self.provider(
                skills_dir=self.tmp / f"skills-{style_id}", style=style_id
            )
            versions = provider.skill_versions()
            self.assertEqual(versions["style"], style_id)
            for kind in ("direction", "retake"):
                self.assertEqual(
                    versions[kind], {"version": 1, "source": "pack"}
                )

    def test_hands_off_retake_system_has_no_calibration(self):
        provider = self.provider(
            skills_dir=self.tmp / "hands", style="hands-off"
        )
        self.assertNotIn(RETAKE_CALIBRATION, capture_retake_system(provider))
        self.assertNotIn("Example B1", capture_retake_system(provider))

    def test_mock_provider_skill_versions(self):
        mock_provider = MockProvider(
            skills_dir=self.tmp / "mock-skills", style="keep-it-simple"
        )
        self.assertEqual(
            mock_provider.skill_versions(),
            {
                "style": "keep-it-simple",
                "direction": {"version": 1, "source": "pack"},
                "retake": {"version": 1, "source": "pack"},
            },
        )
        empty = self.tmp / "mock-empty"
        empty.mkdir()
        bare_mock = MockProvider(skills_dir=empty, seed_skills=False)
        self.assertEqual(
            bare_mock.skill_versions()["direction"]["source"], "built-in"
        )

    def test_unpicked_style_defaults_to_justins_way(self):
        provider = self.provider(skills_dir=self.tmp / "anywhere")
        self.assertEqual(provider.skill_versions()["style"], "justins-way")


# ---------------------------------------------------------------------------
# The direction pass record and recap, the saved-rules pin
# ---------------------------------------------------------------------------


class CapturingSkillsProvider:
    """A direction-pass stand-in whose know-how record comes from
    a real mock provider loaded from a real skills folder."""

    def __init__(self, skills_dir: Path, style: str):
        self._inner = MockProvider(skills_dir=skills_dir, style=style)
        self.packet = None

    def direct_transcript(self, packet, timeline, profile=None):
        self.packet = packet
        return [], [], []

    def drain_usage(self):
        return []

    def skill_versions(self):
        return self._inner.skill_versions()


class DirectionPassSkillsTests(IsolatedHomeCase):
    def setUp(self) -> None:
        super().setUp()
        write_job(
            self.job, spaced_words([f"word{i}" for i in range(40)])
        )
        self.config = WebAppConfig(self.job)
        self.config.session_api_key = "test-key"
        self.settings = {
            **dict(webapp.FRONT_DOOR_DEFAULTS),
            "style": "keep-it-simple",
        }

    def run_pass(self, provider):
        with mock.patch.object(
            webapp, "_direction_provider", return_value=provider
        ):
            return webapp._run_direction_pass(
                self.config, "make it tighter", self.settings
            )

    def test_summary_and_file_carry_the_skills_record(self):
        provider = CapturingSkillsProvider(
            self.tmp / "pass-skills", "keep-it-simple"
        )
        summary = self.run_pass(provider)
        expected = {
            "style": "keep-it-simple",
            "direction": {"version": 1, "source": "pack"},
            "retake": {"version": 1, "source": "pack"},
        }
        self.assertEqual(summary["skills"], expected)
        record = json.loads(
            (self.config.session_dir / "direction_pass.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["skills"], expected)

    def test_recap_line_names_the_style(self):
        provider = CapturingSkillsProvider(
            self.tmp / "pass-skills", "keep-it-simple"
        )
        summary = self.run_pass(provider)
        self.assertIn(
            "Know-how used: Keep it simple, starter pack version 1.",
            summary["lines"],
        )

    def test_recap_line_names_yours_when_yours_was_used(self):
        class YoursProvider(CapturingSkillsProvider):
            def skill_versions(self):
                return {
                    "style": "justins-way",
                    "direction": {"version": 1, "source": "pack+yours"},
                    "retake": {"version": 1, "source": "pack"},
                }

        provider = YoursProvider(self.tmp / "pass-skills", "justins-way")
        self.settings["style"] = "justins-way"
        summary = self.run_pass(provider)
        self.assertIn(
            "Know-how used: Justin's way, starter pack version 1.",
            summary["lines"],
        )
        self.assertIn(
            "Your own added know-how was used too.", summary["lines"]
        )

    def test_saved_rules_still_reach_the_packet_when_a_style_is_picked(self):
        webapp._save_rules_store(
            self.config,
            {
                "rules": [
                    {
                        "rule_id": "rule-1",
                        "pattern_key": "test-pattern",
                        "statement": "Keep the opening story whole.",
                        "active": True,
                    }
                ],
                "dismissed": {},
            },
        )
        provider = CapturingSkillsProvider(
            self.tmp / "pass-skills", "keep-it-simple"
        )
        self.run_pass(provider)
        self.assertEqual(
            provider.packet["saved_rules"], ["Keep the opening story whole."]
        )


# ---------------------------------------------------------------------------
# Through the test server: the page, the pick, the retake record
# ---------------------------------------------------------------------------


class StyleServerTests(IsolatedHomeCase):
    def setUp(self) -> None:
        super().setUp()
        self.job.mkdir()
        for fixture, target in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / fixture, self.job / target)
        self.config = WebAppConfig(self.job)
        self.server = create_server(self.config, host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, data=None):
        payload = None
        headers = {}
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path, data=payload, headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def get_json(self, path):
        status, body = self.request(path)
        self.assertEqual(status, 200)
        return json.loads(body.decode("utf-8"))

    def post_json(self, path, data):
        status, body = self.request(path, data=data)
        return status, json.loads(body.decode("utf-8"))

    def fire_front_door(self, extra=None):
        body = {
            "provider": "mock",
            "remove_retakes": True,
            "cut_silence": False,
            "remove_fillers": False,
            "format_scope": "both",
            "direction_text": "",
        }
        body.update(extra or {})
        status, payload = self.post_json("/api/front-door", body)
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["ok"], payload)
        return payload

    def test_page_shows_the_style_pick_and_the_plain_copy(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        page = body.decode("utf-8")
        self.assertIn("Editing style", page)
        self.assertIn("Justin's way", page)
        self.assertIn("Keep it simple", page)
        self.assertIn("Hands off", page)
        # The same plain copy appears at both controls (the drop
        # block and the Make the edit card).
        self.assertGreaterEqual(page.count(STYLE_COPY), 2)

    def test_unpicked_style_defaults_to_justins_way(self):
        settings = webapp._front_door_settings(self.config)
        self.assertEqual(settings["style"], "justins-way")
        payload = self.fire_front_door()
        self.assertEqual(payload["settings"]["style"], "justins-way")
        job = self.get_json("/api/job")
        self.assertEqual(job["front_door"]["style"], "justins-way")

    def test_picked_style_is_saved_and_the_retake_record_names_it(self):
        payload = self.fire_front_door({"style": "keep-it-simple"})
        self.assertEqual(payload["settings"]["style"], "keep-it-simple")
        job = self.get_json("/api/job")
        self.assertEqual(job["front_door"]["style"], "keep-it-simple")
        # The usual carries the pick to the next job, like the mode.
        loaded = usual.load_usual(dict(webapp.FRONT_DOOR_DEFAULTS))
        self.assertEqual(loaded["style"], "keep-it-simple")
        record = json.loads(
            (self.config.session_dir / "retake_pass.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(record["runs"])
        run = record["runs"][-1]
        self.assertEqual(run["skills"]["style"], "keep-it-simple")
        self.assertEqual(
            run["skills"]["direction"], {"version": 1, "source": "pack"}
        )
        self.assertEqual(
            run["skills"]["retake"], {"version": 1, "source": "pack"}
        )

    def test_unknown_style_is_refused_in_plain_words(self):
        status, payload = self.post_json(
            "/api/front-door",
            {"provider": "mock", "style": "someone-elses-way"},
        )
        self.assertEqual(status, 400)
        self.assertIn("Justin's way", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
