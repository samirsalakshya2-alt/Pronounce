"""Unit tests for the engine-independent acoustic layer, on synthetic signals
whose true properties are known."""

import numpy as np
import pytest

from pronunciation_lab.acoustics import (
    DEFAULT_MIN_WINDOW_MS,
    measure_span,
    waveform_rms,
)

SR = 16_000


def _tone(hz, seconds=1.0, amplitude=0.5):
    t = np.arange(int(SR * seconds)) / SR
    return (amplitude * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _noise(seconds=1.0, amplitude=0.5, seed=0):
    return (np.random.default_rng(seed).uniform(-1, 1, int(SR * seconds)) * amplitude).astype(np.float32)


@pytest.mark.parametrize("hz", [60.0, 100.0, 150.0, 220.0, 300.0])
@pytest.mark.parametrize("span_ms", [10.0, 100.0])
def test_f0_of_a_pure_tone(hz, span_ms):
    m = measure_span(_tone(hz), SR, 400.0, 400.0 + span_ms)
    assert m.voiced is True
    assert m.f0_hz == pytest.approx(hz, rel=0.03)

    # The autocorrelation is the biased estimator, so even a perfect tone peaks
    # at 1 - lag/N: low pitch in a short window scores lower. Pinned so the
    # bias is known when voicing_strength is interpreted.
    window = (m.window_end_ms - m.window_start_ms) * SR / 1000.0
    assert m.voicing_strength == pytest.approx(1 - (SR / hz) / window, abs=0.02)


def test_white_noise_is_unvoiced_with_no_pitch_and_high_zcr():
    m = measure_span(_noise(), SR, 400.0, 500.0)
    assert m.voiced is False
    assert m.f0_hz is None
    assert m.zero_crossing_rate > 0.3
    assert m.spectral_centroid_hz > 2_000


def test_digital_silence_is_undecidable_not_unvoiced():
    m = measure_span(np.zeros(SR, np.float32), SR, 100.0, 200.0)
    assert m.voiced is None
    assert m.f0_hz is None
    assert m.energy_db < -200


def test_energy_and_relative_energy():
    wave = _tone(200, amplitude=0.5)
    m = measure_span(wave, SR, 0.0, 1000.0, reference_rms=waveform_rms(wave))
    assert m.rms == pytest.approx(0.5 / np.sqrt(2), rel=1e-3)
    assert m.energy_db == pytest.approx(20 * np.log10(0.5 / np.sqrt(2)), abs=0.01)
    assert m.relative_energy == pytest.approx(1.0, rel=1e-3)

    quiet = measure_span(wave * 0.1, SR, 0.0, 1000.0, reference_rms=waveform_rms(wave))
    assert quiet.relative_energy == pytest.approx(0.1, rel=1e-3)
    assert measure_span(wave, SR, 0, 100).relative_energy is None


def test_energy_is_over_the_exact_span_not_the_widened_window():
    # Loud tone only inside [480, 500) ms; silence around it.
    wave = np.zeros(SR, np.float32)
    wave[int(0.48 * SR) : int(0.50 * SR)] = _tone(200, 0.02)
    m = measure_span(wave, SR, 480.0, 500.0)

    assert (m.span_start_ms, m.span_end_ms) == (480.0, 500.0)
    assert m.rms == pytest.approx(0.5 / np.sqrt(2), rel=0.05)
    # Pitch window was widened to the minimum, centred on the span.
    assert m.window_end_ms - m.window_start_ms == pytest.approx(DEFAULT_MIN_WINDOW_MS)
    assert (m.window_start_ms + m.window_end_ms) / 2 == pytest.approx(490.0)
    assert m.notes["pitch_scope"] == "widened window"


def test_window_keeps_its_width_at_the_recording_edges():
    wave = _tone(200)
    start = measure_span(wave, SR, 0.0, 20.0)
    end = measure_span(wave, SR, 980.0, 1000.0)

    assert start.window_start_ms == 0.0
    assert start.window_end_ms == pytest.approx(DEFAULT_MIN_WINDOW_MS)
    assert end.window_end_ms == pytest.approx(1000.0)
    assert end.window_start_ms == pytest.approx(1000.0 - DEFAULT_MIN_WINDOW_MS)


def test_spans_outside_or_degenerate_are_clamped_not_crashed():
    wave = _tone(200)
    beyond = measure_span(wave, SR, 1200.0, 1500.0)
    assert beyond.span_end_ms <= 1000.0
    assert beyond.span_start_ms <= beyond.span_end_ms

    point = measure_span(wave, SR, 500.0, 500.0)
    # Widened to one sample so energy is still defined.
    assert point.span_end_ms - point.span_start_ms == pytest.approx(1000.0 / SR)
    assert np.isfinite(point.energy_db)


def test_long_span_uses_the_span_itself_for_pitch():
    m = measure_span(_tone(150), SR, 100.0, 400.0)
    assert (m.window_start_ms, m.window_end_ms) == (100.0, 400.0)
    assert m.notes["pitch_scope"] == "span"


def test_every_measurement_reports_its_windows():
    features = measure_span(_tone(150), SR, 100.0, 110.0).spectral_features()
    for key in (
        "energy_window_start_ms",
        "energy_window_end_ms",
        "pitch_window_start_ms",
        "pitch_window_end_ms",
        "voicing_strength",
        "voicing_method",
    ):
        assert key in features


def test_waveform_rms_of_empty_is_zero():
    assert waveform_rms(np.zeros(0, np.float32)) == 0.0
