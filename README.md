# Talk to Your Editor

A conversational AI editing director for talking-head creators: describe a video fix in plain words, and the program applies the edit and re-renders it for review.

You watch the finished edit, pin comments to the moments you want changed ("remove this repeated line", "make the pause here longer", "bring back the first take of that sentence"), and export them as one structured feedback file. Talk to Your Editor reads that file, works out exactly which words each comment points at, asks Muse Spark (and, for risky calls, Muse Wonder) what should change, checks every proposal against a deterministic safety verifier, applies the approved changes to the edit plan, and renders a revised edit. A second review page then shows you, comment by comment, what was done, and your verdict on each change becomes a graded learning record.

## Why

Creators do not think in timelines and cut points. They think in sentences: "that part repeats", "that pause feels long", "use the other take". The gap between that language and a finished edit is where editing time goes. This project closes the gap with a strict contract: the model may only speak a small language of typed edit operations, and deterministic code, not the model, decides what is safe to apply.

## How it works

1. **Feedback in.** The editing runner's review page exports a feedback JSON file: each comment carries its timestamp on the finished edit, the format it belongs to, and its text.
2. **Timeline mapping.** Finished-edit timestamps are not source timestamps. The direction layer walks the edit plan's kept ranges in output order and maps each comment back to the source time and the exact transcript word it points at, including comments that land after material was cut.
3. **Direction.** Muse Spark proposes operations for ordinary comments. Muse Wonder independently reviews anything risky: restores, take swaps, long cuts, opening or closing passages, and low-confidence proposals. Wonder receives the primary evidence plus the Spark proposal as the claim under review.
4. **Verification.** Deterministic checks run before any change: schema, word boundaries, protected spans, negation guard, frame-grid alignment, overlap and conflict detection, and a cap on how much of the edit one batch may remove. Anything that fails is flagged for the creator with the named reason, never silently applied.
5. **Application.** Approved operations rewrite the edit plan. The original plan is never modified. Tiny word-free slivers left by a cut are absorbed into the cut and recorded in the audit, the same rule the underlying planner uses.
6. **After-review.** A fresh page lists every comment exactly once, what was done about it, and a verdict control (worked, partly, did not work) with notes. The exported grades are the learning record.
7. **Learning.** Repeated graded patterns become candidate rules in a draft creator template. Candidates are always marked unapproved; a person promotes them. This is inference over records, not model training, and the draft says so.

## The test app in 0.4: the simple front door

The local test app now opens on the whole workflow in two choices: **Remove retakes** and **Cut silence**, both on by default, with one silence buffer (0.1 seconds to start) and one button. Everything else, format scope and a spoken-direction box, sits behind a single More choices fold. Comment times are treated as estimates and resolved to the nearest words within a few seconds. The transcript itself explains every removal: a dot on each removed span opens the plain reason, and a note left there is stored against that edit and carried into the next run labeled with what was removed. When graded changes repeat a pattern, the app offers it once as a rule in a Your style card; a saved rule rides into every later run as standing direction, and nothing is adopted without the creator's tap.

## The first edit, from a dropped video (0.6.0)

The app can also start from nothing but a raw video. Drop one video file and the card offers **Make the first edit**, with a box for direction up front ("remove retakes, last take is the best take" sets that run's switches, and the recap names what the words set). The app transcribes the video with word timings, measures speech and silence from the waveform itself, builds a first plan with every word kept, and runs the same deterministic front door a runner job gets. The review video is a fresh, simple splice of the kept ranges, rendered by the app. Transcription route, picked on measured evidence: ElevenLabs Scribe when a key is set (verbatim fillers and restarts, word timings, about a cent per 3-minute video), otherwise a local open-source Whisper model at no cost. Meta's Muse Voice Transcribe returns no word timings, so it serves the Dictate buttons instead: speak a note in any feedback box and the text lands in the box to read and fix before it counts. Intake jobs also get clean captions: a WebVTT file built from the kept words with filler words left out of the caption text only.

## The operation language

Version one allows exactly five operation types:

| Operation | Meaning |
| --- | --- |
| `cut_words` | Remove an exact span of kept words. |
| `restore_words` | Restore an exact span of currently cut words. |
| `set_join_pause` | Set the pause at one numbered join, between 0.08 and 0.50 seconds, bounded by the neighboring speech. |
| `replace_take` | Atomically swap one take for another: restore one span, cut another, both or neither. |
| `flag_for_review` | Make no change; a person decides. |

Risk tiers: short exact cuts and bounded pause changes apply after deterministic checks (tier 1). Restores, take swaps, long cuts, opening or closing changes, and low-confidence proposals require a Muse Wonder review (tier 2). Anything touching protected material, a special insert (for example a signature breath), a negation-bearing span, an unclear target, or more than 25 percent of the finished edit is flagged only (tier 3).

## Quickstart

Requirements: Python 3.11 or newer. The core package is standard library only. One optional extra, `.[speech]`, adds faster-whisper so a dropped video can be transcribed on the machine itself; without it, intake transcription uses an ElevenLabs key when one is set, and the rest of the app is unchanged.

```bash
git clone https://github.com/rosalesrecommendations/talk-to-your-editor.git
cd talk-to-your-editor
python3 -m pip install -e .
```

Offline demo with the deterministic mock provider, using the synthetic job in `tests/fixtures/`:

```bash
tye direct \
  --feedback tests/fixtures/sample_feedback.json \
  --transcript tests/fixtures/sample_transcript.json \
  --cuts tests/fixtures/sample_cuts.json \
  --profile tests/fixtures/sample_profile.json \
  --out-dir out \
  --provider mock

tye after-review \
  --feedback tests/fixtures/sample_feedback.json \
  --operations out/operations_audit.json \
  --out out/after-review.html

tye learn \
  --graded examples/graded-corrections.sample.json \
  --out out/template-draft.json \
  --active-template examples/creator-template.sample.json
```

Live direction with the Meta Model API: set your key from [dev.meta.ai](https://dev.meta.ai) and switch the provider:

```bash
export MODEL_API_KEY=<your key>
tye direct \
  --feedback feedback.json \
  --transcript transcript.json \
  --cuts cuts.json \
  --out-dir out \
  --provider meta
```

Other commands: `tye inspect` summarizes a job, `tye map-time` maps one finished-edit timestamp to its source word, `tye validate-operations` checks an operations file against the schema (and, with a job, against the verifier), and `tye apply` applies a saved operations file. Run the tests with:

```bash
python3 -m unittest discover -s tests
```

## Inputs

- `transcript.json`: word-level transcript of the raw recording (word, start, end), as produced by the editing runner.
- `cuts.json`: the runner's edit plan; the kept ranges, in output order, are the contract the direction layer maps through and rewrites.
- `feedback.json`: the review page export (`video`, `feedback_done`, and `comments` with `id`, `ts`, `fmt`, `text`).
- A creator profile JSON (optional): protected word spans and preferences the verifier must honor.

Rendering the revised plan is done by the editing runner's own render stage, which consumes `revised_cuts.json` directly; stream sync is verified to within 50 ms by that stage, as with any plan it renders.

## Personal testing: v0.2, the test app

Version 0.2 adds a local web test app so a creator can run the whole loop in a browser, no terminal after launch:

```bash
tye serve
```

The app opens with no job loaded: the home page is the front door (drop zone, a one-click built-in sample, and the Meta sign-in check), and a job folder dropped on the page becomes the current job on the spot. `tye serve --job-dir /path/to/runner-job` still starts on a job directly.

The job folder is a runner export (transcript.json and cuts.json, plus the finished video when there is one). The server prints a localhost address for the computer and a LAN address for a phone on the same Wi-Fi. A job folder can also be dropped straight onto the page: the app stages a copy under the session's intake folder and switches to it on the spot, no path typing, and a lone dropped video stages and plays with a plain note about what a full edit still needs. In the page you watch the finished edit, pin comments at the current time, run direction (offline mock, or live Muse Spark and Wonder when MODEL_API_KEY is set in the server environment), read the applied and flagged results with before and after counts, download the revised plan and audit, and open the after-review page to grade the changes. Session files land in a `tye-session/` folder inside the job folder; the original transcript and plan are never modified. An optional render step (`--render-py`, `--source`, `--cleaned-wav`) renders the revised review through the runner's own render stage; when a piece is missing, the app says which piece, in plain words.

The `mac-test-kit/` folder carries a double-click launcher for macOS and a step-by-step personal testing script (Mac first, then phone).

## Rehearsal results (October 26, 2026)

The full loop was rehearsed end to end before this README was finalized:

- **Live model gate.** A live Muse Spark call turned a "remove this repeated line" comment into the exact `cut_words` operation for the repeated span. A forced take-swap comment escalated to Muse Wonder, which returned a reviewed `replace_take`. The same two operations submitted as one batch were refused as overlapping, which is the conflict guard working: two comments fighting over one span are held for the creator, not merged silently.
- **Real-job rehearsal.** On a real runner export (191.4665 seconds, 810 kept words; direction run with the deterministic mock provider, no footage leaves this repository), a sentence cut and a pause change applied and a comment pinned to the signature breath insert was flagged, not applied. The revised plan holds 797 kept words and renders at 187.8998 seconds. The original plan file was byte-unchanged after the run.
- **Render gate.** The revised plan rendered a review video with audio and video streams both at 187.900 seconds, a sync difference of 0 ms against the 50 ms limit.
- **Tests.** 44 tests pass, including from a copy of the tree in a path containing spaces.

## Foundation disclosure

This repository was built during the Meta Global AI Developer Hackathon (October 26 to November 4, 2026), Startup track, theme Build for Your World, by team Rosales Recommendations (Practice Your Passion LLC).

Disclosure, plainly: the direction layer in this repository is new work from the event window. It plugs into a local editing runner that existed before the event: that runner transcribes a raw talking-head video, makes the first-pass edit under the creator's learned rules, renders the finished formats, and generates the review page that exports the feedback file. The runner, its rule set, its test fixtures from real videos, and the creator's footage are pre-existing Practice Your Passion LLC work. They are not in this repository, they are not claimed as event work, and the demo focuses on the new layer: feedback becoming verified, applied edits. Interfaces between the two are the file formats documented above.

One more disclosure, just as plainly: the creator also maintains a separate, pre-existing personal editing system of his own. That system is not part of this project, none of its code is in this repository, and nothing in this repository was taken from it. The intake in this project, the part that takes a dropped raw video and makes its first edit (version 0.6.0), was designed and written fresh for this project, inside the event window, from the file formats documented above.

## Safety and data notes

- Standard-tier Muse models only. The discounted contributor-tier model ids, whose prompts may be used for training, are rejected in code for creator content.
- The API key is read from the `MODEL_API_KEY` environment variable and is never written to code, files, or logs.
- With the Meta provider, the material sent to the API is the comment text plus a small window of transcript words around it, not the video. Rendering and media handling stay local.
- The verifier's protected-span and negation guards exist because an edit that changes what a sentence says is worse than no edit. When in doubt, the system flags and the creator decides.

## Learning, honestly scoped

The hosted Muse Spark and Muse Wonder models are not fine-tuned by this project, and nothing here trains a model. What the system learns from is the graded correction record: feedback, the operation it produced, the audit trail, and the creator's verdict. Pattern extraction over those records drafts the creator template. Fine-tuning an open-weight model (Muse Spark Open) on accumulated graded corrections is a later phase, gated on data volume and scored against held-out examples before it would ever touch a real edit.

## License

Copyright 2026 Practice Your Passion LLC. All rights reserved. This repository is public so the hackathon judges can read and run the code; no open-source license is granted.
