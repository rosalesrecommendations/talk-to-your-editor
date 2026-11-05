"""v0.14 Batch E: Cut and Bring back answer the click
instantly (REPORT issue 3).

In 0.13 clicking Cut or Bring back in the picked-words bar
changed nothing on screen until the /api/word-range save
round-trip returned, about 1.5-2 seconds later in the
review's runs, with no sign in between that the click had
landed (the reviewer clicked twice the first time). Batch E
makes runSelectionToggle optimistic: in the same frame as
the click, before the save is posted, the picked words take
their new look (struck for Cut, un-struck for Bring back)
and the kept count moves with them, because both ride on
the same transcript entries. The existing save then
confirms behind it. If the save fails, either failure path
the handler already had (a not-ok reply, or a thrown post)
first puts the words back to their exact pre-click look and
only then reports the failure the way it always has.

What moves and what does not: Cut flips only kept words,
Bring back flips only cut words; a word already in the
target state and a special insert keep their look, the way
the save itself treats them. Nothing about which words the
actions move, what gets saved, the why question after a
Bring back, or the decision line changes.

The interaction itself is inline DOM code in the page, so
the measured on-screen proof (the look present while the
save response is held back) lives in the fit battery
(fit-test-014.mjs, [0.14E] section). What is pinned here is
the flip/revert logic, mirrored in Python over the page's
optimisticSelectionLook / revertSelectionLook in the style
of the v0.14 bar-anchor mirror, plus the source-level facts
that keep the mirror honest: the flip runs before the post,
both failure paths revert before they report, and the
success path never reverts.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "src" / "talk_to_your_editor" / "templates"
            / "test_app.html").read_text(encoding="utf-8")


def between(start, end):
    i = TEMPLATE.index(start)
    return TEMPLATE[i:TEMPLATE.index(end, i)]


# ---------------------------------------------------------------------------
# The flip/revert logic, mirrored over the page's helpers
# ---------------------------------------------------------------------------

class Entry:
    """One transcript entry in the mirror: a status, and the
    rendered span's class string the way renderTranscript
    builds it ("tw tw-" + status, plus the selected mark
    while the word is picked)."""

    def __init__(self, status):
        self.status = status
        self.span_class = "tw tw-" + status + " tw-selected"


def optimistic_flip(entries, target_status):
    """The page's optimisticSelectionLook: flip only the
    entries this action can move, remember each flip's old
    status and the span's exact old class, and return the
    flips. The kept count is derived from the entries, so
    it is read off them after the flip."""
    flipped = []
    for w in entries:
        frm = "kept" if target_status == "cut" else "cut"
        if w.status != frm:
            continue
        flipped.append({
            "entry": w,
            "status": w.status,
            "span_class": w.span_class,
        })
        w.status = target_status
        classes = w.span_class.split()
        if target_status == "cut":
            classes = [c for c in classes if c != "tw-kept"]
            classes.insert(1, "tw-cut")
        else:
            classes = [c for c in classes if c != "tw-cut"]
            classes.insert(1, "tw-kept")
        w.span_class = " ".join(classes)
    return flipped


def revert_flip(entries_flipped):
    """The page's revertSelectionLook."""
    for f in entries_flipped:
        f["entry"].status = f["status"]
        f["entry"].span_class = f["span_class"]


def kept_count(entries):
    return sum(1 for w in entries if w.status == "kept")


def struck(entry):
    return "tw-cut" in entry.span_class.split()


class OptimisticFlipMirrorTests(unittest.TestCase):
    """E1/E2 logic, at mirror level."""

    def test_cut_strikes_every_kept_pick_in_the_same_step(self):
        entries = [Entry("kept"), Entry("kept"), Entry("kept")]
        optimistic_flip(entries, "cut")
        self.assertTrue(all(struck(w) for w in entries))
        self.assertEqual(kept_count(entries), 0)

    def test_bring_back_unstrikes_every_cut_pick(self):
        entries = [Entry("cut"), Entry("cut")]
        optimistic_flip(entries, "kept")
        self.assertTrue(all(not struck(w) for w in entries))
        self.assertEqual(kept_count(entries), 2)

    def test_cut_moves_the_kept_count_with_the_look(self):
        entries = [Entry("kept"), Entry("kept"), Entry("cut")]
        self.assertEqual(kept_count(entries), 2)
        optimistic_flip(entries, "cut")
        self.assertEqual(kept_count(entries), 0)

    def test_bring_back_moves_the_kept_count_with_the_look(self):
        entries = [Entry("kept"), Entry("cut"), Entry("cut")]
        optimistic_flip(entries, "kept")
        self.assertEqual(kept_count(entries), 3)

    def test_cut_leaves_words_it_cannot_move_alone(self):
        # A word already cut and a special insert keep their
        # look and their status under a Cut, the way the
        # save itself treats them.
        entries = [Entry("cut"), Entry("special"), Entry("kept")]
        flipped = optimistic_flip(entries, "cut")
        self.assertEqual(len(flipped), 1)
        self.assertEqual(entries[0].status, "cut")
        self.assertEqual(entries[1].status, "special")
        self.assertFalse(struck(entries[1]))

    def test_bring_back_leaves_kept_and_special_words_alone(self):
        entries = [Entry("kept"), Entry("special"), Entry("cut")]
        flipped = optimistic_flip(entries, "kept")
        self.assertEqual(len(flipped), 1)
        self.assertEqual(entries[0].status, "kept")
        self.assertEqual(entries[1].status, "special")

    def test_revert_restores_the_exact_pre_click_look(self):
        entries = [Entry("kept"), Entry("cut"), Entry("kept")]
        before = [(w.status, w.span_class) for w in entries]
        flipped = optimistic_flip(entries, "cut")
        revert_flip(flipped)
        self.assertEqual(
            [(w.status, w.span_class) for w in entries], before)
        self.assertEqual(kept_count(entries), 2)

    def test_revert_after_bring_back_restores_the_struck_look(self):
        entries = [Entry("cut"), Entry("cut")]
        before = [(w.status, w.span_class) for w in entries]
        flipped = optimistic_flip(entries, "kept")
        self.assertTrue(all(not struck(w) for w in entries))
        revert_flip(flipped)
        self.assertEqual(
            [(w.status, w.span_class) for w in entries], before)
        self.assertTrue(all(struck(w) for w in entries))

    def test_a_flip_with_nothing_to_move_reverts_clean(self):
        entries = [Entry("cut")]
        flipped = optimistic_flip(entries, "cut")
        self.assertEqual(flipped, [])
        revert_flip(flipped)
        self.assertEqual(entries[0].status, "cut")


class OptimisticSourceTests(unittest.TestCase):
    """The page implements the mirrored flip, before the save."""

    def setUp(self):
        self.helper = between("function optimisticSelectionLook",
                              "function runSelectionToggle")
        # v0.15 Batch G: the slice ends at the keyboard-editing
        # block (the old "// Backspace / Delete" comment named
        # the pre-G handler it replaced).
        self.toggle = between("function runSelectionToggle",
                              "// Keyboard editing (v0.15 Batch G, item 23")

    def test_the_flip_reads_the_pick_and_guards_on_status(self):
        self.assertIn("selectedEntries().forEach", self.helper)
        self.assertIn(
            'var from = targetStatus === "cut" ? "kept" : "cut";',
            self.helper)
        self.assertIn("if (w.status !== from) { return; }", self.helper)

    def test_the_flip_applies_the_rendered_look_classes(self):
        # The struck look is the transcript's own cut class:
        # renderTranscript builds "tw tw-" + status and the
        # .tw-cut rule carries the strike.
        self.assertIn('"tw tw-" + w.status', TEMPLATE)
        self.assertIn(
            ".tw-cut { text-decoration: line-through;", TEMPLATE)
        self.assertIn(
            'span.classList.toggle("tw-cut", targetStatus === "cut");',
            self.helper)
        self.assertIn(
            'span.classList.toggle("tw-kept", targetStatus !== "cut");',
            self.helper)
        self.assertIn("w.status = targetStatus;", self.helper)

    def test_the_flip_finds_each_word_span_by_its_index(self):
        self.assertIn(
            "'.tw[data-index=\"' + w.index + '\"]'", self.helper)

    def test_the_count_moves_in_the_same_frame(self):
        self.assertIn("if (flipped.length) { updateTxCounts(); }",
                      self.helper)

    def test_each_flip_remembers_how_to_undo_itself(self):
        self.assertIn("spanClass: span ? span.className : null",
                      self.helper)
        revert = between("function revertSelectionLook",
                         "function runSelectionToggle")
        self.assertIn("f.entry.status = f.status;", revert)
        self.assertIn("f.span.className = f.spanClass;", revert)
        self.assertIn("updateTxCounts();", revert)

    def test_the_flip_runs_before_the_save_is_posted(self):
        flip_at = self.toggle.index(
            "var optimistic = optimisticSelectionLook(targetStatus);")
        post_at = self.toggle.index('postJson("/api/word-range"')
        self.assertLess(flip_at, post_at)

    def test_both_failure_paths_revert_before_reporting(self):
        self.assertEqual(
            self.toggle.count("revertSelectionLook(optimistic);"), 2)
        # The not-ok reply: revert, then the existing message.
        not_ok = between("if (!data.ok) {",
                         "var goldStayed = null;")
        self.assertLess(
            not_ok.index("revertSelectionLook(optimistic);"),
            not_ok.index("transcriptStatus.textContent"))
        self.assertIn(
            'data.error || "Those words could not be changed."', not_ok)
        # A thrown post: revert in the catch, same message.
        catch = self.toggle[self.toggle.index("}).catch(function () {"):]
        self.assertIn("revertSelectionLook(optimistic);", catch)
        self.assertIn(
            'transcriptStatus.textContent = '
            '"Those words could not be changed.";', catch)

    def test_the_success_path_never_reverts(self):
        success = self.toggle[
            self.toggle.index("var goldStayed = null;"):
            self.toggle.index("}).catch(function () {")]
        self.assertNotIn("revertSelectionLook", success)
        # And it keeps the flow it always had: clear the
        # pick, re-read the job, run the why prompt on a
        # Bring back, re-render from the server state.
        self.assertIn("clearSelection();", success)
        self.assertIn("reloadJobState();", success)
        self.assertIn("reloadTranscript();", success)
        self.assertIn('action === "restore" && data.why_prompt',
                      success)
        # v0.14 Batch G (G6): the show call carries the
        # sequence stamp the toggle took when the Bring back
        # began (beginWhyPrompt), so a prompt superseded by a
        # later answer or action cannot re-open the line.
        self.assertIn(
            'showWhyPrompt(data.why_prompt.entry_id, "restore", whySeq)',
            success)

    def test_the_bar_handlers_still_run_the_same_toggle(self):
        self.assertIn('runSelectionToggle("cut");', TEMPLATE)
        self.assertIn('runSelectionToggle("kept");', TEMPLATE)


if __name__ == "__main__":
    unittest.main()
