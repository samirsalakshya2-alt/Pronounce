# M4 — Phoneme Coach

M4 turns the exhaustive M3/M3.1 evidence into phoneme coaching: observations,
patterns, and practice targets — every statement traceable to evidence and to
an exact, playable part of the recording. It is an interpretation layer: **no
new recognition model, no extra inference.** It does not detect articulation;
the microphone gives acoustic evidence only.

## What you can do

After analysing a recording, click **Phoneme Coach** (beside *Full Recording
Feedback*):

* see findings grouped neutrally — *Recurring in this recording*, *Single
  occurrence — monitor*, *Ambiguous — listen and compare*, *Not detected by the
  recogniser*, *Extra sounds* (long single/ambiguous lists start collapsed);
* read each finding as a count with its counter-evidence ("In N of M
  occurrences of /x/ … /x/ was heard as expected in K other occurrences …");
* **View evidence**: each occurrence with expected → heard, interpretation and
  confidence with reasons, P(expected) and P(competitor), voicing / relative
  energy, exact time, context (position, neighbours, cluster, stress only when
  known), and **▶ Play exact occurrence** / **▶ Play word**;
* **Practice / Listen & compare / Listen & monitor**: sound → word → sentence,
  with your own occurrences to play; general guidance for common contrasts,
  explicitly not a statement about your articulation.

The coach states how many sounds were consistent with the expected sound and
are therefore not listed — and that "consistent" is not proof of a canonical
pronunciation (R04's deliberate /θ/→/t/ was still decoded as /θ/).

## Architecture

```
M1 PronunciationResult (already produced by M3; same evidence, no re-run)
   │
   ├─ M3 diagnosis.build_view ............ word/sound view (unchanged)
   └─ M4 coach.build_coach
         phoneme_coach.build_observations   one observation per expected sound + per inserted sound
         phoneme_patterns.build_patterns    patterns + neutral display groups
         practice.build_targets             practice targets (sound / word / sentence)
         coach.validate_coach               invariants, reported as coach.integrity
```

`service.py` attaches the result as `view["coach"]`; M4's time goes to
`view["processing"]["coach_ms"]`. Files: `app/phoneme_coach.py`,
`app/phoneme_patterns.py`, `app/practice.py`, `app/coach.py`; UI in
`static/app.js` / `index.html` / `style.css`. Reused unchanged: M2's
`PLAUSIBLE_POSTERIOR`, `AMBIGUOUS_MARGIN`, `alignment_suspect_words`,
`FUNCTION_WORDS`, `is_vowel`; M3's `sound_window` / `word_window`.

## Data model

**PhonemeObservation** (`coach.observations[]`): `id`, `kind` (sound /
insertion), `sound_index` (= M3 sound index), `word`, `word_index`,
`expected`, `observed`, `type`, `confidence`, `competitor`,
`expected_posterior`, `observed_posterior`, `competitor_posterior`, `nbest`,
`reasons`, `reference_note`, `span_ms`, `timing_source`, `play_ms`,
`word_play_ms`, `context` {`position_in_word`, `word_position`,
`previous_phone`, `next_phone`, `word_boundary_before/after`,
`in_consonant_cluster`, `sentence_position`, `stress`, `stress_known`},
`acoustic` {`relative_energy`, `energy_db`, `voiced`, `f0_hz`,
`zero_crossing_rate`, `spectral_centroid_hz`} (null when not measured, never
0), `evidence` {`engine`, `frame_span`, `alignment_operation`,
`m3_sound_index`, `invalid`}.

**Pattern** (`coach.patterns[]`): `id`, `kind` (contrast / detection /
insertion), `expected`, `contrast`, `class`, `context`, `group`,
`evidence_strength`, counts, `words`, `word_positions`, `observation_ids`,
`counter_evidence_ids`, `reference_note`, `summary`.

**PracticeTarget** (`coach.practice_targets[]`): `id`, `pattern_id`, `kind`
(practice / compare / monitor), `target_phoneme`, `contrast_phoneme`,
`reason`, `confidence`, `supporting_observation_ids`, `example_words`,
`occurrences` (with exact `play_ms`), `levels` {sound, word, sentence}.

## Interpretation semantics

| Type | When | Confidence |
|---|---|---|
| expected | decoded as expected, runner-up not within 0.2 | high if P(expected) ≥ 0.5, else moderate |
| ambiguous | decoded as expected but runner-up within 0.2; or a substitution whose expected phone is still plausible (P ≥ 0.05), whose top two are within 0.2, or whose expected posterior is unavailable | low |
| substitution_candidate | another phone decoded, expected implausible (P < 0.05), runner-up not close | high if P(observed) − P(expected) ≥ 0.5, else moderate |
| omission_candidate | nothing decoded, expected implausible | low — *not detected ≠ absent* |
| weak_evidence | nothing decoded, expected still plausible | low |
| insertion | decoded phone with no expected phone | low |
| not_interpreted | alignment suspect (M2 rule: word or neighbour with ≥ 3 insertions) or invalid evidence (missing/reversed timing, probability outside [0,1] or NaN, unknown operation, missing word/phoneme) | none |

Estimated (not decoded) locations cap confidence at moderate; when the regular
playback window would exceed 800 ms, playback starts 150 ms before the estimate
and lasts at most 800 ms (estimated regions of several seconds occur, e.g.
across a long pause). Invalid
numbers are never passed on as values: they become null and are kept, as
found, in `evidence.invalid`. N-best values are peak posteriors over a span and
are not expected to sum to 1; they are not renormalised.

## Thresholds (all reported in `coach.thresholds`, all tested at their boundaries)

| Name | Value | Source |
|---|---|---|
| plausible expected posterior | 0.05 | M2 / OpenPronounce |
| close runner-up margin | 0.2 | M2 |
| high-confidence dominance | 0.5 | M4 |
| high-confidence expected posterior | 0.5 | M4 |
| consistent: min occurrences / words / high-confidence | 3 / 2 / 2 | M4 |

## Patterns

Observations of the same expected phoneme pointing the same way form a
pattern; as-expected observations of that phoneme are counted as
counter-evidence. Classes, always "in this recording":

* **one_off** — one observation → "Single observation — monitor for recurrence."
* **repeated** — two or more.
* **consistent** — ≥ 3 occurrences, ≥ 2 words, ≥ 2 high-confidence, and at
  least as many deviations as as-expected decodes → "Recurring pattern in this
  recording."
* **context_specific** — ≥ 2, all in one word position, while the phoneme was
  heard as expected elsewhere → "… specific to that context, not a general /x/
  pattern."

Display order is neutral (recurring, single, ambiguous, not detected, extra
sounds) and time-ordered within a group. There is no score and no ranking.

## Reference and alignment safety

* Pairs explainable by the eSpeak en-us inventory (length marks, reduced
  vowels, rhoticity, US mergers, flaps, e.g. /ʊɹ/–/uː/, /ɔ/–/ʌ/, /ɚ/–/ə/,
  /iː/–/i/) carry: "This may reflect the reference accent / phoneme inventory
  (eSpeak en-us) rather than a pronunciation difference."
* Vowel contrasts seen only in function words carry a note that those words
  have several accepted pronunciations.
* Alignment-suspect evidence is never coached (R08's "before the evening").
* OpenPronounce and raw Wav2Vec2 share one acoustic model; the coach says so
  and never treats their agreement as confirmation.

## Validation (final run, 2026-10-02)

Command: `uv run pytest tests/benchmark tests/phase0 tests/app --deselect tests/phase0/test_audio_record.py::test_audio_record`
(the deselected test records from the microphone).

| | |
|---|---|
| collected | 551 |
| passed | **550** |
| failed / skipped | 0 / 0 |
| deselected | 1 |
| warnings | 1 (known transformers notice from the M1 missing-model test) |

Pre-M4 baseline with the same command: 428 passed. +122 M4 tests:
84 unit / negative / invariant (`test_m4_coach.py`), 38 real-audio /
integration / repeatability / browser (`test_m4_real.py`); JS unit tests
16 → 20. The suite passes in either directory order (a latent `conftest`
import-order bug in the test infrastructure was fixed by moving shared helpers
to `tests/app/apphelpers.py`).

Fault injection: 24 defects injected one at a time (reversed substitution,
threshold comparison, removed/duplicated/dropped evidence, altered posterior,
shifted timestamp, ambiguous→high, unknown stress→unstressed, alignment
handling removed, target without evidence, one-off promotion, counter-evidence
ignored, context detection removed, omission→high, reference note removed,
invalid posterior passed through, playback cap removed, runtime integrity
disabled, derived-timing cap removed, severity ordering, two JS display
defects, coverage miscount): **24 caught, 0 missed, all restored and
checksum-verified.** Re-run after the Recording 49 correction below: still
24/24 caught — no defect depended on a Recording 49 assertion.

Real audio (ground truth): benchmark R01, R04, R05, R07, R08, R11, R14, R17,
R18, R19, R20 — the only recordings whose pronunciation findings are asserted.
Integrity clean on all; coach identical when rebuilt from the stored M2 results
(proves no extra inference); repeat analyses identical.

### New Recording 49 — UI / smoke / performance only, findings NOT validated

The exact target text originally supplied for `New Recording 49.m4a` could not
be recovered. The app keeps analyses in memory only; the one persisted trace,
`data/listening_notes/notes.jsonl`, holds a typed target text for a *different*
audio (an in-app recording, `recording.m4a`, sha256 `4c89c732…`; Recording 49 is
`40c0a588…`), so it cannot be attributed to Recording 49. An earlier M4 draft
used a transcript reconstructed from the audio by ASR as the target and asserted
findings against it; that was circular and has been removed. Recording 49 now
only checks that a 34 s real upload runs end to end with clean coach integrity,
playback inside the audio, capped estimated playback, coach time < 100 ms, and
that the browser UI renders and collapses long groups. The ASR text is used
only as smoke-test input (`SMOKE_TEXT49`) and asserts nothing about
pronunciation. The cost/costs discrepancy with the M3.1 report is therefore
unresolved; one possible explanation is that the M3.1 report came from a
different take of the same passage (the in-app recording above).

Performance (CPU): M4 coach 0.3–1.5 ms per recording (Recording 49, 35 words:
1.46 ms, vs 1.62 s model inference and 2.27 s end-to-end); re-fetching a view
3.4 ms.

## Manual validation

Run in a real (headless) Chrome against the real app; screenshots reviewed.

* **R05** — /w/↔/v/ in "would": single, high confidence, P(/w/) 2% vs
  P(/v/) 86%, exact playback; three ambiguous vowels framed "Listen & compare".
* **R01** — "that" not detected (×3) with "does not prove the sound was
  absent", estimated location and capped playback; /ɔ/–/ʌ/ and /iː/–/i/ with
  reference-accent notes.
* **R04** — no /θ/ finding (the recogniser decoded /θ/); the coverage note now
  states what that silence does and does not mean.
* **Recording 49** — used to review the UI on a long recording (grouping,
  collapsing, wording, capped playback). Its pronunciation findings are not
  validated (see above).

Changes made because of the manual test: capped playback of long estimated
regions; insertion strength shown as "low"; function-word note; collapsing of
long single/ambiguous groups; the coverage note.

## Known limitations

* One acoustic model behind both runnable engines; no independent confirmation;
  no ground truth.
* Patterns are per recording (session/longitudinal aggregation is M7).
* New Recording 49's pronunciation findings are not validated: its original
  target text is unknown. Supplying the exact text would allow a validation.
* eSpeak en-us is the only reference; the reference-variant list is a curated
  heuristic.
* Contrast guidance exists for 12 common contrasts; others get none rather
  than invented advice.
* Stress is the dictionary's (raw engine only); nothing observes stress.

## M5 boundary

M4 never labels a sound chewed, swallowed or reduced, and never infers
anything from duration (CTC spans are not durations). "Weak evidence" means
only that the expected phone stayed plausible without being decoded. M5 will
combine duration, energy, spectral features, voicing, posterior uncertainty,
transitions, position, stress, rate and connected-speech context; M4's
observations already carry the acoustic fields and context it will need.
