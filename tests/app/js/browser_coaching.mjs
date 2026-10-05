// M9 "What to practise now" in a real browser: entry page (live), a finished session's summary (snapshot),
// cross-session Listen, the evidence panel, and "Read these now".
// Usage: node browser_coaching.mjs <base-url> <chrome-path> <summarised-session-id> <screenshot-prefix> [<full-description-session-id> <no-area-session-id>]
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base, chromePath, sid, shotPrefix, sid2, sid3] = process.argv.slice(2);
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
    async function until(fn, ms = 20000) { const t = Date.now(); while (Date.now() - t < ms) { if (fn()) return true; await wait(50); } return false; }
    function readingDom(rd) {
      const q = (sel) => [...rd.querySelectorAll(sel)];
      return { subs: q('.reading-sub').map(s => s.dataset.sub), headings: q('.reading-sub h4').map(h => h.textContent),
        areas: q('.reading-area').map(li => ({ text: li.querySelector('.reading-headline').textContent, band: li.dataset.band,
          scope: li.querySelector('.reading-scope').dataset.scope, label: li.querySelector('.reading-scope').textContent,
          observations: li.querySelector('.reading-observations').textContent,
          pattern: li.querySelector('.reading-pattern') ? li.querySelector('.reading-pattern').textContent : null,
          rate: li.querySelector('.reading-rate') ? li.querySelector('.reading-rate').textContent : null,
          why: li.querySelector('.reading-why') ? li.querySelector('.reading-why').textContent : null,
          listen: [...li.querySelectorAll('.reading-listen')].map(b => b.textContent) })),
        noArea: rd.querySelector('.reading-no-area') ? rd.querySelector('.reading-no-area').textContent : null,
        strengths: q('.reading-strength span').map(s => s.textContent), fluency: q('.reading-fluency span').map(s => s.textContent),
        cautions: q('.reading-caution').map(p => p.textContent), text: rd.innerText };
    }`;
  const out = {};
  out.entry = await run(`${H}
    await until(() => !$('practice-now').hidden && $('practice-now-body').children.length);
    const sec = $('practice-now'), cards = [...sec.querySelectorAll('.coach-action')];
    const first = cards[0];
    const res = { visible: !sec.hidden, count: cards.length, titles: cards.map(c => c.querySelector('.coach-title').textContent),
      kinds: cards.map(c => c.dataset.kind), why: cards.map(c => c.querySelector('.coach-why').textContent),
      examples: cards.map(c => c.querySelectorAll('.coach-examples .coach-listen').length),
      retest: cards.map(c => c.querySelectorAll('.coach-retest .coach-listen').length),
      evidenceClosed: cards.every(c => !c.querySelector('details.coach-evidence').open),
      text: sec.innerText, beforeRecent: sec.compareDocumentPosition($('recent')) === Node.DOCUMENT_POSITION_FOLLOWING };
    // cross-session Listen: the reference's own session/audio is fetched and played
    const R = window.__reader;
    R.lastPlayback = null;
    const btn = first.querySelector('.coach-examples .coach-listen');
    btn.click();
    await until(() => R.lastPlayback, 5000);
    res.ref = R.lastPlayback;
    res.playing = await until(() => btn.classList.contains('playing'), 8000);   // decoded and started
    res.currentSession = R.sessionId || null;
    await wait(300);
    first.querySelector('details.coach-evidence').open = true;
    res.evidence = [...first.querySelectorAll('.coach-evidence li')].map(li => li.textContent);
    return res;`);
  await shot("entry");
  // the summarised session: the issued advice at the top of "Your reading", no unbounded list
  await page.send("Page.navigate", { url: base + "read?session=" + sid });
  await sleep(1500);
  out.summary = await run(`${H}
    await until(() => document.querySelector('#summary:not([hidden]) [data-group="practice_now"]'), 20000);
    const box = $('summary'), sec = box.querySelector('[data-group="practice_now"]');
    const groups = [...box.querySelectorAll('section.summary-group')].map(s => s.dataset.group);
    const rd = box.querySelector('[data-group="this_reading"]');
    return { present: !!sec, first: groups[0], groups, cards: sec ? sec.querySelectorAll('.coach-action').length : -1,
      scopes: [...box.querySelectorAll('section.summary-group .scope')].map(p => p.textContent),
      reading: rd ? readingDom(rd) : null,
      none: sec && sec.querySelector('.coach-none') ? sec.querySelector('.coach-none').innerText : null,
      oldList: box.innerText.includes('Sounds worth practising'), text: box.innerText };`);
  await shot("summary");
  if (sid2) {   // a finished session whose description has every part: the order of "This reading"
    await page.send("Page.navigate", { url: base + "read?session=" + sid2 });
    await sleep(1500);
    out.full = await run(`${H}
      await until(() => document.querySelector('#summary:not([hidden]) [data-group="this_reading"] .reading-sub'), 20000);
      const box = $('summary');
      const groups = [...box.querySelectorAll('section.summary-group')].map(s => s.dataset.group);
      return { groups, reading: readingDom(box.querySelector('[data-group="this_reading"]')) };`);
    await shot("this_reading");
  }
  if (sid3) {   // a finished session where nothing qualified: the explicit message, strengths still after it
    await page.send("Page.navigate", { url: base + "read?session=" + sid3 });
    await sleep(1500);
    out.noArea = await run(`${H}
      await until(() => document.querySelector('#summary:not([hidden]) [data-group="this_reading"] .reading-sub'), 20000);
      return readingDom($('summary').querySelector('[data-group="this_reading"]'));`);
  }
  // "Read these now": a short practice article of the action's retest sentences
  await page.send("Page.navigate", { url: base + "read" });
  await sleep(1500);
  out.practice = await run(`${H}
    await until(() => document.querySelector('#practice-now .coach-read'));
    const card = document.querySelector('#practice-now .coach-action');
    const expected = [...card.querySelectorAll('.coach-retest .coach-listen')].length;
    card.querySelector('.coach-read').click();
    return { expected };`);
  await sleep(2500);
  out.practiceSession = await run(`${H}
    await until(() => window.__reader && window.__reader.snap && location.search.includes('session='), 20000);
    const R = window.__reader;
    return { search: location.search, title: R.snap.article.title, source: R.snap.article.source,
      segments: R.snap.article.segments.filter(s => s.readable).map(s => s.text) };`);
  return out;
}

main().then(async (r) => { console.log(JSON.stringify(r)); await cleanup(); process.exit(0); })
  .catch(async (e) => { console.error(e.stack || String(e)); await cleanup(); process.exit(1); });
