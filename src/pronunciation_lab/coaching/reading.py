"""M9 "This reading" — a current-reading diagnosis of ONE reading.

Primary question: what were the major pronunciation improvement areas in THIS reading?
Secondary question: what was already stable in THIS reading?
Then fluency and cautions. Practice prioritisation stays with the recent-history coaching ("What to practise
now"); persistence across readings and change over time are M10.

Scope: exactly the session's summarised sentences (the latest eligible attempt per sentence, as in "Your
reading"). Nothing from other readings, the recent-history pool, or the coaching result is used.

Evidence is kept apart all the way through: a *clear* observation is an M4 substitution at high/moderate
confidence (unit quality "confident"); an *ambiguous* one is an ambiguous decode or a substitution in a
low-confidence sentence ("supporting"). Ambiguous observations may help a pattern recur (path B) but never
count as clear, never enter the clear-evidence rate and never decide severity.

Improvement areas (0–3, never manufactured), three paths:
  A  clear    — an established target of the shared consolidation (targets.form_targets at reading scale)
                whose clear-evidence rate is high enough;
  B  mixed    — a two-way contrast with ≥ 2 clear plus ambiguous support, ≥ 2 sentences and ≥ 2 real words;
  C  possible — the M5 note on weakened / not clearly detected final sounds.
They are ordered lexicographically (evidence, clear-rate band, sentences, real words, clear observations, id),
never by a weighted score, and each records the criterion that placed it.

This is a description, not a second coaching engine: it never recommends practice, never exposes coaching
hypotheses or tiers, never uses leverage, selection, interventions or practice plans, and never claims anything
across readings or over time. Imports are limited to the shared, stateless parts (evidence, patterns,
targets, knowledge, calibration); a test checks that pool, leverage, selection, plan, contract and engine are
never imported.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any

from pronunciation_lab.coaching import knowledge as K
from pronunciation_lab.coaching.calibration import DEFAULT_READING, ReadingCalibration
from pronunciation_lab.coaching.evidence import ReadingInput, normalise
from pronunciation_lab.coaching.patterns import build_index
from pronunciation_lab.coaching.targets import _COND, _contrast, evaluate, form_targets

READING_VERSION = "m9-read.3"
DESCRIPTIVE_KINDS = ("CONTRAST", "SET", "CONDITIONED", "LEXICAL")
BANDS = ("clear", "mixed", "possible")                      # evidence quality, best first
RATE_BANDS = ("high", "moderate", "low", "none")            # clear-evidence rate bands, highest first
# pattern scope, broadest first: a sound-level pattern reaches more of what is read than one context or one word
SCOPES = ("sound", "context", "word", "possible")
SCOPE_OF_KIND = {"CONTRAST": "sound", "SET": "sound", "CONDITIONED": "context", "LEXICAL": "word", "CLARITY": "possible"}
SCOPE_LABEL = {"sound": "Recurring sound pattern", "context": "Context-specific pattern", "word": "Word-specific",
               "possible": "Possible (final sounds)"}
ORDER_CRITERIA = ("evidence", "pattern_scope", "clear_rate_band", "sentences", "real_words", "clear_observations", "id")
ORDER_REASON = {"evidence": "clearer evidence", "pattern_scope": "a broader pattern",
                "clear_rate_band": "a higher clear-evidence rate", "sentences": "recurrence in more sentences",
                "real_words": "recurrence in more words", "clear_observations": "more clear observations",
                "id": "equal evidence on every criterion (a fixed tie-break)"}
OCCURRENCE_QUALITIES = ("counter", "confident", "supporting", "weak")   # judgeable, non-excluded occurrences
NO_AREA_TEXT = "No major pronunciation pattern was strong enough to call out in this reading."

GENERAL_CAVEAT = ("Heard as expected means the listening model decoded the expected sound; it is not proof of a "
                  "particular pronunciation. A clear observation is a confident decode; an ambiguous one is "
                  "support only. Both local listening models share one acoustic model.")
CAUTIONS = {
    "withheld_sentences": "{n} sentence{s} of this reading could not be separated from what followed, so {it_they} "
                          "{is_are} not described here.",
    "unconfirmed_identity": "{n} sentence{s} {is_are} waiting for you to confirm {it_they} {is_are} the right "
                            "sentence, so {it_they} {is_are} not described here.",
    "level_unmeasurable": "The speech level could not be measured in {n} sentence{s}, so pauses there are not "
                          "described.",
    "low_confidence_analysis": "{n} sentence{s} {was_were} decoded unclearly; {its_their} observations count as "
                               "support only.",
    "few_sentences": "Only {n} sentence{s} {was_were} described, too few to describe recurring patterns.",
    "reference_accent": "{n} difference{s} that may reflect the reference accent (eSpeak en-us) {was_were} not "
                        "counted.",
}
# a description, scoped to this reading: no prescription, no history, no trend, no judgement
BANNED = ("practise", "practice", "should", "need to", "work on", "focus on", "underlying", "your problem",
          "weakness", "struggle", "consistent", "always", "persistent", "tend to", "usually", "keep ", "again",
          "over time", "improving", "improved", "improves", "progress", "regress", "recent readings",
          "across readings", "every time", "wrong", "error", "incorrect", "mistake", "mispronounc", "perfect",
          "score", "%", "rank", "you can't", "you cannot", "don't know", "can't hear", "tongue", "lips", "jaw",
          "swallow", "chew")


def confusable_sounds() -> frozenset[str]:
    """Sounds the declared knowledge marks as diagnostically useful (set families, s/z/sh/zh, th). Strengths only
    name these: that /n/ was heard as expected is true but says little. Knowledge decides what is worth naming;
    the statement itself is always counted from this reading."""
    out: set[str] = set()
    for f in K.families():
        if f.mode == "set":
            out |= f.members
        elif f.id in ("sibilant_place", "th_sounds"):
            out |= {p for pair in f.pairs for p in pair} if f.id == "sibilant_place" else {e for e, _ in f.pairs}
    return frozenset(out)


def _plural(n: int) -> dict[str, str]:
    one = n == 1
    return {"n": str(n), "s": "" if one else "s", "is_are": "is" if one else "are", "was_were": "was" if one else "were",
            "it_they": "it" if one else "they", "its_their": "its" if one else "their"}


def _s(n: int) -> str:
    return "" if n == 1 else "s"


def _ref_ok(ref) -> bool:
    play = (ref or {}).get("play_ms")
    return bool(play) and len(play) == 2 and play[0] is not None and play[1] is not None and 0 <= play[0] < play[1]


def _pick(units, n, evidence=False):
    """Up to n units with playable audio, distinct words; clear before ambiguous, then most recent first."""
    out, seen = [], set()
    rank = {"confident": 0, "supporting": 1}
    ordered = sorted(units, key=lambda x: (x.recorded_at, x.unit_id), reverse=True)
    ordered.sort(key=lambda x: rank.get(x.quality, 2))
    for u in ordered:
        key = getattr(u, "lexical_key", None) or u.unit_id
        if key in seen or not _ref_ok(u.audio):
            continue
        seen.add(key)
        ref = dict(u.audio, unit_id=u.unit_id)
        if evidence:
            ref["evidence"] = "clear" if u.quality == "confident" else ("ambiguous" if u.quality == "supporting" else "possible")
        out.append(ref)
        if len(out) == n:
            break
    return out


def _times(n: int) -> str:
    return "once" if n == 1 else ("twice" if n == 2 else f"{n} times")


# ----------------------------------------------------------------------
# Scope, counts and rates (shared by the builder and the validator)
# ----------------------------------------------------------------------

def _in_scope(scope: dict[str, Any], u) -> bool:
    if getattr(u, "expected", None) not in scope["sounds"]:
        return False
    if scope.get("condition") and not _COND[scope["condition"]][1](u.context):
        return False
    if scope.get("word") and u.lexical_key != scope["word"]:
        return False
    return True


def _occurrences(units, scope) -> list:
    return [u for u in units if getattr(u, "quality", None) in OCCURRENCE_QUALITIES and _in_scope(scope, u)]


def rate_band(rate: float | None, rcal: ReadingCalibration) -> str:
    if rate is None:
        return "none"
    if rate >= rcal.rate_high:
        return "high"
    if rate >= rcal.rate_moderate:
        return "moderate"
    return "low" if rate >= rcal.rate_min else "below"


def area_counts(obs: list, all_units: list, scope: dict[str, Any] | None, rcal: ReadingCalibration) -> dict[str, Any]:
    """Counts of one area, clear and ambiguous kept apart. The clear-evidence rate uses clear observations only."""
    clear = [u for u in obs if u.quality == "confident"]
    amb = [u for u in obs if u.quality == "supporting"]
    real = {u.lexical_key for u in obs if u.lexical_key and not u.fragment_suspect}
    frags = {u.lexical_key for u in obs if u.lexical_key and u.fragment_suspect} - real
    out: dict[str, Any] = {"observations": len(obs), "clear": len(clear), "ambiguous": len(amb),
                           # observation level: how strong each clear decode was (M4), ambiguous kept apart
                           "clear_high": sum(1 for u in clear if u.m4_confidence == "high"),
                           "clear_moderate": sum(1 for u in clear if u.m4_confidence != "high"),
                           "sentences": len({u.sentence_key for u in obs}),
                           "clear_sentences": len({u.sentence_key for u in clear}),
                           "clear_real_words": len({u.lexical_key for u in clear if u.lexical_key and not u.fragment_suspect}),
                           "real_words": len(real), "word_fragments": len(frags)}
    if scope is None:   # path C: no rate
        out.update(occurrences=None, heard_as_expected=None, per_sound=None, rate_sound=None, clear_rate=None,
                   observed_rate=None, rate_band="none", direction_sound=None, clear_differences=None,
                   same_direction=None, direction_share=None)
        return out
    occ = _occurrences(all_units, scope)
    n = len(occ)
    out["occurrences"] = n
    out["heard_as_expected"] = sum(1 for u in occ if u.quality == "counter")
    # per expected sound: clear observations of that sound ÷ its in-scope occurrences (a frequent partner sound
    # never dilutes a confusion); the area's rate is its most affected sound's
    per = {}
    for s in scope["sounds"]:
        n_s = sum(1 for u in occ if u.expected == s)
        c_s = sum(1 for u in clear if u.expected == s)
        per[s] = {"clear": c_s, "occurrences": n_s, "rate": round(c_s / n_s, 3) if n_s else 0.0}
    top = sorted(per, key=lambda s: (-per[s]["rate"], -per[s]["occurrences"], s))[0] if per else None
    # direction, measured on the sound that carries the pattern's clear evidence (as the rate is per sound): of all
    # clear differences of that sound (in scope) in this reading, how many went this way
    dom = sorted(per, key=lambda s: (-per[s]["clear"], -per[s]["rate"], s))[0] if per else None
    # (observations going this way count wherever they are described, so a re-gated remainder is judged fairly)
    diffs = [u for u in all_units if u.quality == "confident" and u.outcome == "heard_other"
             and u.expected == dom and _in_scope(scope, u)] if dom else []
    pairs = set(scope.get("pairs") or [])
    same = sum(1 for u in diffs if f"{u.expected}→{u.heard}" in pairs) if pairs else (per[dom]["clear"] if dom else 0)
    out["direction_sound"] = dom
    out["clear_differences"] = len(diffs)
    out["same_direction"] = same
    out["direction_share"] = round(same / len(diffs), 3) if diffs else None
    out["per_sound"] = per
    out["rate_sound"] = top
    out["clear_rate"] = per[top]["rate"] if top else 0.0
    out["observed_rate"] = round(len(obs) / n, 3) if n else 0.0      # for reference only; never ranks
    out["rate_band"] = rate_band(out["clear_rate"], rcal)
    return out


def order_key(area: dict[str, Any]) -> tuple:
    """Pareto ordering, lexicographic and explainable (never a weighted score). The rate band is compared after the
    pattern scope: a within-word rate (a word read twice is 2 of 2 by construction) is not comparable with a rate
    over every occurrence of a sound."""
    c = area["counts"]
    return (BANDS.index(area["band"]), SCOPES.index(SCOPE_OF_KIND[area["kind"]]),
            RATE_BANDS.index(c["rate_band"]) if c["rate_band"] in RATE_BANDS else 9,
            -c["sentences"], -c["real_words"], -c["clear"], area["id"])


def _decided_by(a: tuple, b: tuple) -> str | None:
    for name, x, y in zip(ORDER_CRITERIA, a, b):
        if x != y:
            return name
    return None


# ----------------------------------------------------------------------
# Wording (descriptive, scoped, composition always disclosed)
# ----------------------------------------------------------------------

def _sound_list(sounds) -> str:
    s = ["/" + x + "/" for x in sounds]
    return s[0] if len(s) == 1 else ", ".join(s[:-1]) + " and " + s[-1]


def _scope_text(scope: dict[str, Any]) -> str:
    text = _sound_list(scope["sounds"])
    if scope.get("word"):
        text += f" in ‘{scope['word']}’"
    if scope.get("condition"):
        text += " " + _COND[scope["condition"]][0]
    return text


def _headline(area: dict[str, Any]) -> str:
    c, kind = area["counts"], area["kind"]
    if kind == "CLARITY":
        return (f"In this reading, final sounds appeared weakened or were not clearly detected in {c['real_words']} "
                f"word{_s(c['real_words'])} (possible: not detected does not prove a sound was absent).")
    where = f"{_times(c['observations'])} in {c['sentences']} sentence{_s(c['sentences'])}"
    if kind == "CONTRAST":
        a, b = area["pairs"][0].split("→")
        what = f"/{a}/ and /{b}/ were heard as each other" if area["two_way"] else f"/{a}/ was heard as /{b}/"
    elif kind == "SET":
        fam = area["family"]["title"] if area.get("family") else "these sounds"
        what = f"the sounds {_sound_list(area['sounds'])} ({fam}) were heard as one another"
    elif kind == "CONDITIONED":
        one = len(area["sounds"]) == 1
        what = f"{_sound_list(area['sounds'])} {'was' if one else 'were'} heard differently {area['condition_label']}"
    else:
        what = f"‘{area['word']}’ was heard differently"
    return f"In this reading, {what} {where}."


def composition(c: dict[str, Any]) -> str:
    """Observation level, never merged: clear decodes (by M4 confidence) and ambiguous decodes (support only)."""
    return (f"{c['clear']} clear ({c['clear_high']} high-confidence, {c['clear_moderate']} moderate-confidence) and "
            f"{c['ambiguous']} ambiguous")


def _evidence_text(area: dict[str, Any]) -> str:
    c = area["counts"]
    if area["band"] == "possible":
        return (f"Individual observations: {c['observations']} possible (M5: weakened or not clearly detected), not "
                "counted as clear or ambiguous differences.")
    words = f"{c['real_words']} word{_s(c['real_words'])}"
    if c["word_fragments"]:
        words += f" and {c['word_fragments']} word fragment{_s(c['word_fragments'])}"
    return (f"Individual observations: {c['observations']} observed difference{_s(c['observations'])} in {words}: "
            f"{composition(c)}.")


def _pattern_text(area: dict[str, Any]) -> str:
    """Pattern level: what the accumulated evidence supports, and how far it generalises (never a probability)."""
    c, scope = area["counts"], SCOPE_OF_KIND[area["kind"]]
    if scope == "possible":
        return "Pattern: possible only; not detected does not prove a sound was absent."
    direction = ""
    if c.get("direction_share") is not None and area["kind"] != "SET":
        d = c["direction_sound"]
        direction = (f" Of the {c['clear_differences']} clear difference{_s(c['clear_differences'])} of "
                     f"{_scope_text(dict(area['scope'], sounds=[d]))} in this reading, {c['same_direction']} "
                     "went this way.")
    if scope == "word":
        return (f"Pattern: word-specific. Strong for ‘{area['word']}’ ({c['clear']} clear in {c['clear_sentences']} "
                f"sentence{_s(c['clear_sentences'])}); the evidence is limited to this word, so it says little about "
                f"{_sound_list(area['sounds'])} in other words.")
    clear_where = (f"{c['clear_sentences']} sentence{_s(c['clear_sentences'])} and {c['clear_real_words']} "
                   f"word{_s(c['clear_real_words'])}")
    what = "within one family of sounds" if area["kind"] == "SET" else "in the same direction"
    if scope == "context":
        text = (f"Pattern: context-specific ({area['condition_label']}): heard clearly {_times(c['clear'])} {what}, in "
                f"{clear_where}; outside that context it was heard differently less often.")
    elif area["band"] == "clear":
        text = f"Pattern: recurring. Heard clearly {_times(c['clear'])} {what}, in {clear_where}."
    else:
        text = (f"Pattern: recurring, partly ambiguous. Heard clearly {_times(c['clear'])} {what}, in {clear_where}, "
                f"supported by {c['ambiguous']} ambiguous observation{_s(c['ambiguous'])} that "
                f"{'is' if c['ambiguous'] == 1 else 'are'} not counted as clear.")
    often = [w for w in area.get("words", []) if w["observations"] >= 2]
    if often and c["real_words"] >= 2:
        text += " Most often in " + ", ".join(f"‘{w['word']}’ ({_times(w['observations'])})" for w in often[:2]) + "."
    return text + direction


def _order_text(area: dict[str, Any]) -> str | None:
    by = area["ordering"]["placed_above_next_by"]
    if by is None:
        return None
    return f"Placed before area {area['ordering']['position'] + 1} for {ORDER_REASON[by]}."


def _rate_text(area: dict[str, Any]) -> str | None:
    c = area["counts"]
    if c["clear_rate"] is None:
        return None
    scope = area["scope"]

    def where(s):
        return _scope_text(dict(scope, sounds=[s]))
    top = c["rate_sound"]
    text = f"Clear-evidence rate: {c['per_sound'][top]['clear']} of {c['per_sound'][top]['occurrences']} occurrences of {where(top)}"
    others = [f"{where(s)}: {v['clear']} of {v['occurrences']}" for s, v in sorted(c["per_sound"].items()) if s != top]
    return text + (f" ({'; '.join(others)})" if others else "") + "."


def _counter_text(area: dict[str, Any]) -> str | None:
    k = area["counts"].get("heard_as_expected")
    if not k:
        return None
    one = len(area["scope"]["sounds"]) == 1
    return f"{_sound_list(area['scope']['sounds'])} {'was' if one else 'were'} heard as expected {_times(k)} in this reading."


# ----------------------------------------------------------------------
# Candidates
# ----------------------------------------------------------------------

def _area(path_band: str, kind: str, uid: str, obs: list, all_units: list, scope, rcal, index, *, pairs=(),
          sounds=(), word=None, condition=None, family_id=None) -> dict[str, Any]:
    directions = {(u.expected, u.heard) for u in obs if getattr(u, "heard", None)}
    fam = K.family(family_id) if family_id else None
    by_word = Counter(u.lexical_key for u in obs if u.lexical_key and not u.fragment_suspect)
    clear_by_word = Counter(u.lexical_key for u in obs if u.lexical_key and u.quality == "confident")
    area: dict[str, Any] = {
        "id": uid, "kind": kind, "band": path_band, "origin": "counted",
        "path": {"clear": "A", "mixed": "B", "possible": "C"}[path_band],
        "pattern_scope": SCOPE_OF_KIND[kind], "pattern_label": SCOPE_LABEL[SCOPE_OF_KIND[kind]],
        "sounds": list(sounds), "pairs": ["→".join(p) for p in pairs], "two_way": len({frozenset(d) for d in directions}) == 1
        and len(directions) == 2, "word": word, "condition": condition,
        "condition_label": _COND[condition][0] if condition else None,
        "family": {"id": fam.id, "title": fam.title, "knowledge": K.FAMILY_TEXT, "origin": "knowledge"} if fam else None,
        "scope": scope, "counts": area_counts(obs, all_units, scope, rcal),
        "words": [{"word": w, "observations": n, "clear": clear_by_word[w]}
                  for w, n in sorted(by_word.items(), key=lambda kv: (-kv[1], kv[0]))],
        "unit_ids": sorted(u.unit_id for u in obs),
    }
    counters = [u for u in _occurrences(all_units, scope) if u.quality == "counter"] if scope else []
    area["examples"] = _pick(obs, rcal.n_examples, evidence=True)
    area["counter_examples"] = _pick(counters, rcal.n_counter)
    return area


def _path_a_area(t, index, all_units, rcal) -> dict[str, Any] | None:
    """Path A: an established target of the shared consolidation at reading scale (its gates already require
    repeated clear observations in several sentences and words, in one direction, not contradicted), plus a
    clear-evidence rate."""
    if t.kind not in DESCRIPTIVE_KINDS or t.tier != "established":
        return None
    scope = {"sounds": list(t.sounds), "condition": t.condition, "word": t.word,
             "pairs": ["→".join(p) for p in t.pairs]}
    area = _area("clear", t.kind, t.target_id, index.get(t.unit_ids), all_units, scope, rcal, index,
                 pairs=t.pairs, sounds=t.sounds, word=t.word, condition=t.condition, family_id=t.family_id)
    area["gates"] = {k: v for k, v in t.checks.items() if not k.startswith("_")}
    area["gates"]["clear_rate"] = {"value": area["counts"]["clear_rate"], "required": rcal.rate_min,
                                   "ok": area["counts"]["clear_rate"] >= rcal.rate_min}
    if not area["gates"]["clear_rate"]["ok"]:
        return None
    area["_target"] = t
    return area


def _path_b_area(t, index, all_units, rcal) -> dict[str, Any] | None:
    """Path B: a two-way contrast whose clear observations recur (≥ 2) with ambiguous support, in several sentences
    and real words, in one direction. Ambiguous observations help it recur; they never count as clear."""
    scope = {"sounds": list(t.sounds), "condition": None, "word": None, "pairs": ["→".join(p) for p in t.pairs]}
    area = _area("mixed", "CONTRAST", "mixed:" + t.target_id, index.get(t.unit_ids), all_units, scope, rcal, index,
                 pairs=t.pairs, sounds=t.sounds)
    c = area["counts"]
    eng = t.checks.get("engine_agreement", {"ok": True})
    share = c["direction_share"]
    gates = {
        "observations": (c["observations"], rcal.mixed_min_observations, c["observations"] >= rcal.mixed_min_observations),
        "clear": (c["clear"], rcal.mixed_min_clear, c["clear"] >= rcal.mixed_min_clear),
        "sentences": (c["sentences"], rcal.mixed_min_sentences, c["sentences"] >= rcal.mixed_min_sentences),
        "real_words": (c["real_words"], rcal.mixed_min_real_words, c["real_words"] >= rcal.mixed_min_real_words),
        "clear_rate": (c["clear_rate"], rcal.rate_min, c["clear_rate"] >= rcal.rate_min),
        "direction": (share, rcal.c_min, share is not None and share >= rcal.c_min),
        "not_contradicted": (not t.contradicted, True, not t.contradicted),
        "engine_agreement": (eng.get("value"), eng.get("required"), bool(eng.get("ok"))),
    }
    area["gates"] = {k: {"value": v, "required": r, "ok": ok} for k, (v, r, ok) in gates.items()}
    if not all(g[2] for g in gates.values()):
        return None
    area["_target"] = t
    return area


def _candidates(index, all_units, rcal, n_sent) -> list[dict]:
    targets, _ = form_targets(index, rcal.formation(), n_sent)
    a = [x for x in (_path_a_area(t, index, all_units, rcal) for t in targets) if x]
    covered = {p for x in a if x["kind"] != "LEXICAL" for p in x["_target"].pairs}   # sound-level clear areas
    by_pair: dict[frozenset, list] = defaultdict(list)
    for p in sorted(index.contrasts):
        by_pair[frozenset(p)].append(p)
    b = []
    for _, ps in sorted(by_pair.items(), key=lambda kv: sorted(kv[1])):
        if covered & set(ps):
            continue   # already described by a clear-evidence sound-level area
        t = evaluate(_contrast(index, sorted(ps)), index, rcal.formation(), 0)
        area = _path_b_area(t, index, all_units, rcal)
        if area:
            b.append(area)
    return a + b + _path_c(index.units.values(), all_units, rcal)


def _regate(area, residual: list[str], index, all_units, rcal, n_sent) -> dict[str, Any] | None:
    """What is left of a candidate once a stronger area has described some of its observations, re-gated on its
    own (as the recent-history selection does): kept only if the remaining evidence still qualifies."""
    t = area.get("_target")
    if t is None or not residual:
        return None
    t2 = evaluate(t.with_units(residual), index, rcal.formation(), n_sent)
    return _path_a_area(t2, index, all_units, rcal) if area["path"] == "A" else _path_b_area(t2, index, all_units, rcal)


def _membership(candidates, index, all_units, rcal, n_sent) -> tuple[list[dict], list[dict]]:
    """Every qualifying candidate becomes an area (no cap); an observation is described once. Candidates are taken
    in Pareto order, so the strongest evidence describes a shared observation; the rest is re-gated, never dropped
    unseen. Returns (areas in Pareto order, absorbed records)."""
    accepted, absorbed, used = [], [], set()
    for c in sorted(candidates, key=order_key):
        shared = used & set(c["unit_ids"])
        if shared:
            residual = [i for i in c["unit_ids"] if i not in used]
            kept = _regate(c, residual, index, all_units, rcal, n_sent)
            absorbed.append({"id": c["id"], "kind": c["kind"], "band": c["band"], "sounds": c["sounds"],
                             "pairs": c["pairs"], "word": c["word"], "shared_observations": len(shared),
                             "absorbed_into": [a["id"] for a in accepted if shared & set(a["unit_ids"])],
                             "remaining": "kept as its own area" if kept else
                             ("none" if not residual else "did not qualify on its own")})
            if kept is None:
                continue
            c = kept
        used |= set(c["unit_ids"])
        accepted.append(c)
    accepted.sort(key=order_key)
    return accepted, absorbed


def _path_c(units, all_units, rcal) -> list[dict]:
    clar = [u for u in units if getattr(u, "m5", None) and u.m5.get("category") in ("possible_omission", "possible_weakening")
            and not u.m5.get("natural_possible") and not u.m5.get("merge_suspect") and u.quality == "weak"
            and u.context.get("word_position") == "final"]
    area = _area("possible", "CLARITY", "clarity:final", clar, all_units, None, rcal, None,
                 sounds=sorted({u.expected for u in clar}), condition="final")
    area["gates"] = {"words": {"value": area["counts"]["real_words"], "required": rcal.clarity_min_words,
                               "ok": area["counts"]["real_words"] >= rcal.clarity_min_words}}
    return [area] if area["gates"]["words"]["ok"] else []


def _strengths(units, excluded_sounds: set, rcal) -> list[dict]:
    occ: dict[str, list] = defaultdict(list)
    for u in units:
        if u.expected and u.quality in OCCURRENCE_QUALITIES:
            occ[u.expected].append(u)
    notable = confusable_sounds()
    cands = []
    for sound, us in occ.items():
        if sound not in notable or sound in excluded_sounds:
            continue
        ok = [u for u in us if u.quality == "counter"]
        diffs = len(us) - len(ok)
        if len(us) >= rcal.strength_min_occurrences and len(ok) / len(us) >= rcal.strength_min_share \
                and diffs <= rcal.strength_max_differences:
            cands.append((-len(us), -len(ok) / len(us), sound, ok, len(us), diffs))
    out = []
    for _, _, sound, ok, total, diffs in sorted(cands)[:rcal.max_strengths]:
        out.append({"origin": "counted", "sound": sound,
                    "counts": {"occurrences": total, "heard_as_expected": len(ok), "differences": diffs},
                    "text": f"In this reading, /{sound}/ was heard as expected in {len(ok)} of {total} occurrences.",
                    "examples": _pick(ok, 2)})
    return out


def _area_sounds(areas) -> set[str]:
    return {s for a in areas for s in a["sounds"]} | {p for a in areas for pair in a["pairs"] for p in pair.split("→")}


# ----------------------------------------------------------------------
# The description
# ----------------------------------------------------------------------

def build_reading_feedback(inputs: list[ReadingInput], *, session_id: str, article_title: str | None = None,
                           coverage: dict[str, Any] | None = None, rcal: ReadingCalibration = DEFAULT_READING,
                           generated_at: str | None = None) -> dict[str, Any]:
    coverage = coverage or {}
    readings = [i.reading for i in inputs]
    units, fluency = [], []
    for i in inputs:
        u, f = normalise(i)
        units += u
        fluency += f
    index = build_index(units, fluency)
    n_sent = len({r.sentence_key for r in readings})
    provenance = {"inputs": [{"attempt_id": r.attempt_id, "job_id": r.job_id, "segment_id": r.segment_id}
                             for r in readings]}
    provenance["fingerprint"] = hashlib.sha256(json.dumps(provenance["inputs"], sort_keys=True).encode()).hexdigest()[:20]

    # 1. major improvement areas (first; strengths never take their place). The gates decide how many qualify;
    #    the Pareto ordering only orders them; nothing is truncated.
    shown: list[dict[str, Any]] = []
    absorbed: list[dict[str, Any]] = []
    if n_sent >= rcal.min_sentences:
        shown, absorbed = _membership(_candidates(index, units, rcal, n_sent), index, units, rcal, n_sent)
        for k, c in enumerate(shown):
            c.pop("_target", None)
            nxt = shown[k + 1] if k + 1 < len(shown) else None
            c["ordering"] = {"position": k + 1, "key": dict(zip(ORDER_CRITERIA, order_key(c))),
                             "placed_above_next_by": _decided_by(order_key(c), order_key(nxt)) if nxt else None,
                             "next": nxt["id"] if nxt else None}
            c["text"] = _headline(c)
            c["evidence_text"] = _evidence_text(c)
            c["pattern_text"] = _pattern_text(c)
            c["rate_text"] = _rate_text(c)
            c["counter_text"] = _counter_text(c)
            c["order_text"] = _order_text(c)
    in_areas = {i for a in shown for i in a["unit_ids"]}

    # everything else heard differently is counted, never listed one by one (clear and ambiguous kept apart)
    rest = [u for u in units if u.quality in ("confident", "supporting") and u.outcome == "heard_other"
            and u.unit_id not in in_areas]
    rc, ra = sum(u.quality == "confident" for u in rest), sum(u.quality == "supporting" for u in rest)
    other = {"count": len(rest), "clear": rc, "ambiguous": ra,
             "note": (f"{len(rest)} other difference{_s(len(rest))} in this reading ({rc} clear, {ra} ambiguous) did "
                      "not form a pattern strong enough to call out; the detailed report below lists them.")
             if rest else None}

    # 2. already stable (after the areas; never a sound involved in a qualifying area, on either side)
    strengths = _strengths(units, _area_sounds(shown), rcal) if n_sent >= rcal.min_sentences else []

    # 3. fluency (sentences with a measurable speech level only)
    flu_units = [f for f in fluency if f.quality == "confident"]
    reliable = [r for r in readings if r.fluency_reliable]
    fluency_out, fluency_note = None, None
    groups = Counter(f.group for f in flu_units)
    if groups:
        group, n = sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        if n >= rcal.fluency_min:
            gu = [f for f in flu_units if f.group == group]
            sents = len({f.sentence_key for f in gu})
            what = {"hesitation": "possible hesitation pauses were noticed inside phrases",
                    "filler": "possible filler sounds were noticed between words",
                    "repetition": "possible repetitions or restarts were noticed"}[group]
            fluency_out = {"group": group, "origin": "counted", "text": f"In this reading, {what} {_times(n)}, in "
                           f"{sents} sentence{_s(sents)}.",
                           "counts": {"noticed": n, "sentences": sents}, "reliable_sentences": len(reliable),
                           "examples": _pick(gu, rcal.n_examples), "unit_ids": sorted(f.unit_id for f in gu)}
    if fluency_out is None and n_sent >= rcal.min_sentences and reliable and len(reliable) == len(readings) \
            and not any(f.group == "hesitation" for f in flu_units):
        fluency_note = {"origin": "counted", "basis": {"reliable_sentences": len(reliable)},
                        "text": "In this reading, no possible hesitation pauses were noticed inside phrases."}

    # 4. cautions (from this session's own coverage and evidence)
    cautions = []

    def caution(code, n):
        if n:
            cautions.append({"code": code, "text": CAUTIONS[code].format(**_plural(n))})
    caution("withheld_sentences", int(coverage.get("feedback_withheld", 0)) + int(coverage.get("feedback_withheld_containment", 0)))
    caution("unconfirmed_identity", int(coverage.get("awaiting_decision", 0)))
    caution("level_unmeasurable", sum(1 for r in readings if r.fluency_reliable is False))
    caution("low_confidence_analysis", sum(1 for r in readings if r.analysis_state == "low_confidence"))
    if readings and n_sent < rcal.min_sentences:
        caution("few_sentences", n_sent)
    caution("reference_accent", index.exclusions.get("reference_variant", 0))

    state = "insufficient" if n_sent < rcal.min_sentences else "feedback"
    cov_text = None
    if coverage:
        cov_text = (f"{coverage.get('recorded', n_sent)} of {coverage.get('sentences', n_sent)} sentences recorded; "
                    f"{n_sent} described here.")
    result = {
        "version": READING_VERSION, "scope": "this_reading", "session_id": session_id, "article_title": article_title,
        "generated_at": generated_at, "calibration": rcal.as_dict(), "knowledge_version": K.KNOWLEDGE_VERSION,
        "provenance": provenance,
        "coverage": {"sentences": coverage.get("sentences"), "recorded": coverage.get("recorded"),
                     "in_feedback": n_sent, "withheld": int(coverage.get("feedback_withheld", 0))
                     + int(coverage.get("feedback_withheld_containment", 0)),
                     "awaiting_decision": int(coverage.get("awaiting_decision", 0)), "text": cov_text},
        "improvement_areas": shown,
        "no_area_text": NO_AREA_TEXT if state == "feedback" and not shown else None,
        "absorbed": absorbed,
        "strengths": strengths, "fluency": fluency_out, "fluency_note": fluency_note, "cautions": cautions,
        "other_differences": other, "caveat": GENERAL_CAVEAT, "state": state,
    }
    issues = validate_reading_feedback(result, index.units, {r.reading_id for r in readings}, rcal)
    result["integrity"] = {"ok": not issues, "issues": issues}
    if issues:  # fail closed: nothing invalid is shown
        result.update(state="unavailable", improvement_areas=[], no_area_text=None, absorbed=[], strengths=[],
                      fluency=None, fluency_note=None)
    return result


# ----------------------------------------------------------------------
# Validation (fails closed): everything is recomputed from this reading's units
# ----------------------------------------------------------------------

def _mask(text: str) -> str:
    return re.sub(r"‘[^’]*’", "‘…’", text or "")


def texts(result: dict[str, Any]):
    for a in result.get("improvement_areas", []):
        for k in ("text", "evidence_text", "pattern_text", "rate_text", "counter_text", "order_text"):
            if a.get(k):
                yield a[k]
    if result.get("no_area_text"):
        yield result["no_area_text"]
    for s in result.get("strengths", []):
        yield s["text"]
    if result.get("fluency"):
        yield result["fluency"]["text"]
    if result.get("fluency_note"):
        yield result["fluency_note"]["text"]
    for c in result.get("cautions", []):
        yield c["text"]
    if (result.get("other_differences") or {}).get("note"):
        yield result["other_differences"]["note"]


def validate_reading_feedback(result: dict[str, Any], units: dict[str, Any], reading_ids: set[str],
                              rcal: ReadingCalibration = DEFAULT_READING) -> list[str]:
    issues = []
    if result.get("scope") != "this_reading":
        issues.append("scope must be this_reading")
    for forbidden in ("actions", "hypothesis", "practice", "decided_by", "pool", "tier"):
        if forbidden in result:
            issues.append(f"a description carries no {forbidden!r}")
    areas = result.get("improvement_areas", [])
    strengths = result.get("strengths", [])
    if len(strengths) > rcal.max_strengths:
        issues.append(f"more than {rcal.max_strengths} strengths")
    if result.get("state") == "feedback" and not areas and result.get("no_area_text") != NO_AREA_TEXT:
        issues.append("no qualifying improvement area must be said explicitly")
    if areas and result.get("no_area_text"):
        issues.append("the no-area message is shown beside improvement areas")
    own = [u for u in units.values() if getattr(u, "reading_id", None) in reading_ids and hasattr(u, "expected")]
    seen: set[str] = set()
    for a in areas + ([result["fluency"]] if result.get("fluency") else []):
        if "hypothesis" in a or "tier" in a:
            issues.append("an area exposes a coaching hypothesis or tier")
        ids = a.get("unit_ids", [])
        if seen & set(ids):
            issues.append("an observation is described twice")
        seen |= set(ids)
        bad = False
        for i in ids:
            u = units.get(i)
            if u is None or u.reading_id not in reading_ids:
                issues.append("an area cites an observation outside this reading")
                bad = True
                break
            if u.exclusion:
                issues.append("an area cites an excluded observation")
                bad = True
                break
        for e in a.get("examples", []) + a.get("counter_examples", []):
            if not _ref_ok(e) or units.get(e.get("unit_id")) is None or units[e["unit_id"]].reading_id not in reading_ids:
                issues.append("an example has no exact playback within this reading")
                break
        if bad or a is result.get("fluency"):
            continue
        issues += _check_area(a, [units[i] for i in ids], own, rcal)
    keys = [order_key(a) for a in areas]
    if keys != sorted(keys):
        issues.append("improvement areas are not in their deterministic order")
    excluded = _area_sounds(areas)
    notable = confusable_sounds()
    for s in strengths:
        snd = s.get("sound")
        occ = [u for u in own if u.expected == snd and u.quality in OCCURRENCE_QUALITIES]
        ok = sum(1 for u in occ if u.quality == "counter")
        if snd not in notable:
            issues.append(f"strength /{snd}/ is not a diagnostically useful dimension")
        if snd in excluded:
            issues.append(f"strength /{snd}/ is involved in an improvement area")
        if s["counts"] != {"occurrences": len(occ), "heard_as_expected": ok, "differences": len(occ) - ok}:
            issues.append(f"strength /{snd}/ counts do not match this reading")
        if len(occ) < rcal.strength_min_occurrences or not occ or ok / len(occ) < rcal.strength_min_share \
                or len(occ) - ok > rcal.strength_max_differences:
            issues.append(f"strength /{snd}/ does not meet the strength criteria")
    for text in texts(result):
        low = _mask(text).lower()
        for b in BANNED:
            if b in low:
                issues.append(f"not descriptive / not scoped ({b.strip()!r}): {text[:60]}")
    for t in [a.get("text", "") for a in areas] + [s["text"] for s in strengths]:
        if not t.startswith("In this reading, "):
            issues.append(f"not scoped to this reading: {t[:60]}")
    return issues


def _check_area(a: dict[str, Any], obs: list, own: list, rcal: ReadingCalibration) -> list[str]:
    issues = []
    band, kind = a.get("band"), a.get("kind")
    if band not in BANDS:
        return [f"unknown evidence band {band!r}"]
    expect_q = ("weak",) if band == "possible" else ("confident", "supporting")
    if any(u.quality not in expect_q for u in obs):
        issues.append("an area cites an observation of the wrong evidence quality")
    counts = area_counts(obs, own, a.get("scope") if band != "possible" else None, rcal)
    if any(a["counts"].get(k) != v for k, v in counts.items()):
        issues.append("an area's counts do not match its observations (clear and ambiguous are kept apart)")
    c = counts
    if band == "clear":
        minimum = rcal.lex_k_conf if kind == "LEXICAL" else rcal.k_conf
        if kind not in DESCRIPTIVE_KINDS or c["clear"] < minimum or c["clear_sentences"] < rcal.s_min \
                or c["clear_rate"] < rcal.rate_min:
            issues.append("a clear-evidence area does not meet path A")
    elif band == "mixed":
        if kind != "CONTRAST" or c["observations"] < rcal.mixed_min_observations or c["clear"] < rcal.mixed_min_clear \
                or c["sentences"] < rcal.mixed_min_sentences or c["real_words"] < rcal.mixed_min_real_words \
                or c["clear_rate"] < rcal.rate_min or c["direction_share"] is None or c["direction_share"] < rcal.c_min:
            issues.append("a mixed-evidence area does not meet path B")
    elif kind != "CLARITY" or c["real_words"] < rcal.clarity_min_words:
        issues.append("a possible area does not meet path C")
    if a.get("pattern_scope") != SCOPE_OF_KIND.get(kind) or not (a.get("pattern_text") or "").startswith("Pattern: "):
        issues.append("an area does not state its pattern scope (broad, context, word-specific, possible)")
    if band != "possible":
        if composition(c) not in (a.get("evidence_text") or ""):
            issues.append("an area does not disclose its evidence composition")
        top = c["rate_sound"]
        if top is None or not (a.get("rate_text") or "").startswith(
                f"Clear-evidence rate: {c['per_sound'][top]['clear']} of {c['per_sound'][top]['occurrences']} occurrences of /{top}/"):
            issues.append("an area's rate is not the clear-evidence rate")
        if not (a.get("text") or "").count(_times(c["observations"])):
            issues.append("an area's headline count is not its observed differences")
    return issues
