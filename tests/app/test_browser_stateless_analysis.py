"""Browser-to-stateless-API integration through native IndexedDB in Chrome."""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from apphelpers import wav_bytes

from pronunciation_lab.longitudinal.store import ProgressStore
from pronunciation_lab.app.service import UserError
from pronunciation_lab.reader.store import ReaderStore

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(not CHROME.exists(), reason="Google Chrome not installed")
def test_browser_analysis_persists_original_audio_and_independent_engine_results(
    fake_server, tmp_path, monkeypatch
):
    client, server = fake_server
    wav_path = tmp_path / "browser-upload.wav"
    wav_path.write_bytes(wav_bytes(tmp=tmp_path))
    data_dir = tmp_path / "persistent-data"
    data_dir.mkdir()
    server.service.data_dir = data_dir

    def forbid_store(*args, **kwargs):
        raise AssertionError("stateless browser analysis must not construct a persistence store")

    monkeypatch.setattr(ReaderStore, "__init__", forbid_store)
    monkeypatch.setattr(ProgressStore, "__init__", forbid_store)
    for engine_id in ("openpronounce", "wav2vec2_raw"):
        engine = server.service._instances[engine_id]
        original_analyze = engine.analyze

        def controlled_analyze(audio_path, expected_text, *, recording_id, original_path=None,
                               _engine_id=engine_id, _original=original_analyze):
            should_fail = (
                (_engine_id == "openpronounce" and expected_text == "OpenPronounce fails.")
                or (_engine_id == "wav2vec2_raw" and expected_text == "Raw Wav2Vec2 fails.")
            )
            if should_fail:
                raise RuntimeError("controlled engine failure")
            return _original(audio_path, expected_text, recording_id=recording_id, original_path=original_path)

        monkeypatch.setattr(engine, "analyze", controlled_analyze)

    original_stateless = server.service.analyze_stateless

    def fail_transport(data, filename, text, article_sentences=None):
        if text == "Transport request failure.":
            raise UserError("server_error", "The analysis service could not complete this request.", 503)
        return original_stateless(data, filename, text, article_sentences)

    monkeypatch.setattr(server.service, "analyze_stateless", fail_transport)
    result = subprocess.run(
        [
            "node",
            str(Path(__file__).parent / "js" / "browser_stateless_analysis.mjs"),
            str(CHROME),
            client.base + "/",
            str(wav_path),
        ],
        capture_output=True,
        text=True,
        timeout=240,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    browser_result = json.loads(result.stdout)
    assert browser_result["status"] == "ok"
    assert len(browser_result["timings"]) == 3
    assert all(math.isfinite(item["request_ms"]) and item["request_ms"] > 0
               for item in browser_result["timings"])
    assert all(math.isfinite(item["persistence_ms"]) and item["persistence_ms"] >= 0
               for item in browser_result["timings"])
    assert list(data_dir.rglob("*")) == []
    assert server.service._analyses == {}
    assert server.service.recent() == []
    assert list(server.service.workspace.iterdir()) == []
