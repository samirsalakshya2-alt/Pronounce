from __future__ import annotations

import time
import traceback
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

import soundfile as sf

from .schema import (
    EngineInfo,
    ProcessingError,
    ProcessingInfo,
    PronunciationResult,
    RecordingAudio,
    RecordingInfo,
    TargetInfo,
)


class ErrorType:
    """Machine-readable `ProcessingError.type` codes shared by every engine."""

    MODEL_UNAVAILABLE = "model_unavailable"
    CREDENTIALS_UNAVAILABLE = "credentials_unavailable"
    API_UNAVAILABLE = "api_unavailable"
    NETWORK_FAILURE = "network_failure"
    MALFORMED_RESPONSE = "malformed_response"
    UNSUPPORTED_LOCALE = "unsupported_locale"
    MISSING_PHONEME_TIMING = "missing_phoneme_timing"
    MISSING_PROSODY = "missing_prosody"
    PARTIAL_RESULT = "partial_result"
    # The engine has a provider boundary but no verified response mapping yet.
    INTEGRATION_UNVERIFIED = "integration_unverified"
    # No defensible implementation of this engine exists (see its module).
    UNRESOLVED_ENGINE = "unresolved_engine"
    EXPECTED_PHONES_MISMATCH = "expected_phones_mismatch"
    # Input problems: the file cannot be decoded, or is too short for the model.
    AUDIO_UNREADABLE = "audio_unreadable"
    AUDIO_TOO_SHORT = "audio_too_short"
    ENGINE_EXCEPTION = "engine_exception"


class EngineError(Exception):
    """An anticipated engine failure with a machine-readable type.

    `safe_analyze` turns it into a result with `status` and one error, instead
    of a crash, so one engine's failure never takes the others down with it.
    """

    status: Literal["failed", "blocked"] = "failed"

    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        stage: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.stage = stage
        self.retryable = retryable


class EngineUnavailable(EngineError):
    """The engine cannot run at all (model, credentials, service): `blocked`."""

    status = "blocked"


class InvalidAudio(EngineError):
    """The input recording cannot be analysed: `failed`, not retryable."""

    status = "failed"

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(error_type, message, stage="audio", retryable=False)


def require_min_samples(n_samples: int, min_samples: int, sample_rate: int) -> None:
    """Reject audio shorter than a model's receptive field before it reaches the model."""
    if n_samples < min_samples:
        raise InvalidAudio(
            ErrorType.AUDIO_TOO_SHORT,
            f"{n_samples} samples ({n_samples * 1000.0 / sample_rate:.1f} ms); the "
            f"model needs at least {min_samples} "
            f"({min_samples * 1000.0 / sample_rate:.1f} ms).",
        )


class PronunciationEngine(ABC):
    """
    Common interface for all pronunciation-analysis engines.

    Each engine adapter receives:
        - an audio file to analyse
        - the intended/expected text
        - the identity of the recording being analysed

    and returns a normalized PronunciationResult.

    `recording_id` and `original_path` are part of the contract because
    `PronunciationResult` embeds `RecordingInfo`: the result is self-describing,
    so the adapter has to know which recording it is describing and which file
    the user would play back. `original_path` defaults to `audio_path` when the
    analysed file *is* the original.
    """

    name: str
    version: str | None = None
    mode: Literal["local", "cloud"]

    @abstractmethod
    def analyze(
        self,
        audio_path: Path,
        expected_text: str,
        *,
        recording_id: str,
        original_path: Path | None = None,
    ) -> PronunciationResult:
        """
        Analyze one recording against the expected text.

        The adapter is responsible for:
        - calling the underlying engine
        - preserving engine-specific evidence
        - converting available results into the common schema
        - leaving unavailable information null rather than inventing it

        The benchmark runner is responsible for:
        - selecting recordings and expected text
        - running multiple engines
        - comparing results
        """
        raise NotImplementedError


def _recording_info(
    audio_path: Path,
    expected_text: str,
    recording_id: str,
    original_path: Path | None,
) -> RecordingInfo:
    """Describe a recording without any engine having produced anything.

    Falls back to zeros when the file cannot even be read, so that a result can
    still be returned for it.
    """
    try:
        info = sf.info(str(audio_path))
        sample_rate, channels = int(info.samplerate), int(info.channels)
        duration_ms = info.frames * 1000.0 / info.samplerate
    except Exception:  # noqa: BLE001 - describing a failure must not fail
        sample_rate, channels, duration_ms = 0, 0, 0.0

    return RecordingInfo(
        id=recording_id,
        audio=RecordingAudio(
            original_path=str(original_path or audio_path),
            analysis_path=str(audio_path),
            sample_rate_hz=sample_rate,
            channels=channels,
            duration_ms=duration_ms,
        ),
        target=TargetInfo(text=expected_text),
    )


def unavailable_result(
    engine: PronunciationEngine,
    audio_path: Path,
    expected_text: str,
    *,
    recording_id: str,
    original_path: Path | None,
    status: Literal["failed", "blocked"],
    error: ProcessingError,
    wall_time_ms: float | None = None,
    engine_evidence: dict | None = None,
) -> PronunciationResult:
    """A result that carries no evidence, only the reason there is none."""
    return PronunciationResult(
        status=status,
        recording=_recording_info(
            Path(audio_path), expected_text, recording_id, original_path
        ),
        engine=EngineInfo(
            name=engine.name,
            version=engine.version,
            mode=engine.mode,
            model=getattr(engine, "model_id", None),
        ),
        processing=ProcessingInfo(wall_time_ms=wall_time_ms),
        engine_evidence=engine_evidence or {"provider": engine.name},
        errors=[error],
    )


def safe_analyze(
    engine: PronunciationEngine,
    audio_path: Path,
    expected_text: str,
    *,
    recording_id: str,
    original_path: Path | None = None,
) -> PronunciationResult:
    """Run one engine without letting its failure escape.

    An `EngineError` becomes a result with its own status (`blocked` for
    `EngineUnavailable`, `failed` for `InvalidAudio`); any other exception
    becomes a `failed` result carrying the exception type and the innermost
    frame. The caller always gets a schema-valid `PronunciationResult` back.
    """
    start = time.perf_counter()
    try:
        return engine.analyze(
            Path(audio_path),
            expected_text,
            recording_id=recording_id,
            original_path=original_path,
        )
    except EngineError as exc:
        return unavailable_result(
            engine,
            audio_path,
            expected_text,
            recording_id=recording_id,
            original_path=original_path,
            status=exc.status,
            error=ProcessingError(
                type=exc.error_type,
                message=exc.message,
                stage=exc.stage,
                retryable=exc.retryable,
            ),
            wall_time_ms=(time.perf_counter() - start) * 1000.0,
        )
    except Exception as exc:  # noqa: BLE001 - isolation is the point
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        return unavailable_result(
            engine,
            audio_path,
            expected_text,
            recording_id=recording_id,
            original_path=original_path,
            status="failed",
            error=ProcessingError(
                type=ErrorType.ENGINE_EXCEPTION,
                message=(
                    f"{type(exc).__name__}: {exc} "
                    f"(at {Path(frame.filename).name}:{frame.lineno} in {frame.name})"
                ),
                stage="analyze",
                retryable=None,
            ),
            wall_time_ms=(time.perf_counter() - start) * 1000.0,
        )
