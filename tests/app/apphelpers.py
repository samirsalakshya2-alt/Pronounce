"""Shared constants and helpers for the app tests.

Kept out of conftest.py on purpose: tests/benchmark has its own conftest.py, and
`from conftest import ...` resolves to whichever conftest Python imported first,
which depends on the order directories are given to pytest.
"""

from pathlib import Path

import numpy as np
import soundfile as sf

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"


def wav_bytes(seconds: float = 1.0, *, sr: int = 16_000, channels: int = 1, subtype: str = "PCM_16",
              silent: bool = False, tmp: Path) -> bytes:
    n = int(sr * seconds)
    rng = np.random.default_rng(0)
    audio = np.zeros(n) if silent else rng.uniform(-0.3, 0.3, n)
    if channels > 1:
        audio = np.stack([audio] * channels, axis=1)
    path = tmp / f"tone_{sr}_{channels}_{subtype}_{seconds}.wav"
    sf.write(path, audio.astype(np.float32), sr, subtype=subtype)
    return path.read_bytes()
