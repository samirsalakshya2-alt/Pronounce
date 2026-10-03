// M12: the article itself is the pronunciation feedback (a session prepared by the test: R13 and R08 analysed,
// other sentences not). Usage: node browser_words.mjs <base-url> <chrome-path> <mic-wav> <session-id> <segment-R13> <segment-R08>
// The audio-service sandbox is disabled for the test browser only: on macOS it
// otherwise cannot read the fake-microphone file and delivers silence.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base, chromePath, micWav, sid, segA, segB] = process.argv.slice(2);
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
    const A = ${JSON.stringify(segA)}, B = ${JSON.stringify(segB)};
    const seg = (id) => document.querySelector('.seg[data-seg="' + id + '"]');
    await until(() => window.__reader && window.__reader.snap && seg(A) && seg(A).querySelector('.aw') && seg(B).querySelector('.aw'));
    const R = window.__reader;
    const out = {};
    const segs = R.segments.filter(s => s.readable);
    const article = () => ({ allVisible: [...document.querySelectorAll('.seg[data-seg]')].every(x => x.checkVisibility()),
      texts: segs.map(s => seg(s.id).textContent), openBlocks: document.querySelectorAll('.drawer:not([hidden])').length,
      panels: [...document.querySelectorAll('.word-panel')].filter(p => !p.hidden).length });
    // the article, as loaded: no clicks
    out.loaded = { ...article(), segTexts: segs.map(s => s.text), paras: document.querySelectorAll('#article-body .para').length,
      annotated: segs.map(s => seg(s.id).classList.contains('annotated')),
      wordsPerSentence: segs.map(s => seg(s.id).querySelectorAll('.aw').length),
      legend: [...document.querySelectorAll('#legend .legend-item')].map(x => x.textContent),
      noteA: document.querySelector('.note[data-note="' + A + '"]').innerText, recordingState: R.controller.state };
    const viewOf = async (id) => { const sp = seg(id); const d = await (await fetch('/api/sessions/' + R.sessionId + '/attempts/' + sp.dataset.attemptId)).json();
      return { view: d.views[sp.dataset.jobId], attempt: sp.dataset.attemptId, job: sp.dataset.jobId }; };
    const va = await viewOf(A), vb = await viewOf(B);
    const awA = [...seg(A).querySelectorAll('.aw')];
    out.wordsA = { spans: awA.map(n => ({ text: n.textContent, cls: n.className, index: +n.dataset.word })),
      view: va.view.words.map(w => ({ word: w.word, status: w.status, index: w.index })) };
    out.wordsB = { statuses: [...seg(B).querySelectorAll('.aw')].map(n => n.className.replace('aw ', '')), count: vb.view.words.length,
      spans: seg(B).querySelectorAll('.aw').length };
    // click a word heard differently: its MVP detail opens under its sentence
    const vw = (n) => va.view.words.find(w => String(w.index) === n.dataset.word);
    const diff = awA.find(n => n.classList.contains('cat-different'));
    diff.click(); await wait(80);
    const panel = document.querySelector('.word-panel');
    const noteA = document.querySelector('.note[data-note="' + A + '"]');
    const w1 = vw(diff);
    out.first = { word: w1.word, heading: panel.querySelector('h3').textContent, visible: !panel.hidden,
      rows: [...panel.querySelectorAll('tr[data-sound]')].map(tr => ({ cls: tr.className, first: tr.children[0].innerText.split('\\n')[0] })),
      expectedSounds: w1.sounds.map(s => [s.expected, s.category]), selected: document.querySelectorAll('.aw.selected').length,
      underItsSentence: panel.parentElement.dataset.extras === A && panel.parentElement.previousElementSibling === noteA,
      ...article(), recordingState: R.controller.state, panelIds: { attempt: panel.dataset.attemptId, job: panel.dataset.jobId, timeline: panel.dataset.timeline } };
    R.lastPlayback = null; panel.querySelector('tr[data-sound] .play-sound').click(); await until(() => R.lastPlayback);
    out.soundRef = R.lastPlayback; out.soundExpected = w1.sounds[0].play_ms;
    R.lastPlayback = null; panel.querySelector('.play-word').click(); await until(() => R.lastPlayback);
    out.wordRef = R.lastPlayback; out.wordExpected = w1.play_ms; out.ids = { attempt: va.attempt, job: va.job };
    // another word: the one panel moves
    const ok = awA.find(n => n.classList.contains('cat-expected'));
    ok.click(); await wait(60);
    out.second = { word: vw(ok).word, panelWord: panel.dataset.word, firstPanelWord: String(w1.index), heading: panel.querySelector('h3').textContent,
      rowCats: [...panel.querySelectorAll('tr[data-sound]')].map(tr => tr.className), selected: document.querySelectorAll('.aw.selected').length,
      panels: article().panels, firstDeselected: !diff.classList.contains('selected') };
    out.others = {};
    for (const st of ['unclear', 'not_detected']) {
      const n = awA.find(x => x.classList.contains('cat-' + st)); n.click(); await wait(40);
      out.others[st] = { word: vw(n).word, rowCats: [...panel.querySelectorAll('tr[data-sound]')].map(tr => tr.className) };
    }
    const cur = document.querySelector('.aw.selected'); cur.click(); await wait(40);   // same word again: closes
    out.closed = { hidden: panel.hidden, selected: document.querySelectorAll('.aw.selected').length, ...article() };
    // deeper layers stay separate: M4 patterns, M5 connected speech, comparison (from the note's Details)
    diff.click(); await wait(60);
    panel.querySelector('.more-about').click();
    const dr = () => document.querySelector('.drawer[data-drawer="' + A + '"]');
    await until(() => dr() && !dr().hidden && dr().querySelector('.compare-attempt'));
    const d = dr();
    const order = (n) => [...document.querySelectorAll('*')].indexOf(n);
    out.deeper = { patterns: !!d.querySelector('details.evidence'), m5: !!d.querySelector('.connected-speech'), compare: !!d.querySelector('.compare-attempt'),
      notInPanel: !panel.contains(d) && !d.contains(panel), panelBeforeDrawer: order(panel) < order(d),
      m5NotInPatterns: !d.querySelector('details.evidence').contains(d.querySelector('.connected-speech')),
      patternsBeforeM5: order(d.querySelector('details.evidence')) < order(d.querySelector('.connected-speech')),
      m5BeforeCompare: order(d.querySelector('.connected-speech')) < order(d.querySelector('.compare-attempt')),
      duplicateSentence: !!d.querySelector('.word-sentence, .rword'), ...article() };
    // Details from the note toggles the same block
    noteA.querySelector('.details-toggle').click(); await wait(40);
    out.detailsClosed = dr().hidden;
    // a not-interpreted word in R08: shown, neutral, opens
    const ni = seg(B).querySelector('.aw.cat-not_interpreted');
    if (ni) { ni.click(); await wait(60); out.notInterpreted = { word: ni.textContent, heading: panel.querySelector('h3').textContent,
      underB: panel.parentElement.dataset.extras === B }; }
    out.text = document.getElementById('article-body').innerText;
    return out;`);
}

main().then(async (r) => { await cleanup(); console.log(JSON.stringify(r)); process.exit(0); })
      .catch(async (e) => { await cleanup(); console.error(String((e && e.stack) || e)); process.exit(1); });
