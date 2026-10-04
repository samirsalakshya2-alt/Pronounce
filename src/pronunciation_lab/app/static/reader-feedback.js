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

const TARGET_TEXT = {
  AMBIGUOUS: "This recording may not match the sentence (for example, part of it was skipped or another sentence was read).",
  MISMATCH: "This recording does not seem to be this sentence, so no pronunciation feedback is shown.",
};

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

if (typeof module !== "undefined") {
  module.exports = { feedbackLine, displayAttempt, primaryJob, TARGET_TEXT, sentencesText, boundaryNote, BOUNDARY_TEXT, otherBoundary };
}

// --- browser ----------------------------------------------------------------------------------------
if (typeof document !== "undefined") {
  const buffers = new Map(); // attempt_id → AudioBuffer of its analysis WAV
  let audioCtx = null, source = null;

  async function attemptBuffer(S, attemptId) {
    if (!buffers.has(attemptId)) {
      audioCtx = audioCtx || new AudioContext();
      const data = await (await fetch(`/api/sessions/${S.sessionId}/attempts/${attemptId}/audio`)).arrayBuffer();
      buffers.set(attemptId, await audioCtx.decodeAudioData(data));
    }
    return buffers.get(attemptId);
  }

  /** Play one window of one attempt; the reference identifies exactly what is heard. */
  async function playRef(S, ref, node) {
    S.lastPlayback = ref;
    const buf = await attemptBuffer(S, ref.attempt_id);
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
    if (targetOf(job) === "MISMATCH" && a.user_disposition !== "kept") return null; // no feedback by default
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
    const tc = targetOf(job);
    const kept = a.user_disposition === "kept";
    let text, cls = "note";
    if (a.state === "ANALYZED" && tc === "MISMATCH" && !kept) { text = "may not be this sentence"; cls += " attention"; }
    else if (a.state === "ANALYZED") {
      text = feedbackLine(job && job.feedback);
      if (tc === "AMBIGUOUS" && !kept) { text += " · may not match"; cls += " attention"; }
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
    const bn = a.state === "ANALYZED" && !(tc === "MISMATCH" && !kept) ? boundaryNote(job && job.boundary) : null;
    if (bn) {
      const line = el("span", { class: "boundary-note", "data-boundary": bn.state, title: bn.sub || bn.line },
        [el("span", { class: "boundary-line", text: bn.line })]);
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
    return [a.id, a.state, a.user_disposition || "", job ? job.id : "", job ? job.state : "", targetOf(job) || ""].join("|");
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

    const target = targetOf(job) || "NOT_CHECKED";
    if (target === "AMBIGUOUS" || target === "MISMATCH") {
      const ask = el("div", { class: "target-ask", "data-target": target }, [el("p", { text: TARGET_TEXT[target] })]);
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
      if (target === "MISMATCH" && a.user_disposition !== "kept") { d.append(footer); return; } // no feedback by default
    }
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

    body.append(boundarySection(S, job, otherBoundary(a, S.snap.jobs)));

    const cmpBox = el("div", { class: "compare" });
    const btn = el("button", { type: "button", class: "compare-attempt", text: "Compare with the other listening model",
      onclick: () => window.__readerApi.compare(a, cmpBox, btn, R) });
    body.append(el("div", { class: "line" }, [btn, el("span", { class: "muted small",
      text: "Both models share one acoustic model; differences are shown, not resolved." })]), cmpBox);
    body.append(el("p", { class: "muted small", text: `Evidence: ${job.engine_id} · recording ${a.id.slice(0, 8)} · analysis ${job.id.slice(0, 8)}` }));
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
    sec.append(el("p", { text: b.feedback_withheld
      ? "Where this sentence ends could not be established, and speech seems to continue after it. No pronunciation feedback is shown for this recording; the recording is kept."
      : plain }));
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
      const tc = job && job.target_confirmation ? job.target_confirmation.state : null;
      const line = (tc === "MISMATCH" || tc === "AMBIGUOUS") && last.user_disposition !== "kept"
        ? "may not match the sentence — open to keep or re-record" : feedbackLine(job && job.feedback);
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
    box.append(el("p", { class: "lead", text: `${cov.included} of ${cov.sentences} sentences included · ${cov.consistent_with_expected} of ${cov.sounds} sounds consistent with the expected sound.` }));
    if (cov.not_included.length) {
      box.append(el("p", { class: "muted small", text: "Not included: " + cov.not_included.map((x) => `sentence ${x.sentence} (${x.reason})`).join(", ") }));
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
    if (sum.practise.length) {
      const sec = el("section", { class: "summary-group", "data-group": "practise" }, [el("h3", { text: "Sounds worth practising" })]);
      for (const t of sum.practise) {
        sec.append(el("div", { class: "coach-card" }, [el("strong", { class: "ipa", text: `/${t.target}/ vs /${t.contrast}/` }),
          ...(t.guidance ? [el("p", { class: "small", text: t.guidance }), el("p", { class: "muted small", text: t.guidance_note })] : []),
          exampleButtons(S, t.examples)]));
      }
      box.append(sec);
    }
    if (!sum.groups.length && !sum.reductions.length) box.append(el("p", { text: "Nothing stood out across the sentences included." }));
    const cav = el("details", {}, [el("summary", { text: "How to read this" })]);
    const ul = el("ul");
    sum.caveats.forEach((c) => ul.append(el("li", { text: c })));
    cav.append(ul);
    box.append(cav);
  }

  const baseUpdate = update;
  function updateAll(S, snap) { baseUpdate(S, snap); renderSummary(S, snap); }

  window.ReaderFeedback = { update: updateAll, renderNote, annotate, toggle, renderDrawer, renderSummary, closeWord };
}
