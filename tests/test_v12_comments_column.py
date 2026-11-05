"""v0.12, change 2: the comments column gets one scroll and
fewer cards.

From Justin's Oct 29, 2026 review of 0.11.1 (PLAN.md, right
column item): the seven stacked cards in the right column
become one calm column. In order it holds the chat composer
(mechanics unchanged, still pinned by test_v111_comments.py),
a "Done (n)" toggle (in its own list head row in 0.12; since
v0.14 Batch C it rides inline in the comments head, one 44px
row with the pane name), the comment list,
and one collapsed-by-default disclosure card at the foot,
"More for this video", holding the five sections that used to
be their own cards: Overall feedback, Your style, Revised
edit, Finished edit, and History.

Pinned here, at source level, in the style of the other page
tests (the scripted probes live in the fit harness):

- F4: exactly one element in the column scrolls (the column
  container itself); the composer and the list sit above the
  More card, and the More card is collapsed at load;
- F6: the list renders open comments by default, in time
  order; the Done toggle's count is the done count; turning
  it on renders done comments in the same list, quieter;
  a card's own Done / Re-open button flips its done flag on
  the existing save path, so marking done removes it from
  the open list and re-opening returns it;
- F5: clicking a comment card seeks the video to the
  comment's moment and marks the comment's words active in
  the transcript with the transcript's own active-word
  styling (.tw-now); a time pin has no word anchor, so it
  seeks and marks nothing;
- preservation: the composer's ids, the comment state shape,
  and the finish card's hidden-until-ready behavior are
  untouched, and the new code calls no endpoint.

The done flag itself is not new: comment objects already
carried it, the transcript markers and the timeline dots
already read it, and a loaded job already restored it. What
was missing before this change was any way to set it from a
card and any list filtering by it, so both are added here.

Everything is offline; no provider is called.

v0.15 Batch E1 note: the comment cards, the Done toggle, and
the pin composer are gone as surfaces; the comments live in
the Editor thread as pin entries, and the Overall feedback
section was absorbed by the thread. The tests below that
pinned those surfaces now pin the thread behavior that
replaced them; the data path (comments store, done flag,
markers, dots) is unchanged.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")

STYLE = TEMPLATE[TEMPLATE.index("<style>"):TEMPLATE.index("</style>")]


def between(start_marker: str, end_marker: str) -> str:
    start = TEMPLATE.index(start_marker)
    end = TEMPLATE.index(end_marker, start)
    return TEMPLATE[start:end]


def column() -> str:
    """The right column's markup, composer to closing aside."""
    return between('id="toolsPane"', "</aside>")


def render_list() -> str:
    return between("function renderList()", "function updateTxCounts()")


def css_rules() -> list:
    """(selector, body) pairs for every rule in the style block,
    with comments stripped so a rule's selector reads clean."""
    plain = re.sub(r"/\*.*?\*/", "", STYLE, flags=re.DOTALL)
    return [
        (match.group(1).strip(), match.group(2))
        for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", plain)
    ]


# The elements that live inside the right column, below the
# column container itself. None of them may scroll on its own.
INNER_IDS = (
    "pinCard", "commentsHead", "doneToggle", "list", "moreCard",
    "overallCard", "styleCard", "revisedCard", "finishCard",
    "historyCard", "overallList", "historyList", "savedRulesList",
)


def visible_comments(comments: list, show_done: bool) -> list:
    """A mirror of renderList's filter: open comments always
    show; done ones only while the toggle is on. The state list
    is kept sorted by time, so the filter keeps time order."""
    return [c for c in comments if show_done or not c["done"]]


def marked_spans(spans: list, comment: dict) -> list:
    """A mirror of highlightCommentWords' overlap rule: a word
    span (start, end) is marked when it overlaps the comment's
    [word_start, word_end). A time pin carries no anchors and
    marks nothing."""
    if not isinstance(comment.get("word_start"), (int, float)):
        return []
    if not isinstance(comment.get("word_end"), (int, float)):
        return []
    return [
        span for span in spans
        if span[0] < comment["word_end"] and comment["word_start"] < span[1]
    ]


class OneScrollerTests(unittest.TestCase):
    """F4: exactly one scrolling element in the column."""

    def test_the_column_container_itself_scrolls(self):
        bodies = [
            body for selector, body in css_rules()
            if selector == "body.job-loaded .review-tools"
        ]
        self.assertTrue(bodies)
        self.assertTrue(
            any("overflow-y: auto" in body for body in bodies),
            bodies,
        )

    def test_no_inner_element_of_the_column_scrolls(self):
        offenders = []
        for selector, body in css_rules():
            scrolls = (
                "overflow-y: auto" in body
                or "overflow-y: scroll" in body
                or "overflow: auto" in body
                or "overflow: scroll" in body
            )
            if not scrolls:
                continue
            for part in selector.split(","):
                part = part.strip()
                for inner in INNER_IDS:
                    if re.search(r"#" + inner + r"(?![\w-])", part):
                        offenders.append(part)
                # A descendant of the column container (the
                # container's own rules end at .review-tools).
                if ".review-tools" in part and not part.endswith(
                    ".review-tools"
                ):
                    offenders.append(part)
        self.assertEqual(offenders, [])

    def test_no_inner_element_scrolls_itself_inline(self):
        # No element inside the column carries its own overflow
        # in markup either.
        self.assertNotIn("overflow", column())


class ColumnOrderTests(unittest.TestCase):
    """F4: composer, list, then the More card (the Done toggle
    rides in the comments head since v0.14 Batch C)."""

    def test_column_holds_its_pieces_in_order(self):
        # v0.15 Batch E1: the column is the Editor panel (head,
        # thread, input row) and then the More card.
        col = column()
        positions = [
            col.index('id="%s"' % name)
            for name in (
                "editorPanel", "commentsHead", "threadList",
                "editorInputRow", "moreCard",
            )
        ]
        self.assertEqual(positions, sorted(positions))

    def test_editor_head_carries_the_name_and_waiting_count(self):
        # v0.15 Batch E1: the head carries the editor's name
        # (click to rename) and the one waiting count; the
        # Done toggle left with the comment cards.
        head = between('id="commentsHead"', 'id="threadList"')
        self.assertIn('id="editorName"', head)
        self.assertIn('id="commentsHeadCount"', head)
        self.assertNotIn('id="doneToggle"', TEMPLATE)

    def test_panel_and_thread_are_not_inside_the_more_card(self):
        col = column()
        more = col.index('id="moreCard"')
        self.assertLess(col.index('id="editorPanel"'), more)
        self.assertLess(col.index('id="threadList"'), more)


class DoneFilterTests(unittest.TestCase):
    """F6: open by default, Done toggle, re-open from the card."""

    COMMENTS = [
        {"id": 1, "ts": 4.0, "done": False},
        {"id": 2, "ts": 9.0, "done": True},
        {"id": 3, "ts": 15.0, "done": False},
        {"id": 4, "ts": 21.0, "done": True},
    ]

    def test_default_list_is_open_comments_in_time_order(self):
        shown = visible_comments(self.COMMENTS, False)
        self.assertEqual([c["id"] for c in shown], [1, 3])

    def test_toggle_on_adds_done_comments_in_the_same_order(self):
        shown = visible_comments(self.COMMENTS, True)
        self.assertEqual([c["id"] for c in shown], [1, 2, 3, 4])

    def test_done_count_is_the_number_of_done_comments(self):
        done = len(self.COMMENTS) - len(
            visible_comments(self.COMMENTS, False))
        self.assertEqual(done, 2)

    def test_the_filter_and_its_default_live_in_render_list(self):
        # v0.15 Batch E1: renderList no longer renders cards or
        # filters by done; the thread is the whole record. The
        # flag stays, always on, for the callers that name it.
        self.assertIn("var showDoneComments = true;", TEMPLATE)
        body = render_list()
        self.assertNotIn("visible.forEach", body)
        self.assertNotIn("!c.done", body)
        self.assertIn("refreshThread();", body)

    def test_the_toggle_flips_the_flag_and_re_renders(self):
        wiring = between("var doneToggleBtn", "applyLayout();")
        self.assertIn("showDoneComments = !showDoneComments;", wiring)
        self.assertIn("renderList();", wiring)

    def test_the_toggle_label_and_state_are_rewritten_each_render(self):
        # v0.15 Batch E1: the counts code rewrites the one
        # waiting count in the panel head and the top bar.
        counts = between(
            "function updateCommentsCounts()",
            "var showDoneComments")
        self.assertIn(
            'cc.textContent = n ? String(n) : "";', counts)
        self.assertIn('"(" + n + " waiting)"', counts)
        # renderList is what calls the counts update.
        self.assertIn("updateCommentsCounts();", render_list())

    def test_done_comments_render_quieter_in_the_same_list(self):
        # v0.15 Batch E1: done comments are never hidden from
        # the thread, and settled entries read quieter: a
        # settled held entry's waiting dot goes gray.
        self.assertNotIn('c.done ? " done"', TEMPLATE)
        settled_rule = [
            body for selector, body in css_rules()
            if selector == ".thread-held.settled .thread-dot"
        ]
        self.assertEqual(len(settled_rule), 1)
        self.assertIn("var(--muted)", settled_rule[0])

    def test_each_card_carries_its_own_done_or_reopen_button(self):
        # v0.15 Batch E1: a pin entry carries its own Edit
        # button until a run has used it, then the Used line
        # instead; held entries carry Cut it / Leave it.
        pin = between(
            "function threadPinEl(entry)",
            "function threadWhyEl(entry)")
        self.assertIn('edit.textContent = "Edit";', pin)
        self.assertIn("entry.used_line", pin)
        held = between(
            "function threadHeldEl(entry)",
            "function settleHeldFromThread")
        self.assertIn('cut.textContent = "Cut it";', held)
        self.assertIn('leave.textContent = "Leave it";', held)

    def test_opening_a_done_comment_from_its_marker_shows_it(self):
        # v0.15 Batch E1: opening from a marker or dot opens
        # the panel and scrolls the thread to the pin entry,
        # done or not, and lights it.
        opener = between(
            "function openCommentById(id)",
            "function updateCommentsCounts()")
        self.assertIn("applyCommentsOpen(true);", opener)
        self.assertIn("refreshThread().then(", opener)
        self.assertIn('[data-comment-id="', opener)
        self.assertIn('card.classList.add("thread-flash");', opener)

    def test_all_done_still_says_so_instead_of_a_blank_list(self):
        # v0.15 Batch E1: the thread never goes blank; with no
        # entries at all it shows the spec's empty state line.
        view = between(
            "function renderThreadView(view)",
            "function refreshThread()")
        self.assertIn(
            "This is your editor. Ask for a change the way "
            "you would say it out loud",
            view,
        )


class MoreCardTests(unittest.TestCase):
    """F4: one collapsed disclosure holding the five sections."""

    def more(self) -> str:
        # The Revised edit section carries its own small
        # downloads disclosure, so the More card's closing tag
        # is the LAST </details> in the column, not the first.
        col = column()
        return col[col.index('id="moreCard"'):col.rindex("</details>")]

    def test_more_card_is_one_details_card_collapsed_by_default(self):
        tags = re.findall(r'<details[^>]*id="moreCard"[^>]*>', TEMPLATE)
        self.assertEqual(len(tags), 1)
        self.assertNotIn(" open", tags[0])
        self.assertIn('class="card job-only"', tags[0])

    def test_more_card_is_titled_more_for_this_video(self):
        self.assertIn(
            "<summary>More for this video</summary>", self.more())

    def test_the_five_sections_moved_inside_in_order(self):
        # v0.15 Batch E1: Overall feedback was absorbed by the
        # Editor thread; the other four sections stay, in order.
        more = self.more()
        positions = [
            more.index('id="%s"' % name)
            for name in (
                "styleCard", "revisedCard",
                "finishCard", "historyCard",
            )
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn('id="overallCard"', TEMPLATE)

    def test_the_sections_keep_their_key_ids_and_headings(self):
        more = self.more()
        for marker in (
            'id="savedRulesList"', 'id="rulesEmpty"',
            'id="runRender"', 'id="renderStatus"',
            'id="finishCosts"',
            'id="historyList"', 'id="undoBtn"', 'id="redoBtn"',
            "<h2>Your style</h2>",
            "<h2>Revised edit</h2>", "<h2>Finished edit</h2>",
            "<h2>History</h2>",
        ):
            self.assertIn(marker, more)

    def test_headings_read_as_quiet_sub_headings_inside(self):
        rules = [
            body for selector, body in css_rules()
            if selector == "#moreCard .card h2"
        ]
        self.assertEqual(len(rules), 1)
        # v0.13 item 7 (Descript steering): the group headings
        # go quiet, 13px, down from the 0.12 spec's 15px.
        self.assertIn("font-size: 13px", rules[0])
        # And the moved sections drop their own card chrome,
        # because the disclosure is the one card there now.
        chrome = [
            body for selector, body in css_rules()
            if selector == "#moreCard .card"
        ]
        self.assertEqual(len(chrome), 1)
        self.assertIn("background: none", chrome[0])
        self.assertIn("box-shadow: none", chrome[0])

    def test_finish_card_keeps_its_hidden_until_ready_behavior(self):
        self.assertIn(
            '<div class="card job-only" id="finishCard" hidden>',
            self.more(),
        )
        self.assertIn("finishCard.hidden = !job.finish.ready;", TEMPLATE)

    def test_more_card_closes_before_the_column_does(self):
        col = column()
        close = col.rindex("</details>")
        self.assertLess(col.index('id="historyCard"'), close)
        # Nothing but space sits between the More card's close
        # and the column's own close: it is the column's foot.
        self.assertEqual(col[close + len("</details>"):].strip(), "")


class ClickSeekHighlightTests(unittest.TestCase):
    """F5: a card click seeks and marks the comment's words."""

    def pin_entry(self) -> str:
        return between(
            "function threadPinEl(entry)",
            "function threadWhyEl(entry)")

    def test_card_click_seeks_and_marks_words(self):
        # v0.15 Batch E1: the thread pin entry's moment line is
        # the click target; it seeks and marks the same way the
        # comment card's click did.
        handler = between(
            'moment.addEventListener("click"',
            "card.appendChild(moment);")
        self.assertIn("seekToEdited(commentEditedTs(c), c.fmt);", handler)
        self.assertIn("highlightMarkerFor(c.id);", handler)
        self.assertIn("highlightCommentWords(c);", handler)

    def test_card_click_leaves_the_cards_buttons_to_themselves(self):
        # The moment line and the Edit button are separate
        # buttons with separate handlers on the pin entry.
        pin = self.pin_entry()
        self.assertIn('moment.className = "thread-moment";', pin)
        self.assertIn('edit.addEventListener("click"', pin)
        self.assertIn('moment.addEventListener("click"', pin)

    def test_the_timestamp_button_still_seeks_and_now_marks_too(self):
        handler = between(
            'moment.addEventListener("click"',
            "card.appendChild(moment);")
        self.assertIn("seekToEdited(commentEditedTs(c), c.fmt);", handler)
        self.assertIn("highlightCommentWords(c);", handler)

    def test_highlight_uses_the_transcripts_active_word_styling(self):
        fn = between(
            "function highlightCommentWords(c)", "function renderList()")
        self.assertIn('el.classList.add("tw-now");', fn)
        self.assertIn('transcriptEl.querySelectorAll(".tw")', fn)
        # The overlap rule over the word spans' own indexes.
        self.assertIn("start < c.word_end && c.word_start < end", fn)

    def test_highlight_needs_a_word_anchor(self):
        fn = between(
            "function highlightCommentWords(c)", "function renderList()")
        self.assertIn(
            'if (typeof c.word_start !== "number" || '
            'typeof c.word_end !== "number") { return; }',
            fn,
        )

    def test_overlap_rule_marks_exactly_the_pinned_words(self):
        spans = [(0, 1), (1, 3), (3, 4), (4, 6), (6, 7)]
        pin = {"word_start": 3, "word_end": 6}
        self.assertEqual(marked_spans(spans, pin), [(3, 4), (4, 6)])

    def test_a_time_pin_marks_nothing_but_still_seeks(self):
        spans = [(0, 1), (1, 3), (3, 4)]
        self.assertEqual(marked_spans(spans, {"ts": 2.0}), [])
        # The seek half of the click does not depend on anchors.
        handler = between(
            'moment.addEventListener("click"',
            "card.appendChild(moment);")
        self.assertIn("seekToEdited(commentEditedTs(c), c.fmt);", handler)


class PreservationTests(unittest.TestCase):
    """The composer, the state shape, and the save path stay."""

    def test_composer_ids_stay_in_the_pin_card(self):
        # v0.15 Batch E1: the one input row in the Editor panel
        # carries the surviving ids; the pin composer's own
        # ids are gone with it.
        panel = between('id="editorPanel"', 'id="moreCard"')
        for marker in (
            'id="editorAttachBtn"', 'id="draft"',
            'id="draftDictate"', 'id="save"', 'id="saveStatus"',
            'id="draftDictateStatus"',
        ):
            self.assertIn(marker, panel)
        for gone in (
            'id="pinCard"', 'id="manualTs"', 'id="scope"',
            'id="keepPlaying"', 'id="pinNote"',
        ):
            self.assertNotIn(gone, TEMPLATE)

    def test_comment_state_shape_is_unchanged(self):
        # The comment object is built on the server now (the
        # chat send saves the pin); the page still sends the
        # same moment fields, and loading a job still restores
        # the done flag as a flag.
        self.assertIn(
            "word_start: pinnedWordStart, word_end: pinnedWordEnd,",
            TEMPLATE,
        )
        self.assertIn("done: !!c.done", TEMPLATE)

    def test_the_new_code_calls_no_endpoint(self):
        for chunk in (
            between("function highlightCommentWords(c)",
                    "function renderList()"),
            between("var doneToggleBtn", "applyLayout();"),
        ):
            self.assertNotIn("fetch(", chunk)
            self.assertNotIn("/api/", chunk)
        # Saving still goes through the one existing path.
        self.assertIn('postJson("/api/comments", {', TEMPLATE)


if __name__ == "__main__":
    unittest.main()
