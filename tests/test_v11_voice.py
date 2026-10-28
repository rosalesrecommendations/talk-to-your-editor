"""v0.11.0, fix item 8: voice commands.

Justin's two layers, tested the way he settled them:

1. Local keyword matching first. Written phrase lists, one per
   command; a matched command is settled on the spot by the pure
   matcher in voice_commands.py and by the /api/voice-command
   route, with ZERO model or direction-provider calls anywhere
   in the run (asserted on mocks).
2. Only unmatched words that read as direction go to the
   existing direction machinery, and only when a key is set.
   Otherwise the words land as a feedback note and the app says
   so plainly.

Navigation is parameterized: the words carry the amount or the
clock target, and every navigation case asserts the command AND
the numeric seconds. The framing phrase "center me in the
frame" is a known command the app cannot do yet: the outcome is
the plain unavailable sentence, never a note, never direction.

Everything here is offline, with mocked providers only.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import voice_commands, webapp  # noqa: E402
from talk_to_your_editor.webapp import (  # noqa: E402
    WebAppConfig,
    create_server,
)

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")

NONSENSE = "the weather is nice today"
DIRECTION_WORDS = (
    "bring the music down in the middle part and warm up the sound"
)


# ---------------------------------------------------------------------------
# Phrase coverage: at least eight natural phrasings per command,
# including every example item 8 names. Each one must match its
# command locally, on the pure matcher alone.
# ---------------------------------------------------------------------------

CORE_PHRASES = {
    "undo": [
        "undo", "undo that", "take that back", "put it back",
        "undo it", "undo the last one", "undo the last change",
        "reverse that", "go back one step", "take it back",
        "put that back", "cancel that last edit",
        "Please undo that.",
    ],
    "redo": [
        "redo", "redo that", "do that again", "put it back on",
        "redo it", "redo the last one", "redo the last change",
        "do it again", "bring that back", "go forward one step",
        "reapply that", "un undo that",
    ],
    "remove_retakes": [
        "remove retakes", "cut the retakes", "take out the retakes",
        "get rid of the retakes", "cut retakes",
        "remove the retakes", "take the retakes out",
        "delete the retakes", "strip the retakes",
        "clean up the retakes", "take out retakes",
        "remove all the retakes",
    ],
    "keep_retakes": [
        "keep the retakes", "keep retakes", "restore retakes",
        "restore the retakes", "leave the retakes in",
        "leave the retakes", "do not remove the retakes",
        "dont remove retakes", "keep all the retakes",
        "put the retakes back", "bring the retakes back",
        "keep the retakes in",
    ],
    "trim_audio": [
        "trim audio", "cut the silence", "tighten it up",
        "remove the pauses", "trim the audio", "cut silence",
        "remove silence", "remove the silence",
        "take out the pauses", "take out the silence",
        "tighten up the audio", "trim the pauses",
    ],
    "play": [
        "play", "play it", "play the video", "start playing",
        "start the video", "keep playing", "resume", "hit play",
        "press play", "play from here", "let it play", "roll it",
    ],
    "pause": [
        "pause", "pause it", "pause the video", "stop the video",
        "stop it", "hold on", "wait", "stop playing", "hit pause",
        "press pause", "pause here", "freeze it",
    ],
    "next_change": [
        "next change", "go to the next change", "next edit",
        "jump to the next change", "skip to the next change",
        "take me to the next change", "find the next change",
        "next cut", "go to next change", "show me the next change",
    ],
    "make_edit": [
        "make the edit", "make it", "run it", "do the edit",
        "run the edit", "make the edits", "go ahead and make the edit",
        "apply the edit", "apply it", "run the front door",
        "make this edit", "go ahead and run it",
    ],
    "center_frame": [
        "center me in the frame", "center me", "centre me",
        "center me in frame", "center the subject",
        "put me in the center of the frame",
        "keep me centered in the frame",
        "move me to the center of the frame",
    ],
}


class PhraseListTests(unittest.TestCase):
    def test_every_command_list_has_at_least_eight_phrases(self):
        for command, phrases in CORE_PHRASES.items():
            self.assertGreaterEqual(
                len(phrases), 8, f"{command} test list is short"
            )
            shipped = voice_commands.PHRASE_LISTS[command]
            self.assertGreaterEqual(
                len(shipped), 8, f"{command} shipped list is short"
            )

    def test_all_core_phrasings_match_their_command(self):
        for command, phrases in CORE_PHRASES.items():
            for phrase in phrases:
                result = voice_commands.match_voice_command(phrase)
                if command == "center_frame":
                    self.assertEqual(
                        result["decision"], "unavailable", phrase
                    )
                    self.assertEqual(result["command"], command)
                else:
                    self.assertEqual(
                        result["decision"], "command",
                        f"{phrase!r} -> {result}",
                    )
                    self.assertEqual(
                        result["command"], command,
                        f"{phrase!r} -> {result}",
                    )

    def test_item_8_named_examples_all_covered(self):
        named = {
            "undo": ["undo", "undo that", "take that back",
                     "put it back"],
            "remove_retakes": [
                "remove retakes", "cut the retakes",
                "take out the retakes", "get rid of the retakes",
                "cut retakes",
            ],
            "trim_audio": [
                "trim audio", "cut the silence", "tighten it up",
                "remove the pauses",
            ],
        }
        for command, phrases in named.items():
            for phrase in phrases:
                result = voice_commands.match_voice_command(phrase)
                self.assertEqual(result["decision"], "command", phrase)
                self.assertEqual(result["command"], command, phrase)

    def test_justins_seed_cases(self):
        for phrase, command in [
            ("remove retakes", "remove_retakes"),
            ("trim audio", "trim_audio"),
            ("undo", "undo"),
        ]:
            result = voice_commands.match_voice_command(phrase)
            self.assertEqual(result["command"], command, phrase)


# ---------------------------------------------------------------------------
# Navigation: command AND numeric target.
# ---------------------------------------------------------------------------

NAV_CASES = [
    # (words, command, seconds)
    ("go back five seconds", "seek_relative", -5.0),
    ("go ahead five seconds", "seek_relative", 5.0),
    ("jump back a minute", "seek_relative", -60.0),
    ("go to one minute and thirty seconds", "seek_absolute", 90.0),
    ("take me to 1:30", "seek_absolute", 90.0),
    ("jump to the beginning", "seek_start", 0),
    ("back to the start", "seek_start", 0),
    ("start over", "seek_start", 0),
    ("go to the beginning", "seek_start", 0),
    ("go to the end", "seek_end", None),
    ("jump to the end", "seek_end", None),
    ("skip forward", "seek_relative", 5.0),
    ("rewind", "seek_relative", -5.0),
    ("fast forward", "seek_relative", 5.0),
    ("go forward twenty five seconds", "seek_relative", 25.0),
    ("back half a minute", "seek_relative", -30.0),
    ("move back one minute and thirty seconds", "seek_relative", -90.0),
    ("take me to 0:45", "seek_absolute", 45.0),
    ("seek to three minutes and ten seconds", "seek_absolute", 190.0),
    ("go back ten seconds", "seek_relative", -10.0),
    ("skip ahead thirty seconds", "seek_relative", 30.0),
    ("go back 15 seconds", "seek_relative", -15.0),
]


class NavigationTests(unittest.TestCase):
    def test_navigation_phrases_resolve_command_and_seconds(self):
        for words, command, seconds in NAV_CASES:
            result = voice_commands.match_voice_command(words)
            self.assertEqual(
                result["decision"], "command", f"{words!r} -> {result}"
            )
            self.assertEqual(result["command"], command, words)
            self.assertEqual(result["seconds"], seconds, words)

    def test_settled_phrases_are_not_read_as_navigation(self):
        # Exact phrase matches run before the navigation parser:
        # "go back one step" is undo, "put that back" is undo.
        for words in ("go back one step", "put that back",
                      "put it back"):
            result = voice_commands.match_voice_command(words)
            self.assertEqual(result["command"], "undo", words)


# ---------------------------------------------------------------------------
# The ask case, nonsense, and the framing outcome (matcher level).
# ---------------------------------------------------------------------------


class DecisionTests(unittest.TestCase):
    def test_near_match_between_two_commands_asks(self):
        for words, expected in [
            ("undo remove retakes", {"undo", "remove_retakes"}),
            ("remove retakes trim audio",
             {"remove_retakes", "trim_audio"}),
        ]:
            result = voice_commands.match_voice_command(words)
            self.assertEqual(result["decision"], "ask", words)
            self.assertEqual(
                set(result["candidates"]), expected, words
            )

    def test_nonsense_matches_nothing(self):
        result = voice_commands.match_voice_command(NONSENSE)
        self.assertEqual(result["decision"], "none")
        self.assertFalse(voice_commands.reads_as_direction(NONSENSE))

    def test_direction_words_read_as_direction(self):
        result = voice_commands.match_voice_command(DIRECTION_WORDS)
        self.assertEqual(result["decision"], "none")
        self.assertTrue(
            voice_commands.reads_as_direction(DIRECTION_WORDS)
        )

    def test_center_me_in_the_frame_is_known_but_unavailable(self):
        result = voice_commands.match_voice_command(
            "center me in the frame"
        )
        self.assertEqual(result["decision"], "unavailable")
        self.assertEqual(result["command"], "center_frame")
        self.assertEqual(
            voice_commands.CENTER_UNAVAILABLE_SENTENCE,
            "I cannot center you in the frame yet.",
        )


# ---------------------------------------------------------------------------
# The real route, on a real job server, providers mocked.
# ---------------------------------------------------------------------------


def write_job(job: Path) -> None:
    job.mkdir(parents=True, exist_ok=True)
    words = []
    t = 0.0
    for text in ("hello", "there", "friend"):
        words.append({"word": text, "start": t, "end": t + 0.4})
        t += 0.5
    (job / "transcript.json").write_text(
        json.dumps({"engine": "test", "duration": 2.0, "words": words}),
        encoding="utf-8",
    )
    (job / "cuts.json").write_text(
        json.dumps({
            "source_duration_s": 2.0,
            "kept_ranges": [{"start": 0.0, "end": 2.0}],
        }),
        encoding="utf-8",
    )


def request_json(base: str, path: str, data=None):
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def provider_mocks():
    """Every seam a model or direction call could pass through."""
    return {
        "MetaProvider": mock.patch.object(webapp, "MetaProvider"),
        "_live_provider": mock.patch.object(webapp, "_live_provider"),
        "_direction_provider": mock.patch.object(
            webapp, "_direction_provider"
        ),
        "_run_direction": mock.patch.object(webapp, "_run_direction"),
        "_run_front_door": mock.patch.object(webapp, "_run_front_door"),
        "_run_direction_pass": mock.patch.object(
            webapp, "_run_direction_pass"
        ),
        "meta_transcribe_wav": mock.patch.object(
            webapp.speech, "meta_transcribe_wav"
        ),
    }


class VoiceRouteCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.job = root / "job-one"
        write_job(self.job)
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        for var in (
            "MODEL_API_KEY", "ELEVENLABS_API_KEY",
            "BASENTEN_API_KEY", "FIREWORKS_API_KEY",
        ):
            os.environ.pop(var, None)
        config = WebAppConfig(self.job, launch_home=self.home)
        self.server = create_server(config, host="127.0.0.1", port=0)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"
        self.addCleanup(self.server.shutdown)

    def post_voice(self, text):
        return request_json(
            self.base, "/api/voice-command", {"text": text}
        )

    def session_feedback(self):
        path = self.job / "tye-session" / "feedback.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def session_log_details(self):
        path = self.job / "tye-session" / "session.log"
        if not path.exists():
            return []
        details = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("detail"):
                details.append(entry["detail"])
        return details


class MatchedRouteTests(VoiceRouteCase):
    def test_matched_command_makes_zero_model_calls(self):
        patches = provider_mocks()
        started = {name: p.start() for name, p in patches.items()}
        try:
            data = self.post_voice("remove retakes")
        finally:
            for p in patches.values():
                p.stop()
        self.assertTrue(data["ok"])
        self.assertEqual(data["decision"], "command")
        self.assertEqual(data["command"], "remove_retakes")
        self.assertEqual(
            data["record"],
            'Heard: "remove retakes". Ran command: Remove retakes.',
        )
        for name, mocked in started.items():
            self.assertEqual(
                mocked.call_count, 0,
                f"{name} was called for a matched command",
            )
        # The record line is in the session log (the history
        # record), and nothing was saved as a note.
        self.assertIn(data["record"], self.session_log_details())
        overall = self.session_feedback().get("overall_feedback", [])
        self.assertNotIn(
            "remove retakes", [n.get("text") for n in overall]
        )

    def test_matched_navigation_returns_seconds(self):
        data = self.post_voice("go back five seconds")
        self.assertEqual(data["decision"], "command")
        self.assertEqual(data["command"], "seek_relative")
        self.assertEqual(data["seconds"], -5.0)
        self.assertEqual(
            data["record"],
            'Heard: "go back five seconds". '
            "Ran command: Go back 5 seconds.",
        )

    def test_ask_case_runs_nothing_and_saves_nothing(self):
        with mock.patch.object(
            webapp, "_run_direction_pass"
        ) as direction_pass:
            data = self.post_voice("undo remove retakes")
        self.assertEqual(data["decision"], "ask")
        self.assertEqual(
            set(data["candidates"]), {"undo", "remove_retakes"}
        )
        self.assertEqual(
            data["message"],
            'Heard: "undo remove retakes". I am not sure which '
            "you meant: Undo or Remove retakes? Nothing ran.",
        )
        self.assertEqual(direction_pass.call_count, 0)
        overall = self.session_feedback().get("overall_feedback", [])
        self.assertEqual(overall, [])

    def test_center_frame_route_is_unavailable_exactly(self):
        with mock.patch.object(
            webapp, "_run_direction_pass"
        ) as direction_pass:
            data = self.post_voice("center me in the frame")
        self.assertEqual(data["decision"], "unavailable")
        self.assertEqual(data["command"], "center_frame")
        self.assertEqual(
            data["message"],
            'Heard: "center me in the frame". '
            "I cannot center you in the frame yet.",
        )
        self.assertEqual(direction_pass.call_count, 0)
        overall = self.session_feedback().get("overall_feedback", [])
        self.assertEqual(overall, [])


class FallbackRouteTests(VoiceRouteCase):
    def test_nonsense_is_saved_as_a_note_through_the_real_route(self):
        with mock.patch.object(
            webapp, "_run_direction_pass"
        ) as direction_pass:
            data = self.post_voice(NONSENSE)
        self.assertEqual(data["decision"], "note")
        self.assertEqual(
            data["message"],
            f'Heard: "{NONSENSE}". I saved your words as a note.',
        )
        self.assertEqual(direction_pass.call_count, 0)
        overall = self.session_feedback().get("overall_feedback", [])
        self.assertIn(NONSENSE, [n.get("text") for n in overall])
        self.assertIn(data["record"], self.session_log_details())

    def test_direction_words_without_a_key_land_as_a_note(self):
        with mock.patch.object(
            webapp, "_run_direction_pass"
        ) as direction_pass:
            data = self.post_voice(DIRECTION_WORDS)
        self.assertEqual(data["decision"], "note")
        self.assertIn(
            "I saved your words as a note.", data["message"]
        )
        self.assertEqual(direction_pass.call_count, 0)
        overall = self.session_feedback().get("overall_feedback", [])
        self.assertIn(DIRECTION_WORDS, [n.get("text") for n in overall])

    def test_direction_words_with_a_key_go_to_direction(self):
        self.server.config.session_api_key = "test-key-voice"
        with mock.patch.object(
            webapp, "_run_direction_pass",
            return_value={"ran": True, "reason": "applied"},
        ) as direction_pass:
            data = self.post_voice(DIRECTION_WORDS)
        self.assertEqual(data["decision"], "direction")
        self.assertTrue(data["direction_ran"])
        self.assertEqual(
            data["record"],
            f'Heard: "{DIRECTION_WORDS}". Sent as direction.',
        )
        self.assertEqual(direction_pass.call_count, 1)
        called_text = direction_pass.call_args[0][1]
        self.assertEqual(called_text, DIRECTION_WORDS)
        overall = self.session_feedback().get("overall_feedback", [])
        self.assertNotIn(
            DIRECTION_WORDS, [n.get("text") for n in overall]
        )


# ---------------------------------------------------------------------------
# The page: the talk button, its wiring, and the handler map.
# Each command id must reach the SAME handler its button uses,
# asserted by construction in the page source.
# ---------------------------------------------------------------------------


class PageTests(unittest.TestCase):
    def test_talk_button_and_dictation_wiring_present(self):
        self.assertIn('id="voiceTalkBtn"', TEMPLATE)
        self.assertIn('id="voiceText"', TEMPLATE)
        self.assertIn('id="voiceStatus"', TEMPLATE)
        self.assertIn(
            'wireDictation("voiceTalkBtn", "voiceText", "voiceStatus")',
            TEMPLATE,
        )
        self.assertIn('"/api/voice-command"', TEMPLATE)
        self.assertIn("[voice-command-begin]", TEMPLATE)
        self.assertIn("[voice-command-end]", TEMPLATE)

    def test_each_command_uses_the_same_handler_as_its_button(self):
        same_handler = {
            "undo": 'voiceClick("undoBtn")',
            "redo": 'voiceClick("redoBtn")',
            "remove_retakes": 'voiceClick("passRetakesBtn")',
            "trim_audio": 'voiceClick("passSilenceBtn")',
            "make_edit": 'voiceClick("fdFire")',
            "play": 'voiceClick("playPauseBtn")',
            "pause": 'voiceClick("playPauseBtn")',
            "next_change": 'voiceClick("nextChangeBtn")',
            "seek_start": 'voiceClick("goStartBtn")',
            "seek_end": 'voiceClick("goEndBtn")',
            "seek_relative": "stepBy(seconds",
            "seek_absolute": "seekToEdited(",
            # No button exists for keep retakes; the voice path
            # drives the Remove retakes switch itself.
            "keep_retakes": 'voiceById("fdRetakes")',
        }
        for command, needle in same_handler.items():
            self.assertIn(command + ":", TEMPLATE, command)
            self.assertIn(needle, TEMPLATE, command)


if __name__ == "__main__":
    unittest.main()
