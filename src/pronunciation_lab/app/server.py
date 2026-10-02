"""Local HTTP server for the Pronunciation Lab UI (standard library only).

Binds to 127.0.0.1 by default. Endpoints:

    GET  /                               the single-page UI
    GET  /static/<file>                  UI assets (fixed whitelist)
    GET  /api/status                     engines, ffmpeg, local benchmark recordings
    GET  /api/analyses                   analyses in this session (newest first)
    POST /api/analyze?text=..&engine=..  body: raw audio bytes; X-Filename header
    POST /api/analyze-benchmark          JSON {recording_id, engine, text?}
    GET  /api/analyses/<id>              the analysis view
    GET  /api/analyses/<id>/audio        the analysis WAV (the timeline of all timings)
    GET  /api/analyses/<id>/notes        listening notes for that analysis
    POST /api/notes                      JSON {analysis_id, sound_index, verdict, comment?}

Errors are JSON: {"error": {"code": ..., "message": ...}}.
"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pronunciation_lab.app.audio_input import MAX_UPLOAD_BYTES
from pronunciation_lab.app.service import AnalysisService, UserError

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"index.html", "app.js", "style.css"}
MAX_JSON_BYTES = 64 * 1024
_ANALYSIS_RE = re.compile(r"^/api/analyses/([^/]+)(/audio|/notes)?$")


class Handler(BaseHTTPRequestHandler):
    server: "LabServer"
    protocol_version = "HTTP/1.1"

    # quiet by default; the CLI turns logging on
    def log_message(self, fmt: str, *args: Any) -> None:
        if self.server.verbose:
            super().log_message(fmt, *args)

    # ------------------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, code: str, message: str) -> None:
        self._json(status, {"error": {"code": code, "message": message}})

    def _body(self, limit: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise UserError("bad_request", "Invalid Content-Length.") from None
        if length < 0:
            raise UserError("bad_request", "Invalid Content-Length.")
        if length > limit:
            # drain politely so the client sees the error rather than a reset
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 1 << 16))
                if not chunk:
                    break
                remaining -= len(chunk)
            raise UserError("payload_too_large", f"The upload is larger than {limit // (1024 * 1024) or 1} MB.", 413)
        return self.rfile.read(length) if length else b""

    def _json_body(self) -> dict[str, Any]:
        raw = self._body(MAX_JSON_BYTES)
        try:
            data = json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise UserError("bad_json", "The request body is not valid JSON.") from None
        if not isinstance(data, dict):
            raise UserError("bad_json", "The request body must be a JSON object.")
        return data

    def _dispatch(self, routes) -> None:
        try:
            routes()
        except UserError as exc:
            self._error(exc.status, exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 - never leak a traceback page
            self.server.last_exception = exc
            self._error(500, "internal_error", "Something went wrong on the server.")

    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch(self._get)

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(self._get)

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch(self._post)

    def _get(self) -> None:
        url = urlparse(self.path)
        service = self.server.service
        path = url.path

        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/api/status":
            return self._json(200, service.status())
        if path == "/api/analyses":
            return self._json(200, {"analyses": service.recent()})

        match = _ANALYSIS_RE.match(path)
        if match:
            analysis = service.get(match.group(1))
            if match.group(2) == "/audio":
                body = analysis.audio.analysis_path.read_bytes()
                return self._send(200, body, "audio/wav", {"Content-Disposition": "inline; filename=analysis.wav"})
            if match.group(2) == "/notes":
                return self._json(200, {"notes": service.notes_for(analysis.id)})
            return self._json(200, analysis.view)

        raise UserError("not_found", "Not found.", 404)

    def _static(self, name: str) -> None:
        if name not in STATIC_FILES:
            raise UserError("not_found", "Not found.", 404)
        body = (STATIC_DIR / name).read_bytes()
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type.endswith("javascript"):
            content_type += "; charset=utf-8"
        self._send(200, body, content_type)

    def _post(self) -> None:
        url = urlparse(self.path)
        service = self.server.service
        query = parse_qs(url.query)

        if url.path == "/api/analyze":
            data = self._body(MAX_UPLOAD_BYTES)
            filename = self.headers.get("X-Filename", "upload")
            analysis = service.analyze_upload(
                data, filename,
                (query.get("text") or [""])[0],
                (query.get("engine") or [None])[0],
            )
            return self._json(200, analysis.view)

        if url.path == "/api/analyze-benchmark":
            body = self._json_body()
            analysis = service.analyze_benchmark(
                str(body.get("recording_id", "")), body.get("engine"), body.get("text"),
            )
            return self._json(200, analysis.view)

        if url.path == "/api/notes":
            body = self._json_body()
            note = service.add_note(
                str(body.get("analysis_id", "")), body.get("sound_index"),
                str(body.get("verdict", "")), body.get("comment"),
            )
            return self._json(200, {"note": note})

        raise UserError("not_found", "Not found.", 404)


class LabServer(ThreadingHTTPServer):
    daemon_threads = True
    # socketserver's default listen backlog is 5; a browser opening several
    # connections at once (page assets + API calls) got connection resets.
    request_queue_size = 64

    def __init__(self, address: tuple[str, int], service: AnalysisService, *, verbose: bool = False) -> None:
        super().__init__(address, Handler)
        self.service = service
        self.verbose = verbose
        self.last_exception: Exception | None = None

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"


def start_in_thread(service: AnalysisService, host: str = "127.0.0.1", port: int = 0) -> tuple[LabServer, threading.Thread]:
    server = LabServer((host, port), service)
    thread = threading.Thread(target=server.serve_forever, name="pronunciation-lab-server", daemon=True)
    thread.start()
    return server, thread

