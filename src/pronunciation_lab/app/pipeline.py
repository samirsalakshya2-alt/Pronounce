"""The analysis pipeline, callable without HTTP (M12 phase 1).

One recording, one engine:

    safe_analyze (under the process-wide inference lock)
    → M3 build_view → M4 build_coach → M5 build_reduction → M8 build_fluency

This is exactly what `AnalysisService._run` did inline; the lab (M3–M5) and the
M12 reader both call it, so they produce identical evidence and views for the
same analysis WAV, text and engine.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import soundfile as sf

from pronunciation_lab.app.audio_input import is_silent
from pronunciation_lab.app.coach import build_coach
from pronunciation_lab.app.diagnosis import build_view
from pronunciation_lab.app.fluency import build_fluency
from pronunciation_lab.app.reduction import build_reduction, empty_reduction
from pronunciation_lab.benchmark.base import PronunciationEngine, safe_analyze
from pronunciation_lab.benchmark.schema import PronunciationResult

SILENT_MESSAGE = "The recording appears to be silent. Check that the microphone is working."


def build_analysis_view(result: PronunciationResult, analysis_path: Path | None = None) -> dict[str, Any]:
    """M3 view + M4 coach + M5 reduction + M8 fluency for one result (no inference)."""
    view = build_view(result)
    # M4: interpretation of the same evidence (no extra inference).
    coach = build_coach(result)
    coach_timing = coach.pop("_timing_ms", None)
    view["coach"] = coach
    view["processing"]["coach_ms"] = sum(coach_timing.values()) if coach_timing else 0.0
    # M5: reduction / connected-speech layer over the M4 observations (M4 output unchanged).
    if coach["state"] == "ok":
        reduction = build_reduction(result, coach["observations"])
    else:
        reduction = empty_reduction(coach["state"], {"id": result.engine.name, "model": result.engine.model,
                                                     "phone_set": result.phone_set})
    view["reduction"] = reduction
    view["processing"]["reduction_ms"] = reduction.pop("timing_ms")
    if view.get("state") == "no_speech" and analysis_path is not None and is_silent(analysis_path):
        view["message"] = SILENT_MESSAGE
    # M8: fluency, an independent layer over the same evidence and audio (M3–M5 objects unchanged).
    t0 = time.perf_counter()
    samples, rate = _samples(analysis_path)
    view["fluency"] = build_fluency(result, samples, rate)
    view["processing"]["fluency_ms"] = (time.perf_counter() - t0) * 1000.0
    return view


def _samples(path: Path | None):
    if path is None or not Path(path).is_file():
        return None, None
    try:
        x, rate = sf.read(str(path), dtype="float32", always_2d=False)
    except (RuntimeError, OSError):
        return None, None
    return (x.mean(axis=1) if x.ndim > 1 else x), rate


def analyze_pipeline(
    engine: PronunciationEngine,
    analysis_path: Path,
    text: str,
    *,
    recording_id: str,
    lock: threading.Lock,
    original_path: Path | None = None,
    on_inference_done: Callable[[], None] | None = None,
) -> tuple[PronunciationResult, dict[str, Any]]:
    """Run one engine on one analysis WAV and build its view. At most one inference runs under `lock`."""
    with lock:
        result = safe_analyze(engine, analysis_path, text, recording_id=recording_id, original_path=original_path)
        if on_inference_done is not None:
            on_inference_done()
    return result, build_analysis_view(result, analysis_path)
