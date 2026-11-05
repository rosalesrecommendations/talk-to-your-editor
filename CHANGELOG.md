# Changelog

## Cut words in the transcript, and set each pause by hand - 2026-11-04 (ships as 0.27.6)

- In the transcript, picking words and pressing Backspace or Delete cuts them, and a small toolbar above any selection offers Cut, Bring back, and Comment; Undo restores the words exactly.
- Every shortened pause shows as a small mark between the words ("·0.2 s"); clicking it steps that one join down or up by 0.1 s, restores the recorded pause with Original, or plays the join with Listen, and a pause can never be set longer than the one recorded.

## The keyed run passes the same gate, your style keeps its home, and taps teach - 2026-11-04 (ships as 0.27.5)

- With a key, the model's proposals now pass through the same last check as every other run before anything renders: a sentence said only once comes back if a proposal cut it, restarts and stopped tries are settled there too, and the silence ceiling applies; replaying your keyed Day 48 run's own files, the four passages your run lost are all in and the export measures 193.9 s with no silence of half a second or more.
- Saying the same line a few ways while you search for it is one chain: only the last complete saying stays, and the tries wait under one spot ("N tries at this line, kept the last") with Bring back; a try that flips a meaning (a not the kept saying does not share, a different number) still waits as your call.
- The editor learns from your decisions: every Cut it or Keep it, and every reason you type, offers one plain rule with Save or Not now; saving answers "Thanks. Learned: ..." and the next edit's Done bubble counts the rules it used. Your styles and rules now live in ~/.talk-to-your-editor, so unzipping a new kit never loses them, and a finished export plays a short chime as its message lands in the chat.

## Said once means it stays, one plan for both shapes, and the Day 48 rules - 2026-11-04 (ships as 0.27.4)

- A model proposal can never cut a sentence said only once, whatever the direction says: the July testing passage and the wife line stay in, proven against the exact proposals your keyed run made; your keyed Mac run is still the final word.
- Export both now renders both shapes from one snapshot of the plan, so the 16:9 and the 9:16 are the same edit; the video starts right before "Today's" with no held breath, and the 9:16 crop matches the zoom of your finished shorts.
- Restrictive by default: a likely retake is cut up front and waits as a spot you can bring back with one tap, never left in for you to find; the silence ceiling stays on, clamped so it only trims stretches with no words inside them.
- On a video it never learned from (Day 48), restarts play only in their last complete saying, no silence of half a second or more survives, and anything said after the subscribe line is cut; measured here, that export runs 192.6 s with zero long silences.

## The plan is checked one last time, and the edit is one conversation - 2026-11-04 (ships as 0.27.3)

- A final check reads the settled plan before anything renders: a take that stopped mid-sentence, with dead air after it and the same thought said whole later, comes out with its silence in every run (the stopped take at 1:25 in your keyed run), and the video's last line is never cut by a pass; if a pass cut it, it comes back and waits as your call instead.
- The opening starts at the breath: an over-long breath before your first line is trimmed to the breath itself (at most 0.8 seconds), so the video no longer opens with 1.6 seconds of quiet; measured on the export, the first word now lands at about 0.76 seconds.
- The conversation carries the run: one live progress bubble (stages that check off and strike through, elapsed and about how long is left, learned from this machine's past runs), a Done line that says "Your turn: N spots to check, then export.", spot cards with Cut it, Keep it, Reply, and Skip (the word Approve is gone), tap chips for offered fixes instead of typing "run it", and one Export in the chat for both shapes or one, with the timeline links and CapCut beside it, always rendering the current plan.
- Teaching adds up: the findings a teach shows are counted as what they are (rules and measurements), the save saves exactly the rules shown and says so, and after saving the editor tells you the next step: "Drop a video and I will edit it in this style."

## The four fixes from your keyed run - 2026-11-04 (ships as 0.27.2)

- Said only once means it stays: on a keyed run, a model proposal that would cut a passage said nowhere else in the video (your testing passage, "originally tested in July at... 2.18 centimeters") is now held for your call instead of applied. Proved by unit tests and by transcribing the built export; your next keyed run is the final word.
- Your call means your call: a proposal that touches a spot already waiting for your decision (the "My wife has been telling me this" pair) is held the same way, and the line stays in the edit.
- A stopped take takes its tail with it: the fragment after the dead air ("My life.") no longer plays on its own, and approving a spot never refuses over a word edge again; the cut widens to whole words and whole sayings at its edges and applies. Proven on the export: after "offer up to God" the full saying follows with no gap.
- The closing is never a repeat: the same-point detector no longer flags a passage restated in the final fifth of the edit, so your closing prayer is left alone, while a mid-video restatement still becomes a spot to review.

## The keyed run cuts right, the chat behaves, and the screens match your pictures - 2026-11-04 (ships as 0.27.1)

- Keyed runs (ElevenLabs Scribe transcripts) cut like the proof runs now: every transcript is normalized first ("thirty" is 30, "Today is" is "Today's"), so your template opening line is never cut as setup talk, the complete take wins over a broken piece (no more mid-sentence splice in the book sentence), and the stopped take before the full prayer comes out with its silence. On Day 30 with your Scribe transcript: opening in, agreement 96.8 percent, one long silence, no cut words audible.
- Chat during an edit: status questions get short progress lines (stage, elapsed, roughly how long is left) read from the one job record, a complaint or anything unclear runs nothing and costs nothing, a question gets its answer (how to review, whether your keys work), no direction means no direction pass, and there is one cost, in dollars, the same on the card, in the chat, and on Done.
- The interface matches your reference pictures: teaching lives in the chat (two file slots and Learn from this in a bubble), home centers the conversation and fills the window, the Edit screen has one top row and a bigger player that shows the real length before play, the your-call card sits above the transcript text, the timeline runs full width with a waveform, the video bubble's name and length read in white, chat replies stay to three lines, and Finish shows a small framed preview of each shape before export.
- Run-through fixes: approving a review spot that cannot be cut safely now keeps the spot and shows the reason on its card, whole-edit Undo works after later chat that changed nothing, a stored spot's "same point" time is recomputed from the sound when the job opens, the teach summary shows what was measured (pauses, breaths, the opening) instead of generic rules, and exporting from the Done bubble now renders at the same -14 LUFS as every other export.

## The cut matches your edit, and Finish exports full frame - 2026-11-03 (ships as 0.27.0)

- Real spoken lines are never dropped as slivers again: a short line between two pauses is content, a fragment never wins over a complete take, a span marked your call is never cut without your yes, and a take you stopped partway comes out when a later take says the same thing whole. On Day 30 the six dropped lines are back, and word agreement with your edit is 96.7 percent.
- Length: a cut take now comes out whole, head included, and where a kept passage makes the same point as a later one it waits as a spot to review ("This makes the same point as [time]. Cut it?") instead of cutting itself. Day 30 runs 177.3 s before review against your 165.4 s; approving the two spots covers the rest.
- Finish exports at full frame now: 3840x2160 for 16:9 and 2160x3840 for 9:16, face size within 3 percent of your reference, loudness at -14 LUFS. Export also iterates its face-size corrections on a 1080-high copy and renders the full frame once, instead of a full render for every correction.
- Small fixes: the Undone bubble is back to one line, "Why did you cut that?" answers about the cut nearest where you are watching, and the offer to teach your style first only shows before an edit starts, never mid-edit.

## Sound tells the truth, the track is one piece, and nothing is final until you say so - 2026-11-03 (ships as 0.26.0)

- Sound is the source of truth: after the plan, each kept piece's real word edges are measured from the audio and the word list is updated from that, so what the list says is cut is really not playing. Proved on Day 30 by transcribing the rendered video and diffing it against the plan: 0 cut words audible.
- The soundtrack is built as one continuous track from the source in a single pass and muxed with the picture: no fades to silence, so no holes at joins (your 0.25.0 run counted 114; this build counts 0), and audio and video durations agree within one frame (yours drifted 7.1 s).
- One summary only: the long recap bubble is gone, the Done bubble is the only summary and every number on it comes from the one count, its review entry is a single "🙋 N spots to review" link, and answers about a cut come from the editor's own record, about the most recent cut when "that" is unclear, never an invented reason. The progress bubble is stored on the server like the Done bubble: reload mid-edit and it is still there with the true elapsed time, Done replaces it when the edit finishes, and a lost edit reads "This edit was interrupted" with a Try again button instead of spinning forever.
- Nothing is final until you say so: every Done bubble carries one small "Undo this edit" link that restores the video to before this edit, and chat-directed changes keep their per-action Undo.

## The chat keeps its word, and home matches your picture - 2026-11-02 (ships as 0.25.0)

- The conversation renders in time order, and nothing you say is lost: the first message after the page opens is saved, the Done message (time, cost, counts, spots) survives a reload, and a "New message" pill appears when a reply lands while you are scrolled up, instead of moving your view.
- One count everywhere: the Done message, the review link, and the transcript header all read the same numbers, computed once from the finished plan; a join becomes a spot to review only when it fails a real measured check, so a clean edit no longer reports every join as abrupt.
- A detected retake is cut and the last take kept, without asking, unless the copies say different things (a "not", different numbers, a repeat said on purpose); those wait as your call. And a question in the chat is answered from the record at no cost: only a clear command runs an edit.
- Home matches your reference picture: the app name and Beta tag sit at the top of the left column, the slim list keeps its divider, there is no top bar, the conversation fills the window edge to edge, "Knows your style" and the home "Shrink chat" link are gone, and the style picker is a pill under the editor's name, below the rules links.

## P0 fix round, the zero push: mid-sentence starts - 2026-11-02 (ships as 0.25.0)

- Kept pieces never start mid-sentence now: on the Day 30 pair the count went from 9 to 0, each resolved the way your finished edit handles it. A piece that starts partway into a sentence is joined back to the words it was missing, starts at the kept take when you cut the first try, or is cut with its take when only a fragment of a cut take was left.
- A repeat inside one sentence is not a take: "My wife has been telling me this, my friends have been telling me this" is a parallel line said on purpose, so it is flagged your call and never cut on its own; and a fix never revives a take you cut, because the join is placed so none of the cut take's words play.
- Proof numbers on the Day 30 pair: v025-work/p0-proof-025-zero.md (output 173.4 s next to your 165.4 s; silences of 0.5 s or more in the output: 2, yours 1; word agreement with your edit up from 79.8 to 82.5 percent). The gate test now fails any build that leaves a mid-sentence start in the proof plan.

## P0 fix round, phase 1: the engine - 2026-11-02 (ships as 0.25.0)

- Your first line is safe: the opening now anchors on your template line ("Today's day N of ..."). Setup talk and abandoned takes before it can still be cut; the template line itself is never read as a take, and a line later in the video that quotes it is not a retake of it.
- Retakes settle whole: when the editor finds an earlier take of a line, the whole earlier take comes out and the whole later take stays. Before, the cut was narrowed to the exact words the later take repeats, so the unmatched words of the earlier take stayed in the edit, spliced against the later one.
- Kept pieces start at sentence starts: after the join check, any piece that would start mid-sentence is backed up to the start of its sentence, unless the words before it belong to a settled retake cut, the head is long, or a real silence sits inside it. A short span trim runs after, so a restored head brings no dead air with it.
- The audio is the truth about words: a word the transcript stamps across pure silence no longer anchors its piece or shields the quiet around it, and a piece holding only such words and no measured sound is dropped. Proof numbers on the Day 30 pair: v025-work/p0-proof-025.md (output 167.3 s next to your 165.4 s; silences of 0.5 s or more in the output: 2, yours 1; the reworded take now cuts whole).
- The four proof measures are now automatic checks in the test suite (tests/test_v25_gate.py), so a future build that cuts the opener, splices a take, or leaves long silences fails loudly.

## P0 fix round - 2026-11-02 (ships as 0.24.0)

- The video builds now: each kept piece renders on its own with a short fade at its edges, then the pieces join, so memory stays flat no matter how long the edit is (0.23's single pass was killed on a real Mac).
- Silence is measured from the audio, not the transcript: quiet inside a piece, and at its head and tail, is trimmed to your pauses, breaths whole, your Keep marks untouched.
- The opening follows your template: setup talk and abandoned takes before your first real line are cut; the breath right before that line stays.
- Retakes catch reworded lines: the same line said again with words swapped is caught, the later complete take stays, unsure pairs are flagged your call.
- Home matches your reference: slim plain video list, conversation centered, no tabs or level bar; the chat scrolls to the newest message and never yanks you while you read. Proof numbers: studio-work/msg2r2/P0-REPORT-2.md.

## Studio, Message 2R - 2026-11-02 (ships as 0.23.0)

- Smooth, never choppy: a new engine pass sets every cut edge on the measured sound of the room, never inside a word, breaths all or nothing, and a join check after every edit fixes what it can and flags the rest as your call. Proved on the Day 30 pair in studio-work/msg2r/P0-REPORT.md.
- The conversation is the home screen: no Train tab, a slim list of your videos on the left, your videos and the edit as playable bubbles, a pop-up player, and the chat docked beside the Edit screen.
- Send a video right in the chat: the editor answers in one bubble with the style, the toggles, the cost and usual time, and Make the edit; progress and the done message live in that bubble.
- Named styles: rules and Inspiration belong to a style picked under the editor's name; rules open over the chat with examples, in-place editing, and hover-minus removal with Undo; adding a rule by saying it works.
- Two thin endpoints only: /api/styles (pick, new, rename) and /api/rule-edit (a rule's own words). Teaching links say plainly what works; other people's YouTube, Instagram, TikTok, and Facebook videos are never downloaded.

## Studio, Message 2 + Train fixes - 2026-11-01 (ships as 0.22.0)

- Edit screen (beats 4 to 6b): parts come in as small pictures in your order, direction card shows the cost before you start, first edit with an honest level map, transcript with reason tags and your-call cards, chat about exact spots, timeline with joins and crossfades, movable panels.
- Train fixes from the Nov 1 feedback: raw slot first, finished second; "A style I like" takes several videos or links, teaches style only, and saves as Inspiration (new /api/inspiration-save, creator-level inspiration.json), apart from your rules; every saved rule opens its examples, with Find more rules and Add my own rule (new /api/rule-custom) below the list. Nothing saves without a yes.
- Only those two endpoints are new; every other call uses endpoints that already existed. Train and Edit both passed a full browser proof.

## Studio reset, Message 1 - 2026-11-01 (ships as 0.21.0)

- New front end: templates/studio.html (808 lines), served at /studio and at / (the page that opens on launch); the classic page is unchanged at /classic. Studio shell with Train, Edit, and Finish screens, the shared editor chat, dictation in the chat bar, and the Train screen (beats 1 to 3).
- Saved rules and the teaching framing profile moved to one creator-level store in the launch home, read and written by every job and by Train with no job loaded; framing is kept per shape (9:16 and 16:9 side by side). On first run, rules and framing saved inside existing job folders merge in once, so nothing saved before is lost.
- /api/rule-save, /api/rule-dismiss, and /api/rule-remove now work with no job loaded. A first edit's recap names each saved rule it carried: "Saved rule applied: ...". Proved end to end in a browser: rule saved on Train with no job, app quit and reopened, new video's first edit recap shows the rule applied.

## 0.20.0 - 2026-11-01 (in progress)

Chunk A: the Editor's face and name, and text-only chat.

- The Editor has a face and a name on the first page
  too. The ask-the-editor chat at the top of the start
  screen now opens with the same round clapper board
  avatar and the same name as the Editor panel, instead
  of a bare "Ask the editor" heading. Double-click the
  name on either page to rename the Editor; it is one
  saved name everywhere. Ask "what is your name" and the
  answer is the name. Costs: 0 cents (local work only).
- The conversation is text only now, the way you asked
  for it: chatting like a text conversation, no voice
  talking at you. Spoken replies are gone, toggle and
  all; nothing in the app reads a reply out loud, and
  flipping a clean-up switch never says anything either.
  Your messages and the Editor's stay as text bubbles,
  yours on the right, the Editor's on the left. Talking
  into the microphone to dictate still works; only the
  talking back is gone. Costs: 0 cents (local work
  only).

Chunk B: one bring-in path, and direction on the editing
page.

- One way in. The start screen had two "Bring in a video"
  sections stacked (the drop card, then the bring-in
  pop-up's own heading) plus three empty Video,
  Transcript, and Editor panels waiting underneath. Now
  there is one bring-in card and one heading, and the
  empty placeholder panels are gone. The pop-up that
  lists your videos keeps only the bring-in itself: the
  parts and their order, Add more videos, Bring these in,
  and the test-video switch. Costs: 0 cents (local work
  only).
- Direction lives on the editing page now. The Requests
  for your editor box, the Remove retakes, Word gaps, and
  Remove filler words switches, the "How involved do you
  want to be?" choice, Your usual way, and Make the edit
  used to sit inside the bring-in pop-up, where they were
  crushed into a narrow column that overlapped itself.
  They are now a card on the page itself, ahead of the
  workspace, wide enough to read: the Requests box in the
  main column and the Remove choices beside it on a wide
  screen. Bring in a lone video and the pop-up closes;
  the direction is set right there on the page before the
  first edit is made, and the pop-up says so. The first
  edit itself runs exactly as before. Costs: 0 cents
  (local work only).

Chunk A2: the chat looks like a text conversation.

- The look you pointed at in your own chat app is now
  the look here, on the first page chat and in the Editor
  panel alike. The Editor's face and name sit centered
  at the top of the thread, like the person at the top
  of a text conversation. Your messages ride in filled
  blue bubbles on the right; the Editor's sit in plain
  dark bubbles on the left. (They were the other way
  around before: yours dark, the Editor's blue tinted.)
- While the Editor works on a reply, three dots breathe
  in a small bubble, the way a text conversation shows
  someone typing; on the first page the dots show for a
  beat before the answer lands. Each thread keeps one
  message bar at the bottom with a round send button,
  and the first page's typing box is a rounded bar now
  too. Nothing about the conversation itself changed:
  same answers, same Copy, Reply, and Undo beside each
  Editor message, same double-click rename, same Coach
  mode. Costs: 0 cents (local work only).

Chunk C: a clock that counts steadily, a usual length,
and no clipped words.

- The clock counts steadily now. While the first edit
  runs, the time so far used to jump, fall back to the
  same few seconds, sit there, and count again, because
  the page restarted its clock from the server's figure
  on every check, and that figure only moves when a
  step changes. The run now has one clock, set once
  when the run starts, and the seconds count 1, 2, 3
  the whole way through. Costs: 0 cents (local work
  only).
- A usual length, when the app knows one. The app now
  remembers how long each finished first edit took on
  this computer, and once it has a few of them, the
  progress line says so in plain words beside the time
  so far: "usually about 2 minutes". It is a usual,
  known before the run starts; it never counts down,
  and there is still no percentage. With too little
  history, the line shows the time so far alone.
  Costs: 0 cents (local work only).
- No clipped words behind the working card. The card
  in the middle of the screen now sits inside the
  window with a margin on every side and its words
  wrap inside it, and while a step runs the page
  behind it is held to the window's width, so words
  at the edges ("Word gaps", "How involved do you
  want to be?") stay whole. Costs: 0 cents (local
  work only).

Chunk D: the editing page is readable, the video fits
its own shape, and Train the editor is a main path.

- Bigger type across the editing page. The transcript
  words rise from 17px to 19px, and Bigger text in
  Settings steps up from the new base, to 21px. The
  panel names, the notes and counts on a job, the card
  headings, and the direction card's rows (Requests,
  the Remove switches and their summaries) all take a
  step up too. Costs: 0 cents (local work only).
- Review suggestions you can read. Each suggestion's
  words and its quoted line are 16px now, and the text
  wraps inside its card instead of running past the
  card's edge in the narrow right hand column. The
  rule suggestions under Your style match. Costs:
  0 cents (local work only).
- The video fits its own shape. The preview box is now
  sized to the loaded video's real shape: a tall video
  stands tall and large in its panel, a wide video sits
  wide, and the picture is never stretched. The
  player's own controls ride on the picture as before.
  Costs: 0 cents (local work only).
- The Edit settings strip stays in reach. On a short
  window the strip's bar now gives a little instead of
  sitting clipped at the bottom edge with the timeline;
  opened, its rows scroll inside it and the "Edit
  settings" line itself stays pinned at the top of the
  bar. Costs: 0 cents (local work only).
- Train the editor is a main path, not a side extra.
  On the first page it is its own titled section under
  "Bring in a video", in plain words: show the editor
  one finished edit, it shows you the rules it learned
  before anything is saved, and its cuts start closer
  to yours. The graduation cap on its button is the
  normal size now (it had no size at all and rendered
  huge), and the vague "costs a few cents" line is
  replaced with the real per-run cost, the same wording
  the teaching steps use: about 4 cents for every 10
  minutes of video, plus about 5 cents for the read.
  The teaching card itself no longer calls teaching
  optional. Costs: 0 cents (local work only).

## 0.19.0 - 2026-11-01

- Take the edit to your other editor. Under Revised
  edit, three new buttons download the current edit as
  a timeline file: one for Final Cut Pro and DaVinci
  Resolve, one for Premiere Pro, and an EDL for most
  other editors. The file carries the same kept pieces,
  in the same order, with the same trims, and the sound
  crossfades ride along in the Final Cut and Premiere
  files (an EDL cannot carry them). Open the file in
  the other program and keep changing the edit there.
  Building the fixed video here works exactly as
  before. Costs: 0 cents (the files are written on
  this computer).

## 0.18.0 - 2026-11-01

- The Editor is a conversation now, the way you asked for
  it on Nov 1. Your messages sit on the right and the
  Editor's on the left, like a message thread. When you
  open a job, the Editor greets you. While it works, one
  plain line says what it is doing, and the reply arrives
  whole: what changed, and what is waiting on you. Costs:
  0 cents (local work only).
- A face and a name you can change. The Editor wears a
  round clapper board avatar, and its name sits beside
  it. Double-click the name and type a new one: it is
  saved on this computer, survives reloads, and the
  whole app uses it. Costs: 0 cents.
- The Editor talks back. Spoken replies are on: new
  answers are read out loud in the computer's own voice
  (the browser's speech, no network, no cost). The
  speaker button in the Editor's header turns it off
  and on. Costs: 0 cents per reply.
- Reply to one exact message. Every Editor message has
  a three-dot button beside it, and the same menu opens
  on press-and-hold or right-click. The menu is Copy,
  Reply, Undo, in that order. Reply puts a small bar
  above the typing box naming the message you are
  answering, and the Editor's answer is about that one
  message. Costs: 0 cents.
- Undo one action, not everything. When a message
  reports an action, Undo in its menu reverses just
  that action from the saved state, with no new run,
  no model call, and no cost. The message turns gray
  with a line through it and is marked Undone, so the
  record still shows what happened, and its menu then
  offers Redo, which puts the action back the same
  way. If one action cannot be reversed on its own
  without disturbing later work, the Editor says so in
  plain words and offers the safe next step instead of
  quietly undoing everything. Costs: 0 cents.
- Coach mode. Ask "What would you do to make this
  better?" in the thread, or tap the offer the Editor
  makes when an edit finishes. The Editor answers in
  plain words, in priority order: first what it found
  in this video's own words and editing record, then
  story ideas for keeping people watching. Ask about
  one part, like the opening, and it answers that
  first, then adds anything else it noticed that you
  did not ask about. Every suggestion names something
  really in your video. Say "do it" or "do the first
  one" and that suggestion runs the normal way. Ask
  how to record better and it adds tips marked for
  next time. Asking costs 0 cents; running a suggestion
  you pick costs the same as any direction, about 5
  cents per edited video (estimate).
- The edit itself is untouched, and the standing
  checks hold exactly: IMG_2516 still grades 1,247
  kept / 89 cut / 0 held, and the Day 30 match still
  agrees on 716 of 825 words (86.8%) in both ways of
  working, with none of the retakes you cut left in
  unflagged.
- Test count for this round: the full unit suite runs
  1,544 tests, 49 of them new for the conversation,
  the message actions, and Coach mode. One older test
  (the first-edit stage count in test_v061) fails the
  same way on the untouched 0.17.1 code in this
  environment, counting 7 stages where it expects 8;
  it is not from this round.

## 0.17.1 - 2026-11-01

- The stronger way of working is named Proactive, your word
  for it, instead of Strictest. The name changed everywhere
  you see it: the pill in both controls, the row summaries,
  the recap line ("Your way of working: Proactive."), and
  the front door's error message. Nothing underneath
  changed: the two ways of working still record as
  "cleanest" and "tailor", "Leave more in" keeps its name,
  and the edit itself is untouched, so the standing
  regression (IMG_2516, 1,247 kept / 89 cut / 0 held) holds
  exactly. Costs: 0 cents (local work only).
- Verified on your Day 30 video before shipping: the app
  edited the raw footage in both ways of working and was
  scored against your finished edit. It agreed on 716 of
  825 words (86.8%), up from 682 (82.7%) on the same test in
  0.16, because passages whose copies say different things
  now wait for your call instead of being cut. Of the
  retakes you cut, none was left in unflagged: 8 of 10 were
  cut (7 of them marked unsure for your review), and 2 wait
  as your call because their copies differ by a "not". The
  model's direction pass still cannot run here (it needs
  the model key), so that part stays unproven until a run
  on your Mac. Costs: 0 cents (local work only).

## 0.17.0 - 2026-10-31

- Fixed the differing-copies check the item 8 work added: it
  read the flag from a field saved retake records do not
  carry, so a pair whose copies genuinely differ (one with a
  "not", one without) would have been cut instead of waiting
  for your call. The check now reads the flag where records
  keep it and also compares the two copies' words, so the
  same line said twice, "not" and all, still settles as a
  cut. A differing pair is offered as one whole span on its
  card, never split into pieces. Costs: 0 cents (found by
  the test suite; local work only).

- Playing a video no longer scans every word on the page ten
  times a second. The spoken-word highlight now looks the word
  up in a list the transcript keeps as it is drawn, and the
  selection paint only touches the words whose state changed
  and paints once per frame while dragging, so highlighting
  follows the pointer smoothly and the page stops churning
  under playback (feedback item 4's browser slowdown, and
  item 5's highlighting).
  Costs: 0 cents (local work only).
- The first-edit run now reports from the middle of the
  screen: a card with a spinner, the stage's name, the step
  count, and the time so far, over a dimmed page. The made-up
  percentage and time-remaining are gone everywhere; the bar
  in the pop-up is a plain moving bar that claims no number
  (feedback items 1, 17, 18; the server half shipped earlier
  in this release).
  Costs: 0 cents (local work only).
- Opening Edit settings on the editor page can no longer
  push the page past its own bottom with no way to scroll:
  the settings bar now caps its height and scrolls inside
  itself (feedback item 2).
  Costs: 0 cents (local work only).
- "Teach the app" is "Train the editor" everywhere, and it
  lives on the first page: a Train the editor row under the
  drop area opens the training card, and the bring-in pop-up
  no longer carries a teaching step of its own. The training
  card is one clear path: whose editing it is, the finished
  edit as a file or a link (a YouTube link works), the raw
  video and the screen recording under "Add more, if you
  have them", then what happens and what it costs (feedback
  items 10, 11, 12).
  Costs: 0 cents (local work only).
- The first page carries a small "Ask the editor" chat:
  three starter questions and a box, answered on the spot
  from what the app can actually do, at no cost (feedback
  item 13).
  Costs: 0 cents (local work only).
- "Bring in a job" is "Bring in a video" everywhere,
  including the top bar's bring-in button (feedback item 14).
  Costs: 0 cents (local work only).
- A multi-video bring-in shows each video as a small picture
  of itself (or a plain Video chip when no picture can be
  made), and an "Add more videos" button beside "Bring these
  in" adds more files to the same bring-in; drag-to-reorder
  and the up and down buttons work as before (feedback
  item 16).
  Costs: 0 cents (local work only).
- The pop-up's video preview is a clean media card in the
  shape Premiere, Final Cut, and Descript use: a framed
  picture with no browser control bar, one round play button
  on the picture, the duration as a small badge, and the
  file's name under it (feedback item 22).
  Costs: 0 cents (local work only).
- The direction box in the bring-in pop-up is titled
  "Requests for your editor" (feedback item 23), and the
  pop-up is wider with two columns on a wide screen, so the
  dead space that sat beside the Remove column goes to the
  direction side instead (feedback items 20, 21).
  Costs: 0 cents (local work only).
- The editor page opens blank before any video: three quiet
  panes say what will live in them, the video pane takes a
  drop or its Choose a video button, and dropping a video
  anywhere fills them, Descript-style (feedback item 24).
  Costs: 0 cents (local work only).
- The editor wears a small "beta" tag beside its name
  (feedback item 25), and the cuts an edit run makes no
  longer pour into the conversation: they collect in a
  Review suggestions card under the editor, each with its
  words, its time, and Bring back / Keep cut buttons
  (feedback item 26).
  Costs: 0 cents (local work only).
- The Editor chat is right-sized: a shorter panel, a smaller
  avatar and name, tighter entries and buttons, so the chat
  stops crowding out the video and the transcript (feedback
  item 20, as refined by Justin mid-round: "That chat
  interface is huge").
  Costs: 0 cents (local work only).
- The top bar's pane buttons are the toggle bar: Video,
  Transcript, and Editor each say On or Off in words beside
  their name, in the same place every time, so a hidden pane
  is visibly off and the same button brings it back
  (feedback item 28). The Follow button names itself and its
  state in words and wears a playhead mark instead of the
  funnel that read as a filter (feedback item 6). Reading
  width Wide now means the full width of the pane, so the
  two settings are unmistakably different (feedback
  item 27). The Edit settings bar states plainly that
  changing choices costs nothing and only running an edit
  costs (feedback item 30).
  Costs: 0 cents (local work only).
- "Download the finished edit" sits in the action row beside
  Make the edit as soon as a finished video exists; before
  that, a plain note in the same spot says the download
  appears once the finished video is built (feedback
  item 29).
  Costs: 0 cents (local work only).
- Dictation no longer takes over: the microphone button
  keeps its icon and size, gains a small red dot, and a thin
  level meter beside it moves with his voice. The clock and
  the words live in the status line, which says he can keep
  talking, that words are added as he pauses, and that he
  can keep typing too. Dictated words land at the cursor
  (feedback item 19).
  Costs: 0 cents (local work only).
- Held spots are titled "Your call" in the pop-up, on the
  transcript markers, and in the history, in place of
  "Recommended to remove" (feedback item 8's wording half;
  the cutting-by-default half shipped earlier in this
  release).
  Costs: 0 cents (local work only).

The round from Justin's recorded hands-on review of the 0.15 kit (Oct 31, 2026): every one of the 31 things he named, fixed. Built batch by batch; the rest of the round follows in the same version.

- **"Put them back in" restores the retakes now (Batch A, the 0.16 conversation test's one failure).** The voice and chat matcher heard any sentence carrying the word "back" as a move in the video, so "put them back in" jumped the video back five seconds and restored nothing. Restore shapes are now read before navigation: a restore verb (put, bring, add, restore, get) with the thing that was taken out (them, those, the retakes, everything) and "back", with no time words anywhere, restores the retakes. The natural family comes with it ("bring them back", "put the retakes back in", "put everything back", and their kin, also as settled phrases). Sentences carrying a time stay moves ("go back five seconds" still goes back five seconds), and the settled single-object phrasings keep their settled meanings ("put it back" is still undo; "put that back in" is still redo).
- **Costs.** This change runs on your Mac: the matcher is local work and costs 0 cents. No model calls are made for these decisions.
- **Progress never invents a number again (Batch A, feedback items 1 and 18).** A run's progress used to carry a percentage and a time remaining computed from the pace of the steps finished so far. Step times vary too much for that to mean anything: at the last step it read "Step 9 of 9, 100% done" while the step was still working, and the time remaining cycled (9 seconds, 11 seconds, 9 again) while nothing finished. Justin, watching it in the review: "It doesn't actually feel true. Something feels off and I'm losing trust." Both figures are gone at every stage, in the run's state and in its one-line summary: a run reports the step it is on and how long it has been running, both measured, and nothing else. The page's rendering of the same rule, and the progress display's move to the center of the screen, land in the page batch of this round.
- **Costs.** This change runs on your Mac: the progress figures are local work and cost 0 cents. No model calls are made for these decisions.
- **Detected retakes are cut by default in every way of working (feedback items 7 and 8).** In the 0.15 review Justin found clear retakes left in his Day 45 video and the app asking whether to remove them: "Be proactive, cut the retakes, and let me know if I need to bring them back." The proactive settlement that 0.15 ran only in the Strictest way of working now runs in Leave more in too: a detected repeat (an earlier take said again right after, or a directed cut whose words duplicate words that stay) is struck at record time, named in the run's recap as struck, and restorable with the usual bring-back. The one case that still asks is a pair whose copies genuinely say different things (one copy includes a "not", so which copy survives can flip the meaning); that spot waits as a question, and its card states the default first (the copy that includes more stays). Identical copies said twice settle like every other repeat. Nothing else changed: protected material never settles, and a cut the checker refuses for a hard reason still waits.
- **Costs.** This change runs on your Mac: the settlement is local work and costs 0 cents. No model calls are made for these decisions.
- **A YouTube link brings the video in now (feedback item 3).** Teaching the app from his own uploaded video failed in the review: a YouTube watch page names no video file in the places the app knew to look. The link reader now recognizes YouTube links (watch, youtu.be, and Shorts) and reads the player data the watch page itself carries: a ready-made stream downloads as it is, and when YouTube offers picture and sound as separate streams, both download and are joined with ffmpeg into one file. When YouTube will not hand the video over (a private video, a sign-in wall, an encoded stream address), the message says what happened and what to do instead: download it from YouTube Studio and drop the file in. Other links work exactly as before.
- **Costs.** This change runs on your Mac: the download and the join are local work and cost 0 cents. No model calls are made for these decisions.
- **The Editor chat is a conversation again (feedback item 26).** A finished run used to list every cut it made as its own line in the chat ("Cut 3 words at 0:05..."), which Justin called a word-vomit list. Those per-cut lines now land in their own place: a Review suggestions card listing each cut with its words and time, and Bring back / Keep cut on each one. The chat keeps the run's summary sentences, the way people talk. Answers the editor gives from the record (why a piece came out, what a run cost) still carry their detail in the chat, because there the detail is the answer.
- **Costs.** This change runs on your Mac: filing and settling suggestions is local work and costs 0 cents. No model calls are made for these decisions.

## 0.16.0 - 2026-10-31

The "edit it by hand" round, green-lit early on Oct 31: timeline manual editing, built batch by batch. Batch A is the first change; the rest of the round follows in the same version.

- **The plan can now be edited by hand, in code (Batch A, items 1–8's engine).** A new module, `manual_edits`, holds the timeline's own operations as pure transforms of the plan's kept ranges: split a piece at a source time, move a piece's edge (trim), take a piece out, move a piece to a new place in the order, paste copied pieces at a playhead time, and set one join's sound crossfade. Each returns a new plan and changes nothing it should not: a special insert (signature breath, freeze frame, donor audio) can never be targeted, swallowed, or crossed; a protected word can never be trimmed or deleted away (a trim clamps at its boundary instead); a piece keeps at least the plan's minimum length. A crossfade is stored on the piece it fades into (`fade_in_s`), so it survives splits, moves, and pastes and always means the same join. Join pauses are recomputed the house way after every operation. The web endpoints, the band's gestures, and the render follow in later batches of this same version.
- **Costs.** This change runs on your Mac: moving ranges in the plan is local work and costs 0 cents. No model calls are made for these decisions.
- **The app now takes the hand edits, and the render honors a join's own crossfade (Batch B, items 1–8's server half).** Six new endpoints (`/api/timeline/split`, `trim`, `delete`, `move`, `paste`, `fade`) run the Batch A transforms on the session's current plan and land each result in `revised_cuts.json` under one history checkpoint, the same landing every word click uses, so undo and redo cover hand edits and the splice (the watched edited video) goes stale and rebuilds from the new plan, as it does for any edit. A refusal (a special insert, a protected word, a piece at its minimum length) answers in plain words and writes nothing. The job payload now names each timeline piece's place in the plan, its span in the recording, whether it is a special insert, and the crossfade at the join into it, which is what the band's new gestures read. The splice builder fades a join by the length stored on the piece it plays into when one is set, and by the plan-wide Smooth the joins choice otherwise; with neither set, the graph is the plain concat it has always been.
- **Costs.** This change runs on your Mac: the endpoints and the splice render are local work and cost 0 cents. No model calls are made for these decisions.
- **The timeline can now be edited by hand (Batch C, items 1–8's page half).** The pieces on the timeline are the controls. Clicking a piece picks it (a blue ring) as well as its words. A Split at playhead button under the timeline cuts the piece under the playhead in two. Dragging a piece's left or right edge lengthens or shortens it, pulling back words or quiet the edit cut, or trimming more; the server clamps the drag at the next piece, at a protected word, and at a minimum length, and the line under the timeline says why it stopped. Dragging a piece sideways moves it to a new place in the order; it stops at a special insert, like every hand edit. Cmd+X, Cmd+C, and Cmd+V cut, copy, and paste the picked piece (or the picked words' span from the transcript; cutting words rides the word path), and paste lands at the playhead, splitting the piece there to make room. Delete or Backspace with a piece picked takes the piece out and the edit closes up, with no gap left. Where two pieces meet, a small diamond sits on the seam; clicking it opens the crossfade choices for that one cut (none, or a blend of 0.04, 0.1, 0.25, or 0.5 seconds), and a join with a blend set shows its diamond in blue. The new keys are in the How this works shortcut list, next to the ones already there. Typing in a field keeps the field's own clipboard and keys, as before. Undo and redo cover every one of these, since each lands as one history step.
- **Costs.** This change runs on your Mac: the gestures and their endpoints are local work and cost 0 cents. No model calls are made for these decisions.
- **The timeline tools moved below the band (Batch C fix-up).** The Split button row, the status line, and the crossfade choices first sat inside the timeline band, whose height is fixed. They squeezed the band's scroll row, the only row that can shrink, down to nothing at smaller band heights, and painted over the top of the pieces, so pieces there could not be clicked at all. They now sit right below the band, where there is room, and the band's own rows are exactly the ones 0.15 had. Found by the fit battery: every band gesture that needed a real click missed, while the same actions worked from the keyboard.
- **Costs.** This change runs on your Mac: moving three page elements costs 0 cents. No model calls are made for these decisions.
- **A press on a piece now belongs to the piece (Batch C fix-up 2).** A press on a timeline piece bubbled up to the track, whose scrub took over the gesture and the pointer capture, so the piece never received the click that followed: clicking a piece did not pick it, and clicking a crossfade diamond did not open its choices. The piece and the diamond now hold their own presses. Found by the fit battery and proven with a recorded event trace: before, the click landed on the track; after, it lands on the piece and the pick sticks.
- **Costs.** This change runs on your Mac: stopping two events from bubbling costs 0 cents. No model calls are made for these decisions.
- **The crossfade diamond is its drawn size again (Batch C fix-up 3).** The page's base button rule gives every button a minimum height of 48px, and the diamond at each cut inherited it: the button stretched to 48px tall inside a 30px track, and the edge grips painted over its middle, so clicking the diamond started a trim instead of opening the crossfade choices. The diamond now opts out of the minimum (the way the timeline's dots already did) and paints above the grips inside its own small patch. Found by the fit battery, proven by computed-style reads in a live page.
- **Costs.** This change runs on your Mac: two lines of style cost 0 cents. No model calls are made for these decisions.

## 0.15.0 - 2026-10-30

The round from Justin's Oct 30 hand-test of 0.14, built batch by batch. Batch A is the first change; the rest of the round follows in the same version.

- **In the Cleanest cut, a repeat the app itself detected is now struck by default (Batch A, item 22).** Before, a detected repeat could wait in "Recommended to remove" even in the strictest way of working: an earlier take the retake check flagged or could not settle, and a directed cut the editor held back whose words are said again in what stays, both sat in the edit until you called them. Now, in the Cleanest cut only, those spans are cut at the moment they are spotted, struck through in place like any other cut. The recap names how many were struck automatically, and any of them can be brought back the usual way: pick the words and choose Bring back, or undo. Leaving a detected repeat in now takes that explicit ask, which is the way round it should be. In Tailor it with me, the same spots still wait for your call, exactly as before. Protected parts of a video are never struck this way, a change big enough to trip the 25 percent hold still waits, and a held cut whose words are not a repeat of what stays keeps the old behavior in both ways of working.
- **Costs.** This change runs on your Mac: comparing the words and applying the cuts are local work and cost 0 cents. No model calls are made for these decisions.
- **Clicking away now puts the pick and its card away, every time (Batch B, item 19).** Before, clicking the video or the timeline closed the picked-words bar, but clicking empty space in the transcript did nothing, and the "Recommended to remove" card never closed by clicking away at all; both could sit there until you found the Close button or pressed Escape. The cause was that closing lived in three separate places and the card was never on any of them. Now one place owns closing for the bar, its menu, and the card: a click anywhere outside them puts them away, and Escape does the same. A note you have started on a spot is still never lost: closing the card keeps the draft on that spot, and it is there when you open the spot again. The "Recommended to remove" card is also slimmer (item 21): it shows the words, one line of advice, and Cut it / Leave it / Close, and that is all; the explanation paragraph, the note box, and the take-that-stayed block are gone from it.
- **Picking takes a real pick, and slow taps count (Batch B, items 18 and 20).** A drag only picks when it ends inside the transcript; a drag that ends on the video or anywhere else leaves nothing picked and nothing highlighted. And two taps on the same word count as a double-click pick even when they are spaced out, up to about a second apart, so slow taps open the actions the same way a quick double-click does. One click still only moves the video to that word.
- **Plainer names on the pick's actions (Batch B, items 27, 28, and 29).** "Fix the words" is now "Change words" everywhere. The "Clear the pick" menu item is gone: the pick goes away by clicking elsewhere or pressing Escape, and the bar's own close button is now an × with the name "Done". "Hide" is now "Erase", because the words leave the reading view; the line under the transcript says how to bring them back. The icons in the transcript and the bar all say what they do when you hover over them, and the "Teach the app" button in the top bar stays an icon with a hover name instead of turning into words when clicked. Removals still only happen from the pick's own surface, never from a stray click on a margin icon.
- **Costs.** This change runs on your Mac: the closing, picking, and naming are local work and cost 0 cents. No model calls are made for these decisions.
- **The first page is one job now, and bringing in a video happens in a pop-up over the editor (Batch C, items 1, 2, and 3).** Before, the first page filled up as you worked: the preview, the direction box, the stages list, and the Remove choices all stacked onto it. Now the first page shows only the app's name and the one job, bring in a video, with one short line. Dropping or picking files opens the editor page, blank until a job is processed, with the intake pop-up floating over it; the preview and the choices live in the pop-up, and closing it returns you to the same place on the editor page. Files dropped together are listed in the pop-up as parts of one job: drag a part to move it, or use its up and down buttons, and that order is the order the parts are processed in. Several videos are joined into one video in that order before the first edit runs on them; if parts cannot be joined, the app says so and takes in the first part in your order. The longer explanations did not go away: the stages list, what an edit costs and what that covers, and the test-video explanation now live behind "How this works".
- **The top bar and Settings are calmer (Batch C, items 4 and 11).** The Settings button now sits on the header's own line and says "Settings" beside its icon. Inside, Settings is short labeled rows, Problem report, Text size, Layout, Saved transcripts, and Sign-in, with at most one short hint line each; "Reset layout" is a plain button whose long sentence is now the short hint "Panel sizes go back to how they started."
- **One name, and a plainer way line (Batch C, items 8 and 10).** "Make the first edit" now reads "Make the edit" everywhere, the same name on the front door and in the editor. "Editing style: Justin's way" is now a plain line, "Your usual way", in the intake pop-up and the editor's choices, with a "Change" button that opens the same pick as before; the word "style" has left those surfaces.
- **Processing shows itself on the editor page (Batch C, item 12).** While a job processes, a progress bar runs in the intake pop-up and in the transcript pane head's place, the video area dims with a spinner ring in the app's blue, and the transcript area shows flashing dots with "Writing down the words…". Each state clears the moment its real content lands. A first edit's bar follows the run's real stages; the editor's own Make the edit run reports no stages to the page, so its bar is an honest moving bar with no made-up percent.
- **Costs.** This change runs on your Mac: the pop-up, the ordering, the joining of parts, and the loading states are local work and cost 0 cents. No model calls are made for these decisions.

- **Every dictation button is the microphone now (Batch D, item 7).** Before, most dictation buttons were word buttons that said "Dictate" (and the pop-up note's said "Dictate the note"), while the comment box's was already a microphone icon. Now every dictation control in the app is that same microphone icon button, the same size as the app's other icon buttons, with its name in the hover tooltip. The word has left the buttons; the status lines beside them still say in plain words what is happening while you record.
- **Teaching keeps its graduation cap, and the intake pop-up offers it (Batch D, items 5 and 6).** A short research note compared how Descript, CapCut, and Claude present teaching and learning (filed with this round's evidence): all three lead with words and have no teach icon of their own, so there was no plainer symbol to borrow, and the cap, the example you gave, stays. It is now the one icon everywhere teaching appears: the top bar, the teach card's heading, and the new intake step. That step is new: when you bring in a video and the app has no rules taught by you yet, the intake pop-up offers one optional step, "Teach the app your way", which opens the same teaching card as the top-bar button. "Skip for now" dismisses it in one tap and never holds up "Bring these in", and once you have saved rules the step does not appear at all.
- **How involved you want to be is one pill (Batch D, item 9).** The two loose buttons, "Cleanest cut" and "Tailor it with me", are now one pill with two parts, in both the intake pop-up and the editor's choices: "Strictest" and "Leave more in". The part you pick fills with the app's blue and its words get heavier, so the choice reads even without the colour. The line under the pill says what the pick does in plain words: Strictest finishes everything the editor is sure about and strikes repeats it detects; Leave more in leaves more in and the editor asks before taking things out. Underneath, nothing changed: the runs still record the same two ways of working as before.
- **Costs.** This change runs on your Mac: the icons, the teach step, and the pill are local work and cost 0 cents. No model calls are made for these decisions.

- **The Comments pane is your editor now (Batch E1).** The right rail is a conversation with the editor. Its name sits at the top with a small round mark and a plain status line under it ("Ready when you are", "Working on it" while a run works); click the name to rename the editor, and the new name is remembered for you across every video, not just this one. The top-bar button says the editor's name too, and one waiting count, held items plus replies you have not seen, shows the same in the panel and the top bar. Closing the panel is "Hide editor", and the app remembers which way you left it.
- **One thread per video, and it keeps everything.** Your messages sit in the dark block; the editor answers in plain text. When it changes words, its answer names each spot by the time those words sit at in the edit now, and tapping that line picks the words in the transcript, the same as picking them yourself. If those words are later cut entirely, the line dims and says so. Questions about the record ("why was this cut", "what did you take out near the end", "what did that run cost") are answered from the record the app already keeps, with no run and no model call. When Make the edit finishes, its summary is posted into the thread, including how many repeats were struck automatically in Strictest. Nothing in the thread is ever rewritten or deleted; a correction is a new entry.
- **Held items, pins, and story questions live in the thread.** A "Recommended to remove" spot that waits for your call appears as a held entry with Cut it and Leave it buttons; deciding settles the entry where it sits, changes the words in place, and turns the timeline dot gray, without moving your place in the thread. Pinning a moment (the + button by the input, the pick's Comment action, or the timeline's comment button) attaches the moment over the one input; a sent pin also lands in the thread with its "At 1:24 in the video" line, markers and dots still take you to it and back, and once a run has used a pin it locks and says which run used it. The "Why did you put that back?" question moved out of the transcript header into the thread, with the same answers as buttons there. A saved rule is noted in the thread too.
- **One input, and asking is safe.** There is one box at the panel's bottom: a + for the moment, "Message" to type in, the microphone to dictate (the words land in the box, never sent on their own), and a round blue send button that appears only when the box holds words. Enter sends; Shift+Enter starts a new line. The old pin-a-comment box, overall feedback box, and comment cards are gone as separate surfaces; the comments underneath are unchanged, so markers, dots, and counts still work. Known voice commands typed or dictated into the box ("undo", "make the edit") run as commands and the thread notes what they did. And an ask that would need a run that spends money does not run on the first send: the editor answers with what it would cost and waits for your plain yes.
- **One name for the way of working.** The run recap called the two ways "Cleanest cut" and "Tailor it with me" while the controls have said "Strictest" and "Leave more in" since Batch D. The recap and the thread now use the controls' names, so the name is the same everywhere. (The Batch A note above still describes the strict way's behavior in the older words; the behavior did not change.)
- **Costs.** The thread itself, the record answers, renaming, and reading are local work and cost 0 cents. Asks that run the editor's direction keep the same estimated costs direction runs have always shown, and the reply names the run's cost when money was spent.

- **Picked words can be sent to the editor, and several notes go together (Batch E2, item 15).** The picked-words bar (and its More menu) now has "Send to Editor", and Cmd/Ctrl+Shift+M does the same thing. It carries the picked words into the Editor panel as a queued note: a small chip above the message box showing the moment ("At 1:24 in the video") and the first few words, with an × that takes off just that note. Picking more words and sending them too queues another chip, and carrying words never unpicks them in the transcript. Type or dictate one message covering the notes and press the one "Send to edit" button: the notes and the message go as a single ask, each note is also pinned on its moment for the run, your message in the thread lists every moment it carried, the editor answers once, and the queue clears. An ask that would spend money still waits for your plain yes first, exactly as before. The bar's Comment button still pins one moment the way it did.
- **Clean-up choices live in the Editor panel now (Batch E2, item 17).** Above the message box there is a "Clean-up" row of chips: Remove retakes, Word gaps, Remove filler words, and Hide ums and uhs in captions. Each chip shows whether the choice is on or off, and pressing it flips the same stored choice its existing control sets (the Edit settings choices for the first three, the captions choice for the last one, caption file rebuilt or removed to match, as today), and the thread gains a short editor line saying what changed, so the panel stays the record. The Remove choices count from the next Make the edit run, and the chips and the older controls always agree because they are the one setting. There is no studio-sound choice anywhere in the app, so no chip was invented for it; the intake pop-up's Remove choices are still the first edit's stage choices and stay where they are, and the More rail keeps its other groups (your rules, the revised edit, history).
- **Story questions explain themselves (Batch E2, item 16).** A "Why did you put that back?" entry in the thread now has a small "i" button ("More about this") that opens the editor's reason right in the entry: the words the question is about, why the editor flagged or cut them, and what your answer teaches it. Before, the entry showed only the question and the answer buttons, and the reason lived elsewhere.
- **Costs.** Carrying words, queueing, flipping clean-up choices, and reading the reason are local work and cost 0 cents. A Send to edit ask that runs direction costs what a direction run costs, named before it runs when a sign-in key is saved, as before.

- **A pane can never be lost again (Batch F, item 14).** Before, hiding the transcript from its own heading and closing the Editor panel were remembered in two different places, so together they came back on the next visit as the video alone beside a stretch of empty black, and the only way back for the transcript was a small picture button with no words on it. A remembered full-width video did the same thing with no way back in the top bar at all. Now the top bar always carries one button per pane, named in plain words, Video, Transcript, and Editor, each showing whether its pane is up and putting it back or away with one click. A saved layout that would open with more than one pane hidden is repaired as it loads: the transcript comes back, because the words are the work. A saved pane width below the smallest usable size is repaired to the pane's starting width instead of being kept. And Reset layout still brings everything back at once.
- **The transcript and the Editor can swap sides (Batch F, item 14).** A Swap button in the transcript's heading, named "Swap transcript and Editor sides", trades the two panes' places in the row. Each keeps its own width, the dividers resize the same panes as before, and the choice is remembered with the layout.
- **The transcript reads narrower, and the Editor can take two widths (Batch F, items 26 and 36).** The words now wrap at a narrower measure, about 57 characters a line instead of 68, and a Reading width button in the transcript's heading switches between Narrow and the old Wide; the pick stays on the same words across the change, because only the wrapping moves. The Editor panel's Make wider is now a second width, about double, taken from the transcript's share: the transcript keeps its smallest usable width, every pane stays in the row, and the button reads "Back to normal" while the Editor is wide.
- **Change words opens at the words (Batch F, item 37).** The correction box used to open at the bottom of the transcript pane, away from the words it was fixing. It now opens right next to the picked words, the same place the picked-words bar sits, and follows the words as you click the first and last of the part to change. Clicking away or pressing Escape closes it and puts you back in the transcript, and the words it will change do not move.
- **One name for the carry shortcut (Batch F, drive-by).** The printed shortcut list called Cmd or Ctrl + Shift + M "Comment on the picked words", but since Batch E2 that key sends the picked words to the Editor. The list now says "Send the picked words to the Editor", so the printed name and the key do the same thing.
- **Costs.** This change runs on your Mac: the layout, the wrapping, and the box placement are local work and cost 0 cents. No model calls are made for these decisions.

- **Keyboard editing, the way you asked for it (Batch G, item 23).** With words picked, Cmd or Ctrl + Backspace now strikes just the picked words, and plain Backspace or Delete removes the whole line the pick is in, the full paragraph of the transcript. Both go through the same cut the action bar's Cut uses, so the words change where they sit, the edited time and the timeline update in the same moment, the preview follows the new plan, and undo brings everything back. The printed shortcut list and the Cut item in the pick's menu name the new keys. The keys never fire while you are typing in a box, while a pop-up is open, or when nothing is picked.
- **Erase the quiet inside a word gap (Batch G, item 24).** Double-clicking the dots between two words now picks that gap, snapped to the exact silence between the word before it and the word after it, and a small bar names the gap's length with an Erase button; Backspace or Delete does the same. Where the gap sits where two kept pieces meet, erasing shortens that pause to the shortest the plan allows, through the same pause-shortening the silence work uses, and the edited time drops at once; undo restores the quiet. One honest limit: a gap inside a single kept piece has no pause of its own in the edit plan, so picking it works but erasing says plainly that there is no pause there to shorten, rather than changing the plan a second way.
- **Clicking a word lands everything on that word (Batch G, item 32).** Before, a click set the player's time straight from the word's place in the original recording and let the screens work out the edited place from that, and the working-out stopped at the end of the word before across the silence in front of the clicked word, so the cursor, the clock, and the marked word could sit at the start of that gap instead. Now the click seeks by the word's own place on the edited clock, sets the clock and the timeline cursor in the same moment, and marks and scrolls to the word whether Follow is on or paused, so the video, the timeline, and the transcript agree on the same word at the same time.
- **Costs.** This change runs on your Mac: the keys, the gap trim, and the click are local work and cost 0 cents. No model calls are made for these decisions.

- **The word states can be told apart at a glance (Batch H, item 25).** Before, a kept word, a word the app recommended removing, and a corrected word were all the same brightness; only a faint tinted wash told them apart, and a cut word was dimmed so far down it was hard to read at all. Now each kind of word has its own look, in a form, not just a colour. Kept words are plain bright text with no mark on them. Cut words are struck through and dimmed to a step that stays easy to read, because a cut word is still a word you may bring back. A cut the app was not sure about keeps its orange wash and a solid line under the words, sits one brightness step above a sure cut, and is struck through like any cut. Words recommended to remove keep their light red wash at full brightness and now have a dotted line under them, the only dotted line under any words, with the same diamond marker at the end of the spot as before. Corrected words keep their blue wash and solid line underneath. The timeline at the bottom tells the same story the same way: its cut words used to be printed in red, and are now the same struck, dimmed words as in the transcript, and a merged block of cut words has a line drawn through its middle.
- **The transcript's small print is on the same sizes as the rest of the app (Batch H, item 25).** The time at the start of each paragraph, the speaker name, and the shortcut print in the pick's menu were a size nothing else uses; they now match the app's small-print size. The card that opens from a diamond marker or a removed-words dot, the "why" and "recommended to remove" card, had its words, its title, and its note box at a size nothing else uses; they now match the app's reading-text size. The words of the transcript themselves are the same size as always.
- **Costs.** This change runs on your Mac: the colours, lines, and sizes are local work and cost 0 cents. No model calls are made for these decisions.

- **Fit is a reset now, not a lock (Batch I, item 31).** Before, pressing Fit put the whole edit in view and the timeline would not move left or right from there; you had to unfit first. Now Fit is just a reset: as soon as you click and drag on the timeline, the view leaves the fitted size at the zoom you were using (or the reading size) and pans with your drag, and Fit can be pressed again at any time to reset once more. Shift+Z still toggles between Fit and the zoom you were at.
- **The timeline zooms the way you expect, and its joins can be smoothed (Batch I, item 30).** Holding Cmd or Ctrl and scrolling over the timeline zooms around the spot under your pointer (that was already there), and pinching on the timeline now zooms the same way, through the same one zoom the slider, the + and - buttons, the keys, and Fit all use. The ruler along the top shows time labels that step honestly with the zoom, 1 second apart up close and a minute apart at Fit, on the edited clock. The sound wave under the segments is drawn from the edit's own shape, the kept pieces in their edited order, and stays drawn at reading zoom. And a new choice in Edit settings, "Smooth the joins (crossfade)", is on unless you turn it off: where two kept pieces meet in a video the app itself builds, the sound now fades across the join for 40 milliseconds so the cut does not click or pop. The picture still cuts exactly as before; only the sound is smoothed, and the recap says so when a build used it. One honest limit: a finished video built by the runner's own render script is untouched by this choice, because that script is not part of this app; every video the app builds itself (the review video, and a dropped video's finished render) carries the fade.
- **The Remove choices live above the timeline, and in the transport row too (Batch I, items 33 and 35).** Edit settings has moved out of the Make the edit card into a slim bar directly above the timeline band, with the same controls and the same saved choices; the card keeps Make the edit and the format choice. Once a first edit exists, the transport row's empty space also carries Remove retakes, Remove filler words, and Word gaps as small labeled buttons showing whether each is on. They are the same one setting in three places (this bar, the transport row, and the Editor panel's Clean-up chips), so flipping any one of them flips them all, and they can never disagree. There is still no studio-sound choice anywhere in the app, so none was added here.
- **The Make the edit area is cleaned up (Batch I, item 34).** Vertical and Landscape are now one pill with two parts, "Vertical 9:16" and "Landscape 16:9", and the part you pick fills with the app's blue, like the other pills. The round button under them that turned red and pulsed with no name on it was the Talk button: it listens for a spoken command ("remove retakes", "undo", "make the edit") and runs it. It is now the same microphone button as every other Dictate button in the app, beside a plain label that says what the row is for: "Say a direction".
- **Every quick action is on the top bar, and the transport words sit beside their icons (Batch I, items 38 and 39).** Bring in, Teach, Video, Transcript, Editor, How this works, and Settings are all direct buttons on the top bar; nothing lives only behind a three-dots menu, and the bar has no three-dots menu at full width. In the transport row, each button's word now sits beside its icon on one line, at the size the rest of the app uses for labels, instead of being typeset under the icon: Prev change, Start, Back 5, Play, Fwd 5, End, Next change. Back 5 and Fwd 5 are the same width as each other, the zoom percent is a plain readout beside the zoom buttons rather than part of one, and Fit stands at the row's full height. (There is no Find button in the app and never was; the word in the feedback list came from "Fwd 5", which got the same treatment.)
- **Costs.** This change runs on your Mac: the zooming, the panning, the layout, and the crossfade are local work (the fade is part of the same ffmpeg build the app already runs) and cost 0 cents. No model calls are made for these decisions.

- **A brought-back word's "why" answer can no longer be lost (Batch J).** The app answers each request on its own thread, and the learning store and the conversation thread are both files that get read, changed, and written back. Two changes landing at the same moment could both read the same starting file and write in turn, so the second write silently erased the first one's record. That is how the record for words you brought back could go missing while its "Why did you put that back?" question stayed in the thread: the question was there, but answering it failed because the record it answers was gone. Now every read-change-write of those two stores happens one at a time, and the files themselves are written by replacing them whole, so a reader never sees a half-written file. A question in the thread always has its record, and the first answer settles it.
- **Reading zoom always draws the timeline's words (Batch J).** The zoom slider's steps line up from its lowest setting, so asking it for the reading size could land a hair under, at 119.96 pixels per second instead of 120, depending on the video's edited length. The readout rounded that to "Zoom 100%", but the word row's rule was a hard at-or-over 120, so the band claimed to be at reading zoom and drew no words. A zoom aimed at the reading size now lands exactly on it, from every control, so the name and the word row always agree.
- **Two small finishes from the symmetry re-measurement (Batch J).** The whole app was measured again against the 0.14 symmetry ledger. Two values had drifted off the shared set and are back on it: the settings bar's three pass buttons were 40px tall, a height nothing else uses, and are now the 36px small-button height; and the Editor panel's name, which is its heading, now carries the same 700 weight as the app's other headings. The merged cut blocks on the timeline draw their strike line with a plain line element instead of a gradient layer, so the one-blue, no-gradient-blend look is literally true in the code as well as on screen.
- **Costs.** This change runs on your Mac: the store locking, the zoom landing, and the finishes are local work and cost 0 cents. No model calls are made for these decisions.

## 0.14.0 - 2026-10-30

The finishing pass on how the app looks and answers, from the two design reviews of 0.13. One system, applied all the way around.

- **One blue.** Every blue button, link, badge, and marker is now the same blue (#0064d1). Before, the main button was a blend of three blues, the on switches used another blue, and links used two more. Orange now means one thing only: a decision the app made ("Kept the fuller take. This copy is out."). The Follow button's paused state no longer borrows the orange; it reads as off, in gray.
- **Buttons come in one set of shapes.** Every icon button is the same size (38px, same corners, same icon size), including the zoom buttons and the buttons in the comment box; Fit is the same width as its zoom pair. Action buttons are one height (48px), the small buttons inside cards are one height (36px), and the timeline row is one height (46px). The round Play button keeps its shape; it is the one exception, on purpose. Cards all use the same corner now, and the two typing boxes match the other fields.
- **One set of words sizes, one font.** Card headings are all one size, button words are two sizes, reading text is one size, and small notes are one size. A few buttons quietly used a different font (Arial) from the rest of the app; everything now uses the same system font.
- **Spacing and edges line up.** Spacing now comes from one short scale (4, 8, 12, 16, 22), the gaps between the columns match on both sides (16px), and the three panel headings start on the same line at the same height. The zoom controls sit in the timeline's own row instead of being placed by a one-off margin, and the Back 5 / Forward 5 buttons are the same width, like a pair should be.
- **Comment cards fit their column.** A card's time and format chips wrap inside the card, so the Delete button is no longer cut off at the column's edge, and the badges at the top of a card stay on one line at one height.
- **The action bar sits at the words you picked.** Before, it always opened at the top of the transcript, however far down your pick was. The cause was plain: it measured the pick's position from the start of its paragraph instead of from the panel, so every pick looked like it was near the top. It now opens just above your words, and just below them when your pick is at the very top of the panel. It still never leaves the transcript panel.
- **Cut and Bring back answer the moment you click.** The words change the moment the button is pressed; the saving catches up behind it. Before, nothing moved for a second or two, and the click felt dead.
- **Smaller finishes.** Picking words the app marked "Recommended to remove" now shows that advice in the bar ("Earlier take, said again right after. Cut it, or leave it as is."), with Cut first. Clicking the thin overview strip at the bottom of the timeline now jumps there, like the rest of the timeline. The "Why did you put that back?" question is now a short line under the transcript heading instead of a card parked at the bottom of your reading. The clock by the play button is labeled "Edited time", since the video's own clock counts the original time and the two drift apart after cuts. And at the zoomed-out view the timeline no longer prints its words as a smear; the blocks stay as a map, and the words come back at reading zoom.
- **Two things left as they are, on purpose.** The "make wider" icon in each panel heading keeps its glyph: the app's own icon set has no plainer picture for that job, and the other arrows already mean other things. "Edit choices" keeps its wider button shape: it belongs to the same 48px action family as the other worded buttons.
- **Costs.** Everything in this version runs on your Mac: colors, layout, and the click answers are local work and cost 0 cents. The cost of making an edit is unchanged from 0.13.

## 0.13.0 - 2026-10-29

The app now decides two kinds of repeats itself instead of asking you, from Justin's Oct 29 evening review of 0.12.

- **A try that stops mid-word is out.** When a take stops early and its last word is cut off partway (like "f-" or "b-"), the app takes that try out and keeps the take that follows. Before, that try could be left in the edit because the pass was not sure about it.
- **A line tried several times in a row keeps only the last full try.** When the same line is started again and again and the last try says it in full, the app takes every earlier try out and keeps the full one. Before, a line that included a word like "don't" was always handed to you to decide, even when every earlier try was plainly a restart of the same line, so all the tries stayed in the edit.
- **Both kinds of cut can still be brought back.** Each one is marked in orange as a cut the app was not sure about, with the same note as before: "I was not sure about this one. I took it out so the last take stays. Double check here if you want it back." Pick the words and choose Bring back to return any of them.
- **Real choices still come to you.** A repeated line where the copies say different things, and a set of tries whose last try does not finish the line, are still flagged for your decision, exactly as before. Protected parts of a video still cannot be cut by these cuts.
- **Costs.** This change runs on your Mac: measuring the words and comparing the tries are local work and cost 0 cents. No model calls are made for these decisions.


Gestures (item 2)
- One click on a word now only jumps the video there; it no longer
  picks the word or opens anything, and it clears any pick that was
  showing. Double-click picks exactly that word and opens the
  action bar. Dragging across words paints the pick while you drag
  and opens the bar when you let go. Shift+click extends a pick
  only when a pick already exists.
- Retired behavior, named: the 0.12 one-tap restore (one tap on a
  struck word brought it back) is gone. To bring a word back now,
  double-click or drag to pick it, then Bring back. The orange
  "not sure" flag on unsure cuts and its wording stay.

One surface for picked words (item 3, revised to Justin's
Descript recording)
- The one surface is a compact bar at the pick, Descript-style:
  the old floating bubble is restyled into it, it floats inside
  the transcript pane at the picked words, and the picked-words
  strip under the panel is deleted entirely (element and JS).
  The bar carries a tiny word count ("3 words") as quiet text,
  icon buttons with plain-words tooltips — Comment (speech
  bubble), Cut (scissors), and one contextual slot: Bring back
  (undo arrow) when the pick is out, or Keep the cut (check)
  when the pick carries the app's pending decision — and a "…"
  overflow button.
- The decision line rides with the bar: when the pick overlaps a
  pair the app decided or flagged, the bar shows the one line
  ("Kept the fuller take. This copy is out." for a decided pair;
  the flag's own reason otherwise). It never asks which copy
  stays.
- The "…" overflow menu lists every action for the pick with its
  shortcut printed next to it: Cut — Backspace; Comment —
  Cmd+Shift+M; Fix the words (no key); Hide (no key); Clear the
  pick — Esc. Fix the words and Hide live only in the overflow.
  The standalone "Fix words" button is gone; the overflow's Fix
  action opens the same fix panel.
- The bar is positioned in the transcript pane's own coordinates
  and clamped to the pane's visible box, flipped above the pick
  near the bottom, so it can never cover the timeline band or
  the transport row.
- Clicking a retake flag in the text now picks the flagged words
  and opens the bar with the decision line and Bring back,
  instead of opening the old flag pop-up. (The shared note
  pop-up other features use is untouched.)
- The timeline's Comment button no longer floats over the band;
  it is a fixed speech-bubble button in the transport row.

Dismissal (item 5)
- Pressing down on the video pane, the timeline band, or the
  comments column closes the bar and clears the pick. A comment
  draft is never discarded by this; only transcript-anchored
  state closes.

Timeline sync (item 1)
- Seeks that start from the timeline band (track drag, ruler,
  overview, segments) now run the transcript sync too: on release
  the word at the new playhead is marked current and scrolled
  into view, and a light sync stays live during the drag.
- The timeline block for the playhead word now lights on the
  band, in sync with the transcript's current word — after a
  word click, after a band click, and during playback (T4). A
  word printed in full lights itself; a word merged into a
  group lights its group.

Layout (item 6)
- Panel header buttons are fixed-size icon buttons (38px, capped
  at 150x44, no flex growth), so a header button can never stretch
  into the giant "Make wider" button from the 0.12 screenshots.
  Make wider / Back to normal, Hide, and Swap sides are icons now
  (arrows and sidebar glyphs) with their names in tooltips; the
  expand state shows as a highlighted fill.
- Pane minimums are guaranteed by the grid: video 260px,
  transcript 340px, comments 260px. Below the three-column width
  the comments already ride in the bottom row / behind the
  top-bar Comments toggle rather than crushing the transcript.

Calm header, top bar, and right rail (items 7 and 9)
- The transcript header is one row, at most 44px: the title,
  Follow as a small playhead-glyph toggle (tooltip and
  aria-pressed carry its state; amber when paused; tapping it
  returns the view to the playhead), the hidden-words button
  when it applies, and the pane icon buttons. The word-count
  headline and the "Times match the video you are watching."
  sentence are removed from the header; the count survives as
  quiet text inside the More card.
- The top bar is icon buttons: + (Bring in another job),
  graduation cap (Teach the app), question circle (How this
  works), speech bubble (Comments — this button IS the comments
  toggle now: it opens and closes the column, with a count badge,
  a state-flipping tooltip, and the highlighted on state), and a
  gear (Settings).
- The right rail takes Descript's calm form: the comments
  column header carries a small speech-bubble glyph, and the
  More card's groups (Overall feedback, Your style, Revised
  edit, Finished edit, History) get quiet group headings with
  small icon + short label action rows. What the groups
  contain is unchanged.

Shortcuts (item 9)
- The shortcut map is one data table in the page (SHORTCUTS);
  the How this works list is generated from it, so the list and
  the keys cannot drift apart. The hand-written shortcut prose
  line is gone (its on/off switch stays).
- J/K/L work as a shuttle: L plays forward and repeat presses
  double the rate to 4x; J rewinds in growing steps (a plain
  video element cannot play backwards); K stops and resets;
  holding K and tapping J or L steps one frame.
- Up/Down and the ; / ' keys step to the previous / next change.
- Zoom keys are plain + and - now; Cmd/Ctrl+= belongs to the
  browser and no longer zooms the timeline. Shift+Z Fit and
  Cmd/Ctrl+scroll zoom are unchanged.
- Cmd+Z undo and Cmd+Shift+Z redo (both already existed through
  the History checkpoints) are now in the documented map, as are
  Space, M (pin a note), Cmd/Ctrl+Shift+M (comment on the pick),
  and Backspace — whose list entry now spells out that it cuts
  the picked words, not a segment.
- Esc is in the map as "Clear the pick / close the surface":
  it folds the bar's overflow menu first, then clears the pick
  (it also closes a pop up, through the pop-up's own handler).
- Item 8's manual-editing keys (split, nudge) are deliberately
  not in this round's map; that work is a later gate.

=========================================================================

Timeline band at its smallest height (item 6 geometry):
- The transport row's Comment button (item 3) drew its speech-
  bubble icon at the SVG's full intrinsic size (about 69px),
  because it was the one icon button in the page with no icon
  size rule. The button stood about 95px tall and the transport
  row 101px instead of 52px, and inside the fixed-height band
  that extra height pushed the waveform row about 52px below
  the band's bottom edge at the band's minimum height. The icon
  is now sized 20px like the page's other icon buttons, the
  transport row is 52px again, and the band's inner column no
  longer spends the old standalone panel's 4px/14px margins
  inside the band. At the band's 180px minimum the waveform
  row's bottom now sits inside the band (889px against the
  band's 890px bottom in the fit battery's drag proof), and
  every other band row fits inside it too. The band's minimum,
  default, and drag behaviour are unchanged.

Test notes for this round: three older tests changed on purpose, each because 0.13 retired what it pinned. The picked-words strip test now asserts the strip is gone (item 3; superseding checks in test_v12_bubble.py and test_v09_transcript.py). The More card heading test now pins the quieter 13px group headings (item 7). The paragraph-render tests' helper now stubs the action bar's new updater name. The long conversion test is unchanged; it is load-sensitive in a full run and passes on its own.

Manual editing (split a segment at the playhead, drag segment edges to trim, short crossfade at cuts) is researched and planned, and lands as its own 0.14 update right after this one, so this round's fixes ship verified instead of rushed.

Costs for everything in 0.13: all of it runs on your Mac. Gestures, the action bar, the layout, the icons, and the shortcuts are local work and cost 0 cents. No model calls were added.


## 0.12 - 2026-10-29

The timeline round from Justin's Oct 29 review of 0.11.1. Change 1 of the round: the timeline band itself. (The right column, the left column, and the selection bubble are their own changes, still to come.)

- **The timeline band zooms and scrolls, and the words are printed on it.** The band's rows used to be squeezed to fit the window's width, so on a long video the pieces shrank to slivers and there was nothing to read. Now the ruler, the segment blocks, the comment dots, the captions row, and the sound wave live on a strip that is as wide as the edit is long times a zoom, inside a window you scroll sideways. Zoom runs from Fit (the whole edit in view) up to 200 pixels per second of video. Inside each segment block, the words themselves are printed: a word shows as full text only when its space is wide enough for the whole word, measured the way the page measures text; words that do not fit merge into a plain group block, so no word is ever cut off and no dots ever stand in for one. At the reading size, 120 pixels per second, the words read like the transcript. Cut words keep the dimmed, struck look they have in the transcript. The wave draws only the part of the strip you are looking at. The ruler's numbered marks step with the zoom: far apart when the whole edit is fitted in, close together when you are zoomed in. Clicking the band, the ruler, the wave, or a caption still jumps the video to that spot, exactly as before, and the transport buttons and the clock are still on the band.
- **A thin overview strip sits above the ruler.** It always shows the whole edit at once, segments and comment dots, with a box around the part the zoomed strip is showing. Clicking the strip centres the zoomed window on that spot; dragging the box scrolls the strip. The box is there at Fit too, covering the whole strip, so where you are in the edit is never a guess.
- **Zoom controls on the band, in plain numbers.** A minus button, a slider, a plus button, and a Fit button sit on the band's transport row, with a readout that says the zoom as a percent: "Zoom 100%" means the reading size, 120 pixels per second. The Fit button toggles between the whole edit and the zoom you were just at. The keys work too: Cmd or Ctrl with = zooms in and with - zooms out, around the playhead when it is on screen; Shift+Z is the same Fit toggle. Holding Cmd or Ctrl and rolling the mouse wheel over the band zooms with the spot under the pointer staying put; a plain wheel roll scrolls the strip sideways. Every zoom control is a real button or slider, so the keyboard reaches all of it.
- **Playing follows the playhead.** Zoomed in, when the playhead reaches the right edge of the visible window, the strip pages over so the playhead comes back in at the left and keeps playing. The transcript's own follow works exactly as it did.
- **The band remembers, and its minimum grew by one row.** Your zoom and scroll position are saved with the rest of the layout and come back when you return to the job; Reset layout puts the zoom back to Fit along with everything else. Because the overview strip is a new row inside the band, the band's smallest height rose from 156px to 180px and its starting height from 176px to 200px, so every row still fits inside the band at its smallest, and the band still gives up height before the workspace above it does.
- **Costs.** Everything in this change runs on your Mac: measuring words, drawing the strip, and remembering the zoom are local work and cost 0 cents. No new network endpoints, no model calls.
- New tests cover the zoom range and controls, the word-fit rule with its no-truncated-words rule, the scroll window and both wheel behaviors, the Fit toggle, the playback follow, the ruler's stepping, the layout store's new keys, the overview strip and its box, and the waveform's visible window (tests/test_v12_timeline.py). Four older tests changed on purpose, all for the same reason (the band's minimum and default heights rose by the overview row's height, 156px to 180px and 176px to 200px), and their superseding check is BandMinimumTests in the new file, which pins the new numbers directly: BandMarkupTests.test_band_spans_the_full_width, BandHeightTests.test_drag_is_wired_to_the_top_edge_with_a_minimum, RowBandFitTests.test_band_yields_to_its_minimum_before_the_row_yields, and RowBandFitTests.test_band_minimum_keeps_every_row_inside_the_band, all in tests/test_v111_timeline_band.py.
- **The comments column is one calm column with one scroll (change 2 of the round).** The right column used to read as seven stacked cards. Now the column itself is the only thing in it that scrolls, and it holds, in order: the comment box (same box, same buttons, same pinning, nothing about how a comment is written or saved has changed), the comment list, and one closed section at the foot called "More for this video" that holds Overall feedback, Your style, Revised edit, Finished edit, and History, each under its own quiet heading. Nothing was removed: every section, button, and list inside is the same one, in the same order, working the same way, including the Finished edit section staying hidden until a finished video is ready. Opening "More for this video" is one click when you want them.
- **Done comments leave the open list, and one button brings them back.** Each comment card now has a small Done button. Marking a comment done takes it out of the list, so the list shows only what still needs your eye. A quiet button at the head of the list says "Done (2)", with the real count; tap it and the done comments appear in the same list, in the same time order, shown faded so they read as finished. A done comment's button says Re-open, and tapping it puts the comment back in the open list. The counts stay right either way, and if every comment is done, the list says so in plain words instead of showing a blank space. Opening a done comment from its marker in the transcript brings it into the list first, so you always land on the card.
- **Clicking a comment card now jumps the video to its moment.** Before, only the small time at the top of a card jumped; clicking the card itself only flashed the comment's marker. Now a click anywhere on the card (not on its buttons) jumps the video to the comment's moment, the same jump the time button always made, and if the comment is pinned to words, those words light up in the transcript with the same wash the spoken word carries, so you see exactly what the comment is about. A comment pinned to a time alone still jumps, and marks nothing.
- **Costs.** Everything in this change runs on your Mac: filtering a list, counting, and lighting up words are local work and cost 0 cents. No new network endpoints, no model calls.
- New tests cover the one-scroll rule for the column, the column's order, the open-only default list with the Done toggle's count and the card-level Done and Re-open path, the More card closed by default holding the five sections with their key ids and the Finished edit card's hidden behavior, and the card click's seek and word marking (tests/test_v12_comments_column.py). No older test changed in this change.
- **The left column is the video first, and the words under it are gone (change 3 of the round).** The column used to hold the video and then a whole card: a heading, a paragraph explaining the card, Edit settings, the format buttons, Make the edit, Talk, and the recap and money lines, all inside the pane's own scroll, so the picture stayed small and the button sat below the pane's fold. Now the column gives its height to the video first: the picture is sized by the space it is given, its height as well as its width, so at wide windows it can grow past the size the old card left it, and the Make wider and drag choices work exactly as before. One row sits at the foot of the column with exactly two things in it: Make the edit (the same button, the same run) and an Edit choices button.
- **Everything the card held moved under that button, into Edit choices.** The Edit choices section starts closed. Open it and everything is there, the same controls in the same order working the same way: Edit settings (unchanged inside), the direction box and its Dictate, the format buttons, Talk, and the recap and money lines under the button they describe. Nothing was removed. With the choices closed, Make the edit is reachable without scrolling the left pane at 1440x900 and at 900x800; the pane can still scroll when the choices are open on a small window. The card's old heading is gone too, because the button in the row already says Make the edit.
- **The explainer paragraph is gone, and its one fact moved house.** The paragraph that sat at the top of the card ("Watch, set the choices, then make the edit...") is removed, so no explainer text sits in the column at load. Its one fact worth keeping, that the Remove choices live in exactly one place, Edit settings, was not stated inside Edit settings itself before, so that one short line now sits inside the Edit settings section, ahead of the choices.
- **Costs.** Everything in this change runs on your Mac: opening a section, sizing a picture, and remembering the layout are local work and cost 0 cents. No new network endpoints, no model calls.
- New tests cover the action row (exactly two controls, Make the edit inside it), the Edit choices section closed by default holding every moved control with its id, the wiring that opens and closes it, the gone explainer with its fact now inside Edit settings, the video-first sizing rules and the fixed row, and the front door rendering unchanged with no job loaded (tests/test_v12_left_column.py). Two older tests changed on purpose, both because Make the edit now sits in the action row ahead of the card instead of inside it after Edit settings: StagedFlowTests.test_each_pass_has_its_own_button_with_a_line_of_context and ReviewScreenTests.test_review_remove_settings_are_a_compact_details_affordance, both in tests/test_v09_layout.py, and their superseding checks are ActionRowTests and ChoicesRegionTests in the new file, which pin the button's new home and the pass list's new place directly.
- **Picking words shows their actions right on the pick (change 4 of the round).** Selecting words in the transcript settles a small pill above the last word picked (below it when the pick sits near the top of the panel, and always kept inside the panel sideways). The pill holds only the actions that fit those words, never more than four: Comment, with its shortcut printed on it (Cmd+Shift+M; Ctrl+Shift+M works too, from anywhere in the review while words are picked), Restore when any picked word is cut, Keep the cut when any picked word is an orange unsure cut, Remove when every picked word is in the edit, and Hide. Comment pins the picked words in the comment box, the same word pin a double click makes, and puts the typing there. Restore and Remove run the same range change the strip under the transcript runs, over exactly the picked words. Keep the cut settles a spot that is still waiting on its own card through that card's Leave it verdict; for an unsure retake cut, which has no card of its own, keeping the cut leaves the cut standing and says so under the transcript.
- **One tap still brings a cut back, and a drag no longer triggers it by accident.** One tap brings a flagged cut back, the same as any cut, exactly as before: tapping one struck word brings back exactly that word, orange unsure ones included, through the same restore the strip runs. What is new is that the tap has to be a tap. If the press grows into a drag or a text selection, the tap restore stands down, and the pill above the settled selection offers Restore for the whole pick instead. The word click itself is unchanged: it still seeks to the word and picks exactly it, and the restore runs on that pick only when the gesture stayed a click.
- **Hide tidies the reading view, and only the reading view.** Hide collapses the picked words into one small dotted chip in the prose, with the hidden count named on it. The words keep their place in the edit, cut or kept exactly as they were, the counts still count them, and playing steps over them without a hiccup. Hidden words are remembered for this job with the rest of the layout; while any are hidden, a "Show hidden words (n)" button sits in the transcript header and brings them all back, and Reset layout clears them with everything else. The pill goes away on Escape (with the focus returned to the transcript), on a press outside it and its words, when the transcript scrolls or the window resizes, or when any of its actions runs, and it never appears for a selection inside a typing box or a popup, while fixing words, or with no job loaded. The single-click word popup and the strip under the transcript work exactly as before.
- **Costs.** Everything in this change runs on your Mac: watching a selection settle, placing the pill, telling a tap from a drag, and remembering hidden words are local work and cost 0 cents. No new network endpoints, no model calls.
- New tests cover the pill and its buttons for each mix of words (kept, cut, orange unsure, mixed), the four-button cap with Hide yielding, the settle timing and the placement, flip, and clamp rules, Comment and its shortcut on the existing pin path, the range paths Restore and Remove reuse, Keep the cut's two ways of settling, the hide and show round trip in the layout store with its header button, every way the pill goes away and the places it never appears, and the one-tap restore's click-versus-drag line: a tap within 4px with no selection restores, a drag or a selection never does (tests/test_v12_bubble.py). No older test changed in this change.
- **Fit fits again, even after zooming.** After zooming in and moving around, pressing Fit made the timeline strip exactly as wide as the window, but the strip could still scroll sideways by 92px: a comment dot's hover tip, centred on a dot near the end of the edit and invisible until hovered, hung that far past the strip's end, and a ruler label and a caption block hung a few pixels past it too. Boxes centred on the strip (the tip, the ruler's labels) now shift sideways to sit wholly inside it, the same rule the hover time bubble already followed, and caption blocks stop at the strip's end, so at Fit there is nowhere left to scroll. Hovering a dot near the end of a zoomed strip still shows its whole tip, shifted inside to fit. Costs: local work only, 0 cents, no model calls. New tests pin the clamp and the measured case (tests/test_v12_timeline.py, FitOverflowTests). No older test changed.
- **On a small window the timeline's top row wraps instead of running off the side.** Under 720px wide, the clock, the transport buttons, and the zoom controls together were wider than the window, so the page ran about 47px too wide and could scroll sideways, which the small window layout never allows. The row now wraps there and the zoom controls take a line of their own, all at their usual size with nothing hidden, and the band grows by that line's height so the strip under it keeps its room; at 720px and up nothing changes. Costs: local work only, 0 cents, no model calls. New tests pin the wrap (tests/test_v12_timeline.py, NarrowWindowTransportTests). No older test changed.
- **The video starts bigger (final change of the round).** The video column's starting width rose from 350px to 410px, and in the narrower laptop range (1100px to 1400px wide) from 320px to 380px. The extra width comes out of the transcript's share, since the transcript is the column that flexes: at a 1440px window it still gets 680px, and the panel minimums, maximums, and fit rules are unchanged, so the three columns still fit at every window size with no sideways scrolling. If you already saved your own panel widths, nothing changes for you: your saved layout is read exactly as saved, and dragging and its memory work exactly as before. Reset layout now puts the video column back at the new, bigger starting size. Costs: local work only, 0 cents, no model calls. One new test pins the new starting widths and the fit at both ends of the range (tests/test_v11_layout.py, PanelWidthLimitTests.test_video_column_starts_wider_in_v012). Older tests changed on purpose, all as values updates to the new starting widths: PanelWidthLimitTests.test_stored_widths_are_read_through_clamps and WholeWorkspaceFitTests.test_three_columns_fit_a_1440_window_at_their_widest in tests/test_v11_layout.py, WindowDerivedVideoMaxTests.test_no_fixed_small_clamp_on_the_video_column and FitInvariantTests.test_columns_sum_to_no_more_than_the_window (plus the column_widths helper's default) in tests/test_v111_resize.py, and V2FrameTests.test_no_page_scrollbar_at_laptop_sizes in tests/test_v09_layout.py.
- **Fixed: picking cut words after writing a comment never brought up the bubble.** Once a comment had been pinned, the typing box kept the keyboard focus, and the bubble quietly refused to appear for any new pick made while that focus lasted, which in practice meant the picks tested right after a comment: cut words and mixed picks, so Restore and Keep the cut could not be reached. Kept-word picks tested before a comment worked, which made it look as if cut words themselves were the problem; they were not, and the words the pick covers are now what decides. A pick made in the transcript brings the bubble up even while the comment box still holds the focus, while a pick remembered from before still never pops the bubble up while you are typing, and picks inside a typing box still show nothing. Costs: local work only, 0 cents, no model calls. New tests run the page's own transcript builder and bubble code in a script over the built-in sample job's real cut words, in the stuck focus state the proof found, and fail on the old page exactly where the browser did (tests/test_v12_bubble.py, BubbleBehaviorNodeTests). Two older tests changed on purpose, both because they pinned the one line that caused this: CommentActionTests.test_no_live_selection_means_the_shortcut_does_nothing and DismissalTests.test_no_bubble_for_selections_inside_fields_or_popups, and their superseding checks are the gated lines pinned in those same tests plus BubbleBehaviorNodeTests.

## 0.11.1 - 2026-10-28

The polish round from Justin's evening test of 0.11.0: the top of the review screen, the window sizes, the comment box, the timeline, and stricter retakes.

- **The three bars at the top of the review are gone.** The test-video switch, the "Job loaded" bar, and the "Teaching is tucked away" bar no longer sit between the header and the workspace. Bringing in another job is now a plus button in the header row, named "Bring in another job." The test-video switch lives in Settings, with the same words and the same behavior. Teach the app is a button in the header row, opening the same teaching choices. The workspace starts right under the header and gets the height back.
- **Windows resize in both directions, and the video can get big.** Dragging works sideways and up and down: a handle under the workspace makes the row taller or shorter, and the video column's largest width now comes from the window itself, so on a full screen the video can grow big enough to see detail. The transcript and comments shrink to their smallest sizes but stay in view, and the fit rules from 0.11.0 still hold at every size. Widths and the new height are remembered across sessions, and Reset layout puts them all back.
- **The comment box reads like a chat input.** The big blue "Comment at current time" and "Save comment" pills are gone. The words go in one clean box, with a microphone icon button beside it for dictating and a round blue send button with an up arrow to save, the way chat inputs work. "Comment at current time" is now a small quiet button with a clock mark. Every icon carries its plain name on hover or focus. Pinning, saving, dictation, the seconds entry, the format picker, and the keep-playing switch all work exactly as before.
- **The timeline is now a full-width band across the bottom, laid out like Descript's.** The transport buttons and the clock moved down with it. Inside the band: a time ruler with second marks, the edit's segments as blocks with your comment dots, a captions row when captions are on, and the sound wave under it all, so quiet stretches and pauses are visible by eye. The band's height drags by its top edge and is remembered. Playing moves the band's playhead and the transcript's current word together; clicking the band, the ruler, or the wave jumps the video, the same as clicking a word.
- **Retakes are strict by default.** On the day 42 video the edit left most of the repeated tries in, because the unsure ones waited for a second look that kept them. Now a suspected retake comes out by default, at the first edit and in Remove retakes alike, keeping the best or last take. The cuts the pass was not sure about are flagged in the review in orange, and the word popup and the recap say so in plain words: "I was not sure about this one. I took it out so the last take stays. Double check here if you want it back." One tap brings a flagged cut back, the same as any cut. Sure cuts are not flagged. Your own explicit choice still wins over the default: direction that says to only cut the sure ones, or a saved choice, is followed instead. One cost note: the unsure pairs used to wait for one model judgment call per video; under the strict default that call no longer happens, which saves a few cents a video. Under an explicit loose choice it runs exactly as before.

## 0.11.0 - 2026-10-28

The fix round from Justin's screen recording of 0.10.1 the same day: the review screen fit problems, plus saved transcripts.

- **The Teach the app card gets out of the review's way.** In 0.10.1 the full card sat at the top of the review screen for the whole review, pushing the video and transcript below the fold. Once a job is loaded it now collapses to a slim bar with one button (the same treatment the drop card already had), and the choices are one click away. With no job loaded, the front door keeps the full card.
- **The workspace always fits the window.** The arrangeable panels had no limits, so the columns could be stretched past the window and the comments column could fall off the right edge entirely, while the video sat small inside a wide black panel. Panel widths now have minimums and maximums that dragging and saved layouts both respect, the three columns always fit a normal window at 100% zoom with no sideways page scroll, hiding a panel gives its space to the others inside the same width, and the video keeps its own shape in its panel.
- **Transcript lines no longer collide around cut words.** The line spacing was smaller than the marker boxes that sit in a line, and struck words painted outside their line, so dense take stretches rendered lines on top of each other. Line boxes now reserve room for everything a line can hold, at both text sizes, so no two lines ever overlap.
- **A transcribed video never pays for transcription twice.** The app now saves each transcript (words with timings, and which engine made it) on the Mac, matched by the video itself (file size plus a hash), not its name. Bringing the same video back in any later session reuses the saved transcript before any call to ElevenLabs, and the recap says so in plain words. A different video sharing the name does not match. Settings has one line to clear the saved transcripts.
- **Everything fits in the main view, at any window size.** The "Bring in a job" card was never closed in the page, so the whole review (video, transcript, comments) sat inside that card instead of in the page column the fitted layout was written for: the review grew to about three times the window height while the page was locked against scrolling and the panels, given no bounded height, did not scroll either. Everything below the window was simply unreachable, which is the "I can't move up and down" Justin reported. The card is closed now, the fitted frame holds from full screen down to a half-size window (panels resize, the video shrinks keeping its shape, the transcript flexes, comments move to a band), and on a very small window the page scrolls normally as a last resort. The pop up scroll lock now also syncs when the page loads and releases on every close path.
- **Voice commands.** A Talk button on the review screen: hold it (or tap to start and stop), say a command, and it happens. The words are matched on this computer against written lists of the ways people say each command: undo, redo, remove retakes, keep the retakes, trim audio, play, pause, next change, make the edit, and moving around (go back five seconds, go to one minute and thirty seconds, jump to the beginning). A matched command runs the same action as its button, instantly, at no cost, with no model call. Words that match nothing go to the direction box's machinery when a key is set and the words read as a direction; otherwise they are saved as a note and the app says so. A near match between two commands asks instead of guessing. "Center me in the frame" is recognized and answered plainly: the app cannot do that one yet. The recap and history name what was heard and what was done.
- **The transcript reads like Descript.** Words now read as flowing paragraphs of prose. Dead air shows as dots right inside the paragraph where the silence is: a short pause is a dot or two, a longer silence is a longer run of dots (one dot for each started third of a second, up to a cap). Cut words stay in place, struck through and dimmed, so a removed take reads in context. Clicking a word still opens its reason, takes still work as takes, the current word still follows the video, and comments still pin to words.

## 0.10.1 - 2026-10-28

The fix round from your first hands-on test of 0.10.0 the same day, where the first edit stopped at writing the words down and nothing could say why.

- **When Scribe refuses, the page says the real reason.** In 0.10.0 the answer ElevenLabs sent back could not be read by the app at all: its error shape (a "detail" object) was one the app did not know, so every refusal, whatever it was, came out as the same doubled sentence, and nothing anywhere recorded the status or the provider's own words. Now the page names the plain reason (the key was not accepted; ElevenLabs refused that recording, with its own words attached; ElevenLabs is at its limit; the app could not reach it), and the full detail goes into the session log. The Scribe request itself did not change between 0.9.0 and 0.10.0; what changed is that a refusal can now be seen.
- **A saved key and a used key can no longer be two different things.** Settings reads and writes the key file on this Mac; a run used to read only the session and the environment the launcher set at start. Now a run falls back to the saved key file itself, so what Settings says is saved is what the app can use. The same alignment covers the Baseten and Fireworks keys.
- **Check the ElevenLabs key before a run.** "Check the sign-ins" in Settings now checks the ElevenLabs key too (a harmless account read) and answers in plain words, so a wrong key is caught in Settings instead of mid-edit.
- **The problem report now catches front-door failures.** A first edit writes its steps into the front door's session log as it goes, failure included, and the report zip carries that log whether or not a job is open. In 0.10.0 a front-door failure produced a report with no log in it.
- **The problem report's name is clickable (your ask).** After a report is made, its name on the page is a button: click it and Finder opens the folder with the file picked out, ready to drag into the chat.
- **Settings scrolls cleanly.** The pop up's background is fully opaque (the page's words used to show faintly through and double up with its own), reaching its end no longer hands the scroll to the page, and while a pop up is open the page behind does not move. Opening Settings at the money card scrolls the pop up itself, not the page.
- **The money total says what it covers.** The running total now reads as what it is: every run saved on this computer, including runs from earlier copies of the app (the 0.10.0 card showed about $1.07, most of it one wrong entry from the 0.9.0 bug, as if it were today's). The reset already cleared it fully; the words now match.

## 0.10.0 - 2026-10-28

The learning version, plus the fixes from your hands-on test of 0.9.0 the same morning. The app now starts from shared starter know-how, learns your own rules at two speeds, can be taught on purpose, and the player finally shows the edit you made.

- **The player shows the edited video (FIX A).** In 0.9.0 your words showed the cuts while the player went on playing the original at full length: the edited video lived in the session and the open page never switched to it. Now, after any edit run, the page swaps to the new edited video on its own (no reload), says in plain words which one you are watching ("You are watching: the edited video" / "the original video"), shows "Building your edited video…" while it is being made, and never shows the original as if it were the edit. If an edit's video cannot be built, the page says so. The recap also says the length change out loud: "Your video was 10:16. The edit is 3:07."
- **Problem report (FIX B).** Settings now has a Problem report button. One click saves one zip in the job's folder with the app version, a step-by-step session log (every stage, with times), the playback log, the run records, a costs-and-rounds summary, and the current cut counts. Anything that looks like a key is stripped out before the zip is made. Every session now writes that step-by-step log as it goes, so a report always exists to send.
- **Starter styles.** The know-how the editor reads from is now saved as files you can see, in two groups that stay separate: starter styles that ship with the app, and your own learned material. At the start of a job you can pick a style: "Justin's way" (the default, unchanged from 0.9.0), "Keep it simple", or "Hands off". If you pick nothing, the app uses Justin's way and says so. Your own saved rules win over the style when they disagree, and the run record and recap name the style and pack versions used.
- **One short question, sometimes.** When you bring back a cut, or leave a held part in, the app may ask one short optional question about why, with plain tap answers and room for a short note you can dictate. One tap skips it, it never blocks the edit, and it never asks twice for the same action. Your answer is saved with that grade, so the app learns the reason, not just the click.
- **Two speeds for learned rules.** Small rules (filler words, pauses, breaths, single words) are learned the way they were, shown as learned, with an easy undo. Story rules (anything that can change what a video means) are shown one at a time, labeled plainly as a story rule, and need your clear yes before they ever apply. They never arrive bundled with small rules.
- **Test videos.** A job can be marked as a test video when you drop it or later, and the review screen says "Test video: the app will not learn from this one." Nothing from a test video is learned: no grades, no verdicts, no patterns.
- **Teach the app on purpose (optional).** You can drop a recording of yourself editing while talking through your choices; the app writes down the talking (about 4 cents per 10 minutes), reads it once, and drafts rules with the moment in the recording and your own words attached. You can also drop a before-and-after pair by itself (drafts say they come from your cuts, with no spoken reason attached), or a finished video you like the style of (drafts cover only what can honestly be seen, and say they come from watching the finished video only), or paste a link to a finished video; if the app cannot get the video from a link, it says so and asks for the file. Drafts marked not tested yet stay that way until they have been used on a real edit and kept, and nothing applies until you say yes.
- **Breaths choice removed (Justin's call on Oct 28).** There is no Breaths setting anywhere anymore. When quiet between words is cut, breath sounds inside that quiet are cut too; the one remaining setting is "Quiet between your words": "When you stop talking for a moment, the app cuts the quiet down and leaves only this much." A moment you have marked to keep is still kept whole, because it is marked. Direction that mentions breaths is taken as plain guidance words, never a setting, and never fails. Old saved settings that carry a breaths choice load fine and are ignored.
- **The review screen, rebuilt to the new design.** Everything fits on one screen at laptop sizes with no page scrolling: each panel scrolls inside itself. The video, the player controls (standard pictures with their word printed under each), one timeline of the edited video (pieces, your comment dots, the playhead; a blue dot means a spot is still waiting on your call, and the dot goes away once you have decided), and Make the edit sit in one column; the transcript reads as flowing paragraphs with one time per paragraph, on the same clock as the video; comments live in their own panel that can close for room. You can arrange the screen: swap the video and transcript sides, turn a panel off and back on, or make one panel take the full width, and the app remembers your arrangement. Settings and "How this works" open as pop ups over the page; the page behind does not move. Money details live behind one line on the Make the edit card now, inside Settings. A "Bigger text" choice lives in Settings too.
- **When a video cannot be made watchable, the page names the file to send back.** Every such message now says the details were saved in a file called playback.log, in the playback folder inside the job's tye-session folder, so the real cause can be found instead of guessed. No error codes on the page.

## 0.9.0 - 2026-10-28

The feedback version. Every item in your 15-minute walkthrough video of 0.8.0, thirty notes plus four flags, fixed and then checked by an independent pass that worked from your list alone before this shipped.

- **Your video becomes watchable on every screen, with a real percentage.** The 45-minute wait had three stacked causes: a copy whose build had never started rested in an "unstarted" state that neither screen's polling ever kicked off; the plain page a browser lands on for a not-ready video still carried the old wording ("A long video can take a few minutes") and never showed the converter's percent; and no log of any build existed anywhere. Now both screens share one playback state, an unstarted build is kicked off by the poll itself, the pending page shows the real percent, and every build writes a log in the job (`tye-session/playback/playback.log`: building, progress, ready), so a stall is never silent again. The old wording appears nowhere in the app.
- **One player message at a time, everywhere.** One status control owns the player's message on every surface, front door included. An error that arrives while a copy is being made no longer paints over the preparing message.
- **The first screen leads with your edit.** The direction box is now the hero at the top ("Describe your edit"), with Make the first edit right there. Under it, a short panel shows the stages in order, including a Transcribe button that writes the words down on their own and opens them on the review screen before any cuts. The Remove choices sit below as one-line summaries with (recommended) markers; the detail opens only when you ask for it.
- **Edit settings live in exactly one place.** On the review screen the Remove choices, keeper and breaths choices, formats, and direction moved into one "Edit settings" panel, so a choice can never be set in two places that disagree. The one-pass-at-a-time buttons are there too. Tabs that were named by their machinery ("Advanced") are renamed or gone.
- **The panes resize.** A divider between the video and the transcript drags (or moves with arrow keys) to give either side more room, and the choice is remembered on this Mac.
- **A note lands on the edit it was written on.** Pinning a note now anchors to the words and time showing when you pressed Pin, and several notes pinned in a row each keep their own target. A wrong-target bug in how notes were saved is fixed at the root.
- **Popovers stay until you say so.** The note and comment popovers no longer close when you click elsewhere or scroll; they close on Save or Close. They open clear of the video, keep their text at any window size, and dictation works inside them. Gold spot cards show their full text, and leaving a note on a spot no longer approves the cut.
- **Cmd+Z undoes.** Command+Z (and Control+Z) now undo, with Shift for redo and Control+Y on Windows, from anywhere except while you are typing in a field. The History buttons still work too.
- **Held spots are light red now.** The spots the editor held for your call changed from gold to light red and read "Recommended to remove", the words behind your own reasoning for the change. (Your call, reversing the earlier gold.) How they are decided and graded did not change at all; only the color and the words did. The reason written on each spot is now one plain sentence (the garbled "carries a knot" wording is gone), and a held spot over a long passage can be answered in parts instead of all or nothing.
- **Phrase editing, both ways.** A highlight still grows only as you extend it, one word at a time. Restoring works on the phrase you select: click struck words and only those words come back (Shift+click or Restore phrase brings back the connected run). Selecting words next to a cut no longer treats the cut words as selected.
- **Follow is honest.** Scrolling the transcript turns Follow off and it stays off until you press its button; pressing play no longer switches it back on silently, and making a cut never scrolls the transcript on its own. When a cut lands while Follow is off, the rebuilt transcript restores your reading place from a scroll anchor on the first visible word, so you never lose where you were.
- **The retake check is priced right, and the estimate explains itself.** The app's own money card charged the retake check about 97 cents against the published "about 2 cents". The root cause: the retake judge's escalations ran Muse Wonder calls, and the cost accounting folded those Wonder dollars into the Retake check line. Fixed at the root: the judge now makes one Spark call for the whole video, and any Wonder usage prices onto the Second review line instead, so a feature's line only ever carries the model its price describes. A normal run prices at about 2 cents, and the second review shows as its own line at about 59 cents when a run needs it. Under each estimate there is now a plain "Why this number" line naming what drives it, including what the same edit costs with no second review.
- **First-edit progress shows its work.** The five steps now report elapsed time, percent, and time left where the measurement exists, on the card and in the job summary. A line left from an older version that promised a long wait on the fast transcription path is corrected.
- **Costs.** Everything else in this version is local compute or presentation and costs 0 cents: playback, layout, popovers, keys, undo. The one repriced line is the retake check (about 2 cents typical, about 5 cents on a long video), now matching the published table. The hosted Spark routes from 0.8.1 behave exactly as before.
- **The Day 30 test: still not re-run.** Same standing block as every version since 0.7.1: the eval harness takes its provider keys from the shell it runs in, and the build shells do not carry them. The Cleanest cut re-run (attempt 4) remains blocked the same way, no calls were made for this version, and the attempt 3 counts stand (76 leftovers of 144 cut words; wrongly cut 2 of 681 kept words; the protected spans clean).
- New tests cover the single playback path and its log (front door and job surfaces, the synthetic 10-minute HEVC watchability run), the one-message rule on every surface, the hero and stages layout, Transcribe on its own, the single Edit settings panel, the resizable panes, note anchoring in both pin orders and across directions, popover persistence and placement, Cmd/Ctrl+Z, the red held spots and their copy, phrase grow and phrase restore, follow honesty including scroll-place preservation across a re-render, the repriced retake line against invoice-shaped numbers, the self-explaining estimate, and step progress reporting (519 in all, up from 400).

## 0.8.1 - 2026-10-27

The credit pools version. The hackathon partners gave you two $100 credit pools that serve the same Spark model the editor reads with: Baseten and Fireworks. Until now only Donna's own tooling could spend them. Now the app can: Spark-tier work, the retake check and the direction read, can ride either pool instead of your Meta balance, and the money view keeps each pool separate so you can watch each one drain on its own.

- **Two new keys, same card, same discipline.** The sign-in card gains a Baseten key row and a Fireworks key row under the ElevenLabs rows. Each works exactly like the keys you already know: paste it for this session, or Save on this Mac (a private file beside the others, folder 700, file 600) and the launcher picks it up at every start. Keys are never echoed back by the app.
- **One choice: Spark work runs on.** Above the new rows, a single choice names the route for Spark-tier work: Meta (the default, what the app has always used), Baseten credits, or Fireworks credits. The choice is remembered in your usual, like the Remove choices, and takes effect on the next run. Muse Wonder, the second review, exists only on Meta, so Wonder work stays on Meta no matter which route is chosen.
- **Only the wire changes.** On a hosted route, a Spark call goes to that provider's endpoint with that provider's key and its Spark Open model name. Everything else is untouched: the same prompts and few-shot calibration, the same typed operations (every operation still records Muse Spark as its source), the same verifier and safety path, and the same escalation to Wonder. The retake judge and the direction read are the two passengers; nothing else moves.
- **A route that cannot be reached falls back once, and says so.** If a hosted Spark call errors or times out, the run retries that call once on Meta (when a Meta key exists) and finishes there. The recap names what actually ran: "Fireworks could not be reached, so this read ran on Meta." A chosen route with no key saved yet behaves as Meta from the start, and the line beside the route choice says so before you run anything.
- **The sign-in check covers the new keys.** Check the sign-ins now also checks each hosted key you have saved, with its own plain answer: accepted, or not accepted, with the note that Spark work runs on Meta until it works. A refused hosted key never blocks a run that Meta can carry.
- **Costs follow the pool.** Every cost entry now records the pool it drew on. Recap lines name it: "Reading your direction (Fireworks credits): about 5 cents." and "Retake check (Baseten credits): about 2 cents." Meta lines keep their plain wording, exactly as before. The money card gains a **By pool** list under this Mac's totals (the Meta estimate, Baseten credits, Fireworks credits, and the rest), shown whenever more than one pool has been used, and the before-it-runs estimate under the edit buttons prices the Spark work against the chosen pool while the Wonder review stays priced on Meta. Cost records written by older versions get their pool derived the same way the app always knew it (a paid transcription was ElevenLabs, a direction read was Meta), so the running total carries forward whole.
- **The hosted rates are estimates, labeled as such.** Neither Baseten nor Fireworks publishes per-token prices in our records, so hosted Spark work prices at the same derived Spark rates as the Meta route (about $0.35 per million prompt tokens, $3.65 per million replies), drawn against the pool's $100 instead of the Meta balance. Of a worst-case first edit at about 71 cents, about 12 cents (the retake check and the direction read) can ride a hosted pool; the Wonder review, about 59 cents, is always Meta's.
- **Nothing changes with no hosted key.** No Baseten or Fireworks key saved means the app behaves exactly as 0.8.0: the route sits on Meta, the recap lines read as they always have, and the money card shows no pool list until a second pool exists.
- **The Day 30 test: still not re-run.** Same standing block as every version since 0.7.1: the eval harness takes its provider keys from the shell it runs in, and the build shells do not carry them. The Cleanest cut re-run (attempt 4) remains blocked the same way, no calls were made for this version, and the attempt 3 counts stand (76 leftovers of 144 cut words; wrongly cut 2 of 681 kept words; the protected spans clean).
- New tests cover the route choice and its persistence in the usual, the transport swap against mocked hosted endpoints (URL, wire model, bearer key, the usage record's route), Wonder pinned to Meta under a hosted route, the one-time fallback to Meta on a hosted error and on a timeout with the recap naming the route that ran, a chosen route with no key resolving to Meta, the direction pass and the retake judge riding a hosted route with their parsing unchanged, cost entries and recap lines per pool, the money view's pools kept separate, records written before pools deriving theirs, the estimate under a chosen route, the hosted sign-in check's plain words, the key endpoints (paste, save on this Mac, remove, unknown kinds refused), and the no-key state matching 0.8.0 (400 in all). Six older tests changed on purpose, all for the same reason (the contracts grew by the route): the three sign-in state assertions in the web app tests now expect the hosted fields at their defaults, the front door key set and defaults tests expect the route among the choices, the usual round trip writes eight keys instead of seven, and the direction-run test's stand-in provider accepts the constructor's new route options by name while still pinning that the pasted key is what reaches it.

## 0.8.0 - 2026-10-27

The watch-and-edit version. Your first real test found the three things this version fixes: a 13-minute video sat on "Getting your video ready to watch" for 45 minutes, an old "could not play" message showed through behind it, and the page made you scroll between the video and the words. Now the picture arrives in minutes instead of 45 with a percentage you can watch climb, only one player message ever shows, and on your Mac the video and the transcript sit side by side, with editing that works the way Descript's does: highlight words, press Backspace, they are cut; click the struck words, they come back.

- **The watchable copy, rebuilt around a hard bound.** Before, one conversion attempt could run for 30 minutes with the page showing nothing, a second attempt could add 30 more, and every video in the job converted at the same time, fighting for the same machine. Now the converter's own progress drives everything: the page shows its real percentage while it works ("Getting your video ready to watch... 42%. You can keep editing; the picture will catch up."), an attempt that stops advancing for 30 seconds is stopped and the next encoding way takes over, and each attempt is capped by the video's length (about three quarters of it), with the whole conversion capped as well (about one and a quarter times the length). Conversions also run one at a time now. Measured on the build machine (a small shared server, far weaker than your MacBook Pro), from logs read back after the runs: the shipped kit's rehearsal took a 13-minute HEVC video to a ready copy in 339 seconds end to end, and a stress fixture converted in 416 seconds, the percentage climbing the whole way. On Apple silicon, with the hardware encoder first in line, expect it faster; that is an expectation, not a measurement. A video that is already browser-playable still gets no conversion at all.
- **One build per video, ever.** The copy is made at most once per job and source: reloading the page mid-build starts nothing new, and a finished copy is reused on every later visit and after restarts. A conversion that truly fails says so in plain words ("The watchable copy could not be made..."), once, and is not retried on every poll; the next app start tries again.
- **One player message at a time.** The player now runs through a single status control: preparing, could-not-play, or ready, never two showing together. A playback error that arrives while a copy is still being made no longer paints the failure message over the preparing one.
- **Side by side on the Mac.** At desktop widths the review screen splits in half: video on the left, staying put while you scroll, with the timeline bar and the edit cards under it; the transcript on the right in its own scroll, following the video as it plays, exactly as before. On a phone or a narrow window the page stacks in the same order it always has.
- **Highlight editing in the transcript.** Drag across words (or shift-click, or drag on touch) and the run lights up as one phrase. Press Backspace or Delete, or the Cut button in the phrase's strip, and the whole phrase is cut at once: struck through, dimmed, skipped by the preview in real time, one checkpoint in History, so one Undo brings it all back. Clicking struck words, or selecting them and pressing Restore, brings the whole phrase back at once. Typing in the transcript still does not exist; Fix words remains the way to correct a word.
- **The safety rules did not move.** A phrase cut goes through the same validator and verifier as a word click, per unbroken run of words. Gold spots are never cut by a highlight: selecting one opens its gold card for your call, and the words around it still move. Protected words and special inserts are left standing, and the answer names what stayed and why, in plain words ("Cut 12 words. 3 words stayed: That part is protected (closing call to action), so it cannot be changed."). A single change that would remove over a quarter of a video's words still waits for a second review, exactly as before.
- **Costs.** Everything new in this version runs on your Mac and costs 0 cents: the conversion is local compute, the split layout is presentation, and phrase cuts ride the editing passes already on the books. The per-feature table from 0.7.2 stands unchanged.
- **The Day 30 test: still not re-run.** Same standing block as 0.7.1 and 0.7.2: the eval harness takes its provider keys from the shell it runs in, and the build shells do not carry them. No calls were made for this version either; the attempt 3 counts stand (76 leftovers of 144 cut words; wrongly cut 2 of 681 kept words; the protected spans clean).
- New tests cover the conversion bounds, a stalled encoder being stopped, a never-finishing encoder hitting its cap, failover between encoding ways, one-build-per-video across reloads and restarts, the progress in the job summary, a measured 13-minute HEVC conversion with climbing progress, phrase cuts and restores as single checkpoints, gold and protected and special words partitioning out of a highlight with the note, the one-status player rule, and the split layout at desktop width with the stacked order kept on narrow screens (372 in all). Three older page tests changed on purpose, all for the same reason (the player overlays now answer to one status control, and word clicks ride the phrase path): the could-not-play and loading tests now pin the controller's wiring, including the rule that an error during a build is ignored, and the transcript test pins the range endpoint.

## 0.7.2 - 2026-10-27

The money version. You asked to see what each feature costs, in the app, before and after it runs. Now every run is priced from what it actually used, and the figures are on the page: one line per feature in the recap, a total for the job, an estimate before you press the button, and a running total for this Mac.

- **One place for rates.** A new costs module holds every rate, every money formula, and every figure the app shows, with the derivation written down in it. Meta does not publish Muse Spark and Muse Wonder per-token prices in our records, so those two are derived estimates: the October 21 yardstick bill split $14.28 as Spark $2.81 and Wonder $11.47, with Wonder's replies the cost driver, and the app's captured token counts turn that split into per-token rates (Spark about $0.35 per million prompt tokens and $3.65 per million replies; Wonder about $138 and $239). ElevenLabs Scribe ($0.22 per audio hour) and Meta dictation ($3.00 per 1,000 minutes) are published rates. When a new real bill lands, the rates get corrected in that one module and nowhere else.
- **What a run cost, in the recap.** The first edit's recap now ends its pass lines with one line per feature that ran ("Retake check: about 2 cents."), closing with the run's total ("This edit cost about 71 cents in all." on a typical 10-minute video whose direction is read live). The **Make the edit** result lines carry the same lines. A feature that did not run does not appear. The numbers come from the run's own records: the token usage the app has captured since 0.7.0, the transcription route and the video's length, a dictation's seconds.
- **Before it runs.** Under **Make the first edit** and under **Make the edit**, one quiet line estimates the run ("This first edit usually costs about X."). It follows your Remove choices and whether direction will be read, and it learns: once this Mac has real runs on its books, the estimate prices from what those runs actually cost; before that, it prices from the video's length and the documented typical runs.
- **The money card.** A card titled **What the editing has cost** shows this job's lines and total, and below them the running total on this Mac broken down by feature, kept in a small private file (`~/.talk-to-your-editor/money.json`) that every run adds to. **Reset the running total** starts the Mac's total over; each job's own cost record stays in its folder. The **Finished edit** card carries the job's cost lines too. On the home page (no job loaded) the card is the running total alone.
- **The honest zeros.** Work that runs on this Mac costs nothing and says "0 cents": local transcription, the filler pass, the silence pass. The also-noticed flags read "0 cents" as well, with the note that they ride along with the direction read, because they do: no extra call is made for them. Dictated notes are on the books at a fraction of a cent each.
- **Estimates say "about."** Every priced figure in the app carries the word, because the model rates are derived, not published. A future credit system can sit on top of these same numbers later; this version is money, shown plainly.
- **The Day 30 test: the re-run is still owed, and nothing was spent.** Attempt 4 (Cleanest cut, the frozen configuration) remains blocked the same way it was for 0.7.1: the eval harness takes its provider keys from the shell it runs in, and the build shells do not carry them. It was not re-run for this version, no calls were made, and the attempt 3 counts stand as the latest result (76 leftovers of 144 cut words; wrongly cut 2 of 681 kept words; the protected spans clean).
- New tests cover the rates math against hand-computed fixtures, the recap cost lines from fixture records, the accounting seams, the before-it-runs estimate with and without history, the money store's totals, persistence, and reset, and the two new endpoints (347 in all). No older test changed: the money rides along as new fields and new lines, and every contract the older tests pin still holds as written.

## 0.7.1 - 2026-10-27

The involvement version. You now choose how involved to be, and the spots the editor is not sure about stop being a quiet set-aside: they wait in gold in the transcript, with the reason attached, for your call. Your verdicts, and the notes you attach to them, teach the app the same way your grades do.

- **How involved do you want to be?** One new choice in the Remove moment, at the drop and on the **Make the edit** card, remembered in your usual like the other choices: **Cleanest cut** (the editor finishes everything it is sure about; the spots it is unsure about wait in gold, for whenever you want them) or **Tailor it with me** (the editor brings every spot it is unsure about to you, in gold, before it counts). Every recap names the way of working the run used, and how many spots are waiting in gold.
- **Gold spots.** In the transcript, the words a pass suspected but did not settle now sit washed gold, with a small gold marker of their own (not the blue dot of a finished edit, not the amber flag of an also-noticed note). They come from three places: a direction the model proposed that the safety check held back, a retake the engine measured but could not cut on its own (a pair carrying a "not", a borderline pair no judgment settled, a cut the verifier held), and any other change the verifier held for a person's eyes. Rest on the marker (Mac) or tap it (phone) and the card says what the spot is and why the editor is unsure, in plain words, with the take that stayed shown when there is one.
- **Your call, with your context.** The gold card asks for one decision: **Cut it** or **Leave it**. A cut goes through the same validate, verify, and apply path as every other edit, right then; the holds that made the spot gold (a negative statement, a missing second review) are the decision you are making, so they do not hold it again, and every other safety rule holds exactly as written. Leaving a spot settles it: it is never offered again in this job, and for a retake it records the same keep-both decision a model judgment writes. Before you decide you can add a note, typed or dictated, and the note is saved with the verdict.
- **Verdicts teach, notes and all.** Each verdict is recorded as a grade in the same learning store the review page writes, so it counts toward rule suggestions under the same rule as any grade (three supporting verdicts, or one you mark as your style). When a pattern surfaces as a suggested rule, your note rides with it as the sample context. A note on a cut also lands on the edit the cut made, in the same place the transcript dots read their notes from.
- **The negative-statement rule, settled as product policy.** Until now, every cut whose words contain a negation waited for a person, always; that rule is why the Day 30 test's leftover count stood where it did. The mode now carries the policy. In **Cleanest cut**, a directed cut that Muse Wonder has reviewed goes through when the words it removes are an exact repeat of words that stay (a copy coming out cannot change what the kept copy says), or when your own direction named those words; the check is exact on purpose, and anything less still waits. In **Tailor it with me**, every one of those cuts waits in gold. The audit record marks each cut that settled this way, and says which of the two reasons settled it.
- **The Day 30 test: the re-run is still owed, and nothing was spent.** The frozen re-run (attempt 4, Cleanest cut, the same configuration as attempts 1 to 3) was launched once for this version. It stopped at the first stage, before any model call: the eval harness takes its provider keys from the shell it runs in, and the build shell for this version does not carry them, so the app honestly reported it had no way to write the words down. No calls were made, nothing was spent, and nothing was scored. The attempt 3 counts stand as the latest result (76 leftovers of 144 cut words; wrongly cut 2 of 681 kept words; the protected spans clean), and the re-run remains to be done from a shell that carries the keys.
- New tests cover the mode and its write-back, gold from each source, verdicts applying and settling, rules surfacing with the note as context, and the negation policy in both modes (316 in all). Three older tests changed on purpose, all for the same reason (the front door gained its seventh choice): the usual store's round trip now writes seven keys instead of six, the no-job summary test expects the mode among the front door keys and the new empty gold list beside the flags, and the front door defaults test gained the mode line in its running list of choices that ride along.

## 0.7.0 - 2026-10-27

The first-run version. The moment a video is dropped is now the whole setup: one block of choices under the word **Remove**, a usual the app remembers from video to video, the ums and uhs taken out of the sound itself, direction beyond the switches read by a model pass over the whole transcript, a short "also noticed" list that never cuts anything, and a transcript panel that follows the video while you read it.

- **The Remove moment.** Above the direction box at the drop, one block headed **Remove** carries the three removals: **Retakes** (with the keeper choice: the last take, the first clean take, or Best delivery (Beta)), **Silence** (the buffer, plus the breaths choice), and **Filler words**: "Take out the ums and uhs." Everything is set before the first edit is pressed, and it all arrives already set to your usual.
- **Your usual.** The six choices are now remembered at the level of you, not the video: a small private file in your home folder (`~/.talk-to-your-editor/usual.json`, readable only by your account) holds them, and both the drop block and the **Make the edit** card write back to it whenever you change a choice. A new video starts from the choices you actually use. Direction you speak or type for one video still wins for that video, as always.
- **Fillers come out of the sound.** The filler pass is a fixed, deterministic list (um, uh, er, ah, hmm, and their stretched spellings, the same conservative list the captions have always used), never a model's guess. It runs between retakes and silence in the first pass, and from the **Make the edit** card too. Protected moments are never touched, every removed filler lands as a transcript dot with its reason, and any of them can be brought back with one click.
- **The first pass runs the whole pipeline, live.** A dropped video's first edit now runs in the order the work actually flows: the words are written down, the silences measured, the first plan laid out, retakes checked (the clear ones by the measuring engine; the near ones judged live by the model when a Meta key is saved, instead of the offline stand-in the intake used before), fillers taken out, silences cut, your direction read, and the review video built.
- **Direction is read, not just mapped.** When your direction says more than the switches can hold ("keep it tight and keep the story moving"), one model pass now reads the whole transcript with that direction and proposes the edits through the same verifier and risk tiers as everything else: small certain changes apply, risky ones are reviewed by Muse Wonder in a single batched review, and anything protected, unclear, or only a story judgment becomes a flag for you instead of a cut. Nothing is ever cut on story grounds alone. The pass is taught by examples from Justin's own Day 30 edit, the same calibration the retake judge now carries: sentence assembly by cuts only, a tone cut taking its self-correction with it, a repeated point cut only when a fuller version survives later and the direction asks for tightness, values wording never smoothed, performed repetition never touched. Every change it makes lands as a dot with its reason. With no key saved, the recap says in one sentence that your direction will be read when a key is saved, and it waits as overall guidance, as before.
- **Also noticed.** The same read can raise short flags, on their own lines in the recap and as amber markers in the transcript: a point made again with nothing new, a thread opened and never returned to, a strong line buried in flatter material, two lines that say almost the same thing (retake, or said twice on purpose?), one word fighting its sentence, a thought that stops halfway. Flags never cut anything, ever. The opening hook, the temperature line and the breath before it, and the closing prayer, declaration, and sign-off are never flagged, and neither is anything your direction or a saved rule already settled.
- **The transcript is a panel now.** It scrolls in its own bounded panel, grouped into lines with a time chip on each line (tap the chip to jump there). While the video plays, the current line and word light up, and **Follow the video** (on unless you turn it off) keeps the reading place in view; scrolling the panel yourself hands control back to you until you press play again. Clicking a word jumps the video to it and picks it; a strip pinned at the panel's foot offers **Cut these words**, **Bring these words back**, and **Fix the words** for the pick. The dots after removed words now open a small floating card beside the dot (hover on a Mac, tap on a phone, Esc closes it) carrying the removed words, the reason, and the note box; the old panel at the foot of the transcript is gone. The pause rule is also stated the way it actually works, on both cards: pauses over the buffer are shortened to the buffer, and keeping the natural breath is named as the one exception protected on purpose.
- **The Day 30 test, run three times.** This version was graded against Justin's own finished Day 30 edit, mechanically, the way the calibration specifies: the raw video dropped as a first edit under his standing configuration, scored on exactly two mistake kinds. All three runs passed the safety side outright: wrongly cut words were 3, 2, and 2 of 681 kept words (the bar is 1 percent, about 7), with zero wrongly cut words inside the protected spans on the second and third runs, and the drifted near-duplicate the old tool missed (row 7) resolved on runs two and three. All three runs failed the leftover side: 100, 85, and 76 of about 149 cut words were left in (the bar is 10 percent). The eval names the cause precisely, and it is a policy, not a miss: about 58 of the leftover words are the repeated disclaimer variants and the "severe, not bad, but" tone cut, spans that contain a negation. The verifier's standing safety rule holds every negation-bearing span for the creator's review and never applies it unattended, so the direction pass cannot apply those cuts on a first pass no matter how certain it is; what it does instead is hand the creator the exact span with its reason, one click from applying. Changing that rule is a product decision, not a calibration fix, so it was not changed to pass the test. The counts stand as the honest result: the pass is safe to leave unattended, and on this video it leaves more behind than the bar allows.
- New tests cover the usual store, the filler pass, the direction pass and its flags, and the contracts where the pieces meet (297 in all). Older tests changed on purpose: the front-door defaults gained the filler choice; the first-edit progress test now counts eight stages instead of seven, because the filler pass is a stage of its own; and several test classes now isolate the home folder, because a front-door save writing the creator's usual otherwise leaks one test's choices into the next test's fresh job. That is a test harness fix; the behavior it protects is the new one working as designed.

## 0.6.1 - 2026-10-27

The finishing version: when a review is done, one card hands the work back; breaths become a choice instead of a side effect; everything the app is doing is visible while it does it; keys can be saved on this Mac so restarts stop asking; and a video this browser cannot play gets a watchable copy made in the background, at a size that finishes, instead of a conversion started inside the play button.

- **The finish card.** When you have a revised edit built and you have visited its review page, a **Finished edit** card appears with the finished work together: the video (watch it, or download it as finished-edit.mp4) and, for a job the app took in itself, its caption file. It says plainly that this is the latest video you built from this edit. The card never appears before both halves are true, and it never claims more than the app can see.
- **Breaths are a choice.** Under **More choices**, beside the silence buffer: **Breaths: Keep the natural breath / Trim breaths with the pause.** Keeping is the default: the silence pass never trims a join below a natural sentence-start breath. That floor is 0.3 seconds, and it is not a guess: the app's own sound measurement treats quiet stretches of 0.3 seconds or less as part of one unbroken stretch of speech, so a breath at a sentence start is never trimmed below the shortest quiet the app itself still hears as speech continuing. Trimming hands the whole pause to the buffer instead, breath and all, for the places where the next thought should land right away. Intention is hard to judge, so the protection carries it: a join beside a protected moment, or one a pinned comment points at, is never trimmed under either setting; every trimmed join still lands as a transcript dot with its reason and can be brought back; the choice is saved with the front-door settings; and the recap names the state in plain words ("Breaths are being kept natural." / "Breaths are trimmed with the pauses."). Spoken or typed direction can set it too ("keep the breaths natural" / "cut the breaths too"), at the drop and in the direction box, and the recap names it when it does.
- **A missing file is a sentence, not code.** If a job folder is missing a file it needs, the page now says one plain sentence ("That job is missing a file it needs. Drop the folder again, or try the built-in sample."), shows a banner saying the job is not whole yet, and keeps every action that needs those files out of sight until the job is whole. No screen in the app can land on raw JSON anymore; the API still answers JSON to programs, as before.
- **Progress you can see.** Pressing **Make the first edit** now fills the card with the current stage in plain words (Listening to your video / Writing the words down / Measuring the silences / Laying out the first plan / Checking for retakes / Cutting the silence / Building your review video), which step of how many, and a moving bar. Dictation shows itself too: while you speak, the button is a red recording indicator counting the seconds; when you stop, the button and its box show a spinner and "Writing your words…" until the words land. No control is ever just gray and silent while it works.
- **Keys saved on this Mac.** The sign-in card gains **Save on this Mac** for both the Meta key and an ElevenLabs key (the ElevenLabs key can now be pasted on the page too, for the session or saved). Saving writes the same private files the launcher has always read (in your home folder, readable only by your account), so a saved key is simply there after a restart and after a new kit download. The card shows "Key saved on this Mac" with a Remove option; the key itself is never displayed.
- **Captions, renamed and moved home.** The switch is now **Hide ums and uhs in captions**, and it lives where the captions are handed back: beside the caption download on the Revised edit card, and on the finish card. Same default (on), same behavior; flipping it rebuilds the caption file, or removes it, right away. The intake card no longer carries it.
- **Watchable copies, rebuilt the way the research specified.** A video the browser cannot play (an iPhone's HEVC is the usual case) gets an H.264 copy, but the copy is now made the moment the drop finishes (or the job loads), in the background, capped at 720p with a 5 Mbps ceiling, so a 13-minute 4K original takes a few minutes instead of most of an hour and hundreds of megabytes instead of gigabytes. On a Mac the hardware encoder is used when it exists, with the software encoder as fallback; a machine with neither gets a plain sentence, never the raw file. While the copy is being made, the player says "Getting your video ready to watch…" and swaps the copy in when it lands; the original is never served in its place, and a failed conversion is never remembered as "serve the original." (Measured after the fix, on a generated 65-second 1080p HEVC clip: the copy was built by the drop alone, before any play request, at 720p, and it is the copy the play route serves.)
- Five older tests changed on purpose: the front-door defaults now include the breaths choice; the no-job video route is now a plain page like the rest; the HEVC playback test now polls for the background copy the way the page does, instead of expecting a conversion inside the play request; the sign-in state assertions grew the ElevenLabs and saved-on-this-Mac fields the state now reports; and the two front-door silence assertions now record that the pass trims nothing on the sample fixture, because every one of its joins borders a protected moment or the signature insert, and a protected join is never trimmed. The trimming mechanics are covered on a purpose-built fixture in the new tests.

## 0.6.0 - 2026-10-27

A creator can now drop a single raw video and get a first edit, with no runner involved. This version also gives the feedback boxes a Meta-powered Dictate button, lets direction be given at the moment of the drop, and adds clean captions that leave the fillers out.

- **Make the first edit.** Drop one video file and the card offers **Make the first edit**. The app writes down the words with their timings, measures the speech and the silences from the sound itself, lays out a first plan with every word kept, then runs the same front door a runner job gets: Cut silence at the saved buffer and Remove retakes under the saved keeper rule. What you review is an actual first edit, and every change lands as a dot with its reason, like any other run. The review video is made by the app itself, by splicing the kept pieces of your video together; your original file is never changed. A video with no speech in it gets a plain "no words were found" answer, not an error, and a long video says up front that writing down its words will take a while.
- **Two ways to write down the words, picked on evidence.** With an ElevenLabs key saved, the app sends the sound to ElevenLabs Scribe, which keeps fillers and restarts as words (edit decisions need them; anything cleaned away could never be cut) and times every word. Measured cost: about a cent for a 3-minute video. With no key, the app writes the words down on this Mac itself with an open-source Whisper model, free, after a one-time download of the model (about 150 MB) that the progress line names honestly. Meta's speech-to-text was measured too: it is fast and cheap, but it returns no word timings at all, so it cannot drive an edit; it powers dictation instead (below).
- **Direction at the drop.** The first-edit card takes direction up front, typed or dictated: "remove retakes, last take is the best take" sets the switches for that run, and the recap names what your words set, in plain words. Controls the app has (retakes on or off, which take stays, silence on or off and its buffer when you name a number) are set by what you said, and what you said wins over the saved settings for that video. Anything that does not map to a control rides into the run as overall guidance, the same channel overall feedback already uses.
- **Dictate, through Meta.** Every feedback box (the front-door direction box, a comment, the overall feedback box, the note on an edit, and the direction box at the drop) now has a **Dictate** button: speak, and Meta's voice transcription writes your words into the box for you to read and fix before anything counts. Typing always remains. With no Meta key saved, the button says so in one plain sentence and typing carries on; the browser's own voice typing stays available in the edit-note popup as a clearly marked second choice.
- **Clean captions.** For a job the app took in itself, a **Clean captions** switch (on unless you turn it off) builds a caption file from the words that stayed in the edit, with the fillers (um, uh, er, ah, hmm, and their stretched spellings) left out of the caption text only. The sound and the written-down words are untouched, and meaning-bearing words are never on the list. The file downloads from the Revised edit card. Proven on a speech clip built to contain them: the transcript kept four fillers and the caption file held none.
- Two sentences from earlier versions changed on purpose: a dropped lone video no longer says a full edit needs the runner, because the app can make that first edit itself now; two tests were updated to match.

## 0.5.2 - 2026-10-27

The app opens. That is the whole change, and it was the missing one: until now the launcher stopped in Terminal to ask for a job folder path before the page existed, so the drop zone built in 0.4.1 sat behind the very door it was meant to remove.

- **No job required to open.** Double-click the launcher and the app comes up with no job loaded. The home page is the front door: the **Bring in a job** drop zone front and center, the folder and file picker buttons, one quiet line offering the **built-in sample** as a single click, and the Meta sign-in check. Nothing asks for a path, and nothing waits in Terminal. The Terminal window only needs to stay open; it is never spoken to.
- **The key prompt is gone too.** A Meta key saved on the Mac is still used without asking, exactly as before. With no saved key the app runs the offline mock and says so; paste a key in the sign-in box on the page whenever you want live Muse direction, kept for that session only.
- **Drops land the same as ever.** Drop a runner job folder and the app takes it in and runs on the staged copy; drop a lone video and it plays in the card with the same plain sentence about what a full edit still needs. A no-job launch keeps its session, its staged drops, and the staged sample in one neutral place: a `.launch-home` folder beside the app, so nothing is scattered and deleting the kit folder takes it all with it.
- **Starting on a job still works.** Pass a job folder path as the launcher's argument, or run `tye serve --job-dir`, and the app starts on that job exactly as before. Every route, session rule, and safety check is unchanged; the only new answers are for having no job yet, and they are one plain sentence: "No job is loaded yet. Drop a runner job folder on the page, or try the built-in sample."

## 0.5.1 - 2026-10-27

- **You can now choose which take stays.** Behind More choices there is one new line: "When a line is said more than once, keep:" with three choices. **The last take** is the rule you already know, and it stays the default. **The first clean take** keeps the first complete delivery instead, through the same safety checks, and the result says which rule decided. Your pick is saved with your profile and remembered in the session, and whenever it is not the default the front door says so before you fire: "Remove retakes is on. Keeping: the first clean take."
- **Best delivery (Beta) is here, watching only.** Pick it and the last take still decides every cut. In the background, Beta records the take it would have kept on every pair where there is a real choice, judging by flow: which delivery finishes the sentence and carries the story forward. Its pick shows as a note on the removed words and in the review rows, and the card keeps score in plain words: "Best delivery has matched your pick in X of its last Y calls," once you grade a change or swap a take. When two takes say the very same words, Beta always calls it a tie and the last take stays; it never invents a difference. Pairs under protection or a keep-whole direction are left out of the score. Nothing promotes Beta on its own in this version.

## 0.5.0 - 2026-10-27

Remove retakes is an engine now, not a suggestion. Until this version the front door's retakes switch only sent one sentence of guidance to the direction model, so with no comments pinned it measured nothing. Now the app measures the transcript itself, locally, before any model is asked anything.

- The local check: the transcript is read in sentence-sized pieces and nearby pieces are scored against each other (how much of one is contained in the other, whether they start the same way, how closely they match). Clear pairs are taken out through the same safety check every other change passes. Pairs near the line are judged, and pairs that carry a "not", or a short line said twice in a row that could be emphasis, are never cut by the machine at all; they are flagged for you, because which copy stays can change the meaning.
- Judged by flow, not just by similarity: the take that completes its sentence cleanly beats a broken or trailing one, and among complete takes the last one wins. Every pair remembers whether each side finished its sentence. Borderline pairs go to the model in one call per video, carrying only the pairs, your saved rules, and anything you said in the direction box; the model answers keep or cut per pair and never chooses the words itself, so a judged cut always lands on the measured words. What takes attention away from the story (false starts, restarts, dead-end fragments) is what gets struck. A line that merely fails to serve the direction is never cut on story grounds alone; it is flagged for your review instead. With no model available, borderline pairs become scored flags with the match explained in plain words, never silence.
- Both takes, where you already look: a retake the app takes out lands in the transcript dots like every other edit, and its panel shows the take that stayed next to the one that came out, with two buttons: Bring these words back, and Swap takes. Both pass the same safety check as everything else.
- Grades teach it your bands: a graded retake carries which band found it, so the patterns behind the style card are per band (the sure local finds, the model-judged ones, the ones left to you). Grade a retake "did not work" and that pair is never proposed again.
- Measured, honestly: on the labeled IMG_2295 edit (the runner's own 16 retake decisions), the clear band's proposals were right 9 times out of 9 (100% precision) and found 9 of the 16; 15 of the 16 surfaced in some band, nothing was proposed over protected or already-settled words, and all four leftovers the runner missed landed the way they should (the broken "drugs or," line and the doubled "it's not bad" as flags for a person, the "And sometimes..." fragment cut, the false start judged). The clear band is a small sample, so it stays narrow on purpose: it only ever proposes through the safety check, and your dots and grades are the standing review. Delivery signals (how a take sounds: pitch shape, pacing, confidence) are a tiebreaker for a later version, scored and kept off the automatic path; pitch amount alone does not separate your kept takes from your cut ones, so it is not used.

## 0.4.1 - 2026-10-27

Drag and drop. The app vacuums a job in: no path typing, no Terminal.

- Drop a runner job folder anywhere on the test page and the app takes it in. The folder arrives file by file, with progress in plain words ("Bringing in file 3 of 12"), and is staged as a copy in the session's intake folder. The moment the copy is in, the app runs on it exactly as if its path had been picked at launch, and the page reloads on the new job. Your original folder is never changed; the app works on the copy it took in. Choosing a folder or files with a button works too, for the days dragging is awkward.
- Drop a single video and it stages and plays right on the page. The page says in one sentence what a full edit still needs: the word-by-word transcript and the edit plan that part 1, the runner, makes. A video that is not in a browser-friendly format is played through a converted copy, the same arrangement as job videos, and the staged original is never touched.
- Drop a folder that is not a finished-edit job and the page says so, plainly: a job folder carries transcript.json and cuts.json together, nothing was loaded, and nothing was changed. Drop the job folder itself and it loads.
- The folder-path route is untouched: the launcher still takes a pasted path exactly as before, and the drop simply lands in the same place by a shorter road.

## 0.4.0 - 2026-10-27

The simple build. The test page was rebuilt around how a creator actually works: two choices, one button, and the transcript itself explaining every change.

- A front door that fits on one screen: Remove retakes and Cut silence, both on unless you turn them off, one silence buffer (a slider or a typed number, starting at 0.1 seconds), and one Make the edit button. Everything else lives behind a single More choices fold, which holds the format scope and a spoken-direction box; what you say there adds to the two choices, and saying "unless" marks the one spot to treat differently. Cut silence trims the long pauses toward your buffer through the same safety checks as any other change, and never adds silence back.
- Scrubbing you can feel: the timeline handle grows under your finger or cursor while you drag, a bubble above it shows the exact time where you will land, and Back 5s and Back 1s buttons sit beside the player.
- Times are estimates now, everywhere: a pinned comment is treated as pointing near a moment, not at an exact frame. Direction resolves it to the nearest words within about three seconds either way, and the model is told to trust the words and the note over the exact second.
- The transcript is the why: every span this app has taken out carries a dot. Click the dot and a small panel names the removed words and why they were removed, in plain sentences. There is no separate page to hunt through.
- Notes belong to edits, not timestamps: from that same panel you can type a note, or speak it where the browser offers voice typing. The note is stored against the edit itself, listed under it from then on, and handed to the next direction run labeled with what was removed, so it still means the same thing after the timing shifts. No note is ever lost.
- Your style, learned one yes at a time: when the same kind of change keeps working in your grades (three times, or once when you mark a change "This is my style" on the review page), the app offers it once, in plain words: "Pattern caught." Save it and it becomes a rule that rides into every later run as standing direction; pass it over and it waits; remove a saved rule whenever you like. Nothing is ever adopted without your tap. The review page gained a Send grades to the app button, so grades reach the app without a file hand-off (the export still works too).
- The page's headline counts now describe the edit as your session currently has it, matching the transcript, instead of the job as it originally shipped.

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

## 0.16.0, test fix-up (fit battery and full suite)
- The 0.13 shortcut test that banned manual-editing keys
  from the printed list now expects the 0.16 rows (split,
  clipboard, delete) and keeps banning the keys the design
  left out (Cmd+B blade, Shift+T, nudge). Same treatment
  the 0.15 round gave the Backspace row.
