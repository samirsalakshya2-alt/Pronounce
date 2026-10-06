"""M10 pattern states: a deterministic fold over a pattern's fresh sessions in time order (pure).

Two separate axes, so "where it occurs" is never collapsed into "how it is changing":

* lifecycle `state` — INSUFFICIENT_HISTORY, EMERGING, PERSONAL_RECURRING, PERSISTENT, IMPROVING, STABLE, RETIRED,
  REGRESSED;
* `scope` — PERSONAL_RECURRING (crosses texts), ARTICLE_BOUND, WORD_SPECIFIC, CONTEXT_SPECIFIC (personal and
  concentrated in one context, see context.py), INSUFFICIENT_HISTORY.

Lifecycle rules (all counts are clear observations in FRESH readings; expected = baseline rate × later chances):

    → EMERGING            every personal gate except the third session (≥ 3 clear, ≥ 2 words, ≥ 2 articles, direction)
    → PERSONAL_RECURRING  personal gates on the evidence so far: ≥ 3 clear, ≥ 3 sessions, ≥ 2 articles, ≥ 2 real
                          words, ≥ half of the sound's clear differences going this way. The evidence up to this
                          point is the BASELINE window; everything after it is the LATER window.
    → STABLE              later window: ≥ 40 chances in ≥ 3 sessions and ≥ 2 articles, mostly words not read in the
                          baseline, the baseline predicting ≥ 3, and observed ≤ the retire share of that prediction
    → IMPROVING           later window: ≥ 20 chances in ≥ 2 sessions, predicted ≥ 2, observed ≤ the improving share
    → PERSISTENT          later window: ≥ 3 sessions, predicted ≥ 3, observed ≥ 0.75 of the prediction
    STABLE → RETIRED      a quiet watch window of ≥ 2 more sessions with chances (silently monitored from then on)
    STABLE/RETIRED → REGRESSED   the post-stable window meets the personal gates again (never one observation);
                          the post-stable window becomes the new baseline

IMPROVING and PERSISTENT are separated by a hysteresis band (observed between 0.5 and 0.75 of the prediction keeps
the current state), and the later window is cumulative, so one observation cannot make a state oscillate. Every
transition is kept with its evidence; nothing is overwritten.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from pronunciation_lab.longitudinal.calibration import LongitudinalCalibration

STATES_VERSION = "m10-states.1"
LIFECYCLE = ("INSUFFICIENT_HISTORY", "EMERGING", "PERSONAL_RECURRING", "PERSISTENT", "IMPROVING", "STABLE",
             "RETIRED", "REGRESSED")
SCOPES = ("PERSONAL_RECURRING", "CONTEXT_SPECIFIC", "WORD_SPECIFIC", "ARTICLE_BOUND", "INSUFFICIENT_HISTORY")
ACTIVE = ("PERSONAL_RECURRING", "PERSISTENT", "IMPROVING", "REGRESSED")


def window(points: list[dict[str, Any]]) -> dict[str, Any]:
    clear_words: Counter = Counter()
    clear_by_word: Counter = Counter()
    opps_by_word: Counter = Counter()
    for p in points:
        clear_words.update(p["clear_words"])
        clear_by_word.update(p["clear_by_word"])
        opps_by_word.update(p["opps_by_word"])
    diffs = [p["clear_differences"] for p in points if p["clear_differences"] is not None]
    clear = sum(p["clear"] for p in points)
    opps = sum(p["opportunities"] for p in points)
    return {
        "sessions": len(points), "articles": len({p["article"] for p in points}),
        "opportunities": opps, "clear": clear, "ambiguous": sum(p["ambiguous"] for p in points),
        "clear_sessions": sum(1 for p in points if p["clear"]),
        "clear_articles": len({p["article"] for p in points if p["clear"]}),
        "clear_words": dict(clear_words), "clear_by_word": dict(clear_by_word), "opps_by_word": opps_by_word,
        "clear_differences": sum(diffs) if diffs else None,
        "direction_share": round(clear / sum(diffs), 3) if diffs and sum(diffs) else None,
        "rate": round(clear / opps, 4) if opps else None,
        "session_ids": [p["session_id"] for p in points],
    }


class Window:
    """A running window (identical to `window(points[a:b])`, accumulated point by point so the fold is linear)."""

    def __init__(self) -> None:
        self.points: list[dict[str, Any]] = []
        self.clear_words: Counter = Counter()
        self.clear_by_word: Counter = Counter()
        self.opps_by_word: Counter = Counter()
        self.articles: set = set()
        self.clear_articles: set = set()
        self.clear = self.opps = self.amb = self.clear_sessions = 0
        self.diffs: int | None = None

    def add(self, p: dict[str, Any]) -> "Window":
        self.points.append(p)
        self.clear_words.update(p["clear_words"])
        self.clear_by_word.update(p["clear_by_word"])
        self.opps_by_word.update(p["opps_by_word"])
        self.articles.add(p["article"])
        self.clear += p["clear"]
        self.opps += p["opportunities"]
        self.amb += p["ambiguous"]
        if p["clear"]:
            self.clear_sessions += 1
            self.clear_articles.add(p["article"])
        if p["clear_differences"] is not None:
            self.diffs = (self.diffs or 0) + p["clear_differences"]
        return self

    def view(self) -> dict[str, Any]:
        d = self.diffs
        return {"sessions": len(self.points), "articles": len(self.articles), "opportunities": self.opps,
                "clear": self.clear, "ambiguous": self.amb, "clear_sessions": self.clear_sessions,
                "clear_articles": len(self.clear_articles), "clear_words": dict(self.clear_words),
                "clear_by_word": dict(self.clear_by_word), "opps_by_word": Counter(self.opps_by_word),
                "clear_differences": d, "direction_share": round(self.clear / d, 3) if d else None,
                "rate": round(self.clear / self.opps, 4) if self.opps else None,
                "session_ids": [p["session_id"] for p in self.points]}


def personal_gates(w: dict[str, Any], cal: LongitudinalCalibration, fluency: bool) -> dict[str, bool]:
    gates = {"clear": w["clear"] >= cal.personal_min_clear,
             "sessions": w["clear_sessions"] >= cal.personal_min_sessions,
             "articles": w["clear_articles"] >= cal.personal_min_articles}
    if not fluency:
        gates["words"] = len(w["clear_words"]) >= cal.personal_min_words
        gates["direction"] = w["direction_share"] is not None and w["direction_share"] >= cal.direction_min_share
    return gates


def classify_scope(w: dict[str, Any], cal: LongitudinalCalibration, fluency: bool) -> str:
    if all(personal_gates(w, cal, fluency).values()):
        return "PERSONAL_RECURRING"
    # word-specific: every clear observation (word fragments included) in one word
    if not fluency and w["clear"] >= cal.word_min_clear and len(w["clear_by_word"]) == 1 and len(w["clear_words"]) == 1 \
            and w["clear_sessions"] >= cal.word_min_sessions:
        return "WORD_SPECIFIC"
    if w["clear"] >= cal.article_bound_min_clear and w["clear_articles"] == 1:
        return "ARTICLE_BOUND"
    return "INSUFFICIENT_HISTORY"


def _emerging(w, cal, fluency: bool) -> bool:
    return w["clear"] >= cal.emerging_min_clear and w["clear_sessions"] >= cal.emerging_min_sessions \
        and w["clear_articles"] >= cal.personal_min_articles \
        and (fluency or (len(w["clear_words"]) >= cal.personal_min_words and w["direction_share"] is not None
                         and w["direction_share"] >= cal.direction_min_share))


def later_check(baseline: dict[str, Any], later: dict[str, Any], cal: LongitudinalCalibration,
                noise: dict[str, Any], fluency: bool) -> dict[str, Any]:
    """The later window against the baseline: expected = baseline rate × later chances (an expected count, not a
    probability), and which thresholds it meets."""
    rate = baseline["rate"] or 0.0
    expected = round(rate * later["opportunities"], 2)
    base_words = set(baseline["opps_by_word"])
    new = sum(n for w, n in later["opps_by_word"].items() if w not in base_words)
    fresh_share = 1.0 if fluency else (round(new / later["opportunities"], 3) if later["opportunities"] else 0.0)
    eff = noise["effective"]
    retire = (later["opportunities"] >= cal.retire_min_opportunities and later["sessions"] >= cal.retire_min_sessions
              and later["articles"] >= cal.retire_min_articles and fresh_share >= cal.retire_min_fresh_word_share
              and expected >= cal.retire_min_expected
              and later["clear"] <= math.floor(expected * eff["retire_max_share"] + 1e-9))
    improving = (later["opportunities"] >= cal.improve_min_opportunities
                 and later["sessions"] >= cal.improve_min_sessions and expected >= cal.improve_min_expected
                 and later["clear"] <= expected * eff["improve_max_share"])
    persistent = (later["sessions"] >= cal.persist_min_sessions and expected >= cal.persist_min_expected
                  and later["clear"] >= expected * cal.persist_min_share)
    return {"expected": expected, "observed": later["clear"], "opportunities": later["opportunities"],
            "sessions": later["sessions"], "articles": later["articles"], "fresh_word_share": fresh_share,
            "baseline_rate": baseline["rate"], "retire": retire, "improving": improving, "persistent": persistent}


def _summary(w):
    return {k: w[k] for k in ("sessions", "articles", "opportunities", "clear", "ambiguous", "clear_sessions",
                              "clear_articles", "direction_share", "rate")} | {"words": len(w["clear_words"])}


def fold(points: list[dict[str, Any]], cal: LongitudinalCalibration, noise: dict[str, Any],
         fluency: bool = False) -> dict[str, Any]:
    state = "INSUFFICIENT_HISTORY"
    transitions: list[dict[str, Any]] = []
    start = 0                       # first point of the current establishment window
    est = None                      # index of the establishing point (end of baseline)
    stable_at = None
    baseline = None
    last_check = None

    def move(i, to, reason, evidence):
        nonlocal state
        transitions.append({"at_session": points[i]["session_id"], "time": points[i]["time"], "from": state,
                            "to": to, "reason": reason, "evidence": evidence})
        state = to

    grow = Window()                 # the establishment window: points[start:i + 1]
    later_w = Window()              # the later window: points[est + 1:i + 1]
    post_w = Window()               # the post-stable window: points[stable_at + 1:i + 1]
    for i in range(len(points)):
        if state in ("INSUFFICIENT_HISTORY", "EMERGING"):
            w = grow.add(points[i]).view()
            if all(personal_gates(w, cal, fluency).values()):
                est, baseline = i, w
                later_w = Window()
                move(i, "PERSONAL_RECURRING", "recurring across texts: the personal gates are met", _summary(w))
            elif state == "INSUFFICIENT_HISTORY" and _emerging(w, cal, fluency):
                move(i, "EMERGING", "recurring in several readings, not enough history to call it personal",
                     _summary(w))
        elif state in ("PERSONAL_RECURRING", "PERSISTENT", "IMPROVING", "REGRESSED"):
            later = later_w.add(points[i]).view()
            chk = later_check(baseline, later, cal, noise, fluency)
            last_check = chk
            ev = {k: chk[k] for k in ("expected", "observed", "opportunities", "sessions", "articles",
                                      "fresh_word_share", "baseline_rate")}
            if chk["retire"]:
                stable_at = i
                post_w = Window()
                move(i, "STABLE", "no longer recurring strongly enough to prioritise", ev)
            elif chk["improving"] and state != "IMPROVING":
                move(i, "IMPROVING", "occurring less often than the baseline predicts", ev)
            elif chk["persistent"] and state != "PERSISTENT":
                move(i, "PERSISTENT", "still recurring at its baseline rate despite more chances", ev)
        elif state in ("STABLE", "RETIRED"):
            post = post_w.add(points[i]).view()
            if all(personal_gates(post, cal, fluency).values()):
                start, est, baseline = stable_at + 1, i, post
                later_w = Window()
                move(i, "REGRESSED", "had become stable, then met the personal gates again", _summary(post))
            elif state == "STABLE" and post["sessions"] >= cal.watch_min_sessions:
                move(i, "RETIRED", "quiet through the watch window; monitored silently", _summary(post))

    total = window(points)
    out = {"version": STATES_VERSION, "state": state, "scope": classify_scope(total, cal, fluency),
           "transitions": transitions, "totals": _summary(total), "clear_words": total["clear_words"],
           "baseline": None, "later": None, "post_stable": None}
    if baseline is not None:
        out["baseline"] = _summary(baseline) | {"session_ids": baseline["session_ids"],
                                                "opps_by_word": dict(baseline["opps_by_word"])}
    if est is not None and state not in ("STABLE", "RETIRED"):
        out["later"] = later_check(baseline, later_w.view(), cal, noise, fluency) if baseline else None
    elif last_check is not None:
        out["later"] = last_check
    if stable_at is not None and state in ("STABLE", "RETIRED"):
        out["post_stable"] = _summary(post_w.view())
    return out
