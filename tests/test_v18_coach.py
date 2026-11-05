"""Tests for v0.18 Builder C: Coach mode in the thread.

Covers success criterion 6 of the v0.18 plan, including the
two-way refinement:

- Asking "What would you do to make this better?" (the canned
  prompt, also offered as a tappable suggestion on a finished
  edit's thread entry) returns a short, plain list in priority
  order: suggestions grounded in this video's transcript and
  editing record first (left-in retakes, long pauses, held
  items, repeated lines), then story suggestions (opening,
  order, payoff).
- Every suggestion names something really in this video, and
  carries a stable id. "Do it" / "do the first one" / "do the
  second one" map a suggestion id into the normal direction
  path (mock provider, $0 for the coach list itself).
- The coach works both ways: a specific question (about the
  opening) is answered first, before the unasked-for findings,
  and the unasked-for findings are still there.
- Future-recording tips never ride along on a plain coach
  answer; they appear, marked for next time, only when the
  creator asks how to record better.
- The coach path makes no provider call and spends nothing.
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

from talk_to_your_editor import coach, costs, webapp  # noqa: E402
from talk_to_your_editor.timeline import Timeline  # noqa: E402
from talk_to_your_editor.webapp import WebAppConfig, create_server  # noqa: E402

PAGE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")

SENTENCES = [
    "Welcome back to the channel my friends today is a good day.",
    "This part is hard to say out loud.",
    "This part is hard to say out loud.",
    "The water is cold and my breath comes fast at first and I "
    "think about every hard thing I have carried this year.",
    "Why would God let this happen to me?",
    "I will not give up on this.",
    "Thanks for watching.",
]

REPEATED_LINE = "This part is hard to say out loud."
# Word spans in the fixture, counted from the sentences above:
# the greeting is words 0-11, the two tries are 12-19 and 20-27,
# the long pause sits after word 27, the strong question is
# 51-58, and the payoff line is 59-65.
RETAKE_SPAN = (12, 20)
HELD_SPAN = (59, 66)


def fixture_words():
    words = []
    t = 0.0
    for index, sentence in enumerate(SENTENCES):
        for token in sentence.split():
            words.append(
                {"word": token, "start": round(t, 3),
                 "end": round(t + 0.35, 3)}
            )
            t += 0.45
        if index == 2:
            t += 3.4  # The long pause, after the repeated line.
    return words


WORDS = fixture_words()


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


def seed_held(config) -> None:
    """One held item on the payoff line, waiting for a call."""
    record = {
        "gold_id": "gold-59-66-held",
        "word_start": HELD_SPAN[0],
        "word_end": HELD_SPAN[1],
        "kind": "held",
        "source": "direction",
        "reason": (
            "The editor recommends removing these words, but the "
            "change is a big one, so you decide whether they go."
        ),
        "text": "I will not give up on this.",
        "operation": None,
        "status": "open",
        "note": None,
        "created_at": webapp._utc_now(),
        "decided_at": None,
    }
    webapp._save_gold(config, [record])


def apply_cut(config, start: int, end: int) -> None:
    transcript = json.loads(
        (config.job_dir / "transcript.json").read_text(encoding="utf-8")
    )
    words = transcript["words"]
    timeline = Timeline(transcript, webapp._effective_cuts(config))
    kept = [
        i
        for i in range(len(words))
        if timeline.word_status(i) == "kept" and not (start <= i < end)
    ]
    ranges = []
    for i in kept:
        if ranges and i == ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], i + 1)
        else:
            ranges.append((i, i + 1))
    revised = {
        "source_duration_s": transcript["duration"],
        "kept_ranges": [
            {"start": words[s]["start"], "end": words[e - 1]["end"]}
            for s, e in ranges
        ],
    }
    config.session_dir.mkdir(parents=True, exist_ok=True)
    (config.session_dir / "revised_cuts.json").write_text(
        json.dumps(revised), encoding="utf-8"
    )


class CoachServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v18-coach-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"
        write_job(self.job, WORDS)
        self.config = WebAppConfig(self.job, launch_home=self.tmp / "launch")
        seed_held(self.config)
        self.server = create_server(self.config, host="127.0.0.1", port=0)
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

    def request(self, path, data=None):
        payload = None
        headers = {}
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path, data=payload, headers=headers,
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def post_json(self, path, data):
        status, body = self.request(path, data=data)
        return status, json.loads(body.decode("utf-8"))

    def get_json(self, path):
        status, body = self.request(path)
        return status, json.loads(body.decode("utf-8"))

    def ask(self, text):
        status, data = self.post_json("/api/thread/message", {"text": text})
        self.assertEqual(status, 200, data)
        return data

    def word_status(self, index):
        transcript = json.loads(
            (self.job / "transcript.json").read_text(encoding="utf-8")
        )
        timeline = Timeline(transcript, webapp._effective_cuts(self.config))
        return timeline.word_status(index)


class CoachModuleTests(unittest.TestCase):
    """The analysis itself, straight from the local record."""

    def build(self):
        transcript = {
            "engine": "test",
            "duration": float(WORDS[-1]["end"]),
            "words": WORDS,
        }
        cuts = {
            "source_duration_s": transcript["duration"],
            "kept_ranges": [
                {"start": 0.0, "end": transcript["duration"]}
            ],
        }
        held = [
            {
                "gold_id": "gold-59-66-held",
                "word_start": HELD_SPAN[0],
                "word_end": HELD_SPAN[1],
                "kind": "held",
                "reason": "The editor recommends removing these words.",
                "text": "I will not give up on this.",
            }
        ]
        return coach.build_suggestions(transcript, cuts, held=held)

    def test_suggestion_ids_are_stable(self):
        first = [s["id"] for s in self.build()]
        second = [s["id"] for s in self.build()]
        self.assertEqual(first, second)
        self.assertEqual(len(first), len(set(first)))
        self.assertTrue(all(sid.startswith("coach-") for sid in first))

    def test_intent_matcher_shapes(self):
        self.assertEqual(
            coach.coach_focus("What would you do to make this better?"),
            "general",
        )
        self.assertEqual(
            coach.coach_focus("How can I make the opening stronger?"),
            "opening",
        )
        self.assertEqual(
            coach.coach_focus("How can I record better next time?"),
            "recording",
        )
        # A direction stays a direction: no question, no coach.
        self.assertIsNone(coach.coach_focus("Make the opening stronger."))
        self.assertIsNone(coach.coach_focus("Cut the first ten seconds."))
        self.assertEqual(coach.followup_index("do it"), 1)
        self.assertEqual(coach.followup_index("do the first one"), 1)
        self.assertEqual(coach.followup_index("do the second one"), 2)
        self.assertIsNone(coach.followup_index("Tighten the opening."))


class CannedPromptTests(CoachServerCase):
    def test_record_suggestions_come_before_story_suggestions(self):
        data = self.ask("What would you do to make this better?")
        self.assertEqual(data["kind"], "coach")
        entry = data["entry"]
        suggestions = entry["suggestions"]
        self.assertTrue(suggestions)
        categories = [s["category"] for s in suggestions]
        # Every record suggestion sits ahead of every story one.
        self.assertEqual(
            categories,
            sorted(categories, key=lambda c: 0 if c == "record" else 1),
        )
        kinds = [s["kind"] for s in suggestions]
        for expected in ("retake", "pause", "held", "opening", "payoff"):
            self.assertIn(expected, kinds)
        # The list is short and in the record order the plan
        # names: retakes, pauses, held items, then story.
        self.assertLessEqual(len(suggestions), 7)
        self.assertEqual(kinds[:3], ["retake", "pause", "held"])
        # The reply's numbered lines match the suggestion order.
        # Since 0.27.1 (P1b item 7) a chat answer shows at most
        # three lines; the rest wait on the entry as lines_more
        # and come back when he says "more". The full reply is
        # lines + lines_more, in order, nothing dropped.
        all_lines = entry["lines"] + (entry.get("lines_more") or [])
        lines = [ln for ln in all_lines if ln[:2].rstrip(".").isdigit()]
        self.assertEqual(len(lines), len(suggestions))
        self.assertIn(REPEATED_LINE, lines[0])
        self.assertLessEqual(1 + len(entry["lines"]), 3)

    def test_a_suggestion_names_actual_transcript_content(self):
        data = self.ask("What would you do to make this better?")
        suggestions = data["entry"]["suggestions"]
        retake = suggestions[0]
        self.assertEqual(retake["kind"], "retake")
        self.assertEqual(
            (retake["word_start"], retake["word_end"]), RETAKE_SPAN
        )
        self.assertIn(REPEATED_LINE, retake["text"])
        self.assertIn(REPEATED_LINE, retake["direction"])
        joined = " ".join(s["text"] for s in suggestions)
        # The long pause, the held line, the greeting opening and
        # the strong line are all named from this transcript.
        self.assertIn("seconds of quiet", joined)
        self.assertIn("I will not give up on this.", joined)
        self.assertIn("Welcome back to the channel", joined)
        self.assertIn("Why would God let this happen to me?", joined)
        # Plain words throughout: no em dashes in the reply.
        self.assertNotIn("\u2014", data["entry"]["text"] + joined)

    def test_coach_answer_makes_no_provider_call_and_spends_nothing(self):
        with mock.patch.object(
            webapp, "_run_direction_pass",
            side_effect=AssertionError("provider path ran"),
        ) as direction, mock.patch.object(
            webapp, "_direction_provider",
            side_effect=AssertionError("provider built"),
        ) as provider:
            data = self.ask("What would you do to make this better?")
        self.assertEqual(data["kind"], "coach")
        direction.assert_not_called()
        provider.assert_not_called()
        self.assertEqual(
            costs.load_session_entries(self.config.session_dir), []
        )

    def test_no_recording_tips_in_a_plain_coach_answer(self):
        data = self.ask("What would you do to make this better?")
        joined = data["entry"]["text"] + " " + " ".join(
            data["entry"]["lines"]
        )
        self.assertNotIn("For next time", joined)
        self.assertNotIn("next time you record", joined.lower())


class SpecificQuestionTests(CoachServerCase):
    def test_the_question_is_answered_before_unasked_for_findings(self):
        data = self.ask("How can I make the opening stronger?")
        self.assertEqual(data["kind"], "coach")
        entry = data["entry"]
        # The answer to the question comes first, in its terms,
        # quoting this video's actual opening.
        self.assertIn("About the opening", entry["text"])
        self.assertIn("Welcome back to the channel", entry["text"])
        # The full reply: shown lines plus the held ones
        # (0.27.1, P1b item 7: three lines shown, rest on "more").
        full = entry["text"] + "\n" + "\n".join(
            entry["lines"] + (entry.get("lines_more") or [])
        )
        self.assertLess(
            full.index("About the opening"),
            full.index("Also, from watching the record:"),
        )
        # The first suggestion offered is the opening one, so a
        # follow-up names the suggestion the answer led with.
        self.assertEqual(entry["suggestions"][0]["kind"], "opening")
        focused_at = full.index("Open on the strong line instead.")
        self.assertLess(
            focused_at, full.index("Also, from watching the record:")
        )

    def test_unasked_for_findings_are_present_anyway(self):
        data = self.ask("How can I make the opening stronger?")
        entry = data["entry"]
        # Present anyway: in the reply, shown or held for
        # "more" (0.27.1, P1b item 7), never dropped.
        all_lines = entry["lines"] + (entry.get("lines_more") or [])
        self.assertIn("Also, from watching the record:", all_lines)
        also_at = all_lines.index("Also, from watching the record:")
        rest = " ".join(all_lines[also_at:])
        # Findings the creator never mentioned: the repeated
        # line and the long pause are both surfaced.
        self.assertIn(REPEATED_LINE, rest)
        self.assertIn("seconds of quiet", rest)
        kinds = [s["kind"] for s in entry["suggestions"]]
        self.assertIn("retake", kinds)
        self.assertIn("pause", kinds)

    def test_a_pacing_question_leads_with_the_pause(self):
        data = self.ask("Is the pacing too slow anywhere?")
        self.assertEqual(data["kind"], "coach")
        self.assertIn("About the pacing", data["entry"]["text"])
        self.assertEqual(data["entry"]["suggestions"][0]["kind"], "pause")


class FollowUpTests(CoachServerCase):
    def _capturing_pass(self, captured, cut_start, cut_end):
        def run(config, text, settings):
            captured.append(text)
            apply_cut(config, cut_start, cut_end)
            return {
                "ran": True, "applied": 1, "flagged_ops": 0,
                "flags": [], "lines": ["Made that change."],
                "usage": [], "route_events": [], "mode": "cleanest",
                "negation_applied": 0, "auto_struck": [],
                "auto_struck_count": 0, "gold": 0,
            }

        return run

    def test_do_it_runs_the_first_suggestion_as_a_direction(self):
        coach_data = self.ask("What would you do to make this better?")
        first = coach_data["entry"]["suggestions"][0]
        captured = []
        with mock.patch.object(
            webapp, "_run_direction_pass",
            side_effect=self._capturing_pass(
                captured, RETAKE_SPAN[0], RETAKE_SPAN[1]
            ),
        ):
            data = self.ask("do it")
        self.assertEqual(data["kind"], "direction")
        self.assertEqual(data["coach_suggestion_id"], first["id"])
        self.assertEqual(captured, [first["direction"]])
        self.assertIn(REPEATED_LINE, captured[0])
        # The direction really landed on the suggestion's words.
        self.assertEqual(self.word_status(RETAKE_SPAN[0]), "cut")
        self.assertEqual(self.word_status(RETAKE_SPAN[1]), "kept")

    def test_do_the_second_one_maps_to_the_second_suggestion(self):
        coach_data = self.ask("What would you do to make this better?")
        second = coach_data["entry"]["suggestions"][1]
        self.assertEqual(second["kind"], "pause")
        captured = []
        with mock.patch.object(
            webapp, "_run_direction_pass",
            side_effect=self._capturing_pass(captured, 0, 2),
        ):
            data = self.ask("do the second one")
        self.assertEqual(data["kind"], "direction")
        self.assertEqual(data["coach_suggestion_id"], second["id"])
        self.assertEqual(captured, [second["direction"]])
        self.assertIn("pause", captured[0])

    def test_do_the_first_one_after_a_specific_question(self):
        data = self.ask("How can I make the opening stronger?")
        first = data["entry"]["suggestions"][0]
        self.assertEqual(first["kind"], "opening")
        captured = []
        with mock.patch.object(
            webapp, "_run_direction_pass",
            side_effect=self._capturing_pass(captured, 0, 2),
        ):
            follow = self.ask("do the first one")
        self.assertEqual(follow["coach_suggestion_id"], first["id"])
        self.assertEqual(captured, [first["direction"]])

    def test_a_direction_is_not_swallowed_by_coach(self):
        captured = []
        with mock.patch.object(
            webapp, "_run_direction_pass",
            side_effect=self._capturing_pass(captured, 0, 2),
        ):
            data = self.ask("Cut the first ten seconds.")
        # A plain direction enters the direction path with its
        # own words, never the coach path.
        self.assertEqual(data["kind"], "direction")
        self.assertEqual(captured, ["Cut the first ten seconds."])
        self.assertNotIn("coach_suggestion_id", data)


class RecordingTipsTests(CoachServerCase):
    def test_recording_tips_appear_marked_when_asked(self):
        data = self.ask("How can I record better next time?")
        self.assertEqual(data["kind"], "coach")
        entry = data["entry"]
        marked = [
            ln for ln in entry["lines"] if ln.startswith("For next time:")
        ]
        self.assertTrue(marked, entry["lines"])
        joined = " ".join(marked)
        # The tips are grounded in this video, not generic: the
        # repeated line and the long pause both teach one.
        self.assertIn(REPEATED_LINE, joined)
        self.assertIn("pause", joined.lower())


class EntryPointTests(CoachServerCase):
    def test_a_finished_edit_posts_no_recap_bubble(self):
        # 0.26, fix 3: the coach offer rode the long recap
        # bubble, and the recap bubble is removed. A finished
        # front door edit now posts no "run" entry (and so no
        # coach offer) to the thread; the Done bubble is the
        # run's only summary in the chat. The coach is still
        # there when he asks, through the coach answers.
        status, result = self.post_json(
            "/api/front-door",
            {
                "remove_retakes": False,
                "cut_silence": False,
                "remove_fillers": False,
                "provider": "mock",
            },
        )
        self.assertEqual(status, 200, result)
        status, view = self.get_json("/api/thread")
        self.assertEqual(status, 200)
        runs = [e for e in view["entries"] if e["kind"] == "run"]
        self.assertEqual(len(runs), 0)

    def test_the_template_renders_the_tappable_offer(self):
        self.assertIn("thread-coach-offer", PAGE)
        self.assertIn(
            'offer.textContent = "What would you do to make this better?"',
            PAGE,
        )
        self.assertIn("entry.coach_offer", PAGE)
        self.assertIn("thread-coach-do", PAGE)
        self.assertIn("data-coach-id", PAGE)


if __name__ == "__main__":
    unittest.main()
