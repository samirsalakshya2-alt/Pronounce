"""WavLM engine: UNRESOLVED.

WavLM (`microsoft/wavlm-*`) is a self-supervised representation model. It has
no phone vocabulary and no scoring head, so on its own it produces no
pronunciation evidence. A WavLM engine therefore needs a fine-tuned checkpoint,
and none found so far is defensible as *the* WavLM evidence provider.
Investigated on 2026-10-01/02 (Hugging Face Hub search: wavlm + phoneme / phone /
ipa / pronunciation / gop / espeak / timit / mispronunciation / speechocean):

1. `speech31/wavlm-large-english-phoneme` (and `-english-ipa`, same layout)
   Rejected: not WavLM. The config declares `Wav2Vec2ForCTC` / `model_type:
   wav2vec2` (transformers 4.11.3), and the checkpoint's 417 tensors contain no
   WavLM-specific parameters (no gated relative position bias); all 415 backbone
   tensors are `wav2vec2.*`. Using it would be a Wav2Vec2 model under a WavLM
   name. Also: no model card, no license, no stated training data.

2. `Jianshu001/wavlm-phoneme-scorer` (MIT)
   The only genuine candidate: a real `WavLMModel` (WavLM-Large, top 6 layers
   fine-tuned) with an MLP head giving a per-phone score (0-100) and error
   probability. Verified by inspecting `wavlm_finetuned.pt` (revision
   19f6b967): 487 backbone tensors including 73 WavLM-specific ones, plus
   score/error/phone-embedding heads; its stored test metrics (AUC 0.870,
   Pearson 0.645, MAE 16.5) match the model card. It loads under
   `torch.load(weights_only=True)` once `numpy._core.multiarray.scalar` and
   numpy dtypes are allowlisted (the stored metrics are numpy scalars), so the
   upstream `weights_only=False` is not needed. Not adopted, because:
     * it was trained and evaluated on children's speech only (11.6K recordings,
       dataset not public); validity for an adult speaker is unknown;
     * single author, created 2026-03, no downloads, no independent use;
     * its phone timing and GOP feature come from a *second* model,
       `facebook/wav2vec2-xlsr-53-espeak-cv-ft` (CTC forced alignment), so only
       the score/error head is WavLM evidence;
     * it needs that model (~1.2 GB), the `microsoft/wavlm-large` config,
       `g2p_en` (+ NLTK data) and vendored third-party inference code;
     * it scores expected phones only: no observed phone, no N-best.
   Decision (2026-10-02, project owner): NOT adopted. WavLM stays unresolved
   until a defensible adult-speech checkpoint is identified.

This engine reports `blocked` with `unresolved_engine`, and the findings are
carried in the result.
"""

from __future__ import annotations

from pathlib import Path

from pronunciation_lab.benchmark.base import (
    ErrorType,
    PronunciationEngine,
    unavailable_result,
)
from pronunciation_lab.benchmark.schema import ProcessingError, PronunciationResult

CANDIDATES = {
    "speech31/wavlm-large-english-phoneme": {
        "verdict": "rejected",
        "reason": "Wav2Vec2ForCTC architecture; no WavLM-specific tensors in checkpoint",
        "also": ["no model card", "no license", "no stated training data"],
    },
    "speech31/wavlm-large-english-ipa": {
        "verdict": "rejected",
        "reason": "same layout as speech31/wavlm-large-english-phoneme (Wav2Vec2ForCTC config)",
    },
    "Jianshu001/wavlm-phoneme-scorer": {
        "verdict": "not adopted (owner decision 2026-10-02)",
        "reason": "genuine WavLM-Large + scoring head, but trained on children's speech only",
        "verified": {
            "revision": "19f6b9675d02a61dcd5b2aec0fdcc4364c1fc9da",
            "wavlm_specific_tensors": 73,
            "safe_load": "weights_only=True with numpy scalar/dtype allowlisted",
            "stored_metrics_match_model_card": True,
        },
        "requires": [
            "facebook/wav2vec2-xlsr-53-espeak-cv-ft (forced alignment + GOP)",
            "microsoft/wavlm-large config",
            "g2p_en and NLTK data",
            "vendored inference code",
        ],
        "evidence_it_would_provide": [
            "per-phone score 0-100 (WavLM head)",
            "per-phone error probability (WavLM head)",
            "GOP and phone timing (from the wav2vec2-xlsr-53 aligner, not WavLM)",
        ],
        "evidence_it_would_not_provide": ["observed phone", "N-best", "stress/prosody"],
    },
}


class WavLMEngine(PronunciationEngine):
    name = "wavlm"
    mode = "local"
    version = None
    model_id = None

    def readiness(self) -> dict[str, object]:
        return {
            "engine": self.name,
            "state": "unresolved",
            "reason": ErrorType.UNRESOLVED_ENGINE,
            "candidates": CANDIDATES,
        }

    def analyze(
        self,
        audio_path: Path,
        expected_text: str,
        *,
        recording_id: str,
        original_path: Path | None = None,
    ) -> PronunciationResult:
        return unavailable_result(
            self,
            audio_path,
            expected_text,
            recording_id=recording_id,
            original_path=original_path,
            status="blocked",
            error=ProcessingError(
                type=ErrorType.UNRESOLVED_ENGINE,
                message=(
                    "No defensible WavLM phoneme/pronunciation checkpoint has been "
                    "adopted; see pronunciation_lab.benchmark.engines.wavlm."
                ),
                stage="model_selection",
                retryable=False,
            ),
            engine_evidence={"provider": self.name, "readiness": self.readiness()},
        )
