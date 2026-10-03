// Unit tests for the pure helpers in app.js (run with node; no browser).
"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const h = require(path.join(__dirname, "../../../src/pronunciation_lab/app/static/app.js"));

let n = 0;
function test(name, fn) { fn(); n += 1; }

test("formatSeconds", () => {
  assert.equal(h.formatSeconds(0), "0.00 s");
  assert.equal(h.formatSeconds(3320), "3.32 s");
});

test("formatSpan marks estimates", () => {
  assert.equal(h.formatSpan([3320, 3340], false), "3.32 s–3.34 s");
  assert.equal(h.formatSpan([4820, 5100], true), "4.82 s–5.10 s (estimated)");
});

test("formatProbability", () => {
  assert.equal(h.formatProbability(null), "–");
  assert.equal(h.formatProbability(undefined), "–");
  assert.equal(h.formatProbability(0.004), "<1%");
  assert.equal(h.formatProbability(0.1), "10%");
  assert.equal(h.formatProbability(0.836), "84%");
});

test("playbackArgs plays exactly the window, in seconds", () => {
  assert.deepEqual(h.playbackArgs([3170, 3470], 7.85), { offset: 3.17, duration: 0.30000000000000027 });
  const a = h.playbackArgs([3170, 3470], 7.85);
  assert.ok(Math.abs(a.offset - 3.17) < 1e-9 && Math.abs(a.duration - 0.3) < 1e-9);
});

test("playbackArgs clamps to the buffer", () => {
  const a = h.playbackArgs([-100, 500], 7.85);
  assert.equal(a.offset, 0);
  assert.ok(Math.abs(a.duration - 0.5) < 1e-9);
  const b = h.playbackArgs([7700, 8000], 7.85);
  assert.ok(Math.abs(b.offset - 7.7) < 1e-9 && Math.abs(b.duration - 0.15) < 1e-9);
  const c = h.playbackArgs([9000, 9100], 7.85);
  assert.equal(c.duration, 0);
});

test("analyzeUrl encodes text and engine", () => {
  assert.equal(h.analyzeUrl("Think & say?", "wav2vec2_raw"), "/api/analyze?text=Think+%26+say%3F&engine=wav2vec2_raw");
  assert.equal(h.analyzeUrl("a", ""), "/api/analyze?text=a");
});

test("recordingFilename follows the recorder's MIME type", () => {
  assert.equal(h.recordingFilename("audio/webm;codecs=opus"), "recording.webm");
  assert.equal(h.recordingFilename("audio/mp4"), "recording.m4a");
  assert.equal(h.recordingFilename("audio/ogg"), "recording.ogg");
  assert.equal(h.recordingFilename("audio/wav"), "recording.wav");
  assert.equal(h.recordingFilename(""), "recording.bin");
});

test("engineOptionLabel shows why an engine cannot be used", () => {
  assert.equal(h.engineOptionLabel({ label: "WavLM", state: "unresolved", reason: "unresolved_engine" }),
    "WavLM — unresolved (unresolved engine)");
  assert.equal(h.engineOptionLabel({ label: "Raw", state: "runnable", reason: null }), "Raw");
});

test("summaryChips lists only present categories, in a fixed order", () => {
  assert.deepEqual(h.summaryChips({ unclear: 3, expected: 26, different: 0, not_detected: 3 }).map((c) => c.category),
    ["expected", "unclear", "not_detected"]);
  assert.deepEqual(h.summaryChips({}), []);
});

test("extraSoundsText", () => {
  assert.equal(h.extraSoundsText([]), "");
  assert.equal(h.extraSoundsText(["ɪ"]), "Extra sound heard right after: /ɪ/");
  assert.equal(h.extraSoundsText(["ə", "ɪ"]), "Extra sounds heard right after: /ə/ /ɪ/");
});

test("category titles never judge", () => {
  for (const t of Object.values(h.CATEGORY_TITLES).concat(Object.values(h.WORD_TITLES))) {
    assert.ok(!/wrong|incorrect|mistake|error|score/i.test(t), t);
  }
});

test("wordTitle says 'all' only when every sound shares the status", () => {
  const nd = { category: "not_detected" }, ex = { category: "expected" }, un = { category: "unclear" };
  assert.equal(h.wordTitle({ status: "not_detected", sounds: [nd, nd, nd] }), "not detected");
  assert.equal(h.wordTitle({ status: "not_detected", sounds: [ex, nd] }), "some sounds not detected");
  assert.equal(h.wordTitle({ status: "expected", sounds: [ex, ex] }), "all sounds heard as expected");
  assert.equal(h.wordTitle({ status: "unclear", sounds: [un] }), "all sounds unclear");
  assert.equal(h.wordTitle({ status: "unclear", sounds: [] }), "some sounds unclear");
  for (const t of Object.values(h.ALL_WORD_TITLES)) assert.ok(!/wrong|incorrect|mistake|error|score/i.test(t));
});

// --- Full Recording Feedback (copied text) -------------------------------------
function sampleView() {
  const sound = (index, expected, heard, category, text, extra) => ({
    index, expected, expected_hint: expected === "θ" ? "th as in think" : null, heard,
    heard_hint: null, category, text, confidence: heard ? 0.8 : null, expected_probability: 0.84,
    alternatives: [{ phone: heard || "z", probability: 0.8 }, { phone: "t", probability: 0.07 }],
    extra_sounds_after: extra || [], span_ms: [1000 + index * 20, 1020 + index * 20],
    timing_estimated: heard === null, play_ms: [900, 1200],
  });
  return {
    state: "ok", target_text: "Think that | here.", source: "benchmark R01 (Slow, very clear)",
    duration_ms: 7850.7, engine: { id: "wav2vec2_raw", model: "facebook/m@abc", phone_set: "ipa-espeak-raw" },
    summary: { expected: 1, unclear: 1, not_detected: 1 }, heard_sequence: ["θ", "ɪ", "i"],
    extra_sounds_before_first_word: [], warnings: [],
    caveats: ["This shows what one speech-recognition model heard."],
    words: [
      { index: 0, word: "think", status: "unclear", span_ms: [1000, 1360], timing_estimated: false, flagged_by_engine: null,
        sounds: [sound(0, "θ", "θ", "expected", "Heard as expected: /θ/", ["ɪ"]), sound(1, "iː", "i", "unclear", "Unclear: closest to /i/")] },
      { index: 1, word: "that", status: "not_detected", span_ms: [4820, 5100], timing_estimated: true, flagged_by_engine: true,
        sounds: [sound(2, "ð", null, "not_detected", "Not detected: no clear /ð/ was found here; location estimated")] },
    ],
  };
}

test("fullFeedbackText covers the whole recording, every word and every sound", () => {
  const text = h.fullFeedbackText(sampleView());
  assert.ok(text.startsWith("# Pronunciation feedback — full recording"));
  assert.ok(text.includes("- Target text: Think that | here."));
  assert.ok(text.includes("- Recording: benchmark R01 (Slow, very clear)"));
  assert.ok(text.includes("- Engine: wav2vec2_raw (facebook/m@abc), phone set ipa-espeak-raw"));
  assert.ok(text.includes("- Words: 2") && text.includes("- Heard as expected: 1") && text.includes("- Not detected: 1"));
  assert.ok(text.includes("Everything the recogniser heard: /θ ɪ i/"));
  assert.ok(text.includes("### 1. think — some sounds unclear"));
  assert.ok(text.includes("### 2. that — not detected"));
  assert.ok(text.includes("Word located at 4.82 s–5.10 s (estimated) · flagged by OpenPronounce"));
  const rows = text.split("\n").filter((l) => /^\| \d+ \|/.test(l));
  assert.equal(rows.length, 3);
  assert.ok(rows[0].includes("/θ/ (th as in think)") && rows[0].includes("Extra sound heard right after: /ɪ/"));
  assert.ok(rows[0].includes("| 84% |") && rows[0].includes("| 80% |") && rows[0].includes("1.00 s–1.02 s"));
  assert.ok(rows[1].includes("| Unclear: closest to /i/ |"));
  for (const r of rows) assert.ok(!/(Heard as expected|Unclear|Not detected|Not interpreted): \1/.test(r), "category repeated: " + r);
  assert.ok(rows[2].includes("| — |") && rows[2].includes("(estimated)") && rows[2].includes("| – |"));
  assert.ok(text.includes("## How to read this") && text.includes("- This shows what one speech-recognition model heard."));
});

test("fullFeedbackText keeps the Markdown table intact", () => {
  const v = sampleView();
  v.words[0].sounds[1].text = "a | b";
  const row = h.fullFeedbackText(v).split("\n").find((l) => l.startsWith("| 2 |"));
  assert.ok(row.includes("a \\| b"));
  assert.equal(row.split(/(?<!\\)\|/).length - 2, 8);  // exactly 8 cells
});

test("fullFeedbackText for no speech and failed analyses", () => {
  const ns = h.fullFeedbackText({ state: "no_speech", target_text: "Hi.", message: "No speech sounds were detected in this recording.", caveats: [] });
  assert.ok(ns.includes("## Result") && ns.includes("No speech sounds were detected") && !ns.includes("## Word by word"));
  const f = h.fullFeedbackText({ state: "failed", target_text: "Hi.", error: { message: "The recording is too short to analyse.", detail: "0.1 s" }, caveats: [] });
  assert.ok(f.includes("The recording is too short to analyse. (0.1 s)") && !f.includes("## Summary"));
});

test("fullFeedbackText never scores or ranks", () => {
  assert.ok(!/score|grade|rank|wrong|incorrect/i.test(h.fullFeedbackText(sampleView())));
});

// --- M4 Phoneme Coach helpers ------------------------------------------------------
test("coachPatternTitle per pattern kind", () => {
  assert.equal(h.coachPatternTitle({ kind: "contrast", expected: "w", contrast: "v" }), "/w/ ↔ /v/");
  assert.equal(h.coachPatternTitle({ kind: "detection", expected: "s", contrast: null }), "/s/ — not clearly detected");
  assert.equal(h.coachPatternTitle({ kind: "insertion", expected: null, contrast: "ɪ" }), "Extra /ɪ/");
});

test("patternClassLabel never ranks and names the context", () => {
  assert.equal(h.patternClassLabel({ class: "one_off" }), "Single observation");
  assert.equal(h.patternClassLabel({ class: "consistent" }), "Recurring pattern in this recording");
  assert.equal(h.patternClassLabel({ class: "context_specific", context: "final" }), "Context-specific (word-final only)");
  for (const t of Object.values(h.PATTERN_CLASS_LABELS).concat(Object.values(h.OBSERVATION_LABELS))) {
    assert.ok(!/score|rank|worst|best|wrong|incorrect|mistake|error|fail/i.test(t), t);
  }
});

test("formatMeasure: null is unavailable, zero is a real zero", () => {
  assert.equal(h.formatMeasure(null), "unavailable");
  assert.equal(h.formatMeasure(undefined), "unavailable");
  assert.equal(h.formatMeasure(0), "0.00");
  assert.equal(h.formatMeasure(118.4, "Hz", 0), "118 Hz");
});

test("contextText: unknown stress is never 'unstressed'", () => {
  const base = { word_position: "final", previous_phone: "t", next_phone: null, in_consonant_cluster: true };
  assert.equal(h.contextText({ ...base, stress: null, stress_known: false }),
    "word-final · between /t/ and /–/ · in a consonant cluster · stress unknown");
  assert.equal(h.contextText({ ...base, stress: "primary", stress_known: true, in_consonant_cluster: false }),
    "word-final · between /t/ and /–/ · primary stress (dictionary)");
  const known_none = h.contextText({ ...base, stress: null, stress_known: true });
  assert.ok(!/unstressed|stress unknown/.test(known_none));
});

// --- M5 ------------------------------------------------------------------------
const CANDIDATE = {
  expected: "t",
  raw_observation: { engine: "openpronounce", observed: null, expected_posterior: 0.007 },
  evidence: {
    temporal_slot: { slot_ms: 80, pause_adjacent: false, comparables: 1 },
    acoustic: { relative_energy: null, measured_over: "estimated region" },
  },
  interpretation: {
    streams: ["recognition", "temporal_slot"], reasons: ["not decoded"], label: "Ambiguous reduction",
    candidate_explanations: [{ label: "identical neighbouring sound" }],
  },
  evidence_strength: "ambiguous",
  where: { word: "want", word_position: "final", syllable_position: "coda", previous_phone: "n", next_phone: "t" },
};

test("slotText: context, never the sound's duration", () => {
  const t = h.slotText({ slot_ms: 80, pause_adjacent: true, comparables: 3 });
  assert.equal(t, "contextual slot 80 ms between the neighbouring decoded sounds (not this sound's duration); next to a pause; 3 other occurrences to compare");
  assert.ok(/unavailable/.test(h.slotText({ slot_ms: null })));
  assert.ok(!/lasted|duration of \d/.test(t));
});

test("reductionChain: observation → evidence → interpretation → strength, in order", () => {
  const lines = h.reductionChain(CANDIDATE);
  assert.equal(lines.length, 4);
  assert.ok(lines[0].startsWith("Raw engine observation (openpronounce): expected /t/, not decoded, P(/t/) <1%"));
  assert.ok(lines[1].startsWith("Evidence (recognition, temporal slot): not decoded; contextual slot 80 ms"));
  assert.ok(!/relative energy/.test(lines[1]));  // unavailable measurement is not shown as a value
  assert.equal(lines[2], "Candidate interpretation: Ambiguous reduction — possible explanation: identical neighbouring sound");
  assert.equal(lines[3], "Evidence strength: ambiguous evidence");
});

test("agreementText names the engines, never a winner", () => {
  assert.equal(h.agreementText("only_second", "wav2vec2_raw", "openpronounce"), "Listed only from openpronounce's evidence");
  assert.equal(h.agreementText("only_first", "wav2vec2_raw", "openpronounce"), "Listed only from wav2vec2_raw's evidence");
  assert.equal(h.agreementText("same_category", "a", "b"), "Both engines: same interpretation");
});

test("whereText and strength labels", () => {
  assert.equal(h.whereText(CANDIDATE), "'want' · word-final · coda (derived) · between /n/ and /t/");
  assert.deepEqual(Object.keys(h.STRENGTH_LABELS), ["moderate", "low", "ambiguous", "insufficient"]);
  for (const v of [...Object.values(h.STRENGTH_LABELS), ...Object.values(h.AGREEMENT_LABELS)]) {
    assert.ok(!/high|wrong|incorrect|score|best|worst/i.test(v), v);
  }
});

test("alignWords: the complete original sentence, words matched in order", () => {
  const words = ["think", "about", "the", "three", "things", "that", "you", "want", "to", "change"].map((word, i) => ({ word, index: i }));
  const text = "Think about the three things that you want to change.";
  const pieces = h.alignWords(text, words);
  assert.equal(pieces.map((p) => p.text).join(""), text);               // nothing dropped, order kept
  assert.deepEqual(pieces.filter((p) => p.wordIndex !== undefined).map((p) => p.wordIndex), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.equal(pieces.find((p) => p.wordIndex === 0).text, "Think");     // original capitals
  assert.equal(pieces.at(-1).text, ".");                                 // punctuation kept as text
});

test("alignWords: apostrophes, hyphens, unmatched words and look-ahead", () => {
  const text = "“Don't stop,” she said — a well-known 2024 line.";
  const words = ["don't", "stop", "she", "said", "a", "well-known", "line"].map((word, i) => ({ word, index: i }));
  const pieces = h.alignWords(text, words);
  assert.equal(pieces.map((p) => p.text).join(""), text);
  const matched = pieces.filter((p) => p.wordIndex !== undefined).map((p) => p.text);
  assert.deepEqual(matched, ["Don't", "stop", "she", "said", "a", "well-known", "line"]);
  assert.ok(pieces.some((p) => p.text === "2024" && p.wordIndex === undefined));   // shown, not clickable
  assert.deepEqual(h.alignWords("Hello there.", []).map((p) => p.text).join(""), "Hello there.");
});

console.log(`ok ${n} tests`);
