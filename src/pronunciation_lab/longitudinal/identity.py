"""Canonical longitudinal pattern identity, independent of M9 target ids.

M9 target ids describe a consolidation decision at one moment (`set:front_vowel_ladder`, `contrast:ɛ~ɪ`,
`lexical:exposure:ʒ`, `conditioned:medial:contrast:s~ʃ`) and can change between snapshots as evidence moves
between a family and its members. M10 tracks the stable unit beneath them: one directed sound difference.

    sub:ɛ>ɪ                         broad, directed (the unit of every longitudinal state)
    sub:ɛ>ɪ@word_position=medial    contextual manifestation of the broad pattern (a view, not a separate state)
    sub:ɛ>ɪ#word=anthropic          word-specific manifestation (a view, not a separate state)
    fluency:hesitation              a fluency pattern (sentence-level opportunities)

Contextual and word ids always reduce to their broad id (`broad_of`), so a broad personal pattern stays linkable
to its manifestations and history is never fragmented. A two-way contrast is two directed patterns sharing one
`contrast_group`; the opposite direction is the reverse-direction guard (`reverse_of`).
"""

from __future__ import annotations

import re
from typing import Any

IDENTITY_VERSION = "m10-id.1"
_SUB = re.compile(r"^sub:(?P<e>[^>@#]+)>(?P<h>[^>@#]+)(?:@(?P<dim>[a-z_]+)=(?P<val>[^#]+))?(?:#word=(?P<word>.+))?$")


def broad_id(expected: str, heard: str) -> str:
    return f"sub:{expected}>{heard}"


def context_id(expected: str, heard: str, dim: str, value: str) -> str:
    return f"{broad_id(expected, heard)}@{dim}={value}"


def word_id(expected: str, heard: str, word: str) -> str:
    return f"{broad_id(expected, heard)}#word={word}"


def fluency_id(group: str) -> str:
    return f"fluency:{group}"


def parse(pid: str) -> dict[str, Any]:
    if pid.startswith("fluency:"):
        return {"kind": "fluency", "group": pid.split(":", 1)[1], "expected": None, "heard": None,
                "context": None, "word": None}
    m = _SUB.match(pid)
    if not m:
        raise ValueError(f"not a canonical pattern id: {pid!r}")
    ctx = {"dimension": m["dim"], "value": m["val"]} if m["dim"] else None
    return {"kind": "sub", "group": None, "expected": m["e"], "heard": m["h"], "context": ctx, "word": m["word"]}


def broad_of(pid: str) -> str:
    p = parse(pid)
    return pid if p["kind"] == "fluency" else broad_id(p["expected"], p["heard"])


def reverse_of(pid: str) -> str | None:
    p = parse(pid)
    return None if p["kind"] == "fluency" else broad_id(p["heard"], p["expected"])


def contrast_group(pid: str) -> str | None:
    p = parse(pid)
    return None if p["kind"] == "fluency" else "contrast:" + "~".join(sorted({p["expected"], p["heard"]}))


def _pairs(target: dict[str, Any]) -> list[tuple[str, str]]:
    out = []
    for p in target.get("pairs") or []:
        e, h = p.split("→") if isinstance(p, str) else p
        out.append((e, h))
    return out


def link_target(target: dict[str, Any]) -> dict[str, Any]:
    """An M9 target (as serialised in coaching results) → the canonical patterns it is about.

    CONTRAST / SET → every directed pair it contains (a family is several directed patterns);
    CONDITIONED → the same broad patterns, with the context recorded as a qualifier;
    LEXICAL → the broad patterns of its pairs, with the word recorded as a qualifier;
    FLUENCY → the fluency group; CLARITY → none (M5 "not detected" is never counted as absence)."""
    kind = target.get("kind")
    patterns: list[str] = []
    if kind in ("CONTRAST", "SET", "CONDITIONED", "LEXICAL"):
        patterns = sorted({broad_id(e, h) for e, h in _pairs(target)})
    elif kind == "FLUENCY" and target.get("group"):
        patterns = [fluency_id(target["group"])]
    return {"identity_version": IDENTITY_VERSION, "m9_target_id": target.get("target_id"), "m9_kind": kind,
            "patterns": patterns, "context": target.get("condition"), "word": target.get("word"),
            "tracked": bool(patterns),
            "reason": None if patterns else "not tracked longitudinally (M5 'not detected' cannot mean absent)"}
