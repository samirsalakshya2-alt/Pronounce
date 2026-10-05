"""M9 level 4 — interventions and practice plans.

A target becomes an intervention only if it is trainable:
    specific_guidance        existing M4 contrast guidance applies (practice.CONTRAST_GUIDANCE)
    listen_compare_fallback  no guidance: compare your own clear examples (needs enough of them)
    word_practice            lexical target
    phrase_practice          fluency target
and only if it has at least one playable example and one retest sentence. Otherwise there is no
intervention, and the target cannot be selected. No guidance text is ever invented here; fallback wording
is fixed and generic, and every example is the user's own recording.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from pronunciation_lab.coaching import knowledge as K
from pronunciation_lab.coaching.calibration import DEFAULT, Calibration
from pronunciation_lab.coaching.evidence import Reading
from pronunciation_lab.coaching.patterns import Index
from pronunciation_lab.coaching.targets import SOUND_KINDS, Target, counter_ids

TRAINABILITY_LEVEL = {"specific_guidance": 2, "listen_compare_fallback": 1, "word_practice": 1, "phrase_practice": 1}
FALLBACK_TEXT = ("No general guidance is stored for this contrast: listen to your own clear examples and imitate "
                 "them, then compare with the ones heard differently.")


@dataclass
class Intervention:
    target_id: str
    kind: str
    trainability: str
    action_text: str
    examples: list[dict[str, Any]]
    counter_examples: list[dict[str, Any]]
    words: list[dict[str, Any]]
    retest: list[dict[str, Any]]
    guidance: list[K.KnowledgeContribution] = field(default_factory=list)
    transfer: dict[str, Any] = field(default_factory=dict)

    @property
    def level(self) -> int:
        return TRAINABILITY_LEVEL[self.trainability]


def _lbl(p: str) -> str:
    return K.sound_label(p)


def action_text(t: Target) -> str:
    if t.kind == "CONTRAST":
        a, b = t.pairs[0]
        if t.two_way:
            return f"Practise telling {_lbl(a)} and {_lbl(b)} apart"
        return f"Practise keeping {_lbl(a)} distinct from {_lbl(b)}"
    if t.kind == "SET":
        sounds = sorted({p for pair in t.pairs for p in pair})
        return f"Practise telling apart {K.family(t.family_id).title} ({', '.join('/' + s + '/' for s in sounds)})"
    if t.kind == "CONDITIONED":
        from pronunciation_lab.coaching.targets import _COND
        return f"Practise {', '.join(_lbl(s) for s in t.sounds)} {_COND[t.condition][0]}"
    if t.kind == "LEXICAL":
        return f"Practise the word ‘{t.word}’"
    return {"hesitation": "Practise reading in phrase-sized chunks",
            "filler": "Practise pausing silently between phrases",
            "repetition": "Practise reading the affected phrases smoothly before the whole sentence"}[t.group]


def _audio_ok(ref: dict[str, Any] | None) -> bool:
    play = (ref or {}).get("play_ms")
    return bool(play) and len(play) == 2 and play[0] is not None and play[1] is not None and 0 <= play[0] < play[1]


def _recent(units):
    return sorted(units, key=lambda u: (u.recorded_at, u.unit_id), reverse=True)


def _distinct_words(units, n):
    out, seen = [], set()
    for u in units:
        key = getattr(u, "lexical_key", None) or u.unit_id
        if key in seen or not _audio_ok(u.audio):
            continue
        seen.add(key)
        out.append(u)
        if len(out) == n:
            break
    return out


def _example(u, role: str) -> dict[str, Any]:
    ref = dict(u.audio)
    ref.update({"unit_id": u.unit_id, "role": role})
    if hasattr(u, "expected"):
        ref.update({"expected": u.expected, "heard": u.heard if role == "heard_differently" else u.expected,
                    "confidence": u.m4_confidence})
    return ref


def build_intervention(t: Target, index: Index, readings: dict[str, Reading], cal: Calibration = DEFAULT) -> Intervention | None:
    units = index.get(t.unit_ids)
    if t.kind == "CLARITY":
        return None  # a weak signal is never an action
    confident = [u for u in units if u.quality == "confident"]
    examples = [_example(u, "heard_differently") for u in _distinct_words(_recent(confident), cal.n_examples)]
    counters_all = [index.units[i] for i in counter_ids(t, index)]
    ex_words = {e.get("word", "").lower() for e in examples}
    counters_sorted = sorted(_recent(counters_all), key=lambda u: (u.lexical_key not in ex_words,))  # same words first
    counter = [_example(u, "heard_as_expected") for u in _distinct_words(counters_sorted, cal.n_counter)]
    # practice words: recurring words of the target's confident units (never fragments)
    by_word = Counter(u.lexical_key for u in confident if not u.fragment_suspect) if t.kind in SOUND_KINDS else Counter()
    sentences_by_word: dict[str, set[str]] = defaultdict(set)
    for u in confident if t.kind in SOUND_KINDS else []:
        sentences_by_word[u.lexical_key].add(u.sentence_key)
    words = []
    for w, n in sorted(by_word.items(), key=lambda x: (-x[1], -len(sentences_by_word[x[0]]), x[0]))[:cal.n_words]:
        u = next(x for x in _recent(confident) if x.lexical_key == w)
        ref = dict(u.audio, play_ms=u.audio.get("word_play_ms") or u.audio.get("play_ms"), kind="word",
                   unit_id=u.unit_id)
        words.append({"word": u.word, "times_heard_differently": n, "ref": ref if _audio_ok(ref) else None})
    # retest: the sentences with most target units, most recent reading of each
    per_sentence: dict[str, list] = defaultdict(list)
    for u in units:
        per_sentence[u.sentence_key].append(u)
    ranked = sorted(per_sentence.items(), key=lambda kv: (len(kv[1]), max(x.recorded_at for x in kv[1])), reverse=True)
    retest = []
    for skey, us in ranked:
        latest = max(us, key=lambda x: (x.recorded_at, x.unit_id))
        r = readings.get(latest.reading_id)
        if r is None or not _audio_ok(r.sentence_ref):
            continue
        retest.append({"text": r.sentence_text, "sentence_key": skey, "reading_id": r.reading_id,
                       "units": len(us), "ref": dict(r.sentence_ref, kind="sentence")})
        if len(retest) == cal.n_retest:
            break
    if t.kind == "FLUENCY":
        examples = [_example(u, "noticed") for u in _distinct_words(_recent(units), cal.n_examples)]
    if not examples or not retest:
        return None  # nothing exact to listen to, or nothing to re-read: no concrete action
    guidance: list[K.KnowledgeContribution] = []
    if t.kind == "FLUENCY":
        trainability = "phrase_practice"
    elif t.kind == "LEXICAL":
        trainability = "word_practice"
        guidance = K.guidance_for([p for p in t.pairs])
    else:
        guidance = K.guidance_for(list(t.pairs))
        if guidance:
            trainability = "specific_guidance"
        elif len(counter) >= cal.fallback_min_counter:
            trainability = "listen_compare_fallback"
        else:
            return None
    return Intervention(t.target_id, t.kind, trainability, action_text(t), examples, counter, words, retest,
                        guidance, _transfer(t, index))


def _transfer(t: Target, index: Index) -> dict[str, Any]:
    """Hypothesised, never measured: other words in the pool's reading that contain the target's sounds."""
    if t.kind == "LEXICAL":
        return {"origin": "hypothesised", "text": "This target is specific to one word.", "potential_words": []}
    if t.kind == "FLUENCY":
        return {"origin": "hypothesised", "potential_words": [],
                "text": "Reading in phrases may also help other sentences; this is a possibility, not something measured."}
    seen = {index.units[i].lexical_key for i in t.unit_ids}
    others = sorted({u.lexical_key for u in index.units.values() if getattr(u, "expected", None) in t.sounds
                     and u.lexical_key and u.lexical_key not in seen and not u.fragment_suspect
                     and not u.function_word})
    return {"origin": "hypothesised", "potential_words": others[:8], "potential_word_count": len(others),
            "text": ("Practising this may also help other words with these sounds in what you read"
                     + (f" (for example {', '.join(others[:3])})" if others else "")
                     + "; this is a possibility, not something measured.")}


def steps(t: Target, iv: Intervention) -> list[dict[str, Any]]:
    """The ordered practice steps of one action. Text is fixed per kind; examples are the user's own audio."""
    listen = {"step": "listen", "text": "Listen to your own examples: first heard as expected, then heard differently.",
              "examples": iv.counter_examples + iv.examples}
    retest = {"step": "retest", "text": "Record these sentences again (Read these now).", "sentences": iv.retest}
    reread = {"step": "sentences", "text": "Read these sentences slowly, then at your normal pace.", "sentences": iv.retest}
    if t.kind in ("CONTRAST", "SET"):
        contrast = {"step": "contrast", "text": "Say the sounds slowly, one after the other, keeping them distinct.",
                    "guidance": [g.to_dict() for g in iv.guidance] or None,
                    "fallback": None if iv.guidance else FALLBACK_TEXT}
        return [listen, contrast, {"step": "words", "text": "Say these words from your readings, slowly, then at your normal pace.",
                                   "words": iv.words}, reread, retest]
    if t.kind == "CONDITIONED":
        contrast = {"step": "context", "text": "Say the sound in this position, then in other positions, keeping it the same.",
                    "guidance": [g.to_dict() for g in iv.guidance] or None,
                    "fallback": None if iv.guidance else FALLBACK_TEXT}
        return [listen, contrast, {"step": "words", "text": "Say these words from your readings, slowly, then at your normal pace.",
                                   "words": iv.words}, reread, retest]
    if t.kind == "LEXICAL":
        listen_w = {"step": "listen", "text": f"Listen to your readings of ‘{t.word}’.",
                    "examples": iv.examples + iv.counter_examples}
        return [listen_w, {"step": "word", "text": f"Say ‘{t.word}’ slowly, then at your normal pace.",
                           "guidance": [g.to_dict() for g in iv.guidance] or None, "words": iv.words}, reread, retest]
    return [{"step": "listen", "text": "Listen to the moments where a pause or repeat was noticed.", "examples": iv.examples},
            {"step": "chunk", "text": "Mark the phrase breaks at commas and full stops; read one phrase at a time."},
            {"step": "sentences", "text": "Read the whole sentence in phrases.", "sentences": iv.retest}, retest]
