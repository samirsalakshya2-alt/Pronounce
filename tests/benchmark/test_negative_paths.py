"""Realistic failures, against the real local engines and real files.

Every case must come back as a schema-valid result with a machine-readable
reason, and must leave the engine usable for the next recording.
"""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import resample_poly

from pronunciation_lab.benchmark.base import ErrorType, safe_analyze
from pronunciation_lab.benchmark.engines.openpronounce import OpenPronounceEngine
from pronunciation_lab.benchmark.engines.wav2vec2_raw import Wav2Vec2RawEngine
from pronunciation_lab.benchmark.frames import WAV2VEC2_RECEPTIVE_FIELD_SAMPLES
from pronunciation_lab.benchmark.schema import PronunciationResult

TEXT = "Think about the three things that you want to change."
ENGINES = ["openpronounce_r01", "raw_r01"]


@pytest.fixture(scope="module")
def files(tmp_path_factory, r01_row):
    d = tmp_path_factory.mktemp("audio")
    (d / "garbage.wav").write_bytes(b"RIFF\x00\x00not a wave file" * 20)
    (d / "zero_bytes.wav").write_bytes(b"")
    sf.write(d / "empty.wav", np.zeros(0, np.float32), 16_000)
    sf.write(d / "10ms.wav", np.full(160, 0.01, np.float32), 16_000)
    sf.write(
        d / "receptive_field.wav",
        np.full(WAV2VEC2_RECEPTIVE_FIELD_SAMPLES, 0.01, np.float32),
        16_000,
    )
    sf.write(d / "silence.wav", np.zeros(16_000, np.float32), 16_000)

    r01, _ = sf.read(r01_row["audio_path"], dtype="float32")
    at_44k = resample_poly(r01, 441, 160).astype(np.float32)
    sf.write(d / "stereo_44k.wav", np.stack([at_44k, at_44k], axis=1), 44_100)
    return d


def _analyze(engine, path):
    result = safe_analyze(engine, path, TEXT, recording_id="NEG")
    PronunciationResult.model_validate_json(result.model_dump_json())
    return result


@pytest.mark.parametrize("fixture", ENGINES)
@pytest.mark.parametrize(
    "name, error_type",
    [
        ("missing.wav", ErrorType.AUDIO_UNREADABLE),
        ("garbage.wav", ErrorType.AUDIO_UNREADABLE),
        ("zero_bytes.wav", ErrorType.AUDIO_UNREADABLE),
        ("empty.wav", ErrorType.AUDIO_TOO_SHORT),
        ("10ms.wav", ErrorType.AUDIO_TOO_SHORT),
    ],
)
def test_bad_input_fails_with_a_typed_reason(fixture, name, error_type, files, request):
    engine = request.getfixturevalue(fixture)[0]
    result = _analyze(engine, files / name)

    assert result.status == "failed"
    assert result.words == []
    [error] = result.errors
    assert (error.type, error.stage, error.retryable) == (error_type, "audio", False)
    assert result.engine.name == engine.name


@pytest.mark.parametrize("fixture", ENGINES)
def test_audio_at_the_receptive_field_is_accepted(fixture, files, request):
    engine = request.getfixturevalue(fixture)[0]
    result = _analyze(engine, files / "receptive_field.wav")

    assert result.status == "ok"
    assert result.engine_evidence["frame_clock"]["n_frames"] == 1


@pytest.mark.parametrize("fixture", ENGINES)
def test_silence_yields_all_omissions_with_real_posteriors(fixture, files, request):
    engine = request.getfixturevalue(fixture)[0]
    result = _analyze(engine, files / "silence.wav")

    assert result.status == "ok"
    assert result.engine_evidence["recognition"]["phones"] == []
    phonemes = [p for w in result.words for p in w.phonemes]
    assert phonemes
    for p in phonemes:
        assert p.engine_evidence["operation"] == "omission"
        assert p.observed.top is None and p.observed.confidence is None
        assert p.timing.source == "derived"
        # Nothing heard either side: the region is every model frame. 1 s of
        # audio gives 49 frames (the receptive field costs one), so it ends at
        # 980 ms -- the end of the last frame, not the end of the file.
        clock = result.engine_evidence["frame_clock"]
        assert clock["n_frames"] == 49
        assert (p.timing.start_ms, p.timing.end_ms) == (0.0, 49 * clock["ms_per_frame"])
        # The expected phone was genuinely scored, and lost to blank.
        assert 0.0 <= p.engine_evidence["expected_phone_posterior"] < 0.5
        assert p.acoustic.energy_db < -200  # digital silence
        assert p.acoustic.voicing is None   # undecidable, not "unvoiced"
    assert all(w.timing.source == "unknown" for w in result.words)


def test_raw_converts_stereo_44k_and_says_so(raw_r01, files):
    engine, r01_result, _, _ = raw_r01
    result = _analyze(engine, files / "stereo_44k.wav")

    assert result.status == "ok"
    loading = result.engine_evidence["audio_loading"]
    assert loading == {
        "loader": "soundfile",
        "source_sample_rate_hz": 44_100,
        "source_channels": 2,
        "resampled": True,
        "downmixed": True,
    }
    # The result describes the analysis waveform, not the source file.
    assert (result.recording.audio.sample_rate_hz, result.recording.audio.channels) == (16_000, 1)
    assert result.recording.audio.duration_ms == pytest.approx(
        r01_result.recording.audio.duration_ms, abs=1.0
    )
    # A resampling round trip is not the original signal; the decode survives it.
    assert (
        result.engine_evidence["recognition"]["phones"]
        == r01_result.engine_evidence["recognition"]["phones"]
    )


def test_openpronounce_converts_stereo_44k(openpronounce_r01, files):
    engine, r01_result, _, _ = openpronounce_r01
    result = _analyze(engine, files / "stereo_44k.wav")

    assert result.status == "ok"
    assert result.recording.audio.duration_ms == pytest.approx(
        r01_result.recording.audio.duration_ms, abs=1.0
    )
    # librosa's resampler perturbs OpenPronounce's decode by one phone here:
    # recorded as observed behaviour, not hidden.
    heard = result.engine_evidence["recognition"]["phones"]
    assert abs(len(heard) - 29) <= 1


def test_engine_still_works_after_failures(raw_r01, files, r01_row):
    engine, first, _, _ = raw_r01
    for name in ("missing.wav", "garbage.wav", "empty.wav"):
        assert _analyze(engine, files / name).status == "failed"

    again = _analyze(engine, Path(r01_row["audio_path"]))
    assert again.status == "ok"
    assert again.words == first.words


def test_raw_missing_model_is_blocked_not_crashed(files):
    engine = Wav2Vec2RawEngine(revision="0" * 40, local_files_only=True)
    result = _analyze(engine, files / "silence.wav")

    assert result.status == "blocked"
    [error] = result.errors
    assert (error.type, error.stage, error.retryable) == (
        ErrorType.MODEL_UNAVAILABLE,
        "model_load",
        True,
    )


def test_openpronounce_missing_model_is_blocked_not_crashed(files, monkeypatch):
    from openpronounce import phones as op_phones

    def no_model(*args, **kwargs):
        raise OSError("model not found in cache and network disabled")

    monkeypatch.setattr(op_phones, "recognize_phones", no_model)
    result = _analyze(OpenPronounceEngine(), files / "silence.wav")

    assert result.status == "blocked"
    assert result.errors[0].type == ErrorType.MODEL_UNAVAILABLE
