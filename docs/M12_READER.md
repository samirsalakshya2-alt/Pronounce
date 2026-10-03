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

## Target confirmation (`reader/target.py`)

Separate from pronunciation feedback; never free speech recognition. From the
attempt's own alignment evidence:
`support = (expected sounds decoded as expected + substitutions whose expected
sound stays plausible, P ≥ 0.05) / expected sounds`.

| | support | both engines, benchmark |
|---|---|---|
| true pairs (R01–R20 with their own text) | 0.82–0.98 | 40 / 40 MATCH |
| partial reads (first half of the audio; an extra unread sentence) | 0.25–0.70 | 80 / 80 AMBIGUOUS |
| another sentence's text | 0.03–0.34 | 34 MISMATCH, 6 AMBIGUOUS, 0 MATCH |

MATCH ≥ 0.76 (midpoint of the gap between the lowest true pair and the highest
partial read); MISMATCH < 0.25 (below the lowest partial read, so a partly read
sentence is never hidden); no decoded speech → AMBIGUOUS; no evidence →
NOT_APPLICABLE. Calibrated in-sample on one speaker's 20 recordings.

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
* Target confirmation thresholds come from one speaker; another voice or a
  noisy room may need recalibration. A sentence read with a long pause inside
  may be AMBIGUOUS.
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
