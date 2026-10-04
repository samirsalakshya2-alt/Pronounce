"""M7 with the real engines: benchmark recordings followed by continued speech, through the reader and in a browser.

The continuations are other benchmark recordings (another sentence read right after); ground truth for the
sentence is its frozen clean analysis. Recording 49 is not used here.
"""

import io
import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from apphelpers import DATA_DIR

from pronunciation_lab.app.ground_truth import load_frozen, manifest
from pronunciation_lab.app.server import start_in_thread
from pronunciation_lab.app.service import AnalysisService
from pronunciation_lab.benchmark.schema import PronunciationResult
from pronunciation_lab.reader import boundary as B
from pronunciation_lab.reader import model as M
from pronunciation_lab.reader.service import ReaderService
from pronunciation_lab.reader.store import ReaderStore

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HAVE_DATA = (DATA_DIR / "benchmark_wav" / "R01.wav").exists()
ENGINES = ("wav2vec2_raw", "openpronounce")
# (sentence, continuation): the two scenarios of the M7 brief
SCENARIOS = [("R01", "R05"), ("R05", "R11")]

pytestmark = pytest.mark.skipif(not HAVE_DATA, reason="benchmark data missing (data/ is untracked)")


@pytest.fixture(scope="module")
def m7real(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("m7")
    svc = AnalysisService(data_dir=DATA_DIR, notes_path=tmp / "notes.jsonl")
    reader = ReaderService(ReaderStore(tmp / "store"), svc)
    server, thread = start_in_thread(svc, reader=reader)
    yield reader, svc, server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    reader.close()
    svc.close()


def continued(rid, other, gap_s=0.3, lead_s=0.0, tail_s=0.0, rate=16000):
    a, sr = sf.read(DATA_DIR / "benchmark_wav" / f"{rid}.wav", dtype="int16")
    b, _ = sf.read(DATA_DIR / "benchmark_wav" / f"{other}.wav", dtype="int16")
    z = lambda s: np.zeros(int(s * sr), np.int16)  # noqa: E731
    x = np.concatenate([z(lead_s), a, z(gap_s), b, z(tail_s)])
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())
    return buf.getvalue(), len(x), len(a) * 1000.0 / sr


def ops(result):
    return [(p.expected.phoneme, p.engine_evidence.get("operation"), p.observed.top) for w in result.words for p in w.phonemes]


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("rid, other", SCENARIOS)
def test_continued_speech_is_not_analysed_as_the_sentence(m7real, engine, rid, other):
    reader, _, _ = m7real
    man = manifest(DATA_DIR)
    art = reader.create_article(f"{man[rid]['target_text']} {man[other]['target_text']}", f"M7 {rid}+{other}")
    sid = M.new_id()
    reader.create_session(sid, art["id"], engine)
    seg = art["segments"][0]
    data, n, sentence_ms = continued(rid, other)
    aid = M.new_id()
    reader.upload_audio(sid, aid, data, {"segment_id": seg["id"], "run_id": M.new_id(), "sample_rate": 16000,
                                         "start_sample": 0, "end_sample": n, "end_reason": "stopped"})
    assert reader.worker.wait_idle(120)
    detail = reader.attempt_detail(sid, aid)
    [job] = detail["jobs"]
    b = job["boundary"]
    assert job["state"] == "SUCCEEDED" and b["state"] in B.NEEDS_TARGET_ANALYSIS and b["target_analysed"], b
    frozen = load_frozen(rid, engine, DATA_DIR)
    clean_end = max(p.timing.end_ms for w in frozen.words for p in w.phonemes if p.timing.end_ms is not None)
    # the boundary lies between the sentence and the continuation (the continuation starts after the recording + gap)
    assert clean_end - 100 <= b["cut_ms"] <= sentence_ms + 300 + 200, (b["cut_ms"], clean_end, sentence_ms)
    assert b["regions"][-1]["kind"] in ("overflow", "uncertain") and b["regions"][-1]["end_ms"] == pytest.approx(n / 16)
    full = PronunciationResult.model_validate_json(reader.store.result_path(job).read_text())
    target = PronunciationResult.model_validate_json(reader.store.job_file(job, "target_result.json").read_text())
    # the contamination M7 removes: continuation sounds attached to the final word, changed sounds
    full_extra = sum(len(p.engine_evidence.get("extra_heard_phones") or []) for p in full.words[-1].phonemes)
    target_extra = sum(len(p.engine_evidence.get("extra_heard_phones") or []) for p in target.words[-1].phonemes)
    assert full_extra >= 10 and target_extra <= 1, (full_extra, target_extra)
    diff_full = sum(x != y for x, y in zip(ops(full), ops(frozen)))
    diff_target = sum(x != y for x, y in zip(ops(target), ops(frozen)))
    assert diff_target <= 4 and diff_target < diff_full, (diff_target, diff_full)
    view = detail["views"][job["id"]]
    assert view["coach"]["integrity"]["ok"] and view["reduction"]["integrity"]["ok"]
    assert all(o["span_ms"][1] <= b["cut_ms"] + 1 for o in view["coach"]["observations"] if o.get("span_ms"))
    assert job["target_confirmation"]["state"] == "MATCH"
    # nothing was analysed against the next sentence
    snap = reader.snapshot(sid)
    assert snap["segment_states"][art["segments"][1]["id"]] == "UNREAD"


def test_comparison_shares_the_region_and_records_both_assessments(m7real):
    reader, _, _ = m7real
    man = manifest(DATA_DIR)
    art = reader.create_article(f"{man['R01']['target_text']} {man['R05']['target_text']}", "M7 compare")
    sid = M.new_id()
    reader.create_session(sid, art["id"], "wav2vec2_raw")
    data, n, _ = continued("R01", "R05")
    aid = M.new_id()
    reader.upload_audio(sid, aid, data, {"segment_id": art["segments"][0]["id"], "run_id": M.new_id(),
                                         "sample_rate": 16000, "start_sample": 0, "end_sample": n, "end_reason": "stopped"})
    assert reader.worker.wait_idle(120)
    reader.request_comparison(sid, aid)
    assert reader.worker.wait_idle(120)
    detail = reader.attempt_detail(sid, aid)
    p, c = detail["jobs"]
    assert c["boundary"]["source"] == "primary" and c["boundary"]["cut_ms"] == p["boundary"]["cut_ms"]
    other = c["boundary"]["other_engine"]
    assert other["engine"] == "openpronounce" and other["state"] in B.STATES and "acoustic model" in other["note"]
    cmp = reader.comparison(sid, aid)
    assert cmp["integrity"]["ok"] and cmp["engines"] == ["wav2vec2_raw", "openpronounce"]


@pytest.mark.skipif(not Path(CHROME).exists() or shutil.which("node") is None, reason="Chrome/node not installed")
def test_continued_speech_in_a_real_browser(m7real, tmp_path):
    reader, _, server = m7real
    man = manifest(DATA_DIR)
    data, n, sentence_ms = continued("R01", "R05", gap_s=0.4, lead_s=0.3, tail_s=2.0)
    mic = tmp_path / "mic.wav"
    mic.write_bytes(data)
    speak_ms = int(300 + sentence_ms + 400 + 3500)  # the sentence, the pause, then 3.5 s of the next sentence
    text = f"{man['R01']['target_text']} {man['R05']['target_text']}"
    proc = subprocess.run(["node", str(Path(__file__).parent / "js" / "browser_overflow.mjs"), server.url, CHROME, str(mic),
                           str(tmp_path / "m7"), str(speak_ms), text], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-4000:]
    r = json.loads(proc.stdout)
    rd, an, ls = r["reading"], r["analysed"], r["listen"]
    assert len(rd["attempts"]) == 1 and rd["attempts"][0]["reason"] == "stopped" and rd["state"] == "RELEASED"
    assert an["ok"], an
    b = an["boundary"]
    assert b["state"] in B.NEEDS_TARGET_ANALYSIS, b
    after = b["regions"][-1]
    assert after["kind"] in ("overflow", "uncertain") and after["end_ms"] - after["start_ms"] > 2000
    # the subtle note, in the reader's words; the next sentence is untouched
    if b["state"] == "TARGET_PLUS_OVERFLOW":
        assert an["boundaryLine"] == "Continued speech detected after this sentence."
        assert an["boundarySub"] == "This continuation was not included in the pronunciation analysis."
    else:
        assert an["boundaryLine"].startswith("Sentence boundary uncertain")
    assert an["segStates"][1] == "UNREAD" and an["note1Hidden"] and an["words1"] == 0 and not an["annotated1"]
    assert an["words0"] == len(man["R01"]["target_text"].split())
    for banned in ("score", "wrong", "incorrect", "error", "warning"):
        assert banned not in an["note0"].lower()
    # exact playback of the continuation and of the sentence (the attempt's analysis WAV timeline)
    o, t = ls["overflowRef"], ls["targetRef"]
    assert (o["attempt_id"], o["job_id"], o["timeline"], o["kind"]) == (an["attempt"], an["job"], "analysis_wav", after["kind"])
    assert o["play_ms"] == [b["cut_ms"], after["end_ms"]]
    assert t["kind"] == "target" and t["play_ms"] == [0, b["cut_ms"]]
    assert ls["buttons"] == ["target", after["kind"]] and ls["sectionState"] == b["state"]
    assert "Evidence" in ls["sectionText"] and not ls["inPatterns"]
    # after a comparison, Details shows the other model's own boundary assessment (evidence, never merged)
    assert not ls["otherBefore"] and ls["otherAfter"].startswith("openpronounce, on its own: ")
    assert "acoustic model" in ls["otherAfter"]
    assert (tmp_path / "m7_note.png").is_file() and (tmp_path / "m7_details.png").is_file()
    # the server holds the whole attempt; the continuation is preserved, never discarded
    sid = r["entry"]["url"].split("=")[1]
    a = reader.attempt_detail(sid, an["attempt"])["attempt"]
    assert a["audio"]["analysis_duration_ms"] == pytest.approx(after["end_ms"], abs=1)


# ----------------------------------------------------------------------
# Speech-dense reading in a noisy room (the M7 manual-test failure), both engines
# ----------------------------------------------------------------------
# The failing manual attempt: low-frequency room noise ~9 dB below the speech, no pause between sentences;
# the level estimator's noise floor came out at -36 dBFS and the speech itself looked inactive, so
# "no speech after the sentence" vetoed 40 decoded continuation sounds. Low-frequency noise 6 dB below the
# speech's median frame level reproduces that profile (floor about -36 dBFS, coverage < 0.15, decoding
# intact) on the benchmark recordings.

from pronunciation_lab.app.pipeline import build_analysis_view  # noqa: E402

DENSE = [("R01", "R05"), ("R05", "R11"), ("R11", "R14")]


def _speech(rid, engine_id, _reader=None):
    """The recording trimmed to its speech (150 ms before the first decoded sound, 60 ms after the last)."""
    frozen = load_frozen(rid, engine_id, DATA_DIR)
    ph = [p for w in frozen.words for p in w.phonemes if p.timing.start_ms is not None
          and p.engine_evidence.get("operation") != "omission"]
    x, sr = sf.read(DATA_DIR / "benchmark_wav" / f"{rid}.wav", dtype="float32")
    return x[int((ph[0].timing.start_ms - 150) * sr / 1000):int((ph[-1].timing.end_ms + 60) * sr / 1000)]


def room_noise(n, ref_db, below_db=6.0, seed=3):
    from scipy.signal import butter, sosfilt

    noise = sosfilt(butter(4, 250, "lowpass", fs=16000, output="sos"), np.random.default_rng(seed).standard_normal(n))
    return (noise / np.sqrt(np.mean(noise ** 2)) * 10 ** ((ref_db - below_db) / 20)).astype(np.float32)


def dense_reading(parts):
    """Concatenate speech parts with no pause and add the room noise; returns (wav bytes, samples, part ends ms)."""
    x = np.concatenate(parts)
    act = B.speech_activity(parts[0], 16000)
    ref = float(np.median(act["db"][act["active"]]))
    x = np.clip(x + room_noise(len(x), ref), -0.99, 0.99)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    ends = list(np.cumsum([len(p) for p in parts]) * 1000.0 / 16000)
    return buf.getvalue(), len(x), ends


def check_sentence_only(reader, job, view, sentence_end_ms, continuation_start_ms):
    b = job["boundary"]
    assert b["state"] in B.NEEDS_TARGET_ANALYSIS and b["target_analysed"] and not b["feedback_withheld"], b["reasons"]
    assert sentence_end_ms - 150 <= b["cut_ms"] <= continuation_start_ms + 250, (b["cut_ms"], sentence_end_ms)
    target = PronunciationResult.model_validate_json(reader.store.job_file(job, "target_result.json").read_text())
    full = PronunciationResult.model_validate_json(reader.store.result_path(job).read_text())
    assert B.evidence_outside_target(view, target, b["cut_ms"]) == [] and b["containment"]["ok"]
    full_extra = sum(len(p.engine_evidence.get("extra_heard_phones") or []) for p in full.words[-1].phonemes)
    target_extra = sum(len(p.engine_evidence.get("extra_heard_phones") or []) for p in target.words[-1].phonemes)
    assert full_extra >= 10 and target_extra <= 1, (full_extra, target_extra)
    assert all(o["span_ms"][1] <= b["cut_ms"] + 1 for o in view["coach"]["observations"] if o.get("span_ms"))
    assert all(c["where"]["span_ms"][1] <= b["cut_ms"] + 1 for c in view["reduction"]["candidates"])
    return target


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("rid, other", DENSE)
def test_speech_dense_continuation_in_a_noisy_room(m7real, engine, rid, other):
    reader, _, _ = m7real
    man = manifest(DATA_DIR)
    s, c = _speech(rid, engine, reader), _speech(other, engine, reader)
    data, n, ends = dense_reading([s, c])
    art = reader.create_article(f"{man[rid]['target_text']} {man[other]['target_text']}", f"M7 dense {rid}")
    sid = M.new_id()
    reader.create_session(sid, art["id"], engine)
    aid = M.new_id()
    reader.upload_audio(sid, aid, data, {"segment_id": art["segments"][0]["id"], "run_id": M.new_id(),
                                         "sample_rate": 16000, "start_sample": 0, "end_sample": n, "end_reason": "stopped"})
    assert reader.worker.wait_idle(120)
    detail = reader.attempt_detail(sid, aid)
    [job] = detail["jobs"]
    b = job["boundary"]
    # the failure mode is reproduced (the level estimate is unreliable) and no longer decides
    assert b["evidence"]["activity_reliable"] is False and b["evidence"]["post_phones"] >= B.STRONG_CONTINUATION_PHONES
    assert b["state"] != "TARGET_ONLY"
    target = check_sentence_only(reader, job, detail["views"][job["id"]], ends[0], ends[0])
    frozen = load_frozen(rid, engine, DATA_DIR)
    assert sum(x != y for x, y in zip(ops(target), ops(frozen))) <= 8
    assert reader.snapshot(sid)["segment_states"][art["segments"][1]["id"]] == "UNREAD"


@pytest.mark.parametrize("engine", ENGINES)
def test_the_manual_reading_pattern(m7real, engine):
    """Select s1, read it and go on into s2; select s2, read it again and go on into s3; select s3, read it; stop."""
    reader, _, _ = m7real
    man = manifest(DATA_DIR)
    r1, r2, r3 = "R01", "R05", "R11"
    s1, s2, s3 = (_speech(r, engine, reader) for r in (r1, r2, r3))
    early = lambda x: x[: int(2.5 * 16000)]  # noqa: E731 - the first 2.5 s of the next sentence
    parts = [s1, early(s2), s2, early(s3), s3]
    data, n, ends = dense_reading(parts)
    art = reader.create_article(" ".join(man[r]["target_text"] for r in (r1, r2, r3)), f"M7 pattern {engine}")
    sid = M.new_id()
    reader.create_session(sid, art["id"], engine)
    run = M.new_id()
    cuts = [0, int(ends[1] * 16), int(ends[3] * 16), n]  # attempt i = [cuts[i], cuts[i+1]) of one capture run
    with wave.open(io.BytesIO(data)) as w:
        frames = w.readframes(n)
    aids = []
    for i, seg in enumerate(art["segments"][:3]):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(frames[cuts[i] * 2:cuts[i + 1] * 2])
        aid = M.new_id()
        reader.upload_audio(sid, aid, buf.getvalue(), {"segment_id": seg["id"], "run_id": run, "sample_rate": 16000,
                                                       "start_sample": cuts[i], "end_sample": cuts[i + 1],
                                                       "end_reason": "switched" if i < 2 else "stopped"})
        aids.append(aid)
    assert reader.worker.wait_idle(180)
    jobs = [reader.attempt_detail(sid, a) for a in aids]
    # attempt 1: sentence 1 + early sentence 2 -> sentence 1 only
    j1 = jobs[0]["jobs"][0]
    t1 = check_sentence_only(reader, j1, jobs[0]["views"][j1["id"]], ends[0], ends[0])
    assert t1.words[-1].word == "change"
    # attempt 2: sentence 2 (read again) + early sentence 3 -> sentence 2 only
    j2 = jobs[1]["jobs"][0]
    off = cuts[1] / 16.0
    t2 = check_sentence_only(reader, j2, jobs[1]["views"][j2["id"]], ends[2] - off, ends[2] - off)
    assert t2.words[-1].word == "valley"
    # attempt 3: sentence 3 alone -> unchanged pre-M7 behaviour
    j3 = jobs[2]["jobs"][0]
    assert j3["boundary"]["state"] == "TARGET_ONLY" and not j3["boundary"]["feedback_withheld"]
    # every attempt kept its own sentence; nothing advanced on its own
    assert [a["attempt"]["segment_id"] for a in jobs] == [s["id"] for s in art["segments"][:3]]
    for a in jobs:
        assert a["attempt"]["audio"]["original_frames"] == a["attempt"]["capture"]["end_sample"] - a["attempt"]["capture"]["start_sample"]


@pytest.mark.skipif(not Path(CHROME).exists() or shutil.which("node") is None, reason="Chrome/node not installed")
def test_the_manual_reading_pattern_in_a_real_browser(m7real, tmp_path):
    reader, _, server = m7real
    man = manifest(DATA_DIR)
    r1, r2, r3 = "R01", "R05", "R11"
    s1, s2, s3 = (_speech(r, "wav2vec2_raw", reader) for r in (r1, r2, r3))
    pause = np.zeros(int(0.9 * 16000), np.float32)
    lead, tail = np.zeros(int(0.5 * 16000), np.float32), np.zeros(int(3.0 * 16000), np.float32)
    early = lambda x: x[: int(2.5 * 16000)]  # noqa: E731
    parts = [lead, s1, early(s2), pause, s2, early(s3), pause, s3, tail]
    x = np.concatenate(parts)
    act = B.speech_activity(s1, 16000)
    x = np.clip(x + room_noise(len(x), float(np.median(act["db"][act["active"]]))), -0.99, 0.99)
    mic = tmp_path / "mic.wav"
    sf.write(mic, x, 16000, subtype="PCM_16")
    ends = np.cumsum([len(p) for p in parts]) * 1000.0 / 16000
    to_s2, to_s3, stop = ends[3] - 450, ends[6] - 450, ends[7] + 1500  # the middle of each pause
    text = " ".join(man[r]["target_text"] for r in (r1, r2, r3))
    proc = subprocess.run(["node", str(Path(__file__).parent / "js" / "browser_pattern.mjs"), server.url, CHROME, str(mic),
                           str(tmp_path / "m7"), f"{to_s2:.0f},{to_s3:.0f},{stop:.0f}", text],
                          capture_output=True, text=True, timeout=400)
    assert proc.returncode == 0, proc.stderr[-4000:]
    r = json.loads(proc.stdout)
    att = r["reading"]["attempts"]
    assert [a["reason"] for a in att] == ["switched", "switched", "stopped"] and att[0]["end"] == att[1]["start"]
    an = r["analysed"]
    assert an["ok"], an
    p1, p2, p3 = an["per"]
    for p, last in ((p1, "change."), (p2, "valley.")):
        b = p["boundary"]
        assert b["state"] in B.NEEDS_TARGET_ANALYSIS and b["target_analysed"] and not b["feedback_withheld"], b["reasons"]
        assert b["containment"]["ok"] and b["regions"][-1]["kind"] in ("overflow", "uncertain")
        assert p["line"] in ("Continued speech detected after this sentence.",
                             "Sentence boundary uncertain — some continued speech may not be included in this sentence's analysis.")
        job = reader.store.load_job(r["entry"]["url"].split("=")[1], p["attempt"], p["job"])
        view = reader.store.load_view(job)
        target = PronunciationResult.model_validate_json(reader.store.job_file(job, "target_result.json").read_text())
        assert B.evidence_outside_target(view, target, b["cut_ms"]) == []
        assert not any(x.engine_evidence.get("extra_heard_phones") for x in target.words[-1].phonemes)
        assert p["words"] == len(an["sentences"][an["per"].index(p)].split())
    assert p3["boundary"]["state"] == "TARGET_ONLY"
    assert all(st in ("FEEDBACK_READY", "NEEDS_ATTENTION") for st in an["segStates"]), an["segStates"]  # each read once
    for ref, p in zip(r["listen"], (p1, p2)):  # each continuation plays exactly, from its own attempt
        after = p["boundary"]["regions"][-1]
        assert ref["attempt_id"] == p["attempt"] and ref["play_ms"] == [after["start_ms"], after["end_ms"]]
    for banned in ("score", "wrong", "incorrect", "error", "warning"):
        assert all(banned not in p["note"].lower() for p in an["per"])
    assert (tmp_path / "m7_pattern.png").is_file()


@pytest.mark.parametrize("engine", ENGINES)
def test_kept_wrong_sentence_without_a_defensible_boundary(m7real, engine):
    """R01 read against R05's text: the sentence cannot be located, so its end cannot be placed. M12 asks
    Keep / Re-record (MISMATCH); keeping answers "is this my sentence", not "where does it end" — feedback
    stays withheld rather than analysing a recording that may contain other speech as the sentence."""
    reader, _, _ = m7real
    man = manifest(DATA_DIR)
    art = reader.create_article(f"{man['R01']['target_text']} {man['R05']['target_text']}", f"M7 kept {engine}")
    sid = M.new_id()
    reader.create_session(sid, art["id"], engine)
    data = (DATA_DIR / "benchmark_wav" / "R01.wav").read_bytes()
    with wave.open(io.BytesIO(data)) as w:
        n = w.getnframes()
    aid = M.new_id()
    reader.upload_audio(sid, aid, data, {"segment_id": art["segments"][1]["id"], "run_id": M.new_id(),
                                         "sample_rate": 16000, "start_sample": 0, "end_sample": n, "end_reason": "stopped"})
    assert reader.worker.wait_idle(120)
    [job] = reader.attempt_detail(sid, aid)["jobs"]
    b = job["boundary"]
    assert job["target_confirmation"]["state"] == "MISMATCH"
    assert b["state"] == "BOUNDARY_UNCERTAIN" and b["feedback_withheld"] and not b["target_analysed"]
    assert any("too unclearly" in r for r in b["reasons"])
    reader.set_disposition(sid, aid, "kept")
    detail = reader.attempt_detail(sid, aid)
    view = detail["views"][job["id"]]
    assert detail["attempt"]["user_disposition"] == "kept"
    assert view["state"] == "boundary_withheld" and view["message"] == B.WITHHELD_MESSAGE and not view["words"]
    reader.session_action(sid, "finish")
    sm = reader.summary(sid)
    assert sm["inputs"] == [] and sm["coverage"]["not_included"][0]["reason"] == "sentence boundary uncertain"
