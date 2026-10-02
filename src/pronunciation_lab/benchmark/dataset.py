"""The benchmark dataset: manifest loading and preflight validation.

The manifest (`data/benchmark_manifest.csv`) is the source of truth for target
text. Nothing here rewrites it or the audio; preflight only reads, and records
content hashes so a run can later prove that neither changed underneath it.

Preflight collects every problem before reporting, rather than stopping at the
first, so one pass shows everything that is wrong with the dataset.
"""

from __future__ import annotations

import csv
import hashlib
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import soundfile as sf

REQUIRED_COLUMNS = ("recording_id", "filename", "target_text", "reading_style", "purpose")
EXPECTED_IDS = tuple(f"R{i:02d}" for i in range(1, 21))

# Recordings that must share one target text (the same sentence read in
# different styles). Different groups must have different texts.
TEXT_GROUPS: dict[str, tuple[str, ...]] = {
    "think_three": ("R01", "R02", "R03", "R04"),
    "very_few_people": ("R05", "R06", "R07"),
    "ship_will_leave": ("R08", "R09", "R10"),
    "world_has_changed": ("R11", "R12", "R13"),
    "i_would_like": ("R14", "R15", "R16"),
    "company_planning": ("R17", "R18"),
    "although_initial": ("R19", "R20"),
}

ANALYSIS_SAMPLE_RATE = 16_000
ANALYSIS_CHANNELS = 1
SUPPORTED_SUBTYPES = ("PCM_16",)
# Prepared WAV vs source M4A duration tolerance (AAC priming/padding).
SOURCE_DURATION_TOLERANCE_S = 0.1


@dataclass(frozen=True)
class Recording:
    recording_id: str
    filename: str
    target_text: str
    reading_style: str
    purpose: str
    wav_path: Path
    source_path: Path

    @property
    def text_group(self) -> str | None:
        for name, ids in TEXT_GROUPS.items():
            if self.recording_id in ids:
                return name
        return None


@dataclass
class Problem:
    code: str
    message: str
    recording_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"code": self.code, "message": self.message, "recording_id": self.recording_id}


@dataclass
class PreflightReport:
    manifest_path: Path
    data_dir: Path
    recordings: list[Recording] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)
    # SHA-256 of every source file, keyed by path relative to `data_dir`.
    hashes: dict[str, str] = field(default_factory=dict)
    audio: dict[str, dict[str, object]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "manifest": str(self.manifest_path),
            "data_dir": str(self.data_dir),
            "n_recordings": len(self.recordings),
            "problems": [p.as_dict() for p in self.problems],
            "warnings": [w.as_dict() for w in self.warnings],
            "audio": self.audio,
            "sha256": self.hashes,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ffprobe_duration(path: Path) -> float | None:
    if shutil.which("ffprobe") is None:
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        return float(out.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return None


def preflight(
    data_dir: Path,
    *,
    manifest_name: str = "benchmark_manifest.csv",
    wav_dir_name: str = "benchmark_wav",
    expected_ids: tuple[str, ...] = EXPECTED_IDS,
    text_groups: dict[str, tuple[str, ...]] = TEXT_GROUPS,
    check_sources: bool = True,
) -> PreflightReport:
    """Validate the manifest and every recording, collecting all problems."""
    data_dir = Path(data_dir)
    manifest = data_dir / manifest_name
    wav_dir = data_dir / wav_dir_name
    report = PreflightReport(manifest_path=manifest, data_dir=data_dir)

    def problem(code: str, message: str, rid: str | None = None) -> None:
        report.problems.append(Problem(code, message, rid))

    # --- manifest ---------------------------------------------------------
    if not manifest.is_file():
        problem("manifest_missing", f"{manifest} does not exist")
        return report

    try:
        with manifest.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            columns = tuple(reader.fieldnames or ())
            rows = list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        problem("manifest_unreadable", f"{type(exc).__name__}: {exc}")
        return report

    missing_columns = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing_columns:
        problem("manifest_columns_missing", f"missing columns {missing_columns}; found {list(columns)}")
        return report

    report.hashes[manifest_name] = sha256_file(manifest)

    seen: dict[str, int] = {}
    for line, row in enumerate(rows, start=2):
        if None in row or any(v is None for v in row.values()):
            problem("manifest_row_malformed", f"line {line}: wrong number of fields")
            continue

        rid = (row["recording_id"] or "").strip()
        if not rid:
            problem("recording_id_missing", f"line {line}: empty recording_id")
            continue
        if rid in seen:
            problem("recording_id_duplicate", f"{rid} on lines {seen[rid]} and {line}", rid)
            continue
        seen[rid] = line

        text = row["target_text"]
        if not text or not text.strip():
            problem("target_text_empty", f"{rid}: target_text is empty", rid)
        elif text != text.strip():
            problem("target_text_whitespace", f"{rid}: target_text has leading/trailing whitespace", rid)

        for column in ("filename", "reading_style", "purpose"):
            if not (row[column] or "").strip():
                problem(f"{column}_empty", f"{rid}: {column} is empty", rid)

        report.recordings.append(
            Recording(
                recording_id=rid,
                filename=row["filename"],
                target_text=text,
                reading_style=row["reading_style"],
                purpose=row["purpose"],
                wav_path=wav_dir / f"{rid}.wav",
                source_path=data_dir / row["filename"],
            )
        )

    ids = [r.recording_id for r in report.recordings]
    for rid in expected_ids:
        if rid not in seen:
            problem("recording_missing_from_manifest", f"{rid} is not in the manifest", rid)
    for rid in ids:
        if rid not in expected_ids:
            problem("recording_unexpected", f"{rid} is not an expected benchmark recording", rid)

    # --- same-text groups ------------------------------------------------
    by_id = {r.recording_id: r for r in report.recordings}
    group_text: dict[str, str] = {}
    for name, members in text_groups.items():
        texts = {by_id[m].target_text for m in members if m in by_id}
        if len(texts) > 1:
            problem("text_group_inconsistent", f"group {name} ({', '.join(members)}) has {len(texts)} different texts")
        elif texts:
            group_text[name] = texts.pop()
    if len(set(group_text.values())) != len(group_text):
        problem("text_groups_overlap", "two different text groups share the same target text")

    # --- audio --------------------------------------------------------------
    if wav_dir.is_dir():
        expected_files = {f"{rid}.wav" for rid in expected_ids}
        for extra in sorted(p.name for p in wav_dir.glob("*.wav") if p.name not in expected_files):
            report.warnings.append(Problem("wav_unexpected", f"unexpected file {extra} in {wav_dir}"))
    else:
        problem("wav_dir_missing", f"{wav_dir} does not exist")

    for rec in report.recordings:
        rid = rec.recording_id
        wav = rec.wav_path

        if not wav.is_file():
            problem("wav_missing", f"{wav} does not exist", rid)
        elif wav.stat().st_size == 0:
            problem("wav_empty_file", f"{wav} is zero bytes", rid)
        else:
            try:
                info = sf.info(str(wav))
            except Exception as exc:  # noqa: BLE001 - libsndfile raises several types
                problem("wav_unreadable", f"{wav}: {type(exc).__name__}: {exc}", rid)
            else:
                report.audio[rid] = {
                    "sample_rate_hz": info.samplerate,
                    "channels": info.channels,
                    "subtype": info.subtype,
                    "frames": info.frames,
                    "duration_s": info.duration,
                }
                if info.samplerate != ANALYSIS_SAMPLE_RATE:
                    problem("wav_sample_rate", f"{rid}: {info.samplerate} Hz, expected {ANALYSIS_SAMPLE_RATE}", rid)
                if info.channels != ANALYSIS_CHANNELS:
                    problem("wav_channels", f"{rid}: {info.channels} channels, expected {ANALYSIS_CHANNELS}", rid)
                if info.subtype not in SUPPORTED_SUBTYPES:
                    problem("wav_subtype", f"{rid}: {info.subtype}, expected one of {SUPPORTED_SUBTYPES}", rid)
                if info.frames <= 0:
                    problem("wav_zero_duration", f"{rid}: no audio frames", rid)
                report.hashes[str(wav.relative_to(data_dir))] = sha256_file(wav)

        if check_sources:
            if not rec.source_path.is_file():
                problem("source_missing", f"{rid}: source {rec.source_path.name} does not exist", rid)
            else:
                report.hashes[rec.filename] = sha256_file(rec.source_path)
                source_duration = _ffprobe_duration(rec.source_path)
                wav_duration = report.audio.get(rid, {}).get("duration_s")
                if source_duration is None:
                    report.warnings.append(Problem("source_duration_unchecked", f"{rid}: ffprobe unavailable", rid))
                elif wav_duration is not None:
                    report.audio[rid]["source_duration_s"] = source_duration
                    if abs(source_duration - float(wav_duration)) > SOURCE_DURATION_TOLERANCE_S:
                        problem(
                            "wav_source_duration_mismatch",
                            f"{rid}: WAV {wav_duration:.3f}s vs source {source_duration:.3f}s",
                            rid,
                        )

    report.recordings.sort(key=lambda r: r.recording_id)
    return report
