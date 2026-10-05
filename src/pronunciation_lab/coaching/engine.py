"""M9 orchestration: pool → evidence → patterns → targets → interventions → selection → contract.

Pure and deterministic: the same inputs, calibration and versions always give the same result (the only
non-derived value is `generated_at`, which the caller passes). Fails closed: if the result does not validate,
no action is shown.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pronunciation_lab.coaching import contract as C
from pronunciation_lab.coaching import knowledge as K
from pronunciation_lab.coaching.calibration import DEFAULT, Calibration
from pronunciation_lab.coaching.evidence import ReadingInput, normalise
from pronunciation_lab.coaching.patterns import build_index
from pronunciation_lab.coaching.plan import build_intervention, steps
from pronunciation_lab.coaching.pool import select_pool
from pronunciation_lab.coaching.selection import select
from pronunciation_lab.coaching.targets import counter_ids, evaluate, form_targets

COACHING_VERSION = "m9.1"


def run_coaching(inputs: list[ReadingInput], *, prior_exclusions: Counter | None = None, cal: Calibration = DEFAULT,
                 now: str | None = None, generated_at: str | None = None, detail: bool = True) -> dict[str, Any]:
    versions = {"coaching": COACHING_VERSION, "knowledge": K.KNOWLEDGE_VERSION, "calibration": cal.version}
    pool = select_pool(inputs, cal, now=now, prior_exclusions=prior_exclusions, versions=versions)
    units, fluency = [], []
    for i in pool.inputs:
        u, f = normalise(i)
        units += u
        fluency += f
    index = build_index(units, fluency)
    n_sentences = len({i.reading.sentence_key for i in pool.inputs})
    targets, consolidation = form_targets(index, cal, n_sentences)
    readings = {i.reading.reading_id: i.reading for i in pool.inputs}

    cache: dict[tuple, Any] = {}

    def intervention(t):
        key = (t.target_id, tuple(t.unit_ids))
        if key not in cache:
            cache[key] = build_intervention(t, index, readings, cal)
        return cache[key]

    def trainability(t):
        iv = intervention(t)
        return None if iv is None else iv.level

    established = [t for t in targets if t.tier == "established"]
    candidates = [t for t in established if intervention(t) is not None]
    sel = select(candidates, lambda t: evaluate(t, index, cal, n_sentences), trainability, cal) if pool.sufficient \
        else None

    result: dict[str, Any] = {
        "version": COACHING_VERSION, "generated_at": generated_at, "knowledge_version": K.KNOWLEDGE_VERSION,
        "calibration": cal.as_dict(),
        "pool": pool.summary(cal) | {"units": dict(Counter(u.quality for u in units)),
                                     "fluency_units": dict(Counter(f.quality for f in fluency)),
                                     "unit_exclusions": dict(sorted(index.exclusions.items()))},
        "state": "no_action", "actions": [], "no_action": None, "not_assessed": C.not_assessed(), "caveats": C.CAVEATS,
    }
    actions = []
    if sel is not None and sel.selected:
        split = cal.time_split[len(sel.selected) - 1]
        for k, t in enumerate(sel.selected):
            iv = intervention(t)
            decided = [r for rnd in sel.rounds if rnd["winner"] == t.target_id for r in rnd["comparisons"]
                       if r["winner"] == t.target_id]   # only the comparisons this action itself won
            absorbed = [a for a in sel.absorbed if a["absorbed_by"] == t.target_id] + \
                [a for a in consolidation if a.get("absorbed_into") == t.target_id
                 or (isinstance(a.get("absorbed_into"), list) and t.target_id in a["absorbed_into"])]
            actions.append(C.action(t, iv, steps(t, iv), k + 1, split[k], decided, absorbed, counter_ids(t, index)))
        result["state"] = "actions"
    else:
        result["no_action"] = C.no_action(_no_action_code(pool, targets, established, candidates, consolidation,
                                                          index))
    result["actions"] = actions
    if detail:
        emerging = sorted((t for t in targets if t.tier == "emerging" and t.kind != "CLARITY"),
                          key=lambda t: (-t.measures["confident"], t.target_id))
        result["detail"] = {
            "candidates": [C.target_dict(t) for t in targets],
            "listen_check": [C.target_dict(t) for t in emerging[:1]],   # never an action; detail view only
            "selection_rounds": sel.rounds if sel else [],
            "absorbed_by_selection": sel.absorbed if sel else [],
            "not_prioritised": [{"target": C.target_dict(x["target"]), "reason": x["reason"],
                                 "comparison": x["comparison"].to_dict() if x["comparison"] else None}
                                for x in (sel.not_prioritised if sel else [])],
            "consolidation": consolidation,
            "knowledge": K.describe(),
        }
    issues = C.validate_coaching(result, index.units, cal)
    result["integrity"] = {"ok": not issues, "issues": issues}
    if issues:  # fail closed: nothing invalid is ever shown as coaching
        result["state"], result["actions"] = "unavailable", []
        result["no_action"] = None
    return result


def _no_action_code(pool, targets, established, candidates, consolidation, index) -> str:
    if not pool.sufficient:
        return pool.insufficient_code
    if established and not candidates:
        audio = any(u.audio.get("play_ms") for t in established for u in index.get(t.unit_ids))
        return "no_trainable_intervention" if audio else "audio_unavailable"
    if any(t.tier == "emerging" and t.kind != "CLARITY" for t in targets):
        return "only_emerging"
    heard = [u for u in index.units.values() if getattr(u, "outcome", None) == "heard_other"]
    if any(r.get("conditions", {}).get("5_not_contradicted", {}).get("ok") is False for r in consolidation):
        return "contradictory"
    if heard and not any(u.quality == "confident" for u in heard):
        if any(u.quality == "supporting" for u in heard):
            return "only_ambiguous"
        return "only_excluded_kinds"
    return "nothing_recurring"
