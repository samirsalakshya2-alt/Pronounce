"""Durable, local JSON store for the M12 reader (no SQLite).

Layout under `root` (default ~/.pronunciation_lab/reader, outside the repository):

    articles/{article_id}.json
    sessions/{session_id}/session.json          atomic rewrite
    sessions/{session_id}/events.jsonl          append-only; a torn last line is skipped
    sessions/{session_id}/attempts/{attempt_id}/attempt.json
    sessions/{session_id}/attempts/{attempt_id}/original.wav, analysis.wav   write-once
    sessions/{session_id}/attempts/{attempt_id}/jobs/{job_id}/job.json
    sessions/{session_id}/attempts/{attempt_id}/jobs/{job_id}/result.json    write-once
    sessions/{session_id}/attempts/{attempt_id}/jobs/{job_id}/view.json      rebuildable from result.json
    sessions/{session_id}/summary.json
    sessions/{session_id}/coaching.json         M9 advice issued with the summary (atomic rewrite)
    sessions/{session_id}/reading_feedback.json M9 "This reading" (descriptive, this session only; regenerable)

Atomic writes: temp file + fsync + rename. Write-once files use a hard link
from the temp file, which fails if the target exists, so audio and result.json
can never be overwritten.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pronunciation_lab.reader.model import now, valid_id

DEFAULT_ROOT = Path.home() / ".pronunciation_lab" / "reader"


class AlreadyExists(FileExistsError):
    pass


def _tmp(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    return Path(name)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = _tmp(path)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_once_bytes(path: Path, data: bytes) -> None:
    tmp = _tmp(path)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError as exc:
            raise AlreadyExists(str(path)) from exc
    finally:
        tmp.unlink(missing_ok=True)


def _dump(obj: Any) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


class ReaderStore:
    def __init__(self, root: Path | None = None) -> None:
        # Directories are created on first write only: starting the app without
        # using the reader leaves nothing on disk.
        self.root = Path(root) if root else DEFAULT_ROOT
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()

    # -- paths ------------------------------------------------------------
    def session_dir(self, sid: str) -> Path:
        if not valid_id(sid):
            raise ValueError("invalid session id")
        return self.root / "sessions" / sid

    def attempt_dir(self, sid: str, aid: str) -> Path:
        if not valid_id(aid):
            raise ValueError("invalid attempt id")
        return self.session_dir(sid) / "attempts" / aid

    def job_dir(self, sid: str, aid: str, jid: str) -> Path:
        if not valid_id(jid):
            raise ValueError("invalid job id")
        return self.attempt_dir(sid, aid) / "jobs" / jid

    @contextmanager
    def session_lock(self, sid: str):
        with self._guard:
            lock = self._locks.setdefault(sid, threading.RLock())
        with lock:
            yield

    # -- articles ---------------------------------------------------------
    def save_article(self, article: dict[str, Any]) -> None:
        write_once_bytes(self.root / "articles" / f"{article['id']}.json", _dump(article))

    def load_article(self, article_id: str) -> dict[str, Any]:
        if not valid_id(article_id):
            raise KeyError(article_id)
        path = self.root / "articles" / f"{article_id}.json"
        if not path.is_file():
            raise KeyError(article_id)
        return json.loads(path.read_text(encoding="utf-8"))

    # -- sessions ---------------------------------------------------------
    def create_session(self, session: dict[str, Any]) -> None:
        d = self.session_dir(session["id"])
        try:
            d.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise AlreadyExists(session["id"]) from exc
        self.save_session(session)

    def save_session(self, session: dict[str, Any]) -> None:
        atomic_write_bytes(self.session_dir(session["id"]) / "session.json", _dump(session))

    def load_session(self, sid: str) -> dict[str, Any]:
        path = self.session_dir(sid) / "session.json"
        if not path.is_file():
            raise KeyError(sid)
        return json.loads(path.read_text(encoding="utf-8"))

    def session_ids(self) -> list[str]:
        base = self.root / "sessions"
        if not base.is_dir():
            return []
        return sorted(p.name for p in base.iterdir()
                      if p.is_dir() and valid_id(p.name) and (p / "session.json").is_file())

    def append_event(self, sid: str, kind: str, **data: Any) -> None:
        line = json.dumps({"at": now(), "kind": kind, **data}, ensure_ascii=False) + "\n"
        with open(self.session_dir(sid) / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()

    def events(self, sid: str) -> list[dict[str, Any]]:
        path = self.session_dir(sid) / "events.jsonl"
        if not path.is_file():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn last line from a crash is skipped, never fatal
        return out

    # -- attempts ---------------------------------------------------------
    def attempt_exists(self, sid: str, aid: str) -> bool:
        return (self.attempt_dir(sid, aid) / "attempt.json").is_file()

    def save_attempt(self, attempt: dict[str, Any]) -> None:
        atomic_write_bytes(self.attempt_dir(attempt["session_id"], attempt["id"]) / "attempt.json", _dump(attempt))

    def load_attempt(self, sid: str, aid: str) -> dict[str, Any]:
        path = self.attempt_dir(sid, aid) / "attempt.json"
        if not path.is_file():
            raise KeyError(aid)
        return json.loads(path.read_text(encoding="utf-8"))

    def write_audio(self, sid: str, aid: str, name: str, data: bytes) -> Path:
        if name not in ("original.wav", "analysis.wav"):
            raise ValueError(name)
        path = self.attempt_dir(sid, aid) / name
        write_once_bytes(path, data)
        return path

    def audio_path(self, sid: str, aid: str, name: str = "analysis.wav") -> Path:
        return self.attempt_dir(sid, aid) / name

    # -- jobs ---------------------------------------------------------------
    def save_job(self, job: dict[str, Any]) -> None:
        atomic_write_bytes(self.job_dir(job["session_id"], job["attempt_id"], job["id"]) / "job.json", _dump(job))

    def load_job(self, sid: str, aid: str, jid: str) -> dict[str, Any]:
        path = self.job_dir(sid, aid, jid) / "job.json"
        if not path.is_file():
            raise KeyError(jid)
        return json.loads(path.read_text(encoding="utf-8"))

    def write_result(self, job: dict[str, Any], result_json: str) -> None:
        write_once_bytes(self.job_dir(job["session_id"], job["attempt_id"], job["id"]) / "result.json",
                         result_json.encode("utf-8"))

    def result_path(self, job: dict[str, Any]) -> Path:
        return self.job_dir(job["session_id"], job["attempt_id"], job["id"]) / "result.json"

    # M7: the sentence's region of the attempt and its own analysis, written once per job
    JOB_FILES = ("boundary.json", "target.wav", "target_result.json")

    def write_job_file(self, job: dict[str, Any], name: str, data: bytes) -> Path:
        if name not in self.JOB_FILES:
            raise ValueError(name)
        path = self.job_dir(job["session_id"], job["attempt_id"], job["id"]) / name
        write_once_bytes(path, data)
        return path

    def job_file(self, job: dict[str, Any], name: str) -> Path:
        if name not in self.JOB_FILES:
            raise ValueError(name)
        return self.job_dir(job["session_id"], job["attempt_id"], job["id"]) / name

    def load_boundary(self, job: dict[str, Any]) -> dict[str, Any] | None:
        path = self.job_file(job, "boundary.json")
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def save_view(self, job: dict[str, Any], view: dict[str, Any]) -> None:
        atomic_write_bytes(self.job_dir(job["session_id"], job["attempt_id"], job["id"]) / "view.json", _dump(view))

    def load_view(self, job: dict[str, Any]) -> dict[str, Any] | None:
        path = self.job_dir(job["session_id"], job["attempt_id"], job["id"]) / "view.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    # -- summary ------------------------------------------------------------
    def save_summary(self, sid: str, summary: dict[str, Any]) -> None:
        atomic_write_bytes(self.session_dir(sid) / "summary.json", _dump(summary))

    def load_summary(self, sid: str) -> dict[str, Any] | None:
        path = self.session_dir(sid) / "summary.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    # -- M9 coaching snapshot: the advice issued with this session's summary (what was shown, when) ------
    def save_coaching(self, sid: str, coaching: dict[str, Any]) -> None:
        atomic_write_bytes(self.session_dir(sid) / "coaching.json", _dump(coaching))

    def load_coaching(self, sid: str) -> dict[str, Any] | None:
        path = self.session_dir(sid) / "coaching.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    # -- M9 "This reading": descriptive feedback on this session's summarised sentences (regenerable) ------------
    def save_reading_feedback(self, sid: str, feedback: dict[str, Any]) -> None:
        atomic_write_bytes(self.session_dir(sid) / "reading_feedback.json", _dump(feedback))

    def load_reading_feedback(self, sid: str) -> dict[str, Any] | None:
        path = self.session_dir(sid) / "reading_feedback.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
