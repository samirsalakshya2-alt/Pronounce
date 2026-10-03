"""Compact inline feedback for one analysed attempt (M12 phase 6).

A count of *listed items*, never a score. It is derived only from the M4/M5
view already produced by the shared pipeline:

* "to notice": M4 patterns in the recurring, single and not-detected groups,
  plus M5 reduction candidates with an interpreted category (not ambiguous,
  not insufficient evidence) whose sound is not already part of a counted M4
  pattern — de-duplicated by observation id;
* "to compare": M4 ambiguous patterns, plus ambiguous M5 candidates not already
  counted;
* extra sounds, insufficient-evidence candidates and not-interpreted sounds are
  counted separately and only shown in the evidence drawer.
"""

from __future__ import annotations

from typing import Any

NOTICE_GROUPS = ("recurring", "single", "not_detected")


def compact_feedback(view: dict[str, Any] | None) -> dict[str, Any] | None:
    if not view:
        return None
    coach = view.get("coach") or {}
    reduction = view.get("reduction") or {}
    if view.get("state") != "ok" or coach.get("state") != "ok":
        return {"state": view.get("state") or coach.get("state") or "unavailable", "notice": 0, "compare": 0,
                "extra_sounds": 0, "insufficient": 0, "not_interpreted": 0,
                "message": view.get("message") or coach.get("message")}

    patterns = {p["id"]: p for p in coach.get("patterns", [])}
    group_of = {pid: g["id"] for g in coach.get("groups", []) for pid in g["pattern_ids"]}
    counted: set[str] = set()
    notice = compare = extra = 0
    for pid, p in patterns.items():
        g = group_of.get(pid)
        if g in NOTICE_GROUPS:
            notice += 1
            counted.update(p["observation_ids"])
        elif g == "ambiguous":
            compare += 1
            counted.update(p["observation_ids"])
        elif g == "insertion":
            extra += 1
    insufficient = 0
    for c in reduction.get("candidates", []):
        cat = c["interpretation"]["category"]
        if cat == "insufficient_evidence":
            insufficient += 1
        elif c["observation_id"] in counted:
            continue
        elif cat == "ambiguous":
            compare += 1
            counted.add(c["observation_id"])
        else:
            notice += 1
            counted.add(c["observation_id"])
    return {
        "state": "ok",
        "notice": notice,
        "compare": compare,
        "extra_sounds": extra,
        "insufficient": insufficient,
        "not_interpreted": (coach.get("coverage") or {}).get("not_interpreted", 0),
        "message": None,
    }


def compact_text(fb: dict[str, Any] | None) -> str:
    """The one-line summary shown under a sentence (mirrors the client's wording)."""
    if not fb:
        return ""
    if fb["state"] != "ok":
        return fb.get("message") or "No speech sounds were detected."
    parts = []
    if fb["notice"]:
        parts.append(f"{fb['notice']} thing{'s' if fb['notice'] != 1 else ''} to notice")
    if fb["compare"]:
        parts.append(f"{fb['compare']} to compare")
    return " · ".join(parts) or "Nothing stood out"
