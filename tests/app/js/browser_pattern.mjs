// M7: the reading pattern of the manual test in a real browser (fake microphone, noisy room).
// Usage: node browser_pattern.mjs <base-url> <chrome-path> <mic-wav> <screenshot-prefix> <toS2,toS3,toStop ms> <article-text>
// The reader selects sentence 1 and reads on into sentence 2; selects sentence 2 in the pause, reads it again and
// on into sentence 3; selects sentence 3, reads it, stops. The audio-service sandbox is disabled for the test
// browser only (on macOS it otherwise cannot read the fake-microphone file).
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base, chromePath, micWav, shotPrefix, switchMs, text] = process.argv.slice(2);
const [toS2, toS3, toStop] = switchMs.split(",").map(Number);
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
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(base + "read")}`, { method: "PUT" })).json();
  const page = await connect(target.webSocketDebuggerUrl);
  await page.send("Runtime.enable");
  const run = async (expr) => {
    const r = await page.send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const shot = async (name) => {
    const s = await page.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(`${shotPrefix}_${name}.png`, Buffer.from(s.data, "base64"));
  };
  const H = `
    const $ = (id) => document.getElementById(id);
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    const R = window.__reader;
    const seg = (i) => document.querySelectorAll('.seg[data-seg]')[i];
    const click = (n) => n.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    async function until(fn, ms = 20000) { const t = Date.now(); while (Date.now() - t < ms) { if (fn()) return true; await wait(50); } return false; }`;
  await run(`${H} await until(() => !$('entry').hidden);`);
  const out = {};
  out.entry = await run(`${H}
    $('entry-title').value = 'Reading ahead';
    $('entry-text').value = ${JSON.stringify(text)};
    $('entry-start').click();
    await until(() => !$('reader').hidden);
    return { url: location.search, segments: R.segments.filter(s => s.readable).map(s => s.text) };`);
  // the manual pattern: select s1 and read on into s2; select s2 (in the pause), read it again and on into s3;
  // select s3, read it; stop. The clicks land in pauses of the microphone file.
  out.reading = await run(`${H}
    click(seg(0));
    await until(() => R.controller.state === 'CAPTURING');
    const t0 = performance.now();
    await wait(${toS2} - (performance.now() - t0)); click(seg(1));
    await wait(${toS3} - (performance.now() - t0)); click(seg(2));
    await wait(${toStop} - (performance.now() - t0)); $('stop-btn').click();
    return { attempts: R.controller.attempts.map(x => ({ id: x.attemptId, seg: x.segmentId, start: x.startSample, end: x.endSample, reason: x.endReason })),
      state: R.controller.state };`);
  out.analysed = await run(`${H}
    const ok = await until(() => { const s = R.snap; return s && s.attempts.length === 3 && s.attempts.every(a => a.state === 'ANALYZED')
      && [0, 1].every(i => seg(i).querySelector('.aw')); }, 180000);
    await wait(300);
    const s = R.snap;
    const note = (i) => document.querySelector('.note[data-note="' + seg(i).dataset.seg + '"]');
    const per = s.attempts.map((a, i) => { const job = s.jobs[a.job_ids[0]]; const bn = note(i).querySelector('.boundary-note');
      return { attempt: a.id, job: job.id, seg: a.segment_id, boundary: job.boundary, feedback: job.feedback,
        words: seg(i).querySelectorAll('.aw').length, line: bn ? bn.querySelector('.boundary-line').textContent : null,
        note: note(i).innerText }; });
    return { ok, per, segStates: s.article.segments.filter(x => x.readable).map(x => s.segment_states[x.id]),
      sentences: R.segments.filter(x => x.readable).map(x => x.text) };`);
  await shot("pattern");
  out.listen = await run(`${H}
    const res = [];
    for (const i of [0, 1]) {
      const note = document.querySelector('.note[data-note="' + seg(i).dataset.seg + '"]');
      const b = note.querySelector('.listen-overflow');
      R.lastPlayback = null; if (b) { b.click(); await until(() => R.lastPlayback, 5000); }
      res.push(R.lastPlayback);
    }
    return res;`);
  return out;
}

main().then(async (r) => { console.log(JSON.stringify(r)); await cleanup(); process.exit(0); })
  .catch(async (e) => { console.error(e.stack || String(e)); await cleanup(); process.exit(1); });
