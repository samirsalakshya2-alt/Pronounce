"""M7 phases 4–5: the reader analyses only the sentence's region; continued speech is kept, stored and playable."""

import hashlib
import io
import json
import wave

import m7helpers as H
import pytest
from benchmark_fakes import FakeUnavailableEngine, factory
from readerhelpers import RUN, capture

from pronunciation_lab.app.service import AnalysisService
from pronunciation_lab.reader import boundary as B
from pronunciation_lab.reader import model as M
from pronunciation_lab.reader import service as reader_service
from pronunciation_lab.reader.service import ReaderService
from pronunciation_lab.reader.store import ReaderStore
from pronunciation_lab.reader.summary import validate_summary

TEXT = "Think about change. The next thing is this."


@pytest.fixture
def m7engines():
    return {"wav2vec2_raw": H.BoundaryEngine("wav2vec2_raw"), "openpronounce": H.BoundaryEngine("openpronounce"),
            "wavlm": FakeUnavailableEngine("wavlm", "unresolved"),
            "azure_pronunciation": FakeUnavailableEngine("azure_pronunciation", "blocked")}


@pytest.fixture
def m7reader(tmp_path, m7engines):
    lab = AnalysisService(notes_path=tmp_path / "notes.jsonl", engine_factory=factory(m7engines),
                          engine_ids=list(m7engines), workspace=tmp_path / "ws")
    r = ReaderService(ReaderStore(tmp_path / "store"), lab)
    yield r
    r.close()
    lab.close()


def read_attempt(reader, continued=True, segment=0):
    art = reader.create_article(TEXT, "M7")
    sid = M.new_id()
    reader.create_session(sid, art["id"])
    seg = art["segments"][segment]
    aid = M.new_id()
    data = H.wav16(H.attempt_audio(continued))
    n = (len(data) - 44) // 2
    reader.start_attempt(sid, aid, {"segment_id": seg["id"], "run_id": RUN, "sample_rate": 16000, "start_sample": 0})
    reader.upload_audio(sid, aid, data, capture(seg["id"], 0, n, rate=16000))
    assert reader.worker.wait_idle(20)
    detail = reader.attempt_detail(sid, aid)
    return sid, aid, art, detail, data


def primary(detail):
    return next(j for j in detail["jobs"] if j["kind"] == "primary")


def files(reader, job):
    return {n: reader.store.job_file(job, n) for n in reader.store.JOB_FILES} | {"result.json": reader.store.result_path(job)}


# ----------------------------------------------------------------------

def test_sentence_only_runs_one_inference_and_keeps_the_whole_attempt(m7reader, m7engines):
    sid, aid, _, detail, _ = read_attempt(m7reader, continued=False)
    job = primary(detail)
    assert job["state"] == "SUCCEEDED" and job["boundary"]["state"] == "TARGET_ONLY"
    assert len(m7engines["wav2vec2_raw"].calls) == 1  # no second inference
    f = files(m7reader, job)
    assert f["boundary.json"].is_file() and not f["target.wav"].exists() and not f["target_result.json"].exists()
    view = detail["views"][job["id"]]
    assert view["boundary"]["regions"][0]["kind"] == "target" and len(view["boundary"]["regions"]) == 1
    assert view["duration_ms"] == pytest.approx(view["boundary"]["duration_ms"], abs=1)


def test_continued_speech_is_kept_out_of_the_analysis(m7reader, m7engines):
    sid, aid, _, detail, data = read_attempt(m7reader)
    job = primary(detail)
    b = job["boundary"]
    assert job["state"] == "SUCCEEDED" and b["state"] == "TARGET_PLUS_OVERFLOW" and b["target_analysed"]
    calls = m7engines["wav2vec2_raw"].calls
    assert len(calls) == 2 and calls[1][0].endswith("-t") and calls[1][1] == pytest.approx(b["cut_ms"], abs=0.1)
    # the full-attempt evidence is kept; the view is the sentence's own analysis
    full = json.loads(files(m7reader, job)["result.json"].read_text())
    tres = json.loads(files(m7reader, job)["target_result.json"].read_text())
    assert len(full["engine_evidence"]["recognition"]["phones"]) == 22
    assert len(tres["engine_evidence"]["recognition"]["phones"]) == 12
    view = detail["views"][job["id"]]
    final = [p for p in tres["words"][-1]["phonemes"]]
    assert all(not p["engine_evidence"]["extra_heard_phones"] for p in final)
    assert view["duration_ms"] == pytest.approx(b["cut_ms"], abs=1)
    for o in view["coach"]["observations"]:  # nothing M4 says lies in the continued speech
        assert o["span_ms"][1] <= b["cut_ms"] + 1
    for c in view["reduction"]["candidates"]:
        assert c["where"]["span_ms"][1] <= b["cut_ms"] + 1
    assert job["target_confirmation"]["state"] == "MATCH"


def test_regions_and_playback_are_exact(m7reader):
    sid, aid, _, detail, data = read_attempt(m7reader)
    job = primary(detail)
    regions = job["boundary"]["regions"]
    assert [r["kind"] for r in regions] == ["target", "overflow"]
    n = (len(data) - 44) // 2
    assert regions[0]["capture_samples"][0] == 0 and regions[-1]["capture_samples"][1] == n
    assert regions[0]["capture_samples"][1] == regions[1]["capture_samples"][0]
    for r in regions:
        ref = r["play"]
        assert (ref["session_id"], ref["attempt_id"], ref["job_id"], ref["timeline"], ref["kind"]) == \
               (sid, aid, job["id"], M.PLAYBACK_TIMELINE, r["kind"])
        assert ref["play_ms"] == [r["start_ms"], r["end_ms"]]
        assert ref["url"].endswith(f"/attempts/{aid}/audio")


def test_original_audio_is_immutable_and_target_wav_is_its_exact_prefix(m7reader):
    sid, aid, _, detail, data = read_attempt(m7reader)
    job = primary(detail)
    original = m7reader.store.audio_path(sid, aid, "original.wav").read_bytes()
    analysis = m7reader.store.audio_path(sid, aid, "analysis.wav").read_bytes()
    assert hashlib.sha256(original).hexdigest() == hashlib.sha256(data).hexdigest()
    target = files(m7reader, job)["target.wav"].read_bytes()
    with wave.open(io.BytesIO(target)) as t, wave.open(io.BytesIO(analysis)) as a:
        n = t.getnframes()
        assert n == round(job["boundary"]["cut_ms"] * 16) and t.readframes(n) == a.readframes(n)


def test_boundary_files_are_write_once(m7reader):
    sid, aid, _, detail, _ = read_attempt(m7reader)
    job = primary(detail)
    from pronunciation_lab.reader.store import AlreadyExists

    for name in m7reader.store.JOB_FILES:
        with pytest.raises(AlreadyExists):
            m7reader.store.write_job_file(job, name, b"x")
    with pytest.raises(ValueError):
        m7reader.store.write_job_file(job, "overflow.wav", b"x")
    stored = json.loads(files(m7reader, job)["boundary.json"].read_text())
    assert B.validate_boundary(stored) == [] and stored["state"] == job["boundary"]["state"]


def test_rerun_after_recovery_reuses_identical_files(m7reader):
    sid, aid, _, detail, _ = read_attempt(m7reader)
    job = primary(detail)
    before = {n: p.read_bytes() for n, p in files(m7reader, job).items()}
    assert m7reader._write_job_file(job, "boundary.json", before["boundary.json"]) == files(m7reader, job)["boundary.json"]
    from pronunciation_lab.reader.store import AlreadyExists

    with pytest.raises(AlreadyExists):
        m7reader._write_job_file(job, "boundary.json", before["boundary.json"] + b" ")


def test_rebuilt_view_equals_the_stored_view(m7reader):
    sid, aid, _, detail, _ = read_attempt(m7reader)
    job = primary(detail)
    stored = detail["views"][job["id"]]
    rebuilt = m7reader.rebuild_view(job)
    for key in ("coach", "reduction", "boundary", "duration_ms"):
        a, b = stored[key], rebuilt[key]
        if isinstance(a, dict):
            a, b = {k: v for k, v in a.items() if k != "timing_ms"}, {k: v for k, v in b.items() if k != "timing_ms"}
        assert a == b, key


def test_failure_of_the_sentence_region_analysis_fails_the_job_never_shows_contaminated_feedback(m7reader, m7engines):
    m7engines["wav2vec2_raw"].fail_on = "-t"
    sid, aid, _, detail, _ = read_attempt(m7reader)
    job = primary(detail)
    assert job["state"] == "FAILED" and job["feedback"] is None and detail["attempt"]["state"] == "ANALYSIS_FAILED"
    assert job["id"] not in detail["views"]
    m7engines["wav2vec2_raw"].fail_on = None
    retry = m7reader.retry(sid, aid, job["id"])
    assert m7reader.worker.wait_idle(20)
    again = m7reader.store.load_job(sid, aid, retry["id"])
    assert again["state"] == "SUCCEEDED" and again["boundary"]["state"] == "TARGET_PLUS_OVERFLOW"


def test_a_failed_result_for_the_sentence_region_fails_the_job(m7reader, m7engines):
    m7engines["wav2vec2_raw"].failed_result_on = "-t"  # the engine answers, but without evidence
    sid, aid, _, detail, _ = read_attempt(m7reader)
    job = primary(detail)
    assert job["state"] == "FAILED" and job["feedback"] is None and job["id"] not in detail["views"]
    assert job["error"]["code"] == "engine_failed"
    # the evidence that was produced is kept (write-once), nothing is shown from it
    assert files(m7reader, job)["target_result.json"].is_file() and files(m7reader, job)["result.json"].is_file()


def test_comparison_uses_the_same_region_and_keeps_its_own_assessment(m7reader, m7engines):
    sid, aid, _, detail, _ = read_attempt(m7reader)
    m7reader.request_comparison(sid, aid)
    assert m7reader.worker.wait_idle(20)
    detail = m7reader.attempt_detail(sid, aid)
    p, c = primary(detail), next(j for j in detail["jobs"] if j["kind"] == "comparison")
    assert c["state"] == "SUCCEEDED" and c["boundary"]["source"] == "primary"
    assert c["boundary"]["cut_ms"] == p["boundary"]["cut_ms"] and c["boundary"]["engine"] == "wav2vec2_raw"
    other = c["boundary"]["other_engine"]
    assert other["engine"] == "openpronounce" and other["differs"] is False and "acoustic model" in other["note"]
    assert m7engines["openpronounce"].calls[-1][1] == pytest.approx(p["boundary"]["cut_ms"], abs=0.1)
    cmp = m7reader.comparison(sid, aid)
    assert cmp["integrity"]["ok"]


def test_summary_only_sees_the_sentence(m7reader):
    sid, aid, _, detail, _ = read_attempt(m7reader)
    m7reader.session_action(sid, "finish")
    summary = m7reader.summary(sid)
    assert summary is not None and validate_summary(summary, m7reader.store) == []
    cut = primary(detail)["boundary"]["cut_ms"]
    for p in summary["patterns"]:
        for e in p["examples"]:
            assert e["play_ms"][1] <= cut + 1


def test_next_sentence_is_untouched(m7reader, m7engines):
    sid, aid, art, detail, _ = read_attempt(m7reader)
    snap = m7reader.snapshot(sid)
    second = art["segments"][1]["id"]
    assert snap["segment_states"][second] == "UNREAD"
    assert all(a["segment_id"] == art["segments"][0]["id"] for a in snap["attempts"])
    assert set(m7engines["wav2vec2_raw"].texts) == {art["segments"][0]["text"]}  # never analysed as sentence 2


def test_invalid_boundary_falls_back_to_the_whole_attempt_reported_as_unreliable(m7reader, monkeypatch):
    real = B.detect_boundary

    def broken(result, samples, sr):
        b = real(result, samples, sr)
        b["regions"][-1]["end_ms"] += 50  # does not tile the attempt
        return b

    monkeypatch.setattr(B, "detect_boundary", broken)
    sid, aid, _, detail, _ = read_attempt(m7reader)
    job = primary(detail)
    assert job["state"] == "SUCCEEDED" and job["boundary"]["state"] == "NO_RELIABLE_BOUNDARY"
    assert "inconsistent" in job["boundary"]["reasons"][0]
    assert not job["boundary"]["target_analysed"]
    # continuation was detected before the boundary was rejected: the whole attempt is NOT shown as feedback
    assert job["boundary"]["feedback_withheld"] and "continued speech was detected" in " ".join(job["boundary"]["reasons"])
    view = detail["views"][job["id"]]
    assert view["state"] == "boundary_withheld" and view["words"] == [] and view["coach"]["observations"] == []
    assert view["reduction"]["candidates"] == [] and job["feedback"]["state"] == "boundary_withheld"
    assert m7reader.rebuild_view(job)["state"] == "boundary_withheld"


# ----------------------------------------------------------------------
# The M7 manual-test failure through the reader: "…underresourced" + sentence 3, speech-dense and noisy
# ----------------------------------------------------------------------

DENSE_TEXT = "Those efforts fail because they're underresourced. But many of the best examples are elsewhere."


def read_dense(reader, engines):
    for e in engines.values():
        if isinstance(e, H.BoundaryEngine):
            e.dense = True
    art = reader.create_article(DENSE_TEXT, "M7 dense")
    sid = M.new_id()
    reader.create_session(sid, art["id"])
    seg = art["segments"][0]
    aid = M.new_id()
    data = H.wav16(H.dense_audio())
    n = (len(data) - 44) // 2
    reader.start_attempt(sid, aid, {"segment_id": seg["id"], "run_id": RUN, "sample_rate": 16000, "start_sample": 0})
    reader.upload_audio(sid, aid, data, capture(seg["id"], 0, n, rate=16000, reason="stopped"))
    assert reader.worker.wait_idle(20)
    return sid, aid, art, reader.attempt_detail(sid, aid)


def assert_contained(reader, job, view):
    """The permanent invariant: nothing M4/M5 consume lies outside the sentence's region."""
    from pronunciation_lab.benchmark.schema import PronunciationResult

    b = job["boundary"]
    end = b["regions"][0]["end_ms"]
    name = "target_result.json" if b["target_analysed"] else None
    res = PronunciationResult.model_validate_json(
        (reader.store.job_file(job, name) if name else reader.store.result_path(job)).read_text())
    assert B.evidence_outside_target(view, res, end) == []
    assert b["containment"]["ok"] and b["containment"]["count"] == 0
    return res, end


def test_underresourced_gets_no_evidence_from_the_next_sentence(m7reader, m7engines):
    sid, aid, art, detail = read_dense(m7reader, m7engines)
    job = primary(detail)
    b = job["boundary"]
    # 1. continuation detected, never TARGET_ONLY; the target ends after the sentence and before sentence 3
    assert job["state"] == "SUCCEEDED" and b["state"] == "BOUNDARY_UNCERTAIN" and b["target_analysed"]
    assert not b["evidence"]["activity_reliable"] and not b["feedback_withheld"]
    assert H.DENSE_END_MS <= b["cut_ms"] < H.DENSE_CONT_MS
    assert [r["kind"] for r in b["regions"]] == ["target", "uncertain"]
    # 2. the whole-attempt evidence shows the contamination that was removed: sentence-3 sounds on "underresourced"
    full = json.loads(files(m7reader, job)["result.json"].read_text())
    under_full = full["words"][-1]["phonemes"]
    assert sum(len(p["engine_evidence"]["extra_heard_phones"]) for p in under_full) >= 30
    assert max(p["timing"]["end_ms"] or 0 for p in under_full) > H.DENSE_CONT_MS  # its /t/ matched in sentence 3
    # 3. the sentence's own analysis: "underresourced" only from sentence 2
    view = detail["views"][job["id"]]
    res, end = assert_contained(m7reader, job, view)
    under = res.words[-1]
    assert under.word == "underresourced"
    for p in under.phonemes:
        assert p.engine_evidence.get("operation") == "match", (p.expected.phoneme, p.engine_evidence.get("operation"))
        assert p.timing.end_ms <= H.DENSE_END_MS and not p.engine_evidence.get("extra_heard_phones")
    assert not any(abs((o.get("span_ms") or [0])[0] - 22820) < 2000 for o in view["coach"]["observations"])
    # no sentence-3 sound reached the analysis: exactly the sentence's sounds were decoded, none inserted
    assert res.engine_evidence["recognition"]["phones"] == " ".join(p for _, p in H.DENSE_SENTENCE).split()
    assert all(not p.engine_evidence.get("extra_heard_phones") for w in res.words for p in w.phonemes)
    # 4. M4 and M5 see only the target
    assert all(o["span_ms"][1] <= end for o in view["coach"]["observations"])
    assert all(c["where"]["span_ms"][1] <= end for c in view["reduction"]["candidates"])
    vw = [w for w in view["words"] if w["word"] == "underresourced"][0]
    assert vw["status"] != "not_interpreted" and vw["play_ms"][1] <= end
    # 5. exact playback: the sentence stops at the boundary; the continuation stays playable
    t, u = (r["play"] for r in b["regions"])
    assert t["play_ms"] == [0.0, b["cut_ms"]] and u["play_ms"] == [b["cut_ms"], H.DENSE_DURATION_MS]
    assert u["kind"] == "uncertain" and u["attempt_id"] == aid
    # 6. the next sentence is untouched, never activated
    snap = m7reader.snapshot(sid)
    assert snap["segment_states"][art["segments"][1]["id"]] == "UNREAD" and len(snap["attempts"]) == 1
    assert set(m7engines["wav2vec2_raw"].texts) == {art["segments"][0]["text"]}


def test_containment_violation_withholds_feedback(m7reader, m7engines, monkeypatch):
    real = B.evidence_outside_target
    monkeypatch.setattr(B, "evidence_outside_target",
                        lambda view, res, end, tolerance_ms=1.0: real(view, res, end - 400.0, tolerance_ms))
    sid, aid, _, detail = read_dense(m7reader, m7engines)
    job = primary(detail)
    assert job["state"] == "SUCCEEDED" and job["boundary"]["feedback_withheld"]
    assert job["boundary"]["containment"]["ok"] is False
    view = detail["views"][job["id"]]
    assert view["state"] == "boundary_withheld" and not view["coach"]["observations"] and not view["words"]
    assert job["feedback"]["message"].startswith("No pronunciation feedback: part of the analysis lay outside")
    assert job["boundary"]["withheld_reason"] == "containment" and job["boundary"]["containment"]["checked"]


def test_no_reliable_boundary_with_plausible_continuation_withholds_feedback(m7reader, m7engines, monkeypatch):
    real = B.detect_boundary

    def no_timing(result, samples, sr):
        rec = result.engine_evidence["recognition"]
        result.engine_evidence["recognition"] = {k: v for k, v in rec.items() if k != "frame_spans"}
        try:
            return real(result, samples, sr)
        finally:
            result.engine_evidence["recognition"] = rec

    monkeypatch.setattr(B, "detect_boundary", no_timing)
    sid, aid, _, detail = read_dense(m7reader, m7engines)
    job = primary(detail)
    b = job["boundary"]
    assert b["state"] == "NO_RELIABLE_BOUNDARY" and b["feedback_withheld"] and not b["target_analysed"]
    assert any("extra sounds were decoded at the end" in r for r in b["reasons"])
    assert detail["views"][job["id"]]["state"] == "boundary_withheld"
    m7reader.session_action(sid, "finish")
    summary = m7reader.summary(sid)
    assert summary["inputs"] == [] and summary["coverage"]["not_included"][0]["reason"].startswith("sentence boundary uncertain")


def test_no_reliable_boundary_without_continuation_keeps_the_pre_m7_fallback(m7reader, m7engines, monkeypatch):
    monkeypatch.setattr(B, "detect_boundary", lambda r, x, sr: B._no_boundary("NO_RELIABLE_BOUNDARY", ["test"],
                                                                             len(x) * 1000.0 / sr, r.engine.name))
    sid, aid, _, detail, _ = read_attempt(m7reader, continued=False)
    job = primary(detail)
    assert job["boundary"]["state"] == "NO_RELIABLE_BOUNDARY" and not job["boundary"]["feedback_withheld"]
    assert detail["views"][job["id"]]["state"] == "ok"


def test_containment_invariant_holds_for_every_scenario(m7reader, m7engines):
    for continued in (False, True):
        sid, aid, _, detail, _ = read_attempt(m7reader, continued=continued)
        job = primary(detail)
        assert_contained(m7reader, job, detail["views"][job["id"]])


def test_undefensible_boundary_withholds_feedback_without_a_second_inference(m7reader, m7engines, monkeypatch):
    real = B.detect_boundary

    def unclear(result, samples, sr):
        b = real(result, samples, sr)
        if b["state"] in B.NEEDS_TARGET_ANALYSIS:
            b["defensible"] = False
            b["state"] = "BOUNDARY_UNCERTAIN"
            for r in b["regions"][1:]:
                r["kind"] = "uncertain"
        return b

    monkeypatch.setattr(B, "detect_boundary", unclear)
    sid, aid, _, detail, _ = read_attempt(m7reader)
    job = primary(detail)
    assert job["boundary"]["feedback_withheld"] and not job["boundary"]["target_analysed"]
    assert len(m7engines["wav2vec2_raw"].calls) == 1 and not files(m7reader, job)["target.wav"].exists()
    view = detail["views"][job["id"]]
    assert view["state"] == "boundary_withheld" and not view["words"] and not view["coach"]["observations"]
    # the continuation is still preserved and playable
    assert [r["kind"] for r in job["boundary"]["regions"]] == ["target", "uncertain"]
    assert job["boundary"]["regions"][1]["play"]["play_ms"][1] == pytest.approx(job["boundary"]["duration_ms"])


# ----------------------------------------------------------------------
# Boundary confidence vs analysis confidence in the reader
# ----------------------------------------------------------------------

def _relied(monkeypatch, recheck_issues=()):
    """The boundary rests on local support alone (whole-recording cost above the limit)."""
    real_detect, real_check = B.detect_boundary, B.check_target_analysis

    def detect(result, samples, sr):
        b = real_detect(result, samples, sr)
        if b["state"] in B.NEEDS_TARGET_ANALYSIS:
            b |= {"defensible": True, "boundary_confidence": "supported", "relied_on_local_support": True}
        return b

    def check(boundary, tres, samples, sr):
        out = real_check(boundary, tres, samples, sr)
        out["target_check"]["issues"] = list(recheck_issues)
        return out

    monkeypatch.setattr(B, "detect_boundary", detect)
    monkeypatch.setattr(B, "check_target_analysis", check)


def test_a_locally_supported_boundary_is_isolated_and_analysed_on_its_own(m7reader, monkeypatch):
    _relied(monkeypatch)
    seen, real_conf, real_pipeline = [], B.analysis_confidence, reader_service.analyze_pipeline

    def tagged(*a, **kw):  # mark the sentence-only analysis (target.wav, recording id "...-t")
        res, view = real_pipeline(*a, **kw)
        res = res.model_copy(deep=True)  # the fake engine may hand out one shared result object
        res.engine_evidence["sentence_only"] = kw["recording_id"].endswith("-t")
        return res, view

    monkeypatch.setattr(reader_service, "analyze_pipeline", tagged)
    monkeypatch.setattr(B, "analysis_confidence", lambda r: seen.append(r.engine_evidence.get("sentence_only")) or real_conf(r))
    sid, aid, _, detail, _ = read_attempt(m7reader)
    job = primary(detail)
    b = m7reader.store.load_boundary(job)
    assert b["relied_on_local_support"] and b["target_analysed"] and not b["feedback_withheld"]
    assert files(m7reader, job)["target.wav"].is_file() and b["containment"]["checked"] and b["containment"]["ok"]
    from pronunciation_lab.benchmark.schema import PronunciationResult
    tres = PronunciationResult.model_validate_json(files(m7reader, job)["target_result.json"].read_text())
    assert seen and all(seen)                   # measured on the sentence-only result, not the full attempt
    assert b["analysis"] == B.analysis_confidence(tres) | {"shown": True}
    view = detail["views"][job["id"]]
    assert view["analysis_confidence"] == b["analysis"] and view["state"] == "ok"


def test_a_locally_supported_cut_that_the_sentence_alone_contradicts_is_withheld(m7reader, monkeypatch):
    _relied(monkeypatch, recheck_issues=["the sentence on its own still shows sound after its end"])
    sid, aid, _, detail, _ = read_attempt(m7reader)
    job = primary(detail)
    b = m7reader.store.load_boundary(job)
    assert b["feedback_withheld"] and b["withheld_reason"] == "boundary" and b["state"] == "BOUNDARY_UNCERTAIN"
    assert [r["kind"] for r in b["regions"]] == ["target", "uncertain"]
    assert detail["views"][job["id"]]["state"] == "boundary_withheld"


def test_containment_is_only_checked_on_an_analysis_that_is_shown(m7reader, monkeypatch):
    real = B.detect_boundary
    monkeypatch.setattr(B, "detect_boundary", lambda r, x, sr: real(r, x, sr) | {"defensible": False,
                                                                                    "state": "BOUNDARY_UNCERTAIN"})
    sid, aid, _, detail, _ = read_attempt(m7reader)
    b = m7reader.store.load_boundary(primary(detail))
    assert b["feedback_withheld"] and b["withheld_reason"] == "boundary"
    assert b["containment"] == {"ok": None, "issues": [], "count": 0, "checked": False}
    assert b["analysis"]["shown"] is False
    st = next(a for a in m7reader.snapshot(sid)["attempts"] if a["id"] == aid)["status"]
    assert st["analysis"] is None and st["analysis_note"] is None   # nothing shown, nothing to qualify
    assert not any("lay outside" in r for r in b["reasons"])


def test_status_and_summary_name_the_real_cause(m7reader, monkeypatch):
    from pronunciation_lab.reader.status import CONTAINMENT_TEXT, LOW_CONFIDENCE_TEXT
    real = B.evidence_outside_target
    monkeypatch.setattr(B, "evidence_outside_target",
                        lambda view, res, end, tolerance_ms=1.0: real(view, res, end - 400.0, tolerance_ms))
    sid, aid, _, detail, _ = read_attempt(m7reader)
    st = next(a for a in m7reader.snapshot(sid)["attempts"] if a["id"] == aid)["status"]
    assert st["feedback"] == "withheld_containment" and st["withheld_reason"] == "containment"
    m7reader.session_action(sid, "finish")
    cov = m7reader.summary(sid)["coverage"]
    assert cov["feedback_withheld_containment"] == 1 and cov["feedback_withheld"] == 0
    assert cov["not_included"][0]["reason"] == "analysis not contained in the sentence — recording preserved, feedback withheld"
    monkeypatch.undo()
    monkeypatch.setattr(B, "analysis_confidence", lambda r: {"state": "low_confidence", "cost_per_sound": 0.62,
                                                           "engine": r.engine.name, "max_cost": 0.5})
    sid, aid, _, detail, _ = read_attempt(m7reader)
    st = next(a for a in m7reader.snapshot(sid)["attempts"] if a["id"] == aid)["status"]
    assert st["feedback"] == "shown" and st["analysis"] == "low_confidence" and st["analysis_note"] == LOW_CONFIDENCE_TEXT
    m7reader.session_action(sid, "finish")
    cov = m7reader.summary(sid)["coverage"]
    assert cov["feedback_included"] == 1 and cov["feedback_low_confidence"] == 1
    assert CONTAINMENT_TEXT.startswith("The sentence was separated")
