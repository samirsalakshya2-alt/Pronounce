"""Local HTTP server for the Pronunciation Lab UI (standard library only).

Binds to 127.0.0.1 by default. Endpoints:

    GET  /                               the single-page UI
    GET  /static/<file>                  UI assets (fixed whitelist)
    GET  /healthz                        process health check
    GET  /api/status                     engines, ffmpeg, local benchmark recordings
    GET  /api/analyses                   analyses in this session (newest first)
    POST /api/analyze?text=..&engine=..  body: raw audio bytes; X-Filename header
    POST /api/stateless/analyze          multipart {target_text, audio}; no retained analysis
    POST /api/analyze-benchmark          JSON {recording_id, engine, text?}
    GET  /api/analyses/<id>              the analysis view
    GET  /api/analyses/<id>/audio        the analysis WAV (the timeline of all timings)
    GET  /api/analyses/<id>/notes        listening notes for that analysis
    POST /api/notes                      JSON {analysis_id, sound_index, verdict, comment?}

M12 reader routes (/read, /api/articles, /api/sessions/...) are in
`pronunciation_lab.reader.http` and only active when a ReaderService is attached.

Errors are JSON: {"error": {"code": ..., "message": ...}}.
"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from pronunciation_lab.app.audio_input import MAX_UPLOAD_BYTES
from pronunciation_lab.app.service import AnalysisService, UserError
from pronunciation_lab.reader import http as reader_http

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"index.html", "app.js", "style.css",
                # M12 reader
                "read.html", "reader.js", "reader.css", "reader-core.js", "reader-feedback.js", "capture-worklet.js",
                # M13 browser-local structured persistence
                "browser-store.js", "reader-browser.js", "reader-browser-coaching.js",
                "reader-browser-longitudinal.js"}
MAX_JSON_BYTES = 64 * 1024
MAX_MULTIPART_OVERHEAD_BYTES = 64 * 1024
_ANALYSIS_RE = re.compile(r"^/api/analyses/([^/]+)(/audio|/notes)?$")


def _stateless_upload(content_type: str, body: bytes) -> tuple[str, bytes, str, list[str]]:
    """Decode the exact multipart fields accepted by the stateless analysis endpoint."""
    try:
        content_type_header = content_type.encode("ascii")
    except UnicodeEncodeError:
        raise UserError("bad_multipart", "The multipart form is malformed.") from None
    header = (
        b"Content-Type: " + content_type_header + b"\r\n"
        b"MIME-Version: 1.0\r\n"
        b"\r\n"
    )
    message = BytesParser(policy=email_policy).parsebytes(header + body)
    if message.get_content_type() != "multipart/form-data" or not message.get_boundary():
        raise UserError("bad_multipart", "Send multipart form data with an audio file and target_text.")

    parts: dict[str, tuple[Any, bytes]] = {}
    try:
        for part in message.iter_parts():
            if part.get_content_disposition() != "form-data":
                raise UserError("bad_multipart", "The multipart form contains an invalid field.")
            name = part.get_param("name", header="content-disposition")
            if name not in ("audio", "target_text", "article_sentences") or name in parts:
                raise UserError("bad_multipart", "The multipart form contains an invalid or duplicate field.")
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes):
                raise UserError("bad_multipart", "The multipart form contains an invalid field.")
            parts[name] = (part, payload)
    except (AttributeError, TypeError, ValueError):
        raise UserError("bad_multipart", "The multipart form is malformed.") from None
    if not parts:
        raise UserError("bad_multipart", "Send multipart form data with an audio file and target_text.")

    if "target_text" not in parts:
        raise UserError("text_empty", "Enter the sentence you read aloud.")
    if "audio" not in parts:
        raise UserError("audio_empty", "No audio was received.")

    target_part, target_bytes = parts["target_text"]
    charset = target_part.get_content_charset()
    if charset and charset.lower().replace("_", "-") not in ("utf-8", "utf8"):
        raise UserError("bad_multipart", "The target_text field must be UTF-8 text.")
    try:
        target_text = target_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise UserError("bad_multipart", "The target_text field must be UTF-8 text.") from None

    article_sentences: list[str] = []
    if "article_sentences" in parts:
        context_part, context_bytes = parts["article_sentences"]
        context_charset = context_part.get_content_charset()
        if context_charset and context_charset.lower().replace("_", "-") not in ("utf-8", "utf8"):
            raise UserError("bad_multipart", "The article_sentences field must be UTF-8 JSON.")
        try:
            decoded_context = json.loads(context_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise UserError("bad_multipart", "The article_sentences field must be UTF-8 JSON.") from None
        if (
            not isinstance(decoded_context, list)
            or len(decoded_context) > 6
            or any(not isinstance(item, str) or len(item) > 300 for item in decoded_context)
        ):
            raise UserError("bad_multipart", "The article_sentences field must contain at most six sentences.")
        article_sentences = decoded_context

    audio_part, audio = parts["audio"]
    filename = audio_part.get_filename() or "upload.bin"
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1] or "upload.bin"
    return target_text, audio, filename, article_sentences


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

        if path == "/healthz":
            return self._json(200, {"status": "ok"})
        if self.server.stateless_only and not (
            path in ("/", "/read", "/read/", "/api/status") or path.startswith("/static/")
        ):
            raise UserError("not_found", "Not found.", 404)
        if path in ("/", "/index.html"):
            return self._static("read.html" if self.server.stateless_only else "index.html")
        if path in ("/read", "/read/"):
            return self._static("read.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/api/status":
            status = service.status()
            if self.server.stateless_only:
                status["stateless_only"] = True
            return self._json(200, status)
        if path == "/api/analyses":
            return self._json(200, {"analyses": service.recent()})

        if reader_http.handle_get(self, path, parse_qs(url.query)):
            return None

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
        if self.server.stateless_only and name not in {
            "read.html", "reader.js", "reader.css", "reader-core.js", "reader-feedback.js",
            "capture-worklet.js", "browser-store.js", "reader-browser.js",
            "reader-browser-coaching.js", "reader-browser-longitudinal.js", "app.js",
        }:
            raise UserError("not_found", "Not found.", 404)
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

        if self.server.stateless_only and url.path != "/api/stateless/analyze":
            self.close_connection = True
            raise UserError("not_found", "Not found.", 404)

        if url.path == "/api/stateless/analyze":
            content_type = self.headers.get("Content-Type", "")
            if not content_type.lower().startswith("multipart/form-data"):
                raise UserError("unsupported_media_type", "Use multipart/form-data for this request.", 415)
            body = self._body(MAX_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD_BYTES)
            text, audio, filename, article_sentences = _stateless_upload(content_type, body)
            if len(audio) > MAX_UPLOAD_BYTES:
                size_mb = MAX_UPLOAD_BYTES // (1024 * 1024) or 1
                raise UserError("payload_too_large", f"The audio file is larger than {size_mb} MB.", 413)
            return self._json(200, service.analyze_stateless(audio, filename, text, article_sentences))

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

        m = re.fullmatch(r"/api/analyses/([0-9a-f]{32})/compare", url.path)
        if m:
            body = {} if self.headers.get("Content-Length") in (None, "0") else self._json_body()
            return self._json(200, service.compare_engines(m.group(1), body.get("engine")))

        if reader_http.handle_post(self, url.path):
            return None

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

    def __init__(self, address: tuple[str, int], service: AnalysisService, *, verbose: bool = False,
                 reader: Any = None, stateless_only: bool = False) -> None:
        super().__init__(address, Handler)
        self.service = service
        self.reader = reader  # M12 ReaderService, optional
        self.stateless_only = stateless_only
        self.verbose = verbose
        self.last_exception: Exception | None = None

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"


def start_in_thread(service: AnalysisService, host: str = "127.0.0.1", port: int = 0,
                    reader: Any = None, stateless_only: bool = False) -> tuple[LabServer, threading.Thread]:
    server = LabServer((host, port), service, reader=reader, stateless_only=stateless_only)
    thread = threading.Thread(target=server.serve_forever, name="pronunciation-lab-server", daemon=True)
    thread.start()
    return server, thread
