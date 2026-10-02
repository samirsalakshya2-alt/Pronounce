"""M5 — cross-engine comparison of reduction evidence (raw Wav2Vec2 ↔ OpenPronounce).

Both engines are interpreted separately (`reduction.build_reduction` on each
engine's own result). This module only pairs their evidence position by
position and labels how the two relate. It never reconciles them, never picks a
winner, and never copies one engine's evidence into the other's.

They share one acoustic model, so agreement is not independent confirmation;
disagreement usually comes from post-processing of the same posteriors (phone
merging, length removal, repeat collapse). The M2 finding is the reference
case: in "want to", raw Wav2Vec2 decodes two separate /t/ runs, while
OpenPronounce's repeat-collapse merges them into one long /t/ span assigned to
"to", leaving the /t/ of "want" not decoded. `decoded_separately_elsewhere`
makes exactly that visible.
"""

from __future__ import annotations

import difflib
from typing import Any

SHARED_MODEL_NOTE = (
    "Both engines use the same acoustic model (wav2vec2-lv-60-espeak-cv-ft): agreement is not independent "
    "confirmation, and differences come from how each post-processes the same posteriors."
)
AGREEMENT = ("same_category", "different_category", "only_first", "only_second")


def _norm(phone: str | None) -> str:
    return (phone or "").replace("ː", "")


def _pair_word(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> list[tuple[dict | None, dict | None]]:
    """Pair the sound observations of one word across engines (inventories can differ)."""
    if len(a) == len(b):
        return list(zip(a, b))
    sm = difflib.SequenceMatcher(a=[_norm(o["expected"]) for o in a], b=[_norm(o["expected"]) for o in b],
                                 autojunk=False)
    out: list[tuple[dict | None, dict | None]] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        xs, ys = a[i1:i2], b[j1:j2]
        k = min(len(xs), len(ys)) if op in ("equal", "replace") else 0
        out += list(zip(xs[:k], ys[:k]))
        out += [(x, None) for x in xs[k:]] + [(None, y) for y in ys[k:]]
    return out


def _side(o: dict[str, Any] | None, cand: dict[str, Any] | None) -> dict[str, Any] | None:
    if o is None:
        return None
    return {
        "observation_id": o["id"],
        "expected": o["expected"],
        "observed": o["observed"],
        "m4_type": o["type"],
        "expected_posterior": o["expected_posterior"],
        "span_ms": o["span_ms"],
        "timing_source": o["timing_source"],
        "play_ms": o["play_ms"],
        "candidate": None if cand is None else {
            "id": cand["id"],
            "category": cand["interpretation"]["category"],
            "label": cand["interpretation"]["label"],
            "evidence_strength": cand["evidence_strength"],
            "signals": cand["interpretation"]["signals"],
            "merge_suspect": cand["interpretation"]["merge_suspect"],
            "summary": cand["summary"],
        },
    }


def _covering_neighbour(obs_seq: list[dict[str, Any]], o: dict[str, Any], phone: str,
                        span: list[float]) -> dict[str, Any] | None:
    """In o's engine: an adjacent sound decoded as `phone` whose span covers `span`."""
    i = obs_seq.index(o)
    for j in (i - 1, i + 1):
        if 0 <= j < len(obs_seq):
            n = obs_seq[j]
            if _norm(n["observed"]) == _norm(phone) and n["timing_source"] == "engine" and n["span_ms"] \
                    and n["span_ms"][0] <= span[0] + 1e-6 and n["span_ms"][1] >= span[1] - 1e-6:
                return n
    return None


def compare(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """first/second: {"engine": id, "observations": [...], "reduction": {...}, "text": str, "duration_ms": float}."""
    if first["engine"] == second["engine"]:
        raise ValueError("comparison needs two different engines")
    if first["text"] != second["text"] or abs(first["duration_ms"] - second["duration_ms"]) > 1.0:
        raise ValueError("the two analyses are not of the same recording and text")

    def sounds(side):
        return [o for o in side["observations"] if o["kind"] == "sound"]

    cands = [{c["observation_id"]: c for c in side["reduction"]["candidates"]} for side in (first, second)]
    seqs = [sounds(first), sounds(second)]
    words = [{}, {}]
    for k, seq in enumerate(seqs):
        for o in seq:
            words[k].setdefault(o["word_index"], []).append(o)

    rows = []
    for wi in sorted(set(words[0]) | set(words[1])):
        for a, b in _pair_word(words[0].get(wi, []), words[1].get(wi, [])):
            ca = cands[0].get(a["id"]) if a else None
            cb = cands[1].get(b["id"]) if b else None
            if ca is None and cb is None:
                continue
            if ca and cb:
                agreement = "same_category" if ca["interpretation"]["category"] == cb["interpretation"]["category"] \
                    else "different_category"
            else:
                agreement = "only_first" if ca else "only_second"
            notes = []
            # One engine did not decode the sound, the other decoded it with its own span,
            # and the first engine's identical neighbour covers that span: repeat collapse.
            for x, y, xi in ((a, b, 0), (b, a, 1)):
                if x and y and x["observed"] is None and y["timing_source"] == "engine" and y["observed"] \
                        and y["span_ms"]:
                    cov = _covering_neighbour(seqs[xi], x, y["observed"], y["span_ms"])
                    if cov:
                        eng_x = (first, second)[xi]["engine"]
                        eng_y = (second, first)[xi]["engine"]
                        notes.append({
                            "kind": "decoded_separately_elsewhere",
                            "text": (f"{eng_y} decoded this /{y['observed']}/ separately "
                                     f"({y['span_ms'][0]:.0f}–{y['span_ms'][1]:.0f} ms); {eng_x} did not decode it, "
                                     f"and its decoded /{cov['observed']}/ for '{cov['word']}' spans "
                                     f"{cov['span_ms'][0]:.0f}–{cov['span_ms'][1]:.0f} ms, covering that region. "
                                     "This difference comes from post-processing of the same posteriors; it is "
                                     "kept, not resolved."),
                            "not_decoded_by": eng_x, "decoded_by": eng_y,
                        })
            if a and b and (a["observed"] is None) != (b["observed"] is None):
                notes.append({"kind": "decode_differs",
                              "text": "One engine decoded a sound here and the other did not."})
            o = a or b
            rows.append({
                "word": o["word"],
                "word_index": wi,
                "first": _side(a, ca),
                "second": _side(b, cb),
                "agreement": agreement,
                "notes": notes,
            })
    return {
        "engines": [first["engine"], second["engine"]],
        "rows": rows,
        "counts": {k: sum(r["agreement"] == k for r in rows) for k in AGREEMENT},
        "shared_model_note": SHARED_MODEL_NOTE,
    }


def validate_comparison(cmp: dict[str, Any], first: dict[str, Any], second: dict[str, Any]) -> list[str]:
    issues = []
    obs = [{o["id"]: o for o in side["observations"]} for side in (first, second)]
    cands = [{c["id"]: c for c in side["reduction"]["candidates"]} for side in (first, second)]
    if cmp["engines"] != [first["engine"], second["engine"]]:
        issues.append("engine order or identity changed")
    for r in cmp["rows"]:
        for k, key in enumerate(("first", "second")):
            side = r[key]
            if side is None:
                continue
            o = obs[k].get(side["observation_id"])
            if o is None:
                issues.append(f"{r['word']}: {key} side references an observation of another engine")
                continue
            if (side["observed"], side["span_ms"], side["m4_type"]) != (o["observed"], o["span_ms"], o["type"]):
                issues.append(f"{r['word']}: {key} side evidence differs from that engine's observation")
            if side["candidate"]:
                c = cands[k].get(side["candidate"]["id"])
                if c is None or c["interpretation"]["category"] != side["candidate"]["category"]:
                    issues.append(f"{r['word']}: {key} side candidate is not that engine's own interpretation")
        f, s = r["first"], r["second"]
        fc = f and f["candidate"]
        sc = s and s["candidate"]
        expected = ("same_category" if fc and sc and fc["category"] == sc["category"] else
                    "different_category" if fc and sc else "only_first" if fc else "only_second")
        if r["agreement"] != expected:
            issues.append(f"{r['word']}: agreement label {r['agreement']} does not match the two interpretations")
    return issues
