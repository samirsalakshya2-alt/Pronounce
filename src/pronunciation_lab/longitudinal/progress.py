"""M10 orchestrator: extracted evidence → the longitudinal result for one engine (pure, deterministic).

    records (source.py) ─▶ eligibility ─▶ evidence classes + word novelty (history.py) ─▶ noise floor (noise.py)
        ─▶ per-session fresh aggregates ─▶ per pattern: points ─▶ state fold (states.py) + context (context.py)
        ─▶ practice records, status and fresh-word outcomes (practice.py) ─▶ adaptive decisions (decisions.py)

Four levels are kept apart in the output: observation (`examples`, with exact playback), pattern (`totals`,
`words`, `context`), longitudinal state (`state`, `scope`, `transitions`, windows with expected counts) and practice
decision (`decision`). The result is validated (fails closed): every cited observation exists in the input, the
vocabulary is controlled, and nothing resembling a score, percentage or causal claim is present.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from pronunciation_lab.longitudinal import context as C
from pronunciation_lab.longitudinal import decisions as D
from pronunciation_lab.longitudinal import history as H
from pronunciation_lab.longitudinal import noise as N
from pronunciation_lab.longitudinal import practice as P
from pronunciation_lab.longitudinal import states as S
from pronunciation_lab.longitudinal.calibration import DEFAULT_LONGITUDINAL, LongitudinalCalibration
from pronunciation_lab.longitudinal.identity import IDENTITY_VERSION, contrast_group, link_target, parse, reverse_of
from pronunciation_lab.longitudinal.source import EXTRACTOR_VERSION

PROGRESS_VERSION = "m10.1"
FORBIDDEN = (r"fixed forever", r"\bcaused?\b", r"practice fixed", r"\bnative", r"%", r"\bconfiden", r"master",
             r"clinical", r"\bscore", r"\brank", r"streak", r"improved by", r"better than", r"\balways\b",
             r"\bguarantee", r"\bprove[sd]?\b", r"\bpercent")
FORBIDDEN_KEYS = ("score", "percent", "percentage", "confidence", "rank", "streak", "progress_score")


def _ref(reading: dict[str, Any], row: dict[str, Any], cls: str) -> dict[str, Any]:
    sid, aid = reading["session_id"], reading["attempt_id"]
    return {"observation": row["o"], "session_id": sid, "attempt_id": aid, "job_id": reading["job_id"],
            "segment_id": reading["segment_id"], "word": row["wd"], "play_ms": row["play"], "span_ms": row["span"],
            "timeline": "analysis_wav", "url": f"/api/sessions/{sid}/attempts/{aid}/audio", "evidence_class": cls,
            "recorded_at": reading["recorded_at"]}


def _default_engine(records: list[dict[str, Any]]) -> str | None:
    """The engine of the most recent eligible reading (as M9's pool), else of the most recent attempt."""
    el = [r for r in records if r["eligible"]] or [r for r in records if r["reading"]["engine"]]
    if not el:
        return None
    return max(el, key=lambda r: (r["reading"]["recorded_at"] or "", r["reading"]["reading_id"]))["reading"]["engine"]


def build_progress(records: list[dict[str, Any]], practice_records: list[dict[str, Any]] | None = None,
                   advice: list[dict[str, Any]] | None = None, *, engine: str | None = None,
                   cal: LongitudinalCalibration = DEFAULT_LONGITUDINAL, generated_at: str | None = None,
                   input_fingerprint: str | None = None) -> dict[str, Any]:
    practice_records = sorted(practice_records or [], key=lambda r: (r["created_at"], r["id"]))
    advice = sorted(advice or [], key=lambda a: (a.get("issued_at") or "", a.get("session_id") or ""))
    engines = sorted({r["reading"]["engine"] for r in records if r["eligible"] and r["reading"]["engine"]})
    engine = engine or _default_engine(records)
    designations = [(rec["created_at"], s["sentence_key"]) for rec in practice_records
                    for s in rec["material"]["sentences"]]
    designations += [(a["issued_at"], k) for a in advice for k in a.get("retest_sentence_keys", [])]
    classified = H.classify_readings(records, engine, designations) if engine else []
    noise = N.calibrate(classified, cal)
    fresh = H.aggregate(classified, "FRESH")
    refs = {row["o"]: _ref(r["reading"], row, r["cls"]) for r in classified for row in r["sounds"]}
    flu_refs = {f["o"]: _ref(r["reading"], {"o": f["o"], "wd": f.get("before"), "play": f["play"], "span": None}, r["cls"])
                for r in classified for f in r["fluency"]}
    practice_sessions = {rec["practice_session_id"] for rec in practice_records}

    patterns = set(H.tracked_patterns(fresh))
    for rec in practice_records:
        patterns |= set(rec["target"]["patterns"])
    statuses = {rec["id"]: P.status(rec, [r for r in records if r["reading"]["engine"] == engine]) for rec in practice_records}

    def points(pid):
        p = parse(pid)
        return H.fluency_points(fresh, p["group"]) if p["kind"] == "fluency" else H.sound_points(fresh, p["expected"], p["heard"])

    items = []
    for pid in sorted(patterns):
        p = parse(pid)
        fl = p["kind"] == "fluency"
        pts = points(pid)
        ps = S.fold(pts, cal, noise, fluency=fl)
        ctx = C.profile(pts, cal) if not fl else {"version": C.CONTEXT_VERSION, "dimensions": {}, "concentrated": [],
                                                    "supported": False}
        if ps["state"] in S.ACTIVE + ("STABLE", "RETIRED") and ctx["concentrated"] and ps["scope"] == "PERSONAL_RECURRING":
            ps["scope"] = "CONTEXT_SPECIFIC"
        outcomes = []
        for rec in practice_records:
            if pid in rec["target"]["patterns"] and not fl:
                rev = reverse_of(pid)
                rp = parse(rev)
                outcomes.append(P.outcome(rec, pid, pts, H.sound_points(fresh, rp["expected"], rp["heard"]), classified,
                                          statuses[rec["id"]], cal, noise))
        decision = D.decide(pid, ps, ctx, outcomes, cal.word_active_min_articles)
        clear_ids = [i for x in pts for i in x["clear_ids"]]
        rmap = flu_refs if fl else refs
        examples = [rmap[i] for i in reversed(clear_ids) if i in rmap and (rmap[i].get("play_ms") or fl)][:3]
        advised = [a for a in advice if pid in a.get("patterns", [])]
        items.append({
            "pattern": pid, "label": D.label(pid), "kind": p["kind"], "contrast_group": contrast_group(pid),
            "reverse": reverse_of(pid), "state": ps["state"], "scope": ps["scope"], "text": D.state_text(ps, fl),
            "transitions": ps["transitions"], "baseline": ps["baseline"], "later": ps["later"],
            "post_stable": ps["post_stable"], "totals": ps["totals"],
            "words": [{"word": w, "clear": n} for w, n in sorted(ps["clear_words"].items(), key=lambda kv: (-kv[1], kv[0]))],
            "context": ctx, "series": [{"session_id": x["session_id"], "time": x["time"], "opportunities": x["opportunities"],
                                        "clear": x["clear"], "ambiguous": x["ambiguous"]} for x in pts],
            "evidence_classes": H.class_counts(classified, p["expected"], p["heard"], p["group"]),
            "examples": examples, "clear_observations": clear_ids,
            "advice": {"times_advised": len(advised), "first": advised[0]["issued_at"] if advised else None,
                       "last": advised[-1]["issued_at"] if advised else None},
            "practice": [o["practice_id"] for o in outcomes], "outcomes": outcomes, "decision": decision,
        })
    items.sort(key=D.order_key)
    for k, it in enumerate(items):
        it["position"] = k + 1

    eligibility = Counter(r["reason"] or "eligible" for r in records if r["reading"]["engine"] == engine)
    legacy = sorted({r["reading"]["session_id"] for r in classified if r["cls"] == "PRACTICE"} - practice_sessions)
    result = {
        "version": PROGRESS_VERSION, "identity_version": IDENTITY_VERSION, "extractor_version": EXTRACTOR_VERSION,
        "states_version": S.STATES_VERSION, "calibration": cal.as_dict(), "engine": engine, "engines": {
            e: {"eligible_readings": sum(1 for r in records if r["eligible"] and r["reading"]["engine"] == e),
                "sessions": len({r["reading"]["session_id"] for r in records if r["eligible"] and r["reading"]["engine"] == e})}
            for e in engines},
        "engine_note": ("Histories are kept per engine and never pooled. OpenPronounce and raw Wav2Vec2 share one "
                        "acoustic model, so agreement between them is not independent confirmation."),
        "history": {"readings": len(classified), "fresh_readings": sum(1 for r in classified if r["cls"] == "FRESH"),
                    "fresh_sessions": len(fresh), "classes": dict(Counter(r["cls"] for r in classified)),
                    "articles": len({s.article_key for s in fresh})},
        "eligibility": dict(sorted(eligibility.items())), "noise": noise, "patterns": items,
        "next_practice": [it["pattern"] for it in items if it["decision"]["active"]],
        "practice": [{"record": rec, "status": statuses[rec["id"]],
                      "outcomes": [o for it in items for o in it["outcomes"] if o["practice_id"] == rec["id"]]}
                     for rec in practice_records],
        "legacy_practice_sessions": [{"session_id": s, "linked": False,
                                      "note": "practice session from before practice records; linked to its advice "
                                              "by title only, so it is never used for practice outcomes"}
                                     for s in legacy],
        "advice_history": advice, "generated_at": generated_at, "input_fingerprint": input_fingerprint,
    }
    result["depth"] = depth_note(result)
    issues = validate(result, set(refs) | set(flu_refs))
    result["integrity"] = {"ok": not issues, "issues": issues}
    if issues:   # fail closed: an invalid longitudinal result is never shown
        result.update(patterns=[], next_practice=[])
    return result


def depth_note(result: dict[str, Any]) -> str:
    n, a = result["history"]["fresh_sessions"], result["history"]["articles"]
    if n == 0:
        return "No eligible readings yet."
    if n < 3 or a < 2:
        return (f"{n} reading{'' if n == 1 else 's'} of {a} text{'' if a == 1 else 's'} so far: patterns are described "
                "per reading; recurring personal patterns need at least 3 readings of 2 different texts.")
    return f"{n} readings of {a} different texts so far."


def texts(result: dict[str, Any]):
    yield result.get("depth") or ""
    yield (result.get("noise") or {}).get("text") or ""
    for it in result.get("patterns", []):
        yield it["text"]
        yield it["decision"]["recommendation"]
        yield it["decision"]["reason"]
        for t in it["transitions"]:
            yield t["reason"]
        for o in it["outcomes"]:
            yield o["reason"]


def _keys(obj, out):
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _keys(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _keys(v, out)


def validate(result: dict[str, Any], known_observations: set[str]) -> list[str]:
    issues = []
    for t in texts(result):
        low = re.sub(r"‘[^’]*’", "‘…’", t).lower()
        for pat in FORBIDDEN:
            if re.search(pat, low):
                issues.append(f"forbidden wording ({pat}): {t[:70]}")
    keys: set[str] = set()
    _keys({k: v for k, v in result.items() if k != "calibration"}, keys)
    for k in FORBIDDEN_KEYS:
        if k in keys:
            issues.append(f"a longitudinal result carries no {k!r}")
    for it in result.get("patterns", []):
        for e in it["examples"]:
            if e["observation"] not in known_observations:
                issues.append(f"{it['pattern']}: an example is not a source observation")
        for i in it["clear_observations"]:
            if i not in known_observations:
                issues.append(f"{it['pattern']}: a counted observation is not a source observation")
                break
        if it["state"] not in S.LIFECYCLE or it["scope"] not in S.SCOPES:
            issues.append(f"{it['pattern']}: unknown state or scope")
        if it["decision"]["decision"] not in D.DECISIONS:
            issues.append(f"{it['pattern']}: unknown decision")
        for o in it["outcomes"]:
            if o["outcome"] not in P.OUTCOMES:
                issues.append(f"{it['pattern']}: unknown practice outcome")
        if it["state"] in ("STABLE", "RETIRED", "IMPROVING") and not it.get("later"):
            issues.append(f"{it['pattern']}: a change claim without its later window")
    order = [D.order_key(it) for it in result.get("patterns", [])]
    if order != sorted(order):
        issues.append("patterns are not in their deterministic order")
    return issues


def adapt_coaching(coaching: dict[str, Any] | None, progress: dict[str, Any]) -> dict[str, Any]:
    """M9 integration (presentation layer only; M9's own result is never changed): each recent-history action gets
    its longitudinal context, and an action whose every pattern is stable for now is not shown as a priority."""
    by = {it["pattern"]: it for it in progress.get("patterns", [])}
    coaching_engine = ((coaching or {}).get("pool") or {}).get("engine")
    if coaching_engine and progress.get("engine") and coaching_engine != progress["engine"]:
        by = {}   # never annotate one engine's advice with another engine's history
    out = []
    for k, a in enumerate((coaching or {}).get("actions") or []):
        link = link_target(a.get("target") or {})
        hist = [{"pattern": p, "label": by[p]["label"], "state": by[p]["state"], "scope": by[p]["scope"],
                 "text": by[p]["text"], "decision": by[p]["decision"]["decision"]} for p in link["patterns"] if p in by]
        stable = bool(hist) and len(hist) == len(link["patterns"]) and all(h["state"] in ("STABLE", "RETIRED") for h in hist)
        out.append({"index": k, "target_id": (a.get("target") or {}).get("target_id"), "patterns": link["patterns"],
                    "history": hist, "prioritised": not stable,
                    "note": "Stable for now in your history, so not prioritised." if stable else None})
    m9_patterns = {p for x in out for p in x["patterns"]}
    extra = [it["pattern"] for it in progress.get("patterns", []) if it["decision"]["active"]
             and it["decision"]["decision"] != "CONTINUE_CURRENT_TARGET" and it["pattern"] not in m9_patterns]
    return {"actions": out, "from_history": extra, "engine": progress.get("engine"),
            "coaching_engine": coaching_engine}
