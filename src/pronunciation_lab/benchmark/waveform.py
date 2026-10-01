"""Loading the analysis waveform for engines that do not bring their own loader.

The analysis waveform is 16 kHz mono float32. It is what acoustic evidence is
measured on, which is why results record `measured_on="analysis"`: it is not the
original recording, which may differ in rate, channels and codec.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

ANALYSIS_SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class AnalysisWaveform:
    samples: np.ndarray
    sample_rate: int
    source_sample_rate: int
    source_channels: int
    resampled: bool
    downmixed: bool

    @property
    def duration_ms(self) -> float:
        return self.samples.size * 1000.0 / self.sample_rate

    def describe(self) -> dict[str, object]:
        return {
            "loader": "soundfile",
            "source_sample_rate_hz": self.source_sample_rate,
            "source_channels": self.source_channels,
            "resampled": self.resampled,
            "downmixed": self.downmixed,
        }


def load_analysis_waveform(path: str | Path) -> AnalysisWaveform:
    """Read `path` as 16 kHz mono float32, recording what had to change."""
    audio, source_rate = sf.read(str(path), dtype="float32", always_2d=True)
    channels = audio.shape[1]

    mono = audio.mean(axis=1, dtype=np.float32) if channels > 1 else audio[:, 0]

    resampled = source_rate != ANALYSIS_SAMPLE_RATE
    if resampled:
        g = gcd(source_rate, ANALYSIS_SAMPLE_RATE)
        mono = resample_poly(
            mono, ANALYSIS_SAMPLE_RATE // g, source_rate // g
        ).astype(np.float32)

    return AnalysisWaveform(
        samples=np.ascontiguousarray(mono, dtype=np.float32),
        sample_rate=ANALYSIS_SAMPLE_RATE,
        source_sample_rate=int(source_rate),
        source_channels=int(channels),
        resampled=resampled,
        downmixed=channels > 1,
    )
