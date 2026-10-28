"""v0.7.0 coordinator contract tests.

The pieces the three build workers owned separately, checked where
they meet: the provider's direction pass (usage capture, the batched
Wonder escalation, flag validation), the page the template ships
(the Remove moment, the transcript panel, the floating card), and
the no-job summary the drop block seeds itself from.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talk_to_your_editor.meta_provider import (  # noqa: E402
    MetaProvider,
    MockProvider,
)
from talk_to_your_editor.timeline import Timeline  # noqa: E402
from talk_to_your_editor.webapp import WebAppConfig, create_server  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "src" / "talk_to_your_editor" / "templates"
            / "test_app.html").read_text(encoding="utf-8")

WORDS = (
    "Today we test the editor and see how it handles a direction. "
    "The water is cold and the morning is bright and clear. "
    "We keep the strong lines and we cut the weak ones here."
).split()


def make_timeline() -> Timeline:
    words = []
    t = 0.0
    for token in WORDS:
        words.append({"word": token, "start": round(t, 2),
                      "end": round(t + 0.3, 2)})
        t += 0.4
    transcript = {"words": words, "duration_s": t}
    cuts = {"version": 1, "video": "x.mp4", "source_duration_s": t,
            "segments": [{"start": 0.0, "end": t, "action": "keep"}],
            "removed_ranges": [], "decisions": [], "summary": {}}
    return Timeline(transcript, cuts)


class _StubChatProvider(MetaProvider):
    """Answers _chat from a script and records every call."""

    def __init__(self, script):
        super().__init__("test-key")
        self.script = list(script)
        self.calls = []

    def _chat(self, model, messages):  # noqa: D102 - test stub
        self.calls.append((model, messages))
        self.usage_log.append(
            {"model": model, "prompt_tokens": 10,
             "completion_tokens": 5})
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def _op(op_type, start, end, **extra):
    op = {"type": op_type, "comment_id": "direction-pass",
          "word_start": start, "word_end": end,
          "reason": "stub reason", "confidence": 0.9,
          "risk_tier": 1, "wonder_reviewed": False,
          "model": "muse-spark-1.3"}
    op.update(extra)
    return op


class DirectionPassProviderTests(unittest.TestCase):
    def packet(self):
        return {"direction": "Keep it tight.",
                "overall": [], "saved_rules": [],
                "plan_state": {"kept_words": len(WORDS),
                               "cut_words": 0}}

    def test_tier1_passes_through_and_flags_validate(self):
        spark = json.dumps({
            "operations": [_op("cut_words", 21, 24)],
            "flags": [
                {"type": "buried_line", "word_start": 14,
                 "word_end": 18, "reason": "A complete line."},
                {"type": "not_a_type", "word_start": 0, "word_end": 2,
                 "reason": "dropped"},
                {"type": "halfway_thought", "word_start": 9,
                 "word_end": 9, "reason": "empty span, dropped"},
            ],
        })
        provider = _StubChatProvider([spark])
        operations, routing, flags = provider.direct_transcript(
            self.packet(), make_timeline())
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0]["comment_id"], "direction-pass")
        self.assertEqual(
            operations[0]["target_comment_id"], "direction-pass")
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["type"], "buried_line")
        self.assertIsNone(flags[0]["word_start2"])
        usage = provider.drain_usage()
        self.assertEqual(len(usage), 1)
        self.assertEqual(provider.drain_usage(), [])

    def test_tier2_ops_share_one_wonder_call(self):
        long_cut = _op("cut_words", 0, 22)  # > 20 words: tier 2
        spark = json.dumps({"operations": [long_cut], "flags": []})
        wonder = json.dumps({"operations": [
            _op("cut_words", 0, 22, risk_tier=2)]})
        provider = _StubChatProvider([spark, wonder])
        operations, routing, _flags = provider.direct_transcript(
            self.packet(), make_timeline())
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(len(operations), 1)
        self.assertTrue(operations[0]["wonder_reviewed"])
        self.assertGreaterEqual(operations[0]["risk_tier"], 2)
        self.assertTrue(any(r["action"] == "wonder_reviewed"
                            for r in routing))

    def test_wonder_failure_flags_every_tier2_op(self):
        long_cut = _op("cut_words", 0, 22)
        spark = json.dumps({"operations": [long_cut], "flags": []})
        provider = _StubChatProvider([spark, RuntimeError("down")])
        operations, _routing, _flags = provider.direct_transcript(
            self.packet(), make_timeline())
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0]["type"], "flag_for_review")

    def test_spark_failure_returns_empty(self):
        provider = _StubChatProvider([RuntimeError("down")])
        self.assertEqual(
            provider.direct_transcript(self.packet(), make_timeline()),
            ([], [], []))

    def test_mock_provider_is_inert(self):
        mock_provider = MockProvider()
        self.assertEqual(
            mock_provider.direct_transcript(
                self.packet(), make_timeline()),
            ([], [], []))
        self.assertEqual(mock_provider.drain_usage(), [])


class TemplateContractTests(unittest.TestCase):
    def test_remove_moment_and_panel_exist(self):
        for needle in ('id="inRetakes"', 'id="inSilence"',
                       'id="inFillers"', 'id="inKeepPills"',
                       'id="fdFillers"',
                       'id="transcriptPanel"', 'id="txFollow"',
                       'id="txActionStrip"', 'id="txCutBtn"',
                       'id="txRestoreBtn"', 'role="dialog"',
                       "Take out the ums and uhs",
                       "Follow the video"):
            self.assertIn(needle, TEMPLATE)

    def test_no_dead_state_route_and_no_feel_pause_copy(self):
        self.assertNotIn("/api/state", TEMPLATE)
        self.assertNotIn("judged by feel", TEMPLATE)

    def test_no_breaths_control_anywhere(self):
        # v0.10: the breaths choice was removed (the creator's
        # call). No pill row, no data attribute, no JS for it.
        for needle in ("BreathsPills", "data-breaths", "setBreaths",
                       "BREATHS_NOTES", "Silence left between words"):
            self.assertNotIn(needle, TEMPLATE)
        # The one remaining silence setting, in the new words.
        self.assertIn("Quiet between your words", TEMPLATE)
        self.assertIn(
            "When you stop talking for a moment, the app cuts "
            "the quiet down and leaves only this much.",
            TEMPLATE,
        )


class NoJobSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v07-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = mock.patch.dict(
            os.environ, {"HOME": str(self.home)}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.launch = self.tmp / "launch home"
        self.launch.mkdir()
        self.server = create_server(
            WebAppConfig(None, launch_home=self.launch),
            host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_job_summary_carries_front_door_and_flags(self):
        with urllib.request.urlopen(self.base + "/api/job",
                                    timeout=10) as resp:
            summary = json.loads(resp.read().decode())
        front_door = summary["front_door"]
        self.assertEqual(
            set(front_door),
            {"remove_retakes", "cut_silence", "buffer_s", "keep_mode",
             "remove_fillers", "mode",
             # v0.8.1: the chosen Spark route rides the front door.
             "spark_route",
             # v0.10: the editing style rides the front door too.
             "style"})
        self.assertEqual(summary["flags"], [])
        self.assertEqual(summary["gold"], [])


if __name__ == "__main__":
    unittest.main()
