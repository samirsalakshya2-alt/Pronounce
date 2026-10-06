/* Pronunciation Lab UI.
 *
 * Pure helpers (top) are exported for node unit tests; the DOM wiring (bottom)
 * only runs in a browser. All evidence comes from the server's analysis view;
 * nothing here invents or re-derives results.
 */
"use strict";

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

const CATEGORY_TITLES = {
  expected: "Heard as expected",
  different: "Heard as a different sound",
  unclear: "Unclear",
  not_detected: "Not detected",
  not_interpreted: "Not interpreted",
};
const WORD_TITLES = {
  expected: "all sounds heard as expected",
  different: "some sounds heard differently",
  unclear: "some sounds unclear",
  not_detected: "some sounds not detected",
  not_interpreted: "not interpreted (unreliable alignment)",
};

const ALL_WORD_TITLES = {
  expected: "all sounds heard as expected",
  different: "all sounds heard differently",
  unclear: "all sounds unclear",
  not_detected: "not detected",
  not_interpreted: "not interpreted (unreliable alignment)",
};

/** "all …" when every sound shares the word's status, otherwise "some …". */
function wordTitle(word) {
  const sounds = word.sounds || [];
  const all = sounds.length > 0 && sounds.every((s) => s.category === word.status);
  return (all ? ALL_WORD_TITLES : WORD_TITLES)[word.status];
}

function formatSeconds(ms) {
  return (ms / 1000).toFixed(2) + " s";
}

function formatSpan(span, estimated) {
  const text = formatSeconds(span[0]) + "–" + formatSeconds(span[1]);
  return estimated ? text + " (estimated)" : text;
}

function formatProbability(p) {
  if (p === null || p === undefined) return "–";
  if (p < 0.01) return "<1%";
  return Math.round(p * 100) + "%";
}

/** Offset and duration (seconds) for AudioBufferSourceNode.start(0, offset, duration). */
function playbackArgs(playMs, bufferDurationS) {
  const start = Math.max(0, playMs[0] / 1000);
  const end = Math.min(bufferDurationS, playMs[1] / 1000);
  return { offset: Math.min(start, bufferDurationS), duration: Math.max(0, end - start) };
}

function analyzeUrl(text, engine) {
  const q = new URLSearchParams({ text: text });
  if (engine) q.set("engine", engine);
  return "/api/analyze?" + q.toString();
}

function recordingFilename(mimeType) {
  const t = (mimeType || "").toLowerCase();
  if (t.includes("webm")) return "recording.webm";
  if (t.includes("ogg")) return "recording.ogg";
  if (t.includes("mp4") || t.includes("aac") || t.includes("m4a")) return "recording.m4a";
  if (t.includes("wav")) return "recording.wav";
  return "recording.bin";
}

function engineOptionLabel(engine) {
  if (engine.state === "runnable") return engine.label;
  return engine.label + " — " + engine.state + (engine.reason ? " (" + engine.reason.replace(/_/g, " ") + ")" : "");
}

function extraSoundsText(phones) {
  if (!phones || !phones.length) return "";
  return "Extra sound" + (phones.length > 1 ? "s" : "") + " heard right after: " + phones.map((p) => "/" + p + "/").join(" ");
}

function localTime(iso) {
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString();
}

function summaryChips(summary) {
  return Object.keys(CATEGORY_TITLES)
    .filter((c) => (summary[c] || 0) > 0)
    .map((c) => ({ category: c, text: CATEGORY_TITLES[c] + ": " + summary[c] }));
}

function phoneWithHint(phone, hintText) {
  if (!phone) return "—";
  return "/" + phone + "/" + (hintText ? " (" + hintText + ")" : "");
}

function mdCell(text) {
  return String(text).replace(/\|/g, "\\|").replace(/\n/g, " ");
}

/** The complete feedback for one analysis as Markdown, for copying elsewhere. */
function fullFeedbackText(view) {
  const lines = ["# Pronunciation feedback — full recording", ""];
  lines.push("- Target text: " + view.target_text);
  if (view.source) lines.push("- Recording: " + view.source);
  if (view.duration_ms !== undefined && view.duration_ms !== null) {
    lines.push("- Length: " + formatSeconds(view.duration_ms) + " (all times refer to the analysed audio, 16 kHz mono)");
  }
  if (view.engine) {
    lines.push("- Engine: " + view.engine.id + (view.engine.model ? " (" + view.engine.model + ")" : "") +
      (view.engine.phone_set ? ", phone set " + view.engine.phone_set : ""));
  }
  lines.push("");

  if (view.state === "failed" || view.state === "unavailable") {
    lines.push("## Result", "", view.error.message + (view.error.detail ? " (" + view.error.detail + ")" : ""), "");
  } else if (view.state === "no_speech") {
    lines.push("## Result", "", view.message, "");
  } else {
    lines.push("## Summary", "");
    lines.push("- Words: " + (view.words || []).length);
    for (const chip of summaryChips(view.summary || {})) lines.push("- " + chip.text);
    if (view.state === "partial") lines.push("- Some evidence is incomplete: " + (view.warnings || []).join("; "));
    lines.push("");
    if (view.heard_sequence) lines.push("Everything the recogniser heard: /" + view.heard_sequence.join(" ") + "/", "");
    if ((view.extra_sounds_before_first_word || []).length) {
      lines.push("Extra sounds heard before the first word: " +
        view.extra_sounds_before_first_word.map((p) => "/" + p + "/").join(" "), "");
    }

    lines.push("## Word by word", "");
    for (const w of view.words || []) {
      lines.push("### " + (w.index + 1) + ". " + w.word + " — " + wordTitle(w));
      lines.push("Word located at " + formatSpan(w.span_ms, w.timing_estimated) +
        (w.flagged_by_engine ? " · flagged by OpenPronounce" : ""), "");
      lines.push("| # | Expected | Heard | What the recogniser found | Chance of expected | Confidence of heard | Alternatives | Where |");
      lines.push("|---|---|---|---|---|---|---|---|");
      for (const s of w.sounds) {
        // s.text already starts with the category wording ("Unclear: …", "Not detected: …").
        const finding = s.text +
          (extraSoundsText(s.extra_sounds_after) ? " · " + extraSoundsText(s.extra_sounds_after) : "");
        lines.push("| " + [
          s.index + 1,
          phoneWithHint(s.expected, s.expected_hint),
          phoneWithHint(s.heard, s.heard_hint),
          finding,
          formatProbability(s.expected_probability),
          formatProbability(s.confidence),
          s.alternatives.slice(0, 3).map((a) => "/" + a.phone + "/ " + formatProbability(a.probability)).join(", ") || "–",
          formatSpan(s.span_ms, s.timing_estimated),
        ].map(mdCell).join(" | ") + " |");
      }
      lines.push("");
    }
  }

  if ((view.caveats || []).length) {
    lines.push("## How to read this", "");
    for (const c of view.caveats) lines.push("- " + c);
    lines.push("");
  }
  return lines.join("\n");
}

// --- M4 Phoneme Coach: pure helpers --------------------------------------------
const COACH_OPEN_LIMIT = 4;
const OBSERVATION_LABELS = {
  expected: "Consistent with the expected sound",
  substitution_candidate: "Substitution candidate",
  omission_candidate: "Not detected (omission candidate)",
  insertion: "Extra sound",
  ambiguous: "Ambiguous",
  weak_evidence: "Weak evidence",
  not_interpreted: "Not interpreted",
};
const PATTERN_CLASS_LABELS = {
  one_off: "Single observation",
  repeated: "Repeated in this recording",
  consistent: "Recurring pattern in this recording",
  context_specific: "Context-specific",
};

function coachPatternTitle(p) {
  if (p.kind === "insertion") return "Extra /" + p.contrast + "/";
  if (p.kind === "detection") return "/" + p.expected + "/ — not clearly detected";
  return "/" + p.expected + "/ ↔ /" + p.contrast + "/";
}

function patternClassLabel(p) {
  const base = PATTERN_CLASS_LABELS[p.class] || p.class;
  return p.class === "context_specific" && p.context ? base + " (word-" + p.context + " only)" : base;
}

/** Acoustic value for display; null/undefined means not measured, never 0. */
function formatMeasure(value, unit, digits) {
  if (value === null || value === undefined) return "unavailable";
  return Number(value).toFixed(digits === undefined ? 2 : digits) + (unit ? " " + unit : "");
}

function contextText(ctx) {
  if (!ctx) return "";
  const parts = ["word-" + ctx.word_position];
  if (ctx.previous_phone || ctx.next_phone) {
    parts.push("between /" + (ctx.previous_phone || "–") + "/ and /" + (ctx.next_phone || "–") + "/");
  }
  if (ctx.in_consonant_cluster) parts.push("in a consonant cluster");
  if (ctx.stress_known && ctx.stress) parts.push(ctx.stress + " stress (dictionary)");
  else if (!ctx.stress_known) parts.push("stress unknown");
  return parts.join(" · ");
}

// --- M5 Reduction & Connected Speech: pure helpers --------------------------------
const REDUCTION_OPEN_LIMIT = 4;
const STRENGTH_LABELS = {
  moderate: "moderate evidence", low: "low evidence", ambiguous: "ambiguous evidence", insufficient: "insufficient evidence",
};
const AGREEMENT_LABELS = {
  same_category: "Both engines: same interpretation",
  different_category: "The engines' interpretations differ",
  only_first: "Listed only from {first}'s evidence",
  only_second: "Listed only from {second}'s evidence",
};

function agreementText(agreement, first, second) {
  return (AGREEMENT_LABELS[agreement] || agreement).replace("{first}", first).replace("{second}", second);
}

/** The contextual temporal slot, always worded as context, never as duration. */
function slotText(ts) {
  if (!ts || ts.slot_ms === null || ts.slot_ms === undefined) return "contextual slot unavailable (start or end of the speech)";
  let s = "contextual slot " + Math.round(ts.slot_ms) + " ms between the neighbouring decoded sounds (not this sound's duration)";
  if (ts.pause_adjacent) s += "; next to a pause";
  if (ts.comparables) s += "; " + ts.comparables + " other occurrence" + (ts.comparables > 1 ? "s" : "") + " to compare";
  return s;
}

/** raw engine observation → evidence → interpretation → evidence strength, as four lines. */
function reductionChain(c) {
  const r = c.raw_observation;
  const heard = r.observed ? "decoded /" + r.observed + "/" : "not decoded";
  const p = r.expected_posterior === null || r.expected_posterior === undefined ? "unavailable" : formatProbability(r.expected_posterior);
  const ev = c.evidence;
  const parts = c.interpretation.reasons.slice();
  parts.push(slotText(ev.temporal_slot));
  if (ev.acoustic.relative_energy !== null && ev.acoustic.relative_energy !== undefined) {
    parts.push("relative energy " + formatMeasure(ev.acoustic.relative_energy) + " (" + ev.acoustic.measured_over + ")");
  }
  const expl = c.interpretation.candidate_explanations.map((e) => e.label).join("; ");
  return [
    "Raw engine observation (" + r.engine + "): expected /" + c.expected + "/, " + heard + ", P(/" + c.expected + "/) " + p,
    "Evidence (" + c.interpretation.streams.join(", ").replace(/_/g, " ") + "): " + parts.join("; "),
    "Candidate interpretation: " + c.interpretation.label + (expl ? " — possible explanation: " + expl : ""),
    "Evidence strength: " + (STRENGTH_LABELS[c.evidence_strength] || c.evidence_strength),
  ];
}

function whereText(c) {
  const w = c.where;
  const parts = ["'" + w.word + "'", "word-" + w.word_position];
  if (w.syllable_position) parts.push(w.syllable_position.replace("_", " ") + " (derived)");
  parts.push("between /" + (w.previous_phone || "–") + "/ and /" + (w.next_phone || "–") + "/");
  return parts.join(" · ");
}

// --- M4/M5 evidence renderers (shared by the lab and the M12 reader) ----------------
// `play(playMs, highlightNode)` plays a window of the analysis WAV the evidence refers to;
// `playWhole()` plays the whole recording. The renderers hold no state of their own.
function domEl(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children || []) node.append(c);
  return node;
}

function createEvidenceRenderers({ el, play, playWhole }) {
  function patternCard(p, obsById, target) {
    const card = el("div", { class: "coach-card", "data-pattern": p.id });
    const evidence = el("div", { class: "coach-evidence", hidden: "" });
    const practice = el("div", { class: "coach-practice", hidden: "" });
    card.append(
      el("div", { class: "row" }, [
        el("strong", { class: "ipa", text: coachPatternTitle(p) }),
        el("span", { class: "chip", text: patternClassLabel(p) }),
        el("span", { class: "muted small", text: p.occurrences + " occurrence" + (p.occurrences > 1 ? "s" : "") +
          " · evidence: " + p.evidence_strength.replace("_", " ") }),
      ]),
      el("p", { text: p.summary }),
    );
    if (p.reference_note) card.append(el("p", { class: "muted small", text: p.reference_note }));
    card.append(el("div", { class: "row" }, [
      el("button", { type: "button", class: "view-evidence", text: "View evidence",
        onclick: () => { evidence.hidden = !evidence.hidden; if (!evidence.childNodes.length) fillEvidence(evidence, p, obsById); } }),
      target ? el("button", { type: "button", class: "view-practice", text: target.kind === "practice" ? "Practice" : target.kind === "compare" ? "Listen & compare" : "Listen & monitor",
        onclick: () => { practice.hidden = !practice.hidden; if (!practice.childNodes.length) fillPractice(practice, target); } }) : el("span"),
    ]), evidence, practice);
    return card;
  }

  function fillEvidence(box, p, obsById) {
    const table = el("table", {}, [el("tr", {}, [
      el("th", { text: "Word" }), el("th", { text: "Expected → heard" }), el("th", { text: "Interpretation" }),
      el("th", { text: "Evidence" }), el("th", { text: "Where & context" }), el("th", { text: "Listen" }),
    ])]);
    for (const id of p.observation_ids) {
      const o = obsById[id];
      const row = el("tr", { "data-observation": o.id, class: "obs-" + o.type });
      const probs = ["P(/" + (o.expected || o.observed) + "/) " + formatProbability(o.kind === "insertion" ? o.observed_posterior : o.expected_posterior)];
      if (o.competitor) probs.push("P(/" + o.competitor + "/) " + formatProbability(o.competitor_posterior));
      const ac = o.acoustic;
      row.append(
        el("td", { text: o.word }),
        el("td", { class: "ipa", text: (o.expected ? "/" + o.expected + "/" : "—") + " → " + (o.observed ? "/" + o.observed + "/" : "not detected") }),
        el("td", {}, [el("span", { text: OBSERVATION_LABELS[o.type] + " (" + o.confidence + " confidence)" }),
          el("span", { class: "hint", text: o.reasons.join("; ") })]),
        el("td", { class: "small", text: probs.join(" · ") + (ac ? " · voiced: " + (ac.voiced === null ? "unavailable" : ac.voiced ? "yes" : "no") +
          " · relative energy: " + formatMeasure(ac.relative_energy) : "") }),
        el("td", { class: "small", text: (o.span_ms ? formatSpan(o.span_ms, o.timing_source !== "engine") : "no timing") + " · " + contextText(o.context) +
          (o.timing_source !== "engine" && o.play_ms ? " · plays " + formatSeconds(o.play_ms[1] - o.play_ms[0]) + " from the estimated location" : "") }),
        el("td", {}, [
          o.play_ms ? el("button", { type: "button", class: "play-occurrence", text: "▶ Play exact occurrence", onclick: () => play(o.play_ms, row) }) : el("span", { text: "unavailable" }),
          o.word_play_ms ? el("button", { type: "button", text: "▶ Play word", onclick: () => play(o.word_play_ms, row) }) : el("span"),
        ]),
      );
      table.append(row);
    }
    box.append(table);
    if (p.counter_evidence_ids.length) {
      box.append(el("p", { class: "muted small", text: "Heard as expected elsewhere: " +
        p.counter_evidence_ids.map((id) => obsById[id].word + " (" + formatSpan(obsById[id].span_ms, false) + ")").join(", ") }));
    }
  }

  function fillPractice(box, t) {
    const s = t.levels.sound;
    box.append(el("p", { text: t.reason }));
    const sound = el("div", {}, [el("strong", { text: "1. Sound: " }),
      el("span", { class: "ipa", text: "/" + s.target + "/" + (s.target_hint ? " (" + s.target_hint + ")" : "") +
        (s.contrast ? " vs /" + s.contrast + "/" + (s.contrast_hint ? " (" + s.contrast_hint + ")" : "") : "") })]);
    box.append(sound);
    if (s.guidance) box.append(el("p", { class: "small", text: s.guidance }), el("p", { class: "muted small", text: s.guidance_note }));
    const words = el("div", {}, [el("strong", { text: "2. Word: " })]);
    for (const occ of t.occurrences) {
      words.append(el("button", { type: "button", class: "play-practice-word", text: "▶ " + occ.word,
        onclick: (ev) => play(occ.word_play_ms || occ.play_ms, ev.target) }));
    }
    box.append(words, el("div", {}, [el("strong", { text: "3. Sentence: " }), el("span", { text: t.levels.sentence.text + " " }),
      el("button", { type: "button", text: "▶ Play whole recording", onclick: () => playWhole() })]));
    box.append(el("p", { class: "muted small", text: "Listen to your own occurrence, then record the word and the sentence again." }));
  }

  function reductionCard(c) {
    const card = el("div", { class: "coach-card", "data-candidate": c.id });
    const chain = el("ol", { class: "reduction-chain small" });
    reductionChain(c).forEach((line) => {
      const [step, ...rest] = line.split(": ");
      chain.append(el("li", {}, [el("span", { class: "step", text: step + ": " }), el("span", { text: rest.join(": ") })]));
    });
    card.append(
      el("div", { class: "row" }, [
        el("strong", { class: "ipa", text: c.interpretation.label + " — /" + c.expected + "/ in '" + c.where.word + "'" }),
        el("span", { class: "chip", text: STRENGTH_LABELS[c.evidence_strength] }),
      ]),
      el("p", { text: c.summary }),
      el("p", { class: "small", text: "Where: " + whereText(c) + " · " + formatSpan(c.where.span_ms, c.where.timing_source !== "engine") }),
      chain,
    );
    for (const e of c.interpretation.candidate_explanations) card.append(el("p", { class: "muted small", text: e.text }));
    card.append(el("div", { class: "row" }, [
      el("button", { type: "button", class: "play-candidate", text: "▶ Play exact occurrence", onclick: () => play(c.where.play_ms, card) }),
      c.where.word_play_ms ? el("button", { type: "button", text: "▶ Play word", onclick: () => play(c.where.word_play_ms, card) }) : el("span"),
    ]));
    return card;
  }

  function sideText(s) {
    if (!s) return "no matching sound in this engine's inventory";
    const heard = s.observed ? "decoded /" + s.observed + "/" : "not decoded";
    const cand = s.candidate ? s.candidate.label + " (" + STRENGTH_LABELS[s.candidate.evidence_strength] + ")" : "not listed";
    return "/" + s.expected + "/ " + heard + " at " + (s.span_ms ? formatSpan(s.span_ms, s.timing_source !== "engine") : "no timing") + " — " + cand;
  }

  function renderComparison(box, cmp) {
    box.innerHTML = "";
    box.append(el("p", { class: "muted small engine-note", text: cmp.shared_model_note }));
    if (!cmp.integrity.ok) box.append(el("p", { class: "error", text: "Integrity check: " + cmp.integrity.issues.join("; ") }));
    const [a, b] = cmp.engines;
    if (!cmp.rows.length) { box.append(el("p", { text: "Neither engine lists a candidate." })); return; }
    const table = el("table", { class: "compare-table" }, [el("tr", {}, [
      el("th", { text: "Word" }), el("th", { text: a }), el("th", { text: b }), el("th", { text: "How they relate" }), el("th", { text: "Listen" })])]);
    for (const r of cmp.rows) {
      const row = el("tr", { "data-agreement": r.agreement });
      const play_ms = (r.first && r.first.play_ms) || (r.second && r.second.play_ms);
      row.append(
        el("td", { text: r.word }),
        el("td", { class: "small", text: sideText(r.first) }),
        el("td", { class: "small", text: sideText(r.second) }),
        el("td", { class: "small" }, [el("span", { text: agreementText(r.agreement, a, b) }),
          ...r.notes.map((n) => el("span", { class: "hint", text: n.text }))]),
        el("td", {}, [play_ms ? el("button", { type: "button", class: "play-compare", text: "▶ Play", onclick: () => play(play_ms, row) }) : el("span")]),
      );
      table.append(row);
  }
    box.append(table);
  }
  /**
   * The MVP word detail: one row per expected sound of the word, from the M3 view
   * (`view.words[i]`). `soundCell(s, row)` builds the last column (the lab adds
   * listening notes); without it the cell only plays the sound.
   */
  function wordDetail(box, w, { anchor = null, listenHeader = "Listen", soundCell = null } = {}) {
    box.append(el("h3", { text: "“" + w.word + "” — " + wordTitle(w) }));
    const wordRow = el("div", { class: "row" }, [
      el("button", { type: "button", class: "play-word", text: "▶ Play word", onclick: () => play(w.play_ms, anchor, "word") }),
      el("span", { class: "muted small", text: "Word located at " + formatSpan(w.span_ms, w.timing_estimated) +
        (w.flagged_by_engine ? " · flagged by OpenPronounce" : "") }),
    ]);
    box.append(wordRow);

    const table = el("table", {}, [el("tr", {}, [
      el("th", { text: "Expected" }), el("th", { text: "Heard" }), el("th", { text: "What the recogniser found" }),
      el("th", { text: "Alternatives" }), el("th", { text: "Where" }), el("th", { text: listenHeader }),
    ])]);
    for (const s of w.sounds) {
      const row = el("tr", { class: "cat-" + s.category, "data-sound": String(s.index) });
      row.append(
        el("td", {}, [el("span", { class: "ipa", text: "/" + s.expected + "/" }), el("span", { class: "hint", text: s.expected_hint || "" })]),
        el("td", {}, [el("span", { class: "ipa", text: s.heard ? "/" + s.heard + "/" : "—" }), el("span", { class: "hint", text: s.heard_hint || "" })]),
        el("td", {}, [
          el("span", { text: s.text }),
          el("span", { class: "hint", text: "chance of /" + s.expected + "/: " + formatProbability(s.expected_probability) }),
          el("span", { class: "hint", text: extraSoundsText(s.extra_sounds_after) }),
        ]),
        el("td", { class: "ipa small", text: s.alternatives.slice(0, 3).map((a) => "/" + a.phone + "/ " + formatProbability(a.probability)).join(", ") }),
        el("td", { class: "small", text: formatSpan(s.span_ms, s.timing_estimated) }),
        soundCell ? soundCell(s, row) : el("td", { class: "notes" }, [el("button", { type: "button", class: "play-sound",
          text: "▶ Play sound", onclick: () => play(s.play_ms, row, "sound") })]),
      );
      table.append(row);
    }
    box.append(table);
    box.append(el("p", { class: "muted small", text:
      "“Play sound” plays " + formatSeconds(300) + " around the point where the sound was located; the exact point is shown under “Where”." }));
  }

  return { patternCard, fillEvidence, fillPractice, reductionCard, sideText, renderComparison, wordDetail };
}

// --- M12 reader: the sentence, word by word ----------------------------------------------
const WORD_TOKEN = /[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu;
const normWord = (t) => (t || "").toLowerCase().replace(/[’]/g, "'").replace(/[^\p{L}\p{N}']/gu, "");

/**
 * The complete original text as pieces in order: {text} for separators and words
 * the analysis has no word for, {text, wordIndex} for words matched (in order,
 * with a short look-ahead) to `words` (the view's words). Nothing is dropped.
 */
function alignWords(text, words) {
  const pieces = [];
  let last = 0, wi = 0;
  for (const m of text.matchAll(WORD_TOKEN)) {
    if (m.index > last) pieces.push({ text: text.slice(last, m.index) });
    const t = normWord(m[0]);
    let found = null;
    for (let k = wi; k < Math.min(words.length, wi + 3); k++) {
      if (normWord(words[k].word) === t) { found = k; break; }
    }
    if (found === null) pieces.push({ text: m[0] });
    else { pieces.push({ text: m[0], wordIndex: found }); wi = found + 1; }
    last = m.index + m[0].length;
  }
  if (last < text.length) pieces.push({ text: text.slice(last) });
  return pieces;
}

if (typeof module !== "undefined") {
  module.exports = {
    CATEGORY_TITLES, WORD_TITLES, formatSeconds, formatSpan, formatProbability,
    playbackArgs, analyzeUrl, recordingFilename, engineOptionLabel, summaryChips, extraSoundsText, localTime,
    wordTitle, ALL_WORD_TITLES, fullFeedbackText,
    OBSERVATION_LABELS, PATTERN_CLASS_LABELS, coachPatternTitle, patternClassLabel, formatMeasure, contextText,
    STRENGTH_LABELS, AGREEMENT_LABELS, agreementText, slotText, reductionChain, whereText,
    domEl, createEvidenceRenderers, alignWords,
  };
}

// ---------------------------------------------------------------------------
// Browser UI
// ---------------------------------------------------------------------------

// The lab page only (the M12 reader loads the renderers above without this block).
if (typeof document !== "undefined" && document.getElementById("analyze-btn")) {
  const $ = (id) => document.getElementById(id);
  const state = {
    status: null, tab: "record", recordedBlob: null, recorder: null, chunks: [],
    view: null, audioCtx: null, buffer: null, source: null, selectedWord: null, notes: {},
    browserStore: null, browserSession: null, localPayload: null, localAttempt: null, localJob: null,
    localRecording: null, localAnalysis: false,
  };
  const LOCAL_SESSION_ID = "browser-analysis-session";
  const ENGINE_IDS = ["openpronounce", "wav2vec2_raw"];

  const el = domEl;
  const { patternCard, fillEvidence, fillPractice, reductionCard, renderComparison, wordDetail } = createEvidenceRenderers({
    el, play: (playMs, highlight) => play(playMs, highlight),
    playWhole: () => state.buffer && play([0, state.buffer.duration * 1000], null),
  });

  function showError(message) {
    const box = $("error-box");
    box.textContent = message;
    box.hidden = !message;
  }

  async function api(url, options) {
    const res = await fetch(url, options);
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) {
      const msg = data && data.error ? data.error.message : "Request failed (" + res.status + ").";
      const error = new Error(msg);
      error.code = data && data.error && data.error.code;
      error.status = res.status;
      throw error;
    }
    return data;
  }

  // --- status / engines ---------------------------------------------------
  async function loadStatus() {
    state.status = await api("/api/status");
    const sel = $("engine-select");
    sel.innerHTML = "";
    for (const e of state.status.engines) {
      const opt = el("option", { value: e.id, text: engineOptionLabel(e) });
      if (e.state !== "runnable") opt.disabled = true;
      if (e.id === state.status.default_engine) opt.selected = true;
      sel.append(opt);
    }
    updateEngineNote();
    $("ffmpeg-state").textContent = state.status.ffmpeg ? "installed" : "not installed — only WAV/FLAC will work";
    const recs = state.status.benchmark_recordings;
    if (recs.length) {
      $("benchmark-tab").hidden = false;
      const bs = $("benchmark-select");
      bs.innerHTML = "";
      for (const r of recs) bs.append(el("option", { value: r.id, text: r.id + " — " + r.style + " — " + r.text }));
    }
    $("engines-footer").textContent = "Engines: " + state.status.engines.map(engineOptionLabel).join(" · ");
    if (!state.status.default_engine) showError("No analysis engine is available on this computer.");
  }

  function updateEngineNote() {
    if (state.tab !== "benchmark") {
      $("engine-note").textContent = "Browser recordings and uploads run both local engines independently and are saved in this browser.";
      return;
    }
    const id = $("engine-select").value;
    const e = state.status.engines.find((x) => x.id === id);
    $("engine-note").textContent = e && e.note ? e.note : "";
  }

  async function initializeBrowserStore() {
    const { BrowserStore } = window.PronounceBrowserStore;
    state.browserStore = await BrowserStore.open();
    try {
      state.browserSession = await state.browserStore.loadSession(LOCAL_SESSION_ID);
    } catch (error) {
      if (!error || error.name !== "RecordNotFoundError") throw error;
      state.browserSession = { id: LOCAL_SESSION_ID, state: "active", created_at: new Date().toISOString() };
      try {
        await state.browserStore.createSession(state.browserSession);
      } catch (createError) {
        if (!createError || createError.name !== "DuplicateRecordError") throw createError;
        state.browserSession = await state.browserStore.loadSession(LOCAL_SESSION_ID);
      }
    }
  }

  // --- tabs ------------------------------------------------------------------
  function selectTab(tab) {
    state.tab = tab;
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    document.querySelectorAll(".tab-body").forEach((b) => (b.hidden = b.dataset.body !== tab));
    if (tab === "benchmark") fillBenchmarkText();
    updateEngineNote();
  }

  function fillBenchmarkText() {
    const id = $("benchmark-select").value;
    const r = state.status.benchmark_recordings.find((x) => x.id === id);
    if (r) $("target-text").value = r.text;
  }

  // --- recording ---------------------------------------------------------------
  async function toggleRecording() {
    const btn = $("record-btn");
    if (state.recorder && state.recorder.state === "recording") {
      state.recorder.stop();
      return;
    }
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      showError("This browser cannot record audio. Upload a file instead.");
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      showError("Microphone access was denied or is unavailable.");
      return;
    }
    showError("");
    state.chunks = [];
    state.recorder = new MediaRecorder(stream);
    state.recorder.ondataavailable = (ev) => { if (ev.data.size) state.chunks.push(ev.data); };
    const started = Date.now();
    const timer = setInterval(() => { $("record-timer").textContent = formatSeconds(Date.now() - started); }, 100);
    state.recorder.onstop = () => {
      clearInterval(timer);
      stream.getTracks().forEach((t) => t.stop());
      state.recordedBlob = new Blob(state.chunks, { type: state.recorder.mimeType });
      const preview = $("record-preview");
      preview.src = URL.createObjectURL(state.recordedBlob);
      preview.hidden = false;
      btn.textContent = "● Record again";
      btn.classList.remove("recording");
    };
    state.recorder.start();
    btn.textContent = "■ Stop recording";
    btn.classList.add("recording");
  }

  // --- analysis --------------------------------------------------------------
  function localId() {
    return crypto.randomUUID().replace(/-/g, "");
  }

  async function persistLocalState(attempt, job, nextState) {
    const now = new Date().toISOString();
    job.state = nextState;
    job.updated_at = now;
    job.state_history.push({ state: nextState, at: now });
    attempt.state = nextState;
    const started = performance.now();
    await state.browserStore.saveJob(job);
    await state.browserStore.saveAttempt(attempt);
    job.timings.persistence_ms += performance.now() - started;
  }

  function engineProcessingTimes(payload) {
    return Object.fromEntries(ENGINE_IDS.map((engineId) => {
      const timing = payload.analyses[engineId].evidence?.processing;
      const value = timing && Number.isFinite(timing.wall_time_ms) ? timing.wall_time_ms : null;
      return [engineId, value];
    }));
  }

  async function analyzeLocally(text, blob, filename) {
    const attemptId = localId();
    const jobId = localId();
    const now = new Date().toISOString();
    const attempt = {
      id: attemptId,
      session_id: state.browserSession.id,
      target_text: text,
      created_at: now,
      state: "queued",
      job_ids: [jobId],
      analysis_result: null,
    };
    const job = {
      id: jobId,
      session_id: state.browserSession.id,
      attempt_id: attemptId,
      state: "queued",
      created_at: now,
      updated_at: now,
      state_history: [{ state: "queued", at: now }],
      result: null,
      error: null,
      timings: { request_ms: null, persistence_ms: 0, engine_wall_time_ms: null },
    };
    const persistenceStarted = performance.now();
    await state.browserStore.saveRecording(state.browserSession.id, attemptId, blob, filename);
    await state.browserStore.saveAttempt(attempt);
    await state.browserStore.saveJob(job);
    job.timings.persistence_ms += performance.now() - persistenceStarted;

    try {
      await persistLocalState(attempt, job, "uploading");
      const form = new FormData();
      form.append("target_text", text);
      form.append("audio", blob, filename);
      await persistLocalState(attempt, job, "analyzing");
      const requestStarted = performance.now();
      const payload = await api("/api/stateless/analyze", { method: "POST", body: form });
      job.timings.request_ms = performance.now() - requestStarted;
      if (!payload || !payload.analyses || ENGINE_IDS.some((id) => !payload.analyses[id])) {
        throw new Error("The analysis service returned an incomplete response.");
      }
      job.timings.engine_wall_time_ms = engineProcessingTimes(payload);
      job.result = payload;
      attempt.analysis_result = payload;
      await persistLocalState(attempt, job, "complete");
      await state.browserStore.saveJob(job);
      await state.browserStore.saveAttempt(attempt);
      await displayLocalResult(attempt, job, blob);
      $("local-analysis-timing").hidden = false;
      $("local-analysis-timing").textContent =
        `Browser request: ${Math.round(job.timings.request_ms)} ms · local persistence: ` +
        `${Math.round(job.timings.persistence_ms)} ms · engine wall times: ` +
        ENGINE_IDS.map((id) => `${id} ${job.timings.engine_wall_time_ms[id] === null
          ? "unavailable" : Math.round(job.timings.engine_wall_time_ms[id]) + " ms"}`).join(" · ");
      return { attempt, job };
    } catch (error) {
      const message = error && error.message ? error.message : "The analysis request failed.";
      job.error = { code: error.code || "analysis_request_failed", message };
      try {
        await persistLocalState(attempt, job, "failed");
        await state.browserStore.saveJob(job);
        await state.browserStore.saveAttempt(attempt);
      } catch (persistenceError) {
        throw new Error(`Analysis failed and its local failure state could not be saved: ${persistenceError.message}`);
      }
      throw error;
    }
  }

  async function displayLocalResult(attempt, job, recording) {
    state.localPayload = job.result;
    state.localAttempt = attempt;
    state.localJob = job;
    state.localRecording = recording;
    state.localAnalysis = true;
    const select = $("result-engine-select");
    select.innerHTML = "";
    for (const engineId of ENGINE_IDS) {
      const analysis = job.result.analyses[engineId];
      const label = engineId === "openpronounce" ? "OpenPronounce" : "Raw Wav2Vec2";
      select.append(el("option", { value: engineId, text: `${label} — ${analysis.state}` }));
    }
    $("result-engine-label").hidden = false;
    select.hidden = false;
    select.onchange = () => displayLocalEngine(select.value);
    select.value = ENGINE_IDS[0];
    await displayLocalEngine(select.value);
  }

  async function displayLocalEngine(engineId) {
    const analysis = state.localPayload.analyses[engineId];
    const evidence = analysis.evidence;
    const view = evidence
      ? { ...evidence, source: "Browser-local recording" }
      : {
        state: analysis.state,
        engine: analysis.result?.engine || { id: engineId },
        target_text: state.localPayload.target_text,
        words: [],
        summary: {},
        error: analysis.error || { message: "This engine could not complete the analysis." },
      };
    state.localAnalysis = true;
    state.buffer = null;
    try {
      state.audioCtx = state.audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      state.buffer = await state.audioCtx.decodeAudioData(await state.localRecording.arrayBuffer());
      $("audio-info").textContent = "Playing the original browser recording; it is retained locally.";
    } catch (error) {
      state.buffer = null;
      $("audio-info").textContent = "Playback is unavailable in this browser; the original recording remains saved locally.";
    }
    await showResult(view);
    $("result-engine-label").hidden = false;
    $("result-engine-select").hidden = false;
  }

  async function analyse() {
    showError("");
    const text = $("target-text").value.trim();
    const engine = $("engine-select").value;
    if (!text) { showError("Enter the sentence you read aloud."); return; }

    let request;
    let localInput = null;
    if (state.tab === "benchmark") {
      state.localAnalysis = false;
      $("result-engine-label").hidden = true;
      $("result-engine-select").hidden = true;
      $("local-analysis-timing").hidden = true;
      request = api("/api/analyze-benchmark", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recording_id: $("benchmark-select").value, engine: engine, text: text }),
      });
    } else {
      let blob = null, name = "upload";
      if (state.tab === "record") {
        blob = state.recordedBlob;
        name = blob ? recordingFilename(blob.type) : name;
        if (!blob) { showError("Record something first."); return; }
      } else {
        const f = $("file-input").files[0];
        if (!f) { showError("Choose an audio file first."); return; }
        blob = f; name = f.name;
      }
      localInput = { blob, name };
    }
    if (localInput && (!state.browserStore || !state.browserSession)) {
      showError("Browser-local storage is not ready. Please wait and try again.");
      return;
    }

    const btn = $("analyze-btn");
    btn.disabled = true;
    if (localInput) {
      state.localAnalysis = false;
      state.localPayload = state.localAttempt = state.localJob = state.localRecording = null;
      $("results-panel").hidden = true;
      $("result-engine-label").hidden = true;
      $("result-engine-select").hidden = true;
      $("local-analysis-timing").hidden = true;
    }
    $("run-status").textContent = "Analysing… (the first analysis also loads the model)";
    try {
      if (localInput) {
        await analyzeLocally(text, localInput.blob, localInput.name);
      } else {
        await showResult(await request);
      }
      $("run-status").textContent = "";
      await refreshHistory();
    } catch (e) {
      $("run-status").textContent = "";
      showError(e.message);
      if (localInput) {
        try {
          await refreshHistory();
        } catch (historyError) {
          showError(`${e.message} Local history could not be refreshed: ${historyError.message}`);
        }
      }
    } finally {
      btn.disabled = false;
    }
  }

  // --- audio ---------------------------------------------------------------------
  async function loadAudio(url) {
    state.audioCtx = state.audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const data = await (await fetch(url)).arrayBuffer();
    state.buffer = await state.audioCtx.decodeAudioData(data);
  }

  function play(playMs, highlight) {
    if (!state.buffer) return;
    if (state.audioCtx.state === "suspended") state.audioCtx.resume();
    if (state.source) { try { state.source.stop(); } catch (e) { /* already stopped */ } }
    document.querySelectorAll(".playing").forEach((n) => n.classList.remove("playing"));
    const args = playbackArgs(playMs, state.buffer.duration);
    const src = state.audioCtx.createBufferSource();
    src.buffer = state.buffer;
    src.connect(state.audioCtx.destination);
    if (highlight) highlight.classList.add("playing");
    src.onended = () => { if (highlight) highlight.classList.remove("playing"); };
    src.start(0, args.offset, args.duration);
    state.source = src;
  }

  // --- result rendering ------------------------------------------------------------
  async function showResult(view) {
    state.view = view;
    state.selectedWord = null;
    $("results-panel").hidden = false;
    $("result-source").textContent = "— " + (view.source || "") + " · " + view.engine.id;
    $("caveat-list").innerHTML = "";
    (view.caveats || []).forEach((c) => $("caveat-list").append(el("li", { text: c })));
    $("word-detail").hidden = true;
    $("full-feedback").hidden = true;
    $("coach").hidden = true;
    $("coach").innerHTML = "";
    $("reduction").hidden = true;
    $("reduction").innerHTML = "";
    $("full-feedback").innerHTML = "";
    $("sentence").innerHTML = "";
    $("summary").innerHTML = "";
    $("heard-sequence").textContent = "";

    const msg = $("result-message");
    msg.hidden = true;
    if (view.state === "failed" || view.state === "unavailable") {
      msg.hidden = false;
      msg.textContent = view.error.message + (view.error.detail ? " (" + view.error.detail + ")" : "");
    } else if (view.state === "no_speech") {
      msg.hidden = false;
      msg.textContent = view.message;
    } else if (view.state === "partial") {
      msg.hidden = false;
      msg.textContent = "Some evidence is incomplete: " + (view.warnings || []).join("; ");
    }

    if (view.audio) {
      await loadAudio(view.audio.url);
      $("audio-info").textContent = "Playing the analysed audio (16 kHz mono, " + formatSeconds(view.audio.duration_ms) +
        (view.audio.conversion === "ffmpeg" ? ", converted from " + view.audio.original_name : "") + ").";
    } else if (!state.localAnalysis) {
      state.buffer = null;
      $("audio-info").textContent = "";
    }
    for (const chip of summaryChips(view.summary || {})) {
      $("summary").append(el("span", { class: "chip cat-" + chip.category, text: chip.text }));
    }
    for (const w of view.words || []) {
      const b = el("button", {
        type: "button", class: "word cat-" + w.status, text: w.word,
        title: w.word + ": " + wordTitle(w), "data-word": String(w.index),
        onclick: () => selectWord(w, b),
      });
      $("sentence").append(b);
    }
    if (view.heard_sequence) $("heard-sequence").textContent = "/" + view.heard_sequence.join(" ") + "/";
    state.notes = {};
    if (view.analysis_id && (view.words || []).length) {
      try {
        const res = await api("/api/analyses/" + view.analysis_id + "/notes");
        for (const n of res.notes) state.notes[n.sound_index] = n.verdict;
      } catch (e) { /* notes are optional */ }
    }
  }

  function selectWord(w, button) {
    document.querySelectorAll(".word.selected").forEach((n) => n.classList.remove("selected"));
    button.classList.add("selected");
    state.selectedWord = w;
    const box = $("word-detail");
    box.hidden = false;
    box.innerHTML = "";
    wordDetail(box, w, { anchor: button, listenHeader: "Listen & note", soundCell: noteCell });
  }

  function noteCell(s, row) {
    const td = el("td", { class: "notes" });
    td.append(el("button", { type: "button", text: "▶ Play sound", onclick: () => play(s.play_ms, row) }));
    if (state.localAnalysis || !state.status.notes_enabled) return td;
    const options = [
      ["as_expected", "I hear /" + s.expected + "/"],
      ["as_heard", s.heard && s.heard !== s.expected ? "I hear /" + s.heard + "/" : null],
      ["something_else", "Something else"],
      ["cannot_tell", "Can't tell"],
    ].filter((o) => o[1]);
    const saved = el("div", { class: "saved", text: state.notes[s.index] ? "Noted: " + state.notes[s.index].replace(/_/g, " ") : "" });
    const btns = el("div");
    for (const [verdict, label] of options) {
      btns.append(el("button", { type: "button", text: label, onclick: async () => {
        try {
          await api("/api/notes", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ analysis_id: state.view.analysis_id, sound_index: s.index, verdict: verdict }) });
          state.notes[s.index] = verdict;
          saved.textContent = "Noted: " + verdict.replace(/_/g, " ");
        } catch (e) { showError(e.message); }
      } }));
    }
    td.append(btns, saved);
    return td;
  }

  // --- full recording feedback ------------------------------------------------------
  function showFullFeedback() {
    const view = state.view;
    if (!view) return;
    const box = $("full-feedback");
    box.hidden = false;
    box.innerHTML = "";
    const status = el("span", { class: "muted small", id: "copy-feedback-status", role: "status" });
    box.append(
      el("h3", { text: "Full Recording Feedback — “" + view.target_text + "”" }),
      el("div", { class: "row" }, [
        el("button", { type: "button", id: "copy-feedback-btn", text: "Copy Feedback", onclick: () => copyFeedback(status) }),
        status,
      ]),
    );

    if (view.state === "failed" || view.state === "unavailable") {
      box.append(el("p", { text: view.error.message + (view.error.detail ? " (" + view.error.detail + ")" : "") }));
      return;
    }
    if (view.state === "no_speech") {
      box.append(el("p", { text: view.message }));
      return;
    }
    box.append(el("p", { class: "small", text: "Words: " + view.words.length + " · " +
      summaryChips(view.summary || {}).map((c) => c.text).join(" · ") }));

    for (const w of view.words) {
      const heading = el("h4", { class: "cat-" + w.status, "data-full-word": String(w.index) }, [
        el("span", { text: (w.index + 1) + ". “" + w.word + "” — " + wordTitle(w) + " " }),
        el("button", { type: "button", text: "▶ Play word", onclick: () => play(w.play_ms, heading) }),
        el("span", { class: "muted small", text: " " + formatSpan(w.span_ms, w.timing_estimated) +
          (w.flagged_by_engine ? " · flagged by OpenPronounce" : "") }),
      ]);
      const table = el("table", {}, [el("tr", {}, [
        el("th", { text: "Expected" }), el("th", { text: "Heard" }), el("th", { text: "What the recogniser found" }),
        el("th", { text: "Alternatives" }), el("th", { text: "Where" }), el("th", { text: "Listen" }),
      ])]);
      for (const s of w.sounds) {
        const row = el("tr", { class: "cat-" + s.category, "data-full-sound": String(s.index) });
        row.append(
          el("td", {}, [el("span", { class: "ipa", text: "/" + s.expected + "/" }), el("span", { class: "hint", text: s.expected_hint || "" })]),
          el("td", {}, [el("span", { class: "ipa", text: s.heard ? "/" + s.heard + "/" : "—" }), el("span", { class: "hint", text: s.heard_hint || "" })]),
          el("td", {}, [
            el("span", { text: s.text }),
            el("span", { class: "hint", text: "chance of /" + s.expected + "/: " + formatProbability(s.expected_probability) }),
            el("span", { class: "hint", text: extraSoundsText(s.extra_sounds_after) }),
          ]),
          el("td", { class: "ipa small", text: s.alternatives.slice(0, 3).map((a) => "/" + a.phone + "/ " + formatProbability(a.probability)).join(", ") }),
          el("td", { class: "small", text: formatSpan(s.span_ms, s.timing_estimated) }),
          el("td", {}, [el("button", { type: "button", text: "▶ Play sound", onclick: () => play(s.play_ms, row) })]),
        );
        table.append(row);
      }
      box.append(heading, table);
    }
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function copyFeedback(status) {
    const text = fullFeedbackText(state.view);
    try {
      await navigator.clipboard.writeText(text);
      status.textContent = "Copied — paste it anywhere (e.g. ChatGPT).";
    } catch (e) {
      // Clipboard blocked: show the text selected so it can be copied by hand.
      let area = document.getElementById("copy-feedback-text"); // created on demand
      if (!area) {
        area = el("textarea", { id: "copy-feedback-text", rows: "10", readonly: "readonly" });
        status.after(area);
      }
      area.value = text;
      area.select();
      status.textContent = "The browser blocked copying; the text below is selected — press Cmd+C.";
    }
  }

  // --- M4 Phoneme Coach ------------------------------------------------------------
  function showCoach() {
    const view = state.view;
    const coach = view && view.coach;
    const box = $("coach");
    box.hidden = false;
    box.innerHTML = "";
    box.append(el("h3", { text: "Phoneme Coach" }));
    if (!coach || coach.state !== "ok") {
      box.append(el("p", { text: (coach && coach.message) || "No coaching is available for this analysis." }));
      return;
    }
    box.append(el("p", { class: "muted small", text: "Evidence from " + coach.engine.id + ". " + coach.engine.shared_model_note }));
    const cav = el("details", {}, [el("summary", { text: "How to read the coach" })]);
    const ul = el("ul");
    coach.caveats.forEach((c) => ul.append(el("li", { text: c })));
    cav.append(ul);
    box.append(cav);
    if (!coach.integrity.ok) box.append(el("p", { class: "error", text: "Integrity check: " + coach.integrity.issues.join("; ") }));
    box.append(el("p", { class: "small", id: "coach-coverage", text: coach.coverage.consistent_with_expected + " of " +
      coach.coverage.sounds + " sounds were consistent with the expected sound. " + coach.coverage.note }));

    const obsById = Object.fromEntries(coach.observations.map((o) => [o.id, o]));
    const patById = Object.fromEntries(coach.patterns.map((p) => [p.id, p]));
    const tgtByPattern = Object.fromEntries(coach.practice_targets.map((t) => [t.pattern_id, t]));
    if (!coach.groups.length) box.append(el("p", { text: "Every interpretable sound was consistent with the expected sound." }));

    for (const g of coach.groups) {
      // Recurring and not-detected groups stay open; long lists of single or
      // ambiguous observations start collapsed so recurring findings are not buried.
      const open = g.id === "recurring" || g.id === "not_detected" || g.pattern_ids.length <= COACH_OPEN_LIMIT;
      const section = el("details", { class: "coach-group", "data-group": g.id }, [
        el("summary", {}, [el("h4", { text: g.title + " (" + g.pattern_ids.length + ")" })]),
        el("p", { class: "muted small", text: g.explanation }),
      ]);
      section.open = open;
      for (const pid of g.pattern_ids) section.append(patternCard(patById[pid], obsById, tgtByPattern[pid]));
      box.append(section);
    }

    const skipped = coach.observations.filter((o) => o.type === "not_interpreted");
    if (skipped.length) {
      const d = el("details", { "data-group": "not_interpreted" }, [el("summary", { text: "Not interpreted (" + skipped.length + ")" })]);
      const list = el("ul", { class: "small" });
      skipped.forEach((o) => list.append(el("li", { text: o.word + " /" + (o.expected || o.observed) + "/ — " + o.reasons.join("; ") })));
      d.append(list);
      box.append(d);
    }
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // --- M5 Reduction & Connected Speech -------------------------------------------------
  function showReduction() {
    const view = state.view;
    const red = view && view.reduction;
    const box = $("reduction");
    box.hidden = false;
    box.innerHTML = "";
    box.append(el("h3", { text: "Reduction & Connected Speech" }));
    if (!red || red.state !== "ok") {
      box.append(el("p", { text: "No reduction analysis is available for this analysis." }));
      return;
    }
    box.append(el("p", { class: "muted small", text: "Recording: " + (view.source || "") + " · evidence from " + red.engine.id + "." }));
    const cav = el("details", {}, [el("summary", { text: "How to read this" })]);
    const ul = el("ul");
    red.caveats.forEach((c) => ul.append(el("li", { text: c })));
    cav.append(ul);
    box.append(cav);
    if (!red.integrity.ok) box.append(el("p", { class: "error", text: "Integrity check: " + red.integrity.issues.join("; ") }));
    const rate = red.speaking_rate || {};
    box.append(el("p", { class: "small", id: "reduction-rate", text: "Speaking rate: " +
      (rate.phones_per_s_excluding_pauses ? rate.phones_per_s_excluding_pauses.toFixed(1) + " decoded sounds per second between pauses" : "unavailable") +
      " · pauses ≥ " + red.thresholds.pause_ms + " ms: " + (rate.pauses || 0) }));

    const other = red.engine.id === "openpronounce" ? "wav2vec2_raw" : "openpronounce";
    const cmpBox = el("div", { id: "reduction-compare" });
    if (state.localAnalysis) {
      box.append(el("p", { class: "muted small", text:
        "Both engines' evidence is saved separately in this browser. Use the engine selector above to view each result." }));
    } else {
      const cmpBtn = el("button", { type: "button", id: "compare-btn", text: "Compare with " + other, onclick: () => runCompare(cmpBox, cmpBtn) });
      box.append(el("div", { class: "row" }, [
        cmpBtn,
        el("span", { class: "muted small", text: "Runs the other local engine on the same audio. Both share one acoustic model; differences are kept, not resolved." }),
      ]), cmpBox);
    }

    const byId = Object.fromEntries(red.candidates.map((c) => [c.id, c]));
    if (!red.groups.length) box.append(el("p", { text: "No reduction or connected-speech candidates in this recording's evidence." }));
    for (const g of red.groups) {
      const open = g.id !== "insufficient_evidence" && g.candidate_ids.length <= REDUCTION_OPEN_LIMIT;
      const section = el("details", { class: "coach-group", "data-reduction-group": g.id },
        [el("summary", {}, [el("h4", { text: g.title + " (" + g.candidate_ids.length + ")" })])]);
      section.open = open;
      for (const id of g.candidate_ids) section.append(reductionCard(byId[id]));
      box.append(section);
    }
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function runCompare(box, btn) {
    btn.disabled = true;
    box.innerHTML = "";
    box.append(el("p", { class: "muted small", text: "Analysing the same audio with the other engine…" }));
    try {
      const cmp = await api("/api/analyses/" + state.view.analysis_id + "/compare", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      renderComparison(box, cmp);
    } catch (e) {
      box.innerHTML = "";
      box.append(el("p", { class: "error", text: "Comparison failed: " + e.message }));
    } finally {
      btn.disabled = false;
    }
  }

  // --- history -----------------------------------------------------------------------
  async function refreshHistory() {
    const list = $("history-list");
    list.innerHTML = "";
    const attempts = await state.browserStore.attempts(state.browserSession.id);
    for (const attempt of attempts.slice().reverse()) {
      const jobId = attempt.job_ids && attempt.job_ids[attempt.job_ids.length - 1];
      const job = jobId ? await state.browserStore.loadJob(attempt.session_id, attempt.id, jobId) : null;
      list.append(el("li", {
        "data-local-attempt": attempt.id,
        text: `${localTime(attempt.created_at)} · Browser-local · ${attempt.state}` +
          ` · “${attempt.target_text}”`,
        onclick: async () => {
          showError("");
          try {
            const recording = await state.browserStore.loadRecording(attempt.session_id, attempt.id);
            if (!job || !job.result || !recording) {
              throw new Error((job && job.error && job.error.message) ||
                "This local analysis is incomplete; its browser recording is still available.");
            }
            await displayLocalResult(attempt, job, recording.blob);
            $("local-analysis-timing").hidden = false;
            $("local-analysis-timing").textContent = `Browser request: ${job.timings.request_ms === null
              ? "unavailable" : Math.round(job.timings.request_ms) + " ms"} · local persistence: ` +
              `${Math.round(job.timings.persistence_ms)} ms`;
          } catch (error) {
            showError(error.message);
          }
        },
      }));
    }
    if (!attempts.length) {
      list.append(el("li", { text: "No browser-local analyses yet." }));
      return;
    }
  }

  // --- wiring --------------------------------------------------------------------------
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));
  $("record-btn").addEventListener("click", toggleRecording);
  $("benchmark-select").addEventListener("change", fillBenchmarkText);
  $("engine-select").addEventListener("change", updateEngineNote);
  $("analyze-btn").addEventListener("click", analyse);
  $("play-all-btn").addEventListener("click", () => state.buffer && play([0, state.buffer.duration * 1000], null));
  $("full-feedback-btn").addEventListener("click", showFullFeedback);
  $("coach-btn").addEventListener("click", showCoach);
  $("reduction-btn").addEventListener("click", showReduction);
  $("record-support").textContent = window.MediaRecorder ? "Recording uses your browser's microphone." : "This browser cannot record; use Upload.";
  loadStatus().then(async () => {
    await initializeBrowserStore();
    await refreshHistory();
  }).catch((e) => showError("Could not initialize browser-owned analysis: " + e.message));
}
