# M12 — Integrated Article Reading Experience

Pronounce becomes an article reader that happens to understand pronunciation.
Paste an article; it is split into sentences and shown as an article. Click a
sentence to start reading it, click the next sentence when you get there. Each
sentence is recorded, uploaded and listened to in the background while you keep
reading; a quiet mark appears after each sentence, and a click on it opens what
was heard, with exact playback of your own recording. At the end, "Finish
reading" gives a summary of this reading.

Run: `uv run python scripts/run_app.py` → the reader opens at
http://127.0.0.1:8642/read (the M3–M5 lab stays at http://127.0.0.1:8642/).
Reading sessions and their recordings are kept outside the repository, in
`~/.pronunciation_lab/reader/` (`--reader-dir` to change; nothing is created
until the reader is used).

The microphone gives acoustic evidence, not a view of the articulators. All
pronunciation interpretation is M4/M5's, unchanged; M12 adds no score, no
ranking and no new pronunciation logic.

## Using it

* **Select a sentence** → recording starts for that sentence (the first time,
  the browser asks for the microphone). **Select the next one** → the previous
  recording ends exactly where the new one begins; it is analysed in the
  background.
* **Pause** (Space) finishes the current sentence and keeps the microphone
  ready; **Resume** records the active sentence again (a new attempt).
  **Stop** (Esc) finishes and releases the microphone. **Finish reading** ends
  the reading and builds the summary once every sentence has been listened to.
  J / K select the next / previous sentence.
* Marks after sentences: `…` being listened to · a number = things to notice +
  things to compare · `✓` nothing stood out · `?` the recording may not be this
  sentence · `!` needs attention (too short, failed, interrupted).
* The article is always shown complete, as continuous paragraphs, and **the
  article itself is the feedback**: once a sentence has been analysed, its own
  words (original text — capitals, punctuation, order; never rebuilt from what
  the recogniser heard) are underlined by their category — heard as expected
  (faint solid) · heard as a different sound (solid) · unclear (dashed) · not
  detected (dotted) · not interpreted (neutral dotted). Sentences not analysed
  yet stay plain text; a legend in the rail lists the categories present.
* Click an underlined word for the MVP sound table under its sentence
  (expected, heard, what the recogniser found, chance of the expected sound,
  alternatives, where, ▶ Play sound / ▶ Play word); one panel at a time —
  another word moves it, the same word closes it. Clicking a word never starts
  a recording; "↻" after the sentence reads it again.
* A quiet note after each analysed sentence keeps the concise feedback
  ("4 things to notice · 9 to compare") and "Details" for the deeper layers:
  Patterns and practice (M4) → Connected speech (M5) → Compare with the other
  listening model, plus ▶ Listen to this recording. The reader and the lab
  share one word renderer (`wordDetail`); the lab adds its listening notes.
* If a recording may not be the sentence, the drawer asks **Keep this
  recording** / **Re-record** and shows no pronunciation feedback until you
  keep it.

## Architecture (as implemented)

```
Browser /read                                        Python app process
  reader.js (DOM) ── reader-core.js (pure)            LabServer (+ reader routes, reader/http.py)
   CaptureController: one MediaStream, one             ReaderService (reader/service.py)
   AudioContext, AudioWorklet (capture-worklet.js)       segmenter · model · store (JSON, atomic)
   → sample-indexed synchronous cuts                     upload → prepare_audio → 202
   PcmRing (bounded) → encodeWav → UploadQueue ──POST──► AnalysisWorker (1 thread, FIFO, primary first)
   poll GET /api/sessions/<id>?since=<rev> ◄────────────    analyze_pipeline (app/pipeline.py, shared with the lab)
  reader-feedback.js + app.js renderers                    under the existing inference lock
                                                          target confirmation · compact feedback · summary
```

* **Phase 1** — `AnalysisService._run`'s body is `app/pipeline.py`
  (`analyze_pipeline`, `build_analysis_view`); the lab and the reader produce
  identical evidence and views.
* **Domain** (`reader/model.py`): Article → ReadingSession → Segment →
  RecordingAttempt → AnalysisJob → AnalysisResult (`result.json` + `view.json`
  per job); PlaybackReference; SessionSummary. session_id and attempt_id are
  client-generated UUID4 hex (validated, reuse rejected); segment_id is
  `<article_id>:<index>`. State machines are explicit tables; every other
  transition is rejected.
* **Store** (`reader/store.py`): JSON files, atomic writes (temp + fsync +
  rename); audio and `result.json` are write-once (hard link from temp, fails
  if present); append-only `events.jsonl` (a torn last line is skipped);
  per-session locks; `rev` increments on every change.
* **Capture**: attempt A owns `[start, cut)`, B owns `[cut, …)` of the same
  run; the cut is a synchronous read of the received-sample count; slicing,
  WAV encoding (16-bit PCM mono at the context rate), upload and analysis are
  deferred. Paused audio belongs to no attempt. The ring keeps only samples of
  open or not-yet-encoded attempts. 60 s per attempt (= the server limit) ends
  the attempt at exactly 60 s.
* **Server**: an upload is validated (WAV must be 16-bit PCM mono; its frame
  count must equal `end_sample − start_sample`; the segment must match the
  started attempt), written once, converted by the existing `prepare_audio`
  (48 kHz → 16 kHz "resampled"; a 16 kHz benchmark WAV is "copied"
  byte-identically), a primary job is queued and the request returns 202. The
  worker runs one job at a time under the lab's inference lock (so lab and
  reader never infer concurrently), primary before comparison jobs.
* **Feedback** (`reader/feedback.py`, `static/reader-feedback.js`): the M4/M5
  renderers were moved out of the lab-only block of `app.js` into
  `createEvidenceRenderers({el, play, playWhole})` and are used by both pages.
  Every Listen control plays one window of one attempt's analysis WAV and
  records `{session_id, segment_id, attempt_id, job_id, timeline:
  "analysis_wav", play_ms}`.

## Decisions made during implementation (spec items marked UNRESOLVED)

| Item | Decision | Why |
|---|---|---|
| Store location | `~/.pronunciation_lab/reader` (outside the repo), lazy creation | personal audio can never be staged by git |
| Playback timeline | the attempt's analysis WAV | every M1–M5 timing refers to it; the client WAV and the analysis WAV share a timeline (no AAC priming) |
| Pre-/post-roll | none: owned intervals only | padding would put neighbouring speech into an attempt (insertions, target confusion) |
| Capture batch | one 128-frame render quantum per message | cut lag ≤ 128 samples (2.7 ms at 48 kHz) |
| Page hidden | auto-pause (`end_reason = page_hidden`) | an attempt never spans time away from the page |
| Resume | a new attempt of the sentence active when paused | the sentence may have been interrupted |
| Audio upload method | `POST …/audio` (not PUT) | an existing M3 test requires PUT → 501 server-wide |
| Default disposition | none; "kept" only by the user | "kept" must mean the user confirmed a doubtful recording |
| Compact line | count of M4 patterns (recurring/single/not detected) + M5 interpreted candidates, de-duplicated by observation; ambiguous → "to compare" | a count of listed items, never a score |
| Summary inputs | per sentence the latest attempt that is not discarded / marked for re-recording, analysed by the session's primary engine, target MATCH or kept by the user | spec: kept, target-confirmed, primary-engine |
| Summary scope | this session only; "this recording" → "this reading" in M4 texts | cross-session is a later milestone (M7) |
| IndexedDB buffering of un-cut audio | not implemented | not needed for a first implementation; a page that crashes mid-sentence loses that sentence's audio (the attempt is marked INTERRUPTED) |
| Keyboard | Space, J/↓, K/↑, Esc | |
| Fonts | system serif stack (Iowan Old Style, Palatino, Georgia) | local assets only |

## Target confirmation (`reader/target.py`) — tc-2

Target confirmation answers *"did the reader probably read this sentence?"* It is separate from:

* pronunciation feedback (M4/M5);
* the sentence boundary (M7);
* summary eligibility.

It never uses free speech recognition: only the analysis's own decoded speech sounds and eSpeak's
pronunciation of the article's known sentences.

### Why tc-1 was replaced

tc-1 measured phone accuracy:

`support = (expected sounds decoded as expected + plausible substitutions) / expected sounds`, MATCH ≥ 0.76.

That conflated identity with pronunciation. In a manual test, three sentences were read slowly in a noisy
room. Every word was found in order across each recording, yet most sounds were decoded as other sounds
(support 0.19–0.33, with non-English tokens from the multilingual model). They were called MISMATCH or
AMBIGUOUS ("may not match"), and the summary included 0 of 3. In an earlier session, two clearly correct
readings (support 0.73 and 0.68) were AMBIGUOUS for the same reason.

### How tc-2 works

tc-2 is contrastive. Each candidate sentence's expected sounds are aligned to the decoded sounds, folded
into a coarse shared phone space (no length, stress, tone digits or diphthong/rhotic detail), with
pronunciation-tolerant costs:

* same-class substitution 0.6, cross-class 1.0;
* missing sound 1.0, extra sound 0.4;
* lead-in and trailing speech are free.

Three measures come out of it:

* **Order evidence** (`order_margin`): the target's fit minus its fit with the same words shuffled. Is this
  sentence there, in this order? Pronunciation errors lower both fits alike.
* **Contrast** (`contrast_margin`): the target's fit minus the best fit among the article's sentences up to
  three before and after (the realistic confusions while reading).
* **Coverage:** the share of substantial words (≥ 3 expected sounds) with an aligned same-class sound.
  Dropped weak forms such as "the" or "to" don't count against it.

| State | Rule | Meaning |
|---|---|---|
| MATCH | order ≥ 0.18 and coverage ≥ 0.8 | "Recording appears to match this sentence." |
| LIKELY_MATCH | this sentence fits best (contrast ≥ 0.08), coverage ≥ 0.9, weak order evidence | "probably this sentence, but the evidence is weak" |
| MISMATCH | order ≤ 0.10 **and** another sentence of the article fits clearly better (contrast ≤ −0.25) | "appears to contain a different sentence" |
| AMBIGUOUS | anything else (partial read, too little evidence, no known alternative) | "I couldn't confidently tell whether this recording is this sentence." |
| NOT_APPLICABLE | the analysis produced no evidence | |

* **MISMATCH needs a better-fitting sentence.** Without one, an unknown or unrelated recording is AMBIGUOUS.
  Reading ahead into the next sentence is not a mismatch: the target's order evidence is still there.
* **Very short sentences** (fewer than 3 words or 8 expected sounds) keep tc-1's phone support.
* **Two engines.** When the other local engine is compared, its identity is stored beside the primary's. A
  primary MISMATCH that the other engine does not share becomes AMBIGUOUS. Agreement is never independent
  confirmation, because the engines share an acoustic model.
* **Cost:** about 15 ms per attempt (alignment plus cached eSpeak G2P), and no inference.

### Calibration (both engines, identical outcomes)

| Set | wav2vec2_raw | openpronounce |
|---|---|---|
| R01–R20 with their own text | 20 MATCH | 20 MATCH |
| …followed by another sentence (immediate, quiet, repeated or half-said final word) | 80/80 MATCH | 79 MATCH, 1 AMBIGUOUS |
| another sentence's text (±3 neighbours as alternatives) | 31 MISMATCH, 9 AMBIGUOUS, **0 MATCH/LIKELY** | 31 MISMATCH, 9 AMBIGUOUS, **0 MATCH/LIKELY** |
| first half of the recording | 20 AMBIGUOUS | 20 AMBIGUOUS |
| true sentence in heavy noise (M7 speech-dense set) | 19 AMBIGUOUS, 1 LIKELY, **0 MISMATCH** | 20 AMBIGUOUS |
| true sentence, noisy, nothing after | 4 MATCH, 7 LIKELY, 9 AMBIGUOUS, 0 MISMATCH | 4 MATCH, 6 LIKELY, 10 AMBIGUOUS, 0 MISMATCH |
| real manual readings (7, two sessions, evaluated locally only) | 3 MATCH, 2 LIKELY, 2 AMBIGUOUS | same |
| …the same recordings scored against the article's other sentences (14) | 6 MISMATCH, 8 AMBIGUOUS, **0 MATCH/LIKELY** | 5 MISMATCH, 9 AMBIGUOUS, 0 MATCH/LIKELY |

## Four separate decisions (`reader/status.py`)

Each attempt's `status` is derived, never stored, and appears in the snapshot. The reader's wording and the
summary both use it, so they cannot disagree.

| Decision | Values |
|---|---|
| A. identity | MATCH · LIKELY_MATCH · AMBIGUOUS · MISMATCH · TOO_SHORT · FAILED |
| B. boundary | M7's state (authoritative) |
| C. feedback | `shown` (with a caution note when the sentence-only analysis has low confidence) · `withheld_boundary` (M7: the sentence's end could not be established) · `withheld_containment` (the separated sentence's analysis did not stay inside it) · `hidden_identity` (a different sentence) · `none` |
| D. summary | included, or a distinct reason |

The summary reasons are:

* marked for re-recording;
* discarded;
* not analysed;
* appears to contain a different sentence;
* sentence boundary uncertain — recording preserved, feedback withheld;
* analysis not contained in the sentence — recording preserved, feedback withheld;
* could not confirm it is this sentence — keep it to include it.

**Normal and probable readings need no action.** MATCH and LIKELY_MATCH are included in the feedback
summary automatically when their feedback is safe. When M7 withheld it, they still count as recorded and
identified. **Keep** is the override for an uncertain identity (AMBIGUOUS). It means *"I confirm it is this
sentence"* and includes the attempt if its feedback is otherwise safe. **It never unlocks unsafe feedback.** A recording that
appears to contain a different sentence, or whose end M7 could not place, stays without feedback when kept.
The reader then says so and offers Re-record.

**Reading summary — recorded vs feedback (sum-2).** The summary never says "0 of N included" for a session
that was read. It reports two lines; the feedback categories are exclusive and add up to the recorded
sentences:

* *5 of 5 sentences recorded · 3 identified · 2 uncertain* — what was read. "Recorded" means MATCH,
  LIKELY_MATCH or AMBIGUOUS, not discarded; a different sentence is listed as "seems to be a different
  sentence", not as recorded.
* *1 sentence in the feedback below · 2 withheld because the sentence boundary was uncertain · 2 waiting for
  you to keep or re-record* — what the pronunciation, connected-speech and fluency feedback covers.

**Fluency in the summary (sum-3).** Below pronunciation and connected speech, a "Fluency" section gives one
line built from the summarised sentences only. It has the number of fluency things to notice and in how many
sentences, what recurs across sentences, and the range of speech rates. Up to three ▶ moments follow, each
playing an exact window with its stated context. It is never a score; see `docs/M8_FLUENCY_DISFLUENCY.md`.

**Order of *Your reading*.** Coverage lines, then **This reading** ("This reading only": a current-reading
diagnosis of this session's summarised sentences: *Major improvement areas* (every qualifying one, in
Pareto order, or "No major pronunciation
pattern was strong enough to call out in this reading."), *Already stable in this reading*, *Fluency*,
*Cautions*), then **What to practise now** ("Based on your recent readings", unchanged), then the detailed
report. See `docs/M9_PARETO_COACHING.md`.

**What to practise now (M9).** In *Your reading*, the 0–3 practice actions issued when the summary
was built (stored as `coaching.json`, so reopening never changes them). They replace the previous unbounded
"Sounds worth practising" list in the UI. The summary data (`practise`, `patterns`, `reductions`, `fluency`)
is unchanged. See `docs/M9_PARETO_COACHING.md`.

**MATCH + BOUNDARY_UNCERTAIN** is a valid state: "Your reading appears to match this sentence, but I couldn't
safely determine where it ended."

**Nothing is ever deleted.** Re-record marks the old attempt (`rerecord_requested`) and keeps its audio,
analysis and history. The summary uses each sentence's latest eligible attempt, which may be an older kept
attempt if the retake is not eligible.

## Validation

`uv run pytest tests/benchmark tests/phase0 tests/app --deselect tests/phase0/test_audio_record.py::test_audio_record`
— see the final M12 report for the counts. M12 test files:
`test_m12_pipeline.py` (pipeline = lab, both engines), `test_m12_domain.py`
(segmenter, every legal/illegal transition, spec-pinned tables, ids, store),
`test_m12_reader_service.py` (worker, 202 path, one inference, priority, FIFO,
ownership validation, too short / rejected / interrupted kept, retry, restart
recovery, worker shutdown, HTTP, keep-alive), `test_m12_capture.py` + `js/reader_core.test.js`
(contiguity, rapid switching, ring bound, 60 s limit, device loss, WAV bytes,
upload retry), `test_m12_feedback.py` + `js/reader_feedback.test.js`,
`test_m12_target.py`, `test_m12_summary.py`, `test_m12_real.py` (real engines:
the reader in headless Chrome with a fake microphone fed by R01, recovery in the
browser, target calibration, R01–R20 through the reader for both engines =
lab evidence, summary, the M2 "want to" disagreement via the reader's
comparison).

Headless Chrome needs `--disable-features=AudioServiceSandbox` for the fake
microphone to read a file on macOS (test browser only). Spike: R01 through the
fake microphone → AudioWorklet → 48 kHz WAV → `prepare_audio` gives the same
32/32 expected-sound decodes and spans as M2 for both engines (one weak inserted
/ɪ/ differs).

## Limitations

* AudioWorklet capture was verified in Chrome 154 (automated); Safari 27 has
  AudioWorklet but was not tested here — check it manually.
* Target confirmation (tc-2) is calibrated on one speaker's benchmark plus two
  real manual sessions. A recording whose decoded sounds are close to noise is
  AMBIGUOUS, even when it is the sentence; Keep includes it. MISMATCH needs
  another sentence of the article (±3) that fits clearly better.
* The summary aggregates one session; patterns across sessions are not
  computed.
* An attempt's audio is uploaded when the sentence ends; a browser crash in the
  middle of a sentence loses that sentence's audio.
* Stress and rhythm are not analysed (later milestones).

## Plug points for M6–M11

* New analysis layers: add to `build_analysis_view` (the shared pipeline) with
  a version and an integrity result; they are stored per job (`view.json`,
  rebuildable from `result.json` via `ReaderService.rebuild_view`).
* Compact line: extend `reader/feedback.py` with counts of the new layer's listed
  items (no scores).
* Drawer: add a section in `reader-feedback.js` using `createEvidenceRenderers`
  conventions; every Listen control must carry a playback reference.
* Session-level aggregation: extend `reader/summary.py`; inputs stay the
  eligible attempts, and `validate_summary` checks every example's source.
* Both local engines stay available per attempt (comparison jobs); nothing
  merges them.
