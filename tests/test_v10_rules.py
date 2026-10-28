"""Tests for v0.10 item 3: two speeds for learned rules.

A learned pattern is either a "small" rule (only about a filler
word, a pause, a breath, or one word kept or cut) or a "story"
rule (everything else, because it can change what a video means).
Small suggestions all surface the way patterns always have; story
suggestions surface one at a time, standing apart from the small
ones, and a saved rule keeps the class it was offered with.

Run from the copy root:
    PYTHONPATH=src python3 -m unittest tests.test_v10_rules
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import creator_rules, webapp  # noqa: E402
from talk_to_your_editor.webapp import WebAppConfig  # noqa: E402

TEMPLATE = (
    ROOT / "src" / "talk_to_your_editor" / "templates" / "test_app.html"
).read_text(encoding="utf-8")

EMPTY_STORE = {"rules": [], "dismissed": {}}


def entry(pattern_key, operation_type, verdict="worked", **extra):
    record = {
        "pattern_key": pattern_key,
        "operation_type": operation_type,
        "verdict": verdict,
    }
    record.update(extra)
    return record


def worked(pattern_key, operation_type, count, **extra):
    return [
        entry(pattern_key, operation_type, **extra) for _ in range(count)
    ]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class RuleClassTests(unittest.TestCase):
    def test_set_join_pause_is_small(self):
        pattern = {
            "pattern_key": "join_length_choice",
            "operation_type": "set_join_pause",
        }
        self.assertEqual(
            creator_rules.rule_class_for(pattern, []), "small"
        )

    def test_filler_key_is_small(self):
        pattern = {
            "pattern_key": "filler:um",
            "operation_type": "cut_words",
        }
        self.assertEqual(
            creator_rules.rule_class_for(pattern, []), "small"
        )

    def test_pause_key_is_small(self):
        pattern = {
            "pattern_key": "long_pause_here",
            "operation_type": "cut_words",
        }
        self.assertEqual(
            creator_rules.rule_class_for(pattern, []), "small"
        )

    def test_breath_key_is_small(self):
        pattern = {
            "pattern_key": "take_out_the_breath_before_the_line",
            "operation_type": "cut_words",
        }
        self.assertEqual(
            creator_rules.rule_class_for(pattern, []), "small"
        )

    def test_all_single_word_supporting_entries_is_small(self):
        pattern = {
            "pattern_key": "cut_that_word",
            "operation_type": "cut_words",
        }
        entries = [
            entry("cut_that_word", "cut_words", word_start=4, word_end=5),
            entry(
                "cut_that_word",
                "cut_words",
                verdict="partly",
                word_start=9,
                word_end=10,
            ),
            entry("cut_that_word", "cut_words", word_start=12, word_end=13),
            # A grade that did not work is not support, so its wide
            # span does not change the answer.
            entry(
                "cut_that_word",
                "cut_words",
                verdict="did_not_work",
                word_start=20,
                word_end=29,
            ),
        ]
        self.assertEqual(
            creator_rules.rule_class_for(pattern, entries), "small"
        )

    def test_replace_take_is_story(self):
        pattern = {
            "pattern_key": "swap_in_the_other_take",
            "operation_type": "replace_take",
        }
        entries = worked(
            "swap_in_the_other_take",
            "replace_take",
            3,
            word_start=10,
            word_end=18,
        )
        self.assertEqual(
            creator_rules.rule_class_for(pattern, entries), "story"
        )

    def test_gold_retake_leave_is_story(self):
        pattern = {
            "pattern_key": "gold:retake:leave",
            "operation_type": "flag_for_review",
        }
        entries = worked("gold:retake:leave", "flag_for_review", 3)
        self.assertEqual(
            creator_rules.rule_class_for(pattern, entries), "story"
        )

    def test_retake_band_is_story(self):
        pattern = {
            "pattern_key": "retake:a:last",
            "operation_type": "cut_words",
        }
        entries = worked("retake:a:last", "cut_words", 3)
        self.assertEqual(
            creator_rules.rule_class_for(pattern, entries), "story"
        )

    def test_multi_word_cut_pattern_is_story(self):
        pattern = {
            "pattern_key": "cut_the_repeated_thought",
            "operation_type": "cut_words",
        }
        entries = [
            entry(
                "cut_the_repeated_thought",
                "cut_words",
                word_start=5,
                word_end=12,
            ),
            entry(
                "cut_the_repeated_thought",
                "cut_words",
                word_start=20,
                word_end=27,
            ),
            entry(
                "cut_the_repeated_thought",
                "cut_words",
                word_start=30,
                word_end=38,
            ),
        ]
        self.assertEqual(
            creator_rules.rule_class_for(pattern, entries), "story"
        )

    def test_unclear_pattern_is_story(self):
        pattern = {
            "pattern_key": "something_new",
            "operation_type": "cut_words",
        }
        # Grades with no word spans recorded cannot show the
        # pattern is only about one word, so it is a story rule.
        entries = worked("something_new", "cut_words", 3)
        self.assertEqual(
            creator_rules.rule_class_for(pattern, entries), "story"
        )
        self.assertEqual(creator_rules.rule_class_for(pattern, []), "story")
        self.assertEqual(creator_rules.rule_class_for({}, []), "story")


# ---------------------------------------------------------------------------
# Surfacing
# ---------------------------------------------------------------------------


class SurfacingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entries = (
            worked("filler:um", "cut_words", 3)
            + worked("join_length_choice", "set_join_pause", 3)
            + [
                entry(
                    "cut_the_repeated_thought",
                    "cut_words",
                    word_start=5 + 10 * i,
                    word_end=12 + 10 * i,
                )
                for i in range(4)
            ]
            + worked("gold:held:cut", "cut_words", 3)
        )

    def test_only_the_strongest_story_suggestion_is_offered(self):
        suggestions = creator_rules.suggestions_for(
            self.entries, dict(EMPTY_STORE)
        )
        story = [s for s in suggestions if s["rule_class"] == "story"]
        self.assertEqual(len(story), 1)
        self.assertEqual(story[0]["pattern_key"], "cut_the_repeated_thought")
        self.assertEqual(story[0]["support_count"], 4)
        self.assertNotIn(
            "gold:held:cut", [s["pattern_key"] for s in suggestions]
        )

    def test_small_suggestions_all_offered_and_come_first(self):
        suggestions = creator_rules.suggestions_for(
            self.entries, dict(EMPTY_STORE)
        )
        self.assertEqual(
            [s["pattern_key"] for s in suggestions],
            [
                "filler:um",
                "join_length_choice",
                "cut_the_repeated_thought",
            ],
        )
        self.assertEqual(
            [s["rule_class"] for s in suggestions],
            ["small", "small", "story"],
        )

    def test_explicit_story_pattern_surfaces_as_the_one_story(self):
        entries = [
            entry(
                "swap_in_the_other_take",
                "replace_take",
                word_start=10,
                word_end=18,
                rule_request=True,
            )
        ]
        suggestions = creator_rules.suggestions_for(
            entries, dict(EMPTY_STORE)
        )
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["rule_class"], "story")
        self.assertTrue(suggestions[0]["explicit"])

    def test_thresholds_are_unchanged(self):
        # Two supporting grades, no style mark: nothing is offered,
        # for either class.
        entries = worked("filler:um", "cut_words", 2) + [
            entry(
                "cut_the_repeated_thought",
                "cut_words",
                word_start=5,
                word_end=12,
            ),
            entry(
                "cut_the_repeated_thought",
                "cut_words",
                word_start=20,
                word_end=27,
            ),
        ]
        self.assertEqual(
            creator_rules.suggestions_for(entries, dict(EMPTY_STORE)), []
        )


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


class SaveRuleTests(unittest.TestCase):
    def suggestion(self, **extra):
        base = {
            "pattern_key": "filler:um",
            "operation_type": "cut_words",
            "statement": "Filler words come out.",
            "support_count": 3,
            "explicit": False,
        }
        base.update(extra)
        return base

    def test_save_rule_stores_the_class(self):
        store = {"rules": [], "dismissed": {}}
        rule = creator_rules.save_rule(
            store, self.suggestion(rule_class="small")
        )
        self.assertEqual(rule["rule_class"], "small")
        self.assertEqual(store["rules"][0]["rule_class"], "small")

        store = {"rules": [], "dismissed": {}}
        rule = creator_rules.save_rule(
            store,
            self.suggestion(
                pattern_key="gold:held:cut", rule_class="story"
            ),
        )
        self.assertEqual(rule["rule_class"], "story")

    def test_save_rule_defaults_to_story_when_no_class(self):
        store = {"rules": [], "dismissed": {}}
        rule = creator_rules.save_rule(store, self.suggestion())
        self.assertEqual(rule["rule_class"], "story")


# ---------------------------------------------------------------------------
# The webapp view and the page
# ---------------------------------------------------------------------------


class WebappViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-v10-rules-"))
        self.job = self.tmp / "job"
        self.job.mkdir(parents=True)
        self.config = WebAppConfig(self.job)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_saved_rules_view_passes_class_and_source_through(self):
        store = {
            "rules": [
                {
                    "rule_id": "rule-filler-um",
                    "pattern_key": "filler:um",
                    "statement": "Filler words come out.",
                    "rule_class": "small",
                    "source": "pattern",
                    "active": True,
                },
                {
                    "rule_id": "rule-gold-held-cut",
                    "pattern_key": "gold:held:cut",
                    "statement": "Held parts like this are cut.",
                    "rule_class": "story",
                    "source": "explicit",
                    "active": True,
                },
            ],
            "dismissed": {},
        }
        webapp._save_rules_store(self.config, store)
        view = webapp._saved_rules_view(self.config)
        self.assertEqual(len(view), 2)
        self.assertEqual(view[0]["rule_class"], "small")
        self.assertEqual(view[0]["source"], "pattern")
        self.assertEqual(view[1]["rule_class"], "story")
        self.assertEqual(view[1]["source"], "explicit")
        # The view is plain JSON data, the shape the page receives.
        self.assertEqual(json.loads(json.dumps(view)), view)

    def test_rule_suggestions_carry_the_class(self):
        entries = worked("filler:um", "cut_words", 3) + [
            entry(
                "cut_the_repeated_thought",
                "cut_words",
                word_start=5 + 10 * i,
                word_end=12 + 10 * i,
            )
            for i in range(3)
        ]
        webapp._save_grades(self.config, entries)
        suggestions = webapp._rule_suggestions(self.config)
        self.assertEqual(
            {s["pattern_key"]: s["rule_class"] for s in suggestions},
            {
                "filler:um": "small",
                "cut_the_repeated_thought": "story",
            },
        )


class TemplateTests(unittest.TestCase):
    def test_story_suggestion_has_its_own_place_on_the_page(self):
        self.assertIn('id="storyRuleSuggestion"', TEMPLATE)
        self.assertIn("storyRuleSuggestionEl.appendChild(card)", TEMPLATE)
        self.assertIn(
            "Story rule: this can change what a video means.", TEMPLATE
        )
        self.assertIn(
            "It needs your clear yes before it ever applies.", TEMPLATE
        )
        self.assertIn("Save this story rule", TEMPLATE)
        # The story buttons post to the same save and dismiss
        # endpoints as the small ones.
        self.assertIn('postJson("/api/rule-save"', TEMPLATE)
        self.assertIn('postJson("/api/rule-dismiss"', TEMPLATE)
        # Small suggestions keep their existing button words.
        self.assertIn('"Save as a rule"', TEMPLATE)

    def test_saved_rows_name_the_class_and_the_grades_they_came_from(self):
        self.assertIn("Small rule", TEMPLATE)
        self.assertIn("Story rule", TEMPLATE)
        self.assertIn("Learned from your grades", TEMPLATE)
        self.assertIn('rule.source === "pattern"', TEMPLATE)
        self.assertIn('rule.source === "explicit"', TEMPLATE)
        # The Remove button (the easy undo) stays.
        self.assertIn('"Remove"', TEMPLATE)


if __name__ == "__main__":
    unittest.main()
