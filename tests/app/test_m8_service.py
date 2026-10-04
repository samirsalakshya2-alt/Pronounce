"""M8 in the reader: the fluency layer follows M7's regions and withholding, is stored, rebuilt and compared."""

import json

import m7helpers as H
import pytest
from benchmark_fakes import FakeUnavailableEngine, factory
from readerhelpers import RUN, capture

from pronunciation_lab.app import fluency as F
from pronunciation_lab.app.service import AnalysisService
from pronunciation_lab.reader import boundary as B
from pronunciation_lab.reader import model as M
from pronunciation_lab.reader.service import ReaderService
from pronunciation_lab.reader.store import ReaderStore

TEXT = "Think about change. The next thing is this."


@pytest.fixture
def m8engines():
    return {"wav2vec2_raw": H.BoundaryEngine("wav2vec2_raw"), "openpronounce": H.BoundaryEngine("openpronounce"),
            "wavlm": FakeUnavailableEngine("wavlm", "unresolved"),
            "azure_pronunciation": FakeUnavailableEngine("azure_pronunciation", "blocked")}


@pytest.fixture
def m8reader(tmp_path, m8engines):
    lab = AnalysisService(notes_path=tmp_path / "notes.jsonl", engine_factory=factory(m8engines),
                          engine_ids=list(m8engines), workspace=tmp_path / "ws")
    r = ReaderService(ReaderStore(tmp_path / "store"), lab)
    yield r
    r.close()
    lab.close()


def read(reader, audio, text=TEXT, segment=0):
    art = reader.create_article(text, "M8")
    sid = M.new_id()
    reader.create_session(sid, art["id"])
    seg = art["segments"][segment]
    aid = M.new_id()
    data = H.wav16(audio)
    n = (len(data) - 44) // 2
    reader.start_attempt(sid, aid, {"segment_id": seg["id"], "run_id": RUN, "sample_rate": 16000, "start_sample": 0})
    reader.upload_audio(sid, aid, data, capture(seg["id"], 0, n, rate=16000, reason="stopped"))
    assert reader.worker.wait_idle(20)
    detail = reader.attempt_detail(sid, aid)
    job = next(j for j in detail["jobs"] if j["kind"] == "primary")
    return sid, aid, art, detail, job, detail["views"].get(job["id"])


def test_sentence_only_attempt_gets_fluency_over_the_whole_attempt(m8reader):
    sid, aid, _, detail, job, view = read(m8reader, H.attempt_audio(continued=False))
    fl = view["fluency"]
    assert job["boundary"]["state"] == "TARGET_ONLY" and fl["state"] == "ok" and fl["integrity"]["ok"]
    assert fl["region"]["end_ms"] == pytest.approx(view["duration_ms"], abs=1)
    assert fl["m7"] == {"state": "TARGET_ONLY", "target_analysed": False, "feedback_withheld": False,
                        "cut_ms": job["boundary"]["cut_ms"]}
    assert fl["continued_speech"] == [] and job["fluency"] == fl["compact"]
    assert job["pipeline_versions"]["fluency"] == F.FLUENCY_VERSION
    assert {"coach", "reduction", "boundary", "fluency"} <= set(view)


def test_overflow_never_becomes_a_target_observation(m8reader):
    sid, aid, _, detail, job, view = read(m8reader, H.attempt_audio(continued=True))
    b, fl = job["boundary"], view["fluency"]
    assert b["state"] in B.NEEDS_TARGET_ANALYSIS and b["target_analysed"] and fl["state"] == "ok"
    cut = b["cut_ms"]
    assert fl["region"]["end_ms"] == pytest.approx(cut, abs=0.1)
    for o in fl["observations"]:
        assert o["end_ms"] <= cut + 1e-6 and o["playback"]["play_ms"][1] <= cut + 1e-6
    # the target's metrics are the sentence's alone: identical to a recording without continued speech
    _, _, _, _, _, clean = read(m8reader, H.attempt_audio(continued=False))
    for key in ("syllable_count", "speaking_interval_ms", "pause_count", "speaking_rate", "articulation_rate"):
        assert fl["metrics"][key] == clean["fluency"]["metrics"][key], key
    # the continued speech is described separately, with its own exact playback
    [cs] = fl["continued_speech"]
    assert cs["kind"] == b["regions"][1]["kind"] and cs["start_ms"] == cut and cs["end_ms"] == pytest.approx(b["duration_ms"])
    assert cs["decoded_sounds"] == len(H.CONTINUATION.split()) and cs["playback"]["play_ms"] == [cut, cs["end_ms"]]
    assert fl["m7"]["target_analysed"] and fl["m7"]["cut_ms"] == cut


def test_overflow_events_stay_out_of_the_target_even_when_they_look_disfluent(m8reader, m8engines, monkeypatch):
    """A continuation full of 'um' and repeats: none of it may reach the sentence's fluency."""
    monkeypatch.setattr(H, "CONTINUATION", "ʌ m ʌ m ð ə ð ə ð ə n ɛ k s t")
    sid, aid, _, detail, job, view = read(m8reader, H.attempt_audio(continued=True))
    fl, cut = view["fluency"], job["boundary"]["cut_ms"]
    assert job["boundary"]["target_analysed"]
    assert [o for o in fl["observations"] if o["type"] in ("FILLER", "REPETITION", "RESTART")] == []
    assert all(o["end_ms"] <= cut for o in fl["observations"])
    # the whole attempt does contain them: the "um"s and repeats are decoded after the cut (so M7's isolation is
    # what keeps them out of the sentence's fluency)
    from pronunciation_lab.benchmark.schema import PronunciationResult

    full = PronunciationResult.model_validate_json(m8reader.store.result_path(job).read_text())
    after = [s.phone for s in F.decoded_sounds(full) if s.start >= cut]
    assert after[:8] == "ʌ m ʌ m ð ə ð ə".split()


def test_speech_dense_uncertain_boundary_keeps_fluency_inside_the_target(m8reader, m8engines):
    for e in m8engines.values():
        if isinstance(e, H.BoundaryEngine):
            e.dense = True
    sid, aid, _, detail, job, view = read(m8reader, H.dense_audio(),
                                          text="Those efforts fail because they're underresourced. But not here.")
    b, fl = job["boundary"], view["fluency"]
    assert b["state"] == "BOUNDARY_UNCERTAIN" and not b["feedback_withheld"]
    assert fl["m7"]["state"] == "BOUNDARY_UNCERTAIN" and fl["region"]["end_ms"] == pytest.approx(b["cut_ms"], abs=0.1)
    assert all(o["end_ms"] <= b["cut_ms"] for o in fl["observations"])
    assert fl["continued_speech"][0]["kind"] == "uncertain"


def test_withheld_feedback_withholds_fluency(m8reader, monkeypatch):
    real = B.detect_boundary

    def broken(result, samples, sr):
        b = real(result, samples, sr)
        b["regions"][-1]["end_ms"] += 50  # inconsistent: M7 withholds when continuation is plausible
        return b

    monkeypatch.setattr(B, "detect_boundary", broken)
    sid, aid, _, detail, job, view = read(m8reader, H.attempt_audio(continued=True))
    assert job["boundary"]["feedback_withheld"]
    fl = view["fluency"]
    assert fl["state"] == "boundary_withheld" and fl["observations"] == [] and fl["metrics"] is None
    assert "continued_speech" not in fl and fl["m7"]["feedback_withheld"]
    assert job["fluency"]["notice"] == 0 and job["fluency"]["state"] == "boundary_withheld"
    assert m8reader.rebuild_view(job)["fluency"]["state"] == "boundary_withheld"


def test_rebuild_reproduces_fluency_exactly(m8reader):
    for continued in (False, True):
        sid, aid, _, detail, job, view = read(m8reader, H.attempt_audio(continued=continued))
        rebuilt = m8reader.rebuild_view(job)
        assert json.dumps(rebuilt["fluency"], sort_keys=True) == json.dumps(view["fluency"], sort_keys=True)


def test_comparison_shows_each_engines_fluency_side_by_side(m8reader):
    sid, aid, _, detail, job, view = read(m8reader, H.attempt_audio(continued=True))
    m8reader.request_comparison(sid, aid)
    assert m8reader.worker.wait_idle(20)
    cmp = m8reader.comparison(sid, aid)
    fl = cmp["fluency"]
    assert fl["engines"] == ["wav2vec2_raw", "openpronounce"] and fl["integrity"]["ok"]
    assert "not independent confirmation" in fl["agreement_text"]["both_decoding_paths"]
    for r in fl["rows"]:
        assert r["agreement"] in ("both_decoding_paths", "first_only", "second_only")


def test_m4_m5_objects_are_untouched_by_m8(m8reader):
    from pronunciation_lab.app.coach import build_coach
    from pronunciation_lab.benchmark.schema import PronunciationResult

    sid, aid, _, detail, job, view = read(m8reader, H.attempt_audio(continued=False))
    result = PronunciationResult.model_validate_json(m8reader.store.result_path(job).read_text())
    coach = build_coach(result)
    coach.pop("_timing_ms", None)
    assert json.dumps(view["coach"], sort_keys=True) == json.dumps(coach, sort_keys=True)


# ----------------------------------------------------------------------
# M8 in the reading summary (M12): counts, what recurs, a few moments to hear, the rate range — no score
# ----------------------------------------------------------------------

def _obs(oid, type_, start, end, strength="low", notice=True, cls=None, label="Possible hesitation"):
    return {"id": oid, "type": type_, "classification": cls, "label": label, "start_ms": start, "end_ms": end,
            "duration_ms": end - start, "observed": f"{(end - start) / 1000:.2f} s pause", "strength": strength,
            "notice": notice, "playback": {"timeline": "analysis_wav", "span_ms": [start, end],
                                           "play_ms": [start - 250, end + 250], "context_ms": [250, 250]}}


class _Store:
    def __init__(self, views):
        self.views = views

    def load_view(self, job):
        return self.views[job["id"]]


def test_fluency_summary_counts_recurring_kinds_examples_and_rate_range():
    from pronunciation_lab.reader.summary import fluency_summary

    def att(i):
        return {"id": f"a{i}", "session_id": "s", "segment_id": f"g{i}"}
    views = {
        "j0": {"fluency": {"state": "ok", "metrics": {"rate_available": True, "speaking_rate": 2.4},
                           "observations": [_obs("f1", "PAUSE", 1000, 1900, cls="possible_hesitation"),
                                            _obs("f2", "PAUSE", 3000, 3300, notice=False, cls="brief_within_phrase")]}},
        "j1": {"fluency": {"state": "ok", "metrics": {"rate_available": True, "speaking_rate": 3.1},
                           "observations": [_obs("f1", "PAUSE", 500, 2800, "moderate", cls="unusually_long",
                                                 label="Long pause"),
                                            _obs("f2", "PAUSE", 4000, 5100, cls="possible_hesitation"),
                                            _obs("f3", "FILLER", 6000, 6400, label="Possible filler")]}},
        "j2": {"fluency": {"state": "boundary_withheld", "observations": [], "metrics": None}},
    }
    used = [(att(0), {"id": "j0"}), (att(1), {"id": "j1"}), (att(2), {"id": "j2"})]
    f = fluency_summary(_Store(views), used, {"g0": 0, "g1": 1, "g2": 2})
    assert f["sentences_analysed"] == 2 and f["notice"] == 4 and f["sentences_with_notice"] == 2
    assert [(k["kind"], k["count"], k["sentences"]) for k in f["kinds"]] == \
        [("LONG_PAUSE", 1, [2]), ("PAUSE", 2, [1, 2]), ("FILLER", 1, [2])]
    assert f["recurring"] == [{"kind": "PAUSE", "label": "possible hesitation pauses", "sentences": [1, 2]}]
    # moderate evidence first, then the longest: at most three moments, each with an exact playback reference
    assert [(e["sentence"], e["label"]) for e in f["examples"]] == \
        [(2, "Long pause"), (2, "Possible hesitation"), (1, "Possible hesitation")]
    assert all(e["span_ms"] and e["play_ms"] and e["context_ms"] == [250, 250] for e in f["examples"])
    assert f["speech_rate"] == {"min": 2.4, "max": 3.1, "sentences": 2, "unit": "syllables per second"}
    assert f["text"] == ("4 fluency things to notice in 2 of 2 sentences · possible hesitation pauses in 2 sentences · "
                         "speech rate 2.4–3.1 syllables per second.")
    for banned in ("score", "%", "rank", "best", "worst", "fluent"):
        assert banned not in f["text"].lower()


def test_reading_summary_takes_fluency_only_from_included_sentences(m8reader, monkeypatch):
    from pronunciation_lab.reader import service as reader_service
    from pronunciation_lab.reader.summary import validate_summary

    monkeypatch.setattr(reader_service, "confirm_target", lambda result, alternatives=None: {"state": "MATCH", "reason": "t"})
    monkeypatch.setattr(H, "CONTINUATION", "ʌ m ʌ m ð ə ð ə ð ə n ɛ k s t")  # disfluent-looking continued speech
    art = m8reader.create_article(TEXT, "M8")
    sid = M.new_id()
    m8reader.create_session(sid, art["id"])
    ids = []
    for k, continued in enumerate((True, False)):
        seg, aid = art["segments"][k], M.new_id()
        data = H.wav16(H.attempt_audio(continued=continued))
        m8reader.start_attempt(sid, aid, {"segment_id": seg["id"], "run_id": RUN, "sample_rate": 16000,
                                          "start_sample": 0})
        m8reader.upload_audio(sid, aid, data, capture(seg["id"], 0, (len(data) - 44) // 2, rate=16000, reason="stopped"))
        assert m8reader.worker.wait_idle(20)
        ids.append(aid)
    sm = m8reader.session_action(sid, "finish")["summary"]
    f = sm["fluency"]
    assert sm["version"] == "sum-3" and validate_summary(sm, m8reader.store) == []
    assert f["sentences_analysed"] == 2
    views = [m8reader.attempt_detail(sid, a)["views"] for a in ids]
    noticed = [o for v in views for x in v.values() for o in x["fluency"]["observations"] if o["notice"]]
    assert f["notice"] == len(noticed)
    assert not any(k["kind"] in ("FILLER", "REPETITION") for k in f["kinds"])  # the continuation's "um"s: never counted
    assert all(e["attempt_id"] in ids for e in f["examples"])


def test_reading_summary_fluency_ignores_a_withheld_sentence(m8reader, monkeypatch):
    from pronunciation_lab.reader import service as reader_service

    monkeypatch.setattr(reader_service, "confirm_target", lambda result, alternatives=None: {"state": "MATCH", "reason": "t"})
    real = B.detect_boundary

    def broken(result, samples, sr):
        b = real(result, samples, sr)
        b["regions"][-1]["end_ms"] += 50  # inconsistent: withheld, continuation plausible
        return b

    monkeypatch.setattr(B, "detect_boundary", broken)
    sid, aid, _, detail, job, view = read(m8reader, H.attempt_audio(continued=True))
    assert job["boundary"]["feedback_withheld"]
    f = m8reader.session_action(sid, "finish")["summary"]["fluency"]
    assert f["sentences_analysed"] == 0 and f["notice"] == 0 and f["examples"] == []
    assert f["text"] == "No fluency evidence among the sentences included."


def test_reading_summary_fluency_ignores_a_sentence_left_out_for_its_identity(m8reader, monkeypatch):
    # an uncertain identity that the reader did not keep: analysed, with fluency, but not in the summary
    from pronunciation_lab.reader import service as reader_service

    monkeypatch.setattr(reader_service, "confirm_target",
                        lambda result, alternatives=None: {"state": "AMBIGUOUS", "reason": "t"})
    sid, aid, _, detail, job, view = read(m8reader, H.attempt_audio(continued=False))
    assert view["fluency"]["state"] == "ok" and view["fluency"]["metrics"]["rate_available"]
    sm = m8reader.session_action(sid, "finish")["summary"]
    assert sm["inputs"] == [] and sm["fluency"]["sentences_analysed"] == 0 and sm["fluency"]["speech_rate"] is None


def test_summary_validation_rejects_a_fluency_example_from_outside_the_summary(m8reader, monkeypatch):
    from pronunciation_lab.reader import service as reader_service
    from pronunciation_lab.reader.summary import validate_summary

    monkeypatch.setattr(reader_service, "confirm_target", lambda result, alternatives=None: {"state": "MATCH", "reason": "t"})
    sid, aid, _, detail, job, view = read(m8reader, H.attempt_audio(continued=False))
    sm = m8reader.session_action(sid, "finish")["summary"]
    assert validate_summary(sm, m8reader.store) == []
    sm["fluency"]["examples"] = [{"attempt_id": "elsewhere", "job_id": job["id"], "timeline": "analysis_wav",
                                  "play_ms": [0.0, 500.0], "span_ms": [100.0, 400.0]}]
    assert any("not an included attempt" in i for i in validate_summary(sm, m8reader.store))
