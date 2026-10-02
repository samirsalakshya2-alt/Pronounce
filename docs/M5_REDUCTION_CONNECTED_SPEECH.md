# M5 — Reduction & Connected-Speech Coach

M5 begins to answer *"What sound am I actually chewing/swallowing, where exactly
did it happen, and can I hear that exact part of my recording?"* — without
labelling short or quiet sounds "chewed" or "swallowed". It is an evidence and
interpretation layer over the M4 observations: no new model, no extra inference
for one engine; the optional engine comparison runs the second local engine on
the same audio.

**The microphone cannot establish articulatory truth.** Everything here is about
what the recogniser and the acoustic measurements show. A "possible reduction"
means the evidence is *consistent with* a reduction; it never establishes that
a sound was weakened, merged or left out, and it is never an error.

## What you can do

1. Analyse a recording (Record / Upload / Benchmark) → Full Recording Feedback →
   Phoneme Coach (all unchanged) → **Reduction & Connected Speech**.
2. Each candidate card shows:
   * **what** — a neutral label (Possible omission / weakening / compression /
     substitution / coarticulation / connected-speech reduction, Ambiguous
     reduction, Monitor — insufficient evidence) and a cautious summary;
   * **where** — word, word position, syllable position (derived), neighbouring
     sounds, exact time (estimated locations marked);
   * **why** — the chain *raw engine observation → evidence → candidate
     interpretation → evidence strength*, and candidate explanations from the
     connected-speech context;
   * **▶ Play exact occurrence / ▶ Play word** — the original recording.
3. **Compare with openpronounce / wav2vec2_raw** runs the other local engine on
   the same audio and shows both engines' evidence side by side, with how they
   relate. Disagreement is shown, never resolved.

Run: `uv run python scripts/run_app.py` → http://127.0.0.1:8642/.

## Definitions

| Term | Meaning | Not |
|---|---|---|
| **Contextual temporal slot** | time from the end of the previous decoded sound to the start of the next decoded sound around the target | the duration of the target phoneme (CTC spans mark posterior peaks; the slot includes transitions and silence) |
| **Possible omission** | not decoded, expected posterior implausible (< 0.05), plus a second kind of evidence (e.g. no room between the neighbours) | proof the sound was absent |
| **Possible weakening** | decoded (or still plausible) with acoustic evidence of weakness (lowest relative energy of this sound here, no voicing where expected) plus a second kind of evidence | a weak articulation |
| **Possible compression** | the contextual slot is shorter than every other occurrence of this sound here, plus recognition evidence | a short sound |
| **Possible substitution** | another sound clearly won the decode, plus a second kind of evidence | "wrong" |
| **Possible coarticulation** | a substitution towards the variant a neighbouring sound predicts (e.g. /n/→[m] before /b/), plus a second kind of evidence | proof of assimilation |
| **Possible connected-speech reduction** | an omission / weakening / compression pattern in a context where English commonly reduces sounds | an error |
| **Ambiguous reduction** | competing decodes, or a possible decoding merge (below) | either reading |
| **Insufficient evidence** | only one kind of evidence; listed to monitor, not interpreted | a finding |

## Methodology

### Evidence streams

Three observational streams; connected-speech context is an *explanation*,
never a stream.

| Stream | Signals | Source |
|---|---|---|
| recognition | omission · not decoded but plausible · substitution · ambiguous decode · weak support (expected posterior < 0.5) | M4 observation (M1 evidence) |
| contextual temporal slot | shorter than every comparable occurrence · no room (≤ 1 model frame) | decoded neighbours (matched, substituted and inserted sounds) |
| acoustic | lowest relative energy among comparable decoded occurrences · no voicing where the sound is normally voiced | M1 acoustic measurements |

Rules (each is an enforced invariant and tested):

* **Any category other than "insufficient evidence" needs two streams.** No
  single signal — not detected, short slot, low energy, low confidence, one
  substitution — produces a reduction category.
* A single stream is listed (as insufficient evidence) only when it is the
  recogniser's own evidence bearing on reduction (not decoded, or a
  substitution a context predicts). **A within-recording rank signal alone is
  never listed: every set has a minimum**, so "the shortest /s/" always exists
  — before this rule, 61 of 99 raw-engine items across R01–R20 were exactly
  that.
* **Evidence strength is qualitative and never "high"**: moderate (all three
  streams), low (two), ambiguous, insufficient.
* Natural connected speech is a *possibility attached to the evidence*, not a
  verdict; one kind of evidence only names the context ("a connected-speech
  context applies, but one kind of evidence is not enough to interpret it").

### Thresholds (all reported in `reduction.thresholds`, all boundary-tested)

| Threshold | Value | Justification |
|---|---|---|
| pause | ≥ 250 ms | M2 pause length (reused) |
| minimum comparables | 3 other occurrences of the same phoneme | for exchangeable occurrences the chance of being the strict minimum of n+1 is 1/(n+1) ≤ 0.25 — a weak signal by design, used only with another stream |
| no room | ≤ 1 model frame (20 ms) | CTC needs at least one frame to emit a phone |
| weak support | expected posterior < 0.5 | M4 high-confidence level (reused) |
| plausible / ambiguous | 0.05 / 0.2 margin | M2 / M4 (reused) |

Comparisons are *relative and within the recording*: same expected phoneme
only; slots next to a pause and estimated (not decoded) regions are excluded
from comparisons. Voicing is not used next to a pause or at an edge (the
voicing window reaches into silence). There is no absolute duration or energy
cutoff anywhere.

### Context

Word position, syllable position (derived from vowel positions — the engines do
not report syllables), neighbouring sounds across word boundaries, function
words, lexical stress *only where the engine reports it* (raw; OpenPronounce
reports none and is never treated as unstressed), pause proximity, speaking rate
(decoded sounds per second between pauses — identical to the M2 measure; also
per stretch between pauses), and recurrence (same category elsewhere / heard as
expected elsewhere).

Connected-speech contexts (candidate explanations, from standard descriptions
of English connected speech, e.g. Gimson's *Pronunciation of English*, Roach's
*English Phonetics and Phonology*): identical neighbouring sound across a word
boundary · /t d/ between consonants · alveolar before another place ·
alveolar before /j/ (yod coalescence) · /t d/ between vowels (en-us flapping) ·
final /t/ before a consonant (glottalisation) · function word (weak form) · /h/
in an unstressed pronoun or auxiliary · vowel without lexical stress · voiced
obstruent before a voiceless sound or pause. Each says which evidence pattern it
would be *consistent with*; matching evidence never proves the process.

### Decoding merges (the M2 "want to" finding)

A sound that was not decoded, next to an adjacent sound decoded as the same
phone (or its voicing partner, e.g. "need to") whose span touches or overlaps
its region, is flagged **merge suspect**: OpenPronounce collapses repeated
phones, and CTC merges repeats without a blank. Such a candidate is never a
reduction category — it stays *ambiguous* (or insufficient), with the note to
compare engines.

## Engine-specific evidence

* Each engine is interpreted separately from its own result; candidates carry
  the engine id and its raw observation, and the validator rejects evidence of
  another engine.
* `engine_compare` pairs positions by word, then by expected phone (aligning
  the raw /ɜː ɹ/ vs OpenPronounce /ɚ/ inventory difference in "were",
  "encouraging"), and labels each pair *same interpretation / interpretations
  differ / only from one engine's evidence*. When one engine did not decode a
  sound that the other decoded separately, and the first engine's adjacent span
  covers it, the note says so: "… This difference comes from post-processing of
  the same posteriors; it is kept, not resolved."
* Both engines use one acoustic model (wav2vec2-lv-60-espeak-cv-ft): agreement
  is not independent confirmation (shown in the UI and the comparison).

## Benchmark results (R01–R20, both engines)

Ground truth is the M2 manifest (R01–R20). `ground_truth.require_ground_truth`
refuses anything else; **New Recording 49 is not ground truth** (smoke / UI /
performance only). Benchmark *purposes* describe the reading intent, not what
the recogniser must find, and are not asserted.

| | raw Wav2Vec2 | OpenPronounce |
|---|---|---|
| candidates (all) | 37 | 48 |
| insufficient evidence | 28 | 35 |
| ambiguous | 6 | 9 |
| possible connected-speech reduction | 1 | 2 |
| possible weakening | 1 | 1 |
| possible substitution | 1 | 1 |

Per recording (interpreted / insufficient), raw | OpenPronounce: R01 0/3 | 0/4,
R02 0/0 | 0/1, R03 1/3 | 2/3, R04 0/0 | 0/1, R05 0/0 | 0/0, R06 0/2 | 0/2,
R07 0/0 | 0/0, R08 0/1 | 0/2, R09 0/1 | 0/2, R10 0/1 | 0/2, R11 1/3 | 1/3,
R12 0/3 | 0/3, R13 0/3 | 1/3, R14 0/1 | 0/2, R15 0/0 | 0/1, R16 0/0 | 0/0,
R17 1/0 | 2/0, R18 4/2 | 4/2, R19 2/3 | 3/2, R20 0/2 | 0/2.

Consistency check (reported, not asserted): interpreted candidates appear
mostly in fast readings — R03 (fast) and R18 (fast) have the most; R18 (4 per
engine) exceeds R17 (normal, same text; 1 | 2). Slow R19 also has some
(ambiguous vowels in "initial"). As in M2, speed does not uniformly add
evidence (R07, R10, R16 fast: none).

Cross-engine (49 paired positions with a candidate in either engine): same
interpretation 35, only OpenPronounce 12, only raw 1, different 1;
"decoded separately elsewhere" notes 11.

**M2 regression, preserved:** in R01–R04 raw decodes two separate /t/ in "want
to"; OpenPronounce does not decode the /t/ of "want" and its /t/ for "to" covers
that region (R01: 5720–6180 ms). M5 flags OpenPronounce's /t/ as a merge
suspect (insufficient evidence, never a reduction category) and the comparison
shows the disagreement. A second instance found by M5: R19 "need to" — raw
decodes [d] for the /t/ of "to" (9080–9100 ms); OpenPronounce's /d/ of "need"
spans 8380–9100 ms; OpenPronounce alone would have listed a possible
connected-speech reduction of /t/ — now ambiguous (voicing-partner merge).

Examples (raw, R18 fast): /t/ in "its" — not decoded but plausible (P 6%),
contextual slot 40 ms, shortest of 6 → possible connected-speech reduction
(function word), low evidence. /ɹ/ in "reduce" decoded [ə] with the lowest /ɹ/
energy → possible substitution, low. /s/ in "costs" not decoded → insufficient
evidence (monitor).

## Performance (CPU, warm medians of 3; cold = first call)

| | raw Wav2Vec2 | OpenPronounce |
|---|---|---|
| model load (cold, R01) | 2801 ms | 5811 ms |
| cold end-to-end R01 (7.9 s audio) | 3463 ms | 6670 ms |
| warm inference R01 / R08 / R18 / R19 | 320 / 493 / 323 / 694 ms | 324 / 505 / 326 / 693 ms |
| warm post-processing R01 / R19 | 88 / 112 ms | 20 / 46 ms |
| warm end-to-end (engine + view + M4 + M5) R01 / R19 | 408 / 810 ms | 346 / 771 ms |
| ms per audio second (warm) | 48–56 | 44–47 |
| end-to-end ÷ audio duration (warm) | 0.048–0.056 | 0.044–0.047 |
| **M5 reduction layer** | **0.35–1.83 ms** (0.06–0.41 % of end-to-end) | **0.36–1.83 ms** (0.07–0.50 %) |
| peak RSS (process) | ≈ 2.9 GB | ≈ 2.9 GB |

App "Compare engines" on R01 (OpenPronounce cold in the app process): 3.8 s;
repeated request reuses the linked analysis (1 ms). New Recording 49 (34 s)
smoke: reduction < 100 ms per engine (asserted), integrity clean.

## Tests

Command: `uv run pytest tests/benchmark tests/phase0 tests/app --deselect tests/phase0/test_audio_record.py::test_audio_record`

| collected | passed | failed | skipped | deselected | warnings |
|---|---|---|---|---|---|
| 732 (731 selected) | **731** | 0 | 0 | 1 (microphone test) | 1 (known transformers notice, M1 missing-model test) |

M4 baseline 550 → +181 M5 tests: `test_m5_reduction.py` 91 (unit, boundary,
negative, invariants, context table, "want to" synthetic, comparison,
ground truth), `test_m5_app.py` 10 (service, M4 unchanged, compare endpoint and
its negative paths), `test_m5_real.py` 80 (R01–R20 × both engines from frozen
evidence, comparison on all 20, speaking rate = M2, "want to" R01–R04 for both
engines and the comparison, R19 "need to", Recording 49 refused as ground truth,
live = frozen for both engines (R01, R03), live performance for both engines,
Recording 49 smoke, real-browser UI). JS unit tests 20 → 24.

Fault injection: 26 defects, one at a time, full suite each, restored and
checksum-verified: slot window shifted, acoustic value mismatched, neighbour
context reversed, word boundary misassigned, unknown stress as unstressed,
speaking rate corrupted, candidate from not-detected alone, strength promoted,
OpenPronounce evidence used for raw and vice versa, playback shifted,
disagreement reconciled, natural reduction labelled "wrong", Recording 49
admitted as ground truth, merge detection removed, voicing-partner merge
ignored, comparables minimum and pause / no-room boundaries changed, rank-only
signal listed, JS slot worded as duration, JS strength shown as high, M4
observations modified by M5, comparison with the same engine, pause-adjacent
slots and estimated regions used as comparables. **First pass 24/26 caught; the
2 survivors (comparable exclusions) exposed a test gap, two boundary tests were
added, and both are now caught: 26/26.**

## Manual test procedure

1. `uv run python scripts/run_app.py` → http://127.0.0.1:8642/
2. Benchmark R01 → Analyse → Phoneme Coach (unchanged) → Reduction & Connected
   Speech: three "that" sounds under *Monitor — insufficient evidence*; open the
   card, read the four-step chain, ▶ Play exact occurrence.
3. Compare with openpronounce: the "want" row shows raw decoded /t/ at
   5.72–5.74 s, OpenPronounce not decoded, and the note that OpenPronounce's /t/
   for "to" covers that region — kept, not resolved. ▶ Play.
4. R18 (fast): connected-speech reduction of /t/ in "its", substitution of /ɹ/
   in "reduce", ambiguous /s/ in "supply". Listen; judge whether the label is
   useful and whether anything overclaims.
5. R19 with OpenPronounce selected: "to" /t/ is *ambiguous* (merge suspect);
   compare with raw: raw decoded [d].
6. Upload or record your own sentence; check that nothing reads as a score,
   ranking or judgement.

Automated browser review (headless Chrome) of R01, the comparison and R18 was
done; it led to two wording fixes (single-stream summaries no longer say
"consistent with natural connected speech"; engine-agreement labels name the
engine).

## Known limitations

* No annotated ground truth for reduction exists: validation covers invariants,
  the known engine disagreement, cross-style consistency and listening — not
  accuracy against labels.
* One acoustic model behind both engines; post-processing differences are the
  only engine differences.
* Within-recording comparisons need ≥ 3 other occurrences of the same sound, so
  short sentences rarely have temporal or acoustic signals; most items there are
  "insufficient evidence" or nothing.
* The temporal slot is context, not duration; relative energy and voicing are
  measured on 20 ms posterior-peak spans (voicing with a widened window).
* Syllable position is derived from vowel positions; stress only from the raw
  engine's dictionary (eSpeak en-us), never observed.
* The connected-speech table is a curated list of common English (en-us)
  processes; contexts outside it get no explanation rather than an invented one.
* Engine comparison is on demand and costs a second inference (≈ 0.35–0.7 s warm,
  ≈ 3–6 s cold).

## M6 boundary / handoff

Not built in M5: accent classification, articulatory inference, speaker
diagnosis, a universal connected-speech model, any learned reduction model,
longitudinal profiles, a targeted practice loop, cloud engines.

M6 can build on: per-candidate chains with engine identity and exact playback;
`reduction.thresholds`; the engine-comparison rows (including decoding-merge
notes); within-recording comparisons; the context table. Open questions for
M6: listener-validated labels for a subset of candidates (to measure usefulness
rather than invariants), whether recurring candidates across sessions are
stable, and whether OpenPronounce's repeat-collapse should be avoided in any
product path given the merge findings.
