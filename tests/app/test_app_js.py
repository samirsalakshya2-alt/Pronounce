"""Front-end checks: JS syntax, JS helper unit tests (node), and HTML/JS wiring."""

import re
import shutil
import subprocess

import pytest
from apphelpers import PROJECT_ROOT

STATIC = PROJECT_ROOT / "src" / "pronunciation_lab" / "app" / "static"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@needs_node
def test_app_js_syntax():
    subprocess.run(["node", "--check", str(STATIC / "app.js")], check=True, capture_output=True)


@needs_node
def test_js_helpers():
    out = subprocess.run(["node", str(PROJECT_ROOT / "tests" / "app" / "js" / "app_helpers.test.js")],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert re.search(r"ok \d+ tests", out.stdout)


def test_every_element_the_script_uses_exists_in_the_page():
    html = (STATIC / "index.html").read_text()
    js = (STATIC / "app.js").read_text()
    ids_in_html = set(re.findall(r'id="([^"]+)"', html))
    ids_in_js = set(re.findall(r'\$\("([^"]+)"\)', js))
    assert ids_in_js, "no element lookups found"
    assert ids_in_js <= ids_in_html, ids_in_js - ids_in_html


def test_page_loads_only_local_assets():
    html = (STATIC / "index.html").read_text()
    assert not re.search(r'(src|href)="https?://', html)
    assert '<script src="/static/app.js">' in html


def test_ui_text_never_offers_a_score():
    for name in ("index.html", "app.js"):
        text = (STATIC / name).read_text().lower()
        assert "score" not in text and "grade" not in text and "wrong" not in text


# ----------------------------------------------------------------------
# Full Recording Feedback
# ----------------------------------------------------------------------

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def test_full_feedback_controls_are_in_the_results_panel():
    html = (STATIC / "index.html").read_text()
    results = html[html.index('id="results-panel"'):html.index('id="history-panel"')]
    assert '<button type="button" id="full-feedback-btn">Full Recording Feedback</button>' in results
    assert '<div id="full-feedback" hidden></div>' in results
    # the existing word-level elements are all still there, in their original order
    order = [results.index(f'id="{i}"') for i in ("sentence", "word-detail", "full-feedback-btn", "full-feedback", "heard-details")]
    assert order == sorted(order)


@needs_node
def test_full_feedback_text_for_a_real_analysis(real_server):
    """The copied text of a real R01 analysis covers every word and every sound."""
    import json as _json

    client, _ = real_server
    status, view = client.post_json("/api/analyze-benchmark", {"recording_id": "R01"})
    assert status == 200
    script = (
        "const h=require(process.argv[1]);let d='';process.stdin.on('data',c=>d+=c);"
        "process.stdin.on('end',()=>process.stdout.write(h.fullFeedbackText(JSON.parse(d))));"
    )
    out = subprocess.run(["node", "-e", script, str(STATIC / "app.js")], input=_json.dumps(view),
                         capture_output=True, text=True, check=True).stdout
    assert "- Target text: Think about the three things that you want to change." in out
    assert "- Words: 10" in out and "- Heard as expected: 26" in out and "- Unclear: 3" in out and "- Not detected: 3" in out
    for i, w in enumerate(view["words"], 1):
        assert f"### {i}. {w['word']} — " in out
    rows = [line for line in out.splitlines() if re.match(r"^\| \d+ \|", line)]
    assert len(rows) == sum(len(w["sounds"]) for w in view["words"]) == 32
    assert "Everything the recogniser heard: /" + " ".join(view["heard_sequence"]) + "/" in out
    assert "Extra sound heard right after: /ɪ/" in out and "(estimated)" in out
    assert "## How to read this" in out


@needs_node
@pytest.mark.skipif(not __import__("os").path.exists(CHROME), reason="Google Chrome not installed")
def test_full_feedback_in_a_real_browser(real_server, tmp_path):
    """Headless Chrome against the real app: open, copy, word view unchanged, playback works."""
    import json as _json

    client, _ = real_server
    shot = tmp_path / "full_feedback.png"
    proc = subprocess.run(
        ["node", str(PROJECT_ROOT / "tests" / "app" / "js" / "browser_full_feedback.mjs"), client.base + "/", CHROME, str(shot)],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    r = _json.loads(proc.stdout)

    assert r["errorBox"] is None and r["fullHidden0"] is True
    fv = r["fullView"]
    assert fv["visible"] and fv["heading"] == "Full Recording Feedback — “Think about the three things that you want to change.”"
    assert fv["wordHeadings"] == [f"{i}. “{w}”" for i, w in enumerate(
        ["think", "about", "the", "three", "things", "that", "you", "want", "to", "change"], 1)]
    assert fv["soundRows"] == 32 and fv["playSoundButtons"] == 32 and fv["playWordButtons"] == 10
    assert "Words: 10" in fv["summaryLine"] and "Heard as expected: 26" in fv["summaryLine"]
    assert fv["wordDetailStillVisible"]

    # existing word-level feedback is identical before and after using the full view
    assert r["detailBefore"] == r["detailAfter"] and "“three”" in r["detailBefore"]
    assert len(r["wordButtons"]) == 10
    # exact-audio playback, both views
    assert r["wordPlaybackHighlighted"] and r["fullSoundHighlighted"] and r["fullSoundCleared"] and r["fullWordHighlighted"]
    # copy puts the complete structured feedback on the clipboard
    assert r["clipboardError"] is None, r["clipboardError"]
    clip = r["clipboard"]
    assert clip.startswith("# Pronunciation feedback — full recording") and "### 10. change — " in clip
    assert len([line for line in clip.splitlines() if re.match(r"^\| \d+ \|", line)]) == 32
    assert r["copyStatus"].startswith("Copied")
    # a new analysis does not keep the previous recording's full view
    assert r["fullHiddenAfterNewAnalysis"] and "Very few people" in r["r05Heading"]
    assert shot.stat().st_size > 10_000
