"""Synthetic attempts for the M7 boundary tests: a sentence, what was decoded (with timing), and audio.

The decoded sounds are aligned to the sentence by the engines' own aligner (`ctc.align`), so the
operations and insertions have exactly the shape a real CTC engine produces — including the
whole-recording alignment that attaches continued speech to the final word.
"""

from __future__ import annotations

import numpy as np
from benchmark_fakes import FakeEngine

from pronunciation_lab.benchmark.ctc import align
from pronunciation_lab.benchmark.schema import (
    AcousticEvidence,
    EngineInfo,
    ExpectedPhoneme,
    ObservedPhoneme,
    PhonemeResult,
    ProcessingInfo,
    ProsodyEvidence,
    PronunciationResult,
    RecordingAudio,
    RecordingInfo,
    TargetInfo,
    TimingInfo,
    WordResult,
)

SR = 16000
MS_PER_FRAME = 20

# "Think about the three things that you want to change." (shortened) and its continuation
SENTENCE = [("think", "θ ɪ ŋ k"), ("about", "ə b aʊ t"), ("change", "tʃ eɪ n dʒ")]
CONTINUATION = "ð ə n ɛ k s t θ ɪ ŋ"  # "the next thing"


def heard(phones: str, start_ms: float, step_ms: float = 80.0, dur_ms: float = 60.0):
    """Decoded sounds every `step_ms` from `start_ms`, each lasting `dur_ms` (frame-aligned)."""
    out, t = [], start_ms
    for p in phones.split():
        out.append((p, t, t + dur_ms))
        t += step_ms
    return out


def make(words=SENTENCE, decoded=(), *, engine="wav2vec2_raw", status="ok", duration_ms=None):
    """A PronunciationResult as a CTC engine would assemble it from `decoded` [(phone, start_ms, end_ms)]."""
    groups = [p.split() for _, p in words]
    flat = [p for g in groups for p in g]
    hp = [d[0] for d in decoded]
    spans = [(int(round(d[1] / MS_PER_FRAME)), max(int(round(d[1] / MS_PER_FRAME)) + 1, int(round(d[2] / MS_PER_FRAME))))
             for d in decoded]
    pairs = align(flat, hp)
    matched, ins_after, last = {}, {}, -1
    for e, h in pairs:
        if e is not None and h is not None:
            matched[e] = h
            last = e
        elif e is not None:
            last = e
        else:
            ins_after.setdefault(last, []).append(h)

    def detail(h):
        f0, f1 = spans[h]
        return {"phone": hp[h], "confidence": 0.8, "frame_start": f0, "frame_end": f1,
                "start_ms": f0 * MS_PER_FRAME, "end_ms": f1 * MS_PER_FRAME}

    word_results, idx = [], 0
    for (word, _), g in zip(words, groups):
        phonemes = []
        for pos, ph in enumerate(g):
            h = matched.get(idx)
            if h is not None:
                f0, f1 = spans[h]
                op = "match" if hp[h] == ph else "substitution"
                timing = TimingInfo(start_ms=f0 * MS_PER_FRAME, end_ms=f1 * MS_PER_FRAME,
                                    duration_ms=(f1 - f0) * MS_PER_FRAME, source="engine", frame_start=f0, frame_end=f1)
                obs = ObservedPhoneme(top=hp[h], confidence=0.8, frame_start=f0, frame_end=f1)
            else:
                op, obs = "omission", ObservedPhoneme()
                timing = TimingInfo(start_ms=None, end_ms=None, source="derived")
            phonemes.append(PhonemeResult(
                position=pos, expected=ExpectedPhoneme(phoneme=ph, position=pos, source="test"), observed=obs,
                timing=timing, acoustic=AcousticEvidence(measured_on="analysis"), prosody=ProsodyEvidence(),
                engine_evidence={"operation": op, "expected_phone_posterior": 0.5 if op != "match" else 0.9,
                                 "extra_heard_phones": [detail(x) for x in ins_after.get(idx, [])]}))
            idx += 1
        word_results.append(WordResult(word=word, expected_phonemes=g, timing=TimingInfo(source="derived"),
                                       phonemes=phonemes, engine_evidence={}))
    dur = duration_ms if duration_ms is not None else (max([d[2] for d in decoded] or [0]) + 500)
    return PronunciationResult(
        status=status,
        recording=RecordingInfo(id="m7", audio=RecordingAudio(original_path="m7.wav", analysis_path="m7.wav", sample_rate_hz=SR,
                                                                 channels=1, duration_ms=dur),
                                target=TargetInfo(text=" ".join(w for w, _ in words))),
        engine=EngineInfo(name=engine, version="1", mode="local", model="test"),
        processing=ProcessingInfo(device="cpu"),
        phone_set="test",
        words=word_results,
        engine_evidence={"recognition": {"phones": hp, "confidences": [0.8] * len(hp), "frame_spans": spans},
                         "frame_clock": {"ms_per_frame": MS_PER_FRAME}},
    )


def audio(duration_ms, speech=(), *, noise_db=-65.0, speech_db=-20.0, seed=0):
    """Room noise with speech-level sound in each (start_ms, end_ms) interval."""
    rng = np.random.default_rng(seed)
    n = int(duration_ms * SR / 1000)
    x = rng.standard_normal(n) * 10 ** (noise_db / 20)
    for a, b in speech:
        i, j = int(a * SR / 1000), min(n, int(b * SR / 1000))
        t = np.arange(j - i) / SR
        x[i:j] += np.sin(2 * np.pi * 180 * t) * 10 ** (speech_db / 20) * np.sqrt(2)
    return x.astype(np.float32)


def sentence_decoded(start_ms=300.0):
    """The sentence decoded as expected: 12 sounds, 80 ms apart; returns (decoded, end_ms)."""
    d = heard(" ".join(p for _, p in SENTENCE), start_ms)
    return d, d[-1][2]


# ----------------------------------------------------------------------
# A fake engine that "hears" the sentence, and a continuation when the audio is long enough
# ----------------------------------------------------------------------

GAP_MS = 500.0


def attempt_audio(continued=True, tail_ms=400.0):
    """16 kHz audio: the sentence (speech energy where it is decoded), optionally continued speech."""
    d, end = sentence_decoded()
    speech = [(300, end)]
    dur = end + tail_ms
    if continued:
        c = heard(CONTINUATION, end + GAP_MS)
        speech.append((c[0][1], c[-1][2]))
        dur = c[-1][2] + tail_ms
    return audio(dur, speech)


def wav16(x):
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


class BoundaryEngine(FakeEngine):
    """Decodes the sentence; adds the continuation's sounds when the audio extends past it."""

    def __init__(self, name):
        super().__init__(name)
        self.calls = []  # (recording_id, duration_ms)
        self.fail_on = None  # recording-id suffix that fails, e.g. "-t" (the sentence-region pass)
        self.failed_result_on = None  # recording-id suffix answered with a failed result (no exception)
        self.texts = []
        self.dense = False  # the M7 manual-test failure: speech-dense, noisy, "…underresourced" + sentence 3

    def analyze(self, audio_path, expected_text, *, recording_id, original_path=None):
        import soundfile as sf

        x, sr = sf.read(str(audio_path), dtype="float32")
        dur = len(x) * 1000.0 / sr
        self.calls.append((recording_id, dur))
        self.texts.append(expected_text)
        if self.fail_on and recording_id.endswith(self.fail_on):
            raise RuntimeError("engine exploded on the sentence region")
        if self.dense:
            d, c = dense_decoded()
            words = DENSE_SENTENCE
        else:
            d, end = sentence_decoded()
            c = heard(CONTINUATION, end + GAP_MS)
            words = SENTENCE
        decoded = d + [s for s in c if s[2] <= dur]
        status = "failed" if self.failed_result_on and recording_id.endswith(self.failed_result_on) else "ok"
        return make(words, decoded, engine=self.name, duration_ms=dur, status=status)


# ----------------------------------------------------------------------
# The M7 manual-test failure, reproduced deterministically (no personal audio)
# ----------------------------------------------------------------------
# Sentence 2 ended at ~19.3 s with "…underresourced"; the reader went straight on into sentence 3
# (~20.0 → 25.6 s) in a noisy room: noise at -36 dBFS, speech around -27 dBFS, so the recording's
# noise-floor estimate came out at -36 dBFS and the speech itself looked inactive. Text written for
# the test; levels and timing are those measured on the failing attempt.

DENSE_SENTENCE = [("those", "ð oʊ z"), ("efforts", "ɛ f ɚ t s"), ("fail", "f eɪ l"), ("because", "b ɪ k ʌ z"),
                  ("they're", "ð ɛ ɹ"), ("underresourced", "ʌ n d ɚ ɹ ᵻ s ɔːɹ s t")]
DENSE_CONTINUATION = ("b ʌ t m ɛ n i ʌ v ð ə m oʊ s t ɪ m p æ k t f əl ɪ ɡ z æ m p əl z ʌ v k ə l æ b ɚ ɹ eɪ ʃ ə n")
DENSE_END_MS, DENSE_CONT_MS, DENSE_DURATION_MS = 19300.0, 20040.0, 25800.0
DENSE_NOISE_DB, DENSE_SPEECH_DB = -36.0, -27.0


def dense_decoded():
    n = sum(len(p.split()) for _, p in DENSE_SENTENCE)
    sentence = heard(" ".join(p for _, p in DENSE_SENTENCE), DENSE_END_MS - 60 - (n - 1) * 80.0, step_ms=80.0, dur_ms=20.0)
    k = len(DENSE_CONTINUATION.split())
    cont = heard(DENSE_CONTINUATION, DENSE_CONT_MS, step_ms=(25600.0 - 20 - DENSE_CONT_MS) / (k - 1), dur_ms=20.0)
    return sentence, cont


def dense_audio(duration_ms=DENSE_DURATION_MS, cont_db=DENSE_SPEECH_DB, noise_db=DENSE_NOISE_DB, cont=True):
    sentence, c = dense_decoded()
    x = audio(duration_ms, [(sentence[0][1], DENSE_END_MS)], noise_db=noise_db, speech_db=DENSE_SPEECH_DB)
    if cont and duration_ms > DENSE_CONT_MS:
        extra = audio(duration_ms, [(DENSE_CONT_MS, min(duration_ms, c[-1][2]))], noise_db=-200.0, speech_db=cont_db, seed=1)
        x = x + extra
    return x
