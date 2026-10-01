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
from openpronounce.device import get_device

from pronunciation_lab.acoustics import waveform_rms
from pronunciation_lab.benchmark import ctc
from pronunciation_lab.benchmark.base import (
    EngineUnavailable,
    ErrorType,
    InvalidAudio,
    PronunciationEngine,
    require_min_samples,
)
from pronunciation_lab.benchmark.frames import (
    WAV2VEC2_RECEPTIVE_FIELD_SAMPLES,
    frame_clock,
)
from pronunciation_lab.benchmark.schema import (
    EngineInfo,
    ProcessingError,
    ProcessingInfo,
    PronunciationResult,
    RecordingAudio,
    RecordingInfo,
    TargetInfo,
)

SAMPLE_RATE = 16_000

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
        try:
            op_phones.recognize_phones(silence, sampling_rate=SAMPLE_RATE, lang=self.lang)
        except OSError as exc:
            raise EngineUnavailable(
                ErrorType.MODEL_UNAVAILABLE,
                f"{op_phones.PHONE_MODEL_NAME} could not be loaded: {exc}",
                stage="model_load",
                retryable=True,
            ) from exc
        self._model_load_ms = (time.perf_counter() - start) * 1000.0
        self._warm = True

        return self._model_load_ms

    # ------------------------------------------------------------------
    # Shared CTC helpers
    # ------------------------------------------------------------------

    _align = staticmethod(ctc.align)

    def _phone_token_ids(self, vocab: tuple[str, ...]) -> dict[str, list[int]]:
        """Map each OpenPronounce-normalized phone to the vocabulary ids behind it."""
        return ctc.phone_token_ids(
            vocab, lambda token: op_phones.normalize_phone(token, self.lang)
        )

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

        # 1. Waveform, before the model: a bad file should fail without paying for
        #    a model load. OpenPronounce's recognisers take a float32 waveform,
        #    not a path; load_audio also resamples to 16 kHz mono for us.
        try:
            waveform = openpronounce.load_audio(str(audio_path), sr=SAMPLE_RATE)
        except Exception as exc:  # noqa: BLE001 - librosa/ffmpeg raise several types
            raise InvalidAudio(
                ErrorType.AUDIO_UNREADABLE, f"{type(exc).__name__}: {exc}"
            ) from exc
        require_min_samples(waveform.size, WAV2VEC2_RECEPTIVE_FIELD_SAMPLES, SAMPLE_RATE)

        model_load_ms = self.warmup() if not self._warm else None
        run_type = "cold" if model_load_ms is not None else "warm"

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
                    type=ErrorType.EXPECTED_PHONES_MISMATCH,
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

        # 4. Align, then assemble per-word, per-phoneme evidence.
        word_results = ctc.build_words(
            provider=self.name,
            words=words,
            expected_groups=expected_groups,
            heard_phones=heard_phones,
            heard_confidences=heard_confidences,
            heard_spans=heard_spans,
            log_posteriors=log_posteriors,
            token_ids=phone_token_ids,
            clock=clock,
            waveform=waveform,
            sample_rate=SAMPLE_RATE,
            reference_rms=reference_rms,
            nbest_size=self.nbest_size,
            expected_source="espeak, OpenPronounce-normalized",
            # OpenPronounce reports neither syllable structure nor stress, and
            # espeak's stress marks are stripped before comparison. Left null
            # rather than guessed.
            expected_stress=None,
            word_evidence=lambda word_index: {
                # OpenPronounce's own per-word report, verbatim, for the words it
                # chose to flag. Absent means "not flagged", which is not the
                # same as "correct".
                "provider_report": provider_word_reports.get(word_index),
                "flagged_by_provider": word_index in provider_word_reports,
            },
        )

        postprocessing_ms = (time.perf_counter() - postprocessing_start) * 1000.0
        wall_time_ms = (time.perf_counter() - wall_start) * 1000.0

        result = PronunciationResult(
            status="partial" if errors else "ok",
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
                model=op_phones.PHONE_MODEL_NAME,
            ),
            phone_set="ipa-openpronounce-normalized",
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
                device=str(get_device()),
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
