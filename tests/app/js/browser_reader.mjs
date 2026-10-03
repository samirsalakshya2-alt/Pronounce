// Drive the M12 reader in headless Chrome with a fake microphone fed by a WAV file.
// Usage: node browser_reader.mjs <base-url> <chrome-path> <mic-wav> <screenshot-prefix>
// The audio-service sandbox is disabled for the test browser only: on macOS it
// otherwise cannot read the fake-microphone file and delivers silence.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base, chromePath, micWav, shotPrefix] = process.argv.slice(2);
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
  await page.send("Page.enable");
  const errors = [];
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
    $('entry-title').value = 'A browser reading test';
    $('entry-source').value = 'Benchmark sentences';
    $('entry-text').value = 'Think about the three things that you want to change. Very few people would value the view from this valley.\\n\\nThe ship will leave the harbor before the evening begins. The world has changed significantly over the last six months.';
    $('entry-start').click();
    await until(() => !$('reader').hidden);
    const art = $('article').getBoundingClientRect();
    return { title: $('article-title').textContent, source: $('article-source').textContent,
      segments: document.querySelectorAll('.seg[data-seg]').length, paragraphs: document.querySelectorAll('#article-body .para').length,
      articleShare: (art.width) / window.innerWidth, railWidth: $('rail').getBoundingClientRect().width,
      bodyFont: getComputedStyle($('article-body')).fontFamily, lineHeight: parseFloat(getComputedStyle($('article-body')).lineHeight) / parseFloat(getComputedStyle($('article-body')).fontSize),
      url: location.search, label: $('mic-label').textContent,
      articleText: $('article-body').innerText.replace(/\\s+/g, ' '), segTexts: window.__reader.segments.map(s => s.text),
      allVisible: [...document.querySelectorAll('.seg[data-seg]')].every(x => x.checkVisibility()), cards: document.querySelectorAll('.drawer:not([hidden])').length };`);

  out.reading = await run(`${H}
    click(seg(0));                                       // first selection: arms the microphone
    await until(() => R.controller.state === 'CAPTURING');
    const armedLabel = $('mic-label').textContent;
    await wait(1500);
    const t0 = performance.now(); click(seg(1)); const switchMs = performance.now() - t0;   // synchronous cut
    const afterSwitch = { first: seg(0).classList.contains('recording'), second: seg(1).classList.contains('recording') };
    const whileRecording = { allVisible: [...document.querySelectorAll('.seg[data-seg]')].every(x => x.checkVisibility()),
      articleText: $('article-body').innerText.replace(/\\s+/g, ' '), cards: document.querySelectorAll('.drawer:not([hidden])').length };
    const a = R.controller.attempts;
    const contiguous01 = a[0].endSample === a[1].startSample && a[0].endReason === 'switched';
    await wait(1200);
    click(seg(1));                                       // double selection: no new attempt
    const afterDouble = R.controller.attempts.length;
    click(seg(3)); click(seg(2));                        // rapid switching: an empty attempt for sentence 4, kept
    await wait(1000);
    $('pause-btn').click();                              // PAUSE: finalise, stay armed
    const paused = { state: R.controller.state, label: $('mic-label').textContent, button: $('pause-btn').textContent };
    await wait(800);                                     // not recorded
    $('pause-btn').click();                              // RESUME: new attempt of the active sentence
    await until(() => R.controller.state === 'CAPTURING');
    const resumed = R.controller.current.segmentId === seg(2).dataset.seg && R.controller.current.localNumber === 2;
    await wait(1000);
    $('stop-btn').click();                               // STOP: finalise, release the microphone
    const stopped = { state: R.controller.state, tracksLive: !!(R.stream && R.stream.getTracks().some(t => t.readyState === 'live')) };
    const attempts = R.controller.attempts.map(x => ({ id: x.attemptId, seg: x.segmentId, start: x.startSample, end: x.endSample,
      reason: x.endReason, run: x.runId, n: x.localNumber }));
    return { armedLabel, switchMs, afterSwitch, whileRecording, contiguous01, afterDouble, paused, resumed, stopped, attempts };`);

  out.analysed = await run(`${H}
    const ok = await until(() => { const s = R.snap; if (!s) return false;
      const up = R.uploads.status(); const q = s.queue;
      return !up.waiting && !up.uploading && !q.queued_primary && !q.running && s.attempts.length >= 5 &&
        s.attempts.every(a => !['RECORDED', 'QUEUED', 'ANALYZING', 'CAPTURING'].includes(a.state)); }, 60000);
    await wait(300);
    const s = R.snap;
    return { ok, uploads: R.uploads.status(), states: s.attempts.map(a => [a.segment_id.slice(-4), a.state, a.capture.start_sample, a.capture.end_sample, a.capture.end_reason]),
      marks: [...document.querySelectorAll('.note')].map(m => ({ hidden: m.hidden, cls: m.className, text: m.textContent })),
      progress: $('progress').textContent, queue: $('queue').textContent, session: s.session.state };`);
  await shot("after_reading");

  out.feedback = await run(`${H}
    const segId = seg(0).dataset.seg;
    const mark = document.querySelector('.note[data-note="' + segId + '"]');
    const markText = mark.textContent, markTitle = mark.querySelector('.compact').textContent;
    mark.querySelector('.details-toggle').click();
    await until(() => document.querySelector('.drawer[data-drawer="' + segId + '"]:not([hidden])'));
    let d = document.querySelector('.drawer[data-drawer="' + segId + '"]');
    const ask = d.querySelector('.target-ask');
    const target = ask ? ask.dataset.target : null;
    const hiddenByMismatch = target === 'MISMATCH' && !d.querySelector('details.evidence');
    if (ask) { d.querySelector('.keep-btn').click(); await wait(400); await until(() => d.querySelector('details.evidence'), 5000); }
    d = document.querySelector('.drawer[data-drawer="' + segId + '"]');
    const drawerIds = { attempt: d.dataset.attemptId, job: d.dataset.jobId, timeline: d.dataset.timeline };
    const noteC = document.querySelector('.note[data-note="' + segId + '"] .compact');
    const compact = noteC ? noteC.textContent : null;
    d.querySelector('.listen-attempt').click();
    await until(() => R.lastPlayback);
    const listenRef = R.lastPlayback;
    await until(() => d.querySelector('.compare-attempt'), 10000);
    const det = d;
    d.querySelector('details.evidence').open = true;
    const sections = { coachGroups: det.querySelectorAll('.coach-group').length, patternCards: det.querySelectorAll('.coach-card[data-pattern]').length,
      reductionCards: det.querySelectorAll('.coach-card[data-candidate]').length, words: det.querySelectorAll('.rword').length };
    let soundRef = null;
    const ev = det.querySelector('.view-evidence');
    if (ev) { ev.click(); await wait(50); const b = det.querySelector('.play-occurrence'); if (b) { R.lastPlayback = null; b.click(); await until(() => R.lastPlayback); soundRef = R.lastPlayback; } }
    det.querySelector('.compare-attempt').click();
    await until(() => det.querySelector('.compare .engine-note') || det.querySelector('.compare .error'), 60000);
    const cmp = { note: det.querySelector('.compare .engine-note') ? det.querySelector('.compare .engine-note').textContent : null,
      error: det.querySelector('.compare .error') ? det.querySelector('.compare .error').textContent : null,
      headers: [...det.querySelectorAll('.compare th')].map(th => th.textContent),
      rows: det.querySelectorAll('.compare tr[data-agreement]').length };
    const rail = document.querySelector('#rail-feedback .rail-line') ? document.querySelector('#rail-feedback .rail-line').textContent : null;
    return { markText, markTitle, target, hiddenByMismatch, drawerIds, compact, listenRef, sections, soundRef, cmp, rail, text: d.innerText };`);
  await shot("feedback");

  out.keyboard = await run(`${H}
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'j' }));   // next sentence: re-arms, new run
    await until(() => R.controller.state === 'CAPTURING');
    const cur = R.controller.current.segmentId;
    await wait(600);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    return { selected: cur, state: R.controller.state, runs: new Set(R.controller.attempts.map(a => a.runId)).size };`);

  out.finish = await run(`${H}
    await until(() => !R.uploads.status().waiting && !R.uploads.status().uploading, 20000);
    $('finish-btn').click();
    await until(() => R.snap.session.state === 'FINISHED' || R.snap.session.state === 'SUMMARIZED', 20000);
    await until(() => R.snap.session.state === 'SUMMARIZED' && !$('summary').hidden && $('summary').querySelector('.lead'), 30000);
    return { session: R.snap.session.state, finishDisabled: $('finish-btn').disabled,
      summary: { heading: $('summary').querySelector('h2') && $('summary').querySelector('h2').textContent,
        lead: $('summary').querySelector('.lead') && $('summary').querySelector('.lead').textContent, text: $('summary').innerText } };`);

  // reload: the session and its recordings come back from the server
  await page.send("Page.reload");
  await sleep(500);
  out.reload = await run(`${H}
    await until(() => window.__reader && window.__reader.snap);
    const S = window.__reader;
    await until(() => document.querySelectorAll('.note:not([hidden])').length > 0);
    return { attempts: S.snap.attempts.length, session: S.snap.session.state, title: document.getElementById('article-title').textContent,
      visibleMarks: document.querySelectorAll('.note:not([hidden])').length, progress: document.getElementById('progress').textContent };`);
  await shot("reloaded");
  return out;
}

main().then(async (r) => { await cleanup(); console.log(JSON.stringify(r)); process.exit(0); })
      .catch(async (e) => { await cleanup(); console.error(String((e && e.stack) || e)); process.exit(1); });
