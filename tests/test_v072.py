"""Tests for v0.7.2: per-feature cost visibility (stdlib unittest).

Covers the costs module's rates math against hand-computed
fixtures, the money formatting words, recap cost lines built
from fixture cost records, the web app's accounting seams (the
first-edit entry builder and the direction entries, both from
fixture usage records), the before-it-runs estimate from a
video's length (fallback typicals with no history, recent-run
averages with history), the Mac money store's totals,
persistence and reset, the summary money contract, and the
estimate and reset endpoints over HTTP with no job loaded.

Every test isolates the home folder the way the v0.7.0 backend
tests do, so the money store under test is the test's own.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import costs, webapp
from talk_to_your_editor.webapp import WebAppConfig, create_server

# Hand-computed from the rates in costs.py (themselves derived
# in that module's docstring from the Oct 21 yardstick bill):
#   Spark  prompt $0.35/M, completion $3.65/M
#   Wonder prompt $138/M,  completion $239/M
SPARK_JUDGE = {"model": "muse-spark-1.3", "prompt_tokens": 3450,
               "completion_tokens": 6350}            # $0.024385
SPARK_DIRECTION = {"model": "muse-spark-1.3", "prompt_tokens": 24700,
                   "completion_tokens": 12600}      # $0.054635
WONDER_REVIEW = {"model": "muse-wonder-1.0", "prompt_tokens": 560,
                 "completion_tokens": 2150}         # $0.59113
SPARK_USD = 0.024385
DIRECTION_USD = 0.054635
WONDER_USD = 0.59113


def write_job(job: Path, words: list) -> None:
    """A minimal job: one kept range covering every word."""
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


class IsolatedHomeCase(unittest.TestCase):
    """A temp home folder, so the money store is the test's own."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v072-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def money_path(self) -> Path:
        return self.home / ".talk-to-your-editor" / "money.json"


# ---------------------------------------------------------------------------
# Rates math
# ---------------------------------------------------------------------------


class RatesMathTests(unittest.TestCase):
    def test_usage_split_prices_each_model_at_its_own_rates(self):
        spark, wonder = costs.split_usage_cost(
            [SPARK_JUDGE, WONDER_REVIEW]
        )
        self.assertAlmostEqual(spark, SPARK_USD, places=9)
        self.assertAlmostEqual(wonder, WONDER_USD, places=9)

    def test_baseten_spark_prices_at_spark_rates(self):
        entry = {"model": "muse-spark-open-1026", "prompt_tokens": 3450,
                 "completion_tokens": 6350}
        spark, wonder = costs.split_usage_cost([entry])
        self.assertAlmostEqual(spark, SPARK_USD, places=9)
        self.assertEqual(wonder, 0.0)

    def test_wonder_completion_is_the_cost_driver(self):
        spark_one = costs.token_cost("muse-spark-1.3", 0, 1_000_000)
        wonder_one = costs.token_cost("muse-wonder-1.0", 0, 1_000_000)
        self.assertGreater(wonder_one, spark_one * 60)

    def test_malformed_usage_prices_as_zero(self):
        spark, wonder = costs.split_usage_cost(
            [None, {"model": "muse-spark-1.3"}, {"prompt_tokens": "x"}]
        )
        self.assertEqual((spark, wonder), (0.0, 0.0))

    def test_transcription_rates(self):
        # Scribe: $0.22 per audio hour, so 591 seconds is $0.0361...
        self.assertAlmostEqual(
            costs.transcription_cost("scribe", 591), 0.036117, places=6
        )
        self.assertAlmostEqual(
            costs.transcription_cost("scribe", 3600), 0.22, places=9
        )
        self.assertEqual(costs.transcription_cost("local", 3600), 0.0)

    def test_dictation_rate_matches_the_ledgers_measured_calls(self):
        # The ledger's real calls: 45.04 s billed $0.00225 and
        # 6.08 s billed $0.00030 at $3.00 per 1,000 minutes.
        self.assertAlmostEqual(costs.dictation_cost(45.04), 0.002252,
                               places=9)
        self.assertAlmostEqual(costs.dictation_cost(6.08), 0.000304,
                               places=9)

    def test_format_usd_words(self):
        self.assertEqual(costs.format_usd(0.0), "0 cents")
        self.assertEqual(costs.format_usd(0.002), "about 0 cents")
        self.assertEqual(costs.format_usd(0.01), "about 1 cent")
        self.assertEqual(costs.format_usd(0.036117), "about 4 cents")
        self.assertEqual(costs.format_usd(0.59113), "about 59 cents")
        self.assertEqual(costs.format_usd(2.5), "about $2.50")


# ---------------------------------------------------------------------------
# Recap cost lines from fixture cost records
# ---------------------------------------------------------------------------


class JobCostBlockTests(unittest.TestCase):
    def entries(self):
        return [
            costs.make_entry("transcription", 0.036117, "scribe"),
            costs.make_entry("retake_judge", SPARK_USD, "first edit"),
            costs.make_entry("direction", DIRECTION_USD, "direction run"),
            costs.make_entry("wonder", WONDER_USD, "direction run"),
            costs.make_entry("flags", 0.0, "rides along"),
            costs.make_entry("filler", 0.0, "on this computer"),
            costs.make_entry("silence", 0.0, "on this computer"),
        ]

    def test_recap_lines_one_per_feature_plus_total(self):
        block = costs.job_cost_block(self.entries())
        self.assertEqual(
            block["recap_lines"],
            [
                "Writing the words down: about 4 cents.",
                "Retake check: about 2 cents.",
                "Reading your direction: about 5 cents.",
                "Second review (Muse Wonder): about 59 cents.",
                "Also noticed: 0 cents.",
                "Taking out the ums and uhs: 0 cents.",
                "Cutting the silence: 0 cents.",
                "This edit cost about 71 cents in all.",
            ],
        )
        self.assertAlmostEqual(block["total_usd"], 0.706267, places=6)

    def test_features_that_did_not_run_do_not_appear(self):
        block = costs.job_cost_block(
            [costs.make_entry("transcription", 0.036117, "scribe")]
        )
        self.assertEqual(
            block["recap_lines"],
            [
                "Writing the words down: about 4 cents.",
                "This edit cost about 4 cents in all.",
            ],
        )

    def test_no_entries_no_block(self):
        self.assertIsNone(costs.job_cost_block([]))

    def test_repeated_features_add_up(self):
        block = costs.job_cost_block(
            [
                costs.make_entry("direction", 0.05, "one"),
                costs.make_entry("direction", 0.07, "two"),
            ]
        )
        self.assertEqual(len(block["lines"]), 1)
        self.assertAlmostEqual(block["total_usd"], 0.12, places=9)


# ---------------------------------------------------------------------------
# The accounting seams, from fixture pass records
# ---------------------------------------------------------------------------


class AccountingSeamTests(IsolatedHomeCase):
    def test_first_edit_entries_from_fixture_summaries(self):
        settings = {"remove_fillers": True, "cut_silence": True}
        retake_summary = {"usage": [SPARK_JUDGE]}
        direction_summary = {
            "ran": True,
            "usage": [SPARK_DIRECTION, WONDER_REVIEW],
        }
        entries = webapp._first_edit_cost_entries(
            "scribe", 591.0, settings, retake_summary, direction_summary
        )
        by_feature = {e["feature"]: e["usd"] for e in entries}
        self.assertAlmostEqual(
            by_feature["transcription"], 0.036117, places=6
        )
        self.assertAlmostEqual(by_feature["retake_judge"], SPARK_USD,
                               places=9)
        self.assertAlmostEqual(by_feature["direction"], DIRECTION_USD,
                               places=9)
        self.assertAlmostEqual(by_feature["wonder"], WONDER_USD, places=9)
        self.assertEqual(by_feature["flags"], 0.0)
        self.assertEqual(by_feature["filler"], 0.0)
        self.assertEqual(by_feature["silence"], 0.0)
        # The scribe entry carries its duration, so the estimate
        # can learn a per-minute rate from it later.
        transcription = next(
            e for e in entries if e["feature"] == "transcription"
        )
        self.assertEqual(transcription["duration_s"], 591.0)

    def test_first_edit_skips_a_direction_pass_that_did_not_run(self):
        entries = webapp._first_edit_cost_entries(
            "local", 591.0, {}, None, {"ran": False, "usage": []}
        )
        self.assertEqual([e["feature"] for e in entries], ["transcription"])
        self.assertEqual(entries[0]["usd"], 0.0)

    def test_direction_entries_carry_flags_at_zero(self):
        entries = webapp._direction_cost_entries(
            [SPARK_DIRECTION, WONDER_REVIEW]
        )
        by_feature = {e["feature"]: e["usd"] for e in entries}
        self.assertAlmostEqual(by_feature["direction"], DIRECTION_USD,
                               places=9)
        self.assertAlmostEqual(by_feature["wonder"], WONDER_USD, places=9)
        self.assertEqual(by_feature["flags"], 0.0)

    def test_account_costs_writes_the_session_and_the_mac_total(self):
        write_job(self.job, [{"word": "hi", "start": 0.0, "end": 0.4}])
        config = WebAppConfig(self.job)
        webapp._account_costs(
            config, [costs.make_entry("direction", 0.05, "test")]
        )
        session_entries = costs.load_session_entries(config.session_dir)
        self.assertEqual(len(session_entries), 1)
        self.assertEqual(session_entries[0]["feature"], "direction")
        mac_entries = costs.load_money_entries()
        self.assertEqual(len(mac_entries), 1)
        self.assertEqual(mac_entries[0]["job"], "job")

    def test_front_door_run_accounts_its_passes(self):
        words = []
        t = 0.0
        for text in ("we went to the", "we went to the store today"):
            for piece in text.split():
                words.append({"word": piece, "start": t, "end": t + 0.3})
                t += 0.35
        write_job(self.job, words)
        config = WebAppConfig(self.job)
        result = webapp._run_front_door(config, {})
        # The result carries the cost lines for the page, ending
        # in the run's total.
        self.assertTrue(result["cost_lines"])
        self.assertTrue(
            result["cost_lines"][-1].startswith("This edit cost")
        )
        self.assertTrue(
            any(
                line.startswith("Retake check:")
                for line in result["cost_lines"]
            )
        )
        # And the run is on the books in both places.
        features = {
            e["feature"] for e in costs.load_session_entries(
                config.session_dir
            )
        }
        self.assertIn("retake_judge", features)
        self.assertIn("direction", features)
        self.assertIn("flags", features)
        self.assertTrue(costs.load_money_entries())


# ---------------------------------------------------------------------------
# The before-it-runs estimate
# ---------------------------------------------------------------------------


class EstimateTests(unittest.TestCase):
    ALL_ON = {"remove_retakes": True, "remove_fillers": True,
              "cut_silence": True}

    def test_first_edit_estimate_from_length_no_history(self):
        estimate = costs.estimate_run(
            591.0, "first_edit", self.ALL_ON, True, route="scribe"
        )
        # 0.036117 + 0.024385 + 0.054635 + 0.59113 = 0.706267.
        self.assertEqual(
            estimate["sentence"],
            "This first edit usually costs about 71 cents.",
        )
        labels = [row["label"] for row in estimate["lines"]]
        self.assertEqual(
            labels,
            [
                "Writing the words down",
                "Retake check",
                "Reading your direction",
                "Second review (Muse Wonder)",
                "Also noticed",
                "Taking out the ums and uhs",
                "Cutting the silence",
            ],
        )
        flags = next(
            row for row in estimate["lines"]
            if row["feature"] == "flags"
        )
        self.assertEqual(flags["usd"], 0.0)
        self.assertIn("rides along", flags["note"])

    def test_local_route_transcription_is_free_and_says_so(self):
        estimate = costs.estimate_run(
            591.0, "first_edit", self.ALL_ON, True, route="local"
        )
        transcription = estimate["lines"][0]
        self.assertEqual(transcription["usd"], 0.0)
        self.assertEqual(transcription["note"], "on this computer")
        self.assertEqual(
            estimate["sentence"],
            "This first edit usually costs about 67 cents.",
        )

    def test_estimate_scales_with_length(self):
        short = costs.estimate_run(
            295.5, "front_door", {"remove_retakes": True}, False
        )
        self.assertAlmostEqual(
            short["total_usd"], SPARK_USD / 2, places=9
        )
        self.assertEqual(
            short["sentence"], "This edit usually costs about 1 cent."
        )
        self.assertEqual(len(short["lines"]), 1)

    def test_no_direction_no_direction_lines(self):
        estimate = costs.estimate_run(
            591.0, "front_door", {"remove_retakes": True}, False
        )
        features = [row["feature"] for row in estimate["lines"]]
        self.assertEqual(features, ["retake_judge"])

    def test_history_averages_win_over_typicals(self):
        history = [
            costs.make_entry("direction", 0.10, "run"),
            costs.make_entry("direction", 0.20, "run"),
        ]
        estimate = costs.estimate_run(
            60.0, "front_door", {}, True, history_entries=history
        )
        direction = next(
            row for row in estimate["lines"]
            if row["feature"] == "direction"
        )
        self.assertAlmostEqual(direction["usd"], 0.15, places=9)
        # Wonder has no history, so it still prices from the
        # typicals, scaled to this video's 60 seconds.
        wonder = next(
            row for row in estimate["lines"]
            if row["feature"] == "wonder"
        )
        self.assertAlmostEqual(
            wonder["usd"], WONDER_USD * 60.0 / 591.0, places=6
        )

    def test_zero_cost_history_does_not_drag_the_estimate(self):
        history = [costs.make_entry("direction", 0.0, "offline mock")]
        estimate = costs.estimate_run(
            591.0, "front_door", {}, True, history_entries=history
        )
        direction = next(
            row for row in estimate["lines"]
            if row["feature"] == "direction"
        )
        self.assertAlmostEqual(direction["usd"], DIRECTION_USD, places=9)

    def test_history_gives_transcription_a_per_minute_rate(self):
        history = [
            costs.make_entry(
                "transcription", 0.11, "scribe", duration_s=1800.0
            )
        ]
        estimate = costs.estimate_run(
            600.0, "first_edit", {}, False,
            route="scribe", history_entries=history,
        )
        transcription = estimate["lines"][0]
        self.assertAlmostEqual(
            transcription["usd"], 0.11 * 600.0 / 1800.0, places=9
        )


# ---------------------------------------------------------------------------
# The Mac money store: totals, persistence, reset
# ---------------------------------------------------------------------------


class MoneyStoreTests(IsolatedHomeCase):
    def test_totals_by_feature_and_persistence(self):
        entries = [
            costs.make_entry("direction", 0.05, "a"),
            costs.make_entry("direction", 0.07, "b"),
            costs.make_entry("wonder", 0.59, "a"),
        ]
        costs.append_money_entries(entries, "job-one")
        costs.append_money_entries(
            [costs.make_entry("dictation", 0.002, "note")], "job-two"
        )
        loaded = costs.load_money_entries()
        self.assertEqual(len(loaded), 4)
        view = costs.money_view([], loaded)
        mac = view["mac"]
        self.assertAlmostEqual(mac["total_usd"], 0.712, places=9)
        self.assertEqual(mac["jobs"], 2)
        by_feature = {row["feature"]: row for row in mac["lines"]}
        self.assertAlmostEqual(
            by_feature["direction"]["usd"], 0.12, places=9
        )
        self.assertEqual(by_feature["wonder"]["text"], "about 59 cents")
        self.assertIsNone(view["job"])

    def test_money_file_is_private(self):
        costs.append_money_entries(
            [costs.make_entry("direction", 0.05, "a")], "job"
        )
        mode = self.money_path().stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_reset_clears_the_total_but_not_the_job_record(self):
        session = self.tmp / "session"
        costs.append_session_entries(
            session, [costs.make_entry("direction", 0.05, "a")]
        )
        costs.append_money_entries(
            [costs.make_entry("direction", 0.05, "a")], "job"
        )
        costs.reset_money()
        self.assertEqual(costs.load_money_entries(), [])
        self.assertEqual(len(costs.load_session_entries(session)), 1)
        # The store still works after a reset.
        costs.append_money_entries(
            [costs.make_entry("silence", 0.0, "b")], "job"
        )
        self.assertEqual(len(costs.load_money_entries()), 1)


# ---------------------------------------------------------------------------
# The summary contract and the endpoints, over HTTP
# ---------------------------------------------------------------------------


class MoneyEndpointTests(IsolatedHomeCase):
    def setUp(self) -> None:
        super().setUp()
        self.launch = self.tmp / "launch home"
        self.launch.mkdir()
        self.server = create_server(
            WebAppConfig(None, launch_home=self.launch),
            host="127.0.0.1",
            port=0,
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
        super().tearDown()

    def get_json(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post_json(self, path, payload):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_no_job_summary_carries_an_empty_money_view(self):
        summary = self.get_json("/api/job")
        money = summary["money"]
        self.assertIsNone(money["job"])
        self.assertEqual(money["mac"]["total_usd"], 0.0)
        self.assertEqual(money["mac"]["lines"], [])

    def test_cost_estimate_endpoint_first_edit(self):
        data = self.post_json(
            "/api/cost-estimate",
            {
                "surface": "first_edit",
                "duration_s": 591,
                "settings": {"remove_retakes": True,
                             "remove_fillers": True,
                             "cut_silence": True},
                "has_direction": True,
            },
        )
        self.assertTrue(data["ok"])
        estimate = data["estimate"]
        self.assertTrue(
            estimate["sentence"].startswith(
                "This first edit usually costs about"
            )
        )
        features = [row["feature"] for row in estimate["lines"]]
        self.assertIn("transcription", features)
        self.assertIn("wonder", features)

    def test_cost_estimate_endpoint_front_door_minimal(self):
        data = self.post_json(
            "/api/cost-estimate",
            {
                "surface": "front_door",
                "duration_s": 591,
                "settings": {"remove_retakes": True},
                "has_direction": False,
            },
        )
        estimate = data["estimate"]
        self.assertEqual(
            [row["feature"] for row in estimate["lines"]],
            ["retake_judge"],
        )
        self.assertEqual(
            estimate["sentence"], "This edit usually costs about 2 cents."
        )

    def test_money_reset_endpoint_clears_and_reports(self):
        costs.append_money_entries(
            [costs.make_entry("direction", 0.05, "a")], "job"
        )
        summary = self.get_json("/api/job")
        self.assertAlmostEqual(
            summary["money"]["mac"]["total_usd"], 0.05, places=9
        )
        data = self.post_json("/api/money-reset", {})
        self.assertTrue(data["ok"])
        self.assertEqual(data["money"]["mac"]["total_usd"], 0.0)
        self.assertEqual(costs.load_money_entries(), [])


# ---------------------------------------------------------------------------
# The template carries the cost surfaces
# ---------------------------------------------------------------------------


class TemplateContractTests(unittest.TestCase):
    def test_cost_surfaces_exist(self):
        template = (
            ROOT / "src" / "talk_to_your_editor" / "templates"
            / "test_app.html"
        ).read_text(encoding="utf-8")
        for needle in (
            'id="firstEditCost"',
            'id="fdCostEstimate"',
            'id="finishCosts"',
            'id="moneyCard"',
            'id="moneyReset"',
            "What the editing has cost",
            "Reset the running total",
            "/api/cost-estimate",
            "/api/money-reset",
            "cost_lines",
            "renderMoney",
        ):
            self.assertIn(needle, template)


if __name__ == "__main__":
    unittest.main()
