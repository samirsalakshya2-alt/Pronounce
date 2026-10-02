"""M5 — Reduction & Connected-Speech Coach: interpretation of the M4 observations.

Each candidate keeps one chain, in this order, and each step stays separately
visible:

    raw engine observation   what the recogniser decoded (copied from M4, unchanged)
    → evidence               recognition / contextual temporal slot / acoustic signals, plus context
    → interpretation         a *possible* category, with candidate explanations
    → evidence strength      qualitative: moderate / low / ambiguous / insufficient

Rules (all tested):

* Evidence comes in three observational streams — recognition, contextual
  temporal slot, acoustic. Connected-speech context is an *explanation*, never a
  stream. A category other than "insufficient evidence" needs signals from at
  least two streams. No single signal — "not detected", a short slot, low
  energy, low confidence or one substitution — can produce a reduction category.
* The temporal slot is context between neighbouring decoded sounds, never the
  duration of the target sound.
* A sound not decoded next to an identical decoded sound may be two sounds
  merged by the recogniser's decoding (OpenPronounce collapses repeated phones;
  CTC merges repeats without a blank between them). Such a candidate is
  *ambiguous* in a single engine; only the other engine's evidence can show
  whether separate sounds were decoded (see `engine_compare`).
* Evidence strength is never "high": audio alone cannot establish what the
  articulators did.
* A possible connected-speech reduction is not an error. No score, ranking or
  judgemental wording.
"""

from __future__ import annotations

import time
from typing import Any

from pronunciation_lab.app.connected_speech import PROCESSES, consistent, explanation
from pronunciation_lab.app.phoneme_coach import HIGH_EXPECTED, timing_ok
from pronunciation_lab.app.reduction_evidence import EVIDENCE_THRESHOLDS, MIN_COMPARABLES, measure, speaking_rate, _anchors
from pronunciation_lab.benchmark.schema import PronunciationResult

REDUCTION_VERSION = "m5.1"

STREAMS = ("recognition", "temporal_slot", "acoustic")
CATEGORIES = (
    "possible_substitution",
    "possible_weakening",
    "possible_compression",
    "possible_omission",
    "possible_coarticulation",
    "possible_connected_speech_reduction",
    "ambiguous",
    "insufficient_evidence",
)
EVIDENCE_STRENGTHS = ("moderate", "low", "ambiguous", "insufficient")
REDUCTION_PATTERNS = ("omission", "weakening", "compression")

CATEGORY_LABELS = {
    "possible_substitution": "Possible substitution",
    "possible_weakening": "Possible weakening",
    "possible_compression": "Possible compression",
    "possible_omission": "Possible omission",
    "possible_coarticulation": "Possible coarticulation",
    "possible_connected_speech_reduction": "Possible connected-speech reduction",
    "ambiguous": "Ambiguous reduction",
    "insufficient_evidence": "Monitor — insufficient evidence",
}
# Neutral display order: by kind of evidence, never by severity.
GROUP_ORDER = (
    "possible_connected_speech_reduction", "possible_coarticulation", "possible_omission", "possible_weakening",
    "possible_compression", "possible_substitution", "ambiguous", "insufficient_evidence",
)

SIGNAL_TEXT = {
    "omission": "not decoded, and the expected sound's posterior is below the plausibility level",
    "not_decoded_plausible": "not decoded, although the expected sound remains plausible in this region",
    "substitution": "a different sound clearly won the decode",
    "ambiguous_decode": "the decode is ambiguous between competing sounds",
    "weak_support": f"decoded as expected but with weak support (posterior below {HIGH_EXPECTED})",
    "shorter_slot": "its contextual temporal slot is shorter than every other occurrence of this sound here",
    "no_room": "the neighbouring decoded sounds leave at most one model frame for it",
    "lower_energy": "its relative energy is lower than every other decoded occurrence of this sound here",
    "voicing_not_found": "no voicing was found although this sound is normally voiced",
}
STREAM_OF = {
    "omission": "recognition", "not_decoded_plausible": "recognition", "substitution": "recognition",
    "ambiguous_decode": "recognition", "weak_support": "recognition",
    "shorter_slot": "temporal_slot", "no_room": "temporal_slot",
    "lower_energy": "acoustic", "voicing_not_found": "acoustic",
}

MERGE_NOTE = (
    "A neighbouring sound decoded as this sound (or its voiced/voiceless partner) touches this region. The "
    "recogniser's decoding can merge two such sounds into one (OpenPronounce collapses repeated phones), so "
    "this may be a decoding artifact rather than a reduction. Compare with the other engine before drawing a "
    "conclusion."
)
# Voicing partners: across a word boundary ("need to", "had to") the recogniser can
# decode both sounds as one repeated phone, which repeat-collapse then merges.
VOICING_PARTNERS = {a: b for x, y in (("t", "d"), ("p", "b"), ("k", "ɡ"), ("s", "z"), ("f", "v"), ("θ", "ð"),
                                      ("ʃ", "ʒ"), ("tʃ", "dʒ")) for a, b in ((x, y), (y, x))}
NOT_ERROR_NOTE = (
    "A possible reduction is not an error: reduced and merged sounds are a normal part of fluent connected speech."
)
REDUCTION_CAVEATS = [
    "The microphone gives acoustic evidence, not the articulatory gesture: the evidence can be consistent with "
    "a reduction without establishing that the sound was weakened, merged or left out.",
    "The contextual temporal slot is the time between neighbouring decoded sounds; it is not the duration of "
    "the sound.",
    "Comparisons are within this recording only; they need at least "
    f"{MIN_COMPARABLES} other occurrences of the same sound.",
    NOT_ERROR_NOTE,
    "OpenPronounce and the raw Wav2Vec2 recogniser share one acoustic model, so agreement between them is not "
    "independent confirmation.",
]

_BANNED = ("wrong", "incorrect", "mistake", "error in your", "bad pronunciation", "score", "worst", "best",
           "you failed", "your tongue", "your lips", "your mouth", "swallowed", "chewed", "proves", "definitely")


def _signals(o: dict[str, Any], m: dict[str, Any]) -> list[str]:
    sig: list[str] = []
    t = o["type"]
    if t == "omission_candidate":
        sig.append("omission")
    elif t == "weak_evidence":
        sig.append("not_decoded_plausible")
    elif t == "substitution_candidate":
        sig.append("substitution")
    elif t == "ambiguous":
        sig.append("ambiguous_decode")
    elif t == "expected" and o["expected_posterior"] is not None and o["expected_posterior"] < HIGH_EXPECTED:
        sig.append("weak_support")
    if m["temporal_slot"]["shorter_than_all_comparables"]:
        sig.append("shorter_slot")
    if m["temporal_slot"]["no_room"]:
        sig.append("no_room")
    if m["acoustic"]["lower_than_all_comparables"]:
        sig.append("lower_energy")
    if m["acoustic"]["voicing_not_found"]:
        sig.append("voicing_not_found")
    return sig


def _merge_suspect(o: dict[str, Any], neighbours: list[dict[str, Any]], m: dict[str, Any]) -> bool:
    """Not decoded, and an adjacent sound decoded as this phone (or its voicing partner) overlaps or touches it.

    That is what a recogniser merge of two identical sounds looks like from one
    engine (M2 "want to": OpenPronounce's single /t/ span for "to" starts where
    the /t/ of "want" was expected).
    """
    if o["observed"] is not None or m["decoded"] or not o["span_ms"]:
        return False
    frame = m["temporal_slot"]["ms_per_frame"]
    start, end = o["span_ms"]
    for n in neighbours:
        if n["timing_source"] == "engine" and n["span_ms"] \
                and n["observed"] in (o["expected"], VOICING_PARTNERS.get(o["expected"])) \
                and n["span_ms"][0] <= end + frame and n["span_ms"][1] >= start - frame:
            return True
    return False


def _pattern(signals: list[str]) -> str:
    if "omission" in signals:
        return "omission"
    if "not_decoded_plausible" in signals:
        return "weakening"
    if "substitution" in signals:
        return "substitution"
    if "ambiguous_decode" in signals:
        return "ambiguous"
    if "lower_energy" in signals or "voicing_not_found" in signals:
        return "weakening"
    if "shorter_slot" in signals or "no_room" in signals:
        return "compression"
    return "weak_support"  # recognition support only; never a reduction pattern on its own


def interpret(o: dict[str, Any], m: dict[str, Any], neighbours: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One candidate, or None when no stream shows anything worth listing."""
    signals = _signals(o, m)
    streams = [s for s in STREAMS if any(STREAM_OF[x] == s for x in signals)]
    if not streams:
        return None
    pattern = _pattern(signals)
    processes = m["context"]["processes"]
    matching = [p for p in processes if consistent(p, pattern, o["expected"], o["observed"])]
    merge = _merge_suspect(o, neighbours, m)
    reasons = [SIGNAL_TEXT[s] for s in signals]
    if merge:
        reasons.append(MERGE_NOTE)

    if len(streams) < 2:
        # One stream: listed only when it is the recogniser's own evidence and bears
        # on reduction. A within-recording rank signal alone (shortest slot, lowest
        # energy) is never listed: every set has a minimum, so one occurrence of each
        # frequent sound would always be flagged. Voicing alone is too noisy on short
        # spans. Substitutions and ambiguous decodes alone are M4's business unless a
        # connected-speech context predicts them.
        relevant = streams == ["recognition"] and (
            pattern in REDUCTION_PATTERNS or (pattern == "substitution" and bool(matching)))
        if not relevant:
            return None
        category, strength = "insufficient_evidence", "insufficient"
        reasons.append(f"only one kind of evidence ({streams[0].replace('_', ' ')}); at least two are needed")
    elif merge:
        category, strength = "ambiguous", "ambiguous"
    elif pattern == "ambiguous":
        category, strength = "ambiguous", "ambiguous"
    else:
        if pattern == "substitution":
            category = "possible_coarticulation" if matching else "possible_substitution"
        elif matching:
            category = "possible_connected_speech_reduction"
        else:
            category = f"possible_{pattern}"
        strength = "moderate" if len(streams) == 3 else "low"

    natural = bool(matching)
    return {
        "category": category,
        "label": CATEGORY_LABELS[category],
        "evidence_pattern": pattern,
        "signals": signals,
        "streams": streams,
        "evidence_strength": strength,
        "merge_suspect": merge,
        "candidate_explanations": [explanation(p) for p in matching],
        "contexts_present": [explanation(p) for p in processes],
        "natural_connected_speech_possible": natural,
        "reasons": reasons,
    }


def _summary(c: dict[str, Any]) -> str:
    e, w = c["expected"], c["where"]["word"]
    head = {
        "possible_substitution": f"Possible substitution of /{e}/ in '{w}'.",
        "possible_weakening": f"Possible weakening of /{e}/ in '{w}'.",
        "possible_compression": f"Possible compression of /{e}/ in '{w}'.",
        "possible_omission": f"Possible omission of /{e}/ in '{w}'.",
        "possible_coarticulation": f"Possible coarticulation of /{e}/ in '{w}'.",
        "possible_connected_speech_reduction": f"Possible connected-speech reduction of /{e}/ in '{w}'.",
        "ambiguous": f"Ambiguous evidence for /{e}/ in '{w}'.",
        "insufficient_evidence": f"/{e}/ in '{w}': one kind of evidence only — monitor.",
    }[c["interpretation"]["category"]]
    tail = ""
    cat = c["interpretation"]["category"]
    if cat == "insufficient_evidence" and c["interpretation"]["candidate_explanations"]:
        labels = ", ".join(e["label"] for e in c["interpretation"]["candidate_explanations"])
        tail = (f" A connected-speech context applies ({labels}), but one kind of evidence is not enough to "
                "interpret it.")
    elif c["interpretation"]["natural_connected_speech_possible"] and cat not in ("ambiguous", "insufficient_evidence"):
        tail = " The evidence is consistent with natural connected speech; it cannot determine whether the sound was fully articulated."
    elif c["interpretation"]["category"] not in ("insufficient_evidence", "ambiguous"):
        tail = " The acoustic evidence is consistent with this, but cannot establish what was articulated."
    return head + tail


def build_reduction(result: PronunciationResult, observations: list[dict[str, Any]]) -> dict[str, Any]:
    t0 = time.perf_counter()
    duration = result.recording.audio.duration_ms
    engine = {"id": result.engine.name, "model": result.engine.model, "phone_set": result.phone_set}
    sounds = [o for o in observations if o["kind"] == "sound"]
    measurements = measure(result, observations)
    rate = speaking_rate(_anchors(observations))

    candidates = []
    for i, o in enumerate(sounds):
        m = measurements.get(o["id"])
        if m is None:
            continue
        interp = interpret(o, m, [sounds[j] for j in (i - 1, i + 1) if 0 <= j < len(sounds)])
        if interp is None:
            continue
        candidates.append({
            "id": "r-" + o["id"],
            "observation_id": o["id"],
            "sound_index": o["sound_index"],
            "expected": o["expected"],
            "engine": engine,
            "where": {
                # the recording itself is the view's `source` (per-analysis ids stay out of evidence)
                "sentence": result.recording.target.text,
                "word": o["word"],
                "word_index": o["word_index"],
                "span_ms": o["span_ms"],
                "timing_source": o["timing_source"],
                "play_ms": o["play_ms"],
                "word_play_ms": o["word_play_ms"],
                "previous_phone": m["context"]["previous_phone"],
                "next_phone": m["context"]["next_phone"],
                "word_position": m["context"]["word_position"],
                "syllable_position": m["context"]["syllable_position"],
            },
            "raw_observation": {
                "engine": engine["id"],
                "observed": o["observed"],
                "alignment_operation": o["evidence"]["alignment_operation"],
                "expected_posterior": o["expected_posterior"],
                "observed_posterior": o["observed_posterior"],
                "nbest": o["nbest"][:3],
                "frame_span": o["evidence"]["frame_span"],
                "m4_type": o["type"],
                "m4_confidence": o["confidence"],
            },
            "evidence": {
                "temporal_slot": m["temporal_slot"],
                "acoustic": m["acoustic"],
                "context": m["context"],
            },
            "interpretation": interp,
            "evidence_strength": interp["evidence_strength"],
        })
    for c in candidates:
        c["summary"] = _summary(c)
        same = [x for x in candidates if x is not c and x["expected"] == c["expected"]]
        heard = [o for o in observations if o["kind"] == "sound" and o["expected"] == c["expected"]
                 and o["type"] == "expected" and o["id"] != c["observation_id"]]
        c["recurrence"] = {
            "same_category_elsewhere": [x["id"] for x in same
                                        if x["interpretation"]["category"] == c["interpretation"]["category"]],
            "heard_as_expected_elsewhere": len(heard),
        }

    groups = [{"id": g, "title": CATEGORY_LABELS[g],
               "candidate_ids": [c["id"] for c in sorted(candidates, key=lambda c: c["where"]["span_ms"][0])
                                 if c["interpretation"]["category"] == g]}
              for g in GROUP_ORDER]
    groups = [g for g in groups if g["candidate_ids"]]
    out = {
        "version": REDUCTION_VERSION,
        "state": "ok",
        "engine": engine,
        "thresholds": EVIDENCE_THRESHOLDS,
        "speaking_rate": {"phones_per_s_excluding_pauses": rate["phones_per_s_excluding_pauses"],
                          "pauses": rate["pauses"], "pause_ms_total": rate["pause_ms_total"]},
        "candidates": candidates,
        "groups": groups,
        "counts": {k: sum(c["interpretation"]["category"] == k for c in candidates) for k in CATEGORIES},
        "caveats": REDUCTION_CAVEATS,
    }
    issues = validate_reduction(out, observations, duration_ms=duration)
    out["integrity"] = {"ok": not issues, "issues": issues}
    out["timing_ms"] = (time.perf_counter() - t0) * 1000.0
    return out


def empty_reduction(state: str, engine: dict[str, Any]) -> dict[str, Any]:
    return {"version": REDUCTION_VERSION, "state": state, "engine": engine, "thresholds": EVIDENCE_THRESHOLDS,
            "speaking_rate": None, "candidates": [], "groups": [], "counts": {k: 0 for k in CATEGORIES},
            "caveats": REDUCTION_CAVEATS, "integrity": {"ok": True, "issues": []}, "timing_ms": 0.0}


def validate_reduction(red: dict[str, Any], observations: list[dict[str, Any]], *, duration_ms: float) -> list[str]:
    """Invariants for any reduction output. Returns violations."""
    issues: list[str] = []
    by_id = {o["id"]: o for o in observations}
    ids = [c["id"] for c in red["candidates"]]
    if len(ids) != len(set(ids)):
        issues.append("duplicate candidate ids")
    for c in red["candidates"]:
        cid, it = c["id"], c["interpretation"]
        o = by_id.get(c["observation_id"])
        if o is None:
            issues.append(f"{cid}: references unknown observation")
            continue
        if o["type"] == "not_interpreted":
            issues.append(f"{cid}: built from a not-interpreted observation")
        if c["engine"]["id"] != red["engine"]["id"] or c["raw_observation"]["engine"] != red["engine"]["id"] \
                or o["evidence"]["engine"]["id"] != red["engine"]["id"]:
            issues.append(f"{cid}: evidence from a different engine")
        raw = c["raw_observation"]
        if (raw["observed"], raw["expected_posterior"], raw["m4_type"], raw["alignment_operation"]) != \
                (o["observed"], o["expected_posterior"], o["type"], o["evidence"]["alignment_operation"]):
            issues.append(f"{cid}: raw observation differs from the engine evidence")
        if it["category"] not in CATEGORIES:
            issues.append(f"{cid}: unknown category {it['category']}")
        if c["evidence_strength"] not in EVIDENCE_STRENGTHS or c["evidence_strength"] != it["evidence_strength"]:
            issues.append(f"{cid}: unknown or inconsistent evidence strength")
        streams = {STREAM_OF[s] for s in it["signals"]}
        if set(it["streams"]) != streams:
            issues.append(f"{cid}: streams do not match signals")
        if it["category"] != "insufficient_evidence" and len(streams) < 2:
            issues.append(f"{cid}: {it['category']} from a single evidence stream")
        if (it["category"] == "insufficient_evidence") != (c["evidence_strength"] == "insufficient"):
            issues.append(f"{cid}: insufficient evidence must be labelled insufficient")
        if (it["category"] == "ambiguous") != (c["evidence_strength"] == "ambiguous"):
            issues.append(f"{cid}: ambiguous must be labelled ambiguous")
        if c["evidence_strength"] == "moderate" and len(streams) < 3:
            issues.append(f"{cid}: moderate evidence needs all three streams")
        if it["category"] == "possible_omission" and "omission" not in it["signals"]:
            issues.append(f"{cid}: omission without recognition evidence")
        if it["merge_suspect"] and it["category"] not in ("ambiguous", "insufficient_evidence"):
            issues.append(f"{cid}: possible decoding merge not kept ambiguous")
        if it["category"] in ("possible_connected_speech_reduction", "possible_coarticulation") \
                and not it["candidate_explanations"]:
            issues.append(f"{cid}: connected-speech category without a candidate explanation")
        if it["natural_connected_speech_possible"] and not it["candidate_explanations"]:
            issues.append(f"{cid}: natural connected speech claimed without a context")
        for ex in it["candidate_explanations"]:
            if ex["id"] not in PROCESSES:
                issues.append(f"{cid}: unknown connected-speech process {ex['id']}")
        # traceable location and exact playback (the M4 window, unshifted)
        w = c["where"]
        if w["play_ms"] != o["play_ms"] or w["span_ms"] != o["span_ms"] or w["word_play_ms"] != o["word_play_ms"]:
            issues.append(f"{cid}: playback/location differs from the observation")
        span, play = w["span_ms"], w["play_ms"]
        if not span or not play or not timing_ok(span[0], span[1]) or \
                not (0 <= play[0] <= span[0] < play[1] <= duration_ms + 1e-6):
            issues.append(f"{cid}: no exact playback inside the recording")
        if (w["previous_phone"], w["next_phone"], w["word"]) != \
                (o["context"]["previous_phone"], o["context"]["next_phone"], o["word"]):
            issues.append(f"{cid}: context does not match the observation")
        ts = c["evidence"]["temporal_slot"]
        if ts["slot_ms"] is not None:
            prev, nxt = ts["previous_decoded"], ts["next_decoded"]
            if abs((nxt["start_ms"] - prev["end_ms"]) - ts["slot_ms"]) > 1e-6:
                issues.append(f"{cid}: temporal slot does not match its neighbours")
            if prev["end_ms"] > span[0] + 1e-6 or nxt["start_ms"] < span[1] - 1e-6:
                issues.append(f"{cid}: temporal slot does not contain the sound")
        if "shorter_slot" in it["signals"] and (ts["comparables"] < MIN_COMPARABLES or ts["pause_adjacent"]):
            issues.append(f"{cid}: slot comparison without enough comparables or next to a pause")
        ac = c["evidence"]["acoustic"]
        if ac["relative_energy"] != ((o["acoustic"] or {}).get("relative_energy")) or \
                ac["voiced"] != ((o["acoustic"] or {}).get("voiced")):
            issues.append(f"{cid}: acoustic evidence does not match the observation")
        if "lower_energy" in it["signals"] and ac["comparables"] < MIN_COMPARABLES:
            issues.append(f"{cid}: energy comparison without enough comparables")
        ctx = c["evidence"]["context"]
        if ctx["stress"] is not None and not ctx["stress_known"]:
            issues.append(f"{cid}: stress reported although the engine reports none")
        if "unstressed_vowel" in ctx["processes"] and not ctx["stress_known"]:
            issues.append(f"{cid}: unknown stress treated as unstressed")
        text = " ".join([c["summary"], it["label"], *it["reasons"],
                         *(e["text"] for e in it["candidate_explanations"])]).lower()
        for b in _BANNED:
            if b in text:
                issues.append(f"{cid}: judgemental or overclaiming wording ({b!r})")
    grouped = [i for g in red["groups"] for i in g["candidate_ids"]]
    if sorted(grouped) != sorted(ids) or len(grouped) != len(set(grouped)):
        issues.append("every candidate must appear in exactly one group")
    for g in red["groups"]:
        if any(next(c for c in red["candidates"] if c["id"] == i)["interpretation"]["category"] != g["id"]
               for i in g["candidate_ids"] if i in ids):
            issues.append(f"group {g['id']} holds a candidate of another category")
    return issues
