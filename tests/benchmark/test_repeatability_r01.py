"""Repeatability on real R01.

On CPU, both engines are expected to be bit-for-bit deterministic in every piece
of evidence. Only processing times vary (they are wall-clock measurements), and
`run_type` changes from cold to warm by design.
"""

from pathlib import Path

import numpy as np

from pronunciation_lab.benchmark.engines.openpronounce import OpenPronounceEngine
from pronunciation_lab.benchmark.engines.wav2vec2_raw import Wav2Vec2RawEngine


def _evidence(result):
    data = result.model_dump()
    data.pop("processing")
    return data


def _run(engine, row):
    return engine.analyze(
        Path(row["audio_path"]),
        row["target_text"],
        recording_id="R01",
        original_path=Path(row["original_path"]),
    )


def test_raw_is_deterministic_warm_and_across_instances(raw_r01, r01_row):
    engine, first, log_posteriors, _ = raw_r01

    warm = _run(engine, r01_row)
    assert warm.processing.run_type == "warm"
    assert warm.processing.model_load_ms is None
    assert _evidence(warm) == _evidence(first)
    np.testing.assert_array_equal(engine.last_recognition.log_posteriors, log_posteriors)

    fresh_engine = Wav2Vec2RawEngine(local_files_only=True)
    fresh = _run(fresh_engine, r01_row)
    assert fresh.processing.run_type == "cold"
    assert _evidence(fresh) == _evidence(first)
    np.testing.assert_array_equal(fresh_engine.last_recognition.log_posteriors, log_posteriors)


def test_openpronounce_is_deterministic_warm_and_across_instances(openpronounce_r01, r01_row):
    engine, first, log_posteriors, _ = openpronounce_r01

    warm = _run(engine, r01_row)
    assert warm.processing.run_type == "warm"
    assert _evidence(warm) == _evidence(first)
    np.testing.assert_array_equal(engine.last_recognition.log_posteriors, log_posteriors)

    # OpenPronounce caches its model process-wide, so a second instance reuses
    # it; the instance itself still reports a cold first run.
    fresh = _run(OpenPronounceEngine(), r01_row)
    assert _evidence(fresh) == _evidence(first)
