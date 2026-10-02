"""M5 — the ground-truth real-audio set and its frozen evidence.

Only the M2 benchmark recordings R01–R20, with target text from the manifest,
are ground truth for pronunciation validation. Anything else — notably "New
Recording 49", whose original target text is unknown — is refused here, so it
cannot be admitted as ground truth by accident. Target text is never
reconstructed by speech recognition.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from pronunciation_lab.benchmark.dataset import EXPECTED_IDS
from pronunciation_lab.benchmark.schema import PronunciationResult

FROZEN_RUN = "m2-20261002"
GROUND_TRUTH_IDS = EXPECTED_IDS  # R01..R20


def manifest(data_dir: Path) -> dict[str, dict[str, str]]:
    with (data_dir / "benchmark_manifest.csv").open(newline="", encoding="utf-8") as f:
        rows = {r["recording_id"].strip(): r for r in csv.DictReader(f)}
    return {rid: rows[rid] for rid in GROUND_TRUTH_IDS if rid in rows}


def require_ground_truth(recording_id: str, data_dir: Path) -> dict[str, str]:
    """The manifest row for a ground-truth recording; ValueError for anything else."""
    if recording_id not in GROUND_TRUTH_IDS:
        raise ValueError(f"{recording_id!r} is not a ground-truth benchmark recording (R01–R20 only)")
    rows = manifest(data_dir)
    if recording_id not in rows:
        raise ValueError(f"{recording_id!r} is missing from the benchmark manifest")
    return rows[recording_id]


def load_frozen(recording_id: str, engine_id: str, data_dir: Path, run: str = FROZEN_RUN) -> PronunciationResult:
    """The frozen M2 result for a ground-truth recording; its target text must equal the manifest's."""
    row = require_ground_truth(recording_id, data_dir)
    path = data_dir / "benchmark_results" / "runs" / run / "cells" / f"{recording_id}__{engine_id}.json"
    cell = json.loads(path.read_text(encoding="utf-8"))["cell"]
    result = PronunciationResult.model_validate(cell["result"])
    if result.recording.target.text != row["target_text"]:
        raise ValueError(f"{recording_id}: frozen result text differs from the manifest")
    return result
