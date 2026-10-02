# M2 → M3 Handoff

What M2 produced, what it did and did not establish, and what M3 has to decide.
M2 makes **no architecture decision** and does not freeze either product.
Every observation below is a recogniser observation on one speaker with no
human annotation — not a pronunciation judgement and not ground truth.

## 1. Evidence that exists

Run `m2-20261002` (local, gitignored: `data/benchmark_results/runs/m2-20261002/`).

| Artifact | Content |
|---|---|
| `cells/<R>__<engine>.json` (120) | One per nominal cell: status, engine identity, performance, the full M1 `PronunciationResult` (evidence cells), errors, readiness (blocked/unresolved), validation, SHA-256 |
| `raw/<R>__<engine>.npz` (40) | Posteriorgram (frames × 392 vocab), vocab, decoded phones, confidences, frame spans — enough to recompute every N-best and posterior without rerunning a model |
| `summaries/matrix.json` | 6 × 20 state matrix, exclusions |
| `summaries/task_analysis.json` | Per task × engine × recording: every target occurrence with expected/decoded, outcome, posterior, competitors, N-best, margin, timing, acoustics |
| `summaries/phenomena.json` | Per engine: counts, substitution/omission/insertion tables, uncertainty, expected-stress acoustic correlates, per-recording measurements |
| `summaries/style_comparison.json` | Same text across styles: every deviating position with a rule-based label |
| `summaries/cross_engine.json` | Shared vs only-in-one spans, per-word disagreements |
| `summaries/word_diagnosis.json` | Per word occurrence, per engine: operations, provider flag, alignment-suspect |
| `summaries/performance.json` | Cold/warm timings, per recording, environment |
| `summaries/repeatability.json` | 16 cells × 2 reruns, all identical |
| `summaries/validation.json`, `preflight.json` | Integrity and dataset checks |

Code: `pronunciation_lab.benchmark.{dataset,cells,runner,validate,analysis,repeatability,cli}`.
Full numbers and rules: `docs/M2_BENCHMARK_REPORT.md`.

## 2. What was actually tested

| Task | Recordings | Evidence from |
|---|---|---|
| /θ/ /ð/, intended /θ/→/t/ | R01–R04 | one acoustic model, two post-processings |
| /v/ vs /w/ | R05–R07 (+ every /w/ in the set) | same |
| /ɪ/ vs /iː/ | R08–R10 | same (only raw keeps length) |
| /r/ and clusters | R11–R13 | same |
| function words, boundaries | R14–R16 | same |
| long connected speech | R17–R20 | same |
| lexical stress | all | raw only: *expected* stress (eSpeak) + acoustic correlates |
| prosody | — | **no engine** |
| runtime | all | both (CPU, one machine) |

## 3. Engine status

* **Runnable:** OpenPronounce and raw Wav2Vec2 — **one acoustic model**
  (`facebook/wav2vec2-lv-60-espeak-cv-ft`); posteriorgrams bit-identical in all
  20 recordings. Their agreement is a single observation. OpenPronounce's
  revision is not pinned in its own results (it follows the cache's `main`).
* **Blocked (credentials):** Azure, SpeechSuper, Speechace — 60 cells.
  Boundaries exist; no request or response mapping until a real response is
  inspected.
* **Unresolved:** WavLM — 20 cells. Jianshu001 children's scorer not adopted.

## 4. Cross-engine disagreements (all from post-processing of identical posteriors)

1. **"want to"** (R01–R04): raw decodes two /t/ runs; OpenPronounce's
   repeat-collapse merges them into one phone (R01: 460 ms; R02–R04:
   80–100 ms), assigns it to "to", and reports "want" /t/ as not decoded in all
   four readings. Preserved as two different word diagnoses.
2. **Length** (/iː/ vs /i/, /uː/ vs /u/): invisible to OpenPronounce by
   design; raw reports them as substitutions.
3. **Merged vowels** (ɔ→ɑ etc.) change which positions are substitutions.

Totals: 918 shared spans, 18 only in OpenPronounce, 36 only in raw; 37 word
occurrences where operations or timing differ.

## 5. Recurring observations (this speaker, this dataset)

| Observation | Where | Strength of evidence |
|---|---|---|
| /w/ decoded [v] | 3 of 23 /w/ targets: "would" R05, R07; "world" R11 | confident decodes (P(v) 0.86–0.97); not consistent across styles |
| word-final consonant not decoded | "months" /s/ in all three styles; "changed" /d/ and "world" /d/ in R11, R13 | P 0.00–0.22; *not decoded ≠ absent* (R13 "changed" region has fricative-like energy) |
| stable difference from eSpeak `en-us` | "want" /ɔ/→[ʌ] (4/4 readings); "during" /ʊɹ/→[uː] (3/3) | may be a reference-accent difference — hypothesis only |
| /ŋ/→[n], /k/→[ɡ], /s/→[z] | "increase", R17 & R18 | identical in both readings |
| /iː/→[ɪ] | "evening", R09, R10 | ambiguous (P(iː) 0.22–0.36) |
| more pauses in "slow" readings | all groups (e.g. 13 vs 0 pauses, R08 vs R10) | articulation rate excluding pauses shows no consistent ordering |
| deviations do **not** rise consistently with speed | only R13 and R18 exceed every slower reading of their text; R03, R07, R16 do not | counts in report §7 |

## 6. Evidence gaps

1. **No ground truth.** No listening verification or phonetic annotation of
   any recording.
2. **No independent second model.** Every agreement between the runnable
   engines is one observation.
3. **R04 (intended /θ/→/t/) is indistinguishable from ordinary readings by
   P(t)** (0.03–0.15 vs 0.02–0.16 in R01–R02). Whether [t] was produced is
   unknown.
4. **No observed stress, prosody, intonation or rhythm** from any engine.
5. **No phone duration**: CTC spans are posterior peaks.
6. **One reference accent** (eSpeak `en-us`): stable differences from it look
   like deviations.
7. **Alignment artifacts** where the decode contains unexpected material
   (R08 tail, R07, R16); handled by a heuristic exclusion rule.
8. **"Not decoded" regions are inferred**, sometimes over 1 s and into
   silence; their posteriors and acoustics are diluted.
9. **One speaker, 7 sentences**: nothing generalises; no claim about Indian
   English is supported.
10. **Cloud engines and WavLM**: zero evidence; their value is untested.

## 7. Questions for M3

1. Both runnable engines are one model. Is a second, *independent* acoustic
   evidence source required before any product decision — and which (cloud
   credentials, an adult WavLM/HuBERT phoneme model, forced alignment)?
2. Before relying on any observation in §5: can the key items be verified by
   listening (R04 vs R03 "three", the three [v] tokens, "months" /s/, the R08
   tail)? This is the cheapest available ground truth.
3. Why is R04 not distinguishable from R01–R02 by P(t)? Test a forced-choice
   /θ/ vs /t/ comparison against the canonical decode; examine whether
   text-agnostic decoding favours canonical phones.
4. Should the expected-pronunciation reference be accent-aware (e.g. eSpeak
   `en-in`, multiple accepted variants), so stable accent features stop
   appearing as deviations?
5. Should cross-word repeat-collapse and length removal (OpenPronounce
   normalization) be disallowed in any product path, given the "want to"
   effect on word diagnosis and the loss of the /ɪ/–/iː/ length contrast?
6. Is unit-cost string alignment adequate, or does M3 need posterior-based
   forced alignment (scoring the expected phone at its location), which would
   also remove R08-type artifacts and the need for the suspect heuristic?
7. What threshold structure should separate *confident deviation*,
   *ambiguous* and *reduction* — and how will it be validated without
   annotation (e.g. a small hand-labelled subset)?
8. Which product needs which evidence: a learner-facing tool cannot surface
   ambiguous substitutions as errors; a research view can. How should
   uncertainty be presented?
9. Is a human-annotated subset (e.g. the θ/ð/v/w/ɪ/iː targets) the next
   highest-value investment, and is more data (speakers, sentences) needed
   before any decision?
10. Are the word-final non-decodes a speaker pattern or a recogniser pattern?
    (Needs an independent model or annotation.)
11. Does "slow" need redefining for the product (pauses vs articulation rate)?
12. Must every engine pin its model revision in its own result before M3
    evidence is relied on (OpenPronounce currently does not)?
