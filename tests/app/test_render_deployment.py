"""Render-mode startup and route-isolation tests."""

from apphelpers import Client

from pronunciation_lab.app import __main__ as app_main
from pronunciation_lab.app.server import start_in_thread


def test_stateless_server_only_exposes_ui_status_health_and_analysis(fake_service):
    fake_service.data_dir = None
    fake_service.notes_path = None
    server, thread = start_in_thread(fake_service, stateless_only=True)
    client = Client(server.url)
    try:
        status, health = client.get_json("/healthz")
        assert status == 200 and health == {"status": "ok"}

        status, body = client.get_json("/api/status")
        assert status == 200
        assert body["benchmark_recordings"] == []
        assert body["notes_enabled"] is False
        assert body["stateless_only"] is True

        status, _, page = client.request("GET", "/")
        assert status == 200 and b"Pronounce \xe2\x80\x94 Reader" in page
        assert b"/static/browser-store.js" in page
        assert b"/static/reader-browser.js" in page

        status, _, page = client.request("GET", "/read")
        assert status == 200 and b"Pronounce \xe2\x80\x94 Reader" in page

        for path in (
            "/api/analyses", "/api/sessions",
        ):
            status, data = client.get_json(path)
            assert status == 404 and data["error"]["code"] == "not_found"

        for path in (
            "/static/read.html", "/static/reader.js", "/static/reader-core.js",
            "/static/reader-feedback.js", "/static/browser-store.js",
            "/static/reader-browser.js", "/static/reader-browser-coaching.js",
            "/static/reader-browser-longitudinal.js", "/static/capture-worklet.js",
        ):
            status, _, body = client.request("GET", path)
            assert status == 200 and body

        for path in (
            "/static/index.html", "/static/style.css",
        ):
            status, data = client.get_json(path)
            assert status == 404 and data["error"]["code"] == "not_found"
        status, _, shared_renderer = client.request("GET", "/static/app.js")
        assert status == 200 and b"createEvidenceRenderers" in shared_renderer

        status, _, body = client.request(
            "POST", "/api/analyze", b"",
            {"Content-Type": "application/octet-stream"},
        )
        assert status == 404 and b'"not_found"' in body

        status, _, body = client.request(
            "POST", "/api/stateless/analyze", b"",
            {"Content-Type": "application/octet-stream"},
        )
        assert status == 415 and b'"unsupported_media_type"' in body
        assert server.reader is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_local_mode_keeps_lab_root_and_reader_route(lab, reader):
    server, thread = start_in_thread(lab, reader=reader)
    client = Client(server.url)
    try:
        status, _, root = client.request("GET", "/")
        assert status == 200 and b"Pronunciation Lab" in root
        status, _, hosted_reader = client.request("GET", "/read")
        assert status == 200 and b"Pronounce \xe2\x80\x94 Reader" in hosted_reader
        status, body = client.get_json("/api/status")
        assert status == 200 and "stateless_only" not in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_stateless_cli_uses_render_port_and_skips_reader(monkeypatch, capsys):
    seen = {}

    class FakeService:
        engines = {}

        def __init__(self, **kwargs):
            seen["service_kwargs"] = kwargs

        def close(self):
            seen["service_closed"] = True

    class FakeServer:
        def __init__(self, address, service, **kwargs):
            seen["address"] = address
            seen["server_kwargs"] = kwargs
            self.url = "http://0.0.0.0:43123/"

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            seen["server_closed"] = True

    def unexpected_reader(*args, **kwargs):
        raise AssertionError("stateless deployment must not instantiate ReaderService")

    monkeypatch.setenv("PORT", "43123")
    monkeypatch.setattr(app_main, "AnalysisService", FakeService)
    monkeypatch.setattr(app_main, "LabServer", FakeServer)
    monkeypatch.setattr(app_main, "ReaderService", unexpected_reader)
    monkeypatch.setattr(app_main.signal, "signal", lambda *args: None)

    result = app_main.main([
        "--host", "0.0.0.0", "--stateless-only", "--no-browser", "--no-warmup",
    ])
    assert result == 0
    assert seen["address"] == ("0.0.0.0", 43123)
    assert seen["service_kwargs"] == {}
    assert seen["server_kwargs"]["reader"] is None
    assert seen["server_kwargs"]["stateless_only"] is True
    assert seen["server_closed"] and seen["service_closed"]
    assert "Reader:" not in capsys.readouterr().out
