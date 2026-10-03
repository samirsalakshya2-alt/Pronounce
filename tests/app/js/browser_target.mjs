// M12 target confirmation in the browser: a session prepared by the test with a MATCH and a MISMATCH attempt.
// Usage: node browser_target.mjs <base-url> <chrome-path> <mic-wav> <session-id> <match-segment> <mismatch-segment>
// The audio-service sandbox is disabled for the test browser only: on macOS it
// otherwise cannot read the fake-microphone file and delivers silence.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base, chromePath, micWav, sid, matchSeg, mismatchSeg] = process.argv.slice(2);
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
  return run(`
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    async function until(fn, ms = 20000) { const t = Date.now(); while (Date.now() - t < ms) { if (fn()) return true; await wait(50); } return false; }
    await until(() => window.__reader && window.__reader.snap && document.querySelectorAll('.note:not([hidden])').length >= 2);
    const R = window.__reader;
    const open = async (seg) => { const dd = document.querySelector('.drawer[data-drawer="' + seg + '"]');
      if (!dd || dd.hidden) document.querySelector('.note[data-note="' + seg + '"] .details-toggle').click();
      await until(() => document.querySelector('.drawer[data-drawer="' + seg + '"]:not([hidden])')); return document.querySelector('.drawer[data-drawer="' + seg + '"]'); };
    const out = {};
    let d = await open(${JSON.stringify(matchSeg)});
    await until(() => d.querySelector('details.evidence'));
    out.match = { ask: !!d.querySelector('.target-ask'), details: !!d.querySelector('details.evidence'),
      compact: document.querySelector('.note[data-note=' + JSON.stringify(${JSON.stringify(matchSeg)}) + '] .compact').textContent };
    const mm = document.querySelector('.note[data-note=${JSON.stringify(mismatchSeg)}]');
    out.mismatchMark = { text: mm.textContent, title: mm.querySelector('.compact').textContent };
    d = await open(${JSON.stringify(mismatchSeg)});
    out.mismatch = { ask: d.querySelector('.target-ask') && d.querySelector('.target-ask').dataset.target, details: !!d.querySelector('details.evidence'),
      compact: !!d.querySelector('.compact'), text: d.innerText, keep: !!d.querySelector('.keep-btn'), rerecord: !!d.querySelector('.rerecord-btn') };
    out.rail = document.querySelector('#rail-feedback .rail-line') && document.querySelector('#rail-feedback .rail-line').textContent;
    const mmSeg = document.querySelector('.seg[data-seg=${JSON.stringify(mismatchSeg)}]');
    out.mismatchAnnotated = { annotated: mmSeg.classList.contains('annotated'), words: mmSeg.querySelectorAll('.aw').length,
      matchAnnotated: document.querySelector('.seg[data-seg=${JSON.stringify(matchSeg)}]').classList.contains('annotated') };
    d.querySelector('.keep-btn').click();
    await until(() => { const x = document.querySelector('.drawer[data-drawer=${JSON.stringify(mismatchSeg)}]'); return x && x.querySelector('details.evidence'); });
    d = document.querySelector('.drawer[data-drawer=${JSON.stringify(mismatchSeg)}]');
    await until(() => document.querySelector('.seg[data-seg=${JSON.stringify(mismatchSeg)}]').classList.contains('annotated'), 5000);
    out.afterKeep = { details: !!d.querySelector('details.evidence'), stillAsks: !!d.querySelector('.target-ask'),
      annotated: document.querySelector('.seg[data-seg=${JSON.stringify(mismatchSeg)}]').classList.contains('annotated') };
    d.querySelector('.rerecord-btn').click();
    await until(() => R.controller.state === 'CAPTURING');
    out.rerecord = { recording: R.controller.current && R.controller.current.segmentId, drawerHidden: d.hidden };
    await wait(800);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await until(() => !R.uploads.status().waiting && !R.uploads.status().uploading, 20000);
    await wait(300);
    out.dispositions = R.snap.attempts.map(a => [a.segment_id, a.user_disposition]);
    return out;`);
}

main().then(async (r) => { await cleanup(); console.log(JSON.stringify(r)); process.exit(0); })
      .catch(async (e) => { await cleanup(); console.error(String((e && e.stack) || e)); process.exit(1); });
