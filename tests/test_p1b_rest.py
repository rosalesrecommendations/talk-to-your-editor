"""More P1b tests: measured teach summary, answer cap, Finish."""

from __future__ import annotations

import random
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import teaching, webapp
from talk_to_your_editor.webapp import WebAppConfig


def _words(spec):
    return [
        {"word": text, "start": start, "end": end}
        for text, start, end in spec
    ]


class SummaryLinesTest(unittest.TestCase):
    """P2-11: the summary lines are measured, not generic."""

    WORDS = _words([
        ("Today", 0.0, 0.4), ("is", 0.45, 0.7),
        ("day", 0.75, 1.0), ("thirty", 1.05, 1.4),
        ("of", 1.45, 1.6), ("seeing", 1.65, 2.0),
        ("if", 2.05, 2.2), ("God", 2.25, 2.6),
        ("will", 2.65, 2.9), ("use", 2.95, 3.2),
        ("cold", 3.25, 3.55), ("water", 3.6, 3.9),
        ("today.", 3.95, 4.4),
        ("It", 4.9, 5.1), ("is", 5.15, 5.35),
        ("really", 5.4, 5.8), ("cold", 5.85, 6.2),
        ("today.", 6.25, 6.7),
        ("Breathe", 7.1, 7.5), ("in", 7.55, 7.8),
        ("slowly", 7.85, 8.3), ("now.", 8.35, 8.8),
        ("And", 9.5, 9.7), ("we", 9.75, 9.95),
        ("go.", 10.0, 10.4),
    ])

    def test_opening_and_sentence_pauses_measured(self) -> None:
        lines = teaching.measured_summary(None, self.WORDS, None)
        self.assertTrue(
            lines[0].startswith("Your finished edit opens on:"),
            lines,
        )
        self.assertIn("Today is day thirty", lines[0])
        pause_lines = [
            line for line in lines
            if line.startswith("Pauses between sentences")
        ]
        # Gaps after sentence ends: 0.5, 0.4, 0.7 -> median 0.5.
        self.assertEqual(len(pause_lines), 1, lines)
        self.assertIn("0.5 seconds (3 measured)", pause_lines[0])

    def test_no_words_no_lines(self) -> None:
        self.assertEqual(teaching.measured_summary(None, [], None), [])
        self.assertEqual(
            teaching.measured_summary(None, None, None), []
        )


    def test_kept_sound_pauses_counted_from_sound(self) -> None:
        # 3.2 s WAV, sound everywhere except 1.5 s to 2.5 s.
        # Words a/b/c leave a 0.5 s gap with sound and a
        # 1.0 s gap of dead air.
        tmp = Path(tempfile.mkdtemp(prefix="tye-p1b-wav-"))
        rate = 16000
        total = int(rate * 3.2)
        rng = random.Random(7)
        frames = bytearray()
        for i in range(total):
            t = i / rate
            value = 0 if 1.5 <= t < 2.5 else rng.randint(-6000, 6000)
            frames += struct.pack("<h", value)
        path = tmp / "finished.wav"
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(bytes(frames))
        words = _words([
            ("a", 0.0, 0.5), ("b", 1.0, 1.5), ("c", 2.5, 3.0),
        ])
        self.assertEqual(
            teaching._kept_sound_pauses(words, path), (1, 2)
        )

    def test_kept_sound_pauses_none_without_file(self) -> None:
        words = _words([("a", 0.0, 0.5), ("b", 1.0, 1.5)])
        self.assertIsNone(teaching._kept_sound_pauses(words, None))


class AnswerCapTest(unittest.TestCase):
    """Item 7: answers cap at three lines; "more" expands."""

    def test_short_answer_unchanged(self) -> None:
        text, lines, more = webapp._cap_answer("One line.", ["Two."])
        self.assertEqual((text, lines, more), ("One line.", ["Two."], []))

    def test_long_answer_capped_at_three(self) -> None:
        text, lines, more = webapp._cap_answer(
            "Head.", ["a", "b", "c", "d", "e"]
        )
        self.assertEqual(len(lines), 2)
        self.assertEqual(more, ["c", "d", "e"])
        self.assertIn("more", text)

    def test_more_round_trip(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="tye-p1b-cap-"))
        config = WebAppConfig(None, launch_home=tmp / "launch")
        webapp._append_thread(config, {
            "kind": "editor", "status": "settled",
            "text": "Head.", "lines": ["a", "b"],
            "lines_more": ["c", "d"], "claims": [],
        })
        self.assertTrue(webapp._is_more_ask("more"))
        self.assertTrue(webapp._is_more_ask("show me more"))
        self.assertFalse(webapp._is_more_ask("cut more at the start"))
        result = webapp._more_answer_send(config)
        self.assertIsNotNone(result)
        self.assertEqual(result["entry"]["text"], "c")
        self.assertEqual(result["entry"]["lines"], ["d"])
        # Spent: a second ask finds nothing held.
        self.assertIsNone(webapp._more_answer_send(config))


class FinishStateTest(unittest.TestCase):
    """Item 8: one shape's framing failure cannot empty Finish."""

    def test_shape_view_survives_framing_failure(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="tye-p1b-fin-"))
        job = tmp / "job"
        job.mkdir()
        config = WebAppConfig(job, launch_home=tmp / "launch")
        with mock.patch.object(
            webapp, "_finish_window",
            side_effect=RuntimeError("no face today"),
        ):
            view = webapp._finish_shape_view(config, "9:16")
        self.assertEqual(view["aspect"], "9:16")
        self.assertTrue(view["notes"])


if __name__ == "__main__":
    unittest.main()
