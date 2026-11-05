"""v0.17, feedback item 3: a YouTube link must bring the video in.

Justin's words, trying to teach the app from his own uploaded
video: "It should be able to do that from YouTube. I need you
to fix that." The watch page names no video file in its meta
tags, so the old page read found nothing. The fix reads the
player data the page itself carries.

Everything here is offline: the page fetch is replaced with
fixture pages, downloads are replaced with a writer that
records the address it was handed, and the join step is
replaced where a pair of streams is involved. Covered:

- watch, youtu.be, and shorts links all resolve to the video;
- a ready-made stream lands as linked.mp4;
- separate picture and sound streams are downloaded and joined;
- a private video answers with the plain sign-in message and
  the YouTube Studio fallback;
- a page with no playable data, and a page whose streams carry
  no direct address, answer plainly instead of silently failing;
- a non-YouTube link still takes the old path (the generic
  failure message, untouched).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talk_to_your_editor import teaching

VIDEO_ID = "dQw4w9WgXcQ"


def page_with(player: dict) -> str:
    return (
        "<html><script>var ytInitialPlayerResponse = "
        + json.dumps(player)
        + ";</script></html>"
    )


def ok_player(**streaming) -> dict:
    return {
        "playabilityStatus": {"status": "OK"},
        "streamingData": streaming,
        "videoDetails": {"title": "A test video"},
    }


class FakeDownload:
    def __init__(self):
        self.calls = []

    def __call__(self, url, dest, user_agent="TalkToYourEditor"):
        self.calls.append((url, Path(dest).name))
        Path(dest).write_bytes(b"bytes-from:" + url.encode("utf-8"))
        return Path(dest)


class YouTubeFetchTests(unittest.TestCase):
    def run_fetch(self, url, player):
        fake = FakeDownload()
        with mock.patch.object(
            teaching, "_fetch_youtube_page",
            return_value=page_with(player),
        ) as page_fetch, mock.patch.object(
            teaching, "_download", fake
        ):
            with tempfile.TemporaryDirectory() as tmp:
                landed = teaching.fetch_link(url, Path(tmp))
                landed_bytes = landed.read_bytes()
        return fake, page_fetch, landed, landed_bytes

    def test_watch_link_lands_the_ready_made_stream(self):
        player = ok_player(formats=[
            {"url": "https://streams.example/low", "mimeType": "video/mp4",
             "height": 360, "contentLength": "1000"},
            {"url": "https://streams.example/high", "mimeType": "video/mp4",
             "height": 720, "contentLength": "2000"},
        ])
        fake, page_fetch, landed, data = self.run_fetch(
            f"https://www.youtube.com/watch?v={VIDEO_ID}", player
        )
        page_fetch.assert_called_once_with(VIDEO_ID)
        self.assertEqual(landed.name, "linked.mp4")
        # The taller picture wins.
        self.assertEqual(fake.calls[0][0], "https://streams.example/high")
        self.assertEqual(data, b"bytes-from:https://streams.example/high")

    def test_short_and_shorts_links_resolve_to_the_same_video(self):
        player = ok_player(formats=[
            {"url": "https://streams.example/only",
             "mimeType": "video/mp4", "height": 360},
        ])
        for url in (
            f"https://youtu.be/{VIDEO_ID}?si=share",
            f"https://www.youtube.com/shorts/{VIDEO_ID}",
            f"https://www.youtube.com/watch?v={VIDEO_ID}&t=42s",
        ):
            with self.subTest(url=url):
                _fake, page_fetch, landed, _data = self.run_fetch(
                    url, player
                )
                page_fetch.assert_called_once_with(VIDEO_ID)
                self.assertEqual(landed.name, "linked.mp4")

    def test_separate_picture_and_sound_are_joined(self):
        player = ok_player(adaptiveFormats=[
            {"url": "https://streams.example/v360",
             "mimeType": "video/mp4; codecs=\"avc1\"", "height": 360},
            {"url": "https://streams.example/v720",
             "mimeType": "video/mp4; codecs=\"avc1\"", "height": 720},
            {"url": "https://streams.example/a1",
             "mimeType": "audio/mp4; codecs=\"mp4a\"", "bitrate": 128000},
            {"url": "https://streams.example/noaddr",
             "mimeType": "video/webm", "height": 1080,
             "signatureCipher": "encoded"},
        ])

        def fake_merge(video_path, audio_path, dest):
            Path(dest).write_bytes(
                Path(video_path).read_bytes()
                + b"+"
                + Path(audio_path).read_bytes()
            )
            return Path(dest)

        fake = FakeDownload()
        with mock.patch.object(
            teaching, "_fetch_youtube_page",
            return_value=page_with(player),
        ), mock.patch.object(teaching, "_download", fake), \
                mock.patch.object(
                    teaching, "_merge_youtube_parts",
                    side_effect=fake_merge,
                ):
            with tempfile.TemporaryDirectory() as tmp:
                landed = teaching.fetch_link(
                    f"https://www.youtube.com/watch?v={VIDEO_ID}",
                    Path(tmp),
                )
                data = landed.read_bytes()
                leftovers = sorted(
                    p.name for p in Path(tmp).iterdir()
                )
        self.assertEqual(landed.name, "linked.mp4")
        self.assertEqual(
            [call[0] for call in fake.calls],
            ["https://streams.example/v720",
             "https://streams.example/a1"],
        )
        self.assertEqual(
            data,
            b"bytes-from:https://streams.example/v720"
            b"+bytes-from:https://streams.example/a1",
        )
        # The part files are cleaned up after the join.
        self.assertEqual(leftovers, ["linked.mp4"])

    def test_private_video_answers_plainly(self):
        player = {
            "playabilityStatus": {
                "status": "LOGIN_REQUIRED",
                "reason": "This video is private",
            }
        }
        with mock.patch.object(
            teaching, "_fetch_youtube_page",
            return_value=page_with(player),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(
                    teaching.TeachingLinkError
                ) as raised:
                    teaching.fetch_link(
                        f"https://www.youtube.com/watch?v={VIDEO_ID}",
                        Path(tmp),
                    )
        message = str(raised.exception)
        self.assertIn("private or needs a sign-in", message)
        self.assertIn("YouTube Studio", message)

    def test_encoded_only_streams_answer_plainly(self):
        player = ok_player(adaptiveFormats=[
            {"mimeType": "video/mp4", "height": 720,
             "signatureCipher": "encoded"},
            {"mimeType": "audio/mp4", "signatureCipher": "encoded"},
        ])
        with mock.patch.object(
            teaching, "_fetch_youtube_page",
            return_value=page_with(player),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(
                    teaching.TeachingLinkError
                ) as raised:
                    teaching.fetch_link(
                        f"https://youtu.be/{VIDEO_ID}", Path(tmp)
                    )
        self.assertIn("did not offer that video", str(raised.exception))

    def test_page_without_player_data_answers_plainly(self):
        with mock.patch.object(
            teaching, "_fetch_youtube_page",
            return_value="<html><body>consent wall</body></html>",
        ):
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(
                    teaching.TeachingLinkError
                ) as raised:
                    teaching.fetch_link(
                        f"https://www.youtube.com/watch?v={VIDEO_ID}",
                        Path(tmp),
                    )
        self.assertIn("did not give the app", str(raised.exception))

    def test_non_youtube_link_keeps_the_old_path(self):
        # A lookalike host is not YouTube; the generic page read
        # runs and fails with the generic message, untouched.
        with mock.patch.object(
            teaching, "_fetch_page",
            return_value="<html><body>no video</body></html>",
        ):
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(
                    teaching.TeachingLinkError
                ) as raised:
                    teaching.fetch_link(
                        "https://notyoutube.example/watch?v=" + VIDEO_ID,
                        Path(tmp),
                    )
        self.assertEqual(
            str(raised.exception),
            "The app could not get that video from the link.",
        )


if __name__ == "__main__":
    unittest.main()
