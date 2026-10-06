"""M10 extraction: the reader store → compact, source-referenced longitudinal evidence (read-only).

One record per attempt. An eligible attempt yields its sound observations (M4) and fluency observations (M8) with
the unit quality and exclusion decided by M9's own normalisation (coaching/evidence.py), so clear / ambiguous /
excluded mean exactly what they mean in M9. An ineligible attempt is never discarded silently: it is kept with
the reader's own reason (reader/status.py) — discarded, re-recorded, not analysed (too short / failed),
different sentence (MISMATCH), boundary or containment withheld (M7), unconfirmed identity, other engine.

Nothing here reruns inference or writes anything. Raw engine output is referenced (session / attempt / job /
observation ids, playback windows), never copied.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from pronunciation_lab.coaching.evidence import fragment_words, normalise, text_key
from pronunciation_lab.reader.coaching_source import _attempt_input
from pronunciation_lab.reader.status import attempt_status

EXTRACTOR_VERSION = "m10-src.1"
PRACTICE_SOURCE = "What to practise now"   # articles created by "Read these now" (M9 retest sentences)

# reader/status.py summary codes → M10 eligibility reasons (the reader's decision, never re-decided here)
INELIGIBLE = {
    "discarded": "discarded", "rerecord": "rerecorded", "not_analysed": "not_analysed",
    "other_engine": "other_engine", "different_sentence": "mismatch", "boundary": "boundary_withheld",
    "containment": "containment_withheld", "unconfirmed": "unconfirmed_identity",
}


def _stress(ctx: dict[str, Any]) -> str | None:
    if not ctx.get("stress_known"):
        return None
    return ctx.get("stress") or "unstressed"   # dictionary stress (eSpeak), never observed prosody


def fingerprint(store, sid: str, aid: str, session: dict[str, Any], article: dict[str, Any] | None) -> str | None:
    """Cheap change detector for one attempt: its attempt.json and primary job.json bytes, the view's size and
    mtime, the session engine and the article source. Any change re-extracts the attempt."""
    adir = store.attempt_dir(sid, aid)
    try:
        h = hashlib.sha1((adir / "attempt.json").read_bytes())
    except FileNotFoundError:
        return None
    for jdir in sorted((adir / "jobs").glob("*")) if (adir / "jobs").is_dir() else []:
        for name in ("job.json",):
            p = jdir / name
            if p.is_file():
                h.update(p.read_bytes())
        v = jdir / "view.json"
        if v.is_file():
            st = v.stat()
            h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
    h.update(f"{session.get('engine_default')}|{(article or {}).get('source')}|{EXTRACTOR_VERSION}".encode())
    return h.hexdigest()


def extract_attempt(store, session: dict[str, Any], aid: str, article: dict[str, Any] | None,
                    fragments: frozenset[str], practice_sessions: set[str]) -> dict[str, Any] | None:
    """One attempt → {"reading": …, "eligible": bool, "reason": …, "sounds": [...], "fluency": [...]}."""
    sid = session["id"]
    try:
        attempt = store.load_attempt(sid, aid)
    except KeyError:
        return None
    jobs = []
    for j in attempt.get("job_ids", []):
        try:
            jobs.append(store.load_job(sid, aid, j))
        except KeyError:
            continue
    primary = next((j for j in reversed(jobs) if j["kind"] == "primary"), None)
    status = attempt_status(attempt, primary, session["engine_default"])
    practice = sid in practice_sessions or (article or {}).get("source") == PRACTICE_SOURCE
    base = {"reading_id": f"{sid}:{aid}", "session_id": sid, "attempt_id": aid,
            "job_id": (primary or {}).get("id"), "segment_id": attempt.get("segment_id"),
            "engine": (primary or {}).get("engine_id") or session.get("engine_default"),
            "recorded_at": attempt.get("created_at"), "sentence_key": text_key(attempt.get("target_text") or ""),
            "sentence_text": attempt.get("target_text"), "article_id": session.get("article_id"),
            "article_key": (article or {}).get("text_sha256") or session.get("article_id"),
            "practice_session": practice}
    code = status.get("summary_code")
    if code is not None:
        return {"reading": base, "eligible": False, "reason": INELIGIBLE.get(code, code),
                "detail": status.get("identity"), "sounds": [], "fluency": []}
    inp, reason = _attempt_input(store, session, aid, article, fragments)
    if inp is None:
        return {"reading": base, "eligible": False, "reason": "no_view" if reason == "no_view" else reason,
                "detail": status.get("identity"), "sounds": [], "fluency": []}
    units, flu = normalise(inp)
    sound_index = {o["id"]: o.get("sound_index") for o in inp.coach_observations}
    r = inp.reading
    base |= {"job_id": r.job_id, "engine": r.engine, "analysis_state": r.analysis_state,
             "fluency_reliable": r.fluency_reliable, "coach_version": inp.coach_version,
             "sentence_ref": r.sentence_ref}
    sounds = []
    for u in units:
        if u.expected is None and u.outcome != "extra":
            continue
        ctx = u.context
        sounds.append({
            "o": u.unit_id, "obs": u.observation_id, "e": u.expected, "h": u.heard, "out": u.outcome,
            "q": u.quality, "x": u.exclusion, "c": u.m4_confidence, "w": u.lexical_key, "wd": u.word,
            "wi": u.word_index, "si": sound_index.get(u.observation_id), "frag": u.fragment_suspect,
            "pos": ctx.get("word_position"), "st": _stress(ctx), "cl": ctx.get("in_consonant_cluster"),
            "sp": ctx.get("sentence_position"), "ag": u.engine_agreement,
            "play": (u.audio or {}).get("play_ms"), "span": (u.audio or {}).get("span_ms")})
    fluency = [{"o": f.unit_id, "g": f.group, "q": f.quality, "play": (f.audio or {}).get("play_ms"),
                "before": f.word_before, "after": f.word_after} for f in flu]
    return {"reading": base, "eligible": True, "reason": None, "detail": status.get("identity"),
            "sounds": sounds, "fluency": fluency}


def content_words(text: str) -> frozenset[str]:
    from pronunciation_lab.coaching.evidence import lexical_key
    from pronunciation_lab.coaching.knowledge import function_word
    return frozenset(k for t in re.split(r"\s+", text or "") if (k := lexical_key(t)) and not function_word(k))


def text_groups(articles: dict[str, str], min_containment: float, min_words: int) -> dict[str, str]:
    """article_key → text id. Texts whose content words are (almost) contained in one another are one text, so
    excerpts of one article pasted separately never count as different texts. Deterministic (union-find, the
    smallest key names the group)."""
    keys = sorted(articles)
    words = {k: content_words(articles[k]) for k in keys}
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            wa, wb = words[a], words[b]
            small = min(len(wa), len(wb))
            if small >= min_words and len(wa & wb) / small >= min_containment:
                ra, rb = find(a), find(b)
                parent[max(ra, rb)] = min(ra, rb)
    return {k: find(k) for k in keys}


def iter_attempts(store):
    """(session, article, fragments, attempt id) for every stored attempt, sessions in id order."""
    articles: dict[str, Any] = {}
    for sid in store.session_ids():
        session = store.load_session(sid)
        aid_ = session["article_id"]
        if aid_ not in articles:
            try:
                art = store.load_article(aid_)
            except KeyError:
                art = None
            articles[aid_] = (art, fragment_words(art["text"]) if art else frozenset())
        art, frags = articles[aid_]
        for aid in session["attempt_ids"]:
            yield session, art, frags, aid
