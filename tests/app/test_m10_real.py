"""M10 with the real engines: R01–R20 read through the reader with BOTH engines into one store.

R01–R20 are not longitudinal learning ground truth (no learning happened between them): these tests check the
longitudinal machinery on real evidence — engine separation, eligibility, evidence classes for repeated texts,
determinism, incremental = rebuild, exact playback, and that M10 never runs inference.
"""

import json

import pytest
from apphelpers import DATA_DIR

from pronunciation_lab.app.ground_truth import GROUND_TRUTH_IDS, manifest
from pronunciation_lab.app.service import AnalysisService
from pronunciation_lab.reader import model as M
from pronunciation_lab.reader.coaching_source import load_inputs
from pronunciation_lab.reader.service import ReaderService
from pronunciation_lab.reader.store import ReaderStore

HAVE_DATA = (DATA_DIR / "benchmark_wav" / "R01.wav").exists()
ENGINES = ("wav2vec2_raw", "openpronounce")
pytestmark = pytest.mark.skipif(not HAVE_DATA, reason="benchmark data missing (data/ is untracked)")


def canon(r):
    return json.dumps({k: v for k, v in r.items() if k not in ("generated_at", "update")}, sort_keys=True, default=str)


@pytest.fixture(scope="module")
def history(tmp_path_factory):
    svc = AnalysisService(data_dir=DATA_DIR, notes_path=tmp_path_factory.mktemp("m10n") / "notes.jsonl")
    reader = ReaderService(ReaderStore(tmp_path_factory.mktemp("m10store")), svc)
    import wave
    man = manifest(DATA_DIR)
    texts = list(dict.fromkeys(man[r]["target_text"] for r in GROUND_TRUTH_IDS))
    for engine in ENGINES:
        art = reader.create_article("\n\n".join(texts), f"Benchmark {engine}")
        seg_by_text = {s["text"]: s for s in art["segments"] if s["readable"]}
        sids = []
        for _ in range(3):
            sid = M.new_id()
            reader.create_session(sid, art["id"], engine)
            sids.append(sid)
        for k, rid in enumerate(GROUND_TRUTH_IDS):
            path = DATA_DIR / "benchmark_wav" / f"{rid}.wav"
            with wave.open(str(path)) as w:
                n = w.getnframes()
            reader.upload_audio(sids[k % 3], M.new_id(), path.read_bytes(),
                                {"segment_id": seg_by_text[man[rid]["target_text"]]["id"], "run_id": M.new_id(),
                                 "sample_rate": 16000, "start_sample": 0, "end_sample": n, "end_reason": "stopped"})
        assert reader.worker.wait_idle(900)
        for sid in sids:
            reader.session_action(sid, "finish")
    yield reader
    reader.close()
    svc.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_r01_r20_longitudinal_on_each_engine(history, engine):
    r = history.progress(engine)
    assert r["integrity"]["ok"], r["integrity"] and r["engine"] == engine
    assert set(r["engines"]) == set(ENGINES) and "not independent confirmation" in r["engine_note"]
    # the eligible readings are exactly M9's eligible readings for this engine (same eligibility, nothing dropped)
    m9 = [i for i in load_inputs(history.store)[0] if i.reading.engine == engine]
    assert r["engines"][engine]["eligible_readings"] == len(m9) == r["history"]["readings"]
    assert r["eligibility"]["eligible"] == len(m9)                         # every other attempt keeps its reason
    classes = r["history"]["classes"]
    assert sum(classes.values()) == len(m9) and classes.get("PRACTICE", 0) == 0
    # one article read in 3 sessions: every sentence counts once as fresh evidence; R01–R20 repeat some texts
    assert r["history"]["fresh_readings"] == len({i.reading.sentence_key for i in m9})
    assert r["history"]["articles"] == 1 and classes.get("REPEAT", 0) == len(m9) - r["history"]["fresh_readings"]
    # one text only: nothing can be called personal (it would need two different texts)
    assert not [it for it in r["patterns"] if it["state"] in ("PERSONAL_RECURRING", "PERSISTENT", "STABLE", "RETIRED")]
    for it in r["patterns"]:
        for e in it["examples"]:
            att = history.store.load_attempt(e["session_id"], e["attempt_id"])
            assert e["job_id"] in att["job_ids"] and history.store.audio_path(e["session_id"], e["attempt_id"]).is_file()
            assert 0 <= e["play_ms"][0] < e["play_ms"][1] <= att["audio"]["duration_ms"] + 1


def test_engine_histories_are_separate_and_differences_traceable(history):
    raw, op = history.progress("wav2vec2_raw"), history.progress("openpronounce")
    raw_obs = {o for it in history.progress_view("wav2vec2_raw", detail=True)["progress"]["patterns"] for o in it["clear_observations"]}
    op_obs = {o for it in history.progress_view("openpronounce", detail=True)["progress"]["patterns"] for o in it["clear_observations"]}
    assert raw_obs and op_obs and not raw_obs & op_obs                     # never pooled
    sessions = {sid: history.store.load_session(sid)["engine_default"] for sid in history.store.session_ids()}
    assert all(sessions[o.split(":")[0]] == "wav2vec2_raw" for o in raw_obs)
    assert all(sessions[o.split(":")[0]] == "openpronounce" for o in op_obs)
    assert raw["input_fingerprint"] == op["input_fingerprint"]             # one store, two engine views


def test_no_inference_determinism_and_rebuild(history, monkeypatch):
    def no_inference(*a, **k):
        raise AssertionError("M10 must not run pronunciation inference")
    monkeypatch.setattr(history.analysis, "_engine", no_inference)
    for engine in ENGINES:
        inc = history.progress(engine)
        assert canon(inc) == canon(history.progress(engine, rebuild=True)) == canon(history.progress(engine))
