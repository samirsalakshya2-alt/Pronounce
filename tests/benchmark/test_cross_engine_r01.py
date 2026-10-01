"""Cross-checks between OpenPronounce and raw Wav2Vec2 on R01.

Both run the same checkpoint, so this file pins two things:

* what is SHARED (and therefore is not independent corroboration): the frame
  posteriors and the frame spans of every phone the decoders agree on;
* where they DISAGREE, and that each disagreement is kept as each engine saw it,
  never reconciled.

The R01 disagreements (inspected 2026-10-02):

    word   expected (op / raw)   OpenPronounce          raw
    about  ə / ɐ                 match                  match      (inventory only)
    three  i / iː                match i                SUBSTITUTION iː -> i
    you    u / uː                match                  match      (inventory only)
    want   t                     OMISSION               match, frames 286-287
    to     t                     match, frames 286-309  match, frames 307-309

The want/to case is an OpenPronounce normalization artifact: "w ʌ n t | t ʊ"
has two /t/ runs, which OpenPronounce's repeat-collapse merges into one phone
spanning frames 286-309 (a 460 ms "span" across a pause) and assigns to "to".
The raw decoder shows /t/ was heard at the end of "want".
"""

import numpy as np
import pytest


def _word(result, text):
    return next(w for w in result.words if w.word == text)


def _spans(result):
    return [tuple(s) for s in result.engine_evidence["recognition"]["frame_spans"]]


def test_same_recording_same_clock(openpronounce_r01, raw_r01):
    op, raw = openpronounce_r01[1], raw_r01[1]
    assert op.recording.audio.duration_ms == pytest.approx(raw.recording.audio.duration_ms)
    assert op.engine_evidence["frame_clock"] == raw.engine_evidence["frame_clock"]


def test_posteriors_are_shared_evidence(openpronounce_r01, raw_r01):
    np.testing.assert_allclose(
        np.exp(openpronounce_r01[2]), np.exp(raw_r01[2]), atol=1e-3
    )


def test_timing_agrees_wherever_decoders_agree(openpronounce_r01, raw_r01):
    """Frame spans are shared: identical except where normalization merged runs."""
    op_spans, raw_spans = set(_spans(openpronounce_r01[1])), set(_spans(raw_r01[1]))

    only_op = op_spans - raw_spans
    only_raw = raw_spans - op_spans
    assert only_op == {(286, 309)}
    assert only_raw == {(286, 287), (307, 309)}
    # The merged span is exactly the hull of the two raw runs.
    assert min(s[0] for s in only_raw) == 286 and max(s[1] for s in only_raw) == 309

    same = [
        (a.word, a.timing.start_ms, a.timing.end_ms)
        for a, b in zip(openpronounce_r01[1].words, raw_r01[1].words)
        if (a.timing.start_ms, a.timing.end_ms) == (b.timing.start_ms, b.timing.end_ms)
    ]
    assert {w for w, *_ in same} == {
        "think", "about", "the", "three", "things", "that", "you", "change",
    }


def test_want_to_disagreement_is_preserved_not_reconciled(openpronounce_r01, raw_r01):
    op, raw = openpronounce_r01[1], raw_r01[1]

    op_want_t, raw_want_t = _word(op, "want").phonemes[-1], _word(raw, "want").phonemes[-1]
    assert op_want_t.engine_evidence["operation"] == "omission"
    assert op_want_t.timing.source == "derived"
    assert raw_want_t.engine_evidence["operation"] == "match"
    assert (raw_want_t.timing.frame_start, raw_want_t.timing.frame_end) == (286, 287)

    op_to_t, raw_to_t = _word(op, "to").phonemes[0], _word(raw, "to").phonemes[0]
    assert (op_to_t.timing.start_ms, op_to_t.timing.end_ms) == (5720.0, 6180.0)
    assert (raw_to_t.timing.start_ms, raw_to_t.timing.end_ms) == (6140.0, 6180.0)

    # Word timings differ accordingly and are each internally consistent.
    assert (_word(op, "want").timing.end_ms, _word(raw, "want").timing.end_ms) == (5640.0, 5740.0)
    assert (_word(op, "to").timing.start_ms, _word(raw, "to").timing.start_ms) == (5720.0, 6140.0)


def test_length_contrast_disagreement_is_preserved(openpronounce_r01, raw_r01):
    op_final = _word(openpronounce_r01[1], "three").phonemes[-1]
    raw_final = _word(raw_r01[1], "three").phonemes[-1]

    assert (op_final.expected.phoneme, op_final.engine_evidence["operation"]) == ("i", "match")
    assert (raw_final.expected.phoneme, raw_final.engine_evidence["operation"]) == ("iː", "substitution")
    # Same frames, same acoustics: only the interpretation differs.
    assert (op_final.timing.frame_start, op_final.timing.frame_end) == (
        raw_final.timing.frame_start,
        raw_final.timing.frame_end,
    )
    assert op_final.acoustic == raw_final.acoustic


def test_the_dropped_word_is_dropped_by_both(openpronounce_r01, raw_r01):
    """Agreement here is NOT two independent confirmations: same posteriors."""
    for result in (openpronounce_r01[1], raw_r01[1]):
        assert all(p.engine_evidence["operation"] == "omission" for p in _word(result, "that").phonemes)


def test_shared_and_independent_evidence_is_declared(raw_r01):
    evidence = raw_r01[1].engine_evidence
    assert any("posteriors" in s for s in evidence["shared_with_openpronounce"])
    assert any("acoustic" in s for s in evidence["shared_with_openpronounce"])
    assert any("normalization" in s for s in evidence["independent_of_openpronounce"])
