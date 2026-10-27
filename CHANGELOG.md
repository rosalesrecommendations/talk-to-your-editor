# Changelog

## 0.3.1 - 2026-10-27

A night of fixes from the audit pass: everything the page says is now plainer, playback is safer, and a session can be walked back.

- Plain words everywhere: every result and every held note is explained in plain language, on the test page and the grading page alike. Internal check codes and machine wording no longer appear anywhere a viewer can see them; a held note says what protected it and why, in a sentence a person would say.
- Browser-safe video: the app now recognizes the common video files a runner job can hold (.mp4, .mov, .m4v), and when a file is in a format a browser cannot play, it quietly makes a converted playback copy inside the session folder and plays that instead. The original file is never touched. If a video truly cannot be played, the player says so in one plain sentence instead of loading forever.
- Rendering sets itself up: when the job folder already carries the pieces rendering needs (the render script, the cleaned audio, the original video), the app finds and uses them on its own. When a piece is missing, the message is one plain sentence naming what is missing, not a list of machine names.
- Undo, redo, and History: a new History card records every change in the session, and Undo and Redo step back and forward through them, so nothing done in a session is ever lost or final until you say so.
- Direction builds on your session: a direction run now starts from the edit as your session currently has it, including words you cut or restored by clicking in the transcript, instead of resetting to the job's original plan. Each run is kept as its own round in the session, so earlier runs are never overwritten.
- Quieter server logs: when a browser stops a video stream mid-flight, the server no longer prints a long error trace for it. Real errors still show.
- Launcher speaks plainly: the Mac launcher now opens with the part 1 and part 2 map (the runner makes the first edit; this app directs the fixes), and if a video file is pasted where a job folder belongs, it says that is a file, not a folder, instead of calling the folder missing.

## 0.3.0 - 2026-10-27

The feedback release: the test page learned to work the way a creator actually gives notes. Built piece by piece from a live test session on a real edit.

- Timeline and notes: a timeline bar under the video carries a dot for every note; hover or tap a dot to read it, click to jump there, and drag the playhead to the exact second. A note now pins itself to the playhead the moment typing starts, typing pauses the video (with a keep-playing switch), and a note can never inherit a stale time from the note before it.
- Direction runs always finish: when two notes collide on the same words, the first edit applies and the later one is held with a plain reason on its own note. Failure messages are written in plain words; internal codes stay in the server log. The action buttons say what they do: "Build the fixed video" and "Grade the changes."
- Transcript view: the edit's words on screen, kept words normal and cut words struck through and dimmed. Clicking a word cuts it or brings it back, through the same safety checks as any other change.
- Word corrections: fix a word the transcript heard wrong, split one word into the two or three it should be, or merge pieces back into one word. Corrections change the text and the captions built from it; the sound and the timing never change. Corrected words are marked, every correction can be removed, and direction reads the corrected text.
- Comment scope and overall feedback: a note applies to both cuts of a video unless it is aimed at Vertical only or Landscape only, and a separate Overall feedback box holds notes about the whole video that are not tied to a moment.
- Honest formats: the Vertical and Landscape buttons only appear for cuts the job actually has, the player never crops the picture, shows a loading state instead of a black box, and says in one plain sentence when a cut is missing.
- Sign-in check on the page: the test page shows whether a Meta sign-in is set, checks it with one harmless call so a bad key is caught before a run, and accepts a pasted key for the session only. It is never written to disk or shown back.
- Visual polish: the page was tightened toward a clean, calm, rounded look in one blue family, with the developer links tucked behind an "Advanced: session files" section.

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
