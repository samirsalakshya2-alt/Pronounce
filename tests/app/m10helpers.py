"""SYNTHETIC longitudinal histories for M10 tests (temporary stores only; never real personal data).

    h = History(tmp_path)                                  # a structurally real reader store, labelled synthetic
    sid = h.read("T1", [obs, ...], [obs, ...])             # a new text read in one session: one list per sentence
    h.read_again(sid_or_text, ...)                         # the same text (and sentences) read again
    h.practice(action, sentences, readings)                # a practice session + its explicit practice record

Observations come from m9helpers (sub / amb / ok / omit / uninterpreted). Words are lexical keys: give each text
its own words (W("t3", 4)) unless a test is about repeated or practised words. Time advances one minute per reading.
"""

from __future__ import annotations

import m9helpers as H

from pronunciation_lab.longitudinal import store as LS
from pronunciation_lab.longitudinal.practice import new_record
from pronunciation_lab.longitudinal.source import PRACTICE_SOURCE
from pronunciation_lab.reader import model as M
from pronunciation_lab.reader.store import ReaderStore

SYNTHETIC = "synthetic M10 test history (not personal data)"


_LETTERS = str.maketrans("0123456789", "abcdefghij")


def W(prefix: str, n: int) -> list[str]:
    """n distinct alphabetic words (digits become letters, so they are real words, not fragments):
    W('t3', 2) → ['tdwa', 'tdwb']."""
    return [f"{prefix}w{i}".translate(_LETTERS) for i in range(n)]


def clear(e, h, words, conf="high", **kw):
    return [H.sub(w, e, h, conf=conf, **kw) for w in words]


def ok(e, words, **kw):
    return [x for w in words for x in H.ok(w, e, 1, **kw)]


def stressed(o, stress="primary"):
    return dict(o, context=dict(o["context"], stress=stress, stress_known=True))


class History:
    def __init__(self, root, engine: str = "wav2vec2_raw"):
        self.store = ReaderStore(root)
        self.engine = engine
        self.n = 0
        self.texts: dict[str, dict] = {}

    def _time(self) -> str:
        self.n += 1
        return f"2026-11-{1 + self.n // 1440:02d}T{(self.n // 60) % 24:02d}:{self.n % 60:02d}:00+00:00"

    def article(self, name: str, n_sentences: int, source: str | None = None, sentences: list[str] | None = None) -> dict:
        if name in self.texts:
            return self.texts[name]
        sents = sentences or [f"{name} {name}s{k} {name}x{k} {name}y{k}." for k in range(n_sentences)]
        aid = M.new_id()
        art = {"id": aid, "title": f"{name} ({SYNTHETIC})", "source": source, "text": "\n\n".join(sents),
               "text_sha256": "sha-" + name, "segmenter_version": "s", "created_at": M.now(),
               "segments": [{"id": f"{aid}:{k:04d}", "text": s, "readable": True} for k, s in enumerate(sents)]}
        self.store.save_article(art)
        self.texts[name] = art
        return art

    def session(self, art: dict, engine: str | None = None) -> str:
        sid = M.new_id()
        self.store.create_session({"id": sid, "article_id": art["id"], "engine_default": engine or self.engine,
                                   "state": "SUMMARIZED", "segmenter_version": "s", "model_version": "m",
                                   "created_at": M.now(), "updated_at": M.now(), "rev": 0, "attempt_ids": [],
                                   "current_run_id": None})
        return sid

    def attempt(self, sid: str, art: dict, k: int, observations, *, identity="MATCH", withheld=False,
                withheld_reason=None, disposition=None, state="ANALYZED", job_engine=None, analysis="ok",
                fluency=None) -> str:
        aid, jid = M.new_id(), M.new_id()
        session = self.store.load_session(sid)
        seg = art["segments"][k]
        engine = job_engine or session["engine_default"]
        b = {"state": "BOUNDARY_UNCERTAIN" if withheld else "TARGET_ONLY", "feedback_withheld": withheld,
             "withheld_reason": withheld_reason if withheld else None, "analysis": {"state": analysis, "shown": not withheld}}
        attempt = {"id": aid, "session_id": sid, "segment_id": seg["id"], "attempt_number": 1, "target_text": seg["text"],
                   "capture": {}, "audio": {"duration_ms": 3000.0}, "state": state, "error": None, "job_ids": [jid],
                   "user_disposition": disposition, "created_at": self._time(), "updated_at": M.now()}
        job = {"id": jid, "session_id": sid, "attempt_id": aid, "kind": "primary", "engine_id": engine,
               "state": "SUCCEEDED", "target_confirmation": {"state": identity}, "boundary": b}
        obs = H.numbered(*observations)
        view = {"state": "boundary_withheld" if withheld else "ok", "duration_ms": 3000.0,
                "coach": {"version": "m4.1", "observations": [] if withheld else obs},
                "reduction": {"state": "ok", "candidates": []}, "fluency": fluency}
        ref = M.playback_reference(attempt, job, [0.0, 2400.0], [0.0, 2400.0], "target")
        view["boundary"] = {"regions": [{"kind": "target", "start_ms": 0.0, "end_ms": 2400.0, "play": ref}]}
        self.store.save_attempt(attempt)
        self.store.save_job(job)
        self.store.save_view(job, view)
        session["attempt_ids"].append(aid)
        self.store.save_session(session)
        return aid

    def read(self, name: str, *sentences, engine: str | None = None, fluency=None, source=None, **kw) -> str:
        """A text (new, or the same text again) read in a new session; one observation list per sentence."""
        art = self.article(name, len(sentences), source=source)
        sid = self.session(art, engine)
        for k, obs in enumerate(sentences):
            self.attempt(sid, art, k, obs, fluency=fluency, **kw)
        return sid

    def read_at(self, name: str, n_sentences: int, sentences: dict[int, list], **kw) -> str:
        """Some sentences (by index) of a text with `n_sentences` sentences, in a new session: one text read across
        several sessions without re-reading a sentence (every reading is fresh, but the text is the same)."""
        art = self.article(name, n_sentences)
        sid = self.session(art, kw.pop("engine", None))
        for k, obs in sorted(sentences.items()):
            self.attempt(sid, art, k, obs, **kw)
        return sid

    def practice(self, action: dict, sentences: list[str], readings: list[list], *, read: bool = True,
                 name: str | None = None) -> dict:
        """A practice session started from an M9 action ("Read these now") with its explicit record."""
        name = name or f"practice{self.n}"
        art = self.article(name, len(sentences), source=PRACTICE_SOURCE, sentences=sentences)
        sid = self.session(art)
        record = new_record(M.new_id(), self._time(), sid, art, action,
                            {"generated_at": "2026-11-01T00:00:00+00:00", "fingerprint": "f"})
        LS.ProgressStore(self.store.root).save_practice(record)
        if read:
            for k, obs in enumerate(readings):
                self.attempt(sid, art, k, obs)
        return record

    def progress(self, **kw):
        kw.setdefault("generated_at", "t")
        return LS.update(self.store, **kw)


def action(pairs, kind="CONTRAST", target_id=None, condition=None, word=None, group=None, trainability="listen_compare_fallback"):
    return {"action_text": "Practise " + ", ".join(pairs), "rank_in_plan": 1,
            "target": {"target_id": target_id or ("contrast:" + "~".join(sorted({x for p in pairs for x in p.split("→")}))),
                       "kind": kind, "pairs": pairs, "condition": condition, "word": word, "group": group},
            "practice": {"trainability": trainability, "retest_sentences": []}}


def pattern(result, pid):
    return next((it for it in result["patterns"] if it["pattern"] == pid), None)


# --- standard histories ---------------------------------------------------------------------------------------

def personal_baseline(h: History, e="ɛ", hd="ɪ", texts=3, clear_per=2, ok_per=8, prefix="b"):
    """`texts` different texts, each with `clear_per` clear e→hd in distinct words and `ok_per` heard as expected."""
    for t in range(texts):
        p = f"{prefix}{t}"
        h.read(f"text-{p}", clear(e, hd, W(p, clear_per)) + ok(e, W(p + "ok", ok_per)))


def quiet_texts(h: History, e="ɛ", texts=4, ok_per=12, prefix="q", hd=None, clear_per=0):
    for t in range(texts):
        p = f"{prefix}{t}"
        obs = ok(e, W(p, ok_per)) + (clear(e, hd, W(p + "c", clear_per)) if clear_per else [])
        h.read(f"text-{p}", obs)
