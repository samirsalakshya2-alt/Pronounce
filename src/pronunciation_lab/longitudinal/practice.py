"""M10 practice: explicit practice records, their measured status, and practice → fresh-word outcomes (pure).

A practice record is written once, when the user starts practice from an M9 action ("Read these now"). Advice
that was only displayed (coaching.json snapshots) is advice history, never practice. Status is derived from the
practice session's own recordings — never assumed from a click: not started / partly read / every practice
sentence read, with the first and last recording times. No practice duration is invented.

Outcome of one practice record for one of its patterns (ordinary FRESH readings only; never causal):

    INSUFFICIENT_OUTCOME  practice not started, no baseline before it, or after it fewer than 2 sessions / fewer than
                          3 predicted occurrences in unpractised words
    TRANSFER              in unpractised words the pattern occurred at most the improving share of what the
                          pre-practice rate predicts ("improvement was observed after practice")
    REVERSE_DIRECTION     as TRANSFER, but the opposite direction (B→A after practising A→B) rose at pattern level
                          (≥ 3 clear in ≥ 2 sessions and ≥ 2 words, at ≥ 2× its own pre-practice rate): overcorrection
    NO_TRANSFER           not decreased in unpractised words, but decreased in the practised material (later fresh
                          readings of practised words, or the practice session's own retest readings)
    CONTINUING            neither

Practised words = every word of the practice material. Retest readings inside the practice session are shown as
"during practice" and may only support NO_TRANSFER (a decision to move to fresh words); they never establish
transfer, improvement, retirement or a baseline.
"""

from __future__ import annotations

import re
from typing import Any

from pronunciation_lab.coaching.evidence import lexical_key, text_key
from pronunciation_lab.longitudinal.calibration import LongitudinalCalibration
from pronunciation_lab.longitudinal.history import OPPORTUNITY
from pronunciation_lab.longitudinal.identity import IDENTITY_VERSION, link_target, parse, reverse_of
from pronunciation_lab.longitudinal.states import window

PRACTICE_VERSION = "m10-practice.1"
OUTCOMES = ("TRANSFER", "NO_TRANSFER", "INSUFFICIENT_OUTCOME", "REVERSE_DIRECTION", "CONTINUING")


def material_words(sentences: list[str]) -> list[str]:
    words = set()
    for s in sentences:
        for tok in re.split(r"\s+", s or ""):
            k = lexical_key(tok)
            if k:
                words.add(k)
    return sorted(words)


def new_record(practice_id: str, created_at: str, session_id: str, article: dict[str, Any],
               action: dict[str, Any], advice: dict[str, Any]) -> dict[str, Any]:
    """The write-once record of one explicit practice start, linked to the M9 action it came from."""
    sentences = [s["text"] for s in article.get("segments", []) if s.get("readable")]
    link = link_target(action["target"])
    return {
        "version": PRACTICE_VERSION, "id": practice_id, "created_at": created_at,
        "practice_session_id": session_id, "article_id": article["id"], "source": "what_to_practise_now",
        "advice": {"coaching_generated_at": advice.get("generated_at"), "pool_fingerprint": advice.get("fingerprint"),
                   "m9_target_id": action["target"].get("target_id"), "action_text": action.get("action_text"),
                   "plan_position": action.get("rank_in_plan")},
        "target": link | {"identity_version": IDENTITY_VERSION},
        "reverse_patterns": sorted({r for p in link["patterns"] if (r := (reverse_of(p) if p.startswith("sub:") else None))}),
        "practice_type": "retest_sentences:" + str((action.get("practice") or {}).get("trainability")),
        "mode": None,   # careful / natural is not captured; never inferred
        "material": {"sentences": [{"text": s, "sentence_key": text_key(s)} for s in sentences],
                     "words": material_words(sentences)},
    }


def status(record: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Measured status of a practice record from its session's recordings (eligible or not)."""
    mine = [r for r in records if r["reading"]["session_id"] == record["practice_session_id"]]
    eligible = [r for r in mine if r["eligible"]]
    keys = {s["sentence_key"] for s in record["material"]["sentences"]}
    read = {r["reading"]["sentence_key"] for r in eligible} & keys
    times = sorted(r["reading"]["recorded_at"] for r in mine if r["reading"]["recorded_at"])
    completion = "not_started" if not mine else ("completed" if keys and read == keys else "partial")
    return {"recordings": len(mine), "eligible_recordings": len(eligible), "sentences": len(keys),
            "sentences_read": len(read), "completion": completion,
            "first_recording_at": times[0] if times else None, "last_recording_at": times[-1] if times else None,
            "duration": None}


def _split(points, words: set[str]):
    p_opps = p_clear = u_opps = u_clear = 0
    u_sessions = set()
    for p in points:
        for w, n in p["opps_by_word"].items():
            if w in words:
                p_opps += n
            else:
                u_opps += n
                if n:
                    u_sessions.add(p["session_id"])
        for w, n in p["clear_by_word"].items():
            if w in words:
                p_clear += n
            else:
                u_clear += n
    return p_opps, p_clear, u_opps, u_clear, len(u_sessions)


def _practice_readings(classified, record, expected, heard):
    opps = clear = 0
    ids = []
    for r in classified:
        if r["reading"]["session_id"] != record["practice_session_id"]:
            continue
        for s in r["sounds"]:
            if s["e"] == expected and s["q"] in OPPORTUNITY:
                opps += 1
                if s["q"] == "confident" and s["out"] == "heard_other" and s["h"] == heard:
                    clear += 1
                    ids.append(s["o"])
    return opps, clear, ids


def outcome(record: dict[str, Any], pattern: str, points: list[dict[str, Any]], reverse_points: list[dict[str, Any]],
            classified: list[dict[str, Any]], st: dict[str, Any], cal: LongitudinalCalibration,
            noise: dict[str, Any]) -> dict[str, Any]:
    p = parse(pattern)
    share = noise["effective"]["improve_max_share"]
    start = st["first_recording_at"] or record["created_at"]
    end = st["last_recording_at"] or record["created_at"]
    pre = [x for x in points if x["time"] < start]
    post = [x for x in points if x["time"] > end and x["session_id"] != record["practice_session_id"]]
    base = window(pre)
    rate = base["rate"] or 0.0
    words = set(record["material"]["words"])
    p_opps, p_clear, u_opps, u_clear, u_sessions = _split(post, words)
    r_opps, r_clear, r_ids = _practice_readings(classified, record, p["expected"], p["heard"])
    unpractised = {"opportunities": u_opps, "clear": u_clear, "sessions": u_sessions,
                   "expected": round(rate * u_opps, 2)}
    practised = {"opportunities": p_opps, "clear": p_clear, "expected": round(rate * p_opps, 2)}
    during = {"opportunities": r_opps, "clear": r_clear, "expected": round(rate * r_opps, 2), "clear_ids": r_ids,
              "evidence_class": "PRACTICE (retest; feedback only, never learning evidence)"}
    rpre, rpost = window([x for x in reverse_points if x["time"] < start]), \
        window([x for x in reverse_points if x["time"] > end])
    rev_rate_pre = rpre["rate"] or 0.0
    reverse = {"pattern": reverse_of(pattern), "pre_rate": rpre["rate"], "post_rate": rpost["rate"],
               "post_clear": rpost["clear"], "post_clear_sessions": rpost["clear_sessions"],
               "post_words": len(rpost["clear_words"])}
    reverse["rose"] = bool(rpost["clear"] >= cal.reverse_min_clear and rpost["clear_sessions"] >= cal.reverse_min_sessions
                           and reverse["post_words"] >= 2 and rpost["rate"] is not None
                           and rpost["rate"] >= cal.reverse_ratio * rev_rate_pre)
    decreased = unpractised["expected"] >= cal.outcome_min_expected and u_clear <= unpractised["expected"] * share
    practised_better = (practised["expected"] >= cal.outcome_practised_min_expected and p_clear <= practised["expected"] * share) \
        or (during["expected"] >= cal.outcome_practised_min_expected and r_clear <= during["expected"] * share)
    if st["completion"] == "not_started":
        result, reason = "INSUFFICIENT_OUTCOME", "practice was not started"
    elif not base["opportunities"] or base["clear"] == 0:
        result, reason = "INSUFFICIENT_OUTCOME", "no baseline before the practice"
    elif u_sessions < cal.outcome_min_sessions or unpractised["expected"] < cal.outcome_min_expected:
        result, reason = "INSUFFICIENT_OUTCOME", "not enough chances in unpractised words since the practice"
    elif decreased:
        result, reason = ("REVERSE_DIRECTION", "the opposite direction rose") if reverse["rose"] else \
            ("TRANSFER", "occurred less often in unpractised words than the earlier rate predicts")
    elif practised_better:
        result, reason = "NO_TRANSFER", "less often in the practised material, but not in new words"
    else:
        result, reason = "CONTINUING", "still occurring in new words at about the earlier rate"
    ctx = record["target"].get("context")
    outside = None
    if ctx in ("initial", "medial", "final") and result == "TRANSFER":
        i_o = sum(x["ctx_opps"].get("word_position", {}).get(ctx, 0) for x in post)
        i_c = sum(x["ctx_clear"].get("word_position", {}).get(ctx, 0) for x in post)
        o_o = sum(x["opportunities"] for x in post) - i_o
        o_c = sum(x["clear"] for x in post) - i_c
        exp_out = round(rate * o_o, 2)
        outside = {"context": ctx, "inside": {"opportunities": i_o, "clear": i_c},
                   "outside": {"opportunities": o_o, "clear": o_c, "expected": exp_out},
                   "continuing_outside": exp_out >= cal.outcome_min_expected and o_c > exp_out * share}
    return {"version": PRACTICE_VERSION, "practice_id": record["id"], "pattern": pattern, "outcome": result,
            "reason": reason, "baseline": {"sessions": base["sessions"], "opportunities": base["opportunities"],
                                          "clear": base["clear"], "rate": base["rate"]},
            "post_sessions": [x["session_id"] for x in post], "unpractised": unpractised, "practised": practised,
            "during_practice": during, "reverse": reverse, "context_split": outside}
