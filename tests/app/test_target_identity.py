"""Target confirmation (tc-2) is sentence identity, not pronunciation scoring: a false-rejection matrix.

Decoded sounds are synthetic edits of the sentence's own eSpeak pronunciation, aligned by the engines'
aligner (m7helpers.make); alternatives are the other sentences of the same article, as in the reader.
"""

import random

import m7helpers as H7
import pytest

from pronunciation_lab.benchmark.analysis import is_vowel
from pronunciation_lab.reader import target as T
from pronunciation_lab.reader.status import attempt_status

ARTICLE = ["Think about the three things that you want to change.",
           "Very few people would value the view from this valley.",
           "The ship will leave the harbor before the evening begins.",
           "The world has changed significantly over the last six months."]
TEXT = ARTICLE[0]
ALTS = ARTICLE[1:]
UNRELATED = "Please call Stella and ask her to bring these things with her from the store."


def words_of(text):
    import re
    return [w.lower() for w in re.findall(r"[A-Za-z']+", text)]


def sounds(text):
    return [list(g) for g in T.sentence_sounds(text)]


def result_for(decoded_phones, text=TEXT, engine="wav2vec2_raw", step=80.0):
    words = list(zip(words_of(text), [" ".join(g) for g in sounds(text)]))
    decoded = [(p, 400 + k * step, 400 + k * step + 40) for k, p in enumerate(decoded_phones)]
    return H7.make(words, decoded, engine=engine, duration_ms=400 + len(decoded) * step + 500, text=text)


def flat(text):
    return [p for g in sounds(text) for p in g]


def state(phones, alts=ALTS, **kw):
    return T.confirm_target(result_for(phones, **kw), alts)


VOWEL_SHIFT = {"ɪ": "ɛ", "a": "ʌ", "e": "ɛ", "ʌ": "ɑ", "ə": "ʌ", "u": "ʊ", "i": "ɪ", "æ": "ɛ", "o": "ɔ", "ɑ": "ɔ", "ɛ": "æ"}


# ----------------------------------------------------------------------
# correct sentence, however it was pronounced: never MISMATCH
# ----------------------------------------------------------------------

def test_1_exact_clean_sentence_matches():
    tc = state(flat(TEXT))
    assert tc["state"] == "MATCH" and tc["identity"]["coverage"] == 1.0 and tc["version"] == "tc-2"


def test_2_accent_every_vowel_heard_differently():
    phones = [VOWEL_SHIFT.get(p, p) for p in flat(TEXT)]
    tc = state(phones)
    assert tc["state"] in ("MATCH", "LIKELY_MATCH"), tc["identity"]


def test_3_many_substitutions():
    rnd = random.Random(3)
    cons = ["t", "d", "k", "s", "n", "m", "l"]
    phones = [(rnd.choice(cons) if not is_vowel(p) and rnd.random() < 0.35 else p) for p in flat(TEXT)]
    assert state(phones)["state"] in ("MATCH", "LIKELY_MATCH")


def test_4_weak_sounds_omitted():
    phones = [p for p in flat(TEXT) if p not in ("ə", "ð", "t")]
    assert state(phones)["state"] in ("MATCH", "LIKELY_MATCH")


def test_4b_weak_function_words_dropped_entirely():
    groups = sounds(TEXT)
    kept = [g for w, g in zip(words_of(TEXT), groups) if w not in ("the", "you", "to")]  # weak forms elided
    tc = state([p for g in kept for p in g])
    assert tc["state"] == "MATCH" and tc["identity"]["coverage"] == 1.0, tc["identity"]


@pytest.mark.parametrize("step", [30.0, 400.0])
def test_5_6_fast_and_slow_reading(step):
    assert state(flat(TEXT), step=step)["state"] == "MATCH"  # identity does not use timing


def test_7_connected_speech():
    phones = ["ɾ" if p == "t" else p for p in flat(TEXT)]  # flapping, "want to" → "wanna"-like
    assert state(phones)["state"] == "MATCH"


def test_8_target_then_continuation_into_the_next_sentence():
    tc = state(flat(TEXT) + flat(ARTICLE[1]))
    assert tc["state"] == "MATCH", tc["identity"]


def test_8c_imperfect_sentence_then_a_cleanly_decoded_neighbour():
    # the continuation fits its own sentence better than the (accented) target fits ours: still not a mismatch,
    # because this sentence's words are there, in order
    rnd = random.Random(7)
    cons = {"θ": "t", "ð": "d", "z": "s", "ŋ": "n", "w": "v", "j": "dʒ", "tʃ": "ʃ"}
    accented = [VOWEL_SHIFT.get(p, cons.get(p, p) if rnd.random() < 0.6 else p) for p in flat(TEXT)]
    tc = state(accented + flat(ARTICLE[1]))
    assert tc["identity"]["contrast_margin"] <= T.CONTRAST_MISMATCH and tc["state"] == "MATCH", tc["identity"]


def test_8b_lead_in_before_the_sentence():
    assert state(flat(ARTICLE[3])[-8:] + flat(TEXT))["state"] == "MATCH"


def test_9_boundary_uncertain_is_not_a_mismatch():
    attempt = {"state": "ANALYZED", "user_disposition": None}
    job = {"state": "SUCCEEDED", "engine_id": "e", "target_confirmation": {"state": "MATCH"},
           "boundary": {"state": "BOUNDARY_UNCERTAIN", "feedback_withheld": True}}
    s = attempt_status(attempt, job, "e")
    assert s["identity"] == "MATCH" and s["feedback"] == "withheld_boundary"
    assert s["message"] == "Your reading appears to match this sentence, but I couldn't safely determine where it ended."
    assert s["summary"].startswith("sentence boundary uncertain") and s["preserved"] and not s["needs_decision"]


def test_14_noise_extra_sounds_inserted():
    rnd = random.Random(14)
    phones = []
    for p in flat(TEXT):
        phones.append(p)
        if rnd.random() < 0.3:
            phones.append(rnd.choice(["h", "ʔ", "ə", "s"]))
    assert state(phones)["state"] in ("MATCH", "LIKELY_MATCH")


def test_phone_inventory_differences_cost_nothing():
    # length marks, tone digits and diphthong/rhotic spellings differ between the engines' phone sets
    phones = [(p + "ː5" if is_vowel(p) else p) for p in flat(TEXT)]
    tc = state(phones, engine="openpronounce")
    assert tc["state"] == "MATCH" and tc["identity"]["fit"] == 1.0, tc["identity"]
    assert T.fold("eɪ") == T.fold("e") and T.fold("ɚ") == T.fold("ə") and T.fold("ɑː5") == "ɑ"


def test_pronunciation_tolerant_costs_keep_a_heavy_accent_identified():
    # every vowel and a third of the consonants heard differently: identity rests on order, not accuracy
    rnd = random.Random(7)
    cons = {"θ": "t", "ð": "d", "z": "s", "ŋ": "n", "w": "v", "j": "dʒ", "tʃ": "ʃ"}
    phones = [VOWEL_SHIFT.get(p, cons.get(p, p) if rnd.random() < 0.6 else p) for p in flat(TEXT)]
    tc = state(phones)
    assert tc["state"] == "MATCH", tc["identity"]
    assert tc["identity"]["fit"] >= 0.6


@pytest.mark.parametrize("text", ["Think about it now.", ARTICLE[3] + " " + ARTICLE[2]])
def test_sentence_length_does_not_decide(text):
    assert T.confirm_target(result_for(flat(text), text=text), ALTS)["state"] == "MATCH"


# ----------------------------------------------------------------------
# another sentence, nothing, unusable
# ----------------------------------------------------------------------

def test_10_clearly_wrong_sentence_of_the_article():
    tc = state(flat(ARTICLE[2]))
    assert tc["state"] == "MISMATCH" and tc["identity"]["closest_alternative"]["text"] == ARTICLE[2]


def test_10b_a_wrong_sentence_is_never_a_match_even_without_alternatives():
    assert state(flat(ARTICLE[2]), alts=[])["state"] == "AMBIGUOUS"  # no competitor known: no MISMATCH either


def test_11_completely_unrelated_sentence_is_never_confirmed():
    assert state(flat(UNRELATED))["state"] in ("AMBIGUOUS", "MISMATCH")


def test_12_silence():
    tc = state([])
    assert tc["state"] == "AMBIGUOUS" and tc["reason"] == "no speech sounds were decoded"


def test_13_too_short_recording_status():
    s = attempt_status({"state": "TOO_SHORT", "user_disposition": None}, None, "e")
    assert s["identity"] == "TOO_SHORT" and s["message"] == "Recording is too short to confirm the sentence."
    assert s["feedback"] == "none" and s["summary"] == "not analysed" and s["preserved"]


def test_partial_reading_is_ambiguous_never_hidden():
    tc = state(flat(TEXT)[: len(flat(TEXT)) // 2])
    assert tc["state"] == "AMBIGUOUS"


def test_very_short_sentence_keeps_phone_support():
    tc = T.confirm_target(result_for(flat("Go now."), text="Go now."), ALTS)
    assert tc["identity"] is None and "phone support" in tc["reason"]


# ----------------------------------------------------------------------
# two engines: disagreement, failures
# ----------------------------------------------------------------------

def test_15_engine_disagreement_turns_mismatch_into_ambiguous():
    a = {"state": "MISMATCH", "engine": "wav2vec2_raw", "identity": {}}
    b = {"state": "MATCH", "engine": "openpronounce", "identity": {}}
    c = T.combine_engines(a, b)
    assert c["state"] == "AMBIGUOUS" and c["primary_state"] == "MISMATCH"
    assert c["other_engine"]["state"] == "MATCH" and "not independent confirmation" in c["other_engine"]["note"]
    # agreement is recorded, not promoted to certainty
    same = T.combine_engines({"state": "MATCH", "engine": "x"}, {"state": "MATCH", "engine": "y"})
    assert same["state"] == "MATCH" and same["other_engine"]["state"] == "MATCH"


def test_16_one_engine_failure_does_not_reject():
    c = T.combine_engines({"state": "MATCH", "engine": "x"}, {"state": "NOT_APPLICABLE", "engine": "y"})
    assert c["state"] == "MATCH"
    r = result_for(flat(TEXT))
    r.status = "failed"
    assert T.confirm_target(r, ALTS)["state"] == "NOT_APPLICABLE"


def test_17_both_engines_failed():
    r = result_for(flat(TEXT))
    r.status = "failed"
    tc = T.confirm_target(r, ALTS)
    s = attempt_status({"state": "ANALYSIS_FAILED", "user_disposition": None}, None, "e")
    assert tc["state"] == "NOT_APPLICABLE" and s["identity"] == "FAILED" and s["message"] == "Recording could not be analysed."


# ----------------------------------------------------------------------
# determinism and evidence
# ----------------------------------------------------------------------

def test_identity_is_deterministic_and_explained():
    a, b = state(flat(TEXT)), state(flat(TEXT))
    assert a == b
    idn = a["identity"]
    for key in ("fit", "coverage", "order_margin", "shuffled_fit", "contrast_margin", "closest_alternative",
                "alternatives_compared"):
        assert key in idn
    assert a["evidence"]["support"] is not None  # tc-1 phone support still reported


def test_neighbours_are_the_article_around_the_sentence():
    texts = [f"s{i}" for i in range(10)]
    assert T.neighbours(texts, 0) == ["s1", "s2", "s3"]
    assert T.neighbours(texts, 5) == ["s2", "s3", "s4", "s6", "s7", "s8"]
