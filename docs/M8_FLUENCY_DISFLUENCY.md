# M8 — Fluency & Disfluency

M8 answers a narrow question about one recording: *what in its timing may affect spoken fluency, where
exactly did it happen, what evidence supports that reading, and can I hear it?*

It is the fluency dimension only, a separate layer (`view["fluency"]`) beside:

* pronunciation (M4, `coach`);
* connected speech (M5, `reduction`);
* the sentence boundary (M7, `boundary`).

Prosody (M6) is not implemented.

## Scope

* **Pauses:** silent pauses with exact timing, classified by their context.
* **Rates:** speaking rate, articulation rate, pause ratio, pause count, mean and median pause.
* **Candidates:** fillers, repetitions, restarts, false starts, and changes of pace within the sentence.
* **Synthesis:** a qualitative summary and a count of "fluency things to notice".
* **Reader:** one inline phrase and a Details section, each observation with its exact time and ▶ Listen.
* **Reading summary (M12):** counts, what recurs across sentences, up to three moments to hear, and the range
  of speech rates (no score).
* **M7 integration:** only the sentence's region is analysed; continued speech is described separately;
  withheld feedback stays withheld.
* **Engines:** each engine's evidence is kept separately, and comparing them shows disagreement.

## Non-goals

* No fluency score, disfluency score, percentage, ranking or percentile. Strength describes evidence,
  never performance.
* No word recognition. The engines recognise speech sounds, so M8 never claims to know which words were
  said beyond the sentence's own text.
* No diagnosis of intention: "possible false start", never "you made a false start".
* No universal "good" rate. A personal baseline over time is M10.
* No change to M4, M5 or M7 objects, and no new model, inference, dependency or audio file.

## Architecture

```
analyze_pipeline → build_analysis_view(result, analysis.wav)
    M3 view · M4 coach · M5 reduction            (unchanged, byte-identical)
    M8 build_fluency(result, samples)            → view["fluency"]       (app/fluency.py)
reader (M7): the view is built from target.wav when M7 cut the sentence out, so M8 only sees the sentence
    view["fluency"]["m7"]                — M7 state, cut, whether withheld
    view["fluency"]["continued_speech"]  — overflow/uncertain region described from the whole-attempt result
    _withheld_view                       — withheld boundary ⇒ fluency withheld (state boundary_withheld)
    job["fluency"]                       — compact {state, notice, pace} for the inline note
    comparison()["fluency"]              — compare_fluency(): the two engines side by side
    summary["fluency"]                   — fluency_summary(): aggregate of the sentences in the summary
```

**Inputs** are all existing evidence:

* the decoded sounds and their alignment to the sentence (matched or substituted expected sounds, and
  "extra" sounds placed on no expected sound);
* M7's speech-activity estimator, including its reliability check;
* the sentence's punctuation;
* the expected eSpeak pronunciation.

M8 takes about 5–25 ms per recording against 0.4–0.7 s of warm inference, with about 6 MB transient
memory. It does not mutate the engine result (tested on R01–R20, both engines).

## Observation schema

Every observation (`FluencyObservation`) follows **Observed → Evidence → Interpretation → Strength**.

| Field | Contents |
|---|---|
| `id`, `type` | type is one of PAUSE · FILLER · REPETITION · FALSE_START · RESTART · RATE_ANOMALY · OTHER_HESITATION |
| `label` | user-facing ("Possible hesitation", "Likely self-repetition", …) |
| `classification` | for pauses only |
| `start_ms`, `end_ms`, `duration_ms` | on the analysed audio's timeline |
| `observed` | e.g. "0.89 s pause", "'the' heard twice", "/ʌ m/" |
| `evidence[]` | `FluencyEvidence{kind, detail, value}`; kinds: position, punctuation, silence, relative_duration, decoded_gap, decoded_sounds, acoustic, pause, alternative, similarity, adjacency, lexical, interruption, onset_overlap, stretch_rate |
| `interpretation` | cautious wording ("consistent with…", "may also be…", "intention cannot be known") |
| `strength` | moderate · low · ambiguous · insufficient. There is no "high" |
| `context` | surrounding words, position, `part_of` when a pause belongs to a repetition or restart |
| `playback` | `{timeline: "analysis_wav", span_ms, play_ms, context_ms}`: `span_ms` is the event exactly; `play_ms` = span − `context_ms[0]` … span + `context_ms[1]` (clamped to the audio). The context is stated, never silent: the reader shows the exact times and each Listen says how much context it adds |
| `engine`, `notice` | the engine, and whether it counts as "a fluency thing to notice" |

**`validate_fluency`** rejects:

* a negative, inverted or out-of-region interval;
* a duration that doesn't match the interval;
* missing playback, a playback window that doesn't cover the observation inside the audio, or one that is not
  exactly the span plus its stated `context_ms`;
* a duplicate observation (same type and interval), or two candidates (filler, repetition, restart, false
  start, other hesitation) claiming the same moment — one decoded sound is never counted twice;
* empty evidence;
* a type without its required evidence:
  * a pause needs position evidence (never duration alone);
  * a filler needs its decoded sounds;
  * a repetition or restart needs decoded sounds, similarity, adjacency and lexical evidence;
  * a false start needs decoded sounds, onset overlap and an interruption;
* a hesitation without relative-duration evidence;
* leading or trailing silence reported as a pause, and overlapping pauses;
* "high" strength, an unknown type, or judgemental wording;
* a rate that isn't syllables over its interval, a syllable count that isn't the expected vowel nuclei,
  or a rate given while unavailable or when the sentence wasn't recognisably decoded;
* an articulation rate or pause ratio given although the pauses were not verified as silence;
* a score in the summary.

The result is stored as `fluency.integrity`.

## Pause methodology

1. **Candidate.** A gap of at least 250 ms (the M2 pause length) between consecutive decoded sounds. This
   is a candidate threshold, never a classification.
2. **Silence check (when the level estimate is reliable).** M7's coverage check must pass: at least 60 %
   of the sentence's decoded sounds fall on speech-like frames. The pause is then the gap trimmed of
   speech-like frames at both ends, so a breath or click inside it remains part of the pause. It must
   contain at least 150 ms of actual silence.
   * If it doesn't, sound continues through the gap with nothing decoded. When that lasts ≥ 400 ms between
     words, it becomes `OTHER_HESITATION` with *insufficient* strength (a prolonged sound, a hum or a
     breath).
   * If the level estimate is unreliable, the pause is the decoded gap, with strength at most *low*.
3. **Context:**
   * where it is: at a phrase or sentence boundary (punctuation after the previous word), between words
     inside a phrase, inside a word, or where words were not decoded;
   * how long it is relative to this recording's own pace: silence ÷ the **reference**, the median
     onset-to-onset interval between consecutive words *with any pause inside a step replaced by the
     recording's typical gap between decoded sounds* (`word_interval_basis: "pauses removed"`).

     Pauses must not be part of their own reference. In m8.1 they were, and a real reading with many pauses
     inflated it to 1.38 s, so its 1.3 s pauses inside phrases were labelled "Brief pause". A pause is
     *replaced* by the typical gap, not removed outright: decoders time a sound as a short peak, so a pause
     gap also holds the end of the sound before it, and removing it made the reference too short (tested).
     A slow reader has a long reference, so slow speech alone is never a hesitation.
4. **Classification:**

| Class | Label | Rule | Noticed |
|---|---|---|---|
| `unusually_long` | Long pause | relative ≥ 4, anywhere. Moderate inside a phrase with a reliable estimate; low at a boundary ("may be planning or breathing") | yes |
| `natural_boundary` | Pause at a phrase boundary | at punctuation | no |
| `possible_hesitation` | Possible hesitation | inside a phrase or word, relative ≥ 2. Moderate when relative ≥ 3 and the estimate is reliable | yes |
| `brief_within_phrase` | Pause within a phrase | shorter. A short silence inside a word before a stop consonant is a closure, not a hesitation | no |
| `insufficient_evidence` | Pause | context unknown | no |

The 2× threshold was calibrated on R01–R20 with the new reference. At 1×, most of their ordinary
within-phrase pauses (160–690 ms) would be flagged; at 2×, 2–3 are flagged per engine, while inserted 1 s
pauses are still found.

**When a pause is not counted although it is listed:**

* *Level estimate unreliable* (M7's coverage check fails): a pause is only the gap between decoded sounds,
  which may include parts of those sounds. It is kept as a candidate with low or insufficient strength, but
  only an unusually long one is a thing to notice. Articulation rate, pause ratio and pace are not given.
  This was found on real readings in a noisy room: 4 of 5 attempts, with up to 30 gap-only "pauses" in one
  sentence.
* *Sentence not recognisably decoded* (fewer than half of its sounds found): where words lie, and so
  "inside a phrase", is uncertain, and extra decoded sounds are plentiful. Everything is listed with "listed
  but not counted"; only an unusually long pause counts. A real attempt with 27 % of its sounds found had
  17–19 notices before this rule.

Leading and trailing silence are reported as metrics (`leading_silence_ms`, `trailing_silence_ms`), never
as pauses.

## Rate methodology

| Measure | Definition |
|---|---|
| `syllable_count` | vowel nuclei in the expected eSpeak pronunciation of the words from the first to the last word with a decoded sound. Never decoded sounds. |
| `speaking_interval_ms` | from the first to the last decoded sound of the sentence |
| `speaking_rate` | syllables ÷ speaking interval |
| `articulation_rate` | syllables ÷ (speaking interval − silent pauses); only when pauses are verified as silence (`articulation_available`, with the reason otherwise) |
| `pause_ratio` | silent pause time ÷ speaking interval (verified pauses only) |
| `speech_active_ms` | speech-like frames in the interval (reliable estimate only) |

**Unavailable** (with the reason) when:

* fewer than half of the sentence's expected sounds were decoded *exactly* (M7's defensibility limit;
  extra sounds don't count against it, so fillers and repeats never make a rate unavailable);
* there are fewer than 4 syllables;
* there is no interval.

**Pace:** steady, uneven (a `RATE_ANOMALY`), pause-heavy (pause ratio ≥ 0.3), or insufficient evidence
(including when pauses could not be verified, or the sentence was not recognisably decoded; the summary
then says which).
`RATE_ANOMALY` is a stretch between pauses with at least 5 vowel nuclei and 600 ms whose vowel rate is
≥ 1.5× or ≤ 0.6× the rest of the sentence: "relatively fast/slow passage for this sentence". It is low
strength and not noticed by default.

## Filler methodology

A candidate needs all of:

* **decoded sounds:** a run of extra sounds, placed on no expected sound, shaped like a filled pause: a
  central or open vowel (ə ʌ ɜ ɐ ɚ ɛ æ a …), optionally ending in m/n ("uh", "er", "um", "erm"), at most
  3 sounds. High vowels (ɪ, ᵻ) are not filler-shaped: a lone /ɪ/ is far more often part of "is", "it", "in";
* **not part of a repetition or restart:** repetitions and restarts are found first, and a filler never
  reuses their sounds. On a real reading the same /ɪ/ was both a "Possible filler" and the first sound of
  "'is' heard twice";
* **position:** between words, or before the first or after the last word. An /əm/ inside a word is never
  a candidate.

Supporting evidence:

* sustained speech-like sound ≥ 200 ms around it. The event spans that sound, bounded by the neighbouring
  decoded sounds, not only the decoder's 20 ms peak, so Listen plays the filler itself;
* a pause just before or after it.

An **alternative** lowers the strength to *ambiguous*: a neighbouring word whose expected vowel was not
decoded, so the sounds may belong to that word.

Strength is **moderate** only with sustained sound, an adjacent pause and a reliable estimate; otherwise
**low**. A thing to notice needs moderate evidence or an "um" shape (vowel + nasal): a lone low-strength
vowel stays listed but is not counted, since decoders often insert a lone /ə/ between consonants. The label is "Possible filler", with "acoustically consistent with a filled pause (such as
'um'); it may also be part of a word decoded out of place".

## Repetition and restart methodology

Two adjacent, near-identical stretches of decoded sounds (repetitions are found from the decoded sequence,
not from the alignment's runs):

* **Similarity** ≥ 75 %: unit edit distance, with a same-class substitution costing 0.5. Stretches of 3
  sounds or fewer must be identical: two sounds that merely share a vowel are not a copy (this was found
  on R08).
* **Timing:** at most 1.5 s apart.
* **They must need explaining.** Either ≥ 60 % of their sounds are surplus extra sounds, or one copy was
  forced onto a word whose expected sounds it does not resemble (similarity < 0.5). The second case is a
  *displaced copy*: "you you want" read where a weak "that" was expected. Identical words in the text
  itself ("had had") never count.
* **Lexical anchor:** the copy made mostly of the sentence's own sounds names the word(s).

**REPETITION** when the copy matches the anchor's full expected words:

* "Likely self-repetition" when an interruption separates the copies and similarity ≥ 85 %;
* otherwise "Possible repetition", which "may be a self-repetition or intentional emphasis";
* two-sound copies without an interruption are *ambiguous* and not noticed;
* moderate only with an interruption, a reliable estimate and more than 2 sounds.

**RESTART** when the first copy matches the beginning of a word that then continues ("chan— change"). It
is moderate only when interrupted, ≥ 85 % similar, ≥ 3 sounds, with a reliable estimate; otherwise low.

## False-start methodology

An extra-sound run must:

* begin like the speech that follows it, for at least 2 identical sounds;
* then differ for at least 2 sounds;
* be followed by a silent interruption.

It is always **low** strength: "consistent with a false start (the speaker's intention cannot be known
from the audio)". A pause alone, a substitution, or extra sounds without onset overlap never produce one
(all tested). A pause inside a repetition, restart, false start or filler is shown as part of it
(`part_of`), never counted again. Sound with nothing decoded inside one of these events, or next to a
filler, is part of it, not a separate "other hesitation".

## M7 integration

| M7 state | M8 |
|---|---|
| TARGET_ONLY | the whole attempt |
| TARGET_PLUS_OVERFLOW / BOUNDARY_UNCERTAIN (sentence analysed alone) | the sentence region only (the view comes from `target.wav`); the continued speech is in `continued_speech` |
| NO_RELIABLE_BOUNDARY without plausible continuation | the whole attempt (pre-M7 behaviour) |
| feedback withheld | `state: "boundary_withheld"`, no observations, no metrics, no continued-speech description |

`continued_speech[]` gives for each overflow or uncertain region:

* duration and speech-active ms;
* decoded sounds per second;
* pause candidates;
* an exact Listen window.

It never gives a syllable rate, because its words are unknown, and it is never merged into the sentence's
metrics. Tests show a continuation full of "um" and repeats produces no target observation, and that the
sentence's metrics equal those of the same sentence without continuation.

## In the reading summary (M12)

`summary["fluency"]` (summary version sum-3) is built only from the sentences the summary includes: the
same sentence-only analyses M4/M5 use. A withheld, discarded, re-recorded or unconfirmed attempt
contributes nothing. It gives:

* the number of things to notice, and in how many sentences;
* the kinds, with the sentences each occurs in, and what recurs (a kind in ≥ 2 sentences);
* up to three moments to hear (moderate evidence first, then the longest), each an exact playback
  reference with its stated context;
* the range of speech rates.

One line, for example "5 fluency things to notice in 3 of 5 sentences · possible hesitation pauses in 3
sentences · speech rate 2.1–3.4 syllables per second". It is shown below pronunciation and connected speech,
with "Counts of moments to listen to, not a judgement of the reading". `validate_summary` checks that its examples come
from included attempts.

## Engines and the shared acoustic model

Each job is one engine, and its observations carry `engine`. `compare_fluency` pairs the two engines'
noticed observations by type and time overlap (≥ 50 %):

* `both_decoding_paths`: "Both decoding paths show this; supporting evidence, not independent
  confirmation";
* `first_only` / `second_only`: "Only <engine> shows this".

`validate_fluency_comparison` rejects a single-engine observation presented as shown by both. Both local
engines share one acoustic model, so agreement is never independent confirmation.

## Benchmark methodology

* **R01–R20** (read sentences, one speaker) are a baseline and regression set, **not disfluency ground
  truth**.
* **Controlled variants** (`tests/app/m8variants.py`) are spliced from them at a word boundary inside a
  phrase, using the frozen M2 timings. They are generated in temporary folders and never committed:
  * inserted room-tone pause (1 s), and two pauses (0.9 s each);
  * a 1 s pause at the sentence's comma (R17–R20), which should read as natural phrasing;
  * filler-like vowel: the steady centre of one of the speaker's vowels, tiled to 380 ms, between pauses;
  * repeated word, after a pause, and repeated phrase;
  * partial word + pause + word;
  * false start: the next word's onset + part of another word + pause;
  * slow and fast reading: the whole recording time-stretched (WSOLA, pitch unchanged) to ×1.35 and ×0.75
    duration.

**These variants show that the detector responds to each construction. They do not establish real-world
filler, repetition or false-start accuracy.** That needs genuine recordings with known disfluencies.

## Validation results (m8.2)

**R01–R20, both engines** (wav2vec2_raw / openpronounce):

* 20/20 ok, integrity ok, level estimate reliable, rates available.
* Speaking rate 1.50–4.90 syllables/s (median 3.08); articulation rate 2.39–5.14 / 2.25–5.14; pause ratio
  0.00–0.37 / 0.00–0.33; reference 200–500 / 200–520 ms.
* Silent pauses 59 / 53, mostly "Pause within a phrase" (53 / 47), 3 at phrase boundaries.
* Things to notice: 3 per engine, all pauses: R03 (0.45 s, low), R08 (1.25 s, as a long pause on
  wav2vec2_raw and as a possible hesitation on openpronounce), plus R08 0.56 s (wav2vec2_raw) or R04 0.46 s
  (openpronounce). m8.1 noticed 9 / 7.
* No filler, repetition, restart or false start is noticed on any clean recording.
* **Disagreement, preserved:** 8 compared rows, of which 1 is wav2vec2_raw only and 1 openpronounce only;
  pause counts differ on 6 recordings.
* Output is identical across warm repeats and fresh engine instances, and the engine result is never mutated.

**Controlled variants**, R01–R20 × both engines, 408 analyses. 0 integrity issues, 0 playback-invariant
failures, all deterministic.

| Variant | wav2vec2_raw | openpronounce |
|---|---|---|
| inserted pause (possible hesitation / long pause) | 20/20 | 18/20 |
| two inserted pauses (each) | 40/40 | 39/40 |
| pause at the comma → natural phrasing, never "possible hesitation" | 4/4 | 4/4 |
| repeated word | 15/20 | 15/20 |
| repeated phrase | 10/20 | 10/20 |
| partial word + restart (restart or repetition) | 15/20 | 14/20 |
| false start (false start or restart) | 6/20 | 7/20 |
| filler-like vowel | 3/20 | 3/20 |
| clean | 0 disfluency candidates | 0 disfluency candidates |
| fast (×0.75) | rate ×1.33, 0 new notices | rate ×1.33, 0 new notices |
| slow (×1.35) | rate ×0.74; 3 recordings gain a low "possible hesitation", R08 a low repetition | rate ×0.74; 1 gains a low "possible hesitation", R08 a low repetition |

The filler variant is mostly not decoded as a central vowel, so a candidate never arises: the conservative
outcome. One m8.1 "hit" (R18) was a stray 70 ms /ʌ/ *before* the inserted vowel; m8.2 no longer counts it.
In the slow variant, borderline pauses (≈ 1.9× the reference) cross 2×, because decoder peaks do not
stretch with the audio while pauses do. These are low strength, but this is a known limit.

**Connected speech (M5):** across R01–R20, 37 / 48 M5 candidate sites; 1 / 2 noticed pauses overlap one,
all on R08, whose decoding is unusual. No filler, repetition or restart lies at a reduction site.

**Real readings** (the developer's own stored sessions, read only and never committed: 11 sessions, 71
attempts, replayed through a fresh reader with the openpronounce comparison on every attempt):

* every M7 state occurs: TARGET_ONLY 45, BOUNDARY_UNCERTAIN 14 (feedback shown), TARGET_PLUS_OVERFLOW 7,
  withheld 4, NO_RELIABLE_BOUNDARY 1;
* 0 invariant problems:
  * withheld ⇒ fluency withheld, with no observations, metrics or continued-speech description;
  * sentence cut out ⇒ fluency region = the cut, and no event or Listen window beyond it;
  * continued speech described with M7's regions;
  * playback context exact;
  * summaries valid;
* comparison rows: 147 both decoding paths, 15 wav2vec2_raw only, 5 openpronounce only. The reader refuses
  a comparison for a withheld attempt (409), as intended.

**Browser session** (headless Chrome, fake microphone, recording through the reader's own controls):

* a sentence read with an intentional 1.2 s pause and a restart, then continuing into the next sentence:
  * M7 cut the sentence 19 ms after the continuation began;
  * the pause was the one thing to notice ("Long pause", 1.53 s, moderate);
  * each Listen played exactly the stated window, all inside the sentence;
  * the continuation had its own Listen, marked as not part of the analysis;
  * the restart was not detected (as in the R01 restart variant);
* "↻ Read again" on the same sentence, then the next sentence:
  * the newer attempt is summarised and the older one stays stored;
  * the summary's fluency line read "Nothing stood out in the timing of 2 sentences · speech rate 1.9–2.2
    syllables per second".

**M4/M5/M7 regression:** hashes of the M4 coach, M5 reduction, M3 words and M7 boundary decisions on
R01–R20 × both engines plus 32 stored M7 calibration variants, 232 entries:

* identical to the frozen M7 baseline (commit 16edc2c);
* against the pre-M8 baseline, M4, M5 and words are byte-identical. The differences are only the `fluency`
  key, and the M7 fix's reason wording on 8 speech-dense noisy cases (state, cut and withholding unchanged).

**Fault injection (M8):** 56 injected defects, all caught, every file restored byte-for-byte. The 31 m8.1
defects, with F16 re-anchored, plus:

* timing: missing timing accepted;
* duplicates and double counting: a duplicated event; a repetition's sound reused as a filler; the validator
  ignoring double counting; undecoded sound inside a repetition reported again;
* false candidates: a high-vowel filler; the repetition surplus requirement dropped; a lone weak vowel
  counted;
* pause thresholds: hesitation at 1×; candidate gap halved; the reference including its pauses; the
  reference removing pauses outright;
* rates: articulation rate from unverified pauses; speaking rate over interval minus pauses;
* gating: unreliable-estimate gating removed; unclear-decoding gating removed;
* playback and spans: padding not stated; filler span reduced to the decoded peak; a pause inside a restart
  not marked part of it;
* summary: fluency from excluded attempts; unnoticed observations counted; a score; examples not validated;
* UI: an articulation rate from unverified pauses; Listen hiding its context.

The 24 M7 boundary-confidence defects re-run after M8: all caught.

**Tests:**

* `test_m8_fluency.py`: schema, pauses, the reference, gating, rates, fillers, repetitions, restarts, false
  starts, summary, validator, comparison.
* `test_m8_service.py`: M7 states, overflow isolation, withheld, rebuild, comparison, M4 untouched, and the
  reading-summary aggregate (included sentences only).
* `test_m8_real.py`: R01–R20 both engines, warm and fresh repeatability, the variants (including two pauses,
  comma pause, slow and fast) with exact playback, M7 overflow through the reader, and a browser run.
* `js/reader_feedback.test.js`: the reader's fluency wording, rates, exact times and Listen context.

## Performance

R01–R20 variants (3.1–22.2 s of audio), Apple M5 CPU:

| | wav2vec2_raw | openpronounce |
|---|---|---|
| engine create | 0.5–0.7 s | 0.6 s |
| first (cold) inference, 7.9 s of audio | 2.9–5.5 s | 5.0–5.3 s |
| warm inference, mean | 0.48 s | 0.40 s |
| realtime factor (warm inference) | 0.053 | 0.044 |
| M8 `build_fluency`, median / p95 / max | 9 / 25 / 41 ms | 9 / 24 / 28 ms |
| whole view (M3–M8), median | 11 ms | 11 ms |
| M8 peak traced memory | ≤ 8.1 MB | ≤ 8.1 MB |
| process RSS growth (model loaded, 204 analyses) | ≈ 3.8 GB | ≈ 4.0 GB |

M8 adds well under 5 % to a warm analysis.

## Target confirmation revised during M8 validation

The M8 manual test showed correct sentences reported as "may not match". The cause was target confirmation
measuring phone accuracy, not M8. It was replaced by contrastive sentence identity (tc-2), with four separate
decisions per attempt (identity, boundary, feedback, summary); see `docs/M12_READER.md`. The fluency layer
itself is unchanged; it still follows M7's regions and withholding.

## Known limitations

* **No real disfluency ground truth.** Detection rates come from synthetic splices of one speaker's
  readings; the real sessions only check invariants (containment, withholding, playback), not accuracy.
  Real fillers ("um" with its own voice quality), real repetitions and real false starts still need labelled
  recordings.
* **Noisy rooms.** On the developer's real readings, M7's level estimate was unreliable in 4 of 5 attempts
  of one session. M8 then only lists gap-only pauses, counts only long ones, and gives no articulation rate
  or pace. This is honest, but it means less fluency feedback in exactly those rooms. A better silence
  estimate needs M7's estimator and is out of M8's scope.
* **Phone recognisers, not word recognisers.** A filler or repeat is only visible if decoded as extra
  sounds. The aligner can absorb it into a weakly decoded neighbouring word (handled for repetitions by the
  displaced-copy rule, not for fillers). Restarts and false starts are found in fewer than 3 in 4
  constructions.
* **Calibration.** The relative thresholds (2×, 3×, 4× the reference) and copy similarity are set on
  synthetic material and R01–R20. Borderline pauses in a much slower reading can cross 2× (slow variant: 1–3
  of 20, low strength).
* **Edge pauses.** A pause right at the M7 cut is outside the analysed region, so it is never reported.
* **Pace.** "Relatively fast/slow" only compares stretches within one sentence. Personal baselines are M10.
* **Lab page.** The lab (M3–M5 page) receives `view["fluency"]` but does not display it; the reader does.
