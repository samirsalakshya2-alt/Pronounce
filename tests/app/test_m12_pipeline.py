"""M12 phase 1: the analysis pipeline is callable without HTTP and is the lab's pipeline."""

import json
import threading

import pytest
from apphelpers import DATA_DIR, wav_bytes

from pronunciation_lab.app.audio_input import prepare_audio
from pronunciation_lab.app.pipeline import analyze_pipeline, build_analysis_view
from pronunciation_lab.benchmark.schema import PronunciationResult

LAB_ONLY = ("analysis_id", "source", "audio", "processing")


def stable(view):
    view = json.loads(json.dumps(view))
    for k in LAB_ONLY:
        view.pop(k, None)
    return view


def test_pipeline_without_http_matches_the_lab(fake_service, fake_engines, tmp_path):
    data = wav_bytes(1, tmp=tmp_path)
    lab = fake_service.analyze_upload(data, "rec.wav", "Think about three.", None)
    audio = prepare_audio(data, "rec.wav", tmp_path / "ws")
    calls = []
    result, view = analyze_pipeline(fake_engines["wav2vec2_raw"], audio.analysis_path, "Think about three.",
                                    recording_id="x", lock=threading.Lock(), on_inference_done=lambda: calls.append(1))
    assert calls == [1] and isinstance(result, PronunciationResult)
    assert stable(view) == stable(lab.view)
    assert set(view) >= {"coach", "reduction", "words"} and view["reduction"]["integrity"]["ok"]


def test_view_is_rebuildable_from_result_json(fake_service, tmp_path):
    lab = fake_service.analyze_upload(wav_bytes(1, tmp=tmp_path), "rec.wav", "Think about three.", None)
    rebuilt = build_analysis_view(PronunciationResult.model_validate_json(lab.result_json), lab.audio.analysis_path)
    assert stable(rebuilt) == stable(lab.view)


def test_pipeline_holds_the_lock_only_for_inference(fake_engines, tmp_path):
    audio = prepare_audio(wav_bytes(1, tmp=tmp_path), "rec.wav", tmp_path / "ws")
    lock = threading.Lock()
    seen = []
    analyze_pipeline(fake_engines["openpronounce"], audio.analysis_path, "Think.", recording_id="x", lock=lock,
                     on_inference_done=lambda: seen.append(lock.locked()))
    assert seen == [True] and not lock.locked()


@pytest.mark.skipif(not (DATA_DIR / "benchmark_wav" / "R01.wav").exists(), reason="benchmark data missing")
@pytest.mark.parametrize("engine", ["wav2vec2_raw", "openpronounce"])
def test_real_r01_pipeline_equals_lab_for_both_engines(real_server, engine, tmp_path):
    client, svc = real_server
    status, lab = client.post_json("/api/analyze-benchmark", {"recording_id": "R01", "engine": engine})
    assert status == 200
    data = (DATA_DIR / "benchmark_wav" / "R01.wav").read_bytes()
    audio = prepare_audio(data, "R01.wav", tmp_path / "ws")
    _, view = analyze_pipeline(svc._instances[engine], audio.analysis_path, lab["target_text"],
                               recording_id="m12", lock=svc._lock)
    assert view["engine"]["id"] == engine
    assert stable(view) == stable(lab)
