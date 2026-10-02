"""AnalysisService: engines, text, analyses, retention, notes, lifecycle."""

import json

import pytest
from benchmark_fakes import FakeEngine, factory
from apphelpers import wav_bytes

from pronunciation_lab.app.service import AnalysisService, UserError, validate_text


@pytest.mark.parametrize("text, code", [
    ("", "text_empty"), ("   ", "text_empty"), (None, "text_empty"),
    ("1234 5678", "text_no_words"), ("?!.,", "text_no_words"), ("x" * 301, "text_too_long"),
])
def test_invalid_text(text, code):
    with pytest.raises(UserError) as e:
        validate_text(text)
    assert e.value.code == code


def test_valid_text_is_trimmed():
    assert validate_text("  Think about it.  ") == "Think about it."
    assert validate_text("x" * 300) == "x" * 300


def test_engine_status_lists_every_state(fake_service):
    states = {e["id"]: e["state"] for e in fake_service.status()["engines"]}
    assert states == {"wav2vec2_raw": "runnable", "openpronounce": "runnable",
                      "wavlm": "unresolved", "azure_pronunciation": "blocked"}
    assert fake_service.status()["default_engine"] == "wav2vec2_raw"


@pytest.mark.parametrize("engine, code, status", [
    ("wavlm", "engine_unavailable", 409), ("azure_pronunciation", "engine_unavailable", 409), ("nope", "engine_unknown", 400),
])
def test_unusable_engines_are_refused_without_running(fake_service, fake_engines, tmp_path, engine, code, status):
    with pytest.raises(UserError) as e:
        fake_service.analyze_upload(wav_bytes(1, tmp=tmp_path), "a.wav", "Think.", engine)
    assert (e.value.code, e.value.status) == (code, status)
    for eng in fake_engines.values():
        assert eng.calls == []


def test_no_runnable_engine(tmp_path):
    from benchmark_fakes import FakeUnavailableEngine

    svc = AnalysisService(engine_factory=factory({"x": FakeUnavailableEngine("x", "blocked")}), engine_ids=["x"])
    try:
        assert svc.status()["default_engine"] is None
        with pytest.raises(UserError) as e:
            svc.analyze_upload(wav_bytes(1, tmp=tmp_path), "a.wav", "Think.", None)
        assert e.value.code == "engine_unknown"
    finally:
        svc.close()


def test_text_is_validated_before_audio_or_engine(fake_service, fake_engines):
    with pytest.raises(UserError) as e:
        fake_service.analyze_upload(b"", "a.wav", "", None)
    assert e.value.code == "text_empty"
    assert fake_engines["wav2vec2_raw"].calls == []


def test_bad_audio_never_reaches_the_engine(fake_service, fake_engines, tmp_path):
    with pytest.raises(UserError) as e:
        fake_service.analyze_upload(wav_bytes(0.1, tmp=tmp_path), "a.wav", "Think.", None)
    assert e.value.code == "audio_too_short"
    assert fake_engines["wav2vec2_raw"].calls == []
    assert list(fake_service.workspace.iterdir()) == []  # the failed upload left nothing behind


def test_analysis_is_stored_and_retrievable(fake_service, tmp_path):
    a = fake_service.analyze_upload(wav_bytes(1, tmp=tmp_path), "a.wav", "Think about three.", None)
    assert fake_service.get(a.id) is a
    assert a.view["analysis_id"] == a.id and a.view["state"] == "ok"
    assert a.view["audio"]["url"] == f"/api/analyses/{a.id}/audio"
    assert json.loads((a.workdir / "result.json").read_text())["recording"]["target"]["text"] == "Think about three."
    assert fake_service.recent()[0]["analysis_id"] == a.id


def test_engine_exception_becomes_a_failed_view(tmp_path):
    engines = {"wav2vec2_raw": FakeEngine("wav2vec2_raw", plan={})}
    engines["wav2vec2_raw"].analyze = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    svc = AnalysisService(engine_factory=factory(engines), engine_ids=list(engines))
    try:
        a = svc.analyze_upload(wav_bytes(1, tmp=tmp_path), "a.wav", "Think.", None)
        assert a.view["state"] == "failed" and a.view["error"]["code"] == "engine_exception"
        assert "boom" in a.view["error"]["detail"]
    finally:
        svc.close()


@pytest.mark.parametrize("bad_id", ["", "abc", "../../etc", "0" * 32, "Z" * 32])
def test_unknown_analysis_ids(fake_service, bad_id):
    with pytest.raises(UserError) as e:
        fake_service.get(bad_id)
    assert (e.value.code, e.value.status) == ("analysis_unknown", 404)


def test_old_analyses_are_evicted_and_deleted(tmp_path, fake_engines):
    svc = AnalysisService(engine_factory=factory(fake_engines), engine_ids=list(fake_engines), max_analyses=3)
    try:
        made = [svc.analyze_upload(wav_bytes(0.5, tmp=tmp_path), "a.wav", f"Word {i}.", None) for i in range(5)]
        assert [a["analysis_id"] for a in svc.recent()] == [m.id for m in reversed(made[2:])]
        for old in made[:2]:
            assert not old.workdir.exists()
            with pytest.raises(UserError):
                svc.get(old.id)
        assert all(m.workdir.exists() for m in made[2:])
    finally:
        svc.close()


def test_close_removes_the_workspace(fake_engines, tmp_path):
    svc = AnalysisService(engine_factory=factory(fake_engines), engine_ids=list(fake_engines))
    svc.analyze_upload(wav_bytes(0.5, tmp=tmp_path), "a.wav", "Hi there.", None)
    ws = svc.workspace
    assert ws.exists()
    svc.close()
    assert not ws.exists()


def test_given_workspace_is_not_deleted(fake_engines, tmp_path):
    ws = tmp_path / "keep"
    svc = AnalysisService(engine_factory=factory(fake_engines), engine_ids=list(fake_engines), workspace=ws)
    svc.close()
    assert ws.exists()


# ----------------------------------------------------------------------
# Listening notes
# ----------------------------------------------------------------------


@pytest.fixture
def analysis(fake_service, tmp_path):
    return fake_service.analyze_upload(wav_bytes(1, tmp=tmp_path), "a.wav", "Think about three.", None)


def test_note_is_persisted_with_references_not_audio(fake_service, analysis):
    note = fake_service.add_note(analysis.id, 0, "as_heard", "  sounds like a b  ")
    assert note["verdict"] == "as_heard" and note["comment"] == "sounds like a b"
    assert note["audio_sha256"] == analysis.audio.original_sha256
    assert note["expected"] == "a" and note["word"] == "think" and note["span_ms"] == [0.0, 20.0]
    lines = fake_service.notes_path.read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0]) == note
    assert fake_service.notes_for(analysis.id) == [note]


@pytest.mark.parametrize("index, verdict, code", [
    (99, "as_heard", "sound_unknown"), ("0", "as_heard", "sound_unknown"), (True, "as_heard", "sound_unknown"),
    (None, "as_heard", "sound_unknown"), (0, "great", "verdict_invalid"), (0, "", "verdict_invalid"),
])
def test_invalid_notes(fake_service, analysis, index, verdict, code):
    with pytest.raises(UserError) as e:
        fake_service.add_note(analysis.id, index, verdict)
    assert e.value.code == code
    assert not fake_service.notes_path.exists()


def test_note_for_unknown_analysis(fake_service):
    with pytest.raises(UserError) as e:
        fake_service.add_note("0" * 32, 0, "as_heard")
    assert e.value.code == "analysis_unknown"


def test_notes_disabled(fake_engines, tmp_path):
    svc = AnalysisService(engine_factory=factory(fake_engines), engine_ids=list(fake_engines))
    try:
        a = svc.analyze_upload(wav_bytes(1, tmp=tmp_path), "a.wav", "Think.", None)
        with pytest.raises(UserError) as e:
            svc.add_note(a.id, 0, "as_heard")
        assert e.value.code == "notes_disabled"
        assert svc.notes_for(a.id) == []
    finally:
        svc.close()


def test_notes_survive_a_restart_and_tolerate_a_torn_line(fake_service, analysis, fake_engines, tmp_path):
    fake_service.add_note(analysis.id, 0, "cannot_tell")
    with fake_service.notes_path.open("a") as f:
        f.write('{"analysis_id": "torn')  # simulated crash mid-write
    assert [n["verdict"] for n in fake_service.notes_for(analysis.id)] == ["cannot_tell"]
    assert len(fake_service.notes_path.read_text().splitlines()) == 2


# ----------------------------------------------------------------------
# Benchmark recordings
# ----------------------------------------------------------------------


def test_benchmark_list_only_includes_present_recordings(tmp_path, fake_engines):
    from benchmark_fakes import make_dataset

    data = make_dataset(tmp_path, ["R01", "R02"])
    (data / "benchmark_wav" / "R02.wav").unlink()
    svc = AnalysisService(data_dir=data, engine_factory=factory(fake_engines), engine_ids=list(fake_engines))
    try:
        assert [r["id"] for r in svc.benchmark_recordings()] == ["R01"]
        a = svc.analyze_benchmark("R01", None)
        assert a.target_text == "Think about three things." and a.audio.conversion == "copied"
        with pytest.raises(UserError) as e:
            svc.analyze_benchmark("R02", None)
        assert e.value.status == 404
    finally:
        svc.close()


def test_no_data_dir_means_no_benchmark(fake_service):
    assert fake_service.benchmark_recordings() == []
    with pytest.raises(UserError):
        fake_service.analyze_benchmark("R01", None)


def test_malformed_manifest_is_ignored(tmp_path, fake_engines):
    data = tmp_path / "data"
    data.mkdir()
    (data / "benchmark_manifest.csv").write_bytes(b"\xff\xfe\x00 not csv")
    svc = AnalysisService(data_dir=data, engine_factory=factory(fake_engines), engine_ids=list(fake_engines))
    try:
        assert svc.benchmark_recordings() == []
    finally:
        svc.close()


def test_workspaces_of_dead_processes_are_removed_at_startup(tmp_path, fake_engines, monkeypatch):
    import os
    import tempfile

    from pronunciation_lab.app import service as S

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    dead = tmp_path / f"{S.WORKSPACE_PREFIX}999999-abc"   # no such process
    alive = tmp_path / f"{S.WORKSPACE_PREFIX}{os.getppid()}-def"  # a live process
    foreign = tmp_path / f"{S.WORKSPACE_PREFIX}old-format"  # not ours to judge
    for d in (dead, alive, foreign):
        (d / "x").mkdir(parents=True)
        (d / "x" / "original.wav").write_bytes(b"audio")
    monkeypatch.setattr(S, "_pid_alive", lambda pid: pid != 999999)

    svc = AnalysisService(engine_factory=factory(fake_engines), engine_ids=list(fake_engines))
    try:
        assert svc.stale_workspaces_removed == [dead]
        assert not dead.exists() and alive.exists() and foreign.exists()
        assert svc.workspace.name.startswith(f"{S.WORKSPACE_PREFIX}{os.getpid()}-")
    finally:
        svc.close()


def test_pid_alive():
    import os

    from pronunciation_lab.app.service import _pid_alive

    assert _pid_alive(os.getpid())
    assert not _pid_alive(999999)
