"""M12 phase 2: segmentation, domain state machines, identity rules and the durable store."""

import json
import threading

import pytest

from pronunciation_lab.reader import model as M
from pronunciation_lab.reader import segmenter as SG
from pronunciation_lab.reader.store import AlreadyExists, ReaderStore, write_once_bytes

ARTICLE = """The Quiet Revolution

Mr. Smith arrived at 9 a.m. on Monday. He said, "It's done!" Then he left; nobody followed. J. K. Rowling wrote it — e.g. the first book.

1. First point is here. 2. Second one.
"""


# ----------------------------------------------------------------------
# Segmenter
# ----------------------------------------------------------------------

def test_segments_are_sentences_with_exact_offsets():
    segs = SG.segment(ARTICLE)
    assert [s.text for s in segs] == [
        "The Quiet Revolution",
        "Mr. Smith arrived at 9 a.m. on Monday.",
        'He said, "It\'s done!"',
        "Then he left; nobody followed.",
        "J. K. Rowling wrote it — e.g. the first book.",
        "1. First point is here.",
        "2. Second one.",
    ]
    assert [s.paragraph_index for s in segs] == [0, 1, 1, 1, 1, 2, 2]
    for s in segs:
        assert SG.collapse(ARTICLE[s.char_start:s.char_end]) == s.text


def test_segmentation_reconstructs_the_article_and_is_deterministic():
    segs = SG.segment(ARTICLE)
    # every non-whitespace character belongs to exactly one segment, in order
    covered = "".join(ARTICLE[s.char_start:s.char_end] for s in segs)
    assert "".join(covered.split()) == "".join(ARTICLE.split())
    assert all(a.char_end <= b.char_start for a, b in zip(segs, segs[1:]))
    assert SG.segment(ARTICLE) == segs


def test_long_sentences_are_split_within_the_analysis_limit():
    text = "Alpha beta gamma, " * 40 + "the end."
    segs = SG.segment(text)
    assert len(segs) > 1 and all(len(s.text) <= SG.MAX_SEGMENT_CHARS for s in segs)
    assert all(s.text.endswith(",") for s in segs[:-1])  # split at clause marks
    assert "".join("".join(text[s.char_start:s.char_end].split()) for s in segs) == "".join(text.split())


def test_long_sentence_without_punctuation_splits_at_spaces():
    text = "word " * 200
    segs = SG.segment(text)
    assert all(len(s.text) <= SG.MAX_SEGMENT_CHARS for s in segs)
    assert all(" " not in text[s.char_end - 1:s.char_end] for s in segs)


@pytest.mark.parametrize("bad", ["", "   \n\n ", "123. 456.", "— — —"])
def test_articles_without_words_are_rejected(bad):
    with pytest.raises(ValueError):
        SG.segment(bad)


def test_oversized_article_is_rejected():
    with pytest.raises(ValueError):
        SG.segment("a " * (SG.MAX_ARTICLE_CHARS // 2 + 1))


# ----------------------------------------------------------------------
# Identity and state machines
# ----------------------------------------------------------------------

def test_ids():
    assert M.valid_id(M.new_id())
    for bad in ("", "ABC", "0" * 31, "g" * 32, None, 5, "0" * 32 + "\n"):
        assert not M.valid_id(bad)
    assert M.segment_id("a" * 32, 7) == "a" * 32 + ":0007"


def _all_states(table):
    return set(table) | {s for v in table.values() for s in v}


@pytest.mark.parametrize("table, kind", [(M.SESSION_TRANSITIONS, "session"), (M.ATTEMPT_TRANSITIONS, "attempt"),
                                         (M.JOB_TRANSITIONS, "job")])
def test_every_transition_is_either_legal_or_rejected(table, kind):
    states = _all_states(table)
    for a in states:
        for b in states:
            entity = {"id": "x", "state": a}
            if b in table.get(a, set()):
                M.transition(entity, table, b, kind)
                assert entity["state"] == b
            else:
                with pytest.raises(M.IllegalTransition):
                    M.transition(entity, table, b, kind)
                assert entity["state"] == a


# The frozen specification, written out independently of the implementation.
SPEC_SESSION = {
    "READY": {"READING"},
    "READING": {"PAUSED", "STOPPED", "FINISHED", "INTERRUPTED"},
    "PAUSED": {"READING", "STOPPED", "FINISHED", "INTERRUPTED"},
    "STOPPED": {"READING", "FINISHED", "INTERRUPTED"},
    "INTERRUPTED": {"PAUSED", "READING", "STOPPED", "FINISHED"},
    "FINISHED": {"SUMMARIZED", "READING"},
    "SUMMARIZED": {"READING", "SUMMARIZED"},
}
SPEC_ATTEMPT = {
    "CAPTURING": {"RECORDED", "TOO_SHORT", "REJECTED", "INTERRUPTED"},
    "INTERRUPTED": {"RECORDED", "TOO_SHORT", "REJECTED"},
    "RECORDED": {"QUEUED"},
    "QUEUED": {"ANALYZING"},
    "ANALYZING": {"ANALYZED", "ANALYSIS_FAILED", "QUEUED"},
    "ANALYSIS_FAILED": {"QUEUED"},
    "ANALYZED": set(), "TOO_SHORT": set(), "REJECTED": set(),
}
SPEC_JOB = {"QUEUED": {"RUNNING", "CANCELLED"}, "RUNNING": {"SUCCEEDED", "FAILED", "QUEUED"},
            "SUCCEEDED": set(), "FAILED": set(), "CANCELLED": set()}


def test_transition_tables_match_the_frozen_specification():
    assert M.SESSION_TRANSITIONS == SPEC_SESSION
    assert M.ATTEMPT_TRANSITIONS == SPEC_ATTEMPT
    assert M.JOB_TRANSITIONS == SPEC_JOB
    assert M.SESSION_ACTIONS == {"start": "READING", "resume": "READING", "pause": "PAUSED", "stop": "STOPPED",
                                 "finish": "FINISHED"}


def test_terminal_states_have_no_exit():
    for st in ("ANALYZED", "TOO_SHORT", "REJECTED"):
        assert M.ATTEMPT_TRANSITIONS[st] == set()
    for st in M.TERMINAL_JOB_STATES:
        assert M.JOB_TRANSITIONS[st] == set()


def _article():
    return M.make_article(ARTICLE, "T", SG.segment(ARTICLE))


def test_attempt_carries_full_identity_and_target_text():
    art = _article()
    sess = M.make_session(M.new_id(), art, "wav2vec2_raw")
    seg = art["segments"][3]
    att = M.make_attempt(sess, seg, M.new_id(), 1, {"run_id": M.new_id(), "sample_rate": 48000, "start_sample": 0})
    assert (att["session_id"], att["segment_id"], att["target_text"]) == (sess["id"], seg["id"], seg["text"])
    assert att["user_disposition"] is None  # an explicit "kept" is the user's decision, never the default
    job = M.make_job(att, "openpronounce", "primary")
    assert (job["attempt_id"], job["session_id"], job["segment_id"]) == (att["id"], sess["id"], seg["id"])
    ref = M.playback_reference(att, job, [0, 300], [100, 120], "sound")
    assert ref["attempt_id"] == att["id"] and ref["job_id"] == job["id"] and ref["timeline"] == "analysis_wav"


def test_segment_state_is_derived():
    att = {"state": "CAPTURING", "job_ids": []}
    assert M.segment_state([], {}) == "UNREAD"
    assert M.segment_state([att], {}) == "RECORDING"
    for st, exp in (("QUEUED", "PROCESSING"), ("ANALYSIS_FAILED", "NEEDS_ATTENTION"), ("TOO_SHORT", "NEEDS_ATTENTION")):
        assert M.segment_state([att | {"state": st}], {}) == exp
    done = {"state": "ANALYZED", "job_ids": ["j"]}
    for target, exp in (("MATCH", "FEEDBACK_READY"), ("AMBIGUOUS", "NEEDS_ATTENTION"), ("MISMATCH", "NEEDS_ATTENTION")):
        jobs = {"j": {"kind": "primary", "target_confirmation": {"state": target}}}
        assert M.segment_state([done], jobs) == exp


# ----------------------------------------------------------------------
# Store
# ----------------------------------------------------------------------

def test_store_round_trip_and_write_once(tmp_path):
    st = ReaderStore(tmp_path)
    art = _article()
    st.save_article(art)
    assert st.load_article(art["id"]) == art
    with pytest.raises(AlreadyExists):
        st.save_article(art)
    sess = M.make_session(M.new_id(), art, "wav2vec2_raw")
    st.create_session(sess)
    with pytest.raises(AlreadyExists):
        st.create_session(sess)  # a reused session id is rejected
    att = M.make_attempt(sess, art["segments"][1], M.new_id(), 1, {"run_id": M.new_id()})
    st.save_attempt(att)
    assert st.load_attempt(sess["id"], att["id"]) == att
    st.write_audio(sess["id"], att["id"], "original.wav", b"RIFF1")
    with pytest.raises(AlreadyExists):
        st.write_audio(sess["id"], att["id"], "original.wav", b"RIFF2")  # audio is immutable
    assert st.audio_path(sess["id"], att["id"], "original.wav").read_bytes() == b"RIFF1"
    job = M.make_job(att, "wav2vec2_raw", "primary")
    st.save_job(job)
    st.write_result(job, '{"a": 1}')
    with pytest.raises(AlreadyExists):
        st.write_result(job, '{"a": 2}')  # result.json is immutable
    assert st.session_ids() == [sess["id"]]
    assert not list(tmp_path.rglob(".*.*"))  # no temp files left behind


def test_store_rejects_invalid_ids_and_paths(tmp_path):
    st = ReaderStore(tmp_path)
    for bad in ("../x", "a" * 31, "A" * 32):
        with pytest.raises(ValueError):
            st.session_dir(bad)
    with pytest.raises(KeyError):
        st.load_article("../../etc")
    with pytest.raises(ValueError):
        st.write_audio("a" * 32, "b" * 32, "../evil.wav", b"x")


def test_events_log_is_append_only_and_tolerates_a_torn_line(tmp_path):
    st = ReaderStore(tmp_path)
    art = _article()
    sess = M.make_session(M.new_id(), art, "wav2vec2_raw")
    st.create_session(sess)
    st.append_event(sess["id"], "a", x=1)
    st.append_event(sess["id"], "b", x=2)
    with open(st.session_dir(sess["id"]) / "events.jsonl", "a") as f:
        f.write('{"kind": "torn"')
    assert [e["kind"] for e in st.events(sess["id"])] == ["a", "b"]


def test_concurrent_write_once_has_exactly_one_winner(tmp_path):
    target = tmp_path / "x" / "result.json"
    wins, losses = [], []

    def go(i):
        try:
            write_once_bytes(target, f"{i}".encode())
            wins.append(i)
        except AlreadyExists:
            losses.append(i)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1 and len(losses) == 15 and target.read_text() == str(wins[0])


def test_store_files_are_plain_json(tmp_path):
    st = ReaderStore(tmp_path)
    art = _article()
    st.save_article(art)
    assert json.loads((tmp_path / "articles" / f"{art['id']}.json").read_text())["id"] == art["id"]


def test_store_creates_nothing_until_first_write(tmp_path):
    root = tmp_path / "never"
    st = ReaderStore(root)
    assert st.session_ids() == [] and not root.exists()


def test_default_store_is_outside_the_repository():
    from pathlib import Path

    from pronunciation_lab.reader.store import DEFAULT_ROOT
    repo = Path(__file__).resolve().parents[2]
    assert repo not in DEFAULT_ROOT.resolve().parents
