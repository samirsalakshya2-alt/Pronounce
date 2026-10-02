"""Cell invariants and raw-evidence correspondence, one violation at a time."""

from types import SimpleNamespace

import numpy as np
import pytest
from benchmark_fakes import FakeEngine, factory, make_dataset, make_result

from pronunciation_lab.benchmark.base import ErrorType
from pronunciation_lab.benchmark.cells import (
    BenchmarkCell,
    CellError,
    CellErrorType,
    CellPerformance,
)
from pronunciation_lab.benchmark.dataset import preflight
from pronunciation_lab.benchmark.runner import BenchmarkRunner
from pronunciation_lab.benchmark.validate import raw_correspondence_issues, validate_cell


@pytest.fixture
def wav(tmp_path):
    data = make_dataset(tmp_path, ["R01"])
    return data / "benchmark_wav" / "R01.wav"


def good_cell(wav, *, cold=False) -> BenchmarkCell:
    result = make_result(engine="e", recording_id="R01", text="Think about three things.", audio_path=wav, cold=cold)
    result.processing.wall_time_ms = 100.0
    result.processing.inference_ms = 50.0
    result.processing.postprocessing_ms = 20.0
    result.processing.model_load_ms = 10.0 if cold else None
    result.processing.realtime_factor = 100.0 / result.recording.audio.duration_ms
    p = result.processing
    return BenchmarkCell(
        benchmark_run_id="t", cell_id="R01__e", recording_id="R01", engine_id="e",
        input_filename="benchmark_wav/R01.wav", source_filename="Source R01.m4a",
        target_text="Think about three things.", reading_style="Slow", purpose="p",
        status="ok", result=result,
        performance=CellPerformance(
            wall_time_ms=p.wall_time_ms, model_load_ms=p.model_load_ms, inference_ms=p.inference_ms,
            postprocessing_ms=p.postprocessing_ms, realtime_factor=p.realtime_factor,
            run_type=p.run_type, runner_wall_ms=105.0,
        ),
    )


def issues(cell):
    return validate_cell(cell).issues


@pytest.mark.parametrize("cold", [False, True])
def test_good_cell_is_valid(wav, cold):
    assert issues(good_cell(wav, cold=cold)) == []


@pytest.mark.parametrize("mutate, expected", [
    (lambda c: setattr(c.performance, "inference_ms", 90.0), "timing parts exceed wall time"),
    (lambda c: setattr(c.performance, "model_load_ms", 5.0), "model_load_ms present iff cold run violated"),
    (lambda c: setattr(c.performance, "realtime_factor", 0.5), "realtime_factor inconsistent with wall time and duration"),
    (lambda c: setattr(c.performance, "postprocessing_ms", -1.0), "negative postprocessing_ms"),
    (lambda c: setattr(c.performance, "runner_wall_ms", 50.0), "runner wall time shorter than engine wall time"),
    (lambda c: setattr(c.performance, "run_type", None), "missing timing field run_type"),
    (lambda c: setattr(c, "performance", None), "evidence cell without performance"),
    (lambda c: setattr(c, "status", "partial"), "result status 'ok' != cell status 'partial'"),
    (lambda c: setattr(c.result.recording, "id", "R02"), "result recording id 'R02' != 'R01'"),
    (lambda c: setattr(c.result.recording.target, "text", "x"), "result target text differs from the manifest"),
    (lambda c: setattr(c.result.engine, "name", "other"), "result engine 'other' != 'e'"),
    (lambda c: setattr(c.result.engine, "model", None), "local engine result without model identity"),
    (lambda c: setattr(c.result.recording.audio, "analysis_path", "R07.wav"), "result analysis file differs from the cell input file"),
    (lambda c: setattr(c.result, "words", []), "evidence status without word evidence"),
    (lambda c: setattr(c, "result", None), "evidence status without a result"),
    (lambda c: setattr(c, "cell_id", "R02__e"), "cell_id does not match recording/engine"),
])
def test_each_invariant_is_enforced(wav, mutate, expected):
    cell = good_cell(wav)
    mutate(cell)
    assert expected in issues(cell)


def test_partial_needs_an_explanation(wav):
    cell = good_cell(wav)
    cell.status = cell.result.status = "partial"
    assert "partial result without errors explaining it" in issues(cell)


def base(status, **kw):
    return BenchmarkCell(
        benchmark_run_id="t", cell_id="R01__e", recording_id="R01", engine_id="e",
        input_filename="benchmark_wav/R01.wav", source_filename="s.m4a", target_text="x",
        reading_style="s", purpose="p", status=status, **kw,
    )


def test_failed_blocked_unresolved_requirements():
    assert "failed cell without a structured error" in issues(base("failed"))
    assert issues(base("failed", errors=[CellError(type="engine_exception", message="m")])) == []

    assert "blocked cell without a blocked reason" in issues(base("blocked"))
    wrong = base("blocked", errors=[CellError(type=ErrorType.UNRESOLVED_ENGINE, message="m")])
    assert "unresolved engine recorded as blocked" in issues(wrong)
    assert issues(base("blocked", errors=[CellError(type=ErrorType.CREDENTIALS_UNAVAILABLE, message="m")])) == []

    assert "unresolved cell without an unresolved reason" in issues(base("unresolved"))
    assert issues(base("unresolved", availability={"reason": ErrorType.UNRESOLVED_ENGINE})) == []


def test_blocked_cell_with_evidence_is_invalid(wav):
    cell = good_cell(wav)
    cell.status = "blocked"
    cell.errors = [CellError(type=ErrorType.CREDENTIALS_UNAVAILABLE, message="m")]
    assert "blocked cell carries word evidence" in issues(cell)


# ----------------------------------------------------------------------
# Raw evidence correspondence
# ----------------------------------------------------------------------


def raw_cell(wav):
    cell = good_cell(wav)
    cell.result.engine_evidence = {
        "recognition": {"phones": ["a", "b"], "confidences": [0.9, 0.8], "frame_spans": [[0, 1], [2, 3]]},
        "posteriorgram": {"n_frames": 4, "vocab_size": 3},
        "frame_clock": {"n_frames": 4},
    }
    return cell


def raw(**over):
    arrays = {
        "heard_phones": np.array(["a", "b"]),
        "confidences": np.array([0.9, 0.8]),
        "frame_spans": np.array([[0, 1], [2, 3]]),
        "log_posteriors": np.zeros((4, 3), np.float32),
        "vocab": np.array(["<pad>", "a", "b"]),
    }
    return arrays | over


def test_matching_raw_evidence_passes(wav):
    assert raw_correspondence_issues(raw_cell(wav), raw()) == []


@pytest.mark.parametrize("over, expected", [
    ({"heard_phones": np.array(["a", "c"])}, "raw heard phones differ from the result"),
    ({"frame_spans": np.array([[0, 1], [2, 4]])}, "raw frame spans differ from the result"),
    ({"confidences": np.array([0.9, 0.7])}, "raw confidences differ from the result"),
    ({"log_posteriors": np.zeros((5, 3), np.float32)}, "raw posteriorgram frame count differs from the result"),
    ({"vocab": np.array(["a", "b"])}, "raw posteriorgram vocabulary size differs from the result"),
    ({"log_posteriors": np.zeros(4, np.float32)}, "raw log posteriors missing or not 2-D"),
])
def test_raw_mismatch_is_detected(wav, over, expected):
    assert expected in raw_correspondence_issues(raw_cell(wav), raw(**over))


def test_missing_raw_for_a_posteriorgram_engine_is_detected(wav):
    assert raw_correspondence_issues(raw_cell(wav), {}) == [
        "engine reports a posteriorgram but no raw evidence was stored"
    ]


class StaleRawEngine(FakeEngine):
    """Claims a posteriorgram but keeps last_recognition from somewhere else."""

    def analyze(self, audio_path, expected_text, *, recording_id, original_path=None):
        result = super().analyze(audio_path, expected_text, recording_id=recording_id)
        result.engine_evidence |= {
            "recognition": {"phones": ["a"], "confidences": [0.9], "frame_spans": [[0, 1]]},
            "posteriorgram": {"n_frames": 2, "vocab_size": 2},
            "frame_clock": {"n_frames": 2},
        }
        self.last_recognition = SimpleNamespace(
            phones=["z"], confidences=[0.9], spans=[(0, 1)],
            log_posteriors=np.zeros((2, 2)), vocab=("<pad>", "z"),
        )
        return result


def test_runner_refuses_raw_evidence_that_does_not_match(tmp_path):
    data = make_dataset(tmp_path, ["R01"])
    report = preflight(data, expected_ids=("R01",))
    runner = BenchmarkRunner(tmp_path / "run", "t", report.recordings, ["e"], data_dir=data,
                             engine_factory=factory({"e": StaleRawEngine("e")}), known_engines=["e"])
    runner.run(report)
    cell = runner.store.load("R01__e").cell
    assert cell.status == "failed"
    assert cell.raw_evidence is None
    assert cell.errors[-1].type == CellErrorType.RAW_EVIDENCE_MISMATCH
    assert "heard phones" in cell.errors[-1].message
