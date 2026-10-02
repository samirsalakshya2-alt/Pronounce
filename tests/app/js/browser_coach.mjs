// Drive the Phoneme Coach UI in headless Chrome over the DevTools protocol.
// Usage: node browser_coach.mjs <base-url> <chrome-path> <screenshot-prefix>
// Expects the server session to already contain an analysis of "New Recording 49"
// (optional) and of a silent recording; analyses R05 and R01 itself through the UI.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base, chromePath, shotPrefix] = process.argv.slice(2);
const profile = mkdtempSync(join(tmpdir(), "pl-chrome-"));
const chrome = spawn(chromePath, [
  "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run",
  "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required", "--window-size=1200,2400", "about:blank",
], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// Kill Chrome and remove its profile only after it has exited (otherwise it can
// recreate files in the profile directory after the removal).
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
  return { send, close: () => ws.close() };
}

async function main() {
  let port = null;
  for (let i = 0; i < 100 && !port; i++) {
    const f = join(profile, "DevToolsActivePort");
    if (existsSync(f)) port = readFileSync(f, "utf8").split("\n")[0]; else await sleep(100);
  }
  if (!port) throw new Error("Chrome did not start");
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(base)}`, { method: "PUT" })).json();
  const page = await connect(target.webSocketDebuggerUrl);
  await page.send("Runtime.enable");
  const run = async (expr) => {
    const r = await page.send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const shot = async (name) => {
    const s = await page.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    writeFileSync(`${shotPrefix}_${name}.png`, Buffer.from(s.data, "base64"));
  };
  await run(`for (let i = 0; i < 100 && !document.querySelector('#benchmark-select option'); i++) await new Promise(r => setTimeout(r, 100));`);

  const helpers = `
    const $ = (id) => document.getElementById(id);
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    async function analyse(rid) {
      document.querySelector('[data-tab=benchmark]').click();
      $('benchmark-select').value = rid; $('benchmark-select').dispatchEvent(new Event('change'));
      const before = $('result-source').textContent;
      $('analyze-btn').click();
      for (let i = 0; i < 300 && $('result-source').textContent === before; i++) await wait(100);
      await wait(300);
    }
    async function openHistory(fragment) {
      const li = [...document.querySelectorAll('#history-list li')].find(l => l.textContent.includes(fragment));
      if (!li) return false;
      li.click(); await wait(800); return true;
    }
    function coachSnapshot() {
      const box = $('coach');
      return {
        visible: !box.hidden,
        heading: box.querySelector('h3') ? box.querySelector('h3').textContent : null,
        message: box.querySelector('p') ? box.querySelector('p').textContent : null,
        groups: [...box.querySelectorAll('.coach-group')].map(s => ({ id: s.dataset.group, title: s.querySelector('h4').textContent, open: s.open,
          cards: [...s.querySelectorAll('.coach-card')].map(c => ({ id: c.dataset.pattern, title: c.querySelector('strong').textContent,
            label: c.querySelector('.chip').textContent, summary: c.querySelector('p').textContent,
            practiceButton: c.querySelector('.view-practice') ? c.querySelector('.view-practice').textContent : null })) })),
        notInterpreted: box.querySelector('[data-group=not_interpreted] summary')?.textContent || null,
        coverage: $('coach-coverage') ? $('coach-coverage').textContent : null,
      };
    }`;

  const out = await run(`${helpers}
    const res = {};
    await analyse('R05');
    $('coach-btn').click(); await wait(200);
    res.r05 = coachSnapshot();
    // open the /w/ ↔ /v/ card: evidence + exact playback + practice
    const card = [...document.querySelectorAll('.coach-card')].find(c => c.querySelector('strong').textContent === '/w/ ↔ /v/');
    card.querySelector('.view-evidence').click(); await wait(100);
    const rows = [...card.querySelectorAll('tr[data-observation]')];
    res.r05Evidence = rows.map(r => [...r.children].map(td => td.innerText.replace(/\\n/g, ' | ')));
    const playBtn = rows[0].querySelector('.play-occurrence');
    playBtn.click(); await wait(80);
    res.r05Highlighted = rows[0].classList.contains('playing'); await wait(500);
    res.r05Cleared = !rows[0].classList.contains('playing');
    card.querySelector('.view-practice').click(); await wait(100);
    res.r05Practice = card.querySelector('.coach-practice').innerText;
    const wordBtn = card.querySelector('.play-practice-word');
    wordBtn.click(); await wait(80); res.r05WordPlayHighlighted = wordBtn.classList.contains('playing');
    // ambiguous card wording
    const amb = [...document.querySelectorAll('.coach-group[data-group=ambiguous] .coach-card')][0];
    amb.querySelector('.view-practice').click(); await wait(100);
    res.r05Ambiguous = { button: amb.querySelector('.view-practice').textContent, practice: amb.querySelector('.coach-practice').innerText };
    res.r05PageText = document.body.innerText;

    // M3.1 Full Recording Feedback still works alongside
    $('full-feedback-btn').click(); await wait(200);
    res.fullFeedbackRows = document.querySelectorAll('#full-feedback tr[data-full-sound]').length;

    await analyse('R01');
    res.coachClosedOnNewAnalysis = $('coach').hidden && $('coach').innerHTML === '';
    $('coach-btn').click(); await wait(200);
    res.r01 = coachSnapshot();
    const det = [...document.querySelectorAll('.coach-group[data-group=not_detected] .coach-card')][0];
    det.querySelector('.view-evidence').click(); await wait(100);
    res.r01DetectionEvidence = det.querySelector('.coach-evidence').innerText;

    res.rec49Loaded = await openHistory('New Recording 49');
    if (res.rec49Loaded) { $('coach-btn').click(); await wait(300); res.rec49 = coachSnapshot(); }
    res.silenceLoaded = await openHistory('silence');
    if (res.silenceLoaded) { $('coach-btn').click(); await wait(200); res.silence = coachSnapshot(); }
    res.engineFooter = $('engines-footer').textContent;
    res.errorBox = $('error-box').hidden ? null : $('error-box').textContent;
    return res;`);
  if (out.rec49Loaded) {
    await run(`${helpers} await openHistory('New Recording 49'); $('coach-btn').click(); await wait(300);
      const c = document.querySelector('.coach-card');  // smoke only: open the first card
      if (c) { c.querySelector('.view-evidence').click(); c.querySelector('.view-practice').click(); }
      await wait(200); $('coach').scrollIntoView();`);
    await shot("rec49");
  }
  await run(`${helpers} await analyse('R05'); $('coach-btn').click(); await wait(200);
    const c = [...document.querySelectorAll('.coach-card')].find(x => x.querySelector('strong').textContent === '/w/ ↔ /v/');
    c.querySelector('.view-evidence').click(); c.querySelector('.view-practice').click(); await wait(200);`);
  await shot("r05");
  page.close();
  return out;
}

main().then(async (r) => { await cleanup(); console.log(JSON.stringify(r)); process.exit(0); })
      .catch(async (e) => { await cleanup(); console.error(String((e && e.stack) || e)); process.exit(1); });
