"""M5 in the app: service integration, engine comparison endpoint, negative paths (fake engines)."""

import json

import pytest
from apphelpers import wav_bytes
from benchmark_fakes import FakeEngine, FakeUnavailableEngine, factory

from pronunciation_lab.app.coach import build_coach
from pronunciation_lab.app.service import AnalysisService, UserError
from pronunciation_lab.benchmark.schema import PronunciationResult


def test_every_analysis_carries_the_reduction_layer_and_m4_is_unchanged(fake_service, tmp_path):
    a = fake_service.analyze_upload(wav_bytes(1, tmp=tmp_path), "rec.wav", "Think about three.", None)
    red = a.view["reduction"]
    assert red["state"] == "ok" and red["integrity"]["ok"] and red["engine"]["id"] == "wav2vec2_raw"
    assert "timing_ms" not in red and a.view["processing"]["reduction_ms"] >= 0.0
    coach = build_coach(PronunciationResult.model_validate_json(a.result_json))
    coach.pop("_timing_ms")
    assert json.loads(json.dumps(coach)) == json.loads(json.dumps(a.view["coach"]))


def test_compare_runs_the_other_engine_once_on_the_same_audio(fake_service, fake_engines, tmp_path):
    a = fake_service.analyze_upload(wav_bytes(1, tmp=tmp_path), "rec.wav", "Think about three.", None)
    cmp = fake_service.compare_engines(a.id)
    assert cmp["engines"] == ["wav2vec2_raw", "openpronounce"] and cmp["integrity"]["ok"]
    assert set(cmp["analysis_ids"]) == {"wav2vec2_raw", "openpronounce"} and cmp["analysis_ids"]["wav2vec2_raw"] == a.id
    other = fake_service.get(cmp["analysis_ids"]["openpronounce"])
    assert other.audio.original_sha256 == a.audio.original_sha256 and other.target_text == a.target_text
    assert set(cmp["reductions"]) == set(cmp["processing"]) == {"wav2vec2_raw", "openpronounce"}
    calls = len(fake_engines["openpronounce"].calls)
    again = fake_service.compare_engines(a.id)
    assert len(fake_engines["openpronounce"].calls) == calls and again["analysis_ids"] == cmp["analysis_ids"]
    # starting from the other side gives the same pairing, in the same engine order
    assert fake_service.compare_engines(other.id)["engines"] == ["wav2vec2_raw", "openpronounce"]


@pytest.mark.parametrize("engine", ["wav2vec2_raw", "wavlm", "azure_pronunciation", "nope"])
def test_compare_only_with_the_other_local_engine(fake_service, tmp_path, engine):
    a = fake_service.analyze_upload(wav_bytes(1, tmp=tmp_path), "rec.wav", "Think.", None)
    with pytest.raises(UserError) as e:
        fake_service.compare_engines(a.id, engine)
    assert e.value.code == "compare_unsupported"


def test_compare_unknown_analysis(fake_service):
    with pytest.raises(UserError) as e:
        fake_service.compare_engines("0" * 32)
    assert e.value.code == "analysis_unknown" and e.value.status == 404


def test_compare_without_evidence_is_refused(fake_service, tmp_path):
    a = fake_service.analyze_upload(wav_bytes(1, tmp=tmp_path), "rec.wav", "Think.", None)
    a.view["reduction"]["state"] = "no_evidence"
    with pytest.raises(UserError) as e:
        fake_service.compare_engines(a.id)
    assert e.value.code == "compare_unsupported"


def test_compare_when_the_other_engine_is_unavailable(tmp_path):
    engines = {"wav2vec2_raw": FakeEngine("wav2vec2_raw"),
               "openpronounce": FakeUnavailableEngine("openpronounce", "blocked")}
    svc = AnalysisService(notes_path=tmp_path / "n.jsonl", engine_factory=factory(engines), engine_ids=list(engines))
    try:
        a = svc.analyze_upload(wav_bytes(1, tmp=tmp_path), "rec.wav", "Think.", None)
        with pytest.raises(UserError) as e:
            svc.compare_engines(a.id)
        assert e.value.code == "engine_unavailable" and e.value.status == 409
    finally:
        svc.close()


def test_compare_endpoint(fake_server, tmp_path):
    client, _ = fake_server
    status, view = client.upload(wav_bytes(1, tmp=tmp_path), "Think about three.")
    assert status == 200 and view["reduction"]["state"] == "ok"
    path = f"/api/analyses/{view['analysis_id']}/compare"
    status, _, body = client.request("POST", path)  # no body
    data = json.loads(body)
    assert status == 200 and data["engines"] == ["wav2vec2_raw", "openpronounce"] and data["integrity"]["ok"]
    status, data = client.post_json(path, {"engine": "openpronounce"})
    assert status == 200
    status, data = client.post_json(path, {"engine": "wavlm"})
    assert status == 400 and data["error"]["code"] == "compare_unsupported"
    status, _, body = client.request("POST", path, b"{nope", {"Content-Type": "application/json"})
    assert status == 400 and json.loads(body)["error"]["code"] == "bad_json"
    status, data = client.post_json("/api/analyses/" + "0" * 32 + "/compare", {})
    assert status == 404 and data["error"]["code"] == "analysis_unknown"
    status, _, _ = client.request("POST", "/api/analyses/xyz/compare")
    assert status == 404
