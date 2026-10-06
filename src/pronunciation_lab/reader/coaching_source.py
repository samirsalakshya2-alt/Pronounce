"""M9 adapter: the reader store → eligible readings for the coaching core (the only M9 code that does I/O).

Read-only. Eligibility is the reader's own decision (reader/status.py: an attempt counts when its summary
reason is None — identified, feedback shown, not discarded or marked for re-recording, analysed by the
session's engine). Withheld sentences (M7) and continued speech never reach M9: a withheld view has no
observations, and a sentence-only view contains only the sentence.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from pronunciation_lab.app.engine_compare import _pair_word  # M5's own pairing of two engines' observations
from pronunciation_lab.coaching.evidence import Reading, ReadingInput, fragment_words, text_key
from pronunciation_lab.reader import model as M
from pronunciation_lab.reader.status import attempt_status


def _agreement(primary_obs: list[dict[str, Any]], other_obs: list[dict[str, Any]]) -> dict[str, str]:
    """Per primary observation: does the other engine's paired observation say the same? (recorded, never resolved)"""
    def by_word(obs):
        out = defaultdict(list)
        for o in obs:
            if o.get("kind") == "sound":
                out[o.get("word_index")].append(o)
        return out

    def outcome(o):
        t = o.get("type")
        if t == "expected":
            return ("expected", None)
        if t in ("substitution_candidate", "ambiguous"):
            return ("heard_other", o.get("competitor") or o.get("observed"))
        return (t, None)

    a, b = by_word(primary_obs), by_word(other_obs)
    out: dict[str, str] = {}
    for wi, obs in a.items():
        for x, y in _pair_word(obs, b.get(wi, [])):
            if x is None:
                continue
            out[x["id"]] = "only_this_engine" if y is None else ("agree" if outcome(x) == outcome(y) else "differ")
    return out


def _sentence_ref(attempt: dict[str, Any], job: dict[str, Any], view: dict[str, Any]) -> dict[str, Any]:
    regions = (view.get("boundary") or {}).get("regions") or []
    target = next((r for r in regions if r.get("kind") == "target" and r.get("play")), None)
    if target:
        return dict(target["play"])
    duration = view.get("duration_ms") or (attempt.get("audio") or {}).get("duration_ms") or 0.0
    return M.playback_reference(attempt, job, [0.0, duration], [0.0, duration], "sentence")


def load_inputs(store) -> tuple[list[ReadingInput], Counter]:
    """Every eligible reading in the store, with the evidence its views hold, plus reading-level exclusions."""
    exclusions: Counter = Counter()
    inputs: list[ReadingInput] = []
    articles: dict[str, dict[str, Any] | None] = {}
    for sid in store.session_ids():
        session = store.load_session(sid)
        aid_ = session["article_id"]
        if aid_ not in articles:
            try:
                articles[aid_] = store.load_article(aid_)
            except KeyError:
                articles[aid_] = None
        article = articles[aid_]
        fragments = fragment_words(article["text"]) if article else frozenset()
        for aid in session["attempt_ids"]:
            inp, reason = _attempt_input(store, session, aid, article, fragments)
            if reason == "missing":
                continue
            if reason is not None:
                exclusions[reason] += 1
                continue
            inputs.append(inp)
    return inputs, exclusions


def _attempt_input(store, session: dict[str, Any], aid: str, article: dict[str, Any] | None,
                   fragments: frozenset[str]) -> tuple[ReadingInput | None, str | None]:
    """One stored attempt → a ReadingInput, or the reason it is not one ("missing" when the attempt is gone).
    Shared by load_inputs (recent history, coaching) and load_session_inputs (this reading)."""
    sid = session["id"]
    try:
        attempt = store.load_attempt(sid, aid)
    except KeyError:
        return None, "missing"
    jobs = []
    for j in attempt.get("job_ids", []):
        try:
            jobs.append(store.load_job(sid, aid, j))
        except KeyError:
            continue
    primary = next((j for j in reversed(jobs) if j["kind"] == "primary"), None)
    status = attempt_status(attempt, primary, session["engine_default"])
    if status["summary"] is not None:
        return None, "other_engine" if status.get("summary_code") == "other_engine" else "not_eligible"
    view = store.load_view(primary)
    if not view or view.get("state") == "boundary_withheld" or not (view.get("coach") or {}).get("observations"):
        return None, "no_view"
    other = next((j for j in reversed(jobs) if j["kind"] == "comparison" and j["state"] == "SUCCEEDED"), None)
    other_view = store.load_view(other) if other else None
    agreement = _agreement(view["coach"]["observations"], (other_view.get("coach") or {}).get("observations", [])) \
        if other_view and other_view.get("state") != "boundary_withheld" else {}
    analysis = status.get("analysis")
    reduction = view.get("reduction") or {}
    reading = Reading(
        reading_id=f"{sid}:{aid}", session_id=sid, attempt_id=aid, job_id=primary["id"],
        segment_id=attempt["segment_id"], engine=primary["engine_id"], recorded_at=attempt["created_at"],
        sentence_key=text_key(attempt["target_text"]), sentence_text=attempt["target_text"],
        article_key=(article or {}).get("text_sha256") or session["article_id"],
        analysis_state=analysis if analysis in ("ok", "low_confidence") else "unknown",
        fluency_reliable=((view.get("fluency") or {}).get("metrics") or {}).get("activity_reliable"),
        has_comparison=bool(agreement), sentence_ref=_sentence_ref(attempt, primary, view))
    return ReadingInput(
        reading=reading, coach_observations=view["coach"]["observations"],
        coach_version=view["coach"].get("version"),
        reduction_candidates=reduction.get("candidates", []) if reduction.get("state") == "ok" else [],
        fluency=view.get("fluency"), engine_agreement=agreement, fragment_words=fragments), None


def _article(store, session: dict[str, Any]) -> tuple[dict[str, Any] | None, frozenset[str]]:
    try:
        article = store.load_article(session["article_id"])
    except KeyError:
        return None, frozenset()
    return article, fragment_words(article["text"])


def load_session_inputs(store, sid: str, attempt_ids: list[str]) -> tuple[list[ReadingInput], Counter]:
    """THIS READING only: exactly the given attempts of one session (the summary's inputs — the latest eligible
    attempt per sentence). Never reads another session; superseded and re-recorded attempts are not passed in."""
    session = store.load_session(sid)
    article, fragments = _article(store, session)
    inputs, exclusions = [], Counter()
    for aid in attempt_ids:
        if aid not in session["attempt_ids"]:
            exclusions["not_in_session"] += 1
            continue
        inp, reason = _attempt_input(store, session, aid, article, fragments)
        if reason is not None:
            exclusions[reason] += 1
            continue
        inputs.append(inp)
    return inputs, exclusions
