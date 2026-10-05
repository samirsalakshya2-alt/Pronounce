"""M9 in the reader: on-demand coaching across sessions, the issued snapshot at summary time, the HTTP route."""

import json

import m9helpers as H
from apphelpers import Client
from m9helpers import StoreBuilder, numbered
from readerhelpers import new_session, record

from pronunciation_lab.app.server import start_in_thread


def _strong_history(root):
    b = StoreBuilder(root)
    sessions = [b.session() for _ in range(3)]
    for k in range(12):
        w = ["sit", "list", "fill", "bit"][k % 4]
        b.attempt(sessions[k % 3], numbered(H.sub(w, "ɪ", "iː") if k < 6 else H.ok("bit", "ɪ"), H.ok("sit", "ɪ", 2)),
                  sentence=f"Sentence {k} with {w}.")
    return b


def test_empty_history_abstains_and_detail_is_opt_in(reader):
    c = reader.coaching()
    assert c["state"] == "no_action" and c["no_action"]["code"] == "history_too_small" and "detail" not in c
    assert "detail" in reader.coaching(detail=True) and c["integrity"]["ok"]


def test_coaching_across_sessions_and_its_cache(reader):
    b = _strong_history(reader.store.root)
    c1 = reader.coaching()
    assert c1["state"] == "actions" and len(c1["actions"]) == 1 and c1["pool"]["sessions"] == 3
    assert reader.coaching() is not c1 and reader.coaching()["generated_at"] == c1["generated_at"]   # cached
    b.attempt(b.store.session_ids()[0], numbered(H.sub("fit", "ɪ", "iː")), sentence="One more sentence.")
    c2 = reader.coaching()
    assert c2["pool"]["readings"] == 13 and c2["pool"]["fingerprint"] != c1["pool"]["fingerprint"]


def test_the_advice_issued_with_a_summary_is_stored_and_never_silently_changes(reader):
    _strong_history(reader.store.root)
    sid, art, _ = new_session(reader)
    record(reader, sid, art["segments"][0])
    assert reader.worker.wait_idle(10)
    reader.session_action(sid, "finish")
    issued = reader.store.load_coaching(sid)
    assert issued is not None and issued["state"] in ("actions", "no_action") and "detail" not in issued
    snap = reader.snapshot(sid)
    assert snap["coaching"] == issued and snap["summary"] is not None
    # more reading elsewhere changes live coaching, not the advice issued with that summary
    b = StoreBuilder(reader.store.root)
    other = b.session()
    for k in range(4):
        b.attempt(other, numbered(H.sub("we", "w", "v")), sentence=f"Extra {k}.")
    reader.session_action(sid, "reopen")
    assert reader.store.load_coaching(sid) == issued
    # the M12 summary itself keeps its own data semantics (M9 only changes what the UI shows first)
    assert {"patterns", "groups", "practise", "reductions", "fluency"} <= set(snap["summary"])


def test_coaching_failure_never_breaks_the_summary(reader, monkeypatch):
    def boom(detail=False):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(reader, "coaching", boom)
    sid, art, _ = new_session(reader)
    record(reader, sid, art["segments"][0])
    assert reader.worker.wait_idle(10)
    reader.session_action(sid, "finish")
    assert reader.summary(sid) is not None
    issued = reader.store.load_coaching(sid)
    assert issued["state"] == "unavailable" and issued["actions"] == [] and "synthetic failure" in issued["error"]


def test_http_route(reader, lab):
    _strong_history(reader.store.root)
    server, thread = start_in_thread(lab, reader=reader)
    try:
        client = Client(server.url)
        status, body = client.get_json("/api/coaching")
        assert status == 200 and body["coaching"]["state"] == "actions" and "detail" not in body["coaching"]
        status, body = client.get_json("/api/coaching?detail=1")
        assert status == 200 and body["coaching"]["detail"]["candidates"]
        json.dumps(body)  # serialisable end to end
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ----------------------------------------------------------------------
# "This reading" beside "What to practise now"
# ----------------------------------------------------------------------

import hashlib  # noqa: E402

from pronunciation_lab.reader import service as reader_service  # noqa: E402


def _finished_session(reader, n_segments=2, rerecord_first=False):
    sid, art, _ = new_session(reader)
    segs = [s for s in art["segments"] if s["readable"]][:n_segments]
    first = None
    for k, seg in enumerate(segs):
        aid, _ = record(reader, sid, seg, start=48000 * k)
        if k == 0:
            first = aid
    assert reader.worker.wait_idle(10)
    if rerecord_first:
        reader.set_disposition(sid, first, "rerecord_requested")
        record(reader, sid, segs[0], start=48000 * 9)
        assert reader.worker.wait_idle(10)
    reader.session_action(sid, "finish")
    return sid, first


def test_this_reading_is_stored_with_the_summary_and_scoped_to_its_inputs(reader):
    sid, first = _finished_session(reader, rerecord_first=True)
    rf = reader.store.load_reading_feedback(sid)
    summary = reader.store.load_summary(sid)
    assert rf["scope"] == "this_reading" and rf["state"] in ("feedback", "insufficient") and rf["integrity"]["ok"]
    assert [x["attempt_id"] for x in rf["provenance"]["inputs"]] == [i["attempt_id"] for i in summary["inputs"]]
    assert first not in {x["attempt_id"] for x in rf["provenance"]["inputs"]}          # superseded attempt excluded
    snap = reader.snapshot(sid)
    assert snap["reading_feedback"] == rf and snap["coaching"] is not None             # both, side by side
    assert reader.reading_feedback(sid) == rf


def test_this_reading_never_uses_the_recent_history_pool(reader, monkeypatch):
    def no_pool(store):
        raise AssertionError("the recent-history pool was read")
    monkeypatch.setattr(reader_service, "load_inputs", no_pool)
    sid, _ = _finished_session(reader)
    rf = reader.store.load_reading_feedback(sid)
    assert rf["state"] in ("feedback", "insufficient") and rf["integrity"]["ok"]
    assert reader.store.load_coaching(sid)["state"] == "unavailable"   # coaching needed the pool; the description did not


def test_a_failing_description_never_breaks_the_summary_or_the_coaching(reader, monkeypatch):
    monkeypatch.setattr(reader_service, "build_reading_feedback", lambda *a, **k: 1 / 0)
    sid, _ = _finished_session(reader)
    assert reader.summary(sid) is not None and reader.store.load_coaching(sid) is not None
    assert reader.store.load_reading_feedback(sid)["state"] == "unavailable"


def test_a_first_ever_reading_is_described_while_coaching_abstains(reader):
    sid, _ = _finished_session(reader)
    assert reader.store.load_coaching(sid)["no_action"]["code"] in ("history_too_small", "single_session")
    assert reader.store.load_reading_feedback(sid)["scope"] == "this_reading"


def test_reading_feedback_route(reader, lab):
    sid, _ = _finished_session(reader)
    server, thread = start_in_thread(lab, reader=reader)
    try:
        status, body = Client(server.url).get_json(f"/api/sessions/{sid}/reading-feedback")
        assert status == 200 and body["reading_feedback"]["scope"] == "this_reading"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_issued_coaching_records_are_kept_and_loadable_for_m10(reader):
    _strong_history(reader.store.root)
    sids = [_finished_session(reader)[0] for _ in range(2)]
    def digest(sid):
        return hashlib.sha256((reader.store.session_dir(sid) / "coaching.json").read_bytes()).hexdigest()
    before = {sid: digest(sid) for sid in sids}
    for sid in sids:            # regenerating the description touches nothing of the coaching record
        (reader.store.session_dir(sid) / "reading_feedback.json").unlink()
        reader.reading_feedback(sid)
    assert {sid: digest(sid) for sid in sids} == before
    # M10 can read every issued advice and every per-reading description through the public store API
    records = [(sid, reader.store.load_coaching(sid), reader.store.load_reading_feedback(sid))
               for sid in reader.store.session_ids()]
    issued = [c for _, c, _ in records if c and c.get("state") == "actions"]
    assert issued and all(a["supporting_unit_ids"] and a["why"]["measures"] and c["pool"]["fingerprint"]
                          for c in issued for a in c["actions"])
    assert all(r["scope"] == "this_reading" for _, _, r in records if r)


def test_an_older_stored_description_is_rebuilt_never_the_issued_coaching(reader):
    sid, _ = _finished_session(reader)
    coaching_before = (reader.store.session_dir(sid) / "coaching.json").read_bytes()
    old = {"scope": "this_reading", "version": "m9-read.1", "state": "feedback", "highlights": [], "went_well": []}
    reader.store.save_reading_feedback(sid, old)
    snap = reader.snapshot(sid)
    rf = snap["reading_feedback"]
    assert rf["version"] == reader_service.READING_VERSION and "improvement_areas" in rf and "highlights" not in rf
    assert reader.store.load_reading_feedback(sid) == rf == reader.reading_feedback(sid)
    assert (reader.store.session_dir(sid) / "coaching.json").read_bytes() == coaching_before
