"""M10 in the reader: incremental update = full rebuild, append-only logs, immutable sources, the HTTP routes,
explicit practice records, and the M9 integration (presentation layer; M9's own outputs unchanged)."""

import hashlib
import json

import m9helpers as H
import pytest
from apphelpers import Client
from m10helpers import History, W, action, clear, ok, pattern, personal_baseline, quiet_texts
from m9helpers import StoreBuilder, numbered
from readerhelpers import new_session, record

from pronunciation_lab.app.server import start_in_thread
from pronunciation_lab.longitudinal import store as LS
from pronunciation_lab.longitudinal.source import EXTRACTOR_VERSION, PRACTICE_SOURCE
from pronunciation_lab.reader import model as M


def canon(r):
    return json.dumps({k: v for k, v in r.items() if k not in ("generated_at", "update")}, sort_keys=True, default=str)


def source_hashes(root):
    """Every stored file outside longitudinal/ (M4–M9 results, views, audio, summaries, coaching)."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and "longitudinal" not in p.relative_to(root).parts}


# ----------------------------------------------------------------------
# K. Incremental update ≡ full rebuild
# ----------------------------------------------------------------------

def test_incremental_updates_equal_a_full_rebuild_at_every_step(tmp_path):
    h = History(tmp_path)
    steps = [lambda: personal_baseline(h), lambda: quiet_texts(h, texts=2),
             lambda: h.practice(action(["ɛ→ɪ"]), ["pracwa pracwb."], [ok("ɛ", ["pracwa", "pracwb"])]),
             lambda: quiet_texts(h, texts=3, prefix="r"),
             lambda: h.read("text-b0", clear("ɛ", "ɪ", W("b0", 2)))]          # a repeat of an old text
    for step in steps:
        step()
        inc = h.progress()                                           # incremental (persisting the cache)
        assert inc["update"]["mode"] == "incremental"
        assert canon(inc) == canon(h.progress(rebuild=True, persist=False))


def test_incremental_re_extracts_only_new_or_changed_attempts(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    first = h.progress()
    assert first["update"]["extracted"] == 3 and first["update"]["reused"] == 0
    quiet_texts(h, texts=1)
    second = h.progress()
    assert second["update"] == {"mode": "incremental", "extracted": 1, "reused": 3}
    # a changed attempt (discarded later) is re-extracted and its evidence leaves the history
    sid = h.store.session_ids()[0]
    s = h.store.load_session(sid)
    a = h.store.load_attempt(sid, s["attempt_ids"][0])
    a["user_disposition"] = "discarded"
    h.store.save_attempt(a)
    third = h.progress()
    assert third["update"]["extracted"] == 1 and third["eligibility"]["discarded"] == 1
    assert canon(third) == canon(h.progress(rebuild=True, persist=False))


def test_an_old_extractor_version_forces_re_extraction(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    h.progress()
    ps = LS.ProgressStore(h.store.root)
    cache = ps.load_cache()
    assert cache["extractor_version"] == EXTRACTOR_VERSION
    cache["extractor_version"] = "m10-src.0"
    ps.save_cache(cache)
    assert h.progress()["update"]["extracted"] == 3


def test_logs_are_append_only_and_transitions_are_published_once(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    h.progress()
    ps = LS.ProgressStore(h.store.root)
    ledger, log = ps.ledger(), ps.transitions_log()
    assert len(ledger) == 3 and [t["to"] for t in log] == ["EMERGING", "PERSONAL_RECURRING"]
    h.progress()                                                     # nothing new: nothing appended
    assert ps.ledger() == ledger and ps.transitions_log() == log
    quiet_texts(h, texts=2)
    h.progress()
    assert ps.ledger()[:3] == ledger and len(ps.ledger()) == 5
    assert ps.transitions_log()[:2] == log and ps.transitions_log()[-1]["to"] == "IMPROVING"
    assert all(t["versions"].startswith("m10.1/") for t in ps.transitions_log())


def test_m10_never_writes_outside_its_own_directory(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    quiet_texts(h, texts=4)
    before = source_hashes(h.store.root)
    h.progress()
    h.progress(rebuild=True)
    assert source_hashes(h.store.root) == before
    assert {p.name for p in (h.store.root / "longitudinal").iterdir()} <= {"cache", "ledger.jsonl", "transitions.jsonl", "practice"}


def test_persist_false_reads_only(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    h.progress(persist=False)
    assert not (h.store.root / "longitudinal").exists()


# ----------------------------------------------------------------------
# The reader service: summary hook, routes, practice records, M9 integration
# ----------------------------------------------------------------------

def _strong_history(root):
    b = StoreBuilder(root)
    sessions = [b.session() for _ in range(3)]
    for k in range(12):
        w = ["sit", "list", "fill", "bit"][k % 4]
        b.attempt(sessions[k % 3], numbered(H.sub(w, "ɪ", "iː") if k < 6 else H.ok("bit", "ɪ"), H.ok("sit", "ɪ", 2)),
                  sentence=f"Sentence {k} with {w}.")
    return b


def test_finishing_a_reading_updates_the_history_incrementally(reader):
    sid, art, _ = new_session(reader)
    record(reader, sid, art["segments"][0])
    assert reader.worker.wait_idle(10)
    reader.session_action(sid, "finish")
    ps = LS.ProgressStore(reader.store.root)
    assert ps.load_cache()["readings"] and len(ps.ledger()) >= 1


def test_a_failing_history_never_breaks_the_summary(reader, monkeypatch):
    monkeypatch.setattr(LS, "update", lambda *a, **k: 1 / 0)
    sid, art, _ = new_session(reader)
    record(reader, sid, art["segments"][0])
    assert reader.worker.wait_idle(10)
    reader.session_action(sid, "finish")
    assert reader.summary(sid) is not None and reader.store.load_coaching(sid) is not None


def test_m9_outputs_are_unchanged_by_the_longitudinal_layer(reader):
    _strong_history(reader.store.root)
    before = json.dumps(reader.coaching(detail=True), sort_keys=True, default=str)
    reader.progress()
    reader.progress(rebuild=True)
    reader._coaching_cache = None
    after = reader.coaching(detail=True)
    assert json.dumps(after | {"generated_at": json.loads(before)["generated_at"]}, sort_keys=True, default=str) == before


def test_progress_and_practice_routes(reader, lab):
    _strong_history(reader.store.root)
    live = reader.coaching()
    target_id = live["actions"][0]["target"]["target_id"]
    art = reader.create_article("sit list fill bit.\n\nbit fill list sit.", "Practice: x", PRACTICE_SOURCE)
    sid = M.new_id()
    reader.create_session(sid, art["id"], "wav2vec2_raw")
    server, thread = start_in_thread(lab, reader=reader)
    try:
        c = Client(server.url)
        status, body = c.get_json("/api/progress")
        assert status == 200 and body["progress"]["integrity"]["ok"] and "coaching_adaptation" in body
        assert all("clear_observations" not in it for it in body["progress"]["patterns"])
        status, detail = c.get_json("/api/progress?detail=1")
        assert status == 200 and len(detail["progress"]["patterns"]) >= len(body["progress"]["patterns"])
        status, rec = c.post_json("/api/practice", {"session_id": sid, "target_id": target_id})
        assert status == 200 and rec["practice"]["practice_session_id"] == sid
        assert rec["practice"]["advice"]["m9_target_id"] == target_id and rec["practice"]["target"]["patterns"]
        status, again = c.post_json("/api/practice", {"session_id": sid, "target_id": target_id})
        assert again["practice"]["id"] == rec["practice"]["id"]                    # one record per practice session
        sid3 = M.new_id()
        reader.create_session(sid3, art["id"], "wav2vec2_raw")
        status, err = c.post_json("/api/practice", {"session_id": sid3, "target_id": "contrast:nope"})
        assert status == 404                                                     # advice that does not exist
        plain = reader.create_article("An ordinary text.", "T")
        sid2 = M.new_id()
        reader.create_session(sid2, plain["id"], "wav2vec2_raw")
        status, err = c.post_json("/api/practice", {"session_id": sid2, "target_id": target_id})
        assert status == 400                                                     # not started as practice
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_stable_pattern_is_not_shown_as_an_m9_priority(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    quiet_texts(h, texts=6)
    r = h.progress()
    assert pattern(r, "sub:ɛ>ɪ")["state"] == "RETIRED"
    from pronunciation_lab.longitudinal.progress import adapt_coaching
    coaching = {"pool": {"engine": "wav2vec2_raw"}, "actions": [
        {"target": {"target_id": "contrast:ɛ~ɪ", "kind": "CONTRAST", "pairs": ["ɛ→ɪ"]}},
        {"target": {"target_id": "contrast:s~z", "kind": "CONTRAST", "pairs": ["s→z"]}}]}
    a = adapt_coaching(coaching, r)["actions"]
    assert a[0]["prioritised"] is False and a[0]["note"] == "Stable for now in your history, so not prioritised."
    assert a[1]["prioritised"] is True and a[1]["history"] == []           # no history: M9's advice stands as is


@pytest.mark.parametrize("bad", [{}, {"session_id": "x" * 32}, {"target_id": "contrast:x"}])
def test_practice_requests_are_validated(reader, bad):
    from pronunciation_lab.app.service import UserError
    with pytest.raises(UserError):
        reader.record_practice(bad)
