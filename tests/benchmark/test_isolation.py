"""One engine failing must never break the common architecture."""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from pronunciation_lab.benchmark.base import (
    EngineUnavailable,
    ErrorType,
    PronunciationEngine,
    safe_analyze,
)
from pronunciation_lab.benchmark.engines import ENGINES, create_engine
from pronunciation_lab.benchmark.schema import PronunciationResult


class _Raises(PronunciationEngine):
    name = "raises"
    mode = "local"

    def __init__(self, exc):
        self.exc = exc

    def analyze(self, audio_path, expected_text, *, recording_id, original_path=None):
        raise self.exc


@pytest.fixture
def wav(tmp_path) -> Path:
    path = tmp_path / "tone.wav"
    sf.write(path, np.zeros(16_000, dtype=np.float32), 16_000)
    return path


def test_unavailable_engine_becomes_a_blocked_result(wav):
    engine = _Raises(
        EngineUnavailable(
            ErrorType.MODEL_UNAVAILABLE, "no model", stage="model_load", retryable=True
        )
    )
    result = safe_analyze(engine, wav, "hello", recording_id="X")

    assert isinstance(result, PronunciationResult)
    assert result.status == "blocked"
    assert result.words == []
    [error] = result.errors
    assert (error.type, error.stage, error.retryable) == (
        ErrorType.MODEL_UNAVAILABLE,
        "model_load",
        True,
    )
    assert result.recording.audio.duration_ms == pytest.approx(1000.0)


def test_unexpected_exception_becomes_a_failed_result(wav):
    result = safe_analyze(_Raises(ValueError("boom")), wav, "hello", recording_id="X")

    assert result.status == "failed"
    [error] = result.errors
    assert error.type == ErrorType.ENGINE_EXCEPTION
    assert "ValueError: boom" in error.message
    assert result.processing.wall_time_ms is not None
    # Still serialisable, like any other result.
    PronunciationResult.model_validate_json(result.model_dump_json())


def test_unreadable_audio_still_yields_a_result(tmp_path):
    result = safe_analyze(
        _Raises(RuntimeError("x")), tmp_path / "missing.wav", "hi", recording_id="X"
    )
    assert result.status == "failed"
    assert result.recording.audio.duration_ms == 0.0


def test_registry_names_all_six_engines():
    assert list(ENGINES) == [
        "openpronounce",
        "wav2vec2_raw",
        "wavlm",
        "azure_pronunciation",
        "speechsuper",
        "speechace",
    ]
    with pytest.raises(KeyError):
        create_engine("nope")


def test_a_failing_engine_does_not_affect_another(raw_r01, r01_row):
    """Failures before and after a real run leave its evidence untouched."""
    engine, reference, _, _ = raw_r01
    audio = Path(r01_row["audio_path"])

    assert safe_analyze(_Raises(MemoryError("oom")), audio, "x", recording_id="A").status == "failed"
    result = safe_analyze(engine, audio, r01_row["target_text"], recording_id="R01")
    assert safe_analyze(_Raises(KeyError("k")), audio, "x", recording_id="B").status == "failed"

    assert result.status == "ok"
    assert result.words == reference.words
    assert result.engine_evidence["recognition"] == reference.engine_evidence["recognition"]
