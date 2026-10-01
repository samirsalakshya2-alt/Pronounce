"""OpenPronounce adapter.

OpenPronounce is treated as an evidence-producing engine, not a scorer. Only its
phone recogniser is used:

    openpronounce.load_audio   -> waveform
    phones.recognize_phones    -> phones, confidences, frame spans, log posteriors
    phones.compare_phones      -> expected phones per word, PER, per-word reports

`compare_audio_with_text` is deliberately **not** called on this path. It
synthesises a reference utterance (gTTS by default, i.e. a network request per
analysis) and loads a second ~1.2 GB ASR checkpoint, neither of which is needed
for phoneme-level evidence. Energy, F0 and voicing are instead measured directly
from the analysis waveform over the recognised spans (see
`pronunciation_lab.acoustics`), which keeps them engine-independent and offline.

What is preserved, and what is deliberately absent:

* N-best candidates are derived from the model's own frame posteriors over each
  span. They are real posteriors, never placeholders. The full posteriorgram is
  too large to embed in a JSON result, so it is kept on the engine instance
  (`last_recognition`) and only described in the result.
* Stress and prominence are left null: this engine reports no stress, and
  espeak's stress marks are stripped before the phone comparison happens.
* Span duration is *not* phone duration. CTC emits a phone at a posterior peak,
  so a span locates a phone reliably but does not measure its extent. Nothing
  here is derived from span length alone.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import openpronounce
from openpronounce import phones as op_phones

from pronunciation_lab.acoustics import measure_span, waveform_rms
from pronunciation_lab.benchmark.base import PronunciationEngine
from pronunciation_lab.benchmark.frames import frame_clock
from pronunciation_lab.benchmark.schema import (
    AcousticEvidence,
    EngineInfo,
    ExpectedPhoneme,
    NBestCandidate,
    ObservedPhoneme,
    PhonemeResult,
    ProcessingError,
    ProcessingInfo,
    PronunciationResult,
    ProsodyEvidence,
    RecordingAudio,
    RecordingInfo,
    TargetInfo,
    TimingInfo,
    WordResult,
)

SAMPLE_RATE = 16_000

# Alignment operations recorded per expected phoneme.
OP_MATCH = "match"
OP_SUBSTITUTION = "substitution"
OP_OMISSION = "omission"


class OpenPronounceEngine(PronunciationEngine):
    """Adapter from OpenPronounce's phone recogniser to the benchmark schema."""

    name = "openpronounce"
    mode = "local"

    def __init__(self, *, lang: str = "en", nbest_size: int = 5) -> None:
        self.lang = lang
        self.nbest_size = nbest_size
        self.version = getattr(openpronounce, "__version__", None)

        self._model_load_ms: float | None = None
        self._warm = False

        #: Full `PhoneRecognition` from the most recent `analyze()` call,
        #: including the (frames x vocab) log posteriors. Kept here rather than
        #: in the result because the array is far too large to serialise.
        self.last_recognition: Any | None = None

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------

    def warmup(self) -> float:
        """Load the phone model, returning the load time in ms.

        OpenPronounce loads its model lazily inside the first recognition call,
        which would otherwise charge the model load to the first recording's
        inference time. Running one tiny forward pass first keeps the benchmark's
        timing attribution honest.
        """
        if self._warm:
            return self._model_load_ms or 0.0

        start = time.perf_counter()
        silence = np.zeros(SAMPLE_RATE // 10, dtype=np.float32)
        op_phones.recognize_phones(silence, sampling_rate=SAMPLE_RATE, lang=self.lang)
        self._model_load_ms = (time.perf_counter() - start) * 1000.0
        self._warm = True

        return self._model_load_ms

    # ------------------------------------------------------------------
    # Alignment
    # ------------------------------------------------------------------

    @staticmethod
    def _align(
        expected: list[str],
        heard: list[str],
    ) -> list[tuple[int | None, int | None]]:
        """Levenshtein alignment of expected against heard phones.

        Returns `(expected_index, observed_index)` pairs in sequence order;
        `None` marks an omission (no heard phone) or an insertion (no expected
        phone). Deterministic and transparent by design -- the benchmark needs to
        be able to show why a phoneme was called an omission.
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

    # ------------------------------------------------------------------
    # N-best from the model's own posteriors
    # ------------------------------------------------------------------

    def _phone_token_ids(self, vocab: tuple[str, ...]) -> dict[str, list[int]]:
        """Map each normalized phone to the vocabulary ids that normalize to it."""
        mapping: dict[str, list[int]] = {}

        for index, token in enumerate(vocab):
            if token.startswith("<") and token.endswith(">"):
                continue

            phone = op_phones.normalize_phone(token, self.lang)
            if not phone:
                continue

            mapping.setdefault(phone, []).append(index)

        return mapping

    @staticmethod
    def _nbest(
        log_posteriors: np.ndarray,
        phone_token_ids: dict[str, list[int]],
        frame_start: int,
        frame_end: int,
        size: int,
    ) -> list[NBestCandidate]:
        """Top-`size` phones by peak posterior over frames `[frame_start, frame_end)`.

        Each probability is the highest posterior that phone reaches anywhere in
        the span (over any of the vocabulary tokens that normalize to it). These
        are genuine model posteriors, but they are peaks over a span rather than
        a normalized distribution, so they do not sum to 1.
        """
        if frame_end <= frame_start or log_posteriors.size == 0:
            return []

        block = log_posteriors[frame_start:frame_end]
        if block.size == 0:
            return []

        scored = [
            (phone, float(np.exp(block[:, ids].max())))
            for phone, ids in phone_token_ids.items()
        ]
        scored.sort(key=lambda item: item[1], reverse=True)

        return [
            NBestCandidate(phoneme=phone, probability=probability, score=None)
            for phone, probability in scored[:size]
        ]

    @staticmethod
    def _phone_posterior(
        log_posteriors: np.ndarray,
        phone_token_ids: dict[str, list[int]],
        phone: str,
        frame_start: int,
        frame_end: int,
    ) -> float | None:
        """Peak posterior of one specific phone over a frame range.

        For an omitted phoneme this answers "was the expected sound really
        absent, or merely too weak to win the decode?" -- without claiming either.
        """
        ids = phone_token_ids.get(phone)
        if not ids or frame_end <= frame_start or log_posteriors.size == 0:
            return None

        block = log_posteriors[frame_start:frame_end]
        if block.size == 0:
            return None

        return float(np.exp(block[:, ids].max()))

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        audio_path: Path,
        expected_text: str,
        *,
        recording_id: str,
        original_path: Path | None = None,
    ) -> PronunciationResult:
        audio_path = Path(audio_path)
        original_path = Path(original_path) if original_path is not None else audio_path

        errors: list[ProcessingError] = []

        wall_start = time.perf_counter()

        model_load_ms = self.warmup() if not self._warm else None
        run_type = "cold" if model_load_ms is not None else "warm"

        # 1. Waveform. OpenPronounce's recognisers take a float32 waveform, not a
        #    path; load_audio also resamples to 16 kHz mono for us.
        waveform = openpronounce.load_audio(str(audio_path), sr=SAMPLE_RATE)
        duration_ms = waveform.size * 1000.0 / SAMPLE_RATE
        reference_rms = waveform_rms(waveform)

        # 2. Phone recognition.
        inference_start = time.perf_counter()
        recognition = op_phones.recognize_phones(
            waveform,
            sampling_rate=SAMPLE_RATE,
            lang=self.lang,
        )
        inference_ms = (time.perf_counter() - inference_start) * 1000.0

        self.last_recognition = recognition

        heard_phones = list(recognition.phones)
        heard_confidences = list(recognition.confidences)
        heard_spans = list(recognition.spans)
        log_posteriors = np.asarray(recognition.log_posteriors)

        postprocessing_start = time.perf_counter()

        clock = frame_clock(
            n_frames=int(log_posteriors.shape[0]) if log_posteriors.ndim == 2 else 0,
            n_samples=int(waveform.size),
            sample_rate=SAMPLE_RATE,
        )

        phone_token_ids = self._phone_token_ids(tuple(recognition.vocab))

        # 3. Comparison against the target text.
        comparison = op_phones.compare_phones(
            recognition,
            expected_text,
            lang=self.lang,
        )

        # Expected phones per word, from OpenPronounce's own phonemization. This
        # is the only source used; there is no text-to-phone fallback, because the
        # expected sequence must match what compare_phones scored against.
        words, expected_groups = op_phones.get_expected_phones(
            expected_text,
            lang=self.lang,
        )

        if len(expected_groups) != len(comparison.get("expected_phones", [])):
            errors.append(
                ProcessingError(
                    type="expected_phones_mismatch",
                    message=(
                        "get_expected_phones returned "
                        f"{len(expected_groups)} word groups but compare_phones "
                        f"returned {len(comparison.get('expected_phones', []))}."
                    ),
                )
            )

        # OpenPronounce reports per-word detail only for words it flagged; index
        # it so the matching WordResult can carry it verbatim.
        provider_word_reports: dict[int, dict[str, Any]] = {
            report["position"]: report
            for report in comparison.get("errors", [])
            if isinstance(report, dict) and "position" in report
        }

        # 4. Flatten the expected sequence, remembering word ownership.
        expected_flat: list[str] = []
        owner_word: list[int] = []
        index_in_word: list[int] = []

        for word_index, group in enumerate(expected_groups):
            for position, phone in enumerate(group):
                expected_flat.append(phone)
                owner_word.append(word_index)
                index_in_word.append(position)

        # 5. Align, then split the alignment into matches, omissions, insertions.
        alignment = self._align(expected_flat, heard_phones)

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
                    span = heard_spans[matched[i]]
                    previous_end = span[1]
                    break

            following_start = clock.n_frames
            for i in range(expected_index + 1, len(expected_flat)):
                if i in matched:
                    span = heard_spans[matched[i]]
                    following_start = span[0]
                    break

            if following_start <= previous_end:
                following_start = min(previous_end + 1, max(clock.n_frames, 1))

            return previous_end, following_start

        # 6. Build per-word, per-phoneme results.
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
                    # The engine placed nothing here; the region is inferred from
                    # the neighbouring phones so the user can still listen to it.
                    timing_source = "derived"
                    operation = OP_OMISSION

                start_ms, end_ms = clock.span_ms((frame_start, frame_end))

                acoustics = measure_span(
                    waveform,
                    SAMPLE_RATE,
                    start_ms,
                    end_ms,
                    reference_rms=reference_rms,
                )

                nbest = self._nbest(
                    log_posteriors,
                    phone_token_ids,
                    frame_start,
                    frame_end,
                    self.nbest_size,
                )

                expected_posterior = self._phone_posterior(
                    log_posteriors,
                    phone_token_ids,
                    expected_phone,
                    frame_start,
                    frame_end,
                )

                extra_heard = [
                    heard_detail(i)
                    for i in insertions_after.get(expected_index, [])
                ]

                phoneme_results.append(
                    PhonemeResult(
                        position=expected_index,
                        expected=ExpectedPhoneme(
                            phoneme=expected_phone,
                            position=position_in_word,
                            # OpenPronounce reports neither syllable structure nor
                            # stress, and espeak's stress marks are stripped
                            # before comparison. Left null rather than guessed.
                            syllable=None,
                            stress=None,
                        ),
                        observed=ObservedPhoneme(
                            top=observed_phone,
                            confidence=confidence,
                            nbest=nbest,
                            frame_start=frame_start,
                            frame_end=frame_end,
                        ),
                        timing=TimingInfo(
                            start_ms=start_ms,
                            end_ms=end_ms,
                            duration_ms=(
                                end_ms - start_ms
                                if start_ms is not None and end_ms is not None
                                else None
                            ),
                            source=timing_source,
                            frame_start=frame_start,
                            frame_end=frame_end,
                        ),
                        acoustic=AcousticEvidence(
                            energy_db=acoustics.energy_db,
                            relative_energy=acoustics.relative_energy,
                            f0_hz=acoustics.f0_hz,
                            voicing=acoustics.voiced,
                            spectral_features=acoustics.spectral_features(),
                        ),
                        # No stress or prominence evidence from this engine.
                        prosody=ProsodyEvidence(),
                        engine_evidence={
                            "provider": self.name,
                            "operation": operation,
                            "expected_phone_posterior": expected_posterior,
                            "extra_heard_phones": extra_heard,
                            "nbest_method": (
                                "peak frame posterior per normalized phone over "
                                "the span; peaks over a span, not a normalized "
                                "distribution"
                            ),
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

            word_results.append(
                WordResult(
                    word=word,
                    expected_phonemes=list(group),
                    timing=word_timing,
                    phonemes=phoneme_results,
                    engine_evidence={
                        "provider": self.name,
                        "position": word_index,
                        # OpenPronounce's own per-word report, verbatim, for the
                        # words it chose to flag. Absent means "not flagged",
                        # which is not the same as "correct".
                        "provider_report": provider_word_reports.get(word_index),
                        "flagged_by_provider": word_index in provider_word_reports,
                    },
                )
            )

        postprocessing_ms = (time.perf_counter() - postprocessing_start) * 1000.0
        wall_time_ms = (time.perf_counter() - wall_start) * 1000.0

        result = PronunciationResult(
            schema_version="0.1",
            recording=RecordingInfo(
                id=recording_id,
                audio=RecordingAudio(
                    original_path=str(original_path),
                    analysis_path=str(audio_path),
                    # Describes the waveform that was analysed, which load_audio
                    # guarantees is 16 kHz mono, not necessarily the original file.
                    sample_rate_hz=SAMPLE_RATE,
                    channels=1,
                    duration_ms=duration_ms,
                ),
                target=TargetInfo(text=expected_text),
            ),
            engine=EngineInfo(
                name=self.name,
                version=self.version,
                mode="local",
            ),
            processing=ProcessingInfo(
                wall_time_ms=wall_time_ms,
                model_load_ms=model_load_ms,
                inference_ms=inference_ms,
                postprocessing_ms=postprocessing_ms,
                realtime_factor=(
                    wall_time_ms / duration_ms if duration_ms > 0 else None
                ),
                run_type=run_type,
                timing_method="monotonic_clock",
            ),
            words=word_results,
            engine_evidence={
                "provider": self.name,
                "phone_model": op_phones.PHONE_MODEL_NAME,
                "lang": self.lang,
                "recognition": {
                    "phones": heard_phones,
                    "confidences": [float(c) for c in heard_confidences],
                    "frame_spans": [list(span) for span in heard_spans],
                },
                "comparison": comparison,
                "frame_clock": clock.as_dict(),
                "posteriorgram": {
                    "n_frames": int(log_posteriors.shape[0])
                    if log_posteriors.ndim == 2
                    else 0,
                    "vocab_size": len(recognition.vocab),
                    "retained_in_result": False,
                    "note": (
                        "Full (frames x vocab) log posteriors are too large to "
                        "serialise; available on the engine instance as "
                        "`last_recognition.log_posteriors` for the most recent "
                        "analysis."
                    ),
                },
                "not_called": {
                    "compare_audio_with_text": (
                        "Skipped deliberately: needs a synthesised reference "
                        "(network TTS by default) and a second ASR checkpoint, "
                        "neither required for phoneme-level evidence."
                    )
                },
            },
            errors=errors,
        )

        return result
