"""Phase 2 chunk 1 (0.25): chat persistence, findings 4 and 5.

Finding 5a: the first message sent after the page opens, before
any job exists, must land in the server thread. It used to be
lost twice over: the no-job gate refused both thread write
routes, and GET /api/thread answered with a hard-coded empty
view. When a job then arrived, its fresh thread replaced the
launch session's, orphaning anything said at the front door.

Finding 5b: the chat-first bubbles (his sent video, the Done
bubble with its counts) are stored in the thread as entries
carrying chat_video / chat_done payloads, so a reload renders
them from the thread.

Finding 4 (server half): entries come back from the thread in
the order they were said, created_at first to last.
"""

import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from talk_to_your_editor.webapp import WebAppConfig, create_server

_KEY_VARS = (
    "MODEL_API_KEY",
    "ELEVENLABS_API_KEY",
    "BASETEN_API_KEY",
    "FIREWORKS_API_KEY",
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


class ThreadPersistServer:
    def __enter__(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        env = {"HOME": str(root / "home")}
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        for name in _KEY_VARS:
            os.environ.pop(name, None)
        launch = root / "launch"
        launch.mkdir()
        (root / "home").mkdir()
        self.root = root
        self.launch = launch
        config = WebAppConfig(None, launch_home=launch)
        self.config = config
        self.server = create_server(config, host="127.0.0.1", port=0)
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self._env.stop()
        self._tmp.cleanup()


class TestFirstSendPersistence(unittest.TestCase):
    def test_first_message_before_any_job_is_stored(self):
        with ThreadPersistServer() as live:
            status, job = _get(live.base, "/api/job")
            self.assertEqual(status, 200)
            self.assertFalse(job["has_job"])
            status, data = _post(
                live.base, "/api/thread/message",
                {"text": "hello editor, first message"},
            )
            self.assertEqual(status, 200, data)
            texts = [
                (e["kind"], e.get("text") or "")
                for e in data["entries"]
            ]
            self.assertIn(
                ("creator", "hello editor, first message"), texts
            )
            # It survives a fresh read (the reload case).
            status, view = _get(live.base, "/api/thread")
            self.assertEqual(status, 200)
            texts = [
                (e["kind"], e.get("text") or "")
                for e in view["entries"]
            ]
            self.assertIn(
                ("creator", "hello editor, first message"), texts
            )
            # And it is on disk in the launch session's thread.
            stored = json.loads(
                (live.launch / "tye-session" / "thread.json")
                .read_text()
            )
            stored_texts = [e.get("text") or "" for e in stored["entries"]]
            self.assertIn("hello editor, first message", stored_texts)

    def test_thread_append_stores_chat_payloads_in_order(self):
        with ThreadPersistServer() as live:
            status, _ = _post(live.base, "/api/thread", {
                "id": "chatvideo-test1",
                "kind": "creator",
                "status": "settled",
                "text": "same as always",
                "chat_video": {
                    "text": "same as always",
                    "files": [{"name": "clip.mp4", "duration_s": 42.0}],
                },
            })
            self.assertEqual(status, 200)
            status, _ = _post(live.base, "/api/thread", {
                "id": "chatdone-test1",
                "kind": "editor",
                "status": "settled",
                "text": "✅ Done in 0:31 · 12¢",
                "chat_done": {
                    "intake_id": "abc",
                    "took_s": 31,
                    "cents": 12,
                    "output_duration_s": 40.5,
                    "retakes": 3,
                    "silences": 5,
                    "fillers": 2,
                    "notes": 0,
                    "joins_checked": 9,
                    "joins_smooth": 9,
                    "your_call": [],
                    "review_count": 0,
                },
            })
            self.assertEqual(status, 200)
            status, view = _get(live.base, "/api/thread")
            self.assertEqual(status, 200)
            ids = [e["id"] for e in view["entries"]]
            self.assertEqual(
                ids, ["chatvideo-test1", "chatdone-test1"]
            )
            video = view["entries"][0]
            self.assertEqual(
                video["chat_video"]["files"][0]["name"], "clip.mp4"
            )
            done = view["entries"][1]
            self.assertEqual(done["chat_done"]["retakes"], 3)
            self.assertEqual(done["chat_done"]["cents"], 12)
            # Stored order is the order said: created_at never
            # runs backwards.
            stamps = [e["created_at"] for e in view["entries"]]
            self.assertEqual(stamps, sorted(stamps))

    def test_front_door_thread_carries_into_the_job(self):
        with ThreadPersistServer() as live:
            _post(
                live.base, "/api/thread/message",
                {"text": "hello editor, first message"},
            )
            _post(live.base, "/api/thread", {
                "id": "chatvideo-test1",
                "kind": "creator",
                "status": "settled",
                "text": "",
                "chat_video": {
                    "text": "",
                    "files": [{"name": "clip.mp4", "duration_s": 42.0}],
                },
            })
            status, before = _get(live.base, "/api/thread")
            before_ids = [e["id"] for e in before["entries"]]
            self.assertEqual(len(before_ids), 3)
            # A job arrives: the sample becomes the job, through
            # the same _become_job path a dropped folder takes.
            status, data = _post(live.base, "/api/load-sample", {})
            self.assertEqual(status, 200, data)
            status, job = _get(live.base, "/api/job")
            self.assertTrue(job["has_job"])
            status, view = _get(live.base, "/api/thread")
            ids = [e["id"] for e in view["entries"]]
            for carried in before_ids:
                self.assertIn(carried, ids)
            # Carried entries keep the order they were said in,
            # ahead of anything the job's own thread adds later.
            positions = [ids.index(i) for i in before_ids]
            self.assertEqual(positions, sorted(positions))
            stamps = [
                e["created_at"] for e in view["entries"]
                if e["id"] in before_ids
            ]
            self.assertEqual(stamps, sorted(stamps))
            # The job's own thread file holds them too.
            job_thread = (
                live.launch / "sample-job" / "tye-session" / "thread.json"
            )
            stored = json.loads(job_thread.read_text())
            stored_ids = [e["id"] for e in stored["entries"]]
            for carried in before_ids:
                self.assertIn(carried, stored_ids)
            # Becoming a job again does not duplicate them.
            status, data = _post(live.base, "/api/load-sample", {})
            self.assertEqual(status, 200, data)
            status, view = _get(live.base, "/api/thread")
            ids = [e["id"] for e in view["entries"]]
            for carried in before_ids:
                self.assertEqual(ids.count(carried), 1)


if __name__ == "__main__":
    unittest.main()
