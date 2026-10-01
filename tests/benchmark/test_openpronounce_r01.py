"""End-to-end regression test for the OpenPronounce engine on R01.

R01 is "Think about the three things that you want to change." read slowly and
clearly. OpenPronounce's phone recogniser has a known, reproducible output for
it, pinned here so that adapter changes cannot silently alter the evidence:

    29 recognised phones, phone error rate 0.2188, "that" flagged.

The test loads the real model and the real WAV -- it is the only place that
proves the adapter runs end to end, which is precisely what was missing when the
input-type and timing-unit bugs went unnoticed.
"""

import csv
from pathlib import Path
from unittest.mock import patch

import pytest

from pronunciation_lab.benchmark.engines.openpronounce import OpenPronounceEngine
from pronunciation_lab.benchmark.schema import PronunciationResult

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = PROJECT_ROOT / "data" / "benchmark_manifest.csv"
WAV_DIR = PROJECT_ROOT / "data" / "benchmark_wav"

RECORDING_ID = "R01"

EXPECTED_PHONE_COUNT = 29
EXPECTED_PHONE_ERROR_RATE = 0.2188
EXPECTED_FLAGGED_WORD = "that"


def _manifest_row(recording_id: str) -> dict[str, str]:
    with MANIFEST.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["recording_id"] == recording_id:
                return row

    raise AssertionError(f"{recording_id} not found in {MANIFEST}")


@pytest.fixture(scope="module")
def r01():
    audio_path = WAV_DIR / f"{RECORDING_ID}.wav"
    if not audio_path.exists():
        pytest.skip(f"benchmark audio missing: {audio_path}")

    row = _manifest_row(RECORDING_ID)
    engine = OpenPronounceEngine()

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "compare_audio_with_text must not be called on the benchmark path: "
            "it synthesises a reference via network TTS and loads a second ASR "
            "checkpoint."
        )

    # Enforced here rather than merely documented: the benchmark path has to stay
    # offline and single-model.
    with patch("openpronounce.compare_audio_with_text", forbidden):
        result = engine.analyze(
            audio_path,
            row["target_text"],
            recording_id=RECORDING_ID,
            original_path=PROJECT_ROOT / "data" / row["filename"],
        )

    return result, row, engine


def _all_phonemes(result: PronunciationResult):
    return [phoneme for word in result.words for phoneme in word.phonemes]


# ----------------------------------------------------------------------
# End to end
# ----------------------------------------------------------------------


def test_analyze_returns_a_valid_result(r01):
    result, row, _ = r01

    assert isinstance(result, PronunciationResult)
    assert result.recording.id == RECORDING_ID
    assert result.recording.target.text == row["target_text"]
    assert result.engine.name == "openpronounce"
    assert result.engine.mode == "local"
    assert not result.errors


def test_result_is_json_serialisable(r01):
    """Catches numpy scalars or arrays leaking into the preserved evidence."""
    result, _, _ = r01

    assert result.model_dump_json()


def test_recording_metadata_describes_the_analysed_audio(r01):
    result, row, _ = r01

    audio = result.recording.audio
    assert audio.sample_rate_hz == 16_000
    assert audio.channels == 1
    assert abs(audio.duration_ms - 7850.7) < 5.0
    assert audio.analysis_path.endswith(f"{RECORDING_ID}.wav")
    # The untouched iPhone original stays addressable for playback.
    assert audio.original_path.endswith(row["filename"])


# ----------------------------------------------------------------------
# Known recognition output
# ----------------------------------------------------------------------


def test_recognition_matches_known_r01_output(r01):
    result, _, _ = r01
    evidence = result.engine_evidence

    phones = evidence["recognition"]["phones"]
    assert len(phones) == EXPECTED_PHONE_COUNT
    assert phones[0] == "θ"

    comparison = evidence["comparison"]
    assert comparison["phone_error_rate"] == pytest.approx(
        EXPECTED_PHONE_ERROR_RATE, abs=1e-4
    )
    assert EXPECTED_FLAGGED_WORD in comparison["words_with_errors"]


def test_confidences_and_spans_accompany_every_heard_phone(r01):
    result, _, _ = r01
    recognition = result.engine_evidence["recognition"]

    assert len(recognition["confidences"]) == EXPECTED_PHONE_COUNT
    assert len(recognition["frame_spans"]) == EXPECTED_PHONE_COUNT
    assert all(0.0 <= c <= 1.0 for c in recognition["confidences"])


# ----------------------------------------------------------------------
# Timing units -- the regression that motivated this test
# ----------------------------------------------------------------------


def test_frame_clock_is_reported_and_sane(r01):
    result, _, _ = r01
    clock = result.engine_evidence["frame_clock"]

    assert clock["method"] == "model_stride"
    assert clock["ms_per_frame"] == pytest.approx(20.0)
    # The old bug's implied rate was 1/16 ms per frame.
    assert clock["ms_per_frame"] > 1.0


def test_phoneme_timings_span_the_recording(r01):
    """The timing-unit guard.

    Reading frame indices as sample offsets put all 29 phones inside the first
    ~22 ms of a 7.85 s recording. Real timings have to reach the end of it.
    """
    result, _, _ = r01
    duration_ms = result.recording.audio.duration_ms

    ends = [p.timing.end_ms for p in _all_phonemes(result) if p.timing.end_ms]
    assert ends

    assert max(ends) > duration_ms * 0.5
    assert max(ends) <= duration_ms


def test_phoneme_timings_are_ordered_and_inside_the_recording(r01):
    result, _, _ = r01
    duration_ms = result.recording.audio.duration_ms

    previous_start = -1.0
    for phoneme in _all_phonemes(result):
        timing = phoneme.timing
        assert timing.start_ms is not None
        assert timing.end_ms is not None

        assert 0.0 <= timing.start_ms <= duration_ms
        assert timing.start_ms <= timing.end_ms <= duration_ms
        assert timing.start_ms >= previous_start

        previous_start = timing.start_ms


def test_word_timings_come_from_the_engine_where_phones_were_heard(r01):
    result, _, _ = r01

    timed = [w for w in result.words if w.timing.start_ms is not None]
    assert len(timed) >= len(result.words) - 1

    for word in timed:
        assert word.timing.source == "engine"
        assert word.timing.end_ms > word.timing.start_ms


# ----------------------------------------------------------------------
# Word-level reports from compare_phones
# ----------------------------------------------------------------------


def test_every_word_carries_expected_and_observed_phonemes(r01):
    result, row, _ = r01

    assert len(result.words) == len(row["target_text"].split())

    for word in result.words:
        assert word.expected_phonemes, f"{word.word} has no expected phonemes"
        assert len(word.phonemes) == len(word.expected_phonemes)

        for position, phoneme in enumerate(word.phonemes):
            assert phoneme.expected.phoneme == word.expected_phonemes[position]
            assert phoneme.engine_evidence["operation"] in {
                "match",
                "substitution",
                "omission",
            }


def test_provider_word_report_is_preserved_for_the_flagged_word(r01):
    result, _, _ = r01

    flagged = [
        word
        for word in result.words
        if word.engine_evidence.get("flagged_by_provider")
    ]
    assert [word.word for word in flagged] == [EXPECTED_FLAGGED_WORD]

    report = flagged[0].engine_evidence["provider_report"]
    assert report["word"] == EXPECTED_FLAGGED_WORD
    assert report["expected"] == "ðæt"
    # OpenPronounce's own per-phone judgement, kept verbatim.
    assert [p["expected"] for p in report["phones"]] == ["ð", "æ", "t"]


def test_unflagged_words_carry_no_invented_provider_report(r01):
    result, _, _ = r01

    for word in result.words:
        if not word.engine_evidence["flagged_by_provider"]:
            assert word.engine_evidence["provider_report"] is None


def test_the_dropped_word_is_recorded_as_omission_with_derived_timing(r01):
    """"that" was not heard at all, but must still be locatable in the audio."""
    result, _, _ = r01

    word = next(w for w in result.words if w.word == EXPECTED_FLAGGED_WORD)

    assert all(p.observed.top is None for p in word.phonemes)
    assert all(p.engine_evidence["operation"] == "omission" for p in word.phonemes)

    for phoneme in word.phonemes:
        assert phoneme.timing.source == "derived"
        assert phoneme.timing.start_ms is not None
        assert phoneme.timing.end_ms is not None


# ----------------------------------------------------------------------
# N-best and uncertainty
# ----------------------------------------------------------------------


def test_nbest_comes_from_real_posteriors(r01):
    result, _, _ = r01

    matched = [
        p
        for p in _all_phonemes(result)
        if p.engine_evidence["operation"] == "match"
    ]
    assert matched

    for phoneme in matched:
        nbest = phoneme.observed.nbest
        assert nbest, "a decoded phone must have candidates from its own posteriors"

        probabilities = [c.probability for c in nbest]
        assert all(p is not None and 0.0 <= p <= 1.0 for p in probabilities)
        # Ranked, and never a fabricated single-candidate stand-in.
        assert probabilities == sorted(probabilities, reverse=True)
        assert len(nbest) > 1

        # The decoded phone is the strongest candidate over its own span.
        assert nbest[0].phoneme == phoneme.observed.top

        assert all(c.score is None for c in nbest)


def test_omitted_phonemes_report_the_expected_phone_posterior(r01):
    """Evidence for "absent or merely too weak to win the decode?" -- not a verdict."""
    result, _, _ = r01

    omitted = [
        p
        for p in _all_phonemes(result)
        if p.engine_evidence["operation"] == "omission"
    ]
    assert omitted

    for phoneme in omitted:
        posterior = phoneme.engine_evidence["expected_phone_posterior"]
        assert posterior is None or 0.0 <= posterior <= 1.0


def test_posteriorgram_is_described_in_the_result_and_kept_on_the_engine(r01):
    result, _, engine = r01

    posteriorgram = result.engine_evidence["posteriorgram"]
    assert posteriorgram["retained_in_result"] is False
    assert posteriorgram["n_frames"] == 392
    assert posteriorgram["vocab_size"] == 392

    # The full array stays available for later N-best work, out of the JSON.
    assert engine.last_recognition is not None
    assert engine.last_recognition.log_posteriors.shape == (392, 392)


# ----------------------------------------------------------------------
# Acoustic evidence, measured independently of the engine
# ----------------------------------------------------------------------


def test_acoustic_evidence_is_measured_for_every_phoneme(r01):
    result, _, _ = r01

    for phoneme in _all_phonemes(result):
        acoustic = phoneme.acoustic

        assert acoustic.energy_db is not None
        assert acoustic.relative_energy is not None and acoustic.relative_energy >= 0.0
        assert acoustic.voicing in {True, False, None}

        if acoustic.f0_hz is not None:
            assert 50.0 <= acoustic.f0_hz <= 400.0
            assert acoustic.voicing is True

        features = acoustic.spectral_features
        assert features is not None
        assert 0.0 <= features["zero_crossing_rate"] <= 1.0
        assert features["spectral_centroid_hz"] >= 0.0


def test_acoustic_windows_are_reported_so_spans_are_not_read_as_durations(r01):
    result, _, _ = r01

    for phoneme in _all_phonemes(result):
        features = phoneme.acoustic.spectral_features
        assert features["energy_window_start_ms"] <= features["energy_window_end_ms"]
        assert features["pitch_window_start_ms"] <= features["pitch_window_end_ms"]

        assert phoneme.engine_evidence["span_is_posterior_peak_not_duration"] is True


def test_voiced_and_voiceless_phonemes_are_distinguished(r01):
    """A sanity check that the measurements track the signal at all."""
    result, _, _ = r01

    vowels = {"ɪ", "i", "ə", "ʌ", "aʊ", "eɪ", "u", "ʊ", "æ", "ɑ"}

    vowel_energies = [
        p.acoustic.relative_energy
        for p in _all_phonemes(result)
        if p.expected.phoneme in vowels
        and p.engine_evidence["operation"] != "omission"
    ]
    voiceless_energies = [
        p.acoustic.relative_energy
        for p in _all_phonemes(result)
        if p.expected.phoneme in {"θ", "t", "k"}
        and p.engine_evidence["operation"] != "omission"
    ]

    assert vowel_energies and voiceless_energies

    mean_vowel = sum(vowel_energies) / len(vowel_energies)
    mean_voiceless = sum(voiceless_energies) / len(voiceless_energies)
    assert mean_vowel > mean_voiceless


# ----------------------------------------------------------------------
# Nothing invented
# ----------------------------------------------------------------------


def test_unavailable_evidence_stays_null(r01):
    result, _, _ = r01

    for phoneme in _all_phonemes(result):
        # This engine reports no stress, syllable structure or prominence.
        assert phoneme.expected.stress is None
        assert phoneme.expected.syllable is None
        assert phoneme.prosody.stress_expected is None
        assert phoneme.prosody.stress_observed is None
        assert phoneme.prosody.prominence is None

        if phoneme.engine_evidence["operation"] == "omission":
            assert phoneme.observed.top is None
            assert phoneme.observed.confidence is None


def test_processing_timings_separate_model_load_from_inference(r01):
    result, _, _ = r01
    processing = result.processing

    assert processing.wall_time_ms > 0
    assert processing.inference_ms > 0
    assert processing.postprocessing_ms is not None
    assert processing.realtime_factor is not None
    assert processing.timing_method == "monotonic_clock"
    assert processing.run_type == "cold"
    assert processing.model_load_ms is not None
