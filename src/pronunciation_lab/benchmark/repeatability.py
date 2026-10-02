"""Benchmark-level repeatability: rerun stored cells and compare evidence.

For each selected recording and runnable local engine, the cell is recomputed
twice -- once on a fresh engine instance (cold) and once more on the same
instance (warm) -- and compared with the stored cell:

* the whole normalized result except `processing` (wall-clock timings, which
  are expected to differ) must be identical;
* the stored raw arrays (posteriorgram, heard phones, confidences, spans) must
  be bit-identical.

Any difference is listed by JSON path, so a nondeterminism can be located
rather than tolerated.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from pronunciation_lab.benchmark.base import safe_analyze
from pronunciation_lab.benchmark.cells import EVIDENCE_STATUSES, CellStore, cell_id
from pronunciation_lab.benchmark.dataset import Recording
from pronunciation_lab.benchmark.engines import create_engine
from pronunciation_lab.benchmark.runner import classify_engine, extract_raw

# Fields that are measurements of this run, not evidence.
NON_ANALYTICAL = ("processing",)


def analytical_view(result) -> dict[str, Any]:
    data = result.model_dump(mode="json")
    for key in NON_ANALYTICAL:
        data.pop(key, None)
    return data


def diff_paths(a: Any, b: Any, path: str = "$", limit: int = 20) -> list[str]:
    out: list[str] = []

    def walk(x: Any, y: Any, p: str) -> None:
        if len(out) >= limit:
            return
        if type(x) is not type(y):
            out.append(f"{p}: type {type(x).__name__} != {type(y).__name__}")
        elif isinstance(x, dict):
            for k in sorted(set(x) | set(y)):
                if k not in x or k not in y:
                    out.append(f"{p}.{k}: present in one side only")
                else:
                    walk(x[k], y[k], f"{p}.{k}")
        elif isinstance(x, list):
            if len(x) != len(y):
                out.append(f"{p}: length {len(x)} != {len(y)}")
            for i, (xi, yi) in enumerate(zip(x, y)):
                walk(xi, yi, f"{p}[{i}]")
        elif x != y:
            out.append(f"{p}: {x!r} != {y!r}")

    walk(a, b, path)
    return out


def raw_differences(stored: dict[str, np.ndarray], fresh: dict[str, np.ndarray] | None) -> list[str]:
    if fresh is None:
        return ["no raw evidence from rerun"]
    out = []
    for key in sorted(set(stored) | set(fresh)):
        if key not in stored or key not in fresh:
            out.append(f"raw.{key}: present in one side only")
        elif stored[key].shape != fresh[key].shape or not np.array_equal(stored[key], fresh[key]):
            out.append(f"raw.{key}: arrays differ")
    return out


def check_repeatability(
    run_dir: Path,
    recordings: list[Recording],
    engine_ids: list[str],
    *,
    engine_factory: Callable[[str], Any] = create_engine,
    log: Callable[[str], None] = lambda message: None,
) -> dict[str, Any]:
    store = CellStore(run_dir)
    comparisons: list[dict[str, Any]] = []
    not_checked: list[dict[str, str]] = []

    for eid in engine_ids:
        availability = classify_engine(eid, engine_factory)
        if availability.state != "runnable":
            not_checked.append({"engine_id": eid, "reason": f"engine {availability.state}"})
            continue

        engine = engine_factory(eid)  # fresh instance: first rerun is cold
        for rec in recordings:
            cid = cell_id(rec.recording_id, eid)
            loaded = store.load(cid)
            if loaded.state != "complete" or loaded.cell.status not in EVIDENCE_STATUSES:
                not_checked.append({"cell_id": cid, "reason": f"stored cell {loaded.state}"
                                    + (f" ({loaded.cell.status})" if loaded.cell else "")})
                continue
            stored = loaded.cell
            stored_view = analytical_view(stored.result)
            stored_raw = store.load_raw(stored)

            entry: dict[str, Any] = {"cell_id": cid, "runs": []}
            for label in ("first_rerun", "second_rerun"):
                result = safe_analyze(
                    engine, rec.wav_path, rec.target_text,
                    recording_id=rec.recording_id, original_path=rec.source_path,
                )
                differences = diff_paths(stored_view, analytical_view(result))
                raw_diff = raw_differences(stored_raw, extract_raw(engine)) if stored_raw else []
                entry["runs"].append({
                    "label": label,
                    "run_type": result.processing.run_type,
                    "identical_evidence": not differences,
                    "identical_raw": not raw_diff,
                    "differences": differences + raw_diff,
                    "wall_time_ms": result.processing.wall_time_ms,
                    "stored_wall_time_ms": stored.performance.wall_time_ms if stored.performance else None,
                })
            entry["identical"] = all(r["identical_evidence"] and r["identical_raw"] for r in entry["runs"])
            comparisons.append(entry)
            log(f"{cid}: {'identical' if entry['identical'] else 'DIFFERS'}")

    return {
        "compared_fields": "entire PronunciationResult except processing; raw arrays bit-for-bit",
        "excluded_fields": list(NON_ANALYTICAL),
        "comparisons": comparisons,
        "not_checked": not_checked,
        "summary": {
            "cells_compared": len(comparisons),
            "reruns": sum(len(c["runs"]) for c in comparisons),
            "all_identical": bool(comparisons) and all(c["identical"] for c in comparisons),
            "differing_cells": [c["cell_id"] for c in comparisons if not c["identical"]],
        },
    }
