"""Message 2R fix round (0.24.0): Justin's Mac test of 0.23.0.

Five fixes, each with a test that names it:
1. The render builds piece by piece and joins with concat, so a
   many-piece edit never holds every piece in one ffmpeg pass.
2. Silence is measured from the audio and trimmed inside pieces.
3. The opening rule: setup talk and abandoned takes before the
   first real line are cut; the breath before the line stays.
4. A reworded retake (words reordered, a verb form changed) is
   caught, with the later take kept.
"""

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talk_to_your_editor import intake_build, opening, retakes, smooth  # noqa: E402

ROOM = -50.0
SPEECH = -20.0
BREATH = -35.0


def env_with(spans, total_s=12.0):
    env = [ROOM] * int(total_s / smooth.FRAME_S)
    for start, end, level in spans:
        a = int(start / smooth.FRAME_S)
        b = int(end / smooth.FRAME_S)
        for i in range(a, min(b, len(env))):
            env[i] = level
    return env


def word(text, start, end):
    return {"word": text, "start": start, "end": end}


# ---------------------------------------------------------------------------
# Fix 2: silence from the audio, inside pieces
# ---------------------------------------------------------------------------


def span(start, end):
    return {"start": start, "end": end}


class InternalSilence(unittest.TestCase):
    def test_long_silence_inside_a_piece_is_trimmed_to_the_pause(self):
        words = [word("hello", 1.0, 1.5), word("again", 4.0, 4.5)]
        env = env_with([(1.0, 1.5, SPEECH), (4.0, 4.5, SPEECH)])
        spans = [span(0.97, 1.53), span(3.97, 4.53)]
        ranges = [{"start": 0.9, "end": 4.6}]
        out, report = smooth.trim_internal_silences(
            words, ranges, env, spans=spans
        )
        self.assertEqual(report["trimmed"], 1)
        self.assertEqual(len(out), 2)
        heard = (out[0]["end"] - 1.5) + (4.0 - out[1]["start"])
        self.assertAlmostEqual(heard, 0.17, delta=0.03)
        self.assertGreater(report["seconds_removed"], 2.0)

    def test_breath_inside_the_silence_stays_whole(self):
        words = [word("hello", 1.0, 1.5), word("again", 4.5, 5.0)]
        env = env_with([
            (1.0, 1.5, SPEECH), (2.6, 2.95, BREATH), (4.5, 5.0, SPEECH),
        ])
        spans = [span(0.97, 1.53), span(2.57, 2.98), span(4.47, 5.03)]
        ranges = [{"start": 0.9, "end": 5.1}]
        out, _ = smooth.trim_internal_silences(
            words, ranges, env, spans=spans
        )
        self.assertEqual(len(out), 3)
        breath_piece = out[1]
        self.assertLessEqual(breath_piece["start"], 2.6)
        self.assertGreaterEqual(breath_piece["end"], 2.95)

    def test_long_head_quiet_is_trimmed(self):
        words = [word("first", 0.2, 0.6), word("hello", 3.0, 3.5)]
        env = env_with([(0.2, 0.6, SPEECH), (3.0, 3.5, SPEECH)])
        spans = [span(0.17, 0.63), span(2.97, 3.53)]
        ranges = [{"start": 0.1, "end": 0.7}, {"start": 1.2, "end": 3.6}]
        out, report = smooth.trim_internal_silences(
            words, ranges, env, spans=spans
        )
        self.assertEqual(report["trimmed"], 1)
        # The second piece now starts 0.04 s before the word's sound.
        self.assertAlmostEqual(out[1]["start"], 2.96, delta=0.02)

    def test_drifted_word_still_gets_its_head_trimmed(self):
        # The transcript stamps "But" at 4.92-5.28 but its speech
        # span is 5.30-6.10: the word sits wholly in the gap
        # between spans. The head quiet before it must still trim.
        words = [word("first", 0.2, 0.6), word("But", 4.92, 5.28)]
        env = env_with([(0.2, 0.6, SPEECH), (5.33, 6.07, SPEECH)])
        spans = [span(0.17, 0.63), span(5.30, 6.10)]
        ranges = [{"start": 0.1, "end": 0.7}, {"start": 4.85, "end": 6.2}]
        out, report = smooth.trim_internal_silences(
            words, ranges, env, spans=spans
        )
        self.assertEqual(report["trimmed"], 1)
        self.assertAlmostEqual(out[1]["start"], 5.29, delta=0.02)

    def test_short_gap_is_left_alone(self):
        words = [word("hello", 1.0, 1.5), word("again", 1.75, 2.25)]
        env = env_with([(1.0, 1.5, SPEECH), (1.75, 2.25, SPEECH)])
        spans = [span(0.97, 1.53), span(1.72, 2.28)]
        ranges = [{"start": 0.9, "end": 2.35}]
        out, report = smooth.trim_internal_silences(
            words, ranges, env, spans=spans
        )
        self.assertEqual(report["trimmed"], 0)
        self.assertEqual(len(out), 1)

    def test_protected_word_keeps_its_silence(self):
        words = [word("hello", 1.0, 1.5), word("again", 4.0, 4.5)]
        env = env_with([(1.0, 1.5, SPEECH), (4.0, 4.5, SPEECH)])
        spans = [span(0.97, 1.53), span(3.97, 4.53)]
        ranges = [{"start": 0.9, "end": 4.6}]
        out, report = smooth.trim_internal_silences(
            words, ranges, env, protected_words={1}, spans=spans
        )
        self.assertEqual(report["trimmed"], 0)
        self.assertEqual(len(out), 1)


# ---------------------------------------------------------------------------
# Fix 3: the opening
# ---------------------------------------------------------------------------


def _opening_fixture():
    # Setup talk, each bit set apart by a long pause, then an
    # abandoned take of the day line, a breath, then the real line.
    words = [
        word("I", 1.0, 1.2), word("love", 1.2, 1.5), word("you.", 1.5, 1.9),
        word("Good.", 3.4, 3.8),
        word("Thank", 5.3, 5.6), word("you.", 5.6, 6.0),
        word("Today's", 7.5, 7.8), word("day", 7.8, 8.0),
        word("30", 8.0, 8.3), word("of", 8.3, 8.5),
        word("seeing", 8.5, 8.8), word("if", 8.8, 9.0),
        word("God", 9.0, 9.3), word("will", 9.3, 9.6),
        word("Today's", 11.5, 11.8), word("day", 11.8, 12.0),
        word("30", 12.0, 12.3), word("of", 12.3, 12.5),
        word("seeing", 12.5, 12.8), word("if", 12.8, 13.0),
        word("God", 13.0, 13.3), word("will", 13.3, 13.6),
        word("use", 13.6, 13.9), word("cold", 13.9, 14.2),
        word("water.", 14.2, 14.6),
    ]
    spans = [(w["start"], w["end"], SPEECH) for w in words]
    spans.append((10.9, 11.35, BREATH))  # the breath before the line
    env = env_with(spans, total_s=16.0)
    ranges = [
        {"start": 0.9, "end": 2.0},
        {"start": 3.3, "end": 3.9},
        {"start": 5.2, "end": 6.1},
        {"start": 7.4, "end": 9.7},
        {"start": 11.4, "end": 14.7},
    ]
    return words, ranges, env


class OpeningRule(unittest.TestCase):
    def test_setup_talk_and_abandoned_take_are_cut_head_trimmed(self):
        words, ranges, env = _opening_fixture()
        out, report = opening.opening_plan(words, ranges, env)
        self.assertTrue(report["fired"])
        self.assertEqual(report["first_line_word"], 14)
        self.assertEqual(len(report["cut_words"]), 14)
        kept = set()
        for rng in out:
            if "insert_kind" not in rng:
                kept.update(smooth._range_words(words, rng))
        self.assertEqual(min(kept), 14)
        # Filming-day P0-3: the head breath no longer stays;
        # the plan starts right before the first word (its
        # stamp here, within the 0.2 s proof bar of its sound).
        self.assertIsNone(report["breath_kept"])
        self.assertFalse(
            any(r.get("insert_kind") == "signature_breath" for r in out)
        )
        self.assertGreaterEqual(out[0]["start"], words[14]["start"] - 0.35)

    def test_a_flowing_opening_is_left_alone(self):
        words = [
            word("It", 1.0, 1.2), word("was", 1.2, 1.4),
            word("cold.", 1.4, 1.8),
            word("Really", 2.0, 2.3), word("cold", 2.3, 2.6),
            word("water", 2.6, 2.9), word("that", 2.9, 3.1),
            word("morning", 3.1, 3.5), word("changed", 3.5, 3.9),
            word("everything.", 3.9, 4.4),
        ]
        env = env_with([(w["start"], w["end"], SPEECH) for w in words],
                       total_s=6.0)
        ranges = [{"start": 0.9, "end": 4.5}]
        out, report = opening.opening_plan(words, ranges, env)
        self.assertFalse(report["fired"])
        self.assertEqual(out, ranges)


# ---------------------------------------------------------------------------
# Fix 4: the reworded retake
# ---------------------------------------------------------------------------


def _unit(tokens, start_s, end_s, terminal):
    return {
        "protected": False,
        "settled": False,
        "start_s": start_s,
        "end_s": end_s,
        "kept_tokens": tokens,
        "terminal": terminal,
    }


class RewordedRetake(unittest.TestCase):
    def test_justins_pair_scores_as_a_duplicate(self):
        earlier = _unit(
            ["every", "time", "i've", "gotten", "into", "this", "tank"],
            172.94, 174.34, True,
        )
        later = _unit(
            ["i", "said", "today's", "day", "blank", "of",
             "every", "time", "i", "got", "into", "this", "tank",
             "i've", "said", "today's", "day", "blank", "of",
             "seeing", "if"],
            174.38, 180.42, False,
        )
        settings = retakes.settings_from_profile(None)
        scored = retakes._score_pair(earlier, later, [], settings)
        self.assertIsNotNone(scored)
        self.assertEqual(scored["band"], "a")

    def test_window_offset_marks_where_the_real_take_starts(self):
        earlier = _unit(
            ["every", "time", "i've", "gotten", "into", "this", "tank"],
            172.94, 174.34, True,
        )
        later = _unit(
            ["i", "said", "today's", "day", "blank", "of",
             "every", "time", "i", "got", "into", "this", "tank",
             "i've", "said", "today's", "day", "blank", "of",
             "seeing", "if"],
            174.38, 180.42, False,
        )
        settings = retakes.settings_from_profile(None)
        scored = retakes._score_pair(earlier, later, [], settings)
        self.assertTrue(scored["reworded"])
        self.assertEqual(scored["window_offset"], 6)

    def test_leftover_head_before_the_real_take_is_cut(self):
        unit = {
            "kept_indices": list(range(41, 62)),
            "kept_tokens": [
                "i", "said", "today's", "day", "blank", "of",
                "every", "time", "i", "got", "into", "this", "tank",
                "i've", "said", "today's", "day", "blank", "of",
                "seeing", "if",
            ],
        }
        raw_words = [
            {"word": "word"} for _ in range(41)
        ] + [
            {"word": w} for w in (
                "I said today's day blank of Every time I got into "
                "this tank I've said today's day blank of seeing if"
            ).split()
        ]
        scored = {"reworded": True, "negation": False,
                  "window_offset": 6, "band": "a"}
        head = retakes._leftover_head(scored, unit, raw_words)
        self.assertIsNotNone(head)
        self.assertEqual(head["cut_start"], 41)
        self.assertEqual(head["cut_end"], 47)
        self.assertEqual(head["keep_start"], 47)
        self.assertEqual(head["band"], "a")

    def test_reworded_pair_keeps_the_later_unit(self):
        units = [
            {"settled": False, "protected": False, "terminal": True},
            {"settled": False, "protected": False, "terminal": False},
        ]
        settings = retakes.settings_from_profile(None)
        keep = retakes._kept_unit_index(units, [0, 1], settings, {(0, 1)})
        self.assertEqual(keep, 1)
        keep = retakes._kept_unit_index(units, [0, 1], settings)
        self.assertEqual(keep, 0)

    def test_buried_repeat_at_distance_is_a_callback_not_a_take(self):
        # The day line's tail recurs inside the real take's
        # sentence. With a complete sentence between them and no
        # opening anchor, the pair is a callback: no record.
        day_line = _unit(
            ["today's", "day", "30", "of", "seeing", "if", "god",
             "will", "use", "cold", "water", "to", "help", "get",
             "rid", "of", "my", "thyroid", "cancer"],
            152.08, 158.96, True,
        )
        between = _unit(
            ["every", "time", "i've", "gotten", "into", "this", "tank"],
            172.94, 174.34, True,
        )
        take = _unit(
            ["every", "time", "i", "got", "into", "this", "tank",
             "i've", "said", "today's", "day", "blank", "of",
             "seeing", "if", "god", "will", "use", "cold", "water",
             "to", "help", "get", "rid", "of", "my", "thyroid",
             "cancer"],
            176.0, 190.0, False,
        )
        settings = retakes.settings_from_profile(None)
        scored = retakes._score_pair(day_line, take, [between], settings)
        self.assertIsNone(scored)

    def test_unrelated_line_does_not_pair(self):
        earlier = _unit(
            ["every", "time", "i've", "gotten", "into", "this", "tank"],
            172.94, 174.34, True,
        )
        later = _unit(
            ["the", "water", "was", "cold", "and", "i", "stayed",
             "in", "for", "two", "minutes"],
            180.0, 185.0, True,
        )
        settings = retakes.settings_from_profile(None)
        scored = retakes._score_pair(earlier, later, [], settings)
        self.assertIsNone(scored)


# ---------------------------------------------------------------------------
# Fix 1: the render is per-piece plus concat (the picture still
# is, in 0.26; the sound became one continuous pass, Fix 2)
# ---------------------------------------------------------------------------


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg needed")
class PerPieceRender(unittest.TestCase):
    def test_many_pieces_render_with_one_ffmpeg_per_piece(self):
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        source = tmp / "src.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc=size=160x120:rate=20:duration=8",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
             "-c:v", "libx264", "-crf", "24", "-pix_fmt", "yuv420p",
             "-c:a", "aac", str(source)],
            check=True,
        )
        ranges = [
            {"start": round(i * 0.25, 2), "end": round(i * 0.25 + 0.2, 2)}
            for i in range(30)
        ]
        calls = []
        real = intake_build._run_ffmpeg

        def counting(command, timeout=1800):
            calls.append(command)
            return real(command, timeout)

        try:
            intake_build._run_ffmpeg = counting
            out = intake_build.render_splice(
                source, {"kept_ranges": ranges}, tmp / "out.mp4",
                has_audio=True,
            )
        finally:
            intake_build._run_ffmpeg = real
        self.assertTrue(out.is_file())
        # Fix 2 (0.26): 30 piece renders (picture only, one
        # ffmpeg each, memory still flat), then exactly 1 video
        # concat join, exactly 1 single-pass audio build, and 1
        # mux. The old shape (each piece carrying its own AAC,
        # joined by one concat) put encoder priming at every
        # join and is gone on purpose.
        self.assertEqual(len(calls), 33)
        concat = calls[-3]
        self.assertIn("concat", concat)
        audio_calls = [
            c for c in calls
            if any("[outa]" in str(part) for part in c)
        ]
        self.assertEqual(len(audio_calls), 1)
        for piece_call in calls[:30]:
            self.assertIn("-an", piece_call)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of", "csv=p=0", str(out)],
            capture_output=True, text=True,
        )
        self.assertAlmostEqual(float(probe.stdout.strip()), 6.0, delta=0.35)
        decode = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(out), "-f", "null", "-"],
            capture_output=True, text=True,
        )
        self.assertEqual(decode.returncode, 0)
        self.assertEqual(decode.stderr, "")


if __name__ == "__main__":
    unittest.main()
