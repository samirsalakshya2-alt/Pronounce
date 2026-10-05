"""M9 calibration — every tunable parameter in one versioned place.

These values are PROVISIONAL. They were chosen conservatively from the evidence available when M9 was
built (one speaker's stored readings and the R01–R20 benchmark) and are not scientifically established.
Every coaching result reports the calibration it used, so a change here is always visible and a result can
always be regenerated with the calibration it was made with.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

CALIBRATION_VERSION = "m9-cal.3"

# Order in which candidate interventions are compared (leverage.py). A criterion decides only when the
# difference is meaningful (MARGINS); otherwise the next one is consulted. Never a weighted sum.
# m9-cal.2: recurrence across sessions added after sentence breadth (real data: a fluency pattern in 3 sessions
# tied a vowel family in 7 on sentence breadth and then won on a pause-vs-confusion count).
ORDERING = ("tier", "breadth_sentences", "recurrence_sessions", "breadth_words", "coverage", "reach", "concentration",
            "trainability")


@dataclass(frozen=True)
class Calibration:
    version: str = CALIBRATION_VERSION
    # --- evidence pool (pool.py) --------------------------------------------------------------------
    pool_n_max: int = 40                 # most recent eligible readings considered
    pool_min_readings: int = 10          # below this: no action ("history too small")
    pool_min_sessions: int = 2           # below this: no action ("single session")
    pool_max_age_days: float | None = None   # optional staleness guard; off by default
    # --- target gates (targets.py) ----------------------------------------------------------------------
    # m9-cal.3: 4 → 5. On the stored readings the top two actions were stable for k_conf 3–6, pool 20–61 and
    # c_min 0.4–0.6, while a third action at k_conf 4 rested on exactly 4 confident observations and changed with
    # pool size (d~ɹ / none / s~ʃ): a minimum-evidence slot. At 5 the result is the same at pool 20, 30 and 40.
    k_conf: int = 5                      # confident units (M4 high/moderate substitutions); supporting never count
    w_min: int = 3                       # distinct words
    s_min: int = 3                       # distinct sentences
    sessions_min: int = 2                # distinct sessions
    c_min: float = 0.5                   # concentration: share of the sound's confident deviations this target explains
    disagree_max: float = 0.34           # share of compared units where the other engine differs → tier demoted
    # --- consolidation -----------------------------------------------------------------------------------
    m_merge: float = 1.5                 # a family must explain at least this × its largest member
    ctx_min_in: int = 8                  # occurrences needed inside a condition …
    ctx_min_out: int = 8                 # … and outside it, to make any context claim
    ctx_ratio: float = 2.0               # inside deviation share ≥ this × outside share
    ctx_max_share: float = 0.6           # a condition covering more of the sound's occurrences is not specific
    lex_share: float = 0.8               # ≤ lex_max_words words hold this share of a target's confident units
    lex_max_words: int = 2
    lex_k_conf: int = 3                  # confident units for a single-word (lexical) target
    lex_min_readings: int = 2            # a lexical target's word heard differently in ≥ this many readings
    lex_rel: int = 2                     # the word occurs in ≥ this many distinct sentences of the pool
    # --- leverage comparison (leverage.py) ---------------------------------------------------------------
    margin_ratio: float = 1.5
    margins_abs: dict[str, float] = field(default_factory=lambda: {
        "breadth_sentences": 2, "recurrence_sessions": 2, "breadth_words": 2, "coverage": 2, "reach": 2.0,
        "concentration": 0.1,
        "trainability": 1})
    ordering: tuple[str, ...] = ORDERING
    # --- selection and plan --------------------------------------------------------------------------------
    max_actions: int = 3
    max_fluency: int = 1
    n_examples: int = 3                  # heard-differently examples shown per action
    n_counter: int = 3                   # heard-as-expected examples to compare with
    n_words: int = 5                     # practice words
    n_retest: int = 2                    # retest sentences
    fallback_min_counter: int = 2        # listen-and-compare fallback needs this many own clear examples
    time_split: tuple[tuple[int, ...], ...] = ((12,), (7, 5), (5, 4, 3))   # suggested minutes

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT = Calibration()


# ----------------------------------------------------------------------------------------------------------
# "This reading" (coaching/reading.py) — a separate, versioned calibration. It does not change Calibration
# above (nor the coaching fingerprint). Thresholds are at the scale of ONE reading (one article session),
# provisional and conservative, like everything here.
# ----------------------------------------------------------------------------------------------------------

READING_CALIBRATION_VERSION = "m9-read-cal.3"


@dataclass(frozen=True)
class ReadingCalibration:
    version: str = READING_CALIBRATION_VERSION
    # recurring within this reading (passed to the shared pattern-consolidation machinery)
    k_conf: int = 3                      # confident observations
    w_min: int = 2                       # distinct words
    s_min: int = 2                       # distinct sentences
    c_min: float = 0.5                   # concentration within this reading
    m_merge: float = 1.5
    ctx_min_in: int = 4
    ctx_min_out: int = 4
    ctx_ratio: float = 2.0
    ctx_max_share: float = 0.6
    lex_share: float = 0.8
    lex_max_words: int = 1               # within one article only a single dominant word is a word pattern
    lex_k_conf: int = 2                  # a word heard differently in ≥ 2 sentences of this article
    lex_rel: int = 2
    # the synthesis: major improvement areas first, then what was already stable
    min_sentences: int = 2               # fewer summarised sentences: coverage, cautions and counts only
    rate_min: float = 0.10               # clear-evidence rate gate (clear ÷ in-scope occurrences), paths A and B
    rate_high: float = 0.40              # rate bands for ordering: high ≥ this …
    rate_moderate: float = 0.20          # … moderate ≥ this, low ≥ rate_min
    mixed_min_observations: int = 3      # path B: clear + ambiguous observations …
    mixed_min_clear: int = 2             # … of which at least this many clear (ambiguous is support only)
    mixed_min_sentences: int = 2
    mixed_min_real_words: int = 2        # word fragments from the article text do not count
    # (no cap on the number of improvement areas: the gates decide how many qualify, the ordering only orders)
    max_strengths: int = 3               # "already stable in this reading"
    strength_min_occurrences: int = 10   # a diagnostically useful sound read this often in this reading …
    strength_min_share: float = 0.9      # … heard as expected in at least this share (ambiguous counts against)
    strength_max_differences: int = 1    # … with fewer than 2 non-excluded differences
    fluency_min: int = 2                 # noticed fluency observations of one kind
    clarity_min_words: int = 3           # M5 two-stream omission/weakening candidates in different words
    n_examples: int = 3
    n_counter: int = 2

    def formation(self) -> Calibration:
        """The configuration handed to targets.form_targets for pattern consolidation within one reading."""
        return Calibration(version=self.version + "/formation", k_conf=self.k_conf, w_min=self.w_min, s_min=self.s_min,
                           sessions_min=1, c_min=self.c_min, m_merge=self.m_merge, ctx_min_in=self.ctx_min_in,
                           ctx_min_out=self.ctx_min_out, ctx_ratio=self.ctx_ratio, ctx_max_share=self.ctx_max_share,
                           lex_share=self.lex_share, lex_max_words=self.lex_max_words, lex_k_conf=self.lex_k_conf,
                           lex_min_readings=self.lex_k_conf,
                           lex_rel=self.lex_rel)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_READING = ReadingCalibration()
