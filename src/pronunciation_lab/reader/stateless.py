"""M12 sentence analysis for temporary stateless browser requests.

This module reuses the local Reader's M7 and target-confirmation domain
functions, but owns no store, session, job, or retained audio.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

import soundfile as sf

from pronunciation_lab.app.fluency import describe_region, empty_fluency
from pronunciation_lab.app.pipeline import analyze_pipeline
from pronunciation_lab.app.reduction import empty_reduction
from pronunciation_lab.benchmark.base import PronunciationEngine
from pronunciation_lab.benchmark.schema import PronunciationResult
from pronunciation_lab.reader import boundary as B
from pronunciation_lab.reader.target import confirm_target


def analyze_reader_view(
    engine: PronunciationEngine,
    full_result: PronunciationResult,
    full_view: dict[str, Any],
    analysis_path: Path,
    text: str,
    alternatives: list[str],
    lock,
) -> dict[str, Any]:
    """Return M12-shaped sentence evidence and M7 decisions for one engine."""
    samples, sample_rate = sf.read(str(analysis_path), dtype="float32", always_2d=False)
    if samples.ndim > 1:
        samples = samples.mean(axis=1)

    boundary = B.detect_boundary(full_result, samples, sample_rate)
    detected_state = boundary["state"]
    issues = B.validate_boundary(boundary)
    if issues:
        boundary = B._no_boundary(
            "NO_RELIABLE_BOUNDARY",
            ["the boundary evidence was inconsistent: " + "; ".join(issues)],
            boundary.get("duration_ms") or len(samples) * 1000.0 / sample_rate,
            full_result.engine.name,
        )
    boundary |= {
        "source": "own",
        "target_analysed": False,
        "feedback_withheld": False,
        "withheld_reason": None,
    }
    if boundary.get("defensible") is False:
        boundary["feedback_withheld"] = True
        boundary["withheld_reason"] = "boundary"
    if boundary["state"] == "NO_RELIABLE_BOUNDARY":
        plausible = B.continuation_plausible(full_result, detected_state)
        if plausible:
            boundary["reasons"] += plausible
            boundary["feedback_withheld"] = True
            boundary["withheld_reason"] = "boundary"

    result, view = full_result, full_view
    if boundary["state"] in B.NEEDS_TARGET_ANALYSIS and not boundary["feedback_withheld"]:
        target_bytes, sample_count = B.target_wav_bytes(analysis_path.read_bytes(), boundary["cut_ms"])
        with tempfile.TemporaryDirectory(prefix="pronunciation-lab-target-") as temp:
            target_path = Path(temp) / "target.wav"
            target_path.write_bytes(target_bytes)
            target_result, target_view = analyze_pipeline(
                engine,
                target_path,
                text,
                recording_id=f"stateless-target-{uuid.uuid4().hex[:8]}",
                lock=lock,
            )
        if target_result.status in ("failed", "blocked"):
            raise RuntimeError("The sentence could not be analysed on its own.")
        boundary = B.check_target_analysis(boundary, target_result, samples[:sample_count], sample_rate)
        boundary["target_analysed"] = True
        result, view = target_result, target_view
        if boundary.get("relied_on_local_support") and boundary["target_check"]["issues"]:
            boundary["feedback_withheld"] = True
            boundary["withheld_reason"] = "boundary"
            boundary["state"] = "BOUNDARY_UNCERTAIN"
            for region in boundary["regions"]:
                if region["kind"] == "overflow":
                    region["kind"] = "uncertain"

    if boundary["feedback_withheld"]:
        boundary["containment"] = {"ok": None, "issues": [], "count": 0, "checked": False}
    else:
        leaks = B.evidence_outside_target(view, result, boundary["cut_ms"])
        leaks += [
            f"M8 {observation['id']} [{observation['start_ms']:.0f}, "
            f"{observation['end_ms']:.0f}] ms lies outside the sentence"
            for observation in (view.get("fluency") or {}).get("observations") or []
            if observation["end_ms"] > boundary["cut_ms"] + 1.0
        ]
        boundary["containment"] = {
            "ok": not leaks, "issues": leaks[:20], "count": len(leaks), "checked": True,
        }
        if leaks:
            boundary["feedback_withheld"] = True
            boundary["withheld_reason"] = "containment"
            boundary["reasons"] += [f"{len(leaks)} pieces of evidence lay outside the sentence"]

    analysis = B.analysis_confidence(result) | {"shown": not boundary["feedback_withheld"]}
    boundary["analysis"] = analysis
    if boundary["feedback_withheld"]:
        view = _withheld_view(view, boundary.get("withheld_reason"))
    else:
        view["analysis_confidence"] = analysis
    view["boundary"] = _boundary_view(boundary)
    _add_continued_speech(view, boundary, full_result, samples, sample_rate)
    target_confirmation = confirm_target(result, alternatives)
    return {
        "state": "ok",
        "result": result.model_dump(mode="json"),
        "evidence": view,
        "target_confirmation": target_confirmation,
        "boundary": view["boundary"],
        "full_recording": {
            "result": full_result.model_dump(mode="json"),
            "evidence": full_view,
        },
    }


def _boundary_view(boundary: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": B.BOUNDARY_VERSION,
        "state": boundary["state"],
        "engine": boundary["engine"],
        "source": boundary.get("source"),
        "cut_ms": boundary["cut_ms"],
        "duration_ms": boundary["duration_ms"],
        "target_analysed": boundary["target_analysed"],
        "reasons": boundary["reasons"],
        "regions": [
            {key: region[key] for key in ("kind", "start_ms", "end_ms", "speech_ms") if key in region}
            for region in boundary["regions"]
            if region["end_ms"] > region["start_ms"]
        ],
        "evidence": boundary.get("evidence") or {},
        "feedback_withheld": bool(boundary.get("feedback_withheld")),
        "containment": boundary.get("containment"),
        "withheld_reason": boundary.get("withheld_reason"),
        "defensible": boundary.get("defensible"),
        "boundary_confidence": boundary.get("boundary_confidence"),
        "analysis": boundary.get("analysis"),
        "other_engine": None,
        "target_check": boundary.get("target_check"),
    }


def _add_continued_speech(view: dict[str, Any], boundary: dict[str, Any], full: PronunciationResult,
                          samples, sample_rate: int) -> None:
    fluency = view.get("fluency")
    if not isinstance(fluency, dict):
        return
    fluency["m7"] = {
        "state": boundary["state"],
        "target_analysed": boundary.get("target_analysed", False),
        "feedback_withheld": bool(boundary.get("feedback_withheld")),
        "cut_ms": boundary["cut_ms"],
    }
    if fluency.get("state") != "ok" or boundary.get("feedback_withheld"):
        return
    fluency["continued_speech"] = [
        describe_region(full, samples, sample_rate, region["start_ms"], region["end_ms"], region["kind"])
        for region in boundary["regions"]
        if region["kind"] in ("overflow", "uncertain") and region["end_ms"] > region["start_ms"]
    ]


def _withheld_view(view: dict[str, Any], reason: str | None) -> dict[str, Any]:
    message = (
        "No pronunciation feedback: part of the analysis lay outside this sentence, so none of it is shown."
        if reason == "containment"
        else B.WITHHELD_MESSAGE
    )
    engine = (view.get("coach") or {}).get("engine") or (view.get("reduction") or {}).get("engine") or {}
    out = {key: value for key, value in view.items() if key not in ("words", "coach", "reduction", "fluency")}
    out.update({
        "state": "boundary_withheld",
        "message": message,
        "words": [],
        "coach": {
            "version": (view.get("coach") or {}).get("version"),
            "state": "boundary_withheld",
            "message": message,
            "engine": engine,
            "observations": [],
            "patterns": [],
            "groups": [],
            "practice_targets": [],
            "integrity": {"ok": True, "issues": []},
        },
        "reduction": empty_reduction("boundary_withheld", engine),
        "fluency": empty_fluency("boundary_withheld", message, engine.get("id")),
    })
    out["reduction"].pop("timing_ms", None)
    return out
