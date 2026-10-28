"""Tests for v0.10 item 5: teaching video intake (stdlib unittest).

Covers the plan's acceptance list:
(i)   a fixture recording with clear talking produces draft rules
      with reasons and timestamps, none saved without a yes;
(ii)  a recording with no usable talking ends in the exact plain
      message;
(iii) a recording plus a raw/finished pair attaches proof to the
      matching draft;
(iv)  a finished-video-only style drop drafts only pace, captions,
      and order drafts, each labeled as from the finished video;
(v)   a pair on its own drafts rules from the cuts, each labeled
      as coming from the cuts with no spoken reason;
(vi)  a pasted link: a direct file link lands as an input, and a
      page with no video ends in the exact could-not-get-it
      message (localhost servers only);
(vii) saving a teaching suggestion stores source "teaching" and
      tested False, and the tested flag flips under the test-video
      rule (and under the normal-job-plus-grade rule).

Transcription is stubbed at the teaching module's seam and the
provider is the mock (no sign-in key), so no test makes a live
call or spends anything.
"""

from __future__ import annotations

import http.server
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
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import creator_rules, teaching, webapp
from talk_to_your_editor.webapp import WebAppConfig, create_server

NO_SPEECH_MESSAGE = (
    "The app could not learn from that recording. It needs to "
    "hear you talking through your choices."
)
LINK_MESSAGE = (
    "The app could not get that video from the link. Drop the "
    "video file here instead."
)
PAIR_NOTE = "From your cuts. No spoken reason was attached to this one."
FINISHED_NOTE = "From watching the finished video only."


def words_of(sentences, start=0.0, step=0.5, width=0.4):
    """Timed word dicts from a list of sentence strings."""
    words = []
    t = start
    for sentence in sentences:
        for token in sentence.split():
            words.append({"word": token, "start": t, "end": t + width})
            t += step
    return words


def timed_words(tokens, times):
    return [
        {"word": token, "start": s, "end": e}
        for token, (s, e) in zip(tokens, times)
    ]


# (i) A recording with clear talking: three sentences state rules.
RECORDING_WORDS = words_of(
    [
        "Today I will show you how I edit this video for everyone.",
        "I always cut the first try of a line.",
        "The last take is the one that stays in the video.",
        "Never leave a long pause in the middle of a sentence.",
        "Um, I keep the prayer exactly as it was said before.",
    ]
)

# (iii) The pair the D2 calibration is built on: "severe, not bad,
# but" is cut in the finished edit.
RAW_SENTENCES = [
    "welcome back to the channel today we are talking about cold water",
    "increase the blood flow in the severe, not bad, but the water "
    "has health benefits for everyone who tries it every single day",
]
FINISHED_SENTENCES = [
    "welcome back to the channel today we are talking about cold water",
    "increase the blood flow in the water has health benefits for "
    "everyone who tries it every single day",
]
PROOF_RECORDING_WORDS = words_of(
    [
        "Let me show you the edit I made for this video today.",
        "I cut the words severe and bad because they sound harsh.",
    ]
)

# (v) A pair on its own: three fillers, all cut.
PAIR_RAW_WORDS = words_of(
    [
        "um this is the start um of the story and uh it goes on "
        "from here to the very end of the video today for sure",
    ]
)
PAIR_FINISHED_WORDS = [
    word
    for word in PAIR_RAW_WORDS
    if word["word"] not in ("um", "uh")
]


def style_finished_words():
    """Thirty words in chunks of five, the chunks split by pauses
    longer than 0.6 seconds; the last word ends at 34.5 seconds."""
    words = []
    t = 0.0
    for index in range(30):
        if index and index % 5 == 0:
            t += 0.9  # the gap before this chunk
        words.append({"word": f"word{index}", "start": t, "end": t + 1.0})
        t += 1.0
    return words


class TeachingServerCase(unittest.TestCase):
    """The real HTTP server on a temp job, with a temp home."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-teach-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(
            os.environ, {"HOME": str(self.home)}
        )
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        os.environ.pop("ELEVENLABS_API_KEY", None)
        self.job = self.tmp / "job"
        self.job.mkdir()
        transcript = {
            "engine": "test",
            "duration": 1.0,
            "words": [{"word": "hi", "start": 0.0, "end": 0.9}],
        }
        (self.job / "transcript.json").write_text(
            json.dumps(transcript), encoding="utf-8"
        )
        (self.job / "cuts.json").write_text(
            json.dumps(
                {
                    "source_duration_s": 1.0,
                    "kept_ranges": [{"start": 0.0, "end": 1.0}],
                }
            ),
            encoding="utf-8",
        )
        self.config = WebAppConfig(self.job)
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

    # -- helpers -----------------------------------------------------------

    def request(self, path, data=None, raw=None):
        url = self.base + path
        headers = {}
        payload = raw
        if data is not None:
            payload = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        method = "POST" if (data is not None or raw is not None) else "GET"
        req = urllib.request.Request(
            url, data=payload, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def post_json(self, path, data):
        status, body = self.request(path, data=data)
        return status, json.loads(body.decode("utf-8"))

    def teach_begin(self, kind="mine"):
        status, begun = self.post_json("/api/teach/begin", {"kind": kind})
        self.assertEqual(status, 200, begun)
        return begun["teach_id"]

    def teach_file(self, teach_id, role, name="video.mp4"):
        url = (
            "/api/teach/file?teach=" + quote(teach_id)
            + "&role=" + role + "&name=" + quote(name)
        )
        status, body = self.request(url, raw=b"\x00\x01\x02\x03" * 64)
        self.assertEqual(status, 200, body)

    def teach_run(self, teach_id):
        status, result = self.post_json(
            "/api/teach/run", {"teach_id": teach_id}
        )
        self.assertEqual(status, 200, result)
        return result

    def stub_transcribe(self, fake):
        patcher = mock.patch.object(teaching, "transcribe_media", fake)
        patcher.start()
        self.addCleanup(patcher.stop)


class RecordingDraftTests(TeachingServerCase):
    def test_recording_produces_drafts_none_saved(self):
        self.stub_transcribe(lambda path, key: list(RECORDING_WORDS))
        teach_id = self.teach_begin("mine")
        self.teach_file(teach_id, "recording", "recording.mp4")
        result = self.teach_run(teach_id)
        self.assertTrue(result["ok"])
        drafts = result["drafts"]
        self.assertEqual(len(drafts), 3)
        for draft in drafts:
            self.assertEqual(draft["basis"], "recording")
            self.assertEqual(draft["kind"], "mine")
            self.assertEqual(
                draft["reason"], "You said this while showing the edit."
            )
            self.assertIsNotNone(draft["at_s"])
            self.assertTrue(draft["quote"])
            self.assertTrue(draft["statement"])
            self.assertEqual(
                draft["pattern_key"], "teach:" + draft["draft_id"]
            )
            self.assertIsNone(draft["proof"])
        statements = [d["statement"] for d in drafts]
        self.assertIn("I always cut the first try of a line", statements)
        # The drafts surface as suggestions, marked as teaching and
        # untested, and nothing is a saved rule yet.
        teach_suggestions = [
            s
            for s in result["suggestions"]
            if s.get("source") == "teaching"
        ]
        self.assertEqual(len(teach_suggestions), 3)
        for suggestion in teach_suggestions:
            self.assertEqual(
                suggestion["source_note"],
                "From your teaching video. Not tested yet.",
            )
            self.assertEqual(suggestion["support_count"], 0)
            self.assertTrue(suggestion["explicit"])
            self.assertEqual(suggestion["rule_class"], "story")
        store = webapp._rules_store(self.config)
        self.assertEqual(store["rules"], [])
        on_disk = teaching.load_drafts(self.config.session_dir)
        self.assertEqual(len(on_disk), 3)

    def test_dismissed_teaching_draft_does_not_resurface(self):
        self.stub_transcribe(lambda path, key: list(RECORDING_WORDS))
        teach_id = self.teach_begin("mine")
        self.teach_file(teach_id, "recording", "recording.mp4")
        result = self.teach_run(teach_id)
        key = result["drafts"][0]["pattern_key"]
        status, dismissed = self.post_json(
            "/api/rule-dismiss", {"pattern_key": key}
        )
        self.assertEqual(status, 200, dismissed)
        remaining = [
            d["pattern_key"]
            for d in teaching.load_drafts(self.config.session_dir)
        ]
        self.assertNotIn(key, remaining)
        offered = {
            s["pattern_key"] for s in webapp._rule_suggestions(self.config)
        }
        self.assertNotIn(key, offered)

    def test_no_usable_talking_ends_in_the_plain_message(self):
        def no_speech(path, key):
            raise teaching.TeachingError("no_speech")

        self.stub_transcribe(no_speech)
        teach_id = self.teach_begin("mine")
        self.teach_file(teach_id, "recording", "recording.mp4")
        result = self.teach_run(teach_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["drafts"], [])
        self.assertEqual(result["message"], NO_SPEECH_MESSAGE)


class ProofTests(TeachingServerCase):
    def test_recording_plus_pair_attaches_proof(self):
        raw_words = words_of(RAW_SENTENCES)
        finished_words = words_of(FINISHED_SENTENCES)

        def fake(path, key):
            name = Path(path).name
            if name == "recording.mp4":
                return list(PROOF_RECORDING_WORDS)
            if name == "raw.mp4":
                return list(raw_words)
            if name == "finished.mp4":
                return list(finished_words)
            raise AssertionError(name)

        self.stub_transcribe(fake)
        teach_id = self.teach_begin("mine")
        self.teach_file(teach_id, "recording", "recording.mp4")
        self.teach_file(teach_id, "raw", "raw.mp4")
        self.teach_file(teach_id, "finished", "finished.mp4")
        result = self.teach_run(teach_id)
        drafts = result["drafts"]
        self.assertEqual(len(drafts), 1)
        draft = drafts[0]
        self.assertEqual(draft["basis"], "recording")
        self.assertIsNotNone(draft["proof"])
        self.assertIn('"severe"', draft["proof"])
        self.assertIn('"bad"', draft["proof"])
        suggestion = next(
            s
            for s in result["suggestions"]
            if s["pattern_key"] == draft["pattern_key"]
        )
        self.assertEqual(suggestion["proof"], draft["proof"])
        self.assertEqual(
            suggestion["source_note"], "From your teaching video."
        )


class PairOnlyTests(TeachingServerCase):
    def test_pair_only_drafts_come_from_the_cuts(self):
        def fake(path, key):
            name = Path(path).name
            if name == "raw.mp4":
                return list(PAIR_RAW_WORDS)
            if name == "finished.mp4":
                return list(PAIR_FINISHED_WORDS)
            raise AssertionError(name)

        self.stub_transcribe(fake)
        teach_id = self.teach_begin("mine")
        self.teach_file(teach_id, "raw", "raw.mp4")
        self.teach_file(teach_id, "finished", "finished.mp4")
        result = self.teach_run(teach_id)
        drafts = result["drafts"]
        self.assertEqual(len(drafts), 1)
        draft = drafts[0]
        self.assertEqual(draft["basis"], "pair")
        self.assertEqual(draft["source_note"], PAIR_NOTE)
        self.assertEqual(draft["quote"], "")
        self.assertIsNone(draft["at_s"])
        self.assertIn("filler", draft["statement"].lower())
        self.assertIn("3 times", draft["reason"])


class StyleFinishedOnlyTests(TeachingServerCase):
    def test_finished_only_style_drafts_are_surface_only(self):
        self.stub_transcribe(lambda path, key: style_finished_words())
        teach_id = self.teach_begin("style")
        self.teach_file(teach_id, "finished", "finished.mp4")
        result = self.teach_run(teach_id)
        drafts = result["drafts"]
        self.assertEqual(len(drafts), 3)
        for draft in drafts:
            self.assertEqual(draft["basis"], "finished_only")
            self.assertEqual(draft["kind"], "style")
            self.assertEqual(draft["source_note"], FINISHED_NOTE)
        statements = [d["statement"] for d in drafts]
        self.assertTrue(
            any("words a minute" in s for s in statements), statements
        )
        self.assertTrue(
            any(s.startswith("Captions run about") for s in statements),
            statements,
        )
        self.assertTrue(
            any("opens with" in s and "closes with" in s for s in statements),
            statements,
        )


# ---------------------------------------------------------------------------
# Link fetching, against a localhost server only
# ---------------------------------------------------------------------------

MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 8


class _LinkHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: A003 - stdlib name
        pass

    def do_GET(self):  # noqa: N802 - stdlib name
        if self.path == "/clip.mp4":
            body = MP4_BYTES
            ctype = "video/mp4"
        elif self.path == "/share":
            body = (
                '<html><head><meta property="og:video" '
                'content="/clip.mp4"></head><body>share</body></html>'
            ).encode("utf-8")
            ctype = "text/html"
        else:
            body = b"<html><body>no video here</body></html>"
            ctype = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LinkTests(TeachingServerCase):
    def setUp(self) -> None:
        super().setUp()
        self.link_server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _LinkHandler
        )
        self.link_port = self.link_server.server_address[1]
        self.link_thread = threading.Thread(
            target=self.link_server.serve_forever, daemon=True
        )
        self.link_thread.start()
        self.link_base = f"http://127.0.0.1:{self.link_port}"

    def tearDown(self) -> None:
        self.link_server.shutdown()
        self.link_server.server_close()
        super().tearDown()

    def test_direct_file_link_lands(self):
        with tempfile.TemporaryDirectory() as tmp:
            landed = teaching.fetch_link(
                self.link_base + "/clip.mp4", Path(tmp)
            )
            self.assertTrue(landed.is_file())
            self.assertEqual(landed.read_bytes(), MP4_BYTES)

    def test_share_page_link_lands(self):
        with tempfile.TemporaryDirectory() as tmp:
            landed = teaching.fetch_link(
                self.link_base + "/share", Path(tmp)
            )
            self.assertTrue(landed.is_file())
            self.assertEqual(landed.read_bytes(), MP4_BYTES)

    def test_page_without_video_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(teaching.TeachingLinkError):
                teaching.fetch_link(self.link_base + "/plain", Path(tmp))

    def test_link_endpoint_failure_is_the_plain_message(self):
        teach_id = self.teach_begin("mine")
        status, result = self.post_json(
            "/api/teach/link",
            {"teach_id": teach_id, "url": self.link_base + "/plain",
             "as": "finished"},
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertFalse(result["landed"])
        self.assertEqual(result["message"], LINK_MESSAGE)

    def test_link_endpoint_direct_file_lands(self):
        teach_id = self.teach_begin("style")
        status, result = self.post_json(
            "/api/teach/link",
            {"teach_id": teach_id, "url": self.link_base + "/clip.mp4",
             "as": "finished"},
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertTrue(result["landed"])
        record = self.server.teachings[teach_id]
        landed = Path(record["dir"]) / record["finished"]
        self.assertEqual(landed.read_bytes(), MP4_BYTES)


# ---------------------------------------------------------------------------
# Saving and the tested flag
# ---------------------------------------------------------------------------


class SaveAndTestedTests(TeachingServerCase):
    def seed_saved_teaching_rule(self):
        draft = teaching.make_draft(
            statement="Filler words like um and uh are cut out.",
            reason="In the pair you shared, filler words were cut 3 times.",
            at_s=None,
            quote="",
            basis="pair",
            kind="mine",
            source_note=PAIR_NOTE,
        )
        teaching.add_drafts(self.config.session_dir, [draft])
        suggestion = next(
            s
            for s in webapp._rule_suggestions(self.config)
            if s["pattern_key"] == draft["pattern_key"]
        )
        store = webapp._rules_store(self.config)
        rule = creator_rules.save_rule(store, suggestion)
        webapp._save_rules_store(self.config, store)
        return draft, suggestion, rule

    def test_save_stores_teaching_source_and_untested(self):
        draft, suggestion, rule = self.seed_saved_teaching_rule()
        self.assertEqual(suggestion["source"], "teaching")
        self.assertEqual(suggestion["source_note"], PAIR_NOTE)
        self.assertEqual(rule["source"], "teaching")
        self.assertIs(rule["tested"], False)
        self.assertEqual(rule["basis"], "pair")
        self.assertEqual(rule["kind"], "mine")
        view = webapp._saved_rules_view(self.config)
        self.assertEqual(view[0]["source"], "teaching")
        self.assertIs(view[0]["tested"], False)

    def test_tested_flips_on_a_test_video_run(self):
        self.seed_saved_teaching_rule()
        # No finished direction run yet: nothing flips.
        self.assertFalse(webapp._refresh_teaching_tested(self.config))
        webapp._write_json(
            self.config.session_dir / "direction_pass.json",
            {"ran": True},
        )
        webapp._write_json(
            self.config.session_dir / "test_video.json",
            {"test_video": True},
        )
        self.assertTrue(webapp._refresh_teaching_tested(self.config))
        store = webapp._rules_store(self.config)
        self.assertIs(store["rules"][0]["tested"], True)
        view = webapp._saved_rules_view(self.config)
        self.assertIs(view[0]["tested"], True)

    def test_tested_flips_on_a_normal_job_with_a_worked_grade(self):
        self.seed_saved_teaching_rule()
        webapp._write_json(
            self.config.session_dir / "direction_pass.json",
            {"ran": True},
        )
        # A normal job with no worked grade yet: nothing flips.
        self.assertFalse(webapp._refresh_teaching_tested(self.config))
        webapp._save_grades(
            self.config,
            [{"record_id": "g1", "verdict": "worked"}],
        )
        self.assertTrue(webapp._refresh_teaching_tested(self.config))
        store = webapp._rules_store(self.config)
        self.assertIs(store["rules"][0]["tested"], True)


# ---------------------------------------------------------------------------
# Unit-level pieces
# ---------------------------------------------------------------------------


class PairDiffUnitTests(unittest.TestCase):
    def test_pair_diff_counts_and_spans(self):
        raw = words_of(RAW_SENTENCES)
        finished = words_of(FINISHED_SENTENCES)
        diff = teaching.pair_diff(raw, finished)
        # The finished edit drops "severe, not bad, but" and the
        # extra "the" that stood before "water" only in the raw.
        self.assertEqual(diff["cut_count"], 5)
        self.assertEqual(diff["kept_count"], len(raw) - 5)
        self.assertEqual(len(diff["cut_spans"]), 1)
        start, end = diff["cut_spans"][0]
        cut_text = " ".join(w["word"] for w in raw[start:end])
        self.assertEqual(cut_text, "severe, not bad, but the")

    def test_drafts_from_pair_duplicate_lines_and_pauses(self):
        tokens = (
            "the quick brown fox jumps over the lazy dog near the "
            "river bank today plus more words to fill the line out"
        ).split()
        raw = []
        t = 0.0
        for token in tokens:
            raw.append({"word": token, "start": t, "end": t + 0.4})
            t += 0.5
        # The same ten words again, kept this time.
        for token in tokens[:10]:
            raw.append({"word": token, "start": t, "end": t + 0.4})
            t += 0.5
        # Three long pauses between kept words at the end.
        for token in ("after", "the", "long", "pauses", "end"):
            raw.append({"word": token, "start": t, "end": t + 0.4})
            t += 1.6
        finished = raw[10:]
        diff = teaching.pair_diff(raw, finished)
        drafts = teaching.drafts_from_pair(diff, raw)
        statements = [d["statement"] for d in drafts]
        self.assertIn(
            "Earlier tries at a line are cut; the last try stays.",
            statements,
        )
        self.assertIn(
            "Long pauses between words are taken out.", statements
        )
        for draft in drafts:
            self.assertEqual(draft["source_note"], PAIR_NOTE)
            self.assertEqual(draft["quote"], "")


class TranscribeMediaUnitTests(unittest.TestCase):
    def test_no_route_is_no_speech(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "recording.mp4"
            media.write_bytes(b"not really a video")
            with mock.patch.object(
                teaching.speech, "extract_audio", return_value=1.0
            ), mock.patch.object(
                teaching.speech, "local_available", return_value=False
            ):
                with self.assertRaises(teaching.TeachingError) as ctx:
                    teaching.transcribe_media(media, None)
            self.assertEqual(str(ctx.exception), "no_speech")

    def test_too_few_words_is_no_speech(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "recording.mp4"
            media.write_bytes(b"not really a video")
            few = {
                "words": [
                    {"word": "hi", "start": 0.0, "end": 0.4}
                    for _ in range(5)
                ]
            }
            with mock.patch.object(
                teaching.speech, "extract_audio", return_value=1.0
            ), mock.patch.object(
                teaching.speech, "local_available", return_value=True
            ), mock.patch.object(
                teaching.speech,
                "local_transcribe_wav",
                return_value=few,
            ):
                with self.assertRaises(teaching.TeachingError) as ctx:
                    teaching.transcribe_media(media, None)
            self.assertEqual(str(ctx.exception), "no_speech")


class TemplateTests(unittest.TestCase):
    def test_teach_card_copy_is_on_the_page(self):
        page = (
            ROOT
            / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
        ).read_text(encoding="utf-8")
        for text in (
            "Teach the app",
            "The fast way to teach the app how you edit. This is "
            "optional. The app also learns over time from your "
            "edits and your grades.",
            "Is this your own editing, or a style you like from "
            "someone else?",
            "My editing",
            "A style I like",
            "Screen recording of the editing, with talking",
            "The raw video, if you have it",
            "The finished edit, if you have it",
            "Or paste a link to a finished video",
            "Listening costs about 4 cents for every 10 minutes "
            "of recording, plus about 5 cents for the read.",
            "Teach from this",
            "Not tested yet",
        ):
            self.assertIn(text, page, text)


if __name__ == "__main__":
    unittest.main()
