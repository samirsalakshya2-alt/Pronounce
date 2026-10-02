"""Acceptance checks on the committed-to benchmark run itself.

Skipped when the run directory is absent (benchmark results are personal data
and are not in Git). Re-validates the real 20 x 6 run from disk: every nominal
cell, statuses, integrity, source hashes, and that the summaries agree with the
cells they were computed from.
"""

import json

import pytest
from conftest import PROJECT_ROOT

from pronunciation_lab.benchmark.cells import CellStore
from pronunciation_lab.benchmark.dataset import preflight
from pronunciation_lab.benchmark.engines import ENGINES
from pronunciation_lab.benchmark.validate import validate_run

RUN_ID = "m2-20261002"
RUN_DIR = PROJECT_ROOT / "data" / "benchmark_results" / "runs" / RUN_ID

pytestmark = pytest.mark.skipif(not RUN_DIR.is_dir(), reason=f"benchmark run {RUN_ID} not present")


@pytest.fixture(scope="module")
def recordings():
    report = preflight(PROJECT_ROOT / "data")
    assert report.ok
    return report.recordings


def test_full_run_validates(recordings):
    v = validate_run(RUN_DIR, recordings, list(ENGINES), list(ENGINES))
    assert v["ok"], v["issues"][:10]
    assert v["nominal_cells"] == 120
    assert v["counts"] == {"ok": 40, "partial": 0, "failed": 0, "blocked": 60, "unresolved": 20, "missing": 0, "corrupt": 0}


def test_status_by_engine(recordings):
    store = CellStore(RUN_DIR)
    expected = {
        "openpronounce": "ok", "wav2vec2_raw": "ok", "wavlm": "unresolved",
        "azure_pronunciation": "blocked", "speechsuper": "blocked", "speechace": "blocked",
    }
    for eid, status in expected.items():
        assert {store.load(f"{r.recording_id}__{eid}").cell.status for r in recordings} == {status}


def test_summaries_agree_with_cells(recordings):
    summaries = RUN_DIR / "summaries"
    matrix = json.loads((summaries / "matrix.json").read_text())
    assert matrix["counts"] == {"ok": 40, "unresolved": 20, "blocked": 60}
    assert matrix["excluded_from_analysis"] == [
        {"cell_id": f"{r.recording_id}__{e}", "reason": f"no evidence ({s})"}
        for r in recordings
        for e, s in (("wavlm", "unresolved"), ("azure_pronunciation", "blocked"),
                     ("speechsuper", "blocked"), ("speechace", "blocked"))
    ]

    store = CellStore(RUN_DIR)
    phenomena = json.loads((summaries / "phenomena.json").read_text())
    for eid in ("openpronounce", "wav2vec2_raw"):
        per_rec = phenomena["engines"][eid]["per_recording"]
        for r in recordings:
            cell = store.load(f"{r.recording_id}__{eid}").cell
            n_expected = sum(len(w.phonemes) for w in cell.result.words)
            assert per_rec[r.recording_id]["n_expected"] == n_expected
            assert per_rec[r.recording_id]["n_heard"] == len(cell.result.engine_evidence["recognition"]["phones"])
        if eid == "openpronounce":
            for r in recordings:
                cell = store.load(f"{r.recording_id}__{eid}").cell
                assert per_rec[r.recording_id]["provider_phone_error_rate"] == cell.result.engine_evidence["comparison"]["phone_error_rate"]

    performance = json.loads((summaries / "performance.json").read_text())
    for eid in ("openpronounce", "wav2vec2_raw"):
        e = performance["engines"][eid]
        assert e["cold_runs"] == 1 and e["warm_runs"] == 19
        assert e["model_load_ms"]["n"] == 1


def test_repeatability_summary_present_and_clean():
    repeat = json.loads((RUN_DIR / "summaries" / "repeatability.json").read_text())
    assert repeat["summary"]["all_identical"] is True
    assert repeat["summary"]["cells_compared"] >= 8


def test_r08_alignment_artifact_is_excluded_on_both_sides():
    """The artifact phones of "evening" in R08 must never reach task outcomes."""
    tasks = json.loads((RUN_DIR / "summaries" / "task_analysis.json").read_text())
    for eid in ("openpronounce", "wav2vec2_raw"):
        occurrences = tasks["tasks"]["i_contrast"]["by_engine"][eid]["occurrences"]
        r08 = {(o["word"], o["expected"]): o["outcome"] for o in occurrences if o["recording_id"] == "R08"}
        evening = [o for (w, _), o in r08.items() if w == "evening"]
        assert evening and set(evening) == {"alignment_suspect"}
        assert all(o != "decoded_as_other" or w != "evening" for (w, _), o in r08.items())
