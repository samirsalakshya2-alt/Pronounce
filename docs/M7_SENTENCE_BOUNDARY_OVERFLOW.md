# M7 — Sentence Boundary & Overflow Analysis

When a reader keeps speaking after the active sentence, the recording attempt
still ends only when they select another sentence, pause or stop (the
**capture boundary**, M12). M7 adds an **analysis boundary** inside that one
attempt. Only the sentence's own region is given to pronunciation analysis
(M4 phoneme coach, M5 connected speech, and M6 later). The rest is preserved
as **continued speech** for M8 and is never interpreted.

Guiding rule: M7 is optimised for **not contaminating pronunciation
feedback**, not for a clever boundary. When in doubt it says so
(`BOUNDARY_UNCERTAIN`) and never adds the doubtful audio to the sentence's
analysis.

## Why it is needed (measured, not assumed)

R01 followed by R05 (0.3 s apart) and analysed against R01's text gives the
same pattern on both engines:

* **All 34 continuation sounds are attached to the final /dʒ/ ("change") as
  insertions.**
* **"to" and "change" become alignment-suspect.**
* **Earlier sounds change decode.** "to" /t/ is heard as [d] instead of the
  clean [t]: the model uses bidirectional context.

Across R01–R20 with synthesised continuations, both engines, the whole-attempt
analysis attaches about 900 continuation sounds to final words per 20
recordings, and changes 54–65 per-sound decisions relative to the clean
recordings. Removing the continuation from the result afterwards is not
enough: the earlier decodes are already affected. The sentence has to be
analysed **on its own audio**.

## Boundary states

| State | Meaning | Analysed as the sentence |
|---|---|---|
| `TARGET_ONLY` | nothing credible after the sentence (silence, breath, a release, a weak final sound, a few decoder sounds over silence) | the whole attempt (one inference, unchanged from M12) |
| `TARGET_PLUS_OVERFLOW` | the final word was decoded, then continued speech confirmed by speech energy | `[0, cut)` only; `[cut, end)` is *overflow* |
| `BOUNDARY_UNCERTAIN` | continued speech, but the end of the sentence is not clear (final word partly decoded, interleaved or split by a pause, the final word said again, speech energy not measurable, decoded continuation without measured energy, or long speech-like sound that was not decoded) | `[0, cut)` only; `[cut, end)` is *uncertain* — preserved, never interpreted. If the sentence itself was decoded too unclearly to place its end, **no feedback** (withheld) |
| `NO_RELIABLE_BOUNDARY` | evidence missing or invalid (failed analysis, no decoded timing, malformed or impossible timing, audio too short, an inconsistent boundary) | the whole attempt (as before M7) **only if nothing suggests continuation**; otherwise **no feedback** (withheld) |

**Withheld feedback.** When continued speech is plausible but no defensible
boundary exists, the attempt is analysed and stored as usual, but no
pronunciation feedback is shown:

* the view's state is `boundary_withheld`, with no annotated words, no M4
  observations and no M5 candidates;
* the summary leaves the attempt out ("sentence boundary uncertain");
* the reader shows the uncertain wording.

The rule is that false uncertainty is preferable to false pronunciation
errors.

Regions always tile the attempt: `target [0, cut)` then at most one
`overflow` or `uncertain` region `[cut, duration)`. Every millisecond is
attributed and nothing is discarded. Silence is not a separate region; each
region records its own `speech_ms`.

## Evidence (one inference: the attempt's own analysis)

All evidence comes from the full-attempt result the reader already computes;
there is no extra model.

1. **End-free alignment.** The sentence's expected sounds are aligned
   (unit-cost, as in `ctc.align`) against a *prefix* of the decoded sounds;
   decoded sounds after the prefix cost nothing. A whole-recording alignment
   has to place continued speech somewhere, and it stretches the final word
   across it (engine evidence `engine_alignment_stretched`). The end-free
   alignment does not.
   * *Ties.* Among equally good ends the later one is kept, so the sentence
     keeps its own substituted sounds, with two limits:
     * never across a pause (≥ 250 ms, the M2 pause);
     * each extra sound must stand for the sentence's own sound credibly: an
       exact match, or vowel for vowel / consonant for consonant.

     Otherwise an unsaid final sound would cost the same as a continuation
     sound substituted for it. Example from the calibration: "…six months"
     (final /s/ not decoded) followed straight away by "I would…" put the
     diphthong "aɪ" in as /s/.
   * *Defensibility.* The cut is defensible only if the sentence itself was
     decoded recognisably: at most 0.5 alignment differences per expected
     sound. Measured values:
     * R01–R20 with continuations: ≤ 0.30;
     * the manual-test attempts (noisy room, correct cuts): 0.36–0.42;
     * speech-dense noisy simulations: 0.59–0.91.

     In the last group the cut wanders by seconds in either direction, so
     with continuation present, feedback is withheld and no second inference
     runs.
2. **Repeated final word.** If the next few decoded sounds (within the final
   word's length + 4) end with the sentence's final word, the sentence ends
   after the later occurrence. Found in the benchmark: R08 contains "begin…
   beginning… begins". Without this rule the real final word would have been
   cut off. A repeat followed by more speech is always `BOUNDARY_UNCERTAIN`.
3. **Decoded sounds after the sentence:** at least 3 (about one short word)
   are needed to count as continuation.
4. **Speech-like sound after the sentence.** This is independent of the
   decoder: 20 ms frames, active when 15 dB above the recording's own noise
   floor (10th percentile, excluding exact digital silence) and above
   −60 dBFS. At least 250 ms is needed (the clean benchmark maximum is
   240 ms). If ≥ 1000 ms of speech-like sound is present that the decoder did
   not decode, the state is `BOUNDARY_UNCERTAIN`, never ignored.
   * **Reliability of the level estimate.** This evidence is trusted only
     when it agrees with the decoder on the sentence itself: at least 60 % of
     the sentence's decoded sounds must fall on frames it marks as speech.
     In a noisy or speech-dense recording, the noise-floor estimate can come
     from speech, or from room noise a few dB below it, and the estimator
     then marks the speech itself as inactive. Coverage: R01–R20 0.96–1.00;
     the failing manual-test attempts 0.07 and 0.10.
   * **No acoustic veto of decoded continuation.** Missing energy can rule
     out a short decoded tail (3–7 sounds over silence, a reliable estimate).
     It can never rule out ≥ 8 decoded sounds (about two words): that is
     `BOUNDARY_UNCERTAIN`. With an unreliable estimate, ≥ 3 decoded sounds
     after the sentence is always `BOUNDARY_UNCERTAIN`, never `TARGET_ONLY`.
     Fewer than 3 is `TARGET_ONLY`, because nothing decoded can attach to
     the sentence's analysis.
5. **Completion of the final word:** ≥ 50 % of its sounds decoded, including
   its last sound, and the sentence's last decoded sound belongs to it.
6. **Suspicious alignment:** insertions inside the final word, or the final
   word's sounds separated by a pause.
7. **Gap:** a gap under 100 ms before the continuation is reported as an
   approximate boundary.

The **cut** is the quietest 20 ms frame between the sentence's last decoded
sound (+60 ms where there is room) and the first continuation sound. Duration
alone never creates a boundary, and neither does silence alone.

**After the sentence is analysed on its own, the boundary is checked again.**
It is downgraded to `BOUNDARY_UNCERTAIN` (never re-cut) if:

* the sentence region alone still shows sound after its end; or
* the final word is decoded less completely than in the whole attempt (the
  cut may have clipped it).

## Pipeline, storage and playback

`ReaderService.run_job` works per job:

1. **Full attempt.** `analyze_pipeline(analysis.wav)` → `result.json`
   (write-once, the full-attempt evidence, unchanged).
2. **Boundary.** `detect_boundary`, then `map_to_capture` (each region's
   sample range on the original capture run, exact and contiguous), then
   `validate_boundary`.
   * An invalid boundary is never acted on: the job falls back to
     `NO_RELIABLE_BOUNDARY`, with the issues as the reason.
   * A `NO_RELIABLE_BOUNDARY` falls back to the whole attempt only when
     `continuation_plausible` finds nothing. It looks for two kinds of
     evidence that don't depend on timing: the detector saw continuation
     before its boundary was rejected, or ≥ 3 extra sounds sit on the final
     word in the whole-attempt alignment (R01–R20: at most 1). Otherwise
     feedback is withheld.
3. **Sentence region,** only for `TARGET_PLUS_OVERFLOW` / `BOUNDARY_UNCERTAIN`:
   * `target.wav` is a sample-exact prefix of `analysis.wav` (PCM copied,
     never re-encoded), written once in the job directory;
   * `analyze_pipeline(target.wav)` → `target_result.json` (write-once);
   * `view.json`, target confirmation and the compact feedback all come from
     this result.

   A failed sentence-region analysis fails the job (Retry is offered). It
   never falls back to contaminated feedback.
4. **Storage.** `boundary.json` is written once (decision, reasons, evidence,
   thresholds, regions, the consistency check). The job record and
   `view.json` carry `boundary` for the reader. Each region has an exact
   playback reference `{session_id, segment_id, attempt_id, job_id,
   timeline: "analysis_wav", kind: target|overflow|uncertain, play_ms}`.
   Because the sentence region starts at 0, every M1–M5 timing in the
   sentence's analysis is already on the attempt's `analysis_wav` timeline.
5. **Comparison.** A comparison job uses the primary job's boundary (the same
   region, so both engines describe the same audio and `compare()` durations
   match). It stores the other engine's own assessment as
   `boundary.other_engine` with `differs` (state differs or cuts > 200 ms
   apart) and the shared-acoustic-model caveat. Nothing is merged, and no
   engine is preferred after the fact.
6. **Rebuild.** `rebuild_view` rebuilds from `target_result.json` when it
   exists, and withholds again when `boundary.json` says so.
7. **Containment invariant (every job).** `evidence_outside_target` checks
   that every timestamp M4/M5 consume lies inside the sentence's region
   `[0, cut]` (1 ms tolerance). This covers:
   * phone timings;
   * extra sounds, before and inside words;
   * word and sound spans and playback windows;
   * M4 observation spans and playback windows;
   * M5 candidate spans and playback windows.

   The result is stored as `boundary.containment`. Any violation withholds
   the feedback.
8. **Immutability.** `original.wav` and `analysis.wav` are never modified.
   A re-run after recovery accepts an existing file only if its bytes are
   identical.

The summary (M12) reads `view.json`, so it only ever sees the sentence. M6,
once it exists, plugs into `build_analysis_view` and inherits this.

## Reader

* **After an analysed sentence,** inline in the paragraph (the article stays
  continuous):
  * *Continued speech detected after this sentence.* `▶ Listen` *This
    continuation was not included in the pronunciation analysis.*
  * or *Sentence boundary uncertain — some continued speech may not be
    included in this sentence's analysis.* `▶ Listen`

  This is italic and muted: no warning colour, no score.
* **Details → Sentence boundary:**
  * a plain explanation;
  * `▶ Listen to the sentence` and `▶ Listen to the continued speech` (or
    `the uncertain part`), each exact on the attempt's analysis WAV;
  * a collapsed *Evidence* block: the decision and its engine, the boundary
    time, the last sound of the sentence, the final word decoded, the sounds
    and speech-like sound after it, the gap, the reasons, and the other
    model's assessment once compared.
* There is no automatic sentence advancement. The next sentence stays
  unread; the continuation is never analysed against it.

## Manual-test correction (speech-dense reading)

**What went wrong.** The manual test (one reader, a noisy room, reading
ahead into the next sentence before selecting it) produced a sentence-2
attempt that ended with "…underresourced". It ran straight on into sentence
3 (19.3 s → 20.0–25.6 s).

* **The detector saw the continuation, then vetoed it.** It found 40
  decoded sounds after the sentence. But the level estimate put the
  recording's noise floor at −36 dBFS and its threshold at −21 dBFS, above
  most of the speech, so it measured 70 ms of "speech-like sound" and the
  energy rule turned this into `TARGET_ONLY`.
* **The whole attempt went to M4/M5.** Sentence-3 sounds were attached to
  "underresourced", its /t/ was matched at 22.82 s inside sentence 3, and
  the word became `not_interpreted`.

**Fix:**

* the level estimate's reliability check;
* no acoustic veto of decoded continuation;
* phonetically credible tie extension;
* the defensibility limit;
* withheld feedback for an unresolved or undefensible boundary;
* the containment invariant.

**The same recordings re-analysed with the fix** (read only, never
committed), both engines:

* **Sentence 2:** `BOUNDARY_UNCERTAIN`, cut 19.50 s.
  * "underresourced" ends with its own /t/ at 19.28 s and has no extra
    sounds.
  * M4 evidence ends at 19.30 s; nothing lies past the cut.
* **Sentence 1:** unchanged (cut 10.12 s).
* **Sentence 3:** `TARGET_ONLY` (it had no continuation).

## Boundary-confidence correction (boundary vs analysis confidence)

A real attempt (44.9 s, sentence ending at about 41.3 s, followed by a 1 s
silent pause and continued reading) was withheld because the *whole
recording* was decoded at 0.548 differences per expected sound (raw
Wav2Vec2). That is just above the 0.5 defensibility limit, although its end
was clearly marked. The whole-recording cost measures decoding quality, not
where the sentence ends. Two separate decisions now apply:

* **Boundary confidence** (`boundary_confidence`) uses local temporal
  evidence:
  * `supported`: the pause and continued speech around the cut establish
    the end;
  * `clear_recording`: no such local evidence, but the recording was decoded
    clearly enough (≤ 0.5);
  * `insufficient`: neither. **Only this** withholds feedback as "could not
    be separated".
* **Analysis confidence** (`analysis`: `ok` / `low_confidence` /
  `unavailable`) is measured on the result actually shown, i.e. the
  sentence-only analysis after isolation. It never moves or invalidates a
  boundary. When it is low, the shown feedback carries a caution note.

**Local support** requires all of the following (failures are listed in
`evidence.boundary_support.failed`):

* a reliable speech-level estimate;
* enough continued speech;
* a pause of at least 250 ms before it, at least 80 % silent;
* the last 3 words decoded at ≤ 0.5 differences per sound;
* the final word reached, and not interrupted or repeated;
* the chosen cut falls on a silent frame. If it does not, support is
  revoked, and a boundary that existed only because of the support reverts
  to `BOUNDARY_UNCERTAIN`.

A final sound missing before such a pause (a weakly released /d/) is a
pronunciation observation, not an unknown boundary.

The heavy-noise protection is kept. Speech-dense noisy recordings
(calibration E) fail local support (unreliable level estimate, garbled end,
no silent pause), so they stay withheld at 20/20 on both engines, with
identical cuts.

**Safety around a boundary that rests on local support alone**
(`relied_on_local_support`):

* the sentence is always isolated (`target.wav`, `target_result.json`),
  never analysed as the full attempt;
* if the sentence-only re-check reports issues, feedback is withheld
  (`withheld_reason: "boundary"`);
* containment (M4, M5, M8 and words inside the cut) is checked on the
  analysis actually shown. If anything lies outside, everything is withheld
  (`withheld_reason: "containment"`) rather than trimmed, with its own
  wording.

A withheld analysis is not checked (`containment.checked: false`) and its
analysis confidence is not reported (`analysis.shown: false`).

**Results:**

* **The real attempt:**
  * `TARGET_PLUS_OVERFLOW`, cut 41.33 s, supported (1060 ms pause, 93 %
    silent, local cost 0.33);
  * sentence-only analysis at 0.49 (raw Wav2Vec2) and 0.46
    (OpenPronounce), with no evidence outside the sentence;
  * the final /d/ is reported as an omission.
* **Calibration:** 9 cases per engine moved from `BOUNDARY_UNCERTAIN` to
  `TARGET_PLUS_OVERFLOW` (weak final consonants before clear pauses). No
  withholding decision changed, and the reference snapshots of M4, M5 and
  the annotated words are unchanged.

## Validation (summary — see the M7 report for counts)

**Calibration set** (`scratchpad`, not committed): R01–R20 per engine, 13
variants each, every continuation also analysed on its sentence region.

| Case | Variant |
|---|---|
| clean | the recording as is |
| A | + 1 s of silence |
| B | immediate continuation (another sentence's recording) |
| C | continuation after a 200 ms pause |
| D | continuation after a 1 s pause |
| E | speech-dense: no pause, white room noise 9 dB below the speech |
| F | continuation 20 dB quieter |
| G | continuation 8 dB louder |
| H | final word said again, then continuation |
| I | half-spoken final word, then continuation |
| J | continuation 8 dB quieter |
| N0 | the sentence alone, noisy (control) |
| N1 | the sentence + 1 s, noisy (control) |

| | wav2vec2_raw | openpronounce |
|---|---|---|
| clean + A: TARGET_ONLY | 40 / 40 | 40 / 40 |
| continuation cases (B–J): never TARGET_ONLY | 180 / 180 | 180 / 180 |
| … evidence outside the sentence's region (containment) | 0 | 0 |
| … B, C, D, F, G, J: cut relative to the true sentence end | −110 … +30 ms | −110 … +30 ms |
| E (speech-dense, heavy noise): withheld (no defensible cut) | 20 / 20 | 20 / 20 |
| sounds attached to the final word, B–D (whole → region) | 2922 → 6 | 2870 → 6 |
| noisy controls N0 + N1 without continuation: TARGET_ONLY / withheld | 31 / 9 | 36 / 4 |
| boundary validator issues | 0 | 0 |

**Real-engine pytest (both engines).** Low-frequency room noise 6 dB below
the speech, with no pause, reproduces the manual-test profile:

* noise floor about −36 dBFS;
* level-estimate coverage < 0.15;
* intact decoding.

On R01+R05, R05+R11 and R11+R14 through the reader, every case is
`BOUNDARY_UNCERTAIN` with the cut at the sentence end and containment clean.
The full manual reading pattern (s1 + early s2 → s2 + early s3 → s3) also
passes, both through the service and in a real browser.

**Performance (per attempt, background worker):**

* Detection takes 4–5 ms on average, at most 16 ms.
* Its transient memory is about 10 MB for a 26 s attempt: frame energies
  from a cumulative sum.
* A clean attempt has no extra inference.
* With continuation, one extra inference runs on the sentence region: mean
  0.45 s, against about 0.8 s for the whole attempt.
* No extra inference runs when feedback is withheld.

**Tests:**

* `test_m7_boundary.py` (93 tests):
  * domain model, invariants, every detector scenario and false-positive
    guard;
  * a speech-dense replica of the failure (floor −36 dBFS, speech −27 dB);
  * a 20-case level × noise grid proving strong decoded continuation is
    never `TARGET_ONLY`;
  * coverage, defensibility, phonetic tie extension;
  * the plausibility and containment checks.
* `test_m7_service.py`:
  * "underresourced" + sentence 3 through the reader: no sentence-3 sound
    in M4/M5, its /t/ at 19.28 s not 22.82 s;
  * exact playback, next sentence untouched;
  * containment violation, plausible-continuation fallback, undefensible
    boundary → withheld (and left out of the summary);
  * the containment invariant over all scenarios.
* `test_m7_real.py`:
  * the earlier scenarios;
  * speech-dense room-noise continuations (3 pairs × 2 engines);
  * the manual reading pattern (2 engines);
  * browser runs for a single continuation and for the manual reading
    pattern.
* `js/reader_feedback.test.js`: the note wording, including withheld
  feedback.
* `test_m12_real.py` requires `TARGET_ONLY` and a single inference for
  every R01–R20 recording, both engines.

## Limitations

* Calibrated on one speaker's 20 benchmark recordings, with continuations
  made by joining two of them, and on one real manual test.
  * Thresholds: level-estimate coverage 0.6, defensibility 0.5,
    strong continuation 8 sounds.
  * The real attempts sit at 0.36–0.55 on the defensibility scale. Above
    0.5, feedback depends on a clear local pause (boundary-confidence
    correction). A noisier room with no pause between sentences may still
    get "withheld" rather than feedback.
* Noise without continuation is not M7's concern. A noisy sentence with
  nothing after it stays `TARGET_ONLY`, as before M7. But in very noisy
  recordings the decoder can produce sounds that look like continuation:
  4–9 of 40 noisy controls were withheld (false uncertainty, never false
  feedback).
* A continuation that starts with the sentence's final word ("…change.
  Change is…") is treated as a repeat. The cut then falls after the second
  word, and the state is `BOUNDARY_UNCERTAIN`.
* When the final word is only partly said before continuing, the boundary
  can still be `TARGET_PLUS_OVERFLOW` if the decoder recovers the word from
  context (3–4 of 20).
* A comparison uses the primary engine's boundary. If the other engine sees
  continuation where the primary did not, this is shown as disagreement in
  Details; the primary analysis is not redone. Disagreement is never taken
  as proof that there was no continuation.
* The M3–M5 lab page (`/`) records one sentence per analysis and is
  unchanged: M7 applies to the reader.
* Continued speech is only preserved and playable. Fillers, repetitions,
  false starts and their analysis are M8.
