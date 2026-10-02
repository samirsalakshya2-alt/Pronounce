// Drive the M5 Reduction & Connected Speech UI in headless Chrome over the DevTools protocol.
// Usage: node browser_reduction.mjs <base-url> <chrome-path> <screenshot-prefix>
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
// Kill Chrome and remove its profile only after it has exited.
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
    function snapshot() {
      const box = $('reduction');
      const cards = [...box.querySelectorAll('.coach-card')];
      return {
        heading: box.querySelector('h3') ? box.querySelector('h3').textContent : null,
        rate: $('reduction-rate') ? $('reduction-rate').textContent : null,
        groups: [...box.querySelectorAll('[data-reduction-group]')].map(g => ({ id: g.dataset.reductionGroup, open: g.open,
          cards: g.querySelectorAll('.coach-card').length })),
        firstCard: cards.length ? { title: cards[0].querySelector('strong').textContent,
          chain: [...cards[0].querySelectorAll('.reduction-chain li')].map(li => li.textContent) } : null,
        cardTexts: cards.map(c => c.innerText),
      };
    }`;

  const out = await run(`${helpers}
    const res = {};
    await analyse('R01');
    $('full-feedback-btn').click(); await wait(200);
    res.fullFeedbackRows = document.querySelectorAll('#full-feedback tr[data-full-sound]').length;
    $('coach-btn').click(); await wait(200);
    res.coachVisible = !$('coach').hidden;
    res.coachGroups = document.querySelectorAll('#coach .coach-group').length;
    $('reduction-btn').click(); await wait(200);
    res.r01 = snapshot();
    const card = document.querySelector('#reduction .coach-card');
    const btn = card.querySelector('.play-candidate');
    btn.click(); await wait(80);
    res.candidateHighlighted = card.classList.contains('playing');
    $('compare-btn').click();
    for (let i = 0; i < 600 && !document.querySelector('#reduction-compare table') && !document.querySelector('#reduction-compare .error'); i++) await wait(100);
    const err = document.querySelector('#reduction-compare .error');
    if (err) throw new Error(err.textContent);
    res.compareNote = document.querySelector('#reduction-compare .engine-note').textContent;
    res.compareRows = [...document.querySelectorAll('#reduction-compare tr[data-agreement]')].map(tr => ({
      word: tr.children[0].textContent, first: tr.children[1].textContent, second: tr.children[2].textContent,
      relation: tr.children[3].innerText, agreement: tr.dataset.agreement }));
    const row = document.querySelector('#reduction-compare tr[data-agreement]');
    row.querySelector('.play-compare').click(); await wait(80);
    res.comparePlayHighlighted = row.classList.contains('playing');
    res.reductionText = $('reduction').innerText;

    await analyse('R18');
    res.reductionClosedOnNewAnalysis = $('reduction').hidden && $('reduction').innerHTML === '';
    $('reduction-btn').click(); await wait(200);
    res.r18 = snapshot();
    res.reductionText += $('reduction').innerText;
    res.errorBox = $('error-box').hidden ? null : $('error-box').textContent;
    return res;`);
  await run(`${helpers} document.querySelectorAll('#reduction details').forEach(d => d.open = true); $('reduction').scrollIntoView();`);
  await shot("r18");
  page.close();
  return out;
}

main().then(async (r) => { await cleanup(); console.log(JSON.stringify(r)); process.exit(0); })
      .catch(async (e) => { await cleanup(); console.error(String((e && e.stack) || e)); process.exit(1); });
