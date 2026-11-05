"""Fixes 7, 8, and 9 (0.27): slivers, wrong strikes, abandoned takes.

Fix 7: a short spoken line between two pauses is content, not a
sliver. A piece is dropped for its length only when it holds no
measured speech sound (a word's sound bounds inside it, or the
middle of a kept word's sound). And the sound truth remove step
never cuts part of a sentence: when a cut would leave a fragment
of the sentence standing alone, it refuses and records the case.

Fix 8: a few shared words inside a long line are not a retake.
A pair is a candidate only when the overlap covers most of the
shorter side AND a real share of the longer side; a complete
take is never struck in favor of a fragment. And a your-call
(gold) span is never cut without the creator's yes: the
detected-repeat settlement, the retake pass, and the smooth
pass all treat open gold spans as blocked.

Fix 9: a stopped take that shares its opening with the complete
take after it is a restart, detected locally (no model), and a
negation makes a pair the creator's call only when exactly one
copy carries it and both copies are complete. A "not" on both
copies (or neither) is a plain retake, and the stopped earlier
takes are cut in both provider paths.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from talk_to_your_editor import retakes, smooth, webapp  # noqa: E402
from talk_to_your_editor.timeline import Timeline  # noqa: E402

try:
    from test_retakes import timeline_for
except ImportError:  # discovery rooted at the repo instead of tests/
    from tests.test_retakes import timeline_for

ROOM = -50.0
SPEECH = -20.0

FILLER_A = "We are talking about the morning routine again today."
FILLER_B = "It shapes the whole day more than people think now."


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


def span(start, end):
    return {"start": start, "end": end}


# ---------------------------------------------------------------------------
# Fix 7 (a): the sliver rule reads sound, not just kept midpoints
# ---------------------------------------------------------------------------


class SliverRuleTests(unittest.TestCase):
    def test_piece_holds_sound_reads_containment_and_kept_mids(self):
        bounds = [(2.0, 2.03), (3.0, 3.3)]
        # A whole word's sound bounds inside the piece: sound.
        self.assertTrue(smooth.piece_holds_sound(bounds, 1.99, 2.035))
        # The bare tail of a word's sound: no whole word, no
        # kept midpoint, so no sound the piece can claim.
        self.assertFalse(smooth.piece_holds_sound(bounds, 3.27, 3.30))
        # The middle of a kept word's sound inside: sound.
        self.assertTrue(
            smooth.piece_holds_sound(bounds, 3.10, 3.20, kept_mids=[3.15])
        )
        # A kept midpoint outside the piece does not hold it.
        self.assertFalse(
            smooth.piece_holds_sound(bounds, 3.10, 3.14, kept_mids=[3.15])
        )
        self.assertFalse(
            smooth.piece_holds_sound(bounds, 3.10, 3.14, kept_mids=[])
        )

    def test_drop_soundless_pieces_keeps_a_sounding_fragment(self):
        bounds = [(2.0, 2.03), (3.0, 3.3)]
        ranges = [
            {"start": 1.99, "end": 2.035},   # holds word 0's sound
            {"start": 3.27, "end": 3.30},    # a cut word's tail only
            {"start": 4.0, "end": 4.0},      # no length at all
            {"start": 5.0, "end": 5.4},      # ordinary piece
        ]
        out = smooth.drop_soundless_pieces([], ranges, bounds)
        self.assertEqual(
            [(r["start"], r["end"]) for r in out],
            [(1.99, 2.035), (5.0, 5.4)],
        )

    def test_short_piece_holding_a_whole_words_sound_is_not_dropped(self):
        # A tiny word whose whole measured sound is 0.03 s, in a
        # 0.045 s piece: content, however short.
        words = [word("Hello", 1.0, 1.4), word("a", 2.0, 2.03),
                 word("world", 3.0, 3.4)]
        spans = [span(0.98, 1.42), span(2.0, 2.03), span(2.98, 3.42)]
        env = env_with(
            [(s["start"], s["end"], SPEECH) for s in spans], total_s=5.0
        )
        ranges = [
            {"start": 0.95, "end": 1.6},
            {"start": 1.99, "end": 2.035},
            {"start": 2.95, "end": 3.6},
        ]
        out, report = smooth.sound_truth_pass(
            words, ranges, env, spans=spans
        )
        self.assertEqual(report["slivers_dropped"], 0)
        self.assertEqual(len(out), 3)


# ---------------------------------------------------------------------------
# Fix 7 (b): the remove step never cuts part of a sentence
# ---------------------------------------------------------------------------


class SentenceSplitRefusalTests(unittest.TestCase):
    def _words(self):
        return [
            word("This", 1.0, 1.2),
            word("was", 1.25, 1.4),
            word("worth", 1.45, 1.7),
            word("it.", 1.75, 1.95),
        ]

    def _spans(self):
        return [span(0.98, 1.22), span(1.23, 1.42),
                span(1.43, 1.72), span(1.73, 1.97)]

    def _env(self, spans):
        return env_with(
            [(s["start"], s["end"], SPEECH) for s in spans], total_s=4.0
        )

    def test_cutting_a_sentence_head_leaving_a_lone_fragment_is_refused(self):
        # Justin's case: "This was worth it." with the first
        # three words settled-cut. Taking their sound out would
        # leave a lone "it." playing, so the pass refuses, keeps
        # the line whole, and records the case as unresolved.
        words, spans = self._words(), self._spans()
        ranges = [{"start": 0.95, "end": 2.05}]
        out, report = smooth.sound_truth_pass(
            words, ranges, self._env(spans), spans=spans,
            blocked_words={0, 1, 2},
        )
        self.assertEqual(report["cut_sounds_removed"], 0)
        refused = [
            u for u in report["unresolved"]
            if "split its sentence" in u["reason"]
        ]
        self.assertEqual(
            sorted(u["word_index"] for u in refused), [0, 1, 2]
        )
        self.assertEqual(out, ranges)

    def test_the_same_cut_applies_when_no_fragment_would_stand(self):
        # Control: the whole sentence is settled-cut, so no
        # fragment stands alone and the removal proceeds.
        words, spans = self._words(), self._spans()
        ranges = [{"start": 0.95, "end": 2.05}]
        out, report = smooth.sound_truth_pass(
            words, ranges, self._env(spans), spans=spans,
            blocked_words={0, 1, 2, 3},
        )
        self.assertEqual(report["cut_sounds_removed"], 4)
        self.assertFalse(
            any(
                "split its sentence" in u["reason"]
                for u in report["unresolved"]
            )
        )


# ---------------------------------------------------------------------------
# Fix 8 (a): shared phrases are not retakes; completeness wins
# ---------------------------------------------------------------------------


class SharedPhraseGateTests(unittest.TestCase):
    def test_a_short_line_recurring_at_a_long_lines_tail_is_not_a_pair(self):
        # Justin's "This thyroid cancer" shape: a short line
        # whose words come back at the END of the next line is a
        # line of its own, not a take of it.
        tl = timeline_for(
            [
                FILLER_A,
                "This thyroid cancer",
                "Because if I'm going to be praying and believing "
                "that God is going to set me free from this thyroid "
                "cancer I need to stop holding on to it.",
                FILLER_B,
            ],
            gap_after={1: 2.0},
        )
        self.assertEqual(retakes.find_pairs(tl, None), [])

    def test_a_complete_line_sharing_a_phrase_with_a_longer_one_is_not_a_pair(self):
        # Justin's "I would love my life" shape: a complete line
        # and a longer complete take sharing only "I would love"
        # are two lines, not two takes.
        tl = timeline_for(
            [
                FILLER_A,
                "I would love my life.",
                "If God would ever want me to walk through "
                "something like cancer, I would love to be able "
                "to use the tools and resources.",
                FILLER_B,
            ]
        )
        self.assertEqual(retakes.find_pairs(tl, None), [])

    def test_a_genuine_complete_retake_still_pairs(self):
        line = "The water is colder than it looks this morning."
        tl = timeline_for([FILLER_A, line, line, FILLER_B])
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["band"], "a")


# ---------------------------------------------------------------------------
# Fix 9: restarts are detected locally; negation flags on asymmetry only
# ---------------------------------------------------------------------------


class RestartDetectionTests(unittest.TestCase):
    def test_a_stopped_take_sharing_its_opening_with_the_next_unit_is_a_restart(self):
        # The stopped take is its own unit (a pause split it);
        # the completion rewords the opening ("I've gotten" /
        # "I got") and finishes the line.
        tl = timeline_for(
            [
                FILLER_A,
                "Every time I've gotten into this tank I said",
                "Every time I got into this tank I've said the "
                "whole line out loud today.",
                FILLER_B,
            ],
            gap_after={1: 2.0},
        )
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["band"], "a")
        self.assertEqual(
            pairs[0]["cut"]["text"],
            "Every time I've gotten into this tank I said",
        )

    def test_a_reworded_restart_inside_one_unit_is_detected(self):
        # The same shape with no pause split: both tries live in
        # one unit, and the exact-run scan misses the reworded
        # opening. The fuzzy restart scan catches it.
        tl = timeline_for(
            [
                FILLER_A,
                "Every time I've gotten into this tank I said "
                "today's day blank of every time I got into this "
                "tank I've said today's day blank of seeing if "
                "God will use cold water today.",
                FILLER_B,
            ]
        )
        pairs = retakes.find_pairs(tl, None)
        cuts = [p for p in pairs if p["shape"] == "internal_restart"]
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["band"], "a")
        self.assertEqual(
            cuts[0]["cut"]["text"],
            "Every time I've gotten into this tank I said "
            "today's day blank of",
        )

    def test_a_shared_negation_restart_is_a_plain_cut_not_a_flag(self):
        # Justin's "I'm not saying" shape: both copies carry the
        # "not", so the stopped take is cut, not flagged unsure.
        tl = timeline_for(
            [
                FILLER_A,
                "I'm not saying those that have cancer should "
                "jump into cold water and get rid of the thyroid "
                "cancer I'm not saying that those who have cancer "
                "should jump into cold water and get rid of it "
                "today.",
                FILLER_B,
            ]
        )
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["band"], "b")
        self.assertFalse(pairs[0]["negation"])
        ops = retakes.strict_cut_operations(tl, None, pairs=pairs)
        self.assertEqual(len(ops), 1)
        first = len(FILLER_A.split())
        self.assertEqual(ops[0]["word_start"], first)
        self.assertEqual(ops[0]["word_end"], first + 19)

    def test_a_one_sided_negation_restart_still_flags(self):
        # The speaker corrects himself ON the negation ("let go
        # of it, not hey..."): one copy carries the "not", the
        # completion does not, so it stays the creator's call.
        tl = timeline_for(
            [
                FILLER_A,
                "Let go of it not hey let go of getting healed "
                "and stop claiming it today.",
                FILLER_B,
            ]
        )
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["band"], "flag")
        self.assertTrue(pairs[0]["negation"])


# ---------------------------------------------------------------------------
# Fix 8 (b): a your-call span is never cut without the creator's yes
# ---------------------------------------------------------------------------


def _gold_job(tmp, sentences):
    """A fresh job folder with the given sentences, all kept."""
    import json

    from test_retakes import make_cuts, make_transcript

    job = tmp / "job"
    job.mkdir()
    transcript = make_transcript(sentences)
    (job / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (job / "cuts.json").write_text(
        json.dumps(make_cuts(transcript)), encoding="utf-8"
    )
    return webapp.WebAppConfig(job), transcript


GOLD_SENTENCES = [
    "We are talking about the morning routine again.",
    "I will not give up on this family.",
    "I will give up on this family.",
    "It shapes the whole day more than people think.",
]
# FILLER (8 words) | negated line (words 8-15) | plain line
# (words 16-22) | FILLER_B. The negated line is the span the
# creator is still deciding (gold, open).


def _open_gold_candidate(word_start=8, word_end=16):
    return {
        "gold_id": webapp._gold_id(word_start, word_end, "retake"),
        "word_start": word_start,
        "word_end": word_end,
        "kind": "retake",
        "source": "retakes",
        "status": "open",
        "reason": "The copies say different things; your call.",
        "text": "I will not give up on this family.",
        "copies_differ": True,
    }


class GoldProtectionTests(unittest.TestCase):
    def setUp(self):
        import shutil
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="tye-fix789-gold-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_settlement_never_strikes_an_open_gold_span(self):
        # The open span that still blocks under filming-day
        # P0-4 is a parallel your-call (performed repetition
        # inside one sentence); differing takes now settle as
        # cuts with bring-back records instead of waiting.
        config, _transcript = _gold_job(self.tmp, GOLD_SENTENCES)
        waiting = _open_gold_candidate()
        waiting["reason"] = retakes._FLAG_PARALLEL_REASON
        webapp._record_gold_spans(config, [waiting])
        candidate = dict(_open_gold_candidate())
        candidate.pop("status")
        candidate["copies_differ"] = False
        settled = webapp._record_gold_detected_repeats(
            config, [candidate], "cleanest"
        )
        self.assertEqual(settled, [])
        records = webapp._load_gold(config)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "open")
        cuts = webapp._effective_cuts(config)
        timeline = Timeline(_transcript, cuts)
        for i in range(8, 16):
            self.assertEqual(timeline.word_status(i), "kept")

    def test_the_same_candidate_settles_when_no_gold_waits(self):
        config, _transcript = _gold_job(self.tmp, GOLD_SENTENCES)
        candidate = dict(_open_gold_candidate())
        candidate.pop("status")
        candidate["copies_differ"] = False
        settled = webapp._record_gold_detected_repeats(
            config, [candidate], "cleanest"
        )
        self.assertEqual(len(settled), 1)
        cuts = webapp._effective_cuts(config)
        timeline = Timeline(_transcript, cuts)
        for i in range(8, 16):
            self.assertEqual(timeline.word_status(i), "cut")

    def test_the_retake_pass_never_proposes_a_cut_over_open_gold(self):
        line = "I will not give up on this family."
        sentences = [
            "We are talking about the morning routine again.",
            line,
            line,
            "It shapes the whole day more than people think.",
        ]
        config, _transcript = _gold_job(self.tmp, sentences)
        webapp._record_gold_spans(config, [_open_gold_candidate()])
        summary = webapp._run_retake_pass(config, "mock")
        self.assertEqual(summary["pairs"], 0)
        cuts = webapp._effective_cuts(config)
        timeline = Timeline(_transcript, cuts)
        for i in range(8, 16):
            self.assertEqual(timeline.word_status(i), "kept")


# ---------------------------------------------------------------------------
# Fix 7 (c) and the Day 30 outcomes, at plan level
# ---------------------------------------------------------------------------

STUDY = (
    Path.home() / "workspace" / "goals"
    / "muse-news-content-engine-200k-subscribers" / "files"
    / "creator-platform" / "study"
)
RAW_VIDEO = STUDY / "pairs" / "5 Before.MOV"
RAW_TX = STUDY / "transcripts" / "set5-raw.transcript.json"

# The six lines Justin's 0.26 run lost, by word index in the
# Day 30 transcript.
SIX_LINES = {
    "Why?": (82, 83),
    "Why cold water?": (309, 315),
    "So here we are.": (461, 465),
    "So what's next?": (661, 669),
    "December 29th, that's the next time": (690, 705),
    "This was worth it.": (610, 614),
}
STOPPED_TAKES = {
    "the stopped Every time take": (34, 47),
    "the stopped I'm not saying take": (249, 268),
    "the stopped That if God try": (416, 426),
}
KEPT_TAKES = {
    "the complete Every time take": (47, 56),
    "the I'm not saying keeper": (268, 287),
    "the complete If God take": (426, 451),
    "This thyroid cancer.": (83, 86),
    "the Because if I'm line": (86, 114),
}


@unittest.skipUnless(
    RAW_VIDEO.is_file() and RAW_TX.is_file(),
    "the Day 30 study pair is not on this machine",
)
class StampOverlapRuleTests(unittest.TestCase):
    """Fix 7, the Day 30 "It is the Lord's" shape: a sentence
    head heard just before its stamp, in the piece its stamp
    window touches. The plan's ranges claim that stretch of
    the word, so the truth pass must not take its sound out
    as if the plan had cut it. A word whose stamp touches no
    piece is still removed the way fix 1 settled."""

    def _fixture(self, head_stamp):
        words = [
            word("mine.", 1.0, 1.4),
            word("It", *head_stamp),
            word("is.", 3.2, 3.6),
        ]
        spans = [span(0.97, 1.25), span(1.3, 1.58), span(3.17, 3.63)]
        env = env_with(
            [(s["start"], s["end"], SPEECH) for s in spans],
            total_s=6.0,
        )
        ranges = [
            {"start": 0.95, "end": 1.6},
            {"start": 3.1, "end": 3.7},
        ]
        return words, spans, env, ranges

    def test_a_sentence_head_whose_stamp_touches_its_piece_stays(self):
        # Stamp 1.45-2.3: midpoint (1.87) in the gap, but the
        # window overlaps the piece [0.95, 1.6] holding its
        # sound (span 1.3-1.58, midpoint inside the piece).
        words, spans, env, ranges = self._fixture((1.45, 2.3))
        out, report = smooth.sound_truth_pass(words, ranges, env, spans=spans)
        self.assertEqual(report["cut_sounds_removed"], 0)
        bounds = smooth.sound_word_bounds(words, env, spans)
        after = smooth.sound_statuses(words, out, bounds)
        self.assertEqual(after[1], "kept")

    def test_a_word_whose_stamp_touches_no_piece_is_removed(self):
        # Stamp 1.7-2.3: midpoint outside and the window clear
        # of the piece; the fix 1 removal still applies.
        words, spans, env, ranges = self._fixture((1.7, 2.3))
        out, report = smooth.sound_truth_pass(words, ranges, env, spans=spans)
        self.assertEqual(report["cut_sounds_removed"], 1)
        bounds = smooth.sound_word_bounds(words, env, spans)
        after = smooth.sound_statuses(words, out, bounds)
        self.assertEqual(after[1], "cut")


class Day30Fixtures(unittest.TestCase):
    def test_the_six_lines_survive_and_the_stopped_takes_go(self):
        import json
        import os
        import shutil
        import subprocess
        import tempfile
        from unittest import mock

        from talk_to_your_editor import intake_build, transcribe
        from talk_to_your_editor.webapp import WebAppConfig

        raw_data = json.loads(RAW_TX.read_text())
        raw_words = raw_data["words"]
        segments = raw_data.get("segments")
        tmp = Path(tempfile.mkdtemp(prefix="fix789-day30-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        wav = tmp / "raw-16k.wav"
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(RAW_VIDEO),
             "-vn", "-ac", "1", "-ar", "16000", str(wav)],
            capture_output=True, text=True, timeout=1200,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-300:])
        spans = transcribe.measure_speech_spans(wav)
        home = tmp / "home"
        home.mkdir()
        job = tmp / "job"
        job.mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            os.environ.pop("MODEL_API_KEY", None)
            os.environ.pop("ELEVENLABS_API_KEY", None)
            transcript_result = {
                "engine": raw_data.get("model", "study"),
                "duration": float(raw_data["duration"]),
                "words": raw_words,
                "segments": segments,
            }
            intake_build.build_job_files(
                job, RAW_VIDEO.name, transcript_result, spans,
                {"duration": float(raw_data["duration"])},
                captions_on=False,
            )
            link = job / RAW_VIDEO.name
            if not link.exists():
                link.symlink_to(RAW_VIDEO)
            work_dir = job / "intake-work"
            work_dir.mkdir(exist_ok=True)
            shutil.copyfile(wav, work_dir / "audio.wav")
            config = WebAppConfig(job, launch_home=tmp / "launch")
            with mock.patch.object(
                webapp, "_render_session_splice", return_value=None
            ):
                result = webapp._run_front_door(config, {
                    "provider": "mock",
                    "remove_retakes": True,
                    "keep_mode": "last",
                    "cut_silence": True,
                    "buffer_s": 0.1,
                    "remove_fillers": False,
                    "mode": "cleanest",
                })
            self.assertTrue(result.get("ok"), result)
            cuts = webapp._effective_cuts(config)
        transcript = json.loads((job / "transcript.json").read_text())
        timeline = Timeline(transcript, cuts)
        statuses = [timeline.word_status(i) for i in range(len(raw_words))]

        for name, (start, end) in SIX_LINES.items():
            for i in range(start, end):
                self.assertEqual(
                    statuses[i], "kept",
                    f"{name}: word {i} ({raw_words[i]['word']!r}) "
                    f"must survive the plan",
                )
        for name, (start, end) in STOPPED_TAKES.items():
            for i in range(start, end):
                self.assertNotEqual(
                    statuses[i], "kept",
                    f"{name}: word {i} ({raw_words[i]['word']!r}) "
                    f"must be out of the plan",
                )
        for name, (start, end) in KEPT_TAKES.items():
            for i in range(start, end):
                self.assertEqual(
                    statuses[i], "kept",
                    f"{name}: word {i} ({raw_words[i]['word']!r}) "
                    f"must stay in the plan",
                )


if __name__ == "__main__":
    unittest.main()
