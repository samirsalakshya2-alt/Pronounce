"""M10 history: evidence classes, word novelty and per-session aggregates for one engine (pure, deterministic).

Evidence classes (a reading belongs to exactly one, in time order):

    PRACTICE  read inside a practice session ("Read these now" / a practice record): practice evidence only
    RETEST    a sentence read before, that had been designated as retest material (M9 retest sentences or practice
              material) — chosen because differences occurred there, so never independent evidence
    REPEAT    any other sentence read before (same engine): not independent of its earlier reading
    FRESH     the first eligible reading of this sentence text with this engine

Only FRESH readings build personal baselines, judge improvement, retirement, regression and transfer. The other
classes are counted and shown (a good retest is useful feedback), never used as learning evidence.

Word novelty (per sound observation): FRESH_WORD when the word had never been read before (any class, same
engine), else SEEN_WORD. Practised words are decided per practice record (practice.py).

Opportunities for a sound = its judged occurrences: heard as expected (counter), clear difference (confident) or
ambiguous difference (supporting). Excluded observations (reference accent, function-word vowel, context-predicted,
merge or alignment suspect, neighbour shift, implausible pair, not interpreted, extra) and "not detected" (weak)
are neither opportunities nor differences: not detected never means absent.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

CLASSES = ("FRESH", "REPEAT", "RETEST", "PRACTICE")
OPPORTUNITY = ("counter", "confident", "supporting")
CONTEXT_DIMENSIONS = ("word_position", "dictionary_stress", "cluster", "sentence_position")
_CTX_FIELD = {"word_position": "pos", "dictionary_stress": "st", "cluster": "cl", "sentence_position": "sp"}


def ctx_value(row: dict[str, Any], dim: str) -> str | None:
    v = row.get(_CTX_FIELD[dim])
    if v is None:
        return None
    if dim == "cluster":
        return "in_cluster" if v else "not_in_cluster"
    return str(v)


def classify_readings(records: list[dict[str, Any]], engine: str,
                      designations: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Eligible readings of one engine in time order, each with its evidence class and word novelty.
    `designations`: (time, sentence_key) — when a sentence became retest / practice material."""
    eligible = sorted((r for r in records if r["eligible"] and r["reading"]["engine"] == engine),
                      key=lambda r: (r["reading"]["recorded_at"] or "", r["reading"]["reading_id"]))
    designated = sorted(designations)
    seen_sentences: set[str] = set()
    seen_words: set[str] = set()
    out = []
    for r in eligible:
        rd = r["reading"]
        t, key = rd["recorded_at"] or "", rd["sentence_key"]
        if rd["practice_session"]:
            cls = "PRACTICE"
        elif key in seen_sentences:
            cls = "RETEST" if any(k == key and dt <= t for dt, k in designated) else "REPEAT"
        else:
            cls = "FRESH"
        sounds = []
        for s in r["sounds"]:
            sounds.append(dict(s, novelty="FRESH_WORD" if s["w"] and s["w"] not in seen_words else "SEEN_WORD"))
        out.append({"reading": rd, "cls": cls, "sounds": sounds, "fluency": r["fluency"]})
        seen_sentences.add(key)
        seen_words |= {s["w"] for s in r["sounds"] if s["w"]}
    return out


@dataclass
class PairAgg:
    clear: int = 0
    ambiguous: int = 0
    clear_ids: list[str] = field(default_factory=list)
    ambiguous_ids: list[str] = field(default_factory=list)
    clear_words: Counter = field(default_factory=Counter)      # real (non-fragment) words with a clear difference
    clear_by_word: Counter = field(default_factory=Counter)    # every word (incl. fragments), for word splits
    ctx_clear: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    sentences: set = field(default_factory=set)


@dataclass
class SoundAgg:
    opportunities: int = 0
    opps_by_word: Counter = field(default_factory=Counter)
    ctx_opps: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    clear_differences: int = 0                                  # clear differences of this sound, any direction
    pairs: dict[str, PairAgg] = field(default_factory=lambda: defaultdict(PairAgg))


@dataclass
class SessionAgg:
    session_id: str
    time: str
    article_key: str
    readings: list[str] = field(default_factory=list)
    sounds: dict[str, SoundAgg] = field(default_factory=lambda: defaultdict(SoundAgg))
    fluency_opps: int = 0                                       # fresh sentences with a measurable speech level
    fluency_hits: Counter = field(default_factory=Counter)      # group → sentences with a noticed observation
    fluency_ids: dict[str, list] = field(default_factory=lambda: defaultdict(list))


def aggregate(classified: list[dict[str, Any]], cls: str = "FRESH") -> list[SessionAgg]:
    """Per-session aggregates of one evidence class, sessions ordered by their first reading of that class."""
    sessions: dict[str, SessionAgg] = {}
    for r in classified:
        if r["cls"] != cls:
            continue
        rd = r["reading"]
        s = sessions.get(rd["session_id"])
        if s is None:
            s = sessions[rd["session_id"]] = SessionAgg(rd["session_id"], rd["recorded_at"] or "",
                                                        rd.get("text_id") or rd["article_key"])
        s.readings.append(rd["reading_id"])
        for row in r["sounds"]:
            e = row["e"]
            if not e or row["q"] not in OPPORTUNITY:
                continue
            snd = s.sounds[e]
            snd.opportunities += 1
            snd.opps_by_word[row["w"]] += 1
            for dim in CONTEXT_DIMENSIONS:
                v = ctx_value(row, dim)
                if v is not None:
                    snd.ctx_opps[dim][v] += 1
            if row["out"] != "heard_other" or not row["h"]:
                continue
            p = snd.pairs[row["h"]]
            if row["q"] == "confident":
                snd.clear_differences += 1
                p.clear += 1
                p.clear_ids.append(row["o"])
                p.clear_by_word[row["w"]] += 1
                if row["w"] and not row["frag"]:
                    p.clear_words[row["w"]] += 1
                p.sentences.add(rd["sentence_key"])
                for dim in CONTEXT_DIMENSIONS:
                    v = ctx_value(row, dim)
                    if v is not None:
                        p.ctx_clear[dim][v] += 1
            else:
                p.ambiguous += 1
                p.ambiguous_ids.append(row["o"])
        if rd.get("fluency_reliable"):
            s.fluency_opps += 1
            groups = {f["g"] for f in r["fluency"] if f["q"] == "confident"}
            for g in groups:
                s.fluency_hits[g] += 1
                s.fluency_ids[g] += [f["o"] for f in r["fluency"] if f["q"] == "confident" and f["g"] == g]
    return sorted(sessions.values(), key=lambda s: (s.time, s.session_id))


def sound_points(sessions: list[SessionAgg], expected: str, heard: str) -> list[dict[str, Any]]:
    """The per-session evidence of one directed pattern: every session in which the sound had chances."""
    out = []
    for s in sessions:
        snd = s.sounds.get(expected)
        if snd is None or snd.opportunities == 0:
            continue
        p = snd.pairs.get(heard) or PairAgg()
        out.append({"session_id": s.session_id, "time": s.time, "article": s.article_key,
                    "opportunities": snd.opportunities, "clear": p.clear, "ambiguous": p.ambiguous,
                    "clear_differences": snd.clear_differences, "clear_words": Counter(p.clear_words),
                    "clear_by_word": Counter(p.clear_by_word), "opps_by_word": Counter(snd.opps_by_word),
                    "ctx_opps": {d: Counter(c) for d, c in snd.ctx_opps.items()},
                    "ctx_clear": {d: Counter(c) for d, c in p.ctx_clear.items()},
                    "clear_ids": list(p.clear_ids), "ambiguous_ids": list(p.ambiguous_ids),
                    "sentences": len(p.sentences)})
    return out


def fluency_points(sessions: list[SessionAgg], group: str) -> list[dict[str, Any]]:
    out = []
    for s in sessions:
        if s.fluency_opps == 0:
            continue
        n = s.fluency_hits.get(group, 0)
        out.append({"session_id": s.session_id, "time": s.time, "article": s.article_key,
                    "opportunities": s.fluency_opps, "clear": n, "ambiguous": 0, "clear_differences": None,
                    "clear_words": Counter(), "clear_by_word": Counter(), "opps_by_word": Counter(),
                    "ctx_opps": {}, "ctx_clear": {}, "clear_ids": list(s.fluency_ids.get(group, [])),
                    "ambiguous_ids": [], "sentences": n})
    return out


def tracked_patterns(sessions: list[SessionAgg]) -> list[str]:
    """Every directed sound pattern with at least one clear fresh observation, and every fluency group noticed."""
    from pronunciation_lab.longitudinal.identity import broad_id, fluency_id
    out = set()
    for s in sessions:
        for e, snd in s.sounds.items():
            for h, p in snd.pairs.items():
                if p.clear:
                    out.add(broad_id(e, h))
        for g, n in s.fluency_hits.items():
            if n:
                out.add(fluency_id(g))
    return sorted(out)


def class_counts(classified: list[dict[str, Any]], expected: str | None, heard: str | None,
                 group: str | None = None) -> dict[str, dict[str, int]]:
    """Per evidence class: readings, chances and clear observations of one pattern (shown, not judged)."""
    out = {c: {"readings": 0, "opportunities": 0, "clear": 0, "ambiguous": 0} for c in CLASSES}
    for r in classified:
        c = out[r["cls"]]
        c["readings"] += 1
        if group is not None:
            if r["reading"].get("fluency_reliable"):
                c["opportunities"] += 1
                c["clear"] += any(f["g"] == group and f["q"] == "confident" for f in r["fluency"])
            continue
        for row in r["sounds"]:
            if row["e"] != expected or row["q"] not in OPPORTUNITY:
                continue
            c["opportunities"] += 1
            if row["out"] == "heard_other" and row["h"] == heard:
                c["clear" if row["q"] == "confident" else "ambiguous"] += 1
    return out
