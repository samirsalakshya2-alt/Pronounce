"""Fixtures for the M3/M4 application tests."""

import sys

import pytest
from apphelpers import DATA_DIR, PROJECT_ROOT, Client, wav_bytes  # noqa: F401 - re-exported for older imports

sys.path.insert(0, str(PROJECT_ROOT / "tests" / "benchmark"))  # reuse the M2 fakes

from benchmark_fakes import FakeEngine, FakeUnavailableEngine, factory  # noqa: E402

from pronunciation_lab.app.server import start_in_thread  # noqa: E402
from pronunciation_lab.app.service import AnalysisService  # noqa: E402
from pronunciation_lab.reader.service import ReaderService  # noqa: E402
from pronunciation_lab.reader.store import ReaderStore  # noqa: E402
from readerhelpers import ScriptedEngine  # noqa: E402


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


# --- M12 reader (fake engines) ----------------------------------------------

@pytest.fixture
def engines():
    ScriptedEngine.tracker.update(in_flight=0, max=0)
    return {
        "wav2vec2_raw": ScriptedEngine("wav2vec2_raw"),
        "openpronounce": ScriptedEngine("openpronounce"),
        "wavlm": FakeUnavailableEngine("wavlm", "unresolved"),
        "azure_pronunciation": FakeUnavailableEngine("azure_pronunciation", "blocked"),
    }


@pytest.fixture
def lab(tmp_path, engines):
    svc = AnalysisService(notes_path=tmp_path / "notes.jsonl", engine_factory=factory(engines),
                          engine_ids=list(engines), workspace=tmp_path / "ws")
    yield svc
    svc.close()


@pytest.fixture
def reader(tmp_path, lab):
    r = ReaderService(ReaderStore(tmp_path / "store"), lab)
    yield r
    r.close()
