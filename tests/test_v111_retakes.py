"""Tests for v0.11.1 fix item 5: strict retakes by default, unsure flagged.

Strict is the default at the first edit and in the Remove retakes
action alike (both run the same pass through the front door), with
no settings change needed. A suspected retake is cut, keeping the
take the keeper rule names. A cut the pass was not sure about (a
near-duplicate that might carry something new, or a keeper choice
that was close) is marked unsure in the stored cut data, shown as
an unsure cut in the review, and carries the plain note in the
word popup data and the recap. Sure cuts (exact duplicates,
clearly superseded tries) carry no flag. An explicit choice that
says otherwise (the profile's retakes knob, or a per-video
direction that says to only cut the sure ones) still wins over
the default. Offline only: the mock provider judges nothing.
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

from talk_to_your_editor import direction_parse, retakes
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig, create_server

try:
    from test_retakes import make_cuts, make_transcript, timeline_for
except ImportError:  # discovery rooted at the repo instead of tests/
    from tests.test_retakes import make_cuts, make_transcript, timeline_for

VIDEO_BYTES = bytes(range(256)) * 32

NOTE_LAST = (
    "I was not sure about this one. I took it out so the last "
    "take stays. Double check here if you want it back."
)
NOTE_FIRST = (
    "I was not sure about this one. I took it out so the first "
    "clean take stays. Double check here if you want it back."
)

FILLER_A = "We are talking about the morning routine again."
FILLER_B = "It shapes the whole day more than people think."
DUPE = "This part is hard to say out loud."
# A near-duplicate pair: one word differs, so coverage sits under
# the trusted band and the pair is borderline by measurement.
NEAR_A = "The morning light comes through the kitchen window today."
NEAR_B = "The morning light comes through the kitchen window softly."


class StrictDefaultTests(unittest.TestCase):
    def test_strict_is_the_detector_default(self):
        self.assertTrue(retakes.DEFAULT_SETTINGS["strict"])
        self.assertTrue(retakes.settings_from_profile(None)["strict"])
        self.assertTrue(retakes.settings_from_profile({})["strict"])

    def test_profile_knob_is_an_explicit_choice_that_wins(self):
        profile = {"retakes": {"strict": False}}
        self.assertFalse(retakes.settings_from_profile(profile)["strict"])
        profile = {"retakes": {"strict": True}}
        self.assertTrue(retakes.settings_from_profile(profile)["strict"])
        # A non-boolean knob is not a choice anyone made.
        profile = {"retakes": {"strict": "no"}}
        self.assertTrue(retakes.settings_from_profile(profile)["strict"])

    def test_direction_can_say_otherwise_for_one_video(self):
        parsed = direction_parse.parse_direction(
            "Only cut the retakes you are sure about."
        )
        self.assertEqual(parsed["overrides"].get("retake_strict"), False)
        parsed = direction_parse.parse_direction("Be strict with retakes.")
        self.assertEqual(parsed["overrides"].get("retake_strict"), True)
        parsed = direction_parse.parse_direction("Remove retakes.")
        self.assertNotIn("retake_strict", parsed["overrides"])

    def test_unsure_note_names_the_keeper_as_it_is(self):
        self.assertEqual(retakes.unsure_note({"keep": "last"}), NOTE_LAST)
        self.assertEqual(retakes.unsure_note({"keep": "first"}), NOTE_FIRST)
        # Best delivery applies the last take while it is in shadow.
        self.assertEqual(retakes.unsure_note({"keep": "best"}), NOTE_LAST)


class UnsureMarkTests(unittest.TestCase):
    def test_exact_duplicate_cut_carries_no_unsure_flag(self):
        tl = timeline_for([FILLER_A, DUPE, DUPE, FILLER_B])
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["band"], "a")
        self.assertFalse(pairs[0]["unsure"])
        self.assertIsNone(pairs[0]["unsure_kind"])

    def test_broken_try_cut_carries_no_unsure_flag(self):
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
        self.assertEqual(pairs[0]["band"], "a")
        self.assertFalse(pairs[0]["unsure"])

    def test_near_duplicate_is_borderline_and_unsure(self):
        tl = timeline_for([FILLER_A, NEAR_A, NEAR_B, FILLER_B])
        pairs = retakes.find_pairs(tl, None)
        self.assertEqual(len(pairs), 1)
        pair = pairs[0]
        self.assertEqual(pair["band"], "b")
        self.assertTrue(pair["unsure"])
        self.assertEqual(pair["unsure_kind"], "near_duplicate")
        self.assertEqual(pair["keep"]["text"], NEAR_B)

    def test_strict_operations_cut_the_borderline_pair(self):
        tl = timeline_for([FILLER_A, NEAR_A, NEAR_B, FILLER_B])
        pairs = retakes.find_pairs(tl, None)
        ops = retakes.strict_cut_operations(tl, None, pairs=pairs)
        self.assertEqual(len(ops), 1)
        op = ops[0]
        self.assertEqual(op["type"], "cut_words")
        self.assertEqual(op["word_start"], pairs[0]["cut"]["word_start"])
        self.assertEqual(op["word_end"], pairs[0]["cut"]["word_end"])
        self.assertEqual(op["reason"], NOTE_LAST)
        # The old loose path on its own still cuts nothing here.
        self.assertEqual(retakes.band_a_operations(tl, None, pairs=pairs), [])


HTTP_SENTENCES = [
    "Welcome back to the channel today my friends.",
    FILLER_A,
    "First we get up before the sun rises.",
    DUPE,
    DUPE,
    NEAR_A,
    NEAR_B,
    "After that we sit down with coffee and the notebook.",
    "Thanks for watching and for being here today.",
]
TOTAL_WORDS = sum(len(s.split()) for s in HTTP_SENTENCES)


def _offset(index):
    return sum(len(s.split()) for s in HTTP_SENTENCES[:index])


DUPE_FIRST = _offset(3)
DUPE_WIDTH = len(DUPE.split())
NEAR_FIRST = _offset(5)
NEAR_WIDTH = len(NEAR_A.split())


class StrictHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v111-retakes-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
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
        self.start_server()

    def start_server(self):
        self.server = create_server(WebAppConfig(self.job), host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
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

    def fire_front_door(self, **extra):
        body = {
            "provider": "mock",
            "remove_retakes": True,
            "cut_silence": False,
            "buffer_s": 0.1,
            "format_scope": "both",
            "direction_text": "",
        }
        body.update(extra)
        status, data = self.call("/api/front-door", body)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"], data)
        return data

    def session_record(self):
        path = self.job / "tye-session" / "retake_pass.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_strict_default_cuts_suspected_retake_and_flags_unsure(self):
        data = self.fire_front_door()
        summary = data["retakes"]
        # No settings change was made: strict is simply in force.
        self.assertTrue(summary["strict"])
        self.assertEqual(summary["band_a"], 1)
        self.assertEqual(summary["band_b"], 1)
        self.assertEqual(summary["applied"], 2)
        self.assertEqual(summary["unsure"], 1)
        self.assertEqual(summary["unsure_note"], NOTE_LAST)
        status, job = self.call("/api/job")
        self.assertEqual(status, 200, job)
        self.assertEqual(
            job["words_kept"], TOTAL_WORDS - DUPE_WIDTH - NEAR_WIDTH
        )
        spans = {s["word_start"]: s for s in job["edit_spans"]}
        sure = spans[DUPE_FIRST]
        self.assertIsNotNone(sure["retake"])
        self.assertFalse(sure["retake"]["unsure"])
        self.assertIsNone(sure["retake"]["unsure_note"])
        unsure = spans[NEAR_FIRST]
        self.assertIsNotNone(unsure["retake"])
        self.assertTrue(unsure["retake"]["unsure"])
        # The popup data carries the note in plain words.
        self.assertEqual(unsure["retake"]["unsure_note"], NOTE_LAST)
        # The stored pair record carries the mark too.
        record = self.session_record()
        by_band = {p["band"]: p for p in record["pairs"]}
        self.assertFalse(by_band["a"]["unsure"])
        self.assertTrue(by_band["b"]["unsure"])
        self.assertEqual(by_band["b"]["outcome"], "applied")
        # The transcript words of the unsure cut are named as such,
        # and the sure cut's words are not.
        words = {
            w["index"]: w for w in job["transcript_words"] if "index" in w
        }
        self.assertTrue(words[NEAR_FIRST].get("unsure_cut"))
        self.assertFalse(words[DUPE_FIRST].get("unsure_cut", False))

    def test_restoring_an_unsure_cut_works_like_any_restore(self):
        self.fire_front_door()
        edit_id = f"edit-{NEAR_FIRST}-{NEAR_FIRST + NEAR_WIDTH}"
        status, result = self.call(
            "/api/edit-action", {"edit_id": edit_id, "action": "restore"}
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["changed"], result)
        status, job = self.call("/api/job")
        self.assertEqual(job["words_kept"], TOTAL_WORDS - DUPE_WIDTH)

    def test_profile_choice_for_loose_still_wins(self):
        (self.job / "profile.json").write_text(
            json.dumps({"retakes": {"strict": False}}), encoding="utf-8"
        )
        data = self.fire_front_door()
        summary = data["retakes"]
        self.assertFalse(summary["strict"])
        # The sure duplicate is still cut; the borderline pair is
        # left as a flag for the creator's call, as before.
        self.assertEqual(summary["applied"], 1)
        self.assertEqual(summary["flagged"], 1)
        self.assertEqual(summary["unsure"], 0)
        status, job = self.call("/api/job")
        self.assertEqual(job["words_kept"], TOTAL_WORDS - DUPE_WIDTH)

    def test_template_shows_unsure_cuts_and_the_note(self):
        status, page = self.call("/")
        self.assertEqual(status, 200)
        self.assertIn("tw-unsure", page)
        self.assertIn("editPopupUnsure", page)
        self.assertIn("unsure_note", page)


if __name__ == "__main__":
    unittest.main()
