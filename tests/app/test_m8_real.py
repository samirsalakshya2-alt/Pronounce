"""M8 with the real engines: R01–R20 baseline (both engines), controlled variants, M7 isolation, browser.

R01–R20 are read sentences without known disfluencies: a baseline and regression set, not disfluency ground
truth. The controlled variants are spliced from them (m8variants.py) and are generated in temporary folders;
they show that the detector responds to each construction, not how accurate it is on real speech.
"""

import io
import json
import shutil
import subprocess
import wave
from pathlib import Path

import m8variants as V
import numpy as np
import pytest
import soundfile as sf
from apphelpers import DATA_DIR

from pronunciation_lab.app import fluency as F
from pronunciation_lab.app.coach import build_coach
from pronunciation_lab.app.ground_truth import GROUND_TRUTH_IDS, manifest
from pronunciation_lab.app.pipeline import build_analysis_view
from pronunciation_lab.app.reduction import build_reduction
from pronunciation_lab.app.server import start_in_thread
from pronunciation_lab.app.service import AnalysisService
from pronunciation_lab.benchmark.base import safe_analyze
from pronunciation_lab.benchmark.engines import create_engine
from pronunciation_lab.reader import boundary as B
from pronunciation_lab.reader import model as M
from pronunciation_lab.reader.service import ReaderService
from pronunciation_lab.reader.store import ReaderStore

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HAVE_DATA = (DATA_DIR / "benchmark_wav" / "R01.wav").exists()
ENGINES = ("wav2vec2_raw", "openpronounce")
DISFLUENCY = ("FILLER", "REPETITION", "RESTART", "FALSE_START")

pytestmark = pytest.mark.skipif(not HAVE_DATA, reason="benchmark data missing (data/ is untracked)")


@pytest.fixture(scope="module")
def m8real(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("m8")
    svc = AnalysisService(data_dir=DATA_DIR, notes_path=tmp / "notes.jsonl")
    reader = ReaderService(ReaderStore(tmp / "store"), svc)
    server, thread = start_in_thread(svc, reader=reader)
    yield reader, svc, server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    reader.close()
    svc.close()


def _stable(fl):
    return json.dumps(fl, sort_keys=True)


def _analyse(svc, engine, path, text, rid="m8"):
    with svc._lock:
        return safe_analyze(svc._instances[engine], path, text, recording_id=rid)


# ----------------------------------------------------------------------
# R01–R20 baseline, both engines
# ----------------------------------------------------------------------

@pytest.mark.parametrize("engine", ENGINES)
def test_benchmark_baseline(m8real, engine):
    _, svc, _ = m8real
    man = manifest(DATA_DIR)
    for rid in GROUND_TRUTH_IDS:
        path = DATA_DIR / "benchmark_wav" / f"{rid}.wav"
        result = _analyse(svc, engine, path, man[rid]["target_text"], rid)
        before = result.model_dump_json()
        view = build_analysis_view(result, path)
        assert result.model_dump_json() == before, rid  # M8 never mutates the engine result
        fl = view["fluency"]
        assert fl["state"] == "ok" and fl["integrity"]["ok"] and fl["engine"] == engine, (rid, fl["integrity"])
        m = fl["metrics"]
        assert m["rate_available"] and m["activity_reliable"], rid
        assert 1.0 < m["speaking_rate"] <= m["articulation_rate"] < 7.0, (rid, m["speaking_rate"])
        # read sentences: pauses may be noticed, but no filler / repetition / restart / false start
        assert not [o for o in fl["observations"] if o["notice"] and o["type"] in DISFLUENCY], rid
        assert all(o["strength"] in F.STRENGTHS for o in fl["observations"])
        # M4 and M5 are exactly what they are without M8
        coach = build_coach(result)
        coach.pop("_timing_ms", None)
        assert _stable(view["coach"]) == _stable(coach), rid
        red = build_reduction(result, coach["observations"])
        red.pop("timing_ms", None)
        assert _stable(view["reduction"]) == _stable(red), rid


@pytest.mark.parametrize("engine", ENGINES)
def test_repeatable_warm_and_fresh(m8real, engine):
    _, svc, _ = m8real
    man = manifest(DATA_DIR)
    fresh = create_engine(engine)
    for rid in ("R01", "R08", "R17"):
        path = DATA_DIR / "benchmark_wav" / f"{rid}.wav"
        x, sr = sf.read(path, dtype="float32")
        a = F.build_fluency(_analyse(svc, engine, path, man[rid]["target_text"], rid), x, sr)
        b = F.build_fluency(_analyse(svc, engine, path, man[rid]["target_text"], rid), x, sr)
        c = F.build_fluency(safe_analyze(fresh, path, man[rid]["target_text"], recording_id=rid), x, sr)
        assert _stable(a) == _stable(b) == _stable(c), rid


# ----------------------------------------------------------------------
# Controlled variants (cases each construction is detected in with both engines; see the M8 doc for rates)
# ----------------------------------------------------------------------

CASES = [("pause", "R01"), ("filler", "R15"), ("word_rep", "R02"), ("phrase_rep", "R05"), ("restart", "R12"),
         ("false_start", "R07")]


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("kind, rid", CASES)
def test_controlled_variant_is_detected_with_exact_playback(m8real, tmp_path, engine, kind, rid):
    _, svc, _ = m8real
    man = manifest(DATA_DIR)
    x, mark = V.variant(rid, kind, engine)
    path = tmp_path / f"{rid}_{kind}.wav"
    sf.write(path, x, 16000, subtype="PCM_16")
    x16, sr = sf.read(path, dtype="float32")
    fl = F.build_fluency(_analyse(svc, engine, path, man[rid]["target_text"]), x16, sr)
    assert fl["integrity"]["ok"], fl["integrity"]
    lo, hi = mark["start_ms"] - 400, mark["end_ms"] + 400
    at = [o for o in fl["observations"] if o["end_ms"] >= lo and o["start_ms"] <= hi]
    if kind == "pause":
        hits = [o for o in at if o["type"] == "PAUSE" and o["classification"] in ("possible_hesitation", "unusually_long")]
    else:
        hits = [o for o in at if o["type"] in V.EXPECTED[kind] and o["notice"]]
    assert hits, (kind, rid, [(o["type"], o.get("classification")) for o in at])
    for o in hits:  # exact playback: the window covers the observation, inside the recording
        pb = o["playback"]
        assert pb["timeline"] == "analysis_wav" and pb["span_ms"] == [o["start_ms"], o["end_ms"]]
        assert 0 <= pb["play_ms"][0] <= o["start_ms"] <= o["end_ms"] <= pb["play_ms"][1] <= len(x16) * 1000 / sr + 1
        assert o["strength"] != "high" and o["evidence"]
        assert o["start_ms"] < mark["end_ms"] and o["end_ms"] > mark["start_ms"] - 400  # it is the inserted material


def _variant_fluency(svc, engine, tmp_path, rid, kind):
    man = manifest(DATA_DIR)
    x, mark = V.variant(rid, kind, engine)
    path = tmp_path / f"{rid}_{kind}.wav"
    sf.write(path, x, 16000, subtype="PCM_16")
    x16, sr = sf.read(path, dtype="float32")
    fl = F.build_fluency(_analyse(svc, engine, path, man[rid]["target_text"]), x16, sr)
    assert fl["integrity"]["ok"], fl["integrity"]
    return fl, mark


@pytest.mark.parametrize("engine", ENGINES)
def test_two_inserted_pauses_are_each_a_thing_to_notice(m8real, tmp_path, engine):
    _, svc, _ = m8real
    fl, mark = _variant_fluency(svc, engine, tmp_path, "R02", "two_pauses")
    for a, b in mark["spans_ms"]:
        assert [o for o in fl["observations"] if o["type"] == "PAUSE" and o["notice"] and o["end_ms"] >= a - 400
                and o["start_ms"] <= b + 400], (a, b)


@pytest.mark.parametrize("engine", ENGINES)
def test_a_pause_at_the_comma_is_natural_phrasing_not_a_hesitation(m8real, tmp_path, engine):
    _, svc, _ = m8real
    fl, mark = _variant_fluency(svc, engine, tmp_path, "R19", "phrase_pause")
    at = [o for o in fl["observations"] if o["type"] == "PAUSE" and o["end_ms"] >= mark["start_ms"] - 400
          and o["start_ms"] <= mark["end_ms"] + 400]
    assert at and all(o["classification"] in ("natural_boundary", "unusually_long") for o in at)
    assert all(o["strength"] == "low" or not o["notice"] for o in at)  # a long boundary pause: "planning or breathing"


@pytest.mark.parametrize("engine", ENGINES)
def test_slow_or_fast_reading_alone_adds_no_candidates(m8real, tmp_path, engine):
    _, svc, _ = m8real
    clean, _ = _variant_fluency(svc, engine, tmp_path, "R07", "clean")
    for kind, ratio in (("slow", 1 / 1.35), ("fast", 1 / 0.75)):
        fl, _ = _variant_fluency(svc, engine, tmp_path, "R07", kind)
        assert fl["metrics"]["speaking_rate"] / clean["metrics"]["speaking_rate"] == pytest.approx(ratio, rel=0.05), kind
        assert fl["summary"]["notice"] <= clean["summary"]["notice"], (kind, [o["label"] for o in fl["observations"] if o["notice"]])
        assert not [o for o in fl["observations"] if o["type"] in DISFLUENCY and o["notice"]], kind


@pytest.mark.parametrize("engine", ENGINES)
def test_clean_variant_has_no_disfluency_candidates(m8real, engine):
    _, svc, _ = m8real
    man = manifest(DATA_DIR)
    for rid in ("R02", "R07", "R12", "R15"):
        path = DATA_DIR / "benchmark_wav" / f"{rid}.wav"
        x, sr = sf.read(path, dtype="float32")
        fl = F.build_fluency(_analyse(svc, engine, path, man[rid]["target_text"]), x, sr)
        assert not [o for o in fl["observations"] if o["type"] in DISFLUENCY and o["notice"]], rid


# ----------------------------------------------------------------------
# M7 through the reader: continued speech full of repeats never reaches the sentence's fluency
# ----------------------------------------------------------------------

def _wav(x):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def _upload(reader, sid, seg, data):
    with wave.open(io.BytesIO(data)) as w:
        n = w.getnframes()
    aid = M.new_id()
    reader.upload_audio(sid, aid, data, {"segment_id": seg["id"], "run_id": M.new_id(), "sample_rate": 16000,
                                         "start_sample": 0, "end_sample": n, "end_reason": "stopped"})
    return aid


@pytest.mark.parametrize("engine", ENGINES)
def test_disfluent_continuation_stays_out_of_the_sentence(m8real, engine):
    reader, _, _ = m8real
    man = manifest(DATA_DIR)
    s, _ = sf.read(DATA_DIR / "benchmark_wav" / "R01.wav", dtype="float32")
    cont, _ = V.variant("R05", "word_rep", engine)  # the continued speech itself contains a repetition
    x = np.concatenate([s, np.zeros(int(0.4 * 16000), np.float32), cont])
    art = reader.create_article(f"{man['R01']['target_text']} {man['R05']['target_text']}", f"M8 overflow {engine}")
    sid = M.new_id()
    reader.create_session(sid, art["id"], engine)
    aid = _upload(reader, sid, art["segments"][0], _wav(x))
    assert reader.worker.wait_idle(120)
    detail = reader.attempt_detail(sid, aid)
    [job] = detail["jobs"]
    b, fl = job["boundary"], detail["views"][job["id"]]["fluency"]
    assert b["state"] in B.NEEDS_TARGET_ANALYSIS and b["target_analysed"] and not b["feedback_withheld"]
    assert fl["state"] == "ok" and fl["integrity"]["ok"] and fl["region"]["end_ms"] == pytest.approx(b["cut_ms"], abs=0.1)
    assert all(o["end_ms"] <= b["cut_ms"] and o["playback"]["play_ms"][1] <= b["cut_ms"] + 1e-6 for o in fl["observations"])
    assert not [o for o in fl["observations"] if o["type"] in DISFLUENCY]
    [cs] = fl["continued_speech"]
    assert cs["start_ms"] == b["cut_ms"] and cs["decoded_sounds"] > 20 and cs["kind"] == b["regions"][-1]["kind"]
    assert job["fluency"] == fl["compact"] and reader.snapshot(sid)["segment_states"][art["segments"][1]["id"]] == "UNREAD"


# ----------------------------------------------------------------------
# Browser: the note's fluency phrase, Details → Fluency, exact Listen
# ----------------------------------------------------------------------

@pytest.mark.skipif(not Path(CHROME).exists() or shutil.which("node") is None, reason="Chrome/node not installed")
def test_fluency_in_the_reader(m8real, tmp_path):
    reader, _, server = m8real
    man = manifest(DATA_DIR)
    rep, _ = V.variant("R02", "word_rep", "wav2vec2_raw")
    art = reader.create_article(f"{man['R02']['target_text']} {man['R05']['target_text']}", "M8 browser")
    sid = M.new_id()
    reader.create_session(sid, art["id"], "wav2vec2_raw")
    s1, s2 = art["segments"]
    a1 = _upload(reader, sid, s1, _wav(rep))
    a2 = _upload(reader, sid, s2, (DATA_DIR / "benchmark_wav" / "R05.wav").read_bytes())
    assert reader.worker.wait_idle(120)
    j1 = reader.attempt_detail(sid, a1)["jobs"][0]
    v1 = reader.attempt_detail(sid, a1)["views"][j1["id"]]["fluency"]
    expected = [o for o in v1["observations"] if o["notice"]]
    assert j1["fluency"]["notice"] == len(expected) >= 1
    proc = subprocess.run(["node", str(Path(__file__).parent / "js" / "browser_fluency.mjs"), server.url, CHROME,
                           str(DATA_DIR / "benchmark_wav" / "R01.wav"), sid, s1["id"], s2["id"], str(tmp_path / "m8")],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-4000:]
    r = json.loads(proc.stdout)
    n = len(expected)
    assert f"{n} fluency thing{'s' if n != 1 else ''} to notice" in r["note1"]
    assert "fluency" not in r["note2"]  # a clean reading adds nothing to its note
    assert r["sectionTitle"] == f"Fluency ({n})" and r["items"] == [o["label"] for o in expected]
    assert r["summary"] == v1["summary"]["text"] and r["rate"].startswith("Speech rate:")
    ref = r["ref"]
    assert (ref["attempt_id"], ref["job_id"], ref["timeline"]) == (a1, j1["id"], "analysis_wav")
    assert ref["kind"] == "fluency:" + expected[0]["type"] and ref["play_ms"] == expected[0]["playback"]["play_ms"]
    assert r["separate"] and r["articleIntact"] and r["drawers"] == 1
    for banned in ("score", "%", "wrong", "fluent speaker", "rank"):
        assert banned not in r["sectionText"].lower()
    assert (tmp_path / "m8_fluency.png").is_file()
