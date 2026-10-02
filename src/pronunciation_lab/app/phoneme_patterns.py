"""M4 — grouping observations into patterns, without turning them into diagnoses.

A pattern is a set of observations of the same *expected* phoneme that point the
same way: towards the same competing phone (a contrast pattern), or towards the
expected phone not being decoded (a detection pattern), or the same extra phone
being decoded (an insertion pattern). Expected-as-expected occurrences of the same
phoneme are counted as counter-evidence, so "/s/ was missed twice" is always
reported next to "/s/ was heard as expected 5 times".

Classes (all "in this recording", never "you have a … problem"):

    one_off           exactly one supporting observation
    repeated          two or more
    consistent        >= CONSISTENT_MIN_OCCURRENCES, in >= CONSISTENT_MIN_WORDS words,
                      >= CONSISTENT_MIN_HIGH high-confidence, and deviations at least
                      as frequent as as-expected decodes of the same phoneme
    context_specific  two or more, all in one word position, while the same phoneme
                      was decoded as expected in other positions

Observations that are not interpreted (alignment suspect, invalid evidence) and
as-expected observations never support a pattern.

Display groups use a neutral, evidence-based order — never a ranking:
recurring, single occurrence, ambiguous only, not detected only, extra sounds.
Within a group, patterns are ordered by when they first occur.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from pronunciation_lab.app.phoneme_coach import REFERENCE_NOTE, is_reference_variant
from pronunciation_lab.benchmark.analysis import FUNCTION_WORDS, is_vowel

FUNCTION_WORD_NOTE = (
    "Only seen in function words (e.g. 'the', 'to'), which have several accepted pronunciations; the "
    "reference lists one of them, so this may not be a pronunciation difference."
)

CONSISTENT_MIN_OCCURRENCES = 3
CONSISTENT_MIN_WORDS = 2
CONSISTENT_MIN_HIGH = 2

PATTERN_THRESHOLDS = {
    "consistent_min_occurrences": CONSISTENT_MIN_OCCURRENCES,
    "consistent_min_words": CONSISTENT_MIN_WORDS,
    "consistent_min_high_confidence": CONSISTENT_MIN_HIGH,
}

SUPPORTING_TYPES = ("substitution_candidate", "ambiguous", "omission_candidate", "weak_evidence", "insertion")
UNCERTAIN_TYPES = ("ambiguous", "weak_evidence")
DETECTION_TYPES = ("omission_candidate", "weak_evidence")

GROUPS = (
    ("recurring", "Recurring in this recording",
     "Two or more occurrences point the same way. Still observations of this recording, not a fixed trait."),
    ("single", "Single occurrence — monitor",
     "Seen once. Not a pattern; listen to it and watch whether it recurs."),
    ("ambiguous", "Ambiguous — listen and compare",
     "The evidence does not clearly favour one sound. Listen to the occurrence and decide for yourself."),
    ("not_detected", "Not detected by the recogniser",
     "The recogniser found no clear sound here. Not detected does not prove the sound was absent."),
    ("insertion", "Extra sounds",
     "The recogniser decoded a sound that the expected pronunciation does not have."),
)


def pattern_key(obs: dict[str, Any]) -> tuple[str, str | None, str | None] | None:
    t = obs["type"]
    if t not in SUPPORTING_TYPES:
        return None
    if t == "insertion":
        return ("insertion", None, obs["observed"])
    if t in DETECTION_TYPES:
        return ("detection", obs["expected"], None)
    return ("contrast", obs["expected"], obs["competitor"])


def _summary(kind, expected, contrast, n, total, elsewhere, cls, context, n_uncertain):
    if kind == "insertion":
        return f"The recogniser decoded an extra /{contrast}/ {n} time{'s' if n > 1 else ''} in this recording."
    if kind == "detection":
        text = (f"The recogniser did not clearly detect /{expected}/ in {n} of {total} occurrence"
                f"{'s' if total > 1 else ''}. Not detected does not prove the sound was absent.")
    elif n_uncertain == n:
        text = (f"In {n} of {total} occurrence{'s' if total > 1 else ''} of /{expected}/, the evidence is "
                f"ambiguous between /{expected}/ and /{contrast}/.")
    else:
        text = (f"In {n} of {total} occurrence{'s' if total > 1 else ''} of /{expected}/, the acoustic/recognition "
                f"evidence is more consistent with /{contrast}/.")
    if elsewhere:
        text += f" /{expected}/ was heard as expected in {elsewhere} other occurrence{'s' if elsewhere > 1 else ''}."
    if cls == "one_off":
        text += " Single observation — monitor for recurrence."
    elif cls == "context_specific":
        text += f" Only seen word-{context}, so this is specific to that context, not a general /{expected}/ pattern."
    elif cls == "consistent":
        text += " Recurring pattern in this recording."
    return text


def build_patterns(observations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (patterns, display groups)."""
    by_key: OrderedDict = OrderedDict()
    for obs in observations:
        key = pattern_key(obs)
        if key is not None:
            by_key.setdefault(key, []).append(obs)

    interpretable = [o for o in observations if o["kind"] == "sound" and o["type"] != "not_interpreted"]
    patterns = []
    for n_pat, ((kind, expected, contrast), support) in enumerate(by_key.items()):
        same_phone = [o for o in interpretable if o["expected"] == expected] if expected else []
        as_expected = [o for o in same_phone if o["type"] == "expected"]
        positions = {o["context"].get("word_position") for o in support}
        words = list(OrderedDict.fromkeys(o["word"] for o in support))
        n = len(support)
        n_high = sum(o["confidence"] == "high" for o in support)
        n_uncertain = sum(o["type"] in UNCERTAIN_TYPES for o in support)

        if n == 1:
            cls, context = "one_off", None
        else:
            context = next(iter(positions)) if len(positions) == 1 else None
            elsewhere_positions = {o["context"]["word_position"] for o in as_expected}
            if context is not None and kind != "insertion" and elsewhere_positions - {context}:
                cls = "context_specific"
            elif (n >= CONSISTENT_MIN_OCCURRENCES and len(words) >= CONSISTENT_MIN_WORDS
                  and n_high >= CONSISTENT_MIN_HIGH and n >= len(as_expected)):
                cls = "consistent"
            else:
                cls = "repeated"

        if kind == "insertion":
            group = "insertion"
        elif kind == "detection":
            group = "not_detected"
        elif n_uncertain == n:
            group = "ambiguous"
        else:
            group = "recurring" if n > 1 else "single"

        strength = ("strong" if n_high == n else "ambiguous" if n_uncertain == n
                    else "mixed" if n_high else "moderate")
        if kind == "detection":
            strength = "not_detected"
        elif kind == "insertion":
            strength = "low"  # inserted sounds are low-confidence by definition
        total = len(same_phone) if expected else n

        patterns.append({
            "id": f"p{n_pat:02d}",
            "kind": kind,
            "expected": expected,
            "contrast": contrast,
            "class": cls,
            "context": context,
            "group": group,
            "evidence_strength": strength,
            "occurrences": n,
            "high_confidence_occurrences": n_high,
            "uncertain_occurrences": n_uncertain,
            "occurrences_of_expected_phoneme": total,
            "heard_as_expected_elsewhere": len(as_expected),
            "words": words,
            "word_positions": sorted(p for p in positions if p),
            "observation_ids": [o["id"] for o in support],
            "counter_evidence_ids": [o["id"] for o in as_expected],
            "first_ms": min((o["span_ms"][0] for o in support if o["span_ms"]), default=None),
            "reference_note": (REFERENCE_NOTE if is_reference_variant(expected, contrast)
                               else FUNCTION_WORD_NOTE if (kind == "contrast" and is_vowel(expected)
                                                           and all(w in FUNCTION_WORDS for w in words))
                               else None),
            "summary": _summary(kind, expected, contrast, n, total, len(as_expected), cls, context, n_uncertain),
        })

    groups = []
    for gid, title, explanation in GROUPS:
        members = sorted((p for p in patterns if p["group"] == gid),
                         key=lambda p: (p["first_ms"] is None, p["first_ms"] or 0.0, p["id"]))
        if members:
            groups.append({"id": gid, "title": title, "explanation": explanation,
                           "pattern_ids": [p["id"] for p in members]})
    return patterns, groups
