import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const {
  buildReadingFeedback, buildSummary, runCoaching, progress, update, newPracticeRecord,
} = require("../../../src/pronunciation_lab/app/static/reader-browser-coaching.js");

// Integration input contract:
// - `inputs` accepts canonical M9 ReadingInput objects (`reading`, `coach_observations`,
//   `reduction_candidates`, `fluency`, etc.) or BrowserReader eligible rows
//   (`session`, `article`, `attempt`, `job`, `view`, `status`).
// - `sessions` accepts BrowserReader `{session, article}` rows; their eligible `inputs`
//   are converted to M10 compact records. Pre-extracted M10 rows may instead be supplied
//   under each session's `records` array.
// - `longitudinalStore` is BrowserLongitudinalStore (with `.store`, cache, ledger,
//   transitions, and practice-record methods); `progress` reads it while `update`
//   persists refreshed cache/ledger/transitions.
function input(index, observations, options = {}) {
  const session = options.session ?? `session-${index % 2}`;
  const attempt = `attempt-${index}`;
  const job = `job-${index}`;
  return {
    reading: {
      reading_id: `${session}:${attempt}`, session_id: session, attempt_id: attempt, job_id: job,
      segment_id: `segment-${index}`, engine: options.engine || "engine-a",
      recorded_at: options.recorded_at || `2026-01-${String(index + 1).padStart(2, "0")}T00:00:00Z`,
      sentence_key: options.sentence_key || `sentence-${index}`, sentence_text: `Sentence ${index}`,
      article_key: "article", analysis_state: options.analysis_state || "ok",
      fluency_reliable: options.fluency_reliable ?? true,
      sentence_ref: { session_id: session, attempt_id: attempt, job_id: job, segment_id: `segment-${index}`,
        timeline: "analysis_wav", play_ms: [0, 1200], span_ms: [0, 1200] },
    },
    coach_version: "m4.1",
    coach_observations: observations.map((o, n) => ({
      id: `o${n}`, kind: "sound", type: "substitution_candidate", confidence: "high",
      expected_posterior: 0.01, competitor_posterior: 0.8, expected: "ɪ", competitor: "iː",
      observed: "iː", word: `word${n}`, word_index: n, play_ms: [n * 100, n * 100 + 50],
      span_ms: [n * 100, n * 100 + 40], word_play_ms: [n * 100, n * 100 + 75],
      context: { word_position: "medial", previous_phone: "s", next_phone: "t" }, ...o,
    })),
    reduction_candidates: options.reductions || [],
    engine_agreement: options.engine_agreement || {},
    fluency: options.fluency || null,
    fragment_words: options.fragment_words || [],
  };
}

test("M4 quality gates preserve uncertainty and exclusions", () => {
  const sample = input(0, [
    { id: "clear", word: "sit" },
    { id: "amb", type: "ambiguous", confidence: "low", competitor: "iː", word: "bit" },
    { id: "low-sentence", word: "fit" },
    { id: "vowel-function", expected: "ɪ", word: "in" },
    { id: "accent-variant", expected: "i", competitor: "iː", observed: "iː", word: "see" },
    { id: "extra", type: "insertion", expected: null, observed: "t", word: "extra" },
    { id: "unreadable", type: "not_interpreted", word: "word" },
  ], { analysis_state: "ok" });
  sample.coach_observations[2].confidence = "moderate";
  sample.reading.analysis_state = "low_confidence";
  const parsed = require("../../../src/pronunciation_lab/app/static/reader-browser-coaching.js").normaliseInput(sample);
  const byId = Object.fromEntries(parsed.units.map((u) => [u.observation_id, u]));
  assert.equal(byId.clear.quality, "supporting", "a low-confidence M7 sentence downgrades a clear M4 substitution");
  assert.equal(byId.amb.quality, "supporting");
  assert.equal(byId["vowel-function"].exclusion, "function_word_variant");
  assert.equal(byId["accent-variant"].exclusion, "reference_variant");
  assert.equal(byId.extra.exclusion, "extra_sound");
  assert.equal(byId.unreadable.exclusion, "not_interpreted");
  assert.equal(byId.clear.audio.timeline, "analysis_wav");
  assert.equal(byId.clear.audio.play_ms[1], 50);
});

test("M5 natural-speech and merge candidates do not become pronunciation evidence", () => {
  const x = input(1, [
    { id: "natural", type: "omission_candidate", confidence: "low", expected: "t", word: "last", context: { word_position: "final" } },
    { id: "merged", type: "substitution_candidate", expected: "t", competitor: "d", observed: "d", word: "land" },
  ], { reductions: [
    { observation_id: "natural", interpretation: { category: "possible_omission", natural_connected_speech_possible: true } },
    { observation_id: "merged", merge_suspect: true, interpretation: { category: "possible_coarticulation" } },
  ] });
  const { units } = require("../../../src/pronunciation_lab/app/static/reader-browser-coaching.js").normaliseInput(x);
  assert.deepEqual(units.map((u) => u.exclusion), ["context_predicted", "merge_suspect"]);
});

test("M8 only admits noticed phrase pauses at a measurable speech level", () => {
  const x = input(2, [], { fluency: { state: "ok", metrics: { activity_reliable: false }, observations: [
    { id: "p1", type: "PAUSE", notice: true, classification: "possible_hesitation", context: { position: "inside_phrase" },
      playback: { play_ms: [10, 300], span_ms: [20, 250] } },
    { id: "p2", type: "PAUSE", notice: true, classification: "possible_hesitation", context: { position: "boundary" } },
    { id: "f1", type: "FILLER", notice: true, context: {}, playback: { play_ms: [400, 500], span_ms: [410, 490] } },
  ] } });
  const { fluency } = require("../../../src/pronunciation_lab/app/static/reader-browser-coaching.js").normaliseInput(x);
  assert.equal(fluency.length, 2);
  assert.ok(fluency.every((f) => f.exclusion === "unreliable_level"));
  assert.equal(fluency.some((f) => f.observation_id === "p2"), false);
});

test("single-session summary mirrors ReaderFeedback shape, latest-eligible selection, and coverage exclusions", async () => {
  const session = {
    id: "summary-session", engine_default: "engine-a", rev: 7,
    attempt_ids: ["included", "withheld", "discarded", "fallback", "latest-mismatch", "ambiguous"],
  };
  const article = {
    id: "summary-article",
    segments: Array.from({ length: 5 }, (_, index) => ({
      id: `seg-${index + 1}`, index, readable: true, text: `Sentence ${index + 1}`,
    })),
  };
  const soundObservations = [
    { id: "sub-1", kind: "sound", type: "substitution_candidate", confidence: "high",
      expected: "ɛ", competitor: "ɪ", word: "best", word_index: 0,
      play_ms: [100, 180], span_ms: [120, 160], word_play_ms: [80, 220], context: { word_position: "medial" } },
    { id: "sub-2", kind: "sound", type: "substitution_candidate", confidence: "high",
      expected: "ɛ", competitor: "ɪ", word: "left", word_index: 1,
      play_ms: [300, 380], span_ms: [320, 360], word_play_ms: [280, 420], context: { word_position: "medial" } },
    { id: "sub-3", kind: "sound", type: "substitution_candidate", confidence: "moderate",
      expected: "ɛ", competitor: "ɪ", word: "next", word_index: 2,
      play_ms: [500, 580], span_ms: [520, 560], word_play_ms: [480, 620], context: { word_position: "medial" } },
    { id: "counter", kind: "sound", type: "expected", confidence: "high",
      expected: "ɛ", competitor: null, word: "dress", word_index: 3,
      play_ms: [700, 780], span_ms: [720, 760], context: { word_position: "final" } },
  ];
  const attempts = new Map();
  const jobs = new Map();
  const views = new Map();
  const addAttempt = (id, segment, options = {}) => {
    const jobId = `${id}-job`;
    const attempt = {
      id, session_id: session.id, segment_id: `seg-${segment}`, target_text: `Sentence ${segment}`,
      state: "ANALYZED", user_disposition: options.disposition || null, created_at: `2026-01-0${segment}T00:00:00Z`,
      job_ids: [jobId], audio: { duration_ms: 1200 },
    };
    const job = {
      id: jobId, session_id: session.id, attempt_id: id, kind: "primary", engine_id: "engine-a",
      state: "SUCCEEDED", target_confirmation: { state: options.identity || "MATCH" },
      boundary: options.boundary || { state: "TARGET_ONLY", feedback_withheld: false, analysis: { state: "ok" } },
    };
    attempts.set(id, attempt);
    jobs.set(jobId, job);
    views.set(jobId, options.view || {
      state: "ok", duration_ms: 1200,
      coach: { observations: [] },
      reduction: { state: "ok", candidates: [] },
      fluency: { state: "not_available" },
    });
    return attempt;
  };
  addAttempt("included", 1, { view: {
    state: "ok", duration_ms: 1200, coach: { observations: soundObservations },
    reduction: { state: "ok", candidates: [
      { observation_id: "sub-1", expected: "ɛ", evidence_strength: "moderate", where: {
        play_ms: [100, 180], span_ms: [120, 160], word: "best", word_play_ms: [80, 220],
      }, interpretation: {
        category: "possible_coarticulation", label: "Possible coarticulation", natural_connected_speech_possible: true,
        candidate_explanations: [{ id: "linking", text: "Connected speech can link these sounds." }],
      } },
      { observation_id: "sub-2", expected: "ɛ", evidence_strength: "low", where: {
        play_ms: [300, 380], span_ms: [320, 360], word: "left",
      }, interpretation: {
        category: "possible_coarticulation", label: "Possible coarticulation", natural_connected_speech_possible: false,
        candidate_explanations: [{ id: "linking", text: "Connected speech can link these sounds." }],
      } },
    ] },
    fluency: { state: "ok", metrics: { rate_available: true, speaking_rate: 4.2 }, observations: [
      { id: "pause", type: "PAUSE", classification: "unusually_long", notice: true, label: "Long pause",
        observed: "a long pause", strength: "moderate", duration_ms: 450,
        playback: { play_ms: [900, 1200], span_ms: [940, 1150], context_ms: [700, 1200] } },
    ] },
  } });
  addAttempt("withheld", 2, { boundary: {
    state: "BOUNDARY_UNCERTAIN", feedback_withheld: true, withheld_reason: "boundary",
    analysis: { state: "ok" },
  } });
  addAttempt("discarded", 3, { disposition: "discarded" });
  addAttempt("fallback", 4);
  addAttempt("latest-mismatch", 4, { identity: "MISMATCH" });
  addAttempt("ambiguous", 5, { identity: "AMBIGUOUS" });
  const store = {
    async loadAttempt(_sid, id) { return attempts.get(id); },
    async loadJob(_sid, _aid, jid) { return jobs.get(jid); },
    async loadView(job) { return views.get(job.id); },
  };
  const summary = await buildSummary(store, session, article);
  assert.equal(summary.version, "sum-3");
  assert.equal(summary.session_rev, 7);
  assert.equal(summary.coverage.sentences, 5);
  assert.equal(summary.coverage.read, 5);
  assert.equal(summary.coverage.included, 2);
  assert.equal(summary.coverage.feedback_included, 2);
  assert.equal(summary.coverage.recorded, 4);
  assert.equal(summary.coverage.feedback_withheld, 1);
  assert.equal(summary.coverage.awaiting_decision, 1);
  assert.equal(summary.inputs.length, 2);
  assert.equal(summary.inputs[1].attempt_id, "fallback", "later mismatch falls back to the latest eligible attempt");
  assert.deepEqual(summary.coverage.not_included, [
    { segment_id: "seg-2", sentence: 2,
      reason: "sentence boundary uncertain — recording preserved, feedback withheld" },
    { segment_id: "seg-3", sentence: 3, reason: "discarded" },
    { segment_id: "seg-5", sentence: 5,
      reason: "could not confirm it is this sentence — keep it to include it" },
  ]);
  assert.equal(summary.patterns[0].kind, "contrast");
  assert.equal(summary.patterns[0].class, "context_specific");
  assert.equal(summary.patterns[0].summary,
    "In 3 of 4 occurrences of /ɛ/, the acoustic/recognition evidence is more consistent with /ɪ/. /ɛ/ was heard as expected in 1 other occurrence. Only seen word-medial, so this is specific to that context, not a general /ɛ/ pattern.");
  assert.equal(summary.groups[0].id, "recurring");
  assert.equal(summary.practise[0].pattern_id, summary.patterns[0].id);
  assert.equal(summary.practise[0].guidance, null, "unsupported /ɛ/–/ɪ/ articulation guidance is not invented");
  assert.equal(summary.reductions[0].occurrences, 2);
  assert.equal(summary.reductions[0].explanations.length, 1);
  assert.equal(summary.reductions[0].examples[0].timeline, "analysis_wav");
  assert.equal(summary.fluency.kinds[0].kind, "LONG_PAUSE");
  assert.equal(summary.fluency.speech_rate.min, 4.2);
  assert.equal(summary.fluency.examples[0].attempt_id, "included");
  assert.deepEqual(summary.caveats.length, 4);
});

test("per-reading feedback stays scoped, separates evidence levels, and exposes no scores", () => {
  const rows = [
    input(0, [{ word: "sit" }]),
    input(1, [{ word: "bit" }]),
    input(2, [{ word: "fit" }]),
  ];
  rows[0].reading.session_id = rows[1].reading.session_id = rows[2].reading.session_id = "one-reading";
  rows.forEach((r) => { r.reading.reading_id = `one-reading:${r.reading.attempt_id}`; });
  const result = buildReadingFeedback(rows);
  assert.equal(result.scope, "this_reading");
  assert.equal(result.state, "feedback");
  assert.equal(result.integrity.ok, true);
  assert.equal(result.improvement_areas[0].band, "clear");
  assert.equal(result.improvement_areas[0].counts.clear, 3);
  assert.equal(result.improvement_areas[0].counts.ambiguous, 0);
  assert.equal("score" in result, false);
  assert.equal("practice" in result, false);
  assert.ok(result.provenance.fingerprint.match(/^[0-9a-f]{20}$/));
});

test("mixed M9 reading evidence stays support-only and uses the clear-observation rate", () => {
  const rows = [
    input(0, [{ type: "substitution_candidate", expected: "ɛ", competitor: "ɪ", observed: "ɪ", word: "best" },
      ...Array.from({ length: 4 }, (_, i) => ({ id: `ok-${i}`, type: "expected", confidence: "high",
        expected: "ɛ", word: `counter-a-${i}`, word_index: i, play_ms: [800 + i * 50, 840 + i * 50], span_ms: [800 + i * 50, 840 + i * 50] }))]),
    input(1, [{ type: "substitution_candidate", expected: "ɛ", competitor: "ɪ", observed: "ɪ", word: "spell" },
      ...Array.from({ length: 4 }, (_, i) => ({ id: `ok-${i}`, type: "expected", confidence: "high",
        expected: "ɛ", word: `counter-b-${i}`, word_index: i, play_ms: [800 + i * 50, 840 + i * 50], span_ms: [800 + i * 50, 840 + i * 50] }))]),
    input(2, [{ type: "ambiguous", confidence: "low", expected: "ɛ", competitor: "ɪ", observed: "ɪ", word: "level" },
      ...Array.from({ length: 2 }, (_, i) => ({ id: `ok-${i}`, type: "expected", confidence: "high",
        expected: "ɛ", word: `counter-c-${i}`, word_index: i, play_ms: [800 + i * 50, 840 + i * 50], span_ms: [800 + i * 50, 840 + i * 50] }))]),
  ];
  rows.forEach((r) => {
    r.reading.session_id = "single-reading";
    r.reading.reading_id = `single-reading:${r.reading.attempt_id}`;
    r.coach_observations.forEach((o) => {
      if (o.type === "substitution_candidate" || o.type === "ambiguous") o.expected = "ɛ";
    });
  });
  const result = buildReadingFeedback(rows);
  const [area] = result.improvement_areas;
  assert.equal(result.integrity.ok, true);
  assert.equal(area.band, "mixed");
  assert.equal(area.counts.clear, 2);
  assert.equal(area.counts.ambiguous, 1);
  assert.equal(area.counts.clear_rate, 0.154);
  assert.match(area.evidence_text, /2 clear \(2 high-confidence, 0 moderate-confidence\) and 1 ambiguous/);
  assert.match(area.rate_text, /2 of 13 occurrences/);
});

test("coaching selects only the most recent engine pool and returns deterministic history gates", () => {
  const rows = Array.from({ length: 10 }, (_, i) => input(i, [], {
    session: `session-${i % 2}`, engine: i === 9 ? "engine-b" : "engine-a",
    recorded_at: `2026-02-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
  }));
  const a = runCoaching(rows, { no_view: 2 });
  const b = runCoaching(rows, { no_view: 2 });
  assert.equal(a.pool.engine, "engine-b");
  assert.equal(a.pool.readings, 1);
  assert.equal(a.pool.reading_exclusions.other_engine, 9);
  assert.equal(a.pool.reading_exclusions.no_view, 2);
  assert.equal(a.no_action.code, "history_too_small");
  assert.equal(a.pool.fingerprint, b.pool.fingerprint);
  assert.deepEqual(a.actions, []);
  assert.equal("score" in a, false);
});

test("M9 builds a playable intervention and records the Pareto reason for action order", () => {
  const rows = Array.from({ length: 10 }, (_, i) => {
    const soundA = i < 8;
    const observations = [{
      type: "substitution_candidate", confidence: "high",
      expected: soundA ? "s" : "t", competitor: soundA ? "ʃ" : "d", observed: soundA ? "ʃ" : "d",
      word: `${soundA ? "sound" : "target"}${i}`, word_index: 0,
      play_ms: [100, 220], span_ms: [110, 210], word_play_ms: [90, 230],
    }];
    if (i < 5) {
      observations.push({ id: `secondary-${i}`, type: "substitution_candidate", confidence: "high",
        expected: "f", competitor: "v", observed: "v", word: `friction${i}`, word_index: 1,
        play_ms: [300, 420], span_ms: [310, 410], word_play_ms: [290, 430] });
      observations.push({ id: `counter-${i}`, type: "expected", expected: "f", confidence: "high",
        word: `counter${i}`, word_index: 2, play_ms: [500, 620], span_ms: [510, 610], word_play_ms: [490, 630] });
    }
    if (i >= 8) observations.push({ id: `counter-s-${i}`, type: "expected", expected: "s", confidence: "high",
      word: `expected${i}`, word_index: 2, play_ms: [500, 620], span_ms: [510, 610], word_play_ms: [490, 630] });
    return input(i, observations, { session: `session-${i % 2}`, sentence_key: `sentence-${i}` });
  });
  const first = runCoaching(rows);
  const repeated = runCoaching(rows);
  assert.equal(first.state, "actions");
  assert.equal(first.actions.length, 2);
  assert.deepEqual(first.actions.map((a) => a.target.target_id), ["contrast:s~ʃ", "contrast:f~v"]);
  assert.deepEqual(first.actions[0].why.decided_by.map((r) => r.criterion), ["breadth_sentences"]);
  assert.equal(first.actions[0].why.decided_by[0].winner, "contrast:s~ʃ");
  assert.equal(first.actions[0].practice.trainability, "specific_guidance");
  assert.ok(first.actions[0].practice.examples.length > 0);
  assert.ok(first.actions[0].practice.counter_examples.length > 0);
  assert.ok(first.actions[0].practice.retest_sentences.length > 0);
  assert.equal(first.actions[0].practice.steps.at(-1).step, "retest");
  assert.deepEqual(first.actions[0].supporting_unit_ids, repeated.actions[0].supporting_unit_ids);
  assert.deepEqual(first.detail.selection_rounds, repeated.detail.selection_rounds);
  assert.equal(new Set(first.actions.flatMap((a) => a.supporting_unit_ids)).size,
    first.actions.reduce((n, a) => n + a.supporting_unit_ids.length, 0));
});

test("M9 combines reciprocal contrasts into one two-way target without reusing units", () => {
  const rows = Array.from({ length: 10 }, (_, i) => {
    const forward = i < 5;
    return input(i, [{
      type: "substitution_candidate", confidence: "high",
      expected: forward ? "ɪ" : "iː", competitor: forward ? "iː" : "ɪ", observed: forward ? "iː" : "ɪ",
      word: `contrastword${i}`, word_index: 0,
      play_ms: [100, 220], span_ms: [110, 210], word_play_ms: [90, 230],
    }], { session: `session-${i % 2}`, sentence_key: `sentence-${i}` });
  });
  const result = runCoaching(rows);
  const target = result.actions.find((a) => a.target.target_id === "contrast:iː~ɪ");
  assert.ok(target);
  assert.equal(target.target.two_way, true);
  assert.equal(target.target.pairs.length, 2);
  assert.deepEqual(target.target.pairs, ["iː→ɪ", "ɪ→iː"]);
  assert.equal(target.supporting_unit_ids.length, 10);
  assert.equal(new Set(target.supporting_unit_ids).size, 10);
  assert.equal(target.action_text, "Practise telling /iː/ and /ɪ/ apart");
});

test("M9 merges eligible members into a family target with auditable conditions", () => {
  const rows = Array.from({ length: 10 }, (_, i) => {
    const heard = i < 5 ? "ɪ" : "eɪ";
    const observations = [{
      type: "substitution_candidate", confidence: "high", expected: "ɛ", competitor: heard, observed: heard,
      word: `vowelword${i}`, word_index: 0, play_ms: [100, 220], span_ms: [110, 210],
    }, {
      id: `expected-${i}`, type: "expected", expected: "ɛ", confidence: "high",
      word: `stable${i}`, word_index: 1, play_ms: [300, 420], span_ms: [310, 410],
    }];
    return input(i, observations, { session: `session-${i % 2}`, sentence_key: `vowel-sentence-${i}` });
  });
  const result = runCoaching(rows);
  const family = result.detail.candidates.find((candidate) => candidate.target_id === "set:front_vowel_ladder");
  assert.ok(family, "both declared contrast members merge into their shared family");
  assert.equal(family.kind, "SET");
  assert.equal(family.tier, "established");
  assert.equal(family.checks.confident.ok, true);
  assert.equal(family.consolidation_record[0].decision, "merged");
  assert.equal(family.consolidation_record[0].conditions["4_explains_clearly_more"].ok, true);
  assert.ok(result.detail.consolidation.some((record) =>
    record.target === "contrast:ɛ~ɪ" && record.absorbed_into === "set:front_vowel_ladder"));
  assert.ok(result.actions.some((action) => action.target.target_id === "set:front_vowel_ladder"));
});

test("M9 narrows a broad sound pattern to the strongest supported context", () => {
  const rows = Array.from({ length: 10 }, (_, i) => {
    const observations = [];
    if (i < 8) observations.push({
      type: "substitution_candidate", confidence: "high", expected: "s", competitor: "ʃ", observed: "ʃ",
      word: `finalword${i}`, word_index: 0, context: { word_position: "final" },
      play_ms: [100, 220], span_ms: [110, 210],
    });
    for (let n = 0; n < 2; n++) observations.push({
      id: `counter-${i}-${n}`, type: "expected", expected: "s", confidence: "high",
      word: `initialword${i}-${n}`, word_index: n + 1,
      context: { word_position: "initial" }, play_ms: [300 + n * 100, 380 + n * 100],
      span_ms: [320 + n * 100, 360 + n * 100],
    });
    return input(i, observations, { session: `session-${i % 2}`, sentence_key: `context-sentence-${i}` });
  });
  const result = runCoaching(rows);
  const narrowed = result.detail.candidates.find((candidate) => candidate.target_id === "conditioned:final:contrast:s~ʃ");
  assert.ok(narrowed);
  assert.equal(narrowed.condition, "final");
  assert.equal(narrowed.tier, "established");
  assert.equal(narrowed.consolidation_record.at(-1).decision, "narrowed");
  assert.ok(result.detail.consolidation.some((record) =>
    record.target === "contrast:s~ʃ" && record.absorbed_into === narrowed.target_id));
});

test("M9 forms an independently supported single-word target", () => {
  const rows = Array.from({ length: 10 }, (_, i) => {
    const observations = [];
    if (i < 9) {
      observations.push({
        type: "substitution_candidate", confidence: "high", expected: "s", competitor: "ʃ", observed: "ʃ",
        word: i < 3 ? "blossom" : `otherword${i}`, word_index: 0,
        play_ms: [100, 220], span_ms: [110, 210],
      });
    }
    if (i >= 3) observations.push({
      id: `stable-${i}`, type: "expected", expected: "s", confidence: "high",
      word: `stableword${i}`, word_index: 1, play_ms: [300, 420], span_ms: [310, 410],
    });
    return input(i, observations, { session: `session-${i % 2}`, sentence_key: `lexical-sentence-${i}` });
  });
  const result = runCoaching(rows);
  const lexical = result.detail.candidates.find((candidate) => candidate.target_id === "lexical:blossom:s");
  assert.ok(lexical);
  assert.equal(lexical.kind, "LEXICAL");
  assert.equal(lexical.tier, "established");
  assert.equal(lexical.measures.confident, 3);
  assert.equal(lexical.checks.readings_of_word.ok, true);
  assert.ok(result.detail.absorbed_by_selection.some((record) => record.target === lexical.target_id),
    "the broader selected sound action removes the lexical target's shared evidence before re-gating it");
});

test("M9 reports final-consonant clarity only as a capped emerging candidate", () => {
  const rows = Array.from({ length: 10 }, (_, i) => input(i, i < 6 ? [{
    type: "weak_evidence", confidence: "low", expected: "t", word: `finalword${i}`, word_index: 0,
    context: { word_position: "final" }, play_ms: [100, 220], span_ms: [110, 210],
  }] : [], { session: `session-${i % 2}`, sentence_key: `clarity-sentence-${i}` }));
  const result = runCoaching(rows);
  const clarity = result.detail.candidates.find((candidate) => candidate.target_id === "clarity:final_consonant");
  assert.ok(clarity);
  assert.equal(clarity.kind, "CLARITY");
  assert.equal(clarity.tier, "emerging");
  assert.match(clarity.hypothesis, /not detected does not prove a sound was absent/);
  assert.equal(result.actions.some((action) => action.target.kind === "CLARITY"), false);
  assert.equal(result.no_action.code, "nothing_recurring");
});

test("progress and update accept BrowserReader histories and keep persistence opt-in", async () => {
  const sample = input(0, [{ word: "sit" }]);
  sample.session = { id: sample.reading.session_id, engine_default: "engine-a" };
  sample.article = { id: "article-0", text: "A sentence.", segments: [] };
  sample.attempt = { id: sample.reading.attempt_id, segment_id: sample.reading.segment_id,
    target_text: sample.reading.sentence_text, created_at: sample.reading.recorded_at,
    audio: { duration_ms: 1200 } };
  sample.job = { id: sample.reading.job_id, engine_id: "engine-a", boundary: { analysis: { state: "ok" } } };
  sample.view = { state: "ok", duration_ms: 1200, coach: { version: "m4.1", observations: sample.coach_observations },
    reduction: { state: "ok", candidates: [] }, fluency: { state: "ok", metrics: { activity_reliable: true }, observations: [] } };
  delete sample.reading;
  delete sample.coach_observations;
  const stored = { cache: null, ledger: [], transitions: [] };
  const longitudinalStore = {
    async loadCache() { return stored.cache || {}; },
    async ledger() { return stored.ledger; },
    async transitionsLog() { return stored.transitions; },
    async practiceRecords() { return []; },
    async saveCache(value) { stored.cache = value; },
    async appendLedger(rows) { stored.ledger.push(...rows); },
    async appendTransitions(rows) { stored.transitions.push(...rows); },
  };
  const sessions = [{ session: sample.session, article: sample.article }];
  const readOnly = await progress({ inputs: [sample], sessions, longitudinalStore });
  assert.equal(readOnly.progress.version, "m10.1");
  assert.equal(stored.cache, null, "progress does not persist");
  const refreshed = await update({ inputs: [sample], sessions, longitudinalStore,
    generated_at: "2026-03-01T00:00:00Z" });
  assert.equal(refreshed.progress.version, "m10.1");
  assert.equal(stored.cache.extractor_version, "m10-src.1");
  assert.equal(stored.ledger.length, 1);
});

test("new practice record follows the M10 practice-record contract", () => {
  const record = newPracticeRecord({
    id: "practice-1", created_at: "2026-03-01T00:00:00Z", session_id: "practice-session",
    source_session_id: "source-session", target_id: "contrast:ɛ~ɪ",
    target: { target_id: "contrast:ɛ~ɪ", kind: "CONTRAST", pairs: ["ɛ→ɪ", "ɪ→ɛ"] },
    article: { id: "practice-article", segments: [
      { text: "Best spell level.", readable: true }, { text: "A fragment.", readable: false },
    ] },
    action: { target: { target_id: "contrast:ɛ~ɪ", kind: "CONTRAST", pairs: ["ɛ→ɪ", "ɪ→ɛ"] },
      action_text: "Practise the contrast", rank_in_plan: 1, practice: { trainability: "specific_guidance" } },
    advice: { generated_at: "2026-02-28T00:00:00Z", pool: { fingerprint: "pool-1" } },
  });
  assert.equal(record.version, "m10-practice.1");
  assert.equal(record.practice_session_id, "practice-session");
  assert.equal(record.article_id, "practice-article");
  assert.deepEqual(record.target.patterns, ["sub:ɛ>ɪ", "sub:ɪ>ɛ"]);
  assert.deepEqual(record.reverse_patterns, ["sub:ɛ>ɪ", "sub:ɪ>ɛ"]);
  assert.equal(record.advice.coaching_generated_at, "2026-02-28T00:00:00Z");
  assert.equal(record.practice_type, "retest_sentences:specific_guidance");
  assert.deepEqual(record.material.sentences.map((s) => s.text), ["Best spell level."]);
  assert.deepEqual(record.material.words, ["best", "level", "spell"]);
  assert.equal(record.mode, null);
  assert.equal("source_session_id" in record, false, "the Python M10 record shape does not persist this argument");
});
