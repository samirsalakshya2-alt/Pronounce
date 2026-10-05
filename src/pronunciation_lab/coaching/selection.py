"""M9 selection — the smallest useful set of interventions (0–3), without redundancy.

    1. take the best candidate that passes every gate (leverage.best);
    2. remove the units it covers from every other candidate;
    3. re-evaluate the others on their RESIDUAL evidence — a candidate that no longer passes was a symptom of
       what was chosen and is recorded as absorbed;
    4. repeat until MAX_ACTIONS are chosen or nothing passes.

Fluency and sound units are disjoint, so removal never crosses modalities; at most MAX_FLUENCY fluency
actions. Three is a ceiling: nothing is ever added to fill a slot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pronunciation_lab.coaching.calibration import DEFAULT, Calibration
from pronunciation_lab.coaching.leverage import ComparisonRecord, best
from pronunciation_lab.coaching.targets import Target


@dataclass
class Selection:
    selected: list[Target] = field(default_factory=list)
    rounds: list[dict[str, Any]] = field(default_factory=list)          # per pick: comparisons made
    absorbed: list[dict[str, Any]] = field(default_factory=list)        # symptoms of a selected action
    not_prioritised: list[dict[str, Any]] = field(default_factory=list)    # {target, comparison, reason}


def select(candidates: list[Target], reevaluate: Callable[[Target], Target],
           trainability: Callable[[Target], int | None], cal: Calibration = DEFAULT) -> Selection:
    """`candidates`: established targets with an intervention. `reevaluate(target)` recomputes the tier of a
    target restricted to its current unit_ids; `trainability(target)` is None when no intervention exists."""
    sel = Selection()
    taken: set[str] = set()
    remaining = sorted(candidates, key=lambda t: t.target_id)
    fluency = 0
    last_loss: dict[str, ComparisonRecord] = {}
    while len(sel.selected) < cal.max_actions:
        eligible: list[Target] = []
        for c in list(remaining):
            residual = [u for u in c.unit_ids if u not in taken]
            current = c if len(residual) == len(c.unit_ids) else reevaluate(c.with_units(residual))
            if current.tier != "established" or trainability(current) is None:
                remaining.remove(c)
                by = sel.selected[-1].target_id if sel.selected else None
                sel.absorbed.append({"target": c.target_id, "absorbed_by": by, "residual_units": len(residual),
                                     "original_units": len(c.unit_ids), "failed": current.checks.get("_failed", []),
                                     "reason": "its remaining evidence no longer passes the gates once the selected "
                                               "action's observations are removed"})
                continue
            if current.kind == "FLUENCY" and fluency >= cal.max_fluency:
                continue
            eligible.append(current)
        if not eligible:
            break
        winner, records = best(eligible, trainability, cal)
        for r in records:
            last_loss[r.loser] = r
        sel.rounds.append({"pick": len(sel.selected) + 1, "winner": winner.target_id,
                           "candidates": [t.target_id for t in eligible], "comparisons": [r.to_dict() for r in records]})
        sel.selected.append(winner)
        taken.update(winner.unit_ids)
        fluency += winner.kind == "FLUENCY"
        remaining = [c for c in remaining if c.target_id != winner.target_id]
    for c in remaining:
        residual = [u for u in c.unit_ids if u not in taken]
        current = c if len(residual) == len(c.unit_ids) else reevaluate(c.with_units(residual))
        if current.tier != "established" or trainability(current) is None:
            continue
        if current.kind == "FLUENCY" and fluency >= cal.max_fluency:
            sel.not_prioritised.append({"target": current, "comparison": None,
                                        "reason": f"at most {cal.max_fluency} fluency action is shown at a time"})
            continue
        rec = last_loss.get(c.target_id)
        reason = "lost a comparison" if rec else f"the limit of {cal.max_actions} actions was reached"
        sel.not_prioritised.append({"target": current, "comparison": rec, "reason": reason})
    return sel
