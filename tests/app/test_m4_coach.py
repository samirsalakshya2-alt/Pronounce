"""M4 Phoneme Coach — unit, negative and invariant tests on constructed evidence."""

import copy
import math

import pytest

from pronunciation_lab.app import coach as C
from pronunciation_lab.app import phoneme_coach as PC
from pronunciation_lab.app import practice as PR
from pronunciation_lab.benchmark.schema import (
    AcousticEvidence,
    EngineInfo,
    ExpectedPhoneme,
    NBestCandidate,
    ObservedPhoneme,
    PhonemeResult,
    ProcessingInfo,
    PronunciationResult,
    ProsodyEvidence,
    RecordingAudio,
    RecordingInfo,
    TargetInfo,
    TimingInfo,
    WordResult,
)

DURATION = 5000.0


def ph(expected, observed=None, op=None, ep=0.9, nbest=None, start=0.0, end=20.0, source="engine",
       extras=None, stress=None, acoustic=None):
    op = op or ("match" if observed == expected else "omission" if observed is None else "substitution")
    if nbest is None:
        nbest = [(observed or "z", 0.9), ("q", 0.02)] if observed != expected else [(expected, 0.9), ("q", 0.02)]
    return PhonemeResult(
        position=0,
        expected=ExpectedPhoneme(phoneme=expected, stress=stress, source="espeak"),
        observed=ObservedPhoneme(top=observed, confidence=nbest[0][1] if observed else None,
                                 nbest=[NBestCandidate(phoneme=a, probability=b) for a, b in nbest],
                                 frame_start=int(start // 20), frame_end=int(end // 20)),
        timing=TimingInfo(start_ms=start, end_ms=end, duration_ms=(end - start) if None not in (start, end) else None,
                          source=source),
        acoustic=acoustic or AcousticEvidence(measured_on="analysis", energy_db=-20.0, relative_energy=1.2,
                                              voicing=True, f0_hz=120.0,
                                              spectral_features={"zero_crossing_rate": 0.1, "spectral_centroid_hz": 900.0}),
        prosody=ProsodyEvidence(),
        engine_evidence={"operation": op, "expected_phone_posterior": ep,
                         "extra_heard_phones": extras or []},
    )


def result(words, heard=None, status="ok"):
    """words: list of (word, [phonemes]); timings are laid out sequentially when not given."""
    t = 100.0
    out = []
    for w, phs in words:
        for p in phs:
            if p.timing.start_ms == 0.0 and p.timing.end_ms == 20.0:
                p.timing.start_ms, p.timing.end_ms = t, t + 20.0
                p.timing.frame_start, p.timing.frame_end = int(t // 20), int((t + 20) // 20)
            t += 120.0
        starts = [p.timing.start_ms for p in phs if p.timing.source == "engine" and p.timing.start_ms is not None]
        ends = [p.timing.end_ms for p in phs if p.timing.source == "engine" and p.timing.end_ms is not None]
        out.append(WordResult(word=w, expected_phonemes=[p.expected.phoneme for p in phs],
                              timing=TimingInfo(start_ms=min(starts) if starts else None, end_ms=max(ends) if ends else None,
                                                source="engine" if starts else "unknown"),
                              phonemes=phs, engine_evidence={}))
    return PronunciationResult(
        status=status,
        recording=RecordingInfo(id="X", audio=RecordingAudio(original_path="x.wav", sample_rate_hz=16000, channels=1,
                                                             duration_ms=DURATION), target=TargetInfo(text="a test sentence")),
        engine=EngineInfo(name="wav2vec2_raw", mode="local", model="m@1"),
        processing=ProcessingInfo(wall_time_ms=1.0),
        phone_set="ipa-espeak-raw",
        words=out,
        engine_evidence={"recognition": {"phones": heard if heard is not None else ["x"]}},
    )


def obs_of(res, i=0):
    return [o for o in PC.build_observations(res) if o["kind"] == "sound"][i]


# ----------------------------------------------------------------------
# Classification and thresholds
# ----------------------------------------------------------------------

@pytest.mark.parametrize("op, observed, ep, nbest, typ, conf", [
    ("match", "w", 0.9, [("w", 0.9), ("v", 0.05)], "expected", "high"),
    ("match", "w", 0.49, [("w", 0.49), ("v", 0.05)], "expected", "moderate"),     # below HIGH_EXPECTED
    ("match", "w", 0.5, [("w", 0.5), ("v", 0.05)], "expected", "high"),           # boundary
    ("match", "θ", 0.5, [("θ", 0.5), ("t", 0.31)], "ambiguous", "low"),           # margin 0.19 < 0.2
    ("match", "θ", 0.5, [("θ", 0.5), ("t", 0.30)], "expected", "high"),           # margin 0.2 is not close
    ("match", "w", None, [("w", 0.9), ("v", 0.0)], "expected", "moderate"),       # posterior unavailable
    ("substitution", "v", 0.02, [("v", 0.87), ("w", 0.02)], "substitution_candidate", "high"),
    ("substitution", "v", 0.02, [("v", 0.51), ("w", 0.02)], "substitution_candidate", "moderate"),  # 0.49 < 0.5
    ("substitution", "v", 0.02, [("v", 0.52), ("w", 0.02)], "substitution_candidate", "high"),      # 0.50 dominance
    ("substitution", "v", 0.05, [("v", 0.87), ("w", 0.05)], "ambiguous", "low"),  # expected plausible (boundary)
    ("substitution", "v", 0.049, [("v", 0.87), ("w", 0.049)], "substitution_candidate", "high"),
    ("substitution", "v", 0.01, [("v", 0.40), ("ʋ", 0.25)], "ambiguous", "low"),  # close runner-up
    ("substitution", "v", None, [("v", 0.9), ("w", 0.0)], "ambiguous", "low"),    # cannot establish dominance
    ("omission", None, 0.0, [("z", 0.01), ("q", 0.0)], "omission_candidate", "low"),
    ("omission", None, 0.06, [("w", 0.06), ("q", 0.0)], "weak_evidence", "low"),
    ("omission", None, None, [("z", 0.01), ("q", 0.0)], "omission_candidate", "low"),
])
def test_classification_and_boundaries(op, observed, ep, nbest, typ, conf):
    o = obs_of(result([("word", [ph("w" if observed in (None, "v", "w") else observed, observed, op, ep, nbest)])]))
    assert (o["type"], o["confidence"]) == (typ, conf), o["reasons"]


def test_omission_never_claims_absence():
    o = obs_of(result([("word", [ph("s", None, ep=0.0, nbest=[("ə", 0.01), ("q", 0.0)])])]))
    assert any("does not prove the sound was absent" in r for r in o["reasons"])
    assert o["observed"] is None and o["observed_posterior"] is None


def test_substitution_keeps_both_posteriors_and_competitor():
    o = obs_of(result([("would", [ph("w", "v", ep=0.02, nbest=[("v", 0.86), ("w", 0.02)])])]))
    assert (o["competitor"], o["observed_posterior"], o["competitor_posterior"], o["expected_posterior"]) == ("v", 0.86, 0.86, 0.02)


def test_derived_timing_caps_confidence():
    o = obs_of(result([("w", [ph("w", "v", ep=0.0, nbest=[("v", 0.9), ("w", 0.0)], source="derived")])]))
    assert o["type"] == "substitution_candidate" and o["confidence"] == "moderate"
    assert "location estimated from neighbouring sounds" in o["reasons"]


# ----------------------------------------------------------------------
# Context, stress, acoustics, reference notes
# ----------------------------------------------------------------------

def test_context_positions_neighbours_and_cluster():
    res = result([("a", [ph("ə", "ə")]), ("stop", [ph("s", "s"), ph("t", "t"), ph("ɑ", "ɑ"), ph("p", "p")])])
    obs = [o for o in PC.build_observations(res)]
    by = {(o["word"], o["context"]["position_in_word"]): o for o in obs}
    assert by[("a", 0)]["context"]["word_position"] == "single"
    s, t, a, p = (by[("stop", i)]["context"] for i in range(4))
    assert (s["word_position"], t["word_position"], p["word_position"]) == ("initial", "medial", "final")
    assert (s["previous_phone"], s["next_phone"]) == ("ə", "t")           # crosses the word boundary
    assert s["word_boundary_before"] and p["word_boundary_after"] and p["next_phone"] is None
    assert s["in_consonant_cluster"] and t["in_consonant_cluster"] and not a["in_consonant_cluster"] and not p["in_consonant_cluster"]


def test_unknown_stress_is_never_unstressed():
    no_stress = obs_of(result([("w", [ph("ɪ", "ɪ", stress=None)])]))
    assert no_stress["context"]["stress"] is None and no_stress["context"]["stress_known"] is False
    known = PC.build_observations(result([("w", [ph("ɪ", "ɪ", stress="primary"), ph("ə", "ə", stress=None)])]))
    assert known[0]["context"] == known[0]["context"] | {"stress": "primary", "stress_known": True}
    assert known[1]["context"]["stress"] is None and known[1]["context"]["stress_known"] is True


def test_missing_acoustics_are_null_not_zero():
    a = AcousticEvidence(measured_on="analysis", energy_db=None, relative_energy=None, voicing=None, f0_hz=None,
                         spectral_features=None)
    o = obs_of(result([("w", [ph("w", "w", acoustic=a)])]))
    assert o["acoustic"] == {"relative_energy": None, "energy_db": None, "voiced": None, "f0_hz": None,
                             "zero_crossing_rate": None, "spectral_centroid_hz": None}


@pytest.mark.parametrize("expected, competitor, note", [("ʊɹ", "uː", True), ("ɔ", "ʌ", True), ("iː", "i", True),
                                                        ("w", "v", False), ("ɪ", "iː", False), ("θ", "t", False)])
def test_reference_notes(expected, competitor, note):
    o = obs_of(result([("w", [ph(expected, competitor, ep=0.01, nbest=[(competitor, 0.8), (expected, 0.01)])])]))
    assert (o["reference_note"] is not None) == note


# ----------------------------------------------------------------------
# Negative / malformed evidence
# ----------------------------------------------------------------------

@pytest.mark.parametrize("mutate, reason", [
    (lambda p: setattr(p.timing, "start_ms", None), "missing timing"),
    (lambda p: setattr(p.timing, "end_ms", None), "missing timing"),
    (lambda p: (setattr(p.timing, "start_ms", 300.0), setattr(p.timing, "end_ms", 200.0)), "invalid timing"),  # reversed
    (lambda p: (setattr(p.timing, "start_ms", 300.0), setattr(p.timing, "end_ms", 300.0)), "invalid timing"),  # zero length
    (lambda p: (setattr(p.timing, "start_ms", -20.0), setattr(p.timing, "end_ms", 0.0)), "invalid timing"),
    (lambda p: setattr(p.timing, "end_ms", float("nan")), "invalid timing"),
    (lambda p: p.engine_evidence.update(expected_phone_posterior=1.5), "invalid expected-phone posterior"),
    (lambda p: p.engine_evidence.update(expected_phone_posterior=-0.1), "invalid expected-phone posterior"),
    (lambda p: p.engine_evidence.update(expected_phone_posterior=float("nan")), "invalid expected-phone posterior"),
    (lambda p: setattr(p.observed.nbest[0], "probability", 1.2), "invalid N-best"),
    (lambda p: setattr(p.observed.nbest[0], "phoneme", ""), "invalid N-best"),
    (lambda p: p.engine_evidence.update(operation="teleport"), "unknown alignment operation"),
    (lambda p: p.engine_evidence.pop("operation"), "unknown alignment operation"),
    (lambda p: setattr(p.expected, "phoneme", ""), "missing expected phoneme"),
])
def test_malformed_evidence_is_not_interpreted(mutate, reason):
    p = ph("w", "v", ep=0.02, nbest=[("v", 0.87), ("w", 0.02)], start=200.0, end=220.0)
    mutate(p)
    o = obs_of(result([("would", [p])]))
    assert (o["type"], o["confidence"]) == ("not_interpreted", "none")
    assert reason in o["reasons"][0]
    assert o["evidence"]["invalid"] and any(reason in x for x in o["evidence"]["invalid"])
    for key in ("expected_posterior", "observed_posterior", "competitor_posterior"):
        assert o[key] is None or 0.0 <= o[key] <= 1.0  # invalid numbers never surface as values
    coach = C.build_coach(result([("would", [p])]))
    assert coach["patterns"] == [] and coach["practice_targets"] == [] and coach["integrity"]["ok"], coach["integrity"]


def test_missing_word_is_not_interpreted():
    res = result([("would", [ph("w", "v", ep=0.02, nbest=[("v", 0.87), ("w", 0.02)])])])
    res.words[0].word = ""
    assert obs_of(res)["type"] == "not_interpreted"


def test_nbest_peaks_need_not_sum_to_one():
    """N-best values are peak posteriors over a span; they are not renormalised or rejected."""
    o = obs_of(result([("w", [ph("w", "w", ep=0.9, nbest=[("w", 0.9), ("v", 0.6), ("b", 0.5)])])]))
    assert o["type"] == "expected" and [c["probability"] for c in o["nbest"]] == [0.9, 0.6, 0.5]


def test_alignment_suspect_words_are_never_coached():
    pile = [{"phone": x, "start_ms": 600.0 + i * 20, "end_ms": 620.0 + i * 20, "frame_start": 30, "frame_end": 31}
            for i, x in enumerate("abc")]
    res = result([("one", [ph("w", "v", ep=0.0, nbest=[("v", 0.9), ("w", 0.0)])]),
                  ("two", [ph("t", "t", extras=pile)]),
                  ("three", [ph("θ", "t", ep=0.0, nbest=[("t", 0.9), ("θ", 0.0)])]),
                  ("four", [ph("w", "v", ep=0.0, nbest=[("v", 0.9), ("w", 0.0)])])])
    coach = C.build_coach(res)
    by_word = {}
    for o in coach["observations"]:
        by_word.setdefault(o["word"], []).append(o)
    for w in ("one", "two", "three"):
        assert {o["type"] for o in by_word[w]} == {"not_interpreted"}, w
        assert any("alignment suspect" in r for o in by_word[w] for r in o["reasons"])
    assert by_word["four"][0]["type"] == "substitution_candidate"
    supported = {r for p in coach["patterns"] for r in p["observation_ids"]}
    assert all(o["id"] not in supported for w in ("one", "two", "three") for o in by_word[w])


@pytest.mark.parametrize("status", ["failed", "blocked"])
def test_unavailable_analysis_has_no_coaching(status):
    coach = C.build_coach(result([("w", [ph("w", "w")])], status=status))
    assert coach["state"] == "unavailable" and coach["observations"] == [] and coach["patterns"] == []


@pytest.mark.parametrize("heard, words", [([], [("w", [ph("w", "w")])]), (["x"], [])])
def test_no_speech_or_empty_transcript_has_no_coaching(heard, words):
    coach = C.build_coach(result(words, heard=heard))
    assert coach["state"] == "no_evidence" and coach["practice_targets"] == []


# ----------------------------------------------------------------------
# Patterns
# ----------------------------------------------------------------------

def sub(exp, obs, conf_p=0.9, ep=0.0):
    return ph(exp, obs, ep=ep, nbest=[(obs, conf_p), (exp, ep)])


def amb(exp, obs):
    return ph(exp, obs, ep=0.2, nbest=[(obs, 0.6), (exp, 0.2)])


def test_one_off_is_monitor_not_a_pattern_claim():
    coach = C.build_coach(result([("sing", [ph("s", "s"), sub("ŋ", "n")])]))
    [p] = coach["patterns"]
    assert (p["class"], p["group"]) == ("one_off", "single")
    assert "Single observation — monitor for recurrence." in p["summary"]
    [t] = coach["practice_targets"]
    assert t["kind"] == "monitor" and t["supporting_observation_ids"] == p["observation_ids"]


def test_repeated_versus_consistent():
    two = C.build_coach(result([("would", [sub("w", "v")]), ("we", [sub("w", "v")])]))
    assert two["patterns"][0]["class"] == "repeated" and two["patterns"][0]["group"] == "recurring"
    three = C.build_coach(result([("would", [sub("w", "v")]), ("we", [sub("w", "v")]), ("wants", [sub("w", "v")])]))
    p = three["patterns"][0]
    assert p["class"] == "consistent" and "Recurring pattern in this recording." in p["summary"]
    assert three["practice_targets"][0]["kind"] == "practice"


def test_consistent_needs_more_deviations_than_expected_decodes():
    words = [(f"w{i}", [sub("w", "v")]) for i in range(3)] + [(f"x{i}", [ph("w", "w")]) for i in range(4)]
    p = C.build_coach(result(words))["patterns"][0]
    assert p["occurrences"] == 3 and p["heard_as_expected_elsewhere"] == 4 and p["class"] == "repeated"
    assert "was heard as expected in 4 other occurrences" in p["summary"]


def test_consistent_needs_high_confidence_support():
    words = [(f"w{i}", [sub("w", "v", conf_p=0.4)]) for i in range(3)]  # dominance 0.4 < 0.5: moderate
    assert C.build_coach(result(words))["patterns"][0]["class"] == "repeated"


def test_context_specific_pattern():
    # /s/ not detected only word-finally, heard as expected word-initially
    words = [("sit", [ph("s", "s"), ph("ɪ", "ɪ"), ph("t", "t")]),
             ("bus", [ph("b", "b"), ph("ʌ", "ʌ"), ph("s", None, ep=0.0, nbest=[("ə", 0.01), ("q", 0.0)])]),
             ("yes", [ph("j", "j"), ph("ɛ", "ɛ"), ph("s", None, ep=0.0, nbest=[("ə", 0.01), ("q", 0.0)])])]
    p = C.build_coach(result(words))["patterns"][0]
    assert (p["kind"], p["class"], p["context"]) == ("detection", "context_specific", "final")
    assert "Only seen word-final" in p["summary"] and "not a general /s/ pattern" in p["summary"]


def test_ambiguous_only_is_grouped_and_framed_as_compare():
    coach = C.build_coach(result([("think", [amb("θ", "t")]), ("thing", [amb("θ", "t")])]))
    [p] = coach["patterns"]
    assert p["group"] == "ambiguous" and p["evidence_strength"] == "ambiguous"
    assert "ambiguous between /θ/ and /t/" in p["summary"]
    [t] = coach["practice_targets"]
    assert t["kind"] == "compare" and "not a confirmed difference" in t["reason"]


def test_matches_and_mixed_evidence_do_not_merge_different_contrasts():
    coach = C.build_coach(result([("a", [sub("s", "ʃ")]), ("b", [sub("s", "z")]), ("c", [sub("s", "ʃ")])]))
    keys = {(p["expected"], p["contrast"]): p["occurrences"] for p in coach["patterns"]}
    assert keys == {("s", "ʃ"): 2, ("s", "z"): 1}


def test_insertions_form_their_own_patterns():
    extra = [{"phone": "ɪ", "start_ms": 130.0, "end_ms": 150.0, "frame_start": 6, "frame_end": 7}]
    coach = C.build_coach(result([("three", [ph("θ", "θ", extras=extra), ph("ɹ", "ɹ")])]))
    [p] = coach["patterns"]
    assert (p["kind"], p["contrast"], p["group"]) == ("insertion", "ɪ", "insertion")


def test_groups_follow_neutral_order_and_time():
    words = [("a", [amb("θ", "t")]), ("b", [sub("w", "v")]), ("c", [sub("w", "v")]), ("d", [sub("ŋ", "n")]),
             ("e", [ph("s", None, ep=0.0, nbest=[("ə", 0.01), ("q", 0.0)])])]
    coach = C.build_coach(result(words))
    assert [g["id"] for g in coach["groups"]] == ["recurring", "single", "ambiguous", "not_detected"]
    for g in coach["groups"]:
        firsts = [next(p for p in coach["patterns"] if p["id"] == pid)["first_ms"] for pid in g["pattern_ids"]]
        assert firsts == sorted(firsts)


# ----------------------------------------------------------------------
# Practice targets
# ----------------------------------------------------------------------

def test_practice_target_levels_and_evidence():
    coach = C.build_coach(result([("would", [sub("w", "v")]), ("we", [sub("w", "v")])]))
    [t] = coach["practice_targets"]
    assert (t["target_phoneme"], t["contrast_phoneme"]) == ("w", "v")
    assert t["levels"]["sound"]["guidance"] and t["levels"]["sound"]["guidance_note"].startswith("General description")
    assert t["levels"]["word"]["words"] == ["would", "we"] and t["levels"]["sentence"]["text"] == "a test sentence"
    assert [o["observation_id"] for o in t["occurrences"]] == t["supporting_observation_ids"]
    assert all(o["play_ms"] and o["word_play_ms"] for o in t["occurrences"])


def test_no_target_without_evidence():
    pattern = {"id": "p00", "observation_ids": ["nope"], "group": "single", "kind": "contrast", "expected": "w",
               "contrast": "v", "summary": "", "evidence_strength": "strong", "reference_note": None}
    assert PR.build_targets([pattern], {}, "text") == []


def test_unknown_contrast_has_no_invented_guidance():
    assert PR.guidance("ʔ", "ɣ") is None and PR.guidance("w", None) is None
    assert PR.guidance("v", "w") == PR.guidance("w", "v")


# ----------------------------------------------------------------------
# Invariants (the validator, and that it catches violations)
# ----------------------------------------------------------------------

@pytest.fixture
def good():
    words = [("would", [sub("w", "v")]), ("we", [sub("w", "v")]), ("sing", [ph("s", "s"), sub("ŋ", "n")]),
             ("think", [amb("θ", "t")])]
    coach = C.build_coach(result(words))
    assert coach["integrity"] == {"ok": True, "issues": []}
    return coach


@pytest.mark.parametrize("damage, issue", [
    (lambda c: c["observations"].append(copy.deepcopy(c["observations"][0])), "duplicate observation ids"),
    (lambda c: c["patterns"][0]["observation_ids"].append("o999"), "references unknown observation"),
    (lambda c: c["patterns"][0].__setitem__("observation_ids", []), "pattern without supporting observations"),
    (lambda c: c["patterns"][0]["observation_ids"].append(c["patterns"][0]["observation_ids"][0]), "duplicate evidence references"),
    (lambda c: c["practice_targets"][0].__setitem__("supporting_observation_ids", []), "practice target without supporting evidence"),
    (lambda c: c["practice_targets"][0].__setitem__("pattern_id", "p99"), "practice target without a pattern"),
    (lambda c: c["observations"][0].__setitem__("span_ms", [300.0, 200.0]), "timing/playback outside"),
    (lambda c: c["observations"][0].__setitem__("play_ms", None), "without exact timing/playback"),
    (lambda c: c["observations"][0].__setitem__("expected_posterior", 1.3), "outside [0, 1]"),
    (lambda c: next(o for o in c["observations"] if o["type"] == "ambiguous").__setitem__("confidence", "high"),
     "cannot be high-confidence"),
    (lambda c: c["observations"][0].__setitem__("confidence", "none"), "impossible for type"),
    (lambda c: c["observations"][0]["context"].update(stress="unstressed", stress_known=False), "stress reported although"),
    (lambda c: c["observations"][0]["reasons"].append("your tongue touched the teeth"), "articulatory wording"),
    (lambda c: c["patterns"][0].__setitem__("summary", "You pronounced it wrong."), "articulatory wording"),
    (lambda c: c["groups"].pop(), "exactly one display group"),
    (lambda c: c["patterns"][0].__setitem__("class", "one_off"), "one_off with"),
])
def test_validator_catches_violations(good, damage, issue):
    damaged = copy.deepcopy(good)
    damage(damaged)
    issues = C.validate_coach(damaged, duration_ms=DURATION)
    assert any(issue in i for i in issues), issues


def test_no_score_or_ranking_fields(good):
    import json

    import re

    text = json.dumps(good).lower()
    for word in ("score", "rank", "worst", "best", "grade", "native", "rating"):
        assert not re.search(rf"\b{word}\b", text), word


def test_thresholds_are_reported(good):
    assert good["thresholds"]["plausible_expected_posterior"] == 0.05
    assert good["thresholds"]["close_runner_up_margin"] == 0.2
    assert good["thresholds"]["high_confidence_dominance"] == 0.5
    assert good["thresholds"]["patterns"] == {"consistent_min_occurrences": 3, "consistent_min_words": 2,
                                              "consistent_min_high_confidence": 2}
    assert not math.isnan(good["thresholds"]["high_confidence_expected_posterior"])



def test_estimated_playback_window():
    assert PC.estimated_play_window(1000.0, 1040.0, 5000.0) == list(PC.sound_window(1000.0, 1040.0, 5000.0))
    assert PC.estimated_play_window(1000.0, 4000.0, 5000.0) == [850.0, 1650.0]
    assert PC.estimated_play_window(100.0, 4000.0, 5000.0) == [0.0, 800.0]
    assert PC.estimated_play_window(4900.0, 4990.0, 5000.0)[1] == 5000.0


def test_insertion_patterns_are_low_strength():
    extra = [{"phone": "ɪ", "start_ms": 130.0, "end_ms": 150.0, "frame_start": 6, "frame_end": 7}]
    [p] = C.build_coach(result([("three", [ph("θ", "θ", extras=extra)])]))["patterns"]
    assert p["evidence_strength"] == "low"


def test_function_word_vowel_note_only_for_function_words():
    fw = C.build_coach(result([("the", [ph("ð", "ð"), sub("ɪ", "ə")]), ("to", [ph("t", "t"), sub("ɪ", "ə")])]))
    assert "function words" in fw["patterns"][0]["reference_note"]
    content = C.build_coach(result([("the", [sub("ɪ", "ə")]), ("ship", [ph("ʃ", "ʃ"), sub("ɪ", "ə")])]))
    assert content["patterns"][0]["reference_note"] is None


def test_build_coach_reports_its_own_integrity_violations(monkeypatch):
    """The runtime self-check must surface a broken pattern, not just the unit validator."""
    real = C.build_patterns

    def broken(observations):
        patterns, groups = real(observations)
        patterns[0]["observation_ids"].append("o999")
        return patterns, groups

    monkeypatch.setattr(C, "build_patterns", broken)
    coach = C.build_coach(result([("would", [sub("w", "v")]), ("we", [sub("w", "v")])]))
    assert coach["integrity"]["ok"] is False
    assert any("references unknown observation o999" in i for i in coach["integrity"]["issues"])



def test_coverage_states_what_is_not_listed():
    coach = C.build_coach(result([("think", [ph("θ", "θ"), ph("ɪ", "ɪ")]), ("would", [sub("w", "v")])]))
    cov = coach["coverage"]
    assert (cov["sounds"], cov["consistent_with_expected"], cov["listed_in_patterns"]) == (3, 2, 1)
    assert "not proof that it was pronounced canonically" in cov["note"]
    damaged = copy.deepcopy(coach)
    damaged["coverage"]["consistent_with_expected"] = 3
    assert any("coverage count" in i for i in C.validate_coach(damaged, duration_ms=DURATION))
