"""Unit tests for the shared CTC evidence assembly, on synthetic posteriors."""

import numpy as np
import pytest

from pronunciation_lab.benchmark import ctc
from pronunciation_lab.benchmark.frames import frame_clock

VOCAB = ("<pad>", "a", "b", "c", "[UNK]")
SR = 16_000


def _posteriors(frame_ids, confident=0.9):
    """One-hot-ish log posteriors: `confident` on the given id, the rest shared."""
    n = len(frame_ids)
    probs = np.full((n, len(VOCAB)), (1 - confident) / (len(VOCAB) - 1))
    probs[np.arange(n), frame_ids] = confident
    return np.log(probs)


def test_align_reports_match_omission_and_insertion():
    alignment = ctc.align(["a", "b", "c"], ["a", "x", "c", "d"])
    assert alignment == [(0, 0), (1, 1), (2, 2), (None, 3)]

    alignment = ctc.align(["a", "b", "c"], ["a", "c"])
    assert (1, None) in alignment


def test_greedy_decode_collapses_runs_and_drops_blank_and_specials():
    # a a <pad> a b [UNK] c c
    lp = _posteriors([1, 1, 0, 1, 2, 4, 3, 3])
    tokens, confidences, spans = ctc.greedy_decode(lp, VOCAB, blank_id=0)

    assert tokens == ["a", "a", "b", "c"]
    assert spans == [(0, 2), (3, 4), (4, 5), (6, 8)]
    assert all(c == pytest.approx(0.9) for c in confidences)


def test_nbest_is_ranked_real_posteriors_and_expected_posterior_is_reported():
    lp = _posteriors([1, 1, 2])
    token_ids = ctc.phone_token_ids(VOCAB)
    assert "<pad>" not in token_ids and "[UNK]" not in token_ids

    candidates = ctc.nbest(lp, token_ids, 0, 2, size=3)
    assert candidates[0].phoneme == "a"
    probabilities = [c.probability for c in candidates]
    assert probabilities == sorted(probabilities, reverse=True)

    assert ctc.phone_posterior(lp, token_ids, "c", 0, 2) == pytest.approx(0.025)
    assert ctc.phone_posterior(lp, token_ids, "zz", 0, 2) is None
    assert ctc.nbest(lp, token_ids, 2, 2, size=3) == []


def test_phone_token_ids_merges_through_normalize():
    ids = ctc.phone_token_ids(("<pad>", "iː", "i"), normalize=lambda t: t.replace("ː", ""))
    assert ids == {"i": [1, 2]}


def _build(expected_groups, frame_ids):
    lp = _posteriors(frame_ids)
    tokens, confidences, spans = ctc.greedy_decode(lp, VOCAB, 0)
    n_samples = len(frame_ids) * 320
    waveform = np.sin(np.linspace(0, 400 * np.pi, n_samples)).astype(np.float32)
    return ctc.build_words(
        provider="test",
        words=[f"w{i}" for i in range(len(expected_groups))],
        expected_groups=expected_groups,
        heard_phones=tokens,
        heard_confidences=confidences,
        heard_spans=spans,
        log_posteriors=lp,
        token_ids=ctc.phone_token_ids(VOCAB),
        clock=frame_clock(n_frames=len(frame_ids), n_samples=n_samples, sample_rate=SR),
        waveform=waveform,
        sample_rate=SR,
        reference_rms=0.7,
        nbest_size=3,
        expected_source="test",
        expected_stress=[[None] * len(g) for g in expected_groups],
    )


def test_build_words_assigns_operations_timing_and_acoustics():
    # heard: a . . c   expected: [a b] [c]
    words = _build([["a", "b"], ["c"]], [1, 0, 0, 3, 0])

    ops = [p.engine_evidence["operation"] for w in words for p in w.phonemes]
    assert ops == ["match", "omission", "match"]

    omitted = words[0].phonemes[1]
    assert omitted.observed.top is None and omitted.observed.confidence is None
    assert omitted.timing.source == "derived"
    # Placed in the gap between the neighbouring heard phones.
    assert omitted.timing.start_ms == pytest.approx(20.0)
    assert omitted.timing.end_ms == pytest.approx(60.0)

    matched = words[0].phonemes[0]
    assert matched.timing.source == "engine"
    assert (matched.timing.start_ms, matched.timing.end_ms) == (0.0, 20.0)
    assert matched.acoustic.measured_on == "analysis"
    assert matched.acoustic.energy_db is not None
    assert matched.prosody.stress_observed is None

    assert words[1].timing.source == "engine"


def test_build_words_keeps_insertions_including_leading_ones():
    # heard: b a c   expected: [a c] -> leading "b" has no expected phone before it
    words = _build([["a", "c"]], [2, 0, 1, 0, 3])
    first = words[0].phonemes[0]

    assert [x["phone"] for x in first.engine_evidence["leading_heard_phones"]] == ["b"]
    assert ctc.operation_counts(words[0]) == {
        "match": 2,
        "substitution": 0,
        "omission": 0,
        "insertion": 0,
    }


def test_fully_omitted_word_has_no_engine_timing():
    words = _build([["a"], ["b"]], [1, 0, 0])
    assert words[1].timing.source == "unknown"
    assert words[1].timing.start_ms is None


def test_missing_confidence_stays_null_rather_than_invented():
    lp = _posteriors([1, 0, 3])
    tokens, _, spans = ctc.greedy_decode(lp, VOCAB, 0)
    words = ctc.build_words(
        provider="test",
        words=["w"],
        expected_groups=[["a", "c"]],
        heard_phones=tokens,
        heard_confidences=[],  # engine supplied none
        heard_spans=spans,
        log_posteriors=lp,
        token_ids=ctc.phone_token_ids(VOCAB),
        clock=frame_clock(n_frames=3, n_samples=960, sample_rate=SR),
        waveform=np.zeros(960, np.float32),
        sample_rate=SR,
        reference_rms=0.0,
        nbest_size=3,
    )
    for p in words[0].phonemes:
        assert p.observed.top is not None
        assert p.observed.confidence is None
        # No reference RMS: relative energy is unavailable, not zero.
        assert p.acoustic.relative_energy is None
        assert p.expected.stress is None and p.expected.source is None


def test_missing_posteriorgram_gives_no_nbest_and_no_posterior():
    empty = np.zeros((0, len(VOCAB)))
    token_ids = ctc.phone_token_ids(VOCAB)
    assert ctc.nbest(empty, token_ids, 0, 1, size=3) == []
    assert ctc.phone_posterior(empty, token_ids, "a", 0, 1) is None


def test_greedy_decode_of_all_blank_is_empty():
    tokens, confidences, spans = ctc.greedy_decode(_posteriors([0, 0, 0]), VOCAB, 0)
    assert (tokens, confidences, spans) == ([], [], [])


def test_blank_is_dropped_by_id_even_when_it_looks_like_a_phone():
    """The blank must be removed because it is the blank, not because of its spelling."""
    vocab = ("_", "a", "b")
    probs = np.full((4, 3), 0.05)
    probs[np.arange(4), [1, 0, 0, 2]] = 0.9
    tokens, _, spans = ctc.greedy_decode(np.log(probs), vocab, blank_id=0)
    assert tokens == ["a", "b"]
    assert spans == [(0, 1), (3, 4)]


def test_align_tie_breaking_is_pinned():
    """Equal-cost alignments are resolved deterministically, preferring substitution.

    For expected [a b] / heard [b x], two substitutions and omission+match+insertion
    both cost 2. The aligner picks the substitutions; pinned so that a cost change
    (which would flip it) cannot pass silently.
    """
    assert ctc.align(["a", "b"], ["b", "x"]) == [(0, 0), (1, 1)]
    # A single differing phone is one substitution, never omission + insertion.
    assert ctc.align(["a", "b", "c"], ["a", "x", "c"]) == [(0, 0), (1, 1), (2, 2)]
