"""Benchmark data-integrity checks.

`validate_cell` checks one cell on its own and is applied by the runner before
every write. `validate_run` checks a whole run directory against the manifest:
completeness of the nominal matrix, stray or duplicate files, raw evidence vs
normalized result, and that the source audio and manifest are byte-identical to
what preflight hashed when the run started.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from pronunciation_lab.benchmark.base import ErrorType
from pronunciation_lab.benchmark.cells import (
    CELL_STATUSES,
    EVIDENCE_STATUSES,
    BenchmarkCell,
    CellStore,
    CellValidation,
    cell_id,
)
from pronunciation_lab.benchmark.dataset import Recording, sha256_file

# Slack for comparing sums of separately measured perf_counter intervals.
TIMING_EPS_MS = 1.0


def validate_cell(cell: BenchmarkCell) -> CellValidation:
    issues: list[str] = []
    result = cell.result

    if cell.status not in CELL_STATUSES:
        issues.append(f"unknown status {cell.status!r}")

    if cell.cell_id != cell_id(cell.recording_id, cell.engine_id):
        issues.append("cell_id does not match recording/engine")

    if cell.status in EVIDENCE_STATUSES:
        if result is None:
            issues.append("evidence status without a result")
        else:
            if result.status != cell.status:
                issues.append(f"result status {result.status!r} != cell status {cell.status!r}")
            issues.extend(_result_metadata_issues(cell))
            if not result.words:
                issues.append("evidence status without word evidence")
            if cell.status == "partial" and not result.errors:
                issues.append("partial result without errors explaining it")
        issues.extend(_performance_issues(cell))

    elif cell.status == "failed":
        if not cell.errors:
            issues.append("failed cell without a structured error")
        if any(not e.type or not e.message for e in cell.errors):
            issues.append("failed cell error lacks type or message")

    elif cell.status == "blocked":
        if not cell.errors or not cell.errors[0].type:
            issues.append("blocked cell without a blocked reason")
        if cell.errors and cell.errors[0].type == ErrorType.UNRESOLVED_ENGINE:
            issues.append("unresolved engine recorded as blocked")
        if result is not None and result.words:
            issues.append("blocked cell carries word evidence")

    elif cell.status == "unresolved":
        if not cell.availability or cell.availability.get("reason") != ErrorType.UNRESOLVED_ENGINE:
            issues.append("unresolved cell without an unresolved reason")
        if result is not None and result.words:
            issues.append("unresolved cell carries word evidence")

    if cell.status not in EVIDENCE_STATUSES and cell.raw_evidence is not None:
        issues.append("raw evidence attached to a cell without evidence")

    return CellValidation(status="invalid" if issues else "valid", issues=issues)


def _result_metadata_issues(cell: BenchmarkCell) -> list[str]:
    result = cell.result
    issues = []
    if result.recording.id != cell.recording_id:
        issues.append(f"result recording id {result.recording.id!r} != {cell.recording_id!r}")
    if result.recording.target.text != cell.target_text:
        issues.append("result target text differs from the manifest")
    if result.engine.name != cell.engine_id:
        issues.append(f"result engine {result.engine.name!r} != {cell.engine_id!r}")
    if result.engine.mode == "local" and not result.engine.model:
        issues.append("local engine result without model identity")
    if not Path(result.recording.audio.analysis_path or "").name == Path(cell.input_filename).name:
        issues.append("result analysis file differs from the cell input file")
    return issues


def _performance_issues(cell: BenchmarkCell) -> list[str]:
    p = cell.performance
    if p is None:
        return ["evidence cell without performance"]

    issues = []
    for name in ("wall_time_ms", "inference_ms", "postprocessing_ms", "realtime_factor", "run_type"):
        if getattr(p, name) is None:
            issues.append(f"missing timing field {name}")
    if issues:
        return issues

    for name in ("wall_time_ms", "inference_ms", "postprocessing_ms", "realtime_factor"):
        if getattr(p, name) < 0:
            issues.append(f"negative {name}")
    parts = p.inference_ms + p.postprocessing_ms + (p.model_load_ms or 0.0)
    if parts > p.wall_time_ms + TIMING_EPS_MS:
        issues.append("timing parts exceed wall time")
    if (p.run_type == "cold") != (p.model_load_ms is not None):
        issues.append("model_load_ms present iff cold run violated")
    duration = cell.result.recording.audio.duration_ms if cell.result else 0.0
    if duration > 0 and abs(p.realtime_factor - p.wall_time_ms / duration) > 1e-6:
        issues.append("realtime_factor inconsistent with wall time and duration")
    if p.runner_wall_ms is not None and p.runner_wall_ms + TIMING_EPS_MS < p.wall_time_ms:
        issues.append("runner wall time shorter than engine wall time")
    return issues


def raw_correspondence_issues(cell: BenchmarkCell, raw: dict[str, np.ndarray]) -> list[str]:
    """Raw arrays must be the evidence the normalized result was built from."""
    if cell.result is None:
        return []
    evidence = cell.result.engine_evidence
    if "posteriorgram" not in evidence:
        return [] if not raw else ["raw evidence for an engine without a posteriorgram"]
    if not raw:
        return ["engine reports a posteriorgram but no raw evidence was stored"]

    issues = []
    recognition = evidence.get("recognition", {})
    if [str(x) for x in raw.get("heard_phones", [])] != list(recognition.get("phones", [])):
        issues.append("raw heard phones differ from the result")
    if raw.get("frame_spans") is not None and raw["frame_spans"].tolist() != recognition.get("frame_spans", []):
        if not (raw["frame_spans"].size == 0 and not recognition.get("frame_spans")):
            issues.append("raw frame spans differ from the result")
    if not np.allclose(raw.get("confidences", np.zeros(0)), np.asarray(recognition.get("confidences", []), dtype=float)):
        issues.append("raw confidences differ from the result")
    lp = raw.get("log_posteriors")
    if lp is None or lp.ndim != 2:
        issues.append("raw log posteriors missing or not 2-D")
    else:
        if lp.shape[0] != evidence["posteriorgram"]["n_frames"]:
            issues.append("raw posteriorgram frame count differs from the result")
        if lp.shape[1] != evidence["posteriorgram"]["vocab_size"] or len(raw.get("vocab", [])) != lp.shape[1]:
            issues.append("raw posteriorgram vocabulary size differs from the result")
        if lp.shape[0] != evidence["frame_clock"]["n_frames"]:
            issues.append("raw posteriorgram frame count differs from the frame clock")
    return issues


def validate_run(
    run_dir: Path,
    recordings: list[Recording],
    engine_ids: list[str],
    known_engines: list[str],
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    store = CellStore(run_dir)
    issues: list[dict[str, Any]] = []

    def issue(code: str, message: str, cid: str | None = None) -> None:
        issues.append({"code": code, "message": message, "cell_id": cid})

    metadata_path = run_dir / "run_metadata.json"
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else None
    if metadata is None:
        issue("run_metadata_missing", f"{metadata_path} does not exist")
    run_id = metadata["benchmark_run_id"] if metadata else None

    for eid in engine_ids:
        if eid not in known_engines:
            issue("engine_unknown", f"{eid} is not a registered engine")

    scan = store.scan()
    for name in scan["unexpected"]:
        issue("stray_file", f"unexpected file in cells/: {name}")
    for name in scan["temp"]:
        issue("temp_file_left", f"interrupted write left {name}")

    nominal = {cell_id(r.recording_id, e): (r, e) for r in recordings for e in engine_ids}
    for cid in sorted(set(scan["cells"]) - set(nominal)):
        issue("cell_outside_matrix", f"{cid} is not in the nominal matrix", cid)

    counts = {s: 0 for s in ("ok", "partial", "failed", "blocked", "unresolved", "missing", "corrupt")}
    seen_pairs: dict[tuple[str, str], str] = {}

    for cid, (rec, eid) in nominal.items():
        loaded = store.load(cid)
        if loaded.state != "complete":
            counts[loaded.state] += 1
            issue(f"cell_{loaded.state}", loaded.reason or f"{cid} has no result", cid)
            continue

        cell = loaded.cell
        counts[cell.status] += 1
        pair = (cell.recording_id, cell.engine_id)
        if pair in seen_pairs:
            issue("duplicate_cell", f"{cid} duplicates {seen_pairs[pair]}", cid)
        seen_pairs[pair] = cid

        if run_id is not None and cell.benchmark_run_id != run_id:
            issue("run_id_mismatch", f"{cid} belongs to run {cell.benchmark_run_id}", cid)
        if cell.target_text != rec.target_text:
            issue("target_text_mismatch", f"{cid} target text differs from the manifest", cid)
        if (cell.reading_style, cell.purpose) != (rec.reading_style, rec.purpose):
            issue("manifest_fields_mismatch", f"{cid} style/purpose differ from the manifest", cid)
        if cell.source_filename != rec.filename:
            issue("source_filename_mismatch", f"{cid} source file differs from the manifest", cid)

        for problem in validate_cell(cell).issues:
            issue("cell_invalid", problem, cid)
        if cell.status in EVIDENCE_STATUSES:
            for problem in raw_correspondence_issues(cell, store.load_raw(cell)):
                issue("raw_mismatch", problem, cid)

    # Source data must be byte-identical to what preflight hashed.
    if metadata:
        data_dir = Path(metadata["preflight"]["data_dir"])
        for key, expected in metadata["preflight"]["sha256"].items():
            path = data_dir / key
            if not path.is_file():
                issue("source_data_missing", f"{key} no longer exists")
            elif sha256_file(path) != expected:
                issue("source_data_modified", f"{key} changed since the run started")

    return {
        "ok": not issues,
        "benchmark_run_id": run_id,
        "nominal_cells": len(nominal),
        "counts": counts,
        "issues": issues,
    }

