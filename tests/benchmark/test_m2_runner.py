"""Benchmark runner: classification, isolation, resumability and storage integrity."""

import json
import os

import pytest
from benchmark_fakes import FakeEngine, FakeUnavailableEngine, factory, make_dataset

from pronunciation_lab.benchmark import cells as cells_module
from pronunciation_lab.benchmark.base import ErrorType
from pronunciation_lab.benchmark.cells import CellErrorType, CellStore, cell_id
from pronunciation_lab.benchmark.dataset import preflight
from pronunciation_lab.benchmark.runner import BenchmarkRunner
from pronunciation_lab.benchmark.validate import validate_run

IDS = ["R01", "R02", "R03", "R05"]


@pytest.fixture
def data(tmp_path):
    return make_dataset(tmp_path, IDS)


@pytest.fixture
def report(data):
    r = preflight(data, expected_ids=tuple(IDS), check_sources=True)
    assert r.ok, r.problems
    return r


def make_runner(tmp_path, report, engines, run_id="t1"):
    return BenchmarkRunner(
        tmp_path / "runs" / run_id, run_id, report.recordings, list(engines),
        data_dir=report.data_dir, engine_factory=factory(engines), known_engines=list(engines),
    )


def statuses(runner):
    store = runner.store
    return {
        cid: (store.load(cid).cell.status if store.load(cid).cell else store.load(cid).state)
        for cid in (cell_id(r.recording_id, e) for e in runner.engine_ids for r in runner.recordings)
    }


def check(runner):
    return validate_run(runner.run_dir, runner.recordings, runner.engine_ids, runner.engine_ids)


# ----------------------------------------------------------------------
# Classification
# ----------------------------------------------------------------------


def test_full_matrix_with_every_state(tmp_path, report):
    engines = {
        "good": FakeEngine("good", plan={"R03": "partial"}),
        "broken": FakeEngine("broken", plan={"R02": RuntimeError("boom")}),
        "cloud": FakeUnavailableEngine("cloud", "blocked"),
        "nomodel": FakeUnavailableEngine("nomodel", "unresolved"),
    }
    runner = make_runner(tmp_path, report, engines)
    result = runner.run(report)

    s = statuses(runner)
    assert s["R01__good"] == "ok" and s["R03__good"] == "partial"
    assert s["R02__broken"] == "failed"
    assert {s[f"{r}__broken"] for r in ("R01", "R03", "R05")} == {"ok"}  # failure isolated
    assert {s[f"{r}__cloud"] for r in IDS} == {"blocked"}
    assert {s[f"{r}__nomodel"] for r in IDS} == {"unresolved"}
    assert engines["cloud"].calls == [] and engines["nomodel"].calls == []  # never executed

    assert len(result.executed) == 8 and len(result.recorded_without_execution) == 8
    v = check(runner)
    assert v["ok"], v["issues"]
    assert v["counts"] == {"ok": 6, "partial": 1, "failed": 1, "blocked": 4, "unresolved": 4, "missing": 0, "corrupt": 0}

    failed = runner.store.load("R02__broken").cell
    assert failed.errors[0].type == ErrorType.ENGINE_EXCEPTION and "boom" in failed.errors[0].message
    blocked = runner.store.load("R01__cloud").cell
    assert blocked.errors[0].type == ErrorType.CREDENTIALS_UNAVAILABLE and blocked.errors[0].retryable is True
    unresolved = runner.store.load("R01__nomodel").cell
    assert unresolved.availability["reason"] == ErrorType.UNRESOLVED_ENGINE
    assert unresolved.errors[0].retryable is False


def test_unknown_engine_is_rejected_before_anything_runs(tmp_path, report):
    with pytest.raises(KeyError):
        BenchmarkRunner(tmp_path / "r", "x", report.recordings, ["nope"], data_dir=report.data_dir,
                        engine_factory=factory({}), known_engines=["good"])
    assert not (tmp_path / "r").exists()


def test_cli_rejects_unknown_engine(tmp_path, data):
    from pronunciation_lab.benchmark.cli import main

    assert main(["--data-dir", str(data), "--results-dir", str(tmp_path), "run", "--run-id", "x", "--engines", "nope"]) == 2
    assert not (tmp_path / "x").exists()


def test_engine_that_cannot_be_constructed_fails_its_cells(tmp_path, report):
    def broken_factory(name):
        raise ImportError("missing dependency")

    runner = BenchmarkRunner(tmp_path / "r", "x", report.recordings, ["e"], data_dir=report.data_dir,
                             engine_factory=broken_factory, known_engines=["e"])
    runner.run(report)
    cell = runner.store.load("R01__e").cell
    assert cell.status == "failed"
    assert cell.errors[0].type == CellErrorType.ENGINE_CONSTRUCTION_FAILED


def test_malformed_engine_result(tmp_path, report):
    runner = make_runner(tmp_path, report, {"e": FakeEngine("e", plan={"R01": "malformed"})})
    runner.run(report)
    cell = runner.store.load("R01__e").cell
    assert cell.status == "failed" and cell.result is None
    assert cell.errors[0].type == CellErrorType.MALFORMED_ENGINE_RESULT
    assert runner.store.load("R02__e").cell.status == "ok"


def test_result_describing_another_recording_is_rejected(tmp_path, report):
    runner = make_runner(tmp_path, report, {"e": FakeEngine("e", plan={"R01": "wrong_metadata"})})
    runner.run(report)
    cell = runner.store.load("R01__e").cell
    assert cell.status == "failed"
    assert cell.errors[-1].type == CellErrorType.RESULT_METADATA_MISMATCH
    assert "R99" in cell.errors[-1].message


def test_missing_timing_fields_are_flagged_invalid(tmp_path, report):
    runner = make_runner(tmp_path, report, {"e": FakeEngine("e", plan={"R01": "no_timing"})})
    runner.run(report)
    cell = runner.store.load("R01__e").cell
    assert cell.validation.status == "invalid"
    assert "missing timing field wall_time_ms" in cell.validation.issues
    v = check(runner)
    assert not v["ok"]
    assert any(i["code"] == "cell_invalid" and i["cell_id"] == "R01__e" for i in v["issues"])


# ----------------------------------------------------------------------
# Resumability
# ----------------------------------------------------------------------


def test_completed_cells_are_skipped(tmp_path, report):
    engine = FakeEngine("e")
    runner = make_runner(tmp_path, report, {"e": engine})
    runner.run(report)
    assert engine.calls == IDS

    again = make_runner(tmp_path, report, {"e": engine}).run(report)
    assert engine.calls == IDS  # nothing recomputed
    assert again.executed == [] and len(again.skipped) == 4


def test_resume_after_interruption(tmp_path, report):
    """Ctrl-C on the third recording: two cells survive, the rest run on resume."""
    calls = []
    first = FakeEngine("e", plan={"R03": KeyboardInterrupt()}, calls=calls)
    with pytest.raises(KeyboardInterrupt):
        make_runner(tmp_path, report, {"e": first}).run(report)

    runner = make_runner(tmp_path, report, {"e": FakeEngine("e", calls=calls)})
    assert statuses(runner) == {"R01__e": "ok", "R02__e": "ok", "R03__e": "missing", "R05__e": "missing"}
    assert not list(runner.store.cells_dir.glob("*.tmp")) and not list(runner.store.cells_dir.glob(".*.tmp"))

    runner.run(report)
    assert calls == ["R01", "R02", "R03", "R03", "R05"]  # R01/R02 not recomputed
    assert check(runner)["ok"]


def test_failed_cells_rerun_only_when_asked(tmp_path, report):
    flaky = {"n": 0}

    def fail_once(engine, rid):
        flaky["n"] += 1
        return RuntimeError("transient") if flaky["n"] == 1 else "ok"

    calls = []
    runner = make_runner(tmp_path, report, {"e": FakeEngine("e", plan={"R02": fail_once}, calls=calls)})
    runner.run(report)
    assert runner.store.load("R02__e").cell.status == "failed"

    runner.run(report)  # plain resume leaves the failed cell alone
    assert calls.count("R02") == 1

    runner.run(report, retry_failed=True)
    cell = runner.store.load("R02__e").cell
    assert cell.status == "ok" and cell.attempt == 2
    assert calls.count("R02") == 2 and calls.count("R01") == 1


def test_force_reruns_everything(tmp_path, report):
    calls = []
    runner = make_runner(tmp_path, report, {"e": FakeEngine("e", calls=calls)})
    runner.run(report)
    runner.run(report, force=True)
    assert calls == IDS + IDS
    assert runner.store.load("R01__e").cell.attempt == 2


def test_blocked_cells_rerun_when_the_engine_becomes_available(tmp_path, report):
    blocked = FakeUnavailableEngine("e", "blocked")
    runner = make_runner(tmp_path, report, {"e": blocked})
    runner.run(report)
    assert set(statuses(runner).values()) == {"blocked"}

    now_ok = FakeEngine("e")
    make_runner(tmp_path, report, {"e": now_ok}).run(report)
    assert now_ok.calls == IDS
    assert set(statuses(runner).values()) == {"ok"}


def test_still_blocked_cells_are_not_rewritten(tmp_path, report):
    runner = make_runner(tmp_path, report, {"e": FakeUnavailableEngine("e", "blocked")})
    runner.run(report)
    before = runner.store.cell_path("R01__e").read_bytes()
    result = runner.run(report)
    assert result.recorded_without_execution == []
    assert runner.store.cell_path("R01__e").read_bytes() == before


def test_resuming_with_changed_source_data_is_refused(tmp_path, report, data):
    runner = make_runner(tmp_path, report, {"e": FakeEngine("e")})
    runner.run(report)
    rows = (data / "benchmark_manifest.csv").read_text().replace("Very few people.", "Very few people!")
    (data / "benchmark_manifest.csv").write_text(rows)
    changed = preflight(data, expected_ids=tuple(IDS))
    with pytest.raises(ValueError, match="source data changed"):
        make_runner(tmp_path, changed, {"e": FakeEngine("e")}).run(changed)


def test_a_different_run_in_the_same_directory_is_refused(tmp_path, report):
    make_runner(tmp_path, report, {"e": FakeEngine("e")}, run_id="a").run(report)
    other = BenchmarkRunner(tmp_path / "runs" / "a", "b", report.recordings, ["e"], data_dir=report.data_dir,
                            engine_factory=factory({"e": FakeEngine("e")}), known_engines=["e"])
    with pytest.raises(ValueError, match="belongs to run a"):
        other.run(report)


# ----------------------------------------------------------------------
# Storage integrity
# ----------------------------------------------------------------------


def test_interrupted_write_leaves_no_false_success(tmp_path, report, monkeypatch):
    runner = make_runner(tmp_path, report, {"e": FakeEngine("e")})
    real_replace = os.replace

    def crash_on_r02(src, dst):
        if str(dst).endswith("R02__e.json"):
            raise OSError("disk full during rename")
        return real_replace(src, dst)

    monkeypatch.setattr(cells_module.os, "replace", crash_on_r02)
    with pytest.raises(OSError):
        runner.run(report)
    monkeypatch.setattr(cells_module.os, "replace", real_replace)

    assert runner.store.load("R02__e").state == "missing"
    assert not [p for p in runner.store.cells_dir.iterdir() if p.name.endswith(".tmp")]
    runner.run(report)
    assert check(runner)["ok"]


@pytest.mark.parametrize(
    "damage, reason",
    [
        (lambda p: p.write_text(p.read_text()[: len(p.read_text()) // 2]), "unparseable"),
        (lambda p: p.write_text("[]"), "not a cell envelope"),
        (lambda p: p.write_text(p.read_text().replace('"status": "ok"', '"status": "okay"')), "schema"),
        (lambda p: p.write_text(p.read_text().replace('"device": "cpu"', '"device": "gpu"', 1)), "checksum"),
    ],
)
def test_corrupt_cells_are_never_complete_and_are_rerun(tmp_path, report, damage, reason):
    calls = []
    runner = make_runner(tmp_path, report, {"e": FakeEngine("e", calls=calls)})
    runner.run(report)
    damage(runner.store.cell_path("R01__e"))

    loaded = runner.store.load("R01__e")
    assert loaded.state == "corrupt" and reason in loaded.reason
    v = check(runner)
    assert any(i["code"] == "cell_corrupt" for i in v["issues"])

    runner.run(report)
    assert calls.count("R01") == 2
    assert runner.store.load("R01__e").state == "complete"


def test_cell_under_the_wrong_name_is_corrupt(tmp_path, report):
    runner = make_runner(tmp_path, report, {"e": FakeEngine("e")})
    runner.run(report)
    path = runner.store.cell_path("R01__e")
    path.write_bytes(runner.store.cell_path("R02__e").read_bytes())
    assert runner.store.load("R01__e").state == "corrupt"


def test_duplicate_and_stray_files_are_reported(tmp_path, report):
    runner = make_runner(tmp_path, report, {"e": FakeEngine("e")})
    runner.run(report)
    cells_dir = runner.store.cells_dir
    (cells_dir / "R01__e copy.json").write_bytes(runner.store.cell_path("R01__e").read_bytes())
    (cells_dir / ".R02__e.json.123.abc.tmp").write_text("{")
    (cells_dir / "R04__e.json").write_bytes(runner.store.cell_path("R01__e").read_bytes())

    codes = {(i["code"], i["cell_id"]) for i in check(runner)["issues"]}
    assert ("stray_file", None) in codes
    assert ("temp_file_left", None) in codes
    assert ("cell_outside_matrix", "R04__e") in codes


def test_cells_record_the_run_and_manifest_fields(tmp_path, report):
    runner = make_runner(tmp_path, report, {"e": FakeEngine("e")})
    runner.run(report)
    cell = runner.store.load("R05__e").cell
    rec = next(r for r in report.recordings if r.recording_id == "R05")
    assert (cell.benchmark_run_id, cell.recording_id, cell.engine_id) == ("t1", "R05", "e")
    assert (cell.target_text, cell.reading_style, cell.purpose, cell.source_filename) == (
        rec.target_text, rec.reading_style, rec.purpose, rec.filename,
    )
    assert cell.input_filename == "benchmark_wav/R05.wav"
    assert cell.text_group == "very_few_people"
    assert cell.engine_model == "fake/model@1" and cell.engine_version == "1.0"
    assert cell.started_at <= cell.finished_at
    p = cell.performance
    assert p.runner_wall_ms >= 0 and p.run_type == "warm" and p.model_load_ms is None

    metadata = json.loads((runner.run_dir / "run_metadata.json").read_text())
    assert metadata["engine_identity"]["e"]["model_id"] == "fake/model@1"
    assert metadata["environment"]["packages"]["torch"]
    assert len(metadata["sessions"]) == 1 and metadata["sessions"][0]["executed"] == [f"{r}__e" for r in IDS]
    snapshot = (runner.run_dir / "manifest_snapshot.csv").read_text()
    assert rec.target_text in snapshot


def test_source_modification_is_detected_by_validation(tmp_path, report, data):
    runner = make_runner(tmp_path, report, {"e": FakeEngine("e")})
    runner.run(report)
    with (data / "benchmark_wav" / "R02.wav").open("r+b") as f:
        f.seek(100)
        f.write(b"\x01\x02")
    issues = {(i["code"], i["message"].split()[0]) for i in check(runner)["issues"]}
    assert ("source_data_modified", "benchmark_wav/R02.wav") in issues


def test_cell_text_differing_from_manifest_is_detected(tmp_path, report):
    runner = make_runner(tmp_path, report, {"e": FakeEngine("e")})
    runner.run(report)
    store = CellStore(runner.run_dir)
    cell = store.load("R01__e").cell
    cell.target_text = "Something else."
    store.write(cell)  # a validly written cell with the wrong text
    codes = {i["code"] for i in check(runner)["issues"] if i["cell_id"] == "R01__e"}
    assert "target_text_mismatch" in codes and "cell_invalid" in codes


class _RawEngine(FakeEngine):
    """A fake engine that keeps a small, consistent posteriorgram."""

    def analyze(self, audio_path, expected_text, *, recording_id, original_path=None):
        import numpy as np
        from types import SimpleNamespace

        result = super().analyze(audio_path, expected_text, recording_id=recording_id)
        result.engine_evidence |= {
            "recognition": {"phones": ["a"], "confidences": [0.9], "frame_spans": [[0, 1]]},
            "posteriorgram": {"n_frames": 2, "vocab_size": 2},
            "frame_clock": {"n_frames": 2},
        }
        self.last_recognition = SimpleNamespace(
            phones=["a"], confidences=[0.9], spans=[(0, 1)],
            log_posteriors=np.log(np.full((2, 2), 0.5)), vocab=("<pad>", "a"),
        )
        return result


@pytest.mark.parametrize("damage, reason", [
    (lambda p: p.write_bytes(p.read_bytes()[:-7] + b"tamper!"), "raw evidence checksum mismatch"),
    (lambda p: p.unlink(), "raw evidence file missing"),
])
def test_tampered_or_missing_raw_evidence_makes_the_cell_corrupt(tmp_path, report, damage, reason):
    calls = []
    runner = make_runner(tmp_path, report, {"e": _RawEngine("e", calls=calls)})
    runner.run(report)
    cell = runner.store.load("R01__e").cell
    assert cell.status == "ok" and cell.raw_evidence is not None
    assert runner.store.load_raw(cell)["heard_phones"].tolist() == ["a"]

    damage(runner.run_dir / cell.raw_evidence.path)
    loaded = runner.store.load("R01__e")
    assert loaded.state == "corrupt" and loaded.reason == reason

    runner.run(report)  # resumed: the corrupt cell is recomputed, with fresh raw evidence
    assert calls.count("R01") == 2
    assert runner.store.load("R01__e").state == "complete"
