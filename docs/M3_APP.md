# M3 — Pronunciation Lab app (MVP + listening notes)

M3 turns the M1/M2 evidence layer into a local application you can use in a
browser. M2 remains frozen; M3 consumes it unchanged.

## Start it

```bash
cd ~/Documents/Pronounce
uv run python scripts/run_app.py
```

It prints `Pronunciation Lab running at http://127.0.0.1:8642/` and opens that
page. Use **127.0.0.1**, not `localhost` (another project of yours has a cached
page on `localhost`). Stop with Ctrl+C. Options: `--port`, `--no-browser`,
`--no-warmup`, `--data-dir`, `--notes-file`.

## What you can do

1. **Provide audio** — record in the browser, upload a file (WAV; .m4a/.mp3/.webm
   via ffmpeg), or pick one of your local benchmark recordings.
2. **Type the sentence** you read (filled in automatically for benchmark recordings).
3. **Analyse** with the raw Wav2Vec2 recogniser (default) or OpenPronounce.
4. **See every word and sound** coloured by what the recogniser found:
   heard as expected · heard as a different sound · unclear · not detected ·
   not interpreted.
5. **Click a word** to see each sound: expected vs heard (with plain-English
   hints), the chance the expected sound was there, alternatives, and the exact
   time it was located.
6. **Listen**: play the whole recording, a word, or 0.3 s around a sound —
   always from the analysed audio, the timeline all times refer to.
7. **Note what you hear** for any sound (I hear /x/ · I hear /y/ · something
   else · can't tell). Notes are saved to `data/listening_notes/notes.jsonl`
   (gitignored) with references only — never audio.
8. **Start another analysis** without restarting; earlier ones stay in
   "This session" until the app stops.

## Decisions taken in M3 (from the M2 handoff)

| Handoff question | M3 decision |
|---|---|
| Q5 — normalisation in a product path | The **raw** recogniser is the default: OpenPronounce's normalisation loses vowel length and invented the "want to" omission in M2. OpenPronounce stays selectable, labelled as the same model. |
| Q8 — how to present uncertainty | Five categories, never "wrong"/"error"/score. A substitution whose expected sound is still plausible (≥ 0.05) or whose top candidate barely wins (margin < 0.2) is **unclear**. Words with unreliable alignment are **not interpreted**. Thresholds are M2's. |
| Q2 — listening verification | Exact playback plus **listening notes**: the first step toward the annotated subset (Q7, Q9). |
| Q1 — independent second engine | Not solvable in M3 (cloud blocked, WavLM unresolved by decision). The UI lists every engine and why it cannot run. |
| Q4 — accent-aware reference | Not available: this eSpeak has no Indian-English voice. The caveat names the en-us reference. |
| Q12 — revision pinning | The default (raw) engine is pinned; OpenPronounce still follows the cache's `main`. |

## Architecture

```
browser (static/index.html, app.js)  --HTTP-->  server.py (stdlib, 127.0.0.1)
                                                    |
                                                service.py
                    audio_input.py  <-------------- | --------------> diagnosis.py
          (16 kHz mono analysis WAV;               |               (M1 result -> user view;
           benchmark WAV copied byte-for-byte;     |                M2 thresholds & alignment rule)
           .m4a via the benchmark's ffmpeg cmd)    |
                                     M1 engines via safe_analyze, availability via M2 classify_engine
```

No M1/M2 source file was changed. No new dependency was added.

## Limitations

* One acoustic model behind both runnable engines; no independent confirmation.
* The recogniser's view only — no ground truth; R04-type intended substitutions may not be decoded.
* eSpeak en-us is the only reference pronunciation.
* Sound timings locate a sound; they are not durations. "Play sound" plays 0.3 s of context.
* Session analyses are temporary (removed when the app stops); only listening notes persist.
* Recording requires a browser with MediaRecorder; conversion of browser recordings needs ffmpeg.
* Single-user, localhost-only; analyses run one at a time.

## Manual test plan

1. **Benchmark R01 (compare with M2).** Benchmark recording → R01 → Analyse.
   Expect: 10 words; *three*, *want*, *to* unclear (amber, dashed); *that* not
   detected (purple, dotted); the rest as expected. Chips: expected 26 · unclear 3 ·
   not detected 3. Click *three*: /θ/ heard as expected (84%), extra /ɪ/ after it,
   /iː/ → /i/ unclear. Play word / play sound.
2. **Fresh recording.** Record tab → record "Very few people would value the view
   from this valley." → stop → type the sentence → Analyse. Check what was heard
   for *would*/*very*/*view*, listen, add a note.
3. **Upload your original Voice Memo** `data/New Recording 33.m4a` with the R05
   sentence: same result as benchmark R05 (*would* heard as /v/).
4. **Engine switch.** R01 with OpenPronounce: *want* becomes not detected and *three*
   expected (length ignored) — the M2 disagreement, shown as is.
5. **Failure states.** Analyse with an empty sentence; record < 0.3 s; upload a
   non-audio file; check the blocked engines cannot be selected.
