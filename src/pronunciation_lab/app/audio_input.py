"""Turning whatever the user provides into the analysis WAV.

Accepted: any file libsndfile reads (WAV, FLAC, OGG/Vorbis, ...) and, when
ffmpeg is installed, anything ffmpeg decodes — notably iPhone Voice Memos
(.m4a) and browser recordings (.webm / .mp4).

The analysis WAV is 16 kHz mono PCM_16, the benchmark format:

* a file already in that format is copied byte-for-byte, so analysing a
  benchmark WAV gives exactly the M2 evidence;
* other files readable by libsndfile are downmixed/resampled with the same
  loader the raw engine uses;
* everything else is converted with the exact ffmpeg command used by
  `scripts/prepare_benchmark_audio.py`, so an original .m4a yields the same WAV
  the benchmark was built from.

The original upload is kept next to the analysis WAV, but playback and
timings always refer to the analysis WAV (the M4A original is offset by AAC
priming, ~64 ms in the benchmark).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from pronunciation_lab.benchmark.waveform import ANALYSIS_SAMPLE_RATE, load_analysis_waveform

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MIN_DURATION_MS = 300.0
MAX_DURATION_MS = 60_000.0
FFMPEG_TIMEOUT_S = 60

_SAFE_SUFFIX = re.compile(r"^\.[a-z0-9]{1,5}$")


class AudioInputError(Exception):
    """A problem with the user's audio, with a message meant for the user."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PreparedAudio:
    original_path: Path
    original_name: str
    analysis_path: Path
    duration_ms: float
    conversion: str  # "copied" | "resampled" | "ffmpeg"
    original_sha256: str
    source: dict[str, object]


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _suffix(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if _SAFE_SUFFIX.match(suffix) else ".bin"


def _is_analysis_format(info) -> bool:
    return info.samplerate == ANALYSIS_SAMPLE_RATE and info.channels == 1 and info.subtype == "PCM_16" and info.format == "WAV"


def prepare_audio(data: bytes, filename: str, workdir: Path) -> PreparedAudio:
    """Validate and convert one upload into `workdir/analysis.wav`."""
    if not data:
        raise AudioInputError("audio_empty", "No audio was received.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise AudioInputError(
            "audio_too_large",
            f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    workdir.mkdir(parents=True, exist_ok=True)
    original = workdir / f"original{_suffix(filename)}"
    original.write_bytes(data)
    analysis = workdir / "analysis.wav"

    source: dict[str, object] = {"filename": Path(filename or "").name or "upload", "bytes": len(data)}
    try:
        info = sf.info(str(original))
    except Exception:  # noqa: BLE001 - not readable by libsndfile
        info = None

    if info is not None and info.frames > 0:
        source |= {"sample_rate_hz": info.samplerate, "channels": info.channels, "subtype": info.subtype}
        if _is_analysis_format(info):
            shutil.copyfile(original, analysis)
            conversion = "copied"
        else:
            waveform = load_analysis_waveform(original)
            sf.write(str(analysis), waveform.samples, ANALYSIS_SAMPLE_RATE, subtype="PCM_16")
            conversion = "resampled"
    elif info is not None:  # readable header, no audio
        raise AudioInputError("audio_too_short", "The recording contains no audio.")
    else:
        if not ffmpeg_available():
            raise AudioInputError(
                "audio_unsupported",
                "This audio format needs ffmpeg, which is not installed. Upload a WAV file instead.",
            )
        try:
            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", str(original), "-vn", "-ac", "1", "-ar", str(ANALYSIS_SAMPLE_RATE),
                 "-c:a", "pcm_s16le", str(analysis)],
                capture_output=True, timeout=FFMPEG_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as exc:
            raise AudioInputError("audio_unreadable", "Converting the audio took too long.") from exc
        if proc.returncode != 0 or not analysis.exists():
            analysis.unlink(missing_ok=True)
            raise AudioInputError("audio_unreadable", "The audio could not be read. Is it a valid recording?")
        conversion = "ffmpeg"

    try:
        out = sf.info(str(analysis))
    except Exception as exc:  # noqa: BLE001
        raise AudioInputError("audio_unreadable", "The audio could not be read.") from exc

    duration_ms = out.frames * 1000.0 / out.samplerate
    if duration_ms < MIN_DURATION_MS:
        raise AudioInputError(
            "audio_too_short",
            f"The recording is {duration_ms / 1000:.2f} s long; record at least {MIN_DURATION_MS / 1000:.1f} s.",
        )
    if duration_ms > MAX_DURATION_MS:
        raise AudioInputError(
            "audio_too_long",
            f"The recording is {duration_ms / 1000:.0f} s long; the limit is {MAX_DURATION_MS / 1000:.0f} s.",
        )

    return PreparedAudio(
        original_path=original,
        original_name=str(source["filename"]),
        analysis_path=analysis,
        duration_ms=duration_ms,
        conversion=conversion,
        original_sha256=hashlib.sha256(data).hexdigest(),
        source=source,
    )


def is_silent(path: Path, threshold_dbfs: float = -60.0) -> bool:
    """True when the whole analysis WAV is below `threshold_dbfs` RMS."""
    samples, _ = sf.read(str(path), dtype="float64")
    if samples.size == 0:
        return True
    rms = float(np.sqrt(np.mean(np.square(samples))))
    return 20.0 * np.log10(rms + 1e-12) < threshold_dbfs
