"""Shared constants and helpers for the app tests.

Kept out of conftest.py on purpose: tests/benchmark has its own conftest.py, and
`from conftest import ...` resolves to whichever conftest Python imported first,
which depends on the order directories are given to pytest.
"""

import json
import urllib.error
import urllib.request
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


class Client:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def get_json(self, path):
        status, _, body = self.request("GET", path)
        return status, json.loads(body)

    def post_json(self, path, payload):
        status, _, body = self.request("POST", path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
        return status, json.loads(body)

    def upload(self, audio: bytes, text: str, engine: str | None = None, filename: str = "rec.wav"):
        from urllib.parse import urlencode

        q = {"text": text} | ({"engine": engine} if engine else {})
        status, _, body = self.request("POST", "/api/analyze?" + urlencode(q), audio, {"X-Filename": filename})
        return status, json.loads(body)
