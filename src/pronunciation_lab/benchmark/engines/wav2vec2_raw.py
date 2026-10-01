"""Raw Wav2Vec2 phone recogniser: the checkpoint's own CTC output, nothing added.

Model: `facebook/wav2vec2-lv-60-espeak-cv-ft`, pinned to a revision.

OpenPronounce runs the same checkpoint. This engine exists to separate what the
*model* says from what OpenPronounce's post-processing makes of it, so it applies
none of that post-processing:

* Decoding is plain greedy CTC (frame argmax, repeats collapsed, blank and
  special tokens dropped). No phone merging (ɔ→ɑ, ɾ→t, ᵻ→ɪ, ...), no length-mark
  removal, no collapsing of adjacent repeats such as "ɚ ɹ".
* Expected phones come from espeak directly (phonemizer, `en-us`), with stress
  marks split off into `ExpectedPhoneme.stress`. Token for token, the espeak
  output is the inventory the checkpoint was fine-tuned on.
* The alignment is unit-cost and phonetically unweighted, with no alternate
  pronunciations of function words. Word-level reports are derived counts only;
  the model itself reports nothing per word.
* N-best candidates are raw vocabulary tokens. The vocabulary is multilingual
  (392 tokens), so a candidate can be a token no English speaker would produce;
  that is what the model's posteriors say, and it is kept.

What is shared with OpenPronounce, and therefore NOT independent evidence:
the checkpoint, its frame posteriors (identical up to audio-loading differences),
espeak as the source of expected phones, and the acoustic measurements (which
depend only on the waveform and the spans). What *is* independent: the decoding
normalization, the expected-phone inventory, expected stress, the alignment, and
any substitution/omission decision that follows from them.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

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
from pronunciation_lab.benchmark.waveform import load_analysis_waveform

MODEL_ID = "facebook/wav2vec2-lv-60-espeak-cv-ft"
# The revision the local cache's `main` pointed at when this adapter was written.
MODEL_REVISION = "ae45363bf3413b374fecd9dc8bc1df0e24c3b7f4"

ESPEAK_LANGUAGE = "en-us"

_WORD_RE = re.compile(r"[\w']+")
_STRESS_MARKS = {"ˈ": "primary", "ˌ": "secondary"}


@dataclass(frozen=True)
class RawRecognition:
    """Everything the model produced for one recording (kept off the result)."""

    tokens: list[str]
    confidences: list[float]
    spans: list[tuple[int, int]]
    log_posteriors: np.ndarray
    vocab: tuple[str, ...]


def expected_phones(text: str) -> tuple[list[str], list[list[str]], list[list[str | None]], dict[str, Any]]:
    """Words, espeak phones per word, and espeak's lexical stress per phone.

    Stress marks are split off the phone they precede rather than discarded, so
    the phone matches the model's vocabulary and the stress is still reported.
    """
    from phonemizer import phonemize
    from phonemizer.backend import EspeakBackend
    from phonemizer.separator import Separator

    words = [w.lower() for w in _WORD_RE.findall(text)]
    details: dict[str, Any] = {
        "g2p": "espeak via phonemizer",
        "language": ESPEAK_LANGUAGE,
        "espeak_version": ".".join(str(v) for v in EspeakBackend.version()),
        "with_stress": True,
        "fallback_per_word": False,
    }

    def run(chunk: str, word_sep: str) -> str:
        return phonemize(
            chunk,
            language=ESPEAK_LANGUAGE,
            backend="espeak",
            strip=True,
            with_stress=True,
            preserve_punctuation=False,
            separator=Separator(phone=" ", word=word_sep, syllable=""),
        )

    raw_groups = [g.split() for g in run(" ".join(words), " | ").split("|")] if words else []

    if len(raw_groups) != len(words):
        # espeak can merge or split words; fall back to one call per word so
        # word ownership stays exact.
        details["fallback_per_word"] = True
        raw_groups = [run(word, "").split() for word in words]

    groups: list[list[str]] = []
    stresses: list[list[str | None]] = []

    for raw in raw_groups:
        phones: list[str] = []
        marks: list[str | None] = []
        for token in raw:
            stress = None
            while token and token[0] in _STRESS_MARKS:
                stress = _STRESS_MARKS[token[0]]
                token = token[1:]
            if token:
                phones.append(token)
                marks.append(stress)
        groups.append(phones)
        stresses.append(marks)

    return words, groups, stresses, details


class Wav2Vec2RawEngine(PronunciationEngine):
    """Raw CTC phone evidence from `facebook/wav2vec2-lv-60-espeak-cv-ft`."""

    name = "wav2vec2_raw"
    mode = "local"
    model_id = MODEL_ID

    def __init__(
        self,
        *,
        revision: str = MODEL_REVISION,
        device: str = "cpu",
        nbest_size: int = 5,
        local_files_only: bool = False,
    ) -> None:
        import transformers

        self.revision = revision
        self.device = device
        self.nbest_size = nbest_size
        self.local_files_only = local_files_only
        self.version = f"transformers {transformers.__version__}"

        self._processor = None
        self._model = None
        self._vocab: tuple[str, ...] = ()
        self._blank_id = 0
        self._commit_hash: str | None = None

        #: Raw recognition from the most recent `analyze()` call, including the
        #: (frames x vocab) log posteriors, which are too large to serialise.
        self.last_recognition: RawRecognition | None = None

    # ------------------------------------------------------------------
    # Model lifecycle
    # ------------------------------------------------------------------

    def warmup(self) -> float:
        """Load processor and model, returning the load time in ms."""
        if self._model is not None:
            return 0.0

        start = time.perf_counter()
        try:
            from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

            processor = Wav2Vec2Processor.from_pretrained(
                MODEL_ID,
                revision=self.revision,
                local_files_only=self.local_files_only,
            )
            model = Wav2Vec2ForCTC.from_pretrained(
                MODEL_ID,
                revision=self.revision,
                local_files_only=self.local_files_only,
            )
        except OSError as exc:
            raise EngineUnavailable(
                ErrorType.MODEL_UNAVAILABLE,
                f"{MODEL_ID}@{self.revision} could not be loaded: {exc}",
                stage="model_load",
                retryable=True,
            ) from exc

        model.to(self.device)
        model.eval()

        vocab = processor.tokenizer.get_vocab()
        self._vocab = tuple(t for t, _ in sorted(vocab.items(), key=lambda kv: kv[1]))
        self._blank_id = int(processor.tokenizer.pad_token_id)
        self._commit_hash = getattr(model.config, "_commit_hash", None)
        self._processor = processor
        self._model = model

        # One tiny forward pass, as the OpenPronounce adapter does, so that
        # first-call framework initialisation is charged to model loading and
        # not to the first recording's inference time.
        self._log_posteriors(np.zeros(1600, dtype=np.float32), 16_000)

        return (time.perf_counter() - start) * 1000.0

    def _log_posteriors(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        import torch

        inputs = self._processor(samples, sampling_rate=sample_rate, return_tensors="pt")
        with torch.no_grad():
            logits = self._model(inputs.input_values.to(self.device)).logits[0]
        return torch.log_softmax(logits.float(), dim=-1).cpu().numpy()

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

        # Input first: a bad file should fail without paying for a model load.
        try:
            waveform = load_analysis_waveform(audio_path)
        except Exception as exc:  # noqa: BLE001 - libsndfile raises several types
            raise InvalidAudio(
                ErrorType.AUDIO_UNREADABLE, f"{type(exc).__name__}: {exc}"
            ) from exc
        samples = waveform.samples
        require_min_samples(
            samples.size, WAV2VEC2_RECEPTIVE_FIELD_SAMPLES, waveform.sample_rate
        )

        cold = self._model is None
        model_load_ms = self.warmup() if cold else None
        reference_rms = waveform_rms(samples)

        # 1. Model posteriors.
        inference_start = time.perf_counter()
        log_posteriors = self._log_posteriors(samples, waveform.sample_rate)
        inference_ms = (time.perf_counter() - inference_start) * 1000.0

        postprocessing_start = time.perf_counter()

        # 2. Raw greedy decode.
        tokens, confidences, spans = ctc.greedy_decode(
            log_posteriors, self._vocab, self._blank_id
        )
        self.last_recognition = RawRecognition(
            tokens=tokens,
            confidences=confidences,
            spans=spans,
            log_posteriors=log_posteriors,
            vocab=self._vocab,
        )

        clock = frame_clock(
            n_frames=int(log_posteriors.shape[0]),
            n_samples=int(samples.size),
            sample_rate=waveform.sample_rate,
        )

        # 3. Expected phones, straight from espeak.
        g2p_start = time.perf_counter()
        words, groups, stresses, g2p_details = expected_phones(expected_text)
        g2p_ms = (time.perf_counter() - g2p_start) * 1000.0

        vocab_set = set(self._vocab)
        out_of_vocab = sorted({p for g in groups for p in g if p not in vocab_set})
        if out_of_vocab:
            # Kept as expected phones (they are what espeak says), but they can
            # never be matched, so the comparison is incomplete.
            errors.append(
                ProcessingError(
                    type=ErrorType.EXPECTED_PHONES_MISMATCH,
                    message=f"espeak phones not in the model vocabulary: {out_of_vocab}",
                    stage="g2p",
                    retryable=False,
                )
            )

        # 4. Align and assemble evidence. Every vocabulary token is its own phone.
        token_ids = ctc.phone_token_ids(self._vocab)
        word_results = ctc.build_words(
            provider=self.name,
            words=words,
            expected_groups=groups,
            heard_phones=tokens,
            heard_confidences=confidences,
            heard_spans=spans,
            log_posteriors=log_posteriors,
            token_ids=token_ids,
            clock=clock,
            waveform=samples,
            sample_rate=waveform.sample_rate,
            reference_rms=reference_rms,
            nbest_size=self.nbest_size,
            expected_source="espeak",
            expected_stress=stresses,
            word_evidence=lambda word_index: {
                # The model reports nothing per word.
                "provider_report": None,
            },
        )
        for word in word_results:
            word.engine_evidence["derived_operation_counts"] = ctc.operation_counts(word)

        postprocessing_ms = (time.perf_counter() - postprocessing_start) * 1000.0
        wall_time_ms = (time.perf_counter() - wall_start) * 1000.0

        n_expected = sum(len(g) for g in groups)
        edits = sum(
            counts[ctc.OP_SUBSTITUTION] + counts[ctc.OP_OMISSION] + counts["insertion"]
            for counts in (w.engine_evidence["derived_operation_counts"] for w in word_results)
        )
        if word_results and word_results[0].phonemes:
            edits += len(
                word_results[0].phonemes[0].engine_evidence.get("leading_heard_phones", [])
            )

        return PronunciationResult(
            status="partial" if errors else "ok",
            recording=RecordingInfo(
                id=recording_id,
                audio=RecordingAudio(
                    original_path=str(original_path),
                    analysis_path=str(audio_path),
                    sample_rate_hz=waveform.sample_rate,
                    channels=1,
                    duration_ms=waveform.duration_ms,
                ),
                target=TargetInfo(text=expected_text),
            ),
            engine=EngineInfo(
                name=self.name,
                version=self.version,
                mode="local",
                model=f"{MODEL_ID}@{self.revision}",
            ),
            phone_set="ipa-espeak-raw",
            processing=ProcessingInfo(
                wall_time_ms=wall_time_ms,
                model_load_ms=model_load_ms,
                inference_ms=inference_ms,
                postprocessing_ms=postprocessing_ms,
                realtime_factor=(
                    wall_time_ms / waveform.duration_ms if waveform.duration_ms > 0 else None
                ),
                run_type="cold" if cold else "warm",
                timing_method="monotonic_clock",
                device=self.device,
                # g2p is part of postprocessing_ms. espeak's first call in a
                # process includes its own initialisation.
                stages_ms={"g2p": g2p_ms},
            ),
            words=word_results,
            engine_evidence={
                "provider": self.name,
                "model": MODEL_ID,
                "revision": self.revision,
                "resolved_commit": self._commit_hash,
                "decoding": (
                    "greedy CTC: frame argmax, repeats collapsed, blank and "
                    "special tokens dropped; no phone normalization"
                ),
                "normalization": None,
                "expected_phones": g2p_details,
                "audio_loading": waveform.describe(),
                "recognition": {
                    "phones": tokens,
                    "confidences": confidences,
                    "frame_spans": [list(s) for s in spans],
                },
                # Unit-cost edit distance over raw tokens; derived here, not
                # reported by the model.
                "derived_phone_error_rate": (edits / n_expected) if n_expected else None,
                "frame_clock": clock.as_dict(),
                "posteriorgram": {
                    "n_frames": int(log_posteriors.shape[0]),
                    "vocab_size": len(self._vocab),
                    "retained_in_result": False,
                    "note": (
                        "Full (frames x vocab) log posteriors are kept on the "
                        "engine as `last_recognition.log_posteriors`."
                    ),
                },
                "shared_with_openpronounce": [
                    "checkpoint and frame posteriors",
                    "espeak as expected-phone source",
                    "acoustic measurements (depend only on waveform and spans)",
                ],
                "independent_of_openpronounce": [
                    "decoding normalization (none here)",
                    "expected-phone inventory and lexical stress",
                    "alignment and resulting substitution/omission calls",
                ],
            },
            errors=errors,
        )
