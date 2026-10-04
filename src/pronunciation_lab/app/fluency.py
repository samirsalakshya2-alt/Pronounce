"""M8 — Fluency & disfluency: what in the timing of one recording may affect spoken fluency.

A separate analysis layer (`view["fluency"]`) beside M4 (`coach`), M5 (`reduction`) and M7
(`boundary`); it reads their inputs but never changes them. No new model, no new inference:

    decoded sounds + their alignment to the sentence (the engine's own result)
    + speech activity of the analysed audio (M7's estimator, with its reliability check)
    + the sentence text (punctuation) and its expected pronunciation (eSpeak syllables)
        → observations: Observed → Evidence → Interpretation → Strength

Every observation is a *candidate* described with cautious language. Strength is the strength of
the evidence for the interpretation — moderate, low, ambiguous or insufficient — never "high", and
never a score: nothing here is a fluency score, a percentage or a ranking.

The engines are phone recognisers, not word recognisers. Fillers, repetitions, restarts and false
starts are therefore only visible as decoded sounds the alignment could not place on the sentence
("extra sounds"), together with their timing, their neighbours and the audio around them. Both local
engines share one acoustic model: agreement between them is supporting, not independent, evidence.

M7 decides which audio is "the sentence": this module analyses whatever audio it is given (the whole
attempt, or the sentence region M7 cut out) and never looks past its end.
"""

from __future__ import annotations

import math
import re
import statistics
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

import numpy as np

from pronunciation_lab.benchmark.analysis import PAUSE_MS, is_vowel
from pronunciation_lab.benchmark.schema import PronunciationResult

FLUENCY_VERSION = "m8.2"
TIMELINE = "analysis_wav"

SHARED_MODEL_NOTE = ("Both local listening models share one acoustic model: when both decode the same "
                     "sequence, that supports an observation but is not independent confirmation.")


class ObservationType(str, Enum):
    PAUSE = "PAUSE"
    FILLER = "FILLER"
    REPETITION = "REPETITION"
    FALSE_START = "FALSE_START"
    RESTART = "RESTART"
    RATE_ANOMALY = "RATE_ANOMALY"
    OTHER_HESITATION = "OTHER_HESITATION"


class Strength(str, Enum):
    """Strength of the evidence for an interpretation. There is deliberately no 'high'."""
    MODERATE = "moderate"
    LOW = "low"
    AMBIGUOUS = "ambiguous"
    INSUFFICIENT = "insufficient"


class PauseClass(str, Enum):
    NATURAL_BOUNDARY = "natural_boundary"          # at punctuation: expected
    BRIEF_WITHIN_PHRASE = "brief_within_phrase"    # short relative to this attempt's pace: not interpreted
    POSSIBLE_HESITATION = "possible_hesitation"    # inside a phrase or a word, long relative to the pace
    UNUSUALLY_LONG = "unusually_long"              # long relative to this attempt's pace, anywhere
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


TYPES = tuple(t.value for t in ObservationType)
STRENGTHS = tuple(s.value for s in Strength)
PAUSE_CLASSES = tuple(c.value for c in PauseClass)

# --- thresholds (documented in docs/M8_FLUENCY_DISFLUENCY.md) ---------------------------------
# A gap of at least the M2 pause length between consecutive decoded sounds is a pause *candidate*;
# it is never a classification by itself.
PAUSE_CANDIDATE_MS = PAUSE_MS
# With reliable speech activity, a candidate is a silent pause only if it contains this much silence.
MIN_SILENT_MS = 150.0
# Relative duration = silence / this attempt's median word onset-to-onset interval *with the pause time
# inside each step removed* (its own articulation pace). Pauses must not be part of their own reference:
# otherwise a reading with many pauses inflates it and its pauses are judged short (found on a real reading:
# 1.3 s pauses inside phrases against a 1.38 s "typical interval"). A slow reader has a long reference, so
# slow speech alone is never a hesitation.
# Calibrated on R01–R20 (fluent read sentences): against this reference 1× flags most of their ordinary
# within-phrase pauses (160–690 ms); 2× flags few and still catches inserted 1 s pauses.
HESITATION_REL = 2.0        # inside a phrase: at least two words' worth of articulation time in silence
MODERATE_HESITATION_REL = 3.0
UNUSUALLY_LONG_REL = 4.0
# A stretch of sound with nothing decoded, between words, this long: possible prolongation / hum.
UNDECODED_SOUND_MS = 400.0
# Rates are given only when the sentence was recognisably decoded and long enough to measure.
RATE_MIN_SUPPORT = 0.5
RATE_MIN_SYLLABLES = 4
PAUSE_HEAVY_RATIO = 0.3
# Pace inside the sentence: a stretch between pauses this much faster/slower than the whole.
STRETCH_MIN_VOWELS, STRETCH_MIN_MS = 5, 600.0
FAST_STRETCH, SLOW_STRETCH = 1.5, 0.6
# Extra-sound runs: decoded sounds the alignment placed on no expected sound, at most this far apart.
RUN_JOIN_MS = 200.0
# Filler shape: a central/open vowel (um, uh, er, erm), optionally followed by a nasal; 1–3 sounds.
# High vowels (ɪ, ᵻ) are not hesitation vowels: a lone /ɪ/ is far more often part of "is", "it", "in".
FILLER_VOWELS = frozenset({"ə", "ʌ", "ɜ", "ɜː", "ɐ", "ɚ", "ɝ", "ɛ", "æ", "a", "ɑ", "ɑː", "ɔ", "ɔː"})
FILLER_CODAS = frozenset({"m", "n"})
FILLER_MIN_SUSTAINED_MS = 200.0
# Copies: two phone sequences are "the same" at this similarity (1 - edit distance / length).
COPY_SIMILARITY = 0.75
MODERATE_COPY_SIMILARITY = 0.85
SHORT_COPY = 3              # copies of at most this many sounds must be identical
ONSET_OVERLAP = 2           # false start: begins like what follows for at least this many sounds …
DIVERGENCE = 2              # … then differs for at least this many
ADJACENT_MS = 1500.0

THRESHOLDS = {
    "pause_candidate_ms": PAUSE_CANDIDATE_MS, "min_silent_ms": MIN_SILENT_MS, "hesitation_rel": HESITATION_REL,
    "moderate_hesitation_rel": MODERATE_HESITATION_REL, "unusually_long_rel": UNUSUALLY_LONG_REL,
    "undecoded_sound_ms": UNDECODED_SOUND_MS, "rate_min_support": RATE_MIN_SUPPORT,
    "rate_min_syllables": RATE_MIN_SYLLABLES, "pause_heavy_ratio": PAUSE_HEAVY_RATIO,
    "stretch_min_vowels": STRETCH_MIN_VOWELS, "stretch_min_ms": STRETCH_MIN_MS, "fast_stretch": FAST_STRETCH,
    "slow_stretch": SLOW_STRETCH, "run_join_ms": RUN_JOIN_MS, "filler_min_sustained_ms": FILLER_MIN_SUSTAINED_MS,
    "copy_similarity": COPY_SIMILARITY, "moderate_copy_similarity": MODERATE_COPY_SIMILARITY,
    "onset_overlap": ONSET_OVERLAP, "divergence": DIVERGENCE, "adjacent_ms": ADJACENT_MS, "short_copy": SHORT_COPY,
}

LABELS = {
    ("PAUSE", "natural_boundary"): "Pause at a phrase boundary",
    ("PAUSE", "brief_within_phrase"): "Pause within a phrase",
    ("PAUSE", "possible_hesitation"): "Possible hesitation",
    ("PAUSE", "unusually_long"): "Long pause",
    ("PAUSE", "insufficient_evidence"): "Pause",
    "FILLER": "Possible filler",
    "REPETITION": "Possible repetition",
    "FALSE_START": "Possible false start",
    "RESTART": "Possible restart",
    "RATE_ANOMALY": "Change of pace",
    "OTHER_HESITATION": "Possible hesitation",
}


# ----------------------------------------------------------------------
# Schema
# ----------------------------------------------------------------------

@dataclass
class FluencyEvidence:
    kind: str          # e.g. "position", "punctuation", "silence", "relative_duration", "decoded_sounds", ...
    detail: str        # plain sentence
    value: Any = None


@dataclass
class FluencyObservation:
    id: str
    type: str
    label: str
    start_ms: float
    end_ms: float
    observed: str
    evidence: list[FluencyEvidence]
    interpretation: str
    strength: str
    context: dict[str, Any]
    playback: dict[str, Any]
    engine: str | None
    notice: bool                    # "a fluency thing to notice" (shown in the compact line)
    classification: str | None = None

    @property
    def duration_ms(self) -> float:
        return self.end_ms - self.start_ms

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration_ms"] = self.duration_ms
        return d


@dataclass
class FluencyMetrics:
    syllable_count: int | None
    speaking_interval_ms: float | None
    speech_active_ms: float | None
    pause_count: int
    pause_total_ms: float
    mean_pause_ms: float | None
    median_pause_ms: float | None
    pause_ratio: float | None
    speaking_rate: float | None        # syllables per second over the speaking interval
    articulation_rate: float | None    # syllables per second over the speaking interval minus pauses
    rate_available: bool
    rate_unavailable_reason: str | None
    leading_silence_ms: float | None
    trailing_silence_ms: float | None
    word_interval_ms: float | None     # median onset-to-onset interval between consecutive words
    activity_reliable: bool | None
    activity_coverage: float | None
    definitions: dict[str, str] = field(default_factory=dict)
    # articulation rate and pause ratio need pauses verified as silence (reliable level estimate)
    articulation_available: bool = False
    articulation_unavailable_reason: str | None = None
    word_interval_basis: str | None = None


@dataclass
class FluencySummary:
    pace: str          # steady | uneven | pause-heavy | insufficient evidence
    text: str
    notice: int
    by_type: dict[str, int]


DEFINITIONS = {
    "syllable_count": "vowel nuclei in the expected (eSpeak) pronunciation of the words from the first to the last "
                      "word with a decoded sound — never decoded sounds",
    "speaking_interval_ms": "from the first to the last decoded sound of the sentence (leading and trailing "
                            "silence excluded, pauses and extra sounds inside included)",
    "speaking_rate": "syllable_count / speaking interval (syllables per second)",
    "articulation_rate": "syllable_count / (speaking interval - silent pauses) (syllables per second; only when "
                         "pauses are verified as silence)",
    "pause_ratio": "silent pause time / speaking interval (only when pauses are verified as silence)",
    "speech_active_ms": "speech-like frames inside the speaking interval (only when the level estimate is reliable)",
    "pause": f"a gap of at least {PAUSE_CANDIDATE_MS:.0f} ms between decoded sounds (candidate) containing at least "
             f"{MIN_SILENT_MS:.0f} ms of silence when the level estimate is reliable; classified by its context",
}


# ----------------------------------------------------------------------
# Evidence extraction
# ----------------------------------------------------------------------

DECODED_OPS = ("match", "substitution")
STOPS = frozenset({"p", "t", "k", "b", "d", "ɡ", "g", "tʃ", "dʒ", "ʔ"})
_WORD_RE = re.compile(r"[A-Za-z0-9À-ɏ]+(?:['’][A-Za-zÀ-ɏ]+)*")
SENTENCE_PUNCT = set(".!?…")
PHRASE_PUNCT = set(",;:—–()\"“”")


@dataclass
class Sound:
    phone: str
    start: float
    end: float
    kind: str        # "target" (placed on an expected sound) | "extra" (placed on none)
    word: int        # target: its word; extra: the word it follows (-1: before the first word)
    pos: int         # target: index in its word; extra: index of the expected sound it follows (-1: leading)
    between: bool    # extra only: lies between words (not inside a word)
    confidence: float | None = None


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower().replace("’", "'"))


def word_boundaries(result: PronunciationResult, text: str) -> dict[int, str]:
    """Punctuation after each word of the result: 'sentence', 'phrase' or absent (look-ahead 3 like alignWords)."""
    out: dict[int, str] = {}
    words = [_norm(w.word) for w in result.words]
    wi = 0
    for m in _WORD_RE.finditer(text):
        nxt = _WORD_RE.search(text, m.end())
        between = text[m.end():nxt.start()] if nxt else text[m.end():]
        found = next((k for k in range(wi, min(len(words), wi + 3)) if words[k] == _norm(m.group(0))), None)
        if found is None:
            continue
        wi = found + 1
        if any(c in SENTENCE_PUNCT for c in between):
            out[found] = "sentence"
        elif any(c in PHRASE_PUNCT for c in between) or re.search(r"\s[-–—]+\s|--", between):
            out[found] = "phrase"
    return out


def decoded_sounds(result: PronunciationResult) -> list[Sound]:
    out: list[Sound] = []
    for wi, w in enumerate(result.words):
        decoded_positions = [pi for pi, p in enumerate(w.phonemes) if p.engine_evidence.get("operation") in DECODED_OPS]
        last_decoded = max(decoded_positions, default=-1)
        for pi, p in enumerate(w.phonemes):
            ev = p.engine_evidence
            if wi == 0 and pi == 0:
                for x in ev.get("leading_heard_phones") or []:
                    if _ok(x.get("start_ms"), x.get("end_ms")):
                        out.append(Sound(x["phone"], float(x["start_ms"]), float(x["end_ms"]), "extra", -1, -1, True,
                                         x.get("confidence")))
            if ev.get("operation") in DECODED_OPS and p.timing.source == "engine" and _ok(p.timing.start_ms, p.timing.end_ms):
                out.append(Sound(p.observed.top or p.expected.phoneme, float(p.timing.start_ms), float(p.timing.end_ms),
                                 "target", wi, pi, False, p.observed.confidence))
            for x in ev.get("extra_heard_phones") or []:
                if _ok(x.get("start_ms"), x.get("end_ms")):
                    out.append(Sound(x["phone"], float(x["start_ms"]), float(x["end_ms"]), "extra", wi, pi,
                                     pi >= last_decoded, x.get("confidence")))
    out.sort(key=lambda s: (s.start, s.end))
    return out


def _ok(a, b) -> bool:
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isfinite(a) and math.isfinite(b) \
        and 0 <= a <= b


def _slot(s: Sound) -> tuple[int, str]:
    """Where a sound sits relative to the words: (word, 'in') or (word, 'after')."""
    if s.kind == "target":
        return s.word, "in"
    return (s.word, "after") if s.between else (s.word, "in")


def edit_similarity(a: list[str], b: list[str]) -> float:
    """1 - unit-cost edit distance / longer length (a same-class substitution costs 0.5)."""
    if not a and not b:
        return 1.0
    n, m = len(a), len(b)
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, m + 1):
            sub = 0.0 if a[i - 1] == b[j - 1] else (0.5 if _same_class(a[i - 1], b[j - 1]) else 1.0)
            prev, dp[j] = dp[j], min(dp[j] + 1, dp[j - 1] + 1, prev + sub)
    return 1.0 - dp[m] / max(n, m)


def _same_class(a: str, b: str) -> bool:
    return is_vowel(a) == is_vowel(b)


# ----------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------

class _Ctx:
    """Everything the detectors share for one analysed recording."""

    def __init__(self, result: PronunciationResult, samples, sample_rate, duration_ms: float):
        from pronunciation_lab.reader.boundary import MIN_ACTIVITY_COVERAGE, activity_coverage, speech_activity

        self.result, self.duration = result, duration_ms
        self.engine = result.engine.name
        self.sounds = decoded_sounds(result)
        self.index = {id(x): k for k, x in enumerate(self.sounds)}
        self.target = [s for s in self.sounds if s.kind == "target"]
        self.boundaries = word_boundaries(result, result.recording.target.text or "")
        self.samples, self.sr = samples, sample_rate
        self.act = speech_activity(samples, sample_rate) if samples is not None and sample_rate else None
        if self.act is not None and len(self.act["active"]) and self.target:
            self.coverage = activity_coverage(self.act, [(s.start, s.end) for s in self.target])
            self.reliable = self.coverage >= MIN_ACTIVITY_COVERAGE
        else:
            self.coverage, self.reliable = None, False
        onsets = {}
        for s in self.target:
            onsets.setdefault(s.word, s.start)
        ws = sorted(onsets)
        pairs = [(onsets[a], onsets[b]) for a, b in zip(ws, ws[1:]) if b == a + 1]
        gaps = [(x.end, y.start) for x, y in zip(self.sounds, self.sounds[1:]) if y.start - x.end >= PAUSE_CANDIDATE_MS]
        # each step as it would be without its pause: the pause is replaced by this recording's typical gap
        # between decoded sounds (decoders time sounds as short peaks, so a pause gap also holds the end of the
        # sound before it; removing the gap outright would make the reference too short)
        small = [y.start - x.end for x, y in zip(self.sounds, self.sounds[1:]) if 0 <= y.start - x.end < PAUSE_CANDIDATE_MS]
        typical = statistics.median(small) if small else 0.0
        steps = []
        for t0, t1 in pairs:
            inside = [g1 - g0 for g0, g1 in gaps if t0 <= g0 and g1 <= t1]
            steps.append(t1 - t0 - sum(inside) + typical * len(inside))
        steps = [x for x in steps if x > 0]
        self.word_interval = statistics.median(steps) if steps else None
        self.interval_basis = "pauses removed" if steps else None

    # -- speech activity ----------------------------------------------------------------------
    def silent_run(self, a: float, b: float) -> tuple[float, float, float] | None:
        """The gap [a, b] trimmed of speech-like frames at both ends (a breath or click inside stays part of
        the pause), and the total silence inside it (ms). None if the estimate is not reliable."""
        if not self.reliable:
            return None
        hop, active = self.act["hop_ms"], self.act["active"]
        i0, i1 = max(0, int(math.ceil(a / hop))), min(len(active), int(b / hop))
        quiet = [i for i in range(i0, i1) if not active[i]]
        if not quiet:
            return (a, a, 0.0)
        return max(a, quiet[0] * hop), min(b, (quiet[-1] + 1) * hop), len(quiet) * hop

    def active_ms(self, a: float, b: float) -> float | None:
        if not self.reliable:
            return None
        hop, active = self.act["hop_ms"], self.act["active"]
        i0, i1 = max(0, int(math.ceil(a / hop))), min(len(active), int(b / hop))
        return float(active[i0:i1].sum()) * hop if i1 > i0 else 0.0

    def stretch(self, a: float, b: float) -> tuple[float, float] | None:
        """The contiguous speech-like stretch containing the middle of [a, b], in ms (reliable estimate only)."""
        if not self.reliable:
            return None
        hop, active = self.act["hop_ms"], self.act["active"]
        c = min(len(active) - 1, max(0, int(((a + b) / 2) / hop)))
        if not active[c]:
            return None
        lo = hi = c
        while lo > 0 and active[lo - 1]:
            lo -= 1
        while hi + 1 < len(active) and active[hi + 1]:
            hi += 1
        return lo * hop, (hi + 1) * hop

    def sustained_ms(self, a: float, b: float) -> float | None:
        """Length of the contiguous speech-like stretch containing [a, b] (reliable estimate only)."""
        if not self.reliable:
            return None
        hop, active = self.act["hop_ms"], self.act["active"]
        c = min(len(active) - 1, max(0, int(((a + b) / 2) / hop)))
        if not active[c]:
            return 0.0
        lo = hi = c
        while lo > 0 and active[lo - 1]:
            lo -= 1
        while hi + 1 < len(active) and active[hi + 1]:
            hi += 1
        return (hi - lo + 1) * hop

    def play(self, a: float, b: float, pad_before: float = 250.0, pad_after: float = 250.0) -> dict[str, Any]:
        """Exact span, and the window Listen plays: the span plus stated context (clamped to the audio)."""
        lo, hi = max(0.0, a - pad_before), max(b, min(self.duration, b + pad_after))
        return {"timeline": TIMELINE, "span_ms": [a, b], "play_ms": [lo, hi], "context_ms": [a - lo, hi - b]}

    def word(self, i: int) -> str | None:
        return self.result.words[i].word if 0 <= i < len(self.result.words) else None

    def expected(self, first: int, last: int) -> list[str]:
        return [p.expected.phoneme for w in self.result.words[max(0, first):last + 1] for p in w.phonemes]


def _pauses(c: _Ctx) -> tuple[list[FluencyObservation], list[tuple[float, float]], list[dict[str, Any]]]:
    """Silent pauses between decoded sounds, classified by context; also undecoded sound stretches."""
    obs, silent, other = [], [], []
    for a, b in zip(c.sounds, c.sounds[1:]):
        gap = b.start - a.end
        if gap < PAUSE_CANDIDATE_MS:
            continue
        evidence = [FluencyEvidence("decoded_gap", f"{gap:.0f} ms between decoded sounds [{a.phone}] and [{b.phone}]", gap)]
        run = c.silent_run(a.end, b.start)
        if c.reliable:
            silent_ms = run[2]
            if silent_ms < MIN_SILENT_MS:
                # sound continues through the gap but nothing was decoded: not a silent pause
                left, right = _slot(a), _slot(b)
                if gap >= UNDECODED_SOUND_MS and left != right:
                    other.append({"start": a.end, "end": b.start, "gap": gap, "a": a, "b": b})
                continue
            start, end = run[0], run[1]
            evidence.append(FluencyEvidence("silence", f"{silent_ms:.0f} ms without speech-like sound "
                                                       "(level estimate reliable in this recording)", silent_ms))
        else:
            start, end = a.end, b.start
            evidence.append(FluencyEvidence("silence", "speech level not measurable reliably in this recording; "
                                                       "the pause is the gap between decoded sounds", None))
        dur = end - start
        silent.append((start, end))
        lw, (rw, rkind) = a.word, _slot(b)
        right_word = rw if b.kind == "target" or rkind == "in" else rw + 1
        inside = a.word == b.word and (a.kind == "target" or not a.between) and (b.kind == "target" or not b.between) \
            and a.word >= 0
        rel = dur / c.word_interval if c.word_interval else None
        ctx: dict[str, Any] = {"word_before": c.word(lw), "word_after": c.word(right_word if not inside else lw),
                               "word_index_before": lw, "word_index_after": right_word}
        boundary = c.boundaries.get(lw) if not inside and right_word == lw + 1 else None
        if inside:
            position = "inside a word"
        elif right_word == lw + 1:
            position = "at a " + ("sentence" if boundary == "sentence" else "phrase") + " boundary" if boundary \
                else "between words inside a phrase"
        else:
            position = "where words were not decoded"
        ctx["position"] = position
        evidence.append(FluencyEvidence("position", f"{position}" + (f" (after '{c.word(lw)}')" if lw >= 0 else ""),
                                        position))
        if rel is not None:
            evidence.append(FluencyEvidence("relative_duration", f"{rel:.1f}× this recording's typical interval between "
                                                                 f"word onsets ({c.word_interval:.0f} ms, "
                                                                 f"{c.interval_basis})", round(rel, 2)))
        if rel is None or position == "where words were not decoded":
            cls, strength, notice = PauseClass.INSUFFICIENT_EVIDENCE, Strength.INSUFFICIENT, False
            interp = "A pause; its context is not clear enough to interpret it."
        elif rel >= UNUSUALLY_LONG_REL:
            cls = PauseClass.UNUSUALLY_LONG
            within = not boundary
            strength = Strength.MODERATE if within and c.reliable else Strength.LOW
            notice = True
            interp = ("An unusually long pause relative to the pace of this recording"
                      + (" inside a phrase; consistent with hesitation or planning." if within
                         else " at a phrase boundary; it may be planning or breathing."))
        elif boundary:
            cls, strength, notice = PauseClass.NATURAL_BOUNDARY, (Strength.MODERATE if c.reliable else Strength.LOW), False
            interp = "A pause where the text has punctuation: consistent with natural phrasing."
        elif rel >= HESITATION_REL:
            cls, notice = PauseClass.POSSIBLE_HESITATION, True
            strength = Strength.MODERATE if c.reliable and rel >= MODERATE_HESITATION_REL else Strength.LOW
            interp = ("A silence inside a word; consistent with hesitation (or with a word read in parts)." if inside
                      else "A pause inside a phrase, long relative to this recording's pace; consistent with hesitation "
                           "(it may also be planning).")
        else:
            cls, strength, notice = PauseClass.BRIEF_WITHIN_PHRASE, (Strength.LOW if c.reliable else Strength.INSUFFICIENT), False
            closure = inside and b.phone in STOPS
            interp = ("A silence inside a word, short relative to this recording's pace"
                      + (" — before a stop consonant, where a closure is silent anyway" if closure else "")
                      + "; not interpreted as hesitation." if inside else
                      "A pause inside a phrase, short relative to this recording's pace; not interpreted as hesitation.")
        if not c.reliable:
            # only the gap between decoded sounds is known: it may include parts of those sounds. Kept as a
            # candidate; only an unusually long one is counted as a thing to notice.
            notice = notice and cls == PauseClass.UNUSUALLY_LONG
            interp += (" The speech level could not be measured reliably in this recording, so this is the gap "
                       "between decoded sounds and may include parts of them.")
        obs.append(FluencyObservation("", "PAUSE", LABELS[("PAUSE", cls.value)], start, end,
                                      f"{dur / 1000:.2f} s pause", evidence, interp, strength.value, ctx,
                                      c.play(start, end), c.engine, notice, cls.value))
    return obs, silent, other


def _runs(c: _Ctx) -> list[list[Sound]]:
    """Consecutive extra sounds (placed on no expected sound), joined when close together."""
    runs: list[list[Sound]] = []
    prev: Sound | None = None
    for s in c.sounds:
        if s.kind == "extra":
            if runs and runs[-1][-1] is prev and s.start - prev.end <= RUN_JOIN_MS:
                runs[-1].append(s)
            else:
                runs.append([s])
        prev = s
    return runs


def _interrupt_after(c: _Ctx, end: float, silent: list[tuple[float, float]]) -> tuple[bool, str | None]:
    for s, e in silent:
        if 0 <= s - end <= 300:
            return True, f"a {e - s:.0f} ms pause follows"
    return False, None


def _interrupt_before(c: _Ctx, start: float, silent: list[tuple[float, float]]) -> tuple[bool, str | None]:
    for s, e in silent:
        if 0 <= start - e <= 300:
            return True, f"a {e - s:.0f} ms pause precedes"
    return False, None


def _fillers(c: _Ctx, runs, silent, taken: set[int]) -> tuple[list[FluencyObservation], set[int]]:
    """Filler candidates among the extra-sound runs not already explained by a repetition or restart (one
    decoded sound is never counted twice). The event spans the speech-like sound around the decoded sounds,
    between the neighbouring decoded sounds — a decoder reports a sound as a short spike, not its extent."""
    obs, used = [], set()
    for k, run in enumerate(runs):
        i0, i1 = c.index[id(run[0])], c.index[id(run[-1])]
        if any(j in taken for j in range(i0, i1 + 1)):
            continue  # part of a repetition or restart
        phones = [s.phone for s in run]
        shape = 1 <= len(phones) <= 3 and phones[0] in FILLER_VOWELS and \
            all(p in FILLER_VOWELS for p in phones[:-1]) and (phones[-1] in FILLER_VOWELS or phones[-1] in FILLER_CODAS)
        if not shape or not run[0].between:
            continue  # inside a word, or not filler-shaped: not a filler candidate
        used.add(k)
        decoded = (run[0].start, run[-1].end)
        start, end = decoded
        lo_b = c.sounds[i0 - 1].end if i0 > 0 else 0.0
        hi_b = c.sounds[i1 + 1].start if i1 + 1 < len(c.sounds) else c.duration
        st = c.stretch(*decoded)
        if st is not None:
            start, end = min(start, max(st[0], lo_b)), max(end, min(st[1], hi_b))
        lw = run[0].word
        ctx = {"word_before": c.word(lw), "word_after": c.word(lw + 1), "word_index_before": lw,
               "position": "before the first word" if lw < 0 else ("after the last word" if lw + 1 >= len(c.result.words)
                                                                     else "between words")}
        evidence = [FluencyEvidence("decoded_sounds", f"extra decoded sounds /{' '.join(phones)}/ that match no expected "
                                                      "sound of the sentence", phones),
                    FluencyEvidence("position", f"{ctx['position']}" + (f" (after '{c.word(lw)}')" if lw >= 0 else ""),
                                    ctx["position"])]
        sustained = (end - start) if st is not None else (0.0 if c.reliable else None)
        if sustained is not None:
            evidence.append(FluencyEvidence("acoustic", f"decoded at {decoded[0]:.0f}–{decoded[1]:.0f} ms; the speech-like "
                                                        f"sound containing them, between the neighbouring decoded sounds, "
                                                        f"lasts {sustained:.0f} ms", sustained))
        before, why_b = _interrupt_before(c, start, silent)
        after, why_a = _interrupt_after(c, end, silent)
        if before or after:
            evidence.append(FluencyEvidence("pause", "; ".join(x for x in (why_b, why_a) if x), True))
        # an alternative: a neighbouring expected word lost a vowel the alignment could have used here
        alt = None
        for wi in (lw, lw + 1):
            if 0 <= wi < len(c.result.words):
                lost = [p.expected.phoneme for p in c.result.words[wi].phonemes
                        if p.engine_evidence.get("operation") == "omission" and is_vowel(p.expected.phoneme)]
                if lost:
                    alt = c.word(wi)
        if alt:
            evidence.append(FluencyEvidence("alternative", f"the neighbouring word '{alt}' has an expected vowel that was "
                                                           "not decoded; these sounds may belong to it", alt))
        sustained_ok = sustained is not None and sustained >= FILLER_MIN_SUSTAINED_MS
        if alt:
            strength = Strength.AMBIGUOUS
        elif sustained_ok and (before or after) and c.reliable:
            strength = Strength.MODERATE
        else:
            strength = Strength.LOW
        interp = (f"Acoustically consistent with a filled pause (such as “{_filler_spelling(phones)}”); "
                  "it may also be part of a word decoded out of place.")
        # a lone decoded vowel without acoustic support is common decoder behaviour (e.g. a schwa between
        # consonants): listed, but a thing to notice only with moderate evidence or an "um" shape
        um_shaped = len(phones) >= 2 and phones[-1] in FILLER_CODAS
        notice = strength == Strength.MODERATE or (strength == Strength.LOW and um_shaped)
        obs.append(FluencyObservation("", "FILLER", LABELS["FILLER"], start, end, f"/{' '.join(phones)}/", evidence,
                                      interp, strength.value, ctx, c.play(start, end), c.engine, notice))
    return obs, used


def _filler_spelling(phones: list[str]) -> str:
    return "um" if phones[-1] in FILLER_CODAS else ("er" if phones[0] in ("ɜ", "ɜː", "ɚ", "ɝ") else "uh")


def _words_of(c: _Ctx, seq: list[Sound]) -> list[int]:
    out: list[int] = []
    for s in seq:
        if s.kind == "target" and (not out or out[-1] != s.word):
            out.append(s.word)
    return out


def _between(c: _Ctx, end: float, start: float, silent) -> tuple[bool, str | None]:
    for s, e in silent:
        if s >= end - 1e-6 and e <= start + 1e-6:
            return True, f"a {e - s:.0f} ms pause between them"
    return False, None


def _copies(c: _Ctx, silent) -> tuple[list[FluencyObservation], set[int]]:
    """Repetitions and restarts: two adjacent, near-identical stretches of decoded sounds that need surplus
    (extra) sounds to be explained — so identical words in the text itself never count. Returns the indices
    of the decoded sounds they use, which no other candidate may use again."""
    obs: list[FluencyObservation] = []
    snd, phones = c.sounds, [s.phone for s in c.sounds]
    taken: set[int] = set()
    for L in range(min(12, len(snd) // 2), 1, -1):
        for i in range(0, len(snd) - 2 * L + 1):
            span = range(i, i + 2 * L)
            if any(k in taken for k in span):
                continue
            A, B = snd[i:i + L], snd[i + L:i + 2 * L]
            if B[0].start - A[-1].end > ADJACENT_MS:
                continue
            sim = edit_similarity(phones[i:i + L], phones[i + L:i + 2 * L])
            if sim < COPY_SIMILARITY or (L <= SHORT_COPY and sim < 1.0):
                continue  # short stretches must be identical: two sounds sharing a vowel are not a copy
            extras = sum(1 for k in span if snd[k].kind == "extra")
            # the lexical anchor: the later copy that is mostly the sentence's own sounds (the aligner may have
            # put one sound of the other copy on a neighbouring word)
            anchor = B if sum(1 for x in B if x.kind == "target") >= sum(1 for x in A if x.kind == "target") else A
            words = _words_of(c, anchor)
            if not words:
                continue  # no anchor in the sentence
            surplus = extras >= max(2, math.ceil(0.6 * L))
            # a copy the aligner forced onto another word: its sounds fit the repeated word, not the word
            # they were aligned to (e.g. "you you want" read where a weak "that" was expected)
            other = A if anchor is B else B
            other_words = [w for w in _words_of(c, other) if w not in words]
            displaced = bool(other_words) and \
                edit_similarity([x.phone for x in other], c.expected(other_words[0], other_words[-1])) < 0.5
            if not (surplus or displaced):
                continue  # explained by the sentence itself (e.g. "had had" in the text): not a repetition
            expected = c.expected(words[0], words[-1])
            last_word = words[-1]
            continues = any(s.kind == "target" and s.word == last_word for s in snd[i + 2 * L:i + 2 * L + 4])
            full = edit_similarity(phones[i:i + L], expected) >= COPY_SIMILARITY
            first_word = c.expected(words[0], words[0])
            prefix = not full and continues and \
                edit_similarity(phones[i:i + L], first_word[:L]) >= COPY_SIMILARITY and L < len(first_word)
            if not (full or prefix):
                continue
            taken.update(span)
            kind = "REPETITION" if full else "RESTART"
            interrupted, why = _between(c, A[-1].end, B[0].start, silent)
            start = A[0].start
            end = B[-1].end if kind == "REPETITION" else max(s.end for s in snd[i + L:i + 2 * L + 4]
                                                             if s.kind == "target" and s.word == last_word) \
                if continues else B[-1].end
            word_text = " ".join(c.word(w) for w in words)
            how = (f"{extras} of these sounds are extra (placed on no expected sound)" if surplus else
                   f"the aligner placed one copy on '{' '.join(c.word(w) for w in other_words)}', whose expected "
                   "sounds it does not resemble")
            evidence = [
                FluencyEvidence("decoded_sounds", f"/{' '.join(phones[i:i + L])}/ then /{' '.join(phones[i + L:i + 2 * L])}/; "
                                                  + how, [phones[i:i + L], phones[i + L:i + 2 * L]]),
                FluencyEvidence("similarity", f"the two stretches are {sim:.0%} alike", round(sim, 2)),
                FluencyEvidence("adjacency", f"{max(B[0].start - A[-1].end, 0):.0f} ms apart",
                                max(B[0].start - A[-1].end, 0.0)),
                FluencyEvidence("lexical", (f"they match the expected sounds of '{word_text}'" if full else
                                            f"the first stretch matches the beginning of '{c.word(words[0])}', "
                                            "which then continues"), word_text),
            ]
            if interrupted:
                evidence.append(FluencyEvidence("interruption", why, True))
            short = L <= 2
            if kind == "REPETITION":
                likely = interrupted and sim >= MODERATE_COPY_SIMILARITY
                label = "Likely self-repetition" if likely else LABELS["REPETITION"]
                strength = (Strength.AMBIGUOUS if short and not interrupted else
                            Strength.MODERATE if likely and c.reliable and not short else Strength.LOW)
                interp = ("The same sounds occur twice in a row"
                          + (", with an interruption between them; consistent with a self-repetition." if interrupted
                             else "; this may be a self-repetition or intentional emphasis."))
                observed = f"'{word_text}' heard twice"
            else:
                label = LABELS["RESTART"]
                strength = Strength.MODERATE if interrupted and sim >= MODERATE_COPY_SIMILARITY and c.reliable \
                    and L >= 3 else Strength.LOW
                interp = ("The beginning of a word is heard, then the word again from its start; consistent with a "
                          "restart (the speaker's intention cannot be known from the audio).")
                observed = f"/{' '.join(phones[i:i + L])}/ then '{c.word(words[0])}'"
            ctx = {"words": [c.word(w) for w in words], "word_indices": words, "length": L}
            obs.append(FluencyObservation("", kind, label, start, end, observed, evidence, interp, strength.value, ctx,
                                          c.play(start, end, 150.0, 150.0), c.engine, strength != Strength.AMBIGUOUS))
    return obs, taken


def _false_starts(c: _Ctx, runs, used, taken: set[int], silent) -> list[FluencyObservation]:
    """An extra-sound run that begins like what follows, then differs, and is interrupted."""
    obs: list[FluencyObservation] = []
    snd = c.sounds
    for k, run in enumerate(runs):
        if k in used or len(run) < ONSET_OVERLAP + DIVERGENCE:
            continue
        i0, i1 = c.index[id(run[0])], c.index[id(run[-1])]
        if any(j in taken for j in range(i0, i1 + 1)):
            continue
        r = [x.phone for x in run]
        after = snd[i1 + 1:i1 + 1 + len(r) + 4]
        if not after or after[0].kind != "target" or after[0].start - run[-1].end > ADJACENT_MS:
            continue
        f = [x.phone for x in after]
        onset = 0
        while onset < min(len(r), len(f)) and r[onset] == f[onset]:
            onset += 1
        interrupted, why = _between(c, run[-1].end, after[0].start, silent)
        if onset < ONSET_OVERLAP or len(r) - onset < DIVERGENCE or not interrupted:
            continue
        words = _words_of(c, after[:onset])
        start, end = run[0].start, after[onset - 1].end
        evidence = [FluencyEvidence("decoded_sounds", f"extra decoded sounds /{' '.join(r)}/ placed on no expected sound", r),
                    FluencyEvidence("onset_overlap", f"the first {onset} sounds match how the following speech begins "
                                                     f"('{' '.join(c.word(w) for w in words) or '?'}'), then "
                                                     f"{len(r) - onset} sounds differ", onset),
                    FluencyEvidence("interruption", why, True)]
        ctx = {"words": [c.word(w) for w in words], "word_indices": words}
        obs.append(FluencyObservation("", "FALSE_START", LABELS["FALSE_START"], start, end, f"/{' '.join(r)}/, interrupted",
                                      evidence, "Speech begins like what follows, breaks off and continues differently; "
                                      "consistent with a false start (the speaker's intention cannot be known from the "
                                      "audio).", Strength.LOW.value, ctx, c.play(start, end, 150.0, 150.0), c.engine, True))
    return obs


def _other(c: _Ctx, other) -> list[FluencyObservation]:
    obs = []
    for g in other:
        a, b = g["a"], g["b"]
        ctx = {"word_before": c.word(a.word), "word_after": c.word(b.word if b.kind == "target" else b.word + 1),
               "position": "between words"}
        evidence = [FluencyEvidence("decoded_gap", f"{g['gap']:.0f} ms between decoded sounds", g["gap"]),
                    FluencyEvidence("acoustic", "speech-like sound continues through it, but no speech sound was decoded",
                                    c.active_ms(g["start"], g["end"]))]
        obs.append(FluencyObservation("", "OTHER_HESITATION", LABELS["OTHER_HESITATION"], g["start"], g["end"],
                                      f"{g['gap'] / 1000:.2f} s of sound with no decoded speech", evidence,
                                      "Sound without decoded speech between words; possibly a prolonged sound, a hum or "
                                      "a breath (insufficient evidence to tell).", Strength.INSUFFICIENT.value, ctx,
                                      c.play(g["start"], g["end"]), c.engine, False))
    return obs


def _metrics(c: _Ctx, silent: list[tuple[float, float]], support: float | None) -> FluencyMetrics:
    lead = c.sounds[0].start if c.sounds else None
    trail = c.duration - c.sounds[-1].end if c.sounds else None
    reason = None
    if not c.target:
        reason = "no sound of the sentence was decoded"
    t0 = c.target[0].start if c.target else None
    t1 = c.target[-1].end if c.target else None
    first_w = c.target[0].word if c.target else None
    last_w = c.target[-1].word if c.target else None
    syllables = sum(1 for ph in c.expected(first_w, last_w) if is_vowel(ph)) if c.target else None
    interval = (t1 - t0) if c.target else None
    inside = [(max(s, t0), min(e, t1)) for s, e in silent if c.target and e > t0 and s < t1]
    pause_total = sum(e - s for s, e in inside)
    durations = [e - s for s, e in inside]
    if reason is None and (support is None or support < RATE_MIN_SUPPORT):
        reason = "the sentence was not recognisably decoded (fewer than half of its expected sounds found)"
    if reason is None and syllables < RATE_MIN_SYLLABLES:
        reason = f"too short to measure a rate ({syllables} syllables)"
    if reason is None and (interval is None or interval <= 0 or interval - pause_total <= 0):
        reason = "no speaking interval"
    available = reason is None
    art_reason = reason
    if art_reason is None and not c.reliable:
        art_reason = ("pauses could not be checked against the recording's sound level (speech level not "
                      "measurable reliably), so the time spent without pauses is not known")
    art = art_reason is None
    return FluencyMetrics(
        syllable_count=syllables, speaking_interval_ms=interval,
        speech_active_ms=c.active_ms(t0, t1) if c.target else None,
        pause_count=len(inside), pause_total_ms=pause_total,
        mean_pause_ms=statistics.fmean(durations) if durations else None,
        median_pause_ms=statistics.median(durations) if durations else None,
        pause_ratio=(pause_total / interval) if interval and c.reliable else None,
        speaking_rate=(syllables / (interval / 1000.0)) if available else None,
        articulation_rate=(syllables / ((interval - pause_total) / 1000.0)) if art else None,
        rate_available=available, rate_unavailable_reason=reason,
        leading_silence_ms=lead, trailing_silence_ms=trail, word_interval_ms=c.word_interval,
        activity_reliable=c.reliable if c.act is not None else None, activity_coverage=c.coverage,
        definitions=DEFINITIONS, articulation_available=art, articulation_unavailable_reason=art_reason,
        word_interval_basis=c.interval_basis)


def _pace(c: _Ctx, m: FluencyMetrics, silent) -> list[FluencyObservation]:
    """Stretches between pauses that are much faster or slower than the sentence as a whole (only when the
    pauses that delimit the stretches are verified as silence)."""
    if not m.rate_available or not c.reliable:
        return []
    vowels = [s for s in c.target if is_vowel(c.result.words[s.word].phonemes[s.pos].expected.phoneme)]
    bounds = sorted(silent)
    stretches, cur = [], []
    for v in vowels:
        if cur and any(cur[-1].end <= s < v.start for s, _ in bounds):
            stretches.append(cur)
            cur = []
        cur.append(v)
    if cur:
        stretches.append(cur)
    total_ms = sum(st[-1].end - st[0].start for st in stretches if len(st) > 1)
    total_v = sum(len(st) - 1 for st in stretches if len(st) > 1)
    if total_ms <= 0 or len(stretches) < 2:
        return []
    overall = total_v / (total_ms / 1000.0)
    obs = []
    for st in stretches:
        dur = st[-1].end - st[0].start
        if len(st) < STRETCH_MIN_VOWELS or dur < STRETCH_MIN_MS:
            continue
        local = (len(st) - 1) / (dur / 1000.0)
        ratio = local / overall
        if FAST_STRETCH > ratio > SLOW_STRETCH:
            continue
        fast = ratio >= FAST_STRETCH
        words = sorted({s.word for s in st})
        ctx = {"words": [c.word(w) for w in words], "ratio": round(ratio, 2)}
        ev = [FluencyEvidence("stretch_rate", f"{local:.1f} vowel nuclei per second in this stretch against {overall:.1f} "
                                              "in the sentence's other stretches", round(local, 2)),
              FluencyEvidence("position", f"between pauses, '{c.word(words[0])} … {c.word(words[-1])}'", None)]
        obs.append(FluencyObservation("", "RATE_ANOMALY", LABELS["RATE_ANOMALY"], st[0].start, st[-1].end,
                                      "relatively fast passage for this sentence" if fast else
                                      "relatively slow passage for this sentence", ev,
                                      "The pace of this stretch differs clearly from the rest of the sentence "
                                      "(no comparison with other speakers).", Strength.LOW.value, ctx,
                                      c.play(st[0].start, st[-1].end, 100.0, 100.0), c.engine, False))
    return obs


def _summary(obs: list[FluencyObservation], m: FluencyMetrics) -> FluencySummary:
    notice = [o for o in obs if o.notice]
    by_type: dict[str, int] = {}
    for o in notice:
        by_type[o.type] = by_type.get(o.type, 0) + 1
    if not m.rate_available or not m.articulation_available:
        pace = "insufficient evidence"
    elif any(o.type == "RATE_ANOMALY" for o in obs):
        pace = "uneven"
    elif m.pause_ratio is not None and m.pause_ratio >= PAUSE_HEAVY_RATIO:
        pace = "pause-heavy"
    else:
        pace = "steady"
    phrases = []
    groups: dict[str, int] = {}
    for o in notice:  # unusually long pauses are named as such, not merged into "hesitation pauses"
        g = "LONG_PAUSE" if o.type == "PAUSE" and o.classification == PauseClass.UNUSUALLY_LONG.value else o.type
        groups[g] = groups.get(g, 0) + 1
    names = {"LONG_PAUSE": ("long pause", "long pauses"),
             "PAUSE": ("possible hesitation pause", "possible hesitation pauses"),
             "OTHER_HESITATION": ("possible hesitation", "possible hesitations"),
             "FILLER": ("possible filler", "possible fillers"), "REPETITION": ("possible repetition", "possible repetitions"),
             "RESTART": ("possible restart", "possible restarts"), "FALSE_START": ("possible false start", "possible false starts"),
             "RATE_ANOMALY": ("change of pace", "changes of pace")}
    words = {1: "one", 2: "two", 3: "three", 4: "four"}
    for t in ("LONG_PAUSE", "PAUSE", "FILLER", "REPETITION", "RESTART", "FALSE_START", "OTHER_HESITATION", "RATE_ANOMALY"):
        n = groups.get(t, 0)
        if n:
            phrases.append(f"{words.get(n, str(n))} {names[t][0] if n == 1 else names[t][1]}")
    lead = {"steady": "Speech was generally steady", "uneven": "The pace changed within the sentence",
            "pause-heavy": "Speech contained a lot of pausing", "insufficient evidence":
            "There is not enough evidence to describe the pace"}[pace]
    unverified = pace == "insufficient evidence" and m.rate_available
    if unverified:
        lead = "Pauses could not be checked against this recording's sound level, so the pace is not described"
    elif pace == "insufficient evidence" and "not recognisably decoded" in (m.rate_unavailable_reason or ""):
        lead = ("The sentence was decoded too unclearly to describe its pace; only very long pauses are counted")
    if phrases and pace == "insufficient evidence":
        text = lead + ". To notice: " + (", ".join(phrases[:-1]) + " and " + phrases[-1] if len(phrases) > 1
                                         else phrases[0]) + "."
    elif phrases:
        text = lead + ", with " + (", ".join(phrases[:-1]) + " and " + phrases[-1] if len(phrases) > 1 else phrases[0]) + "."
    else:
        text = lead + "; nothing stood out." if pace != "insufficient evidence" else lead + "."
    if notice and all(o.strength in ("low", "ambiguous") for o in notice):
        text += " The evidence is weak."
    return FluencySummary(pace=pace, text=text, notice=len(notice), by_type=by_type)


def _support(result: PronunciationResult) -> float | None:
    """Share of the sentence's expected sounds decoded exactly (substitutions and omissions count against it;
    extra sounds do not, so fillers and repeats never make a rate unavailable). >= 0.5 is M7's
    defensibility limit (at most 0.5 differences per expected sound)."""
    phones = [p for w in result.words for p in w.phonemes]
    if not phones:
        return None
    return sum(1 for p in phones if p.engine_evidence.get("operation") == "match") / len(phones)


def empty_fluency(state: str, message: str | None = None, engine: str | None = None) -> dict[str, Any]:
    return {"version": FLUENCY_VERSION, "state": state, "message": message, "engine": engine, "observations": [],
            "metrics": None, "summary": None, "compact": {"state": state, "notice": 0},
            "thresholds": THRESHOLDS, "caveats": CAVEATS, "integrity": {"ok": True, "issues": []}}


CAVEATS = [
    "Fluency observations are candidates from timing and decoded sounds, not judgements: pauses, repeats and "
    "pace can all be natural or intentional.",
    "The listening models recognise speech sounds, not words: fillers, repetitions and restarts are inferred from "
    "extra decoded sounds and may be wrong. Listen to each one.",
    SHARED_MODEL_NOTE,
    "No fluency score is computed; rates are not compared with other speakers.",
]


def build_fluency(result: PronunciationResult, samples: np.ndarray | None = None,
                  sample_rate: int | None = None) -> dict[str, Any]:
    """The fluency layer for one analysed recording (the whole attempt, or M7's sentence region)."""
    engine = result.engine.name
    if result.status in ("failed", "blocked"):
        return empty_fluency("unavailable", "No fluency evidence: the analysis did not produce evidence.", engine)
    duration = result.recording.audio.duration_ms
    if samples is not None and sample_rate:
        duration = len(samples) * 1000.0 / sample_rate
    c = _Ctx(result, samples, sample_rate, duration)
    if not c.target:
        return empty_fluency("no_speech", "No speech sounds of the sentence were decoded.", engine)
    pauses, silent, other = _pauses(c)
    runs = _runs(c)
    copies, taken = _copies(c, silent)
    fillers, used = _fillers(c, runs, silent, taken)
    copies += _false_starts(c, runs, used, taken, silent)
    # sound with nothing decoded next to a filler is the filler itself (a prolonged "uhhh"), and inside a
    # repetition, restart or false start it is part of that event: never a second event for the same moment
    other = [g for g in other if not any(o.start_ms < g["end"] and g["start"] < o.end_ms for o in fillers + copies)]
    support = _support(result)
    metrics = _metrics(c, silent, support)
    pace = _pace(c, metrics, silent)
    if support is None or support < RATE_MIN_SUPPORT:
        # the sentence was not recognisably decoded: where words lie (and so "inside a phrase") is uncertain and
        # extra decoded sounds are plentiful. Everything stays listed; only an unusually long pause is counted.
        for o in pauses + fillers + copies:
            if o.notice and not (o.type == "PAUSE" and o.classification == PauseClass.UNUSUALLY_LONG.value):
                o.notice = False
                o.interpretation += (" The sentence was decoded unclearly, so where this lies in it is uncertain; "
                                     "it is listed but not counted.")
    for p in pauses:
        host = next((o for o in copies + fillers if o.start_ms - 1e-6 <= p.start_ms and p.end_ms <= o.end_ms + 300), None)
        if host is not None:  # a pause inside a repetition, restart, false start or filler belongs to it
            p.notice = False
            p.context["part_of"] = host.type
    obs = sorted(pauses + fillers + copies + _other(c, other) + pace, key=lambda o: (o.start_ms, o.end_ms, o.type))
    for i, o in enumerate(obs, 1):
        o.id = f"f{i}"
    summary = _summary(obs, metrics)
    out = {
        "version": FLUENCY_VERSION, "state": "ok", "message": None, "engine": engine,
        "region": {"start_ms": 0.0, "end_ms": duration, "timeline": TIMELINE},
        "observations": [o.to_dict() for o in obs],
        "metrics": asdict(metrics),
        "summary": asdict(summary),
        "compact": {"state": "ok", "notice": summary.notice, "pace": summary.pace},
        "thresholds": THRESHOLDS, "caveats": CAVEATS,
    }
    issues = validate_fluency(out, result)
    out["integrity"] = {"ok": not issues, "issues": issues}
    return out


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------

CANDIDATE_TYPES = ("FILLER", "REPETITION", "RESTART", "FALSE_START", "OTHER_HESITATION")

REQUIRED_EVIDENCE = {
    "PAUSE": {"position"},                         # never classified from duration alone
    "FILLER": {"decoded_sounds", "position"},      # phonetic evidence, not timing alone
    "REPETITION": {"decoded_sounds", "similarity", "adjacency", "lexical"},   # never from text alone
    "RESTART": {"decoded_sounds", "similarity", "adjacency"},
    "FALSE_START": {"decoded_sounds", "onset_overlap", "interruption"},   # never from a pause alone
    "RATE_ANOMALY": {"stretch_rate"},
    "OTHER_HESITATION": {"acoustic"},
}


def validate_fluency(fl: dict[str, Any], result: PronunciationResult | None = None) -> list[str]:
    """Invariants for any fluency output. Returns violations."""
    issues: list[str] = []
    if fl.get("state") != "ok":
        if fl.get("observations"):
            issues.append(f"state {fl.get('state')} with observations")
        return issues
    region = fl.get("region") or {}
    lo, hi = region.get("start_ms", 0.0), region.get("end_ms")
    obs = fl.get("observations") or []
    m = fl.get("metrics") or {}
    ids = [o.get("id") for o in obs]
    if len(set(ids)) != len(ids):
        issues.append("duplicate observation ids")
    for o in obs:
        oid = o.get("id")
        s, e = o.get("start_ms"), o.get("end_ms")
        if o.get("type") not in TYPES:
            issues.append(f"{oid}: unknown type {o.get('type')!r}")
        if o.get("strength") not in STRENGTHS:
            issues.append(f"{oid}: strength {o.get('strength')!r} is not allowed")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (s, e)):
            issues.append(f"{oid}: invalid times")
            continue
        if s < 0:
            issues.append(f"{oid}: negative timestamp")
        if e < s:
            issues.append(f"{oid}: end before start")
        if hi is None or s < lo - 1e-6 or e > hi + 1.0:
            issues.append(f"{oid}: outside the analysed audio [{lo}, {hi}]")
        if abs(o.get("duration_ms", -1) - (e - s)) > 1e-6:
            issues.append(f"{oid}: duration does not match its interval")
        pb = o.get("playback") or {}
        span, play = pb.get("span_ms"), pb.get("play_ms")
        if pb.get("timeline") != TIMELINE or not span or not play:
            issues.append(f"{oid}: no exact playback")
        else:
            if abs(span[0] - s) > 1e-6 or abs(span[1] - e) > 1e-6:
                issues.append(f"{oid}: playback span differs from the observation")
            if play[0] > s + 1e-6 or play[1] < e - 1e-6 or play[0] < 0 or (hi is not None and play[1] > hi + 1.0):
                issues.append(f"{oid}: playback window does not cover the observation inside the audio")
            ctx_ms = pb.get("context_ms")
            if not ctx_ms or len(ctx_ms) != 2 or min(ctx_ms) < -1e-6 or \
                    abs(play[0] - (s - ctx_ms[0])) > 1e-6 or abs(play[1] - (e + ctx_ms[1])) > 1e-6:
                issues.append(f"{oid}: playback context not stated exactly (play window ≠ span ± context)")
        ev = o.get("evidence") or []
        if not ev or any(not x.get("kind") or not x.get("detail") for x in ev):
            issues.append(f"{oid}: no evidence")
        kinds = {x.get("kind") for x in ev}
        missing = REQUIRED_EVIDENCE.get(o.get("type"), set()) - kinds
        if missing:
            issues.append(f"{oid}: {o.get('type')} without {', '.join(sorted(missing))} evidence")
        if o.get("type") == "PAUSE":
            if o.get("classification") not in PAUSE_CLASSES:
                issues.append(f"{oid}: unknown pause classification")
            if o.get("classification") in ("possible_hesitation", "unusually_long") and \
                    "relative_duration" not in kinds:
                issues.append(f"{oid}: hesitation without relative-duration evidence")
            sp0 = (m.get("speaking_interval_ms") is not None)
            if sp0 and m.get("leading_silence_ms") is not None and e <= m["leading_silence_ms"] + 1e-6:
                issues.append(f"{oid}: leading silence reported as a pause")
            if sp0 and m.get("trailing_silence_ms") is not None and hi is not None and \
                    s >= hi - m["trailing_silence_ms"] - 1e-6:
                issues.append(f"{oid}: trailing silence reported as a pause")
        text = " ".join([str(o.get("label")), str(o.get("interpretation")), str(o.get("observed"))]).lower()
        for banned in ("score", "wrong", "incorrect", "error", "you made", "disfluent speaker", "%"):
            if banned in text:
                issues.append(f"{oid}: judgemental wording ({banned})")
    pauses = sorted((o["start_ms"], o["end_ms"]) for o in obs if o.get("type") == "PAUSE")
    if any(b[0] < a[1] - 1e-6 for a, b in zip(pauses, pauses[1:])):
        issues.append("overlapping pauses")
    keys = [(o.get("type"), o.get("start_ms"), o.get("end_ms")) for o in obs]
    if len(set(keys)) != len(keys):
        issues.append("duplicate observation (same type and interval)")
    # one moment is one candidate: a filler, repetition, restart, false start or hesitation never shares time
    # with another (the same decoded sound counted twice)
    cands = sorted((o["start_ms"], o["end_ms"], o.get("id"), o.get("type")) for o in obs if o.get("type") in CANDIDATE_TYPES
                   and all(isinstance(o.get(k), (int, float)) for k in ("start_ms", "end_ms")))
    for i, a in enumerate(cands):
        for b in cands[i + 1:]:
            if b[0] >= a[1] - 1e-6:
                break
            issues.append(f"{a[2]} ({a[3]}) and {b[2]} ({b[3]}) claim the same moment")
    # rates: syllables from the expected pronunciation, never decoded sounds; none when unreliable
    if m:
        if m.get("rate_available"):
            if not m.get("speaking_interval_ms") or m.get("syllable_count") is None:
                issues.append("rate available without an interval or a syllable count")
            else:
                sr_ = m["syllable_count"] / (m["speaking_interval_ms"] / 1000.0)
                if m.get("speaking_rate") is None or abs(m["speaking_rate"] - sr_) > 1e-6:
                    issues.append("speaking rate is not syllables / speaking interval")
                denom = m["speaking_interval_ms"] - m["pause_total_ms"]
                if m.get("articulation_available"):
                    if m.get("articulation_rate") is None or denom <= 0 or \
                            abs(m["articulation_rate"] - m["syllable_count"] / (denom / 1000.0)) > 1e-6:
                        issues.append("articulation rate is not syllables / (interval - pauses)")
                elif m.get("articulation_rate") is not None or m.get("pause_ratio") is not None:
                    issues.append("articulation rate or pause ratio given although pauses were not verified as silence")
            if m.get("articulation_available") and not m.get("activity_reliable"):
                issues.append("articulation rate marked available without a reliable level estimate")
        elif m.get("speaking_rate") is not None or m.get("articulation_rate") is not None:
            issues.append("a rate is given although it is marked unavailable")
        if result is not None:
            target = [s for s in decoded_sounds(result) if s.kind == "target"]
            if target:
                first, last = target[0].word, target[-1].word
                expected = [p.expected.phoneme for w in result.words[first:last + 1] for p in w.phonemes]
                if m.get("syllable_count") != sum(1 for ph in expected if is_vowel(ph)):
                    issues.append("syllable count is not the expected vowel nuclei")
            sup = _support(result)
            if m.get("rate_available") and (sup is None or sup < RATE_MIN_SUPPORT):
                issues.append("a rate is given although the sentence was not recognisably decoded")
    summ = (fl.get("summary") or {}).get("text") or ""
    for banned in ("score", "%", "percent", "fluent speaker", "rank", "wrong"):
        if banned in summ.lower():
            issues.append(f"summary uses '{banned}'")
    return issues


def fluency_compact(fl: dict[str, Any] | None) -> dict[str, Any] | None:
    if not fl:
        return None
    return dict(fl.get("compact") or {"state": fl.get("state"), "notice": 0})


# ----------------------------------------------------------------------
# M7 continued speech (overflow / uncertain region): described, never interpreted as the sentence
# ----------------------------------------------------------------------

def describe_region(result: PronunciationResult, samples: np.ndarray | None, sample_rate: int | None,
                    start_ms: float, end_ms: float, kind: str) -> dict[str, Any]:
    """Timing of the continued speech M7 kept outside the sentence (from the whole-attempt analysis)."""
    from pronunciation_lab.reader.boundary import speech_activity

    rec = result.engine_evidence.get("recognition") or {}
    mpf = (result.engine_evidence.get("frame_clock") or {}).get("ms_per_frame") or 20
    spans = []
    for s in rec.get("frame_spans") or []:
        try:
            a, b = float(s[0]) * mpf, float(s[1]) * mpf
        except (TypeError, ValueError, IndexError):
            continue
        if start_ms <= a and b <= end_ms:
            spans.append((a, b))
    spans.sort()
    gaps = [b[0] - a[1] for a, b in zip(spans, spans[1:]) if b[0] - a[1] >= PAUSE_CANDIDATE_MS]
    active = None
    if samples is not None and sample_rate:
        act = speech_activity(samples, sample_rate)
        hop = act["hop_ms"]
        i0, i1 = int(math.ceil(start_ms / hop)), int(end_ms / hop)
        active = float(act["active"][i0:i1].sum()) * hop if i1 > i0 else 0.0
    span = (spans[-1][1] - spans[0][0]) if spans else None
    return {"kind": kind, "start_ms": start_ms, "end_ms": end_ms, "duration_ms": end_ms - start_ms,
            "speech_active_ms": active, "decoded_sounds": len(spans),
            "decoded_sounds_per_s": (len(spans) / (span / 1000.0)) if span and span > 0 else None,
            "pause_candidates": len(gaps), "pause_candidate_ms": sum(gaps),
            "note": "Continued speech is kept with the recording and is not part of this sentence's analysis; "
                    "no syllable rate is given because its words are unknown.",
            "playback": {"timeline": TIMELINE, "span_ms": [start_ms, end_ms], "play_ms": [start_ms, end_ms],
                         "context_ms": [0.0, 0.0]}}


# ----------------------------------------------------------------------
# Two engines: side by side, never merged
# ----------------------------------------------------------------------

def compare_fluency(first: dict[str, Any], second: dict[str, Any], overlap: float = 0.5) -> dict[str, Any]:
    """Pair two engines' observations of the same audio by type and time overlap. Agreement is not
    independent confirmation (shared acoustic model); single-engine observations stay single-engine."""
    a_obs = [o for o in first.get("observations") or [] if o.get("notice") or o.get("type") != "PAUSE"]
    b_obs = [o for o in second.get("observations") or [] if o.get("notice") or o.get("type") != "PAUSE"]
    rows, used = [], set()
    for o in a_obs:
        match = None
        for j, p in enumerate(b_obs):
            if j in used or p["type"] != o["type"]:
                continue
            inter = min(o["end_ms"], p["end_ms"]) - max(o["start_ms"], p["start_ms"])
            span = max(o["end_ms"], p["end_ms"]) - min(o["start_ms"], p["start_ms"])
            if span > 0 and inter / span >= overlap or (span == 0 and inter == 0):
                match = j
                break
        if match is None:
            rows.append({"type": o["type"], "engines": [first.get("engine")], "first": o, "second": None,
                         "agreement": "first_only"})
        else:
            used.add(match)
            rows.append({"type": o["type"], "engines": [first.get("engine"), second.get("engine")], "first": o,
                         "second": b_obs[match], "agreement": "both_decoding_paths"})
    for j, p in enumerate(b_obs):
        if j not in used:
            rows.append({"type": p["type"], "engines": [second.get("engine")], "first": None, "second": p,
                         "agreement": "second_only"})
    rows.sort(key=lambda r: (r["first"] or r["second"])["start_ms"])
    return {"engines": [first.get("engine"), second.get("engine")], "rows": rows, "shared_model_note": SHARED_MODEL_NOTE,
            "agreement_text": {"both_decoding_paths": "Both decoding paths show this; supporting evidence, not "
                                                      "independent confirmation.",
                               "first_only": f"Only {first.get('engine')} shows this.",
                               "second_only": f"Only {second.get('engine')} shows this."}}


def validate_fluency_comparison(cmp: dict[str, Any]) -> list[str]:
    issues = []
    e1, e2 = cmp["engines"]
    for r in cmp["rows"]:
        if r["agreement"] == "both_decoding_paths":
            if not (r["first"] and r["second"]) or r["engines"] != [e1, e2]:
                issues.append("a single engine's observation presented as shown by both")
            elif r["first"].get("engine") == r["second"].get("engine"):
                issues.append("both sides of an agreement come from the same engine")
        elif r["agreement"] == "first_only":
            if r["second"] is not None or r["engines"] != [e1] or (r["first"] or {}).get("engine") not in (None, e1):
                issues.append(f"an observation of {e1} attributed wrongly")
        elif r["agreement"] == "second_only":
            if r["first"] is not None or r["engines"] != [e2] or (r["second"] or {}).get("engine") not in (None, e2):
                issues.append(f"an observation of {e2} attributed wrongly")
        else:
            issues.append(f"unknown agreement {r['agreement']!r}")
    if "independent" not in cmp.get("shared_model_note", "") and "not independent" not in str(cmp.get("agreement_text")):
        issues.append("missing shared-model caveat")
    return issues
