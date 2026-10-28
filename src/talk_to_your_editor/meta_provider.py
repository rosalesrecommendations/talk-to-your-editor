"""Model providers: Muse Spark / Muse Wonder via the Meta Model API, plus a
deterministic mock for offline demos and tests.

Routing is deterministic and mirrors the verifier's risk tiers:

- Spark (``muse-spark-1.3``) proposes operations for ordinary comments.
- Wonder (``muse-wonder-1.0``) is required for restore and replace-take
  proposals, cuts longer than 20 words or 10 seconds, opening or closing
  passages, and any proposal under 0.80 confidence. When a Wonder review is
  required, the provider either escalates (Wonder receives the primary
  evidence plus the Spark proposal as the claim under review) or, when
  escalation is disabled, records the requirement and converts the operation
  to ``flag_for_review`` so nothing high-risk is silently applied.

Standard tier only. Contributor-tier model ids are rejected by the schema.
The API key comes from the ``MODEL_API_KEY`` environment variable and is
never written to code, files, or logs.

Hosted Spark routes (v0.8.1): Spark-tier calls may instead ride one
of the hackathon partner credit pools, Baseten or Fireworks, which
serve Muse Spark Open over OpenAI-compatible chat completions.
Only the transport changes (endpoint, bearer key, wire model id);
prompts, calibration, parsing, and the operations' provenance are
untouched, and Wonder always stays on the Meta route, the only
place it exists. A hosted call that errors or times out falls back
to the Meta route once, and the fallback is recorded as a route
event so the recap can say which route actually ran.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional

from .schema import (
    MAX_JOIN_PAUSE_S,
    MIN_JOIN_PAUSE_S,
    Operation,
    SchemaError,
    validate_operations,
)
from .timeline import Timeline
from .verifier import required_tier

API_URL = "https://api.meta.ai/v1/chat/completions"
MODELS_URL = "https://api.meta.ai/v1/models"
SPARK_MODEL = "muse-spark-1.3"
WONDER_MODEL = "muse-wonder-1.0"

# The routes a Spark-tier call can take (v0.8.1). "meta" is the
# Meta Model API the app has always used. The two hosted routes
# are the hackathon partner credit pools: each carries $100 of
# credits for Muse Spark Open, is OpenAI-compatible, and takes a
# bearer key. Wonder has no hosted route: it only exists on Meta.
BASETEN_URL = "https://inference.baseten.co/v1/chat/completions"
BASETEN_MODELS_URL = "https://inference.baseten.co/v1/models"
BASETEN_SPARK_MODEL = "meta-models/muse-spark-open-1026"
FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
FIREWORKS_MODELS_URL = "https://api.fireworks.ai/inference/v1/models"
FIREWORKS_SPARK_MODEL = "accounts/fireworks/models/muse-spark-open"

SPARK_ROUTES = {
    "meta": {"url": API_URL, "models_url": MODELS_URL, "model": SPARK_MODEL},
    "baseten": {
        "url": BASETEN_URL,
        "models_url": BASETEN_MODELS_URL,
        "model": BASETEN_SPARK_MODEL,
    },
    "fireworks": {
        "url": FIREWORKS_URL,
        "models_url": FIREWORKS_MODELS_URL,
        "model": FIREWORKS_SPARK_MODEL,
    },
}
HOSTED_SPARK_ROUTES = ("baseten", "fireworks")
ROUTE_LABELS = {
    "meta": "Meta",
    "baseten": "Baseten",
    "fireworks": "Fireworks",
}

SCHEMA_SPEC = """Return JSON only: {"operations": [ ... ]}. Allowed operation types:
- {"type":"cut_words","comment_id":str,"word_start":int,"word_end":int,"reason":str,"confidence":0..1,"model":str,"risk_tier":1..3}
- {"type":"restore_words", same word fields}
- {"type":"replace_take","cut_word_start":int,"cut_word_end":int,"restore_word_start":int,"restore_word_end":int, ...common}
- {"type":"set_join_pause","join_index":int,"target_pause_s":0.08..0.50, ...common}
- {"type":"flag_for_review","reason":str, ...common}
Word spans are half-open: words[word_start:word_end]. Use the exact model id
given in the request as "model". No other fields are allowed."""

RETAKE_CALIBRATION = """
CALIBRATION EXAMPLES FROM THE CREATOR'S OWN EDITING (his finished edit is the answer key; his reasons are in his words)

Example B1, a plain chain of takes (last take wins):
Cut pair:  "and I’m just preaching the book itself to everyone around me. And shout it out, man. And you"
Keep pair (later): "and preach the book line to everyone around me and shout it out, man. And you preach it wherever you go"
Verdict: cut_earlier, confidence high.
His reason: "Remove retakes. My last take is always my best take. I don’t move on until I get it right." (walkthrough 11:47)

Example B2, a drifted near-duplicate (the pair the tools miss):
Earlier: "I’ve gotten into this tank I said today’s day blank of Every time"
Later: "I get into this tank, I said, today is day 30 of the cold plunge challenge"
Verdict: cut_earlier, confidence medium to high. The two deliveries share few exact words; the earlier one dies mid-phrase ("day blank of") and the later one completes the same opening move. The sayings are the same act performed twice, once broken.
His reason: "It did not catch that I wish it would have." (walkthrough 13:56); "I would love if you can catch that type of stuff, Muse." (walkthrough 14:21)

Example B3, three voices of one prayer (keep everything):
Earlier: "God, I pray that you give me an excitement and a fervor and a giving heart to where if you ask me to, God, I will give it away to show I am yours"
Later: "And if there’s any prayer that I have, it’s this: God, I pray that you give me an excitement and a fervor for giving, and a heart to, if you asked me to, that I’d give it away, to show you that I’m yours"
And later still: "if there’s any prayer that I would have is this: God, I pray that you give me an excitement and a fervor for giving. God, may you give me a heart that if you asked me to give it away, I will give it away, to show you that I am yours"
Verdict: keep_both (all three stand). Similar wording, different sayings: each is a complete prayer, and the second opens a contrast ("any prayer that I have") the third answers.
His reason: "That part I said three times in different ways. So, I would want to keep all three of those." (walkthrough 29:30)

Example B4, a measurement update (not a pair):
Earlier: "and that is a full 2.18 centimeters"
Later: "but actually it was wrong. It’s 2.16 centimeters"
Verdict: not_a_pair. The later words CORRECT the earlier number; nothing was re-said. Correcting a fact is not a retake, and cutting the correction would leave the wrong number standing as the video's claim.
His reason: "But actually, it was wrong. It’s 2.16 centimeters." (from the finished edit; the correction is the content)

Example B5, earlier wording of a point made again later (later wording wins):
Cut: "and the offer that I gave to him way back"
Keep (later): "and the submission I gave to him way back when"
Verdict: cut_earlier, confidence high. Same clause, finished the second time with the word he settled on.
His reason: the finished edit keeps the later wording; he cuts the earlier attempt without comment while assembling the sentence (walkthrough 27:35 region).

Example B6, an assembly the judge must not touch (keep_both):
Earlier: "To offer up to God"
Much later, after other material: "my life"
Verdict: keep_both. These are not two takes of one line. They are two pieces of one sentence the creator assembles across a gap; neither repeats the other, and cutting either breaks the sentence he built.
His reason: "I’m evaluating the grammar here, jumping to what makes the most sense." (walkthrough 25:18)
"""

DIRECTION_PASS_RULES = """
You are reading the creator's direction for THIS video and proposing edits to the whole transcript at once. The operations above are the only moves you may make. Operations are cuts only: you may cut words, restore words already cut, replace a take, set a join pause, or flag something for the creator's review. You may never import words from elsewhere, reorder passages, or rewrite wording.

Hard rules:
- Never cut on story grounds alone. A cut needs the direction's words behind it, or one of the discernment patterns in the examples below. If a passage merely seems weak, slow, or off-topic, flag it instead of cutting it.
- The opening hook and the closing prayer, declaration, and sign-off are protected. Propose nothing there except a flag.
- Precise values wording is never smoothed, substituted, or cut for style. If a word fights the sentence around it, that is a flag, not a cut, unless the direction names it.
- Performed repetition that carries a new voice, a new fact, or devotional structure is content, not a retake. Propose nothing for it.
- When the direction asks for tightness, an earlier passage may be cut ONLY when a fuller, cleaner version of the same point survives later in the transcript. Without tightness language in the direction, the same finding is a repeated_point flag, never a cut.
- A repeated passage is not protected by the values rule when a fuller version of the same passage survives later. The values rule protects wording from being smoothed or substituted, and protects a passage that stands alone; it does not protect the earlier copies of a passage said more than once. When the fuller version survives later, propose the cut on the earlier copies, exactly as the repeated-point example below shows.
- Propose the operation for every finding the patterns support, even when the span contains a negation (not, never, no). The safety checks, not you, decide what may apply unattended; a proposal that is held still hands the creator the exact span and your reason. Reserve flags for findings you cannot settle, never as a safer substitute for a cut the patterns support.
- Sentence assembly is cuts only: when a sentence is scattered across broken attempts, cut the broken pieces and leave the pieces that join into the sentence he finished. Never propose importing the missing piece from another attempt.
- Keep every proposed span as small as the finding allows. A tone cut takes the offending words and their self-correction with them, nothing more.

CALIBRATION EXAMPLES FROM THE CREATOR'S OWN EDITING

Example D1, sentence assembly by cuts only:
Transcript region: "...To offer up to God [4.5 second pause] Hey God, if I ever get cancer, I would love... my life... That if God would ever want me to walk some..."
Direction: "Keep it tight and keep the story moving."
Correct operations: cut_words on "Hey God, if I ever get cancer, I would love" and cut_words on "That if God would ever want me to walk some". Nothing else. The kept words join into the sentence he finished: "To offer up to God my life..." Never import words to complete a sentence; assembly is cuts only, and the pieces must already sit next to the join.
His reason: "I'm evaluating the grammar here, jumping to what makes the most sense." (walkthrough 25:18)

Example D2, a tone cut takes the word and its correction:
Transcript region: "...increase the blood flow in the severe, not bad, but..."
Direction: "Keep it tight."
Correct operation: cut_words on "severe, not bad, but" (4 words). The passage continues "...the water has health benefits" and the splice lands clean. When the direction supports tightening, a word that fights the video's tone goes WITH its self-correction; cutting the word alone would leave "not bad, but" dangling.
His reason: "He said severe, that felt negative, so I removed that." (walkthrough 19:10)

Example D3, a repeated point cut under tightness language (56 words):
Transcript region: an early passage gives the cold plunge disclaimer in two full variants plus the head of a third ("I am not recommending this to anyone. This is a personal test on my own body to see if it is possible. I was told I had..."). A complete single variant of the same disclaimer survives later in the transcript.
Direction: "Keep it tight and keep the story moving."
Correct operation: cut_words spanning the two early variants and the third variant's head (56 words), because the fuller version survives later. Without the tightness language, the correct output for the same finding is a repeated_point flag and no operation.
His reason: the finished edit keeps one complete variant, the last one, whole (walkthrough 15:30 region).

Example D4, values wording protected (no operation):
Transcript region: "...bringing suffering to God and allowing him to use it for something far greater than I could ever imagine, possibly for my good, hopefully for the glory of God, but ultimately to offer my life to him in submission regardless of the cost, regardless of the consequences, regardless of what it means for me, regardless of what it means for my family, and regardless of what happens here on out."
Direction: "Keep it tight and keep the story moving."
Correct output: NO operation on this passage, even under tightness language. The wording is the point; it is precise, complete, and load-bearing. Never smooth it, never substitute a shorter synonym, never cut it for pace.
His reason: the passage stands whole in his finished edit, and he re-says pieces of it later in the prayer on purpose.

Example D5, performed repetition (no operation):
Transcript region: the same prayer said three times in different voices across the video (see the retake calibration): "God, I pray that you give me an excitement and a fervor and a giving heart...", later "...if there's any prayer that I have, it's this: God, I pray that you give me an excitement and a fervor for giving...", later still a third full saying.
Direction: "Keep it tight and keep the story moving."
Correct output: NO operation. Each saying is complete, each carries a new voice or a new turn, and the repetition is the devotional structure of the piece.
His reason: "That part I said three times in different ways. So, I would want to keep all three of those." (walkthrough 29:30)

Example D6, a stumble loop excised as one small unit:
Transcript region: "...with God, with support helping to"
Direction: "Keep it tight."
Correct operation: cut_words on "support helping to" (3 words): a loop of half-started phrases between two stable words, removed as one unit so the sentence runs "with God, with..." into what follows. Stumble loops are small; if the span you want is longer than a few words, it is not a stumble loop.
His reason: the finished edit runs the sentence straight through; the loop is restart debris, not content (walkthrough 20:40 region).

Work through the whole transcript in order before you answer. This creator's videos usually hold several of these patterns at once: more than one retake chain, an earlier passage whose fuller version survives later, small stumble loops at sentence joins, a sentence restarted at its head. When a pattern matches, take the whole span it covers: every earlier variant of a repeated passage, not just the longest one; both broken pieces of an assembled sentence, not just the first. Stopping after the first few findings leaves the edit half done. The restraint in the examples still governs every span: when two readings are genuinely possible, flag it instead of cutting.
"""

DIRECTION_PASS_FLAGS = """
In the same reply you may add a "flags" array: things you noticed that the creator did not ask about. A flag NEVER carries an operation and NEVER removes anything. Each flag is {"type": one of the six below, "word_start": int, "word_end": int, "word_start2": int or null, "word_end2": int or null, "reason": one short plain sentence}.
The six types, and only these:
1. repeated_point: the same point made again later with nothing new added.
2. dropped_thread: a story, a name, a number, or a question raised and never returned to.
3. buried_line: one complete line carrying its own point, sitting in the middle of flatter material where it will be skimmed past. A strong line is one the video would be poorer without (essentiality), one that stands complete on its own and makes sense to someone who never saw the rest (completeness), and one that says precisely what the creator means with no slippage (values fit). A flag may say a line READS strong, never that it SOUNDS strong: you have the words, not the voice.
4. drifted_repeat: two spans that say almost the same thing in different words, where you cannot settle whether it was a retake or said twice on purpose. Give both spans (word_start/word_end and word_start2/word_end2).
5. wording_fights: a single word that clashes in tone or sense with the sentence around it. word_start/word_end cover that one word.
6. halfway_thought: a thought that begins and stops, with its completion nowhere in the transcript.
Never flag: the opening hook; the temperature line or the breath before it; the closing prayer, declaration, blessing, or sign-off (as repetition, dropped thread, or buried line); anything already settled by the direction or a saved rule; single-word hearing differences; anything whose only fault is that it does not serve the direction. If no type truly fits, raise no flag. One flag per finding.

Return JSON only: {"operations": [...], "flags": [...]}. Every operation uses the shared schema and carries "target_comment_id": "direction-pass".
"""

DIRECTION_PASS_PAUSES = """
Pauses: silence is handled by the deterministic pass at the chosen buffer; the model does not re-time pauses by feel. The only pause protections are the named exceptions, and protected spans already exclude them from cutting.
"""

DIRECTION_FLAG_TYPES = {
    "repeated_point",
    "dropped_thread",
    "buried_line",
    "drifted_repeat",
    "wording_fights",
    "halfway_thought",
}

WONDER_FALLBACK_REASON = (
    "The reviewer could not be reached, so this was flagged instead "
    "of applied."
)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _clean_direction_flags(raw_flags, word_count: int) -> list[dict]:
    """Validate the model's direction-pass flags, dropping invalid ones."""
    flags: list[dict] = []
    if not isinstance(raw_flags, list):
        return flags
    for entry in raw_flags:
        if not isinstance(entry, dict):
            continue
        flag_type = entry.get("type")
        if flag_type not in DIRECTION_FLAG_TYPES:
            continue
        start, end = entry.get("word_start"), entry.get("word_end")
        if not _is_int(start) or not _is_int(end):
            continue
        if not 0 <= start < end <= word_count:
            continue
        start2, end2 = entry.get("word_start2"), entry.get("word_end2")
        if not (
            _is_int(start2)
            and _is_int(end2)
            and 0 <= start2 < end2 <= word_count
        ):
            start2 = end2 = None
        reason = str(entry.get("reason") or "")[:300]
        flags.append(
            {
                "type": flag_type,
                "word_start": start,
                "word_end": end,
                "word_start2": start2,
                "word_end2": end2,
                "reason": reason,
            }
        )
    return flags


def _kept_context(timeline: Timeline, start: int, end: int) -> tuple[str, str]:
    """Up to 12 kept words before and after a span, as plain text."""
    words = timeline.words
    before: list[str] = []
    index = start - 1
    while index >= 0 and len(before) < 12:
        if timeline.word_status(index) == "kept":
            before.append(str(words[index].get("word", "")))
        index -= 1
    before.reverse()
    after: list[str] = []
    index = end
    while index < len(words) and len(after) < 12:
        if timeline.word_status(index) == "kept":
            after.append(str(words[index].get("word", "")))
        index += 1
    return " ".join(before), " ".join(after)


def _span_text(timeline: Timeline, op: Operation) -> str:
    """The words of an operation's primary span, joined."""
    spans = op.word_spans()
    if not spans:
        return ""
    start, end, _role = spans[0]
    return " ".join(
        str(timeline.words[i].get("word", "")) for i in range(start, end)
    )


class ProviderError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response, robustly."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ProviderError("model_response_not_json")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ProviderError(f"model_response_not_json:{exc.msg}") from exc


def _sentence_span(timeline: Timeline, word_index: int) -> tuple[int, int]:
    words = timeline.words
    start = word_index
    while start > 0 and not words[start - 1]["word"].rstrip().endswith(
        (".", "!", "?")
    ):
        start -= 1
    end = word_index + 1
    while end < len(words) and not words[end - 1]["word"].rstrip().endswith(
        (".", "!", "?")
    ):
        end += 1
    return start, end


class MockProvider:
    """Deterministic provider: derives operations from comment intent."""

    name = "mock"

    usage_log: list = []

    def drain_usage(self) -> list:
        """The mock calls no model, so there is never usage to drain."""
        return []

    def drain_route_events(self) -> list:
        """The mock never leaves its own machine, so no route events."""
        return []

    def direct(
        self,
        feedback: dict,
        timeline: Timeline,
        profile: Optional[dict] = None,
    ) -> tuple[list[dict], list[dict]]:
        operations: list[dict] = []
        routing: list[dict] = []
        for comment in _comments(feedback):
            text = str(comment.get("text", ""))
            comment_id = str(comment.get("id", "comment"))
            lowered = text.lower()
            base = {
                "comment_id": comment_id,
                "model": "mock",
                "risk_tier": 1,
                "wonder_reviewed": False,
            }
            ts = comment.get("ts")
            mapping = None
            if ts is not None:
                try:
                    # A pinned time is an estimate: resolve it to the
                    # nearest editable words within a few seconds
                    # either way instead of demanding the exact second.
                    mapping = timeline.map_output_time_approx(float(ts))
                except ValueError:
                    mapping = None
            if "pause" in lowered:
                operations.append(self._pause_op(comment, base, timeline))
            elif mapping is None:
                operations.append(
                    {**base, "type": "flag_for_review",
                     "reason": "comment timestamp could not be mapped",
                     "confidence": 0.5, "risk_tier": 3}
                )
            elif mapping["special"]:
                operations.append(
                    {**base, "type": "flag_for_review",
                     "reason": "comment is pinned to a protected insert",
                     "confidence": 0.5, "risk_tier": 3}
                )
            elif any(k in lowered for k in ("restore", "put back", "bring back")):
                operations.append(self._restore_op(comment, base, timeline, mapping))
            elif any(k in lowered for k in ("remove", "cut", "delete", "take out")):
                start, end = _sentence_span(timeline, mapping["word_index"])
                confidence = 0.6 if "maybe" in lowered else 0.9
                operations.append(
                    {**base, "type": "cut_words", "word_start": start,
                     "word_end": end,
                     "reason": f"mock: remove the sentence at word {start}",
                     "confidence": confidence}
                )
            else:
                operations.append(
                    {**base, "type": "flag_for_review",
                     "reason": "intent not recognized by the mock provider",
                     "confidence": 0.5, "risk_tier": 3}
                )
            routing.append(
                {"comment_id": comment_id, "route": "mock", "model": "mock"}
            )
        return operations, routing

    @staticmethod
    def _pause_op(comment: dict, base: dict, timeline: Timeline) -> dict:
        joins = timeline.joins()
        ts = float(comment.get("ts", 0.0))
        nearest = min(joins, key=lambda j: abs(j["output_time"] - ts))
        match = re.search(r"(\d*\.\d+|\d+)", str(comment.get("text", "")))
        target = float(match.group(1)) if match else 0.20
        target = min(max(target, MIN_JOIN_PAUSE_S), MAX_JOIN_PAUSE_S)
        return {
            **base,
            "type": "set_join_pause",
            "join_index": nearest["join_index"],
            "target_pause_s": target,
            "reason": f"mock: set join {nearest['join_index']} pause to {target}s",
            "confidence": 0.9,
        }

    @staticmethod
    def _restore_op(
        comment: dict, base: dict, timeline: Timeline, mapping: dict
    ) -> dict:
        source_time = mapping["source_time"]
        cut_indices = [
            i
            for i, w in enumerate(timeline.words)
            if timeline.word_status(i) == "cut"
            and abs((float(w["start"]) + float(w["end"])) / 2.0 - source_time) < 3.0
        ]
        if not cut_indices:
            return {
                **base, "type": "flag_for_review",
                "reason": "no cut words near the comment to restore",
                "confidence": 0.5, "risk_tier": 3,
            }
        start = cut_indices[0]
        end = cut_indices[-1] + 1
        while start > 0 and timeline.word_status(start - 1) == "cut":
            start -= 1
        while end < len(timeline.words) and timeline.word_status(end) == "cut":
            end += 1
        return {
            **base, "type": "restore_words", "word_start": start,
            "word_end": end, "reason": "mock: restore the cut span",
            "confidence": 0.85, "risk_tier": 2,
        }

    def direct_transcript(self, packet, timeline, profile=None):
        """The mock proposes nothing for a whole-transcript pass."""
        return [], [], []

    def judge_retakes(self, pairs, timeline, guidance=None):
        """The mock provider judges nothing: Band B degrades to flags."""
        return None

    def judge_shadow(self, pairs, timeline, guidance=None):
        """The mock provider shadows nothing: the local ranking answers."""
        return None


def _comments(feedback: dict) -> list[dict]:
    if isinstance(feedback, list):
        return feedback
    return feedback.get("comments", [])


def _overall_guidance(feedback: dict) -> list[str]:
    """The feedback's overall notes as plain guidance texts.

    Overall notes are about the whole video rather than one moment,
    so they are handed to the model with every comment it directs.
    """
    if not isinstance(feedback, dict):
        return []
    notes = feedback.get("overall_feedback")
    if not isinstance(notes, list):
        return []
    texts = []
    for note in notes:
        if isinstance(note, dict):
            text = str(note.get("text") or "").strip()
        else:
            text = str(note or "").strip()
        if text:
            texts.append(text)
    return texts


def _edit_guidance(feedback: dict) -> list[dict]:
    """Notes the creator left on specific earlier edits.

    A note on an edit is about the removed material itself, not about
    a timestamp, so it is handed to the model labeled with what was
    removed and why, and the model applies it wherever that material
    is involved in this run.
    """
    if not isinstance(feedback, dict):
        return []
    notes = feedback.get("edit_notes")
    if not isinstance(notes, list):
        return []
    guidance = []
    for item in notes:
        if not isinstance(item, dict):
            continue
        text = str(item.get("note") or "").strip()
        if not text:
            continue
        guidance.append(
            {
                "removed_text": str(item.get("removed_text") or ""),
                "reason": str(item.get("reason") or ""),
                "note": text,
            }
        )
    return guidance


def check_hosted_signin(
    route: str, api_key: Optional[str], timeout_s: float = 60.0
) -> bool:
    """One harmless list call to prove a hosted route's key works.

    Mirrors ``MetaProvider.check_signin``: True when the service
    answers the models list, False when it refuses the key
    (401/403). Anything else raises ProviderError with the route's
    own codes (``baseten_api_http_500``, ``fireworks_api_unreachable:...``),
    so callers report it the same way they report a failed run.
    No key material is logged.
    """
    spec = SPARK_ROUTES.get(route)
    if spec is None or route == "meta":
        raise ProviderError(f"unknown_hosted_route:{route}")
    if not api_key:
        raise ProviderError(f"{route} key is not set")
    request = urllib.request.Request(
        spec["models_url"],
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            response.read()
        return True
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False
        raise ProviderError(f"{route}_api_http_{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"{route}_api_unreachable:{exc.reason}") from exc


class MetaProvider:
    """Live provider over the Meta Model API (OpenAI-compatible).

    Spark-tier calls may ride a hosted route (Baseten or
    Fireworks) when the creator chose one and saved its key;
    Wonder calls always use the Meta route. ``spark_route`` in
    the state the app reads is the EFFECTIVE route: a chosen
    hosted route with no key available resolves to "meta" at
    construction, so a run never aims at a route it cannot use.
    """

    name = "meta"

    def __init__(
        self,
        api_key: Optional[str] = None,
        allow_wonder: bool = True,
        timeout_s: float = 60.0,
        spark_route: str = "meta",
        baseten_key: Optional[str] = None,
        fireworks_key: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("MODEL_API_KEY")
        self.allow_wonder = allow_wonder
        self.timeout_s = timeout_s
        self.usage_log: list[dict] = []
        self._hosted_keys = {
            "baseten": baseten_key or os.environ.get("BASETEN_API_KEY") or None,
            "fireworks": fireworks_key
            or os.environ.get("FIREWORKS_API_KEY")
            or None,
        }
        requested = spark_route if spark_route in SPARK_ROUTES else "meta"
        if requested != "meta" and not self._hosted_keys.get(requested):
            requested = "meta"
        self.spark_route = requested
        self.route_events: list[dict] = []

    def drain_usage(self) -> list[dict]:
        """Return the usage recorded since the last drain, and clear it."""
        drained = self.usage_log
        self.usage_log = []
        return drained

    def drain_route_events(self) -> list[dict]:
        """Return the route fallbacks recorded since the last drain.

        One entry per Spark call that asked for a hosted route and
        ran on Meta instead: {"requested", "used", "error"}. The
        recap reads these to say which route actually ran.
        """
        drained = self.route_events
        self.route_events = []
        return drained

    def _hosted_key(self, route: str) -> Optional[str]:
        return self._hosted_keys.get(route)

    def check_signin(self) -> bool:
        """One harmless list call to prove the sign-in works.

        Returns True when the service answers the models list, False
        when it refuses the key (401/403). Anything else (no key, an
        unreachable service, another HTTP error) raises ProviderError
        with the usual codes, so callers report it the same way they
        report a failed direction run. No key material is logged.
        """
        if not self.api_key:
            raise ProviderError("MODEL_API_KEY is not set")
        request = urllib.request.Request(
            MODELS_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_s
            ) as response:
                response.read()
            return True
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return False
            raise ProviderError(f"meta_api_http_{exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError(
                f"meta_api_unreachable:{exc.reason}"
            ) from exc

    def _record_usage(self, model: str, data: dict, route: str = "meta") -> None:
        """Append one usage entry for a successful chat response.

        A response without a usage object is tolerated: it records
        zero tokens rather than failing the call over bookkeeping.
        The entry names the wire model and the route that served
        the call, so the cost accounting can charge the pool the
        call actually drew on.
        """
        usage = data.get("usage") if isinstance(data, dict) else None
        if not isinstance(usage, dict):
            usage = {}
        entry = {"model": model, "route": route}
        for key in ("prompt_tokens", "completion_tokens"):
            try:
                entry[key] = int(usage.get(key) or 0)
            except (TypeError, ValueError):
                entry[key] = 0
        log = getattr(self, "usage_log", None)
        if log is None:
            log = self.usage_log = []
        log.append(entry)

    def _chat(self, model: str, messages: list[dict]) -> str:
        """One chat call, on the transport the model tier calls for.

        Wonder always goes to Meta. A Spark call goes to the
        chosen hosted route when there is one; if that call errors
        or times out, it is retried once on Meta (when a Meta key
        exists) and the fallback is recorded as a route event, so
        a hosted pool being down never strands a run that Meta
        could have served.
        """
        if model == SPARK_MODEL and self.spark_route != "meta":
            route = self.spark_route
            spec = SPARK_ROUTES[route]
            try:
                return self._chat_once(
                    spec["url"],
                    self._hosted_key(route),
                    spec["model"],
                    messages,
                    route,
                )
            except ProviderError as exc:
                if not self.api_key:
                    raise
                self.route_events.append(
                    {"requested": route, "used": "meta", "error": str(exc)}
                )
        if not self.api_key:
            raise ProviderError("MODEL_API_KEY is not set")
        return self._chat_once(API_URL, self.api_key, model, messages, "meta")

    def _chat_once(
        self,
        url: str,
        api_key: Optional[str],
        model: str,
        messages: list[dict],
        route: str,
    ) -> str:
        """One chat call over one transport, with the standing
        response_format retry. Errors carry the route's own code
        prefix (``meta_api_http_401``, ``fireworks_api_unreachable:...``),
        the shape the plain-words mapping has always read."""
        if not api_key:
            raise ProviderError(f"{route} key is not set")
        body = {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        for attempt in (True, False):
            payload = dict(body)
            if not attempt:
                payload.pop("response_format", None)
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout_s
                ) as response:
                    data = json.loads(response.read().decode("utf-8"))
                self._record_usage(model, data, route)
                return data["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as exc:
                if attempt and exc.code == 400:
                    continue  # response_format unsupported: retry without it
                raise ProviderError(f"{route}_api_http_{exc.code}") from exc
            except urllib.error.URLError as exc:
                raise ProviderError(
                    f"{route}_api_unreachable:{exc.reason}"
                ) from exc
        raise ProviderError(f"{route}_api_unreachable")

    def _propose(
        self, model: str, comment: dict, evidence: dict, extra: Optional[str] = None
    ) -> list[Operation]:
        system = (
            "You are the direction layer of a video editor. You convert one "
            "creator comment into strict edit operations. " + SCHEMA_SPEC
            + " The comment's timestamp is an estimate, not an exact "
            "point: it has been resolved to the nearest words within a "
            "few seconds either way. Trust the words in the evidence "
            "window and what the comment says over the exact second."
        )
        public_evidence = {
            k: v for k, v in evidence.items() if not k.startswith("_")
        }
        user = json.dumps(
            {"comment": comment, "evidence": public_evidence, "model": model,
             "note": extra or ""}
        )
        content = self._chat(
            model,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        parsed = extract_json(content)
        try:
            return validate_operations(parsed)
        except SchemaError as exc:
            raise ProviderError(
                f"model_operations_invalid:{'; '.join(exc.reasons)}"
            ) from exc

    def direct(
        self,
        feedback: dict,
        timeline: Timeline,
        profile: Optional[dict] = None,
    ) -> tuple[list[dict], list[dict]]:
        operations: list[dict] = []
        routing: list[dict] = []
        overall = _overall_guidance(feedback)
        edit_guidance = _edit_guidance(feedback)
        for comment in _comments(feedback):
            evidence = self._evidence(
                comment, timeline, profile, overall, edit_guidance
            )
            proposed = self._propose(SPARK_MODEL, comment, evidence)
            for op in proposed:
                route = self._route(op, comment, evidence, operations, routing)
                routing.append(route)
        return operations, routing

    def direct_transcript(
        self,
        packet: dict,
        timeline: Timeline,
        profile: Optional[dict] = None,
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """One whole-transcript direction pass: Spark proposes, and any
        tier-2 proposals go to Wonder in ONE batched review call.

        ``packet`` carries the creator's unmapped direction verbatim,
        his standing whole-video guidance, his saved rules, and the
        current plan state. Returns ``(operations, routing, flags)``.
        At most two model calls are made; a failed or unparseable
        Spark reply returns three empty lists rather than raising.
        """
        # A whole-transcript read reasons far longer than the
        # per-comment proposals the shared ceiling was set for
        # (measured at 59 to 80 seconds on a ten-minute transcript,
        # against the 60 second default): the pass gets real room,
        # and the shared ceiling is restored for every other call.
        previous_timeout = self.timeout_s
        self.timeout_s = max(self.timeout_s, 240.0)
        try:
            return self._direct_transcript(packet, timeline, profile)
        finally:
            self.timeout_s = previous_timeout

    def _direct_transcript(
        self,
        packet: dict,
        timeline: Timeline,
        profile: Optional[dict] = None,
    ) -> tuple[list[dict], list[dict], list[dict]]:
        if not isinstance(packet, dict):
            packet = {}
        direction = str(packet.get("direction") or "")
        user_payload = {
            "direction": direction,
            "overall_guidance": list(packet.get("overall") or []),
            "saved_rules": list(packet.get("saved_rules") or []),
            "plan_state": dict(packet.get("plan_state") or {}),
            "transcript": [
                {
                    "i": index,
                    "w": word.get("word", ""),
                    "s": word.get("start"),
                    "status": timeline.word_status(index),
                }
                for index, word in enumerate(timeline.words)
            ],
        }
        system = (
            SCHEMA_SPEC
            + DIRECTION_PASS_RULES
            + DIRECTION_PASS_FLAGS
            + DIRECTION_PASS_PAUSES
        )
        try:
            content = self._chat(
                SPARK_MODEL,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user_payload)},
                ],
            )
            parsed = extract_json(content)
        except Exception:
            return [], [], []
        if not isinstance(parsed, dict):
            return [], [], []
        flags = _clean_direction_flags(
            parsed.get("flags"), len(timeline.words)
        )
        raw_operations = parsed.get("operations")
        if not isinstance(raw_operations, list):
            raw_operations = []
        prepared: list[dict] = []
        for raw in raw_operations:
            if not isinstance(raw, dict):
                continue
            op = dict(raw)
            # The schema's carrier field is comment_id; the direction
            # pass stamps it (and the target_comment_id alias on the
            # returned dicts) itself, whatever the model sent.
            op.pop("target_comment_id", None)
            op["comment_id"] = "direction-pass"
            # The proposing model is Spark by construction, whatever
            # the reply claims: the field is provenance, and a wrong
            # value here (the model once sent "direction-pass") makes
            # the schema reject every operation in the pass.
            op["model"] = SPARK_MODEL
            prepared.append(op)
        validated = self._validate_direction_operations(prepared)

        operations: list[dict] = []
        routing_entries: list[tuple[int, dict]] = []
        tier2: list[tuple[int, Operation, int]] = []
        slots: list[Optional[dict]] = [None] * len(validated)
        for position, op in enumerate(validated):
            tier = required_tier(op, timeline)
            if op.type == "flag_for_review" or tier >= 3:
                data = op.to_dict()
                data["target_comment_id"] = "direction-pass"
                data["risk_tier"] = 3
                slots[position] = data
                routing_entries.append(
                    (
                        position,
                        {
                            "target_comment_id": "direction-pass",
                            "action": "flagged",
                            "from_tier": int(op.risk_tier),
                            "to_tier": 3,
                            "reason": op.reason,
                        },
                    )
                )
            elif tier == 2:
                tier2.append((position, op, tier))
            else:
                data = op.to_dict()
                data["target_comment_id"] = "direction-pass"
                slots[position] = data
        if tier2:
            reviewed = self._review_direction_batch(
                direction, tier2, timeline
            )
            for (position, op, tier), data, action, to_tier, reason in reviewed:
                slots[position] = data
                routing_entries.append(
                    (
                        position,
                        {
                            "target_comment_id": "direction-pass",
                            "action": action,
                            "from_tier": tier,
                            "to_tier": to_tier,
                            "reason": reason,
                        },
                    )
                )
        operations = [slot for slot in slots if slot is not None]
        routing = [
            entry
            for _position, entry in sorted(
                routing_entries, key=lambda pair: pair[0]
            )
        ]
        return operations, routing, flags

    @staticmethod
    def _validate_direction_operations(
        prepared: list[dict],
    ) -> list[Operation]:
        """Validate direction-pass operations the way _propose does.

        The batch is validated whole first; if the schema rejects it,
        each operation is validated on its own and only the invalid
        ones are dropped, so one bad proposal cannot sink the pass.
        """
        if not prepared:
            return []
        try:
            return validate_operations({"operations": prepared})
        except SchemaError:
            pass
        validated: list[Operation] = []
        for op in prepared:
            try:
                validated.extend(validate_operations([op]))
            except SchemaError:
                continue
        return validated

    def _review_direction_batch(
        self,
        direction: str,
        tier2: list[tuple[int, Operation, int]],
        timeline: Timeline,
    ) -> list[tuple[tuple[int, Operation, int], dict, str, int, str]]:
        """One Wonder call reviewing every tier-2 proposal at once.

        Returns one entry per tier-2 operation: the reviewed operation
        dict, the routing action, its resulting tier, and the reason.
        Any failure (call error, unparseable reply, wrong operation
        count, schema rejection) flags every proposal instead.
        """
        proposals = []
        for number, (_position, op, _tier) in enumerate(tier2):
            spans = op.word_spans()
            start, end = (spans[0][0], spans[0][1]) if spans else (0, 0)
            context_before, context_after = _kept_context(
                timeline, start, end
            )
            proposals.append(
                {
                    "n": number,
                    "operation": {
                        **op.to_dict(),
                        "target_comment_id": "direction-pass",
                    },
                    "words": _span_text(timeline, op),
                    "context_before": context_before,
                    "context_after": context_after,
                }
            )
        system = (
            "You are the reviewer for a video editor's direction pass. "
            "Review ALL of the proposals at once against the creator's "
            "direction and the hard rules in the proposals' context. "
            "For each proposal, confirm it, correct it (keeping the "
            "same operation shape), or replace it with a "
            "flag_for_review operation. Return JSON only: "
            '{"operations": [...]} with exactly one operation per '
            "proposal, in the same order. Every operation uses the "
            "shared schema: "
            + SCHEMA_SPEC
        )
        user = json.dumps(
            {"direction": direction, "proposals": proposals}
        )
        reviewed_ops: Optional[list[Operation]] = None
        try:
            content = self._chat(
                WONDER_MODEL,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            parsed = extract_json(content)
            raw_reviewed = (
                parsed.get("operations") if isinstance(parsed, dict) else None
            )
            if isinstance(raw_reviewed, list) and len(raw_reviewed) == len(
                tier2
            ):
                cleaned = []
                for raw in raw_reviewed:
                    if not isinstance(raw, dict):
                        raise ProviderError("wonder_review_not_operations")
                    op = dict(raw)
                    op.pop("target_comment_id", None)
                    op["comment_id"] = "direction-pass"
                    if op.get("model") not in (
                        SPARK_MODEL,
                        WONDER_MODEL,
                    ):
                        op["model"] = WONDER_MODEL
                    cleaned.append(op)
                reviewed_ops = validate_operations(
                    {"operations": cleaned}
                )
        except Exception:
            reviewed_ops = None
        results = []
        for index, item in enumerate(tier2):
            _position, op, _tier = item
            if reviewed_ops is not None:
                data = reviewed_ops[index].to_dict()
                data["target_comment_id"] = "direction-pass"
                data["wonder_reviewed"] = True
                data["risk_tier"] = max(int(data.get("risk_tier") or 1), 2)
                results.append(
                    (item, data, "wonder_reviewed", data["risk_tier"],
                     data["reason"])
                )
            else:
                data = {
                    "type": "flag_for_review",
                    "comment_id": "direction-pass",
                    "target_comment_id": "direction-pass",
                    "reason": WONDER_FALLBACK_REASON,
                    "confidence": op.confidence,
                    "model": SPARK_MODEL,
                    "risk_tier": 3,
                    "wonder_reviewed": False,
                }
                results.append(
                    (item, data, "flagged", 3, WONDER_FALLBACK_REASON)
                )
        return results

    def judge_retakes(
        self,
        pairs: list[dict],
        timeline: Timeline,
        guidance: Optional[list] = None,
    ) -> Optional[tuple[list[dict], dict]]:
        """Judge the borderline retake pairs in one Spark call.

        The local detector has already measured every pair; the model
        only picks a verdict per pair id and never draws a span, so a
        judged cut always lands on the measured words. Returns
        ``(operations, verdicts)`` where verdicts maps every answered
        pair id to its verdict string, or None when no judgment could
        be made at all, so the caller degrades the pairs to scored
        flags instead of losing them. Raises ProviderError on a
        failed call, like every other provider path.
        """
        if not pairs:
            return [], {}
        words = timeline.words
        packet = []
        for pair in pairs:
            spans = sorted(
                (pair["cut"], pair["keep"]),
                key=lambda span: span["word_start"],
            )
            first, second = spans[0], spans[1]
            lo = max(0, first["word_start"] - 10)
            hi = min(len(words), second["word_end"] + 10)
            first_is_cut = first is pair["cut"]
            packet.append(
                {
                    "pair_id": pair["pair_id"],
                    "first_delivery": first["text"],
                    "second_delivery": second["text"],
                    "first_completes_the_sentence": (
                        pair.get("cut_complete")
                        if first_is_cut
                        else pair.get("keep_complete")
                    ),
                    "second_completes_the_sentence": (
                        pair.get("keep_complete")
                        if first_is_cut
                        else pair.get("cut_complete")
                    ),
                    "surrounding_words": " ".join(
                        str(words[i].get("word", "")) for i in range(lo, hi)
                    ),
                    "measured": {
                        "coverage": pair.get("coverage"),
                        "ratio": pair.get("ratio"),
                        "shape": pair.get("shape"),
                    },
                }
            )
        system = (
            "You judge pairs of lines from one video transcript. A local "
            "checker measured each pair and found the two deliveries "
            "similar; it cannot tell a retake from a line said twice on "
            "purpose. Judge by storytelling flow, not similarity alone. "
            "Ask of each pair: which delivery completes its sentence "
            "cleanly, and which one is broken or trailing? Does the "
            "line carry the story forward? Does it serve the direction "
            "the creator gave for this video (in the guidance, when any "
            "is given)? Would keeping both copies break the flow, one "
            "of them pulling attention away from the story? For each "
            "pair answer one verdict: cut_earlier (the first delivery "
            "is a failed try at the second; the second stays), "
            "cut_later (the first delivery is the complete one and the "
            "second is the failed restart), keep_both (the repetition "
            "is intentional: a refrain, an emphasis, a deliberate "
            "callback), or not_a_pair (the two are simply different "
            "lines). The creator's standing rule is that the last "
            "complete take wins unless the words say otherwise."
            + RETAKE_CALIBRATION
            + " One "
            "hard rule: a cut verdict requires a genuine retake, two "
            "tries at the same line. A line that merely fails to "
            "serve the direction is never cut on story grounds alone; "
            "answer keep_both for it, so a person reviews it instead. "
            "Return JSON "
            'only: {"verdicts": [{"pair_id": "...", "verdict": '
            '"cut_earlier" | "cut_later" | "keep_both" | "not_a_pair", '
            '"confidence": 0.0 to 1.0, "reason": "one short plain '
            'sentence"}]}. Answer every pair exactly once.'
        )
        user = json.dumps(
            {"pairs": packet, "guidance": guidance or []}
        )
        content = self._chat(
            SPARK_MODEL,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        parsed = extract_json(content)
        raw_verdicts = (
            parsed.get("verdicts") if isinstance(parsed, dict) else None
        )
        if not isinstance(raw_verdicts, list):
            return None
        by_id = {pair["pair_id"]: pair for pair in pairs}
        verdicts: dict = {}
        operations: list[dict] = []
        routing: list[dict] = []
        for item in raw_verdicts:
            if not isinstance(item, dict):
                continue
            pair_id = item.get("pair_id")
            verdict = item.get("verdict")
            pair = by_id.get(pair_id)
            if pair is None or verdict not in (
                "cut_earlier",
                "cut_later",
                "keep_both",
                "not_a_pair",
            ):
                continue
            verdicts[pair_id] = verdict
            if verdict not in ("cut_earlier", "cut_later"):
                continue
            spans = sorted(
                (pair["cut"], pair["keep"]),
                key=lambda span: span["word_start"],
            )
            target = spans[0] if verdict == "cut_earlier" else spans[1]
            try:
                confidence = float(item.get("confidence", 0.7))
            except (TypeError, ValueError):
                confidence = 0.7
            confidence = min(0.99, max(0.5, confidence))
            reason = str(item.get("reason") or "").strip()
            if not reason:
                reason = (
                    "A model judged this pair a retake and kept the "
                    "other delivery."
                )
            try:
                proposed = validate_operations(
                    [
                        {
                            "type": "cut_words",
                            "word_start": target["word_start"],
                            "word_end": target["word_end"],
                            "comment_id": pair_id,
                            "reason": reason,
                            "confidence": confidence,
                            "model": SPARK_MODEL,
                            "risk_tier": 1,
                            "wonder_reviewed": False,
                        }
                    ]
                )
            except SchemaError:
                continue
            comment = {
                "id": pair_id,
                "text": (
                    "Retake check. First delivery: "
                    f"\"{spans[0]['text']}\" Second delivery: "
                    f"\"{spans[1]['text']}\""
                ),
            }
            evidence = {
                "_timeline": timeline,
                "pair": {
                    "first_delivery": spans[0]["text"],
                    "second_delivery": spans[1]["text"],
                    "measured": packet[
                        [p["pair_id"] for p in packet].index(pair_id)
                    ]["measured"],
                },
            }
            for op in proposed:
                route = self._route(op, comment, evidence, operations, routing)
                routing.append(route)
        return operations, verdicts

    def judge_shadow(
        self,
        pairs: list[dict],
        timeline: Timeline,
        guidance: Optional[list] = None,
    ) -> Optional[dict]:
        """Best delivery (Beta), in shadow: pick keepers, change nothing.

        One Spark call for the whole video, like ``judge_retakes``.
        The pairs handed in are genuine choices only (both
        deliveries complete, never word-identical; identical twins
        are declared ties before any call). The model names the
        delivery it would keep per pair, or declares a tie; the
        caller only records the pick, so no span is ever drawn or
        applied from this answer. Returns a map of pair id to
        {"pick": "positional" | "other" | "tie", "reason": str},
        where "positional" names the delivery the positional rule
        kept, or None when no judgment could be made at all, so the
        caller falls back to the local ranking.
        """
        if not pairs:
            return {}
        words = timeline.words
        packet = []
        for pair in pairs:
            spans = sorted(
                (pair["cut"], pair["keep"]),
                key=lambda span: span["word_start"],
            )
            first, second = spans[0], spans[1]
            lo = max(0, first["word_start"] - 10)
            hi = min(len(words), second["word_end"] + 10)
            packet.append(
                {
                    "pair_id": pair["pair_id"],
                    "first_delivery": first["text"],
                    "second_delivery": second["text"],
                    "surrounding_words": " ".join(
                        str(words[i].get("word", "")) for i in range(lo, hi)
                    ),
                    "measured": {
                        "coverage": pair.get("coverage"),
                        "ratio": pair.get("ratio"),
                        "shape": pair.get("shape"),
                    },
                }
            )
        system = (
            "You are checking, for a video editor, which of two "
            "deliveries of the same line carries the story best. "
            "Both deliveries complete the sentence; a local checker "
            "measured them and found them similar. Judge by "
            "storytelling flow, not similarity alone: which delivery "
            "says the line in one clean run, carries the story "
            "forward, and serves the direction the creator gave for "
            "this video (in the guidance, when any is given)? A "
            "delivery that restarts inside the line, trails off, or "
            "leaves part of the line out is the weaker one. When the "
            "words cannot separate the two, answer tie; never invent "
            "a difference the words do not show, and never judge by "
            "which came first or last. You are only recording a "
            "pick; nothing is cut from your answer. For each pair "
            "answer one pick: keep_first, keep_second, or tie, with "
            "one short plain sentence of reason. Return JSON only: "
            '{"picks": [{"pair_id": "...", "pick": "keep_first" | '
            '"keep_second" | "tie", "reason": "one short plain '
            'sentence"}]}. Answer every pair exactly once.'
        )
        user = json.dumps(
            {"pairs": packet, "guidance": guidance or []}
        )
        content = self._chat(
            SPARK_MODEL,
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        parsed = extract_json(content)
        raw_picks = parsed.get("picks") if isinstance(parsed, dict) else None
        if not isinstance(raw_picks, list):
            return None
        by_id = {pair["pair_id"]: pair for pair in pairs}
        picks: dict = {}
        for item in raw_picks:
            if not isinstance(item, dict):
                continue
            pair = by_id.get(item.get("pair_id"))
            pick = item.get("pick")
            if pair is None or pick not in ("keep_first", "keep_second", "tie"):
                continue
            reason = str(item.get("reason") or "").strip()
            if pick == "tie":
                picks[pair["pair_id"]] = {
                    "pick": "tie",
                    "reason": reason or (
                        "The words could not separate these two, "
                        "so the last take stays."
                    ),
                }
                continue
            spans = sorted(
                (pair["cut"], pair["keep"]),
                key=lambda span: span["word_start"],
            )
            chosen = spans[0] if pick == "keep_first" else spans[1]
            picks[pair["pair_id"]] = {
                "pick": (
                    "positional" if chosen is pair["keep"] else "other"
                ),
                "reason": reason
                or "This delivery carries the story better.",
            }
        return picks

    def _route(
        self,
        op: Operation,
        comment: dict,
        evidence: dict,
        operations: list[dict],
        routing: list[dict],
    ) -> dict:
        timeline = evidence["_timeline"]
        needs_wonder = required_tier(op, timeline) >= 2
        record = {
            "comment_id": op.comment_id,
            "route": "spark",
            "model": SPARK_MODEL,
            "requires_wonder": needs_wonder,
        }
        if not needs_wonder or op.wonder_reviewed:
            operations.append(op.to_dict())
            return record
        record["route"] = "wonder"
        record["model"] = WONDER_MODEL
        if not self.allow_wonder:
            record["wonder_escalation"] = "disabled_recorded"
            operations.append(
                {
                    "type": "flag_for_review",
                    "comment_id": op.comment_id,
                    "reason": (
                        "requires_wonder_review: high-risk proposal held "
                        "because Wonder escalation is disabled"
                    ),
                    "confidence": op.confidence,
                    "model": SPARK_MODEL,
                    "risk_tier": 3,
                    "wonder_reviewed": False,
                }
            )
            return record
        reviewed = self._propose(
            WONDER_MODEL,
            comment,
            evidence,
            extra=(
                "Review this Spark proposal as the claim under review. "
                f"Proposal: {json.dumps(op.to_dict())}. Confirm it, correct "
                "it, or replace it with flag_for_review."
            ),
        )
        for reviewed_op in reviewed:
            data = reviewed_op.to_dict()
            data["wonder_reviewed"] = True
            data["risk_tier"] = max(data["risk_tier"], 2)
            operations.append(data)
        record["wonder_escalation"] = "completed"
        return record

    @staticmethod
    def _evidence(
        comment: dict,
        timeline: Timeline,
        profile: Optional[dict],
        overall: Optional[list] = None,
        edit_guidance: Optional[list] = None,
    ) -> dict:
        ts = comment.get("ts")
        mapping = None
        if ts is not None:
            try:
                mapping = timeline.map_output_time_approx(float(ts))
            except ValueError:
                mapping = None
        window: list[dict] = []
        if mapping and mapping.get("word_index") is not None:
            center = mapping["word_index"]
            for i in range(max(0, center - 15), min(len(timeline.words), center + 16)):
                word = timeline.words[i]
                window.append(
                    {
                        "index": i,
                        "word": word["word"],
                        "start": word["start"],
                        "end": word["end"],
                        "status": timeline.word_status(i),
                    }
                )
        return {
            "_timeline": timeline,
            "mapping": mapping,
            "timestamp_estimate": {
                "window_s": mapping.get("window_s") if mapping else None,
                "approximate": bool(mapping and mapping.get("approximate")),
                "note": (
                    "The pinned time is an estimate. The mapping was "
                    "resolved to the nearest words within a few seconds "
                    "either way; trust the words and the comment over "
                    "the exact second."
                ),
            },
            "words": window,
            "joins": timeline.joins(),
            "protected_spans": timeline.protected_spans(profile),
            "profile": profile or {},
            "overall_guidance": list(overall or []),
            "edit_guidance": list(edit_guidance or []),
        }
