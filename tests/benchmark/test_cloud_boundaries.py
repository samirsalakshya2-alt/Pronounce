"""Cloud engine boundaries.

No credentials exist for Azure, SpeechSuper or Speechace, so no real
integration can run. These tests pin the boundary: credentials are read from the
environment only, and the engine returns an explicit, machine-readable `blocked`
result rather than a fabricated one -- with or without credentials, until a
mapping has been built from a real response.
"""

import socket

import numpy as np
import pytest
import soundfile as sf

from pronunciation_lab.benchmark.base import ErrorType, safe_analyze
from pronunciation_lab.benchmark.engines import create_engine
from pronunciation_lab.benchmark.engines.cloud import CloudEngine

CLOUD = ["azure_pronunciation", "speechsuper", "speechace"]


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "x.wav"
    sf.write(path, np.zeros(8_000, dtype=np.float32), 16_000)
    return path


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """The boundary must not touch the network in any state."""

    def refuse(*args, **kwargs):
        raise AssertionError("cloud boundary attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def _clear(monkeypatch, engine: CloudEngine):
    for name in engine.credential_env_vars:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("name", CLOUD)
def test_missing_credentials_give_a_blocked_result(name, wav, monkeypatch):
    engine = create_engine(name)
    _clear(monkeypatch, engine)

    assert engine.mode == "cloud"
    assert engine.credential_env_vars

    result = safe_analyze(engine, wav, "hello", recording_id="X")

    assert result.status == "blocked"
    assert result.words == []
    [error] = result.errors
    assert error.type == ErrorType.CREDENTIALS_UNAVAILABLE
    assert error.stage == "credentials"

    readiness = result.engine_evidence["readiness"]
    assert readiness["missing_env"] == list(engine.credential_env_vars)
    assert result.engine.mode == "cloud"

    # Nothing measured, nothing invented.
    processing = result.processing
    assert processing.inference_ms is None
    assert processing.api_latency_ms is None
    assert processing.provider_processing_ms is None


@pytest.mark.parametrize("name", CLOUD)
def test_credentials_alone_do_not_produce_unverified_evidence(name, wav, monkeypatch):
    engine = create_engine(name)
    for var in engine.credential_env_vars:
        monkeypatch.setenv(var, "dummy")

    result = engine.analyze(wav, "hello", recording_id="X")

    assert result.status == "blocked"
    [error] = result.errors
    assert error.type == ErrorType.INTEGRATION_UNVERIFIED
    assert result.engine_evidence["readiness"]["missing_env"] == []
    assert result.engine_evidence["verification_checklist"]
