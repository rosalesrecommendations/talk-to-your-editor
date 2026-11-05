"""0.26 fixes 3, 4, 5, from Justin's 0.25.0 Mac run.

Fix 3: the long recap bubble is removed; only the Done bubble
stays, and every remaining number about the edit reads the one
count (_edit_counts) computed once from the final plan, with
the sound-based Timeline as the only ruler.

Fix 4: the Done bubble shows one review link, "🙋 N spots to
review"; the per-join links that ran together are gone, and
the individual spots live inside the review.

Fix 5: an answer about a cut states only causes the editor's
own record supports. A span no record names is never explained
as a click in the transcript. "Why did you cut that?" with no
referent answers about the most recent cut in the record, or
asks which one he means. (0.27, fix 11 supersedes the
most-recent half of that: the answer now names the cut nearest
the playhead the page sends, and asks when no position
arrives; see test_fix11.)
"""

from __future__ import annotations

import inspect
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

from talk_to_your_editor import webapp
from talk_to_your_editor.webapp import WebAppConfig, create_server

VIDEO_BYTES = bytes(range(256)) * 32
STUDIO = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "studio.html"
).read_text(encoding="utf-8")
WEBAPP = (
    ROOT / "src" / "talk_to_your_editor" / "webapp.py"
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
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-fix345-"))
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

    def front_door(self):
        status, data = self.call("/api/front-door", {
            "provider": "mock",
            "remove_retakes": True,
            "cut_silence": False,
            "buffer_s": 0.1,
            "format_scope": "both",
            "direction_text": "",
        })
        assert status == 200 and data.get("ok"), data
        return data


class RecapBubbleGoneTests(unittest.TestCase):
    """Fix 3, server side: no recap entry, claims still filed,
    and the run's spoken lines read the one count."""

    def test_no_run_entry_and_claims_still_filed(self):
        with JobServer() as live:
            live.front_door()
            status, thread = live.call("/api/thread")
            self.assertEqual(status, 200, thread)
            runs = [e for e in thread["entries"] if e.get("kind") == "run"]
            self.assertEqual(runs, [])
            for entry in thread["entries"]:
                blob = (entry.get("text") or "") + "\n" + "\n".join(
                    entry.get("lines") or []
                )
                self.assertNotIn("Retakes:", blob)
                self.assertNotIn("detected repeat", blob)
            # The run's cuts are still filed as review claims.
            self.assertGreaterEqual(len(thread.get("review") or []), 1)

    def test_run_lines_read_the_one_count(self):
        with JobServer() as live:
            data = live.front_door()
            counts = webapp._edit_counts(live.config)
            # Filming-day P0-4 (restrictive by default): the
            # negation pair is a likely retake, so it is struck
            # with a bring-back spot like the plain repeat, not
            # left in as a your-call. 2 removed, 0 set aside.
            self.assertEqual(counts["retakes_removed"], 2)
            self.assertEqual(counts["retakes_set_aside"], 0)
            joined_lines = "\n".join(data.get("lines") or [])
            self.assertIn(
                f"Retakes: {counts['retakes_removed']} removed, "
                f"{counts['retakes_set_aside']} set aside",
                joined_lines,
            )
            mode = "\n".join(data.get("mode_lines") or [])
            struck = re.search(r"(\d+) detected repeat", mode)
            self.assertIsNotNone(struck, mode)
            self.assertEqual(
                int(struck.group(1)), counts["retakes_removed"]
            )
            waiting = re.search(r"(\d+) spots?[^.]*waiting for your call", mode)
            if counts["spots_to_review"]:
                self.assertIsNotNone(waiting, mode)
                self.assertEqual(
                    int(waiting.group(1)), counts["spots_to_review"]
                )
            else:
                # Nothing waits: every finding was struck with a
                # bring-back spot (P0-4), so no waiting line.
                self.assertIsNone(waiting, mode)

    def test_summary_helper_posts_nothing(self):
        source = inspect.getsource(webapp._append_run_summary_thread)
        self.assertNotIn("_append_thread(", source)
        self.assertIn("_record_review_claims(", source)


class SpanCauseTests(unittest.TestCase):
    """Fix 5, the explainer: causes come from the record only,
    on the one sound ruler."""

    def test_front_door_spans_have_record_causes(self):
        with JobServer() as live:
            live.front_door()
            spans = webapp._edit_spans(live.config)
            self.assertTrue(spans)
            for span in spans:
                self.assertNotIn("by clicking", span["reason"])
                self.assertNotEqual(span["source"], "transcript")
            dup = [
                s for s in spans
                if s["removed_text"] == "This part is hard to say out loud."
            ]
            self.assertEqual(len(dup), 1)
            self.assertIn("take", dup[0]["reason"])
            self.assertIsNotNone(dup[0]["retake"])

    def test_spans_use_the_sound_ruler_on_both_sides(self):
        # Word 2's stamp sits inside the job plan's ranges, but
        # its measured sound does not: on the stamp ruler the old
        # explainer invented a span for it. With the sound
        # bounds lent to the base plan too, only word 3 (whose
        # sound the base kept and the current plan cut) is a span.
        tmp = Path(tempfile.mkdtemp(prefix="tye-fix345-ruler-"))
        try:
            job = tmp / "job"
            job.mkdir()
            words = []
            for i, token in enumerate(("alpha", "bravo", "charlie", "delta")):
                words.append({
                    "word": token,
                    "start": round(i * 0.5, 3),
                    "end": round(i * 0.5 + 0.4, 3),
                })
            transcript = {"words": words, "duration": 2.0}
            (job / "transcript.json").write_text(
                json.dumps(transcript), encoding="utf-8"
            )
            (job / "cuts.json").write_text(json.dumps({
                "kept_ranges": [{"start": 0.0, "end": 2.0}],
                "source_duration_s": 2.0,
            }), encoding="utf-8")
            session = job / "tye-session"
            session.mkdir()
            (session / "revised_cuts.json").write_text(json.dumps({
                "kept_ranges": [{"start": 0.0, "end": 0.95}],
                "source_duration_s": 2.0,
                "word_sound_bounds": [
                    [0.0, 0.4], [0.5, 0.9], [3.0, 3.4], [1.5, 1.9],
                ],
            }), encoding="utf-8")
            config = WebAppConfig(job)
            spans = webapp._edit_spans(config)
            self.assertEqual(
                [(s["word_start"], s["word_end"]) for s in spans],
                [(3, 4)],
            )
            # And its cause is the honest fallback: no record
            # names a pass, and nothing blames a click.
            self.assertEqual(spans[0]["source"], "passes")
            self.assertNotIn("by clicking", spans[0]["reason"])
            self.assertIn("not a change you made by hand", spans[0]["reason"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class WhyAnswerTests(unittest.TestCase):
    """Fix 5, the chat answer: a named spot, or an honest ask.
    (Fix 11 replaced the most-recent-cut fallback with the cut
    nearest the playhead.) Never an invented cause, never a run."""

    def _ask(self, live, text):
        with mock.patch.object(
            webapp, "_run_direction_pass",
            side_effect=AssertionError("direction ran"),
        ):
            return live.call("/api/thread/message", {"text": text})

    def test_why_that_without_a_playhead_asks_which_cut(self):
        # 0.27, fix 11 supersedes the fix 5 fallback this test
        # used to pin: a bare "why did you cut that?" with no
        # playhead position no longer answers about the most
        # recent cut (in Justin's test that named a cut at the
        # last second of the video). It asks, listing real cuts.
        # The nearest-playhead answer is pinned in test_fix11.
        with JobServer() as live:
            live.front_door()
            status, data = self._ask(live, "Why did you cut that?")
            self.assertEqual(status, 200, data)
            self.assertEqual(data["kind"], "record")
            text = data["entry"]["text"]
            self.assertIn("cannot tell which cut", text)
            self.assertIn("hard to say out loud", text)
            self.assertNotIn("most recent cut", text)
            self.assertNotIn("by clicking", text)
            self.assertNotIn("You cut these words", text)

    def test_why_about_a_named_time_matches_the_recorded_pass(self):
        with JobServer() as live:
            live.front_door()
            spans = webapp._edit_spans(live.config)
            dup = [
                s for s in spans
                if s["removed_text"] == "This part is hard to say out loud."
            ][0]
            status, data = self._ask(live, "Why did you cut the words at 0:01?")
            self.assertEqual(status, 200, data)
            self.assertEqual(data["kind"], "record")
            text = data["entry"]["text"]
            self.assertIn("From the record", text)
            self.assertIn(dup["reason"], text)
            self.assertNotIn("by clicking", text)

    def test_why_that_asks_when_no_decision_is_recorded(self):
        tmp = Path(tempfile.mkdtemp(prefix="tye-fix345-ask-"))
        home = tmp / "home"
        home.mkdir()
        env = mock.patch.dict(os.environ, {"HOME": str(home)}, clear=False)
        env.start()
        server = None
        try:
            job = tmp / "job"
            job.mkdir()
            words = []
            for i, token in enumerate(("alpha", "bravo", "charlie", "delta")):
                words.append({
                    "word": token,
                    "start": round(i * 0.5, 3),
                    "end": round(i * 0.5 + 0.4, 3),
                })
            (job / "transcript.json").write_text(
                json.dumps({"words": words, "duration": 2.0}),
                encoding="utf-8",
            )
            (job / "cuts.json").write_text(json.dumps({
                "kept_ranges": [{"start": 0.0, "end": 2.0}],
                "source_duration_s": 2.0,
            }), encoding="utf-8")
            session = job / "tye-session"
            session.mkdir()
            (session / "revised_cuts.json").write_text(json.dumps({
                "kept_ranges": [{"start": 0.0, "end": 1.45}],
                "source_duration_s": 2.0,
            }), encoding="utf-8")
            (job / "review.mp4").write_bytes(VIDEO_BYTES)
            config = WebAppConfig(job)
            server = create_server(config, host="127.0.0.1", port=0)
            thread = threading.Thread(
                target=server.serve_forever, daemon=True
            )
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            payload = json.dumps(
                {"text": "Why did you cut that?"}
            ).encode()
            req = urllib.request.Request(
                base + "/api/thread/message",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with mock.patch.object(
                webapp, "_run_direction_pass",
                side_effect=AssertionError("direction ran"),
            ):
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode() or "{}")
            self.assertEqual(data["kind"], "record")
            text = data["entry"]["text"]
            self.assertIn("cannot tell which cut", text)
            self.assertIn("delta", text)
            self.assertNotIn("most recent cut", text)
            self.assertNotIn("by clicking", text)
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
            env.stop()
            shutil.rmtree(tmp, ignore_errors=True)


class DoneBubbleShapeTests(unittest.TestCase):
    """Fix 4, and the canned-line audit for fix 5(c)."""

    def test_no_per_join_links_in_the_done_bubble(self):
        self.assertNotIn(
            '"Join at " + fmtClock(spot.output_time_s || 0) + ": your call"',
            STUDIO,
        )

    def test_one_review_link_in_the_done_bubble(self):
        # Still exactly one review link in the Done bubble;
        # its words are panel 2's now (0.27.3, chunk B item
        # 6/7): "Review the spots", with the count carried by
        # the bold Your turn line above it.
        self.assertIn('"Review the spots"', STUDIO)
        self.assertIn('"Your turn: " + turnSpots', STUDIO)

    def test_the_clicking_lie_is_gone_everywhere(self):
        self.assertNotIn(
            "You cut these words by clicking in the transcript.", WEBAPP
        )
        self.assertNotIn(
            "You cut these words by clicking in the transcript.", STUDIO
        )


if __name__ == "__main__":
    unittest.main()
