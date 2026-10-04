"""One attempt's status as four separate decisions (never collapsed into "may not match"):

    A. identity  — is this probably the sentence?           target confirmation (reader/target.py)
    B. boundary  — where did the sentence end?              M7 (reader/boundary.py), authoritative
    C. feedback  — is it safe to show pronunciation feedback?
    D. summary   — is this attempt included in the reading summary, and if not, why?

Normal and probable readings need nothing from the reader: MATCH and LIKELY_MATCH count as recorded and
identified, and their feedback is summarised whenever it is safe (M7 did not withhold it). Keep is an override
for an uncertain identity (AMBIGUOUS): "I confirm it is this sentence". It never forces feedback that is
unsafe: a recording that appears to contain a different sentence, or whose sentence could not be separated
from other speech (M7 withheld), stays without feedback when kept.
Nothing here deletes anything: every attempt and its audio stay stored whatever its status.
"""

from __future__ import annotations

from typing import Any

IDENTITY_TEXT = {
    "MATCH": "Recording appears to match this sentence.",
    "LIKELY_MATCH": "This recording is probably this sentence (it fits it best of the article's sentences), "
                    "but the evidence is weak.",
    "AMBIGUOUS": "I couldn't confidently tell whether this recording is this sentence.",
    "MISMATCH": "This recording appears to contain a different sentence.",
    "TOO_SHORT": "Recording is too short to confirm the sentence.",
    "FAILED": "Recording could not be analysed.",
    "PENDING": "Listening to this recording…",
    "NOT_APPLICABLE": "Recording could not be analysed.",
}
MATCH_WITHHELD_TEXT = "Your reading appears to match this sentence, but I couldn't safely determine where it ended."
CONTAINMENT_TEXT = ("The sentence was separated from what followed, but part of its analysis did not stay inside the "
                    "sentence, so no pronunciation feedback is shown.")
LOW_CONFIDENCE_TEXT = ("Many of this sentence's sounds were decoded differently from the expected ones, so treat its "
                       "pronunciation feedback with caution.")
LIKELY_WITHHELD_TEXT = ("Your reading is probably this sentence, but I couldn't safely determine where it ended.")

SUMMARY_REASONS = {
    "discarded": "discarded",
    "rerecord": "marked for re-recording",
    "not_analysed": "not analysed",
    "other_engine": "analysed by another engine",
    "different_sentence": "appears to contain a different sentence",
    "boundary": "sentence boundary uncertain — recording preserved, feedback withheld",
    "containment": "analysis not contained in the sentence — recording preserved, feedback withheld",
    "unconfirmed": "could not confirm it is this sentence — keep it to include it",
}
IDENTIFIED = ("MATCH", "LIKELY_MATCH")


def attempt_status(attempt: dict[str, Any], primary: dict[str, Any] | None, engine: str | None = None) -> dict[str, Any]:
    st = attempt["state"]
    disp = attempt.get("user_disposition")
    kept = disp == "kept"
    out: dict[str, Any] = {"preserved": True, "disposition": disp, "kept": kept}
    if st == "TOO_SHORT":
        identity = "TOO_SHORT"
    elif st in ("ANALYSIS_FAILED", "REJECTED", "INTERRUPTED"):
        identity = "FAILED"
    elif st != "ANALYZED" or primary is None or primary.get("state") != "SUCCEEDED":
        identity = "PENDING" if st in ("CAPTURING", "RECORDED", "QUEUED", "ANALYZING") else "FAILED"
    else:
        identity = (primary.get("target_confirmation") or {}).get("state") or "NOT_APPLICABLE"
    b = (primary or {}).get("boundary") or {}
    withheld = bool(b.get("feedback_withheld"))
    # why feedback is withheld: the boundary itself (genuinely unknown end), or the sentence-only analysis not
    # staying inside the sentence (containment) — never because the decoding was merely unclear
    cause = (b.get("withheld_reason") or "boundary") if withheld else None
    # pronunciation-analysis confidence only describes an analysis that is shown (measured after isolation)
    analysis = None if withheld else (b.get("analysis") or {}).get("state")
    out |= {"identity": identity, "boundary": b.get("state"), "feedback_withheld": withheld, "withheld_reason": cause,
            "boundary_confidence": b.get("boundary_confidence"), "analysis": analysis}

    # C. feedback
    if identity in ("TOO_SHORT", "FAILED", "PENDING", "NOT_APPLICABLE", "NOT_CHECKED"):
        feedback = "none"  # no identity evidence: nothing to show, and Keep cannot make it count
    elif withheld:
        feedback = "withheld_containment" if cause == "containment" else "withheld_boundary"
    elif identity == "MISMATCH":
        feedback = "hidden_identity"
    else:
        feedback = "shown"
    out["feedback"] = feedback

    # the message the reader shows (identity, with the boundary when it is why feedback is withheld)
    if feedback == "withheld_containment" and identity in IDENTIFIED:
        message = CONTAINMENT_TEXT
    elif withheld and identity == "MATCH":
        message = MATCH_WITHHELD_TEXT
    elif withheld and identity == "LIKELY_MATCH":
        message = LIKELY_WITHHELD_TEXT
    else:
        message = IDENTITY_TEXT.get(identity, "")
    out["message"] = message
    out["analysis_note"] = LOW_CONFIDENCE_TEXT if feedback == "shown" and analysis == "low_confidence" else None

    # D. summary
    if disp == "discarded":
        reason = "discarded"
    elif disp == "rerecord_requested":
        reason = "rerecord"
    elif feedback == "none":
        reason = "not_analysed"
    elif engine is not None and primary.get("engine_id") != engine:
        reason = "other_engine"
    elif identity == "MISMATCH":
        reason = "different_sentence"
    elif withheld:
        reason = "containment" if cause == "containment" else "boundary"
    elif identity in IDENTIFIED or kept:
        reason = None
    else:
        reason = "unconfirmed"
    out["summary"] = None if reason is None else SUMMARY_REASONS[reason]
    out["summary_code"] = reason
    # Keep is asked only where the identity is uncertain (or another sentence seems to have been read)
    out["needs_decision"] = identity in ("AMBIGUOUS", "MISMATCH") and not kept and \
        disp not in ("discarded", "rerecord_requested")
    # reading coverage, separate from feedback: this sentence was (probably) read and the audio is kept
    out["recorded"] = identity in ("MATCH", "LIKELY_MATCH", "AMBIGUOUS") and disp != "discarded"
    out["identity_group"] = ("identified" if identity in IDENTIFIED or (identity == "AMBIGUOUS" and kept) else
                             "uncertain" if identity == "AMBIGUOUS" else
                             "different" if identity == "MISMATCH" else "unusable")
    return out
