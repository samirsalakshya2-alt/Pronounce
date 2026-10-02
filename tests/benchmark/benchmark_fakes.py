"""Synthetic datasets and controllable engines for benchmark-runner tests.

The fakes produce schema-valid `PronunciationResult`s that satisfy every cell
invariant, so a test that breaks one invariant breaks only that one.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from pronunciation_lab.benchmark.base import ErrorType, PronunciationEngine
from pronunciation_lab.benchmark.dataset import TEXT_GROUPS
from pronunciation_lab.benchmark.schema import (
    AcousticEvidence,
    EngineInfo,
    ExpectedPhoneme,
    NBestCandidate,
    ObservedPhoneme,
    PhonemeResult,
    ProcessingError,
    ProcessingInfo,
    PronunciationResult,
    ProsodyEvidence,
    RecordingAudio,
    RecordingInfo,
    TargetInfo,
    TimingInfo,
    WordResult,
)

GROUP_TEXT = {
    "think_three": "Think about three things.",
    "very_few_people": "Very few people.",
    "ship_will_leave": "The ship will leave.",
    "world_has_changed": "The world has changed.",
    "i_would_like": "I would like that.",
    "company_planning": "The company is planning.",
    "although_initial": "Although the results were good.",
}
STYLES = ["Slow, clear", "Normal", "Fast", "Normal"]


def make_dataset(
    root: Path,
    ids: list[str] | None = None,
    *,
    seconds: float = 0.5,
    sources: bool = True,
) -> Path:
    """A data dir shaped like the real one: manifest, 16 kHz mono PCM_16 WAVs, sources."""
    data = Path(root) / "data"
    (data / "benchmark_wav").mkdir(parents=True)
    ids = ids or [f"R{i:02d}" for i in range(1, 21)]
    rng = np.random.default_rng(0)

    rows = []
    for rid in ids:
        group = next(g for g, members in TEXT_GROUPS.items() if rid in members)
        style = STYLES[TEXT_GROUPS[group].index(rid) % len(STYLES)]
        filename = f"Source {rid}.m4a"
        rows.append({
            "recording_id": rid,
            "filename": filename,
            "target_text": GROUP_TEXT[group],
            "reading_style": style,
            "purpose": f"synthetic {group}",
        })
        audio = (rng.uniform(-0.2, 0.2, int(16_000 * seconds))).astype(np.float32)
        sf.write(data / "benchmark_wav" / f"{rid}.wav", audio, 16_000, subtype="PCM_16")
        if sources:
            (data / filename).write_bytes(b"not decoded by tests")

    write_manifest(data / "benchmark_manifest.csv", rows)
    return data


def write_manifest(path: Path, rows: list[dict[str, str]], fieldnames=None) -> None:
    fieldnames = fieldnames or ["recording_id", "filename", "target_text", "reading_style", "purpose"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def make_result(
    *,
    engine: str,
    recording_id: str,
    text: str,
    audio_path: Path,
    cold: bool,
    status: str = "ok",
    observed: str = "a",
    started: float | None = None,
) -> PronunciationResult:
    duration = sf.info(str(audio_path)).duration * 1000.0
    # Honest timings: the engine may not claim more time than really elapsed,
    # which the runner checks against its own clock.
    wall = (time.perf_counter() - started) * 1000.0 if started is not None else 0.0
    phoneme = PhonemeResult(
        position=0,
        expected=ExpectedPhoneme(phoneme="a", position=0, source="fake"),
        observed=ObservedPhoneme(
            top=observed,
            confidence=0.9,
            nbest=[NBestCandidate(phoneme=observed, probability=0.9), NBestCandidate(phoneme="b", probability=0.05)],
            frame_start=0,
            frame_end=1,
        ),
        timing=TimingInfo(start_ms=0.0, end_ms=20.0, duration_ms=20.0, source="engine", frame_start=0, frame_end=1),
        acoustic=AcousticEvidence(measured_on="analysis", energy_db=-20.0, relative_energy=1.0),
        prosody=ProsodyEvidence(),
        engine_evidence={
            "operation": "match" if observed == "a" else "substitution",
            "expected_phone_posterior": 0.9,
            "extra_heard_phones": [],
        },
    )
    return PronunciationResult(
        status=status,
        recording=RecordingInfo(
            id=recording_id,
            audio=RecordingAudio(
                original_path=str(audio_path), analysis_path=str(audio_path),
                sample_rate_hz=16_000, channels=1, duration_ms=duration,
            ),
            target=TargetInfo(text=text),
        ),
        engine=EngineInfo(name=engine, version="1.0", mode="local", model="fake/model@1"),
        processing=ProcessingInfo(
            wall_time_ms=wall,
            model_load_ms=wall * 0.1 if cold else None,
            inference_ms=wall * 0.5,
            postprocessing_ms=wall * 0.2,
            realtime_factor=wall / duration,
            run_type="cold" if cold else "warm",
            device="cpu",
        ),
        phone_set="fake",
        words=[WordResult(
            word=text.split()[0].lower().strip("."),
            expected_phonemes=["a"],
            timing=TimingInfo(start_ms=0.0, end_ms=20.0, duration_ms=20.0, source="engine"),
            phonemes=[phoneme],
            engine_evidence={"provider": engine},
        )],
        engine_evidence={"provider": engine, "recognition": {"phones": [observed]}},
        errors=[ProcessingError(type="partial_result", message="fake partial")] if status == "partial" else [],
    )


class FakeEngine(PronunciationEngine):
    """Records every call; behaviour per recording is controlled by `plan`."""

    mode = "local"
    version = "1.0"
    model_id = "fake/model@1"

    def __init__(self, name: str = "fake", plan: dict[str, object] | None = None, calls: list | None = None):
        self.name = name
        self.plan = plan or {}
        self.calls = calls if calls is not None else []
        self._cold = True

    def analyze(self, audio_path, expected_text, *, recording_id, original_path=None):
        started = time.perf_counter()
        self.calls.append(recording_id)
        action = self.plan.get(recording_id, "ok")
        if callable(action):
            action = action(self, recording_id)
        if isinstance(action, BaseException):
            raise action
        if action == "malformed":
            return {"not": "a result"}
        cold, self._cold = self._cold, False
        if action == "wrong_metadata":
            return make_result(engine=self.name, recording_id="R99", text=expected_text, audio_path=audio_path, cold=cold, started=started)
        if action == "no_timing":
            result = make_result(engine=self.name, recording_id=recording_id, text=expected_text, audio_path=audio_path, cold=cold, started=started)
            result.processing.wall_time_ms = None
            return result
        if action == "random":
            observed = "a" if time.perf_counter_ns() % 2 else "c"
            return make_result(engine=self.name, recording_id=recording_id, text=expected_text,
                               audio_path=audio_path, cold=cold, observed=observed, started=started)
        return make_result(engine=self.name, recording_id=recording_id, text=expected_text,
                           audio_path=audio_path, cold=cold, status=action if action in ("ok", "partial") else "ok", started=started)


class FakeUnavailableEngine(PronunciationEngine):
    """An engine that declares itself blocked or unresolved and must never run."""

    mode = "cloud"
    version = None
    model_id = None

    def __init__(self, name: str, state: str):
        self.name = name
        self.state = state
        self.calls: list[str] = []

    def readiness(self):
        return {
            "engine": self.name,
            "state": self.state,
            "reason": ErrorType.UNRESOLVED_ENGINE if self.state == "unresolved" else ErrorType.CREDENTIALS_UNAVAILABLE,
        }

    def analyze(self, *args, **kwargs):  # pragma: no cover - must not be reached
        self.calls.append(kwargs.get("recording_id"))
        raise AssertionError(f"{self.name} is {self.state} and must not be executed")


def factory(engines: dict[str, PronunciationEngine]):
    def create(name: str) -> PronunciationEngine:
        if name not in engines:
            raise KeyError(name)
        return engines[name]
    return create
