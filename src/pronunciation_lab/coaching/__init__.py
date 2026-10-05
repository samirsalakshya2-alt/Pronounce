"""M9 — Pareto Coaching Engine.

A pure, deterministic reasoning core: from the evidence M4–M8 already produced across recent eligible
readings, it selects 0–3 practice interventions ("What to practise now"). It runs no recognition, never
modifies stored evidence, and never scores the user. See docs/M9_PARETO_COACHING.md.

Levels are kept distinct throughout:

    observation (evidence.EvidenceUnit / FluencyUnit)   measured
    pattern     (patterns.Pattern)                       counted
    target      (targets.Target)                         inferred (knowledge contributions labelled)
    intervention / action (plan)                         coaching

The only I/O lives in `reader/coaching_source.py`; everything here works on plain data.
"""

from pronunciation_lab.coaching.engine import COACHING_VERSION, run_coaching  # noqa: E402

__all__ = ["COACHING_VERSION", "run_coaching"]
