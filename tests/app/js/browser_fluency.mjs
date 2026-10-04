// M8: fluency in the reader — the note's fluency phrase, Details → Fluency with exact Listen.
// Usage: node browser_fluency.mjs <base-url> <chrome-path> <mic-wav> <session-id> <segment-a> <segment-b> <screenshot-prefix>
// (the session is prepared by the test; the fake microphone is not used). The audio-service sandbox is
// disabled for the test browser only.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base, chromePath, micWav, sid, segA, segB, shotPrefix] = process.argv.slice(2);
const profile = mkdtempSync(join(tmpdir(), "pl-chrome-"));
const chrome = spawn(chromePath, [
  "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run",
  "--no-default-browser-check", "--window-size=1440,1000",
  "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${micWav}`,
  "--disable-features=AudioServiceSandbox", "--autoplay-policy=no-user-gesture-required", "about:blank",
], { stdio: "ignore" });
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
  for (let i = 0; i < 100 && !port; i++) {
    const f = join(profile, "DevToolsActivePort");
    if (existsSync(f)) port = readFileSync(f, "utf8").split("\n")[0]; else await sleep(100);
  }
  if (!port) throw new Error("Chrome did not start");
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(base + "read?session=" + sid)}`, { method: "PUT" })).json();
  const page = await connect(target.webSocketDebuggerUrl);
  await page.send("Runtime.enable");
  const run = async (expr) => {
    const r = await page.send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const out = await run(`
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    async function until(fn, ms = 20000) { const t = Date.now(); while (Date.now() - t < ms) { if (fn()) return true; await wait(50); } return false; }
    const A = ${JSON.stringify(segA)}, B = ${JSON.stringify(segB)};
    const seg = (id) => document.querySelector('.seg[data-seg="' + id + '"]');
    const note = (id) => document.querySelector('.note[data-note="' + id + '"]');
    await until(() => window.__reader && window.__reader.snap && seg(A) && seg(A).querySelector('.aw') && seg(B).querySelector('.aw'));
    const R = window.__reader;
    const out = { note1: note(A).innerText, note2: note(B).innerText };
    note(A).querySelector('.details-toggle').click();
    const dr = () => document.querySelector('.drawer[data-drawer="' + A + '"]');
    await until(() => dr() && !dr().hidden && dr().querySelector('details.fluency'));
    const sec = dr().querySelector('details.fluency');
    sec.open = true;
    out.sectionTitle = sec.querySelector('summary').textContent;
    const firstList = sec.querySelector(':scope > ul.fluency-list');
    out.items = firstList ? [...firstList.querySelectorAll('.fl-label')].map(x => x.textContent) : [];
    out.summary = sec.querySelector('.fluency-summary').textContent;
    out.rate = sec.querySelector('.fluency-rate').textContent;
    R.lastPlayback = null; firstList.querySelector('.listen-fluency').click(); await until(() => R.lastPlayback, 5000);
    out.ref = R.lastPlayback;
    out.separate = !dr().querySelector('details.evidence details.fluency') && !dr().querySelector('.connected-speech details.fluency')
      && !!dr().querySelector('details.evidence') && sec.parentElement === dr().querySelector('details.evidence').parentElement;
    out.sectionText = sec.innerText;
    out.articleIntact = [...document.querySelectorAll('.seg[data-seg]')].every(x => x.checkVisibility())
      && R.segments.filter(s => s.readable).every(s => seg(s.id).textContent === s.text);
    out.drawers = document.querySelectorAll('.drawer:not([hidden])').length;
    return out;`);
  const s = await page.send("Page.captureScreenshot", { format: "png" });
  writeFileSync(`${shotPrefix}_fluency.png`, Buffer.from(s.data, "base64"));
  return out;
}

main().then(async (r) => { console.log(JSON.stringify(r)); await cleanup(); process.exit(0); })
  .catch(async (e) => { console.error(e.stack || String(e)); await cleanup(); process.exit(1); });
