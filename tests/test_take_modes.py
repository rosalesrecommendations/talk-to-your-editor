"""Tests for the v0.5.1 keeper modes and Best delivery (Beta) shadow.

Three modes for "When a line is said more than once, keep:": the
last take (the default, unchanged), the first clean take (the first
complete delivery stays), and Best delivery (Beta), which runs in
shadow: the positional rule still applies, and Beta only records
the keeper it would have chosen on pairs where there is a genuine
choice. The hard gate is tested directly: word-identical twins are
always declared ties, so Beta can never invent a difference.
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
from talk_to_your_editor.meta_provider import MetaProvider, MockProvider
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig, create_server

VIDEO_BYTES = bytes(range(256)) * 32


def make_transcript(sentences, gap_after=None):
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


def settings_for(mode):
    return retakes.settings_from_profile({"retakes": {"keep": mode}})


FILLER_A = "We are talking about the morning routine again."
FILLER_B = "It shapes the whole day more than people think."
DUPE = "This part is hard to say out loud."
FULLER = "The morning light makes everything feel possible and bright and calm today."
LEANER = "The morning light makes everything feel possible and bright today."
RESTARTED = "The morning light, the morning light makes everything feel possible today."
CLEAN = "The morning light makes everything feel possible today."


class ModeSettingsTests(unittest.TestCase):
    def test_profile_accepts_best_and_ignores_unknown_modes(self):
        self.assertEqual(settings_for("best")["keep"], "best")
        self.assertEqual(settings_for("first")["keep"], "first")
        profile = {"retakes": {"keep": "whichever"}}
        self.assertEqual(
            retakes.settings_from_profile(profile)["keep"], "last"
        )

    def test_first_clean_take_keeps_the_first_delivery(self):
        tl = timeline_for([FILLER_A, DUPE, DUPE, FILLER_B])
        pairs = retakes.find_pairs(tl, None, settings_for("first"))
        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        # The keeper is the earlier delivery; the cut is the later.
        self.assertLess(pair["keep"]["word_start"], pair["cut"]["word_start"])
        self.assertEqual(
            pair["reason"],
            "Kept the first clean take; the later tries come out.",
        )
        self.assertTrue(pair["cut_complete"])
        self.assertTrue(pair["keep_complete"])

    def test_first_clean_fragment_reason(self):
        whole = "The drive home takes about an hour in traffic."
        tl = timeline_for(
            [FILLER_A, whole, "The drive home takes about", FILLER_B],
            gap_after={2: 2.0},
        )
        pairs = retakes.find_pairs(tl, None, settings_for("first"))
        self.assertEqual(len(pairs), 1)
        self.assertEqual(
            pairs[0]["reason"],
            "This try comes after the line was already said in full, "
            "and it stops early. The first clean take stays.",
        )

    def test_best_mode_applies_the_positional_last_take(self):
        tl = timeline_for([FILLER_A, DUPE, DUPE, FILLER_B])
        last_pairs = retakes.find_pairs(tl, None, settings_for("last"))
        best_pairs = retakes.find_pairs(tl, None, settings_for("best"))
        self.assertEqual(
            [(p["cut"]["word_start"], p["keep"]["word_start"]) for p in last_pairs],
            [(p["cut"]["word_start"], p["keep"]["word_start"]) for p in best_pairs],
        )


class ShadowRankTests(unittest.TestCase):
    def test_twin_tie_is_a_hard_gate(self):
        # Word-identical twins are ties by definition, even when
        # the second was delivered after a long pause: no text
        # evidence may separate them.
        tl = timeline_for([FILLER_A, DUPE, DUPE, FILLER_B])
        pair = retakes.find_pairs(tl, None)[0]
        self.assertTrue(retakes.deliveries_identical(pair, tl))
        answer = retakes.shadow_rank(pair, tl)
        self.assertEqual(answer["pick"], "tie")
        self.assertEqual(answer["reason"], retakes.TIE_REASON)

    def test_rank_prefers_the_delivery_without_a_restart(self):
        tl = timeline_for([FILLER_A, CLEAN, RESTARTED, FILLER_B])
        pairs = [
            pair
            for pair in retakes.find_pairs(tl, None)
            if retakes.genuine_choice(pair)
        ]
        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        answer = retakes.shadow_rank(pair, tl)
        # The later delivery restarts inside the line, so Beta
        # would have kept the earlier one: a flip.
        self.assertEqual(answer["pick"], "other")

    def test_rank_agrees_when_the_later_take_is_the_clean_one(self):
        tl = timeline_for([FILLER_A, RESTARTED, CLEAN, FILLER_B])
        pairs = [
            pair
            for pair in retakes.find_pairs(tl, None)
            if retakes.genuine_choice(pair)
        ]
        self.assertTrue(pairs)
        pair = pairs[0]
        answer = retakes.shadow_rank(pair, tl)
        self.assertEqual(answer["pick"], "positional")

    def test_rank_prefers_the_delivery_that_says_the_whole_line(self):
        tl = timeline_for([FILLER_A, FULLER, LEANER, FILLER_B])
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        self.assertEqual(pair["band"], "a")
        answer = retakes.shadow_rank(pair, tl)
        self.assertEqual(answer["pick"], "other")
        self.assertIn("says the whole line", answer["reason"])


class _StubShadowJudge(MetaProvider):
    """A MetaProvider whose chat returns canned shadow picks."""

    def __init__(self, payload):
        super().__init__("test-key", allow_wonder=False)
        self._payload = payload
        self.requests = []

    def _chat(self, model, messages):
        self.requests.append(messages)
        return json.dumps(self._payload)


class ShadowJudgeTests(unittest.TestCase):
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
            "cut_complete": True,
            "keep_complete": True,
            "pattern_key": "retake:earlier_take:band_b",
            "reason": "borderline",
        }

    def test_judge_maps_picks_against_the_positional_keeper(self):
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
        pair_flip = self._pair(20, 26)
        pair_agree = self._pair(40, 46)
        payload = {
            "picks": [
                {"pair_id": pair_flip["pair_id"], "pick": "keep_first",
                 "reason": "The first try says it in one clean run."},
                {"pair_id": pair_agree["pair_id"], "pick": "keep_second",
                 "reason": "The second finishes the thought."},
                {"pair_id": "retake-999-1000-1001-1002", "pick": "tie",
                 "reason": "Not a real pair."},
            ]
        }
        provider = _StubShadowJudge(payload)
        picks = provider.judge_shadow([pair_flip, pair_agree], tl)
        self.assertIsNotNone(picks)
        # The positional keeper is the later span in these fixtures:
        # keep_first flips it, keep_second agrees with it.
        self.assertEqual(picks[pair_flip["pair_id"]]["pick"], "other")
        self.assertEqual(picks[pair_agree["pair_id"]]["pick"], "positional")
        self.assertEqual(len(provider.requests), 1)

    def test_mock_shadow_judges_nothing(self):
        self.assertIsNone(MockProvider().judge_shadow([], None))

    def test_unparseable_shadow_answer_is_none(self):
        provider = _StubShadowJudge({"nope": True})
        tl = timeline_for([FILLER_A, DUPE, DUPE, FILLER_B])
        self.assertIsNone(provider.judge_shadow([self._pair(9, 17)], tl))


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
DUPE_FIRST = sum(len(s.split()) for s in HTTP_SENTENCES[:5])   # 42
DUPE_WIDTH = len(DUPE.split())                                  # 8
TOTAL_WORDS = sum(len(s.split()) for s in HTTP_SENTENCES)       # 94


class KeeperModeHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-modes-"))
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

    def fire(self, keep_mode=None):
        body = {
            "provider": "mock",
            "remove_retakes": True,
            "cut_silence": False,
            "buffer_s": 0.1,
            "format_scope": "both",
            "direction_text": "",
        }
        if keep_mode is not None:
            body["keep_mode"] = keep_mode
        status, data = self.call("/api/front-door", body)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"], data)
        return data

    def session_record(self):
        path = self.job / "tye-session" / "retake_pass.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_default_mode_is_last_with_no_recap(self):
        data = self.fire()
        self.assertEqual(data["settings"]["keep_mode"], "last")
        self.assertIsNone(data["recap"])
        record = self.session_record()
        self.assertEqual(record["pairs"][0]["keep_mode"], "last")
        self.assertEqual(record["shadow"], [])

    def test_first_mode_keeps_the_first_delivery(self):
        data = self.fire(keep_mode="first")
        self.assertEqual(
            data["recap"],
            "Remove retakes is on. Keeping: the first clean take.",
        )
        status, job = self.call("/api/job")
        self.assertEqual(job["words_kept"], TOTAL_WORDS - DUPE_WIDTH)
        self.assertEqual(job["front_door"]["keep_mode"], "first")
        removed = {s["word_start"] for s in job["edit_spans"]}
        self.assertIn(DUPE_FIRST + DUPE_WIDTH, removed)
        self.assertNotIn(DUPE_FIRST, removed)
        record = self.session_record()
        pair = record["pairs"][0]
        self.assertEqual(pair["keep"]["word_start"], DUPE_FIRST)
        self.assertEqual(
            pair["reason"],
            "Kept the first clean take; the later tries come out.",
        )

    def test_best_mode_twin_is_a_tie_and_shadow_applies_nothing(self):
        data = self.fire(keep_mode="best")
        self.assertEqual(
            data["recap"],
            "Remove retakes is on. Keeping: best delivery (Beta).",
        )
        # The positional rule did exactly what the last-take mode does.
        self.assertEqual(data["retakes"]["applied"], 1)
        self.assertEqual(data["retakes"]["shadow"]["ties"], 1)
        self.assertEqual(data["retakes"]["shadow"]["flips"], 0)
        status, job = self.call("/api/job")
        self.assertEqual(job["words_kept"], TOTAL_WORDS - DUPE_WIDTH)
        record = self.session_record()
        self.assertEqual(len(record["shadow"]), 1)
        entry = record["shadow"][0]
        self.assertEqual(entry["status"], "scored")
        self.assertEqual(entry["beta_pick"], "tie")
        self.assertFalse(entry["flip"])
        self.assertIsNone(entry["matched"])
        # The dot carries the shadow line as information.
        span = [
            s for s in job["edit_spans"] if s["word_start"] == DUPE_FIRST
        ][0]
        self.assertEqual(span["retake"]["shadow"]["pick"], "tie")

    def test_mode_default_comes_from_the_profile(self):
        profile = {"retakes": {"keep": "first"}}
        (self.job / "profile.json").write_text(
            json.dumps(profile), encoding="utf-8"
        )
        status, job = self.call("/api/job")
        self.assertEqual(job["front_door"]["keep_mode"], "first")

    def test_bad_mode_is_a_plain_error(self):
        status, data = self.call(
            "/api/front-door",
            {
                "provider": "mock",
                "remove_retakes": True,
                "cut_silence": False,
                "buffer_s": 0.1,
                "keep_mode": "whichever",
                "format_scope": "both",
                "direction_text": "",
            },
        )
        self.assertEqual(status, 400, data)
        self.assertIn("first clean take", data["error"])


FLIP_SENTENCES = [
    "Welcome back to the channel today my friends.",
    "We are talking about the morning routine again.",
    FULLER,
    LEANER,
    "After that we sit down with coffee and the notebook.",
    "Thanks for watching and for being here today.",
]
FULLER_FIRST = sum(len(s.split()) for s in FLIP_SENTENCES[:2])  # 15
FULLER_WIDTH = len(FULLER.split())                              # 13


class BetaFlipHttpTests(unittest.TestCase):
    """A Band A pair where Beta's pick disagrees with position."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-beta-"))
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
        transcript = make_transcript(FLIP_SENTENCES)
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

    def fire_best(self):
        status, data = self.call(
            "/api/front-door",
            {
                "provider": "mock",
                "remove_retakes": True,
                "cut_silence": False,
                "buffer_s": 0.1,
                "keep_mode": "best",
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

    def test_flip_recorded_and_positional_still_applies(self):
        data = self.fire_best()
        self.assertEqual(data["retakes"]["shadow"]["flips"], 1)
        record = self.session_record()
        entry = record["shadow"][0]
        self.assertEqual(entry["beta_pick"], "other")
        self.assertTrue(entry["flip"])
        self.assertIsNone(entry["matched"])
        # Position decided the edit: the fuller earlier take came
        # out and the leaner later one stayed.
        status, job = self.call("/api/job")
        removed = {s["word_start"] for s in job["edit_spans"]}
        self.assertIn(FULLER_FIRST, removed)
        self.assertIn("Best delivery", job["beta"]["line"] or "")
        self.assertEqual(job["beta"]["flips"], 1)
        self.assertEqual(job["beta"]["settled"], 0)

    def test_swap_settles_the_flip_as_a_match(self):
        self.fire_best()
        edit_id = f"edit-{FULLER_FIRST}-{FULLER_FIRST + FULLER_WIDTH}"
        status, result = self.call(
            "/api/edit-action", {"edit_id": edit_id, "action": "swap"}
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["changed"], result)
        record = self.session_record()
        entry = record["shadow"][0]
        self.assertEqual(entry["matched"], True)
        self.assertEqual(entry["match_source"], "swap")
        status, job = self.call("/api/job")
        self.assertEqual(
            job["beta"]["line"],
            "Best delivery has matched your pick in 1 of its last 1 calls.",
        )

    def test_worked_grade_settles_the_flip_as_no_match(self):
        self.fire_best()
        record = self.session_record()
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
            "rule_request": False,
        }
        status, data = self.call("/api/grades", {"entries": [entry]})
        self.assertEqual(status, 200, data)
        record = self.session_record()
        shadow = record["shadow"][0]
        self.assertEqual(shadow["matched"], False)
        self.assertEqual(shadow["match_source"], "grade")
        grades = json.loads(
            (self.job / "tye-session" / "grades.json").read_text(
                encoding="utf-8"
            )
        )
        stored = grades["entries"] if isinstance(grades, dict) else grades
        self.assertTrue(stored[0]["pattern_key"].endswith(":best"))
        status, job = self.call("/api/job")
        self.assertEqual(
            job["beta"]["line"],
            "Best delivery has matched your pick in 0 of its last 1 calls.",
        )


if __name__ == "__main__":
    unittest.main()
