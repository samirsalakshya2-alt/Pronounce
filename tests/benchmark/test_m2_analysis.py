"""Task analysis: interpretation rules, robustness to bad input, no ranking."""

import json

import pytest
from benchmark_fakes import FakeEngine, FakeUnavailableEngine, factory, make_dataset

from pronunciation_lab.benchmark import analysis as A
from pronunciation_lab.benchmark.cells import CellStore
from pronunciation_lab.benchmark.dataset import preflight
from pronunciation_lab.benchmark.runner import BenchmarkRunner


def row(**kw):
    base = {
        "operation": "match", "observed": "a", "expected": "a", "expected_posterior": 0.9,
        "nbest_margin": 0.9, "alignment_suspect": False, "function_word": False,
        "position_in_word": 0, "word_length": 3, "expected_stress": None, "speed": "slow",
        "stress_known": True,
    }
    return base | kw


# ----------------------------------------------------------------------
# Rules
# ----------------------------------------------------------------------


@pytest.mark.parametrize("style, rank", [
    ("Slow, very clear", "slow"), ("Normal conversational", "normal"), ("Fast, natural", "fast"),
    ("Deliberate /θ/→/t/", "deliberate_substitution"), ("Slow, deliberate", "slow"),
    ("Normal, clear", "normal"), ("Whispered", "unknown"),
])
def test_speed_rank(style, rank):
    assert A.speed_rank(style) == rank


def test_consistent_deviation_requires_the_same_deviation_everywhere():
    same = {r: row(operation="substitution", observed="ʌ", speed=s) for r, s in (("R1", "slow"), ("R2", "normal"), ("R3", "fast"))}
    assert A._classify_position(same, ["R1", "R2", "R3"]) == "consistent_deviation_candidate"
    same["R2"] = same["R2"] | {"observed": "ə"}
    assert A._classify_position(same, ["R1", "R2", "R3"]) != "consistent_deviation_candidate"


def test_fast_only_omission_of_a_function_word_is_a_reduction_candidate():
    by = {
        "R1": row(speed="slow", function_word=True),
        "R2": row(speed="normal", function_word=True),
        "R3": row(speed="fast", function_word=True, operation="omission", observed=None, expected_posterior=0.0),
    }
    assert A._classify_position(by, ["R1", "R2", "R3"]) == "connected_speech_reduction_candidate"


def test_fast_only_omission_with_plausible_expected_phone_is_ambiguous():
    by = {
        "R1": row(speed="slow", function_word=True),
        "R3": row(speed="fast", function_word=True, operation="omission", observed=None, expected_posterior=0.2),
    }
    assert A._classify_position(by, ["R1", "R3"]) == "ambiguous"


def test_fast_only_change_on_a_stressed_word_initial_consonant_is_not_called_reduction():
    by = {
        "R1": row(speed="slow", expected="k", position_in_word=0),
        "R3": row(speed="fast", expected="k", operation="substitution", observed="ɡ", nbest_margin=0.8, expected_posterior=0.01),
    }
    assert A._classify_position(by, ["R1", "R3"]) == "style_specific_deviation"


def test_deviation_in_slow_speech_only_is_never_called_reduction():
    by = {
        "R1": row(speed="slow", function_word=True, operation="omission", observed=None, expected_posterior=0.0),
        "R3": row(speed="fast", function_word=True),
    }
    assert A._classify_position(by, ["R1", "R3"]) == "style_specific_deviation"


def test_alignment_suspect_overrides_everything():
    by = {"R1": row(alignment_suspect=True, operation="substitution", observed="x"), "R3": row(speed="fast")}
    assert A._classify_position(by, ["R1", "R3"]) == "alignment_suspect"


@pytest.mark.parametrize("task, r, outcome", [
    ("th_dental_fricatives", row(expected="θ", observed="t", operation="substitution"), "decoded_as_stop"),
    ("th_dental_fricatives", row(expected="ð", observed="d", operation="substitution"), "decoded_as_stop"),
    ("th_dental_fricatives", row(expected="θ", observed="f", operation="substitution"), "decoded_as_other"),
    ("th_dental_fricatives", row(expected="θ", observed=None, operation="omission"), "not_decoded"),
    ("i_contrast", row(expected="iː", observed="i", operation="substitution"), "same_class_length_differs"),
    ("i_contrast", row(expected="ɪ", observed="iː", operation="substitution"), "crossed_contrast"),
    ("i_contrast", row(expected="ɪ", observed="eɪ", operation="substitution"), "decoded_as_other"),
    ("v_w", row(expected="w", observed="v", operation="substitution", alignment_suspect=True), "alignment_suspect"),
    ("v_w", row(expected="w", observed="w"), "decoded_as_expected"),
])
def test_task_outcomes(task, r, outcome):
    assert A._outcome(task, A.TASKS[task], r) == outcome


def test_cluster_and_rhotic_selection():
    word = [  # "strɪŋz": s t r are a cluster, ŋ z are a cluster, r is rhotic
        {"word_index": 0, "expected": p} for p in ("s", "t", "ɹ", "ɪ", "ŋ", "z")
    ]
    selected = A._task_rows(A.TASKS["r_and_clusters"], word, None)
    assert [(r["expected"], r["in_cluster"], r["rhotic"]) for r in selected] == [
        ("s", True, False), ("t", True, False), ("ɹ", True, True), ("ŋ", True, False), ("z", True, False),
    ]
    single = [{"word_index": 0, "expected": p} for p in ("b", "ɪ", "t")]
    assert A._task_rows(A.TASKS["r_and_clusters"], single, None) == []


def test_edit_distance():
    assert A._normalized_edit_distance([], []) == 0.0
    assert A._normalized_edit_distance(list("abc"), list("abc")) == 0.0
    assert A._normalized_edit_distance(list("abc"), list("abd")) == pytest.approx(1 / 3)
    assert A._normalized_edit_distance(list("ab"), list("abcd")) == pytest.approx(0.5)


def test_vowel_and_rhotic_classes():
    assert all(A.is_vowel(v) for v in ("ɪ", "iː", "aʊ", "ɚ", "əl", "ᵻ"))
    assert not any(A.is_vowel(c) for c in ("θ", "t", "ŋ", "dʒ", None, ""))
    assert all(A.is_rhotic(r) for r in ("ɹ", "ɚ", "ɑːɹ"))


# ----------------------------------------------------------------------
# Bad input never breaks analysis
# ----------------------------------------------------------------------


@pytest.fixture
def mixed_run(tmp_path):
    ids = ["R01", "R02", "R03", "R04"]
    data = make_dataset(tmp_path, ids)
    report = preflight(data, expected_ids=tuple(ids))
    engines = {
        "e": FakeEngine("e", plan={"R02": RuntimeError("x")}),
        "cloud": FakeUnavailableEngine("cloud", "blocked"),
    }
    runner = BenchmarkRunner(tmp_path / "run", "t", report.recordings, list(engines), data_dir=data,
                             engine_factory=factory(engines), known_engines=list(engines))
    runner.run(report)
    store = CellStore(runner.run_dir)
    # R03: corrupt file.  R04: validly written but malformed evidence.
    store.cell_path("R03__e").write_text("{ truncated")
    cell = store.load("R04__e").cell
    del cell.result.words[0].phonemes[0].engine_evidence["operation"]
    store.write(cell)
    return runner, report


def test_malformed_input_is_excluded_with_reasons(mixed_run):
    runner, report = mixed_run
    run = A.load_run(runner.run_dir, report.recordings, ["e", "cloud"])
    reasons = {x["cell_id"]: x["reason"] for x in run.excluded}

    assert set(run.evidence) == {"R01__e"}
    assert reasons["R02__e"] == "no evidence (failed)"
    assert reasons["R03__e"].startswith("corrupt")
    assert "without alignment operation" in reasons["R04__e"]
    assert reasons["R01__cloud"] == "no evidence (blocked)"


def test_every_summary_is_produced_despite_bad_cells(mixed_run):
    runner, report = mixed_run
    written = A.write_analysis(runner.run_dir, report.recordings, ["e", "cloud"])
    names = {p.name for p in written}
    assert names == {"matrix.json", "performance.json", "task_analysis.json", "phenomena.json",
                     "style_comparison.json", "cross_engine.json", "word_diagnosis.json"}
    matrix = json.loads((runner.run_dir / "summaries" / "matrix.json").read_text())
    assert matrix["counts"] == {"ok": 2, "failed": 1, "blocked": 4, "corrupt": 1}
    assert matrix["matrix"]["e"]["R03"] == {"status": "corrupt"}
    tasks = json.loads((runner.run_dir / "summaries" / "task_analysis.json").read_text())
    th = tasks["tasks"]["th_dental_fricatives"]["by_engine"]
    assert th["cloud"]["status"] == ["blocked"]
    assert set(th["e"]["per_recording"]) == {"R01"}


def test_no_summary_ranks_engines(mixed_run):
    runner, report = mixed_run
    A.write_analysis(runner.run_dir, report.recordings, ["e", "cloud"])
    for path in (runner.run_dir / "summaries").glob("*.json"):
        text = path.read_text().lower()
        for word in ('"rank', '"best', '"winner', '"overall_score', '"score":'):
            assert word not in text, f"{path.name} contains {word}"


def test_slow_style_deviation_blocks_the_reduction_label():
    """If the slowest reading already deviates, a fast-style deviation is not a reduction."""
    by = {
        "R1": row(speed="slow", function_word=True, operation="substitution", observed="x",
                  nbest_margin=0.9, expected_posterior=0.01),
        "R3": row(speed="fast", function_word=True, operation="omission", observed=None, expected_posterior=0.0),
    }
    assert A._classify_position(by, ["R1", "R3"]) == "style_specific_deviation"


def test_reference_is_the_slowest_available_style_even_when_it_is_normal():
    """Groups without a slow reading (R17/R18): 'normal' is the reference."""
    ok = {"R17": row(speed="normal", function_word=True),
          "R18": row(speed="fast", function_word=True, operation="omission", observed=None, expected_posterior=0.0)}
    assert A._classify_position(ok, ["R17", "R18"]) == "connected_speech_reduction_candidate"

    both = {"R17": row(speed="normal", function_word=True, operation="substitution", observed="x",
                       nbest_margin=0.9, expected_posterior=0.01),
            "R18": row(speed="fast", function_word=True, operation="omission", observed=None, expected_posterior=0.0)}
    assert A._classify_position(both, ["R17", "R18"]) == "style_specific_deviation"


def _word(insertions_on_first_phone, n=2):
    from types import SimpleNamespace

    phonemes = [SimpleNamespace(engine_evidence={"extra_heard_phones": [{"phone": "x"}] * insertions_on_first_phone})]
    phonemes += [SimpleNamespace(engine_evidence={"extra_heard_phones": []}) for _ in range(n - 1)]
    return SimpleNamespace(phonemes=phonemes)


def test_alignment_suspicion_covers_the_neighbours_of_an_insertion_pile():
    """R08 regression: the insertions pile up on "the", but "evening" (next word)
    is the one aligned onto the wrong material, so it must be suspect too."""
    words = [_word(0), _word(0), _word(8), _word(0), _word(0)]  # ... before the evening begins
    assert A.alignment_suspect_words(words) == {1, 2, 3}
    assert A.alignment_suspect_words([_word(2), _word(0)]) == set()
    assert A.alignment_suspect_words([_word(3)]) == {0}



def test_unknown_stress_is_not_treated_as_unstressed():
    """Pre-freeze review regression: OpenPronounce reports no stress, so its
    vowels must not qualify for the 'unstressed vowel' reduction clause
    ("world" /ɚ/ was mislabelled a reduction candidate)."""
    vowel = dict(expected="ɚ", position_in_word=1, word_length=4)
    known = {"R11": row(speed="slow", **vowel),
             "R13": row(speed="fast", operation="substitution", observed="ɛ", nbest_margin=0.8, expected_posterior=0.01, **vowel)}
    assert A._classify_position(known, ["R11", "R13"]) == "connected_speech_reduction_candidate"

    unknown = {r: v | {"stress_known": False} for r, v in known.items()}
    assert A._classify_position(unknown, ["R11", "R13"]) == "style_specific_deviation"
