// Unit tests for the M12 capture core (reader-core.js), run with node.
"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const R = require(path.join(__dirname, "../../../src/pronunciation_lab/app/static/reader-core.js"));

let n = 0;
const tests = [];
function test(name, fn) { tests.push([name, fn]); }

// A controller fed with a deterministic ramp so every sample value equals its index (mod 2^20).
function rig(opts = {}) {
  const deferred = [];
  const finalized = [];
  const started = [];
  const c = new R.CaptureController({
    sessionId: "s".repeat(32), defer: (fn) => deferred.push(fn),
    onFinalized: (a, s) => finalized.push([a, s]), onStarted: (a) => started.push(a), ...opts,
  });
  c.requesting();
  c.armed(opts.rate || 48000);
  let index = 0;
  const feed = (frames, q = 128) => {
    for (let done = 0; done < frames; done += q) {
      const d = new Float32Array(Math.min(q, frames - done));
      for (let i = 0; i < d.length; i++) d[i] = ((index + i) % 1048576) / 1048576;
      c.push(index, d);
      index += d.length;
    }
  };
  const flush = () => { while (deferred.length) deferred.shift()(); };
  return { c, feed, flush, finalized, started, deferred };
}

const owned = (s, a) => s.every((v, i) => Math.abs(v - ((a.startSample + i) % 1048576) / 1048576) < 1e-9);

test("selection cuts contiguously: A=[start,cut), B=[cut,…)", () => {
  const { c, feed, flush, finalized } = rig();
  feed(1000);
  const { started: a } = c.select("seg:0001", "One.");
  assert.equal(a.startSample, 1000);
  feed(48000);
  const { finalized: fa, started: b } = c.select("seg:0002", "Two.");
  assert.equal(fa, a);
  assert.equal(a.endSample, 49000);
  assert.equal(b.startSample, 49000);             // no gap, no overlap
  assert.equal(a.endReason, "switched");
  feed(24000);
  c.pause();
  flush();
  assert.equal(finalized.length, 2);
  const [[x, sx], [y, sy]] = finalized;
  assert.equal(sx.length, 48000);
  assert.equal(sy.length, 24000);
  assert.ok(owned(sx, x) && owned(sy, y));        // every sample is exactly the one at its index
});

test("the cut does no work: slicing is deferred and samples arriving later belong to the new attempt", () => {
  const { c, feed, deferred, flush, finalized } = rig();
  c.select("a", "A");
  feed(512);
  c.select("b", "B");
  assert.equal(finalized.length, 0);              // nothing encoded synchronously
  assert.equal(deferred.length, 1);
  feed(512);                                      // arrives after the click
  flush();
  assert.equal(finalized[0][1].length, 512);
  assert.equal(c.current.segmentId, "b");
  c.pause();
  flush();
  assert.equal(finalized[1][1].length, 512);
});

test("rapid switching and double selection", () => {
  const { c, feed, flush, finalized } = rig();
  c.select("a", "A");
  feed(256);
  const same = c.select("a", "A");                // selecting the sentence already recording is a no-op
  assert.deepEqual(same, { finalized: null, started: null });
  c.select("b", "B");
  c.select("c", "C");                             // zero-length attempt for b: kept, never dropped
  feed(128);
  c.select("a", "A");
  c.stop();
  flush();
  const lens = finalized.map(([a, s]) => [a.segmentId, s.length, a.localNumber]);
  assert.deepEqual(lens, [["a", 256, 1], ["b", 0, 1], ["c", 128, 1], ["a", 0, 2]]);
  for (let i = 1; i < finalized.length; i++) assert.equal(finalized[i][0].startSample, finalized[i - 1][0].endSample);
});

test("attempts tile the run: union of owned intervals = captured audio while recording", () => {
  const { c, feed, flush, finalized } = rig();
  const segs = ["a", "b", "c", "d", "e"];
  feed(300);
  let t = 300;
  c.select(segs[0], "x");
  for (let i = 1; i < 40; i++) {
    const frames = 50 + ((i * 7919) % 3000);
    feed(frames);
    t += frames;
    c.select(segs[i % segs.length], "x");
  }
  feed(777);
  t += 777;
  c.pause();
  flush();
  assert.equal(finalized[0][0].startSample, 300);
  assert.equal(finalized.at(-1)[0].endSample, t);
  let total = 0;
  for (let i = 0; i < finalized.length; i++) {
    const [a, s] = finalized[i];
    assert.equal(s.length, a.endSample - a.startSample);
    assert.ok(owned(s, a));
    if (i) assert.equal(a.startSample, finalized[i - 1][0].endSample);
    total += s.length;
  }
  assert.equal(total, t - 300);
});

test("paused audio belongs to no attempt and is released", () => {
  const { c, feed, flush, finalized } = rig();
  c.select("a", "A");
  feed(1000);
  c.pause();
  feed(5000);                                     // not recorded
  c.select("b", "B");
  assert.equal(c.current.startSample, 6000);
  feed(1000);
  c.pause();
  flush();
  assert.deepEqual(finalized.map(([a]) => [a.startSample, a.endSample, a.endReason]), [[0, 1000, "paused"], [6000, 7000, "paused"]]);
  assert.equal(c.ring.retainedSamples() <= 128, true);
});

test("ring memory is bounded by the open attempt", () => {
  const { c, feed, flush } = rig();
  c.select("a", "A");
  for (let i = 0; i < 50; i++) {
    feed(4800);
    c.select(i % 2 ? "a" : "b", "x");
    flush();
    assert.ok(c.ring.retainedSamples() <= 4800 + 128, c.ring.retainedSamples());
  }
});

test("the 60-second limit finalises exactly at the limit and stays armed", () => {
  const { c, feed, flush, finalized } = rig({ rate: 8000 });
  c.select("a", "A");
  feed(8000 * 60 + 1000);
  flush();
  assert.equal(c.state, "ARMED");
  const [[a, s]] = finalized;
  assert.equal(a.endReason, "limit_reached");
  assert.equal(s.length, 8000 * 60);
  assert.ok(owned(s, a));
});

test("stop releases; device loss keeps what was recorded", () => {
  const { c, feed, flush, finalized } = rig();
  c.select("a", "A");
  feed(2000);
  const a = c.deviceLost();
  flush();
  assert.equal(c.state, "RELEASED");
  assert.equal(a.endReason, "interrupted");
  assert.equal(finalized[0][1].length, 2000);
  assert.throws(() => c.select("b", "B"));        // must re-arm (RESUME re-acquires the microphone)
  c.requesting();
  c.armed(48000);
  const { started } = c.select("b", "B");
  assert.equal(started.startSample, 0);           // a new run restarts sample indices
  assert.notEqual(started.runId, a.runId);
});

test("capture state machine: illegal transitions throw", () => {
  const c = new R.CaptureController({ sessionId: "x" });
  assert.throws(() => c.select("a", "A"));
  assert.throws(() => c.armed(48000));            // NO_PERMISSION → ARMED needs REQUESTING
  c.requesting();
  c.denied();
  assert.equal(c.state, "DENIED");
  assert.throws(() => c.select("a", "A"));
  c.requesting();
  c.armed(48000);
  assert.equal(c.finalizeCurrent(), null);
});

test("non-contiguous worklet audio is refused", () => {
  const r = new R.PcmRing();
  r.append(0, new Float32Array(128));
  assert.throws(() => r.append(256, new Float32Array(128)));
  r.releaseBefore(128);
  assert.throws(() => r.slice(0, 64));            // released samples cannot be re-sliced
});

test("WAV encoding: header, sample count, clipping", () => {
  const s = new Float32Array([0, 0.5, -0.5, 1, -1, 2, -2]);
  const buf = Buffer.from(R.encodeWav(s, 48000));
  assert.equal(buf.toString("ascii", 0, 4), "RIFF");
  assert.equal(buf.readUInt32LE(4), 36 + s.length * 2);
  assert.equal(buf.toString("ascii", 8, 12), "WAVE");
  assert.equal(buf.readUInt16LE(20), 1);          // PCM
  assert.equal(buf.readUInt16LE(22), 1);          // mono
  assert.equal(buf.readUInt32LE(24), 48000);
  assert.equal(buf.readUInt16LE(34), 16);
  assert.equal(buf.readUInt32LE(40), s.length * 2);
  const vals = Array.from({ length: s.length }, (_, i) => buf.readInt16LE(44 + i * 2));
  assert.deepEqual(vals, [0, 16384, -16384, 32767, -32768, 32767, -32768]);
  assert.equal(R.encodeWav(new Float32Array(0), 16000).byteLength, 44);
});

test("capture header carries the full ownership", () => {
  const h = JSON.parse(R.captureHeader({ segmentId: "s:0001", runId: "r", sampleRate: 48000, startSample: 5,
    endSample: 9, endReason: "switched", wallClockStart: 1, wallClockEnd: 2 }));
  assert.deepEqual(h, { segment_id: "s:0001", run_id: "r", sample_rate: 48000, start_sample: 5, end_sample: 9,
    end_reason: "switched", wall_clock_start: 1, wall_clock_end: 2 });
});

test("uuidHex is 32 lower-case hex, version 4", () => {
  const ids = new Set();
  for (let i = 0; i < 200; i++) {
    const u = R.uuidHex();
    assert.match(u, /^[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}$/);
    ids.add(u);
  }
  assert.equal(ids.size, 200);
});

const tick = () => new Promise((r) => setImmediate(r));

test("upload queue: success, retry with backoff, permanent failure kept, manual retry", async () => {
  const calls = [];
  const timers = [];
  let fail = { a: 2, b: 99, c: 0 };
  const q = new R.UploadQueue({
    concurrency: 2, backoffMs: [10, 20, 30],
    schedule: (fn) => timers.push(fn),
    send: (item) => {
      calls.push(item.id);
      if (fail[item.id] > 0) { fail[item.id]--; return Promise.reject(item.id === "b" && fail.b > 90
        ? Object.assign(new Error("bad audio"), { retryable: false }) : new Error("network")); }
      return Promise.resolve({ ok: item.id });
    },
  });
  q.add({ id: "a" }); q.add({ id: "b" }); q.add({ id: "c" });
  for (let i = 0; i < 10; i++) { await tick(); while (timers.length) timers.shift()(); }
  assert.deepEqual([...q.done.keys()].sort(), ["a", "c"]);
  assert.equal(q.failed.has("b"), true);          // not retryable: kept, not dropped
  assert.equal(calls.filter((x) => x === "a").length, 3);
  fail.b = 0;
  assert.equal(q.retry("b"), true);
  for (let i = 0; i < 5; i++) await tick();
  assert.equal(q.done.has("b"), true);
  assert.deepEqual(q.status(), { waiting: 0, uploading: 0, failed: 0, done: 3 });
});

test("upload queue gives up after the backoff schedule but keeps the item", async () => {
  const timers = [];
  const q = new R.UploadQueue({ backoffMs: [1, 1], schedule: (fn) => timers.push(fn),
    send: () => Promise.reject(new Error("offline")) });
  q.add({ id: "x" });
  for (let i = 0; i < 10; i++) { await tick(); while (timers.length) timers.shift()(); }
  assert.equal(q.failed.get("x").error, "offline");
});

test("formatClock", () => {
  assert.equal(R.formatClock(0), "0:00");
  assert.equal(R.formatClock(65.9), "1:05");
});

(async () => {
  for (const [name, fn] of tests) {
    try { await fn(); n += 1; } catch (e) { console.error("FAIL", name, e); process.exit(1); }
  }
  console.log(`ok ${n} tests`);
})();
