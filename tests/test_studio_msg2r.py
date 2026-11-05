"""Message 2R (brief 8): named styles, rule editing in the
rules view, and honest teaching links.

Styles live in styles.json in the launch home. Rules saved
before styles existed carry no style and read as the default
style's, so nothing from 0.21/0.22 is lost. Teaching links:
other people's YouTube, Instagram, TikTok, and Facebook videos
are never downloaded; Drive and Dropbox share links resolve to
the file itself.
"""

import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from talk_to_your_editor import teaching
from talk_to_your_editor.webapp import (
    CREATOR_RULES_FILENAME,
    STYLE_YOUTUBE_MESSAGE,
    WebAppConfig,
    create_server,
)


def _post(base, path, payload):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw or "{}")
        except ValueError:
            return exc.code, {"raw": raw}


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode() or "{}")


class StudioMsg2rServer:
    def __enter__(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"HOME": str(root / "home")})
        self._env.start()
        launch = root / "launch"
        launch.mkdir()
        (root / "home").mkdir()
        config = WebAppConfig(None, launch_home=launch)
        self.config = config
        self.server = create_server(config, host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self._env.stop()
        self._tmp.cleanup()


class TestStyles(unittest.TestCase):
    def test_default_style_and_new_style_flow(self):
        with StudioMsg2rServer() as live:
            status, data = _get(live.base, "/api/job")
            self.assertEqual(status, 200)
            self.assertEqual(data["styles"]["current"], "My editing")
            status, data = _post(live.base, "/api/styles", {"action": "new", "name": "Wedding films"})
            self.assertEqual(status, 200)
            self.assertEqual(data["styles"]["current"], "Wedding films")
            names = [s["name"] for s in data["styles"]["styles"]]
            self.assertEqual(names, ["My editing", "Wedding films"])

    def test_rule_saved_under_current_style_and_rename_retags(self):
        with StudioMsg2rServer() as live:
            _post(live.base, "/api/styles", {"action": "new", "name": "Wedding films"})
            status, data = _post(
                live.base, "/api/rule-custom",
                {"statement": "Always keep the vows in full"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(
                data["saved_rules"][0]["statement"], "Always keep the vows in full"
            )
            status, data = _get(live.base, "/api/job")
            counts = {s["name"]: s["rules"] for s in data["styles"]["styles"]}
            self.assertEqual(counts.get("Wedding films"), 1)
            status, data = _post(
                live.base, "/api/styles",
                {"action": "rename", "name": "Wedding films", "new_name": "Weddings"},
            )
            self.assertEqual(status, 200)
            status, data = _get(live.base, "/api/job")
            counts = {s["name"]: s["rules"] for s in data["styles"]["styles"]}
            self.assertEqual(counts.get("Weddings"), 1)

    def test_old_rules_without_style_still_listed(self):
        with StudioMsg2rServer() as live:
            path = live.config.launch_home / CREATOR_RULES_FILENAME
            path.write_text(json.dumps({
                "version": 1,
                "rules": [{
                    "rule_id": "rule-old1", "statement": "Keep the breath",
                    "pattern_key": "old1", "active": True,
                }],
            }))
            status, data = _get(live.base, "/api/job")
            self.assertEqual(status, 200)
            old = [r for r in data["saved_rules"] if r["rule_id"] == "rule-old1"]
            self.assertEqual(len(old), 1)
            self.assertEqual(data["styles"]["styles"][0]["rules"], 1)

    def test_select_unknown_style_fails(self):
        with StudioMsg2rServer() as live:
            status, data = _post(live.base, "/api/styles", {"action": "select", "name": "Nope"})
            self.assertEqual(status, 400)


class TestRuleEdit(unittest.TestCase):
    def test_edit_rule_words_in_place(self):
        with StudioMsg2rServer() as live:
            status, data = _post(
                live.base, "/api/rule-custom", {"statement": "Keep the first words"}
            )
            rule_id = data["saved_rules"][0]["rule_id"]
            status, data = _post(
                live.base, "/api/rule-edit",
                {"rule_id": rule_id, "statement": "Keep the opening words"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(data["saved_rules"][0]["statement"], "Keep the opening words")

    def test_edit_unknown_rule_fails(self):
        with StudioMsg2rServer() as live:
            status, _ = _post(
                live.base, "/api/rule-edit",
                {"rule_id": "rule-nope", "statement": "Anything"},
            )
            self.assertEqual(status, 400)


class TestTeachingLinks(unittest.TestCase):
    def _begin(self, base, kind):
        status, data = _post(base, "/api/teach/begin", {"kind": kind})
        self.assertEqual(status, 200)
        return data["teach_id"]

    def test_social_links_refused_plainly(self):
        with StudioMsg2rServer() as live:
            teach_id = self._begin(live.base, "style")
            for url in (
                "https://www.instagram.com/reel/abc/",
                "https://www.tiktok.com/@x/video/1",
                "https://www.facebook.com/watch/?v=1",
            ):
                status, data = _post(
                    live.base, "/api/teach/link",
                    {"teach_id": teach_id, "url": url},
                )
                self.assertEqual(status, 200, url)
                self.assertFalse(data["landed"], url)
                self.assertIn("cannot download", data["message"])

    def test_style_youtube_refused_with_studio_path(self):
        with StudioMsg2rServer() as live:
            teach_id = self._begin(live.base, "style")
            status, data = _post(
                live.base, "/api/teach/link",
                {"teach_id": teach_id, "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            )
            self.assertEqual(status, 200)
            self.assertFalse(data["landed"])
            self.assertEqual(data["message"], STYLE_YOUTUBE_MESSAGE)

    def test_direct_download_urls(self):
        self.assertEqual(
            teaching.direct_download_url(
                "https://drive.google.com/file/d/FILEID123/view?usp=sharing"
            ),
            "https://drive.google.com/uc?export=download&id=FILEID123",
        )
        self.assertEqual(
            teaching.direct_download_url(
                "https://www.dropbox.com/s/abc/clip.mp4?dl=0"
            ),
            "https://dl.dropboxusercontent.com/s/abc/clip.mp4?dl=1",
        )
        # Anything else is not a share link; the caller reads it
        # as pasted.
        self.assertIsNone(
            teaching.direct_download_url("https://example.com/clip.mp4")
        )


class TestStudioMsg2rMarkers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.html = (root / "src/talk_to_your_editor/templates/studio.html").read_text()

    def test_chat_first_markers(self):
        for marker in (
            'id="screenHome"', 'id="navHome"', 'id="videoList"',
            'id="styleBtn"', 'id="styleMenu"', 'id="rulesLink"',
            'id="addRuleLink"', 'id="stageRow"', 'id="plusBtn"',
            'id="rulesOverlay"', 'id="playerOverlay"', 'id="popVideo"',
            'id="backChat1"', 'id="tbBack"', "__tyeStagedSend",
            "__tyeOnEditDone", "Export in the chat",
            "are never downloaded",
        ):
            self.assertIn(marker, self.html, marker)

    def test_no_editor_speech_and_no_option_dictation(self):
        self.assertNotIn("speechSynthesis", self.html)
        self.assertNotIn('e.key === "Alt"', self.html)
        self.assertNotIn('e.code === "AltLeft"', self.html)

    def test_buttons_never_wrap(self):
        self.assertIn("button { white-space: nowrap; }", self.html)


if __name__ == "__main__":
    unittest.main()
