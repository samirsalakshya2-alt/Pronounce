"""Plain-language hints for IPA symbols, so the UI is usable without phonetics.

Hints are examples of where the sound occurs in (American) English words,
matching the eSpeak `en-us` reference the expected phones come from. They are
orientation, not definitions. Unknown symbols get no hint rather than a guess.
"""

from __future__ import annotations

_HINTS: dict[str, str] = {
    # consonants
    "p": "p as in pen", "b": "b as in bad", "t": "t as in ten", "d": "d as in day",
    "k": "k as in cat", "ɡ": "g as in go", "f": "f as in fat", "v": "v as in van",
    "θ": "th as in think", "ð": "th as in this", "s": "s as in see", "z": "z as in zoo",
    "ʃ": "sh as in ship", "ʒ": "s as in measure", "h": "h as in hat", "m": "m as in man",
    "n": "n as in no", "ŋ": "ng as in sing", "l": "l as in leg", "ɹ": "r as in red",
    "r": "rolled/tapped r", "ɾ": "flapped t as in American 'water'", "w": "w as in wet",
    "j": "y as in yes", "tʃ": "ch as in chin", "dʒ": "j as in jam", "ʔ": "glottal stop",
    "x": "ch as in Scottish 'loch'", "ʋ": "between v and w",
    # vowels
    "ɪ": "i as in sit", "i": "ee as in happy (short)", "iː": "ee as in see",
    "ᵻ": "reduced i as in 'roses'", "ɛ": "e as in bed", "e": "e as in 'café'",
    "æ": "a as in cat", "ʌ": "u as in cup", "ə": "a as in about (unstressed)",
    "ɐ": "reduced a as in 'a' / about", "ɑ": "a as in father", "ɑː": "a as in father",
    "ɔ": "aw as in thought", "ɔː": "aw as in law", "ʊ": "oo as in book",
    "u": "oo as in food (short)", "uː": "oo as in food", "ɜ": "ur as in nurse",
    "ɜː": "ur as in nurse", "ɚ": "er as in butter", "eɪ": "ay as in day",
    "aɪ": "i as in my", "ɔɪ": "oy as in boy", "aʊ": "ow as in now", "oʊ": "o as in go",
    "əl": "le as in bottle", "ɑːɹ": "ar as in car", "ɔːɹ": "or as in for",
    "ɔɹ": "or as in for", "ɑɹ": "ar as in car", "oɹ": "or as in for",
    "ɪɹ": "eer as in near", "ɛɹ": "air as in hair", "ʊɹ": "oor as in tour",
    "a": "a as in Spanish 'casa'", "o": "o as in 'go' (pure)", "oː": "long o",
}


def hint(phone: str | None) -> str | None:
    """A short example for `phone`, or None when we have no reliable example."""
    if not phone:
        return None
    return _HINTS.get(phone)
