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
console.log("ok reader-feedback tests");
