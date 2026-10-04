"""Reading coverage vs feedback: normal and probable readings count without Keep; Keep overrides an uncertain
identity only; boundary-withheld recordings count as recorded but their feedback stays withheld."""

import json
import subprocess
from pathlib import Path

import pytest
from readerhelpers import new_session, record

from pronunciation_lab.reader import service as reader_service
from pronunciation_lab.reader.status import attempt_status

TEXT5 = "One sentence here. Two sentence there. Three one follows. Four ends it. Five closes it."
WITHHELD = "sentence boundary uncertain — recording preserved, feedback withheld"


def setup(monkeypatch, plan):
    """plan: one (identity, withheld) per analysed attempt, in order."""
    identities = iter([state for state, _ in plan])
    monkeypatch.setattr(reader_service, "confirm_target",
                        lambda result, alternatives=None: {"state": next(identities), "reason": "test"})
    # M7 withholds feedback for the attempts the plan marks (as an undefensible boundary would)
    withheld = [w for _, w in plan]
    orig = reader_service.ReaderService._sentence_analysis

    def analysis(self, job, attempt, engine, path, result, view):
        res, v, b = orig(self, job, attempt, engine, path, result, view)
        if withheld.pop(0):
            b["feedback_withheld"] = True
            b["state"] = "BOUNDARY_UNCERTAIN"
            v = reader_service._withheld_view(v)
            v["boundary"] = self._boundary_view(attempt, job, b)
        return res, v, b

    monkeypatch.setattr(reader_service.ReaderService, "_sentence_analysis", analysis)


def finish(reader, sid):
    assert reader.worker.wait_idle(10)
    return reader.session_action(sid, "finish")["summary"]


def status(reader, sid, aid):
    return next(a for a in reader.snapshot(sid)["attempts"] if a["id"] == aid)["status"]


def read_one(reader, monkeypatch, identity, withheld):
    setup(monkeypatch, [(identity, withheld)])
    sid, art, _ = new_session(reader, TEXT5)
    a, _ = record(reader, sid, art["segments"][0])
    return sid, a, finish(reader, sid)


def test_1_match_with_a_safe_boundary_is_summarised(reader, monkeypatch):
    sid, a, sm = read_one(reader, monkeypatch, "MATCH", False)
    assert [i["attempt_id"] for i in sm["inputs"]] == [a]
    c = sm["coverage"]
    assert (c["recorded"], c["identified"], c["feedback_included"], c["feedback_withheld"]) == (1, 1, 1, 0)


def test_2_match_with_an_uncertain_boundary_is_recorded_but_feedback_withheld(reader, monkeypatch):
    sid, a, sm = read_one(reader, monkeypatch, "MATCH", True)
    s = status(reader, sid, a)
    assert s["recorded"] and s["feedback"] == "withheld_boundary" and not s["needs_decision"]
    assert sm["inputs"] == [] and sm["coverage"]["not_included"][0]["reason"] == WITHHELD
    c = sm["coverage"]
    assert (c["recorded"], c["identified"], c["feedback_included"], c["feedback_withheld"]) == (1, 1, 0, 1)
    assert all(v["state"] == "boundary_withheld" for v in reader.attempt_detail(sid, a)["views"].values())


def test_3_likely_match_with_a_safe_boundary_is_summarised_without_keep(reader, monkeypatch):
    sid, a, sm = read_one(reader, monkeypatch, "LIKELY_MATCH", False)
    assert [i["attempt_id"] for i in sm["inputs"]] == [a]
    assert not status(reader, sid, a)["needs_decision"]
    assert reader.snapshot(sid)["segment_states"][reader.store.load_attempt(sid, a)["segment_id"]] == "FEEDBACK_READY"


def test_4_likely_match_with_an_uncertain_boundary_is_recorded_but_feedback_withheld(reader, monkeypatch):
    sid, a, sm = read_one(reader, monkeypatch, "LIKELY_MATCH", True)
    c = sm["coverage"]
    assert sm["inputs"] == [] and (c["recorded"], c["identified"], c["feedback_withheld"]) == (1, 1, 1)
    assert status(reader, sid, a)["message"].startswith("Your reading is probably this sentence")


def test_5_ambiguous_is_not_summarised_until_kept(reader, monkeypatch):
    sid, a, sm = read_one(reader, monkeypatch, "AMBIGUOUS", False)
    c = sm["coverage"]
    assert sm["inputs"] == [] and (c["recorded"], c["uncertain"], c["awaiting_decision"]) == (1, 1, 1)
    assert sm["coverage"]["not_included"][0]["reason"] == "could not confirm it is this sentence — keep it to include it"
    assert status(reader, sid, a)["needs_decision"]


@pytest.mark.parametrize("withheld, included", [(False, True), (True, False)])
def test_6_ambiguous_kept_is_summarised_only_if_otherwise_safe(reader, monkeypatch, withheld, included):
    sid, a, sm = read_one(reader, monkeypatch, "AMBIGUOUS", withheld)
    reader.set_disposition(sid, a, "kept")
    reader.session_action(sid, "resume")
    sm = finish(reader, sid)
    assert ([i["attempt_id"] for i in sm["inputs"]] == [a]) is included
    assert sm["coverage"]["identified"] == 1  # the reader's Keep confirms the identity either way


def test_7_mismatch_kept_is_preserved_but_feedback_stays_withheld(reader, monkeypatch):
    sid, a, sm = read_one(reader, monkeypatch, "MISMATCH", False)
    reader.set_disposition(sid, a, "kept")
    reader.session_action(sid, "resume")
    sm = finish(reader, sid)
    s = status(reader, sid, a)
    assert sm["inputs"] == [] and s["feedback"] == "hidden_identity" and not s["recorded"]
    assert (sm["coverage"]["recorded"], sm["coverage"]["different"]) == (0, 1)
    assert reader.store.audio_path(sid, a, "original.wav").is_file()


def test_8_rerecord_preserves_the_old_attempt(reader, monkeypatch):
    setup(monkeypatch, [("AMBIGUOUS", False), ("MATCH", False)])
    sid, art, _ = new_session(reader, TEXT5)
    seg = art["segments"][0]
    a, _ = record(reader, sid, seg)
    assert reader.worker.wait_idle(10)
    reader.set_disposition(sid, a, "rerecord_requested")
    b, _ = record(reader, sid, seg, start=48000)
    sm = finish(reader, sid)
    assert [i["attempt_id"] for i in sm["inputs"]] == [b]
    for x in (a, b):
        assert (reader.store.attempt_dir(sid, x) / "original.wav").is_file()
    assert sm["coverage"]["recorded"] == 1 and sm["coverage"]["identified"] == 1  # one sentence, counted once


def test_9_the_summary_separates_recorded_from_feedback(reader, monkeypatch):
    # the manual-test shape: 5 sentences read, none kept
    plan = [("LIKELY_MATCH", True), ("AMBIGUOUS", False), ("AMBIGUOUS", False), ("LIKELY_MATCH", True),
            ("LIKELY_MATCH", False)]
    setup(monkeypatch, plan)
    sid, art, _ = new_session(reader, TEXT5)
    for k, seg in enumerate(art["segments"]):
        record(reader, sid, seg, start=48000 * k)
    sm = finish(reader, sid)
    c = sm["coverage"]
    assert (c["sentences"], c["recorded"], c["identified"], c["uncertain"]) == (5, 5, 3, 2)
    assert (c["feedback_included"], c["feedback_withheld"], c["awaiting_decision"]) == (1, 2, 2)
    assert c["feedback_included"] + c["feedback_withheld"] + c["awaiting_decision"] == c["recorded"]
    assert c["included"] == 1  # (the old key, kept for compatibility)
    # the reader's wording for this coverage (the same helper the page uses)
    module = Path(__file__).parents[2] / "src/pronunciation_lab/app/static/reader-feedback.js"
    out = subprocess.run(["node", "-e", f"const F = require({json.dumps(str(module))});"
                          f"console.log(JSON.stringify(F.readingLines({json.dumps(c)})))"],
                         capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [
        "5 of 5 sentences recorded · 3 identified · 2 uncertain",
        "1 sentence in the feedback below · 2 withheld because the sentence boundary was uncertain · "
        "2 waiting for you to keep or re-record"]


def test_feedback_categories_are_exclusive_for_an_uncertain_withheld_reading(reader, monkeypatch):
    # the manual-test shape that exposed it: AMBIGUOUS and boundary-withheld counts once, as withheld
    setup(monkeypatch, [("AMBIGUOUS", True), ("AMBIGUOUS", False), ("LIKELY_MATCH", False)])
    sid, art, _ = new_session(reader, TEXT5)
    for k, seg in enumerate(art["segments"][:3]):
        record(reader, sid, seg, start=48000 * k)
    c = finish(reader, sid)["coverage"]
    assert (c["feedback_included"], c["feedback_withheld"], c["awaiting_decision"]) == (1, 1, 1)
    assert c["feedback_included"] + c["feedback_withheld"] + c["awaiting_decision"] == c["recorded"] == 3


def test_status_fields_for_every_identity():
    att = {"state": "ANALYZED", "user_disposition": None}
    job = lambda tc, w=False: {"state": "SUCCEEDED", "engine_id": "e", "target_confirmation": {"state": tc},  # noqa: E731
                               "boundary": {"state": "BOUNDARY_UNCERTAIN" if w else "TARGET_ONLY", "feedback_withheld": w}}
    table = {(tc, w): attempt_status(att, job(tc, w), "e") for tc in ("MATCH", "LIKELY_MATCH", "AMBIGUOUS", "MISMATCH")
             for w in (False, True)}
    assert {k: v["summary"] is None for k, v in table.items()} == {
        ("MATCH", False): True, ("MATCH", True): False, ("LIKELY_MATCH", False): True, ("LIKELY_MATCH", True): False,
        ("AMBIGUOUS", False): False, ("AMBIGUOUS", True): False, ("MISMATCH", False): False, ("MISMATCH", True): False}
    assert {k: v["recorded"] for k, v in table.items()} == {k: k[0] != "MISMATCH" for k in table}
    assert {k: v["needs_decision"] for k, v in table.items()} == {k: k[0] in ("AMBIGUOUS", "MISMATCH") for k in table}
    for st in ("TOO_SHORT", "ANALYSIS_FAILED"):
        s = attempt_status({"state": st, "user_disposition": None}, None, "e")
        assert s["summary"] == "not analysed" and not s["recorded"] and s["identity_group"] == "unusable"
