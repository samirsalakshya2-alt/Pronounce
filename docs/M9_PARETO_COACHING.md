# M9 — Pareto Coaching Engine

M9 answers one question: **"If I have 10–15 minutes to practise, what should I spend them on?"**

From everything M4–M8 safely observed across your recent readings, it selects **0–3 practice actions**.
Three is a ceiling, not a quota. When the evidence isn't there, it says so and recommends nothing.

It is a coach, not a report:

* no score, no ranking, no list of everything heard differently;
* no recognition, no new model, no change to any stored evidence;
* no claims about articulation, perception, or improvement (improvement is M10).

## What you see

**"What to practise now"** appears in two places:

* at the top of the reader's start page: live, over your recent readings;
* in *Your reading* after you finish a session, below "This reading": the advice issued then, stored and never
  silently changed. It replaces the previous unbounded "Sounds worth practising" list. The M12 summary data is
  unchanged.

Each action shows:

* the action, for example "Practise telling apart the vowels in beat, bit, bait, bet and bat (/eɪ/, /ɛ/, /ɪ/)";
* about how many minutes it should take;
* why, in one or two sentences with counts and counter-evidence;
* the practice steps;
* up to 3 of your own examples with ▶;
* up to 2 sentences to retest, with ▶ and **Read these now**, which opens a short practice article of exactly
  those sentences.

**Evidence** opens on request and keeps observed, inferred and general-knowledge statements apart:

* what was observed and in how many sessions, sentences and words;
* how often the sounds were heard as expected, with examples;
* concentration;
* the hypothesis;
* general knowledge (never about you);
* why this action was chosen over the alternatives;
* possible transfer, marked as not measured.

## Two outputs, two questions

| | **This reading** | **What to practise now** |
|---|---|---|
| question | what were the major improvement areas in this article, and what was already stable? | what should I practise next? |
| scope | one session's summarised sentences (the latest eligible attempt per sentence, as in *Your reading*) | the recent eligible readings across sessions (the pool) |
| nature | a current-reading diagnosis: 0–3 improvement areas, then strengths (counts, rates, examples) | coaching: 0–3 practice actions |
| module | `coaching/reading.py` | `coaching/engine.py` and the layers below |
| stored | `sessions/{sid}/reading_feedback.json` (regenerable) | `sessions/{sid}/coaching.json` (advice issued with the summary) |
| label in the UI | "This reading only" | "Based on your recent readings" |

M10 (later) adds "what persists across readings" and "am I improving", built on these records. M9 makes no
cross-reading or over-time claims in either output.

## Four levels, never blurred

| Level | Type | Origin | Example |
|---|---|---|---|
| observation | `EvidenceUnit` / `FluencyUnit` | measured | expected /ɛ/, heard /eɪ/, high confidence, *best*, reading R |
| pattern | `Pattern` | counted | /ɛ/ heard as /eɪ/: 12 confident, 7 words, 8 sessions |
| target | `Target` | inferred (+ labelled knowledge) | "the vowels of *bit / bait / bet* were often heard as one another" |
| intervention | action + plan | coaching | listen → contrast → your words → your sentences → retest |

## Architecture

```
reader store (unchanged) ──► reader/coaching_source.py   eligible readings + views + provenance (only I/O)
                                   │
src/pronunciation_lab/coaching/    ▼  pure, deterministic
  pool.py        N most recent eligible readings of one engine; sufficiency floor; fingerprint
  evidence.py    M4/M5/M7/M8 → units; unit quality; exclusion reasons (never deleted)
  patterns.py    counting only: contrasts, counter-evidence, exposure, fluency, clarity
  targets.py     formation, family merge (5 conditions), lexical/context narrowing, tiers
  knowledge.py   declared phonetic knowledge (plausibility, families, M4 guidance): never evidence
  leverage.py    ordered criteria with meaningful-difference margins → ComparisonRecord
  selection.py   0–3, residual re-gating, absorbed symptoms, ≤ 1 fluency action
  plan.py        trainable interventions and practice steps (own audio only)
  contract.py    CoachingResult + validate_coaching (fails closed)
  calibration.py every tunable value, versioned, echoed in every result
  engine.py      run_coaching()
reader/service.py  coaching() on demand (cached by evidence key); snapshot at summary time
reader/store.py    sessions/{sid}/coaching.json (issued advice)
reader/http.py     GET /api/coaching[?detail=1]
static/            read.html, reader.js, reader-feedback.js (renderCoaching; playRef honours the reference's
                   session), reader.css
```

## Evidence pool

* **Reading:** one eligible attempt. The reader's own decision (`reader/status.py`) applies: identified, feedback
  shown, not discarded or marked for re-recording, analysed by the session's engine. Withheld sentences (M7)
  and continued speech never reach M9.
* **Window:** the N most recent eligible readings (count-based) of the engine of the most recent reading.
  Other-engine readings are excluded with a reason. An optional maximum age is off by default.
* **Sufficiency:** no action below 10 readings or 2 sessions.
* **Re-reads:** each one is a reading, but breadth counts distinct sentences and words, and the gates require
  ≥ 2 sessions. Re-reading one sentence cannot establish a target on its own.
* **Identity across pasted copies:** sentences and articles are matched by normalised text.

## Unit quality and exclusions

| Quality | When |
|---|---|
| confident | M4 substitution, high/moderate confidence, plausible pair, analysis ok |
| supporting | M4 ambiguous, or confident in a sentence M7 marked low-confidence. **Never counts towards any gate or breadth** |
| weak | not detected: a clarity signal only |
| counter | heard as expected (counter-evidence) |
| excluded | `not_interpreted`, `extra_sound`, `reference_variant`, `function_word_variant` (vowels in function words), `implausible_pair` (across vowel/consonant), `neighbour_shift` (heard = the neighbouring expected sound), `context_predicted` (M5 natural reduction / coarticulation), `merge_suspect`, `unreliable_level` (M8) |

## Targets and tiers

Target kinds are a closed set:

| Kind | Meaning |
|---|---|
| CONTRAST | one-way or two-way |
| SET | knowledge family, merged only under the five conditions |
| CONDITIONED | context |
| LEXICAL | one word |
| FLUENCY | hesitation, filler or repetition |
| CLARITY | capped at emerging: never an action |

Anything else (perception, knowledge vs execution, articulators, stress, rhythm, intonation, rate-dependence,
"not automated", swallowing as a cause) is in `UNSUPPORTED`. It is published as `not_assessed` and rejected by
the validator.

**ESTABLISHED** requires all of the following. The breadth checks count confident units only.

| Check | Requirement |
|---|---|
| confident units | ≥ K_CONF |
| words | ≥ W_MIN |
| sentences | ≥ S_MIN |
| sessions | ≥ 2 |
| concentration | ≥ C_MIN: the target's share of these sounds' confident deviations |
| contradiction | none |
| engine disagreement | ≤ DISAGREE_MAX among compared units |

LEXICAL targets have their own checks:

* ≥ LEX_K_CONF confident units;
* ≥ 2 readings of the word;
* ≥ 2 sessions;
* the word occurs in ≥ 2 sentences of your reading;
* not a hyphenation fragment.

**EMERGING:** some confident evidence, but a check fails. Shown only in the detailed view (`listen_check`); it
never takes an action slot.

**INSUFFICIENT:** never surfaced.

**Engines:** agreement between the two local engines never raises anything (they share one acoustic model).
Disagreement, where a comparison exists, lowers the tier.

## Consolidation (parsimony)

A **family merge** requires all five conditions, each recorded:

1. each member passes on its own evidence (all gates except concentration, which is measured on the family:
   a sound with two systematic competitors in one family splits its deviations between them);
2. a declared relation, with coherent directions;
3. one intervention applies;
4. the union explains ≥ M_MERGE × the largest member;
5. no contradiction (for example, the reverse direction of a directional family).

A directional family takes only its own direction; the opposite direction keeps its own candidate.

**Narrowing**, always against counter-evidence:

* **Lexical:** ≤ 2 words carry ≥ LEX_SHARE of the confident units, each with enough units and readings,
  and the rest would not pass on its own.
* **Context:** a condition with enough occurrences on both sides, inside share ≥ CTX_RATIO × outside, enough
  confident units in ≥ 2 sessions, and specific: the condition covers ≤ CTX_MAX_SHARE of the sound's
  occurrences. "Vowels in the middle of words" is not a context.

A condition seen only in one context, with too little elsewhere, gives the note "only observed …, not enough
evidence elsewhere".

Every unit has one owner. Replaced targets are recorded as absorbed.

## Leverage and selection (no score)

**Comparison:** criteria in order:

1. tier;
2. breadth (sentences);
3. recurrence (sessions);
4. breadth (words);
5. coverage;
6. reach;
7. concentration;
8. trainability.

A criterion decides only when the difference is meaningful (ratio ≥ MARGIN_RATIO and absolute ≥ its margin;
fractions and levels use the absolute margin). Words, coverage, reach and concentration are never compared
between a fluency target and a sound target: counts of pauses and of confusions are not commensurable. If
nothing decides, a stable tie-break is recorded as such. Every decision is a `ComparisonRecord`, for example
"Chosen over fluency:hesitation — recurrence (different sessions): 7 vs 3."

**Selection:**

1. Pick the best eligible candidate.
2. Remove its units from every other candidate.
3. Re-evaluate the rest on their residual evidence; one that no longer passes was a symptom (absorbed).
4. Repeat until 3 actions or nothing passes.

At most 1 fluency action. Expected transfer is never a criterion; it is shown labelled "hypothesised".

## Practice

| Kind | Steps |
|---|---|
| contrast / set | listen (your heard-as-expected, then heard-differently examples) → contrast slowly → your words → your sentences → retest |
| conditioned | listen → the sound in this position, then others → words → sentences → retest |
| lexical | listen → the word slowly, then at your normal pace → sentence → retest |
| fluency | listen → mark phrase breaks, read phrase by phrase → whole sentence → retest |

**Trainability:**

| Level | When |
|---|---|
| `specific_guidance` | existing M4 contrast guidance (12 pairs) |
| `listen_compare_fallback` | fixed wording; needs ≥ 2 of your own clear examples in different words |
| `word_practice` | lexical target |
| `phrase_practice` | fluency target |

Otherwise there is no intervention, and the target cannot be selected. Every example and sentence is your own
recording with exact playback.

**Time split:** 12 minutes for one action, 7 + 5 for two, 5 + 4 + 3 for three.

## Output contract (`CoachingResult`)

**Top level:**

* `version`, `generated_at`, `knowledge_version`, `calibration`;
* `pool` (readings, sessions, sentences, engine, fingerprint, unit qualities, exclusion counts);
* `state` (`actions` / `no_action` / `unavailable`);
* `actions[0..3]`;
* `no_action` {code, message, what_would_help};
* `not_assessed`, `caveats`, `integrity`;
* with `?detail=1`, `detail`: every candidate with its checks and measures, `listen_check`, selection rounds,
  absorptions, not-prioritised with reasons, consolidation records, the knowledge layer.

**Action:**

* `action_text`, `time_minutes`;
* `target` (kind, sounds, pairs, condition, word, family, hypothesis, tier, checks, measures);
* `why` {text, measures, decided_by, absorbed};
* `counter_evidence`, `transfer` (origin "hypothesised");
* `practice` {trainability, steps, guidance, examples, counter_examples, words, retest_sentences};
* `supporting_unit_ids`, `counter_unit_ids`, `knowledge_contributions`, `consolidation_record`, `notes`,
  `caveats`.

**`validate_coaching`** (fail closed: an invalid result shows no action) checks:

* at most 3 actions, at most 1 fluency;
* established tiers only;
* units disjoint across actions;
* cited units exist and aren't excluded;
* counts recomputable from units;
* every reference playable;
* closed target kinds;
* knowledge labelled;
* transfer hypothesised;
* banned wording in M9's own text (your article words are masked);
* known exclusion reasons;
* a reason code for every no-action.

**No-action codes:** `history_too_small`, `single_session`, `only_emerging`, `only_ambiguous`,
`only_excluded_kinds`, `contradictory`, `no_trainable_intervention`, `audio_unavailable`, `nothing_recurring`.

## Persistence

* Raw evidence: the reader store, unchanged.
* Patterns, targets, interventions: recomputed on demand (deterministic). The service caches by the eligible
  evidence key, and the cache is invalidated by any new eligible reading.
* Issued advice: `sessions/{sid}/coaching.json`, written with the summary. A coaching failure never breaks the
  summary; it is stored as `unavailable`.
* Calibration and knowledge: versioned modules, echoed in every result.

## Calibration (provisional, `m9-cal.3`)

| Parameter | Value | Note |
|---|---|---|
| pool_n_max / min readings / min sessions | 40 / 10 / 2 | |
| K_CONF / W_MIN / S_MIN / sessions | 5 / 3 / 3 / 2 | K_CONF raised from 4 (see below) |
| C_MIN | 0.5 | |
| DISAGREE_MAX | 0.34 | uncalibrated: comparisons are rare |
| M_MERGE | 1.5 | |
| CTX_MIN_IN / OUT, CTX_RATIO, CTX_MAX_SHARE | 8 / 8, 2.0, 0.6 | |
| LEX_SHARE / LEX_MAX_WORDS / LEX_K_CONF / readings / relevance | 0.8 / 2 / 3 / 2 / 2 | |
| MARGIN_RATIO / margins | 1.5 / sentences 2, sessions 2, words 2, coverage 2, reach 2, concentration 0.1, trainability 1 | |
| ordering | tier, breadth_sentences, recurrence_sessions, breadth_words, coverage, reach, concentration, trainability | |

**Observations from the developer's own stored readings** (read-only, never committed: 15 sessions, 61
eligible readings, the pool = the 40 most recent across 12 sessions):

* **Exclusions:** 707 observations uninterpreted, 268 function-word vowels, 161 reference variants, 109
  implausible cross-class pairs (e.g. /iː/→/t/), 59 context-predicted, 9 neighbour shifts. Confident: 115.
  Supporting: 319.
* **Result:** two actions.
  1. The front-vowel family (/eɪ/, /ɛ/, /ɪ/): 17 confident observations, 12 words, 7 sessions, heard as
     expected 230 times, about 43 of every 100 words.
  2. Phrase-chunk practice for within-phrase hesitation: 31 pauses, 11 sentences, 3 sessions.
  A filler pattern was established but not shown (one fluency action). "best" is only a listen-check (one
  sentence, re-read 5 times).
* **Corrections made because of this data:**
  * breadth gates had counted ambiguous observations (/s/~/ʃ/ looked established with 4 confident units in
    14 "words");
  * "in the middle of words" was being treated as a context for vowels;
  * a fluency pattern beat a vowel family by comparing 31 pauses with 17 confusions. Recurrence (sessions)
    was added and non-commensurable criteria are skipped across modalities;
  * a fluency plan built on a sound-only field;
  * K_CONF 4 → 5.
* **Sensitivity:** the two actions are stable for K_CONF 3–6, C_MIN 0.4–0.6, pool 20–61, every
  CTX_MAX_SHARE, W_MIN and DISAGREE_MAX tested. A third action at K_CONF 4 rested on exactly 4 confident
  observations and changed with pool size (/d/~/ɹ/, none, /s/~/ʃ/).
* **Split-half:** halves of the history (about 30 readings each) agree on the hesitation action. The vowel
  family appears in one half only. The data volume is near the limit for sound targets, which argues against
  lowering thresholds.

## Tests

* `tests/app/test_m9_core.py`: the pure core, from synthetic evidence (`m9helpers.py`). Covers:
  * knowledge, unit quality and every exclusion, the pool, patterns;
  * targets: one-way and two-way contrasts, family merge and refusals, contradictions, narrowing to context
    and to a word, fragments, engine disagreement, clarity cap, fluency;
  * leverage and selection: deciding criterion, ties, cross-modality, 1/3/3+1 actions, absorption, one fluency;
  * plan and contract: practice steps, fallback, abstention codes, validator defects, user words,
    determinism.
* `tests/app/test_m9_source.py`: the adapter on a synthetic store (eligibility exclusions, provenance, M7
  sentence references, pre-M7 views, engine agreement, store → action end to end).
* `tests/app/test_m9_service.py`: on-demand coaching, cache, issued snapshot (stable on reopen), failure
  isolation, HTTP.
* `tests/app/test_m9_real.py`: R01–R20 through the real reader with both engines (invariants, references,
  determinism, under 2 s), and a real browser.
* `tests/app/js/reader_feedback.test.js`: the concise coaching view and evidence lines.

## This reading (`coaching/reading.py`, `m9-read.3`)

A current-reading diagnosis beside the coaching, never part of it. It answers, for this reading only:

1. **What were the major pronunciation improvement areas in this reading?** (primary)
2. **What was already stable in this reading?** (secondary)

then fluency and cautions. Practice prioritisation stays with *What to practise now* (recent history);
cross-reading persistence and change over time are M10.

**Inputs:** exactly `summary.inputs` (`reader/coaching_source.load_session_inputs`). Nothing from other
sessions, the pool, or `coaching.json`. Superseded and re-recorded attempts are not included.

**What it may do:** reuse the shared stateless parts (evidence normalisation, unit quality and exclusions,
counting, `targets.form_targets` / `targets.evaluate` at reading scale via `ReadingCalibration.formation()`);
name sounds, families and contexts from the declared knowledge; report counts, rates, examples and
counter-examples.

**What it never does:** recommend practice; expose coaching hypotheses or tiers; use leverage, selection,
interventions, practice plans or the pool (a test checks its imports); make cross-reading or over-time claims;
manufacture a finding or hide one. The number of improvement areas is decided by the evidence (0, 1, 2, 3, 5 or
more are all valid); there is no display limit.

### Evidence: clear and ambiguous are never merged

* **clear** = an M4 substitution at high or moderate confidence in a sentence whose analysis was not
  low-confidence (unit quality `confident`);
* **ambiguous** = an ambiguous M4 decode, or a substitution in a low-confidence sentence (`supporting`).

Ambiguous observations may help a pattern *recur* (Path B) but are never counted as clear, never enter the
clear-evidence rate and never decide severity. Every area carries both counts and its wording discloses the
composition ("3 observed differences in 3 words: 2 clear (2 high-confidence, 0 moderate-confidence) and 1
ambiguous"). Exclusions are unchanged and apply on every
path: reference accent, function-word vowels, context-predicted (M5 natural reduction / coarticulation), merge
or alignment suspects, neighbour shifts, implausible pairs, uninterpreted and extra sounds.

**Clear-evidence rate**, per expected sound = clear observations of that sound ÷ its in-scope occurrences, where in-scope occurrences are the
non-excluded occurrences of the area's expected sound(s) (in its condition or word, if any) that were heard as
expected, heard as another sound (clear or ambiguous), or not clearly detected. The observed rate
(clear + ambiguous ÷ occurrences) is stored for reference only. An area's rate is that of its most
affected sound, and the text shows every sound's numbers: pooling both sounds of a two-way contrast would
let a frequent partner (e.g. /ə/ or /ɪ/) dilute a real confusion (on the stored data, /eɪ/ ↔ /ɪ/ with 2 of
15 /eɪ/ clear would have read as 3 of 44).

**Real words:** word fragments from the article text (e.g. "competi", "tion", flagged `fragment_suspect`)
do not count as distinct words for Path B or for ordering.

### Three levels of evidence, kept apart

| Level | Question | Where it comes from | Shown as |
|---|---|---|---|
| observation | how strong is the evidence that this one occurrence was heard differently? | M4: clear (high- or moderate-confidence substitution) or ambiguous; exclusions | "Individual observations: … 7 clear (6 high-confidence, 1 moderate-confidence) and 3 ambiguous" |
| pattern | is there accumulated evidence of one recurring pattern, and how far does it generalise? | recurrence of clear observations across sentences and words, same direction, counter-evidence, consolidation (family, context, word) | "Pattern: recurring … / word-specific … / context-specific … / possible …" with a pattern-type chip |
| priority | in which order should the qualifying areas be read? | the Pareto ordering below | "Placed before area 2 for a broader pattern." |

Recurrence is evidence, not a display statistic: a pattern qualifies only when clear observations *recur*
(several sentences, several words, one direction), so five moderate-confidence clear decodes of /ɛ/ heard as
/ɪ/ in four sentences can qualify while one very strong single observation cannot. Recurrence never upgrades
ambiguity: ambiguous decodes support a recurring pattern (Path B) only alongside at least two clear ones,
and five ambiguous decodes alone never qualify. Nothing is turned into a probability or a weighted score.

**Direction.** Measured on the sound that carries the pattern's clear evidence: of all clear differences of
that sound in this reading, the share that went this way (observations going this way count wherever they are
described). Path A has it through the shared concentration gate; Path B requires ≥ 0.5 too (`direction`), so
unrelated substitutions that merely share an expected sound (/ɛ/ → /ɪ/, /ɛ/ → /æ/, /ɛ/ → /ə/, two each) do
not form a pattern. On the stored data this gate removes nothing; it is a consistency guard.

### 1. Major improvement areas (no limit)

* **Path A — clear evidence** (`band: clear`): an established target from `form_targets` at reading scale
  (contrast, sound family, context-specific, or word-specific), unchanged gates: ≥ 3 clear observations
  (word-specific: ≥ 2 of one word in ≥ 2 sentences), ≥ 2 words, ≥ 2 sentences, concentration ≥ 0.5, not
  contradicted, engine-disagreement check — plus clear-evidence rate ≥ 0.10.
* **Path B — mixed evidence** (`band: mixed`): a two-way contrast not already described by a sound-level Path A
  area, with ≥ 3 observations of which ≥ 2 clear, ≥ 2 sentences, ≥ 2 distinct real words, clear-evidence rate
  ≥ 0.10, direction ≥ 0.5, not contradicted, engine-disagreement check. 1 clear + 5 ambiguous never qualifies.
* **Path C — clarity** (`band: possible`): the existing M5 two-stream note (final sounds weakened or not
  clearly detected in ≥ 3 words, never natural reductions). Always worded as possible; it has no rate.

**Pattern scope** (`pattern_scope`, shown as a chip): *Recurring sound pattern* (contrast, sound family),
*Context-specific pattern* (one word position), *Word-specific* ("strong for this word; the evidence is
limited to this word"), *Possible*. Word-specific areas are kept, never discarded for being narrow, and never
presented as a sound-level finding.

**Membership** (the gates decide how many; nothing is truncated). Every qualifying candidate becomes an area,
and an observation is described once. When two qualifying candidates share observations, they are taken in
the Pareto order below; the first describes the shared observations and what remains of the other is
**re-gated on its own** (as the recent-history selection does): kept if it still qualifies, otherwise recorded
in `absorbed` with the reason, its remaining observations counted in "other differences". This replaced a
silent drop that, on a real reading, hid a broader pattern behind its own word subset (/ɑː/ → /oʊ/ in
'models' and twice in 'anthropic': the consolidation had kept it a sound pattern, 2 of 3 being below the 0.8
word-share test, but the 'anthropic' candidate took its observations first and the pattern disappeared).

Single observations never become areas; they stay in the detailed report and are only counted here.

**Ordering (Pareto)** — lexicographic, never a weighted score; each area records the criterion that placed it
before the next one (`ordering.placed_above_next_by`, worded in `order_text`):

1. evidence: clear > mixed > possible;
2. pattern scope: sound > context > word;
3. clear-evidence rate band: high (≥ 0.40) > moderate (≥ 0.20) > low (≥ 0.10);
4. distinct sentences;
5. distinct real words;
6. clear observations;
7. stable id.

Scope comes before the rate band because a word-specific rate is over one word's occurrences (a word read twice
and heard differently twice is 2 of 2 by construction) and is not comparable with a rate over every occurrence
of a sound; before this rule every word-specific area outranked every broad pattern of the same evidence. 3 of 5
still ranks above 6 of 50 within a scope, whatever the raw counts.

When nothing qualifies (and the reading has ≥ 2 sentences): "No major pronunciation pattern was strong
enough to call out in this reading."

### 2. Already stable in this reading (up to 3)

Computed after the areas; it never takes an area's place. A sound qualifies when it:

* is a diagnostically useful dimension in the declared knowledge (front / back vowel sets, s/z/sh/zh, th);
* occurred ≥ 10 times in this reading (heard as expected, clear, ambiguous or not clearly detected);
* was heard as expected in ≥ 90 % of them (ambiguous decodes count against it);
* has fewer than 2 non-excluded differences in this reading;
* is not involved, on either side, in any qualifying improvement area.

Wording: "In this reading, /ð/ was heard as expected in 13 of 13 occurrences." Never "perfect" or "correct",
never advice.

### 3. Fluency, 4. Cautions

Fluency: at most one line, from sentences with a measurable speech level only (≥ 2 noticed observations of
one kind), or "no possible hesitation pauses were noticed inside phrases" when every sentence was measurable.
Cautions: withheld or unconfirmed sentences, unmeasurable level, low-confidence analysis, too few sentences,
reference-accent differences not counted.

### Output, UI and validation

`ReadingFeedback`: `coverage`, `improvement_areas`, `no_area_text`, `absorbed`, `strengths`,
`fluency`, `fluency_note`, `cautions`, `other_differences`, `provenance`, `state` (`feedback` /
`insufficient` / `unavailable`), `integrity`. A stored description from an older version is rebuilt from the
same write-once results (unlike `coaching.json`, which is never rebuilt).

UI, inside *Your reading*: **This reading** ("This reading only") → *Major improvement areas* (all of them,
numbered; headline with the pattern-type chip, individual observations, pattern, clear-evidence rate and
heard-as-expected count, why it is placed there, ▶ examples (ambiguous ones labelled) and ▶ heard as expected) → *Already stable in
this reading* → *Fluency* → *Cautions*; then **What to practise now** ("Based on your recent readings",
unchanged); then the detailed report (unchanged).

The validator (fails closed) recomputes every count (including the high/moderate split and direction), both
rates, the path minimums, the pattern scope, the ordering, the
strength criteria and the area/strength disjointness from the units of this reading; rejects observations from
other readings or excluded ones, double descriptions, missing composition disclosure, examples without exact
playback, coaching keys (`actions`, `hypothesis`, `practice`, `tier`, `pool`, `decided_by`), and prescriptive,
judgemental or longitudinal wording ("practise", "should", "work on", "consistently", "persistent", "usually",
"again", "over time", "improving", "mispronounced", "%" …). The heading "Major improvement areas" is a fixed
UI label, not a claim.

**Calibration** `m9-read-cal.3` (separate from the coaching calibration `m9-cal.3`, unchanged):

| Parameter | Value |
|---|---|
| Path A | ≥ 3 clear, ≥ 2 words, ≥ 2 sentences, concentration ≥ 0.5 (word-specific: ≥ 2 clear of one word, ≥ 2 sentences); context minimums 4 / 4 |
| Path B | ≥ 3 observations, ≥ 2 clear, ≥ 2 sentences, ≥ 2 real words, direction ≥ 0.5 |
| clear-evidence rate | ≥ 0.10; bands: high ≥ 0.40, moderate ≥ 0.20 |
| Path C | ≥ 3 words |
| areas / strengths shown | no limit / ≤ 3 |
| strengths | ≥ 10 occurrences, ≥ 90 % heard as expected, < 2 differences |
| minimum sentences | 2 |
| fluency | ≥ 2 noticed observations |

## M10 boundary

M9 never compares windows, computes change, or words anything as progress; the validator rejects "improv",
"progress" and "better than". The stored `coaching.json` per summary records what was advised when, for M10
to evaluate.

## Known limitations

* **No ground truth of usefulness:** whether practising a selected target helps is unmeasured (M10).
* **Thin data:** half of the stored history already selects differently for sound targets. Recommendations
  firm up with more readings.
* **Guidance:** only 12 contrasts have stored guidance. The front-vowel family falls back to listen-and-compare,
  because /ɛ/–/eɪ/, /eɪ/–/ɪ/ and /ɛ/–/ɪ/ have no guidance yet. Adding guidance is a reviewed knowledge change.
* **Plausibility is class-level** (vowel vs consonant). Odd within-class pairs (e.g. /d/–/ɹ/, possibly a tapped
  r decoded as /d/) are not filtered.
* **Unstressed-syllable reductions not caught by M5** (e.g. /eɪ/ heard as /ə/) may appear as contrasts. There
  is no observed stress to tell them apart (M6).
* **Fluency evidence** only comes from recordings with a measurable speech level, which is about a third of
  the stored history.
* **Engine disagreement** can't be calibrated: comparison jobs exist for 1 of 74 attempts.
