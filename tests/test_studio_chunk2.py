"""Phase 2 chunk 2 (0.25): findings 6, 7, 8.

Finding 6: a review spot exists only when a join still fails a
measured check after the fixes, and every surface (Done bubble,
review link, transcript header, the persisted Done payload)
reads one count computed once from the final plan.

Finding 7: a detected retake is cut by default, last take kept.
It waits as your call only when the copies differ in meaning: a
negation in one copy, different numbers, or an intentional
(parallel) repeat.

Finding 8: questions are answered from the record and never run
an edit; statement commands still run.
"""

from __future__ import annotations

import json
import os
import re
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

from talk_to_your_editor import retakes, smooth, webapp
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig, create_server

VIDEO_BYTES = bytes(range(256)) * 32
STUDIO = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "studio.html"
).read_text(encoding="utf-8")


def make_transcript(sentences):
    words = []
    t = 0.0
    for sentence in sentences:
        for token in sentence.split():
            words.append(
                {"word": token, "start": round(t, 3), "end": round(t + 0.35, 3)}
            )
            t += 0.45
    return {"words": words, "duration": round(t, 3)}


def make_cuts(transcript):
    total = float(transcript["words"][-1]["end"]) + 0.5
    return {"kept_ranges": [{"start": 0.0, "end": total}],
            "source_duration_s": total}


def _words(sentences):
    return make_transcript(sentences)["words"]


class JoinCheckHonestyTests(unittest.TestCase):
    """Finding 6a: fixed joins count as smooth; nothing is
    flagged without a remaining failed measure."""

    ROOM = -50.0
    SPEECH = -20.0

    def _env(self, spans, total_s=10.0):
        env = [self.ROOM] * int(total_s / smooth.FRAME_S)
        for start, end, level in spans:
            a = int(start / smooth.FRAME_S)
            b = int(end / smooth.FRAME_S)
            for i in range(a, min(b, len(env))):
                env[i] = level
        return env

    def test_fixed_join_counts_smooth_and_is_not_a_spot(self):
        words = [
            {"word": "hello", "start": 1.0, "end": 1.5},
            {"word": "world", "start": 3.0, "end": 3.5},
        ]
        env = self._env([(1.0, 1.5, self.SPEECH), (3.0, 3.5, self.SPEECH)])
        # The first piece ends inside "hello": a real measured
        # failure, and the check fixes it by moving the edge.
        ranges = [{"start": 0.9, "end": 1.3}, {"start": 2.9, "end": 3.6}]
        _, check = smooth.join_check(words, ranges, env)
        self.assertEqual(check["joins_checked"], 1)
        kinds = [k for i in check["issues"] for k in i["kinds"]]
        self.assertIn("cut_inside_word", kinds)
        self.assertTrue(all(i["fixed"] for i in check["issues"]))
        # A fixed join is smooth, and it is not offered for
        # review. Before the fix, joins_smooth counted every
        # join that had ever carried an issue, so this read 0.
        self.assertEqual(check["joins_smooth"], 1)
        self.assertEqual(check["your_call"], [])

    def test_unfixable_failure_stays_a_spot(self):
        words = [
            {"word": "hello", "start": 1.0, "end": 1.5},
            {"word": "world", "start": 1.56, "end": 2.0},
        ]
        env = self._env([(1.0, 1.5, self.SPEECH), (1.56, 2.0, self.SPEECH)])
        ranges = [{"start": 0.9, "end": 1.52}, {"start": 1.53, "end": 2.1}]
        _, check = smooth.join_check(words, ranges, env)
        kinds = [k for i in check["your_call"] for k in i["kinds"]]
        self.assertIn("pause_under_60ms", kinds)
        self.assertEqual(check["joins_smooth"], 0)

    def test_measure_only_check_and_the_crossfade_remedy(self):
        words = [
            {"word": "hello", "start": 1.0, "end": 1.5},
            {"word": "world", "start": 3.0, "end": 3.5},
        ]
        # The two words sit 12 dB apart: a real loudness jump at
        # the join, with no other failure (long pause, whole
        # words, contiguous sentence).
        env = self._env([(1.0, 1.5, -20.0), (3.0, 3.5, -32.0)])
        ranges = [{"start": 0.9, "end": 1.55}, {"start": 2.96, "end": 3.6}]
        # Measure-only, no remedy in place: the join is a spot,
        # and the check changed nothing.
        out, check = smooth.join_check(words, ranges, env, measure_only=True)
        self.assertEqual(
            [k for i in check["your_call"] for k in i["kinds"]],
            ["loudness_jump"],
        )
        self.assertEqual(check["joins_smooth"], 0)
        self.assertNotIn("fade_in_s", out[1])
        # Measure-only, the remedy (the long crossfade) already
        # on the join: handled, not a spot.
        faded = [dict(ranges[0]), dict(ranges[1], fade_in_s=smooth.LONG_FADE_S)]
        _, settled = smooth.join_check(words, faded, env, measure_only=True)
        self.assertEqual(settled["your_call"], [])
        self.assertEqual(settled["joins_smooth"], 1)
        # The fixing check applies the remedy itself.
        out, fixed_check = smooth.join_check(words, ranges, env)
        self.assertEqual(out[1].get("fade_in_s"), smooth.LONG_FADE_S)
        self.assertEqual(fixed_check["your_call"], [])

    def test_take_start_is_not_a_mid_sentence_start(self):
        words = [
            {"word": "the", "start": 1.0, "end": 1.2},
            {"word": "water", "start": 1.3, "end": 1.7},
            {"word": "helps", "start": 2.0, "end": 2.4},
            {"word": "people", "start": 2.5, "end": 2.9},
        ]
        env = self._env([
            (1.0, 1.2, self.SPEECH), (1.3, 1.7, self.SPEECH),
            (2.0, 2.4, self.SPEECH), (2.5, 2.9, self.SPEECH),
        ])
        ranges = [
            {"start": 0.9, "end": 1.25},
            {"start": 1.95, "end": 3.0},
        ]
        _, check = smooth.join_check(words, ranges, env)
        kinds = [k for i in check["your_call"] for k in i["kinds"]]
        self.assertIn("mid_sentence_start", kinds)
        # The same join, when "helps" is a settled retake's kept
        # take start, is a line start, not a mid-sentence start.
        _, settled = smooth.join_check(
            words, ranges, env, take_start_words={2}
        )
        self.assertEqual(settled["your_call"], [])
        self.assertEqual(settled["joins_smooth"], settled["joins_checked"])


class RetakeRuleTests(unittest.TestCase):
    """Finding 7 at the detector: same-meaning pairs settle as
    cuts; meaning-differing pairs are flagged, with the reason."""

    def _plan(self, sentences):
        words = _words(sentences)
        pairs = retakes.find_pairs(
            Timeline({"words": words}, make_cuts({"words": words})), None
        )
        return {"pairs": pairs}

    def test_exact_duplicate_is_a_cut_not_a_flag(self):
        plan = self._plan([
            "Welcome back to the channel today friends.",
            "This part is hard to say out loud.",
            "This part is hard to say out loud.",
            "That is everything for today folks.",
        ])
        self.assertEqual(len(plan["pairs"]), 1)
        pair = plan["pairs"][0]
        self.assertNotEqual(pair["band"], "flag")
        self.assertEqual(pair["cut"]["word_start"], 7)
        self.assertEqual(pair["keep"]["word_start"], 15)

    def test_negation_pair_is_flagged(self):
        plan = self._plan([
            "I will not give up on this plan.",
            "I will give up on this plan.",
        ])
        self.assertEqual(len(plan["pairs"]), 1)
        pair = plan["pairs"][0]
        self.assertEqual(pair["band"], "flag")
        self.assertEqual(pair["reason"], retakes._FLAG_NEGATION_REASON)

    def test_numbers_differ_needs_numbers_on_both_sides(self):
        # "One" doing pronoun work is wording, not a fact: the
        # fragment pair in test_v13_retakes ("But the f-" against
        # "But one of the leaders...") must keep its ordinary
        # band. A number named on both sides, with different
        # values, is a changed fact.
        self.assertFalse(retakes._numbers_differ(
            ["but", "the", "f"],
            ["but", "one", "of", "the", "leaders"],
        ))
        self.assertFalse(retakes._numbers_differ(
            ["forty", "degrees"], ["degrees"],
        ))
        self.assertTrue(retakes._numbers_differ(
            ["one", "leader"], ["two", "leaders"],
        ))
        self.assertTrue(retakes._numbers_differ(
            ["forty", "degrees"], ["fifty", "degrees"],
        ))

    def test_numbers_pair_is_flagged_with_its_own_reason(self):
        plan = self._plan([
            "The temperature is forty degrees outside today.",
            "The temperature is fifty degrees outside today.",
        ])
        self.assertEqual(len(plan["pairs"]), 1)
        pair = plan["pairs"][0]
        self.assertEqual(pair["band"], "flag")
        self.assertEqual(pair["reason"], retakes._FLAG_NUMBER_REASON)

    def test_parallel_repeat_is_flagged(self):
        plan = self._plan([
            "Welcome back everyone, today we gather here to listen "
            "and today we gather here to listen once more.",
        ])
        self.assertEqual(len(plan["pairs"]), 1)
        pair = plan["pairs"][0]
        self.assertEqual(pair["band"], "flag")
        self.assertEqual(pair["reason"], retakes._FLAG_PARALLEL_REASON)


class GoldCandidateTests(unittest.TestCase):
    """Finding 7 at the gold store: only meaning-differing pairs
    become your-call candidates; same-meaning pairs settle."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-chunk2-gold-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.job = self.tmp / "job"
        self.job.mkdir()

    def _candidates(self, sentences):
        transcript = make_transcript(sentences)
        pairs = retakes.find_pairs(
            Timeline(transcript, make_cuts(transcript)), None
        )
        # The pass stamps each pair with its outcome before the
        # candidate builder reads them: flagged pairs are the
        # ones it could not settle.
        for pair in pairs:
            pair["outcome"] = (
                "flagged" if pair.get("band") == "flag" else "applied"
            )
        return webapp._gold_candidates_from_retakes(
            pairs, transcript["words"]
        )

    def test_same_meaning_pair_settles_with_no_candidate(self):
        candidates = self._candidates([
            "This part is hard to say out loud.",
            "This part is hard to say out loud.",
        ])
        self.assertEqual(candidates, [])

    def test_negation_pair_waits_as_your_call(self):
        candidates = self._candidates([
            "I will not give up on this plan.",
            "I will give up on this plan.",
        ])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["kind"], "retake")
        self.assertEqual(
            candidates[0]["reason"], webapp._GOLD_REASON_RETAKE_DIFFER
        )

    def test_numbers_pair_waits_as_your_call(self):
        candidates = self._candidates([
            "The temperature is forty degrees outside today.",
            "The temperature is fifty degrees outside today.",
        ])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0]["reason"], webapp._GOLD_REASON_RETAKE_NUMBERS
        )

    def test_parallel_pair_waits_as_your_call(self):
        candidates = self._candidates([
            "Welcome back everyone, today we gather here to listen "
            "and today we gather here to listen once more.",
        ])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0]["reason"], retakes._FLAG_PARALLEL_REASON
        )


class JobServer:
    SENTENCES = [
        "Welcome back to the channel today friends.",
        "This part is hard to say out loud.",
        "This part is hard to say out loud.",
        "I will not give up on this plan.",
        "I will give up on this plan.",
        "That is everything for today folks.",
    ]

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-chunk2-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(
            os.environ, {"HOME": str(self.home)}, clear=False
        )
        self._env.start()
        for name in (
            "MODEL_API_KEY", "ELEVENLABS_API_KEY",
            "BASETEN_API_KEY", "FIREWORKS_API_KEY",
        ):
            os.environ.pop(name, None)
        self.job = self.tmp / "job"
        self.job.mkdir()
        transcript = make_transcript(self.SENTENCES)
        (self.job / "transcript.json").write_text(
            json.dumps(transcript), encoding="utf-8"
        )
        (self.job / "cuts.json").write_text(
            json.dumps(make_cuts(transcript)), encoding="utf-8"
        )
        (self.job / "review.mp4").write_bytes(VIDEO_BYTES)
        self.config = WebAppConfig(self.job)
        self.server = create_server(self.config, host="127.0.0.1", port=0)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, path, data=None):
        payload = None if data is None else json.dumps(data).encode()
        req = urllib.request.Request(
            self.base + path,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                return exc.code, json.loads(raw or "{}")
            except ValueError:
                return exc.code, {"raw": raw}


class OneCountTests(unittest.TestCase):
    """Finding 6b + 7, end to end: one real edit plan, and every
    surface's numbers agree with the single counts source."""

    def test_counts_agree_across_surfaces(self):
        with JobServer() as live:
            status, data = live.call("/api/front-door", {
                "provider": "mock",
                "remove_retakes": True,
                "cut_silence": False,
                "buffer_s": 0.1,
                "format_scope": "both",
                "direction_text": "",
            })
            self.assertEqual(status, 200, data)
            self.assertTrue(data["ok"], data)
            # Seed the smooth record the pass would have written,
            # with one join still failing a measured check.
            session = live.job / "tye-session"
            session.mkdir(exist_ok=True)
            (session / "smooth_pass.json").write_text(json.dumps({
                "ran": True,
                "joins_checked": 40,
                "joins_smooth": 39,
                "edges_set": 5,
                "your_call": [{
                    "join_index": 3,
                    "kind": "click",
                    "output_time_s": 4.2,
                    "text": "a click at the join",
                }],
            }), encoding="utf-8")
            status, job = live.call("/api/job")
            self.assertEqual(status, 200, job)
            counts = job["counts"]
            # Finding 7 in the counts: the exact duplicate was
            # removed; the negation pair waits as your call.
            self.assertEqual(counts["retakes_removed"], 1)
            self.assertEqual(counts["retakes_set_aside"], 1)
            # The word counts are the summary's own numbers.
            self.assertEqual(counts["words_cut"], job["words_cut"])
            self.assertEqual(counts["words_kept"], job["words_kept"])
            self.assertGreaterEqual(counts["words_cut"], 7)
            # Joins come from the smooth record, and the review
            # total is open gold plus failing joins, nothing else.
            self.assertEqual(counts["joins_checked"], 40)
            self.assertEqual(counts["joins_failed"], 1)
            self.assertEqual(
                counts["joins_failed"], len(job["smooth"]["your_call"])
            )
            self.assertEqual(
                counts["spots_to_review"],
                len(job["gold"]) + counts["joins_failed"],
            )
            aside_in_gold = [
                g for g in job["gold"]
                if g.get("kind") == "retake" or g.get("source") == "retakes"
            ]
            self.assertEqual(
                counts["retakes_set_aside"], len(aside_in_gold)
            )
            # The run's own result speaks the one count's numbers.
            self.assertIn(
                "Retakes: 1 removed, 1 set aside",
                "\n".join(data.get("lines") or []),
            )
            # 0.26, fix 3: the thread carries no recap bubble at
            # all, so no thread surface can speak a different
            # number: no "run" entry, and no "Retakes:" line in
            # any entry the thread holds.
            status, thread = live.call("/api/thread")
            runs = [e for e in thread["entries"] if e.get("kind") == "run"]
            self.assertEqual(runs, [])
            recap_texts = []
            for e in thread["entries"]:
                recap_texts.append(e.get("text") or "")
                recap_texts.extend(e.get("lines") or [])
            joined = "\n".join(recap_texts)
            self.assertNotIn("Retakes:", joined)


class StudioCountsSourceTests(unittest.TestCase):
    """The client reads the one count and never claims a join
    may sound abrupt."""

    def test_done_payload_carries_the_counts(self):
        self.assertIn("job.counts", STUDIO)
        self.assertIn("counts: counts", STUDIO)
        self.assertIn("counts.retakes_removed", STUDIO)
        self.assertIn("counts.spots_to_review", STUDIO)

    def test_no_abrupt_claim_anywhere(self):
        self.assertNotIn("may sound abrupt", STUDIO)
        self.assertIn("did not pass the join check", STUDIO)
        self.assertIn("joins_failed", STUDIO)

    def test_transcript_header_reads_the_counts(self):
        self.assertIn("spots to review", STUDIO)
        self.assertIn("E.job.counts", STUDIO)


class QuestionShapeTests(unittest.TestCase):
    def test_question_shapes(self):
        for text in (
            "Why did you cut that?",
            "What did you change?",
            "And what would you do to make it better?",
            "How long is the edit",
            "Did you keep the intro",
            "Can you tell me why that was cut",
            "Is it ready",
            "Tell me how the joins look",
        ):
            self.assertTrue(webapp._is_chat_question(text), text)

    def test_commands_are_not_questions(self):
        for text in (
            "Make the edit",
            "Cut it tighter",
            "Remove the retakes",
            "Do it",
            "Do the edit",
            "Keep both copies",
        ):
            self.assertFalse(webapp._is_chat_question(text), text)


class ChatQuestionGateTests(unittest.TestCase):
    """Finding 8 over HTTP: questions answer from the record
    with the direction pass never invoked; a statement command
    reaches it."""

    def _send(self, live, text):
        return live.call("/api/thread/message", {"text": text})

    def test_why_question_answers_with_no_run(self):
        with JobServer() as live:
            with mock.patch.object(
                webapp, "_run_direction_pass",
                side_effect=AssertionError("direction ran"),
            ) as direction:
                status, data = self._send(live, "Why did you cut that?")
            self.assertEqual(status, 200, data)
            self.assertEqual(data["kind"], "record")
            self.assertEqual(direction.call_count, 0)
            self.assertTrue(data["entry"].get("record_answer"))

    def test_what_changed_answers_with_no_run(self):
        with JobServer() as live:
            with mock.patch.object(
                webapp, "_run_direction_pass",
                side_effect=AssertionError("direction ran"),
            ) as direction:
                status, data = self._send(live, "What did you change?")
            self.assertEqual(status, 200, data)
            self.assertEqual(data["kind"], "record")
            self.assertEqual(direction.call_count, 0)

    def test_general_question_answers_with_no_run(self):
        with JobServer() as live:
            with mock.patch.object(
                webapp, "_run_direction_pass",
                side_effect=AssertionError("direction ran"),
            ) as direction:
                status, data = self._send(live, "When did you finish it?")
            self.assertEqual(status, 200, data)
            self.assertEqual(data["kind"], "record")
            self.assertEqual(direction.call_count, 0)
            self.assertIn("From the record", data["entry"]["text"])

    def test_cut_it_tighter_is_a_command(self):
        with JobServer() as live:
            with mock.patch.object(
                webapp, "_run_direction_pass",
                return_value={"lines": ["Tightened the cuts."], "usage": []},
            ) as direction:
                status, data = self._send(live, "Cut it tighter")
            self.assertEqual(status, 200, data)
            self.assertEqual(data["kind"], "direction")
            self.assertEqual(direction.call_count, 1)


class StagedMakeTheEditTests(unittest.TestCase):
    """Finding 8, client half: a question beside a staged video
    routes to the thread send, and typing the command with the
    video runs the edit once the video is in."""

    def test_question_gate_sits_before_the_staged_send(self):
        gate = STUDIO.index("chatLooksLikeQuestion(earlyText)")
        staged = STUDIO.index("window.__tyeStagedSend()")
        self.assertLess(gate, staged)

    def test_typed_command_runs_the_edit_after_intake(self):
        self.assertRegex(
            STUDIO,
            re.compile(
                r"\(make\|run\|do\)\\s\+\(the\\s\+\)\?edit"
            ),
        )
        self.assertIn(
            'runChatEdit(reply, intakeId, totalDuration, ""', STUDIO
        )


if __name__ == "__main__":
    unittest.main()
