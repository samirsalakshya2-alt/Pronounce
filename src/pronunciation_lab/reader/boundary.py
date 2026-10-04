"""M7 — Sentence boundary & overflow: which part of a recording attempt belongs to its sentence.

The user may keep speaking after finishing the active sentence. The recording
attempt (capture boundary) still ends only when they select another sentence or
stop; M7 adds an *analysis* boundary inside the attempt:

    attempt timeline (analysis WAV, 0 → duration)
    ├── target      the sentence: the only audio given to pronunciation analysis (M4/M5, later M6)
    ├── uncertain   cannot be attributed confidently: preserved, not analysed as the sentence
    └── overflow    continued speech after the sentence: preserved for M8, never analysed as the sentence

Every millisecond belongs to exactly one region; the original audio is never
modified. Continued speech is not a mistake and is not classified here (no
fillers, repetitions or restarts — that is M8).

Evidence (from one engine's analysis of the whole attempt — no extra model):

* the last *decoded* expected sound of the sentence (end of its CTC peak span);
* decoded sounds after it (the recognition sequence; continuation shows up as
  phones the alignment can only place as insertions after the final word);
* speech-like activity after it (frame energy relative to the recording's own
  noise floor) — independent of the decoder;
* completion of the final word (how much of it was decoded) and whether its
  alignment is interleaved with insertions or split by a pause;
* the gap between the sentence and the first decoded continuation sound.

Decision (conservative; a duration or silence alone never creates a boundary):

    NO_RELIABLE_BOUNDARY  evidence missing or invalid → whole attempt is the target (as before M7)
    TARGET_ONLY           < MIN_OVERFLOW_PHONES decoded sounds after the sentence, or almost no
                          speech energy after it (breath, release, a weak final sound)
    TARGET_PLUS_OVERFLOW  decoded continuation AND speech energy after a completed final word
    BOUNDARY_UNCERTAIN    continuation present but the final word is weakly decoded or its
                          alignment is suspicious; or long speech-like activity the decoder
                          did not decode — target = up to the last decoded target sound,
                          the rest is preserved as uncertain

Calibration (R01–R20, both engines, docs/M7_SENTENCE_BOUNDARY_OVERFLOW.md):
clean recordings have 0 decoded sounds after the sentence and at most 240 ms
of speech-like energy after it.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from pronunciation_lab.benchmark.analysis import PAUSE_MS
from pronunciation_lab.benchmark.schema import PronunciationResult

BOUNDARY_VERSION = "m7.1"
STATES = ("TARGET_ONLY", "TARGET_PLUS_OVERFLOW", "BOUNDARY_UNCERTAIN", "NO_RELIABLE_BOUNDARY")
REGION_KINDS = ("target", "overflow", "uncertain")
NEEDS_TARGET_ANALYSIS = ("TARGET_PLUS_OVERFLOW", "BOUNDARY_UNCERTAIN")

# A continuation is at least a short word: fewer decoded sounds are treated as a tail of the sentence.
MIN_OVERFLOW_PHONES = 3
# Speech-like energy after the sentence needed before decoded sounds count as continuation
# (clean benchmark recordings: at most 240 ms of trailing energy).
MIN_OVERFLOW_SPEECH_MS = 250.0
# Speech-like activity the decoder did not decode at all, long enough to keep out of the target.
UNDECODED_SPEECH_MS = 1000.0
# CTC spans mark where a sound peaked, not where it ended: keep this much after the last
# decoded target sound when there is room before the continuation.
MIN_TAIL_MS = 60.0
# A gap shorter than this between sentence and continuation is reported as a tight boundary.
CLEAN_GAP_MS = 100.0
# Final word counts as completed when at least this share of its expected sounds was decoded.
MIN_FINAL_WORD_DECODED = 0.5
# Speech activity: 20 ms frames every 10 ms; active when this many dB above the recording's
# 10th-percentile frame level (its noise floor) and above an absolute floor.
ACTIVITY_WIN_MS, ACTIVITY_HOP_MS = 20.0, 10.0
ACTIVITY_MARGIN_DB, ACTIVITY_MIN_DBFS = 15.0, -60.0
DIGITAL_SILENCE_DBFS = -90.0
# Speech-energy evidence is only trusted when the activity estimator agrees with the decoder on the
# sentence itself: at least this share of the sentence's decoded sounds must fall on active frames.
# In a noisy or speech-dense recording the noise-floor estimate can come from speech (or from room
# noise only a few dB below it); the estimator then marks the speech itself as inactive, and its
# "no speech after the sentence" must not veto decoded continuation. R01–R20: 0.96–1.00; the
# M7 manual-test attempts that failed: 0.07 and 0.10.
MIN_ACTIVITY_COVERAGE = 0.6
# Decoded continuation this long (about two words) is never vetoed by missing speech energy.
STRONG_CONTINUATION_PHONES = 8
# Where the sentence ends is only defensible when the sentence itself was decoded recognisably: at most
# this many alignment errors per expected sound (end-free alignment cost / expected sounds). Above it
# (heavy noise, another text) the cut wanders by seconds in either direction — into the continuation or
# into the sentence — so feedback is withheld instead. R01–R20 + continuations: <= 0.30; the M7
# manual-test attempts (noisy room, cuts correct): 0.36–0.42; speech-dense noisy simulations: 0.59–0.91.
MAX_DEFENSIBLE_COST = 0.5
# Two engines' boundaries "differ" when their states differ or their cuts are this far apart.
DISAGREE_CUT_MS = 200.0
# The few decoded sounds after the cheapest end may themselves finish with the sentence's final word
# (a word repeated or restarted inside the sentence: "begin… beginning… begins"): the sentence then
# ends after the later one, and the boundary is never certain.
# Final-word match: at least this share of its sounds within its length + 2 last decoded sounds.
FINAL_WORD_AT_END_SHARE = 0.8
REPEAT_TAIL_MAX_EXTRA = 4

THRESHOLDS = {
    "min_overflow_phones": MIN_OVERFLOW_PHONES, "min_overflow_speech_ms": MIN_OVERFLOW_SPEECH_MS,
    "undecoded_speech_ms": UNDECODED_SPEECH_MS, "min_tail_ms": MIN_TAIL_MS, "clean_gap_ms": CLEAN_GAP_MS,
    "min_final_word_decoded": MIN_FINAL_WORD_DECODED, "pause_ms": PAUSE_MS,
    "final_word_at_end_share": FINAL_WORD_AT_END_SHARE, "repeat_tail_max_extra": REPEAT_TAIL_MAX_EXTRA,
    "disagree_cut_ms": DISAGREE_CUT_MS, "min_activity_coverage": MIN_ACTIVITY_COVERAGE,
    "strong_continuation_phones": STRONG_CONTINUATION_PHONES, "max_defensible_cost": MAX_DEFENSIBLE_COST,
    "activity": {"win_ms": ACTIVITY_WIN_MS, "hop_ms": ACTIVITY_HOP_MS, "margin_db": ACTIVITY_MARGIN_DB,
                 "min_dbfs": ACTIVITY_MIN_DBFS, "digital_silence_dbfs": DIGITAL_SILENCE_DBFS},
}

DECODED_OPS = ("match", "substitution")


def speech_activity(samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    """Frame levels and a speech-like activity mask relative to the recording's own noise floor."""
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    hop = max(1, int(round(sample_rate * ACTIVITY_HOP_MS / 1000.0)))
    win = max(hop, int(round(sample_rate * ACTIVITY_WIN_MS / 1000.0)))
    if len(x) < win:
        return {"db": np.zeros(0), "active": np.zeros(0, bool), "hop_ms": ACTIVITY_HOP_MS, "floor_db": None}
    n = 1 + (len(x) - win) // hop
    cs = np.concatenate([[0.0], np.cumsum(x * x)])  # frame energies in O(n) memory
    starts = np.arange(n) * hop
    rms = np.sqrt(np.maximum(cs[starts + win] - cs[starts], 0.0) / win) + 1e-9
    db = 20.0 * np.log10(rms)
    real = db[db > DIGITAL_SILENCE_DBFS]  # exact digital silence (padding, muted input) is not a noise floor
    floor = float(np.percentile(real, 10)) if len(real) else DIGITAL_SILENCE_DBFS
    active = db > max(floor + ACTIVITY_MARGIN_DB, ACTIVITY_MIN_DBFS)
    return {"db": db, "active": active, "hop_ms": ACTIVITY_HOP_MS, "floor_db": floor}


def _active_ms(act: dict[str, Any], start_ms: float, end_ms: float) -> float:
    hop = act["hop_ms"]
    a, b = int(math.ceil(start_ms / hop)), int(math.floor(end_ms / hop))
    if b <= a or not len(act["active"]):
        return 0.0
    return float(act["active"][a:b].sum()) * hop


def activity_coverage(act: dict[str, Any], spans: list[tuple[float, float]]) -> float:
    """Share of decoded sounds whose loudest nearby frame the activity estimator marks as speech."""
    hop, db, active = act["hop_ms"], act["db"], act["active"]
    if not spans or not len(db):
        return 0.0
    hits = 0
    for s, e in spans:
        lo, hi = max(0, int((s - ACTIVITY_WIN_MS / 2) / hop)), min(len(db), int((e + ACTIVITY_WIN_MS / 2) / hop) + 1)
        if hi > lo:
            hits += bool(active[lo + int(np.argmax(db[lo:hi]))])
    return hits / len(spans)


def _quietest_ms(act: dict[str, Any], lo: float, hi: float) -> float | None:
    """Centre of the lowest-energy frame whose centre lies in [lo, hi]."""
    hop, db = act["hop_ms"], act["db"]
    if not len(db):
        return None
    centres = np.arange(len(db)) * hop + ACTIVITY_WIN_MS / 2.0
    sel = np.where((centres >= lo) & (centres <= hi))[0]
    if not len(sel):
        return None
    return float(centres[sel[np.argmin(db[sel])]])


def _valid_span(p) -> bool:
    s, e = p.timing.start_ms, p.timing.end_ms
    return isinstance(s, (int, float)) and isinstance(e, (int, float)) and math.isfinite(s) and math.isfinite(e) \
        and 0 <= s < e


def end_free_alignment(expected: list[str], heard: list[str],
                       pause_before: set[int] | None = None) -> tuple[int, list[tuple[int | None, int | None]]]:
    """Unit-cost alignment of the whole sentence against a *prefix* of the decoded sounds.

    Decoded sounds after the prefix cost nothing, so continued speech is not forced into the
    sentence (a whole-recording alignment must place it somewhere and stretches the final word
    over it). Returns k (decoded sounds used by the sentence) and the (expected, heard) pairs.

    Among equally good ends, the later one keeps the sentence's own (substituted) sounds in the
    sentence — but never across a pause: `pause_before` holds the indices of decoded sounds that
    follow a pause, and a tied later end may not reach past one (otherwise an unsaid final sound
    "costs" the same as a continuation sound substituted for it).
    """
    n, m = len(expected), len(heard)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        dp[i][0] = i
        e, row, prev = expected[i - 1], dp[i], dp[i - 1]
        for j in range(1, m + 1):
            row[j] = min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (e != heard[j - 1]))
    best = min(dp[n])
    end_free_alignment.last_cost = best

    def trace(end: int) -> list[tuple[int | None, int | None]]:
        out: list[tuple[int | None, int | None]] = []
        i, j = n, end
        while i > 0 or j > 0:
            if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (expected[i - 1] != heard[j - 1]):
                out.append((i - 1, j - 1)); i -= 1; j -= 1
            elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
                out.append((i - 1, None)); i -= 1
            else:
                out.append((None, j - 1)); j -= 1
        out.reverse()
        return out

    ties = [j for j in range(m + 1) if dp[n][j] == best]
    k, pairs = ties[0], trace(ties[0])
    for j in ties[1:]:
        if any(i in (pause_before or ()) for i in range(k, j)):
            break
        cand = trace(j)
        # the extra sounds must stand for the sentence's own sounds credibly: an exact match, or a
        # substitution within the same broad class (vowel for vowel, consonant for consonant)
        if not all(e is None or same_class(expected[e], heard[h]) for e, h in cand if h is not None and h >= k):
            continue  # not this end; a later equally good end may still be credible
        k, pairs = j, cand
    return k, pairs


_VOWEL_CHARS = set("aeiouyɪʊʌəɛæɑɔɐɚɝɜɒʏøœᵻɘɵɤɯɨʉ")


def same_class(a: str, b: str) -> bool:
    """Vowel for vowel or consonant for consonant (rhotacised vowels such as ɚ, ɔːɹ count as vowels)."""
    return a == b or ((a[:1] in _VOWEL_CHARS) == (b[:1] in _VOWEL_CHARS))


def ends_with(tail: list[str], word: list[str]) -> bool:
    """Do the last decoded sounds match the word (free start, anchored end)?"""
    if not word or not tail:
        return False
    window = tail[-(len(word) + 2):]
    k, pairs = end_free_alignment(word[::-1], window[::-1])  # reversed: anchored at the end, free start
    if not pairs or pairs[0] != (0, 0):  # the last decoded sound must be the word's last sound
        return False
    matched = sum(1 for i, j in pairs if i is not None and j is not None and word[::-1][i] == window[::-1][j])
    return matched >= FINAL_WORD_AT_END_SHARE * len(word)


def _no_boundary(state: str, reasons: list[str], duration: float, engine: str | None, evidence=None) -> dict[str, Any]:
    return {"version": BOUNDARY_VERSION, "state": state, "engine": engine, "reasons": reasons,
            "duration_ms": duration, "cut_ms": duration, "evidence": evidence or {}, "thresholds": THRESHOLDS,
            "regions": [{"kind": "target", "start_ms": 0.0, "end_ms": duration}]}


def detect_boundary(result: PronunciationResult, samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    """Partition one attempt's analysis timeline into target / uncertain / overflow regions."""
    duration = len(samples) * 1000.0 / sample_rate if sample_rate else 0.0
    engine = result.engine.name
    if result.status in ("failed", "blocked") or not result.words or duration <= 0:
        return _no_boundary("NO_RELIABLE_BOUNDARY", ["the analysis produced no evidence"], duration, engine)
    rec = result.engine_evidence.get("recognition") or {}
    heard, spans = rec.get("phones"), rec.get("frame_spans")
    ms_per_frame = (result.engine_evidence.get("frame_clock") or {}).get("ms_per_frame")
    if not isinstance(heard, list) or not isinstance(spans, list) or len(heard) != len(spans) \
            or not isinstance(ms_per_frame, (int, float)) or ms_per_frame <= 0:
        return _no_boundary("NO_RELIABLE_BOUNDARY", ["no decoded timing to place a boundary"], duration, engine)
    try:
        span_ms = [(float(f0) * ms_per_frame, float(f1) * ms_per_frame) for f0, f1 in spans]
    except (TypeError, ValueError):
        return _no_boundary("NO_RELIABLE_BOUNDARY", ["decoded timing is malformed"], duration, engine)
    if any(not (math.isfinite(x0) and math.isfinite(x1) and 0 <= x0 <= x1) for x0, x1 in span_ms) \
            or any(b[0] < a[0] for a, b in zip(span_ms, span_ms[1:])):
        return _no_boundary("NO_RELIABLE_BOUNDARY", ["decoded timing is malformed"], duration, engine)
    if span_ms and span_ms[-1][1] > duration + ms_per_frame:
        return _no_boundary("NO_RELIABLE_BOUNDARY", ["decoded timing lies outside the recording"], duration, engine)

    words = result.words
    expected = [p.expected.phoneme for w in words for p in w.phonemes]
    word_of = [wi for wi, w in enumerate(words) for _ in w.phonemes]
    pauses = {j for j in range(1, len(span_ms)) if span_ms[j][0] - span_ms[j - 1][1] >= PAUSE_MS}
    k, pairs = end_free_alignment(expected, heard, pauses)
    cost_per_sound = end_free_alignment.last_cost / len(expected) if expected else None
    used = [(i, j) for i, j in pairs if i is not None and j is not None]
    if not expected or not used:
        return _no_boundary("NO_RELIABLE_BOUNDARY", ["no sound of the sentence was decoded"], duration, engine)
    # a final word repeated or restarted inside the sentence ("begin… beginning… begins"): the cheapest
    # end is the first attempt at it; if the next few decoded sounds end with the final word, the
    # sentence ends there instead (keeping the sentence's own sounds in the sentence).
    final_wi = len(words) - 1
    final_phones = [e for e, w in zip(expected, word_of) if w == final_wi]
    repeat_end = next((j for j in range(min(len(heard), k + len(final_phones) + REPEAT_TAIL_MAX_EXTRA), k, -1)
                       if ends_with(heard[k:j], final_phones)), None)
    first_end_ms = span_ms[k - 1][1]
    if repeat_end is not None:
        k = repeat_end
    t_end = span_ms[k - 1][1]
    post = span_ms[k:]
    act = speech_activity(samples, sample_rate)
    if not len(act["active"]):
        return _no_boundary("NO_RELIABLE_BOUNDARY", ["the recording is too short to measure speech activity"],
                            duration, engine)
    speech_after = _active_ms(act, t_end, duration)
    coverage = activity_coverage(act, [span_ms[j] for _, j in used])
    reliable = coverage >= MIN_ACTIVITY_COVERAGE

    final_idx = [i for i, w in enumerate(word_of) if w == final_wi]
    final_heard = [j for i, j in used if word_of[i] == final_wi]
    final_share = len(final_heard) / len(final_idx) if final_idx else 0.0
    last_wi = word_of[used[-1][0]]
    inside = [j for i, j in pairs if i is None and final_heard and final_heard[0] < j < final_heard[-1]]
    split_by_pause = any(span_ms[b][0] - span_ms[a][1] >= PAUSE_MS for a, b in zip(final_heard, final_heard[1:]))
    # the engine's own alignment of the whole attempt: did it place sentence sounds after this end?
    engine_end = max((p.timing.end_ms for w in words for p in w.phonemes
                      if p.engine_evidence.get("operation") in DECODED_OPS and _valid_span(p)), default=None)
    stretched = engine_end is not None and engine_end > t_end + ms_per_frame
    final_last_decoded = bool(final_idx) and any(i == final_idx[-1] for i, _ in used)

    evidence = {
        "last_target_ms": t_end, "last_target_word": words[last_wi].word, "last_target_word_index": last_wi,
        "target_decoded_sounds": k, "decoded_sounds": len(heard),
        "final_word": words[final_wi].word, "final_word_decoded": f"{len(final_heard)}/{len(final_idx)}",
        "final_word_complete": last_wi == final_wi and final_share >= MIN_FINAL_WORD_DECODED and final_last_decoded,
        "final_word_repeated": repeat_end is not None, "first_final_word_end_ms": first_end_ms,
        "post_phones": len(post), "post_first_ms": post[0][0] if post else None,
        "post_last_ms": post[-1][1] if post else None,
        "speech_after_ms": speech_after, "gap_ms": (post[0][0] - t_end) if post else None,
        "interleaved_insertions": len(inside), "final_word_split_by_pause": split_by_pause,
        "engine_alignment_end_ms": engine_end, "engine_alignment_stretched": stretched,
        "noise_floor_db": act["floor_db"], "activity_coverage": coverage, "activity_reliable": reliable,
        "alignment_cost_per_sound": cost_per_sound,
    }

    # Decoded continuation is the primary evidence; speech energy may confirm it or rule out
    # decoder noise over silence, but only when the energy estimator is reliable in this recording.
    if len(post) < MIN_OVERFLOW_PHONES:
        if reliable and speech_after >= UNDECODED_SPEECH_MS:
            state = "BOUNDARY_UNCERTAIN"
            reasons = [f"{speech_after:.0f} ms of speech-like sound after the sentence was not decoded"]
        else:
            # nothing decoded after the sentence can attach to its analysis
            return _no_boundary("TARGET_ONLY", [f"no continued speech after the sentence ({len(post)} decoded sounds"
                                                + (f", {speech_after:.0f} ms of speech-like sound)" if reliable else
                                                   "; speech level not measurable in this recording)")],
                                duration, engine, evidence)
    elif not reliable:
        state = "BOUNDARY_UNCERTAIN"
        reasons = [f"{len(post)} further sounds were decoded after the sentence",
                   f"speech level could not be measured reliably in this recording (the level estimate found "
                   f"speech at only {coverage:.0%} of the sentence's own sounds); the decoded sounds are trusted"]
    elif speech_after < MIN_OVERFLOW_SPEECH_MS:
        if len(post) < STRONG_CONTINUATION_PHONES:
            return _no_boundary("TARGET_ONLY", [f"{len(post)} decoded sounds after the sentence but only "
                                                f"{speech_after:.0f} ms of speech-like sound"], duration, engine, evidence)
        state = "BOUNDARY_UNCERTAIN"
        reasons = [f"{len(post)} further sounds were decoded after the sentence, although only "
                   f"{speech_after:.0f} ms of speech-like sound was measured"]
    elif evidence["final_word_complete"] and not inside and not split_by_pause and repeat_end is None:
        state = "TARGET_PLUS_OVERFLOW"
        reasons = [f"the final word '{words[final_wi].word}' was decoded ({evidence['final_word_decoded']} sounds)",
                   f"{len(post)} further sounds were decoded after it, with {speech_after:.0f} ms of speech-like sound"]
    else:
        state = "BOUNDARY_UNCERTAIN"
        reasons = [f"{len(post)} further sounds were decoded after the sentence"]
        if repeat_end is not None:
            reasons.append(f"the final word '{words[final_wi].word}' seems to be said more than once; "
                           "the sentence is taken to end after the later one")
        if not evidence["final_word_complete"]:
            reasons.append(f"the final word '{words[final_wi].word}' was only partly decoded "
                           f"({evidence['final_word_decoded']} sounds)")
        if inside:
            reasons.append(f"{len(inside)} extra sounds were decoded inside the final word")
        if split_by_pause:
            reasons.append("the final word's decoded sounds are separated by a pause")
    defensible = cost_per_sound is not None and cost_per_sound <= MAX_DEFENSIBLE_COST
    if not defensible:
        state = "BOUNDARY_UNCERTAIN"
        reasons.append(f"the sentence itself was decoded too unclearly to place its end ({cost_per_sound:.2f} "
                       "differences per expected sound); no pronunciation feedback is shown")
    elif stretched:
        reasons.append("analysed as one recording, the sentence's alignment extended into the continued speech; "
                       "the sentence is analysed again on its own")

    # the cut: quietest point between the sentence's last decoded sound and the continuation
    hi = post[0][0] if post else duration
    lo = t_end + min(MIN_TAIL_MS, max(0.0, (hi - t_end) / 2.0))
    cut = _quietest_ms(act, lo, hi)
    if cut is None:
        cut = min(duration, max(t_end, (t_end + hi) / 2.0))
    cut = float(min(max(cut, t_end), duration))
    if post and hi - t_end < CLEAN_GAP_MS:
        reasons.append(f"the gap before the continuation is short ({hi - t_end:.0f} ms); the boundary is approximate")
    second = "overflow" if state == "TARGET_PLUS_OVERFLOW" else "uncertain"
    regions = [{"kind": "target", "start_ms": 0.0, "end_ms": cut}]
    if cut < duration:
        regions.append({"kind": second, "start_ms": cut, "end_ms": duration})
    for r in regions:
        r["speech_ms"] = _active_ms(act, r["start_ms"], r["end_ms"])
    return {"version": BOUNDARY_VERSION, "state": state, "engine": engine, "reasons": reasons, "duration_ms": duration,
            "cut_ms": cut, "evidence": evidence, "thresholds": THRESHOLDS, "regions": regions, "defensible": defensible}


def map_to_capture(boundary: dict[str, Any], capture: dict[str, Any] | None) -> dict[str, Any]:
    """Add each region's sample range on the original capture run (exact, contiguous)."""
    if not capture or capture.get("start_sample") is None or capture.get("end_sample") is None \
            or not capture.get("sample_rate"):
        return boundary
    s0, s1, sr = capture["start_sample"], capture["end_sample"], capture["sample_rate"]
    for r in boundary["regions"]:
        a = s0 + int(round(r["start_ms"] * sr / 1000.0))
        b = s0 + int(round(r["end_ms"] * sr / 1000.0))
        r["capture_samples"] = [min(a, s1), min(b, s1)]
    boundary["regions"][-1]["capture_samples"][1] = s1  # the last region ends exactly where the attempt ends
    return boundary


def validate_boundary(b: dict[str, Any]) -> list[str]:
    """Invariants: regions tile the whole attempt; the target is never empty and contains the sentence."""
    issues = []
    if b.get("state") not in STATES:
        return [f"unknown boundary state {b.get('state')!r}"]
    if not b.get("reasons"):
        issues.append("a boundary decision without reasons")
    regions, dur = b.get("regions") or [], b.get("duration_ms")
    if not regions or not isinstance(dur, (int, float)):
        return issues + ["no regions"]
    if regions[0]["kind"] != "target" or regions[0]["start_ms"] != 0.0:
        issues.append("the first region must be the target, starting at 0")
    for r in regions:
        if r["kind"] not in REGION_KINDS:
            issues.append(f"unknown region kind {r['kind']!r}")
        if not (isinstance(r["start_ms"], (int, float)) and isinstance(r["end_ms"], (int, float))
                and math.isfinite(r["start_ms"]) and math.isfinite(r["end_ms"]) and r["start_ms"] <= r["end_ms"]):
            issues.append(f"{r['kind']} region has invalid times")
    for a, c in zip(regions, regions[1:]):
        if abs(a["end_ms"] - c["start_ms"]) > 1e-6:
            issues.append("regions are not contiguous (a gap or an overlap)")
    if abs(regions[-1]["end_ms"] - dur) > 1e-6:
        issues.append("regions do not cover the whole recording")
    if len({r["kind"] for r in regions}) != len(regions):
        issues.append("a region kind appears twice")
    target = regions[0]
    if target["end_ms"] <= 0:
        issues.append("the target region is empty")
    if abs(target["end_ms"] - b.get("cut_ms", -1)) > 1e-6:
        issues.append("the cut does not match the end of the target region")
    last = (b.get("evidence") or {}).get("last_target_ms")
    if last is not None and target["end_ms"] + 1e-6 < last:
        issues.append("the target region ends before the sentence's last decoded sound")
    kinds = [r["kind"] for r in regions]
    expected = {"TARGET_ONLY": [["target"]], "NO_RELIABLE_BOUNDARY": [["target"]],
                "TARGET_PLUS_OVERFLOW": [["target", "overflow"], ["target"]],
                "BOUNDARY_UNCERTAIN": [["target", "uncertain"], ["target"]]}[b["state"]]
    if kinds not in expected:
        issues.append(f"regions {kinds} do not fit state {b['state']}")
    if b["state"] in ("TARGET_ONLY", "NO_RELIABLE_BOUNDARY") and abs(target["end_ms"] - dur) > 1e-6:
        issues.append(f"{b['state']} must keep the whole recording as the target")
    caps = [r.get("capture_samples") for r in regions]
    if any(c is not None for c in caps):
        if any(c is None or c[0] > c[1] for c in caps):
            issues.append("capture sample ranges are missing or reversed")
        elif any(x[1] != y[0] for x, y in zip(caps, caps[1:])):
            issues.append("capture sample ranges are not contiguous")
    return issues


# ----------------------------------------------------------------------
# Target-region analysis support
# ----------------------------------------------------------------------

def target_wav_bytes(analysis_wav: bytes, cut_ms: float) -> tuple[bytes, int]:
    """The analysis WAV's first `cut_ms`, sample-exact (PCM copied, never re-encoded). Returns (wav, samples)."""
    import io
    import wave

    with wave.open(io.BytesIO(analysis_wav), "rb") as w:
        params, total = w.getparams(), w.getnframes()
        n = int(round(cut_ms * params.framerate / 1000.0))
        if not 0 < n <= total:
            raise ValueError(f"target region of {n} samples does not fit a recording of {total}")
        frames = w.readframes(n)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        w.writeframes(frames)
    return out.getvalue(), n


def check_target_analysis(boundary: dict[str, Any], target_result: PronunciationResult,
                          target_samples: np.ndarray, sample_rate: int) -> dict[str, Any]:
    """After the sentence is analysed on its own: is it still consistent with a clean boundary?

    TARGET_PLUS_OVERFLOW becomes BOUNDARY_UNCERTAIN when the target region alone still looks
    like it contains continued speech, or when the final word is decoded less completely than in
    the whole recording (the cut may have clipped it). Nothing is re-cut: uncertainty is reported.
    """
    again = detect_boundary(target_result, target_samples, sample_rate)
    out = {"state": again["state"], "reasons": again["reasons"]}
    before = (boundary.get("evidence") or {}).get("final_word_decoded")
    after = (again.get("evidence") or {}).get("final_word_decoded")
    out |= {"final_word_decoded_whole": before, "final_word_decoded_target": after}
    issues = []
    if again["state"] not in ("TARGET_ONLY",):
        issues.append("the sentence on its own still shows sound after its end")
    if before and after and int(after.split("/")[0]) < int(before.split("/")[0]):
        issues.append("the final word was decoded less completely in the sentence on its own")
    out["issues"] = issues
    if issues and boundary["state"] == "TARGET_PLUS_OVERFLOW":
        boundary["state"] = "BOUNDARY_UNCERTAIN"
        boundary["reasons"] = boundary["reasons"] + issues
        for r in boundary["regions"]:
            if r["kind"] == "overflow":
                r["kind"] = "uncertain"
    boundary["target_check"] = out
    return boundary


def compare_boundaries(used: dict[str, Any], own: dict[str, Any]) -> dict[str, Any]:
    """Another engine's own boundary assessment of the same attempt, kept as evidence (never merged)."""
    differs = used["state"] != own["state"] or abs(used["cut_ms"] - own["cut_ms"]) > DISAGREE_CUT_MS
    return {"engine": own["engine"], "state": own["state"], "cut_ms": own["cut_ms"], "reasons": own["reasons"],
            "differs": differs,
            "note": "Both local listening models share one acoustic model; agreement is not independent confirmation."}


# ----------------------------------------------------------------------
# Safety: never present feedback that continued speech may have contaminated
# ----------------------------------------------------------------------

WITHHELD_MESSAGE = "No pronunciation feedback: this sentence could not be separated reliably from the rest of the recording."


def continuation_plausible(result: PronunciationResult, detected_state: str | None = None) -> list[str]:
    """Timing-free reasons to think the attempt goes on after the sentence (empty if none).

    Used when no defensible boundary exists: the whole attempt must then not be analysed as the
    sentence. Evidence: the detector found continuation (before its boundary was rejected), or the
    whole-attempt alignment attached several extra sounds to the final word (R01–R20: at most 1).
    """
    reasons = []
    if detected_state in NEEDS_TARGET_ANALYSIS:
        reasons.append("continued speech was detected, but its boundary could not be placed consistently")
    if result.words:
        extra = sum(len(p.engine_evidence.get("extra_heard_phones") or []) for p in result.words[-1].phonemes)
        if extra >= MIN_OVERFLOW_PHONES:
            reasons.append(f"{extra} extra sounds were decoded at the end of the sentence")
    return reasons


def evidence_outside_target(view: dict[str, Any], result: PronunciationResult | None, end_ms: float,
                            tolerance_ms: float = 1.0) -> list[str]:
    """Every timestamp M4/M5 consume must lie inside the target region [0, end_ms]. Returns violations."""
    limit = end_ms + tolerance_ms
    issues: list[str] = []

    def check(where: str, window) -> None:
        if not window:
            return
        a, b = window[0], window[1]
        if a is None or b is None:
            return
        if not (a < limit and b <= limit):
            issues.append(f"{where} [{a:.0f}, {b:.0f}] ms lies outside the sentence (ends {end_ms:.0f} ms)")

    if result is not None:
        for w in result.words:
            for p in w.phonemes:
                check(f"'{w.word}' /{p.expected.phoneme}/ timing", (p.timing.start_ms, p.timing.end_ms))
                for x in (p.engine_evidence.get("extra_heard_phones") or []) + \
                         (p.engine_evidence.get("leading_heard_phones") or []):
                    check(f"'{w.word}' extra sound [{x.get('phone')}]", (x.get("start_ms"), x.get("end_ms")))
    for w in view.get("words") or []:
        check(f"word '{w.get('word')}' playback", w.get("play_ms"))
        for snd in w.get("sounds") or []:
            check(f"'{w.get('word')}' sound /{snd.get('expected')}/", snd.get("span_ms"))
            check(f"'{w.get('word')}' sound playback", snd.get("play_ms"))
    for o in (view.get("coach") or {}).get("observations") or []:
        for key in ("span_ms", "play_ms", "word_play_ms"):
            check(f"M4 observation {o.get('id')} {key}", o.get(key))
    for c in (view.get("reduction") or {}).get("candidates") or []:
        for key in ("span_ms", "play_ms", "word_play_ms"):
            check(f"M5 candidate {c.get('id')} {key}", (c.get("where") or {}).get(key))
    return issues
