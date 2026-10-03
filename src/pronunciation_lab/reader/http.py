"""HTTP routes for the M12 reader (mounted by app.server; existing routes are unchanged).

    POST /api/articles                                        {text, title?, source?}
    GET  /api/sessions                                        recent reading sessions
    POST /api/sessions                                        {session_id, article_id, engine?}
    GET  /api/sessions/<sid>[?since=<rev>]                    snapshot (or {"unchanged": true})
    POST /api/sessions/<sid>/state                            {action: start|pause|resume|stop|finish|reopen, run_id?}
    POST /api/sessions/<sid>/attempts/<aid>/start             {segment_id, run_id, sample_rate, start_sample}
    POST /api/sessions/<sid>/attempts/<aid>/audio             body: 16-bit PCM mono WAV; X-Capture header (JSON) → 202
    GET  /api/sessions/<sid>/attempts/<aid>                   attempt, jobs, views
    GET  /api/sessions/<sid>/attempts/<aid>/audio             the attempt's analysis WAV (playback timeline)
    POST /api/sessions/<sid>/attempts/<aid>/disposition       {value: kept|discarded|rerecord_requested}
    POST /api/sessions/<sid>/attempts/<aid>/compare           queue the other local engine (comparison job)
    GET  /api/sessions/<sid>/attempts/<aid>/comparison        both engines' evidence, paired (never merged)
    POST /api/sessions/<sid>/attempts/<aid>/jobs/<jid>/retry  retry a failed analysis
    GET  /api/sessions/<sid>/summary                          the single-session reading summary
"""

from __future__ import annotations

import json
import re
from typing import Any

from pronunciation_lab.app.service import UserError
from pronunciation_lab.reader.service import MAX_UPLOAD_BYTES

_ID = r"([0-9a-f]{32})"
_SESSION = re.compile(rf"/api/sessions/{_ID}")
_SESSION_STATE = re.compile(rf"/api/sessions/{_ID}/state")
_SUMMARY = re.compile(rf"/api/sessions/{_ID}/summary")
_ATTEMPT = re.compile(rf"/api/sessions/{_ID}/attempts/{_ID}(/start|/audio|/disposition|/compare|/comparison)?")
_RETRY = re.compile(rf"/api/sessions/{_ID}/attempts/{_ID}/jobs/{_ID}/retry")


def _reader(handler):
    reader = getattr(handler.server, "reader", None)
    if reader is None:
        raise UserError("reader_unavailable", "The reader is not enabled on this server.", 404)
    return reader


def handle_get(handler, path: str, query: dict[str, list[str]]) -> bool:
    if not path.startswith("/api/sessions"):
        return False
    reader = _reader(handler)
    if path == "/api/sessions":
        handler._json(200, {"sessions": reader.list_sessions()})
        return True
    if m := _SESSION.fullmatch(path):
        since = (query.get("since") or [None])[0]
        rev = int(since) if since is not None and since.isdigit() else None
        handler._json(200, reader.snapshot(m.group(1), rev))
        return True
    if m := _SUMMARY.fullmatch(path):
        handler._json(200, {"summary": reader.summary(m.group(1))})
        return True
    if m := _ATTEMPT.fullmatch(path):
        sid, aid, tail = m.groups()
        if tail is None:
            handler._json(200, reader.attempt_detail(sid, aid))
            return True
        if tail == "/audio":
            body = reader.attempt_audio(sid, aid).read_bytes()
            handler._send(200, body, "audio/wav", {"Content-Disposition": "inline; filename=analysis.wav"})
            return True
        if tail == "/comparison":
            handler._json(200, reader.comparison(sid, aid))
            return True
    return False


def handle_post(handler, path: str) -> bool:
    if path == "/api/articles":
        body = handler._json_body()
        handler._json(200, {"article": _reader(handler).create_article(body.get("text"), body.get("title"),
                                                                         body.get("source"))})
        return True
    if not path.startswith("/api/sessions"):
        return False
    reader = _reader(handler)
    if path == "/api/sessions":
        body = handler._json_body()
        handler._json(200, reader.create_session(body.get("session_id"), body.get("article_id"), body.get("engine")))
        return True
    if m := _SESSION_STATE.fullmatch(path):
        body = handler._json_body()
        handler._json(200, reader.session_action(m.group(1), body.get("action"), body.get("run_id")))
        return True
    if m := _RETRY.fullmatch(path):
        _optional_json(handler)  # always consume the body: leftovers would corrupt the next keep-alive request
        handler._json(202, {"job": reader.retry(*m.groups())})
        return True
    if m := _ATTEMPT.fullmatch(path):
        sid, aid, tail = m.groups()
        if tail == "/start":
            handler._json(200, {"attempt": reader.start_attempt(sid, aid, handler._json_body())})
            return True
        if tail == "/audio":
            meta = _capture_header(handler.headers.get("X-Capture"))
            data = handler._body(MAX_UPLOAD_BYTES)
            handler._json(202, reader.upload_audio(sid, aid, data, meta))
            return True
        if tail == "/disposition":
            handler._json(200, {"attempt": reader.set_disposition(sid, aid, handler._json_body().get("value"))})
            return True
        if tail == "/compare":
            _optional_json(handler)
            handler._json(202, {"job": reader.request_comparison(sid, aid)})
            return True
    return False


def _optional_json(handler) -> dict[str, Any]:
    return {} if handler.headers.get("Content-Length") in (None, "0") else handler._json_body()


def _capture_header(raw: str | None) -> dict[str, Any]:
    try:
        meta = json.loads(raw or "")
    except json.JSONDecodeError as exc:
        raise UserError("capture_invalid", "Missing or invalid X-Capture header.") from exc
    if not isinstance(meta, dict):
        raise UserError("capture_invalid", "Missing or invalid X-Capture header.")
    return meta
