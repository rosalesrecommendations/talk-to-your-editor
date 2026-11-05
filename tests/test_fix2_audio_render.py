"""Fix 2 (0.26): the soundtrack is one continuous track.

Justin's 0.25.0 Mac test found 114 stretches of digital silence
at the joins of the rendered edit and a decoded audio stream
7.1 s longer than the picture: every piece's AAC had been
encoded on its own (an encoder's priming and padding at every
join) and faded to silence at both edges. These tests pin the
fix on small synthetic sources: the picture still renders piece
by piece, the sound is built in one pass and encoded once, and
the two streams land the same length by construction.
"""

from __future__ import annotations

import array
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from talk_to_your_editor import intake_build


def make_source(path: Path, seconds: float = 12.0) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", str(seconds),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", str(path),
        ],
        check=True,
    )
    return path


def stream_durations(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "stream=codec_type,duration", "-of", "csv", str(path),
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    found = {}
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) >= 3:
            found[parts[1]] = float(parts[2])
    return found


def decoded_audio(path: Path, work: Path) -> tuple:
    wav_path = work / "decoded.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-i", str(path),
            "-map", "0:a", "-f", "wav", "-ac", "1", "-ar", "48000",
            str(wav_path),
        ],
        check=True,
    )
    with wave.open(str(wav_path)) as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        samples = array.array("h")
        samples.frombytes(handle.readframes(frames))
    return samples, rate


def silence_runs(samples, rate, min_s=0.010):
    """Runs at or below -72 dBFS lasting at least min_s."""
    threshold = 32767 * 10 ** (-72 / 20)
    runs = []
    i = 0
    total = len(samples)
    while i < total:
        if abs(samples[i]) <= threshold:
            j = i
            while j < total and abs(samples[j]) <= threshold:
                j += 1
            if (j - i) / rate >= min_s:
                runs.append((i / rate, (j - i) / rate))
            i = j
        else:
            i += 1
    return runs


CUTS = {
    "kept_ranges": [
        {"start": 0.5, "end": 3.5},
        {"start": 5.0, "end": 8.0},
        {"start": 9.0, "end": 11.5},
    ]
}
FRAME_S = 1 / 30


class ContinuousAudioRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-fix2-"))
        self.source = make_source(self.tmp / "src.mp4")

    def render(self, cuts=None, **kwargs) -> Path:
        out = self.tmp / "out.mp4"
        intake_build.render_splice(
            self.source, cuts or CUTS, out, **kwargs
        )
        return out

    def test_streams_land_the_same_length(self) -> None:
        out = self.render(crossfade_s=0.04)
        durations = stream_durations(out)
        self.assertIn("video", durations)
        self.assertIn("audio", durations)
        # Spans 3.0 + 3.0 + 2.5 at 30 fps sum to exactly 8.5 s.
        self.assertAlmostEqual(durations["video"], 8.5, delta=FRAME_S)
        self.assertLessEqual(
            abs(durations["audio"] - durations["video"]), FRAME_S
        )

    def test_decoded_audio_does_not_outgrow_the_picture(self) -> None:
        out = self.render(crossfade_s=0.04)
        durations = stream_durations(out)
        samples, rate = decoded_audio(out, self.tmp)
        decoded_s = len(samples) / rate
        self.assertLessEqual(
            abs(decoded_s - durations["video"]), FRAME_S
        )

    def test_no_digital_silence_at_the_joins(self) -> None:
        out = self.render(crossfade_s=0.04)
        samples, rate = decoded_audio(out, self.tmp)
        runs = silence_runs(samples, rate)
        at_join = [
            run for run in runs
            if any(abs(run[0] - join) <= 0.06 for join in (3.0, 6.0))
        ]
        self.assertEqual(at_join, [])
        self.assertEqual(runs, [])

    def test_plain_butt_joins_are_still_continuous(self) -> None:
        out = self.render(crossfade_s=0.0)
        durations = stream_durations(out)
        self.assertLessEqual(
            abs(durations["audio"] - durations["video"]), FRAME_S
        )
        samples, rate = decoded_audio(out, self.tmp)
        self.assertEqual(silence_runs(samples, rate), [])

    def test_piece_spans_round_to_whole_frames(self) -> None:
        cuts = {
            "kept_ranges": [
                {"start": 0.5, "end": 2.53},   # 2.03 s -> 61 frames
                {"start": 4.0, "end": 6.51},   # 2.51 s -> 75 frames
            ]
        }
        out = self.render(cuts, crossfade_s=0.04)
        durations = stream_durations(out)
        expect = (61 + 75) / 30
        self.assertAlmostEqual(durations["video"], expect, delta=FRAME_S)
        self.assertLessEqual(
            abs(durations["audio"] - durations["video"]), FRAME_S
        )

    def test_single_piece_and_silent_picture_still_render(self) -> None:
        out = self.render(
            {"kept_ranges": [{"start": 1.0, "end": 4.0}]},
            crossfade_s=0.04,
        )
        durations = stream_durations(out)
        self.assertAlmostEqual(durations["audio"], 3.0, delta=FRAME_S)
        quiet = self.tmp / "quiet.mp4"
        intake_build.render_splice(
            self.source, CUTS, quiet, has_audio=False,
            crossfade_s=0.04,
        )
        durations = stream_durations(quiet)
        self.assertIn("video", durations)
        self.assertNotIn("audio", durations)


class JoinFadeMathTests(unittest.TestCase):
    def test_fade_is_clamped_by_the_shorter_piece(self) -> None:
        ranges = [{"start": 0.0, "end": 0.06}, {"start": 1.0, "end": 4.0}]
        fades = intake_build._join_fades(ranges, [0.06, 3.0], 0.04)
        self.assertEqual(len(fades), 1)
        self.assertLessEqual(fades[0], 0.06 * 0.45 + 1e-9)

    def test_own_fade_in_wins_over_the_plan_wide_choice(self) -> None:
        ranges = [
            {"start": 0.0, "end": 3.0},
            {"start": 4.0, "end": 7.0, "fade_in_s": 0.02},
        ]
        fades = intake_build._join_fades(ranges, [3.0, 3.0], 0.04)
        self.assertAlmostEqual(fades[0], 0.02)

    def test_zero_choice_means_butt_joins(self) -> None:
        ranges = [{"start": 0.0, "end": 3.0}, {"start": 4.0, "end": 7.0}]
        self.assertEqual(
            intake_build._join_fades(ranges, [3.0, 3.0], 0.0), [0.0]
        )

    def test_graph_trims_the_chain_to_the_exact_sample_count(self) -> None:
        ranges = [{"start": 0.5, "end": 3.5}, {"start": 5.0, "end": 8.0}]
        graph = intake_build._continuous_audio_graph(
            ranges, [3.0, 3.0], [0.04], 6 * 48000
        )
        self.assertIn("atrim=end_sample=288000", graph)
        self.assertIn("acrossfade=d=0.0400", graph)
        # The first piece's atrim runs one fade past its span end:
        # the extension is what the acrossfade consumes, so the
        # piece still contributes exactly its span.
        self.assertIn("atrim=start=0.5000:end=3.5400", graph)
        self.assertIn("atrim=start=5.0000:end=8.0000", graph)


if __name__ == "__main__":
    unittest.main()
