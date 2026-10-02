"""Fixtures for the M3 application tests."""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
sys.path.insert(0, str(PROJECT_ROOT / "tests" / "benchmark"))  # reuse the M2 fakes

from benchmark_fakes import FakeEngine, FakeUnavailableEngine, factory  # noqa: E402

from pronunciation_lab.app.server import start_in_thread  # noqa: E402
from pronunciation_lab.app.service import AnalysisService  # noqa: E402


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


@pytest.fixture
def fake_engines():
    return {
        "wav2vec2_raw": FakeEngine("wav2vec2_raw"),
        "openpronounce": FakeEngine("openpronounce"),
        "wavlm": FakeUnavailableEngine("wavlm", "unresolved"),
        "azure_pronunciation": FakeUnavailableEngine("azure_pronunciation", "blocked"),
    }


@pytest.fixture
def fake_service(tmp_path, fake_engines):
    svc = AnalysisService(
        notes_path=tmp_path / "notes" / "notes.jsonl",
        engine_factory=factory(fake_engines),
        engine_ids=list(fake_engines),
    )
    yield svc
    svc.close()


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


@pytest.fixture
def fake_server(fake_service):
    server, thread = start_in_thread(fake_service)
    yield Client(server.url), server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def real_server(tmp_path_factory):
    """The real service with the real engines and the local benchmark data."""
    if not (DATA_DIR / "benchmark_wav" / "R01.wav").exists():
        pytest.skip("benchmark data missing (data/ is untracked)")
    notes = tmp_path_factory.mktemp("notes") / "notes.jsonl"
    svc = AnalysisService(data_dir=DATA_DIR, notes_path=notes)
    server, thread = start_in_thread(svc)
    yield Client(server.url), svc
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    svc.close()
