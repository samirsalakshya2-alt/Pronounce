"""Hosted Reader flow against stateless analysis using native Chrome IndexedDB."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from apphelpers import wav_bytes
from benchmark_fakes import factory

from pronunciation_lab.app.server import start_in_thread
from pronunciation_lab.app.service import AnalysisService
from pronunciation_lab.longitudinal.store import ProgressStore
from pronunciation_lab.reader.service import ReaderService
from pronunciation_lab.reader.store import ReaderStore

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(not CHROME.exists(), reason="Google Chrome not installed")
def test_hosted_reader_uses_browser_local_indexeddb_and_stateless_analysis(
    tmp_path, fake_engines, monkeypatch
):
    service = AnalysisService(
        notes_path=tmp_path / "notes.jsonl",
        engine_factory=factory(fake_engines),
        engine_ids=list(fake_engines),
        workspace=tmp_path / "workspace",
    )
    server, thread = start_in_thread(service, stateless_only=True)
    client_url = f"http://127.0.0.1:{server.server_address[1]}/"
    data_dir = tmp_path / "persistent-data"
    data_dir.mkdir()
    service.data_dir = data_dir
    temporary_root = tmp_path / "request-temporary"
    temporary_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary_root))

    def forbid_storage(*args, **kwargs):
        raise AssertionError("stateless hosted Reader must not instantiate server-side stores")

    monkeypatch.setattr(ReaderStore, "__init__", forbid_storage)
    monkeypatch.setattr(ProgressStore, "__init__", forbid_storage)
    monkeypatch.setattr(ReaderService, "__init__", forbid_storage)
    for engine_id in ("openpronounce", "wav2vec2_raw"):
        engine = service._instances[engine_id]
        original = engine.analyze

        def controlled(audio_path, expected_text, *, recording_id, original_path=None,
                       _engine_id=engine_id, _original=original):
            if ((_engine_id == "openpronounce" and expected_text == "OpenPronounce fails.")
                    or (_engine_id == "wav2vec2_raw" and expected_text == "Raw Wav2Vec2 fails.")):
                raise RuntimeError("controlled engine failure")
            return _original(audio_path, expected_text, recording_id=recording_id, original_path=original_path)

        monkeypatch.setattr(engine, "analyze", controlled)

    wav_path = tmp_path / "controlled.wav"
    wav_path.write_bytes(wav_bytes(tmp=tmp_path))
    try:
        result = subprocess.run(
            [
                "node", str(Path(__file__).parent / "js" / "browser_reader_stateless.mjs"),
                str(CHROME), client_url, str(wav_path),
            ],
            capture_output=True, text=True, timeout=300,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        output = json.loads(result.stdout)
        assert output["status"] == "ok"
        assert len(output["results"]) == 3
        assert output["reopened"]["sessions"] == 3
        assert output["reopened"]["readerVisible"]
        assert output["reopened"]["renderedAttempts"] == 1
        assert output["isolatedBrowserSessions"] == 0
        assert all(item["hasSeparateEvidence"] for item in output["results"])
        assert output["results"][0]["attempt"] == "ANALYZED"
        assert output["results"][1]["attempt"] == "ANALYZED"
        assert output["results"][2]["attempt"] == "ANALYSIS_FAILED"
        assert output["results"][0]["openResult"] == "ok"
        assert output["results"][0]["rawResult"] == "ok"
        assert output["results"][1]["openResult"] == "failed"
        assert output["results"][1]["rawResult"] == "ok"
        assert output["results"][2]["openResult"] == "ok"
        assert output["results"][2]["rawResult"] == "failed"
        assert list(data_dir.rglob("*")) == []
        assert list(temporary_root.iterdir()) == []
        assert list(service.workspace.iterdir()) == []
        assert service._analyses == {}
        assert service.recent() == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()
