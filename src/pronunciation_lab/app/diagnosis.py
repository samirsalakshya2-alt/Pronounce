"""From an M1 `PronunciationResult` to what the user sees.

The view keeps the M2 discipline:

* every statement is "what the recogniser heard", never "what you got wrong";
* uncertainty is shown, not resolved: a substitution whose expected sound is
  still plausible, or whose top candidate barely wins, is *unclear*;
* a sound that was not decoded is *not detected*, and its location is marked
  as estimated, never as "missing";
* words whose alignment is unreliable (the M2 `alignment_suspect` rule) are
  *not interpreted*;
* every sound and word carries the exact millisecond span the evidence points
  at, plus a playback window around it, both on the analysis-audio timeline.

Thresholds are the M2 ones, imported, so the app and the benchmark agree.
"""

from __future__ import annotations

from typing import Any

from pronunciation_lab.app.phones import hint
from pronunciation_lab.benchmark.analysis import (
    AMBIGUOUS_MARGIN,
    PLAUSIBLE_POSTERIOR,
    alignment_suspect_words,
)
from pronunciation_lab.benchmark.base import ErrorType
from pronunciation_lab.benchmark.schema import PronunciationResult

# Playback windows. A CTC span marks where the recogniser was most sure of a
# sound (often 20 ms); too short to hear. The sound is played with context
# around that point; the exact span is still reported.
SOUND_CONTEXT_MS = 300.0
WORD_PAD_BEFORE_MS = 80.0
WORD_PAD_AFTER_MS = 120.0

CATEGORIES = ("expected", "different", "unclear", "not_detected", "not_interpreted")

WORD_PRIORITY = ("not_interpreted", "different", "not_detected", "unclear", "expected")

CATEGORY_TITLES = {
    "expected": "Heard as expected",
    "different": "Heard as a different sound",
    "unclear": "Unclear",
    "not_detected": "Not detected",
    "not_interpreted": "Not interpreted",
}

ERROR_MESSAGES = {
    ErrorType.AUDIO_TOO_SHORT: "The recording is too short to analyse.",
    ErrorType.AUDIO_UNREADABLE: "The audio could not be read.",
    ErrorType.MODEL_UNAVAILABLE: "The speech model is not available on this computer.",
    ErrorType.CREDENTIALS_UNAVAILABLE: "This engine needs credentials that are not configured.",
    ErrorType.INTEGRATION_UNVERIFIED: "This engine is not integrated yet.",
    ErrorType.UNRESOLVED_ENGINE: "This engine has no usable model yet.",
    ErrorType.ENGINE_EXCEPTION: "The analysis failed unexpectedly.",
}

CAVEATS = [
    "This shows what one speech-recognition model heard. It is not a teacher's "
    "judgement and can be wrong; listen to the segment to decide for yourself.",
    "Expected sounds come from a US-English dictionary (eSpeak en-us). A sound "
    "that differs from it may be an accent difference, not a mistake.",
    "Timings locate a sound; they do not measure how long it lasted.",
]


def _clamp(start: float, end: float, duration: float) -> tuple[float, float]:
    start = max(0.0, min(start, duration))
    end = max(start, min(end, duration))
    return start, end


def sound_window(start_ms: float, end_ms: float, duration_ms: float) -> tuple[float, float]:
    """A window of at least SOUND_CONTEXT_MS centred on the span, inside the audio."""
    centre = (start_ms + end_ms) / 2.0
    half = max((end_ms - start_ms) / 2.0, SOUND_CONTEXT_MS / 2.0)
    start, end = centre - half, centre + half
    # keep the requested width at the edges where possible
    if start < 0:
        end, start = end - start, 0.0
    if end > duration_ms:
        start, end = start - (end - duration_ms), duration_ms
    return _clamp(start, end, duration_ms)


def word_window(start_ms: float, end_ms: float, duration_ms: float) -> tuple[float, float]:
    return _clamp(start_ms - WORD_PAD_BEFORE_MS, end_ms + WORD_PAD_AFTER_MS, duration_ms)


def classify_sound(operation: str, expected_posterior: float | None, margin: float | None) -> str:
    """The user-facing category of one expected sound (alignment checked by the caller)."""
    plausible = (expected_posterior or 0.0) >= PLAUSIBLE_POSTERIOR
    if operation == "match":
        return "expected"
    if operation == "substitution":
        if plausible or (margin is not None and margin < AMBIGUOUS_MARGIN):
            return "unclear"
        return "different"
    if operation == "omission":
        return "unclear" if plausible else "not_detected"
    raise ValueError(f"unknown alignment operation {operation!r}")


def _sound_text(category: str, expected: str, observed: str | None, closest: str | None) -> str:
    if category == "expected":
        return f"Heard as expected: /{expected}/"
    if category == "different":
        return f"Heard as /{observed}/ where /{expected}/ was expected"
    if category == "unclear":
        if observed:
            return f"Unclear: closest to /{observed}/, but /{expected}/ is also possible"
        return f"Unclear: /{expected}/ was not clearly detected, but may be present"
    if category == "not_detected":
        best = f" (strongest candidate there: /{closest}/)" if closest and closest != expected else ""
        return f"Not detected: no clear /{expected}/ was found here{best}; location estimated"
    return "Not interpreted: the recogniser heard extra sounds around this word, so the alignment here is unreliable"


def error_view(result: PronunciationResult) -> dict[str, Any]:
    error = result.errors[0] if result.errors else None
    code = error.type if error else "unknown"
    return {
        "state": "unavailable" if result.status == "blocked" else "failed",
        "error": {
            "code": code,
            "message": ERROR_MESSAGES.get(code, "The analysis could not be completed."),
            "detail": error.message if error else None,
        },
    }


def build_view(result: PronunciationResult) -> dict[str, Any]:
    """User-facing view of one analysis. Pure function of the engine's result."""
    base: dict[str, Any] = {
        "engine": {
            "id": result.engine.name,
            "model": result.engine.model,
            "version": result.engine.version,
            "phone_set": result.phone_set,
        },
        "target_text": result.recording.target.text,
        "duration_ms": result.recording.audio.duration_ms,
        "audio_timeline": "analysis",
        "caveats": CAVEATS,
        "processing": {
            "wall_time_ms": result.processing.wall_time_ms,
            "inference_ms": result.processing.inference_ms,
            "run_type": result.processing.run_type,
        },
    }

    if result.status in ("failed", "blocked"):
        return base | error_view(result)

    duration = result.recording.audio.duration_ms
    heard = result.engine_evidence.get("recognition", {}).get("phones", [])
    if not heard:
        return base | {
            "state": "no_speech",
            "message": "No speech sounds were detected in this recording.",
            "words": [],
            "summary": {c: 0 for c in CATEGORIES},
        }

    suspects = alignment_suspect_words(result.words)
    words = []
    summary = {c: 0 for c in CATEGORIES}
    index = 0

    for wi, word in enumerate(result.words):
        sounds = []
        for p in word.phonemes:
            nbest = [(c.phoneme, c.probability) for c in p.observed.nbest]
            margin = (nbest[0][1] - nbest[1][1]) if len(nbest) > 1 else None
            operation = p.engine_evidence["operation"]
            if wi in suspects:
                category = "not_interpreted"
            else:
                category = classify_sound(operation, p.engine_evidence.get("expected_phone_posterior"), margin)
            summary[category] += 1

            start, end = p.timing.start_ms, p.timing.end_ms
            play_start, play_end = sound_window(start, end, duration)
            closest = nbest[0][0] if nbest else None
            sounds.append({
                "index": index,
                "expected": p.expected.phoneme,
                "expected_hint": hint(p.expected.phoneme),
                "expected_stress": p.expected.stress,
                "heard": p.observed.top,
                "heard_hint": hint(p.observed.top),
                "category": category,
                "text": _sound_text(category, p.expected.phoneme, p.observed.top, closest),
                "confidence": p.observed.confidence,
                "expected_probability": p.engine_evidence.get("expected_phone_posterior"),
                "alternatives": [
                    {"phone": ph, "probability": prob, "hint": hint(ph)} for ph, prob in nbest
                ],
                "extra_sounds_after": [x["phone"] for x in p.engine_evidence.get("extra_heard_phones", [])],
                "span_ms": [start, end],
                "timing_estimated": p.timing.source != "engine",
                "play_ms": [play_start, play_end],
                "acoustic": {
                    "relative_energy": p.acoustic.relative_energy,
                    "voiced": p.acoustic.voicing,
                    "pitch_hz": p.acoustic.f0_hz,
                },
            })
            index += 1

        # A word is labelled by its most notable sound; the UI words this as
        # "some sounds ..." so one undetected sound never reads as a lost word.
        word_categories = {s["category"] for s in sounds}
        word_status = next(c for c in WORD_PRIORITY if c in word_categories)

        if word.timing.start_ms is not None:
            span = [word.timing.start_ms, word.timing.end_ms]
            estimated = False
        else:  # no sound of the word was decoded: use the estimated sound regions
            span = [min(s["span_ms"][0] for s in sounds), max(s["span_ms"][1] for s in sounds)]
            estimated = True
        play = word_window(span[0], span[1], duration)

        words.append({
            "index": wi,
            "word": word.word,
            "status": word_status,
            "span_ms": span,
            "timing_estimated": estimated,
            "play_ms": list(play),
            "flagged_by_engine": word.engine_evidence.get("flagged_by_provider"),
            "sounds": sounds,
        })

    leading = []
    if result.words and result.words[0].phonemes:
        leading = [x["phone"] for x in result.words[0].phonemes[0].engine_evidence.get("leading_heard_phones", [])]

    return base | {
        "state": "partial" if result.status == "partial" else "ok",
        "warnings": [e.message for e in result.errors],
        "heard_sequence": list(heard),
        "extra_sounds_before_first_word": leading,
        "words": words,
        "summary": summary,
    }
