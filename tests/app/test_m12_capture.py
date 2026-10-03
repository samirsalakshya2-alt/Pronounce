"""M12 phase 4: the browser capture core (pure JS, run with node)."""

import re
import shutil
import subprocess

import pytest
from apphelpers import PROJECT_ROOT

STATIC = PROJECT_ROOT / "src" / "pronunciation_lab" / "app" / "static"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_capture_core():
    out = subprocess.run(["node", str(PROJECT_ROOT / "tests" / "app" / "js" / "reader_core.test.js")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr + out.stdout
    assert re.search(r"ok \d+ tests", out.stdout)


def test_worklet_forwards_every_sample_with_its_index():
    src = (STATIC / "capture-worklet.js").read_text()
    assert 'registerProcessor("pronounce-capture"' in src
    assert "this.index += frames" in src and "start: this.index" in src


def test_capture_files_are_served(fake_server):
    client, _ = fake_server
    for name in ("capture-worklet.js", "reader-core.js"):
        status, headers, body = client.request("GET", "/static/" + name)
        assert status == 200 and "javascript" in headers["Content-Type"] and body
