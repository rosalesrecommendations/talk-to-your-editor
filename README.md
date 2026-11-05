# Talk to Your Editor

An editor that learns how you cut, from your own before and after.

You talk to it like texting a person. It cuts your next video your way, and every finished video teaches it more of your judgment, so the longer you use it, the less you have to fix. Nothing is final until you say so.

## The loop

1. **Teach it.** Give it a before and an after, your raw video and your finished edit, by file or by link. A finished video can teach it too.
2. **It tells you what it noticed.** In plain words: you cut the retakes, you kept the last take, you trim long pauses. Not a list of every cut. It asks before it saves anything.
3. **It edits your next video your way.** Retakes out, long gaps trimmed, joins placed on the room sound, never mid-word. Likely retakes are cut and shown as spots you can bring back with one tap; genuine judgment calls wait as spots for your review.
4. **Finish where you want.** Finish inside the app, or export the edit as a timeline file to the program you already use.

## Muse proposes, code decides

Muse Spark proposes the changes. Muse Wonder reviews the risky calls: restores, take swaps, long cuts, anything near the opening or closing. Deterministic checks decide what is safe to apply. Anything that fails is flagged for you with the reason, never applied quietly.

The model may only speak five operations:

| Operation | Meaning |
| --- | --- |
| `cut_words` | Remove an exact span of kept words. |
| `restore_words` | Restore an exact span of currently cut words. |
| `set_join_pause` | Set the pause at one numbered join, between 0.08 and 0.50 seconds, bounded by the neighboring speech. |
| `replace_take` | Atomically swap one take for another: restore one span, cut another, both or neither. |
| `flag_for_review` | Make no change; a person decides. |

## Run it

```bash
python3 -m pip install -e .
tye serve
```

Open http://127.0.0.1:8765; Studio opens on the conversation. Set MODEL_API_KEY for live Muse direction; without a key, the app runs offline with a deterministic mock and the same checks. The optional `.[speech]` extra adds local transcription (Whisper, running on your computer).

## The proof it learns

The demo is one real video: a 10-minute raw talking-head recording, and the creator's own finished edit of it at 165.4 seconds.

- The app's edit runs 175.6 seconds before review.
- Transcribe both edits and diff them: 667 of his 692 words match his finished edit, 96.4 percent.
- Said plainly: the style pack's calibration examples all come from this same video, so that score is measured on the video the app learned from. It is not a general accuracy number. On a new video there is no score; it is tested by watching the edit.
- The held-out test is a second video, Day 48, which the app never learned from: on a video it never learned from, it kept about 90% of my words, with no dead air, in about 3:15 against my 2:42.
- Six short spoken lines an earlier build dropped are back in. The stopped, abandoned takes are out.
- Two spots are left for his review, both passages that make the same point twice. Approving them lands the length on his edit.

That gap, and the spots, are the point: the app gets most of the way there and asks about the rest.

## Why creators cancel editing tools

Review mining of the tools creators leave (Descript, Gling, Recut, TimeBolt) keeps surfacing the same complaints. Each has an answer here:

- **Cuts that delete real words.** A deterministic verifier, a negation guard, and protected words stand between any proposal and your edit.
- **Hard, choppy cuts.** Cuts sit on measured room sound, breaths stay whole or go whole, and a join check flags any join that fails a real measurement.
- **A transcript you cannot trust.** Word timings are re-measured from the audio itself, so the word list follows the sound before anything downstream reads it.
- **Lost work.** Every change is a saved step, with its own undo and redo. The Done bubble carries Undo for the whole edit, and Export saves a reopenable project file.
- **Exports that fail or need another editor.** Timeline files for Premiere Pro, Final Cut Pro, and DaVinci Resolve, read back and counted against the plan.
- **Being charged for the AI's mistakes.** Every run shows its real cost before you tap. The direction we are building toward: never charge for the editor's own mistakes.

## Honest limits

- **CapCut.** CapCut has no timeline format to hand over. The app says so plainly: download the finished video and bring it into CapCut.
- **What is learned.** Rules drawn from your own graded work, shown to you first and saved only when you say yes. The hosted Muse models are not trained or fine-tuned by this project.
- **What leaves the computer.** Rendering and media stay on your machine. With a Meta key, direction sends your message text plus a small window of transcript words, not the video. Transcription runs on your machine with the speech extra, or through ElevenLabs if you set that key. With no keys, the app runs fully offline.
- **Not in this build yet.** Music beds and burned-in captions. The exported file does carry a subtitle track.

## Next

- **Style match score.** A number that shows how well the editor matches your taste, and grows as it learns: onboarding answers (what you make, your niche, your account type), each teach pair, each saved rule, and each review tap raise it. The Done bubble shows it as "matched your style 89%, up from 82%", computed from real agreement wherever a finished edit exists to measure against. Later: a share card carrying the score and an invite link, and a waitlist with handles and rank for the launch page.

## License

Copyright 2026 Practice Your Passion LLC. All rights reserved. This repository is public so the hackathon judges can read and run the code; no open-source license is granted.

## Foundation disclosure

This repository was built during the Meta Global AI Developer Hackathon (October 26 to November 4, 2026), Startup track, theme Build for Your World, by team Rosales Recommendations (Practice Your Passion LLC).

Disclosure, plainly: the direction layer in this repository is new work from the event window. It plugs into a local editing runner that existed before the event: that runner transcribes a raw talking-head video, makes the first-pass edit under the creator's learned rules, renders the finished formats, and generates the review page that exports the feedback file. The runner, its rule set, its test fixtures from real videos, and the creator's footage are pre-existing Practice Your Passion LLC work. They are not in this repository, they are not claimed as event work, and the demo focuses on the new layer: feedback becoming verified, applied edits. Interfaces between the two are the file formats documented above.

One more disclosure, just as plainly: the creator also maintains a separate, pre-existing personal editing system of his own. That system is not part of this project, none of its code is in this repository, and nothing in this repository was taken from it. The intake in this project, the part that takes a dropped raw video and makes its first edit (version 0.6.0), was designed and written fresh for this project, inside the event window, from the file formats documented above.

## Safety and data notes

- Standard-tier Muse models only. The discounted contributor-tier model ids, whose prompts may be used for training, are rejected in code for creator content.
- The API key is read from the `MODEL_API_KEY` environment variable and is never written to code, files, or logs.
- With the Meta provider, the material sent to the API is the comment text plus a small window of transcript words around it, not the video. Rendering and media handling stay local.
- The verifier's protected-span and negation guards exist because an edit that changes what a sentence says is worse than no edit. When in doubt, the system flags and the creator decides.

