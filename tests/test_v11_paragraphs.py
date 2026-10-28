"""Tests for v0.11.0 item 9: the transcript in Descript's
paragraph form (stdlib unittest, no dependencies).

From Justin's 2026-10-28 direction (FIX-ITEMS item 9) and his
Descript screenshot: the words read as flowing paragraphs of
prose; dead air shows as dots inline in the paragraph, exactly
where the silence is, a short pause a dot or two and a long
silence a long run of dots; cut words stay in place, struck
through and dimmed, so a whole cut take reads as a dimmed struck
passage in the flow. His standing additions stay: clicking a
word opens its reason, repeated takes are handled as takes, the
current word follows the video, and comments pin to words.

The dot scale under test (the one written in the template, in
the fenced pause module): no dots under 0.3 seconds; from
there, one dot for each full 0.3 seconds of pause (0.3 to 0.59
seconds shows 1 dot, 1.2 seconds shows 4, 3.0 seconds shows
10); capped at 30 dots (a pause of 9 seconds or more).

The fixture transcript has known word timings and known gaps
(about 0.1s, 0.45s, 1.2s, and 3.0s), known cut words (one whole
repeated take, words 5 to 9, is cut; the same line said again,
words 10 to 14, is kept), and paragraphs that break at the
1.2s and 3.0s gaps under the server's 0.8s rule.

The render tests run the page's own builder for real under
Node: the fenced pause module and the renderTranscript
function are lifted out of the served template text and run
against a small fake document, fed with the entries the
server's real transcript-building path
(webapp._transcript_entries) produced for the fixture job.
The server tests read the same entries directly. The page
tests read the template text, the same way
test_v09_transcript.py and test_v11_transcript.py do.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import webapp
from talk_to_your_editor.webapp import WebAppConfig

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
)

NODE = shutil.which("node")


def page_html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The fixture: known words, known timings, known gaps
# ---------------------------------------------------------------------------

# (text, start) with every word lasting 0.4 seconds. The gaps
# after each word: 0.1, 0.45, 1.2, 0.1, 3.0, then 0.1s steps,
# 0.4 after the cut take, 0.1s steps, 1.2, 0.1, 0.1.
FIXTURE_SPEC = [
    ("If", 0.00), ("you", 0.50), ("wanna", 1.35),
    ("believe", 2.95), ("that", 3.45),
    ("God", 6.85), ("can", 7.35), ("still", 7.85),
    ("do", 8.35), ("miracles.", 8.85),
    ("If", 9.65), ("you", 10.15), ("love", 10.65),
    ("God", 11.15), ("stories", 11.65),
    ("and", 13.25), ("wanna", 13.75), ("believe.", 14.25),
]
WORD_S = 0.4
CUT_WORDS = list(range(5, 10))          # the whole first take
KEPT_RANGES = [
    {"start": 0.0, "end": 6.9},
    {"start": 9.4, "end": 15.05},
]
# Paragraphs under the server's 0.8s break rule: the 1.2s gap
# before word 3, the 3.0s gap before word 5, and the 1.2s gap
# before word 15 each start a new paragraph.
EXPECTED_PARAGRAPHS = [
    [0, 1, 2], [3, 4], list(range(5, 15)), [15, 16, 17],
]
EXPECTED_PARA_INDEX = [0, 0, 0, 1, 1] + [2] * 10 + [3, 3, 3]
# (word the run follows, dot count): gaps of 0.45s, 1.2s, 3.0s,
# 0.4s (after the cut take), and 1.2s. Every 0.1s gap shows no
# run at all, and the last word has no run.
EXPECTED_RUNS = [(1, 1), (2, 4), (4, 10), (9, 1), (14, 4)]


def fixture_words() -> list:
    return [
        {"word": text, "start": start, "end": round(start + WORD_S, 2)}
        for text, start in FIXTURE_SPEC
    ]


def write_job(job: Path) -> None:
    job.mkdir(parents=True, exist_ok=True)
    words = fixture_words()
    transcript = {
        "engine": "test",
        "duration": float(words[-1]["end"]),
        "words": words,
    }
    cuts = {
        "source_duration_s": transcript["duration"],
        "kept_ranges": KEPT_RANGES,
    }
    (job / "transcript.json").write_text(
        json.dumps(transcript), encoding="utf-8")
    (job / "cuts.json").write_text(json.dumps(cuts), encoding="utf-8")


class IsolatedHomeCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v11-para-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()
        os.environ.pop("MODEL_API_KEY", None)
        self.job = self.tmp / "job"
        write_job(self.job)
        self.config = WebAppConfig(self.job)

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def entries(self) -> list:
        entries, _corrections = webapp._transcript_entries(self.config)
        return entries


# ---------------------------------------------------------------------------
# Server path: grouping, timings, and statuses the page renders from
# ---------------------------------------------------------------------------


class ServerEntriesTests(IsolatedHomeCase):
    def test_words_group_into_the_expected_paragraphs(self):
        words = fixture_words()
        self.assertEqual(
            webapp._paragraph_indices(words), EXPECTED_PARA_INDEX)
        entries = self.entries()
        grouped = {}
        for entry in entries:
            grouped.setdefault(entry["paragraph"], []).append(
                entry["index"])
        self.assertEqual(
            [grouped[k] for k in sorted(grouped)], EXPECTED_PARAGRAPHS)

    def test_entries_carry_the_original_timings_the_dots_measure(self):
        entries = self.entries()
        gaps = {}
        for prev, nxt in zip(entries, entries[1:]):
            gaps[prev["index"]] = round(nxt["start"] - prev["end"], 2)
        self.assertEqual(gaps[0], 0.1)
        self.assertEqual(gaps[1], 0.45)
        self.assertEqual(gaps[2], 1.2)
        self.assertEqual(gaps[4], 3.0)
        self.assertEqual(gaps[9], 0.4)
        self.assertEqual(gaps[14], 1.2)

    def test_the_cut_take_is_cut_and_the_keeper_is_kept(self):
        entries = self.entries()
        for entry in entries:
            want = "cut" if entry["index"] in CUT_WORDS else "kept"
            self.assertEqual(entry["status"], want, entry)


# ---------------------------------------------------------------------------
# Page text: the scale is written down, the dots are not words,
# and the standing hooks are still wired to word spans
# ---------------------------------------------------------------------------


class PageTextTests(unittest.TestCase):
    def setUp(self):
        self.html = page_html()

    def test_the_dot_scale_is_written_down_in_one_place(self):
        self.assertIn("[tye-pause-dots-begin]", self.html)
        self.assertIn("[tye-pause-dots-end]", self.html)
        block = self.html[
            self.html.index("[tye-pause-dots-begin]"):
            self.html.index("[tye-pause-dots-end]")]
        self.assertIn("no dots", block.replace("shows no dots", "no dots"))
        self.assertIn("one dot for each full 0.3 seconds", block)
        self.assertIn("capped at 30 dots", block)
        self.assertIn("var PAUSE_DOT_STEP_S = 0.3;", block)
        self.assertIn("var PAUSE_DOT_MAX = 30;", block)

    def test_pause_runs_are_plain_spans_with_no_word_data(self):
        start = self.html.index('pauseEl.className = "tpause"')
        chunk = self.html[max(0, start - 700):start + 700]
        # The builder sets a class, the dot text, and a hidden
        # flag for screen readers; it never sets word data, so
        # no word logic can pick a dot run up as a word.
        self.assertNotIn("pauseEl.dataset", chunk)
        self.assertIn('pauseEl.setAttribute("aria-hidden", "true")', chunk)
        self.assertIn("pauseEl.textContent = pauseDotText(pauseSeconds)",
                      chunk)
        # The dots are measured from the entry timings, between
        # this word and the next one, at paragraph breaks too
        # (the code runs before the next paragraph is started).
        self.assertIn("pauseGapSeconds(w, nextEntry)", chunk)
        render = self.html[
            self.html.index("function renderTranscript(words)"):]
        self.assertLess(
            render.index('pauseEl.className = "tpause"'),
            render.index("Object.keys(dotByLast)"),
        )

    def test_pause_dots_are_quiet_marks_in_the_css(self):
        body = self.html[
            self.html.index("#transcriptPanel .tpause {"):]
        body = body[:body.index("}")]
        self.assertIn("color: var(--faint)", body)
        self.assertIn("opacity: 0.75", body)
        # A long run wraps inside the paragraph instead of
        # pushing the line wider than the panel.
        self.assertIn("overflow-wrap: anywhere", body)
        self.assertNotIn("cursor: pointer", body)

    def test_cut_words_stay_struck_and_dimmed_in_place(self):
        self.assertIn(
            ".tw-cut { text-decoration: line-through; opacity: 0.38; }",
            self.html)
        self.assertIn('span.className = "tw tw-" + w.status', self.html)

    def test_standing_hooks_still_ride_on_the_word_spans(self):
        html = self.html
        # Clicking a word opens its reason: the edit dot keeps
        # its edit id and its popup wiring.
        self.assertIn('dot.className = "tdot"', html)
        self.assertIn("dot.dataset.editId = editSpan.edit_id", html)
        self.assertIn("openEditPopup(editSpan, anchor)", html)
        # Takes: the held marker and wash for a repeated take.
        self.assertIn('marker.className = "theld"', html)
        self.assertIn("openGoldPopup(g, anchor)", html)
        self.assertIn('(inGold(w) ? " tw-held" : "")', html)
        # Follow the video: word spans keep their index data,
        # which is what the follow code looks up, and the
        # paragraph takes the now class with its word.
        self.assertIn("span.dataset.index = w.index", html)
        self.assertIn("span.dataset.end", html)
        self.assertIn('classList.add("tx-para-now")', html)
        self.assertIn("tw-now", html)
        # Comments pin to words: the pinned words take the
        # commented class and the marker sits in the paragraph
        # margin with its comment id.
        self.assertIn('(isCommented ? " tw-commented" : "")', html)
        self.assertIn('marker.className = "tx-comment-marker"', html)
        self.assertIn("marker.dataset.commentId = first.id", html)


# ---------------------------------------------------------------------------
# The scale itself, exercised for real under Node
# ---------------------------------------------------------------------------

SCALE_SCENARIO = r"""
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const begin = html.indexOf("// [tye-pause-dots-begin]");
const end = html.indexOf("// [tye-pause-dots-end]");
if (begin < 0 || end < 0 || end <= begin) {
  console.log(JSON.stringify({ error: "pause markers missing" }));
  process.exit(0);
}
eval(html.slice(begin, end));
const out = {};
out.counts = [0, 0.1, 0.29, 0.3, 0.45, 0.59, 0.6, 0.9, 1.2, 3.0, 9.0, 20.0]
  .map(pauseDotCount);
out.textFor = [0.1, 0.45, 1.2, 3.0].map(function (p) {
  return pauseDotText(p).length;
});
out.gap = pauseGapSeconds({ end: 1.75 }, { start: 2.95 });
out.gapNone = pauseGapSeconds({ end: 1.75 }, null);
out.gapOverlap = pauseGapSeconds({ end: 2.0 }, { start: 1.9 });
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "needs node on PATH")
class DotScaleBehaviorTests(unittest.TestCase):
    def run_scenario(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "scale.js"
            script.write_text(SCALE_SCENARIO, encoding="utf-8")
            out = subprocess.run(
                [NODE, str(script), str(TEMPLATE)],
                capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads(out.stdout)
        self.assertNotIn("error", result, result)
        return result

    def test_counts_follow_the_written_scale(self):
        self.assertEqual(
            self.run_scenario()["counts"],
            [0, 0, 0, 1, 1, 1, 2, 3, 4, 10, 30, 30],
        )

    def test_no_dots_below_the_threshold(self):
        counts = self.run_scenario()["counts"]
        self.assertEqual(counts[1], 0)   # 0.1s
        self.assertEqual(counts[2], 0)   # 0.29s

    def test_longer_pauses_show_strictly_more_dots(self):
        counts = self.run_scenario()["counts"]
        picked = [counts[4], counts[8], counts[9]]  # 0.45, 1.2, 3.0
        self.assertEqual(picked, [1, 4, 10])
        self.assertTrue(picked[0] < picked[1] < picked[2])
        whole = self.run_scenario()["counts"]
        self.assertEqual(whole, sorted(whole))

    def test_the_cap_keeps_a_huge_gap_from_flooding_the_page(self):
        counts = self.run_scenario()["counts"]
        self.assertEqual(counts[10], 30)  # 9 seconds
        self.assertEqual(counts[11], 30)  # 20 seconds

    def test_dot_text_length_matches_the_count(self):
        self.assertEqual(self.run_scenario()["textFor"], [0, 1, 4, 10])

    def test_gap_is_next_start_minus_this_end(self):
        result = self.run_scenario()
        self.assertAlmostEqual(result["gap"], 1.2)
        self.assertEqual(result["gapNone"], 0)
        self.assertEqual(result["gapOverlap"], 0)


# ---------------------------------------------------------------------------
# The real render, exercised under Node with a fake document
# ---------------------------------------------------------------------------

RENDER_SCENARIO = r"""
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const entries = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

class El {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.attrs = {};
    this.className = "";
    this.textContent = "";
    this.type = "";
  }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(name, value) { this.attrs[name] = value; }
  addEventListener() {}
  querySelectorAll() { return []; }
}
const document = {
  createElement: function (tag) { return new El(tag); },
  createTextNode: function (text) {
    return { isText: true, textContent: text, children: [] };
  }
};

const pBegin = html.indexOf("// [tye-pause-dots-begin]");
const pEnd = html.indexOf("// [tye-pause-dots-end]");
if (pBegin < 0 || pEnd < 0) {
  console.log(JSON.stringify({ error: "pause markers missing" }));
  process.exit(0);
}
eval(html.slice(pBegin, pEnd));

var transcriptEl = new El("p");
var transcriptPanel = null;
var followOn = false;
var transcriptEntries = [];
var currentWordIndex = null;
var editSpans = [{ word_end: 10, edit_id: "edit-take-1" }];
var jobFlags = [];
var jobGold = [{
  word_start: 5, word_end: 10,
  reason: "This take is said again right after."
}];
var state = { comments: [{
  id: "c1", word_start: 15, word_end: 17, done: false,
  text: "Check this ending."
}] };
function entryByIndex(idx) {
  for (var i = 0; i < transcriptEntries.length; i++) {
    if (transcriptEntries[i].index === idx) { return transcriptEntries[i]; }
  }
  return null;
}
function commentEditedTs() { return 0; }
function fmtTransport() { return "0:00"; }
function wirePopupAnchor() {}
function openEditPopup() {}
function openFlagPopup() {}
function openGoldPopup() {}
function updateTxCounts() {}
function refreshFixSelection() {}
function updateTxActionStrip() {}
function updateChangeButtons() {}

const rBegin = html.indexOf("function renderTranscript(words)");
const rEnd = html.indexOf("function reloadTranscript()", rBegin);
if (rBegin < 0 || rEnd < 0) {
  console.log(JSON.stringify({ error: "render markers missing" }));
  process.exit(0);
}
eval(html.slice(rBegin, rEnd));

renderTranscript(entries);

function findByClass(el, cls) {
  return el.children.filter(function (c) { return c.className === cls; });
}
const out = { paragraphs: [] };
transcriptEl.children.forEach(function (para) {
  const row = findByClass(para, "tx-para-row")[0];
  const wordsSpan = findByClass(row, "tx-words")[0];
  const tokens = [];
  let lastWord = null;
  wordsSpan.children.forEach(function (child) {
    if (child.isText) { return; }
    if (child.className.indexOf("tw ") === 0) {
      lastWord = parseInt(child.dataset.index, 10);
      tokens.push({
        kind: "word", index: lastWord, cls: child.className,
        text: child.textContent,
        end: parseInt(child.dataset.end, 10)
      });
    } else if (child.className === "tpause") {
      tokens.push({
        kind: "pause", after: lastWord,
        count: child.textContent.length,
        chars: child.textContent,
        datasetKeys: Object.keys(child.dataset),
        ariaHidden: child.attrs["aria-hidden"] || null,
        title: child.title || ""
      });
    } else {
      tokens.push({
        kind: "marker", cls: child.className, after: lastWord,
        editId: child.dataset.editId || null,
        label: child.attrs["aria-label"] || ""
      });
    }
  });
  const margins = findByClass(para, "tx-margin");
  const commentMarkers = [];
  margins.forEach(function (margin) {
    margin.children.forEach(function (m) {
      commentMarkers.push({
        cls: m.className, commentId: m.dataset.commentId || null
      });
    });
  });
  out.paragraphs.push({ tokens: tokens, commentMarkers: commentMarkers });
});
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "needs node on PATH")
class RenderedParagraphTests(IsolatedHomeCase):
    def render(self):
        entries = self.entries()
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "render.js"
            data = Path(tmp) / "entries.json"
            script.write_text(RENDER_SCENARIO, encoding="utf-8")
            data.write_text(json.dumps(entries), encoding="utf-8")
            out = subprocess.run(
                [NODE, str(script), str(TEMPLATE), str(data)],
                capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads(out.stdout)
        self.assertNotIn("error", result, result)
        return result

    def all_tokens(self, rendered):
        tokens = []
        for para in rendered["paragraphs"]:
            tokens.extend(para["tokens"])
        return tokens

    def test_words_group_into_the_expected_paragraphs(self):
        rendered = self.render()
        grouped = [
            [t["index"] for t in para["tokens"] if t["kind"] == "word"]
            for para in rendered["paragraphs"]
        ]
        self.assertEqual(grouped, EXPECTED_PARAGRAPHS)

    def test_word_texts_and_indices_are_unchanged(self):
        tokens = [t for t in self.all_tokens(self.render())
                  if t["kind"] == "word"]
        self.assertEqual(
            [t["text"] for t in tokens],
            [text for text, _start in FIXTURE_SPEC])
        self.assertEqual(
            [t["index"] for t in tokens], list(range(len(FIXTURE_SPEC))))
        self.assertEqual(
            [t["end"] for t in tokens],
            [i + 1 for i in range(len(FIXTURE_SPEC))])

    def test_dot_runs_sit_at_the_measured_gaps_with_scale_counts(self):
        pauses = [t for t in self.all_tokens(self.render())
                  if t["kind"] == "pause"]
        self.assertEqual(
            [(t["after"], t["count"]) for t in pauses], EXPECTED_RUNS)
        # Strictly more dots for the clearly longer gaps among
        # the fixture's 0.45s, 1.2s, and 3.0s pauses.
        by_after = {t["after"]: t["count"] for t in pauses}
        self.assertTrue(by_after[1] < by_after[2] < by_after[4])

    def test_no_dots_below_the_threshold_and_none_after_the_last_word(self):
        pauses = [t for t in self.all_tokens(self.render())
                  if t["kind"] == "pause"]
        after_words = {t["after"] for t in pauses}
        # Every 0.1s gap in the fixture, and the end of the
        # transcript, stay dot free.
        for quiet_word in (0, 3, 5, 6, 7, 8, 10, 11, 12, 13, 15, 16, 17):
            self.assertNotIn(quiet_word, after_words)

    def test_runs_at_paragraph_breaks_render_in_the_closing_paragraph(self):
        rendered = self.render()
        # The 1.2s run after word 2 closes paragraph 0, the 3.0s
        # run after word 4 closes paragraph 1, and the 1.2s run
        # after word 14 closes paragraph 2.
        closing = []
        for para in rendered["paragraphs"]:
            runs = [t for t in para["tokens"] if t["kind"] == "pause"]
            if runs:
                closing.append((runs[-1]["after"], runs[-1]["count"]))
        self.assertEqual(closing, [(2, 4), (4, 10), (14, 4)])

    def test_dot_runs_carry_no_word_data(self):
        pauses = [t for t in self.all_tokens(self.render())
                  if t["kind"] == "pause"]
        self.assertTrue(pauses)
        for run in pauses:
            self.assertEqual(run["datasetKeys"], [])
            self.assertEqual(run["ariaHidden"], "true")
            self.assertEqual(set(run["chars"]), {"·"})
            self.assertIn("Dead air", run["title"])

    def test_cut_take_renders_struck_in_place_and_the_keeper_reads_clean(self):
        tokens = [t for t in self.all_tokens(self.render())
                  if t["kind"] == "word"]
        for token in tokens:
            if token["index"] in CUT_WORDS:
                self.assertIn("tw-cut", token["cls"], token)
            else:
                self.assertNotIn("tw-cut", token["cls"], token)
        # The struck take stays in the same paragraph as the
        # keeper that follows it, readable in context.
        rendered = self.render()
        para2 = [t for t in rendered["paragraphs"][2]["tokens"]
                 if t["kind"] == "word"]
        self.assertEqual(
            [t["index"] for t in para2], list(range(5, 15)))

    def test_reason_and_take_hooks_are_on_the_words_they_belong_to(self):
        tokens = self.all_tokens(self.render())
        markers = [t for t in tokens if t["kind"] == "marker"]
        edit_dots = [t for t in markers if t["cls"] == "tdot"]
        self.assertEqual(len(edit_dots), 1)
        self.assertEqual(edit_dots[0]["editId"], "edit-take-1")
        self.assertEqual(edit_dots[0]["after"], 9)
        self.assertIn("Why these words were removed",
                      edit_dots[0]["label"])
        held = [t for t in markers if t["cls"] == "theld"]
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["after"], 9)
        self.assertIn("Recommended to remove", held[0]["label"])
        words = [t for t in tokens if t["kind"] == "word"]
        for token in words:
            if token["index"] in CUT_WORDS:
                self.assertIn("tw-held", token["cls"], token)

    def test_comment_marker_pins_in_the_paragraph_margin(self):
        rendered = self.render()
        last = rendered["paragraphs"][3]
        self.assertEqual(len(last["commentMarkers"]), 1)
        marker = last["commentMarkers"][0]
        self.assertIn("tx-comment-marker", marker["cls"])
        self.assertEqual(marker["commentId"], "c1")
        for para in rendered["paragraphs"][:3]:
            self.assertEqual(para["commentMarkers"], [])
        words = [t for t in last["tokens"] if t["kind"] == "word"]
        commented = [t["index"] for t in words
                     if "tw-commented" in t["cls"]]
        self.assertEqual(commented, [15, 16])


if __name__ == "__main__":
    unittest.main()
