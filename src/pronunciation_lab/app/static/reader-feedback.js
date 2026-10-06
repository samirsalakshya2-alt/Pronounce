// M12 reader — inline feedback. Evidence is rendered with the shared M4/M5
// renderers from app.js (createEvidenceRenderers); nothing here interprets
// pronunciation. Every Listen control plays one window of one attempt's
// analysis WAV and records {session_id, segment_id, attempt_id, job_id, timeline, play_ms}.
"use strict";

// --- pure helpers (node-testable) ----------------------------------------------------------------
function sentencesText(list) {
  return (list.length === 1 ? "sentence " : "sentences ") + list.join(", ");
}

function feedbackLine(fb) {
  if (!fb) return "";
  if (fb.state !== "ok") return fb.message || "No speech sounds were detected.";
  const parts = [];
  if (fb.notice) parts.push(`${fb.notice} thing${fb.notice === 1 ? "" : "s"} to notice`);
  if (fb.compare) parts.push(`${fb.compare} to compare`);
  return parts.join(" · ") || "Nothing stood out";
}

/** The attempt whose feedback a sentence shows: the latest one not discarded (or the latest). */
function displayAttempt(attempts) {
  const kept = attempts.filter((a) => a.user_disposition !== "discarded");
  return (kept.length ? kept : attempts)[(kept.length ? kept : attempts).length - 1] || null;
}

function primaryJob(attempt, jobs) {
  const ids = (attempt && attempt.job_ids) || [];
  const primaries = ids.map((id) => jobs[id]).filter((j) => j && j.kind === "primary");
  return primaries.length ? primaries[primaries.length - 1] : null;
}

// Identity (is this the sentence?) is shown separately from the boundary and from feedback safety; the
// server's attempt.status carries the decision and its wording (reader/status.py). These are fallbacks.
const TARGET_TEXT = {
  MATCH: "Recording appears to match this sentence.",
  LIKELY_MATCH: "This recording is probably this sentence (it fits it best of the article's sentences), but the evidence is weak.",
  AMBIGUOUS: "I couldn't confidently tell whether this recording is this sentence.",
  MISMATCH: "This recording appears to contain a different sentence.",
  TOO_SHORT: "Recording is too short to confirm the sentence.",
  FAILED: "Recording could not be analysed.",
};
const NOTE_IDENTITY = { AMBIGUOUS: "couldn't confirm the sentence", LIKELY_MATCH: "probably this sentence",
  MISMATCH: "appears to be a different sentence" };

/** The identity/feedback decision for one attempt; "kept" from the attempt itself (it changes on click). */
function identityOf(a, job) {
  const st = (a && a.status) || {};
  const identity = st.identity || (job && job.target_confirmation ? job.target_confirmation.state : null);
  const kept = !!a && a.user_disposition === "kept";
  const feedback = st.feedback || (identity === "MISMATCH" ? "hidden_identity" : "shown");
  return { identity, kept, feedback, message: st.message || TARGET_TEXT[identity] || "",
    needsDecision: ["AMBIGUOUS", "MISMATCH"].includes(identity) && !kept
      && !["discarded", "rerecord_requested"].includes(a && a.user_disposition) };
}

// M7: what the reader says about speech that continued after the sentence (never a warning or a score)
const BOUNDARY_TEXT = {
  TARGET_PLUS_OVERFLOW: {
    line: "Continued speech detected after this sentence.",
    sub: "This continuation was not included in the pronunciation analysis.",
  },
  BOUNDARY_UNCERTAIN: {
    line: "Sentence boundary uncertain — some continued speech may not be included in this sentence's analysis.",
    sub: "",
  },
};

/** The other engine's own boundary assessment, kept on the attempt's comparison job (null before a comparison). */
function otherBoundary(attempt, jobs) {
  const ids = (attempt && attempt.job_ids) || [];
  const c = ids.map((id) => jobs[id]).filter((j) => j && j.kind === "comparison" && j.state === "SUCCEEDED").pop();
  return (c && c.boundary && c.boundary.other_engine) || null;
}

/** The boundary note for a job, or null (sentence only / no boundary checked). */
function boundaryNote(boundary) {
  if (!boundary) return null;
  // feedback withheld (no defensible boundary): the uncertain wording, never the whole attempt's feedback
  const state = boundary.feedback_withheld ? "BOUNDARY_UNCERTAIN" : boundary.state;
  if (!BOUNDARY_TEXT[state]) return null;
  const regions = boundary.regions || [];
  const after = regions.find((r) => r.kind === "overflow" || r.kind === "uncertain") || null;
  const target = regions.find((r) => r.kind === "target") || null;
  return { state, ...BOUNDARY_TEXT[state], after, target, withheld: !!boundary.feedback_withheld };
}

/** M7: how the end of the sentence was established (boundary confidence is separate from analysis quality). */
const BOUNDARY_CONFIDENCE_TEXT = {
  supported: "by the pause and the continued speech around it",
  clear_recording: "by the decoded sentence (the recording was decoded clearly enough)",
  insufficient: "not established: no clear pause, and the recording was decoded unclearly",
};

/** The Details sentence for a boundary: genuinely unknown end, analysis not contained, or the usual text. */
function boundaryPlainText(b, plain) {
  if (b.feedback_withheld && b.withheld_reason === "containment") {
    return "The sentence was separated from the speech that followed (listen to each part below), but part of its analysis fell outside the sentence, so none of it is shown. The recording is kept.";
  }
  if (b.feedback_withheld) {
    return "Where this sentence ends could not be established, and speech seems to continue after it. No pronunciation feedback is shown for this recording; the recording is kept.";
  }
  return plain;
}

/** The reading summary's two lines: what was read and identified, then what feedback covers. */
function readingLines(cov) {
  const n = (k, one, many) => `${k} ${k === 1 ? one : many}`;
  const first = [`${cov.recorded ?? cov.read} of ${n(cov.sentences, "sentence", "sentences")} recorded`];
  if (cov.identified) first.push(`${cov.identified} identified`);
  if (cov.uncertain) first.push(`${cov.uncertain} uncertain`);
  if (cov.different) first.push(`${cov.different} ${cov.different === 1 ? "seems" : "seem"} to be a different sentence`);
  const second = [`${n(cov.feedback_included ?? cov.included, "sentence", "sentences")} in the feedback below`];
  if (cov.feedback_low_confidence) second.push(`${cov.feedback_low_confidence} with uncertain pronunciation evidence`);
  if (cov.feedback_withheld) second.push(`${cov.feedback_withheld} withheld because the sentence boundary was uncertain`);
  if (cov.feedback_withheld_containment) second.push(`${cov.feedback_withheld_containment} withheld because part of the analysis fell outside the sentence`);
  if (cov.awaiting_decision) second.push(`${cov.awaiting_decision} waiting for you to keep or re-record`);
  return [first.join(" · "), second.join(" · ")];
}

// M8: fluency — a separate dimension from pronunciation; counts of things to notice, never a score
function fluencyLine(compact) {
  if (!compact || compact.state !== "ok" || !compact.notice) return "";
  return `${compact.notice} fluency thing${compact.notice === 1 ? "" : "s"} to notice`;
}

function rateText(m) {
  if (!m) return "Speech rate: not measured.";
  if (!m.rate_available) return `Speech rate: not measured — ${m.rate_unavailable_reason}.`;
  const speech = `Speech rate: ${m.speaking_rate.toFixed(1)} syllables per second`;
  // articulation rate needs pauses verified as silence (older stored results have no flag: available)
  if (m.articulation_available === false) return `${speech} · articulation rate not measured — ${m.articulation_unavailable_reason}.`;
  const pauses = m.pause_count ? ` · ${m.pause_count} pause${m.pause_count === 1 ? "" : "s"} (${(m.pause_total_ms / 1000).toFixed(1)} s)` : " · no pauses";
  return `${speech} · articulation rate ${m.articulation_rate.toFixed(1)} (without pauses)${pauses}`;
}

/** The exact interval of one observation, and what its Listen control plays around it. */
function spanText(o) {
  return `${(o.start_ms / 1000).toFixed(2)}–${(o.end_ms / 1000).toFixed(2)} s`;
}

function listenTitle(o) {
  const c = (o.playback && o.playback.context_ms) || [0, 0];
  if (!c[0] && !c[1]) return `Plays exactly ${spanText(o)}`;
  return `Plays ${spanText(o)} with ${(c[0] / 1000).toFixed(2)} s before and ${(c[1] / 1000).toFixed(2)} s after it for context`;
}

/** What the Details section lists: things to notice first; everything else under "Other timing". */
function fluencyItems(fl) {
  const obs = (fl && fl.state === "ok" && fl.observations) || [];
  return { notice: obs.filter((o) => o.notice), other: obs.filter((o) => !o.notice) };
}

function fluencyItemText(o) {
  const where = o.context && o.context.word_before && o.context.word_after && o.type === "PAUSE"
    ? ` (${o.context.position}, after “${o.context.word_before}”)`
    : (o.context && o.context.position ? ` (${o.context.position})` : "");
  return `${o.observed}${where}`;
}

const STRENGTH_TEXT = { moderate: "evidence: moderate", low: "evidence: low", ambiguous: "evidence: ambiguous",
  insufficient: "evidence: insufficient" };

// M9: "What to practise now" — 0–3 actions; the concise view, built only from the coaching contract.
const COACHING_UNAVAILABLE = "Practice suggestions are not available right now.";

function coachingView(c) {
  if (!c) return { state: "none", actions: [], message: null };
  if (c.state === "unavailable") return { state: "unavailable", actions: [], message: COACHING_UNAVAILABLE };
  if (c.state !== "actions") {
    const na = c.no_action || {};
    return { state: "no_action", actions: [], message: na.message || null, help: na.what_would_help || null };
  }
  return {
    state: "actions",
    actions: c.actions.map((a) => ({
      rank: a.rank_in_plan, title: a.action_text, minutes: a.time_minutes, why: a.why.text,
      steps: a.practice.steps.map((st) => st.text),
      examples: a.practice.examples.slice(0, 3).map((e) => ({ label: e.word || e.label || "this moment", ref: e })),
      retest: a.practice.retest_sentences.map((r) => ({ text: r.text, ref: r.ref })),
      transfer: a.transfer && a.transfer.text,
    })),
    message: null,
  };
}

// M9 "This reading": what happened in this article — a description, scoped to this reading, never advice.
const READING_SCOPE = "This reading only";
const COACHING_SCOPE = "Based on your recent readings";

// Order inside "This reading": major improvement areas → already stable → fluency → cautions.
function readingView(rf) {
  if (!rf || rf.state === "unavailable") return { state: rf ? "unavailable" : "none", areas: [], strengths: [], fluency: [], cautions: [] };
  const areas = (rf.improvement_areas || []).map((a, k) => ({
    rank: k + 1, kind: a.kind, band: a.band, text: a.text,
    scope: a.pattern_scope, label: a.pattern_label,
    observations: a.evidence_text, pattern: a.pattern_text || null,
    rate: [a.rate_text, a.counter_text].filter(Boolean).join(" ") || null,
    why: a.order_text || null,
    examples: (a.examples || []).slice(0, 3), counterExamples: (a.counter_examples || []).slice(0, 2),
  }));
  const fluency = [];
  if (rf.fluency) fluency.push({ text: rf.fluency.text, examples: (rf.fluency.examples || []).slice(0, 3) });
  if (rf.fluency_note) fluency.push({ text: rf.fluency_note.text, examples: [] });
  return {
    state: rf.state, areas, noArea: rf.no_area_text || null,
    other: (rf.other_differences && rf.other_differences.note) || null,
    strengths: (rf.strengths || []).map((s) => ({ text: s.text, examples: s.examples || [] })),
    fluency, cautions: (rf.cautions || []).map((c) => c.text),
  };
}

/** The expandable evidence of one action, as lines (measured / counted / inferred / knowledge kept apart). */
function actionEvidenceLines(a) {
  const m = a.why.measures;
  const lines = [
    `Observed: ${m.confident} confident observations in ${m.conf_sessions} sessions, ${m.conf_sentences} sentences` +
      (m.conf_words ? `, ${m.conf_words} words` : "") + (m.supporting ? ` (plus ${m.supporting} ambiguous, as support only)` : "") + ".",
  ];
  if (m.counter) lines.push(`Heard as expected: ${m.counter} other occurrences.`);
  if (m.concentration != null) {
    lines.push(m.concentration >= 0.995 ? "All confident differences of these sounds point this way."
      : `About ${Math.round(m.concentration * 10)} in 10 confident differences of these sounds point this way.`);
  }
  lines.push(`Hypothesis (inferred): ${a.target.hypothesis}`);
  for (const k of a.knowledge_contributions) lines.push(`Knowledge (general, not about you): ${k.text}`);
  for (const d of a.why.decided_by) lines.push(`Chosen over ${d.loser} — ${d.criterion_label}: ${d.winner_value} vs ${d.loser_value}.`);
  if (a.transfer && a.transfer.text) lines.push(`Possible transfer (not measured): ${a.transfer.text}`);
  return lines;
}

// M10: personal progress — the longitudinal result as groups and evidence chains (built only from the M10 result).
const DECISION_LABEL = { CONTINUE_CURRENT_TARGET: "Keep practising", MOVE_TO_FRESH_WORDS: "Move to new words",
  MOVE_TO_NEW_CONTEXT: "Move to another context", CHANGE_PRACTICE_METHOD: "Change the method",
  REDUCE_PRIORITY: "Lower priority", RETIRE: "Stop for now", WATCH_FOR_REGRESSION: "Watching silently",
  INSUFFICIENT_HISTORY: "Watching", INCREASE_CONTEXT_DIFFICULTY: "Harder context" };
const SCOPE_LABEL = { PERSONAL_RECURRING: "Likely personal", CONTEXT_SPECIFIC: "Likely personal · one context",
  WORD_SPECIFIC: "One word", ARTICLE_BOUND: "One text so far", INSUFFICIENT_HISTORY: "" };
const PROGRESS_SCOPE = "Based on all your readings";

function aboutCount(x) { return x == null ? "?" : (x >= 10 ? String(Math.round(x)) : String(Math.round(x * 10) / 10)); }
function oneIn(rate) { return rate ? `about 1 in ${Math.max(1, Math.round(1 / rate))}` : "none"; }

function outcomeText(o) {
  const u = o.unpractised;
  const n = `${u.clear} in ${u.opportunities} chances in new words, where your earlier rate predicts about ${aboutCount(u.expected)}`;
  switch (o.outcome) {
    case "TRANSFER": return `Improvement was observed after practice: ${n}.`;
    case "NO_TRANSFER": return `In the practised material it occurred less often, but in new words it continued: ${n}.`;
    case "CONTINUING": return `Still occurring in new words after practice: ${n}.`;
    case "REVERSE_DIRECTION":
      return `Less often after practice, but the opposite direction rose (${(o.reverse || {}).post_clear} times): possible overcorrection.`;
    default: return `Not enough new readings since this practice to tell (${o.reason}).`;
  }
}

function patternChain(it) {
  const t = it.totals;
  const lines = [];
  const chances = it.kind === "fluency" ? "measurable sentences" : "chances";
  lines.push(`Pattern: ${t.clear} clear` + (t.ambiguous ? ` (plus ${t.ambiguous} ambiguous, support only)` : "") +
    ` in ${t.clear_sessions} reading${t.clear_sessions === 1 ? "" : "s"} of ${t.clear_articles} text${t.clear_articles === 1 ? "" : "s"}` +
    (it.kind === "fluency" ? "" : `, ${t.words} word${t.words === 1 ? "" : "s"}`) + `; ${t.opportunities} ${chances}.`);
  if (it.scope === "CONTEXT_SPECIFIC" && it.context.concentrated.length) {
    lines.push("Where: concentrated " + it.context.concentrated.map((c) =>
      `${c.label} (${c.inside.clear} in ${c.inside.opportunities}, vs ${c.outside.clear} in ${c.outside.opportunities} elsewhere)`).join("; ") + "." +
      (it.context.note ? " " + it.context.note : ""));
  }
  if (it.baseline) lines.push(`Before: ${it.baseline.clear} in ${it.baseline.opportunities} ${chances} (${oneIn(it.baseline.rate)}).`);
  if (it.later && it.later.opportunities) {
    lines.push(`Since then: ${it.later.observed} in ${it.later.opportunities} ${chances} across ${it.later.sessions} readings; ` +
      `your earlier rate predicts about ${aboutCount(it.later.expected)}.`);
  }
  for (const tr of it.transitions) lines.push(`History: ${tr.to.toLowerCase().replace(/_/g, " ")} — ${tr.reason} (${(tr.time || "").slice(0, 10)}).`);
  const ec = it.evidence_classes;
  const other = ["REPEAT", "RETEST", "PRACTICE"].filter((c) => ec[c] && ec[c].readings).map((c) => `${ec[c].readings} ${c.toLowerCase()}`);
  if (other.length) lines.push(`Not used as evidence of change: ${other.join(", ")} readings (re-reading a sentence is not independent evidence).`);
  for (const o of it.outcomes) lines.push(`Practice: ${outcomeText(o)}`);
  lines.push(`Next: ${it.decision.recommendation}`);
  return lines;
}

function progressView(progress, adaptation) {
  if (!progress || !progress.integrity || !progress.integrity.ok) return { state: "unavailable", groups: [] };
  const items = (progress.patterns || []).map((it) => ({ pattern: it.pattern, label: it.label, state: it.state, scope: it.scope,
    scopeLabel: SCOPE_LABEL[it.scope] || "", text: it.text, decision: it.decision.decision,
    decisionLabel: DECISION_LABEL[it.decision.decision], recommendation: it.decision.recommendation,
    active: it.decision.active, chain: patternChain(it), examples: (it.examples || []).slice(0, 3) }));
  const pick = (f) => items.filter(f);
  const groups = [
    { id: "practise", title: "Keep working on", items: pick((x) => x.active) },
    { id: "less_often", title: "Occurring less often", items: pick((x) => !x.active && (x.state === "IMPROVING" || x.decision === "REDUCE_PRIORITY")) },
    { id: "stable", title: "Stable for now", items: pick((x) => x.state === "STABLE" || x.state === "RETIRED") },
    { id: "emerging", title: "New — not enough history yet", items: pick((x) => !x.active && x.state === "EMERGING") },
  ].filter((g) => g.items.length);
  const practice = (progress.practice || []).map((p) => ({ title: p.record.advice.action_text, completion: p.status.completion,
    read: `${p.status.sentences_read} of ${p.status.sentences} practice sentences read`,
    outcomes: p.outcomes.map((o) => outcomeText(o)) }));
  return { state: groups.length ? "patterns" : "none", depth: progress.depth, noise: progress.noise && progress.noise.text,
    engineNote: progress.engine_note, groups, practice, hidden: (adaptation && adaptation.actions || []).filter((a) => !a.prioritised).length };
}

if (typeof module !== "undefined") {
  module.exports = { feedbackLine, displayAttempt, primaryJob, TARGET_TEXT, NOTE_IDENTITY, identityOf, sentencesText, boundaryNote, BOUNDARY_TEXT,
    otherBoundary, fluencyLine, rateText, fluencyItems, fluencyItemText, STRENGTH_TEXT, readingLines, spanText, listenTitle,
    boundaryPlainText, BOUNDARY_CONFIDENCE_TEXT, coachingView, actionEvidenceLines, COACHING_UNAVAILABLE,
    readingView, READING_SCOPE, COACHING_SCOPE, progressView, patternChain, outcomeText, PROGRESS_SCOPE, DECISION_LABEL };
}

// --- browser ----------------------------------------------------------------------------------------
if (typeof document !== "undefined") {
  const buffers = new Map(); // attempt_id → AudioBuffer of its analysis WAV
  let audioCtx = null, source = null;

  async function attemptBuffer(S, attemptId, ref) {
    if (!buffers.has(attemptId)) {
      audioCtx = audioCtx || new AudioContext();
      // M9 examples come from other sessions: the reference names its own session (and audio url)
      const url = (ref && ref.url) || `/api/sessions/${(ref && ref.session_id) || S.sessionId}/attempts/${attemptId}/audio`;
      const data = await (await fetch(url)).arrayBuffer();
      buffers.set(attemptId, await audioCtx.decodeAudioData(data));
    }
    return buffers.get(attemptId);
  }

  /** Play one window of one attempt; the reference identifies exactly what is heard. */
  async function playRef(S, ref, node) {
    S.lastPlayback = ref;
    const buf = await attemptBuffer(S, ref.attempt_id, ref);
    if (audioCtx.state === "suspended") await audioCtx.resume();
    if (source) { try { source.stop(); } catch (e) { /* already stopped */ } }
    document.querySelectorAll(".drawer .playing, .seg.playing").forEach((n) => n.classList.remove("playing"));
    const args = playbackArgs(ref.play_ms, buf.duration);
    source = audioCtx.createBufferSource();
    source.buffer = buf;
    source.connect(audioCtx.destination);
    if (node) node.classList.add("playing");
    source.onended = () => { if (node) node.classList.remove("playing"); };
    source.start(0, args.offset, args.duration);
  }

  function refFor(attempt, job, playMs, kind) {
    return { session_id: attempt.session_id, segment_id: attempt.segment_id, attempt_id: attempt.id,
      job_id: job ? job.id : null, timeline: "analysis_wav", kind, play_ms: playMs };
  }

  function attemptsOf(S, segId) {
    return (S.snap ? S.snap.attempts : []).filter((a) => a.segment_id === segId);
  }

  function attentionText(a, job) {
    if (a.state === "TOO_SHORT") return "Too short to listen to — read it again.";
    if (a.state === "REJECTED") return (a.error && a.error.message) || "This recording could not be used.";
    if (a.state === "INTERRUPTED") return "This recording was interrupted before it was saved.";
    if (a.state === "ANALYSIS_FAILED") return "Listening to this sentence failed.";
    if (job && job.target_confirmation) return TARGET_TEXT[job.target_confirmation.state] || "";
    return "";
  }

  function targetOf(job) {
    return job && job.target_confirmation ? job.target_confirmation.state : null;
  }

  /** Renderers bound to one attempt's analysis: every Listen control plays that attempt's analysis WAV. */
  function renderersFor(S, a, job) {
    return createEvidenceRenderers({
      el: domEl, play: (playMs, node, kind) => playRef(S, refFor(a, job, playMs, kind || "sound"), node),
      playWhole: () => playRef(S, refFor(a, job, [0, a.audio.analysis_duration_ms], "segment"), null),
    });
  }

  const views = new Map(); // job_id → promise of its analysis view (fetched once)
  function viewOf(S, a, job) {
    if (!views.has(job.id)) {
      views.set(job.id, fetch(`/api/sessions/${S.sessionId}/attempts/${a.id}`).then((r) => r.json())
        .then((d) => d.views[job.id]).catch((e) => { views.delete(job.id); throw e; }));
    }
    return views.get(job.id);
  }

  /** The attempt whose evidence annotates a sentence, or null (not analysed / not confirmed as this sentence). */
  function annotationSource(S, segId) {
    const a = displayAttempt(attemptsOf(S, segId));
    if (!a || a.state !== "ANALYZED") return null;
    const job = primaryJob(a, S.snap.jobs);
    if (!job || job.state !== "SUCCEEDED") return null;
    if (identityOf(a, job).feedback !== "shown") return null; // a different sentence, or feedback withheld
    return { a, job };
  }

  /**
   * The article sentence itself becomes the feedback: its original text (capitals,
   * punctuation, order) is kept, and each word the analysis has a word for is wrapped and
   * underlined by its M3 category. Unanalysed (or recording) sentences stay plain text.
   */
  async function annotate(S, segId, recording) {
    const span = S.segEls[segId];
    const seg = S.segById[segId];
    if (!span || !S.snap) return;
    const src = recording ? null : annotationSource(S, segId);
    const key = src ? `${src.job.id}|${src.a.user_disposition || ""}` : "";
    if ((span.dataset.annotation || "") === key) return;
    span.dataset.annotation = key;
    if (S.wordSel && S.wordSel.segId === segId) closeWord(S);
    if (!src) { span.textContent = seg.text; span.classList.remove("annotated"); updateLegend(); return; }
    let view;
    try { view = await viewOf(S, src.a, src.job); } catch (e) { span.dataset.annotation = ""; return; }
    if (span.dataset.annotation !== key) return; // superseded while loading
    if (!view || view.state !== "ok") { span.textContent = seg.text; span.classList.remove("annotated"); updateLegend(); return; }
    const words = view.words || [];
    span.textContent = "";
    span.classList.add("annotated");
    span.dataset.attemptId = src.a.id;
    span.dataset.jobId = src.job.id;
    for (const piece of alignWords(seg.text, words)) {
      if (piece.wordIndex === undefined) { span.append(document.createTextNode(piece.text)); continue; }
      const w = words[piece.wordIndex];
      const node = domEl("span", { class: "aw cat-" + w.status, "data-word": String(w.index), role: "button", tabindex: "0",
        "aria-expanded": "false", title: w.word + ": " + wordTitle(w), text: piece.text });
      const open = (e) => { e.stopPropagation(); openWord(S, segId, src.a, src.job, w, node); };
      node.addEventListener("click", open);
      node.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); open(e); } });
      span.append(node);
    }
    updateLegend();
  }

  /** The container after a sentence's note: the word panel, then the Details block (display: contents). */
  function extrasFor(S, segId) {
    S.extras = S.extras || {};
    let c = S.extras[segId];
    if (!c) {
      c = document.createElement("div");
      c.className = "seg-extras";
      c.dataset.extras = segId;
      S.markEls[segId].after(c);
      S.extras[segId] = c;
    }
    return c;
  }

  function closeWord(S) {
    if (!S.wordSel) return;
    S.wordSel.node.classList.remove("selected");
    S.wordSel.node.setAttribute("aria-expanded", "false");
    if (S.wordPanel) S.wordPanel.hidden = true;
    S.wordSel = null;
  }

  /** Click on an annotated word: the MVP sound-level detail for that word (one panel, it moves). */
  function openWord(S, segId, a, job, w, node) {
    const el = domEl;
    if (S.wordSel && S.wordSel.node === node) { closeWord(S); return; }
    closeWord(S);
    const panel = S.wordPanel || (S.wordPanel = el("div", { class: "word-panel" }));
    panel.innerHTML = "";
    Object.assign(panel.dataset, { seg: segId, word: String(w.index), attemptId: a.id, jobId: job.id, timeline: "analysis_wav" });
    renderersFor(S, a, job).wordDetail(panel, w, { anchor: node });
    panel.append(el("div", { class: "line panel-footer" }, [
      el("button", { type: "button", class: "more-about",
        text: "Patterns, connected speech and comparison for this sentence",
        onclick: () => { const d = S.drawers[segId]; if (!d || d.hidden) toggle(S, segId); } }),
      el("button", { type: "button", class: "close-word", text: "Close", onclick: () => closeWord(S) }),
    ]));
    extrasFor(S, segId).prepend(panel);
    panel.hidden = false;
    node.classList.add("selected");
    node.setAttribute("aria-expanded", "true");
    S.wordSel = { segId, node };
  }

  /** A compact legend in the rail, with only the categories present in the annotated article. */
  function updateLegend() {
    const box = document.getElementById("legend");
    if (!box) return;
    const present = new Set([...document.querySelectorAll("#article-body .aw")].map((n) => n.className.match(/cat-(\w+)/)[1]));
    box.innerHTML = "";
    box.hidden = !present.size;
    for (const c of ["expected", "different", "unclear", "not_detected", "not_interpreted"]) {
      if (present.has(c)) box.append(domEl("span", { class: "legend-item cat-" + c, text: CATEGORY_TITLES[c] }));
    }
  }

  /**
   * The inline note right after a sentence: the concise feedback ("12 things to notice · 7 to
   * compare"), "Details" (M4 patterns, M5 connected speech, comparison) and "Read again". It is
   * secondary: the annotated words in the article are the feedback.
   */
  function renderNote(S, segId, note, info) {
    const el = domEl;
    const all = attemptsOf(S, segId);
    note.innerHTML = "";
    if (info.recording || (!info.pending && (!all.length || info.state === "UNREAD"))) { note.hidden = true; return; }
    note.hidden = false;
    if (info.pending) {
      note.className = "note processing";
      note.append(el("span", { class: "compact", text: "listening…" }));
      return;
    }
    const a = displayAttempt(all);
    const job = primaryJob(a, S.snap.jobs);
    const idn = identityOf(a, job);
    const tc = idn.identity;
    let text, cls = "note";
    if (a.state === "ANALYZED" && idn.feedback === "hidden_identity") { text = NOTE_IDENTITY.MISMATCH; cls += " attention"; }
    else if (a.state === "ANALYZED") {
      text = feedbackLine(job && job.feedback);
      const fl = fluencyLine(job && job.fluency);
      if (fl) text += " · " + fl;
      if (a.status && a.status.analysis_note) text += " · pronunciation evidence uncertain";
      if (NOTE_IDENTITY[tc] && !idn.kept && idn.feedback === "shown") {
        text += " · " + NOTE_IDENTITY[tc];
        if (tc === "AMBIGUOUS") cls += " attention";
      }
    } else { text = attentionText(a, job) || "needs attention"; cls += " attention"; }
    note.className = cls;
    const open = !!(S.drawers[segId] && !S.drawers[segId].hidden);
    // each phrase stays together; a line may only break at " · "
    const compact = el("span", { class: "compact" });
    text.split(" · ").forEach((part, i) => { if (i) compact.append(" · "); compact.append(el("span", { class: "phrase", text: part })); });
    note.append(compact, " ");
    note.append(
      el("button", { type: "button", class: "details-toggle", "aria-expanded": String(open),
        text: (open ? "▾" : "▸") + " Details", onclick: (e) => { e.stopPropagation(); toggle(S, segId); } }), " ",
      el("button", { type: "button", class: "read-again", title: "Read this sentence again (new recording)",
        "aria-label": "Read again", text: "↻", onclick: (e) => { e.stopPropagation(); window.__readerApi.readAgain(segId); } }));
    const bn = a.state === "ANALYZED" && idn.feedback !== "hidden_identity" ? boundaryNote(job && job.boundary) : null;
    if (bn) {
      // withheld but the reading matches: say so ("appears to match … couldn't determine where it ended")
      const lineText = bn.withheld && (tc === "MATCH" || tc === "LIKELY_MATCH") ? idn.message : bn.line;
      const line = el("span", { class: "boundary-note", "data-boundary": bn.state, title: bn.sub || lineText },
        [el("span", { class: "boundary-line", text: lineText })]);
      if (bn.after && bn.after.play) {
        line.append(" ", el("button", { type: "button", class: "listen-overflow",
          "aria-label": "Listen to the continued speech", text: "▶ Listen",
          onclick: (e) => { e.stopPropagation(); playRef(S, bn.after.play, e.target); } }));
      }
      if (bn.sub) line.append(" ", el("span", { class: "boundary-sub", text: bn.sub }));
      note.append(" ", line);
    }
  }

  /** The Details block: after its sentence (and its word panel), inside the paragraph, subordinate to the article. */
  function drawerFor(S, segId) {
    let d = S.drawers[segId];
    if (!d) {
      d = document.createElement("div");
      d.className = "drawer inline-details";
      d.dataset.drawer = segId;
      d.hidden = true;
      extrasFor(S, segId).append(d);
      S.drawers[segId] = d;
    }
    return d;
  }

  /** What a drawer shows; an open drawer is rebuilt only from a pending/attention state. */
  function signature(a, job) {
    return [a.id, a.state, a.user_disposition || "", job ? job.id : "", job ? job.state : "", targetOf(job) || "",
      (a.status && a.status.identity) || ""].join("|");
  }

  function refreshNotes(S) { if (window.__readerApi && window.__readerApi.refresh) window.__readerApi.refresh(); }

  /** "Details": open (rendering the latest attempt, or `attemptId`) or close the block after the sentence. */
  function toggle(S, segId, attemptId) {
    const d = S.drawers[segId];
    if (!d || d.hidden || attemptId) renderDrawer(S, segId, attemptId);
    else d.hidden = true;
    refreshNotes(S);
  }

  function renderDrawer(S, segId, attemptId) {
    const el = domEl;
    const d = drawerFor(S, segId);
    d.hidden = false;
    d.innerHTML = "";
    const all = attemptsOf(S, segId);
    const a = all.find((x) => x.id === attemptId) || displayAttempt(all);
    if (!a) { d.hidden = true; return; }
    const job = primaryJob(a, S.snap.jobs);
    d.dataset.attemptId = a.id;
    d.dataset.jobId = job ? job.id : "";
    d.dataset.timeline = "analysis_wav";
    d.dataset.sig = signature(a, job);

    const footer = el("div", { class: "line drawer-footer" });
    if (a.audio && a.audio.analysis) {
      footer.append(el("button", { type: "button", class: "listen-attempt", text: "▶ Listen to this recording",
        onclick: (ev) => playRef(S, refFor(a, job, [0, a.audio.analysis_duration_ms], "segment"), ev.target) }));
    }
    if (all.length > 1) {
      const sel = el("select", { "aria-label": "Recording" });
      for (const x of all) sel.append(el("option", { value: x.id, text: `Recording ${x.attempt_number} of ${all.length}` + (x.user_disposition === "discarded" ? " (discarded)" : "") }));
      sel.value = a.id;
      sel.addEventListener("change", () => toggle(S, segId, sel.value));
      footer.append(sel);
    }

    if (a.state !== "ANALYZED") {
      d.append(el("p", { class: "muted", text: ["QUEUED", "ANALYZING", "RECORDED"].includes(a.state)
        ? "Listening to this sentence…" : attentionText(a, job) }));
      if (a.state === "ANALYSIS_FAILED" && job) {
        d.append(el("button", { type: "button", text: "Try again", onclick: () => window.__readerApi.retry(a, job) }));
      }
      d.append(footer);
      return;
    }

    const idn = identityOf(a, job);
    const target = idn.identity || "NOT_CHECKED";
    if (idn.kept && ["AMBIGUOUS", "LIKELY_MATCH", "MISMATCH"].includes(target)) {
      const kn = el("p", { class: "muted small kept-note", "data-target": target, text: target === "MISMATCH"
        ? "Kept — the recording is preserved. Pronunciation feedback stays hidden because it appears to contain a different sentence."
        : "Kept — you confirmed this is the sentence; it is included in your reading summary." });
      if (target === "MISMATCH") {
        kn.append(" ", el("button", { type: "button", class: "rerecord-btn", text: "Re-record",
          onclick: () => window.__readerApi.rerecord(a, segId) }));
      }
      d.append(kn);
    }
    if (idn.needsDecision) {
      const ask = el("div", { class: "target-ask", "data-target": target }, [el("p", { text: idn.message })]);
      ask.append(
        el("button", { type: "button", class: "keep-btn", text: "Keep this recording",
          onclick: () => {
            window.__readerApi.disposition(a, "kept", segId);
            // shown now (on the snapshot currently displayed, which may be newer than this drawer);
            // the server's copy follows on the next poll
            a.user_disposition = "kept";
            const current = S.snap.attempts.find((x) => x.id === a.id);
            if (current) current.user_disposition = "kept";
            renderDrawer(S, segId, a.id);
            annotate(S, segId, false);
            refreshNotes(S);
          } }),
        el("button", { type: "button", class: "rerecord-btn", text: "Re-record",
          onclick: () => window.__readerApi.rerecord(a, segId) }),
      );
      d.append(ask);
    } else if (target === "MATCH" && (idn.feedback === "withheld_boundary" || idn.feedback === "withheld_containment")) {
      d.append(el("p", { class: "identity-note", "data-target": target, text: idn.message }));
    }
    if (a.status && a.status.analysis_note) d.append(el("p", { class: "muted analysis-note", text: a.status.analysis_note }));
    // a different sentence: never pronunciation feedback, kept or not (Keep preserves; it does not unlock)
    if (idn.feedback === "hidden_identity") { d.append(footer); return; }
    const body = el("div", { class: "drawer-body" }, [el("p", { class: "muted", text: "Loading…" })]);
    d.append(body, footer);
    fillDrawer(S, body, a, job);
  }

  /** Details: the deeper layers for one sentence — M4 patterns and practice, M5 connected speech, comparison. */
  async function fillDrawer(S, body, a, job) {
    const el = domEl;
    const view = await viewOf(S, a, job);
    body.innerHTML = "";
    const R = renderersFor(S, a, job);
    if (view.state !== "ok") body.append(el("p", { class: "muted", text: view.message || "No speech sounds were detected." }));

    // M4: patterns and practice
    const coach = view.coach;
    const more = el("details", { class: "evidence" }, [el("summary", { text: "Patterns and practice" })]);
    if (coach && coach.state === "ok") {
      const obsById = Object.fromEntries(coach.observations.map((o) => [o.id, o]));
      const patById = Object.fromEntries(coach.patterns.map((p) => [p.id, p]));
      const tgt = Object.fromEntries(coach.practice_targets.map((t) => [t.pattern_id, t]));
      for (const g of coach.groups) {
        const sec = el("div", { class: "coach-group", "data-group": g.id }, [el("h4", { text: g.title })]);
        for (const pid of g.pattern_ids) sec.append(R.patternCard(patById[pid], obsById, tgt[pid]));
        more.append(sec);
      }
      if (!coach.groups.length) more.append(el("p", { class: "muted", text: "Every interpretable sound was consistent with the expected sound." }));
      more.append(el("p", { class: "muted small", text: coach.coverage.note }));
    }
    body.append(more);

    // M5: connected speech, a separate section
    const red = view.reduction;
    if (red && red.state === "ok" && red.candidates.length) {
      const byId = Object.fromEntries(red.candidates.map((c) => [c.id, c]));
      const listed = red.groups.filter((g) => g.id !== "insufficient_evidence").reduce((n, g) => n + g.candidate_ids.length, 0);
      const sec = el("details", { class: "connected-speech" }, [el("summary", { text: `Connected speech (${listed})` })]);
      for (const g of red.groups) {
        const holder = g.id === "insufficient_evidence" ? el("details", {}, [el("summary", { text: g.title + ` (${g.candidate_ids.length})` })]) : sec;
        for (const id of g.candidate_ids) holder.append(R.reductionCard(byId[id]));
        if (holder !== sec) sec.append(holder);
      }
      body.append(sec);
    }

    body.append(fluencySection(S, a, job, view.fluency));
    body.append(boundarySection(S, job, otherBoundary(a, S.snap.jobs)));

    const cmpBox = el("div", { class: "compare" });
    const btn = el("button", { type: "button", class: "compare-attempt", text: "Compare with the other listening model",
      onclick: () => window.__readerApi.compare(a, cmpBox, btn, R) });
    body.append(el("div", { class: "line" }, [btn, el("span", { class: "muted small",
      text: "Both models share one acoustic model; differences are shown, not resolved." })]), cmpBox);
    body.append(el("p", { class: "muted small", text: `Evidence: ${job.engine_id} · recording ${a.id.slice(0, 8)} · analysis ${job.id.slice(0, 8)}` }));
  }

  /** Details → Fluency (M8): the summary, things to notice with Listen, the rate; evidence on request. */
  function fluencySection(S, a, job, fl) {
    const el = domEl;
    const items = fluencyItems(fl);
    const sec = el("details", { class: "fluency", "data-state": fl ? fl.state : "none" },
      [el("summary", { text: `Fluency (${items.notice.length})` })]);
    if (!fl || fl.state !== "ok") {
      sec.append(el("p", { class: "muted", text: (fl && fl.message) || "No fluency evidence for this recording." }));
      return sec;
    }
    sec.append(el("p", { class: "fluency-summary", text: fl.summary.text }));
    const listen = (o, label) => el("button", { type: "button", class: "listen-fluency", "data-id": o.id, text: label || "▶ Listen",
      title: listenTitle(o), "aria-label": `Listen: ${o.label}, ${listenTitle(o)}`,
      onclick: (ev) => playRef(S, refFor(a, job, o.playback.play_ms, "fluency:" + o.type), ev.target) });
    const at = (o) => el("span", { class: "muted small fl-time", text: `at ${spanText(o)}` });
    const list = el("ul", { class: "fluency-list" });
    for (const o of items.notice) {
      list.append(el("li", { class: "fluency-item", "data-type": o.type }, [
        el("span", { class: "fl-label", text: o.label }), " ",
        el("span", { class: "fl-observed", text: fluencyItemText(o) }), " ", at(o), " ",
        listen(o), " ", el("span", { class: "muted small", text: STRENGTH_TEXT[o.strength] || "" })]));
    }
    if (items.notice.length) sec.append(list);
    if (items.notice.length) sec.append(el("p", { class: "muted small fluency-listen-note",
      text: "Each ▶ Listen plays the moment with a little context before and after it; the exact times are shown." }));
    sec.append(el("p", { class: "fluency-rate", text: rateText(fl.metrics) }));
    if (items.other.length) {
      const other = el("details", { class: "fluency-other" }, [el("summary", { text: `Other timing (${items.other.length})` })]);
      const ul = el("ul", { class: "fluency-list" });
      for (const o of items.other) ul.append(el("li", { class: "fluency-item", "data-type": o.type }, [
        el("span", { class: "fl-label", text: o.label }), " ", el("span", { class: "fl-observed", text: fluencyItemText(o) }), " ", at(o), " ", listen(o)]));
      other.append(ul);
      sec.append(other);
    }
    for (const cs of fl.continued_speech || []) {
      sec.append(el("p", { class: "muted small fluency-continued" }, [
        `Continued speech after the sentence (not part of this analysis): ${(cs.duration_ms / 1000).toFixed(1)} s. `,
        el("button", { type: "button", class: "listen-fluency-continued", text: "▶ Listen",
          onclick: (ev) => playRef(S, refFor(a, job, cs.playback.play_ms, cs.kind), ev.target) })]));
    }
    const tech = el("details", { class: "fluency-evidence" }, [el("summary", { text: "Evidence" })]);
    for (const o of fl.observations) {
      const ul = el("ul", { class: "small" });
      for (const e of o.evidence) ul.append(el("li", { text: e.detail }));
      tech.append(el("p", { class: "small", text: `${o.label} · ${(o.start_ms / 1000).toFixed(2)}–${(o.end_ms / 1000).toFixed(2)} s · ${STRENGTH_TEXT[o.strength]} · ${o.engine}` }),
        ul, el("p", { class: "small muted", text: o.interpretation }));
    }
    for (const c of fl.caveats || []) tech.append(el("p", { class: "small muted", text: c }));
    sec.append(tech);
    return sec;
  }

  /** The other listening model's fluency evidence, side by side; agreement is not independent confirmation. */
  function renderFluencyComparison(box, cmp) {
    const el = domEl;
    const sec = el("div", { class: "fluency-compare" }, [el("h4", { text: "Fluency, by listening model" })]);
    if (!cmp.rows.length) sec.append(el("p", { class: "muted small", text: "Neither model shows a fluency observation to compare." }));
    for (const r of cmp.rows) {
      const o = r.first || r.second;
      sec.append(el("p", { class: "small", "data-agreement": r.agreement,
        text: `${o.label} · ${fluencyItemText(o)} — ${cmp.agreement_text[r.agreement]}` }));
    }
    sec.append(el("p", { class: "muted small", text: cmp.shared_model_note }));
    box.append(sec);
  }

  /** Details → Sentence boundary: what was analysed, Listen to each part, and the technical evidence. */
  function boundarySection(S, job, other) {
    const el = domEl;
    const b = job && job.boundary;
    const sec = el("details", { class: "boundary", "data-boundary": b ? b.state : "NONE" },
      [el("summary", { text: "Sentence boundary" })]);
    if (!b) { sec.append(el("p", { class: "muted small", text: "Not checked for this recording." })); return sec; }
    const plain = {
      TARGET_ONLY: "Only this sentence was heard in this recording; all of it was analysed.",
      TARGET_PLUS_OVERFLOW: "Speech continued after this sentence. Only the sentence was analysed; the continuation is kept with the recording.",
      BOUNDARY_UNCERTAIN: "Where this sentence ends is uncertain. Only the part up to the boundary was analysed; the rest is kept with the recording.",
      NO_RELIABLE_BOUNDARY: "The end of the sentence could not be checked; the whole recording was analysed.",
    }[b.state];
    sec.append(el("p", { class: "boundary-plain", "data-withheld": b.withheld_reason || "", text: boundaryPlainText(b, plain) }));
    const names = { target: "▶ Listen to the sentence", overflow: "▶ Listen to the continued speech", uncertain: "▶ Listen to the uncertain part" };
    const line = el("div", { class: "line" });
    for (const r of b.regions || []) {
      if (b.regions.length < 2 || !r.play) continue;
      line.append(el("button", { type: "button", class: "listen-region", "data-region": r.kind, text: names[r.kind],
        onclick: (ev) => playRef(S, r.play, ev.target) }), " ");
    }
    if (line.childNodes.length) sec.append(line);
    const ev = b.evidence || {};
    const ms = (v) => (typeof v === "number" ? `${Math.round(v)} ms` : "—");
    const rows = [
      ["Decision", `${b.state} (${b.engine}${b.source === "primary" ? ", the first model's boundary" : ""})`],
      ["Boundary", `${ms(b.cut_ms)} of ${ms(b.duration_ms)}`],
      ["Last sound of the sentence", `${ms(ev.last_target_ms)} (“${ev.last_target_word || "—"}”)`],
      ["Final word decoded", ev.final_word_decoded ? `${ev.final_word_decoded} sounds of “${ev.final_word}”` : "—"],
      ["Sounds decoded after it", ev.post_phones === undefined ? "—" : String(ev.post_phones)],
      ["Speech-like sound after it", ms(ev.speech_after_ms)],
      ["Gap before the continuation", ms(ev.gap_ms)],
      ["How the end was found", BOUNDARY_CONFIDENCE_TEXT[b.boundary_confidence] || "—"],
      ["Pronunciation evidence", b.analysis && b.analysis.cost_per_sound != null
        ? `${b.analysis.state === "ok" ? "clear enough" : "uncertain"} (${b.analysis.cost_per_sound.toFixed(2)} differences per expected sound, ${b.analysis.engine})` : "—"],
    ];
    const tech = el("details", { class: "boundary-evidence" }, [el("summary", { text: "Evidence" })]);
    const dl = el("dl", { class: "small" });
    for (const [k, v] of rows) dl.append(el("dt", { text: k }), el("dd", { text: v }));
    tech.append(dl);
    const reasons = el("ul", { class: "small" });
    for (const r of b.reasons || []) reasons.append(el("li", { text: r }));
    tech.append(reasons);
    if (other) {
      tech.append(el("p", { class: "small other-boundary", text: `${other.engine}, on its own: ${other.state} at ${ms(other.cut_ms)}`
        + (other.differs ? " — the models differ here." : " — the same boundary.") + " " + other.note }));
    }
    sec.append(tech);
    return sec;
  }

  function update(S, snap) {
    // Open Details blocks are left as they are while being read, except a block still
    // showing "listening…" or an attention state, which is refreshed when its attempt changes.
    for (const [segId, d] of Object.entries(S.drawers)) {
      if (d.hidden || d.querySelector(".drawer-body")) continue;
      const all = attemptsOf(S, segId);
      const a = all.find((x) => x.id === d.dataset.attemptId) || displayAttempt(all);
      if (a && d.dataset.sig !== signature(a, primaryJob(a, snap.jobs))) renderDrawer(S, segId, a.id);
    }
    const rail = document.getElementById("rail-feedback");
    const done = snap.attempts.filter((a) => a.state === "ANALYZED");
    const last = done[done.length - 1];
    rail.innerHTML = "";
    if (last) {
      const job = primaryJob(last, snap.jobs);
      const idx = S.segments.findIndex((s) => s.id === last.segment_id) + 1;
      const idn = identityOf(last, job);
      const line = idn.needsDecision ? `${NOTE_IDENTITY[idn.identity]} — open to keep or re-record`
        : feedbackLine(job && job.feedback);
      rail.append(domEl("button", { type: "button", class: "rail-line", text: `Sentence ${idx}: ${line}`,
        onclick: () => S.segEls[last.segment_id].scrollIntoView({ block: "center", behavior: "smooth" }) }));
    }
  }

  // --- reading summary (single session) ---------------------------------------------------
  function exampleButtons(S, examples) {
    const el = domEl;
    const box = el("div", { class: "row examples" });
    for (const ref of examples) {
      box.append(el("button", { type: "button", class: "play-example", "data-attempt": ref.attempt_id,
        text: `▶ Sentence ${ref.sentence} · ${ref.word}`, onclick: (ev) => playRef(S, ref, ev.target) }));
    }
    return box;
  }

  /** M9 actions (0–3): concise card per action; evidence on request; emerging items never shown here. */
  function renderCoaching(S, box, coaching) {
    const el = domEl;
    const v = coachingView(coaching);
    box.querySelectorAll(".coach-actions, .coach-none").forEach((n) => n.remove());
    if (v.state !== "actions") {
      if (v.message) box.append(el("div", { class: "coach-none" }, [el("p", { text: v.message }),
        ...(v.help ? [el("p", { class: "muted small", text: v.help })] : [])]));
      return v;
    }
    const list = el("ol", { class: "coach-actions" });
    v.actions.forEach((a, i) => {
      const raw = coaching.actions[i];
      const listen = (ref, label) => el("button", { type: "button", class: "play-example coach-listen", text: `▶ ${label}`,
        title: ref.span_ms ? listenTitle({ start_ms: ref.span_ms[0], end_ms: ref.span_ms[1], playback: { context_ms: ref.context_ms } }) : "",
        onclick: (ev) => playRef(S, ref, ev.target) });
      const steps = el("ol", { class: "coach-steps small" });
      a.steps.forEach((t) => steps.append(el("li", { text: t })));
      const evidence = el("details", { class: "coach-evidence" }, [el("summary", { text: "Evidence" })]);
      const ul = el("ul", { class: "small" });
      actionEvidenceLines(raw).forEach((t) => ul.append(el("li", { text: t })));
      evidence.append(ul);
      const counter = raw.practice.counter_examples.slice(0, 3);
      if (counter.length) evidence.append(el("p", { class: "small" }, ["Heard as expected: ",
        ...counter.map((r) => listen(r, r.word || "example"))]));
      const guidance = raw.practice.guidance;
      if (guidance.length) evidence.append(el("p", { class: "small muted", text: `${guidance.map((g) => g.text).join(" ")} (${raw.practice.guidance_note})` }));
      const readBtn = el("button", { type: "button", class: "coach-read", text: "Read these now",
        onclick: () => window.__readerApi && window.__readerApi.startPractice(a.retest.map((r) => r.text), a.title,
          raw.target.target_id) });
      list.append(el("li", { class: "coach-action", "data-target": raw.target.target_id, "data-kind": raw.target.kind }, [
        el("div", { class: "row" }, [el("strong", { class: "coach-title", text: a.title }),
          el("span", { class: "muted small", text: `about ${a.minutes} min` })]),
        el("p", { class: "coach-why", text: a.why }),
        el("details", { class: "coach-practise" }, [el("summary", { text: "Practise" }), steps]),
        el("p", { class: "coach-examples small" }, ["Your examples: ", ...a.examples.map((e) => listen(e.ref, e.label))]),
        el("div", { class: "coach-retest small" }, [el("span", { text: "Retest: " }),
          ...a.retest.map((r) => listen(r.ref, r.text.length > 48 ? r.text.slice(0, 46) + "…" : r.text)), readBtn]),
        evidence,
      ]));
    });
    box.append(list);
    return v;
  }

  /** M10: M9's actions with their history; an action that is stable for now in the history is not a priority. */
  function annotateCoaching(box, adaptation) {
    const el = domEl;
    if (!adaptation) return;
    const lis = [...box.querySelectorAll("li.coach-action")];
    const stable = [];
    adaptation.actions.forEach((a) => {
      const li = lis[a.index];
      if (!li || !a.history.length) return;
      li.insertBefore(el("p", { class: "small coach-history", text: "Your history: " + a.history.map((h) => h.text).join(" ") }),
        li.querySelector(".coach-practise"));
      if (!a.prioritised) { li.classList.add("coach-deprioritised"); stable.push(li); }
    });
    if (stable.length) {
      const d = el("details", { class: "coach-stable" }, [el("summary", { text: `Stable for now in your history (${stable.length})` })]);
      stable.forEach((li) => d.append(li));
      box.append(d);
    }
  }

  /** M10: "Your patterns over time" — groups of patterns, each with its evidence chain and exact playback. */
  function renderProgress(S, box, progress, adaptation) {
    const el = domEl;
    const v = progressView(progress, adaptation);
    box.innerHTML = "";
    if (v.state === "unavailable") { box.append(el("p", { class: "muted", text: "Your history is not available right now." })); return v; }
    if (v.depth) box.append(el("p", { class: "progress-depth", text: v.depth }));
    if (v.state === "none") box.append(el("p", { class: "muted", text: "No pattern has enough history yet to say how it is changing." }));
    for (const g of v.groups) {
      const sec = el("div", { class: "progress-group", "data-group": g.id }, [el("h3", { text: g.title })]);
      const ul = el("ul", { class: "progress-items" });
      for (const it of g.items) {
        const chain = el("details", { class: "progress-chain" }, [el("summary", { text: "How Pronounce knows this" })]);
        const cl = el("ul", { class: "small" });
        it.chain.forEach((line) => cl.append(el("li", { text: line })));
        chain.append(cl);
        if (it.examples.length) chain.append(el("p", { class: "small" }, ["Listen: ", ...it.examples.map((r) =>
          el("button", { type: "button", class: "play-example progress-listen", text: `▶ ${r.word || "moment"}`,
            onclick: (ev) => playRef(S, r, ev.target) }))]));
        ul.append(el("li", { class: "progress-item", "data-pattern": it.pattern, "data-state": it.state, "data-decision": it.decision }, [
          el("div", { class: "row" }, [el("strong", { class: "ipa", text: it.label }),
            ...(it.scopeLabel ? [el("span", { class: "chip", text: it.scopeLabel })] : []),
            el("span", { class: "chip progress-decision", text: it.decisionLabel })]),
          el("p", { class: "progress-text", text: it.text }),
          el("p", { class: "small progress-next", text: it.recommendation }),
          chain]));
      }
      sec.append(ul);
      box.append(sec);
    }
    if (v.practice.length) {
      const pr = el("details", { class: "progress-practice" }, [el("summary", { text: `Your practice (${v.practice.length})` })]);
      const ul = el("ul", { class: "small" });
      v.practice.forEach((p) => ul.append(el("li", { text: `${p.title} — ${p.read}. ` + (p.outcomes.join(" ") || "") })));
      pr.append(ul);
      box.append(pr);
    }
    const how = el("details", { class: "progress-how" }, [el("summary", { text: "How to read this" })]);
    [v.noise, v.engineNote, "Only the first reading of each sentence counts as evidence of change; re-reads, retests and practice are shown but never counted. Nothing here is a score."]
      .filter(Boolean).forEach((t) => how.append(el("p", { class: "small muted", text: t })));
    box.append(how);
    return v;
  }

  function renderSummary(S, snap) {
    const el = domEl;
    const box = document.getElementById("summary");
    const state = snap.session.state;
    const sum = snap.summary;
    if (!sum && state !== "FINISHED") { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = "";
    box.append(el("h2", { text: "Your reading" }));
    if (!sum) {
      const waiting = snap.attempts.filter((a) => ["RECORDED", "QUEUED", "ANALYZING", "CAPTURING"].includes(a.state)).length;
      box.append(el("p", { class: "muted", text: `Listening to the last ${waiting || ""} sentence${waiting === 1 ? "" : "s"}… the summary follows.` }));
      return;
    }
    if (sum.stale) box.append(el("p", { class: "notice", text: "You read more after this summary. Finish reading again to update it." }));
    const cov = sum.coverage;
    const [readLine, feedbackLine2] = readingLines(cov);
    box.append(el("p", { class: "lead", text: readLine + "." }));
    box.append(el("p", { class: "summary-feedback", text: feedbackLine2 + "." }));
    if (cov.sounds) {
      box.append(el("p", { class: "muted small", text: `${cov.consistent_with_expected} of ${cov.sounds} sounds consistent with the expected sound.` }));
    }
    if (cov.not_included.length) {
      box.append(el("p", { class: "muted small", text: "Not in the feedback: " + cov.not_included.map((x) => `sentence ${x.sentence} (${x.reason})`).join(", ") }));
    }
    if (snap.reading_feedback) {
      const rv = readingView(snap.reading_feedback);
      const sec = el("section", { class: "summary-group this-reading", "data-group": "this_reading" },
        [el("h3", { text: "This reading" }), el("p", { class: "muted small scope", text: READING_SCOPE })]);
      const listen = (ref, label) => el("button", { type: "button", class: "play-example reading-listen", text: `▶ ${label}`,
        onclick: (ev) => playRef(S, ref, ev.target) });
      if (rv.state === "unavailable") sec.append(el("p", { class: "muted", text: "A description of this reading is not available." }));
      const sub = (id, title) => el("div", { class: "reading-sub", "data-sub": id }, [el("h4", { text: title })]);
      const exampleLabel = (e) => (e.word || e.label || "listen") + (e.evidence === "ambiguous" ? " (ambiguous)" : "");
      if (rv.state === "feedback") {
        const areas = sub("areas", "Major improvement areas");
        if (rv.areas.length) {
          const ol = el("ol", { class: "reading-areas" });
          for (const a of rv.areas) {
            ol.append(el("li", { class: "reading-area", "data-kind": a.kind, "data-band": a.band }, [
              el("span", { class: "reading-headline", text: a.text }), " ",
              el("span", { class: "chip reading-scope", "data-scope": a.scope, text: a.label }),
              el("p", { class: "small reading-observations", text: a.observations }),
              ...(a.pattern ? [el("p", { class: "small reading-pattern", text: a.pattern })] : []),
              ...(a.rate ? [el("p", { class: "muted small reading-rate", text: a.rate })] : []),
              ...(a.why ? [el("p", { class: "muted small reading-why", text: a.why })] : []),
              el("div", { class: "reading-listen-row" }, [
                ...a.examples.map((e) => listen(e, exampleLabel(e))),
                ...(a.counterExamples.length ? [el("span", { class: "muted small", text: " heard as expected: " })] : []),
                ...a.counterExamples.map((e) => listen(e, e.word || "listen")),
              ]),
            ]));
          }
          areas.append(ol);
        } else if (rv.noArea) {
          areas.append(el("p", { class: "reading-no-area", text: rv.noArea }));
        }
        if (rv.other) areas.append(el("p", { class: "muted small", text: rv.other }));
        sec.append(areas);
      }
      if (rv.strengths.length) {
        const st = sub("strengths", "Already stable in this reading");
        const ul = el("ul", { class: "reading-strengths" });
        for (const s of rv.strengths) ul.append(el("li", { class: "reading-strength" }, [el("span", { text: s.text }), " ",
          ...s.examples.map((e) => listen(e, e.word || "listen"))]));
        st.append(ul);
        sec.append(st);
      }
      if (rv.fluency.length) {
        const fl = sub("fluency", "Fluency");
        for (const f of rv.fluency) fl.append(el("p", { class: "reading-fluency" }, [el("span", { text: f.text }), " ",
          ...f.examples.map((e) => listen(e, e.word || e.label || "listen"))]));
        sec.append(fl);
      }
      if (rv.cautions.length) {
        const ca = sub("cautions", "Cautions");
        for (const c of rv.cautions) ca.append(el("p", { class: "muted small reading-caution", text: c }));
        sec.append(ca);
      }
      box.append(sec);
    }
    if (snap.coaching) {
      const practice = el("section", { class: "summary-group practice-now", "data-group": "practice_now" },
        [el("h3", { text: "What to practise now" }), el("p", { class: "muted small scope", text: COACHING_SCOPE })]);
      renderCoaching(S, practice, snap.coaching);
      box.append(practice);
    }
    const byId = Object.fromEntries(sum.patterns.map((p) => [p.id, p]));
    for (const g of sum.groups) {
      const sec = el("section", { class: "summary-group", "data-group": g.id }, [el("h3", { text: g.title })]);
      for (const pid of g.pattern_ids) {
        const p = byId[pid];
        sec.append(el("div", { class: "coach-card summary-pattern", "data-pattern": p.id }, [
          el("div", { class: "row" }, [el("strong", { class: "ipa", text: coachPatternTitle(p) }),
            el("span", { class: "chip", text: patternClassLabel(p) }),
            el("span", { class: "muted small", text: sentencesText(p.sentences) })]),
          el("p", { text: p.summary }),
          ...(p.reference_note ? [el("p", { class: "muted small", text: p.reference_note })] : []),
          exampleButtons(S, p.examples),
        ]));
      }
      box.append(sec);
    }
    if (sum.reductions.length) {
      const sec = el("section", { class: "summary-group", "data-group": "connected_speech" }, [el("h3", { text: "Connected speech" })]);
      for (const r of sum.reductions) {
        sec.append(el("div", { class: "coach-card summary-reduction" }, [
          el("div", { class: "row" }, [el("strong", { class: "ipa", text: `${r.label} — /${r.expected}/` }),
            el("span", { class: "muted small", text: `${r.occurrences}× · ${sentencesText(r.sentences)}` })]),
          ...r.explanations.map((e) => el("p", { class: "muted small", text: e.text })),
          exampleButtons(S, r.examples),
        ]));
      }
      box.append(sec);
    }
    const flu = sum.fluency;
    if (flu && flu.sentences_analysed) {
      // M8: secondary to the article and to pronunciation — one line, what recurs, at most three moments to hear
      const sec = el("section", { class: "summary-group", "data-group": "fluency" }, [el("h3", { text: "Fluency" }),
        el("p", { class: "summary-fluency", text: flu.text })]);
      if (flu.examples.length) {
        const row = el("div", { class: "row examples" });
        for (const ref of flu.examples) {
          const title = listenTitle({ start_ms: ref.span_ms[0], end_ms: ref.span_ms[1], playback: { context_ms: ref.context_ms } });
          row.append(el("button", { type: "button", class: "play-example play-fluency", "data-attempt": ref.attempt_id,
            title, text: `▶ Sentence ${ref.sentence} · ${ref.label} (${ref.observed})`, onclick: (ev) => playRef(S, ref, ev.target) }));
        }
        sec.append(row);
      }
      sec.append(el("p", { class: "muted small", text: "Counts of moments to listen to, not a judgement of the reading: pauses and repeats can be natural or intentional." }));
      box.append(sec);
    }
    // M9 replaces the unbounded "Sounds worth practising" list (the summary data keeps it, unchanged).
    if (!sum.groups.length && !sum.reductions.length) box.append(el("p", { text: "Nothing stood out across the sentences included." }));
    const cav = el("details", {}, [el("summary", { text: "How to read this" })]);
    const ul = el("ul");
    sum.caveats.forEach((c) => ul.append(el("li", { text: c })));
    cav.append(ul);
    box.append(cav);
  }

  const baseUpdate = update;
  function updateAll(S, snap) { baseUpdate(S, snap); renderSummary(S, snap); }

  window.ReaderFeedback = { update: updateAll, renderNote, annotate, toggle, renderDrawer, renderSummary, closeWord, renderCoaching,
    renderProgress, annotateCoaching,
    renderFluencyComparison };
}
