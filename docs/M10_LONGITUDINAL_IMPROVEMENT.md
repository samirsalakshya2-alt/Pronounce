# M10 — Personal progress & adaptive improvement

**Objective.** Use accumulated readings to tell genuine personal pronunciation patterns from reading and article
variability, decide when a pattern is improving or has become stable, find where it occurs, test whether practice
shows up beyond the practised material, detect regression, and adapt what to practise next — without ever crediting
chance as learning, and without scores, dashboards or unsupported claims.

The measure of success is the next practice decision, not the amount of history shown.

## Why the design is conservative (measured on the real history, 2026-10-05/06)

* A sound heard clearly as something else is heard the same way at the same position on a re-read of the same
  sentence only about **25 %** of the time (107 of 435). A single re-read says almost nothing.
* Sentence-level counts swing several-fold between re-reads (one ~92-sound sentence: 5, 3, 3, 5, 1, 7, 6, 5, 2, 5).
* Article composition decides how many chances a sound gets (1 to 116 per session for /ɛ/).
* Many "different articles" were excerpts of one article pasted separately (100 % word containment).
* Only the first reading of each sentence is independent: of 139 eligible readings, 85 are first readings (8
  texts); 43 are re-reads, 9 practice and 2 retests.

So every conclusion is drawn from pooled, opportunity-counted, first-reading evidence across different texts, and
judged against an **expected count** (baseline rate × later chances) — never a p-value, interval or score.

## Architecture

```
reader store (immutable results)         longitudinal/  (pure core; the only I/O is source.py and store.py)
  attempts, jobs, views, coaching.json ─▶ source      eligibility (reader/status.py) + M9 evidence normalisation
                                          history     evidence classes (FRESH/REPEAT/RETEST/PRACTICE), word novelty,
                                                      per-session opportunity aggregates per sound and direction
                                          noise       noise floor from the user's own re-reads
                                          states      deterministic fold: lifecycle state + scope, with hysteresis
                                          context     within-sound context profile, stable across two halves
                                          practice    write-once records, measured status, fresh-word outcomes
                                          decisions   adaptive decision + evidence chain
                                          progress    orchestration, validation (fails closed), M9 adaptation
                                          store       cache (per session), append-only ledger + transition log
reader/service.py  progress(), progress_view(), record_practice(); incremental update after every summary
reader/http.py     GET /api/progress[?engine=&detail=1&rebuild=1]   POST /api/practice {session_id, target_id}
static/            "Your patterns over time" on the entry page; history line on M9's actions; practice record
```

M10 never reruns inference and never writes stored M4–M9 results. Everything it writes lives under
`<reader root>/longitudinal/`:

| File | Kind | Content |
|---|---|---|
| `practice/{id}.json` | write-once | explicit practice records |
| `ledger.jsonl` | append-only | every attempt extracted, with its source fingerprint and extractor version |
| `transitions.jsonl` | append-only | every state transition ever published, with algorithm versions |
| `cache/{session}.json` | rebuildable | extracted per-attempt evidence keyed by a source fingerprint |

## Four levels, kept apart

| Level | Example | Where |
|---|---|---|
| 1 observation | expected /θ/, heard /t/, clear (high-confidence M4 decode), in 'think', ▶ exact playback | `examples`, `clear_observations` |
| 2 pattern | /θ/ → /t/: 12 clear (+3 ambiguous) in 5 readings of 4 texts, 8 words, 140 chances | `totals`, `words`, `context` |
| 3 longitudinal state | PERSISTENT / IMPROVING / STABLE …, with baseline and later windows and expected counts | `state`, `scope`, `transitions`, `baseline`, `later` |
| 4 practice decision | MOVE_TO_FRESH_WORDS: "Practise /θ/ in new words, not only the practised sentences." | `decision` |

## Data model (all serialisable, versioned, deterministic, source-traceable)

* **Extracted record** (one per attempt): reading identity (session, attempt, job, segment, sentence, article and
  text group, engine, time, practice flag) + `eligible`/`reason` + compact sound rows (expected, heard, outcome,
  clear/ambiguous/counter/weak/excluded quality and exclusion reason, M4 confidence, word, word index, context,
  engine agreement, playback window) + fluency rows. Raw engine output is referenced, never copied.
* **PatternHistory** (`patterns[]`): pattern id, label, contrast group, reverse id, state, scope, text, transitions,
  baseline, later, post-stable windows, totals, words, context profile, per-session series, evidence-class counts,
  examples, advice history, practice outcomes, decision, position.
* **EvidenceWindow / OpportunityCount**: `baseline`, `later` (expected, observed, chances, sessions, texts,
  fresh-word share), `post_stable`.
* **NoiseCalibration** (`noise`), **ContextProfile** (`context`), **PracticeRecord** (`practice[].record`),
  **PracticeOutcome / TransferResult** (`outcomes[]`: unpractised, practised, during-practice, reverse, context
  split), **RegressionStatus** (state REGRESSED + `post_stable`), **AdaptivePracticeDecision** (`decision`),
  **LongitudinalConclusion** (`text` + `decision.recommendation`).
* Versions: `m10.1` (progress), `m10-id.1` (identity), `m10-src.1` (extractor), `m10-states.1`, `m10-noise.1`,
  `m10-ctx.1`, `m10-practice.1`, `m10-decisions.1`, calibration `m10-cal.1`. Each result carries the input
  fingerprint and generation time.

## Pattern identity

The unit of every longitudinal state is one **directed sound difference**: `sub:ɛ>ɪ` (expected /ɛ/, heard /ɪ/).
Contextual (`sub:ɛ>ɪ@word_position=medial`) and word-specific (`sub:ɛ>ɪ#word=anthropic`) ids are manifestations
that always reduce to their broad id, so history is never fragmented. Fluency patterns: `fluency:hesitation`.

M9 targets map onto these ids (`identity.link_target`): a contrast or family → every directed pair it contains; a
context or word target → its pairs, with the context / word recorded as a qualifier; fluency → its group; clarity
→ not tracked (M5 "not detected" is never absence). So `contrast:ɛ~ɪ`, `set:front_vowel_ladder` and
`lexical:best:ɛ` all link to `sub:ɛ>ɪ`, whatever M9 called it at the time. The two directions of a contrast share a
`contrast_group`; the opposite direction is the reverse-direction guard.

## Eligibility

Attempts — the reader's own decision (`reader/status.py`), kept with the reason, never dropped:
`discarded`, `rerecorded`, `not_analysed` (too short / failed), `mismatch` (different sentence), `boundary_withheld`
and `containment_withheld` (M7), `unconfirmed_identity`, `other_engine`, `no_view`.

Observations — M9's own normalisation decides quality: clear (`confident`), ambiguous (`supporting`, support only),
heard as expected (`counter`), not detected (`weak`), excluded (reference accent, function-word vowel,
context-predicted, merge or alignment suspect, neighbour shift, implausible pair, not interpreted, extra sound).
**Chances** for a sound are its judged occurrences: heard as expected, clear or ambiguous. Excluded and "not
detected" observations are neither chances nor differences.

## Evidence classes and word novelty

| Class | Meaning | Used for change? |
|---|---|---|
| FRESH | first eligible reading of this sentence text with this engine | yes — baseline, change, retirement, regression, transfer |
| REPEAT | a sentence read before | no (shown) |
| RETEST | a sentence read before that had been designated as M9 retest or practice material | no (shown) — chosen because differences occurred there |
| PRACTICE | read inside a practice session | no (shown as "during practice") |

Word novelty: a FRESH_WORD has never been read before (same engine). Retirement requires most later chances to be
in words not read in the baseline window; practice outcomes split practised from unpractised words.

**Texts.** Two articles are the same text when ≥ 80 % of the smaller one's content words (function words ignored)
occur in the other, so excerpts of one article pasted separately never count as "different texts".

## Chance / noise model

* **Expected counts.** For a pattern with baseline rate *r* (clear ÷ chances in the baseline window) and *n* later
  chances, the expected count is *r × n*. Statements are worded with it: "not observed in 48 chances across 4
  later readings, where your earlier rate predicts about 9.6".
* **Noise floor** (`noise.py`), from the user's own re-reads of the same sentence (same engine):
  position repeat rate, and the lowest later-half ÷ earlier-half pooled ratio over sentences read ≥ 4 times with ≥ 3
  earlier clear differences (≥ 3 such sentences needed). It can only **tighten** the change thresholds:
  `effective share = min(default share, lowest ratio)`. On the real history the lowest ratio is 1.1 (re-reads never
  reduced counts), so the defaults stand.
* No p-values, intervals, significance tests, probabilities, percentages or scores are computed anywhere.

## Personal vs article vs word

| Scope | Rule (FRESH evidence) |
|---|---|
| PERSONAL_RECURRING | ≥ 3 clear in ≥ 3 sessions of ≥ 2 texts, ≥ 2 real words, ≥ half of the sound's clear differences going this way |
| CONTEXT_SPECIFIC | personal, and concentrated in one context (below) |
| WORD_SPECIFIC | ≥ 2 clear, all in one word (word fragments included), in ≥ 2 sessions; an active target only across ≥ 2 texts |
| ARTICLE_BOUND | ≥ 2 clear, all within one text |
| INSUFFICIENT_HISTORY | otherwise |

Raw counts alone never decide; ambiguity never becomes clear through repetition.

## Pattern states (deterministic fold, with hysteresis)

| State | Rule |
|---|---|
| INSUFFICIENT_HISTORY | not enough evidence |
| EMERGING | every personal gate except the third session |
| PERSONAL_RECURRING | the personal gates met; the evidence so far is the **baseline** window, everything after it the **later** window |
| IMPROVING | later window: ≥ 20 chances in ≥ 2 sessions, expected ≥ 2, observed ≤ 0.5 × expected |
| PERSISTENT | later window: ≥ 3 sessions, expected ≥ 3, observed ≥ 0.75 × expected (0.5–0.75 keeps the current state) |
| STABLE | retirement gates (below) |
| RETIRED | STABLE, then a quiet watch window of ≥ 2 sessions; monitored silently |
| REGRESSED | after STABLE/RETIRED, the post-stable window meets the personal gates again (never one observation); it becomes the new baseline |

The later window is cumulative and the bands overlap, so one observation never makes a state oscillate. Every
transition is kept with its evidence and published once to the append-only log.

## Retirement ("stop practising this for now")

STABLE requires, in the later window: ≥ 40 chances, ≥ 3 sessions, ≥ 2 texts, most chances in words not read in the
baseline, the baseline predicting ≥ 3 occurrences, and observed ≤ ⌊expected × 0.25⌋ (tightened by the noise
floor). Wording: "Stable for now … No longer recurring strongly enough to prioritise." Never "fixed" or "mastered".

## Regression watch

STABLE and RETIRED patterns keep being counted silently. One isolated occurrence never reopens a pattern: the
post-stable window must meet the same personal gates as the original pattern (≥ 3 clear, ≥ 3 sessions, ≥ 2 texts,
≥ 2 words, direction). Wording: "This pattern had become stable, but it has started recurring again across several
readings."

## Context map

Within the pattern's own sound only: the rate inside a context (e.g. word-medial) vs the same sound's rate
everywhere else. Concentrated when: ≥ 4 chances on each side, the context covering ≤ 60 % of the sound's chances,
≥ 2 clear inside, the inside rate ≥ 2× outside, and more clear inside than the outside rate predicts in **both
halves** of the history. Dimensions: word position, dictionary stress (from the pronunciation dictionary — never
observed prosody; the UI says so), consonant cluster, sentence position. Speed and sentence length are not
dimensions: on the real history they showed no effect (4.6 / 4.2 / 4.5 clear per 100 sounds for slow / mid /
fast sentences; no length gradient). A concentrated context becomes the practice unit (sound × context).

## Practice records

Written once when the user starts practice from an M9 action ("Read these now" → `POST /api/practice`): the M9
target and action, its canonical patterns and reverse patterns, the practice type (M9 trainability), the material
(sentences and words), `mode: null` (careful/natural is not captured, never inferred), and the advice snapshot
(generation time, pool fingerprint). One record per practice session; the endpoint refuses sessions that were not
started as practice and advice that does not exist. Displayed advice (`coaching.json`) is advice history, never
practice. Status is measured from the practice session's recordings — not started / partial / completed, first and
last recording times; `duration` is `null` (never invented). Practice sessions from before practice records exist
are listed as `legacy_practice_sessions` (linked by title only) and never used for outcomes.

## Practice → fresh-word outcome (never causal)

Pre-practice baseline: FRESH evidence before the practice. Post: FRESH evidence in ordinary sessions after it.

| Outcome | Rule |
|---|---|
| INSUFFICIENT_OUTCOME | practice not started, no baseline, or after it < 2 sessions / < 3 predicted occurrences in unpractised words |
| TRANSFER | in unpractised words: observed ≤ 0.5 × predicted — "Improvement was observed after practice" |
| REVERSE_DIRECTION | as TRANSFER, but the opposite direction rose: ≥ 3 clear in ≥ 2 sessions and ≥ 2 words, ≥ 2× its own pre-practice rate (overcorrection guard) |
| NO_TRANSFER | not decreased in new words, but decreased in the practised material (later fresh readings of practised words, or the practice session's own retests) |
| CONTINUING | neither |

A good retest alone can never establish transfer, improvement, retirement or a baseline; it can only support
NO_TRANSFER, which leads to a decision to move to fresh words. When the practised target had a context
(e.g. word-medial) and transfer is seen, the outside of that context is checked too.

## Adaptive decisions

| Situation | Decision |
|---|---|
| STABLE | RETIRE |
| RETIRED | WATCH_FOR_REGRESSION |
| REGRESSED | CONTINUE_CURRENT_TARGET (reopened) |
| latest outcome REVERSE_DIRECTION | CHANGE_PRACTICE_METHOD (practise both directions) |
| latest outcome NO_TRANSFER | MOVE_TO_FRESH_WORDS |
| latest outcome TRANSFER, still occurring outside the practised context | MOVE_TO_NEW_CONTEXT |
| CONTINUING after ≥ 2 practice rounds | CHANGE_PRACTICE_METHOD |
| IMPROVING, or latest outcome TRANSFER | REDUCE_PRIORITY |
| PERSONAL_RECURRING / PERSISTENT, or a word habit across texts | CONTINUE_CURRENT_TARGET (unit: context, word or sound) |
| EMERGING, ARTICLE_BOUND, INSUFFICIENT | INSUFFICIENT_HISTORY (watched) |

INCREASE_CONTEXT_DIFFICULTY is defined but never emitted: no supported ordering of difficulty exists. Every pattern
gets a decision; there is no top-N. The active ones are ordered lexicographically: decision (method change, fresh
words, new context, continue …), state (regressed, persistent, personal …), sessions with clear evidence, words,
clear count, id. Every decision has its evidence chain: observation (▶ examples) → pattern → context → history
(baseline / since then / transitions / what was not used) → practice outcome → recommendation.

## M9 integration (presentation layer only)

M9's qualification, Pareto ordering, `coaching.json`, `/api/coaching` and "This reading" are unchanged.
`progress.adapt_coaching` reads M9's live result and, for the same engine only:

* adds a history line to each M9 action ("Your history: Recurring across 6 readings of 6 different texts …");
* moves an action whose every pattern is STABLE or RETIRED into a collapsed "Stable for now in your history" list
  instead of showing it as a priority;
* lists M10 decisions that M9 does not show (e.g. MOVE_TO_FRESH_WORDS) as `from_history`.

The integration happens in `ReaderService.progress_view` → `GET /api/progress` → `reader.js` (entry page).

## User experience

The entry page shows **What to practise now** (M9, annotated) and **Your patterns over time** ("Based on all your
readings"): how much history exists, then groups — *Keep working on*, *Occurring less often*, *Stable for now*, *New —
not enough history yet* — each pattern with its type ("Likely personal", "Likely personal · one context", "One word",
"One text so far"), the decision, a one-line conclusion, the next step and "How Pronounce knows this". A "How to
read this" note states the noise floor, the engine note and that nothing is a score.

## Calibration (`m10-cal.1`) — provisional, named, testable

| Parameter | Value | Why |
|---|---|---|
| same text | ≥ 80 % content-word containment (≥ 5 words) | excerpts of one article are one text |
| personal | ≥ 3 clear, ≥ 3 sessions, ≥ 2 texts, ≥ 2 words, direction ≥ 0.5 | recurrence must cross texts and words, one direction (M9's c_min) |
| emerging | personal gates except the third session | near-personal only |
| word habit | ≥ 2 clear in one word, ≥ 2 sessions; active across ≥ 2 texts | a word read twice in one article is not a habit |
| article-bound | ≥ 2 clear, one text | "limited to the article tested so far" |
| retire | ≥ 40 chances, ≥ 3 sessions, ≥ 2 texts, ≥ 50 % new words, expected ≥ 3, observed ≤ ⌊0.25 × expected⌋ | absence must be informative, not untested |
| improving | ≥ 20 chances, ≥ 2 sessions, expected ≥ 2, observed ≤ 0.5 × expected | early, cautious |
| persistent | ≥ 3 sessions, expected ≥ 3, observed ≥ 0.75 × expected | hysteresis band 0.5–0.75 |
| watch | ≥ 2 sessions | stable → retired |
| context | ≥ 4 chances each side, ≤ 60 % share, ≥ 2 clear, ratio ≥ 2, both halves | M9's context rules + stability |
| noise | ≥ 4 reads, ≥ 3 earlier clear, ≥ 3 sentences | enough re-reads to calibrate |
| outcome | ≥ 2 later sessions, expected ≥ 3 in unpractised words (≥ 2 in practised) | no outcome from one reading |
| reverse | ≥ 3 clear, ≥ 2 sessions, ≥ 2 words, ≥ 2× pre-practice rate | overcorrection at pattern level only |
| method change | ≥ 2 continuing outcomes | one round is not enough to judge a method |

## What M10 can honestly claim

* which patterns recur across different texts and words (likely personal), and which so far appear limited to one
  text or one word;
* that a pattern occurred less often than the user's own earlier rate predicts, over enough later chances in new
  words and texts ("early evidence suggests", "stable for now");
* that a stable pattern started recurring again at pattern level;
* where a pattern is concentrated, compared within its own sound;
* what was practised, how much of it was read, and what was observed afterwards in new words — including no transfer
  and a rising opposite direction.

## What M10 deliberately does not claim

* causality ("practice fixed it"), permanence ("fixed forever", "mastered"), percentages, scores, streaks or ranks;
* anything from one reading, one re-read, one text or one observation;
* retention over weeks (the history spans days), learning velocity, practice efficiency, diminishing returns;
* speed or spontaneous-speech transfer (read-aloud only; no rate effect visible; no natural-speech capture);
* prosody or stress effects beyond the dictionary label; vowel-quality trends (no formants); articulation;
* native-speaker comparison; clinical statements;
* confirmation from engine agreement (OpenPronounce and raw Wav2Vec2 share one acoustic model).

## Engines

Histories are kept per engine and never pooled; the default is the engine of the most recent reading. Engine
differences stay traceable (every counted observation id names its session, and sessions name their engine). M10
needs no inference: tests make the analysis engines fail while M10 runs. R01–R20 through the reader with both
engines are a regression source for the machinery (one text, so nothing becomes personal), never learning ground
truth.

## Privacy and reproducibility

Local only; no audio or results leave the machine. Everything is derived from immutable stored results; a full
rebuild (`GET /api/progress?rebuild=1`) re-extracts every attempt without re-running inference and gives the same
result as incremental updates (tested at every step of growing histories). The test suite can never touch the
user's real store: `ReaderStore()` defaults to a temporary directory inside tests, and the whole session fails if
any file under `~/.pronunciation_lab/reader` changed.

## Performance (local, no inference)

| History | Full rebuild | Update, nothing new | Update, +1 reading |
|---|---|---|---|
| 5 sessions / 15 readings (synthetic) | 37 ms | 10 ms | 12 ms |
| 30 / 90 (synthetic) | 210 ms | 58 ms | 60 ms |
| 300 / 900 (synthetic) | 2.2 s (66 MB peak) | 0.8 s | 0.75 s |
| real store, 139 eligible readings | 1.5 s (37 MB peak) | — | — |

The fold is linear in history length; the cache is one file per session, so an update rewrites only what changed.

## Manual validation protocol

Each step: open the entry page, read "Your patterns over time", expand "How Pronounce knows this", play an example.

1. **New user / one reading** — one text: only "N readings so far …"; no pattern called personal.
2. **Three readings with a recurring pattern** — three different texts with the same difference in different
   words: *Keep working on*, "likely personal".
3. **Article-specific** — one text read across three sessions (different sentences): "Appears limited to the
   article tested so far"; not a target.
4. **Five+ readings** — a baseline forms; "Before: … (about 1 in N)" appears in the chain.
5. **Approaching retirement** — several new texts where the sound is fine: *Occurring less often*, "early evidence".
6. **Retired** — ≥ 40 chances in ≥ 3 later texts without it: *Stable for now*; the matching M9 action moves to
   "Stable for now in your history".
7. **Regression** — the pattern again in three new texts: "had become stable, but … recurring again".
8. **Practice → transfer** — "Read these now", read the practice sentences, then two new texts without the
   pattern: "Improvement was observed after practice".
9. **Practice → no transfer** — practice reads cleanly, new texts still show it: "Move to new words".
10. **Context-specific** — the difference only in stressed (or word-medial) syllables: "Likely personal · one
    context" with the dictionary-stress note.
11. **Several patterns** — every qualifying pattern listed, in a stable order; none hidden.
12. **Nothing qualifies** — "No pattern has enough history yet to say how it is changing."

## Future work (deferred, not partially implemented)

Retention after weeks (needs calendar history and a record of attention moving elsewhere); a controlled speed ladder
(same sentence slow / normal / fast) before any speed claim; spontaneous speech (needs a ground-truth strategy);
learning velocity, practice efficiency and diminishing returns (need several practice cycles per pattern);
posterior-margin trends (validate against re-read noise first); formant-based vowel tracking from the original
audio; listening tasks; INCREASE_CONTEXT_DIFFICULTY once a difficulty ordering is evidenced; explicit careful /
natural practice mode.
