"""M5 Reduction & Connected-Speech Coach — unit, negative, boundary and invariant tests.

Evidence is constructed so every signal is controlled exactly. Timings are laid
out explicitly: neighbouring sounds 100 ms apart unless a test moves them.
"""

import copy
import json

import pytest
from test_m4_coach import ph, result

from pronunciation_lab.app import connected_speech as CS
from pronunciation_lab.app import reduction as RD
from pronunciation_lab.app import reduction_evidence as RE
from pronunciation_lab.app.coach import build_coach
from pronunciation_lab.app.engine_compare import compare, validate_comparison
from pronunciation_lab.app.ground_truth import GROUND_TRUTH_IDS, require_ground_truth
from pronunciation_lab.app.phoneme_coach import build_observations
from pronunciation_lab.benchmark.schema import AcousticEvidence


def S(expected, start, observed="same", dur=20.0, **kw):
    """A sound with an explicit span; observed=None makes it not decoded (estimated location)."""
    observed = expected if observed == "same" else observed
    if observed is None:
        kw.setdefault("ep", 0.0)
        kw.setdefault("nbest", [("ə", 0.02), ("q", 0.01)])
        kw.setdefault("source", "derived")
    return ph(expected, observed, start=start, end=start + dur, **kw)


def ac(rel=1.2, voiced=True):
    return AcousticEvidence(measured_on="analysis", energy_db=-20.0, relative_energy=rel, voicing=voiced, f0_hz=120.0,
                            spectral_features={"zero_crossing_rate": 0.1, "spectral_centroid_hz": 900.0})


def run(words, engine="wav2vec2_raw"):
    res = result(words)
    res.engine.name = engine
    obs = build_observations(res)
    return res, obs, RD.build_reduction(res, obs)


def cand(red, word, expected):
    found = [c for c in red["candidates"] if c["where"]["word"] == word and c["expected"] == expected]
    assert len(found) <= 1
    return found[0] if found else None


# ----------------------------------------------------------------------
# A single kind of evidence never yields a reduction category
# ----------------------------------------------------------------------

def test_not_detected_alone_is_insufficient_evidence():
    _, _, red = run([("aka", [S("a", 1000), S("k", 1100, None, dur=60), S("a", 1200)])])
    c = cand(red, "aka", "k")
    assert c["interpretation"]["category"] == "insufficient_evidence" and c["evidence_strength"] == "insufficient"
    assert c["interpretation"]["streams"] == ["recognition"]
    assert any("at least two are needed" in r for r in c["interpretation"]["reasons"])
    assert red["integrity"]["ok"], red["integrity"]


def test_not_decoded_but_plausible_alone_is_insufficient():
    _, _, red = run([("aka", [S("a", 1000), S("k", 1100, None, dur=60, ep=0.2), S("a", 1200)])])
    c = cand(red, "aka", "k")
    assert c["interpretation"]["signals"] == ["not_decoded_plausible"]
    assert c["interpretation"]["category"] == "insufficient_evidence"


def _s_series(target_slot_neighbour_ms=None, n_others=3, target_energy=1.2, other_energy=1.2,
              target_observed="same", target_ep=0.9, target_nbest=None):
    """Words 'mas' (not a function word): /a/ then /s/. The target /s/ is last; its neighbours can move closer."""
    words, t = [], 1000.0
    for _ in range(n_others):
        words.append(("mas", [S("a", t), S("s", t + 100, acoustic=ac(other_energy, voiced=False))]))
        t += 200.0
    if target_slot_neighbour_ms is None:
        words.append(("mas", [S("a", t), S("s", t + 100, target_observed, ep=target_ep, nbest=target_nbest,
                                         acoustic=ac(target_energy, voiced=False))]))
        t += 200.0
    else:
        d = target_slot_neighbour_ms
        words.append(("mas", [S("a", t), S("s", t + 20 + d, target_observed, ep=target_ep, nbest=target_nbest,
                                         acoustic=ac(target_energy, voiced=False))]))
        t = t + 20 + d + 20 + d
    words.append(("a", [S("a", t)]))
    return words


def test_shorter_temporal_slot_alone_is_not_listed():
    _, _, red = run(_s_series(target_slot_neighbour_ms=10))
    assert all(c["expected"] != "s" for c in red["candidates"])


def test_lower_energy_alone_is_not_listed():
    _, _, red = run(_s_series(target_energy=0.1))
    assert all(c["expected"] != "s" for c in red["candidates"])


def test_voicing_alone_is_not_listed():
    _, _, red = run([("aba", [S("a", 1000), S("b", 1100, acoustic=ac(voiced=False)), S("a", 1200)])])
    assert cand(red, "aba", "b") is None


def test_substitution_alone_without_a_predicting_context_is_left_to_m4():
    _, _, red = run([("aka", [S("a", 1000), S("k", 1100, "p", ep=0.0, nbest=[("p", 0.9), ("k", 0.0)]), S("a", 1200)])])
    assert cand(red, "aka", "k") is None


def test_substitution_predicted_by_context_alone_is_insufficient_with_explanation():
    # "ten boys": final /n/ before /b/ decoded as [m]
    _, _, red = run([("ten", [S("t", 1000), S("ɛ", 1100), S("n", 1200, "m", ep=0.0, nbest=[("m", 0.9), ("n", 0.0)])]),
                     ("boys", [S("b", 1300), S("ɔɪ", 1400), S("z", 1500)])])
    c = cand(red, "ten", "n")
    assert c["interpretation"]["category"] == "insufficient_evidence"
    assert [e["id"] for e in c["interpretation"]["candidate_explanations"]] == ["alveolar_assimilation"]
    assert c["interpretation"]["natural_connected_speech_possible"]
    # one kind of evidence: the context is named, but nothing is claimed to be consistent with it
    assert "one kind of evidence is not enough" in c["summary"] and "consistent with" not in c["summary"]


# ----------------------------------------------------------------------
# Two or more streams
# ----------------------------------------------------------------------

def test_omission_with_no_room_is_a_possible_omission():
    _, _, red = run([("aka", [S("a", 1000), S("k", 1020, None), S("a", 1040)])])
    c = cand(red, "aka", "k")
    assert c["evidence"]["temporal_slot"]["slot_ms"] == 20.0 and c["evidence"]["temporal_slot"]["no_room"]
    assert c["interpretation"]["category"] == "possible_omission"
    assert c["interpretation"]["streams"] == ["recognition", "temporal_slot"]
    assert c["evidence_strength"] == "low"
    assert not c["interpretation"]["natural_connected_speech_possible"]


def test_no_room_boundary_is_one_frame():
    _, _, red = run([("aka", [S("a", 1000), S("k", 1020, None, dur=21), S("a", 1041)])])
    c = cand(red, "aka", "k")
    assert c["evidence"]["temporal_slot"]["slot_ms"] == 21.0 and not c["evidence"]["temporal_slot"]["no_room"]
    assert c["interpretation"]["category"] == "insufficient_evidence"


def test_omission_in_a_connected_speech_context_is_a_possible_reduction_not_an_error():
    # "cat go": final /t/ before /ɡ/
    _, _, red = run([("cat", [S("k", 1000), S("æ", 1100), S("t", 1120, None)]),
                     ("go", [S("ɡ", 1140), S("oʊ", 1240)])])
    c = cand(red, "cat", "t")
    it = c["interpretation"]
    assert it["category"] == "possible_connected_speech_reduction" and it["evidence_pattern"] == "omission"
    assert {e["id"] for e in it["candidate_explanations"]} == {"glottalisation", "alveolar_assimilation"}
    assert it["natural_connected_speech_possible"]
    assert "consistent with natural connected speech" in c["summary"]
    assert "cannot determine whether the sound was fully articulated" in c["summary"]
    for word in ("wrong", "incorrect", "error", "swallowed", "chewed", "score"):
        assert word not in json.dumps(c).lower()


def test_three_streams_give_moderate_never_high():
    words = _s_series(target_slot_neighbour_ms=10, target_energy=0.1, target_ep=0.3,
                      target_nbest=[("s", 0.3), ("z", 0.05)])
    _, _, red = run(words)
    [c] = [c for c in red["candidates"] if c["expected"] == "s"]
    assert c["interpretation"]["signals"] == ["weak_support", "shorter_slot", "lower_energy"]
    assert c["interpretation"]["category"] == "possible_weakening" and c["evidence_strength"] == "moderate"
    assert all(x["evidence_strength"] != "high" for x in red["candidates"])
    assert "high" not in RD.EVIDENCE_STRENGTHS


def test_two_non_recognition_streams_give_low_weakening():
    _, _, red = run(_s_series(target_slot_neighbour_ms=10, target_energy=0.1))
    [c] = [c for c in red["candidates"] if c["expected"] == "s"]
    assert c["interpretation"]["streams"] == ["temporal_slot", "acoustic"]
    assert c["interpretation"]["category"] == "possible_weakening" and c["evidence_strength"] == "low"


def test_ambiguous_decode_with_other_evidence_stays_ambiguous():
    words = _s_series(target_slot_neighbour_ms=10, target_observed="z", target_ep=0.3,
                      target_nbest=[("z", 0.4), ("s", 0.3)])
    _, _, red = run(words)
    [c] = [c for c in red["candidates"] if c["expected"] == "s"]
    assert c["raw_observation"]["m4_type"] == "ambiguous"
    assert c["interpretation"]["category"] == "ambiguous" and c["evidence_strength"] == "ambiguous"


def test_substitution_with_other_evidence_and_predicting_context_is_possible_coarticulation():
    words = [("ten", [S("t", 1000), S("ɛ", 1100), S("n", 1180, "m", ep=0.0, nbest=[("m", 0.9), ("n", 0.0)],
                                                   acoustic=ac(0.1))]),
             ("boys", [S("b", 1210), S("ɔɪ", 1300), S("z", 1400)])]
    for k in range(3):  # three other /n/ with higher energy
        words.append(("in", [S("ɪ", 1600 + k * 200), S("n", 1700 + k * 200, acoustic=ac(1.5))]))
    _, _, red = run(words)
    c = cand(red, "ten", "n")
    assert c["interpretation"]["signals"] == ["substitution", "lower_energy"]
    assert c["interpretation"]["category"] == "possible_coarticulation"
    assert c["interpretation"]["candidate_explanations"][0]["id"] == "alveolar_assimilation"


# ----------------------------------------------------------------------
# Within-recording comparisons: boundaries
# ----------------------------------------------------------------------

@pytest.mark.parametrize("n_others, signalled", [(2, False), (3, True)])
def test_slot_comparison_needs_three_comparables(n_others, signalled):
    res, obs, _ = run(_s_series(target_slot_neighbour_ms=10, n_others=n_others))
    m = RE.measure(res, obs)
    target = [o for o in obs if o["expected"] == "s"][-1]
    assert m[target["id"]]["temporal_slot"]["comparables"] == n_others
    assert m[target["id"]]["temporal_slot"]["shorter_than_all_comparables"] is signalled


def test_slot_equal_to_the_shortest_comparable_is_not_shorter():
    res, obs, _ = run(_s_series())  # all slots equal
    m = RE.measure(res, obs)
    assert not any(v["temporal_slot"]["shorter_than_all_comparables"] for v in m.values())


@pytest.mark.parametrize("gap, pause", [(249.0, False), (250.0, True)])
def test_pause_boundary_is_the_m2_pause_length(gap, pause):
    res, obs, _ = run([("ab", [S("a", 1000), S("b", 1100)]), ("c", [S("k", 1120 + gap)])])
    m = RE.measure(res, obs)
    b = next(o for o in obs if o["expected"] == "b")
    assert m[b["id"]]["temporal_slot"]["gap_after_ms"] == gap
    assert m[b["id"]]["temporal_slot"]["pause_adjacent"] is pause


@pytest.mark.parametrize("n_others, signalled", [(2, False), (3, True)])
def test_energy_comparison_needs_three_decoded_comparables(n_others, signalled):
    res, obs, _ = run(_s_series(n_others=n_others, target_energy=0.1))
    m = RE.measure(res, obs)
    target = [o for o in obs if o["expected"] == "s"][-1]
    assert m[target["id"]]["acoustic"]["lower_than_all_comparables"] is signalled


def test_pause_adjacent_occurrences_are_not_slot_comparables():
    words = [("mas", [S("a", 1000), S("s", 1100)]), ("mas", [S("a", 1200), S("s", 1300)]),
             ("mas", [S("a", 1400), S("s", 1500)]),            # followed by a 380 ms pause
             ("mas", [S("a", 1900), S("s", 1930)]), ("a", [S("a", 1960)])]  # target: slot 40 ms
    res, obs, _ = run(words)
    m = RE.measure(res, obs)
    s_obs = [o for o in obs if o["expected"] == "s"]
    assert m[s_obs[2]["id"]]["temporal_slot"]["pause_adjacent"]
    target = m[s_obs[3]["id"]]["temporal_slot"]
    assert target["comparables"] == 2 and not target["shorter_than_all_comparables"]


def test_estimated_regions_are_not_energy_comparables():
    words = [("mas", [S("a", 1000), S("s", 1100, acoustic=ac(1.2))]), ("mas", [S("a", 1200), S("s", 1300, acoustic=ac(1.2))]),
             ("mas", [S("a", 1400), S("s", 1500, None, acoustic=ac(1.0))]),   # not decoded: estimated region
             ("mas", [S("a", 1600), S("s", 1700, acoustic=ac(0.1))]), ("a", [S("a", 1800)])]
    res, obs, _ = run(words)
    m = RE.measure(res, obs)
    target = m[[o for o in obs if o["expected"] == "s"][-1]["id"]]["acoustic"]
    assert target["comparables"] == 2 and not target["lower_than_all_comparables"]


def test_estimated_regions_are_not_compared_acoustically():
    words = _s_series(target_observed=None, target_energy=0.01)
    res, obs, _ = run(words)
    m = RE.measure(res, obs)
    target = [o for o in obs if o["expected"] == "s"][-1]
    assert m[target["id"]]["acoustic"]["measured_over"] == "estimated region"
    assert not m[target["id"]]["acoustic"]["lower_than_all_comparables"]


def test_voicing_next_to_a_pause_is_not_a_signal():
    res, obs, _ = run([("ab", [S("a", 1000), S("b", 1100, acoustic=ac(voiced=False))]), ("c", [S("k", 1500)])])
    m = RE.measure(res, obs)
    b = next(o for o in obs if o["expected"] == "b")
    assert m[b["id"]]["temporal_slot"]["pause_adjacent"] and not m[b["id"]]["acoustic"]["voicing_not_found"]


# ----------------------------------------------------------------------
# Temporal slot: context, not duration
# ----------------------------------------------------------------------

def test_temporal_slot_is_between_neighbouring_decoded_sounds():
    res, obs, _ = run([("abc", [S("a", 1000), S("b", 1100), S("k", 1300)])])
    m = RE.measure(res, obs)
    b = next(o for o in obs if o["expected"] == "b")
    ts = m[b["id"]]["temporal_slot"]
    assert ts["slot_ms"] == 1300 - 1020
    assert ts["previous_decoded"]["end_ms"] == 1020 and ts["next_decoded"]["start_ms"] == 1300
    assert (ts["gap_before_ms"], ts["gap_after_ms"]) == (80.0, 180.0)
    assert "not the duration" in ts["note"]


def test_inserted_sounds_are_neighbours_too():
    extra = [{"phone": "ə", "start_ms": 1150.0, "end_ms": 1170.0, "frame_start": 57, "frame_end": 58}]
    res, obs, _ = run([("abc", [S("a", 1000), S("b", 1100, extras=extra), S("k", 1300)])])
    m = RE.measure(res, obs)
    b = next(o for o in obs if o["expected"] == "b")
    assert m[b["id"]]["temporal_slot"]["next_decoded"]["start_ms"] == 1150.0


def test_speaking_rate_excludes_pauses_and_splits_stretches():
    anchors = [(0.0, 20.0, "a"), (100.0, 120.0, "b"), (400.0, 420.0, "c"), (500.0, 520.0, "d")]
    rate = RE.speaking_rate(anchors)
    # gaps 80, 280 (pause), 80; span 520 - pause 280 = 240 ms for 4 sounds
    assert rate["pauses"] == 1 and rate["pause_ms_total"] == 280.0
    assert rate["phones_per_s_excluding_pauses"] == pytest.approx(4 / 0.24)
    assert [(s["start_ms"], s["end_ms"], s["sounds"]) for s in rate["stretches"]] == [(0.0, 120.0, 2), (400.0, 520.0, 2)]
    assert rate["stretches"][0]["phones_per_s"] == pytest.approx(2 / 0.12)


def test_speaking_rate_without_sounds():
    assert RE.speaking_rate([])["phones_per_s_excluding_pauses"] is None


@pytest.mark.parametrize("phones, i, pos", [
    (["k", "æ", "t"], 0, "onset"), (["k", "æ", "t"], 1, "nucleus"), (["k", "æ", "t"], 2, "coda"),
    (["æ", "k", "t", "ɪ"], 1, "between_vowels"), (["s", "t"], 0, None),
])
def test_syllable_position_is_derived_from_vowels(phones, i, pos):
    assert RE.syllable_position(phones, i) == pos


# ----------------------------------------------------------------------
# Context table: candidate explanations, never labels
# ----------------------------------------------------------------------

def _ctx(word, phones, i, prev_word_last=None, next_word_first=None, stress_known=False, stress=None):
    return {"expected": phones[i], "word": word, "context": {
        "previous_phone": phones[i - 1] if i > 0 else prev_word_last,
        "next_phone": phones[i + 1] if i < len(phones) - 1 else next_word_first,
        "word_boundary_before": i == 0, "word_boundary_after": i == len(phones) - 1,
        "sentence_position": "inside", "stress": stress, "stress_known": stress_known}}


@pytest.mark.parametrize("obs, kw, expected", [
    (_ctx("want", ["w", "ʌ", "n", "t"], 3, next_word_first="t"), {}, {"identical_neighbour", "td_between_consonants", "glottalisation"}),
    (_ctx("last", ["l", "æ", "s", "t"], 3, next_word_first="s"), {}, {"td_between_consonants", "glottalisation"}),
    (_ctx("ten", ["t", "ɛ", "n"], 2, next_word_first="b"), {}, {"alveolar_assimilation"}),
    (_ctx("would", ["w", "ʊ", "d"], 2, next_word_first="j"), {}, {"yod_coalescence", "weak_form"}),
    (_ctx("bad", ["b", "æ", "d"], 2, next_word_first="j"), {}, {"yod_coalescence"}),
    (_ctx("water", ["w", "ɔ", "t", "ɚ"], 2), {}, {"flapping"}),
    (_ctx("and", ["æ", "n", "d"], 2, next_word_first="ð"), {}, {"weak_form", "td_between_consonants"}),
    (_ctx("him", ["h", "ɪ", "m"], 0, prev_word_last="t"), {}, {"weak_form", "h_dropping"}),
    (_ctx("has", ["h", "æ", "z"], 2, next_word_first="t"), {}, {"weak_form", "final_devoicing"}),
    (_ctx("bag", ["b", "æ", "ɡ"], 2), {"edge_after": True}, {"final_devoicing"}),
    (_ctx("about", ["ɐ", "b", "aʊ", "t"], 0, stress_known=True), {}, {"unstressed_vowel"}),
    (_ctx("about", ["ɐ", "b", "aʊ", "t"], 0, stress_known=False), {}, set()),  # unknown stress is not unstressed
    (_ctx("about", ["ɐ", "b", "aʊ", "t"], 2, stress_known=True, stress="primary"), {}, set()),
])
def test_connected_speech_contexts(obs, kw, expected):
    args = {"pause_after": False, "edge_after": False} | kw
    assert set(CS.contexts(obs, **args)) == expected


@pytest.mark.parametrize("process, pattern, exp, obs, ok", [
    ("alveolar_assimilation", "substitution", "n", "m", True),
    ("alveolar_assimilation", "substitution", "n", "l", False),
    ("flapping", "substitution", "t", "ɾ", True),
    ("yod_coalescence", "substitution", "d", "dʒ", True),
    ("weak_form", "substitution", "æ", "ə", True),
    ("weak_form", "substitution", "æ", "ɑ", False),
    ("final_devoicing", "substitution", "z", "s", True),
    ("final_devoicing", "omission", "z", None, False),
    ("identical_neighbour", "omission", "t", None, True),
    ("td_between_consonants", "compression", "t", "t", True),
])
def test_process_consistency(process, pattern, exp, obs, ok):
    assert CS.consistent(process, pattern, exp, obs) is ok


def test_explanations_are_possibilities_not_proofs():
    for pid, spec in CS.PROCESSES.items():
        text = spec["explanation"].lower()
        assert "commonly" in text or "often" in text or "can" in text, pid
        for bad in ("proves", "definitely", "always", "wrong", "incorrect"):
            assert bad not in text, (pid, bad)


# ----------------------------------------------------------------------
# M2 "want to": the decoding merge must not become a swallowed /t/
# ----------------------------------------------------------------------

def _want_to(engine, merged, no_room=False):
    """raw: both /t/ decoded separately. openpronounce: /t/ of 'want' not decoded, one long /t/ for 'to'."""
    want = [S("w", 1000), S("ʌ", 1100), S("n", 1200)]
    if merged:
        t_start = 1240 if no_room else 1300
        want.append(S("t", 1220, None, dur=t_start - 1220))
        to = [S("t", t_start, dur=1760 - t_start), S("ə", 1800)]
    else:
        want.append(S("t", 1300))  # as in R01: raw's /t/ of "want" starts where OpenPronounce's merged span starts
        to = [S("t", 1660), S("ə", 1800)]
    return run([("want", want), ("to", to), ("change", [S("tʃ", 1900), S("eɪ", 2000)])], engine=engine)


def test_raw_engine_two_t_observations_give_no_reduction_candidate():
    _, obs, red = _want_to("wav2vec2_raw", merged=False)
    ts = [o for o in obs if o["expected"] == "t" and o["kind"] == "sound"]
    assert [(o["observed"], o["timing_source"]) for o in ts] == [("t", "engine"), ("t", "engine")]
    assert ts[0]["span_ms"][1] < ts[1]["span_ms"][0]
    assert cand(red, "want", "t") is None


def test_merged_t_alone_is_insufficient_and_flagged_as_possible_decoding_merge():
    _, _, red = _want_to("openpronounce", merged=True)
    c = cand(red, "want", "t")
    assert c["interpretation"]["merge_suspect"]
    assert c["interpretation"]["category"] == "insufficient_evidence"
    assert RD.MERGE_NOTE in c["interpretation"]["reasons"]


def test_merged_t_with_more_evidence_stays_ambiguous_never_omission():
    _, _, red = _want_to("openpronounce", merged=True, no_room=True)
    c = cand(red, "want", "t")
    assert c["interpretation"]["signals"] == ["omission", "no_room"]
    assert c["interpretation"]["merge_suspect"]
    assert c["interpretation"]["category"] == "ambiguous" and c["evidence_strength"] == "ambiguous"


def test_voicing_partner_merge_is_flagged():
    # "need to": /d/ decoded with a span reaching the /t/ of "to", which was not decoded
    _, _, red = run([("need", [S("n", 1000), S("iː", 1100), S("d", 1200, dur=200)]),
                     ("to", [S("t", 1400, None), S("ʊ", 1420)])], engine="openpronounce")
    c = cand(red, "to", "t")
    assert c["interpretation"]["merge_suspect"] and c["interpretation"]["category"] in ("ambiguous", "insufficient_evidence")


def _sides():
    out = []
    for engine, merged in (("wav2vec2_raw", False), ("openpronounce", True)):
        res, obs, red = _want_to(engine, merged)
        out.append({"engine": engine, "observations": obs, "reduction": red, "text": res.recording.target.text,
                    "duration_ms": res.recording.audio.duration_ms})
    return out


def test_cross_engine_comparison_preserves_the_want_to_disagreement():
    a, b = _sides()
    cmp = compare(a, b)
    assert validate_comparison(cmp, a, b) == []
    [row] = [r for r in cmp["rows"] if r["word"] == "want"]
    assert row["agreement"] == "only_second"
    assert row["first"]["observed"] == "t" and row["first"]["candidate"] is None
    assert row["second"]["observed"] is None and row["second"]["candidate"]["merge_suspect"]
    [note] = [n for n in row["notes"] if n["kind"] == "decoded_separately_elsewhere"]
    assert (note["not_decoded_by"], note["decoded_by"]) == ("openpronounce", "wav2vec2_raw")
    assert "1300–1760 ms" in note["text"] and "kept, not resolved" in note["text"]
    assert "not independent confirmation" in cmp["shared_model_note"]


def test_comparison_keeps_each_engines_own_interpretation():
    a, b = _sides()
    cmp = compare(a, b)
    tampered = copy.deepcopy(cmp)
    row = next(r for r in tampered["rows"] if r["word"] == "want")
    row["agreement"] = "same_category"  # silently reconciled
    assert any("agreement label" in i for i in validate_comparison(tampered, a, b))
    swapped = copy.deepcopy(cmp)
    for r in swapped["rows"]:
        r["first"], r["second"] = r["second"], r["first"]
    assert validate_comparison(swapped, a, b)


def test_comparison_refuses_mismatched_inputs():
    a, b = _sides()
    with pytest.raises(ValueError):
        compare(a, a)
    with pytest.raises(ValueError):
        compare(a, b | {"text": "something else"})


def test_rhotic_inventory_difference_is_paired():
    res_a, obs_a, red_a = run([("were", [S("w", 1000), S("ɜː", 1100, None), S("ɹ", 1120)])])
    res_b, obs_b, red_b = run([("were", [S("w", 1000), S("ɚ", 1100, None)])], engine="openpronounce")
    a = {"engine": "wav2vec2_raw", "observations": obs_a, "reduction": red_a, "text": res_a.recording.target.text,
         "duration_ms": res_a.recording.audio.duration_ms}
    b = {"engine": "openpronounce", "observations": obs_b, "reduction": red_b, "text": res_b.recording.target.text,
         "duration_ms": res_b.recording.audio.duration_ms}
    cmp = compare(a, b)
    assert validate_comparison(cmp, a, b) == []
    [row] = cmp["rows"]
    assert row["first"]["expected"] == "ɜː" and row["second"]["expected"] == "ɚ"


# ----------------------------------------------------------------------
# Traceability chain and integrity
# ----------------------------------------------------------------------

def test_every_candidate_keeps_the_full_chain():
    _, obs, red = _want_to("openpronounce", merged=True, no_room=True)
    by_id = {o["id"]: o for o in obs}
    for c in red["candidates"]:
        o = by_id[c["observation_id"]]
        assert c["raw_observation"]["engine"] == "openpronounce" == c["engine"]["id"]
        assert c["raw_observation"]["observed"] == o["observed"]
        assert c["raw_observation"]["expected_posterior"] == o["expected_posterior"]
        assert set(c["evidence"]) == {"temporal_slot", "acoustic", "context"}
        assert c["interpretation"]["category"] in RD.CATEGORIES
        assert c["evidence_strength"] in RD.EVIDENCE_STRENGTHS
        assert c["where"]["play_ms"] == o["play_ms"] and c["where"]["span_ms"] == o["span_ms"]
        assert c["where"]["sentence"] == "a test sentence"


def test_not_interpreted_sounds_are_never_candidates():
    pile = [{"phone": x, "start_ms": 1130.0 + i * 5, "end_ms": 1133.0 + i * 5, "frame_start": 56, "frame_end": 57}
            for i, x in enumerate("abc")]
    _, obs, red = run([("aka", [S("a", 1000, extras=pile), S("k", 1200, None, dur=60), S("a", 1300)])])
    assert {o["type"] for o in obs if o["word"] == "aka" and o["kind"] == "sound"} == {"not_interpreted"}
    assert red["candidates"] == []


def test_engine_with_unknown_stress_never_reports_unstressed():
    _, obs, red = run([("about", [S("ɐ", 1000, None), S("b", 1020), S("aʊ", 1120), S("t", 1220)])],
                      engine="openpronounce")
    for c in red["candidates"]:
        assert c["evidence"]["context"]["stress"] is None and "unstressed_vowel" not in c["evidence"]["context"]["processes"]


def test_m4_coach_is_unchanged_by_the_reduction_layer():
    res, _, _ = _want_to("openpronounce", merged=True)
    before = build_coach(res)
    before.pop("_timing_ms")
    obs = copy.deepcopy(before["observations"])
    RD.build_reduction(res, before["observations"])
    assert before["observations"] == obs


def test_reduction_is_deterministic():
    a = _want_to("openpronounce", merged=True, no_room=True)[2]
    b = _want_to("openpronounce", merged=True, no_room=True)[2]
    a.pop("timing_ms"), b.pop("timing_ms")
    assert a == b


def _valid():
    _, obs, red = _want_to("openpronounce", merged=True, no_room=True)
    _, obs2, red2 = run([("cat", [S("k", 1000), S("æ", 1100), S("t", 1120, None)]), ("go", [S("ɡ", 1140), S("oʊ", 1240)])])
    assert red["integrity"]["ok"] and red2["integrity"]["ok"]
    return obs2, red2


def _first(red):
    return red["candidates"][0]


@pytest.mark.parametrize("mutate, message", [
    (lambda r: _first(r)["where"].update(play_ms=[_first(r)["where"]["play_ms"][0] + 200, _first(r)["where"]["play_ms"][1] + 200]),
     "playback/location differs"),
    (lambda r: _first(r)["evidence"]["acoustic"].update(relative_energy=0.001), "acoustic evidence does not match"),
    (lambda r: _first(r)["where"].update(previous_phone=_first(r)["where"]["next_phone"],
                                         next_phone=_first(r)["where"]["previous_phone"]), "context does not match"),
    (lambda r: _first(r)["raw_observation"].update(engine="openpronounce"), "evidence from a different engine"),
    (lambda r: _first(r)["interpretation"].update(signals=["omission"], streams=["recognition"]), "single evidence stream"),
    (lambda r: _first(r).update(evidence_strength="high") or _first(r)["interpretation"].update(evidence_strength="high"),
     "unknown or inconsistent evidence strength"),
    (lambda r: _first(r).update(summary="This /t/ was wrong."), "judgemental or overclaiming wording"),
    (lambda r: _first(r)["interpretation"].update(candidate_explanations=[]), "without a candidate explanation"),
    (lambda r: _first(r)["evidence"]["temporal_slot"].update(slot_ms=5.0), "temporal slot does not match"),
    (lambda r: _first(r)["evidence"]["context"].update(stress="unstressed"), "stress reported"),
    (lambda r: r["groups"].clear(), "exactly one group"),
    (lambda r: _first(r)["interpretation"].update(merge_suspect=True), "decoding merge not kept ambiguous"),
])
def test_validator_catches_violations(mutate, message):
    obs, red = _valid()
    bad = copy.deepcopy(red)
    mutate(bad)
    issues = RD.validate_reduction(bad, obs, duration_ms=5000.0)
    assert any(message in i for i in issues), issues


def test_unavailable_result_has_an_empty_reduction():
    e = RD.empty_reduction("unavailable", {"id": "wav2vec2_raw"})
    assert e["candidates"] == [] and e["state"] == "unavailable" and e["integrity"]["ok"]


# ----------------------------------------------------------------------
# Ground truth: R01–R20 only
# ----------------------------------------------------------------------

@pytest.mark.parametrize("rid", ["New Recording 49", "Recording 49", "R21", "R00", "app-12345678", ""])
def test_only_benchmark_recordings_are_ground_truth(rid, tmp_path):
    (tmp_path / "benchmark_manifest.csv").write_text(
        "recording_id,filename,target_text,reading_style,purpose\nR01,a.m4a,Text.,slow,x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        require_ground_truth(rid, tmp_path)


def test_ground_truth_reads_the_manifest(tmp_path):
    (tmp_path / "benchmark_manifest.csv").write_text(
        "recording_id,filename,target_text,reading_style,purpose\nR01,a.m4a,Text.,slow,x\n", encoding="utf-8")
    assert require_ground_truth("R01", tmp_path)["target_text"] == "Text."
    with pytest.raises(ValueError):
        require_ground_truth("R02", tmp_path)  # in R01–R20 but not in this manifest
    assert GROUND_TRUTH_IDS == tuple(f"R{i:02d}" for i in range(1, 21))
