"""M12 phases 3 and 8: reader service, background analysis worker, recovery and HTTP routes (fake engines)."""

import json
import threading
import time

import pytest
from apphelpers import Client
from readerhelpers import RUN, TEXT, ScriptedEngine, capture, new_session, record, wav48

from pronunciation_lab.app.server import start_in_thread
from pronunciation_lab.app.service import UserError
from pronunciation_lab.reader import model as M
from pronunciation_lab.reader.feedback import compact_feedback
from pronunciation_lab.reader.service import ReaderService
from pronunciation_lab.reader.store import ReaderStore

# ----------------------------------------------------------------------
# Sessions and attempts
# ----------------------------------------------------------------------

def test_article_session_snapshot(reader):
    sid, art, snap = new_session(reader)
    assert [s["text"] for s in art["segments"]] == ["The first sentence is here.", "A second one follows.", "And a third."]
    assert snap["session"]["state"] == "READY" and snap["session"]["engine_default"] == "wav2vec2_raw"
    assert set(snap["segment_states"].values()) == {"UNREAD"}
    assert reader.snapshot(sid, since=snap["rev"]) == {"unchanged": True, "rev": snap["rev"]}


def test_recording_is_analysed_in_the_background_and_fully_identified(reader):
    sid, art, _ = new_session(reader)
    seg = art["segments"][1]
    aid, out = record(reader, sid, seg)
    assert out["attempt"]["state"] == "QUEUED" and out["job"]["kind"] == "primary"
    assert reader.worker.wait_idle(10)
    detail = reader.attempt_detail(sid, aid)
    att, [job] = detail["attempt"], detail["jobs"]
    assert att["state"] == "ANALYZED" and job["state"] == "SUCCEEDED"
    assert (att["session_id"], att["segment_id"], att["id"], att["target_text"]) == (sid, seg["id"], aid, seg["text"])
    assert (job["session_id"], job["segment_id"], job["attempt_id"]) == (sid, seg["id"], aid)
    assert reader.analysis._instances["wav2vec2_raw"].texts == [seg["text"]]  # analysed against its own sentence
    view = detail["views"][job["id"]]
    assert {"coach", "reduction"} <= set(view) and view["reduction"]["integrity"]["ok"]
    assert job["feedback"] == compact_feedback(view) and job["feedback"]["state"] == "ok"
    assert reader.attempt_audio(sid, aid).read_bytes()[:4] == b"RIFF"
    snap = reader.snapshot(sid)
    assert snap["segment_states"][seg["id"]] == "FEEDBACK_READY" and snap["session"]["state"] == "READING"


def test_upload_returns_without_waiting_for_inference(reader, engines):
    sid, art, _ = new_session(reader)
    engines["wav2vec2_raw"].gate.clear()  # inference blocks
    try:
        _, first = record(reader, sid, art["segments"][0])
        deadline = time.monotonic() + 5
        while reader.worker.queue_status()["running"] is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert reader.worker.queue_status()["running"] is not None
        t0 = time.perf_counter()
        aid2, second = record(reader, sid, art["segments"][1], start=48000)  # capture continues
        elapsed = time.perf_counter() - t0
        assert second["attempt"]["state"] == "QUEUED" and elapsed < 1.0, elapsed
        assert reader.snapshot(sid)["queue"]["queued_primary"] == 1
    finally:
        engines["wav2vec2_raw"].gate.set()
    assert reader.worker.wait_idle(10)
    assert reader.attempt_detail(sid, aid2)["attempt"]["state"] == "ANALYZED"


def test_at_most_one_inference_across_reader_and_lab(reader, lab, engines, tmp_path):
    engines["wav2vec2_raw"].delay = 0.05
    sid, art, _ = new_session(reader)
    for i, seg in enumerate(art["segments"]):
        record(reader, sid, seg, start=i * 48000)
    data, _ = wav48(1.0)
    threads = [threading.Thread(target=lab.analyze_upload, args=(data, "x.wav", "Think.", None)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert reader.worker.wait_idle(10)
    assert ScriptedEngine.tracker["max"] == 1


def test_primary_jobs_run_before_comparison_jobs_fifo(tmp_path, lab):
    reader = ReaderService(ReaderStore(tmp_path / "s"), lab, start_worker=False)
    sid, art, _ = new_session(reader)
    a1, o1 = record(reader, sid, art["segments"][0])
    reader.worker.run_pending()
    cmp_job = reader.request_comparison(sid, a1)
    _, o2 = record(reader, sid, art["segments"][1], start=48000)
    _, o3 = record(reader, sid, art["segments"][2], start=96000)
    reader.worker.run_pending()
    assert reader.worker.processed == [o1["job"]["id"], o2["job"]["id"], o3["job"]["id"], cmp_job["id"]]
    assert reader.request_comparison(sid, a1)["id"] == cmp_job["id"]  # not queued twice


def test_multiple_attempts_per_segment_are_numbered(reader):
    sid, art, _ = new_session(reader)
    seg = art["segments"][0]
    a1, _ = record(reader, sid, seg)
    a2, _ = record(reader, sid, seg, start=48000)
    assert reader.worker.wait_idle(10)
    numbers = [reader.attempt_detail(sid, a)["attempt"]["attempt_number"] for a in (a1, a2)]
    assert numbers == [1, 2]


# ----------------------------------------------------------------------
# Ownership validation and nothing silently lost
# ----------------------------------------------------------------------

def test_duplicate_upload_is_rejected(reader):
    sid, art, _ = new_session(reader)
    seg = art["segments"][0]
    aid, _ = record(reader, sid, seg)
    data, n = wav48(1.0)
    with pytest.raises(UserError) as e:
        reader.upload_audio(sid, aid, data, capture(seg["id"], 0, n))
    assert e.value.code == "attempt_duplicate"


@pytest.mark.parametrize("mutate, code", [
    (lambda c, n: c | {"end_sample": c["start_sample"] + n + 1}, "capture_invalid"),   # interval ≠ audio
    (lambda c, n: c | {"sample_rate": 44100}, "capture_invalid"),
    (lambda c, n: c | {"start_sample": None}, "capture_invalid"),
    (lambda c, n: c | {"end_reason": "because"}, "capture_invalid"),
    (lambda c, n: c | {"segment_id": "x"}, "segment_unknown"),
])
def test_invalid_capture_metadata_is_refused(reader, mutate, code):
    sid, art, _ = new_session(reader)
    data, n = wav48(1.0)
    with pytest.raises(UserError) as e:
        reader.upload_audio(sid, M.new_id(), data, mutate(capture(art["segments"][0]["id"], 0, n), n))
    assert e.value.code == code


@pytest.mark.parametrize("kw", [{"channels": 2}, {"width": 1}])
def test_only_pcm16_mono_wav_is_accepted(reader, kw):
    sid, art, _ = new_session(reader)
    data, n = wav48(0.5, **kw)
    with pytest.raises(UserError) as e:
        reader.upload_audio(sid, M.new_id(), data, capture(art["segments"][0]["id"], 0, n))
    assert e.value.code == "audio_format"
    with pytest.raises(UserError):
        reader.upload_audio(sid, M.new_id(), b"not a wav", capture(art["segments"][0]["id"], 0, 0))


def test_segment_mismatch_with_the_started_attempt(reader):
    sid, art, _ = new_session(reader)
    aid = M.new_id()
    reader.start_attempt(sid, aid, {"segment_id": art["segments"][0]["id"], "run_id": RUN, "sample_rate": 48000,
                                    "start_sample": 0})
    data, n = wav48(1.0)
    with pytest.raises(UserError) as e:
        reader.upload_audio(sid, aid, data, capture(art["segments"][1]["id"], 0, n))
    assert e.value.code == "segment_mismatch"


@pytest.mark.parametrize("seconds, state", [(0.0, "TOO_SHORT"), (0.2, "TOO_SHORT"), (61.0, "REJECTED")])
def test_too_short_and_too_long_are_kept_not_dropped(reader, seconds, state):
    sid, art, _ = new_session(reader)
    seg = art["segments"][0]
    data, n = wav48(seconds, rate=8000)
    aid = M.new_id()
    out = reader.upload_audio(sid, aid, data, capture(seg["id"], 0, n, rate=8000))
    assert out["attempt"]["state"] == state and out["job"] is None
    assert reader.store.audio_path(sid, aid, "original.wav").read_bytes() == data  # the audio is kept
    snap = reader.snapshot(sid)
    assert aid in snap["session"]["attempt_ids"] and snap["segment_states"][seg["id"]] == "NEEDS_ATTENTION"


def test_upload_without_a_start_notice_still_creates_the_attempt(reader):
    sid, art, _ = new_session(reader)
    data, n = wav48(1.0)
    aid = M.new_id()
    out = reader.upload_audio(sid, aid, data, capture(art["segments"][2]["id"], 0, n))
    assert out["attempt"]["segment_id"] == art["segments"][2]["id"] and out["attempt"]["state"] == "QUEUED"


def test_reused_ids_are_rejected(reader):
    sid, art, _ = new_session(reader)
    with pytest.raises(UserError) as e:
        reader.create_session(sid, art["id"])
    assert e.value.code == "session_exists"
    aid, _ = record(reader, sid, art["segments"][0])
    with pytest.raises(UserError) as e:
        reader.start_attempt(sid, aid, {"segment_id": art["segments"][0]["id"]})
    assert e.value.code == "attempt_exists"


def test_session_actions_and_illegal_transitions(reader):
    sid, art, _ = new_session(reader)
    with pytest.raises(UserError) as e:
        reader.session_action(sid, "pause")  # READY → PAUSED is not allowed
    assert e.value.code == "transition_invalid"
    record(reader, sid, art["segments"][0])
    assert reader.worker.wait_idle(10)
    for action, state in (("pause", "PAUSED"), ("resume", "READING"), ("stop", "STOPPED"), ("resume", "READING")):
        assert reader.session_action(sid, action)["session"]["state"] == state
    # FINISH with every analysis done: FINISHED, then the summary is built → SUMMARIZED
    assert reader.session_action(sid, "finish")["session"]["state"] == "SUMMARIZED"
    with pytest.raises(UserError):
        reader.session_action(sid, "pause")  # SUMMARIZED → PAUSED is not allowed
    with pytest.raises(UserError):
        reader.session_action(sid, "explode")
    kinds = [e["kind"] for e in reader.store.events(sid)]
    assert kinds.count("session_state") >= 6


def test_reopen_interrupts_captures_of_another_page_and_late_audio_still_lands(reader):
    sid, art, _ = new_session(reader)
    seg = art["segments"][0]
    aid = M.new_id()
    reader.start_attempt(sid, aid, {"segment_id": seg["id"], "run_id": RUN, "sample_rate": 48000, "start_sample": 0})
    snap = reader.session_action(sid, "reopen", M.new_id())
    att = next(a for a in snap["attempts"] if a["id"] == aid)
    assert att["state"] == "INTERRUPTED" and snap["session"]["state"] == "INTERRUPTED"
    data, n = wav48(1.0)
    out = reader.upload_audio(sid, aid, data, capture(seg["id"], 0, n))
    assert out["attempt"]["state"] == "QUEUED"


def test_failed_analysis_is_kept_and_can_be_retried(reader, engines):
    sid, art, _ = new_session(reader)
    engines["wav2vec2_raw"].fail = True
    aid, out = record(reader, sid, art["segments"][0])
    assert reader.worker.wait_idle(10)
    detail = reader.attempt_detail(sid, aid)
    assert detail["attempt"]["state"] == "ANALYSIS_FAILED" and detail["jobs"][0]["state"] == "FAILED"
    assert detail["jobs"][0]["error"]["message"]
    engines["wav2vec2_raw"].fail = False
    job = reader.retry(sid, aid, out["job"]["id"])
    assert job["try_count"] == 2 and job["retry_of"] == out["job"]["id"]
    assert reader.worker.wait_idle(10)
    detail = reader.attempt_detail(sid, aid)
    assert detail["attempt"]["state"] == "ANALYZED" and [j["state"] for j in detail["jobs"]] == ["FAILED", "SUCCEEDED"]
    with pytest.raises(UserError):
        reader.retry(sid, aid, job["id"])  # only failed jobs


def test_discarded_attempts_stay_visible(reader):
    sid, art, _ = new_session(reader)
    aid, _ = record(reader, sid, art["segments"][0])
    reader.set_disposition(sid, aid, "discarded")
    assert reader.snapshot(sid)["attempts"][0]["user_disposition"] == "discarded"
    with pytest.raises(UserError):
        reader.set_disposition(sid, aid, "deleted")


# ----------------------------------------------------------------------
# Recovery and shutdown
# ----------------------------------------------------------------------

def test_restart_requeues_running_jobs_and_interrupts_live_captures(tmp_path, lab):
    store = ReaderStore(tmp_path / "s")
    r1 = ReaderService(store, lab, start_worker=False)
    sid, art, _ = new_session(r1)
    aid, out = record(r1, sid, art["segments"][0])
    live = M.new_id()
    r1.start_attempt(sid, live, {"segment_id": art["segments"][1]["id"], "run_id": RUN, "sample_rate": 48000,
                                 "start_sample": 48000})
    job = store.load_job(sid, aid, out["job"]["id"])  # simulate a crash during inference
    M.transition(job, M.JOB_TRANSITIONS, "RUNNING", "job")
    store.save_job(job)
    att = store.load_attempt(sid, aid)
    M.transition(att, M.ATTEMPT_TRANSITIONS, "ANALYZING", "attempt")
    store.save_attempt(att)

    r2 = ReaderService(store, lab, start_worker=False)  # "restart"
    assert r2.recovered == {"requeued": 1, "interrupted_attempts": 1, "interrupted_sessions": 1}
    assert store.load_job(sid, aid, out["job"]["id"])["state"] == "QUEUED"
    assert store.load_attempt(sid, live)["state"] == "INTERRUPTED"
    assert store.load_session(sid)["state"] == "INTERRUPTED"
    assert r2.worker.run_pending() == 1
    assert store.load_attempt(sid, aid)["state"] == "ANALYZED"


def test_worker_joins_on_close(tmp_path, lab):
    r = ReaderService(ReaderStore(tmp_path / "s"), lab)
    thread = r.worker._thread
    assert thread.is_alive()
    r.close()
    assert not thread.is_alive()


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------

@pytest.fixture
def reader_server(lab, reader):
    server, thread = start_in_thread(lab, reader=reader)
    yield Client(server.url), reader
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_http_flow(reader_server):
    client, reader = reader_server
    status, data = client.post_json("/api/articles", {"text": TEXT, "title": "T"})
    assert status == 200
    art = data["article"]
    sid = M.new_id()
    status, snap = client.post_json("/api/sessions", {"session_id": sid, "article_id": art["id"]})
    assert status == 200 and snap["session"]["id"] == sid
    seg = art["segments"][0]
    aid = M.new_id()
    status, _ = client.post_json(f"/api/sessions/{sid}/attempts/{aid}/start",
                                 {"segment_id": seg["id"], "run_id": RUN, "sample_rate": 48000, "start_sample": 0})
    assert status == 200
    wav, n = wav48(1.0)
    status, _, body = client.request("POST", f"/api/sessions/{sid}/attempts/{aid}/audio", wav,
                                     {"X-Capture": json.dumps(capture(seg["id"], 0, n))})
    assert status == 202 and json.loads(body)["attempt"]["state"] == "QUEUED"
    assert reader.worker.wait_idle(10)
    status, detail = client.get_json(f"/api/sessions/{sid}/attempts/{aid}")
    assert status == 200 and detail["attempt"]["state"] == "ANALYZED"
    status, headers, audio = client.request("GET", f"/api/sessions/{sid}/attempts/{aid}/audio")
    assert status == 200 and headers["Content-Type"] == "audio/wav" and audio[:4] == b"RIFF"
    status, snap2 = client.get_json(f"/api/sessions/{sid}?since={snap['rev']}")
    assert status == 200 and snap2["unchanged"] is False and snap2["rev"] > snap["rev"]
    status, same = client.get_json(f"/api/sessions/{sid}?since={snap2['rev']}")
    assert same == {"unchanged": True, "rev": snap2["rev"]}
    status, listing = client.get_json("/api/sessions")
    assert listing["sessions"][0]["id"] == sid
    status, st = client.post_json(f"/api/sessions/{sid}/state", {"action": "pause"})
    assert status == 200 and st["session"]["state"] == "PAUSED"


@pytest.mark.parametrize("method, path, body, code, http", [
    ("POST", "/api/articles", {"text": ""}, "article_empty", 400),
    ("POST", "/api/sessions", {"session_id": "nope", "article_id": "0" * 32}, "session_id_invalid", 400),
    ("GET", "/api/sessions/" + "0" * 32, None, "session_unknown", 404),
    ("POST", "/api/sessions/" + "0" * 32 + "/state", {"action": "pause"}, "session_unknown", 404),
])
def test_http_errors(reader_server, method, path, body, code, http):
    client, _ = reader_server
    status, data = client.post_json(path, body) if method == "POST" else client.get_json(path)
    assert status == http and data["error"]["code"] == code


def test_audio_upload_needs_capture_header(reader_server):
    client, reader = reader_server
    sid, art, _ = new_session(reader)
    wav, _ = wav48(0.5)
    status, _, body = client.request("POST", f"/api/sessions/{sid}/attempts/{M.new_id()}/audio", wav)
    assert status == 400 and json.loads(body)["error"]["code"] == "capture_invalid"


def test_reader_routes_404_without_a_reader_and_lab_routes_unchanged(fake_server):
    client, _ = fake_server
    status, data = client.get_json("/api/sessions")
    assert status == 404 and data["error"]["code"] == "reader_unavailable"
    status, _, _ = client.request("PUT", "/api/status", b"")
    assert status == 501
    status, data = client.get_json("/api/status")
    assert status == 200 and data["default_engine"] == "wav2vec2_raw"


def test_post_bodies_are_consumed_on_keep_alive_connections(reader_server):
    """A POST whose body is not read would corrupt the next request on the same connection."""
    import http.client

    client, reader = reader_server
    sid, art, _ = new_session(reader)
    aid, out = record(reader, sid, art["segments"][0])
    assert reader.worker.wait_idle(10)
    host, port = client.base.split("//")[1].split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    for path in (f"/api/sessions/{sid}/attempts/{aid}/compare",
                 f"/api/sessions/{sid}/attempts/{aid}/jobs/{out['job']['id']}/retry"):
        conn.request("POST", path, body=b"{}", headers={"Content-Type": "application/json"})
        conn.getresponse().read()
        conn.request("GET", f"/api/sessions/{sid}")  # same connection
        resp = conn.getresponse()
        assert resp.status == 200, path
        resp.read()
    conn.close()
