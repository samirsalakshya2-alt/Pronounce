// M12 reader — pure capture and upload logic (no DOM; unit-tested with node).
//
// Audio ownership: one continuous capture run is cut into attempts at sample
// indices. An attempt owns [startSample, endSample) of its run; consecutive
// attempts share the cut index, so there is no gap and no overlap. A cut is a
// synchronous read of the number of samples received so far — selecting the
// next sentence never waits for encoding, upload, the server or analysis.
// Samples received while no attempt is open (paused / armed) belong to no one
// and are discarded.
"use strict";

const LIMIT_SECONDS = 60;          // = the server's MAX_DURATION_MS
const MIN_SECONDS = 0.3;           // = the server's MIN_DURATION_MS (shorter attempts are kept as TOO_SHORT)

function uuidHex() {
  const b = new Uint8Array(16);
  (globalThis.crypto || require("node:crypto").webcrypto).getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

// --- PCM ring -----------------------------------------------------------------------
class PcmRing {
  constructor() { this.chunks = []; this.end = 0; this.dropped = 0; }

  append(start, data) {
    if (start !== this.end) throw new Error(`non-contiguous audio: expected ${this.end}, got ${start}`);
    this.chunks.push({ start, data });
    this.end = start + data.length;
  }

  /** Copy of samples [a, b). Throws if any requested sample was already released. */
  slice(a, b) {
    if (b < a) throw new Error("reversed slice");
    const out = new Float32Array(b - a);
    let filled = 0;
    for (const c of this.chunks) {
      const s = Math.max(a, c.start), e = Math.min(b, c.start + c.data.length);
      if (e > s) { out.set(c.data.subarray(s - c.start, e - c.start), s - a); filled += e - s; }
    }
    if (filled !== b - a) throw new Error(`samples [${a}, ${b}) not all available`);
    return out;
  }

  /** Free every chunk that ends at or before `index`. */
  releaseBefore(index) {
    while (this.chunks.length && this.chunks[0].start + this.chunks[0].data.length <= index) {
      this.dropped += this.chunks.shift().data.length;
    }
  }

  retainedSamples() { return this.chunks.reduce((n, c) => n + c.data.length, 0); }
}

// --- capture state machine ---------------------------------------------------------
const CAPTURE_TRANSITIONS = {
  NO_PERMISSION: ["REQUESTING"],
  REQUESTING: ["ARMED", "DENIED"],
  DENIED: ["REQUESTING"],
  ARMED: ["CAPTURING", "RELEASED"],
  CAPTURING: ["CAPTURING", "ARMED", "RELEASED"],
  RELEASED: ["REQUESTING"],
};

/**
 * Owns attempt boundaries for one session. `onFinalized(attempt, samples)` and
 * `onStarted(attempt)` are called after the synchronous state change; the
 * finalised samples are sliced in a deferred task so the cut itself does no work.
 */
class CaptureController {
  constructor({ sessionId, sampleRate = 48000, onStarted, onFinalized, defer, limitSeconds = LIMIT_SECONDS }) {
    this.sessionId = sessionId;
    this.sampleRate = sampleRate;
    this.state = "NO_PERMISSION";
    this.runId = null;
    this.received = 0;
    this.ring = new PcmRing();
    this.current = null;      // the open attempt
    this.pending = [];        // finalised attempts whose samples are not yet sliced
    this.attempts = [];       // every attempt of this page, in order
    this.onStarted = onStarted || (() => {});
    this.onFinalized = onFinalized || (() => {});
    this.defer = defer || ((fn) => setTimeout(fn, 0));
    this.limitSeconds = limitSeconds;
    this.limitSamples = Math.round(limitSeconds * sampleRate);
    this.attemptNumbers = {};
  }

  _to(next) {
    if (!CAPTURE_TRANSITIONS[this.state].includes(next)) throw new Error(`capture ${this.state} → ${next} is not allowed`);
    this.state = next;
  }

  requesting() { this._to("REQUESTING"); }
  denied() { this._to("DENIED"); }

  /** A new continuous run (microphone stream) is ready; sample indices restart at 0. */
  armed(sampleRate) {
    this._to("ARMED");
    this.runId = uuidHex();
    this.sampleRate = sampleRate;
    this.limitSamples = Math.round(this.limitSeconds * sampleRate);
    this.received = 0;
    this.ring = new PcmRing();
  }

  /** Audio from the worklet, in order. */
  push(start, data) {
    this.ring.append(start, data);
    this.received = start + data.length;
    if (this.current && this.received - this.current.startSample >= this.limitSamples) {
      this._finalize(this.current.startSample + this.limitSamples, "limit_reached");
      this._to("ARMED");
    }
    this._releaseUnowned();
  }

  _releaseUnowned() {
    const keep = Math.min(this.current ? this.current.startSample : this.received,
      ...this.pending.map((a) => a.startSample));
    this.ring.releaseBefore(keep);
  }

  _finalize(cut, reason) {
    const a = this.current;
    a.endSample = cut;
    a.endReason = reason;
    a.wallClockEnd = Date.now();
    a.status = "FINALIZING";
    this.current = null;
    this.pending.push(a);
    this.defer(() => this._deliver(a));
    return a;
  }

  _deliver(a) {
    const samples = this.ring.slice(a.startSample, a.endSample);
    this.pending = this.pending.filter((p) => p !== a);
    this._releaseUnowned();
    a.status = "FINALIZED";
    this.onFinalized(a, samples);
  }

  /**
   * Select a segment: start reading it. Synchronous. If another segment is being
   * recorded, its attempt ends exactly where the new one begins.
   * Returns {finalized, started}; selecting the segment already recording is a no-op.
   */
  select(segmentId, targetText) {
    if (this.state !== "ARMED" && this.state !== "CAPTURING") throw new Error(`cannot record while ${this.state}`);
    if (this.current && this.current.segmentId === segmentId) return { finalized: null, started: null };
    const cut = this.received;
    const finalized = this.current ? this._finalize(cut, "switched") : null;
    const n = (this.attemptNumbers[segmentId] || 0) + 1;
    this.attemptNumbers[segmentId] = n;
    const started = {
      attemptId: uuidHex(), sessionId: this.sessionId, segmentId, targetText, runId: this.runId,
      sampleRate: this.sampleRate, startSample: cut, endSample: null, endReason: null,
      wallClockStart: Date.now(), wallClockEnd: null, localNumber: n, status: "CAPTURING",
    };
    this.current = started;
    this.attempts.push(started);
    this.state = "CAPTURING";
    this.onStarted(started);
    return { finalized, started };
  }

  /** Close the open attempt; stay armed (FINALIZE CURRENT and PAUSE). */
  finalizeCurrent(reason = "finalized") {
    if (!this.current) return null;
    const a = this._finalize(this.received, reason);
    this._to("ARMED");
    return a;
  }

  pause() { return this.finalizeCurrent("paused"); }

  /** Close the open attempt and release the microphone (STOP). The caller stops the tracks. */
  stop(reason = "stopped") {
    const a = this.current ? this._finalize(this.received, reason) : null;
    if (this.state !== "RELEASED") this._to("RELEASED");
    return a;
  }

  /** The device went away (track ended): keep what was recorded. */
  deviceLost() { return this.stop("interrupted"); }

  elapsedSeconds() {
    return this.current ? (this.received - this.current.startSample) / this.sampleRate : 0;
  }
}

// --- WAV encoding --------------------------------------------------------------------
/** 16-bit PCM mono WAV of Float32 samples in [-1, 1] (clipped). */
function encodeWav(samples, sampleRate) {
  const n = samples.length;
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE");
  str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff), true);
  }
  return buf;
}

function rms(samples) {
  let s = 0;
  for (let i = 0; i < samples.length; i++) s += samples[i] * samples[i];
  return samples.length ? Math.sqrt(s / samples.length) : 0;
}

// --- upload queue ----------------------------------------------------------------------
/**
 * Sends finalised attempts in the background. `send(item)` returns a promise; a
 * rejection with `retryable !== false` is retried with backoff. Nothing is dropped:
 * a permanently failed item stays in `failed` until `retry(id)`.
 */
class UploadQueue {
  constructor({ send, onChange, concurrency = 2, backoffMs = [500, 1000, 2000, 4000], schedule }) {
    this.send = send;
    this.onChange = onChange || (() => {});
    this.concurrency = concurrency;
    this.backoffMs = backoffMs;
    this.schedule = schedule || ((fn, ms) => setTimeout(fn, ms));
    this.waiting = [];
    this.active = new Map();
    this.done = new Map();
    this.failed = new Map();
  }

  add(item) { this.waiting.push({ ...item, tries: 0 }); this._pump(); this.onChange(this.status()); }

  status() {
    return { waiting: this.waiting.length, uploading: this.active.size, failed: this.failed.size, done: this.done.size };
  }

  retry(id) {
    const item = this.failed.get(id);
    if (!item) return false;
    this.failed.delete(id);
    item.tries = 0;
    this.waiting.push(item);
    this._pump();
    this.onChange(this.status());
    return true;
  }

  _pump() {
    while (this.active.size < this.concurrency && this.waiting.length) {
      const item = this.waiting.shift();
      this.active.set(item.id, item);
      item.tries += 1;
      Promise.resolve().then(() => this.send(item)).then((res) => {
        this.active.delete(item.id);
        this.done.set(item.id, res);
        this._pump();
        this.onChange(this.status(), item, res);
      }, (err) => {
        this.active.delete(item.id);
        const retryable = !(err && err.retryable === false);
        if (retryable && item.tries <= this.backoffMs.length) {
          this.schedule(() => { this.waiting.push(item); this._pump(); this.onChange(this.status()); },
            this.backoffMs[item.tries - 1]);
        } else {
          item.error = (err && err.message) || String(err);
          this.failed.set(item.id, item);
        }
        this._pump();
        this.onChange(this.status(), item, null, err);
      });
    }
  }
}

function captureHeader(a) {
  return JSON.stringify({
    segment_id: a.segmentId, run_id: a.runId, sample_rate: a.sampleRate, start_sample: a.startSample,
    end_sample: a.endSample, end_reason: a.endReason, wall_clock_start: a.wallClockStart, wall_clock_end: a.wallClockEnd,
  });
}

function formatClock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

if (typeof module !== "undefined") {
  module.exports = {
    LIMIT_SECONDS, MIN_SECONDS, uuidHex, PcmRing, CAPTURE_TRANSITIONS, CaptureController, encodeWav, rms,
    UploadQueue, captureHeader, formatClock,
  };
}
