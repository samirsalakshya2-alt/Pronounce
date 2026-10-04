// Unit tests for the pure helpers of reader-feedback.js.
"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const F = require(path.join(__dirname, "../../../src/pronunciation_lab/app/static/reader-feedback.js"));

assert.equal(F.feedbackLine(null), "");
assert.equal(F.feedbackLine({ state: "ok", notice: 0, compare: 0 }), "Nothing stood out");
assert.equal(F.feedbackLine({ state: "ok", notice: 1, compare: 0 }), "1 thing to notice");
assert.equal(F.feedbackLine({ state: "ok", notice: 2, compare: 1 }), "2 things to notice · 1 to compare");
assert.equal(F.feedbackLine({ state: "no_speech", message: "No speech sounds were detected in this recording." }),
  "No speech sounds were detected in this recording.");

const a1 = { id: "a1", user_disposition: "kept" }, a2 = { id: "a2", user_disposition: "discarded" };
assert.equal(F.displayAttempt([]), null);
assert.equal(F.displayAttempt([a1, a2]), a1);            // a discarded attempt is not the one shown
assert.equal(F.displayAttempt([a2]), a2);                // unless it is the only one
assert.equal(F.displayAttempt([a2, { id: "a3" }]).id, "a3");

const jobs = { p1: { id: "p1", kind: "primary" }, c1: { id: "c1", kind: "comparison" }, p2: { id: "p2", kind: "primary" } };
assert.equal(F.primaryJob({ job_ids: ["p1", "c1"] }, jobs).id, "p1");
assert.equal(F.primaryJob({ job_ids: ["p1", "c1", "p2"] }, jobs).id, "p2");   // a retry supersedes
assert.equal(F.primaryJob({ job_ids: ["c1"] }, jobs), null);
for (const t of Object.values(F.TARGET_TEXT)) assert.ok(!/wrong|incorrect|score/i.test(t));
assert.equal(F.sentencesText([1]), "sentence 1");
assert.equal(F.sentencesText([1, 4]), "sentences 1, 4");
// M7: the boundary note
assert.equal(F.boundaryNote(null), null);
assert.equal(F.boundaryNote({ state: "TARGET_ONLY", regions: [{ kind: "target" }] }), null);
assert.equal(F.boundaryNote({ state: "NO_RELIABLE_BOUNDARY", regions: [{ kind: "target" }] }), null);
const over = F.boundaryNote({ state: "TARGET_PLUS_OVERFLOW",
  regions: [{ kind: "target", play: { play_ms: [0, 900] } }, { kind: "overflow", play: { play_ms: [900, 2000] } }] });
assert.equal(over.line, "Continued speech detected after this sentence.");
assert.equal(over.sub, "This continuation was not included in the pronunciation analysis.");
assert.deepEqual(over.after.play.play_ms, [900, 2000]);
assert.deepEqual(over.target.play.play_ms, [0, 900]);
const unc = F.boundaryNote({ state: "BOUNDARY_UNCERTAIN", regions: [{ kind: "target" }, { kind: "uncertain", play: {} }] });
assert.equal(unc.line, "Sentence boundary uncertain — some continued speech may not be included in this sentence's analysis.");
assert.equal(unc.after.kind, "uncertain");
// feedback withheld (no defensible boundary, continuation plausible): the uncertain wording, even without regions
const wh = F.boundaryNote({ state: "NO_RELIABLE_BOUNDARY", feedback_withheld: true, regions: [{ kind: "target" }] });
assert.equal(wh.state, "BOUNDARY_UNCERTAIN");
assert.equal(wh.line, F.BOUNDARY_TEXT.BOUNDARY_UNCERTAIN.line);
assert.equal(wh.after, null);
assert.equal(wh.withheld, true);
assert.equal(F.boundaryNote({ state: "TARGET_PLUS_OVERFLOW", feedback_withheld: true, regions: [] }).state, "BOUNDARY_UNCERTAIN");
for (const t of Object.values(F.BOUNDARY_TEXT)) assert.ok(!/wrong|incorrect|score|error|warning/i.test(t.line + t.sub));
const oe = { engine: "openpronounce", state: "TARGET_PLUS_OVERFLOW", cut_ms: 900, differs: false, note: "shared" };
const bj = { p: { kind: "primary", state: "SUCCEEDED", boundary: {} }, c: { kind: "comparison", state: "SUCCEEDED", boundary: { other_engine: oe } },
  q: { kind: "comparison", state: "RUNNING" } };
assert.equal(F.otherBoundary({ job_ids: ["p"] }, bj), null);              // not compared yet
assert.equal(F.otherBoundary({ job_ids: ["p", "q"] }, bj), null);         // comparison still running
assert.deepEqual(F.otherBoundary({ job_ids: ["p", "c"] }, bj), oe);
console.log("ok reader-feedback tests");
