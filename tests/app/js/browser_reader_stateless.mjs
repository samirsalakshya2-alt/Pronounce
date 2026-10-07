import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [chromePath, baseUrl, wavPath] = process.argv.slice(2);
if (!chromePath || !baseUrl || !wavPath) {
  throw new Error("Usage: browser_reader_stateless.mjs <chrome-path> <base-url> <wav-path>");
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function launchBrowser() {
  const profile = mkdtempSync(join(tmpdir(), "pronounce-reader-stateless-"));
  const chrome = spawn(chromePath, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    "--disable-background-networking", `--user-data-dir=${profile}`, "--remote-debugging-port=0", "about:blank",
  ], { stdio: "ignore" });
  return { profile, chrome };
}
async function stopBrowser(browser) {
  if (browser.chrome.exitCode === null && browser.chrome.signalCode === null) {
    const exited = new Promise((resolve) => browser.chrome.once("close", resolve));
    browser.chrome.kill("SIGTERM");
    await Promise.race([exited, sleep(5000)]);
    if (browser.chrome.exitCode === null && browser.chrome.signalCode === null) browser.chrome.kill("SIGKILL");
  }
  rmSync(browser.profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
async function connect(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let nextId = 0;
  const pending = new Map();
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      pending.get(message.id)(message);
      pending.delete(message.id);
    }
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, (message) => message.error
      ? reject(new Error(`${method}: ${message.error.message}`)) : resolve(message.result));
    socket.send(JSON.stringify({ id, method, params }));
  });
  return { send, close: () => socket.close() };
}
async function openPage(browser, url) {
  const activePort = join(browser.profile, "DevToolsActivePort");
  for (let i = 0; i < 100 && !existsSync(activePort); i++) await sleep(100);
  if (!existsSync(activePort)) throw new Error("Chrome did not start its DevTools endpoint");
  const port = readFileSync(activePort, "utf8").split("\n")[0];
  const target = await (await fetch(
    `http://127.0.0.1:${port}/json/new?${encodeURIComponent(url)}`, { method: "PUT" },
  )).json();
  const page = await connect(target.webSocketDebuggerUrl);
  await page.send("Runtime.enable");
  await page.send("Page.enable");
  return page;
}
async function evaluate(page, code) {
  const result = await page.send("Runtime.evaluate", {
    expression: `(async () => { ${code} })()`, awaitPromise: true, returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  }
  return result.result.value;
}

const wav = readFileSync(wavPath);
const wavBase64 = wav.toString("base64");
const wavHash = createHash("sha256").update(wav).digest("hex");
const browserA = launchBrowser();
let browserB;
let pageA;
let pageB;
try {
  pageA = await openPage(browserA, baseUrl);
  const runA = async (expression) => evaluate(pageA, expression);
  await runA(`
    const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
    for (let i = 0; i < 300; i++) {
      if (window.__reader?.status && window.__reader.browser && !document.getElementById("entry").hidden) {
        if (document.title !== "Pronounce — Reader") throw new Error("Hosted root did not serve the Reader UI.");
        if (!window.__reader.status.stateless_only) throw new Error("Hosted Reader did not detect stateless mode.");
        return true;
      }
      await wait(50);
    }
    throw new Error("Hosted Reader did not initialize at /.");
  `);

  const results = [];
  for (const scenario of [
    { text: "Both engines work.", open: "ok", raw: "ok", attempt: "ANALYZED" },
    { text: "OpenPronounce fails.", open: "failed", raw: "ok", attempt: "ANALYZED" },
    { text: "Raw Wav2Vec2 fails.", open: "ok", raw: "failed", attempt: "ANALYSIS_FAILED" },
  ]) {
    const result = await runA(`
      const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
      const $ = (id) => document.getElementById(id);
      const state = window.__reader;
      if (!state.sessionId) {
        $("entry-text").value = ${JSON.stringify(scenario.text)};
        $("entry-title").value = "Browser-owned article";
        $("entry-start").click();
        for (let i = 0; i < 300 && !state.sessionId; i++) await wait(50);
      } else {
        const snapshot = await state.browser.createSession(
          ${JSON.stringify(scenario.text)}, "Browser-owned article", null, "wav2vec2_raw");
        state.sessionId = snapshot.session.id;
        state.segments = snapshot.article.segments;
        state.segById = Object.fromEntries(state.segments.map((item) => [item.id, item]));
      }
      const sessionId = state.sessionId;
      if (!sessionId) throw new Error("Reader session was not created: " + $("entry-error").textContent);
      const session = await state.browser.store.loadSession(sessionId);
      const segment = state.segments[0];
      const capture = { attemptId: crypto.randomUUID().replaceAll("-", ""), runId: crypto.randomUUID().replaceAll("-", ""),
        segmentId: segment.id, sampleRate: 16000, startSample: 0, endSample: 16000,
        endReason: "stopped", wallClockStart: Date.now(), wallClockEnd: Date.now() + 1000 };
      await state.browser.startAttempt(sessionId, capture);
      const bytes = Uint8Array.from(atob(${JSON.stringify(wavBase64)}), (c) => c.charCodeAt(0));
      const snapshot = await state.browser.submitAttempt(sessionId, capture, new Blob([bytes], { type: "audio/wav" }));
      state.snap = snapshot;
      window.ReaderFeedback.update(state, snapshot);
      const attempt = await state.browser.store.loadAttempt(sessionId, capture.attemptId);
      const jobs = await Promise.all(attempt.job_ids.map((id) =>
        state.browser.store.loadJob(sessionId, attempt.id, id)));
      const rows = {};
      for (const job of jobs) rows[job.engine_id] = job;
      const recording = await state.browser.store.loadRecording(sessionId, attempt.id);
      const digest = await crypto.subtle.digest("SHA-256", await recording.blob.arrayBuffer());
      return {
        sessionId, attempt: attempt.state,
        openState: rows.openpronounce.state, rawState: rows.wav2vec2_raw.state,
        openResult: rows.openpronounce.engine_result?.status || rows.openpronounce.engine_result?.state || null,
        rawResult: rows.wav2vec2_raw.engine_result?.status || rows.wav2vec2_raw.engine_result?.state || null,
        hasSeparateEvidence: rows.openpronounce.engine_evidence?.engine?.id === "openpronounce"
          && rows.wav2vec2_raw.engine_evidence?.engine?.id === "wav2vec2_raw",
        targetState: rows.wav2vec2_raw.target_confirmation?.state || null,
        hasView: !!await state.browser.store.loadView(rows.wav2vec2_raw),
        audioHash: Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join(""),
        requestMs: state.browser.lastTimings?.request_ms,
        persistenceMs: state.browser.lastTimings?.persistence_ms,
      };
    `);
    if (result.attempt !== scenario.attempt) throw new Error(`Attempt lifecycle mismatch: ${JSON.stringify(result)}`);
    if (result.openResult !== scenario.open || result.rawResult !== scenario.raw) {
      throw new Error(`Engine failure isolation mismatch: ${JSON.stringify(result)}`);
    }
    if (!result.hasSeparateEvidence || result.audioHash !== wavHash || !result.targetState) {
      throw new Error(`Browser-local result/audio persistence failed: ${JSON.stringify(result)}`);
    }
    if (scenario.open === "ok" && scenario.raw === "ok" && !result.hasView) {
      throw new Error(`Successful M7 Reader view was not persisted: ${JSON.stringify(result)}`);
    }
    if (!(Number.isFinite(result.requestMs) && result.requestMs > 0
        && Number.isFinite(result.persistenceMs) && result.persistenceMs >= 0)) {
      throw new Error(`Browser timing was not captured: ${JSON.stringify(result)}`);
    }
    results.push(result);
  }

  await pageA.send("Page.reload");
  const reopened = await evaluate(pageA, `
    const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
    for (let i = 0; i < 300 && !window.__reader?.browser; i++) await wait(50);
    for (let i = 0; i < 300 && !window.__reader?.snap; i++) await wait(50);
    const store = await PronounceBrowserStore.BrowserStore.open();
    const sessions = await store.sessionIds();
    const currentSession = new URLSearchParams(location.search).get("session");
    const attempts = await store.attempts(currentSession);
    const attempt = attempts[0];
    const job = await store.loadJob(currentSession, attempt.id, attempt.job_ids.at(-1));
    const recording = await store.loadRecording(currentSession, attempt.id);
    const bytes = new Uint8Array(await recording.blob.arrayBuffer());
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
      (byte) => byte.toString(16).padStart(2, "0")).join("");
    const output = { sessions: sessions.length, attempts: attempts.length, jobState: job.state,
      audioHash: hash, hasResult: !!job.engine_result, readerVisible: !document.getElementById("reader").hidden,
      renderedAttempts: window.__reader.snap?.attempts.length || 0 };
    store.close();
    return output;
  `);
  if (reopened.sessions !== 3 || reopened.attempts !== 1 || reopened.audioHash !== wavHash
      || !reopened.hasResult || !reopened.readerVisible || reopened.renderedAttempts !== 1) {
    throw new Error(`IndexedDB close/reopen persistence failed: ${JSON.stringify(reopened)}`);
  }

  browserB = launchBrowser();
  pageB = await openPage(browserB, baseUrl);
  const isolated = await evaluate(pageB, `
    const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
    for (let i = 0; i < 300 && !window.__reader?.browser; i++) await wait(50);
    const store = await PronounceBrowserStore.BrowserStore.open();
    const output = { sessions: await store.sessionIds(),
      attempts: await store.attempts("does-not-exist") };
    store.close();
    return output;
  `);
  if (isolated.sessions.length || isolated.attempts.length) {
    throw new Error(`Separate browser profile saw Browser A's history: ${JSON.stringify(isolated)}`);
  }
  console.log(JSON.stringify({ status: "ok", results, reopened, isolatedBrowserSessions: isolated.sessions.length }));
} finally {
  pageA?.close();
  pageB?.close();
  await stopBrowser(browserA);
  if (browserB) await stopBrowser(browserB);
}
