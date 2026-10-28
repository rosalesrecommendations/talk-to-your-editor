"""Tests for v0.9.0 Cluster 5: costs, estimates, and run progress.

Covers FLAG C (the retake check priced at about 97 cents against
a published about 2 cents), item 25 (the estimate explains itself
in plain words), item 15 (every long step shows elapsed time, a
percentage, and a time remaining computed from the run's actual
progress), and item 26 (step copy matches measured behavior).

FLAG C's trace, in numbers: the published typical for the retake
judge is one Spark call, 3450 prompt + 6350 completion tokens,
which prices to $0.024385 (about 2 cents) at the derived Spark
rates. The old pass then escalated every judged cut to Wonder,
one review call per cut, and the accounting folded those Wonder
dollars into the Retake check line. These tests pin the fix at
both roots: the judge makes the one Spark call only, and any
Wonder usage a pass records prices onto the Second review line,
never onto the feature's own line.

No live call is made anywhere: the provider's chat is stubbed.
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
from talk_to_your_editor.meta_provider import (
    SPARK_MODEL,
    WONDER_MODEL,
    MetaProvider,
)
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig, create_server

# Hand-computed from the rates in costs.py, as in test_v072.py.
SPARK_JUDGE = {"model": "muse-spark-1.3", "prompt_tokens": 3450,
               "completion_tokens": 6350}            # $0.024385
WONDER_REVIEW = {"model": "muse-wonder-1.0", "prompt_tokens": 560,
                 "completion_tokens": 2150}         # $0.59113
SPARK_USD = 0.024385
WONDER_USD = 0.59113

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
)


def timeline_for(sentences):
    words = []
    t = 0.0
    for sentence in sentences:
        for token in sentence.split():
            words.append({"word": token, "start": round(t, 3),
                          "end": round(t + 0.35, 3)})
            t += 0.45
    transcript = {"words": words, "duration": round(t, 3)}
    total = float(words[-1]["end"]) + 0.5
    cuts = {"kept_ranges": [{"start": 0.0, "end": total}],
            "source_duration_s": total}
    return Timeline(transcript, cuts)


# ---------------------------------------------------------------------------
# FLAG C, root 1: the judge makes one Spark call, never a Wonder
# call per judged cut
# ---------------------------------------------------------------------------


class _CountingJudge(MetaProvider):
    """A provider whose chat records the model of every call and
    answers with canned verdicts (Spark) or a canned reviewed
    operation (Wonder, if anything ever asks)."""

    def __init__(self, pair_id):
        super().__init__("test-key", allow_wonder=True)
        self.pair_id = pair_id
        self.models = []

    def _chat(self, model, messages):
        self.models.append(model)
        if model == WONDER_MODEL:
            return json.dumps({"operations": [{
                "type": "cut_words",
                "comment_id": self.pair_id,
                "word_start": 0,
                "word_end": 25,
                "reason": "Reviewed and confirmed.",
                "confidence": 0.9,
                "model": WONDER_MODEL,
                "risk_tier": 2,
                "wonder_reviewed": False,
            }]})
        return json.dumps({"verdicts": [{
            "pair_id": self.pair_id,
            "verdict": "cut_earlier",
            "confidence": 0.6,
            "reason": "The second try finishes it.",
        }]})


class JudgeCallCountTests(unittest.TestCase):
    def test_judged_cut_never_escalates_to_wonder(self):
        sentences = [
            "One two three four five six seven eight nine ten.",
            "Eleven twelve thirteen fourteen fifteen sixteen seventeen "
            "eighteen nineteen twenty.",
            "Alpha bravo charlie delta echo foxtrot golf hotel india "
            "juliet.",
            "Kilo lima mike november oscar papa quebec romeo sierra "
            "tango.",
            "Uniform victor whiskey xray yankee zulu apple banana "
            "cherry dragon.",
            "Eagle falcon gorilla hippo iguana jaguar koala lemur "
            "monkey narwhal.",
            "Otter panda quail raven snake tiger urchin viper walrus "
            "yak.",
            "Zebra anchor beacon compass dagger ember fjord glacier "
            "harbor island.",
        ]
        timeline = timeline_for(sentences)
        words = timeline.words
        # A 25-word cut at 0.6 confidence needs a second review
        # twice over (over 20 words, under the 0.80 floor): the
        # exact shape that used to buy one Wonder call per cut.
        pair = {
            "pair_id": "retake-0-25-30-55",
            "cut": {"word_start": 0, "word_end": 25,
                    "text": " ".join(w["word"] for w in words[0:25])},
            "keep": {"word_start": 30, "word_end": 55,
                     "text": " ".join(w["word"] for w in words[30:55])},
            "band": "b",
            "retake_class": "earlier_take",
            "coverage": 0.7,
            "ratio": 0.7,
            "shape": "unit_pair",
            "cut_complete": True,
            "keep_complete": True,
        }
        provider = _CountingJudge(pair["pair_id"])
        result = provider.judge_retakes([pair], timeline)
        self.assertIsNotNone(result)
        operations, verdicts = result
        self.assertEqual(verdicts, {pair["pair_id"]: "cut_earlier"})
        # The whole pass is the one Spark judgment call.
        self.assertEqual(provider.models, [SPARK_MODEL])
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0]["model"], SPARK_MODEL)
        # Not Wonder-reviewed: the safety check, not a paid review
        # call, holds a cut like this for the creator's call.
        self.assertFalse(operations[0]["wonder_reviewed"])


# ---------------------------------------------------------------------------
# FLAG C, root 2: Wonder usage never lands on another feature's line
# ---------------------------------------------------------------------------


class WonderSplitAccountingTests(unittest.TestCase):
    def test_mixed_usage_splits_wonder_onto_its_own_line(self):
        entries = webapp._usage_cost_entries(
            "retake_judge", [SPARK_JUDGE, WONDER_REVIEW], "first edit"
        )
        by_feature = {e["feature"]: e["usd"] for e in entries}
        self.assertAlmostEqual(
            by_feature["retake_judge"], SPARK_USD, places=9
        )
        self.assertAlmostEqual(by_feature["wonder"], WONDER_USD, places=9)
        block = costs.job_cost_block(entries)
        self.assertIn(
            "Retake check: about 2 cents.", block["recap_lines"]
        )
        self.assertIn(
            "Second review (Muse Wonder): about 59 cents.",
            block["recap_lines"],
        )

    def test_spark_only_usage_is_one_retake_line(self):
        entries = webapp._usage_cost_entries(
            "retake_judge", [SPARK_JUDGE], "first edit"
        )
        self.assertEqual([e["feature"] for e in entries], ["retake_judge"])
        block = costs.job_cost_block(entries)
        self.assertEqual(
            block["recap_lines"],
            [
                "Retake check: about 2 cents.",
                "This edit cost about 2 cents in all.",
            ],
        )

    def test_first_edit_entries_split_the_retake_pass_the_same_way(self):
        entries = webapp._first_edit_cost_entries(
            "scribe",
            591.0,
            {"remove_fillers": True, "cut_silence": True},
            {"usage": [SPARK_JUDGE, WONDER_REVIEW]},
            None,
        )
        by_feature = {e["feature"]: e["usd"] for e in entries}
        self.assertAlmostEqual(
            by_feature["retake_judge"], SPARK_USD, places=9
        )
        self.assertAlmostEqual(by_feature["wonder"], WONDER_USD, places=9)


class EstimateAccountingAgreementTests(unittest.TestCase):
    """The published table and the in-app line describe the same
    one-call pass, so a typical run agrees within rounding."""

    def test_typical_retake_price_is_about_two_cents(self):
        typical = costs._typical_cost("retake_judge", 591.0)
        self.assertAlmostEqual(typical, SPARK_USD, places=9)
        self.assertEqual(costs.format_usd(typical), "about 2 cents")

    def test_estimate_and_accounting_agree_on_a_typical_run(self):
        estimate = costs.estimate_run(
            591.0, "front_door", {"remove_retakes": True}, False
        )
        row = estimate["lines"][0]
        self.assertEqual(row["feature"], "retake_judge")
        entries = webapp._usage_cost_entries(
            "retake_judge", [SPARK_JUDGE], "first edit"
        )
        block = costs.job_cost_block(entries)
        # Same words for the same work, from both sides.
        self.assertEqual(row["text"], "about 2 cents")
        self.assertIn(
            f"Retake check: {row['text']}.", block["recap_lines"]
        )
        self.assertAlmostEqual(
            estimate["total_usd"], block["total_usd"], places=9
        )


# ---------------------------------------------------------------------------
# Item 25: the estimate explains itself, per route and per pool
# ---------------------------------------------------------------------------


class EstimateExplanationTests(unittest.TestCase):
    ALL_ON = {"remove_retakes": True, "remove_fillers": True,
              "cut_silence": True}

    def test_direction_explanation_names_the_second_review(self):
        text = costs.estimate_explanation(
            "first_edit", self.ALL_ON, True, route="scribe"
        )
        self.assertIn("ElevenLabs Scribe", text)
        self.assertIn("checking for retakes", text)
        self.assertIn(
            "reading your direction and a second review", text
        )
        self.assertIn("The second review is the biggest part", text)
        self.assertIn("about", costs.estimate_run(
            591.0, "first_edit", self.ALL_ON, True, route="scribe"
        )["sentence"])

    def test_no_direction_no_second_review_in_the_explanation(self):
        text = costs.estimate_explanation(
            "first_edit", self.ALL_ON, False, route="scribe"
        )
        self.assertNotIn("second review", text)
        self.assertIn("checking for retakes", text)

    def test_front_door_explanation_skips_transcription(self):
        text = costs.estimate_explanation(
            "front_door", {"remove_retakes": True}, True
        )
        self.assertNotIn("Scribe", text)
        self.assertNotIn("writing the words down", text)
        self.assertIn("second review", text)

    def test_hosted_route_is_named_and_wonder_stays_on_meta(self):
        text = costs.estimate_explanation(
            "front_door", {"remove_retakes": True}, True,
            spark_route="fireworks",
        )
        self.assertIn("Fireworks credits", text)
        self.assertIn("the second review always runs on Meta", text)
        meta_text = costs.estimate_explanation(
            "front_door", {"remove_retakes": True}, True,
            spark_route="meta",
        )
        self.assertNotIn("credits", meta_text)

    def test_nothing_on_says_so_plainly(self):
        self.assertEqual(
            costs.estimate_explanation("front_door", {}, False),
            "Nothing that costs anything is turned on for this edit.",
        )

    def test_estimate_run_carries_the_explanation(self):
        estimate = costs.estimate_run(
            591.0, "first_edit", self.ALL_ON, True,
            route="scribe", spark_route="baseten",
        )
        self.assertIn("second review", estimate["explanation"])
        self.assertIn("Baseten credits", estimate["explanation"])
        self.assertEqual(
            estimate["explanation"],
            costs.estimate_explanation(
                "first_edit", self.ALL_ON, True,
                route="scribe", spark_route="baseten",
            ),
        )


# ---------------------------------------------------------------------------
# Item 15: elapsed, percent, and remaining from actual progress
# ---------------------------------------------------------------------------


class ProgressFieldsTests(unittest.TestCase):
    def test_first_stage_has_elapsed_but_no_remaining_yet(self):
        fields = webapp._progress_fields(100.0, 100.0, 1, 8, now=100.0)
        self.assertEqual(fields["elapsed_s"], 0.0)
        self.assertEqual(fields["percent"], 13)
        # No stage has finished, so there is no honest pace yet.
        self.assertIsNone(fields["remaining_s"])

    def test_remaining_comes_from_the_finished_stages_pace(self):
        # At stage 3 of 8, 20 seconds in: two stages finished at
        # 10 seconds each, six stage-lengths remain (the current
        # one plus five), so about 60 seconds to go.
        fields = webapp._progress_fields(100.0, 118.0, 3, 8, now=120.0)
        self.assertEqual(fields["elapsed_s"], 20.0)
        self.assertEqual(fields["stage_elapsed_s"], 2.0)
        self.assertEqual(fields["percent"], 38)
        self.assertEqual(fields["remaining_s"], 60.0)

    def test_last_stage_leaves_one_stage_length(self):
        fields = webapp._progress_fields(0.0, 60.0, 8, 8, now=70.0)
        self.assertEqual(fields["percent"], 100)
        self.assertEqual(fields["remaining_s"], 10.0)

    def test_no_total_no_percent_no_remaining(self):
        fields = webapp._progress_fields(0.0, 0.0, 0, 0, now=5.0)
        self.assertIsNone(fields["percent"])
        self.assertIsNone(fields["remaining_s"])
        self.assertEqual(fields["elapsed_s"], 5.0)

    def test_progress_line_words(self):
        fields = {
            "stage_index": 3, "stage_total": 8, "percent": 38,
            "elapsed_s": 20.0, "remaining_s": 60.0,
        }
        self.assertEqual(
            webapp._progress_line(fields),
            "Step 3 of 8 · 38% · 0:20 so far · about 1:00 to go",
        )

    def test_progress_line_omits_what_it_cannot_honestly_say(self):
        fields = {
            "stage_index": 1, "stage_total": 8, "percent": 13,
            "elapsed_s": 0.0, "remaining_s": None,
        }
        self.assertEqual(
            webapp._progress_line(fields), "Step 1 of 8 · 13% · 0:00 so far"
        )

    def test_format_clock(self):
        self.assertEqual(webapp._format_clock(7), "0:07")
        self.assertEqual(webapp._format_clock(760), "12:40")
        self.assertEqual(webapp._format_clock(0), "0:00")


# ---------------------------------------------------------------------------
# Item 26: no step promises a long wait it does not take
# ---------------------------------------------------------------------------


class StepCopyTests(unittest.TestCase):
    def test_no_step_claims_a_long_wait(self):
        source = (
            ROOT / "src" / "talk_to_your_editor" / "webapp.py"
        ).read_text(encoding="utf-8")
        template = TEMPLATE.read_text(encoding="utf-8")
        for banned in (
            "This will take a while",
            "so this step takes a while",
            "Listening to your video",
        ):
            self.assertNotIn(banned, source)
            self.assertNotIn(banned, template)

    def test_first_step_names_what_it_does(self):
        source = (
            ROOT / "src" / "talk_to_your_editor" / "webapp.py"
        ).read_text(encoding="utf-8")
        self.assertIn("Taking the sound out of your video", source)

    def test_the_one_kept_wait_claim_is_the_download(self):
        source = (
            ROOT / "src" / "talk_to_your_editor" / "webapp.py"
        ).read_text(encoding="utf-8")
        # The local route's first-run model download is the only
        # step copy still promising minutes, because the download
        # really takes them.
        self.assertEqual(source.count("can take a few minutes"), 1)
        self.assertIn("the speech model downloads once", source)


# ---------------------------------------------------------------------------
# The template and the endpoint carry the new surfaces
# ---------------------------------------------------------------------------


class NewSurfaceContractTests(unittest.TestCase):
    def test_template_carries_explanation_and_progress_detail(self):
        template = TEMPLATE.read_text(encoding="utf-8")
        for needle in (
            'id="firstEditCostWhy"',
            'id="fdCostWhy"',
            'id="firstEditProgressDetail"',
            "data.estimate.explanation",
            "firstEditProgressDetail",
            "remaining_s",
            "setInterval(renderFirstEditProgressDetail, 1000)",
        ):
            self.assertIn(needle, template)


class EstimateEndpointExplanationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v09-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(
            os.environ, {"HOME": str(self.home)}
        )
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        launch = self.tmp / "launch home"
        launch.mkdir()
        self.server = create_server(
            WebAppConfig(None, launch_home=launch),
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
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post_json(self, path, payload):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_estimate_endpoint_explains_the_direction_jump(self):
        plain = self.post_json(
            "/api/cost-estimate",
            {"surface": "front_door", "duration_s": 591,
             "settings": {"remove_retakes": True},
             "has_direction": False},
        )["estimate"]
        directed = self.post_json(
            "/api/cost-estimate",
            {"surface": "front_door", "duration_s": 591,
             "settings": {"remove_retakes": True},
             "has_direction": True},
        )["estimate"]
        self.assertEqual(plain["sentence"],
                         "This edit usually costs about 2 cents.")
        self.assertNotIn("second review", plain["explanation"])
        self.assertIn("second review", directed["explanation"])
        self.assertIn("biggest part", directed["explanation"])
        self.assertGreater(directed["total_usd"], plain["total_usd"])


if __name__ == "__main__":
    unittest.main()
