/* Browser-owned M12 Reader domain and persistence integration. */
"use strict";

(function (root) {
  const MAX_SEGMENT_CHARS = 300;
  const MAX_ARTICLE_CHARS = 50000;
  const ABBREVIATIONS = new Set(("mr mrs ms dr prof st sr jr vs etc inc ltd co corp no fig e.g i.e a.m p.m " +
    "u.s u.k approx dept est govt mt jan feb mar apr jun jul aug sep sept oct nov dec").split(/\s+/u));
  const END = /[.!?…]+["'”’)\]]*(?=\s)/gu;
  const CLAUSE = /[;:,—–]\s/gu;
  const WORD = /[^\W\d_]/u;
  const ID = () => root.uuidHex();
  const now = () => new Date().toISOString();
  const STATES = {
    READY: ["READING"],
    READING: ["PAUSED", "STOPPED", "FINISHED", "INTERRUPTED"],
    PAUSED: ["READING", "STOPPED", "FINISHED", "INTERRUPTED"],
    STOPPED: ["READING", "FINISHED", "INTERRUPTED"],
    INTERRUPTED: ["PAUSED", "READING", "STOPPED", "FINISHED"],
    FINISHED: ["SUMMARIZED", "READING"],
    SUMMARIZED: ["READING", "SUMMARIZED"],
  };

  function collapse(text) { return text.replace(/\s+/gu, " ").trim(); }
  function hasLetter(text) { return WORD.test(text); }

  function paragraphs(text) {
    const spans = [];
    const separator = /\n[ \t]*\n\s*/gu;
    let start = 0, match;
    while ((match = separator.exec(text))) {
      spans.push([start, match.index]);
      start = separator.lastIndex;
    }
    spans.push([start, text.length]);
    return spans.map(([a, b]) => {
      while (a < b && /\s/u.test(text[a])) a++;
      while (b > a && /\s/u.test(text[b - 1])) b--;
      return [a, b];
    }).filter(([a, b]) => a < b);
  }

  function isAbbreviation(text, end) {
    if (text[end - 1] !== ".") return false;
    let start = end - 1;
    while (start > 0 && !/\s/u.test(text[start - 1])) start--;
    const token = text.slice(start, end - 1).replace(/^["'“‘([]+/u, "").toLowerCase();
    return ABBREVIATIONS.has(token) || (token.length === 1 && /^[a-z]$/iu.test(token));
  }

  function sentenceSpans(text, start, end) {
    const out = [];
    let segmentStart = start;
    END.lastIndex = start;
    let match;
    while ((match = END.exec(text)) && match.index < end) {
      const finish = END.lastIndex;
      if (isAbbreviation(text, match.index + (text[match.index] === "." ? 1 : match[0].length))) continue;
      let next = finish;
      while (next < end && /\s/u.test(text[next])) next++;
      if (next < end && !/[\p{Lu}\d"'“‘([]/u.test(text[next])) continue;
      out.push([segmentStart, finish]);
      segmentStart = next;
    }
    if (segmentStart < end) out.push([segmentStart, end]);
    return out;
  }

  function splitLong(text, start, end) {
    const parts = [];
    while (collapse(text.slice(start, end)).length > MAX_SEGMENT_CHARS) {
      let windowEnd = start, count = 0, previousWhitespace = false;
      for (let i = start; i < end; i++) {
        const whitespace = /\s/u.test(text[i]);
        if (!(whitespace && previousWhitespace)) count++;
        previousWhitespace = whitespace;
        if (count > MAX_SEGMENT_CHARS) break;
        windowEnd = i + 1;
      }
      let cut = null;
      CLAUSE.lastIndex = start;
      let match;
      while ((match = CLAUSE.exec(text)) && match.index < windowEnd) {
        if (hasLetter(text.slice(start, match.index + 1))) cut = match.index + 1;
      }
      if (cut === null) {
        cut = text.lastIndexOf(" ", windowEnd - 1);
        if (cut <= start) cut = windowEnd;
      }
      parts.push([start, cut]);
      start = cut;
      while (start < end && /\s/u.test(text[start])) start++;
    }
    if (start < end) parts.push([start, end]);
    return parts;
  }

  function segmentArticle(text, articleId) {
    if (typeof text !== "string" || text.length > MAX_ARTICLE_CHARS) {
      throw new Error(`Article must be text no longer than ${MAX_ARTICLE_CHARS} characters.`);
    }
    const spans = [];
    for (const [paragraphIndex, [paragraphStart, paragraphEnd]] of paragraphs(text).entries()) {
      const pieces = [];
      for (const [start, end] of sentenceSpans(text, paragraphStart, paragraphEnd)) {
        pieces.push(...splitLong(text, start, end));
      }
      const merged = [];
      let pending = null;
      for (let [start, end] of pieces) {
        if (pending !== null && collapse(text.slice(pending, end)).length <= MAX_SEGMENT_CHARS) {
          start = pending;
          pending = null;
        } else if (pending !== null) {
          merged.push([pending, start]);
          pending = null;
        }
        if (!hasLetter(text.slice(start, end))) {
          pending = start;
          continue;
        }
        merged.push([start, end]);
      }
      if (pending !== null) {
        const lastEnd = pieces[pieces.length - 1][1];
        if (merged.length && collapse(text.slice(merged[merged.length - 1][0], lastEnd)).length <= MAX_SEGMENT_CHARS) {
          merged[merged.length - 1][1] = lastEnd;
        } else {
          merged.push([pending, lastEnd]);
        }
      }
      spans.push(...merged.map(([start, end]) => ({ paragraphIndex, start, end })));
    }
    if (!spans.some(({ start, end }) => hasLetter(text.slice(start, end)))) throw new Error("Article contains no words.");
    return spans.map(({ paragraphIndex, start, end }, index) => ({
      id: `${articleId}:${String(index).padStart(4, "0")}`, index, paragraph_index: paragraphIndex,
      char_start: start, char_end: end, text: collapse(text.slice(start, end)),
      readable: hasLetter(text.slice(start, end)),
    }));
  }

  function transition(session, next) {
    if (session.state === next) return;
    if (!(STATES[session.state] || []).includes(next)) throw new Error(`Session cannot transition ${session.state} to ${next}.`);
    session.state = next;
    session.updated_at = now();
    session.rev++;
  }

  function combineIdentity(primary, other) {
    const out = { ...primary };
    out.other_engine = {
      engine: other.engine, state: other.state, identity: other.identity,
      note: "Both local listening models share one acoustic model; agreement is not independent confirmation.",
    };
    if (primary.state === "MISMATCH" && ["MATCH", "LIKELY_MATCH", "AMBIGUOUS"].includes(other.state)) {
      out.primary_state = "MISMATCH";
      out.state = "AMBIGUOUS";
      out.reason = "the listening models disagree about whether this is the sentence";
    }
    return out;
  }

  function attemptStatus(attempt, primary, engine) {
    const state = attempt.state;
    let identity;
    if (state === "TOO_SHORT") identity = "TOO_SHORT";
    else if (["ANALYSIS_FAILED", "REJECTED", "INTERRUPTED"].includes(state)) identity = "FAILED";
    else if (state !== "ANALYZED" || !primary || primary.state !== "SUCCEEDED") {
      identity = ["CAPTURING", "RECORDED", "QUEUED", "ANALYZING"].includes(state) ? "PENDING" : "FAILED";
    } else identity = primary.target_confirmation?.state || "NOT_APPLICABLE";
    const boundary = primary?.boundary || {};
    const withheld = Boolean(boundary.feedback_withheld);
    const cause = withheld ? (boundary.withheld_reason || "boundary") : null;
    const analysis = withheld ? null : boundary.analysis?.state || null;
    const feedback = ["TOO_SHORT", "FAILED", "PENDING", "NOT_APPLICABLE", "NOT_CHECKED"].includes(identity)
      ? "none" : withheld ? (cause === "containment" ? "withheld_containment" : "withheld_boundary")
        : identity === "MISMATCH" ? "hidden_identity" : "shown";
    const messages = {
      MATCH: "Recording appears to match this sentence.",
      LIKELY_MATCH: "This recording is probably this sentence (it fits it best of the article's sentences), but the evidence is weak.",
      AMBIGUOUS: "I couldn't confidently tell whether this recording is this sentence.",
      MISMATCH: "This recording appears to contain a different sentence.",
      TOO_SHORT: "Recording is too short to confirm the sentence.",
      FAILED: "Recording could not be analysed.",
      PENDING: "Listening to this recording…",
    };
    const summaryCodes = {
      discarded: "discarded", rerecord: "marked for re-recording", not_analysed: "not analysed",
      other_engine: "analysed by another engine", different_sentence: "appears to contain a different sentence",
      boundary: "sentence boundary uncertain — recording preserved, feedback withheld",
      containment: "analysis not contained in the sentence — recording preserved, feedback withheld",
      unconfirmed: "could not confirm it is this sentence — keep it to include it",
    };
    const identified = ["MATCH", "LIKELY_MATCH"].includes(identity);
    const reason = attempt.user_disposition === "discarded" ? "discarded"
      : attempt.user_disposition === "rerecord_requested" ? "rerecord"
        : feedback === "none" ? "not_analysed"
          : engine && primary?.engine_id !== engine ? "other_engine"
            : identity === "MISMATCH" ? "different_sentence"
              : withheld ? (cause === "containment" ? "containment" : "boundary")
                : identified || attempt.user_disposition === "kept" ? null : "unconfirmed";
    return {
      preserved: true, disposition: attempt.user_disposition, kept: attempt.user_disposition === "kept",
      identity, boundary: boundary.state || null, feedback_withheld: withheld, withheld_reason: cause,
      boundary_confidence: boundary.boundary_confidence || null, analysis, feedback,
      message: withheld && identity === "MATCH"
        ? "Your reading appears to match this sentence, but I couldn't safely determine where it ended."
        : messages[identity] || "",
      analysis_note: feedback === "shown" && analysis === "low_confidence"
        ? "Many of this sentence's sounds were decoded differently from the expected ones, so treat its pronunciation feedback with caution."
        : null,
      summary: reason === null ? null : summaryCodes[reason], summary_code: reason,
      needs_decision: ["AMBIGUOUS", "MISMATCH"].includes(identity) && attempt.user_disposition !== "kept" &&
        !["discarded", "rerecord_requested"].includes(attempt.user_disposition),
      recorded: ["MATCH", "LIKELY_MATCH", "AMBIGUOUS"].includes(identity) &&
        attempt.user_disposition !== "discarded",
      identity_group: identified || (identity === "AMBIGUOUS" && attempt.user_disposition === "kept") ? "identified"
        : identity === "AMBIGUOUS" ? "uncertain" : identity === "MISMATCH" ? "different" : "unusable",
    };
  }

  function compactFeedback(view) {
    const coach = view.coach || {};
    const reduction = view.reduction || {};
    if (view.state !== "ok" || coach.state !== "ok") {
      return { state: view.state || coach.state || "unavailable", notice: 0, compare: 0, message: view.message || coach.message };
    }
    const patterns = Object.fromEntries((coach.patterns || []).map((item) => [item.id, item]));
    const groupOf = {};
    for (const group of coach.groups || []) for (const patternId of group.pattern_ids) groupOf[patternId] = group.id;
    const counted = new Set();
    let notice = 0, compare = 0;
    for (const [id, pattern] of Object.entries(patterns)) {
      if (["recurring", "single", "not_detected"].includes(groupOf[id])) {
        notice++;
        for (const oid of pattern.observation_ids) counted.add(oid);
      } else if (groupOf[id] === "ambiguous") {
        compare++;
        for (const oid of pattern.observation_ids) counted.add(oid);
      }
    }
    for (const candidate of reduction.candidates || []) {
      const category = candidate.interpretation.category;
      if (category === "insufficient_evidence" || counted.has(candidate.observation_id)) continue;
      if (category === "ambiguous") compare++; else notice++;
      counted.add(candidate.observation_id);
    }
    return { state: "ok", notice, compare, extra_sounds: 0, insufficient: 0, not_interpreted: coach.coverage?.not_interpreted || 0 };
  }

  function playbackRef(attempt, job, playMs, spanMs, kind) {
    return {
      session_id: attempt.session_id, segment_id: attempt.segment_id, attempt_id: attempt.id,
      job_id: job?.id || null, timeline: "analysis_wav", kind, play_ms: playMs, span_ms: spanMs,
    };
  }

  function boundaryFor(job, attempt) {
    const boundary = job.boundary ? structuredClone(job.boundary) : null;
    if (!boundary) return null;
    boundary.regions = (boundary.regions || []).map((region) => ({
      ...region,
      play: playbackRef(attempt, job, [region.start_ms, region.end_ms], [region.start_ms, region.end_ms], region.kind),
    }));
    return boundary;
  }

  class BrowserReader {
    constructor(store, status) {
      this.store = store;
      this.status = status;
      this.longitudinal = store.progressStore();
      this.article = null;
      this.sessionId = null;
      this.lastTimings = null;
    }

    static async open(status) {
      return new BrowserReader(await root.PronounceBrowserStore.BrowserStore.open(), status);
    }

    async sessions() {
      const sessions = [];
      for (const id of await this.store.sessionIds()) {
        const session = await this.store.loadSession(id);
        const article = await this.store.loadArticle(session.article_id);
        sessions.push({ id, title: article.title, state: session.state, updated_at: session.updated_at,
          attempts: session.attempt_ids.length });
      }
      return sessions.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    }

    async homeState() {
      const history = await this._eligibleHistory();
      const coachingApi = root.PronounceReaderCoaching;
      const coaching = coachingApi
        ? await coachingApi.runCoaching(history.inputs, history.exclusions)
        : { state: "unavailable", actions: [] };
      const longitudinal = coachingApi?.progress
        ? await coachingApi.progress({
          inputs: history.inputs, sessions: history.sessions, longitudinalStore: this.longitudinal, coaching,
        })
        : { progress: { state: "unavailable", patterns: [] }, coaching_adaptation: null };
      return {
        coaching: longitudinal.coaching || coaching,
        progress: longitudinal.progress || longitudinal,
        adaptation: longitudinal.coaching_adaptation || null,
      };
    }

    async createSession(text, title, source, engine) {
      const articleId = ID(), sessionId = ID(), created = now();
      const article = {
        id: articleId, title: (title || "").trim().slice(0, 200) || "Untitled article",
        source: (source || "").trim().slice(0, 200) || null, text,
        segmenter_version: "seg-1", created_at: created, segments: segmentArticle(text, articleId),
      };
      article.text_sha256 = Array.from(new Uint8Array(await crypto.subtle.digest(
        "SHA-256", new TextEncoder().encode(text))), (byte) => byte.toString(16).padStart(2, "0")).join("");
      const session = {
        id: sessionId, article_id: articleId, engine_default: engine, state: "READY",
        segmenter_version: article.segmenter_version, model_version: "m12.1",
        created_at: created, updated_at: created, rev: 0, attempt_ids: [], current_run_id: null,
      };
      await this.store.saveArticle(article);
      await this.store.createSession(session);
      await this.store.appendEvent(sessionId, "session_created", { article_id: articleId, engine });
      this.sessionId = sessionId;
      this.article = article;
      return this.snapshot(sessionId);
    }

    async reopen(sessionId) {
      const session = await this.store.loadSession(sessionId);
      if (session.state === "READING") transition(session, "INTERRUPTED");
      if (session.state === "INTERRUPTED") transition(session, "PAUSED");
      await this.store.saveSession(session);
      this.sessionId = sessionId;
      this.article = await this.store.loadArticle(session.article_id);
      return this.snapshot(sessionId);
    }

    async startAttempt(sessionId, capture) {
      const session = await this.store.loadSession(sessionId);
      const article = await this.store.loadArticle(session.article_id);
      const segment = article.segments.find((item) => item.id === capture.segmentId && item.readable);
      if (!segment) throw new Error("That sentence is not part of this article.");
      const attempts = await this.store.attempts(sessionId);
      const attempt = {
        id: capture.attemptId, session_id: sessionId, segment_id: segment.id,
        attempt_number: 1 + attempts.filter((item) => item.segment_id === segment.id).length,
        target_text: segment.text,
        capture: {
          run_id: capture.runId, sample_rate: capture.sampleRate, start_sample: capture.startSample,
          end_sample: null, end_reason: null, wall_clock_start: new Date(capture.wallClockStart).toISOString(),
          wall_clock_end: null,
        },
        audio: null, state: "CAPTURING", error: null, job_ids: [], user_disposition: null,
        created_at: now(), updated_at: now(),
      };
      session.attempt_ids.push(attempt.id);
      if (session.state !== "READING") transition(session, "READING");
      session.current_run_id = capture.runId;
      await this.store.saveSession(session);
      await this.store.saveAttempt(attempt);
      await this.store.appendEvent(sessionId, "attempt_started", { attempt_id: attempt.id, segment_id: segment.id });
    }

    async submitAttempt(sessionId, capture, wav) {
      const attempt = await this.store.loadAttempt(sessionId, capture.attemptId);
      const session = await this.store.loadSession(sessionId);
      const article = await this.store.loadArticle(session.article_id);
      attempt.capture = {
        run_id: capture.runId, sample_rate: capture.sampleRate, start_sample: capture.startSample,
        end_sample: capture.endSample, end_reason: capture.endReason,
        wall_clock_start: new Date(capture.wallClockStart).toISOString(),
        wall_clock_end: new Date(capture.wallClockEnd).toISOString(),
      };
      const recording = new Blob([wav], { type: "audio/wav" });
      await this.store.saveRecording(sessionId, attempt.id, recording, "recording.wav");
      attempt.audio = {
        duration_ms: (capture.endSample - capture.startSample) * 1000 / capture.sampleRate,
        analysis_duration_ms: (capture.endSample - capture.startSample) * 1000 / capture.sampleRate,
        analysis: true,
      };
      attempt.state = attempt.audio.duration_ms < 300 ? "TOO_SHORT" : "RECORDED";
      attempt.updated_at = now();
      await this.store.saveAttempt(attempt);
      if (attempt.state === "TOO_SHORT") {
        await this.store.appendEvent(sessionId, "attempt_too_short", { attempt_id: attempt.id });
        await this.refreshDerived(sessionId);
        return this.snapshot(sessionId);
      }
      return this._analyzeStored(sessionId, attempt, article, recording);
    }

    async _analyzeStored(sessionId, attempt, article, recording) {
      const session = await this.store.loadSession(sessionId);
      const engines = ["openpronounce", "wav2vec2_raw"];
      const primaryEngine = session.engine_default;
      const stamp = now();
      const jobs = {};
      attempt.state = "QUEUED";
      attempt.error = null;
      for (const engineId of engines) {
        const job = {
          id: ID(), session_id: sessionId, attempt_id: attempt.id, segment_id: attempt.segment_id,
          engine_id: engineId, kind: engineId === primaryEngine ? "primary" : "comparison", state: "QUEUED",
          try_count: 1, enqueued_at: stamp, started_at: null, finished_at: null, error: null,
          target_confirmation: { state: "NOT_CHECKED" }, pipeline_versions: null, updated_at: stamp,
          state_history: ["queued"],
        };
        attempt.job_ids.push(job.id);
        jobs[engineId] = job;
        await this.store.saveJob(job);
      }
      await this.store.saveAttempt(attempt);
      attempt.state = "ANALYZING";
      for (const job of Object.values(jobs)) {
        job.state = "RUNNING";
        job.started_at = now();
        job.updated_at = job.started_at;
        job.state_history.push("analyzing");
        await this.store.saveJob(job);
      }
      await this.store.saveAttempt(attempt);

      const form = new FormData();
      form.append("target_text", attempt.target_text);
      const readable = article.segments.filter((segment) => segment.readable);
      const segmentIndex = readable.findIndex((segment) => segment.id === attempt.segment_id);
      form.append("article_sentences", JSON.stringify(readable.slice(Math.max(0, segmentIndex - 3), segmentIndex)
        .concat(readable.slice(segmentIndex + 1, segmentIndex + 4)).map((segment) => segment.text)));
      form.append("audio", recording, "recording.wav");
      const started = performance.now();
      try {
        const response = await fetch("/api/stateless/analyze", { method: "POST", body: form });
        const payload = await response.json();
        if (!response.ok) {
          const error = new Error(payload?.error?.message || `HTTP ${response.status}`);
          error.code = payload?.error?.code || "analysis_failed";
          throw error;
        }
        this.lastTimings = { request_ms: performance.now() - started, persistence_ms: 0 };
        for (const engineId of engines) {
          const engineResult = payload.analyses?.[engineId];
          const job = jobs[engineId];
          const readerResult = engineResult?.reader;
          job.engine_result = engineResult?.result || null;
          job.engine_evidence = engineResult?.evidence || null;
          job.result = readerResult?.state === "ok" ? readerResult.result : null;
          job.error = engineResult?.error || readerResult?.error || null;
          if (engineResult?.state === "ok" && readerResult?.state === "ok") {
            job.state = "SUCCEEDED";
            job.target_confirmation_own = readerResult.target_confirmation;
            job.target_confirmation = readerResult.target_confirmation;
            job.boundary = readerResult.boundary;
            job.finished_at = now();
            job.feedback = compactFeedback(readerResult.evidence);
            job.pipeline_versions = {
              coach: readerResult.evidence?.coach?.version,
              reduction: readerResult.evidence?.reduction?.version,
              boundary: readerResult.boundary?.version,
              fluency: readerResult.evidence?.fluency?.version,
            };
            await this.store.saveView(job, readerResult.evidence);
            job.state_history.push("complete");
          } else {
            job.state = "FAILED";
            job.error ||= { code: "analysis_failed", message: "This engine could not complete the analysis." };
            job.finished_at = now();
            job.state_history.push("failed");
          }
          job.updated_at = job.finished_at;
          await this.store.saveJob(job);
        }
        const primary = jobs[primaryEngine];
        const other = jobs[engines.find((engineId) => engineId !== primaryEngine)];
        if (primary.state === "SUCCEEDED" && other.state === "SUCCEEDED") {
          primary.target_confirmation = combineIdentity(primary.target_confirmation_own, other.target_confirmation_own);
          const differs = primary.boundary.state !== other.boundary.state ||
            Math.abs(primary.boundary.cut_ms - other.boundary.cut_ms) > 200 ||
            primary.boundary.boundary_confidence !== other.boundary.boundary_confidence;
          other.boundary = {
            ...structuredClone(primary.boundary),
            other_engine: {
              engine: other.engine_id, state: other.boundary.state, cut_ms: other.boundary.cut_ms,
              reasons: other.boundary.reasons, defensible: other.boundary.defensible,
              boundary_confidence: other.boundary.boundary_confidence, differs,
              note: "Both local listening models share one acoustic model; agreement is not independent confirmation.",
            },
          };
          await this.store.saveJob(primary);
          await this.store.saveJob(other);
        }
        attempt.state = primary.state === "SUCCEEDED" ? "ANALYZED" : "ANALYSIS_FAILED";
        attempt.error = primary.error;
        attempt.updated_at = now();
        await this.store.saveAttempt(attempt);
        await this.store.appendEvent(sessionId, "analysis_done", { attempt_id: attempt.id, request_id: payload.request_id });
      } catch (error) {
        for (const job of Object.values(jobs)) {
          if (job.state === "RUNNING") {
            job.state = "FAILED";
            job.error = { code: error.code || "analysis_failed", message: error.message || "Analysis failed." };
            job.finished_at = now();
            job.updated_at = job.finished_at;
            job.state_history.push("failed");
            await this.store.saveJob(job);
          }
        }
        attempt.state = "ANALYSIS_FAILED";
        attempt.error = { code: error.code || "analysis_failed", message: error.message || "Analysis failed." };
        attempt.updated_at = now();
        await this.store.saveAttempt(attempt);
        await this.store.appendEvent(sessionId, "analysis_failed", { attempt_id: attempt.id });
        error.retryable = false;
        throw error;
      }
      const savedAt = performance.now();
      await this.refreshDerived(sessionId);
      this.lastTimings.persistence_ms = performance.now() - savedAt;
      return this.snapshot(sessionId);
    }

    async disposition(sessionId, attemptId, value) {
      if (!["kept", "discarded", "rerecord_requested"].includes(value)) throw new Error("Invalid attempt disposition.");
      const attempt = await this.store.loadAttempt(sessionId, attemptId);
      attempt.user_disposition = value;
      attempt.updated_at = now();
      await this.store.saveAttempt(attempt);
      await this.store.appendEvent(sessionId, "disposition", { attempt_id: attemptId, value });
      await this.refreshDerived(sessionId);
      return this.snapshot(sessionId);
    }

    async retry(sessionId, attemptId) {
      const attempt = await this.store.loadAttempt(sessionId, attemptId);
      if (attempt.state !== "ANALYSIS_FAILED") throw new Error("Only a failed analysis can be retried.");
      const saved = await this.store.loadRecording(sessionId, attemptId);
      if (!saved) throw new Error("The original browser recording is missing.");
      const article = await this.store.loadArticle((await this.store.loadSession(sessionId)).article_id);
      return this._analyzeStored(sessionId, attempt, article, saved.blob);
    }

    async action(sessionId, action) {
      const session = await this.store.loadSession(sessionId);
      const target = { start: "READING", resume: "READING", pause: "PAUSED", stop: "STOPPED", finish: "FINISHED" }[action];
      if (!target) throw new Error("Unknown reading action.");
      transition(session, target);
      await this.store.saveSession(session);
      await this.store.appendEvent(sessionId, "session_" + action);
      if (action === "finish") await this.refreshDerived(sessionId);
      return this.snapshot(sessionId);
    }

    async createPractice(sentences, title, targetId, sourceSessionId) {
      const text = sentences.join("\n\n");
      const snapshot = await this.createSession(text, `Practice: ${title || ""}`.slice(0, 200),
        "What to practise now", (await this.store.loadSession(sourceSessionId)).engine_default);
      if (targetId && root.PronounceReaderLongitudinal?.newPracticeRecord) {
        const article = await this.store.loadArticle(snapshot.session.article_id);
        const record = root.PronounceReaderLongitudinal.newPracticeRecord({
          id: ID(), created_at: now(), session_id: snapshot.session.id, article,
          target_id: targetId, source_session_id: sourceSessionId,
        });
        await this.longitudinal.savePractice(record);
      }
      return snapshot;
    }

    async loadRecording(sessionId, attemptId) {
      const row = await this.store.loadRecording(sessionId, attemptId);
      if (!row) throw new Error("The original browser recording is unavailable.");
      return row.blob;
    }

    async comparison(sessionId, attemptId) {
      const attempt = await this.store.loadAttempt(sessionId, attemptId);
      const results = [];
      for (const jobId of attempt.job_ids) {
        const job = await this.store.loadJob(sessionId, attemptId, jobId);
        if (job.kind === "primary" || job.kind === "comparison") {
          results.push({
            engine: job.engine_id,
            label: this.status.engines.find((engine) => engine.id === job.engine_id)?.label || job.engine_id,
            state: job.state,
            identity: job.target_confirmation?.state,
            boundary: job.boundary?.state,
            error: job.error?.message,
            view: await this.store.loadView(job),
          });
        }
      }
      return results.sort((a, b) => ["wav2vec2_raw", "openpronounce"].indexOf(a.engine) -
        ["wav2vec2_raw", "openpronounce"].indexOf(b.engine));
    }

    async play(sessionId, attemptId, playMs = null) {
      const blob = await this.loadRecording(sessionId, attemptId);
      const context = new AudioContext();
      const buffer = await context.decodeAudioData(await blob.arrayBuffer());
      if (context.state === "suspended") await context.resume();
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      const start = playMs ? Math.min(buffer.duration, Math.max(0, playMs[0] / 1000)) : 0;
      const end = playMs ? Math.min(buffer.duration, playMs[1] / 1000) : buffer.duration;
      source.start(0, start, Math.max(0, end - start));
      source.onended = () => context.close();
      return true;
    }

    async snapshot(sessionId = this.sessionId) {
      const session = await this.store.loadSession(sessionId);
      const article = await this.store.loadArticle(session.article_id);
      const attempts = await this.store.attempts(sessionId);
      const jobs = {};
      for (const attempt of attempts) {
        for (const id of attempt.job_ids) jobs[id] = await this.store.loadJob(sessionId, attempt.id, id);
        const primary = [...attempt.job_ids].reverse().map((id) => jobs[id]).find((job) => job?.kind === "primary");
        attempt.status = attemptStatus(attempt, primary, session.engine_default);
        for (const id of attempt.job_ids) {
          const job = jobs[id];
          if (!job) continue;
          job.boundary = boundaryFor(job, attempt);
        }
      }
      const bySegment = Object.fromEntries(article.segments.map((segment) => [segment.id, []]));
      for (const attempt of attempts) (bySegment[attempt.segment_id] ||= []).push(attempt);
      const segmentStates = {};
      for (const segment of article.segments) {
        const last = bySegment[segment.id].at(-1);
        if (!last) segmentStates[segment.id] = "UNREAD";
        else if (last.state === "CAPTURING") segmentStates[segment.id] = "RECORDING";
        else if (["RECORDED", "QUEUED", "ANALYZING"].includes(last.state)) segmentStates[segment.id] = "PROCESSING";
        else if (["ANALYSIS_FAILED", "TOO_SHORT", "REJECTED", "INTERRUPTED"].includes(last.state)) {
          segmentStates[segment.id] = "NEEDS_ATTENTION";
        } else {
          const primary = [...last.job_ids].reverse().map((id) => jobs[id]).find((job) => job?.kind === "primary");
          segmentStates[segment.id] = ["AMBIGUOUS", "MISMATCH"].includes(primary?.target_confirmation?.state) &&
            last.user_disposition !== "kept" ? "NEEDS_ATTENTION" : "FEEDBACK_READY";
        }
      }
      return {
        unchanged: false, rev: session.rev, session,
        article: { id: article.id, title: article.title, source: article.source,
          segments: article.segments, segmenter_version: article.segmenter_version },
        attempts, jobs, segment_states: segmentStates,
        queue: { queued_primary: attempts.filter((attempt) =>
          ["RECORDED", "QUEUED", "ANALYZING"].includes(attempt.state)).length,
        queued_comparison: 0, running: attempts.some((attempt) => attempt.state === "ANALYZING") ? { kind: "primary" } : null },
        summary: await this.store.loadSummary(sessionId),
        coaching: await this.store.loadCoaching(sessionId),
        reading_feedback: await this.store.loadReadingFeedback(sessionId),
      };
    }

    async refreshDerived(sessionId) {
      const session = await this.store.loadSession(sessionId);
      const article = await this.store.loadArticle(session.article_id);
      const attempts = await this.store.attempts(sessionId);
      if (attempts.some((attempt) => ["CAPTURING", "RECORDED", "QUEUED", "ANALYZING"].includes(attempt.state))) return;
      const attemptRows = [];
      for (const attempt of attempts) {
        const jobs = await Promise.all(attempt.job_ids.map((id) =>
          this.store.loadJob(sessionId, attempt.id, id)));
        const job = [...jobs].reverse().find((item) => item.kind === "primary") || null;
        const status = attemptStatus(attempt, job, session.engine_default);
        const view = job?.state === "SUCCEEDED" ? await this.store.loadView(job) : null;
        attemptRows.push({ attempt, job, status, view, segment: article.segments.find((item) => item.id === attempt.segment_id) });
      }
      const usedRows = [];
      for (const segment of article.segments.filter((item) => item.readable)) {
        const row = [...attemptRows].reverse().find((item) =>
          item.attempt.segment_id === segment.id && item.status.summary === null);
        if (row) {
          if (!row.view) throw new Error(`Browser analysis view is missing for attempt ${row.attempt.id}.`);
          usedRows.push({ ...row, session, article, segment });
        }
      }
      if (root.PronounceReaderCoaching?.buildSummary) {
        const summary = await root.PronounceReaderCoaching.buildSummary(
          this.store, session, article, attemptRows,
        );
        summary.stale = session.state !== "FINISHED";
        await this.store.saveSummary(sessionId, summary);
      }
      if (root.PronounceReaderCoaching?.buildReadingFeedback) {
        const summary = await this.store.loadSummary(sessionId);
        const readingInputs = usedRows.map((row) => ({ ...row, session, article }));
        Object.assign(readingInputs, {
          coverage: summary?.coverage || {}, session_id: sessionId, article_title: article.title,
          generated_at: now(),
        });
        const readingFeedback = await root.PronounceReaderCoaching.buildReadingFeedback(readingInputs);
        if (readingFeedback) await this.store.saveReadingFeedback(sessionId, readingFeedback);
      }
      if (root.PronounceReaderCoaching?.runCoaching) {
        const history = await this._eligibleHistory();
        const coaching = await root.PronounceReaderCoaching.runCoaching(history.inputs, history.exclusions);
        await this.store.saveCoaching(sessionId, coaching);
        if (root.PronounceReaderCoaching.update) {
          await root.PronounceReaderCoaching.update({
            inputs: history.inputs, sessions: history.sessions, longitudinalStore: this.longitudinal, coaching,
          });
        }
      }
      if (session.state === "FINISHED" && (await this.store.loadSummary(sessionId))) {
        transition(session, "SUMMARIZED");
        await this.store.saveSession(session);
      }
    }

    async _eligibleHistory() {
      const sessions = [];
      const inputs = [];
      const exclusions = {};
      for (const sessionId of await this.store.sessionIds()) {
        const session = await this.store.loadSession(sessionId);
        const article = await this.store.loadArticle(session.article_id);
        sessions.push({ session, article });
        for (const attempt of await this.store.attempts(sessionId)) {
          const primary = await Promise.all(attempt.job_ids.map((id) =>
            this.store.loadJob(sessionId, attempt.id, id))).then((all) =>
            [...all].reverse().find((job) => job.kind === "primary"));
          const status = attemptStatus(attempt, primary, session.engine_default);
          if (status.summary !== null) {
            const reason = status.summary_code || "not_eligible";
            exclusions[reason] = (exclusions[reason] || 0) + 1;
            continue;
          }
          const view = await this.store.loadView(primary);
          if (!view || view.state === "boundary_withheld" || !view.coach?.observations?.length) {
            exclusions.no_view = (exclusions.no_view || 0) + 1;
            continue;
          }
          inputs.push({ session, article, attempt, job: primary, view, status });
        }
      }
      return { sessions, inputs, exclusions };
    }
  }

  root.PronounceReaderBrowser = { BrowserReader, segmentArticle, attemptStatus };
  if (typeof module === "object" && module.exports) module.exports = { BrowserReader, segmentArticle, attemptStatus };
})(globalThis);
