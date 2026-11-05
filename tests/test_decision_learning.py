"""Tests for source (c): learning from the creator's decisions.

One verdict on a review spot proposes one rule, in plain
words, with Save / Not now. Saving answers "Thanks. Learned:
..." and the rule settles matching spots on the next edit,
with the job summary counting the decision rules it used, so
the Done bubble can say "Used N rules learned from your last
edit." Nothing saves without the yes: Not now saves nothing
and the save path refuses the pattern afterwards.

Run from the repository root:
    PYTHONPATH=src python3 -m unittest tests.test_decision_learning
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

from talk_to_your_editor import webapp
from talk_to_your_editor.webapp import WebAppConfig, create_server


def timed_words(count: int) -> list:
    return [
        {"word": f"word{i}", "start": float(i), "end": float(i) + 0.9}
        for i in range(count)
    ]


def write_job(job: Path, words: list) -> None:
    job.mkdir(parents=True, exist_ok=True)
    transcript = {
        "engine": "test",
        "duration": float(words[-1]["end"]) if words else 0.0,
        "words": words,
    }
    cuts = {
        "source_duration_s": transcript["duration"],
        "kept_ranges": [{"start": 0.0, "end": transcript["duration"]}],
    }
    (job / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")


def gold_candidate(kind: str, start: int = 2, end: int = 5) -> dict:
    return {
        "gold_id": webapp._gold_id(start, end, kind),
        "word_start": start,
        "word_end": end,
        "kind": kind,
        "source": "retakes" if kind == "retake" else "direction",
        "reason": "held for your call",
        "text": "word2 word3 word4",
        "keep_text": None,
        "keep_word_start": None,
        "keep_word_end": None,
        "pair_id": None,
        "pair_key": None,
        "operation": None,
        "status": "open",
        "note": None,
        "created_at": webapp._utc_now(),
        "decided_at": None,
    }


class DecisionServer:
    """A real server on an ephemeral port over one job."""

    def __init__(self, job: Path, launch_home: Path = None):
        self.config = WebAppConfig(job, launch_home=launch_home)
        self.server = create_server(self.config, host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def post_json(self, path, data):
        payload = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def get_json(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))


class DecisionLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-decision-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"
        write_job(self.job, timed_words(20))
        self.api = DecisionServer(self.job)
        self.config = self.api.config

    def tearDown(self) -> None:
        self.api.close()
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def thread_entries(self):
        status, data = self.api.get_json("/api/thread")
        self.assertEqual(status, 200)
        return data["entries"]

    def test_verdict_proposes_one_rule_and_save_teaches_it(self):
        candidate = gold_candidate("retake")
        webapp._record_gold_spans(self.config, [candidate])
        note = "I left that in because it compares 2.18 to 2.16."
        status, result = self.api.post_json(
            "/api/gold-verdict",
            {
                "gold_id": candidate["gold_id"],
                "decision": "leave",
                "note": note,
            },
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])

        # One open proposal, carrying his own words as the rule.
        proposals = [
            e
            for e in self.thread_entries()
            if e.get("kind") == "rule_proposal"
        ]
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["status"], "open")
        self.assertEqual(proposals[0]["pattern_key"], "gold:retake:leave")
        self.assertEqual(proposals[0]["statement"], note)

        # The proposal rides the rule suggestions as a decision.
        offered = [
            s
            for s in webapp._rule_suggestions(self.config)
            if s["pattern_key"] == "gold:retake:leave"
        ]
        self.assertEqual(len(offered), 1)
        self.assertEqual(offered[0]["source"], "decision")
        self.assertEqual(offered[0]["statement"], note)

        # Save: the rule lands, the proposal settles, and the
        # chat says what it learned, in one line, exactly.
        status, saved = self.api.post_json(
            "/api/rule-save", {"pattern_key": "gold:retake:leave"}
        )
        self.assertEqual(status, 200, saved)
        store = webapp._rules_store(self.config)
        rules = [
            r
            for r in store["rules"]
            if r.get("pattern_key") == "gold:retake:leave"
        ]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["statement"], note)
        entries = self.thread_entries()
        learned = [
            e["text"]
            for e in entries
            if e.get("kind") == "rule" and e.get("text", "").startswith(
                "Thanks. Learned:"
            )
        ]
        self.assertEqual(learned, ["Thanks. Learned: " + note])
        settled = [
            e
            for e in entries
            if e.get("kind") == "rule_proposal"
        ]
        self.assertEqual(settled[0]["status"], "settled")

        # A new spot of the same kind never waits for a verdict
        # at all now: the saved rule settles it on sight, and no
        # new proposal opens.
        again = gold_candidate("retake", start=8, end=11)
        webapp._record_gold_spans(self.config, [again])
        records = webapp._load_gold(self.config)
        again_record = [
            r for r in records if r["gold_id"] == again["gold_id"]
        ]
        self.assertEqual(len(again_record), 1)
        self.assertEqual(again_record[0]["status"], "left")
        self.assertTrue(again_record[0]["settled_by_rule"])
        proposals = [
            e
            for e in self.thread_entries()
            if e.get("kind") == "rule_proposal"
            and e.get("status") == "open"
        ]
        self.assertEqual(proposals, [])

        # The next edit: a fresh job under the same roof. A new
        # retake spot is settled by the saved rule on sight, and
        # the job summary counts the rule it used.
        job2 = self.tmp / "job2"
        write_job(job2, timed_words(20))
        api2 = DecisionServer(job2, launch_home=self.config.launch_home)
        try:
            fresh = gold_candidate("retake")
            webapp._record_gold_spans(api2.config, [fresh])
            records = webapp._load_gold(api2.config)
            settled = [
                r for r in records if r["gold_id"] == fresh["gold_id"]
            ]
            self.assertEqual(len(settled), 1)
            self.assertEqual(settled[0]["status"], "left")
            self.assertTrue(settled[0]["settled_by_rule"])
            self.assertEqual(
                settled[0]["decision_rule"], "gold:retake:leave"
            )
            status, summary = api2.get_json("/api/job")
            self.assertEqual(status, 200)
            self.assertEqual(summary["decision_rules_used"], 1)
        finally:
            api2.close()

    def test_not_now_saves_nothing_and_save_refuses_after(self):
        candidate = gold_candidate("held")
        webapp._record_gold_spans(self.config, [candidate])
        status, _ = self.api.post_json(
            "/api/gold-verdict",
            {"gold_id": candidate["gold_id"], "decision": "leave", "note": ""},
        )
        self.assertEqual(status, 200)
        proposals = [
            e
            for e in self.thread_entries()
            if e.get("kind") == "rule_proposal"
        ]
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["pattern_key"], "gold:held:leave")
        self.assertEqual(
            proposals[0]["statement"],
            webapp._GOLD_RULE_STATEMENTS[("held", "leave")],
        )
        status, _ = self.api.post_json(
            "/api/rule-dismiss", {"pattern_key": "gold:held:leave"}
        )
        self.assertEqual(status, 200)
        store = webapp._rules_store(self.config)
        self.assertEqual(
            [
                r
                for r in store["rules"]
                if r.get("pattern_key") == "gold:held:leave"
            ],
            [],
        )
        proposals = [
            e
            for e in self.thread_entries()
            if e.get("kind") == "rule_proposal"
        ]
        self.assertEqual(proposals[0]["status"], "settled")
        status, refused = self.api.post_json(
            "/api/rule-save", {"pattern_key": "gold:held:leave"}
        )
        self.assertEqual(status, 400, refused)


if __name__ == "__main__":
    unittest.main()
