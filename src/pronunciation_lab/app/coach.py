"""M4 Phoneme Coach — assembly and integrity.

`build_coach(result)` turns one M1 result into observations, patterns, display
groups and practice targets, then checks its own invariants. Every coaching
statement is reachable from an observation id, and every observation carries
its evidence and an exact playback window.
"""

from __future__ import annotations

import time
from typing import Any

from pronunciation_lab.app.phoneme_coach import (
    CONFIDENCE_LEVELS,
    COACH_VERSION,
    OBSERVATION_TYPES,
    THRESHOLDS,
    build_observations,
)
from pronunciation_lab.app.phoneme_patterns import PATTERN_THRESHOLDS, build_patterns
from pronunciation_lab.app.practice import build_targets
from pronunciation_lab.benchmark.schema import PronunciationResult

COVERAGE_NOTE = (
    "Sounds consistent with the expected sound are not listed. \"Consistent\" means the recogniser found "
    "evidence consistent with the expected sound, not proof that it was pronounced canonically (in the "
    "benchmark, a deliberate /θ/→/t/ reading was still decoded as /θ/). Full Recording Feedback lists every sound."
)

SHARED_MODEL_NOTE = (
    "OpenPronounce and the raw Wav2Vec2 recogniser use the same underlying acoustic model; agreement "
    "between them is therefore not independent confirmation."
)
COACH_CAVEATS = [
    "The coach interprets what one speech-recognition model heard. The microphone gives acoustic "
    "evidence, not a view of your mouth: statements are about the evidence, not about your articulation.",
    "Patterns describe this recording only; they are not a permanent judgement about you.",
    "Expected sounds come from eSpeak en-us; some differences may reflect that reference accent.",
    SHARED_MODEL_NOTE,
]

_ARTICULATORY = ("your tongue", "your lips", "your mouth did", "you failed", "you pronounced",
                 "wrong", "incorrect", "mistake", "poor", "bad pronunciation", "score")


def _empty(state: str, message: str, engine: dict[str, Any]) -> dict[str, Any]:
    return {"version": COACH_VERSION, "state": state, "message": message, "engine": engine,
            "thresholds": THRESHOLDS | {"patterns": {}}, "observations": [], "patterns": [], "groups": [],
            "practice_targets": [], "counts": {t: 0 for t in OBSERVATION_TYPES}, "caveats": COACH_CAVEATS,
            "integrity": {"ok": True, "issues": []}}


def build_coach(result: PronunciationResult) -> dict[str, Any]:
    engine = {"id": result.engine.name, "model": result.engine.model, "phone_set": result.phone_set,
              "shared_model_note": SHARED_MODEL_NOTE}
    if result.status in ("failed", "blocked"):
        return _empty("unavailable", "No coaching: the analysis did not produce evidence.", engine)
    heard = result.engine_evidence.get("recognition", {}).get("phones")
    if not heard or not result.words:
        return _empty("no_evidence", "No coaching: no speech sounds were decoded in this recording.", engine)

    t0 = time.perf_counter()
    observations = build_observations(result)
    t1 = time.perf_counter()
    patterns, groups = build_patterns(observations)
    by_id = {o["id"]: o for o in observations}
    targets = build_targets(patterns, by_id, result.recording.target.text)
    t2 = time.perf_counter()

    coach = {
        "version": COACH_VERSION,
        "state": "ok",
        "message": None,
        "engine": engine,
        "thresholds": THRESHOLDS | {"patterns": PATTERN_THRESHOLDS},
        "observations": observations,
        "patterns": patterns,
        "groups": groups,
        "practice_targets": targets,
        "counts": {t: sum(o["type"] == t for o in observations) for t in OBSERVATION_TYPES},
        "coverage": {
            "sounds": sum(o["kind"] == "sound" for o in observations),
            "consistent_with_expected": sum(o["kind"] == "sound" and o["type"] == "expected" for o in observations),
            "listed_in_patterns": len({r for pat in patterns for r in pat["observation_ids"]}),
            "not_interpreted": sum(o["type"] == "not_interpreted" for o in observations),
            "note": COVERAGE_NOTE,
        },
        "caveats": COACH_CAVEATS,
    }
    coach["integrity"] = {"ok": True, "issues": []}
    issues = validate_coach(coach, duration_ms=result.recording.audio.duration_ms)
    coach["integrity"] = {"ok": not issues, "issues": issues}
    coach["_timing_ms"] = {"observations": (t1 - t0) * 1000.0, "patterns_and_targets": (t2 - t1) * 1000.0}
    return coach


def validate_coach(coach: dict[str, Any], *, duration_ms: float) -> list[str]:
    """Invariants that must hold for any coach output. Returns a list of violations."""
    issues: list[str] = []
    obs = coach["observations"]
    ids = [o["id"] for o in obs]
    by_id = {o["id"]: o for o in obs}
    if len(ids) != len(set(ids)):
        issues.append("duplicate observation ids")
    sound_idx = [o["sound_index"] for o in obs if o["kind"] == "sound"]
    if len(sound_idx) != len(set(sound_idx)) or sound_idx != sorted(sound_idx):
        issues.append("sound observations duplicated or out of order")

    for o in obs:
        if o["type"] not in OBSERVATION_TYPES:
            issues.append(f"{o['id']}: unknown type {o['type']}")
        if o["confidence"] not in CONFIDENCE_LEVELS:
            issues.append(f"{o['id']}: unknown confidence {o['confidence']}")
        if (o["type"] == "not_interpreted") != (o["confidence"] == "none"):
            issues.append(f"{o['id']}: confidence {o['confidence']} impossible for type {o['type']}")
        if o["type"] in ("ambiguous", "weak_evidence", "omission_candidate", "insertion") and o["confidence"] == "high":
            issues.append(f"{o['id']}: {o['type']} cannot be high-confidence")
        if o["type"] == "substitution_candidate" and (o["observed"] is None or o["observed"] == o["expected"]):
            issues.append(f"{o['id']}: substitution without a different decoded phone")
        if o["type"] in ("omission_candidate", "weak_evidence") and o["observed"] is not None:
            issues.append(f"{o['id']}: omission with a decoded phone")
        if o["type"] != "not_interpreted":
            span, play = o["span_ms"], o["play_ms"]
            if not span or not play:
                issues.append(f"{o['id']}: interpretable observation without exact timing/playback")
            elif not (0 <= span[0] < span[1] <= duration_ms + 1e-6 and 0 <= play[0] <= span[0] < play[1] <= duration_ms + 1e-6):
                issues.append(f"{o['id']}: timing/playback outside the recording or not starting at the span")
            elif o["timing_source"] == "engine" and play[1] < span[1]:
                issues.append(f"{o['id']}: playback does not contain the decoded span")
        for key in ("expected_posterior", "observed_posterior", "competitor_posterior"):
            v = o.get(key)
            if v is not None and not (0.0 <= v <= 1.0):
                issues.append(f"{o['id']}: {key} {v} outside [0, 1]")
        ctx = o.get("context") or {}
        if ctx.get("stress") is not None and not ctx.get("stress_known"):
            issues.append(f"{o['id']}: stress reported although the engine reports none")
        if any(w in " ".join(o["reasons"]).lower() for w in _ARTICULATORY):
            issues.append(f"{o['id']}: judgemental or articulatory wording")

    pattern_ids = set()
    for p in coach["patterns"]:
        pattern_ids.add(p["id"])
        refs = p["observation_ids"]
        if not refs:
            issues.append(f"{p['id']}: pattern without supporting observations")
        if len(refs) != len(set(refs)):
            issues.append(f"{p['id']}: duplicate evidence references")
        for r in refs + p["counter_evidence_ids"]:
            if r not in by_id:
                issues.append(f"{p['id']}: references unknown observation {r}")
        for r in refs:
            o = by_id.get(r)
            if o and o["type"] in ("expected", "not_interpreted"):
                issues.append(f"{p['id']}: supported by a {o['type']} observation {r}")
        if p["occurrences"] != len(refs):
            issues.append(f"{p['id']}: occurrence count does not match its evidence")
        if p["class"] == "one_off" and len(refs) != 1:
            issues.append(f"{p['id']}: one_off with {len(refs)} observations")
        if p["class"] != "one_off" and len(refs) < 2:
            issues.append(f"{p['id']}: {p['class']} with a single observation")
        if any(w in p["summary"].lower() for w in _ARTICULATORY):
            issues.append(f"{p['id']}: judgemental or articulatory wording")

    cov = coach.get("coverage")
    if cov is not None:
        if cov["consistent_with_expected"] != sum(o["kind"] == "sound" and o["type"] == "expected" for o in obs):
            issues.append("coverage count of consistent sounds does not match the observations")
        if cov["sounds"] != sum(o["kind"] == "sound" for o in obs):
            issues.append("coverage sound count does not match the observations")

    grouped = [pid for g in coach["groups"] for pid in g["pattern_ids"]]
    if sorted(grouped) != sorted(pattern_ids) or len(grouped) != len(set(grouped)):
        issues.append("every pattern must appear in exactly one display group")

    for t in coach["practice_targets"]:
        if t["pattern_id"] not in pattern_ids:
            issues.append(f"{t['id']}: practice target without a pattern")
        if not t["supporting_observation_ids"]:
            issues.append(f"{t['id']}: practice target without supporting evidence")
        for r in t["supporting_observation_ids"]:
            if r not in by_id:
                issues.append(f"{t['id']}: references unknown observation {r}")
        if len(t["occurrences"]) != len(t["supporting_observation_ids"]):
            issues.append(f"{t['id']}: occurrences do not match supporting evidence")
        for occ in t["occurrences"]:
            if not occ["play_ms"]:
                issues.append(f"{t['id']}: occurrence {occ['observation_id']} without exact playback")
    return issues
