"""The user-facing diagnosis: categories, windows, states, and language."""

import re

import pytest
from benchmark_fakes import make_dataset, make_result

from pronunciation_lab.app import diagnosis as D
from pronunciation_lab.app.phones import hint
from pronunciation_lab.benchmark.base import ErrorType
from pronunciation_lab.benchmark.schema import NBestCandidate, ProcessingError


@pytest.mark.parametrize("op, posterior, margin, category", [
    ("match", 0.9, 0.8, "expected"),
    ("match", 0.3, 0.05, "expected"),            # a decoded match stays expected
    ("substitution", 0.01, 0.8, "different"),    # confident other sound
    ("substitution", 0.05, 0.8, "unclear"),      # expected still plausible (boundary)
    ("substitution", 0.049, 0.8, "different"),
    ("substitution", 0.01, 0.19, "unclear"),     # top candidate barely wins
    ("substitution", 0.01, 0.2, "different"),    # boundary: margin exactly 0.2 is not ambiguous
    ("substitution", 0.01, None, "different"),
    ("omission", 0.0, None, "not_detected"),
    ("omission", 0.04, None, "not_detected"),
    ("omission", 0.06, None, "unclear"),         # absent from the decode but plausible
])
def test_classify_sound(op, posterior, margin, category):
    assert D.classify_sound(op, posterior, margin) == category


def test_unknown_operation_is_rejected():
    with pytest.raises(ValueError):
        D.classify_sound("insertion", 0.1, 0.1)


@pytest.mark.parametrize("span, duration, window", [
    ((1000, 1020), 5000, (860, 1160)),     # widened to 300 ms, centred
    ((0, 20), 5000, (0, 300)),             # left edge keeps the width
    ((4990, 5000), 5000, (4700, 5000)),    # right edge keeps the width
    ((1000, 1600), 5000, (1000, 1600)),    # long spans are kept whole
    ((0, 20), 200, (0, 200)),              # never beyond the audio
])
def test_sound_window(span, duration, window):
    start, end = D.sound_window(*span, duration)
    assert (round(start), round(end)) == window
    assert start <= span[0] and end >= min(span[1], duration)


def test_word_window_pads_and_clamps():
    assert D.word_window(1000, 1360, 5000) == (920, 1480)
    assert D.word_window(30, 100, 150) == (0, 150)


# ----------------------------------------------------------------------
# build_view on constructed results
# ----------------------------------------------------------------------


@pytest.fixture
def wav(tmp_path):
    return make_dataset(tmp_path, ["R01"]) / "benchmark_wav" / "R01.wav"


def result_with(wav, observed="a", op=None, posterior=0.9, nbest=None, extras=None, status="ok"):
    r = make_result(engine="wav2vec2_raw", recording_id="R01", text="Think about three things.",
                    audio_path=wav, cold=False, observed=observed, status=status)
    p = r.words[0].phonemes[0]
    if op:
        p.engine_evidence["operation"] = op
    p.engine_evidence["expected_phone_posterior"] = posterior
    if nbest is not None:
        p.observed.nbest = [NBestCandidate(phoneme=a, probability=b) for a, b in nbest]
    if extras is not None:
        p.engine_evidence["extra_heard_phones"] = [{"phone": x} for x in extras]
    if op == "omission":
        p.observed.top = None
        p.timing.source = "derived"
    return r


def test_view_of_a_match(wav):
    view = D.build_view(result_with(wav))
    assert view["state"] == "ok"
    [word] = view["words"]
    [sound] = word["sounds"]
    assert word["status"] == "expected" and sound["category"] == "expected"
    assert sound["span_ms"] == [0.0, 20.0] and sound["timing_estimated"] is False
    assert view["summary"] == {"expected": 1, "different": 0, "unclear": 0, "not_detected": 0, "not_interpreted": 0}
    assert view["audio_timeline"] == "analysis"


def test_view_of_a_confident_substitution(wav):
    view = D.build_view(result_with(wav, observed="b", posterior=0.01, nbest=[("b", 0.9), ("a", 0.01)]))
    sound = view["words"][0]["sounds"][0]
    assert sound["category"] == "different"
    assert sound["text"] == "Heard as /b/ where /a/ was expected"
    assert view["words"][0]["status"] == "different"


def test_view_of_an_omission_marks_estimated_timing(wav):
    view = D.build_view(result_with(wav, op="omission", posterior=0.0, nbest=[("z", 0.4), ("a", 0.0)]))
    sound = view["words"][0]["sounds"][0]
    assert sound["category"] == "not_detected"
    assert sound["heard"] is None and sound["timing_estimated"] is True
    assert "location estimated" in sound["text"] and "/z/" in sound["text"]


def test_insertions_are_shown(wav):
    view = D.build_view(result_with(wav, extras=["ɪ"]))
    assert view["words"][0]["sounds"][0]["extra_sounds_after"] == ["ɪ"]


def test_alignment_suspect_word_is_not_interpreted(wav):
    view = D.build_view(result_with(wav, extras=["x", "y", "z"]))
    word = view["words"][0]
    assert word["status"] == "not_interpreted"
    assert word["sounds"][0]["category"] == "not_interpreted"
    assert view["summary"]["not_interpreted"] == 1


def test_word_status_priority():
    assert D.WORD_PRIORITY == ("not_interpreted", "different", "not_detected", "unclear", "expected")


def test_partial_state_carries_warnings(wav):
    view = D.build_view(result_with(wav, status="partial"))
    assert view["state"] == "partial" and view["warnings"] == ["fake partial"]


@pytest.mark.parametrize("status, code, state", [
    ("failed", ErrorType.AUDIO_TOO_SHORT, "failed"),
    ("failed", ErrorType.AUDIO_UNREADABLE, "failed"),
    ("failed", ErrorType.ENGINE_EXCEPTION, "failed"),
    ("blocked", ErrorType.MODEL_UNAVAILABLE, "unavailable"),
    ("blocked", ErrorType.CREDENTIALS_UNAVAILABLE, "unavailable"),
    ("blocked", ErrorType.UNRESOLVED_ENGINE, "unavailable"),
])
def test_error_states(wav, status, code, state):
    r = result_with(wav)
    r.status = status
    r.words = []
    r.errors = [ProcessingError(type=code, message="detail")]
    view = D.build_view(r)
    assert view["state"] == state
    assert view["error"]["code"] == code
    assert view["error"]["message"] == D.ERROR_MESSAGES[code]
    assert "words" not in view


def test_no_heard_phones_is_no_speech(wav):
    r = result_with(wav)
    r.engine_evidence["recognition"]["phones"] = []
    view = D.build_view(r)
    assert view["state"] == "no_speech" and view["words"] == []


FORBIDDEN = re.compile(r"\b(wrong|incorrect|mistake|error|bad|score|correct|failed to)\b", re.IGNORECASE)


@pytest.mark.parametrize("kwargs", [
    {}, {"observed": "b", "posterior": 0.01, "nbest": [("b", 0.9), ("a", 0.0)]},
    {"observed": "b", "posterior": 0.3}, {"op": "omission", "posterior": 0.0},
    {"op": "omission", "posterior": 0.3}, {"extras": ["x", "y", "z"]},
])
def test_sound_texts_never_judge(wav, kwargs):
    view = D.build_view(result_with(wav, **kwargs))
    for word in view["words"]:
        for sound in word["sounds"]:
            assert not FORBIDDEN.search(sound["text"]), sound["text"]


def test_caveats_state_the_limits():
    text = " ".join(D.CAVEATS)
    assert "eSpeak en-us" in text and "not a teacher" in text and "do not measure how long" in text


def test_phone_hints():
    assert hint("θ") == "th as in think" and hint("iː") == "ee as in see"
    assert hint("q̃") is None and hint(None) is None
