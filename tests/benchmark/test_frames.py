"""Frame -> millisecond conversion.

These tests encode the exact failure that made every timestamp this project ever
produced wrong: CTC frame indices were divided by the sample rate, as though they
were sample offsets. That compresses a whole utterance into its first ~20 ms.

R01's real numbers are used as the fixture (125611 samples at 16 kHz, 392 frames
returned by the model), so the regression is pinned to real data without needing
to load a model.
"""

from pronunciation_lab.benchmark.frames import frame_clock

R01_SAMPLES = 125_611
R01_FRAMES = 392
SAMPLE_RATE = 16_000


def _r01_clock():
    return frame_clock(
        n_frames=R01_FRAMES,
        n_samples=R01_SAMPLES,
        sample_rate=SAMPLE_RATE,
    )


def test_wav2vec2_frame_is_20ms():
    clock = _r01_clock()

    assert clock.method == "model_stride"
    assert clock.ms_per_frame == 20.0


def test_frame_indices_are_not_treated_as_sample_indices():
    """The specific regression guard.

    Frame 50 of R01 is the /θ/ of "Think", one second into the recording. The
    old sample-index reading placed it at 3.1 ms, inside the leading silence.
    """
    clock = _r01_clock()

    assert clock.start_ms(50) == 1000.0

    sample_index_reading = 50 / SAMPLE_RATE * 1000.0
    assert abs(clock.start_ms(50) - sample_index_reading) > 900.0


def test_spans_cover_the_recording():
    clock = _r01_clock()

    assert abs(clock.duration_ms - 7850.7) < 1.0
    # The last frame must land near the end of the audio, not at its start.
    assert clock.end_ms(R01_FRAMES) > clock.duration_ms * 0.9


def test_timings_are_clamped_to_the_recording():
    clock = _r01_clock()

    assert clock.start_ms(-5) == 0.0
    assert clock.end_ms(R01_FRAMES * 10) == clock.duration_ms


def test_falls_back_when_the_stride_does_not_hold():
    """A model with a different stride must not be forced onto 20 ms frames."""
    clock = frame_clock(
        n_frames=100,
        n_samples=R01_SAMPLES,
        sample_rate=SAMPLE_RATE,
    )

    assert clock.method == "derived_from_duration"
    assert abs(clock.ms_per_frame - clock.duration_ms / 100) < 1e-9


def test_empty_recognition_does_not_divide_by_zero():
    clock = frame_clock(n_frames=0, n_samples=0, sample_rate=SAMPLE_RATE)

    assert clock.ms_per_frame == 0.0
    assert clock.duration_ms == 0.0
