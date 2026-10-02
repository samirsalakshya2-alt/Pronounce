"""Benchmark cells and their on-disk store.

A cell is one (recording, engine) evaluation. It wraps the engine's own
`PronunciationResult` -- the M1 contract, unchanged -- with what the benchmark
needs around it: which run, which recording, the cell status, performance, the
raw-evidence file, and validation.

Cell status is the benchmark's classification and deliberately keeps apart:

    ok          evidence produced
    partial     evidence produced, something expected missing (see errors)
    failed      the engine ran and broke, or its result was unusable
    blocked     the engine cannot run (credentials, model, service)
    unresolved  no defensible implementation of the engine exists

Storage is one JSON file per cell plus, for engines with a posteriorgram, one
`.npz` raw-evidence file. Writes are atomic (temp file, fsync, rename). A cell
file carries a SHA-256 of its own content and of its raw file, so a truncated,
edited or mismatched file is detected as corrupt and never counted as complete.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, ValidationError

from pronunciation_lab.benchmark.schema import PronunciationResult

CELL_SCHEMA_VERSION = "1"

CellStatus = Literal["ok", "partial", "failed", "blocked", "unresolved"]
CELL_STATUSES: tuple[str, ...] = ("ok", "partial", "failed", "blocked", "unresolved")
EVIDENCE_STATUSES = ("ok", "partial")

_CELL_FILE_RE = re.compile(r"^(R\d{2,})__([a-z0-9_]+)\.json$")


class CellErrorType:
    """Benchmark-level error codes, in addition to the M1 `ErrorType` codes."""

    MALFORMED_ENGINE_RESULT = "malformed_engine_result"
    RESULT_METADATA_MISMATCH = "result_metadata_mismatch"
    RAW_EVIDENCE_MISMATCH = "raw_evidence_mismatch"
    ENGINE_CONSTRUCTION_FAILED = "engine_construction_failed"


class CellError(BaseModel):
    type: str
    message: str
    stage: str | None = None
    retryable: bool | None = None


class CellPerformance(BaseModel):
    """Copied from the engine's ProcessingInfo, plus the runner's own clock."""

    wall_time_ms: float | None = None
    model_load_ms: float | None = None
    inference_ms: float | None = None
    postprocessing_ms: float | None = None
    realtime_factor: float | None = None
    run_type: Literal["cold", "warm"] | None = None
    device: str | None = None
    stages_ms: dict[str, float] | None = None
    api_latency_ms: float | None = None
    provider_processing_ms: float | None = None
    # Measured by the runner around the whole cell, including raw-evidence
    # extraction but not the cell write.
    runner_wall_ms: float | None = None


class RawEvidenceRef(BaseModel):
    path: str  # relative to the run directory
    sha256: str
    format: Literal["npz"] = "npz"
    arrays: dict[str, list[int]]  # name -> shape


class CellValidation(BaseModel):
    status: Literal["valid", "invalid", "unchecked"] = "unchecked"
    issues: list[str] = Field(default_factory=list)


class BenchmarkCell(BaseModel):
    cell_schema_version: str = CELL_SCHEMA_VERSION

    benchmark_run_id: str
    cell_id: str
    recording_id: str
    engine_id: str

    engine_version: str | None = None
    engine_model: str | None = None
    engine_mode: Literal["local", "cloud"] | None = None

    input_filename: str  # WAV, relative to the data directory
    source_filename: str  # original M4A, relative to the data directory
    target_text: str
    reading_style: str
    purpose: str
    text_group: str | None = None

    status: CellStatus
    attempt: int = 1
    started_at: str | None = None
    finished_at: str | None = None

    performance: CellPerformance | None = None
    result: PronunciationResult | None = None
    raw_evidence: RawEvidenceRef | None = None

    errors: list[CellError] = Field(default_factory=list)
    # Blocked / unresolved: the engine's own readiness statement.
    availability: dict[str, Any] | None = None

    validation: CellValidation = Field(default_factory=CellValidation)


def cell_id(recording_id: str, engine_id: str) -> str:
    return f"{recording_id}__{engine_id}"


def _canonical(cell: BenchmarkCell) -> str:
    return cell.model_dump_json()


def _digest(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write `data` to `path` so that `path` is either the old or the new file.

    Never a partial one: write a uniquely named temp file in the same directory,
    fsync it, rename it over the target, fsync the directory.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_bytes(
        path, (json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n").encode("utf-8")
    )


@dataclass
class LoadedCell:
    state: Literal["complete", "missing", "corrupt"]
    cell: BenchmarkCell | None = None
    reason: str | None = None


class CellStore:
    """Per-cell JSON files under `<run_dir>/cells`, raw arrays under `<run_dir>/raw`."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.cells_dir = self.run_dir / "cells"
        self.raw_dir = self.run_dir / "raw"

    def cell_path(self, cid: str) -> Path:
        return self.cells_dir / f"{cid}.json"

    def raw_path(self, cid: str) -> Path:
        return self.raw_dir / f"{cid}.npz"

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def write(self, cell: BenchmarkCell, raw_arrays: dict[str, np.ndarray] | None = None) -> BenchmarkCell:
        """Persist a cell (and its raw arrays first, so a cell never points at nothing)."""
        if raw_arrays is not None:
            buffer = io.BytesIO()
            np.savez_compressed(buffer, **raw_arrays)
            data = buffer.getvalue()
            path = self.raw_path(cell.cell_id)
            atomic_write_bytes(path, data)
            cell.raw_evidence = RawEvidenceRef(
                path=str(path.relative_to(self.run_dir)),
                sha256=_digest(data),
                arrays={k: list(np.asarray(v).shape) for k, v in raw_arrays.items()},
            )

        payload = _canonical(cell)
        atomic_write_json(
            self.cell_path(cell.cell_id),
            {"sha256": _digest(payload), "cell": json.loads(payload)},
        )
        return cell

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def load(self, cid: str) -> LoadedCell:
        path = self.cell_path(cid)
        if not path.exists():
            return LoadedCell("missing")

        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return LoadedCell("corrupt", reason=f"unparseable: {type(exc).__name__}")

        if not isinstance(envelope, dict) or set(envelope) != {"sha256", "cell"}:
            return LoadedCell("corrupt", reason="not a cell envelope")

        try:
            cell = BenchmarkCell.model_validate(envelope["cell"])
        except ValidationError as exc:
            return LoadedCell("corrupt", reason=f"schema: {exc.error_count()} errors")

        if _digest(_canonical(cell)) != envelope["sha256"]:
            return LoadedCell("corrupt", cell=cell, reason="checksum mismatch")

        if cell.cell_id != cid or cell.cell_id != cell_id(cell.recording_id, cell.engine_id):
            return LoadedCell("corrupt", cell=cell, reason="cell identity does not match its file name")

        if cell.raw_evidence is not None:
            raw = self.run_dir / cell.raw_evidence.path
            if not raw.is_file():
                return LoadedCell("corrupt", cell=cell, reason="raw evidence file missing")
            if _digest(raw.read_bytes()) != cell.raw_evidence.sha256:
                return LoadedCell("corrupt", cell=cell, reason="raw evidence checksum mismatch")

        return LoadedCell("complete", cell=cell)

    def load_raw(self, cell: BenchmarkCell) -> dict[str, np.ndarray]:
        if cell.raw_evidence is None:
            return {}
        with np.load(self.run_dir / cell.raw_evidence.path, allow_pickle=False) as data:
            return {k: data[k] for k in data.files}

    def scan(self) -> dict[str, Any]:
        """Every file in the cells directory, classified."""
        cells: dict[str, str] = {}
        unexpected: list[str] = []
        temp: list[str] = []
        if self.cells_dir.is_dir():
            for path in sorted(self.cells_dir.iterdir()):
                if path.name.endswith(".tmp"):
                    temp.append(path.name)
                elif _CELL_FILE_RE.match(path.name):
                    cells[path.stem] = path.name
                else:
                    unexpected.append(path.name)
        return {"cells": cells, "unexpected": unexpected, "temp": temp}
