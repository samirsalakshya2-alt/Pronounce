// Drive the real UI in headless Chrome over the DevTools protocol (no extra packages).
// Usage: node browser_full_feedback.mjs <base-url> <chrome-path> <screenshot-path>
// Prints one JSON object with what the page showed. Chrome is a child process and
// is always killed before exit.
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base, chromePath, shotPath] = process.argv.slice(2);
const profile = mkdtempSync(join(tmpdir(), "pl-chrome-"));
const chrome = spawn(chromePath, [
  "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run",
  "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required", "--window-size=1200,2000", "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function cleanup() { try { chrome.kill("SIGKILL"); } catch {} try { rmSync(profile, { recursive: true, force: true }); } catch {} }

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
    if (existsSync(f)) port = readFileSync(f, "utf8").split("\n")[0];
    else await sleep(100);
  }
  if (!port) throw new Error("Chrome did not start");
  const version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
  const browser = await connect(version.webSocketDebuggerUrl);
  const origin = new URL(base).origin;
  await browser.send("Browser.grantPermissions", { origin, permissions: ["clipboardReadWrite", "clipboardSanitizedWrite"] });
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(base)}`, { method: "PUT" })).json();
  const page = await connect(target.webSocketDebuggerUrl);
  await page.send("Runtime.enable");
  await page.send("Emulation.setFocusEmulationEnabled", { enabled: true });
  const run = async (expr) => {
    const r = await page.send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  await run(`for (let i = 0; i < 100 && !document.querySelector('#benchmark-select option'); i++) await new Promise(r => setTimeout(r, 100));`);

  const out = await run(`
    const $ = (id) => document.getElementById(id);
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    const detailOf = (word) => { [...document.querySelectorAll('#sentence .word')].find(b => b.textContent === word).click();
      return $('word-detail').innerText; };
    const fullHidden0 = $('full-feedback').hidden;
    document.querySelector('[data-tab=benchmark]').click();
    $('benchmark-select').value = 'R01'; $('benchmark-select').dispatchEvent(new Event('change'));
    $('analyze-btn').click();
    for (let i = 0; i < 300 && !document.querySelector('#sentence .word'); i++) await wait(100);
    await wait(300);
    const detailBefore = detailOf('three');
    const wordButtons = [...document.querySelectorAll('#sentence .word')].map(b => b.textContent + ':' + b.className);

    // Word-level playback (existing behaviour)
    const wrow = document.querySelector('#word-detail tr[data-sound]');
    wrow.querySelector('button').click(); await wait(80);
    const wordPlaybackHighlighted = wrow.classList.contains('playing'); await wait(500);

    $('full-feedback-btn').click(); await wait(200);
    const full = $('full-feedback');
    const fullView = {
      visible: !full.hidden, heading: full.querySelector('h3').textContent,
      wordHeadings: [...full.querySelectorAll('h4[data-full-word]')].map(h => h.innerText.split(' — ')[0]),
      soundRows: full.querySelectorAll('tr[data-full-sound]').length,
      playWordButtons: [...full.querySelectorAll('h4 button')].filter(b => b.textContent === '▶ Play word').length,
      playSoundButtons: [...full.querySelectorAll('tr[data-full-sound] button')].filter(b => b.textContent === '▶ Play sound').length,
      summaryLine: full.querySelector('p.small').textContent,
      wordDetailStillVisible: !$('word-detail').hidden,
    };

    // Playback from the full view
    const frow = full.querySelector('tr[data-full-sound]');
    frow.querySelector('button').click(); await wait(80);
    const fullSoundHighlighted = frow.classList.contains('playing'); await wait(500);
    const fullSoundCleared = !frow.classList.contains('playing');
    const fhead = full.querySelector('h4[data-full-word]');
    fhead.querySelector('button').click(); await wait(80);
    const fullWordHighlighted = fhead.classList.contains('playing');

    // Copy
    window.focus();
    $('copy-feedback-btn').click(); await wait(500);
    let clipboard = null, clipboardError = null;
    try { clipboard = await navigator.clipboard.readText(); } catch (e) { clipboardError = String(e); }
    const copyStatus = $('copy-feedback-status').textContent;

    // Existing word-level feedback after the full view was used
    const detailAfter = detailOf('three');
    // Play whole recording still works
    $('play-all-btn').click(); await wait(100);

    // A new analysis closes the previous full view
    $('benchmark-select').value = 'R05'; $('benchmark-select').dispatchEvent(new Event('change'));
    $('analyze-btn').click();
    for (let i = 0; i < 300 && document.querySelector('#sentence .word')?.textContent !== 'very'; i++) await wait(100);
    const fullHiddenAfterNewAnalysis = $('full-feedback').hidden && $('full-feedback').innerHTML === '';
    $('full-feedback-btn').click(); await wait(200);
    const r05Heading = $('full-feedback').querySelector('h3').textContent;
    // reopen R01's full view for the screenshot
    $('benchmark-select').value = 'R01'; $('benchmark-select').dispatchEvent(new Event('change'));
    $('analyze-btn').click();
    for (let i = 0; i < 300 && document.querySelector('#sentence .word')?.textContent !== 'think'; i++) await wait(100);
    $('full-feedback-btn').click(); await wait(300);
    window.scrollTo(0, 0);
    return { fullHidden0, wordButtons, detailBefore, detailAfter, wordPlaybackHighlighted, fullView,
             fullSoundHighlighted, fullSoundCleared, fullWordHighlighted, clipboard, clipboardError, copyStatus,
             fullHiddenAfterNewAnalysis, r05Heading, errorBox: $('error-box').hidden ? null : $('error-box').textContent };
  `);
  const shot = await page.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  writeFileSync(shotPath, Buffer.from(shot.data, "base64"));
  page.close(); browser.close();
  return out;
}

main().then((r) => { cleanup(); console.log(JSON.stringify(r)); process.exit(0); })
      .catch((e) => { cleanup(); console.error(String(e && e.stack || e)); process.exit(1); });
