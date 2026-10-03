"""M12 phase 6: compact inline feedback derived from the M4/M5 view (counts of listed items, never a score)."""

import re
import shutil
import subprocess

import pytest
from apphelpers import PROJECT_ROOT
from test_m4_coach import ph, result

from pronunciation_lab.app.pipeline import build_analysis_view
from pronunciation_lab.reader.feedback import compact_feedback, compact_text


def view_of(words):
    return build_analysis_view(result(words))


def test_nothing_listed_means_nothing_stood_out():
    v = view_of([("cat", [ph("k", "k"), ph("æ", "æ"), ph("t", "t")])])
    fb = compact_feedback(v)
    assert fb == {"state": "ok", "notice": 0, "compare": 0, "extra_sounds": 0, "insufficient": 0,
                  "not_interpreted": 0, "message": None}
    assert compact_text(fb) == "Nothing stood out"


def test_counts_patterns_and_ambiguous_items():
    v = view_of([("we", [ph("w", "v", ep=0.0, nbest=[("v", 0.9), ("w", 0.0)]), ph("iː", "iː")]),
                 ("think", [ph("θ", "t", ep=0.3, nbest=[("t", 0.5), ("θ", 0.3)]), ph("ɪ", "ɪ"), ph("ŋ", "ŋ"), ph("k", "k")])])
    fb = compact_feedback(v)
    groups = {g["id"]: len(g["pattern_ids"]) for g in v["coach"]["groups"]}
    assert fb["notice"] == groups.get("single", 0) + groups.get("recurring", 0) + groups.get("not_detected", 0) == 1
    assert fb["compare"] == groups.get("ambiguous", 0) == 1
    assert compact_text(fb) == "1 thing to notice · 1 to compare"


def test_extra_sounds_and_insufficient_evidence_are_not_in_the_line():
    extra = [{"phone": "ə", "start_ms": 150.0, "end_ms": 170.0, "frame_start": 7, "frame_end": 8}]
    v = view_of([("aka", [ph("a", "a", extras=extra), ph("k", None, ep=0.0, nbest=[("ə", 0.02)], source="derived",
                                                            start=320.0, end=380.0), ph("a", "a")])])
    fb = compact_feedback(v)
    cats = [c["interpretation"]["category"] for c in v["reduction"]["candidates"]]
    assert fb["extra_sounds"] == 1 and fb["insufficient"] == cats.count("insufficient_evidence")
    # the not-detected /k/ is one M4 pattern; its M5 candidate is the same sound and is not counted twice
    assert fb["notice"] == 1


def test_m5_candidate_on_an_m4_sound_is_counted_once():
    # /t/ not decoded with no room between its neighbours: an M4 not-detected pattern and an M5 candidate
    v = view_of([("cat", [ph("k", "k"), ph("æ", "æ"), ph("t", None, ep=0.0, nbest=[("ə", 0.02)], source="derived",
                                                          start=240.0, end=260.0)]),
                 ("go", [ph("ɡ", "ɡ", start=260.0, end=280.0), ph("oʊ", "oʊ")])])
    obs_in_patterns = {o for p in v["coach"]["patterns"] for o in p["observation_ids"]}
    m5 = [c for c in v["reduction"]["candidates"] if c["interpretation"]["category"] != "insufficient_evidence"]
    assert m5 and all(c["observation_id"] in obs_in_patterns for c in m5)
    assert compact_feedback(v)["notice"] == len(v["coach"]["patterns"])


@pytest.mark.parametrize("status", ["failed", "blocked"])
def test_no_evidence(status):
    v = build_analysis_view(result([("a", [ph("a", "a")])], status=status))
    fb = compact_feedback(v)
    assert fb["state"] != "ok" and fb["notice"] == fb["compare"] == 0 and compact_text(fb)


def test_wording_never_scores_or_judges():
    for fb in ({"state": "ok", "notice": n, "compare": c} for n in range(4) for c in range(3)):
        text = compact_text(fb).lower()
        assert not re.search(r"score|%|wrong|incorrect|error|bad|good|best|worst|rank", text), text
    assert compact_feedback(None) is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_feedback_js_helpers():
    out = subprocess.run(["node", str(PROJECT_ROOT / "tests" / "app" / "js" / "reader_feedback.test.js")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr + out.stdout
