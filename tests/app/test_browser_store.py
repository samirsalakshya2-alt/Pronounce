"""M13-B browser-local persistence tests against native IndexedDB in Chrome."""

import shutil
import subprocess
from pathlib import Path

import pytest

from apphelpers import PROJECT_ROOT

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def test_browser_store_script_is_served_as_a_local_asset(fake_server):
    client, _ = fake_server
    status, headers, body = client.request("GET", "/static/browser-store.js")
    assert status == 200
    assert "javascript" in headers["Content-Type"]
    assert b"indexedDB.open" in body


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(not CHROME.exists(), reason="Google Chrome not installed")
def test_browser_store_uses_native_indexeddb_and_isolates_databases():
    result = subprocess.run(
        ["node", str(PROJECT_ROOT / "tests" / "app" / "js" / "browser_store.mjs"), str(CHROME)],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "ok BrowserStore native IndexedDB recording/result round-trip and isolation" in result.stdout
