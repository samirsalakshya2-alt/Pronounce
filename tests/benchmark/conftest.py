import csv
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from pronunciation_lab.benchmark import ctc
from pronunciation_lab.benchmark.schema import PronunciationResult

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = PROJECT_ROOT / "data" / "benchmark_manifest.csv"
WAV_DIR = PROJECT_ROOT / "data" / "benchmark_wav"

# One 16 kHz sample, in ms: the resolution at which sample-rounded acoustic
# windows can differ from frame-derived phoneme timings.
ONE_SAMPLE_MS = 1000.0 / 16_000


@pytest.fixture(scope="session")
def r01_row() -> dict[str, str]:
    """Manifest row for R01; skips when the (untracked) benchmark data is absent."""
    audio_path = WAV_DIR / "R01.wav"
    if not MANIFEST.exists() or not audio_path.exists():
        pytest.skip("benchmark data missing (data/ is intentionally untracked)")

    with MANIFEST.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["recording_id"] == "R01":
                return {
                    **row,
                    "audio_path": str(audio_path),
                    "original_path": str(PROJECT_ROOT / "data" / row["filename"]),
                }

    raise AssertionError(f"R01 not found in {MANIFEST}")


def _run(engine, row):
    return engine.analyze(
        Path(row["audio_path"]),
        row["target_text"],
        recording_id="R01",
        original_path=Path(row["original_path"]),
    )


@pytest.fixture(scope="session")
def openpronounce_r01(r01_row):
    """(engine, result, log_posteriors, token_ids) for one real OpenPronounce run."""
    from pronunciation_lab.benchmark.engines.openpronounce import OpenPronounceEngine

    engine = OpenPronounceEngine()
    result = _run(engine, r01_row)
    recognition = engine.last_recognition
    return (
        engine,
        result,
        np.asarray(recognition.log_posteriors),
        engine._phone_token_ids(tuple(recognition.vocab)),
    )


@pytest.fixture(scope="session")
def raw_r01(r01_row):
    """(engine, result, log_posteriors, token_ids) for one real raw Wav2Vec2 run."""
    from pronunciation_lab.benchmark.engines.wav2vec2_raw import Wav2Vec2RawEngine

    engine = Wav2Vec2RawEngine(local_files_only=True)
    result = _run(engine, r01_row)
    recognition = engine.last_recognition
    return (
        engine,
        result,
        recognition.log_posteriors,
        ctc.phone_token_ids(recognition.vocab),
    )


def assert_evidence_invariants(
    result: PronunciationResult,
    log_posteriors: np.ndarray,
    token_ids: dict[str, list[int]],
    nbest_size: int,
) -> None:
    """Invariants every CTC-engine result must satisfy, checked against its source.

    Not "is it there" checks: every number that claims to come from the model is
    recomputed from the model's posteriorgram and compared.
    """
    PronunciationResult.model_validate_json(result.model_dump_json())

    audio = result.recording.audio
    duration = audio.duration_ms
    clock = result.engine_evidence["frame_clock"]
    n_frames = clock["n_frames"]
    ms_per_frame = clock["ms_per_frame"]
    recognition = result.engine_evidence["recognition"]

    # --- recording: analysis and original stay distinguishable ------------
    assert audio.analysis_path != audio.original_path
    assert Path(audio.original_path).exists()
    info = sf.info(audio.analysis_path)
    assert (audio.sample_rate_hz, audio.channels) == (16_000, 1)
    assert duration == pytest.approx(info.frames * 1000.0 / info.samplerate, abs=0.1)
    assert n_frames == log_posteriors.shape[0]

    # --- raw recognition preserved, and every heard phone accounted once ---
    heard_spans = [tuple(s) for s in recognition["frame_spans"]]
    assert len(recognition["phones"]) == len(recognition["confidences"]) == len(heard_spans)
    by_span = {
        tuple(span): (phone, conf)
        for phone, conf, span in zip(
            recognition["phones"], recognition["confidences"], heard_spans
        )
    }
    accounted: list[tuple[int, int]] = []

    phonemes = [p for w in result.words for p in w.phonemes]
    assert [p.position for p in phonemes] == list(range(len(phonemes)))

    for word in result.words:
        engine_timed = []

        for p in word.phonemes:
            t, o = p.timing, p.observed
            op = p.engine_evidence["operation"]

            # --- timing ------------------------------------------------
            assert 0.0 <= t.start_ms <= t.end_ms <= duration
            assert t.duration_ms == pytest.approx(t.end_ms - t.start_ms)
            assert t.duration_ms >= 0.0
            assert 0 <= t.frame_start <= t.frame_end <= n_frames
            assert (o.frame_start, o.frame_end) == (t.frame_start, t.frame_end)
            assert t.start_ms == pytest.approx(min(t.frame_start * ms_per_frame, duration))
            assert t.end_ms == pytest.approx(min(t.frame_end * ms_per_frame, duration))

            # --- observed vs operation: nothing invented for omissions ----
            if op == "omission":
                assert o.top is None and o.confidence is None
                assert t.source == "derived"
            else:
                assert t.source == "engine"
                span = (t.frame_start, t.frame_end)
                assert span in by_span, "observed phone must be a recognised phone"
                phone, conf = by_span[span]
                assert o.top == phone
                assert o.confidence == pytest.approx(conf)
                assert (op == "match") == (o.top == p.expected.phoneme)
                accounted.append(span)
                engine_timed.append(t)

            for extra in p.engine_evidence["extra_heard_phones"] + p.engine_evidence.get(
                "leading_heard_phones", []
            ):
                span = (extra["frame_start"], extra["frame_end"])
                assert by_span[span][0] == extra["phone"]
                accounted.append(span)

            # --- N-best: recomputed from the posteriorgram ---------------
            block = log_posteriors[t.frame_start : t.frame_end]
            probabilities = [c.probability for c in o.nbest]
            assert len(o.nbest) <= nbest_size
            assert probabilities == sorted(probabilities, reverse=True)
            for c in o.nbest:
                assert c.score is None
                expected = float(np.exp(block[:, token_ids[c.phoneme]].max()))
                assert c.probability == pytest.approx(expected, rel=1e-6)
            if block.size:
                # Nothing outside the list beats its weakest member.
                best_all = sorted(
                    (float(np.exp(block[:, ids].max())) for ids in token_ids.values()),
                    reverse=True,
                )
                assert probabilities == pytest.approx(best_all[: len(probabilities)])
            if o.top is not None:
                assert o.nbest[0].phoneme == o.top

            posterior = p.engine_evidence["expected_phone_posterior"]
            ids = token_ids.get(p.expected.phoneme)
            if ids and block.size:
                assert posterior == pytest.approx(float(np.exp(block[:, ids].max())), rel=1e-6)
            else:
                assert posterior is None

            # --- acoustics: tied to this span, window identified ---------
            a = p.acoustic
            f = a.spectral_features
            assert a.measured_on == "analysis"
            assert f["energy_scope"] == "exact span"
            assert f["energy_window_start_ms"] == pytest.approx(t.start_ms, abs=ONE_SAMPLE_MS)
            if t.end_ms > t.start_ms:
                assert f["energy_window_end_ms"] == pytest.approx(t.end_ms, abs=ONE_SAMPLE_MS)
            assert f["pitch_window_start_ms"] <= f["energy_window_start_ms"] + ONE_SAMPLE_MS
            assert f["pitch_window_end_ms"] >= f["energy_window_end_ms"] - ONE_SAMPLE_MS
            assert f["pitch_window_end_ms"] - f["pitch_window_start_ms"] >= min(50.0, duration) - ONE_SAMPLE_MS
            assert 0.0 <= f["zero_crossing_rate"] <= 1.0
            assert 0.0 <= f["spectral_centroid_hz"] <= 8_000.0
            assert a.relative_energy is not None and a.relative_energy >= 0.0
            if a.f0_hz is not None:
                assert a.voicing is True and 50.0 <= a.f0_hz <= 400.0
            if a.voicing is not None:
                assert a.voicing == (f["voicing_strength"] >= 0.3)

            # --- nothing a CTC recogniser cannot observe ----------------
            assert p.expected.syllable is None
            assert p.prosody.stress_observed is None
            assert p.prosody.stress_expected is None
            assert p.prosody.prominence is None

        # --- word timing is exactly the hull of its heard phones ---------
        if engine_timed:
            assert word.timing.source == "engine"
            assert word.timing.start_ms == min(t.start_ms for t in engine_timed)
            assert word.timing.end_ms == max(t.end_ms for t in engine_timed)
        else:
            assert word.timing.source == "unknown"
            assert word.timing.start_ms is None and word.timing.end_ms is None

    assert Counter(accounted) == Counter(heard_spans), "heard phones lost or duplicated"


@pytest.fixture
def check_invariants():
    return assert_evidence_invariants
