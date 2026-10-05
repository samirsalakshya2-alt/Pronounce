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
  "Speech rate: not measured — too short to measure a rate (2 syllables).");
assert.equal(F.rateText({ rate_available: true, speaking_rate: 2.94, articulation_rate: 3.51, pause_count: 1, pause_total_ms: 890,
  articulation_available: true }),
  "Speech rate: 2.9 syllables per second · articulation rate 3.5 (without pauses) · 1 pause (0.9 s)");
// pauses not verified as silence: no articulation rate, no pause total presented as fact
assert.equal(F.rateText({ rate_available: true, speaking_rate: 1.81, articulation_rate: null, pause_count: 10, pause_total_ms: 6200,
  articulation_available: false, articulation_unavailable_reason: "pauses could not be checked" }),
  "Speech rate: 1.8 syllables per second · articulation rate not measured — pauses could not be checked.");
// exact interval and stated context for Listen
const lo = { start_ms: 2880, end_ms: 4380, playback: { span_ms: [2880, 4380], play_ms: [2630, 4630], context_ms: [250, 250] } };
assert.equal(F.spanText(lo), "2.88–4.38 s");
assert.equal(F.listenTitle(lo), "Plays 2.88–4.38 s with 0.25 s before and 0.25 s after it for context");
assert.equal(F.listenTitle({ start_ms: 0, end_ms: 500, playback: { context_ms: [0, 0] } }), "Plays exactly 0.00–0.50 s");
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
// M9: What to practise now — concise view from the contract; evidence lines keep their origins apart
const ref = (w) => ({ session_id: "s", attempt_id: "a", timeline: "analysis_wav", play_ms: [100, 400], span_ms: [160, 340],
  url: "/api/sessions/s/attempts/a/audio", word: w });
const action = { rank_in_plan: 1, action_text: "Practise telling /ɪ/ and /iː/ apart", time_minutes: 12,
  why: { text: "In 3 sessions, /ɪ/ was heard as /iː/ 6 times across 4 words.", decided_by: [
    { winner: "contrast:iː~ɪ", loser: "contrast:s~ʃ", criterion_label: "breadth (different sentences)", winner_value: 6, loser_value: 3 }],
    measures: { confident: 6, conf_sessions: 3, conf_sentences: 6, conf_words: 4, supporting: 2, counter: 12, concentration: 0.75 } },
  target: { target_id: "contrast:iː~ɪ", kind: "CONTRAST", hypothesis: "/ɪ/ was often heard as /iː/ in your recent readings." },
  transfer: { origin: "hypothesised", text: "Practising this may also help other words; this is a possibility, not something measured." },
  knowledge_contributions: [{ origin: "knowledge", text: "/ɪ/ is usually short; /iː/ longer." }],
  practice: { steps: [{ text: "Listen" }, { text: "Contrast" }], examples: [ref("sit"), ref("list"), ref("fill"), ref("bit")],
    counter_examples: [ref("sit")], retest_sentences: [{ text: "Sentence one.", ref: ref(null) }], guidance: [], guidance_note: null } };
let cv = F.coachingView({ state: "actions", actions: [action], detail: { listen_check: [{ target_id: "x" }] } });
assert.equal(cv.state, "actions");
assert.equal(cv.actions.length, 1);
assert.deepEqual(cv.actions[0].examples.map((e) => e.label), ["sit", "list", "fill"]);   // at most three
assert.deepEqual(cv.actions[0].steps, ["Listen", "Contrast"]);
assert.ok(!JSON.stringify(cv).includes("listen_check"));                              // emerging never in the view
cv = F.coachingView({ state: "no_action", actions: [], no_action: { code: "single_session", message: "Not enough evidence.", what_would_help: "Read again." } });
assert.deepEqual([cv.state, cv.message, cv.help], ["no_action", "Not enough evidence.", "Read again."]);
assert.equal(F.coachingView({ state: "unavailable", actions: [] }).message, F.COACHING_UNAVAILABLE);
assert.equal(F.coachingView(null).state, "none");
const lines = F.actionEvidenceLines(action);
assert.ok(lines[0].startsWith("Observed: 6 confident observations in 3 sessions, 6 sentences, 4 words (plus 2 ambiguous"));
assert.ok(lines.includes("About 8 in 10 confident differences of these sounds point this way."));
assert.ok(lines.some((l) => l.startsWith("Hypothesis (inferred): ")));
assert.ok(lines.some((l) => l.startsWith("Knowledge (general, not about you): ")));
assert.ok(lines.some((l) => l.startsWith("Possible transfer (not measured): ")));
assert.ok(lines.some((l) => l === "Chosen over contrast:s~ʃ — breadth (different sentences): 6 vs 3."));
for (const banned of ["score", "wrong", "error", "%", "rank"]) assert.ok(!lines.join(" ").toLowerCase().includes(banned), banned);
// M9 "This reading": improvement areas first, then strengths, fluency, cautions; scoped, never actions
const rfx = { scope: "this_reading", state: "feedback",
  improvement_areas: [{ kind: "CONTRAST", band: "mixed", text: "In this reading, /ɛ/ was heard as /ɪ/ 3 times in 3 sentences.",
    pattern_scope: "sound", pattern_label: "Recurring sound pattern",
    evidence_text: "Individual observations: 3 observed differences in 3 words: 2 clear (2 high-confidence, 0 moderate-confidence) and 1 ambiguous.",
    pattern_text: "Pattern: recurring, partly ambiguous. Heard clearly twice in the same direction, in 2 sentences and 2 words, supported by 1 ambiguous observation that is not counted as clear.",
    order_text: null, rate_text: "Clear-evidence rate: 2 of 13 occurrences of /ɛ/.",
    counter_text: "/ɛ/ was heard as expected 10 times in this reading.",
    examples: [ref("best"), ref("spell"), Object.assign(ref("level"), { evidence: "ambiguous" }), ref("west")], counter_examples: [ref("bed")] }],
  no_area_text: null,
  strengths: [{ text: "In this reading, /θ/ was heard as expected in 12 of 12 occurrences.", examples: [] }],
  fluency: { text: "In this reading, possible hesitation pauses were noticed inside phrases twice, in 2 sentences.", examples: [] },
  fluency_note: null,
  cautions: [{ code: "level_unmeasurable", text: "The speech level could not be measured in 1 sentence, so pauses there are not described." }],
  other_differences: { count: 3, note: "3 other differences in this reading (1 clear, 2 ambiguous) did not form a pattern strong enough to call out; the detailed report below lists them." } };
const rv = F.readingView(rfx);
assert.equal(rv.state, "feedback");
assert.equal(rv.areas.length, 1);
assert.equal(rv.areas[0].rank, 1);
assert.equal(rv.areas[0].band, "mixed");
assert.equal(rv.areas[0].examples.length, 3);
assert.ok(rv.areas[0].observations.includes("2 clear (") && rv.areas[0].observations.includes("1 ambiguous"));
assert.ok(rv.areas[0].pattern.startsWith("Pattern: ") && rv.areas[0].rate.startsWith("Clear-evidence rate: 2 of 13"));
assert.equal(rv.areas[0].scope, "sound");
assert.equal(rv.areas[0].label, "Recurring sound pattern");
assert.equal(rv.areas[0].why, null);
assert.equal(rv.strengths.length, 1);
assert.equal(rv.fluency.length, 1);
assert.equal(rv.cautions.length, 1);
assert.equal(rv.noArea, null);
assert.ok(rv.other.startsWith("3 other"));
assert.ok(rv.areas.every((a) => a.text.startsWith("In this reading")));
assert.ok(!JSON.stringify(rv).includes("Practise"));
const none = F.readingView(Object.assign({}, rfx, { improvement_areas: [], no_area_text: "No major pronunciation pattern was strong enough to call out in this reading." }));
assert.equal(none.areas.length, 0);
assert.ok(none.noArea.startsWith("No major pronunciation pattern"));
assert.equal(none.strengths.length, 1);           // strengths still shown, never in the areas' place
assert.equal(F.readingView({ state: "unavailable" }).state, "unavailable");
assert.equal(F.readingView(null).state, "none");
assert.equal(F.READING_SCOPE, "This reading only");
assert.equal(F.COACHING_SCOPE, "Based on your recent readings");
console.log("ok reader-feedback tests");
