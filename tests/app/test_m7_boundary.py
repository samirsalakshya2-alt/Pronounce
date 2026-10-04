"""M7 sentence boundary: domain model, invariants and the conservative detector (synthetic attempts)."""

import io
import wave

import m7helpers as H
import numpy as np
import pytest

from pronunciation_lab.reader import boundary as B

SR = H.SR


def detect(decoded, speech, duration_ms, words=H.SENTENCE, **kw):
    r = H.make(words, decoded, duration_ms=duration_ms, **kw)
    b = B.detect_boundary(r, H.audio(duration_ms, speech), SR)
    assert B.validate_boundary(b) == [], b
    return b


def with_continuation(gap_ms, step_ms=80.0):
    d, end = H.sentence_decoded()
    c = H.heard(H.CONTINUATION, end + gap_ms, step_ms=step_ms, dur_ms=min(60.0, step_ms))
    dur = c[-1][2] + 400
    return d + c, [(300, end), (end + gap_ms, c[-1][2])], dur, end, c[0][1]


# ----------------------------------------------------------------------
# Domain model
# ----------------------------------------------------------------------

def test_states_and_region_kinds_are_fixed():
    assert B.STATES == ("TARGET_ONLY", "TARGET_PLUS_OVERFLOW", "BOUNDARY_UNCERTAIN", "NO_RELIABLE_BOUNDARY")
    assert B.REGION_KINDS == ("target", "overflow", "uncertain")
    assert B.NEEDS_TARGET_ANALYSIS == ("TARGET_PLUS_OVERFLOW", "BOUNDARY_UNCERTAIN")


def test_regions_tile_the_whole_attempt_without_gap_or_overlap():
    d, speech, dur, end, _ = with_continuation(400)
    b = detect(d, speech, dur)
    assert b["regions"][0] == b["regions"][0] | {"kind": "target", "start_ms": 0.0}
    assert b["regions"][0]["end_ms"] == b["regions"][1]["start_ms"] == b["cut_ms"]
    assert b["regions"][-1]["end_ms"] == b["duration_ms"] == pytest.approx(dur)
    assert sum(r["end_ms"] - r["start_ms"] for r in b["regions"]) == pytest.approx(dur)


def test_every_decision_has_reasons_evidence_and_thresholds():
    for case in (with_continuation(400), with_continuation(0)):
        b = detect(*case[:3])
        assert b["reasons"] and b["evidence"] and b["thresholds"] == B.THRESHOLDS and b["version"] == B.BOUNDARY_VERSION


def test_capture_mapping_is_exact_and_contiguous():
    d, speech, dur, _, _ = with_continuation(400)
    b = detect(d, speech, dur)
    n48 = int(round(dur * 48))
    cap = {"sample_rate": 48000, "start_sample": 1000, "end_sample": 1000 + n48}
    B.map_to_capture(b, cap)
    (a0, a1), (b0, b1) = (r["capture_samples"] for r in b["regions"])
    assert a0 == 1000 and a1 == b0 == 1000 + int(round(b["cut_ms"] * 48)) and b1 == 1000 + n48
    assert B.validate_boundary(b) == []


def test_capture_mapping_without_capture_metadata_is_left_out():
    d, speech, dur, _, _ = with_continuation(400)
    b = B.map_to_capture(detect(d, speech, dur), None)
    assert all("capture_samples" not in r for r in b["regions"])


@pytest.mark.parametrize("corrupt, message", [
    (lambda b: b.update(state="MAYBE"), "unknown boundary state"),
    (lambda b: b.update(reasons=[]), "without reasons"),
    (lambda b: b["regions"][1].update(start_ms=b["regions"][1]["start_ms"] + 10), "not contiguous"),
    (lambda b: b["regions"][1].update(end_ms=b["regions"][1]["end_ms"] - 10), "do not cover"),
    (lambda b: b["regions"][0].update(kind="overflow"), "first region must be the target"),
    (lambda b: b["regions"][1].update(kind="silence"), "unknown region kind"),
    (lambda b: b["regions"][1].update(kind="target"), "appears twice"),
    (lambda b: b.update(cut_ms=b["cut_ms"] + 5), "cut does not match"),
    (lambda b: b["evidence"].update(last_target_ms=b["cut_ms"] + 100), "ends before the sentence"),
    (lambda b: b.update(state="TARGET_ONLY"), "do not fit state"),
    (lambda b: b["regions"][0].update(end_ms=float("nan")), "invalid times"),
    (lambda b: (b["regions"][0].update(end_ms=0.0), b["regions"][1].update(start_ms=0.0), b.update(cut_ms=0.0)),
     "target region is empty"),
])
def test_validator_catches_corruption(corrupt, message):
    d, speech, dur, _, _ = with_continuation(400)
    b = detect(d, speech, dur)
    corrupt(b)
    assert any(message in i for i in B.validate_boundary(b)), B.validate_boundary(b)


def test_validator_catches_capture_mapping_corruption():
    d, speech, dur, _, _ = with_continuation(400)
    b = B.map_to_capture(detect(d, speech, dur), {"sample_rate": 48000, "start_sample": 0, "end_sample": int(dur * 48)})
    b["regions"][1]["capture_samples"][0] += 5
    assert any("capture sample ranges are not contiguous" in i for i in B.validate_boundary(b))
    b["regions"][1]["capture_samples"] = None
    assert any("missing or reversed" in i for i in B.validate_boundary(b))


def test_target_only_must_keep_the_whole_attempt():
    d, end = H.sentence_decoded()
    b = detect(d, [(300, end)], end + 600)
    b["regions"][0]["end_ms"] -= 100
    b["cut_ms"] -= 100
    b["regions"].append({"kind": "uncertain", "start_ms": b["cut_ms"], "end_ms": b["duration_ms"]})
    assert B.validate_boundary(b)


# ----------------------------------------------------------------------
# Detector scenarios (Phase 3)
# ----------------------------------------------------------------------

def test_target_only():
    d, end = H.sentence_decoded()
    b = detect(d, [(300, end)], end + 300)
    assert b["state"] == "TARGET_ONLY" and b["cut_ms"] == b["duration_ms"]
    assert [r["kind"] for r in b["regions"]] == ["target"]


def test_short_trailing_silence():
    d, end = H.sentence_decoded()
    assert detect(d, [(300, end)], end + 150)["state"] == "TARGET_ONLY"


def test_long_trailing_silence_is_not_overflow():
    d, end = H.sentence_decoded()
    b = detect(d, [(300, end)], end + 8000)
    assert b["state"] == "TARGET_ONLY" and b["regions"] == [b["regions"][0]]


def test_digital_silence_padding_does_not_lower_the_noise_floor():
    d, end = H.sentence_decoded()
    r = H.make(H.SENTENCE, d, duration_ms=end + 3000)
    x = H.audio(end + 3000, [(300, end)])
    x[int((end + 200) * SR / 1000):] = 0.0  # exact zeros after the sentence (muted input)
    b = B.detect_boundary(r, x, SR)
    assert b["state"] == "TARGET_ONLY" and b["evidence"]["noise_floor_db"] > B.DIGITAL_SILENCE_DBFS


def test_continuation_after_a_pause():
    d, speech, dur, end, first = with_continuation(600)
    b = detect(d, speech, dur)
    assert b["state"] == "TARGET_PLUS_OVERFLOW"
    assert end <= b["cut_ms"] <= first
    assert [r["kind"] for r in b["regions"]] == ["target", "overflow"]
    assert b["evidence"]["post_phones"] == len(H.CONTINUATION.split())


def test_immediate_continuation_is_detected_with_an_approximate_cut():
    d, speech, dur, end, first = with_continuation(20)
    b = detect(d, speech, dur)
    assert b["state"] in B.NEEDS_TARGET_ANALYSIS
    assert end <= b["cut_ms"] <= first
    assert any("approximate" in r for r in b["reasons"])


def test_fast_continuation():
    d, speech, dur, end, first = with_continuation(60, step_ms=50.0)
    b = detect(d, speech, dur)
    assert b["state"] in B.NEEDS_TARGET_ANALYSIS and end <= b["cut_ms"] <= first


def test_continuation_never_lies_inside_the_target_region():
    for gap in (20, 150, 400, 1000):
        d, speech, dur, end, first = with_continuation(gap)
        b = detect(d, speech, dur)
        assert b["evidence"]["last_target_ms"] <= b["cut_ms"] <= first, gap


def test_whole_recording_alignment_stretching_into_the_continuation_is_not_trusted():
    # the continuation ends in "...ɪ n dʒ" so the whole-recording alignment places "change"'s last sounds there
    d, end = H.sentence_decoded()
    c = H.heard("ð ə n ɛ k s t eɪ n dʒ", end + 400)
    r = H.make(H.SENTENCE, d + c, duration_ms=c[-1][2] + 300)
    engine_end = max(p.timing.end_ms for w in r.words for p in w.phonemes if p.timing.end_ms is not None)
    assert engine_end > end + 400  # the engine's own alignment stretched
    b = B.detect_boundary(r, H.audio(c[-1][2] + 300, [(300, end), (end + 400, c[-1][2])]), SR)
    assert b["state"] in B.NEEDS_TARGET_ANALYSIS and b["cut_ms"] < end + 400
    assert b["evidence"]["engine_alignment_stretched"] and any("analysed again" in x for x in b["reasons"])


def test_partly_decoded_final_word_before_a_clear_pause_is_a_found_boundary():
    # M7 boundary-confidence correction: the pause and the continued speech establish the end; the final
    # word's missing sounds lie before the pause and are a pronunciation observation, not an unknown boundary
    d, end = H.sentence_decoded()
    d = d[:-2]  # "change" only partly decoded ("tʃ eɪ"), then a pause, then the reader goes on
    c = H.heard(H.CONTINUATION, d[-1][2] + 300)
    b = detect(d + c, [(300, d[-1][2]), (c[0][1], c[-1][2])], c[-1][2] + 300)
    assert b["state"] == "TARGET_PLUS_OVERFLOW" and b["boundary_confidence"] == "supported"
    assert [r["kind"] for r in b["regions"]] == ["target", "overflow"]
    assert any("pronunciation observation" in r for r in b["reasons"])
    assert d[-1][2] <= b["cut_ms"] <= c[0][1]


def test_without_a_pause_local_support_is_never_claimed():
    d, end = H.sentence_decoded()
    d = d[:-2]  # "change" only partly decoded, then the next sentence straight away: no pause marks the end
    c = H.heard(H.CONTINUATION, d[-1][2] + 80)
    b = detect(d + c, [(300, c[-1][2])], c[-1][2] + 300)
    support = b["evidence"]["boundary_support"]
    assert not support["supported"] and "no pause before the continued speech" in support["failed"]
    assert b["boundary_confidence"] == "clear_recording" and not b["relied_on_local_support"]
    assert any("approximate" in r for r in b["reasons"])


def test_alignment_suspect_insertions_inside_the_final_word_make_it_uncertain():
    d, end = H.sentence_decoded()
    # "tʃ eɪ" + 2 unexpected sounds + "n dʒ", then continuation
    head = d[:-2]
    t = head[-1][2] + 20
    mid = H.heard("ʌ m", t)
    tail = H.heard("n dʒ", mid[-1][2] + 20)
    c = H.heard(H.CONTINUATION, tail[-1][2] + 400)
    dec = head + mid + tail + c
    b = detect(dec, [(300, tail[-1][2]), (c[0][1], c[-1][2])], c[-1][2] + 300)
    assert b["state"] == "BOUNDARY_UNCERTAIN" and any("inside the final word" in r for r in b["reasons"])


def test_final_word_split_by_a_pause_is_uncertain():
    d, end = H.sentence_decoded()
    head, tail = d[:-1], [("dʒ", d[-1][1] + 600, d[-1][2] + 600)]
    c = H.heard(H.CONTINUATION, tail[0][2] + 400)
    b = detect(head + tail + c, [(300, head[-1][2]), (tail[0][1], tail[0][2]), (c[0][1], c[-1][2])], c[-1][2] + 300)
    assert b["state"] == "BOUNDARY_UNCERTAIN" and b["evidence"]["final_word_split_by_pause"]


def test_repeated_final_word_inside_the_sentence_is_kept_in_the_target():
    # "... chan- change" (a restart of the final word): the later one ends the sentence
    d, end = H.sentence_decoded()
    again = H.heard("tʃ eɪ n dʒ", end + 200)
    b = detect(d[:-2] + [("n", d[-2][1], d[-2][2])] + again, [(300, again[-1][2])], again[-1][2] + 400)
    assert b["state"] == "TARGET_ONLY"
    d2 = d + again
    b = detect(d2, [(300, again[-1][2])], again[-1][2] + 400)
    assert b["state"] == "TARGET_ONLY" and b["cut_ms"] == b["duration_ms"]


def test_repeated_final_word_followed_by_continuation_keeps_the_later_word_and_is_uncertain():
    d, end = H.sentence_decoded()
    again = H.heard("tʃ eɪ n dʒ", end + 200)
    c = H.heard(H.CONTINUATION, again[-1][2] + 500)
    b = detect(d + again + c, [(300, again[-1][2]), (c[0][1], c[-1][2])], c[-1][2] + 300)
    assert b["state"] == "BOUNDARY_UNCERTAIN" and b["evidence"]["final_word_repeated"]
    assert again[-1][2] <= b["cut_ms"] <= c[0][1]


# --- false-positive protection -----------------------------------------

def test_breath_after_the_sentence_is_not_overflow():
    d, end = H.sentence_decoded()
    breath = H.heard("h h", end + 400)  # one or two decoded sounds
    b = detect(d + breath, [(300, end), (end + 400, end + 700)], end + 1200)
    assert b["state"] == "TARGET_ONLY"


def test_decoded_sounds_without_speech_energy_are_not_overflow():
    d, end = H.sentence_decoded()
    junk = H.heard("ə h ə h", end + 300)  # decoder noise over near-silence
    b = detect(d + junk, [(300, end), (end + 300, end + 500)], end + 1200)
    assert b["state"] == "TARGET_ONLY" and "speech-like" in b["reasons"][0]


def test_weak_final_consonant_not_detected_is_not_a_boundary():
    d, end = H.sentence_decoded()
    b = detect(d[:-1], [(300, end)], end + 600)  # final /dʒ/ not detected
    assert b["state"] == "TARGET_ONLY"


def test_trailing_vowel_is_not_overflow():
    d, end = H.sentence_decoded()
    b = detect(d + [("ə", end + 20, end + 140)], [(300, end + 140)], end + 600)
    assert b["state"] == "TARGET_ONLY"


def test_short_pause_inside_the_sentence_is_not_a_boundary():
    words = H.SENTENCE
    d = H.heard("θ ɪ ŋ k ə b aʊ t", 300)
    d += H.heard("tʃ eɪ n dʒ", d[-1][2] + 450)  # a 450 ms pause before the final word
    b = detect(d, [(300, d[7][2]), (d[8][1], d[-1][2])], d[-1][2] + 500, words=words)
    assert b["state"] == "TARGET_ONLY"


def test_substituted_sounds_accent_are_not_overflow():
    d, end = H.sentence_decoded()
    accent = [(("t" if p == "θ" else "s" if p == "z" else "ɛ" if p == "eɪ" else p), a, b) for p, a, b in d]
    assert detect(accent, [(300, end)], end + 600)["state"] == "TARGET_ONLY"


def test_long_undecoded_speech_after_the_sentence_is_uncertain_not_ignored():
    d, end = H.sentence_decoded()
    b = detect(d, [(300, end), (end + 300, end + 1800)], end + 2200)  # speech energy, nothing decoded
    assert b["state"] == "BOUNDARY_UNCERTAIN" and [r["kind"] for r in b["regions"]] == ["target", "uncertain"]


# --- no reliable boundary ----------------------------------------------

def test_failed_result_has_no_reliable_boundary():
    d, end = H.sentence_decoded()
    b = detect(d, [(300, end)], end + 600, status="failed")
    assert b["state"] == "NO_RELIABLE_BOUNDARY" and b["cut_ms"] == b["duration_ms"]


@pytest.mark.parametrize("damage", ["no_spans", "no_clock", "length_mismatch", "nan", "reversed", "outside", "garbage"])
def test_missing_or_malformed_timing_has_no_reliable_boundary(damage):
    d, speech, dur, _, _ = with_continuation(400)
    r = H.make(H.SENTENCE, d, duration_ms=dur)
    rec = r.engine_evidence["recognition"]
    if damage == "no_spans":
        del rec["frame_spans"]
    elif damage == "no_clock":
        del r.engine_evidence["frame_clock"]
    elif damage == "length_mismatch":
        rec["frame_spans"] = rec["frame_spans"][:-1]
    elif damage == "nan":
        rec["frame_spans"][3] = (float("nan"), 4)
    elif damage == "reversed":
        rec["frame_spans"][3] = (rec["frame_spans"][3][1] + 5, rec["frame_spans"][3][0])
    elif damage == "outside":
        rec["frame_spans"][-1] = (rec["frame_spans"][-1][0], rec["frame_spans"][-1][0] + 100000)
    elif damage == "garbage":
        rec["frame_spans"][2] = "x"
    b = B.detect_boundary(r, H.audio(dur, speech), SR)
    assert b["state"] == "NO_RELIABLE_BOUNDARY" and [x["kind"] for x in b["regions"]] == ["target"]
    assert B.validate_boundary(b) == []


def test_nothing_decoded_has_no_reliable_boundary():
    b = detect([], [], 2000)
    assert b["state"] == "NO_RELIABLE_BOUNDARY"


def test_too_short_audio_has_no_reliable_boundary():
    d, end = H.sentence_decoded()
    r = H.make(H.SENTENCE, d, duration_ms=end)
    b = B.detect_boundary(r, np.zeros(100, np.float32), SR)
    assert b["state"] == "NO_RELIABLE_BOUNDARY"


# ----------------------------------------------------------------------
# End-free alignment
# ----------------------------------------------------------------------

def test_end_free_alignment_ignores_trailing_sounds():
    k, pairs = B.end_free_alignment(list("abcd"), list("abcdxyzw"))
    assert k == 4 and pairs == [(0, 0), (1, 1), (2, 2), (3, 3)]


def test_end_free_alignment_keeps_substitutions_and_omissions_inside():
    k, pairs = B.end_free_alignment(list("abcd"), list("abxdqq"))
    assert k == 4 and (2, 2) in pairs
    k, pairs = B.end_free_alignment(list("abcd"), list("abd"))
    assert k == 3 and (2, None) in pairs


def test_end_free_alignment_prefers_the_later_of_equal_ends_but_not_across_a_pause():
    assert B.end_free_alignment(list("ab"), list("ab"))[0] == 2
    # "abcd" said as "ab", then "xz...": deleting c,d costs the same as substituting x,z for them
    assert B.end_free_alignment(list("abcd"), list("abxzqw"))[0] == 4
    assert B.end_free_alignment(list("abcd"), list("abxzqw"), pause_before={2})[0] == 2
    # a tied later end must stand for the sentence's sounds credibly: consonant /s/ "heard as" the diphthong
    # that begins the continuation ("…months. I would…") is not the sentence's /s/
    assert B.end_free_alignment(["m", "ʌ", "n", "θ", "s"], ["m", "ʌ", "n", "θ", "aɪ", "w", "uː", "d"])[0] == 4
    assert B.end_free_alignment(["m", "ʌ", "n", "θ", "s"], ["m", "ʌ", "n", "θ", "z", "w", "uː", "d"])[0] == 5
    assert B.same_class("s", "z") and B.same_class("ɪ", "iː") and B.same_class("ɔːɹ", "ɚ") and not B.same_class("s", "aɪ")
    assert B.end_free_alignment([], list("xy"))[0] == 0


def test_ends_with():
    assert B.ends_with(list("qqabcd"), list("abcd"))
    assert B.ends_with(list("qqabxd"), list("abcd")) is False  # 3/4 < 0.8
    assert B.ends_with(list("abcdqqq"), list("abcd")) is False
    assert B.ends_with([], list("ab")) is False
    assert B.ends_with(list("abcdx"), list("abcd")) is False  # anchored: the last sound must be the word's
    assert B.ends_with(list("abcdz"), list("abcde")) is True  # a substituted last sound still ends it (4/5)


# ----------------------------------------------------------------------
# Target region audio, consistency check, engine comparison
# ----------------------------------------------------------------------

def _wav(n, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.arange(n) % 30000).astype("<i2").tobytes())
    return buf.getvalue()


def test_target_wav_is_a_sample_exact_prefix():
    data = _wav(32000)
    out, n = B.target_wav_bytes(data, 1234.5)
    assert n == round(1234.5 * 16)
    with wave.open(io.BytesIO(out)) as w, wave.open(io.BytesIO(data)) as o:
        assert w.getparams()[:3] == o.getparams()[:3] and w.getnframes() == n
        assert w.readframes(n) == o.readframes(n)


@pytest.mark.parametrize("cut", [0.0, -5.0, 2001.0])
def test_target_wav_rejects_an_impossible_region(cut):
    with pytest.raises(ValueError):
        B.target_wav_bytes(_wav(32000), cut)


def test_consistency_check_keeps_a_clean_target():
    d, speech, dur, end, _ = with_continuation(600)
    b = detect(d, speech, dur)
    n = int(round(b["cut_ms"] * SR / 1000))
    target = H.make(H.SENTENCE, H.sentence_decoded()[0], duration_ms=b["cut_ms"])
    out = B.check_target_analysis(b, target, H.audio(dur, speech)[:n], SR)
    assert out["state"] == "TARGET_PLUS_OVERFLOW" and out["target_check"]["issues"] == []


def test_consistency_check_downgrades_when_the_final_word_loses_sounds():
    d, speech, dur, end, _ = with_continuation(600)
    b = detect(d, speech, dur)
    n = int(round(b["cut_ms"] * SR / 1000))
    target = H.make(H.SENTENCE, H.sentence_decoded()[0][:-1], duration_ms=b["cut_ms"])
    out = B.check_target_analysis(b, target, H.audio(dur, speech)[:n], SR)
    assert out["state"] == "BOUNDARY_UNCERTAIN" and [r["kind"] for r in out["regions"]] == ["target", "uncertain"]
    assert B.validate_boundary(out) == []


def test_consistency_check_downgrades_when_the_target_still_has_continued_speech():
    d, speech, dur, end, _ = with_continuation(600)
    b = detect(d, speech, dur)
    still = H.make(H.SENTENCE, d, duration_ms=dur)  # as if the cut had not removed the continuation
    out = B.check_target_analysis(b, still, H.audio(dur, speech), SR)
    assert out["state"] == "BOUNDARY_UNCERTAIN"


def test_engine_comparison_is_evidence_never_a_merge():
    d, speech, dur, _, _ = with_continuation(600)
    a = detect(d, speech, dur, engine="wav2vec2_raw")
    same = B.compare_boundaries(a, detect(d, speech, dur, engine="openpronounce"))
    assert same["differs"] is False and "share one acoustic model" in same["note"]
    other = detect(H.sentence_decoded()[0], speech[:1], dur, engine="openpronounce")
    diff = B.compare_boundaries(a, other)
    assert diff["differs"] is True and diff["engine"] == "openpronounce" and diff["state"] == "TARGET_ONLY"
    assert a["state"] == "TARGET_PLUS_OVERFLOW"  # unchanged


# ----------------------------------------------------------------------
# M7 manual-test failure: speech-dense / noisy recording, "…underresourced" + sentence 3
# ----------------------------------------------------------------------

def _dense(**kw):
    s, c = H.dense_decoded()
    r = H.make(H.DENSE_SENTENCE, s + c, duration_ms=H.DENSE_DURATION_MS)
    return B.detect_boundary(r, H.dense_audio(**kw), SR), s, c


def test_speech_dense_continuation_is_never_target_only():
    b, s, c = _dense()
    assert B.validate_boundary(b) == []
    assert b["state"] == "BOUNDARY_UNCERTAIN", b["reasons"]  # was TARGET_ONLY before the fix
    assert b["evidence"]["activity_reliable"] is False and b["evidence"]["activity_coverage"] < B.MIN_ACTIVITY_COVERAGE
    assert b["evidence"]["post_phones"] == len(H.DENSE_CONTINUATION.split())
    assert s[-1][2] <= b["cut_ms"] <= c[0][1]  # after "…underresourced", before sentence 3
    assert [r["kind"] for r in b["regions"]] == ["target", "uncertain"]
    assert any("could not be measured reliably" in x for x in b["reasons"])


def test_the_failure_mode_reproduces_the_measured_activity_estimate():
    act = B.speech_activity(H.dense_audio(), SR)
    assert -38 < act["floor_db"] < -34  # the real attempt: -36.0 dBFS
    assert act["active"].sum() * act["hop_ms"] < 2000  # the speech itself looks inactive


def test_speech_dense_sentence_without_continuation_stays_target_only():
    s, _ = H.dense_decoded()
    r = H.make(H.DENSE_SENTENCE, s, duration_ms=H.DENSE_DURATION_MS)
    b = B.detect_boundary(r, H.dense_audio(cont=False), SR)
    assert b["state"] == "TARGET_ONLY" and "not measurable" in b["reasons"][0]


@pytest.mark.parametrize("noise_db", [-80.0, -60.0, -45.0, -36.0, -30.0])
@pytest.mark.parametrize("cont_db", [-50.0, -40.0, -27.0, -15.0])
def test_strong_decoded_continuation_is_never_vetoed_by_speech_energy(noise_db, cont_db):
    """Invariant: credible decoded continuation can never become TARGET_ONLY, whatever the levels."""
    s, c = H.dense_decoded()
    r = H.make(H.DENSE_SENTENCE, s + c, duration_ms=H.DENSE_DURATION_MS)
    b = B.detect_boundary(r, H.dense_audio(cont_db=cont_db, noise_db=noise_db), SR)
    assert b["state"] in B.NEEDS_TARGET_ANALYSIS, (noise_db, cont_db, b["reasons"])
    assert s[-1][2] <= b["cut_ms"] <= c[0][1]


def test_reliable_but_silent_energy_with_strong_decoded_continuation_is_uncertain():
    d, end = H.sentence_decoded()
    c = H.heard(H.CONTINUATION, end + 400)  # 10 decoded sounds over silence
    b = detect(d + c, [(300, end)], c[-1][2] + 300)
    assert b["evidence"]["activity_reliable"] and b["evidence"]["speech_after_ms"] < B.MIN_OVERFLOW_SPEECH_MS
    assert b["state"] == "BOUNDARY_UNCERTAIN" and "only" in b["reasons"][0]


def test_unreliable_energy_with_a_short_decoded_tail_is_uncertain_not_target_only():
    s, _ = H.dense_decoded()
    tail = H.heard("b ʌ t m", H.DENSE_CONT_MS, step_ms=140.0, dur_ms=20.0)
    r = H.make(H.DENSE_SENTENCE, s + tail, duration_ms=H.DENSE_DURATION_MS)
    b = B.detect_boundary(r, H.dense_audio(), SR)
    assert b["state"] == "BOUNDARY_UNCERTAIN"


def test_activity_coverage():
    x = H.audio(2000, [(500, 1000)])
    act = B.speech_activity(x, SR)
    assert B.activity_coverage(act, [(600, 620), (800, 820)]) == 1.0
    assert B.activity_coverage(act, [(1500, 1520)]) == 0.0
    assert B.activity_coverage(act, []) == 0.0


# --- safety: plausible continuation and containment ----------------------

def test_continuation_plausible_from_timing_free_evidence():
    d, end = H.sentence_decoded()
    clean = H.make(H.SENTENCE, d)
    assert B.continuation_plausible(clean) == []
    stretched = H.make(H.SENTENCE, d + H.heard(H.CONTINUATION, end + 400))  # whole-recording alignment
    assert any("extra sounds" in r for r in B.continuation_plausible(stretched))
    assert B.continuation_plausible(clean, "TARGET_PLUS_OVERFLOW")
    assert B.continuation_plausible(clean, "TARGET_ONLY") == []


def test_evidence_outside_target_finds_every_kind_of_leak():
    d, end = H.sentence_decoded()
    r = H.make(H.SENTENCE, d)
    assert B.evidence_outside_target({}, r, end) == []
    assert B.evidence_outside_target({}, r, end - 100)  # phone timings past the end
    view = {"words": [{"word": "w", "play_ms": [0, 50], "sounds": [{"expected": "a", "span_ms": [0, 10], "play_ms": [0, 2000]}]}],
            "coach": {"observations": [{"id": "o1", "span_ms": [0, 10], "play_ms": [0, 10], "word_play_ms": [0, 1500]}]},
            "reduction": {"candidates": [{"id": "c1", "where": {"span_ms": [1200, 1300]}}]}}
    issues = B.evidence_outside_target(view, None, 1000)
    assert len(issues) == 3 and any("M4" in i for i in issues) and any("M5" in i for i in issues)
    assert B.evidence_outside_target(view, None, 3000) == []
    r2 = H.make(H.SENTENCE, d + H.heard(H.CONTINUATION, end + 400))
    assert any("extra sound" in i for i in B.evidence_outside_target({}, r2, end + 100))


def _garbled(decoded, every=2):
    """The sentence decoded unrecognisably (heavy noise): every other sound heard as something else."""
    return [(("ʔ" if i % every == 0 else p), a, b) for i, (p, a, b) in enumerate(decoded)]


def test_undefensible_boundary_when_the_sentence_is_decoded_too_unclearly():
    d, end = H.sentence_decoded()
    c = H.heard(H.CONTINUATION, end + 400)
    b = detect(_garbled(d, every=1) + c, [(300, end), (end + 400, c[-1][2])], c[-1][2] + 300)
    assert b["evidence"]["alignment_cost_per_sound"] > B.MAX_DEFENSIBLE_COST
    assert b["state"] == "BOUNDARY_UNCERTAIN" and b["defensible"] is False
    assert any("too unclearly" in r for r in b["reasons"])


def test_moderately_unclear_sentence_keeps_a_defensible_boundary():
    d, end = H.sentence_decoded()
    c = H.heard(H.CONTINUATION, end + 400)
    b = detect(_garbled(d, every=4) + c, [(300, end), (end + 400, c[-1][2])], c[-1][2] + 300)
    assert b["evidence"]["alignment_cost_per_sound"] <= B.MAX_DEFENSIBLE_COST and b["defensible"] is True
    assert b["state"] in B.NEEDS_TARGET_ANALYSIS


def test_unclear_sentence_without_continuation_is_not_affected():
    d, end = H.sentence_decoded()
    b = detect(_garbled(d, every=1), [(300, end)], end + 600)
    assert b["state"] == "TARGET_ONLY"


# ----------------------------------------------------------------------
# Boundary confidence (local) vs analysis confidence (after isolation)
# ----------------------------------------------------------------------

LONG = [("the", "ð ə"), ("second", "s ɛ k ə n d"), ("mistake", "m ɪ s t eɪ k"), ("is", "ɪ z"),
        ("believing", "b ɪ l iː v ɪ ŋ"), ("that", "ð æ t"), ("collaborating", "k ə l æ b ə ɹ eɪ t ɪ ŋ"),
        ("is", "ɪ z"), ("an", "æ n"), ("act", "æ k t"), ("of", "ʌ v"), ("goodwill", "ɡ ʊ d w ɪ l")]


def long_decoded():
    d = H.heard(" ".join(p for _, p in LONG), 300)
    return d, d[-1][2]


def _garble_start(decoded, share=0.75):
    """The first `share` of the sentence decoded as nothing like it (the whole recording decoded poorly)."""
    k = int(len(decoded) * share)
    return [("ʔ", a, b) for _, a, b in decoded[:k]] + decoded[k:]


def poorly_decoded_with_pause(gap_ms=1000.0, last_sound=True, noisy=False, garble_end=False, pause_sound=False):
    """A long sentence decoded poorly as a whole (its start garbled) but with a clear end, then a pause."""
    d, end = long_decoded()
    d = _garble_start(d)
    if garble_end:
        d = [("ʔ", a, b) for _, a, b in d]
    if not last_sound:
        d = d[:-1]                                   # the final /dʒ/ not decoded before the pause
    last = d[-1][2]
    c = H.heard(H.CONTINUATION, last + gap_ms)
    speech = [(300, last), (c[0][1], c[-1][2])] + ([(last + 300, last + gap_ms - 300)] if pause_sound else [])
    dur = c[-1][2] + 400
    r = H.make(LONG, d + c, duration_ms=dur)
    x = H.audio(dur, speech, noise_db=-24.0 if noisy else -65.0)
    b = B.detect_boundary(r, x, SR)
    assert B.validate_boundary(b) == []
    return b, last, c[0][1]


def test_poor_whole_decoding_with_a_clear_pause_is_a_supported_boundary():
    b, last, first = poorly_decoded_with_pause()
    assert b["evidence"]["alignment_cost_per_sound"] > B.MAX_DEFENSIBLE_COST   # the old rule would withhold
    assert b["boundary_confidence"] == "supported" and b["relied_on_local_support"] and b["defensible"]
    assert b["state"] == "TARGET_PLUS_OVERFLOW" and last <= b["cut_ms"] <= first
    assert any("rests on the pause" in r for r in b["reasons"])


def test_missing_final_sound_before_a_clear_pause_is_a_pronunciation_observation():
    b, last, first = poorly_decoded_with_pause(last_sound=False)
    assert b["evidence"]["final_word_decoded"] == "5/6" and not b["evidence"]["final_word_complete"]
    assert b["state"] == "TARGET_PLUS_OVERFLOW" and b["boundary_confidence"] == "supported"
    assert any("pronunciation observation, not an unknown boundary" in r for r in b["reasons"])


@pytest.mark.parametrize("kw, failure", [
    ({"noisy": True}, "speech level not measurable reliably"),            # heavy noise: no local support
    ({"gap_ms": 150.0}, "no pause before the continued speech"),
    ({"garble_end": True}, "the end of the sentence was decoded unclearly"),
    ({"pause_sound": True}, "the pause is not silent"),
])
def test_without_local_evidence_the_whole_recording_rule_still_protects(kw, failure):
    b, last, first = poorly_decoded_with_pause(**kw)
    assert failure in b["evidence"]["boundary_support"]["failed"]
    assert b["boundary_confidence"] == "insufficient" and not b["defensible"] and b["state"] == "BOUNDARY_UNCERTAIN"
    assert any("no clear pause marks it" in r or "no clear pause marks the end" in r for r in b["reasons"])


def test_support_is_revoked_when_the_cut_is_not_silent(monkeypatch):
    monkeypatch.setattr(B, "_quietest_ms", lambda act, lo, hi: lo)  # force the cut to the edge of the pause
    d, end = long_decoded()
    d = _garble_start(d)
    last = d[-1][2]
    c = H.heard(H.CONTINUATION, last + 1000)
    dur = c[-1][2] + 400
    x = H.audio(dur, [(300, last + 100), (c[0][1], c[-1][2])])  # speech-like sound right where the cut is forced
    b = B.detect_boundary(H.make(LONG, d + c, duration_ms=dur), x, SR)
    assert "the cut does not fall on a silent frame" in b["evidence"]["boundary_support"]["failed"]
    assert not b["defensible"] and b["state"] == "BOUNDARY_UNCERTAIN"


def test_a_boundary_found_only_through_the_pause_is_reverted_when_the_cut_is_not_silent(monkeypatch):
    # clearly decoded, final word partly decoded: only the pause made it a found boundary; without a silent cut
    # the end is approximate again (still defensible: the recording itself was decoded clearly)
    monkeypatch.setattr(B, "_quietest_ms", lambda act, lo, hi: lo)
    d, end = H.sentence_decoded()
    d = d[:-2]
    c = H.heard(H.CONTINUATION, d[-1][2] + 600)
    b = detect(d + c, [(300, d[-1][2] + 80), (c[0][1], c[-1][2])], c[-1][2] + 300)
    assert "the cut does not fall on a silent frame" in b["evidence"]["boundary_support"]["failed"]
    assert b["defensible"] and b["boundary_confidence"] == "clear_recording"
    assert b["state"] == "BOUNDARY_UNCERTAIN" and [r["kind"] for r in b["regions"]] == ["target", "uncertain"]


def test_clear_recordings_are_unchanged_by_the_correction():
    d, speech, dur, end, first = with_continuation(600)
    b = detect(d, speech, dur)
    assert b["state"] == "TARGET_PLUS_OVERFLOW" and b["defensible"] and not b["relied_on_local_support"]
    assert b["boundary_confidence"] in ("supported", "clear_recording")


def test_analysis_confidence_is_measured_on_the_result_it_is_given():
    d, end = long_decoded()
    clear = B.analysis_confidence(H.make(LONG, d))
    poor = B.analysis_confidence(H.make(LONG, _garble_start(d)))
    assert clear["state"] == "ok" and clear["cost_per_sound"] == 0.0
    assert poor["state"] == "low_confidence" and poor["cost_per_sound"] > B.MAX_DEFENSIBLE_COST
    failed = H.make(LONG, d, status="failed")
    assert B.analysis_confidence(failed)["state"] == "unavailable"


def test_engine_disagreement_on_defensibility_is_visible():
    a, _, _ = poorly_decoded_with_pause()
    other = dict(a, defensible=False, boundary_confidence="insufficient")
    cmp = B.compare_boundaries(a, other)
    assert cmp["differs"] and cmp["defensible"] is False and cmp["boundary_confidence"] == "insufficient"
    assert B.compare_boundaries(a, dict(a))["differs"] is False
