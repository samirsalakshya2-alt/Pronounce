"""M12 reader service: sessions, segment attempts, and background analysis.

Recording, upload and analysis are decoupled:

* the browser cuts a continuous capture into attempts (sample-exact, no gaps,
  no overlap) and uploads each attempt's WAV when it is finalised;
* an upload is validated, its audio written once, converted with the existing
  `prepare_audio`, a primary AnalysisJob is queued, and the request returns
  (HTTP 202) without waiting for inference;
* one AnalysisWorker thread runs jobs one at a time — primary jobs before
  comparison jobs, FIFO within each — through the shared `analyze_pipeline`
  under the existing process-wide inference lock, so the lab and the reader
  never run two inferences at once.

Every record carries session_id, segment_id and attempt_id; every attempt keeps
target_text = the segment text at capture time. Nothing is deleted
automatically: too-short, rejected, interrupted, failed and discarded attempts
all remain visible.
"""

from __future__ import annotations

import io
import shutil
import threading
import time
import wave
from collections import deque
from pathlib import Path
from typing import Any

from pronunciation_lab.app.audio_input import MAX_DURATION_MS, MIN_DURATION_MS, AudioInputError, prepare_audio
from pronunciation_lab.app.engine_compare import compare, validate_comparison
from pronunciation_lab.app.pipeline import analyze_pipeline, build_analysis_view
from pronunciation_lab.app.service import AnalysisService, UserError
from pronunciation_lab.benchmark.schema import PronunciationResult
from pronunciation_lab.reader import model as M
from pronunciation_lab.reader.feedback import compact_feedback
from pronunciation_lab.reader.segmenter import segment
from pronunciation_lab.reader.store import AlreadyExists, ReaderStore
from pronunciation_lab.reader.summary import build_summary, validate_summary
from pronunciation_lab.reader.target import confirm_target

MAX_UPLOAD_BYTES = 30 * 1024 * 1024


def parse_client_wav(data: bytes) -> dict[str, int]:
    """The reader uploads 16-bit PCM mono WAV encoded in the browser; anything else is refused."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            info = {"channels": w.getnchannels(), "sample_width": w.getsampwidth(),
                    "sample_rate": w.getframerate(), "frames": w.getnframes()}
    except (wave.Error, EOFError) as exc:
        raise UserError("audio_format", "The reader expects a 16-bit PCM WAV recording.") from exc
    if info["channels"] != 1 or info["sample_width"] != 2 or info["sample_rate"] < 8000:
        raise UserError("audio_format", "The reader expects a 16-bit PCM mono WAV recording.")
    return info


class ReaderService:
    def __init__(self, store: ReaderStore, analysis: AnalysisService, *, start_worker: bool = True) -> None:
        self.store = store
        self.analysis = analysis
        self.worker = AnalysisWorker(self)
        self.recovered = self.recover()
        if start_worker:
            self.worker.start()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _session(self, sid: str) -> dict[str, Any]:
        if not M.valid_id(sid):
            raise UserError("session_unknown", "This reading session does not exist.", 404)
        try:
            return self.store.load_session(sid)
        except KeyError as exc:
            raise UserError("session_unknown", "This reading session does not exist.", 404) from exc

    def _attempt(self, sid: str, aid: str) -> dict[str, Any]:
        if not M.valid_id(aid):
            raise UserError("attempt_unknown", "This recording does not exist.", 404)
        try:
            return self.store.load_attempt(sid, aid)
        except KeyError as exc:
            raise UserError("attempt_unknown", "This recording does not exist.", 404) from exc

    def _touch(self, session: dict[str, Any]) -> None:
        session["rev"] += 1
        session["updated_at"] = M.now()
        self.store.save_session(session)

    def _set_session_state(self, session: dict[str, Any], new: str) -> None:
        if session["state"] != new:
            M.transition(session, M.SESSION_TRANSITIONS, new, "session")
            self.store.append_event(session["id"], "session_state", state=new)

    def _set_attempt_state(self, attempt: dict[str, Any], new: str) -> None:
        M.transition(attempt, M.ATTEMPT_TRANSITIONS, new, "attempt")
        self.store.append_event(attempt["session_id"], "attempt_state", attempt_id=attempt["id"],
                                segment_id=attempt["segment_id"], state=new)

    def local_engines(self) -> list[str]:
        return [e for e in M.ENGINES if e in self.analysis._instances]

    # ------------------------------------------------------------------
    # articles and sessions
    # ------------------------------------------------------------------

    def create_article(self, text: Any, title: Any = None, source: Any = None) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            raise UserError("article_empty", "Paste the article you want to read.")
        try:
            spans = segment(text)
        except ValueError as exc:
            raise UserError("article_invalid", f"This article cannot be read: {exc}.") from exc
        article = M.make_article(text, title if isinstance(title, str) else None, spans,
                                 source=source if isinstance(source, str) else None)
        self.store.save_article(article)
        return article

    def create_session(self, session_id: Any, article_id: Any, engine: Any = None) -> dict[str, Any]:
        if not M.valid_id(session_id):
            raise UserError("session_id_invalid", "Invalid session id.")
        try:
            article = self.store.load_article(article_id)
        except KeyError as exc:
            raise UserError("article_unknown", "This article does not exist.", 404) from exc
        engines = self.local_engines()
        eid = engine or self.analysis.default_engine()
        if eid not in engines:
            raise UserError("engine_unavailable", "Choose a local engine that is available.", 409)
        session = M.make_session(session_id, article, eid)
        try:
            self.store.create_session(session)
        except AlreadyExists as exc:
            raise UserError("session_exists", "This session id is already in use.", 409) from exc
        self.store.append_event(session_id, "session_created", article_id=article["id"], engine=eid)
        return self.snapshot(session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        out = []
        for sid in self.store.session_ids():
            s = self.store.load_session(sid)
            try:
                title = self.store.load_article(s["article_id"])["title"]
            except KeyError:
                title = None
            out.append({"id": sid, "title": title, "state": s["state"], "updated_at": s["updated_at"],
                        "attempts": len(s["attempt_ids"])})
        return sorted(out, key=lambda x: x["updated_at"], reverse=True)

    def snapshot(self, sid: str, since: int | None = None) -> dict[str, Any]:
        session = self._session(sid)
        if since is not None and since == session["rev"]:
            return {"unchanged": True, "rev": session["rev"]}
        article = self.store.load_article(session["article_id"])
        attempts = [self.store.load_attempt(sid, a) for a in session["attempt_ids"]]
        jobs: dict[str, dict[str, Any]] = {}
        for a in attempts:
            for j in a["job_ids"]:
                jobs[j] = self.store.load_job(sid, a["id"], j)
        by_segment: dict[str, list[dict[str, Any]]] = {}
        for a in attempts:
            by_segment.setdefault(a["segment_id"], []).append(a)
        return {
            "unchanged": False,
            "rev": session["rev"],
            "session": session,
            "article": {k: article[k] for k in ("id", "title", "source", "segments", "segmenter_version")},
            "attempts": attempts,
            "jobs": jobs,
            "segment_states": {seg["id"]: M.segment_state(by_segment.get(seg["id"], []), jobs)
                               for seg in article["segments"]},
            "queue": self.worker.queue_status(),
            "summary": self.summary(sid),
        }

    def session_action(self, sid: str, action: Any, run_id: Any = None) -> dict[str, Any]:
        with self.store.session_lock(sid):
            session = self._session(sid)
            if action == "reopen":
                # A new page instance: any attempt still capturing in another run can no longer finish.
                for aid in session["attempt_ids"]:
                    a = self.store.load_attempt(sid, aid)
                    if a["state"] == "CAPTURING" and a["capture"].get("run_id") != run_id:
                        self._set_attempt_state(a, "INTERRUPTED")
                        a["capture"]["end_reason"] = "interrupted"
                        self.store.save_attempt(a)
                if session["state"] == "READING":
                    self._set_session_state(session, "INTERRUPTED")
            elif action in M.SESSION_ACTIONS:
                try:
                    self._set_session_state(session, M.SESSION_ACTIONS[action])
                except M.IllegalTransition as exc:
                    raise UserError("transition_invalid", str(exc), 409) from exc
            else:
                raise UserError("action_invalid", "Unknown session action.")
            if M.valid_id(run_id):
                session["current_run_id"] = run_id
            self._touch(session)
        if action == "finish":
            self.maybe_summarize(sid)
        return self.snapshot(sid)

    # ------------------------------------------------------------------
    # attempts
    # ------------------------------------------------------------------

    def _segment(self, session: dict[str, Any], segment_id: Any) -> dict[str, Any]:
        article = self.store.load_article(session["article_id"])
        seg = next((s for s in article["segments"] if s["id"] == segment_id), None)
        if seg is None:
            raise UserError("segment_unknown", "That sentence is not part of this article.")
        if not seg["readable"]:
            raise UserError("segment_unreadable", "That part of the article has no words to read.")
        return seg

    def _new_attempt(self, session: dict[str, Any], seg: dict[str, Any], aid: str, capture: dict[str, Any]) -> dict[str, Any]:
        number = 1 + sum(1 for a in session["attempt_ids"]
                         if self.store.load_attempt(session["id"], a)["segment_id"] == seg["id"])
        attempt = M.make_attempt(session, seg, aid, number, capture)
        self.store.save_attempt(attempt)
        session["attempt_ids"].append(aid)
        if session["state"] != "READING":
            self._set_session_state(session, "READING")
        self.store.append_event(session["id"], "attempt_started", attempt_id=aid, segment_id=seg["id"],
                                capture=capture)
        return attempt

    @staticmethod
    def _capture(meta: dict[str, Any]) -> dict[str, Any]:
        def num(key):
            v = meta.get(key)
            return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None
        cap = {"run_id": meta.get("run_id") if M.valid_id(meta.get("run_id")) else None,
               "sample_rate": num("sample_rate"), "start_sample": num("start_sample"),
               "end_sample": num("end_sample"), "end_reason": meta.get("end_reason"),
               "wall_clock_start": meta.get("wall_clock_start"), "wall_clock_end": meta.get("wall_clock_end")}
        if cap["end_reason"] is not None and cap["end_reason"] not in M.END_REASONS:
            raise UserError("capture_invalid", "Unknown end reason.")
        return cap

    def start_attempt(self, sid: str, aid: Any, meta: dict[str, Any]) -> dict[str, Any]:
        if not M.valid_id(aid):
            raise UserError("attempt_id_invalid", "Invalid recording id.")
        with self.store.session_lock(sid):
            session = self._session(sid)
            if self.store.attempt_exists(sid, aid):
                raise UserError("attempt_exists", "This recording id is already in use.", 409)
            seg = self._segment(session, meta.get("segment_id"))
            attempt = self._new_attempt(session, seg, aid, self._capture(meta))
            self._touch(session)
        return attempt

    def upload_audio(self, sid: str, aid: Any, data: bytes, meta: dict[str, Any]) -> dict[str, Any]:
        """Store one finalised attempt and queue its analysis. Never waits for inference."""
        if not M.valid_id(aid):
            raise UserError("attempt_id_invalid", "Invalid recording id.")
        info = parse_client_wav(data)
        capture = self._capture(meta)
        start, end = capture["start_sample"], capture["end_sample"]
        if start is None or end is None or end < start:
            raise UserError("capture_invalid", "The recording interval is missing or reversed.")
        if end - start != info["frames"] or capture["sample_rate"] != info["sample_rate"]:
            raise UserError("capture_invalid", "The audio does not match its recording interval.")
        with self.store.session_lock(sid):
            session = self._session(sid)
            if self.store.attempt_exists(sid, aid):
                attempt = self.store.load_attempt(sid, aid)
                if attempt["audio"] is not None:
                    raise UserError("attempt_duplicate", "This recording was already received.", 409)
                if attempt["segment_id"] != meta.get("segment_id"):
                    raise UserError("segment_mismatch", "This recording belongs to another sentence.", 409)
                if attempt["capture"].get("start_sample") not in (None, start) or \
                        attempt["capture"].get("run_id") not in (None, capture["run_id"]):
                    raise UserError("capture_invalid", "The recording interval does not match its start.", 409)
                attempt["capture"] |= {k: v for k, v in capture.items() if v is not None}
            else:  # the start notice was lost: the upload alone creates the attempt
                seg = self._segment(session, meta.get("segment_id"))
                attempt = self._new_attempt(session, seg, aid, capture)
            try:
                self.store.write_audio(sid, aid, "original.wav", data)
            except AlreadyExists as exc:
                raise UserError("attempt_duplicate", "This recording was already received.", 409) from exc
            duration_ms = info["frames"] * 1000.0 / info["sample_rate"]
            attempt["audio"] = {"original_sha256": _sha256(data), "original_frames": info["frames"],
                                "original_sample_rate": info["sample_rate"], "duration_ms": duration_ms,
                                "analysis": None, "conversion": None}
            job = None
            if duration_ms < MIN_DURATION_MS:
                self._set_attempt_state(attempt, "TOO_SHORT")
                attempt["error"] = {"code": "audio_too_short", "message": "Too short to analyse."}
            elif duration_ms > MAX_DURATION_MS + 1.0:
                self._set_attempt_state(attempt, "REJECTED")
                attempt["error"] = {"code": "audio_too_long", "message": "Longer than the 60-second limit."}
            else:
                work = self.store.attempt_dir(sid, aid) / ".prepare"
                try:
                    prepared = prepare_audio(data, "original.wav", work)
                    self.store.write_audio(sid, aid, "analysis.wav", prepared.analysis_path.read_bytes())
                    attempt["audio"] |= {"analysis": "analysis.wav", "conversion": prepared.conversion,
                                         "analysis_duration_ms": prepared.duration_ms}
                    self._set_attempt_state(attempt, "RECORDED")
                    job = self._queue(session, attempt, session["engine_default"], "primary")
                except AudioInputError as exc:
                    state = "TOO_SHORT" if exc.code == "audio_too_short" else "REJECTED"
                    self._set_attempt_state(attempt, state)
                    attempt["error"] = {"code": exc.code, "message": exc.message}
                finally:
                    shutil.rmtree(work, ignore_errors=True)
            self.store.save_attempt(attempt)
            self.store.append_event(sid, "attempt_audio", attempt_id=aid, segment_id=attempt["segment_id"],
                                    start_sample=start, end_sample=end, state=attempt["state"])
            self._touch(session)
        if job is not None:
            self.worker.submit(job)
        return {"attempt": attempt, "job": job}

    def _queue(self, session: dict[str, Any], attempt: dict[str, Any], engine: str, kind: str) -> dict[str, Any]:
        job = M.make_job(attempt, engine, kind)
        self.store.save_job(job)
        attempt["job_ids"].append(job["id"])
        if kind == "primary":
            self._set_attempt_state(attempt, "QUEUED")
        self.store.append_event(session["id"], "job_queued", job_id=job["id"], attempt_id=attempt["id"],
                                segment_id=attempt["segment_id"], engine=engine, job_kind=kind)
        return job

    def attempt_detail(self, sid: str, aid: str) -> dict[str, Any]:
        self._session(sid)
        attempt = self._attempt(sid, aid)
        jobs = [self.store.load_job(sid, aid, j) for j in attempt["job_ids"]]
        return {"attempt": attempt, "jobs": jobs,
                "views": {j["id"]: self.store.load_view(j) for j in jobs if j["state"] == "SUCCEEDED"}}

    def attempt_audio(self, sid: str, aid: str) -> Path:
        self._session(sid)
        attempt = self._attempt(sid, aid)
        path = self.store.audio_path(sid, aid, "analysis.wav")
        if not attempt["audio"] or not path.is_file():
            raise UserError("audio_unavailable", "This recording has no analysable audio.", 404)
        return path

    def set_disposition(self, sid: str, aid: str, value: Any) -> dict[str, Any]:
        if value not in M.DISPOSITIONS:
            raise UserError("disposition_invalid", "Choose keep, discard or re-record.")
        with self.store.session_lock(sid):
            session = self._session(sid)
            attempt = self._attempt(sid, aid)
            attempt["user_disposition"] = value
            attempt["updated_at"] = M.now()
            self.store.save_attempt(attempt)
            self.store.append_event(sid, "disposition", attempt_id=aid, segment_id=attempt["segment_id"], value=value)
            self._touch(session)
        return attempt

    def retry(self, sid: str, aid: str, jid: str) -> dict[str, Any]:
        with self.store.session_lock(sid):
            session = self._session(sid)
            attempt = self._attempt(sid, aid)
            if jid not in attempt["job_ids"]:
                raise UserError("job_unknown", "This analysis does not exist.", 404)
            old = self.store.load_job(sid, aid, jid)
            if old["state"] != "FAILED":
                raise UserError("retry_invalid", "Only a failed analysis can be retried.", 409)
            if old["kind"] == "primary" and attempt["state"] == "ANALYSIS_FAILED":
                self._set_attempt_state(attempt, "QUEUED")
            job = M.make_job(attempt, old["engine_id"], old["kind"])
            job["try_count"] = old["try_count"] + 1
            job["retry_of"] = jid
            self.store.save_job(job)
            attempt["job_ids"].append(job["id"])
            self.store.save_attempt(attempt)
            self.store.append_event(sid, "job_retry", job_id=job["id"], retry_of=jid, attempt_id=aid,
                                    segment_id=attempt["segment_id"])
            self._touch(session)
        self.worker.submit(job)
        return job

    def request_comparison(self, sid: str, aid: str) -> dict[str, Any]:
        """Queue the other local engine for this attempt (lower priority than primary jobs)."""
        with self.store.session_lock(sid):
            session = self._session(sid)
            attempt = self._attempt(sid, aid)
            if attempt["state"] != "ANALYZED":
                raise UserError("compare_unsupported", "Compare after the first analysis has finished.", 409)
            jobs = [self.store.load_job(sid, aid, j) for j in attempt["job_ids"]]
            primary = next(j for j in jobs if j["kind"] == "primary" and j["state"] == "SUCCEEDED")
            other = next((e for e in M.ENGINES if e != primary["engine_id"]), None)
            if other not in self.local_engines():
                raise UserError("engine_unavailable", "The other local engine is not available.", 409)
            existing = next((j for j in jobs if j["kind"] == "comparison" and j["engine_id"] == other
                             and j["state"] in ("QUEUED", "RUNNING", "SUCCEEDED")), None)
            if existing:
                return existing
            job = self._queue(session, attempt, other, "comparison")
            self.store.save_attempt(attempt)
            self._touch(session)
        self.worker.submit(job)
        return job

    # ------------------------------------------------------------------
    # analysis (worker side)
    # ------------------------------------------------------------------

    def run_job(self, sid: str, aid: str, jid: str) -> None:
        with self.store.session_lock(sid):
            session = self.store.load_session(sid)
            attempt = self.store.load_attempt(sid, aid)
            job = self.store.load_job(sid, aid, jid)
            if job["state"] != "QUEUED":
                return
            M.transition(job, M.JOB_TRANSITIONS, "RUNNING", "job")
            job["started_at"] = M.now()
            self.store.save_job(job)
            if job["kind"] == "primary":
                self._set_attempt_state(attempt, "ANALYZING")
                self.store.save_attempt(attempt)
            self._touch(session)

        state, error, view, target = "FAILED", None, None, {"state": "NOT_APPLICABLE"}
        try:
            engine = self.analysis._instances.get(job["engine_id"])
            if engine is None:
                raise UserError("engine_unavailable", "This engine is not available.")
            path = self.store.audio_path(sid, aid, "analysis.wav")
            result, view = analyze_pipeline(engine, path, attempt["target_text"], recording_id=f"reader-{aid[:8]}",
                                            lock=self.analysis._lock,
                                            on_inference_done=lambda: self.analysis._warm.add(job["engine_id"]))
            self.store.write_result(job, result.model_dump_json())
            self.store.save_view(job, view)
            target = confirm_target(result)
            if result.status in ("failed", "blocked"):
                error = {"code": "engine_" + result.status,
                         "message": "; ".join(e.message for e in result.errors) or "The analysis did not produce evidence."}
            else:
                state = "SUCCEEDED"
        except Exception as exc:  # noqa: BLE001 - a failed job is recorded, never lost
            error = {"code": getattr(exc, "code", "analysis_error"), "message": str(exc) or type(exc).__name__}

        with self.store.session_lock(sid):
            session = self.store.load_session(sid)
            attempt = self.store.load_attempt(sid, aid)
            job = self.store.load_job(sid, aid, jid)
            M.transition(job, M.JOB_TRANSITIONS, state, "job")
            job |= {"finished_at": M.now(), "error": error, "target_confirmation": target,
                    "feedback": compact_feedback(view) if state == "SUCCEEDED" else None,
                    "pipeline_versions": _versions(view)}
            self.store.save_job(job)
            if job["kind"] == "primary":
                self._set_attempt_state(attempt, "ANALYZED" if state == "SUCCEEDED" else "ANALYSIS_FAILED")
                self.store.save_attempt(attempt)
            self.store.append_event(sid, "job_done", job_id=jid, attempt_id=aid, segment_id=attempt["segment_id"],
                                    state=state)
            self._touch(session)
        self.maybe_summarize(sid)

    def maybe_summarize(self, sid: str) -> dict[str, Any] | None:
        """Build the reading summary once reading is finished and every primary analysis is terminal."""
        with self.store.session_lock(sid):
            session = self.store.load_session(sid)
            if session["state"] != "FINISHED":
                return None
            for aid in session["attempt_ids"]:
                a = self.store.load_attempt(sid, aid)
                if a["state"] in ("CAPTURING", "RECORDED", "QUEUED", "ANALYZING"):
                    return None  # still waiting for this sentence
            article = self.store.load_article(session["article_id"])
            summary = build_summary(self.store, session, article)
            issues = validate_summary(summary, self.store)
            summary["integrity"] = {"ok": not issues, "issues": issues}
            self.store.save_summary(sid, summary)
            self._set_session_state(session, "SUMMARIZED")
            self.store.append_event(sid, "summary_built", inputs=len(summary["inputs"]), version=summary["version"])
            self._touch(session)
            return summary

    def summary(self, sid: str) -> dict[str, Any] | None:
        session = self._session(sid)
        summary = self.store.load_summary(sid)
        return None if summary is None else summary | {"stale": session["state"] != "SUMMARIZED"}

    def comparison(self, sid: str, aid: str) -> dict[str, Any]:
        """Both local engines' evidence for one attempt, paired by the M5 comparison (never merged)."""
        self._session(sid)
        attempt = self._attempt(sid, aid)
        jobs = [self.store.load_job(sid, aid, j) for j in attempt["job_ids"]]
        primary = next((j for j in reversed(jobs) if j["kind"] == "primary" and j["state"] == "SUCCEEDED"), None)
        other = next((j for j in reversed(jobs) if j["kind"] == "comparison"), None)
        if primary is None or other is None:
            raise UserError("comparison_unavailable", "No engine comparison for this recording yet.", 404)
        if other["state"] in ("QUEUED", "RUNNING"):
            raise UserError("comparison_pending", "The other listening model is still working.", 409)
        if other["state"] != "SUCCEEDED":
            raise UserError("comparison_failed", (other.get("error") or {}).get("message") or "The comparison failed.", 409)
        pair = sorted((primary, other), key=lambda j: M.ENGINES.index(j["engine_id"]))
        sides = []
        for j in pair:
            view = self.store.load_view(j)
            sides.append({"engine": j["engine_id"], "observations": view["coach"]["observations"],
                          "reduction": view["reduction"], "text": attempt["target_text"],
                          "duration_ms": view["duration_ms"]})
        if any(side["reduction"].get("state") != "ok" for side in sides):
            raise UserError("comparison_failed", "One of the models produced no evidence for this recording.", 409)
        result = compare(*sides)
        issues = validate_comparison(result, *sides)
        result |= {"attempt_id": aid, "session_id": sid, "segment_id": attempt["segment_id"],
                   "job_ids": {j["engine_id"]: j["id"] for j in pair}, "timeline": M.PLAYBACK_TIMELINE,
                   "integrity": {"ok": not issues, "issues": issues}}
        return result

    def rebuild_view(self, job: dict[str, Any]) -> dict[str, Any]:
        result = PronunciationResult.model_validate_json(self.store.result_path(job).read_text(encoding="utf-8"))
        return build_analysis_view(result, self.store.audio_path(job["session_id"], job["attempt_id"]))

    # ------------------------------------------------------------------
    # recovery and shutdown
    # ------------------------------------------------------------------

    def recover(self) -> dict[str, int]:
        """After a restart: RUNNING jobs are re-queued, live captures marked interrupted. Nothing is dropped."""
        counts = {"requeued": 0, "interrupted_attempts": 0, "interrupted_sessions": 0}
        pending: list[tuple[str, dict[str, Any]]] = []
        for sid in self.store.session_ids():
            with self.store.session_lock(sid):
                session = self.store.load_session(sid)
                changed = False
                for aid in session["attempt_ids"]:
                    attempt = self.store.load_attempt(sid, aid)
                    if attempt["state"] == "CAPTURING":
                        self._set_attempt_state(attempt, "INTERRUPTED")
                        attempt["capture"]["end_reason"] = "interrupted"
                        counts["interrupted_attempts"] += 1
                        changed = True
                    for jid in attempt["job_ids"]:
                        job = self.store.load_job(sid, aid, jid)
                        if job["state"] == "RUNNING":
                            M.transition(job, M.JOB_TRANSITIONS, "QUEUED", "job")
                            self.store.save_job(job)
                            if job["kind"] == "primary" and attempt["state"] == "ANALYZING":
                                self._set_attempt_state(attempt, "QUEUED")
                            counts["requeued"] += 1
                            changed = True
                        if job["state"] == "QUEUED":
                            pending.append((job["enqueued_at"], job))
                    self.store.save_attempt(attempt)
                if session["state"] == "READING":
                    self._set_session_state(session, "INTERRUPTED")
                    counts["interrupted_sessions"] += 1
                    changed = True
                if changed:
                    self.store.append_event(sid, "recovered", **counts)
                    self._touch(session)
        for _, job in sorted(pending, key=lambda x: x[0]):
            self.worker.submit(job)
        return counts

    def close(self) -> None:
        self.worker.stop()


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _versions(view: dict[str, Any] | None) -> dict[str, Any] | None:
    if not view:
        return None
    return {"coach": (view.get("coach") or {}).get("version"), "reduction": (view.get("reduction") or {}).get("version")}


class AnalysisWorker:
    """One thread; primary jobs first, FIFO within a kind; joins cleanly on stop()."""

    def __init__(self, service: ReaderService) -> None:
        self.service = service
        self._queues: dict[str, deque] = {"primary": deque(), "comparison": deque()}
        self._cond = threading.Condition()
        self._stop = False
        self._thread: threading.Thread | None = None
        self.running: dict[str, Any] | None = None
        self.processed: list[str] = []  # job ids in execution order (tests, diagnostics)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="reader-analysis-worker", daemon=True)
            self._thread.start()

    def submit(self, job: dict[str, Any]) -> None:
        with self._cond:
            q = self._queues[job["kind"]]
            if all(j["id"] != job["id"] for j in q):
                q.append({"id": job["id"], "session_id": job["session_id"], "attempt_id": job["attempt_id"],
                          "kind": job["kind"]})
            self._cond.notify()

    def queue_status(self) -> dict[str, Any]:
        with self._cond:
            return {"queued_primary": len(self._queues["primary"]), "queued_comparison": len(self._queues["comparison"]),
                    "running": dict(self.running) if self.running else None}

    def _next(self) -> dict[str, Any] | None:
        for kind in ("primary", "comparison"):
            if self._queues[kind]:
                return self._queues[kind].popleft()
        return None

    def run_pending(self) -> int:
        """Process everything queued on the calling thread (used when the thread is not started)."""
        n = 0
        while True:
            with self._cond:
                item = self._next()
            if item is None:
                return n
            self._process(item)
            n += 1

    def _process(self, item: dict[str, Any]) -> None:
        with self._cond:
            self.running = item
        try:
            self.service.run_job(item["session_id"], item["attempt_id"], item["id"])
            self.processed.append(item["id"])
        except Exception:  # noqa: BLE001 - keep the worker alive; the job stays visible in the store
            pass
        finally:
            with self._cond:
                self.running = None

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._stop and not (self._queues["primary"] or self._queues["comparison"]):
                    self._cond.wait(timeout=1.0)
                if self._stop:
                    return
                item = self._next()
            self._process(item)

    def stop(self, timeout: float = 30.0) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def wait_idle(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._cond:
                if not self._queues["primary"] and not self._queues["comparison"] and self.running is None:
                    return True
            time.sleep(0.01)
        return False
