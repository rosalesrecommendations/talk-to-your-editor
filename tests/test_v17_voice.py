"""v0.17: the restore said the natural way (0.16 conversation
test failure, carried into the 0.17 round at Justin's direction).

The 0.16 test battery's T1 ran Justin's own scripted flow in
the Editor chat: "remove the retakes", then "put them back
in", then "why did you remove this piece?". The middle ask
failed: "put them back in" was heard as a seek (go back five
seconds) because the navigation parser claims any sentence
carrying the word "back", and nothing was restored.

0.17: restore shapes are read before navigation. A restore
verb (put, bring, add, restore, get) with the thing that was
taken out (them, those, the retakes, everything) and "back",
with no time words anywhere, is keep_retakes. Sentences that
carry a time stay navigation; the settled one-word-object
phrasings keep their settled commands ("put it back" is undo,
"put that back in" is redo).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor.voice_commands import match_voice_command  # noqa: E402


def decide(text):
    result = match_voice_command(text)
    return result.get("decision"), result.get("command"), result.get("seconds")


class NaturalRestoreTests(unittest.TestCase):
    def test_put_them_back_in_restores_the_retakes(self):
        # The exact sentence the 0.16 conversation test failed on.
        self.assertEqual(
            decide("put them back in"), ("command", "keep_retakes", None)
        )

    def test_the_restore_family(self):
        for text in (
            "Put them back in.",
            "put them back",
            "bring them back",
            "bring those back",
            "add them back",
            "put those back in",
            "put the retakes back in",
            "put back the retakes",
            "restore them",
            "put everything back",
            "bring everything back",
            "put them all back in",
            "please put them back in",
            "get them back",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    decide(text), ("command", "keep_retakes", None)
                )

    def test_navigation_keeps_its_sentences(self):
        cases = {
            "go back five seconds": ("command", "seek_relative", -5.0),
            "go back": ("command", "seek_relative", -5.0),
            "back five seconds": ("command", "seek_relative", -5.0),
            "skip forward ten seconds": ("command", "seek_relative", 10.0),
            "go back to the start": ("command", "seek_start", 0),
            "go back one minute": ("command", "seek_relative", -60.0),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(decide(text), expected)

    def test_settled_single_object_phrasings_keep_their_commands(self):
        self.assertEqual(decide("put it back"), ("command", "undo", None))
        self.assertEqual(
            decide("put that back in"), ("command", "redo", None)
        )
        self.assertEqual(decide("put it back on"), ("command", "redo", None))
        self.assertEqual(
            decide("bring back the retakes"),
            ("command", "keep_retakes", None),
        )
        self.assertEqual(
            decide("remove the retakes"),
            ("command", "remove_retakes", None),
        )

    def test_a_time_word_keeps_a_restore_shape_out_of_the_restore(self):
        # "in five seconds" is a time, so this is not the plain
        # restore shape; it must not settle as keep_retakes.
        decision, command, _seconds = decide("put them back in five seconds")
        self.assertNotEqual(command, "keep_retakes")


if __name__ == "__main__":
    unittest.main()
