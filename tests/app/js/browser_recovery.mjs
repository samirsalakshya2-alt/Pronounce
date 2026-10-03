// M12 recovery in the browser.
// Usage: node browser_recovery.mjs <base-url> <chrome-path> <mic-wav> <mode: deny|recover>
// The audio-service sandbox is disabled for the test browser only: on macOS it
// otherwise cannot read the fake-microphone file and delivers silence.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base, chromePath, micWav, mode] = process.argv.slice(2);
const profile = mkdtempSync(join(tmpdir(), "pl-chrome-"));
const chrome = spawn(chromePath, [
  "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run",
  "--no-default-browser-check", "--window-size=1440,1000",
  ...(mode === "deny" ? ["--deny-permission-prompts"] : ["--use-fake-ui-for-media-stream"]),
  "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${micWav}`,
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
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(base + "read")}`, { method: "PUT" })).json();
  const page = await connect(target.webSocketDebuggerUrl);
  await page.send("Runtime.enable");
  const run = async (expr) => {
    const r = await page.send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const H = `
    const $ = (id) => document.getElementById(id);
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    async function until(fn, ms = 20000) { const t = Date.now(); while (Date.now() - t < ms) { if (fn()) return true; await wait(50); } return false; }
    const seg = (i) => document.querySelectorAll('.seg[data-seg]')[i];
    const click = (n) => n.dispatchEvent(new MouseEvent('click', { bubbles: true }));`;
  await run(`${H} await until(() => !$('entry').hidden);
    $('entry-text').value = 'One short sentence here. Another short sentence there. A third sentence follows. And a fourth one ends it.';
    $('entry-start').click(); await until(() => !$('reader').hidden);`);
  if (mode === "deny") {
    return run(`${H} const R = window.__reader;
      click(seg(0));
      await until(() => R.controller.state === 'DENIED', 10000);
      await wait(300);
      return { state: R.controller.state, notice: $('notice').textContent, attempts: R.controller.attempts.length,
        serverAttempts: R.snap.attempts.length, label: $('mic-label').textContent };`);
  }
  return run(`${H} const R = window.__reader;
    const out = {};
    // device loss: the track ends while reading; what was recorded is kept
    click(seg(0)); await until(() => R.controller.state === 'CAPTURING'); await wait(700);
    R.stream.getAudioTracks()[0].dispatchEvent(new Event('ended'));
    out.device = { state: R.controller.state, reason: R.controller.attempts[0].endReason, notice: $('notice').textContent };
    // page hidden: auto-pause, the attempt is finalised as page_hidden
    click(seg(1)); await until(() => R.controller.state === 'CAPTURING'); await wait(700);
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
    document.dispatchEvent(new Event('visibilitychange'));
    out.hidden = { state: R.controller.state, reason: R.controller.attempts.at(-1).endReason, notice: $('notice').textContent };
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
    // uploads: the first attempt below fails twice (network) then succeeds; the second fails permanently, then is retried by hand
    const realFetch = window.fetch.bind(window);
    let transient = 2, permanent = true; const seen = [];
    window.fetch = (url, opts) => {
      if (String(url).endsWith('/audio') && opts && opts.method === 'POST') {
        const id = String(url).split('/attempts/')[1].split('/')[0];
        seen.push(id);
        if (id === out.flaky && transient > 0) { transient--; return Promise.reject(new TypeError('network down')); }
        if (id === out.broken && permanent) return Promise.resolve(new Response(JSON.stringify({ error: { code: 'x', message: 'refused' } }), { status: 400 }));
      }
      return realFetch(url, opts);
    };
    R.uploads.backoffMs = [100, 100, 100];
    const origAdd = R.uploads.add.bind(R.uploads);
    let n = 0;
    R.uploads.add = (item) => { if (n === 0) out.flaky = item.id; if (n === 1) out.broken = item.id; n++; origAdd(item); };
    click(seg(2)); await until(() => R.controller.state === 'CAPTURING'); await wait(600);
    click(seg(3)); await wait(600);
    $('pause-btn').click();
    await until(() => R.uploads.status().failed === 1 && R.uploads.done.has(out.flaky), 15000);
    out.uploads = { status: R.uploads.status(), flakyTries: seen.filter(x => x === out.flaky).length, queueText: $('queue').textContent,
      retryButton: !!$('queue').querySelector('button') };
    permanent = false;
    $('queue').querySelector('button').click();
    await until(() => R.uploads.status().failed === 0 && R.uploads.done.has(out.broken), 15000);
    out.afterRetry = R.uploads.status();
    await until(() => R.snap && R.snap.attempts.length === 4 && R.snap.attempts.every(a => !['CAPTURING', 'QUEUED', 'ANALYZING', 'RECORDED'].includes(a.state)), 40000);
    out.server = R.snap.attempts.map(a => [a.segment_id.slice(-4), a.state, a.capture.end_reason]);
    window.fetch = realFetch;
    return out;`);
}

main().then(async (r) => { await cleanup(); console.log(JSON.stringify(r)); process.exit(0); })
      .catch(async (e) => { await cleanup(); console.error(String((e && e.stack) || e)); process.exit(1); });
