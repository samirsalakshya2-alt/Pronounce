"""Test-suite guard: the user's real reader store (~/.pronunciation_lab/reader) is never touched by tests.

1. Every test gets an isolated default store: `ReaderStore()` without a root points to a temporary directory
   (the module constant DEFAULT_ROOT itself is left alone, so tests can still check the real default path).
2. The whole session fails if any file under the real store was created, changed or removed while the tests
   ran — including from subprocesses (e.g. the launcher tests), which the in-process redirect cannot reach.
"""

from __future__ import annotations

import pytest

from store_guard import REAL_STORE, store_changes, store_snapshot


def pytest_sessionstart(session):
    session.config._real_store_before = store_snapshot(REAL_STORE)


def pytest_sessionfinish(session, exitstatus):
    before = getattr(session.config, "_real_store_before", None)
    if before is None:
        return
    changes = store_changes(before, store_snapshot(REAL_STORE))
    if changes:
        session.config._real_store_changes = changes
        print("\nSTORE SAFETY: the test session touched the user's real reader store:\n  " + "\n  ".join(changes[:20]))
        session.exitstatus = 1


@pytest.fixture(autouse=True)
def _isolated_default_reader_store(tmp_path_factory, monkeypatch):
    try:
        from pronunciation_lab.reader.store import ReaderStore
    except Exception:   # benchmark / phase0 environments without the reader
        yield
        return
    default = tmp_path_factory.mktemp("default_reader_store")
    original = ReaderStore.__init__

    def isolated(self, root=None):
        original(self, root if root is not None else default)

    monkeypatch.setattr(ReaderStore, "__init__", isolated)
    yield
