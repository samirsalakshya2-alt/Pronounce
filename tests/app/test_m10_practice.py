"""M10 practice records, practice → fresh-word outcomes, the reverse-direction guard, and adaptive decisions.

All histories are SYNTHETIC (m10helpers). Baseline in every test: /ɛ/ heard as /ɪ/ in 6 of 30 chances (rate 0.2)
across 3 texts — a likely personal pattern.
"""

import json

import m9helpers as H
from m10helpers import History, W, action, clear, ok, pattern, personal_baseline

from pronunciation_lab.longitudinal import practice as P
from pronunciation_lab.longitudinal.store import ProgressStore

PW = W("prac", 4)                                   # the practised words
PRACTICE_SENTENCES = [" ".join(PW) + " one.", " ".join(PW) + " two.", " ".join(PW) + " three."]


def practise(h, readings=None, **kw):
    readings = readings if readings is not None else [ok("ɛ", PW) for _ in PRACTICE_SENTENCES]
    return h.practice(action(["ɛ→ɪ"]), PRACTICE_SENTENCES, readings, **kw)


def later(h, texts, clear_per=0, ok_per=12, prefix="post", e="ɛ", hd="ɪ", extra=lambda t: []):
    for t in range(texts):
        p = f"{prefix}{t}"
        h.read(f"text-{p}", clear(e, hd, W(p + "c", clear_per)) + ok(e, W(p, ok_per)) + extra(t))


def outcome_of(r):
    it = pattern(r, "sub:ɛ>ɪ")
    return it, it["outcomes"][-1] if it["outcomes"] else None


# ----------------------------------------------------------------------
# H. Practice records
# ----------------------------------------------------------------------

def test_an_explicit_practice_record_is_written_once_and_linked_to_its_advice(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    rec = practise(h)
    assert rec["target"]["patterns"] == ["sub:ɛ>ɪ"] and rec["target"]["m9_target_id"] == "contrast:ɛ~ɪ"
    assert rec["reverse_patterns"] == ["sub:ɪ>ɛ"] and rec["mode"] is None
    assert set(PW) <= set(rec["material"]["words"]) and len(rec["material"]["sentences"]) == 3
    assert rec["practice_type"] == "retest_sentences:listen_compare_fallback"
    ps = ProgressStore(h.store.root)
    try:
        ps.save_practice(rec)
        raise AssertionError("a practice record was overwritten")
    except FileExistsError:
        pass
    r = h.progress()
    [p] = r["practice"]
    assert p["status"]["completion"] == "completed" and p["status"]["duration"] is None   # never invented
    assert r["history"]["classes"]["PRACTICE"] == 3                                      # practice is not fresh evidence


def test_displayed_advice_is_not_practice(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    sid = h.store.session_ids()[0]
    h.store.save_coaching(sid, {"generated_at": "2026-11-01T01:00:00+00:00", "pool": {"engine": "wav2vec2_raw"},
                                "actions": [action(["ɛ→ɪ"]) | {"practice": {"retest_sentences": [{"text": "x"}]}}]})
    r = h.progress()
    it = pattern(r, "sub:ɛ>ɪ")
    assert it["advice"]["times_advised"] == 1 and it["practice"] == [] and r["practice"] == []


def test_practice_not_started_partial_and_legacy_sessions(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    not_started = practise(h, read=False, name="ns")
    partial = h.practice(action(["ɛ→ɪ"]), PRACTICE_SENTENCES, [ok("ɛ", PW)], name="partial")
    h.read("legacy-practice", ok("ɛ", PW), source="What to practise now")       # before practice records existed
    r = h.progress()
    st = {p["record"]["id"]: p["status"]["completion"] for p in r["practice"]}
    assert st == {not_started["id"]: "not_started", partial["id"]: "partial"}
    o = next(o for o in pattern(r, "sub:ɛ>ɪ")["outcomes"] if o["practice_id"] == not_started["id"])
    assert o["outcome"] == "INSUFFICIENT_OUTCOME" and o["reason"] == "practice was not started"
    [legacy] = r["legacy_practice_sessions"]
    assert legacy["linked"] is False and "never used for practice outcomes" in legacy["note"]


def test_practice_sentences_read_again_later_are_retests_not_fresh_evidence(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    practise(h)
    art = h.texts[next(n for n in h.texts if n.startswith("practice"))]
    sid = h.session(art)                                        # an ordinary session re-reading practice material
    h.attempt(sid, art, 0, ok("ɛ", PW))
    r = h.progress()
    assert r["history"]["classes"].get("RETEST", 0) + r["history"]["classes"].get("PRACTICE", 0) == 4
    assert r["history"]["classes"]["FRESH"] == 3


# ----------------------------------------------------------------------
# I. Practice → fresh-word outcome, transfer and the reverse-direction guard
# ----------------------------------------------------------------------

def test_transfer_in_unpractised_words(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    practise(h)
    later(h, 2)                                                 # 24 chances in new words, about 4.8 predicted, 0 seen
    it, o = outcome_of(h.progress())
    assert o["outcome"] == "TRANSFER" and o["unpractised"] == {"opportunities": 24, "clear": 0, "sessions": 2, "expected": 4.8}
    assert it["decision"]["decision"] == "REDUCE_PRIORITY"


def test_no_transfer_when_only_the_practised_material_improved(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    practise(h)                                                 # 12 practised chances, about 2.4 predicted, 0 seen
    later(h, 2, clear_per=2, ok_per=8)                          # new words: still at the baseline rate
    it, o = outcome_of(h.progress())
    assert o["outcome"] == "NO_TRANSFER" and o["during_practice"]["clear"] == 0
    assert it["decision"]["decision"] == "MOVE_TO_FRESH_WORDS"
    assert it["decision"]["recommendation"].startswith("Practise /ɛ/ (heard as /ɪ/) in new words")


def test_a_good_retest_alone_never_establishes_transfer(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    practise(h)                                                 # perfect during practice …
    it, o = outcome_of(h.progress())                            # … but no later ordinary reading yet
    assert o["outcome"] == "INSUFFICIENT_OUTCOME" and it["state"] == "PERSONAL_RECURRING"


def test_insufficient_fresh_word_evidence(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    practise(h)
    later(h, 1)                                                 # one later session only
    _, o = outcome_of(h.progress())
    assert o["outcome"] == "INSUFFICIENT_OUTCOME" and "not enough chances" in o["reason"]


def test_reverse_direction_after_practice_is_an_overcorrection_guard(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    practise(h)
    later(h, 2, extra=lambda t: clear("ɪ", "ɛ", W(f"rev{t}", 2)) + ok("ɪ", W(f"revok{t}", 6)))
    it, o = outcome_of(h.progress())
    assert o["outcome"] == "REVERSE_DIRECTION" and o["reverse"]["rose"] and o["reverse"]["post_clear"] == 4
    assert it["decision"]["decision"] == "CHANGE_PRACTICE_METHOD" and "both directions" in it["decision"]["recommendation"]


def test_one_reverse_occurrence_is_not_an_overcorrection(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    practise(h)
    later(h, 2, extra=lambda t: clear("ɪ", "ɛ", W(f"rev{t}", 1 if t == 0 else 0)) + ok("ɪ", W(f"revok{t}", 6)))
    _, o = outcome_of(h.progress())
    assert o["outcome"] == "TRANSFER" and not o["reverse"]["rose"]


def test_continuing_then_a_method_change_after_repeated_practice(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    bad_practice = [clear("ɛ", "ɪ", PW[:1]) + ok("ɛ", PW[1:]) for _ in PRACTICE_SENTENCES]   # not better in practice
    practise(h, bad_practice, name="round1")
    later(h, 2, clear_per=2, ok_per=8, prefix="a")
    it, o = outcome_of(h.progress())
    assert o["outcome"] == "CONTINUING" and it["decision"]["decision"] == "CONTINUE_CURRENT_TARGET"
    practise(h, bad_practice, name="round2")
    later(h, 2, clear_per=2, ok_per=8, prefix="c")
    it, o = outcome_of(h.progress())
    assert [x["outcome"] for x in it["outcomes"]] == ["CONTINUING", "CONTINUING"]
    assert it["decision"]["decision"] == "CHANGE_PRACTICE_METHOD"


def test_transfer_in_the_practised_context_but_not_outside_it(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    h.practice(action(["ɛ→ɪ"], kind="CONDITIONED", condition="medial"), PRACTICE_SENTENCES,
               [ok("ɛ", PW) for _ in PRACTICE_SENTENCES])
    for t in range(3):
        p = f"ctx{t}"
        h.read(f"text-{p}", ok("ɛ", W(p + "m", 30)) + clear("ɛ", "ɪ", W(p + "f", 3), position="final")
               + ok("ɛ", W(p + "fo", 4), position="final"))
    it, o = outcome_of(h.progress())
    assert o["outcome"] == "TRANSFER" and o["context_split"]["continuing_outside"]
    assert it["decision"]["decision"] == "MOVE_TO_NEW_CONTEXT"


def test_outcome_wording_never_claims_causality(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    practise(h)
    later(h, 2)
    r = h.progress()
    text = json.dumps(r, ensure_ascii=False).lower()
    for banned in ("caused", "practice fixed", "fixed forever", "mastered", "% better"):
        assert banned not in text
    assert r["integrity"]["ok"]


# ----------------------------------------------------------------------
# J. Adaptive decisions: every pattern gets one, with its evidence chain; no top-N
# ----------------------------------------------------------------------

def test_every_tracked_pattern_gets_an_evidence_traceable_decision_without_a_limit(tmp_path):
    h = History(tmp_path)
    pairs = [("ɛ", "ɪ"), ("s", "z"), ("w", "v"), ("θ", "t"), ("ŋ", "n")]
    for t in range(3):
        obs = []
        for e, hd in pairs:
            obs += clear(e, hd, W(f"{t}{e}{hd}".encode("ascii", "ignore").decode() or f"x{t}", 2)) + ok(e, W(f"o{t}{len(obs)}", 6))
        h.read(f"multi{t}", obs)
    r = h.progress()
    active = [it for it in r["patterns"] if it["decision"]["active"]]
    assert len(active) == 5 and r["next_practice"] == [it["pattern"] for it in active]     # all five, none truncated
    for it in active:
        assert it["examples"] and all(e["url"].endswith("/audio") and e["play_ms"] for e in it["examples"])
        assert it["totals"]["clear"] == 6 and it["series"] and it["decision"]["recommendation"]


def test_decisions_map_states(tmp_path):
    from pronunciation_lab.longitudinal.decisions import decide
    ps = {"state": "PERSONAL_RECURRING", "scope": "PERSONAL_RECURRING", "totals": {"clear_articles": 3}, "clear_words": {"a": 1}}
    ctx = {"concentrated": []}
    assert decide("sub:ɛ>ɪ", ps, ctx, [])["decision"] == "CONTINUE_CURRENT_TARGET"
    for state, want in (("STABLE", "RETIRE"), ("RETIRED", "WATCH_FOR_REGRESSION"), ("REGRESSED", "CONTINUE_CURRENT_TARGET"),
                        ("IMPROVING", "REDUCE_PRIORITY"), ("EMERGING", "INSUFFICIENT_HISTORY"),
                        ("INSUFFICIENT_HISTORY", "INSUFFICIENT_HISTORY")):
        assert decide("sub:ɛ>ɪ", dict(ps, state=state, scope="INSUFFICIENT_HISTORY"), ctx, [])["decision"] == want, state
    assert decide("sub:ɛ>ɪ", dict(ps, state="INSUFFICIENT_HISTORY", scope="ARTICLE_BOUND"), ctx, [])["decision"] == \
        "INSUFFICIENT_HISTORY"
    assert "INCREASE_CONTEXT_DIFFICULTY" in __import__("pronunciation_lab.longitudinal.decisions", fromlist=["x"]).DECISIONS


def test_material_words_come_from_the_practice_text():
    assert P.material_words(["The best spell, again!"]) == ["again", "best", "spell", "the"]


def test_practice_and_fluency_patterns_are_not_judged_for_transfer(tmp_path):
    h = History(tmp_path)
    for t in range(3):
        h.read(f"f{t}", ok("ɛ", W(f"f{t}", 3)), fluency=H.fluency(H.hesitation()))
    h.practice(action([], kind="FLUENCY", target_id="fluency:hesitation", group="hesitation"), ["a b c."], [ok("ɛ", W("pp", 2))])
    it = pattern(h.progress(), "fluency:hesitation")
    assert it["outcomes"] == [] and it["state"] == "PERSONAL_RECURRING"


def test_practised_words_in_later_readings_are_kept_apart_from_new_words(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    practise(h)
    later(h, 2, extra=lambda t: clear("ɛ", "ɪ", PW[:2]))     # the practised words still differ; new words do not
    _, o = outcome_of(h.progress())
    assert o["outcome"] == "TRANSFER" and o["practised"]["clear"] == 4 and o["unpractised"]["clear"] == 0


def test_one_later_session_is_never_enough_for_an_outcome(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    practise(h)
    later(h, 1, ok_per=40)                                    # plenty of chances, but one session
    _, o = outcome_of(h.progress())
    assert o["outcome"] == "INSUFFICIENT_OUTCOME" and o["unpractised"]["expected"] >= 3


def test_a_word_habit_from_one_text_is_not_an_active_target(tmp_path):
    h = History(tmp_path)
    for s in range(3):
        h.read_at("same-text", 6, {s: clear("ɑː", "oʊ", ["anthropic"]) + ok("ɑː", W(f"st{s}", 5))})
    it = pattern(h.progress(), "sub:ɑː>oʊ")
    assert it["scope"] == "WORD_SPECIFIC" and not it["decision"]["active"]
    assert it["text"] == "Appears limited to ‘anthropic’ so far."
