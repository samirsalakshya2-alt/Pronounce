"""M10 core: identity, eligibility, chance awareness, personal vs article, states, context, determinism.

All histories here are SYNTHETIC (m10helpers) and live in temporary stores.
"""

import dataclasses
import json

import m9helpers as H
import pytest
from m10helpers import History, W, clear, ok, pattern, personal_baseline, quiet_texts, stressed

from pronunciation_lab.longitudinal import identity as I
from pronunciation_lab.longitudinal import progress as PG
from pronunciation_lab.longitudinal.calibration import DEFAULT_LONGITUDINAL
from pronunciation_lab.longitudinal.source import text_groups


def canon(r):
    return json.dumps({k: v for k, v in r.items() if k not in ("generated_at", "update")}, sort_keys=True, default=str)


# ----------------------------------------------------------------------
# A. Pattern identity
# ----------------------------------------------------------------------

@pytest.mark.parametrize("target", [
    {"target_id": "contrast:ɛ~ɪ", "kind": "CONTRAST", "pairs": ["ɛ→ɪ", "ɪ→ɛ"]},
    {"target_id": "set:front_vowel_ladder", "kind": "SET", "pairs": ["ɛ→ɪ", "eɪ→ɪ", "ɪ→ɛ"], "family_id": "front_vowel_ladder"},
    {"target_id": "conditioned:medial:contrast:ɛ~ɪ", "kind": "CONDITIONED", "pairs": ["ɛ→ɪ"], "condition": "medial"},
    {"target_id": "lexical:best:ɛ", "kind": "LEXICAL", "pairs": ["ɛ→ɪ"], "word": "best"},
])
def test_different_m9_target_ids_map_to_the_same_canonical_pattern(target):
    link = I.link_target(target)
    assert "sub:ɛ>ɪ" in link["patterns"] and link["identity_version"] == I.IDENTITY_VERSION
    assert link["m9_target_id"] == target["target_id"]


def test_direction_word_and_context_are_distinct_but_reduce_to_the_broad_pattern():
    assert I.broad_id("ɛ", "ɪ") != I.broad_id("ɪ", "ɛ") and I.reverse_of("sub:ɛ>ɪ") == "sub:ɪ>ɛ"
    assert I.contrast_group("sub:ɛ>ɪ") == I.contrast_group("sub:ɪ>ɛ") == "contrast:ɛ~ɪ"
    ctx, word = I.context_id("ɛ", "ɪ", "word_position", "medial"), I.word_id("ɛ", "ɪ", "anthropic")
    assert ctx == "sub:ɛ>ɪ@word_position=medial" and word == "sub:ɛ>ɪ#word=anthropic"
    assert I.broad_of(ctx) == I.broad_of(word) == "sub:ɛ>ɪ"
    assert I.parse(word)["word"] == "anthropic" and I.parse(ctx)["context"] == {"dimension": "word_position", "value": "medial"}
    assert I.link_target({"target_id": "fluency:hesitation", "kind": "FLUENCY", "group": "hesitation"})["patterns"] == ["fluency:hesitation"]
    assert I.link_target({"target_id": "clarity:final_consonant", "kind": "CLARITY"})["tracked"] is False
    with pytest.raises(ValueError):
        I.parse("contrast:ɛ~ɪ")


def test_history_from_differently_named_m9_targets_lands_on_one_pattern(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    r = h.progress()
    # whichever M9 target the advice used, it links to the same longitudinal history
    for t in ({"target_id": "contrast:ɛ~ɪ", "kind": "CONTRAST", "pairs": ["ɛ→ɪ"]},
              {"target_id": "set:front_vowel_ladder", "kind": "SET", "pairs": ["ɛ→ɪ", "eɪ→ɪ"]}):
        coaching = {"actions": [{"target": t}], "pool": {"engine": "wav2vec2_raw"}}
        adapted = PG.adapt_coaching(coaching, r)["actions"][0]
        assert any(x["pattern"] == "sub:ɛ>ɪ" and x["state"] == "PERSONAL_RECURRING" for x in adapted["history"])


# ----------------------------------------------------------------------
# B. Eligibility: nothing silently dropped, nothing ineligible counted
# ----------------------------------------------------------------------

@pytest.mark.parametrize("kw, reason", [
    ({"identity": "MISMATCH"}, "mismatch"), ({"identity": "AMBIGUOUS"}, "unconfirmed_identity"),
    ({"withheld": True}, "boundary_withheld"), ({"withheld": True, "withheld_reason": "containment"}, "containment_withheld"),
    ({"state": "TOO_SHORT"}, "not_analysed"), ({"state": "ANALYSIS_FAILED"}, "not_analysed"),
    ({"disposition": "discarded"}, "discarded"), ({"disposition": "rerecord_requested"}, "rerecorded"),
    ({"job_engine": "openpronounce"}, "other_engine"),
])
def test_ineligible_attempts_are_kept_with_their_reason_and_never_counted(tmp_path, kw, reason):
    h = History(tmp_path)
    for t in range(4):
        h.read(f"bad{t}", clear("ɛ", "ɪ", W(f"x{t}", 3)) + ok("ɛ", W(f"y{t}", 5)), **kw)
    r = h.progress()
    assert r["eligibility"].get(reason) == 4 and "eligible" not in r["eligibility"]
    assert pattern(r, "sub:ɛ>ɪ") is None and r["history"]["fresh_readings"] == 0


@pytest.mark.parametrize("obs, what", [
    ([H.sub("roses", "ᵻ", "ɪ") for _ in range(3)], "reference_variant"),
    ([H.sub("the", "ə", "ɪ") for _ in range(3)], "function_word_variant"),
    ([H.sub("leaders", "iː", "t") for _ in range(3)], "implausible_pair"),
])
def test_excluded_observations_are_neither_differences_nor_chances(tmp_path, obs, what):
    h = History(tmp_path)
    for t in range(4):
        h.read(f"ex{t}", obs + ok("ɛ", W(f"e{t}", 4)))
    r = h.progress()
    assert all(it["totals"]["clear"] == 0 for it in r["patterns"]), what


def test_not_detected_is_never_absence_and_ambiguous_never_clear(tmp_path):
    h = History(tmp_path)
    for t in range(5):
        h.read(f"nd{t}", [H.omit(w, "t") for w in W(f"o{t}", 3)] + [H.amb(w, "ɛ", "ɪ") for w in W(f"a{t}", 3)]
               + ok("ɛ", W(f"k{t}", 4)))
    r = h.progress()
    it = pattern(r, "sub:ɛ>ɪ")
    assert it is None or (it["totals"]["clear"] == 0 and it["state"] == "INSUFFICIENT_HISTORY")
    assert not any(p["pattern"].startswith("sub:t>") for p in r["patterns"])   # omissions create no pattern


# ----------------------------------------------------------------------
# C. Chance awareness
# ----------------------------------------------------------------------

def test_one_good_reread_is_not_improvement_and_one_bad_one_is_not_regression(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    before = h.progress()
    assert pattern(before, "sub:ɛ>ɪ")["state"] == "PERSONAL_RECURRING"
    # the same texts read again, perfectly: repeats are never evidence of change
    for t in range(3):
        h.read(f"text-b{t}", ok("ɛ", W(f"b{t}", 2)) + ok("ɛ", W(f"b{t}ok", 8)))
    after = h.progress()
    it = pattern(after, "sub:ɛ>ɪ")
    assert it["state"] == "PERSONAL_RECURRING" and after["history"]["classes"]["REPEAT"] == 3
    assert it["evidence_classes"]["REPEAT"]["readings"] == 3 and it["later"]["opportunities"] == 0


def test_a_bad_reread_does_not_reopen_a_stable_pattern(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    quiet_texts(h, texts=6)
    assert pattern(h.progress(), "sub:ɛ>ɪ")["state"] in ("STABLE", "RETIRED")
    h.read("text-b0", clear("ɛ", "ɪ", W("b0", 2)) + ok("ɛ", W("b0ok", 8)))   # an old text, badly: a repeat
    assert pattern(h.progress(), "sub:ɛ>ɪ")["state"] in ("STABLE", "RETIRED")


def test_article_composition_cannot_fake_a_trend(tmp_path):
    # later texts simply contain no /ɛ/: "not seen" is "not tested", never improvement
    h = History(tmp_path)
    personal_baseline(h)
    for t in range(6):
        h.read(f"nothing{t}", ok("s", W(f"n{t}", 12)))
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert it["state"] == "PERSONAL_RECURRING" and (it["later"] or {}).get("opportunities", 0) == 0


def test_opportunities_not_sessions_decide(tmp_path):
    # four later texts, but only 2 chances each: far below the retirement minimum
    h = History(tmp_path)
    personal_baseline(h)
    quiet_texts(h, texts=4, ok_per=2)
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert it["state"] not in ("STABLE", "RETIRED", "IMPROVING") and it["later"]["opportunities"] == 8


# ----------------------------------------------------------------------
# D. Personal vs article / word
# ----------------------------------------------------------------------

def test_recurring_across_three_sessions_of_two_texts_is_personal(tmp_path):
    h = History(tmp_path)
    h.read("A", clear("ɛ", "ɪ", W("a", 1)) + ok("ɛ", W("ao", 6)))
    h.read("B", clear("ɛ", "ɪ", W("b", 1)) + ok("ɛ", W("bo", 6)))
    assert pattern(h.progress(), "sub:ɛ>ɪ")["state"] == "INSUFFICIENT_HISTORY"
    h.read("C", clear("ɛ", "ɪ", W("c", 1)) + ok("ɛ", W("co", 6)))
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert (it["state"], it["scope"]) == ("PERSONAL_RECURRING", "PERSONAL_RECURRING")
    assert it["text"] == "Recurring across 3 readings of 3 different texts, in 3 words — likely personal."


def test_many_times_inside_one_article_is_not_personal(tmp_path):
    h = History(tmp_path)
    h.read("same", *[clear("ɛ", "ɪ", W(f"s{k}", 2)) + ok("ɛ", W(f"so{k}", 4)) for k in range(6)])
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert it["state"] == "INSUFFICIENT_HISTORY" and it["scope"] == "ARTICLE_BOUND"
    assert it["text"] == "Appears limited to the article tested so far." and not it["decision"]["active"]


def test_excerpts_of_one_article_pasted_separately_are_one_text():
    groups = text_groups({"full": "alpha bravo charlie delta echo foxtrot golf hotel india juliet",
                          "excerpt": "charlie delta echo foxtrot golf hotel", "other": "kilo lima mike november oscar papa"},
                         0.8, 5)
    assert groups["full"] == groups["excerpt"] != groups["other"]


def test_one_word_only_is_word_specific_and_active_only_across_texts(tmp_path):
    h = History(tmp_path)
    for t in range(3):
        h.read(f"w{t}", clear("ɑː", "oʊ", ["anthropic"]) + ok("ɑː", W(f"wo{t}", 5)))
    it = pattern(h.progress(), "sub:ɑː>oʊ")
    assert it["scope"] == "WORD_SPECIFIC" and it["state"] == "INSUFFICIENT_HISTORY"
    assert it["text"] == "Recurring in ‘anthropic’ across 3 different texts: specific to this word so far."
    assert it["decision"]["decision"] == "CONTINUE_CURRENT_TARGET" and it["decision"]["unit"]["kind"] == "word"


def test_insufficient_history_says_so(tmp_path):
    h = History(tmp_path)
    h.read("one", clear("ɛ", "ɪ", W("o", 2)) + ok("ɛ", W("oo", 6)))
    r = h.progress()
    assert pattern(r, "sub:ɛ>ɪ")["state"] == "INSUFFICIENT_HISTORY"
    assert r["depth"].startswith("1 reading of 1 text so far") and r["next_practice"] == []


def test_emerging_needs_every_personal_gate_but_the_third_session(tmp_path):
    h = History(tmp_path)
    h.read("E1", clear("ɛ", "ɪ", W("e1", 2)) + ok("ɛ", W("e1o", 6)))
    h.read("E2", clear("ɛ", "ɪ", W("e2", 1)) + ok("ɛ", W("e2o", 6)))
    assert pattern(h.progress(), "sub:ɛ>ɪ")["state"] == "EMERGING"
    # scattered: the same counts, but /ɛ/ goes many ways: not one coherent pattern, not emerging
    g = History(tmp_path / "g")
    g.read("S1", clear("ɛ", "ɪ", W("s1", 2)) + clear("ɛ", "æ", W("s1a", 2)) + clear("ɛ", "ə", W("s1b", 2)))
    g.read("S2", clear("ɛ", "ɪ", W("s2", 1)) + clear("ɛ", "æ", W("s2a", 2)) + clear("ɛ", "ə", W("s2b", 2)))
    assert pattern(g.progress(), "sub:ɛ>ɪ")["state"] == "INSUFFICIENT_HISTORY"


# ----------------------------------------------------------------------
# E. Retirement ("stable for now") and F. regression
# ----------------------------------------------------------------------

def test_improving_then_stable_then_retired_with_expected_counts(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)                                        # 6 clear in 30 chances: rate 0.2
    quiet_texts(h, texts=2)                                     # 24 later chances, 0 observed, about 4.8 predicted
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert it["state"] == "IMPROVING" and it["later"]["expected"] == 4.8 and it["later"]["observed"] == 0
    assert it["text"].startswith("Early evidence suggests it is occurring less often: not observed in 24 chances")
    quiet_texts(h, texts=2, prefix="r")                         # 48 later chances in 4 texts: retire
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert it["state"] == "STABLE" and it["decision"]["decision"] == "RETIRE"
    assert it["text"] == ("Stable for now: not observed in 48 chances across 4 later readings, where your earlier rate "
                          "predicts about 9.6. No longer recurring strongly enough to prioritise.")
    quiet_texts(h, texts=2, prefix="w")                         # a quiet watch window: retired, monitored silently
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert it["state"] == "RETIRED" and it["decision"]["decision"] == "WATCH_FOR_REGRESSION"
    assert [t["to"] for t in it["transitions"]] == ["EMERGING", "PERSONAL_RECURRING", "IMPROVING", "STABLE", "RETIRED"]


@pytest.mark.parametrize("later_ok, texts, retired", [
    (13, 3, False),    # 39 chances: one below the minimum of 40
    (10, 4, True),     # exactly 40
    (12, 4, True),     # above
])
def test_retirement_needs_the_opportunity_minimum(tmp_path, later_ok, texts, retired):
    h = History(tmp_path)
    personal_baseline(h)
    quiet_texts(h, texts=texts, ok_per=later_ok)
    st = pattern(h.progress(), "sub:ɛ>ɪ")["state"]
    assert (st == "STABLE") is retired, (later_ok * texts, st)


def test_retirement_needs_new_words_several_texts_and_a_meaningful_prediction(tmp_path):
    # the later chances are all in the baseline's own words: not "mostly new words"
    h = History(tmp_path)
    personal_baseline(h)
    for t in range(4):
        h.read(f"old-words{t}", ok("ɛ", W("b0ok", 8) + W("b1ok", 4)))
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert it["state"] != "STABLE" and it["later"]["fresh_word_share"] == 0.0
    # a rare pattern whose baseline predicts fewer than 3 in the later window can't retire
    g = History(tmp_path / "rare")
    personal_baseline(g, clear_per=1, ok_per=60)
    quiet_texts(g, texts=4, ok_per=12)
    it = pattern(g.progress(), "sub:ɛ>ɪ")
    assert it["state"] != "STABLE" and it["later"]["expected"] < 3


def test_not_enough_later_sessions_or_texts_blocks_retirement(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    h.read("big", *[ok("ɛ", W(f"big{k}", 25)) for k in range(2)])   # 50 chances, one session, one text
    assert pattern(h.progress(), "sub:ɛ>ɪ")["state"] != "STABLE"


def test_one_isolated_recurrence_never_reopens_but_the_original_gates_do(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    quiet_texts(h, texts=6)
    assert pattern(h.progress(), "sub:ɛ>ɪ")["state"] == "RETIRED"
    h.read("iso", clear("ɛ", "ɪ", W("iso", 1)) + ok("ɛ", W("isoo", 8)))
    assert pattern(h.progress(), "sub:ɛ>ɪ")["state"] == "RETIRED"
    h.read("re1", clear("ɛ", "ɪ", W("re1", 1)) + ok("ɛ", W("re1o", 8)))
    assert pattern(h.progress(), "sub:ɛ>ɪ")["state"] == "RETIRED"     # 2 of 3 sessions: not yet
    h.read("re2", clear("ɛ", "ɪ", W("re2", 1)) + ok("ɛ", W("re2o", 8)))
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert it["state"] == "REGRESSED" and it["decision"]["decision"] == "CONTINUE_CURRENT_TARGET"
    assert it["text"] == "This pattern had become stable, but it has started recurring again across several readings."
    assert [t["to"] for t in it["transitions"]][-3:] == ["STABLE", "RETIRED", "REGRESSED"]


def test_persistent_and_the_hysteresis_band(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    for t in range(3):                                          # later: at the baseline rate
        h.read(f"p{t}", clear("ɛ", "ɪ", W(f"p{t}", 2)) + ok("ɛ", W(f"po{t}", 8)))
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert it["state"] == "PERSISTENT" and it["text"].startswith("This remains one of your recurring patterns")


# ----------------------------------------------------------------------
# G. Context map (within the sound only)
# ----------------------------------------------------------------------

def test_context_is_compared_within_the_sound_and_must_hold_in_both_halves(tmp_path):
    h = History(tmp_path)
    for t in range(4):
        stressed_clear = [stressed(o) for o in clear("ɛ", "ɪ", W(f"s{t}", 2))]
        stressed_ok = [stressed(o) for o in ok("ɛ", W(f"so{t}", 2))]
        unstressed_ok = [stressed(o, None) for o in ok("ɛ", W(f"u{t}", 8))]        # unstressed /ɛ/: fine
        h.read(f"c{t}", stressed_clear + stressed_ok + unstressed_ok)
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert it["scope"] == "CONTEXT_SPECIFIC"
    [c] = [c for c in it["context"]["concentrated"] if c["dimension"] == "dictionary_stress"]
    assert c["value"] == "primary" and c["inside"] == {"opportunities": 16, "clear": 8}
    assert it["context"]["note"] == "Stress is the pronunciation dictionary's, not measured from your voice."
    assert it["decision"]["unit"]["kind"] == "context"


def test_context_seen_in_one_half_only_or_too_few_chances_is_not_reported(tmp_path):
    h = History(tmp_path)
    for t in range(4):
        obs = [stressed(o) for o in clear("ɛ", "ɪ", W(f"s{t}", 1))] + ok("ɛ", W(f"u{t}", 6)) \
            if t < 2 else clear("ɛ", "ɪ", W(f"s{t}", 1)) + [stressed(o) for o in ok("ɛ", W(f"so{t}", 3))] + ok("ɛ", W(f"u{t}", 3))
        h.read(f"c{t}", obs)
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert not [c for c in it["context"]["concentrated"] if c["dimension"] == "dictionary_stress"]


# ----------------------------------------------------------------------
# Fluency patterns (sentence-level chances)
# ----------------------------------------------------------------------

def test_fluency_patterns_count_measurable_sentences(tmp_path):
    h = History(tmp_path)
    for t in range(3):
        h.read(f"f{t}", ok("ɛ", W(f"f{t}", 3)), ok("ɛ", W(f"g{t}", 3)), fluency=H.fluency(H.hesitation()))
    it = pattern(h.progress(), "fluency:hesitation")
    assert it["state"] == "PERSONAL_RECURRING" and it["totals"]["opportunities"] == 6
    g = History(tmp_path / "unmeasurable")
    for t in range(3):
        g.read(f"f{t}", ok("ɛ", W(f"f{t}", 3)), fluency=H.fluency(H.hesitation(), reliable=False))
    assert pattern(g.progress(), "fluency:hesitation") is None


# ----------------------------------------------------------------------
# L. Determinism, engine separation, synthetic depths, validation
# ----------------------------------------------------------------------

def test_deterministic(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    quiet_texts(h, texts=3)
    assert canon(h.progress(persist=False)) == canon(h.progress(persist=False)) == canon(h.progress(rebuild=True))


def test_engines_are_never_pooled(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)                                                       # wav2vec2_raw
    for t in range(3):
        h.read(f"op{t}", clear("s", "z", W(f"op{t}", 2)) + ok("s", W(f"opo{t}", 6)), engine="openpronounce")
    raw, op = h.progress(engine="wav2vec2_raw"), h.progress(engine="openpronounce")
    assert pattern(raw, "sub:ɛ>ɪ") and not pattern(raw, "sub:s>z")
    assert pattern(op, "sub:s>z")["state"] == "PERSONAL_RECURRING" and not pattern(op, "sub:ɛ>ɪ")
    assert set(raw["engines"]) == {"wav2vec2_raw", "openpronounce"} and "not independent confirmation" in raw["engine_note"]
    assert h.progress()["engine"] == "openpronounce"                          # the most recent reading's engine
    coaching = {"actions": [{"target": {"target_id": "contrast:ɛ~ɪ", "kind": "CONTRAST", "pairs": ["ɛ→ɪ"]}}],
                "pool": {"engine": "openpronounce"}}
    assert PG.adapt_coaching(coaching, raw)["actions"][0]["history"] == []     # never cross-engine


@pytest.mark.parametrize("n, expect", [(1, "INSUFFICIENT_HISTORY"), (3, "PERSONAL_RECURRING"), (5, "PERSONAL_RECURRING"),
                                       (10, "PERSISTENT"), (22, "PERSISTENT")])
def test_synthetic_histories_of_1_3_5_10_and_20_plus_readings(tmp_path, n, expect):
    h = History(tmp_path)
    personal_baseline(h, texts=n)
    r = h.progress()
    assert pattern(r, "sub:ɛ>ɪ")["state"] == expect and r["integrity"]["ok"]
    assert r["history"]["fresh_sessions"] == n


def test_the_result_is_validated_and_fails_closed(tmp_path, monkeypatch):
    h = History(tmp_path)
    personal_baseline(h)
    r = h.progress()
    assert r["integrity"]["ok"]
    bad = json.loads(json.dumps(r))
    bad["patterns"][0]["text"] = "Your pronunciation improved 82% and is fixed forever."
    bad["patterns"][1 if len(bad["patterns"]) > 1 else 0]["examples"].append({"observation": "nowhere:x"})
    bad["score"] = 1
    issues = PG.validate(bad, {o for it in r["patterns"] for o in it["clear_observations"]})
    assert any("forbidden wording" in i for i in issues) and any("no 'score'" in i for i in issues)
    assert any("not a source observation" in i for i in issues)
    monkeypatch.setattr(PG, "validate", lambda *a, **k: ["synthetic defect"])
    r = h.progress(persist=False)
    assert r["integrity"]["ok"] is False and r["patterns"] == [] and r["next_practice"] == []


def test_calibration_is_named_versioned_and_only_tightened_by_noise(tmp_path):
    cal = DEFAULT_LONGITUDINAL
    assert cal.version.startswith("m10-cal.") and cal.retire_min_opportunities == 40
    h = History(tmp_path)
    personal_baseline(h)
    assert h.progress()["noise"]["source"] == "defaults"
    loose = dataclasses.replace(cal, retire_max_share=0.9)
    assert loose.retire_max_share == 0.9    # every threshold is a parameter, never a constant in the logic


def test_the_running_window_equals_recomputing_it():
    import random
    from collections import Counter
    from pronunciation_lab.longitudinal import states as S
    rng = random.Random(7)
    pts = []
    for k in range(40):
        cw = Counter({f"w{rng.randint(0, 9)}": rng.randint(0, 2) for _ in range(3)})
        pts.append({"session_id": f"s{k}", "time": f"t{k:03d}", "article": f"a{rng.randint(0, 5)}", "opportunities": rng.randint(0, 20),
                    "clear": sum(cw.values()), "ambiguous": rng.randint(0, 3), "clear_differences": rng.choice([None, rng.randint(0, 6)]),
                    "clear_words": +cw, "clear_by_word": +cw, "opps_by_word": Counter({f"w{rng.randint(0, 20)}": 3}), "clear_ids": []})
    for a in range(0, 40, 7):
        w = S.Window()
        for b in range(a, 40):
            w.add(pts[b])
            assert w.view() == S.window(pts[a:b + 1]), (a, b)


# ----------------------------------------------------------------------
# Gaps found by fault injection: each rule pinned on its own
# ----------------------------------------------------------------------

def test_a_sentence_designated_as_retest_and_read_again_is_retest_evidence(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    first = h.store.session_ids()
    sid = next(s for s in first if h.store.load_session(s)["article_id"] == h.texts["text-b0"]["id"])
    h.store.save_coaching(sid, {"generated_at": "2026-11-01T00:03:30+00:00", "pool": {"engine": "wav2vec2_raw"},
                                "actions": [{"target": {"target_id": "contrast:ɛ~ɪ", "kind": "CONTRAST", "pairs": ["ɛ→ɪ"]},
                                             "practice": {"retest_sentences": [{"text": h.texts["text-b0"]["segments"][0]["text"]}]}}]})
    h.read("text-b0", ok("ɛ", W("b0", 10)))            # M9's retest sentence, read again in an ordinary session
    h.read("text-b1", ok("ɛ", W("b1", 10)))            # an old sentence that was never designated
    r = h.progress()
    assert r["history"]["classes"]["RETEST"] == 1 and r["history"]["classes"]["REPEAT"] == 1


def test_not_detected_and_excluded_observations_are_not_chances(tmp_path):
    h = History(tmp_path)
    for t in range(3):
        h.read(f"T{t}", clear("ɛ", "ɪ", W(f"c{t}", 1)) + ok("ɛ", W(f"k{t}", 6)) + [H.omit(w, "ɛ", position="medial") for w in W(f"n{t}", 3)]
               + [H.sub("then", "ɛ", "ɪ")] + [H.uninterpreted(w, "ɛ") for w in W(f"u{t}", 2)])
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert it["totals"]["opportunities"] == 21 and it["totals"]["clear"] == 3


def test_the_noise_floor_from_rereads_can_only_tighten(tmp_path):
    h = History(tmp_path)
    for s in range(3):                                   # three sentences, each read four times, fewer later
        for read, n in enumerate((3, 2, 1, 0)):
            h.read(f"rr{s}", clear("ɛ", "ɪ", W(f"rr{s}", n)) + ok("ɛ", W(f"rro{s}", 6)))
    noise = h.progress()["noise"]
    assert noise["source"] == "user_rereads" and noise["sentences_used"] == 3 and noise["lowest_half_ratio"] == 0.2
    assert noise["effective"] == {"retire_max_share": 0.2, "improve_max_share": 0.2}
    assert noise["position_repeat"]["clear_before"] > 0 and "smaller" not in noise["text"]


def test_one_text_read_in_many_sessions_is_article_bound_not_personal(tmp_path):
    h = History(tmp_path)
    for s in range(4):
        h.read_at("long", 8, {s: clear("ɛ", "ɪ", W(f"l{s}", 2)) + ok("ɛ", W(f"lo{s}", 6))})
    r = h.progress()
    assert r["history"]["classes"] == {"FRESH": 4} and r["history"]["articles"] == 1
    it = pattern(r, "sub:ɛ>ɪ")
    assert (it["state"], it["scope"]) == ("INSUFFICIENT_HISTORY", "ARTICLE_BOUND") and not it["decision"]["active"]


def test_scattered_directions_never_become_personal(tmp_path):
    h = History(tmp_path)
    for t in range(3):
        h.read(f"sc{t}", clear("ɛ", "ɪ", W(f"i{t}", 1)) + clear("ɛ", "æ", W(f"a{t}", 2)) + clear("ɛ", "ə", W(f"e{t}", 2))
               + ok("ɛ", W(f"o{t}", 6)))
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert it["totals"]["direction_share"] == 0.2 and it["state"] == "INSUFFICIENT_HISTORY"


def test_two_observations_are_not_emerging(tmp_path):
    h = History(tmp_path)
    h.read("E1", clear("ɛ", "ɪ", W("e1", 1)) + ok("ɛ", W("e1o", 6)))
    h.read("E2", clear("ɛ", "ɪ", W("e2", 1)) + ok("ɛ", W("e2o", 6)))
    assert pattern(h.progress(), "sub:ɛ>ɪ")["state"] == "INSUFFICIENT_HISTORY"


def test_retirement_needs_several_later_texts(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    for s in range(4):                                   # 4 later sessions, 48 chances, but all from ONE text
        h.read_at("one-later-text", 4, {s: ok("ɛ", W(f"ol{s}", 12))})
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert it["later"]["sessions"] == 4 and it["later"]["articles"] == 1 and it["state"] != "STABLE"


def test_a_clear_observation_in_a_word_fragment_breaks_word_specificity(tmp_path):
    h = History(tmp_path)
    h.read("F1", clear("ɑː", "oʊ", ["anthropic"]) + ok("ɑː", W("f1", 5)))
    h.read("F2", clear("ɑː", "oʊ", ["anthropic"]) + ok("ɑː", W("f2", 5)))
    h.read("F3", clear("ɑː", "oʊ", ["ro"]) + ok("ɑː", W("f3", 5)))        # 'ro': a fragment, still a clear observation
    it = pattern(h.progress(), "sub:ɑː>oʊ")
    assert it["scope"] != "WORD_SPECIFIC"


def test_hysteresis_keeps_improving_inside_the_band(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    quiet_texts(h, texts=2)                                                 # improving: 0 of about 4.8
    h.read("band", clear("ɛ", "ɪ", W("band", 4)) + ok("ɛ", W("bando", 8)))  # 4 of about 7.2: between 0.5 and 0.75
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert it["state"] == "IMPROVING" and it["later"]["observed"] == 4 and it["later"]["expected"] == 7.2


def test_a_context_covering_most_chances_is_not_specific(tmp_path):
    h = History(tmp_path)
    for t in range(4):
        h.read(f"m{t}", clear("ɛ", "ɪ", W(f"m{t}", 2)) + ok("ɛ", W(f"mo{t}", 14)) + ok("ɛ", W(f"f{t}", 4), position="final"))
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    assert not [c for c in it["context"]["concentrated"] if c["dimension"] == "word_position"]   # medial = 80 % of chances


def test_a_context_seen_in_one_half_only_is_not_reported(tmp_path):
    h = History(tmp_path)
    for t in range(4):
        if t < 2:   # first half: stressed /ɛ/ heard as /ɪ/
            obs = [stressed(o) for o in clear("ɛ", "ɪ", W(f"s{t}", 2))] + [stressed(o, None) for o in ok("ɛ", W(f"u{t}", 8))]
        else:       # second half: the difference is in unstressed syllables, stressed ones are fine
            obs = [stressed(o, None) for o in clear("ɛ", "ɪ", W(f"s{t}", 1))] + [stressed(o) for o in ok("ɛ", W(f"so{t}", 4))] \
                + [stressed(o, None) for o in ok("ɛ", W(f"u{t}", 4))]
        h.read(f"c{t}", obs)
    it = pattern(h.progress(), "sub:ɛ>ɪ")
    row = next(r for r in it["context"]["dimensions"]["dictionary_stress"] if r["value"] == "primary")
    assert not row["stable_in_both_halves"] and not row["concentrated"]
