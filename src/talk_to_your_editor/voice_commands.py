"""Local voice commands for the review screen (v0.11.0, fix item 8).

Two layers, in the order Justin settled:

1. Local matching first. The phrase lists in this module are
   written fresh for this app. A matched command runs the exact
   action its button runs, at $0, instantly, with no model call:
   this module is pure text work and makes no calls at all.
2. Only words that match nothing here can go further, and the
   web app decides that part (direction when a key is set and
   the words read as direction, otherwise a feedback note).

Matching rules, kept deliberately strict:

- Words are normalized (case, punctuation, filler words at the
  edges) before anything is compared.
- An exact phrase match acts. Words that contain phrases from
  two different commands ask instead of acting: unclear words
  never trigger a guess, destructive or otherwise.
- A close spelling of two different commands also asks. A close
  spelling of only one command matches nothing: it is never
  enough on its own to run something.
- Navigation commands are parameterized: the amount or the
  clock target is pulled out of the spoken words, including
  spoken numbers and clock forms like "1:30".

One catalog entry has no action yet: centering a person in the
frame. The app has no centering or reframing action, so those
words are recognized as a known command the app cannot do yet,
and the answer says so plainly instead of guessing at another
action or saving the words somewhere else.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher


# ---------------------------------------------------------------------------
# The catalog: one phrase list per command
# ---------------------------------------------------------------------------

COMMAND_LABELS = {
    "undo": "Undo",
    "redo": "Redo",
    "remove_retakes": "Remove retakes",
    "keep_retakes": "Keep retakes",
    "trim_audio": "Trim audio",
    "play": "Play",
    "pause": "Pause",
    "next_change": "Next change",
    "make_edit": "Make the edit",
    "seek_relative": "Move in the video",
    "seek_absolute": "Go to a time in the video",
    "seek_start": "Go to the beginning",
    "seek_end": "Go to the end",
    "center_frame": "Center me in the frame",
}

PHRASE_LISTS: dict = {
    "undo": [
        "undo",
        "undo that",
        "undo it",
        "undo the last change",
        "undo the last edit",
        "undo last change",
        "take that back",
        "take it back",
        "put it back",
        "put that back",
        "go back one step",
        "step back",
        "reverse that",
        "revert that",
        "cancel that change",
        "cancel that last edit",
        "never mind that change",
        "undo the last one",
    ],
    "redo": [
        "redo",
        "redo that",
        "redo it",
        "redo the last change",
        "do that again",
        "put that back in",
        "put it back on",
        "bring that back",
        "restore that change",
        "move forward one step",
        "step forward",
        "un undo that",
        "undo the undo",
        "redo the last one",
        "do it again",
        "go forward one step",
        "reapply that",
    ],
    "remove_retakes": [
        "remove retakes",
        "remove the retakes",
        "cut the retakes",
        "cut retakes",
        "take out the retakes",
        "take the retakes out",
        "get rid of the retakes",
        "delete the retakes",
        "drop the retakes",
        "clean up the retakes",
        "take out the repeated lines",
        "remove the repeated takes",
        "cut the repeated takes",
        "only keep one take",
        "keep only the last take",
        "strip the retakes",
        "take out retakes",
        "remove all the retakes",
    ],
    "keep_retakes": [
        "keep retakes",
        "keep the retakes",
        "restore retakes",
        "restore the retakes",
        "bring back the retakes",
        "put the retakes back",
        "leave the retakes in",
        "keep all the takes",
        "do not remove retakes",
        "dont remove the retakes",
        "stop removing retakes",
        "turn off remove retakes",
        "leave the retakes",
        "do not remove the retakes",
        "dont remove retakes",
        "keep all the retakes",
        "bring the retakes back",
        "keep the retakes in",
    ],
    "trim_audio": [
        "trim audio",
        "trim the audio",
        "cut the silence",
        "cut silence",
        "remove the silence",
        "remove silence",
        "tighten it up",
        "tighten the audio",
        "remove the pauses",
        "remove pauses",
        "cut the pauses",
        "take out the pauses",
        "shorten the pauses",
        "trim the pauses",
        "cut the dead air",
        "remove the dead air",
        "take out the gaps",
        "cut the gaps between words",
        "take out the silence",
        "tighten up the audio",
    ],
    "play": [
        "play",
        "play it",
        "play the video",
        "start playing",
        "start the video",
        "keep playing",
        "resume",
        "resume playing",
        "press play",
        "roll the video",
        "let it play",
        "hit play",
        "play from here",
        "roll it",
    ],
    "pause": [
        "pause",
        "pause it",
        "pause the video",
        "stop the video",
        "stop playing",
        "hold on",
        "wait",
        "press pause",
        "freeze it",
        "stop it there",
        "stop it",
        "hit pause",
        "pause here",
    ],
    "next_change": [
        "next change",
        "next edit",
        "go to the next change",
        "jump to the next change",
        "next cut",
        "go to the next cut",
        "skip to the next change",
        "move to the next change",
        "the next one",
        "next spot",
        "go to the next spot",
        "take me to the next change",
        "find the next change",
        "go to next change",
        "show me the next change",
    ],
    "make_edit": [
        "make the edit",
        "make edit",
        "run the edit",
        "do the edit",
        "make it",
        "run it",
        "apply the edit",
        "apply the changes",
        "make the changes",
        "go ahead and make the edit",
        "build the edit",
        "finish the edit",
        "make the edits",
        "apply it",
        "run the front door",
        "make this edit",
    ],
    # Navigation examples. The matcher below is parameterized:
    # these are the shapes it understands, with the amount or
    # the clock target pulled out of the words.
    "seek_relative": [
        "go back five seconds",
        "go ahead five seconds",
        "jump back a minute",
        "skip forward ten seconds",
        "skip back ten seconds",
        "rewind five seconds",
        "fast forward thirty seconds",
        "back five seconds",
        "forward ten seconds",
        "move back one minute",
        "go forward twenty five seconds",
        "back half a minute",
    ],
    "seek_absolute": [
        "go to one minute and thirty seconds",
        "take me to 1:30",
        "go to two minutes",
        "jump to forty five seconds",
        "take me to 0:45",
        "go to the one minute mark",
        "seek to three minutes and ten seconds",
        "move to one minute",
        "take me to 2:00",
        "go to ninety seconds",
    ],
    "seek_start": [
        "jump to the beginning",
        "go to the beginning",
        "back to the start",
        "go back to the start",
        "back to the beginning",
        "return to the beginning",
        "take me to the beginning",
        "go to the start",
        "start over",
        "start from the beginning",
        "rewind to the beginning",
        "jump to the start",
    ],
    "seek_end": [
        "jump to the end",
        "go to the end",
        "take me to the end",
        "skip to the end",
        "jump to the end of the video",
        "go to the end of the video",
        "move to the end",
        "take me to the very end",
    ],
    "center_frame": [
        "center me in the frame",
        "centre me in the frame",
        "center me",
        "centre me",
        "put me in the center of the frame",
        "put me in the centre of the frame",
        "center the subject",
        "center me in frame",
        "keep me centered in the frame",
        "keep me centred in the frame",
        "move me to the center of the frame",
        "reframe me in the center",
    ],
}

# Commands a voice match may run. center_frame is in the catalog
# on purpose and is not in this set: it is known, and unavailable.
ACTION_COMMANDS = tuple(
    name for name in COMMAND_LABELS if name != "center_frame"
)

UNAVAILABLE_COMMANDS = ("center_frame",)

CENTER_UNAVAILABLE_SENTENCE = "I cannot center you in the frame yet."


# ---------------------------------------------------------------------------
# Normalizing
# ---------------------------------------------------------------------------

_FILLER_STARTS = (
    "please could you ",
    "please can you ",
    "could you please ",
    "can you please ",
    "could you ",
    "can you ",
    "would you ",
    "hey ",
    "okay ",
    "ok ",
    "so ",
    "just ",
    "please ",
    "i want you to ",
    "i need you to ",
    "go ahead and ",
)

_FILLER_ENDS = (" please", " thanks", " thank you", " now")


def normalize(text) -> str:
    """The words, in the one shape every comparison uses.

    Lower case, punctuation gone (a clock like 1:30 keeps its
    colon), filler words at the edges gone, spaces single. A
    hyphen becomes a space, so "twenty-five" reads as two words.
    """
    value = str(text or "").lower().replace("\u2019", "'")
    value = value.replace("-", " ")
    value = re.sub(r"[^\w\s:']", " ", value)
    value = value.replace("'", "")
    value = re.sub(r"\s+", " ", value).strip()
    changed = True
    while changed:
        changed = False
        for filler in _FILLER_STARTS:
            if value.startswith(filler):
                value = value[len(filler):].strip()
                changed = True
        for filler in _FILLER_ENDS:
            if value.endswith(filler):
                value = value[: -len(filler)].strip()
                changed = True
        if value == "please":
            value = ""
    return value


# ---------------------------------------------------------------------------
# Spoken numbers and durations
# ---------------------------------------------------------------------------

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

_UNIT_SECONDS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600,
}


def _number_at(tokens: list, index: int):
    """(value, words used) for a number starting at ``index``.

    Handles digits, single number words, and compound forms
    like "twenty five". Returns (None, 0) when no number starts
    there.
    """
    if index >= len(tokens):
        return None, 0
    token = tokens[index]
    if token.isdigit():
        return int(token), 1
    if token in _NUMBER_WORDS:
        value = _NUMBER_WORDS[token]
        if (
            value >= 20
            and index + 1 < len(tokens)
            and tokens[index + 1] in _NUMBER_WORDS
            and _NUMBER_WORDS[tokens[index + 1]] < 10
        ):
            return value + _NUMBER_WORDS[tokens[index + 1]], 2
        return value, 1
    return None, 0


def parse_duration(text: str):
    """Seconds in a spoken duration, or None when there is none.

    Reads "five seconds", "a minute", "half a minute", and
    compound forms like "one minute and thirty seconds". A bare
    number with no unit reads as seconds, the way a person
    means it after go back or go ahead.
    """
    normalized = normalize(text)
    if not normalized:
        return None
    clock = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", normalized)
    if clock:
        parts = [int(part) for part in clock.groups() if part is not None]
        total = 0
        for part in parts:
            total = total * 60 + part
        return float(total)
    tokens = normalized.split()
    total = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "half" and index + 2 < len(tokens) + 1:
            pair = tokens[index + 1:index + 3]
            if len(pair) == 2 and pair[0] in ("a", "an") and pair[1] in _UNIT_SECONDS:
                total = (total or 0) + _UNIT_SECONDS[pair[1]] / 2.0
                index += 3
                continue
        if token in ("a", "an") and index + 1 < len(tokens) and tokens[index + 1] in _UNIT_SECONDS:
            total = (total or 0) + _UNIT_SECONDS[tokens[index + 1]]
            index += 2
            continue
        value, used = _number_at(tokens, index)
        if value is not None:
            unit_index = index + used
            if unit_index < len(tokens) and tokens[unit_index] in _UNIT_SECONDS:
                total = (total or 0) + value * _UNIT_SECONDS[tokens[unit_index]]
                index = unit_index + 1
                continue
            if total is None:
                # A bare number: seconds.
                total = float(value)
                index = unit_index
                continue
        index += 1
    return float(total) if total is not None else None


def _clock_target(text: str):
    """Seconds for a clock-form target on its own, or None."""
    match = re.search(r"\b\d+:\d{1,2}(?::\d{1,2})?\b", text)
    if not match:
        return None
    return parse_duration(match.group(0))


# ---------------------------------------------------------------------------
# Navigation: parameterized seek commands
# ---------------------------------------------------------------------------

_BACK_WORDS = ("back", "backward", "backwards", "rewind")
_FORWARD_WORDS = ("ahead", "forward", "forwards", "fast forward")


def _match_navigation(normalized: str):
    """A seek decision dict for navigation words, or None.

    Start and end are their own commands. A direction with an
    amount is seek_relative (signed seconds; back is negative).
    A direction with no amount uses the review player's own
    step, five seconds, the step its Back 5 and Fwd 5 buttons
    use. A "go to" with a time is seek_absolute.
    """
    if normalized in PHRASE_LISTS["seek_start"]:
        return {"command": "seek_start", "seconds": 0}
    if normalized in PHRASE_LISTS["seek_end"]:
        return {"command": "seek_end", "seconds": None}
    if re.search(r"\b(beginning|start)\b", normalized) and (
        "back to" in normalized or "go to" in normalized
        or "jump to" in normalized or "take me to" in normalized
        or "return to" in normalized or normalized == "start over"
        or "from the beginning" in normalized
        or "rewind to" in normalized
    ):
        return {"command": "seek_start", "seconds": 0}
    if re.search(r"\bend\b", normalized) and (
        "go to" in normalized or "jump to" in normalized
        or "take me to" in normalized or "skip to" in normalized
    ):
        return {"command": "seek_end", "seconds": None}

    # Absolute: go to a clock or a spoken time.
    absolute_head = (
        "go to ", "take me to ", "jump to ", "seek to ",
        "move to ", "go to the ", "take me to the ",
    )
    if normalized.startswith(absolute_head) or " to " in normalized and (
        normalized.startswith(("go ", "take ", "jump ", "seek ", "move "))
    ):
        target_text = normalized
        clock = _clock_target(target_text)
        if clock is not None:
            return {"command": "seek_absolute", "seconds": clock}
        spoken = parse_duration(
            target_text.replace("the", " ").replace("mark", " ")
        )
        if spoken is not None and (
            ":" in target_text
            or any(unit in target_text for unit in _UNIT_SECONDS)
        ):
            return {"command": "seek_absolute", "seconds": spoken}

    # Relative: a direction, with or without an amount.
    backwards = any(
        re.search(rf"\b{re.escape(word)}\b", normalized)
        for word in _BACK_WORDS
    )
    forwards = any(
        re.search(rf"\b{re.escape(word)}\b", normalized)
        for word in _FORWARD_WORDS
    ) or normalized.startswith(("skip forward", "fast forward"))
    if backwards == forwards:
        # Both directions named, or neither: not a clear move.
        if normalized in ("skip forward", "fast forward", "rewind"):
            forwards = normalized != "rewind"
            backwards = normalized == "rewind"
        else:
            return None
    amount = parse_duration(normalized)
    if amount is None:
        amount = 5.0
    seconds = -amount if backwards else amount
    return {"command": "seek_relative", "seconds": seconds}


# ---------------------------------------------------------------------------
# The match itself
# ---------------------------------------------------------------------------


def _contains_matches(normalized: str) -> list:
    """Command ids whose phrases appear inside the words.

    Navigation phrases are parameterized, so they are matched
    by the navigation parser, never by this containment scan.
    """
    found = []
    for command, phrases in PHRASE_LISTS.items():
        if command in ("seek_relative", "seek_absolute", "seek_start", "seek_end"):
            continue
        for phrase in phrases:
            if phrase in normalized:
                found.append(command)
                break
    return found


def _near_candidates(normalized: str) -> list:
    """Two commands the words are a close spelling of, or [].

    Close means a high similarity to a phrase from each of two
    different commands, with the two scores near each other.
    One close command on its own is not enough to act on, and
    never returns here.
    """
    scored = []
    for command, phrases in PHRASE_LISTS.items():
        if command in ("seek_relative", "seek_absolute", "seek_start", "seek_end"):
            continue
        best = 0.0
        for phrase in phrases:
            best = max(
                best,
                SequenceMatcher(None, normalized, phrase).ratio(),
            )
        if best >= 0.72:
            scored.append((best, command))
    scored.sort(reverse=True)
    if len(scored) >= 2 and scored[0][0] - scored[1][0] <= 0.08:
        return [scored[0][1], scored[1][1]]
    return []


def command_label(command: str, seconds=None) -> str:
    """The plain name of a command, with its amount when it has one."""
    if command == "seek_relative" and seconds is not None:
        amount = abs(seconds)
        shown = int(amount) if float(amount).is_integer() else amount
        direction = "back" if seconds < 0 else "ahead"
        unit = "second" if amount == 1 else "seconds"
        if amount and amount % 60 == 0:
            minutes = int(amount // 60)
            return (
                f"Go {direction} {minutes} "
                f"{'minute' if minutes == 1 else 'minutes'}"
            )
        return f"Go {direction} {shown} {unit}"
    if command == "seek_absolute" and seconds is not None:
        shown = int(seconds) if float(seconds).is_integer() else seconds
        return f"Go to {shown} seconds"
    return COMMAND_LABELS.get(command, command)


def match_voice_command(text) -> dict:
    """Match dictated words against the local catalog.

    Returns a plain dict, one of:

    - {"decision": "command", "command": id, "seconds": ...}
      for a clear match (seconds only on navigation commands);
    - {"decision": "ask", "candidates": [id, id]} when the
      words sit between two commands and nothing may run;
    - {"decision": "unavailable", "command": "center_frame"}
      for the known command the app cannot do yet;
    - {"decision": "none"} when nothing matches.

    Pure and synchronous: no calls, no waiting. That is the
    speed design in code: the decision comes back at once and
    the page fires the command's own handler in the same step.
    """
    heard = str(text or "").strip()
    normalized = normalize(heard)
    result = {"heard": heard, "normalized": normalized, "seconds": None}
    if not normalized:
        result["decision"] = "none"
        return result

    # Exact phrases first, so a settled phrase like "go back
    # one step" (undo) or "put that back" (undo) is never read
    # as a navigation move by the parameterized parser below.
    for command, phrases in PHRASE_LISTS.items():
        if command in ("seek_relative", "seek_absolute"):
            continue
        if normalized in phrases:
            if command in UNAVAILABLE_COMMANDS:
                result.update(decision="unavailable", command=command)
            elif command == "seek_start":
                result.update(
                    decision="command", command=command, seconds=0
                )
            elif command == "seek_end":
                result.update(decision="command", command=command)
            else:
                result.update(decision="command", command=command)
            return result

    navigation = _match_navigation(normalized)
    if navigation is not None:
        result.update(
            decision="command",
            command=navigation["command"],
            seconds=navigation["seconds"],
        )
        return result

    contained = _contains_matches(normalized)
    if len(contained) >= 2:
        result.update(
            decision="ask", candidates=contained[:2]
        )
        return result
    if len(contained) == 1:
        command = contained[0]
        if command in UNAVAILABLE_COMMANDS:
            result.update(decision="unavailable", command=command)
        else:
            result.update(decision="command", command=command)
        return result

    near = _near_candidates(normalized)
    if len(near) == 2:
        result.update(decision="ask", candidates=near)
        return result

    result["decision"] = "none"
    return result


# ---------------------------------------------------------------------------
# Unmatched words: do they read as direction?
# ---------------------------------------------------------------------------

_DIRECTION_HINTS = (
    "make", "cut", "remove", "trim", "take out", "shorten",
    "tighten", "fix", "change", "move", "add", "keep", "leave",
    "lower", "raise", "turn down", "turn up", "bring", "put",
    "show", "hide", "edit", "warm", "louder", "quieter",
    "slower", "faster", "music", "sound", "opening", "ending",
    "middle", "story", "part",
)


def reads_as_direction(text) -> bool:
    """A simple, deterministic test: no model call decides this.

    Words read as direction when they carry a plain action or
    edit word from the list above and run to at least three
    words. Anything else is a note, not a guess at direction.
    """
    normalized = normalize(text)
    if len(normalized.split()) < 3:
        return False
    return any(
        re.search(rf"\b{re.escape(hint)}\b", normalized)
        or hint in normalized
        for hint in _DIRECTION_HINTS
    )
