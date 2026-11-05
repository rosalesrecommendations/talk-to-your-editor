"""Tests for 0.28 Coach Phase 1: the YouTube-strategy lens.

Covers, from the local record alone ($0, no provider):

- The intro check: confirm the click in about 7 seconds, a
  fresh hook by about 30 seconds, specifics over vague claims,
  and the could-it-stand-alone-as-a-Short test.
- Alternative openings built from his own takes (the other take
  of the first line, the strongest line, the strongest
  question), each tap to try, capped at 3.
- The retention map: hook, early payoff, rising action and
  climax labeled from the plan, with a payoff that never lands
  or lands too early flagged.
- Titles and thumbnails for Finish: about 30 candidates from
  the video's own lines, the best 5 shown with glance reads,
  and every shown title deliverable by the video.
- The Short pick: a 30 to 60 second stretch of the edit.
- Predict before posting: the coach offers a score, his number
  saves with the video, and the Finish state shows it.
- The Done bubble's one line: the job summary carries the
  coach count, and a stored Done payload takes it too.
- Reject on a card: the suggestion leaves the list and the
  Done count follows.
- The About line, word for word, and never another name.
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

from talk_to_your_editor import coach, webapp  # noqa: E402
from talk_to_your_editor.timeline import Timeline  # noqa: E402
from talk_to_your_editor.webapp import WebAppConfig, create_server  # noqa: E402

STUDIO = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "studio.html"
).read_text(encoding="utf-8")


def make_words(sentences, gap_after=None):
    words = []
    t = 0.0
    for index, sentence in enumerate(sentences):
        for token in sentence.split():
            words.append(
                {"word": token, "start": round(t, 3),
                 "end": round(t + 0.35, 3)}
            )
            t += 0.45
        if gap_after and index in gap_after:
            t += gap_after[index]
    return words


def transcript_of(words):
    return {
        "engine": "test",
        "duration": float(words[-1]["end"]) if words else 0.0,
        "words": words,
    }


def cuts_of(words, keep_spans=None):
    """kept_ranges covering keep_spans (word spans), or all."""
    if keep_spans is None:
        keep_spans = [(0, len(words))]
    return {
        "source_duration_s": float(words[-1]["end"]),
        "kept_ranges": [
            {"start": words[s]["start"], "end": words[e - 1]["end"]}
            for s, e in keep_spans
        ],
    }


# A greeting opening with nothing specific in it.
GREETING = [
    "Welcome back to the channel my friends today is a good day.",
    "This part is hard to say out loud.",
    "This part is hard to say out loud.",
    "The water is cold and my breath comes fast at first.",
    "Why would God let this happen to me?",
    "I will not give up on this.",
    "Thanks for watching.",
]

# A numbered, specific opening in his series shape.
SPECIFIC = [
    "Today is day thirty of seeing if God will use cold water.",
    "Today's temperature is forty degrees Fahrenheit.",
    "The nodule measured 2.18 centimeters across at the last scan.",
    "Why would God let this happen to me?",
    "I will not give up on this, not today, not ever.",
    "The water is cold and my breath comes fast at first.",
    "God bless you friends and I will see you tomorrow.",
]

# The opening promises, nothing later pays it off.
FLAT = [
    "What if the water takes everything from me?",
    "The water is cold today and the light is gray.",
    "I sit here and I breathe and I wait a while.",
    "The tank is in the yard behind the house.",
    "Nothing much changes from one day to the next here.",
    "That is the whole report for today I guess.",
]

# The strongest moment lands early, then flat lines.
EARLY = [
    "Today I got in the water like every other day.",
    "Why would God let this happen to me?",
    "The water is cold today and the light is gray.",
    "I sit here and I breathe and I wait a while.",
    "The tank is in the yard behind the house.",
    "Nothing much changes from one day to the next here.",
    "That is the whole report for today I guess.",
]


def rich_sentences():
    """A long, varied video: questions, numbers, a day line."""
    lines = [
        "Today is day thirty of seeing if God will use cold water.",
        "Today's temperature is forty degrees Fahrenheit.",
    ]
    topics = [
        "Why would God let this happen to me?",
        "The nodule measured 2.18 centimeters across at the scan.",
        "I counted 214 breaths before the shaking stopped today.",
        "What if the cold is the very thing that heals me?",
        "My hands went numb after ninety seconds in the water.",
        "The doctor said the left side looks clear and calm.",
        "How do you keep going when nothing seems to change?",
        "I read the verse about the valley this morning slowly.",
        "Three friends told me this story changed their mornings.",
        "The water took my breath and gave it back stronger.",
    ]
    tails = [
        "before sunrise",
        "in the dark yard",
        "on the cold porch",
        "with frost outside",
        "while stars were out",
    ]
    for round_ in range(3):
        for topic in topics:
            lines.append(topic.rstrip(".?") + " " + tails[round_] + ".")
    lines.append("God bless you friends and I will see you tomorrow.")
    return lines


class IntroCheckTests(unittest.TestCase):
    def checks(self, sentences):
        words = make_words(sentences)
        transcript = transcript_of(words)
        view = coach.phase1_view(transcript, cuts_of(words))
        return {c["key"]: c for c in view["intro"]}, view

    def test_greeting_opening_fails_confirm_and_specifics(self):
        checks, _ = self.checks(GREETING)
        self.assertFalse(checks["confirm_click"]["ok"])
        self.assertFalse(checks["specifics"]["ok"])
        self.assertIn("greeting", checks["confirm_click"]["line"])

    def test_specific_opening_passes(self):
        checks, _ = self.checks(SPECIFIC)
        self.assertTrue(checks["confirm_click"]["ok"])
        self.assertTrue(checks["specifics"]["ok"])
        self.assertIn("thirty", checks["specifics"]["line"])

    def test_four_checks_always_present(self):
        checks, _ = self.checks(SPECIFIC)
        self.assertEqual(
            set(checks),
            {"confirm_click", "fresh_hook", "specifics", "short_test"},
        )


class RetentionMapTests(unittest.TestCase):
    def retention(self, sentences):
        words = make_words(sentences)
        view = coach.phase1_view(
            transcript_of(words), cuts_of(words)
        )
        return view["retention"]

    def test_labels_the_beats(self):
        retention = self.retention(SPECIFIC)
        self.assertIsNotNone(retention["hook"])
        self.assertIsNotNone(retention["climax"])
        self.assertIn("day thirty", retention["hook"]["quote"])

    def test_payoff_that_never_lands_is_flagged(self):
        retention = self.retention(FLAT)
        joined = " ".join(retention["flags"])
        self.assertIn("never lands", joined)

    def test_payoff_landing_early_is_flagged(self):
        retention = self.retention(EARLY)
        joined = " ".join(retention["flags"])
        self.assertIn("lands early", joined)


class AlternativeOpeningTests(unittest.TestCase):
    def test_the_other_take_of_the_first_line(self):
        sentences = [
            "This is the line I open with every single day.",
            "This is the line I open with every single day.",
            "The water is cold and my breath comes fast.",
            "Why would God let this happen to me?",
            "I will not give up on this.",
        ]
        words = make_words(sentences)
        # The first saying (words 0-10) is out; the second stands.
        cuts = cuts_of(words, keep_spans=[(10, len(words))])
        pairs = [{
            "cut": {"word_start": 0, "word_end": 10},
            "keep": {"word_start": 10, "word_end": 20},
        }]
        suggestions = coach.build_suggestions(
            transcript_of(words), cuts, recorded_retake_pairs=pairs
        )
        alts = [s for s in suggestions if s["kind"] == "opening_alt"]
        self.assertEqual(len(alts), 1)
        self.assertIn("other take", alts[0]["text"])
        self.assertIn("Nothing changes until you approve",
                      alts[0]["text"])
        self.assertTrue(alts[0]["your_call"])
        self.assertEqual(alts[0]["principle"], "Click and watch")

    def test_at_most_three_alternatives(self):
        words = make_words(rich_sentences())
        suggestions = coach.build_suggestions(
            transcript_of(words), cuts_of(words)
        )
        alts = [s for s in suggestions if s["kind"] == "opening_alt"]
        self.assertLessEqual(len(alts), 3)


class TitlesTests(unittest.TestCase):
    def setUp(self):
        self.words = make_words(rich_sentences())
        self.transcript = transcript_of(self.words)
        self.titles = coach.build_titles(
            self.transcript, cuts_of(self.words)
        )

    def test_about_thirty_candidates_best_five(self):
        self.assertGreaterEqual(self.titles["candidates"], 25)
        self.assertEqual(len(self.titles["best"]), 5)

    def test_every_shown_title_is_deliverable(self):
        said = {
            token.lower().strip(".,?")
            for token in " ".join(
                w["word"] for w in self.words
            ).split()
        }
        for item in self.titles["best"]:
            for token in item["title"].replace(":", " ").split():
                cleaned = token.lower().strip(".,?\"")
                if cleaned.isdigit():
                    continue  # the Day number, from the day line
                self.assertIn(cleaned, said, item["title"])

    def test_glance_reads_are_plain(self):
        for item in self.titles["best"]:
            self.assertEqual(item["chars"], len(item["title"]))
            self.assertIn("characters", item["glance"])

    def test_thumbnails_carry_few_words(self):
        self.assertTrue(self.titles["thumbnails"])
        for thumb in self.titles["thumbnails"]:
            self.assertLessEqual(len(thumb["text"].split()), 5)
        self.assertIn("series", self.titles["series_line"].lower())


class ShortPickTests(unittest.TestCase):
    def test_a_thirty_to_sixty_second_stretch(self):
        words = make_words(rich_sentences())
        pick = coach.short_pick(transcript_of(words), cuts_of(words))
        self.assertIsNotNone(pick)
        length = pick["output_end_s"] - pick["output_start_s"]
        self.assertGreaterEqual(length, 30.0)
        self.assertLessEqual(length, 60.0)
        self.assertIn("For a Short", pick["line"])


class ReplyShapeTests(unittest.TestCase):
    def test_every_suggestion_names_its_principle(self):
        words = make_words(GREETING)
        suggestions = coach.build_suggestions(
            transcript_of(words), cuts_of(words)
        )
        self.assertTrue(suggestions)
        for suggestion in suggestions:
            self.assertTrue(suggestion["principle"])
            self.assertIn("at_s", suggestion)

    def test_prediction_ask_rides_the_general_reply(self):
        words = make_words(SPECIFIC)
        reply = coach.coach_reply(
            transcript_of(words), cuts_of(words)
        )
        joined = "\n".join(reply["lines"])
        self.assertIn("Predict before you post", joined)
        self.assertTrue(1 <= reply["prediction_offer"] <= 10)
        saved = coach.coach_reply(
            transcript_of(words), cuts_of(words),
            prediction_saved=True,
        )
        self.assertNotIn(
            "Predict before you post", "\n".join(saved["lines"])
        )

    def test_rejected_ids_leave_the_list(self):
        words = make_words(GREETING)
        transcript = transcript_of(words)
        cuts = cuts_of(words)
        first = coach.build_suggestions(transcript, cuts)
        rejected = {first[0]["id"]}
        second = coach.build_suggestions(
            transcript, cuts, rejected_ids=rejected
        )
        self.assertNotIn(first[0]["id"], [s["id"] for s in second])

    def test_titles_focus(self):
        self.assertEqual(coach.coach_focus("titles?"), "titles")
        self.assertEqual(
            coach.coach_focus("what titles would you pick?"),
            "titles",
        )
        # A longer plain statement stays a direction.
        self.assertIsNone(
            coach.coach_focus("Cut the title card at the start please.")
        )

    def test_about_line_is_exact_and_names_no_endorsement(self):
        self.assertEqual(
            coach.ABOUT_LINE,
            "built on public YouTube strategy frameworks, including "
            "Paddy Galloway's core, casual, and new audience idea.",
        )


def write_job(job: Path, words: list) -> None:
    job.mkdir(parents=True, exist_ok=True)
    transcript = transcript_of(words)
    (job / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (job / "cuts.json").write_text(
        json.dumps(cuts_of(words)), encoding="utf-8"
    )


class CoachPhase1ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-coach-p1-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"
        write_job(self.job, make_words(GREETING))
        self.config = WebAppConfig(self.job, launch_home=self.tmp / "launch")
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


class DoneLineTests(CoachPhase1ServerCase):
    def test_job_summary_carries_the_coach_count(self):
        status, summary = self.get_json("/api/job")
        self.assertEqual(status, 200)
        self.assertGreater(summary["coach_ideas"]["count"], 0)

    def test_a_stored_done_payload_takes_the_coach_line(self):
        status, data = self.post_json(
            "/api/thread",
            {
                "id": "chatrun-test",
                "kind": "editor",
                "status": "settled",
                "text": "Done",
                "chat_done": {"took_s": 12, "cents": 0},
            },
        )
        self.assertEqual(status, 200, data)
        entry = data.get("entry") or {}
        payload = entry.get("chat_done") or {}
        self.assertGreater(payload.get("coach", {}).get("count", 0), 0)

    def test_template_renders_exactly_one_coach_line(self):
        # The Done bubble carries exactly one coach line, built
        # from the count; the About note in the thread is a
        # different string and must not grow a second Done line.
        self.assertEqual(STUDIO.count('"Coach: " + p.coach.count'), 1)
        self.assertNotIn("Coach: 2 ideas to make this stronger", STUDIO)


class SuggestionCardTests(CoachPhase1ServerCase):
    def test_opening_the_coach_gives_cards_with_principles(self):
        data = self.ask("What would you do to make this better?")
        self.assertEqual(data["kind"], "coach")
        entry = data["entry"]
        self.assertEqual(entry["coach_about"], coach.ABOUT_LINE)
        self.assertTrue(entry["suggestions"])
        for suggestion in entry["suggestions"]:
            self.assertTrue(suggestion["principle"])
            self.assertIn("your_call", suggestion)
        self.assertIn("renderCoachCards", STUDIO)
        self.assertIn('cardBtn("Approve"', STUDIO)
        self.assertIn('cardBtn("Reject"', STUDIO)
        self.assertIn('cardBtn("Reply"', STUDIO)

    def test_approving_an_opening_changes_nothing_until_then(self):
        before = webapp._effective_cuts(self.config)
        data = self.ask("What would you do to make this better?")
        suggestions = data["entry"]["suggestions"]
        opening = next(
            s for s in suggestions if s["kind"] in ("opening", "order")
        )
        # The coach list alone changed nothing: the coach never
        # cuts on its own.
        self.assertEqual(
            json.dumps(before, sort_keys=True),
            json.dumps(webapp._effective_cuts(self.config),
                       sort_keys=True),
        )
        # Approving hands the suggestion's own direction to the
        # normal direction path (which applies it like any typed
        # direction; with no key saved it defers the read, the
        # same honesty every classic suggestion gets).
        result = self.ask(f"do {opening['id']}")
        self.assertEqual(result["kind"], "direction")
        self.assertEqual(
            result.get("coach_suggestion_id"), opening["id"]
        )

    def test_reject_takes_it_off_the_list_and_the_done_count(self):
        data = self.ask("What would you do to make this better?")
        suggestions = data["entry"]["suggestions"]
        target = suggestions[0]
        before = webapp._coach_done_view(self.config)["count"]
        status, result = self.post_json(
            "/api/coach/reject", {"suggestion_id": target["id"]}
        )
        self.assertEqual(status, 200, result)
        after = webapp._coach_done_view(self.config)["count"]
        self.assertEqual(after, before - 1)
        again = self.ask("What would you do to make this better?")
        ids = [s["id"] for s in again["entry"]["suggestions"]]
        self.assertNotIn(target["id"], ids)


class TitlesAndPredictionTests(CoachPhase1ServerCase):
    def test_titles_question_answers_in_the_same_conversation(self):
        data = self.ask("titles?")
        self.assertEqual(data["kind"], "coach")
        self.assertEqual(data["focus"], "titles")
        self.assertEqual(len(data["titles"]["best"]), 5)

    def test_finish_state_carries_titles_and_the_offer(self):
        status, state = self.get_json("/api/finish/state")
        self.assertEqual(status, 200)
        self.assertEqual(len(state["titles"]["best"]), 5)
        self.assertIsNone(state["prediction"]["score"])
        self.assertIsNotNone(state["prediction"]["offer"])

    def test_prediction_saves_with_the_video(self):
        status, result = self.post_json(
            "/api/coach/predict", {"score": 8}
        )
        self.assertEqual(status, 200, result)
        saved = webapp._coach_prediction(self.config)
        self.assertEqual(saved["score"], 8)
        status, state = self.get_json("/api/finish/state")
        self.assertEqual(state["prediction"]["score"], 8)
        payload = webapp._project_payload(self.config)
        self.assertEqual(payload["coach_prediction"]["score"], 8)

    def test_a_bare_number_answers_the_prediction_ask(self):
        self.ask("What would you do to make this better?")
        data = self.ask("I say 7")
        self.assertIn("Saved your prediction: 7 out of 10",
                      data["entry"]["text"])
        self.assertEqual(
            webapp._coach_prediction(self.config)["score"], 7
        )

    def test_prediction_rejects_a_bad_score(self):
        status, _ = self.post_json("/api/coach/predict", {"score": 42})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
