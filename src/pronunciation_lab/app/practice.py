"""M4 — practice targets: sound → word → sentence, each tied to real occurrences.

A target exists only for a pattern, and only with the pattern's observations as
its evidence. Its `kind` follows the evidence, not a severity:

    practice  recurring, not purely ambiguous: practise the contrast
    compare   ambiguous only: listen to your occurrence and compare
    monitor   single occurrence, or not detected only: listen; watch for recurrence

Sound-level guidance is a general description of how two sounds are usually
told apart. It is explicitly *not* a statement about what the user's mouth did.
Playback always refers to the user's own recording; nothing is synthesised.
"""

from __future__ import annotations

from typing import Any

from pronunciation_lab.app.phones import hint

GUIDANCE_NOTE = "General description of how these sounds are usually distinguished — not a measurement of your articulation."

# Unordered contrast -> general guidance.
CONTRAST_GUIDANCE: dict[frozenset, str] = {
    frozenset(("w", "v")): "/w/ is usually made with rounded lips and no teeth contact; /v/ with the lower lip lightly touching the upper teeth, with voicing.",
    frozenset(("θ", "t")): "/θ/ is usually a continuous breathy sound with the tongue near the upper teeth; /t/ is a short stop-and-release.",
    frozenset(("ð", "d")): "/ð/ is usually a continuous voiced sound with the tongue near the upper teeth; /d/ is a short voiced stop.",
    frozenset(("θ", "s")): "/θ/ is usually softer and more diffuse; /s/ is a sharper, high-pitched hiss.",
    frozenset(("s", "ʃ")): "/s/ is usually a sharp high hiss with spread lips; /ʃ/ (as in 'ship') is lower-pitched, often with rounded lips.",
    frozenset(("s", "z")): "/s/ is usually voiceless; /z/ is the same hiss with voicing.",
    frozenset(("ŋ", "n")): "/ŋ/ (as in 'sing') is usually made at the back of the mouth; /n/ behind the upper teeth.",
    frozenset(("ɪ", "iː")): "/ɪ/ (as in 'sit') is usually short and relaxed; /iː/ (as in 'see') is longer and tenser.",
    frozenset(("tʃ", "dʒ")): "/tʃ/ (as in 'chin') is usually voiceless; /dʒ/ (as in 'jam') is voiced.",
    frozenset(("f", "v")): "/f/ is usually voiceless; /v/ is the same sound with voicing.",
    frozenset(("ɹ", "l")): "/ɹ/ is usually made without the tongue tip touching the roof of the mouth; /l/ with it touching.",
    frozenset(("ð", "z")): "/ð/ is usually made with the tongue near the upper teeth; /z/ is a sharper voiced hiss.",
}


def guidance(a: str | None, b: str | None) -> str | None:
    if not a or not b:
        return None
    return CONTRAST_GUIDANCE.get(frozenset((a, b)))


def build_targets(patterns: list[dict[str, Any]], observations: dict[str, dict[str, Any]],
                  target_text: str) -> list[dict[str, Any]]:
    targets = []
    for p in patterns:
        support = [observations[i] for i in p["observation_ids"] if i in observations]
        if not support:  # never a target without evidence
            continue
        if p["group"] == "recurring":
            kind = "practice"
        elif p["group"] == "ambiguous":
            kind = "compare"
        else:
            kind = "monitor"

        target, contrast = (p["contrast"], None) if p["kind"] == "insertion" else (p["expected"], p["contrast"])
        if kind == "practice":
            reason = p["summary"]
        elif kind == "compare":
            reason = (f"This is ambiguous between /{target}/ and /{contrast}/. Listen to the recording and compare "
                      f"the contrast; it is not a confirmed difference.")
        elif p["kind"] == "detection":
            reason = (f"/{target}/ was not clearly detected. Listen to whether you hear it; not detected does not "
                      f"prove it was absent.")
        else:
            reason = "Single observation — listen to it and monitor for recurrence."

        targets.append({
            "id": "t-" + p["id"],
            "pattern_id": p["id"],
            "kind": kind,
            "target_phoneme": target,
            "contrast_phoneme": contrast,
            "reason": reason,
            "confidence": p["evidence_strength"],
            "supporting_observation_ids": [o["id"] for o in support],
            "example_words": list(dict.fromkeys(o["word"] for o in support)),
            "occurrences": [
                {"observation_id": o["id"], "word": o["word"], "span_ms": o["span_ms"],
                 "play_ms": o["play_ms"], "word_play_ms": o["word_play_ms"]}
                for o in support
            ],
            "levels": {
                "sound": {
                    "target": target, "target_hint": hint(target),
                    "contrast": contrast, "contrast_hint": hint(contrast),
                    "guidance": guidance(target, contrast),
                    "guidance_note": GUIDANCE_NOTE if guidance(target, contrast) else None,
                },
                "word": {"words": list(dict.fromkeys(o["word"] for o in support))},
                "sentence": {"text": target_text},
            },
            "reference_note": p["reference_note"],
        })
    return targets
