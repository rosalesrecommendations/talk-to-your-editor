"""Tests for the v0.7.0 backend (stdlib unittest, no dependencies).

Covers the creator's usual store and its place in the front door's
seeding, the deterministic filler pass, the direction pass with a
stub provider, and the first edit's per-drop settings validation.

Every test isolates the usual store: the home folder is pointed at
a temp dir for the whole case, because the usual is creator-level
state and a test run must never read or write the real one.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import usual, webapp
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig


def write_job(job: Path, words: list, profile: dict | None = None) -> None:
    """A minimal job: one kept range covering every word."""
    job.mkdir(parents=True, exist_ok=True)
    transcript = {
        "engine": "test",
        "duration": float(words[-1]["end"]) if words else 0.0,
        "words": words,
    }
    cuts = {
        "source_duration_s": transcript["duration"],
        "kept_ranges": [
            {"start": 0.0, "end": transcript["duration"]}
        ],
    }
    (job / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")
    if profile is not None:
        (job / "profile.json").write_text(
            json.dumps(profile), encoding="utf-8"
        )


def spaced_words(tokens: list, step: float = 0.5, width: float = 0.4):
    return [
        {"word": token, "start": i * step, "end": i * step + width}
        for i, token in enumerate(tokens)
    ]


class IsolatedHomeCase(unittest.TestCase):
    """A temp home folder, so the usual store is the test's own."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v07-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(
            os.environ, {"HOME": str(self.home)}
        )
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def usual_path(self) -> Path:
        return self.home / ".talk-to-your-editor" / "usual.json"


# ---------------------------------------------------------------------------
# The usual store
# ---------------------------------------------------------------------------


class UsualStoreTests(IsolatedHomeCase):
    def test_round_trip_writes_only_the_stored_keys(self):
        # Seven since v0.7.1 (the involvement mode joined), eight
        # since v0.8.1: the chosen Spark route rides along too.
        settings = {
            "spark_route": "baseten",
            "remove_retakes": False,
            "cut_silence": False,
            "buffer_s": 0.25,
            "keep_mode": "first",
            "breaths": "trim",
            "remove_fillers": False,
            "mode": "tailor",
            "something_else": "not stored",
        }
        usual.save_usual(settings)
        payload = json.loads(self.usual_path().read_text(encoding="utf-8"))
        self.assertEqual(set(payload), set(usual.USUAL_KEYS))
        loaded = usual.load_usual(dict(webapp.FRONT_DOOR_DEFAULTS))
        for key in usual.USUAL_KEYS:
            self.assertEqual(loaded[key], settings[key])

    def test_saved_file_and_folder_are_private(self):
        usual.save_usual(dict(webapp.FRONT_DOOR_DEFAULTS))
        self.assertEqual(
            stat.S_IMODE(self.usual_path().stat().st_mode), 0o600
        )
        self.assertEqual(
            stat.S_IMODE(self.usual_path().parent.stat().st_mode), 0o700
        )

    def test_sanitization(self):
        self.usual_path().parent.mkdir(parents=True)
        self.usual_path().write_text(
            json.dumps(
                {
                    "remove_retakes": "yes",  # not a bool: default wins
                    "cut_silence": False,  # a real bool: honored
                    "buffer_s": 5.0,  # clamped to the top of the range
                    "keep_mode": "whichever",  # unknown word: default
                    "breaths": "loud",  # unknown word: default
                    "remove_fillers": False,
                    "mode": "sometimes",  # unknown word: default
                }
            ),
            encoding="utf-8",
        )
        loaded = usual.load_usual(dict(webapp.FRONT_DOOR_DEFAULTS))
        self.assertTrue(loaded["remove_retakes"])
        self.assertFalse(loaded["cut_silence"])
        self.assertEqual(loaded["buffer_s"], 0.5)
        self.assertEqual(loaded["keep_mode"], "last")
        self.assertEqual(loaded["breaths"], "keep")
        self.assertFalse(loaded["remove_fillers"])
        self.assertEqual(loaded["mode"], "cleanest")

    def test_missing_and_corrupt_files_fall_back_to_defaults(self):
        defaults = dict(webapp.FRONT_DOOR_DEFAULTS)
        self.assertEqual(usual.load_usual(defaults), defaults)
        self.usual_path().parent.mkdir(parents=True)
        self.usual_path().write_text("not json at all", encoding="utf-8")
        loaded = usual.load_usual(defaults)
        self.assertEqual(loaded, defaults)
        # The fallback is a copy: changing it does not reach back
        # into the caller's defaults.
        loaded["keep_mode"] = "best"
        self.assertEqual(defaults["keep_mode"], "last")


# ---------------------------------------------------------------------------
# Front door seeding: defaults, usual, profile, session
# ---------------------------------------------------------------------------


class FrontDoorSeedingTests(IsolatedHomeCase):
    def test_usual_beats_defaults_profile_beats_usual(self):
        usual.save_usual(
            {
                "remove_retakes": True,
                "cut_silence": True,
                "buffer_s": 0.3,
                "keep_mode": "best",
                "breaths": "trim",
                "remove_fillers": False,
            }
        )
        write_job(
            self.job,
            spaced_words(["hello", "world"]),
            profile={"retakes": {"keep": "first"}},
        )
        settings = webapp._front_door_settings(WebAppConfig(self.job))
        self.assertEqual(settings["buffer_s"], 0.3)  # usual over default
        self.assertEqual(settings["breaths"], "trim")  # usual over default
        self.assertFalse(settings["remove_fillers"])  # usual over default
        # The profile's keeper seed outranks the usual.
        self.assertEqual(settings["keep_mode"], "first")

    def test_session_beats_usual_and_profile(self):
        usual.save_usual(
            {
                **dict(webapp.FRONT_DOOR_DEFAULTS),
                "keep_mode": "best",
                "breaths": "trim",
            }
        )
        write_job(
            self.job,
            spaced_words(["hello", "world"]),
            profile={"retakes": {"keep": "first"}},
        )
        config = WebAppConfig(self.job)
        config.session_dir.mkdir(parents=True)
        (config.session_dir / "front_door.json").write_text(
            json.dumps({"keep_mode": "last", "breaths": "keep"}),
            encoding="utf-8",
        )
        settings = webapp._front_door_settings(config)
        self.assertEqual(settings["keep_mode"], "last")
        self.assertEqual(settings["breaths"], "keep")
        # And the front door defaults now carry the filler choice.
        self.assertTrue(settings["remove_fillers"])


# ---------------------------------------------------------------------------
# The filler pass
# ---------------------------------------------------------------------------

# Forty words; the window rules in the verifier hold cuts near the
# opening and closing, so the fillers that must land sit mid-talk.
# Index 30 is a filler inside a protected span and must survive.
FILLER_TOKENS = [f"word{i}" for i in range(40)]
FILLER_TOKENS[3] = "friends."
FILLER_TOKENS[15] = "um"
FILLER_TOKENS[20] = "uh"
FILLER_TOKENS[21] = "um"
FILLER_TOKENS[25] = "like"
FILLER_TOKENS[30] = "um"


class FillerPassTests(IsolatedHomeCase):
    def setUp(self) -> None:
        super().setUp()
        write_job(
            self.job,
            spaced_words(FILLER_TOKENS),
            profile={
                "protected_spans": [
                    {
                        "word_start": 30,
                        "word_end": 31,
                        "label": "protected moment",
                    }
                ]
            },
        )
        self.config = WebAppConfig(self.job)
        self.settings = dict(webapp.FRONT_DOOR_DEFAULTS)

    def test_operations_coalesce_runs_and_skip_protected(self):
        transcript = json.loads(
            (self.job / "transcript.json").read_text(encoding="utf-8")
        )
        cuts = json.loads((self.job / "cuts.json").read_text(encoding="utf-8"))
        timeline = Timeline(transcript, cuts)
        profile = json.loads(
            (self.job / "profile.json").read_text(encoding="utf-8")
        )
        operations = webapp._filler_operations(
            timeline, self.settings, profile
        )
        spans = [(op["word_start"], op["word_end"]) for op in operations]
        self.assertEqual(spans, [(15, 16), (20, 22)])
        for op in operations:
            self.assertEqual(op["type"], "cut_words")
            self.assertEqual(op["comment_id"], "filler-pass")

    def test_pass_applies_and_records(self):
        summary = webapp._run_filler_pass(self.config, self.settings)
        self.assertTrue(summary["ran"])
        # Three words: the lone filler plus the adjacent run of two.
        self.assertEqual(summary["applied"], 3)
        self.assertEqual(
            summary["lines"], ["Took out 3 filler words (ums and uhs)."]
        )
        record = json.loads(
            (self.config.session_dir / "filler_pass.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["applied"], 3)
        transcript = json.loads(
            (self.job / "transcript.json").read_text(encoding="utf-8")
        )
        timeline = Timeline(transcript, webapp._effective_cuts(self.config))
        for index in (15, 20, 21):
            self.assertEqual(timeline.word_status(index), "cut")
        # The protected filler and the meaning-bearing word survive.
        self.assertEqual(timeline.word_status(30), "kept")
        self.assertEqual(timeline.word_status(25), "kept")

    def test_pass_off_runs_nothing(self):
        settings = {**self.settings, "remove_fillers": False}
        summary = webapp._run_filler_pass(self.config, settings)
        self.assertEqual(summary, {"ran": False, "applied": 0, "lines": []})
        self.assertFalse(
            (self.config.session_dir / "filler_pass.json").exists()
        )


# ---------------------------------------------------------------------------
# The direction pass
# ---------------------------------------------------------------------------

# Eighty words at one second each, so flag times exercise the
# minutes:seconds format. The first sentence ends at word 3, so the
# opening hook is words 0..3.
DIRECTION_TOKENS = [f"word{i}" for i in range(80)]
DIRECTION_TOKENS[3] = "friends."
DIRECTION_TOKENS[40] = "banana"


def direction_words():
    return [
        {"word": token, "start": float(i), "end": float(i) + 0.9}
        for i, token in enumerate(DIRECTION_TOKENS)
    ]


class StubProvider:
    """A provider stand-in with canned operations, flags, and usage."""

    def __init__(self, operations, flags):
        self._operations = operations
        self._flags = flags
        self.packet = None

    def direct_transcript(self, packet, timeline, profile=None):
        self.packet = packet
        return list(self._operations), [], list(self._flags)

    def drain_usage(self):
        return [
            {
                "model": "muse-spark-1.3",
                "prompt_tokens": 120,
                "completion_tokens": 40,
            }
        ]


def stub_operations():
    return [
        {
            "type": "cut_words",
            "comment_id": "direction-pass",
            # The provider's routing-only key: the pass must strip
            # it before the strict apply validation sees the op.
            "target_comment_id": "direction-pass",
            "word_start": 52,
            "word_end": 54,
            "reason": "These words repeat what was just said.",
            "confidence": 0.9,
            "model": "muse-spark-1.3",
            "risk_tier": 1,
            "wonder_reviewed": False,
        },
        {
            "type": "flag_for_review",
            "comment_id": "direction-pass",
            "target_comment_id": "direction-pass",
            "word_start": 60,
            "word_end": 61,
            "reason": "A person should judge this spot.",
            "confidence": 0.5,
            "model": "muse-spark-1.3",
            "risk_tier": 3,
            "wonder_reviewed": False,
        },
    ]


def stub_flags():
    # Deliberately unordered: the pass sorts by word_start.
    return [
        {"type": "halfway_thought", "word_start": 75, "word_end": 77,
         "reason": "The thought trails off."},
        {"type": "wording_fights", "word_start": 40, "word_end": 41,
         "reason": "The word does not fit its sentence."},
        {"type": "repeated_point", "word_start": 48, "word_end": 50,
         "reason": "Inside the protected span: dropped."},
        {"type": "buried_line", "word_start": 2, "word_end": 3,
         "reason": "Inside the opening hook: dropped."},
        {"type": "dropped_thread", "word_start": 52, "word_end": 54,
         "reason": "Points at words the pass itself cut: dropped."},
        {"type": "dropped_thread", "word_start": 21, "word_end": 23,
         "reason": "Overlaps the flag already kept: dropped."},
        {"type": "drifted_repeat", "word_start": 32, "word_end": 34,
         "word_start2": 36, "word_end2": 38,
         "reason": "The same line, drifted."},
        {"type": "buried_line", "word_start": 28, "word_end": 31,
         "reason": "A strong line in the middle of a list."},
        {"type": "repeated_point", "word_start": 20, "word_end": 22,
         "reason": "The point comes back."},
    ]


class DirectionPassTests(IsolatedHomeCase):
    def setUp(self) -> None:
        super().setUp()
        write_job(
            self.job,
            direction_words(),
            profile={
                "protected_spans": [
                    {
                        "word_start": 48,
                        "word_end": 50,
                        "label": "protected moment",
                    }
                ]
            },
        )
        self.config = WebAppConfig(self.job)
        self.config.session_api_key = "test-key"
        self.settings = dict(webapp.FRONT_DOOR_DEFAULTS)

    def run_pass(self):
        stub = StubProvider(stub_operations(), stub_flags())
        with mock.patch.object(
            webapp, "_direction_provider", return_value=stub
        ):
            summary = webapp._run_direction_pass(
                self.config, "make it tighter", self.settings
            )
        return stub, summary

    def test_no_key_pass_does_not_run(self):
        self.config.session_api_key = None
        summary = webapp._run_direction_pass(
            self.config, "make it tighter", self.settings
        )
        self.assertFalse(summary["ran"])
        self.assertEqual(summary["reason"], "no_key")
        self.assertEqual(
            summary["lines"],
            ["Your direction will be read when a key is saved."],
        )
        self.assertFalse(
            (self.config.session_dir / "flags.json").exists()
        )

    def test_pass_applies_filters_and_records(self):
        stub, summary = self.run_pass()
        self.assertTrue(summary["ran"])
        self.assertEqual(summary["applied"], 1)
        self.assertEqual(summary["flagged_ops"], 1)
        self.assertEqual(
            summary["usage"],
            [
                {
                    "model": "muse-spark-1.3",
                    "prompt_tokens": 120,
                    "completion_tokens": 40,
                }
            ],
        )
        # The packet carries the direction, the plan state, and the
        # (empty) overall notes and saved rules for this fresh job.
        self.assertEqual(stub.packet["direction"], "make it tighter")
        self.assertEqual(stub.packet["plan_state"]["kept_words"], 80)
        self.assertEqual(stub.packet["overall"], [])
        self.assertEqual(stub.packet["saved_rules"], [])
        # The cut landed in the revised plan.
        transcript = json.loads(
            (self.job / "transcript.json").read_text(encoding="utf-8")
        )
        timeline = Timeline(transcript, webapp._effective_cuts(self.config))
        self.assertEqual(timeline.word_status(52), "cut")
        self.assertEqual(timeline.word_status(53), "cut")

        flags = summary["flags"]
        self.assertEqual(
            [(flag["type"], flag["word_start"]) for flag in flags],
            [
                ("repeated_point", 20),
                ("buried_line", 28),
                ("drifted_repeat", 32),
                ("wording_fights", 40),
                ("halfway_thought", 75),
            ],
        )
        # Exact recap formatting, one flag of each surviving type.
        self.assertEqual(
            [flag["recap"] for flag in flags],
            [
                "One thing to see: you make the same point again at 0:20.",
                "A strong line is buried at 0:28: 'word28 word29 word30'",
                "These two lines at 0:32 say almost the same thing. "
                "Retake, or said twice on purpose?",
                "One word fights the sentence around it at 0:40: 'banana'.",
                "This thought stops halfway at 1:15 and nothing "
                "finishes it.",
            ],
        )
        drifted = flags[2]
        self.assertEqual(drifted["word_start2"], 36)
        self.assertEqual(drifted["word_end2"], 38)
        self.assertEqual(drifted["time_s"], 32.0)
        self.assertEqual(flags[3]["quote"], "banana")

        self.assertEqual(
            summary["lines"],
            [
                "Read your direction and made 1 change.",
                "1 suggestion set aside for your review.",
            ]
            + [flag["recap"] for flag in flags],
        )

        flags_file = json.loads(
            (self.config.session_dir / "flags.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(flags_file["flags"], flags)
        record = json.loads(
            (self.config.session_dir / "direction_pass.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(record["ran"])
        self.assertEqual(record["applied"], 1)
        self.assertEqual(record["flagged_ops"], 1)
        self.assertEqual(record["flags"], flags)
        self.assertEqual(record["usage"], summary["usage"])
        # The job summary serves the same flags.
        self.assertEqual(webapp._session_flags(self.config), flags)


# ---------------------------------------------------------------------------
# First edit per-drop settings validation, and transcript entry times
# ---------------------------------------------------------------------------


class DropSettingsTests(IsolatedHomeCase):
    def test_no_settings_object_returns_none(self):
        self.assertIsNone(webapp._validated_drop_settings({}))
        self.assertIsNone(
            webapp._validated_drop_settings({"settings": "nope"})
        )
        self.assertIsNone(webapp._validated_drop_settings(None))

    def test_validation_keeps_only_valid_front_door_keys(self):
        validated = webapp._validated_drop_settings(
            {
                "settings": {
                    "remove_retakes": False,
                    "cut_silence": "yes",  # not a bool: dropped
                    "remove_fillers": True,
                    "buffer_s": 5,  # clamped
                    "keep_mode": "first",
                    "breaths": "loud",  # unknown word: dropped
                    "provider": "meta",  # not a front door key
                }
            }
        )
        self.assertEqual(
            validated,
            {
                "remove_retakes": False,
                "remove_fillers": True,
                "buffer_s": 0.5,
                "keep_mode": "first",
            },
        )


class TranscriptEntryTimesTests(IsolatedHomeCase):
    def test_entries_carry_source_times(self):
        write_job(self.job, spaced_words(FILLER_TOKENS))
        config = WebAppConfig(self.job)
        entries, _ = webapp._transcript_entries(config)
        self.assertEqual(entries[0]["start"], 0.0)
        self.assertEqual(entries[0]["end"], 0.4)
        self.assertEqual(entries[15]["start"], 7.5)
        self.assertEqual(entries[15]["end"], 7.9)
        summary = webapp._job_summary(config)
        self.assertEqual(summary["flags"], [])
        self.assertEqual(
            summary["transcript_words"][15]["start"], 7.5
        )


if __name__ == "__main__":
    unittest.main()
