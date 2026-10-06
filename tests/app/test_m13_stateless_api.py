"""M13-C.1: stateless, dual-engine analysis API."""

import json
import tempfile
from pathlib import Path

import pytest
from apphelpers import wav_bytes

from pronunciation_lab.app import server as server_module
from pronunciation_lab.app import service as service_module
from pronunciation_lab.longitudinal.store import ProgressStore
from pronunciation_lab.reader.store import ReaderStore


ENDPOINT = "/api/stateless/analyze"
BOUNDARY = "pronounce-m13c-boundary"


def multipart(audio=None, text="Think about three.", *, fields=None):
    parts = []
    if fields is None:
        fields = [("target_text", text.encode())]
        if audio is not None:
            fields.append(("audio", audio))
    for name, value in fields:
        if name == "audio":
            headers = (
                b'Content-Disposition: form-data; name="audio"; filename="recording.wav"\r\n'
                b"Content-Type: audio/wav\r\n"
            )
        else:
            headers = f'Content-Disposition: form-data; name="{name}"\r\n'.encode()
        parts.extend((b"--" + BOUNDARY.encode() + b"\r\n", headers, b"\r\n", value, b"\r\n"))
    parts.append(b"--" + BOUNDARY.encode() + b"--\r\n")
    return b"".join(parts)


def post(client, body, content_type=None):
    return client.request(
        "POST",
        ENDPOINT,
        body,
        {"Content-Type": content_type or f"multipart/form-data; boundary={BOUNDARY}"},
    )


def _assert_no_paths_or_temp_names(payload, temporary_dirs):
    strings = []

    def visit(value):
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            strings.append(value)

    visit(payload)
    assert all(not Path(value).is_absolute() for value in strings)
    for directory in temporary_dirs:
        assert all(str(directory) not in value for value in strings)
        assert all(directory.name not in value for value in strings)


def _forbid_storage_construction(*args, **kwargs):
    raise AssertionError("stateless analysis must not construct a reader or longitudinal store")


def test_stateless_request_runs_both_existing_engine_pipelines(fake_server, tmp_path, monkeypatch):
    client, server = fake_server
    audio = wav_bytes(tmp=tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    server.service.data_dir = data_dir
    before = list(server.service.workspace.iterdir())
    temporary_dirs = []
    original_temporary_directory = tempfile.TemporaryDirectory
    temporary_root = tmp_path / "request-temporary"
    temporary_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary_root))

    def tracked_temporary_directory(*args, **kwargs):
        directory = original_temporary_directory(*args, **kwargs)
        temporary_dirs.append(Path(directory.name))
        return directory

    engine_observations = []
    for engine_id, engine in server.service._instances.items():
        original_analyze = engine.analyze

        def observe_audio(audio_path, expected_text, *, recording_id, original_path=None,
                          _engine_id=engine_id, _original=original_analyze):
            analysis_path = Path(audio_path)
            uploaded_path = Path(original_path)
            assert analysis_path.is_file()
            assert uploaded_path.is_file()
            assert analysis_path.parent == uploaded_path.parent
            engine_observations.append((_engine_id, analysis_path, uploaded_path, expected_text))
            return _original(audio_path, expected_text, recording_id=recording_id, original_path=original_path)

        monkeypatch.setattr(engine, "analyze", observe_audio)

    monkeypatch.setattr(ReaderStore, "__init__", _forbid_storage_construction)
    monkeypatch.setattr(ProgressStore, "__init__", _forbid_storage_construction)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", tracked_temporary_directory)

    status, _, raw = post(client, multipart(audio))
    response = json.loads(raw)

    assert status == 200
    assert len(response["request_id"]) == 32
    assert response["target_text"] == "Think about three."
    assert response["duration_ms"] == pytest.approx(1000)
    assert set(response["analyses"]) == {"openpronounce", "wav2vec2_raw"}
    assert response["analyses"]["openpronounce"]["state"] == "ok"
    assert response["analyses"]["wav2vec2_raw"]["state"] == "ok"
    for engine_id, analysis in response["analyses"].items():
        assert analysis["result"]["engine"]["name"] == engine_id
        assert analysis["result"]["engine_evidence"]["provider"] == engine_id
        assert analysis["evidence"]["engine"]["id"] == engine_id
        assert "original_path" not in analysis["result"]["recording"]["audio"]
        assert "analysis_path" not in analysis["result"]["recording"]["audio"]
    assert len(server.service._instances["openpronounce"].calls) == 1
    assert len(server.service._instances["wav2vec2_raw"].calls) == 1
    assert server.service._analyses == {}
    assert server.service.recent() == []
    assert list(server.service.workspace.iterdir()) == before
    assert not list(data_dir.iterdir())
    assert temporary_dirs and all(not directory.exists() for directory in temporary_dirs)
    assert list(temporary_root.iterdir()) == []
    assert {observation[0] for observation in engine_observations} == {"openpronounce", "wav2vec2_raw"}
    assert all(observation[1] != observation[2] for observation in engine_observations)
    assert {observation[1].parent for observation in engine_observations} == set(temporary_dirs)
    _assert_no_paths_or_temp_names(response, temporary_dirs)
    assert all(str(directory) not in raw.decode() for directory in temporary_dirs)


@pytest.mark.parametrize(
    ("body", "content_type", "status_code", "code", "message"),
    [
        (b"not a multipart body", f"multipart/form-data; boundary={BOUNDARY}", 400,
         "bad_multipart", "Send multipart form data with an audio file and target_text."),
        (b"", "application/octet-stream", 415,
         "unsupported_media_type", "Use multipart/form-data for this request."),
        (multipart(b"audio", text=""), None, 400, "text_empty", "Enter the sentence you read aloud."),
        (multipart(fields=[("target_text", b"Think.")]), None, 400,
         "audio_empty", "No audio was received."),
        (multipart(fields=[("audio", b"wav")]), None, 400,
         "text_empty", "Enter the sentence you read aloud."),
        (multipart(fields=[("target_text", b"\xff"), ("audio", b"wav")]), None, 400,
         "bad_multipart", "The target_text field must be UTF-8 text."),
    ],
)
def test_stateless_malformed_and_missing_fields_are_deterministic(
    fake_server, body, content_type, status_code, code, message
):
    client, _ = fake_server
    responses = [post(client, body, content_type) for _ in range(2)]
    assert [status for status, _, _ in responses] == [status_code, status_code]
    payloads = [json.loads(raw) for _, _, raw in responses]
    assert payloads == [{"error": {"code": code, "message": message}}] * 2


def test_stateless_request_rejects_missing_target_before_analysis(fake_server, tmp_path):
    client, server = fake_server
    status, _, raw = post(client, multipart(wav_bytes(tmp=tmp_path), text=""))
    assert status == 400 and json.loads(raw)["error"]["code"] == "text_empty"
    assert all(not engine.calls for engine in server.service._instances.values())


def test_stateless_request_rejects_missing_audio(fake_server):
    client, server = fake_server
    status, _, raw = post(client, multipart(fields=[("target_text", b"Think.")]))
    assert status == 400 and json.loads(raw)["error"]["code"] == "audio_empty"
    assert all(not engine.calls for engine in server.service._instances.values())


def test_stateless_request_rejects_oversized_audio_with_json_413(fake_server, monkeypatch):
    client, server = fake_server
    monkeypatch.setattr(server_module, "MAX_UPLOAD_BYTES", 1000)
    status, _, raw = post(client, multipart(b"x" * 1001))
    assert status == 413
    assert json.loads(raw) == {
        "error": {"code": "payload_too_large", "message": "The audio file is larger than 1 MB."}
    }
    assert all(not engine.calls for engine in server.service._instances.values())


def test_stateless_request_rejects_oversized_multipart_body(fake_server, monkeypatch):
    client, _ = fake_server
    monkeypatch.setattr(server_module, "MAX_UPLOAD_BYTES", 100)
    status, _, raw = post(client, b"x" * (100 + server_module.MAX_MULTIPART_OVERHEAD_BYTES + 1))
    assert status == 413
    assert json.loads(raw) == {
        "error": {"code": "payload_too_large", "message": "The upload is larger than 1 MB."}
    }


def test_stateless_request_rejects_duplicate_fields(fake_server, tmp_path):
    client, _ = fake_server
    body = multipart(
        wav_bytes(tmp=tmp_path),
        fields=[("target_text", b"Think."), ("target_text", b"Again."), ("audio", b"wav")],
    )
    status, _, raw = post(client, body)
    assert status == 400
    assert json.loads(raw) == {
        "error": {"code": "bad_multipart", "message": "The multipart form contains an invalid or duplicate field."}
    }


def test_stateless_failure_is_isolated_and_temp_audio_is_removed(fake_server, tmp_path, monkeypatch):
    client, server = fake_server
    temporary_dirs = []
    original_temporary_directory = tempfile.TemporaryDirectory
    temporary_root = tmp_path / "request-temporary"
    temporary_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary_root))

    def tracked_temporary_directory(*args, **kwargs):
        directory = original_temporary_directory(*args, **kwargs)
        temporary_dirs.append(Path(directory.name))
        return directory

    def fail_with_private_path(*args, **kwargs):
        assert Path(args[0]).is_file()
        raise RuntimeError("failure detail includes /private/server/path")

    monkeypatch.setattr(server.service._instances["wav2vec2_raw"], "analyze", fail_with_private_path)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", tracked_temporary_directory)
    monkeypatch.setattr(ReaderStore, "__init__", _forbid_storage_construction)
    monkeypatch.setattr(ProgressStore, "__init__", _forbid_storage_construction)
    status, _, raw = post(client, multipart(wav_bytes(tmp=tmp_path)))
    payload = json.loads(raw)

    assert status == 200
    assert payload["analyses"]["openpronounce"]["state"] == "ok"
    assert payload["analyses"]["wav2vec2_raw"]["state"] == "failed"
    assert payload["analyses"]["wav2vec2_raw"]["result"]["errors"][0]["message"] == (
        "The engine could not complete this analysis."
    )
    assert "/private/server/path" not in raw.decode()
    assert temporary_dirs and all(not directory.exists() for directory in temporary_dirs)
    assert list(temporary_root.iterdir()) == []
    _assert_no_paths_or_temp_names(payload, temporary_dirs)
    assert server.service._analyses == {}
    assert list(server.service.workspace.iterdir()) == []


def test_stateless_unavailable_engine_is_explicit(fake_server, tmp_path):
    client, server = fake_server
    del server.service._instances["openpronounce"]
    status, _, raw = post(client, multipart(wav_bytes(tmp=tmp_path)))
    payload = json.loads(raw)
    assert status == 200
    assert payload["analyses"]["openpronounce"] == {
        "state": "unavailable",
        "error": {"code": "engine_unavailable", "message": "This analysis engine is not available."},
    }
    assert payload["analyses"]["wav2vec2_raw"]["state"] == "ok"


def test_stateless_temp_audio_is_removed_when_analysis_pipeline_raises(fake_server, tmp_path, monkeypatch):
    client, server = fake_server
    temporary_dirs = []
    original_temporary_directory = tempfile.TemporaryDirectory
    original_pipeline = service_module.analyze_pipeline
    raw_engine = server.service._instances["wav2vec2_raw"]
    temporary_root = tmp_path / "request-temporary"
    temporary_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary_root))

    def tracked_temporary_directory(*args, **kwargs):
        directory = original_temporary_directory(*args, **kwargs)
        temporary_dirs.append(Path(directory.name))
        return directory

    def fail_raw_pipeline(engine, *args, **kwargs):
        if engine is raw_engine:
            assert Path(args[0]).is_file()
            raise RuntimeError("private pipeline failure")
        return original_pipeline(engine, *args, **kwargs)

    monkeypatch.setattr(tempfile, "TemporaryDirectory", tracked_temporary_directory)
    monkeypatch.setattr(service_module, "analyze_pipeline", fail_raw_pipeline)

    status, _, raw = post(client, multipart(wav_bytes(tmp=tmp_path)))
    payload = json.loads(raw)
    assert status == 200
    assert payload["analyses"]["openpronounce"]["state"] == "ok"
    assert payload["analyses"]["wav2vec2_raw"] == {
        "state": "failed",
        "error": {"code": "analysis_failed", "message": "This engine could not complete the analysis."},
    }
    assert "private pipeline failure" not in raw.decode()
    assert temporary_dirs and all(not directory.exists() for directory in temporary_dirs)
    assert list(temporary_root.iterdir()) == []
    _assert_no_paths_or_temp_names(payload, temporary_dirs)


def test_stateless_requests_leave_no_persistent_artifacts_on_success_failure_or_invalid_input(
    fake_server, tmp_path, monkeypatch
):
    client, server = fake_server
    data_dir = tmp_path / "persistent-data"
    data_dir.mkdir()
    server.service.data_dir = data_dir
    temporary_root = tmp_path / "request-temporary"
    temporary_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary_root))
    temporary_dirs = []
    original_temporary_directory = tempfile.TemporaryDirectory

    def tracked_temporary_directory(*args, **kwargs):
        directory = original_temporary_directory(*args, **kwargs)
        temporary_dirs.append(Path(directory.name))
        return directory

    monkeypatch.setattr(tempfile, "TemporaryDirectory", tracked_temporary_directory)
    monkeypatch.setattr(ReaderStore, "__init__", _forbid_storage_construction)
    monkeypatch.setattr(ProgressStore, "__init__", _forbid_storage_construction)

    status, _, raw = post(client, multipart(wav_bytes(tmp=tmp_path), "Successful input."))
    assert status == 200 and set(json.loads(raw)["analyses"]) == {"openpronounce", "wav2vec2_raw"}
    assert list(data_dir.rglob("*")) == []

    for failed_engine, target in (
        ("openpronounce", "Open engine failure."),
        ("wav2vec2_raw", "Raw engine failure."),
    ):
        engine = server.service._instances[failed_engine]
        original_analyze = engine.analyze

        def fail_for_request(audio_path, expected_text, *, recording_id, original_path=None,
                             _original=original_analyze):
            assert Path(audio_path).is_file()
            if expected_text == target:
                raise RuntimeError("failure at /private/engine/path")
            return _original(audio_path, expected_text, recording_id=recording_id, original_path=original_path)

        monkeypatch.setattr(engine, "analyze", fail_for_request)
        status, _, raw = post(client, multipart(wav_bytes(tmp=tmp_path), target))
        payload = json.loads(raw)
        assert status == 200 and payload["analyses"][failed_engine]["state"] == "failed"
        _assert_no_paths_or_temp_names(payload, temporary_dirs)
        assert list(data_dir.rglob("*")) == []
        monkeypatch.setattr(engine, "analyze", original_analyze)

    for body in (
        b"malformed",
        multipart(fields=[("target_text", b"Missing audio.")]),
        multipart(fields=[("audio", b"Missing target.")]),
    ):
        status, _, raw = post(client, body)
        assert status == 400 and "error" in json.loads(raw)
        assert list(data_dir.rglob("*")) == []

    assert server.service._analyses == {}
    assert server.service.recent() == []
    assert temporary_dirs and all(not directory.exists() for directory in temporary_dirs)
    assert list(temporary_root.iterdir()) == []


def test_stateless_requests_are_independent_and_use_distinct_temporary_directories(fake_server, tmp_path, monkeypatch):
    client, server = fake_server
    temporary_root = tmp_path / "request-temporary"
    temporary_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary_root))
    temporary_dirs = []
    original_temporary_directory = tempfile.TemporaryDirectory

    def tracked_temporary_directory(*args, **kwargs):
        directory = original_temporary_directory(*args, **kwargs)
        temporary_dirs.append(Path(directory.name))
        return directory

    monkeypatch.setattr(tempfile, "TemporaryDirectory", tracked_temporary_directory)
    first_audio = wav_bytes(seconds=0.5, tmp=tmp_path)
    second_audio = wav_bytes(seconds=0.8, tmp=tmp_path)
    first_status, _, first_raw = post(client, multipart(first_audio, "First request sentence."))
    second_status, _, second_raw = post(client, multipart(second_audio, "Second request sentence."))
    first, second = json.loads(first_raw), json.loads(second_raw)

    assert first_status == second_status == 200
    assert first["request_id"] != second["request_id"]
    assert first["target_text"] == "First request sentence."
    assert second["target_text"] == "Second request sentence."
    assert first["duration_ms"] == pytest.approx(500)
    assert second["duration_ms"] == pytest.approx(800)
    assert "Second request sentence." not in first_raw.decode()
    assert "First request sentence." not in second_raw.decode()
    assert len(temporary_dirs) == 2 and temporary_dirs[0] != temporary_dirs[1]
    assert all(not directory.exists() for directory in temporary_dirs)
    assert list(temporary_root.iterdir()) == []
    assert server.service._analyses == {}
    assert server.service.recent() == []
    _assert_no_paths_or_temp_names(first, temporary_dirs)
    _assert_no_paths_or_temp_names(second, temporary_dirs)
