// M10 "Your patterns over time" in a real browser: the entry page's longitudinal section, the history line on
// M9's actions, the evidence chain, and "Read these now" creating an explicit practice record.
// Usage: node browser_progress.mjs <base-url> <chrome-path> <screenshot-prefix>
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base, chromePath, shotPrefix] = process.argv.slice(2);
const profile = mkdtempSync(join(tmpdir(), "pl-chrome-"));
const chrome = spawn(chromePath, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run",
  "--no-default-browser-check", "--window-size=1440,1400", "--autoplay-policy=no-user-gesture-required", "about:blank"],
  { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function cleanup() {
  const exited = chrome.exitCode !== null ? Promise.resolve() : new Promise((r) => chrome.once("exit", r));
  try { chrome.kill("SIGKILL"); } catch {}
  await Promise.race([exited, sleep(5000)]);
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
}
async function connect(url) {
  const ws = new WebSocket(url);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
  const send = (method, params = {}) => new Promise((res, rej) => {
    const mid = ++id; pending.set(mid, (m) => (m.error ? rej(new Error(method + ": " + m.error.message)) : res(m.result)));
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  return { send };
}

async function main() {
  let port = null;
  for (let i = 0; i < 100 && !port; i++) { const f = join(profile, "DevToolsActivePort"); if (existsSync(f)) port = readFileSync(f, "utf8").split("\n")[0]; else await sleep(100); }
  if (!port) throw new Error("Chrome did not start");
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(base + "read")}`, { method: "PUT" })).json();
  const page = await connect(target.webSocketDebuggerUrl);
  await page.send("Runtime.enable");
  await page.send("Page.enable");
  const run = async (expr) => {
    const r = await page.send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const shot = async (name) => { const s = await page.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(`${shotPrefix}_${name}.png`, Buffer.from(s.data, "base64")); };
  const H = `
    const $ = (id) => document.getElementById(id);
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    async function until(fn, ms = 20000) { const t = Date.now(); while (Date.now() - t < ms) { if (fn()) return true; await wait(50); } return false; }`;
  const out = {};
  out.entry = await run(`${H}
    await until(() => !$('patterns-over-time').hidden && $('patterns-over-time-body').querySelector('.progress-item'));
    const box = $('patterns-over-time-body');
    const items = [...box.querySelectorAll('.progress-item')];
    const first = items[0];
    first.querySelector('details.progress-chain').open = true;
    box.querySelector('details.progress-how').open = true;
        return {
      visible: !$('patterns-over-time').hidden, scope: $('patterns-over-time').querySelector('.scope').textContent,
      depth: box.querySelector('.progress-depth') ? box.querySelector('.progress-depth').textContent : null,
      groups: [...box.querySelectorAll('.progress-group')].map((g) => g.dataset.group),
      items: items.map((li) => ({ pattern: li.dataset.pattern, state: li.dataset.state, decision: li.dataset.decision,
        text: li.querySelector('.progress-text').textContent, next: li.querySelector('.progress-next').textContent })),
      chain: [...first.querySelectorAll('.progress-chain li')].map((li) => li.textContent),
      listen: first.querySelectorAll('.progress-listen').length,
      history: [...document.querySelectorAll('#practice-now-body .coach-history')].map((p) => p.textContent),
      progressAfterPractice: $('practice-now').compareDocumentPosition($('patterns-over-time')) === Node.DOCUMENT_POSITION_FOLLOWING,
      how: box.querySelector('.progress-how').innerText, text: box.innerText };`);
  await shot("progress");
  await run(`document.getElementById('patterns-over-time').scrollIntoView(); return true;`);
  await sleep(300);
  await shot("progress_section");
  out.practice = await run(`${H}
    const btn = document.querySelector('#practice-now .coach-action .coach-read');
    const targetId = btn.closest('.coach-action').dataset.target;
    btn.click();
    return { targetId };`);
  await sleep(2500);
  out.practiceSession = await run(`${H}
    await until(() => location.search.includes('session='), 20000);
    return { search: location.search };`);
  return out;
}

main().then(async (r) => { console.log(JSON.stringify(r)); await cleanup(); process.exit(0); })
  .catch(async (e) => { console.error(e.stack || String(e)); await cleanup(); process.exit(1); });
