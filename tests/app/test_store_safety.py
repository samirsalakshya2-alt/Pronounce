"""The test suite can never touch the user's real reader store (see tests/conftest.py)."""

from pathlib import Path

import store_guard as guard

from pronunciation_lab.reader.store import DEFAULT_ROOT, ReaderStore


def test_a_default_store_inside_tests_is_isolated():
    st = ReaderStore()
    assert st.root != DEFAULT_ROOT and guard.REAL_STORE not in [st.root, *st.root.parents]
    assert DEFAULT_ROOT == Path.home() / ".pronunciation_lab" / "reader"     # the real default itself is unchanged


def test_the_session_guard_detects_any_change(tmp_path):
    root = tmp_path / "reader"
    (root / "sessions" / "a").mkdir(parents=True)
    f = root / "sessions" / "a" / "attempt.json"
    f.write_text("{}")
    before = guard.store_snapshot(root)
    assert guard.store_changes(before, guard.store_snapshot(root)) == []
    f.write_text('{"state": "x"}')                          # rewritten (the M9-era launcher defect re-saved files)
    (root / "sessions" / "a" / "new.json").write_text("{}")
    changes = guard.store_changes(before, guard.store_snapshot(root))
    assert "changed: sessions/a/attempt.json" in changes and "created: sessions/a/new.json" in changes


def test_the_guard_is_active_for_this_session(request):
    assert hasattr(request.config, "_real_store_before")
