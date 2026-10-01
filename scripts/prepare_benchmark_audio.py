import csv
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
MANIFEST = DATA_DIR / "benchmark_manifest.csv"
OUTPUT_DIR = DATA_DIR / "benchmark_wav"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


with MANIFEST.open(newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

print(f"Found {len(rows)} recordings in manifest.")

if len(rows) != 20:
    raise RuntimeError(f"Expected 20 recordings, found {len(rows)}")


for row in rows:
    recording_id = row["recording_id"]
    source = DATA_DIR / row["filename"]
    output = OUTPUT_DIR / f"{recording_id}.wav"

    if not source.exists():
        raise FileNotFoundError(
            f"{recording_id}: source file not found: {source}"
        )

    print(f"\n{recording_id}: {source.name}")

    source_duration = get_duration(source)

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i", str(source),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(output),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    output_duration = get_duration(output)

    print(f"  Source:  {source_duration:.2f}s")
    print(f"  WAV:     {output_duration:.2f}s")
    print(f"  Created: {output.name}")


print("\nAudio preprocessing complete.")
print(f"WAV files: {OUTPUT_DIR}")
