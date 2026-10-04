"""Synthetic attempts for the M8 fluency tests: a sentence laid out sound by sound, with pauses,
extra sounds and repeats where a test asks for them, and audio that has speech exactly there.

Decoded sounds are aligned to the sentence by the engines' own aligner (via m7helpers.make), so
extra sounds land where a real CTC engine's alignment would put them.
"""

from __future__ import annotations

import m7helpers as H

SR = H.SR
WORDS = [("think", "θ ɪ ŋ k"), ("about", "ə b aʊ t"), ("it", "ɪ t"), ("then", "ð ɛ n"), ("change", "tʃ eɪ n dʒ"),
         ("the", "ð ə"), ("plan", "p l æ n")]
TEXT = "Think about it, then change the plan."
PHONE_MS, IN_WORD_GAP, WORD_GAP = 60.0, 20.0, 60.0


def layout(events, start_ms=500.0, tail_ms=600.0):
    """events: ("w", i) a word · ("p", ms) a silent pause · ("x", "ʌ m") extra sounds · ("cut", i, k) the first k
    sounds of word i (a fragment). Returns decoded [(phone, start, end)], speech intervals, duration, marks."""
    t, decoded, speech, marks = start_ms, [], [], []
    for ev in events:
        if ev[0] == "p":
            t += ev[1] - WORD_GAP
            marks.append(("pause", t - ev[1] + WORD_GAP, t + WORD_GAP))
            continue
        phones = WORDS[ev[1]][1].split() if ev[0] == "w" else ev[1].split() if ev[0] == "x" else \
            WORDS[ev[1]][1].split()[:ev[2]]
        s0 = t
        for k, p in enumerate(phones):
            decoded.append((p, t, t + PHONE_MS))
            t += PHONE_MS + (IN_WORD_GAP if k < len(phones) - 1 else 0)
        speech.append((s0, t))
        marks.append((ev[0], s0, t))
        t += WORD_GAP
    return decoded, speech, t + tail_ms, marks


def build(events, *, noise_db=-65.0, speech_db=-20.0, engine="wav2vec2_raw", text=TEXT, words=WORDS, **kw):
    decoded, speech, dur, marks = layout(events, **kw)
    result = H.make(words, decoded, engine=engine, duration_ms=dur, text=text)
    audio = H.audio(dur, speech, noise_db=noise_db, speech_db=speech_db)
    return result, audio, marks


ALL = [("w", i) for i in range(len(WORDS))]
