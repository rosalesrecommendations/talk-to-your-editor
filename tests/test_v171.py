"""Tests for v0.17.1: the stronger way of working is named
Proactive (Justin's word, Nov 1 2026), not Strictest.

The rename is label-only: the server's mode values stay
"cleanest" and "tailor", and "Leave more in" keeps its name.
These tests pin the new label everywhere a user sees it (the
recap lines, the mode pill in both controls, the front door's
validation message) and pin that the old name is gone from
the shipped page.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import webapp
from talk_to_your_editor.webapp import WebAppConfig

TEMPLATE = (
    ROOT / "src/talk_to_your_editor/templates/test_app.html"
).read_text(encoding="utf-8")


class ProactiveLabelTests(unittest.TestCase):
    def test_mode_labels_use_the_new_name(self):
        self.assertEqual(webapp.MODE_LABELS["cleanest"], "Proactive")
        self.assertEqual(webapp.MODE_LABELS["tailor"], "Leave more in")

    def test_internal_mode_values_are_unchanged(self):
        self.assertEqual(
            webapp.INVOLVEMENT_MODES, ("cleanest", "tailor")
        )

    def test_the_pill_says_proactive_in_both_controls(self):
        self.assertEqual(TEMPLATE.count("Proactive (recommended)"), 2)
        self.assertEqual(
            TEMPLATE.count(
                'data-mode="cleanest" aria-pressed="true">Proactive<'
            ),
            2,
        )
        self.assertEqual(
            TEMPLATE.count('data-mode="tailor"'), 2
        )

    def test_the_old_name_is_gone_from_the_page(self):
        self.assertNotIn("Strictest", TEMPLATE)

    def test_front_door_validation_names_proactive(self):
        job = Path(tempfile.mkdtemp(prefix="tye-v171-")) / "job"
        job.mkdir(parents=True)
        (job / "transcript.json").write_text(json.dumps({
            "engine": "test",
            "duration": 0.4,
            "words": [{"word": "hi", "start": 0.0, "end": 0.4}],
        }), encoding="utf-8")
        (job / "cuts.json").write_text(json.dumps({
            "source_duration_s": 0.4,
            "kept_ranges": [{"start": 0.0, "end": 0.4}],
        }), encoding="utf-8")
        config = WebAppConfig(job)
        with self.assertRaises(ValueError) as caught:
            webapp._run_front_door(config, {"mode": "bogus"})
        self.assertEqual(
            str(caught.exception), "Choose Proactive, or Leave more in."
        )


if __name__ == "__main__":
    unittest.main()
