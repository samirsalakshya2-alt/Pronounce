"""M10 adaptive practice decisions: longitudinal state → the next practice step, with its evidence chain (pure).

    STABLE                                  → RETIRE                  stop spending practice time on it for now
    RETIRED                                 → WATCH_FOR_REGRESSION    monitored silently
    REGRESSED                               → CONTINUE_CURRENT_TARGET reopened: it reappeared across readings
    latest practice outcome REVERSE_DIRECTION → CHANGE_PRACTICE_METHOD practise both directions (overcorrection)
    latest practice outcome NO_TRANSFER     → MOVE_TO_FRESH_WORDS
    latest practice outcome TRANSFER, still occurring outside the practised context → MOVE_TO_NEW_CONTEXT
    practice outcomes CONTINUING ≥ 2 times  → CHANGE_PRACTICE_METHOD
    IMPROVING, or latest outcome TRANSFER   → REDUCE_PRIORITY
    PERSONAL_RECURRING / PERSISTENT, word-specific across sessions → CONTINUE_CURRENT_TARGET (unit: the context
                                              where it is concentrated, the word, or the sound)
    EMERGING, ARTICLE_BOUND, INSUFFICIENT   → INSUFFICIENT_HISTORY    keep reading; watched

INCREASE_CONTEXT_DIFFICULTY is defined but never emitted: the evidence gives no supported ordering of difficulty
(speed and sentence length showed no effect; stress is dictionary-only). There is no score and no top-N: every
pattern gets a decision; the active ones are ordered by an explicit lexicographic rule.
"""

from __future__ import annotations

from typing import Any

from pronunciation_lab.longitudinal.identity import parse

DECISIONS_VERSION = "m10-decisions.1"
DECISIONS = ("CHANGE_PRACTICE_METHOD", "MOVE_TO_FRESH_WORDS", "MOVE_TO_NEW_CONTEXT", "CONTINUE_CURRENT_TARGET",
             "INCREASE_CONTEXT_DIFFICULTY", "REDUCE_PRIORITY", "RETIRE", "WATCH_FOR_REGRESSION", "INSUFFICIENT_HISTORY")
ACTIVE_DECISIONS = ("CHANGE_PRACTICE_METHOD", "MOVE_TO_FRESH_WORDS", "MOVE_TO_NEW_CONTEXT", "CONTINUE_CURRENT_TARGET")
STATE_ORDER = ("REGRESSED", "PERSISTENT", "PERSONAL_RECURRING", "IMPROVING", "EMERGING", "STABLE", "RETIRED",
               "INSUFFICIENT_HISTORY")
ORDER_CRITERIA = ("decision", "state", "clear_sessions", "words", "clear", "pattern")
FLUENCY_LABEL = {"hesitation": "possible hesitation pauses inside phrases", "filler": "possible filler sounds",
                 "repetition": "possible repetitions or restarts"}


def label(pattern_id: str) -> str:
    p = parse(pattern_id)
    if p["kind"] == "fluency":
        return FLUENCY_LABEL.get(p["group"], p["group"])
    return f"/{p['expected']}/ heard as /{p['heard']}/"


def _times(n) -> str:
    return "once" if n == 1 else ("twice" if n == 2 else f"{n} times")


def _s(n) -> str:
    return "" if n == 1 else "s"


def about(x: float | None) -> str:
    if x is None:
        return "?"
    return str(int(round(x))) if x >= 10 else f"{round(x, 1):g}"


def state_text(ps: dict[str, Any], fluency: bool = False) -> str:
    st, t, later = ps["state"], ps["totals"], ps.get("later") or {}
    exp, obs, opp, ses = later.get("expected"), later.get("observed"), later.get("opportunities"), later.get("sessions")
    chances = (f"{opp} measurable sentence{_s(opp)}" if fluency else f"{opp} chance{_s(opp)}") if opp is not None else ""
    if st in ("INSUFFICIENT_HISTORY", "EMERGING") and ps["scope"] == "WORD_SPECIFIC":
        word = next(iter(ps["clear_words"]), "one word")
        if t["clear_articles"] >= 2:
            return f"Recurring in ‘{word}’ across {t['clear_articles']} different texts: specific to this word so far."
        return f"Appears limited to ‘{word}’ so far."
    if st in ("INSUFFICIENT_HISTORY", "EMERGING") and ps["scope"] == "ARTICLE_BOUND":
        return "Appears limited to the article tested so far."
    if st == "PERSONAL_RECURRING":
        return (f"Recurring across {t['clear_sessions']} readings of {t['clear_articles']} different texts"
                + (f", in {t['words']} words" if t.get("words") else "") + " — likely personal.")
    if st == "PERSISTENT":
        return (f"This remains one of your recurring patterns: {_times(obs)} in {chances} since it was "
                f"established, where your earlier rate predicts about {about(exp)}.")
    if st == "IMPROVING":
        return (f"Early evidence suggests it is occurring less often: {_times(obs) if obs else 'not observed'} in "
                f"{chances} across {ses} later readings, where your earlier rate predicts about {about(exp)}.")
    if st in ("STABLE", "RETIRED"):
        seen = "not observed" if not obs else f"observed {_times(obs)}"
        tail = " It is still monitored silently." if st == "RETIRED" else ""
        return (f"Stable for now: {seen} in {chances} across {ses} later readings, where your earlier "
                f"rate predicts about {about(exp)}. No longer recurring strongly enough to prioritise.{tail}")
    if st == "REGRESSED":
        return "This pattern had become stable, but it has started recurring again across several readings."
    if st == "EMERGING":
        return "Appearing across several recent readings, but there is not enough history yet to call it personal."
    return "Not enough history yet."


def _unit(pid: str, ps: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    p = parse(pid)
    if p["kind"] == "fluency":
        return {"kind": "fluency", "text": label(pid)}
    if ps["scope"] == "WORD_SPECIFIC" and ps["clear_words"]:
        w = next(iter(ps["clear_words"]))
        return {"kind": "word", "word": w, "text": f"the word ‘{w}’ (/{p['expected']}/)"}
    conc = (ctx or {}).get("concentrated") or []
    if conc:
        c = conc[0]
        return {"kind": "context", "dimension": c["dimension"], "value": c["value"],
                "text": f"/{p['expected']}/ {c['label']}"}
    return {"kind": "sound", "text": f"/{p['expected']}/ (heard as /{p['heard']}/)"}


def decide(pid: str, ps: dict[str, Any], ctx: dict[str, Any], outcomes: list[dict[str, Any]],
           word_min_articles: int = 2) -> dict[str, Any]:
    st, scope = ps["state"], ps["scope"]
    judged = [o for o in outcomes if o["outcome"] != "INSUFFICIENT_OUTCOME"]
    latest = judged[-1] if judged else None
    continuing = sum(1 for o in judged if o["outcome"] == "CONTINUING")
    unit = _unit(pid, ps, ctx)
    name = label(pid)
    if st == "STABLE":
        d, why = "RETIRE", "stable for now"
    elif st == "RETIRED":
        d, why = "WATCH_FOR_REGRESSION", "stable for now and monitored silently"
    elif st == "REGRESSED":
        d, why = "CONTINUE_CURRENT_TARGET", "it reappeared after being stable"
    elif latest and latest["outcome"] == "REVERSE_DIRECTION":
        d, why = "CHANGE_PRACTICE_METHOD", "the opposite direction rose after practice (possible overcorrection)"
    elif latest and latest["outcome"] == "NO_TRANSFER":
        d, why = "MOVE_TO_FRESH_WORDS", "less often in the practised material, but not in new words"
    elif latest and latest["outcome"] == "TRANSFER" and (latest.get("context_split") or {}).get("continuing_outside"):
        d, why = "MOVE_TO_NEW_CONTEXT", "less often in the practised context, still occurring outside it"
    elif continuing >= 2:
        d, why = "CHANGE_PRACTICE_METHOD", f"still occurring in new words after {continuing} practice rounds"
    elif st == "IMPROVING" or (latest and latest["outcome"] == "TRANSFER"):
        d, why = "REDUCE_PRIORITY", "occurring less often than before"
    elif st in ("PERSONAL_RECURRING", "PERSISTENT") or \
            (scope == "WORD_SPECIFIC" and ps["totals"]["clear_articles"] >= word_min_articles):
        d, why = "CONTINUE_CURRENT_TARGET", "recurring" + (" across texts" if scope != "WORD_SPECIFIC" else " in this word")
    else:
        d, why = "INSUFFICIENT_HISTORY", "not enough history yet"
    rec = {
        "RETIRE": f"Stop spending practice time on {name} for now; it stays monitored.",
        "WATCH_FOR_REGRESSION": f"No practice needed for {name}; it is monitored silently.",
        "CONTINUE_CURRENT_TARGET": f"Keep practising {unit['text']}.",
        "CHANGE_PRACTICE_METHOD": (f"Try a different method for {name}: practise both directions, listening and "
                                   "comparing the two sounds." if latest and latest["outcome"] == "REVERSE_DIRECTION"
                                   else f"Try a different method for {name}: listen and compare before reading."),
        "MOVE_TO_FRESH_WORDS": f"Practise {unit['text']} in new words, not only the practised sentences.",
        "MOVE_TO_NEW_CONTEXT": f"Practise {name} outside the context you practised.",
        "REDUCE_PRIORITY": f"Lower priority for {name} for now.",
        "INSUFFICIENT_HISTORY": "Keep reading; it is being watched.",
    }[d]
    return {"version": DECISIONS_VERSION, "pattern": pid, "decision": d, "active": d in ACTIVE_DECISIONS,
            "reason": why, "unit": unit, "recommendation": rec, "based_on_outcome": latest["practice_id"] if latest else None}


def order_key(item: dict[str, Any]) -> tuple:
    t = item["totals"]
    return (DECISIONS.index(item["decision"]["decision"]), STATE_ORDER.index(item["state"]) if item["state"] in STATE_ORDER else 9,
            -t["clear_sessions"], -t.get("words", 0), -t["clear"], item["pattern"])
