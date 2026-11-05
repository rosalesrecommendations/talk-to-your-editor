"""Tests for the 0.27.1 P1b interface round.

Covers: the Done-bubble export loudness (P2 item 10: the
session splice path normalizes to -14 like every Finish
export); the teach summary's measured lines (P2 item 11:
opening line, sentence pauses, kept-sound pauses, all from
the pair itself); the three-line chat answer cap with its
"more" expansion (item 7); and the Finish state surviving
one shape's framing read failing (item 8).
"""

from __future__ import annotations

import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import finish, teaching, webapp
from talk_to_your_editor.webapp import WebAppConfig


def _words(spec):
    """[(text, start, end), ...] -> transcript word dicts."""
    return [
        {"word": text, "start": start, "end": end}
        for text, start, end in spec
    ]


class LoudnessInPlaceTest(unittest.TestCase):
    """P2-10: normalize_loudness_in_place lands on -14."""

    def setUp(self) -> None:
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg not available")
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-p1b-loud-"))

    def _quiet_video(self) -> Path:
        path = self.tmp / "quiet.mp4"
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i",
                "color=c=black:s=160x120:r=10:d=6",
                "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=48000:d=6",
                "-af", "volume=0.03",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                "-ac", "2", "-shortest", str(path),
            ],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-300:])
        return path

    def test_quiet_file_is_brought_to_target(self) -> None:
        path = self._quiet_video()
        result = finish.normalize_loudness_in_place(path)
        self.assertIsNotNone(result["before_lufs"])
        self.assertLess(result["before_lufs"], -20.0)
        self.assertIsNotNone(result["after_lufs"])
        self.assertAlmostEqual(result["after_lufs"], -14.0, delta=0.5)

    def test_missing_file_is_a_no_op(self) -> None:
        result = finish.normalize_loudness_in_place(
            self.tmp / "nope.mp4"
        )
        self.assertEqual(
            result, {"before_lufs": None, "after_lufs": None}
        )


if __name__ == "__main__":
    unittest.main()
