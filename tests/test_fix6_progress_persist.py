"""0.26 fix 6: the progress bubble must survive a reload.

The run's progress lives in the server thread as ONE entry
(kind "editor", a fixed id, a chat_progress payload), written
when Make the edit starts. Its stage updates rewrite that same
entry, and the Done payload replaces it under the same id, so
the thread never holds a trail of stage entries or a stale
progress entry beside the Done. The server stamps the start
time (started_at_ms) from its own clock when the entry is
first written; a stage update that does not name a start keeps
the original, and only an update naming one outright (Try
again, a new run in the same entry) moves it.
"""

import time
import unittest

from test_studio_thread_persist import ThreadPersistServer, _get, _post

_RUN_ID = "chatrun-intake1"
_PROGRESS = {
    "id": _RUN_ID,
    "kind": "editor",
    "status": "open",
    "text": "Working on it…",
    "chat_progress": {
        "intake_id": "intake1",
        "files": [{"name": "clip.mp4", "duration_s": 42.0}],
        "style": "My editing",
        "stage": "words",
        "stage_text": "Writing the words down…",
    },
}


def _run_entries(view):
    return [e for e in view["entries"] if e.get("id") == _RUN_ID]


class TestProgressEntryUpsert(unittest.TestCase):
    def test_server_stamps_the_start_from_its_own_clock(self):
        with ThreadPersistServer() as live:
            before = int(time.time() * 1000)
            status, data = _post(live.base, "/api/thread", dict(_PROGRESS))
            after = int(time.time() * 1000)
            self.assertEqual(status, 200, data)
            started = data["entry"]["chat_progress"]["started_at_ms"]
            self.assertIsInstance(started, int)
            self.assertGreaterEqual(started, before)
            self.assertLessEqual(started, after)

    def test_stage_updates_rewrite_the_same_entry(self):
        with ThreadPersistServer() as live:
            status, first = _post(live.base, "/api/thread", dict(_PROGRESS))
            self.assertEqual(status, 200, first)
            started = first["entry"]["chat_progress"]["started_at_ms"]
            created = first["entry"]["created_at"]
            update = dict(_PROGRESS)
            update["chat_progress"] = {
                **_PROGRESS["chat_progress"],
                "stage": "retakes",
                "stage_text": "Checking for retakes…",
            }
            status, data = _post(live.base, "/api/thread", update)
            self.assertEqual(status, 200, data)
            status, view = _get(live.base, "/api/thread")
            runs = _run_entries(view)
            self.assertEqual(len(runs), 1, view["entries"])
            entry = runs[0]
            self.assertEqual(entry["chat_progress"]["stage"], "retakes")
            self.assertEqual(
                entry["chat_progress"]["stage_text"],
                "Checking for retakes…",
            )
            # The start and the entry's place in the thread do
            # not move when a stage update lands.
            self.assertEqual(
                entry["chat_progress"]["started_at_ms"], started
            )
            self.assertEqual(entry["created_at"], created)

    def test_done_replaces_the_progress_entry_under_the_same_id(self):
        with ThreadPersistServer() as live:
            _post(live.base, "/api/thread", dict(_PROGRESS))
            status, data = _post(live.base, "/api/thread", {
                "id": _RUN_ID,
                "kind": "editor",
                "status": "settled",
                "text": "✅ Done in 0:31 · 12¢",
                "chat_done": {
                    "intake_id": "intake1",
                    "took_s": 31,
                    "cents": 12,
                    "retakes": 3,
                    "review_count": 0,
                },
            })
            self.assertEqual(status, 200, data)
            status, view = _get(live.base, "/api/thread")
            runs = _run_entries(view)
            self.assertEqual(len(runs), 1, view["entries"])
            entry = runs[0]
            self.assertIn("chat_done", entry)
            self.assertNotIn("chat_progress", entry)
            self.assertEqual(entry["chat_done"]["retakes"], 3)
            self.assertEqual(entry["status"], "settled")

    def test_a_named_start_moves_the_start_try_again(self):
        with ThreadPersistServer() as live:
            status, first = _post(live.base, "/api/thread", dict(_PROGRESS))
            started = first["entry"]["chat_progress"]["started_at_ms"]
            restart = dict(_PROGRESS)
            restart["chat_progress"] = {
                **_PROGRESS["chat_progress"],
                "interrupted": False,
                "started_at_ms": started + 60000,
            }
            status, data = _post(live.base, "/api/thread", restart)
            self.assertEqual(status, 200, data)
            status, view = _get(live.base, "/api/thread")
            runs = _run_entries(view)
            self.assertEqual(len(runs), 1)
            self.assertEqual(
                runs[0]["chat_progress"]["started_at_ms"], started + 60000
            )

    def test_a_plain_entry_with_the_same_id_is_not_merged(self):
        with ThreadPersistServer() as live:
            status, _ = _post(live.base, "/api/thread", {
                "id": _RUN_ID,
                "kind": "editor",
                "status": "settled",
                "text": "a plain editor note",
            })
            self.assertEqual(status, 200)
            status, _ = _post(live.base, "/api/thread", dict(_PROGRESS))
            self.assertEqual(status, 200)
            status, view = _get(live.base, "/api/thread")
            self.assertEqual(len(_run_entries(view)), 2)


class TestRunEntryMirrorsToLaunchThread(unittest.TestCase):
    """A run starts in the launch home's thread and the job's
    thread takes over at adoption. Writes made while the job
    is current must also land in the launch thread, or the
    next launch (which reads the launch thread first) would
    show the run frozen at its last pre-adoption stage."""

    def test_stage_and_done_writes_mirror_to_the_launch_thread(self):
        import tempfile
        from pathlib import Path

        from talk_to_your_editor import webapp
        from talk_to_your_editor.webapp import WebAppConfig

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "launch"
            job_root = Path(tmp) / "job"
            job_root.mkdir(parents=True)
            launch = WebAppConfig(None, launch_home=home)
            job = WebAppConfig(job_root, launch_home=home)

            webapp._append_thread(launch, dict(_PROGRESS))
            update = dict(_PROGRESS)
            update["chat_progress"] = {
                **_PROGRESS["chat_progress"],
                "stage": "retakes",
                "stage_text": "Checking for retakes…",
            }
            webapp._append_thread(job, update)
            launch_runs = [
                e for e in webapp._load_thread(launch)
                if e.get("id") == _RUN_ID
            ]
            self.assertEqual(len(launch_runs), 1)
            self.assertEqual(
                launch_runs[0]["chat_progress"]["stage"], "retakes"
            )

            webapp._append_thread(job, {
                "id": _RUN_ID,
                "kind": "editor",
                "status": "settled",
                "text": "✅ Done in 0:31 · 12¢",
                "chat_done": {"intake_id": "intake1", "took_s": 31},
            })
            for config in (launch, job):
                runs = [
                    e for e in webapp._load_thread(config)
                    if e.get("id") == _RUN_ID
                ]
                self.assertEqual(len(runs), 1)
                self.assertIn("chat_done", runs[0])
                self.assertNotIn("chat_progress", runs[0])


if __name__ == "__main__":
    unittest.main()
