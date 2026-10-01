from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


# ============================================================
# Recording
# ============================================================

class RecordingAudio(BaseModel):
    original_path: str
    analysis_path: str | None = None

    sample_rate_hz: int
    channels: int
    duration_ms: float


class TargetInfo(BaseModel):
    text: str


class RecordingInfo(BaseModel):
    id: str
    audio: RecordingAudio
    target: TargetInfo


# ============================================================
# Engine
# ============================================================

class EngineInfo(BaseModel):
    name: str
    version: str | None = None
    mode: Literal["local", "cloud"]


# ============================================================
# Processing / benchmark timing
# ============================================================

class ProcessingInfo(BaseModel):
    wall_time_ms: float | None = None

    model_load_ms: float | None = None
    inference_ms: float | None = None
    postprocessing_ms: float | None = None

    realtime_factor: float | None = None

    run_type: Literal["cold", "warm"] | None = None

    timing_method: Literal[
        "monotonic_clock",
        "provider_reported",
        "unknown",
    ] = "monotonic_clock"


# ============================================================
# Phoneme timing
# ============================================================

class TimingInfo(BaseModel):
    start_ms: float | None = None
    end_ms: float | None = None
    duration_ms: float | None = None
    source: Literal[
        "engine",
        "forced_alignment",
        "acoustic_detection",
        "derived",
        "unknown",
    ] | None = None
    frame_start: int | None = None
    frame_end: int | None = None


# ============================================================
# Expected phoneme
# ============================================================

class ExpectedPhoneme(BaseModel):
    phoneme: str

    position: int | None = None
    syllable: int | None = None

    stress: str | None = None


# ============================================================
# Observed phoneme / N-best candidates
# ============================================================

class NBestCandidate(BaseModel):
    phoneme: str
    probability: float | None = None
    score: float | None = None


class ObservedPhoneme(BaseModel):
    top: str | None = None
    confidence: float | None = None
    nbest: list[NBestCandidate] = Field(default_factory=list)
    frame_start: int | None = None
    frame_end: int | None = None


# ============================================================
# Acoustic evidence
# ============================================================

class AcousticEvidence(BaseModel):
    energy_db: float | None = None
    relative_energy: float | None = None

    f0_hz: float | None = None

    voicing: bool | None = None

    spectral_features: dict[str, Any] | None = None


# ============================================================
# Prosody evidence
# ============================================================

class ProsodyEvidence(BaseModel):
    stress_expected: str | None = None
    stress_observed: str | None = None

    prominence: float | None = None


# ============================================================
# Phoneme-level result
# ============================================================

class PhonemeResult(BaseModel):
    position: int

    expected: ExpectedPhoneme

    observed: ObservedPhoneme

    timing: TimingInfo

    acoustic: AcousticEvidence

    prosody: ProsodyEvidence

    engine_evidence: dict[str, Any] = Field(default_factory=dict)


# ============================================================
# Word-level result
# ============================================================

class WordResult(BaseModel):
    word: str

    expected_phonemes: list[str] = Field(default_factory=list)

    timing: TimingInfo

    phonemes: list[PhonemeResult] = Field(default_factory=list)

    engine_evidence: dict[str, Any] = Field(default_factory=dict)


# ============================================================
# Errors
# ============================================================

class ProcessingError(BaseModel):
    type: str
    message: str


# ============================================================
# Complete normalized pronunciation result
# ============================================================

class PronunciationResult(BaseModel):
    schema_version: str = "0.1"

    recording: RecordingInfo

    engine: EngineInfo

    processing: ProcessingInfo

    words: list[WordResult] = Field(default_factory=list)

    # Raw / recording-level provider output, preserved verbatim. Phoneme- and
    # word-level evidence lives on PhonemeResult / WordResult; this is for what
    # the provider reports about the utterance as a whole (its own error list,
    # overall rates, model identity, timing metadata).
    engine_evidence: dict[str, Any] = Field(default_factory=dict)

    # Failures that occurred while producing this result. Not pronunciation
    # errors -- those are provider evidence, not processing problems.
    errors: list[ProcessingError] = Field(default_factory=list)