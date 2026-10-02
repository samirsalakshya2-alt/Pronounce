"""M5 — reduction evidence: measurements only, no interpretation.

For every interpretable sound observation (M4) this module measures, from the
same M1 evidence (no new inference):

* **contextual temporal slot** — the interval between the neighbouring decoded
  sounds around the target (end of the previous decoded sound to the start of
  the next one). This is *context*, NOT the duration of the target phoneme:
  CTC spans mark where the recogniser was most sure of a sound, and the gap
  between neighbours includes transitions and silence. It is compared only
  with slots of the same expected phoneme in the same recording;
* **pause proximity** — a gap of at least the M2 pause length (250 ms) next to
  the sound, or no decoded sound at all for that long around it;
* **within-recording comparisons** of relative energy for the same phoneme;
* **voicing** where the expected sound is normally voiced;
* **speaking rate** — decoded sounds per second excluding pauses (the M2
  measure) for the recording and for the stretch between pauses that contains
  the sound;
* **position** — word position (M4), a syllable position derived from vowel
  positions in the expected phones (the engines do not report syllables), and
  the connected-speech contexts of `connected_speech`.

Comparisons are rank-based. A sound is "shorter/lower than every comparable
occurrence" only when at least MIN_COMPARABLES other occurrences exist; if the
occurrences were exchangeable, the chance of being the strict minimum of n+1 is
1/(n+1) <= 0.25, so this is a weak signal by design and only ever one of
several evidence streams.
"""

from __future__ import annotations

import math
from typing import Any

from pronunciation_lab.app.connected_speech import contexts, expected_voiced
from pronunciation_lab.app.phoneme_coach import HIGH_EXPECTED, timing_ok
from pronunciation_lab.benchmark.analysis import FUNCTION_WORDS, PAUSE_MS, is_vowel
from pronunciation_lab.benchmark.schema import PronunciationResult

# Fewest other occurrences of the same phoneme needed before a within-recording
# comparison is reported (see module docstring for the rationale).
MIN_COMPARABLES = 3
# A sound that was not decoded has "no room" when its neighbouring decoded
# sounds leave at most this many model frames for it (CTC needs at least one
# frame to emit a phone).
NO_ROOM_FRAMES = 1
DEFAULT_MS_PER_FRAME = 20.0

TEMPORAL_NOTE = (
    "Contextual temporal slot: the time between the neighbouring decoded sounds. It is context, not the "
    "duration of this sound."
)

EVIDENCE_THRESHOLDS = {
    "pause_ms": PAUSE_MS,
    "min_comparables": MIN_COMPARABLES,
    "no_room_frames": NO_ROOM_FRAMES,
    "weak_support_expected_posterior_below": HIGH_EXPECTED,
}


def _anchors(observations: list[dict[str, Any]]) -> list[tuple[float, float, str]]:
    """Every decoded sound (matched, substituted or inserted) with engine timing."""
    out = []
    for o in observations:
        span = o.get("span_ms")
        if span and o["timing_source"] == "engine" and timing_ok(span[0], span[1]):
            out.append((float(span[0]), float(span[1]), o["id"]))
    return sorted(out)


def speaking_rate(anchors: list[tuple[float, float, str]]) -> dict[str, Any]:
    """Decoded sounds per second excluding pauses (M2 `phones_per_s_excluding_pauses`), plus stretches."""
    if not anchors:
        return {"phones_per_s_excluding_pauses": None, "pauses": 0, "pause_ms_total": 0.0, "stretches": []}
    gaps = [(a[1], b[0]) for a, b in zip(anchors, anchors[1:])]
    pauses = [(s, e) for s, e in gaps if e - s >= PAUSE_MS]
    span = anchors[-1][1] - anchors[0][0]
    pause_total = sum(e - s for s, e in pauses)
    rate = len(anchors) / ((span - pause_total) / 1000.0) if span > pause_total else None

    stretches, start, count = [], anchors[0][0], 0
    bounds = iter(pauses + [(math.inf, math.inf)])
    nxt = next(bounds)
    for a in anchors:
        if a[0] >= nxt[1]:
            stretches.append({"start_ms": start, "end_ms": nxt[0], "sounds": count})
            start, count, nxt = a[0], 0, next(bounds)
        count += 1
    stretches.append({"start_ms": start, "end_ms": anchors[-1][1], "sounds": count})
    for s in stretches:
        dur = s["end_ms"] - s["start_ms"]
        s["phones_per_s"] = s["sounds"] / (dur / 1000.0) if dur > 0 else None
    return {"phones_per_s_excluding_pauses": rate, "pauses": len(pauses), "pause_ms_total": pause_total,
            "stretches": stretches}


def syllable_position(phones: list[str], i: int) -> str | None:
    """Onset / nucleus / coda from vowel positions in the expected phones (derived, not reported by engines)."""
    vowels = [k for k, p in enumerate(phones) if is_vowel(p)]
    if not vowels:
        return None
    if is_vowel(phones[i]):
        return "nucleus"
    if i < vowels[0]:
        return "onset"
    if i > vowels[-1]:
        return "coda"
    return "between_vowels"


def measure(result: PronunciationResult, observations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Measurements for every interpretable sound observation, keyed by observation id."""
    ms_per_frame = float(result.engine_evidence.get("frame_clock", {}).get("ms_per_frame") or DEFAULT_MS_PER_FRAME)
    anchors = _anchors(observations)
    rate = speaking_rate(anchors)
    word_phones = {wi: [p.expected.phoneme for p in w.phonemes] for wi, w in enumerate(result.words)}

    sounds = [o for o in observations if o["kind"] == "sound" and o["type"] != "not_interpreted" and o["span_ms"]]
    base: dict[str, dict[str, Any]] = {}
    for o in sounds:
        start, end = o["span_ms"]
        prev = max((a for a in anchors if a[2] != o["id"] and a[1] <= start + 1e-6), key=lambda a: a[1], default=None)
        nxt = min((a for a in anchors if a[2] != o["id"] and a[0] >= end - 1e-6), key=lambda a: a[0], default=None)
        slot = (nxt[0] - prev[1]) if prev and nxt else None
        gap_before = (start - prev[1]) if prev else None
        gap_after = (nxt[0] - end) if nxt else None
        decoded = o["timing_source"] == "engine"
        pause_before = gap_before is not None and gap_before >= PAUSE_MS
        pause_after = gap_after is not None and gap_after >= PAUSE_MS
        # A sound that was not decoded fills the gap between its neighbours by
        # construction; a long gap there is itself a pause-length stretch.
        undecoded_stretch = not decoded and slot is not None and slot >= PAUSE_MS
        stretch = next((s for s in rate["stretches"] if s["start_ms"] - 1e-6 <= start <= s["end_ms"] + 1e-6), None)
        phones = word_phones.get(o["word_index"], [])
        pi = o["context"]["position_in_word"]
        base[o["id"]] = {
            "observation_id": o["id"],
            "decoded": decoded,
            "temporal_slot": {
                "slot_ms": slot,
                "previous_decoded": {"observation_id": prev[2], "end_ms": prev[1]} if prev else None,
                "next_decoded": {"observation_id": nxt[2], "start_ms": nxt[0]} if nxt else None,
                "gap_before_ms": gap_before,
                "gap_after_ms": gap_after,
                "pause_adjacent": pause_before or pause_after or undecoded_stretch,
                "at_edge": prev is None or nxt is None,
                "no_room": (not decoded and slot is not None and slot <= NO_ROOM_FRAMES * ms_per_frame),
                "ms_per_frame": ms_per_frame,
                "note": TEMPORAL_NOTE,
            },
            "acoustic": {
                "relative_energy": (o["acoustic"] or {}).get("relative_energy"),
                "voiced": (o["acoustic"] or {}).get("voiced"),
                "expected_voiced": expected_voiced(o["expected"]),
                "measured_over": "decoded span" if decoded else "estimated region",
            },
            "context": {
                "word_position": o["context"]["word_position"],
                "syllable_position": syllable_position(phones, pi) if 0 <= pi < len(phones) else None,
                "syllable_position_source": "derived from vowel positions in the expected phones",
                "previous_phone": o["context"]["previous_phone"],
                "next_phone": o["context"]["next_phone"],
                "word_boundary_before": o["context"]["word_boundary_before"],
                "word_boundary_after": o["context"]["word_boundary_after"],
                "function_word": o["word"].lower().strip(".,;:!?'\"") in FUNCTION_WORDS,
                "stress": o["context"]["stress"],
                "stress_known": o["context"]["stress_known"],
                "pause_before": pause_before or (prev is None),
                "pause_after": pause_after or (nxt is None),
                "local_phones_per_s": stretch["phones_per_s"] if stretch else None,
                "recording_phones_per_s": rate["phones_per_s_excluding_pauses"],
                "processes": contexts(o, pause_after=pause_after or undecoded_stretch, edge_after=nxt is None),
            },
        }

    # Within-recording comparisons (same expected phoneme only).
    for o in sounds:
        m = base[o["id"]]
        slot = m["temporal_slot"]["slot_ms"]
        peers = [base[p["id"]] for p in sounds if p["id"] != o["id"] and p["expected"] == o["expected"]]
        slot_peers = [p["temporal_slot"]["slot_ms"] for p in peers
                      if p["temporal_slot"]["slot_ms"] is not None and not p["temporal_slot"]["pause_adjacent"]]
        usable = slot is not None and not m["temporal_slot"]["pause_adjacent"]
        m["temporal_slot"]["comparables"] = len(slot_peers)
        m["temporal_slot"]["comparable_slots_ms"] = sorted(slot_peers)
        m["temporal_slot"]["shorter_than_all_comparables"] = (
            usable and len(slot_peers) >= MIN_COMPARABLES and slot < min(slot_peers))

        energy = m["acoustic"]["relative_energy"]
        energy_peers = [p["acoustic"]["relative_energy"] for p in peers
                        if p["decoded"] and p["acoustic"]["relative_energy"] is not None]
        m["acoustic"]["comparables"] = len(energy_peers)
        m["acoustic"]["comparable_relative_energy"] = sorted(energy_peers)
        m["acoustic"]["lower_than_all_comparables"] = (
            m["decoded"] and energy is not None and len(energy_peers) >= MIN_COMPARABLES and energy < min(energy_peers))
        # Not next to a pause or at an edge: the voicing measurement window then
        # reaches into silence, where the autocorrelation estimate is unreliable.
        m["acoustic"]["voicing_not_found"] = (
            m["decoded"] and m["acoustic"]["expected_voiced"] is True and m["acoustic"]["voiced"] is False
            and not m["temporal_slot"]["pause_adjacent"] and not m["temporal_slot"]["at_edge"])
    return base


def evidence_context(result: PronunciationResult, observations: list[dict[str, Any]]) -> dict[str, Any]:
    rate = speaking_rate(_anchors(observations))
    return {"phones_per_s_excluding_pauses": rate["phones_per_s_excluding_pauses"], "pauses": rate["pauses"],
            "pause_ms_total": rate["pause_ms_total"]}
