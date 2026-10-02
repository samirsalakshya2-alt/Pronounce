"""The app with the real engines and real audio, through HTTP.

The frozen M2 run is the reference: the app must show exactly the evidence the
benchmark stored for the same recording, play exactly the analysed audio, and
classify every sound consistently with that evidence.
"""

import json
import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf
from conftest import DATA_DIR

from pronunciation_lab.app.diagnosis import classify_sound

M2_CELLS = DATA_DIR / "benchmark_results" / "runs" / "m2-20261002" / "cells"
TEXT_R01 = "Think about the three things that you want to change."


def m2_result(rid, engine):
    path = M2_CELLS / f"{rid}__{engine}.json"
    if not path.exists():
        pytest.skip("M2 run not present")
    return json.loads(path.read_text())["cell"]["result"]


def stable(view):
    """The view without per-analysis identifiers and timings."""
    view = json.loads(json.dumps(view))
    for key in ("analysis_id", "processing", "source", "audio"):
        view.pop(key, None)
    return view


@pytest.fixture(scope="module")
def r01_view(real_server):
    client, _ = real_server
    status, view = client.post_json("/api/analyze-benchmark", {"recording_id": "R01"})
    assert status == 200
    return view


def test_r01_matches_the_frozen_m2_evidence(r01_view):
    m2 = m2_result("R01", "wav2vec2_raw")
    assert r01_view["state"] == "ok"
    assert r01_view["heard_sequence"] == m2["engine_evidence"]["recognition"]["phones"]
    assert [w["word"] for w in r01_view["words"]] == [w["word"] for w in m2["words"]]
    for vw, mw in zip(r01_view["words"], m2["words"]):
        for vs, mp in zip(vw["sounds"], mw["phonemes"]):
            assert vs["expected"] == mp["expected"]["phoneme"]
            assert vs["heard"] == mp["observed"]["top"]
            assert vs["span_ms"] == [mp["timing"]["start_ms"], mp["timing"]["end_ms"]]
            assert vs["expected_probability"] == mp["engine_evidence"]["expected_phone_posterior"]
            nb = [(c["phoneme"], c["probability"]) for c in mp["observed"]["nbest"]]
            margin = nb[0][1] - nb[1][1] if len(nb) > 1 else None
            assert vs["category"] == classify_sound(mp["engine_evidence"]["operation"],
                                                    mp["engine_evidence"]["expected_phone_posterior"], margin)


def test_r01_shows_the_known_m2_findings(r01_view):
    words = {w["word"]: w for w in r01_view["words"]}
    assert words["that"]["status"] == "not_detected" and words["that"]["timing_estimated"] is True
    three = words["three"]["sounds"]
    assert (three[-1]["expected"], three[-1]["heard"], three[-1]["category"]) == ("iː", "i", "unclear")
    assert three[0]["extra_sounds_after"] == ["ɪ"]
    assert words["want"]["sounds"][-1]["category"] == "expected"  # raw keeps want's /t/ (M2 cross-check)
    assert r01_view["summary"] == {"expected": 26, "different": 0, "unclear": 3, "not_detected": 3, "not_interpreted": 0}


def test_playback_serves_exactly_the_analysed_audio(real_server, r01_view):
    client, _ = real_server
    status, headers, body = client.request("GET", r01_view["audio"]["url"])
    assert status == 200 and body == (DATA_DIR / "benchmark_wav" / "R01.wav").read_bytes()
    assert r01_view["audio"]["conversion"] == "copied"


def test_every_playback_window_contains_its_evidence(r01_view):
    duration = r01_view["duration_ms"]
    for w in r01_view["words"]:
        assert 0 <= w["play_ms"][0] <= w["span_ms"][0] <= w["span_ms"][1] <= w["play_ms"][1] <= duration
        for s in w["sounds"]:
            assert 0 <= s["play_ms"][0] <= s["span_ms"][0] <= s["span_ms"][1] <= s["play_ms"][1] <= duration
            assert s["play_ms"][1] - s["play_ms"][0] >= min(300.0, duration) - 1e-6
            # spans sit on the 20 ms frame grid (exact, not approximated)
            assert s["span_ms"][0] % 20 == 0 and (s["span_ms"][1] % 20 == 0 or s["span_ms"][1] == duration)


def test_heard_words_have_sound_where_the_evidence_points(r01_view, real_server):
    audio, sr = sf.read(str(DATA_DIR / "benchmark_wav" / "R01.wav"), dtype="float64")
    global_rms = np.sqrt(np.mean(audio ** 2))
    for w in r01_view["words"]:
        if w["timing_estimated"]:
            continue
        seg = audio[int(w["span_ms"][0] * sr / 1000): int(w["span_ms"][1] * sr / 1000)]
        assert np.sqrt(np.mean(seg ** 2)) > 0.3 * global_rms, w["word"]


def test_uploading_the_benchmark_wav_gives_the_same_view(real_server, r01_view):
    client, _ = real_server
    status, view = client.upload((DATA_DIR / "benchmark_wav" / "R01.wav").read_bytes(), TEXT_R01, filename="R01.wav")
    assert status == 200 and stable(view) == stable(r01_view)


def test_repeated_analysis_is_identical(real_server, r01_view):
    client, _ = real_server
    for _ in range(2):
        status, view = client.post_json("/api/analyze-benchmark", {"recording_id": "R01"})
        assert status == 200 and view["analysis_id"] != r01_view["analysis_id"]
        assert stable(view) == stable(r01_view)
        assert view["processing"]["run_type"] == "warm"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_original_iphone_m4a_reproduces_the_benchmark_wav(real_server, r01_view):
    """Uploading the original Voice Memo yields the benchmark WAV and the same evidence."""
    client, svc = real_server
    m4a = DATA_DIR / "New Recording 29.m4a"
    if not m4a.exists():
        pytest.skip("original recording not present")
    status, view = client.upload(m4a.read_bytes(), TEXT_R01, filename=m4a.name)
    assert status == 200 and view["audio"]["conversion"] == "ffmpeg"
    _, _, wav = client.request("GET", view["audio"]["url"])
    assert wav == (DATA_DIR / "benchmark_wav" / "R01.wav").read_bytes()
    assert stable(view) == stable(r01_view)


def test_openpronounce_view_shows_its_own_flags(real_server):
    client, _ = real_server
    status, view = client.post_json("/api/analyze-benchmark", {"recording_id": "R01", "engine": "openpronounce"})
    assert status == 200 and view["engine"]["id"] == "openpronounce"
    words = {w["word"]: w for w in view["words"]}
    assert words["that"]["flagged_by_engine"] is True
    assert words["want"]["sounds"][-1]["category"] == "not_detected"  # the M2 "want to" merge, shown as is
    assert all(s["expected_stress"] is None for w in view["words"] for s in w["sounds"])


@pytest.mark.parametrize("engine", ["wavlm", "azure_pronunciation", "speechsuper", "speechace"])
def test_unavailable_engines_are_refused(real_server, engine):
    client, _ = real_server
    status, data = client.post_json("/api/analyze-benchmark", {"recording_id": "R01", "engine": engine})
    assert status == 409 and data["error"]["code"] == "engine_unavailable"
    status, st = client.get_json("/api/status")
    entry = next(e for e in st["engines"] if e["id"] == engine)
    assert entry["state"] in ("blocked", "unresolved") and entry["reason"]


def test_r04_intended_substitution_is_reported_as_heard(real_server):
    """The app reports what the recogniser heard, even when the reading intended otherwise."""
    client, _ = real_server
    status, view = client.post_json("/api/analyze-benchmark", {"recording_id": "R04"})
    th = [s for w in view["words"] for s in w["sounds"] if s["expected"] == "θ"]
    assert len(th) == 3 and all(s["heard"] == "θ" for s in th)


def test_silence_is_reported_as_no_speech(real_server, tmp_path):
    client, _ = real_server
    path = tmp_path / "silence.wav"
    sf.write(path, np.zeros(16_000, np.float32), 16_000, subtype="PCM_16")
    status, view = client.upload(path.read_bytes(), TEXT_R01)
    assert status == 200 and view["state"] == "no_speech"
    assert "silent" in view["message"] and view["words"] == []


def test_noise_is_not_presented_as_speech_evidence(real_server, tmp_path):
    client, _ = real_server
    path = tmp_path / "noise.wav"
    sf.write(path, (np.random.default_rng(3).normal(0, 0.05, 32_000)).astype(np.float32), 16_000, subtype="PCM_16")
    status, view = client.upload(path.read_bytes(), "Think about the three things.")
    assert status == 200
    # The recogniser decodes nothing from white noise: no speech, and -- since
    # the recording is not silent -- no "check your microphone" message either.
    assert view["state"] == "no_speech" and view["words"] == []
    assert view["message"] == "No speech sounds were detected in this recording."


@pytest.mark.skipif(shutil.which("espeak-ng") is None, reason="espeak-ng not installed")
def test_fresh_synthesised_recording(real_server, tmp_path):
    """A recording the benchmark has never seen (eSpeak TTS), end to end."""
    client, _ = real_server
    path = tmp_path / "tts.wav"
    subprocess.run(["espeak-ng", "-v", "en-us", "-s", "140", "-w", str(path), "Think about the three things"], check=True)
    status, view = client.upload(path.read_bytes(), "Think about the three things", filename="tts.wav")
    assert status == 200 and view["state"] == "ok"
    assert view["audio"]["conversion"] == "resampled"  # eSpeak writes 22.05 kHz
    assert [w["word"] for w in view["words"]] == ["think", "about", "the", "three", "things"]
    assert view["summary"]["expected"] >= 0.6 * sum(view["summary"].values())


def test_notes_on_a_real_analysis(real_server, r01_view):
    client, svc = real_server
    that = next(w for w in r01_view["words"] if w["word"] == "that")
    idx = that["sounds"][0]["index"]
    status, note = client.post_json("/api/notes", {"analysis_id": r01_view["analysis_id"], "sound_index": idx,
                                                   "verdict": "cannot_tell"})
    assert status == 200
    n = note["note"]
    assert (n["word"], n["expected"], n["heard_by_engine"], n["engine_category"]) == ("that", "ð", None, "not_detected")
    assert n["model"].endswith("@ae45363bf3413b374fecd9dc8bc1df0e24c3b7f4")
    stored = [json.loads(line) for line in svc.notes_path.read_text().splitlines()]
    assert stored[-1] == n
    # A note holds references (hash, times, phones), never audio data.
    assert set(n) == {"recorded_at", "analysis_id", "audio_sha256", "source", "target_text", "engine", "model",
                      "word", "sound_index", "expected", "heard_by_engine", "engine_category", "span_ms",
                      "verdict", "comment"}
