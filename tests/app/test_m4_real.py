"""M4 Phoneme Coach on real audio: integration with M3, known M2 behaviours, repeatability.

Benchmark recordings R01–R20 (local, untracked data directory) are the ground-truth
real-audio set. "New Recording 49" is read from ~/Downloads when present, as a UI /
smoke / performance check only: its original target text could not be recovered, so
no assertion here depends on what the coach finds in it (see docs/M4_PHONEME_COACH.md).
"""

import json
from pathlib import Path

import pytest
from apphelpers import DATA_DIR

from pronunciation_lab.app.coach import build_coach, validate_coach
from pronunciation_lab.benchmark.schema import PronunciationResult

RECORDINGS = ["R01", "R04", "R05", "R07", "R08", "R11", "R14", "R17", "R18", "R19", "R20"]
REC49 = Path.home() / "Downloads" / "New Recording 49.m4a"
# Smoke-test input only. Reconstructed from the audio by ASR, NOT the user's target text:
# it must never be used as pronunciation ground truth or to assert findings.
SMOKE_TEXT49 = ("Although the initial results were encouraging, we still need to examine the underlying data before making a "
          "final decision. The team wants to understand whether the changes will improve customer service without "
          "increasing transportation costs.")
M2_CELLS = DATA_DIR / "benchmark_results" / "runs" / "m2-20261002" / "cells"


@pytest.fixture(scope="module")
def views(real_server):
    client, _ = real_server
    out = {}
    for rid in RECORDINGS:
        status, view = client.post_json("/api/analyze-benchmark", {"recording_id": rid})
        assert status == 200, view
        out[rid] = view
    return out


def coach_of(view):
    return view["coach"]


def find(coach, expected, contrast=None, kind="contrast"):
    return [p for p in coach["patterns"] if p["kind"] == kind and p["expected"] == expected and p["contrast"] == contrast]


# ----------------------------------------------------------------------
# Integration: M3 evidence -> M4 observations -> patterns -> targets -> view
# ----------------------------------------------------------------------

@pytest.mark.parametrize("rid", RECORDINGS)
def test_coach_is_consistent_with_the_m3_view(views, rid):
    view = views[rid]
    coach = coach_of(view)
    assert coach["state"] == "ok" and coach["integrity"] == {"ok": True, "issues": []}
    assert validate_coach(coach, duration_ms=view["duration_ms"]) == []
    sounds = {s["index"]: s for w in view["words"] for s in w["sounds"]}
    sound_obs = [o for o in coach["observations"] if o["kind"] == "sound"]
    assert len(sound_obs) == len(sounds)  # every M3 sound has exactly one observation
    for o in sound_obs:
        s = sounds[o["evidence"]["m3_sound_index"]]
        assert (o["expected"], o["observed"]) == (s["expected"], s["heard"])
        assert o["expected_posterior"] == s["expected_probability"]
        assert o["span_ms"] == s["span_ms"]
        if o["timing_source"] == "engine":
            assert o["play_ms"] == s["play_ms"]  # decoded sounds: exactly M3's playback window
        else:  # estimated regions: start at the estimate, capped
            assert o["play_ms"][0] <= o["span_ms"][0] and o["play_ms"][1] - o["play_ms"][0] <= 800.0 + 1e-6
        if s["category"] == "not_interpreted":
            assert o["type"] == "not_interpreted"
        if o["type"] == "substitution_candidate":
            assert s["category"] == "different"  # M4 never upgrades M3's caution
    assert view["processing"]["coach_ms"] < 100.0


@pytest.mark.parametrize("rid", RECORDINGS)
def test_no_extra_inference_and_same_result_from_stored_evidence(views, rid):
    """M4 is a pure function of the M1 evidence: the stored M2 result gives the same coach."""
    path = M2_CELLS / f"{rid}__wav2vec2_raw.json"
    if not path.exists():
        pytest.skip("M2 run not present")
    stored = PronunciationResult.model_validate(json.loads(path.read_text())["cell"]["result"])
    coach = build_coach(stored)
    coach.pop("_timing_ms")
    assert coach == coach_of(views[rid])


# ----------------------------------------------------------------------
# Known M2 behaviours stay understandable
# ----------------------------------------------------------------------

@pytest.mark.parametrize("rid, word", [("R05", "would"), ("R07", "would"), ("R11", "world")])
def test_w_to_v_is_a_high_confidence_single_occurrence(views, rid, word):
    coach = coach_of(views[rid])
    [p] = find(coach, "w", "v")
    assert (p["class"], p["group"], p["words"], p["evidence_strength"]) == ("one_off", "single", [word], "strong")
    assert "more consistent with /v/" in p["summary"] and "Single observation — monitor for recurrence." in p["summary"]
    [t] = [t for t in coach["practice_targets"] if t["pattern_id"] == p["id"]]
    assert t["kind"] == "monitor" and t["occurrences"][0]["play_ms"]
    o = next(o for o in coach["observations"] if o["id"] == p["observation_ids"][0])
    assert o["competitor_posterior"] > 0.85 and o["expected_posterior"] < 0.05


def test_r04_intended_substitution_is_not_claimed(views):
    """M2: the recogniser decoded /θ/ in R04; M4 must not invent a /θ/→/t/ finding."""
    coach = coach_of(views["R04"])
    th = [o for o in coach["observations"] if o["expected"] == "θ"]
    assert len(th) == 3 and {o["type"] for o in th} == {"expected"}
    assert not [p for p in coach["patterns"] if p["expected"] == "θ"]
    # ...but the coach says what its silence means
    assert coach["coverage"]["consistent_with_expected"] == 31
    assert "deliberate /θ/→/t/" in coach["coverage"]["note"]


def test_r01_not_detected_is_not_called_absent(views):
    coach = coach_of(views["R01"])
    that = [o for o in coach["observations"] if o["word"] == "that" and o["kind"] == "sound"]
    assert {o["type"] for o in that} == {"omission_candidate"} and {o["confidence"] for o in that} == {"low"}
    assert all(o["timing_source"] == "derived" for o in that)
    for p in coach["patterns"]:
        if p["kind"] == "detection":
            assert p["group"] == "not_detected" and "does not prove the sound was absent" in p["summary"]


@pytest.mark.parametrize("rid, expected, contrast", [("R01", "ɔ", "ʌ"), ("R01", "iː", "i"), ("R14", "ʊɹ", "uː")])
def test_reference_accent_notes(views, rid, expected, contrast):
    [p] = find(coach_of(views[rid]), expected, contrast)
    assert p["reference_note"] and "reference accent" in p["reference_note"]


def test_r08_alignment_artifact_is_not_coached(views):
    coach = coach_of(views["R08"])
    skipped = {o["word"] for o in coach["observations"] if o["type"] == "not_interpreted"}
    assert {"before", "the", "evening"} <= skipped
    supported = {r for p in coach["patterns"] for r in p["observation_ids"]}
    assert not any(o["id"] in supported for o in coach["observations"] if o["type"] == "not_interpreted")


def test_unknown_stress_with_openpronounce(real_server):
    client, _ = real_server
    status, view = client.post_json("/api/analyze-benchmark", {"recording_id": "R01", "engine": "openpronounce"})
    coach = coach_of(view)
    assert coach["engine"]["id"] == "openpronounce" and "not independent confirmation" in coach["engine"]["shared_model_note"]
    assert all(o["context"]["stress"] is None and o["context"]["stress_known"] is False
               for o in coach["observations"] if o["kind"] == "sound")
    raw = coach_of(client.post_json("/api/analyze-benchmark", {"recording_id": "R01"})[1])
    assert any(o["context"]["stress_known"] for o in raw["observations"] if o["kind"] == "sound")


# ----------------------------------------------------------------------
# Repeatability
# ----------------------------------------------------------------------

@pytest.mark.parametrize("rid", ["R01", "R05", "R18"])
def test_repeatability(real_server, views, rid):
    client, _ = real_server
    status, again = client.post_json("/api/analyze-benchmark", {"recording_id": rid})
    assert status == 200 and coach_of(again) == coach_of(views[rid])


# ----------------------------------------------------------------------
# New Recording 49 (from ~/Downloads, if present): UI / smoke / performance only.
# Pronunciation findings are NOT validated: the original target text is unknown.
# ----------------------------------------------------------------------

@pytest.fixture(scope="module")
def rec49(real_server):
    if not REC49.exists():
        pytest.skip("New Recording 49.m4a not available")
    client, _ = real_server
    status, view = client.upload(REC49.read_bytes(), SMOKE_TEXT49, filename=REC49.name)
    assert status == 200, view
    return view


def test_rec49_smoke_integrity_and_performance(rec49):
    """A long real recording runs end to end with clean invariants; nothing about its findings is asserted."""
    coach = coach_of(rec49)
    assert coach["state"] == "ok" and coach["integrity"]["ok"]
    assert validate_coach(coach, duration_ms=rec49["duration_ms"]) == []
    assert rec49["processing"]["coach_ms"] < 100.0
    for o in coach["observations"]:
        assert 0 <= o["play_ms"][0] < o["play_ms"][1] <= rec49["duration_ms"]


def test_rec49_estimated_playback_is_capped(rec49):
    """Invariant (no pronunciation claim): estimated locations use the M3 window, capped at 800 ms."""
    from pronunciation_lab.app.diagnosis import sound_window

    duration = rec49["duration_ms"]
    for o in coach_of(rec49)["observations"]:
        if o["kind"] == "sound" and o["timing_source"] == "derived":
            regular = list(sound_window(*o["span_ms"], duration))
            if regular[1] - regular[0] <= 800.0:
                assert o["play_ms"] == regular
            else:
                start = max(0.0, o["span_ms"][0] - 150.0)
                assert o["play_ms"] == [start, min(duration, start + 800.0)]


# ----------------------------------------------------------------------
# Browser: the real UI in headless Chrome
# ----------------------------------------------------------------------

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


@pytest.mark.skipif(not Path(CHROME).exists(), reason="Google Chrome not installed")
def test_coach_in_a_real_browser(real_server, tmp_path):
    import shutil
    import subprocess

    import numpy as np
    import soundfile as sf

    if shutil.which("node") is None:
        pytest.skip("node not installed")
    client, _ = real_server
    silence = tmp_path / "silence.wav"
    sf.write(silence, np.zeros(16_000, np.float32), 16_000, subtype="PCM_16")
    assert client.upload(silence.read_bytes(), "Think about it.", filename="silence.wav")[0] == 200
    if REC49.exists():
        assert client.upload(REC49.read_bytes(), SMOKE_TEXT49, filename=REC49.name)[0] == 200

    proc = subprocess.run(["node", str(Path(__file__).parent / "js" / "browser_coach.mjs"), client.base + "/", CHROME,
                           str(tmp_path / "coach")], capture_output=True, text=True, timeout=240)
    assert proc.returncode == 0, proc.stderr
    r = json.loads(proc.stdout)
    assert r["errorBox"] is None

    # R05: single /w/ ↔ /v/ observation, ambiguous items, extra sound; neutral group order
    r05 = r["r05"]
    assert r05["visible"] and r05["heading"] == "Phoneme Coach"
    assert r05["coverage"].startswith("31 of 35 sounds were consistent with the expected sound.")
    assert "not proof that it was pronounced canonically" in r05["coverage"]
    assert [g["id"] for g in r05["groups"]] == ["single", "ambiguous", "insertion"]
    assert all(g["open"] for g in r05["groups"])  # short groups stay open
    [wv] = [c for g in r05["groups"] for c in g["cards"] if c["title"] == "/w/ ↔ /v/"]
    assert wv["label"] == "Single observation" and "Single observation — monitor for recurrence." in wv["summary"]
    assert wv["practiceButton"] == "Listen & monitor"
    [[word, change, interp, evidence, where, listen]] = r["r05Evidence"]
    assert word == "would" and change == "/w/ → /v/" and interp.startswith("Substitution candidate (high confidence)")
    assert "P(/w/) 2%" in evidence and "P(/v/) 86%" in evidence and "word-initial" in where and "Play exact occurrence" in listen
    assert r["r05Highlighted"] and r["r05Cleared"] and r["r05WordPlayHighlighted"]
    assert "1. Sound:" in r["r05Practice"] and "/w/ (w as in wet) vs /v/ (v as in van)" in r["r05Practice"]
    assert "not a measurement of your articulation" in r["r05Practice"] and "2. Word:" in r["r05Practice"]
    assert r["r05Ambiguous"]["button"] == "Listen & compare" and "not a confirmed difference" in r["r05Ambiguous"]["practice"]
    for banned in ("score", "Score", "worst", "best", "wrong", "incorrect", "your tongue", "you pronounced"):
        assert banned not in r["r05PageText"], banned
    assert r["fullFeedbackRows"] > 0  # M3.1 view still works

    # R01: not-detected wording, coach closes on a new analysis
    assert r["coachClosedOnNewAnalysis"]
    assert "not_detected" in [g["id"] for g in r["r01"]["groups"]]
    assert "does not prove the sound was absent" in r["r01DetectionEvidence"]
    assert "estimated" in r["r01DetectionEvidence"]

    # silence: explicit no-coaching state
    assert r["silenceLoaded"] and "No coaching" in r["silence"]["message"]
    assert "WavLM — unresolved" in r["engineFooter"] and "blocked" in r["engineFooter"]

    if REC49.exists():  # UI smoke on a long real recording; its findings are not asserted
        assert r["rec49Loaded"] and r["rec49"]["visible"] and r["rec49"]["heading"] == "Phoneme Coach"
        assert r["rec49"]["coverage"] and r["rec49"]["groups"]
        for g in r["rec49"]["groups"]:  # long groups start collapsed
            assert g["open"] == (g["id"] in ("recurring", "not_detected") or len(g["cards"]) <= 4), g["id"]
