"""The six evidence engines, by name.

Factories import lazily so that asking about a cloud engine never loads torch,
and a broken optional dependency only affects the engine that needs it.
"""

from __future__ import annotations

from collections.abc import Callable

from pronunciation_lab.benchmark.base import PronunciationEngine


def _openpronounce() -> PronunciationEngine:
    from .openpronounce import OpenPronounceEngine

    return OpenPronounceEngine()


def _wav2vec2_raw() -> PronunciationEngine:
    from .wav2vec2_raw import Wav2Vec2RawEngine

    return Wav2Vec2RawEngine()


def _wavlm() -> PronunciationEngine:
    from .wavlm import WavLMEngine

    return WavLMEngine()


def _azure() -> PronunciationEngine:
    from .cloud import AzurePronunciationEngine

    return AzurePronunciationEngine()


def _speechsuper() -> PronunciationEngine:
    from .cloud import SpeechSuperEngine

    return SpeechSuperEngine()


def _speechace() -> PronunciationEngine:
    from .cloud import SpeechaceEngine

    return SpeechaceEngine()


ENGINES: dict[str, Callable[[], PronunciationEngine]] = {
    "openpronounce": _openpronounce,
    "wav2vec2_raw": _wav2vec2_raw,
    "wavlm": _wavlm,
    "azure_pronunciation": _azure,
    "speechsuper": _speechsuper,
    "speechace": _speechace,
}


def create_engine(name: str) -> PronunciationEngine:
    try:
        factory = ENGINES[name]
    except KeyError:
        raise KeyError(f"unknown engine {name!r}; known: {sorted(ENGINES)}") from None
    return factory()
