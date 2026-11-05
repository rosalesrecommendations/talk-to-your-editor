"""Round 0.25.0, phase 1: Justin's 0.24.0 Mac-run findings 1-3.

1. The opening rule never cuts the template opener ("Today's
   day N of ..."); a later quotation of it is not a retake.
2. A detected retake settles whole: the whole earlier take is
   offered as one span, never narrowed to the exact repeated
   runs; and a kept piece never starts mid-sentence.
3. The audio is the truth about words: a word stamped across
   room-level sound anchors nothing and protects nothing.

The zero push (0.25.0, after phase 1 left 9 mid-sentence
starts): every start is resolved the way Justin's finished
edit resolves it. The guard restores the contiguous missing
run before a piece (not the whole sentence head: the rest is
already in the piece before), a start at a kept take's first
word counts as a line start when settled cuts took the head,
a short tail of a cut take is cut with its take, and a repeat
inside one sentence after kept words is a parallel line, not
a take, so it is flagged and never settled. The gate below
counts a start as bad only when none of those applies, and
asserts the count is zero: any mid-sentence start in the
proof plan fails this test loudly.

The Day 30 gate at the bottom runs the real pair through the
pipeline stages and asserts the four proof metrics at plan
level (first line kept, zero mid-sentence starts, few long
silences implied by the plan, length next to his 2:45). The
rendered-audio silence count is proven in
v025-work/p0-proof-025.md; this test keeps the plan honest.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talk_to_your_editor import opening, retakes, smooth, webapp  # noqa: E402

ROOM = -50.0
SPEECH = -20.0
BREATH = -35.0


def env_with(spans, total_s=12.0):
    env = [ROOM] * int(total_s / smooth.FRAME_S)
    for start, end, level in spans:
        a = int(start / smooth.FRAME_S)
        b = int(end / smooth.FRAME_S)
        for i in range(a, min(b, len(env))):
            env[i] = level
    return env


def word(text, start, end):
    return {"word": text, "start": start, "end": end}


def span(start, end):
    return {"start": start, "end": end}


# ---------------------------------------------------------------------------
# Finding 1: the template opener is never cut
# ---------------------------------------------------------------------------


def _opener_words():
    # His real first line, the temperature line, then much later
    # a sentence quoting the opener (the shape that made the
    # coverage test read the opener as an abandoned take).
    texts = (
        "Today's day 30 of seeing if God will use cold water to "
        "help get rid of this thyroid cancer."
    ).split()
    texts += "Today's temperature is 40 degrees Fahrenheit.".split()
    texts += "Did you catch what I said differently there?".split()
    texts += (
        "I said, today's day 30 of seeing if God will use cold "
        "water to help get rid of this thyroid cancer, every time."
    ).split()
    words = []
    t = 1.0
    for text in texts:
        words.append(word(text, t, t + 0.3))
        t += 0.42
        if text.endswith((".", "?")):
            t += 1.6  # a real pause between sentences
    return words


class OpenerAnchor(unittest.TestCase):
    def test_the_opener_is_kept_whole_even_with_a_later_quotation(self):
        words = _opener_words()
        env = env_with(
            [(w["start"], w["end"], SPEECH) for w in words],
            total_s=words[-1]["end"] + 2.0,
        )
        ranges = [{"start": 0.9, "end": words[-1]["end"] + 0.1}]
        out, report = opening.opening_plan(words, ranges, env)
        self.assertEqual(report["cut_words"], [])
        self.assertEqual(out[0]["start"], ranges[0]["start"])
        kept = set()
        for rng in out:
            if "insert_kind" not in rng:
                kept.update(smooth._range_words(words, rng))
        self.assertEqual(min(kept), 0)

    def test_an_abandoned_opener_take_before_the_real_one_is_cut(self):
        words = [
            word("Today's", 1.0, 1.3), word("day", 1.42, 1.6),
            word("30", 1.72, 1.9), word("of", 2.02, 2.2),
            word("seeing", 2.32, 2.6), word("if", 2.72, 2.9),
            word("God", 3.02, 3.3), word("will", 3.42, 3.7),
            word("Today's", 5.7, 6.0), word("day", 6.12, 6.3),
            word("30", 6.42, 6.6), word("of", 6.72, 6.9),
            word("seeing", 7.02, 7.3), word("if", 7.42, 7.6),
            word("God", 7.72, 8.0), word("will", 8.12, 8.4),
            word("use", 8.52, 8.8), word("cold", 8.92, 9.2),
            word("water.", 9.32, 9.7),
        ]
        spans = [(w["start"], w["end"], SPEECH) for w in words]
        spans.append((5.1, 5.55, BREATH))  # the breath before the line
        env = env_with(spans, total_s=11.0)
        ranges = [
            {"start": 0.9, "end": 3.8},
            {"start": 5.6, "end": 9.8},
        ]
        out, report = opening.opening_plan(words, ranges, env)
        self.assertTrue(report["fired"])
        self.assertEqual(report["first_line_word"], 8)
        self.assertEqual(len(report["cut_words"]), 8)
        # Filming-day P0-3: no breath is kept at the head; the
        # plan starts right before the first word instead.
        self.assertIsNone(report["breath_kept"])
        first_sound = words[8]["start"]
        self.assertGreaterEqual(out[0]["start"], first_sound - 0.2)


# ---------------------------------------------------------------------------
# Finding 2a: a retake pair settles as one whole cut
# ---------------------------------------------------------------------------


class WholeTakeGold(unittest.TestCase):
    def test_a_held_pair_offers_the_whole_earlier_take(self):
        texts = (
            "Every time I've gotten into this tank I said today's "
            "day blank of every time I got into this tank I've "
            "said it plainly."
        ).split()
        words = [
            word(text, 1.0 + 0.4 * i, 1.3 + 0.4 * i)
            for i, text in enumerate(texts)
        ]
        pair = {
            "pair_id": "p1",
            "rule": "retake",
            "band": "a",
            "outcome": "held",
            "cut": {"word_start": 0, "word_end": 10, "text": ""},
            "keep": {"word_start": 10, "word_end": 19, "text": ""},
        }
        candidates = webapp._gold_candidates_from_retakes([pair], words)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["word_start"], 0)
        self.assertEqual(candidates[0]["word_end"], 10)


# ---------------------------------------------------------------------------
# Finding 2b: a kept piece starts at a sentence start
# ---------------------------------------------------------------------------


def _guard_words():
    texts = "Did you catch what I said there. Then we kept going on.".split()
    words = []
    t = 1.0
    for text in texts:
        words.append(word(text, t, t + 0.3))
        t += 0.42
    return words


class SentenceStartGuard(unittest.TestCase):
    def _env(self, words):
        return env_with(
            [(w["start"] - 0.05, w["end"] + 0.05, SPEECH) for w in words],
            total_s=words[-1]["end"] + 2.0,
        )

    def test_a_mid_sentence_start_backs_up_to_the_sentence_start(self):
        words = _guard_words()
        env = self._env(words)
        # The piece starts at "catch" (word 2); words 0-1 were cut.
        ranges = [{"start": words[2]["start"] - 0.04, "end": words[7]["end"] + 0.2}]
        out, report = smooth.enforce_sentence_starts(words, ranges, env)
        self.assertEqual(report["fixed"], 1)
        self.assertLessEqual(out[0]["start"], words[0]["start"])

    def test_a_blocked_head_word_keeps_the_start_where_it_is(self):
        words = _guard_words()
        env = self._env(words)
        ranges = [{"start": words[2]["start"] - 0.04, "end": words[7]["end"] + 0.2}]
        out, report = smooth.enforce_sentence_starts(
            words, ranges, env, blocked_words={0, 1}
        )
        self.assertEqual(report["fixed"], 0)
        self.assertEqual(report["left"], 1)
        self.assertEqual(out[0]["start"], ranges[0]["start"])

    def test_a_pause_trim_inside_one_sentence_is_left_alone(self):
        words = _guard_words()
        env = self._env(words)
        ranges = [
            {"start": words[0]["start"] - 0.04, "end": words[1]["end"] + 0.1},
            {"start": words[2]["start"] - 0.04, "end": words[7]["end"] + 0.2},
        ]
        out, report = smooth.enforce_sentence_starts(words, ranges, env)
        self.assertEqual(report["fixed"], 0)
        self.assertEqual(len(out), 2)


# ---------------------------------------------------------------------------
# The zero push: the guard resolves starts the way his edit does
# ---------------------------------------------------------------------------


def _long_sentence_words():
    # One 8-word sentence, then a second sentence. The head of
    # the first sentence is partly in the piece before: the old
    # whole-head count read heads like this as too long to fix
    # (his Day 30 heads measured 9 and 18 words) while the truly
    # missing runs were a few words.
    texts = (
        "One two three four five six seven eight. "
        "Next sentence starts here."
    ).split()
    words = []
    t = 1.0
    for text in texts:
        words.append(word(text, t, t + 0.3))
        t += 0.42
    return words


class GuardZeroPush(unittest.TestCase):
    def _env(self, words):
        return env_with(
            [(w["start"] - 0.05, w["end"] + 0.05, SPEECH) for w in words],
            total_s=words[-1]["end"] + 2.0,
        )

    def test_a_missing_run_restores_without_the_whole_head(self):
        words = _long_sentence_words()
        env = self._env(words)
        ranges = [
            {"start": words[0]["start"] - 0.04,
             "end": words[1]["end"] + 0.1},
            {"start": words[5]["start"] - 0.04,
             "end": words[11]["end"] + 0.2},
        ]
        out, report = smooth.enforce_sentence_starts(words, ranges, env)
        self.assertEqual(report["fixed"], 1)
        self.assertEqual(report["details"][0]["how"], "restore_run")
        self.assertEqual(report["details"][0]["run_words"], [2, 3, 4])
        starts = [r["start"] for r in out]
        self.assertLessEqual(min(starts), words[2]["start"])
        kept = set()
        for rng in out:
            kept.update(smooth._range_words(words, rng))
        self.assertTrue({2, 3, 4, 5} <= kept)

    def test_a_start_at_a_kept_take_is_a_line_start(self):
        texts = "Alpha beta gamma delta epsilon.".split()
        words = []
        t = 1.0
        for text in texts:
            words.append(word(text, t, t + 0.3))
            t += 0.42
        env = self._env(words)
        ranges = [
            {"start": words[2]["start"] - 0.04,
             "end": words[4]["end"] + 0.2},
        ]
        out, report = smooth.enforce_sentence_starts(
            words, ranges, env,
            blocked_words={0, 1}, take_start_words={2},
        )
        # Not restored (the head is a settled cut), not cut as a
        # fragment (the take start wins over the fragment rule),
        # and not left: the piece is exactly as it was.
        self.assertEqual(report["fixed"], 0)
        self.assertEqual(report["left"], 0)
        self.assertEqual(report["details"][0]["how"], "take_start")
        self.assertEqual(out[0]["start"], ranges[0]["start"])

    def test_a_short_tail_of_a_cut_take_is_cut_with_its_take(self):
        texts = (
            "Alpha beta gamma delta epsilon. Next one here."
        ).split()
        words = []
        t = 1.0
        for text in texts:
            words.append(word(text, t, t + 0.3))
            t += 0.42
        env = self._env(words)
        ranges = [
            {"start": words[3]["start"] - 0.04,
             "end": words[4]["end"] + 0.2},
            {"start": words[5]["start"] - 0.04,
             "end": words[7]["end"] + 0.2},
        ]
        out, report = smooth.enforce_sentence_starts(
            words, ranges, env, blocked_words={0, 1, 2},
        )
        self.assertEqual(report["fixed"], 1)
        self.assertEqual(
            report["details"][0]["how"], "fragment_cut_with_take"
        )
        kept = set()
        for rng in out:
            kept.update(smooth._range_words(words, rng))
        self.assertNotIn(3, kept)
        self.assertNotIn(4, kept)
        self.assertIn(5, kept)

    def test_a_restore_never_swallows_a_settled_cut_word(self):
        # "Through the / through the ..." shape: the kept take
        # repeats the cut take's words in continuous speech, so
        # the run word's sound start sits before the cut words'
        # sound midpoints. Backing the edge to the sound start
        # would play the cut take; the edge clamps to just past
        # the last cut word's midpoint instead.
        texts = "Through the through the valley flows.".split()
        words = []
        t = 1.0
        for text in texts:
            words.append(word(text, t, t + 0.3))
            t += 0.42
        env = env_with([(0.95, 3.6, SPEECH)], total_s=5.0)
        spans = [span(0.95, 3.6)]
        ranges = [
            {"start": words[3]["start"] - 0.04,
             "end": words[5]["end"] + 0.2},
        ]
        out, report = smooth.enforce_sentence_starts(
            words, ranges, env,
            blocked_words={0, 1}, spans=spans,
        )
        self.assertEqual(report["fixed"], 1)
        self.assertEqual(report["details"][0]["run_words"], [2])
        sbounds = smooth.span_sound_bounds(words, spans)

        def in_piece(i):
            mid = (sbounds[i][0] + sbounds[i][1]) / 2
            return any(
                r["start"] - 1e-6 <= mid <= r["end"] + 1e-6
                for r in out
            )

        self.assertFalse(in_piece(0))
        self.assertFalse(in_piece(1))
        self.assertTrue(in_piece(2))
        self.assertTrue(in_piece(3))

    def test_a_span_placed_word_restores_from_its_span_start(self):
        # "December" shape: the word sounds a second before its
        # stamp, inside a stretch its stamp does not sound in.
        # The spans place its sound; the envelope, walking from
        # the stamp, does not.
        words = [
            word("Before", 1.0, 1.3), word("words", 1.42, 1.72),
            word("here", 1.84, 2.14), word("now.", 2.26, 2.56),
            word("December", 5.0, 5.36), word("29th.", 5.36, 5.72),
        ]
        env = env_with(
            [(0.97, 2.6, SPEECH), (4.15, 4.45, SPEECH),
             (5.36, 5.72, SPEECH)],
            total_s=7.0,
        )
        # The real geometry: the burst span ends 0.45 s before
        # the stamp starts (the nearest span, so the span ruler
        # places the word at that span), and the next span sits
        # more than 0.75 s past the stamp's end, out of reach.
        spans = [span(0.97, 2.6), span(4.0, 4.55), span(6.83, 7.6)]
        ranges = [
            {"start": 0.96, "end": words[3]["end"] + 0.1},
            {"start": words[5]["start"] - 0.04,
             "end": words[5]["end"] + 0.2},
        ]
        out, report = smooth.enforce_sentence_starts(
            words, ranges, env, spans=spans
        )
        self.assertEqual(report["fixed"], 1)
        self.assertEqual(report["details"][0]["run_words"], [4])
        self.assertLessEqual(
            min(r["start"] for r in out if r["end"] > 5.0), 4.30
        )


class ParallelRestartFlag(unittest.TestCase):
    def _unit(self, tokens):
        return {
            "word_start": 0,
            "word_end": len(tokens),
            "tokens": tokens,
            "kept_indices": list(range(len(tokens))),
            "kept_tokens": list(tokens),
            "kept_start": 0,
            "kept_end": len(tokens),
            "terminal": True,
            "protected": False,
            "settled": False,
            "start_s": 1.0,
            "end_s": 9.0,
        }

    def test_a_repeat_after_kept_words_in_one_sentence_is_flagged(self):
        tokens = (
            "my wife has been telling me this my friends have "
            "been telling me this and even in the comments"
        ).split()
        candidates = retakes._intra_unit_candidates(
            self._unit(tokens), retakes.settings_from_profile(None)
        )
        by_start = {c["cut_start"]: c for c in candidates}
        self.assertIn(3, by_start)
        self.assertEqual(by_start[3]["band"], "flag")
        self.assertEqual(
            by_start[3]["flag_reason"], retakes._FLAG_PARALLEL_REASON
        )

    def test_a_repeat_at_the_unit_start_stays_a_judgment_candidate(self):
        tokens = (
            "helping to support helping to suppress inflammatory "
            "marked blood cells"
        ).split()
        candidates = retakes._intra_unit_candidates(
            self._unit(tokens), retakes.settings_from_profile(None)
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["cut_start"], 0)
        self.assertEqual(candidates[0]["band"], "b")


# ---------------------------------------------------------------------------
# Finding 3: the audio is the truth about words
# ---------------------------------------------------------------------------


class PhantomWord(unittest.TestCase):
    def test_a_word_stamped_over_silence_does_not_shield_the_gap(self):
        words = [
            word("hello", 1.0, 1.5),
            word("I'm", 2.0, 7.0),  # stamped across pure silence
            word("again", 8.0, 8.5),
        ]
        env = env_with([(1.0, 1.5, SPEECH), (8.0, 8.5, SPEECH)],
                       total_s=10.0)
        spans = [span(0.97, 1.53), span(7.97, 8.53)]
        ranges = [{"start": 0.9, "end": 8.6}]
        out, report = smooth.trim_internal_silences(
            words, ranges, env, spans=spans
        )
        kept = sum(r["end"] - r["start"] for r in out)
        self.assertGreaterEqual(report["trimmed"], 1)
        self.assertLess(kept, 3.0)  # the 6.4 s gap is gone

    def test_a_piece_of_only_silent_words_is_dropped(self):
        words = [
            word("hello", 1.0, 1.5),
            word("I'm", 2.0, 7.0),
            word("again", 8.0, 8.5),
        ]
        env = env_with([(1.0, 1.5, SPEECH), (8.0, 8.5, SPEECH)],
                       total_s=10.0)
        spans = [span(0.97, 1.53), span(7.97, 8.53)]
        ranges = [{"start": 1.9, "end": 7.1}]
        out, report = smooth.trim_internal_silences(
            words, ranges, env, spans=spans
        )
        self.assertEqual(out, [])
        self.assertEqual(
            [d["kind"] for d in report["details"]], ["silent_piece"]
        )


# ---------------------------------------------------------------------------
# The Day 30 gate: the four proof metrics on the real pair
# ---------------------------------------------------------------------------

STUDY = (
    Path.home() / "workspace" / "goals"
    / "muse-news-content-engine-200k-subscribers" / "files"
    / "creator-platform" / "study"
)
RAW_VIDEO = STUDY / "pairs" / "5 Before.MOV"
RAW_TX = STUDY / "transcripts" / "set5-raw.transcript.json"
HIS_LENGTH_S = 165.4  # his finished Day 30 edit, 2:45
OPENER_LAST_WORD = 18  # "Today's day 30 ... thyroid cancer", words 0-18


def _plan_gate_metrics(words, ranges, spans, segments, env=None,
                       blocked_words=frozenset(),
                       take_start_words=frozenset()):
    """The four proof metrics, measured on a settled plan.

    Words belong to pieces by their measured sound (span sound
    bounds), the ruler the trimmer and the word scoring use.
    Mid-sentence starts use the same ruler as the sentence-start
    guard: a piece is fine when its first word starts a sentence,
    when its previous kept word is the word right before it, or
    when it starts at a kept take's first word and every head
    word was taken out by a settled retake cut (the edited
    sentence begins at the kept take). Anything else is a
    mid-sentence start, and the gate asserts there are none.
    Long silences are the gaps of 0.5 s or more between measured
    speech spans inside a kept piece.
    """
    sbounds = smooth.span_sound_bounds(words, spans)

    def piece_words(rng):
        lo, hi = float(rng["start"]), float(rng["end"])
        return [
            i for i in range(len(words))
            if lo - 1e-6 <= (sbounds[i][0] + sbounds[i][1]) / 2 <= hi + 1e-6
        ]

    ends = smooth.sentence_end_indices(words, segments)
    kept = []
    for rng in ranges:
        if "insert_kind" not in rng:
            kept.extend(piece_words(rng))
    first_word = min(kept) if kept else None
    opener_whole = all(
        i in set(kept) for i in range(OPENER_LAST_WORD + 1)
    )
    mid_sentence = 0
    mid_sentence_words = []
    prev_last = None
    for rng in ranges:
        if "insert_kind" in rng:
            continue
        its = piece_words(rng)
        if not its:
            continue
        first_i = its[0]
        if not smooth.is_sentence_start(words, ends, first_i) and (
            prev_last is None or prev_last != first_i - 1
        ):
            s_i = first_i
            while s_i > 0 and not smooth.is_sentence_start(
                words, ends, s_i
            ):
                s_i -= 1
            head = list(range(s_i, first_i))
            is_take_start = (
                first_i in take_start_words
                and bool(head)
                and all(wi in blocked_words for wi in head)
            )
            if not is_take_start:
                mid_sentence += 1
                mid_sentence_words.append(first_i)
        prev_last = its[-1]
    # Plain splices: bad starts with no justification. A bad
    # start is justified only when backing it up would revive a
    # settled retake cut, or the missing head is long (over the
    # guard's word or time cap), or a measured silence of 0.5 s
    # or more crosses the extension. Anything else is the exact
    # failure Justin named, and this count must stay at zero.
    plain_splices = _plain_splices(
        words, ranges, segments, sbounds, env, blocked_words, spans
    )
    span_list = sorted(
        (float(s["start"]), float(s["end"])) for s in spans
    )
    long_silences = 0
    for rng in ranges:
        cursor = float(rng["start"])
        for s, e in span_list:
            if e <= cursor:
                continue
            if s >= float(rng["end"]):
                break
            if s - cursor >= 0.5:
                long_silences += 1
            cursor = max(cursor, min(e, float(rng["end"])))
        if float(rng["end"]) - cursor >= 0.5:
            long_silences += 1
    length = sum(
        float(r["end"]) - float(r["start"]) for r in ranges
    )
    return {
        "first_word": first_word,
        "opener_whole": opener_whole,
        "mid_sentence_starts": mid_sentence,
        "mid_sentence_words": mid_sentence_words,
        "plain_splices": plain_splices,
        "long_silences": long_silences,
        "length_s": length,
    }


def _plain_splices(words, ranges, segments, sbounds, env,
                   blocked_words, spans):
    """Bad piece starts that nothing justifies (see above)."""
    ends = smooth.sentence_end_indices(words, segments)
    room = smooth.room_level_db(env) if env else None
    starts = None
    span_list = sorted(
        (float(s["start"]), float(s["end"])) for s in spans
    )
    span_gaps = [
        (e1, s2)
        for (s1, e1), (s2, e2) in zip(span_list, span_list[1:])
        if s2 - e1 >= 0.5
    ]
    if env:
        starts = [
            max(
                smooth.word_sound_bounds(env, room, w)[0],
                float(w["start"]) - smooth.HEAD_STAMP_SLACK_S,
            )
            for w in words
        ]

    def piece_words(rng):
        lo, hi = float(rng["start"]), float(rng["end"])
        return [
            i for i in range(len(words))
            if lo - 1e-6 <= (sbounds[i][0] + sbounds[i][1]) / 2 <= hi + 1e-6
        ]

    plain = []
    prev_last = None
    for rng in ranges:
        if "insert_kind" in rng:
            continue
        its = piece_words(rng)
        if not its:
            continue
        first_i = its[0]
        if not smooth.is_sentence_start(words, ends, first_i) and (
            prev_last is None or prev_last != first_i - 1
        ):
            s_i = first_i
            while s_i > 0 and not smooth.is_sentence_start(
                words, ends, s_i
            ):
                s_i -= 1
            missing = list(range(s_i, first_i))
            justified = (
                len(missing) > smooth.MAX_HEAD_WORDS_RESTORED
                or any(wi in blocked_words for wi in missing)
            )
            if not justified and starts is not None:
                new_start = max(
                    0.0, starts[s_i] - smooth.HEAD_IN_S
                )
                if float(rng["start"]) - new_start > (
                    smooth.MAX_HEAD_RESTORE_S
                ):
                    justified = True
                else:
                    # A measured silence of 0.5 s or more between
                    # the head word's sound and this piece's first
                    # word's sound: restoring the head would mean
                    # importing that dead air (the "December 29th"
                    # case, a real pause inside the phrase, which
                    # his own edit also keeps). The gap is read on
                    # the span ruler, the trimmer's own measure.
                    head_sound = sbounds[s_i][0]
                    first_sound = sbounds[first_i][0]
                    for a, b in span_gaps:
                        if a >= head_sound - 0.05 and b <= first_sound + 0.05:
                            justified = True
                            break
            if not justified:
                plain.append(first_i)
        prev_last = its[-1]
    return plain


@unittest.skipUnless(
    RAW_VIDEO.is_file() and RAW_TX.is_file(),
    "the Day 30 study pair is not on this machine",
)
class Day30Gate(unittest.TestCase):
    def test_the_day_30_plan_passes_the_four_proof_metrics(self):
        import json
        import os
        import shutil
        import subprocess
        import tempfile
        from unittest import mock

        from talk_to_your_editor import intake_build, transcribe
        from talk_to_your_editor.webapp import WebAppConfig

        raw_data = json.loads(RAW_TX.read_text())
        raw_words = raw_data["words"]
        segments = raw_data.get("segments")
        tmp = Path(tempfile.mkdtemp(prefix="v25-gate-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        wav = tmp / "raw-16k.wav"
        proc = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(RAW_VIDEO),
             "-vn", "-ac", "1", "-ar", "16000", str(wav)],
            capture_output=True, text=True, timeout=1200,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-300:])
        spans = transcribe.measure_speech_spans(wav)
        home = tmp / "home"
        home.mkdir()
        job = tmp / "job"
        job.mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            os.environ.pop("MODEL_API_KEY", None)
            os.environ.pop("ELEVENLABS_API_KEY", None)
            transcript_result = {
                "engine": raw_data.get("model", "study"),
                "duration": float(raw_data["duration"]),
                "words": raw_words,
                "segments": segments,
            }
            intake_build.build_job_files(
                job, RAW_VIDEO.name, transcript_result, spans,
                {"duration": float(raw_data["duration"])},
                captions_on=False,
            )
            link = job / RAW_VIDEO.name
            if not link.exists():
                link.symlink_to(RAW_VIDEO)
            work_dir = job / "intake-work"
            work_dir.mkdir(exist_ok=True)
            shutil.copyfile(wav, work_dir / "audio.wav")
            config = WebAppConfig(job, launch_home=tmp / "launch")
            with mock.patch.object(
                webapp, "_render_session_splice", return_value=None
            ):
                result = webapp._run_front_door(config, {
                    "provider": "mock",
                    "remove_retakes": True,
                    "keep_mode": "last",
                    "cut_silence": True,
                    "buffer_s": 0.1,
                    "remove_fillers": False,
                    "mode": "cleanest",
                })
            self.assertTrue(result.get("ok"), result)
            cuts = webapp._effective_cuts(config)
            record = json.loads(
                (config.session_dir / "retake_pass.json").read_text()
            )
        blocked_words = set()
        take_start_words = set()
        for pair in record.get("pairs") or []:
            if pair.get("outcome") not in ("applied", "held"):
                continue
            cut_side = pair.get("cut") or {}
            cs, ce = cut_side.get("word_start"), cut_side.get("word_end")
            if isinstance(cs, int) and isinstance(ce, int):
                blocked_words.update(range(cs, ce))
            keep_side = pair.get("keep") or {}
            ks = keep_side.get("word_start")
            if isinstance(ks, int):
                take_start_words.add(ks)
        env = smooth.envelope_db(wav)
        metrics = _plan_gate_metrics(
            raw_words, cuts["kept_ranges"], spans, segments,
            env=env, blocked_words=blocked_words,
            take_start_words=take_start_words,
        )
        # 1. The first spoken line is the template opener, whole.
        self.assertEqual(metrics["first_word"], 0)
        self.assertTrue(metrics["opener_whole"])
        # 2. Zero mid-sentence starts (the zero push): a start is
        # bad when it is not a sentence start, its previous kept
        # word is not the word right before it, and it is not a
        # kept take's first word standing in for a settled-cut
        # head. Any such start fails this test loudly, and so
        # does a plain splice.
        self.assertEqual(
            metrics["mid_sentence_starts"], 0,
            f"mid-sentence starts at words "
            f"{metrics['mid_sentence_words']}",
        )
        self.assertEqual(
            metrics["plain_splices"], [],
            f"plain splices at words {metrics['plain_splices']} "
            f"(all bad starts: {metrics['mid_sentence_starts']})",
        )
        # 3. Long silences inside the plan: his edit has 1.
        self.assertLessEqual(metrics["long_silences"], 2)
        # 4. Length next to his 2:45.
        self.assertLessEqual(metrics["length_s"], HIS_LENGTH_S + 20.0)
        self.assertGreaterEqual(metrics["length_s"], HIS_LENGTH_S - 20.0)


if __name__ == "__main__":
    unittest.main()
