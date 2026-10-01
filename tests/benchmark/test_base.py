from pathlib import Path

import pytest

from pronunciation_lab.benchmark.base import PronunciationEngine


class DummyEngine(PronunciationEngine):
    name = "dummy"
    version = "0.1"
    mode = "local"

    def analyze(
        self,
        audio_path: Path,
        expected_text: str,
        *,
        recording_id: str,
        original_path: Path | None = None,
    ):
        return None


def test_engine_interface():
    engine = DummyEngine()

    assert engine.name == "dummy"
    assert engine.version == "0.1"
    assert engine.mode == "local"


def test_base_engine_is_abstract():
    with pytest.raises(TypeError):
        PronunciationEngine()