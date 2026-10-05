"""M9 level 2 — patterns: counting only, never interpretation.

A pattern is a set of units that point the same way (expected /a/ heard as /b/; a fluency group; not-detected
sounds of one class in one word position), with its counter-evidence and breadth counted. Patterns carry no
wording beyond counts; hypotheses are formed in targets.py.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from pronunciation_lab.coaching import knowledge as K
from pronunciation_lab.coaching.evidence import EvidenceUnit, FluencyUnit


def measure(units: list[Any]) -> dict[str, int]:
    """Breadth of a set of units (EvidenceUnit or FluencyUnit).

    Gates, leverage and explanations use the *confident* breadth (conf_*): supporting units never count
    towards establishing a target. The totals are kept for the detailed view."""
    conf = [u for u in units if u.quality == "confident"]
    return {
        "conf_words": len({getattr(u, "lexical_key", None) for u in conf if getattr(u, "lexical_key", None)}),
        "conf_sentences": len({u.sentence_key for u in conf}),
        "conf_readings": len({u.reading_id for u in conf}),
        "conf_sessions": len({u.session_id for u in conf}),
        "units": len(units),
        "confident": sum(1 for u in units if u.quality == "confident"),
        "supporting": sum(1 for u in units if u.quality == "supporting"),
        "words": len({getattr(u, "lexical_key", None) for u in units if getattr(u, "lexical_key", None)}),
        "sentences": len({u.sentence_key for u in units}),
        "readings": len({u.reading_id for u in units}),
        "sessions": len({u.session_id for u in units}),
    }


@dataclass
class Pattern:
    key: str
    kind: str                      # contrast | fluency | clarity
    expected: str | None = None
    heard: str | None = None
    group: str | None = None
    unit_ids: list[str] = field(default_factory=list)      # confident + supporting (or weak, for clarity)

    def to_dict(self, index: "Index") -> dict[str, Any]:
        return {"key": self.key, "kind": self.kind, "expected": self.expected, "heard": self.heard,
                "group": self.group, "unit_ids": list(self.unit_ids), "measures": measure(index.get(self.unit_ids)),
                "origin": "counted"}


@dataclass
class Index:
    """All units of the pool, and the counts every later stage needs."""
    units: dict[str, Any]
    contrasts: dict[tuple[str, str], Pattern]
    fluency: dict[str, Pattern]
    clarity: dict[tuple[str, str], Pattern]
    counter_by_expected: dict[str, list[str]]           # heard-as-expected unit ids per expected sound
    conf_dev_by_expected: dict[str, Counter]            # expected → Counter(heard → confident units), plausible only
    exposure_words: int                                  # word occurrences read in the pool
    exposure_by_sound: dict[str, set[tuple[str, int]]]   # sound → word occurrences containing it
    word_sentences: dict[str, set[str]]                  # lexical key → sentences in the pool containing it
    exclusions: Counter

    def get(self, ids) -> list[Any]:
        return [self.units[i] for i in ids if i in self.units]


def build_index(units: list[EvidenceUnit], fluency: list[FluencyUnit]) -> Index:
    by_id: dict[str, Any] = {}
    contrasts: dict[tuple[str, str], Pattern] = {}
    counter: dict[str, list[str]] = defaultdict(list)
    conf_dev: dict[str, Counter] = defaultdict(Counter)
    exposure: dict[str, set[tuple[str, int]]] = defaultdict(set)
    words_read: set[tuple[str, int]] = set()
    word_sentences: dict[str, set[str]] = defaultdict(set)
    clarity: dict[tuple[str, str], Pattern] = {}
    exclusions: Counter = Counter()
    for u in sorted(units, key=lambda x: x.unit_id):
        by_id[u.unit_id] = u
        if u.exclusion:
            exclusions[u.exclusion] += 1
        if u.expected is not None:   # exposure is about the text read, whatever was heard
            occ = (u.reading_id, u.word_index)
            words_read.add(occ)
            exposure[u.expected].add(occ)
            if u.lexical_key:
                word_sentences[u.lexical_key].add(u.sentence_key)
        if u.quality == "counter":
            counter[u.expected].append(u.unit_id)
        elif u.quality in ("confident", "supporting") and u.outcome == "heard_other":
            p = contrasts.setdefault((u.expected, u.heard), Pattern(f"contrast:{u.expected}→{u.heard}", "contrast",
                                                                    u.expected, u.heard))
            p.unit_ids.append(u.unit_id)
            if u.quality == "confident":
                conf_dev[u.expected][u.heard] += 1
        elif u.quality == "weak" and not K.is_vowel_sound(u.expected) and u.context.get("word_position") == "final":
            key = ("consonant", "final")
            p = clarity.setdefault(key, Pattern("clarity:final_consonant", "clarity", group="final_consonant"))
            p.unit_ids.append(u.unit_id)
    flu: dict[str, Pattern] = {}
    for f in sorted(fluency, key=lambda x: x.unit_id):
        by_id[f.unit_id] = f
        if f.exclusion:
            exclusions[f.exclusion] += 1
            continue
        flu.setdefault(f.group, Pattern(f"fluency:{f.group}", "fluency", group=f.group)).unit_ids.append(f.unit_id)
    return Index(units=by_id, contrasts=contrasts, fluency=flu, clarity=clarity, counter_by_expected=dict(counter),
                 conf_dev_by_expected=dict(conf_dev), exposure_words=len(words_read), exposure_by_sound=dict(exposure),
                 word_sentences=dict(word_sentences), exclusions=exclusions)
