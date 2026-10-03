"""Target confirmation (M12 phase 7): does this recording belong to its sentence?

Separate from pronunciation feedback, and never based on free speech
recognition. It uses only the alignment evidence of the analysis itself: the
expected phones of the sentence aligned against what the engine decoded.

    support = (expected sounds decoded as expected, or substituted while the
               expected sound stays plausible, P >= 0.05 — the M2 level)
              / expected sounds

    MATCH          support >= 0.76
    AMBIGUOUS      0.25 <= support < 0.76, or no speech sounds decoded
    MISMATCH       support < 0.25
    NOT_APPLICABLE the analysis produced no evidence (failed / blocked)

Calibration (benchmark R01–R20, both local engines; docs/M12_READER.md):
true pairs 0.82–0.98, partial reads (half the audio; an unread extra sentence)
0.25–0.70, recordings paired with another sentence 0.03–0.34. MATCH is the
midpoint of the gap between the lowest true pair and the highest partial read;
MISMATCH is below the lowest partial read, so a partly read sentence is never
hidden — it is at most AMBIGUOUS. One speaker, in-sample: a limitation.
"""

from __future__ import annotations

from typing import Any

from pronunciation_lab.benchmark.analysis import PLAUSIBLE_POSTERIOR, alignment_suspect_words
from pronunciation_lab.benchmark.schema import PronunciationResult

TARGET_VERSION = "tc-1"
MATCH_MIN_SUPPORT = 0.76
MISMATCH_BELOW = 0.25
THRESHOLDS = {"match_min_support": MATCH_MIN_SUPPORT, "mismatch_below": MISMATCH_BELOW,
              "plausible_expected_posterior": PLAUSIBLE_POSTERIOR}


def target_evidence(result: PronunciationResult) -> dict[str, Any]:
    phones = [p for w in result.words for p in w.phonemes]
    ops = [p.engine_evidence.get("operation") for p in phones]
    eps = [p.engine_evidence.get("expected_phone_posterior") for p in phones]
    plausible_subs = sum(1 for o, e in zip(ops, eps)
                         if o == "substitution" and isinstance(e, (int, float)) and e >= PLAUSIBLE_POSTERIOR)
    inserted = sum(len(p.engine_evidence.get("extra_heard_phones") or []) for p in phones)
    if phones:
        inserted += len(phones[0].engine_evidence.get("leading_heard_phones") or [])
    n = len(phones)
    return {
        "expected_sounds": n,
        "matched": ops.count("match"),
        "plausible_substitutions": plausible_subs,
        "substituted": ops.count("substitution"),
        "not_detected": ops.count("omission"),
        "inserted": inserted,
        "decoded_sounds": len(result.engine_evidence.get("recognition", {}).get("phones") or []),
        "alignment_suspect_words": len(alignment_suspect_words(result.words)),
        "support": (ops.count("match") + plausible_subs) / n if n else None,
    }


def confirm_target(result: PronunciationResult) -> dict[str, Any]:
    base = {"version": TARGET_VERSION, "engine": result.engine.name, "thresholds": THRESHOLDS}
    if result.status in ("failed", "blocked") or not result.words:
        return base | {"state": "NOT_APPLICABLE", "reason": "the analysis produced no evidence", "evidence": None}
    ev = target_evidence(result)
    if ev["expected_sounds"] == 0:
        return base | {"state": "NOT_APPLICABLE", "reason": "the sentence has no expected sounds", "evidence": ev}
    if ev["decoded_sounds"] == 0:
        return base | {"state": "AMBIGUOUS", "reason": "no speech sounds were decoded", "evidence": ev}
    s = ev["support"]
    if s >= MATCH_MIN_SUPPORT:
        state, reason = "MATCH", "most expected sounds were found in the recording"
    elif s < MISMATCH_BELOW:
        state, reason = "MISMATCH", "few expected sounds were found; the recording may be of another sentence"
    else:
        state, reason = "AMBIGUOUS", "only part of the sentence was found; part may be missing or another sentence read"
    return base | {"state": state, "reason": reason, "evidence": ev}
