"""M9 adapter: reader store → eligible readings (eligibility, provenance, references, agreement)."""

from collections import Counter

import m9helpers as H
from m9helpers import ENGINE, StoreBuilder, numbered

from pronunciation_lab.coaching import run_coaching
from pronunciation_lab.coaching.evidence import text_key
from pronunciation_lab.reader.coaching_source import load_inputs


def test_only_eligible_readings_are_loaded_and_exclusions_are_counted(tmp_path):
    b = StoreBuilder(tmp_path)
    s = b.session()
    obs = numbered(H.sub("change", "eɪ", "ɛ"))
    good = b.attempt(s, obs)
    b.attempt(s, obs, withheld=True)                       # M7 withheld: never coached
    b.attempt(s, obs, identity="AMBIGUOUS")                # uncertain identity, not kept
    kept = b.attempt(s, obs, identity="AMBIGUOUS", disposition="kept")
    b.attempt(s, obs, identity="MISMATCH")
    b.attempt(s, obs, disposition="rerecord_requested")
    b.attempt(s, obs, disposition="discarded")
    b.attempt(s, obs, job_engine="openpronounce")          # analysed by another engine than the session's
    b.attempt(s, obs, state="ANALYSIS_FAILED")
    inputs, excl = load_inputs(b.store)
    assert {i.reading.attempt_id for i in inputs} == {good, kept}
    assert excl == Counter({"not_eligible": 6, "other_engine": 1})


def test_readings_keep_provenance_and_the_sentence_reference(tmp_path):
    b = StoreBuilder(tmp_path)
    s = b.session()
    a = b.attempt(s, numbered(H.sub("change", "eɪ", "ɛ")), analysis="low_confidence")
    pre_m7 = b.attempt(s, numbered(H.ok("plan", "æ")), boundary=False)
    inputs, _ = load_inputs(b.store)
    r = next(i.reading for i in inputs if i.reading.attempt_id == a)
    assert r.reading_id == f"{s}:{a}" and r.engine == ENGINE and r.analysis_state == "low_confidence"
    assert r.sentence_key == text_key("then change the plan") and r.article_key == b.article["text_sha256"]
    assert r.sentence_ref["play_ms"] == [0.0, 2400.0] and r.sentence_ref["kind"] == "target"   # M7 sentence only
    r2 = next(i.reading for i in inputs if i.reading.attempt_id == pre_m7)
    assert r2.sentence_ref["play_ms"] == [0.0, 3000.0]                                          # whole attempt
    assert "best" not in next(i for i in inputs).fragment_words


def test_engine_agreement_comes_from_the_comparison_job(tmp_path):
    b = StoreBuilder(tmp_path)
    s = b.session()
    primary = numbered(H.sub("change", "eɪ", "ɛ"), H.ok("plan", "æ"))
    other = [dict(primary[0], type="expected", competitor=None), dict(primary[1])]
    b.attempt(s, primary, comparison=other)
    [inp], _ = load_inputs(b.store)
    assert inp.engine_agreement == {"o000": "differ", "o001": "agree"} and inp.reading.has_comparison


def test_store_to_action_end_to_end_keeps_exact_references(tmp_path):
    b = StoreBuilder(tmp_path)
    sessions = [b.session() for _ in range(3)]
    for k in range(12):
        w = ["sit", "list", "fill", "bit"][k % 4]
        b.attempt(sessions[k % 3], numbered(H.sub(w, "ɪ", "iː") if k < 6 else H.ok("bit", "ɪ"), H.ok("sit", "ɪ", 2)),
                  sentence=f"Sentence {k} with {w}.")
    inputs, excl = load_inputs(b.store)
    r = run_coaching(inputs, prior_exclusions=excl)
    assert r["state"] == "actions" and r["integrity"]["ok"], r["integrity"]
    [a] = r["actions"]
    for e in a["practice"]["examples"]:
        att = b.store.load_attempt(e["session_id"], e["attempt_id"])            # resolves to a stored attempt
        assert e["url"] == f"/api/sessions/{att['session_id']}/attempts/{att['id']}/audio"
        assert e["job_id"] in att["job_ids"] and e["timeline"] == "analysis_wav"
    for s in a["practice"]["retest_sentences"]:
        assert s["ref"]["play_ms"] == [0.0, 2400.0] and s["text"].startswith("Sentence")
