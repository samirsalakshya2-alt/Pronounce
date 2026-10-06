"""M10 persistence and collection (the only M10 code that writes, and only under <reader root>/longitudinal/).

    longitudinal/practice/{practice_id}.json   write-once practice records (never overwritten)
    longitudinal/ledger.jsonl                  append-only: every attempt extracted, with its source fingerprint
    longitudinal/transitions.jsonl             append-only: every state transition ever published, with versions
    longitudinal/cache/{session_id}.json       rebuildable: per-attempt extracted evidence, keyed by fingerprint
                                               (one file per session, so an update rewrites only what changed)

Stored M4–M9 results are never written. Incremental update re-extracts only attempts whose fingerprint changed (or
that are new) and recomputes the longitudinal state from the cached evidence; a full rebuild re-extracts every
attempt from the stored results. Both feed the same pure `build_progress`, so they give the same result.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pronunciation_lab.coaching.evidence import text_key
from pronunciation_lab.longitudinal import source as SRC
from pronunciation_lab.longitudinal.calibration import DEFAULT_LONGITUDINAL, LongitudinalCalibration
from pronunciation_lab.longitudinal.identity import link_target
from pronunciation_lab.longitudinal.progress import PROGRESS_VERSION, build_progress
from pronunciation_lab.reader.model import now
from pronunciation_lab.reader.store import atomic_write_bytes, write_once_bytes


def _dump(obj: Any) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


class ProgressStore:
    def __init__(self, reader_root: Path) -> None:
        self.root = Path(reader_root) / "longitudinal"

    # practice records: write-once
    def save_practice(self, record: dict[str, Any]) -> None:
        write_once_bytes(self.root / "practice" / f"{record['id']}.json", _dump(record))

    def practice_records(self) -> list[dict[str, Any]]:
        d = self.root / "practice"
        if not d.is_dir():
            return []
        return sorted((json.loads(p.read_text(encoding="utf-8")) for p in d.glob("*.json")),
                      key=lambda r: (r["created_at"], r["id"]))

    # cache: rebuildable, one file per session
    def load_cache(self) -> dict[str, Any]:
        d = self.root / "cache"
        readings: dict[str, Any] = {}
        if d.is_dir():
            for p in sorted(d.glob("*.json")):
                try:
                    c = json.loads(p.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue            # a damaged cache file is simply re-extracted
                if c.get("extractor_version") == SRC.EXTRACTOR_VERSION:
                    readings.update(c.get("readings") or {})
        return {"extractor_version": SRC.EXTRACTOR_VERSION, "readings": readings} if readings else {}

    def save_cache(self, cache: dict[str, Any], sessions: set[str] | None = None) -> None:
        """Write the cache files of `sessions` (all when None); remove files of sessions no longer present."""
        d = self.root / "cache"
        by_session: dict[str, dict[str, Any]] = {}
        for rid, entry in cache["readings"].items():
            by_session.setdefault(rid.split(":")[0], {})[rid] = entry
        for sid, readings in by_session.items():
            if sessions is None or sid in sessions:
                atomic_write_bytes(d / f"{sid}.json", _dump({"extractor_version": cache["extractor_version"],
                                                              "readings": readings}))
        if d.is_dir():
            for p in d.glob("*.json"):
                if p.stem not in by_session:
                    p.unlink()

    # append-only logs
    def _append(self, name: str, lines: list[dict[str, Any]]) -> None:
        if not lines:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / name, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()

    def _read(self, name: str) -> list[dict[str, Any]]:
        p = self.root / name
        if not p.is_file():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue   # a torn last line is skipped, never fatal
        return out

    def ledger(self) -> list[dict[str, Any]]:
        return self._read("ledger.jsonl")

    def transitions_log(self) -> list[dict[str, Any]]:
        return self._read("transitions.jsonl")

    def append_ledger(self, lines):
        self._append("ledger.jsonl", lines)

    def append_transitions(self, lines):
        self._append("transitions.jsonl", lines)


def advice_history(store) -> list[dict[str, Any]]:
    """What M9 displayed, and when (its issued coaching.json snapshots): advice, never practice."""
    out = []
    for sid in store.session_ids():
        c = store.load_coaching(sid)
        if not c or not c.get("actions"):
            continue
        for a in c["actions"]:
            link = link_target(a.get("target") or {})
            out.append({"issued_at": c.get("generated_at"), "session_id": sid, "engine": (c.get("pool") or {}).get("engine"),
                        "target_id": link["m9_target_id"], "patterns": link["patterns"], "plan_position": a.get("rank_in_plan"),
                        "retest_sentence_keys": [text_key(s.get("text") or "")
                                                 for s in (a.get("practice") or {}).get("retest_sentences", [])]})
    return out


def collect(store, pstore: ProgressStore, rebuild: bool = False, cal: LongitudinalCalibration = DEFAULT_LONGITUDINAL
            ) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Every stored attempt as an extracted record. Returns (records, new cache, re-extracted reading ids)."""
    practice_sessions = {r["practice_session_id"] for r in pstore.practice_records()}
    cached = {} if rebuild else (pstore.load_cache().get("readings") or {})
    previous = set(cached)
    readings: dict[str, Any] = {}
    records, extracted = [], []
    texts: dict[str, str] = {}
    for session, art, frags, aid in SRC.iter_attempts(store):
        if art:
            texts[art.get("text_sha256") or art["id"]] = art.get("text") or ""
        sid = session["id"]
        rid = f"{sid}:{aid}"
        fp = SRC.fingerprint(store, sid, aid, session, art)
        fp = None if fp is None else hashlib.sha1(f"{fp}|{sid in practice_sessions}".encode()).hexdigest()
        hit = cached.get(rid)
        if fp is not None and hit and hit.get("fingerprint") == fp:
            rec = hit["record"]
        else:
            rec = SRC.extract_attempt(store, session, aid, art, frags, practice_sessions)
            if rec is None:
                continue
            extracted.append(rid)
        readings[rid] = {"fingerprint": fp, "record": rec}
        records.append(rec)
    groups = SRC.text_groups(texts, cal.same_text_min_containment, cal.same_text_min_words)
    records = [dict(r, reading=dict(r["reading"], text_id=groups.get(r["reading"]["article_key"], r["reading"]["article_key"])))
               for r in records]
    return records, {"extractor_version": SRC.EXTRACTOR_VERSION, "readings": readings, "_previous": previous}, extracted


def input_fingerprint(cache: dict[str, Any], practice: list[dict[str, Any]], advice: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for rid in sorted(cache["readings"]):
        h.update(f"{rid}={cache['readings'][rid]['fingerprint']};".encode())
    for r in practice:
        h.update(f"practice={r['id']};".encode())
    for a in advice:
        h.update(f"advice={a['issued_at']}:{a['session_id']}:{a['target_id']};".encode())
    return h.hexdigest()[:24]


def update(store, *, engine: str | None = None, rebuild: bool = False,
           cal: LongitudinalCalibration = DEFAULT_LONGITUDINAL, generated_at: str | None = None,
           persist: bool = True) -> dict[str, Any]:
    """Collect (incrementally, or rebuilding), build the longitudinal result, and record what was ingested and
    published. `persist=False` reads only (no cache, ledger or log writes)."""
    pstore = store.progress_store()
    records, cache, extracted = collect(store, pstore, rebuild=rebuild, cal=cal)
    practice = pstore.practice_records()
    advice = advice_history(store)
    fp = input_fingerprint(cache, practice, advice)
    result = build_progress(records, practice, advice, engine=engine, cal=cal, generated_at=generated_at or now(),
                            input_fingerprint=fp)
    result["update"] = {"mode": "rebuild" if rebuild else "incremental", "extracted": len(extracted),
                        "reused": len(records) - len(extracted)}
    previous = cache.pop("_previous")
    if persist:
        if rebuild:
            pstore.save_cache(cache)
        elif extracted or previous != set(cache["readings"]):
            changed = {rid.split(":")[0] for rid in extracted} | {rid.split(":")[0] for rid in previous ^ set(cache["readings"])}
            pstore.save_cache(cache, changed)
        if extracted:
            pstore.append_ledger([{"at": now(), "reading_id": rid, "fingerprint": cache["readings"][rid]["fingerprint"],
                                   "extractor_version": SRC.EXTRACTOR_VERSION,
                                   "mode": "rebuild" if rebuild else "incremental"} for rid in extracted])
        published = {(t.get("versions"), t.get("engine"), t.get("pattern"), t.get("at_session"), t.get("to"))
                     for t in pstore.transitions_log()}
        versions = f"{PROGRESS_VERSION}/{result['states_version']}/{cal.version}"
        new = []
        for it in result["patterns"]:
            for t in it["transitions"]:
                key = (versions, result["engine"], it["pattern"], t["at_session"], t["to"])
                if key not in published:
                    new.append({"versions": versions, "engine": result["engine"], "pattern": it["pattern"],
                                "at_session": t["at_session"], "time": t["time"], "from": t["from"], "to": t["to"],
                                "reason": t["reason"], "published_at": now()})
        pstore.append_transitions(new)
    return result
