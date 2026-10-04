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
// M8: fluency wording
assert.equal(F.fluencyLine(null), "");
assert.equal(F.fluencyLine({ state: "boundary_withheld", notice: 0 }), "");
assert.equal(F.fluencyLine({ state: "ok", notice: 0 }), "");
assert.equal(F.fluencyLine({ state: "ok", notice: 1 }), "1 fluency thing to notice");
assert.equal(F.fluencyLine({ state: "ok", notice: 2 }), "2 fluency things to notice");
assert.equal(F.rateText({ rate_available: false, rate_unavailable_reason: "too short to measure a rate (2 syllables)" }),
  "Speaking rate: not measured — too short to measure a rate (2 syllables).");
assert.equal(F.rateText({ rate_available: true, speaking_rate: 2.94, articulation_rate: 3.51, pause_count: 1, pause_total_ms: 890 }),
  "Speaking rate: 2.9 syllables per second · 3.5 without pauses · 1 pause (0.9 s)");
const flv = { state: "ok", observations: [
  { id: "f1", type: "PAUSE", notice: false, observed: "0.69 s pause", context: { position: "at a phrase boundary", word_before: "it", word_after: "then" } },
  { id: "f2", type: "PAUSE", notice: true, observed: "0.89 s pause", context: { position: "between words inside a phrase", word_before: "then", word_after: "change" } },
  { id: "f3", type: "REPETITION", notice: true, observed: "'the' heard twice", context: { words: ["the"] } }] };
const it = F.fluencyItems(flv);
assert.deepEqual(it.notice.map((o) => o.id), ["f2", "f3"]);
assert.deepEqual(it.other.map((o) => o.id), ["f1"]);
assert.equal(F.fluencyItemText(flv.observations[1]), "0.89 s pause (between words inside a phrase, after “then”)");
assert.equal(F.fluencyItemText(flv.observations[2]), "'the' heard twice");
assert.deepEqual(F.fluencyItems({ state: "boundary_withheld", observations: [] }), { notice: [], other: [] });
for (const t of [F.fluencyLine({ state: "ok", notice: 3 }), ...Object.values(F.STRENGTH_TEXT)]) {
  assert.ok(!/score|%|wrong|fluent|rank/i.test(t), t);
}
// identity: distinct wording; Keep never unlocks a different sentence's feedback
assert.equal(F.TARGET_TEXT.MATCH, "Recording appears to match this sentence.");
assert.equal(F.TARGET_TEXT.MISMATCH, "This recording appears to contain a different sentence.");
assert.equal(F.TARGET_TEXT.AMBIGUOUS, "I couldn't confidently tell whether this recording is this sentence.");
assert.equal(F.TARGET_TEXT.TOO_SHORT, "Recording is too short to confirm the sentence.");
assert.equal(new Set(Object.values(F.TARGET_TEXT)).size, Object.keys(F.TARGET_TEXT).length);  // no catch-all
for (const t of [...Object.values(F.TARGET_TEXT), ...Object.values(F.NOTE_IDENTITY)]) assert.ok(!/may not match|wrong|incorrect|score/i.test(t), t);
const st = (identity, feedback, message) => ({ identity, feedback, message });
let v = F.identityOf({ user_disposition: null, status: st("AMBIGUOUS", "shown", "m") }, null);
assert.deepEqual([v.identity, v.kept, v.needsDecision, v.feedback], ["AMBIGUOUS", false, true, "shown"]);
v = F.identityOf({ user_disposition: "kept", status: st("AMBIGUOUS", "shown", "m") }, null);
assert.deepEqual([v.kept, v.needsDecision], [true, false]);
v = F.identityOf({ user_disposition: "kept", status: st("MISMATCH", "hidden_identity", "m") }, null);
assert.equal(v.feedback, "hidden_identity");  // kept, still hidden
v = F.identityOf({ user_disposition: null, status: st("MATCH", "withheld_boundary",
  "Your reading appears to match this sentence, but I couldn't safely determine where it ended.") }, null);
assert.ok(!v.needsDecision && /appears to match/.test(v.message));
v = F.identityOf({ user_disposition: "rerecord_requested", status: st("MISMATCH", "hidden_identity", "m") }, null);
assert.equal(v.needsDecision, false);
// without a server status (older snapshots): from the job
v = F.identityOf({ user_disposition: null }, { target_confirmation: { state: "MISMATCH" } });
assert.deepEqual([v.identity, v.feedback], ["MISMATCH", "hidden_identity"]);
// the reading summary: recorded vs feedback, never "0 of N included" for a session that was read
assert.deepEqual(F.readingLines({ sentences: 5, recorded: 5, identified: 3, uncertain: 2, different: 0,
  feedback_included: 1, feedback_withheld: 2, awaiting_decision: 2 }),
  ["5 of 5 sentences recorded · 3 identified · 2 uncertain",
   "1 sentence in the feedback below · 2 withheld because the sentence boundary was uncertain · 2 waiting for you to keep or re-record"]);
assert.deepEqual(F.readingLines({ sentences: 4, recorded: 4, identified: 4, uncertain: 0, different: 0,
  feedback_included: 4, feedback_withheld: 0, awaiting_decision: 0 }),
  ["4 of 4 sentences recorded · 4 identified", "4 sentences in the feedback below"]);
assert.equal(F.readingLines({ sentences: 2, recorded: 1, identified: 1, uncertain: 0, different: 1,
  feedback_included: 1, feedback_withheld: 0, awaiting_decision: 1 })[0], "1 of 2 sentences recorded · 1 identified · 1 seems to be a different sentence");
assert.equal(F.identityOf({ user_disposition: null, status: { identity: "LIKELY_MATCH", feedback: "shown" } }, null).needsDecision, false);
// boundary confidence vs analysis confidence: "could not be established" only for a genuinely unknown end
assert.deepEqual(F.readingLines({ sentences: 3, recorded: 3, identified: 3, uncertain: 0, different: 0,
  feedback_included: 2, feedback_low_confidence: 1, feedback_withheld: 0, feedback_withheld_containment: 1, awaiting_decision: 0 })[1],
  "2 sentences in the feedback below · 1 with uncertain pronunciation evidence · 1 withheld because part of the analysis fell outside the sentence");
const unknownEnd = F.boundaryPlainText({ feedback_withheld: true, withheld_reason: "boundary" }, "plain");
const leaked = F.boundaryPlainText({ feedback_withheld: true, withheld_reason: "containment" }, "plain");
assert.match(unknownEnd, /could not be established/);
assert.ok(!/could not be established|could not be separated/.test(leaked) && /separated from the speech that followed/.test(leaked));
assert.equal(F.boundaryPlainText({ feedback_withheld: false, withheld_reason: null, boundary_confidence: "supported" }, "plain"), "plain");
assert.ok(/pause/.test(F.BOUNDARY_CONFIDENCE_TEXT.supported) && !/unclear/.test(F.BOUNDARY_CONFIDENCE_TEXT.supported));
assert.match(F.BOUNDARY_CONFIDENCE_TEXT.insufficient, /not established/);
console.log("ok reader-feedback tests");
