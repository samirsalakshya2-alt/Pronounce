"""Synthetic evidence for the M9 core tests: readings and M4/M5/M8-shaped observations, as stored views hold them.

    rd(n, session=1, ...)                    a Reading (n orders recording time)
    sub(word, expected, heard, ...)          M4 substitution_candidate (confidence high by default)
    amb(word, expected, heard)               M4 ambiguous
    omit(word, expected)                     M4 omission_candidate
    ok(word, expected, n=1)                  M4 "expected" observations (counter-evidence)
    inp(reading, *obs, ...)                  a ReadingInput (ids assigned in order)
"""

from __future__ import annotations

from pronunciation_lab.coaching.evidence import Reading, ReadingInput, text_key

ENGINE = "wav2vec2_raw"


def sid(k: int) -> str:
    return f"{k:032x}"


def rd(n: int, session: int = 1, sentence: str | None = None, analysis: str = "ok", reliable: bool | None = True,
       engine: str = ENGINE, comparison: bool = False, article: str = "a") -> Reading:
    s, a, j = sid(session), sid(1000 + n), sid(5000 + n)
    text = sentence or f"Sentence number {n}."
    return Reading(reading_id=f"{s}:{a}", session_id=s, attempt_id=a, job_id=j, segment_id=f"{sid(9)}:{n:04d}",
                   engine=engine, recorded_at=f"2026-10-0{1 + (session % 9)}T10:{n // 60:02d}:{n % 60:02d}+00:00",
                   sentence_key=text_key(text), sentence_text=text, article_key=article, analysis_state=analysis,
                   fluency_reliable=reliable, has_comparison=comparison,
                   sentence_ref={"session_id": s, "segment_id": f"{sid(9)}:{n:04d}", "attempt_id": a, "job_id": j,
                                 "timeline": "analysis_wav", "kind": "target", "play_ms": [0.0, 3000.0],
                                 "span_ms": [0.0, 3000.0], "url": f"/api/sessions/{s}/attempts/{a}/audio"})


def _obs(word, expected, typ, conf, observed, competitor, position, cluster, prev, nxt, wi, play, sentence_position):
    span = [100.0 + 100 * wi, 160.0 + 100 * wi]
    return {"kind": "sound", "word": word, "word_index": wi, "expected": expected, "observed": observed, "type": typ,
            "confidence": conf, "competitor": competitor, "expected_posterior": 0.01, "competitor_posterior": 0.9,
            "play_ms": [span[0] - 60, span[1] + 60] if play else None, "span_ms": span if play else None,
            "word_play_ms": [span[0] - 100, span[1] + 100] if play else None, "timing_source": "engine",
            "context": {"word_position": position, "previous_phone": prev, "next_phone": nxt,
                        "in_consonant_cluster": cluster, "sentence_position": sentence_position,
                        "stress": None, "stress_known": False}}


def sub(word, expected, heard, conf="high", position="medial", cluster=False, prev=None, nxt=None, wi=0, play=True,
        sentence_position="inside"):
    return _obs(word, expected, "substitution_candidate", conf, heard, heard, position, cluster, prev, nxt, wi, play,
                sentence_position)


def amb(word, expected, heard, position="medial", wi=0, **kw):
    return _obs(word, expected, "ambiguous", "low", expected, heard, position, kw.get("cluster", False), None, None,
                wi, True, "inside")


def omit(word, expected, position="final", wi=0, cluster=False):
    return _obs(word, expected, "omission_candidate", "low", None, None, position, cluster, None, None, wi, True,
                "inside")


def ok(word, expected, n=1, position="medial", cluster=False, wi=0):
    return [_obs(word, expected, "expected", "high", expected, None, position, cluster, None, None, wi, True, "inside")
            for _ in range(n)]


def uninterpreted(word, expected):
    o = _obs(word, expected, "not_interpreted", "none", None, None, "medial", False, None, None, 0, True, "inside")
    return o


def flatten(items):
    out = []
    for x in items:
        out.extend(x if isinstance(x, list) else [x])
    return out


def inp(reading: Reading, *obs, reductions=(), fluency=None, agreement=None, fragments=(), coach_version="m4.1"):
    observations = []
    for k, o in enumerate(flatten(obs)):
        observations.append(dict(o, id=f"o{k:03d}"))
    return ReadingInput(reading=reading, coach_observations=observations, coach_version=coach_version,
                        reduction_candidates=list(reductions), fluency=fluency, engine_agreement=dict(agreement or {}),
                        fragment_words=frozenset(fragments))


def m5(observation_id, category="possible_connected_speech_reduction", natural=True, merge=False):
    return {"id": "r-" + observation_id, "observation_id": observation_id, "evidence_strength": "low",
            "interpretation": {"category": category, "natural_connected_speech_possible": natural,
                               "merge_suspect": merge, "contexts_present": ["flapping"] if natural else []}}


def fluency(*obs, reliable=True):
    out = []
    for k, (typ, cls, before, after, pos) in enumerate(obs):
        out.append({"id": f"f{k + 1}", "type": typ, "classification": cls, "label": typ.title(), "strength": "low",
                    "notice": True, "start_ms": 1000.0 + 500 * k, "end_ms": 1800.0 + 500 * k,
                    "context": {"word_before": before, "word_after": after, "position": pos},
                    "playback": {"timeline": "analysis_wav", "span_ms": [1000.0 + 500 * k, 1800.0 + 500 * k],
                                 "play_ms": [750.0 + 500 * k, 2050.0 + 500 * k], "context_ms": [250.0, 250.0]}})
    return {"state": "ok", "observations": out, "metrics": {"activity_reliable": reliable}}


def hesitation(before="the", after="plan"):
    return ("PAUSE", "possible_hesitation", before, after, "between words inside a phrase")


def scatter(obs_list, n_readings=12, sessions=3, base=(), start=0, **rd_kw):
    """Spread observations round-robin over `n_readings` readings (distinct sentences), `sessions` sessions;
    `base` observations are added to every reading. Word indexes are assigned per reading."""
    buckets = [[] for _ in range(n_readings)]
    for k, o in enumerate(flatten(obs_list)):
        buckets[k % n_readings].append(o)
    out = []
    for k, b in enumerate(buckets):
        items = [dict(o, word_index=j) for j, o in enumerate(b + flatten(list(base)))]
        out.append(inp(rd(start + k, session=1 + (start + k) % sessions, **rd_kw), *items))
    return out


def words(*ws):
    """Cycle through words: words('a', 'b')(5) → ['a', 'b', 'a', 'b', 'a']."""
    return lambda n: [ws[k % len(ws)] for k in range(n)]


def subs(expected, heard, n, ws, **kw):
    return [sub(w, expected, heard, **kw) for w in ws(n)]


# --- a synthetic reader store (structurally real: articles, sessions, attempts, jobs, views) -------------------

from pronunciation_lab.reader import model as M  # noqa: E402
from pronunciation_lab.reader.store import ReaderStore  # noqa: E402


def numbered(*items):
    """Observations with ids and word indexes, as a stored view holds them."""
    return [dict(o, id=f"o{k:03d}", word_index=k) for k, o in enumerate(flatten(list(items)))]


class StoreBuilder:
    def __init__(self, root, text="Think about it. Then change the plan. It is best to test it."):
        self.store = ReaderStore(root)
        self.article = {"id": M.new_id(), "title": "T", "source": None, "text": text,
                        "text_sha256": "sha-" + text_key(text), "segmenter_version": "s", "created_at": M.now(),
                        "segments": []}
        self.store.save_article(self.article)
        self.n = 0

    def session(self, engine=ENGINE):
        sid = M.new_id()
        self.store.create_session({"id": sid, "article_id": self.article["id"], "engine_default": engine, "state": "SUMMARIZED",
                                   "segmenter_version": "s", "model_version": "m", "created_at": M.now(),
                                   "updated_at": M.now(), "rev": 0, "attempt_ids": [], "current_run_id": None})
        return sid

    def attempt(self, sid, observations, *, sentence="Then change the plan.", identity="MATCH", withheld=False,
                disposition=None, job_engine=None, comparison=None, boundary=True, analysis="ok", fluency=None,
                state="ANALYZED", audio=None):
        self.n += 1
        aid, jid = M.new_id(), M.new_id()
        session = self.store.load_session(sid)
        engine = job_engine or session["engine_default"]
        b = {"state": "BOUNDARY_UNCERTAIN" if withheld else "TARGET_ONLY", "feedback_withheld": withheld,
             "withheld_reason": "boundary" if withheld else None, "analysis": {"state": analysis, "shown": not withheld}}
        attempt = {"id": aid, "session_id": sid, "segment_id": f"{self.article['id']}:0001", "attempt_number": 1,
                   "target_text": sentence, "capture": {}, "audio": {"duration_ms": 3000.0}, "state": state,
                   "error": None, "job_ids": [jid], "user_disposition": disposition,
                   "created_at": f"2026-10-05T10:00:{self.n:02d}+00:00", "updated_at": M.now()}
        job = {"id": jid, "session_id": sid, "attempt_id": aid, "kind": "primary", "engine_id": engine,
               "state": "SUCCEEDED", "target_confirmation": {"state": identity}, "boundary": b}
        view = {"state": "boundary_withheld" if withheld else "ok", "duration_ms": 3000.0,
                "coach": {"version": "m4.1", "observations": [] if withheld else observations},
                "reduction": {"state": "ok", "candidates": []}, "fluency": fluency}
        if boundary:
            ref = M.playback_reference(attempt, job, [0.0, 2400.0], [0.0, 2400.0], "target")
            view["boundary"] = {"regions": [{"kind": "target", "start_ms": 0.0, "end_ms": 2400.0, "play": ref},
                                            {"kind": "overflow", "start_ms": 2400.0, "end_ms": 3000.0}]}
        self.store.save_attempt(attempt)
        self.store.save_job(job)
        self.store.save_view(job, view)
        if audio is not None:  # a real WAV for playback (browser tests)
            self.store.write_audio(sid, aid, "analysis.wav", audio)
        if comparison is not None:
            cj = {"id": M.new_id(), "session_id": sid, "attempt_id": aid, "kind": "comparison",
                  "engine_id": "openpronounce", "state": "SUCCEEDED"}
            attempt["job_ids"].append(cj["id"])
            self.store.save_attempt(attempt)
            self.store.save_job(cj)
            self.store.save_view(cj, {"state": "ok", "coach": {"observations": comparison}})
        session["attempt_ids"].append(aid)
        self.store.save_session(session)
        return aid
