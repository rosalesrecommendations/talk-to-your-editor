"""0.27.5 keyed-path round: the creator home, and the
export-finish chime.

Styles and rules live in ~/.talk-to-your-editor, not in the
kit folder: unzipping a new kit (a new launch home) keeps
them, and a store saved inside an old kit's launch home is
copied over on first use. The export's finish lands its
chat message as before and now also plays a short Web Audio
chime; speechSynthesis stays at zero.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talk_to_your_editor import webapp  # noqa: E402
from talk_to_your_editor.webapp import WebAppConfig  # noqa: E402


class CreatorHomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="tye-home-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self._env = mock.patch.dict(
            os.environ, {"HOME": str(self.home)}
        )
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_styles_live_in_the_creator_home_not_the_kit(self):
        kit_a = self.tmp / "kit-a"
        config_a = WebAppConfig(None, launch_home=kit_a)
        webapp._save_styles(
            config_a,
            {"styles": [{"name": "My style"}], "current": "My style"},
        )
        saved = self.home / ".talk-to-your-editor" / "styles.json"
        self.assertTrue(saved.is_file())
        self.assertFalse((kit_a / "styles.json").exists())
        # A fresh kit folder (a new launch home) still loads it.
        kit_b = self.tmp / "kit-b"
        config_b = WebAppConfig(None, launch_home=kit_b)
        loaded = webapp._load_styles(config_b)
        self.assertEqual(loaded["current"], "My style")
        self.assertIn(
            "My style", [s["name"] for s in loaded["styles"]]
        )

    def test_rules_path_is_the_creator_home(self):
        config = WebAppConfig(None, launch_home=self.tmp / "kit")
        self.assertEqual(
            webapp._creator_rules_path(config),
            self.home / ".talk-to-your-editor" / "creator_rules.json",
        )

    def test_a_store_stranded_in_an_old_kit_is_copied_over(self):
        kit = self.tmp / "old-kit"
        kit.mkdir()
        (kit / "styles.json").write_text(
            '{"styles": [{"name": "Stranded"}], '
            '"current": "Stranded"}'
        )
        config = WebAppConfig(None, launch_home=kit)
        loaded = webapp._load_styles(config)
        self.assertEqual(loaded["current"], "Stranded")
        copied = self.home / ".talk-to-your-editor" / "styles.json"
        self.assertTrue(copied.is_file())


class ExportChimeTests(unittest.TestCase):
    def test_the_finish_plays_a_chime_and_never_a_voice(self):
        html = (
            Path(__file__).resolve().parents[1]
            / "src" / "talk_to_your_editor" / "templates" / "studio.html"
        ).read_text(encoding="utf-8")
        self.assertIn("function playExportChime(", html)
        self.assertIn("AudioContext", html)
        done = html.split("function paintExportDone", 1)[1]
        self.assertIn("playExportChime();", done[:200])
        self.assertIn("Exported to Downloads:", html)
        self.assertNotIn("speechSynthesis", html)


if __name__ == "__main__":
    unittest.main()
