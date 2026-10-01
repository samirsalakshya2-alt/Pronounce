"""Real R01 integration for the raw Wav2Vec2 engine.

Loads `facebook/wav2vec2-lv-60-espeak-cv-ft` and the real R01 WAV. Pins the raw
decode, and pins precisely what this engine does and does not share with
OpenPronounce (same checkpoint, so the same posteriors; different decoding).
"""

from pathlib import Path

import numpy as np
import pytest

from pronunciation_lab.benchmark.engines.wav2vec2_raw import (
    MODEL_ID,
    MODEL_REVISION,
    Wav2Vec2RawEngine,
    expected_phones,
)
from pronunciation_lab.benchmark.schema import PronunciationResult

# Raw greedy decode of R01, as produced by this checkpoint/revision on CPU.
EXPECTED_RAW_PHONES = (
    "θ ɪ ŋ k ɐ b aʊ t ð ə θ ɪ ɹ i θ ɪ ŋ z j uː w ʌ n t t ʊ tʃ eɪ n dʒ"
).split()


@pytest.fixture(scope="module")
def r01(r01_row):
    engine = Wav2Vec2RawEngine(local_files_only=True)
    result = engine.analyze(
        Path(r01_row["audio_path"]),
        r01_row["target_text"],
        recording_id="R01",
        original_path=Path(r01_row["original_path"]),
    )
    return result, engine, r01_row


def _phonemes(result):
    return [p for w in result.words for p in w.phonemes]


def test_result_is_valid_and_serialisable(r01):
    result, _, _ = r01
    assert result.status == "ok"
    assert result.errors == []
    assert result.engine.name == "wav2vec2_raw"
    assert result.engine.model == f"{MODEL_ID}@{MODEL_REVISION}"
    assert result.phone_set == "ipa-espeak-raw"
    PronunciationResult.model_validate_json(result.model_dump_json())


def test_raw_decode_is_pinned_and_unnormalized(r01):
    result, _, _ = r01
    evidence = result.engine_evidence

    assert evidence["recognition"]["phones"] == EXPECTED_RAW_PHONES
    assert evidence["normalization"] is None
    assert evidence["resolved_commit"] == MODEL_REVISION
    # Raw tokens that OpenPronounce would have rewritten survive here.
    assert "ɐ" in EXPECTED_RAW_PHONES and "uː" in EXPECTED_RAW_PHONES


def test_expected_phones_come_from_espeak_with_stress_split_off():
    words, groups, stresses, details = expected_phones("Think about the three")
    assert words == ["think", "about", "the", "three"]
    assert groups == [["θ", "ɪ", "ŋ", "k"], ["ɐ", "b", "aʊ", "t"], ["ð", "ə"], ["θ", "ɹ", "iː"]]
    assert stresses[0] == [None, "primary", None, None]
    assert stresses[1][2] == "secondary"
    assert details["language"] == "en-us"
    assert not any("ˈ" in p or "ˌ" in p for g in groups for p in g)


def test_expected_stress_is_reported_but_never_observed(r01):
    result, _, _ = r01
    stressed = [p for p in _phonemes(result) if p.expected.stress]
    assert stressed
    for phoneme in _phonemes(result):
        assert phoneme.expected.source == "espeak"
        assert phoneme.prosody.stress_observed is None
        assert phoneme.prosody.prominence is None


def test_word_structure_and_derived_counts(r01):
    result, _, row = r01
    assert [w.word for w in result.words] == [
        w.strip(".,").lower() for w in row["target_text"].split()
    ]
    for word in result.words:
        assert len(word.phonemes) == len(word.expected_phonemes)
        assert word.engine_evidence["provider_report"] is None
        counts = word.engine_evidence["derived_operation_counts"]
        assert sum(counts[k] for k in ("match", "substitution", "omission")) == len(
            word.phonemes
        )

    that = next(w for w in result.words if w.word == "that")
    assert that.engine_evidence["derived_operation_counts"]["omission"] == 3
    assert that.timing.source == "unknown"
    assert all(p.timing.source == "derived" for p in that.phonemes)

    # The length contrast OpenPronounce erases is visible here.
    three = next(w for w in result.words if w.word == "three")
    final = three.phonemes[-1]
    assert (final.expected.phoneme, final.observed.top) == ("iː", "i")
    assert final.engine_evidence["operation"] == "substitution"
    assert {c.phoneme for c in final.observed.nbest} >= {"i", "iː"}


def test_timings_are_ordered_in_ms_and_inside_the_recording(r01):
    result, _, _ = r01
    duration = result.recording.audio.duration_ms
    assert result.engine_evidence["frame_clock"]["ms_per_frame"] == pytest.approx(20.0)

    previous = -1.0
    for phoneme in _phonemes(result):
        t = phoneme.timing
        assert 0.0 <= t.start_ms <= t.end_ms <= duration
        assert t.start_ms >= previous
        previous = t.start_ms
    assert previous > duration * 0.5


def test_nbest_and_acoustics_for_every_heard_phone(r01):
    result, _, _ = r01
    for phoneme in _phonemes(result):
        assert phoneme.acoustic.measured_on == "analysis"
        assert phoneme.acoustic.energy_db is not None
        assert phoneme.acoustic.spectral_features["pitch_window_end_ms"] is not None

        if phoneme.observed.top is not None:
            nbest = phoneme.observed.nbest
            assert len(nbest) > 1
            assert nbest[0].phoneme == phoneme.observed.top
            probabilities = [c.probability for c in nbest]
            assert probabilities == sorted(probabilities, reverse=True)
        posterior = phoneme.engine_evidence["expected_phone_posterior"]
        assert posterior is None or 0.0 <= posterior <= 1.0


def test_processing_timing(r01):
    result, _, _ = r01
    p = result.processing
    assert p.run_type == "cold"
    assert p.model_load_ms > 0 and p.inference_ms > 0 and p.postprocessing_ms > 0
    assert p.wall_time_ms >= p.inference_ms + p.postprocessing_ms
    assert p.device == "cpu"
    assert p.api_latency_ms is None


def test_posteriors_are_shared_with_openpronounce_and_decoding_is_not(r01):
    """The one place the shared/independent claim is checked, not just stated."""
    from openpronounce import load_audio
    from openpronounce import phones as op_phones

    result, engine, row = r01
    raw = engine.last_recognition

    waveform = load_audio(row["audio_path"], sr=16_000)
    op = op_phones.recognize_phones(waveform, sampling_rate=16_000)

    assert raw.vocab == tuple(op.vocab)
    assert raw.log_posteriors.shape == op.log_posteriors.shape
    # Same checkpoint, same input: the same frame-level evidence.
    np.testing.assert_allclose(
        np.exp(raw.log_posteriors), np.exp(op.log_posteriors), atol=1e-3
    )
    # ...while the decoded phone sequences differ, purely through normalization.
    assert raw.tokens != list(op.phones)
    assert [op_phones.normalize_phone(t) for t in raw.tokens] != raw.tokens
