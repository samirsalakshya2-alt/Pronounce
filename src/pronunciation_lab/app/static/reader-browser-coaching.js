"use strict";

(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PronounceReaderCoaching = api;
})(typeof globalThis === "undefined" ? this : globalThis, function () {
  const COACHING_VERSION = "m9.1";
  const READING_VERSION = "m9-read.3";
  const KNOWLEDGE_VERSION = "m9-kn.1";
  const CALIBRATION_VERSION = "m9-cal.3";
  const READING_CALIBRATION_VERSION = "m9-read-cal.3";
  const POOL = Object.freeze({ n_max: 40, min_readings: 10, min_sessions: 2 });
  const READING_CAL = Object.freeze({
    version: READING_CALIBRATION_VERSION, k_conf: 3, w_min: 2, s_min: 2, c_min: 0.5, m_merge: 1.5,
    ctx_min_in: 4, ctx_min_out: 4, ctx_ratio: 2, ctx_max_share: 0.6, lex_share: 0.8,
    lex_max_words: 1, lex_k_conf: 2, lex_rel: 2, min_sentences: 2, rate_min: 0.1, rate_high: 0.4,
    rate_moderate: 0.2, mixed_min_observations: 3, mixed_min_clear: 2, mixed_min_sentences: 2,
    mixed_min_real_words: 2, max_strengths: 3, strength_min_occurrences: 10, strength_min_share: 0.9,
    strength_max_differences: 1, fluency_min: 2, clarity_min_words: 3, n_examples: 3, n_counter: 2,
  });
  const COACH_CAL = Object.freeze({
    version: CALIBRATION_VERSION, pool_n_max: 40, pool_min_readings: 10, pool_min_sessions: 2,
    pool_max_age_days: null, k_conf: 5, w_min: 3, s_min: 3, sessions_min: 2, c_min: 0.5,
    disagree_max: 0.34, m_merge: 1.5, ctx_min_in: 8, ctx_min_out: 8, ctx_ratio: 2, ctx_max_share: 0.6,
    lex_share: 0.8, lex_max_words: 2, lex_k_conf: 3, lex_min_readings: 2, lex_rel: 2,
    margin_ratio: 1.5, margins_abs: { breadth_sentences: 2, recurrence_sessions: 2, breadth_words: 2,
      coverage: 2, reach: 2, concentration: 0.1, trainability: 1 },
    ordering: ["tier", "breadth_sentences", "recurrence_sessions", "breadth_words", "coverage", "reach",
      "concentration", "trainability"],
    max_actions: 3, max_fluency: 1, n_examples: 3, n_counter: 3, n_words: 5, n_retest: 2,
    fallback_min_counter: 2, time_split: [[12], [7, 5], [5, 4, 3]],
  });
  const OUTCOME = Object.freeze({
    expected: "as_expected", substitution_candidate: "heard_other", ambiguous: "heard_other",
    omission_candidate: "not_detected", weak_evidence: "not_detected", insertion: "extra",
    not_interpreted: "uninterpreted",
  });
  const FLUENCY_GROUP = Object.freeze({
    PAUSE: "hesitation", FILLER: "filler", REPETITION: "repetition", RESTART: "repetition",
    FALSE_START: "repetition",
  });
  const FUNCTION_WORDS = new Set(("a an the to of and in on at for from with by as into over i you he she it we " +
    "they me him her us them my your his its our their this that these those is are was were be been am has have had " +
    "do does did will would can could should shall may might must not no so but or if than then there what which who " +
    "when where although before during still").split(" "));
  const CROSS_CLASS_PLAUSIBLE = new Set(["ɚ~ɹ", "ɝ~ɹ", "əl~l"]);
  const REFERENCE_VARIANT_PAIRS = [
    ["i", "iː"], ["u", "uː"], ["ᵻ", "ɪ"], ["ɐ", "ə"], ["ɐ", "æ"], ["ɐ", "ʌ"], ["ə", "ʌ"],
    ["ɜː", "ɚ"], ["ɜ", "ɚ"], ["ɔ", "ʌ"], ["ɔ", "ɑ"], ["ɔ", "ɑː"], ["ɑ", "ɑː"], ["ʊɹ", "uː"],
    ["ʊɹ", "ʊ"], ["ɔːɹ", "oːɹ"], ["əl", "l"], ["ɾ", "t"], ["ɾ", "d"], ["ɚ", "ə"], ["ɔ", "ɔː"],
  ];
  const REFERENCE_PAIRS = new Set(REFERENCE_VARIANT_PAIRS.map((p) => pairKey(...p)));
  const FAMILIES = [
    { id: "front_vowel_ladder", mode: "set", members: ["iː", "i", "ɪ", "eɪ", "ɛ", "æ"] },
    { id: "back_rounded_vowels", mode: "set", members: ["ʊ", "uː", "u", "oʊ"] },
    { id: "sibilant_place", mode: "pairs", pairs: [["s", "ʃ"], ["ʃ", "s"], ["z", "ʒ"], ["ʒ", "z"]] },
    { id: "th_sounds", mode: "pairs", pairs: [["θ", "t"], ["θ", "s"], ["θ", "f"], ["ð", "d"], ["ð", "z"], ["ð", "v"]] },
    { id: "devoicing", mode: "pairs", pairs: [["b", "p"], ["d", "t"], ["ɡ", "k"], ["v", "f"],
      ["ð", "θ"], ["z", "s"], ["ʒ", "ʃ"], ["dʒ", "tʃ"]] },
    { id: "voicing", mode: "pairs", pairs: [["p", "b"], ["t", "d"], ["k", "ɡ"], ["f", "v"],
      ["θ", "ð"], ["s", "z"], ["ʃ", "ʒ"], ["tʃ", "dʒ"]] },
  ];
  const FAMILY_TEXT = "These sounds are related in English phonetics (general knowledge, not a measurement of your speech).";
  const STRENGTH_SOUNDS = new Set(FAMILIES.flatMap((f) =>
    f.mode === "set" ? f.members : f.id === "sibilant_place" ? f.pairs.flat() : f.id === "th_sounds"
      ? f.pairs.map((p) => p[0]) : []));
  const EXCLUSIONS = Object.freeze({
    not_interpreted: "M4 could not interpret it (alignment suspect or invalid evidence)",
    extra_sound: "an extra decoded sound; not used for coaching targets",
    reference_variant: "may reflect the reference accent / phoneme inventory (eSpeak en-us)",
    function_word_variant: "a vowel in a function word, which has several accepted pronunciations",
    implausible_pair: "a substitution across sound classes, more likely an alignment artifact",
    context_predicted: "M5: consistent with natural connected speech or a context-predicted variant",
    merge_suspect: "M5: a possible decoding merge of neighbouring sounds",
    neighbour_shift: "the heard sound is the neighbouring expected sound: an alignment shift or assimilation is likelier than a confusion",
    unreliable_level: "M8: speech level not measurable, so the pause is only a gap between decoded sounds",
  });
  const READING_EXCLUSIONS = Object.freeze({
    not_eligible: "not in the reading summary (withheld, unconfirmed, different sentence, discarded, re-recording requested, or not analysed)",
    other_engine: "analysed by another listening model than the pool's",
    outside_window: "older than the readings considered now", no_view: "no stored analysis view",
  });
  const GENERAL_CAVEAT = "Heard as expected means the listening model decoded the expected sound; it is not proof of a particular pronunciation. A clear observation is a confident decode; an ambiguous one is support only. Both local listening models share one acoustic model.";
  const NO_AREA_TEXT = "No major pronunciation pattern was strong enough to call out in this reading.";
  const SUMMARY_VERSION = "sum-3";
  const SUMMARY_CAVEATS = [
    "This summarises one reading. It describes what the listening model heard, not a fixed trait.",
    "Both local listening models share one acoustic model; agreement between them is not independent confirmation.",
    "Expected sounds come from a US-English reference (eSpeak en-us); some differences may reflect that reference.",
    "Stress and rhythm are not analysed yet.",
  ];
  const SUMMARY_GROUPS = [
    ["recurring", "Recurring in this recording",
      "Two or more occurrences point the same way. Still observations of this recording, not a fixed trait."],
    ["single", "Single occurrence — monitor", "Seen once. Not a pattern; listen to it and watch whether it recurs."],
    ["ambiguous", "Ambiguous — listen and compare",
      "The evidence does not clearly favour one sound. Listen to the occurrence and decide for yourself."],
    ["not_detected", "Not detected by the recogniser",
      "The recogniser found no clear sound here. Not detected does not prove the sound was absent."],
    ["insertion", "Extra sounds", "The recogniser decoded a sound that the expected pronunciation does not have."],
  ];
  const SUMMARY_REFERENCE_NOTE =
    "This may reflect the reference accent / phoneme inventory (eSpeak en-us) rather than a pronunciation difference.";
  const FUNCTION_WORD_NOTE =
    "Only seen in function words (e.g. 'the', 'to'), which have several accepted pronunciations; the reference lists one of them, so this may not be a pronunciation difference.";
  const SUMMARY_GUIDANCE_NOTE =
    "General description of how these sounds are usually distinguished — not a measurement of your articulation.";
  const SUMMARY_GUIDANCE = [
    [["w", "v"], "/w/ is usually made with rounded lips and no teeth contact; /v/ with the lower lip lightly touching the upper teeth, with voicing."],
    [["θ", "t"], "/θ/ is usually a continuous breathy sound with the tongue near the upper teeth; /t/ is a short stop-and-release."],
    [["ð", "d"], "/ð/ is usually a continuous voiced sound with the tongue near the upper teeth; /d/ is a short voiced stop."],
    [["θ", "s"], "/θ/ is usually softer and more diffuse; /s/ is a sharper, high-pitched hiss."],
    [["s", "ʃ"], "/s/ is usually a sharp high hiss with spread lips; /ʃ/ (as in 'ship') is lower-pitched, often with rounded lips."],
    [["s", "z"], "/s/ is usually voiceless; /z/ is the same hiss with voicing."],
    [["ŋ", "n"], "/ŋ/ (as in 'sing') is usually made at the back of the mouth; /n/ behind the upper teeth."],
    [["ɪ", "iː"], "/ɪ/ (as in 'sit') is usually short and relaxed; /iː/ (as in 'see') is longer and tenser."],
    [["tʃ", "dʒ"], "/tʃ/ (as in 'chin') is usually voiceless; /dʒ/ (as in 'jam') is voiced."],
    [["f", "v"], "/f/ is usually voiceless; /v/ is the same sound with voicing."],
    [["ɹ", "l"], "/ɹ/ is usually made without the tongue tip touching the roof of the mouth; /l/ with it touching."],
    [["ð", "z"], "/ð/ is usually made with the tongue near the upper teeth; /z/ is a sharper voiced hiss."],
  ];
  const COACH_CAVEATS = [
    "These actions come from your recent readings: they describe what the listening model heard, not a fixed trait.",
    "Both local listening models share one acoustic model; agreement between them is not independent confirmation.",
    "Expected sounds come from a US-English reference (eSpeak en-us); some differences may reflect that reference.",
    "This suggests what to practise; it does not measure improvement.",
  ];
  const NO_ACTION = Object.freeze({
    history_too_small: ["There isn't enough evidence yet to recommend a specific practice target.", "Read a few more sentences; recommendations need several readings."],
    single_session: ["There isn't enough evidence yet to recommend a specific practice target.", "Read again on another occasion: a pattern has to recur across sessions."],
    only_emerging: ["Nothing recurs strongly enough yet to recommend a specific practice target.", "Keep reading; some patterns are starting to appear but need more evidence."],
    only_ambiguous: ["The evidence so far is ambiguous, so there is no specific practice target yet.", "Keep reading; ambiguous decodes alone are never enough for a recommendation."],
    only_excluded_kinds: ["What was heard differently so far is not something to practise (it may reflect the reference accent, natural connected speech or function words).", "Keep reading; nothing needs special practice from this evidence."],
    contradictory: ["The evidence points in different directions, so there is no specific practice target yet.", "Keep reading; more readings will show whether a pattern is real."],
    no_trainable_intervention: ["A recurring pattern was found, but there is no concrete practice for it yet.", "Keep reading; more examples of your own will make a practice possible."],
    audio_unavailable: ["A recurring pattern was found, but its recordings are not available to practise with.", "Read the sentences again so there are recordings to listen to."],
    nothing_recurring: ["Nothing recurs across your recent readings that needs specific practice.", "Keep reading; this updates as you read."],
  });

  function val(obj, ...keys) {
    for (const k of keys) if (obj && obj[k] !== undefined && obj[k] !== null) return obj[k];
    return undefined;
  }
  function pairKey(a, b) { return [String(a || ""), String(b || "")].sort().join("~"); }
  function lexicalKey(word) { return String(word || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}_']/gu, ""); }
  function isVowel(phone) {
    return !!phone && /[aeiouæɐɑɒɔəɚɛɜɝɪʊʌᵻɨʉøœɵyɯɤ]/u.test(phone);
  }
  function plausible(expected, heard) {
    if (!expected || !heard) return false;
    return isVowel(expected) === isVowel(heard) || CROSS_CLASS_PLAUSIBLE.has(pairKey(expected, heard));
  }
  function referenceVariant(expected, heard) {
    return !!expected && !!heard && REFERENCE_PAIRS.has(pairKey(expected, heard));
  }
  function functionWord(word) { return FUNCTION_WORDS.has(String(word || "").toLowerCase()); }
  function parseFragmentWords(articleText) {
    const out = new Set();
    const text = String(articleText || "").normalize("NFKC");
    for (const m of text.matchAll(/([\p{L}\p{N}_]+)-\s+([\p{L}\p{N}_]+)/gu)) {
      out.add(m[1].toLowerCase()); out.add(m[2].toLowerCase());
    }
    return out;
  }
  function textKey(text) {
    const normalized = String(text || "").normalize("NFKC").toLowerCase()
      .replace(/[^\p{L}\p{N}_']+/gu, " ").trim();
    return sha256(normalized).slice(0, 16);
  }
  function readingOf(input) { return input && (input.reading || input); }
  function ids(r) {
    return {
      session_id: val(r, "session_id", "sessionId") || "",
      attempt_id: val(r, "attempt_id", "attemptId") || "",
      job_id: val(r, "job_id", "jobId") || "",
      segment_id: val(r, "segment_id", "segmentId") || "",
    };
  }
  function playbackRef(r, play, span, kind, extras = {}) {
    const i = ids(r);
    return { ...i, timeline: "analysis_wav", kind, play_ms: play ?? null, span_ms: span ?? null,
      url: `/api/sessions/${i.session_id}/attempts/${i.attempt_id}/audio`, ...extras };
  }
  function normaliseInput(input) {
    if (input?.session && input?.attempt && input?.job && input?.view) input = readerInput(input);
    const r = readingOf(input) || {};
    const ri = {
      ...r,
      reading_id: val(r, "reading_id", "readingId") || `${ids(r).session_id}:${ids(r).attempt_id}`,
      session_id: ids(r).session_id, attempt_id: ids(r).attempt_id, job_id: ids(r).job_id,
      segment_id: ids(r).segment_id, engine: val(r, "engine", "engine_id", "engineId") || "",
      recorded_at: val(r, "recorded_at", "recordedAt") || "",
      sentence_key: val(r, "sentence_key", "sentenceKey") || String(val(r, "sentence_text", "sentenceText") || ""),
      sentence_text: val(r, "sentence_text", "sentenceText") || "",
      analysis_state: val(r, "analysis_state", "analysisState") || "unknown",
      fluency_reliable: val(r, "fluency_reliable", "fluencyReliable"),
    };
    const observations = val(input, "coach_observations", "coachObservations") || [];
    const reductions = val(input, "reduction_candidates", "reductionCandidates") || [];
    const reductionById = new Map();
    for (const c of reductions) {
      const it = c.interpretation || {};
      reductionById.set(c.observation_id ?? c.observationId, {
        category: it.category, strength: val(c, "evidence_strength", "evidenceStrength"),
        contexts_present: it.contexts_present || it.contextsPresent || [],
        natural_possible: !!val(it, "natural_connected_speech_possible", "naturalConnectedSpeechPossible"),
        merge_suspect: !!val(c, "merge_suspect", "mergeSuspect"),
      });
    }
    const agreements = val(input, "engine_agreement", "engineAgreement") || {};
    const fragments = new Set(val(input, "fragment_words", "fragmentWords") || []);
    for (const w of parseFragmentWords(val(input, "article_text", "articleText") || val(input, "article")?.text)) fragments.add(w);
    const units = observations.map((o) => {
      const kind = o.kind;
      const outcome = OUTCOME[o.type] || "uninterpreted";
      let heard = outcome === "heard_other" ? (o.competitor || o.observed) : outcome === "extra" ? o.observed : null;
      const word = o.word || "";
      const lk = lexicalKey(word);
      const context = o.context || {};
      const m5 = reductionById.get(o.id) || null;
      const u = {
        unit_id: `${ri.session_id}:${ri.attempt_id}:${ri.job_id}:${o.id}`,
        reading_id: ri.reading_id, session_id: ri.session_id, sentence_key: ri.sentence_key,
        recorded_at: ri.recorded_at, observation_id: o.id, engine: ri.engine,
        coach_version: val(input, "coach_version", "coachVersion") ?? null, word, lexical_key: lk,
        word_index: val(o, "word_index", "wordIndex") ?? -1, function_word: functionWord(lk),
        fragment_suspect: lk.length < 3 || !/^[\p{L}\p{N}_']+$/u.test(lk) || fragments.has(lk),
        expected: kind === "sound" ? o.expected ?? null : null, heard, outcome,
        m4_type: o.type, m4_confidence: o.confidence,
        expected_posterior: val(o, "expected_posterior", "expectedPosterior") ?? null,
        competitor_posterior: val(o, "competitor_posterior", "competitorPosterior") ?? null,
        context: Object.fromEntries(["word_position", "previous_phone", "next_phone", "in_consonant_cluster",
          "sentence_position", "stress", "stress_known"].map((k) => [k, val(context, k, camel(k)) ?? null])),
        m5, engine_agreement: agreements[o.id] || "not_compared",
        audio: playbackRef(ri, val(o, "play_ms", "playMs"), val(o, "span_ms", "spanMs"), "sound", {
          word, observation_id: o.id, word_play_ms: val(o, "word_play_ms", "wordPlayMs") ?? null,
          timing_source: val(o, "timing_source", "timingSource") ?? null,
        }),
        quality: "excluded", reasons: [], exclusion: null, source: "M4",
      };
      assessUnit(u, ri);
      return u;
    });
    const flu = val(input, "fluency") || null;
    const fluency = [];
    if (flu && flu.state === "ok") {
      const reliable = !!(flu.metrics && val(flu.metrics, "activity_reliable", "activityReliable"));
      for (const o of flu.observations || []) {
        const group = FLUENCY_GROUP[o.type];
        if (!group || !o.notice) continue;
        const context = o.context || {};
        if (o.type === "PAUSE" && !["possible_hesitation", "unusually_long"].includes(o.classification)
            || o.type === "PAUSE" && String(context.position || "").includes("boundary")) continue;
        const pb = o.playback || {};
        fluency.push({
          unit_id: `${ri.session_id}:${ri.attempt_id}:${ri.job_id}:${o.id}`, reading_id: ri.reading_id,
          session_id: ri.session_id, sentence_key: ri.sentence_key, recorded_at: ri.recorded_at,
          observation_id: o.id, engine: ri.engine, group, type: o.type, classification: o.classification ?? null,
          label: o.label ?? null, strength: o.strength ?? null, word_before: val(context, "word_before", "wordBefore") ?? null,
          word_after: val(context, "word_after", "wordAfter") ?? null, position: context.position ?? null,
          reliable, audio: playbackRef(ri, val(pb, "play_ms", "playMs"), val(pb, "span_ms", "spanMs"), "fluency", {
            observation_id: o.id, context_ms: val(pb, "context_ms", "contextMs") ?? null, label: o.label ?? null,
          }),
          quality: reliable ? "confident" : "excluded", exclusion: reliable ? null : "unreliable_level", source: "M8",
        });
      }
    }
    return { reading: ri, units, fluency, coach_version: val(input, "coach_version", "coachVersion") ?? null };
  }
  function readerInput(source) {
    const { session, article, attempt, job, view } = source;
    const boundary = job.boundary || {};
    const targetRegion = (boundary.regions || []).find((region) => region.kind === "target" && region.play);
    const duration = val(view, "duration_ms", "durationMs") || attempt.audio?.duration_ms || 0;
    const sid = session.id, aid = attempt.id;
    return {
      reading: {
        reading_id: `${sid}:${aid}`, session_id: sid, attempt_id: aid, job_id: job.id,
        segment_id: attempt.segment_id, engine: job.engine_id || session.engine_default,
        recorded_at: attempt.created_at, sentence_key: textKey(attempt.target_text),
        sentence_text: attempt.target_text, article_key: article?.text_sha256 || session.article_id,
        article_id: session.article_id, practice_session: article?.source === "What to practise now",
        analysis_state: boundary.analysis?.state || "unknown",
        fluency_reliable: view.fluency?.metrics?.activity_reliable ?? null,
        has_comparison: !!source.engine_agreement,
        sentence_ref: targetRegion ? targetRegion.play : playbackRef({
          session_id: sid, attempt_id: aid, job_id: job.id, segment_id: attempt.segment_id,
        }, [0, duration], [0, duration], "sentence"),
      },
      coach_observations: view.coach?.observations || [],
      coach_version: view.coach?.version || null,
      reduction_candidates: view.reduction?.state === "ok" ? view.reduction.candidates || [] : [],
      fluency: view.fluency || null,
      engine_agreement: source.engine_agreement || {},
      article_text: article?.text || "",
    };
  }
  function camel(s) { return s.replace(/_([a-z])/g, (_, c) => c.toUpperCase()); }
  function assessUnit(u, reading) {
    if (u.outcome === "uninterpreted") { u.exclusion = "not_interpreted"; return; }
    if (u.outcome === "extra") { u.exclusion = "extra_sound"; return; }
    if (u.function_word && isVowel(u.expected)) { u.exclusion = "function_word_variant"; return; }
    if (u.outcome === "as_expected") { u.quality = "counter"; return; }
    if (u.outcome === "not_detected") {
      if (u.m5 && u.m5.merge_suspect) u.exclusion = "merge_suspect";
      else if (u.m5 && u.m5.natural_possible) u.exclusion = "context_predicted";
      else u.quality = "weak";
      return;
    }
    if (referenceVariant(u.expected, u.heard)) u.exclusion = "reference_variant";
    else if (!plausible(u.expected, u.heard)) u.exclusion = "implausible_pair";
    else if (u.heard === u.context.previous_phone || u.heard === u.context.next_phone) u.exclusion = "neighbour_shift";
    else if (u.m5 && u.m5.merge_suspect) u.exclusion = "merge_suspect";
    else if (u.m5 && (u.m5.natural_possible || u.m5.category === "possible_coarticulation")) u.exclusion = "context_predicted";
    else if (u.m4_type === "substitution_candidate" && ["high", "moderate"].includes(u.m4_confidence)) {
      if (reading.analysis_state === "low_confidence") {
        u.quality = "supporting";
        u.reasons.push("the sentence's analysis was low-confidence (M7), so this counts as support only");
      } else u.quality = "confident";
    } else {
      u.quality = "supporting";
      u.reasons.push("ambiguous decode (M4): support only");
    }
  }

  function unitRefOk(ref) {
    const p = ref && ref.play_ms;
    return Array.isArray(p) && p.length === 2 && p[0] != null && p[1] != null && 0 <= p[0] && p[0] < p[1];
  }
  function pick(units, n, evidence = false) {
    const sorted = units.slice().sort((a, b) => String(b.recorded_at).localeCompare(String(a.recorded_at))
      || String(b.unit_id).localeCompare(String(a.unit_id)));
    const rank = { confident: 0, supporting: 1 };
    sorted.sort((a, b) => (rank[a.quality] ?? 2) - (rank[b.quality] ?? 2));
    const out = [], seen = new Set();
    for (const u of sorted) {
      const k = u.lexical_key || u.unit_id;
      if (seen.has(k) || !unitRefOk(u.audio)) continue;
      seen.add(k);
      const ref = { ...u.audio, unit_id: u.unit_id };
      if (evidence) ref.evidence = u.quality === "confident" ? "clear" : u.quality === "supporting" ? "ambiguous" : "possible";
      out.push(ref);
      if (out.length === n) break;
    }
    return out;
  }
  function makeCounter(items) {
    const c = {};
    for (const item of items) c[item] = (c[item] || 0) + 1;
    return c;
  }
  function uniq(xs) { return [...new Set(xs)]; }
  function plural(n, singular, pluralForm = `${singular}s`) { return n === 1 ? singular : pluralForm; }
  function times(n) { return n === 1 ? "once" : n === 2 ? "twice" : `${n} times`; }
  function soundList(sounds) {
    const xs = sounds.map((s) => `/${s}/`);
    return xs.length < 2 ? (xs[0] || "") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
  }
  function round3(n) { return Math.round(n * 1000) / 1000; }
  function inScope(u, scope) {
    if (!scope || !scope.sounds.includes(u.expected)) return false;
    if (scope.word && u.lexical_key !== scope.word) return false;
    if (scope.condition === "final" && u.context.word_position !== "final") return false;
    return true;
  }
  function rateBand(rate) { return rate == null ? "none" : rate >= 0.4 ? "high" : rate >= 0.2 ? "moderate" : rate >= 0.1 ? "low" : "below"; }
  function countsFor(obs, allUnits, scope) {
    const clear = obs.filter((u) => u.quality === "confident");
    const ambiguous = obs.filter((u) => u.quality === "supporting");
    const real = new Set(obs.filter((u) => u.lexical_key && !u.fragment_suspect).map((u) => u.lexical_key));
    const fragments = new Set(obs.filter((u) => u.lexical_key && u.fragment_suspect).map((u) => u.lexical_key));
    for (const w of real) fragments.delete(w);
    const out = {
      observations: obs.length, clear: clear.length, ambiguous: ambiguous.length,
      clear_high: clear.filter((u) => u.m4_confidence === "high").length,
      clear_moderate: clear.filter((u) => u.m4_confidence !== "high").length,
      sentences: uniq(obs.map((u) => u.sentence_key)).length,
      clear_sentences: uniq(clear.map((u) => u.sentence_key)).length,
      clear_real_words: uniq(clear.filter((u) => u.lexical_key && !u.fragment_suspect).map((u) => u.lexical_key)).length,
      real_words: real.size, word_fragments: fragments.size,
    };
    if (!scope) return Object.assign(out, {
      occurrences: null, heard_as_expected: null, per_sound: null, rate_sound: null, clear_rate: null,
      observed_rate: null, rate_band: "none", direction_sound: null, clear_differences: null,
      same_direction: null, direction_share: null,
    });
    const occurrence = allUnits.filter((u) => ["counter", "confident", "supporting", "weak"].includes(u.quality) && inScope(u, scope));
    const per = {};
    for (const s of scope.sounds) {
      const ofSound = occurrence.filter((u) => u.expected === s);
      const c = clear.filter((u) => u.expected === s).length;
      per[s] = { clear: c, occurrences: ofSound.length, rate: ofSound.length ? round3(c / ofSound.length) : 0 };
    }
    const ranking = Object.keys(per).sort((a, b) => per[b].rate - per[a].rate || per[b].occurrences - per[a].occurrences || a.localeCompare(b));
    const top = ranking[0] || null;
    const dominant = Object.keys(per).sort((a, b) => per[b].clear - per[a].clear || per[b].rate - per[a].rate || a.localeCompare(b))[0] || null;
    const diffs = allUnits.filter((u) => u.quality === "confident" && u.outcome === "heard_other"
      && u.expected === dominant && inScope(u, scope));
    const pairs = new Set(scope.pairs || []);
    const same = pairs.size ? diffs.filter((u) => pairs.has(`${u.expected}→${u.heard}`)).length : dominant ? per[dominant].clear : 0;
    return Object.assign(out, {
      occurrences: occurrence.length, heard_as_expected: occurrence.filter((u) => u.quality === "counter").length,
      per_sound: per, rate_sound: top, clear_rate: top ? per[top].rate : 0,
      observed_rate: occurrence.length ? round3(obs.length / occurrence.length) : 0,
      rate_band: rateBand(top ? per[top].rate : 0), direction_sound: dominant, clear_differences: diffs.length,
      same_direction: same, direction_share: diffs.length ? round3(same / diffs.length) : null,
    });
  }
  function areaFor(kind, band, id, obs, allUnits, scope, sounds, pairs = []) {
    const counts = countsFor(obs, allUnits, scope);
    const byWord = new Map(), clearByWord = new Map();
    for (const u of obs) if (u.lexical_key && !u.fragment_suspect) {
      byWord.set(u.lexical_key, (byWord.get(u.lexical_key) || 0) + 1);
      if (u.quality === "confident") clearByWord.set(u.lexical_key, (clearByWord.get(u.lexical_key) || 0) + 1);
    }
    const words = [...byWord].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([word, observations]) => ({ word, observations, clear: clearByWord.get(word) || 0 }));
    const allCounters = scope ? allUnits.filter((u) => u.quality === "counter" && inScope(u, scope)) : [];
    const unitIds = obs.map((u) => u.unit_id).sort();
    const directions = uniq(obs.filter((u) => u.heard).map((u) => `${u.expected}→${u.heard}`));
    const twoWay = directions.length === 2 && directions.includes(`${sounds[0]}→${sounds[1]}`)
      && directions.includes(`${sounds[1]}→${sounds[0]}`);
    return {
      id, kind, band, path: band === "clear" ? "A" : band === "mixed" ? "B" : "C", origin: "counted",
      pattern_scope: kind === "CLARITY" ? "possible" : "sound",
      pattern_label: kind === "CLARITY" ? "Possible (final sounds)" : "Recurring sound pattern",
      sounds, pairs: pairs.map((p) => `${p[0]}→${p[1]}`), two_way: twoWay, word: null, condition: null,
      condition_label: null, family: null, scope, counts, words,
      unit_ids: unitIds, examples: pick(obs, READING_CAL.n_examples, true),
      counter_examples: pick(allCounters, READING_CAL.n_counter),
    };
  }
  function orderKey(area) {
    const c = area.counts;
    const band = { clear: 0, mixed: 1, possible: 2 };
    const scope = { sound: 0, context: 1, word: 2, possible: 3 };
    const rate = { high: 0, moderate: 1, low: 2, none: 3 };
    return [band[area.band], scope[area.pattern_scope], rate[c.rate_band] ?? 9,
      -c.sentences, -c.real_words, -c.clear, area.id];
  }
  function decidedBy(a, b) {
    const names = ["evidence", "pattern_scope", "clear_rate_band", "sentences", "real_words", "clear_observations", "id"];
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return names[i];
    return null;
  }
  function areaScopeText(area, sounds = area.scope.sounds) {
    return soundList(sounds) + (area.scope.word ? ` in ‘${area.scope.word}’` : "")
      + (area.scope.condition === "final" ? " at the end of words" : "");
  }
  function dominantPair(area) {
    const pairs = makeCounter(area.unit_ids.map((id) => {
      const u = area._units.find((x) => x.unit_id === id);
      return u ? `${u.expected}→${u.heard}` : "";
    }));
    return Object.entries(pairs).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0]?.split("→") || area.pairs[0]?.split("→") || [];
  }
  function areaHeadline(area) {
    const c = area.counts;
    if (area.kind === "CLARITY") return `In this reading, final sounds appeared weakened or were not clearly detected in ${c.real_words} ${plural(c.real_words, "word")} (possible: not detected does not prove a sound was absent).`;
    const where = `${times(c.observations)} in ${c.sentences} ${plural(c.sentences, "sentence")}`;
    const [a, b] = dominantPair(area);
    const what = area.kind === "CONTRAST"
      ? (area.two_way ? `/${a}/ and /${b}/ were heard as each other` : `/${a}/ was heard as /${b}/`)
      : `${soundList(area.sounds)} was heard differently`;
    return `In this reading, ${what} ${where}.`;
  }
  function areaEvidenceText(area) {
    const c = area.counts;
    if (area.band === "possible") return `Individual observations: ${c.observations} possible (M5: weakened or not clearly detected), not counted as clear or ambiguous differences.`;
    const words = `${c.real_words} ${plural(c.real_words, "word")}${c.word_fragments ? ` and ${c.word_fragments} ${plural(c.word_fragments, "word fragment")}` : ""}`;
    return `Individual observations: ${c.observations} observed ${plural(c.observations, "difference")} in ${words}: ${c.clear} clear (${c.clear_high} high-confidence, ${c.clear_moderate} moderate-confidence) and ${c.ambiguous} ambiguous.`;
  }
  function areaPatternText(area) {
    const c = area.counts;
    if (area.pattern_scope === "possible") return "Pattern: possible only; not detected does not prove a sound was absent.";
    const [d] = dominantPair(area);
    const direction = c.direction_share == null ? "" : ` Of the ${c.clear_differences} clear ${plural(c.clear_differences, "difference")} of ${areaScopeText(area, [c.direction_sound])} in this reading, ${c.same_direction} went this way.`;
    if (area.pattern_scope === "word") return "";
    const clearWhere = `${c.clear_sentences} ${plural(c.clear_sentences, "sentence")} and ${c.clear_real_words} ${plural(c.clear_real_words, "word")}`;
    if (area.band === "mixed") return `Pattern: recurring, partly ambiguous. Heard clearly ${times(c.clear)} in the same direction, in ${clearWhere}, supported by ${c.ambiguous} ambiguous ${plural(c.ambiguous, "observation")} that ${c.ambiguous === 1 ? "is" : "are"} not counted as clear.${direction}`;
    return `Pattern: recurring. Heard clearly ${times(c.clear)} in the same direction, in ${clearWhere}.${direction}`;
  }
  function areaRateText(area) {
    const c = area.counts;
    if (c.clear_rate == null) return null;
    const top = c.rate_sound;
    const others = Object.entries(c.per_sound).filter(([s]) => s !== top).sort(([a], [b]) => a.localeCompare(b))
      .map(([s, v]) => `${areaScopeText(area, [s])}: ${v.clear} of ${v.occurrences}`);
    return `Clear-evidence rate: ${c.per_sound[top].clear} of ${c.per_sound[top].occurrences} occurrences of ${areaScopeText(area, [top])}${others.length ? ` (${others.join("; ")})` : ""}.`;
  }
  function buildAreas(allUnits, nSent) {
    if (nSent < READING_CAL.min_sentences) return { areas: [], absorbed: [] };
    const diff = allUnits.filter((u) => u.outcome === "heard_other" && ["confident", "supporting"].includes(u.quality));
    const byUnordered = new Map();
    for (const u of diff) {
      const key = pairKey(u.expected, u.heard);
      if (!byUnordered.has(key)) byUnordered.set(key, { sounds: [u.expected, u.heard].sort(), units: [] });
      byUnordered.get(key).units.push(u);
    }
    const areas = [];
    for (const [pair, group] of byUnordered) {
      const sounds = group.sounds;
      const scope = { sounds, condition: null, word: null, pairs: group.units.map((u) => `${u.expected}→${u.heard}`) };
      const counts = countsFor(group.units, allUnits, scope);
      const directed = makeCounter(group.units.filter((u) => u.quality === "confident").map((u) => `${u.expected}→${u.heard}`));
      const totalClear = Object.values(directed).reduce((a, b) => a + b, 0);
      const majorDirection = Object.entries(directed).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0];
      const directionShare = totalClear && majorDirection ? majorDirection[1] / totalClear : 0;
      const clearWords = uniq(group.units.filter((u) => u.quality === "confident" && !u.fragment_suspect).map((u) => u.lexical_key)).length;
      const pathA = counts.clear >= READING_CAL.k_conf && counts.clear_sentences >= READING_CAL.s_min
        && clearWords >= READING_CAL.w_min && counts.clear_rate >= READING_CAL.rate_min
        && directionShare >= READING_CAL.c_min;
      const pathB = !pathA
        && counts.observations >= READING_CAL.mixed_min_observations && counts.clear >= READING_CAL.mixed_min_clear
        && counts.sentences >= READING_CAL.mixed_min_sentences && counts.real_words >= READING_CAL.mixed_min_real_words
        && counts.clear_rate >= READING_CAL.rate_min && directionShare >= READING_CAL.c_min;
      if (!pathA && !pathB) continue;
      const pairs = [...new Set(group.units.map((u) => [u.expected, u.heard]))];
      const area = areaFor("CONTRAST", pathA ? "clear" : "mixed", `${pathA ? "" : "mixed:"}contrast:${sounds.join("~")}`,
        group.units, allUnits, scope, sounds, pairs);
      area._units = group.units;
      if (pathA) area.gates = { confident: { value: counts.clear, required: READING_CAL.k_conf, ok: true } };
      areas.push(area);
    }
    const clarity = allUnits.filter((u) => u.quality === "weak" && u.m5
      && ["possible_omission", "possible_weakening"].includes(u.m5.category)
      && !u.m5.natural_possible && !u.m5.merge_suspect && u.context.word_position === "final");
    const realWords = uniq(clarity.filter((u) => !u.fragment_suspect).map((u) => u.lexical_key)).length;
    if (realWords >= READING_CAL.clarity_min_words) {
      const sounds = uniq(clarity.map((u) => u.expected)).sort();
      const area = areaFor("CLARITY", "possible", "clarity:final", clarity, allUnits, null, sounds);
      area._units = clarity;
      area.gates = { words: { value: realWords, required: READING_CAL.clarity_min_words, ok: true } };
      areas.push(area);
    }
    areas.sort((a, b) => {
      const band = { clear: 0, mixed: 1, possible: 2 };
      const scope = { sound: 0, context: 1, word: 2, possible: 3 };
      const rate = { high: 0, moderate: 1, low: 2, none: 3 };
      const x = a.counts, y = b.counts;
      return band[a.band] - band[b.band] || scope[a.pattern_scope] - scope[b.pattern_scope]
        || (rate[x.rate_band] ?? 9) - (rate[y.rate_band] ?? 9) || y.sentences - x.sentences
        || y.real_words - x.real_words || y.clear - x.clear || a.id.localeCompare(b.id);
    });
    return { areas, absorbed: [] };
  }
  function areaSoundSet(areas) {
    const out = new Set();
    for (const a of areas) {
      for (const s of a.sounds) out.add(s);
      for (const p of a.pairs) for (const s of p.split("→")) out.add(s);
    }
    return out;
  }
  function buildReadingFeedback(inputs) {
    const parsed = Array.from(inputs || [], normaliseInput);
    const readings = parsed.map((p) => p.reading);
    const allUnits = parsed.flatMap((p) => p.units);
    const fluencyUnits = parsed.flatMap((p) => p.fluency);
    const nSent = uniq(readings.map((r) => r.sentence_key)).length;
    const built = buildAreas(allUnits, nSent);
    const shown = built.areas;
    for (let i = 0; i < shown.length; i++) {
      const area = shown[i], next = shown[i + 1] || null;
      area.ordering = { position: i + 1, key: orderKey(area),
        placed_above_next_by: next ? decidedBy(orderKey(area), orderKey(next)) : null, next: next ? next.id : null };
      area.text = areaHeadline(area);
      area.evidence_text = areaEvidenceText(area);
      area.pattern_text = areaPatternText(area);
      area.rate_text = areaRateText(area);
      area.counter_text = area.counts.heard_as_expected
        ? `${soundList(area.scope.sounds)} ${area.scope.sounds.length === 1 ? "was" : "were"} heard as expected ${times(area.counts.heard_as_expected)} in this reading.` : null;
      const by = area.ordering.placed_above_next_by;
      const reasons = { evidence: "clearer evidence", pattern_scope: "a broader pattern",
        clear_rate_band: "a higher clear-evidence rate", sentences: "recurrence in more sentences",
        real_words: "recurrence in more words", clear_observations: "more clear observations",
        id: "equal evidence on every criterion (a fixed tie-break)" };
      area.order_text = by ? `Placed before area ${i + 2} for ${reasons[by]}.` : null;
      delete area._units;
    }
    const used = new Set(shown.flatMap((a) => a.unit_ids));
    const rest = allUnits.filter((u) => ["confident", "supporting"].includes(u.quality)
      && u.outcome === "heard_other" && !used.has(u.unit_id));
    const otherClear = rest.filter((u) => u.quality === "confident").length;
    const otherAmbiguous = rest.filter((u) => u.quality === "supporting").length;
    const exclusions = makeCounter(allUnits.filter((u) => u.exclusion).map((u) => u.exclusion));
    const strengths = [];
    if (nSent >= READING_CAL.min_sentences) {
      const byExpected = new Map();
      for (const u of allUnits) if (u.expected && ["counter", "confident", "supporting", "weak"].includes(u.quality)) {
        if (!byExpected.has(u.expected)) byExpected.set(u.expected, []);
        byExpected.get(u.expected).push(u);
      }
      for (const [sound, us] of byExpected) {
        if (!STRENGTH_SOUNDS.has(sound) || areaSoundSet(shown).has(sound)) continue;
        const ok = us.filter((u) => u.quality === "counter"), differences = us.length - ok.length;
        if (us.length >= READING_CAL.strength_min_occurrences && ok.length / us.length >= READING_CAL.strength_min_share
            && differences <= READING_CAL.strength_max_differences) {
          strengths.push({ origin: "counted", sound, counts: { occurrences: us.length, heard_as_expected: ok.length, differences },
            text: `In this reading, /${sound}/ was heard as expected in ${ok.length} of ${us.length} occurrences.`,
            examples: pick(ok, 2) });
        }
      }
      strengths.sort((a, b) => b.counts.occurrences - a.counts.occurrences
        || b.counts.heard_as_expected / b.counts.occurrences - a.counts.heard_as_expected / a.counts.occurrences
        || a.sound.localeCompare(b.sound));
      strengths.splice(READING_CAL.max_strengths);
    }
    const countedFluency = fluencyUnits.filter((f) => f.quality === "confident");
    const fluGroups = makeCounter(countedFluency.map((f) => f.group));
    const chosenFlu = Object.entries(fluGroups).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0];
    let fluency = null, fluencyNote = null;
    const reliableReadings = readings.filter((r) => r.fluency_reliable);
    if (chosenFlu && chosenFlu[1] >= READING_CAL.fluency_min) {
      const [group, n] = chosenFlu;
      const obs = countedFluency.filter((f) => f.group === group);
      const sents = uniq(obs.map((f) => f.sentence_key)).length;
      const what = { hesitation: "possible hesitation pauses were noticed inside phrases",
        filler: "possible filler sounds were noticed between words",
        repetition: "possible repetitions or restarts were noticed" }[group];
      fluency = { group, origin: "counted", text: `In this reading, ${what} ${times(n)}, in ${sents} ${plural(sents, "sentence")}.`,
        counts: { noticed: n, sentences: sents }, reliable_sentences: reliableReadings.length,
        examples: pick(obs, READING_CAL.n_examples), unit_ids: obs.map((f) => f.unit_id).sort() };
    } else if (nSent >= READING_CAL.min_sentences && reliableReadings.length === readings.length && readings.length
        && !countedFluency.some((f) => f.group === "hesitation")) {
      fluencyNote = { origin: "counted", basis: { reliable_sentences: reliableReadings.length },
        text: "In this reading, no possible hesitation pauses were noticed inside phrases." };
    }
    const metadata = inputs && inputs.coverage ? inputs : {};
    const coverage = metadata.coverage || {};
    const cautions = [];
    const pluralText = (n) => ({ n: String(n), s: n === 1 ? "" : "s", is_are: n === 1 ? "is" : "are",
      was_were: n === 1 ? "was" : "were", it_they: n === 1 ? "it" : "they", its_their: n === 1 ? "its" : "their" });
    const caution = (code, text) => { if (text) cautions.push({ code, text }); };
    const withheld = Number(coverage.feedback_withheld || 0) + Number(coverage.feedback_withheld_containment || 0);
    if (withheld) { const p = pluralText(withheld); caution("withheld_sentences", `${p.n} sentence${p.s} of this reading could not be separated from what followed, so ${p.it_they} ${p.is_are} not described here.`); }
    const awaiting = Number(coverage.awaiting_decision || 0);
    if (awaiting) { const p = pluralText(awaiting); caution("unconfirmed_identity", `${p.n} sentence${p.s} ${p.is_are} waiting for you to confirm ${p.it_they} ${p.is_are} the right sentence, so ${p.it_they} ${p.is_are} not described here.`); }
    const levelUnmeasurable = readings.filter((r) => r.fluency_reliable === false).length;
    if (levelUnmeasurable) { const p = pluralText(levelUnmeasurable); caution("level_unmeasurable", `The speech level could not be measured in ${p.n} sentence${p.s}, so pauses there are not described.`); }
    const lowConfidence = readings.filter((r) => r.analysis_state === "low_confidence").length;
    if (lowConfidence) { const p = pluralText(lowConfidence); caution("low_confidence_analysis", `${p.n} sentence${p.s} ${p.was_were} decoded unclearly; ${p.its_their} observations count as support only.`); }
    if (readings.length && nSent < READING_CAL.min_sentences) { const p = pluralText(nSent); caution("few_sentences", `Only ${p.n} sentence${p.s} ${p.was_were} described, too few to describe recurring patterns.`); }
    const refVariants = exclusions.reference_variant || 0;
    if (refVariants) { const p = pluralText(refVariants); caution("reference_accent", `${p.n} difference${p.s} that may reflect the reference accent (eSpeak en-us) ${p.was_were} not counted.`); }
    const covText = Object.keys(coverage).length
      ? `${coverage.recorded ?? nSent} of ${coverage.sentences ?? nSent} sentences recorded; ${nSent} described here.` : null;
    const provenanceInputs = readings.map((r) => ({ attempt_id: r.attempt_id, job_id: r.job_id, segment_id: r.segment_id }));
    const provenance = { inputs: provenanceInputs,
      fingerprint: sha256(stableStringify(provenanceInputs)).slice(0, 20) };
    const result = {
      version: READING_VERSION, scope: "this_reading", session_id: readings[0]?.session_id || metadata.session_id || "",
      article_title: metadata.article_title || null, generated_at: metadata.generated_at || null,
      calibration: { ...READING_CAL }, knowledge_version: KNOWLEDGE_VERSION,
      provenance, coverage: { sentences: coverage.sentences ?? null, recorded: coverage.recorded ?? null,
        in_feedback: nSent, withheld, awaiting_decision: awaiting, text: covText },
      improvement_areas: shown, no_area_text: nSent >= 2 && !shown.length ? NO_AREA_TEXT : null,
      absorbed: built.absorbed, strengths, fluency, fluency_note: fluencyNote,
      cautions, other_differences: { count: rest.length, clear: otherClear, ambiguous: otherAmbiguous,
        note: rest.length ? `${rest.length} other difference${rest.length === 1 ? "" : "s"} in this reading (${otherClear} clear, ${otherAmbiguous} ambiguous) did not form a pattern strong enough to call out; the detailed report below lists them.` : null },
      caveat: GENERAL_CAVEAT, state: nSent < 2 ? "insufficient" : "feedback",
      integrity: { ok: true, issues: [] },
      detail: { units: allUnits, fluency_units: fluencyUnits, exclusions },
    };
    return result;
  }

  function readingText(text) {
    return String(text || "").replaceAll("this recording", "this reading").replaceAll("This recording", "This reading");
  }
  function summaryPatternKey(observation) {
    const type = observation.type;
    if (!["substitution_candidate", "ambiguous", "omission_candidate", "weak_evidence", "insertion"].includes(type)) return null;
    if (type === "insertion") return ["insertion", null, observation.observed];
    if (["omission_candidate", "weak_evidence"].includes(type)) return ["detection", observation.expected, null];
    return ["contrast", observation.expected, observation.competitor];
  }
  function summaryPatternLine(kind, expected, contrast, count, total, elsewhere, patternClass, context, uncertain) {
    if (kind === "insertion") return `The recogniser decoded an extra /${contrast}/ ${count} time${count > 1 ? "s" : ""} in this recording.`;
    let text;
    if (kind === "detection") {
      text = `The recogniser did not clearly detect /${expected}/ in ${count} of ${total} occurrence${total > 1 ? "s" : ""}. Not detected does not prove the sound was absent.`;
    } else if (uncertain === count) {
      text = `In ${count} of ${total} occurrence${total > 1 ? "s" : ""} of /${expected}/, the evidence is ambiguous between /${expected}/ and /${contrast}/.`;
    } else {
      text = `In ${count} of ${total} occurrence${total > 1 ? "s" : ""} of /${expected}/, the acoustic/recognition evidence is more consistent with /${contrast}/.`;
    }
    if (elsewhere) text += ` /${expected}/ was heard as expected in ${elsewhere} other occurrence${elsewhere > 1 ? "s" : ""}.`;
    if (patternClass === "one_off") text += " Single observation — monitor for recurrence.";
    else if (patternClass === "context_specific") text += ` Only seen word-${context}, so this is specific to that context, not a general /${expected}/ pattern.`;
    else if (patternClass === "consistent") text += " Recurring pattern in this recording.";
    return text;
  }
  function summaryPatterns(observations) {
    const byKey = new Map();
    for (const observation of observations) {
      const key = summaryPatternKey(observation);
      if (!key) continue;
      const serialized = JSON.stringify(key);
      if (!byKey.has(serialized)) byKey.set(serialized, { key, support: [] });
      byKey.get(serialized).support.push(observation);
    }
    const interpretable = observations.filter((observation) =>
      observation.kind === "sound" && observation.type !== "not_interpreted");
    const patterns = [];
    for (const { key: [kind, expected, contrast], support } of byKey.values()) {
      const samePhone = expected ? interpretable.filter((observation) => observation.expected === expected) : [];
      const asExpected = samePhone.filter((observation) => observation.type === "expected");
      const positions = uniq(support.map((observation) => observation.context?.word_position));
      const words = uniq(support.map((observation) => observation.word));
      const count = support.length;
      const high = support.filter((observation) => observation.confidence === "high").length;
      const uncertain = support.filter((observation) => ["ambiguous", "weak_evidence"].includes(observation.type)).length;
      let patternClass, context;
      if (count === 1) {
        patternClass = "one_off";
        context = null;
      } else {
        context = positions.length === 1 ? positions[0] : null;
        const elsewherePositions = new Set(asExpected.map((observation) => observation.context?.word_position));
        if (context !== null && kind !== "insertion" && [...elsewherePositions].some((position) => position !== context)) {
          patternClass = "context_specific";
        } else if (count >= 3 && words.length >= 2 && high >= 2 && count >= asExpected.length) {
          patternClass = "consistent";
        } else patternClass = "repeated";
      }
      let group = kind === "insertion" ? "insertion" : kind === "detection" ? "not_detected" :
        uncertain === count ? "ambiguous" : count > 1 ? "recurring" : "single";
      let strength = high === count ? "strong" : uncertain === count ? "ambiguous" : high ? "mixed" : "moderate";
      if (kind === "detection") strength = "not_detected";
      else if (kind === "insertion") strength = "low";
      const total = expected ? samePhone.length : count;
      const firstMs = support.map((observation) => observation.span_ms?.[0]).filter((value) => value !== null && value !== undefined);
      const referenceNote = kind === "contrast" && referenceVariant(expected, contrast) ? SUMMARY_REFERENCE_NOTE :
        kind === "contrast" && isVowel(expected) && words.length > 0 && words.every((word) => FUNCTION_WORDS.has(word)) ?
          FUNCTION_WORD_NOTE : null;
      patterns.push({
        id: `p${String(patterns.length).padStart(2, "0")}`, kind, expected, contrast, class: patternClass,
        context, group, evidence_strength: strength, occurrences: count, high_confidence_occurrences: high,
        uncertain_occurrences: uncertain, occurrences_of_expected_phoneme: total,
        heard_as_expected_elsewhere: asExpected.length, words,
        word_positions: positions.filter(Boolean).sort(), observation_ids: support.map((observation) => observation.id),
        counter_evidence_ids: asExpected.map((observation) => observation.id),
        first_ms: firstMs.length ? Math.min(...firstMs) : null,
        reference_note: referenceNote,
        summary: summaryPatternLine(kind, expected, contrast, count, total, asExpected.length, patternClass, context, uncertain),
      });
    }
    const groups = [];
    for (const [id, title, explanation] of SUMMARY_GROUPS) {
      const members = patterns.filter((pattern) => pattern.group === id).sort((a, b) =>
        (a.first_ms === null) - (b.first_ms === null) ||
        (a.first_ms ?? 0) - (b.first_ms ?? 0) || a.id.localeCompare(b.id));
      if (members.length) groups.push({ id, title, explanation, pattern_ids: members.map((pattern) => pattern.id) });
    }
    return [patterns, groups];
  }
  function summaryPlayback(attempt, job, play, span, kind, extra = {}) {
    return playbackRef({
      session_id: attempt.session_id, attempt_id: attempt.id, job_id: job.id, segment_id: attempt.segment_id,
    }, play, span, kind, extra);
  }
  function summaryEligibility(attempt, job, engine) {
    const state = attempt.state, disposition = attempt.user_disposition;
    let identity;
    if (state === "TOO_SHORT") identity = "TOO_SHORT";
    else if (["ANALYSIS_FAILED", "REJECTED", "INTERRUPTED"].includes(state)) identity = "FAILED";
    else if (state !== "ANALYZED" || !job || job.state !== "SUCCEEDED") {
      identity = ["CAPTURING", "RECORDED", "QUEUED", "ANALYZING"].includes(state) ? "PENDING" : "FAILED";
    } else identity = job.target_confirmation?.state || "NOT_APPLICABLE";
    const boundary = job?.boundary || {}, withheld = Boolean(boundary.feedback_withheld);
    const reason = withheld ? (boundary.withheld_reason || "boundary") : null;
    let feedback;
    if (["TOO_SHORT", "FAILED", "PENDING", "NOT_APPLICABLE", "NOT_CHECKED"].includes(identity)) feedback = "none";
    else if (withheld) feedback = reason === "containment" ? "withheld_containment" : "withheld_boundary";
    else if (identity === "MISMATCH") feedback = "hidden_identity";
    else feedback = "shown";
    let summaryCode;
    if (disposition === "discarded") summaryCode = "discarded";
    else if (disposition === "rerecord_requested") summaryCode = "rerecord";
    else if (feedback === "none") summaryCode = "not_analysed";
    else if (engine != null && job?.engine_id !== engine) summaryCode = "other_engine";
    else if (identity === "MISMATCH") summaryCode = "different_sentence";
    else if (withheld) summaryCode = reason === "containment" ? "containment" : "boundary";
    else if (["MATCH", "LIKELY_MATCH"].includes(identity) || disposition === "kept") summaryCode = null;
    else summaryCode = "unconfirmed";
    const identityGroup = ["MATCH", "LIKELY_MATCH"].includes(identity) ||
      (identity === "AMBIGUOUS" && disposition === "kept") ? "identified" :
      identity === "AMBIGUOUS" ? "uncertain" : identity === "MISMATCH" ? "different" : "unusable";
    const recorded = ["MATCH", "LIKELY_MATCH", "AMBIGUOUS"].includes(identity) && disposition !== "discarded";
    const needsDecision = ["AMBIGUOUS", "MISMATCH"].includes(identity) && disposition !== "kept" &&
      !["discarded", "rerecord_requested"].includes(disposition);
    return {
      identity, feedback, summaryCode, identityGroup, recorded, needsDecision,
      analysis: withheld ? null : boundary.analysis?.state || null,
    };
  }
  const SUMMARY_REASON_TEXT = {
    discarded: "discarded", rerecord: "marked for re-recording", not_analysed: "not analysed",
    other_engine: "analysed by another engine", different_sentence: "appears to contain a different sentence",
    boundary: "sentence boundary uncertain — recording preserved, feedback withheld",
    containment: "analysis not contained in the sentence — recording preserved, feedback withheld",
    unconfirmed: "could not confirm it is this sentence — keep it to include it",
  };
  const SUMMARY_FLUENCY_GROUPS = [
    ["LONG_PAUSE", "long pause", "long pauses"], ["PAUSE", "possible hesitation pause", "possible hesitation pauses"],
    ["FILLER", "possible filler", "possible fillers"], ["REPETITION", "possible repetition", "possible repetitions"],
    ["RESTART", "possible restart", "possible restarts"], ["FALSE_START", "possible false start", "possible false starts"],
    ["OTHER_HESITATION", "possible hesitation", "possible hesitations"], ["RATE_ANOMALY", "change of pace", "changes of pace"],
  ];
  function fluencyGroup(observation) {
    return observation.type === "PAUSE" && observation.classification === "unusually_long"
      ? "LONG_PAUSE" : observation.type;
  }
  function summaryFluency(used, segmentNumbers) {
    const counts = {}, where = new Map(), examples = [], rates = [];
    let analysed = 0;
    for (const entry of used) {
      const fluency = entry.view.fluency || {};
      if (fluency.state !== "ok") continue;
      analysed += 1;
      const sentence = segmentNumbers.get(entry.attempt.segment_id);
      const metrics = fluency.metrics || {};
      if (metrics.rate_available && metrics.speaking_rate != null) {
        rates.push([metrics.speaking_rate, sentence]);
      }
      for (const observation of fluency.observations || []) {
        if (!observation.notice) continue;
        const group = fluencyGroup(observation);
        counts[group] = (counts[group] || 0) + 1;
        if (!where.has(group)) where.set(group, new Set());
        where.get(group).add(sentence);
        const strengthOrder = { moderate: 0, low: 1, ambiguous: 2, insufficient: 3 };
        examples.push({
          order: strengthOrder[observation.strength] ?? 9,
          duration: -(observation.duration_ms || 0), sentence, entry, observation,
        });
      }
    }
    examples.sort((a, b) => a.order - b.order || a.duration - b.duration || a.sentence - b.sentence);
    const kinds = SUMMARY_FLUENCY_GROUPS.filter(([group]) => counts[group]).map(([group, singular, pluralForm]) => ({
      kind: group, label: counts[group] === 1 ? singular : pluralForm, count: counts[group],
      sentences: [...where.get(group)].sort((a, b) => a - b),
    }));
    const recurring = kinds.filter((item) => item.sentences.length >= 2)
      .map(({ kind, label, sentences }) => ({ kind, label, sentences }));
    const notice = Object.values(counts).reduce((total, count) => total + count, 0);
    const noticedSentences = new Set([...where.values()].flatMap((set) => [...set]));
    const speechRate = rates.length ? {
      min: Math.min(...rates.map(([rate]) => rate)), max: Math.max(...rates.map(([rate]) => rate)),
      sentences: rates.length, unit: "syllables per second",
    } : null;
    const result = {
      engine_note: "From each included sentence's own analysis; continued speech after a sentence is never counted.",
      sentences_analysed: analysed, notice, sentences_with_notice: noticedSentences.size, kinds, recurring,
      examples: examples.slice(0, 3).map(({ sentence, entry, observation }) => summaryPlayback(
        entry.attempt, entry.primary, observation.playback?.play_ms, observation.playback?.span_ms, "fluency", {
          sentence, label: observation.label, observed: observation.observed, strength: observation.strength,
          context_ms: observation.playback?.context_ms,
        })),
      speech_rate: speechRate,
    };
    const rateText = speechRate ? (Math.abs(speechRate.max - speechRate.min) < 0.05
      ? `speech rate ${speechRate.min.toFixed(1)} syllables per second`
      : `speech rate ${speechRate.min.toFixed(1)}–${speechRate.max.toFixed(1)} syllables per second`) : null;
    const textParts = !analysed ? ["No fluency evidence among the sentences included."] : [
      notice ? `${notice} fluency thing${notice === 1 ? "" : "s"} to notice in ${noticedSentences.size} of ${analysed} sentence${analysed === 1 ? "" : "s"}` :
        `Nothing stood out in the timing of ${analysed} sentence${analysed === 1 ? "" : "s"}`,
      ...recurring.map((item) => `${item.label} in ${item.sentences.length} sentences`),
      ...(rateText ? [rateText] : []),
    ];
    result.text = !analysed ? textParts[0] : `${textParts.join(" · ")}.`;
    return result;
  }
  async function loadSummaryInputs(store, session, attempts) {
    const out = [];
    for (const attempt of attempts) {
      const jobs = [];
      for (const jobId of attempt.job_ids || []) {
        try { jobs.push(await store.loadJob(session.id, attempt.id, jobId)); } catch { /* absent job */ }
      }
      const primary = jobs.slice().reverse().find((job) => job.kind === "primary") || null;
      out.push({
        attempt, jobs, primary,
        view: primary ? await store.loadView(primary).catch(() => null) : null,
      });
    }
    return out;
  }
  function summaryGuidance(expected, contrast) {
    const key = pairKey(expected, contrast);
    return SUMMARY_GUIDANCE.find(([pair]) => pairKey(pair[0], pair[1]) === key)?.[1] || null;
  }
  async function buildSummary(store, session, article, provided = null) {
    if (store && !store.loadAttempt && typeof store === "object" && store.store && store.session) {
      const input = store;
      store = input.store; session = input.session; article = input.article;
      provided = input.attempts || input.inputs || null;
    }
    if (!store || !session || !article) throw new TypeError("buildSummary requires a store, session, and article");
    const sessionId = session.id, engine = session.engine_default;
    const segments = article.segments || [];
    const segmentIndex = new Map(segments.map((segment, index) =>
      [segment.id, segment.index ?? index]));
    let rows;
    if (provided) {
      rows = [];
      for (const supplied of provided) {
        const attempt = supplied.attempt || supplied;
        let job = supplied.job || supplied.primary_job || null;
        const jobs = supplied.jobs || {};
        if (!job && Array.isArray(supplied.jobs)) {
          job = supplied.jobs.slice().reverse().find((item) => item.kind === "primary") || null;
        } else if (!job && supplied.jobs && !Array.isArray(supplied.jobs)) {
          job = (attempt.job_ids || []).map((id) => jobs[id]).filter((item) => item?.kind === "primary").pop() || null;
        }
        if (!job && attempt.job_ids) {
          const loaded = await loadSummaryInputs(store, session, [attempt]);
          job = loaded[0].primary;
          rows.push({ ...loaded[0], supplied });
          continue;
        }
        const view = supplied.view ?? (job ? await store.loadView(job).catch(() => null) : null);
        rows.push({ attempt, primary: job, view, supplied });
      }
    } else {
      const attempts = await Promise.all((session.attempt_ids || []).map((id) => store.loadAttempt(sessionId, id)));
      rows = await loadSummaryInputs(store, session, attempts);
    }
    const bySegment = new Map();
    for (const row of rows) {
      const segmentId = row.attempt.segment_id;
      if (!bySegment.has(segmentId)) bySegment.set(segmentId, []);
      bySegment.get(segmentId).push(row);
    }
    const used = [], excluded = [];
    const reading = {
      recorded: 0, identified: 0, uncertain: 0, different: 0, unusable: 0,
      feedback_withheld: 0, feedback_withheld_containment: 0, awaiting_decision: 0,
      feedback_low_confidence: 0,
    };
    for (const [segmentId, attempts] of bySegment) {
      let chosen = null;
      const statuses = [];
      const reasons = [];
      for (const row of attempts.slice().reverse()) {
        const status = summaryEligibility(row.attempt, row.primary, engine);
        statuses.push({ row, status });
        if (status.summaryCode === null) {
          chosen = row;
          break;
        }
        reasons.push(SUMMARY_REASON_TEXT[status.summaryCode] || status.summaryCode);
      }
      if (chosen) used.push(chosen);
      else {
        const segmentNumber = segmentIndex.get(segmentId);
        excluded.push({
          segment_id: segmentId, sentence: segmentNumber === undefined ? null : segmentNumber + 1,
          reason: reasons[0] || "not analysed",
        });
      }
      const current = statuses.filter(({ row }) =>
        !["discarded", "rerecord_requested"].includes(row.attempt.user_disposition));
      const representative = chosen ? statuses[statuses.length - 1].status :
        (current.length ? current[0].status : statuses[0].status);
      reading.recorded += statuses.some(({ status }) => status.recorded) ? 1 : 0;
      reading[representative.identityGroup] += 1;
      reading.feedback_withheld += !chosen && representative.feedback === "withheld_boundary" ? 1 : 0;
      reading.feedback_withheld_containment += !chosen && representative.feedback === "withheld_containment" ? 1 : 0;
      reading.feedback_low_confidence += chosen && representative.analysis === "low_confidence" ? 1 : 0;
      reading.awaiting_decision += !chosen &&
        !["withheld_boundary", "withheld_containment"].includes(representative.feedback) &&
        representative.needsDecision ? 1 : 0;
    }

    const observations = [], candidates = [], refs = new Map();
    const inputRows = [];
    for (const entry of used) {
      if (!entry.primary || !entry.view) continue;
      const segmentIndexValue = segmentIndex.get(entry.attempt.segment_id);
      const sentence = segmentIndexValue === undefined ? null : segmentIndexValue + 1;
      const coach = entry.view.coach || {}, reduction = entry.view.reduction || {};
      for (const observation of coach.observations || []) {
        const namespaced = { ...observation, id: `${entry.attempt.id}:${observation.id}`, sentence };
        observations.push(namespaced);
        if (observation.play_ms) refs.set(namespaced.id, summaryPlayback(
          entry.attempt, entry.primary, observation.play_ms, observation.span_ms, "sound", {
            word: observation.word, sentence, word_play_ms: observation.word_play_ms,
          }));
      }
      if (reduction.state === "ok") {
        for (const candidate of reduction.candidates || []) candidates.push({ entry, sentence, candidate });
      }
      inputRows.push({
        attempt_id: entry.attempt.id, job_id: entry.primary.id, segment_id: entry.attempt.segment_id,
        sentence, target: entry.primary.target_confirmation?.state,
        user_disposition: entry.attempt.user_disposition ?? null,
      });
    }
    const [patterns, groups] = observations.length ? summaryPatterns(observations) : [[], []];
    const byObservation = new Map(observations.map((observation) => [observation.id, observation]));
    const patternOutput = patterns.sort((a, b) =>
      Math.min(...a.observation_ids.map((id) => byObservation.get(id).sentence)) -
      Math.min(...b.observation_ids.map((id) => byObservation.get(id).sentence))).map((pattern) => {
      const examples = pattern.observation_ids.filter((id) => refs.has(id)).slice(0, 3).map((id) => refs.get(id));
      return {
        id: pattern.id, kind: pattern.kind, expected: pattern.expected, contrast: pattern.contrast,
        class: pattern.class, context: pattern.context, evidence_strength: pattern.evidence_strength,
        occurrences: pattern.occurrences,
        sentences: [...new Set(pattern.observation_ids.map((id) => byObservation.get(id).sentence))].sort((a, b) => a - b),
        words: pattern.words, summary: readingText(pattern.summary), reference_note: pattern.reference_note,
        heard_as_expected_elsewhere: pattern.heard_as_expected_elsewhere, examples,
      };
    });
    const groupOf = new Map();
    for (const group of groups) for (const patternId of group.pattern_ids) groupOf.set(patternId, group.id);
    const groupOutput = groups.map((group) => ({
      id: group.id, title: readingText(group.title),
      pattern_ids: patternOutput.filter((pattern) => groupOf.get(pattern.id) === group.id).map((pattern) => pattern.id),
    }));
    const practise = [];
    for (const pattern of patternOutput) {
      if (groupOf.get(pattern.id) !== "recurring" || pattern.kind !== "contrast") continue;
      const guidance = summaryGuidance(pattern.expected, pattern.contrast);
      practise.push({
        pattern_id: pattern.id, target: pattern.expected, contrast: pattern.contrast,
        guidance, guidance_note: guidance ? SUMMARY_GUIDANCE_NOTE : null, examples: pattern.examples,
      });
    }

    const reductionMap = new Map();
    candidates.sort((a, b) => a.sentence - b.sentence);
    for (const { entry, sentence, candidate } of candidates) {
      const interpretation = candidate.interpretation || {};
      if (interpretation.category === "insufficient_evidence") continue;
      const key = JSON.stringify([interpretation.category, candidate.expected]);
      if (!reductionMap.has(key)) reductionMap.set(key, {
        category: interpretation.category, label: interpretation.label, expected: candidate.expected,
        occurrences: 0, sentences: [], explanations: new Map(),
        natural_connected_speech_possible: false, examples: [],
      });
      const reduction = reductionMap.get(key);
      reduction.occurrences += 1;
      if (!reduction.sentences.includes(sentence)) reduction.sentences.push(sentence);
      for (const explanation of interpretation.candidate_explanations || []) {
        reduction.explanations.set(explanation.id, explanation);
      }
      reduction.natural_connected_speech_possible ||= Boolean(interpretation.natural_connected_speech_possible);
      if (reduction.examples.length < 3) {
        reduction.examples.push(summaryPlayback(entry.attempt, entry.primary,
          candidate.where?.play_ms, candidate.where?.span_ms, "sound", {
            word: candidate.where?.word, sentence, word_play_ms: candidate.where?.word_play_ms,
            evidence_strength: candidate.evidence_strength,
          }));
      }
    }
    const reductionOutput = [...reductionMap.values()].map((reduction) => ({
      ...reduction, explanations: [...reduction.explanations.values()],
    }));
    const readable = segments.filter((segment) => segment.readable);
    const fluency = summaryFluency(used.filter((entry) => entry.view), segmentIndex);
    return {
      version: SUMMARY_VERSION, session_id: sessionId, engine, built_at: new Date().toISOString(),
      session_rev: session.rev,
      coverage: {
        sentences: readable.length, read: bySegment.size, included: used.length, feedback_included: used.length,
        ...reading, not_included: excluded.sort((a, b) => a.sentence - b.sentence),
        sounds: observations.filter((observation) => observation.kind === "sound").length,
        consistent_with_expected: observations.filter((observation) =>
          observation.kind === "sound" && observation.type === "expected").length,
      },
      inputs: inputRows, patterns: patternOutput, groups: groupOutput.filter((group) => group.pattern_ids.length),
      practise, reductions: reductionOutput, fluency, caveats: SUMMARY_CAVEATS,
    };
  }

  function pythonJson(value) {
    if (Array.isArray(value)) return `[${value.map(pythonJson).join(", ")}]`;
    if (value && typeof value === "object") {
      return `{${Object.keys(value).sort().map((k) => `${pythonJson(k)}: ${pythonJson(value[k])}`).join(", ")}}`;
    }
    if (typeof value === "string") {
      return JSON.stringify(value).replace(/[\u007f-\uffff]/g,
        (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, "0")}`);
    }
    return JSON.stringify(value);
  }
  function stableStringify(value) { return pythonJson(value); }
  function sha256(text) {
    const bytes = new TextEncoder().encode(text);
    const words = [];
    for (let i = 0; i < bytes.length; i++) words[i >> 2] = (words[i >> 2] || 0) | bytes[i] << (24 - (i % 4) * 8);
    words[bytes.length >> 2] = (words[bytes.length >> 2] || 0) | 0x80 << (24 - (bytes.length % 4) * 8);
    const bitLength = bytes.length * 8;
    const totalWords = (((bytes.length + 9 + 63) >> 6) << 4);
    words.length = totalWords;
    words[totalWords - 2] = Math.floor(bitLength / 0x100000000);
    words[totalWords - 1] = bitLength >>> 0;
    const k = [
      0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
      0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
      0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
      0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
      0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
      0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
      0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
      0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2,
    ];
    const h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
    const rotr = (x, n) => (x >>> n) | (x << (32 - n));
    for (let off = 0; off < totalWords; off += 16) {
      const w = new Array(64);
      for (let i = 0; i < 16; i++) w[i] = words[off + i] | 0;
      for (let i = 16; i < 64; i++) {
        const x = w[i - 15], y = w[i - 2];
        const s0 = rotr(x, 7) ^ rotr(x, 18) ^ (x >>> 3);
        const s1 = rotr(y, 17) ^ rotr(y, 19) ^ (y >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }
      let [a,b,c,d,e,f,g,hh] = h;
      for (let i = 0; i < 64; i++) {
        const s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const ch = (e & f) ^ (~e & g);
        const t1 = (hh + s1 + ch + k[i] + w[i]) | 0;
        const s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (s0 + maj) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      [a,b,c,d,e,f,g,hh].forEach((v, i) => { h[i] = (h[i] + v) | 0; });
    }
    return h.map((v) => (v >>> 0).toString(16).padStart(8, "0")).join("");
  }
  function runCoaching(inputs, priorExclusions = {}) {
    const parsed = Array.from(inputs || [], normaliseInput);
    const excluded = {};
    const addExclusions = (source) => {
      if (source instanceof Map) for (const [k, v] of source) excluded[k] = (excluded[k] || 0) + Number(v || 0);
      else if (Array.isArray(source)) for (const x of source) excluded[x] = (excluded[x] || 0) + 1;
      else if (source && typeof source === "object") for (const [k, v] of Object.entries(source)) excluded[k] = (excluded[k] || 0) + Number(v || 0);
    };
    addExclusions(priorExclusions || {});
    const ordered = parsed.slice().sort((a, b) => String(b.reading.recorded_at).localeCompare(String(a.reading.recorded_at))
      || String(b.reading.reading_id).localeCompare(String(a.reading.reading_id)));
    const engine = ordered[0]?.reading.engine || null;
    const same = [];
    for (const item of ordered) {
      if (item.reading.engine !== engine) excluded.other_engine = (excluded.other_engine || 0) + 1;
      else same.push(item);
    }
    const selected = same.slice(0, COACH_CAL.pool_n_max).reverse();
    const discarded = same.length - selected.length;
    if (discarded) excluded.outside_window = (excluded.outside_window || 0) + discarded;
    if (!excluded.outside_window) delete excluded.outside_window;
    const poolKey = {
      readings: selected.map((p) => [p.reading.reading_id, p.reading.job_id, p.coach_version]),
      calibration: COACH_CAL,
      versions: { coaching: COACHING_VERSION, knowledge: KNOWLEDGE_VERSION, calibration: CALIBRATION_VERSION },
    };
    const fingerprint = sha256(stableStringify(poolKey)).slice(0, 20);
    const sessions = uniq(selected.map((p) => p.reading.session_id)).sort();
    let insufficientCode = selected.length < COACH_CAL.pool_min_readings ? "history_too_small"
      : sessions.length < COACH_CAL.pool_min_sessions ? "single_session" : null;
    const poolUnits = selected.flatMap((p) => p.units);
    const poolFluency = selected.flatMap((p) => p.fluency);
    const unitQuality = makeCounter(poolUnits.map((u) => u.quality));
    const fluQuality = makeCounter(poolFluency.map((u) => u.quality));
    const candidates = buildCoachingCandidates(poolUnits, poolFluency, selected);
    const interventions = new Map();
    const getIntervention = (candidate) => {
      const key = candidate.target_id + ":" + candidate.unit_ids.join(",");
      if (!interventions.has(key)) interventions.set(key, buildIntervention(candidate, poolUnits, poolFluency, selected));
      return interventions.get(key);
    };
    const established = candidates.filter((c) => c.tier === "established");
    const trainable = established.filter((c) => getIntervention(c));
    const selection = selectCoachingActions(trainable, candidates, poolUnits, poolFluency, selected,
      getIntervention, candidates.consolidation || []);
    const anyHeard = poolUnits.some((u) => u.outcome === "heard_other");
    let code = insufficientCode;
    if (!code) {
      if (selection.actions.length) code = null;
      else if (established.length) code = established.every((t) => !t.has_audio) ? "audio_unavailable" : "no_trainable_intervention";
      else if (candidates.some((t) => t.tier === "emerging" && t.kind !== "CLARITY")) code = "only_emerging";
      else if ((candidates.consolidation || []).some((record) =>
        record.conditions?.["5_not_contradicted"]?.ok === false)) code = "contradictory";
      else if (anyHeard && !poolUnits.some((u) => u.quality === "confident" && u.outcome === "heard_other")) {
        code = poolUnits.some((u) => u.quality === "supporting" && u.outcome === "heard_other")
          ? "only_ambiguous" : "only_excluded_kinds";
      } else code = "nothing_recurring";
    }
    const noAction = code ? (NO_ACTION[code] || NO_ACTION.nothing_recurring) : null;
    const result = {
      version: COACHING_VERSION, generated_at: null, knowledge_version: KNOWLEDGE_VERSION,
      calibration: poolKey.calibration,
      pool: { engine, fingerprint, window: { n_max: COACH_CAL.pool_n_max, n_used: selected.length, max_age_days: null },
        readings: selected.length, sessions: sessions.length, sentences: uniq(selected.map((p) => p.reading.sentence_key)).length,
        recorded_from: selected.length ? selected.map((p) => p.reading.recorded_at).sort()[0] : null,
        recorded_to: selected.length ? selected.map((p) => p.reading.recorded_at).sort().at(-1) : null,
        reading_exclusions: Object.fromEntries(Object.entries(excluded).sort(([a], [b]) => a.localeCompare(b))),
        units: unitQuality, fluency_units: fluQuality,
        unit_exclusions: makeCounter(poolUnits.concat(poolFluency).filter((u) => u.exclusion).map((u) => u.exclusion)) },
      state: selection.actions.length ? "actions" : "no_action", actions: selection.actions,
      no_action: noAction ? { code, message: noAction[0], what_would_help: noAction[1] } : null,
      not_assessed: [], caveats: COACH_CAVEATS, integrity: { ok: true, issues: [] },
      detail: { candidates: candidates.map(publicTarget), listen_check: candidates
          .filter((c) => c.tier === "emerging" && c.kind !== "CLARITY").slice(0, 1).map(publicTarget),
        selection_rounds: selection.rounds, absorbed_by_selection: selection.absorbed,
        not_prioritised: selection.not_prioritised.map((item) => ({ ...item, target: publicTarget(item.target) })),
        consolidation: candidates.consolidation || [],
        knowledge: { version: KNOWLEDGE_VERSION } },
    };
    return result;
  }
  function compactLongitudinalRecord(item, normalized = normaliseInput(item)) {
    const r = normalized.reading;
    const sounds = normalized.units.filter((u) => u.expected != null || u.outcome === "extra").map((u) => ({
      o: u.unit_id, obs: u.observation_id, e: u.expected, h: u.heard, out: u.outcome,
      q: u.quality, x: u.exclusion, c: u.m4_confidence, w: u.lexical_key, wd: u.word, wi: u.word_index,
      si: null, frag: u.fragment_suspect, pos: u.context.word_position, st: u.context.stress_known
        ? (u.context.stress || "unstressed") : null, cl: u.context.in_consonant_cluster,
      sp: u.context.sentence_position, ag: u.engine_agreement, play: u.audio.play_ms, span: u.audio.span_ms,
    }));
    const fluency = normalized.fluency.map((u) => ({
      o: u.unit_id, g: u.group, q: u.quality, play: u.audio.play_ms,
      before: u.word_before, after: u.word_after,
    }));
    return {
      reading: { ...r, article_id: r.article_id || r.session_id, text_id: r.article_key || r.session_id,
        practice_session: !!r.practice_session },
      eligible: true, reason: null, detail: null, sounds, fluency,
    };
  }
  function longitudinalApi() {
    if (globalThis.PronounceReaderLongitudinal) return globalThis.PronounceReaderLongitudinal;
    if (typeof require === "function") {
      try { return require("./reader-browser-longitudinal.js"); } catch { /* not loaded in this environment */ }
    }
    return null;
  }
  function browserSessions(sessions, inputs) {
    const bySession = new Map();
    for (const session of sessions || []) {
      const id = session.id || session.session?.id;
      if (!id) continue;
      bySession.set(id, { ...session, id });
    }
    for (const item of inputs || []) {
      const normalized = normaliseInput(item);
      const sid = normalized.reading.session_id;
      if (!sid) continue;
      if (!bySession.has(sid)) bySession.set(sid, { id: sid });
      const row = bySession.get(sid);
      if (!row.records) row.records = [];
      row.records.push(compactLongitudinalRecord(item, normalized));
    }
    return [...bySession.values()];
  }
  async function runProgress(options = {}, persist = false) {
    const longitudinal = longitudinalApi();
    if (!longitudinal || typeof longitudinal.update !== "function") {
      return { progress: { state: "unavailable", patterns: [] }, coaching: null, coaching_adaptation: null };
    }
    const inputs = options.inputs || [];
    const coaching = options.coaching || runCoaching(inputs, options.exclusions || {});
    const store = options.longitudinalStore || null;
    const browserStore = store?.store || null;
    const common = {
      generated_at: options.generated_at || new Date().toISOString(),
      coaching, engine: options.engine || null, rebuild: !!options.rebuild,
      persist,
    };
    let result;
    if (browserStore) {
      result = await longitudinal.update({ ...common, store: browserStore, progressStore: store });
    } else {
      const sessions = browserSessions(options.sessions || [], inputs);
      const [cache, ledger, transitions, practice] = await Promise.all([
        store?.loadCache?.() || Promise.resolve({}),
        store?.ledger?.() || Promise.resolve([]),
        store?.transitionsLog?.() || Promise.resolve([]),
        store?.practiceRecords?.() || Promise.resolve([]),
      ]);
      result = await longitudinal.update({
        ...common, sessions, cache, ledger, transitions, practice, persist: false,
      });
      if (persist) {
        if (store?.saveCache) await store.saveCache(result.cache);
        const newLedger = result.ledger.slice(ledger.length);
        const newTransitions = result.transitions.slice(transitions.length);
        if (store?.appendLedger) await store.appendLedger(newLedger);
        if (store?.appendTransitions) await store.appendTransitions(newTransitions);
      }
    }
    return { ...result, coaching };
  }
  function progress(options = {}) { return runProgress(options, false); }
  function update(options = {}) { return runProgress(options, true); }

  function practiceTarget(targetId, suppliedTarget) {
    if (suppliedTarget) return suppliedTarget;
    const id = String(targetId || "");
    if (id.startsWith("fluency:")) return { target_id: id, kind: "FLUENCY", group: id.slice(8) };
    if (id.startsWith("contrast:")) {
      const sounds = id.slice("contrast:".length).split("~");
      if (sounds.length === 2) return { target_id: id, kind: "CONTRAST", pairs: [
        `${sounds[0]}→${sounds[1]}`, `${sounds[1]}→${sounds[0]}`,
      ] };
    }
    return { target_id: id || null, kind: null };
  }
  function materialWords(sentences) {
    return [...new Set(sentences.flatMap((text) => String(text || "").split(/\s+/u)
      .map(lexicalKey).filter(Boolean)))].sort((a, b) => a.localeCompare(b));
  }
  function targetLink(target) {
    const patterns = [];
    if (["CONTRAST", "SET", "CONDITIONED", "LEXICAL"].includes(target.kind)) {
      for (const pair of target.pairs || []) {
        const parts = typeof pair === "string" ? pair.split("→") : pair;
        if (parts.length === 2) patterns.push(`sub:${parts[0]}>${parts[1]}`);
      }
    } else if (target.kind === "FLUENCY" && target.group) patterns.push(`fluency:${target.group}`);
    const uniquePatterns = uniq(patterns).sort((a, b) => a.localeCompare(b));
    return {
      identity_version: "m10-id.1", m9_target_id: target.target_id || null, m9_kind: target.kind || null,
      patterns: uniquePatterns, context: target.condition || null, word: target.word || null,
      tracked: uniquePatterns.length > 0,
      reason: uniquePatterns.length ? null : "not tracked longitudinally (M5 'not detected' cannot mean absent)",
    };
  }
  function newPracticeRecord(options = {}) {
    const { id, created_at: createdAt, session_id: sessionId, article, target_id: targetId,
      source_session_id: sourceSessionId } = options;
    const readable = (article?.segments || []).filter((segment) => segment.readable).map((segment) => segment.text);
    const target = practiceTarget(targetId, options.target);
    const link = targetLink(target);
    const action = options.action || {};
    const advice = options.advice || options.coaching || {};
    const patterns = link.patterns;
    const reversePatterns = patterns.filter((pattern) => pattern.startsWith("sub:"))
      .map((pattern) => {
        const parsed = /^sub:([^>]+)>(.+)$/.exec(pattern);
        return parsed ? `sub:${parsed[2]}>${parsed[1]}` : null;
      }).filter(Boolean).sort((a, b) => a.localeCompare(b));
    const record = {
      version: "m10-practice.1", id, created_at: createdAt, practice_session_id: sessionId,
      article_id: article?.id || null, source: "what_to_practise_now",
      advice: {
        coaching_generated_at: advice.generated_at || null,
        pool_fingerprint: advice.pool?.fingerprint || advice.fingerprint || null,
        m9_target_id: target.target_id || targetId || null,
        action_text: action.action_text || options.action_text || null,
        plan_position: action.rank_in_plan ?? options.plan_position ?? null,
      },
      target: { ...link, identity_version: "m10-id.1" },
      reverse_patterns: reversePatterns,
      practice_type: `retest_sentences:${action.practice?.trainability || options.trainability || "None"}`,
      mode: null,
      material: {
        sentences: readable.map((text) => ({ text, sentence_key: textKey(text) })),
        words: materialWords(readable),
      },
    };
    return record;
  }
  const CONTRAST_GUIDANCE = new Map([
    ["v~w", "/w/ is usually made with rounded lips and no teeth contact; /v/ with the lower lip lightly touching the upper teeth, with voicing."],
    ["s~z", "/s/ is usually voiceless; /z/ is the same hiss with voicing."],
    ["s~ʃ", "/s/ is usually a sharp high hiss with spread lips; /ʃ/ (as in 'ship') is lower-pitched, often with rounded lips."],
    ["f~v", "/f/ is usually voiceless; /v/ is the same sound with voicing."],
    ["iː~ɪ", "/ɪ/ (as in 'sit') is usually short and relaxed; /iː/ (as in 'see') is longer and tenser."],
    ["n~ŋ", "/ŋ/ (as in 'sing') is usually made at the back of the mouth; /n/ behind the upper teeth."],
    ["tʃ~dʒ", "/tʃ/ (as in 'chin') is usually voiceless; /dʒ/ (as in 'jam') is voiced."],
    ["l~ɹ", "/ɹ/ is usually made without the tongue tip touching the roof of the mouth; /l/ with it touching."],
    ["s~θ", "/θ/ is usually softer and more diffuse; /s/ is a sharper, high-pitched hiss."],
    ["d~ð", "/ð/ is usually a continuous voiced sound with the tongue near the upper teeth; /d/ is a short voiced stop."],
    ["s~ð", "/ð/ is usually made with the tongue near the upper teeth; /z/ is a sharper voiced hiss."],
    ["t~θ", "/θ/ is usually a continuous breathy sound with the tongue near the upper teeth; /t/ is a short stop-and-release."],
  ]);
  const COACH_FALLBACK = "No general guidance is stored for this contrast: listen to your own clear examples and imitate them, then compare with the ones heard differently.";
  const TRAINABILITY = { specific_guidance: 2, listen_compare_fallback: 1, word_practice: 1, phrase_practice: 1 };
  function byRecent(a, b) {
    return String(b.recorded_at).localeCompare(String(a.recorded_at)) || String(b.unit_id).localeCompare(String(a.unit_id));
  }
  function inTargetScope(target, unit) {
    if (!target.sounds.includes(unit.expected)) return false;
    if (target.condition === "final_cluster" &&
      !(unit.context.word_position === "final" && unit.context.in_consonant_cluster)) return false;
    if (target.condition === "final" && unit.context.word_position !== "final") return false;
    if (target.condition === "initial" && unit.context.word_position !== "initial") return false;
    if (target.condition === "medial" && unit.context.word_position !== "medial") return false;
    if (target.condition === "cluster" && !unit.context.in_consonant_cluster) return false;
    if (target.word && unit.lexical_key !== target.word) return false;
    return true;
  }
  function counterUnits(target, units) {
    if (!["CONTRAST", "SET", "CONDITIONED", "LEXICAL"].includes(target.kind)) return [];
    return units.filter((u) => inTargetScope(target, u) && u.quality === "counter" && u.outcome === "as_expected");
  }
  function evidenceMetrics(target, units, poolSentenceCount) {
    const support = units.filter((u) => target.unit_ids.includes(u.unit_id));
    const confident = support.filter((u) => u.quality === "confident");
    const counters = counterUnits(target, units);
    const m = {
      confident: confident.length, supporting: support.filter((u) => u.quality === "supporting").length,
      conf_words: uniq(confident.map((u) => u.lexical_key).filter(Boolean)).length,
      conf_sentences: uniq(confident.map((u) => u.sentence_key)).length,
      conf_sessions: uniq(confident.map((u) => u.session_id)).length,
      conf_readings: uniq(confident.map((u) => u.reading_id)).length,
      sentences: uniq(support.map((u) => u.sentence_key)).length,
      sessions: uniq(support.map((u) => u.session_id)).length,
    };
    const denominator = units.filter((u) => inTargetScope(target, u) &&
      u.quality === "confident" && u.outcome === "heard_other").length;
    const soundKind = ["CONTRAST", "SET", "CONDITIONED", "LEXICAL"].includes(target.kind);
    const concentration = soundKind && denominator ? round3(confident.length / denominator) : null;
    const compared = support.filter((u) => u.engine_agreement && u.engine_agreement !== "not_compared");
    const differ = compared.filter((u) => u.engine_agreement === "differ").length;
    const engineShare = compared.length ? round3(differ / compared.length) : null;
    const checks = {};
    if (["CONTRAST", "SET", "CONDITIONED"].includes(target.kind)) {
      checks.confident = { value: m.confident, required: COACH_CAL.k_conf, ok: m.confident >= COACH_CAL.k_conf };
      checks.words = { value: m.conf_words, required: COACH_CAL.w_min, ok: m.conf_words >= COACH_CAL.w_min };
      checks.sentences = { value: m.conf_sentences, required: COACH_CAL.s_min, ok: m.conf_sentences >= COACH_CAL.s_min };
      checks.sessions = { value: m.conf_sessions, required: COACH_CAL.sessions_min, ok: m.conf_sessions >= COACH_CAL.sessions_min };
      checks.concentration = { value: concentration, required: COACH_CAL.c_min, ok: concentration !== null && concentration >= COACH_CAL.c_min };
      checks.engine_agreement = { value: engineShare, required: COACH_CAL.disagree_max, ok: engineShare === null || engineShare <= COACH_CAL.disagree_max };
      checks.not_contradicted = { value: !target.contradicted, required: true, ok: !target.contradicted };
    } else if (target.kind === "LEXICAL") {
      const readingsOfWord = uniq(confident.map((u) => u.reading_id)).length;
      const relevantSentences = uniq(units.filter((u) => u.lexical_key === target.word && u.expected != null)
        .map((u) => u.sentence_key)).length;
      checks.confident = { value: m.confident, required: COACH_CAL.lex_k_conf, ok: m.confident >= COACH_CAL.lex_k_conf };
      checks.readings_of_word = { value: readingsOfWord, required: COACH_CAL.lex_min_readings,
        ok: readingsOfWord >= COACH_CAL.lex_min_readings };
      checks.sessions = { value: m.conf_sessions, required: COACH_CAL.sessions_min,
        ok: m.conf_sessions >= COACH_CAL.sessions_min };
      checks.relevance = { value: relevantSentences, required: COACH_CAL.lex_rel, ok: relevantSentences >= COACH_CAL.lex_rel };
      checks.not_fragment = { value: !support.some((u) => u.fragment_suspect), required: true,
        ok: !support.some((u) => u.fragment_suspect) };
    } else {
      const count = target.kind === "FLUENCY" ? m.confident : support.length;
      checks.units = { value: count, required: COACH_CAL.k_conf, ok: count >= COACH_CAL.k_conf };
      checks.sentences = { value: m.sentences, required: COACH_CAL.s_min, ok: m.sentences >= COACH_CAL.s_min };
      checks.sessions = { value: m.sessions, required: COACH_CAL.sessions_min, ok: m.sessions >= COACH_CAL.sessions_min };
    }
    const passed = Object.values(checks).every((c) => c.ok);
    const evidenceN = m.confident + m.supporting + (target.kind === "CLARITY" ? support.length : 0);
    const tier = passed && target.kind !== "CLARITY" ? "established" : (m.confident || target.kind === "CLARITY") && evidenceN >= COACH_CAL.k_conf
      ? "emerging" : "insufficient";
    const exposure = new Set(units.filter((u) => u.expected != null && inTargetScope(target, u))
      .map((u) => `${u.reading_id}:${u.word_index}`));
    let reach = target.kind === "FLUENCY"
      ? (poolSentenceCount ? round1(100 * uniq(support.map((u) => u.sentence_key)).length / poolSentenceCount) : null)
      : target.kind === "CLARITY" || !units.some((u) => u.expected != null) ? null
        : round1(100 * exposure.size / Math.max(1, new Set(units.filter((u) => u.expected != null)
          .map((u) => `${u.reading_id}:${u.word_index}`)).size));
    const failed = Object.entries(checks).filter(([, check]) => !check.ok).map(([name]) => name);
    if (target.kind === "CLARITY" && passed) failed.push("capped: a weak signal until stronger clarity evidence exists");
    checks._failed = failed;
    const measures = { ...m, counter: counters.length, concentration,
      reach, deviation_share: m.confident + counters.length ? round3(m.confident / (m.confident + counters.length)) : null,
      engine_differ_share: engineShare, engine_compared: compared.length };
    return { support, confident, counters, measures, checks, tier };
  }
  function targetHypothesis(target) {
    if (target.kind === "CONTRAST") {
      const [a, b] = target.pairs[0];
      return target.two_way ? `/${a}/ and /${b}/ were often heard as each other in your recent readings.`
        : `/${a}/ was often heard as /${b}/ in your recent readings.`;
    }
    if (target.kind === "SET") {
      const sounds = uniq(target.pairs.flat()).sort();
      return `The sounds of ${target.family_title || "this family"} were often heard as one another in your recent readings.`;
    }
    if (target.kind === "CONDITIONED") {
      return `/${target.sounds.join("/, /")}/ appears less stable ${CONDITIONS.find((c) => c[0] === target.condition)?.[1] || ""} than elsewhere in your recent readings.`;
    }
    if (target.kind === "LEXICAL") {
      return `‘${target.word}’ was heard differently in several readings, while /${target.sounds.join("/, /")}/ is mostly heard as expected in other words.`;
    }
    if (target.kind === "CLARITY") {
      return "Several final consonants were not clearly detected in your recent readings (not detected does not prove a sound was absent).";
    }
    return ({ hesitation: "Possible hesitation pauses recur inside phrases in your recent readings.",
      filler: "Possible filler sounds recur between words in your recent readings.",
      repetition: "Possible repetitions or restarts recur in your recent readings." })[target.group];
  }
  function buildCoachingCandidates(units, fluency, poolInputs) {
    const directed = new Map();
    for (const u of units) if (["confident", "supporting"].includes(u.quality) && u.outcome === "heard_other") {
      const key = `${u.expected}→${u.heard}`;
      if (!directed.has(key)) directed.set(key, []);
      directed.get(key).push(u);
    }
    const candidates = [];
    const absorbed = [];
    const sentenceCount = uniq(poolInputs.map((x) => x.reading.sentence_key)).length;
    const evaluated = (target) => {
      target.unit_ids = [...new Set(target.unit_ids)].sort();
      target.origin = "inferred";
      target.notes ||= [];
      target.knowledge_contributions ||= [];
      target.consolidation_record ||= [];
      target.condition_label = target.condition ? CONDITIONS.find((c) => c[0] === target.condition)?.[1] : null;
      const metricUnits = target.kind === "FLUENCY" ? units.concat(fluency) : units;
      const result = evidenceMetrics(target, metricUnits, sentenceCount);
      Object.assign(target, result, { hypothesis: targetHypothesis(target), tier: result.tier,
        measures: result.measures, checks: result.checks,
        has_audio: target.unit_ids.some((id) => (metricUnits.find((u) => u.unit_id === id) || {}).audio &&
          unitRefOk(metricUnits.find((u) => u.unit_id === id).audio)) });
      return target;
    };
    const contrastById = new Map();
    const consumed = new Set();
    for (const key of [...directed.keys()].sort()) {
      if (consumed.has(key)) continue;
      const [a, b] = key.split("→"), reverse = `${b}→${a}`;
      const keys = directed.has(reverse) ? [key, reverse].sort((x, y) =>
        directed.get(y).filter((u) => u.quality === "confident").length -
          directed.get(x).filter((u) => u.quality === "confident").length) : [key];
      keys.forEach((k) => consumed.add(k));
      const pairs = keys.map((k) => k.split("→"));
      const target = evaluated({ target_id: `contrast:${[a, b].sort().join("~")}`, kind: "CONTRAST",
        sounds: uniq(pairs.map((p) => p[0])).sort(), pairs, family_id: null, condition: null, word: null,
        group: null, two_way: keys.length === 2 && keys.every((k) =>
          directed.get(k).some((u) => u.quality === "confident")),
        unit_ids: keys.flatMap((k) => directed.get(k).map((u) => u.unit_id)), notes: [],
        knowledge_contributions: [], consolidation_record: [] });
      contrastById.set(target.target_id, target);
    }
    const covered = new Set();
    for (const family of FAMILIES) {
      let members;
      if (family.mode === "set") {
        members = [...contrastById.values()].filter((t) => !t.pairs.some((p) => covered.has(p.join("→"))) &&
          t.pairs.every(([expected, heard]) => family.members.includes(expected) && family.members.includes(heard)));
      } else {
        members = [...directed.keys()].sort().filter((key) => {
          const [expected, heard] = key.split("→");
          return !covered.has(key) && family.pairs.some((p) => p[0] === expected && p[1] === heard);
        }).map((key) => {
          const [expected, heard] = key.split("→");
          return evaluated({ target_id: `contrast:${key}`, kind: "CONTRAST", sounds: [expected],
            pairs: [[expected, heard]], unit_ids: directed.get(key).map((u) => u.unit_id),
            two_way: false, notes: [], knowledge_contributions: [], consolidation_record: [] });
        });
      }
      if (!members.length) continue;
      const passing = members.filter((member) => Object.entries(member.checks)
        .every(([name, check]) => name === "concentration" || name.startsWith("_") || check.ok));
      const record = { family: family.id, origin: "knowledge proposal",
        members: members.map((m) => m.target_id), members_passing_alone: passing.map((m) => m.target_id) };
      if (passing.length < 2) {
        record.decision = "not merged: fewer than two members pass the gates on their own";
        absorbed.push(record);
        continue;
      }
      const contradictions = [];
      if (family.mode === "pairs") for (const member of passing) {
        const [expected, heard] = member.pairs[0];
        const forward = (directed.get(`${expected}→${heard}`) || []).filter((u) => u.quality === "confident").length;
        const reverse = (directed.get(`${heard}→${expected}`) || []).filter((u) => u.quality === "confident").length;
        if (reverse >= forward) contradictions.push(`${heard}→${expected} (${reverse}) opposes ${expected}→${heard} (${forward})`);
      }
      const unionIds = [...new Set(passing.flatMap((m) => m.unit_ids))].sort();
      const unionConf = units.filter((u) => unionIds.includes(u.unit_id) && u.quality === "confident").length;
      const largest = Math.max(...passing.map((m) => m.measures.confident));
      const conditions = {
        "1_each_member_passes_alone": true,
        "2_declared_relation_and_coherent": !contradictions.length,
        "3_one_intervention_applies": passing.every((m) => m.kind === "CONTRAST"),
        "4_explains_clearly_more": { union_confident: unionConf, largest_member: largest,
          required_ratio: COACH_CAL.m_merge, ok: unionConf >= COACH_CAL.m_merge * largest },
        "5_not_contradicted": { contradictions, ok: !contradictions.length },
      };
      if (!conditions["1_each_member_passes_alone"] || !conditions["2_declared_relation_and_coherent"] ||
        !conditions["3_one_intervention_applies"] || !conditions["4_explains_clearly_more"].ok ||
        !conditions["5_not_contradicted"].ok) {
        record.conditions = conditions;
        record.decision = "not merged: a merge condition failed";
        absorbed.push(record);
        continue;
      }
      const pairs = passing.flatMap((m) => m.pairs);
      const target = evaluated({ target_id: `set:${family.id}`, kind: "SET",
        sounds: uniq(pairs.map((p) => p[0])).sort(), pairs, family_id: family.id,
        family_title: familyTitle(family.id), unit_ids: unionIds, notes: [],
        knowledge_contributions: [{ entry: `family:${family.id}`, contributed: "proposed family",
          text: FAMILY_TEXT, origin: "knowledge" }],
        consolidation_record: [{ ...record, conditions, decision: "merged" }] });
      for (const member of passing) {
        member.pairs.forEach((p) => covered.add(p.join("→")));
        absorbed.push({ target: member.target_id, absorbed_into: target.target_id, reason: "member of a merged family" });
      }
      candidates.push(target);
    }
    for (const contrast of contrastById.values()) {
      const remainingPairs = contrast.pairs.filter((p) => !covered.has(p.join("→")));
      if (!remainingPairs.length) continue;
      const remainingIds = remainingPairs.flatMap(([a, b]) =>
        (directed.get(`${a}→${b}`) || []).map((u) => u.unit_id));
      candidates.push(evaluated({ ...contrast,
        pairs: remainingPairs, sounds: uniq(remainingPairs.map((p) => p[0])).sort(),
        unit_ids: remainingIds, two_way: remainingPairs.length === 2 && remainingPairs.every(([a, b]) =>
          (directed.get(`${a}→${b}`) || []).some((u) => u.quality === "confident")) }));
    }
    const soundTargets = candidates.slice();
    candidates.length = 0;
    for (const target of soundTargets) candidates.push(...narrowSoundTarget(target, units, sentenceCount, absorbed, evaluated));
    const existingIds = new Set(candidates.map((t) => t.target_id));
    for (const target of lexicalCandidates(units, sentenceCount, evaluated)) {
      if (!existingIds.has(target.target_id)) candidates.push(target);
    }
    const fluByGroup = new Map();
    for (const f of fluency) if (["confident", "supporting"].includes(f.quality)) {
      if (!fluByGroup.has(f.group)) fluByGroup.set(f.group, []);
      fluByGroup.get(f.group).push(f);
    }
    for (const [group, fs] of [...fluByGroup].sort(([a], [b]) => compareText(a, b))) {
      candidates.push(evaluated({ target_id: `fluency:${group}`, kind: "FLUENCY", sounds: [], pairs: [],
        family_id: null, condition: null, condition_label: null, word: null, group, two_way: false,
        unit_ids: fs.map((f) => f.unit_id), notes: [], knowledge_contributions: [], consolidation_record: [] }));
    }
    const clarity = units.filter((u) => u.quality === "weak" && !isVowel(u.expected) &&
      u.context.word_position === "final");
    if (clarity.length) candidates.push(evaluated({ target_id: "clarity:final_consonant", kind: "CLARITY",
      sounds: [], pairs: [], family_id: null, condition: null, word: null, group: "final_consonant",
      unit_ids: clarity.map((u) => u.unit_id), notes: [], knowledge_contributions: [], consolidation_record: [] }));
    candidates.consolidation = absorbed;
    const sorted = candidates.sort((a, b) => compareText(a.target_id, b.target_id));
    sorted.consolidation = absorbed;
    return sorted;
  }
  const CONDITIONS = [
    ["final_cluster", "in consonant clusters at the end of words", (c) => c.word_position === "final" && !!c.in_consonant_cluster],
    ["final", "at the end of words", (c) => c.word_position === "final"],
    ["initial", "at the start of words", (c) => c.word_position === "initial"],
    ["medial", "in the middle of words", (c) => c.word_position === "medial"],
    ["cluster", "in consonant clusters", (c) => !!c.in_consonant_cluster],
  ];
  const FAMILY_TITLES = {
    front_vowel_ladder: "the vowels in beat, bit, bait, bet and bat",
    back_rounded_vowels: "the vowels in book, food and go",
    sibilant_place: "the 's' and 'sh' sounds",
    th_sounds: "the 'th' sounds",
    devoicing: "voiced consonants heard as voiceless",
    voicing: "voiceless consonants heard as voiced",
  };
  function familyTitle(id) { return FAMILY_TITLES[id] || id; }
  function compareText(a, b) { return a < b ? -1 : a > b ? 1 : 0; }
  function round1(n) { return Math.round(n * 10) / 10; }
  function narrowSoundTarget(target, units, poolSentenceCount, absorbed, evaluate) {
    const targetUnits = target.unit_ids.map((id) => units.find((u) => u.unit_id === id)).filter(Boolean);
    const confident = targetUnits.filter((u) => u.quality === "confident").sort((a, b) => compareText(a.unit_id, b.unit_id));
    const byWord = new Map();
    for (const u of confident) byWord.set(u.lexical_key, (byWord.get(u.lexical_key) || 0) + 1);
    const orderedWords = [...byWord].sort((a, b) => b[1] - a[1]);
    if (confident.length && orderedWords.length) {
      const top = orderedWords.slice(0, COACH_CAL.lex_max_words).map(([word]) => word);
      const topN = top.reduce((n, word) => n + byWord.get(word), 0);
      const share = topN / confident.length, outside = confident.length - topN;
      const wordOk = top.every((word) => byWord.get(word) >= COACH_CAL.lex_k_conf &&
        uniq(confident.filter((u) => u.lexical_key === word).map((u) => u.reading_id)).length >= COACH_CAL.lex_min_readings);
      if (share >= COACH_CAL.lex_share && outside < COACH_CAL.k_conf && wordOk) {
        const lexical = top.map((word) => {
          const t = evaluate({ ...target, target_id: `lexical:${word}:${target.sounds.join("|")}`,
            kind: "LEXICAL", word, unit_ids: target.unit_ids.filter((id) =>
              units.find((u) => u.unit_id === id)?.lexical_key === word), notes: target.notes.slice(),
            consolidation_record: target.consolidation_record.slice() });
          t.consolidation_record.push({ narrowed_from: target.target_id, test: "lexical",
            word_share: round3(byWord.get(word) / confident.length), top_words_share: round3(share),
            required: COACH_CAL.lex_share, confident_outside_words: outside });
          return t;
        });
        absorbed.push({ target: target.target_id, absorbed_into: lexical.map((t) => t.target_id),
          reason: "lexical concentration", top_words_share: round3(share) });
        return lexical;
      }
    }
    const counters = counterUnits(target, units);
    const occurrences = targetUnits.concat(counters);
    let best = null;
    for (const [condition, label, predicate] of CONDITIONS) {
      const confIn = confident.filter((u) => predicate(u.context));
      const confOut = confident.filter((u) => !predicate(u.context));
      const occIn = occurrences.filter((u) => ["confident", "counter"].includes(u.quality) && predicate(u.context));
      const occOut = occurrences.filter((u) => ["confident", "counter"].includes(u.quality) && !predicate(u.context));
      if (!confIn.length) continue;
      if (confIn.length === confident.length && occOut.length < COACH_CAL.ctx_min_out) {
        target.notes.push(`only observed ${label}; there is not enough evidence elsewhere to tell whether this is specific to that context`);
        continue;
      }
      if (occIn.length < COACH_CAL.ctx_min_in || occOut.length < COACH_CAL.ctx_min_out) continue;
      if (occIn.length > COACH_CAL.ctx_max_share * (occIn.length + occOut.length)) continue;
      const inShare = confIn.length / occIn.length, outShare = confOut.length / occOut.length;
      const meaningful = confIn.length >= COACH_CAL.k_conf &&
        uniq(confIn.map((u) => u.session_id)).length >= COACH_CAL.sessions_min &&
        (outShare ? inShare >= COACH_CAL.ctx_ratio * outShare : inShare > 0);
      if (!meaningful) continue;
      const ratio = outShare ? inShare / outShare : Infinity;
      if (!best || ratio > best.ratio) best = { condition, label, predicate, ratio, confIn, confOut, occIn, occOut, inShare, outShare };
    }
    if (!best) return [target];
    const stats = { condition: best.condition,
      inside: { confident: best.confIn.length, occurrences: best.occIn.length, share: round3(best.inShare) },
      outside: { confident: best.confOut.length, occurrences: best.occOut.length, share: round3(best.outShare) },
      required_ratio: COACH_CAL.ctx_ratio };
    if (best.confOut.length >= COACH_CAL.k_conf) {
      target.notes.push(`heard differently more often ${best.label} (${best.confIn.length}/${best.occIn.length} vs ${best.confOut.length}/${best.occOut.length} elsewhere), but it also recurs elsewhere, so the broader target is kept`);
      target.consolidation_record.push({ test: "context", decision: "kept broad", ...stats });
      return [target];
    }
    const conditioned = evaluate({ ...target, target_id: `conditioned:${best.condition}:${target.target_id}`,
      kind: "CONDITIONED", condition: best.condition, unit_ids: target.unit_ids.filter((id) => {
        const u = units.find((x) => x.unit_id === id);
        return u && best.predicate(u.context);
      }), notes: target.notes.slice(), consolidation_record: target.consolidation_record.slice() });
    conditioned.consolidation_record.push({ narrowed_from: target.target_id, test: "context", decision: "narrowed", ...stats });
    absorbed.push({ target: target.target_id, absorbed_into: conditioned.target_id, reason: "context concentration", ...stats });
    return [conditioned];
  }
  function lexicalCandidates(units, poolSentenceCount, evaluate) {
    const grouped = new Map();
    for (const u of units) if (u.outcome === "heard_other" && ["confident", "supporting"].includes(u.quality)) {
      const key = `${u.expected}\u0000${u.lexical_key}`;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(u);
    }
    const result = [];
    for (const [key, support] of [...grouped].sort(([a], [b]) => compareText(a, b))) {
      const [sound, word] = key.split("\u0000");
      const confident = support.filter((u) => u.quality === "confident");
      if (confident.length < COACH_CAL.lex_k_conf) continue;
      const counters = units.filter((u) => u.expected === sound && u.quality === "counter" && u.outcome === "as_expected");
      const inWord = confident.length / (confident.length + counters.filter((u) => u.lexical_key === word).length);
      const otherConfident = units.filter((u) => u.outcome === "heard_other" && u.quality === "confident" &&
        u.expected === sound && u.lexical_key !== word).length;
      const otherOccurrences = otherConfident + counters.filter((u) => u.lexical_key !== word).length;
      if (otherOccurrences < COACH_CAL.ctx_min_out) continue;
      if (otherConfident / otherOccurrences > inWord / COACH_CAL.ctx_ratio) continue;
      const pairs = [...new Map(support.map((u) => [`${u.expected}→${u.heard}`, [u.expected, u.heard]])).values()]
        .sort((a, b) => compareText(`${a[0]}→${a[1]}`, `${b[0]}→${b[1]}`));
      const target = evaluate({ target_id: `lexical:${word}:${sound}`, kind: "LEXICAL", sounds: [sound],
        pairs, unit_ids: support.map((u) => u.unit_id), word, family_id: null, condition: null,
        notes: [], knowledge_contributions: [], consolidation_record: [{ test: "lexical",
          in_word_share: round3(inWord), other_words: { confident: otherConfident, occurrences: otherOccurrences } }] });
      result.push(target);
    }
    return result;
  }
  function buildIntervention(target, poolUnits, poolFluency, poolInputs) {
    if (target.kind === "CLARITY") return null;
    const isFluency = target.kind === "FLUENCY";
    const unitPool = isFluency ? poolFluency : poolUnits;
    const support = unitPool.filter((u) => target.unit_ids.includes(u.unit_id));
    const confident = support.filter((u) => u.quality === "confident");
    const recentUnique = (items, limit, alreadyOrdered = false) => {
      const out = [], seen = new Set();
      for (const u of alreadyOrdered ? items : items.slice().sort(byRecent)) {
        const key = u.lexical_key || u.unit_id;
        if (seen.has(key) || !unitRefOk(u.audio)) continue;
        seen.add(key); out.push(u);
        if (out.length === limit) break;
      }
      return out;
    };
    const example = (u, role) => ({ ...u.audio, unit_id: u.unit_id, role,
      expected: u.expected, heard: role === "heard_differently" ? u.heard : u.expected, confidence: u.m4_confidence });
    const examples = recentUnique(isFluency ? support : confident, COACH_CAL.n_examples)
      .map((u) => example(u, isFluency ? "noticed" : "heard_differently"));
    const counters = isFluency ? [] : counterUnits(target, poolUnits);
    const exampleWords = new Set(examples.map((e) => (support.find((u) => u.unit_id === e.unit_id)?.lexical_key || "")));
    counters.sort((a, b) => Number(exampleWords.has(b.lexical_key)) - Number(exampleWords.has(a.lexical_key)) || byRecent(a, b));
    const counterExamples = recentUnique(counters, COACH_CAL.n_counter, true).map((u) => example(u, "heard_as_expected"));
    const wordCounts = new Map();
    for (const u of confident) if (!u.fragment_suspect) {
      const old = wordCounts.get(u.lexical_key) || { n: 0, sentences: new Set() };
      old.n++; old.sentences.add(u.sentence_key); wordCounts.set(u.lexical_key, old);
    }
    const words = isFluency ? [] : [...wordCounts].sort((a, b) =>
      b[1].n - a[1].n || b[1].sentences.size - a[1].sentences.size || compareText(a[0], b[0]))
      .slice(0, COACH_CAL.n_words).map(([word, count]) => {
        const u = confident.slice().sort(byRecent).find((x) => x.lexical_key === word);
        const ref = u ? { ...u.audio, play_ms: u.audio.word_play_ms || u.audio.play_ms, kind: "word", unit_id: u.unit_id } : null;
        return { word: u?.word || word, times_heard_differently: count.n, ref: unitRefOk(ref) ? ref : null };
      });
    const readingById = new Map(poolInputs.map((p) => [p.reading.reading_id, p.reading]));
    const perSentence = new Map();
    for (const u of support) {
      if (!perSentence.has(u.sentence_key)) perSentence.set(u.sentence_key, []);
      perSentence.get(u.sentence_key).push(u);
    }
    const retest = [...perSentence].sort((a, b) => b[1].length - a[1].length ||
      String(b[1].map((u) => u.recorded_at).sort().at(-1)).localeCompare(String(a[1].map((u) => u.recorded_at).sort().at(-1))))
      .flatMap(([sentenceKey, us]) => {
        const latest = us.slice().sort(byRecent)[0], r = readingById.get(latest.reading_id);
        return r && unitRefOk(r.sentence_ref) ? [{ text: r.sentence_text, sentence_key: sentenceKey,
          reading_id: r.reading_id, units: us.length, ref: { ...r.sentence_ref, kind: "sentence" } }] : [];
      }).slice(0, COACH_CAL.n_retest);
    if (!examples.length || !retest.length) return null;
    let guidance = [], trainability;
    if (isFluency) trainability = "phrase_practice";
    else {
      const seen = new Set();
      for (const pair of target.pairs) {
        const sounds = pair.slice().sort(), key = sounds.join("~");
        if (seen.has(key)) continue;
        seen.add(key);
        const text = CONTRAST_GUIDANCE.get(key);
        if (text) guidance.push({ entry: `guidance:${key}`, contributed: "general guidance", text, origin: "knowledge" });
      }
      if (target.kind === "LEXICAL") trainability = "word_practice";
      else if (guidance.length) trainability = "specific_guidance";
      else if (counterExamples.length >= 2) trainability = "listen_compare_fallback";
      else return null;
    }
    const firstPair = target.pairs[0] || [];
    const labels = firstPair.map((s) => `/${s}/`);
    const actionText = isFluency
      ? ({ hesitation: "Practise reading in phrase-sized chunks", filler: "Practise pausing silently between phrases",
        repetition: "Practise reading the affected phrases smoothly before the whole sentence" })[target.group]
      : target.kind === "LEXICAL" ? `Practise the word ‘${target.word}’`
        : target.kind === "CONDITIONED" ? `Practise ${target.sounds.map((s) => `/${s}/`).join(", ")} ${target.condition_label}`
          : target.kind === "SET" ? `Practise telling apart ${familyTitle(target.family_id)} (${uniq(target.pairs.flat()).sort().map((s) => `/${s}/`).join(", ")})`
            : `Practise ${target.two_way ? "telling" : "keeping"} ${labels[0]} ${target.two_way ? "and" : "distinct from"} ${labels[1]}${target.two_way ? " apart" : ""}`;
    const transfer = isFluency ? { origin: "hypothesised", potential_words: [],
      text: "Reading in phrases may also help other sentences; this is a possibility, not something measured." }
      : { origin: "hypothesised", potential_words: [], text: "This practice may help other words; transfer is not measured." };
    return { trainability, level: TRAINABILITY[trainability], action_text: actionText, examples, counter_examples: counterExamples,
      words, retest, guidance, transfer };
  }
  function compareCandidates(a, b, trainability) {
    const ordering = COACH_CAL.ordering;
    const tierValue = { established: 2, emerging: 1, insufficient: 0 };
    const get = (t, criterion) => criterion === "tier" ? tierValue[t.tier]
      : criterion === "trainability" ? trainability(t)?.level ?? null
      : criterion === "breadth_sentences" ? t.measures.conf_sentences
      : criterion === "recurrence_sessions" ? t.measures.conf_sessions
      : criterion === "breadth_words" ? (["CONTRAST", "SET", "CONDITIONED", "LEXICAL"].includes(t.kind)
        ? t.measures.conf_words : null)
      : criterion === "coverage" ? t.measures.confident
      : criterion === "reach" ? t.measures.reach
      : criterion === "concentration" ? t.measures.concentration : null;
    const ratioCriteria = new Set(["breadth_sentences", "recurrence_sessions", "breadth_words", "coverage", "reach"]);
    const sameModalityOnly = new Set(["breadth_words", "coverage", "reach", "concentration"]);
    for (const criterion of ordering) {
      if ((a.kind === "FLUENCY") !== (b.kind === "FLUENCY") && sameModalityOnly.has(criterion)) continue;
      const av = get(a, criterion), bv = get(b, criterion);
      if (av == null || bv == null) continue;
      const diff = Math.abs(av - bv), margin = COACH_CAL.margins_abs[criterion] || 0;
      const meaningful = criterion === "tier" ? av !== bv : diff >= margin &&
        (ratioCriteria.has(criterion) ? Math.min(av, bv) <= 0 || Math.max(av, bv) / Math.min(av, bv) >= 1.5 : diff > 0);
      if (meaningful) {
        const winner = av > bv ? a : b, loser = av > bv ? b : a;
        const winnerValue = winner === a ? av : bv, loserValue = winner === a ? bv : av;
        return { winner: winner.target_id, loser: loser.target_id, criterion,
          winner_value: comparisonValue(criterion, winnerValue),
          loser_value: comparisonValue(criterion, loserValue), tie: false,
          criterion_label: COMPARISON_LABELS[criterion] };
      }
    }
    const winner = a.target_id < b.target_id ? a : b, loser = winner === a ? b : a;
    return { winner: winner.target_id, loser: loser.target_id, criterion: "tie_break",
      winner_value: winner.target_id, loser_value: loser.target_id, tie: true,
      criterion_label: COMPARISON_LABELS.tie_break };
  }
  const COMPARISON_LABELS = {
    tier: "evidence tier", breadth_sentences: "breadth (different sentences)",
    recurrence_sessions: "recurrence (different sessions)", breadth_words: "breadth (different words)",
    coverage: "coverage (confident observations it accounts for)", reach: "reach (how much of your reading contains it)",
    concentration: "consistency of the pattern (concentration)", trainability: "trainability (specific practice guidance available)",
    tie_break: "no meaningful difference (stable order)",
  };
  function comparisonValue(criterion, value) {
    if (criterion === "tier") return ({ 2: "established", 1: "emerging", 0: "insufficient" })[value];
    if (criterion === "trainability") return ({ 2: "specific guidance",
      1: "listen-and-compare / word or phrase practice", 0: "none" })[value] ?? value;
    return value;
  }
  function selectCoachingActions(candidates, allCandidates, poolUnits, poolFluency, poolInputs, getIntervention, consolidation) {
    const selected = [], rounds = [], absorbed = [], notPrioritised = [], remaining = candidates.slice();
    const taken = new Set();
    let fluencyCount = 0;
    const reevaluate = (target, ids) => {
      const clone = { ...target, unit_ids: ids };
      const result = evidenceMetrics(clone, poolUnits.concat(poolFluency), uniq(poolInputs.map((p) => p.reading.sentence_key)).length);
      return Object.assign(clone, result, { tier: result.tier, measures: result.measures, checks: result.checks });
    };
    const trainability = (target) => getIntervention(target);
    while (selected.length < COACH_CAL.max_actions) {
      const eligible = [];
      for (const candidate of [...remaining]) {
        const residual = candidate.unit_ids.filter((id) => !taken.has(id));
        const current = residual.length === candidate.unit_ids.length ? candidate : reevaluate(candidate, residual);
        if (current.tier !== "established" || !trainability(current)) {
          remaining.splice(remaining.indexOf(candidate), 1);
          absorbed.push({ target: candidate.target_id, absorbed_by: selected.at(-1)?.target_id || null,
            residual_units: residual.length, original_units: candidate.unit_ids.length,
            failed: Object.entries(current.checks).filter(([, check]) => !check.ok).map(([name]) => name),
            reason: "its remaining evidence no longer passes the gates once the selected action's observations are removed" });
          continue;
        }
        if (current.kind === "FLUENCY" && fluencyCount >= COACH_CAL.max_fluency) continue;
        eligible.push(current);
      }
      if (!eligible.length) break;
      const sorted = eligible.slice().sort((a, b) => compareText(a.target_id, b.target_id));
      let champion = sorted[0];
      const comparisons = [];
      for (const challenger of sorted.slice(1)) {
        const record = compareCandidates(champion, challenger, trainability);
        comparisons.push(record);
        if (record.winner === challenger.target_id) champion = challenger;
      }
      rounds.push({ pick: selected.length + 1, winner: champion.target_id,
        candidates: eligible.map((x) => x.target_id).sort(), comparisons });
      selected.push(champion); taken.forEach(() => {});
      champion.unit_ids.forEach((id) => taken.add(id));
      if (champion.kind === "FLUENCY") fluencyCount++;
      const ix = remaining.findIndex((x) => x.target_id === champion.target_id);
      if (ix >= 0) remaining.splice(ix, 1);
    }
    const actionRows = selected.map((target, ix) => {
      const iv = getIntervention(target);
      const decided = rounds.flatMap((round) => round.winner === target.target_id
        ? round.comparisons.filter((r) => r.winner === target.target_id) : []);
      const citedCounters = counterUnits(target, poolUnits).map((u) => u.unit_id);
      const measure = target.measures;
      let what;
      if (target.kind === "FLUENCY") what = `possible ${target.group} events`;
      else if (target.kind === "SET") what = `the sounds ${uniq(target.pairs.flat()).sort().map((s) => `/${s}/`).join(", ")} were heard as one another`;
      else if (target.kind === "CONDITIONED") {
        const [a, b] = target.pairs[0];
        what = `${target.sounds.map((s) => `/${s}/`).join(", ")} was heard differently ${target.condition_label}`;
      } else if (target.kind === "LEXICAL") what = `‘${target.word}’ was heard differently`;
      else {
        const [a, b] = target.pairs[0];
        what = target.two_way ? `/${a}/ and /${b}/ were heard as each other` : `/${a}/ was heard as /${b}/`;
      }
      const across = target.kind === "LEXICAL" ? `in ${measure.conf_readings} readings` : `across ${measure.conf_words} words`;
      let whyText = target.kind === "FLUENCY"
        ? `In ${measure.conf_sessions} sessions, ${target.group === "hesitation" ? "possible hesitation pauses inside phrases"
          : target.group === "filler" ? "possible filler sounds" : "possible repetitions or restarts"} were noticed ${measure.confident} times across ${measure.conf_sentences} sentences.`
        : `In ${measure.conf_sessions} sessions, ${what} ${measure.confident} times ${across}.`;
      if (target.kind !== "FLUENCY" && measure.counter) {
        const singular = target.sounds.length === 1;
        whyText += ` ${singular ? `/${target.sounds[0]}/ was` : "These sounds were"} heard as expected in ${measure.counter} other occurrences`;
        if (measure.counter >= measure.confident) whyText += singular ? ", so you already produce it; the issue appears to be consistency." :
          ", so you already produce them; the issue appears to be consistency.";
        else whyText += ".";
      }
      if (measure.reach && target.kind !== "LEXICAL") {
        const singular = target.sounds.length === 1;
        whyText += ` ${singular ? "It occurs" : "They occur"} in about ${Math.round(measure.reach)} of every 100 words you read.`;
      }
      const listen = { step: "listen", text: target.kind === "FLUENCY"
        ? "Listen to the moments where a pause or repeat was noticed."
        : target.kind === "LEXICAL" ? `Listen to your readings of ‘${target.word}’.`
          : "Listen to your own examples: first heard as expected, then heard differently.",
        examples: target.kind === "FLUENCY" ? iv.examples : iv.counter_examples.concat(iv.examples) };
      if (target.kind === "LEXICAL") listen.examples = iv.examples.concat(iv.counter_examples);
      const retest = { step: "retest", text: "Record these sentences again (Read these now).", sentences: iv.retest };
      let steps;
      if (target.kind === "FLUENCY") steps = [listen,
        { step: "chunk", text: "Mark the phrase breaks at commas and full stops; read one phrase at a time." },
        { step: "sentences", text: "Read the whole sentence in phrases.", sentences: iv.retest }, retest]
      else if (target.kind === "LEXICAL") steps = [listen,
        { step: "word", text: `Say ‘${target.word}’ slowly, then at your normal pace.`,
          guidance: iv.guidance.length ? iv.guidance : null, words: iv.words }, 
        { step: "sentences", text: "Read these sentences slowly, then at your normal pace.", sentences: iv.retest }, retest];
      else if (target.kind === "CONDITIONED") steps = [listen,
        { step: "context", text: "Say the sound in this position, then in other positions, keeping it the same.",
          guidance: iv.guidance.length ? iv.guidance : null, fallback: iv.guidance.length ? null : COACH_FALLBACK },
        { step: "words", text: "Say these words from your readings, slowly, then at your normal pace.", words: iv.words },
        { step: "sentences", text: "Read these sentences slowly, then at your normal pace.", sentences: iv.retest }, retest];
      else steps = [listen, { step: "contrast", text: "Say the sounds slowly, one after the other, keeping them distinct.",
          guidance: iv.guidance.length ? iv.guidance : null, fallback: iv.guidance.length ? null : COACH_FALLBACK },
        { step: "words", text: "Say these words from your readings, slowly, then at your normal pace.", words: iv.words },
        { step: "sentences", text: "Read these sentences slowly, then at your normal pace.", sentences: iv.retest }, retest];
      return { rank_in_plan: ix + 1, action_text: iv.action_text,
        time_minutes: COACH_CAL.time_split[selected.length - 1][ix], target: publicTarget(target),
        why: { text: whyText, origin: "counted", measures: measure, decided_by: decided,
          absorbed: [...absorbed.filter((record) => record.absorbed_by === target.target_id),
            ...consolidation.filter((record) => record.absorbed_into === target.target_id ||
              (Array.isArray(record.absorbed_into) && record.absorbed_into.includes(target.target_id)))] },
        evidence_tier: target.tier, counter_evidence: { heard_as_expected: measure.counter, examples: iv.counter_examples },
        transfer: iv.transfer, practice: { trainability: iv.trainability, steps, guidance: iv.guidance,
          guidance_note: iv.guidance.length ? "General description of how these sounds are usually distinguished — not a measurement of your articulation." : null,
          examples: iv.examples, counter_examples: iv.counter_examples, words: iv.words, retest_sentences: iv.retest },
        supporting_unit_ids: target.unit_ids, counter_unit_ids: citedCounters,
        knowledge_contributions: [...(target.knowledge_contributions || []), ...iv.guidance],
        consolidation_record: target.consolidation_record || [], notes: target.notes || [], caveats: [] };
    });
    for (const candidate of remaining) {
      const residual = candidate.unit_ids.filter((id) => !taken.has(id));
      const current = residual.length === candidate.unit_ids.length ? candidate : reevaluate(candidate, residual);
      if (current.tier === "established" && trainability(current) && current.kind === "FLUENCY" && fluencyCount >= COACH_CAL.max_fluency)
        notPrioritised.push({ target: current, comparison: null, reason: `at most ${COACH_CAL.max_fluency} fluency action is shown at a time` });
    }
    return { actions: actionRows, rounds, absorbed, not_prioritised: notPrioritised };
  }
  function publicTarget(target) {
    const { support, confident, counters, has_audio, ...visible } = target;
    return { ...visible, pairs: target.pairs.map((pair) => pair.join("→")) };
  }
  return {
    COACHING_VERSION, READING_VERSION, KNOWLEDGE_VERSION, buildReadingFeedback, runCoaching,
    progress, update, newPracticeRecord, buildSummary,
    // Exporting the normaliser makes evidence decisions inspectable in browser tests and diagnostics.
    normaliseInput,
  };
});
