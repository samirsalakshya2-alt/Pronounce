"""Evidence assembly shared by every CTC phone-recogniser engine.

A CTC recogniser gives a sequence of heard phones, each with a frame span and a
confidence, plus the full (frames x vocab) posteriorgram they were decoded from.
Turning that into per-word, per-phoneme evidence is the same job regardless of
which checkpoint produced it, so it lives here once:

    expected phones  --align-->  heard phones
                      -> match / substitution / omission, plus insertions
                      -> N-best and expected-phone posterior from the posteriorgram
                      -> frame timing via FrameClock
                      -> engine-independent acoustics over each span

What differs between CTC engines -- the checkpoint, the decoding, whether and how
phones are normalized, where the expected phones come from -- stays in each
engine's adapter and is reported in its `engine_evidence`.

The alignment is a plain unit-cost Levenshtein over exact phone strings. It is
deliberately not phonetically weighted: it decides *which expected phone a heard
phone is evidence about*, not how bad a difference is.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from pronunciation_lab.acoustics import measure_span
from pronunciation_lab.benchmark.frames import FrameClock
from pronunciation_lab.benchmark.schema import (
    AcousticEvidence,
    ExpectedPhoneme,
    NBestCandidate,
    ObservedPhoneme,
    PhonemeResult,
    ProsodyEvidence,
    TimingInfo,
    WordResult,
)

# Alignment operations recorded per expected phoneme.
OP_MATCH = "match"
OP_SUBSTITUTION = "substitution"
OP_OMISSION = "omission"

NBEST_METHOD = (
    "peak frame posterior per normalized phone over the span; peaks over a "
    "span, not a normalized distribution"
)


# ----------------------------------------------------------------------
# Alignment
# ----------------------------------------------------------------------


def align(
    expected: Sequence[str],
    heard: Sequence[str],
) -> list[tuple[int | None, int | None]]:
    """Levenshtein alignment of expected against heard phones.

    Returns `(expected_index, observed_index)` pairs in sequence order; `None`
    marks an omission (no heard phone) or an insertion (no expected phone).
    Deterministic and transparent by design -- the benchmark needs to be able to
    show why a phoneme was called an omission.
    """
    n, m = len(expected), len(heard)

    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if expected[i - 1] == heard[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )

    alignment: list[tuple[int | None, int | None]] = []
    i, j = n, m

    while i > 0 or j > 0:
        if i > 0 and j > 0:
            cost = 0 if expected[i - 1] == heard[j - 1] else 1
            if dp[i][j] == dp[i - 1][j - 1] + cost:
                alignment.append((i - 1, j - 1))
                i -= 1
                j -= 1
                continue

        if i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            alignment.append((i - 1, None))
            i -= 1
            continue

        alignment.append((None, j - 1))
        j -= 1

    alignment.reverse()
    return alignment


# ----------------------------------------------------------------------
# Posterior evidence
# ----------------------------------------------------------------------


def phone_token_ids(
    vocab: Sequence[str],
    normalize: Callable[[str], str] | None = None,
) -> dict[str, list[int]]:
    """Map each phone to the vocabulary ids that represent it.

    Special tokens (`<pad>`, `[UNK]`, ...) are skipped. With `normalize`, several
    tokens can map to one phone (e.g. `iː` and `i`); without it every token is
    its own phone.
    """
    mapping: dict[str, list[int]] = {}

    for index, token in enumerate(vocab):
        if is_special_token(token):
            continue

        phone = normalize(token) if normalize is not None else token
        if not phone:
            continue

        mapping.setdefault(phone, []).append(index)

    return mapping


def is_special_token(token: str) -> bool:
    return (token.startswith("<") and token.endswith(">")) or (
        token.startswith("[") and token.endswith("]")
    )


def nbest(
    log_posteriors: np.ndarray,
    token_ids: dict[str, list[int]],
    frame_start: int,
    frame_end: int,
    size: int,
) -> list[NBestCandidate]:
    """Top-`size` phones by peak posterior over frames `[frame_start, frame_end)`.

    Each probability is the highest posterior that phone reaches anywhere in the
    span (over any of the vocabulary tokens that map to it). These are genuine
    model posteriors, but they are peaks over a span rather than a normalized
    distribution, so they do not sum to 1.
    """
    if frame_end <= frame_start or log_posteriors.size == 0:
        return []

    block = log_posteriors[frame_start:frame_end]
    if block.size == 0:
        return []

    scored = [
        (phone, float(np.exp(block[:, ids].max())))
        for phone, ids in token_ids.items()
    ]
    scored.sort(key=lambda item: item[1], reverse=True)

    return [
        NBestCandidate(phoneme=phone, probability=probability, score=None)
        for phone, probability in scored[:size]
    ]


def phone_posterior(
    log_posteriors: np.ndarray,
    token_ids: dict[str, list[int]],
    phone: str,
    frame_start: int,
    frame_end: int,
) -> float | None:
    """Peak posterior of one specific phone over a frame range.

    For an omitted phoneme this answers "was the expected sound really absent,
    or merely too weak to win the decode?" -- without claiming either.
    """
    ids = token_ids.get(phone)
    if not ids or frame_end <= frame_start or log_posteriors.size == 0:
        return None

    block = log_posteriors[frame_start:frame_end]
    if block.size == 0:
        return None

    return float(np.exp(block[:, ids].max()))


def greedy_decode(
    log_posteriors: np.ndarray,
    vocab: Sequence[str],
    blank_id: int,
) -> tuple[list[str], list[float], list[tuple[int, int]]]:
    """Plain greedy CTC decoding, with no phone normalization of any kind.

    Frame-wise argmax, runs of the same id collapsed, blank and special tokens
    dropped. Each kept token's confidence is the peak posterior of that token
    over its run; its span is the half-open frame range of the run.
    """
    ids = np.asarray(log_posteriors).argmax(axis=1)

    tokens: list[str] = []
    confidences: list[float] = []
    spans: list[tuple[int, int]] = []

    start = 0
    for end in range(1, len(ids) + 1):
        if end < len(ids) and ids[end] == ids[start]:
            continue

        token_id = int(ids[start])
        token = vocab[token_id]
        if token_id != blank_id and not is_special_token(token):
            tokens.append(token)
            confidences.append(
                float(np.exp(log_posteriors[start:end, token_id].max()))
            )
            spans.append((start, end))

        start = end

    return tokens, confidences, spans


# ----------------------------------------------------------------------
# Word / phoneme assembly
# ----------------------------------------------------------------------


def build_words(
    *,
    provider: str,
    words: Sequence[str],
    expected_groups: Sequence[Sequence[str]],
    heard_phones: Sequence[str],
    heard_confidences: Sequence[float],
    heard_spans: Sequence[tuple[int, int]],
    log_posteriors: np.ndarray,
    token_ids: dict[str, list[int]],
    clock: FrameClock,
    waveform: np.ndarray,
    sample_rate: int,
    reference_rms: float,
    nbest_size: int,
    expected_source: str | None = None,
    expected_stress: Sequence[Sequence[str | None]] | None = None,
    word_evidence: Callable[[int], dict[str, Any]] | None = None,
) -> list[WordResult]:
    """Assemble per-word, per-phoneme evidence from one CTC recognition."""

    # Flatten the expected sequence; word ownership is recovered from the order.
    expected_flat = [phone for group in expected_groups for phone in group]

    alignment = align(expected_flat, list(heard_phones))

    matched: dict[int, int] = {}
    insertions_after: dict[int, list[int]] = {}
    last_expected = -1

    for expected_index, heard_index in alignment:
        if expected_index is not None and heard_index is not None:
            matched[expected_index] = heard_index
            last_expected = expected_index
        elif expected_index is not None:
            last_expected = expected_index
        elif heard_index is not None:
            insertions_after.setdefault(last_expected, []).append(heard_index)

    def heard_detail(heard_index: int) -> dict[str, Any]:
        span = heard_spans[heard_index] if heard_index < len(heard_spans) else None
        start_ms, end_ms = clock.span_ms(span) if span else (None, None)
        return {
            "phone": heard_phones[heard_index],
            "confidence": (
                float(heard_confidences[heard_index])
                if heard_index < len(heard_confidences)
                else None
            ),
            "frame_start": span[0] if span else None,
            "frame_end": span[1] if span else None,
            "start_ms": start_ms,
            "end_ms": end_ms,
        }

    # Frame fallbacks for omissions: the gap between the surrounding phones.
    def omission_region(expected_index: int) -> tuple[int, int]:
        previous_end = 0
        for i in range(expected_index - 1, -1, -1):
            if i in matched:
                previous_end = heard_spans[matched[i]][1]
                break

        following_start = clock.n_frames
        for i in range(expected_index + 1, len(expected_flat)):
            if i in matched:
                following_start = heard_spans[matched[i]][0]
                break

        if following_start <= previous_end:
            following_start = min(previous_end + 1, max(clock.n_frames, 1))

        return previous_end, following_start

    word_results: list[WordResult] = []
    global_position = 0

    for word_index, (word, group) in enumerate(zip(words, expected_groups)):
        phoneme_results: list[PhonemeResult] = []
        word_frame_starts: list[int] = []
        word_frame_ends: list[int] = []

        for position_in_word, expected_phone in enumerate(group):
            expected_index = global_position
            global_position += 1

            heard_index = matched.get(expected_index)

            if heard_index is not None:
                observed_phone = heard_phones[heard_index]
                confidence = (
                    float(heard_confidences[heard_index])
                    if heard_index < len(heard_confidences)
                    else None
                )
                frame_start, frame_end = heard_spans[heard_index]
                timing_source = "engine"
                operation = (
                    OP_MATCH if observed_phone == expected_phone else OP_SUBSTITUTION
                )
                word_frame_starts.append(frame_start)
                word_frame_ends.append(frame_end)
            else:
                observed_phone = None
                confidence = None
                frame_start, frame_end = omission_region(expected_index)
                # The engine placed nothing here; the region is inferred from the
                # neighbouring phones so the user can still listen to it.
                timing_source = "derived"
                operation = OP_OMISSION

            start_ms, end_ms = clock.span_ms((frame_start, frame_end))

            acoustics = measure_span(
                waveform,
                sample_rate,
                start_ms,
                end_ms,
                reference_rms=reference_rms,
            )

            stress = None
            if expected_stress is not None:
                stress = expected_stress[word_index][position_in_word]

            phoneme_results.append(
                PhonemeResult(
                    position=expected_index,
                    expected=ExpectedPhoneme(
                        phoneme=expected_phone,
                        position=position_in_word,
                        # No CTC engine reports syllable structure.
                        syllable=None,
                        stress=stress,
                        source=expected_source,
                    ),
                    observed=ObservedPhoneme(
                        top=observed_phone,
                        confidence=confidence,
                        nbest=nbest(
                            log_posteriors,
                            token_ids,
                            frame_start,
                            frame_end,
                            nbest_size,
                        ),
                        frame_start=frame_start,
                        frame_end=frame_end,
                    ),
                    timing=TimingInfo(
                        start_ms=start_ms,
                        end_ms=end_ms,
                        duration_ms=end_ms - start_ms,
                        source=timing_source,
                        frame_start=frame_start,
                        frame_end=frame_end,
                    ),
                    acoustic=AcousticEvidence(
                        measured_on="analysis",
                        energy_db=acoustics.energy_db,
                        relative_energy=acoustics.relative_energy,
                        f0_hz=acoustics.f0_hz,
                        voicing=acoustics.voiced,
                        spectral_features=acoustics.spectral_features(),
                    ),
                    # A CTC recogniser observes no stress or prominence.
                    prosody=ProsodyEvidence(),
                    engine_evidence={
                        "provider": provider,
                        "operation": operation,
                        "expected_phone_posterior": phone_posterior(
                            log_posteriors,
                            token_ids,
                            expected_phone,
                            frame_start,
                            frame_end,
                        ),
                        "extra_heard_phones": [
                            heard_detail(i)
                            for i in insertions_after.get(expected_index, [])
                        ],
                        "nbest_method": NBEST_METHOD,
                        "span_is_posterior_peak_not_duration": True,
                    },
                )
            )

        if word_frame_starts and word_frame_ends:
            word_start_ms = clock.start_ms(min(word_frame_starts))
            word_end_ms = clock.end_ms(max(word_frame_ends))
            word_timing = TimingInfo(
                start_ms=word_start_ms,
                end_ms=word_end_ms,
                duration_ms=word_end_ms - word_start_ms,
                source="engine",
                frame_start=min(word_frame_starts),
                frame_end=max(word_frame_ends),
            )
        else:
            # Every phone of the word was omitted: no engine timing exists.
            word_timing = TimingInfo(source="unknown")

        evidence: dict[str, Any] = {"provider": provider, "position": word_index}
        if word_evidence is not None:
            evidence.update(word_evidence(word_index))

        word_results.append(
            WordResult(
                word=word,
                expected_phonemes=list(group),
                timing=word_timing,
                phonemes=phoneme_results,
                engine_evidence=evidence,
            )
        )

    # Insertions before the first expected phone have no phoneme to attach to.
    leading = insertions_after.get(-1, [])
    if leading and word_results and word_results[0].phonemes:
        word_results[0].phonemes[0].engine_evidence["leading_heard_phones"] = [
            heard_detail(i) for i in leading
        ]

    return word_results


def operation_counts(word: WordResult) -> dict[str, int]:
    """Count of each alignment operation in a word (derived, not provider-reported)."""
    counts = {OP_MATCH: 0, OP_SUBSTITUTION: 0, OP_OMISSION: 0, "insertion": 0}
    for phoneme in word.phonemes:
        counts[phoneme.engine_evidence["operation"]] += 1
        counts["insertion"] += len(phoneme.engine_evidence["extra_heard_phones"])
    return counts
