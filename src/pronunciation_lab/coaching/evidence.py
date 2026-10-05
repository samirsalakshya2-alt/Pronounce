"""M9 level 1 — normalised evidence (observations), with explicit quality and exclusion reasons.

One EvidenceUnit per M4 sound observation of an eligible reading; one FluencyUnit per noticed M8
observation. Nothing is deleted: an excluded unit keeps its reason and is counted in the output.

Unit quality (the unit-level gate):

    confident    M4 substitution_candidate with high/moderate confidence, plausible pair, analysis ok
    supporting   M4 ambiguous within a plausible pair, or a confident substitution in a sentence whose
                 analysis M7 marked low-confidence. Supporting units NEVER count towards the
                 established-evidence minimum, so many weak observations cannot outweigh a few strong ones.
    weak         not detected (omission / weak evidence): a clarity signal only
    counter      heard as expected (counter-evidence)
    excluded     see EXCLUSIONS
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from pronunciation_lab.coaching import knowledge as K

QUALITIES = ("confident", "supporting", "weak", "counter", "excluded")

EXCLUSIONS = {
    "not_interpreted": "M4 could not interpret it (alignment suspect or invalid evidence)",
    "extra_sound": "an extra decoded sound; not used for coaching targets",
    "reference_variant": "may reflect the reference accent / phoneme inventory (eSpeak en-us)",
    "function_word_variant": "a vowel in a function word, which has several accepted pronunciations",
    "implausible_pair": "a substitution across sound classes, more likely an alignment artifact",
    "context_predicted": "M5: consistent with natural connected speech or a context-predicted variant",
    "merge_suspect": "M5: a possible decoding merge of neighbouring sounds",
    "neighbour_shift": "the heard sound is the neighbouring expected sound: an alignment shift or assimilation "
                       "is likelier than a confusion",
    "unreliable_level": "M8: speech level not measurable, so the pause is only a gap between decoded sounds",
}
# readings that never enter the pool (counted by the adapter / pool, never ingested as units)
READING_EXCLUSIONS = {
    "not_eligible": "not in the reading summary (withheld, unconfirmed, different sentence, discarded, "
                    "re-recording requested, or not analysed)",
    "other_engine": "analysed by another listening model than the pool's",
    "outside_window": "older than the readings considered now",
    "no_view": "no stored analysis view",
}

FLUENCY_NOTICED = {"PAUSE": "hesitation", "FILLER": "filler", "REPETITION": "repetition", "RESTART": "repetition",
                   "FALSE_START": "repetition"}


def text_key(text: str) -> str:
    """Identity of a sentence (or article) across pasted copies: normalised text, hashed."""
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = re.sub(r"[^\w']+", " ", t).strip()
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]


def lexical_key(word: str) -> str:
    return re.sub(r"[^\w']", "", unicodedata.normalize("NFKC", word or "").lower())


def fragment_words(article_text: str) -> frozenset[str]:
    """Tokens next to a hyphenated break ("possi- bility", "devel-\\nopment"): not real words."""
    out = set()
    for m in re.finditer(r"(\w+)-\s+(\w+)", unicodedata.normalize("NFKC", article_text or "")):
        out.add(m.group(1).lower())
        out.add(m.group(2).lower())
    return frozenset(out)


@dataclass(frozen=True)
class Reading:
    """One eligible attempt (its primary analysis), with provenance."""
    reading_id: str            # "<session_id>:<attempt_id>"
    session_id: str
    attempt_id: str
    job_id: str
    segment_id: str
    engine: str
    recorded_at: str           # ISO timestamp (attempt.created_at)
    sentence_key: str
    sentence_text: str
    article_key: str
    analysis_state: str        # "ok" | "low_confidence" | "unknown" (M7)
    fluency_reliable: bool | None
    has_comparison: bool
    sentence_ref: dict[str, Any]   # playback reference for the sentence (M7 target region)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceUnit:
    unit_id: str
    reading_id: str
    session_id: str
    sentence_key: str
    recorded_at: str
    observation_id: str
    engine: str
    coach_version: str | None
    word: str
    lexical_key: str
    word_index: int
    function_word: bool
    fragment_suspect: bool
    expected: str | None
    heard: str | None
    outcome: str               # as_expected | heard_other | not_detected | extra | uninterpreted
    m4_type: str
    m4_confidence: str
    expected_posterior: float | None
    competitor_posterior: float | None
    context: dict[str, Any]
    m5: dict[str, Any] | None
    engine_agreement: str      # agree | differ | only_this_engine | not_compared
    audio: dict[str, Any]      # play_ms, span_ms, word_play_ms, timing_source, + reference fields
    quality: str = "excluded"
    reasons: list[str] = field(default_factory=list)
    exclusion: str | None = None
    source: str = "M4"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FluencyUnit:
    unit_id: str
    reading_id: str
    session_id: str
    sentence_key: str
    recorded_at: str
    observation_id: str
    engine: str
    group: str                 # hesitation | filler | repetition
    type: str
    classification: str | None
    label: str
    strength: str
    word_before: str | None
    word_after: str | None
    position: str | None
    reliable: bool
    audio: dict[str, Any]
    quality: str = "excluded"
    exclusion: str | None = None
    source: str = "M8"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReadingInput:
    """What the adapter hands the core for one reading: stored evidence, untransformed."""
    reading: Reading
    coach_observations: list[dict[str, Any]]
    coach_version: str | None
    reduction_candidates: list[dict[str, Any]]
    fluency: dict[str, Any] | None
    engine_agreement: dict[str, str]         # observation id → agree | differ | only_this_engine
    fragment_words: frozenset[str] = frozenset()


_OUTCOME = {"expected": "as_expected", "substitution_candidate": "heard_other", "ambiguous": "heard_other",
            "omission_candidate": "not_detected", "weak_evidence": "not_detected", "insertion": "extra",
            "not_interpreted": "uninterpreted"}


def _ref(reading: Reading, play, span, kind: str, **extra) -> dict[str, Any]:
    """A playback reference in the reader's format (reader.model.playback_reference), plus unit ids."""
    return {"session_id": reading.session_id, "segment_id": reading.segment_id, "attempt_id": reading.attempt_id,
            "job_id": reading.job_id, "timeline": "analysis_wav", "kind": kind, "play_ms": play, "span_ms": span,
            "url": f"/api/sessions/{reading.session_id}/attempts/{reading.attempt_id}/audio", **extra}


def assess(u: EvidenceUnit, reading: Reading) -> None:
    """Unit-level gate: set quality, exclusion and reasons (in place)."""
    if u.outcome == "uninterpreted":
        u.quality, u.exclusion = "excluded", "not_interpreted"
        return
    if u.outcome == "extra":
        u.quality, u.exclusion = "excluded", "extra_sound"
        return
    vowel = K.is_vowel_sound(u.expected)
    if u.function_word and vowel:
        u.quality, u.exclusion = "excluded", "function_word_variant"
        return
    if u.outcome == "as_expected":
        u.quality = "counter"
        return
    if u.outcome == "not_detected":
        if u.m5 and u.m5.get("merge_suspect"):
            u.quality, u.exclusion = "excluded", "merge_suspect"
        elif u.m5 and u.m5.get("natural_possible"):
            u.quality, u.exclusion = "excluded", "context_predicted"
        else:
            u.quality = "weak"
        return
    # heard_other
    if K.reference_variant(u.expected, u.heard):
        u.quality, u.exclusion = "excluded", "reference_variant"
    elif not K.plausible(u.expected, u.heard):
        u.quality, u.exclusion = "excluded", "implausible_pair"
    elif u.heard in (u.context.get("previous_phone"), u.context.get("next_phone")):
        u.quality, u.exclusion = "excluded", "neighbour_shift"
    elif u.m5 and (u.m5.get("merge_suspect")):
        u.quality, u.exclusion = "excluded", "merge_suspect"
    elif u.m5 and (u.m5.get("natural_possible") or u.m5.get("category") == "possible_coarticulation"):
        u.quality, u.exclusion = "excluded", "context_predicted"
    elif u.m4_type == "substitution_candidate" and u.m4_confidence in ("high", "moderate"):
        if reading.analysis_state == "low_confidence":
            u.quality = "supporting"
            u.reasons.append("the sentence's analysis was low-confidence (M7), so this counts as support only")
        else:
            u.quality = "confident"
    else:
        u.quality = "supporting"
        u.reasons.append("ambiguous decode (M4): support only")


def normalise(inp: ReadingInput) -> tuple[list[EvidenceUnit], list[FluencyUnit]]:
    r = inp.reading
    m5 = {}
    for c in inp.reduction_candidates or []:
        it = c.get("interpretation") or {}
        m5[c.get("observation_id")] = {"category": it.get("category"), "strength": c.get("evidence_strength"),
                                      "contexts_present": it.get("contexts_present") or [],
                                      "natural_possible": bool(it.get("natural_connected_speech_possible")),
                                      "merge_suspect": bool(it.get("merge_suspect"))}
    units: list[EvidenceUnit] = []
    for o in inp.coach_observations:
        kind = o.get("kind")
        outcome = _OUTCOME.get(o.get("type"), "uninterpreted")
        heard = o.get("competitor") if outcome == "heard_other" else (o.get("observed") if outcome == "extra" else None)
        if outcome == "heard_other" and not heard:
            heard = o.get("observed")
        word = o.get("word") or ""
        lk = lexical_key(word)
        ctx = dict(o.get("context") or {})
        u = EvidenceUnit(
            unit_id=f"{r.session_id}:{r.attempt_id}:{r.job_id}:{o['id']}", reading_id=r.reading_id,
            session_id=r.session_id, sentence_key=r.sentence_key, recorded_at=r.recorded_at, observation_id=o["id"],
            engine=r.engine, coach_version=inp.coach_version, word=word, lexical_key=lk,
            word_index=o.get("word_index", -1), function_word=K.function_word(lk),
            fragment_suspect=(len(lk) < 3 or not lk.replace("'", "").isalpha() or lk in inp.fragment_words),
            expected=o.get("expected") if kind == "sound" else None, heard=heard, outcome=outcome,
            m4_type=o.get("type"), m4_confidence=o.get("confidence"),
            expected_posterior=o.get("expected_posterior"), competitor_posterior=o.get("competitor_posterior"),
            context={k: ctx.get(k) for k in ("word_position", "previous_phone", "next_phone", "in_consonant_cluster",
                                             "sentence_position", "stress", "stress_known")},
            m5=m5.get(o["id"]), engine_agreement=inp.engine_agreement.get(o["id"], "not_compared"),
            audio=_ref(r, o.get("play_ms"), o.get("span_ms"), "sound", word=word, observation_id=o["id"],
                       word_play_ms=o.get("word_play_ms"), timing_source=o.get("timing_source")))
        assess(u, r)
        units.append(u)
    fluency: list[FluencyUnit] = []
    fl = inp.fluency or {}
    if fl.get("state") == "ok":
        reliable = bool((fl.get("metrics") or {}).get("activity_reliable"))
        for o in fl.get("observations") or []:
            group = FLUENCY_NOTICED.get(o.get("type"))
            if group is None or not o.get("notice"):
                continue
            ctx = o.get("context") or {}
            if o["type"] == "PAUSE" and (o.get("classification") not in ("possible_hesitation", "unusually_long")
                                         or "boundary" in (ctx.get("position") or "")):
                continue  # only pauses inside a phrase or word; a long pause at punctuation is phrasing
            pb = o.get("playback") or {}
            f = FluencyUnit(
                unit_id=f"{r.session_id}:{r.attempt_id}:{r.job_id}:{o['id']}", reading_id=r.reading_id,
                session_id=r.session_id, sentence_key=r.sentence_key, recorded_at=r.recorded_at,
                observation_id=o["id"], engine=r.engine, group=group, type=o["type"],
                classification=o.get("classification"), label=o.get("label"), strength=o.get("strength"),
                word_before=ctx.get("word_before"), word_after=ctx.get("word_after"), position=ctx.get("position"),
                reliable=reliable,
                audio=_ref(r, pb.get("play_ms"), pb.get("span_ms"), "fluency", observation_id=o["id"],
                           context_ms=pb.get("context_ms"), label=o.get("label")))
            if reliable:
                f.quality = "confident"
            else:
                f.quality, f.exclusion = "excluded", "unreliable_level"
            fluency.append(f)
    return units, fluency
