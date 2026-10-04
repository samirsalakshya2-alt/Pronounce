"""Controlled M8 variants spliced from the benchmark recordings (generated at test time, never committed).

Word boundaries come from the frozen M2 analysis of each recording (its decoded sound timings). The
variants are synthetic: a spliced pause, repeat or vowel tells us whether the detector responds to that
construction — not how accurately real fillers, repetitions or false starts are detected.

    clean        the recording as is
    pause        a pause (the recording's own room tone) inserted inside a phrase
    filler       a sustained central vowel taken from the recording, between two pauses (an "uh"-like sound)
    word_rep     one word said again after a short pause
    phrase_rep   two words said again after a short pause
    restart      the beginning of the next word, a pause, then the word
    false_start  the beginning of the next word followed by part of another word, a pause, then the speech
    two_pauses   two pauses inserted inside phrases
    phrase_pause a pause inserted at the sentence's comma (a natural phrase boundary; R17–R20 only)
    slow, fast   the whole reading time-stretched (WSOLA: duration × 1.35 / × 0.75, pitch unchanged)
"""

from __future__ import annotations

import re

import numpy as np
import soundfile as sf
from apphelpers import DATA_DIR

from pronunciation_lab.app.ground_truth import load_frozen, manifest
from pronunciation_lab.benchmark.analysis import is_vowel

SR = 16000
CENTRAL = ("ʌ", "ə", "ɐ", "ɜː", "ɚ")


def _ms(x):
    return int(round(x * SR / 1000.0))


def _words(frozen):
    out = []
    for i, w in enumerate(frozen.words):
        ph = [p for p in w.phonemes if p.engine_evidence.get("operation") in ("match", "substitution")
              and p.timing.start_ms is not None]
        if ph:
            out.append({"i": i, "word": w.word, "start": ph[0].timing.start_ms, "end": ph[-1].timing.end_ms, "ph": ph})
    return out


def _room_tone(x, frozen, ms):
    """The recording's own background before speech starts, tiled to `ms`."""
    first = min(p.timing.start_ms for w in frozen.words for p in w.phonemes if p.timing.start_ms is not None)
    seg = x[: max(_ms(min(first - 50, 400)), _ms(60))]
    reps = int(np.ceil(_ms(ms) / max(len(seg), 1)))
    return np.tile(seg, reps)[: _ms(ms)]


def _tile(seg, ms, fade=_ms(25)):
    out = seg.copy()
    while len(out) < _ms(ms):
        ramp = np.linspace(0, 1, fade)
        head = out[-fade:] * (1 - ramp) + seg[:fade] * ramp
        out = np.concatenate([out[:-fade], head, seg[fade:]])
    return out[: _ms(ms)]


def wsola(x, factor, win_ms=40.0, tol_ms=10.0):
    """Time-stretch by `factor` (> 1 slower) without changing pitch: overlap-add of frames chosen to continue the
    previous frame's waveform (WSOLA). Test material only."""
    n, tol = _ms(win_ms), _ms(tol_ms)
    hs = n // 2
    ha = hs / factor
    w = np.hanning(n)
    out = np.zeros(int(len(x) * factor) + n)
    norm = np.zeros_like(out)
    prev, k = 0, 0
    while True:
        target = int(round(k * ha))
        if k == 0:
            s = 0
        else:
            ref = x[prev + hs:prev + hs + n]
            lo, hi = max(0, target - tol), min(len(x) - n, target + tol)
            if hi < lo or len(ref) < n:
                break
            s = lo + int(np.argmax(np.correlate(x[lo:hi + n], ref, "valid")))
        if s + n > len(x) or k * hs + n > len(out):
            break
        out[k * hs:k * hs + n] += x[s:s + n] * w
        norm[k * hs:k * hs + n] += w
        prev, k = s, k + 1
    end = (k - 1) * hs + n if k else 0
    return (out[:end] / np.maximum(norm[:end], 1e-3)).astype(np.float32)


def comma_boundary(frozen, text):
    """The word boundary at the sentence's comma: index into _words()."""
    ws = _words(frozen)
    toks = re.findall(r"[A-Za-z']+[^A-Za-z']*", text)
    after = {k for k, t in enumerate(toks) if "," in t}
    cands = [k for k in range(len(ws) - 1) if ws[k]["i"] in after and ws[k + 1]["i"] == ws[k]["i"] + 1]
    return cands[0] if cands else None


def boundary_in_phrase(rid, frozen, text):
    """A word boundary inside a phrase (no punctuation), near the middle: index into _words()."""
    ws = _words(frozen)
    toks = re.findall(r"[A-Za-z']+[^A-Za-z']*", text)
    punct_after = {k for k, t in enumerate(toks) if re.search(r"[,.;:!?]", t)}
    cands = [k for k in range(1, len(ws) - 2) if ws[k]["i"] not in punct_after and ws[k + 1]["i"] == ws[k]["i"] + 1]
    return min(cands, key=lambda k: abs(k - len(ws) / 2)) if cands else None


def variant(rid: str, kind: str, engine: str = "wav2vec2_raw"):
    """(audio, marks) for one variant; marks give where the inserted material is (ms, in the new audio)."""
    man = manifest(DATA_DIR)
    text = man[rid]["target_text"]
    x, sr = sf.read(DATA_DIR / "benchmark_wav" / f"{rid}.wav", dtype="float32")
    assert sr == SR
    frozen = load_frozen(rid, engine, DATA_DIR)
    if kind == "clean":
        return x, {"kind": kind}
    if kind in ("slow", "fast"):
        factor = 1.35 if kind == "slow" else 0.75
        return wsola(x, factor), {"kind": kind, "factor": factor}
    ws = _words(frozen)
    k = comma_boundary(frozen, text) if kind == "phrase_pause" else boundary_in_phrase(rid, frozen, text)
    if k is None:
        return None, None
    if kind == "two_pauses":
        toks = re.findall(r"[A-Za-z']+[^A-Za-z']*", text)
        punct = {i for i, t in enumerate(toks) if re.search(r"[,.;:!?]", t)}
        others = [j for j in range(1, len(ws) - 2) if abs(j - k) >= 2 and ws[j + 1]["i"] == ws[j]["i"] + 1
                  and ws[j]["i"] not in punct]
        if not others:
            return None, None
        j = min(others, key=lambda j: abs(j - k))
        cuts = sorted(_ms((ws[m]["end"] + ws[m + 1]["start"]) / 2) for m in (k, j))
        tone = _room_tone(x, frozen, 900)
        out = np.concatenate([x[:cuts[0]], tone, x[cuts[0]:cuts[1]], tone, x[cuts[1]:]])
        spans = [(cuts[0], cuts[0] + len(tone)), (cuts[1] + len(tone), cuts[1] + 2 * len(tone))]
        return out, {"kind": kind, "spans_ms": [(a * 1000.0 / SR, b * 1000.0 / SR) for a, b in spans]}
    a, b = ws[k], ws[k + 1]
    cut = _ms((a["end"] + b["start"]) / 2)          # between the two words
    head, tail = x[:cut], x[cut:]
    pause = lambda ms: _room_tone(x, frozen, ms)  # noqa: E731
    word = lambda w: x[_ms(w["start"] - 60):_ms(w["end"] + 60)]  # noqa: E731
    if kind in ("pause", "phrase_pause"):
        ins = pause(1000)
    elif kind == "filler":
        vowel = next((p for w in ws for p in w["ph"] if p.expected.phoneme in CENTRAL), None) or \
            next(p for w in ws for p in w["ph"] if is_vowel(p.expected.phoneme))
        c = (vowel.timing.start_ms + vowel.timing.end_ms) / 2
        # only the steady centre of the vowel (a wider window takes in its neighbouring consonants)
        ins = np.concatenate([pause(350), _tile(x[_ms(c - 35):_ms(c + 35)], 380), pause(350)])
    elif kind == "word_rep":
        ins = np.concatenate([pause(350), word(a)])
    elif kind == "phrase_rep":
        ins = np.concatenate([pause(350), x[_ms(ws[k - 1]["start"] - 60):_ms(a["end"] + 60)]])
    elif kind == "restart":
        part = b["ph"][: max(2, len(b["ph"]) // 2)]
        ins = np.concatenate([x[_ms(b["start"] - 40):_ms(part[-1].timing.end_ms + 30)], pause(400)])
    elif kind == "false_start":
        other = next(w for w in ws[::-1] if w["i"] not in (a["i"], b["i"]) and len(w["ph"]) >= 3)
        part = b["ph"][:2]
        ins = np.concatenate([x[_ms(b["start"] - 40):_ms(part[-1].timing.end_ms + 20)],
                              x[_ms(other["ph"][1].timing.start_ms - 20):_ms(other["ph"][-1].timing.end_ms + 40)], pause(400)])
    else:
        raise ValueError(kind)
    out = np.concatenate([head, ins, tail])
    return out, {"kind": kind, "start_ms": cut * 1000.0 / SR, "end_ms": (cut + len(ins)) * 1000.0 / SR,
                 "word_before": a["word"], "word_after": b["word"]}


KINDS = ("clean", "pause", "filler", "word_rep", "phrase_rep", "restart", "false_start")
EXTRA_KINDS = ("two_pauses", "phrase_pause", "slow", "fast")
EXPECTED = {"pause": {"PAUSE"}, "filler": {"FILLER"}, "word_rep": {"REPETITION"}, "phrase_rep": {"REPETITION"},
            "restart": {"RESTART", "REPETITION"}, "false_start": {"FALSE_START", "RESTART"}}
