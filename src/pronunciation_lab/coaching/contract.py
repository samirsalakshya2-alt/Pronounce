"""M9 output contract — CoachingResult assembly and validation.

The result supports a concise coaching view (action, why, steps, a few examples, retest sentences) and a
detailed view (every measure, unit, comparison, absorption, exclusion and knowledge contribution).
validate_coaching enforces the frozen principles mechanically; a result that fails is never shown as
coaching (the engine fails closed).
"""

from __future__ import annotations

import re
from typing import Any

from pronunciation_lab.coaching import knowledge as K
from pronunciation_lab.coaching.calibration import Calibration
from pronunciation_lab.coaching.evidence import EXCLUSIONS, READING_EXCLUSIONS
from pronunciation_lab.coaching.targets import TARGET_KINDS, UNSUPPORTED, Target

CAVEATS = [
    "These actions come from your recent readings: they describe what the listening model heard, not a fixed trait.",
    "Both local listening models share one acoustic model; agreement between them is not independent confirmation.",
    "Expected sounds come from a US-English reference (eSpeak en-us); some differences may reflect that reference.",
    "This suggests what to practise; it does not measure improvement.",
]

NO_ACTION = {
    "history_too_small": ("There isn't enough evidence yet to recommend a specific practice target.",
                          "Read a few more sentences; recommendations need several readings."),
    "single_session": ("There isn't enough evidence yet to recommend a specific practice target.",
                       "Read again on another occasion: a pattern has to recur across sessions."),
    "only_emerging": ("Nothing recurs strongly enough yet to recommend a specific practice target.",
                      "Keep reading; some patterns are starting to appear but need more evidence."),
    "only_ambiguous": ("The evidence so far is ambiguous, so there is no specific practice target yet.",
                       "Keep reading; ambiguous decodes alone are never enough for a recommendation."),
    "only_excluded_kinds": ("What was heard differently so far is not something to practise (it may reflect the "
                            "reference accent, natural connected speech or function words).",
                            "Keep reading; nothing needs special practice from this evidence."),
    "contradictory": ("The evidence points in different directions, so there is no specific practice target yet.",
                      "Keep reading; more readings will show whether a pattern is real."),
    "no_trainable_intervention": ("A recurring pattern was found, but there is no concrete practice for it yet.",
                                  "Keep reading; more examples of your own will make a practice possible."),
    "audio_unavailable": ("A recurring pattern was found, but its recordings are not available to practise with.",
                          "Read the sentences again so there are recordings to listen to."),
    "nothing_recurring": ("Nothing recurs across your recent readings that needs specific practice.",
                          "Keep reading; this updates as you read."),
}

BANNED = ("wrong", "error", "incorrect", "mistake", "you can't", "you cannot", "you don't know", "don't know how",
          "can't hear", "cannot hear", "tongue", "lips", "jaw", "swallow", "chew", "score", "%", "rank",
          "improv", "progress", "better than", "will fix", "will solve", "guarantee", "bad ")


def _sound_list(t: Target) -> str:
    return ", ".join(f"/{s}/" for s in sorted({p for pair in t.pairs for p in pair}))


def why_text(t: Target) -> str:
    m = t.measures
    if t.kind == "FLUENCY":
        what = {"hesitation": "possible hesitation pauses inside phrases", "filler": "possible filler sounds",
                "repetition": "possible repetitions or restarts"}[t.group]
        return (f"In {m['conf_sessions']} sessions, {what} were noticed {m['confident']} times across "
                f"{m['conf_sentences']} sentences.")
    conf, counter = m["confident"], m["counter"]
    if t.kind == "CONTRAST":
        a, b = t.pairs[0]
        what = f"/{a}/ and /{b}/ were heard as each other" if t.two_way else f"/{a}/ was heard as /{b}/"
    elif t.kind == "SET":
        what = f"the sounds {_sound_list(t)} were heard as one another"
    elif t.kind == "CONDITIONED":
        from pronunciation_lab.coaching.targets import _COND
        what = f"{', '.join('/' + s + '/' for s in t.sounds)} was heard differently {_COND[t.condition][0]}"
    else:
        what = f"‘{t.word}’ was heard differently"
    across = f"in {m['conf_readings']} readings" if t.kind == "LEXICAL" else f"across {m['conf_words']} words"
    text = f"In {m['conf_sessions']} sessions, {what} {conf} times {across}."
    one = len(t.sounds) == 1
    if counter:
        subject = f"/{t.sounds[0]}/ was" if one else "These sounds were"
        text += f" {subject} heard as expected in {counter} other occurrences"
        text += ((", so you already produce it" if one else ", so you already produce them")
                 + "; the issue appears to be consistency." if counter >= conf else ".")
    if m.get("reach") and t.kind != "LEXICAL":
        text += f" {'It occurs' if one else 'They occur'} in about {round(m['reach'])} of every 100 words you read."
    return text


def target_dict(t: Target) -> dict[str, Any]:
    d = t.to_dict()
    fam = K.family(t.family_id) if t.family_id else None
    d["family_title"] = fam.title if fam else None
    return d


def action(t: Target, iv, steps: list[dict[str, Any]], rank: int, minutes: int, decided_by: list[dict],
           absorbed: list[dict], counter_ids: list[str]) -> dict[str, Any]:
    return {
        "rank_in_plan": rank,
        "action_text": iv.action_text,
        "time_minutes": minutes,
        "target": target_dict(t),
        "why": {"text": why_text(t), "origin": "counted", "measures": t.measures, "decided_by": decided_by,
                "absorbed": absorbed},
        "evidence_tier": t.tier,
        "counter_evidence": {"heard_as_expected": t.measures.get("counter", 0),
                             "examples": iv.counter_examples},
        "transfer": iv.transfer,
        "practice": {"trainability": iv.trainability, "steps": steps,
                     "guidance": [g.to_dict() for g in iv.guidance], "guidance_note": K.GUIDANCE_NOTE if iv.guidance else None,
                     "examples": iv.examples, "counter_examples": iv.counter_examples, "words": iv.words,
                     "retest_sentences": iv.retest},
        "supporting_unit_ids": list(t.unit_ids),
        "counter_unit_ids": counter_ids,
        "knowledge_contributions": [k.to_dict() for k in t.knowledge] + [g.to_dict() for g in iv.guidance],
        "consolidation_record": t.consolidation,
        "notes": t.notes,
        "caveats": [K.REFERENCE_NOTE] if any(K.reference_variant(*p) for p in t.pairs) else [],
    }


def no_action(code: str) -> dict[str, Any]:
    message, helps = NO_ACTION[code]
    return {"code": code, "message": message, "what_would_help": helps}


def not_assessed() -> list[dict[str, Any]]:
    return [dict(u) for u in UNSUPPORTED]


def _texts(result: dict[str, Any]):
    """Every user-facing sentence M9 writes (general-knowledge guidance is excluded: it is labelled knowledge)."""
    for a in result.get("actions", []):
        yield a["action_text"]
        yield a["why"]["text"]
        yield a["target"]["hypothesis"]
        yield a["transfer"].get("text", "")
        for s in a["practice"]["steps"]:
            yield s.get("text", "")
            yield s.get("fallback") or ""
        for n in a.get("notes", []):
            yield n
    if result.get("no_action"):
        yield result["no_action"]["message"]
        yield result["no_action"]["what_would_help"]


def _ref_ok(ref: dict[str, Any] | None) -> bool:
    if not ref:
        return False
    play = ref.get("play_ms")
    return (ref.get("timeline") == "analysis_wav" and bool(ref.get("session_id")) and bool(ref.get("attempt_id"))
            and isinstance(play, list) and len(play) == 2 and play[0] is not None and play[1] is not None
            and 0 <= play[0] < play[1] and bool(ref.get("url")))


def validate_coaching(result: dict[str, Any], units: dict[str, Any], cal: Calibration) -> list[str]:
    issues: list[str] = []
    actions = result.get("actions", [])
    if result.get("state") == "actions" and not actions:
        issues.append("state 'actions' without actions")
    if result.get("state") == "no_action" and (actions or not (result.get("no_action") or {}).get("code") in NO_ACTION):
        issues.append("a no-action result must have no actions and a known reason code")
    if len(actions) > cal.max_actions:
        issues.append(f"more than {cal.max_actions} actions")
    if sum(a["target"]["kind"] == "FLUENCY" for a in actions) > cal.max_fluency:
        issues.append("more than one fluency action")
    seen: set[str] = set()
    for a in actions:
        t = a["target"]
        tid = t["target_id"]
        if t["kind"] not in TARGET_KINDS:
            issues.append(f"{tid}: unsupported target kind {t['kind']!r}")
        if a["evidence_tier"] != "established" or t["tier"] != "established":
            issues.append(f"{tid}: an action must rest on established evidence")
        ids = a["supporting_unit_ids"]
        if seen & set(ids):
            issues.append(f"{tid}: shares observations with another action")
        seen |= set(ids)
        cited = [units.get(i) for i in ids + a["counter_unit_ids"]]
        if any(u is None for u in cited):
            issues.append(f"{tid}: cites an observation that is not in the pool")
            continue
        if any(u.exclusion for u in cited):
            issues.append(f"{tid}: cites an excluded observation")
        conf = sum(1 for i in ids if units[i].quality == "confident")
        if conf != a["why"]["measures"]["confident"] or len(a["counter_unit_ids"]) != a["why"]["measures"]["counter"]:
            issues.append(f"{tid}: counts are not recomputable from the cited observations")
        p = a["practice"]
        if not p["examples"] or not all(_ref_ok(e) for e in p["examples"] + p["counter_examples"]):
            issues.append(f"{tid}: an example has no exact playback")
        if not p["retest_sentences"] or not all(_ref_ok(s["ref"]) for s in p["retest_sentences"]):
            issues.append(f"{tid}: a retest sentence has no exact playback")
        if any(w["ref"] is not None and not _ref_ok(w["ref"]) for w in p["words"]):
            issues.append(f"{tid}: a practice word has an invalid playback")
        for e in p["examples"]:
            if e.get("unit_id") not in set(ids):
                issues.append(f"{tid}: an example is not one of its own observations")
        if a["transfer"].get("origin") != "hypothesised":
            issues.append(f"{tid}: expected transfer must be labelled hypothesised")
        if any(k.get("origin") != "knowledge" for k in a["knowledge_contributions"]):
            issues.append(f"{tid}: a knowledge contribution is not labelled as knowledge")
        if t.get("origin") != "inferred":
            issues.append(f"{tid}: a target must be labelled inferred")
    for text in _texts(result):
        low = _mask_user_words(text or "").lower()
        for b in BANNED:
            if b in low:
                issues.append(f"banned wording ({b.strip()!r}): {text[:80]}")
    for r in result.get("pool", {}).get("unit_exclusions", {}):
        if r not in EXCLUSIONS:
            issues.append(f"unknown exclusion reason {r!r}")
    for r in result.get("pool", {}).get("reading_exclusions", {}):
        if r not in READING_EXCLUSIONS:
            issues.append(f"unknown reading exclusion reason {r!r}")
    return issues



def _mask_user_words(text: str) -> str:
    """The user's own words (‘quoted’, or listed after 'for example') are not M9's wording."""
    text = re.sub(r"‘[^’]*’", "‘…’", text)
    return re.sub(r"\(for example [^)]*\)", "(for example …)", text)
