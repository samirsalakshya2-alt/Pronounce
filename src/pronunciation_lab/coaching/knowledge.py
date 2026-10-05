"""M9 declared phonetic knowledge — small, inspectable, and never user evidence.

What this module may contribute:
  * plausibility: whether a decoded substitution is phonetically plausible at all (a vowel decoded where a
    consonant was expected is far more likely an alignment artifact than a pronunciation pattern);
  * families: which contrasts share one practice (a coaching *hypothesis* only, never a measured cause);
  * guidance: references to the existing M4 contrast guidance (practice.CONTRAST_GUIDANCE), never new text.

What it can never contribute: counts, tiers, or any statement about the user. Everything returned here is
tagged ORIGIN = "knowledge" and is kept apart from evidence by type (KnowledgeContribution).

Adding an entry requires a source note and a test showing it consolidates real fragmented evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from pronunciation_lab.app.phoneme_coach import REFERENCE_NOTE, is_reference_variant
from pronunciation_lab.app.phones import hint
from pronunciation_lab.app.practice import GUIDANCE_NOTE, guidance
from pronunciation_lab.benchmark.analysis import FUNCTION_WORDS, is_vowel

KNOWLEDGE_VERSION = "m9-kn.1"
ORIGIN = "knowledge"
FAMILY_TEXT = "These sounds are related in English phonetics (general knowledge, not a measurement of your speech)."

# Rhotic and syllabic notations are vowels for is_vowel but stand for the same sound as a consonant symbol.
CROSS_CLASS_PLAUSIBLE = frozenset(frozenset(p) for p in (("ɚ", "ɹ"), ("ɝ", "ɹ"), ("əl", "l")))


def is_vowel_sound(phone: str | None) -> bool:
    return bool(phone) and is_vowel(phone)


def plausible(expected: str | None, heard: str | None) -> bool:
    """A substitution is plausible when both sounds are of one broad class (vowel / consonant)."""
    if not expected or not heard:
        return False
    if is_vowel(expected) == is_vowel(heard):
        return True
    return frozenset((expected, heard)) in CROSS_CLASS_PLAUSIBLE


def reference_variant(expected: str | None, heard: str | None) -> bool:
    """M4's reference-inventory list (eSpeak en-us), reused unchanged."""
    return is_reference_variant(expected, heard)


def function_word(word: str | None) -> bool:
    return bool(word) and word.lower() in FUNCTION_WORDS


@dataclass(frozen=True)
class Family:
    """A declared group of contrasts that one practice can address.

    mode "set":   any deviation among `members` (mutual confusion within a closed set of sounds)
    mode "pairs": only the declared directed pairs (expected → heard); the reverse direction contradicts
    """
    id: str
    title: str                 # user-facing, e.g. "the vowels in bit, bait and bet"
    mode: str
    members: frozenset[str]
    pairs: frozenset[tuple[str, str]]
    description: str
    source: str

    def covers(self, expected: str, heard: str) -> bool:
        if self.mode == "set":
            return expected in self.members and heard in self.members and expected != heard
        return (expected, heard) in self.pairs

    def reverse(self, expected: str, heard: str) -> bool:
        """For directional families: is (expected → heard) the opposite of a declared pair?"""
        return self.mode == "pairs" and (heard, expected) in self.pairs and (expected, heard) not in self.pairs


_VOICING = (("p", "b"), ("t", "d"), ("k", "ɡ"), ("f", "v"), ("θ", "ð"), ("s", "z"), ("ʃ", "ʒ"), ("tʃ", "dʒ"))
_SOURCE = "standard descriptions of English phonology (e.g. Roach, English Phonetics and Phonology)"

FAMILIES: tuple[Family, ...] = (
    Family("front_vowel_ladder", "the vowels in beat, bit, bait, bet and bat", "set",
           frozenset({"iː", "i", "ɪ", "eɪ", "ɛ", "æ"}), frozenset(),
           "English front vowels form a series from close to open; neighbouring ones are easily heard as each other.",
           _SOURCE),
    Family("back_rounded_vowels", "the vowels in book, food and go", "set",
           frozenset({"ʊ", "uː", "u", "oʊ"}), frozenset(),
           "English back rounded vowels form a short series; neighbouring ones are easily heard as each other.",
           _SOURCE),
    Family("sibilant_place", "the 's' and 'sh' sounds", "pairs", frozenset(),
           frozenset({("s", "ʃ"), ("ʃ", "s"), ("z", "ʒ"), ("ʒ", "z")}),
           "/s z/ and /ʃ ʒ/ differ in where the hiss is made, not in voicing.", _SOURCE),
    Family("th_sounds", "the 'th' sounds", "pairs", frozenset(),
           frozenset({("θ", "t"), ("θ", "s"), ("θ", "f"), ("ð", "d"), ("ð", "z"), ("ð", "v")}),
           "/θ/ and /ð/ are often replaced by nearby stops or fricatives.", _SOURCE),
    Family("devoicing", "voiced consonants heard as voiceless", "pairs", frozenset(),
           frozenset((b, a) for a, b in _VOICING),
           "Voiced consonants heard as their voiceless partners share one practice: keeping voicing.", _SOURCE),
    Family("voicing", "voiceless consonants heard as voiced", "pairs", frozenset(),
           frozenset(_VOICING),
           "Voiceless consonants heard as their voiced partners share one practice: keeping them voiceless.",
           _SOURCE),
)


@dataclass(frozen=True)
class KnowledgeContribution:
    """What declared knowledge added to a target — always labelled, never counted as evidence."""
    entry: str          # e.g. "family:front_vowel_ladder", "guidance:ɛ~eɪ", "plausibility"
    contributed: str    # "proposed family", "general guidance", ...
    text: str
    origin: str = ORIGIN

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def families() -> tuple[Family, ...]:
    return FAMILIES


def family(fid: str) -> Family | None:
    return next((f for f in FAMILIES if f.id == fid), None)


def pair_guidance(a: str, b: str) -> KnowledgeContribution | None:
    text = guidance(a, b)
    if not text:
        return None
    return KnowledgeContribution(f"guidance:{a}~{b}", "general guidance", text)


def guidance_for(pairs: list[tuple[str, str]]) -> list[KnowledgeContribution]:
    """Existing M4 guidance for the given (unordered) pairs, deduplicated, in the given order."""
    out, seen = [], set()
    for a, b in pairs:
        key = frozenset((a, b))
        if key in seen:
            continue
        seen.add(key)
        g = pair_guidance(a, b)
        if g:
            out.append(g)
    return out


def sound_label(phone: str) -> str:
    h = hint(phone)
    return f"/{phone}/" + (f" ({h})" if h else "")


def describe() -> dict[str, Any]:
    """The whole knowledge layer, for inspection in the detailed output."""
    return {"version": KNOWLEDGE_VERSION, "origin": ORIGIN, "guidance_note": GUIDANCE_NOTE,
            "reference_note": REFERENCE_NOTE,
            "cross_class_plausible": sorted("~".join(sorted(p)) for p in CROSS_CLASS_PLAUSIBLE),
            "families": [{"id": f.id, "title": f.title, "mode": f.mode, "members": sorted(f.members),
                          "pairs": sorted("→".join(p) for p in f.pairs), "description": f.description,
                          "source": f.source} for f in FAMILIES]}
