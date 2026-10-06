import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [chromePath, baseUrl, wavPath] = process.argv.slice(2);
if (!chromePath || !baseUrl || !wavPath) {
  throw new Error("Usage: node browser_stateless_analysis.mjs <chrome-path> <base-url> <wav-path>");
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const launchBrowser = () => {
  const profile = mkdtempSync(join(tmpdir(), "pronounce-stateless-browser-"));
  const chrome = spawn(chromePath, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    "--disable-background-networking", `--user-data-dir=${profile}`, "--remote-debugging-port=0", "about:blank",
  ], { stdio: "ignore" });
  return { profile, chrome };
};

async function connect(url) {
  const websocket = new WebSocket(url);
  await new Promise((resolveOpen, rejectOpen) => {
    websocket.onopen = resolveOpen;
    websocket.onerror = rejectOpen;
  });
  let id = 0;
  const pending = new Map();
  websocket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      pending.get(message.id)(message);
      pending.delete(message.id);
    }
  };
  const send = (method, params = {}) => new Promise((resolveResponse, rejectResponse) => {
    const messageId = ++id;
    pending.set(messageId, (message) => message.error
      ? rejectResponse(new Error(`${method}: ${message.error.message}`))
      : resolveResponse(message.result));
    websocket.send(JSON.stringify({ id: messageId, method, params }));
  });
  return { send, close: () => websocket.close() };
}

async function startPage(browser, url) {
  const activePort = join(browser.profile, "DevToolsActivePort");
  for (let i = 0; i < 100 && !existsSync(activePort); i++) await sleep(100);
  if (!existsSync(activePort)) throw new Error("Chrome did not start its DevTools endpoint");
  const devtoolsPort = readFileSync(activePort, "utf8").split("\n")[0];
  const version = await (await fetch(`http://127.0.0.1:${devtoolsPort}/json/version`)).json();
  const connection = await connect(version.webSocketDebuggerUrl);
  const target = await (await fetch(
    `http://127.0.0.1:${devtoolsPort}/json/new?${encodeURIComponent(url)}`,
    { method: "PUT" },
  )).json();
  const page = await connect(target.webSocketDebuggerUrl);
  await page.send("Runtime.enable");
  await page.send("DOM.enable");
  return { connection, page };
}

async function stopBrowser(browser) {
  if (browser.chrome && browser.chrome.exitCode === null && browser.chrome.signalCode === null) {
    const stopped = new Promise((resolve) => browser.chrome.once("close", resolve));
    browser.chrome.kill("SIGKILL");
    await stopped;
  }
  rmSync(browser.profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}

async function evaluate(page, expression) {
  const result = await page.send("Runtime.evaluate", {
    expression: `(async () => { ${expression} })()`,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  }
  return result.result.value;
}

let browserA;
let browserB;
let pageA;
let pageB;
try {
  browserA = launchBrowser();
  ({ page: pageA } = await startPage(browserA, baseUrl));
  await evaluate(pageA, `
    const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
    for (let i = 0; i < 300; i++) {
      if (document.querySelectorAll("#engine-select option").length && window.PronounceBrowserStore) {
        const store = await PronounceBrowserStore.BrowserStore.open();
        try {
          await store.loadSession("browser-analysis-session");
          store.close();
          return true;
        } catch (error) {
          store.close();
          if (!error || error.name !== "RecordNotFoundError") throw error;
        }
      }
      await wait(50);
    }
    throw new Error("Lab page did not initialize");
  `);

  const document = await pageA.send("DOM.getDocument");
  await evaluate(pageA, `document.querySelector('[data-tab="upload"]').click(); return true;`);
  const fileInput = await pageA.send("DOM.querySelector", {
    nodeId: document.root.nodeId,
    selector: "#file-input",
  });
  const expectedHash = createHash("sha256").update(readFileSync(wavPath)).digest("hex");
  await pageA.send("DOM.setFileInputFiles", { files: [wavPath], nodeId: fileInput.nodeId });

  const scenarios = [
    { text: "Both engines are available.", open: "ok", raw: "ok", state: "complete" },
    { text: "OpenPronounce fails.", open: "failed", raw: "ok", state: "complete" },
    { text: "Raw Wav2Vec2 fails.", open: "ok", raw: "failed", state: "complete" },
    { text: "Transport request failure.", state: "failed" },
  ];
  const saved = [];
  for (const scenario of scenarios) {
    const action = await evaluate(pageA, `
      document.getElementById("target-text").value = ${JSON.stringify(scenario.text)};
      document.getElementById("analyze-btn").click();
      return true;
    `);
    if (!action) throw new Error("Could not start the browser analysis");
    await evaluate(pageA, `
      const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
      const started = Date.now();
      while (Date.now() - started < 60000) {
        if (!document.getElementById("analyze-btn").disabled) return true;
        await wait(50);
      }
      throw new Error("Browser analysis did not finish");
    `);
    if (scenario.open === "failed") {
      await evaluate(pageA, `
        const select = document.getElementById("result-engine-select");
        select.value = "wav2vec2_raw";
        select.dispatchEvent(new Event("change"));
        return true;
      `);
    }
    const record = await evaluate(pageA, `
      const store = await PronounceBrowserStore.BrowserStore.open();
      const attempts = await store.attempts("browser-analysis-session");
      const attempt = attempts.filter((item) => item.target_text === ${JSON.stringify(scenario.text)}).at(-1);
      if (!attempt) throw new Error("Browser-local attempt missing: " +
        JSON.stringify({ attempts: attempts.map(item => item.target_text),
          error: document.getElementById("error-box").textContent,
          runStatus: document.getElementById("run-status").textContent }));
      const job = await store.loadJob(attempt.session_id, attempt.id, attempt.job_ids.at(-1));
      const recording = await store.loadRecording(attempt.session_id, attempt.id);
      const audioHash = await crypto.subtle.digest("SHA-256", await recording.blob.arrayBuffer());
      const hash = Array.from(new Uint8Array(audioHash), byte => byte.toString(16).padStart(2, "0")).join("");
      const output = {
        attempt: { id: attempt.id, state: attempt.state, result: attempt.analysis_result },
        job: { state: job.state, state_history: job.state_history, result: job.result,
          error: job.error, timings: job.timings },
        recording: { filename: recording.filename, type: recording.blob.type, size: recording.blob.size, hash },
        selectedEngine: document.getElementById("result-engine-select").value,
        resultSource: document.getElementById("result-source").textContent,
        words: document.querySelectorAll("#sentence .word").length,
      };
      store.close();
      return output;
    `);
    if (record.recording.hash !== expectedHash) throw new Error("Saved browser recording bytes changed");
    if (record.attempt.state !== scenario.state || record.job.state !== scenario.state) {
      throw new Error(`Unexpected local job state for ${scenario.text}: ${record.job.state}`);
    }
    if (scenario.state === "complete") {
      if (record.job.result.analyses.openpronounce.state !== scenario.open
          || record.job.result.analyses.wav2vec2_raw.state !== scenario.raw) {
        throw new Error(`Engine result was not preserved independently for ${scenario.text}`);
      }
      if (JSON.stringify(record.attempt.result) !== JSON.stringify(record.job.result)
          || ["openpronounce", "wav2vec2_raw"].some((id) =>
            record.job.result.analyses[id].evidence?.engine?.id !== id)) {
        throw new Error("The attempt/job did not retain the original engine-specific response");
      }
      const states = record.job.state_history.map((entry) => entry.state);
      if (JSON.stringify(states) !== JSON.stringify(["queued", "uploading", "analyzing", "complete"])) {
        throw new Error(`Unexpected job lifecycle: ${JSON.stringify(states)}`);
      }
      if (!record.resultSource.includes("Browser-local recording") || !record.words) {
        throw new Error(`Existing feedback view did not display browser-local evidence: ${JSON.stringify(record)}`);
      }
      if (!Number.isFinite(record.job.timings.request_ms) || !Number.isFinite(record.job.timings.persistence_ms)) {
        throw new Error("Browser request/local persistence timings were not recorded");
      }
    } else if (record.job.error?.code !== "server_error") {
      throw new Error("Failed request did not retain a meaningful local failure state");
    }
    saved.push(record);
  }

  await pageA.send("Page.reload");
  await evaluate(pageA, `
    const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
    for (let i = 0; i < 300; i++) {
      if (document.querySelectorAll("#history-list [data-local-attempt]").length === 4) return true;
      await wait(50);
    }
    throw new Error("Browser-local history did not survive page refresh");
  `);
  const reopened = await evaluate(pageA, `
    const items = [...document.querySelectorAll("#history-list [data-local-attempt]")];
    items.find(item => item.textContent.includes("Both engines are available.")).click();
    const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
    for (let i = 0; i < 300 && document.getElementById("results-panel").hidden; i++) await wait(50);
    const store = await PronounceBrowserStore.BrowserStore.open();
    const attempts = await store.attempts("browser-analysis-session");
    const output = { count: attempts.length, states: attempts.map(attempt => attempt.state),
      visible: !document.getElementById("results-panel").hidden,
      target: attempts[0].analysis_result.target_text,
      source: document.getElementById("result-source").textContent,
      words: document.querySelectorAll("#sentence .word").length };
    const browserB = await PronounceBrowserStore.BrowserStore.open("pronounce-browser-b-" + crypto.randomUUID());
    output.browserBAttempts = (await browserB.attempts("browser-analysis-session")).length;
    output.browserBAudio = await browserB.loadRecording(
      "browser-analysis-session", attempts[0].id,
    );
    browserB.close();
    store.close();
    return output;
  `);
  if (reopened.count !== 4 || !reopened.visible || !reopened.words
      || !reopened.source.includes("Browser-local recording")
      || reopened.browserBAttempts !== 0 || reopened.browserBAudio !== null) {
    throw new Error(`IndexedDB refresh/isolation failed: ${JSON.stringify(reopened)}`);
  }

  browserB = launchBrowser();
  ({ page: pageB } = await startPage(browserB, baseUrl));
  const isolated = await evaluate(pageB, `
    const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
    for (let i = 0; i < 300; i++) {
      if (window.PronounceBrowserStore) {
        const store = await PronounceBrowserStore.BrowserStore.open();
        const attempts = await store.attempts("browser-analysis-session");
        store.close();
        return attempts.length;
      }
      await wait(50);
    }
    throw new Error("Second browser profile did not initialize IndexedDB");
  `);
  if (isolated !== 0) throw new Error("A separate browser profile read Browser A's analysis history");
  console.log(JSON.stringify({
    status: "ok",
    requests: saved.length,
    timings: saved.filter((item) => item.job.state === "complete").map((item) => item.job.timings),
    reopened,
    secondBrowserAttempts: isolated,
  }));
} finally {
  pageA?.close();
  pageB?.close();
  if (browserA) await stopBrowser(browserA);
  if (browserB) await stopBrowser(browserB);
}
