"""Application service: everything the UI can do, independent of HTTP.

* Engine availability comes from the M2 `classify_engine`, i.e. from each
  engine's own readiness. Blocked and unresolved engines are listed with their
  reason and can never be selected.
* Analyses run through the M1 `safe_analyze`, one at a time (the models are
  shared), and are kept in a per-session temporary workspace that is removed
  on shutdown. Only the most recent `max_analyses` are retained.
* Listening notes — what the *user* hears when replaying a sound — are the
  only persistent state. They are appended to a local JSONL file that holds
  references (hashes, times, phones), never audio.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pronunciation_lab.app.audio_input import (
    AudioInputError,
    PreparedAudio,
    ffmpeg_available,
    prepare_audio,
)
from pronunciation_lab.app.engine_compare import compare, validate_comparison
from pronunciation_lab.app.pipeline import analyze_pipeline
from pronunciation_lab.benchmark.base import PronunciationEngine
from pronunciation_lab.benchmark.engines import ENGINES, create_engine
from pronunciation_lab.benchmark.runner import classify_engine

DEFAULT_ENGINE = "wav2vec2_raw"
MAX_TEXT_CHARS = 300
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
WORKSPACE_PREFIX = "pronunciation-lab-"
_WORKSPACE_RE = re.compile(rf"^{WORKSPACE_PREFIX}(\d+)-")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def remove_stale_workspaces(tmp_root: Path | None = None) -> list[Path]:
    """Delete session workspaces left behind by app processes that no longer exist.

    A crash or a forced kill skips `close()`, which would otherwise leave copies
    of the user's recordings in the temp directory indefinitely.
    """
    root = Path(tmp_root or tempfile.gettempdir())
    removed = []
    for path in root.glob(f"{WORKSPACE_PREFIX}*"):
        match = _WORKSPACE_RE.match(path.name)
        if path.is_dir() and match and not _pid_alive(int(match.group(1))):
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path)
    return removed
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)

ENGINE_LABELS = {
    "wav2vec2_raw": "Wav2Vec2 phoneme recogniser (raw)",
    "openpronounce": "OpenPronounce",
    "wavlm": "WavLM",
    "azure_pronunciation": "Azure Pronunciation Assessment",
    "speechsuper": "SpeechSuper",
    "speechace": "Speechace",
}
ENGINE_NOTES = {
    "wav2vec2_raw": "Keeps every sound the model outputs, including vowel length (e.g. /iː/ vs /ɪ/).",
    "openpronounce": (
        "Same acoustic model as the raw recogniser, with OpenPronounce's normalisation: vowel "
        "length is ignored and repeated sounds across words are merged. Its agreement with the "
        "raw recogniser is not a second opinion."
    ),
}
NOTE_VERDICTS = ("as_expected", "as_heard", "something_else", "cannot_tell")


class UserError(Exception):
    """A request the user can fix; `code` is machine-readable."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class Analysis:
    id: str
    created_at: str
    engine_id: str
    target_text: str
    workdir: Path
    audio: PreparedAudio
    view: dict[str, Any]
    result_json: str
    source_label: str
    sounds: dict[int, dict[str, Any]] = field(default_factory=dict)
    # M5: engine id -> id of the analysis of the same audio with that engine
    linked: dict[str, str] = field(default_factory=dict)


def validate_text(text: str | None) -> str:
    text = (text or "").strip()
    if not text:
        raise UserError("text_empty", "Enter the sentence you read aloud.")
    if len(text) > MAX_TEXT_CHARS:
        raise UserError("text_too_long", f"The text is longer than {MAX_TEXT_CHARS} characters.")
    if not _LETTER_RE.search(text):
        raise UserError("text_no_words", "The text contains no words.")
    return text


class AnalysisService:
    def __init__(
        self,
        *,
        data_dir: Path | None = None,
        notes_path: Path | None = None,
        workspace: Path | None = None,
        engine_factory: Callable[[str], PronunciationEngine] = create_engine,
        engine_ids: list[str] | None = None,
        max_analyses: int = 20,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else None
        self.notes_path = Path(notes_path) if notes_path else None
        self._own_workspace = workspace is None
        if self._own_workspace:
            self.stale_workspaces_removed = remove_stale_workspaces()
        self.workspace = Path(workspace) if workspace else Path(
            tempfile.mkdtemp(prefix=f"{WORKSPACE_PREFIX}{os.getpid()}-")
        )
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.max_analyses = max_analyses
        self._factory = engine_factory
        self._lock = threading.Lock()
        self._analyses: OrderedDict[str, Analysis] = OrderedDict()
        self._warm: set[str] = set()

        self.engines: dict[str, dict[str, Any]] = {}
        self._instances: dict[str, PronunciationEngine] = {}
        for eid in engine_ids or list(ENGINES):
            availability = classify_engine(eid, engine_factory)
            self.engines[eid] = {
                "id": eid,
                "label": ENGINE_LABELS.get(eid, eid),
                "state": availability.state,
                "reason": availability.reason,
                "note": ENGINE_NOTES.get(eid),
                "missing_env": (availability.detail or {}).get("missing_env"),
            }
            if availability.state == "runnable":
                self._instances[eid] = availability.engine

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def default_engine(self) -> str | None:
        if DEFAULT_ENGINE in self._instances:
            return DEFAULT_ENGINE
        return next(iter(self._instances), None)

    def status(self) -> dict[str, Any]:
        return {
            "engines": [self.engines[e] | {"ready": e in self._warm} for e in self.engines],
            "default_engine": self.default_engine(),
            "ffmpeg": ffmpeg_available(),
            "benchmark_recordings": self.benchmark_recordings(),
            "notes_enabled": self.notes_path is not None,
            "limits": {"max_text_chars": MAX_TEXT_CHARS},
        }

    def warmup(self, engine_id: str | None = None) -> None:
        """Load a model ahead of the first analysis (safe to call from a thread)."""
        eid = engine_id or self.default_engine()
        engine = self._instances.get(eid)
        if engine is None or eid in self._warm:
            return
        with self._lock:
            warm = getattr(engine, "warmup", None)
            if callable(warm):
                try:
                    warm()
                except Exception:  # noqa: BLE001 - surfaced by the first analysis instead
                    return
            self._warm.add(eid)

    # ------------------------------------------------------------------
    # Benchmark recordings (optional, local only)
    # ------------------------------------------------------------------

    def benchmark_recordings(self) -> list[dict[str, str]]:
        if not self.data_dir:
            return []
        manifest = self.data_dir / "benchmark_manifest.csv"
        if not manifest.is_file():
            return []
        try:
            with manifest.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except (OSError, csv.Error, UnicodeDecodeError):
            return []
        out = []
        for row in rows:
            rid = (row.get("recording_id") or "").strip()
            if rid and (self.data_dir / "benchmark_wav" / f"{rid}.wav").is_file():
                out.append({
                    "id": rid,
                    "text": row.get("target_text", ""),
                    "style": row.get("reading_style", ""),
                    "purpose": row.get("purpose", ""),
                })
        return out

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def _engine(self, engine_id: str | None) -> tuple[str, PronunciationEngine]:
        eid = engine_id or self.default_engine()
        if eid not in self.engines:
            raise UserError("engine_unknown", f"Unknown engine {eid!r}.")
        if eid not in self._instances:
            info = self.engines[eid]
            raise UserError(
                "engine_unavailable",
                f"{info['label']} is {info['state']} ({info['reason']}) and cannot be used.",
                status=409,
            )
        return eid, self._instances[eid]

    def analyze_upload(self, data: bytes, filename: str, text: str | None, engine_id: str | None) -> Analysis:
        text = validate_text(text)
        eid, engine = self._engine(engine_id)
        return self._run(data, filename, text, eid, engine, source_label=f"upload: {Path(filename or 'upload').name}")

    def analyze_stateless(self, data: bytes, filename: str, text: str | None) -> dict[str, Any]:
        """Analyze one upload with both local engines without retaining request data."""
        text = validate_text(text)
        request_id = uuid.uuid4().hex
        analyses: dict[str, Any] = {}

        with tempfile.TemporaryDirectory(prefix="pronunciation-lab-request-") as temporary:
            try:
                audio = prepare_audio(data, filename, Path(temporary))
            except AudioInputError as exc:
                status = 413 if exc.code == "audio_too_large" else 400
                raise UserError(exc.code, exc.message, status) from exc

            for engine_id in ("openpronounce", "wav2vec2_raw"):
                engine = self._instances.get(engine_id)
                if engine is None:
                    analyses[engine_id] = {
                        "state": "unavailable",
                        "error": {
                            "code": "engine_unavailable",
                            "message": "This analysis engine is not available.",
                        },
                    }
                    continue

                try:
                    result, evidence = analyze_pipeline(
                        engine,
                        audio.analysis_path,
                        text,
                        recording_id=f"request-{request_id[:8]}-{engine_id}",
                        lock=self._lock,
                        original_path=audio.original_path,
                    )
                    result_data = result.model_dump(mode="json")
                    result_data["recording"]["audio"].pop("original_path", None)
                    result_data["recording"]["audio"].pop("analysis_path", None)
                    if result.status in ("failed", "blocked"):
                        for error in result_data.get("errors", []):
                            error["message"] = "The engine could not complete this analysis."
                        if evidence.get("error"):
                            evidence["error"]["detail"] = "The engine could not complete this analysis."
                    analyses[engine_id] = {
                        "state": result.status,
                        "result": result_data,
                        "evidence": evidence,
                    }
                except Exception:  # noqa: BLE001 - each engine is an independent analysis
                    analyses[engine_id] = {
                        "state": "failed",
                        "error": {
                            "code": "analysis_failed",
                            "message": "This engine could not complete the analysis.",
                        },
                    }

            return {
                "request_id": request_id,
                "target_text": text,
                "duration_ms": audio.duration_ms,
                "analyses": analyses,
            }

    def analyze_benchmark(self, recording_id: str, engine_id: str | None, text: str | None = None) -> Analysis:
        match = next((r for r in self.benchmark_recordings() if r["id"] == recording_id), None)
        if match is None:
            raise UserError("recording_unknown", f"Benchmark recording {recording_id!r} is not available.", 404)
        text = validate_text(text if text is not None else match["text"])
        eid, engine = self._engine(engine_id)
        data = (self.data_dir / "benchmark_wav" / f"{recording_id}.wav").read_bytes()
        return self._run(data, f"{recording_id}.wav", text, eid, engine,
                         source_label=f"benchmark {recording_id} ({match['style']})")

    def _run(self, data, filename, text, eid, engine, *, source_label) -> Analysis:
        aid = uuid.uuid4().hex
        workdir = self.workspace / aid
        try:
            audio = prepare_audio(data, filename, workdir)
        except AudioInputError as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            raise UserError(exc.code, exc.message) from exc

        result, view = analyze_pipeline(engine, audio.analysis_path, text, recording_id=f"app-{aid[:8]}",
                                        lock=self._lock, original_path=audio.original_path,
                                        on_inference_done=lambda: self._warm.add(eid))
        view |= {
            "analysis_id": aid,
            "source": source_label,
            "audio": {
                "duration_ms": audio.duration_ms,
                "conversion": audio.conversion,
                "original_name": audio.original_name,
                "url": f"/api/analyses/{aid}/audio",
            },
        }
        analysis = Analysis(
            id=aid,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            engine_id=eid,
            target_text=text,
            workdir=workdir,
            audio=audio,
            view=view,
            result_json=result.model_dump_json(),
            source_label=source_label,
            sounds={s["index"]: s | {"word": w["word"]} for w in view.get("words", []) for s in w["sounds"]},
        )
        (workdir / "result.json").write_text(analysis.result_json, encoding="utf-8")
        self._remember(analysis)
        return analysis

    # ------------------------------------------------------------------
    # M5: cross-engine comparison (on demand)
    # ------------------------------------------------------------------

    COMPARABLE_ENGINES = ("wav2vec2_raw", "openpronounce")

    def compare_engines(self, analysis_id: str, engine_id: str | None = None) -> dict[str, Any]:
        """Analyse the same audio and text with the other local engine and compare reduction evidence.

        Real inference with the second engine (timed); never reuses or edits the
        first engine's evidence. The pair is kept so repeated requests do not re-run.
        """
        analysis = self.get(analysis_id)
        if analysis.engine_id not in self.COMPARABLE_ENGINES:
            raise UserError("compare_unsupported", "Engine comparison needs a local engine analysis.")
        other_id = engine_id or next(e for e in self.COMPARABLE_ENGINES if e != analysis.engine_id)
        if other_id == analysis.engine_id or other_id not in self.COMPARABLE_ENGINES:
            raise UserError("compare_unsupported", "Choose the other local engine to compare with.")
        if analysis.view.get("reduction", {}).get("state") != "ok":
            raise UserError("compare_unsupported", "This analysis has no evidence to compare.")
        linked = self._analyses.get(analysis.linked.get(other_id, ""))
        if linked is None:
            eid, engine = self._engine(other_id)
            data = analysis.audio.original_path.read_bytes()
            linked = self._run(data, analysis.audio.original_name, analysis.target_text, eid, engine,
                               source_label=f"{analysis.source_label} — compared with {eid}")
            analysis.linked[eid] = linked.id
            linked.linked[analysis.engine_id] = analysis.id
        if linked.view.get("reduction", {}).get("state") != "ok":
            raise UserError("compare_unsupported", f"{other_id} produced no evidence for this recording.", 409)

        pair = sorted((analysis, linked), key=lambda a: self.COMPARABLE_ENGINES.index(a.engine_id))
        sides = [{"engine": a.engine_id, "observations": a.view["coach"]["observations"],
                  "reduction": a.view["reduction"], "text": a.target_text,
                  "duration_ms": a.view["duration_ms"]} for a in pair]
        result = compare(*sides)
        issues = validate_comparison(result, *sides)
        result |= {
            "analysis_ids": {a.engine_id: a.id for a in pair},
            "reductions": {a.engine_id: a.view["reduction"] for a in pair},
            "processing": {a.engine_id: a.view["processing"] for a in pair},
            "integrity": {"ok": not issues, "issues": issues},
        }
        return result

    def _remember(self, analysis: Analysis) -> None:
        self._analyses[analysis.id] = analysis
        while len(self._analyses) > self.max_analyses:
            _, old = self._analyses.popitem(last=False)
            shutil.rmtree(old.workdir, ignore_errors=True)

    def get(self, analysis_id: str) -> Analysis:
        if not _ID_RE.match(analysis_id or "") or analysis_id not in self._analyses:
            raise UserError("analysis_unknown", "This analysis is no longer available.", 404)
        return self._analyses[analysis_id]

    def recent(self) -> list[dict[str, Any]]:
        return [
            {"analysis_id": a.id, "created_at": a.created_at, "text": a.target_text,
             "engine": a.engine_id, "source": a.source_label, "state": a.view.get("state")}
            for a in reversed(self._analyses.values())
        ]

    # ------------------------------------------------------------------
    # Listening notes
    # ------------------------------------------------------------------

    def add_note(self, analysis_id: str, sound_index: Any, verdict: str, comment: str | None = None) -> dict[str, Any]:
        if self.notes_path is None:
            raise UserError("notes_disabled", "Listening notes are disabled.", 409)
        analysis = self.get(analysis_id)
        if not isinstance(sound_index, int) or isinstance(sound_index, bool) or sound_index not in analysis.sounds:
            raise UserError("sound_unknown", "That sound is not part of this analysis.")
        if verdict not in NOTE_VERDICTS:
            raise UserError("verdict_invalid", f"Verdict must be one of {', '.join(NOTE_VERDICTS)}.")
        comment = (comment or "").strip()[:500] or None
        sound = analysis.sounds[sound_index]
        note = {
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "analysis_id": analysis.id,
            "audio_sha256": analysis.audio.original_sha256,
            "source": analysis.source_label,
            "target_text": analysis.target_text,
            "engine": analysis.engine_id,
            "model": analysis.view["engine"]["model"],
            "word": sound["word"],
            "sound_index": sound_index,
            "expected": sound["expected"],
            "heard_by_engine": sound["heard"],
            "engine_category": sound["category"],
            "span_ms": sound["span_ms"],
            "verdict": verdict,
            "comment": comment,
        }
        self.notes_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(note, ensure_ascii=False) + "\n"
        with self._lock, self.notes_path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
        return note

    def notes_for(self, analysis_id: str) -> list[dict[str, Any]]:
        self.get(analysis_id)
        if self.notes_path is None or not self.notes_path.exists():
            return []
        out = []
        for line in self.notes_path.read_text(encoding="utf-8").splitlines():
            try:
                note = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn last line from a crash is skipped, never fatal
            if note.get("analysis_id") == analysis_id:
                out.append(note)
        return out

    # ------------------------------------------------------------------

    def close(self) -> None:
        self._analyses.clear()
        if self._own_workspace:
            shutil.rmtree(self.workspace, ignore_errors=True)
