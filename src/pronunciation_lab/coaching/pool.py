"""M9 evidence pool — which recent eligible readings count now.

The N most recent eligible readings for ONE engine (the engine of the most recent eligible reading; readings
of another engine are counted as excluded, because phone inventories and artifacts differ). Count-based,
not time-based: reading is bursty, and a reading count keeps the statistical basis stable while old evidence
drops out as new readings arrive. An optional maximum age exists but is off by default (calibration).

Re-reads are separate readings (real recurrence); breadth is counted elsewhere in distinct sentences and
words, so re-reading one sentence many times can never establish a target on its own.

The pool describes the present only. It never compares windows over time — that is M10.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from pronunciation_lab.coaching.calibration import DEFAULT, Calibration
from pronunciation_lab.coaching.evidence import ReadingInput


@dataclass
class Pool:
    inputs: list[ReadingInput]
    engine: str | None
    exclusions: Counter = field(default_factory=Counter)   # reading-level exclusion reason → count
    fingerprint: str = ""
    sufficient: bool = False
    insufficient_code: str | None = None

    @property
    def sessions(self) -> list[str]:
        return sorted({i.reading.session_id for i in self.inputs})

    def summary(self, cal: Calibration) -> dict[str, Any]:
        times = sorted(i.reading.recorded_at for i in self.inputs)
        return {"engine": self.engine, "fingerprint": self.fingerprint,
                "window": {"n_max": cal.pool_n_max, "n_used": len(self.inputs), "max_age_days": cal.pool_max_age_days},
                "readings": len(self.inputs), "sessions": len(self.sessions),
                "sentences": len({i.reading.sentence_key for i in self.inputs}),
                "recorded_from": times[0] if times else None, "recorded_to": times[-1] if times else None,
                "reading_exclusions": dict(sorted(self.exclusions.items()))}


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def fingerprint(inputs: list[ReadingInput], cal: Calibration, versions: dict[str, Any]) -> str:
    key = {"readings": [[i.reading.reading_id, i.reading.job_id, i.coach_version] for i in inputs],
           "calibration": cal.as_dict(), "versions": versions}
    return hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]


def select_pool(inputs: list[ReadingInput], cal: Calibration = DEFAULT, *, now: str | None = None,
                prior_exclusions: Counter | None = None, versions: dict[str, Any] | None = None) -> Pool:
    """`inputs` are eligible readings (the adapter has already set aside ineligible ones and counted them)."""
    exclusions = Counter(prior_exclusions or {})
    ordered = sorted(inputs, key=lambda i: (i.reading.recorded_at, i.reading.reading_id), reverse=True)
    engine = ordered[0].reading.engine if ordered else None
    same = []
    for i in ordered:
        if i.reading.engine != engine:
            exclusions["other_engine"] += 1
        else:
            same.append(i)
    if cal.pool_max_age_days is not None and now is not None:
        limit = _parse(now) - timedelta(days=cal.pool_max_age_days)
        kept = [i for i in same if (_parse(i.reading.recorded_at) or limit) >= limit]
        exclusions["outside_window"] += len(same) - len(kept)
        same = kept
    window = same[:cal.pool_n_max]
    exclusions["outside_window"] += len(same) - len(window)
    if not exclusions["outside_window"]:
        del exclusions["outside_window"]
    window.reverse()  # oldest first: a stable, readable order
    pool = Pool(inputs=window, engine=engine, exclusions=exclusions)
    pool.fingerprint = fingerprint(window, cal, versions or {})
    if len(window) < cal.pool_min_readings:
        pool.insufficient_code = "history_too_small"
    elif len(pool.sessions) < cal.pool_min_sessions:
        pool.insufficient_code = "single_session"
    pool.sufficient = pool.insufficient_code is None
    return pool
