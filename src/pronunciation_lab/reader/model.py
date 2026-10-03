"""M12 domain model: Article → ReadingSession → Segment → RecordingAttempt → AnalysisJob → AnalysisResult.

Entities are plain JSON-able dicts (persisted as JSON files). Their state
machines are explicit transition tables; any other transition raises
`IllegalTransition`. Identity rules:

* session_id, attempt_id — client-generated UUID4 hex (32 lower-case hex), so
  capture never waits for the server; the server validates and rejects reuse;
* article_id, job_id — server-generated UUID4 hex;
* segment_id — "{article_id}:{index:04d}", derived, never stored independently.

Every attempt carries session_id, segment_id, attempt_id and target_text (the
segment text at capture time). Audio ownership is the half-open sample interval
[start_sample, end_sample) of one capture run.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from pronunciation_lab.reader.segmenter import SEGMENTER_VERSION, SegmentSpan, has_letter

MODEL_VERSION = "m12.1"
ID_RE = re.compile(r"[0-9a-f]{32}")
SEGMENT_ID_RE = re.compile(r"([0-9a-f]{32}):(\d{4})")
ENGINES = ("wav2vec2_raw", "openpronounce")
PLAYBACK_TIMELINE = "analysis_wav"


class IllegalTransition(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    return uuid.uuid4().hex


def valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(ID_RE.fullmatch(value))


def segment_id(article_id: str, index: int) -> str:
    return f"{article_id}:{index:04d}"


# ----------------------------------------------------------------------
# State machines
# ----------------------------------------------------------------------

SESSION_TRANSITIONS = {
    "READY": {"READING"},
    "READING": {"PAUSED", "STOPPED", "FINISHED", "INTERRUPTED"},
    "PAUSED": {"READING", "STOPPED", "FINISHED", "INTERRUPTED"},
    "STOPPED": {"READING", "FINISHED", "INTERRUPTED"},
    "INTERRUPTED": {"PAUSED", "READING", "STOPPED", "FINISHED"},
    "FINISHED": {"SUMMARIZED", "READING"},
    "SUMMARIZED": {"READING", "SUMMARIZED"},
}
# Session actions (§4): what each one does to the session state.
SESSION_ACTIONS = {"start": "READING", "resume": "READING", "pause": "PAUSED", "stop": "STOPPED", "finish": "FINISHED"}

ATTEMPT_TRANSITIONS = {
    "CAPTURING": {"RECORDED", "TOO_SHORT", "REJECTED", "INTERRUPTED"},
    "INTERRUPTED": {"RECORDED", "TOO_SHORT", "REJECTED"},  # audio arrived after all (late upload)
    "RECORDED": {"QUEUED"},
    "QUEUED": {"ANALYZING"},
    "ANALYZING": {"ANALYZED", "ANALYSIS_FAILED", "QUEUED"},  # QUEUED: restart recovery
    "ANALYSIS_FAILED": {"QUEUED"},  # retry
    "ANALYZED": set(),
    "TOO_SHORT": set(),
    "REJECTED": set(),
}
ATTEMPT_STATES = tuple(ATTEMPT_TRANSITIONS)
DISPOSITIONS = ("kept", "discarded", "rerecord_requested")
END_REASONS = ("switched", "paused", "stopped", "finalized", "limit_reached", "page_hidden", "interrupted", "finished")

JOB_TRANSITIONS = {
    "QUEUED": {"RUNNING", "CANCELLED"},
    "RUNNING": {"SUCCEEDED", "FAILED", "QUEUED"},  # QUEUED: restart recovery
    "SUCCEEDED": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}
JOB_KINDS = ("primary", "comparison")
TERMINAL_JOB_STATES = ("SUCCEEDED", "FAILED", "CANCELLED")

TARGET_STATES = ("NOT_CHECKED", "MATCH", "AMBIGUOUS", "MISMATCH", "NOT_APPLICABLE")


def transition(entity: dict[str, Any], table: dict[str, set[str]], new: str, kind: str) -> None:
    old = entity["state"]
    if new not in table.get(old, set()):
        raise IllegalTransition(f"{kind} {entity.get('id', '?')}: {old} → {new} is not allowed")
    entity["state"] = new
    entity["updated_at"] = now()


# ----------------------------------------------------------------------
# Constructors
# ----------------------------------------------------------------------

def make_article(text: str, title: str | None, spans: list[SegmentSpan], *, source: str | None = None) -> dict[str, Any]:
    import hashlib

    aid = new_id()
    return {
        "id": aid,
        "title": (title or "").strip()[:200] or "Untitled article",
        "source": (source or "").strip()[:200] or None,
        "text": text,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "segmenter_version": SEGMENTER_VERSION,
        "created_at": now(),
        "segments": [
            {"id": segment_id(aid, s.index), "index": s.index, "paragraph_index": s.paragraph_index,
             "char_start": s.char_start, "char_end": s.char_end, "text": s.text, "readable": has_letter(s.text)}
            for s in spans
        ],
    }


def make_session(session_id: str, article: dict[str, Any], engine: str) -> dict[str, Any]:
    return {
        "id": session_id,
        "article_id": article["id"],
        "engine_default": engine,
        "state": "READY",
        "segmenter_version": article["segmenter_version"],
        "model_version": MODEL_VERSION,
        "created_at": now(),
        "updated_at": now(),
        "rev": 0,
        "attempt_ids": [],
        "current_run_id": None,
    }


def make_attempt(session: dict[str, Any], segment: dict[str, Any], attempt_id: str, attempt_number: int,
                 capture: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": attempt_id,
        "session_id": session["id"],
        "segment_id": segment["id"],
        "attempt_number": attempt_number,
        "target_text": segment["text"],
        "capture": capture,
        "audio": None,
        "state": "CAPTURING",
        "error": None,
        "job_ids": [],
        "user_disposition": None,  # no decision yet; "kept" only when the user chooses to keep it
        "created_at": now(),
        "updated_at": now(),
    }


def make_job(attempt: dict[str, Any], engine: str, kind: str) -> dict[str, Any]:
    return {
        "id": new_id(),
        "attempt_id": attempt["id"],
        "session_id": attempt["session_id"],
        "segment_id": attempt["segment_id"],
        "engine_id": engine,
        "kind": kind,
        "state": "QUEUED",
        "try_count": 1,
        "enqueued_at": now(),
        "started_at": None,
        "finished_at": None,
        "error": None,
        "target_confirmation": {"state": "NOT_CHECKED"},
        "pipeline_versions": None,
        "updated_at": now(),
    }


def playback_reference(attempt: dict[str, Any], job: dict[str, Any] | None, play_ms, span_ms, kind: str) -> dict[str, Any]:
    """Every Listen control resolves to one attempt and one timeline (the attempt's analysis WAV)."""
    return {
        "session_id": attempt["session_id"],
        "segment_id": attempt["segment_id"],
        "attempt_id": attempt["id"],
        "job_id": job["id"] if job else None,
        "timeline": PLAYBACK_TIMELINE,
        "kind": kind,
        "play_ms": play_ms,
        "span_ms": span_ms,
        "url": f"/api/sessions/{attempt['session_id']}/attempts/{attempt['id']}/audio",
    }


def segment_state(attempts: list[dict[str, Any]], jobs: dict[str, dict[str, Any]]) -> str:
    """A segment's state is derived from its attempts, never stored."""
    if not attempts:
        return "UNREAD"
    latest = attempts[-1]
    st = latest["state"]
    if st in ("CAPTURING",):
        return "RECORDING"
    if st in ("RECORDED", "QUEUED", "ANALYZING"):
        return "PROCESSING"
    if st in ("ANALYSIS_FAILED", "TOO_SHORT", "REJECTED", "INTERRUPTED"):
        return "NEEDS_ATTENTION"
    primary = next((jobs[j] for j in latest["job_ids"] if j in jobs and jobs[j]["kind"] == "primary"), None)
    target = (primary or {}).get("target_confirmation", {}).get("state")
    if target in ("AMBIGUOUS", "MISMATCH"):
        return "NEEDS_ATTENTION"
    return "FEEDBACK_READY"
