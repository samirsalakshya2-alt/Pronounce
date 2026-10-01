"""Mapping CTC frame indices to milliseconds.

Wav2Vec2-family models return frame-indexed spans, not sample-indexed ones. The
convolutional feature extractor downsamples by the product of its strides
(5*2*2*2*2*2*2 = 320 samples), so at 16 kHz one frame is exactly 20 ms.

Treating those frame indices as sample indices compresses an entire utterance
into its first few milliseconds, which is silent, plausible-looking and wrong.
`FrameClock` exists so that the conversion is stated once, checked against the
audio it came from, and reported alongside the timings it produces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Product of the Wav2Vec2 conv feature-extractor strides.
WAV2VEC2_STRIDE_SAMPLES = 320

# How far the frame count predicted from the stride may sit from the frame count
# the model actually returned. The receptive field costs a frame or so at the
# edges, so a small difference is expected; a large one means the stride
# assumption does not hold for this model.
DEFAULT_TOLERANCE_FRAMES = 4

TimingMethod = Literal["model_stride", "derived_from_duration"]


@dataclass(frozen=True)
class FrameClock:
    """Converts frame indices to milliseconds for one recognition result."""

    ms_per_frame: float
    method: TimingMethod
    n_frames: int
    duration_ms: float

    def start_ms(self, frame: int) -> float:
        """Start of `frame`, clamped to the recording."""
        return min(max(frame * self.ms_per_frame, 0.0), self.duration_ms)

    def end_ms(self, frame: int) -> float:
        """End of the frame *before* `frame`, i.e. the exclusive end of a span.

        Spans from CTC decoding are half-open `(start, end)` frame ranges, so the
        end frame index is already one past the last frame of the phone.
        """
        return min(max(frame * self.ms_per_frame, 0.0), self.duration_ms)

    def span_ms(self, span: tuple[int, int]) -> tuple[float, float]:
        start, end = span
        return self.start_ms(start), self.end_ms(end)

    def as_dict(self) -> dict[str, float | str | int]:
        return {
            "ms_per_frame": self.ms_per_frame,
            "method": self.method,
            "n_frames": self.n_frames,
            "duration_ms": self.duration_ms,
        }


def frame_clock(
    *,
    n_frames: int,
    n_samples: int,
    sample_rate: int,
    stride_samples: int = WAV2VEC2_STRIDE_SAMPLES,
    tolerance_frames: int = DEFAULT_TOLERANCE_FRAMES,
) -> FrameClock:
    """Build a `FrameClock` for a recognition result.

    Prefers the model's known stride. If the resulting frame count disagrees with
    the frame count the model returned by more than `tolerance_frames`, falls
    back to deriving the rate from the audio duration and says so via `method`,
    rather than silently applying a stride that does not hold.
    """
    duration_ms = n_samples * 1000.0 / sample_rate

    if n_frames <= 0:
        return FrameClock(
            ms_per_frame=0.0,
            method="derived_from_duration",
            n_frames=0,
            duration_ms=duration_ms,
        )

    stride_ms = stride_samples * 1000.0 / sample_rate
    predicted_frames = n_samples / stride_samples

    if abs(predicted_frames - n_frames) <= tolerance_frames:
        return FrameClock(
            ms_per_frame=stride_ms,
            method="model_stride",
            n_frames=n_frames,
            duration_ms=duration_ms,
        )

    return FrameClock(
        ms_per_frame=duration_ms / n_frames,
        method="derived_from_duration",
        n_frames=n_frames,
        duration_ms=duration_ms,
    )
