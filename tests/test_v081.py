"""Tests for v0.8.1: hosted Spark routes (Baseten and Fireworks).

Covers the route choice and its persistence in the usual, the
provider's transport swap against mocked hosted endpoints (URL,
wire model, bearer key, usage route), the one-time fallback to
Meta on a hosted failure with the recap naming the route that
ran, Wonder pinned to Meta, the costs module's pools (recap
lines, the money view's per-pool breakdown, the estimate under
a chosen route, derivation for records written before pools),
the sign-in card's hosted keys and checks over HTTP, and the
no-hosted-key behavior matching 0.8.0 exactly.

No live call is made anywhere: every endpoint is mocked.
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

from talk_to_your_editor import costs, usual, webapp
from talk_to_your_editor.meta_provider import (
    API_URL as META_URL,
    BASETEN_URL,
    FIREWORKS_URL,
    MetaProvider,
    ProviderError,
    check_hosted_signin,
)
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig, create_server

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The same hand-computed fixtures the v0.7.2 tests price:
#   Spark  prompt $0.35/M, completion $3.65/M
DIRECTION_USD = 0.054635
FW_DIRECTION = {
    "model": "accounts/fireworks/models/muse-spark-open",
    "route": "fireworks",
    "prompt_tokens": 24700,
    "completion_tokens": 12600,
}
BT_JUDGE = {
    "model": "meta-models/muse-spark-open-1026",
    "route": "baseten",
    "prompt_tokens": 3450,
    "completion_tokens": 6350,
}


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def scrub_env(case: unittest.TestCase) -> None:
    """Isolate HOME and clear every key the app reads from env."""
    tmp = Path(tempfile.mkdtemp(prefix="tye-v081-"))
    case.addCleanup(shutil.rmtree, tmp, True)
    home = tmp / "home"
    home.mkdir()
    patcher = mock.patch.dict(os.environ, {"HOME": str(home)})
    patcher.start()
    case.addCleanup(patcher.stop)
    for name in (
        "MODEL_API_KEY",
        "ELEVENLABS_API_KEY",
        "BASETEN_API_KEY",
        "FIREWORKS_API_KEY",
    ):
        os.environ.pop(name, None)
    case.tmp = tmp
    case.home = home


class FakeResponse:
    """A urlopen stand-in: a context manager with a JSON body."""

    def __init__(self, payload: dict, status: int = 200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def chat_payload(content: str, prompt=10, completion=5) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


class RecordingTransport:
    """urlopen replacement: records each request, answers by URL.

    ``fail`` maps a URL substring to the exception it should raise.
    """

    def __init__(self, fail=None):
        self.requests = []
        self.fail = fail or {}

    def __call__(self, request, timeout=None):
        url = request.full_url
        body = json.loads(request.data.decode("utf-8")) if request.data else {}
        self.requests.append(
            {
                "url": url,
                "model": body.get("model"),
                "auth": request.headers.get("Authorization"),
                "body": body,
            }
        )
        for needle, exc in self.fail.items():
            if needle in url:
                raise exc
        if url.endswith("/models"):
            return FakeResponse({"data": []})
        return FakeResponse(chat_payload('{"operations": [], "flags": []}'))

    def urls(self):
        return [r["url"] for r in self.requests]


# ---------------------------------------------------------------------------
# The route choice in the usual
# ---------------------------------------------------------------------------


class UsualRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        scrub_env(self)

    def test_route_persists_and_defaults_to_meta(self):
        self.assertEqual(
            usual.load_usual({"spark_route": "meta"})["spark_route"], "meta"
        )
        usual.save_usual({"spark_route": "fireworks"})
        self.assertEqual(
            usual.load_usual({"spark_route": "meta"})["spark_route"],
            "fireworks",
        )

    def test_unknown_route_cleans_back_to_meta(self):
        usual.save_usual({"spark_route": "somewhere-else"})
        self.assertEqual(
            usual.load_usual({"spark_route": "meta"})["spark_route"], "meta"
        )


# ---------------------------------------------------------------------------
# Provider transport: which wire a Spark call rides
# ---------------------------------------------------------------------------


class ProviderTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        scrub_env(self)

    def provider(self, **kwargs) -> MetaProvider:
        kwargs.setdefault("api_key", "meta-key")
        return MetaProvider(**kwargs)

    def test_spark_call_rides_baseten(self):
        transport = RecordingTransport()
        provider = self.provider(
            spark_route="baseten", baseten_key="bt-key"
        )
        with mock.patch(
            "talk_to_your_editor.meta_provider.urllib.request.urlopen",
            transport,
        ):
            content = provider._chat("muse-spark-1.3", [{"role": "user", "content": "hi"}])
        self.assertEqual(len(transport.requests), 1)
        request = transport.requests[0]
        self.assertEqual(request["url"], BASETEN_URL)
        self.assertEqual(request["model"], "meta-models/muse-spark-open-1026")
        self.assertEqual(request["auth"], "Bearer bt-key")
        self.assertIn("operations", content)
        usage = provider.drain_usage()
        self.assertEqual(usage[0]["route"], "baseten")
        self.assertEqual(usage[0]["model"], "meta-models/muse-spark-open-1026")

    def test_spark_call_rides_fireworks(self):
        transport = RecordingTransport()
        provider = self.provider(
            spark_route="fireworks", fireworks_key="fw-key"
        )
        with mock.patch(
            "talk_to_your_editor.meta_provider.urllib.request.urlopen",
            transport,
        ):
            provider._chat("muse-spark-1.3", [{"role": "user", "content": "hi"}])
        request = transport.requests[0]
        self.assertEqual(request["url"], FIREWORKS_URL)
        self.assertEqual(
            request["model"], "accounts/fireworks/models/muse-spark-open"
        )
        self.assertEqual(request["auth"], "Bearer fw-key")
        self.assertEqual(provider.drain_usage()[0]["route"], "fireworks")

    def test_wonder_never_rides_a_hosted_route(self):
        transport = RecordingTransport()
        provider = self.provider(
            spark_route="fireworks", fireworks_key="fw-key"
        )
        with mock.patch(
            "talk_to_your_editor.meta_provider.urllib.request.urlopen",
            transport,
        ):
            provider._chat("muse-wonder-1.0", [{"role": "user", "content": "hi"}])
        request = transport.requests[0]
        self.assertEqual(request["url"], META_URL)
        self.assertEqual(request["model"], "muse-wonder-1.0")
        self.assertEqual(request["auth"], "Bearer meta-key")
        self.assertEqual(provider.drain_usage()[0]["route"], "meta")

    def test_no_route_chosen_is_meta_as_always(self):
        transport = RecordingTransport()
        provider = self.provider()
        with mock.patch(
            "talk_to_your_editor.meta_provider.urllib.request.urlopen",
            transport,
        ):
            provider._chat("muse-spark-1.3", [{"role": "user", "content": "hi"}])
        self.assertEqual(provider.spark_route, "meta")
        self.assertEqual(transport.requests[0]["url"], META_URL)
        self.assertEqual(transport.requests[0]["model"], "muse-spark-1.3")

    def test_chosen_route_without_a_key_resolves_to_meta(self):
        provider = self.provider(spark_route="fireworks")
        self.assertEqual(provider.spark_route, "meta")

    def test_hosted_http_error_falls_back_to_meta_once(self):
        transport = RecordingTransport(
            fail={
                "inference.baseten.co": urllib.error.HTTPError(
                    BASETEN_URL, 500, "boom", {}, None
                )
            }
        )
        provider = self.provider(spark_route="baseten", baseten_key="bt-key")
        with mock.patch(
            "talk_to_your_editor.meta_provider.urllib.request.urlopen",
            transport,
        ):
            content = provider._chat(
                "muse-spark-1.3", [{"role": "user", "content": "hi"}]
            )
        self.assertIn("operations", content)
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(transport.requests[1]["url"], META_URL)
        self.assertEqual(transport.requests[1]["model"], "muse-spark-1.3")
        events = provider.drain_route_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["requested"], "baseten")
        self.assertEqual(events[0]["used"], "meta")
        self.assertEqual(provider.drain_usage()[0]["route"], "meta")

    def test_hosted_timeout_falls_back_to_meta(self):
        transport = RecordingTransport(
            fail={
                "api.fireworks.ai": urllib.error.URLError("timed out")
            }
        )
        provider = self.provider(
            spark_route="fireworks", fireworks_key="fw-key"
        )
        with mock.patch(
            "talk_to_your_editor.meta_provider.urllib.request.urlopen",
            transport,
        ):
            provider._chat("muse-spark-1.3", [{"role": "user", "content": "hi"}])
        self.assertEqual(transport.urls()[0], FIREWORKS_URL)
        self.assertEqual(transport.urls()[1], META_URL)
        self.assertEqual(len(provider.drain_route_events()), 1)

    def test_hosted_failure_without_a_meta_key_reraises(self):
        transport = RecordingTransport(
            fail={
                "inference.baseten.co": urllib.error.HTTPError(
                    BASETEN_URL, 500, "boom", {}, None
                )
            }
        )
        provider = MetaProvider(
            api_key=None, spark_route="baseten", baseten_key="bt-key"
        )
        with mock.patch(
            "talk_to_your_editor.meta_provider.urllib.request.urlopen",
            transport,
        ):
            with self.assertRaises(ProviderError):
                provider._chat(
                    "muse-spark-1.3", [{"role": "user", "content": "hi"}]
                )
        self.assertEqual(len(transport.requests), 1)

    def test_direction_pass_rides_the_hosted_route(self):
        transport = RecordingTransport()
        provider = self.provider(
            spark_route="fireworks", fireworks_key="fw-key"
        )
        timeline = Timeline(
            load_fixture("sample_transcript.json"),
            load_fixture("sample_cuts.json"),
        )
        with mock.patch(
            "talk_to_your_editor.meta_provider.urllib.request.urlopen",
            transport,
        ):
            operations, routing, flags = provider.direct_transcript(
                {"direction": "keep it tight"}, timeline, None
            )
        self.assertEqual((operations, routing, flags), ([], [], []))
        self.assertEqual(transport.requests[0]["url"], FIREWORKS_URL)
        self.assertEqual(provider.drain_usage()[0]["route"], "fireworks")

    def test_retake_judge_rides_the_hosted_route(self):
        transport = RecordingTransport()
        provider = self.provider(spark_route="baseten", baseten_key="bt-key")
        timeline = Timeline(
            load_fixture("sample_transcript.json"),
            load_fixture("sample_cuts.json"),
        )
        words = timeline.words
        pair = {
            "pair_id": "pair-1",
            "cut": {
                "word_start": 0,
                "word_end": 2,
                "text": " ".join(w["word"] for w in words[0:2]),
            },
            "keep": {
                "word_start": 2,
                "word_end": 4,
                "text": " ".join(w["word"] for w in words[2:4]),
            },
            "coverage": 0.9,
            "ratio": 1.0,
            "shape": "same",
            "cut_complete": True,
            "keep_complete": True,
        }
        verdict_payload = chat_payload(
            json.dumps(
                {
                    "verdicts": [
                        {
                            "pair_id": "pair-1",
                            "verdict": "keep_both",
                            "confidence": 0.9,
                            "reason": "said twice on purpose",
                        }
                    ]
                }
            )
        )

        def answering(request, timeout=None):
            transport.requests.append(
                {"url": request.full_url, "model": None, "auth": None, "body": {}}
            )
            return FakeResponse(verdict_payload)

        with mock.patch(
            "talk_to_your_editor.meta_provider.urllib.request.urlopen",
            answering,
        ):
            judged = provider.judge_retakes([pair], timeline)
        self.assertIsNotNone(judged)
        _operations, verdicts = judged
        self.assertEqual(verdicts, {"pair-1": "keep_both"})
        self.assertEqual(transport.requests[0]["url"], BASETEN_URL)

    def test_check_hosted_signin_accepts_and_refuses(self):
        transport = RecordingTransport()
        with mock.patch(
            "talk_to_your_editor.meta_provider.urllib.request.urlopen",
            transport,
        ):
            self.assertTrue(check_hosted_signin("baseten", "bt-key"))
        self.assertTrue(
            transport.requests[0]["url"].endswith("/models")
        )
        refusing = RecordingTransport(
            fail={
                "api.fireworks.ai": urllib.error.HTTPError(
                    FIREWORKS_URL, 401, "no", {}, None
                )
            }
        )
        with mock.patch(
            "talk_to_your_editor.meta_provider.urllib.request.urlopen",
            refusing,
        ):
            self.assertFalse(check_hosted_signin("fireworks", "bad-key"))


# ---------------------------------------------------------------------------
# Costs: pools on entries, lines, views, and the estimate
# ---------------------------------------------------------------------------


class CostsPoolsTests(unittest.TestCase):
    def test_direction_entries_charge_the_route_that_ran(self):
        entries = webapp._direction_cost_entries([FW_DIRECTION])
        direction = [e for e in entries if e["feature"] == "direction"]
        self.assertEqual(len(direction), 1)
        self.assertEqual(direction[0]["pool"], "fireworks")
        self.assertAlmostEqual(direction[0]["usd"], DIRECTION_USD, places=9)
        block = costs.job_cost_block(entries)
        self.assertIn(
            "Reading your direction (Fireworks credits): about 5 cents.",
            block["recap_lines"],
        )

    def test_retake_entries_charge_the_route_that_ran(self):
        entries = webapp._usage_cost_entries("retake_judge", [BT_JUDGE], "first edit")
        self.assertEqual(entries[0]["pool"], "baseten")
        block = costs.job_cost_block(entries)
        self.assertIn(
            "Retake check (Baseten credits): about 2 cents.",
            block["recap_lines"],
        )

    def test_meta_lines_keep_their_plain_wording(self):
        entries = webapp._direction_cost_entries(
            [
                {
                    "model": "muse-spark-1.3",
                    "prompt_tokens": 24700,
                    "completion_tokens": 12600,
                }
            ]
        )
        block = costs.job_cost_block(entries)
        self.assertIn(
            "Reading your direction: about 5 cents.", block["recap_lines"]
        )

    def test_split_usage_cost_by_route(self):
        by_route = costs.split_usage_cost_by_route(
            [FW_DIRECTION, BT_JUDGE, {"model": "muse-wonder-1.0",
                                      "route": "fireworks",
                                      "prompt_tokens": 560,
                                      "completion_tokens": 2150}]
        )
        self.assertAlmostEqual(by_route["fireworks"][0], DIRECTION_USD, places=9)
        # Wonder is always Meta's, whatever the entry claims.
        self.assertEqual(by_route["fireworks"][1], 0.0)
        self.assertAlmostEqual(by_route["meta"][1], 0.59113, places=9)
        self.assertAlmostEqual(by_route["baseten"][0], 0.024385, places=9)

    def test_money_view_keeps_pools_separate(self):
        entries = costs._clean_entries(
            [
                {"at": "", "job": "", "feature": "direction", "usd": 0.05,
                 "detail": "", "pool": "meta"},
                {"at": "", "job": "", "feature": "direction", "usd": 0.07,
                 "detail": "", "pool": "fireworks"},
                {"at": "", "job": "", "feature": "filler", "usd": 0.0,
                 "detail": "", "pool": "local"},
            ]
        )
        view = costs.money_view(None, entries)
        pools = {row["pool"]: row for row in view["mac"]["pools"]}
        self.assertAlmostEqual(pools["meta"]["usd"], 0.05, places=9)
        self.assertAlmostEqual(pools["fireworks"]["usd"], 0.07, places=9)
        self.assertEqual(pools["fireworks"]["label"], "Fireworks credits")
        self.assertAlmostEqual(view["mac"]["total_usd"], 0.12, places=9)

    def test_records_written_before_pools_derive_theirs(self):
        path = self._money_file(
            [
                {"at": "", "job": "", "feature": "direction", "usd": 0.05,
                 "detail": "old"},
                {"at": "", "job": "", "feature": "transcription", "usd": 0.04,
                 "detail": "old"},
                {"at": "", "job": "", "feature": "transcription", "usd": 0.0,
                 "detail": "old"},
            ]
        )
        entries = costs._read_entries(path)
        self.assertEqual(
            [e["pool"] for e in entries], ["meta", "elevenlabs", "local"]
        )

    def _money_file(self, raw_entries):
        scrub_env(self)
        path = self.home / ".talk-to-your-editor" / "money.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"entries": raw_entries}), encoding="utf-8")
        return path

    def test_estimate_names_the_chosen_route(self):
        settings = {"remove_retakes": True, "remove_fillers": True,
                    "cut_silence": True}
        estimate = costs.estimate_run(
            591, "front_door", settings, True, spark_route="fireworks"
        )
        labels = {row["feature"]: row for row in estimate["lines"]}
        self.assertEqual(
            labels["direction"]["label"],
            "Reading your direction (Fireworks credits)",
        )
        self.assertEqual(labels["direction"]["pool"], "fireworks")
        self.assertEqual(
            labels["retake_judge"]["label"], "Retake check (Fireworks credits)"
        )
        # Wonder stays Meta's, in the estimate as in the run.
        self.assertEqual(labels["wonder"]["label"], "Second review (Muse Wonder)")
        self.assertEqual(labels["wonder"]["pool"], "meta")
        plain = costs.estimate_run(591, "front_door", settings, True)
        plain_labels = {row["feature"]: row["label"] for row in plain["lines"]}
        self.assertEqual(plain_labels["direction"], "Reading your direction")
        self.assertEqual(plain_labels["retake_judge"], "Retake check")


# ---------------------------------------------------------------------------
# The web app: keys, the route endpoint, checks, and the recap
# ---------------------------------------------------------------------------


class FakePassProvider:
    """Stands in for the live provider in a direction pass: it
    reports that its Spark read asked for Fireworks and ran on
    Meta, the fallback the recap must name."""

    def __init__(self):
        self.kwargs = None

    def direct_transcript(self, packet, timeline, profile=None):
        return [], [], []

    def drain_usage(self):
        return [
            {
                "model": "muse-spark-1.3",
                "route": "meta",
                "prompt_tokens": 24700,
                "completion_tokens": 12600,
            }
        ]

    def drain_route_events(self):
        return [
            {
                "requested": "fireworks",
                "used": "meta",
                "error": "fireworks_api_http_500",
            }
        ]


class WebRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        scrub_env(self)
        self.job = self.tmp / "job"
        self.job.mkdir()
        for fixture, target in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / fixture, self.job / target)
        self.config = WebAppConfig(self.job)
        self.server = create_server(self.config, host="127.0.0.1", port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, data=None):
        payload = None
        headers = {}
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path, data=payload, headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def get_json(self, path):
        status, body = self.request(path)
        self.assertEqual(status, 200)
        return json.loads(body.decode("utf-8"))

    def post_json(self, path, data):
        status, body = self.request(path, data=data)
        return status, json.loads(body.decode("utf-8"))

    def test_hosted_key_paste_and_save_on_this_mac(self):
        status, payload = self.post_json(
            "/api/signin", {"kind": "baseten", "api_key": "bt-secret"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["baseten_signin_set"])
        self.assertNotIn("bt-secret", json.dumps(payload))

        status, payload = self.post_json(
            "/api/signin/save", {"kind": "fireworks", "api_key": "fw-secret"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["fireworks_saved_on_mac"])
        saved = self.home / ".talk-to-your-editor" / "fireworks_key"
        self.assertTrue(saved.is_file())
        self.assertEqual(oct(saved.stat().st_mode & 0o777), "0o600")
        self.assertNotIn("fw-secret", json.dumps(payload))

        status, payload = self.post_json(
            "/api/signin/remove", {"kind": "fireworks"}
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["fireworks_saved_on_mac"])
        self.assertFalse(saved.exists())

    def test_unknown_key_kind_is_refused(self):
        status, _payload = self.post_json(
            "/api/signin", {"kind": "somewhere", "api_key": "x"}
        )
        self.assertEqual(status, 400)

    def test_route_choice_persists_in_the_usual(self):
        status, payload = self.post_json(
            "/api/spark-route", {"route": "fireworks"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["spark_route"], "fireworks")
        # No key yet: the effective route is Meta, and says so.
        self.assertEqual(payload["spark_route_effective"], "meta")
        self.assertEqual(
            usual.load_usual({"spark_route": "meta"})["spark_route"],
            "fireworks",
        )
        self.post_json(
            "/api/signin", {"kind": "fireworks", "api_key": "fw-secret"}
        )
        state = self.get_json("/api/signin")
        self.assertEqual(state["spark_route_effective"], "fireworks")

    def test_route_choice_rejects_unknown_routes(self):
        status, _payload = self.post_json(
            "/api/spark-route", {"route": "somewhere-else"}
        )
        self.assertEqual(status, 400)

    def test_live_provider_carries_route_and_keys(self):
        usual.save_usual({"spark_route": "baseten"})
        self.config.session_api_key = "meta-key"
        self.config.session_baseten_key = "bt-key"
        provider = webapp._live_provider(self.config)
        self.assertEqual(provider.spark_route, "baseten")
        self.assertEqual(provider._hosted_key("baseten"), "bt-key")

    def test_signin_check_reports_a_refused_hosted_key_in_plain_words(self):
        self.config.session_fireworks_key = "bad-key"
        with mock.patch.object(
            webapp, "check_hosted_signin", return_value=False
        ):
            status, payload = self.post_json("/api/signin/check", {})
        self.assertEqual(status, 200)
        hosted = payload["hosted"]["fireworks"]
        self.assertFalse(hosted["accepted"])
        self.assertIn("not accepted", hosted["message"])
        self.assertIn("runs on Meta", hosted["message"])
        self.assertNotIn("401", hosted["message"])
        self.assertNotIn("baseten", payload["hosted"])

    def test_direction_pass_recap_names_the_route_that_ran(self):
        self.config.session_api_key = "meta-key"
        fake = FakePassProvider()
        with mock.patch.object(webapp, "_live_provider", return_value=fake):
            summary = webapp._run_direction_pass(
                self.config, "keep it tight", {"mode": "cleanest"}
            )
        self.assertTrue(summary["ran"])
        self.assertIn(
            "Fireworks could not be reached, so this read ran on Meta.",
            summary["lines"],
        )
        entries = webapp._direction_cost_entries(summary["usage"])
        direction = [e for e in entries if e["feature"] == "direction"]
        self.assertEqual(direction[0]["pool"], "meta")

    def test_no_hosted_keys_state_matches_the_old_shape(self):
        state = self.get_json("/api/signin")
        self.assertFalse(state["baseten_signin_set"])
        self.assertFalse(state["fireworks_signin_set"])
        self.assertEqual(state["spark_route"], "meta")
        self.assertEqual(state["spark_route_effective"], "meta")


if __name__ == "__main__":
    unittest.main()
