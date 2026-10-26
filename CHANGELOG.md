# Changelog

## 0.2.1 - 2026-10-26

- Mac launcher: remembered Meta Model API key, on this Mac only. After a one-time paste at the hidden prompt, answering yes to "Remember this key on this Mac only?" saves the key to a private file in the home folder (`~/.talk-to-your-editor/meta_key`, folder 700, file 600); later launches use it without asking and say so. The key never enters the app folder, the kit, the zip, or the repo, and deleting that one file removes it. Declining keeps the old session-only behavior.
- Testing guide: documented the remembered-key flow and where the file lives, and added the Finder drag trick for the job prompt (drag a runner job folder from Finder into the Terminal window to paste its path).

## 0.2.0 - 2026-10-26

Personal Test Edition. Verified before release: 52 tests passing, and the
Mac kit rehearsed end to end from a clean unzip in a path containing
spaces (launcher install, sample job, direction run, after-review served).

- Added a local web test app for trying the feedback-to-edit flow in a browser.
- Added a Mac test kit with a double-click launcher for sample and real runner jobs.
- Added phone testing on the same Wi-Fi through the address printed by the launcher.
- Added a plain-language personal testing guide covering mock direction, optional live Muse direction, after-review grading, and graded-export feedback.

## 0.1.0 - 2026-10-26

Published first slice.

- Added the feedback-to-edit direction layer for timestamped creator comments.
- Added timeline mapping from finished-edit timestamps back to source words and edit-plan ranges.
- Added a strict five-operation edit language: `cut_words`, `restore_words`, `replace_take`, `set_join_pause`, and `flag_for_review`.
- Added Muse Spark direction with Muse Wonder review for higher-risk changes.
- Added deterministic verification, protected-span safeguards, an edit-plan applier, and an audit record.
- Added the after-review grading page and graded-correction export.
- Added candidate-rule learning from repeated graded patterns, with every candidate left unapproved for creator review.
- Added fictional examples, tests, and a foundation disclosure explaining which pre-existing runner work is outside this repository.
