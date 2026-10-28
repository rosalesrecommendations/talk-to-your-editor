"""Tests for v0.9.0 Cluster 4: transcript editing and spans
(stdlib unittest, no dependencies).

Covers Justin's 2026-10-27 feedback items 8, 21, 28, 29, 30 and
FLAG D:

- Item 8 / FLAG D: Cmd+Z and Cmd+Shift+Z (and the Ctrl forms)
  undo and redo the last edit from anywhere on the page, through
  the same History checkpoints the buttons walk; typing in a
  text field keeps its native text undo; clicking or selecting
  struck words restores exactly those words.
- Item 21: held spans render light red and the visible copy reads
  "recommended to remove"; gold.json and the verdict mechanics do
  not change.
- Item 28: held spans narrow to the words that are actually
  repeated and split so each part settles separately, protected
  words are never inside a held span, and every held-span reason
  is one plain sentence.
- Item 29: the Follow control visibly shows its paused state and
  returns to the playhead in one tap; playback starting never
  silently turns following back on.
- Item 30: the selection strip labels its actions in plain words
  and shows the undo shortcut.

The page tests read the served template text; the editing tests
run the same backend functions the page's posts call.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import checkpoints, retakes, webapp
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
)


def page_html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def write_job(job: Path, words: list, profile: dict | None = None) -> None:
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
    if profile is not None:
        (job / "profile.json").write_text(
            json.dumps(profile), encoding="utf-8"
        )


def timed_words(tokens: list) -> list:
    return [
        {"word": token, "start": float(i), "end": float(i) + 0.9}
        for i, token in enumerate(tokens)
    ]


def word_status(config: WebAppConfig, index: int) -> str:
    transcript = webapp._load_json(str(config.job_dir / "transcript.json"))
    timeline = Timeline(transcript, webapp._effective_cuts(config))
    return timeline.word_status(index)


class IsolatedHomeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v09-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Item 8 / FLAG D: keyboard undo and redo, and exact restore
# ---------------------------------------------------------------------------


class KeyboardUndoPageTests(unittest.TestCase):
    def test_keyboard_undo_and_redo_are_wired_from_anywhere(self):
        html = page_html()
        start = html.index("Cmd+Z / Ctrl+Z undoes")
        handler = html[start:html.index("// -- playback sync", start)]
        # Mac and Windows forms, undo and redo.
        self.assertIn('key === "z" && !e.shiftKey', handler)
        self.assertIn('key === "z" && e.shiftKey', handler)
        self.assertIn('e.ctrlKey && key === "y"', handler)
        self.assertIn("e.metaKey || e.ctrlKey", handler)
        # The route is the History walk the buttons use.
        self.assertIn('moveHistory(isUndoKey ? "undo" : "redo")', handler)
        self.assertIn("e.preventDefault()", handler)
        # The History buttons themselves stay as they were.
        self.assertIn('id="undoBtn"', html)
        self.assertIn('id="redoBtn"', html)

    def test_no_app_undo_while_typing_in_the_note_box(self):
        html = page_html()
        start = html.index("Cmd+Z / Ctrl+Z undoes")
        handler = html[start:html.index("// -- playback sync", start)]
        # The typing guard runs before any history move, so a
        # field's native text undo (the note box included) wins.
        self.assertIn("typingInField(e.target)", handler)
        self.assertLess(
            handler.index("typingInField(e.target)"),
            handler.index("moveHistory("),
        )


class PhraseUndoRedoTests(unittest.TestCase):
    """The checkpoints the keyboard route walks, end to end."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v09-range-"))
        job = self.tmp / "job"
        job.mkdir(parents=True)
        for name, dest in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / name, job / dest)
        self.config = WebAppConfig(job)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_keyboard_undo_of_a_phrase_cut(self):
        result = webapp._apply_word_range(self.config, 16, 24, "cut")
        self.assertEqual(result["moved"], 8)
        for index in range(16, 24):
            self.assertEqual(word_status(self.config, index), "cut")
        state = checkpoints.undo(self.config)
        self.assertIsNotNone(state)
        for index in range(16, 24):
            self.assertEqual(word_status(self.config, index), "kept")

    def test_keyboard_redo_reapplies_a_phrase_cut(self):
        webapp._apply_word_range(self.config, 16, 24, "cut")
        checkpoints.undo(self.config)
        for index in range(16, 24):
            self.assertEqual(word_status(self.config, index), "kept")
        state = checkpoints.redo(self.config)
        self.assertIsNotNone(state)
        for index in range(16, 24):
            self.assertEqual(word_status(self.config, index), "cut")


class ExactRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v09-restore-"))
        job = self.tmp / "job"
        job.mkdir(parents=True)
        for name, dest in (
            ("sample_transcript.json", "transcript.json"),
            ("sample_cuts.json", "cuts.json"),
            ("sample_profile.json", "profile.json"),
        ):
            shutil.copyfile(FIXTURES / name, job / dest)
        self.config = WebAppConfig(job)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_restore_of_a_selected_struck_phrase_restores_exactly_the_selected_words(self):
        # Words 8..15 are one struck phrase in the sample. Picking
        # only "is hard to" (10..12) brings back exactly those
        # three words; the rest of the phrase stays cut.
        result = webapp._apply_word_range(self.config, 10, 13, "restore")
        self.assertEqual(result["moved"], 3)
        self.assertEqual(result["stayed"], [])
        for index in (10, 11, 12):
            self.assertEqual(word_status(self.config, index), "kept")
        for index in (8, 9, 13, 14, 15):
            self.assertEqual(word_status(self.config, index), "cut")

    def test_struck_word_click_picks_exactly_that_word(self):
        html = page_html()
        # The whole-struck-phrase expansion is gone from the click
        # path, and the click path says what it does instead.
        self.assertNotIn("while (loPos > 0)", html)
        self.assertNotIn("picks its whole struck phrase", html)
        self.assertIn("picks exactly the word that was clicked", html)


# ---------------------------------------------------------------------------
# Item 21: light red, "recommended to remove", gold internals unchanged
# ---------------------------------------------------------------------------


class HeldColorAndCopyTests(IsolatedHomeCase):
    def test_held_spans_render_light_red_not_gold(self):
        html = page_html()
        self.assertIn(
            ".tw-held { background: rgba(255, 120, 110, 0.22)", html
        )
        self.assertIn("border: 1.5px solid #ff8a80", html)
        self.assertIn("background: rgba(255, 120, 110, 0.16)", html)
        # The gold wash and gold marker colors are retired.
        self.assertNotIn("227, 179, 65", html)
        self.assertNotIn("#e3b341", html)

    def test_visible_copy_reads_recommended_to_remove(self):
        html = page_html()
        self.assertIn('"Recommended to remove"', html)
        self.assertIn(
            'marker.setAttribute("aria-label", "Recommended to remove: "',
            html,
        )
        self.assertIn("marked in light red as recommended to remove", html)
        self.assertNotIn("wait in gold", html)
        self.assertNotIn("in gold, before it counts", html)

    def seed_held(self, config, start=40, end=45):
        candidate = {
            "gold_id": webapp._gold_id(start, end, "negation"),
            "word_start": start,
            "word_end": end,
            "kind": "negation",
            "source": "direction",
            "reason": webapp._GOLD_REASON_NEGATION,
            "text": "some held words",
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
        webapp._record_gold_spans(config, [candidate])
        return candidate

    def test_recap_lines_say_recommended_to_remove(self):
        write_job(self.job, timed_words([f"word{i}" for i in range(80)]))
        config = WebAppConfig(self.job)
        self.seed_held(config)
        settings = dict(webapp.FRONT_DOOR_DEFAULTS)
        settings["mode"] = "tailor"
        lines = webapp._mode_and_gold_lines(config, settings)
        self.assertTrue(
            any("marked as recommended to remove" in line for line in lines)
        )
        self.assertFalse(any("gold" in line for line in lines))

    def test_gold_json_storage_and_verdict_mechanics_are_unchanged(self):
        write_job(self.job, timed_words([f"word{i}" for i in range(80)]))
        config = WebAppConfig(self.job)
        candidate = self.seed_held(config)
        # The internal store keeps its name and its record shape.
        self.assertEqual(checkpoints.GOLD_FILENAME, "gold.json")
        stored = json.loads(
            (config.session_dir / "gold.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(stored["spans"]), 1)
        self.assertEqual(stored["spans"][0]["gold_id"], candidate["gold_id"])
        self.assertEqual(stored["spans"][0]["status"], "open")
        # And a verdict still settles the span the way it always did.
        result = webapp._gold_verdict(
            config, candidate["gold_id"], "leave", ""
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertEqual(webapp._open_gold_spans(config), [])
        labels = [
            entry["label"]
            for entry in checkpoints.state(config)["checkpoints"]
        ]
        self.assertIn("Spot recommended to remove, left in", labels)


# ---------------------------------------------------------------------------
# Item 28: narrow, split held spans; one plain sentence of reason
# ---------------------------------------------------------------------------

# Justin's hook shape: a long opening line whose last clause is
# said again, word for word, a little later; the rest is said once.
HOOK_WORDS = (
    "has anyone ever told you a story that in one moment makes "
    "you think but then the next moment what if it is and then "
    "later he says what if it is clearly this time"
).split()


class NarrowingTests(IsolatedHomeCase):
    def direction_candidates(self, tokens, start, end, protected=()):
        entry = {
            "status": "flagged",
            "type": "cut_words",
            "comment_id": "direction-pass",
            "word_start": start,
            "word_end": end,
            "reason": "This repeats what was already said.",
            "confidence": 0.9,
            "model": "muse-spark-1.3",
            "risk_tier": 2,
            "wonder_reviewed": True,
        }
        verdict = {"reasons": ["wonder_review_required"]}
        return webapp._gold_candidates_from_direction(
            [entry], [verdict], timed_words(tokens),
            protected_ranges=protected,
        )

    def test_direction_span_narrows_to_the_repeated_clause(self):
        # One hold over the whole opening line (0..23): only
        # "what if it is" (19..23) is said again later, so only
        # that clause is held; the rest is never offered.
        candidates = self.direction_candidates(HOOK_WORDS, 0, 23)
        spans = [(c["word_start"], c["word_end"]) for c in candidates]
        self.assertEqual(spans, [(19, 23)])
        self.assertEqual(candidates[0]["text"], "what if it is")
        # The stored operation narrowed with its span.
        self.assertEqual(candidates[0]["operation"]["word_start"], 19)
        self.assertEqual(candidates[0]["operation"]["word_end"], 23)

    def test_unique_span_sharing_only_short_fragments_stays_whole(self):
        # A held span that is NOT a repeat shares short fragments
        # ("i am not", "because the", "is not") with another
        # sentence. Those fragments are not the repeated words:
        # the span stands whole, as one call.
        tokens = (
            "i am not giving up on this because the story is "
            "not over"
        ).split()
        tokens += [f"filler{i}" for i in range(10)]
        tokens += (
            "i am not afraid of the winter because the cold is "
            "not forever"
        ).split()
        candidates = self.direction_candidates(tokens, 23, 36)
        spans = [(c["word_start"], c["word_end"]) for c in candidates]
        self.assertEqual(spans, [(23, 36)])

    def test_retake_span_splits_around_words_the_keeper_does_not_repeat(self):
        tokens = (
            "um well the quick brown fox jumps hmm again today "
            "filler words here now the quick brown fox jumps over "
            "again today"
        ).split()
        words = timed_words(tokens)
        pair = {
            "pair_id": "retake-0-10-14-22",
            "outcome": "held",
            "band": "a",
            "cut": {"word_start": 0, "word_end": 10,
                    "text": " ".join(tokens[0:10])},
            "keep": {"word_start": 14, "word_end": 22,
                     "text": " ".join(tokens[14:22])},
        }
        candidates = webapp._gold_candidates_from_retakes([pair], words)
        spans = [(c["word_start"], c["word_end"]) for c in candidates]
        # "the quick brown fox jumps" and "again today" repeat;
        # "um well" and "hmm" do not, and are never held.
        self.assertEqual(spans, [(2, 7), (8, 10)])
        self.assertEqual(candidates[0]["text"], "the quick brown fox jumps")
        self.assertEqual(candidates[1]["text"], "again today")

    def test_held_span_narrows_around_protected_words(self):
        tokens = [f"w{i}" for i in range(20)]
        words = timed_words(tokens)
        pieces = webapp._narrow_held_span(
            words, 0, 10, list(range(10, 20)),
            protected_ranges=[(4, 6)],
        )
        # The repeat is whole, but words 4..5 are protected: the
        # protection wins and the span narrows around them.
        self.assertEqual(pieces, [(0, 4), (6, 10)])

    def test_split_parts_settle_separately(self):
        tokens = (
            "alpha bravo charlie delta unique echo foxtrot golf "
            "hotel india juliet kilo lima mike november alpha "
            "bravo charlie delta oscar papa quebec romeo echo "
            "foxtrot golf hotel tango"
        ).split()
        write_job(self.job, timed_words(tokens))
        config = WebAppConfig(self.job)
        candidates = self.direction_candidates(tokens, 0, 15)
        spans = [(c["word_start"], c["word_end"]) for c in candidates]
        # Two repeated parts inside one wide hold, with words
        # that are said once between and around them.
        self.assertEqual(spans, [(0, 4), (5, 9)])
        webapp._record_gold_spans(config, candidates)
        first = candidates[0]
        result = webapp._gold_verdict(
            config, first["gold_id"], "cut", ""
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        for index in range(first["word_start"], first["word_end"]):
            self.assertEqual(word_status(config, index), "cut")
        # The other part is untouched and still waiting for its
        # own call.
        remaining = webapp._open_gold_spans(config)
        self.assertEqual(len(remaining), len(candidates) - 1)
        for view in remaining:
            for index in range(view["word_start"], view["word_end"]):
                self.assertEqual(word_status(config, index), "kept")


class HeldReasonTests(unittest.TestCase):
    def reasons(self):
        return [
            webapp._GOLD_REASON_NEGATION,
            webapp._GOLD_REASON_HELD,
            webapp._GOLD_REASON_RETAKE_HELD,
            retakes._FLAG_NEGATION_REASON,
            retakes._FLAG_DOUBLE_REASON,
            retakes.band_b_flag_reason({}),
            retakes.band_b_flag_reason({"coverage": 0.7}),
        ]

    def test_held_reasons_are_one_plain_sentence_each(self):
        for reason in self.reasons():
            self.assertTrue(reason.endswith("."), reason)
            self.assertEqual(reason.count("."), 1, reason)
            self.assertNotIn("?", reason)
            self.assertNotIn("match closely", reason)
            self.assertNotIn("A person decides", reason)

    def test_negation_reason_still_names_the_negative_statement(self):
        self.assertIn("negative statement", webapp._GOLD_REASON_NEGATION)
        self.assertIn("'not'", retakes._FLAG_NEGATION_REASON)


# ---------------------------------------------------------------------------
# Item 29: follow behavior is predictable and visible
# ---------------------------------------------------------------------------


class FollowTests(unittest.TestCase):
    def test_follow_control_shows_its_paused_state(self):
        html = page_html()
        self.assertIn('id="txFollow"', html)
        self.assertIn('"Follow the video: on"', html)
        self.assertIn(
            '"Follow the video: paused — tap to go back to the playhead"',
            html,
        )
        self.assertIn("follow-paused", html)
        # One tap on the control returns to the playhead.
        self.assertIn(
            'txFollowBtn.addEventListener("click", '
            'function () { setFollow(!followOn); });',
            html,
        )

    def test_playback_start_does_not_silently_resume_following(self):
        html = page_html()
        self.assertNotIn(
            'vid.addEventListener("play", '
            'function () { setFollow(true); });',
            html,
        )
        # Manual scrolling still pauses tracking, visibly.
        self.assertIn("if (followOn) { setFollow(false); }", html)


# ---------------------------------------------------------------------------
# Item 30: the selection strip explains itself
# ---------------------------------------------------------------------------


class SelectionStripTests(unittest.TestCase):
    def test_selection_strip_names_its_actions_and_the_undo_shortcut(self):
        html = page_html()
        strip = html[html.index('id="txActionStrip"'):
                     html.index('id="transcriptStatus"')]
        self.assertIn("Cut these words", strip)
        self.assertIn("Bring these words back", strip)
        self.assertIn('id="txUndoHint"', strip)
        self.assertIn("Cmd+Z to undo", strip)
        # The picked words say whether they are cut or in the edit.
        self.assertIn('" — these words are cut"', html)
        self.assertIn('" — these words are in the edit"', html)


if __name__ == "__main__":
    unittest.main()
