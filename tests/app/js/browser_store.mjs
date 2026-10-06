import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const chromePath = process.argv[2];
if (!chromePath) throw new Error("Usage: node browser_store.mjs <chrome-path>");
const here = dirname(fileURLToPath(import.meta.url));
const staticDir = resolve(here, "../../../src/pronunciation_lab/app/static");
const profile = mkdtempSync(join(tmpdir(), "pronounce-browser-store-"));
const server = createServer((request, response) => {
  if (request.url === "/browser-store.js") {
    response.writeHead(200, { "Content-Type": "text/javascript; charset=utf-8" });
    response.end(readFileSync(join(staticDir, "browser-store.js")));
  } else if (request.url === "/") {
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end(readFileSync(join(here, "browser_store_page.html")));
  } else {
    response.writeHead(404);
    response.end();
  }
});

const sleep = (ms) => new Promise((resolveSleep) => setTimeout(resolveSleep, ms));

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

let chrome;
let browser;
let page;
try {
  await new Promise((resolveListen, rejectListen) => {
    server.once("error", rejectListen);
    server.listen(0, "127.0.0.1", resolveListen);
  });
  const { port } = server.address();
  chrome = spawn(chromePath, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    "--disable-background-networking", `--user-data-dir=${profile}`, "--remote-debugging-port=0", "about:blank",
  ], { stdio: "ignore" });
  const activePort = join(profile, "DevToolsActivePort");
  for (let i = 0; i < 100 && !existsSync(activePort); i++) await sleep(100);
  if (!existsSync(activePort)) throw new Error("Chrome did not start its DevTools endpoint");
  const devtoolsPort = readFileSync(activePort, "utf8").split("\n")[0];
  const version = await (await fetch(`http://127.0.0.1:${devtoolsPort}/json/version`)).json();
  browser = await connect(version.webSocketDebuggerUrl);
  const url = `http://127.0.0.1:${port}/`;
  const target = await (await fetch(`http://127.0.0.1:${devtoolsPort}/json/new?${encodeURIComponent(url)}`,
    { method: "PUT" })).json();
  page = await connect(target.webSocketDebuggerUrl);
  await page.send("Runtime.enable");
  const evaluated = await page.send("Runtime.evaluate", {
    expression: `(async () => {
      for (let i = 0; i < 200; i++) {
        const text = document.getElementById("result")?.textContent;
        if (text && text !== "pending") return JSON.parse(text);
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      throw new Error("Timed out waiting for BrowserStore IndexedDB assertions");
    })()`,
    awaitPromise: true,
    returnByValue: true,
  });
  if (evaluated.exceptionDetails) {
    throw new Error(evaluated.exceptionDetails.exception?.description || evaluated.exceptionDetails.text);
  }
  if (evaluated.result.value.status !== "ok") throw new Error(evaluated.result.value.message);
  console.log("ok BrowserStore native IndexedDB recording/result round-trip and isolation");
} finally {
  page?.close();
  browser?.close();
  if (chrome && chrome.exitCode === null && chrome.signalCode === null) {
    const stopped = new Promise((resolveStop) => chrome.once("close", resolveStop));
    chrome.kill("SIGKILL");
    await stopped;
  }
  server.close();
  rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}
