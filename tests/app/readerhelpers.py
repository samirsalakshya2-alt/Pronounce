"""Shared helpers for the M12 reader tests (fixtures live in conftest.py)."""

import io
import threading
import time
import wave

from benchmark_fakes import FakeEngine

from pronunciation_lab.reader import model as M

TEXT = "The first sentence is here. A second one follows. And a third."


class ScriptedEngine(FakeEngine):
    """A fake engine that can block, fail, and records how many analyses overlap."""

    tracker = {"in_flight": 0, "max": 0, "lock": threading.Lock()}

    def __init__(self, name):
        super().__init__(name)
        self.gate = threading.Event()
        self.gate.set()
        self.fail = False
        self.delay = 0.0
        self.texts = []

    def analyze(self, audio_path, expected_text, *, recording_id, original_path=None):
        t = ScriptedEngine.tracker
        with t["lock"]:
            t["in_flight"] += 1
            t["max"] = max(t["max"], t["in_flight"])
        try:
            self.gate.wait(10)
            time.sleep(self.delay)
            self.texts.append(expected_text)
            if self.fail:
                raise RuntimeError("engine exploded")
            return super().analyze(audio_path, expected_text, recording_id=recording_id, original_path=original_path)
        finally:
            with t["lock"]:
                t["in_flight"] -= 1


def wav48(seconds=1.0, rate=48000, channels=1, width=2):
    n = int(seconds * rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        import math
        frames = bytearray()
        for i in range(n):
            v = math.sin(2 * math.pi * 220 * i / rate)
            sample = (int(8000 * v).to_bytes(2, "little", signed=True) if width == 2
                      else (128 + int(100 * v)).to_bytes(1, "little", signed=False))  # 8-bit WAV is unsigned
            for _ in range(channels):
                frames += sample
        w.writeframes(bytes(frames))
    return buf.getvalue(), n



def new_session(reader, text=TEXT, engine=None):
    art = reader.create_article(text, "Test article")
    sid = M.new_id()
    snap = reader.create_session(sid, art["id"], engine)
    return sid, art, snap


def capture(segment_id, start, n, rate=48000, run_id=None, reason="switched"):
    return {"segment_id": segment_id, "run_id": run_id or RUN, "sample_rate": rate, "start_sample": start,
            "end_sample": start + n, "end_reason": reason}


RUN = M.new_id()


def record(reader, sid, seg, start=0, seconds=1.0, aid=None):
    aid = aid or M.new_id()
    data, n = wav48(seconds)
    reader.start_attempt(sid, aid, {"segment_id": seg["id"], "run_id": RUN, "sample_rate": 48000, "start_sample": start})
    out = reader.upload_audio(sid, aid, data, capture(seg["id"], start, n))
    return aid, out
