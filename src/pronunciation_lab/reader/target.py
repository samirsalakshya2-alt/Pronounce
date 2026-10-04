"""Target confirmation: does this recording belong to its sentence? (M12, revised after the M8 manual test)

Target confirmation answers *"did the reader probably read this sentence?"* — not *"how well were its sounds
pronounced?"*. It is separate from pronunciation feedback (M4/M5), from the sentence boundary (M7) and from
summary eligibility, and never uses free speech recognition: only the analysis's own decoded speech sounds.

tc-1 (M12) used phone accuracy: support = expected sounds decoded as expected (or substituted while still
plausible) / expected sounds, MATCH >= 0.76. That conflated identity with pronunciation: a correct sentence
read slowly in a noisy room, with many sounds decoded as other sounds, scored 0.19–0.33 and was called
MISMATCH or AMBIGUOUS although every word of it was found in order (M8 manual test).

tc-2 is contrastive — identity is a comparison between candidate sentences, so pronunciation errors that
degrade the fit to every candidate alike do not flip it:

* each candidate's expected (eSpeak) sounds are aligned to the decoded sounds with a pronunciation-tolerant
  cost (same-class substitution 0.6, cross-class 1.0, a missing sound 1.0, an extra decoded sound inside the
  sentence 0.4; anything before or after the sentence is free, so lead-in and continued speech cost nothing);
  fit = 1 - cost / expected sounds;
* **order evidence** (margin_null): the target's fit minus the mean fit of the same words in shuffled order
  — is *this* sentence there, in this order? Common sounds and words fit any order equally;
* **contrast** (margin_alt): the target's fit minus the best fit among the article's neighbouring sentences
  — does another sentence of the article fit clearly better (read the wrong sentence)?
* **coverage**: share of the sentence's substantial words (>= 3 expected sounds) with an aligned, same-class
  decoded sound — weak function words dropped in fluent speech do not count against it.

States:
    MATCH          order evidence >= 0.18 and coverage >= 0.8
    MISMATCH       little order evidence (<= 0.10) and another sentence of the article fits clearly better (<= -0.25)
    LIKELY_MATCH   this sentence fits best of the article's sentences (margin >= 0.08), coverage >= 0.9,
                   but the order evidence is weak (noisy or heavily accented decoding)
    AMBIGUOUS      anything else (partial read, too little evidence, no alternatives to compare with)
    NOT_APPLICABLE the analysis produced no evidence

Calibration (both local engines; docs/M8_FLUENCY_DISFLUENCY.md "Target confirmation"): R01–R20 true pairs
20/20 MATCH; continuation after the sentence 20/20 MATCH; wrong-sentence pairs 40/40 MISMATCH; half reads
20/20 AMBIGUOUS; heavy-noise true sentences never MISMATCH; no wrong pair above +0.02 contrast or +0.05 order
evidence. One speaker plus two real manual sessions (evaluated locally, never stored in the repository).

Sentences shorter than MIN_WORDS words / MIN_SOUNDS expected sounds have too little structure for a
contrast; they keep tc-1's phone-support rule, reported as such.
"""

from __future__ import annotations

import functools
import hashlib
import random
import re
import unicodedata
from typing import Any

from pronunciation_lab.benchmark.analysis import PLAUSIBLE_POSTERIOR, alignment_suspect_words, is_vowel
from pronunciation_lab.benchmark.schema import PronunciationResult

TARGET_VERSION = "tc-2"
# tc-1 (phone support), still used for very short sentences and always reported as evidence
MATCH_MIN_SUPPORT = 0.76
MISMATCH_BELOW = 0.25
# tc-2 (contrastive identity)
MIN_WORDS, MIN_SOUNDS = 3, 8
SUBSTANTIAL = 3         # coverage counts words of at least this many expected sounds (a dropped "the" or "to"
                        # is ordinary weak-form speech, not a partial reading)
ORDER_MATCH = 0.18
COVERAGE_MATCH = 0.8      # partial reads are excluded by their order evidence (<= 0.14 on the calibration)
COVERAGE_LIKELY = 0.9     # a half read can fit best of the article's sentences: it must not look confirmed
ORDER_ABSENT = 0.10
CONTRAST_MISMATCH = -0.25
CONTRAST_LIKELY = 0.08
SHUFFLES = 6
NEIGHBOURS = 3          # alternatives: up to this many sentences before and after in the article
SAME_CLASS, OTHER_CLASS, MISSING, EXTRA = 0.6, 1.0, 1.0, 0.4

THRESHOLDS = {"match_min_support": MATCH_MIN_SUPPORT, "mismatch_below": MISMATCH_BELOW,
              "plausible_expected_posterior": PLAUSIBLE_POSTERIOR, "min_words": MIN_WORDS, "min_sounds": MIN_SOUNDS,
              "order_match": ORDER_MATCH, "coverage_match": COVERAGE_MATCH, "coverage_likely": COVERAGE_LIKELY, "order_absent": ORDER_ABSENT,
              "contrast_mismatch": CONTRAST_MISMATCH, "contrast_likely": CONTRAST_LIKELY, "shuffles": SHUFFLES,
              "neighbours": NEIGHBOURS, "substantial_word_sounds": SUBSTANTIAL, "costs": {"same_class": SAME_CLASS, "other_class": OTHER_CLASS,
                                                  "missing": MISSING, "extra": EXTRA}}

STATES = ("MATCH", "LIKELY_MATCH", "AMBIGUOUS", "MISMATCH", "NOT_APPLICABLE")
REASONS = {
    "MATCH": "the sentence's words were found in order",
    "LIKELY_MATCH": "this sentence fits the recording best of the article's sentences, but the evidence is weak",
    "AMBIGUOUS": "the recording could not be confidently matched to this sentence",
    "MISMATCH": "another sentence of the article fits the recording clearly better",
}


def target_evidence(result: PronunciationResult) -> dict[str, Any]:
    """tc-1 phone-support evidence (kept for every recording; the decision for very short sentences)."""
    phones = [p for w in result.words for p in w.phonemes]
    ops = [p.engine_evidence.get("operation") for p in phones]
    eps = [p.engine_evidence.get("expected_phone_posterior") for p in phones]
    plausible_subs = sum(1 for o, e in zip(ops, eps)
                         if o == "substitution" and isinstance(e, (int, float)) and e >= PLAUSIBLE_POSTERIOR)
    inserted = sum(len(p.engine_evidence.get("extra_heard_phones") or []) for p in phones)
    if phones:
        inserted += len(phones[0].engine_evidence.get("leading_heard_phones") or [])
    n = len(phones)
    return {
        "expected_sounds": n,
        "matched": ops.count("match"),
        "plausible_substitutions": plausible_subs,
        "substituted": ops.count("substitution"),
        "not_detected": ops.count("omission"),
        "inserted": inserted,
        "decoded_sounds": len(result.engine_evidence.get("recognition", {}).get("phones") or []),
        "alignment_suspect_words": len(alignment_suspect_words(result.words)),
        "support": (ops.count("match") + plausible_subs) / n if n else None,
    }


# ----------------------------------------------------------------------
# Contrastive identity
# ----------------------------------------------------------------------

# a shared, coarse phone space for both engines' decoded sounds and eSpeak's expected sounds: identity, not
# pronunciation, so length, stress, tone digits and rhotic/diphthong detail are folded away
_FOLD = {"ɚ": "ə", "ɝ": "ɜ", "ᵻ": "ɪ", "ɐ": "ʌ", "r": "ɹ", "g": "ɡ", "ɾ": "t", "əl": "l", "ɑɹ": "ɑ", "ɔɹ": "ɔ",
         "ɛɹ": "ɛ", "ɪɹ": "ɪ", "ʊɹ": "ʊ", "oʊ": "o", "eɪ": "e", "aɪ": "a", "aʊ": "a", "ɔɪ": "ɔ"}


def fold(phone: str) -> str:
    p = "".join(ch for ch in unicodedata.normalize("NFD", phone or "") if unicodedata.category(ch) != "Mn")
    p = re.sub(r"[ː˞0-9.ˌˈ]", "", p)
    return _FOLD.get(p, p)


_WORD_RE = re.compile(r"[A-Za-z0-9\u00C0-\u024F]+(?:['’][A-Za-z\u00C0-\u024F]+)*")


@functools.lru_cache(maxsize=1024)
def sentence_sounds(text: str) -> tuple[tuple[str, ...], ...]:
    """A sentence's expected sounds per word: eSpeak (en-us) grapheme-to-phoneme — the same reference the
    engines use — folded. This is pronunciation lookup of known text, not speech recognition."""
    from phonemizer import phonemize
    from phonemizer.separator import Separator

    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words:
        return ()
    out = phonemize(" ".join(words), language="en-us", backend="espeak", strip=True, with_stress=False,
                    preserve_punctuation=False, separator=Separator(phone=" ", word=" | ", syllable=""))
    groups = [tuple(f for f in (fold(p) for p in g.split()) if f) for g in out.split("|")]
    return tuple(g for g in groups if g)


def _sub(a: str, b: str) -> float:
    return 0.0 if a == b else (SAME_CLASS if is_vowel(a) == is_vowel(b) else OTHER_CLASS)


def fit(groups: tuple[tuple[str, ...], ...], heard: list[str]) -> tuple[float, float]:
    """(fit, word coverage) of one candidate sentence: free lead-in and trailing speech."""
    cand = [p for g in groups for p in g]
    word = [i for i, g in enumerate(groups) for _ in g]
    n, m = len(cand), len(heard)
    if not n:
        return 0.0, 0.0
    inf = float("inf")
    prev = [0.0] * (m + 1)          # free lead-in
    back: list[list[int]] = [[0] * (m + 1)]
    for i in range(1, n + 1):
        cur = [prev[0] + MISSING] + [inf] * m
        bt = [1] + [0] * m
        ci = cand[i - 1]
        for j in range(1, m + 1):
            a = prev[j - 1] + _sub(ci, heard[j - 1])
            b = prev[j] + MISSING
            c = cur[j - 1] + EXTRA
            best = a if a <= b and a <= c else (b if b <= c else c)
            cur[j] = best
            bt[j] = 0 if best == a else (1 if best == b else 2)
        back.append(bt)
        prev = cur
    j = min(range(m + 1), key=lambda k: prev[k])  # free trailing speech
    cost = prev[j]
    covered, i = set(), n
    while i > 0 and j > 0:
        step = back[i][j]
        if step == 0:
            if _sub(cand[i - 1], heard[j - 1]) <= SAME_CLASS:
                covered.add(word[i - 1])
            i, j = i - 1, j - 1
        elif step == 1:
            i -= 1
        else:
            j -= 1
    big = [i for i, g in enumerate(groups) if len(g) >= SUBSTANTIAL] or list(range(len(groups)))
    return 1.0 - cost / n, sum(1 for i in big if i in covered) / len(big)


def _shuffles(groups, text: str):
    rnd = random.Random(int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16))  # deterministic
    out = []
    for _ in range(SHUFFLES):
        g = list(groups)
        rnd.shuffle(g)
        out.append(tuple(g))
    return out


def identity_evidence(result: PronunciationResult, alternatives: list[str] | None = None) -> dict[str, Any] | None:
    """Contrastive identity of the recording with its sentence, or None when the sentence is too short."""
    text = result.recording.target.text or ""
    # the target's own expected sounds (eSpeak, as the engine used them); alternatives are phonemized alike
    groups = tuple(t for t in (tuple(f for f in (fold(p.expected.phoneme) for p in w.phonemes) if f)
                               for w in result.words) if t)
    if len(groups) < MIN_WORDS or sum(len(g) for g in groups) < MIN_SOUNDS:
        return None
    heard = [f for f in (fold(p) for p in result.engine_evidence.get("recognition", {}).get("phones") or []) if f]
    s_t, coverage = fit(groups, heard)
    s_null = sum(fit(g, heard)[0] for g in _shuffles(groups, text)) / SHUFFLES
    alts = []
    for a in alternatives or []:
        if a and a.strip() and a != text:
            ag = sentence_sounds(a)
            if ag:
                alts.append({"text": a, "fit": round(fit(ag, heard)[0], 4)})
    best = max(alts, key=lambda x: x["fit"]) if alts else None
    return {"method": "contrastive", "fit": round(s_t, 4), "coverage": round(coverage, 4),
            "order_margin": round(s_t - s_null, 4), "shuffled_fit": round(s_null, 4),
            "contrast_margin": round(s_t - best["fit"], 4) if best else None,
            "closest_alternative": best, "alternatives_compared": len(alts), "decoded_sounds_used": len(heard)}


def neighbours(texts: list[str], index: int) -> list[str]:
    """The article's sentences around `index` (the realistic confusions while reading)."""
    lo, hi = max(0, index - NEIGHBOURS), min(len(texts), index + NEIGHBOURS + 1)
    return [t for k, t in enumerate(texts[lo:hi], lo) if k != index]


def confirm_target(result: PronunciationResult, alternatives: list[str] | None = None) -> dict[str, Any]:
    base = {"version": TARGET_VERSION, "engine": result.engine.name, "thresholds": THRESHOLDS}
    if result.status in ("failed", "blocked") or not result.words:
        return base | {"state": "NOT_APPLICABLE", "reason": "the analysis produced no evidence", "evidence": None,
                       "identity": None}
    ev = target_evidence(result)
    if ev["expected_sounds"] == 0:
        return base | {"state": "NOT_APPLICABLE", "reason": "the sentence has no expected sounds", "evidence": ev,
                       "identity": None}
    if ev["decoded_sounds"] == 0:
        return base | {"state": "AMBIGUOUS", "reason": "no speech sounds were decoded", "evidence": ev, "identity": None}
    idn = identity_evidence(result, alternatives)
    if idn is None:  # very short sentence: tc-1 phone support
        s = ev["support"]
        state = "MATCH" if s >= MATCH_MIN_SUPPORT else ("MISMATCH" if s < MISMATCH_BELOW else "AMBIGUOUS")
        reason = {"MATCH": "most expected sounds were found in the recording",
                  "MISMATCH": "few expected sounds were found; the recording may be of another sentence",
                  "AMBIGUOUS": "only part of the sentence was found"}[state]
        return base | {"state": state, "reason": reason + " (short sentence: phone support)", "evidence": ev,
                       "identity": None}
    if idn["order_margin"] >= ORDER_MATCH and idn["coverage"] >= COVERAGE_MATCH:
        state = "MATCH"
    elif idn["contrast_margin"] is not None and idn["order_margin"] <= ORDER_ABSENT \
            and idn["contrast_margin"] <= CONTRAST_MISMATCH:
        state = "MISMATCH"
    elif idn["contrast_margin"] is not None and idn["contrast_margin"] >= CONTRAST_LIKELY \
            and idn["coverage"] >= COVERAGE_LIKELY:
        state = "LIKELY_MATCH"
    else:
        state = "AMBIGUOUS"
    return base | {"state": state, "reason": REASONS[state], "evidence": ev, "identity": idn}


def combine_engines(primary: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """The other local engine's identity, kept beside the primary's. A MISMATCH that the other engine does not
    share becomes AMBIGUOUS (disagreement); agreement is never treated as independent confirmation."""
    out = dict(primary)
    out["other_engine"] = {"engine": other.get("engine"), "state": other.get("state"), "identity": other.get("identity"),
                           "note": "Both local listening models share one acoustic model; agreement is not "
                                   "independent confirmation."}
    if primary.get("state") == "MISMATCH" and other.get("state") in ("MATCH", "LIKELY_MATCH", "AMBIGUOUS"):
        out["state"] = "AMBIGUOUS"
        out["reason"] = "the listening models disagree about whether this is the sentence"
        out["primary_state"] = "MISMATCH"
    return out
