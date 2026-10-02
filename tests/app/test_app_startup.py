"""Start the real launcher in a subprocess, use it, stop it."""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest
from conftest import PROJECT_ROOT


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launch(port: int, data_dir: Path, *extra: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "run_app.py"), "--port", str(port),
         "--no-browser", "--data-dir", str(data_dir), *extra],
        cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


def wait_ready(port: int, proc: subprocess.Popen, timeout: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"app exited early: {proc.stdout.read()}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
                return json.loads(r.read())
        except OSError:
            time.sleep(0.2)
    raise AssertionError("app did not become ready")


def stop(proc: subprocess.Popen, sig: int, timeout: float = 30.0) -> str:
    """Send `sig`; if the app does not exit in time, kill it (never leave an orphan) and fail."""
    proc.send_signal(sig)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate(timeout=10)
        raise AssertionError(f"app did not stop on signal {sig}; killed. Output:\n{out}") from None
    return out


def pl_workspaces() -> set[str]:
    return {p.name for p in Path(tempfile.gettempdir()).glob("pronunciation-lab-*")}


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
def test_start_use_and_stop(tmp_path, stop_signal):
    before = pl_workspaces()
    port = free_port()
    proc = launch(port, tmp_path, "--no-warmup")
    try:
        status = wait_ready(port, proc)
        assert status["default_engine"] == "wav2vec2_raw"
        assert {e["id"]: e["state"] for e in status["engines"]}["wavlm"] == "unresolved"
        assert status["benchmark_recordings"] == []  # empty data dir: no benchmark tab
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            assert b"Pronunciation Lab" in r.read()
        created = pl_workspaces() - before
        assert len(created) == 1
    finally:
        out = stop(proc, stop_signal)
    assert proc.returncode == 0, out
    assert f"Pronunciation Lab running at http://127.0.0.1:{port}/" in out
    assert "Stopped." in out
    assert pl_workspaces() - before == set()  # the session workspace was removed


def test_port_in_use_fails_cleanly(tmp_path):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        before = pl_workspaces()
        proc = launch(port, tmp_path, "--no-warmup")
        out, _ = proc.communicate(timeout=60)
    assert proc.returncode == 2
    assert f"Could not start on 127.0.0.1:{port}" in out
    assert pl_workspaces() - before == set()


def test_help():
    out = subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "run_app.py"), "--help"],
                         capture_output=True, text=True, cwd=str(PROJECT_ROOT), timeout=60)
    assert out.returncode == 0 and "--port" in out.stdout and "--no-browser" in out.stdout


def test_killed_app_leaves_nothing_after_the_next_start(tmp_path):
    """SIGKILL skips cleanup; the next start removes the orphaned workspace."""
    before = pl_workspaces()
    port = free_port()
    proc = launch(port, tmp_path, "--no-warmup")
    wait_ready(port, proc)
    orphan = pl_workspaces() - before
    assert len(orphan) == 1
    proc.kill()
    proc.wait(timeout=30)
    assert pl_workspaces() - before == orphan  # left behind by the kill

    port2 = free_port()
    proc2 = launch(port2, tmp_path, "--no-warmup")
    try:
        wait_ready(port2, proc2)
        remaining = pl_workspaces() - before
        assert orphan.isdisjoint(remaining) and len(remaining) == 1  # only the new session's
    finally:
        stop(proc2, signal.SIGINT)
    assert pl_workspaces() - before == set()


def test_sigint_stops_the_app_even_if_inherited_as_ignored(tmp_path):
    """Background launches from non-interactive shells start with SIGINT ignored."""
    before = pl_workspaces()
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "run_app.py"), "--port", str(port), "--no-browser",
         "--no-warmup", "--data-dir", str(tmp_path)],
        cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
    )
    try:
        wait_ready(port, proc)
    finally:
        out = stop(proc, signal.SIGINT)
    assert proc.returncode == 0 and "Stopped." in out
    assert pl_workspaces() - before == set()
