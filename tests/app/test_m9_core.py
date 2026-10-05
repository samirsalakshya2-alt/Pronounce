"""M9 pure core: calibration, knowledge, evidence, pool, patterns, targets, leverage, selection, plan, contract.

Synthetic, deterministic evidence built with m9helpers (no audio, no engines).
"""

import pytest

from pronunciation_lab.coaching import knowledge as K
from pronunciation_lab.coaching.calibration import DEFAULT, ORDERING, Calibration


# ----------------------------------------------------------------------
# Layer 1 — calibration and declared knowledge
# ----------------------------------------------------------------------

def test_calibration_is_central_versioned_and_reported():
    d = DEFAULT.as_dict()
    assert d["version"].startswith("m9-cal") and d["max_actions"] == 3 and d["max_fluency"] == 1
    assert tuple(d["ordering"]) == ORDERING and ORDERING[:3] == ("tier", "breadth_sentences", "recurrence_sessions")
    custom = Calibration(k_conf=6)
    assert custom.k_conf == 6 and DEFAULT.k_conf != 6  # frozen dataclass: no shared mutation


def test_plausibility_keeps_broad_class_and_declared_exceptions():
    assert K.plausible("ɛ", "eɪ") and K.plausible("s", "ʃ") and K.plausible("ɚ", "ɹ") and K.plausible("əl", "l")
    assert not K.plausible("iː", "t") and not K.plausible("t", "ɑː") and not K.plausible("d", "eɪ")
    assert not K.plausible("s", None)


def test_families_cover_only_declared_relations():
    ladder = K.family("front_vowel_ladder")
    assert ladder.covers("ɛ", "eɪ") and ladder.covers("eɪ", "ɪ") and not ladder.covers("ɛ", "ɚ")
    dev = K.family("devoicing")
    assert dev.covers("z", "s") and not dev.covers("s", "z") and dev.reverse("s", "z")
    assert not K.family("sibilant_place").covers("s", "z")  # voicing is not the same practice as place


def test_knowledge_contributions_are_labelled_and_reuse_m4_guidance():
    g = K.guidance_for([("ɪ", "iː"), ("iː", "ɪ"), ("ɛ", "eɪ")])
    assert [c.entry for c in g] == ["guidance:ɪ~iː"]       # deduplicated; no guidance invented for ɛ~eɪ
    assert all(c.origin == "knowledge" for c in g)
    d = K.describe()
    assert d["origin"] == "knowledge" and {f["id"] for f in d["families"]} >= {"front_vowel_ladder", "devoicing"}


def test_reference_and_function_word_knowledge_is_m4s():
    assert K.reference_variant("ᵻ", "ɪ") and K.reference_variant("ɚ", "ə") and not K.reference_variant("ɛ", "eɪ")
    assert K.function_word("The") and not K.function_word("collaboration")


# ----------------------------------------------------------------------
# Layer 2 — evidence normalisation and unit quality
# ----------------------------------------------------------------------

import m9helpers as H  # noqa: E402

from pronunciation_lab.coaching import evidence as E  # noqa: E402


def _units(*obs, reading=None, **kw):
    units, fl = E.normalise(H.inp(reading or H.rd(1), *obs, **kw))
    return units, fl


def test_unit_quality_follows_m4_confidence_and_never_upgrades():
    units, _ = _units(H.sub("best", "ɛ", "eɪ"), H.sub("bet", "ɛ", "eɪ", conf="moderate"), H.amb("set", "ɛ", "eɪ"),
                      H.omit("hand", "d"), H.ok("bed", "ɛ"))
    assert [u.quality for u in units] == ["confident", "confident", "supporting", "weak", "counter"]
    assert [u.outcome for u in units] == ["heard_other", "heard_other", "heard_other", "not_detected", "as_expected"]
    assert units[0].heard == "eɪ" and units[2].heard == "eɪ" and "support only" in units[2].reasons[0]


@pytest.mark.parametrize("obs, reason", [
    (H.sub("roses", "ᵻ", "ɪ"), "reference_variant"),
    (H.sub("the", "ə", "ɪ"), "function_word_variant"),
    (H.sub("leaders", "iː", "t"), "implausible_pair"),
    (H.uninterpreted("evening", "iː"), "not_interpreted"),
    (H.sub("tooling", "t", "d", prev="d"), "neighbour_shift"),
    (H.sub("behind", "n", "d", nxt="d"), "neighbour_shift"),
])
def test_uncoachable_evidence_is_excluded_with_a_reason_never_deleted(obs, reason):
    units, _ = _units(obs)
    assert len(units) == 1 and units[0].quality == "excluded" and units[0].exclusion == reason
    assert reason in E.EXCLUSIONS


def test_consonants_in_function_words_are_not_function_word_variants():
    units, _ = _units(H.sub("the", "ð", "d"))
    assert units[0].quality == "confident"


def test_m5_natural_reduction_and_merge_are_excluded():
    units, _ = _units(H.sub("water", "t", "d"), H.omit("want", "t"), H.sub("ten", "n", "m"),
                      reductions=[H.m5("o000"), H.m5("o001", natural=False, merge=True),
                                  H.m5("o002", category="possible_coarticulation", natural=False)])
    assert [u.exclusion for u in units] == ["context_predicted", "merge_suspect", "context_predicted"]
    assert units[0].m5["natural_possible"] and units[1].m5["merge_suspect"]
    # a substitution flagged as a possible decoding merge (e.g. "need to": [d] for /t/) is not coached either
    subst, _ = _units(H.sub("to", "t", "d"), reductions=[H.m5("o000", category="ambiguous", natural=False, merge=True)])
    assert subst[0].exclusion == "merge_suspect"


def test_low_confidence_analysis_downgrades_to_support():
    units, _ = _units(H.sub("best", "ɛ", "eɪ"), reading=H.rd(1, analysis="low_confidence"))
    assert units[0].quality == "supporting" and "low-confidence" in units[0].reasons[0]


def test_units_keep_exact_audio_and_provenance():
    r = H.rd(7, session=3)
    units, _ = _units(H.sub("best", "ɛ", "eɪ", wi=2), reading=r)
    u = units[0]
    assert u.unit_id == f"{r.session_id}:{r.attempt_id}:{r.job_id}:o000" and u.reading_id == r.reading_id
    a = u.audio
    assert (a["session_id"], a["attempt_id"], a["job_id"], a["timeline"]) == (r.session_id, r.attempt_id, r.job_id,
                                                                             "analysis_wav")
    assert a["play_ms"] == [240.0, 420.0] and a["span_ms"] == [300.0, 360.0] and a["observation_id"] == "o000"
    assert a["url"] == f"/api/sessions/{r.session_id}/attempts/{r.attempt_id}/audio" and a["word"] == "best"


def test_fragments_and_short_tokens_are_flagged():
    units, _ = _units(H.sub("bility", "ɪ", "eɪ"), H.sub("jcb", "eɪ", "ɪ"), H.sub("an", "æ", "ɛ"),
                      H.sub("possible", "ɪ", "eɪ"), fragments={"bility", "possi"})
    assert [u.fragment_suspect for u in units] == [True, False, True, False]
    assert E.fragment_words("possi- bility and devel-\nopment") == {"possi", "bility", "devel", "opment"}


def test_engine_agreement_is_carried_not_resolved():
    units, _ = _units(H.sub("best", "ɛ", "eɪ"), H.sub("bed", "ɛ", "eɪ"), agreement={"o000": "differ"})
    assert [u.engine_agreement for u in units] == ["differ", "not_compared"]
    assert all(u.quality == "confident" for u in units)  # disagreement acts at target level, never here


def test_fluency_units_only_noticed_inside_phrases_and_reliable():
    fl = H.fluency(H.hesitation(), ("PAUSE", "unusually_long", "it", "then", "at a phrase boundary"),
                   ("FILLER", None, "then", "change", "between words"), ("PAUSE", "brief_within_phrase", "a", "b",
                                                                          "between words inside a phrase"))
    _, f = _units(H.ok("bed", "ɛ"), fluency=fl)
    assert [(x.group, x.quality) for x in f] == [("hesitation", "confident"), ("filler", "confident")]
    _, f2 = _units(H.ok("bed", "ɛ"), fluency=H.fluency(H.hesitation(), reliable=False))
    assert [(x.quality, x.exclusion) for x in f2] == [("excluded", "unreliable_level")]
    assert f[0].audio["context_ms"] == [250.0, 250.0] and f[0].audio["kind"] == "fluency"


def test_text_keys_identify_sentences_across_pasted_copies():
    assert E.text_key("Think about it,  then CHANGE.") == E.text_key("think about it then change")
    assert E.lexical_key("Collaboration’s") != "" and E.lexical_key("Best,") == "best"


# ----------------------------------------------------------------------
# Layer 3 — evidence pool
# ----------------------------------------------------------------------

from pronunciation_lab.coaching.pool import select_pool  # noqa: E402


def _pool_inputs(n, sessions=2, engine_of=lambda k: H.ENGINE):
    return [H.inp(H.rd(k, session=1 + k % sessions, engine=engine_of(k)), H.ok("bed", "ɛ")) for k in range(n)]


def test_pool_keeps_the_most_recent_readings_of_one_engine():
    inputs = _pool_inputs(50)
    pool = select_pool(inputs, Calibration(pool_n_max=40))
    assert len(pool.inputs) == 40 and pool.engine == H.ENGINE and pool.sufficient
    kept = {i.reading.reading_id for i in pool.inputs}
    newest = sorted(inputs, key=lambda i: i.reading.recorded_at)[-40:]
    assert kept == {i.reading.reading_id for i in newest}
    assert pool.exclusions["outside_window"] == 10
    mixed = _pool_inputs(12, engine_of=lambda k: "openpronounce" if k % 3 == 0 else H.ENGINE)
    p2 = select_pool(mixed)
    newest_engine = max(mixed, key=lambda i: i.reading.recorded_at).reading.engine
    assert p2.engine == newest_engine and all(i.reading.engine == newest_engine for i in p2.inputs)
    assert p2.exclusions["other_engine"] == sum(i.reading.engine != newest_engine for i in mixed)


def test_pool_sufficiency_floor():
    assert select_pool(_pool_inputs(5)).insufficient_code == "history_too_small"
    assert select_pool(_pool_inputs(12, sessions=1)).insufficient_code == "single_session"
    assert select_pool([]).insufficient_code == "history_too_small" and select_pool([]).engine is None


def test_pool_fingerprint_identifies_the_evidence_and_calibration():
    a = select_pool(_pool_inputs(12))
    assert a.fingerprint == select_pool(_pool_inputs(12)).fingerprint
    assert a.fingerprint != select_pool(_pool_inputs(13)).fingerprint
    assert a.fingerprint != select_pool(_pool_inputs(12), Calibration(k_conf=DEFAULT.k_conf + 1)).fingerprint


def test_optional_age_guard_is_off_by_default_and_explicit_when_on():
    inputs = _pool_inputs(12)
    assert len(select_pool(inputs, now="2027-01-01T00:00:00+00:00").inputs) == 12
    aged = select_pool(inputs, Calibration(pool_max_age_days=3), now="2026-10-06T00:00:00+00:00")
    assert len(aged.inputs) < 12 and aged.exclusions["outside_window"] == 12 - len(aged.inputs)


# ----------------------------------------------------------------------
# Layer 4 — patterns (counting only)
# ----------------------------------------------------------------------

from pronunciation_lab.coaching.patterns import build_index, measure  # noqa: E402


def _index(*inputs):
    units, fl = [], []
    for i in inputs:
        u, f = E.normalise(i)
        units += u
        fl += f
    return build_index(units, fl)


def test_patterns_count_direction_counter_evidence_and_exposure():
    idx = _index(H.inp(H.rd(1), H.sub("best", "ɛ", "eɪ", wi=0), H.amb("bet", "ɛ", "eɪ", wi=1), H.ok("bed", "ɛ", wi=2),
                       H.sub("based", "eɪ", "ɛ", wi=3), H.sub("leaders", "iː", "t", wi=4)),
                 H.inp(H.rd(2, session=2), H.sub("best", "ɛ", "eɪ"), H.ok("set", "ɛ", n=1, wi=1)))
    p = idx.contrasts[("ɛ", "eɪ")]
    assert p.kind == "contrast" and len(p.unit_ids) == 3            # 2 confident + 1 supporting
    m = measure(idx.get(p.unit_ids))
    assert (m["confident"], m["supporting"], m["words"], m["sentences"], m["readings"], m["sessions"]) == (2, 1, 2, 2, 2, 2)
    assert ("eɪ", "ɛ") in idx.contrasts and ("iː", "t") not in idx.contrasts   # implausible never forms a pattern
    assert len(idx.counter_by_expected["ɛ"]) == 2
    assert idx.conf_dev_by_expected["ɛ"] == {"eɪ": 2}               # supporting excluded from concentration
    assert idx.exposure_words == 7 and len(idx.exposure_by_sound["ɛ"]) == 5
    assert idx.exclusions["implausible_pair"] == 1
    assert idx.word_sentences["best"] == {H.rd(1).sentence_key, H.rd(2).sentence_key}
    assert p.to_dict(idx)["origin"] == "counted"


def test_fluency_and_clarity_patterns_are_separate_kinds():
    idx = _index(H.inp(H.rd(1), H.omit("hand", "d"), H.omit("hand", "d", position="medial", wi=1),
                       fluency=H.fluency(H.hesitation(), ("FILLER", None, "a", "b", "between words"))))
    assert set(idx.fluency) == {"hesitation", "filler"}
    assert list(idx.clarity) == [("consonant", "final")] and len(idx.clarity[("consonant", "final")].unit_ids) == 1
    assert not idx.contrasts


# ----------------------------------------------------------------------
# Layer 5 — targets: formation, consolidation, tiers
# ----------------------------------------------------------------------

from pronunciation_lab.coaching.targets import UNSUPPORTED, TARGET_KINDS, form_targets  # noqa: E402

W = H.words("best", "spread", "level", "spell", "test", "never", "general", "letter")


def _targets(inputs, cal=DEFAULT):
    units, fl = [], []
    for i in inputs:
        u, f = E.normalise(i)
        units += u
        fl += f
    idx = build_index(units, fl)
    ts, absorbed = form_targets(idx, cal, pool_sentences=len({i.reading.sentence_key for i in inputs}))
    return {t.target_id: t for t in ts}, absorbed, idx


def test_one_strong_one_way_contrast_is_established():
    ts, _, _ = _targets(H.scatter([H.subs("ɛ", "eɪ", 8, W), H.ok("bed", "ɛ", 20)]))
    t = ts["contrast:eɪ~ɛ"]
    assert t.kind == "CONTRAST" and not t.two_way and t.tier == "established", t.checks
    assert t.hypothesis == "/ɛ/ was often heard as /eɪ/ in your recent readings."
    assert t.measures["confident"] == 8 and t.measures["counter"] == 20 and t.measures["concentration"] == 1.0


def test_two_way_confusion_is_one_contrast_with_both_directions():
    ts, _, _ = _targets(H.scatter([H.subs("eɪ", "ɪ", 5, H.words("based", "make", "day", "lake")),
                                   H.subs("ɪ", "eɪ", 4, H.words("sit", "lift", "bit", "list"))]))
    t = ts["contrast:eɪ~ɪ"]
    assert t.two_way and set(t.pairs) == {("eɪ", "ɪ"), ("ɪ", "eɪ")} and t.tier == "established"
    assert t.hypothesis == "/eɪ/ and /ɪ/ were often heard as each other in your recent readings."


def test_weak_or_ambiguous_evidence_never_establishes():
    ts, _, _ = _targets(H.scatter([H.amb(w, "ɛ", "eɪ") for w in W(20)]))
    t = ts["contrast:eɪ~ɛ"]
    assert t.measures["confident"] == 0 and t.measures["supporting"] == 20 and t.tier == "insufficient"


def test_single_session_evidence_is_not_established():
    ts, _, _ = _targets(H.scatter([H.subs("ɛ", "eɪ", 8, W)], sessions=1))
    t = ts["contrast:eɪ~ɛ"]
    assert t.tier == "emerging" and "sessions" in t.checks["_failed"]


def test_scattered_deviations_fail_concentration():
    comp = ["eɪ", "ɪ", "æ", "ʌ", "ɑː", "ɔ", "eɪ", "ɪ", "æ", "ʌ"]
    ts, _, _ = _targets(H.scatter([H.sub(w, "ɛ", h) for w, h in zip(W(10), comp)]))
    assert all(t.tier != "established" for t in ts.values())
    assert ts["contrast:eɪ~ɛ"].checks["concentration"]["ok"] is False


def test_family_merge_requires_all_five_conditions():
    ts, absorbed, _ = _targets(H.scatter([H.subs("ɛ", "eɪ", 6, W), H.subs("eɪ", "ɪ", 5, H.words("based", "make", "day")),
                                          H.subs("ɛ", "ɪ", 5, H.words("enhanced", "exposure", "effect"))]))
    t = ts["set:front_vowel_ladder"]
    assert t.kind == "SET" and t.tier == "established" and t.measures["confident"] == 16
    rec = t.consolidation[0]
    assert rec["decision"] == "merged" and rec["conditions"]["4_explains_clearly_more"]["largest_member"] == 6
    assert t.knowledge[0].entry == "family:front_vowel_ladder" and t.knowledge[0].origin == "knowledge"
    assert not any(k.startswith("contrast:") for k in ts)  # members absorbed, never kept alongside
    assert {a["target"] for a in absorbed if a.get("absorbed_into") == t.target_id} == \
        {"contrast:eɪ~ɛ", "contrast:eɪ~ɪ", "contrast:ɛ~ɪ"}


def test_family_merge_refused_when_it_does_not_explain_clearly_more():
    # both members pass alone (12 and 5), but together they explain 17 < 1.5 × 12
    ts, absorbed, _ = _targets(H.scatter([H.subs("ɛ", "eɪ", 12, W), H.subs("eɪ", "ɪ", 5, H.words("based", "make", "day"))]))
    assert "set:front_vowel_ladder" not in ts and "contrast:eɪ~ɛ" in ts and "contrast:eɪ~ɪ" in ts
    rec = next(a for a in absorbed if a.get("family") == "front_vowel_ladder")
    assert rec["decision"].startswith("not merged") and rec["conditions"]["4_explains_clearly_more"]["ok"] is False


def test_contradicting_directions_block_a_directional_family():
    voiced = H.words("rise", "lose", "news", "ease")
    ts, absorbed, _ = _targets(H.scatter([H.subs("z", "s", 5, voiced), H.subs("d", "t", 5, H.words("road", "seed", "bad")),
                                          H.subs("s", "z", 6, H.words("bus", "kiss", "face"))]))
    assert "set:devoicing" not in ts
    rec = next(a for a in absorbed if a.get("family") == "devoicing")
    assert rec["conditions"]["5_not_contradicted"]["ok"] is False


def test_directional_family_keeps_the_opposite_direction_as_its_own_candidate():
    ts, _, _ = _targets(H.scatter([H.subs("z", "s", 6, H.words("rise", "lose", "news")),
                                   H.subs("d", "t", 6, H.words("road", "seed", "bad")),
                                   H.sub("bus", "s", "z")]))
    assert "set:devoicing" in ts and ts["set:devoicing"].measures["confident"] == 12
    assert ts["contrast:s~z"].pairs == (("s", "z"),) and ts["contrast:s~z"].measures["confident"] == 1


def test_broad_target_is_narrowed_to_its_context():
    inside = [H.sub(w, "s", "ʃ", position="final", cluster=True) for w in H.words("costs", "tests", "lists", "masks")(6)]
    occ_in = H.ok("posts", "s", 4, position="final", cluster=True)
    outside = H.ok("sun", "s", 30, position="initial") + [H.sub("seem", "s", "ʃ", position="initial")]
    ts, absorbed, _ = _targets(H.scatter([inside, occ_in, outside]))
    t = ts["conditioned:final_cluster:contrast:s~ʃ"]
    assert t.kind == "CONDITIONED" and t.tier == "established" and t.measures["confident"] == 6
    rec = t.consolidation[0]
    assert rec["inside"] == {"confident": 6, "occurrences": 10, "share": 0.6} and rec["outside"]["confident"] == 1
    assert "contrast:s~ʃ" not in ts and t.hypothesis.startswith("/s/ appears less stable in consonant clusters at the end")


def test_only_occurring_in_one_context_is_not_called_context_dependency():
    ts, _, _ = _targets(H.scatter([H.subs("s", "ʃ", 6, H.words("costs", "tests", "lists"), position="final"),
                                   H.ok("posts", "s", 4, position="final")]))
    t = ts["contrast:s~ʃ"]
    assert t.kind == "CONTRAST" and any("only observed at the end of words" in n for n in t.notes)


def test_broad_target_is_narrowed_to_a_word_when_one_word_carries_it():
    ts, absorbed, _ = _targets(H.scatter([H.subs("ɛ", "eɪ", 5, H.words("best")),
                                          H.ok("bed", "ɛ", 10), H.ok("level", "ɛ", 10), H.ok("best", "ɛ", 1)]))
    t = ts["lexical:best:ɛ"]
    assert t.kind == "LEXICAL" and t.word == "best" and t.tier == "established", t.checks
    assert "contrast:eɪ~ɛ" not in ts and any(a.get("reason") == "lexical concentration" for a in absorbed)


def test_fragment_words_never_form_a_lexical_target():
    ins = H.scatter([H.subs("ɪ", "eɪ", 5, H.words("bility")), H.ok("sit", "ɪ", 20)])
    for i in ins:
        i.fragment_words = frozenset({"bility"})
    ts, _, _ = _targets(ins)
    assert ts["lexical:bility:ɪ"].tier != "established"
    assert ts["lexical:bility:ɪ"].checks["not_fragment"]["ok"] is False


def test_engine_disagreement_demotes_the_tier():
    ins = H.scatter([H.subs("ɛ", "eɪ", 8, W), H.ok("bed", "ɛ", 10)])
    for i in ins[:6]:
        i.engine_agreement = {o["id"]: "differ" for o in i.coach_observations}
    ts, _, _ = _targets(ins)
    t = ts["contrast:eɪ~ɛ"]
    assert t.tier == "emerging" and "engine_agreement" in t.checks["_failed"]
    ins2 = H.scatter([H.subs("ɛ", "eɪ", 8, W), H.ok("bed", "ɛ", 10)])
    for i in ins2:
        i.engine_agreement = {o["id"]: "agree" for o in i.coach_observations}
    t2 = _targets(ins2)[0]["contrast:eɪ~ɛ"]
    assert t2.tier == "established" and t2.measures["engine_compared"] == 8  # agreement never raises anything


def test_clarity_is_capped_and_fluency_has_its_own_targets():
    fl = H.fluency(H.hesitation(), H.hesitation("of", "the"))
    ins = H.scatter([[H.omit(w, "d") for w in H.words("hand", "road", "need", "bad")(8)]])
    ins += [H.inp(H.rd(100 + k, session=1 + k % 3), H.ok("bed", "ɛ"), fluency=fl) for k in range(6)]
    ts, _, _ = _targets(ins)
    assert ts["clarity:final_consonant"].tier == "emerging" and "capped" in ts["clarity:final_consonant"].checks["_failed"][-1]
    f = ts["fluency:hesitation"]
    assert f.kind == "FLUENCY" and f.tier == "established" and f.measures["confident"] == 12
    assert set(TARGET_KINDS) == {"CONTRAST", "SET", "CONDITIONED", "LEXICAL", "FLUENCY", "CLARITY"}
    assert {u["category"] for u in UNSUPPORTED} >= {"perception", "articulator", "rate_dependence", "swallowing_as_cause"}


def test_family_concentration_is_measured_on_the_family_not_each_member():
    # /ɛ/ heard as /eɪ/ (6) and as /ɪ/ (5), plus 3 scattered out-of-family deviations:
    # alone, each member's concentration is < 0.5; as a family, 11/14 of /ɛ/'s deviations are inside it
    scattered = [H.sub("never", "ɛ", "ʌ"), H.sub("letter", "ɛ", "ɑː"), H.sub("general", "ɛ", "ɔ")]
    ts, absorbed, _ = _targets(H.scatter([H.subs("ɛ", "eɪ", 6, W), H.subs("ɛ", "ɪ", 5, H.words("enhanced", "effect", "else")),
                                          scattered]))
    t = ts["set:front_vowel_ladder"]
    assert t.tier == "established" and t.measures["concentration"] == round(11 / 14, 3)
    members = next(a for a in absorbed if a.get("family") is None and a.get("absorbed_into") == t.target_id)
    assert members  # members recorded as absorbed
    alone = _targets(H.scatter([H.subs("ɛ", "eɪ", 6, W), scattered, H.subs("ɛ", "ʌ", 4, W)]))[0]["contrast:eɪ~ɛ"]
    assert alone.tier != "established" and alone.checks["concentration"]["ok"] is False


def test_small_targets_concentrated_in_one_word_are_not_narrowed():
    ts, absorbed, _ = _targets(H.scatter([H.sub("bus", "s", "z"), H.ok("sun", "s", 10)]))
    assert "contrast:s~z" in ts and not any(a.get("reason") == "lexical concentration" for a in absorbed)


# ----------------------------------------------------------------------
# Layers 6–7 — leverage comparison and 0–3 selection
# ----------------------------------------------------------------------

from pronunciation_lab.coaching import leverage as L  # noqa: E402
from pronunciation_lab.coaching.selection import select  # noqa: E402
from pronunciation_lab.coaching.targets import evaluate  # noqa: E402

FOUR = [("s", "ʃ", H.words("sea", "sun", "sit", "self")), ("w", "v", H.words("we", "way", "west", "wind")),
        ("θ", "t", H.words("think", "three", "thank", "thin")), ("ŋ", "n", H.words("sing", "long", "ring", "thing"))]


def _select(inputs, cal=DEFAULT, train=lambda t: 2):
    ts, absorbed, idx = _targets(inputs, cal)
    n_sent = len({i.reading.sentence_key for i in inputs})
    cands = [t for t in ts.values() if t.tier == "established"]
    return select(cands, lambda t: evaluate(t, idx, cal, n_sent), train, cal), ts


def test_comparison_names_the_deciding_criterion_and_values():
    ts, _, _ = _targets(H.scatter([H.subs("s", "ʃ", 9, H.words("sea", "sun", "sit", "self", "sand", "song")),
                                   H.subs("w", "v", 5, H.words("we", "way", "west"))], n_readings=12))
    rec = L.compare(ts["contrast:s~ʃ"], ts["contrast:v~w"], lambda t: 2)
    assert rec.winner == "contrast:s~ʃ" and rec.criterion == "breadth_sentences" and (rec.winner_value, rec.loser_value) == (9, 5)
    assert "breadth (different sentences): 9 vs 5" == rec.text()


def test_differences_within_the_margin_pass_to_the_next_criterion_and_ties_are_recorded():
    ts, _, _ = _targets(H.scatter([H.subs("s", "ʃ", 6, H.words("sea", "sun", "sit")), H.subs("w", "v", 6, H.words("we", "way", "west"))]))
    a, b = ts["contrast:s~ʃ"], ts["contrast:v~w"]
    rec = L.compare(a, b, lambda t: 2)
    assert rec.tie and rec.criterion == "tie_break" and rec.winner == min(a.target_id, b.target_id)
    rec2 = L.compare(a, b, lambda t: 2 if t is a else 1)
    assert rec2.criterion == "trainability" and rec2.winner == a.target_id


def test_three_independent_strong_targets_give_exactly_three_and_a_fourth_is_not_prioritised():
    sel3, _ = _select(H.scatter([H.subs(e, h, 6, w) for e, h, w in FOUR[:3]]))
    assert len(sel3.selected) == 3 and not sel3.not_prioritised
    sel4, _ = _select(H.scatter([H.subs(e, h, 6, w) for e, h, w in FOUR]))
    assert len(sel4.selected) == 3 and len(sel4.not_prioritised) == 1
    np = sel4.not_prioritised[0]
    assert np["reason"] in ("lost a comparison", "the limit of 3 actions was reached")
    assert len({u for t in sel4.selected for u in t.unit_ids}) == sum(len(t.unit_ids) for t in sel4.selected)  # disjoint


def test_one_strong_target_gives_one_action_never_padding():
    sel, ts = _select(H.scatter([H.subs("s", "ʃ", 6, H.words("sea", "sun", "sit")), H.sub("we", "w", "v"),
                                 H.amb("think", "θ", "t"), H.amb("thin", "θ", "t")]))
    assert [t.target_id for t in sel.selected] == ["contrast:s~ʃ"]


def test_a_selected_action_absorbs_an_overlapping_candidate():
    # a family is selected; a lexical candidate inside it ("best") loses its evidence and disappears
    ins = H.scatter([H.subs("ɛ", "eɪ", 3, H.words("best")), H.subs("ɛ", "eɪ", 4, H.words("spread", "level", "spell")),
                     H.subs("eɪ", "ɪ", 5, H.words("based", "make", "day")), H.ok("bed", "ɛ", 12)])
    sel, ts = _select(ins)
    assert "lexical:best:ɛ" in ts and ts["lexical:best:ɛ"].tier == "established"
    assert [t.target_id for t in sel.selected] == ["set:front_vowel_ladder"]
    a = next(x for x in sel.absorbed if x["target"] == "lexical:best:ɛ")
    assert a["absorbed_by"] == "set:front_vowel_ladder" and a["residual_units"] == 0


def test_at_most_one_fluency_action():
    fl = H.fluency(H.hesitation(), H.hesitation("of", "the"), ("FILLER", None, "a", "b", "between words"),
                   ("FILLER", None, "c", "d", "between words"))
    ins = [H.inp(H.rd(k, session=1 + k % 3), H.ok("bed", "ɛ"), fluency=fl) for k in range(8)]
    sel, ts = _select(ins)
    assert ts["fluency:hesitation"].tier == ts["fluency:filler"].tier == "established"
    assert sum(t.kind == "FLUENCY" for t in sel.selected) == 1
    assert sel.not_prioritised[0]["reason"].startswith("at most 1 fluency action")


def test_no_established_candidate_selects_nothing():
    sel, _ = _select(H.scatter([H.amb(w, "ɛ", "eɪ") for w in W(12)]))
    assert sel.selected == [] and sel.rounds == []


# ----------------------------------------------------------------------
# Layers 8–9 — interventions, practice plan, contract, orchestration
# ----------------------------------------------------------------------

import copy  # noqa: E402

from pronunciation_lab.coaching import contract as C  # noqa: E402
from pronunciation_lab.coaching.engine import run_coaching  # noqa: E402


def _run(inputs, **kw):
    return run_coaching(inputs, generated_at="2026-10-05T00:00:00+00:00", **kw)


def _strong(extra=(), n=6):
    return H.scatter([H.subs("ɪ", "iː", n, H.words("sit", "list", "fill", "bit")), H.ok("sit", "ɪ", 12),
                      H.ok("seed", "iː", 6), *extra])


def test_one_strong_contrast_gives_one_complete_traceable_action():
    r = _run(_strong())
    assert r["state"] == "actions" and r["integrity"]["ok"], r["integrity"]
    [a] = r["actions"]
    assert a["action_text"].startswith("Practise keeping /ɪ/ (i as in sit) distinct from /iː/")
    assert a["time_minutes"] == 12 and a["evidence_tier"] == "established" and a["rank_in_plan"] == 1
    assert a["why"]["text"].startswith("In 3 sessions, /ɪ/ was heard as /iː/ 6 times across 4 words.")
    assert "/ɪ/ was heard as expected in 12 other occurrences, so you already produce it; the issue appears to be " \
        "consistency. It occurs in about" in a["why"]["text"]
    p = a["practice"]
    assert p["trainability"] == "specific_guidance" and p["guidance"][0]["origin"] == "knowledge"
    assert [s["step"] for s in p["steps"]] == ["listen", "contrast", "words", "sentences", "retest"]
    assert len(p["examples"]) == 3 and len(p["counter_examples"]) >= 1 and len(p["retest_sentences"]) == 2
    assert all(e["role"] == "heard_differently" and e["unit_id"] in a["supporting_unit_ids"] for e in p["examples"])
    assert {w["word"] for w in p["words"]} == {"sit", "list", "fill", "bit"}
    assert a["transfer"]["origin"] == "hypothesised" and "not something measured" in a["transfer"]["text"]
    assert len(a["supporting_unit_ids"]) == 6 and len(a["counter_unit_ids"]) == 12
    assert r["pool"]["readings"] == 12 and r["pool"]["sessions"] == 3


@pytest.mark.parametrize("inputs, code", [
    (lambda: _strong()[:5], "history_too_small"),
    (lambda: H.scatter([H.subs("ɪ", "iː", 6, H.words("sit", "list", "fill"))], sessions=1), "single_session"),
    (lambda: H.scatter([H.amb(w, "ɛ", "eɪ") for w in W(12)]), "only_ambiguous"),
    (lambda: H.scatter([H.sub("roses", "ᵻ", "ɪ") for _ in range(12)]), "only_excluded_kinds"),
    (lambda: H.scatter([H.subs("ɪ", "iː", 3, H.words("sit", "list", "fill")), H.amb("bit", "ɪ", "iː"),
                        H.amb("fit", "ɪ", "iː"), H.ok("sit", "ɪ", 12)]), "only_emerging"),
    (lambda: H.scatter([H.ok("sit", "ɪ", 12)]), "nothing_recurring"),
])
def test_abstention_is_explicit(inputs, code):
    r = _run(inputs())
    assert r["state"] == "no_action" and r["actions"] == [] and r["no_action"]["code"] == code, r["no_action"]
    assert r["integrity"]["ok"] and r["no_action"]["message"] and r["no_action"]["what_would_help"]


def test_no_audio_means_no_action():
    ins = H.scatter([[H.sub(w, "ɪ", "iː", play=False) for w in H.words("sit", "list", "fill", "bit")(6)],
                     H.ok("sit", "ɪ", 12)])
    r = _run(ins)
    assert r["state"] == "no_action" and r["no_action"]["code"] in ("audio_unavailable", "no_trainable_intervention")


def test_emerging_evidence_never_takes_an_action_slot_but_stays_in_detail():
    r = _run(_strong(extra=[H.subs("s", "ʃ", 3, H.words("sea", "sun", "sit")), H.amb("say", "s", "ʃ"),
                            H.amb("set", "s", "ʃ")]))
    assert [a["target"]["target_id"] for a in r["actions"]] == ["contrast:iː~ɪ"]
    assert [t["target_id"] for t in r["detail"]["listen_check"]] == ["contrast:s~ʃ"]


def test_without_guidance_the_fallback_is_listen_and_compare_never_invented_text():
    ins = H.scatter([H.subs("ʊ", "uː", 6, H.words("book", "look", "put", "full")),
                     [H.ok(w, "ʊ")[0] for w in H.words("good", "foot", "could")(12)]])
    [a] = _run(ins)["actions"]
    assert a["practice"]["trainability"] == "listen_compare_fallback" and a["practice"]["guidance"] == []
    contrast = next(s for s in a["practice"]["steps"] if s["step"] == "contrast")
    from pronunciation_lab.coaching.plan import FALLBACK_TEXT
    assert contrast["fallback"] == FALLBACK_TEXT and contrast["guidance"] is None
    one_word = H.scatter([H.subs("ʊ", "uː", 6, H.words("book", "look", "put", "full")), H.ok("good", "ʊ", 12)])
    assert _run(one_word)["no_action"]["code"] == "no_trainable_intervention"  # one clear word is too few to compare


def test_withheld_and_ineligible_readings_never_enter_the_pool():
    # the adapter passes only eligible readings; the core must never see continuation (tested with a
    # reading whose units would otherwise be enough on their own)
    r = _run(_strong(), prior_exclusions={"not_eligible": 4})
    assert r["pool"]["reading_exclusions"] == {"not_eligible": 4}


@pytest.mark.parametrize("corrupt, message", [
    (lambda r: r["actions"].append(copy.deepcopy(r["actions"][0])), "shares observations"),
    (lambda r: r["actions"][0].update(evidence_tier="emerging"), "established evidence"),
    (lambda r: r["actions"][0]["supporting_unit_ids"].append("nope"), "not in the pool"),
    (lambda r: r["actions"][0]["why"]["measures"].update(confident=99), "not recomputable"),
    (lambda r: r["actions"][0]["practice"]["examples"][0].update(play_ms=None), "no exact playback"),
    (lambda r: r["actions"][0]["practice"]["retest_sentences"][0]["ref"].update(play_ms=[5, 1]), "retest sentence"),
    (lambda r: r["actions"][0]["transfer"].update(origin="observed"), "hypothesised"),
    (lambda r: r["actions"][0]["target"].update(kind="PERCEPTION"), "unsupported target kind"),
    (lambda r: r["actions"][0].update(action_text="Your tongue is wrong"), "banned wording"),
    (lambda r: r["actions"][0]["knowledge_contributions"].append({"origin": "evidence"}), "labelled as knowledge"),
    (lambda r: r["actions"].extend(copy.deepcopy(r["actions"][0]) for _ in range(3)), "more than 3 actions"),
])
def test_validator_catches_contract_defects(corrupt, message):
    from pronunciation_lab.coaching.patterns import build_index as bi
    ins = _strong()
    r = _run(ins)
    units = bi([u for i in ins for u in E.normalise(i)[0]], []).units
    corrupt(r)
    issues = C.validate_coaching(r, units, DEFAULT)
    assert any(message in i for i in issues), issues


def test_user_words_never_trip_the_language_check():
    ins = H.scatter([H.subs("ɛ", "eɪ", 6, H.words("error", "bad", "wrong")), H.ok("bed", "ɛ", 20)])
    # the user's own article words ("error", "bad", "wrong") appear as practice words / lexical targets
    r = _run(ins)
    assert r["integrity"]["ok"], r["integrity"]


def test_result_is_deterministic():
    a, b = _run(_strong()), _run(_strong())
    assert a == b


def test_a_fluency_action_end_to_end_is_phrase_practice_and_stays_separate():
    fl = H.fluency(H.hesitation(), H.hesitation("of", "the"))
    ins = [H.inp(H.rd(100 + k, session=1 + k % 3), H.ok("bed", "ɛ"), fluency=fl) for k in range(12)]
    ins += _strong()
    r = _run(ins)
    kinds = {a["target"]["kind"]: a for a in r["actions"]}
    assert set(kinds) == {"CONTRAST", "FLUENCY"} and r["integrity"]["ok"], r["integrity"]
    f = kinds["FLUENCY"]
    assert f["practice"]["trainability"] == "phrase_practice" and f["practice"]["words"] == []
    assert [s["step"] for s in f["practice"]["steps"]] == ["listen", "chunk", "sentences", "retest"]
    assert f["why"]["text"].startswith("In 3 sessions, possible hesitation pauses inside phrases were noticed 24 times")
    assert not set(f["supporting_unit_ids"]) & set(kinds["CONTRAST"]["supporting_unit_ids"])
    assert [a["time_minutes"] for a in r["actions"]] == [7, 5]


def test_supporting_evidence_never_adds_breadth():
    # 4 confident in one word + many ambiguous across words: breadth counts confident observations only
    ins = H.scatter([H.subs("s", "ʃ", 4, H.words("starve", "stop")), [H.amb(w, "s", "ʃ") for w in W(18)],
                     H.ok("sun", "s", 12)])
    t = _targets(ins)[0]["contrast:s~ʃ"]
    assert t.measures["words"] > 3 and t.measures["conf_words"] == 2
    assert t.tier != "established" and "words" in t.checks["_failed"]
    assert "4 times across 2 words" in C.why_text(t)


def test_a_condition_covering_most_occurrences_is_not_a_context():
    # vowels are mostly word-medial: "in the middle of words" is not a specific context
    medial = H.subs("ɛ", "eɪ", 8, W)
    ins = H.scatter([medial, H.ok("bed", "ɛ", 30), H.ok("else", "ɛ", 12, position="initial")])   # enough elsewhere
    ts = _targets(ins)[0]
    assert "contrast:eɪ~ɛ" in ts and not any(k.startswith("conditioned:medial") for k in ts)


def test_counts_of_pauses_and_confusions_are_never_compared_and_recurrence_decides():
    fl = H.fluency(H.hesitation(), H.hesitation("of", "the"), H.hesitation("to", "a"))
    flu = [H.inp(H.rd(100 + k, session=1 + k % 2), H.ok("bed", "ɛ"), fluency=fl) for k in range(8)]   # 2 sessions
    snd = H.scatter([H.subs("ɪ", "iː", 8, H.words("sit", "list", "fill", "bit")), H.ok("sit", "ɪ", 12)], n_readings=8,
                    sessions=6)
    ts, _, _ = _targets(flu + snd)
    f, c = ts["fluency:hesitation"], ts["contrast:iː~ɪ"]
    assert f.measures["confident"] == 24 and c.measures["confident"] == 8   # 24 pauses vs 8 confusions
    rec = L.compare(f, c, lambda t: 1)
    assert rec.winner == c.target_id and rec.criterion == "recurrence_sessions", rec


def test_each_action_lists_only_the_comparisons_it_won():
    r = _run(H.scatter([H.subs(e, h, 6, w) for e, h, w in FOUR]))
    assert len(r["actions"]) == 3
    for a in r["actions"]:
        assert all(d["winner"] == a["target"]["target_id"] for d in a["why"]["decided_by"])
    assert any(a["why"]["decided_by"] for a in r["actions"])



def test_no_context_claim_without_enough_evidence_outside_it():
    # strongly concentrated at the end of words, but only 7 occurrences elsewhere (< CTX_MIN_OUT)
    inside = [H.sub(w, "s", "ʃ", position="final") for w in H.words("costs", "tests", "lists")(6)] + H.ok("posts", "s", 2, position="final")
    outside = [H.sub("seem", "s", "ʃ", position="initial")] + H.ok("sun", "s", 6, position="initial")
    ts = _targets(H.scatter([inside, outside]))[0]
    assert "contrast:s~ʃ" in ts and not any(k.startswith("conditioned:") for k in ts)


def test_pause_counts_are_never_compared_with_confusion_counts():
    # same sentences and sessions; coverage (24 pauses vs 8 confusions) must not decide across modalities
    fl = H.fluency(H.hesitation(), H.hesitation("of", "the"), H.hesitation("to", "a"))
    flu = [H.inp(H.rd(100 + k, session=1 + k % 3), H.ok("bed", "ɛ"), fluency=fl) for k in range(8)]
    snd = H.scatter([H.subs("ɪ", "iː", 8, H.words("sit", "list", "fill", "bit")), H.ok("sit", "ɪ", 12)], n_readings=8)
    ts, _, _ = _targets(flu + snd)
    f, c = ts["fluency:hesitation"], ts["contrast:iː~ɪ"]
    assert (f.measures["conf_sentences"], f.measures["conf_sessions"]) == (c.measures["conf_sentences"], c.measures["conf_sessions"])
    rec = L.compare(f, c, lambda t: 1 if t.kind == "FLUENCY" else 2)
    assert rec.criterion == "trainability" and rec.winner == c.target_id, rec


def test_a_difference_must_pass_both_the_ratio_and_the_absolute_margin():
    assert not L.meaningful(3, 2, "breadth_sentences", DEFAULT)      # ratio 1.5 but only 1 apart
    assert L.meaningful(6, 3, "breadth_sentences", DEFAULT)
    assert not L.meaningful(0.55, 0.5, "concentration", DEFAULT) and L.meaningful(0.75, 0.5, "concentration", DEFAULT)
    assert not L.meaningful(9, 7, "coverage", DEFAULT)               # 2 apart but ratio < 1.5



def test_a_result_that_fails_validation_shows_nothing(monkeypatch):
    from pronunciation_lab.coaching import engine as EN
    monkeypatch.setattr(EN.C, "validate_coaching", lambda result, units, cal: ["synthetic defect"])
    r = _run(_strong())
    assert r["state"] == "unavailable" and r["actions"] == [] and r["integrity"] == {"ok": False, "issues": ["synthetic defect"]}


def test_a_lexical_target_named_after_a_users_word_is_not_a_wording_violation():
    ins = H.scatter([H.subs("ɛ", "eɪ", 6, H.words("error")), H.ok("bed", "ɛ", 12), H.ok("level", "ɛ", 12),
                     H.ok("error", "ɛ", 1)])
    r = _run(ins)   # "error" occurs in several sentences of the reading (relevance)
    [a] = r["actions"]
    assert a["target"]["kind"] == "LEXICAL" and a["action_text"] == "Practise the word ‘error’"
    assert r["integrity"]["ok"], r["integrity"]


def test_decided_by_excludes_comparisons_between_other_candidates():
    # the first candidate in id order wins against the second, then loses to the third: that first comparison
    # (between two non-winners) must not appear as a reason for the winner
    ins = H.scatter([H.subs("ŋ", "n", 6, H.words("sing", "long", "ring")), H.subs("s", "ʃ", 6, H.words("sea", "sun", "sit")),
                     H.subs("w", "v", 12, H.words("we", "way", "west", "wind"))])
    r = _run(ins)
    first = r["actions"][0]
    assert first["target"]["target_id"] == "contrast:v~w"
    assert first["why"]["decided_by"] and all(d["winner"] == "contrast:v~w" for d in first["why"]["decided_by"])
