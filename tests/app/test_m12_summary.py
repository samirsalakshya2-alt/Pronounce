"""M12 phase 9: the single-session reading summary (fake engines)."""

from readerhelpers import new_session, record

from pronunciation_lab.reader import model as M
from pronunciation_lab.reader import service as reader_service
from pronunciation_lab.reader.summary import eligibility, validate_summary

TEXT4 = "First sentence here. Second sentence there. Third one follows. Fourth ends it."


def finish(reader, sid):
    assert reader.worker.wait_idle(10)
    return reader.session_action(sid, "finish")


def test_summary_uses_one_eligible_attempt_per_sentence(reader):
    sid, art, _ = new_session(reader, TEXT4)
    s = art["segments"]
    a1, _ = record(reader, sid, s[0])
    a1b, _ = record(reader, sid, s[0], start=48000)        # second attempt of sentence 1: the one used
    a2, _ = record(reader, sid, s[1], start=96000)
    reader.set_disposition(sid, a2, "discarded")            # excluded
    a3, _ = record(reader, sid, s[2], start=144000)
    reader.set_disposition(sid, a3, "rerecord_requested")   # excluded
    snap = finish(reader, sid)
    assert snap["session"]["state"] == "SUMMARIZED"
    sm = snap["summary"]
    assert sm["integrity"] == {"ok": True, "issues": []} and sm["stale"] is False
    assert [i["attempt_id"] for i in sm["inputs"]] == [a1b]
    assert {(x["sentence"], x["reason"]) for x in sm["coverage"]["not_included"]} == {(2, "discarded"), (3, "marked for re-recording")}
    assert sm["coverage"]["sentences"] == 4 and sm["coverage"]["read"] == 3 and sm["coverage"]["included"] == 1
    assert validate_summary(sm, reader.store) == []


def test_target_mismatch_is_excluded_unless_the_user_keeps_it(reader, monkeypatch):
    calls = {"n": 0}

    def fake_target(result):
        calls["n"] += 1
        return {"state": "MISMATCH" if calls["n"] == 1 else "MATCH", "reason": "test"}

    monkeypatch.setattr(reader_service, "confirm_target", fake_target)
    sid, art, _ = new_session(reader, TEXT4)
    a1, _ = record(reader, sid, art["segments"][0])
    a2, _ = record(reader, sid, art["segments"][1], start=48000)
    sm = finish(reader, sid)["summary"]
    assert [i["attempt_id"] for i in sm["inputs"]] == [a2]
    assert sm["coverage"]["not_included"] == [{"segment_id": art["segments"][0]["id"], "sentence": 1,
                                               "reason": "may not match the sentence"}]
    reader.set_disposition(sid, a1, "kept")                  # the user confirms it is this sentence
    reader.session_action(sid, "resume")
    sm = finish(reader, sid)["summary"]
    assert sorted(i["attempt_id"] for i in sm["inputs"]) == sorted([a1, a2])
    assert next(i for i in sm["inputs"] if i["attempt_id"] == a1)["target"] == "MISMATCH"


def test_failed_and_comparison_results_are_never_summarised(reader, engines):
    sid, art, _ = new_session(reader, TEXT4)
    engines["wav2vec2_raw"].fail = True
    record(reader, sid, art["segments"][0])
    assert reader.worker.wait_idle(10)
    engines["wav2vec2_raw"].fail = False
    a2, _ = record(reader, sid, art["segments"][1], start=48000)
    assert reader.worker.wait_idle(10)
    reader.request_comparison(sid, a2)
    sm = finish(reader, sid)["summary"]
    assert [(i["attempt_id"], i["sentence"]) for i in sm["inputs"]] == [(a2, 2)]
    job = reader.store.load_job(sid, a2, sm["inputs"][0]["job_id"])
    assert job["kind"] == "primary" and job["engine_id"] == "wav2vec2_raw"
    assert {"sentence": 1, "reason": "not analysed"}.items() <= sm["coverage"]["not_included"][0].items()


def test_summary_waits_for_pending_analyses(reader, engines):
    sid, art, _ = new_session(reader, TEXT4)
    engines["wav2vec2_raw"].gate.clear()
    try:
        record(reader, sid, art["segments"][0])
        snap = reader.session_action(sid, "finish")
        assert snap["session"]["state"] == "FINISHED" and snap["summary"] is None
    finally:
        engines["wav2vec2_raw"].gate.set()
    assert reader.worker.wait_idle(10)
    snap = reader.snapshot(sid)
    assert snap["session"]["state"] == "SUMMARIZED" and snap["summary"]["inputs"]


def test_reading_on_after_the_summary_marks_it_stale(reader):
    sid, art, _ = new_session(reader, TEXT4)
    record(reader, sid, art["segments"][0])
    finish(reader, sid)
    record(reader, sid, art["segments"][1], start=48000)
    snap = reader.snapshot(sid)
    assert snap["session"]["state"] == "READING" and snap["summary"]["stale"] is True
    snap = finish(reader, sid)
    assert snap["summary"]["stale"] is False and len(snap["summary"]["inputs"]) == 2


def test_eligibility_rules():
    att = {"user_disposition": None, "state": "ANALYZED"}
    job = {"state": "SUCCEEDED", "engine_id": "e", "target_confirmation": {"state": "MATCH"}, "kind": "primary"}
    assert eligibility(att, job, "e") is None
    assert eligibility(att, job, "other") == "analysed by another engine"
    assert eligibility(att | {"state": "TOO_SHORT"}, job, "e") == "not analysed"
    assert eligibility(att, None, "e") == "not analysed"
    for tc in ("AMBIGUOUS", "MISMATCH", "NOT_CHECKED", "NOT_APPLICABLE"):
        assert eligibility(att, job | {"target_confirmation": {"state": tc}}, "e") == "may not match the sentence"
    assert eligibility(att | {"user_disposition": "kept"}, job | {"target_confirmation": {"state": "AMBIGUOUS"}}, "e") is None
    assert eligibility(att | {"user_disposition": "kept"}, job | {"target_confirmation": {"state": "NOT_CHECKED"}}, "e") \
        == "may not match the sentence"


def test_validator_catches_ineligible_inputs_and_foreign_examples(reader):
    sid, art, _ = new_session(reader, TEXT4)
    a1, _ = record(reader, sid, art["segments"][0])
    sm = finish(reader, sid)["summary"]
    reader.set_disposition(sid, a1, "discarded")
    assert any("not eligible" in i for i in validate_summary(sm, reader.store))
    bad = sm | {"patterns": [{"summary": "x", "examples": [{"attempt_id": M.new_id(), "job_id": "j", "timeline": "analysis_wav",
                                                             "play_ms": [0, 300]}]}]}
    reader.set_disposition(sid, a1, "kept")
    assert any("not an included attempt" in i for i in validate_summary(bad, reader.store))
