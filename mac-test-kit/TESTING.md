# Testing Talk to Your Editor

This is your personal test pass for the simple build. Start on the Mac with the built-in sample, then try a real runner job, then use your phone.

There are no wrong reactions here. If something feels confusing, slow, fragile, or just not like how you think, that is the feedback we need.

## What you are testing

Quick map: the runner is part 1. It makes the first edit and gives you a job folder. This app is part 2: you watch that edit here and direct the fixes.

This version is built around one idea: keep the front door simple. Two choices, both on unless you turn them off. One button. The transcript itself explains every change, and the app learns your style one yes at a time.

## Mac test

### 1. Open the test kit

1. Unzip the project folder if it arrived as a zip.
2. Open the `mac-test-kit` folder.
3. Double-click **Talk to Your Editor.command**.

The first run creates a private `.venv` folder and installs the app. That can take a little while. Later runs should be faster.

If macOS blocks the first launch because it is from an unidentified developer:

1. Right-click **Talk to Your Editor.command**.
2. Choose **Open**.
3. Choose **Open** again in the warning box.

You should only need that right-click step once.

### 2. Start with the built-in sample

When Terminal asks:

```text
Paste the runner job folder path, or press Enter for the built-in sample:
```

Press **Enter**.

When it asks for a Meta Model API key, press **Enter** again. That uses the offline mock, so you can learn the flow without spending anything or depending on the network. (If you saved a key on this Mac earlier, the launcher skips that question and uses the saved key; see "Remembering your key on this Mac" below.)

Your browser should open Talk to Your Editor. If it does not, open the Mac address printed in Terminal. It will look like this:

```text
http://127.0.0.1:8765
```

Keep the Terminal window open. Closing it stops the app.

### Bring in a job by dropping it (new)

You do not have to restart the app or paste a path to change jobs. The page itself takes a job in.

1. Open Finder to the runner job folder you want to test.
2. Drag the folder onto the app page. Anywhere on the page works; the **Bring in a job** card at the top lights up while you drag. Let go.
3. The card reports progress in plain words: how many files, and each file as it arrives. When the folder is in, the page reloads and the whole app, video, transcript, counts, is now running on that job. You are testing the dropped job, no restart needed.
4. The app works on **the copy it took in**, staged under the first job's `tye-session/intake` folder. Your original folder is never changed. Notes, runs, and grades for the dropped job live in the dropped copy's own `tye-session`, just as they would if you had launched with its path.

Two more drop shapes to try:

- **Drop a single video file** (an .mp4 or .mov on its own). It stages and plays right in the Bring in a job card. The card says what a full edit still needs: the word-by-word transcript and the edit plan that part 1, the runner, makes. A video alone cannot be directed, and the app says so instead of failing.
- **Drop a folder that is not a job** (a folder of photos, say). The card says plainly that it is not a finished-edit job, names the two files a job folder carries, and confirms nothing was loaded. Your current job keeps running untouched.

If dragging is awkward, the card's **choose a folder** and **choose files** buttons bring in the same things the picking way.

Things worth noticing while you test this: whether the progress wording keeps up with a big folder, whether the reload lands you where you expect, and whether the staged copy behaving like a launched job feels seamless or surprising anywhere.

### 3. Make the edit: the front door

Under the video is the card that matters most: **Make the edit**.

Two choices sit there, both on:

- **Remove retakes.** When a line is said more than once, the earlier tries come out. The app now measures this itself, on your own computer, before any model is asked: clear retakes (the same line said again right after, the later take complete) are taken out through the same safety checks as every other change, even when you have pinned no comments at all. The take that completes its sentence cleanly beats a broken or trailing one; among complete takes, the last one stays.
- **Cut silence.** Long silences are trimmed down toward the buffer.

Pairs that are close but not clear are judged by the model in one call, flow first: which delivery finishes the sentence, which one carries the story forward, and whether keeping both would pull attention away from it. Pairs carrying a "not", and short lines said twice that could be emphasis, are never cut by the machine; they are left in the transcript for you to decide, with the reason in plain words. With no model available, the near pairs become flags with the match explained, never silence. A line is never cut just for failing to serve the direction; story questions become flags, not cuts.

The **silence buffer** is how much silence stays between one word and the next. Slide it or type the number. It starts at **0.1 seconds**. Cutting silence only ever trims; it never adds quiet back, and it goes through the same safety checks as every other change.

Press **Make the edit**. That is the whole front door. The app checks retakes, runs direction with those two choices, trims the silences, and tells you the result: words kept before and after, how many retakes came out, how many were left for you to decide, and how many pauses were trimmed.

Behind the **More choices** fold, for the days you want them:

- **Format scope:** both formats, vertical only, or landscape only.
- **When a line is said more than once, keep:** three choices, and the one you pick is remembered.
  - **The last take.** The usual rule: among complete takes, the last one stays.
  - **The first clean take.** The first complete delivery stays and the later tries come out, through the same safety checks.
  - **Best delivery (Beta).** The last take still stays for now. In the background, Beta records the take it would have kept on every pair where there is a genuine choice, using the same flow question the model judges near pairs with: which delivery finishes the sentence and carries the story forward. It changes nothing. Its pick shows as a note on the removed words and in the review rows, and the card keeps its score in plain words: how often its pick matched the take you ended up keeping, once you grade or swap. When two deliveries say the very same words, Beta always calls it a tie; there is nothing in the words to choose between them, so the last take stays. Nothing promotes Beta on its own; the count is there so you can watch it earn the job.
- **Spoken direction:** anything else this video needs, in your own words. What you say adds to the two choices; it does not replace them. Say **"unless"** when you want one spot treated differently, like "keep the last take, unless the first try has the real laugh in it."

Your choices are remembered in the session, so the card opens the way you left it.

### 4. Watch and scrub

Play the edit. A few things changed about moving through it:

- **Drag the timeline** and the handle grows under your finger or cursor. A bubble above it shows the exact time where you will land when you let go.
- **Back 5s** and **Back 1s** buttons sit beside the player for quick steps back.
- The keyboard still works the way the big editors do, as long as you are not typing in a box: **Space** plays or pauses, **K** pauses, **J** steps back 5 seconds, **L** steps forward 5 seconds, the **arrow keys** step 1 second, **Shift** with an arrow steps 5 seconds, and **M** starts a note pinned right at the playhead. The quiet line under the video lists them all, and its switch turns them off for the session.

### 5. Pin comments, when a spot needs your words

The front door handles the patterns. For a specific moment, pin a comment:

1. When you hear something you want changed, just start typing in the comment box. The note pins itself to wherever the playhead is at that moment. Typing pauses the video; switch on **Keep playing while I type** if you would rather keep listening.
2. **The time does not have to be exact.** A pinned time is an estimate. The editor checks a few seconds either way, about three seconds, and resolves your note to the nearest words by context. Get close; the app does the rest.
3. Write the kind of thing you would naturally say, such as "Remove this repeated line" or "Bring back the first version of this sentence."
4. Choose where the comment applies. It starts at **Both**, meaning both the vertical and the landscape cut.
5. Save the comment. Each new comment pins fresh at its own moment.

For a note about the whole video rather than one moment, like "there are retakes all through this," use the **Overall feedback** box. Overall notes go along with every direction run.

Then either press **Make the edit** on the front door, or use the **Run direction** card lower down, which is the same direction step with the provider picker showing.

Each change comes back with a clear status:

- **Applied:** the program made the change.
- **Flagged:** the program did not change it. A person needs to decide, often because the note touches protected material, a special breath or insert, an unclear target, or wording that could change the meaning.
- **Rejected:** the proposed change failed a check and was not applied.

The reason is always in plain language. If a status surprises you, that surprise is useful test data.

### 6. Read the transcript: every removal explains itself

Open the **Transcript** section. Kept words read normally; cut words are struck through and dimmed.

Every span this app has taken out carries a small **dot** after it. Click the dot:

- A panel names the words that were removed and **why**, in one plain sentence, with the note that asked for it when there is one.
- When the removed words were a retake, the panel also shows **the take that stayed**, and two buttons: **Bring these words back** and **Swap takes**. Swap takes puts the removed take back and takes the stayed one out instead, in one checked step.
- You can **leave a note on that edit** right there. Type it, or press **Speak the note** where the browser offers voice typing (Chrome does; on an iPhone, the keyboard's own microphone works in the typing box). The note is stored against the edit itself, not against a timestamp, so it keeps meaning the same thing even after the timing shifts. It is listed under that edit from then on, and the next direction run receives it labeled with what was removed. No note is ever lost.

Clicking a word still cuts it or brings it back, through the same safety checks, and **Fix words** still repairs words the transcript heard wrong. Fixing words changes the transcript text only; the sound and the timing never change.

### 7. Build the fixed video, then grade the changes

Rendering sets itself up when it can. If the job folder already carries the pieces rendering needs (the render script, the cleaned audio, and the original video), the app finds and uses them on its own. If a piece is missing, the page says which piece, in one plain sentence.

1. Press **Build the fixed video**.
2. Press **Grade the changes** to open the review page.
3. Watch the changed moments and grade each one: **Worked**, **Partly**, or **Did not work**. Retakes get their own rows, showing both takes. Grading a retake **Did not work** retires that pair: it is never proposed again.
4. On any change that is simply how you edit, check **This is my style**.
5. Press **Send grades to the app**. Your grades go straight back into the app. (The export button still works too; keep the file if you want a copy.)

### 8. Your style: rules you approve

Back on the main page, the **Your style** card is where the learning lands.

When the same kind of change keeps working in your grades, three times, or once when you marked it "This is my style," the app offers it once, in plain words:

> Pattern caught: (the pattern, in your words). Make this a rule for future edits?

- **Save as a rule** and it rides into every later run as standing direction, alongside your own notes.
- **Not now** and it waits. If the pattern keeps earning grades, it can be offered again later.
- Every saved rule is listed with a **Remove** button. Removing one is always one tap.

Nothing is ever saved without your yes. There is no silent learning.

### 9. Undo, redo, and History

At the bottom of the page is a **History** card. Every change in this session is recorded there: comments saved, front door runs, words cut or restored, notes on edits, grades, and rules.

- Press **Undo** to step back one change, or **Redo** to step forward again.
- Stepping back never touches the runner job itself; it only moves your session.

Try it once: make a small change you do not care about, undo it, then redo it. The page, the transcript, and the results should all follow.

### Remembering your key on this Mac

Live Muse direction uses your Meta Model API key. There are two ways to give it:

- **In the launcher:** paste the key at the hidden prompt. After you paste it once, the launcher asks:

```text
Remember this key on this Mac only? It is saved in a private file in your home folder, never in the app or the repo. [y/N]
```

Answer **y** and it is saved on this Mac only, in `~/.talk-to-your-editor/meta_key` (folder and file readable only by your account). It never enters the app folder, the kit, the zip, or the project files. Later launches use it without asking. Delete that one file to go back to being asked each time.

- **On the page:** the Run direction card shows whether a sign-in is set. You can paste a key there instead; it is kept for that session only, never written to disk, and never shown back. Press **Check the Meta sign-in** before a live run; it makes one harmless call and tells you in plain words whether the sign-in was accepted.

Never paste the key into chat. The launcher prompt and the page's sign-in field are the places it belongs.

## Test with a real runner job

The shortest route is now drag and drop: launch with the sample, then drop the real job folder onto the page (see "Bring in a job by dropping it" above). The app reloads on the dropped job and you are testing it.

The older route still works too: quit the app and launch it again, pasting the job's path at the prompt.

### Which folder to paste

Paste the **runner job or export folder itself**, not a single file inside it.

**Finder drag trick:** open the folder's parent in Finder, then drag the job folder straight into the Terminal window at the prompt. Its full path is pasted for you; just press **Enter**.

The right folder contains `transcript.json` and `cuts.json` together. It may also contain `profile.json`, `feedback.json`, `review.mp4` or `final.mp4`, and other files from the runner export. If you only see the video, or a folder full of other job folders, you are one level too high or too low.

### Real-job pass

1. Paste the full folder path at the launcher prompt.
2. Press **Enter** at the API key prompt for the mock, unless this run is meant to test live Muse.
3. Press **Make the edit** with the two choices on and watch what the front door does on its own.
4. Pin a comment or two in your own words where the front door did not cover it. Do not aim for exact times.
5. Run it again and check every status and reason.
6. Open the transcript, click a dot, and leave a note on one edit.
7. Build the fixed video, grade the changes, mark one "This is my style" if it earns it, and send the grades to the app.
8. Check the **Your style** card for the rule it offers.

For a live run, repeat the same job later with the key. Comparing mock and live on the same comments makes differences easy to spot.

## Phone test

The phone uses the app running on your Mac. The Mac does the work; the phone is another screen for the same test.

1. Keep the Mac awake, and keep the Terminal window open.
2. Connect the phone and the Mac to the **same Wi-Fi**.
3. On the phone, open the phone address printed by the launcher. It will look like this:

```text
http://192.168.1.25:8765
```

Your numbers will be different. Do not use the `127.0.0.1` address on the phone; that address points the phone back at itself.

4. Play the edit on the phone.
5. Scrub with a finger: the handle should grow, and the bubble should show where you will land.
6. Pin a comment from the phone. For a note on an edit, tap a dot in the transcript and dictate with the keyboard's microphone.
7. Press **Make the edit**, then open **Grade the changes** and grade one change on the phone.
8. Note anything that is hard to tap, hard to read, slow, or awkward with one hand.

If the phone cannot load the page, first check that both devices are on the same Wi-Fi and that the Mac did not fall asleep. Then restart the launcher so it prints a fresh phone address.

## Stop the app

Close the Terminal window, or click into it and press **Control-C**.

To test again, double-click the launcher again. You will choose the sample or a job folder each time.

## What to report back

Use your own words. Short is fine. Voice notes are fine.

Please send:

- What you were trying to do.
- What happened instead.
- The comment you pinned, if a comment was involved.
- The status and reason the app gave.
- Whether the revised edit sounded right.
- Anything that felt confusing, fragile, slow, or unlike your normal way of giving notes.
- The graded export, if you made one.

Two questions this version is still asking, whenever you have a feel for them: is **0.1 seconds** the right silence buffer to start from, or should it be 0.2? And should a rule need **three** repeats before it is offered, or should your one explicit "This is my style" be the only path? The build currently does both: three repeats, or one explicit yes.
