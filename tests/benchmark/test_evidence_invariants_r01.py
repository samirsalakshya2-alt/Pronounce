"""Evidence invariants for every running engine, on real R01 audio.

See `conftest.assert_evidence_invariants`: every number that claims to come from
the model (confidence, N-best, expected-phone posterior, frame timing) is
recomputed from the model's own posteriorgram and compared.
"""

import pytest

ENGINES = ["openpronounce_r01", "raw_r01"]


@pytest.mark.parametrize("fixture", ENGINES)
def test_invariants_hold_on_r01(fixture, request, check_invariants):
    engine, result, log_posteriors, token_ids = request.getfixturevalue(fixture)
    check_invariants(result, log_posteriors, token_ids, engine.nbest_size)


@pytest.mark.parametrize("fixture", ENGINES)
def test_processing_times_are_consistent(fixture, request):
    _, result, _, _ = request.getfixturevalue(fixture)
    p = result.processing

    assert p.run_type == "cold" and p.model_load_ms > 0
    # Parts never exceed the whole (model load and audio load sit outside them).
    assert p.inference_ms + p.postprocessing_ms <= p.wall_time_ms
    assert p.model_load_ms + p.inference_ms + p.postprocessing_ms <= p.wall_time_ms
    assert p.realtime_factor == pytest.approx(
        p.wall_time_ms / result.recording.audio.duration_ms
    )
    assert p.timing_method == "monotonic_clock"
    assert p.device == "cpu"
    # Local engines leave every cloud-only field empty rather than guessing.
    assert (p.api_latency_ms, p.provider_processing_ms, p.network_ms) == (None, None, None)


def test_openpronounce_leaves_stress_null(openpronounce_r01):
    _, result, _, _ = openpronounce_r01
    assert all(p.expected.stress is None for w in result.words for p in w.phonemes)


def test_raw_stress_is_espeak_lexical_and_only_on_vowels(raw_r01):
    _, result, _, _ = raw_r01
    consonants = set("θŋkbtðɹzjwnʃdʒ") | {"tʃ", "dʒ"}
    stressed = [p for w in result.words for p in w.phonemes if p.expected.stress]

    assert {p.expected.stress for p in stressed} <= {"primary", "secondary"}
    assert all(p.expected.phoneme not in consonants for p in stressed)
    # Every content word with a vowel in R01 carries a primary stress.
    for word in ("think", "three", "things", "want", "change"):
        w = next(x for x in raw_r01[1].words if x.word == word)
        assert [p.expected.stress for p in w.phonemes].count("primary") == 1
