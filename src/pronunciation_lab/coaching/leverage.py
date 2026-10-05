"""M9 leverage — an auditable, ordered comparison of candidate interventions. No score.

Criteria are consulted in calibration.ORDERING. A criterion decides only when the difference is meaningful
(count-like criteria: ratio ≥ margin_ratio AND absolute difference ≥ its margin; fractions and levels: the
absolute margin); otherwise the next criterion is consulted. If none decides, a deterministic tie-break
(target id) is used and recorded as "no meaningful difference". Every decision returns a ComparisonRecord
naming the criterion and both values, so "A was chosen over B because …" is always answerable.

Expected transfer is never a criterion: it is hypothesised, not observed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from pronunciation_lab.coaching.calibration import DEFAULT, Calibration
from pronunciation_lab.coaching.targets import SOUND_KINDS, Target

_TIER = {"established": 2, "emerging": 1, "insufficient": 0}
_RATIO_CRITERIA = ("breadth_sentences", "recurrence_sessions", "breadth_words", "coverage", "reach")
# counts of pauses and of sound confusions measure different things: never compared across modalities
_SAME_MODALITY_ONLY = ("breadth_words", "coverage", "reach", "concentration")
LABELS = {
    "tier": "evidence tier",
    "breadth_sentences": "breadth (different sentences)",
    "recurrence_sessions": "recurrence (different sessions)",
    "breadth_words": "breadth (different words)",
    "coverage": "coverage (confident observations it accounts for)",
    "reach": "reach (how much of your reading contains it)",
    "concentration": "consistency of the pattern (concentration)",
    "trainability": "trainability (specific practice guidance available)",
    "tie_break": "no meaningful difference (stable order)",
}


@dataclass(frozen=True)
class ComparisonRecord:
    winner: str
    loser: str
    criterion: str
    winner_value: Any
    loser_value: Any
    tie: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"criterion_label": LABELS[self.criterion]}

    def text(self) -> str:
        if self.tie:
            return f"no meaningful difference on any criterion; kept in a stable order"
        return f"{LABELS[self.criterion]}: {self.winner_value} vs {self.loser_value}"


def value(t: Target, criterion: str, trainability: Callable[[Target], int | None]) -> Any:
    m = t.measures
    if criterion == "tier":
        return _TIER[t.tier]
    if criterion == "breadth_sentences":
        return m.get("conf_sentences")
    if criterion == "recurrence_sessions":
        return m.get("conf_sessions")
    if criterion == "breadth_words":
        return m.get("conf_words") if t.kind in SOUND_KINDS else None
    if criterion == "coverage":
        return m.get("confident")
    if criterion == "reach":
        return m.get("reach")
    if criterion == "concentration":
        return m.get("concentration")
    if criterion == "trainability":
        return trainability(t)
    raise ValueError(criterion)


def meaningful(a, b, criterion: str, cal: Calibration) -> bool:
    if criterion == "tier":
        return a != b
    diff = abs(a - b)
    if diff < cal.margins_abs.get(criterion, 0):
        return False
    if criterion in _RATIO_CRITERIA:
        lo, hi = min(a, b), max(a, b)
        return lo <= 0 or hi / lo >= cal.margin_ratio
    return diff > 0


def compare(a: Target, b: Target, trainability: Callable[[Target], int | None], cal: Calibration = DEFAULT) -> ComparisonRecord:
    cross = (a.kind == "FLUENCY") != (b.kind == "FLUENCY")
    for criterion in cal.ordering:
        if cross and criterion in _SAME_MODALITY_ONLY:
            continue
        va, vb = value(a, criterion, trainability), value(b, criterion, trainability)
        if va is None or vb is None:
            continue  # not comparable across kinds (e.g. words for a fluency target)
        if meaningful(va, vb, criterion, cal):
            w, lo = (a, b) if va > vb else (b, a)
            wv, lv = (va, vb) if va > vb else (vb, va)
            return ComparisonRecord(w.target_id, lo.target_id, criterion, _show(criterion, wv), _show(criterion, lv))
    w, lo = (a, b) if a.target_id < b.target_id else (b, a)
    return ComparisonRecord(w.target_id, lo.target_id, "tie_break", w.target_id, lo.target_id, tie=True)


def _show(criterion: str, v):
    if criterion == "tier":
        return {2: "established", 1: "emerging", 0: "insufficient"}[v]
    if criterion == "trainability":
        return {2: "specific guidance", 1: "listen-and-compare / word or phrase practice", 0: "none"}.get(v, v)
    return v


def best(candidates: list[Target], trainability, cal: Calibration = DEFAULT) -> tuple[Target, list[ComparisonRecord]]:
    """King of the hill in stable id order; every comparison is recorded."""
    ordered = sorted(candidates, key=lambda t: t.target_id)
    champion, records = ordered[0], []
    for challenger in ordered[1:]:
        rec = compare(champion, challenger, trainability, cal)
        records.append(rec)
        if rec.winner == challenger.target_id:
            champion = challenger
    return champion, records
