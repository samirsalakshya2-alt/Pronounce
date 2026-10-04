"""M12 single-session reading summary (no cross-session or longitudinal analysis).

Inputs — one attempt per sentence, the latest that is eligible:

* analysed by the session's primary engine (primary job SUCCEEDED);
* not discarded and not marked for re-recording;
* pronunciation feedback safe to show (not withheld by M7, not a different sentence);
* identity MATCH or LIKELY_MATCH, or AMBIGUOUS explicitly kept by the user (reader/status.py).

Reading coverage is reported separately from feedback: a sentence can be recorded and identified while its
feedback is withheld (M7 boundary) or awaits the reader's decision (uncertain identity).

Everything else is listed under "not included", with the reason. The M4
pattern logic (`build_patterns`) is reused unchanged over the pooled M4
observations of those attempts (ids namespaced "<attempt_id>:<observation_id>"),
so classes keep their meaning, now within this reading. M5 candidates are
grouped by category and sound. Every example is a playback reference to one
exact attempt, job and window of its analysis WAV. No score, no ranking.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from pronunciation_lab.app.phoneme_patterns import build_patterns
from pronunciation_lab.app.practice import GUIDANCE_NOTE, guidance
from pronunciation_lab.reader import model as M
from pronunciation_lab.reader.status import attempt_status

SUMMARY_VERSION = "sum-3"  # sum-3: adds the M8 fluency aggregate
MAX_EXAMPLES = 3
SUMMARY_CAVEATS = [
    "This summarises one reading. It describes what the listening model heard, not a fixed trait.",
    "Both local listening models share one acoustic model; agreement between them is not independent confirmation.",
    "Expected sounds come from a US-English reference (eSpeak en-us); some differences may reflect that reference.",
    "Stress and rhythm are not analysed yet.",
]


def _reading(text: str) -> str:
    return text.replace("this recording", "this reading").replace("This recording", "This reading")


def eligibility(attempt: dict[str, Any], primary: dict[str, Any] | None, engine: str) -> str | None:
    """None if the attempt can be summarised, else the reason it is not (reader/status.py decision D)."""
    return attempt_status(attempt, primary, engine)["summary"]


def build_summary(store, session: dict[str, Any], article: dict[str, Any]) -> dict[str, Any]:
    sid, engine = session["id"], session["engine_default"]
    seg_index = {s["id"]: s["index"] for s in article["segments"]}
    attempts = [store.load_attempt(sid, a) for a in session["attempt_ids"]]
    by_segment: dict[str, list[dict[str, Any]]] = OrderedDict()
    for a in attempts:
        by_segment.setdefault(a["segment_id"], []).append(a)

    used, excluded, reading = [], [], {"recorded": 0, "identified": 0, "uncertain": 0, "different": 0, "unusable": 0,
                                       "feedback_withheld": 0, "feedback_withheld_containment": 0,
                                       "awaiting_decision": 0, "feedback_low_confidence": 0}
    for seg_id, atts in by_segment.items():
        chosen, reasons, statuses = None, [], []
        for a in reversed(atts):
            jobs = [store.load_job(sid, a["id"], j) for j in a["job_ids"]]
            primary = next((j for j in reversed(jobs) if j["kind"] == "primary"), None)
            st = attempt_status(a, primary, engine)
            statuses.append((a, st))
            if st["summary"] is None:
                chosen = (a, primary)
                break
            reasons.append(st["summary"])
        if chosen:
            used.append(chosen)
        else:
            excluded.append({"segment_id": seg_id, "sentence": seg_index[seg_id] + 1, "reason": reasons[0]})
        # the sentence's representative attempt: the one summarised, else the latest not set aside by the reader
        current = [x for x in statuses if x[0]["user_disposition"] not in ("discarded", "rerecord_requested")]
        rep_st = statuses[-1][1] if chosen else (current[0][1] if current else statuses[0][1])
        reading["recorded"] += any(st["recorded"] for _, st in statuses)
        reading[rep_st["identity_group"]] += 1
        reading["feedback_withheld"] += (not chosen) and rep_st["feedback"] == "withheld_boundary"
        reading["feedback_withheld_containment"] += (not chosen) and rep_st["feedback"] == "withheld_containment"
        reading["feedback_low_confidence"] += bool(chosen) and rep_st["analysis"] == "low_confidence"
        # feedback categories are exclusive: withheld feedback stays withheld even if the identity is decided
        reading["awaiting_decision"] += (not chosen) and rep_st["feedback"] not in ("withheld_boundary",
                                                                                "withheld_containment") \
            and rep_st["needs_decision"]

    observations, candidates, refs = [], [], {}
    for a, job in used:
        view = store.load_view(job)
        coach, red = view.get("coach") or {}, view.get("reduction") or {}
        sentence = seg_index[a["segment_id"]] + 1
        for o in coach.get("observations", []):
            nid = f"{a['id']}:{o['id']}"
            observations.append(o | {"id": nid, "sentence": sentence})
            if o.get("play_ms"):
                refs[nid] = M.playback_reference(a, job, o["play_ms"], o["span_ms"], "sound") | \
                    {"word": o["word"], "sentence": sentence, "word_play_ms": o.get("word_play_ms")}
        for c in red.get("candidates", []) if red.get("state") == "ok" else []:
            candidates.append((a, job, sentence, c))

    patterns, groups = build_patterns(observations) if observations else ([], [])
    by_obs = {o["id"]: o for o in observations}

    def first_sentence(p):
        return min(by_obs[i]["sentence"] for i in p["observation_ids"])

    pattern_out = []
    for p in sorted(patterns, key=first_sentence):
        examples = [refs[i] for i in p["observation_ids"] if i in refs][:MAX_EXAMPLES]
        pattern_out.append({
            "id": p["id"], "kind": p["kind"], "expected": p["expected"], "contrast": p["contrast"], "class": p["class"],
            "context": p.get("context"), "evidence_strength": p["evidence_strength"], "occurrences": p["occurrences"],
            "sentences": sorted({by_obs[i]["sentence"] for i in p["observation_ids"]}),
            "words": p["words"], "summary": _reading(p["summary"]), "reference_note": p.get("reference_note"),
            "heard_as_expected_elsewhere": p.get("heard_as_expected_elsewhere"),
            "examples": examples,
        })
    group_of = {pid: g["id"] for g in groups for pid in g["pattern_ids"]}
    group_out = [{"id": g["id"], "title": _reading(g["title"]), "pattern_ids": [p["id"] for p in pattern_out
                                                                              if group_of.get(p["id"]) == g["id"]]}
                 for g in groups]

    practise = []
    for p in pattern_out:
        if group_of.get(p["id"]) == "recurring" and p["kind"] == "contrast":
            g = guidance(p["expected"], p["contrast"])
            practise.append({"pattern_id": p["id"], "target": p["expected"], "contrast": p["contrast"],
                             "guidance": g, "guidance_note": GUIDANCE_NOTE if g else None, "examples": p["examples"]})

    reductions: dict[tuple[str, str], dict[str, Any]] = OrderedDict()
    for a, job, sentence, c in sorted(candidates, key=lambda x: x[2]):
        it = c["interpretation"]
        if it["category"] == "insufficient_evidence":
            continue
        key = (it["category"], c["expected"])
        entry = reductions.setdefault(key, {"category": it["category"], "label": it["label"], "expected": c["expected"],
                                            "occurrences": 0, "sentences": [], "explanations": OrderedDict(),
                                            "natural_connected_speech_possible": False, "examples": []})
        entry["occurrences"] += 1
        if sentence not in entry["sentences"]:
            entry["sentences"].append(sentence)
        for e in it["candidate_explanations"]:
            entry["explanations"][e["id"]] = e
        entry["natural_connected_speech_possible"] |= it["natural_connected_speech_possible"]
        if len(entry["examples"]) < MAX_EXAMPLES:
            entry["examples"].append(M.playback_reference(a, job, c["where"]["play_ms"], c["where"]["span_ms"], "sound")
                                     | {"word": c["where"]["word"], "sentence": sentence,
                                        "word_play_ms": c["where"].get("word_play_ms"),
                                        "evidence_strength": c["evidence_strength"]})
    reduction_out = [v | {"explanations": list(v["explanations"].values())} for v in reductions.values()]

    readable = [s for s in article["segments"] if s["readable"]]
    fluency = fluency_summary(store, used, seg_index)
    return {
        "version": SUMMARY_VERSION,
        "session_id": sid,
        "engine": engine,
        "built_at": M.now(),
        "session_rev": session["rev"],
        "coverage": {
            "sentences": len(readable),
            "read": len(by_segment),
            "included": len(used),
            "feedback_included": len(used),
            **reading,
            "not_included": sorted(excluded, key=lambda x: x["sentence"]),
            "sounds": sum(1 for o in observations if o["kind"] == "sound"),
            "consistent_with_expected": sum(1 for o in observations if o["kind"] == "sound" and o["type"] == "expected"),
        },
        "inputs": [{"attempt_id": a["id"], "job_id": j["id"], "segment_id": a["segment_id"],
                    "sentence": seg_index[a["segment_id"]] + 1, "target": (j.get("target_confirmation") or {}).get("state"),
                    "user_disposition": a["user_disposition"]} for a, j in used],
        "patterns": pattern_out,
        "groups": [g for g in group_out if g["pattern_ids"]],
        "practise": practise,
        "reductions": reduction_out,
        "fluency": fluency,
        "caveats": SUMMARY_CAVEATS,
    }


# M8 in the reading summary: counts of things to notice, what recurs across sentences, a few examples to
# hear, and the range of speech rates — never a score, a ranking or an average "fluency" figure.
FLUENCY_GROUPS = (("LONG_PAUSE", "long pause", "long pauses"),
                  ("PAUSE", "possible hesitation pause", "possible hesitation pauses"),
                  ("FILLER", "possible filler", "possible fillers"),
                  ("REPETITION", "possible repetition", "possible repetitions"),
                  ("RESTART", "possible restart", "possible restarts"),
                  ("FALSE_START", "possible false start", "possible false starts"),
                  ("OTHER_HESITATION", "possible hesitation", "possible hesitations"),
                  ("RATE_ANOMALY", "change of pace", "changes of pace"))
FLUENCY_EXAMPLES = 3
STRENGTH_ORDER = {"moderate": 0, "low": 1, "ambiguous": 2, "insufficient": 3}


def _fluency_group(o: dict[str, Any]) -> str:
    return "LONG_PAUSE" if o["type"] == "PAUSE" and o.get("classification") == "unusually_long" else o["type"]


def fluency_summary(store, used, seg_index) -> dict[str, Any]:
    """Aggregate the M8 layer of the attempts included in the summary (the same sentence-only analyses that
    M4/M5 use: a withheld or excluded attempt contributes nothing)."""
    counts: dict[str, int] = {}
    where: dict[str, set[int]] = {}
    examples, rates, analysed = [], [], 0
    for a, job in used:
        fl = store.load_view(job).get("fluency") or {}
        if fl.get("state") != "ok":
            continue
        analysed += 1
        sentence = seg_index[a["segment_id"]] + 1
        m = fl.get("metrics") or {}
        if m.get("rate_available") and m.get("speaking_rate") is not None:
            rates.append((m["speaking_rate"], sentence))
        for o in fl.get("observations") or []:
            if not o.get("notice"):
                continue
            g = _fluency_group(o)
            counts[g] = counts.get(g, 0) + 1
            where.setdefault(g, set()).add(sentence)
            examples.append((STRENGTH_ORDER.get(o["strength"], 9), -o["duration_ms"], sentence, a, job, o))
    examples.sort(key=lambda x: x[:3])
    names = {g: (one, many) for g, one, many in FLUENCY_GROUPS}
    kinds = [{"kind": g, "label": names[g][0] if counts[g] == 1 else names[g][1], "count": counts[g],
              "sentences": sorted(where[g])} for g, _, _ in FLUENCY_GROUPS if counts.get(g)]
    recurring = [k for k in kinds if len(k["sentences"]) >= 2]
    notice = sum(counts.values())
    out = {
        "engine_note": "From each included sentence's own analysis; continued speech after a sentence is never counted.",
        "sentences_analysed": analysed,
        "notice": notice,
        "sentences_with_notice": len(set().union(*where.values())) if where else 0,
        "kinds": kinds,
        "recurring": [{"kind": k["kind"], "label": k["label"], "sentences": k["sentences"]} for k in recurring],
        "examples": [M.playback_reference(a, job, o["playback"]["play_ms"], o["playback"]["span_ms"], "fluency")
                     | {"sentence": sentence, "label": o["label"], "observed": o["observed"], "strength": o["strength"],
                        "context_ms": o["playback"].get("context_ms")}
                     for _, _, sentence, a, job, o in examples[:FLUENCY_EXAMPLES]],
        "speech_rate": ({"min": min(r for r, _ in rates), "max": max(r for r, _ in rates), "sentences": len(rates),
                         "unit": "syllables per second"} if rates else None),
    }
    out["text"] = fluency_text(out)
    return out


def fluency_text(f: dict[str, Any]) -> str:
    if not f["sentences_analysed"]:
        return "No fluency evidence among the sentences included."
    n, s = f["notice"], f["sentences_analysed"]
    parts = [f"{n} fluency thing{'s' if n != 1 else ''} to notice in {f['sentences_with_notice']} of {s} "
             f"sentence{'s' if s != 1 else ''}" if n else f"Nothing stood out in the timing of {s} "
             f"sentence{'s' if s != 1 else ''}"]
    for r in f["recurring"]:
        parts.append(f"{r['label']} in {len(r['sentences'])} sentences")
    sr = f["speech_rate"]
    if sr:
        parts.append(f"speech rate {sr['min']:.1f} syllables per second" if abs(sr["max"] - sr["min"]) < 0.05 else
                     f"speech rate {sr['min']:.1f}–{sr['max']:.1f} syllables per second")
    return " · ".join(parts) + "."


def validate_summary(summary: dict[str, Any], store) -> list[str]:
    """Every input is eligible and every example points into an included attempt and its primary job."""
    issues = []
    included = {i["attempt_id"]: i for i in summary["inputs"]}
    for i in summary["inputs"]:
        a = store.load_attempt(summary["session_id"], i["attempt_id"])
        j = store.load_job(summary["session_id"], i["attempt_id"], i["job_id"])
        if eligibility(a, j, summary["engine"]) is not None:
            issues.append(f"{i['attempt_id']}: not eligible ({eligibility(a, j, summary['engine'])})")
        if j["kind"] != "primary":
            issues.append(f"{i['attempt_id']}: summarised from a {j['kind']} job")
    if len({i["segment_id"] for i in summary["inputs"]}) != len(summary["inputs"]):
        issues.append("more than one attempt per sentence")
    examples = [e for p in summary["patterns"] for e in p["examples"]] + \
               [e for r in summary["reductions"] for e in r["examples"]] + \
               [e for e in (summary.get("fluency") or {}).get("examples", [])]
    for e in examples:
        src = included.get(e["attempt_id"])
        if src is None or src["job_id"] != e["job_id"] or e["timeline"] != M.PLAYBACK_TIMELINE:
            issues.append(f"example from {e['attempt_id']} is not an included attempt's primary analysis")
        if not e["play_ms"] or e["play_ms"][0] >= e["play_ms"][1]:
            issues.append(f"example from {e['attempt_id']} has no exact playback window")
    text = " ".join([*(p["summary"] for p in summary["patterns"]), *(r["label"] for r in summary["reductions"]),
                     (summary.get("fluency") or {}).get("text", "")]).lower()
    for banned in ("score", "wrong", "incorrect", "worst", "best", "rank"):
        if banned in text:
            issues.append(f"judgemental wording ({banned})")
    return issues
