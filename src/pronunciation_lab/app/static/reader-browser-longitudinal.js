"use strict";

(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PronounceReaderLongitudinal = api;
})(globalThis, function () {
  const PROGRESS_VERSION = "m10.1";
  const IDENTITY_VERSION = "m10-id.1";
  const EXTRACTOR_VERSION = "m10-src.1";
  const STATES_VERSION = "m10-states.1";
  const CONTEXT_VERSION = "m10-ctx.1";
  const NOISE_VERSION = "m10-noise.1";
  const PRACTICE_VERSION = "m10-practice.1";
  const DECISIONS_VERSION = "m10-decisions.1";
  const PRACTICE_SOURCE = "What to practise now";
  const CLASSES = ["FRESH", "REPEAT", "RETEST", "PRACTICE"];
  const OPPORTUNITY = new Set(["counter", "confident", "supporting"]);
  const CONTEXT_DIMENSIONS = ["word_position", "dictionary_stress", "cluster", "sentence_position"];
  const CTX_FIELD = {
    word_position: "pos", dictionary_stress: "st", cluster: "cl", sentence_position: "sp",
  };
  const FLUENCY_LABEL = {
    hesitation: "possible hesitation pauses inside phrases",
    filler: "possible filler sounds",
    repetition: "possible repetitions or restarts",
  };
  const LABELS = {
    "word_position|initial": "at the start of words",
    "word_position|medial": "in the middle of words",
    "word_position|final": "at the end of words",
    "dictionary_stress|primary": "in syllables with main dictionary stress",
    "dictionary_stress|secondary": "in syllables with secondary dictionary stress",
    "dictionary_stress|unstressed": "in syllables without dictionary stress",
    "cluster|in_cluster": "in consonant clusters",
    "cluster|not_in_cluster": "outside consonant clusters",
    "sentence_position|first_word": "in the first word of a sentence",
    "sentence_position|inside": "inside sentences",
    "sentence_position|last_word": "in the last word of a sentence",
  };
  const DECISIONS = [
    "CHANGE_PRACTICE_METHOD", "MOVE_TO_FRESH_WORDS", "MOVE_TO_NEW_CONTEXT", "CONTINUE_CURRENT_TARGET",
    "INCREASE_CONTEXT_DIFFICULTY", "REDUCE_PRIORITY", "RETIRE", "WATCH_FOR_REGRESSION", "INSUFFICIENT_HISTORY",
  ];
  const ACTIVE_DECISIONS = new Set([
    "CHANGE_PRACTICE_METHOD", "MOVE_TO_FRESH_WORDS", "MOVE_TO_NEW_CONTEXT", "CONTINUE_CURRENT_TARGET",
  ]);
  const STATE_ORDER = [
    "REGRESSED", "PERSISTENT", "PERSONAL_RECURRING", "IMPROVING", "EMERGING", "STABLE", "RETIRED",
    "INSUFFICIENT_HISTORY",
  ];
  const DEFAULT_CALIBRATION = Object.freeze({
    version: "m10-cal.1",
    same_text_min_containment: 0.8, same_text_min_words: 5,
    personal_min_clear: 3, personal_min_sessions: 3, personal_min_articles: 2, personal_min_words: 2,
    direction_min_share: 0.5, emerging_min_clear: 3, emerging_min_sessions: 2,
    word_min_clear: 2, word_min_sessions: 2, word_active_min_articles: 2, article_bound_min_clear: 2,
    retire_min_opportunities: 40, retire_min_sessions: 3, retire_min_articles: 2,
    retire_min_fresh_word_share: 0.5, retire_min_expected: 3, retire_max_share: 0.25,
    improve_min_opportunities: 20, improve_min_sessions: 2, improve_min_expected: 2, improve_max_share: 0.5,
    persist_min_sessions: 3, persist_min_expected: 3, persist_min_share: 0.75, watch_min_sessions: 2,
    context_min_opportunities: 4, context_min_clear: 2, context_ratio: 2, context_max_share: 0.6,
    noise_min_reads: 4, noise_min_first_half_clear: 3, noise_min_sentences: 3,
    outcome_min_sessions: 2, outcome_min_expected: 3, outcome_practised_min_expected: 2,
    reverse_min_clear: 3, reverse_min_sessions: 2, reverse_ratio: 2, method_change_after_continuing: 2,
  });
  const FORBIDDEN = [
    /fixed forever/i, /\bcaused?\b/i, /practice fixed/i, /\bnative/i, /%/, /\bconfiden/i, /master/i,
    /clinical/i, /\bscore/i, /\brank/i, /streak/i, /improved by/i, /better than/i, /\balways\b/i,
    /\bguarantee/i, /\bprove[sd]?\b/i, /\bpercent/i,
  ];
  const FORBIDDEN_KEYS = new Set([
    "score", "percent", "percentage", "confidence", "rank", "streak", "progress_score",
  ]);

  function lexicalCompare(a, b) {
    const left = String(a), right = String(b);
    return left < right ? -1 : left > right ? 1 : 0;
  }
  function sorted(values) { return [...values].sort(lexicalCompare); }
  function round(value, digits) {
    const factor = 10 ** digits;
    const scaled = Math.abs(value * factor);
    const lower = Math.floor(scaled);
    const fraction = scaled - lower;
    const rounded = fraction > 0.5 || (fraction === 0.5 && lower % 2 === 1) ? lower + 1 : lower;
    return Math.sign(value) * rounded / factor;
  }
  function counterAdd(counter, key, value = 1) {
    if (key === null || key === undefined) return;
    counter[key] = (counter[key] || 0) + value;
  }
  function mergeCounters(target, source) {
    for (const [key, value] of Object.entries(source || {})) counterAdd(target, key, value);
    return target;
  }
  function sum(values) { return values.reduce((total, value) => total + value, 0); }
  function unique(values) { return [...new Set(values)]; }
  function compareTimeId(a, b) {
    return lexicalCompare(a.time || "", b.time || "") || lexicalCompare(a.session_id || "", b.session_id || "");
  }
  function textKey(text) {
    const normalized = String(text || "").normalize("NFKC").toLowerCase()
      .replace(/[^\p{L}\p{N}_']+/gu, " ").trim();
    return normalized;
  }
  function contentWords(text) {
    const functionWords = new Set([
      "a", "an", "the", "to", "of", "and", "in", "on", "at", "for", "from", "with", "by", "as", "into",
      "over", "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them", "my", "your",
      "his", "its", "our", "their", "this", "that", "these", "those", "is", "are", "was", "were", "be",
      "been", "am", "has", "have", "had", "do", "does", "did", "will", "would", "can", "could", "should",
      "shall", "may", "might", "must", "not", "no", "so", "but", "or", "if", "than", "then", "there",
      "what", "which", "who", "when", "where", "although", "before", "during", "still",
    ]);
    return new Set(String(text || "").split(/\s+/u).map((word) =>
      word.normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}_']/gu, "")).filter((word) =>
      word && !functionWords.has(word)));
  }
  function articleTextGroups(articleTexts, cal) {
    const keys = sorted(Object.keys(articleTexts || {}));
    const words = Object.fromEntries(keys.map((key) => [key, contentWords(articleTexts[key])]));
    const parent = Object.fromEntries(keys.map((key) => [key, key]));
    const find = (key) => {
      while (parent[key] !== key) {
        parent[key] = parent[parent[key]];
        key = parent[key];
      }
      return key;
    };
    for (let i = 0; i < keys.length; i += 1) {
      for (let j = i + 1; j < keys.length; j += 1) {
        const a = keys[i], b = keys[j], wa = words[a], wb = words[b];
        const small = Math.min(wa.size, wb.size);
        if (small < cal.same_text_min_words) continue;
        let intersection = 0;
        for (const word of wa) if (wb.has(word)) intersection += 1;
        if (intersection / small >= cal.same_text_min_containment) {
          const ra = find(a), rb = find(b);
          parent[ra > rb ? ra : rb] = ra < rb ? ra : rb;
        }
      }
    }
    return Object.fromEntries(keys.map((key) => [key, find(key)]));
  }

  function parsePattern(pattern) {
    if (String(pattern).startsWith("fluency:")) {
      return { kind: "fluency", group: pattern.slice("fluency:".length), expected: null, heard: null, context: null, word: null };
    }
    const match = /^sub:([^>@#]+)>([^>@#]+)(?:@([a-z_]+)=([^#]+))?(?:#word=(.+))?$/.exec(pattern);
    if (!match) throw new Error(`not a canonical pattern id: ${JSON.stringify(pattern)}`);
    return {
      kind: "sub", group: null, expected: match[1], heard: match[2],
      context: match[3] ? { dimension: match[3], value: match[4] } : null, word: match[5] || null,
    };
  }
  function broadId(expected, heard) { return `sub:${expected}>${heard}`; }
  function reverseOf(pattern) {
    const parsed = parsePattern(pattern);
    return parsed.kind === "fluency" ? null : broadId(parsed.heard, parsed.expected);
  }
  function contrastGroup(pattern) {
    const parsed = parsePattern(pattern);
    return parsed.kind === "fluency" ? null : `contrast:${sorted(unique([parsed.expected, parsed.heard])).join("~")}`;
  }
  function linkTarget(target = {}) {
    let patterns = [];
    if (["CONTRAST", "SET", "CONDITIONED", "LEXICAL"].includes(target.kind)) {
      const pairs = (target.pairs || []).map((pair) => typeof pair === "string" ? pair.split("→") : pair);
      patterns = sorted(unique(pairs.filter((pair) => pair.length === 2).map(([e, h]) => broadId(e, h))));
    } else if (target.kind === "FLUENCY" && target.group) patterns = [`fluency:${target.group}`];
    return {
      identity_version: IDENTITY_VERSION, m9_target_id: target.target_id ?? null, m9_kind: target.kind ?? null,
      patterns, context: target.condition ?? null, word: target.word ?? null, tracked: patterns.length > 0,
      reason: patterns.length ? null : "not tracked longitudinally (M5 'not detected' cannot mean absent)",
    };
  }
  function label(pattern) {
    const parsed = parsePattern(pattern);
    return parsed.kind === "fluency" ? (FLUENCY_LABEL[parsed.group] || parsed.group) :
      `/${parsed.expected}/ heard as /${parsed.heard}/`;
  }
  function contextValue(row, dimension) {
    const value = row[CTX_FIELD[dimension]];
    if (value === null || value === undefined) return null;
    return dimension === "cluster" ? (value ? "in_cluster" : "not_in_cluster") : String(value);
  }

  function classifyReadings(records, engine, designations) {
    const eligible = records.filter((record) =>
      record.eligible && record.reading.engine === engine).sort((a, b) =>
      lexicalCompare(a.reading.recorded_at || "", b.reading.recorded_at || "") ||
      lexicalCompare(a.reading.reading_id, b.reading.reading_id));
    const designated = [...designations].sort((a, b) => lexicalCompare(a[0], b[0]));
    const seenSentences = new Set(), seenWords = new Set(), out = [];
    for (const record of eligible) {
      const rd = record.reading, time = rd.recorded_at || "", key = rd.sentence_key;
      let cls = "FRESH";
      if (rd.practice_session) cls = "PRACTICE";
      else if (seenSentences.has(key)) cls = designated.some(([at, sentence]) => sentence === key && at <= time) ? "RETEST" : "REPEAT";
      const sounds = (record.sounds || []).map((sound) => ({
        ...sound, novelty: sound.w && !seenWords.has(sound.w) ? "FRESH_WORD" : "SEEN_WORD",
      }));
      out.push({ reading: rd, cls, sounds, fluency: record.fluency || [] });
      seenSentences.add(key);
      for (const sound of record.sounds || []) if (sound.w) seenWords.add(sound.w);
    }
    return out;
  }
  function newSession(sessionId, time, articleKey) {
    return {
      session_id: sessionId, time, article_key: articleKey, readings: [], sounds: {}, fluency_opps: 0,
      fluency_hits: {}, fluency_ids: {},
    };
  }
  function addContext(counterMap, dimension, value) {
    if (value === null) return;
    if (!counterMap[dimension]) counterMap[dimension] = {};
    counterAdd(counterMap[dimension], value);
  }
  function aggregate(classified, cls = "FRESH") {
    const sessions = new Map();
    for (const reading of classified) {
      if (reading.cls !== cls) continue;
      const rd = reading.reading;
      let session = sessions.get(rd.session_id);
      if (!session) {
        session = newSession(rd.session_id, rd.recorded_at || "", rd.text_id || rd.article_key);
        sessions.set(rd.session_id, session);
      }
      session.readings.push(rd.reading_id);
      for (const row of reading.sounds) {
        const expected = row.e;
        if (!expected || !OPPORTUNITY.has(row.q)) continue;
        let sound = session.sounds[expected];
        if (!sound) sound = session.sounds[expected] = {
          opportunities: 0, opps_by_word: {}, ctx_opps: {}, clear_differences: 0, pairs: {},
        };
        sound.opportunities += 1;
        counterAdd(sound.opps_by_word, row.w);
        for (const dimension of CONTEXT_DIMENSIONS) addContext(sound.ctx_opps, dimension, contextValue(row, dimension));
        if (row.out !== "heard_other" || !row.h) continue;
        let pair = sound.pairs[row.h];
        if (!pair) pair = sound.pairs[row.h] = {
          clear: 0, ambiguous: 0, clear_ids: [], ambiguous_ids: [], clear_words: {}, clear_by_word: {},
          ctx_clear: {}, sentences: new Set(),
        };
        if (row.q === "confident") {
          sound.clear_differences += 1;
          pair.clear += 1;
          pair.clear_ids.push(row.o);
          counterAdd(pair.clear_by_word, row.w);
          if (row.w && !row.frag) counterAdd(pair.clear_words, row.w);
          pair.sentences.add(rd.sentence_key);
          for (const dimension of CONTEXT_DIMENSIONS) addContext(pair.ctx_clear, dimension, contextValue(row, dimension));
        } else {
          pair.ambiguous += 1;
          pair.ambiguous_ids.push(row.o);
        }
      }
      if (rd.fluency_reliable) {
        session.fluency_opps += 1;
        const groups = unique(reading.fluency.filter((item) => item.q === "confident").map((item) => item.g));
        for (const group of groups) {
          counterAdd(session.fluency_hits, group);
          session.fluency_ids[group] = (session.fluency_ids[group] || []).concat(
            reading.fluency.filter((item) => item.q === "confident" && item.g === group).map((item) => item.o));
        }
      }
    }
    return [...sessions.values()].sort(compareTimeId);
  }
  function soundPoints(sessions, expected, heard) {
    const out = [];
    for (const session of sessions) {
      const sound = session.sounds[expected];
      if (!sound || !sound.opportunities) continue;
      const pair = sound.pairs[heard] || {};
      out.push({
        session_id: session.session_id, time: session.time, article: session.article_key,
        opportunities: sound.opportunities, clear: pair.clear || 0, ambiguous: pair.ambiguous || 0,
        clear_differences: sound.clear_differences, clear_words: { ...(pair.clear_words || {}) },
        clear_by_word: { ...(pair.clear_by_word || {}) }, opps_by_word: { ...sound.opps_by_word },
        ctx_opps: Object.fromEntries(Object.entries(sound.ctx_opps).map(([d, c]) => [d, { ...c }])),
        ctx_clear: Object.fromEntries(Object.entries(pair.ctx_clear || {}).map(([d, c]) => [d, { ...c }])),
        clear_ids: [...(pair.clear_ids || [])], ambiguous_ids: [...(pair.ambiguous_ids || [])],
        sentences: (pair.sentences || new Set()).size,
      });
    }
    return out;
  }
  function fluencyPoints(sessions, group) {
    return sessions.filter((session) => session.fluency_opps > 0).map((session) => ({
      session_id: session.session_id, time: session.time, article: session.article_key,
      opportunities: session.fluency_opps, clear: session.fluency_hits[group] || 0, ambiguous: 0,
      clear_differences: null, clear_words: {}, clear_by_word: {}, opps_by_word: {}, ctx_opps: {}, ctx_clear: {},
      clear_ids: [...(session.fluency_ids[group] || [])], ambiguous_ids: [],
      sentences: session.fluency_hits[group] || 0,
    }));
  }
  function trackedPatterns(sessions) {
    const out = new Set();
    for (const session of sessions) {
      for (const [expected, sound] of Object.entries(session.sounds)) {
        for (const [heard, pair] of Object.entries(sound.pairs)) if (pair.clear) out.add(broadId(expected, heard));
      }
      for (const [group, count] of Object.entries(session.fluency_hits)) if (count) out.add(`fluency:${group}`);
    }
    return sorted(out);
  }
  function classCounts(classified, expected, heard, group = null) {
    const out = Object.fromEntries(CLASSES.map((name) => [name, { readings: 0, opportunities: 0, clear: 0, ambiguous: 0 }]));
    for (const reading of classified) {
      const counts = out[reading.cls];
      counts.readings += 1;
      if (group !== null) {
        if (reading.reading.fluency_reliable) {
          counts.opportunities += 1;
          if (reading.fluency.some((item) => item.g === group && item.q === "confident")) counts.clear += 1;
        }
        continue;
      }
      for (const row of reading.sounds) {
        if (row.e !== expected || !OPPORTUNITY.has(row.q)) continue;
        counts.opportunities += 1;
        if (row.out === "heard_other" && row.h === heard) {
          counts[row.q === "confident" ? "clear" : "ambiguous"] += 1;
        }
      }
    }
    return out;
  }
  function window(points) {
    const clearWords = {}, clearByWord = {}, oppsByWord = {};
    for (const point of points) {
      mergeCounters(clearWords, point.clear_words);
      mergeCounters(clearByWord, point.clear_by_word);
      mergeCounters(oppsByWord, point.opps_by_word);
    }
    const diffs = points.map((point) => point.clear_differences).filter((value) => value !== null && value !== undefined);
    const clear = sum(points.map((point) => point.clear)), opportunities = sum(points.map((point) => point.opportunities));
    return {
      sessions: points.length, articles: unique(points.map((point) => point.article)).length,
      opportunities, clear, ambiguous: sum(points.map((point) => point.ambiguous)),
      clear_sessions: points.filter((point) => point.clear).length,
      clear_articles: unique(points.filter((point) => point.clear).map((point) => point.article)).length,
      clear_words: clearWords, clear_by_word: clearByWord, opps_by_word: oppsByWord,
      clear_differences: diffs.length ? sum(diffs) : null,
      direction_share: diffs.length && sum(diffs) ? round(clear / sum(diffs), 3) : null,
      rate: opportunities ? round(clear / opportunities, 4) : null,
      session_ids: points.map((point) => point.session_id),
    };
  }
  function summary(w) {
    return Object.fromEntries([
      "sessions", "articles", "opportunities", "clear", "ambiguous", "clear_sessions", "clear_articles",
      "direction_share", "rate",
    ].map((key) => [key, w[key]]).concat([["words", Object.keys(w.clear_words).length]]));
  }
  function personalGates(w, cal, fluency) {
    const gates = {
      clear: w.clear >= cal.personal_min_clear,
      sessions: w.clear_sessions >= cal.personal_min_sessions,
      articles: w.clear_articles >= cal.personal_min_articles,
    };
    if (!fluency) {
      gates.words = Object.keys(w.clear_words).length >= cal.personal_min_words;
      gates.direction = w.direction_share !== null && w.direction_share >= cal.direction_min_share;
    }
    return gates;
  }
  function classifyScope(w, cal, fluency) {
    if (Object.values(personalGates(w, cal, fluency)).every(Boolean)) return "PERSONAL_RECURRING";
    if (!fluency && w.clear >= cal.word_min_clear && Object.keys(w.clear_by_word).length === 1 &&
        Object.keys(w.clear_words).length === 1 && w.clear_sessions >= cal.word_min_sessions) return "WORD_SPECIFIC";
    if (w.clear >= cal.article_bound_min_clear && w.clear_articles === 1) return "ARTICLE_BOUND";
    return "INSUFFICIENT_HISTORY";
  }
  function emerging(w, cal, fluency) {
    return w.clear >= cal.emerging_min_clear && w.clear_sessions >= cal.emerging_min_sessions &&
      w.clear_articles >= cal.personal_min_articles && (fluency ||
        (Object.keys(w.clear_words).length >= cal.personal_min_words && w.direction_share !== null &&
          w.direction_share >= cal.direction_min_share));
  }
  function laterCheck(baseline, later, cal, noise, fluency) {
    const expected = round((baseline.rate || 0) * later.opportunities, 2);
    const baseWords = new Set(Object.keys(baseline.opps_by_word));
    const freshWordChances = sum(Object.entries(later.opps_by_word)
      .filter(([word]) => !baseWords.has(word)).map(([, count]) => count));
    const freshShare = fluency ? 1 : (later.opportunities ? round(freshWordChances / later.opportunities, 3) : 0);
    const eff = noise.effective;
    const retire = later.opportunities >= cal.retire_min_opportunities && later.sessions >= cal.retire_min_sessions &&
      later.articles >= cal.retire_min_articles && freshShare >= cal.retire_min_fresh_word_share &&
      expected >= cal.retire_min_expected &&
      later.clear <= Math.floor(expected * eff.retire_max_share + 1e-9);
    const improving = later.opportunities >= cal.improve_min_opportunities &&
      later.sessions >= cal.improve_min_sessions && expected >= cal.improve_min_expected &&
      later.clear <= expected * eff.improve_max_share;
    const persistent = later.sessions >= cal.persist_min_sessions && expected >= cal.persist_min_expected &&
      later.clear >= expected * cal.persist_min_share;
    return {
      expected, observed: later.clear, opportunities: later.opportunities, sessions: later.sessions,
      articles: later.articles, fresh_word_share: freshShare, baseline_rate: baseline.rate,
      retire, improving, persistent,
    };
  }
  function fold(points, cal, noise, fluency = false) {
    let state = "INSUFFICIENT_HISTORY", start = 0, establishment = null, stableAt = null;
    let baseline = null, lastCheck = null;
    const transitions = [];
    let grow = [], later = [], postStable = [];
    function move(index, to, reason, evidence) {
      transitions.push({
        at_session: points[index].session_id, time: points[index].time, from: state, to, reason, evidence,
      });
      state = to;
    }
    for (let index = 0; index < points.length; index += 1) {
      const point = points[index];
      if (["INSUFFICIENT_HISTORY", "EMERGING"].includes(state)) {
        grow.push(point);
        const current = window(grow);
        if (Object.values(personalGates(current, cal, fluency)).every(Boolean)) {
          establishment = index;
          baseline = current;
          later = [];
          move(index, "PERSONAL_RECURRING", "recurring across texts: the personal gates are met", summary(current));
        } else if (state === "INSUFFICIENT_HISTORY" && emerging(current, cal, fluency)) {
          move(index, "EMERGING", "recurring in several readings, not enough history to call it personal", summary(current));
        }
      } else if (["PERSONAL_RECURRING", "PERSISTENT", "IMPROVING", "REGRESSED"].includes(state)) {
        later.push(point);
        const check = laterCheck(baseline, window(later), cal, noise, fluency);
        lastCheck = check;
        const evidence = Object.fromEntries([
          "expected", "observed", "opportunities", "sessions", "articles", "fresh_word_share", "baseline_rate",
        ].map((key) => [key, check[key]]));
        if (check.retire) {
          stableAt = index;
          postStable = [];
          move(index, "STABLE", "no longer recurring strongly enough to prioritise", evidence);
        } else if (check.improving && state !== "IMPROVING") {
          move(index, "IMPROVING", "occurring less often than the baseline predicts", evidence);
        } else if (check.persistent && state !== "PERSISTENT") {
          move(index, "PERSISTENT", "still recurring at its baseline rate despite more chances", evidence);
        }
      } else if (state === "STABLE" || state === "RETIRED") {
        postStable.push(point);
        const current = window(postStable);
        if (Object.values(personalGates(current, cal, fluency)).every(Boolean)) {
          start = stableAt + 1;
          establishment = index;
          baseline = current;
          later = [];
          move(index, "REGRESSED", "had become stable, then met the personal gates again", summary(current));
        } else if (state === "STABLE" && current.sessions >= cal.watch_min_sessions) {
          move(index, "RETIRED", "quiet through the watch window; monitored silently", summary(current));
        }
      }
    }
    const total = window(points);
    const out = {
      version: STATES_VERSION, state, scope: classifyScope(total, cal, fluency), transitions,
      totals: summary(total), clear_words: total.clear_words, baseline: null, later: null, post_stable: null,
    };
    if (baseline) {
      const baselineWindow = window(establishment === null ? [] :
        (state === "REGRESSED" ? points.slice(start, establishment + 1) : points.slice(0, establishment + 1)));
      out.baseline = summary(baselineWindow);
      out.baseline.session_ids = baselineWindow.session_ids;
      out.baseline.opps_by_word = baselineWindow.opps_by_word;
    }
    if (baseline && establishment !== null && !["STABLE", "RETIRED"].includes(state)) {
      out.later = laterCheck(baseline, window(later), cal, noise, fluency);
    } else if (lastCheck) out.later = lastCheck;
    if (stableAt !== null && ["STABLE", "RETIRED"].includes(state)) out.post_stable = summary(window(postStable));
    return out;
  }

  function contextTotals(points, dimension) {
    const opportunities = {}, clear = {};
    for (const point of points) {
      mergeCounters(opportunities, point.ctx_opps[dimension]);
      mergeCounters(clear, point.ctx_clear[dimension]);
    }
    return [opportunities, clear];
  }
  function contextSide(opportunities, clear, value) {
    const insideOpportunities = opportunities[value] || 0, insideClear = clear[value] || 0;
    return [insideOpportunities, insideClear,
      sum(Object.values(opportunities)) - insideOpportunities, sum(Object.values(clear)) - insideClear];
  }
  function contextProfile(points, cal) {
    if (!points.length || points[0].clear_differences === null) {
      return { version: CONTEXT_VERSION, dimensions: {}, concentrated: [], supported: false };
    }
    const half = Math.ceil(points.length / 2), halves = [points.slice(0, half), points.slice(half)];
    const dimensions = {}, concentrated = [];
    for (const dimension of CONTEXT_DIMENSIONS) {
      const [opportunities, clear] = contextTotals(points, dimension);
      const rows = [];
      for (const value of sorted(Object.keys(opportunities))) {
        const [insideO, insideC, outsideO, outsideC] = contextSide(opportunities, clear, value);
        const insideRate = insideO ? insideC / insideO : null, outsideRate = outsideO ? outsideC / outsideO : null;
        const row = {
          value, label: LABELS[`${dimension}|${value}`] || value,
          inside: { opportunities: insideO, clear: insideC }, outside: { opportunities: outsideO, clear: outsideC },
        };
        const ok = insideO >= cal.context_min_opportunities && outsideO >= cal.context_min_opportunities &&
          insideO <= cal.context_max_share * (insideO + outsideO) && insideC >= cal.context_min_clear &&
          insideRate !== null && (outsideRate === 0 || insideRate >= cal.context_ratio * outsideRate);
        const stable = halves.map((halfPoints) => {
          const [ho, hc] = contextTotals(halfPoints, dimension);
          const [io, ic, oo, oc] = contextSide(ho, hc, value);
          return Boolean(io && ic && ic > (oo ? oc / oo : 0) * io);
        });
        row.stable_in_both_halves = stable.every(Boolean) && points.length >= 2;
        row.concentrated = Boolean(ok && row.stable_in_both_halves);
        rows.push(row);
        if (row.concentrated) concentrated.push({
          dimension, value, label: row.label, inside: row.inside, outside: row.outside,
        });
      }
      dimensions[dimension] = rows;
    }
    return {
      version: CONTEXT_VERSION, dimensions, concentrated, supported: true,
      note: concentrated.some((item) => item.dimension === "dictionary_stress")
        ? "Stress is the pronunciation dictionary's, not measured from your voice." : null,
    };
  }
  function noiseText(before, again, usable, lowest, count) {
    const parts = [];
    if (before) parts.push(`When you re-read the same sentence, a sound heard clearly as something else was heard the same way again in ${again} of ${before} cases, so one re-read is not evidence of change.`);
    if (usable && lowest !== null && lowest < 1) parts.push(`Across ${count} sentences you re-read several times, the later readings had as few as ${lowest} of the earlier readings' clear differences on the same text; drops no larger than that are never called improvement.`);
    else if (usable) parts.push(`Across ${count} sentences you re-read several times, the later readings never had fewer clear differences than the earlier ones, so the default thresholds apply unchanged.`);
    else parts.push("Not enough re-read sentences yet to calibrate from your own readings; default thresholds apply.");
    return parts.join(" ");
  }
  function calibrateNoise(classified, cal) {
    const bySentence = new Map();
    for (const reading of classified) {
      const key = reading.reading.sentence_key;
      if (!bySentence.has(key)) bySentence.set(key, []);
      bySentence.get(key).push(reading);
    }
    const repeated = [...bySentence].filter(([, readings]) => readings.length >= 2);
    const positions = (reading) => {
      const out = new Map();
      for (const sound of reading.sounds) {
        if (sound.e && OPPORTUNITY.has(sound.q)) out.set(`${sound.wi}\0${sound.si}\0${sound.e}`, sound);
      }
      return out;
    };
    let clearBefore = 0, clearAgain = 0;
    for (const [, readings] of repeated) {
      const indexed = readings.map(positions);
      for (let i = 0; i < indexed.length; i += 1) {
        for (let j = i + 1; j < indexed.length; j += 1) {
          for (const [key, sound] of indexed[i]) {
            if (sound.q !== "confident" || sound.out !== "heard_other" || !indexed[j].has(key)) continue;
            clearBefore += 1;
            const again = indexed[j].get(key);
            if (again.q === "confident" && again.h === sound.h) clearAgain += 1;
          }
        }
      }
    }
    const ratios = [];
    for (const [key, readings] of repeated.sort(([a], [b]) => lexicalCompare(a, b))) {
      if (readings.length < cal.noise_min_reads) continue;
      const counts = readings.map((reading) =>
        reading.sounds.filter((sound) => sound.q === "confident" && sound.out === "heard_other").length);
      const half = Math.floor(counts.length / 2);
      const first = sum(counts.slice(0, half)), second = sum(counts.slice(-half));
      if (first >= cal.noise_min_first_half_clear) ratios.push({
        sentence: key.slice(0, 60), reads: readings.length, earlier: first, later: second, ratio: round(second / first, 3),
      });
    }
    const usable = ratios.length >= cal.noise_min_sentences;
    const lowest = ratios.length ? Math.min(...ratios.map((row) => row.ratio)) : null;
    const retire = usable ? Math.min(cal.retire_max_share, lowest) : cal.retire_max_share;
    const improve = usable ? Math.min(cal.improve_max_share, lowest) : cal.improve_max_share;
    return {
      version: NOISE_VERSION, source: usable ? "user_rereads" : "defaults",
      repeated_sentences: repeated.length, sentences_used: ratios.length,
      position_repeat: { clear_before: clearBefore, clear_again: clearAgain,
        share: clearBefore ? round(clearAgain / clearBefore, 3) : null },
      half_ratios: ratios, lowest_half_ratio: lowest,
      effective: { retire_max_share: retire, improve_max_share: improve },
      text: noiseText(clearBefore, clearAgain, usable, lowest, ratios.length),
    };
  }

  function materialWords(sentences) {
    const words = new Set();
    for (const sentence of sentences) {
      for (const token of String(sentence || "").split(/\s+/u)) {
        const word = token.normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}_']/gu, "");
        if (word) words.add(word);
      }
    }
    return sorted(words);
  }
  async function createPracticeRecord(practiceId, createdAt, sessionId, article, action, advice = {}) {
    const sentences = (article.segments || []).filter((segment) => segment.readable).map((segment) => segment.text);
    const link = linkTarget(action.target || {});
    const reversePatterns = sorted(unique(link.patterns
      .filter((pattern) => pattern.startsWith("sub:"))
      .map((pattern) => reverseOf(pattern))));
    const trainability = action.practice?.trainability;
    return {
      version: PRACTICE_VERSION, id: practiceId, created_at: createdAt,
      practice_session_id: sessionId, article_id: article.id, source: PRACTICE_SOURCE,
      advice: {
        coaching_generated_at: advice.generated_at ?? null, pool_fingerprint: advice.fingerprint ?? null,
        m9_target_id: action.target?.target_id ?? null, action_text: action.action_text ?? null,
        plan_position: action.rank_in_plan ?? null,
      },
      target: { ...link, identity_version: IDENTITY_VERSION },
      reverse_patterns: reversePatterns,
      practice_type: `retest_sentences:${trainability === null || trainability === undefined ? "None" : trainability}`,
      mode: null,
      material: {
        sentences: await Promise.all(sentences.map(async (text) => ({ text, sentence_key: await sentenceTextKey(text) }))),
        words: materialWords(sentences),
      },
    };
  }
  function practiceStatus(record, records) {
    const mine = records.filter((item) => item.reading.session_id === record.practice_session_id);
    const eligible = mine.filter((item) => item.eligible);
    const keys = new Set((record.material.sentences || []).map((sentence) => sentence.sentence_key));
    const read = new Set(eligible.map((item) => item.reading.sentence_key).filter((key) => keys.has(key)));
    const times = mine.map((item) => item.reading.recorded_at).filter(Boolean).sort();
    return {
      recordings: mine.length, eligible_recordings: eligible.length, sentences: keys.size,
      sentences_read: read.size, completion: !mine.length ? "not_started" :
        (keys.size && read.size === keys.size ? "completed" : "partial"),
      first_recording_at: times[0] || null, last_recording_at: times[times.length - 1] || null, duration: null,
    };
  }
  function splitPoints(points, practicedWords) {
    let practicedOpportunities = 0, practicedClear = 0, unpracticedOpportunities = 0, unpracticedClear = 0;
    const sessions = new Set();
    for (const point of points) {
      for (const [word, count] of Object.entries(point.opps_by_word)) {
        if (practicedWords.has(word)) practicedOpportunities += count;
        else {
          unpracticedOpportunities += count;
          if (count) sessions.add(point.session_id);
        }
      }
      for (const [word, count] of Object.entries(point.clear_by_word)) {
        if (practicedWords.has(word)) practicedClear += count;
        else unpracticedClear += count;
      }
    }
    return [practicedOpportunities, practicedClear, unpracticedOpportunities, unpracticedClear, sessions.size];
  }
  function practiceReadings(classified, record, expected, heard) {
    let opportunities = 0, clear = 0;
    const ids = [];
    for (const reading of classified) {
      if (reading.reading.session_id !== record.practice_session_id) continue;
      for (const sound of reading.sounds) {
        if (sound.e !== expected || !OPPORTUNITY.has(sound.q)) continue;
        opportunities += 1;
        if (sound.q === "confident" && sound.out === "heard_other" && sound.h === heard) {
          clear += 1;
          ids.push(sound.o);
        }
      }
    }
    return [opportunities, clear, ids];
  }
  function practiceOutcome(record, pattern, points, reversePoints, classified, status, cal, noise) {
    const parsed = parsePattern(pattern), share = noise.effective.improve_max_share;
    const start = status.first_recording_at || record.created_at, end = status.last_recording_at || record.created_at;
    const before = points.filter((point) => point.time < start);
    const after = points.filter((point) => point.time > end && point.session_id !== record.practice_session_id);
    const baseline = window(before), rate = baseline.rate || 0;
    const practicedWords = new Set(record.material.words || []);
    const [practicedOpportunities, practicedClear, unpracticedOpportunities, unpracticedClear, unpracticedSessions] =
      splitPoints(after, practicedWords);
    const [duringOpportunities, duringClear, duringIds] =
      practiceReadings(classified, record, parsed.expected, parsed.heard);
    const unpracticed = {
      opportunities: unpracticedOpportunities, clear: unpracticedClear, sessions: unpracticedSessions,
      expected: round(rate * unpracticedOpportunities, 2),
    };
    const practiced = {
      opportunities: practicedOpportunities, clear: practicedClear,
      expected: round(rate * practicedOpportunities, 2),
    };
    const during = {
      opportunities: duringOpportunities, clear: duringClear, expected: round(rate * duringOpportunities, 2),
      clear_ids: duringIds, evidence_class: "PRACTICE (retest; feedback only, never learning evidence)",
    };
    const reverseBefore = window(reversePoints.filter((point) => point.time < start));
    const reverseAfter = window(reversePoints.filter((point) => point.time > end));
    const reverseRate = reverseBefore.rate || 0;
    const reverse = {
      pattern: reverseOf(pattern), pre_rate: reverseBefore.rate, post_rate: reverseAfter.rate,
      post_clear: reverseAfter.clear, post_clear_sessions: reverseAfter.clear_sessions,
      post_words: Object.keys(reverseAfter.clear_words).length,
    };
    reverse.rose = Boolean(reverseAfter.clear >= cal.reverse_min_clear &&
      reverseAfter.clear_sessions >= cal.reverse_min_sessions && reverse.post_words >= 2 &&
      reverseAfter.rate !== null && reverseAfter.rate >= cal.reverse_ratio * reverseRate);
    const decreased = unpracticed.expected >= cal.outcome_min_expected &&
      unpracticed.clear <= unpracticed.expected * share;
    const practicedBetter = (practiced.expected >= cal.outcome_practised_min_expected &&
      practicedClear <= practiced.expected * share) ||
      (during.expected >= cal.outcome_practised_min_expected && duringClear <= during.expected * share);
    let outcome, reason;
    if (status.completion === "not_started") [outcome, reason] = ["INSUFFICIENT_OUTCOME", "practice was not started"];
    else if (!baseline.opportunities || baseline.clear === 0) [outcome, reason] = ["INSUFFICIENT_OUTCOME", "no baseline before the practice"];
    else if (unpracticedSessions < cal.outcome_min_sessions || unpracticed.expected < cal.outcome_min_expected) {
      [outcome, reason] = ["INSUFFICIENT_OUTCOME", "not enough chances in unpractised words since the practice"];
    } else if (decreased) {
      [outcome, reason] = reverse.rose ? ["REVERSE_DIRECTION", "the opposite direction rose"] :
        ["TRANSFER", "occurred less often in unpractised words than the earlier rate predicts"];
    } else if (practicedBetter) {
      [outcome, reason] = ["NO_TRANSFER", "less often in the practised material, but not in new words"];
    } else [outcome, reason] = ["CONTINUING", "still occurring in new words at about the earlier rate"];
    const context = record.target.context;
    let contextSplit = null;
    if (["initial", "medial", "final"].includes(context) && outcome === "TRANSFER") {
      const insideO = sum(after.map((point) => point.ctx_opps.word_position?.[context] || 0));
      const insideC = sum(after.map((point) => point.ctx_clear.word_position?.[context] || 0));
      const outsideO = sum(after.map((point) => point.opportunities)) - insideO;
      const outsideC = sum(after.map((point) => point.clear)) - insideC;
      const expected = round(rate * outsideO, 2);
      contextSplit = {
        context, inside: { opportunities: insideO, clear: insideC },
        outside: { opportunities: outsideO, clear: outsideC, expected },
        continuing_outside: expected >= cal.outcome_min_expected && outsideC > expected * share,
      };
    }
    return {
      version: PRACTICE_VERSION, practice_id: record.id, pattern, outcome, reason,
      baseline: { sessions: baseline.sessions, opportunities: baseline.opportunities, clear: baseline.clear, rate: baseline.rate },
      post_sessions: after.map((point) => point.session_id),
      unpractised: unpracticed, practised: practiced, during_practice: during,
      reverse, context_split: contextSplit,
    };
  }
  function formatTimes(count) { return count === 1 ? "once" : count === 2 ? "twice" : `${count} times`; }
  function formatAbout(value) {
    if (value === null || value === undefined) return "?";
    return value >= 10 ? String(round(value, 0)) : String(round(value, 1));
  }
  function stateText(state, fluency = false) {
    const lifecycle = state.state, totals = state.totals, later = state.later || {};
    const expected = later.expected, observed = later.observed, opportunities = later.opportunities, sessions = later.sessions;
    const chances = opportunities === null || opportunities === undefined ? "" :
      (fluency ? `${opportunities} measurable sentence${opportunities === 1 ? "" : "s"}` :
        `${opportunities} chance${opportunities === 1 ? "" : "s"}`);
    if (["INSUFFICIENT_HISTORY", "EMERGING"].includes(lifecycle) && state.scope === "WORD_SPECIFIC") {
      const word = Object.keys(state.clear_words)[0] || "one word";
      if (totals.clear_articles >= 2) return `Recurring in ‘${word}’ across ${totals.clear_articles} different texts: specific to this word so far.`;
      return `Appears limited to ‘${word}’ so far.`;
    }
    if (["INSUFFICIENT_HISTORY", "EMERGING"].includes(lifecycle) && state.scope === "ARTICLE_BOUND") return "Appears limited to the article tested so far.";
    if (lifecycle === "PERSONAL_RECURRING") {
      return `Recurring across ${totals.clear_sessions} readings of ${totals.clear_articles} different texts` +
        (totals.words ? `, in ${totals.words} words` : "") + " — likely personal.";
    }
    if (lifecycle === "PERSISTENT") return `This remains one of your recurring patterns: ${formatTimes(observed)} in ${chances} since it was established, where your earlier rate predicts about ${formatAbout(expected)}.`;
    if (lifecycle === "IMPROVING") return `Early evidence suggests it is occurring less often: ${observed ? formatTimes(observed) : "not observed"} in ${chances} across ${sessions} later readings, where your earlier rate predicts about ${formatAbout(expected)}.`;
    if (lifecycle === "STABLE" || lifecycle === "RETIRED") {
      const seen = !observed ? "not observed" : `observed ${formatTimes(observed)}`;
      const tail = lifecycle === "RETIRED" ? " It is still monitored silently." : "";
      return `Stable for now: ${seen} in ${chances} across ${sessions} later readings, where your earlier rate predicts about ${formatAbout(expected)}. No longer recurring strongly enough to prioritise.${tail}`;
    }
    if (lifecycle === "REGRESSED") return "This pattern had become stable, but it has started recurring again across several readings.";
    if (lifecycle === "EMERGING") return "Appearing across several recent readings, but there is not enough history yet to call it personal.";
    return "Not enough history yet.";
  }
  function unitFor(pattern, state, context) {
    const parsed = parsePattern(pattern);
    if (parsed.kind === "fluency") return { kind: "fluency", text: label(pattern) };
    if (state.scope === "WORD_SPECIFIC" && Object.keys(state.clear_words).length) {
      const word = Object.keys(state.clear_words)[0];
      return { kind: "word", word, text: `the word ‘${word}’ (/${parsed.expected}/)` };
    }
    const concentrated = context.concentrated || [];
    if (concentrated.length) {
      const item = concentrated[0];
      return { kind: "context", dimension: item.dimension, value: item.value, text: `/${parsed.expected}/ ${item.label}` };
    }
    return { kind: "sound", text: `/${parsed.expected}/ (heard as /${parsed.heard}/)` };
  }
  function decide(pattern, state, context, outcomes, wordMinArticles) {
    const judged = outcomes.filter((item) => item.outcome !== "INSUFFICIENT_OUTCOME");
    const latest = judged.length ? judged[judged.length - 1] : null;
    const continuing = judged.filter((item) => item.outcome === "CONTINUING").length;
    const unit = unitFor(pattern, state, context), name = label(pattern);
    let decision, reason;
    if (state.state === "STABLE") [decision, reason] = ["RETIRE", "stable for now"];
    else if (state.state === "RETIRED") [decision, reason] = ["WATCH_FOR_REGRESSION", "stable for now and monitored silently"];
    else if (state.state === "REGRESSED") [decision, reason] = ["CONTINUE_CURRENT_TARGET", "it reappeared after being stable"];
    else if (latest?.outcome === "REVERSE_DIRECTION") [decision, reason] = ["CHANGE_PRACTICE_METHOD", "the opposite direction rose after practice (possible overcorrection)"];
    else if (latest?.outcome === "NO_TRANSFER") [decision, reason] = ["MOVE_TO_FRESH_WORDS", "less often in the practised material, but not in new words"];
    else if (latest?.outcome === "TRANSFER" && latest.context_split?.continuing_outside) [decision, reason] = ["MOVE_TO_NEW_CONTEXT", "less often in the practised context, still occurring outside it"];
    else if (continuing >= 2) [decision, reason] = ["CHANGE_PRACTICE_METHOD", `still occurring in new words after ${continuing} practice rounds`];
    else if (state.state === "IMPROVING" || latest?.outcome === "TRANSFER") [decision, reason] = ["REDUCE_PRIORITY", "occurring less often than before"];
    else if (["PERSONAL_RECURRING", "PERSISTENT"].includes(state.state) ||
      (state.scope === "WORD_SPECIFIC" && state.totals.clear_articles >= wordMinArticles)) {
      [decision, reason] = ["CONTINUE_CURRENT_TARGET", `recurring${state.scope === "WORD_SPECIFIC" ? " in this word" : " across texts"}`];
    } else [decision, reason] = ["INSUFFICIENT_HISTORY", "not enough history yet"];
    const recommendations = {
      RETIRE: `Stop spending practice time on ${name} for now; it stays monitored.`,
      WATCH_FOR_REGRESSION: `No practice needed for ${name}; it is monitored silently.`,
      CONTINUE_CURRENT_TARGET: `Keep practising ${unit.text}.`,
      CHANGE_PRACTICE_METHOD: latest?.outcome === "REVERSE_DIRECTION"
        ? `Try a different method for ${name}: practise both directions, listening and comparing the two sounds.`
        : `Try a different method for ${name}: listen and compare before reading.`,
      MOVE_TO_FRESH_WORDS: `Practise ${unit.text} in new words, not only the practised sentences.`,
      MOVE_TO_NEW_CONTEXT: `Practise ${name} outside the context you practised.`,
      REDUCE_PRIORITY: `Lower priority for ${name} for now.`,
      INSUFFICIENT_HISTORY: "Keep reading; it is being watched.",
    };
    return {
      version: DECISIONS_VERSION, pattern, decision, active: ACTIVE_DECISIONS.has(decision), reason, unit,
      recommendation: recommendations[decision], based_on_outcome: latest?.practice_id || null,
    };
  }
  function orderKey(item) {
    const totals = item.totals;
    return [
      DECISIONS.indexOf(item.decision.decision), STATE_ORDER.indexOf(item.state), -totals.clear_sessions,
      -(totals.words || 0), -totals.clear, item.pattern,
    ];
  }
  function compareOrder(a, b) {
    const ka = orderKey(a), kb = orderKey(b);
    for (let i = 0; i < ka.length; i += 1) {
      const cmp = typeof ka[i] === "number" ? ka[i] - kb[i] : lexicalCompare(ka[i], kb[i]);
      if (cmp) return cmp;
    }
    return 0;
  }

  function reference(reading, row, evidenceClass) {
    return {
      observation: row.o, session_id: reading.session_id, attempt_id: reading.attempt_id,
      job_id: reading.job_id, segment_id: reading.segment_id, word: row.wd, play_ms: row.play,
      span_ms: row.span, timeline: "analysis_wav",
      url: `/api/sessions/${reading.session_id}/attempts/${reading.attempt_id}/audio`,
      evidence_class: evidenceClass, recorded_at: reading.recorded_at,
    };
  }
  function defaultEngine(records) {
    const eligible = records.filter((record) => record.eligible);
    const pool = eligible.length ? eligible : records.filter((record) => record.reading.engine);
    if (!pool.length) return null;
    pool.sort((a, b) => lexicalCompare(a.reading.recorded_at || "", b.reading.recorded_at || "") ||
      lexicalCompare(a.reading.reading_id, b.reading.reading_id));
    return pool[pool.length - 1].reading.engine;
  }
  function depthNote(result) {
    const count = result.history.fresh_sessions, articles = result.history.articles;
    if (!count) return "No eligible readings yet.";
    if (count < 3 || articles < 2) {
      return `${count} reading${count === 1 ? "" : "s"} of ${articles} text${articles === 1 ? "" : "s"} so far: patterns are described per reading; recurring personal patterns need at least 3 readings of 2 different texts.`;
    }
    return `${count} readings of ${articles} different texts so far.`;
  }
  function resultTexts(result) {
    const out = [result.depth || "", result.noise?.text || ""];
    for (const item of result.patterns || []) {
      out.push(item.text, item.decision.recommendation, item.decision.reason);
      for (const transition of item.transitions) out.push(transition.reason);
      for (const outcome of item.outcomes) out.push(outcome.reason);
    }
    return out;
  }
  function collectKeys(value, keys) {
    if (Array.isArray(value)) value.forEach((item) => collectKeys(item, keys));
    else if (value && typeof value === "object") {
      for (const [key, child] of Object.entries(value)) {
        keys.add(key);
        collectKeys(child, keys);
      }
    }
  }
  function validate(result, knownObservations) {
    const issues = [];
    for (const text of resultTexts(result)) {
      const safe = String(text).replace(/‘[^’]*’/gu, "‘…’");
      for (const pattern of FORBIDDEN) if (pattern.test(safe)) issues.push(`forbidden wording (${pattern}): ${safe.slice(0, 70)}`);
    }
    const keys = new Set();
    const { calibration, ...withoutCalibration } = result;
    collectKeys(withoutCalibration, keys);
    for (const key of FORBIDDEN_KEYS) if (keys.has(key)) issues.push(`a longitudinal result carries no '${key}'`);
    for (const item of result.patterns || []) {
      for (const example of item.examples) {
        if (!knownObservations.has(example.observation)) issues.push(`${item.pattern}: an example is not a source observation`);
      }
      for (const observation of item.clear_observations) {
        if (!knownObservations.has(observation)) {
          issues.push(`${item.pattern}: a counted observation is not a source observation`);
          break;
        }
      }
      if (!["INSUFFICIENT_HISTORY", "EMERGING", "PERSONAL_RECURRING", "PERSISTENT", "IMPROVING", "STABLE", "RETIRED", "REGRESSED"].includes(item.state) ||
          !["PERSONAL_RECURRING", "CONTEXT_SPECIFIC", "WORD_SPECIFIC", "ARTICLE_BOUND", "INSUFFICIENT_HISTORY"].includes(item.scope)) {
        issues.push(`${item.pattern}: unknown state or scope`);
      }
      if (!DECISIONS.includes(item.decision.decision)) issues.push(`${item.pattern}: unknown decision`);
      for (const outcome of item.outcomes) {
        if (!["TRANSFER", "NO_TRANSFER", "INSUFFICIENT_OUTCOME", "REVERSE_DIRECTION", "CONTINUING"].includes(outcome.outcome)) {
          issues.push(`${item.pattern}: unknown practice outcome`);
        }
      }
      if (["STABLE", "RETIRED", "IMPROVING"].includes(item.state) && !item.later) {
        issues.push(`${item.pattern}: a change claim without its later window`);
      }
    }
    const ordered = [...result.patterns].sort(compareOrder);
    if (ordered.some((item, index) => item.pattern !== result.patterns[index].pattern)) {
      issues.push("patterns are not in their deterministic order");
    }
    return issues;
  }

  function buildProgress(records = [], practiceRecords = [], advice = [], options = {}) {
    const cal = { ...DEFAULT_CALIBRATION, ...(options.calibration || {}) };
    practiceRecords = [...practiceRecords].sort((a, b) =>
      lexicalCompare(a.created_at, b.created_at) || lexicalCompare(a.id, b.id));
    advice = [...advice].sort((a, b) =>
      lexicalCompare(a.issued_at || "", b.issued_at || "") ||
      lexicalCompare(a.session_id || "", b.session_id || ""));
    const engines = sorted(unique(records.filter((record) => record.eligible && record.reading.engine)
      .map((record) => record.reading.engine)));
    const engine = options.engine || defaultEngine(records);
    const designations = [];
    for (const record of practiceRecords) {
      for (const sentence of record.material.sentences || []) designations.push([record.created_at, sentence.sentence_key]);
    }
    for (const item of advice) {
      for (const key of item.retest_sentence_keys || []) designations.push([item.issued_at, key]);
    }
    const classified = engine ? classifyReadings(records, engine, designations) : [];
    const noise = calibrateNoise(classified, cal);
    const fresh = aggregate(classified);
    const refs = new Map(), fluencyRefs = new Map();
    for (const record of classified) {
      for (const row of record.sounds) refs.set(row.o, reference(record.reading, row, record.cls));
      for (const item of record.fluency) {
        fluencyRefs.set(item.o, reference(record.reading,
          { o: item.o, wd: item.before, play: item.play, span: null }, record.cls));
      }
    }
    const practiceSessions = new Set(practiceRecords.map((record) => record.practice_session_id));
    const patternSet = new Set(trackedPatterns(fresh));
    for (const record of practiceRecords) for (const pattern of record.target.patterns || []) patternSet.add(pattern);
    const statuses = new Map(practiceRecords.map((record) => [
      record.id, practiceStatus(record, records.filter((item) => item.reading.engine === engine)),
    ]));
    const items = [];
    for (const pattern of sorted(patternSet)) {
      const parsed = parsePattern(pattern), fluency = parsed.kind === "fluency";
      const points = fluency ? fluencyPoints(fresh, parsed.group) : soundPoints(fresh, parsed.expected, parsed.heard);
      const longitudinalState = fold(points, cal, noise, fluency);
      const context = fluency
        ? { version: CONTEXT_VERSION, dimensions: {}, concentrated: [], supported: false }
        : contextProfile(points, cal);
      if (["PERSONAL_RECURRING", "PERSISTENT", "IMPROVING", "STABLE", "RETIRED", "REGRESSED"].includes(longitudinalState.state) &&
          context.concentrated.length && longitudinalState.scope === "PERSONAL_RECURRING") {
        longitudinalState.scope = "CONTEXT_SPECIFIC";
      }
      const outcomes = [];
      for (const record of practiceRecords) {
        if (record.target.patterns.includes(pattern) && !fluency) {
          const reverse = reverseOf(pattern), reverseParsed = parsePattern(reverse);
          outcomes.push(practiceOutcome(
            record, pattern, points, soundPoints(fresh, reverseParsed.expected, reverseParsed.heard), classified,
            statuses.get(record.id), cal, noise,
          ));
        }
      }
      const decision = decide(pattern, longitudinalState, context, outcomes, cal.word_active_min_articles);
      const clearIds = points.flatMap((point) => point.clear_ids);
      const referenceMap = fluency ? fluencyRefs : refs;
      const examples = clearIds.slice().reverse().filter((id) => referenceMap.has(id) &&
        (referenceMap.get(id).play_ms || fluency)).slice(0, 3).map((id) => referenceMap.get(id));
      const associatedAdvice = advice.filter((item) => (item.patterns || []).includes(pattern));
      items.push({
        pattern, label: label(pattern), kind: parsed.kind, contrast_group: contrastGroup(pattern),
        reverse: reverseOf(pattern), state: longitudinalState.state, scope: longitudinalState.scope,
        text: stateText(longitudinalState, fluency), transitions: longitudinalState.transitions,
        baseline: longitudinalState.baseline, later: longitudinalState.later,
        post_stable: longitudinalState.post_stable, totals: longitudinalState.totals,
        words: Object.entries(longitudinalState.clear_words).sort((a, b) => b[1] - a[1] || lexicalCompare(a[0], b[0]))
          .map(([word, clear]) => ({ word, clear })),
        context, series: points.map((point) => ({
          session_id: point.session_id, time: point.time, opportunities: point.opportunities,
          clear: point.clear, ambiguous: point.ambiguous,
        })),
        evidence_classes: classCounts(classified, parsed.expected, parsed.heard, fluency ? parsed.group : null),
        examples, clear_observations: clearIds,
        advice: {
          times_advised: associatedAdvice.length,
          first: associatedAdvice.length ? associatedAdvice[0].issued_at : null,
          last: associatedAdvice.length ? associatedAdvice[associatedAdvice.length - 1].issued_at : null,
        },
        practice: outcomes.map((outcome) => outcome.practice_id), outcomes, decision,
      });
    }
    items.sort(compareOrder);
    items.forEach((item, index) => { item.position = index + 1; });
    const eligibility = {};
    for (const record of records.filter((item) => item.reading.engine === engine)) {
      counterAdd(eligibility, record.reason || "eligible");
    }
    const legacy = sorted(unique(classified.filter((record) => record.cls === "PRACTICE")
      .map((record) => record.reading.session_id)).filter((sessionId) => !practiceSessions.has(sessionId)));
    const result = {
      version: PROGRESS_VERSION, identity_version: IDENTITY_VERSION, extractor_version: EXTRACTOR_VERSION,
      states_version: STATES_VERSION, calibration: cal, engine,
      engines: Object.fromEntries(engines.map((name) => [name, {
        eligible_readings: records.filter((record) => record.eligible && record.reading.engine === name).length,
        sessions: unique(records.filter((record) => record.eligible && record.reading.engine === name)
          .map((record) => record.reading.session_id)).length,
      }])),
      engine_note: "Histories are kept per engine and never pooled. OpenPronounce and raw Wav2Vec2 share one acoustic model, so agreement between them is not independent confirmation.",
      history: {
        readings: classified.length, fresh_readings: classified.filter((record) => record.cls === "FRESH").length,
        fresh_sessions: fresh.length,
        classes: classified.reduce((counts, record) => { counterAdd(counts, record.cls); return counts; }, {}),
        articles: unique(fresh.map((session) => session.article_key)).length,
      },
      eligibility: Object.fromEntries(Object.entries(eligibility).sort(([a], [b]) => lexicalCompare(a, b))),
      noise, patterns: items, next_practice: items.filter((item) => item.decision.active).map((item) => item.pattern),
      practice: practiceRecords.map((record) => ({
        record, status: statuses.get(record.id),
        outcomes: items.flatMap((item) => item.outcomes.filter((outcome) => outcome.practice_id === record.id)),
      })),
      legacy_practice_sessions: legacy.map((sessionId) => ({
        session_id: sessionId, linked: false,
        note: "practice session from before practice records; linked to its advice by title only, so it is never used for practice outcomes",
      })),
      advice_history: advice, generated_at: options.generated_at || null,
      input_fingerprint: options.input_fingerprint || null,
    };
    result.depth = depthNote(result);
    const known = new Set([...refs.keys(), ...fluencyRefs.keys()]);
    const issues = validate(result, known);
    result.integrity = { ok: issues.length === 0, issues };
    if (issues.length) {
      result.patterns = [];
      result.next_practice = [];
    }
    return result;
  }

  function adaptCoaching(coaching, progress) {
    let byPattern = new Map((progress.patterns || []).map((item) => [item.pattern, item]));
    const coachingEngine = coaching?.pool?.engine || null;
    if (coachingEngine && progress.engine && coachingEngine !== progress.engine) byPattern = new Map();
    const actions = (coaching?.actions || []).map((action, index) => {
      const link = linkTarget(action.target || {});
      const history = link.patterns.filter((pattern) => byPattern.has(pattern)).map((pattern) => {
        const item = byPattern.get(pattern);
        return {
          pattern, label: item.label, state: item.state, scope: item.scope,
          text: item.text, decision: item.decision.decision,
        };
      });
      const stable = history.length > 0 && history.length === link.patterns.length &&
        history.every((item) => ["STABLE", "RETIRED"].includes(item.state));
      return {
        index, target_id: action.target?.target_id || null, patterns: link.patterns, history,
        prioritised: !stable, note: stable ? "Stable for now in your history, so not prioritised." : null,
      };
    });
    const coachingPatterns = new Set(actions.flatMap((item) => item.patterns));
    const extra = (progress.patterns || []).filter((item) =>
      item.decision.active && item.decision.decision !== "CONTINUE_CURRENT_TARGET" && !coachingPatterns.has(item.pattern))
      .map((item) => item.pattern);
    return {
      actions, from_history: extra, engine: progress.engine, coaching_engine: coachingEngine,
    };
  }

  function recordsFromBrowserData(sessions, cache) {
    const records = new Map();
    const put = (record) => {
      if (record?.reading?.reading_id) records.set(record.reading.reading_id, record);
    };
    const sessionList = Array.isArray(sessions) ? sessions : [];
    const liveSessionIds = new Set(sessionList.map((session) => session.id).filter(Boolean));
    const cachedReadings = cache?.readings || {};
    for (const entry of Object.values(cachedReadings)) {
      const record = entry?.record || entry;
      if (!liveSessionIds.size || liveSessionIds.has(record?.reading?.session_id)) put(record);
    }
    for (const session of sessionList) {
      const rows = session.longitudinal_records || session.records || session.readings || [];
      const list = Array.isArray(rows) ? rows : Object.values(rows);
      for (const row of list) put(row?.longitudinal_record || row?.record || row?.cached_record || row);
      for (const attempt of session.attempts || []) {
        put(attempt?.longitudinal_record || attempt?.record || attempt?.cached_record);
      }
    }
    return [...records.values()].sort((a, b) =>
      lexicalCompare(a.reading.recorded_at || "", b.reading.recorded_at || "") ||
      lexicalCompare(a.reading.reading_id, b.reading.reading_id));
  }
  function adviceFromSessions(sessions) {
    const out = [];
    for (const session of sessions || []) {
      const coaching = session.coaching || session.coaching_snapshot;
      if (!coaching?.actions?.length) continue;
      for (const action of coaching.actions) {
        const link = linkTarget(action.target || {});
        out.push({
          issued_at: coaching.generated_at || null, session_id: session.id,
          engine: coaching.pool?.engine || null, target_id: link.m9_target_id,
          patterns: link.patterns, plan_position: action.rank_in_plan,
          retest_sentence_keys: (action.practice?.retest_sentences || []).map((sentence) =>
            sentence.sentence_key || null),
        });
      }
    }
    return out;
  }
  async function fillAdviceSentenceKeys(advice, sessions) {
    const rows = [];
    for (const session of sessions || []) {
      const coaching = session.coaching || session.coaching_snapshot;
      if (!coaching?.actions?.length) continue;
      for (const action of coaching.actions) {
        const keys = await Promise.all((action.practice?.retest_sentences || []).map(async (sentence) =>
          sentence.sentence_key || sentenceTextKey(sentence.text)));
        rows.push({ session_id: session.id, target_id: (action.target || {}).target_id, keys });
      }
    }
    const byAction = new Map(rows.map((row) => [`${row.session_id}\0${row.target_id}`, row.keys]));
    return advice.map((item) => {
      const keys = byAction.get(`${item.session_id}\0${item.target_id}`);
      return keys ? { ...item, retest_sentence_keys: keys } : item;
    });
  }
  async function sentenceTextKey(text) {
    const normalized = textKey(text);
    return digestString(normalized, 16);
  }
  function addTextGroups(records, articleTexts, cal) {
    const groups = articleTextGroups(articleTexts, cal);
    return records.map((record) => {
      const key = record.reading.article_key;
      return {
        ...record,
        reading: { ...record.reading, text_id: groups[key] || record.reading.text_id || key },
      };
    });
  }
  function stableStringify(value) {
    if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
    if (value && typeof value === "object") {
      return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
    }
    return JSON.stringify(value);
  }
  const REFERENCE_VARIANTS = new Set([
    "i|iː", "u|uː", "ɪ|ᵻ", "ɐ|ə", "æ|ɐ", "ɐ|ʌ", "ə|ʌ", "ɚ|ɜː", "ɚ|ɜ", "ɔ|ʌ",
    "ɑ|ɔ", "ɑː|ɔ", "ɑ|ɑː", "ʊ|ʊɹ", "uː|ʊɹ", "oːɹ|ɔːɹ", "l|əl", "t|ɾ", "d|ɾ",
    "ə|ɚ", "ɔ|ɔː",
  ]);
  const FUNCTION_WORDS = new Set([
    "a", "an", "the", "to", "of", "and", "in", "on", "at", "for", "from", "with", "by", "as", "into",
    "over", "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them", "my", "your",
    "his", "its", "our", "their", "this", "that", "these", "those", "is", "are", "was", "were", "be",
    "been", "am", "has", "have", "had", "do", "does", "did", "will", "would", "can", "could", "should",
    "shall", "may", "might", "must", "not", "no", "so", "but", "or", "if", "than", "then", "there",
    "what", "which", "who", "when", "where", "although", "before", "during", "still",
  ]);
  const VOWEL_CHARS = new Set("aeiouæɐɑɒɔəɚɛɜɝɪʊʌᵻɨʉøœɵyɯɤ");
  const EXTRA_PLAUSIBLE = new Set(["ɚ|ɹ", "ɝ|ɹ", "l|əl"]);
  const OBSERVATION_OUTCOME = {
    expected: "as_expected", substitution_candidate: "heard_other", ambiguous: "heard_other",
    omission_candidate: "not_detected", weak_evidence: "not_detected", insertion: "extra",
    not_interpreted: "uninterpreted",
  };
  const FLUENCY_GROUP = {
    PAUSE: "hesitation", FILLER: "filler", REPETITION: "repetition", RESTART: "repetition",
    FALSE_START: "repetition",
  };
  function lexicalKey(word) {
    return String(word || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}_']/gu, "");
  }
  function phoneIsVowel(phone) { return Boolean(phone) && [...phone].some((char) => VOWEL_CHARS.has(char)); }
  function phonePairKey(a, b) { return [a, b].sort().join("|"); }
  function referenceVariant(expected, heard) {
    return Boolean(expected && heard && REFERENCE_VARIANTS.has(phonePairKey(expected, heard)));
  }
  function plausiblePair(expected, heard) {
    if (!expected || !heard) return false;
    return phoneIsVowel(expected) === phoneIsVowel(heard) || EXTRA_PLAUSIBLE.has(phonePairKey(expected, heard));
  }
  function articleFragments(text) {
    const out = new Set();
    const regex = /([\p{L}\p{N}_]+)-\s+([\p{L}\p{N}_]+)/gu;
    let match;
    while ((match = regex.exec(String(text || "").normalize("NFKC")))) {
      out.add(match[1].toLowerCase());
      out.add(match[2].toLowerCase());
    }
    return out;
  }
  function normalizedSoundRows(reading, observations, candidates, fragments) {
    const byObservation = new Map((candidates || []).map((candidate) => {
      const interpretation = candidate.interpretation || {};
      return [candidate.observation_id, {
        category: interpretation.category, strength: candidate.evidence_strength,
        contexts_present: interpretation.contexts_present || [],
        natural_possible: Boolean(interpretation.natural_connected_speech_possible),
        merge_suspect: Boolean(candidate.merge_suspect),
      }];
    }));
    return observations.map((observation) => {
      const outcome = OBSERVATION_OUTCOME[observation.type] || "uninterpreted";
      let heard = outcome === "heard_other" ? observation.competitor : outcome === "extra" ? observation.observed : null;
      if (outcome === "heard_other" && !heard) heard = observation.observed;
      const word = observation.word || "";
      const lexical = lexicalKey(word);
      const context = observation.context || {};
      const m5 = byObservation.get(observation.id);
      const expected = observation.kind === "sound" ? observation.expected : null;
      let quality = "excluded", exclusion = null;
      if (outcome === "uninterpreted") exclusion = "not_interpreted";
      else if (outcome === "extra") exclusion = "extra_sound";
      else if (FUNCTION_WORDS.has(lexical) && phoneIsVowel(expected)) exclusion = "function_word_variant";
      else if (outcome === "as_expected") quality = "counter";
      else if (outcome === "not_detected") {
        if (m5?.merge_suspect) exclusion = "merge_suspect";
        else if (m5?.natural_possible) exclusion = "context_predicted";
        else quality = "weak";
      } else if (referenceVariant(expected, heard)) exclusion = "reference_variant";
      else if (!plausiblePair(expected, heard)) exclusion = "implausible_pair";
      else if ([context.previous_phone, context.next_phone].includes(heard)) exclusion = "neighbour_shift";
      else if (m5?.merge_suspect) exclusion = "merge_suspect";
      else if (m5?.natural_possible || m5?.category === "possible_coarticulation") exclusion = "context_predicted";
      else if (observation.type === "substitution_candidate" &&
        ["high", "moderate"].includes(observation.confidence)) {
        quality = reading.analysis_state === "low_confidence" ? "supporting" : "confident";
      } else quality = "supporting";
      const stress = context.stress_known ? (context.stress || "unstressed") : null;
      const soundIndex = observation.sound_index;
      return {
        o: `${reading.session_id}:${reading.attempt_id}:${reading.job_id}:${observation.id}`,
        obs: observation.id, e: expected, h: heard, out: outcome, q: quality, x: exclusion,
        c: observation.confidence, w: lexical, wd: word, wi: observation.word_index ?? -1,
        si: soundIndex === undefined ? null : soundIndex,
        frag: lexical.length < 3 || !/^[\p{L}]+$/u.test(lexical.replace(/'/gu, "")) || fragments.has(lexical),
        pos: context.word_position ?? null, st: stress, cl: context.in_consonant_cluster ?? null,
        sp: context.sentence_position ?? null, ag: "not_compared",
        play: observation.play_ms ?? null, span: observation.span_ms ?? null,
      };
    }).filter((row) => row.e !== null && row.e !== undefined || row.out === "extra");
  }
  function normalizedFluency(reading, fluencyData) {
    const data = fluencyData || {};
    if (data.state !== "ok") return [];
    const reliable = Boolean((data.metrics || {}).activity_reliable);
    const out = [];
    for (const observation of data.observations || []) {
      const group = FLUENCY_GROUP[observation.type];
      if (!group || !observation.notice) continue;
      const context = observation.context || {};
      if (observation.type === "PAUSE" &&
        (!["possible_hesitation", "unusually_long"].includes(observation.classification) ||
          String(context.position || "").includes("boundary"))) continue;
      const playback = observation.playback || {};
      out.push({
        o: `${reading.session_id}:${reading.attempt_id}:${reading.job_id}:${observation.id}`,
        g: group, q: reliable ? "confident" : "excluded", play: playback.play_ms ?? null,
        before: context.word_before ?? null, after: context.word_after ?? null,
      });
    }
    return out;
  }
  function summaryCode(attempt, primary, engine) {
    const state = attempt.state, disposition = attempt.user_disposition;
    let identity;
    if (state === "TOO_SHORT") identity = "TOO_SHORT";
    else if (["ANALYSIS_FAILED", "REJECTED", "INTERRUPTED"].includes(state)) identity = "FAILED";
    else if (state !== "ANALYZED" || !primary || primary.state !== "SUCCEEDED") {
      identity = ["CAPTURING", "RECORDED", "QUEUED", "ANALYZING"].includes(state) ? "PENDING" : "FAILED";
    } else identity = primary.target_confirmation?.state || "NOT_APPLICABLE";
    const boundary = primary?.boundary || {};
    const withheld = Boolean(boundary.feedback_withheld);
    const cause = withheld ? (boundary.withheld_reason || "boundary") : null;
    const feedback = ["TOO_SHORT", "FAILED", "PENDING", "NOT_APPLICABLE", "NOT_CHECKED"].includes(identity)
      ? "none" : withheld ? (cause === "containment" ? "withheld_containment" : "withheld_boundary")
        : identity === "MISMATCH" ? "hidden_identity" : "shown";
    if (disposition === "discarded") return "discarded";
    if (disposition === "rerecord_requested") return "rerecord";
    if (feedback === "none") return "not_analysed";
    if (engine !== null && engine !== undefined && primary?.engine_id !== engine) return "other_engine";
    if (identity === "MISMATCH") return "different_sentence";
    if (withheld) return cause === "containment" ? "containment" : "boundary";
    if (["MATCH", "LIKELY_MATCH"].includes(identity) || disposition === "kept") return null;
    return "unconfirmed";
  }
  function ineligibleReason(code) {
    return ({
      discarded: "discarded", rerecord: "rerecorded", not_analysed: "not_analysed",
      other_engine: "other_engine", different_sentence: "mismatch", boundary: "boundary_withheld",
      containment: "containment_withheld", unconfirmed: "unconfirmed_identity",
    })[code] || code;
  }
  function targetIdentity(primary) { return primary?.target_confirmation?.state || "NOT_APPLICABLE"; }
  function sentenceReference(attempt, primary, view) {
    const regions = view?.boundary?.regions || [];
    const target = regions.find((region) => region.kind === "target" && region.play);
    if (target) return { ...target.play };
    const duration = view?.duration_ms || attempt.audio?.duration_ms || 0;
    const ref = {
      session_id: attempt.session_id, segment_id: attempt.segment_id, attempt_id: attempt.id,
      job_id: primary?.id || null, timeline: "analysis_wav", kind: "sentence",
      play_ms: [0, duration], span_ms: [0, duration],
      url: `/api/sessions/${attempt.session_id}/attempts/${attempt.id}/audio`,
    };
    return ref;
  }
  async function extractBrowserAttempt(store, session, attempt, article, practiceSessionIds) {
    const jobs = [];
    for (const jobId of attempt.job_ids || []) {
      try { jobs.push(await store.loadJob(session.id, attempt.id, jobId)); } catch { /* missing jobs are ignored */ }
    }
    const primary = jobs.slice().reverse().find((job) => job.kind === "primary") || null;
    const baseReading = {
      reading_id: `${session.id}:${attempt.id}`, session_id: session.id, attempt_id: attempt.id,
      job_id: primary?.id || null, segment_id: attempt.segment_id || null,
      engine: primary?.engine_id || session.engine_default || null,
      recorded_at: attempt.created_at || null,
      sentence_key: await sentenceTextKey(attempt.target_text || ""),
      sentence_text: attempt.target_text || "", article_id: session.article_id || null,
      article_key: article?.text_sha256 || session.article_id || null,
      practice_session: practiceSessionIds.has(session.id) || article?.source === PRACTICE_SOURCE,
    };
    const code = summaryCode(attempt, primary, session.engine_default);
    if (code !== null) return {
      reading: baseReading, eligible: false, reason: ineligibleReason(code),
      detail: targetIdentity(primary), sounds: [], fluency: [],
    };
    if (!primary) return {
      reading: baseReading, eligible: false, reason: "not_analysed",
      detail: targetIdentity(primary), sounds: [], fluency: [],
    };
    let view;
    try { view = await store.loadView(primary); } catch { view = null; }
    if (!view || view.state === "boundary_withheld" || !(view.coach?.observations || []).length) {
      return { reading: baseReading, eligible: false, reason: "no_view",
        detail: targetIdentity(primary), sounds: [], fluency: [] };
    }
    const boundary = primary.boundary || {};
    const analysisState = boundary.feedback_withheld ? null : boundary.analysis?.state;
    const reading = {
      ...baseReading, job_id: primary.id, engine: primary.engine_id,
      analysis_state: ["ok", "low_confidence"].includes(analysisState) ? analysisState : "unknown",
      fluency_reliable: view.fluency?.metrics?.activity_reliable ?? null,
      coach_version: view.coach?.version || null, sentence_ref: sentenceReference(attempt, primary, view),
    };
    const candidates = view.reduction?.state === "ok" ? view.reduction.candidates || [] : [];
    return {
      reading, eligible: true, reason: null, detail: targetIdentity(primary),
      sounds: normalizedSoundRows(reading, view.coach.observations, candidates, articleFragments(article?.text)),
      fluency: normalizedFluency(reading, view.fluency),
    };
  }
  async function collectFromBrowserStore(store, options = {}) {
    const longitudinalStore = options.progressStore || store.progressStore?.();
    const [sessionIds, storedPractice, storedCache] = await Promise.all([
      store.sessionIds(), options.practice || options.practiceRecords ||
        longitudinalStore?.practiceRecords?.() || Promise.resolve([]),
      options.cache ? Promise.resolve(options.cache) : longitudinalStore?.loadCache?.() || Promise.resolve({}),
    ]);
    const practiceRecords = storedPractice;
    const practiceSessionIds = new Set(practiceRecords.map((record) => record.practice_session_id));
    const cache = options.rebuild || storedCache?.extractor_version !== EXTRACTOR_VERSION ? {} : storedCache || {};
    const readings = {}, records = [], articleTexts = {};
    const previous = new Set(Object.keys(cache.readings || {}));
    const extracted = [];
    for (const sessionId of sessionIds) {
      const session = await store.loadSession(sessionId);
      let article = null;
      try { article = await store.loadArticle(session.article_id); } catch { /* missing article is retained as unknown */ }
      if (article) articleTexts[article.text_sha256 || article.id] = article.text || "";
      const storedAttempts = await store.attempts(sessionId);
      const attemptsById = new Map(storedAttempts.map((attempt) => [attempt.id, attempt]));
      const attempts = (session.attempt_ids || []).map((attemptId) => attemptsById.get(attemptId)).filter(Boolean);
      for (const attempt of attempts) {
        const jobs = [];
        for (const jobId of attempt.job_ids || []) {
          try { jobs.push(await store.loadJob(sessionId, attempt.id, jobId)); } catch { /* removed job */ }
        }
        const fingerprint = await digestFingerprint({ session, article, attempt, jobs, practice: practiceSessionIds.has(sessionId) });
        const readingId = `${sessionId}:${attempt.id}`;
        const cached = cache.readings?.[readingId];
        let record;
        if (!options.rebuild && cached?.fingerprint === fingerprint && cached.record) record = cached.record;
        else {
          const attemptStore = {
            loadJob: (sid, aid, jid) => store.loadJob(sid, aid, jid),
            loadView: (job) => store.loadView(job),
          };
          record = await extractBrowserAttempt(attemptStore, session, attempt, article, practiceSessionIds);
          extracted.push(readingId);
        }
        readings[readingId] = { fingerprint, record };
        records.push({ ...record, fingerprint });
      }
    }
    const groups = articleTextGroups(articleTexts, { ...DEFAULT_CALIBRATION, ...(options.calibration || {}) });
    const groupedRecords = records.map((record) => ({
      ...record,
      reading: {
        ...record.reading,
        text_id: groups[record.reading.article_key] || record.reading.article_key,
      },
    }));
    const advice = [];
    for (const sessionId of sessionIds) {
      const coaching = await store.loadCoaching(sessionId);
      if (!coaching?.actions?.length) continue;
      for (const action of coaching.actions) {
        const link = linkTarget(action.target || {});
        const keys = await Promise.all((action.practice?.retest_sentences || []).map((sentence) =>
          sentenceTextKey(sentence.text || "")));
        advice.push({
          issued_at: coaching.generated_at || null, session_id: sessionId,
          engine: coaching.pool?.engine || null, target_id: link.m9_target_id, patterns: link.patterns,
          plan_position: action.rank_in_plan, retest_sentence_keys: keys,
        });
      }
    }
    return {
      records: groupedRecords, practice: practiceRecords, advice,
      cache: { extractor_version: EXTRACTOR_VERSION, readings },
      extracted, previous,
      ledger: options.ledger || await longitudinalStore?.ledger?.() || [],
      transitions: options.transitions || await longitudinalStore?.transitionsLog?.() || [],
      sessions: sessionIds.map((id) => ({ id })),
    };
  }
  async function digestFingerprint(value) {
    return digestString(stableStringify(value), 24);
  }
  async function digestString(value, length) {
    const serialized = String(value);
    if (globalThis.crypto?.subtle && typeof TextEncoder !== "undefined") {
      const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(serialized));
      return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, length);
    }
    if (typeof require === "function") {
      return require("node:crypto").createHash("sha256").update(serialized, "utf8").digest("hex").slice(0, length);
    }
    let hash = 2166136261;
    for (let i = 0; i < serialized.length; i += 1) hash = Math.imul(hash ^ serialized.charCodeAt(i), 16777619);
    return (hash >>> 0).toString(16).padStart(8, "0");
  }
  async function browserInputFingerprint(readings, practiceRecords, advice) {
    const entries = Object.keys(readings).sort(lexicalCompare)
      .map((id) => `${id}=${readings[id].fingerprint ?? "None"};`);
    for (const record of [...practiceRecords].sort((a, b) =>
      lexicalCompare(a.created_at, b.created_at) || lexicalCompare(a.id, b.id))) {
      entries.push(`practice=${record.id};`);
    }
    for (const item of advice) {
      const py = (value) => value === null || value === undefined ? "None" : String(value);
      entries.push(`advice=${py(item.issued_at)}:${py(item.session_id)}:${py(item.target_id)};`);
    }
    return digestString(entries.join(""), 24);
  }

  /**
   * Recompute progress from browser-local longitudinal evidence. `cache.readings` values are
   * `{fingerprint, record}` entries in the same compact, source-referenced shape as M10's
   * extractor output. Session `records`/`readings` may supply new extracted records before
   * they are added to that cache. This module never reads or writes a server store.
   */
  async function update(input = {}) {
    if (input.store) {
      const local = await collectFromBrowserStore(input.store, input);
      const result = await update({
        ...input, ...local, store: null,
        sessions: local.sessions.map((session) => ({
          ...session, records: local.records.filter((record) => record.reading.session_id === session.id),
        })),
        practice: local.practice, advice: local.advice, cache: local.cache, forcedExtracted: local.extracted,
      });
      result.extracted = local.extracted;
      if (input.persist !== false) {
        const longitudinalStore = input.progressStore || input.store.progressStore?.();
        if (longitudinalStore?.saveCache) await longitudinalStore.saveCache(result.cache);
        const newLedger = result.ledger.slice(local.ledger.length);
        const newTransitions = result.transitions.slice(local.transitions.length);
        if (longitudinalStore?.appendLedger) await longitudinalStore.appendLedger(newLedger);
        if (longitudinalStore?.appendTransitions) await longitudinalStore.appendTransitions(newTransitions);
      }
      return result;
    }
    const sessions = input.sessions || [];
    const suppliedCache = input.cache || {};
    const cache = suppliedCache.extractor_version && suppliedCache.extractor_version !== EXTRACTOR_VERSION
      ? {} : suppliedCache;
    const practiceRecords = input.practice || input.practiceRecords || [];
    let advice = input.advice || adviceFromSessions(sessions);
    if (!input.advice) advice = await fillAdviceSentenceKeys(advice, sessions);
    const records = recordsFromBrowserData(sessions, input.rebuild ? {} : cache);
    const articleTexts = { ...(input.article_texts || {}) };
    for (const article of input.articles || []) {
      const articleKey = article.text_sha256 || article.id;
      if (articleKey) articleTexts[articleKey] = article.text || "";
    }
    for (const session of sessions) {
      const article = session.article;
      if (!article) continue;
      const articleKey = article.text_sha256 || article.id || session.article_id;
      if (articleKey) articleTexts[articleKey] = article.text || "";
    }
    const cal = { ...DEFAULT_CALIBRATION, ...(input.calibration || {}) };
    const groupedRecords = addTextGroups(records, articleTexts, cal);
    const readingMap = { ...(input.rebuild ? {} : cache.readings || {}) };
    let extracted = [...(input.forcedExtracted || [])];
    const extractedSet = new Set(extracted);
    for (const record of groupedRecords) {
      const readingId = record.reading.reading_id;
      const previous = readingMap[readingId];
      const recordUnchanged = previous?.record && stableStringify(previous.record) === stableStringify(record);
      const sameFingerprint = record.fingerprint && previous?.fingerprint === record.fingerprint;
      if (input.rebuild || !previous || (!recordUnchanged && !sameFingerprint)) {
        const { fingerprint, ...cachedRecord } = record;
        readingMap[readingId] = { fingerprint: fingerprint || null, record: cachedRecord };
        if (!extractedSet.has(readingId)) {
          extracted.push(readingId);
          extractedSet.add(readingId);
        }
      } else if (!readingMap[readingId].record ||
          (record.fingerprint && readingMap[readingId].fingerprint !== record.fingerprint)) {
        const { fingerprint, ...cachedRecord } = record;
        readingMap[readingId] = { fingerprint: fingerprint || null, record: cachedRecord };
        if (!extractedSet.has(readingId)) {
          extracted.push(readingId);
          extractedSet.add(readingId);
        }
      }
    }
    const currentReadingIds = new Set(groupedRecords.map((record) => record.reading.reading_id));
    for (const readingId of Object.keys(readingMap)) {
      if (!currentReadingIds.has(readingId)) delete readingMap[readingId];
    }
    const normalizedCache = { extractor_version: EXTRACTOR_VERSION, readings: readingMap };
    const inputFingerprint = input.input_fingerprint ||
      await browserInputFingerprint(readingMap, practiceRecords, advice);
    const progress = buildProgress(groupedRecords, practiceRecords, advice, {
      engine: input.engine || null, calibration: cal, generated_at: input.generated_at,
      input_fingerprint: inputFingerprint,
    });
    progress.update = { mode: input.rebuild ? "rebuild" : "incremental", extracted: extracted.length, reused: records.length - extracted.length };
    const published = new Set((input.transitions || []).map((item) => [
      item.versions, item.engine, item.pattern, item.at_session, item.to,
    ].join("\0")));
    const versions = `${PROGRESS_VERSION}/${STATES_VERSION}/${progress.calibration.version}`;
    const newTransitions = [];
    for (const item of progress.patterns) {
      for (const transition of item.transitions) {
        const key = [versions, progress.engine, item.pattern, transition.at_session, transition.to].join("\0");
        if (published.has(key)) continue;
        newTransitions.push({
          versions, engine: progress.engine, pattern: item.pattern, at_session: transition.at_session,
          time: transition.time, from: transition.from, to: transition.to, reason: transition.reason,
          published_at: input.generated_at || new Date().toISOString(),
        });
        published.add(key);
      }
    }
    const ledger = [...(input.ledger || [])];
    for (const readingId of extracted) {
      ledger.push({
        at: input.generated_at || new Date().toISOString(), reading_id: readingId,
        fingerprint: readingMap[readingId].fingerprint || null, extractor_version: EXTRACTOR_VERSION,
        mode: input.rebuild ? "rebuild" : "incremental",
      });
    }
    return {
      progress, coaching_adaptation: adaptCoaching(input.coaching || null, progress),
      cache: normalizedCache, ledger, transitions: [...(input.transitions || []), ...newTransitions],
      extracted,
    };
  }

  return {
    PROGRESS_VERSION, IDENTITY_VERSION, EXTRACTOR_VERSION, STATES_VERSION, DEFAULT_CALIBRATION,
    broadId, parsePattern, reverseOf, contrastGroup, linkTarget, materialWords, buildProgress,
    createPracticeRecord, adaptCoaching, recordsFromBrowserData, update,
  };
});
