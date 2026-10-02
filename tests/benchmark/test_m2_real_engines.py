"""The runner with the real engines and the real recordings R01-R04.

Runs the whole 6-engine matrix for one text group into a temporary run
directory: real evaluations for OpenPronounce and raw Wav2Vec2, blocked and
unresolved cells for the rest. Checks raw-evidence preservation against the
engines, benchmark-level determinism, integrity, and that the source data is
untouched.
"""

from pathlib import Path

import numpy as np
import pytest
from conftest import PROJECT_ROOT

from pronunciation_lab.benchmark import analysis as A
from pronunciation_lab.benchmark.cells import CellStore
from pronunciation_lab.benchmark.dataset import preflight, sha256_file
from pronunciation_lab.benchmark.engines import ENGINES
from pronunciation_lab.benchmark.repeatability import check_repeatability
from pronunciation_lab.benchmark.runner import BenchmarkRunner
from pronunciation_lab.benchmark.validate import validate_run

IDS = ("R01", "R02", "R03", "R04")


@pytest.fixture(scope="module")
def real_run(tmp_path_factory, r01_row):
    data = PROJECT_ROOT / "data"
    report = preflight(data)
    assert report.ok
    before = dict(report.hashes)
    recordings = [r for r in report.recordings if r.recording_id in IDS]
    run_dir = tmp_path_factory.mktemp("m2") / "real"
    runner = BenchmarkRunner(run_dir, "real", recordings, list(ENGINES), data_dir=data)
    result = runner.run(report)
    return runner, report, recordings, result, before


def test_matrix_states(real_run):
    runner, _, recordings, result, _ = real_run
    assert len(result.executed) == 8
    assert len(result.recorded_without_execution) == 16
    v = validate_run(runner.run_dir, recordings, list(ENGINES), list(ENGINES))
    assert v["ok"], v["issues"]
    assert v["counts"] == {"ok": 8, "partial": 0, "failed": 0, "blocked": 12, "unresolved": 4, "missing": 0, "corrupt": 0}


def test_raw_evidence_is_the_engines_own_output(real_run):
    runner, _, _, _, _ = real_run
    store = CellStore(runner.run_dir)
    for eid in ("openpronounce", "wav2vec2_raw"):
        cell = store.load(f"R01__{eid}").cell
        raw = store.load_raw(cell)
        recognition = cell.result.engine_evidence["recognition"]
        assert raw["heard_phones"].tolist() == recognition["phones"]
        assert raw["frame_spans"].tolist() == recognition["frame_spans"]
        np.testing.assert_allclose(raw["confidences"], recognition["confidences"])
        assert raw["log_posteriors"].shape == (392, 392) and raw["vocab"].shape == (392,)
        # The posteriorgram reproduces the stored N-best exactly.
        p = cell.result.words[0].phonemes[0]
        block = raw["log_posteriors"][p.timing.frame_start:p.timing.frame_end]
        token = list(raw["vocab"]).index(p.observed.top) if eid == "wav2vec2_raw" else None
        if token is not None:
            assert p.observed.nbest[0].probability == pytest.approx(float(np.exp(block[:, token].max())))
        assert cell.raw_evidence.arrays["log_posteriors"] == [392, 392]


def test_cells_carry_engine_identity_and_timing(real_run):
    runner, _, _, _, _ = real_run
    store = CellStore(runner.run_dir)
    raw = store.load("R01__wav2vec2_raw").cell
    assert raw.engine_model == "facebook/wav2vec2-lv-60-espeak-cv-ft@ae45363bf3413b374fecd9dc8bc1df0e24c3b7f4"
    assert raw.performance.run_type == "cold" and raw.performance.model_load_ms > 0
    warm = store.load("R02__wav2vec2_raw").cell.performance
    assert warm.run_type == "warm" and warm.model_load_ms is None
    assert warm.stages_ms and "g2p" in warm.stages_ms
    for rid in IDS:
        p = store.load(f"{rid}__openpronounce").cell.performance
        assert p.inference_ms + p.postprocessing_ms <= p.wall_time_ms <= p.runner_wall_ms


def test_blocked_and_unresolved_cells_carry_the_engines_reason(real_run):
    runner, _, _, _, _ = real_run
    store = CellStore(runner.run_dir)
    for eid in ("azure_pronunciation", "speechsuper", "speechace"):
        cell = store.load(f"R01__{eid}").cell
        assert cell.status == "blocked" and cell.result is None and cell.raw_evidence is None
        assert cell.availability["missing_env"] and cell.engine_mode == "cloud"
    wavlm = store.load("R01__wavlm").cell
    assert wavlm.status == "unresolved"
    assert wavlm.availability["candidates"]["Jianshu001/wavlm-phoneme-scorer"]["verdict"].startswith("not adopted")


def test_benchmark_rerun_is_deterministic(real_run):
    runner, _, recordings, _, _ = real_run
    report = check_repeatability(runner.run_dir, recordings[:2], ["openpronounce", "wav2vec2_raw", "wavlm"])
    assert report["summary"]["all_identical"], report["summary"]
    assert report["summary"]["cells_compared"] == 4 and report["summary"]["reruns"] == 8
    assert {r["run_type"] for c in report["comparisons"] for r in c["runs"]} == {"cold", "warm"}
    assert {"engine_id": "wavlm", "reason": "engine unresolved"} in report["not_checked"]


def test_repeatability_detects_a_difference(real_run):
    """Fault injection: a stored cell edited after the run must not compare equal."""
    runner, _, recordings, _, _ = real_run
    store = CellStore(runner.run_dir)
    cell = store.load("R03__wav2vec2_raw").cell
    original = cell.model_copy(deep=True)
    cell.result.words[1].phonemes[0].observed.confidence += 0.01
    store.write(cell, {k: v for k, v in store.load_raw(cell).items()})
    try:
        rec = next(r for r in recordings if r.recording_id == "R03")
        report = check_repeatability(runner.run_dir, [rec], ["wav2vec2_raw"])
        assert report["summary"]["differing_cells"] == ["R03__wav2vec2_raw"]
        assert any("confidence" in d for d in report["comparisons"][0]["runs"][0]["differences"])
    finally:
        store.write(original, store.load_raw(original))


def test_analysis_of_the_real_run(real_run):
    runner, _, recordings, _, _ = real_run
    A.write_analysis(runner.run_dir, recordings, list(ENGINES))
    import json

    tasks = json.loads((runner.run_dir / "summaries" / "task_analysis.json").read_text())
    th = tasks["tasks"]["th_dental_fricatives"]["by_engine"]
    for eid in ("openpronounce", "wav2vec2_raw"):
        r03 = th[eid]["per_recording"]["R03"]["outcomes"]
        assert r03.get("decoded_as_stop") == 1  # "three" decoded with [t] in fast speech
        r04 = th[eid]["per_recording"]["R04"]["outcomes"]
        assert r04 == {"decoded_as_expected": 5}  # deliberate θ→t NOT decoded as t
    for eid in ("wavlm", "azure_pronunciation"):
        assert th[eid]["status"] in (["unresolved"], ["blocked"])

    style = json.loads((runner.run_dir / "summaries" / "style_comparison.json").read_text())
    group = style["groups"]["think_three"]["engines"]["wav2vec2_raw"]
    assert group["speed_order"] == ["R01", "R02", "R03"]
    want = [p for p in group["deviating_positions"] if p["word"] == "want" and p["expected"] == "ɔ"]
    assert want and want[0]["label"] == "consistent_deviation_candidate"


def test_source_data_untouched(real_run):
    runner, report, _, _, before = real_run
    after = {key: sha256_file(report.data_dir / key) for key in before}
    assert after == before
    # Nothing but results inside the run directory.
    assert not list(Path(runner.run_dir).rglob("*.wav")) and not list(Path(runner.run_dir).rglob("*.m4a"))
