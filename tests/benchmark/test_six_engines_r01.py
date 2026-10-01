"""All six engines on R01 through the common contract.

Not the benchmark: one recording, to show that runnable engines produce evidence
and blocked ones produce explicit, machine-readable reasons -- side by side,
without one affecting another.
"""

from pathlib import Path

import pytest

from pronunciation_lab.benchmark.base import ErrorType, safe_analyze
from pronunciation_lab.benchmark.engines import ENGINES, create_engine
from pronunciation_lab.benchmark.schema import PronunciationResult

RUNNING = {"openpronounce", "wav2vec2_raw"}
BLOCKED = {
    "wavlm": ErrorType.UNRESOLVED_ENGINE,
    "azure_pronunciation": ErrorType.CREDENTIALS_UNAVAILABLE,
    "speechsuper": ErrorType.CREDENTIALS_UNAVAILABLE,
    "speechace": ErrorType.CREDENTIALS_UNAVAILABLE,
}


@pytest.fixture(scope="module")
def results(r01_row):
    with pytest.MonkeyPatch.context() as mp:
        for var in (
            "AZURE_SPEECH_KEY",
            "AZURE_SPEECH_REGION",
            "SPEECHSUPER_APP_KEY",
            "SPEECHSUPER_SECRET_KEY",
            "SPEECHACE_API_KEY",
        ):
            mp.delenv(var, raising=False)

        return {
            name: safe_analyze(
                create_engine(name),
                Path(r01_row["audio_path"]),
                r01_row["target_text"],
                recording_id="R01",
                original_path=Path(r01_row["original_path"]),
            )
            for name in ENGINES
        }


def test_every_engine_returns_a_schema_valid_result(results):
    assert set(results) == RUNNING | set(BLOCKED)
    for name, result in results.items():
        assert result.engine.name == name
        assert result.recording.id == "R01"
        PronunciationResult.model_validate_json(result.model_dump_json())


@pytest.mark.parametrize("name", sorted(RUNNING))
def test_running_engines_produce_evidence(results, name):
    result = results[name]
    assert result.status == "ok", result.errors
    assert len(result.words) == 10
    assert result.phone_set
    assert result.processing.inference_ms > 0
    assert result.processing.api_latency_ms is None
    for phoneme in (p for w in result.words for p in w.phonemes):
        assert phoneme.timing.source in {"engine", "derived"}
        assert phoneme.acoustic.measured_on == "analysis"


@pytest.mark.parametrize("name", sorted(BLOCKED))
def test_blocked_engines_say_why(results, name):
    result = results[name]
    assert result.status == "blocked"
    assert result.words == []
    assert [e.type for e in result.errors] == [BLOCKED[name]]
    assert result.engine_evidence["readiness"]["state"] in {"blocked", "unresolved"}
    # The recording is still described, from the file itself.
    assert result.recording.audio.duration_ms == pytest.approx(7850.7, abs=5.0)


def test_wavlm_records_why_each_candidate_was_not_adopted(results):
    candidates = results["wavlm"].engine_evidence["readiness"]["candidates"]
    assert candidates["speech31/wavlm-large-english-phoneme"]["verdict"] == "rejected"
    assert "children" in candidates["Jianshu001/wavlm-phoneme-scorer"]["reason"]
