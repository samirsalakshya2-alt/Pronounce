"""Resumable benchmark runner over (recording x engine) cells.

Engines come from the M1 registry and are classified before anything runs:

    runnable    -> every cell is executed through `safe_analyze`
    blocked     -> cells recorded as blocked with the engine's readiness, never executed
    unresolved  -> cells recorded as unresolved with the engine's readiness, never executed

Resuming. A cell is skipped only when its file loads as `complete` (parses,
checksums match, identity matches its name, raw evidence intact) AND its status
is ok or partial. Missing and corrupt cells always run. Failed cells run again
with `retry_failed`. Blocked/unresolved cells are re-classified on every
invocation (cheap, no execution): if an engine has become runnable, its cells
run. `force` reruns everything.

Each cell is written atomically before the next one starts, so an interrupted
run loses at most the cell in flight, and never leaves a false success.
"""

from __future__ import annotations

import csv
import io
import json
import platform
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

import numpy as np

from pronunciation_lab.benchmark.base import ErrorType, PronunciationEngine, safe_analyze
from pronunciation_lab.benchmark.cells import (
    EVIDENCE_STATUSES,
    BenchmarkCell,
    CellError,
    CellErrorType,
    CellPerformance,
    CellStore,
    atomic_write_bytes,
    atomic_write_json,
    cell_id,
)
from pronunciation_lab.benchmark.dataset import PreflightReport, Recording
from pronunciation_lab.benchmark.engines import ENGINES, create_engine
from pronunciation_lab.benchmark.schema import PronunciationResult
from pronunciation_lab.benchmark.validate import raw_correspondence_issues, validate_cell

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ----------------------------------------------------------------------
# Engine availability
# ----------------------------------------------------------------------


@dataclass
class Availability:
    engine_id: str
    state: str  # runnable | blocked | unresolved | failed
    reason: str | None = None
    detail: dict[str, Any] | None = None
    engine: PronunciationEngine | None = None


def classify_engine(
    engine_id: str,
    factory: Callable[[str], PronunciationEngine] = create_engine,
) -> Availability:
    """Decide from the engine itself whether its cells can be executed."""
    try:
        engine = factory(engine_id)
    except KeyError:
        raise
    except Exception as exc:  # noqa: BLE001 - an engine that cannot even be built
        return Availability(
            engine_id,
            "failed",
            CellErrorType.ENGINE_CONSTRUCTION_FAILED,
            {"message": f"{type(exc).__name__}: {exc}"},
        )

    readiness = getattr(engine, "readiness", None)
    if callable(readiness):
        detail = readiness()
        state = detail.get("state")
        if state in ("blocked", "unresolved"):
            return Availability(engine_id, state, detail.get("reason"), detail, engine)
    return Availability(engine_id, "runnable", None, None, engine)


# ----------------------------------------------------------------------
# Environment
# ----------------------------------------------------------------------


def environment_snapshot() -> dict[str, Any]:
    packages = {}
    for name in ("torch", "transformers", "numpy", "scipy", "soundfile", "librosa",
                 "openpronounce", "phonemizer", "pydantic", "huggingface-hub"):
        try:
            packages[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            packages[name] = None
    try:
        from phonemizer.backend import EspeakBackend

        espeak = ".".join(str(v) for v in EspeakBackend.version())
    except Exception:  # noqa: BLE001
        espeak = None

    def git(*args: str) -> str | None:
        try:
            return subprocess.run(
                ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True, timeout=10
            ).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return None

    status = git("status", "--porcelain")
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "packages": packages,
        "espeak": espeak,
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty_files": len(status.splitlines()) if status is not None else None,
    }


# ----------------------------------------------------------------------
# Raw evidence
# ----------------------------------------------------------------------


def extract_raw(engine: PronunciationEngine) -> dict[str, np.ndarray] | None:
    """The engine's last recognition as arrays, or None if it keeps none."""
    rec = getattr(engine, "last_recognition", None)
    if rec is None:
        return None
    phones = getattr(rec, "phones", None)
    if phones is None:
        phones = getattr(rec, "tokens", None)
    spans = np.asarray(list(rec.spans), dtype=np.int64).reshape(-1, 2)
    return {
        "log_posteriors": np.asarray(rec.log_posteriors, dtype=np.float32),
        "vocab": np.asarray(list(rec.vocab), dtype=str),
        "heard_phones": np.asarray(list(phones), dtype=str),
        "confidences": np.asarray(list(rec.confidences), dtype=np.float64),
        "frame_spans": spans,
    }


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------


@dataclass
class RunPlan:
    run: list[str] = field(default_factory=list)
    skip: dict[str, str] = field(default_factory=dict)  # cell_id -> reason


@dataclass
class RunReport:
    benchmark_run_id: str
    executed: list[str] = field(default_factory=list)
    recorded_without_execution: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)
    availability: dict[str, dict[str, Any]] = field(default_factory=dict)
    duration_s: float = 0.0


class BenchmarkRunner:
    def __init__(
        self,
        run_dir: Path,
        benchmark_run_id: str,
        recordings: list[Recording],
        engine_ids: list[str],
        *,
        data_dir: Path,
        engine_factory: Callable[[str], PronunciationEngine] = create_engine,
        known_engines: list[str] | None = None,
        log: Callable[[str], None] = lambda message: None,
    ) -> None:
        known = list(known_engines if known_engines is not None else ENGINES)
        unknown = [e for e in engine_ids if e not in known]
        if unknown:
            raise KeyError(f"unknown engine(s) {unknown}; known: {known}")
        if len(set(r.recording_id for r in recordings)) != len(recordings):
            raise ValueError("duplicate recording ids")

        self.run_dir = Path(run_dir)
        self.run_id = benchmark_run_id
        self.recordings = recordings
        self.engine_ids = engine_ids
        self.data_dir = Path(data_dir)
        self.factory = engine_factory
        self.store = CellStore(self.run_dir)
        self.log = log

    # --- planning -------------------------------------------------------

    def plan(self, *, retry_failed: bool, force: bool, availability: dict[str, Availability]) -> RunPlan:
        plan = RunPlan()
        for eid in self.engine_ids:
            state = availability[eid].state
            for rec in self.recordings:
                cid = cell_id(rec.recording_id, eid)
                loaded = self.store.load(cid)
                if force:
                    plan.run.append(cid)
                    continue
                if loaded.state != "complete":
                    plan.run.append(cid)
                    continue
                cell = loaded.cell
                if cell.benchmark_run_id != self.run_id:
                    plan.run.append(cid)
                elif cell.status in EVIDENCE_STATUSES:
                    plan.skip[cid] = f"complete ({cell.status})"
                elif cell.status == "failed":
                    if retry_failed:
                        plan.run.append(cid)
                    else:
                        plan.skip[cid] = "failed (use retry_failed)"
                elif cell.status == state:
                    plan.skip[cid] = f"still {state}"
                else:
                    plan.run.append(cid)  # availability changed
        return plan

    # --- execution --------------------------------------------------------

    def run(
        self,
        preflight: PreflightReport,
        *,
        retry_failed: bool = False,
        force: bool = False,
    ) -> RunReport:
        start = time.perf_counter()
        report = RunReport(self.run_id)

        availability = {}
        for eid in self.engine_ids:
            availability[eid] = classify_engine(eid, self.factory)
            a = availability[eid]
            report.availability[eid] = {"state": a.state, "reason": a.reason}
            self.log(f"engine {eid}: {a.state}{' (' + a.reason + ')' if a.reason else ''}")

        self._write_run_metadata(preflight, availability, retry_failed=retry_failed, force=force)

        plan = self.plan(retry_failed=retry_failed, force=force, availability=availability)
        report.skipped = plan.skip
        to_run = set(plan.run)

        for eid in self.engine_ids:
            a = availability[eid]
            for rec in self.recordings:
                cid = cell_id(rec.recording_id, eid)
                if cid not in to_run:
                    continue
                previous = self.store.load(cid)
                attempt = (previous.cell.attempt + 1) if previous.cell is not None else 1

                if a.state == "runnable":
                    cell, raw = self._execute(a.engine, eid, rec, attempt)
                    report.executed.append(cid)
                else:
                    cell, raw = self._record_unavailable(a, rec, attempt), None
                    report.recorded_without_execution.append(cid)

                cell.validation = validate_cell(cell)
                self.store.write(cell, raw)
                report.statuses[cid] = cell.status
                self.log(f"{cid}: {cell.status}" + (f" [{cell.errors[0].type}]" if cell.errors else ""))

        report.duration_s = time.perf_counter() - start
        self._append_session(report, retry_failed=retry_failed, force=force)
        return report

    def _base_cell(self, eid: str, rec: Recording, status: str, attempt: int) -> BenchmarkCell:
        return BenchmarkCell(
            benchmark_run_id=self.run_id,
            cell_id=cell_id(rec.recording_id, eid),
            recording_id=rec.recording_id,
            engine_id=eid,
            input_filename=str(rec.wav_path.relative_to(self.data_dir)),
            source_filename=rec.filename,
            target_text=rec.target_text,
            reading_style=rec.reading_style,
            purpose=rec.purpose,
            text_group=rec.text_group,
            status=status,
            attempt=attempt,
        )

    def _record_unavailable(self, a: Availability, rec: Recording, attempt: int) -> BenchmarkCell:
        if a.state == "failed":
            cell = self._base_cell(a.engine_id, rec, "failed", attempt)
            cell.errors = [CellError(type=a.reason, message=a.detail["message"], stage="engine_construction")]
            return cell

        cell = self._base_cell(a.engine_id, rec, a.state, attempt)
        cell.availability = a.detail
        engine = a.engine
        cell.engine_mode = getattr(engine, "mode", None)
        cell.engine_version = getattr(engine, "version", None)
        cell.engine_model = getattr(engine, "model_id", None)
        cell.errors = [
            CellError(
                type=a.reason or "unavailable",
                message=f"{a.engine_id} is {a.state}; not executed",
                stage="availability",
                retryable=a.state == "blocked",
            )
        ]
        now = _now()
        cell.started_at = cell.finished_at = now
        return cell

    def _execute(self, engine: PronunciationEngine, eid: str, rec: Recording, attempt: int):
        cell = self._base_cell(eid, rec, "failed", attempt)
        cell.engine_mode = engine.mode
        cell.started_at = _now()

        if hasattr(engine, "last_recognition"):
            engine.last_recognition = None  # never attach a previous cell's raw evidence

        t0 = time.perf_counter()
        result = safe_analyze(
            engine,
            rec.wav_path,
            rec.target_text,
            recording_id=rec.recording_id,
            original_path=rec.source_path,
        )
        raw = None

        if not isinstance(result, PronunciationResult):
            cell.errors = [
                CellError(
                    type=CellErrorType.MALFORMED_ENGINE_RESULT,
                    message=f"engine returned {type(result).__name__}, not PronunciationResult",
                    stage="result",
                    retryable=False,
                )
            ]
        else:
            cell.result = result
            cell.engine_version = result.engine.version
            cell.engine_model = result.engine.model
            cell.status = _cell_status(result)
            cell.errors = [CellError(**e.model_dump()) for e in result.errors]
            p = result.processing
            cell.performance = CellPerformance(
                wall_time_ms=p.wall_time_ms,
                model_load_ms=p.model_load_ms,
                inference_ms=p.inference_ms,
                postprocessing_ms=p.postprocessing_ms,
                realtime_factor=p.realtime_factor,
                run_type=p.run_type,
                device=p.device,
                stages_ms=p.stages_ms,
                api_latency_ms=p.api_latency_ms,
                provider_processing_ms=p.provider_processing_ms,
            )

            mismatch = []
            if result.recording.id != rec.recording_id:
                mismatch.append(f"recording id {result.recording.id!r}")
            if result.recording.target.text != rec.target_text:
                mismatch.append("target text")
            if result.engine.name != eid:
                mismatch.append(f"engine name {result.engine.name!r}")
            if mismatch:
                cell.status = "failed"
                cell.errors.append(
                    CellError(
                        type=CellErrorType.RESULT_METADATA_MISMATCH,
                        message="result does not describe this cell: " + ", ".join(mismatch),
                        stage="result",
                        retryable=False,
                    )
                )

            if cell.status in EVIDENCE_STATUSES:
                raw = extract_raw(engine)
                problems = raw_correspondence_issues(cell, raw or {})
                if problems:
                    cell.status = "failed"
                    raw = None
                    cell.errors.append(
                        CellError(
                            type=CellErrorType.RAW_EVIDENCE_MISMATCH,
                            message="; ".join(problems),
                            stage="raw_evidence",
                            retryable=True,
                        )
                    )

        if cell.performance is not None:
            cell.performance.runner_wall_ms = (time.perf_counter() - t0) * 1000.0
        cell.finished_at = _now()
        return cell, raw

    # --- metadata ---------------------------------------------------------

    def _metadata_path(self) -> Path:
        return self.run_dir / "run_metadata.json"

    def _write_run_metadata(self, preflight: PreflightReport, availability, *, retry_failed, force) -> None:
        path = self._metadata_path()
        if path.exists():
            metadata = json.loads(path.read_text())
            if metadata["benchmark_run_id"] != self.run_id:
                raise ValueError(f"{self.run_dir} belongs to run {metadata['benchmark_run_id']}")
            if metadata["preflight"]["sha256"] != preflight.hashes:
                raise ValueError(
                    "source data changed since this run started (manifest or audio hashes differ); "
                    "start a new run instead of resuming"
                )
        else:
            metadata = {
                "benchmark_run_id": self.run_id,
                "created_at": _now(),
                "recordings": [r.recording_id for r in self.recordings],
                "engines": self.engine_ids,
                "preflight": {
                    "manifest": str(preflight.manifest_path),
                    "data_dir": str(preflight.data_dir),
                    "sha256": preflight.hashes,
                    "warnings": [w.as_dict() for w in preflight.warnings],
                },
                "sessions": [],
            }
            self.run_dir.mkdir(parents=True, exist_ok=True)
            buffer = io.StringIO()
            writer = csv.DictWriter(
                buffer, fieldnames=["recording_id", "filename", "target_text", "reading_style", "purpose"]
            )
            writer.writeheader()
            for r in self.recordings:
                writer.writerow({
                    "recording_id": r.recording_id, "filename": r.filename, "target_text": r.target_text,
                    "reading_style": r.reading_style, "purpose": r.purpose,
                })
            atomic_write_bytes(self.run_dir / "manifest_snapshot.csv", buffer.getvalue().encode("utf-8"))

        metadata["environment"] = environment_snapshot()
        metadata["availability"] = {
            eid: {"state": a.state, "reason": a.reason, "detail": a.detail} for eid, a in availability.items()
        }
        metadata["engine_identity"] = {
            eid: {
                "mode": getattr(a.engine, "mode", None),
                "version": getattr(a.engine, "version", None),
                "model_id": getattr(a.engine, "model_id", None),
                "revision": getattr(a.engine, "revision", None),
            }
            for eid, a in availability.items()
        }
        metadata["pending_session"] = {"started_at": _now(), "retry_failed": retry_failed, "force": force}
        atomic_write_json(path, metadata)

    def _append_session(self, report: RunReport, *, retry_failed, force) -> None:
        path = self._metadata_path()
        metadata = json.loads(path.read_text())
        session = metadata.pop("pending_session", {})
        session.update({
            "finished_at": _now(),
            "duration_s": report.duration_s,
            "executed": report.executed,
            "recorded_without_execution": report.recorded_without_execution,
            "skipped": len(report.skipped),
        })
        metadata["sessions"].append(session)
        metadata["updated_at"] = _now()
        atomic_write_json(path, metadata)


def _cell_status(result: PronunciationResult) -> str:
    if result.status == "blocked" and any(e.type == ErrorType.UNRESOLVED_ENGINE for e in result.errors):
        return "unresolved"
    return result.status
