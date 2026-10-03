// M12 reader — DOM layer. Pure capture/upload logic lives in reader-core.js.
//
// The sentence is the recording control: selecting it starts reading it.
// Selecting the next sentence cuts the recording synchronously (no awaits): the
// previous attempt is encoded and uploaded in the background and analysed on the
// server while the user keeps reading.
"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  const S = {
    status: null, snap: null, rev: null, sessionId: null, segments: [], segById: {},
    segEls: {}, markEls: {}, drawers: {}, controller: null, uploads: null, stream: null, ctx: null, node: null,
    level: 0, arming: false, lastSelected: null, pollTimer: null, localAttempts: {},
  };
  window.__reader = S; // read-only hook for browser tests

  function el(tag, attrs, children) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null && v !== false) n.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children || []) n.append(c);
    return n;
  }

  async function api(url, options) {
    const res = await fetch(url, options);
    let body = null;
    try { body = await res.json(); } catch (e) { /* empty */ }
    if (!res.ok) {
      const err = new Error((body && body.error && body.error.message) || `HTTP ${res.status}`);
      err.code = body && body.error && body.error.code;
      err.status = res.status;
      throw err;
    }
    return body;
  }
  const post = (url, body) => api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

  function notice(text) { $("notice").textContent = text || ""; $("notice").hidden = !text; }

  // --- entry ----------------------------------------------------------------------------
  async function showEntry() {
    $("entry").hidden = false;
    $("reader").hidden = true;
    const sel = $("entry-engine");
    sel.innerHTML = "";
    for (const e of S.status.engines.filter((x) => x.state === "runnable" && ["wav2vec2_raw", "openpronounce"].includes(x.id))) {
      sel.append(el("option", { value: e.id, text: e.label }));
    }
    sel.value = S.status.default_engine;
    try {
      const { sessions } = await api("/api/sessions");
      $("recent").hidden = !sessions.length;
      $("recent-list").innerHTML = "";
      for (const s of sessions.slice(0, 10)) {
        $("recent-list").append(el("li", { text: `${s.title} · ${s.attempts} recording${s.attempts === 1 ? "" : "s"}`,
          onclick: () => { location.search = "?session=" + s.id; } }));
      }
    } catch (e) { /* the list is optional */ }
  }

  async function startFromEntry(ev) {
    ev.preventDefault();
    $("entry-error").hidden = true;
    $("entry-start").disabled = true;
    try {
      const { article } = await post("/api/articles", { text: $("entry-text").value, title: $("entry-title").value,
        source: $("entry-source").value });
      const sid = uuidHex();
      const snap = await post("/api/sessions", { session_id: sid, article_id: article.id, engine: $("entry-engine").value });
      history.replaceState(null, "", "/read?session=" + sid);
      openReader(snap);
    } catch (e) {
      $("entry-error").textContent = e.message;
      $("entry-error").hidden = false;
    } finally {
      $("entry-start").disabled = false;
    }
  }

  // --- reader -------------------------------------------------------------------------------
  async function openSession(sid) {
    try {
      // A new page: any capture another page left open can no longer finish.
      const snap = await post(`/api/sessions/${sid}/state`, { action: "reopen", run_id: null });
      openReader(snap);
    } catch (e) {
      notice("");
      await showEntry();
      $("entry-error").textContent = e.message;
      $("entry-error").hidden = false;
    }
  }

  function openReader(snap) {
    $("entry").hidden = true;
    $("reader").hidden = false;
    S.sessionId = snap.session.id;
    S.segments = snap.article.segments;
    S.segById = Object.fromEntries(S.segments.map((s) => [s.id, s]));
    $("article-title").textContent = snap.article.title;
    $("top-title").textContent = snap.article.title;
    document.title = snap.article.title + " — Pronounce";
    $("article-source").textContent = snap.article.source || "";
    $("article-source").hidden = !snap.article.source;
    renderBody();
    S.controller = new CaptureController({ sessionId: S.sessionId, onStarted: attemptStarted, onFinalized: attemptFinalized });
    S.uploads = new UploadQueue({ send: uploadAttempt, onChange: () => { renderQueue(); schedulePoll(300); } });
    applySnapshot(snap);
    schedulePoll(1000);
    requestAnimationFrame(tick);
  }

  function renderBody() {
    const body = $("article-body");
    body.innerHTML = "";
    let para = null, pIndex = -1;
    for (const seg of S.segments) {
      if (seg.paragraph_index !== pIndex) {
        para = el("div", { class: "para", "data-paragraph": seg.paragraph_index });
        body.append(para);
        pIndex = seg.paragraph_index;
      } else {
        para.append(" ");
      }
      if (!seg.readable) { para.append(el("span", { class: "seg nontext", text: seg.text })); continue; }
      const span = el("span", { class: "seg", tabindex: "0", role: "button", "data-seg": seg.id, text: seg.text,
        "aria-label": "Read: " + seg.text });
      // an analysed sentence is the feedback: its words open their detail; "Read again" records it again
      span.addEventListener("click", () => { if (!span.classList.contains("annotated")) onSegment(seg.id); });
      span.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); onSegment(seg.id); } });
      // inline after the sentence: its concise feedback and "Details" (filled once it has been read)
      const note = el("span", { class: "note", hidden: true, "data-note": seg.id });
      S.segEls[seg.id] = span;
      S.markEls[seg.id] = note;
      para.append(span, note);
    }
  }

  // --- capture --------------------------------------------------------------------------------
  async function ensureArmed() {
    const c = S.controller;
    if (c.state === "ARMED" || c.state === "CAPTURING") return true;
    if (!navigator.mediaDevices || !window.AudioWorkletNode) {
      notice("This browser cannot record audio here.");
      return false;
    }
    c.requesting();
    renderMic();
    try {
      S.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } });
    } catch (e) {
      c.denied();
      renderMic();
      notice("Microphone access was denied. Allow the microphone for this page, then click a sentence again.");
      return false;
    }
    S.ctx = new AudioContext();
    await S.ctx.audioWorklet.addModule("/static/capture-worklet.js");
    const source = S.ctx.createMediaStreamSource(S.stream);
    S.node = new AudioWorkletNode(S.ctx, "pronounce-capture", { numberOfInputs: 1, numberOfOutputs: 1 });
    const mute = S.ctx.createGain();
    mute.gain.value = 0; // keeps the graph pulled in every browser; nothing is played
    source.connect(S.node).connect(mute).connect(S.ctx.destination);
    S.node.port.onmessage = (e) => {
      c.push(e.data.start, e.data.data);
      S.level = Math.max(S.level * 0.85, Math.min(1, rms(e.data.data) * 6));
    };
    for (const t of S.stream.getAudioTracks()) t.addEventListener("ended", deviceLost);
    c.armed(S.ctx.sampleRate);
    notice("");
    return true;
  }

  function releaseMic() {
    if (S.stream) S.stream.getTracks().forEach((t) => t.stop());
    if (S.node) S.node.port.onmessage = null;
    if (S.ctx) S.ctx.close().catch(() => {});
    S.stream = S.ctx = S.node = null;
    S.level = 0;
  }

  function onSegment(segId) {
    if (S.arming) return;
    const c = S.controller;
    if (c.state === "ARMED" || c.state === "CAPTURING") { select(segId); return; }
    S.arming = true;
    ensureArmed().then((ok) => { S.arming = false; if (ok) select(segId); renderMic(); },
      (e) => { S.arming = false; notice("Could not start recording: " + e.message); renderMic(); });
  }

  /** Synchronous: the cut, the new attempt and the UI update happen in this call. */
  function select(segId) {
    const seg = S.segById[segId];
    const { started } = S.controller.select(segId, seg.text);
    if (!started) return;
    S.lastSelected = segId;
    renderSegments();
    renderMic();
  }

  function attemptStarted(a) {
    S.localAttempts[a.attemptId] = a;
    post(`/api/sessions/${S.sessionId}/attempts/${a.attemptId}/start`, {
      segment_id: a.segmentId, run_id: a.runId, sample_rate: a.sampleRate, start_sample: a.startSample,
      wall_clock_start: a.wallClockStart,
    }).catch(() => { /* the upload alone also creates the attempt */ });
  }

  function attemptFinalized(a, samples) {
    if (a.endReason === "limit_reached") {
      notice("Recording stopped at 60 seconds for this sentence. Click the next sentence to continue.");
      renderMic();
    }
    const wav = encodeWav(samples, a.sampleRate);
    S.uploads.add({ id: a.attemptId, attempt: a, wav });
    renderSegments();
  }

  async function uploadAttempt(item) {
    const a = item.attempt;
    const res = await fetch(`/api/sessions/${S.sessionId}/attempts/${a.attemptId}/audio`, {
      method: "POST", headers: { "Content-Type": "audio/wav", "X-Capture": captureHeader(a) }, body: item.wav,
    }).catch((e) => { throw Object.assign(new Error("offline: " + e.message), { retryable: true }); });
    if (res.ok) { a.status = "UPLOADED"; return res.json(); }
    let body = null;
    try { body = await res.json(); } catch (e) { /* empty */ }
    const code = body && body.error && body.error.code;
    if (code === "attempt_duplicate") { a.status = "UPLOADED"; return body; } // already stored
    const err = new Error((body && body.error && body.error.message) || `HTTP ${res.status}`);
    err.retryable = res.status >= 500;
    a.status = "UPLOAD_FAILED";
    throw err;
  }

  function deviceLost() {
    if (!S.controller || S.controller.state === "RELEASED") return;
    S.controller.deviceLost();
    releaseMic();
    notice("The microphone stopped. What was recorded so far is kept. Click a sentence to continue.");
    post(`/api/sessions/${S.sessionId}/state`, { action: "stop" }).catch(() => {});
    renderAll();
  }

  // --- session actions --------------------------------------------------------------------------
  function pauseOrResume() {
    const c = S.controller;
    if (c.state === "CAPTURING") {
      c.pause();
      post(`/api/sessions/${S.sessionId}/state`, { action: "pause" }).then(applySnapshot).catch(() => {});
    } else if (S.lastSelected) {
      onSegment(S.lastSelected); // RESUME: a new attempt of the sentence that was active
    }
    renderAll();
  }

  function stop(reason) {
    const c = S.controller;
    if (c.state !== "RELEASED" && c.state !== "NO_PERMISSION" && c.state !== "DENIED") c.stop(reason || "stopped");
    releaseMic();
    renderAll();
  }

  function stopReading() {
    stop("stopped");
    post(`/api/sessions/${S.sessionId}/state`, { action: "stop" }).then(applySnapshot).catch(() => {});
  }

  function finishReading() {
    stop("finished");
    post(`/api/sessions/${S.sessionId}/state`, { action: "finish" }).then(applySnapshot).catch((e) => notice(e.message));
  }

  function step(delta) {
    const readable = S.segments.filter((s) => s.readable);
    const i = readable.findIndex((s) => s.id === S.lastSelected);
    const next = readable[Math.max(0, Math.min(readable.length - 1, i + delta))];
    if (next) { onSegment(next.id); S.segEls[next.id].scrollIntoView({ block: "center", behavior: "smooth" }); }
  }

  // --- server state -------------------------------------------------------------------------------
  function schedulePoll(ms) {
    clearTimeout(S.pollTimer);
    S.pollTimer = setTimeout(poll, ms);
  }

  async function poll() {
    try {
      const snap = await api(`/api/sessions/${S.sessionId}` + (S.rev !== null ? `?since=${S.rev}` : ""));
      if (!snap.unchanged) applySnapshot(snap);
    } catch (e) { /* transient: try again */ }
    const busy = S.snap && (S.snap.queue.queued_primary || S.snap.queue.queued_comparison || S.snap.queue.running);
    schedulePoll(busy || S.controller.state === "CAPTURING" ? 700 : 2500);
  }

  function applySnapshot(snap) {
    if (!snap || snap.unchanged) return;
    S.snap = snap;
    S.rev = snap.rev;
    renderAll();
    if (window.ReaderFeedback) window.ReaderFeedback.update(S, snap);
  }

  // --- rendering -----------------------------------------------------------------------------------
  function attemptsBySegment() {
    const out = {};
    for (const a of (S.snap ? S.snap.attempts : [])) (out[a.segment_id] = out[a.segment_id] || []).push(a);
    return out;
  }

  function renderSegments() {
    const states = S.snap ? S.snap.segment_states : {};
    const recording = S.controller && S.controller.current ? S.controller.current.segmentId : null;
    const local = new Set(Object.values(S.localAttempts).filter((a) => a.status !== "UPLOADED").map((a) => a.segmentId));
    for (const seg of S.segments) {
      const span = S.segEls[seg.id];
      if (!span) continue;
      const st = states[seg.id] || "UNREAD";
      span.classList.toggle("recording", seg.id === recording);
      span.classList.toggle("read", st !== "UNREAD" || local.has(seg.id));
      const pending = seg.id !== recording && (local.has(seg.id) || st === "PROCESSING");
      if (window.ReaderFeedback) {
        window.ReaderFeedback.renderNote(S, seg.id, S.markEls[seg.id], { state: st, pending, recording: seg.id === recording });
        window.ReaderFeedback.annotate(S, seg.id, seg.id === recording);
      }
    }
  }

  function renderMic() {
    const c = S.controller;
    const box = $("mic-state");
    const st = c ? c.state : "NO_PERMISSION";
    box.dataset.state = st === "CAPTURING" ? "recording" : st === "ARMED" ? "paused" : "idle";
    const seg = c && c.current ? S.segments.findIndex((s) => s.id === c.current.segmentId) + 1 : 0;
    $("mic-label").textContent = st === "CAPTURING" ? `Reading sentence ${seg}`
      : st === "ARMED" ? "Paused — click a sentence or Resume"
      : st === "REQUESTING" ? "Waiting for the microphone…"
      : st === "DENIED" ? "Microphone blocked"
      : st === "RELEASED" ? "Microphone off — click a sentence to continue"
      : "Click a sentence to start reading";
    $("pause-btn").disabled = !(st === "CAPTURING" || (S.lastSelected && (st === "ARMED" || st === "RELEASED")));
    $("pause-btn").textContent = st === "CAPTURING" ? "❚❚ Pause" : "▶ Resume";
    $("stop-btn").disabled = !(st === "CAPTURING" || st === "ARMED");
    const finished = S.snap && ["FINISHED", "SUMMARIZED"].includes(S.snap.session.state);
    $("finish-btn").disabled = !(S.snap && S.snap.attempts.length) || finished;
  }

  function renderQueue() {
    const up = S.uploads ? S.uploads.status() : { waiting: 0, uploading: 0, failed: 0 };
    const q = S.snap ? S.snap.queue : { queued_primary: 0, running: null };
    const listening = (q.queued_primary || 0) + (q.running && q.running.kind === "primary" ? 1 : 0) + up.waiting + up.uploading;
    const parts = [];
    if (listening) parts.push(`Listening to ${listening} sentence${listening === 1 ? "" : "s"}…`);
    if (up.failed) parts.push(`${up.failed} upload${up.failed === 1 ? "" : "s"} failed — `);
    $("queue").textContent = parts.join(" ") || (S.snap && S.snap.attempts.some((a) => a.state === "ANALYZED") ? "All caught up." : "");
    if (up.failed) {
      $("queue").append(el("button", { type: "button", text: "Retry", onclick: () => {
        for (const id of [...S.uploads.failed.keys()]) S.uploads.retry(id);
      } }));
    }
  }

  function renderProgress() {
    const readable = S.segments.filter((s) => s.readable);
    const by = attemptsBySegment();
    const read = readable.filter((s) => by[s.id] || Object.values(S.localAttempts).some((a) => a.segmentId === s.id)).length;
    $("progress").hidden = false;
    $("progress").textContent = `${read} / ${readable.length} sentences read`;
  }

  function renderAll() { renderSegments(); renderMic(); renderQueue(); renderProgress(); }

  function tick() {
    const c = S.controller;
    $("mic-timer").textContent = c && c.state === "CAPTURING" ? formatClock(c.elapsedSeconds()) : "";
    S.level *= 0.92;
    $("level-bar").style.width = Math.round((c && c.state === "CAPTURING" ? S.level : 0) * 100) + "%";
    requestAnimationFrame(tick);
  }


  // --- actions used by the feedback drawer ----------------------------------------------------------
  window.__readerApi = {
    refresh() { renderSegments(); },
    readAgain(segId) { onSegment(segId); },
    retry(a, job) {
      post(`/api/sessions/${S.sessionId}/attempts/${a.id}/jobs/${job.id}/retry`, {})
        .then(() => schedulePoll(200)).catch((e) => notice(e.message));
    },
    disposition(a, value) {
      return post(`/api/sessions/${S.sessionId}/attempts/${a.id}/disposition`, { value })
        .then(() => schedulePoll(50)).catch((e) => notice(e.message));
    },
    rerecord(a, segId) {
      this.disposition(a, "rerecord_requested");
      if (S.drawers[segId]) S.drawers[segId].hidden = true;
      onSegment(segId); // a new recording of the same sentence; the old one stays in the history // a new attempt of the same sentence; the old one stays in the history
    },
    async compare(a, box, btn, R) {
      btn.disabled = true;
      box.innerHTML = "";
      box.append(el("p", { class: "muted small", text: "Listening with the other model…" }));
      try {
        await post(`/api/sessions/${S.sessionId}/attempts/${a.id}/compare`, {});
        for (let i = 0; i < 240; i++) {
          const res = await fetch(`/api/sessions/${S.sessionId}/attempts/${a.id}/comparison`);
          if (res.ok) { R.renderComparison(box, await res.json()); return; }
          const body = await res.json().catch(() => ({}));
          if (!body.error || body.error.code !== "comparison_pending") {
            throw new Error((body.error && body.error.message) || `HTTP ${res.status}`);
          }
          await new Promise((r) => setTimeout(r, 500));
        }
        throw new Error("timed out");
      } catch (e) {
        box.innerHTML = "";
        box.append(el("p", { class: "error", text: "Comparison failed: " + e.message }));
      } finally {
        btn.disabled = false;
      }
    },
  };

  // --- wiring -----------------------------------------------------------------------------------------
  $("entry-form").addEventListener("submit", startFromEntry);
  $("pause-btn").addEventListener("click", pauseOrResume);
  $("stop-btn").addEventListener("click", stopReading);
  $("finish-btn").addEventListener("click", finishReading);
  document.addEventListener("keydown", (e) => {
    if (!S.controller || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
    if (e.key === " ") { e.preventDefault(); pauseOrResume(); }
    else if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); step(1); }
    else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); step(-1); }
    else if (e.key === "Escape") stopReading();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden && S.controller && S.controller.state === "CAPTURING") {
      S.controller.finalizeCurrent("page_hidden");
      post(`/api/sessions/${S.sessionId}/state`, { action: "pause" }).catch(() => {});
      notice("Paused because the page was hidden. Click a sentence to continue.");
      renderAll();
    }
  });
  window.addEventListener("beforeunload", (e) => {
    const c = S.controller;
    const unsent = S.uploads && (S.uploads.status().waiting || S.uploads.status().uploading);
    if ((c && c.state === "CAPTURING") || unsent) { e.preventDefault(); e.returnValue = ""; }
  });

  (async () => {
    S.status = await api("/api/status");
    const sid = new URLSearchParams(location.search).get("session");
    if (sid) await openSession(sid); else await showEntry();
  })().catch((e) => notice("Could not reach the app: " + e.message));
})();
