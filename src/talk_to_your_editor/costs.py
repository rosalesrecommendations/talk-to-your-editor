"""What the editing costs: one module for every money figure.

Justin asked for this on October 27, 2026: every build estimates
what each feature costs, and the app itself shows the money,
visually, per feature and per job. This module is the single
place every one of those figures comes from: the rates, the
arithmetic, the formatting, the per-job cost record, the running
total on this Mac, and the before-it-runs estimate. Nothing else
in the app names a price.

Every dollar figure here is an ESTIMATE, and the copy the app
shows says so with the word "about". The rates below are derived
from the evidence in ``~/workspace/hackathon-build/
api-spend-ledger.md``; when Justin reads a new billing anchor,
correct the constants here and nowhere else.

## Where the rates come from

Muse Spark and Muse Wonder per-token prices are not published
in our records, so they are derived from the one real bill we
have, against the exact token counts the app now captures:

- The yardstick bill (Meta receipt #EP8Z6A2FB4, Oct 21, $14.28
  total) split as Muse Spark 1.3 $2.81 (19.7 percent) and Muse
  Wonder 1.0 $11.47 (80.3 percent). Of Wonder's share, $9.98
  was response (completion) tokens: an 87 percent completion
  share, applied to both models here as a documented
  assumption.
- The v0.7.0 build session captured exact usage: Spark 108,833
  prompt + 69,137 completion tokens; Wonder 1,113 prompt +
  4,308 completion; and the session's cost was estimated at
  $1.10 to $1.85. Applying the bill's model shares to the
  $1.475 midpoint, over those counts, gives Spark about $0.35
  per million prompt tokens and $3.65 per million completion
  tokens, and Wonder about $138 per million prompt and $239
  per million completion.
- Cross-check: one Day 30 eval attempt's model work (about
  28k Spark prompt + 23k Spark completion + 3k Wonder tokens)
  prices to about $0.77 at these rates, inside the ledger's
  own "under $0.90 even at the high blended rate" for the same
  work. Wonder is the cost driver, as the bill said.

The other two rates are published, not derived:

- ElevenLabs Scribe (the video transcription route): $0.22
  per audio hour, the published batch rate.
- Meta dictation (Muse Voice Transcribe): $3.00 per 1,000
  audio minutes. The ledger's measured calls agree exactly:
  45.04 seconds billed $0.00225 and 6.08 seconds $0.00030.

Local work is $0 and says so: faster-whisper transcription on
this computer, the deterministic filler pass, and the silence
pass. The also-noticed flags ride along on the direction call
that already ran, so their marginal cost is $0 too.

Hosted Spark routes (v0.8.1): a Spark-tier call may ride the
Baseten or Fireworks credit pools instead of the Meta balance.
Neither provider publishes per-token prices in our records, so
hosted calls price at the same derived Spark rates above, and
every figure stays labeled an estimate. What the route changes
is the POOL the estimate is charged to: each cost entry names
its pool (Meta, Baseten credits, Fireworks credits, ElevenLabs,
or this computer), the recap names a hosted pool on the line it
paid for, and the money view keeps the pools apart so each
pool's drain is visible on its own.

The retake check (v0.9.0, FLAG C): the published price of about
2 cents is the one Spark judgment call the pass makes, priced
from the typicals below. The roughly 97 cents a real job once
showed on that line was not that call: every judged cut used to
be escalated to Wonder, one review call per cut, and the
accounting folded those Wonder dollars into the retake line.
The pass now makes the one Spark call only (the safety check
holds any cut that needs a second review), and the accounting
splits any Wonder usage a pass does record onto the Second
review line, the way direction runs have always been split, so
no feature's line can silently carry another model's money.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Rates (estimates; see the module docstring for the derivation)
# ---------------------------------------------------------------------------

SPARK_USD_PER_M_PROMPT = 0.35
SPARK_USD_PER_M_COMPLETION = 3.65
WONDER_USD_PER_M_PROMPT = 138.0
WONDER_USD_PER_M_COMPLETION = 239.0

SCRIBE_USD_PER_AUDIO_HOUR = 0.22
DICTATION_USD_PER_1000_MINUTES = 3.00

# The features, in the order the money views list them, with
# the plain label the page shows.
FEATURES = (
    ("transcription", "Writing the words down"),
    ("retake_judge", "Retake check"),
    ("direction", "Reading your direction"),
    ("wonder", "Second review (Muse Wonder)"),
    ("flags", "Also noticed"),
    ("filler", "Taking out the ums and uhs"),
    ("silence", "Cutting the silence"),
    ("dictation", "Dictation"),
)
FEATURE_LABELS = dict(FEATURES)
FEATURE_ORDER = {key: index for index, (key, _label) in enumerate(FEATURES)}

# The pools a cost can be charged to (v0.8.1), in the order the
# money views list them, with the plain label the page shows.
# "meta" is the pay-as-you-go Meta balance the app has always
# estimated against; "baseten" and "fireworks" are the two $100
# hackathon partner credit pools; "elevenlabs" is the Scribe
# transcription route's own billing; "local" is work that ran
# on this computer and cost nothing.
POOLS = ("meta", "baseten", "fireworks", "elevenlabs", "local")
POOL_LABELS = {
    "meta": "Meta",
    "baseten": "Baseten credits",
    "fireworks": "Fireworks credits",
    "elevenlabs": "ElevenLabs",
    "local": "On this computer",
}
POOL_ORDER = {pool: index for index, pool in enumerate(POOLS)}
# The suffix a recap line carries when a hosted pool paid for
# the feature. Meta lines keep the wording they have always had.
POOL_SUFFIX = {
    "baseten": " (Baseten credits)",
    "fireworks": " (Fireworks credits)",
}

COSTS_FILENAME = "costs.json"
MONEY_DIR_NAME = ".talk-to-your-editor"
MONEY_FILENAME = "money.json"

# Typical token counts for one model feature on one run, from
# the ledger's observed live runs (the Day 30 eval passes ran
# on a 591-second video). The before-it-runs estimate falls
# back to these, scaled by the new video's length, whenever
# the Mac's own history has nothing for the feature yet.
REFERENCE_DURATION_S = 591.0
TYPICAL_TOKENS = {
    # The Band B retake judge: one Spark call per video.
    "retake_judge": (3450, 6350),
    # The direction pass: one whole-transcript Spark call.
    "direction": (24700, 12600),
    # The batched Wonder escalation inside a direction run.
    "wonder": (560, 2150),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Pricing captured work
# ---------------------------------------------------------------------------


def token_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """The estimated dollars for one captured model call.

    A model name carrying "wonder" prices at the Wonder rates;
    every other model the app calls (Muse Spark on Meta, Muse
    Spark Open on Baseten or Fireworks) prices at the Spark
    rates. The route a call rode decides the pool it is charged
    to, never the rate.
    """
    if "wonder" in str(model or "").lower():
        per_m_prompt = WONDER_USD_PER_M_PROMPT
        per_m_completion = WONDER_USD_PER_M_COMPLETION
    else:
        per_m_prompt = SPARK_USD_PER_M_PROMPT
        per_m_completion = SPARK_USD_PER_M_COMPLETION
    return (
        prompt_tokens * per_m_prompt + completion_tokens * per_m_completion
    ) / 1_000_000.0


def _usage_entry_cost(entry) -> tuple:
    """(usd, is_wonder) for one captured usage entry, or None
    when the entry is too malformed to price."""
    if not isinstance(entry, dict):
        return None
    try:
        prompt = int(entry.get("prompt_tokens") or 0)
        completion = int(entry.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        return None
    cost = token_cost(entry.get("model"), prompt, completion)
    return cost, "wonder" in str(entry.get("model") or "").lower()


def split_usage_cost(usage) -> tuple:
    """(spark_usd, wonder_usd) for a list of captured usage entries.

    Usage entries are the provider's own records: {model,
    prompt_tokens, completion_tokens}. Anything malformed prices
    as zero rather than failing the accounting.
    """
    spark = 0.0
    wonder = 0.0
    for entry in usage or []:
        priced = _usage_entry_cost(entry)
        if priced is None:
            continue
        cost, is_wonder = priced
        if is_wonder:
            wonder += cost
        else:
            spark += cost
    return spark, wonder


def split_usage_cost_by_route(usage) -> dict:
    """{route: (spark_usd, wonder_usd)} for captured usage entries.

    Usage entries recorded since v0.8.1 name the route that
    served the call; older entries, and any without a usable
    route, belong to "meta", the only route there was. Wonder
    usage is always on the Meta route, whatever an entry claims:
    Wonder exists nowhere else.
    """
    by_route: dict = {}
    for entry in usage or []:
        priced = _usage_entry_cost(entry)
        if priced is None:
            continue
        cost, is_wonder = priced
        route = entry.get("route") if isinstance(entry, dict) else None
        if is_wonder or route not in POOLS:
            route = "meta"
        spark, wonder = by_route.get(route, (0.0, 0.0))
        if is_wonder:
            wonder += cost
        else:
            spark += cost
        by_route[route] = (spark, wonder)
    return by_route


def transcription_cost(route: str, duration_s: float) -> float:
    """The estimated dollars for writing one video's words down.

    Scribe prices per audio second at its published hourly
    rate; the local route runs on this computer and is free.
    """
    if route == "scribe":
        return max(0.0, float(duration_s or 0.0)) * (
            SCRIBE_USD_PER_AUDIO_HOUR / 3600.0
        )
    return 0.0


def dictation_cost(duration_s: float) -> float:
    """The estimated dollars for one dictated note, at Meta's
    published $3.00 per 1,000 audio minutes."""
    return max(0.0, float(duration_s or 0.0)) * (
        DICTATION_USD_PER_1000_MINUTES / 60_000.0
    )


# ---------------------------------------------------------------------------
# Formatting (the UI's words for money; estimates say "about")
# ---------------------------------------------------------------------------


def format_usd(usd: float) -> str:
    """One money figure in the app's plain words.

    Zero is stated plainly ("0 cents": the work ran on this
    computer). Anything estimated carries "about": under half
    a cent reads "about 0 cents", a cent or two rounds to whole
    cents, and a dollar or more reads as dollars.
    """
    usd = float(usd or 0.0)
    if usd <= 0.0:
        return "0 cents"
    cents = usd * 100.0
    if cents < 0.5:
        return "about 0 cents"
    if cents < 1.5:
        return "about 1 cent"
    if cents < 99.5:
        return f"about {round(cents):.0f} cents"
    return f"about ${usd:,.2f}"


def cost_line(label: str, usd: float) -> str:
    """One recap line: the feature, and what it cost."""
    return f"{label}: {format_usd(usd)}."


# ---------------------------------------------------------------------------
# Cost entries and the per-job record (tye-session/costs.json)
#
# An entry is {at, feature, usd, detail}. The writers in the
# web app build them from the passes' own records (captured
# token usage, the transcription route and duration, a
# dictation's length). Like the usual store, this accounting
# never raises: a failed write loses a line of bookkeeping,
# never an edit.
# ---------------------------------------------------------------------------


def derive_pool(feature: str, usd: float) -> str:
    """The pool an entry belongs to when it does not name one.

    Records written before pools existed were all charged the
    way the app worked then: model features to Meta, paid
    transcription to ElevenLabs, and the deterministic passes
    to this computer, which is free either way.
    """
    if feature == "transcription":
        return "elevenlabs" if usd > 0 else "local"
    if feature in ("filler", "silence"):
        return "local"
    return "meta"


def entry_pool(entry: dict) -> str:
    """One cost entry's pool, named or derived."""
    pool = entry.get("pool")
    if pool in POOLS:
        return pool
    return derive_pool(entry.get("feature"), float(entry.get("usd") or 0.0))


def row_label(feature: str, pool: str) -> str:
    """The recap label for one feature charged to one pool: the
    feature's own label, with a hosted pool named in parentheses."""
    return FEATURE_LABELS[feature] + POOL_SUFFIX.get(pool, "")


def make_entry(
    feature: str, usd: float, detail: str = "", duration_s=None, pool=None
) -> dict:
    entry = {
        "at": _utc_now(),
        "feature": feature,
        "usd": float(usd or 0.0),
        "detail": detail,
    }
    if pool in POOLS:
        entry["pool"] = pool
    if duration_s:
        entry["duration_s"] = float(duration_s)
    return entry


def _clean_entries(raw) -> list:
    entries = []
    if not isinstance(raw, list):
        return entries
    for item in raw:
        if not isinstance(item, dict):
            continue
        feature = item.get("feature")
        if feature not in FEATURE_LABELS:
            continue
        try:
            usd = float(item.get("usd") or 0.0)
        except (TypeError, ValueError):
            continue
        entry = {
            "at": str(item.get("at") or ""),
            "job": str(item.get("job") or ""),
            "feature": feature,
            "usd": usd,
            "detail": str(item.get("detail") or ""),
            "pool": (
                item.get("pool")
                if item.get("pool") in POOLS
                else derive_pool(feature, usd)
            ),
        }
        try:
            duration = float(item.get("duration_s") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration > 0:
            entry["duration_s"] = duration
        entries.append(entry)
    return entries


def _read_entries(path: Path) -> list:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(payload, dict):
        return _clean_entries(payload.get("entries"))
    return _clean_entries(payload)


def _write_entries(path: Path, entries: list) -> None:
    """Write the entries file, atomically where the folder allows.

    The folder is made private the way the app keeps its other
    home files, and the write is a temp file replaced over the
    real one, so a stopped write can never leave a half-written
    record behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"entries": entries}, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def append_session_entries(session_dir: Path, entries: list) -> None:
    """Add entries to one job session's cost record. Never raises."""
    if not entries:
        return
    try:
        path = Path(session_dir) / COSTS_FILENAME
        existing = _read_entries(path) if path.is_file() else []
        _write_entries(path, existing + list(entries))
    except OSError:
        pass


def load_session_entries(session_dir: Path) -> list:
    """One job session's recorded cost entries (never raises)."""
    path = Path(session_dir) / COSTS_FILENAME
    if not path.is_file():
        return []
    return _read_entries(path)


# ---------------------------------------------------------------------------
# The running total on this Mac (~/.talk-to-your-editor/money.json)
# ---------------------------------------------------------------------------


def _money_path() -> Path:
    """The money file, resolved at call time (the usual's rule,
    so a test can point the home folder somewhere temporary)."""
    return Path.home() / MONEY_DIR_NAME / MONEY_FILENAME


def append_money_entries(entries: list, job_label: str) -> None:
    """Mirror entries into the Mac-wide running total. Never raises."""
    if not entries:
        return
    try:
        path = _money_path()
        existing = _read_entries(path) if path.is_file() else []
        stamped = [
            {**entry, "job": str(job_label or "")} for entry in entries
        ]
        _write_entries(path, existing + stamped)
    except OSError:
        pass


def load_money_entries() -> list:
    """Every entry in the Mac-wide running total (never raises)."""
    path = _money_path()
    if not path.is_file():
        return []
    return _read_entries(path)


def reset_money() -> None:
    """Clear the running total. The jobs' own records stay."""
    try:
        _write_entries(_money_path(), [])
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Views: per-feature lines and totals, for the recap and the cards
# ---------------------------------------------------------------------------


def _by_feature(entries: list) -> list:
    """[{feature, pool, label, usd, text}] for the features
    present, in canonical order, with each feature's total per
    pool. A feature that ran on two pools gets a line per pool,
    so the recap names which pool paid for which part."""
    totals: dict = {}
    for entry in entries:
        key = (entry["feature"], entry_pool(entry))
        totals[key] = totals.get(key, 0.0) + entry["usd"]
    rows = []
    for feature, pool in sorted(
        totals, key=lambda k: (FEATURE_ORDER[k[0]], POOL_ORDER[k[1]])
    ):
        usd = totals[(feature, pool)]
        rows.append(
            {
                "feature": feature,
                "pool": pool,
                "label": row_label(feature, pool),
                "usd": usd,
                "text": format_usd(usd),
            }
        )
    return rows


def _by_pool(entries: list) -> list:
    """[{pool, label, usd, text}] for the pools present, in
    canonical order, with each pool's total."""
    totals: dict = {}
    for entry in entries:
        pool = entry_pool(entry)
        totals[pool] = totals.get(pool, 0.0) + entry["usd"]
    return [
        {
            "pool": pool,
            "label": POOL_LABELS[pool],
            "usd": totals[pool],
            "text": format_usd(totals[pool]),
        }
        for pool in sorted(totals, key=lambda p: POOL_ORDER[p])
    ]


def job_cost_block(entries: list) -> Optional[dict]:
    """One job's costs: per-feature lines, a total, recap text.

    Returns None when the job has no recorded costs, so the
    page can simply not show a block.
    """
    if not entries:
        return None
    rows = _by_feature(entries)
    total = sum(entry["usd"] for entry in entries)
    lines = [cost_line(row["label"], row["usd"]) for row in rows]
    lines.append(f"This edit cost {format_usd(total)} in all.")
    return {
        "lines": rows,
        "total_usd": total,
        "total_text": format_usd(total),
        "recap_lines": lines,
    }


def money_view(session_entries, mac_entries: list) -> dict:
    """The money payload every summary carries.

    ``job`` is the loaded job's own block (None when there is
    no job, or the job has no recorded costs yet); ``mac`` is
    the running total on this Mac, broken down by feature,
    with the counts of jobs and runs it covers.
    """
    job_block = (
        job_cost_block(session_entries) if session_entries else None
    )
    rows = _by_feature(mac_entries)
    total = sum(entry["usd"] for entry in mac_entries)
    jobs = {entry["job"] for entry in mac_entries if entry["job"]}
    return {
        "job": job_block,
        "mac": {
            "lines": rows,
            "pools": _by_pool(mac_entries),
            "total_usd": total,
            "total_text": format_usd(total),
            "jobs": len(jobs),
            "entries": len(mac_entries),
        },
    }


# ---------------------------------------------------------------------------
# The before-it-runs estimate
# ---------------------------------------------------------------------------


def _history(entries: list) -> dict:
    """What this Mac's recorded runs say a feature usually costs.

    Returns {feature: {"runs": n, "mean_usd": x}} over entries
    that actually spent something: offline mock runs record $0
    honestly, and they must not drag the estimate of a live run
    down to nothing. Transcription also gets a per-audio-minute
    mean from the entries that recorded a duration.
    """
    spent: dict = {}
    for entry in entries:
        if entry["usd"] > 0:
            spent.setdefault(entry["feature"], []).append(entry)
    history = {}
    for feature, rows in spent.items():
        history[feature] = {
            "runs": len(rows),
            "mean_usd": sum(r["usd"] for r in rows) / len(rows),
        }
    scribe_rows = [
        entry
        for entry in spent.get("transcription", [])
        if entry.get("duration_s")
    ]
    if scribe_rows:
        minutes = sum(float(r["duration_s"]) for r in scribe_rows) / 60.0
        if minutes > 0:
            history["transcription"]["usd_per_minute"] = (
                sum(r["usd"] for r in scribe_rows) / minutes
            )
    return history


def _typical_cost(feature: str, duration_s: float) -> float:
    """The fallback price for one model feature on one run:
    the documented typical token counts, scaled by how this
    video's length compares to the 591-second video the counts
    were observed on."""
    prompt, completion = TYPICAL_TOKENS[feature]
    scale = 1.0
    if duration_s and duration_s > 0:
        scale = float(duration_s) / REFERENCE_DURATION_S
    model = "muse-wonder-1.0" if feature == "wonder" else "muse-spark-1.3"
    return token_cost(model, prompt * scale, completion * scale)


def estimate_run(
    duration_s: float,
    surface: str,
    settings: dict,
    has_direction: bool,
    route: str = "local",
    history_entries: Optional[list] = None,
    spark_route: str = "meta",
) -> dict:
    """What one run will probably cost, before it runs.

    ``surface`` is "first_edit" (a dropped video: transcription
    is part of the run) or "front_door" (a job that already has
    its words). ``settings`` carries the Remove choices the
    creator has set. Recent-run history wins where the Mac has
    it; the documented typicals carry the rest. The returned
    lines are only the features this run would use.

    ``spark_route`` is the route Spark-tier work will ride: the
    hosted rates are the same derived Spark rates, so the route
    changes only which pool each Spark line is charged to, and
    the hosted pool is named on the line. Wonder is estimated
    on Meta whatever the Spark route: it exists nowhere else.
    """
    if spark_route not in ("meta", "baseten", "fireworks"):
        spark_route = "meta"
    settings = settings or {}
    history = _history(history_entries or [])
    duration_s = max(0.0, float(duration_s or 0.0))
    picked: dict = {}

    def price(feature: str) -> float:
        if feature in history:
            return history[feature]["mean_usd"]
        return _typical_cost(feature, duration_s)

    if surface == "first_edit":
        if route == "scribe":
            per_minute = history.get("transcription", {}).get(
                "usd_per_minute"
            )
            if per_minute is not None and duration_s > 0:
                picked["transcription"] = per_minute * (duration_s / 60.0)
            else:
                picked["transcription"] = transcription_cost(
                    route, duration_s
                )
        else:
            picked["transcription"] = 0.0
    if settings.get("remove_retakes"):
        picked["retake_judge"] = price("retake_judge")
    if has_direction:
        picked["direction"] = price("direction")
        picked["wonder"] = price("wonder")
        picked["flags"] = 0.0
    if settings.get("remove_fillers"):
        picked["filler"] = 0.0
    if settings.get("cut_silence"):
        picked["silence"] = 0.0

    def pool_of(feature: str) -> str:
        if feature == "transcription":
            return "elevenlabs" if route == "scribe" else "local"
        if feature in ("retake_judge", "direction", "flags"):
            return spark_route
        if feature == "wonder":
            return "meta"
        return "local"

    rows = []
    for feature in sorted(picked, key=lambda f: FEATURE_ORDER[f]):
        usd = picked[feature]
        note = ""
        if feature == "transcription" and route != "scribe":
            note = "on this computer"
        if feature == "flags":
            note = "rides along with the direction read"
        pool = pool_of(feature)
        rows.append(
            {
                "feature": feature,
                "pool": pool,
                "label": row_label(feature, pool),
                "usd": usd,
                "text": format_usd(usd),
                "note": note,
            }
        )
    total = sum(picked.values())
    opener = (
        "This first edit usually costs"
        if surface == "first_edit"
        else "This edit usually costs"
    )
    return {
        "lines": rows,
        "total_usd": total,
        "total_text": format_usd(total),
        "sentence": f"{opener} {format_usd(total)}.",
        "explanation": estimate_explanation(
            surface, settings, has_direction, route, spark_route
        ),
    }


def estimate_explanation(
    surface: str,
    settings: dict,
    has_direction: bool,
    route: str = "local",
    spark_route: str = "meta",
) -> str:
    """Why the estimate is what it is, in plain words (item 25).

    The number moves when the run's work changes, and the line
    under the button has to say what changed it: writing direction
    adds the direction read and its second review (the expensive
    part), the retake check is one reading of the measured pairs,
    and the words are written by ElevenLabs Scribe or on this
    computer. The sentence names only the parts this run would
    actually use, in the order they run, and names the hosted
    pool when Spark work rides one, so the figure's movement as
    the creator types, toggles, and picks a route is predictable.
    """
    settings = settings or {}
    parts = []
    if surface == "first_edit":
        if route == "scribe":
            parts.append(
                "writing the words down with ElevenLabs Scribe"
            )
        else:
            parts.append("writing the words down on this computer")
    if settings.get("remove_retakes"):
        parts.append("checking for retakes")
    if has_direction:
        parts.append(
            "reading your direction and a second review of the "
            "riskier changes"
        )
    if settings.get("remove_fillers"):
        parts.append("taking out the ums and uhs on this computer")
    if settings.get("cut_silence"):
        parts.append("cutting the silence on this computer")
    if not parts:
        return "Nothing that costs anything is turned on for this edit."
    text = "That covers " + ", ".join(parts) + "."
    if has_direction:
        text += (
            " The second review is the biggest part. Clear the "
            "direction box and the estimate drops to the checks "
            "alone."
        )
    if spark_route in POOL_SUFFIX:
        text += (
            " The reading work runs on "
            + POOL_LABELS[spark_route]
            + "; the second review always runs on Meta."
        )
    return text
