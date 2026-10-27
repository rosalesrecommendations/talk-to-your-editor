# Testing Talk to Your Editor

This is your personal test pass. Start on the Mac with the built-in sample, then try a real runner job, then use your phone.

There are no wrong reactions here. If something feels confusing, slow, fragile, or just not like how you think, that is the feedback we need.

## What you are testing

You watch a finished edit, pin comments to moments you want changed, and let the program work out the edit. It should show you what it changed, flag anything it should not touch automatically, render a revised version for review, and let you grade each change. You can also read the whole edit as a transcript, cut or restore words with a click, and fix words the transcript heard wrong.

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

### 3. Pin comments

Under the video there is a timeline bar. Every saved comment gets a dot on it, in the place where the comment belongs. Hover over a dot (or tap it) to read the comment, click it to jump the video to that moment, and drag the white playhead line to move through the video by the second.

While you watch, the keyboard works the way the big editors do, as long as you are not typing in a box: **Space** plays or pauses, **K** pauses, **J** steps back 5 seconds, **L** steps forward 5 seconds, the **arrow keys** step 1 second, **Shift** with an arrow steps 5 seconds, and **M** starts a note pinned right at the playhead. The quiet line under the video lists them all, and its switch turns them off for the rest of the session if you would rather not have them.

1. Play the sample edit.
2. When you hear something you want changed, just start typing in the comment box. The note pins itself to wherever the playhead is at that moment, and the page shows "Pinned at" with the time so you can check it. Typing pauses the video; if you would rather keep listening while you write, switch on **Keep playing while I type**.
3. Write the kind of thing you would naturally say, such as:
   - "Remove this repeated line."
   - "Make the pause here longer."
   - "Bring back the first version of this sentence."
   - "Do not change this part."
4. Choose where the comment applies. The choice starts at **Both**, meaning both the vertical and the landscape cut. Change it to Vertical only or Landscape only when the note is about just one of them.
5. Save the comment. Each new comment pins fresh at its own moment; a note never quietly reuses the time of the one before it. (If you ever need an exact moment, the seconds box still works as a deliberate override for that one note.)
6. Add a few comments if you want. You do not need perfect wording. Plain words are the test.

For a note about the whole video rather than one moment, like "there are retakes all through this," use the **Overall feedback** box under the comments. Overall notes are saved with your comments and go to direction as standing guidance.

If the sample already has feedback loaded, you can use that too. Adding one comment of your own still helps because it tests how the page feels.

One more thing on this page: the **Vertical (9:16)** and **Landscape (16:9)** buttons only offer cuts the job actually has, so a job with one cut simply shows the one it has. While a video loads, the player says it is loading instead of sitting black.

### 4. Run direction

Pick a direction mode and run it.

- **Mock (offline) first:** this is the safe rehearsal mode. It proves the page, the mapping, the checks, and the revised edit flow.
- **Meta (Muse Spark / Wonder) later:** live direction through your Meta Model API sign-in.

The run always finishes. When two of your notes collide on the same words, the first edit is applied and the later one is held with a plain reason on its own note, instead of the whole run stopping.

There are two ways to give the app your key:

- **In the launcher:** quit the app, launch it again, and paste your Meta Model API key at the hidden prompt. The typing will not show on screen. After you paste it once, the launcher asks one extra question:

```text
Remember this key on this Mac only? It is saved in a private file in your home folder, never in the app or the repo. [y/N]
```

Answer **y** and you will not be asked again on this Mac. Answer **n** (or just press Enter) and the key is used only for that Terminal session.

- **On the page:** the page shows whether a Meta sign-in is set. You can paste a key there instead; it is kept for that session only, never written to disk, and never shown back. Before a live run, press **Check the Meta sign-in** on the page. It makes one harmless call to Meta and tells you in plain words whether the sign-in was accepted, so a wrong or expired key is caught before you spend a run on it.

Never paste the key into chat. The launcher prompt and the page's sign-in field are the places it belongs.

### Remembering your key on this Mac

If you answer **y**, the key is saved on this Mac only, in a private file in your home folder:

```text
~/.talk-to-your-editor/meta_key
```

The folder is readable only by your account, and the file is readable only by you. The key is never written into the app folder, the test kit, the zip, or the project files. On later launches, the launcher reads that file, prints a line saying the saved key from this Mac is being used, and turns live Muse direction on without asking.

To remove the saved key, delete that one file:

```text
rm ~/.talk-to-your-editor/meta_key
```

After that, the launcher goes back to asking each time, and pressing Enter at the prompt uses the offline mock.

### 5. Read the result for each comment

Each comment should get a clear status:

- **Applied:** the program made the change.
- **Flagged:** the program did not change it. A person needs to decide, often because the comment touches protected material, a special breath or insert, an unclear target, or wording that could change the meaning.
- **Rejected:** the proposed change failed a check and was not applied.

Read the reason in plain language. If a status surprises you, note the comment and what you expected instead. That surprise is useful test data.

### 6. Build the fixed video, then grade the changes

1. Press **Build the fixed video** to turn the revised plan into a watchable video of the fixed edit.
2. Press **Grade the changes** to open the review page.
3. Watch the changed moments.
4. Pick one change and grade it:
   - **Worked**
   - **Partly**
   - **Did not work**
5. Add a note if the grade needs one sentence of explanation.
6. Download or export the graded results.

Keep that graded export. Send it back with your notes so the exact comments, operations, and grades can drive the next version.

### 7. Work in the transcript

Open the **Transcript** section on the page. It shows the edit as words: kept words read normally, and cut words are struck through and dimmed, so you can see the whole edit at a glance.

- **Cut or restore with a click:** click a struck word to bring it back, or a kept word to cut it. The click goes through the same safety checks as any other change. If a word cannot be moved (for example protected material), it stays put and the page says why in one plain sentence.
- **Fix words the transcript heard wrong:** press **Fix words**, then click one word, or a run of neighboring words, and retype what belongs there. Split one word into the two or three it should be, merge pieces back into one word, or just fix a spelling. Fixing words changes the text, and the captions built from it, only; the sound and the timing never change. Fixed words carry a "Corrected from" note, your corrections are listed together, and any correction can be removed to put the original words back. Direction reads the corrected text, so a misheard word stops confusing it.

## Test with a real runner job

After the sample feels clear, quit the app and launch it again.

### Which folder to paste

Paste the **runner job or export folder itself**, not a single file inside it.

**Finder drag trick:** you do not have to type or copy the path. Open the folder's parent in Finder, then drag the job folder straight into the Terminal window at the prompt. Its full path is pasted for you; just press **Enter**.

The right folder contains:

- `transcript.json`
- `cuts.json`

It may also contain:

- `profile.json`
- `feedback.json`
- `review.mp4` or `final.mp4`
- other files from that runner export

A simple check: open the folder in Finder. If you can see `transcript.json` and `cuts.json` together, that is the folder to paste.

If you only see the video, or you see a folder full of other job folders, you are one level too high or too low. Go into the specific export for the video you want to test.

### Real-job pass

1. Paste the full folder path at the launcher prompt. Dragging the folder into Terminal is an easy way to get the path.
2. Press **Enter** at the API key prompt for the mock, unless this specific run is meant to test live Muse.
3. Watch the finished edit and pin comments in your own words. Add an overall note if the whole video has a pattern worth naming.
4. Run direction.
5. Check every status and reason.
6. Skim the transcript for words the edit cut that should be back, and for any word the transcript heard wrong; fix them there.
7. Build the fixed video, then open **Grade the changes**, grade at least one change, and download the graded export.

For a live run, repeat the same job later with the key. Comparing mock and live on the same comments makes differences easy to spot.

## Phone test

The phone uses the app running on your Mac. The Mac does the work; the phone is another screen for the same test.

1. Keep the Mac awake.
2. Keep the Terminal window open.
3. Connect the phone and Mac to the same Wi-Fi.
4. On the phone, open the phone address printed by the launcher. It will look like this:

```text
http://192.168.1.25:8765
```

Your numbers will be different. Do not use the `127.0.0.1` address on the phone; that address points the phone back at itself.

5. Play the edit on the phone.
6. Pause and pin a comment from the phone. Try tapping a timeline dot to jump to a note.
7. Run direction.
8. Open **Grade the changes** and grade a change on the phone.
9. Note anything that is hard to tap, hard to read, slow, or awkward with one hand.

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
- The graded export you downloaded.

Every grade and complaint feeds the next iteration. "This feels wrong" is a valid report; add the moment or comment if you can, and we will track down why.
