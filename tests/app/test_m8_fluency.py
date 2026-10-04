"""M8 fluency layer: schema, pauses, rates, fillers, repetitions, restarts, false starts, validation (synthetic)."""

import copy
import json
import math

import m7helpers as H7
import m8helpers as M
import pytest

from pronunciation_lab.app import fluency as F

A = M.ALL


def run(events, **kw):
    result, audio, marks = M.build(events, **kw)
    before = result.model_dump_json()
    fl = F.build_fluency(result, audio, M.SR)
    assert result.model_dump_json() == before  # never mutates the engine result
    assert fl["integrity"] == {"ok": True, "issues": []}, fl["integrity"]
    return fl, result, marks


def of(fl, type_, **match):
    return [o for o in fl["observations"] if o["type"] == type_ and all(o.get(k) == v for k, v in match.items())]


# ----------------------------------------------------------------------
# Schema and contract
# ----------------------------------------------------------------------

def test_types_and_strengths_have_no_score_and_no_high():
    assert F.TYPES == ("PAUSE", "FILLER", "REPETITION", "FALSE_START", "RESTART", "RATE_ANOMALY", "OTHER_HESITATION")
    assert F.STRENGTHS == ("moderate", "low", "ambiguous", "insufficient")
    assert "high" not in F.STRENGTHS


def test_observation_shape_and_exact_playback():
    fl, _, _ = run(A[:4] + [("p", 900)] + A[4:])
    [o] = fl["observations"]
    for key in ("id", "type", "label", "start_ms", "end_ms", "duration_ms", "observed", "evidence", "interpretation",
                "strength", "context", "playback", "engine"):
        assert key in o
    assert o["duration_ms"] == o["end_ms"] - o["start_ms"] > 0
    assert o["playback"]["timeline"] == "analysis_wav" and o["playback"]["span_ms"] == [o["start_ms"], o["end_ms"]]
    assert o["playback"]["play_ms"][0] <= o["start_ms"] and o["playback"]["play_ms"][1] >= o["end_ms"]
    assert all(e["kind"] and e["detail"] for e in o["evidence"]) and o["engine"] == "wav2vec2_raw"
    assert fl["region"] == {"start_ms": 0.0, "end_ms": pytest.approx(fl["region"]["end_ms"]), "timeline": "analysis_wav"}
    json.dumps(fl)  # JSON-able


def test_deterministic():
    r, x, _ = M.build(A[:4] + [("p", 900)] + A[4:5] + [("w", 5), ("p", 300)] + A[5:])
    assert json.dumps(F.build_fluency(r, x, M.SR), sort_keys=True) == json.dumps(F.build_fluency(r, x, M.SR), sort_keys=True)


def test_failed_and_empty_results():
    r = H7.make(M.WORDS, [], status="failed")
    assert F.build_fluency(r)["state"] == "unavailable"
    r = H7.make(M.WORDS, [], duration_ms=2000)
    fl = F.build_fluency(r)
    assert fl["state"] == "no_speech" and fl["observations"] == [] and F.validate_fluency(fl) == []


# ----------------------------------------------------------------------
# Pauses
# ----------------------------------------------------------------------

def test_clean_reading_has_nothing_to_notice():
    fl, _, _ = run(A)
    assert fl["observations"] == [] and fl["summary"]["notice"] == 0
    assert fl["summary"]["pace"] == "steady" and "nothing stood out" in fl["summary"]["text"]


def test_pause_at_punctuation_is_a_natural_boundary_not_a_hesitation():
    fl, _, _ = run(A[:3] + [("p", 700)] + A[3:])  # "Think about it, | then change the plan."
    [p] = of(fl, "PAUSE")
    assert p["classification"] == "natural_boundary" and not p["notice"]
    assert p["context"]["word_before"] == "it" and "phrase boundary" in p["context"]["position"]


def test_pause_inside_a_phrase_long_for_the_pace_is_a_possible_hesitation():
    # the reference is this recording's word interval with its pauses replaced by its typical gap (300 ms here):
    # 700 ms is 2.3× → possible hesitation, low; 1000 ms is 3.3× → moderate (reliable level estimate)
    low, _, _ = run(A[:4] + [("p", 700)] + A[4:])  # "then | change"
    [q] = of(low, "PAUSE")
    assert q["classification"] == "possible_hesitation" and q["notice"] and q["strength"] == "low"
    assert low["metrics"]["word_interval_basis"] == "pauses removed"
    fl, _, _ = run(A[:4] + [("p", 1000)] + A[4:])
    [p] = of(fl, "PAUSE")
    assert p["classification"] == "possible_hesitation" and p["notice"] and p["strength"] == "moderate"
    kinds = {e["kind"] for e in p["evidence"]}
    assert {"position", "relative_duration", "silence"} <= kinds
    assert "consistent with hesitation" in p["interpretation"]


def test_brief_pause_inside_a_phrase_is_not_interpreted():
    fl, _, _ = run(A[:4] + [("p", 300)] + A[4:])
    [p] = of(fl, "PAUSE")
    assert p["classification"] == "brief_within_phrase" and not p["notice"]


def test_same_duration_classified_by_context_not_by_duration():
    at_comma, _, _ = run(A[:3] + [("p", 900)] + A[3:])
    in_phrase, _, _ = run(A[:4] + [("p", 900)] + A[4:])
    assert of(at_comma, "PAUSE")[0]["classification"] == "natural_boundary"
    assert of(in_phrase, "PAUSE")[0]["classification"] == "possible_hesitation"


def test_unusually_long_pause_relative_to_the_pace():
    fl, _, _ = run(A[:4] + [("p", 2500)] + A[4:])
    [p] = of(fl, "PAUSE")
    assert p["classification"] == "unusually_long" and p["notice"]


def test_unusually_long_pause_at_a_boundary_is_low_strength():
    fl, _, _ = run(A[:3] + [("p", 2500)] + A[3:])
    [p] = of(fl, "PAUSE")
    assert p["classification"] == "unusually_long" and p["strength"] == "low" and "boundary" in p["interpretation"]


def test_leading_and_trailing_silence_are_never_pauses():
    fl, _, _ = run(A, start_ms=3000.0, tail_ms=3000.0)
    assert of(fl, "PAUSE") == []
    m = fl["metrics"]
    assert m["leading_silence_ms"] == pytest.approx(3000.0) and m["trailing_silence_ms"] >= 3000.0


def test_short_gap_below_the_candidate_length_is_not_a_pause():
    fl, _, _ = run(A[:4] + [("p", 200)] + A[4:])
    assert of(fl, "PAUSE") == []


def test_gap_with_sound_but_nothing_decoded_is_not_a_silent_pause():
    result, audio, _ = M.build(A[:4] + [("p", 700)] + A[4:])
    audio = H7.audio(len(audio) * 1000 / M.SR, [(400, 1640), (1600, 2400), (2300, 3500)])  # hum through the gap
    fl = F.build_fluency(result, audio, M.SR)
    assert of(fl, "PAUSE") == []
    [o] = of(fl, "OTHER_HESITATION")
    assert o["strength"] == "insufficient" and not o["notice"] and o["context"]["word_before"] == "then"


def test_unreliable_level_estimate_uses_decoded_gaps_and_caps_strength():
    fl, _, _ = run(A[:4] + [("p", 900)] + A[4:], noise_db=-22.0, speech_db=-20.0)
    assert fl["metrics"]["activity_reliable"] is False
    [p] = of(fl, "PAUSE")
    assert p["strength"] in ("low", "insufficient")
    assert any("not measurable reliably" in e["detail"] for e in p["evidence"])


def test_silence_inside_a_word_before_a_stop_is_not_a_hesitation():
    words = [("think", "θ ɪ ŋ k"), ("about", "ə b aʊ t"), ("it", "ɪ t")]
    d = H7.heard("θ ɪ ŋ", 400) + H7.heard("k", 700 + 220) + H7.heard("ə b aʊ t ɪ t", 1100)
    r = H7.make(words, d, duration_ms=2200, text="Think about it.")
    x = H7.audio(2200, [(400, 640), (920, 1000), (1100, 1600)])
    fl = F.build_fluency(r, x, M.SR)
    [p] = of(fl, "PAUSE")
    assert p["context"]["position"] == "inside a word" and p["classification"] == "brief_within_phrase"
    assert "stop consonant" in p["interpretation"] and not p["notice"]


# ----------------------------------------------------------------------
# Rates
# ----------------------------------------------------------------------

def test_rates_use_expected_syllables_and_documented_definitions():
    fl, result, marks = run(A[:4] + [("p", 900)] + A[4:])
    m = fl["metrics"]
    expected_vowels = sum(1 for w in result.words for p in w.phonemes if F.is_vowel(p.expected.phoneme))
    assert m["syllable_count"] == expected_vowels == 8
    first = marks[0][1]
    last = [mk for mk in marks if mk[0] == "w"][-1][2]
    assert m["speaking_interval_ms"] == pytest.approx(last - first)
    assert m["speaking_rate"] == pytest.approx(8 / (m["speaking_interval_ms"] / 1000))
    assert m["articulation_rate"] == pytest.approx(8 / ((m["speaking_interval_ms"] - m["pause_total_ms"]) / 1000))
    assert m["pause_ratio"] == pytest.approx(m["pause_total_ms"] / m["speaking_interval_ms"])
    assert m["pause_count"] == 1 and m["mean_pause_ms"] == m["median_pause_ms"] == m["pause_total_ms"]
    assert m["speech_active_ms"] is not None and m["rate_available"] and set(F.DEFINITIONS) <= set(m["definitions"])


def test_extra_decoded_sounds_do_not_change_the_syllable_count():
    clean, _, _ = run(A)
    extra, _, _ = run(A[:4] + [("x", "ʌ m ʌ")] + A[4:])
    assert clean["metrics"]["syllable_count"] == extra["metrics"]["syllable_count"] == 8


def test_rate_unavailable_when_the_sentence_is_not_recognisably_decoded():
    d, _, _, _ = M.layout(A)
    garbled = [("ʔ" if i % 3 else p, a, b) for i, (p, a, b) in enumerate(d)]
    r = H7.make(M.WORDS, garbled, duration_ms=4000, text=M.TEXT)
    fl = F.build_fluency(r, H7.audio(4000, [(500, 3300)]), M.SR)
    m = fl["metrics"]
    assert not m["rate_available"] and m["speaking_rate"] is None and m["articulation_rate"] is None
    assert "not recognisably decoded" in m["rate_unavailable_reason"] and fl["summary"]["pace"] == "insufficient evidence"


def test_an_unclearly_decoded_sentence_counts_only_very_long_pauses():
    import m8helpers
    # most of the sentence's sounds decoded as other sounds: positions in the sentence are uncertain
    garbled = [("x", "k k k k"), ("w", 1), ("p", 900), ("x", "s s s s s"), ("w", 4), ("p", 2500), ("x", "f f f f")]
    result, audio, _ = m8helpers.build(garbled)
    fl = F.build_fluency(result, audio, M.SR)
    assert fl["integrity"]["ok"], fl["integrity"]
    assert not fl["metrics"]["rate_available"] and "not recognisably decoded" in fl["metrics"]["rate_unavailable_reason"]
    noticed = [o for o in fl["observations"] if o["notice"]]
    assert all(o["type"] == "PAUSE" and o["classification"] == "unusually_long" for o in noticed)
    assert any("listed but not counted" in o["interpretation"] for o in fl["observations"] if not o["notice"])
    assert fl["summary"]["text"].startswith("The sentence was decoded too unclearly to describe its pace")


def test_rate_unavailable_for_a_very_short_sentence():
    words = [("it", "ɪ t")]
    r = H7.make(words, H7.heard("ɪ t", 400), duration_ms=1200, text="It.")
    fl = F.build_fluency(r, H7.audio(1200, [(400, 560)]), M.SR)
    assert not fl["metrics"]["rate_available"] and "too short" in fl["metrics"]["rate_unavailable_reason"]


def test_unreliable_level_estimate_gives_no_articulation_rate_and_does_not_describe_the_pace():
    # only gaps between decoded sounds are known: they may include parts of the sounds themselves
    fl, _, _ = run(A[:1] + [("p", 700)] + A[1:3] + [("p", 900)] + A[3:5] + [("p", 900)] + A[5:],
                   noise_db=-22.0, speech_db=-20.0)
    m = fl["metrics"]
    assert m["activity_reliable"] is False and m["rate_available"] and m["speaking_rate"] is not None
    assert not m["articulation_available"] and m["articulation_rate"] is None and m["pause_ratio"] is None
    assert "sound level" in m["articulation_unavailable_reason"]
    assert fl["summary"]["pace"] == "insufficient evidence"
    assert fl["summary"]["text"].startswith("Pauses could not be checked against this recording's sound level")
    assert of(fl, "RATE_ANOMALY") == []
    for p in of(fl, "PAUSE"):  # kept as candidates; only an unusually long one is a thing to notice
        assert p["notice"] == (p["classification"] == "unusually_long") and "may include parts of them" in p["interpretation"]


def test_unreliable_unusually_long_pause_is_still_noticed():
    fl, _, _ = run(A[:4] + [("p", 2500)] + A[4:], noise_db=-22.0, speech_db=-20.0)
    [p] = of(fl, "PAUSE")
    assert p["classification"] == "unusually_long" and p["notice"] and p["strength"] == "low"
    assert fl["summary"]["text"].endswith("To notice: one long pause. The evidence is weak.")


def test_pause_reference_leaves_out_steps_that_contain_pauses():
    # a reading with many pauses must not inflate its own reference until its long pauses look short
    events = [("w", 0), ("p", 1300), ("w", 1), ("p", 1300), ("w", 2), ("w", 3), ("w", 4), ("w", 5), ("w", 6)]
    fl, _, _ = run(events)
    m = fl["metrics"]
    assert m["word_interval_basis"] == "pauses removed" and m["word_interval_ms"] < 500
    within = [p for p in of(fl, "PAUSE") if p["context"]["position"] == "between words inside a phrase"]
    assert within and all(p["classification"] in ("possible_hesitation", "unusually_long") and p["notice"] for p in within)
    assert not any(p["label"] == "Brief pause" for p in of(fl, "PAUSE"))


def test_reference_stays_the_articulation_pace_when_nearly_every_word_is_followed_by_a_pause():
    # pauses after almost every word: the reference is each word step with its pause removed, never inflated
    events = [("w", 0), ("p", 1100), ("w", 1), ("p", 1100), ("w", 2), ("w", 3), ("p", 1100), ("w", 4), ("p", 1100),
              ("w", 5), ("p", 1100), ("w", 6)]
    fl, _, _ = run(events)
    assert fl["metrics"]["word_interval_basis"] == "pauses removed" and fl["metrics"]["word_interval_ms"] < 500
    inside = [p for p in of(fl, "PAUSE") if p["context"]["position"] == "between words inside a phrase"]
    assert inside and all(p["classification"] in ("possible_hesitation", "unusually_long") for p in inside)


def test_slow_reading_alone_is_never_a_hesitation():
    # the same reading with every sound and gap 1.6× longer: classifications are relative to its own pace
    import m8helpers
    base, _, _ = run(A[:4] + [("p", 300)] + A[4:])
    old = (m8helpers.PHONE_MS, m8helpers.IN_WORD_GAP, m8helpers.WORD_GAP)
    try:
        m8helpers.PHONE_MS, m8helpers.IN_WORD_GAP, m8helpers.WORD_GAP = 96.0, 32.0, 96.0
        slow, _, _ = run(A[:4] + [("p", 480)] + A[4:])
    finally:
        m8helpers.PHONE_MS, m8helpers.IN_WORD_GAP, m8helpers.WORD_GAP = old
    assert slow["metrics"]["speaking_rate"] < base["metrics"]["speaking_rate"]
    assert [p["classification"] for p in of(slow, "PAUSE")] == [p["classification"] for p in of(base, "PAUSE")]
    assert slow["summary"]["notice"] == base["summary"]["notice"] == 0


def _peaks(pauses_after=(), pause_ms=600.0):
    """Decoder-style timing: each sound a 20 ms peak every 80 ms; optional pauses after some words."""
    d, t = [], 400.0
    for wi, (_, phones) in enumerate(M.WORDS):
        for ph in phones.split():
            d.append((ph, t, t + 20.0))
            t += 80.0
        if wi in pauses_after:
            t += pause_ms
    return d, t + 400.0


def test_reference_is_the_same_with_or_without_pauses_for_peak_timed_sounds():
    # a pause is replaced by the recording's typical gap, not removed outright (removing it would leave the
    # step shorter than the same step read without a pause, because a peak-timed sound has no real end)
    plain, dur0 = _peaks()
    paused, dur1 = _peaks(pauses_after=(1, 3, 5))
    refs = []
    for d, dur in ((plain, dur0), (paused, dur1)):
        r = H7.make(M.WORDS, d, duration_ms=dur, text=M.TEXT)
        refs.append(F.build_fluency(r, H7.audio(dur, [(380, dur - 380)]), M.SR)["metrics"]["word_interval_ms"])
    assert refs[0] == pytest.approx(refs[1], abs=5), refs


def test_sounds_without_timing_are_ignored_not_trusted():
    result, audio, _ = M.build(A[:4] + [("p", 700)] + A[4:])
    broken = result.model_copy(deep=True)
    ph = broken.words[2].phonemes[0]
    ph.timing.start_ms, ph.timing.end_ms = None, None                 # a decoded sound with no time
    ev = broken.words[3].phonemes[0].engine_evidence
    ev["extra_heard_phones"] = [{"phone": "ʌ", "start_ms": None, "end_ms": None},       # extra sounds with
                                {"phone": "ə", "start_ms": float("nan"), "end_ms": 10.0},  # missing or invalid
                                {"phone": "ɐ", "start_ms": 900.0, "end_ms": 800.0}]       # times
    sounds = F.decoded_sounds(broken)
    assert all(math.isfinite(s.start) and math.isfinite(s.end) and s.start <= s.end for s in sounds)
    assert len(sounds) == len(F.decoded_sounds(result)) - 1
    fl = F.build_fluency(broken, audio, M.SR)
    assert fl["state"] == "ok" and fl["integrity"]["ok"], fl["integrity"]


def test_pause_within_a_phrase_label_is_neutral():
    fl, _, _ = run(A[:4] + [("p", 300)] + A[4:])
    [p] = of(fl, "PAUSE")
    assert p["label"] == "Pause within a phrase" and "brief" not in p["interpretation"].lower()


def test_pause_heavy_summary():
    fl, _, _ = run(A[:1] + [("p", 700)] + A[1:3] + [("p", 900)] + A[3:5] + [("p", 900)] + A[5:])
    assert fl["metrics"]["pause_ratio"] >= F.PAUSE_HEAVY_RATIO and fl["summary"]["pace"] == "pause-heavy"


# ----------------------------------------------------------------------
# Fillers
# ----------------------------------------------------------------------

def test_filler_between_words_with_pauses_is_a_possible_filler():
    fl, _, _ = run(A[:4] + [("p", 300), ("x", "ʌ m"), ("p", 300)] + A[4:])
    [f] = of(fl, "FILLER")
    assert f["observed"] == "/ʌ m/" and f["notice"] and f["strength"] in ("low", "moderate")
    kinds = {e["kind"] for e in f["evidence"]}
    assert {"decoded_sounds", "position", "pause"} <= kinds and "um" in f["interpretation"]
    assert f["context"]["word_before"] == "then" and f["context"]["word_after"] == "change"


def test_sustained_filler_with_pauses_is_moderate():
    result, _, marks = M.build(A[:4] + [("p", 300), ("x", "ʌ m"), ("p", 300)] + A[4:])
    x0, x1 = next((m[1], m[2]) for m in marks if m[0] == "x")
    words = [(m[1], m[2]) for m in marks if m[0] == "w"]
    dur = len(M.build(A[:4] + [("p", 300), ("x", "ʌ m"), ("p", 300)] + A[4:])[1]) * 1000 / M.SR
    audio = H7.audio(dur, words + [(x0 - 60, x1 + 60)])  # the vowel sustained a little longer than its decoded peaks
    fl = F.build_fluency(result, audio, M.SR)
    [f] = of(fl, "FILLER")
    assert f["strength"] == "moderate" and any(e["kind"] == "acoustic" for e in f["evidence"])


def test_filler_spans_its_own_sound_not_just_the_decoded_peak():
    result, _, marks = M.build(A[:4] + [("p", 300), ("x", "ʌ m"), ("p", 300)] + A[4:])
    x0, x1 = next((m[1], m[2]) for m in marks if m[0] == "x")
    words = [(m[1], m[2]) for m in marks if m[0] == "w"]
    dur = len(M.build(A[:4] + [("p", 300), ("x", "ʌ m"), ("p", 300)] + A[4:])[1]) * 1000 / M.SR
    fl = F.build_fluency(result, H7.audio(dur, words + [(x0 - 60, x1 + 60)]), M.SR)
    [f] = of(fl, "FILLER")
    assert f["start_ms"] <= x0 - 50 and f["end_ms"] >= x1 + 50   # the sustained sound, not only the decoded peaks
    assert f["playback"]["span_ms"] == [f["start_ms"], f["end_ms"]] and fl["integrity"]["ok"]
    assert any("decoded at" in e["detail"] for e in f["evidence"])


def test_a_lone_vowel_without_acoustic_support_is_listed_but_not_noticed():
    fl, _, _ = run(A[:4] + [("p", 300), ("x", "ʌ"), ("p", 300)] + A[4:], noise_db=-22.0, speech_db=-20.0)
    for f in of(fl, "FILLER"):
        assert f["strength"] == "low" and not f["notice"]


def test_high_vowels_are_not_filler_shaped():
    fl, _, _ = run(A[:4] + [("p", 300), ("x", "ɪ"), ("p", 300)] + A[4:])
    assert of(fl, "FILLER") == []


def test_one_decoded_sound_is_never_both_a_filler_and_part_of_a_repetition():
    # "I am | am here": the extra "æ m" is filler-shaped ("um") but it is the first copy of "am"
    words = [("I", "aɪ"), ("am", "æ m"), ("here", "h ɪ ɹ"), ("now", "n aʊ")]
    d = H7.heard("aɪ æ m", 400) + H7.heard("æ m h ɪ ɹ n aʊ", 1100)   # a 400 ms pause after the first "am"
    r = H7.make(words, d, duration_ms=2300, text="I am here now.")
    fl = F.build_fluency(r, H7.audio(2300, [(400, 640), (1100, 1640)]), M.SR)
    assert fl["integrity"]["ok"], fl["integrity"]
    [r] = of(fl, "REPETITION")
    assert r["context"]["words"] == ["am"]
    assert of(fl, "FILLER") == []


def test_filler_shape_inside_a_word_is_not_a_filler():
    words = [("summer", "s ʌ m ɚ"), ("ends", "ɛ n d z")]
    d = H7.heard("s ʌ ə m ɚ ɛ n d z", 400)  # an extra /ə/ inside "summer"
    r = H7.make(words, d, duration_ms=1800, text="Summer ends.")
    fl = F.build_fluency(r, H7.audio(1800, [(400, 1100)]), M.SR)
    assert of(fl, "FILLER") == []


def test_non_filler_extra_sounds_are_not_fillers():
    fl, _, _ = run(A[:4] + [("p", 300), ("x", "s t"), ("p", 300)] + A[4:])
    assert of(fl, "FILLER") == []


def test_filler_shape_next_to_a_word_that_lost_its_vowel_is_ambiguous():
    words = [("think", "θ ɪ ŋ k"), ("a", "ə"), ("lot", "l ɑː t")]
    d = H7.heard("θ ɪ ŋ k", 400) + H7.heard("ə", 1000) + H7.heard("l ɑː t", 1500)
    r = H7.make(words, d[:4] + [("ʌ", 1000, 1060)] + d[5:], duration_ms=2500, text="Think a lot.")
    fl = F.build_fluency(r, H7.audio(2500, [(400, 700), (1000, 1060), (1500, 1800)]), M.SR)
    for f in of(fl, "FILLER"):
        assert f["strength"] == "ambiguous" and not f["notice"]


# ----------------------------------------------------------------------
# Repetitions, restarts, false starts
# ----------------------------------------------------------------------

def test_word_repetition_with_an_interruption_is_a_likely_self_repetition():
    fl, _, marks = run(A[:5] + [("w", 5), ("p", 300)] + A[5:])  # "the | the plan"
    [r] = of(fl, "REPETITION")
    assert r["label"] == "Likely self-repetition" and r["context"]["words"] == ["the"] and r["notice"]
    kinds = {e["kind"] for e in r["evidence"]}
    assert {"decoded_sounds", "similarity", "adjacency", "lexical", "interruption"} <= kinds
    first_the = [m for m in marks if m[0] == "w"][5]
    assert r["start_ms"] == pytest.approx(first_the[1])  # playback covers both copies
    # the pause between the copies is part of it, not a separate hesitation
    assert all(not p["notice"] for p in of(fl, "PAUSE"))


def test_a_long_pause_inside_a_repetition_is_part_of_it_not_a_second_thing_to_notice():
    fl, _, _ = run(A[:5] + [("w", 5), ("p", 1000)] + A[5:])  # "the | (1 s) the plan"
    [r] = of(fl, "REPETITION")
    [p] = [p for p in of(fl, "PAUSE") if r["start_ms"] <= p["start_ms"] and p["end_ms"] <= r["end_ms"]]
    assert p["classification"] in ("possible_hesitation", "unusually_long")   # long enough to be noticed on its own
    assert not p["notice"] and p["context"]["part_of"] == "REPETITION"
    assert fl["summary"]["notice"] == 1


def test_tight_short_repetition_is_ambiguous_may_be_emphasis():
    fl, _, _ = run(A[:5] + [("w", 5)] + A[5:])
    [r] = of(fl, "REPETITION")
    assert r["strength"] == "ambiguous" and not r["notice"] and "emphasis" in r["interpretation"]


def test_phrase_repetition():
    fl, _, _ = run(A[:6] + [("p", 350)] + A[4:])  # "change the | change the plan"
    [r] = of(fl, "REPETITION")
    assert r["context"]["words"] == ["change", "the"] and r["strength"] == "moderate"


def test_two_sounds_sharing_only_a_vowel_are_not_a_copy():
    # R08 ("The ship will leave"): /p iː/ (end of "ship" + a weak "will") then /l iː/ ("leave") are not a restart
    words = [("ship", "ʃ ɪ p"), ("will", "w ɪ l"), ("leave", "l iː v")]
    d = H7.heard("ʃ eɪ p", 400) + H7.heard("iː", 900) + H7.heard("l iː v", 1200)
    r = H7.make(words, d, duration_ms=2000, text="Ship will leave.")
    fl = F.build_fluency(r, H7.audio(2000, [(400, 620), (900, 960), (1200, 1420)]), M.SR)
    assert [o for o in fl["observations"] if o["type"] in ("RESTART", "REPETITION")] == []


def test_repetition_in_the_text_itself_is_not_a_repetition():
    words = [("had", "h æ d"), ("had", "h æ d"), ("left", "l ɛ f t")]
    r = H7.make(words, H7.heard("h æ d h æ d l ɛ f t", 400), duration_ms=1800, text="Had had left.")
    fl = F.build_fluency(r, H7.audio(1800, [(400, 1200)]), M.SR)
    assert of(fl, "REPETITION") == [] and of(fl, "RESTART") == []


def test_partial_word_then_the_word_is_a_possible_restart():
    fl, _, _ = run(A[:4] + [("cut", 4, 2), ("p", 400)] + A[4:])  # "chan- | change"
    [r] = of(fl, "RESTART")
    assert r["context"]["words"] == ["change"] and "restart" in r["interpretation"]
    assert "cannot be known" in r["interpretation"]
    [p] = of(fl, "PAUSE")  # the pause inside the restart is part of it, not a second thing to notice
    assert not p["notice"] and p["context"]["part_of"] == "RESTART"


def test_false_start_needs_onset_overlap_divergence_and_an_interruption():
    fl, _, _ = run(A[:4] + [("x", "tʃ eɪ s t ɪ"), ("p", 400)] + A[4:])
    [f] = of(fl, "FALSE_START")
    assert {"decoded_sounds", "onset_overlap", "interruption"} <= {e["kind"] for e in f["evidence"]}
    assert f["strength"] == "low" and "cannot be known" in f["interpretation"]
    assert all(not p["notice"] and p["context"]["part_of"] == "FALSE_START" for p in of(fl, "PAUSE"))
    assert fl["summary"]["notice"] == 1


def test_no_false_start_from_a_pause_alone_or_without_interruption():
    pause_only, _, _ = run(A[:4] + [("p", 900)] + A[4:])
    assert of(pause_only, "FALSE_START") == []
    no_break, _, _ = run(A[:4] + [("x", "tʃ eɪ s t ɪ")] + A[4:])
    assert of(no_break, "FALSE_START") == []


def test_no_false_start_from_unrelated_extra_sounds_followed_by_a_pause():
    fl, _, _ = run(A[:4] + [("x", "s t ɪ k"), ("p", 400)] + A[4:])  # no onset overlap with "change"
    assert of(fl, "FALSE_START") == []


def test_no_false_start_or_repetition_from_substitutions():
    d, _, _, _ = M.layout(A)
    subs = [({"θ": "t", "ð": "d", "æ": "ɛ"}.get(p, p), a, b) for p, a, b in d]
    r = H7.make(M.WORDS, subs, duration_ms=4000, text=M.TEXT)
    fl = F.build_fluency(r, H7.audio(4000, [(500, 3400)]), M.SR)
    assert [o for o in fl["observations"] if o["type"] in ("FALSE_START", "RESTART", "REPETITION", "FILLER")] == []


# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------

def test_summary_is_qualitative_and_counts_only_things_to_notice():
    fl, _, _ = run(A[:4] + [("p", 900)] + A[4:5] + [("w", 5), ("p", 300)] + A[5:])
    s = fl["summary"]
    assert s["notice"] == sum(1 for o in fl["observations"] if o["notice"]) == 2
    assert "possible hesitation pause" in s["text"] and "possible repetition" in s["text"]
    for banned in ("score", "%", "fluent", "rank", "wrong", "/100"):
        assert banned not in s["text"].lower()
    assert fl["compact"] == {"state": "ok", "notice": 2, "pace": s["pace"]}


# ----------------------------------------------------------------------
# Validation catches every defect
# ----------------------------------------------------------------------

@pytest.fixture
def full():
    fl, result, _ = run(A[:4] + [("p", 900)] + A[4:5] + [("w", 5), ("p", 300)] + A[5:])
    return fl, result


@pytest.mark.parametrize("corrupt, message", [
    (lambda o, fl: o.update(start_ms=-5.0, duration_ms=o["end_ms"] + 5), "negative timestamp"),
    (lambda o, fl: o.update(end_ms=o["start_ms"] - 10, duration_ms=-10), "end before start"),
    (lambda o, fl: o.update(end_ms=fl["region"]["end_ms"] + 500, duration_ms=fl["region"]["end_ms"] + 500 - o["start_ms"]),
     "outside the analysed audio"),
    (lambda o, fl: o.update(playback=None), "no exact playback"),
    (lambda o, fl: o["playback"].update(span_ms=[o["start_ms"] + 50, o["end_ms"]]), "playback span differs"),
    (lambda o, fl: o.update(evidence=[]), "no evidence"),
    (lambda o, fl: o.update(strength="high"), "not allowed"),
    (lambda o, fl: o.update(type="SCORE"), "unknown type"),
    (lambda o, fl: o.update(duration_ms=o["duration_ms"] + 1), "duration does not match"),
    (lambda o, fl: o.update(interpretation="a wrong pause"), "judgemental"),
])
def test_validator_catches_observation_defects(full, corrupt, message):
    fl, result = full
    fl = copy.deepcopy(fl)
    corrupt(fl["observations"][0], fl)
    assert any(message in i for i in F.validate_fluency(fl, result)), F.validate_fluency(fl, result)


def test_validator_catches_a_pause_classified_by_duration_alone(full):
    fl, result = copy.deepcopy(full[0]), full[1]
    p = next(o for o in fl["observations"] if o["type"] == "PAUSE" and o["classification"] == "possible_hesitation")
    p["evidence"] = [e for e in p["evidence"] if e["kind"] in ("decoded_gap", "silence")]
    assert any("without position" in i for i in F.validate_fluency(fl, result))


def test_validator_catches_a_repetition_from_text_alone(full):
    fl, result = copy.deepcopy(full[0]), full[1]
    r = next(o for o in fl["observations"] if o["type"] == "REPETITION")
    r["evidence"] = [e for e in r["evidence"] if e["kind"] == "lexical"]
    assert any("REPETITION without" in i for i in F.validate_fluency(fl, result))


def test_validator_catches_rates_from_decoded_sounds_or_when_unreliable(full):
    fl, result = copy.deepcopy(full[0]), full[1]
    decoded = len(result.engine_evidence["recognition"]["phones"])
    m = fl["metrics"]
    m["syllable_count"] = decoded
    m["speaking_rate"] = decoded / (m["speaking_interval_ms"] / 1000)
    m["articulation_rate"] = decoded / ((m["speaking_interval_ms"] - m["pause_total_ms"]) / 1000)
    assert any("expected vowel nuclei" in i for i in F.validate_fluency(fl, result))
    fl2 = copy.deepcopy(full[0])
    fl2["metrics"]["rate_available"] = False
    assert any("marked unavailable" in i for i in F.validate_fluency(fl2, result))


def test_validator_catches_duplicates_double_counting_and_hidden_padding(full):
    fl, result = copy.deepcopy(full[0]), full[1]
    r = copy.deepcopy(next(o for o in fl["observations"] if o["type"] == "REPETITION"))
    r["id"] = "fdup"
    fl["observations"].append(r)
    issues = F.validate_fluency(fl, result)
    assert any("duplicate observation" in i for i in issues) and any("claim the same moment" in i for i in issues)
    fl2 = copy.deepcopy(full[0])
    o = fl2["observations"][0]
    o["playback"]["play_ms"] = [max(0.0, o["start_ms"] - 900), o["end_ms"]]  # padding not stated
    assert any("context not stated exactly" in i for i in F.validate_fluency(fl2, result))
    fl3 = copy.deepcopy(full[0])
    del fl3["observations"][0]["playback"]["context_ms"]
    assert any("context not stated exactly" in i for i in F.validate_fluency(fl3, result))


def test_validator_catches_an_articulation_rate_from_unverified_pauses(full):
    fl, result = copy.deepcopy(full[0]), full[1]
    fl["metrics"]["activity_reliable"] = False
    assert any("without a reliable level estimate" in i for i in F.validate_fluency(fl, result))
    fl2 = copy.deepcopy(full[0])
    fl2["metrics"]["articulation_available"] = False
    assert any("not verified as silence" in i for i in F.validate_fluency(fl2, result))


def test_summary_names_long_pauses_as_such():
    fl, _, _ = run(A[:4] + [("p", 2500)] + A[4:])
    assert [p["label"] for p in of(fl, "PAUSE")] == ["Long pause"]
    assert "one long pause" in fl["summary"]["text"] and "hesitation pause" not in fl["summary"]["text"]


def test_validator_catches_overlapping_pauses_and_summary_scores(full):
    fl, result = copy.deepcopy(full[0]), full[1]
    p = copy.deepcopy(next(o for o in fl["observations"] if o["type"] == "PAUSE"))
    p["id"] = "fx"
    fl["observations"].append(p)
    assert any("overlapping pauses" in i for i in F.validate_fluency(fl, result))
    fl2 = copy.deepcopy(full[0])
    fl2["summary"]["text"] = "Fluency score 73/100"
    assert any("score" in i for i in F.validate_fluency(fl2, result))


# ----------------------------------------------------------------------
# Continued speech and engine comparison
# ----------------------------------------------------------------------

def test_describe_region_is_timing_only():
    result, audio, _ = M.build(A)
    d = F.describe_region(result, audio, M.SR, 1000.0, 2500.0, "overflow")
    assert d["kind"] == "overflow" and d["duration_ms"] == 1500.0 and d["decoded_sounds"] > 0
    assert d["playback"]["play_ms"] == [1000.0, 2500.0] and "no syllable rate" in d["note"]
    assert "syllable" not in json.dumps({k: v for k, v in d.items() if k != "note"})


def test_engine_comparison_keeps_single_engine_evidence_single():
    a, _, _ = run(A[:4] + [("p", 900)] + A[4:5] + [("w", 5), ("p", 300)] + A[5:], engine="wav2vec2_raw")
    b, _, _ = run(A[:4] + [("p", 900)] + A[4:], engine="openpronounce")
    cmp = F.compare_fluency(a, b)
    assert F.validate_fluency_comparison(cmp) == []
    agree = {r["type"]: r["agreement"] for r in cmp["rows"]}
    assert agree["PAUSE"] == "both_decoding_paths" and agree["REPETITION"] == "first_only"
    assert "not independent confirmation" in cmp["agreement_text"]["both_decoding_paths"]
    assert "acoustic model" in cmp["shared_model_note"]


@pytest.mark.parametrize("who", ["first_only", "second_only"])
def test_comparison_validator_catches_single_engine_presented_as_consensus(who):
    a, _, _ = run(A[:4] + [("p", 900)] + A[4:5] + [("w", 5), ("p", 300)] + A[5:], engine="openpronounce")
    b, _, _ = run(A[:4] + [("p", 900)] + A[4:5] + [("w", 5), ("p", 300)] + A[5:], engine="wav2vec2_raw")
    first, second = (a, {**b, "observations": [o for o in b["observations"] if o["type"] != "REPETITION"]}) \
        if who == "first_only" else ({**a, "observations": [o for o in a["observations"] if o["type"] != "REPETITION"]}, b)
    cmp = F.compare_fluency(first, second)
    row = next(r for r in cmp["rows"] if r["type"] == "REPETITION")
    assert row["agreement"] == who
    row["agreement"] = "both_decoding_paths"
    row["engines"] = cmp["engines"]
    assert any("presented as shown by both" in i for i in F.validate_fluency_comparison(cmp))
