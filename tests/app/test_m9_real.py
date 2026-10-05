"""M9 with the real engines: benchmark readings through the reader (both engines), and the browser UI.

R01–R20 are read sentences without labelled pronunciation targets: these tests assert M9's invariants
(integrity, determinism, exact references, disjoint actions, timing), never which target "should" be chosen.
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

import m9helpers as H
import pytest
from apphelpers import DATA_DIR
from m9helpers import StoreBuilder, numbered

from pronunciation_lab.app.ground_truth import GROUND_TRUTH_IDS, manifest
from pronunciation_lab.app.server import start_in_thread
from pronunciation_lab.app.service import AnalysisService
from pronunciation_lab.coaching import run_coaching
from pronunciation_lab.coaching.reading import READING_VERSION, build_reading_feedback
from pronunciation_lab.reader import model as M
from pronunciation_lab.reader.coaching_source import load_inputs
from pronunciation_lab.reader.service import ReaderService
from pronunciation_lab.reader.store import ReaderStore

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HAVE_DATA = (DATA_DIR / "benchmark_wav" / "R01.wav").exists()
ENGINES = ("wav2vec2_raw", "openpronounce")
ORDER = ("areas", "strengths", "fluency", "cautions")


def _full_description_inputs():
    """Six sentences of one synthetic reading: a word-specific difference (2 clear in 'level'), a mixed-evidence
    sound pattern (2 clear, 1 ambiguous), a stable sound, possible hesitations."""
    ws = ["sit", "list", "fill", "bit"]
    fl = H.fluency(H.hesitation(), H.hesitation("of", "the"))
    return [H.inp(H.rd(900 + k, session=9), *([H.sub(ws[k], "ɪ", "iː")] if k < 2 else []),
                  *([H.amb("fit", "ɪ", "iː")] if k == 2 else []), *([H.sub("level", "ɛ", "eɪ")] if k in (3, 4) else []),
                  H.ok("sit", "ɪ"), H.ok("bed", "ɛ"), *H.ok("think", "θ", 2), fluency=fl)
            for k in range(6)]


def _no_area_inputs():
    """Six sentences with one-off differences only, and a stable sound."""
    singles = [H.sub("sit", "ɪ", "iː"), H.sub("we", "w", "v"), H.sub("think", "θ", "t")]
    return [H.inp(H.rd(950 + k, session=10), *([singles[k]] if k < 3 else []), *H.ok("she", "ʃ", 2)) for k in range(6)]

pytestmark = pytest.mark.skipif(not HAVE_DATA, reason="benchmark data missing (data/ is untracked)")


@pytest.fixture(scope="module")
def svc(tmp_path_factory):
    s = AnalysisService(data_dir=DATA_DIR, notes_path=tmp_path_factory.mktemp("m9n") / "notes.jsonl")
    yield s
    s.close()


def _upload(reader, sid, seg, path: Path):
    import wave
    data = path.read_bytes()
    with wave.open(str(path)) as w:
        n = w.getnframes()
    aid = M.new_id()
    reader.upload_audio(sid, aid, data, {"segment_id": seg["id"], "run_id": M.new_id(), "sample_rate": 16000,
                                         "start_sample": 0, "end_sample": n, "end_reason": "stopped"})
    return aid


def _read_benchmark(reader, engine, sessions=3):
    man = manifest(DATA_DIR)
    texts = list(dict.fromkeys(man[r]["target_text"] for r in GROUND_TRUTH_IDS))
    art = reader.create_article("\n\n".join(texts), f"Benchmark {engine}")
    seg_by_text = {s["text"]: s for s in art["segments"] if s["readable"]}
    sids = []
    for k in range(sessions):
        sid = M.new_id()
        reader.create_session(sid, art["id"], engine)
        sids.append(sid)
    for k, rid in enumerate(GROUND_TRUTH_IDS):
        _upload(reader, sids[k % sessions], seg_by_text[man[rid]["target_text"]], DATA_DIR / "benchmark_wav" / f"{rid}.wav")
    assert reader.worker.wait_idle(600)
    for sid in sids:
        reader.session_action(sid, "finish")
    return sids


@pytest.mark.parametrize("engine", ENGINES)
def test_benchmark_history_through_the_reader(svc, tmp_path, engine):
    reader = ReaderService(ReaderStore(tmp_path / "store"), svc)
    try:
        sids = _read_benchmark(reader, engine)
        t = time.perf_counter()
        c = reader.coaching(detail=True)
        elapsed = time.perf_counter() - t
        assert c["integrity"]["ok"], c["integrity"]
        assert c["pool"]["engine"] == engine and c["pool"]["sessions"] == 3 and c["pool"]["readings"] >= 10
        assert c["state"] in ("actions", "no_action") and len(c["actions"]) <= 3
        assert elapsed < 2.0, elapsed                               # aggregation only: no inference
        units = set()
        for a in c["actions"]:
            assert not units & set(a["supporting_unit_ids"])
            units |= set(a["supporting_unit_ids"])
            refs = a["practice"]["examples"] + a["practice"]["counter_examples"] + [s["ref"] for s in a["practice"]["retest_sentences"]]
            for ref in refs:                                        # every reference resolves to stored audio
                att = reader.store.load_attempt(ref["session_id"], ref["attempt_id"])
                assert ref["job_id"] in att["job_ids"] and reader.store.audio_path(ref["session_id"], ref["attempt_id"]).is_file()
                assert 0 <= ref["play_ms"][0] < ref["play_ms"][1] <= att["audio"]["duration_ms"] + 1
        # deterministic: recomputed from the store, the same decision (generated_at aside)
        inputs, excl = load_inputs(reader.store)
        again = run_coaching(inputs, prior_exclusions=excl, generated_at=c["generated_at"])
        assert json.dumps(again, sort_keys=True, default=str) == json.dumps(c, sort_keys=True, default=str)
        # the advice issued with each summary is stored; M4–M8 views were not touched by M9
        for sid in sids:
            assert reader.store.load_coaching(sid) is not None and reader.summary(sid) is not None
    finally:
        reader.close()


@pytest.mark.skipif(not Path(CHROME).exists() or shutil.which("node") is None, reason="Chrome/node not installed")
def test_what_to_practise_now_in_the_browser(svc, tmp_path):
    reader = ReaderService(ReaderStore(tmp_path / "store"), svc)
    server, thread = start_in_thread(svc, reader=reader)
    try:
        # a history with real audio for playback (synthetic evidence), across three sessions
        b = StoreBuilder(tmp_path / "store")
        wav = (DATA_DIR / "benchmark_wav" / "R01.wav").read_bytes()
        hist = [b.session() for _ in range(3)]
        for k in range(12):
            w = ["sit", "list", "fill", "bit"][k % 4]
            b.attempt(hist[k % 3], numbered(H.sub(w, "ɪ", "iː") if k < 6 else H.ok("bit", "ɪ"), H.ok("sit", "ɪ", 2),
                                           H.sub("we", "w", "v") if k % 2 else H.ok("way", "w")),
                      sentence=f"Practice sentence {k} with {w}.", audio=wav)
        # one genuinely read and finished session: its summary carries the issued advice
        man = manifest(DATA_DIR)
        art = reader.create_article(man["R01"]["target_text"], "A real reading")
        sid = M.new_id()
        reader.create_session(sid, art["id"], "wav2vec2_raw")
        _upload(reader, sid, art["segments"][0], DATA_DIR / "benchmark_wav" / "R01.wav")
        assert reader.worker.wait_idle(120)
        reader.session_action(sid, "finish")
        # a second finished session whose "This reading" has every part (areas, strengths, fluency, cautions): a
        # validated description built from synthetic evidence, stored as this session's description (UI order)
        sid2 = M.new_id()
        reader.create_session(sid2, art["id"], "wav2vec2_raw")
        _upload(reader, sid2, art["segments"][0], DATA_DIR / "benchmark_wav" / "R01.wav")
        assert reader.worker.wait_idle(120)
        reader.session_action(sid2, "finish")
        full = build_reading_feedback(_full_description_inputs(), session_id=sid2, coverage={"sentences": 7, "recorded": 7,
                                      "feedback_withheld": 1}, generated_at="t")
        assert full["integrity"]["ok"] and full["improvement_areas"] and full["strengths"] and full["fluency"] and full["cautions"]
        reader.store.save_reading_feedback(sid2, full)
        sid3 = M.new_id()
        reader.create_session(sid3, art["id"], "wav2vec2_raw")
        _upload(reader, sid3, art["segments"][0], DATA_DIR / "benchmark_wav" / "R01.wav")
        assert reader.worker.wait_idle(120)
        reader.session_action(sid3, "finish")
        none = build_reading_feedback(_no_area_inputs(), session_id=sid3, generated_at="t")
        assert none["integrity"]["ok"] and not none["improvement_areas"] and none["no_area_text"] and none["strengths"]
        reader.store.save_reading_feedback(sid3, none)
        live = reader.coaching()
        assert live["state"] == "actions" and live["integrity"]["ok"]
        proc = subprocess.run(["node", str(Path(__file__).parent / "js" / "browser_coaching.mjs"), server.url, CHROME, sid,
                               str(tmp_path / "m9"), sid2, sid3], capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stderr[-4000:]
        r = json.loads(proc.stdout)
        e = r["entry"]
        assert e["visible"] and e["beforeRecent"] and e["count"] == len(live["actions"]) and 1 <= e["count"] <= 3
        assert e["titles"] == [a["action_text"] for a in live["actions"]]
        assert e["evidenceClosed"] and all(1 <= n <= 3 for n in e["examples"]) and all(1 <= n <= 2 for n in e["retest"])
        assert e["ref"]["session_id"] != e["currentSession"] and e["ref"]["session_id"] in hist and e["playing"]
        assert any(x.startswith("Observed: ") for x in e["evidence"]) and any(x.startswith("Hypothesis (inferred)") for x in e["evidence"])
        for banned in ("score", "wrong", "error", "rank"):
            assert banned not in e["text"].lower()
        s = r["summary"]
        assert s["present"] and s["groups"][:2] == ["this_reading", "practice_now"] and not s["oldList"]
        assert s["scopes"][:2] == ["This reading only", "Based on your recent readings"]
        # the real reading: the page shows exactly its stored description, in the fixed order
        rf = reader.store.load_reading_feedback(sid)
        sr = s["reading"]
        assert sr is not None and rf["version"] == READING_VERSION and rf["integrity"]["ok"]
        assert [a["text"] for a in sr["areas"]] == [a["text"] for a in rf["improvement_areas"]]
        assert sr["strengths"] == [x["text"] for x in rf["strengths"]] and sr["cautions"] == [c["text"] for c in rf["cautions"]]
        assert sr["noArea"] == rf["no_area_text"]
        assert sr["subs"] == [x for x in ORDER if x in sr["subs"]]
        # every part present: Major improvement areas → Already stable → Fluency → Cautions → What to practise now
        f = r["full"]
        assert f["groups"][:2] == ["this_reading", "practice_now"]
        assert f["reading"]["subs"] == list(ORDER)
        assert f["reading"]["headings"] == ["Major improvement areas", "Already stable in this reading", "Fluency", "Cautions"]
        assert [a["text"] for a in f["reading"]["areas"]] == [a["text"] for a in full["improvement_areas"]]
        for shown, area in zip(f["reading"]["areas"], full["improvement_areas"]):   # observation / pattern / order kept apart
            assert shown["observations"] == area["evidence_text"] and " ambiguous" in shown["observations"]
            assert shown["pattern"] == area["pattern_text"] and shown["scope"] == area["pattern_scope"]
            assert shown["label"] == area["pattern_label"] and shown["rate"].startswith("Clear-evidence rate: ")
            assert shown["why"] == area["order_text"]
        assert f["reading"]["noArea"] is None and f["reading"]["strengths"] == [x["text"] for x in full["strengths"]]
        assert any(label.endswith("(ambiguous)") for a in f["reading"]["areas"] for label in a["listen"])   # never passed off as clear
        assert [a["scope"] for a in f["reading"]["areas"]] == ["word", "sound"]          # word-specific and broad, told apart
        assert f["reading"]["areas"][0]["why"] == "Placed before area 2 for clearer evidence."
        n = r["noArea"]          # nothing qualified: said explicitly, strengths after it, never in its place
        assert n["noArea"] == "No major pronunciation pattern was strong enough to call out in this reading."
        assert n["areas"] == [] and n["subs"][:2] == ["areas", "strengths"] and n["strengths"] == [x["text"] for x in none["strengths"]]
        for text in (sr["text"], f["reading"]["text"]):
            for banned in ("practise", "should", "consistently", "improving", "recent readings"):
                assert banned not in text.lower(), banned
        issued = reader.store.load_coaching(sid)
        assert s["cards"] == len(issued["actions"])
        p = r["practiceSession"]
        assert "session=" in p["search"] and p["source"] == "What to practise now" and p["title"].startswith("Practice: ")
        assert p["segments"] == [x["text"] for x in live["actions"][0]["practice"]["retest_sentences"]]
        assert (tmp_path / "m9_entry.png").is_file() and (tmp_path / "m9_summary.png").is_file()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        reader.close()
