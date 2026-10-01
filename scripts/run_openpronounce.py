"""Run the OpenPronounce engine over one benchmark recording and print the evidence.

The project is not installed into the venv (pytest puts `src` on the path via
`tool.pytest.ini_options`), so run this as:

    PYTHONPATH=src uv run python scripts/run_openpronounce.py
"""

import csv
from pathlib import Path

from pronunciation_lab.benchmark.engines.openpronounce import OpenPronounceEngine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT_ROOT / "data" / "benchmark_manifest.csv"
WAV_DIR = PROJECT_ROOT / "data" / "benchmark_wav"

RECORDING_ID = "R01"


with MANIFEST.open(newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

row = next(r for r in rows if r["recording_id"] == RECORDING_ID)

audio_path = WAV_DIR / f"{RECORDING_ID}.wav"
original_path = PROJECT_ROOT / "data" / row["filename"]

print("Recording:", row["recording_id"])
print("Target:   ", row["target_text"])
print("Audio:    ", audio_path)

engine = OpenPronounceEngine()

result = engine.analyze(
    audio_path,
    row["target_text"],
    recording_id=row["recording_id"],
    original_path=original_path,
)

evidence = result.engine_evidence
comparison = evidence["comparison"]

print("\n=== RECOGNITION ===")
print("heard phones:", " ".join(evidence["recognition"]["phones"]))
print("phone error rate:", comparison["phone_error_rate"])
print("words flagged by provider:", comparison["words_with_errors"])
print("frame clock:", evidence["frame_clock"])

print("\n=== WORDS ===")
for word in result.words:
    timing = word.timing
    span = (
        f"{timing.start_ms:7.1f}-{timing.end_ms:7.1f} ms"
        if timing.start_ms is not None
        else "        no engine timing"
    )
    flagged = " [flagged]" if word.engine_evidence.get("flagged_by_provider") else ""
    print(f"\n{word.word:>14}  {span}{flagged}")

    for phoneme in word.phonemes:
        observed = phoneme.observed.top or "-"
        operation = phoneme.engine_evidence["operation"]
        confidence = phoneme.observed.confidence
        confidence_text = f"{confidence:.3f}" if confidence is not None else "  -  "
        nbest = ", ".join(
            f"{c.phoneme}:{c.probability:.2f}" for c in phoneme.observed.nbest[:3]
        )
        print(
            f"    {phoneme.expected.phoneme:>3} -> {observed:<3} "
            f"{operation:<12} conf={confidence_text} "
            f"{phoneme.timing.start_ms:7.1f}-{phoneme.timing.end_ms:7.1f} ms "
            f"({phoneme.timing.source}) "
            f"energy={phoneme.acoustic.energy_db:6.1f} dB  "
            f"nbest=[{nbest}]"
        )

print("\n=== PROCESSING ===")
print(result.processing.model_dump())

if result.errors:
    print("\n=== PROCESSING ERRORS ===")
    for error in result.errors:
        print(f"{error.type}: {error.message}")
