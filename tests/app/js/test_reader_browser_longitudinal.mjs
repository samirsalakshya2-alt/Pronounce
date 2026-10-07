import assert from "node:assert/strict";
import test from "node:test";
import longitudinal from "../../../src/pronunciation_lab/app/static/reader-browser-longitudinal.js";

function sound(attemptId, index, word, kind = "counter") {
  return {
    o: `${attemptId}:obs:${index}`, obs: `${attemptId}:raw:${index}`,
    e: "ɛ", h: kind === "clear" ? "ɪ" : null,
    out: kind === "clear" ? "heard_other" : "as_expected",
    q: kind === "clear" ? "confident" : "counter",
    x: null, c: kind === "clear" ? "high" : "high",
    w: word, wd: word, wi: index, si: 0, frag: false,
    pos: index === 0 ? "initial" : "medial", st: null, cl: false, sp: "inside",
    ag: "not_compared", play: index === 0 ? 120 : null, span: index === 0 ? 150 : null,
  };
}

function reading(sessionId, attemptId, time, article, { engine = "wav2vec2_raw", practice = false, clear = true } = {}) {
  const sounds = Array.from({ length: 10 }, (_, index) =>
    sound(attemptId, index, `${sessionId}word${index}`, clear && index === 0 ? "clear" : "counter"));
  return {
    reading: {
      reading_id: `${sessionId}:${attemptId}`, session_id: sessionId, attempt_id: attemptId,
      job_id: `${attemptId}:job`, segment_id: `${article}:segment`, engine, recorded_at: time,
      sentence_key: `${article}:sentence`, sentence_text: `${article} sentence`,
      article_id: article, article_key: article, text_id: article, practice_session: practice,
      fluency_reliable: false,
    },
    eligible: true, reason: null, sounds, fluency: [],
  };
}

function baseline() {
  return [
    reading("s1", "a1", "2026-01-01T00:00:00Z", "text1"),
    reading("s2", "a2", "2026-01-02T00:00:00Z", "text2"),
    reading("s3", "a3", "2026-01-03T00:00:00Z", "text3"),
  ];
}

test("builds per-engine M10 history from fresh evidence with faithful state and reference shapes", () => {
  const records = baseline();
  records.push({
    ...reading("other", "other-attempt", "2026-01-04T00:00:00Z", "other-text", { engine: "openpronounce" }),
    eligible: false, reason: "other_engine", sounds: [],
  });
  records.push({
    ...reading("withheld", "withheld-attempt", "2026-01-05T00:00:00Z", "withheld-text"),
    eligible: false, reason: "boundary_withheld", sounds: [],
  });
  const result = longitudinal.buildProgress(records, [], [], { generated_at: "2026-01-05T00:00:00Z" });
  assert.equal(result.version, "m10.1");
  assert.equal(result.engine, "wav2vec2_raw");
  assert.deepEqual(result.engines, { wav2vec2_raw: { eligible_readings: 3, sessions: 3 } });
  assert.equal(result.history.fresh_readings, 3);
  assert.equal(result.history.classes.FRESH, 3);
  assert.equal(result.eligibility.eligible, 3);
  assert.equal(result.eligibility.boundary_withheld, 1);
  assert.equal(result.patterns.length, 1);
  const [item] = result.patterns;
  assert.equal(item.pattern, "sub:ɛ>ɪ");
  assert.equal(item.state, "PERSONAL_RECURRING");
  assert.equal(item.scope, "PERSONAL_RECURRING");
  assert.equal(item.text, "Recurring across 3 readings of 3 different texts, in 3 words — likely personal.");
  assert.equal(item.decision.decision, "CONTINUE_CURRENT_TARGET");
  assert.equal(item.decision.recommendation, "Keep practising /ɛ/ (heard as /ɪ/).");
  assert.equal(item.totals.clear, 3);
  assert.equal(item.totals.opportunities, 30);
  assert.equal(item.examples.length, 3);
  assert.ok(item.examples.every((example) => example.url.startsWith("/api/sessions/")));
  assert.equal(item.evidence_classes.FRESH.clear, 3);
  assert.equal(result.integrity.ok, true, result.integrity.issues.join(", "));
});

test("excludes weak, ineligible, repeated, practice, and cross-engine evidence from learning windows", () => {
  const records = baseline();
  const repeat = reading("s4", "repeat", "2026-01-04T00:00:00Z", "text1");
  records.push(repeat);
  const practiceReading = reading("practice-session", "practice-attempt", "2026-01-05T00:00:00Z", "practice-text",
    { practice: true });
  records.push(practiceReading);
  const excluded = reading("s5", "weak", "2026-01-06T00:00:00Z", "weak-text");
  excluded.sounds = excluded.sounds.map((row) => ({ ...row, q: "weak", out: "not_detected", h: null }));
  records.push(excluded);
  const otherEngine = reading("s6", "other-engine", "2026-01-07T00:00:00Z", "other-engine-text",
    { engine: "openpronounce" });
  records.push(otherEngine);
  const practiceRecord = {
    id: "practice-record", created_at: "2026-01-04T12:00:00Z",
    practice_session_id: "practice-session",
    target: { patterns: ["sub:ɛ>ɪ"], context: null },
    material: {
      sentences: [{ text: "practice sentence", sentence_key: "practice-text:sentence" }],
      words: ["practice", "sentence"],
    },
  };
  const result = longitudinal.buildProgress(records, [practiceRecord], [], { engine: "wav2vec2_raw" });
  const [item] = result.patterns;
  assert.equal(result.history.classes.FRESH, 4);
  assert.equal(result.history.classes.REPEAT, 1);
  assert.equal(result.history.classes.PRACTICE, 1);
  assert.equal(item.totals.clear, 3);
  assert.equal(item.totals.opportunities, 30);
  assert.equal(item.evidence_classes.PRACTICE.readings, 1);
  assert.equal(item.evidence_classes.REPEAT.readings, 1);
  assert.equal(result.practice[0].status.completion, "completed");
  assert.equal(result.practice[0].outcomes[0].outcome, "INSUFFICIENT_OUTCOME");
  assert.equal(result.engines.openpronounce.eligible_readings, 1);
  assert.equal(result.integrity.ok, true, result.integrity.issues.join(", "));
});

test("adapts M9 target identities to matching history only and preserves unlinked actions", () => {
  const result = longitudinal.buildProgress(baseline());
  const coaching = {
    pool: { engine: "wav2vec2_raw" },
    actions: [
      { target: { target_id: "set:front_vowel_ladder", kind: "SET", pairs: ["ɛ→ɪ", "ɪ→ɛ"] } },
      { target: { target_id: "clarity:final_consonant", kind: "CLARITY" } },
    ],
  };
  const adapted = longitudinal.adaptCoaching(coaching, result);
  assert.equal(adapted.actions[0].history[0].pattern, "sub:ɛ>ɪ");
  assert.equal(adapted.actions[0].prioritised, true);
  assert.deepEqual(adapted.actions[1].patterns, []);
  assert.deepEqual(adapted.actions[1].history, []);
  const wrongEngine = longitudinal.adaptCoaching({
    ...coaching, pool: { engine: "openpronounce" },
  }, result);
  assert.deepEqual(wrongEngine.actions[0].history, []);
});

test("browser update consumes cached/session records and appends ledger and transition entries once", async () => {
  const records = baseline().map((record) => ({ ...record, fingerprint: `fp-${record.reading.reading_id}` }));
  const sessions = records.map((record) => ({ id: record.reading.session_id, records: [record] }));
  const first = await longitudinal.update({
    sessions,
    practice: [], cache: {}, ledger: [], transitions: [], coaching: null,
    generated_at: "2026-02-01T00:00:00Z",
  });
  assert.equal(first.progress.update.extracted, 3);
  assert.equal(first.ledger.length, 3);
  assert.equal(first.transitions.length, 1);
  assert.equal(first.transitions[0].to, "PERSONAL_RECURRING");
  assert.equal(first.cache.readings["s1:a1"].record.reading.reading_id, "s1:a1");

  const second = await longitudinal.update({
    sessions,
    practice: [], cache: first.cache, ledger: first.ledger, transitions: first.transitions,
    generated_at: "2026-02-02T00:00:00Z",
  });
  assert.equal(second.progress.update.extracted, 0);
  assert.equal(second.ledger.length, 3);
  assert.equal(second.transitions.length, 1);
});

test("canonical identity and practice material helpers match M10 conventions", () => {
  assert.equal(longitudinal.reverseOf("sub:ɛ>ɪ"), "sub:ɪ>ɛ");
  assert.equal(longitudinal.contrastGroup("sub:ɛ>ɪ"), "contrast:ɛ~ɪ");
  assert.deepEqual(longitudinal.linkTarget({
    target_id: "conditioned:medial", kind: "CONDITIONED", condition: "medial", pairs: ["ɛ→ɪ"],
  }).patterns, ["sub:ɛ>ɪ"]);
  assert.deepEqual(longitudinal.materialWords(["Hello, world!", "WORLD and speech."]), ["and", "hello", "speech", "world"]);
});

test("creates write-once practice records with M9 links and readable retest material", async () => {
  const record = await longitudinal.createPracticeRecord(
    "practice-id", "2026-01-01T00:00:00Z", "practice-session",
    {
      id: "practice-article",
      segments: [
        { text: "Read these now.", readable: true },
        { text: "A non-readable separator.", readable: false },
      ],
    },
    {
      action_text: "Practise the vowel contrast.", rank_in_plan: 2,
      target: { target_id: "contrast:ɛ~ɪ", kind: "CONTRAST", pairs: ["ɛ→ɪ", "ɪ→ɛ"] },
      practice: { trainability: "listen_compare_fallback" },
    },
    { generated_at: "2025-12-31T00:00:00Z", fingerprint: "pool-fingerprint" },
  );
  assert.equal(record.version, "m10-practice.1");
  assert.equal(record.target.m9_target_id, "contrast:ɛ~ɪ");
  assert.deepEqual(record.target.patterns, ["sub:ɛ>ɪ", "sub:ɪ>ɛ"]);
  assert.deepEqual(record.reverse_patterns, ["sub:ɛ>ɪ", "sub:ɪ>ɛ"]);
  assert.equal(record.advice.pool_fingerprint, "pool-fingerprint");
  assert.equal(record.practice_type, "retest_sentences:listen_compare_fallback");
  assert.equal(record.material.sentences.length, 1);
  assert.equal(record.material.sentences[0].sentence_key.length, 16);
});

test("practice outcomes use later fresh-word opportunities and do not treat retests as transfer", () => {
  const records = baseline();
  const practice = reading("practice-session", "practice-attempt", "2026-01-04T12:00:00Z", "practice-text",
    { practice: true });
  records.push(practice);
  for (let session = 1; session <= 2; session += 1) {
    const attemptId = `later-attempt-${session}`;
    const later = reading(`later-session-${session}`, attemptId, `2026-01-0${5 + session}T00:00:00Z`,
      `later-text-${session}`, { clear: false });
    later.sounds = Array.from({ length: 20 }, (_, index) =>
      sound(attemptId, index, `fresh${session}word${index}`, "counter"));
    records.push(later);
  }
  const practiceRecord = {
    id: "explicit-practice", created_at: "2026-01-04T00:00:00Z",
    practice_session_id: "practice-session", target: { patterns: ["sub:ɛ>ɪ"], context: null },
    material: {
      sentences: [{ text: "practice sentence", sentence_key: "practice-text:sentence" }],
      words: Array.from({ length: 10 }, (_, index) => `practice-sessionword${index}`),
    },
  };
  const result = longitudinal.buildProgress(records, [practiceRecord], [], { engine: "wav2vec2_raw" });
  const pattern = result.patterns.find((item) => item.pattern === "sub:ɛ>ɪ");
  const outcome = pattern.outcomes[0];
  assert.equal(outcome.outcome, "TRANSFER");
  assert.deepEqual(outcome.unpractised, { opportunities: 40, clear: 0, sessions: 2, expected: 4 });
  assert.equal(outcome.during_practice.clear, 1);
  assert.equal(pattern.decision.decision, "REDUCE_PRIORITY");
});

test("update extracts browser-local attempts, applies reader eligibility, and persists local M10 state", async () => {
  const sessions = [], articles = new Map(), attemptsBySession = new Map(), jobsByKey = new Map(), viewsByKey = new Map();
  const textWords = [
    "amber acorn orchard meadow lantern meadowlark cedar quartz valley.",
    "violet marble harbor glacier compass thistle canyon saffron summit.",
    "crimson willow prairie telescope silver maple canyon pebble horizon.",
  ];
  const sessionNames = ["alpha", "bravo", "charlie"];
  for (let n = 1; n <= 3; n += 1) {
    const sessionId = `browser-session-${n}`, articleId = `browser-article-${n}`;
    const attemptId = `browser-attempt-${n}`, jobId = `browser-job-${n}`;
    const sentence = textWords[n - 1];
    sessions.push({
      id: sessionId, article_id: articleId, engine_default: "wav2vec2_raw",
      attempt_ids: [attemptId], created_at: `2026-01-0${n}T00:00:00Z`,
    });
    articles.set(articleId, {
      id: articleId, text_sha256: `article-sha-${n}`, text: sentence, source: null,
    });
    attemptsBySession.set(sessionId, [{
      id: attemptId, session_id: sessionId, segment_id: `${articleId}:segment`,
      target_text: sentence, state: "ANALYZED", job_ids: [jobId], user_disposition: null,
      created_at: `2026-01-0${n}T00:00:00Z`,
    }]);
    const job = {
      id: jobId, session_id: sessionId, attempt_id: attemptId, kind: "primary",
      engine_id: "wav2vec2_raw", state: "SUCCEEDED",
      target_confirmation: { state: "MATCH" },
      boundary: { feedback_withheld: false, analysis: { state: "ok" } },
    };
    jobsByKey.set(`${sessionId}:${attemptId}:${jobId}`, job);
    const observations = Array.from({ length: 10 }, (_, index) => ({
      id: `${attemptId}-observation-${index}`, kind: "sound",
      type: index === 0 ? "substitution_candidate" : "expected",
      confidence: "high", expected: "ɛ", competitor: index === 0 ? "ɪ" : null,
      word: `readword${sessionNames[n - 1]}${String.fromCharCode(97 + index)}`, word_index: index,
      sound_index: 0, play_ms: index === 0 ? 130 : null, span_ms: index === 0 ? 150 : null,
      context: { word_position: "medial", sentence_position: "inside", in_consonant_cluster: false },
    }));
    observations[1] = {
      ...observations[1], type: "substitution_candidate", word: "the", competitor: "ɪ",
    };
    observations.push({
      id: `${attemptId}-reference-variant`, kind: "sound", type: "substitution_candidate",
      confidence: "high", expected: "ᵻ", competitor: "ɪ", word: "referenceword",
      word_index: 10, sound_index: 0, context: {},
    });
    viewsByKey.set(`${sessionId}:${attemptId}:${jobId}`, {
      state: "ok", duration_ms: 3000,
      coach: { version: "m4.1", observations },
      reduction: { state: "ok", candidates: [] },
      fluency: { state: "not_available", metrics: { activity_reliable: false } },
    });
  }
  let persistedCache = {}, ledger = [], transitionLog = [];
  const progressStore = {
    async practiceRecords() { return []; },
    async loadCache() { return persistedCache; },
    async ledger() { return ledger; },
    async transitionsLog() { return transitionLog; },
    async saveCache(cache) { persistedCache = cache; },
    async appendLedger(lines) { ledger = ledger.concat(lines); },
    async appendTransitions(lines) { transitionLog = transitionLog.concat(lines); },
  };
  const store = {
    progressStore: () => progressStore,
    async sessionIds() { return sessions.map((session) => session.id); },
    async loadSession(id) { return sessions.find((session) => session.id === id); },
    async attempts(id) { return attemptsBySession.get(id); },
    async loadArticle(id) { return articles.get(id); },
    async loadJob(sid, aid, jid) { return jobsByKey.get(`${sid}:${aid}:${jid}`); },
    async loadView(job) { return viewsByKey.get(`${job.session_id}:${job.attempt_id}:${job.id}`); },
    async loadCoaching() { return null; },
  };
  const first = await longitudinal.update({
    store, generated_at: "2026-02-01T00:00:00Z",
  });
  assert.equal(first.progress.patterns[0].state, "PERSONAL_RECURRING");
  assert.equal(first.progress.update.extracted, 3);
  assert.equal(first.ledger.length, 3);
  assert.equal(first.cache.extractor_version, "m10-src.1");
  const extracted = persistedCache.readings["browser-session-1:browser-attempt-1"].record;
  assert.equal(extracted.eligible, true);
  assert.equal(extracted.sounds.find((row) => row.w === "the").x, "function_word_variant");
  assert.equal(extracted.sounds.find((row) => row.obs.endsWith("reference-variant")).x, "reference_variant");
  assert.equal(first.progress.patterns[0].totals.opportunities, 27);
  assert.equal(first.progress.patterns.some((item) => item.pattern === "sub:ᵻ>ɪ"), false);
  const second = await longitudinal.update({
    store, generated_at: "2026-02-02T00:00:00Z",
  });
  assert.equal(second.progress.update.extracted, 0);
  assert.equal(ledger.length, 3);
  assert.equal(transitionLog.length, 1);
});
