"""v0.15 Batch G: direct editing — keyboard, gap erase, word sync.

Items 23, 24, 32 from Justin's Oct 30 hand-test:

- 23: with a pick active, Cmd/Ctrl+Backspace strikes the picked
  words and plain Backspace/Delete removes the whole line the
  pick is in, both through the existing /api/word-range path;
  the SHORTCUTS table (the printed list's single source) and
  the selection menu's Cut hint name the new mapping.
- 24: double-clicking a word gap picks the gap, snapped to the
  previous word's end and the next word's start; erasing it
  runs the existing pause path (one set_join_pause operation
  through the verifier, the applier, and a history checkpoint)
  and shortens the edited time. A gap inside one kept range
  has no pause entry in the plan, so the erase says so plainly
  and changes nothing.
- 32: a word click seeks on the edited clock (the word's own
  edited start, converted through editedToFileTime), sets the
  readout and band cursor in the same frame, and lands with
  syncTranscriptToPlayhead; the source/edited conversions walk
  across the silence before a word instead of pinning at the
  previous word's edited end (the start of the gap).

Everything is offline; no provider is called.
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

from talk_to_your_editor import checkpoints, webapp
from talk_to_your_editor.timeline import Timeline
from talk_to_your_editor.webapp import WebAppConfig

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")


def between(start_marker: str, end_marker: str) -> str:
    start = TEMPLATE.index(start_marker)
    end = TEMPLATE.index(end_marker, start)
    return TEMPLATE[start:end]


# ---------------------------------------------------------------------------
# Item 23: the keyboard mapping
# ---------------------------------------------------------------------------


class KeyboardMappingTests(unittest.TestCase):
    def test_shortcuts_table_names_justins_mapping(self):
        table = between("var SHORTCUTS = [", "];")
        self.assertIn(
            '{ keys: "Cmd or Ctrl + Backspace", action: '
            '"Strike the picked words" }',
            table,
        )
        self.assertIn(
            '{ keys: "Backspace", action: '
            '"Remove the whole line the pick is in" }',
            table,
        )
        # The old row (plain Backspace cuts the pick) is gone.
        self.assertNotIn("Cut the picked words", table)

    def test_menu_cut_hint_matches_the_table(self):
        menu = between('id="selMenuCut"', 'id="selMenuComment"')
        self.assertIn('<span class="sel-mi-key">Cmd+Backspace</span>', menu)

    def test_handler_strikes_the_pick_on_the_modifier_chord(self):
        handler = between(
            "// Keyboard editing (v0.15 Batch G, item 23",
            "// Cmd+Z / Ctrl+Z undoes",
        )
        # The chord branch runs the same range cut the bar runs.
        chord = handler[handler.index("if (mod) {"):]
        self.assertIn('e.key !== "Backspace"', chord)
        self.assertIn('runSelectionToggle("cut");', chord)

    def test_handler_removes_the_whole_line_on_plain_keys(self):
        handler = between(
            "// Keyboard editing (v0.15 Batch G, item 23",
            "// Cmd+Z / Ctrl+Z undoes",
        )
        self.assertIn("function lineSpanForPick()", TEMPLATE)
        line_fn = between(
            "function lineSpanForPick()", "document.addEventListener(\"keydown\""
        )
        # The line is the paragraph holding the pick's first word.
        self.assertIn("picked[0].paragraph", line_fn)
        plain = handler[handler.index("Plain Backspace / Delete"):]
        self.assertIn("selStart = line.start;", plain)
        self.assertIn("selEnd = line.end;", plain)
        self.assertIn('runSelectionToggle("cut");', plain)

    def test_handler_guards(self):
        handler = between(
            "// Keyboard editing (v0.15 Batch G, item 23",
            "// Cmd+Z / Ctrl+Z undoes",
        )
        self.assertIn("typingInField(e.target)", handler)
        self.assertIn("popupOwnsKeys()", handler)
        self.assertIn("if (fixMode || e.altKey) { return; }", handler)
        # With no pick both keys do nothing.
        self.assertIn(
            "if (selStart === null || selEnd === null) { return; }",
            handler,
        )
        # The popup guard covers the page pop-ups and the card.
        guard = between("function popupOwnsKeys()", "function lineSpanForPick()")
        self.assertIn('classList.contains("popup-open")', guard)
        self.assertIn("editPopup", guard)

    def test_the_old_backspace_handler_is_gone(self):
        self.assertNotIn(
            "Backspace / Delete cuts the pick, the way Descript",
            TEMPLATE,
        )
        self.assertNotIn(
            'if (fixMode || e.metaKey || e.ctrlKey || e.altKey) { return; }',
            TEMPLATE,
        )


# ---------------------------------------------------------------------------
# Item 24: the gap surface in the template
# ---------------------------------------------------------------------------


class GapSurfaceTests(unittest.TestCase):
    def test_dots_carry_the_gaps_real_boundaries(self):
        render = between("var pauseSeconds = pauseGapSeconds", "var entryEnd")
        self.assertIn("pauseEl.dataset.afterIndex = w.index;", render)
        self.assertIn("pauseEl.dataset.beforeIndex = nextEntry.index;", render)
        self.assertIn("pauseEl.dataset.gapStart = w.end;", render)
        self.assertIn("pauseEl.dataset.gapEnd = nextEntry.start;", render)
        self.assertIn("tpause-picked", render)

    def test_double_click_on_dots_picks_the_gap(self):
        handler = between(
            "Double-clicking a gap's dots picks the gap",
            "// Keyboard editing (v0.15 Batch G, item 23",
        )
        self.assertIn('e.target.closest(".tpause")', handler)
        self.assertIn("selectGap(after, before);", handler)

    def test_select_gap_snaps_to_word_end_and_next_start(self):
        fn = between("function selectGap(", "function positionGapBar()")
        self.assertIn("gapStart: prev.end", fn)
        self.assertIn("gapEnd: next.start", fn)
        # A gap pick replaces a word pick.
        self.assertIn("selStart = null;", fn)

    def test_gap_bar_has_an_erase_action(self):
        self.assertIn('id="gapBar"', TEMPLATE)
        self.assertIn('id="gapEraseBtn"', TEMPLATE)
        self.assertIn('id="gapLabel"', TEMPLATE)
        wiring = between(
            'if (gapEraseBtn) {',
            "// Double-clicking a gap's dots picks the gap",
        )
        self.assertIn("runGapErase();", wiring)

    def test_the_bar_owns_the_picked_look(self):
        bar = between("function updateGapBar()", "function runGapErase()")
        self.assertIn('querySelectorAll(".tpause-picked")', bar)
        self.assertIn('classList.add("tpause-picked")', bar)

    def test_gap_erase_posts_the_one_pause_path(self):
        fn = between(
            "function runGapErase()",
            "// Double-clicking a gap's dots picks the gap",
        )
        self.assertIn('postJson("/api/gap-erase", {', fn)
        self.assertIn("after_word_index: gap.afterIndex", fn)
        self.assertIn("before_word_index: gap.beforeIndex", fn)
        self.assertIn("reloadJobState();", fn)
        self.assertIn("refreshHistory();", fn)

    def test_gap_pick_is_dismissed_by_the_one_owner(self):
        owner = between(
            "document.addEventListener(\"pointerdown\", function (e) {",
            "// Escape, the same owner",
        )
        self.assertIn("pickedGap", owner)
        self.assertIn("gapBar && gapBar.contains(t)", owner)
        esc = between(
            "// Escape, the same owner",
            "// The 0.12 one-tap restore on a struck word is retired",
        )
        self.assertIn("pickedGap", esc)
        clear = between("function clearSelection()", "// Comment on the pick")
        self.assertIn("pickedGap = null;", clear)

    def test_backspace_erases_a_picked_gap(self):
        handler = between(
            "// Keyboard editing (v0.15 Batch G, item 23",
            "// Cmd+Z / Ctrl+Z undoes",
        )
        gap_branch = handler[handler.index("if (pickedGap) {"):]
        self.assertIn("runGapErase();", gap_branch)


# ---------------------------------------------------------------------------
# Item 24: the server gap erase
# ---------------------------------------------------------------------------

WORDS = [
    {"word": "one", "start": 0.0, "end": 0.5},
    {"word": "two", "start": 0.6, "end": 1.0},
    {"word": "three", "start": 2.0, "end": 2.5},
    {"word": "four", "start": 2.6, "end": 3.0},
    {"word": "five", "start": 5.0, "end": 5.5},
]

RANGES = [
    {"start": 0.0, "end": 1.1},
    {"start": 1.5, "end": 3.0},
    {"start": 5.0, "end": 5.5},
]


def write_gap_job(job: Path, profile: dict | None = None) -> None:
    job.mkdir(parents=True, exist_ok=True)
    transcript = {"engine": "test", "duration": 5.5, "words": WORDS}
    cuts = {
        "source_duration_s": 5.5,
        "output_duration_s": 3.1,
        "kept_ranges": [dict(r) for r in RANGES],
        "join_pauses_s": [0.6, 0.0],
    }
    (job / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8"
    )
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")
    if profile is not None:
        (job / "profile.json").write_text(
            json.dumps(profile), encoding="utf-8"
        )


class GapEraseServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v15g-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        self.job = self.tmp / "job"

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self) -> WebAppConfig:
        return WebAppConfig(job_dir=self.job)

    def revised(self) -> dict:
        return json.loads(
            (self.job / "tye-session" / "revised_cuts.json").read_text(
                encoding="utf-8"
            )
        )

    def test_a_join_gap_trims_through_the_pause_path(self):
        write_gap_job(self.job)
        result = webapp._apply_gap_erase(self.config(), 1, 2)
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["join_index"], 0)
        # Pause at the join: tail 0.1 + head 0.5 = 0.6 before,
        # tail 0.1 + the 0.08 floor after.
        self.assertAlmostEqual(result["gap_before_s"], 0.6, places=3)
        self.assertAlmostEqual(result["gap_after_s"], 0.18, places=3)
        self.assertAlmostEqual(result["output_duration_s"], 3.1 - 0.42, places=3)
        revised = self.revised()
        self.assertAlmostEqual(revised["kept_ranges"][1]["start"], 1.92, places=3)
        self.assertAlmostEqual(revised["join_pauses_s"][0], 0.08, places=3)

    def test_the_trim_is_one_undo_step(self):
        write_gap_job(self.job)
        config = self.config()
        webapp._apply_gap_erase(config, 1, 2)
        labels = [
            c["label"] for c in checkpoints.state(config)["checkpoints"]
        ]
        self.assertIn("Transcript gap shortened", labels)
        checkpoints.undo(config)
        # Undo returns the session to the pre-trim plan (the
        # job's own cuts, since no session plan existed before).
        effective = webapp._effective_cuts(config)
        self.assertAlmostEqual(
            effective["kept_ranges"][1]["start"], 1.5, places=3
        )

    def test_a_gap_beside_a_cut_word_still_finds_its_join(self):
        # A cut word can sit inside the quiet the creator
        # sees between two kept words; the gap beside it
        # still belongs to the join between those kept words.
        write_gap_job(self.job)
        words = [
            {"word": "one", "start": 0.0, "end": 0.5},
            {"word": "two", "start": 0.6, "end": 1.0},
            {"word": "um", "start": 1.2, "end": 1.4},
            {"word": "three", "start": 2.0, "end": 2.5},
            {"word": "four", "start": 2.6, "end": 3.0},
            {"word": "five", "start": 5.0, "end": 5.5},
        ]
        transcript = {"engine": "test", "duration": 5.5, "words": words}
        (self.job / "transcript.json").write_text(
            json.dumps(transcript), encoding="utf-8")
        # The gap between the cut word (index 2) and the
        # next kept word (index 3) resolves to join 0.
        result = webapp._apply_gap_erase(self.config(), 2, 3)
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertEqual(result["join_index"], 0)

    def test_a_gap_inside_one_range_says_so_and_changes_nothing(self):
        write_gap_job(self.job)
        result = webapp._apply_gap_erase(self.config(), 0, 1)
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertIn("inside one kept piece", result["message"])
        self.assertFalse(
            (self.job / "tye-session" / "revised_cuts.json").exists()
        )

    def test_a_gap_already_at_the_floor_changes_nothing(self):
        write_gap_job(self.job)
        result = webapp._apply_gap_erase(self.config(), 3, 4)
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertIn("already as short", result["message"])

    def test_a_protected_join_is_left_alone(self):
        write_gap_job(
            self.job,
            profile={
                "protected_spans": [
                    {"word_start": 2, "word_end": 3, "label": "keep this"}
                ]
            },
        )
        result = webapp._apply_gap_erase(self.config(), 1, 2)
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertIn("protected", result["message"])

    def test_a_gap_that_is_not_between_neighbours_is_refused(self):
        write_gap_job(self.job)
        with self.assertRaises(ValueError):
            webapp._apply_gap_erase(self.config(), 1, 3)
        with self.assertRaises(ValueError):
            webapp._apply_gap_erase(self.config(), 4, 5)


# ---------------------------------------------------------------------------
# Item 32: the word click and the conversions
# ---------------------------------------------------------------------------


class WordClickSyncTests(unittest.TestCase):
    def test_the_click_seeks_through_the_edited_clock(self):
        click = between(
            "// Clicking a word outside fix mode does one job only",
            'transcriptEl.addEventListener("dblclick"',
        )
        self.assertIn("editedToFileTime(wordEdited) : wordEdited", click)
        self.assertIn("var wordEdited = typeof entry.edited_start", click)
        # The readout is set by the click itself, in the same frame.
        self.assertIn("clockEl.textContent = transportReadout();", click)
        self.assertIn("updatePlayhead();", click)

    def test_the_click_lands_with_the_landing_sync(self):
        click = between(
            "// Clicking a word outside fix mode does one job only",
            'transcriptEl.addEventListener("dblclick"',
        )
        self.assertIn("syncTranscriptToPlayhead();", click)
        # The word path's own sync is the landing one: it is
        # the last sync the handler runs (the paragraph chip
        # branch above it keeps its own playback sync).
        self.assertGreater(
            click.rindex("syncTranscriptToPlayhead();"),
            click.rindex("syncTranscriptPlayback(true);"),
        )

    def test_source_to_edited_walks_across_the_gap(self):
        fn = between(
            "function editedTimeForSource(t)", "function editedToFileTime(et)"
        )
        self.assertIn("prev.edited_end", fn)
        self.assertIn("w.edited_start - prev.edited_end", fn)
        self.assertIn("gapFrac", fn)

    def test_edited_to_file_walks_across_the_gap(self):
        fn = between(
            "function editedToFileTime(et)", "function commentEditedTs(c)"
        )
        self.assertIn("prevKept.edited_end", fn)
        self.assertIn("prevKept.end + gapFrac * (w.start - prevKept.end)", fn)


if __name__ == "__main__":
    unittest.main()
