"""M10 calibration: every threshold is a named, documented, versioned parameter (provisional, not truth).

The values encode a conservative reading of the evidence available today (discovery, 2026-10-05): a clear
difference at one position repeats on a re-read of the same sentence only ~28% of the time, sentence-level counts
swing several-fold between re-reads, and article composition changes how many chances a sound gets. So no
conclusion is drawn from one reading, one re-read or one article, and every change is judged against an expected
count computed from opportunities. The user's own re-reads can only tighten the change thresholds (noise.py).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

LONGITUDINAL_CAL_VERSION = "m10-cal.1"


@dataclass(frozen=True)
class LongitudinalCalibration:
    version: str = LONGITUDINAL_CAL_VERSION

    # -- what counts as a different text -----------------------------------------------------------------
    same_text_min_containment: float = 0.8   # excerpts of one article pasted separately are ONE text: two texts
                                             # are the same when ≥ 80 % of the smaller one's content words occur
                                             # in the other (function words ignored)
    same_text_min_words: int = 5

    # -- personal vs article / word: recurrence must cross texts --------------------------------------------
    personal_min_clear: int = 3          # clear observations in the window (M9's k_conf at reading scale)
    personal_min_sessions: int = 3       # in at least this many sessions …
    personal_min_articles: int = 2       # … of at least two different articles (one article is not "personal")
    personal_min_words: int = 2          # … in at least two real words (otherwise it is word-specific)
    direction_min_share: float = 0.5     # of the sound's clear differences, the share going this way (M9 c_min)
    emerging_min_clear: int = 3          # as personal (≥ 3 clear, ≥ 2 words, ≥ 2 articles), short of ≥ 3 sessions
    emerging_min_sessions: int = 2
    word_min_clear: int = 2              # one word heard differently in ≥ 2 sessions: word-specific
    word_min_sessions: int = 2
    word_active_min_articles: int = 2    # a word habit worth practising crosses texts too
    article_bound_min_clear: int = 2     # clear observations, all within one article: limited to that article

    # -- change against the personal baseline (expected counts, never p-values) ------------------------------
    # retirement: "no longer recurring strongly enough to prioritise"
    retire_min_opportunities: int = 40   # eligible chances for the sound since the baseline
    retire_min_sessions: int = 3         # spread over ≥ 3 later sessions …
    retire_min_articles: int = 2         # … and ≥ 2 articles (one easy article cannot retire a pattern)
    retire_min_fresh_word_share: float = 0.5   # most later chances in words not read in the baseline window
    retire_min_expected: float = 3.0     # the baseline rate must predict several occurrences in the later window
    retire_max_share: float = 0.25       # observed ≤ share × expected (tightened, never loosened, by the noise floor)
    # improving: occurring less often than the baseline predicts, not yet retirable
    improve_min_opportunities: int = 20
    improve_min_sessions: int = 2
    improve_min_expected: float = 2.0
    improve_max_share: float = 0.5
    # persistent: still recurring at (most of) the baseline rate despite more exposure (hysteresis band 0.5–0.75)
    persist_min_sessions: int = 3
    persist_min_expected: float = 3.0
    persist_min_share: float = 0.75
    # stable → retired (silently monitored) after a quiet watch window; regression = the personal gates again
    watch_min_sessions: int = 2

    # -- context map (within one sound; ≥ 4 chances on each side, stable across two windows) ----------------
    context_min_opportunities: int = 4
    context_min_clear: int = 2
    context_ratio: float = 2.0
    context_max_share: float = 0.6       # a context covering most chances anyway is not specific (M9 rule)

    # -- noise floor from the user's own re-reads of the same sentence --------------------------------------
    noise_min_reads: int = 4             # a sentence read at least this often (same engine) …
    noise_min_first_half_clear: int = 3  # … whose first half pooled at least this many clear differences
    noise_min_sentences: int = 3         # enough such sentences to calibrate; otherwise defaults are used

    # -- practice → fresh-word outcome -------------------------------------------------------------------
    outcome_min_sessions: int = 2        # later ordinary sessions after practice with chances for the sound
    outcome_min_expected: float = 3.0    # baseline-predicted occurrences in unpractised words
    outcome_practised_min_expected: float = 2.0
    reverse_min_clear: int = 3           # the opposite direction must recur at pattern level …
    reverse_min_sessions: int = 2
    reverse_ratio: float = 2.0           # … at ≥ twice its own pre-practice rate
    method_change_after_continuing: int = 2   # continuing after this many practice outcomes: change the method

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_LONGITUDINAL = LongitudinalCalibration()
