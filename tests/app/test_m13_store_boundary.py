"""M13-A: UserStore / LongitudinalStore boundary without changing on-disk layout or fingerprints."""

from __future__ import annotations

import hashlib

import m9helpers as H
from m10helpers import History, action, ok, personal_baseline
from m9helpers import StoreBuilder

from pronunciation_lab.longitudinal.source import EXTRACTOR_VERSION, PRACTICE_SOURCE, fingerprint
from pronunciation_lab.longitudinal.store import ProgressStore, update
from pronunciation_lab.reader import model as M
from pronunciation_lab.reader.persistence import LongitudinalStore, UserStore
from pronunciation_lab.reader.store import LocalFileStore, ReaderStore


def _protocol_methods(proto) -> tuple[str, ...]:
    return tuple(n for n, v in vars(proto).items() if callable(v) and not n.startswith("_"))


def _pre_m13a_fingerprint(store, sid, aid, session, article):
    """Exact hash that lived in longitudinal.source.fingerprint before M13-A moved it onto the store."""
    adir = store.attempt_dir(sid, aid)
    try:
        h = hashlib.sha1((adir / "attempt.json").read_bytes())
    except FileNotFoundError:
        return None
    for jdir in sorted((adir / "jobs").glob("*")) if (adir / "jobs").is_dir() else []:
        for name in ("job.json",):
            p = jdir / name
            if p.is_file():
                h.update(p.read_bytes())
        v = jdir / "view.json"
        if v.is_file():
            st = v.stat()
            h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
    h.update(f"{session.get('engine_default')}|{(article or {}).get('source')}|{EXTRACTOR_VERSION}".encode())
    return h.hexdigest()


def test_local_file_store_is_reader_store():
    assert LocalFileStore is ReaderStore


def test_reader_store_satisfies_user_store():
    for name in _protocol_methods(UserStore):
        assert hasattr(ReaderStore, name), name


def test_progress_store_satisfies_longitudinal_store():
    for name in _protocol_methods(LongitudinalStore):
        assert hasattr(ProgressStore, name), name


def test_attempt_fingerprint_matches_pre_m13a_algorithm(tmp_path):
    b = StoreBuilder(tmp_path)
    sid = b.session()
    aid = b.attempt(sid, H.numbered(H.sub("think", "θ", "t"), H.ok("plan", "æ", 1)))
    session = b.store.load_session(sid)
    article = b.store.load_article(session["article_id"])
    expected = _pre_m13a_fingerprint(b.store, sid, aid, session, article)
    assert expected is not None
    assert b.store.attempt_fingerprint(sid, aid, session, article) == expected
    assert fingerprint(b.store, sid, aid, session, article) == expected
    assert LocalFileStore(tmp_path).attempt_fingerprint(sid, aid, session, article) == expected


def test_attempt_fingerprint_missing_attempt_is_none(tmp_path):
    st = ReaderStore(tmp_path)
    sid, aid = "a" * 32, "b" * 32
    assert st.attempt_fingerprint(sid, aid, {"engine_default": "wav2vec2_raw"}, None) is None


def test_longitudinal_update_uses_progress_store(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    calls = []
    orig = h.store.progress_store

    def wrapped():
        calls.append(True)
        return orig()

    h.store.progress_store = wrapped
    update(h.store, persist=True, generated_at="t")
    assert calls


def test_record_practice_uses_progress_store(reader):
    b = StoreBuilder(reader.store.root)
    sessions = [b.session() for _ in range(3)]
    for k in range(12):
        w = ["sit", "list", "fill", "bit"][k % 4]
        b.attempt(sessions[k % 3], H.numbered(H.sub(w, "ɪ", "iː") if k < 6 else H.ok("bit", "ɪ"), H.ok("sit", "ɪ", 2)),
                  sentence=f"Sentence {k} with {w}.")
    live = reader.coaching()
    target_id = live["actions"][0]["target"]["target_id"]
    art = reader.create_article("sit list fill bit.\n\nbit fill list sit.", "Practice: x", PRACTICE_SOURCE)
    sid = M.new_id()
    reader.create_session(sid, art["id"], "wav2vec2_raw")
    calls = []
    orig = reader.store.progress_store

    def wrapped():
        calls.append(True)
        return orig()

    reader.store.progress_store = wrapped
    rec = reader.record_practice({"session_id": sid, "target_id": target_id})
    assert calls and rec["practice_session_id"] == sid


def test_filesystem_layout_is_unchanged_after_progress(tmp_path):
    h = History(tmp_path)
    personal_baseline(h)
    h.practice(action(["ɛ→ɪ"]), ["pracwa pracwb."], [ok("ɛ", ["pracwa", "pracwb"])])
    h.progress()
    root = h.store.root
    assert (root / "articles").is_dir()
    assert (root / "sessions").is_dir()
    long = root / "longitudinal"
    assert (long / "cache").is_dir()
    assert (long / "ledger.jsonl").is_file()
    assert (long / "transitions.jsonl").is_file()
    assert (long / "practice").is_dir()
    assert {p.name for p in long.iterdir()} <= {"cache", "ledger.jsonl", "transitions.jsonl", "practice"}
    assert list(root.iterdir())  # only the documented top-level names
    assert {p.name for p in root.iterdir()} <= {"articles", "sessions", "longitudinal"}
