"""M12 phase 7: target confirmation from alignment evidence (never free ASR), calibrated on benchmark pairs."""

import ast
import inspect

import pytest
from test_m4_coach import ph, result

from pronunciation_lab.reader import target as T


def res_with(n_match, n_sub_plausible=0, n_sub=0, n_omit=0, status="ok", heard=None):
    phs = ([ph("a", "a") for _ in range(n_match)]
           + [ph("a", "e", ep=0.05, nbest=[("e", 0.6), ("a", 0.05)]) for _ in range(n_sub_plausible)]
           + [ph("a", "e", ep=0.01, nbest=[("e", 0.9), ("a", 0.01)]) for _ in range(n_sub)]
           + [ph("a", None, ep=0.0, nbest=[("e", 0.01)], source="derived") for _ in range(n_omit)])
    return result([("w", phs)], status=status, heard=heard)


@pytest.mark.parametrize("args, state", [
    ((76, 0, 24), "MATCH"),           # support 0.76 exactly
    ((75, 0, 25), "AMBIGUOUS"),       # 0.75
    ((70, 6, 24), "MATCH"),           # plausible substitutions count as support
    ((25, 0, 0, 75), "AMBIGUOUS"),    # 0.25 exactly: still ambiguous (a partial read)
    ((24, 0, 0, 76), "MISMATCH"),     # 0.24
    ((0, 0, 50, 50), "MISMATCH"),
])
def test_thresholds(args, state):
    tc = T.confirm_target(res_with(*args))
    assert tc["state"] == state, tc
    assert tc["engine"] == "wav2vec2_raw" and tc["version"] == T.TARGET_VERSION and tc["reason"]


def test_evidence_breakdown():
    tc = T.confirm_target(res_with(3, 1, 1, 1))
    assert tc["evidence"] | {} == tc["evidence"]
    assert {k: tc["evidence"][k] for k in ("expected_sounds", "matched", "plausible_substitutions", "substituted",
                                          "not_detected")} == {"expected_sounds": 6, "matched": 3,
                                                               "plausible_substitutions": 1, "substituted": 2,
                                                               "not_detected": 1}
    assert tc["evidence"]["support"] == pytest.approx(4 / 6)


@pytest.mark.parametrize("status", ["failed", "blocked"])
def test_no_evidence_is_not_applicable(status):
    assert T.confirm_target(res_with(5, status=status))["state"] == "NOT_APPLICABLE"


def test_no_decoded_speech_is_ambiguous_not_mismatch():
    tc = T.confirm_target(res_with(0, 0, 0, 5, heard=[]))
    assert tc["state"] == "AMBIGUOUS" and "no speech" in tc["reason"]


def test_target_confirmation_uses_no_speech_recognition():
    """Only the alignment evidence of the result: no engine, model, transcription or ASR import."""
    tree = ast.parse(inspect.getsource(T))
    imported = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
               {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    # tc-2 adds eSpeak grapheme-to-phoneme of known sentences (phonemizer) and stdlib helpers — no engine module
    assert imported <= {"__future__", "typing", "pronunciation_lab.benchmark.analysis", "pronunciation_lab.benchmark.schema",
                        "phonemizer", "phonemizer.separator", "functools", "hashlib", "random", "re", "unicodedata"}
    assert not any(m.startswith("pronunciation_lab.benchmark.engines") for m in imported)
    src = inspect.getsource(T).lower()
    for word in ("transformers", "whisper", "transcri", "asr", "960h", "create_engine"):
        assert word not in src.replace("never based on free speech\nrecognition", ""), word
    assert list(inspect.signature(T.confirm_target).parameters) == ["result", "alternatives"]
