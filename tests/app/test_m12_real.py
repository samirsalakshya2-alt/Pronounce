"""M12 with the real engines: the reader in a real browser (fake microphone fed by benchmark audio),
and reader analyses of the R01–R20 benchmark recordings with both local engines."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from apphelpers import DATA_DIR, Client

from pronunciation_lab.app.server import start_in_thread
from pronunciation_lab.app.service import AnalysisService
from pronunciation_lab.reader.service import ReaderService
from pronunciation_lab.reader.store import ReaderStore

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HAVE_DATA = (DATA_DIR / "benchmark_wav" / "R01.wav").exists()

pytestmark = pytest.mark.skipif(not HAVE_DATA, reason="benchmark data missing (data/ is untracked)")


@pytest.fixture(scope="module")
def reader_real(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("m12")
    svc = AnalysisService(data_dir=DATA_DIR, notes_path=tmp / "notes.jsonl")
    reader = ReaderService(ReaderStore(tmp / "store"), svc)
    server, thread = start_in_thread(svc, reader=reader)
    yield Client(server.url), reader, svc, server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    reader.close()
    svc.close()


def run_driver(name, server, *args, timeout=300):
    proc = subprocess.run(["node", str(Path(__file__).parent / "js" / name), server.url, CHROME, *args],
                          capture_output=True, text=True, timeout=timeout)
    assert proc.returncode == 0, proc.stderr[-4000:]
    return json.loads(proc.stdout)


# ----------------------------------------------------------------------
# Browser: the reader with a fake microphone
# ----------------------------------------------------------------------

@pytest.mark.skipif(not Path(CHROME).exists() or shutil.which("node") is None, reason="Chrome/node not installed")
def test_reader_in_a_real_browser(reader_real, tmp_path):
    client, reader, _, server = reader_real
    r = run_driver("browser_reader.mjs", server, str(DATA_DIR / "benchmark_wav" / "R01.wav"), str(tmp_path / "reader"))

    # the article is the page
    e = r["entry"]
    assert e["title"] == "A browser reading test" and e["source"] == "Benchmark sentences"
    assert e["segments"] == 4 and e["paragraphs"] == 2 and e["url"].startswith("?session=")
    assert e["articleShare"] > 0.6 and e["railWidth"] <= 300 and "Iowan" in e["bodyFont"] and e["lineHeight"] > 1.6
    # the complete article is visible immediately: every sentence, no cards
    assert all(t in e["articleText"] for t in e["segTexts"]) and e["allVisible"] and e["cards"] == 0

    # selecting sentences records them; the cut is synchronous and contiguous
    rd = r["reading"]
    assert rd["armedLabel"] == "Reading sentence 1"
    assert rd["switchMs"] < 16, rd["switchMs"]          # well within one frame
    assert rd["afterSwitch"] == {"first": False, "second": True} and rd["contiguous01"]
    wr = rd["whileRecording"]                                           # recording never hides the article
    assert wr["allVisible"] and wr["cards"] == 0 and all(t in wr["articleText"] for t in e["segTexts"])
    assert rd["afterDouble"] == 2                       # selecting the recording sentence again is a no-op
    assert rd["paused"]["state"] == "ARMED" and rd["paused"]["button"] == "▶ Resume"
    assert rd["resumed"] and rd["stopped"] == {"state": "RELEASED", "tracksLive": False}
    att = rd["attempts"]
    assert [a["reason"] for a in att] == ["switched", "switched", "switched", "paused", "stopped"]
    for prev, cur in zip(att[:4], att[1:4]):
        assert prev["end"] == cur["start"]              # no gap, no overlap while reading on
    assert att[2]["start"] == att[2]["end"]             # rapid switching: empty attempt, kept
    assert att[4]["start"] > att[3]["end"]              # paused audio belongs to no attempt
    assert len({a["run"] for a in att}) == 1

    # the server received every attempt with exactly that interval, analysed in the background
    an = r["analysed"]
    assert an["ok"] and an["uploads"]["failed"] == 0 and an["uploads"]["done"] == 5
    assert [s[1] for s in an["states"]] == ["ANALYZED", "ANALYZED", "TOO_SHORT", "ANALYZED", "ANALYZED"]
    assert [(s[2], s[3]) for s in an["states"]] == [(a["start"], a["end"]) for a in att]
    assert an["progress"] == "4 / 4 sentences read" and an["session"] == "STOPPED"

    sid = r["entry"]["url"].split("=")[1]
    for a in att:  # audio on disk has exactly the owned number of samples
        detail = reader.attempt_detail(sid, a["id"])["attempt"]
        assert detail["audio"]["original_frames"] == a["end"] - a["start"]
        assert detail["capture"]["start_sample"] == a["start"] and detail["capture"]["run_id"] == a["run"]

    # inline feedback: compact line, exact per-attempt playback, evidence via the shared M4/M5 renderers, comparison
    fb = r["feedback"]
    first = att[0]
    assert fb["drawerIds"]["attempt"] == first["id"] and fb["drawerIds"]["job"] and fb["drawerIds"]["timeline"] == "analysis_wav"
    assert fb["compact"] and re.fullmatch(r"(\d+ things? to notice)?( · )?(\d+ to compare)?|Nothing stood out",
                                                      fb["compact"]), fb["compact"]
    ref = fb["listenRef"]
    assert (ref["session_id"], ref["attempt_id"], ref["job_id"], ref["timeline"], ref["kind"]) == \
        (sid, first["id"], fb["drawerIds"]["job"], "analysis_wav", "segment")
    assert ref["play_ms"][0] == 0 and abs(ref["play_ms"][1] - (first["end"] - first["start"]) / 48.0) < 1.0
    if fb["soundRef"]:
        assert fb["soundRef"]["attempt_id"] == first["id"] and fb["soundRef"]["kind"] == "sound"
        assert 0 <= fb["soundRef"]["play_ms"][0] < fb["soundRef"]["play_ms"][1]
    assert fb["cmp"]["error"] is None and "same acoustic model" in fb["cmp"]["note"]
    assert fb["cmp"]["headers"][1:3] == ["wav2vec2_raw", "openpronounce"]
    for banned in ("score", "wrong", "incorrect", "worst", "best "):
        assert banned not in fb["text"].lower(), banned
    assert fb["rail"] and fb["rail"].startswith("Sentence ")

    assert r["keyboard"]["runs"] == 2 and r["keyboard"]["state"] == "RELEASED"  # resume re-acquired the microphone
    assert r["finish"]["session"] == "SUMMARIZED" and r["finish"]["finishDisabled"]
    sm = r["finish"]["summary"]
    assert sm["heading"] == "Your reading" and re.match(r"\d+ of 4 sentences included", sm["lead"])
    for banned in ("score", "wrong", "incorrect", "worst", "rank"):
        assert banned not in sm["text"].lower()
    assert r["reload"]["attempts"] == 6 and r["reload"]["visibleMarks"] == 4  # nothing lost across a reload


# ----------------------------------------------------------------------
# Target confirmation, calibrated on benchmark pairs (both engines)
# ----------------------------------------------------------------------

from pronunciation_lab.app.ground_truth import GROUND_TRUTH_IDS, load_frozen, manifest  # noqa: E402
from pronunciation_lab.benchmark.base import safe_analyze  # noqa: E402
from pronunciation_lab.reader.target import confirm_target  # noqa: E402

ENGINES = ("wav2vec2_raw", "openpronounce")


def _texts():
    man = manifest(DATA_DIR)
    texts = list(dict.fromkeys(r["target_text"] for r in man.values()))
    return man, texts


@pytest.mark.parametrize("engine", ENGINES)
def test_true_pairs_are_confirmed(engine):
    for rid in GROUND_TRUTH_IDS:
        tc = confirm_target(load_frozen(rid, engine, DATA_DIR))
        assert tc["state"] == "MATCH", (rid, tc["evidence"]["support"])


@pytest.mark.parametrize("engine", ENGINES)
def test_mismatched_pairs_are_never_confirmed(reader_real, engine):
    _, _, svc, _ = reader_real
    man, texts = _texts()
    states = []
    for rid in GROUND_TRUTH_IDS:
        other = texts[(texts.index(man[rid]["target_text"]) + 1) % len(texts)]
        with svc._lock:
            res = safe_analyze(svc._instances[engine], DATA_DIR / "benchmark_wav" / f"{rid}.wav", other, recording_id="tc")
        states.append(confirm_target(res)["state"])
    assert "MATCH" not in states
    assert states.count("MISMATCH") >= 15, states  # calibration: 17/20; the rest AMBIGUOUS (re-record offered)


@pytest.mark.parametrize("engine", ENGINES)
def test_partial_reads_are_ambiguous_never_hidden(reader_real, engine, tmp_path):
    import soundfile as sf

    _, _, svc, _ = reader_real
    man, texts = _texts()
    for rid in ("R01", "R05", "R08", "R11", "R14", "R17", "R19"):
        text = man[rid]["target_text"]
        audio, sr = sf.read(DATA_DIR / "benchmark_wav" / f"{rid}.wav", dtype="int16")
        half = tmp_path / f"{rid}.wav"
        sf.write(half, audio[: len(audio) // 2], sr, subtype="PCM_16")
        extra = texts[(texts.index(text) + 2) % len(texts)]
        with svc._lock:
            a = safe_analyze(svc._instances[engine], half, text, recording_id="tc")
            b = safe_analyze(svc._instances[engine], DATA_DIR / "benchmark_wav" / f"{rid}.wav", text + " " + extra,
                             recording_id="tc")
        assert confirm_target(a)["state"] == "AMBIGUOUS", (rid, "half audio")
        assert confirm_target(b)["state"] == "AMBIGUOUS", (rid, "unread sentence")


# ----------------------------------------------------------------------
# The reader on R01–R20 with both engines: same evidence as the lab
# ----------------------------------------------------------------------

def _upload_benchmark(reader, sid, segment, rid):
    import wave

    from pronunciation_lab.reader import model as M
    path = DATA_DIR / "benchmark_wav" / f"{rid}.wav"
    with wave.open(str(path)) as w:
        n, rate = w.getnframes(), w.getframerate()
    aid = M.new_id()
    reader.upload_audio(sid, aid, path.read_bytes(), {"segment_id": segment["id"], "run_id": M.new_id(),
                                                      "sample_rate": rate, "start_sample": 0, "end_sample": n,
                                                      "end_reason": "switched"})
    return aid


def _stable(view):
    view = json.loads(json.dumps(view))
    for k in ("analysis_id", "processing", "source", "audio"):
        view.pop(k, None)
    return view


@pytest.mark.parametrize("engine", ENGINES)
def test_reader_on_the_benchmark_matches_the_lab(reader_real, engine):
    from pronunciation_lab.app.pipeline import build_analysis_view
    from pronunciation_lab.benchmark.schema import PronunciationResult
    from pronunciation_lab.reader import model as M

    _, reader, _, _ = reader_real
    man = manifest(DATA_DIR)
    article = reader.create_article("\n\n".join(man[r]["target_text"] for r in GROUND_TRUTH_IDS), f"Benchmark ({engine})")
    sid = M.new_id()
    reader.create_session(sid, article["id"], engine)
    by_text = {}
    for seg in article["segments"]:
        by_text.setdefault(seg["text"], seg)
    attempts = {rid: _upload_benchmark(reader, sid, by_text[man[rid]["target_text"]], rid) for rid in GROUND_TRUTH_IDS}
    # one recording read against the wrong sentence
    wrong = _upload_benchmark(reader, sid, by_text[man["R05"]["target_text"]], "R01")
    assert reader.worker.wait_idle(300)
    snap = reader.snapshot(sid)
    for rid, aid in attempts.items():
        detail = reader.attempt_detail(sid, aid)
        [job] = detail["jobs"]
        assert detail["attempt"]["state"] == "ANALYZED" and job["engine_id"] == engine
        assert detail["attempt"]["audio"]["conversion"] == "copied"  # 16 kHz PCM: byte-identical analysis audio
        assert job["target_confirmation"]["state"] == "MATCH", (rid, job["target_confirmation"])
        stored = PronunciationResult.model_validate_json(reader.store.result_path(job).read_text())
        frozen = load_frozen(rid, engine, DATA_DIR)
        assert [(p.expected.phoneme, p.observed.top, p.engine_evidence.get("operation"), p.timing.start_ms)
                for w in stored.words for p in w.phonemes] == \
               [(p.expected.phoneme, p.observed.top, p.engine_evidence.get("operation"), p.timing.start_ms)
                for w in frozen.words for p in w.phonemes], rid
        view = detail["views"][job["id"]]
        # M7: a benchmark recording is the sentence only — one inference, the whole attempt analysed
        assert view.pop("boundary")["state"] == "TARGET_ONLY" == job["boundary"]["state"], rid
        assert not reader.store.job_file(job, "target.wav").exists()
        assert _stable(view) == _stable(build_analysis_view(stored, reader.store.audio_path(sid, aid))), rid
        assert view["coach"]["integrity"]["ok"] and view["reduction"]["integrity"]["ok"]
    jw = reader.attempt_detail(sid, wrong)["jobs"][0]
    assert jw["target_confirmation"]["state"] in ("MISMATCH", "AMBIGUOUS")
    seg_wrong = by_text[man["R05"]["target_text"]]["id"]
    assert snap["segment_states"][seg_wrong] == "NEEDS_ATTENTION"

    # the reading summary: one eligible attempt per sentence, primary engine only, exact examples
    sm = reader.session_action(sid, "finish")["summary"]
    assert sm["integrity"]["ok"], sm["integrity"]
    assert sm["engine"] == engine and sm["coverage"]["sentences"] == 20 and sm["coverage"]["read"] == 7
    inputs = {i["segment_id"]: i for i in sm["inputs"]}
    by_seg = {}
    for rid, aid in attempts.items():
        by_seg.setdefault(by_text[man[rid]["target_text"]]["id"], []).append(aid)
    assert len(inputs) == 7
    for seg_id, i in inputs.items():  # the latest eligible attempt of each sentence…
        assert i["attempt_id"] == by_seg[seg_id][-1] and i["target"] == "MATCH"
    assert inputs[seg_wrong]["attempt_id"] == attempts["R07"] != wrong  # …never the newer wrong-sentence attempt
    for r in sm["reductions"]:
        for e in r["examples"]:
            assert e["attempt_id"] in {i["attempt_id"] for i in sm["inputs"]} and e["timeline"] == "analysis_wav"
    # audio integrity: stored bytes are the uploaded bytes; every example window lies inside its attempt's audio
    import hashlib
    for rid, aid in attempts.items():
        a = reader.attempt_detail(sid, aid)["attempt"]
        stored = reader.store.audio_path(sid, aid, "original.wav").read_bytes()
        assert a["audio"]["original_sha256"] == hashlib.sha256(stored).hexdigest() == \
            hashlib.sha256((DATA_DIR / "benchmark_wav" / f"{rid}.wav").read_bytes()).hexdigest()
    durations = {i["attempt_id"]: reader.attempt_detail(sid, i["attempt_id"])["attempt"]["audio"]["analysis_duration_ms"]
                 for i in sm["inputs"]}
    for e in [e for p in sm["patterns"] for e in p["examples"]] + [e for r in sm["reductions"] for e in r["examples"]]:
        assert 0 <= e["play_ms"][0] < e["play_ms"][1] <= durations[e["attempt_id"]] + 1e-6
    # every logged event about a recording names its session's segment too
    for ev in reader.store.events(sid):
        if "attempt_id" in ev:
            assert ev.get("segment_id"), ev
    # the M2 "want to" merge never becomes a reduction in the summary
    want_t = [r for r in sm["reductions"] if r["expected"] == "t" and any(e["word"] == "want" for e in r["examples"])]
    assert all(r["category"] not in ("possible_omission", "possible_connected_speech_reduction") for r in want_t)

    if engine == "wav2vec2_raw":
        # engine comparison through the reader preserves the M2 disagreement (never merged, no winner)
        r01 = attempts["R01"]
        reader.request_comparison(sid, r01)
        assert reader.worker.wait_idle(120)
        cmp = reader.comparison(sid, r01)
        assert cmp["integrity"]["ok"] and cmp["engines"] == ["wav2vec2_raw", "openpronounce"]
        [row] = [r for r in cmp["rows"] if r["word"] == "want" and r["second"] and r["second"]["expected"] == "t"]
        assert row["first"]["observed"] == "t" and row["second"]["observed"] is None
        assert any(n["kind"] == "decoded_separately_elsewhere" for n in row["notes"])
        assert set(cmp["job_ids"]) == {"wav2vec2_raw", "openpronounce"}
        assert "same acoustic model" in cmp["shared_model_note"]


@pytest.mark.skipif(not Path(CHROME).exists() or shutil.which("node") is None, reason="Chrome/node not installed")
def test_mismatch_hides_feedback_until_kept_in_the_browser(reader_real):
    from pronunciation_lab.reader import model as M

    _, reader, _, server = reader_real
    man = manifest(DATA_DIR)
    # M7: the wrong sentence is R19's — its end is still defensible (TARGET_ONLY), so keeping the recording
    # reveals the evidence. (Against R05's text the sentence cannot be located at all and M7 withholds
    # feedback even after Keep: test_m7_real.py::test_kept_wrong_sentence_without_a_defensible_boundary.)
    article = reader.create_article(man["R01"]["target_text"] + " " + man["R19"]["target_text"], "Target check")
    sid = M.new_id()
    reader.create_session(sid, article["id"])
    s1, s2 = article["segments"]
    _upload_benchmark(reader, sid, s1, "R01")    # the right sentence
    _upload_benchmark(reader, sid, s2, "R01")    # read against the wrong sentence
    assert reader.worker.wait_idle(60)
    states = [reader.attempt_detail(sid, a)["jobs"][0]["target_confirmation"]["state"]
              for a in reader.snapshot(sid)["session"]["attempt_ids"]]
    assert states == ["MATCH", "MISMATCH"]
    assert [reader.attempt_detail(sid, a)["jobs"][0]["boundary"]["state"]
            for a in reader.snapshot(sid)["session"]["attempt_ids"]] == ["TARGET_ONLY", "TARGET_ONLY"]
    r = run_driver("browser_target.mjs", server, str(DATA_DIR / "benchmark_wav" / "R01.wav"), sid, s1["id"], s2["id"])
    assert r["match"] == {"ask": False, "details": True, "compact": r["match"]["compact"]} and r["match"]["compact"]
    assert "may not be this sentence" in r["mismatchMark"]["title"] and "to notice" not in r["mismatchMark"]["text"]
    mm = r["mismatch"]
    assert mm["ask"] == "MISMATCH" and mm["keep"] and mm["rerecord"]
    assert not mm["details"] and not mm["compact"]            # no pronunciation feedback by default
    assert "to notice" not in mm["text"]
    assert "may not match" in r["rail"]
    assert r["mismatchAnnotated"] == {"annotated": False, "words": 0, "matchAnnotated": True}  # plain article text
    assert r["afterKeep"]["details"] and r["afterKeep"]["annotated"]   # the user's choice reveals the evidence
    assert r["rerecord"]["recording"] == s2["id"] and r["rerecord"]["drawerHidden"]
    disp = r["dispositions"]
    assert disp[1] == [s2["id"], "rerecord_requested"] and disp[-1][0] == s2["id"]  # old attempt kept in history


# ----------------------------------------------------------------------
# Recovery in the browser
# ----------------------------------------------------------------------

@pytest.mark.skipif(not Path(CHROME).exists() or shutil.which("node") is None, reason="Chrome/node not installed")
def test_microphone_denied(reader_real):
    _, _, _, server = reader_real
    r = run_driver("browser_recovery.mjs", server, str(DATA_DIR / "benchmark_wav" / "R01.wav"), "deny")
    assert r["state"] == "DENIED" and "denied" in r["notice"] and r["label"] == "Microphone blocked"
    assert r["attempts"] == 0 and r["serverAttempts"] == 0


@pytest.mark.skipif(not Path(CHROME).exists() or shutil.which("node") is None, reason="Chrome/node not installed")
def test_device_loss_page_hidden_and_upload_failures(reader_real):
    _, _, _, server = reader_real
    r = run_driver("browser_recovery.mjs", server, str(DATA_DIR / "benchmark_wav" / "R01.wav"), "recover")
    assert r["device"]["state"] == "RELEASED" and r["device"]["reason"] == "interrupted" and "kept" in r["device"]["notice"]
    assert r["hidden"]["state"] == "ARMED" and r["hidden"]["reason"] == "page_hidden" and "hidden" in r["hidden"]["notice"]
    up = r["uploads"]
    assert up["flakyTries"] == 3 and up["status"]["failed"] == 1 and up["retryButton"] and "failed" in up["queueText"]
    assert r["afterRetry"]["failed"] == 0
    reasons = [s[2] for s in r["server"]]
    assert reasons == ["interrupted", "page_hidden", "switched", "paused"]
    assert all(s[1] in ("ANALYZED", "TOO_SHORT") for s in r["server"])  # nothing lost


# ----------------------------------------------------------------------
# The article itself is the pronunciation feedback
# ----------------------------------------------------------------------

@pytest.mark.skipif(not Path(CHROME).exists() or shutil.which("node") is None, reason="Chrome/node not installed")
def test_article_is_the_feedback_view_in_the_browser(reader_real):
    from pronunciation_lab.reader import model as M

    _, reader, _, server = reader_real
    man = manifest(DATA_DIR)
    text = f"{man['R13']['target_text']} {man['R08']['target_text']} {man['R05']['target_text']}\n\n{man['R01']['target_text']}"
    article = reader.create_article(text, "Words", None)
    sid = M.new_id()
    reader.create_session(sid, article["id"], "wav2vec2_raw")
    sa, sb, sc, sd = article["segments"]
    _upload_benchmark(reader, sid, sa, "R13")       # analysed
    _upload_benchmark(reader, sid, sb, "R08")       # analysed
    assert reader.worker.wait_idle(60)              # sc, sd: not analysed
    r = run_driver("browser_words.mjs", server, str(DATA_DIR / "benchmark_wav" / "R01.wav"), sid, sa["id"], sb["id"])

    ld = r["loaded"]                                # the complete article, no clicks, no cards
    assert ld["allVisible"] and ld["texts"] == ld["segTexts"] and ld["paras"] == 2
    assert ld["openBlocks"] == 0 and ld["panels"] == 0
    assert ld["annotated"] == [True, True, False, False]                  # mixed analysed / not analysed
    assert ld["wordsPerSentence"][2:] == [0, 0]                           # unanalysed sentences: plain article text
    assert ld["legend"] == ["Heard as expected", "Heard as a different sound", "Unclear", "Not detected", "Not interpreted"]
    assert re.search(r"\d+ things? to notice", ld["noteA"]) and "Details" in ld["noteA"]   # concise info kept, secondary
    assert ld["recordingState"] == "NO_PERMISSION"

    wa = r["wordsA"]                                # the analysed sentence itself carries the annotations
    assert [w["index"] for w in wa["spans"]] == [w["index"] for w in wa["view"]]          # every target word, in order
    for span, w in zip(wa["spans"], wa["view"]):
        assert span["text"].lower() == w["word"] and f"cat-{w['status']}" in span["cls"]
    assert wa["spans"][0]["text"] == "The" and ld["texts"][0].endswith("months.")          # capitals, punctuation
    assert {"expected", "different", "unclear", "not_detected"} <= {w["status"] for w in wa["view"]}
    assert r["wordsB"]["spans"] == r["wordsB"]["count"] and "cat-not_interpreted" in r["wordsB"]["statuses"]

    f = r["first"]                                  # click a word: its MVP detail, under its sentence
    assert f["visible"] and f["selected"] == 1 and f["underItsSentence"] and f["heading"].startswith("“" + f["word"] + "”")
    assert [(row["first"], row["cls"]) for row in f["rows"]] == [(f"/{e}/", f"cat-{c}") for e, c in f["expectedSounds"]]
    assert f["allVisible"] and f["texts"] == ld["segTexts"] and f["openBlocks"] == 0
    assert f["recordingState"] == "NO_PERMISSION"   # a word click opens evidence; it does not start recording
    ids = r["ids"]
    assert f["panelIds"] == {"attempt": ids["attempt"], "job": ids["job"], "timeline": "analysis_wav"}
    for ref, kind, window in ((r["soundRef"], "sound", r["soundExpected"]), (r["wordRef"], "word", r["wordExpected"])):
        assert (ref["session_id"], ref["attempt_id"], ref["job_id"], ref["timeline"], ref["kind"], ref["play_ms"]) == \
            (sid, ids["attempt"], ids["job"], "analysis_wav", kind, window)

    sc_ = r["second"]                               # another word: the one panel moves
    assert sc_["panelWord"] != sc_["firstPanelWord"] and sc_["selected"] == 1 and sc_["panels"] == 1 and sc_["firstDeselected"]
    assert sc_["heading"].startswith("“" + sc_["word"] + "”") and set(sc_["rowCats"]) == {"cat-expected"}
    for st in ("unclear", "not_detected"):
        assert f"cat-{st}" in r["others"][st]["rowCats"]
    assert r["closed"]["hidden"] and r["closed"]["selected"] == 0 and r["closed"]["allVisible"]   # same word: closes

    dp = r["deeper"]                                # M4, M5, comparison: separate deeper layers
    assert dp["patterns"] and dp["m5"] and dp["compare"] and dp["notInPanel"] and dp["panelBeforeDrawer"]
    assert dp["m5NotInPatterns"] and dp["patternsBeforeM5"] and dp["m5BeforeCompare"]
    assert not dp["duplicateSentence"] and dp["allVisible"]   # no second, parallel copy of the sentence
    assert r["detailsClosed"]

    ni = r["notInterpreted"]
    assert ni and "not interpreted" in ni["heading"] and ni["underB"]
    for banned in ("score", "wrong", "incorrect", "correct", "worst", "best "):
        assert banned not in r["text"].lower(), banned
