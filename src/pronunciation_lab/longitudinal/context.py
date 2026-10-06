"""M10 context map: where a recurring pattern occurs, compared WITHIN its own sound (pure).

For a directed pattern /e/ → /h/ and one context value (e.g. word-medial), the rate inside that context
(clear /e/→/h/ ÷ chances of /e/ in that context) is compared with the rate of the same sound everywhere else.
Raw context totals are never compared across sounds (different sounds occur in different contexts).

A context is called concentrated when: ≥ 4 chances on each side, the context covering at most 60 % of the sound's
chances (otherwise it is not specific), ≥ 2 clear inside, the inside rate ≥ 2× the outside
rate, and the same holds (more clear inside than the outside rate predicts) in both halves of the history, so a
context seen in one article only is not reported. Dimensions: word position, dictionary stress (from the
pronunciation dictionary, never observed prosody), consonant cluster, sentence position. Speed and sentence length
are not dimensions: the evidence does not support them (see docs).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pronunciation_lab.longitudinal.calibration import LongitudinalCalibration
from pronunciation_lab.longitudinal.history import CONTEXT_DIMENSIONS

CONTEXT_VERSION = "m10-ctx.1"
LABELS = {
    ("word_position", "initial"): "at the start of words", ("word_position", "medial"): "in the middle of words",
    ("word_position", "final"): "at the end of words",
    ("dictionary_stress", "primary"): "in syllables with main dictionary stress",
    ("dictionary_stress", "secondary"): "in syllables with secondary dictionary stress",
    ("dictionary_stress", "unstressed"): "in syllables without dictionary stress",
    ("cluster", "in_cluster"): "in consonant clusters", ("cluster", "not_in_cluster"): "outside consonant clusters",
    ("sentence_position", "first_word"): "in the first word of a sentence",
    ("sentence_position", "inside"): "inside sentences", ("sentence_position", "last_word"): "in the last word of a sentence",
}


def _totals(points, dim):
    opps, clear = Counter(), Counter()
    for p in points:
        opps.update(p["ctx_opps"].get(dim, {}))
        clear.update(p["ctx_clear"].get(dim, {}))
    return opps, clear


def _side(opps, clear, value):
    in_o, in_c = opps[value], clear[value]
    out_o, out_c = sum(opps.values()) - in_o, sum(clear.values()) - in_c
    return in_o, in_c, out_o, out_c


def profile(points: list[dict[str, Any]], cal: LongitudinalCalibration) -> dict[str, Any]:
    if not points or points[0]["clear_differences"] is None:
        return {"version": CONTEXT_VERSION, "dimensions": {}, "concentrated": [], "supported": False}
    half = (len(points) + 1) // 2
    halves = [points[:half], points[half:]]
    dims, concentrated = {}, []
    for dim in CONTEXT_DIMENSIONS:
        opps, clear = _totals(points, dim)
        rows = []
        for value in sorted(opps):
            in_o, in_c, out_o, out_c = _side(opps, clear, value)
            in_rate = in_c / in_o if in_o else None
            out_rate = out_c / out_o if out_o else None
            row = {"value": value, "label": LABELS.get((dim, value), value), "inside": {"opportunities": in_o, "clear": in_c},
                   "outside": {"opportunities": out_o, "clear": out_c}}
            ok = (in_o >= cal.context_min_opportunities and out_o >= cal.context_min_opportunities
                  and in_o <= cal.context_max_share * (in_o + out_o)
                  and in_c >= cal.context_min_clear and in_rate is not None
                  and (out_rate == 0 or in_rate >= cal.context_ratio * out_rate))
            stable = []
            for h in halves:
                ho, hc = _totals(h, dim)
                i_o, i_c, o_o, o_c = _side(ho, hc, value)
                # this half: more clear inside than the outside rate of the same half predicts
                stable.append(bool(i_o and i_c and i_c > (o_c / o_o if o_o else 0) * i_o))
            row["stable_in_both_halves"] = all(stable) and len(points) >= 2
            row["concentrated"] = bool(ok and row["stable_in_both_halves"])
            rows.append(row)
            if row["concentrated"]:
                concentrated.append({"dimension": dim, "value": value, "label": row["label"],
                                     "inside": row["inside"], "outside": row["outside"]})
        dims[dim] = rows
    note = None
    if any(c["dimension"] == "dictionary_stress" for c in concentrated):
        note = "Stress is the pronunciation dictionary's, not measured from your voice."
    return {"version": CONTEXT_VERSION, "dimensions": dims, "concentrated": concentrated, "supported": True,
            "note": note}
