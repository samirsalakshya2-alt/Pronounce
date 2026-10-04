"""Record → identity → boundary → feedback → Keep / Re-record → summary: separate decisions, nothing deleted."""

import pytest
from readerhelpers import new_session, record

from pronunciation_lab.reader import service as reader_service

TEXT4 = "First sentence here. Second sentence there. Third one follows. Fourth ends it."


def identities(monkeypatch, states):
    """confirm_target answers from `states` in order (one per analysed attempt)."""
    seq = iter(states)
    monkeypatch.setattr(reader_service, "confirm_target",
                        lambda result, alternatives=None: {"state": next(seq), "reason": "test"})


def finish(reader, sid):
    assert reader.worker.wait_idle(10)
    return reader.session_action(sid, "finish")["summary"]


def status(reader, sid, aid):
    snap = reader.snapshot(sid)
    return next(a for a in snap["attempts"] if a["id"] == aid)["status"]


def assert_stored(reader, sid, aid):
    d = reader.store.attempt_dir(sid, aid)
    assert (d / "attempt.json").is_file() and (d / "original.wav").is_file() and (d / "analysis.wav").is_file()
    assert reader.attempt_detail(sid, aid)["jobs"], "its analysis is kept too"


def test_rerecord_keeps_both_attempts_and_uses_the_new_one(reader, monkeypatch):
    identities(monkeypatch, ["AMBIGUOUS", "MATCH"])
    sid, art, _ = new_session(reader, TEXT4)
    seg = art["segments"][0]
    a, _ = record(reader, sid, seg)
    assert reader.worker.wait_idle(10)
    s = status(reader, sid, a)
    assert s["identity"] == "AMBIGUOUS" and s["needs_decision"] and s["feedback"] == "shown"
    assert s["message"] == "I couldn't confidently tell whether this recording is this sentence."
    reader.set_disposition(sid, a, "rerecord_requested")               # Re-record
    b, _ = record(reader, sid, seg, start=48000)
    sm = finish(reader, sid)
    assert_stored(reader, sid, a)                                       # never deleted
    assert_stored(reader, sid, b)
    assert status(reader, sid, a)["summary"] == "marked for re-recording" and status(reader, sid, a)["preserved"]
    assert [i["attempt_id"] for i in sm["inputs"]] == [b]


def test_keep_on_the_retake_includes_it(reader, monkeypatch):
    identities(monkeypatch, ["MISMATCH", "AMBIGUOUS"])
    sid, art, _ = new_session(reader, TEXT4)
    seg = art["segments"][0]
    a, _ = record(reader, sid, seg)
    assert reader.worker.wait_idle(10)
    reader.set_disposition(sid, a, "rerecord_requested")
    b, _ = record(reader, sid, seg, start=48000)
    sm = finish(reader, sid)
    assert sm["inputs"] == []
    assert sm["coverage"]["not_included"][0]["reason"] == "could not confirm it is this sentence — keep it to include it"
    reader.set_disposition(sid, b, "kept")                              # Keep the retake
    reader.session_action(sid, "resume")
    sm = finish(reader, sid)
    assert [i["attempt_id"] for i in sm["inputs"]] == [b]
    for x in (a, b):
        assert_stored(reader, sid, x)


def test_keep_on_the_first_attempt_preserves_it_and_a_newer_match_is_still_used(reader, monkeypatch):
    identities(monkeypatch, ["AMBIGUOUS", "MATCH"])
    sid, art, _ = new_session(reader, TEXT4)
    seg = art["segments"][0]
    a, _ = record(reader, sid, seg)
    assert reader.worker.wait_idle(10)
    reader.set_disposition(sid, a, "kept")
    b, _ = record(reader, sid, seg, start=48000)                        # read it again anyway
    sm = finish(reader, sid)
    assert [i["attempt_id"] for i in sm["inputs"]] == [b]               # the latest eligible attempt
    assert status(reader, sid, a)["kept"] and status(reader, sid, a)["summary"] is None
    assert_stored(reader, sid, a)


def test_older_kept_attempt_is_used_when_the_retake_is_not_eligible(reader, monkeypatch):
    identities(monkeypatch, ["AMBIGUOUS", "MISMATCH"])
    sid, art, _ = new_session(reader, TEXT4)
    seg = art["segments"][0]
    a, _ = record(reader, sid, seg)
    assert reader.worker.wait_idle(10)
    reader.set_disposition(sid, a, "kept")
    b, _ = record(reader, sid, seg, start=48000)
    sm = finish(reader, sid)
    assert [i["attempt_id"] for i in sm["inputs"]] == [a]               # preserved and eligible: not ignored
    assert status(reader, sid, b)["summary"] == "appears to contain a different sentence"


@pytest.mark.parametrize("identity", ["MATCH", "LIKELY_MATCH", "AMBIGUOUS", "MISMATCH"])
def test_keep_never_unlocks_withheld_feedback(reader, monkeypatch, identity):
    identities(monkeypatch, [identity])
    real = reader_service.B.detect_boundary

    def withheld(result, samples, sr):
        b = real(result, samples, sr)
        b["regions"][-1]["end_ms"] += 50   # inconsistent boundary → NO_RELIABLE_BOUNDARY
        return b

    monkeypatch.setattr(reader_service.B, "detect_boundary", withheld)
    monkeypatch.setattr(reader_service.B, "continuation_plausible", lambda result, detected=None: ["test continuation"])
    sid, art, _ = new_session(reader, TEXT4)
    a, _ = record(reader, sid, art["segments"][0])
    assert reader.worker.wait_idle(10)
    s = status(reader, sid, a)
    assert s["identity"] == identity and s["feedback"] == "withheld_boundary"
    if identity == "MATCH":
        assert s["message"] == "Your reading appears to match this sentence, but I couldn't safely determine where it ended."
    reader.set_disposition(sid, a, "kept")
    assert status(reader, sid, a)["feedback"] in ("withheld_boundary", "hidden_identity")  # Keep does not unlock
    sm = finish(reader, sid)
    assert sm["inputs"] == []
    reason = sm["coverage"]["not_included"][0]["reason"]
    assert reason == ("appears to contain a different sentence" if identity == "MISMATCH"
                      else "sentence boundary uncertain — recording preserved, feedback withheld")
    view = reader.attempt_detail(sid, a)["views"]
    assert all(v["state"] == "boundary_withheld" for v in view.values())
    assert_stored(reader, sid, a)


def test_mismatch_kept_is_preserved_but_never_feedback(reader, monkeypatch):
    identities(monkeypatch, ["MISMATCH"])
    sid, art, _ = new_session(reader, TEXT4)
    a, _ = record(reader, sid, art["segments"][0])
    assert reader.worker.wait_idle(10)
    reader.set_disposition(sid, a, "kept")
    s = status(reader, sid, a)
    assert s["feedback"] == "hidden_identity" and s["kept"] and not s["needs_decision"]
    assert s["message"] == "This recording appears to contain a different sentence."
    assert finish(reader, sid)["inputs"] == []
    assert_stored(reader, sid, a)


def test_segment_state_follows_identity_and_keep(reader, monkeypatch):
    identities(monkeypatch, ["AMBIGUOUS"])
    sid, art, _ = new_session(reader, TEXT4)
    seg = art["segments"][0]
    a, _ = record(reader, sid, seg)
    assert reader.worker.wait_idle(10)
    assert reader.snapshot(sid)["segment_states"][seg["id"]] == "NEEDS_ATTENTION"
    reader.set_disposition(sid, a, "kept")
    assert reader.snapshot(sid)["segment_states"][seg["id"]] == "FEEDBACK_READY"


def test_status_is_derived_never_stored(reader, monkeypatch):
    identities(monkeypatch, ["MATCH"])
    sid, art, _ = new_session(reader, TEXT4)
    a, _ = record(reader, sid, art["segments"][0])
    assert reader.worker.wait_idle(10)
    assert "status" in next(x for x in reader.snapshot(sid)["attempts"] if x["id"] == a)
    assert "status" not in reader.store.load_attempt(sid, a)


def test_the_reader_compares_with_the_neighbouring_sentences(reader, monkeypatch):
    seen = []
    monkeypatch.setattr(reader_service, "confirm_target",
                        lambda result, alternatives=None: seen.append(alternatives) or {"state": "MATCH", "reason": "t"})
    text = "One here now. Two there now. Three follows now. Four ends it. Five more now."
    sid, art, _ = new_session(reader, text)
    record(reader, sid, art["segments"][2])
    assert reader.worker.wait_idle(10)
    others = [g["text"] for g in art["segments"] if g["readable"]]
    assert seen == [[t for k, t in enumerate(others) if k != 2]]  # ±3 around sentence 3: all the others here
