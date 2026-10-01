from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

from .schema import PronunciationResult


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
