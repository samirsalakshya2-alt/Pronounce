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

if (typeof module !== "undefined") {
  module.exports = {
    CATEGORY_TITLES, WORD_TITLES, formatSeconds, formatSpan, formatProbability,
    playbackArgs, analyzeUrl, recordingFilename, engineOptionLabel, summaryChips, extraSoundsText, localTime,
    wordTitle, ALL_WORD_TITLES, fullFeedbackText,
  };
}

// ---------------------------------------------------------------------------
// Browser UI
// ---------------------------------------------------------------------------

if (typeof document !== "undefined") {
  const $ = (id) => document.getElementById(id);
  const state = {
    status: null, tab: "record", recordedBlob: null, recorder: null, chunks: [],
    view: null, audioCtx: null, buffer: null, source: null, selectedWord: null, notes: {},
  };

  function el(tag, attrs, children) {
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
      throw new Error(msg);
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
    const id = $("engine-select").value;
    const e = state.status.engines.find((x) => x.id === id);
    $("engine-note").textContent = e && e.note ? e.note : "";
  }

  // --- tabs ------------------------------------------------------------------
  function selectTab(tab) {
    state.tab = tab;
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    document.querySelectorAll(".tab-body").forEach((b) => (b.hidden = b.dataset.body !== tab));
    if (tab === "benchmark") fillBenchmarkText();
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
  async function analyse() {
    showError("");
    const text = $("target-text").value.trim();
    const engine = $("engine-select").value;
    if (!text) { showError("Enter the sentence you read aloud."); return; }

    let request;
    if (state.tab === "benchmark") {
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
      request = api(analyzeUrl(text, engine), { method: "POST", headers: { "X-Filename": name }, body: blob });
    }

    const btn = $("analyze-btn");
    btn.disabled = true;
    $("run-status").textContent = "Analysing… (the first analysis also loads the model)";
    try {
      const view = await request;
      await showResult(view);
      $("run-status").textContent = "";
      refreshHistory();
    } catch (e) {
      $("run-status").textContent = "";
      showError(e.message);
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
    box.append(el("h3", { text: "“" + w.word + "” — " + wordTitle(w) }));
    const wordRow = el("div", { class: "row" }, [
      el("button", { type: "button", text: "▶ Play word", onclick: () => play(w.play_ms, button) }),
      el("span", { class: "muted small", text: "Word located at " + formatSpan(w.span_ms, w.timing_estimated) +
        (w.flagged_by_engine ? " · flagged by OpenPronounce" : "") }),
    ]);
    box.append(wordRow);

    const table = el("table", {}, [el("tr", {}, [
      el("th", { text: "Expected" }), el("th", { text: "Heard" }), el("th", { text: "What the recogniser found" }),
      el("th", { text: "Alternatives" }), el("th", { text: "Where" }), el("th", { text: "Listen & note" }),
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
        noteCell(s, row),
      );
      table.append(row);
    }
    box.append(table);
    box.append(el("p", { class: "muted small", text:
      "“Play sound” plays " + formatSeconds(300) + " around the point where the sound was located; the exact point is shown under “Where”." }));
  }

  function noteCell(s, row) {
    const td = el("td", { class: "notes" });
    td.append(el("button", { type: "button", text: "▶ Play sound", onclick: () => play(s.play_ms, row) }));
    if (!state.status.notes_enabled) return td;
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

  // --- history -----------------------------------------------------------------------
  async function refreshHistory() {
    const res = await api("/api/analyses");
    const list = $("history-list");
    list.innerHTML = "";
    if (!res.analyses.length) { list.append(el("li", { text: "No analyses yet." })); return; }
    for (const a of res.analyses) {
      list.append(el("li", {
        text: localTime(a.created_at) + " · " + a.source + " · " + a.engine + " · “" + a.text + "”",
        onclick: async () => { showError(""); try { await showResult(await api("/api/analyses/" + a.analysis_id)); } catch (e) { showError(e.message); } },
      }));
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
  $("record-support").textContent = window.MediaRecorder ? "Recording uses your browser's microphone." : "This browser cannot record; use Upload.";
  loadStatus().then(refreshHistory).catch((e) => showError("Could not reach the app: " + e.message));
}
