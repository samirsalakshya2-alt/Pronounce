"""HTTP layer: routes, errors, static whitelist, concurrency, lifecycle."""

import json
import threading

import pytest
from conftest import wav_bytes

from pronunciation_lab.app import server as server_module


def test_index_and_static_assets(fake_server):
    client, _ = fake_server
    for path, ctype, marker in (("/", "text/html", b"Pronunciation Lab"),
                                ("/static/app.js", "javascript", b"playbackArgs"),
                                ("/static/style.css", "text/css", b"--expected")):
        status, headers, body = client.request("GET", path)
        assert status == 200 and ctype in headers["Content-Type"] and marker in body
        assert headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("path", ["/static/../service.py", "/static/server.py", "/static/%2e%2e/service.py",
                                  "/static/", "/nope", "/api/nope", "/static/index.html/x"])
def test_unknown_and_traversal_paths_are_404(fake_server, path):
    client, _ = fake_server
    status, _, body = client.request("GET", path)
    assert status == 404 and json.loads(body)["error"]["code"] == "not_found"


def test_head_has_no_body(fake_server):
    client, _ = fake_server
    status, headers, body = client.request("HEAD", "/api/status")
    assert status == 200 and body == b"" and int(headers["Content-Length"]) > 0


def test_status(fake_server):
    client, _ = fake_server
    status, data = client.get_json("/api/status")
    assert status == 200
    assert {e["id"] for e in data["engines"]} == {"wav2vec2_raw", "openpronounce", "wavlm", "azure_pronunciation"}
    assert data["default_engine"] == "wav2vec2_raw" and data["notes_enabled"] is True
    assert data["limits"]["max_text_chars"] == 300


def test_upload_analyse_fetch_audio_and_notes(fake_server, tmp_path):
    client, _ = fake_server
    audio = wav_bytes(1, tmp=tmp_path)
    status, view = client.upload(audio, "Think about three.")
    assert status == 200 and view["state"] == "ok"
    aid = view["analysis_id"]

    status, again = client.get_json(f"/api/analyses/{aid}")
    assert status == 200 and again == view
    status, headers, body = client.request("GET", view["audio"]["url"])
    assert status == 200 and headers["Content-Type"] == "audio/wav" and body == audio

    status, note = client.post_json("/api/notes", {"analysis_id": aid, "sound_index": 0, "verdict": "as_expected"})
    assert status == 200 and note["note"]["verdict"] == "as_expected"
    status, notes = client.get_json(f"/api/analyses/{aid}/notes")
    assert [n["verdict"] for n in notes["notes"]] == ["as_expected"]

    status, recent = client.get_json("/api/analyses")
    assert recent["analyses"][0]["analysis_id"] == aid


@pytest.mark.parametrize("make, text, engine, code, http", [
    (lambda t: b"", "Think.", None, "audio_empty", 400),
    (lambda t: wav_bytes(1, tmp=t), "", None, "text_empty", 400),
    (lambda t: wav_bytes(1, tmp=t), "Think.", "nope", "engine_unknown", 400),
    (lambda t: wav_bytes(1, tmp=t), "Think.", "wavlm", "engine_unavailable", 409),
    (lambda t: wav_bytes(1, tmp=t), "Think.", "azure_pronunciation", "engine_unavailable", 409),
    (lambda t: wav_bytes(0.1, tmp=t), "Think.", None, "audio_too_short", 400),
    (lambda t: b"garbage" * 50, "Think.", None, "audio_unreadable", 400),
])
def test_upload_errors_are_json(fake_server, tmp_path, make, text, engine, code, http):
    client, _ = fake_server
    status, data = client.upload(make(tmp_path), text, engine)
    assert status == http and data["error"]["code"] == code and data["error"]["message"]


def test_oversized_upload_is_413(fake_server, monkeypatch):
    client, _ = fake_server
    monkeypatch.setattr(server_module, "MAX_UPLOAD_BYTES", 1000)
    status, data = client.upload(b"\x00" * 5000, "Think.")
    assert status == 413 and data["error"]["code"] == "payload_too_large"


@pytest.mark.parametrize("body, code", [(b"{not json", "bad_json"), (b"[1,2]", "bad_json"), (b"\xff\xfe", "bad_json")])
def test_bad_json_bodies(fake_server, body, code):
    client, _ = fake_server
    for path in ("/api/notes", "/api/analyze-benchmark"):
        status, _, raw = client.request("POST", path, body, {"Content-Type": "application/json"})
        assert status == 400 and json.loads(raw)["error"]["code"] == code


def test_bad_content_length(fake_server):
    import http.client

    _, server = fake_server
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=10)
    conn.putrequest("POST", "/api/notes")
    conn.putheader("Content-Length", "abc")
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 400 and json.loads(resp.read())["error"]["code"] == "bad_request"
    conn.close()


def test_unknown_analysis_routes_are_404(fake_server):
    client, _ = fake_server
    for path in ("/api/analyses/" + "0" * 32, "/api/analyses/" + "0" * 32 + "/audio", "/api/analyses/x/notes"):
        status, data = client.get_json(path)
        assert status == 404 and data["error"]["code"] == "analysis_unknown"


def test_benchmark_without_data_is_404(fake_server):
    client, _ = fake_server
    status, data = client.post_json("/api/analyze-benchmark", {"recording_id": "R01"})
    assert status == 404 and data["error"]["code"] == "recording_unknown"


def test_unsupported_method(fake_server):
    client, _ = fake_server
    status, _, _ = client.request("PUT", "/api/status", b"")
    assert status == 501


def test_internal_errors_do_not_leak_tracebacks(fake_server, monkeypatch):
    client, server = fake_server

    def explode():
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(server.service, "status", explode)
    status, _, body = client.request("GET", "/api/status")
    assert status == 500
    assert json.loads(body) == {"error": {"code": "internal_error", "message": "Something went wrong on the server."}}
    assert b"secret" not in body and b"Traceback" not in body
    assert isinstance(server.last_exception, RuntimeError)


def test_concurrent_and_repeated_analyses(fake_server, tmp_path):
    client, server = fake_server
    audio = wav_bytes(1, tmp=tmp_path)
    results, errors = [], []

    def worker(i):
        try:
            status, view = client.upload(audio, f"Think about {i}.")
            results.append((status, view["analysis_id"], view["target_text"]))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == []
    assert len({aid for _, aid, _ in results}) == 8 and all(s == 200 for s, _, _ in results)
    assert sorted(t for *_, t in results) == sorted(f"Think about {i}." for i in range(8))
    assert len(server.service.recent()) == 8


def test_server_shutdown_releases_the_port(fake_service):
    import socket

    from pronunciation_lab.app.server import start_in_thread

    server, thread = start_in_thread(fake_service)
    port = server.server_address[1]
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    assert not thread.is_alive()
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
    finally:
        s.close()


def test_binds_localhost_only_by_default():
    import inspect

    from pronunciation_lab.app import __main__ as main_module

    src = inspect.getsource(main_module.main)
    assert 'default="127.0.0.1"' in src
    assert inspect.signature(server_module.start_in_thread).parameters["host"].default == "127.0.0.1"
