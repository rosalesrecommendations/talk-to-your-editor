"""Tests for the v0.5.0 retake engine (stdlib unittest, no dependencies).

Three layers, mirroring the engine itself: detector unit tests over
synthetic transcripts built in code (bands, policy, the flow layer's
completeness rule), a judgment test proving the model only picks
verdicts and never draws spans, and HTTP tests driving the real
server through the front door with no comments pinned: the pair is
found and cut locally, the transcript dot carries both takes, swap
and bring back round-trip, grades pick up the per-band pattern key,
and a "did not work" grade retires the pair.
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

from talk_to_your_editor import retakes
from talk_to_your_editor.meta_provider import MetaProvider
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig, create_server

VIDEO_BYTES = bytes(range(256)) * 32


def make_transcript(sentences, gap_after=None):
    """A transcript dict from sentence strings, 0.45s per word.

    ``gap_after`` maps a sentence index to extra silent seconds
    after it, for the cases where a speaker trails off and pauses
    (which is what ends a fragment unit in a real transcript).
    """
    words = []
    t = 0.0
    for index, sentence in enumerate(sentences):
        for token in sentence.split():
            words.append({"word": token, "start": round(t, 3),
                          "end": round(t + 0.35, 3)})
            t += 0.45
        if gap_after and index in gap_after:
            t += gap_after[index]
    return {"words": words, "duration": round(t, 3)}


def make_cuts(transcript, cut_spans=()):
    """One kept range over everything, minus any settled word spans."""
    total = float(transcript["words"][-1]["end"]) + 0.5
    ranges = []
    cursor = 0.0
    for start_i, end_i in sorted(cut_spans):
        start_t = float(transcript["words"][start_i]["start"])
        end_t = float(transcript["words"][end_i - 1]["end"])
        if start_t > cursor:
            ranges.append({"start": cursor, "end": start_t})
        cursor = end_t
    if cursor < total:
        ranges.append({"start": cursor, "end": total})
    return {"kept_ranges": ranges, "source_duration_s": total}


def timeline_for(sentences, cut_spans=(), gap_after=None):
    transcript = make_transcript(sentences, gap_after=gap_after)
    return Timeline(transcript, make_cuts(transcript, cut_spans))


FILLER_A = "We are talking about the morning routine again."
FILLER_B = "It shapes the whole day more than people think."
FILLER_C = "First we get up before the sun rises."
DUPE = "This part is hard to say out loud."


class DetectorTests(unittest.TestCase):
    def test_coverage_is_containment_of_the_shorter(self):
        # Containment is measured over the shorter unit: a short
        # delivery wholly inside a longer one scores a full match,
        # however long the longer one runs.
        self.assertEqual(retakes._coverage(["a", "b", "c"], ["a", "b"]), 1.0)
        self.assertEqual(retakes._coverage(["a", "b"], ["a", "b", "c"]), 1.0)
        self.assertAlmostEqual(
            retakes._coverage(["a", "b", "c"], ["a", "z"]), 0.5
        )
        self.assertEqual(retakes._coverage(["x"], ["y"]), 0.0)

    def test_anchor_len_counts_the_shared_head(self):
        self.assertEqual(
            retakes._anchor_len(["a", "b", "c"], ["a", "b", "z"]), 2
        )
        self.assertEqual(retakes._anchor_len(["a"], ["b"]), 0)

    def test_exact_duplicate_is_band_a_and_cuts_the_earlier(self):
        tl = timeline_for([FILLER_A, DUPE, DUPE, FILLER_B])
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        self.assertEqual(pair["band"], "a")
        self.assertEqual(pair["retake_class"], "earlier_take")
        self.assertEqual(pair["cut"]["text"], DUPE)
        self.assertEqual(pair["keep"]["text"], DUPE)
        self.assertLess(pair["cut"]["word_start"], pair["keep"]["word_start"])
        self.assertTrue(pair["cut_complete"])
        self.assertTrue(pair["keep_complete"])
        self.assertEqual(pair["pattern_key"], "retake:earlier_take:band_a")

    def test_prefix_fragment_is_band_a_false_start(self):
        tl = timeline_for(
            [
                FILLER_A,
                "I was going to the",
                "I was going to the store to buy milk for dinner.",
                FILLER_B,
            ]
        )
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        self.assertEqual(pair["band"], "a")
        self.assertEqual(pair["cut"]["text"], "I was going to the")
        self.assertFalse(pair["cut_complete"])
        self.assertTrue(pair["keep_complete"])

    def test_chain_of_three_keeps_the_last_complete(self):
        tl = timeline_for([FILLER_A, DUPE, DUPE, DUPE, FILLER_B])
        pairs = retakes.find_pairs(tl, None)
        self.assertTrue(pairs)
        keep_starts = {p["keep"]["word_start"] for p in pairs}
        self.assertEqual(len(keep_starts), 1)
        cut_starts = sorted(p["cut"]["word_start"] for p in pairs)
        self.assertEqual(len(cut_starts), 2)
        self.assertLess(max(cut_starts), keep_starts.pop())

    def test_later_fragment_loses_to_earlier_complete(self):
        # The flow rule: a take that completes the sentence cleanly
        # beats a later broken restart of the same line. The broken
        # restart trails off into a pause, which is what ends its
        # unit in a real transcript.
        whole = "The drive home takes about an hour in traffic."
        tl = timeline_for(
            [FILLER_A, whole, "The drive home takes about", FILLER_B],
            gap_after={2: 2.0},
        )
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        self.assertEqual(pair["cut"]["text"], "The drive home takes about")
        self.assertEqual(pair["keep"]["text"], whole)
        self.assertFalse(pair["cut_complete"])
        self.assertTrue(pair["keep_complete"])

    def test_protected_delivery_is_never_a_candidate(self):
        sentences = [FILLER_A, DUPE, DUPE, FILLER_B]
        transcript = make_transcript(sentences)
        tl = Timeline(transcript, make_cuts(transcript))
        first = len(FILLER_A.split())
        width = len(DUPE.split())
        profile = {
            "protected_spans": [
                {"word_start": first, "word_end": first + width,
                 "label": "the first telling is protected"}
            ]
        }
        pairs = retakes.find_pairs(tl, profile)
        for pair in pairs:
            self.assertGreaterEqual(
                pair["cut"]["word_start"], first + width
            )
            self.assertFalse(
                pair["cut"]["word_start"] < first + width
                and pair["cut"]["word_end"] > first
            )

    def test_already_cut_delivery_is_settled(self):
        sentences = [FILLER_A, DUPE, DUPE, FILLER_B]
        first = len(FILLER_A.split())
        width = len(DUPE.split())
        tl = timeline_for(sentences, cut_spans=[(first, first + width)])
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(pairs, [])

    def test_negation_pair_is_flag_only(self):
        line = "I will not give up on this family."
        tl = timeline_for([FILLER_A, line, line, FILLER_B])
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["band"], "flag")
        self.assertEqual(retakes.band_a_operations(tl, None, pairs=pairs), [])

    def test_short_double_is_flag_only(self):
        tl = timeline_for([FILLER_A, "Thank you.", "Thank you.", FILLER_B])
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["band"], "flag")

    def test_distant_repeat_is_a_callback_not_band_a(self):
        tl = timeline_for([DUPE, FILLER_A, FILLER_B, DUPE, FILLER_C])
        for pair in retakes.find_pairs(tl, None):
            self.assertNotEqual(pair["band"], "a")

    def test_band_a_operation_shape(self):
        tl = timeline_for([FILLER_A, DUPE, DUPE, FILLER_B])
        pairs = retakes.find_pairs(tl, None)
        ops = retakes.band_a_operations(tl, None, pairs=pairs)
        self.assertEqual(len(ops), 1)
        op = ops[0]
        self.assertEqual(op["type"], "cut_words")
        self.assertEqual(op["comment_id"], pairs[0]["pair_id"])
        self.assertEqual(op["model"], "mock")
        self.assertEqual(op["word_start"], pairs[0]["cut"]["word_start"])
        self.assertEqual(op["word_end"], pairs[0]["cut"]["word_end"])


class _StubJudge(MetaProvider):
    """A MetaProvider whose chat returns canned verdicts."""

    def __init__(self, payload):
        super().__init__("test-key", allow_wonder=False)
        self._payload = payload

    def _chat(self, model, messages):
        return json.dumps(self._payload)


class JudgeTests(unittest.TestCase):
    def _pair(self, cut_start, keep_start, width=6):
        return {
            "pair_id": f"retake-{cut_start}-{cut_start + width}"
                       f"-{keep_start}-{keep_start + width}",
            "cut": {"word_start": cut_start, "word_end": cut_start + width,
                    "text": "first try at the line here"},
            "keep": {"word_start": keep_start, "word_end": keep_start + width,
                     "text": "the line said in full here"},
            "band": "b",
            "retake_class": "earlier_take",
            "coverage": 0.75,
            "ratio": 0.7,
            "shape": "unit_pair",
            "cut_complete": False,
            "keep_complete": True,
            "pattern_key": "retake:earlier_take:band_b",
            "reason": "borderline",
        }

    def test_model_picks_verdicts_and_never_draws_spans(self):
        sentences = [
            "One two three four five six seven eight nine ten.",
            "Eleven twelve thirteen fourteen fifteen sixteen.",
            "Seventeen eighteen nineteen twenty twentyone twentytwo.",
            "Alpha bravo charlie delta echo foxtrot golf hotel.",
            "India juliet kilo lima mike november oscar papa.",
            "Quebec romeo sierra tango uniform victor whiskey.",
            "Xray yankee zulu apple banana cherry dragon eagle.",
        ]
        tl = timeline_for(sentences)
        pair_cut = self._pair(20, 26)
        pair_keep = self._pair(40, 46)
        payload = {
            "verdicts": [
                {"pair_id": pair_cut["pair_id"], "verdict": "cut_earlier",
                 "confidence": 0.9, "reason": "The second try finishes it.",
                 "word_start": 0, "word_end": 3},
                {"pair_id": pair_keep["pair_id"], "verdict": "keep_both",
                 "confidence": 0.8, "reason": "A refrain, said on purpose."},
                {"pair_id": "retake-999-1000-1001-1002",
                 "verdict": "cut_earlier", "confidence": 0.9,
                 "reason": "Not a real pair."},
            ]
        }
        provider = _StubJudge(payload)
        result = provider.judge_retakes([pair_cut, pair_keep], tl)
        self.assertIsNotNone(result)
        operations, verdicts = result
        self.assertEqual(verdicts[pair_cut["pair_id"]], "cut_earlier")
        self.assertEqual(verdicts[pair_keep["pair_id"]], "keep_both")
        self.assertEqual(len(operations), 1)
        op = operations[0]
        self.assertEqual(op["comment_id"], pair_cut["pair_id"])
        self.assertEqual(op["word_start"], 20)
        self.assertEqual(op["word_end"], 26)

    def test_mock_judges_nothing(self):
        from talk_to_your_editor.meta_provider import MockProvider

        self.assertIsNone(MockProvider().judge_retakes([], None))


HTTP_SENTENCES = [
    "Welcome back to the channel today my friends.",
    "We are talking about the morning routine again.",
    "It shapes the whole day more than people think.",
    "First we get up before the sun rises.",
    "Then we make the bed and open the blinds.",
    DUPE,
    DUPE,
    "After that we sit down with coffee and the notebook.",
    "We write down the one thing that matters most.",
    "And we pray over it before anything else starts.",
    "Thanks for watching and for being here today.",
]
# Word offsets of the duplicate pair inside HTTP_SENTENCES.
DUPE_FIRST = sum(len(s.split()) for s in HTTP_SENTENCES[:5])   # 42
DUPE_WIDTH = len(DUPE.split())                                  # 8
TOTAL_WORDS = sum(len(s.split()) for s in HTTP_SENTENCES)       # 94


class RetakeHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-retakes-"))
        # v0.7.0: front-door saves also write the creator's usual;
        # isolate HOME so tests never write the real one.
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = mock.patch.dict(
            os.environ, {"HOME": str(self.home)}, clear=False
        )
        env.start()
        self.addCleanup(env.stop)
        self.job = self.tmp / "job"
        self.job.mkdir()
        transcript = make_transcript(HTTP_SENTENCES)
        (self.job / "transcript.json").write_text(
            json.dumps(transcript), encoding="utf-8"
        )
        (self.job / "cuts.json").write_text(
            json.dumps(make_cuts(transcript)), encoding="utf-8"
        )
        (self.job / "review.mp4").write_bytes(VIDEO_BYTES)
        self.server = create_server(
            WebAppConfig(self.job), host="127.0.0.1", port=0
        )
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
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
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode()
                try:
                    return resp.status, json.loads(body)
                except ValueError:
                    return resp.status, body
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            try:
                return exc.code, json.loads(body)
            except ValueError:
                return exc.code, body

    def fire_front_door(self):
        status, data = self.call(
            "/api/front-door",
            {
                "provider": "mock",
                "remove_retakes": True,
                "cut_silence": False,
                "buffer_s": 0.1,
                "format_scope": "both",
                "direction_text": "",
            },
        )
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"], data)
        return data

    def session_record(self):
        path = self.job / "tye-session" / "retake_pass.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_front_door_finds_retake_with_no_comments_pinned(self):
        data = self.fire_front_door()
        retakes_summary = data["retakes"]
        self.assertTrue(retakes_summary["ran"])
        self.assertEqual(retakes_summary["band_a"], 1)
        self.assertEqual(retakes_summary["applied"], 1)
        status, job = self.call("/api/job")
        self.assertEqual(status, 200, job)
        self.assertEqual(job["words_kept"], TOTAL_WORDS - DUPE_WIDTH)
        spans = [
            s for s in job["edit_spans"]
            if s["word_start"] == DUPE_FIRST
        ]
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertIsNotNone(span["retake"])
        self.assertEqual(span["retake"]["keep_text"], DUPE)
        self.assertEqual(span["removed_text"], DUPE)

    def test_swap_takes_then_bring_back(self):
        self.fire_front_door()
        edit_id = f"edit-{DUPE_FIRST}-{DUPE_FIRST + DUPE_WIDTH}"
        status, result = self.call(
            "/api/edit-action", {"edit_id": edit_id, "action": "swap"}
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["changed"], result)
        status, job = self.call("/api/job")
        self.assertEqual(job["words_kept"], TOTAL_WORDS - DUPE_WIDTH)
        removed_starts = {s["word_start"] for s in job["edit_spans"]}
        self.assertIn(DUPE_FIRST + DUPE_WIDTH, removed_starts)
        self.assertNotIn(DUPE_FIRST, removed_starts)
        kept_edit = f"edit-{DUPE_FIRST + DUPE_WIDTH}-{DUPE_FIRST + 2 * DUPE_WIDTH}"
        status, result = self.call(
            "/api/edit-action", {"edit_id": kept_edit, "action": "restore"}
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["changed"], result)
        status, job = self.call("/api/job")
        self.assertEqual(job["words_kept"], TOTAL_WORDS)

    def test_grades_pick_up_pattern_key_and_decision_retires_pair(self):
        self.fire_front_door()
        record = self.session_record()
        self.assertEqual(len(record["pairs"]), 1)
        pair = record["pairs"][0]
        entry = {
            "comment_id": pair["pair_id"],
            "ts": 20.0,
            "original_text": "Retake found by the front door.",
            "status": "applied",
            "action_taken": "Removed the words.",
            "operation_type": "cut_words",
            "verdict": "worked",
            "notes": "",
            "rule_request": True,
        }
        status, data = self.call("/api/grades", {"entries": [entry]})
        self.assertEqual(status, 200, data)
        keys = [s["pattern_key"] for s in data["suggestions"]]
        # v0.5.1: graded records carry the keeper mode in the key.
        self.assertIn("retake:earlier_take:band_a:last", keys)
        grades = json.loads(
            (self.job / "tye-session" / "grades.json").read_text(
                encoding="utf-8"
            )
        )
        stored = grades["entries"] if isinstance(grades, dict) else grades
        self.assertEqual(
            stored[0]["pattern_key"], "retake:earlier_take:band_a:last"
        )
        # A "did not work" grade retires the pair: restore the words,
        # fire again, and the pair is not proposed a second time.
        entry["verdict"] = "did_not_work"
        entry["rule_request"] = False
        status, data = self.call("/api/grades", {"entries": [entry]})
        self.assertEqual(status, 200, data)
        record = self.session_record()
        self.assertTrue(record["decisions"])
        edit_id = f"edit-{DUPE_FIRST}-{DUPE_FIRST + DUPE_WIDTH}"
        status, result = self.call(
            "/api/edit-action", {"edit_id": edit_id, "action": "restore"}
        )
        self.assertTrue(result["changed"], result)
        data = self.fire_front_door()
        self.assertEqual(data["retakes"]["pairs"], 0)

    def test_after_review_page_has_the_retake_row(self):
        self.fire_front_door()
        status, page = self.call("/after-review")
        self.assertEqual(status, 200)
        self.assertIn("Retake found by the front door", page)
        self.assertIn(DUPE, page)


if __name__ == "__main__":
    unittest.main()
