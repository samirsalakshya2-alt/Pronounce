"""M5 on real audio: R01–R20 (ground truth) with BOTH local engines, the M2 "want to" disagreement,
live vs frozen equality, performance, and the browser UI.

Frozen M2 results (data/benchmark_results, untracked) give both engines' evidence without new
inference; live tests run the real engines through the app. "New Recording 49" is a UI / smoke /
performance check only — its target text is unknown, so nothing about its findings is asserted.
"""

import json
import time
from pathlib import Path

import pytest
from apphelpers import DATA_DIR

from pronunciation_lab.app.engine_compare import compare, validate_comparison
from pronunciation_lab.app.ground_truth import FROZEN_RUN, GROUND_TRUTH_IDS, load_frozen, require_ground_truth
from pronunciation_lab.app.phoneme_coach import build_observations
from pronunciation_lab.app.reduction import CATEGORIES, build_reduction

ENGINES = ("wav2vec2_raw", "openpronounce")
CELLS = DATA_DIR / "benchmark_results" / "runs" / FROZEN_RUN / "cells"
REC49 = Path.home() / "Downloads" / "New Recording 49.m4a"
# Smoke-test input only (ASR-reconstructed, not the user's target text): never ground truth.
SMOKE_TEXT49 = ("Although the initial results were encouraging, we still need to examine the underlying data before making a "
                "final decision. The team wants to understand whether the changes will improve customer service without "
                "increasing transportation costs.")

pytestmark = pytest.mark.skipif(not (CELLS / "R01__wav2vec2_raw.json").exists(), reason="frozen M2 results missing")

REDUCTION_CATEGORIES = {"possible_omission", "possible_weakening", "possible_compression",
                        "possible_connected_speech_reduction", "possible_coarticulation", "possible_substitution"}


def frozen(rid, engine):
    res = load_frozen(rid, engine, DATA_DIR)
    obs = build_observations(res)
    return res, obs, build_reduction(res, obs)


def side(rid, engine):
    res, obs, red = frozen(rid, engine)
    return {"engine": engine, "observations": obs, "reduction": red, "text": res.recording.target.text,
            "duration_ms": res.recording.audio.duration_ms}


def without_timing(red):
    red = json.loads(json.dumps(red))
    red.pop("timing_ms", None)
    return red


# ----------------------------------------------------------------------
# Frozen evidence: both engines, all 20 recordings
# ----------------------------------------------------------------------

@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("rid", GROUND_TRUTH_IDS)
def test_reduction_on_every_benchmark_recording(rid, engine):
    res, obs, red = frozen(rid, engine)
    assert red["state"] == "ok" and red["integrity"]["ok"], red["integrity"]
    assert red["engine"]["id"] == engine and all(c["raw_observation"]["engine"] == engine for c in red["candidates"])
    by_id = {o["id"]: o for o in obs}
    for c in red["candidates"]:
        it = c["interpretation"]
        assert it["category"] in CATEGORIES
        if it["category"] != "insufficient_evidence":
            assert len(it["streams"]) >= 2, c["id"]
        assert by_id[c["observation_id"]]["type"] != "not_interpreted"
        assert 0 <= c["where"]["play_ms"][0] <= c["where"]["span_ms"][0] < c["where"]["play_ms"][1] <= res.recording.audio.duration_ms
        if engine == "openpronounce":  # reports no stress: never "unstressed"
            assert c["evidence"]["context"]["stress"] is None
            assert "unstressed_vowel" not in c["evidence"]["context"]["processes"]
    # deterministic
    assert without_timing(red) == without_timing(frozen(rid, engine)[2])


@pytest.mark.parametrize("rid", GROUND_TRUTH_IDS)
def test_cross_engine_comparison_on_every_benchmark_recording(rid):
    a, b = side(rid, "wav2vec2_raw"), side(rid, "openpronounce")
    cmp = compare(a, b)
    assert validate_comparison(cmp, a, b) == []
    assert cmp["engines"] == list(ENGINES)


def test_speaking_rate_matches_the_m2_measure():
    from pronunciation_lab.benchmark.analysis import cell_measurements
    from pronunciation_lab.benchmark.cells import BenchmarkCell

    for rid in ("R01", "R08", "R18"):
        for engine in ENGINES:
            cell = BenchmarkCell.model_validate(json.loads((CELLS / f"{rid}__{engine}.json").read_text())["cell"])
            m2 = cell_measurements(cell)
            red = frozen(rid, engine)[2]
            assert red["speaking_rate"]["pauses"] == m2["pauses"], (rid, engine)
            assert red["speaking_rate"]["phones_per_s_excluding_pauses"] == pytest.approx(m2["phones_per_s_excluding_pauses"])


# ----------------------------------------------------------------------
# M2 regression: "want to" (R01–R04)
# ----------------------------------------------------------------------

def _sound(obs, word, expected):
    return [o for o in obs if o["kind"] == "sound" and o["word"] == word and o["expected"] == expected]


@pytest.mark.parametrize("rid", ["R01", "R02", "R03", "R04"])
def test_want_to_raw_engine_decodes_two_separate_t(rid):
    _, obs, red = frozen(rid, "wav2vec2_raw")
    [want_t], [to_t] = _sound(obs, "want", "t"), _sound(obs, "to", "t")
    assert want_t["observed"] == "t" and to_t["observed"] == "t"
    assert want_t["timing_source"] == to_t["timing_source"] == "engine"
    assert want_t["span_ms"][1] <= to_t["span_ms"][0]


@pytest.mark.parametrize("rid", ["R01", "R02", "R03", "R04"])
def test_want_to_openpronounce_merge_is_never_a_swallowed_t(rid):
    _, obs, red = frozen(rid, "openpronounce")
    [want_t], [to_t] = _sound(obs, "want", "t"), _sound(obs, "to", "t")
    assert want_t["observed"] is None and to_t["observed"] == "t"  # merged /t/ span assigned to "to"
    [c] = [c for c in red["candidates"] if c["observation_id"] == want_t["id"]]
    assert c["interpretation"]["merge_suspect"]
    assert c["interpretation"]["category"] in ("insufficient_evidence", "ambiguous")
    assert c["interpretation"]["category"] not in REDUCTION_CATEGORIES


@pytest.mark.parametrize("rid", ["R01", "R02", "R03", "R04"])
def test_want_to_disagreement_is_preserved_in_the_comparison(rid):
    a, b = side(rid, "wav2vec2_raw"), side(rid, "openpronounce")
    cmp = compare(a, b)
    [row] = [r for r in cmp["rows"] if r["word"] == "want" and r["second"]["expected"] == "t"]
    assert row["first"]["observed"] == "t" and row["second"]["observed"] is None
    [note] = [n for n in row["notes"] if n["kind"] == "decoded_separately_elsewhere"]
    assert note["decoded_by"] == "wav2vec2_raw" and note["not_decoded_by"] == "openpronounce"
    assert "kept, not resolved" in note["text"]


def test_need_to_voicing_partner_merge_r19():
    """Second instance of the same mechanism: raw decodes [d] for the /t/ of 'to'; OpenPronounce merges it into 'need'."""
    _, raw_obs, _ = frozen("R19", "wav2vec2_raw")
    [raw_t] = _sound(raw_obs, "to", "t")
    assert raw_t["observed"] == "d" and raw_t["timing_source"] == "engine"
    _, obs, red = frozen("R19", "openpronounce")
    [t] = _sound(obs, "to", "t")
    [c] = [c for c in red["candidates"] if c["observation_id"] == t["id"]]
    assert c["interpretation"]["merge_suspect"] and c["interpretation"]["category"] == "ambiguous"
    cmp = compare(side("R19", "wav2vec2_raw"), side("R19", "openpronounce"))
    [row] = [r for r in cmp["rows"] if r["word"] == "to" and r["second"] and r["second"]["expected"] == "t"]
    assert any(n["kind"] == "decoded_separately_elsewhere" for n in row["notes"])


def test_recording_49_is_not_ground_truth():
    with pytest.raises(ValueError):
        require_ground_truth("New Recording 49", DATA_DIR)
    with pytest.raises(ValueError):
        load_frozen("New Recording 49", "wav2vec2_raw", DATA_DIR)


# ----------------------------------------------------------------------
# Live engines through the app
# ----------------------------------------------------------------------

@pytest.mark.parametrize("rid", ["R01", "R03"])
def test_live_reduction_equals_frozen_for_both_engines(real_server, rid):
    client, _ = real_server
    status, view = client.post_json("/api/analyze-benchmark", {"recording_id": rid})
    assert status == 200
    status, cmp = client.post_json(f"/api/analyses/{view['analysis_id']}/compare", {})
    assert status == 200 and cmp["integrity"]["ok"]
    for engine in ENGINES:
        assert without_timing(cmp["reductions"][engine]) == without_timing(frozen(rid, engine)[2]), engine
    assert cmp["rows"] == compare(side(rid, "wav2vec2_raw"), side(rid, "openpronounce"))["rows"]


def test_live_performance_both_engines(real_server):
    client, _ = real_server
    status, view = client.post_json("/api/analyze-benchmark", {"recording_id": "R18"})
    assert status == 200
    t0 = time.perf_counter()
    status, cmp = client.post_json(f"/api/analyses/{view['analysis_id']}/compare", {})
    wall = (time.perf_counter() - t0) * 1000
    assert status == 200
    for engine in ENGINES:
        p = cmp["processing"][engine]
        assert p["inference_ms"] and p["inference_ms"] > 0
        assert p["reduction_ms"] < 50.0 and p["reduction_ms"] < 0.1 * p["inference_ms"], (engine, p)
    assert wall < 30_000


def test_recording_49_smoke_only(real_server):
    if not REC49.exists():
        pytest.skip("New Recording 49.m4a not available")
    client, _ = real_server
    status, view = client.upload(REC49.read_bytes(), SMOKE_TEXT49, filename=REC49.name)
    assert status == 200 and view["reduction"]["integrity"]["ok"]
    status, cmp = client.post_json(f"/api/analyses/{view['analysis_id']}/compare", {})
    assert status == 200 and cmp["integrity"]["ok"]
    for engine in ENGINES:
        assert cmp["processing"][engine]["reduction_ms"] < 100.0


# ----------------------------------------------------------------------
# Browser: the real UI in headless Chrome
# ----------------------------------------------------------------------

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


@pytest.mark.skipif(not Path(CHROME).exists(), reason="Google Chrome not installed")
def test_reduction_in_a_real_browser(real_server, tmp_path):
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("node not installed")
    client, _ = real_server
    proc = subprocess.run(["node", str(Path(__file__).parent / "js" / "browser_reduction.mjs"), client.base + "/", CHROME,
                           str(tmp_path / "reduction")], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    r = json.loads(proc.stdout)
    assert r["errorBox"] is None

    # M4 still works alongside
    assert r["coachVisible"] and r["coachGroups"] > 0 and r["fullFeedbackRows"] > 0

    # R01 raw: the reduction section, its chain and playback
    red = r["r01"]
    assert red["heading"] == "Reduction & Connected Speech"
    assert red["rate"].startswith("Speaking rate:") and "decoded sounds per second" in red["rate"]
    assert red["groups"] and all(g["id"] for g in red["groups"])
    first = red["firstCard"]
    assert [line.split(":")[0] for line in first["chain"]] == [
        "Raw engine observation (wav2vec2_raw)", "Evidence (recognition)", "Candidate interpretation", "Evidence strength"]
    assert r["candidateHighlighted"]
    assert r["reductionClosedOnNewAnalysis"]

    # engine comparison: the "want to" disagreement is shown, not resolved
    rows = r["compareRows"]
    [want] = [x for x in rows if x["word"] == "want"]
    assert "decoded /t/" in want["first"] and "not decoded" in want["second"]
    assert "wav2vec2_raw decoded this /t/ separately" in want["relation"] and "kept, not resolved" in want["relation"]
    assert "not independent confirmation" in r["compareNote"]
    assert r["comparePlayHighlighted"]

    # wording: no score, ranking or judgement anywhere in the section
    for banned in ("score", "Score", "worst", "best", "wrong", "incorrect", "swallowed", "chewed", "your tongue"):
        assert banned not in r["reductionText"], banned

    # R18 (fast, long): connected-speech candidates are visible with explanations
    assert r["r18"]["groups"]
    assert any("consistent with natural connected speech" in t or "insufficient" in t.lower() for t in r["r18"]["cardTexts"])
