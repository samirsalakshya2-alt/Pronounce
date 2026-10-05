"""M9 level 3 — coaching targets: formation, consolidation (parsimony), and evidence tiers.

Target kinds form a CLOSED set; anything the evidence cannot support is listed in UNSUPPORTED and can never
become a target, an intervention or a sentence (contract.validate_coaching enforces it).

Consolidation keeps one owner per unit along the hierarchy member-contrast → family and broad → narrow:
replaced targets are recorded as absorbed, never kept alongside. Lateral overlaps between unrelated
candidates (e.g. a lexical candidate and a contrast containing the same word) are resolved by selection,
which removes covered units and re-gates the rest.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from pronunciation_lab.coaching import knowledge as K
from pronunciation_lab.coaching.calibration import DEFAULT, Calibration
from pronunciation_lab.coaching.patterns import Index, measure

TARGET_KINDS = ("CONTRAST", "SET", "CONDITIONED", "LEXICAL", "FLUENCY", "CLARITY")
SOUND_KINDS = ("CONTRAST", "SET", "CONDITIONED", "LEXICAL")
TIERS = ("established", "emerging", "insufficient")
CAPPED_AT_EMERGING = ("CLARITY",)   # weak signal until stronger evidence kinds exist

UNSUPPORTED = (
    {"category": "perception", "reason": "no listening test exists; whether you hear a difference is unknown",
     "needs": "a perception (listening) task"},
    {"category": "knowledge_vs_execution", "reason": "acoustics cannot tell 'not knowing' from 'not executing'",
     "needs": "targeted diagnostic tasks"},
    {"category": "articulator", "reason": "a microphone gives acoustic evidence only, never mouth movements",
     "needs": "articulatory measurement"},
    {"category": "stress", "reason": "stress is only taken from the dictionary, never observed", "needs": "prosody analysis"},
    {"category": "rhythm", "reason": "not analysed yet", "needs": "prosody analysis"},
    {"category": "intonation", "reason": "not analysed yet", "needs": "prosody analysis"},
    {"category": "rate_dependence", "reason": "sentence-level rates are too coarse for sound-level claims",
     "needs": "controlled slow / normal / fast readings of the same words"},
    {"category": "not_automated", "reason": "no evidence can establish a cognitive state", "needs": "none planned"},
    {"category": "swallowing_as_cause", "reason": "'not detected' does not prove a sound was absent",
     "needs": "stronger clarity / articulation acoustics"},
)

CONDITIONS: tuple[tuple[str, str, Callable[[dict], bool]], ...] = (
    ("final_cluster", "in consonant clusters at the end of words",
     lambda c: c.get("word_position") == "final" and bool(c.get("in_consonant_cluster"))),
    ("final", "at the end of words", lambda c: c.get("word_position") == "final"),
    ("initial", "at the start of words", lambda c: c.get("word_position") == "initial"),
    ("medial", "in the middle of words", lambda c: c.get("word_position") == "medial"),
    ("cluster", "in consonant clusters", lambda c: bool(c.get("in_consonant_cluster"))),
)
_COND = {cid: (label, pred) for cid, label, pred in CONDITIONS}


@dataclass
class Target:
    target_id: str
    kind: str
    sounds: tuple[str, ...]                       # expected sounds whose occurrences are in scope
    pairs: tuple[tuple[str, str], ...] = ()      # directed (expected, heard) pairs whose units are included
    unit_ids: list[str] = field(default_factory=list)
    family_id: str | None = None
    condition: str | None = None
    word: str | None = None
    group: str | None = None
    two_way: bool = False
    contradicted: bool = False
    knowledge: list[K.KnowledgeContribution] = field(default_factory=list)
    consolidation: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    tier: str = "insufficient"
    checks: dict[str, Any] = field(default_factory=dict)
    measures: dict[str, Any] = field(default_factory=dict)
    hypothesis: str = ""

    def with_units(self, unit_ids: list[str]) -> "Target":
        return replace(self, unit_ids=list(unit_ids), checks={}, measures={}, tier="insufficient",
                       knowledge=list(self.knowledge), consolidation=list(self.consolidation), notes=list(self.notes))

    def to_dict(self) -> dict[str, Any]:
        return {"target_id": self.target_id, "kind": self.kind, "sounds": list(self.sounds),
                "pairs": ["→".join(p) for p in self.pairs], "family_id": self.family_id, "condition": self.condition,
                "condition_label": _COND[self.condition][0] if self.condition else None, "word": self.word,
                "group": self.group, "two_way": self.two_way, "hypothesis": self.hypothesis, "origin": "inferred",
                "tier": self.tier, "checks": self.checks, "measures": self.measures, "notes": self.notes,
                "knowledge_contributions": [k.to_dict() for k in self.knowledge],
                "consolidation_record": self.consolidation, "unit_ids": list(self.unit_ids)}


# ----------------------------------------------------------------------
# Scope: which units of the pool a target's counts refer to
# ----------------------------------------------------------------------

def _in_scope(t: Target, u) -> bool:
    if getattr(u, "expected", None) not in t.sounds:
        return False
    if t.condition and not _COND[t.condition][1](u.context):
        return False
    if t.word and u.lexical_key != t.word:
        return False
    return True


def counter_ids(t: Target, index: Index) -> list[str]:
    if t.kind not in SOUND_KINDS:
        return []
    return [i for s in t.sounds for i in index.counter_by_expected.get(s, []) if _in_scope(t, index.units[i])]


def _confident_deviations_in_scope(t: Target, index: Index) -> int:
    return sum(1 for u in index.units.values() if getattr(u, "quality", None) == "confident"
               and getattr(u, "outcome", None) == "heard_other" and _in_scope(t, u))


def _reach(t: Target, index: Index, pool_sentences: int) -> float | None:
    if t.kind == "FLUENCY":
        sentences = {index.units[i].sentence_key for i in t.unit_ids}
        return round(100.0 * len(sentences) / pool_sentences, 1) if pool_sentences else None
    if t.kind == "CLARITY" or not index.exposure_words:
        return None
    occ = set()
    for u in index.units.values():
        if getattr(u, "expected", None) is not None and _in_scope(t, u):
            occ.add((u.reading_id, u.word_index))
    return round(100.0 * len(occ) / index.exposure_words, 1)


# ----------------------------------------------------------------------
# Tiers (target-level gates) — re-runnable on any subset of units (selection re-gates residuals)
# ----------------------------------------------------------------------

def evaluate(t: Target, index: Index, cal: Calibration = DEFAULT, pool_sentences: int = 0) -> Target:
    units = index.get(t.unit_ids)
    m = measure(units)
    counters = counter_ids(t, index)
    conf = m["confident"]
    checks: dict[str, Any] = {}
    if t.kind in ("CONTRAST", "SET", "CONDITIONED"):
        denom = _confident_deviations_in_scope(t, index)
        conc = round(conf / denom, 3) if denom else None
        checks = {
            "confident": (conf, cal.k_conf, conf >= cal.k_conf),
            "words": (m["conf_words"], cal.w_min, m["conf_words"] >= cal.w_min),
            "sentences": (m["conf_sentences"], cal.s_min, m["conf_sentences"] >= cal.s_min),
            "sessions": (m["conf_sessions"], cal.sessions_min, m["conf_sessions"] >= cal.sessions_min),
            "concentration": (conc, cal.c_min, conc is not None and conc >= cal.c_min),
            "not_contradicted": (not t.contradicted, True, not t.contradicted),
        }
    elif t.kind == "LEXICAL":
        denom = _confident_deviations_in_scope(t, index)
        conc = round(conf / denom, 3) if denom else None
        readings = len({u.reading_id for u in units if u.quality == "confident"})
        checks = {
            "confident": (conf, cal.lex_k_conf, conf >= cal.lex_k_conf),
            "readings_of_word": (readings, cal.lex_min_readings, readings >= cal.lex_min_readings),
            "sessions": (m["conf_sessions"], cal.sessions_min, m["conf_sessions"] >= cal.sessions_min),
            "relevance": (len(index.word_sentences.get(t.word, ())), cal.lex_rel,
                          len(index.word_sentences.get(t.word, ())) >= cal.lex_rel),
            "not_fragment": (not any(u.fragment_suspect for u in units), True,
                             not any(u.fragment_suspect for u in units)),
        }
    else:  # FLUENCY, CLARITY
        conc = None
        n = m["confident"] if t.kind == "FLUENCY" else len(units)
        checks = {
            "units": (n, cal.k_conf, n >= cal.k_conf),
            "sentences": (m["sentences"], cal.s_min, m["sentences"] >= cal.s_min),
            "sessions": (m["sessions"], cal.sessions_min, m["sessions"] >= cal.sessions_min),
        }
    compared = [u for u in units if getattr(u, "engine_agreement", "not_compared") != "not_compared"]
    differ = sum(1 for u in compared if u.engine_agreement == "differ")
    share = round(differ / len(compared), 3) if compared else None
    if t.kind in SOUND_KINDS:
        checks["engine_agreement"] = (share, cal.disagree_max, share is None or share <= cal.disagree_max)
    passed = all(c[2] for c in checks.values())
    evidence_n = conf + m["supporting"] + (len(units) if t.kind == "CLARITY" else 0)
    if passed and t.kind not in CAPPED_AT_EMERGING:
        tier = "established"
    elif (conf >= 1 or t.kind == "CLARITY") and evidence_n >= cal.k_conf:
        tier = "emerging"
    else:
        tier = "insufficient"
    reasons = [k for k, c in checks.items() if not c[2]]
    if t.kind in CAPPED_AT_EMERGING and passed:
        reasons.append("capped: a weak signal until stronger clarity evidence exists")
    t.checks = {k: {"value": v, "required": r, "ok": ok} for k, (v, r, ok) in checks.items()}
    t.checks["_failed"] = reasons
    t.measures = {**m, "counter": len(counters), "concentration": conc, "reach": _reach(t, index, pool_sentences),
                  "deviation_share": round(conf / (conf + len(counters)), 3) if (conf + len(counters)) else None,
                  "engine_differ_share": share, "engine_compared": len(compared)}
    t.tier = tier
    t.hypothesis = hypothesis(t)
    return t


def hypothesis(t: Target) -> str:
    """Inferred-level wording only: 'appears', 'was often heard as' — never a cause."""
    if t.kind == "CONTRAST":
        a, b = t.pairs[0]
        if t.two_way:
            return f"/{a}/ and /{b}/ were often heard as each other in your recent readings."
        return f"/{a}/ was often heard as /{b}/ in your recent readings."
    if t.kind == "SET":
        fam = K.family(t.family_id)
        return f"The sounds of {fam.title} were often heard as one another in your recent readings."
    if t.kind == "CONDITIONED":
        return (f"/{'/, /'.join(t.sounds)}/ appears less stable {_COND[t.condition][0]} than elsewhere in your "
                f"recent readings.")
    if t.kind == "LEXICAL":
        return (f"‘{t.word}’ was heard differently in several readings, while /{'/, /'.join(t.sounds)}/ is mostly "
                f"heard as expected in other words.")
    if t.kind == "FLUENCY":
        return {"hesitation": "Possible hesitation pauses recur inside phrases in your recent readings.",
                "filler": "Possible filler sounds recur between words in your recent readings.",
                "repetition": "Possible repetitions or restarts recur in your recent readings."}[t.group]
    return ("Several final consonants were not clearly detected in your recent readings (not detected does not "
            "prove a sound was absent).")


# ----------------------------------------------------------------------
# Formation and consolidation
# ----------------------------------------------------------------------

def _contrast(index: Index, pairs: list[tuple[str, str]]) -> Target:
    ids = sorted(i for p in pairs for i in index.contrasts[p].unit_ids)
    a, b = pairs[0]
    conf_dir = {p: sum(1 for i in index.contrasts[p].unit_ids if index.units[i].quality == "confident") for p in pairs}
    two_way = len(pairs) == 2 and all(conf_dir[p] >= 1 for p in pairs)
    ordered = tuple(sorted(pairs, key=lambda p: -conf_dir[p]))
    return Target(target_id="contrast:" + "~".join(sorted({a, b})), kind="CONTRAST",
                  sounds=tuple(sorted({p[0] for p in pairs})), pairs=ordered, unit_ids=ids, two_way=two_way)


def _directed(index: Index, pair: tuple[str, str]) -> Target:
    return Target(target_id="contrast:" + "→".join(pair), kind="CONTRAST", sounds=(pair[0],), pairs=(pair,),
                  unit_ids=sorted(index.contrasts[pair].unit_ids))


def _passes_except(t: Target, *skip: str) -> bool:
    return all(c["ok"] for k, c in t.checks.items() if not k.startswith("_") and k not in skip)


def _conf(index: Index, ids) -> list:
    return [index.units[i] for i in ids if index.units[i].quality == "confident"]


def form_targets(index: Index, cal: Calibration = DEFAULT, pool_sentences: int = 0) -> tuple[list[Target], list[dict]]:
    """Return (candidate targets, absorbed records). Candidates may overlap only laterally (resolved by selection)."""
    ev = lambda t: evaluate(t, index, cal, pool_sentences)  # noqa: E731
    absorbed: list[dict[str, Any]] = []

    # 1. contrasts: directed patterns paired into unordered contrasts
    by_pair: dict[frozenset, list[tuple[str, str]]] = defaultdict(list)
    for p in sorted(index.contrasts):
        by_pair[frozenset(p)].append(p)
    contrasts = {key: ev(_contrast(index, sorted(ps))) for key, ps in by_pair.items()}

    # 2. families (declared knowledge proposes; evidence must pass all five conditions)
    sound_targets: list[Target] = []
    covered: set[tuple[str, str]] = set()          # directed pairs owned by a merged family
    for fam in K.families():
        if fam.mode == "set":
            members = [t for key, t in contrasts.items() if not covered & set(t.pairs)
                       and all(fam.covers(*p) for p in t.pairs)]
        else:
            members = [ev(_directed(index, p)) for p in sorted(index.contrasts) if fam.covers(*p)
                       and p not in covered]
        if not members:
            continue
        # Condition 1: every gate on its own evidence, except concentration — a sound with two systematic
        # competitors inside one family (e.g. /ɛ/ heard as /eɪ/ and as /ɪ/) splits its deviations between them,
        # so concentration is measured where the hypothesis is: on the family (the merged target below).
        passing = [m for m in members if _passes_except(m, "concentration")]
        record: dict[str, Any] = {"family": fam.id, "origin": "knowledge proposal",
                                  "members": [m.target_id for m in members],
                                  "members_passing_alone": [m.target_id for m in passing]}
        if len(passing) < 2:
            record["decision"] = "not merged: fewer than two members pass the gates on their own"
            absorbed.append(record)
            continue
        contradicted = []
        if fam.mode == "pairs":
            for m in passing:
                (e, h), = m.pairs
                fwd = len(_conf(index, index.contrasts[(e, h)].unit_ids))
                rev = len(_conf(index, index.contrasts.get((h, e), _EMPTY).unit_ids))
                if rev >= fwd:
                    contradicted.append(f"{h}→{e} ({rev}) opposes {e}→{h} ({fwd})")
        union_ids = sorted({i for m in passing for i in m.unit_ids})
        union_conf = len(_conf(index, union_ids))
        largest = max(len(_conf(index, m.unit_ids)) for m in passing)
        conditions = {
            "1_each_member_passes_alone": True,
            "2_declared_relation_and_coherent": not contradicted,
            "3_one_intervention_applies": all(m.kind == "CONTRAST" for m in passing),
            "4_explains_clearly_more": {"union_confident": union_conf, "largest_member": largest,
                                        "required_ratio": cal.m_merge, "ok": union_conf >= cal.m_merge * largest},
            "5_not_contradicted": {"contradictions": contradicted, "ok": not contradicted},
        }
        ok = all(v if isinstance(v, bool) else v["ok"] for v in conditions.values())
        record["conditions"] = conditions
        if not ok:
            record["decision"] = "not merged: a merge condition failed"
            absorbed.append(record)
            continue
        pairs = tuple(p for m in passing for p in m.pairs)
        st = Target(target_id=f"set:{fam.id}", kind="SET", sounds=tuple(sorted({p[0] for p in pairs})), pairs=pairs,
                    unit_ids=union_ids, family_id=fam.id,
                    knowledge=[K.KnowledgeContribution(f"family:{fam.id}", "proposed family", K.FAMILY_TEXT)])
        st.consolidation.append(record | {"decision": "merged"})
        for m in passing:
            covered.update(m.pairs)
            absorbed.append({"target": m.target_id, "absorbed_into": st.target_id, "reason": "member of a merged family"})
        sound_targets.append(ev(st))
    for key, t in contrasts.items():
        remaining = [p for p in t.pairs if p not in covered]
        if len(remaining) == len(t.pairs):
            sound_targets.append(t)
        elif remaining:  # a directional family took one direction; the other keeps its own evidence
            sound_targets.append(ev(_contrast(index, sorted(remaining))))

    # 3. narrowing: lexical concentration, then context (always against counter-evidence)
    narrowed: list[Target] = []
    for t in sound_targets:
        narrowed.extend(_narrow(t, index, cal, ev, absorbed))

    # 4. independent lexical candidates (lateral; a selected broader target absorbs them)
    have = {t.target_id for t in narrowed}
    for t in _lexical_candidates(index, cal, ev):
        if t.target_id not in have:
            narrowed.append(t)

    # 5. fluency and clarity
    for group, p in sorted(index.fluency.items()):
        narrowed.append(ev(Target(f"fluency:{group}", "FLUENCY", (), unit_ids=sorted(p.unit_ids), group=group)))
    for key, p in sorted(index.clarity.items()):
        narrowed.append(ev(Target("clarity:final_consonant", "CLARITY", (), unit_ids=sorted(p.unit_ids),
                                  group="final_consonant")))
    return sorted(narrowed, key=lambda t: t.target_id), absorbed


class _Empty:
    unit_ids: list[str] = []


_EMPTY = _Empty()


def _narrow(t: Target, index: Index, cal: Calibration, ev, absorbed: list) -> list[Target]:
    conf_units = _conf(index, t.unit_ids)
    # lexical concentration
    by_word = Counter(u.lexical_key for u in conf_units)
    if conf_units and by_word:
        top = [w for w, _ in by_word.most_common(cal.lex_max_words)]
        share = sum(by_word[w] for w in top) / len(conf_units)
        outside = len(conf_units) - sum(by_word[w] for w in top)
        word_ok = all(by_word[w] >= cal.lex_k_conf and len({u.reading_id for u in conf_units if u.lexical_key == w})
                      >= cal.lex_min_readings for w in top)
        if share >= cal.lex_share and outside < cal.k_conf and word_ok:
            lex = []
            for w in top:
                lt = ev(Target(f"lexical:{w}:{'|'.join(t.sounds)}", "LEXICAL", t.sounds, t.pairs,
                               unit_ids=[i for i in t.unit_ids if index.units[i].lexical_key == w], word=w,
                               family_id=t.family_id, knowledge=list(t.knowledge)))
                lt.consolidation.append({"narrowed_from": t.target_id, "test": "lexical",
                                         "word_share": round(by_word[w] / len(conf_units), 3),
                                         "top_words_share": round(share, 3), "required": cal.lex_share,
                                         "confident_outside_words": outside})
                lex.append(lt)
            absorbed.append({"target": t.target_id, "absorbed_into": [x.target_id for x in lex],
                             "reason": "lexical concentration", "top_words_share": round(share, 3)})
            return lex
    # context
    best = None
    occurrences = [index.units[i] for i in t.unit_ids] + [index.units[i] for i in counter_ids(t, index)]
    for cid, label, pred in CONDITIONS:
        conf_in = [u for u in conf_units if pred(u.context)]
        conf_out = [u for u in conf_units if not pred(u.context)]
        occ_in = [u for u in occurrences if (u.quality in ("confident", "counter")) and pred(u.context)]
        occ_out = [u for u in occurrences if (u.quality in ("confident", "counter")) and not pred(u.context)]
        if not conf_in:
            continue
        if len(conf_in) == len(conf_units) and len(occ_out) < cal.ctx_min_out:
            t.notes.append(f"only observed {label}; there is not enough evidence elsewhere to tell whether this "
                           "is specific to that context")
            continue
        if len(occ_in) < cal.ctx_min_in or len(occ_out) < cal.ctx_min_out:
            continue
        if len(occ_in) > cal.ctx_max_share * (len(occ_in) + len(occ_out)):
            continue  # the condition covers most occurrences anyway (e.g. vowels in the middle of words): not specific
        s_in, s_out = len(conf_in) / len(occ_in), len(conf_out) / len(occ_out)
        meaningful = (len(conf_in) >= cal.k_conf and len({u.session_id for u in conf_in}) >= cal.sessions_min
                      and (s_in >= cal.ctx_ratio * s_out if s_out else s_in > 0))
        if meaningful:
            ratio = s_in / s_out if s_out else float("inf")
            cand = (ratio, cid, label, len(conf_in), len(occ_in), len(conf_out), len(occ_out), s_in, s_out)
            if best is None or cand[0] > best[0]:
                best = cand
    if best is None:
        return [t]
    ratio, cid, label, ci, oi, co, oo, s_in, s_out = best
    stats = {"condition": cid, "inside": {"confident": ci, "occurrences": oi, "share": round(s_in, 3)},
             "outside": {"confident": co, "occurrences": oo, "share": round(s_out, 3)}, "required_ratio": cal.ctx_ratio}
    if co >= cal.k_conf:
        t.notes.append(f"heard differently more often {label} ({ci}/{oi} vs {co}/{oo} elsewhere), but it also "
                       "recurs elsewhere, so the broader target is kept")
        t.consolidation.append({"test": "context", "decision": "kept broad"} | stats)
        return [t]
    pred = _COND[cid][1]
    ct = ev(Target(f"conditioned:{cid}:{t.target_id}", "CONDITIONED", t.sounds, t.pairs,
                   unit_ids=[i for i in t.unit_ids if pred(index.units[i].context)], condition=cid,
                   family_id=t.family_id, knowledge=list(t.knowledge)))
    ct.consolidation.append({"narrowed_from": t.target_id, "test": "context", "decision": "narrowed"} | stats)
    absorbed.append({"target": t.target_id, "absorbed_into": ct.target_id, "reason": "context concentration"} | stats)
    return [ct]


def _lexical_candidates(index: Index, cal: Calibration, ev) -> list[Target]:
    """A word heard differently across readings while its sound is mostly stable in other words."""
    out = []
    by_sound_word: dict[tuple[str, str], list] = defaultdict(list)
    for u in index.units.values():
        if getattr(u, "outcome", None) == "heard_other" and u.quality in ("confident", "supporting"):
            by_sound_word[(u.expected, u.lexical_key)].append(u)
    for (sound, word), units in sorted(by_sound_word.items()):
        conf = [u for u in units if u.quality == "confident"]
        if len(conf) < cal.lex_k_conf:
            continue
        counters = [index.units[i] for i in index.counter_by_expected.get(sound, [])]
        in_word = len(conf) / (len(conf) + sum(1 for c in counters if c.lexical_key == word))
        other_conf = sum(1 for u in index.units.values() if getattr(u, "outcome", None) == "heard_other"
                         and u.quality == "confident" and u.expected == sound and u.lexical_key != word)
        other_occ = other_conf + sum(1 for c in counters if c.lexical_key != word)
        if other_occ < cal.ctx_min_out:
            continue
        if other_conf / other_occ > in_word / cal.ctx_ratio:
            continue  # the sound is not mostly stable in other words: not a lexical problem
        pairs = tuple(sorted({(u.expected, u.heard) for u in units}))
        t = ev(Target(f"lexical:{word}:{sound}", "LEXICAL", (sound,), pairs, unit_ids=sorted(u.unit_id for u in units),
                      word=word))
        t.consolidation.append({"test": "lexical", "in_word_share": round(in_word, 3),
                                "other_words": {"confident": other_conf, "occurrences": other_occ}})
        out.append(t)
    return out
