"""M4 Phoneme Coach — observations: one interpreted record per expected sound.

M4 is an interpretation layer. It consumes the M1 `PronunciationResult` that M3
already produced (no new model, no extra inference) and turns every expected
sound — plus every inserted sound — into a `PhonemeObservation` with:

* what was expected and what the recogniser decoded,
* an observation type that keeps uncertainty explicit,
* an interpretation confidence and the reasons behind it,
* context (word position, neighbours, cluster, stress only when known),
* the acoustic measurements M1 made (null when unavailable, never 0),
* exact playback windows on the analysed-audio timeline,
* references to the engine evidence it came from.

Patterns (`phoneme_patterns`) and practice targets (`practice`) are built only
from these observations and always point back to them by id.

Language: every statement is about what the recogniser / acoustic evidence
suggests. Nothing here claims what the speaker's articulators did.
"""

from __future__ import annotations

import math
from typing import Any

from pronunciation_lab.app.diagnosis import sound_window, word_window
from pronunciation_lab.benchmark.analysis import (
    AMBIGUOUS_MARGIN,
    PLAUSIBLE_POSTERIOR,
    alignment_suspect_words,
    is_vowel,
)
from pronunciation_lab.benchmark.schema import PronunciationResult

COACH_VERSION = "m4.1"

# ----------------------------------------------------------------------
# Thresholds — every one is stated here, reported in the output, and tested.
# ----------------------------------------------------------------------

# Expected-phone posterior at or above which the expected sound is "still
# plausible" even though another phone won the decode (M2 / OpenPronounce value).
PLAUSIBLE = PLAUSIBLE_POSTERIOR  # 0.05
# Top-1 minus top-2 N-best posterior below which a decode is ambiguous (M2 value).
CLOSE_MARGIN = AMBIGUOUS_MARGIN  # 0.2
# A substitution candidate is high-confidence only when the competing phone's
# posterior exceeds the expected phone's by at least this much.
HIGH_DOMINANCE = 0.5
# An "as expected" observation is high-confidence when the expected phone's own
# posterior reaches this.
HIGH_EXPECTED = 0.5

THRESHOLDS = {
    "plausible_expected_posterior": PLAUSIBLE,
    "close_runner_up_margin": CLOSE_MARGIN,
    "high_confidence_dominance": HIGH_DOMINANCE,
    "high_confidence_expected_posterior": HIGH_EXPECTED,
}

OBSERVATION_TYPES = (
    "expected",                # decoded as expected, runner-up not close
    "substitution_candidate",  # another phone clearly won and the expected one is implausible
    "omission_candidate",      # nothing decoded here and the expected phone is implausible
    "insertion",               # a decoded phone with no expected phone of its own
    "ambiguous",               # top candidates close, or expected still plausible
    "weak_evidence",           # not decoded, but the expected phone remains plausible
    "not_interpreted",         # alignment suspect or evidence missing/invalid
)
CONFIDENCE_LEVELS = ("high", "moderate", "low", "none")

# Pairs where the expected (eSpeak en-us) phone and the decoded phone differ in a
# way the reference inventory itself can explain: length marks, reduced vowels,
# rhotic notation, US vowel mergers. Unordered.
REFERENCE_VARIANT_PAIRS = frozenset(frozenset(p) for p in (
    ("i", "iː"), ("u", "uː"), ("ᵻ", "ɪ"), ("ɐ", "ə"), ("ɐ", "æ"), ("ɐ", "ʌ"), ("ə", "ʌ"),
    ("ɜː", "ɚ"), ("ɜ", "ɚ"), ("ɔ", "ʌ"), ("ɔ", "ɑ"), ("ɔ", "ɑː"), ("ɑ", "ɑː"), ("ʊɹ", "uː"),
    ("ʊɹ", "ʊ"), ("ɔːɹ", "oːɹ"), ("əl", "l"), ("ɾ", "t"), ("ɾ", "d"),
    ("ɚ", "ə"), ("ɔ", "ɔː"),  # rhoticity (en-us is rhotic); vowel length
))
# An estimated (not decoded) region can be long — e.g. a final sound before trailing
# silence. Its playback starts a little before the estimated location and is capped.
ESTIMATED_PLAY_LEAD_MS = 150.0
ESTIMATED_PLAY_MAX_MS = 800.0

REFERENCE_NOTE = (
    "This may reflect the reference accent / phoneme inventory (eSpeak en-us) rather than "
    "a pronunciation difference."
)


def estimated_play_window(start: float, end: float, duration: float) -> list[float]:
    """Playback for an estimated region: the regular window, capped for long regions."""
    window = list(sound_window(start, end, duration))
    if window[1] - window[0] > ESTIMATED_PLAY_MAX_MS:
        lead = max(0.0, start - ESTIMATED_PLAY_LEAD_MS)
        window = [lead, min(duration, lead + ESTIMATED_PLAY_MAX_MS)]
    return window


def is_reference_variant(a: str | None, b: str | None) -> bool:
    return bool(a and b) and frozenset((a, b)) in REFERENCE_VARIANT_PAIRS


def _valid_probability(p: Any) -> bool:
    return p is None or (isinstance(p, (int, float)) and not isinstance(p, bool)
                         and math.isfinite(p) and 0.0 <= p <= 1.0)


def timing_ok(start, end) -> bool:
    """A usable span: two finite numbers with 0 <= start < end."""
    return (isinstance(start, (int, float)) and isinstance(end, (int, float))
            and not isinstance(start, bool) and not isinstance(end, bool)
            and math.isfinite(start) and math.isfinite(end) and 0 <= start < end)


def _evidence_problems(word: str, expected: str | None, operation: str | None, start, end, ep, nbest) -> list[str]:
    problems = []
    if not word:
        problems.append("missing word")
    if not expected:
        problems.append("missing expected phoneme")
    if operation not in ("match", "substitution", "omission"):
        problems.append(f"unknown alignment operation {operation!r}")
    if start is None or end is None:
        problems.append("missing timing")
    elif not timing_ok(start, end):
        problems.append(f"invalid timing {start!r}–{end!r} ms")
    if not _valid_probability(ep):
        problems.append(f"invalid expected-phone posterior {ep!r}")
    for phone, prob in nbest:
        if not phone or prob is None or not _valid_probability(prob):
            problems.append(f"invalid N-best entry {phone!r}={prob!r}")
    return problems


def classify(operation: str, expected: str, observed: str | None, ep: float | None,
             nbest: list[tuple[str, float]]) -> tuple[str, str, str | None, list[str]]:
    """Return (observation type, interpretation confidence, competitor phone, reasons).

    Pure function of the recogniser evidence for one expected sound, assuming the
    evidence is valid and the alignment is not suspect.
    """
    reasons: list[str] = []
    top = nbest[0] if nbest else None
    second = nbest[1] if len(nbest) > 1 else None
    margin = (top[1] - second[1]) if top and second else None
    plausible = ep is not None and ep >= PLAUSIBLE

    if operation == "match":
        if margin is not None and margin < CLOSE_MARGIN:
            reasons.append(f"runner-up /{second[0]}/ is within {CLOSE_MARGIN} of the decoded /{observed}/")
            return "ambiguous", "low", second[0], reasons
        if ep is None:
            reasons.append("expected-phone posterior unavailable")
            return "expected", "moderate", None, reasons
        return "expected", ("high" if ep >= HIGH_EXPECTED else "moderate"), None, reasons

    if operation == "substitution":
        if ep is None:
            reasons.append("expected-phone posterior unavailable, so dominance cannot be established")
            return "ambiguous", "low", observed, reasons
        if plausible:
            reasons.append(f"expected /{expected}/ still has posterior {ep:.2f} (≥ {PLAUSIBLE})")
            return "ambiguous", "low", observed, reasons
        if margin is not None and margin < CLOSE_MARGIN:
            reasons.append(f"top two candidates are within {CLOSE_MARGIN}")
            return "ambiguous", "low", observed, reasons
        observed_p = top[1] if top and top[0] == observed else None
        if observed_p is not None and observed_p - ep >= HIGH_DOMINANCE:
            return "substitution_candidate", "high", observed, reasons
        reasons.append(f"/{observed}/ won the decode but does not dominate by ≥ {HIGH_DOMINANCE}")
        return "substitution_candidate", "moderate", observed, reasons

    # omission: nothing decoded for this expected sound
    competitor = top[0] if top and top[0] != expected else None
    reasons.append("not detected by the recogniser — this does not prove the sound was absent")
    if plausible:
        reasons.append(f"expected /{expected}/ still has posterior {ep:.2f} (≥ {PLAUSIBLE}) in this region")
        return "weak_evidence", "low", competitor, reasons
    if ep is None:
        reasons.append("expected-phone posterior unavailable")
    return "omission_candidate", "low", competitor, reasons


def _word_position(i: int, n: int) -> str:
    if n == 1:
        return "single"
    return "initial" if i == 0 else "final" if i == n - 1 else "medial"


def build_observations(result: PronunciationResult) -> list[dict[str, Any]]:
    """One observation per expected sound (and per inserted sound), in time order."""
    duration = result.recording.audio.duration_ms
    words = result.words
    suspects = alignment_suspect_words(words)
    stress_known = any(p.expected.stress for w in words for p in w.phonemes)
    engine = {"id": result.engine.name, "model": result.engine.model, "phone_set": result.phone_set}

    observations: list[dict[str, Any]] = []
    index = 0
    for wi, word in enumerate(words):
        phones = [p.expected.phoneme for p in word.phonemes]
        word_span = None
        if word.timing.start_ms is not None and word.timing.end_ms is not None:
            word_span = [word.timing.start_ms, word.timing.end_ms]

        for pi, p in enumerate(word.phonemes):
            ev = p.engine_evidence
            nbest = [(c.phoneme, c.probability) for c in p.observed.nbest]
            start, end = p.timing.start_ms, p.timing.end_ms
            ep = ev.get("expected_phone_posterior")
            operation = ev.get("operation")
            problems = _evidence_problems(word.word, p.expected.phoneme, operation, start, end, ep, nbest)

            if problems:
                obs_type, confidence, competitor, reasons = "not_interpreted", "none", None, ["invalid evidence: " + "; ".join(problems)]
                # Invalid numbers are never passed on as evidence values: they
                # become null here and are kept, as found, in `evidence.invalid`.
                if not _valid_probability(ep):
                    ep = None
                nbest = [(a, b) for a, b in nbest if a and b is not None and _valid_probability(b)]
            elif wi in suspects:
                obs_type, confidence, competitor = "not_interpreted", "none", None
                reasons = ["alignment suspect: the recogniser heard extra sounds around this word, so which "
                           "expected sound this evidence belongs to is unreliable"]
            else:
                obs_type, confidence, competitor, reasons = classify(operation, p.expected.phoneme, p.observed.top, ep, nbest)

            if p.timing.source != "engine" and obs_type not in ("not_interpreted",):
                reasons.append("location estimated from neighbouring sounds")
                if confidence == "high":
                    confidence = "moderate"

            prev_phone = phones[pi - 1] if pi > 0 else (words[wi - 1].phonemes[-1].expected.phoneme
                                                         if wi > 0 and words[wi - 1].phonemes else None)
            next_phone = phones[pi + 1] if pi < len(phones) - 1 else (words[wi + 1].phonemes[0].expected.phoneme
                                                                      if wi + 1 < len(words) and words[wi + 1].phonemes else None)
            expected = p.expected.phoneme
            in_cluster = bool(expected) and not is_vowel(expected) and (
                (pi > 0 and not is_vowel(phones[pi - 1])) or (pi < len(phones) - 1 and not is_vowel(phones[pi + 1]))
            )
            features = p.acoustic.spectral_features or {}
            valid_timing = timing_ok(start, end)
            observed_p = None
            for ph, pr in nbest:
                if ph == p.observed.top:
                    observed_p = pr
                    break
            competitor_p = next((pr for ph, pr in nbest if ph == competitor), None) if competitor else None

            observations.append({
                "id": f"o{index:03d}",
                "kind": "sound",
                "sound_index": index,
                "word": word.word,
                "word_index": wi,
                "expected": expected,
                "observed": p.observed.top,
                "type": obs_type,
                "confidence": confidence,
                "competitor": competitor,
                "expected_posterior": ep,
                "observed_posterior": observed_p if p.observed.top else None,
                "competitor_posterior": competitor_p,
                "nbest": [{"phone": ph, "probability": pr} for ph, pr in nbest],
                "reasons": reasons,
                "reference_note": REFERENCE_NOTE if is_reference_variant(expected, competitor) else None,
                "span_ms": [start, end] if valid_timing else None,
                "timing_source": p.timing.source,
                "play_ms": (None if not valid_timing else
                            estimated_play_window(start, end, duration) if p.timing.source != "engine"
                            else list(sound_window(start, end, duration))),
                "word_play_ms": list(word_window(word_span[0], word_span[1], duration)) if word_span else None,
                "context": {
                    "position_in_word": pi,
                    "word_position": _word_position(pi, len(phones)),
                    "previous_phone": prev_phone,
                    "next_phone": next_phone,
                    "word_boundary_before": pi == 0,
                    "word_boundary_after": pi == len(phones) - 1,
                    "in_consonant_cluster": in_cluster,
                    "sentence_position": "first_word" if wi == 0 else "last_word" if wi == len(words) - 1 else "inside",
                    # Unknown stress is never reported as "unstressed".
                    "stress": p.expected.stress if stress_known else None,
                    "stress_known": stress_known,
                },
                "acoustic": {
                    "relative_energy": p.acoustic.relative_energy,
                    "energy_db": p.acoustic.energy_db,
                    "voiced": p.acoustic.voicing,
                    "f0_hz": p.acoustic.f0_hz,
                    "zero_crossing_rate": features.get("zero_crossing_rate"),
                    "spectral_centroid_hz": features.get("spectral_centroid_hz"),
                },
                "evidence": {
                    "engine": engine,
                    "frame_span": [p.timing.frame_start, p.timing.frame_end],
                    "alignment_operation": operation,
                    "m3_sound_index": index,
                    "invalid": problems or None,
                },
            })

            extras = list(ev.get("extra_heard_phones") or [])
            leading = list(ev.get("leading_heard_phones") or []) if (wi == 0 and pi == 0) else []
            for k, extra in enumerate(leading + extras):
                es, ee = extra.get("start_ms"), extra.get("end_ms")
                ok = timing_ok(es, ee) and wi not in suspects
                observations.append({
                    "id": f"o{index:03d}+{k}",
                    "kind": "insertion",
                    "sound_index": None,
                    "after_sound_index": index,
                    "word": word.word,
                    "word_index": wi,
                    "expected": None,
                    "observed": extra.get("phone"),
                    "type": "insertion" if ok else "not_interpreted",
                    "confidence": "low" if ok else "none",
                    "competitor": None,
                    "expected_posterior": None,
                    "observed_posterior": extra.get("confidence"),
                    "competitor_posterior": None,
                    "nbest": [],
                    "reasons": (["alignment suspect"] if wi in suspects else
                                ["extra sound decoded with no expected sound of its own"
                                 + (" (before the first word)" if k < len(leading) else "")] if ok
                                else ["invalid evidence: insertion timing"]),
                    "reference_note": None,
                    "span_ms": [es, ee] if timing_ok(es, ee) else None,
                    "timing_source": "engine",
                    "play_ms": list(sound_window(es, ee, duration)) if timing_ok(es, ee) else None,
                    "word_play_ms": list(word_window(word_span[0], word_span[1], duration)) if word_span else None,
                    "context": {"after_phone": expected, "word_position": _word_position(pi, len(phones)),
                                "stress": None, "stress_known": stress_known},
                    "acoustic": None,
                    "evidence": {"engine": engine, "frame_span": [extra.get("frame_start"), extra.get("frame_end")],
                                 "alignment_operation": "insertion", "m3_sound_index": None},
                })
            index += 1
    return observations
