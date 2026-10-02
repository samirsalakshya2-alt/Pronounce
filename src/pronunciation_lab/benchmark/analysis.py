"""Task-level analysis of a benchmark run, for M3.

Everything here is *descriptive*. There is no overall score and no ranking. The
structure of every finding is

    observed (what each engine decoded / measured)
      -> evidence (posteriors, N-best, timing, acoustics)
      -> interpretation candidate (rule stated, labelled "candidate")

and the rules are deliberately conservative:

* a substitution is "a different phone was decoded", not "mispronounced";
* low confidence is uncertainty, not error;
* CTC spans locate phones but are not durations, so no duration-based claim is
  made (no "swallowed because short");
* a deviation seen only in faster speech, on a function word, a word-final
  consonant or an unstressed vowel, is a *connected-speech reduction
  candidate*, not an error;
* where the alignment itself looks unreliable (many insertions piled onto one
  word), the word is flagged `alignment_suspect` and its operations are not
  interpreted.

Cells that are missing, corrupt, invalid or have no evidence are excluded and
listed with the reason; analysis never fails because one cell is bad.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pronunciation_lab.benchmark.cells import (
    EVIDENCE_STATUSES,
    BenchmarkCell,
    CellStore,
    atomic_write_json,
    cell_id,
)
from pronunciation_lab.benchmark.dataset import TEXT_GROUPS, Recording

# ----------------------------------------------------------------------
# Thresholds (stated once, reported in every output)
# ----------------------------------------------------------------------

# Expected-phone posterior at or above which the expected phone is called
# "plausibly present" even though another phone won the decode. Same value as
# OpenPronounce's PHONE_PLAUSIBLE_POSTERIOR, used here for both engines.
PLAUSIBLE_POSTERIOR = 0.05
# Top-1 minus top-2 N-best probability below which a decode is "ambiguous".
AMBIGUOUS_MARGIN = 0.2
# A gap between consecutive heard phones at least this long counts as a pause.
PAUSE_MS = 250.0
# A word with at least this many inserted phones attached is alignment-suspect.
SUSPECT_INSERTIONS = 3

THRESHOLDS = {
    "plausible_posterior": PLAUSIBLE_POSTERIOR,
    "ambiguous_margin": AMBIGUOUS_MARGIN,
    "pause_ms": PAUSE_MS,
    "suspect_insertions_per_word": SUSPECT_INSERTIONS,
    "suspect_scope": "the word carrying the insertions and its two neighbours",
}

CAVEATS = [
    "Single speaker, 20 recordings, 7 distinct sentences: observations describe this "
    "benchmark only and do not generalise to Indian English or to other speakers.",
    "Both runnable engines use the same checkpoint and posteriors; their agreement is "
    "not independent confirmation.",
    "A decoded phone differing from the expected phone is a recogniser observation, "
    "not a pronunciation judgement. No human annotation exists for this dataset.",
    "CTC spans mark posterior peaks, not phone durations; no duration claims are made.",
    "No engine observed stress or prosody; lexical stress comes from espeak (expected), "
    "and only acoustic correlates over spans are reported.",
]

_VOWEL_CHARS = set("aeiouæɐɑɒɔəɚɛɜɝɪʊʌᵻɨʉøœɵyɯɤ")

FUNCTION_WORDS = frozenset(
    """a an the to of and in on at for from with by as into over i you he she it we
    they me him her us them my your his its our their this that these those is are
    was were be been am has have had do does did will would can could should shall
    may might must not no so but or if than then there what which who when where
    although before during still""".split()
)

# Phenomenon tasks. Targets are written per phone set because the engines do not
# share one (OpenPronounce drops length marks; raw espeak keeps them).
TASKS: dict[str, dict[str, Any]] = {
    "th_dental_fricatives": {
        "recordings": ("R01", "R02", "R03", "R04"),
        "phenomenon": "/θ/ and /ð/ realisation; R04 read with deliberate /θ/→/t/",
        "targets": {"θ": ("t", "s", "f", "ð"), "ð": ("d", "z", "v", "θ")},
        "stop_realisation": {"θ": "t", "ð": "d"},
    },
    "v_w": {
        "recordings": ("R05", "R06", "R07"),
        "phenomenon": "/v/ vs /w/",
        "targets": {"v": ("w", "b", "f", "ʋ"), "w": ("v", "ʋ", "u", "uː")},
    },
    "i_contrast": {
        "recordings": ("R08", "R09", "R10"),
        "phenomenon": "/ɪ/ vs /iː/",
        "targets": {"ɪ": ("i", "iː", "ᵻ", "eɪ"), "i": ("ɪ", "iː", "eɪ"), "iː": ("ɪ", "i", "eɪ"), "ᵻ": ("ɪ", "i", "iː")},
        "classes": {"ɪ": "short_i", "ᵻ": "short_i", "i": "long_i", "iː": "long_i"},
    },
    "r_and_clusters": {
        "recordings": ("R11", "R12", "R13"),
        "phenomenon": "/r/ and consonant clusters",
        "targets": "rhotic_and_cluster",
    },
    "function_words_boundaries": {
        "recordings": ("R14", "R15", "R16"),
        "phenomenon": "word boundaries, stress and function words",
        "targets": "function_words",
    },
    "long_connected_speech": {
        "recordings": ("R17", "R18", "R19", "R20"),
        "phenomenon": "longer sentences: rhythm, reduction, breath management",
        "targets": "all",
    },
}


def is_vowel(phone: str | None) -> bool:
    return bool(phone) and any(ch in _VOWEL_CHARS for ch in phone)


def is_rhotic(phone: str | None) -> bool:
    return bool(phone) and ("ɹ" in phone or "ɚ" in phone or "r" in phone or "ɝ" in phone)


def speed_rank(reading_style: str) -> str:
    style = reading_style.lower()
    if "deliberate /" in style:
        return "deliberate_substitution"
    if "slow" in style:
        return "slow"
    if "fast" in style:
        return "fast"
    if "normal" in style:
        return "normal"
    return "unknown"


SPEED_ORDER = {"slow": 0, "normal": 1, "fast": 2}


# ----------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------


@dataclass
class LoadedRun:
    run_dir: Path
    cells: dict[str, BenchmarkCell]  # every complete cell, any status
    evidence: dict[str, BenchmarkCell]  # usable evidence cells only
    excluded: list[dict[str, str]]
    # Cells that could not be loaded: cell_id -> "missing" | "corrupt".
    unloadable: dict[str, str]
    recordings: dict[str, Recording]
    engines: list[str]


def load_run(run_dir: Path, recordings: list[Recording], engines: list[str]) -> LoadedRun:
    store = CellStore(run_dir)
    cells: dict[str, BenchmarkCell] = {}
    evidence: dict[str, BenchmarkCell] = {}
    excluded: list[dict[str, str]] = []
    unloadable: dict[str, str] = {}

    for rec in recordings:
        for eid in engines:
            cid = cell_id(rec.recording_id, eid)
            loaded = store.load(cid)
            if loaded.state != "complete":
                unloadable[cid] = loaded.state
                excluded.append({"cell_id": cid, "reason": loaded.state + (f": {loaded.reason}" if loaded.reason else "")})
                continue
            cell = loaded.cell
            cells[cid] = cell
            if cell.status not in EVIDENCE_STATUSES:
                excluded.append({"cell_id": cid, "reason": f"no evidence ({cell.status})"})
                continue
            problem = _evidence_problem(cell)
            if problem:
                excluded.append({"cell_id": cid, "reason": f"malformed evidence: {problem}"})
                continue
            evidence[cid] = cell

    return LoadedRun(
        run_dir=Path(run_dir),
        cells=cells,
        evidence=evidence,
        excluded=excluded,
        unloadable=unloadable,
        recordings={r.recording_id: r for r in recordings},
        engines=engines,
    )


def _evidence_problem(cell: BenchmarkCell) -> str | None:
    if cell.validation.status == "invalid":
        return "cell failed validation: " + "; ".join(cell.validation.issues)
    result = cell.result
    if result is None or not result.words:
        return "no words"
    for word in result.words:
        for p in word.phonemes:
            if "operation" not in p.engine_evidence:
                return "phoneme without alignment operation"
            if p.timing.start_ms is None or p.timing.end_ms is None:
                return "phoneme without timing"
    return None


# ----------------------------------------------------------------------
# Rows
# ----------------------------------------------------------------------


def phoneme_rows(cell: BenchmarkCell) -> list[dict[str, Any]]:
    """One flat row per expected phoneme, with the evidence attached to it."""
    rows = []
    result = cell.result
    suspects = alignment_suspect_words(result.words)
    # Whether this engine reports expected stress at all. Without it, a null
    # stress means "unknown", never "unstressed".
    stress_known = any(p.expected.stress for w in result.words for p in w.phonemes)
    for wi, word in enumerate(result.words):
        suspect = wi in suspects
        for p in word.phonemes:
            nbest = [(c.phoneme, c.probability) for c in p.observed.nbest]
            margin = (nbest[0][1] - nbest[1][1]) if len(nbest) > 1 else None
            features = p.acoustic.spectral_features or {}
            rows.append({
                "recording_id": cell.recording_id,
                "engine_id": cell.engine_id,
                "phone_set": result.phone_set,
                "reading_style": cell.reading_style,
                "speed": speed_rank(cell.reading_style),
                "word_index": wi,
                "word": word.word,
                "function_word": word.word in FUNCTION_WORDS,
                "position_in_word": p.expected.position,
                "word_length": len(word.phonemes),
                "expected": p.expected.phoneme,
                "expected_stress": p.expected.stress,
                "stress_known": stress_known,
                "observed": p.observed.top,
                "operation": p.engine_evidence["operation"],
                "confidence": p.observed.confidence,
                "expected_posterior": p.engine_evidence.get("expected_phone_posterior"),
                "nbest": nbest,
                "nbest_margin": margin,
                "inserted_after": [x["phone"] for x in p.engine_evidence.get("extra_heard_phones", [])],
                "start_ms": p.timing.start_ms,
                "end_ms": p.timing.end_ms,
                "timing_source": p.timing.source,
                "relative_energy": p.acoustic.relative_energy,
                "energy_db": p.acoustic.energy_db,
                "f0_hz": p.acoustic.f0_hz,
                "voicing": p.acoustic.voicing,
                "zero_crossing_rate": features.get("zero_crossing_rate"),
                "spectral_centroid_hz": features.get("spectral_centroid_hz"),
                "alignment_suspect": suspect,
                "provider_flagged_word": word.engine_evidence.get("flagged_by_provider"),
            })
    return rows


def _word_insertions(word) -> int:
    return sum(len(p.engine_evidence.get("extra_heard_phones", [])) for p in word.phonemes)


def alignment_suspect_words(words) -> set[int]:
    """Indices of words whose alignment is not trustworthy.

    A pile of inserted phones on one word is material the aligner could not
    place; it usually belongs to a *neighbouring* word, whose own phones were
    then matched against the wrong part of the decode (R08: the real "evening"
    phones land as insertions on "the", and "evening" is aligned onto a
    repeated "bɪɡɪnɪŋ"). So the word carrying the insertions and both of its
    neighbours are suspect.
    """
    heavy = [i for i, w in enumerate(words) if _word_insertions(w) >= SUSPECT_INSERTIONS]
    return {j for i in heavy for j in (i - 1, i, i + 1) if 0 <= j < len(words)}


def _posterior_of(nbest: list[tuple[str, float]], phone: str) -> float | None:
    for candidate, probability in nbest:
        if candidate == phone:
            return probability
    return None


def _stats(values: Iterable[float | None]) -> dict[str, Any]:
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "min": min(vals),
        "median": statistics.median(vals),
        "mean": statistics.fmean(vals),
        "max": max(vals),
    }


# ----------------------------------------------------------------------
# Per-cell measurements
# ----------------------------------------------------------------------


def cell_measurements(cell: BenchmarkCell) -> dict[str, Any]:
    rows = phoneme_rows(cell)
    ops = Counter(r["operation"] for r in rows)
    insertions = sum(len(r["inserted_after"]) for r in rows)
    first = cell.result.words[0].phonemes[0] if cell.result.words and cell.result.words[0].phonemes else None
    leading = len(first.engine_evidence.get("leading_heard_phones", [])) if first else 0
    insertions += leading

    heard = [r for r in rows if r["timing_source"] == "engine"]
    spans = cell.result.engine_evidence.get("recognition", {}).get("frame_spans", [])
    ms = cell.result.engine_evidence.get("frame_clock", {}).get("ms_per_frame", 20.0)
    onsets = sorted((s[0] * ms, s[1] * ms) for s in spans)
    pauses = [b[0] - a[1] for a, b in zip(onsets, onsets[1:]) if b[0] - a[1] >= PAUSE_MS]
    speech_span_ms = (onsets[-1][1] - onsets[0][0]) if onsets else None
    n_expected = len(rows)
    suspect_words = sorted({(r["word_index"], r["word"]) for r in rows if r["alignment_suspect"]})

    return {
        "n_expected": n_expected,
        "n_heard": len(spans),
        "match": ops.get("match", 0),
        "substitution": ops.get("substitution", 0),
        "omission": ops.get("omission", 0),
        "insertion": insertions,
        "derived_phone_error_rate": (
            (ops.get("substitution", 0) + ops.get("omission", 0) + insertions) / n_expected if n_expected else None
        ),
        "provider_phone_error_rate": cell.result.engine_evidence.get("comparison", {}).get("phone_error_rate"),
        "speech_span_ms": speech_span_ms,
        "heard_phones_per_s": (len(spans) / (speech_span_ms / 1000.0)) if speech_span_ms else None,
        "pauses": len(pauses),
        "pause_ms_total": sum(pauses),
        "phones_per_s_excluding_pauses": (
            len(spans) / ((speech_span_ms - sum(pauses)) / 1000.0)
            if speech_span_ms and speech_span_ms > sum(pauses)
            else None
        ),
        "mean_confidence_heard": _stats(r["confidence"] for r in heard).get("mean"),
        "mean_expected_posterior": _stats(r["expected_posterior"] for r in rows).get("mean"),
        "alignment_suspect_words": [w for _, w in suspect_words],
    }


# ----------------------------------------------------------------------
# Matrix and performance
# ----------------------------------------------------------------------


def matrix(run: LoadedRun) -> dict[str, Any]:
    grid: dict[str, dict[str, Any]] = {}
    counts = Counter()
    for eid in run.engines:
        grid[eid] = {}
        for rid in run.recordings:
            cell = run.cells.get(cell_id(rid, eid))
            if cell is None:
                state = run.unloadable.get(cell_id(rid, eid), "missing")
                grid[eid][rid] = {"status": state}
                counts[state] += 1
                continue
            grid[eid][rid] = {
                "status": cell.status,
                "error_type": cell.errors[0].type if cell.errors else None,
                "validation": cell.validation.status,
            }
            counts[cell.status] += 1
    by_engine = {
        eid: dict(Counter(v["status"] for v in grid[eid].values())) for eid in run.engines
    }
    return {
        "nominal_cells": len(run.engines) * len(run.recordings),
        "counts": dict(counts),
        "by_engine": by_engine,
        "matrix": grid,
    }


def performance(run: LoadedRun) -> dict[str, Any]:
    metadata = json.loads((run.run_dir / "run_metadata.json").read_text())
    out: dict[str, Any] = {
        "note": (
            "Local CPU inference on one machine. Cold = first cell of an engine in a "
            "session (includes model load, reported separately); warm = all later "
            "cells. No cloud timings exist (cloud engines blocked). No scalability "
            "claim beyond these 20 recordings (4.1-16.5 s) is supported."
        ),
        "environment": metadata.get("environment"),
        "sessions": [
            {k: s.get(k) for k in ("started_at", "finished_at", "duration_s", "retry_failed", "force")}
            | {"executed": len(s.get("executed", [])), "recorded_without_execution": len(s.get("recorded_without_execution", []))}
            for s in metadata.get("sessions", [])
        ],
        "engines": {},
    }
    for eid in run.engines:
        cells = [c for c in run.evidence.values() if c.engine_id == eid]
        if not cells:
            continue
        cold = [c for c in cells if c.performance.run_type == "cold"]
        warm = [c for c in cells if c.performance.run_type == "warm"]
        per_rec = []
        for c in sorted(cells, key=lambda c: c.recording_id):
            p = c.performance
            per_rec.append({
                "recording_id": c.recording_id,
                "audio_ms": c.result.recording.audio.duration_ms,
                "run_type": p.run_type,
                "model_load_ms": p.model_load_ms,
                "inference_ms": p.inference_ms,
                "postprocessing_ms": p.postprocessing_ms,
                "wall_time_ms": p.wall_time_ms,
                "realtime_factor": p.realtime_factor,
                "inference_ms_per_audio_s": p.inference_ms / (c.result.recording.audio.duration_ms / 1000.0),
            })
        out["engines"][eid] = {
            "device": cells[0].performance.device,
            "model": cells[0].engine_model,
            "cold_runs": len(cold),
            "warm_runs": len(warm),
            "model_load_ms": _stats(c.performance.model_load_ms for c in cold),
            "warm": {
                "inference_ms": _stats(c.performance.inference_ms for c in warm),
                "postprocessing_ms": _stats(c.performance.postprocessing_ms for c in warm),
                "wall_time_ms": _stats(c.performance.wall_time_ms for c in warm),
                "realtime_factor": _stats(c.performance.realtime_factor for c in warm),
                "inference_ms_per_audio_s": _stats(r["inference_ms_per_audio_s"] for r in per_rec if r["run_type"] == "warm"),
            },
            "per_recording": per_rec,
        }
    return out


# ----------------------------------------------------------------------
# Tasks
# ----------------------------------------------------------------------


def _task_rows(task: dict[str, Any], rows: list[dict[str, Any]], cell: BenchmarkCell) -> list[dict[str, Any]]:
    targets = task["targets"]
    if isinstance(targets, dict):
        return [r for r in rows if r["expected"] in targets]
    if targets == "function_words":
        return [r for r in rows if r["function_word"]]
    if targets == "rhotic_and_cluster":
        selected = []
        for word_index in sorted({r["word_index"] for r in rows}):
            word_rows = [r for r in rows if r["word_index"] == word_index]
            in_cluster = set()
            run: list[int] = []
            for i, r in enumerate(word_rows + [{"expected": "a"}]):
                if not is_vowel(r["expected"]):
                    run.append(i)
                else:
                    if len(run) >= 2:
                        in_cluster.update(run)
                    run = []
            for i, r in enumerate(word_rows):
                if i in in_cluster or is_rhotic(r["expected"]):
                    selected.append(r | {"in_cluster": i in in_cluster, "rhotic": is_rhotic(r["expected"])})
        return selected
    return rows


def _outcome(task_name: str, task: dict[str, Any], row: dict[str, Any]) -> str:
    if row["alignment_suspect"]:
        return "alignment_suspect"
    if row["operation"] == "omission":
        return "not_decoded"
    if row["operation"] == "match":
        return "decoded_as_expected"
    if task_name == "th_dental_fricatives" and row["observed"] == task["stop_realisation"].get(row["expected"]):
        return "decoded_as_stop"
    if task_name == "i_contrast":
        classes = task["classes"]
        if classes.get(row["observed"]) == classes.get(row["expected"]):
            return "same_class_length_differs"
        if row["observed"] in classes:
            return "crossed_contrast"
    return "decoded_as_other"


def task_analysis(run: LoadedRun) -> dict[str, Any]:
    out: dict[str, Any] = {"thresholds": THRESHOLDS, "caveats": CAVEATS, "tasks": {}}
    for name, task in TASKS.items():
        entry: dict[str, Any] = {
            "phenomenon": task["phenomenon"],
            "recordings": list(task["recordings"]),
            "by_engine": {},
        }
        for eid in run.engines:
            cells_status = {
                rid: (run.cells[cell_id(rid, eid)].status if cell_id(rid, eid) in run.cells else "missing")
                for rid in task["recordings"]
            }
            usable = [run.evidence[cell_id(rid, eid)] for rid in task["recordings"] if cell_id(rid, eid) in run.evidence]
            if not usable:
                entry["by_engine"][eid] = {"status": sorted(set(cells_status.values())), "cells": cells_status}
                continue

            occurrences = []
            per_recording = {}
            for cell in usable:
                rows = _task_rows(task, phoneme_rows(cell), cell)
                outcomes = Counter()
                for row in rows:
                    outcome = _outcome(name, task, row)
                    outcomes[outcome] += 1
                    competitors = task["targets"].get(row["expected"], ()) if isinstance(task["targets"], dict) else ()
                    occurrences.append({
                        "recording_id": row["recording_id"],
                        "reading_style": row["reading_style"],
                        "word": row["word"],
                        "word_index": row["word_index"],
                        "position_in_word": row["position_in_word"],
                        "expected": row["expected"],
                        "expected_stress": row["expected_stress"],
                        "observed": row["observed"],
                        "operation": row["operation"],
                        "outcome": outcome,
                        "confidence": row["confidence"],
                        "expected_posterior": row["expected_posterior"],
                        "expected_plausibly_present": (
                            row["expected_posterior"] is not None and row["expected_posterior"] >= PLAUSIBLE_POSTERIOR
                        ),
                        "competitor_posteriors": {c: _posterior_of(row["nbest"], c) for c in competitors},
                        "nbest": row["nbest"],
                        "nbest_margin": row["nbest_margin"],
                        "ambiguous": row["nbest_margin"] is not None and row["nbest_margin"] < AMBIGUOUS_MARGIN,
                        "start_ms": row["start_ms"],
                        "end_ms": row["end_ms"],
                        "timing_source": row["timing_source"],
                        "acoustic": {
                            "relative_energy": row["relative_energy"],
                            "voicing": row["voicing"],
                            "f0_hz": row["f0_hz"],
                            "zero_crossing_rate": row["zero_crossing_rate"],
                            "spectral_centroid_hz": row["spectral_centroid_hz"],
                        },
                        "in_cluster": row.get("in_cluster"),
                        "function_word": row["function_word"],
                    })
                per_recording[cell.recording_id] = {
                    "reading_style": cell.reading_style,
                    "n_targets": len(rows),
                    "outcomes": dict(outcomes),
                    "mean_expected_posterior": _stats(r["expected_posterior"] for r in rows).get("mean"),
                }
            entry["by_engine"][eid] = {
                "status": "evaluated",
                "cells": cells_status,
                "phone_set": usable[0].result.phone_set,
                "per_recording": per_recording,
                "occurrences": occurrences,
            }
        out["tasks"][name] = entry
    return out


# ----------------------------------------------------------------------
# Global phenomena
# ----------------------------------------------------------------------


def phenomena(run: LoadedRun) -> dict[str, Any]:
    out: dict[str, Any] = {"thresholds": THRESHOLDS, "caveats": CAVEATS, "engines": {}}
    for eid in run.engines:
        cells = sorted((c for c in run.evidence.values() if c.engine_id == eid), key=lambda c: c.recording_id)
        if not cells:
            continue
        rows = [r for c in cells for r in phoneme_rows(c)]
        clean = [r for r in rows if not r["alignment_suspect"]]
        subs = Counter((r["expected"], r["observed"]) for r in clean if r["operation"] == "substitution")
        omitted = Counter(r["expected"] for r in clean if r["operation"] == "omission")
        inserted = Counter(p for r in clean for p in r["inserted_after"])
        matched = [r for r in clean if r["operation"] == "match"]
        substituted = [r for r in clean if r["operation"] == "substitution"]
        omitted_rows = [r for r in clean if r["operation"] == "omission"]

        stressed = [r for r in clean if is_vowel(r["expected"]) and r["expected_stress"] == "primary" and r["operation"] != "omission"]
        unstressed = [r for r in clean if is_vowel(r["expected"]) and r["expected_stress"] is None and r["operation"] != "omission"]
        has_stress = any(r["expected_stress"] for r in rows)

        out["engines"][eid] = {
            "phone_set": cells[0].result.phone_set,
            "per_recording": {c.recording_id: cell_measurements(c) for c in cells},
            "phoneme_identity": {
                "expected_phonemes": len(rows),
                "excluded_alignment_suspect": len(rows) - len(clean),
                "match": len(matched),
                "substitution": len(substituted),
                "omission": len(omitted_rows),
                "insertion": sum(len(r["inserted_after"]) for r in clean),
            },
            "top_substitutions": [
                {"expected": e, "observed": o, "count": n} for (e, o), n in subs.most_common(25)
            ],
            "top_omissions": [{"expected": e, "count": n} for e, n in omitted.most_common(15)],
            "top_insertions": [{"phone": p, "count": n} for p, n in inserted.most_common(15)],
            "omissions_by_word_class": {
                "function_word": sum(1 for r in omitted_rows if r["function_word"]),
                "content_word": sum(1 for r in omitted_rows if not r["function_word"]),
                "function_word_phonemes": sum(1 for r in clean if r["function_word"]),
                "content_word_phonemes": sum(1 for r in clean if not r["function_word"]),
            },
            "uncertainty": {
                "confidence_matched": _stats(r["confidence"] for r in matched),
                "confidence_substituted": _stats(r["confidence"] for r in substituted),
                "nbest_margin_matched": _stats(r["nbest_margin"] for r in matched),
                "nbest_margin_substituted": _stats(r["nbest_margin"] for r in substituted),
                "ambiguous_decodes": sum(
                    1 for r in clean if r["nbest_margin"] is not None and r["nbest_margin"] < AMBIGUOUS_MARGIN and r["operation"] != "omission"
                ),
                "decoded_phonemes": len(matched) + len(substituted),
                "substituted_with_expected_plausible": sum(
                    1 for r in substituted if (r["expected_posterior"] or 0.0) >= PLAUSIBLE_POSTERIOR
                ),
                "omitted_with_expected_plausible": sum(
                    1 for r in omitted_rows if (r["expected_posterior"] or 0.0) >= PLAUSIBLE_POSTERIOR
                ),
                "expected_posterior_substituted": _stats(r["expected_posterior"] for r in substituted),
                "expected_posterior_omitted": _stats(r["expected_posterior"] for r in omitted_rows),
            },
            "lexical_stress": (
                {
                    "source": "espeak lexical stress (expected, not observed)",
                    "note": "acoustic correlates over CTC spans; spans are posterior peaks, not vowel durations",
                    "primary_stressed_vowels": {
                        "n": len(stressed),
                        "relative_energy": _stats(r["relative_energy"] for r in stressed),
                        "f0_hz": _stats(r["f0_hz"] for r in stressed),
                        "voiced_fraction": _fraction(r["voicing"] for r in stressed),
                    },
                    "unstressed_vowels": {
                        "n": len(unstressed),
                        "relative_energy": _stats(r["relative_energy"] for r in unstressed),
                        "f0_hz": _stats(r["f0_hz"] for r in unstressed),
                        "voiced_fraction": _fraction(r["voicing"] for r in unstressed),
                    },
                }
                if has_stress
                else {"status": "unavailable", "reason": "engine reports no expected or observed stress"}
            ),
            "prosody": {"status": "unavailable", "reason": "no runnable engine observes stress, intonation or rhythm"},
        }
    return out


def _fraction(values: Iterable[bool | None]) -> dict[str, Any]:
    vals = [v for v in values if v is not None]
    return {"n": len(vals), "fraction_true": (sum(vals) / len(vals)) if vals else None}


# ----------------------------------------------------------------------
# Same text, different styles
# ----------------------------------------------------------------------


def style_comparison(run: LoadedRun) -> dict[str, Any]:
    out: dict[str, Any] = {
        "rules": {
            "consistent_deviation_candidate": "same non-match (same observed phone or omission) in every style of the group",
            "connected_speech_reduction_candidate": (
                "decoded as expected in the slowest style, omitted or substituted only in faster "
                "style(s), on a function word, a word-final consonant or an unstressed vowel"
            ),
            "ambiguous": f"the deviating decode has N-best margin < {AMBIGUOUS_MARGIN} or the expected phone remains plausible (posterior >= {PLAUSIBLE_POSTERIOR})",
            "style_specific_deviation": "any other deviation present in some styles only",
            "alignment_suspect": f"the word or an adjacent word has >= {SUSPECT_INSERTIONS} inserted phones in that style; not interpreted",
        },
        "thresholds": THRESHOLDS,
        "groups": {},
    }
    for group, members in TEXT_GROUPS.items():
        g: dict[str, Any] = {"recordings": list(members), "engines": {}}
        for eid in run.engines:
            cells = {rid: run.evidence.get(cell_id(rid, eid)) for rid in members}
            if not all(cells.values()):
                g["engines"][eid] = {"status": "not evaluated", "cells": {
                    rid: (run.cells[cell_id(rid, eid)].status if cell_id(rid, eid) in run.cells else "missing") for rid in members
                }}
                continue

            styles = {rid: speed_rank(c.reading_style) for rid, c in cells.items()}
            per_style = {}
            for rid, c in cells.items():
                m = cell_measurements(c)
                per_style[rid] = {
                    "reading_style": c.reading_style,
                    "speed": styles[rid],
                    "audio_ms": c.result.recording.audio.duration_ms,
                    **{k: m[k] for k in (
                        "speech_span_ms", "n_heard", "heard_phones_per_s", "phones_per_s_excluding_pauses",
                        "pauses", "pause_ms_total", "match", "substitution", "omission", "insertion",
                        "mean_confidence_heard", "mean_expected_posterior", "alignment_suspect_words",
                    )},
                }

            ordered = sorted(
                (rid for rid in members if styles[rid] in SPEED_ORDER), key=lambda r: SPEED_ORDER[styles[r]]
            )
            rows = {rid: phoneme_rows(c) for rid, c in cells.items()}
            positions = []
            n = len(rows[members[0]])
            for i in range(n):
                by_rid = {rid: rows[rid][i] for rid in members}
                if all(r["operation"] == "match" for r in by_rid.values()):
                    continue
                label = _classify_position(by_rid, ordered)
                ref = by_rid[members[0]]
                positions.append({
                    "word": ref["word"],
                    "word_index": ref["word_index"],
                    "expected": ref["expected"],
                    "expected_stress": ref["expected_stress"],
                    "function_word": ref["function_word"],
                    "word_final": ref["position_in_word"] == ref["word_length"] - 1,
                    "label": label,
                    "by_recording": {
                        rid: {
                            "speed": styles[rid],
                            "observed": r["observed"],
                            "operation": r["operation"],
                            "expected_posterior": r["expected_posterior"],
                            "nbest_margin": r["nbest_margin"],
                            "alignment_suspect": r["alignment_suspect"],
                        }
                        for rid, r in by_rid.items()
                    },
                })
            heard = {rid: c.result.engine_evidence["recognition"]["phones"] for rid, c in cells.items()}
            consistency = {
                f"{a}~{b}": _normalized_edit_distance(heard[a], heard[b])
                for i, a in enumerate(members) for b in members[i + 1:]
            }
            g["engines"][eid] = {
                "per_recording": per_style,
                "speed_order": ordered,
                "deviating_positions": positions,
                "label_counts": dict(Counter(p["label"] for p in positions)),
                "heard_sequence_distance": consistency,
            }
        out["groups"][group] = g
    return out


def _classify_position(by_rid: dict[str, dict[str, Any]], ordered: list[str]) -> str:
    rows = list(by_rid.values())
    if any(r["alignment_suspect"] for r in rows):
        return "alignment_suspect"
    deviating = [r for r in rows if r["operation"] != "match"]
    if len(deviating) == len(rows) and len({(r["operation"], r["observed"]) for r in rows}) == 1:
        return "consistent_deviation_candidate"

    ref = rows[0]
    reducible = ref["function_word"] or (
        ref["position_in_word"] == ref["word_length"] - 1 and not is_vowel(ref["expected"])
    ) or (
        # Unstressed vowels only where the engine reports stress; for an engine
        # without stress information, every vowel's stress is unknown.
        is_vowel(ref["expected"]) and ref.get("stress_known", False) and ref["expected_stress"] is None
    )
    if ordered:
        slowest = by_rid[ordered[0]]
        faster_dev = [by_rid[r] for r in ordered[1:] if by_rid[r]["operation"] != "match"]
        # The slowest available reading is the reference: it must be decoded
        # as expected for a faster-style deviation to count as a reduction.
        slow_ok = slowest["operation"] == "match"
        if slow_ok and faster_dev and reducible:
            if any(_ambiguous(r) for r in faster_dev):
                return "ambiguous"
            return "connected_speech_reduction_candidate"

    if any(_ambiguous(r) for r in deviating):
        return "ambiguous"
    return "style_specific_deviation"


def _ambiguous(r: dict[str, Any]) -> bool:
    if r["operation"] == "omission":
        return (r["expected_posterior"] or 0.0) >= PLAUSIBLE_POSTERIOR
    return (r["nbest_margin"] is not None and r["nbest_margin"] < AMBIGUOUS_MARGIN) or (
        (r["expected_posterior"] or 0.0) >= PLAUSIBLE_POSTERIOR
    )


def _normalized_edit_distance(a: list[str], b: list[str]) -> float:
    if not a and not b:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1] / max(len(a), len(b))


# ----------------------------------------------------------------------
# Cross-engine and word diagnosis
# ----------------------------------------------------------------------


def cross_engine(run: LoadedRun, a: str = "openpronounce", b: str = "wav2vec2_raw") -> dict[str, Any]:
    out: dict[str, Any] = {
        "engines": [a, b],
        "shared_evidence": [
            "same checkpoint (facebook/wav2vec2-lv-60-espeak-cv-ft) and frame posteriors",
            "espeak as expected-phone source",
            "acoustic measurements over identical spans",
        ],
        "independent_evidence": [
            "decoding normalization (OpenPronounce merges phones and collapses repeats; raw does not)",
            "expected-phone inventory and lexical stress",
            "alignment and the substitution/omission decisions that follow",
            "OpenPronounce's own word flags (phonetically weighted)",
        ],
        "recordings": {},
    }
    for rid in run.recordings:
        ca, cb = run.evidence.get(cell_id(rid, a)), run.evidence.get(cell_id(rid, b))
        if not ca or not cb:
            out["recordings"][rid] = {"status": "not comparable"}
            continue
        sa = {tuple(s) for s in ca.result.engine_evidence["recognition"]["frame_spans"]}
        sb = {tuple(s) for s in cb.result.engine_evidence["recognition"]["frame_spans"]}
        word_diffs = []
        for wa, wb in zip(ca.result.words, cb.result.words):
            ops_a = [(p.expected.phoneme, p.observed.top, p.engine_evidence["operation"]) for p in wa.phonemes]
            ops_b = [(p.expected.phoneme, p.observed.top, p.engine_evidence["operation"]) for p in wb.phonemes]
            timing_same = (wa.timing.start_ms, wa.timing.end_ms) == (wb.timing.start_ms, wb.timing.end_ms)
            a_dev = sum(o[2] != "match" for o in ops_a)
            b_dev = sum(o[2] != "match" for o in ops_b)
            if a_dev != b_dev or not timing_same:
                word_diffs.append({
                    "word": wa.word,
                    "timing_identical": timing_same,
                    a: {"ops": ops_a, "start_ms": wa.timing.start_ms, "end_ms": wa.timing.end_ms,
                        "flagged_by_provider": wa.engine_evidence.get("flagged_by_provider")},
                    b: {"ops": ops_b, "start_ms": wb.timing.start_ms, "end_ms": wb.timing.end_ms},
                })
        out["recordings"][rid] = {
            "heard_counts": {a: len(sa), b: len(sb)},
            "spans_only_in": {a: sorted(sa - sb), b: sorted(sb - sa)},
            "spans_shared": len(sa & sb),
            "words_disagreeing": word_diffs,
            "provider_flagged_words": ca.result.engine_evidence.get("comparison", {}).get("words_with_errors"),
        }
    return out


def word_diagnosis(run: LoadedRun) -> dict[str, Any]:
    out: dict[str, Any] = {
        "note": (
            "Per word occurrence: each engine's operations. 'flagged_by_provider' is "
            "OpenPronounce's own phonetically weighted judgement; raw counts are unit-cost "
            "derived counts. Neither is ground truth."
        ),
        "recordings": {},
    }
    for rid in run.recordings:
        words: dict[int, dict[str, Any]] = {}
        for eid in run.engines:
            cell = run.evidence.get(cell_id(rid, eid))
            if not cell:
                continue
            suspects = alignment_suspect_words(cell.result.words)
            for wi, w in enumerate(cell.result.words):
                entry = words.setdefault(wi, {"word": w.word, "engines": {}})
                ops = Counter(p.engine_evidence["operation"] for p in w.phonemes)
                entry["engines"][eid] = {
                    "deviations": len(w.phonemes) - ops.get("match", 0),
                    "operations": dict(ops),
                    "insertions": sum(len(p.engine_evidence.get("extra_heard_phones", [])) for p in w.phonemes),
                    "alignment_suspect": wi in suspects,
                    "flagged_by_provider": w.engine_evidence.get("flagged_by_provider"),
                    "timing_ms": [w.timing.start_ms, w.timing.end_ms],
                    "timing_source": w.timing.source,
                }
        out["recordings"][rid] = [words[i] for i in sorted(words)]
    return out


# ----------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------


def write_analysis(run_dir: Path, recordings: list[Recording], engines: list[str]) -> list[Path]:
    run = load_run(run_dir, recordings, engines)
    summaries = Path(run_dir) / "summaries"
    outputs = {
        "matrix.json": matrix(run) | {"excluded_from_analysis": run.excluded},
        "performance.json": performance(run),
        "task_analysis.json": task_analysis(run),
        "phenomena.json": phenomena(run),
        "style_comparison.json": style_comparison(run),
        "cross_engine.json": cross_engine(run) if {"openpronounce", "wav2vec2_raw"} <= set(engines) else {},
        "word_diagnosis.json": word_diagnosis(run),
    }
    written = []
    for name, payload in outputs.items():
        path = summaries / name
        atomic_write_json(path, payload)
        written.append(path)
    return written
