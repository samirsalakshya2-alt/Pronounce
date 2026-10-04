// M7: continued speech after the active sentence, in a real browser with a fake microphone.
// Usage: node browser_overflow.mjs <base-url> <chrome-path> <mic-wav> <screenshot-prefix> <speak-ms> <article-text>
// The microphone file holds the sentence, a pause, then the next sentence: the reader selects sentence 1
// only, keeps "speaking" into sentence 2, then stops. The audio-service sandbox is disabled for the
// test browser only (on macOS it otherwise cannot read the fake-microphone file).
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base, chromePath, micWav, shotPrefix, speakMs, text] = process.argv.slice(2);
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
    $('entry-title').value = 'Continued speech';
    $('entry-text').value = ${JSON.stringify(text)};
    $('entry-start').click();
    await until(() => !$('reader').hidden);
    return { url: location.search, segments: R.segments.filter(s => s.readable).map(s => s.text) };`);
  // read sentence 1 only, keep speaking (the next sentence), then stop: one attempt
  out.reading = await run(`${H}
    click(seg(0));
    await until(() => R.controller.state === 'CAPTURING');
    await wait(${Number(speakMs)});
    $('stop-btn').click();
    return { attempts: R.controller.attempts.map(x => ({ id: x.attemptId, seg: x.segmentId, start: x.startSample, end: x.endSample, reason: x.endReason })),
      state: R.controller.state };`);
  out.analysed = await run(`${H}
    const ok = await until(() => { const s = R.snap; return s && s.attempts.length === 1 && s.attempts[0].state === 'ANALYZED'
      && seg(0).querySelector('.aw'); }, 120000);
    await wait(300);
    const s = R.snap, a = s.attempts[0], job = s.jobs[a.job_ids[0]];
    const note = (i) => document.querySelector('.note[data-note="' + seg(i).dataset.seg + '"]');
    const bn = note(0).querySelector('.boundary-note');
    return { ok, boundary: job.boundary, attempt: a.id, job: job.id,
      segStates: s.article.segments.filter(x => x.readable).map(x => s.segment_states[x.id]),
      note0: note(0).innerText, boundaryLine: bn ? bn.querySelector('.boundary-line').textContent : null,
      boundarySub: bn && bn.querySelector('.boundary-sub') ? bn.querySelector('.boundary-sub').textContent : null,
      note1Hidden: note(1).hidden, words0: seg(0).querySelectorAll('.aw').length, words1: seg(1).querySelectorAll('.aw').length,
      annotated1: seg(1).classList.contains('annotated'), text0: seg(0).textContent, text1: seg(1).textContent };`);
  await shot("note");
  out.listen = await run(`${H}
    const note = document.querySelector('.note[data-note="' + seg(0).dataset.seg + '"]');
    R.lastPlayback = null; note.querySelector('.listen-overflow').click(); await until(() => R.lastPlayback);
    const overflowRef = R.lastPlayback;
    note.querySelector('.details-toggle').click();
    const dr = () => document.querySelector('.drawer[data-drawer="' + seg(0).dataset.seg + '"]');
    await until(() => dr() && !dr().hidden && dr().querySelector('details.boundary'));
    const b = dr().querySelector('details.boundary'); b.open = true;
    const buttons = [...b.querySelectorAll('.listen-region')].map(x => x.dataset.region);
    R.lastPlayback = null; b.querySelector('.listen-region[data-region="target"]').click(); await until(() => R.lastPlayback);
    const targetRef = R.lastPlayback;
    b.querySelector('details.boundary-evidence').open = true;
    const out = { overflowRef, targetRef, buttons, sectionText: b.innerText, sectionState: b.dataset.boundary,
      inPatterns: !!dr().querySelector('details.evidence details.boundary'), otherBefore: !!b.querySelector('.other-boundary') };
    // compare with the other listening model, then reopen Details: its own boundary assessment is shown
    dr().querySelector('.compare-attempt').click();
    await until(() => { const s = R.snap; const a = s.attempts[0];
      return a.job_ids.some(id => s.jobs[id] && s.jobs[id].kind === 'comparison' && s.jobs[id].state === 'SUCCEEDED'); }, 120000);
    await wait(300);
    note.querySelector('.details-toggle').click(); await wait(100);
    note.querySelector('.details-toggle').click();
    await until(() => dr() && !dr().hidden && dr().querySelector('details.boundary .other-boundary'), 20000);
    const ob = dr().querySelector('details.boundary .other-boundary');
    out.otherAfter = ob ? ob.textContent : null;
    return out;`);
  await shot("details");
  return out;
}

main().then(async (r) => { console.log(JSON.stringify(r)); await cleanup(); process.exit(0); })
  .catch(async (e) => { console.error(e.stack || String(e)); await cleanup(); process.exit(1); });
