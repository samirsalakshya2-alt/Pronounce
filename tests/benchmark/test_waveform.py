import numpy as np
import pytest
import soundfile as sf

from pronunciation_lab.benchmark.waveform import load_analysis_waveform


def test_16k_mono_passes_through_untouched(tmp_path):
    samples = np.random.default_rng(0).uniform(-0.5, 0.5, 16_000).astype(np.float32)
    sf.write(tmp_path / "a.wav", samples, 16_000, subtype="FLOAT")

    w = load_analysis_waveform(tmp_path / "a.wav")
    np.testing.assert_array_equal(w.samples, samples)
    assert (w.resampled, w.downmixed) == (False, False)
    assert w.duration_ms == pytest.approx(1000.0)


def test_stereo_is_downmixed_by_averaging(tmp_path):
    left = np.full(16_000, 0.4, np.float32)
    right = np.full(16_000, -0.2, np.float32)
    sf.write(tmp_path / "s.wav", np.stack([left, right], 1), 16_000, subtype="FLOAT")

    w = load_analysis_waveform(tmp_path / "s.wav")
    assert w.downmixed and not w.resampled
    np.testing.assert_allclose(w.samples, 0.1, atol=1e-6)
    assert w.describe()["source_channels"] == 2


@pytest.mark.parametrize("rate", [8_000, 22_050, 44_100, 48_000])
def test_resampling_preserves_duration_and_pitch(tmp_path, rate):
    t = np.arange(rate) / rate
    sf.write(tmp_path / "r.wav", (0.5 * np.sin(2 * np.pi * 200 * t)).astype(np.float32), rate)

    w = load_analysis_waveform(tmp_path / "r.wav")
    assert w.resampled and w.sample_rate == 16_000
    assert w.source_sample_rate == rate
    assert w.samples.size == 16_000
    spectrum = np.abs(np.fft.rfft(w.samples))
    assert np.fft.rfftfreq(w.samples.size, 1 / 16_000)[spectrum.argmax()] == pytest.approx(200, abs=1)
