"""Engine-independent acoustic measurements over time spans of a waveform.

These measurements are computed directly from the analysis waveform, so they are
available for any engine that can tell us *when* something happened, and they
never depend on a particular engine's own feature extraction (or on a network
call).

Methodological notes
--------------------
A CTC phone "span" marks the frames where the recogniser's posterior peaked for
that phone. It is **not** the articulatory extent of the phone, and it is
usually far shorter. Two consequences are handled explicitly here:

* Energy is reported over the exact span, because summing energy over a wider
  window would mix in the neighbouring sounds.
* Pitch and voicing need several pitch periods before they are estimable at
  all, so they are measured over an explicitly widened window centred on the
  span.

Every window actually used is reported back in the result, so that no
measurement can later be mistaken for a phone duration.

Nothing here decides whether a sound was "weak", "reduced" or "chewed". It only
reports measurements; interpretation belongs further up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Pitch search range. Wide enough for adult male and female speakers; a value
# outside it is reported as "no pitch found" rather than being clamped.
F0_MIN_HZ = 50.0
F0_MAX_HZ = 400.0

# Normalised-autocorrelation peak above which a window is called voiced. This is
# a conventional voicing-decision heuristic, not a tuned parameter: the raw peak
# value is always reported as `voicing_strength` so the decision can be revisited
# (or recomputed with a different threshold) without re-running any engine.
VOICING_AUTOCORRELATION_THRESHOLD = 0.3

# Pitch/voicing need at least a couple of pitch periods. 50 ms covers 2.5
# periods at the 50 Hz floor.
DEFAULT_MIN_WINDOW_MS = 50.0

_EPS = 1e-12


@dataclass(frozen=True)
class SpanAcoustics:
    """Acoustic measurements for one time span of a waveform.

    `span_*_ms` is the span that was asked about. `window_*_ms` is the widened
    window that pitch/voicing/spectral measurements actually used. They differ
    whenever the span was too short to estimate pitch from.
    """

    span_start_ms: float
    span_end_ms: float
    window_start_ms: float
    window_end_ms: float

    rms: float
    energy_db: float
    relative_energy: float | None

    f0_hz: float | None
    voiced: bool | None
    voicing_strength: float

    zero_crossing_rate: float
    spectral_centroid_hz: float

    notes: dict[str, str] = field(default_factory=dict)

    def spectral_features(self) -> dict[str, float | str | None]:
        """The subset that belongs in `AcousticEvidence.spectral_features`."""
        return {
            "zero_crossing_rate": self.zero_crossing_rate,
            "spectral_centroid_hz": self.spectral_centroid_hz,
            "voicing_strength": self.voicing_strength,
            "rms": self.rms,
            "energy_window_start_ms": self.span_start_ms,
            "energy_window_end_ms": self.span_end_ms,
            "pitch_window_start_ms": self.window_start_ms,
            "pitch_window_end_ms": self.window_end_ms,
            **self.notes,
        }


def waveform_rms(waveform: np.ndarray) -> float:
    """Reference RMS of a whole waveform, used to make energy comparable across spans.

    This includes any leading/trailing silence, so `relative_energy` is a
    utterance-relative ratio and not a speech-relative one.
    """
    if waveform.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(np.asarray(waveform, dtype=np.float64)))))


def _ms_to_sample(ms: float, sample_rate: int) -> int:
    return int(round(ms * sample_rate / 1000.0))


def _estimate_f0(
    segment: np.ndarray,
    sample_rate: int,
) -> tuple[float | None, float]:
    """Autocorrelation pitch estimate.

    Returns `(f0_hz_or_None, normalised_peak)`. The peak is returned even when no
    pitch is accepted, so the caller can see how close the decision was.
    """
    if segment.size == 0:
        return None, 0.0

    seg = np.asarray(segment, dtype=np.float64)
    seg = seg - seg.mean()

    # One period at the pitch floor is the bare minimum to see any periodicity.
    min_samples = int(sample_rate / F0_MIN_HZ) + 1
    if seg.size < min_samples:
        return None, 0.0

    energy = float(np.dot(seg, seg))
    if energy <= _EPS:
        return None, 0.0

    correlation = np.correlate(seg, seg, mode="full")[seg.size - 1 :]
    correlation = correlation / correlation[0]

    min_lag = max(1, int(sample_rate / F0_MAX_HZ))
    max_lag = min(int(sample_rate / F0_MIN_HZ), correlation.size - 1)
    if max_lag <= min_lag:
        return None, 0.0

    search = correlation[min_lag : max_lag + 1]
    offset = int(np.argmax(search))
    peak = float(search[offset])
    lag = min_lag + offset

    if lag <= 0:
        return None, peak

    return float(sample_rate) / lag, peak


def _zero_crossing_rate(segment: np.ndarray) -> float:
    if segment.size < 2:
        return 0.0
    signs = np.signbit(np.asarray(segment, dtype=np.float64))
    return float(np.count_nonzero(signs[1:] != signs[:-1]) / (segment.size - 1))


def _spectral_centroid_hz(segment: np.ndarray, sample_rate: int) -> float:
    if segment.size < 2:
        return 0.0

    seg = np.asarray(segment, dtype=np.float64)
    seg = seg - seg.mean()
    windowed = seg * np.hanning(seg.size)

    magnitude = np.abs(np.fft.rfft(windowed))
    total = float(magnitude.sum())
    if total <= _EPS:
        return 0.0

    freqs = np.fft.rfftfreq(seg.size, d=1.0 / sample_rate)
    return float(np.dot(magnitude, freqs) / total)


def measure_span(
    waveform: np.ndarray,
    sample_rate: int,
    start_ms: float,
    end_ms: float,
    *,
    reference_rms: float | None = None,
    min_window_ms: float = DEFAULT_MIN_WINDOW_MS,
) -> SpanAcoustics:
    """Measure one span of `waveform`.

    `start_ms`/`end_ms` are clamped to the waveform; a degenerate span is
    widened to a single sample so that energy is still defined.
    """
    waveform = np.asarray(waveform)
    n_samples = waveform.size

    start = min(max(_ms_to_sample(start_ms, sample_rate), 0), max(n_samples - 1, 0))
    end = min(max(_ms_to_sample(end_ms, sample_rate), start + 1), n_samples)

    span = waveform[start:end]

    rms = float(np.sqrt(np.mean(np.square(np.asarray(span, dtype=np.float64)))))
    energy_db = float(20.0 * np.log10(rms + _EPS))

    relative_energy: float | None = None
    if reference_rms is not None and reference_rms > _EPS:
        relative_energy = float(rms / reference_rms)

    # Widen symmetrically around the span for pitch/voicing/spectral estimates.
    min_window_samples = max(_ms_to_sample(min_window_ms, sample_rate), end - start)
    centre = (start + end) / 2.0
    half = min_window_samples / 2.0

    window_start = int(round(centre - half))
    window_end = int(round(centre + half))

    # Keep the requested width when the span sits at either edge of the audio.
    if window_start < 0:
        window_end += -window_start
        window_start = 0
    if window_end > n_samples:
        window_start = max(0, window_start - (window_end - n_samples))
        window_end = n_samples

    analysis = waveform[window_start:window_end]

    f0_hz, voicing_strength = _estimate_f0(analysis, sample_rate)

    voiced: bool | None
    if f0_hz is None and voicing_strength == 0.0:
        # The window was too short or silent to decide either way.
        voiced = None
    else:
        voiced = voicing_strength >= VOICING_AUTOCORRELATION_THRESHOLD

    # Pitch is only meaningful where the window actually looks voiced.
    if not voiced:
        f0_hz = None

    notes = {
        "energy_scope": "exact span",
        "pitch_scope": (
            "span" if (window_end - window_start) <= (end - start) else "widened window"
        ),
        "voicing_method": "normalised autocorrelation peak",
    }

    return SpanAcoustics(
        span_start_ms=start * 1000.0 / sample_rate,
        span_end_ms=end * 1000.0 / sample_rate,
        window_start_ms=window_start * 1000.0 / sample_rate,
        window_end_ms=window_end * 1000.0 / sample_rate,
        rms=rms,
        energy_db=energy_db,
        relative_energy=relative_energy,
        f0_hz=f0_hz,
        voiced=voiced,
        voicing_strength=voicing_strength,
        zero_crossing_rate=_zero_crossing_rate(analysis),
        spectral_centroid_hz=_spectral_centroid_hz(analysis, sample_rate),
        notes=notes,
    )
