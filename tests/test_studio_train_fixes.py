"""Tests for the Studio Train fixes from Justin's Nov 1 feedback.

(i)   The raw video slot comes before the finished edit, in the
      order he works.
(ii)  "A style I like" saves as Inspiration: a thin endpoint that
      takes only style-kind teaching suggestions (kind "style")
      and lands them in launch_home/inspiration.json, apart from
      the creator's own rules; a saved style is not offered again.
(iii) A rule the creator writes themselves saves through
      /api/rule-custom as a plain statement in the creator store;
      an empty statement is refused.
(iv)  The Train screen carries the rules list, Find more rules,
      Add my own rule, and the Inspiration list.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_studio_creator_store import ServerCase

from talk_to_your_editor import creator_rules, teaching
from talk_to_your_editor.webapp import WebAppConfig

TEMPLATES = ROOT / "src" / "talk_to_your_editor" / "templates"


def seed_draft(launch: Path, *, statement: str, kind: str, basis: str):
    config = WebAppConfig(None, launch_home=launch)
    note = (
        teaching.PAIR_SOURCE_NOTE
        if basis == "pair"
        else teaching.FINISHED_SOURCE_NOTE
    )
    drafts = teaching.add_drafts(
        config.session_dir,
        [
            teaching.make_draft(
                statement=statement,
                reason="From the teaching drop.",
                at_s=None,
                quote="",
                basis=basis,
                kind=kind,
                source_note=note,
            )
        ],
    )
    return drafts[-1]


class TrainMarkupTest(unittest.TestCase):
    def test_raw_slot_first_and_train_sections_exist(self):
        page = (TEMPLATES / "studio.html").read_text(encoding="utf-8")
        self.assertLess(page.index('id="rowRaw"'), page.index('id="rowFinished"'))
        for marker in (
            'id="styleWrap"',
            'id="styleList"',
            'id="rulesList"',
            'id="findRulesBtn"',
            'id="addRuleBtn"',
            'id="ownRuleInput"',
            'id="ownRuleSave"',
            'id="inspWrap"',
            'id="inspList"',
            "Save as Inspiration",
            "/api/inspiration-save",
            "/api/rule-custom",
        ):
            self.assertIn(marker, page)


class InspirationSaveTest(ServerCase):
    def test_only_style_drafts_save_and_only_once(self):
        style = seed_draft(
            self.launch,
            statement="The talking runs at about 150 words a minute.",
            kind="style",
            basis="finished_only",
        )
        mine = seed_draft(
            self.launch,
            statement="Filler words like um and uh are cut out.",
            kind="mine",
            basis="pair",
        )
        base = self.start(WebAppConfig(None, launch_home=self.launch))
        status, body = self.post_json(
            base,
            "/api/inspiration-save",
            {"pattern_keys": [style["pattern_key"], mine["pattern_key"]]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["saved"], 1)
        self.assertEqual(
            [e["statement"] for e in body["inspiration"]],
            ["The talking runs at about 150 words a minute."],
        )
        # It landed at the creator level, apart from the rules.
        saved_file = self.launch / "inspiration.json"
        self.assertTrue(saved_file.is_file())
        store = creator_rules.load_rules_store(
            self.launch / "creator_rules.json"
        )
        self.assertEqual(creator_rules.active_rule_statements(store), [])
        # And it is not offered again as a suggestion.
        status, body = self.request(base, "/api/job")
        summary = json.loads(body)
        offered = [s["pattern_key"] for s in summary["rule_suggestions"]]
        self.assertNotIn(style["pattern_key"], offered)
        self.assertIn(mine["pattern_key"], offered)
        self.assertEqual(
            [e["statement"] for e in summary["inspiration"]],
            ["The talking runs at about 150 words a minute."],
        )
        # Saving the same key again changes nothing.
        status, body = self.post_json(
            base,
            "/api/inspiration-save",
            {"pattern_keys": [style["pattern_key"]]},
        )
        self.assertEqual(body["saved"], 0)
        self.assertEqual(len(body["inspiration"]), 1)


class CustomRuleTest(ServerCase):
    def test_custom_rule_saves_as_a_statement(self):
        base = self.start(WebAppConfig(None, launch_home=self.launch))
        status, body = self.post_json(
            base,
            "/api/rule-custom",
            {"statement": "Never cut the prayer at the end."},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            body["rule"]["statement"], "Never cut the prayer at the end."
        )
        store = creator_rules.load_rules_store(
            self.launch / "creator_rules.json"
        )
        self.assertEqual(
            creator_rules.active_rule_statements(store),
            ["Never cut the prayer at the end."],
        )

    def test_custom_rule_needs_words(self):
        base = self.start(WebAppConfig(None, launch_home=self.launch))
        status, _body = self.post_json(
            base, "/api/rule-custom", {"statement": "   "}
        )
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
