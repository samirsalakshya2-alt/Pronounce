"""M9 "This reading": a current-reading diagnosis of ONE reading (synthetic evidence; one session = one reading).

Major improvement areas first (paths A clear / B mixed / C possible), then what was already stable, then fluency
and cautions. Clear and ambiguous evidence are never merged.
"""

import ast
import copy
from pathlib import Path

import m9helpers as H
import pytest

from pronunciation_lab.coaching import reading as R
from pronunciation_lab.coaching.calibration import DEFAULT_READING
from pronunciation_lab.coaching.engine import run_coaching
from pronunciation_lab.coaching.evidence import normalise
from pronunciation_lab.coaching.patterns import build_index

W = H.words("sit", "list", "fill", "bit", "with", "this")


def reading(*obs, n=6, session=7, **kw):
    """One reading: n sentences of one session (observations spread round-robin over the sentences)."""
    return H.scatter(list(obs), n_readings=n, sessions=1, start=session * 100, **kw)


def fb(inputs, coverage=None, **kw):
    return R.build_reading_feedback(inputs, session_id=inputs[0].reading.session_id if inputs else "s",
                                    article_title="A", coverage=coverage or {"sentences": len(inputs), "recorded": len(inputs)},
                                    generated_at="t", **kw)


def units_of(ins):
    return build_index([u for i in ins for u in normalise(i)[0]], []).units


def areas(r):
    return r["improvement_areas"]


def ambs(expected, heard, ws):
    return [H.amb(w, expected, heard) for w in ws]


# ----------------------------------------------------------------------
# Major improvement areas: the three paths
# ----------------------------------------------------------------------

def test_recurring_clear_problems_become_major_improvement_areas():                       # 1
    r = fb(reading(H.subs("ɪ", "iː", 4, W), H.ok("sit", "ɪ", 6), H.ok("seed", "iː", 3)))
    assert r["state"] == "feedback" and r["integrity"]["ok"], r["integrity"]
    [a] = areas(r)
    assert (a["kind"], a["band"], a["path"], a["origin"]) == ("CONTRAST", "clear", "A", "counted")
    assert a["text"] == "In this reading, /ɪ/ was heard as /iː/ 4 times in 4 sentences."
    assert a["evidence_text"] == ("Individual observations: 4 observed differences in 4 words: 4 clear "
                                  "(4 high-confidence, 0 moderate-confidence) and 0 ambiguous.")
    assert (a["pattern_scope"], a["pattern_label"]) == ("sound", "Recurring sound pattern")
    assert a["pattern_text"] == ("Pattern: recurring. Heard clearly 4 times in the same direction, in 4 sentences and "
                                 "4 words. Of the 4 clear differences of /ɪ/ in this reading, 4 went this way.")
    assert a["order_text"] is None                                                  # the only area
    assert a["rate_text"] == "Clear-evidence rate: 4 of 10 occurrences of /ɪ/."
    assert a["counter_text"] == "/ɪ/ was heard as expected 6 times in this reading."
    assert a["counts"]["clear_rate"] == 0.4 and a["counts"]["rate_band"] == "high"
    assert 1 <= len(a["examples"]) <= 3 and all(e["evidence"] == "clear" for e in a["examples"]) and a["counter_examples"]
    assert r["no_area_text"] is None
    for k in ("hypothesis", "tier", "practice", "action_text", "decided_by"):
        assert k not in a and k not in r


def test_mixed_evidence_qualifies_with_two_clear_plus_ambiguous_support():               # 2, 6
    r = fb(reading(H.sub("best", "ɛ", "ɪ"), H.sub("spell", "ɛ", "ɪ"), H.amb("level", "ɛ", "ɪ"), H.ok("bed", "ɛ", 10)))
    assert r["integrity"]["ok"], r["integrity"]
    [a] = areas(r)
    assert (a["band"], a["path"]) == ("mixed", "B")
    assert a["text"] == "In this reading, /ɛ/ was heard as /ɪ/ 3 times in 3 sentences."
    assert a["evidence_text"].endswith("in 3 words: 2 clear (2 high-confidence, 0 moderate-confidence) and 1 ambiguous.")
    assert a["pattern_text"] == ("Pattern: recurring, partly ambiguous. Heard clearly twice in the same direction, in "
                                 "2 sentences and 2 words, supported by 1 ambiguous observation that is not counted as "
                                 "clear. Of the 2 clear differences of /ɛ/ in this reading, 2 went this way.")
    assert a["rate_text"] == "Clear-evidence rate: 2 of 13 occurrences of /ɛ/."
    assert "3 clear" not in " ".join(R.texts(r))                       # the ambiguous one is never called clear
    assert [e["evidence"] for e in a["examples"]] == ["clear", "clear", "ambiguous"]   # clear first, labelled


def test_ambiguous_observations_never_count_as_clear_or_inflate_the_clear_rate():        # 3, 4
    r = fb(reading(H.subs("ɪ", "iː", 3, W), ambs("ɪ", "iː", ["bit", "fill"]), H.ok("sit", "ɪ", 15)))
    [a] = areas(r)
    c = a["counts"]
    assert (c["clear"], c["ambiguous"], c["observations"], c["occurrences"]) == (3, 2, 5, 20)
    assert c["clear_rate"] == 0.15 and c["observed_rate"] == 0.25 and c["rate_band"] == "low"   # 3/20, never 5/20
    assert ": 3 clear (" in a["evidence_text"] and "and 2 ambiguous" in a["evidence_text"]
    assert a["rate_text"].startswith("Clear-evidence rate: 3 of 20 ")


def test_one_clear_plus_many_ambiguous_never_qualifies():                                # 5
    r = fb(reading(H.sub("best", "ɛ", "ɪ"), ambs("ɛ", "ɪ", ["spell", "level", "west", "tell", "bell"]), H.ok("bed", "ɛ", 4)))
    assert areas(r) == [] and r["no_area_text"] == R.NO_AREA_TEXT and r["integrity"]["ok"]
    assert (r["other_differences"]["clear"], r["other_differences"]["ambiguous"]) == (1, 5)


@pytest.mark.parametrize("case", ["one_sentence", "fragments_only", "rate_below"])
def test_two_clear_plus_one_ambiguous_needs_every_other_gate(case):                       # 6
    if case == "one_sentence":
        ins = [H.inp(H.rd(700, session=7), H.sub("best", "ɛ", "ɪ"), H.sub("spell", "ɛ", "ɪ", wi=1),
                     H.amb("level", "ɛ", "ɪ", wi=2))] + \
              [H.inp(H.rd(701 + k, session=7), *H.ok("bed", "ɛ", 2)) for k in range(5)]
    elif case == "fragments_only":   # word fragments from the article text are not distinct words
        ins = reading(H.sub("co", "ɛ", "ɪ"), H.sub("ti", "ɛ", "ɪ"), H.amb("pe", "ɛ", "ɪ"), H.ok("bed", "ɛ", 10))
    else:                             # 2 clear of 23 occurrences: below a clear-evidence rate of 0.10
        ins = reading(H.sub("best", "ɛ", "ɪ"), H.sub("spell", "ɛ", "ɪ"), H.amb("level", "ɛ", "ɪ"), H.ok("bed", "ɛ", 20))
    r = fb(ins)
    assert r["integrity"]["ok"], r["integrity"]
    assert areas(r) == [] and r["no_area_text"] == R.NO_AREA_TEXT


def test_path_c_clarity_is_possible_never_a_confirmed_contrast():
    ins = []
    for k, w in enumerate(["best", "list", "west", "fast", "mist", "cost"]):
        ins.append(H.inp(H.rd(700 + k, session=7), H.omit(w, "t"), H.ok("sit", "ɪ"),
                         reductions=[H.m5("o000", category="possible_omission", natural=False)]))
    r = fb(ins)
    assert r["integrity"]["ok"], r["integrity"]
    [a] = areas(r)
    assert (a["kind"], a["band"], a["path"]) == ("CLARITY", "possible", "C")
    assert "possible" in a["text"] and a["counts"]["clear"] == 0 and a["counts"]["clear_rate"] is None
    assert a["rate_text"] is None and "not counted as clear or ambiguous" in a["evidence_text"]


def test_a_two_way_rate_is_per_sound_so_a_frequent_partner_never_dilutes_it():
    # found on real readings: /eɪ/ ↔ /ɪ/ pooled as 3 of 44 read as rare; per sound it is 2 of 15 /eɪ/
    r = fb(reading(H.subs("eɪ", "ɪ", 2, H.words("based", "make")), H.sub("list", "ɪ", "eɪ"),
                   H.ok("day", "eɪ", 13), H.ok("sit", "ɪ", 28)))
    [a] = areas(r)
    assert a["two_way"] and a["text"].startswith("In this reading, /eɪ/ and /ɪ/ were heard as each other 3 times")
    assert a["rate_text"] == "Clear-evidence rate: 2 of 15 occurrences of /eɪ/ (/ɪ/: 1 of 29)."
    assert a["counts"]["rate_sound"] == "eɪ" and a["counts"]["clear_rate"] == 0.133


# ----------------------------------------------------------------------
# Ordering: lexicographic, traced, never a weighted score
# ----------------------------------------------------------------------

def test_rate_ranking_uses_clear_evidence_only():                                        # 7
    # /ɛ/→/ɪ/: 2 clear + 4 ambiguous of 12 (observed 6/12, clear 2/12 = low);
    # /s/→/ʃ/: 2 clear + 1 ambiguous of 5 (clear 2/5 = high) — clear evidence decides
    r = fb(reading(H.sub("best", "ɛ", "ɪ"), H.sub("spell", "ɛ", "ɪ"), ambs("ɛ", "ɪ", ["level", "west", "tell", "bell"]),
                   H.ok("bed", "ɛ", 6), H.sub("sea", "s", "ʃ"), H.sub("sun", "s", "ʃ"), H.amb("sip", "s", "ʃ"),
                   H.ok("so", "s", 2)))
    assert [a["pairs"] for a in areas(r)] == [["s→ʃ"], ["ɛ→ɪ"]]
    assert areas(r)[0]["ordering"]["placed_above_next_by"] == "clear_rate_band"
    assert areas(r)[1]["counts"]["observed_rate"] == 0.5 and areas(r)[1]["counts"]["rate_band"] == "low"


def test_three_of_five_ranks_above_six_of_fifty_and_five_of_a_hundred_is_out():          # 8
    r = fb(reading(H.subs("s", "ʃ", 3, H.words("sea", "sun", "sip")), H.ok("so", "s", 2),
                   H.subs("ɛ", "ɪ", 6, H.words("best", "spell", "level", "west", "tell", "bell")), H.ok("bed", "ɛ", 44),
                   H.subs("æ", "ʌ", 5, H.words("cat", "bat", "hat", "map", "tap")), H.ok("man", "æ", 95)))
    got = [(a["pairs"], a["counts"]["clear"], a["counts"]["occurrences"]) for a in areas(r)]
    assert got == [(["s→ʃ"], 3, 5), (["ɛ→ɪ"], 6, 50)]         # 3/5 high > 6/50 low, despite fewer observations
    assert areas(r)[0]["ordering"]["placed_above_next_by"] == "clear_rate_band"
    assert r["other_differences"]["clear"] == 5                  # 5/100: below the rate gate, only counted


def test_breadth_orders_areas_with_the_same_evidence_and_rate_band():                    # 9
    r = fb(reading(H.subs("ɛ", "ɪ", 4, H.words("best", "spell", "level", "west")), H.ok("bed", "ɛ", 12),
                   H.subs("s", "ʃ", 3, H.words("sea", "sun", "sip")), H.ok("so", "s", 9)))
    a, b = areas(r)
    assert a["counts"]["rate_band"] == b["counts"]["rate_band"] == "moderate" and a["band"] == b["band"] == "clear"
    assert (a["pairs"], a["counts"]["sentences"], b["counts"]["sentences"]) == (["ɛ→ɪ"], 4, 3)
    assert a["ordering"]["placed_above_next_by"] == "sentences" and b["ordering"]["placed_above_next_by"] is None
    assert [R.order_key(x) for x in areas(r)] == sorted(R.order_key(x) for x in areas(r))


# ----------------------------------------------------------------------
# Strengths: secondary, never competing
# ----------------------------------------------------------------------

@pytest.mark.parametrize("ok, diffs, shown", [
    (10, 0, True), (9, 0, False),            # at least 10 occurrences
    (9, 1, True), (8, 1, False),             # at least 90 % heard as expected (an ambiguous decode counts against)
    (18, 2, False),                          # 18 of 20 is 90 %, but 2 differences
])
def test_strengths_need_the_strength_criteria(ok, diffs, shown):                          # 10
    r = fb(reading(H.ok("think", "θ", ok), ambs("θ", "t", ["three", "thin"][:diffs]), H.ok("not", "n", 30)))
    assert ("θ" in {s["sound"] for s in r["strengths"]}) is shown, r["strengths"]
    assert "n" not in {s["sound"] for s in r["strengths"]}            # /n/ 30 of 30: not a useful dimension
    if shown:
        [s] = r["strengths"]
        assert s["text"] == f"In this reading, /θ/ was heard as expected in {ok} of {ok + diffs} occurrences."
    assert r["integrity"]["ok"], r["integrity"]


def test_strengths_never_consume_improvement_slots():                                     # 11
    strong = [H.ok("think", "θ", 12), H.ok("she", "ʃ", 12), H.ok("seed", "iː", 12), H.ok("good", "ʊ", 12)]
    r = fb(reading(H.subs("ɛ", "ɪ", 4, H.words("best", "spell", "level", "west")), H.ok("bed", "ɛ", 6), *strong))
    assert len(areas(r)) == 1 and len(r["strengths"]) == 3 and r["no_area_text"] is None
    assert not hasattr(R.DEFAULT_READING, "max_areas") and len(r["strengths"]) <= R.DEFAULT_READING.max_strengths


def test_a_sound_in_an_improvement_area_is_never_a_strength_on_either_side():             # 12
    r = fb(reading(H.subs("ɛ", "ɪ", 4, H.words("best", "spread", "level", "spell")), H.ok("sit", "ɪ", 20), H.ok("bed", "ɛ", 10)))
    assert areas(r) and not {"ɪ", "ɛ"} & {s["sound"] for s in r["strengths"]}


def test_no_qualifying_area_says_so_explicitly_and_still_shows_strengths():               # 13
    r = fb(reading(H.sub("sit", "ɪ", "iː"), H.sub("we", "w", "v"), H.ok("think", "θ", 12)))
    assert areas(r) == [] and r["no_area_text"] == "No major pronunciation pattern was strong enough to call out in this reading."
    assert [s["sound"] for s in r["strengths"]] == ["θ"] and r["integrity"]["ok"]


def test_single_observations_stay_out_and_are_only_counted():                             # 20
    r = fb(reading(H.sub("sit", "ɪ", "iː"), H.sub("we", "w", "v"), H.sub("think", "θ", "t"), H.ok("bed", "ɛ", 6)))
    assert areas(r) == [] and r["other_differences"]["count"] == 3
    assert "did not form a pattern strong enough" in r["other_differences"]["note"]


# ----------------------------------------------------------------------
# Scope: this reading only; coaching untouched
# ----------------------------------------------------------------------

def test_different_articles_give_different_areas_while_coaching_can_agree():              # 14, 15
    history = H.scatter([H.subs("ɪ", "iː", 8, W), H.ok("sit", "ɪ", 12)], n_readings=12, sessions=3)
    a = reading(H.subs("ɪ", "iː", 3, W), H.ok("sit", "ɪ", 3), session=5)
    b = reading(H.subs("s", "ʃ", 3, H.words("sea", "sun", "sit")), H.ok("think", "θ", 10), session=6)
    fa, fbb = fb(a), fb(b)
    assert [x["pairs"] for x in areas(fa)] == [["ɪ→iː"]] and [x["pairs"] for x in areas(fbb)] == [["s→ʃ"]]
    ca = run_coaching(history + a, generated_at="t")
    cb = run_coaching(history + b, generated_at="t")
    assert [x["target"]["target_id"] for x in ca["actions"]][:1] == [x["target"]["target_id"] for x in cb["actions"]][:1] \
        == ["contrast:iː~ɪ"]


def test_only_this_reading_is_used():
    mine = reading(H.subs("ɪ", "iː", 3, W), H.ok("sit", "ɪ", 3), session=1)
    other = reading(H.subs("s", "ʃ", 9, H.words("sea", "sun", "sit")), session=2)   # stronger, but another reading
    r = fb(mine)
    assert [a["sounds"] for a in areas(r)] == [["ɪ"]]
    own = {i.reading.attempt_id for i in mine}
    assert {x["attempt_id"] for x in r["provenance"]["inputs"]} == own
    assert all(i.split(":")[1] in own for a in areas(r) for i in a["unit_ids"])
    assert fb(mine) == r and other  # deterministic; the other reading changes nothing


def test_a_first_ever_reading_is_described_while_coaching_abstains():                     # 18
    first = reading(H.subs("ɪ", "iː", 4, W), H.ok("sit", "ɪ", 6))
    assert run_coaching(first, generated_at="t")["no_action"]["code"] in ("history_too_small", "single_session")
    assert areas(fb(first))


def test_the_reading_branch_never_imports_the_coaching_decision_layers():                 # 16
    tree = ast.parse(Path(R.__file__).read_text(encoding="utf-8"))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
        {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    for forbidden in ("pool", "leverage", "selection", "plan", "contract", "engine"):
        assert f"pronunciation_lab.coaching.{forbidden}" not in imported, forbidden


# ----------------------------------------------------------------------
# Exclusions (19): every category stays out of every path
# ----------------------------------------------------------------------

def _excluded_reading(category):
    ws = ["best", "spell", "level", "west", "tell", "bell"]
    ins = []
    for k, w in enumerate(ws):
        if category == "reference_variant":
            obs, red = [H.sub("roses", "ᵻ", "ɪ")], []
        elif category == "function_word_variant":
            obs, red = [H.sub("the", "ə", "ɪ")], []
        elif category == "context_predicted":
            obs, red = [H.sub(w, "t", "d", position="final")], [H.m5("o000")]
        elif category == "merge_suspect":
            obs, red = [H.sub(w, "t", "d", position="final")], [H.m5("o000", natural=False, merge=True)]
        elif category == "neighbour_shift":
            obs, red = [H.sub(w, "ɛ", "ɪ", prev="ɪ")], []
        elif category == "implausible_pair":
            obs, red = [H.sub("leaders", "iː", "t")], []
        elif category == "not_interpreted":
            obs, red = [H.uninterpreted(w, "ɛ")], []
        else:  # extra_sound
            obs, red = [dict(H.sub(w, "ɛ", "ɪ"), type="insertion")], []
        ins.append(H.inp(H.rd(700 + k, session=7), *obs, H.ok("bed", "ɛ", 2), reductions=red))
    return ins


@pytest.mark.parametrize("category", ["reference_variant", "function_word_variant", "context_predicted", "merge_suspect",
                                      "neighbour_shift", "implausible_pair", "not_interpreted", "extra_sound"])
def test_every_excluded_category_stays_excluded(category):
    ins = _excluded_reading(category)
    units = units_of(ins)
    assert sum(1 for u in units.values() if u.exclusion == category) == 6
    r = fb(ins)
    assert r["integrity"]["ok"], r["integrity"]
    assert areas(r) == [] and r["absorbed"] == [] and r["no_area_text"] == R.NO_AREA_TEXT
    assert r["other_differences"]["count"] == 0
    if category == "reference_variant":
        assert any(c["code"] == "reference_accent" for c in r["cautions"])


# ----------------------------------------------------------------------
# Fluency, cautions, insufficient readings
# ----------------------------------------------------------------------

def test_fluency_and_cautions_come_after_areas_and_strengths():
    fl = H.fluency(H.hesitation(), H.hesitation("of", "the"))
    ins = [H.inp(H.rd(700 + k, session=7), *H.ok("think", "θ", 2), fluency=fl) for k in range(5)]
    r = fb(ins, coverage={"sentences": 8, "recorded": 7, "feedback_withheld": 1, "awaiting_decision": 1})
    assert r["fluency"]["text"] == "In this reading, possible hesitation pauses were noticed inside phrases 10 times, in 5 sentences."
    assert r["strengths"][0]["text"] == "In this reading, /θ/ was heard as expected in 10 of 10 occurrences."
    assert {"withheld_sentences", "unconfirmed_identity"} <= {c["code"] for c in r["cautions"]}
    quiet = [H.inp(H.rd(800 + k, session=8), *H.ok("think", "θ", 2), fluency=H.fluency(reliable=True)) for k in range(5)]
    assert "no possible hesitation pauses" in fb(quiet)["fluency_note"]["text"]
    noisy = [H.inp(H.rd(900 + k, session=9, reliable=False), *H.ok("think", "θ", 2), fluency=H.fluency(H.hesitation(), reliable=False))
             for k in range(5)]
    rn = fb(noisy)
    assert rn["fluency"] is None and rn["fluency_note"] is None and any(c["code"] == "level_unmeasurable" for c in rn["cautions"])
    assert rn["integrity"]["ok"] and rn["state"] == "feedback"


def test_a_single_sentence_is_insufficient_for_areas_but_still_described():
    r = fb(reading(H.subs("ɪ", "iː", 4, W), n=1))
    assert r["state"] == "insufficient" and areas(r) == [] and r["no_area_text"] is None and r["strengths"] == []
    assert any(c["code"] == "few_sentences" for c in r["cautions"])
    assert r["other_differences"]["count"] == 4 and r["integrity"]["ok"]


def test_family_and_word_are_named_descriptively_with_knowledge_labelled():
    fam = fb(reading(H.subs("ɛ", "eɪ", 4, H.words("best", "spread", "level")), H.subs("eɪ", "ɪ", 4, H.words("based", "make", "day"))))
    [a] = areas(fam)
    assert a["kind"] == "SET" and a["family"]["origin"] == "knowledge" and a["text"].startswith("In this reading, the sounds")
    lex = fb(reading(H.subs("ɛ", "eɪ", 3, H.words("best")), H.ok("bed", "ɛ", 6), H.ok("level", "ɛ", 6)))
    [w] = areas(lex)
    assert w["kind"] == "LEXICAL" and w["word"] == "best" and w["text"] == "In this reading, ‘best’ was heard differently 3 times in 3 sentences."
    assert w["rate_text"] == "Clear-evidence rate: 3 of 3 occurrences of /ɛ/ in ‘best’."


def test_user_words_are_not_wording_violations():
    r = fb(reading(H.subs("ɛ", "eɪ", 3, H.words("error")), H.ok("bed", "ɛ", 6), H.ok("level", "ɛ", 6)))
    assert r["integrity"]["ok"] and areas(r)[0]["word"] == "error"


def test_a_two_sentence_word_pattern_is_valid_at_reading_scale():
    r = fb(reading(H.subs("ɑː", "ʌ", 2, H.words("technology")), H.ok("car", "ɑː", 2), H.ok("bed", "ɛ", 8)))
    assert r["integrity"]["ok"], r["integrity"]
    assert [a["kind"] for a in areas(r)] == ["LEXICAL"] and areas(r)[0]["counts"]["clear"] == 2


def test_an_invalid_description_is_never_shown(monkeypatch):
    monkeypatch.setattr(R, "validate_reading_feedback", lambda *a, **k: ["synthetic defect"])
    r = fb(reading(H.subs("ɪ", "iː", 4, W), H.ok("sit", "ɪ", 6), H.ok("think", "θ", 12)))
    assert r["state"] == "unavailable" and areas(r) == [] and r["strengths"] == [] and r["fluency"] is None
    assert r["no_area_text"] is None and r["integrity"]["issues"] == ["synthetic defect"]


# ----------------------------------------------------------------------
# The validator (fails closed): recomputes everything from this reading's units
# ----------------------------------------------------------------------

def _clear():
    return reading(H.subs("ɪ", "iː", 4, W), H.ok("sit", "ɪ", 6), H.ok("think", "θ", 12))


def _mixed_two():
    return reading(H.sub("best", "ɛ", "ɪ"), H.sub("spell", "ɛ", "ɪ"), ambs("ɛ", "ɪ", ["level", "west", "tell", "bell"]),
                   H.ok("bed", "ɛ", 6), H.sub("sea", "s", "ʃ"), H.sub("sun", "s", "ʃ"), H.amb("sip", "s", "ʃ"), H.ok("so", "s", 2))


def _as_clear(r):
    c = r["improvement_areas"][0]["counts"]
    c.update(clear=c["clear"] + c["ambiguous"], ambiguous=0)


def _observed_rate_text(r):
    a = r["improvement_areas"][1]
    c = a["counts"]
    a["rate_text"] = f"Clear-evidence rate: {c['observations']} of {c['occurrences']} occurrences of /ɛ/."


@pytest.mark.parametrize("fixture, corrupt, message", [
    (_clear, lambda r: r["improvement_areas"][0].update(text="You consistently confuse /ɪ/ and /iː/."), "not descriptive"),
    (_clear, lambda r: r["improvement_areas"][0].update(text="In this reading, you should practise /ɪ/."), "not descriptive"),
    (_clear, lambda r: r["improvement_areas"][0].update(text="In this reading, this has improved."), "not descriptive"),
    (_clear, lambda r: r["improvement_areas"][0].update(text="In this reading, /ɪ/ was mispronounced 4 times."), "not descriptive"),
    (_clear, lambda r: r["improvement_areas"][0].update(rate_text="Clear-evidence rate: 40% of /ɪ/."), "not descriptive"),
    (_clear, lambda r: r["improvement_areas"][0].update(text="/ɪ/ was heard as /iː/ 4 times in 4 sentences."), "not scoped to this reading"),
    (_clear, lambda r: r["improvement_areas"][0].update(hypothesis="x"), "coaching hypothesis"),
    (_clear, lambda r: r.update(actions=[]), "no 'actions'"),
    (_clear, lambda r: r["improvement_areas"].append(copy.deepcopy(r["improvement_areas"][0])), "described twice"),
    (_clear, lambda r: r["improvement_areas"][0]["unit_ids"].append("elsewhere:a:j:o1"), "outside this reading"),
    (_clear, lambda r: r["improvement_areas"][0]["counts"].update(clear=99), "counts do not match"),
    (_clear, lambda r: r["improvement_areas"][0]["examples"][0].update(play_ms=None), "exact playback"),
    (_clear, lambda r: r["strengths"][0].update(sound="ɪ"), "involved in an improvement area"),
    (_clear, lambda r: r["strengths"][0]["counts"].update(occurrences=40), "counts do not match"),
    (_clear, lambda r: r["strengths"].append(dict(r["strengths"][0], sound="n", text="In this reading, /n/ was heard as expected.")),
     "not a diagnostically useful dimension"),
    (_mixed_two, _as_clear, "counts do not match"),
    (_mixed_two, lambda r: r["improvement_areas"][0].update(evidence_text="3 clear differences."), "disclose its evidence composition"),
    (_mixed_two, lambda r: r["improvement_areas"][0].update(pattern_scope="word"), "state its pattern scope"),
    (_mixed_two, lambda r: r["improvement_areas"][0].update(pattern_text="Recurring."), "state its pattern scope"),
    (_mixed_two, lambda r: r["improvement_areas"][1]["counts"].update(direction_share=0.2), "counts do not match"),
    (_mixed_two, _observed_rate_text, "not the clear-evidence rate"),
    (_mixed_two, lambda r: r["improvement_areas"].reverse(), "deterministic order"),
    (_mixed_two, lambda r: r["improvement_areas"][0].update(band="clear"), "does not meet path A"),
    (_mixed_two, lambda r: r.update(improvement_areas=[], no_area_text=None), "said explicitly"),
])
def test_validator_keeps_it_descriptive_scoped_and_traceable(fixture, corrupt, message):
    ins = fixture()
    r = fb(ins)
    assert r["integrity"]["ok"], r["integrity"]
    corrupt(r)
    issues = R.validate_reading_feedback(r, units_of(ins), {i.reading.reading_id for i in ins}, DEFAULT_READING)
    assert any(message in x for x in issues), issues


def test_validator_rejects_an_observation_that_belongs_to_another_reading():
    mine = reading(H.subs("ɪ", "iː", 4, W), H.ok("sit", "ɪ", 6), session=1)
    other = reading(H.subs("ɪ", "iː", 4, W), session=2)
    r = fb(mine)
    all_units = units_of(mine + other)
    mine_ids = {i.reading.reading_id for i in mine}
    foreign = next(i for i, u in all_units.items() if u.reading_id not in mine_ids and u.quality == "confident")
    r["improvement_areas"][0]["unit_ids"].append(foreign)
    issues = R.validate_reading_feedback(r, all_units, mine_ids, DEFAULT_READING)
    assert any("outside this reading" in x for x in issues), issues


@pytest.mark.parametrize("ok, diffs, shown", [(18, 2, True), (8, 2, False)])
def test_the_strength_share_applies_on_its_own(ok, diffs, shown):
    # with the default limit of 1 difference the share (≥ 90 %) is implied; checked independently here
    import dataclasses
    rcal = dataclasses.replace(DEFAULT_READING, strength_max_differences=5)
    r = fb(reading(H.ok("think", "θ", ok), ambs("θ", "t", ["three", "thin"][:diffs])), rcal=rcal)
    assert ("θ" in {s["sound"] for s in r["strengths"]}) is shown and r["integrity"]["ok"], r


def test_validator_rejects_an_excluded_observation_in_an_area():
    ins = reading(H.subs("ɪ", "iː", 4, W), H.ok("sit", "ɪ", 6), [H.sub("roses", "ᵻ", "ɪ") for _ in range(2)])
    r = fb(ins)
    units = units_of(ins)
    excluded = next(i for i, u in units.items() if u.exclusion == "reference_variant")
    r["improvement_areas"][0]["unit_ids"].append(excluded)
    issues = R.validate_reading_feedback(r, units, {i.reading.reading_id for i in ins}, DEFAULT_READING)
    assert any("excluded observation" in x for x in issues), issues


# ----------------------------------------------------------------------
# Final revision: observation vs pattern evidence, no fixed limit, Pareto order, lateral overlaps
# ----------------------------------------------------------------------

FIVE = [("s", "ʃ", ["sea", "sun", "sip"], "so"), ("w", "v", ["we", "way", "west"], "win"),
        ("θ", "t", ["think", "three", "thin"], "thumb"), ("ŋ", "n", ["sing", "long", "ring"], "king"),
        ("l", "ɹ", ["light", "lot", "lip"], "low"), ("m", "n", ["map", "mud", "mix"], "mop")]


def _qualifying(specs):
    obs = []
    for e, h, ws, okw in specs:
        obs += [H.sub(w, e, h) for w in ws] + H.ok(okw, e, 2)
    return reading(*obs)


def test_zero_areas_when_many_differences_form_no_pattern():                                # A
    singles = [H.sub(w, e, h) for w, e, h in [("sit", "ɪ", "iː"), ("we", "w", "v"), ("think", "θ", "t"), ("bed", "ɛ", "æ"),
                                                ("cat", "æ", "ʌ"), ("sun", "s", "ʃ"), ("ring", "ŋ", "n"), ("fan", "f", "p")]]
    ambiguous = [H.amb(w, "ɛ", "ɪ") for w in ["best", "spell", "level", "west", "tell", "bell"]]
    r = fb(reading(*singles, *ambiguous, H.ok("bed", "ɛ", 6)))
    assert r["integrity"]["ok"] and areas(r) == [] and r["no_area_text"] == R.NO_AREA_TEXT
    assert (r["other_differences"]["clear"], r["other_differences"]["ambiguous"]) == (8, 6)


def test_exactly_one_qualifying_pattern():                                                    # B
    r = fb(_qualifying(FIVE[:1]))
    assert [a["pairs"] for a in areas(r)] == [["s→ʃ"]] and r["no_area_text"] is None


def test_exactly_three_qualifying_patterns():                                                 # C
    r = fb(_qualifying(FIVE[:3]))
    assert len(areas(r)) == 3 and r["integrity"]["ok"]


def test_more_than_three_qualifying_patterns_are_all_returned_in_a_fixed_order():            # D, N
    r = fb(_qualifying(FIVE))
    assert len(areas(r)) == 6 and r["integrity"]["ok"], r["integrity"]               # none silently truncated
    assert sorted(a["pairs"][0] for a in areas(r)) == sorted(f"{e}→{h}" for e, h, _, _ in FIVE)
    keys = [R.order_key(a) for a in areas(r)]
    assert keys == sorted(keys) and [a["ordering"]["position"] for a in areas(r)] == [1, 2, 3, 4, 5, 6]
    assert fb(_qualifying(FIVE)) == r                                                 # deterministic
    assert [a["id"] for a in fb(_qualifying(list(reversed(FIVE))))["improvement_areas"]] == [a["id"] for a in areas(r)]
    for a in areas(r):                                                                # no hidden score anywhere
        assert not {"score", "priority", "weight"} & set(a) and set(a["ordering"]["key"]) == set(R.ORDER_CRITERIA)


def test_a_single_high_confidence_observation_is_not_a_recurring_pattern():                   # E
    r = fb(reading(H.sub("vision", "ʒ", "z"), H.ok("bed", "ɛ", 6)))     # 1 of 1, high confidence, one word, one sentence
    assert areas(r) == [] and r["other_differences"]["clear"] == 1 and r["no_area_text"] == R.NO_AREA_TEXT


def test_repeated_moderate_confidence_observations_can_form_a_pattern():                      # F
    obs = [H.sub(w, "ɛ", "ɪ", conf="moderate") for w in ["best", "spell", "level", "west", "tell"]] + H.ok("bed", "ɛ", 3)
    r = fb(reading(*obs, n=4))                                                       # 5 of 8, 4 sentences, 5 words
    [a] = areas(r)
    c = a["counts"]
    assert (c["clear"], c["clear_high"], c["clear_moderate"], c["occurrences"], c["sentences"]) == (5, 0, 5, 8, 4)
    assert a["band"] == "clear" and a["pattern_scope"] == "sound"
    assert "5 clear (0 high-confidence, 5 moderate-confidence)" in a["evidence_text"]   # individually moderate …
    assert a["pattern_text"].startswith("Pattern: recurring. Heard clearly 5 times in the same direction")   # … recurring


def test_repeated_ambiguous_observations_never_become_a_clear_pattern():                      # G, L
    r = fb(reading([H.amb(w, "ɛ", "ɪ") for w in ["best", "spell", "level", "west", "tell"]], H.ok("bed", "ɛ", 3)))
    assert areas(r) == [] and (r["other_differences"]["clear"], r["other_differences"]["ambiguous"]) == (0, 5)


def test_a_narrow_word_pattern_qualifies_and_is_marked_word_specific():                       # H
    r = fb(reading(H.subs("ɑː", "oʊ", 2, H.words("anthropic")), H.ok("car", "ɑː", 6), H.ok("bed", "ɛ", 6)))
    [a] = areas(r)
    assert (a["kind"], a["pattern_scope"], a["pattern_label"]) == ("LEXICAL", "word", "Word-specific")
    assert a["pattern_text"].startswith("Pattern: word-specific. Strong for ‘anthropic’ (2 clear in 2 sentences)")
    assert "says little about /ɑː/ in other words" in a["pattern_text"]


def test_a_broad_pattern_is_recognised_as_broader_than_a_word_pattern():                      # I, N
    r = fb(reading(H.subs("s", "ʃ", 3, H.words("sea", "sun", "sip")), H.ok("so", "s", 14),                  # 3 of 17
                   H.subs("ɑː", "oʊ", 2, H.words("anthropic")), H.ok("car", "ɑː", 6), H.ok("bed", "ɛ", 6)))  # 2 of 2
    broad, word = areas(r)
    assert (broad["pattern_scope"], word["pattern_scope"]) == ("sound", "word")
    assert broad["counts"]["rate_band"] == "low" and word["counts"]["rate_band"] == "high"
    assert broad["ordering"]["placed_above_next_by"] == "pattern_scope"                # a within-word 2 of 2 is not
    assert broad["order_text"] == "Placed before area 2 for a broader pattern."         # compared with a sound-wide rate


def test_the_same_direction_strengthens_a_pattern_unrelated_directions_do_not():              # J
    same = fb(reading(H.sub("best", "ɛ", "ɪ"), H.sub("spell", "ɛ", "ɪ"), H.amb("level", "ɛ", "ɪ"), H.ok("bed", "ɛ", 12)))
    assert [a["pairs"] for a in areas(same)] == [["ɛ→ɪ"]]
    assert areas(same)[0]["counts"]["direction_share"] == 1.0
    scattered = fb(scattered_inputs())
    assert areas(scattered) == [], [(a["id"], a["counts"]["direction_share"]) for a in areas(scattered)]
    assert scattered["other_differences"]["clear"] == 6          # each pair: 2 of /ɛ/'s 6 clear differences


def scattered_inputs():
    return reading(H.sub("best", "ɛ", "ɪ"), H.sub("spell", "ɛ", "ɪ"), H.amb("level", "ɛ", "ɪ"),
                   H.sub("west", "ɛ", "æ"), H.sub("tell", "ɛ", "æ"), H.amb("bell", "ɛ", "æ"),
                   H.sub("send", "ɛ", "ə"), H.sub("lend", "ɛ", "ə"), H.amb("mend", "ɛ", "ə"), H.ok("bed", "ɛ", 6))


def test_direction_is_the_deciding_gate_for_scattered_substitutions():
    ins = scattered_inputs()
    units = [x for y in ins for x in normalise(y)[0]]
    index = build_index(units, [])
    t = R.evaluate(R._contrast(index, [("ɛ", "ɪ")]), index, DEFAULT_READING.formation(), 0)
    import dataclasses
    loose = dataclasses.replace(DEFAULT_READING, c_min=0.0)
    area = R._path_b_area(t, index, units, loose)                # every other gate passes …
    assert area is not None and area["counts"]["direction_share"] == round(2 / 6, 3)
    assert R._path_b_area(t, index, units, DEFAULT_READING) is None   # … direction alone rejects it


def test_counterexamples_are_kept_and_never_read_as_always():                                 # K
    r = fb(reading(H.subs("ɪ", "iː", 4, W), H.ok("sit", "ɪ", 6)))
    [a] = areas(r)
    assert a["counts"]["heard_as_expected"] == 6 and a["counter_text"] == "/ɪ/ was heard as expected 6 times in this reading."
    assert a["counter_examples"] and "always" not in " ".join(R.texts(r)).lower()


def test_a_broader_pattern_is_not_hidden_by_its_own_word_subset():
    # found on a real reading: /ɑː/→/oʊ/ (models, anthropic ×2, 3 clear) was dropped because the narrower
    # 'anthropic' candidate took two of its observations first; the consolidation had kept it a sound pattern
    r = fb(reading(H.subs("ɑː", "oʊ", 2, H.words("anthropic")), H.sub("models", "ɑː", "oʊ"),
                   H.amb("products", "ɑː", "oʊ"), H.ok("car", "ɑː", 6), H.ok("bed", "ɛ", 6)))
    [a] = areas(r)
    assert (a["kind"], a["pattern_scope"], a["counts"]["clear"]) == ("CONTRAST", "sound", 3)
    assert "Most often in ‘anthropic’ (twice)" in a["pattern_text"]
    [x] = r["absorbed"]
    assert x["id"] == "lexical:anthropic:ɑː" and x["absorbed_into"] == [a["id"]] and x["remaining"] == "none"


def test_what_remains_of_an_overlapping_candidate_is_re_gated_not_dropped():
    # a stronger candidate describes one observation of a 5-clear pattern; the remaining 4 still qualify on their
    # own and stay an area (re-gated like the recent-history selection), never dropped unseen
    ins = reading(H.subs("s", "ʃ", 5, H.words("sea", "sun", "sip", "set", "sat")), H.ok("so", "s", 4))
    units = [u for i in ins for u in normalise(i)[0]]
    index = build_index(units, [])
    [cand] = R._candidates(index, units, DEFAULT_READING, len(ins))
    stronger = {"id": "0-stronger", "kind": "CONTRAST", "band": "clear", "unit_ids": cand["unit_ids"][:1],
                "counts": {"rate_band": "high", "sentences": 99, "real_words": 99, "clear": 99}}
    accepted, absorbed = R._membership([cand, stronger], index, units, DEFAULT_READING, len(ins))
    assert [a["id"] for a in accepted] == ["0-stronger", cand["id"]]
    assert accepted[1]["counts"]["clear"] == 4 and accepted[1]["counts"]["direction_share"] == 1.0
    assert absorbed == [{"id": cand["id"], "kind": "CONTRAST", "band": "clear", "sounds": ["s"], "pairs": ["s→ʃ"],
                         "word": None, "shared_observations": 1, "absorbed_into": ["0-stronger"],
                         "remaining": "kept as its own area"}]
    # … and when what remains no longer qualifies, it is recorded, and its observations stay counted
    weaker = {**stronger, "unit_ids": cand["unit_ids"][:3]}
    accepted, absorbed = R._membership([cand, weaker], index, units, DEFAULT_READING, len(ins))
    assert [a["id"] for a in accepted] == ["0-stronger"] and absorbed[0]["remaining"] == "did not qualify on its own"
