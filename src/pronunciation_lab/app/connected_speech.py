"""M5 — connected-speech contexts: candidate explanations, never labels.

Standard descriptions of English connected speech (e.g. Gimson's Pronunciation
of English, ch. on connected speech; Roach, English Phonetics and Phonology)
list processes that commonly change or weaken sounds in fluent speech:
elision of /t d/ between consonants, merging of identical sounds across a word
boundary, assimilation of alveolars to a following consonant, yod coalescence,
flapping and glottalisation of /t/ (en-us), weak forms of function words,
reduction of unstressed vowels, devoicing of final voiced obstruents.

This module only says whether a sound sits in a *context* where such a process
is commonly described, and which kind of evidence the process would be
*consistent with*. Matching evidence never proves the process occurred: the
microphone gives acoustic evidence, not the articulatory gesture, and the same
evidence can come from the recogniser itself. Every explanation is phrased as
a possibility.
"""

from __future__ import annotations

from typing import Any

from pronunciation_lab.benchmark.analysis import FUNCTION_WORDS, is_vowel

LABIAL = frozenset({"p", "b", "m", "f", "v", "w"})
VELAR = frozenset({"k", "ɡ", "g", "ŋ"})
POSTALVEOLAR = frozenset({"ʃ", "ʒ", "tʃ", "dʒ", "j"})
VOICELESS_CONSONANTS = frozenset({"p", "t", "k", "f", "θ", "s", "ʃ", "tʃ", "h"})
VOICED_OBSTRUENTS = {"b": "p", "d": "t", "ɡ": "k", "g": "k", "v": "f", "ð": "θ", "z": "s", "ʒ": "ʃ", "dʒ": "tʃ"}
VOICED_SONORANTS = frozenset({"m", "n", "ŋ", "l", "ɹ", "r", "w", "j"})
H_WORDS = frozenset({"he", "him", "his", "her", "have", "has", "had"})


def expected_voiced(phone: str | None) -> bool | None:
    """Whether the expected sound is normally voiced; None when the inventory does not say."""
    if not phone:
        return None
    if is_vowel(phone) or phone in VOICED_SONORANTS or phone in VOICED_OBSTRUENTS:
        return True
    if phone in VOICELESS_CONSONANTS:
        return False
    return None


def _consonant(phone: str | None) -> bool:
    return bool(phone) and not is_vowel(phone)


# id -> label, candidate explanation, and what evidence the process would be consistent with.
PROCESSES: dict[str, dict[str, Any]] = {
    "identical_neighbour": {
        "label": "identical neighbouring sound",
        "explanation": ("In connected speech, two identical sounds across a word boundary are often produced as "
                        "one longer sound. The recogniser's decoding can also merge two identical sounds, so this "
                        "context alone cannot tell the two apart."),
        "consistent_with": {"omission", "weakening", "compression"},
    },
    "td_between_consonants": {
        "label": "/t d/ between consonants",
        "explanation": ("/t/ and /d/ between two consonants are commonly weakened or elided in fluent English "
                        "(e.g. 'last six', 'changed significantly')."),
        "consistent_with": {"omission", "weakening", "compression"},
    },
    "alveolar_assimilation": {
        "label": "alveolar before a different place of articulation",
        "explanation": ("A word-final alveolar sound before a labial, velar or post-alveolar sound is commonly "
                        "assimilated towards the following sound (e.g. 'ten boys' with an /m/-like sound)."),
        "consistent_with": {"weakening", "compression", "omission"},
    },
    "yod_coalescence": {
        "label": "alveolar before /j/",
        "explanation": "A word-final /t d s z/ before /j/ is commonly merged with it (e.g. 'would you' → /dʒ/).",
        "consistent_with": {"weakening", "compression"},
    },
    "flapping": {
        "label": "/t d/ between vowels (en-us)",
        "explanation": ("In US English, /t/ and /d/ between vowel sounds are commonly realised as a short tap "
                        "(e.g. 'water', 'got it')."),
        "consistent_with": {"weakening", "compression"},
    },
    "glottalisation": {
        "label": "final /t/ before a consonant",
        "explanation": "A word-final /t/ before a consonant is commonly replaced or reinforced by a glottal stop.",
        "consistent_with": {"weakening", "omission", "compression"},
    },
    "weak_form": {
        "label": "function word",
        "explanation": ("Function words ('and', 'to', 'of', 'the', …) are commonly said in reduced weak forms in "
                        "fluent speech, with reduced vowels and sometimes a dropped final consonant."),
        "consistent_with": {"omission", "weakening", "compression"},
    },
    "h_dropping": {
        "label": "/h/ in an unstressed pronoun or auxiliary",
        "explanation": "/h/ in words like 'him', 'her', 'has' is commonly dropped inside a phrase.",
        "consistent_with": {"omission", "weakening"},
    },
    "unstressed_vowel": {
        "label": "vowel without lexical stress",
        "explanation": "Vowels without lexical stress are commonly reduced towards a short central vowel.",
        "consistent_with": {"weakening", "compression"},
    },
    "final_devoicing": {
        "label": "voiced obstruent before a voiceless sound or a pause",
        "explanation": ("Voiced obstruents at the end of a word, before a voiceless sound or a pause, are "
                        "commonly partly devoiced in English."),
        "consistent_with": {"weakening"},
    },
}

# Substitutions a process would be consistent with: (process, expected) -> decoded phones.
PREDICTED_VARIANTS: dict[str, dict[str, frozenset[str]]] = {
    "alveolar_assimilation": {
        "t": frozenset({"p", "k", "tʃ"}), "d": frozenset({"b", "ɡ", "g", "dʒ"}), "n": frozenset({"m", "ŋ"}),
        "s": frozenset({"ʃ"}), "z": frozenset({"ʒ"}),
    },
    "yod_coalescence": {"t": frozenset({"tʃ"}), "d": frozenset({"dʒ"}), "s": frozenset({"ʃ"}), "z": frozenset({"ʒ"})},
    "flapping": {"t": frozenset({"ɾ", "d"}), "d": frozenset({"ɾ"})},
    "glottalisation": {"t": frozenset({"ʔ"})},
    "final_devoicing": {k: frozenset({v}) for k, v in VOICED_OBSTRUENTS.items()},
    "weak_form": {},  # vowel reduction handled below
    "unstressed_vowel": {},
}
REDUCED_VOWELS = frozenset({"ə", "ɪ", "ᵻ", "ʊ", "ɐ"})


def contexts(obs: dict[str, Any], *, pause_after: bool, edge_after: bool) -> list[str]:
    """Connected-speech contexts (process ids) for one sound observation.

    Uses only the expected phones, word boundaries and (raw-engine) lexical stress:
    a context is a property of the text and the timing around the sound, not an
    observation of what the speaker did.
    """
    ctx = obs["context"]
    e, prev, nxt = obs["expected"], ctx.get("previous_phone"), ctx.get("next_phone")
    final, initial = ctx.get("word_boundary_after"), ctx.get("word_boundary_before")
    out: list[str] = []
    if _consonant(e) and ((final and nxt == e) or (initial and prev == e)):
        out.append("identical_neighbour")
    if e in ("t", "d") and _consonant(prev) and _consonant(nxt):
        out.append("td_between_consonants")
    if final and e in ("t", "d", "n", "s", "z") and nxt and (nxt in LABIAL or nxt in VELAR or nxt in POSTALVEOLAR) \
            and nxt != "j":
        out.append("alveolar_assimilation")
    if final and e in ("t", "d", "s", "z") and nxt == "j":
        out.append("yod_coalescence")
    if e in ("t", "d") and prev and (is_vowel(prev) or prev in ("ɹ", "n")) and nxt and is_vowel(nxt):
        out.append("flapping")
    if final and e == "t" and _consonant(nxt):
        out.append("glottalisation")
    word = obs["word"].lower().strip(".,;:!?'\"")
    if word in FUNCTION_WORDS:
        out.append("weak_form")
        if e == "h" and word in H_WORDS and ctx.get("sentence_position") != "first_word":
            out.append("h_dropping")
    if is_vowel(e) and ctx.get("stress_known") and ctx.get("stress") is None:
        out.append("unstressed_vowel")
    if final and e in VOICED_OBSTRUENTS and (nxt in VOICELESS_CONSONANTS or pause_after or edge_after):
        out.append("final_devoicing")
    return out


def consistent(process: str, pattern: str, expected: str | None, observed: str | None) -> bool:
    """Whether a pattern of evidence is the kind this process would be consistent with."""
    spec = PROCESSES[process]
    if pattern == "substitution":
        if process in ("weak_form", "unstressed_vowel"):
            return is_vowel(expected) and observed in REDUCED_VOWELS
        return bool(observed) and observed in PREDICTED_VARIANTS.get(process, {}).get(expected, frozenset())
    return pattern in spec["consistent_with"]


def explanation(process: str) -> dict[str, str]:
    return {"id": process, "label": PROCESSES[process]["label"], "text": PROCESSES[process]["explanation"]}
