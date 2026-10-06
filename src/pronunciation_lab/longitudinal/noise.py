"""M10 noise floor: how much the user's own evidence moves when nothing should have changed — re-reads of the
same sentence with the same engine (any evidence class, in time order).

Two measures, both counted (never modelled):

* position repeat: of the sounds heard clearly as something else in one reading, how many were heard the same way
  at the same position in another reading of the same sentence. Low values mean a single re-read says little.
* pooled half-ratio: for a sentence read at least `noise_min_reads` times, the clear differences pooled over the
  later half of its readings ÷ the earlier half (when the earlier half has at least `noise_min_first_half_clear`).
  The lowest ratio seen is the largest drop that re-reading alone produced.

The change thresholds are then tightened, never loosened: a drop no larger than what re-reading the same text
produced is never called improvement or stability.

    effective retire share    = min(retire_max_share, lowest half-ratio)
    effective improving share = min(improve_max_share, lowest half-ratio)

With too few re-read sentences the defaults are used and the result says so.
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from typing import Any

from pronunciation_lab.longitudinal.calibration import LongitudinalCalibration

NOISE_VERSION = "m10-noise.1"


def calibrate(classified: list[dict[str, Any]], cal: LongitudinalCalibration) -> dict[str, Any]:
    by_sentence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in classified:
        by_sentence[r["reading"]["sentence_key"]].append(r)
    repeated = {k: v for k, v in by_sentence.items() if len(v) >= 2}

    def positions(r):
        out = {}
        for s in r["sounds"]:
            if s["e"] and s["q"] in ("counter", "confident", "supporting"):
                out[(s["wi"], s["si"], s["e"])] = s
        return out

    clear_before = clear_again = 0
    for rs in repeated.values():
        pos = [positions(r) for r in rs]
        for a, b in itertools.combinations(range(len(rs)), 2):
            for k, s in pos[a].items():
                if s["q"] == "confident" and s["out"] == "heard_other" and k in pos[b]:
                    clear_before += 1
                    t = pos[b][k]
                    clear_again += t["q"] == "confident" and t["h"] == s["h"]
    ratios = []
    for key, rs in sorted(repeated.items()):
        if len(rs) < cal.noise_min_reads:
            continue
        counts = [sum(1 for s in r["sounds"] if s["q"] == "confident" and s["out"] == "heard_other") for r in rs]
        half = len(counts) // 2
        first, second = sum(counts[:half]), sum(counts[-half:])
        if first >= cal.noise_min_first_half_clear:
            ratios.append({"sentence": key[:60], "reads": len(rs), "earlier": first, "later": second,
                           "ratio": round(second / first, 3)})
    usable = len(ratios) >= cal.noise_min_sentences
    lowest = min((x["ratio"] for x in ratios), default=None)
    retire = cal.retire_max_share if not usable else min(cal.retire_max_share, lowest)
    improve = cal.improve_max_share if not usable else min(cal.improve_max_share, lowest)
    return {
        "version": NOISE_VERSION, "source": "user_rereads" if usable else "defaults",
        "repeated_sentences": len(repeated), "sentences_used": len(ratios),
        "position_repeat": {"clear_before": clear_before, "clear_again": clear_again,
                            "share": round(clear_again / clear_before, 3) if clear_before else None},
        "half_ratios": ratios, "lowest_half_ratio": lowest,
        "effective": {"retire_max_share": retire, "improve_max_share": improve},
        "text": _text(clear_before, clear_again, usable, lowest, len(ratios)),
    }


def _text(before: int, again: int, usable: bool, lowest: float | None, n: int) -> str:
    parts = []
    if before:
        parts.append(f"When you re-read the same sentence, a sound heard clearly as something else was heard the "
                     f"same way again in {again} of {before} cases, so one re-read is not evidence of change.")
    if usable and lowest is not None and lowest < 1:
        parts.append(f"Across {n} sentences you re-read several times, the later readings had as few as "
                     f"{lowest:g} of the earlier readings' clear differences on the same text; drops no larger than "
                     "that are never called improvement.")
    elif usable:
        parts.append(f"Across {n} sentences you re-read several times, the later readings never had fewer clear "
                     "differences than the earlier ones, so the default thresholds apply unchanged.")
    else:
        parts.append("Not enough re-read sentences yet to calibrate from your own readings; default thresholds apply.")
    return " ".join(parts)
