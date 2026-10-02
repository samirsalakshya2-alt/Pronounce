# M2 Benchmark Report — Personal Pronunciation Lab

Benchmark run: `m2-20261002` · code baseline: `17a0d3e` (M1) + uncommitted M2 work · date: 2026-10-02

This report describes **recogniser evidence**, not pronunciation verdicts.
There is no human annotation of any recording, so nothing here is ground
truth. No engine is ranked and no overall score exists. Every interpretation
is labelled as a *candidate* and states the rule that produced it. Nothing
generalises beyond this one speaker and these 20 recordings.

Terms used throughout:

* **decoded as X** — the recogniser's top phone over that span was X.
* **not decoded** — no recognised phone was aligned to the expected phone.
  This is *not* the same as acoustically absent (§6.4).
* **P(X)** — peak posterior of X over the phone's frame span, recomputed from
  the stored posteriorgram.
* **plausible** — expected-phone posterior ≥ 0.05.
* **one observation** — OpenPronounce and raw Wav2Vec2 run the same
  checkpoint; their frame posteriors are bit-identical in all 20 recordings
  (§3). Where they agree, that is one observation, not two.

---

## 1. Objective

Run the M1 six-engine evidence layer over the full 20-recording benchmark,
through a resumable, validated runner; preserve raw and normalized evidence and
timing for every cell; represent every cell that could not run with its real
state; and produce task-level, machine-readable analysis for M3.

## 2. Dataset

| Group | Recordings | Text | Styles (manifest) | Purpose |
|---|---|---|---|---|
| think_three | R01–R04 | Think about the three things that you want to change. | slow · normal · fast · **deliberate /θ/→/t/** | /θ/, /ð/, intended substitution |
| very_few_people | R05–R07 | Very few people would value the view from this valley. | slow · normal · fast | /v/ vs /w/ |
| ship_will_leave | R08–R10 | The ship will leave the harbor before the evening begins. | slow · normal · fast | /ɪ/ vs /iː/ |
| world_has_changed | R11–R13 | The world has changed significantly over the last six months. | slow · normal · fast | /r/, clusters |
| i_would_like | R14–R16 | I would like to understand exactly what happened during the meeting. | slow · normal · fast | boundaries, function words, stress |
| company_planning | R17–R18 | The company is planning to improve its supply chain, reduce transportation costs, and increase customer service levels. | normal · fast (**no slow**) | long sentence, reductions |
| although_initial | R19–R20 | Although the initial results were encouraging, we still need to examine the underlying data before making a final decision. | slow · normal (**no fast**) | clause stress, long-form |

* 20 WAVs, all 16 kHz mono PCM_16, 162.6 s total (4.1–16.5 s each), prepared
  from the iPhone M4A originals by `scripts/prepare_benchmark_audio.py`.
* Preflight: **0 problems, 0 warnings.** Every WAV is 64 ms shorter than its
  M4A (AAC priming/padding), within the 0.1 s reproducibility tolerance.
* SHA-256 of the manifest, all 20 WAVs and all 20 M4As recorded at run start
  and re-verified after all runs: **unchanged**.
* Single speaker. **No human phonetic annotation; no ground truth.** The
  manifest records what each reading *intended*; whether an intention was
  realised acoustically is not known.

## 3. Engine availability and independence

| Engine | State | Reason | Cells |
|---|---|---|---|
| OpenPronounce | runnable | — | 20 executed |
| Raw Wav2Vec2 (`facebook/wav2vec2-lv-60-espeak-cv-ft@ae45363`) | runnable | — | 20 executed |
| WavLM | **unresolved** | `unresolved_engine` — no defensible adult-speech checkpoint; Jianshu001 scorer not adopted (owner decision) | 20 recorded, not executed |
| Azure Pronunciation Assessment | **blocked** | `credentials_unavailable` (`AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`) | 20 recorded, not executed |
| SpeechSuper | **blocked** | `credentials_unavailable` (`SPEECHSUPER_APP_KEY`, `SPEECHSUPER_SECRET_KEY`) | 20 recorded, not executed |
| Speechace | **blocked** | `credentials_unavailable` (`SPEECHACE_API_KEY`) | 20 recorded, not executed |

* Availability is decided by each engine's own `readiness()`; blocked and
  unresolved cells are never executed. No cloud call was made and no provider
  field was populated.
* **The two runnable engines are one acoustic model.** Their stored
  posteriorgrams are bit-identical (max |Δ| = 0) in all 20 recordings, with
  identical 392-token vocabularies. Every difference between them comes from
  post-processing: OpenPronounce merges phones and collapses repeats; raw
  Wav2Vec2 does neither. **Agreement between them is never independent
  confirmation.**
* OpenPronounce records its model as `facebook/wav2vec2-lv-60-espeak-cv-ft`
  without a revision; it loaded the cached `main` (= `ae45363`), as the
  bit-identical posteriors show, but its revision is not pinned (§13).

## 4. Execution summary

| | Count |
|---|---|
| Nominal cells (20 × 6) | 120 |
| Executed (real local evaluations) | 40 |
| Recorded without execution | 80 |
| **ok** | **40** |
| partial | 0 |
| failed | 0 |
| **blocked** | **60** |
| **unresolved** | **20** |
| missing / corrupt | 0 / 0 |

Counts verified directly from the 120 cell files: 120 complete, 120 unique
(recording, engine) pairs, 40 raw files for the 40 evidence cells. Full
benchmark wall time: **24.3 s** (one session). A later resume executed 0
cells and skipped 120. Run-level validation: **ok**, 0 issues.

## 5. Performance

One Apple Silicon machine, CPU (`device=cpu`), one session. Engines ran in
registry order: OpenPronounce first, then raw Wav2Vec2, in one process.

| | OpenPronounce | Raw Wav2Vec2 |
|---|---|---|
| Cold run (R01): model load | 3089 ms | 2021 ms |
| Cold run (R01): inference | 322 ms | 318 ms |
| Warm runs (n = 19): inference, median [min–max] | 280 ms [173–777] | 300 ms [174–693] |
| Warm: postprocessing, median | 21 ms | 87 ms |
| Warm: wall time, median | 302 ms | 384 ms |
| Warm: real-time factor, median [min–max] | 0.045 [0.043–0.053] | 0.055 [0.049–0.062] |
| Warm: inference per second of audio, median | 41.8 ms | 42.0 ms |

* *Cold* = the first cell of an engine in a session (model load reported
  separately, including a warm-up forward pass); *warm* = every later cell.
* Real-time factor = engine wall time ÷ audio duration (verified exact for
  all 40 cells).
* **The two model-load figures are not comparable.** OpenPronounce ran first
  and its load includes one-time framework initialisation in the process; raw
  Wav2Vec2 loaded afterwards. Inference is the same network for both.
* The postprocessing difference is espeak G2P (~60 ms, raw only) plus
  normalization work.
* Inference per audio second stayed within 40–47 ms over 4.1–16.5 s clips.
  **No claim is made for other lengths, machines, devices or loads.**
* No cloud latency exists: every cloud cell is blocked.

## 6. Task-specific findings

### 6.1 /θ/ and /ð/ (R01–R04)

| Recording | θ/ð targets | decoded as expected | decoded [t]/[d] | not decoded |
|---|---|---|---|---|
| R01 slow | 5 | 4 | 0 | 1 ("that" — whole word) |
| R02 normal | 5 | 5 | 0 | 0 |
| R03 fast | 5 | 3 | 1 ("three") | 1 ("that") |
| R04 deliberate /θ/→/t/ | 5 | 5 | 0 | 0 |

P(t) over each /θ/ span (identical in both engines — one observation):

| | think | three | things |
|---|---|---|---|
| R01 slow | 0.07 | 0.06 | 0.11 |
| R02 normal | 0.16 | 0.02 | 0.03 |
| R03 fast | 0.03 | **0.70** (P(θ) = 0.19) | 0.01 |
| R04 deliberate | 0.12 | 0.15 | 0.03 |

* **In R04, which the manifest records as read with a deliberate /θ/→/t/,
  the recogniser decoded /θ/ for all three /θ/ targets** (P(θ) 0.73–0.91).
  R04's P(t) values (0.03–0.15) **lie within the range seen in the ordinary
  readings R01–R02 (0.02–0.16)**: by this measure R04 is not distinguishable
  from them.
* What this does and does not mean: it is a recogniser observation. It does
  **not** establish that the speaker failed to produce [t], nor that the
  recogniser missed a produced [t]. Without listening or annotation the two
  cannot be separated. Possible explanations include a realisation that was
  not stop-like, and a recogniser bias toward canonical phones (its training
  labels came from G2P of transcripts); **no cause is asserted**.
* The only /θ/ decoded [t] in the benchmark is "three" in R03 (fast). No
  causal link between speaking rate and that decode is inferred from one token.
* /ð/ was decoded /ð/ wherever "the"/"that" were decoded (P(ð) ≥ 0.97).
* "that" was not decoded in R01 and R03 (P(ð), P(æ), P(t) ≈ 0 over the
  inferred region) but was in R02 and R04 — a whole-word non-decode, not a
  /ð/ realisation.

### 6.2 /v/ vs /w/ (all recordings)

* All /v/ targets ("very", "value", "view", "valley") were decoded /v/.
* Of the **23 /w/ targets** in the benchmark, **3 were decoded [v]**:

  | Recording | word | P(v) | P(w) |
  |---|---|---|---|
  | R05 slow | would | 0.86 | 0.02 |
  | R07 fast | would | 0.87 | 0.03 |
  | R11 slow | world | 0.97 | 0.01 |

  18 were decoded /w/ — including "would" in R06 (P(w) = 0.84) and in R14–R16,
  and "world" in R12–R13. 2 were not decoded ("will" R08, "were" R19); over
  their inferred regions P(v) exceeds P(w) (0.43 vs 0.12; 0.34 vs 0.16), which
  is weak evidence because those regions are derived, not decoded.
* The three [v] decodes are confident by the recogniser's own posteriors. They
  are acoustic/recogniser observations, not pronunciation judgements, and
  three tokens with no consistent style pattern are **not enough to call this
  a stable property of the speaker**.

### 6.3 /ɪ/ vs /iː/ (R08–R10)

Raw Wav2Vec2 (OpenPronounce merges length and cannot express the "length only" column):

| Recording | targets | as expected | length only | crossed /ɪ/↔/iː/ | other | alignment-suspect |
|---|---|---|---|---|---|---|
| R08 slow | 9 | 3 | 0 | 1 ("will" ɪ→iː, P(iː) = 0.77) | 1 ("ship" ɪ→eɪ, P(eɪ) = 0.90) | 4 |
| R09 normal | 9 | 6 | 1 | 1 ("evening" iː→ɪ, P(ɪ) 0.54 / P(iː) 0.36) | 1 ("the" ɪ→ə) | 0 |
| R10 fast | 9 | 6 | 2 | 1 ("evening" iː→ɪ, P(ɪ) 0.68 / P(iː) 0.22) | 0 | 0 |

* "evening" /iː/→[ɪ] in R09 and R10 with /iː/ still plausible: an
  **ambiguous** crossing, not a confident one.
* **R08 alignment artifact.** R08's decoded tail is
  `ð ə | ɪ v ɪ n ɪ ŋ | b ɪ ɡ ɪ n ɪ ŋ | b ɪ ɡ ɪ n z` (8.4–10.7 s): one more
  "b ɪ ɡ ɪ n ɪ ŋ" than the target text predicts. *What the speaker did there
  is not verified* (it may be a repetition or restart). The unit-cost
  alignment cannot place the extra material: it attaches the decoded
  "ɪ v ɪ n ɪ ŋ" to "the" as 8 insertions and aligns "evening" onto the extra
  "ɡ ɪ n ɪ ŋ", producing /iː/→[ɡ] and /v/→[ɪ]. **These are artifacts of the
  alignment, not properties of the speaker's pronunciation.** "the",
  "evening" and "before" are flagged `alignment_suspect` and excluded from
  every interpretation and count (rule: a word with ≥ 3 insertions and both
  its neighbours). R08's phone error rate (0.44 — edit distance to the eSpeak reference, not
  a pronunciation measure) is dominated by this artifact.

### 6.4 /r/ and clusters (R11–R13)

* All /ɹ/ and r-coloured targets were decoded as expected.
* Word-final cluster consonants not decoded:

  | | R11 slow | R12 normal | R13 fast |
  |---|---|---|---|
  | "months" final /s/ | not decoded, P(s) 0.00 | not decoded, P(s) 0.22 | not decoded, P(s) 0.00 |
  | "changed" final /d/ | not decoded, P(d) 0.04 | decoded /d/ (P 0.47) — but /dʒ/ not decoded | not decoded, P(d) 0.14 |
  | "world" final /d/ | not decoded, P(d) 0.09 | decoded /d/ (P 0.91) — but /l/ not decoded | not decoded, P(d) 0.00 |

* Only "months" /s/ is not decoded in all three styles. It is sentence-final:
  its inferred region (0.5–1.2 s) runs into trailing silence, which dilutes
  any acoustic measurement there.
* **"Not decoded" is not "acoustically absent".** In R13 "changed" /d/, the
  inferred region shows high zero-crossing rate (0.50) and spectral centroid
  (4.7 kHz) — fricative/burst-like energy that no phone was decoded for. The
  evidence supports "the recogniser did not output the expected phone", not
  "the sound was not produced".

### 6.5 Function words and boundaries (R14–R16)

* Function words were decoded as expected except "would" /ʊ/→[uː] (R14),
  "during" /ʊɹ/→[uː] in all three styles, and "during" /ɪ/ not decoded (R14).
* The expected pronunciation comes from **eSpeak `en-us`** (/dʊɹɪŋ/). The
  stable [uː] decode (P(uː) 0.26–0.56, P(ʊɹ) ≤ 0.06) may reflect a difference
  between that reference and the speaker's accent; it should **not be read as
  an error without an accent-appropriate reference**.
* In R16, "understand" carries 3 inserted phones; it and its neighbours "to"
  and "exactly" are alignment-suspect and excluded.

### 6.6 Long connected speech (R17–R20)

Raw Wav2Vec2:

| Recording | style | expected | as expected | other | not decoded |
|---|---|---|---|---|---|
| R17 | normal | 84 | 78 | 6 | 0 |
| R18 | fast | 84 | 67 | 14 | 3 |
| R19 | slow | 83 | 68 | 14 | 1 |
| R20 | normal | 83 | 73 | 10 | 0 |

* R18 (fast) has 17 deviations (substituted + not decoded) vs R17's 6 (same text). Reduction candidates
  (rule in §7): "and" final /d/ and "costs" final /s/ not decoded only in fast.
* Deviations identical in both readings: "increase" /ŋ/→[n], /k/→[ɡ],
  /s/→[z]; "reduce" /d/→[j]; "levels" /əl/→[l].
* R19 (slow) has 15 deviations vs R20 (normal) 10 (same measure): the slow
  reading is not "cleaner" by this measure.

### 6.7 Cross-cutting counts (all 20 recordings)

Alignment-suspect words (R07 "very/few/people", R08 "before/the/evening",
R16 "understand/to/exactly") are excluded:

| | OpenPronounce | Raw Wav2Vec2 |
|---|---|---|
| Expected phonemes | 941 | 945 |
| excluded (alignment-suspect) | 41 | 41 |
| decoded as expected | 804 | 804 |
| substitution | 61 | 76 |
| not decoded | 35 | 24 |
| insertion | 14 | 17 |

* Most frequent substitutions (raw): ᵻ→ɪ 6, ɪ→ə 5, ɔ→ʌ 4, ŋ→n 4, w→v 3, ɐ→æ 3,
  k→ɡ 3, ʊɹ→uː 3. Not-decoded phones: 13 of 24 are word-final and 12 of 24
  are /t d s/ (11 both) — raw; OpenPronounce 21 / 20 / 18 of 35.
* The engines' counts differ **only** through post-processing of identical
  posteriors (phone merging, length removal, repeat collapse) — never because
  one "heard" something the other did not. Example: in "want to"
  (R01–R04), raw decodes two separate /t/ runs (R01: frames 286–287 and
  307–309); OpenPronounce's repeat-collapse merges them into one phone
  (R01: frames 286–309, 460 ms; R02–R04: 80–100 ms), assigns it to "to", and
  reports "want" /t/ as not decoded in all four readings. Both versions are
  preserved as produced; neither is corrected.

## 7. Slow / normal / fast

For each same-text group the analysis compares the same expected phone across
styles and labels every deviating position by a stated rule
(`style_comparison.json`). Rules:

* **reference style** = the slowest *available* style in the group: slow for
  five groups; **normal for company_planning** (no slow reading).
  although_initial has no fast reading. R04 (deliberate) is excluded from the
  speed ordering but included when checking whether a deviation is
  consistent across all readings.
* **consistent_deviation_candidate** — the same deviation in every reading.
* **connected_speech_reduction_candidate** — decoded as expected in the
  reference style, not decoded or substituted only in faster style(s), on a
  function word, word-final consonant or unstressed vowel. "Unstressed" is
  used only where the engine reports expected stress (raw); OpenPronounce
  reports none, so its vowels never qualify by that clause.
* **ambiguous** — the deviating decode has N-best margin < 0.2 or the expected
  phone remains plausible.
* **alignment_suspect** — not interpreted.

| Label | OpenPronounce | Raw Wav2Vec2 |
|---|---|---|
| consistent_deviation_candidate | 13 | 15 |
| connected_speech_reduction_candidate | 3 | 4 |
| ambiguous | 28 | 31 |
| style_specific_deviation | 15 | 14 |
| alignment_suspect | 8 | 9 |

These labels are **rule outputs, not findings about the speaker**; none has
been checked against listening or annotation.

Observations within this speaker:

* **"Slow" readings contain far more pauses; articulation rate between
  pauses changes much less.** Pauses ≥ 250 ms: R08 13 / R09 2 / R10 0;
  R11 9 / R12 3 / R13 1. Decoded phones per second excluding pauses range
  8.4–15.7 across all recordings and both engines, without a consistent
  slow < normal < fast ordering.
* Fast readings compress the speech span (R01 5.9 s → R03 2.8 s;
  R17 13.3 s → R18 6.7 s), but **do not consistently add deviations**.
  Substituted + not decoded + inserted (raw, alignment-suspect words
  excluded), slowest → fastest: R01 7 / R02 2 / R03 6; R05 3 / R06 2 /
  R07 1; R08 3 / R09 4 / R10 4; R11 5 / R12 5 / R13 7; R14 3 / R15 1 / R16 2;
  R17 8 / R18 23. Only R13 and R18 exceed every slower reading of their text.
  Of the 40 positions where a fast reading deviates (raw), 13 are labelled
  ambiguous.
* Reduction candidates are few (raw: "has" /z/, "happened" /d/, "costs" /s/,
  "and" /d/) and are all word-final consonants or function words. A reduction candidate is **not treated as an error**;
  whether it is natural connected speech is not decided here.
* Several consistent deviations are where the speaker's decode differs
  stably from the **eSpeak `en-us`** reference — "want" /ɔ/→[ʌ] in all four
  readings (P(ʌ) 0.34–0.61, P(ɔ) ≤ 0.10), "during" /ʊɹ/→[uː], "has" /ɐ/→[æ].
  These may be reference-accent differences; that is a hypothesis for M3, not
  a conclusion.

## 8. Uncertainty

Denominators exclude alignment-suspect words.

| | OpenPronounce | Raw Wav2Vec2 |
|---|---|---|
| Median confidence: decoded as expected / substituted | 0.94 / 0.66 | 0.94 / 0.63 |
| Median N-best margin: decoded as expected / substituted | 0.92 / 0.51 | 0.91 / 0.45 |
| Ambiguous decodes (margin < 0.2) / all decoded phones | 41 / 865 | 53 / 880 |
| Substitutions with expected-phone posterior ≥ 0.05 | **32 / 61 (52%)** | **44 / 76 (58%)** |
|   … ≥ 0.10 | 21 / 61 (34%) | 35 / 76 (46%) |
|   … ≥ 0.20 | 12 / 61 (20%) | 22 / 76 (29%) |
| Not-decoded phones with expected posterior ≥ 0.05 | 15 / 35 | 9 / 24 |

* At the 0.05 plausibility threshold (OpenPronounce's own constant), **about
  half of all substitutions (52% / 58%) leave the expected phone plausible**.
  The proportion depends on the threshold, as the table shows; a substitution
  label alone is weak evidence.
* Confident substitutions exist (e.g. "would" [v], P = 0.86; "ship" [eɪ],
  P = 0.90) and are the more informative observations.
* Lexical stress (raw only; *expected* stress from eSpeak): primary-stressed
  vowels (n = 132) have median relative energy 1.54 vs 1.35 for unstressed
  (n = 194), with the same median F0 (113 vs 112 Hz). A weak correlate over CTC
  spans; **no engine observes stress or prosody**.

## 9. Repeatability

* `cli repeat` on R01, R03, R04, R08, R13, R16, R18, R19 for both runnable
  engines: 16 cells, 32 reruns (one cold on a fresh engine, one warm),
  compared with the stored cells over the entire result except `processing`,
  and raw arrays bit-for-bit. **32/32 identical.**
* An independent second full run (all 120 cells, separate directory):
  **40/40 evidence cells identical** including raw arrays; 120/120 statuses
  and error types identical.
* Only wall-clock timings differ. Repeatability shows the *pipeline* is
  deterministic; it says nothing about the speaker's consistency.

## 10. Failure-path validation

The runner is tested with controllable fake engines and synthetic datasets
for: missing / malformed / undecodable manifest, missing columns, malformed
rows, duplicate and unexpected IDs, missing recordings, missing / zero-byte /
unreadable / wrong-rate / wrong-channel / wrong-subtype / zero-duration WAVs,
missing sources, empty / whitespace target text, inconsistent same-text
groups, unknown engine (API and CLI), engine that cannot be constructed,
blocked engine, unresolved engine, engine exception, malformed engine result,
result describing another recording, stale or mismatched raw evidence,
tampered or deleted raw files, missing timing fields, interrupted write (crash
during the atomic rename), Ctrl-C mid-run and resume, truncated /
non-envelope / schema-invalid / checksum-mismatched cells, a valid cell under
the wrong name, stray / duplicate / temp files, retry of failed cells,
skipping completed cells, forced rerun, a blocked engine becoming available,
refusal to resume over changed source data or a foreign run directory,
modified source audio after a run, and malformed task-analysis input.

## 11. Data-integrity validation

Enforced per cell before every write (`validate_cell`) and per run
(`validate_run`): one cell per (recording, engine); every nominal cell has a
state; evidence cells have results, words, performance and consistent timing
(parts ≤ wall, model load iff cold, RTF = wall/duration, runner clock ≥
engine clock); failed cells have structured errors; blocked cells have a
reason and no evidence; unresolved cells have the unresolved reason; result
recording, text, engine and input file match the cell; local results carry
model identity; raw arrays reproduce the result's phones, spans, confidences
and posteriorgram shape; manifest fields match; no stray files; source data
byte-identical to run start. Cell files carry a SHA-256 of their content and
of their raw file; both checks are covered by tests and by fault injection.

## 12. Validation summary

Final pass, 2026-10-02, after the pre-freeze review corrections.

**Tests** (`pytest tests/benchmark tests/phase0`, microphone test deselected):

| | Count |
|---|---|
| Collected | 259 |
| Passed | **258** |
| Failed | 0 |
| Skipped | 0 |
| Deselected | 1 (`test_audio_record`: records from the microphone) |
| Warnings | 1 — transformers `FutureWarning` raised inside the M1 missing-model test while loading a deliberately non-existent revision; no effect on results |

133 are the M1/phase-0 tests (all passing, none modified for M2); the rest
are M2 tests covering preflight, runner, cell/raw invariants, analysis rules,
real-engine runs on R01–R04, acceptance on the real 120-cell run, and
consistency of this report and the handoff with the stored results (every
quantitative claim checked against the cells and raw arrays).

**Fault injection** — 27 defects injected one at a time, full suite run each,
source restored and verified byte-identical afterwards. The final pass stalled
once (during the 25th defect, not reproducible: that defect is caught in under
a second by `test_ctc.py`, and every test file completes normally with it
applied); the stalled run was stopped, the defect it left in `ctc.py` was
detected by checksum and reverted, and the last three defects were rerun with
a per-run time limit:

| | |
|---|---|
| M2 defects (store, runner, preflight, validator, analysis, repeatability) | 19 / 19 caught |
| M1 defects re-run as regression | 8 / 8 caught |

Defects that initially survived were all fixed in the test suite before
completion: tampered/deleted raw-evidence files; the reduction rule's
reference style when the slowest reading is *normal* (a redundant clause that
masked it was removed). The pre-freeze review found two analysis defects,
both fixed with regression tests and added to the fault-injection set:
alignment suspicion not reaching the word next to an insertion pile (R08
"evening"), and unknown stress treated as "unstressed" for OpenPronounce
(which mislabelled two of its positions as reduction candidates). It also
corrected several report statements; every quantitative claim in this report
and the handoff (153) was then recomputed independently of the analysis code
from the cells and raw arrays, with 0 mismatches.

**Benchmark checks**

| Check | Result |
|---|---|
| Preflight | ok — 20 recordings, 0 problems, 0 warnings, 41 files hashed |
| Run validation (`cli validate`) | ok — 120/120 cells, 0 issues |
| Resume of the completed run | 120 skipped, 0 executed |
| Repeatability (`cli repeat`, 8 recordings × 2 engines × 2 reruns) | 32/32 identical |
| Independent second full run (all 120 cells) | 40/40 evidence cells identical incl. raw arrays; 120/120 statuses and error types identical |
| Source data after all runs | manifest, 20 WAVs, 20 M4As byte-identical to run start |
| Personal data in Git | none tracked; `data/benchmark_results/` gitignored |
| Secrets | none written (blocked cells record env-var *names* only) |

## 13. Known limitations

* **No ground truth.** Every finding is a recogniser observation on one
  speaker; nothing is a pronunciation judgement.
* **One acoustic model.** The two runnable engines share the checkpoint and
  posteriors; four of six engines produced no evidence.
* The recording read with an intended /θ/→/t/ (R04) is not distinguishable
  from ordinary readings by P(t) (§6.1); the cause is unknown.
* eSpeak `en-us` is the only expected-pronunciation reference; stable
  differences from it surface as "deviations".
* Unit-cost alignment produces artifacts around unexpected decoded material
  (R08, R07, R16); the `alignment_suspect` rule (≥ 3 insertions, plus
  neighbours) catches the cases seen here but is a heuristic.
* Not-decoded regions are inferred from neighbouring phones and can be long
  (up to 1.2 s); posteriors and acoustics over them are diluted.
* CTC spans are not durations; no duration, "swallowed" or "chewed" claim is
  possible from this evidence.
* Interpretation labels are rule-based candidates with fixed thresholds
  (plausible 0.05, ambiguous margin 0.2, pause 250 ms, suspect insertions 3),
  not tuned and not validated against annotation.
* OpenPronounce's model revision is not pinned in its own result (it follows
  the cache's `main`).
* Performance is one CPU machine, one session, 4–17 s clips; cold-start costs
  depend on engine order.

## 14. Questions for M3

See `docs/M2_TO_M3_HANDOFF.md`.

## 15. Reproducing

```bash
# prepare WAVs from the local M4A originals (only if needed)
uv run python scripts/prepare_benchmark_audio.py

# preflight, run (resumable), validate, analyse, repeatability
PYTHONPATH=src uv run python -m pronunciation_lab.benchmark.cli preflight
PYTHONPATH=src uv run python -m pronunciation_lab.benchmark.cli run      --run-id m2-20261002
PYTHONPATH=src uv run python -m pronunciation_lab.benchmark.cli validate --run-id m2-20261002
PYTHONPATH=src uv run python -m pronunciation_lab.benchmark.cli analyze  --run-id m2-20261002
PYTHONPATH=src uv run python -m pronunciation_lab.benchmark.cli repeat   --run-id m2-20261002 \
    --recordings R01 R03 R04 R08 R13 R16 R18 R19

# resume options: --retry-failed reruns failed cells; --force reruns everything

# tests (never include tests/manual: it records from the microphone on import)
uv run pytest tests/benchmark tests/phase0 \
    --deselect tests/phase0/test_audio_record.py::test_audio_record
```

Outputs (gitignored, personal data): `data/benchmark_results/runs/m2-20261002/`
— `run_metadata.json`, `manifest_snapshot.csv`, `cells/*.json` (120),
`raw/*.npz` (40 posteriorgrams), `summaries/*.json`.
